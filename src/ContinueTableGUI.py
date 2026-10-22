"""続行表の編集画面（GJ・GK）。ToN ListTool の続行選択画面と同じ並び・色で、テラーのアイコンを押して選ぶ。

- 左: ラウンドの一覧（細い Listbox。Moon は Mystic Moon・Blood Moon・Twilight・Solstice を 1 つにまとめた「Moon」）
- 上: どちらの表を編集するか（続行無効／デフォ続行）とその表のプリセット（選ぶ・新規・複製・名前変更・削除。GL）・
  名前の検索・全選択／全解除。押す・全選択・全解除・読み込みは、選んでいるプリセットに効く
- 中: テラーのアイコンの格子（tk.Canvas に直接描く。間隔は縦横とも 46px）。上から Classic（0〜133・ListTool の
  並び）・Alternate（134〜169・11/9/7/5/3/1 の逆三角）・Unbound（200〜283）・特別（170〜177 ほか）・
  バリアントの段（Classic だけ 190〜192・ほかの 192 があるラウンドは 192・Fog/Midnight の 315/316・
  Unbound の Self Inserts (Bloodthirsty) の仮の番号）。ブロックの間は 1 行あける。名前は出さない（マウスを
  乗せるとツールチップ）。選んでいないものは背景に 30% で重ね、今の表に入っているものは 100%＋2px の枠
  （赤＝続行無効・緑＝デフォ続行）、もう片方の表に入っているものは 60%＋その色の 1px の枠
- 下: .tnl をデフォ続行へ・.tnnl を続行無効へ読み込む

押したセルの絵と枠だけを `itemconfigure` で変える（格子は作り直さない）。判定への反映（set_current）は押した
その場で、保存は 0.3 秒まとめて 1 回（閉じるときは残りを先に書く）。アイコンは assets/icons のシート
（32px 格子）から OpenCV で切り出し、背景色と混ぜた PNG にする（Pillow は使わない）。絵が無い番号は名前の札
"""
import base64
import functools
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import cv2
import numpy as np

import config
import ContinueTable
import DebugLog
import MatchTNL
import UIFont

SLOTS_FILE = "assets/data/continue_slots.json"
ICONS_DIR = "assets/icons"
ICONS_JSON = f"{ICONS_DIR}/icons.json"
TERRORS_FILE = "assets/data/terrors.json"

# 寸法・色は ToN ListTool 2.17 の続行選択画面を 2026-10-22 のスクリーンショット（46.png・47.png）から測った値
COLUMNS = 12                 # Classic などの格子の 1 行の個数
ALTERNATE_ROWS = (11, 9, 7, 5, 3, 1)   # Alternate は逆三角
CELL_SIZE = 40               # 1 マス（1px の枠を含む 40×40）
ICON_SIZE = 32               # アイコン（32×32 のまま。拡大しない）
ICON_INSET = 4               # マスの左上からアイコンまで
X_PITCH = 46                 # 横の間隔（マス 40px＋6px。GK で詰めた）
Y_PITCH = 46                 # 縦の間隔（逆三角の段の間も同じ）。ブロックの間もこの 1 行ぶんあける
MARGIN = 14                  # 左右の余白（マスの左端まで）
TOP_PAD = 14                 # 最初のブロックの上の余白
CANVAS_WIDTH = 2 * MARGIN + (COLUMNS - 1) * X_PITCH + CELL_SIZE    # 574px
CORNER = 2                   # 枠の角を丸める（斜めに落とす）大きさ
SAVE_DELAY_MS = 300          # 押してから保存するまで（まとめて 1 回）

# 不透明度（アイコンの絵を背景と混ぜる割合）: 選んでいない／もう片方の表に入っている／今の表に入っている／検索に合わない
LEVELS = {"dim": 0.3, "other": 0.6, "full": 1.0, "faint": 0.15}
THIN, THICK = 1, 2           # 枠の太さ

COLOR_GRID_BG = "#2d2d2d"    # 格子の背景
BG_RGB = (0x2D, 0x2D, 0x2D)
COLOR_IDLE = "#a0a0a0"       # 選んでいないものの枠
COLOR_FAINT = "#3c3c3c"      # 検索に合わないものの枠
COLOR_MATCH = config.GUI_ACC  # 検索に合うものの枠
COLOR_DISABLED = "#e64040"   # 続行無効の枠
COLOR_DEFAULT = "#50d250"    # デフォ続行の枠

WHICH_TEXT = {ContinueTable.DISABLED: "続行無効", ContinueTable.DEFAULT: "デフォ続行"}
STRONG = {ContinueTable.DISABLED: COLOR_DISABLED, ContinueTable.DEFAULT: COLOR_DEFAULT}
OTHER = {ContinueTable.DISABLED: ContinueTable.DEFAULT, ContinueTable.DEFAULT: ContinueTable.DISABLED}

LAYOUT_JSON = f"{ICONS_DIR}/layout.json"

# 範囲ごとのブロック（Classic 0〜133・Alternate 134〜169・Unbound 200〜283、残りは特別のブロック）
CLASSIC_RANGE = range(0, 134)
ALTERNATE_RANGE = range(134, 170)
UNBOUND_RANGE = range(200, 284)

# バリアントの段（格子の下に左寄せ）: Bloodthirsty の 192・Fog/Midnight の Neo Pilot・Foxy・Unbound の
# Self Inserts (Bloodthirsty)。HHI（190）と Atrached（191）は Classic だけ
CLASSIC_VARIANT_IDS = (190, 191, 192)
VARIANT_IDS = (192, 315, 316, config.SELF_INSERTS_BLOODTHIRSTY_SLOT)
NO_VARIANT_ROUNDS = frozenset({"Moon", "Mystic Moon", "Blood Moon"})   # すべて特別のブロック（バリアントの段は作らない）
# ラウンドごとの名前の上書き（ラウンド名＝キーの「/」の前, 番号）
ROUND_NAMES = {("Unbound", 192): "Pack of Wild Yet Bloodthirsty Creatures"}

# 一覧の「Moon」: 4 つのラウンドのキーをまとめて 1 つに見せる（押す・全選択・全解除は 4 つすべてへ）
MOON_TAB = "Moon"
ROUND_KEYS_FOR_TAB = {MOON_TAB: [MatchTNL.LOG_TO_TNL[name]
                                 for name in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice")]}
MOON_SLOT_ROUNDS = ("Mystic Moon", "Blood Moon")      # Moon の中身（枠の和）になるラウンド

NOTE = "干し芋・焼き芋（グループのインスタンス）では使いません"


# ── データ（画面を作らずに読める部分）────────────────────
def round_display_name(key: str) -> str:
    """ラウンドのキー（「Classic/クラシック」）→ 一覧に出す名前（「/」の前）"""
    return key.split("/", 1)[0]


def table_keys(tab_key: str) -> list:
    """一覧の 1 つが受け持つ表のキー。Moon は 4 つ、ほかは自分だけ"""
    return list(ROUND_KEYS_FOR_TAB.get(tab_key, [tab_key]))


def _moon_tab(slots: list) -> tuple:
    """(MOON_TAB, 番号) : Mystic Moon と Blood Moon の枠の和（190・191 は Classic だけなので除く）"""
    ids = {i for key, ids in slots if round_display_name(key) in MOON_SLOT_ROUNDS for i in ids}
    return MOON_TAB, sorted(ids - {190, 191})


@functools.lru_cache(maxsize=1)
def load_rounds() -> list:
    """[(一覧のキー, [番号, ...]), ...]。assets/data/continue_slots.json の順。読めなければ空。
    Special/Moon の枠は出さない。Mystic Moon・Blood Moon は 1 つの「Moon」にまとめる（最初の位置）"""
    try:
        raw = json.loads(config.resource_path(SLOTS_FILE).read_text(encoding="utf-8"))
        slots = [(str(key), [int(i) for i in ids]) for key, ids in raw["rounds"]
                 if str(key) != MatchTNL.SPECIAL_MOON_KEY]
    except (OSError, ValueError, KeyError, TypeError):
        DebugLog.exception("ContinueTableGUI.load_rounds")
        return []
    rounds, moon_done = [], False
    for key, ids in slots:
        if round_display_name(key) not in MOON_SLOT_ROUNDS:
            rounds.append((key, ids))
        elif not moon_done:
            rounds.append(_moon_tab(slots))
            moon_done = True
    return rounds


@functools.lru_cache(maxsize=1)
def icon_names() -> dict:
    """{番号: 名前}（assets/icons/icons.json の names）。terrors.json に無い・直したい名前。読めなければ空"""
    try:
        raw = json.loads(config.resource_path(ICONS_JSON).read_text(encoding="utf-8"))
        return {int(tid): str(name) for tid, name in raw.get("names", {}).items()}
    except (OSError, ValueError, TypeError, AttributeError):
        DebugLog.exception("ContinueTableGUI.icon_names")
        return {}


@functools.lru_cache(maxsize=1)
def eight_pages_ids() -> frozenset:
    """8 Pages に出るテラーの続行リストの番号（terrors.json の 8pages の list_id）。読めなければ空"""
    try:
        raw = json.loads(config.resource_path(TERRORS_FILE).read_text(encoding="utf-8"))
        return frozenset(e["list_id"] for e in raw.get("8pages", ())
                         if isinstance(e.get("list_id"), int))
    except (OSError, ValueError, TypeError, AttributeError):
        DebugLog.exception("ContinueTableGUI.eight_pages_ids")
        return frozenset()


@functools.lru_cache(maxsize=1)
def terror_names() -> dict:
    """{続行リストの番号: 名前}。icons.json の names が先。次に terrors.json の classic・alternate・unbound は
    id、8pages は list_id（重なる番号は前者）"""
    try:
        raw = json.loads(config.resource_path(TERRORS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        DebugLog.exception("ContinueTableGUI.terror_names")
        return dict(icon_names())
    names: dict = {}
    for section, key in (("classic", "id"), ("alternate", "id"), ("unbound", "id"),
                         ("8pages", "list_id")):
        for entry in raw.get(section, ()):
            tid = entry.get(key)
            if isinstance(tid, int) and entry.get("name"):
                names.setdefault(tid, entry["name"])
    names.update(icon_names())
    return names


def terror_name(tid: int, round_key: str | None = None) -> str:
    """ツールチップ・検索に使う名前。round_key があれば、そのラウンドだけの名前（Unbound の 192）を先に見る"""
    if round_key is not None:
        special = ROUND_NAMES.get((round_display_name(round_key), tid))
        if special:
            return special
    return terror_names().get(tid) or f"#{tid}"


# ── 格子の並べ方 ─────────────────────────────
@functools.lru_cache(maxsize=1)
def listtool_order() -> dict:
    """{"classic": [番号, ...], "alternate": [番号, ...]}（assets/icons/layout.json。ListTool の並び順）。
    読めなければ空（その場合は番号順）"""
    try:
        raw = json.loads(config.resource_path(LAYOUT_JSON).read_text(encoding="utf-8"))
        return {key: [int(i) for i in raw[key]] for key in ("classic", "alternate")}
    except (OSError, ValueError, KeyError, TypeError):
        DebugLog.exception("ContinueTableGUI.listtool_order")
        return {"classic": [], "alternate": []}


def _in_order(ids, order) -> list:
    """ids を order の並びにする。order に無いものは番号順で後ろに付ける"""
    present, known = set(ids), set(order)
    return [i for i in order if i in present] + sorted(i for i in present if i not in known)


def grid12(ids) -> list:
    """[(番号, 列, 行), ...]。12 列で、行の数 = ceil(n/12)。空き（行の数×12 − n）は 1 行に 1 つ、上の行から
    左端に置く（134 個なら上の 10 行は列 1〜11・下の 2 行は列 0〜11）。空きが行の数より多いとき（個数が少ない）は、
    左から詰めて最後の行だけ短くする"""
    ids = list(ids)
    rows = -(-len(ids) // COLUMNS)
    empty = rows * COLUMNS - len(ids)
    one_per_row = empty <= rows
    cells, pos = [], 0
    for row in range(rows):
        gap = 1 if one_per_row and row < empty else 0
        for col, tid in enumerate(ids[pos:pos + COLUMNS - gap], start=gap):
            cells.append((tid, col, row))
        pos += COLUMNS - gap
    return cells


def pyramid(ids) -> list:
    """[(番号, 列, 行), ...]。11・9・7・5・3・1 の逆三角（真ん中寄せ。列は 0.5 刻みになる）"""
    ids = list(ids)
    cells, pos = [], 0
    for row, size in enumerate(ALTERNATE_ROWS):
        for k, tid in enumerate(ids[pos:pos + size]):
            cells.append((tid, (COLUMNS - size) / 2 + k, row))
        pos += size
    return cells


def _variant_ids(name: str, ids: list) -> list:
    """バリアントの段に行く番号。Classic は 190・191・192、ほかは 192 があれば 192（Fog/Midnight は 315・316 も、
    Unbound は Self Inserts (Bloodthirsty) の仮の番号も）。HHI・Atrached は Classic だけ。Moon は作らない"""
    if name in NO_VARIANT_ROUNDS:
        return []
    if name == "Classic" and 192 in ids:
        return list(CLASSIC_VARIANT_IDS)
    return sorted(i for i in VARIANT_IDS if i in ids)


def layout_for(round_key: str, ids) -> list:
    """上から順のブロック [(種類, [(番号, 列, 行), ...], 行の間隔), ...]。種類は classic・alternate・unbound・
    special・variant（空のブロックは出さない）。ブロックの間は 1 行ぶんあける（描く側）。

    - 8 Pages は 8 Pages に出るテラー（eight_pages_ids）だけ
    - classic: 0〜133 を ListTool の順に 12 列（grid12）。alternate: 134〜169 を ListTool の順に逆三角
      （36 個そろわなければ 12 列）。unbound: 200〜283 を番号順に 12 列。special: 残り（170〜177・193・194・
      196〜199・312〜317 など。Mystic/Blood Moon は全部）を番号順に 12 列
    - variant: Classic は 190・191・192、ほかは 192（Fog/Midnight は 315・316 も、Unbound は 1283 も）。
      左寄せの 1 行"""
    name = round_display_name(round_key)
    ids = list(ids)
    if name == "8 Pages":
        allowed = eight_pages_ids()
        ids = [i for i in ids if i in allowed]
    variant = _variant_ids(name, ids)
    ids = [i for i in ids if i not in variant]
    classic = [i for i in ids if i in CLASSIC_RANGE]
    alternate = [i for i in ids if i in ALTERNATE_RANGE]
    unbound = [i for i in ids if i in UNBOUND_RANGE]
    used = set(classic) | set(alternate) | set(unbound)
    special = sorted(i for i in ids if i not in used)
    order = listtool_order()
    alternate = _in_order(alternate, order["alternate"])
    blocks = [("classic", grid12(_in_order(classic, order["classic"])), Y_PITCH)]
    if len(alternate) == sum(ALTERNATE_ROWS):
        blocks.append(("alternate", pyramid(alternate), Y_PITCH))
    else:
        blocks.append(("alternate", grid12(alternate), Y_PITCH))
    blocks += [("unbound", grid12(sorted(unbound)), Y_PITCH), ("special", grid12(special), Y_PITCH),
               ("variant", [(tid, col, 0) for col, tid in enumerate(variant)], Y_PITCH)]
    return [block for block in blocks if block[1]]


def block_height(cells: list, pitch: int) -> int:
    """ブロックの高さ（最初の行の上から、最後の行のマスの下まで。最後の行は Y_PITCH ぶんで数える）"""
    rows = max(row for _tid, _col, row in cells) + 1
    return (rows - 1) * pitch + Y_PITCH


@functools.lru_cache(maxsize=1)
def icon_index() -> dict:
    """{番号: (シートの名前, 列, 段)}（assets/icons/icons.json）。読めなければ空"""
    try:
        raw = json.loads(config.resource_path(ICONS_JSON).read_text(encoding="utf-8"))
        return {int(tid): (str(s), int(c), int(r)) for tid, (s, c, r) in raw["icons"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        DebugLog.exception("ContinueTableGUI.icon_index")
        return {}


def icon_cell_size() -> int:
    try:
        return int(json.loads(config.resource_path(ICONS_JSON).read_text(encoding="utf-8"))["cell"])
    except (OSError, ValueError, KeyError, TypeError):
        return 32


@functools.lru_cache(maxsize=4)
def _sheet(name: str):
    """シートの画像（BGR または BGRA）。読めなければ None"""
    try:
        sheets = json.loads(config.resource_path(ICONS_JSON).read_text(encoding="utf-8"))["sheets"]
        data = np.fromfile(str(config.resource_path(f"{ICONS_DIR}/{sheets[name]}")), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    except (OSError, ValueError, KeyError, TypeError, cv2.error):
        DebugLog.exception("ContinueTableGUI._sheet")
        return None


def blend_over_bg(img: np.ndarray, alpha: float) -> np.ndarray:
    """BGR または BGRA の画像を、不透明度 alpha で背景色（BG_RGB）に重ねた BGR（uint8）。
    元の透明度（A）もかける（icon×a + bg×(1−a)）"""
    src = img.astype(np.float32)
    a = src[..., 3:4] / 255.0 * alpha if src.shape[2] == 4 else alpha
    bg = np.array(BG_RGB[::-1], dtype=np.float32)
    return np.clip(src[..., :3] * a + bg * (1 - a), 0, 255).round().astype(np.uint8)


def icon_png(tid: int, alpha: float = 1.0) -> bytes | None:
    """番号のアイコンを切り出し、背景色に不透明度 alpha で重ねた 32×32 の PNG のバイト列。
    絵が無い・切り出せないなら None"""
    spot = icon_index().get(tid)
    if spot is None:
        return None
    sheet = _sheet(spot[0])
    if sheet is None:
        return None
    cell = icon_cell_size()
    _name, col, row = spot
    crop = sheet[row * cell:(row + 1) * cell, col * cell:(col + 1) * cell]
    if crop.shape[0] != cell or crop.shape[1] != cell:
        return None
    if cell != ICON_SIZE:
        crop = cv2.resize(crop, (ICON_SIZE, ICON_SIZE), interpolation=cv2.INTER_NEAREST)
    ok, png = cv2.imencode(".png", blend_over_bg(crop, alpha))
    return png.tobytes() if ok else None


def tile_color(tid: int) -> str:
    """絵が無い番号の札の色（番号ごとに決まった、暗めの色）"""
    r, g, b = (60 + (tid * 53) % 100, 60 + (tid * 97) % 100, 60 + (tid * 29) % 100)
    return f"#{r:02x}{g:02x}{b:02x}"


def blend_color(color: str, alpha: float) -> str:
    """"#rrggbb" を不透明度 alpha で背景色に重ねた色"""
    rgb = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(v * alpha + bg * (1 - alpha)) for v, bg in zip(rgb, BG_RGB))


def frame_points(x: int, y: int, width: int) -> list:
    """マス（左上 x,y・40×40）の枠の多角形の頂点。角を CORNER だけ斜めに落として少し丸め、太さ width の線が
    マスの内側に収まるように寄せる"""
    inset = (width - 1) / 2
    left, top = x + inset, y + inset
    right, bottom = x + CELL_SIZE - 1 - inset, y + CELL_SIZE - 1 - inset
    c = CORNER
    return [left + c, top, right - c, top, right, top + c, right, bottom - c,
            right - c, bottom, left + c, bottom, left, bottom - c, left, top + c]


# ── 画面 ─────────────────────────────────
class _Cell:
    """格子の 1 マス。キャンバスの item の id を持つ（押したらこの item だけ変える）"""
    __slots__ = ("tid", "face", "label", "frame")

    def __init__(self, tid: int, face: int, label: int | None, frame: int):
        self.tid = tid
        self.face = face          # アイコンの絵（無ければ名前の札の四角）
        self.label = label        # 名前の札の文字（絵があれば None）
        self.frame = frame        # 枠の四角


class ContinueTableWindow(tk.Toplevel):
    def __init__(self, master, on_change=None):
        super().__init__(master)
        self.title("続行表")
        self.configure(bg=config.GUI_BG)
        self.geometry(f"{CANVAS_WIDTH + 250}x720")
        self.minsize(CANVAS_WIDTH + 230, 420)
        self._on_change = on_change
        self._rounds = load_rounds()
        self._photos: dict = {}                # (番号, 不透明度の種類) → PhotoImage（持っていないと消える）
        self._cells: dict = {}                 # 番号 → _Cell（今の格子）
        self._x0 = 0                           # 格子（幅 CANVAS_WIDTH）を真ん中に寄せる分のずれ
        self._content_height = 0
        self._tip: tuple = ()                  # ツールチップの item の id
        self._save_job = None                  # 保存の予約（after の id）
        self._save_pending = False
        self.v_which = tk.StringVar(value=ContinueTable.DISABLED)
        # 編集しているプリセットの名前（表の種類ごと）。窓ごとの選びとは別（編集の対象を選ぶだけ）
        store = ContinueTable.current()
        self._selected = {which: ContinueTable.fallback_name(store, which)
                          for which in (ContinueTable.DISABLED, ContinueTable.DEFAULT)}
        self.v_preset = tk.StringVar()
        self.v_search = tk.StringVar()
        self._index = 0                        # 左の一覧で選んでいるラウンド
        self._build()
        self.v_which.trace_add("write", lambda *_: self._show_presets())
        self._show_presets()
        if self._rounds:
            self.list_rounds.selection_set(0)
        self._show_round()
        self.v_search.trace_add("write", lambda *_: self._restyle_all())

    # ── 組み立て ──
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="編集する表:").pack(side="left")
        for which in (ContinueTable.DISABLED, ContinueTable.DEFAULT):
            ttk.Radiobutton(top, text=WHICH_TEXT[which], variable=self.v_which, value=which,
                            command=self._restyle_all).pack(side="left", padx=(8, 0))
        ttk.Label(top, text="プリセット:").pack(side="left", padx=(20, 0))
        self.cb_preset = ttk.Combobox(top, textvariable=self.v_preset, state="readonly", width=14)
        self.cb_preset.pack(side="left", padx=(6, 0))
        self.cb_preset.bind("<<ComboboxSelected>>", lambda _e: self._on_preset_picked())
        for text, command in (("新規", self.new_preset), ("複製", self.copy_preset),
                              ("名前変更", self.rename_preset), ("削除", self.delete_preset)):
            ttk.Button(top, text=text, width=len(text) * 2 + 1, command=command).pack(side="left", padx=(6, 0))

        tools = ttk.Frame(self)
        tools.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Label(tools, text="名前で検索:").pack(side="left")
        ttk.Entry(tools, textvariable=self.v_search, width=18).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="全選択", command=self.select_all).pack(side="left", padx=(20, 0))
        ttk.Button(tools, text="全解除", command=self.clear_all).pack(side="left", padx=(6, 0))
        ttk.Label(self, text=NOTE, foreground=config.GUI_YLW).pack(anchor="w", padx=10)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self._build_round_list(body)
        self._build_canvas(body)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text=".tnl をデフォ続行へ読み込む",
                   command=self._import_tnl).pack(side="left")
        ttk.Button(bottom, text=".tnnl を続行無効へ読み込む",
                   command=self._import_tnnl).pack(side="left", padx=(6, 0))

    def _build_round_list(self, parent):
        """左のラウンドの一覧（名前が入る最小の幅・縦に並べる）。暗い色で、選んでいるラウンドを強調する"""
        self.list_rounds = tk.Listbox(
            parent, width=0, exportselection=False, activestyle="none", selectmode="browse",
            bg=COLOR_GRID_BG, fg="#e0e0e0", selectbackground=config.GUI_ACC, selectforeground="#111111",
            highlightthickness=0, borderwidth=0, font=(UIFont.UI, 10))
        for key, _ids in self._rounds:
            self.list_rounds.insert("end", round_display_name(key))
        self.list_rounds.pack(side="left", fill="y", padx=(10, 0), pady=6)
        self.list_rounds.bind("<<ListboxSelect>>", lambda _e: self._on_round_select())

    def _build_canvas(self, parent):
        """格子を描くキャンバス（縦にスクロール）。押す・乗せるは全マス共通の 1 組の束縛"""
        box = ttk.Frame(parent)
        box.pack(side="left", fill="both", expand=True, padx=10, pady=6)
        self.canvas = tk.Canvas(box, bg=COLOR_GRID_BG, highlightthickness=0)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.tag_bind("cell", "<Button-1>", lambda _e: self._on_click())
        self.canvas.tag_bind("cell", "<Enter>", lambda _e: self._on_enter())
        self.canvas.tag_bind("cell", "<Leave>", lambda _e: self._hide_tip())
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def destroy(self):
        self._flush_save()                             # 予約が残っていれば、閉じる前に書く
        try:
            self.canvas.unbind_all("<MouseWheel>")     # 窓の上で閉じても、ほかの窓へ効きを残さない
        except tk.TclError:
            pass
        super().destroy()

    def _wheel(self, event):
        self._hide_tip()
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_canvas_configure(self, event):
        """幅が変わったら、格子を真ん中に寄せ直す（動かすだけ。描き直さない）"""
        self._recenter(event.width)
        self._set_scrollregion(event.width, event.height)

    def _recenter(self, width: int):
        x0 = max(0, (width - CANVAS_WIDTH) // 2)
        if x0 != self._x0:
            self.canvas.move("cell", x0 - self._x0, 0)
            self._x0 = x0
            self._hide_tip()

    def _set_scrollregion(self, width: int, height: int):
        self.canvas.configure(scrollregion=(0, 0, width, max(self._content_height, height)))

    # ── 今の状態 ──
    def _round_key(self) -> str | None:
        """一覧で選んでいるラウンドのキー（Moon は MOON_TAB）"""
        return self._rounds[self._index][0] if 0 <= self._index < len(self._rounds) else None

    def _round_ids(self) -> list:
        return self._rounds[self._index][1] if 0 <= self._index < len(self._rounds) else []

    def _table_keys(self) -> list:
        """今のラウンドの表のキー（Moon は 4 つ）"""
        key = self._round_key()
        return table_keys(key) if key is not None else []

    def _edit_table(self) -> ContinueTable.Table:
        """編集しているプリセット 2 つ（続行無効・デフォ続行）の中身"""
        store = ContinueTable.current()
        return ContinueTable.Table(
            disabled=ContinueTable.presets_of(store, ContinueTable.DISABLED).get(
                self._selected[ContinueTable.DISABLED], {}),
            default=ContinueTable.presets_of(store, ContinueTable.DEFAULT).get(
                self._selected[ContinueTable.DEFAULT], {}))

    def _has(self, table: ContinueTable.Table, which: str, tid: int) -> bool:
        """which の表に、今のラウンドの表のキーのどれかで tid が入っているか"""
        kind = getattr(table, which)
        return any(tid in kind.get(key, ()) for key in self._table_keys())

    def _on_round_select(self):
        """一覧で選んだら、そのラウンドの格子へ（同じラウンドを押し直したときは何もしない）"""
        picked = self.list_rounds.curselection()
        if picked and picked[0] != self._index:
            self._index = picked[0]
            self._show_round()

    def select_round(self, index: int):
        """一覧の index 番目を選ぶ（押したときと同じ）"""
        self.list_rounds.selection_clear(0, "end")
        self.list_rounds.selection_set(index)
        self._on_round_select()

    def _which(self) -> str:
        return self.v_which.get()

    def _searching(self) -> bool:
        return bool(self.v_search.get().strip())

    def _matches(self, tid: int) -> bool:
        needle = self.v_search.get().strip().lower()
        return not needle or needle in terror_name(tid, self._round_key()).lower()

    # ── 格子（ラウンドを変えたときだけ描き直す）──
    def _show_round(self):
        """今のラウンドの格子を描き直す"""
        self._hide_tip()
        self.canvas.delete("cell")
        self._cells = {}
        self._x0 = max(0, (self.canvas.winfo_width() - CANVAS_WIDTH) // 2)
        y = TOP_PAD
        for n, (_kind, cells, pitch) in enumerate(layout_for(self._round_key() or "", self._round_ids())):
            if n:
                y += Y_PITCH                       # ブロックの間は 1 行ぶんあける
            for tid, col, row in cells:
                x = self._x0 + MARGIN + int(col * X_PITCH + 0.5)      # 逆三角の半列ぶんは四捨五入
                self._cells[tid] = self._draw_cell(tid, x, y + row * pitch)
            y += block_height(cells, pitch)
        self._content_height = y + TOP_PAD
        self._set_scrollregion(self.canvas.winfo_width(), self.canvas.winfo_height())
        self._restyle_all()
        self.canvas.yview_moveto(0)

    def _draw_cell(self, tid: int, x: int, y: int) -> _Cell:
        """1 マス（左上 x,y・40×40）: 枠（背景色で塗る＝マス全体が押せる）の上にアイコン（無ければ名前の札）。
        絵の不透明度と枠の色・太さは _restyle で決める"""
        tags = ("cell", f"t{tid}")
        frame = self.canvas.create_polygon(frame_points(x, y, THIN), fill=COLOR_GRID_BG, tags=tags)
        ix, iy = x + ICON_INSET, y + ICON_INSET
        label = None
        if self._photo(tid, "full") is not None:
            face = self.canvas.create_image(ix, iy, anchor="nw", tags=tags)
        else:
            # 絵が無い番号は名前の札（色付きの四角に名前）
            face = self.canvas.create_rectangle(ix, iy, ix + ICON_SIZE - 1, iy + ICON_SIZE - 1,
                                                width=0, tags=tags)
            label = self.canvas.create_text(ix + ICON_SIZE // 2, iy + ICON_SIZE // 2,
                                            text=terror_name(tid, self._round_key()),
                                            width=ICON_SIZE - 2, justify="center",
                                            font=(UIFont.UI, 6), tags=tags)
        return _Cell(tid, face, label, frame)

    def _origin(self, cell: _Cell) -> tuple[int, int]:
        """マスの左上（キャンバス上。窓の幅で寄せ直したあとの今の位置）"""
        fx, fy = self.canvas.coords(cell.face)[:2]
        return round(fx) - ICON_INSET, round(fy) - ICON_INSET

    def _photo(self, tid: int, level: str):
        """番号のアイコンの、不透明度の種類（LEVELS: dim・other・full・faint）ごとの PhotoImage（作ったら持っておく）。
        絵が無ければ None"""
        if (tid, level) not in self._photos:
            png = icon_png(tid, LEVELS[level])
            self._photos[(tid, level)] = (tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
                                          if png else None)
        return self._photos[(tid, level)]

    # ── 見た目（セルごとに itemconfigure）──
    def _style_of(self, tid: int) -> tuple[str, str, int]:
        """(不透明度の種類, 枠の色, 枠の太さ)。今の表に入っている→100%＋太い枠（赤／緑）、もう片方の表→60%＋
        その色の細い枠、どちらも無い→30%＋灰色の細い枠。検索の欄に字があれば、合わないものは 15%＋薄い枠
        （選んでいる・いないにかかわらず）、合うもので選んでいないものは 100% にして枠を目立たせる"""
        if self._searching() and not self._matches(tid):
            return "faint", COLOR_FAINT, THIN
        table, which = self._edit_table(), self._which()
        if self._has(table, which, tid):
            return "full", STRONG[which], THICK
        if self._has(table, OTHER[which], tid):
            return "other", STRONG[OTHER[which]], THIN
        if self._searching():
            return "full", COLOR_MATCH, THICK
        return "dim", COLOR_IDLE, THIN

    def _restyle(self, tid: int):
        """このセルの絵と枠だけ変える"""
        cell = self._cells.get(tid)
        if cell is None:
            return
        level, color, width = self._style_of(tid)
        if cell.label is None:
            self.canvas.itemconfigure(cell.face, image=self._photo(tid, level))
        else:
            self.canvas.itemconfigure(cell.face, fill=blend_color(tile_color(tid), LEVELS[level]))
            self.canvas.itemconfigure(cell.label, fill=blend_color("#ffffff", LEVELS[level]))
        self.canvas.coords(cell.frame, *frame_points(*self._origin(cell), width))
        self.canvas.itemconfigure(cell.frame, outline=color, width=width)

    def _restyle_all(self):
        for tid in self._cells:
            self._restyle(tid)

    # ── 押す・乗せる ──
    def _tid_of(self, item) -> int | None:
        """item の番号（tag "t<番号>"）。セルの item でなければ None"""
        for tag in self.canvas.gettags(item):
            if tag.startswith("t") and tag[1:].isdigit():
                return int(tag[1:])
        return None

    def _current_tid(self) -> int | None:
        """マウスの下のセルの番号"""
        for item in self.canvas.find_withtag("current"):
            tid = self._tid_of(item)
            if tid is not None:
                return tid
        return None

    def _on_click(self):
        tid = self._current_tid()
        if tid is not None:
            self.toggle(tid)

    def _on_enter(self):
        tid = self._current_tid()
        if tid is not None:
            self._show_tip(tid)

    def _show_tip(self, tid: int):
        """セルの上（上に余白が無ければ下）に名前を出す"""
        self._hide_tip()
        cell = self._cells.get(tid)
        if cell is None:
            return
        x0, y0, x1, y1 = self.canvas.bbox(cell.frame)
        text = self.canvas.create_text(0, 0, text=terror_name(tid, self._round_key()), anchor="center",
                                       fill="#ffffff", font=(UIFont.UI, 9), tags="tip")
        left, top, right, bottom = self.canvas.bbox(text)
        width, height = right - left + 10, bottom - top + 6
        above = y0 - height - 4 >= self.canvas.canvasy(0)
        cy = y0 - height / 2 - 4 if above else y1 + height / 2 + 4
        edge = width / 2 + 2                                  # 左右ははみ出さない
        cx = min(max((x0 + x1) / 2, edge), max(edge, self.canvas.winfo_width() - edge))
        self.canvas.coords(text, cx, cy)
        back = self.canvas.create_rectangle(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2,
                                            fill="#111111", outline=COLOR_IDLE, tags="tip")
        self.canvas.tag_raise(text, back)
        self._tip = (text, back)

    def _hide_tip(self):
        if self._tip:
            self.canvas.delete("tip")
            self._tip = ()

    # ── 変える（判定へはすぐ、保存は 0.3 秒まとめて 1 回）──
    def _commit(self, table: ContinueTable.Table, tids=None):
        """編集している 2 つのプリセットを table の中身にして、判定に効かせ（set_current）・保存を予約し・
        変わったセル（None なら全部）の見た目だけ直す"""
        self._commit_store(ContinueTable.with_edited_table(ContinueTable.current(), self._selected, table), tids)

    def _commit_store(self, store: ContinueTable.Store, tids=None):
        ContinueTable.set_current(store)
        self._schedule_save()
        if tids is None:
            self._restyle_all()
        else:
            for tid in tids:
                self._restyle(tid)
        if self._on_change is not None:
            self._on_change()

    def _schedule_save(self):
        self._save_pending = True
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(SAVE_DELAY_MS, self._flush_save)

    def _flush_save(self):
        """予約を取り消し、保存が残っていれば今の表を書く（閉じるときも呼ぶ）"""
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except tk.TclError:
                pass
            self._save_job = None
        if self._save_pending:
            self._save_pending = False
            ContinueTable.save(ContinueTable.current())

    def _with_members(self, tids, add: bool) -> ContinueTable.Table:
        """今の表の、今のラウンドの表のキーすべて（Moon は 4 つ）へ tids を入れた／外した新しい Table"""
        table, which = self._edit_table(), self._which()
        for key in self._table_keys():
            ids = set(getattr(table, which).get(key, ()))
            ids = ids | set(tids) if add else ids - set(tids)
            table = ContinueTable.with_ids(table, which, key, ids)
        return table

    def toggle(self, tid: int):
        """押したマスを入れる／外す。Moon は 4 つのキーのどれかに入っていれば「入っている」＝外す"""
        if self._round_key() is None:
            return
        present = self._has(self._edit_table(), self._which(), tid)
        self._commit(self._with_members([tid], add=not present), (tid,))

    def _visible_tids(self) -> list:
        """今のラウンドで見えているマスの番号（バリアントの段も含む）。検索で絞っているときは合うものだけ"""
        return [tid for tid in self._cells if self._matches(tid)]

    def select_all(self):
        """見えているマスを全部、今の表に入れる"""
        if self._round_key() is not None:
            self._commit(self._with_members(self._visible_tids(), add=True))

    def clear_all(self):
        """見えているマスを全部、今の表から外す"""
        if self._round_key() is not None:
            self._commit(self._with_members(self._visible_tids(), add=False))

    # ── プリセット（GL）──
    def _selected_name(self) -> str:
        """今の表の種類で編集しているプリセットの名前"""
        return self._selected[self._which()]

    def _show_presets(self):
        """プリセットの選択肢と、選んでいる名前を今の表の種類のものに合わせる（無い名前を選んでいたら直す）"""
        store, which = ContinueTable.current(), self._which()
        names = ContinueTable.preset_names(store, which)
        if self._selected[which] not in names:
            self._selected[which] = ContinueTable.fallback_name(store, which)
        self.cb_preset["values"] = names
        self.v_preset.set(self._selected[which])

    def _on_preset_picked(self):
        """ドロップダウンで選んだ: そのプリセットの格子へ（保存するものは無い）"""
        self._selected[self._which()] = self.v_preset.get()
        self._restyle_all()

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        """プリセットの名前を聞く（取り消しは None）"""
        return simpledialog.askstring("続行表", title, initialvalue=initial, parent=self)

    def _apply_presets(self, store: ContinueTable.Store, select: str | None = None):
        """プリセットを変えた Store を効かせる: 判定へ・保存の予約・選択肢の作り直し・格子の見た目"""
        if select is not None:
            self._selected[self._which()] = select
        ContinueTable.set_current(store)
        self._show_presets()                          # 消えた名前を選んだままにしない（見た目より先に）
        self._commit_store(store)

    def _ask_valid_name(self, title: str, initial: str = "", ignore: str | None = None) -> str | None:
        """名前を聞いて、使えるものだけ返す（取り消し・使えない名前は None。使えない理由は出す）"""
        name = self._ask_name(title, initial)
        if name is None:
            return None
        name = name.strip()
        error = ContinueTable.name_error(ContinueTable.current(), self._which(), name, ignore=ignore)
        if error:
            self._error(error)
            return None
        return name

    def new_preset(self):
        """名前を聞いて、空のプリセットを作って選ぶ"""
        name = self._ask_valid_name(f"新しい{WHICH_TEXT[self._which()]}のプリセットの名前")
        if name is not None:
            self._apply_presets(
                ContinueTable.with_new_preset(ContinueTable.current(), self._which(), name), select=name)

    def copy_preset(self):
        """名前を聞いて、選んでいるプリセットの写しを作って選ぶ（名前の既定は「〇〇 のコピー」）"""
        which, source = self._which(), self._selected_name()
        initial = ContinueTable.unique_name(ContinueTable.current(), which, f"{source} のコピー")
        name = self._ask_valid_name("複製の名前", initial)
        if name is not None:
            self._apply_presets(
                ContinueTable.with_copied_preset(ContinueTable.current(), which, source, name), select=name)

    def rename_preset(self):
        """選んでいるプリセットの名前を変える（同じ名前は不可。これを使っていた窓はそのまま新しい名前を使う）"""
        which, old = self._which(), self._selected_name()
        name = self._ask_valid_name("新しい名前", old, ignore=old)
        if name is not None and name != old:
            self._apply_presets(
                ContinueTable.with_renamed_preset(ContinueTable.current(), which, old, name), select=name)

    def delete_preset(self):
        """選んでいるプリセットを、確認してから消す（最後の 1 つは消せない。使っていた窓は「既定」へ）"""
        which, name = self._which(), self._selected_name()
        store = ContinueTable.current()
        if not ContinueTable.can_delete(store, which):
            self._error("最後の 1 つのプリセットは削除できません。")
            return
        if not self._confirm(f"{WHICH_TEXT[which]}のプリセット「{name}」を削除します。よいですか？\n"
                             f"これを使っていた窓は「{ContinueTable.DEFAULT_PRESET}」になります。"):
            return
        self._apply_presets(ContinueTable.with_deleted_preset(store, which, name))

    # ── 取り込み ──
    def _ask_file(self, title: str, pattern: str) -> str:
        return filedialog.askopenfilename(parent=self, title=title,
                                          filetypes=[(pattern, pattern), ("All", "*.*")])

    def _confirm(self, text: str) -> bool:
        return messagebox.askyesno("続行表", text, parent=self)

    def _error(self, text: str):
        messagebox.showerror("続行表", text, parent=self)

    def _import_tnl(self):
        self._import(ContinueTable.DEFAULT, ".tnl", "*.tnl", ContinueTable.from_tnl)

    def _import_tnnl(self):
        self._import(ContinueTable.DISABLED, ".tnnl", "*.tnnl", ContinueTable.from_tnnl)

    def _import(self, which: str, ext: str, pattern: str, reader):
        """ファイルを選ぶ → 「今の表を置き換えます」の確認 → 読み込んで置き換える"""
        path = self._ask_file(f"{ext} を選択", pattern)
        if not path:
            return
        if not self._confirm(f"{WHICH_TEXT[which]}のプリセット「{self._selected[which]}」の今の中身を、"
                             f"{ext} の中身に置き換えます。よいですか？"):
            return
        try:
            new = reader(path)
        except Exception:
            DebugLog.exception("ContinueTableGUI._import")
            self._error(f"{ext} を読めませんでした。ファイルを確かめてください。")
            return
        self._commit(ContinueTable.with_all(self._edit_table(), which, new))
