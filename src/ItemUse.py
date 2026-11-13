"""手に持っている物と、その間の手のクリックの記録。

「持っている」は VRChat のログの Pickup object / Drop object の区間（店の装備 `Equipping N.` とは別のもの）。
名前は問わず覚える。持っている間の手の左クリックを、そのとき持っていた物の名前つきで 1 回分だけ覚える。
使い道は 2 つ（どちらも LogMonitor / ActionExecutor が判定する）: 消費系アイテムを手に持ってクリックしたら
アイテムロスト、Maxwell を手に持ったまま自爆したらアイテムロスト。数えるのは手のクリックだけで、ツール自身の
OSC・クリックは数えない。

- 状態は窓ごとの UseTracker が持つ。持っているかを書き換えるのは LogMonitor だけ。クリックの記録はマウスフックの
  スレッドから呼ばれるので、Tracker の中に鍵を 1 つ持つ
- このモジュールはプロジェクトのほかのモジュールを import しない（標準ライブラリだけ。WindowOperator からの循環を
  避ける）。前面の hwnd は呼ぶ側が調べて渡す
- debug.log はフックの中で書かない。Tracker の中に溜め、LogMonitor が take_debug_lines() で取り出して書く
"""
import threading
import time
from collections import deque

# クリックの元（debug.log の表記）
SOURCE_HAND = "手"

_DEBUG_LINES_MAX = 100          # 溜めておく debug.log の行の上限（書かれないまま増え続けないように）


def norm(name) -> str:
    """ログの名前を比べる形にする（空白をすべて除き、大文字小文字を見ない）。None・空は ""。"""
    if not name:
        return ""
    return "".join(str(name).split()).casefold()


class UseTracker:
    """1 つの窓の「手に持っている物」と手のクリックの記録。メソッドはどれも鍵の中で動く"""

    def __init__(self, window_idx, clock=time.monotonic):
        self.window_idx = window_idx
        self._clock = clock
        self._lock = threading.Lock()
        self._held = ""                 # 手に持っている物のログの名前（空 = 持っていない）
        self._used = None               # クリックの印: そのとき持っていた物の名前（None = 無し）
        self._lines: deque = deque(maxlen=_DEBUG_LINES_MAX)

    # ── 持っているか（LogMonitor だけが呼ぶ）──────────
    def pickup(self, name):
        """拾った。名前は問わず持っているとして覚える。同じ名前なら何もしない。別の物は置き換える"""
        with self._lock:
            if name == self._held:
                return
            if self._held:
                self._lines.append(f"離した: {self._held}")   # Drop の無い次の Pickup は置き換える
            self._held = name
            self._used = None
            self._lines.append(f"持った: {name}")

    def drop(self, name):
        """離した。持っているものと同じ名前のときだけ持っていないに戻す"""
        with self._lock:
            if self._held and name == self._held:
                self._held = ""
                self._used = None
                self._lines.append(f"離した: {name}")

    def reset(self, reason):
        """持っていない・クリックの印も消す"""
        with self._lock:
            if self._held:
                self._lines.append(f"持っていない: {reason}")
            self._held = ""
            self._used = None

    def held_name(self) -> str:
        """今持っている物のログの名前（持っていなければ ""）"""
        with self._lock:
            return self._held

    # ── クリック ────────────────────────────
    def use(self, source) -> bool:
        """手のクリックがあった。持っていれば、そのとき持っていた物の名前を印として覚えて True（印が残っていれば
        上書きしない）。持っていなければ False。debug.log の行は溜めない（連打で溢れさせない）"""
        with self._lock:
            if not self._held:
                return False
            if self._used is None:
                self._used = self._held
            return True

    def take_use(self):
        """クリックの印があれば、その名前を返して印を消す。無ければ None"""
        with self._lock:
            name, self._used = self._used, None
            return name

    def take_debug_lines(self) -> list:
        """溜まった debug.log の行を取り出す（取り出した分は消える）"""
        with self._lock:
            lines = list(self._lines)
            self._lines.clear()
            return lines


# ── hwnd → Tracker の登録表 ─────────────────────
_registry_lock = threading.Lock()
_registry: dict = {}


def register(hwnd, tracker):
    if not hwnd:
        return
    with _registry_lock:
        _registry[int(hwnd)] = tracker


def unregister(hwnd, tracker):
    """同じ Tracker のときだけ外す（別の窓の登録を巻き込まない）"""
    if not hwnd:
        return
    with _registry_lock:
        if _registry.get(int(hwnd)) is tracker:
            del _registry[int(hwnd)]


def tracker_for(hwnd):
    if not hwnd:
        return None
    with _registry_lock:
        return _registry.get(int(hwnd))


def on_click(hwnd, source):
    """hwnd の窓へのクリックを使用の合図として渡す。hwnd が 0・登録の無い窓なら何もしない"""
    tracker = tracker_for(hwnd)
    if tracker is None:
        return None
    return tracker.use(source)


def reset_for_tests():
    """テストの後始末用。登録表を空に戻す"""
    with _registry_lock:
        _registry.clear()
