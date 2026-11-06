"""グループインスタンスの待機列に入れるようになった合図を、VRChat のログから読む。

読む行（接頭辞の `時刻 Debug -` を外した後。ID は伏せてある）:
  待機列に入れた   [API] Requesting Post instances/wrld_X:39837~group(grp_X)~groupAccessType(plus)~region(jp)/queue {{}} retryCount: 2
  入室の候補       [API] Requesting Get instances/wrld_X:39837~group(grp_X)~groupAccessType(plus)~region(jp) {{}} retryCount: 2
  待機列を抜けた   [API] Requesting Delete instances/wrld_X:39837~…/queue …
  入室した         [Behaviour] Joining wrld_X:39837~…

合図の決まり。その窓の Get の行が、次の 3 つを全部満たしたとき:
1. そのインスタンスが、どれかの監視中の窓で待機列に入れられている（POST。窓どうしで共有する。アカウントは
   共通なので、POST しなかった窓にも同じ Get が出る）
2. Get のログの時刻が、その POST のログの時刻より QUEUE_READY_AFTER_POST_SEC 秒以上後
3. その窓のログに、前後 QUEUE_READY_QUIET_SEC 秒以内（両端を含む）に、別のインスタンスの Get が無い
   （同じインスタンスの Get は数えない。メニューの行は決まりに入れない）
インスタンスの印は `wrld_…:番号~…` の全部。

待機列に入れたものを忘れるとき: 合図が出た・Delete を読んだ・Joining を読んだ・POST から QUEUE_FORGET_SEC 秒たった。
監視を始めてから読んだ POST だけを数える（さかのぼって探さない）。

後ろの 3 秒は、次の行が来るのを待たない（行の間が 55 秒開いた窓があり、VRChat の通知は 60 秒しか出ない）。
候補は「候補より 3 秒以上後の時刻の行を読んだ」か「読んでから実時間で 3 秒たった」（tick。読み取りのループが
0.3 秒ごとに呼ぶ）ときに閉じる。閉じる前に読んだ行に別のインスタンスの Get があれば取り消す。

インスタンスの ID はどのログにも出さない（ここは何も書かない。呼ぶ側のコールバックにも渡さない）。
"""
import re
import threading
import time
from dataclasses import dataclass

import config
import LogParser

# 全部の行がここを通るので、まず安い部分文字列の検査で絞ってから正規表現を使う
_MARK_API = "instances/"
_MARK_JOIN = "Joining"

_RE_API = re.compile(r"^\[API\] Requesting (Get|Post|Delete) instances/(wrld_[^\s/]+)(/queue)?(?:\s|$)")
_RE_JOIN = re.compile(r"^\[Behaviour\] Joining (wrld_\S+)")


# ═══════════════════════════════════════════════
#  窓どうしで共有する、待機列に入れたインスタンス
# ═══════════════════════════════════════════════
@dataclass(frozen=True)
class Post:
    """待機列に入れた（POST）記録。window_idx はその行を読んだ窓、hands_free は読んだ時点のその窓の放置モード"""
    window_idx: int
    at: float
    hands_free: bool


_lock = threading.Lock()
_queued: dict = {}          # インスタンスの印 → Post


def note_post(key: str, at: float, window_idx: int, hands_free: bool):
    """待機列に入れた。同じインスタンスを入れ直したら、新しい方で置き換える（10 秒は新しい POST から数える）"""
    with _lock:
        for old in [k for k, p in _queued.items() if at - p.at >= config.QUEUE_FORGET_SEC]:
            del _queued[old]
        _queued[key] = Post(window_idx, at, bool(hands_free))


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
    """全部忘れる（テスト用）"""
    with _lock:
        _queued.clear()


# ═══════════════════════════════════════════════
#  窓ごとの候補（その窓のログの Get）
# ═══════════════════════════════════════════════
class _Candidate:
    __slots__ = ("key", "at", "read", "cancelled")

    def __init__(self, key, at, read):
        self.key, self.at, self.read, self.cancelled = key, at, read, False


class Candidates:
    """1 窓ぶん。読み取りのスレッド 1 本からだけ使う（feed と tick は同じスレッド）。

    on_ready(post) は合図が出たとき 1 回呼ぶ（post は待機列に入れた記録。インスタンスの印は渡さない）。
    hands_free() は POST の行を読んだ時点で呼び、記録に残す。clock は実時間（テストが差し替える）"""

    def __init__(self, window_idx: int, on_ready, hands_free=lambda: False, clock=time.monotonic):
        self.window_idx = window_idx
        self._on_ready = on_ready
        self._hands_free = hands_free
        self._clock = clock
        self._recent: list = []         # この窓で最近読んだ Get (ログの時刻, 印)。前後の判定用
        self._pending: list = []        # 閉じるのを待っている候補

    def feed(self, at, line: str):
        """1 行を読んだ。at はその行のログの時刻（epoch 秒。無ければ None）"""
        if _MARK_API in line or _MARK_JOIN in line:
            self._read(at, LogParser.strip_prefix(line))
        if self._pending and at is not None:
            self._close_due(at)

    def tick(self):
        """読み取りのループから 0.3 秒ごとに。読んでから実時間で QUEUE_READY_QUIET_SEC 秒たった候補を閉じる"""
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
                    note_post(key, at, self.window_idx, self._hands_free())
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
            post = take_ready(cand.key, cand.at)
            if post is not None:
                self._on_ready(post)
