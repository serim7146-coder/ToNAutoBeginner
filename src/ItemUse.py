"""手に持ったアイテムの「使用」の判定と、クールタイム（CT）・効果の残りの表示。

「持っている」は VRChat のログの Pickup object / Drop object の区間（店の装備 `Equipping N.` とは別のもの）。
持っている間の左クリック（手・OSC の /input/UseRight・ツールのクリック）を使用と数える。表（TABLE）にある
アイテムは Bloodstained Charm と武器（バット・コイル・銃など）。表に無いアイテムは持っているとは数えない。

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

FULL_INSTANCE_PLAYERS = 16      # 満員の人数（CT が cooldown_full_sec になる人数）

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
    log_name: str               # VRChat のログの名前（Pickup object: 'BloodKatCharm'）。空白の無い形
    display_name: str           # 画面の名前（店の名前。assets/data/item.json）
    cooldown_sec: float         # CT の基礎の秒数（1 人のとき）
    cooldown_per_player: float = 0.0    # 人数が 1 人増えるごとに足す秒数（基礎 +（人数 − 1）× これ）
    effect_sec: float = 0.0             # 使うと必ず付く効果のうち、いちばん長いものの秒数
    cooldown_full_sec: float = 0.0      # 満員（16 人）の CT。0 より大きければ 1〜16 人を直線でつなぐ（per_player は使わない）
    cooldown_players_cap: int = FULL_INSTANCE_PLAYERS   # これより多い人数はこの人数として数える
    auto_click_sec: float = 0.0         # 自動クリックの間隔の秒数。0 は自動クリックの対象でない
    aliases: tuple = ()                 # ログに出うる別の名前（空白・大文字小文字は見ない）


# 秒数は wiki のアイテムのページの値（CD 早見表より優先）。効果の秒数は約の値で、実機で直す前提。
# ログの名前は wiki の名前から空白を除いたもの。表示の名前は店の名前
_CLICK = KIND_CLICK_CD
TABLE: tuple = (
    UseItem(_CLICK, "BloodKatCharm", "Bloody Kat Charm", 1.0, 0.0, 2.0, auto_click_sec=2.0),
    UseItem(_CLICK, "MetalBat", "Metal Bat", 15.0, 1.0),
    # Bloodstained Bat: 殴ってダメージを与えると CT が戻る（ツールにはダメージが見えない。表の値は戻らないときの CT）
    UseItem(_CLICK, "BloodstainedBat", "Bloody Bat", 20.0, 1.0, aliases=("BloodyBat",)),
    UseItem(_CLICK, "MetalPipe", "Metal Pipe", 16.0, 1.0),
    UseItem(_CLICK, "Justitia", "Justitia", 22.0, 1.0),
    UseItem(_CLICK, "ColorableBat", "Color Bat", 15.0, 1.0, aliases=("ColorBat",)),
    UseItem(_CLICK, "GhostBat", "Ghost Bat", 15.0, 1.0),
    UseItem(_CLICK, "CorkscrewCoil", "Corkscrew Coil", 30.0, 0.0, 3.0, cooldown_full_sec=35.0),
    UseItem(_CLICK, "DestroyerCoil", "Destroyer Coil", 20.0, 0.5, 15.0),
    UseItem(_CLICK, "AntiqueRevolver", "Rusty Pistol", 25.0, 1.0, aliases=("RustyPistol",)),
    UseItem(_CLICK, "BloodyRevolver", "Bloody Pistol", 3.5, 0.0, cooldown_full_sec=7.0, aliases=("BloodyPistol",)),
    UseItem(_CLICK, "PalePistol", "Pale Pistol", 27.0, 1.0),
    UseItem(_CLICK, "RubberMallet", "Rubber Hammer", 15.0, 1.0, aliases=("RubberHammer",)),
    UseItem(_CLICK, "Taser", "Taser", 28.0, 1.0),
    UseItem(_CLICK, "GranFaust", "Gran Faust", 12.5, 0.0, 5.0, cooldown_full_sec=17.0),
    UseItem(_CLICK, "Illumina", "Illumina", 8.0, 1.0, cooldown_players_cap=10),
    UseItem(_CLICK, "ParadiseLost", "Paradise Lost", 27.0, 1.0),
    UseItem(_CLICK, "TorchofObsession", "Torch of Obsession", 26.0, 1.0, 19.0),
    # Dropkick: ゲームの CT は着地から数える（ツールはクリックから数える）。人数では変わらない
    UseItem(_CLICK, "Dropkick", "Dropkick", 25.0),
    UseItem(_CLICK, "TBH", "TBH", 22.0, 0.0, cooldown_full_sec=27.0),
    UseItem(_CLICK, "RedBull", "Redbull", 40.0, 0.0, 14.0),
)


def _norm(name) -> str:
    """ログの名前を比べる形（空白を除き、大文字小文字を同じに）"""
    return str(name).replace(" ", "").casefold()


_INDEX: dict = {}               # _norm(名前) → 表の行（ログの名前と aliases の全部）
_index_table = None             # _INDEX を作ったときの TABLE（テストが TABLE を差し替えたら作り直す）


def _index() -> dict:
    global _INDEX, _index_table
    table = TABLE
    if _index_table is not table:
        index: dict = {}
        for row in table:
            for name in (row.log_name, *row.aliases):
                index[_norm(name)] = row
        _INDEX, _index_table = index, table
    return _INDEX


def item(name):
    """ログの名前から表の行を引く（空白・大文字小文字は見ない。別名も引ける）。無ければ None"""
    return _index().get(_norm(name))


def cooldown_for(row: UseItem, players) -> float:
    """CT の秒数。人数が None（分からない）なら 1 人とみなす。人数は row.cooldown_players_cap までで頭打ち。
    cooldown_full_sec があれば 1〜満員（16 人）を直線でつなぎ、無ければ基礎 +（人数 − 1）× per_player"""
    count = 1 if players is None else max(1, int(players))
    count = min(count, row.cooldown_players_cap)
    if row.cooldown_full_sec:
        return row.cooldown_sec + (row.cooldown_full_sec - row.cooldown_sec) * (count - 1) / (FULL_INSTANCE_PLAYERS - 1)
    return row.cooldown_sec + (count - 1) * row.cooldown_per_player


# 表に無いアイテムの名前を debug.log に 1 回だけ書くための記録（全窓で共有。増え続けないよう上限を付ける）
_UNKNOWN_NAME_MAX_CHARS = 64
_UNKNOWN_NAMES_MAX = 200
_unknown_lock = threading.Lock()
_unknown_names: set = set()


def _note_unknown(name) -> bool:
    """この名前を初めて見たなら True（覚える）。上限まで覚えたあとの新しい名前は覚えず False（書かない）。
    プレイヤーの ID（usr_）が入った名前も書かない"""
    key = str(name)[:_UNKNOWN_NAME_MAX_CHARS]
    if "usr_" in key:
        return False                # プレイヤーの ID が入った名前は書かない
    with _unknown_lock:
        if key in _unknown_names or len(_unknown_names) >= _UNKNOWN_NAMES_MAX:
            return False
        _unknown_names.add(key)
        return True


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
        """拾った。表にある名前なら持っている（持っているのは表のログの名前で覚える）。無い名前なら持っていない
        （片手で 1 つの前提）。表に無い名前は debug.log に 1 回だけ書く"""
        with self._lock:
            row = item(name)
            if row is not None and row.log_name == self._held:
                return
            if row is None:
                if self._held:
                    self._lines.append(f"離した: {self._held}")
                self._held = ""
                if _note_unknown(name):
                    self._lines.append(f"表にないアイテム: {str(name)[:_UNKNOWN_NAME_MAX_CHARS]}")
                return
            if self._held:
                self._lines.append(f"離した: {self._held}")   # Drop の無い次の Pickup は置き換える
            self._held = row.log_name
            line = f"持った: {row.log_name}"
            if name != row.log_name:
                line += f"（ログの名前 {name}）"
            self._lines.append(line)

    def drop(self, name):
        """離した。持っているものと同じアイテムのときだけ持っていないに戻す"""
        with self._lock:
            row = item(name)
            if self._held and row is not None and row.log_name == self._held:
                self._held = ""
                self._lines.append(f"離した: {row.log_name}")

    def held_row(self):
        """持っているアイテムの表の行。持っていなければ None"""
        with self._lock:
            return item(self._held) if self._held else None

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
        """今の CT の秒数。人数で変わらない物（per_player も full も 0）は人数を見ない。人数が取れなければ基礎の秒数"""
        if not row.cooldown_per_player and not row.cooldown_full_sec:
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


def status_line(status) -> str:
    """窓の上の表示の文字（窓の番号なし）。status は UseTracker.status() の戻り（持っていなければ空文字）"""
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
    return f"{display}  " + "・".join(parts)


def status_text(window_idx, status) -> str:
    """オーバーレイの 1 行（窓の番号つき）。status は UseTracker.status() の戻り（持っていなければ空文字）"""
    if not status:
        return ""
    return f"窓{window_idx} " + status_line(status)


def reset_for_tests():
    """テストの後始末用。登録表と、表に無いアイテムの名前の記録を空に戻す"""
    with _registry_lock:
        _registry.clear()
    with _unknown_lock:
        _unknown_names.clear()
