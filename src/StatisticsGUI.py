import calendar
import math
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

import config
import SharedState
import UIFont
import WindowOperator
import ConnectDB
import RoundStore
import Statistics

BG  = config.GUI_BG
FG  = config.GUI_FG
ACC = config.GUI_ACC
RED = config.GUI_RED
GRN = config.GUI_GRN
SUB = config.GUI_SUB
YLW = config.GUI_YLW
ORG = config.GUI_ORG
ROUND_CHIP_COLUMNS = 6
ROUND_CHIP_WIDTH = 21
ROUND_CHIP_MIN_WIDTH = 158
DEFAULT_EXCLUDED_ROUNDS = {"Classic", "Run"}
ROUND_CHART_COLORS = (
    "#accdff",
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


def _round_count_sort_key(row: tuple[str, int, int]) -> tuple[int, tuple[int, int, str]]:
    round_name, count, _slots = row
    return (-count, _round_order_key(round_name))


# 組み合わせのタブに出す行数（多い順）
COMBO_ROWS = 200


def _round_name(round_id) -> str:
    """ラウンドの番号 → 名前（表に無い番号はその数字）"""
    return config.ROUND_TYPE_NAMES.get(round_id, str(round_id))


def _db_time(dt: datetime) -> int:
    """ローカルの日時 → DB の time（2026-01-01 UTC からの秒）"""
    return int(dt.timestamp()) - config.DB_TIME_EPOCH


def _local(db_time: int) -> datetime:
    return datetime.fromtimestamp(int(db_time) + config.DB_TIME_EPOCH)


class StatisticsWindow(tk.Toplevel):
    """統計画面 v1。DB から差分だけ手元の SQLite（RoundStore）へ取り込み、集計は手元で行う。

    タブ: ラウンド／テラー／時間の流れ／組み合わせ／マルチ。範囲は「全体」か「自分の分」
    （1人目が自分か、other_uids に自分がいる行。ほかの人の uid は表示しない）
    """

    def __init__(self, parent, store=None):
        super().__init__(parent)
        self.title("ToN Statistics")
        self.geometry("1180x900")
        self.minsize(980, 760)
        self.configure(bg=BG)
        self.store = store or RoundStore.default_store()
        self._dt_vars: dict[str, dict[str, tk.IntVar]] = {}
        self.round_vars: dict[str, tk.BooleanVar] = {}
        self._round_ids: dict[str, int] = {}
        self.v_terror_category = tk.StringVar(value="unbound")
        self.v_scope = tk.StringVar(value="all")
        self._category_buttons: dict[str, tk.Button] = {}
        self._round_chart_rows: list[tuple[str, int, int]] = []
        self._round_chart_job: str | None = None
        self._rows_loading = False
        self._range_set = False
        self._filter: RoundStore.Filter | None = None
        self._terror_stats_cache: dict[str, tuple[int, int, list[Statistics.TerrorStatistic]]] = {}
        self._map_counts_cache: dict[int, list[tuple[str, int]]] = {}
        self._selected_terror: int | None = None
        self.v_status = tk.StringVar(value="統計データ未読み込み")
        self.v_info = tk.StringVar(value="")
        self._remember_own_window()      # 録画中だけキャプチャから外すため
        self._build_ui()
        self._load_rows_async()

    def _remember_own_window(self):
        """当ツールの窓として覚える。VRChat の窓とは別の入れ物（前面判定を汚さない）"""
        hwnd = WindowOperator.own_window_hwnd(self)
        if not hwnd:
            return
        SharedState.register_own_window(hwnd)

        def forget(event, _hwnd=hwnd):
            if event.widget is self:     # 子ウィジェットの Destroy は無視
                SharedState.unregister_own_window(_hwnd)
                WindowOperator.set_capture_excluded(_hwnd, False)

        try:
            self.bind("<Destroy>", forget, add="+")
        except tk.TclError:
            pass

    # ── 画面 ─────────────────────────────────
    def _build_ui(self):
        controls = ttk.LabelFrame(self, text="集計条件", padding=8)
        controls.pack(fill="x", padx=12, pady=(12, 6))

        scope = ttk.Frame(controls)
        scope.pack(fill="x", pady=(0, 6))
        ttk.Label(scope, text="範囲").pack(side="left", padx=(0, 6))
        for text, value in (("全体", "all"), ("自分の分", "mine")):
            ttk.Radiobutton(scope, text=text, value=value, variable=self.v_scope,
                            command=self._analyze).pack(side="left", padx=(0, 8))
        ttk.Label(scope, textvariable=self.v_info, foreground=SUB).pack(side="left", padx=(12, 0))

        period = ttk.Frame(controls)
        period.pack(fill="x", pady=(0, 8))
        self._make_datetime_picker(period, "開始", "start").pack(side="left", padx=(0, 18))
        self._make_datetime_picker(period, "終了", "end").pack(side="left", padx=(0, 18))
        ttk.Button(period, text="更新", command=self._load_rows_async).pack(side="left", padx=(0, 6))
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

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=6)
        self._build_round_tab()
        self._build_terror_tab()
        self._build_time_tab()
        self._build_combo_tab()
        self._build_multi_tab()

        ttk.Label(
            self,
            text="判定: 出やすい a=0.05 / かなり出やすい a=0.025 / テーブル！ a=0.001",
            foreground=YLW,
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _tab(self, text: str) -> ttk.Frame:
        frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(frame, text=text)
        return frame

    def _tree(self, parent, columns) -> ttk.Treeview:
        """[(列, 見出し, 幅, 寄せ)] の表（縦のスクロールつき）"""
        body = ttk.Frame(parent)
        body.pack(side="left", fill="both", expand=True)
        tree = ttk.Treeview(body, columns=[c[0] for c in columns], show="headings")
        for col, text, width, anchor in columns:
            tree.heading(col, text=text)
            tree.column(col, width=width, minwidth=48, anchor=anchor, stretch=(anchor == "w"))
        scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        return tree

    def _build_round_tab(self):
        tab = self._tab("ラウンド")
        tab.grid_columnconfigure(0, minsize=360, weight=3)
        tab.grid_columnconfigure(1, minsize=440, weight=2)
        tab.grid_rowconfigure(0, weight=1)
        self.round_chart = tk.Canvas(tab, bg=BG, height=260, highlightthickness=0, bd=0)
        self.round_chart.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.round_chart.bind("<Configure>", self._schedule_round_chart_draw)
        legend = ttk.Frame(tab)
        legend.grid(row=0, column=1, sticky="nsew")
        self.round_legend = self._tree(legend, (("mark", "", 34, "center"), ("round", "ラウンド", 260, "w"),
                                                ("count", "回数", 82, "e"), ("percent", "%", 82, "e")))

    def _build_terror_tab(self):
        tab = self._tab("テラー")
        pane = ttk.PanedWindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        terror_frame = ttk.LabelFrame(pane, text="テラー出現回数", padding=8)
        pane.add(terror_frame, weight=3)
        category_bar = tk.Frame(terror_frame, bg=BG)
        category_bar.pack(fill="x", pady=(0, 8))
        for label, value in (("Classic", "classic"), ("Alternate", "alternate"), ("Unbound", "unbound")):
            btn = tk.Button(category_bar, text=label, command=lambda v=value: self._set_terror_category(v),
                            padx=14, pady=3, bd=1, relief="raised", bg=SUB, fg=FG,
                            activebackground=ACC, activeforeground=BG, font=(UIFont.UI, 9, "bold"))
            btn.pack(side="left", padx=(0, 6))
            self._category_buttons[value] = btn
        self._refresh_category_buttons()
        body = ttk.Frame(terror_frame)
        body.pack(fill="both", expand=True)
        self.terror_tree = self._tree(body, (("id", "ID", 58, "e"), ("name", "テラー", 260, "w"),
                                             ("count", "回数", 86, "e"), ("expected", "期待", 86, "e"),
                                             ("p_value", "上側p値", 110, "e"), ("label", "判定", 130, "center")))
        self.terror_tree.bind("<<TreeviewSelect>>", self._on_terror_selected)
        map_frame = ttk.LabelFrame(pane, text="選択テラーのマップ一覧", padding=8)
        pane.add(map_frame, weight=2)
        self.map_tree = self._tree(map_frame, (("map", "マップ", 260, "w"), ("count", "回数", 90, "e")))

    def _build_time_tab(self):
        tab = self._tab("時間の流れ")
        self.v_time_note = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.v_time_note, foreground=YLW).pack(anchor="w")
        self.day_chart = tk.Canvas(tab, bg=BG, height=240, highlightthickness=0, bd=0)
        self.day_chart.pack(fill="both", expand=True, pady=(4, 8))
        self.hour_chart = tk.Canvas(tab, bg=BG, height=240, highlightthickness=0, bd=0)
        self.hour_chart.pack(fill="both", expand=True)
        self._day_rows: list[tuple] = []
        self._hour_rows: list[tuple] = []
        for canvas in (self.day_chart, self.hour_chart):
            canvas.bind("<Configure>", lambda _e: self._draw_time_charts())

    def _build_combo_tab(self):
        tab = self._tab("組み合わせ")
        pane = ttk.PanedWindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        pairs = ttk.LabelFrame(pane, text="一緒に出たテラーの組（2体以上のラウンド）", padding=8)
        pane.add(pairs, weight=1)
        self.pair_tree = self._tree(pairs, (("a", "テラー", 220, "w"), ("b", "テラー", 220, "w"),
                                            ("count", "回数", 80, "e")))
        maps = ttk.LabelFrame(pane, text="マップとテラーの組", padding=8)
        pane.add(maps, weight=1)
        self.map_terror_tree = self._tree(maps, (("map", "マップ", 220, "w"), ("terror", "テラー", 220, "w"),
                                                 ("count", "回数", 80, "e")))

    def _build_multi_tab(self):
        tab = self._tab("マルチ")
        self.v_multi_note = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.v_multi_note, foreground=YLW).pack(anchor="w", pady=(0, 6))
        self.multi_tree = self._tree(tab, (("players", "見た人数", 120, "e"), ("count", "ラウンド数", 120, "e"),
                                           ("percent", "%", 100, "e")))

    # ── 期間 ─────────────────────────────────
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

    # ── 取り込み（差分だけ DB から）──────────────────────
    def _load_rows_async(self):
        if self._rows_loading:
            return
        self._rows_loading = True
        self.v_status.set("統計データ更新中...")
        threading.Thread(target=self._load_rows_worker, daemon=True).start()

    def _load_rows_worker(self):
        ok = self.store.sync(ConnectDB.fetch_rounds)
        try:
            self.after(0, lambda: self._on_rows_loaded(ok))
        except (tk.TclError, RuntimeError):
            pass                     # 取り込みの間に閉じた

    def _on_rows_loaded(self, ok: bool):
        self._rows_loading = False
        self.v_info.set(f"最終更新 {datetime.now().strftime('%H:%M:%S')} / 手元 {self.store.count()}件")
        self._populate_rounds()
        if not self._range_set:
            first, last = self.store.time_range()
            if first is not None:
                self._set_picker_datetime("start", _local(first))
                self._set_picker_datetime("end", _local(last))
                self._range_set = True
        self._analyze()
        if not ok:
            self.v_status.set("更新できませんでした（手元の分を表示）")

    # ── ラウンドの選択 ───────────────────────────
    def _populate_rounds(self):
        previous = {name: var.get() for name, var in self.round_vars.items()}
        for child in self.round_chip_frame.winfo_children():
            child.destroy()
        self.round_vars.clear()
        self._round_ids = {_round_name(rid): rid for rid in self.store.available_rounds()}
        for col in range(ROUND_CHIP_COLUMNS):
            self.round_chip_frame.grid_columnconfigure(col, minsize=ROUND_CHIP_MIN_WIDTH, weight=1,
                                                       uniform="round_chip")
        row = col = 0
        for display_name, round_name in _ordered_round_entries(list(self._round_ids)):
            if round_name is None:
                if col:
                    row += 1
                    col = 0
                tk.Frame(self.round_chip_frame, bg=ACC, height=1).grid(
                    row=row, column=0, columnspan=ROUND_CHIP_COLUMNS, sticky="ew", padx=4, pady=(6, 5))
                row += 1
                continue
            var = tk.BooleanVar(value=previous.get(round_name, round_name not in DEFAULT_EXCLUDED_ROUNDS))
            self.round_vars[round_name] = var
            chip = tk.Checkbutton(self.round_chip_frame, text=display_name, variable=var, indicatoron=False,
                                  command=self._style_round_chips, bg=SUB, fg=FG, selectcolor=ACC,
                                  activebackground=ACC, activeforeground=BG, font=(UIFont.UI, 9, "bold"),
                                  relief="raised", bd=1, width=ROUND_CHIP_WIDTH, padx=0, pady=4,
                                  anchor="center")
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

    def _style_round_chips(self):
        for child in self.round_chip_frame.winfo_children():
            if not isinstance(child, tk.Checkbutton):
                continue
            round_name = getattr(child, "round_name", child.cget("text"))
            var = self.round_vars.get(round_name)
            selected = bool(var and var.get())
            child.configure(bg=ACC if selected else SUB, fg=BG if selected else FG,
                            relief="sunken" if selected else "raised")

    def _set_terror_category(self, category: str):
        if self.v_terror_category.get() == category:
            return
        self.v_terror_category.set(category)
        self._refresh_category_buttons()
        self._analyze()

    def _refresh_category_buttons(self):
        active = self.v_terror_category.get()
        for category, btn in self._category_buttons.items():
            selected = category == active
            btn.configure(bg=ACC if selected else SUB, fg=BG if selected else FG,
                          relief="sunken" if selected else "raised")

    # ── 集計 ─────────────────────────────────
    def _make_filter(self, start_at: datetime, end_at: datetime, rounds: set[str]) -> RoundStore.Filter:
        return RoundStore.Filter(
            start=_db_time(start_at), end=_db_time(end_at),
            rounds=frozenset(self._round_ids[name] for name in rounds if name in self._round_ids),
            mine=self.v_scope.get() == "mine")

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
            self._clear_all()
            self.v_status.set("ラウンドを選択してください")
            return

        flt = self._make_filter(start_at, end_at, rounds)
        if flt != self._filter:
            self._filter = flt
            self._terror_stats_cache.clear()
            self._map_counts_cache.clear()
        round_rows = Statistics.round_summary_from_counts(
            (_round_name(rid), count, slots) for rid, count, slots in self.store.round_summary(flt))
        self._show_round_stats(round_rows)

        category = self.v_terror_category.get()
        stats = self._terror_stats_cache.get(category)
        if stats is None:
            candidate_ids = Statistics.candidate_ids_for_category(category, config.TERRORS)
            stats = Statistics.analyze_terror_counts(self.store.terror_counts(flt), config.TERRORS,
                                                     candidate_ids)
            self._terror_stats_cache[category] = stats
        total_slots, candidate_count, terror_rows = stats
        self._render_terror_stats(terror_rows)
        self._clear_tree(self.map_tree)
        self._render_time(flt)
        self._render_combos(flt)
        self._render_multi(flt)
        total_rounds = sum(count for _name, count, _slots in round_rows)
        self.v_status.set(f"{total_rounds}ラウンド / {total_slots}枠 / 候補{candidate_count}体")

    def _clear_all(self):
        self._clear_round_stats()
        for tree in (self.terror_tree, self.map_tree, self.pair_tree, self.map_terror_tree, self.multi_tree):
            self._clear_tree(tree)
        self._day_rows, self._hour_rows = [], []
        self._draw_time_charts()

    # ── ラウンドのタブ ─────────────────────────────
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
            self.round_legend.insert("", "end", values=("■", _round_display_name(round_name), count,
                                                        f"{percent:.1f}"), tags=(tag,))

    def _schedule_round_chart_draw(self, _event=None):
        if self._round_chart_job is not None:
            try:
                self.after_cancel(self._round_chart_job)
            except tk.TclError:
                pass
        self._round_chart_job = self.after(70, self._draw_round_chart)

    def _draw_round_slice(self, canvas: tk.Canvas, cx: float, cy: float, radius: float,
                          start: float, extent: float, color: str):
        steps = max(2, int(abs(extent) / 4) + 1)
        points = [(cx, cy)]
        for step in range(steps + 1):
            angle = math.radians(start + extent * step / steps)
            points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
        canvas.create_polygon(*points, fill=color, outline=BG, width=1)

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
            slice_start = start - extent
            self._draw_round_slice(canvas, cx, cy, radius, slice_start, extent, color)
            start = slice_start
        inner = size * 0.38
        canvas.create_oval(cx - inner / 2, cy - inner / 2, cx + inner / 2, cy + inner / 2, fill=BG, outline=BG)
        canvas.create_text(cx, cy - 8, text=str(total), fill=FG, font=(UIFont.UI, 18, "bold"))
        canvas.create_text(cx, cy + 14, text="rounds", fill=YLW, font=(UIFont.UI, 9))

    def _show_round_stats(self, rows: list[tuple[str, int, int]]):
        ordered_rows = sorted(rows, key=_round_count_sort_key)
        self._round_chart_rows = ordered_rows
        self._render_round_legend(ordered_rows)
        self._schedule_round_chart_draw()

    # ── テラーのタブ ──────────────────────────────
    def _render_terror_stats(self, rows: list[Statistics.TerrorStatistic]):
        self._clear_tree(self.terror_tree)
        for row in rows:
            self.terror_tree.insert("", "end", iid=str(row.terror_id), values=(
                row.terror_id, row.name, row.count, f"{row.expected:.2f}",
                self._format_p_value(row.p_value), row.label))

    def _on_terror_selected(self, _event=None):
        selection = self.terror_tree.selection()
        if not selection:
            return
        try:
            terror_id = int(selection[0])
        except ValueError:
            return
        self._selected_terror = terror_id
        self._clear_tree(self.map_tree)
        rows = self._map_counts_cache.get(terror_id)
        if rows is None:
            rows = Statistics.map_counts_from_entries(
                (map_id, _round_name(rid), hits)
                for map_id, rid, hits in self.store.map_counts_for_terror(self._filter, terror_id))
            self._map_counts_cache[terror_id] = rows
        for map_name, count in rows:
            self.map_tree.insert("", "end", values=(map_name, count))
        if self._filter is not None:
            self._render_time(self._filter)

    # ── 時間の流れのタブ ───────────────────────────
    def _render_time(self, flt: RoundStore.Filter):
        tid = self._selected_terror
        self._day_rows = self.store.time_series(flt, "day", tid)
        self._hour_rows = self.store.time_series(flt, "hour", tid)
        if tid is None:
            self.v_time_note.set("ラウンド数（テラーのタブで選ぶと、そのテラーの出現数と割合）")
        else:
            self.v_time_note.set(f"{Statistics.terror_name(tid, config.TERRORS)} の出現数（ラウンド数に対する割合）")
        self._draw_time_charts()

    def _draw_time_charts(self):
        tid = self._selected_terror
        self._draw_bars(self.day_chart, "日ごと", [(str(k)[5:], n, hits) for k, n, hits in self._day_rows], tid)
        by_hour = {int(k): (n, hits) for k, n, hits in self._hour_rows if k is not None}
        self._draw_bars(self.hour_chart, "時間帯（0〜23時）",
                        [(str(h), *by_hour.get(h, (0, 0))) for h in range(24)], tid)

    def _draw_bars(self, canvas: tk.Canvas, title: str, rows, terror_id):
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        canvas.create_text(8, 10, text=title, anchor="w", fill=YLW, font=(UIFont.UI, 9))
        values = [(label, hits if terror_id is not None else n, n) for label, n, hits in rows]
        top = max((v for _l, v, _n in values), default=0)
        if not values or top <= 0:
            return
        left, bottom, usable = 8, height - 18, height - 40
        step = (width - 16) / len(values)
        for index, (label, value, rounds) in enumerate(values):
            x0 = left + index * step + 1
            x1 = x0 + max(step - 2, 1)
            y = bottom - usable * value / top
            canvas.create_rectangle(x0, y, x1, bottom, fill=ACC, outline="")
            if step >= 22:
                canvas.create_text((x0 + x1) / 2, bottom + 9, text=label, fill=FG, font=(UIFont.UI, 7))
                text = str(value) if terror_id is None else (
                    f"{value}\n{value / rounds * 100:.0f}%" if rounds else str(value))
                canvas.create_text((x0 + x1) / 2, y - 12, text=text, fill=FG, font=(UIFont.UI, 7))

    # ── 組み合わせのタブ ───────────────────────────
    def _render_combos(self, flt: RoundStore.Filter):
        self._clear_tree(self.pair_tree)
        for a, b, count in self.store.terror_pairs(flt)[:COMBO_ROWS]:
            self.pair_tree.insert("", "end", values=(Statistics.terror_name(a, config.TERRORS),
                                                     Statistics.terror_name(b, config.TERRORS), count))
        self._clear_tree(self.map_terror_tree)
        merged: dict[tuple[str, int], int] = {}
        for map_id, rid, tid, count in self.store.map_terror_counts(flt):
            key = (Statistics.map_name_for_id(map_id, _round_name(rid)), tid)
            merged[key] = merged.get(key, 0) + count
        ordered = sorted(merged.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
        for (map_name, tid), count in ordered[:COMBO_ROWS]:
            self.map_terror_tree.insert("", "end", values=(map_name, Statistics.terror_name(tid, config.TERRORS),
                                                           count))

    # ── マルチのタブ ──────────────────────────────
    def _render_multi(self, flt: RoundStore.Filter):
        self._clear_tree(self.multi_tree)
        if not flt.mine:
            self.v_multi_note.set("範囲を「自分の分」にすると表示します")
            return
        counts = self.store.player_counts(flt)
        total = sum(counts.values())
        if not total:
            self.v_multi_note.set("自分の分のラウンドがありません")
            return
        solo = counts.get(1, 0)
        self.v_multi_note.set(f"ソロ {solo}（{solo / total * 100:.1f}%） / "
                              f"マルチ {total - solo}（{(total - solo) / total * 100:.1f}%）")
        for players in sorted(counts):
            self.multi_tree.insert("", "end", values=(f"{players}人", counts[players],
                                                      f"{counts[players] / total * 100:.1f}"))

    # ── 共通 ─────────────────────────────────
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
