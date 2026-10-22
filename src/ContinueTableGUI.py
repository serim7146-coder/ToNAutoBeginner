"""続行表の編集画面（GI）。ListTool の続行選択画面のように、テラーのアイコンを押して選ぶ。

- 上: どちらの表を編集するか（続行無効／デフォ続行）・ラウンドの選択（小さなドロップダウン）・名前の検索
- 中: テラーのアイコンの格子（tk.Canvas に直接描く）。1 行 12 個・行ごとに真ん中寄せ（Alternate は 11/9/7/5/3/1 の
  逆三角）。名前は出さない（マウスを乗せるとツールチップ）。選んでいないものは暗く、今の表に入っているものは
  明るく＋太い枠（赤＝続行無効・緑＝デフォ続行）、もう片方の表に入っているものは 60% の明るさ＋その色の細い枠。
  Bloodthirsty（190〜192）・Fog/Midnight の 315/316 は、格子の下の「バリアントの段」に左寄せで置く
- 下: .tnl をデフォ続行へ・.tnnl を続行無効へ読み込む／このラウンドを全部外す

押したセルの絵と枠だけを `itemconfigure` で変える（格子は作り直さない）。判定への反映（set_current）は押した
その場で、保存は 0.3 秒まとめて 1 回（閉じるときは残りを先に書く）。アイコンは assets/icons のシート
（32px 格子）から OpenCV で切り出して 40px に拡大する（Pillow は使わない）。絵が無い番号は名前の札
"""
import base64
import functools
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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

COLUMNS = 12                 # 格子の 1 行の個数
ALTERNATE_ROWS = (11, 9, 7, 5, 3, 1)   # Alternate は逆三角
ICON_SIZE = 40               # 表示するアイコンの大きさ（32px を INTER_NEAREST で 40px へ拡大）
CELL = 48                    # 1 マスの大きさ（アイコン 40px＋まわりの枠と隙間）
MARGIN = 4                   # マスの端からアイコンまで
GRID_WIDTH = COLUMNS * CELL  # 格子の幅（キャンバスが広ければ真ん中に寄せる）
TOP_PAD = 8                  # 最初の段の上の余白
SAVE_DELAY_MS = 300          # 押してから保存するまで（まとめて 1 回）

# 明るさ（アイコンの絵に掛ける）: 選んでいない／もう片方の表に入っている／今の表に入っている
LEVELS = {"dim": 0.35, "other": 0.6, "full": 1.0}
THIN, THICK = 1, 3           # 枠の太さ

COLOR_GRID_BG = "#2a2a2a"    # 格子の背景（暗い灰色）
COLOR_IDLE = "#7a7a7a"       # 選んでいないものの枠（灰色）
COLOR_FAINT = "#3c3c3c"      # 検索に合わないものの枠
COLOR_MATCH = config.GUI_ACC  # 検索に合うものの枠
COLOR_DISABLED = config.GUI_RED
COLOR_DEFAULT = config.GUI_GRN

WHICH_TEXT = {ContinueTable.DISABLED: "続行無効", ContinueTable.DEFAULT: "デフォ続行"}
STRONG = {ContinueTable.DISABLED: COLOR_DISABLED, ContinueTable.DEFAULT: COLOR_DEFAULT}
OTHER = {ContinueTable.DISABLED: ContinueTable.DEFAULT, ContinueTable.DEFAULT: ContinueTable.DISABLED}

# バリアントの段（格子の下に左寄せ）: Bloodthirsty の 3 つと Fog/Midnight の Neo Pilot・Foxy
BLOODTHIRSTY_IDS = (190, 191, 192)
VARIANT_IDS = (190, 191, 192, 315, 316)
NO_VARIANT_ROUNDS = frozenset({"Mystic Moon", "Blood Moon"})   # 13 個すべて本体の格子
# ラウンドごとの名前の上書き（ラウンド名＝キーの「/」の前, 番号）
ROUND_NAMES = {("Unbound", 192): "Pack of Wild Yet Bloodthirsty Creatures"}

NOTE = "干し芋・焼き芋（グループのインスタンス）では使いません"


# ── データ（画面を作らずに読める部分）────────────────────
def round_display_name(key: str) -> str:
    """ラウンドのキー（「Classic/クラシック」）→ 一覧に出す名前（「/」の前）"""
    return key.split("/", 1)[0]


@functools.lru_cache(maxsize=1)
def load_rounds() -> list:
    """[(ラウンドのキー, [番号, ...]), ...]。assets/data/continue_slots.json の順。読めなければ空。
    Special/Moon の枠は出さない（中身は各ラウンドのバリアントの段へ移した）"""
    try:
        raw = json.loads(config.resource_path(SLOTS_FILE).read_text(encoding="utf-8"))
        return [(str(key), [int(i) for i in ids]) for key, ids in raw["rounds"]
                if str(key) != MatchTNL.SPECIAL_MOON_KEY]
    except (OSError, ValueError, KeyError, TypeError):
        DebugLog.exception("ContinueTableGUI.load_rounds")
        return []


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
def _split_rows(ids: list, sizes) -> list:
    """ids を sizes の順に切る。sizes を使い切った残りは COLUMNS 個ずつ"""
    rows, pos = [], 0
    for size in sizes:
        if pos >= len(ids):
            break
        rows.append(ids[pos:pos + size])
        pos += size
    rows.extend(ids[i:i + COLUMNS] for i in range(pos, len(ids), COLUMNS))
    return rows


def layout_for(round_key: str, ids) -> tuple[list, list]:
    """(本体の格子の段 [[番号, ...], ...], バリアントの段 [番号, ...])。

    - 8 Pages は 8 Pages に出るテラー（eight_pages_ids）だけ
    - Bloodthirsty（192）があるラウンドは 190・191・192 を、Fog/Midnight は 315・316 も、本体から外して
      バリアントの段へ（Unbound の Bloodthirsty は 192 だけ）。Mystic Moon・Blood Moon は全部本体
    - Alternate は 11/9/7/5/3/1 の逆三角・ほかは 12 個ずつ"""
    name = round_display_name(round_key)
    ids = list(ids)
    if name == "8 Pages":
        allowed = eight_pages_ids()
        ids = [i for i in ids if i in allowed]
    variant: list = []
    if name not in NO_VARIANT_ROUNDS:
        wanted = {i for i in VARIANT_IDS if i in ids}
        if 192 in wanted and name != "Unbound":
            wanted.update(BLOODTHIRSTY_IDS)
        variant = sorted(wanted)
        ids = [i for i in ids if i not in wanted]
    sizes = ALTERNATE_ROWS if name == "Alternate" else ()
    return _split_rows(ids, sizes), variant


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


def icon_png(tid: int, brightness: float = 1.0) -> bytes | None:
    """番号のアイコンを切り出し、40px へ拡大した PNG のバイト列（brightness 倍の明るさ。透明はそのまま）。
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
    big = cv2.resize(crop, (ICON_SIZE, ICON_SIZE), interpolation=cv2.INTER_NEAREST)
    if brightness != 1.0:
        big[..., :3] = (big[..., :3].astype(np.float32) * brightness).round().astype(np.uint8)
    ok, png = cv2.imencode(".png", big)
    return png.tobytes() if ok else None


def tile_color(tid: int) -> str:
    """絵が無い番号の札の色（番号ごとに決まった、暗めの色）"""
    r, g, b = (60 + (tid * 53) % 100, 60 + (tid * 97) % 100, 60 + (tid * 29) % 100)
    return f"#{r:02x}{g:02x}{b:02x}"


def scale_color(color: str, factor: float) -> str:
    """"#rrggbb" の明るさを factor 倍にした色"""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(min(255, round(v * factor)) for v in (r, g, b))


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
        self.geometry("680x720")
        self.minsize(560, 420)
        self._on_change = on_change
        self._rounds = load_rounds()
        self._photos: dict = {}                # (番号, 明るさの種類) → PhotoImage（持っていないと消える）
        self._cells: dict = {}                 # 番号 → _Cell（今の格子）
        self._x0 = 0                           # 格子を真ん中に寄せる分のずれ
        self._content_height = 0
        self._tip: tuple = ()                  # ツールチップの item の id
        self._save_job = None                  # 保存の予約（after の id）
        self._save_pending = False
        self.v_which = tk.StringVar(value=ContinueTable.DISABLED)
        self.v_search = tk.StringVar()
        self.v_round = tk.StringVar()
        self._build()
        if self._rounds:
            self.combo_round.current(0)
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
        ttk.Label(top, text="ラウンド:").pack(side="left", padx=(20, 0))
        self.combo_round = ttk.Combobox(top, textvariable=self.v_round, state="readonly", width=18,
                                        values=[round_display_name(key) for key, _ids in self._rounds])
        self.combo_round.pack(side="left", padx=(6, 0))
        self.combo_round.bind("<<ComboboxSelected>>", lambda _e: self._show_round())
        ttk.Label(top, text="名前で検索:").pack(side="left", padx=(20, 0))
        ttk.Entry(top, textvariable=self.v_search, width=18).pack(side="left", padx=(6, 0))
        ttk.Label(self, text=NOTE, foreground=config.GUI_YLW).pack(anchor="w", padx=10)

        self._build_canvas()

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text=".tnl をデフォ続行へ読み込む",
                   command=self._import_tnl).pack(side="left")
        ttk.Button(bottom, text=".tnnl を続行無効へ読み込む",
                   command=self._import_tnnl).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="このラウンドを全部外す",
                   command=self._clear_round).pack(side="left", padx=(6, 0))

    def _build_canvas(self):
        """格子を描くキャンバス（縦にスクロール）。押す・乗せるは全マス共通の 1 組の束縛"""
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10, pady=6)
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
        x0 = max(0, (width - GRID_WIDTH) // 2)
        if x0 != self._x0:
            self.canvas.move("cell", x0 - self._x0, 0)
            self._x0 = x0
            self._hide_tip()

    def _set_scrollregion(self, width: int, height: int):
        self.canvas.configure(scrollregion=(0, 0, width, max(self._content_height, height)))

    # ── 今の状態 ──
    def _round_key(self) -> str | None:
        index = self.combo_round.current()
        return self._rounds[index][0] if 0 <= index < len(self._rounds) else None

    def _round_ids(self) -> list:
        index = self.combo_round.current()
        return self._rounds[index][1] if 0 <= index < len(self._rounds) else []

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
        main_rows, variant_row = layout_for(self._round_key() or "", self._round_ids())
        self._x0 = max(0, (self.canvas.winfo_width() - GRID_WIDTH) // 2)
        y = TOP_PAD
        for row in main_rows:
            self._draw_row(row, y, (COLUMNS - len(row)) * CELL // 2)     # 行ごとに真ん中寄せ
            y += CELL
        if variant_row:
            y += CELL if main_rows else 0          # 本体の格子の下に 1 段あけて、左寄せ
            self._draw_row(variant_row, y, 0)
            y += CELL
        self._content_height = y + TOP_PAD
        self._set_scrollregion(self.canvas.winfo_width(), self.canvas.winfo_height())
        self._restyle_all()
        self.canvas.yview_moveto(0)

    def _draw_row(self, ids: list, y: int, indent: int):
        for n, tid in enumerate(ids):
            self._cells[tid] = self._draw_cell(tid, self._x0 + indent + n * CELL, y)

    def _draw_cell(self, tid: int, x: int, y: int) -> _Cell:
        """1 マス: アイコン（無ければ名前の札）＋ 枠。絵の明るさと枠の色・太さは _restyle で決める"""
        tags = ("cell", f"t{tid}")
        ix, iy = x + MARGIN, y + MARGIN
        label = None
        if self._photo(tid, "full") is not None:
            face = self.canvas.create_image(ix, iy, anchor="nw", tags=tags)
        else:
            # 絵が無い番号は名前の札（色付きの四角に名前）
            face = self.canvas.create_rectangle(ix, iy, ix + ICON_SIZE, iy + ICON_SIZE,
                                                width=0, tags=tags)
            label = self.canvas.create_text(ix + ICON_SIZE // 2, iy + ICON_SIZE // 2,
                                            text=terror_name(tid, self._round_key()),
                                            width=ICON_SIZE - 2, justify="center",
                                            font=(UIFont.UI, 6), tags=tags)
        frame = self.canvas.create_rectangle(ix - 2, iy - 2, ix + ICON_SIZE + 1, iy + ICON_SIZE + 1,
                                             tags=tags)
        return _Cell(tid, face, label, frame)

    def _photo(self, tid: int, level: str):
        """番号のアイコンの、明るさの種類（dim・other・full）ごとの PhotoImage（作ったら持っておく）。
        絵が無ければ None"""
        if (tid, level) not in self._photos:
            png = icon_png(tid, LEVELS[level])
            self._photos[(tid, level)] = (tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
                                          if png else None)
        return self._photos[(tid, level)]

    # ── 見た目（セルごとに itemconfigure）──
    def _style_of(self, tid: int) -> tuple[str, str, int]:
        """(明るさの種類, 枠の色, 枠の太さ)。今の表に入っている→明るく＋太い枠（赤／緑）、もう片方の表→60%＋
        その色の細い枠、どちらも無い→暗く＋灰色の細い枠。検索の欄に字があれば、合わないものは暗いまま枠も薄く
        （選んでいる・いないにかかわらず）、合うもので選んでいないものは明るくして枠を目立たせる"""
        if self._searching() and not self._matches(tid):
            return "dim", COLOR_FAINT, THIN
        table, key, which = ContinueTable.current(), self._round_key(), self._which()
        if tid in getattr(table, which).get(key, ()):
            return "full", STRONG[which], THICK
        if tid in getattr(table, OTHER[which]).get(key, ()):
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
            self.canvas.itemconfigure(cell.face, fill=scale_color(tile_color(tid), LEVELS[level]))
            self.canvas.itemconfigure(cell.label, fill=scale_color("#ffffff", LEVELS[level]))
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
        x0, y0, x1, y1 = self.canvas.coords(cell.frame)
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
        """判定に効かせ（set_current）・保存を予約し・変わったセル（None なら全部）の見た目だけ直す"""
        ContinueTable.set_current(table)
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

    def toggle(self, tid: int):
        key = self._round_key()
        if key is None:
            return
        self._commit(ContinueTable.with_toggled(ContinueTable.current(), self._which(), key, tid), (tid,))

    def _clear_round(self):
        """今の表の、このラウンドを全部外す（バリアントの段も含む）"""
        key = self._round_key()
        if key is not None:
            self._commit(ContinueTable.with_ids(ContinueTable.current(), self._which(), key, ()))

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
        if not self._confirm(f"{WHICH_TEXT[which]}の今の中身を、{ext} の中身に置き換えます。よいですか？"):
            return
        try:
            new = reader(path)
        except Exception:
            DebugLog.exception("ContinueTableGUI._import")
            self._error(f"{ext} を読めませんでした。ファイルを確かめてください。")
            return
        self._commit(ContinueTable.with_all(ContinueTable.current(), which, new))
