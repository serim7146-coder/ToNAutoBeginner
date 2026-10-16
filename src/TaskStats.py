"""CPU を仕事ごとに記録する（FI。依頼者 2026-10-16「可視化はしていいと思いますね」）。

v1.2.0 の 12 時間で、本体の CPU は合計 5,668 秒。名前の分かる生きているスレッド（画面・ログ監視・速さ受信…）は
2,313 秒で、残り約 59% はラウンドごとの短いスレッドだった（外からは何の仕事か分からない）。

スレッドを立てる所を thread(name, target, …) に替えると、終わるときにそのスレッドの CPU（time.thread_time()）を
名前に足す。長生きのスレッドは立っている間の CPU を ProcStats.thread_cpu（GetThreadTimes）で読み、前回の行からの
差を出す。MEMORY_LOG_SEC ごとに mainGUI._log_memory が snapshot() で前回からの差をまとめ、line() で debug.log の
1 行にする。数字を取るだけで、各仕事の中身・スレッドの数・daemon・例外の扱いは変えない。

どのスレッドから呼んでもよい。2 つの辞書は _lock で守る（仕事 1 つあたり、終わるときにロックを 1 回）。
"""
import threading
import time

import ProcStats
import config

_lock = threading.Lock()
_finished: dict = {}        # 名前 → [回数, CPU 秒]。前回の行からの、終わった仕事の集計
_live: dict = {}            # スレッドの ident → [名前, native_id, 最後に読んだ CPU 秒]。いま生きている名前付きスレッド
_last_wall = None           # 前回の行の時刻（time.monotonic()）
_last_process_cpu = None
_last_tk_cpu = None
_last_peak_ws = None


def register_live(name: str):
    """長生きのスレッドが自分の最初に呼ぶ。終わるときは unregister_live()。前に入っていたもの（同じスレッドが
    入れ子で走ったとき）を返す"""
    ident = threading.get_ident()
    entry = [name, threading.get_native_id(), time.thread_time()]
    with _lock:
        before = _live.get(ident)
        _live[ident] = entry
    return before


def unregister_live(before=None):
    """register_live の登録を外し、最後に読んでから終わるまでの CPU を「終わった」側に足す
    （生きている側で読んだ分は引く。二重に数えない）"""
    ident = threading.get_ident()
    cpu = time.thread_time()
    with _lock:
        entry = _live.pop(ident, None)
        if before is not None:
            _live[ident] = before
        if entry is None:
            return
        counted = _finished.setdefault(entry[0], [0, 0.0])
        counted[0] += 1
        counted[1] += max(0.0, cpu - entry[2])


def run(name: str, target, *args, **kwargs):
    """スレッドの target を包む。終わりに、このスレッドの CPU を name に足す。例外は握らずに外へ出す"""
    before = register_live(name)
    try:
        return target(*args, **kwargs)
    finally:
        unregister_live(before)


class _Tracked:
    """threading.Thread の target に渡す呼び出し役。呼ばれたら run で target を包んで走らせる。
    __name__・__func__ など target の持ち物はそのまま見せる（テストが Thread の呼び出しから target の名前を読む）"""

    def __init__(self, name: str, target, kwargs: dict):
        self._task_name, self._target, self._kwargs = name, target, kwargs

    def __call__(self, *args):
        return run(self._task_name, self._target, *args, **self._kwargs)

    def __getattr__(self, attr):
        if attr in ("_task_name", "_target", "_kwargs"):
            raise AttributeError(attr)             # まだ入っていない（コピーの途中など）。自分を呼び続けない
        return getattr(self._target, attr)


def thread(name: str, target, *args, daemon: bool = True, thread_name: str = None, **kwargs) -> threading.Thread:
    """threading.Thread を作って返す（start はしない。呼び出し側の .start() をそのまま使う）。
    Thread への渡し方は今までと同じ（target・あれば args・daemon）で、target だけが run を通る。
    kwargs は target へ渡す。thread_name は Thread の名前（OS のスレッドの名前ではなく Python の名前）"""
    options = {"daemon": daemon}
    if args:
        options["args"] = args
    if thread_name is not None:
        options["name"] = thread_name
    return threading.Thread(target=_Tracked(name, target, kwargs), **options)


def snapshot(tk_cpu_now, process_cpu_now, peak_ws, wall_now: float = None) -> dict:
    """前回の行からの差をまとめて返し、集計をリセットする。
    {first, elapsed, process_delta, tk_delta, finished: [(名前, 回数, CPU)], live: [(名前, 本数, CPU)],
     peak_ws, peak_delta}。finished・live は CPU の多い順。取れなかった値・初回の差は None。
    初回（前回が無い）は差が取れないので first=True。終わった仕事は捨て、生きているスレッドは今の値を土台にする"""
    global _last_wall, _last_process_cpu, _last_tk_cpu, _last_peak_ws
    wall_now = time.monotonic() if wall_now is None else wall_now
    with _lock:
        first = _last_wall is None
        done = dict(_finished)
        _finished.clear()
        live = {}
        for entry in _live.values():
            cpu = ProcStats.thread_cpu(entry[1])
            if cpu is None:
                continue                # 開けない（終わった直後）
            delta, entry[2] = max(0.0, cpu - entry[2]), cpu     # 読んだ分は「終わった」側で引く
            counted = live.setdefault(entry[0], [0, 0.0])
            counted[0] += 1
            counted[1] += delta

        def diff(now, last):
            return None if now is None or last is None else max(0.0, now - last)
        result = {
            "first": first,
            "elapsed": None if first else wall_now - _last_wall,
            "process_delta": diff(process_cpu_now, _last_process_cpu),
            "tk_delta": diff(tk_cpu_now, _last_tk_cpu),
            "finished": [] if first else _ranked(done),
            "live": [] if first else _ranked(live),
            "peak_ws": peak_ws,
            "peak_delta": None if peak_ws is None or _last_peak_ws is None else peak_ws - _last_peak_ws,
        }
        _last_wall, _last_process_cpu, _last_tk_cpu = wall_now, process_cpu_now, tk_cpu_now
        _last_peak_ws = peak_ws
    return result


def _ranked(counts: dict) -> list:
    return sorted(((name, n, cpu) for name, (n, cpu) in counts.items()), key=lambda row: (-row[2], row[0]))


def _top(rows: list, show_count) -> str:
    """CPU の多い順に上位 MEMORY_LOG_TOP 個。残りがあれば「…」"""
    top = config.MEMORY_LOG_TOP
    parts = []
    for name, n, cpu in rows[:top]:
        count = f" ×{n}" if show_count or n > 1 else ""
        parts.append(f"{name}{count} {cpu:.1f}秒")
    return " / ".join(parts) + (" …" if len(rows) > top else "") if parts else "なし"


def line(snap: dict) -> str:
    """debug.log の「[CPU] …」1 行。秒は小数 1 桁。初回は「計測開始」だけ"""
    if snap["first"]:
        return "[CPU] 計測開始"
    elapsed = snap["elapsed"]
    span = f"{elapsed / 60:.0f}分" if elapsed >= 60 else f"{elapsed:.0f}秒"

    def seconds(value):
        return "?" if value is None else f"{value:.1f}秒"
    process = snap["process_delta"]
    percent = "?" if process is None or elapsed <= 0 else f"{process / elapsed * 100:.1f}%"
    return (f"[CPU] {span}で {seconds(process)}（{percent}）・画面 {seconds(snap['tk_delta'])}"
            f"・終わった: {_top(snap['finished'], True)}・生きている: {_top(snap['live'], False)}")
