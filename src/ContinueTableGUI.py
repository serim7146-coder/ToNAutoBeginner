"""続行表の編集画面（GH）。ListTool・ToN Utils の続行選択画面のように、テラーのアイコンを押して選ぶ。

- 上: どちらの表を編集するか（続行無効／デフォ続行）・名前の検索
- 左: ラウンドの一覧（assets/data/continue_slots.json の順）
- 右: テラーのアイコンの格子（1 行 10 個）。押すと今の表に入れる／外す。枠の色: 赤＝続行無効・緑＝デフォ続行・
  薄い色＝もう片方の表に入っている
- 下: .tnl をデフォ続行へ・.tnnl を続行無効へ読み込む／このラウンドを全部外す

変えたらすぐ保存し、判定に効かせる（ContinueTable.set_current）。アイコンは assets/icons のシート
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
import UIFont

SLOTS_FILE = "assets/data/continue_slots.json"
ICONS_DIR = "assets/icons"
ICONS_JSON = f"{ICONS_DIR}/icons.json"
TERRORS_FILE = "assets/data/terrors.json"

COLUMNS = 10                 # 格子の 1 行の個数
ICON_SIZE = 40               # 表示するアイコンの大きさ（32px を INTER_NEAREST で 40px へ拡大）
CELL_WIDTH = 64              # 1 マスの幅
BORDER = 3                   # 枠の太さ

COLOR_IDLE = config.GUI_SUB
COLOR_DISABLED = config.GUI_RED
COLOR_DEFAULT = config.GUI_GRN
COLOR_DISABLED_FAINT = "#5a3340"       # もう片方の表（続行無効）に入っている
COLOR_DEFAULT_FAINT = "#3d5a3f"        # もう片方の表（デフォ続行）に入っている

WHICH_TEXT = {ContinueTable.DISABLED: "続行無効", ContinueTable.DEFAULT: "デフォ続行"}
STRONG = {ContinueTable.DISABLED: COLOR_DISABLED, ContinueTable.DEFAULT: COLOR_DEFAULT}
FAINT = {ContinueTable.DISABLED: COLOR_DISABLED_FAINT, ContinueTable.DEFAULT: COLOR_DEFAULT_FAINT}
OTHER = {ContinueTable.DISABLED: ContinueTable.DEFAULT, ContinueTable.DEFAULT: ContinueTable.DISABLED}

NOTE = "干し芋・焼き芋（グループのインスタンス）では使いません"


# ── データ（画面を作らずに読める部分）────────────────────
def round_display_name(key: str) -> str:
    """ラウンドのキー（「Classic/クラシック」）→ 一覧に出す名前（「/」の前）"""
    return key.split("/", 1)[0]


@functools.lru_cache(maxsize=1)
def load_rounds() -> list:
    """[(ラウンドのキー, [番号, ...]), ...]。assets/data/continue_slots.json の順。読めなければ空"""
    try:
        raw = json.loads(config.resource_path(SLOTS_FILE).read_text(encoding="utf-8"))
        return [(str(key), [int(i) for i in ids]) for key, ids in raw["rounds"]]
    except (OSError, ValueError, KeyError, TypeError):
        DebugLog.exception("ContinueTableGUI.load_rounds")
        return []


@functools.lru_cache(maxsize=1)
def terror_names() -> dict:
    """{続行リストの番号: 名前}。classic・alternate・unbound は id、8pages は list_id（重なる番号は前者）"""
    try:
        raw = json.loads(config.resource_path(TERRORS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        DebugLog.exception("ContinueTableGUI.terror_names")
        return {}
    names: dict = {}
    for section, key in (("classic", "id"), ("alternate", "id"), ("unbound", "id"),
                         ("8pages", "list_id")):
        for entry in raw.get(section, ()):
            tid = entry.get(key)
            if isinstance(tid, int) and entry.get("name"):
                names.setdefault(tid, entry["name"])
    return names


def terror_name(tid: int) -> str:
    return terror_names().get(tid) or f"#{tid}"


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


def icon_png(tid: int) -> bytes | None:
    """番号のアイコンを切り出し、40px へ拡大した PNG のバイト列。絵が無い・切り出せないなら None"""
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
    ok, png = cv2.imencode(".png", big)
    return png.tobytes() if ok else None


def tile_color(tid: int) -> str:
    """絵が無い番号の札の色（番号ごとに決まった、暗めの色）"""
    r, g, b = (60 + (tid * 53) % 100, 60 + (tid * 97) % 100, 60 + (tid * 29) % 100)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── 画面 ─────────────────────────────────
class ContinueTableWindow(tk.Toplevel):
    def __init__(self, master, on_change=None):
        super().__init__(master)
        self.title("続行表")
        self.configure(bg=config.GUI_BG)
        self.geometry("900x620")
        self.minsize(720, 460)
        self._on_change = on_change
        self._rounds = load_rounds()
        self._photos: dict = {}                # 番号 → PhotoImage（持っていないと消える）
        self._cells: dict = {}                 # 番号 → マスの Frame（今の格子）
        self.v_which = tk.StringVar(value=ContinueTable.DISABLED)
        self.v_search = tk.StringVar()
        self._build()
        if self._rounds:
            self.list_rounds.selection_set(0)
        self._show_round()
        self.v_search.trace_add("write", lambda *_: self._show_round())

    # ── 組み立て ──
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="編集する表:").pack(side="left")
        for which in (ContinueTable.DISABLED, ContinueTable.DEFAULT):
            ttk.Radiobutton(top, text=WHICH_TEXT[which], variable=self.v_which, value=which,
                            command=self._restyle_all).pack(side="left", padx=(8, 0))
        ttk.Label(top, text="名前で検索:").pack(side="left", padx=(20, 0))
        ttk.Entry(top, textvariable=self.v_search, width=22).pack(side="left", padx=(6, 0))
        ttk.Label(self, text=NOTE, foreground=config.GUI_YLW).pack(anchor="w", padx=10)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        self.list_rounds = tk.Listbox(body, exportselection=False, width=20, bg=config.GUI_SUB,
                                      fg=config.GUI_FG, selectbackground=config.GUI_ACC,
                                      selectforeground=config.GUI_BG, font=(UIFont.UI, 10),
                                      activestyle="none", highlightthickness=0)
        for key, _ids in self._rounds:
            self.list_rounds.insert("end", round_display_name(key))
        self.list_rounds.pack(side="left", fill="y")
        self.list_rounds.bind("<<ListboxSelect>>", lambda _e: self._show_round())
        self._build_grid(body)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text=".tnl をデフォ続行へ読み込む",
                   command=self._import_tnl).pack(side="left")
        ttk.Button(bottom, text=".tnnl を続行無効へ読み込む",
                   command=self._import_tnnl).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="このラウンドを全部外す",
                   command=self._clear_round).pack(side="left", padx=(6, 0))

    def _build_grid(self, parent):
        """スクロールできる格子の入れ物"""
        box = ttk.Frame(parent)
        box.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.canvas = tk.Canvas(box, bg=config.GUI_BG, highlightthickness=0)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = tk.Frame(self.canvas, bg=config.GUI_BG)
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def destroy(self):
        try:
            self.canvas.unbind_all("<MouseWheel>")     # 窓の上で閉じても、ほかの窓へ効きを残さない
        except tk.TclError:
            pass
        super().destroy()

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ── 今の状態 ──
    def _round_key(self) -> str | None:
        sel = self.list_rounds.curselection()
        return self._rounds[sel[0]][0] if sel else None

    def _round_ids(self) -> list:
        sel = self.list_rounds.curselection()
        return self._rounds[sel[0]][1] if sel else []

    def _which(self) -> str:
        return self.v_which.get()

    def _matches(self, tid: int) -> bool:
        needle = self.v_search.get().strip().lower()
        return not needle or needle in terror_name(tid).lower()

    # ── 格子 ──
    def _show_round(self):
        """今のラウンドの格子を作り直す（検索の絞り込みも）"""
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._cells = {}
        shown = [tid for tid in self._round_ids() if self._matches(tid)]
        for n, tid in enumerate(shown):
            cell = self._make_cell(tid)
            cell.grid(row=n // COLUMNS, column=n % COLUMNS, padx=2, pady=2, sticky="n")
            self._cells[tid] = cell
        self._restyle_all()
        self.canvas.yview_moveto(0)

    def _photo(self, tid: int):
        if tid not in self._photos:
            png = icon_png(tid)
            self._photos[tid] = (tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
                                 if png else None)
        return self._photos[tid]

    def _make_cell(self, tid: int) -> tk.Frame:
        """1 マス: アイコン（無ければ名前の札）＋ 名前。どこを押しても切り替わる"""
        name = terror_name(tid)
        cell = tk.Frame(self.grid_frame, bg=config.GUI_BG, width=CELL_WIDTH,
                        highlightthickness=BORDER, highlightbackground=COLOR_IDLE)
        photo = self._photo(tid)
        inner = []
        if photo is not None:
            face = tk.Label(cell, image=photo, bg=config.GUI_BG, bd=0)
        else:
            # 絵が無い番号は名前の札（色付きの四角に名前）
            face = tk.Frame(cell, width=ICON_SIZE, height=ICON_SIZE, bg=tile_color(tid))
            face.pack_propagate(False)
            text = tk.Label(face, text=name, bg=tile_color(tid), fg="#ffffff",
                            wraplength=ICON_SIZE - 2, font=(UIFont.UI, 6), bd=0)
            text.pack(expand=True)
            inner.append(text)
        face.pack(padx=2, pady=(2, 0))
        caption = tk.Label(cell, text=name, bg=config.GUI_BG, fg=config.GUI_FG, font=(UIFont.UI, 7),
                           wraplength=CELL_WIDTH - 4, justify="center", height=2, bd=0)
        caption.pack(padx=1, pady=(0, 2))
        for widget in (cell, face, caption, *inner):
            widget.bind("<Button-1>", lambda _e, t=tid: self.toggle(t))
        return cell

    def _border_color(self, tid: int) -> str:
        table, key, which = ContinueTable.current(), self._round_key(), self._which()
        if tid in getattr(table, which).get(key, ()):
            return STRONG[which]
        if tid in getattr(table, OTHER[which]).get(key, ()):
            return FAINT[OTHER[which]]
        return COLOR_IDLE

    def _restyle_all(self):
        for tid, cell in self._cells.items():
            cell.configure(highlightbackground=self._border_color(tid))

    # ── 変える（すぐ保存して判定に効かせる）──
    def _commit(self, table: ContinueTable.Table):
        ContinueTable.set_current(table)
        ContinueTable.save(table)
        self._restyle_all()
        if self._on_change is not None:
            self._on_change()

    def toggle(self, tid: int):
        key = self._round_key()
        if key is None:
            return
        self._commit(ContinueTable.with_toggled(ContinueTable.current(), self._which(), key, tid))

    def _clear_round(self):
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
