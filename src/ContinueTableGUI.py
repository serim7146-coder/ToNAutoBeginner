"""続行表の編集画面（GJ・GK）。ToN ListTool の続行選択画面と同じ並び・色で、テラーのアイコンを押して選ぶ。

- 左: ラウンドの一覧（細い Listbox。Mystic Moon・Blood Moon・Twilight・Solstice・Special・Cold Night は 1 つの「Special/Moon」）
- 上: どれを編集するか（続行無効／デフォ続行／続行リスト）と、続行無効・デフォ続行のプリセット（選ぶ・新規・
  複製・名前変更・削除。GL。続行リストはプリセットが無いので欄を隠す）・名前の検索・全選択／全解除。押す・
  全選択・全解除・読み込みは、選んでいるプリセット（続行リストはツールの続行リストそのもの。GN）に効く
- 中: テラーのアイコンの格子（tk.Canvas に直接描く。間隔は縦横とも 46px）。上から Classic（0〜133・ListTool の
  並び）・Alternate（1000〜1035・11/9/7/5/3/1 の逆三角）・Unbound（2000〜2083）・特別（8 Pages 限定のテラー・
  Special/Moon の中身）・バリアントの段（4000 番台。HHI・Atrached・Bloodthirsty・Gigabytes・Neo Pilot・Evil Purple Foxy・
  Glorbo・Self Inserts (Bloodthirsty)。そのラウンドの枠にあるものだけ）。ブロックの間は 1 行あける。名前は出さない
  （マウスを乗せるとツールチップ）。選んでいないものは背景に 30% で重ね、今の表に入っているものは 100%＋2px の枠
  （赤＝続行無効・緑＝デフォ続行）、もう片方の表に入っているものは 60%＋その色の 1px の枠。
  そのラウンドに出ないテラー（そのラウンドの枠に無いもの。Ghost の BFF や 8 Pages に出ないものなど）のマスは、格子の
  形はそのままに「選べないマス」: 12% の暗い絵・枠なし・押しても何も起きない・全選択／全解除の対象外・ツールチップは
  「{名前}（このラウンドには出ません）」。8 Pages のタブは、その行（3000 番台）を ListTool の位置に選べるマスとして置く
- 下: .tnl をデフォ続行へ・.tnnl を続行無効へ読み込む。その下に「アイテムで切り替え」の欄（GM）: 決まり 1 つ
  = 1 行（アイテム・続行無効・デフォ続行のプリセット・↑↓×）。同じアイテムが上にある行は薄い文字

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
import ContinueList
import ContinueTable
import DebugLog
import MatchTNL
import TerrorIds
import UIFont

ICONS_DIR = "assets/icons"
ICONS_JSON = f"{ICONS_DIR}/icons.json"

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

# 不透明度（アイコンの絵を背景と混ぜる割合）: 選んでいない／もう片方の表に入っている／今の表に入っている／検索に合わない／
# そのラウンドに出ない（選べないマス）
LEVELS = {"dim": 0.3, "other": 0.6, "full": 1.0, "faint": 0.15, "gone": 0.12}
THIN, THICK = 1, 2           # 枠の太さ

COLOR_GRID_BG = "#2d2d2d"    # 格子の背景
BG_RGB = (0x2D, 0x2D, 0x2D)
COLOR_IDLE = "#a0a0a0"       # 選んでいないものの枠
COLOR_FAINT = "#3c3c3c"      # 検索に合わないものの枠
COLOR_MATCH = config.GUI_ACC  # 検索に合うものの枠
COLOR_DISABLED = "#e64040"   # 続行無効の枠
COLOR_DEFAULT = "#50d250"    # デフォ続行の枠
COLOR_LIST = "#4FA3FF"       # 続行リストの枠（GN）

# 編集する表の種類。続行無効・デフォ続行は ContinueTable の表、LIST はツールの続行リスト（ContinueList。GN）
LIST = "list"
WHICH_TEXT = {ContinueTable.DISABLED: "続行無効", ContinueTable.DEFAULT: "デフォ続行", LIST: "続行リスト"}
STRONG = {ContinueTable.DISABLED: COLOR_DISABLED, ContinueTable.DEFAULT: COLOR_DEFAULT, LIST: COLOR_LIST}
# 選んでいる表のほかに薄く見せる表（先に入っているほうの色。続行リストを選んでいるときは、重ねる 2 つの表）
OTHERS = {ContinueTable.DISABLED: (ContinueTable.DEFAULT,), ContinueTable.DEFAULT: (ContinueTable.DISABLED,),
          LIST: (ContinueTable.DEFAULT, ContinueTable.DISABLED)}

LAYOUT_JSON = f"{ICONS_DIR}/layout.json"

# 範囲ごとのブロック（Classic 0〜999・Alternate 1000〜1999・Unbound 2000〜2999・8 Pages 3000〜3999、
# 4000 番台は Special/Moon のタブでは特別のブロック、ほかのタブではバリアントの段）
CLASSIC_RANGE = range(0, TerrorIds.ALTERNATE_BASE)
ALTERNATE_RANGE = range(TerrorIds.ALTERNATE_BASE, TerrorIds.UNBOUND_BASE)
UNBOUND_RANGE = range(TerrorIds.UNBOUND_BASE, TerrorIds.EIGHT_PAGES_BASE)
EIGHT_PAGES_RANGE = range(TerrorIds.EIGHT_PAGES_BASE, TerrorIds.FIXED_BASE)

# ラウンドごとの名前の上書き（ラウンド名＝キーの「/」の前, 番号）
ROUND_NAMES = {("Unbound", TerrorIds.BLOODTHIRSTY_CREATURE): "Pack of Wild Yet Bloodthirsty Creatures"}
SPECIAL_MOON_TAB = "Special/Moon"       # 一覧に出す名前（キーの「/」の前ではなく全部）
GONE_TIP = "（このラウンドには出ません）"

NOTE = "干し芋・焼き芋（グループのインスタンス）では使いません"

# 「アイテムで切り替え」の欄（GM）
RULES_TITLE = "アイテムで切り替え"
RULES_NOTE = "窓が持っているアイテムで、上から最初に当てはまる決まりのプリセットを使います（薄い行は、上に同じアイテムがあるので使われません）"
RULES_AREA_HEIGHT = 112      # 決まりの行を並べる欄の高さ（行が増えたら縦にスクロール）
COLOR_RULE_DIM = "#7f849c"   # 使われない決まりの文字
STYLE_RULE_DIM = "Dim.TCombobox"


# ── データ（画面を作らずに読める部分）────────────────────
def round_display_name(key: str) -> str:
    """ラウンドのキー（「Classic/クラシック」）→ 一覧に出す名前（「/」の前）。Special/Moon だけはそのまま"""
    if key == MatchTNL.SPECIAL_MOON_KEY:
        return SPECIAL_MOON_TAB
    return key.split("/", 1)[0]


def table_keys(tab_key: str) -> list:
    """一覧の 1 つが受け持つ表のキー（今は一覧 1 つにキー 1 つ。Special/Moon もツールのキーが 1 つ）"""
    return [tab_key]


def load_rounds() -> list:
    """[(一覧のキー, [番号, ...]), ...]。assets/data/continue_slots.json（MatchTNL.slots）の順。読めなければ空。
    枠はそのラウンドに出るテラーだけ（出ないテラーは格子の形だけ残して選べないマスにする）"""
    return [(key, list(ids)) for key, ids in MatchTNL.slots().rounds]


def terror_name(tid: int, round_key: str | None = None) -> str:
    """ツールチップ・検索に使う名前。round_key があれば、そのラウンドだけの名前（Unbound の Bloodthirsty）を先に見る。
    表に無ければ `#番号`"""
    if round_key is not None:
        special = ROUND_NAMES.get((round_display_name(round_key), tid))
        if special:
            return special
    return TerrorIds.name(tid)


# ── アイテムの決まり（GM）───────────────────────
def item_choices(items: dict) -> list:
    """[(id, 選択肢の文字), ...]。先頭は「持っていない」(0)、あとは item.json の全アイテムを店（分類）ごとにまとめ、
    「店 / 名前」。文字が重なるときは末尾に id を付ける"""
    choices, seen = [(ContinueTable.ITEM_NONE, ContinueTable.ITEM_NONE_LABEL)], {ContinueTable.ITEM_NONE_LABEL}
    shops: dict = {}
    for item_id, item in items.items():
        shops.setdefault(item.category, []).append((item_id, item))
    for shop, entries in shops.items():
        for item_id, item in entries:
            text = f"{shop} / {item.name}"
            if text in seen:
                text = f"{text} (#{item_id})"
            seen.add(text)
            choices.append((item_id, text))
    return choices


def unknown_item_text(item_id: int) -> str:
    """item.json に無い id（保存した決まりに残っていたとき）の選択肢の文字"""
    return f"#{item_id}（表に無い）"


def pick_text(pick) -> str:
    """決まりの値（KEEP・None・プリセット名）→ ドロップダウンの文字"""
    if pick is ContinueTable.KEEP:
        return ContinueTable.KEEP_LABEL
    return ContinueTable.NONE_LABEL if pick is None else pick


def pick_from_text(text: str):
    """ドロップダウンの文字 → 決まりの値（pick_text の逆）"""
    if text == ContinueTable.KEEP_LABEL:
        return ContinueTable.KEEP
    return None if text == ContinueTable.NONE_LABEL else text


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


def _positions(kind: str, order=()) -> list:
    """そのブロックの格子の位置（ツールの独自の番号）。ListTool の並び（order）が先、無いものは番号順で後ろ"""
    return _in_order([own for own in TerrorIds.ids() if TerrorIds.kind(own) == kind], order)


def _eight_pages_cell(position: int, by_position: dict) -> int:
    """8 Pages のタブで、格子の位置（Classic・Alternate の番号）に置くマスの番号。その位置に出る 8 Pages の行
    （3000 番台）があればそれ（選べるマス）、無ければ位置の番号のまま（選べないマス）"""
    return by_position.get(position, position)


def layout_for(round_key: str, ids) -> list:
    """上から順のブロック [(種類, [(番号, 列, 行), ...], 行の間隔), ...]。種類は classic・alternate・unbound・
    special・variant（空のブロックは出さない）。ブロックの間は 1 行ぶんあける（描く側）。

    - 格子の形はラウンドで変えない: そのブロックに選べるテラーが 1 つでもあるラウンドは、Classic の 134 個
      （ListTool の順に 12 列）・Alternate の 36 個（ListTool の順に逆三角）・Unbound の 84 個（番号順に 12 列）を
      全部並べる。ids（そのラウンドの枠）に無い位置が「選べないマス」（出ないテラー。描く側が見分ける）
    - 8 Pages: 8 Pages の行（3000 番台）を、その行の ListTool の番号の位置（Classic・Alternate の格子）に置く。
      格子に位置の無い 8 Pages 限定のテラー（Baldi など）は特別のブロック（ListTool の番号の順）
    - special: Special/Moon のタブは枠の中身そのもの（番号順）
    - variant: 4000 番台（Special/Moon のタブ以外）。枠にあるものだけ、番号順の左寄せの 1 行"""
    ids = list(ids)
    have = set(ids)
    order = listtool_order()
    eight_pages = round_key == TerrorIds.EIGHT_PAGES_KEY
    special_moon = round_key == MatchTNL.SPECIAL_MOON_KEY
    variant = [] if special_moon else sorted(i for i in ids if i >= TerrorIds.FIXED_BASE)
    classic = _positions("classic", order["classic"])
    alternate = _positions("alternate", order["alternate"])
    unbound = _positions("unbound")
    special = sorted(i for i in ids if i >= TerrorIds.FIXED_BASE) if special_moon else []
    if eight_pages:
        # 8 Pages の行 → 格子の位置（その行の ListTool の番号を通常の索引で引いた番号）。位置の無い行は特別のブロック
        by_position, loose = {}, []
        for own in sorted(i for i in ids if i in EIGHT_PAGES_RANGE):
            row = TerrorIds.row(own)
            position = TerrorIds.TABLE.by_listtool.get(row.listtool) if row is not None else None
            if position in CLASSIC_RANGE or position in ALTERNATE_RANGE:
                by_position[position] = own
            else:
                loose.append((row.listtool if row is not None and row.listtool is not None else own, own))
        used_classic = any(p in by_position for p in classic)
        used_alternate = any(p in by_position for p in alternate)
        classic = [_eight_pages_cell(p, by_position) for p in classic] if used_classic else []
        alternate = [_eight_pages_cell(p, by_position) for p in alternate] if used_alternate else []
        unbound = []
        special = [own for _lt, own in sorted(loose)]
    else:
        classic = classic if any(i in CLASSIC_RANGE for i in have) else []
        alternate = alternate if any(i in ALTERNATE_RANGE for i in have) else []
        unbound = unbound if any(i in UNBOUND_RANGE for i in have) else []
    blocks = [("classic", grid12(classic), Y_PITCH)]
    if len(alternate) == sum(ALTERNATE_ROWS):
        blocks.append(("alternate", pyramid(alternate), Y_PITCH))
    else:
        blocks.append(("alternate", grid12(alternate), Y_PITCH))
    blocks += [("unbound", grid12(unbound), Y_PITCH), ("special", grid12(special), Y_PITCH),
               ("variant", [(tid, col, 0) for col, tid in enumerate(variant)], Y_PITCH)]
    return [block for block in blocks if block[1]]


def block_height(cells: list, pitch: int) -> int:
    """ブロックの高さ（最初の行の上から、最後の行のマスの下まで。最後の行は Y_PITCH ぶんで数える）"""
    rows = max(row for _tid, _col, row in cells) + 1
    return (rows - 1) * pitch + Y_PITCH


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
    spot = TerrorIds.icon(tid)
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
    def __init__(self, master, on_change=None, on_list_change=None, on_import_tnl=None, on_export_tnl=None):
        super().__init__(master)
        self.title("続行表")
        self.configure(bg=config.GUI_BG)
        self.geometry(f"{CANVAS_WIDTH + 250}x860")
        self.minsize(CANVAS_WIDTH + 230, 420)
        self._on_change = on_change
        self._on_list_change = on_list_change  # 続行リストを変えた（呼び出し側が全窓へ反映する。GN）
        self._on_import_tnl = on_import_tnl    # 「.tnl を続行リストへ読み込む」（呼び出し側が持つ。parent= で窓を渡す。無ければボタンを出さない）
        self._on_export_tnl = on_export_tnl    # 「続行リストを .tnl に書き出す」（同上）
        self._rounds = load_rounds()
        self._photos: dict = {}                # (番号, 不透明度の種類) → PhotoImage（持っていないと消える）
        self._cells: dict = {}                 # 番号 → _Cell（今の格子）
        self._available: frozenset = frozenset()   # 今のラウンドの枠にある番号（選べるマス。ほかは選べないマス）
        self._x0 = 0                           # 格子（幅 CANVAS_WIDTH）を真ん中に寄せる分のずれ
        self._content_height = 0
        self._tip: tuple = ()                  # ツールチップの item の id
        self._save_job = None                  # 保存の予約（after の id）
        self._save_pending = False
        self._list_save_pending = False        # 続行リスト（ContinueList）の保存の予約
        self._item_choices = item_choices(config.ITEMS)      # [(id, 文字), ...]
        self._rule_rows: list = []             # 決まりの行ごとの部品（_show_rules が作り直す）
        self.v_which = tk.StringVar(value=LIST)
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
        for which in (LIST, ContinueTable.DISABLED, ContinueTable.DEFAULT):
            ttk.Radiobutton(top, text=WHICH_TEXT[which], variable=self.v_which, value=which,
                            command=self._restyle_all).pack(side="left", padx=(8, 0))
        # プリセットの欄（続行リストにはプリセットが無いので、選んだら隠す。並びは top の最後なので、戻すときは順に pack し直す）
        self._preset_widgets = []                  # [(部品, pack の引数), ...]
        self._add_preset_widget(ttk.Label(top, text="プリセット:"), padx=(20, 0))
        self.cb_preset = ttk.Combobox(top, textvariable=self.v_preset, state="readonly", width=14)
        self._add_preset_widget(self.cb_preset, padx=(6, 0))
        self.cb_preset.bind("<<ComboboxSelected>>", lambda _e: self._on_preset_picked())
        for text, command in (("新規", self.new_preset), ("複製", self.copy_preset),
                              ("名前変更", self.rename_preset), ("削除", self.delete_preset)):
            self._add_preset_widget(ttk.Button(top, text=text, width=len(text) * 2 + 1, command=command),
                                    padx=(6, 0))

        tools = ttk.Frame(self)
        tools.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Label(tools, text="名前で検索:").pack(side="left")
        ttk.Entry(tools, textvariable=self.v_search, width=18).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="全選択", command=self.select_all).pack(side="left", padx=(20, 0))
        ttk.Button(tools, text="全解除", command=self.clear_all).pack(side="left", padx=(6, 0))
        ttk.Label(self, text=NOTE, foreground=config.GUI_YLW).pack(anchor="w", padx=10)

        # 下の段（欄・ボタン）は side="bottom" で先に置く（窓が低いときに削れるのは格子のほう）
        self._build_item_rules()
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        # 続行リストの 2 つ（呼び出し側の関数。無ければ出さない）｜少しあけて｜プリセットの表へ読み込む 2 つ
        if self._on_import_tnl is not None:
            ttk.Button(bottom, text=".tnl を続行リストへ読み込む",
                       command=lambda: self._on_import_tnl(parent=self)).pack(side="left")
        if self._on_export_tnl is not None:
            ttk.Button(bottom, text="続行リストを .tnl に書き出す",
                       command=lambda: self._on_export_tnl(parent=self)).pack(side="left", padx=(6, 0))
        gap = 20 if (self._on_import_tnl is not None or self._on_export_tnl is not None) else 0
        ttk.Button(bottom, text=".tnnl を続行無効へ読み込む",
                   command=self._import_tnnl).pack(side="left", padx=(gap, 0))
        ttk.Button(bottom, text=".tnl をデフォ続行へ読み込む",
                   command=self._import_tnl).pack(side="left", padx=(6, 0))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self._build_round_list(body)
        self._build_canvas(body)

    def _add_preset_widget(self, widget, **pack):
        widget.pack(side="left", **pack)
        self._preset_widgets.append((widget, pack))

    def _build_item_rules(self):
        """「アイテムで切り替え」の欄（決まりの行を縦にスクロールする欄と「決まりを足す」）"""
        style = ttk.Style(self)
        style.configure(STYLE_RULE_DIM, foreground=COLOR_RULE_DIM)
        style.map(STYLE_RULE_DIM, foreground=[("readonly", COLOR_RULE_DIM), ("disabled", COLOR_RULE_DIM)])
        box = ttk.LabelFrame(self, text=RULES_TITLE, padding=6)
        box.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        ttk.Label(box, text=RULES_NOTE, foreground=config.GUI_YLW).pack(anchor="w")
        area = ttk.Frame(box)
        area.pack(fill="x", pady=(4, 0))
        self.rules_canvas = tk.Canvas(area, height=RULES_AREA_HEIGHT, bg=config.GUI_BG, highlightthickness=0)
        bar = ttk.Scrollbar(area, orient="vertical", command=self.rules_canvas.yview)
        self.rules_canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.rules_canvas.pack(side="left", fill="x", expand=True)
        self.rules_frame = ttk.Frame(self.rules_canvas)
        self.rules_canvas.create_window((0, 0), window=self.rules_frame, anchor="nw")
        self.rules_frame.bind("<Configure>", lambda _e: self.rules_canvas.configure(
            scrollregion=self.rules_canvas.bbox("all")))
        self.rules_canvas.bind("<Enter>", lambda _e: self.rules_canvas.bind_all("<MouseWheel>", self._rules_wheel))
        self.rules_canvas.bind("<Leave>", lambda _e: self.rules_canvas.unbind_all("<MouseWheel>"))
        ttk.Button(box, text="決まりを足す", command=self.add_rule).pack(anchor="w", pady=(4, 0))
        self._show_rules()

    def _rules_wheel(self, event):
        self.rules_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

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
            self.canvas.unbind_all("<MouseWheel>")     # 窓の上で閉じても、ほかの窓へ効きを残さない（決まりの欄も同じ）
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
        """一覧で選んでいるラウンドのキー"""
        return self._rounds[self._index][0] if 0 <= self._index < len(self._rounds) else None

    def _round_ids(self) -> list:
        return self._rounds[self._index][1] if 0 <= self._index < len(self._rounds) else []

    def _table_keys(self) -> list:
        """今のラウンドの表のキー"""
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
        """which の表に、今のラウンドの表のキーで tid が入っているか（LIST はツールの続行リスト）"""
        kind = ContinueList.current() if which == LIST else getattr(table, which)
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
        self._available = frozenset(self._round_ids())
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
        """番号のアイコンの、不透明度の種類（LEVELS: dim・other・full・faint・gone）ごとの PhotoImage（作ったら持っておく）。
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
        （選んでいる・いないにかかわらず）、合うもので選んでいないものは 100% にして枠を目立たせる。
        そのラウンドに出ないテラー（枠に無いマス）は何があっても 12%＋枠なし（選べないマス）"""
        if tid not in self._available:
            return "gone", COLOR_GRID_BG, THIN
        if self._searching() and not self._matches(tid):
            return "faint", COLOR_FAINT, THIN
        table, which = self._edit_table(), self._which()
        if self._has(table, which, tid):
            return "full", STRONG[which], THICK
        for other in OTHERS[which]:
            if self._has(table, other, tid):
                return "other", STRONG[other], THIN
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
        if tid is not None and tid in self._available:      # 選べないマスは何も起きない
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
        label = terror_name(tid, self._round_key()) + ("" if tid in self._available else GONE_TIP)
        text = self.canvas.create_text(0, 0, text=label, anchor="center",
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

    def _schedule_save(self, table: bool = True):
        """保存を予約する（table=False は続行リストだけ。続行表のファイルは書かない）"""
        self._save_pending = self._save_pending or table
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
        if self._list_save_pending:
            self._list_save_pending = False
            ContinueList.save_current()

    def _commit_list(self, change, tids=None):
        """ツールの続行リストを change(今の dict) の戻り値にして、全窓の判定へ効かせ（呼び出し側）・保存を予約し・
        変わったセル（None なら全部）の見た目だけ直す。続行表のファイル（continue_table.json）は触らない"""
        ContinueList.update(change)
        self._list_save_pending = True
        self._schedule_save(table=False)
        if tids is None:
            self._restyle_all()
        else:
            for tid in tids:
                self._restyle(tid)
        if self._on_list_change is not None:
            self._on_list_change()

    def refresh_list(self):
        """ツールの続行リストが画面の外で変わった（生存で外れた）: 続行リストを見ているなら見た目を直す"""
        if self._which() == LIST:
            self._restyle_all()

    def _change_members(self, tids, add: bool):
        """今の表（続行リストを含む）の、今のラウンドの表のキーへ tids を入れる／外す"""
        if self._which() == LIST:
            change = ContinueList.with_ids_added if add else ContinueList.with_ids_removed
            keys = self._table_keys()
            self._commit_list(lambda data: change(data, keys, tids))
        else:
            self._commit(self._with_members(tids, add=add))

    def _with_members(self, tids, add: bool) -> ContinueTable.Table:
        """今の表の、今のラウンドの表のキーへ tids を入れた／外した新しい Table"""
        table, which = self._edit_table(), self._which()
        for key in self._table_keys():
            ids = set(getattr(table, which).get(key, ()))
            ids = ids | set(tids) if add else ids - set(tids)
            table = ContinueTable.with_ids(table, which, key, ids)
        return table

    def toggle(self, tid: int):
        """押したマスを入れる／外す。そのラウンドに出ない（枠に無い）テラーのマスは何も起きない"""
        if self._round_key() is None or tid not in self._available:
            return
        if self._which() == LIST:
            keys = self._table_keys()
            self._commit_list(lambda data: ContinueList.toggled(data, keys, tid), (tid,))
            return
        present = self._has(self._edit_table(), self._which(), tid)
        self._commit(self._with_members([tid], add=not present), (tid,))

    def _visible_tids(self) -> list:
        """今のラウンドで見えている選べるマスの番号（バリアントの段も含む。選べないマスは入らない）。
        検索で絞っているときは合うものだけ"""
        return [tid for tid in self._cells if tid in self._available and self._matches(tid)]

    def select_all(self):
        """見えているマスを全部、今の表に入れる"""
        if self._round_key() is not None:
            self._change_members(self._visible_tids(), add=True)

    def clear_all(self):
        """見えているマスを全部、今の表から外す"""
        if self._round_key() is not None:
            self._change_members(self._visible_tids(), add=False)

    # ── プリセット（GL）──
    def _selected_name(self) -> str:
        """今の表の種類で編集しているプリセットの名前"""
        return self._selected[self._which()]

    def _show_presets(self):
        """プリセットの選択肢と、選んでいる名前を今の表の種類のものに合わせる（無い名前を選んでいたら直す）。
        続行リストにはプリセットが無いので、プリセットの欄ごと隠す"""
        store, which = ContinueTable.current(), self._which()
        if which == LIST:
            for widget, _pack in self._preset_widgets:
                widget.pack_forget()
            return
        for widget, pack in self._preset_widgets:
            if not widget.winfo_manager():
                widget.pack(side="left", **pack)
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
        self._show_rules()                            # 決まりの選択肢（プリセット名）も作り直す
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

    # ── アイテムで切り替え（GM）──
    def _row_item_map(self, item_id: int) -> dict:
        """行のアイテムのドロップダウンの {文字: id}。item.json に無い id なら、その行だけ選択肢に足す"""
        mapping = {text: tid for tid, text in self._item_choices}
        if item_id not in mapping.values():
            mapping[unknown_item_text(item_id)] = item_id
        return mapping

    def _show_rules(self):
        """決まりの行を今の保存内容から作り直す（足す・動かす・消す・プリセットの増減のあと）"""
        for child in self.rules_frame.winfo_children():
            child.destroy()
        self._rule_rows = []
        store = ContinueTable.current()
        values = {which: [ContinueTable.KEEP_LABEL, ContinueTable.NONE_LABEL,
                          *ContinueTable.preset_names(store, which)]
                  for which in (ContinueTable.DISABLED, ContinueTable.DEFAULT)}
        for index, rule in enumerate(store.item_rules):
            self._rule_rows.append(self._build_rule_row(index, rule, values))
        self._dim_shadowed_rules()

    def _build_rule_row(self, index: int, rule: ContinueTable.ItemRule, values: dict) -> dict:
        """1 つの決まりの行: アイテム・続行無効・デフォ続行のドロップダウンと ↑ ↓ ×"""
        mapping = self._row_item_map(rule.item)
        text_of = {tid: text for text, tid in mapping.items()}
        row = {"items": mapping,
               "v_item": tk.StringVar(value=text_of[rule.item]),
               ContinueTable.DISABLED: tk.StringVar(value=pick_text(rule.disabled)),
               ContinueTable.DEFAULT: tk.StringVar(value=pick_text(rule.default))}
        frame = ttk.Frame(self.rules_frame)
        frame.pack(fill="x", pady=1)
        row["frame"] = frame
        row["cb_item"] = self._rule_combo(frame, row["v_item"], list(mapping), 30, index)
        row["cb_item"].pack(side="left")
        for which in (ContinueTable.DISABLED, ContinueTable.DEFAULT):
            ttk.Label(frame, text=f"{WHICH_TEXT[which]}:").pack(side="left", padx=(10, 0))
            row[f"cb_{which}"] = self._rule_combo(frame, row[which], values[which], 12, index)
            row[f"cb_{which}"].pack(side="left", padx=(4, 0))
        for text, command in (("↑", lambda i=index: self.move_rule(i, -1)),
                              ("↓", lambda i=index: self.move_rule(i, +1)),
                              ("×", lambda i=index: self.delete_rule(i))):
            button = ttk.Button(frame, text=text, width=3, command=command)
            button.pack(side="left", padx=(6 if text == "↑" else 2, 0))
            row[text] = button
        return row

    def _rule_combo(self, parent, variable, values, width: int, index: int) -> ttk.Combobox:
        box = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=width)
        box.bind("<<ComboboxSelected>>", lambda _e, i=index: self._on_rule_picked(i))
        return box

    def _dim_shadowed_rules(self):
        """上に同じアイテムの決まりがある行（使われない）のドロップダウンを薄い文字にする"""
        shadowed = ContinueTable.shadowed_rules(ContinueTable.current())
        for row, dim in zip(self._rule_rows, shadowed):
            for key in ("cb_item", f"cb_{ContinueTable.DISABLED}", f"cb_{ContinueTable.DEFAULT}"):
                row[key].configure(style=STYLE_RULE_DIM if dim else "TCombobox")

    def _on_rule_picked(self, index: int):
        """行のドロップダウンで選んだ: その行の決まりを変えて、判定へ・保存の予約"""
        row = self._rule_rows[index]
        store = ContinueTable.with_rule_changed(
            ContinueTable.current(), index, item=row["items"][row["v_item"].get()],
            disabled=pick_from_text(row[ContinueTable.DISABLED].get()),
            default=pick_from_text(row[ContinueTable.DEFAULT].get()))
        self._commit_store(store, tids=())
        self._dim_shadowed_rules()

    def add_rule(self):
        """決まりを末尾に足す（初めは「持っていない」・どちらも窓のまま）"""
        self._commit_store(ContinueTable.with_rule_added(ContinueTable.current()), tids=())
        self._show_rules()
        self.rules_canvas.update_idletasks()
        self.rules_canvas.yview_moveto(1.0)

    def move_rule(self, index: int, delta: int):
        """index 番目の決まりを上（-1）・下（+1）へ。端では何もしない"""
        store = ContinueTable.with_rule_moved(ContinueTable.current(), index, delta)
        if store is not ContinueTable.current():
            self._commit_store(store, tids=())
            self._show_rules()

    def delete_rule(self, index: int):
        store = ContinueTable.with_rule_deleted(ContinueTable.current(), index)
        if store is not ContinueTable.current():
            self._commit_store(store, tids=())
            self._show_rules()

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
