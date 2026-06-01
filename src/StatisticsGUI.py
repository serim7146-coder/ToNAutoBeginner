import calendar
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

import config
import ConnectDB
import Statistics

BG  = "#1e1e2e"
FG  = "#cdd6f4"
ACC = "#89b4fa"
RED = "#f38ba8"
GRN = "#a6e3a1"
SUB = "#313244"
YLW = "#f9e2af"
ORG = "#fab387"
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
        ("Fog (Alternate)", ("Fog (Alternate)",)),
        ("Ghost", ("Ghost",)),
        ("Ghost (Alternate)", ("Ghost (Alternate)",)),
        ("Punish", ("Punished",)),
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
        canvas.create_arc(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            start=start,
            extent=-extent,
            style=tk.PIESLICE,
            fill=color,
            outline=BG,
            width=1,
        )

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


