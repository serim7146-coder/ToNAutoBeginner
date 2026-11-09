"""最大 WS（ピーク）の急な増えを見張り、そのとき動いていた仕事を debug.log に 1 行書く。

10 分ごとの [メモリ]・[CPU] の行（mainGUI._log_memory）では、数秒で 1GB ほど跳ねて戻るメモリの原因が分からない。
MEMORY_JUMP_CHECK_SEC ごとに ProcStats.peak_working_set() を読み、前回から MEMORY_JUMP_MB 以上増えていたら、
動いている名前付きスレッド・この間に終わった仕事・ほかのスレッド・Tk のスレッドで走っていた関数を 1 行にして書く。
増えていなければ何も書かない。最初の 1 回は土台を覚えるだけ。最大 WS が取れなければ何もしない。

監視は止まっていても動く。書くのは debug.log だけ（画面のログには出さない）。Tk は一切呼ばない。
"""
import threading
import time

import DebugLog
import ProcStats
import TaskStats
import config

_MB = 1024 * 1024

_sleep = time.sleep         # テストが差し替える（待たずに 1 周ずつ動かす）
_lock = threading.Lock()
_thread = None              # 立てた見張りのスレッド（2 本目は立てない）


def _names(rows, show_count) -> str:
    """[(名前, 回数)] を「名前 ×回数 / …」に。上位 MEMORY_LOG_TOP 個で切り、残りがあれば「…」。無ければ「なし」"""
    top = config.MEMORY_LOG_TOP
    parts = [f"{name} ×{n}" if show_count or n > 1 else name for name, n in rows[:top]]
    return " / ".join(parts) + (" …" if len(rows) > top else "") if parts else "なし"


def _is_jump(prev_peak, peak) -> bool:
    return prev_peak is not None and peak is not None and peak - prev_peak >= config.MEMORY_JUMP_MB * _MB


def jump_line(prev_peak, peak, ws, private, live, finished, others, tk_name):
    """急増の 1 行を返す。前回の最大 WS か今回が None、または増えが MEMORY_JUMP_MB 未満なら None。
    live・finished・others は [(名前, 回数)]。tk_name は Tk のスレッドで走っていた関数の名前（無ければ None）"""
    if not _is_jump(prev_peak, peak):
        return None

    def mb(value):
        return "?" if value is None else f"{value / _MB:.1f}MB"
    return (f"[メモリ] 急増 +{(peak - prev_peak) / _MB:.0f}MB（最大 WS {prev_peak / _MB:.0f}→{peak / _MB:.0f}MB）"
            f"・WS {mb(ws)}・Private {mb(private)}"
            f"・動いている: {_names(live, False)}・この間に終わった: {_names(finished, True)}"
            f"・ほかのスレッド: {_names(others, False)}・画面: {tk_name or 'なし'}")


def _check_once(state: dict):
    """1 回分。state は {"peak": 前回の最大 WS, "mark": TaskStats.finished_since の印}（呼び出し側が持ち回る）。
    急増なら debug.log に書いて、その行を返す。そうでなければ None。最大 WS が取れなければ state も動かさず None"""
    peak = ProcStats.peak_working_set()
    if peak is None:
        return None
    prev = state.get("peak")
    finished, state["mark"] = TaskStats.finished_since(state.get("mark"))    # 毎回進める（差は「この間」だけにする）
    state["peak"] = peak
    if not _is_jump(prev, peak):
        return None
    ws, private = ProcStats.memory()
    live, others = TaskStats.alive_names()
    line = jump_line(prev, peak, ws, private, live, finished, others, TaskStats.current_tk())
    DebugLog.write(line)
    return line


def _loop():
    state = {}
    while True:
        _sleep(config.MEMORY_JUMP_CHECK_SEC)
        try:
            _check_once(state)
        except Exception:
            DebugLog.exception("MemoryWatch")


def start():
    """見張りのスレッド（MemoryWatch。daemon）を 1 本だけ立てる。2 回目以降は何もしない。どのスレッドから呼んでもよい"""
    global _thread
    with _lock:
        if _thread is not None:
            return
        _thread = TaskStats.thread("MemoryWatch", _loop, thread_name="MemoryWatch")
        _thread.start()
