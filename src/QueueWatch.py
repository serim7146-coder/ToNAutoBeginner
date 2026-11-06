"""グループインスタンスの待機列に入れるようになった合図を、VRChat のログから読む。

マクロの開始・窓とログの割り当てとは関係なく、スマホ通知の「通知する」が ON のあいだ、ツールが自分で VRChat の
ログ（output_log_*.txt）を読む（Watcher）。読むだけで、マクロの操作・入力・OSC は何もしない。待機列はアカウントの
もので窓のものではない（1 つの窓で並ぶと、全部の窓が同じ待機列に動く）ので、窓の番号は使わない。

読む行（接頭辞の `時刻 Debug -` を外した後。ID は伏せてある）:
  待機列に入れた   [API] Requesting Post instances/wrld_X:39837~group(grp_X)~groupAccessType(plus)~region(jp)/queue {{}} retryCount: 2
  入室の候補       [API] Requesting Get instances/wrld_X:39837~group(grp_X)~groupAccessType(plus)~region(jp) {{}} retryCount: 2
  待機列を抜けた   [API] Requesting Delete instances/wrld_X:39837~…/queue …
  入室した         [Behaviour] Joining wrld_X:39837~…

合図の決まり。あるログの Get の行が、次の 3 つを全部満たしたとき:
1. そのインスタンスが、どれかのログで待機列に入れられている（POST。ログどうしで共有する。アカウントは
   共通なので、POST しなかった窓にも同じ Get が出る）
2. Get のログの時刻が、その POST のログの時刻より QUEUE_READY_AFTER_POST_SEC 秒以上後
3. その Get のログに、前後 QUEUE_READY_QUIET_SEC 秒以内（両端を含む）に、別のインスタンスの Get が無い
   （同じインスタンスの Get は数えない。メニューの行は決まりに入れない）
インスタンスの印は `wrld_…:番号~…` の全部。

待機列に入れたものを忘れるとき: 合図が出た・Delete を読んだ・Joining を読んだ・POST から QUEUE_FORGET_SEC 秒たった。
ログは最初に見つけたとき先頭から読む（ツールを開く前・通知を入れる前の POST も数える。そのとき読んだ合図は
記録も一緒に使い切るので、あとで出直さない）。Get のログの時刻が QUEUE_READY_NOTICE_SEC 秒以上前の合図は、
VRChat の通知が消えているので、黙って使い切る（ログにも出さず、送らない）。

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

_RE_API = re.compile(r"^\[API\] Requesting (Get|Post|Delete) instances/(wrld_[^\s/]+)(/queue)?(?:\s|$)")
_RE_JOIN = re.compile(r"^\[Behaviour\] Joining (wrld_\S+)")


# ═══════════════════════════════════════════════
#  ログどうしで共有する、待機列に入れたインスタンス
# ═══════════════════════════════════════════════
@dataclass(frozen=True)
class Post:
    """待機列に入れた（POST）記録。at はその行のログの時刻"""
    at: float


_lock = threading.Lock()
_queued: dict = {}          # インスタンスの印 → Post


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


def note_join(key: str):
    """入室した"""
    with _lock:
        _queued.pop(key, None)


def take_ready(key: str, get_at: float):
    """合図にしてよいなら、待機列に入れた記録（Post）を返して忘れる。そうでなければ None。
    POST から QUEUE_FORGET_SEC 秒たっていたら忘れる。QUEUE_READY_AFTER_POST_SEC 秒に足りなければ記録は残す"""
    with _lock:
        post = _queued.get(key)
        if post is None:
            return None
        age = get_at - post.at
        if age >= config.QUEUE_FORGET_SEC:
            del _queued[key]
            return None
        if age < config.QUEUE_READY_AFTER_POST_SEC:
            return None
        del _queued[key]
        return post


def reset():
    """全部忘れる（Watcher.start とテスト用）"""
    with _lock:
        _queued.clear()


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
    clock は実時間（テストが差し替える）"""

    def __init__(self, on_ready, clock=time.monotonic):
        self._on_ready = on_ready
        self._clock = clock
        self._recent: list = []         # このログで最近読んだ Get (ログの時刻, 印)。前後の判定用
        self._pending: list = []        # 閉じるのを待っている候補

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
                    self._on_get(at, key)
            elif verb == "Post" and queue:
                if at is not None:
                    note_post(key, at)
            elif verb == "Delete" and queue:
                note_delete(key)
            return
        m = _RE_JOIN.match(text)
        if m:
            note_join(m.group(1))

    def _on_get(self, at: float, key: str):
        quiet = config.QUEUE_READY_QUIET_SEC
        self._recent = [r for r in self._recent if abs(at - r[0]) <= quiet]
        conflict = any(r_key != key for _r_at, r_key in self._recent)
        for cand in self._pending:
            if cand.key != key and abs(at - cand.at) <= quiet:
                cand.cancelled = True       # 後ろ 3 秒以内に別のインスタンスの Get
        self._recent.append((at, key))
        if not conflict:                    # 前 3 秒以内に別のインスタンスの Get があれば候補にしない
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
            if take_ready(cand.key, cand.at) is not None:
                self._on_ready(cand.at)


# ═══════════════════════════════════════════════
#  ログを自分で読む見張り（マクロとは別。通知する が ON のあいだ）
# ═══════════════════════════════════════════════
_CHUNK_BYTES = 256 * 1024
_MARKS = (b"instances/", b"Joining")


class _Followed:
    """追いかけている 1 本のログ"""
    __slots__ = ("path", "order", "pos", "rest", "cands", "last_grow")

    def __init__(self, path, order, cands, now):
        self.path, self.order, self.cands = path, order, cands
        self.pos = 0                    # 見つけたら先頭から読む
        self.rest = b""                 # 書きかけの行（改行がまだ来ていない末尾。バイトのまま持ち越す）
        self.last_grow = now


class Watcher:
    """VRChat のログを自分で読み、待機列に入れるようになったら on_signal() を 1 回呼ぶ。

    読むだけ（マクロの操作・入力・OSC はしない）。poll() が 1 回ぶんで、テストはこれを直接回す。
    start() で裏のスレッド（daemon）が poll() を QUEUE_WATCH_INTERVAL_SEC ごとに回す。
    on_signal は裏のスレッドから呼ばれる（Tk の変数には触らないこと）"""

    def __init__(self, log_dir, on_signal, clock=time.monotonic, wall=time.time):
        self._log_dir = Path(log_dir)
        self._on_signal = on_signal
        self._clock = clock
        self._wall = wall
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
        for at, _order, text, log in batch:
            log.cands.feed(at, text)
        for log in followed:
            log.cands.tick()

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
            self._logs[path] = _Followed(path, self._order, Candidates(self._ready, self._clock), now)

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
            log.pos = f.tell()
        log.last_grow = now
        return out

    def _ready(self, get_at: float):
        """合図が出た。Get が古い（VRChat の通知が消えている）ものは黙って使い切る"""
        if self._wall() - get_at >= config.QUEUE_READY_NOTICE_SEC:
            return
        try:
            self._on_signal()
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


def notify_ready(logger):
    """合図が出た（Watcher の裏のスレッドから）。ツールのログに書き、「通知する」が ON なら、待機列の声を 1 回鳴らし
    （「アナウンス」の待機列のチェックが OFF なら鳴らない）、宛先が正しければスマホへ送る。「通知する」が OFF なら
    声も送信もしない。声の失敗は送信を止めず、送信の失敗は声に響かない。完全放置モードでも鳴る。
    logger は裏のスレッドから呼んでよいもの。宛先・インスタンスの印はログに出さない"""
    logger("[スマホ通知] 待機列に入れるようになりました")
    try:
        if not SharedState.get_phone_notify():
            return
    except Exception:
        DebugLog.exception("QueueWatch.notify_ready")
        return
    try:
        PlaySound.play_sound(SharedState.get_queue_voice(), kind="queue")
    except Exception:
        DebugLog.exception("QueueWatch.notify_ready.sound")
    try:
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
