import os
import calendar
import math
import tkinter as tk
import win32gui
import glob
import threading
from pathlib import Path
from tkinter import ttk, scrolledtext, filedialog, messagebox
from datetime import datetime
from typing import Optional

import config
import ConnectDB
import LogMonitor
import PlaySound
import MatchTNL
import Statistics

# ═══════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════
VRCHAT_LOG_DIR = (
    Path(os.environ.get("APPDATA", "~")) / ".." / "LocalLow" / "VRChat" / "VRChat"
).resolve()

VRCHAT_WINDOW_CLASS  = "UnityWndClass"


# ═══════════════════════════════════════════════
#  最新ログを読み取る処理
# ═══════════════════════════════════════════════
def get_vrchat_windows(hwnd_count: int) -> list[int]:
    hwnds = []
    found_hwnd = 0
    def cb(h, _):
        nonlocal found_hwnd
        if found_hwnd >= hwnd_count:
            return
        if not win32gui.IsWindowVisible(h):
            return
        if "VRChat" not in win32gui.GetWindowText(h):
            return
        if win32gui.GetClassName(h) == VRCHAT_WINDOW_CLASS:
            hwnds.insert(0, h)
            found_hwnd += 1
    if found_hwnd >= hwnd_count:
        return hwnds
    win32gui.EnumWindows(cb, None)
    return hwnds

def find_latest_logs(base_dir: Path, count: int = 4) -> list[Path]:
    """ファイル名から時刻を読み取り、最新count件を古い順で返す。
    ファイル名形式: output_log_YYYY-MM-DD_HH-MM-SS.txt
    窓1=最も古いログ、窓N=最も新しいログ の順に割り当てる。"""
    pattern = str(base_dir / "output_log_*.txt")
    files = glob.glob(pattern)

    def log_datetime(path: str) -> str:
        # ファイル名から日時部分を抽出してソートキーにする
        name = os.path.basename(path)
        # output_log_2026-05-09_00-35-09.txt → "2026-05-09_00-35-09"
        try:
            return name.replace("output_log_", "").replace(".txt", "")
        except Exception:
            return ""

    # ファイル名の日時で降順ソート（新しい順）→ 最新count件取得 → 昇順（古い順）に戻す
    sorted_desc = sorted(files, key=log_datetime, reverse=True)
    latest = sorted_desc[:count]
    return [Path(f) for f in sorted(latest, key=log_datetime, reverse=False)]

def resource_path(filename: str) -> Path:
    candidates = [
        Path(__file__).resolve().parent / filename,
        Path(__file__).resolve().parent.parent / filename,
    ]

    compiled = globals().get("__compiled__")
    if compiled is not None:
        candidates.append(Path(compiled.containing_dir) / filename)

    for path in candidates:
        if path.exists():
            return path

    return candidates[0]

# ═══════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════
BG  = "#1e1e2e"
FG  = "#cdd6f4"
ACC = "#89b4fa"
RED = "#f38ba8"
GRN = "#a6e3a1"
SUB = "#313244"
YLW = "#f9e2af"
ORG = "#fab387"


class WindowTab(ttk.Frame):
    def __init__(self, parent, idx: int):
        super().__init__(parent)
        self.idx = idx
        self._hwnd_map: dict[str, int] = {}
        self._build()

    def _build(self):
        p = self

        def section(text):
            ttk.Label(p, text=text, background=BG, foreground=ACC,
                      font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))

        # ── VRChatウィンドウ ──
        section("■ VRChatウィンドウ")
        self.v_hwnd_sel = tk.StringVar(value="未選択")
        hf = ttk.Frame(p)
        hf.pack(fill="x", padx=10, pady=2)
        self.cb_hwnd = ttk.Combobox(hf, textvariable=self.v_hwnd_sel,
                                     state="readonly", width=36)
        self.cb_hwnd.pack(side="left", padx=(0, 4))
        ttk.Button(hf, text="🔄 更新", command=lambda: self._refresh_hwnds(1)).pack(side="left") # refresh_hwndsに1を入力することで、最新でアクティブになった窓を選択する

        # ── ログファイル ──
        section("■ ログファイル")
        self.v_log = tk.StringVar()
        lf = ttk.Frame(p)
        lf.pack(fill="x", padx=10, pady=2)
        ttk.Entry(lf, textvariable=self.v_log, width=80).pack(side="left", padx=(0, 4))
        ttk.Button(lf, text="…", width=3, command=self._browse_log).pack(side="left")

        # ── ON/OFF ──
        ttk.Separator(p, orient="horizontal").pack(fill="x", padx=10, pady=8)
        cf = ttk.Frame(p)
        cf.pack(fill="x", padx=10)
        self.v_active      = tk.BooleanVar(value=True)
        self.v_auto_begin  = tk.BooleanVar(value=True)
        self.v_do_skip     = tk.BooleanVar(value=True)
        self.v_cancel_afk  = tk.BooleanVar(value=True)
        self.v_hoshiimo    = tk.BooleanVar(value=False)
        self.v_announce_intermission = tk.BooleanVar(value=False)
        ttk.Checkbutton(cf, text="この窓を有効化",            variable=self.v_active).pack(side="left")
        ttk.Checkbutton(cf, text="自動Begin",                variable=self.v_auto_begin).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="自動自爆",                 variable=self.v_do_skip).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="DTM/Waldo続行 (3クラまで)", variable=self.v_cancel_afk).pack(side="left", padx=(12, 0))
        cf2 = ttk.Frame(p)
        cf2.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Checkbutton(cf2, text="干し芋自動自爆",           variable=self.v_hoshiimo).pack(side="left")
        ttk.Checkbutton(cf2, text="Intermissionアナウンス",    variable=self.v_announce_intermission).pack(side="left", padx=(12, 0))


    # 最新のアクティブになったデータから取得し、VRChatウィンドウを古い順にhwndが入った配列で返す
    def _refresh_hwnds(self, hwnd_count: int):
        hwnds = get_vrchat_windows(hwnd_count)
        self._hwnd_map = {}
        choices = []
        for i, h in enumerate(hwnds):
            label = f"[{i+1}] HWND={h:#010x}"
            self._hwnd_map[label] = h
            choices.append(label)
        self.cb_hwnd["values"] = choices
        if choices and self.v_hwnd_sel.get() == "未選択":
            self.v_hwnd_sel.set(choices[0])

    def _get_selected_hwnd(self) -> int:
        return self._hwnd_map.get(self.v_hwnd_sel.get(), 0)

    def _browse_log(self):
        p = filedialog.askopenfilename(
            title="VRChatログを選択",
            initialdir=str(VRCHAT_LOG_DIR),
            filetypes=[("Log", "*.txt"), ("All", "*.*")]
        )
        if p:
            self.v_log.set(p)

    def get_config(self) -> "tuple[Optional[LogMonitor.WindowConfig], Optional[str]]":
        if not self.v_active.get():
            return None, None
        log_str = self.v_log.get().strip()
        if not log_str:
            return None, "ログファイルが未設定です"
        log_path = Path(log_str)
        if not log_path.exists():
            return None, f"ログファイルが存在しません: {log_path.name}"
        return LogMonitor.WindowConfig(
            hwnd=self._get_selected_hwnd(),
            log_path=log_path,
            active=True,
            auto_begin=self.v_auto_begin.get(),
            do_skip=self.v_do_skip.get(),
            cancel_afk=self.v_cancel_afk.get(),
            hoshiimo_skip=self.v_hoshiimo.get(),
            announce_intermission=self.v_announce_intermission.get(),
        ), None


# ── ログオーバーレイ ──────────────────────────────
class LogOverlay(tk.Toplevel):
    """半透明の最前面ログオーバーレイウィンドウ"""
    MAX_LINES = 20

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Log Overlay")
        self.overrideredirect(True)      # タイトルバーなし
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.75)
        self.configure(bg="#000000")
        self.geometry("520x320+10+10")
        self._lines: list[str] = []
        self._drag_x = 0
        self._drag_y = 0

        # ヘッダ（ドラッグ用）
        hf = tk.Frame(self, bg="#1e1e2e", cursor="fleur")
        hf.pack(fill="x")
        tk.Label(hf, text="ToNAutoBeginner Log", bg="#1e1e2e", fg="#89b4fa",
                 font=("Consolas", 9, "bold")).pack(side="left", padx=6)
        tk.Button(hf, text="✕", bg="#1e1e2e", fg="#f38ba8",
                  font=("Consolas", 9), relief="flat", bd=0,
                  command=self.close).pack(side="right", padx=4)
        # 透明度スライダー
        tk.Label(hf, text="α:", bg="#1e1e2e", fg="#cdd6f4",
                 font=("Consolas", 8)).pack(side="right")
        self._alpha = tk.DoubleVar(value=0.75)
        tk.Scale(hf, from_=0.2, to=1.0, resolution=0.05,
                 variable=self._alpha, orient="horizontal", length=80,
                 bg="#1e1e2e", fg="#cdd6f4", troughcolor="#313244",
                 highlightthickness=0, bd=0,
                 command=lambda v: self.attributes("-alpha", float(v))
                 ).pack(side="right")
        hf.bind("<ButtonPress-1>",   self._drag_start)
        hf.bind("<B1-Motion>",       self._drag_move)

        # ログテキスト
        self.text = tk.Text(
            self, bg="#000000", fg="#a6e3a1",
            font=("Consolas", 9), state="disabled",
            relief="flat", bd=0, wrap="word",
            insertbackground="#cdd6f4"
        )
        self.text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # リサイズグリップ
        grip = tk.Label(self, text="⠿", bg="#000000", fg="#444444",
                        cursor="size_nw_se", font=("Consolas", 10))
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>",  self._resize_start)
        grip.bind("<B1-Motion>",      self._resize_move)
        self._rw = self._rh = 0

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._closed = False

    def append(self, msg: str):
        if self._closed:
            return
        self._lines.append(msg)
        if len(self._lines) > self.MAX_LINES:
            self._lines.pop(0)
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n".join(self._lines))
        self.text.see("end")
        self.text.config(state="disabled")

    def close(self):
        self._closed = True
        self.destroy()

    def _drag_start(self, e):
        self._drag_x = e.x_root - self.winfo_x()
        self._drag_y = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    def _resize_start(self, e):
        self._rw = self.winfo_width()
        self._rh = self.winfo_height()
        self._drag_x = e.x_root
        self._drag_y = e.y_root

    def _resize_move(self, e):
        nw = max(300, self._rw + e.x_root - self._drag_x)
        nh = max(120, self._rh + e.y_root - self._drag_y)
        self.geometry(f"{nw}x{nh}")


# ── 折りたたみ可能フレーム ──────────────────────
class CollapsibleFrame(tk.Frame):
    """クリックで折りたたみ可能なLabelFrame風ウィジェット"""
    def __init__(self, parent, text: str, collapsed: bool = False, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self._collapsed = collapsed
        # ヘッダ行
        hf = tk.Frame(self, bg=BG)
        hf.pack(fill="x")
        self._toggle_btn = tk.Button(
            hf, text="▶" if collapsed else "▼",
            bg=BG, fg=ACC, font=("Segoe UI", 9),
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle)
        self._toggle_btn.pack(side="left")
        tk.Label(hf, text=text, bg=BG, fg=ACC,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=4)
        # 区切り線
        tk.Frame(hf, bg=SUB, height=1).pack(side="left", fill="x", expand=True, pady=6)
        # コンテンツ領域
        self.content = tk.Frame(self, bg=BG)
        if not collapsed:
            self.content.pack(fill="x", padx=8, pady=(0, 4))

    def _toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.content.pack_forget()
            self._toggle_btn.config(text="▶")
        else:
            self.content.pack(fill="x", padx=8, pady=(0, 4))
            self._toggle_btn.config(text="▼")


# ── メインウィンドウ ──────────────────────────
class LegacyStatisticsWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("ToN Statistics")
        self.geometry("1040x720")
        self.configure(bg=BG)
        self.rows: list[dict] = []
        self.filtered_rows: list[dict] = []
        self.v_start = tk.StringVar()
        self.v_end = tk.StringVar()
        self.v_status = tk.StringVar(value="統計データ未読み込み")
        self._loaded_deferred_rounds: set[str] = set()
        self._loading_deferred_rounds: set[str] = set()
        self._build_ui()
        self._load_rows_async()

    def _build_ui(self):
        controls = ttk.LabelFrame(self, text="集計条件", padding=8)
        controls.pack(fill="x", padx=12, pady=(12, 6))

        period = ttk.Frame(controls)
        period.pack(fill="x", pady=(0, 6))
        ttk.Label(period, text="開始日時").pack(side="left")
        ttk.Entry(period, textvariable=self.v_start, width=18).pack(side="left", padx=(4, 10))
        ttk.Label(period, text="終了日時").pack(side="left")
        ttk.Entry(period, textvariable=self.v_end, width=18).pack(side="left", padx=(4, 10))
        ttk.Button(period, text="再読み込み", command=self._load_rows_async).pack(side="left", padx=(0, 6))
        ttk.Button(period, text="集計", command=self._analyze).pack(side="left")
        ttk.Label(period, textvariable=self.v_status, foreground=YLW).pack(side="left", padx=(12, 0))

        rounds = ttk.Frame(controls)
        rounds.pack(fill="x")
        list_frame = ttk.Frame(rounds)
        list_frame.pack(side="left", fill="both", expand=True)
        ttk.Label(list_frame, text="ラウンド（複数選択）").pack(anchor="w")
        self.round_list = tk.Listbox(
            list_frame, selectmode="multiple", height=7,
            bg="#181825", fg=FG, selectbackground=ACC,
            selectforeground=BG, exportselection=False
        )
        self.round_list.pack(side="left", fill="both", expand=True)
        round_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.round_list.yview)
        round_scroll.pack(side="left", fill="y")
        self.round_list.configure(yscrollcommand=round_scroll.set)

        round_buttons = ttk.Frame(rounds)
        round_buttons.pack(side="left", padx=(10, 0), anchor="n")
        ttk.Button(round_buttons, text="全選択", command=self._select_all_rounds).pack(fill="x", pady=(18, 4))
        ttk.Button(round_buttons, text="全解除", command=self._clear_round_selection).pack(fill="x")

        self.round_stats_frame = ttk.LabelFrame(self, text="ラウンド統計", padding=8)
        self.round_tree = ttk.Treeview(
            self.round_stats_frame, columns=("round", "count", "slots"), show="headings", height=5
        )
        self.round_tree.heading("round", text="ラウンド")
        self.round_tree.heading("count", text="回数")
        self.round_tree.heading("slots", text="テラー枠")
        self.round_tree.column("round", width=260)
        self.round_tree.column("count", width=80, anchor="e")
        self.round_tree.column("slots", width=80, anchor="e")
        self.round_tree.pack(fill="x")

        self.stats_grid = ttk.Frame(self)
        self.stats_grid.pack(fill="both", expand=True, padx=12, pady=6)
        self.stats_grid.columnconfigure(0, weight=3)
        self.stats_grid.columnconfigure(1, weight=2)
        self.stats_grid.rowconfigure(0, weight=1)

        terror_frame = ttk.LabelFrame(self.stats_grid, text="テラー出現回数・仮説検定", padding=8)
        terror_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.terror_tree = ttk.Treeview(
            terror_frame,
            columns=("id", "name", "count", "expected", "p_value", "label"),
            show="headings",
        )
        for col, text, width, anchor in (
            ("id", "ID", 52, "e"),
            ("name", "テラー", 220, "w"),
            ("count", "回数", 72, "e"),
            ("expected", "期待", 72, "e"),
            ("p_value", "上側p値", 96, "e"),
            ("label", "判定", 120, "w"),
        ):
            self.terror_tree.heading(col, text=text)
            self.terror_tree.column(col, width=width, anchor=anchor)
        terror_scroll = ttk.Scrollbar(terror_frame, orient="vertical", command=self.terror_tree.yview)
        self.terror_tree.configure(yscrollcommand=terror_scroll.set)
        self.terror_tree.pack(side="left", fill="both", expand=True)
        terror_scroll.pack(side="left", fill="y")
        self.terror_tree.bind("<<TreeviewSelect>>", self._on_terror_selected)

        map_frame = ttk.LabelFrame(self.stats_grid, text="選択テラーのマップ一覧", padding=8)
        map_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.map_tree = ttk.Treeview(map_frame, columns=("map", "count"), show="headings")
        self.map_tree.heading("map", text="マップ")
        self.map_tree.heading("count", text="回数")
        self.map_tree.column("map", width=220)
        self.map_tree.column("count", width=80, anchor="e")
        map_scroll = ttk.Scrollbar(map_frame, orient="vertical", command=self.map_tree.yview)
        self.map_tree.configure(yscrollcommand=map_scroll.set)
        self.map_tree.pack(side="left", fill="both", expand=True)
        map_scroll.pack(side="left", fill="y")

        ttk.Label(
            self,
            text="判定: 出やすい a=0.05 / かなり出やすい a=0.025 / テーブル！ a=0.001",
            foreground=YLW,
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _load_rows_async(self):
        self.v_status.set("統計データ取得中...")
        self._clear_tree(self.round_tree)
        self._clear_tree(self.terror_tree)
        self._clear_tree(self.map_tree)
        threading.Thread(target=self._load_rows_worker, daemon=True).start()

    def _load_rows_worker(self):
        try:
            rows = ConnectDB.get_ToNRoundStatistics()
            self.after(0, lambda: self._on_rows_loaded(rows, None))
        except Exception as e:
            self.after(0, lambda error=e: self._on_rows_loaded([], error))

    def _on_rows_loaded(self, rows: list[dict], error: Exception | None):
        if error:
            self.v_status.set(f"取得エラー: {error}")
            return
        self.rows = rows
        self._populate_rounds()
        dates = sorted(dt for dt in (Statistics.row_datetime(row) for row in rows) if dt is not None)
        if dates:
            self.v_start.set(Statistics.format_datetime(dates[0]))
            self.v_end.set(Statistics.format_datetime(dates[-1]))
        self.v_status.set(f"{len(rows)}件取得")
        self._analyze()

    def _populate_rounds(self):
        for child in self.round_chip_frame.winfo_children():
            child.destroy()
        self.round_vars.clear()
        rounds = Statistics.available_rounds(self.rows)
        for col in range(ROUND_CHIP_COLUMNS):
            self.round_chip_frame.grid_columnconfigure(
                col,
                minsize=ROUND_CHIP_MIN_WIDTH,
                weight=1,
                uniform="round_chip",
            )

        row = 0
        col = 0
        for display_name, round_name in _ordered_round_entries(rounds):
            if round_name is None:
                if col:
                    row += 1
                    col = 0
                separator = tk.Frame(self.round_chip_frame, bg=ACC, height=1)
                separator.grid(
                    row=row,
                    column=0,
                    columnspan=ROUND_CHIP_COLUMNS,
                    sticky="ew",
                    padx=4,
                    pady=(6, 5),
                )
                row += 1
                continue

            var = tk.BooleanVar(value=round_name not in DEFAULT_EXCLUDED_ROUNDS)
            self.round_vars[round_name] = var
            chip = tk.Checkbutton(
                self.round_chip_frame,
                text=display_name,
                variable=var,
                indicatoron=False,
                command=self._style_round_chips,
                bg=SUB,
                fg=FG,
                selectcolor=ACC,
                activebackground=ACC,
                activeforeground=BG,
                font=("Segoe UI", 9, "bold"),
                relief="raised",
                bd=1,
                width=ROUND_CHIP_WIDTH,
                padx=0,
                pady=4,
                anchor="center",
            )
            chip.round_name = round_name
            chip.grid(row=row, column=col, padx=4, pady=4, sticky="ew")

            col += 1
            if col >= ROUND_CHIP_COLUMNS:
                row += 1
                col = 0
        self._style_round_chips()

    def _select_all_rounds(self):
        for var in self.round_vars.values():
            var.set(True)
        self._style_round_chips()

    def _clear_round_selection(self):
        for var in self.round_vars.values():
            var.set(False)
        self._style_round_chips()

    def _selected_rounds(self) -> set[str]:
        return {round_name for round_name, var in self.round_vars.items() if var.get()}

    def _ensure_deferred_rounds_loaded(self, rounds: set[str]) -> bool:
        missing = sorted(
            (rounds & DEFAULT_EXCLUDED_ROUNDS)
            - self._loaded_deferred_rounds
            - self._loading_deferred_rounds
        )
        if not missing:
            return True

        self._loading_deferred_rounds.update(missing)
        self.v_status.set(f"{', '.join(missing)} を追加ロード中...")
        threading.Thread(target=self._load_deferred_rounds_worker, args=(missing,), daemon=True).start()
        return False

    def _load_deferred_rounds_worker(self, rounds: list[str]):
        try:
            rows = ConnectDB.get_ToNRoundStatistics(exclude_rounds=None, include_rounds=rounds)
            self.after(0, lambda: self._on_deferred_rounds_loaded(rounds, rows, None))
        except Exception as e:
            self.after(0, lambda error=e: self._on_deferred_rounds_loaded(rounds, [], error))

    def _on_deferred_rounds_loaded(self, rounds: list[str], rows: list[dict], error: Exception | None):
        self._loading_deferred_rounds.difference_update(rounds)
        if error:
            self.v_status.set(f"{', '.join(rounds)} の追加ロードに失敗: {error}")
            return

        self._loaded_deferred_rounds.update(rounds)
        existing_keys = {
            (
                row.get("created_at"),
                row.get("round"),
                row.get("map_id"),
                tuple(row.get("terror_ids") or []),
                row.get("transformed_uid"),
            )
            for row in self.rows
        }
        for row in rows:
            key = (
                row.get("created_at"),
                row.get("round"),
                row.get("map_id"),
                tuple(row.get("terror_ids") or []),
                row.get("transformed_uid"),
            )
            if key not in existing_keys:
                self.rows.append(row)
                existing_keys.add(key)
        self.v_status.set(f"{', '.join(rounds)} を{len(rows)}件追加ロード")
        self._analyze()

    def _style_round_chips(self):
        for child in self.round_chip_frame.winfo_children():
            if not isinstance(child, tk.Checkbutton):
                continue
            round_name = getattr(child, "round_name", child.cget("text"))
            var = self.round_vars.get(round_name)
            selected = bool(var and var.get())
            child.configure(
                bg=ACC if selected else SUB,
                fg=BG if selected else FG,
                relief="sunken" if selected else "raised",
            )

    def _set_terror_category(self, category: str):
        self.v_terror_category.set(category)
        self._refresh_category_buttons()
        self._analyze()

    def _refresh_category_buttons(self):
        active = self.v_terror_category.get()
        for category, btn in self._category_buttons.items():
            selected = category == active
            btn.configure(
                bg=ACC if selected else SUB,
                fg=BG if selected else FG,
                relief="sunken" if selected else "raised",
            )

    def _analyze(self):
        try:
            start_at = Statistics.parse_datetime(self.v_start.get())
            end_at = Statistics.parse_datetime(self.v_end.get())
        except ValueError as e:
            messagebox.showerror("日時エラー", str(e), parent=self)
            return

        rounds = self._selected_rounds()
        if not rounds:
            self.filtered_rows = []
            self._clear_tree(self.round_tree)
            self._clear_tree(self.terror_tree)
            self._clear_tree(self.map_tree)
            self._hide_round_stats()
            self.v_status.set("ラウンドを選択してください")
            return

        self.filtered_rows = Statistics.filter_rows(self.rows, start_at, end_at, rounds)
        round_rows = Statistics.round_summary(self.filtered_rows)
        if len(rounds) > 1 and len(round_rows) > 1:
            self._show_round_stats(round_rows)
        else:
            self._hide_round_stats()

        candidate_ids = Statistics.candidate_ids_for_category(self.v_terror_category.get(), config.TERRORS)
        total_slots, candidate_count, terror_rows = Statistics.analyze_terrors(
            self.filtered_rows, config.TERRORS, candidate_ids
        )
        self._render_terror_stats(terror_rows)
        self._clear_tree(self.map_tree)
        self.v_status.set(
            f"{len(self.filtered_rows)}ラウンド / {total_slots}枠 / 候補{candidate_count}体"
        )

    def _show_round_stats(self, rows: list[tuple[str, int, int]]):
        self._clear_tree(self.round_tree)
        for round_name, count, slots in rows:
            self.round_tree.insert("", "end", values=(round_name, count, slots))
        self.round_stats_frame.pack(fill="x", padx=12, pady=6, before=self.stats_grid)

    def _hide_round_stats(self):
        self.round_stats_frame.pack_forget()

    def _render_terror_stats(self, rows: list[Statistics.TerrorStatistic]):
        self._clear_tree(self.terror_tree)
        for row in rows:
            self.terror_tree.insert(
                "",
                "end",
                iid=str(row.terror_id),
                values=(
                    row.terror_id,
                    row.name,
                    row.count,
                    f"{row.expected:.2f}",
                    self._format_p_value(row.p_value),
                    row.label,
                ),
            )

    def _on_terror_selected(self, _event=None):
        selection = self.terror_tree.selection()
        if not selection:
            return
        try:
            terror_id = int(selection[0])
        except ValueError:
            return
        self._clear_tree(self.map_tree)
        for map_name, count in Statistics.map_counts_for_terror(self.filtered_rows, terror_id):
            self.map_tree.insert("", "end", values=(map_name, count))

    def _clear_tree(self, tree: ttk.Treeview):
        items = tree.get_children()
        if items:
            tree.delete(*items)

    def _format_p_value(self, value: float) -> str:
        if value == 0.0:
            return "0"
        if value < 0.0001:
            return f"{value:.2e}"
        return f"{value:.6f}"


ROUND_CHIP_COLUMNS = 5
ROUND_CHIP_WIDTH = 21
ROUND_CHIP_MIN_WIDTH = 158
DEFAULT_EXCLUDED_ROUNDS = {"Classic", "Run"}
ROUND_CHART_COLORS = (
    "#89b4fa",
    "#a6e3a1",
    "#f9e2af",
    "#f38ba8",
    "#cba6f7",
    "#94e2d5",
    "#fab387",
    "#74c7ec",
    "#eba0ac",
    "#b4befe",
    "#f5c2e7",
    "#a6adc8",
)
ROUND_ORDER_GROUPS = (
    (
        ("Classic", ("Classic",)),
        ("8 Pages", ("8 Pages",)),
        ("Fog", ("Fog",)),
        ("Fog(Alternate)", ("Fog (Alternate)", "Fog(Alternate)", "Fog Alternate")),
        ("Ghost", ("Ghost",)),
        ("Ghost(Alternate)", ("Ghost (Alternate)", "Ghost(Alternate)", "Ghost Alternate")),
        ("Punish", ("Punished", "Punish")),
        ("Sabotage", ("Sabotage",)),
        ("Bloodbath", ("Bloodbath",)),
        ("Double Trouble", ("Double Trouble",)),
        ("Bloodbath EX", ("Bloodbath EX",)),
        ("Cracked", ("Cracked",)),
        ("Alternate", ("Alternate",)),
        ("Midnight", ("Midnight",)),
        ("Unbound", ("Unbound",)),
        ("Run", ("Run",)),
    ),
    (
        ("Mystic Moon", ("Mystic Moon",)),
        ("Blood Moon", ("Blood Moon",)),
        ("Twilight", ("Twilight",)),
        ("Solstice", ("Solstice",)),
        ("Randomizer", ("Randomizer",)),
        ("Classic.exe", ("Classic.exe",)),
    ),
)


def _ordered_round_entries(rounds: list[str]) -> list[tuple[str | None, str | None]]:
    available = {str(round_name).strip() for round_name in rounds if str(round_name).strip()}
    available.update(DEFAULT_EXCLUDED_ROUNDS)
    used: set[str] = set()
    entries: list[tuple[str | None, str | None]] = []

    for group_index, group in enumerate(ROUND_ORDER_GROUPS):
        group_entries: list[tuple[str, str]] = []
        for label, aliases in group:
            actual_name = next((name for name in aliases if name in available), None)
            if actual_name is None:
                continue
            group_entries.append((label, actual_name))
            used.add(actual_name)

        if group_index > 0 and group_entries and entries:
            entries.append((None, None))
        entries.extend(group_entries)

    entries.extend((round_name, round_name) for round_name in sorted(available - used))
    return entries


def _round_display_name(round_name: str) -> str:
    normalized = str(round_name).strip()
    for group in ROUND_ORDER_GROUPS:
        for label, aliases in group:
            if normalized == label or normalized in aliases:
                return label
    return normalized


def _round_order_key(round_name: str) -> tuple[int, int, str]:
    normalized = str(round_name).strip()
    for group_index, group in enumerate(ROUND_ORDER_GROUPS):
        for round_index, (label, aliases) in enumerate(group):
            if normalized == label or normalized in aliases:
                return (group_index, round_index, label)
    return (len(ROUND_ORDER_GROUPS), 0, normalized)


class StatisticsWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("ToN Statistics")
        self.geometry("1180x880")
        self.minsize(980, 760)
        self.configure(bg=BG)
        self.rows: list[dict] = []
        self.filtered_rows: list[dict] = []
        self._dt_vars: dict[str, dict[str, tk.IntVar]] = {}
        self.round_vars: dict[str, tk.BooleanVar] = {}
        self.v_terror_category = tk.StringVar(value="unbound")
        self._category_buttons: dict[str, tk.Button] = {}
        self._round_stats_visible = False
        self._round_chart_rows: list[tuple[str, int, int]] = []
        self._round_chart_job: str | None = None
        self._loaded_deferred_rounds: set[str] = set()
        self._loading_deferred_rounds: set[str] = set()
        self.v_status = tk.StringVar(value="統計データ未読み込み")
        self._build_ui()
        self._load_rows_async()

    def _build_ui(self):
        controls = ttk.LabelFrame(self, text="集計条件", padding=8)
        controls.pack(fill="x", padx=12, pady=(12, 6))

        period = ttk.Frame(controls)
        period.pack(fill="x", pady=(0, 8))
        self._make_datetime_picker(period, "開始", "start").pack(side="left", padx=(0, 18))
        self._make_datetime_picker(period, "終了", "end").pack(side="left", padx=(0, 18))
        ttk.Button(period, text="再読み込み", command=self._load_rows_async).pack(side="left", padx=(0, 6))
        ttk.Button(period, text="集計", command=self._analyze).pack(side="left")
        ttk.Label(period, textvariable=self.v_status, foreground=YLW).pack(side="left", padx=(12, 0))

        rounds = ttk.Frame(controls)
        rounds.pack(fill="x")
        list_frame = ttk.Frame(rounds)
        list_frame.pack(side="left", fill="both", expand=True)
        ttk.Label(list_frame, text="ラウンド（複数選択）").pack(anchor="w")
        self.round_chip_frame = tk.Frame(list_frame, bg=BG)
        self.round_chip_frame.pack(fill="x", pady=(4, 0))

        round_buttons = ttk.Frame(rounds)
        round_buttons.pack(side="left", padx=(10, 0), anchor="n")
        ttk.Button(round_buttons, text="全選択", command=self._select_all_rounds).pack(fill="x", pady=(18, 4))
        ttk.Button(round_buttons, text="全解除", command=self._clear_round_selection).pack(fill="x")

        self.main_pane = ttk.PanedWindow(self, orient="vertical")
        self.main_pane.pack(fill="both", expand=True, padx=12, pady=6)

        self.round_stats_frame = ttk.LabelFrame(self.main_pane, text="ラウンド統計", padding=8)
        round_chart_body = ttk.Frame(self.round_stats_frame)
        round_chart_body.pack(fill="both", expand=True)
        round_chart_body.grid_columnconfigure(0, minsize=360, weight=3)
        round_chart_body.grid_columnconfigure(1, minsize=440, weight=2)
        round_chart_body.grid_columnconfigure(2, weight=0)
        round_chart_body.grid_rowconfigure(0, weight=1)
        self.round_chart = tk.Canvas(
            round_chart_body,
            bg=BG,
            height=260,
            highlightthickness=0,
            bd=0,
        )
        self.round_chart.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.round_chart.bind("<Configure>", self._schedule_round_chart_draw)
        self.round_legend = ttk.Treeview(
            round_chart_body,
            columns=("mark", "round", "count", "percent"),
            show="headings",
            height=8,
        )
        self.round_legend.heading("mark", text="")
        self.round_legend.heading("round", text="ラウンド")
        self.round_legend.heading("count", text="回数")
        self.round_legend.heading("percent", text="%")
        self.round_legend.column("mark", width=34, minwidth=34, anchor="center", stretch=False)
        self.round_legend.column("round", width=260, minwidth=180, stretch=True)
        self.round_legend.column("count", width=82, minwidth=70, anchor="e", stretch=False)
        self.round_legend.column("percent", width=82, minwidth=70, anchor="e", stretch=False)
        round_legend_scroll = ttk.Scrollbar(round_chart_body, orient="vertical", command=self.round_legend.yview)
        self.round_legend.configure(yscrollcommand=round_legend_scroll.set)
        self.round_legend.grid(row=0, column=1, sticky="nsew")
        round_legend_scroll.grid(row=0, column=2, sticky="ns")

        self.results_pane = ttk.PanedWindow(self.main_pane, orient="horizontal")
        self.main_pane.add(self.results_pane, weight=4)

        terror_frame = ttk.LabelFrame(self.results_pane, text="テラー出現回数", padding=8)
        self.results_pane.add(terror_frame, weight=3)
        category_bar = tk.Frame(terror_frame, bg=BG)
        category_bar.pack(fill="x", pady=(0, 8))
        for label, value in (("Classic", "classic"), ("Alternate", "alternate"), ("Unbound", "unbound")):
            btn = tk.Button(
                category_bar,
                text=label,
                command=lambda v=value: self._set_terror_category(v),
                padx=14,
                pady=3,
                bd=1,
                relief="raised",
                bg=SUB,
                fg=FG,
                activebackground=ACC,
                activeforeground=BG,
                font=("Segoe UI", 9, "bold"),
            )
            btn.pack(side="left", padx=(0, 6))
            self._category_buttons[value] = btn
        self._refresh_category_buttons()
        self.terror_tree = ttk.Treeview(
            terror_frame,
            columns=("id", "name", "count", "expected", "p_value", "label"),
            show="headings",
        )
        for col, text, width, anchor in (
            ("id", "ID", 58, "e"),
            ("name", "テラー", 260, "w"),
            ("count", "回数", 86, "e"),
            ("expected", "期待", 86, "e"),
            ("p_value", "上側p値", 110, "e"),
            ("label", "判定", 130, "w"),
        ):
            self.terror_tree.heading(col, text=text)
            self.terror_tree.column(col, width=width, minwidth=48, anchor=anchor, stretch=(col == "name"))
        terror_scroll = ttk.Scrollbar(terror_frame, orient="vertical", command=self.terror_tree.yview)
        self.terror_tree.configure(yscrollcommand=terror_scroll.set)
        self.terror_tree.pack(side="left", fill="both", expand=True)
        terror_scroll.pack(side="left", fill="y")
        self.terror_tree.bind("<<TreeviewSelect>>", self._on_terror_selected)

        map_frame = ttk.LabelFrame(self.results_pane, text="選択テラーのマップ一覧", padding=8)
        self.results_pane.add(map_frame, weight=2)
        self.map_tree = ttk.Treeview(map_frame, columns=("map", "count"), show="headings")
        self.map_tree.heading("map", text="マップ")
        self.map_tree.heading("count", text="回数")
        self.map_tree.column("map", width=260, minwidth=120, stretch=True)
        self.map_tree.column("count", width=90, minwidth=60, anchor="e", stretch=False)
        map_scroll = ttk.Scrollbar(map_frame, orient="vertical", command=self.map_tree.yview)
        self.map_tree.configure(yscrollcommand=map_scroll.set)
        self.map_tree.pack(side="left", fill="both", expand=True)
        map_scroll.pack(side="left", fill="y")

        ttk.Label(
            self,
            text="判定: 出やすい a=0.05 / かなり出やすい a=0.025 / テーブル！ a=0.001",
            foreground=YLW,
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _make_datetime_picker(self, parent, label: str, key: str) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Label(frame, text=label).pack(side="left", padx=(0, 4))
        vars_ = {
            "year": tk.IntVar(value=2026),
            "month": tk.IntVar(value=1),
            "day": tk.IntVar(value=1),
            "hour": tk.IntVar(value=0),
        }
        self._dt_vars[key] = vars_
        self._spin(frame, vars_["year"], 2000, 2100, 6).pack(side="left")
        ttk.Label(frame, text="年").pack(side="left")
        self._spin(frame, vars_["month"], 1, 12, 3).pack(side="left")
        ttk.Label(frame, text="月").pack(side="left")
        self._spin(frame, vars_["day"], 1, 31, 3).pack(side="left")
        ttk.Label(frame, text="日").pack(side="left", padx=(0, 6))
        self._spin(frame, vars_["hour"], 0, 23, 3).pack(side="left")
        ttk.Label(frame, text="時").pack(side="left")
        return frame

    def _spin(self, parent, variable: tk.IntVar, from_: int, to: int, width: int) -> ttk.Spinbox:
        return ttk.Spinbox(parent, from_=from_, to=to, textvariable=variable, width=width, wrap=True)

    def _set_picker_datetime(self, key: str, dt: datetime):
        vars_ = self._dt_vars[key]
        vars_["year"].set(dt.year)
        vars_["month"].set(dt.month)
        vars_["day"].set(dt.day)
        vars_["hour"].set(dt.hour)

    def _picker_datetime(self, key: str) -> datetime:
        vars_ = self._dt_vars[key]
        year = int(vars_["year"].get())
        month = max(1, min(12, int(vars_["month"].get())))
        last_day = calendar.monthrange(year, month)[1]
        day = max(1, min(last_day, int(vars_["day"].get())))
        hour = max(0, min(23, int(vars_["hour"].get())))
        vars_["month"].set(month)
        vars_["day"].set(day)
        vars_["hour"].set(hour)
        if key == "end":
            return datetime(year, month, day, hour, 59, 59)
        return datetime(year, month, day, hour, 0, 0)

    def _load_rows_async(self):
        self.v_status.set("統計データ取得中...")
        self._loaded_deferred_rounds.clear()
        self._loading_deferred_rounds.clear()
        self._clear_round_stats()
        self._clear_tree(self.terror_tree)
        self._clear_tree(self.map_tree)
        threading.Thread(target=self._load_rows_worker, daemon=True).start()

    def _load_rows_worker(self):
        try:
            rows = ConnectDB.get_ToNRoundStatistics()
            self.after(0, lambda: self._on_rows_loaded(rows, None))
        except Exception as e:
            self.after(0, lambda error=e: self._on_rows_loaded([], error))

    def _on_rows_loaded(self, rows: list[dict], error: Exception | None):
        if error:
            self.v_status.set(f"取得エラー: {error}")
            return
        self.rows = rows
        self._populate_rounds()
        dates = sorted(dt for dt in (Statistics.row_datetime(row) for row in rows) if dt is not None)
        if dates:
            self._set_picker_datetime("start", dates[0])
            self._set_picker_datetime("end", dates[-1])
        self.v_status.set(f"{len(rows)}件取得")
        self._analyze()

    def _populate_rounds(self):
        for child in self.round_chip_frame.winfo_children():
            child.destroy()
        self.round_vars.clear()
        rounds = Statistics.available_rounds(self.rows)
        for col in range(ROUND_CHIP_COLUMNS):
            self.round_chip_frame.grid_columnconfigure(
                col,
                minsize=ROUND_CHIP_MIN_WIDTH,
                weight=1,
                uniform="round_chip",
            )

        row = 0
        col = 0
        for display_name, round_name in _ordered_round_entries(rounds):
            if round_name is None:
                if col:
                    row += 1
                    col = 0
                separator = tk.Frame(self.round_chip_frame, bg=ACC, height=1)
                separator.grid(
                    row=row,
                    column=0,
                    columnspan=ROUND_CHIP_COLUMNS,
                    sticky="ew",
                    padx=4,
                    pady=(6, 5),
                )
                row += 1
                continue

            var = tk.BooleanVar(value=round_name not in DEFAULT_EXCLUDED_ROUNDS)
            self.round_vars[round_name] = var
            chip = tk.Checkbutton(
                self.round_chip_frame,
                text=display_name,
                variable=var,
                indicatoron=False,
                command=self._style_round_chips,
                bg=SUB,
                fg=FG,
                selectcolor=ACC,
                activebackground=ACC,
                activeforeground=BG,
                font=("Segoe UI", 9, "bold"),
                relief="raised",
                bd=1,
                width=ROUND_CHIP_WIDTH,
                padx=0,
                pady=4,
                anchor="center",
            )
            chip.round_name = round_name
            chip.grid(row=row, column=col, padx=4, pady=4, sticky="ew")

            col += 1
            if col >= ROUND_CHIP_COLUMNS:
                row += 1
                col = 0
        self._style_round_chips()

    def _select_all_rounds(self):
        for var in self.round_vars.values():
            var.set(True)
        self._style_round_chips()

    def _clear_round_selection(self):
        for var in self.round_vars.values():
            var.set(False)
        self._style_round_chips()

    def _selected_rounds(self) -> set[str]:
        return {round_name for round_name, var in self.round_vars.items() if var.get()}

    def _ensure_deferred_rounds_loaded(self, rounds: set[str]) -> bool:
        missing = sorted(
            (rounds & DEFAULT_EXCLUDED_ROUNDS)
            - self._loaded_deferred_rounds
            - self._loading_deferred_rounds
        )
        if not missing:
            return True

        self._loading_deferred_rounds.update(missing)
        self.v_status.set(f"{', '.join(missing)} を追加ロード中...")
        threading.Thread(target=self._load_deferred_rounds_worker, args=(missing,), daemon=True).start()
        return False

    def _load_deferred_rounds_worker(self, rounds: list[str]):
        try:
            rows = ConnectDB.get_ToNRoundStatistics(exclude_rounds=None, include_rounds=rounds)
            self.after(0, lambda: self._on_deferred_rounds_loaded(rounds, rows, None))
        except Exception as e:
            self.after(0, lambda error=e: self._on_deferred_rounds_loaded(rounds, [], error))

    def _on_deferred_rounds_loaded(self, rounds: list[str], rows: list[dict], error: Exception | None):
        self._loading_deferred_rounds.difference_update(rounds)
        if error:
            self.v_status.set(f"{', '.join(rounds)} の追加ロードに失敗: {error}")
            return

        self._loaded_deferred_rounds.update(rounds)
        existing_keys = {
            (
                row.get("created_at"),
                row.get("round"),
                row.get("map_id"),
                tuple(row.get("terror_ids") or []),
                row.get("transformed_uid"),
            )
            for row in self.rows
        }
        for row in rows:
            key = (
                row.get("created_at"),
                row.get("round"),
                row.get("map_id"),
                tuple(row.get("terror_ids") or []),
                row.get("transformed_uid"),
            )
            if key not in existing_keys:
                self.rows.append(row)
                existing_keys.add(key)
        self.v_status.set(f"{', '.join(rounds)} を{len(rows)}件追加ロード")
        self._analyze()

    def _style_round_chips(self):
        for child in self.round_chip_frame.winfo_children():
            if not isinstance(child, tk.Checkbutton):
                continue
            round_name = getattr(child, "round_name", child.cget("text"))
            var = self.round_vars.get(round_name)
            selected = bool(var and var.get())
            child.configure(
                bg=ACC if selected else SUB,
                fg=BG if selected else FG,
                relief="sunken" if selected else "raised",
            )

    def _set_terror_category(self, category: str):
        self.v_terror_category.set(category)
        self._refresh_category_buttons()
        self._analyze()

    def _refresh_category_buttons(self):
        active = self.v_terror_category.get()
        for category, btn in self._category_buttons.items():
            selected = category == active
            btn.configure(
                bg=ACC if selected else SUB,
                fg=BG if selected else FG,
                relief="sunken" if selected else "raised",
            )

    def _analyze(self):
        try:
            start_at = self._picker_datetime("start")
            end_at = self._picker_datetime("end")
        except (ValueError, tk.TclError) as e:
            messagebox.showerror("日時エラー", f"日時を確認してください: {e}", parent=self)
            return

        if start_at > end_at:
            messagebox.showerror("日時エラー", "開始日時は終了日時以前にしてください。", parent=self)
            return

        rounds = self._selected_rounds()
        if not rounds:
            self.filtered_rows = []
            self._clear_round_stats()
            self._clear_tree(self.terror_tree)
            self._clear_tree(self.map_tree)
            self._hide_round_stats()
            self.v_status.set("ラウンドを選択してください")
            return
        if not self._ensure_deferred_rounds_loaded(rounds):
            return

        self.filtered_rows = Statistics.filter_rows(self.rows, start_at, end_at, rounds)
        round_rows = Statistics.round_summary(self.filtered_rows)
        if len(rounds) > 1 and len(round_rows) > 1:
            self._show_round_stats(round_rows)
        else:
            self._hide_round_stats()

        candidate_ids = Statistics.candidate_ids_for_category(self.v_terror_category.get(), config.TERRORS)
        total_slots, candidate_count, terror_rows = Statistics.analyze_terrors(
            self.filtered_rows, config.TERRORS, candidate_ids
        )
        self._render_terror_stats(terror_rows)
        self._clear_tree(self.map_tree)
        self.v_status.set(
            f"{len(self.filtered_rows)}ラウンド / {total_slots}枠 / 候補{candidate_count}体"
        )

    def _clear_round_stats(self):
        self._round_chart_rows = []
        if self._round_chart_job is not None:
            try:
                self.after_cancel(self._round_chart_job)
            except tk.TclError:
                pass
            self._round_chart_job = None
        if hasattr(self, "round_legend"):
            self._clear_tree(self.round_legend)
        if hasattr(self, "round_chart"):
            self.round_chart.delete("all")

    def _render_round_legend(self, rows: list[tuple[str, int, int]]):
        self._clear_tree(self.round_legend)
        total = sum(count for _, count, _ in rows)
        if total <= 0:
            return
        for index, (round_name, count, _slots) in enumerate(rows):
            color = ROUND_CHART_COLORS[index % len(ROUND_CHART_COLORS)]
            tag = f"round_color_{index}"
            self.round_legend.tag_configure(tag, foreground=color)
            percent = count / total * 100
            self.round_legend.insert(
                "",
                "end",
                values=("■", _round_display_name(round_name), count, f"{percent:.1f}"),
                tags=(tag,),
            )

    def _schedule_round_chart_draw(self, _event=None):
        if self._round_chart_job is not None:
            try:
                self.after_cancel(self._round_chart_job)
            except tk.TclError:
                pass
        self._round_chart_job = self.after(70, self._draw_round_chart)

    def _draw_round_slice(
        self,
        canvas: tk.Canvas,
        cx: float,
        cy: float,
        radius: float,
        start: float,
        extent: float,
        color: str,
    ):
        steps = max(4, int(extent / 4))
        points: list[float] = [cx, cy]
        for step in range(steps + 1):
            angle = math.radians(start - extent * step / steps)
            points.extend((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
        canvas.create_polygon(points, fill=color, outline=BG, width=1)

    def _draw_round_chart(self):
        self._round_chart_job = None
        canvas = self.round_chart
        canvas.delete("all")
        rows = self._round_chart_rows
        total = sum(count for _, count, _ in rows)
        if total <= 0:
            return

        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        size = min(width, height) - 18
        if size <= 20:
            return
        radius = size / 2
        cx = width / 2
        cy = height / 2

        start = 90.0
        for index, (_round_name, count, _slots) in enumerate(rows):
            extent = count / total * 360
            color = ROUND_CHART_COLORS[index % len(ROUND_CHART_COLORS)]
            self._draw_round_slice(canvas, cx, cy, radius, start, extent, color)
            start -= extent

        inner = size * 0.38
        canvas.create_oval(
            cx - inner / 2,
            cy - inner / 2,
            cx + inner / 2,
            cy + inner / 2,
            fill=BG,
            outline=BG,
        )
        canvas.create_text(cx, cy - 8, text=str(total), fill=FG, font=("Segoe UI", 18, "bold"))
        canvas.create_text(cx, cy + 14, text="rounds", fill=YLW, font=("Segoe UI", 9))

    def _show_round_stats(self, rows: list[tuple[str, int, int]]):
        ordered_rows = sorted(rows, key=lambda row: _round_order_key(row[0]))
        self._round_chart_rows = ordered_rows
        self._render_round_legend(ordered_rows)
        if not self._round_stats_visible:
            self.main_pane.insert(0, self.round_stats_frame, weight=2)
            self._round_stats_visible = True
        self._schedule_round_chart_draw()

    def _hide_round_stats(self):
        if self._round_stats_visible:
            self.main_pane.forget(self.round_stats_frame)
            self._round_stats_visible = False
        self._clear_round_stats()

    def _render_terror_stats(self, rows: list[Statistics.TerrorStatistic]):
        self._clear_tree(self.terror_tree)
        for row in rows:
            self.terror_tree.insert(
                "",
                "end",
                iid=str(row.terror_id),
                values=(
                    row.terror_id,
                    row.name,
                    row.count,
                    f"{row.expected:.2f}",
                    self._format_p_value(row.p_value),
                    row.label,
                ),
            )

    def _on_terror_selected(self, _event=None):
        selection = self.terror_tree.selection()
        if not selection:
            return
        try:
            terror_id = int(selection[0])
        except ValueError:
            return
        self._clear_tree(self.map_tree)
        for map_name, count in Statistics.map_counts_for_terror(self.filtered_rows, terror_id):
            self.map_tree.insert("", "end", values=(map_name, count))

    def _clear_tree(self, tree: ttk.Treeview):
        items = tree.get_children()
        if items:
            tree.delete(*items)

    def _format_p_value(self, value: float) -> str:
        if value == 0.0:
            return "0"
        if value < 0.0001:
            return f"{value:.2e}"
        return f"{value:.6f}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        
        icon_path = resource_path("ToNAutoBeginnerIcon.ico")
        if icon_path.exists():
            self.iconbitmap(default=str(icon_path))
            
        self.title("ToNAutoBeginner")
        self.geometry("900x860")
        self.minsize(820, 760)
        self.configure(bg=BG)
        self.v_tnl       = tk.StringVar()
        self.v_win_count = tk.IntVar(value=4)
        self.keepOn_set: dict = {}
        self.monitors: list[LogMonitor.LogMonitor] = []
        self._running = False
        self._overlay: LogOverlay | None = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",            background=BG)
        s.configure("TLabel",            background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("TButton",           font=("Segoe UI", 10, "bold"), padding=4)
        s.configure("TCheckbutton",      background=BG, foreground=FG, font=("Segoe UI", 10))
        s.map("TCheckbutton", background=[("active", BG)])
        s.configure("TLabelframe",       background=BG, foreground=ACC)
        s.configure("TLabelframe.Label", background=BG, foreground=ACC,
                    font=("Segoe UI", 10, "bold"))
        s.configure("TEntry",            fieldbackground=SUB, foreground=FG)
        s.configure("TSpinbox",          fieldbackground=SUB, foreground=FG)
        s.configure("TNotebook",         background=BG, tabmargins=[2, 2, 2, 0])
        s.configure("TNotebook.Tab",     background=SUB, foreground=FG, padding=[10, 4])
        s.map("TNotebook.Tab",
              background=[("selected", ACC)], foreground=[("selected", BG)])
        s.configure("TSeparator",        background=SUB)

        # ① TNL
        f1 = ttk.LabelFrame(self, text="① 続行リスト(.tnl)", padding=8)
        f1.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Entry(f1, textvariable=self.v_tnl, width=58).pack(side="left", padx=(0, 6))
        ttk.Button(f1, text="参照…",    command=self._browse_tnl).pack(side="left")
        ttk.Button(f1, text="再読み込み", command=self._load_tnl).pack(side="left", padx=(4, 0))
        self.lbl_tnl = ttk.Label(f1, text="未読み込み", foreground=RED)
        self.lbl_tnl.pack(side="left", padx=(10, 0))

        # ② 自爆キー設定
        fk = ttk.Frame(self)
        fk.pack(fill="x", padx=12, pady=(0, 4))
        ttk.Label(fk, text="自爆キー:").pack(side="left")
        self.v_suicide_key = tk.StringVar(value=config.SELF_SUICIDE_KEY)
        ek = ttk.Entry(fk, textvariable=self.v_suicide_key, width=6)
        ek.pack(side="left", padx=(6, 4))
        ttk.Button(fk, text="適用",
                   command=lambda: LogMonitor.LogMonitor.set_suicide_key(self.v_suicide_key.get().strip())
                   ).pack(side="left")

        # ③ 窓数・ログ
        f2 = CollapsibleFrame(self, text="② 窓数・ログ設定")
        f2.pack(fill="x", padx=12, pady=4)
        f2 = f2.content  # 以降はcontent内に追加
        wf = ttk.Frame(f2)
        wf.pack(fill="x")
        ttk.Label(wf, text="窓数:").pack(side="left")
        sb = ttk.Spinbox(wf, from_=1, to=8, textvariable=self.v_win_count, width=4,
                         command=self._on_win_count_change)
        sb.pack(side="left", padx=(4, 16))
        sb.bind("<FocusOut>", lambda e: self._on_win_count_change())
        ttk.Button(wf, text="📄 最新ログを自動割り当て",
                   command=self._assign_logs).pack(side="left")


        self.lbl_win_warn = ttk.Label(
            f2, text="※ 窓数はマクロ起動前に設定してください", foreground=YLW)
        self.lbl_win_warn.pack(anchor="w")

        # ③ 窓タブ
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="x", expand=False, padx=12, pady=4)
        self.tabs: list[WindowTab] = []
        self._rebuild_tabs(self.v_win_count.get())

        # 音声ファイル設定
        fv_wrap = CollapsibleFrame(self, text="③ 音声アナウンス設定", collapsed=True)
        fv_wrap.pack(fill="x", padx=12, pady=4)
        fv = fv_wrap.content

        def voice_row(parent, label, var):
            f = ttk.Frame(parent)
            f.pack(fill="x", pady=2)
            ttk.Label(f, text=label, width=24, anchor="w").pack(side="left")
            ttk.Entry(f, textvariable=var, width=36).pack(side="left", padx=(0, 4))
            ttk.Button(f, text="…", width=3,
                command=lambda v=var: v.set(
                    filedialog.askopenfilename(
                        title="音声ファイルを選択",
                        filetypes=[("音声", "*.wav *.mp3"), ("All", "*.*")]
                    ) or v.get()
                )).pack(side="left")

        self.v_voice_continue     = tk.StringVar(value=config.VOICE_CONTINUE)
        self.v_voice_fog          = tk.StringVar(value=config.VOICE_FOG)
        self.v_voice_item_lost    = tk.StringVar(value=config.VOICE_ITEM_LOST)
        self.v_voice_intermission = tk.StringVar(value=config.VOICE_INTERMISSION)
        self.v_voice_foxy         = tk.StringVar(value=config.VOICE_FOXY)
        voice_row(fv, "続行ラウンド:", self.v_voice_continue)
        voice_row(fv, "霧ラウンド:", self.v_voice_fog)
        voice_row(fv, "アイテムロスト:", self.v_voice_item_lost)
        voice_row(fv, "Intermission:", self.v_voice_intermission)
        voice_row(fv, "Foxy:", self.v_voice_foxy)

        # 音量スライダー
        volf = ttk.Frame(fv)
        volf.pack(fill="x", pady=(6, 0))
        ttk.Label(volf, text="音量:").pack(side="left")
        self.v_volume = tk.DoubleVar(value=1.0)
        ttk.Scale(volf, from_=0.0, to=1.0, variable=self.v_volume,
                  orient="horizontal", length=160,
                  command=lambda v: PlaySound.set_sound_volume(float(v))).pack(side="left", padx=(6, 4))
        self.lbl_volume = ttk.Label(volf, text="100%")
        self.lbl_volume.pack(side="left")
        self.v_volume.trace_add("write", lambda *_: self.lbl_volume.config(
            text=f"{int(self.v_volume.get()*100)}%"))

        # AFK解除設定（DTM / Waldo）
        # コントロール
        fc = ttk.Frame(self)
        fc.pack(pady=6)
        self.btn_start = ttk.Button(fc, text="▶ マクロ開始",
                                    command=self._start, width=16)
        self.btn_start.pack(side="left", padx=6)
        self.btn_stop  = ttk.Button(fc, text="■ 停止",
                                    command=self._stop, state="disabled", width=12)
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(fc, text="📋 オーバーレイ",
                   command=self._toggle_overlay, width=14).pack(side="left", padx=6)
        ttk.Button(fc, text="統計",
                   command=self._open_statistics, width=10).pack(side="left", padx=6)
        ttk.Label(fc, text="緊急停止: Pキー長押し",
                  foreground=ORG).pack(side="left", padx=(10, 0))

        # 完全放置モード（全窓共通）
        fhf = ttk.Frame(self)
        fhf.pack(pady=(0, 4))
        self.btn_hands_free = tk.Button(
            fhf, text="🤖 完全放置モード: OFF（全窓共通）",
            bg=SUB, fg=FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_hands_free)
        self.btn_hands_free.pack(side="left")
        ttk.Label(fhf, text="← ONにするとアイテムロストを無視・全ラウンド即自爆",
                  foreground=YLW).pack(side="left", padx=(10, 0))

        # アイテム取得→Begin
        fif = ttk.Frame(self)
        fif.pack(pady=(0, 4))
        self.btn_item_get_begin = tk.Button(
            fif, text="🎯 アイテム取得→Begin: OFF（全窓共通）",
            bg=SUB, fg=FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_item_get_begin)
        self.btn_item_get_begin.pack(side="left")
        ttk.Label(fif, text="← ONにするとアイテムロストラウンド開始時にフォーカス＆フリーズ",
                  foreground=YLW).pack(side="left", padx=(10, 0))

        # ログ
        fl = ttk.LabelFrame(self, text="ログ出力", padding=4)
        fl.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(
            fl, height=16, bg="#181825", fg=FG, width=80,
            font=("Consolas", 9), state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(fl, text="クリア", command=self._clear_log).pack(anchor="e", pady=(2, 0))

        # クレジット表示
        tk.Label(self, text="Credit: VOICEVOX冥鳴ひまり",
                 bg=BG, fg=SUB, font=("Segoe UI", 8)).pack(anchor="e", padx=12)

    def _rebuild_tabs(self, count: int):
        for tab in self.tabs:
            self.nb.forget(tab)
        self.tabs.clear()
        for i in range(count):
            tab = WindowTab(self.nb, i)
            self.nb.add(tab, text=f"窓{i + 1}")
            self.tabs.append(tab)

    def _on_win_count_change(self):
        if self._running:
            return
        try:
            n = max(1, min(8, int(self.v_win_count.get())))
        except (ValueError, tk.TclError):
            n = 1
        self.v_win_count.set(n)
        self._rebuild_tabs(n)

    def _toggle_item_get_begin(self):
        val = not LogMonitor.get_item_get_begin_mode()
        LogMonitor.set_item_get_begin_mode(val)
        if val:
            self.btn_item_get_begin.config(
                text="🎯 アイテム取得→Begin: ON（全窓共通）",
                bg="#1a3a5a", fg="#89b4fa", relief="sunken")
            self._log("[アイテム取得→Begin] ON: ラウンド開始時にフォーカス＆フリーズ")
        else:
            self.btn_item_get_begin.config(
                text="🎯 アイテム取得→Begin: OFF（全窓共通）",
                bg=SUB, fg=FG, relief="raised")
            self._log("[アイテム取得→Begin] OFF")

    def _toggle_hands_free(self):
        val = not LogMonitor.get_hands_free()
        LogMonitor.set_hands_free(val)
        if val:
            self.btn_hands_free.config(
                text="🤖 完全放置モード: ON（全窓共通）",
                bg="#3a1a1a", fg=RED, relief="sunken")
            self._log("[放置モード] ON: アイテムロスト無視・全ラウンド即自爆")
        else:
            self.btn_hands_free.config(
                text="🤖 完全放置モード: OFF（全窓共通）",
                bg=SUB, fg=FG, relief="raised")
            self._log("[放置モード] OFF")

    def _browse_tnl(self):
        p = filedialog.askopenfilename(
            title="tnlファイルを選択",
            filetypes=[("TNL files", "*.tnl"), ("All", "*.*")]
        )
        if p:
            self.v_tnl.set(p)
            self._load_tnl()

    def _load_tnl(self):
        p = self.v_tnl.get().strip()
        if not p or not Path(p).exists():
            messagebox.showerror("エラー", "tnlファイルが見つかりません")
            return
        try:
            self.keepOn_set, meta = MatchTNL.load_tnl(p)
            total = sum(len(v) for v in self.keepOn_set.values())
            msg = f"[{meta['list_name']}] {len(self.keepOn_set)}ラウンド / {total}件 スキップ対象"
            self.lbl_tnl.config(text=msg, foreground=GRN)
            self._log(f"[TNL] {msg}")
        except Exception as e:                                  
            messagebox.showerror("TNL読み込みエラー", str(e))

    def _assign_logs(self):
        """
        VRChatウィンドウとログファイルを起動順（古い順）に自動割り当てる。
        有効な窓タブのi番目 → HWND[i] + ログ[i] を一括設定。
        """
        hwnds = get_vrchat_windows(self.v_win_count.get()) # 選ばれたのが古い順で配列に格納される。
        active_tabs = [tab for tab in self.tabs if tab.v_active.get()]
        count = max(len(hwnds), len(active_tabs))
        logs = find_latest_logs(VRCHAT_LOG_DIR, count)

        if not hwnds and not logs:
            self._log("[自動割り当て] VRChatウィンドウもログも見つかりません")
            return

        for i, tab in enumerate(active_tabs):
            # HWND割り当て
            if i < len(hwnds):
                hwnd = hwnds[i]
                # ComboboxにHWNDを追加して選択
                tab._refresh_hwnds(self.v_win_count.get())
                label = next(
                    (k for k, v in tab._hwnd_map.items() if v == hwnd), None)
                if label:
                    tab.v_hwnd_sel.set(label)

            # ログ割り当て
            if i < len(logs):
                tab.v_log.set(str(logs[i]))

            hwnd_str = f"{hwnds[i]:#010x}" if i < len(hwnds) else "未検出"
            log_str  = logs[i].name if i < len(logs) else "未検出"
            self._log(f"[窓{tab.idx+1}] HWND={hwnd_str} → {log_str}")
        self._log(f"[自動割り当て] {len(active_tabs)}窓に割り当てました")

    def _start(self):
        if not self.keepOn_set:
            messagebox.showwarning("警告", "先にtnlを読み込んでください")
            return

        # 有効な窓のログが空なら自動割り当て
        active_tabs = [tab for tab in self.tabs if tab.v_active.get()]
        logs = find_latest_logs(VRCHAT_LOG_DIR, len(active_tabs))
        log_idx = 0
        for tab in active_tabs:
            if not tab.v_log.get().strip() and log_idx < len(logs):
                tab.v_log.set(str(logs[log_idx]))
                self._log(f"[窓{tab.idx+1}] ログを自動割り当て: {logs[log_idx].name}")
            log_idx += 1

        self.monitors.clear()
        for tab in self.tabs:
            cfg, err = tab.get_config()
            if cfg is None:
                if err:
                    self._log(f"[窓{tab.idx+1}] スキップ: {err}")
                continue
            if cfg.hwnd == 0:
                self._log(f"[窓{tab.idx+1}] ⚠ HWNDが未選択です。窓タブで「🔄 更新」してVRChatウィンドウを選択してください")
            # 音声ファイルパスをAppのGUI設定から注入
            cfg.voice_continue     = self.v_voice_continue.get().strip()
            cfg.voice_fog          = self.v_voice_fog.get().strip()
            cfg.voice_item_lost    = self.v_voice_item_lost.get().strip()
            cfg.voice_intermission = self.v_voice_intermission.get().strip()
            cfg.voice_foxy          = self.v_voice_foxy.get().strip()
            self._log(f"[窓{tab.idx+1}] HWND={cfg.hwnd:#010x}  ログ={cfg.log_path.name}")
            mon = LogMonitor.LogMonitor(cfg, self.keepOn_set, self._log, window_idx=tab.idx + 1)
            self.monitors.append(mon)
            mon.start()

        if not self.monitors:
            messagebox.showerror("エラー", "有効な窓/ログが見つかりません")
            return

        self._running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_win_warn.config(
            text="⚠ 動作中です。窓数はマクロ停止後に変更できます", foreground=RED)
        self._log(f"[起動] {len(self.monitors)}窓の監視を開始")

    def _stop(self):
        LogMonitor._EQUIP_WAIT_EVENT.set()          # フリーズ中でも確実に解除
        LogMonitor._continue_round_reset()        # 続行ラウンドフリーズも解除
        for m in self.monitors:
            m.stop()
        self.monitors.clear()
        self._running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_win_warn.config(
            text="※ 窓数はマクロ起動前に設定してください", foreground=YLW)
        self._log("[停止] マクロを停止しました")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}\n"
        def _a():
            self.log_text.config(state="normal")
            self.log_text.insert("end", line)
            self.log_text.see("end")
            self.log_text.config(state="disabled")
            if self._overlay and not self._overlay._closed:
                self._overlay.append(f"[{ts}] {msg}")
        self.after(0, _a)

    def _open_statistics(self):
        StatisticsWindow(self)

    def _toggle_overlay(self):
        if self._overlay and not self._overlay._closed:
            self._overlay.close()
            self._overlay = None
        else:
            self._overlay = LogOverlay(self)

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _on_close(self):
        self._stop()
        self.destroy()
