"""グループインスタンスの待機列に入れるようになった合図を、VRChat のログから読む。

マクロの開始・窓とログの割り当てとは関係なく、「待機列に入れるようになったら知らせる」が ON のあいだ、ツールが自分で VRChat の
ログ（output_log_*.txt）を読む（Watcher）。読むだけで、マクロの操作・入力・OSC は何もしない。待機列はアカウントの
もので窓のものではない（1 つの窓で並ぶと、全部の窓が同じ待機列に動く）ので、窓の番号は使わない。

読む行（接頭辞の `時刻 Debug -` を外した後。ID は伏せてある）:
  待機列に入れた   [API] Requesting Post instances/wrld_X:39837~group(grp_X)~groupAccessType(plus)~region(jp)/queue {{}} retryCount: 2
  入室の候補       [API] Requesting Get instances/wrld_X:39837~group(grp_X)~groupAccessType(plus)~region(jp) {{}} retryCount: 2
  待機列を抜けた   [API] Requesting Delete instances/wrld_X:39837~…/queue …
  入室した         [Behaviour] Joining wrld_X:39837~…
  順番の通知が消えた  Remove notification … of type: localNotifs, id: N, created at: M/D/Y h:m:s UTC, … message: "「入る」を押して移動します。"
                   （押したときはその瞬間、放置なら作成の約 59 秒後に出る。行には名前とユーザー ID が入っているので、
                   通知の id と作られた時刻だけを取り出して行は捨てる。どこにも書かない）

合図の決まり。あるログの Get の行が、次の 4 つを全部満たしたとき:
1. そのインスタンスが、どれかのログで待機列に入れられている（POST。ログどうしで共有する。アカウントは
   共通なので、POST しなかった窓にも同じ Get が出る）
2. Get のログの時刻が、その POST のログの時刻より QUEUE_READY_AFTER_POST_SEC 秒以上後
   （実測では出番が無い。POST から 10 秒以内に同じインスタンスの Get は 15 件中 0 件）
3. その Get のログに、前後 QUEUE_READY_QUIET_SEC 秒未満（ちょうど 3 秒は数えない。ログの時刻は秒までなので、
   ちょうど 3 秒差が出やすい）に、別のインスタンスの Get が無い
   （同じインスタンスの Get は数えない。メニューの行は決まりに入れない）
4. その Get が、そのログで [Behaviour] Joining wrld_… の直後の 1 本目でない（入り直した直後に VRChat が待機列の
   表示を取り直す Get で、順番が来た合図ではない）
インスタンスの印は `wrld_…:番号~…` の全部。

待機列に入れたものを忘れるとき: 順番の通知が消えた行（Remove）を読んだ・Delete を読んだ・Joining を読んだ・
POST から QUEUE_FORGET_SEC 秒たった。合図を出したことでは忘れない（誤検出だったとき、次の本物の合図を出せなくなるため）。
ログは最初に見つけたとき先頭から読む（ツールを開く前・通知を入れる前の POST も数える）。

合図を出す前に、ログで答え合わせをする（Watcher._ready）:
1. その Get の通知がもう消えている（同じ読み取りの束に、Get から ±QUEUE_READY_MATCH_SEC 秒の Remove の行がある）
   → 黙る。記録も忘れる
2. Remove は無いが、その Get より QUEUE_READY_CONFIRM_SEC 秒以上後の行を同じログで読んでいる
   → 通知が出ていれば Remove が出ているはずなので、誤検出。黙る。記録は残す
3. ログが何も語らない（1 も 2 も無い）が、Get のログの時刻が実時間で QUEUE_READY_NOTICE_SEC 秒以上前
   → VRChat の通知は消えているので黙る。記録は残す（ログにも出さず、送らない）
4. どれでもない → 知らせる
合図を出したら答え合わせ待ちにし、QUEUE_READY_CONFIRM_SEC 秒のあいだは同じ印で合図を出さない（1 つの待機列で合図は 1 回）。
そのあいだに Remove を読めば本物だったので記録を忘れる。QUEUE_READY_CONFIRM_SEC 秒たっても Remove が来なければ
誤検出なので、待ちだけ解いて記録は残す（次の単独の Get で出し直せる）。
待機列に入れた行（POST）を読んだときも、その行のログの時刻が QUEUE_READY_NOTICE_SEC 秒未満なら、ツールのログに
1 行出す（on_queued。声・スマホへの送信はしない）。先頭から読み直しているときの古い POST では出さない。

後ろの 3 秒は、次の行が来るのを待たない（行の間が 55 秒開いた窓があり、VRChat の通知は 60 秒しか出ない）。
候補は「候補より 3 秒以上後の時刻の行を読んだ」か「読んでから実時間で 3 秒たった」（tick。Watcher が読むたびに
呼ぶ）ときに閉じる。閉じる前に読んだ行に別のインスタンスの Get があれば取り消す。

インスタンスの ID はどのログにも出さない（ここは何も書かない。呼ぶ側のコールバックにも渡さない）。
"""
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config
import DebugLog
import LogParser
import PhoneNotify
import PlaySound
import SharedState
import TaskStats
import VRChatDiscovery

# 全部の行がここを通るので、まず安い部分文字列の検査で絞ってから正規表現を使う
_MARK_API = "instances/"
_MARK_JOIN = "Joining"
# 待機列の順番の通知が消えた行。押したか期限切れかに関わらず出る（期限切れは作成の約 59 秒後）。
# 本文は日本語クライアントのもの（英語クライアントは考慮しない）。VRChat が文言を変えたら、この判定は効かなくなる
_MARK_READY_GONE = "「入る」を押して移動します。"

_RE_API = re.compile(r"^\[API\] Requesting (Get|Post|Delete) instances/(wrld_[^\s/]+)(/queue)?(?:\s|$)")
_RE_JOIN = re.compile(r"^\[Behaviour\] Joining (wrld_\S+)")
_RE_READY_GONE = re.compile(r"of type: localNotifs, id: (\d+), created at: "
                            r"(\d+)/(\d+)/(\d+) (\d+):(\d+):(\d+) UTC")


def _read_ready_gone(text: str):
    """待機列の順番の通知が消えた行なら (通知の id, 作られた時刻 epoch 秒) を返す。違えば None。
    行の中身（名前・ユーザー ID）は返さない・どこにも書かない"""
    if _MARK_READY_GONE not in text:
        return None
    m = _RE_READY_GONE.search(text)
    if not m:
        return None
    notice_id, month, day, year, hour, minute, second = m.groups()
    try:
        made = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second),
                        tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    return notice_id, made



# ═══════════════════════════════════════════════
#  ログどうしで共有する、待機列に入れたインスタンス
# ═══════════════════════════════════════════════
@dataclass(frozen=True)
class Post:
    """待機列に入れた（POST）記録。at はその行のログの時刻"""
    at: float


@dataclass(frozen=True)
class _Awaiting:
    """合図を出して、答え合わせを待っている印。get_at は合図に使った Get のログの時刻、
    fired は合図を出した実時間（monotonic）"""
    get_at: float
    fired: float


_lock = threading.Lock()
_queued: dict = {}          # インスタンスの印 → Post
_awaiting: dict = {}        # インスタンスの印 → _Awaiting（_lock で守る）


def note_post(key: str, at: float):
    """待機列に入れた。同じインスタンスを入れ直したら、新しい方で置き換える（10 秒は新しい POST から数える）"""
    with _lock:
        for old in [k for k, p in _queued.items() if at - p.at >= config.QUEUE_FORGET_SEC]:
            del _queued[old]
        _queued[key] = Post(at)


def note_delete(key: str):
    """待機列を抜けた"""
    with _lock:
        _queued.pop(key, None)
        _awaiting.pop(key, None)


def note_join(key: str):
    """入室した"""
    with _lock:
        _queued.pop(key, None)
        _awaiting.pop(key, None)


def hold_ready(key: str, get_at: float, now: float) -> bool:
    """合図にしてよいなら、その印を答え合わせ待ちにして True。そうでなければ False。
    記録は消さない（消すのは Remove の行を読んだとき）。
    POST から QUEUE_FORGET_SEC 秒たっていたら忘れて False。
    QUEUE_READY_AFTER_POST_SEC 秒に足りなければ記録を残して False
    （この門は今のログでは出番が無い。実測で POST から 10 秒以内に同じインスタンスの Get は 0 件）。
    すでに答え合わせ待ちなら False（1 つの待機列で合図は 1 回）。now は実時間（monotonic）"""
    with _lock:
        post = _queued.get(key)
        if post is None:
            return False
        age = get_at - post.at
        if age >= config.QUEUE_FORGET_SEC:
            del _queued[key]
            _awaiting.pop(key, None)
            return False
        if age < config.QUEUE_READY_AFTER_POST_SEC:
            return False
        if key in _awaiting:
            return False
        _awaiting[key] = _Awaiting(get_at, now)
        return True


def note_ready_gone(created_at: float):
    """順番の通知が消えた（Remove の行を読んだ）。created at が合図に使った Get の
    QUEUE_READY_MATCH_SEC 秒以内の答え合わせ待ちがあれば、本物だったので記録を忘れる。
    無ければ何もしない（ほかの窓の分・押したあとの分）"""
    with _lock:
        for key in [k for k, a in _awaiting.items()
                    if abs(a.get_at - created_at) <= config.QUEUE_READY_MATCH_SEC]:
            del _awaiting[key]
            _queued.pop(key, None)


def release_ready(get_at: float):
    """その Get の答え合わせ待ちを解く。記録は残す（誤検出だったので、次の単独の Get で出し直せる）"""
    with _lock:
        for key in [k for k, a in _awaiting.items() if a.get_at == get_at]:
            del _awaiting[key]


def release_unconfirmed(now: float) -> int:
    """QUEUE_READY_CONFIRM_SEC 秒たっても Remove の行が来なかった答え合わせ待ちを解く。
    記録は残す。now は monotonic。戻り値は解いた数（debug.log に出す数のため。印は返さない）"""
    with _lock:
        stale = [k for k, a in _awaiting.items() if now - a.fired >= config.QUEUE_READY_CONFIRM_SEC]
        for key in stale:
            del _awaiting[key]
        return len(stale)


def reset():
    """全部忘れる（Watcher.start とテスト用）"""
    with _lock:
        _queued.clear()
        _awaiting.clear()


# ═══════════════════════════════════════════════
#  ログごとの候補（そのログの Get）
# ═══════════════════════════════════════════════
class _Candidate:
    __slots__ = ("key", "at", "read", "cancelled")

    def __init__(self, key, at, read):
        self.key, self.at, self.read, self.cancelled = key, at, read, False


class Candidates:
    """1 本のログぶん。読み取りのスレッド 1 本からだけ使う（feed と tick は同じスレッド）。

    on_ready(get_at) は合図が出たとき 1 回呼ぶ（get_at は Get のログの時刻 epoch 秒。インスタンスの印は渡さない）。
    on_queued(post_at) は、待機列に入れた行（POST）を読むたびに呼ぶ（post_at は POST のログの時刻 epoch 秒。
    印は渡さない。無ければ呼ばない）。clock は実時間（テストが差し替える）"""

    def __init__(self, on_ready, clock=time.monotonic, on_queued=None):
        self._on_ready = on_ready
        self._clock = clock
        self._on_queued = on_queued
        self._recent: list = []         # このログで最近読んだ Get (ログの時刻, 印)。前後の判定用
        self._pending: list = []        # 閉じるのを待っている候補
        # ワールドに入り直した（Joining）直後の 1 本目の Get か。待機列の表示を取り直しているだけなので候補にしない
        self._armed = False

    def feed(self, at, line: str):
        """1 行を読んだ。at はその行のログの時刻（epoch 秒。無ければ None）"""
        if _MARK_API in line or _MARK_JOIN in line:
            self._read(at, LogParser.strip_prefix(line))
        if self._pending and at is not None:
            self._close_due(at)

    def tick(self):
        """読み取りのたびに。読んでから実時間で QUEUE_READY_QUIET_SEC 秒たった候補を閉じる"""
        if self._pending:
            self._close_due(None)

    # ── 行の読み取り ────────────────────
    def _read(self, at, text: str):
        m = _RE_API.match(text)
        if m:
            verb, key, queue = m.groups()
            if verb == "Get" and not queue:
                if at is not None:
                    if self._armed:
                        # 入り直した直後の 1 本目は、VRChat が待機列の表示を戻すために取りに来た分。
                        # 候補にも前後の記録にも入れない
                        self._armed = False
                        return
                    self._on_get(at, key)
            elif verb == "Post" and queue:
                if at is not None:
                    note_post(key, at)
                    if self._on_queued is not None:
                        try:
                            self._on_queued(at)
                        except Exception:
                            DebugLog.exception("QueueWatch.Candidates.on_queued")
            elif verb == "Delete" and queue:
                note_delete(key)
            return
        m = _RE_JOIN.match(text)
        if m:
            note_join(m.group(1))
            self._armed = True          # 次の Get 1 本は数えない

    def _on_get(self, at: float, key: str):
        quiet = config.QUEUE_READY_QUIET_SEC
        self._recent = [r for r in self._recent if abs(at - r[0]) < quiet]
        conflict = any(r_key != key for _r_at, r_key in self._recent)
        for cand in self._pending:
            if cand.key != key and abs(at - cand.at) < quiet:
                cand.cancelled = True       # 後ろ 3 秒未満に別のインスタンスの Get
        self._recent.append((at, key))
        if not conflict:                    # 前 3 秒未満に別のインスタンスの Get があれば候補にしない
            self._pending.append(_Candidate(key, at, self._clock()))

    # ── 閉じる ───────────────────────
    def _close_due(self, at):
        """at: 今読んだ行のログの時刻。None なら tick（実時間だけで見る）"""
        quiet = config.QUEUE_READY_QUIET_SEC
        now = self._clock()
        due, keep = [], []
        for cand in self._pending:
            if cand.cancelled:
                continue
            # 行を読んでいるあいだはログの時刻だけで見る（まとめて読んだ後ろの行が、取り消す Get かもしれない）。
            # 実時間は tick（at が None）だけ
            if at >= cand.at + quiet if at is not None else now - cand.read >= quiet:
                due.append(cand)
            else:
                keep.append(cand)
        self._pending = keep                # 呼ぶ前に直す（コールバックが例外を出しても残らない）
        for cand in due:
            if hold_ready(cand.key, cand.at, now):
                self._on_ready(cand.at)


# ═══════════════════════════════════════════════
#  ログを自分で読む見張り（マクロとは別。通知する が ON のあいだ）
# ═══════════════════════════════════════════════
_CHUNK_BYTES = 256 * 1024
_MARKS = (b"instances/", b"Joining", _MARK_READY_GONE.encode("utf-8"))


class _Followed:
    """追いかけている 1 本のログ"""
    __slots__ = ("path", "order", "pos", "rest", "cands", "last_grow", "last_at")

    def __init__(self, path, order, cands, now):
        self.path, self.order, self.cands = path, order, cands
        self.pos = 0                    # 見つけたら先頭から読む
        self.rest = b""                 # 書きかけの行（改行がまだ来ていない末尾。バイトのまま持ち越す）
        self.last_grow = now
        self.last_at = None             # そのログで読んだ行の、いちばん新しいログの時刻（Remove の行も含む）


class Watcher:
    """VRChat のログを自分で読み、待機列に入れるようになったら on_signal() を 1 回呼ぶ。

    読むだけ（マクロの操作・入力・OSC はしない）。poll() が 1 回ぶんで、テストはこれを直接回す。
    start() で裏のスレッド（daemon）が poll() を QUEUE_WATCH_INTERVAL_SEC ごとに回す。
    on_signal は裏のスレッドから呼ばれる（Tk の変数には触らないこと）。
    on_queued は、待機列に入れた行を読んだとき（そのログの時刻が QUEUE_READY_NOTICE_SEC 秒未満のものだけ）に
    裏のスレッドから 1 回呼ぶ（無ければ呼ばない。インスタンスの印は渡さない）"""

    def __init__(self, log_dir, on_signal, clock=time.monotonic, wall=time.time, on_queued=None):
        self._log_dir = Path(log_dir)
        self._on_signal = on_signal
        self._on_queued = on_queued
        self._clock = clock
        self._wall = wall
        self._gone: dict = {}           # 通知の id → 作られた時刻（poll のあいだだけ。id で 1 回に数える）
        self._logs: dict = {}           # パス → _Followed（見つけた順）
        self._order = 0
        self._last_scan = None
        self._stop = threading.Event()
        self._thread = None

    # ── 1 回ぶん ──────────────────────────
    def poll(self):
        now = self._clock()
        if self._last_scan is None or now - self._last_scan >= config.QUEUE_WATCH_SCAN_SEC:
            self._last_scan = now
            self._scan(now)
        # 全部のログで読んだ行を 1 つにまとめ、時刻順（同じ時刻はログを見つけた順）で渡す。
        # ログAの POST とログBの Get は、時刻の順に判定する（最初の読みでも）
        batch = []
        followed = list(self._logs.values())
        for log in followed:
            try:
                for at, text in self._read_new(log, now):
                    batch.append((at, log.order, text, log))
            except OSError:
                continue
        batch.sort(key=lambda item: (item[0], item[1]))
        # 流す前に、順番の通知が消えた行だけ先に拾う（読み直しのときは、Get とその Remove が同じ束に入る。
        # 合図を出す直前に見て、もう消えている通知で知らせないため）
        self._gone = {}
        for _at, _order, text, _log in batch:
            found = _read_ready_gone(text)
            if found is not None:
                self._gone[found[0]] = found[1]
        for at, _order, text, log in batch:
            log.cands.feed(at, text)
        for log in followed:
            log.cands.tick()
        # 前の poll で出した合図の答え合わせ（生きているログでは Remove は 59 秒後の別の poll で来る）
        for created_at in self._gone.values():
            note_ready_gone(created_at)
        gave_up = release_unconfirmed(now)
        if gave_up:
            DebugLog.write(f"[待機列] 答え合わせできなかった合図 {gave_up} 件（記録は残す）")
        self._gone = {}

    def _scan(self, now):
        """ログの一覧を見直す。生きているログを追いかけ始め、長く止まったログはやめる"""
        for path, log in list(self._logs.items()):
            if now - log.last_grow >= config.QUEUE_FORGET_SEC:
                del self._logs[path]
        try:
            paths = VRChatDiscovery.find_latest_logs(self._log_dir, config.LOG_MATCH_CANDIDATE_COUNT)
        except OSError:
            return
        wall = self._wall()
        for path in paths:
            if path in self._logs:
                continue
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue
            if wall - mtime > config.LOG_LIVE_GRACE_SEC:
                continue
            self._order += 1
            log = _Followed(path, self._order, None, now)
            log.cands = Candidates(lambda at, lg=log: self._ready(at, lg), self._clock, on_queued=self._queued)
            self._logs[path] = log

    def _read_new(self, log, now) -> list:
        """増えた分だけ読む。(ログの時刻, 行) の一覧（待機列に関係する行だけ）。
        増えていないログは開かない。開いたらすぐ閉じる（ハンドルを持ち続けない）"""
        size = os.stat(log.path).st_size
        if size < log.pos:                  # 短くなった（作り直し）。先頭から読み直す
            log.pos = 0
            log.rest = b""
        if size == log.pos:
            return []
        out = []
        with open(log.path, "rb") as f:
            f.seek(log.pos)
            while True:
                chunk = f.read(_CHUNK_BYTES)
                if not chunk:
                    break
                # 書きかけの行は読まない（半分の行・日本語の文字の途中を判定に使わない）
                lines = (log.rest + chunk).split(b"\n")
                log.rest = lines.pop()
                for raw in lines:
                    if not any(mark in raw for mark in _MARKS):     # 関係ない行は持たない
                        continue
                    text = raw.rstrip(b"\r").decode("utf-8", errors="replace")
                    at = LogParser.log_time(text)
                    if at is not None:
                        out.append((at, text))
                        if log.last_at is None or at > log.last_at:
                            log.last_at = at
            log.pos = f.tell()
        log.last_grow = now
        return out

    def _ready(self, get_at: float, log):
        """合図が出た。知らせるかを決める（モジュールの docstring の判定 1〜4）。

        1. その Get の通知がもう消えている（同じ束に Remove の行がある）→ 黙る。記録も忘れる
        2. Remove は無いが、その Get より QUEUE_READY_CONFIRM_SEC 秒以上後の行を同じログで読んでいる
           → 通知が出ていれば Remove が出ているはずなので誤検出 → 黙る。記録は残す
        3. ログが何も語らない（1 も 2 も無い）が、実時間で QUEUE_READY_NOTICE_SEC 秒以上前
           → 黙る。記録は残す（VRChat が落ちた直後のログを読んだとき）
        4. どれでもない → 知らせる（答え合わせは poll が続ける）"""
        match = config.QUEUE_READY_MATCH_SEC
        if any(abs(made - get_at) <= match for made in self._gone.values()):
            note_ready_gone(get_at)         # 本物だったが、もう消えている。記録を忘れる
            return
        if log.last_at is not None and log.last_at >= get_at + config.QUEUE_READY_CONFIRM_SEC:
            release_ready(get_at)
            return
        if self._wall() - get_at >= config.QUEUE_READY_NOTICE_SEC:
            release_ready(get_at)
            return
        try:
            self._on_signal()
        except Exception:
            DebugLog.exception("QueueWatch.Watcher")

    def _queued(self, post_at: float):
        """待機列に入れた行を読んだ。POST が古い（ログを先頭から読み直しているだけの）ものは黙る"""
        if self._wall() - post_at >= config.QUEUE_READY_NOTICE_SEC:
            return
        if self._on_queued is None:
            return
        try:
            self._on_queued()
        except Exception:
            DebugLog.exception("QueueWatch.Watcher")

    # ── 裏のスレッド ───────────────────────
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        reset()
        self._stop.clear()
        self._thread = TaskStats.thread("QueueWatch.Watcher._run", self._run, daemon=True,
                                        thread_name="QueueWatcher")
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    def _run(self):
        while True:
            try:
                self.poll()
            except Exception:
                DebugLog.exception("QueueWatch.Watcher")
            if self._stop.wait(config.QUEUE_WATCH_INTERVAL_SEC):
                break


def notify_queued(logger):
    """待機列に入った（Watcher の裏のスレッドから）。ツールのログに 1 行書くだけ（声・スマホへの送信はしない）。
    インスタンスの印はログに出さない"""
    logger("[待機列] 並びました")


def notify_ready(logger):
    """合図が出た（Watcher の裏のスレッドから）。ツールのログに書き、「待機列に入れるようになったら知らせる」が
    ON なら待機列の声を 1 回鳴らし（「アナウンス」の待機列のチェックが OFF なら鳴らない）、そのうえでスマホ通知の
    「通知する」が ON で宛先が正しければスマホへ送る（前は声も送信も「通知する」だけで決まっていた）。
    声の失敗は送信を止めず、送信の失敗は声に響かない。完全放置モードでも鳴る。
    logger は裏のスレッドから呼んでよいもの。宛先・インスタンスの印はログに出さない"""
    logger("[待機列] 入れるようになりました")
    try:
        if not SharedState.get_queue_notify():
            return
    except Exception:
        DebugLog.exception("QueueWatch.notify_ready")
        return
    try:
        PlaySound.play_sound(SharedState.get_queue_voice(), kind="queue")
    except Exception:
        DebugLog.exception("QueueWatch.notify_ready.sound")
    try:
        if not SharedState.get_phone_notify():
            return
        topic = SharedState.get_phone_topic()
        if not PhoneNotify.is_valid_topic(topic):
            return
        title, message = PhoneNotify.queue_ready_text()

        def done(ok: bool, reason: str):
            if not ok:
                logger(f"スマホ通知: 送信に失敗しました（{reason}）")
        PhoneNotify.send(topic, title, message, on_done=done)
    except Exception:
        DebugLog.exception("QueueWatch.notify_ready")
