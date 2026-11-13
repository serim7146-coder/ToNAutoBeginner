"""手に持ったアイテムの「使用」の判定と、クールタイム（CT）・効果の残りの表示。

「持っている」は VRChat のログの Pickup object / Drop object の区間（店の装備 `Equipping N.` とは別のもの）。
持っている間の左クリック（手・OSC の /input/UseRight・ツールのクリック）を使用と数える。今は Bloodstained Charm
（ログの名前 BloodKatCharm）だけ。ほかのアイテムは TABLE に 1 行足し、判定を足す。

- 状態は窓ごとの UseTracker が持つ。持っているかを書き換えるのは LogMonitor だけ。使用の判定はマウスフック・
  OSC を送るスレッド・ツールのクリックなどからも呼ばれるので、Tracker の中に鍵を 1 つ持つ
- このモジュールはプロジェクトのほかのモジュールを import しない（標準ライブラリと DebugLog だけ。WindowOperator
  からの循環を避ける）。前面の hwnd は呼ぶ側が調べて渡す
- debug.log はフックの中で書かない。Tracker の中に溜め、LogMonitor が take_debug_lines() で取り出して書く
"""
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

import DebugLog

# アイテムの種類
KIND_CLICK_CD = "click_cd"      # クリックで使い、使うとクールタイムに入る
KIND_HOLD = "hold"              # 押している間だけ効く（今は未対応）
KIND_PASSIVE = "passive"        # 持っているだけで効く（今は未対応）
KINDS = (KIND_CLICK_CD, KIND_HOLD, KIND_PASSIVE)

# 使用の元（debug.log の表記）
SOURCE_HAND = "手"
SOURCE_OSC = "OSC"
SOURCE_TOOL = "ツール"

USE_ADDRESS = "/input/UseRight"

# use() の結果
USED = "used"
COOLDOWN = "cooldown"
NOT_HOLDING = "not_holding"
NOT_USABLE = "not_usable"

_EPS = 1e-9                     # CT の明けの境目（ちょうどは使用に数える）
_REMAIN_EPS = 1e-6              # 残りがこれ以下なら終わったもの
_DEBUG_LINES_MAX = 100          # 溜めておく debug.log の行の上限（書かれないまま増え続けないように）


@dataclass(frozen=True)
class UseItem:
    kind: str
    log_name: str               # VRChat のログの名前（Pickup object: 'BloodKatCharm'）
    display_name: str           # 画面の名前（店の名前。assets/data/item.json）
    cooldown_sec: float         # CT の基礎の秒数
    cooldown_per_player: float = 0.0    # 人数が 1 人増えるごとに足す秒数（基礎 +（人数 − 1）× これ）
    effect_sec: float = 0.0


# 効果の秒数は wiki の約の値。実機で直す前提
TABLE: tuple = (
    UseItem(KIND_CLICK_CD, "BloodKatCharm", "Bloody Kat Charm", 1.0, 0.0, 2.0),
)


def item(log_name):
    """ログの名前から表の行を引く。無ければ None"""
    for row in TABLE:
        if row.log_name == log_name:
            return row
    return None


def cooldown_for(row: UseItem, players) -> float:
    """CT の秒数。人数が None（分からない）なら 1 人とみなす"""
    count = 1 if players is None else max(1, int(players))
    return row.cooldown_sec + (count - 1) * row.cooldown_per_player


def _ceil_tenth(value: float) -> float:
    """0.1 秒単位で切り上げる（浮動小数の端数で 1 つ多くならないよう少し引く）"""
    return math.ceil(value * 10 - 1e-6) / 10


class UseTracker:
    """1 つの窓の「持っているアイテム」と使用の記録。メソッドはどれも鍵の中で動く"""

    def __init__(self, window_idx, clock=time.monotonic, players=None):
        self.window_idx = window_idx
        self._clock = clock
        self._players = players         # 呼ぶと今の人数（int か None）を返す関数。CT が人数で変わる物だけが呼ぶ
        self._lock = threading.Lock()
        self._held = ""                 # 持っているアイテムのログの名前（空 = 持っていない）
        # ログの名前 → [CT の明ける時刻, 効果の終わる時刻]。Drop しても消さない（持ち直したら CT の続き）
        self._times: dict = {}
        self._cd_presses = 0            # CT 中の押下の数（次の使用の行に添えて 0 に戻す）
        self._osc_down = False          # OSC の /input/UseRight がいま 1 か
        self._lines: deque = deque(maxlen=_DEBUG_LINES_MAX)

    # ── 持っているか（LogMonitor だけが呼ぶ）──────────
    def pickup(self, name):
        """拾った。表にある名前なら持っている。無い名前なら持っていない（片手で 1 つの前提）"""
        with self._lock:
            if name == self._held:
                return
            if item(name) is None:
                if self._held:
                    self._lines.append(f"離した: {self._held}")
                self._held = ""
                return
            if self._held:
                self._lines.append(f"離した: {self._held}")   # Drop の無い次の Pickup は置き換える
            self._held = name
            self._lines.append(f"持った: {name}")

    def drop(self, name):
        """離した。持っているものと同じ名前のときだけ持っていないに戻す"""
        with self._lock:
            if self._held and name == self._held:
                self._held = ""
                self._lines.append(f"離した: {name}")

    def reset(self, reason):
        """持っていない・CT と効果の時刻も消す・OSC の押下の状態も戻す"""
        with self._lock:
            if self._held:
                self._lines.append(f"持っていない: {reason}")
            self._held = ""
            self._times.clear()
            self._cd_presses = 0
            self._osc_down = False

    # ── 使用の判定 ──────────────────────────
    def _cooldown_sec(self, row: UseItem) -> float:
        """今の CT の秒数。係数が 0 の物は人数を見ない。人数が取れなければ基礎の秒数"""
        if not row.cooldown_per_player:
            return row.cooldown_sec
        try:
            players = self._players() if self._players is not None else None
        except Exception:
            DebugLog.exception("ItemUse.UseTracker._cooldown_sec")
            players = None
        return cooldown_for(row, players)

    def _use_locked(self, source):
        now = self._clock()
        name = self._held
        if not name:
            return NOT_HOLDING
        row = item(name)
        if row is None or row.kind != KIND_CLICK_CD:
            return NOT_USABLE
        times = self._times.get(name)
        if times is not None and now < times[0] - _EPS:
            self._cd_presses += 1
            return COOLDOWN
        cd = self._cooldown_sec(row)
        self._times[name] = [now + cd, now + row.effect_sec]
        line = f"使用: {name}（{source}・CT {cd:.1f}秒・効果 {row.effect_sec:.1f}秒）"
        if self._cd_presses:
            line += f"（CT中の押下 {self._cd_presses} 回は数えず）"
            self._cd_presses = 0
        self._lines.append(line)
        return USED

    def use(self, source):
        """使用の合図（手のクリック・OSC・ツールのクリック）。"used" / "cooldown" / "not_holding" / "not_usable" """
        with self._lock:
            return self._use_locked(source)

    def osc_use_input(self, down: bool):
        """/input/UseRight の値が変わった。0→1（押し始め）だけ使用。押したままの送り直し（1→1）は数えない"""
        with self._lock:
            was, self._osc_down = self._osc_down, bool(down)
            if down and not was:
                return self._use_locked(SOURCE_OSC)
        return None

    # ── 表示 ───────────────────────────────
    def status(self, now=None):
        """持っていれば (ログの名前, 表示の名前, 効果の残り秒, CT の残り秒)。持っていなければ None"""
        with self._lock:
            name = self._held
            if not name:
                return None
            if now is None:
                now = self._clock()
            row = item(name)
            times = self._times.get(name)
            cd_left = max(0.0, times[0] - now) if times else 0.0
            effect_left = max(0.0, times[1] - now) if times else 0.0
            return name, (row.display_name if row else name), effect_left, cd_left

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


def status_text(window_idx, status) -> str:
    """オーバーレイの 1 行。status は UseTracker.status() の戻り（持っていなければ空文字）"""
    if not status:
        return ""
    _name, display, effect_left, cd_left = status
    parts = []
    if effect_left > _REMAIN_EPS:
        parts.append(f"効果 {_ceil_tenth(effect_left):.1f}秒")
    if cd_left > _REMAIN_EPS:
        parts.append(f"CT {_ceil_tenth(cd_left):.1f}秒")
    else:
        parts.append("使用可")      # 効果だけ残っているときも、何も無いときも
    return f"窓{window_idx} {display}  " + "・".join(parts)


def reset_for_tests():
    """テストの後始末用。登録表を空に戻す"""
    with _registry_lock:
        _registry.clear()
