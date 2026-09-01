import json
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, filedialog, messagebox
from datetime import datetime
from typing import Optional

import config
from config import resource_path
import AutoUpdate
import LogMonitor
import SharedState
import PlaySound
import MatchTNL
import VRChatDiscovery
import VRChatLauncher
import ToNEntry
import OSCClient
from StatisticsGUI import StatisticsWindow

try:
    import keyboard
except ImportError:
    keyboard = None


# ── 設定ファイル（前回のtnlパス等の永続化） ──────
def load_settings() -> dict:
    """設定ファイルを読み込む。無い・壊れている場合は空dict。"""
    try:
        return json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict):
    """設定ファイルへ保存する（失敗しても動作継続）"""
    try:
        config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def launch_window_count(win_count: int, already_open: int) -> int:
    """新しく起動する窓数の既定値。

    マクロを適用する窓数から、すでに開いているVRChatの窓数を引いた数。
    足りている（または多い）場合は0。
    """
    try:
        win_count = int(win_count)
        already_open = int(already_open)
    except (TypeError, ValueError):
        return 0
    return max(0, win_count - max(0, already_open))


def tabs_to_launch(tabs: list, count: int) -> list:
    """起動対象の窓タブを後ろから count 個選ぶ。

    既存の窓は起動時刻順に先頭の窓タブへ割り当てられる（_assign_windows_and_logs）。
    そのため新しく開く窓は後ろのタブに対応し、プロファイルとOSCポートも
    そのタブのものを使う必要がある。
    """
    if count <= 0:
        return []
    return tabs[max(0, len(tabs) - count):]


def build_launch_plan(tabs: list) -> list:
    """起動計画 (表示用の窓番号, プロファイルID, OSC/インスタンス割当index)。

    OSC割当はタブ番号そのものを使う。監視開始時のポート決定
    （App._start の ports_for_window(tab.idx)）と一致させるため。
    """
    return [(tab.idx + 1, tab.v_profile.get(), tab.idx) for tab in tabs]


class WindowTab(ttk.Frame):
    def __init__(self, parent, idx: int, on_log_selected=None):
        super().__init__(parent)
        self.idx = idx
        self._hwnd_map: dict[str, int] = {}
        self._on_log_selected = on_log_selected
        self._build()

    def _build(self):
        p = self

        def section(text):
            ttk.Label(p, text=text, background=config.GUI_BG, foreground=config.GUI_ACC,
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

        # ── 起動プロファイル ──
        section("■ 起動プロファイル（VRChat起動時に使用）")
        pf = ttk.Frame(p)
        pf.pack(fill="x", padx=10, pady=2)
        ttk.Label(pf, text="--profile=").pack(side="left")
        self.v_profile = tk.IntVar(value=0)
        ttk.Spinbox(pf, from_=0, to=15, textvariable=self.v_profile, width=4).pack(side="left", padx=(2, 8))
        ttk.Label(pf, text="※ 同じ番号の窓は同じアカウント設定を共有します",
                  foreground=config.GUI_YLW).pack(side="left")

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
        self.v_freeze_8pages = tk.BooleanVar(value=False)
        self.v_freeze_punish = tk.BooleanVar(value=False)
        ttk.Checkbutton(cf, text="この窓を有効化",            variable=self.v_active).pack(side="left")
        ttk.Checkbutton(cf, text="自動Begin",                variable=self.v_auto_begin).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="自動自爆",                 variable=self.v_do_skip).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="DTM/Waldo続行 (3クラまで)", variable=self.v_cancel_afk).pack(side="left", padx=(12, 0))
        cf2 = ttk.Frame(p)
        cf2.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Checkbutton(cf2, text="干し芋自動自爆",           variable=self.v_hoshiimo).pack(side="left")
        ttk.Checkbutton(cf2, text="Intermissionアナウンス",    variable=self.v_announce_intermission).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf2, text="8 Pages検知でフリーズ",      variable=self.v_freeze_8pages).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf2, text="Punished検知でフリーズ",     variable=self.v_freeze_punish).pack(side="left", padx=(12, 0))

        # ラウンド突入で全窓フリーズ（張った窓自身は自爆できる）
        cf3 = ttk.Frame(p)
        cf3.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Label(cf3, text="突入で全窓停止:").pack(side="left")
        self.v_freeze_rounds = {}
        for name in config.ROUND_FREEZE_SELECTABLE:
            var = tk.BooleanVar(value=False)
            self.v_freeze_rounds[name] = var
            ttk.Checkbutton(cf3, text=name, variable=var).pack(side="left", padx=(8, 0))


    # 最新のアクティブになったデータから取得し、VRChatウィンドウを古い順にhwndが入った配列で返す
    def _refresh_hwnds(self, hwnd_count: int):
        hwnds = VRChatDiscovery.get_vrchat_windows(hwnd_count)
        self.set_hwnd_choices(hwnds)

    def set_hwnd_choices(self, hwnds: list[int], selected_hwnd: int | None = None):
        self._hwnd_map = {}
        choices = []
        for i, h in enumerate(hwnds):
            label = f"[{i+1}] HWND={h:#010x}"
            self._hwnd_map[label] = h
            choices.append(label)
        self.cb_hwnd["values"] = choices
        if selected_hwnd is not None:
            label = next((k for k, v in self._hwnd_map.items() if v == selected_hwnd), None)
            if label:
                self.v_hwnd_sel.set(label)
        elif choices and self.v_hwnd_sel.get() == "未選択":
            self.v_hwnd_sel.set(choices[0])

    def _get_selected_hwnd(self) -> int:
        return self._hwnd_map.get(self.v_hwnd_sel.get(), 0)

    def _browse_log(self):
        p = filedialog.askopenfilename(
            title="VRChatログを選択",
            initialdir=str(config.VRCHAT_LOG_DIR),
            filetypes=[("Log", "*.txt"), ("All", "*.*")]
        )
        if p:
            self.v_log.set(p)
            if self._on_log_selected:
                self._on_log_selected(self)

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
            freeze_on_8pages=self.v_freeze_8pages.get(),
            freeze_on_punish=self.v_freeze_punish.get(),
            freeze_rounds={name for name, var in self.v_freeze_rounds.items()
                           if var.get()},
        ), None


# ── ログオーバーレイ ──────────────────────────────
class LogOverlay(tk.Toplevel):
    """半透明の最前面ログオーバーレイウィンドウ"""
    MAX_LINES = config.GUI_OVERLAY_LOG_MAX_LINES

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
        super().__init__(parent, bg=config.GUI_BG, **kwargs)
        self._collapsed = collapsed
        # ヘッダ行
        hf = tk.Frame(self, bg=config.GUI_BG)
        hf.pack(fill="x")
        self._toggle_btn = tk.Button(
            hf, text="▶" if collapsed else "▼",
            bg=config.GUI_BG, fg=config.GUI_ACC, font=("Segoe UI", 9),
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle)
        self._toggle_btn.pack(side="left")
        tk.Label(hf, text=text, bg=config.GUI_BG, fg=config.GUI_ACC,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=4)
        # 区切り線
        tk.Frame(hf, bg=config.GUI_SUB, height=1).pack(side="left", fill="x", expand=True, pady=6)
        # コンテンツ領域
        self.content = tk.Frame(self, bg=config.GUI_BG)
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        icon_path = resource_path("ToNAutoBeginnerIcon.ico")
        if icon_path.exists():
            self.iconbitmap(default=str(icon_path))

        self.title("ToNAutoBeginner")
        self.geometry("980x1080")
        self.minsize(880, 900)
        self.configure(bg=config.GUI_BG)
        self.v_tnl       = tk.StringVar()
        self.v_win_count = tk.IntVar(value=4)
        self.keepOn_set: dict = {}
        self.monitors: list[LogMonitor.LogMonitor] = []
        self._running = False
        self._overlay: LogOverlay | None = None
        self._log_line_count = 0
        self._emergency_stop_key_pressed = False
        self._entry_stop = threading.Event()   # 入室時自動操作の中断フラグ
        self._launched_tab_indices: list[int] | None = None  # 今回起動した窓タブ
        self._build_ui()
        self._load_saved_settings()
        self._auto_detect_windows()
        self._sync_launch_count()
        AutoUpdate.cleanup_old_exe()
        self._start_update_check()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_emergency_stop_polling()

    def _start_emergency_stop_polling(self):
        if keyboard is None:
            return
        self.after(config.EMERGENCY_STOP_POLL_MS, self._poll_emergency_stop_key)

    def _poll_emergency_stop_key(self):
        try:
            now = keyboard.is_pressed(config.EMERGENCY_STOP_KEY)
            if now and not self._emergency_stop_key_pressed:
                self._log("[緊急停止] Pキーが押されました")
                self.after(0, self._stop)
            self._emergency_stop_key_pressed = now
        except Exception:
            pass

        try:
            self.after(config.EMERGENCY_STOP_POLL_MS, self._poll_emergency_stop_key)
        except tk.TclError:
            pass

    def _build_ui(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",            background=config.GUI_BG)
        s.configure("TLabel",            background=config.GUI_BG, foreground=config.GUI_FG, font=("Segoe UI", 10))
        s.configure("TButton",           font=("Segoe UI", 10, "bold"), padding=4)
        s.configure("TCheckbutton",      background=config.GUI_BG, foreground=config.GUI_FG, font=("Segoe UI", 10))
        s.map("TCheckbutton", background=[("active", config.GUI_BG)])
        s.configure("TLabelframe",       background=config.GUI_BG, foreground=config.GUI_ACC)
        s.configure("TLabelframe.Label", background=config.GUI_BG, foreground=config.GUI_ACC,
                    font=("Segoe UI", 10, "bold"))
        s.configure("TEntry",            fieldbackground=config.GUI_SUB, foreground=config.GUI_FG)
        s.configure("TSpinbox",          fieldbackground=config.GUI_SUB, foreground=config.GUI_FG)
        s.configure("TNotebook",         background=config.GUI_BG, tabmargins=[2, 2, 2, 0])
        s.configure("TNotebook.Tab",     background=config.GUI_SUB, foreground=config.GUI_FG, padding=[10, 4])
        s.map("TNotebook.Tab",
              background=[("selected", config.GUI_ACC)], foreground=[("selected", config.GUI_BG)])
        s.configure("TSeparator",        background=config.GUI_SUB)

        # ① TNL
        f1 = ttk.LabelFrame(self, text="① 続行リスト(.tnl)", padding=8)
        f1.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Entry(f1, textvariable=self.v_tnl, width=58).pack(side="left", padx=(0, 6))
        ttk.Button(f1, text="参照…",    command=self._browse_tnl).pack(side="left")
        ttk.Button(f1, text="再読み込み", command=self._load_tnl).pack(side="left", padx=(4, 0))
        self.lbl_tnl = ttk.Label(f1, text="未読み込み", foreground=config.GUI_RED)
        self.lbl_tnl.pack(side="left", padx=(10, 0))

        # ② 自爆キー設定
        fk = ttk.Frame(self)
        fk.pack(fill="x", padx=12, pady=(0, 4))
        ttk.Label(fk, text="自爆キー:").pack(side="left")
        self.v_suicide_key = tk.StringVar(value=config.SELF_SUICIDE_KEY)
        ek = ttk.Entry(fk, textvariable=self.v_suicide_key, width=6)
        ek.pack(side="left", padx=(6, 4))
        ttk.Button(fk, text="適用",
                   command=lambda: SharedState.set_suicide_key(self.v_suicide_key.get().strip())
                   ).pack(side="left")

        # ③ 窓数・ログ
        f2 = CollapsibleFrame(self, text="② 窓数・ログ設定")
        f2.pack(fill="x", padx=12, pady=4)
        f2 = f2.content  # 以降はcontent内に追加
        wf = ttk.Frame(f2)
        wf.pack(fill="x")
        ttk.Label(wf, text="窓数:").pack(side="left")
        sb = ttk.Spinbox(wf, from_=1, to=config.MAX_WINDOWS,
                         textvariable=self.v_win_count, width=4,
                         command=self._on_win_count_change)
        sb.pack(side="left", padx=(4, 16))
        sb.bind("<FocusOut>", lambda e: self._on_win_count_change())
        ttk.Button(wf, text="📄 最新ログを自動割り当て",
                   command=self._assign_logs).pack(side="left")

        # ── VRChat起動 ──
        ttk.Separator(f2, orient="horizontal").pack(fill="x", pady=6)
        lf1 = ttk.Frame(f2)
        lf1.pack(fill="x")
        self.btn_launch = ttk.Button(lf1, text="🚀 VRChatを起動", command=self._launch_vrchat)
        self.btn_launch.pack(side="left")
        ttk.Label(lf1, text="起動する窓数:").pack(side="left", padx=(12, 0))
        self.v_launch_count = tk.IntVar(value=0)
        ttk.Spinbox(lf1, from_=0, to=config.MAX_WINDOWS,
                    textvariable=self.v_launch_count,
                    width=4).pack(side="left", padx=(4, 0))
        ttk.Label(lf1, text="※ 既定は「窓数 − 起動済みの窓数」",
                  foreground=config.GUI_YLW).pack(side="left", padx=(4, 0))
        # デスクトップモードとOSCポート割り当ては常時有効（設定不要のため非表示）
        self.v_desktop_mode = tk.BooleanVar(value=True)
        self.v_use_osc = tk.BooleanVar(value=True)
        self.btn_stop_entry = ttk.Button(
            lf1, text="■ 入室操作を中止", command=self._cancel_ton_entry, state="disabled")
        self.btn_stop_entry.pack(side="left", padx=(10, 0))
        self.lbl_launch = ttk.Label(lf1, text="", foreground=config.GUI_GRN)
        self.lbl_launch.pack(side="left", padx=(10, 0))

        lf2 = ttk.Frame(f2)
        lf2.pack(fill="x", pady=(4, 0))
        self.v_join_world = tk.BooleanVar(value=False)
        ttk.Checkbutton(lf2, text="ToNへ自動的にJoin",
                        variable=self.v_join_world).pack(side="left")
        self.v_instance_link = tk.StringVar()
        ttk.Entry(lf2, textvariable=self.v_instance_link, width=50).pack(side="left", padx=(6, 4))
        ttk.Button(lf2, text="最新ログから取得",
                   command=self._fill_instance_link_from_log).pack(side="left")
        ttk.Label(lf2, text="※ 空欄ならToNの新規インスタンスを自動生成（窓ごとに別インスタンス）",
                  foreground=config.GUI_YLW).pack(side="left", padx=(8, 0))

        lf25 = ttk.Frame(f2)
        lf25.pack(fill="x", pady=(4, 0))
        self.v_ton_entry = tk.BooleanVar(value=config.TON_ENTRY_ENABLED)
        ttk.Checkbutton(lf25, text="入室後の選択画面を自動突破",
                        variable=self.v_ton_entry).pack(side="left")
        self.v_ton_begin = tk.BooleanVar(value=config.TON_ENTRY_BEGIN)
        ttk.Checkbutton(lf25, text="続けてBeginまで押す",
                        variable=self.v_ton_begin).pack(side="left", padx=(12, 0))
        ttk.Label(lf25, text="※ 警告同意→Casual→BGMあり→LET ME PLAY の順に押します",
                  foreground=config.GUI_YLW).pack(side="left", padx=(10, 0))

        lf3 = ttk.Frame(f2)
        lf3.pack(fill="x", pady=(2, 0))
        ttk.Label(lf3, text="起動exe:").pack(side="left")
        self.v_vrchat_exe = tk.StringVar()
        ttk.Entry(lf3, textvariable=self.v_vrchat_exe, width=56).pack(side="left", padx=(4, 4))
        ttk.Button(lf3, text="…", width=3, command=self._browse_vrchat_exe).pack(side="left")
        ttk.Label(lf3, text="※ 空欄ならSteamから自動検出",
                  foreground=config.GUI_YLW).pack(side="left", padx=(6, 0))


        self.lbl_win_warn = ttk.Label(
            f2, text="※ 窓数はマクロ起動前に設定してください", foreground=config.GUI_YLW)
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
        self.v_voice_8pages       = tk.StringVar(value=config.VOICE_8PAGES)
        self.v_voice_punish       = tk.StringVar(value=config.VOICE_PUNISH)
        voice_row(fv, "続行ラウンド:", self.v_voice_continue)
        voice_row(fv, "霧ラウンド:", self.v_voice_fog)
        voice_row(fv, "アイテムロスト:", self.v_voice_item_lost)
        voice_row(fv, "Intermission:", self.v_voice_intermission)
        voice_row(fv, "Foxy:", self.v_voice_foxy)
        voice_row(fv, "8 Pages(速度先読み):", self.v_voice_8pages)
        voice_row(fv, "Punish(速度先読み):", self.v_voice_punish)

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
                  foreground=config.GUI_ORG).pack(side="left", padx=(10, 0))

        # 完全放置モード（全窓共通）
        fhf = ttk.Frame(self)
        fhf.pack(pady=(0, 4))
        self.btn_hands_free = tk.Button(
            fhf, text="🤖 完全放置モード: OFF（全窓共通）",
            bg=config.GUI_SUB, fg=config.GUI_FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_hands_free)
        self.btn_hands_free.pack(side="left")
        ttk.Label(fhf, text="← ONにするとアイテムロストを無視・全ラウンド即自爆"
                            "（プライベート系の窓でのみ機能します）",
                  foreground=config.GUI_YLW).pack(side="left", padx=(10, 0))

        # アイテム取得→Begin
        fif = ttk.Frame(self)
        fif.pack(pady=(0, 4))
        self.btn_item_get_begin = tk.Button(
            fif, text="🎯 アイテム取得→Begin: OFF（全窓共通）",
            bg=config.GUI_SUB, fg=config.GUI_FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_item_get_begin)
        self.btn_item_get_begin.pack(side="left")
        ttk.Label(fif, text="← ONにするとアイテムロストラウンド開始時にフォーカス＆フリーズ",
                  foreground=config.GUI_YLW).pack(side="left", padx=(10, 0))

        # 速度によるラウンド種別の検知（全窓共通）
        fsd = ttk.Frame(self)
        fsd.pack(pady=(0, 4))
        self.btn_speed_detect = tk.Button(
            fsd, text="", bg=config.GUI_SUB, fg=config.GUI_FG,
            font=("Segoe UI", 11, "bold"), relief="raised", padx=12, pady=5,
            command=self._toggle_speed_detect)
        self.btn_speed_detect.pack(side="left")
        ttk.Label(fsd, text="← Begin受理後の移動速度で 8 Pages / Punished を判定"
                            "（判定は全インスタンス・横移動はプライベートのみ）",
                  foreground=config.GUI_YLW).pack(side="left", padx=(10, 0))
        self._refresh_speed_detect_button()

        # ログ
        fl = ttk.LabelFrame(self, text="ログ出力", padding=4)
        fl.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(
            fl, height=16, bg="#181825", fg=config.GUI_FG, width=80,
            font=("Consolas", 9), state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(fl, text="クリア", command=self._clear_log).pack(anchor="e", pady=(2, 0))

        # クレジット表示
        tk.Label(self, text="Credit: VOICEVOX冥鳴ひまり",
                 bg=config.GUI_BG, fg=config.GUI_SUB, font=("Segoe UI", 8)).pack(anchor="e", padx=12)

    def _rebuild_tabs(self, count: int):
        for tab in self.tabs:
            try:
                self.nb.forget(tab)
            except tk.TclError:
                pass
            tab.destroy()
        self.tabs.clear()
        for i in range(count):
            tab = WindowTab(self.nb, i, on_log_selected=self._on_tab_log_selected)
            self.nb.add(tab, text=f"窓{i + 1}")
            self.tabs.append(tab)
        self._apply_saved_profiles()

    def _on_win_count_change(self):
        if self._running:
            return
        try:
            n = max(1, min(config.MAX_WINDOWS, int(self.v_win_count.get())))
        except (ValueError, tk.TclError):
            n = 1
        self.v_win_count.set(n)
        self._sync_launch_count()
        if len(self.tabs) == n:
            return
        self._rebuild_tabs(n)

    def _sync_launch_count(self):
        """起動する窓数を「窓数 − 既に開いているVRChat窓数」に合わせる。

        手で入れ直した値は次に窓数を変えるかVRChatを起動するまで保たれる。
        """
        if self._running:
            return
        try:
            win_count = int(self.v_win_count.get())
        except (ValueError, tk.TclError):
            return
        opened = len(VRChatDiscovery.get_vrchat_windows_by_start_time(config.MAX_WINDOWS))
        self.v_launch_count.set(launch_window_count(win_count, opened))

    def _launch_count_value(self, max_count: int) -> int:
        """入力された起動窓数を 0〜max_count に収めて返す"""
        try:
            n = int(self.v_launch_count.get())
        except (ValueError, tk.TclError):
            n = 0
        n = max(0, min(max_count, n))
        self.v_launch_count.set(n)
        return n

    def _toggle_item_get_begin(self):
        val = not SharedState.get_item_begin_mode()
        SharedState.set_item_begin_mode(val)
        if val:
            self.btn_item_get_begin.config(
                text="🎯 アイテム取得→Begin: ON（全窓共通）",
                bg="#1a3a5a", fg="#89b4fa", relief="sunken")
            self._log("[アイテム取得→Begin] ON: ラウンド開始時にフォーカス＆フリーズ")
        else:
            self.btn_item_get_begin.config(
                text="🎯 アイテム取得→Begin: OFF（全窓共通）",
                bg=config.GUI_SUB, fg=config.GUI_FG, relief="raised")
            self._log("[アイテム取得→Begin] OFF")

    def _refresh_speed_detect_button(self):
        on = SharedState.get_speed_detect()
        self.btn_speed_detect.config(
            text=f"⏩ 速度でラウンド種別を検知: {'ON' if on else 'OFF'}（全窓共通）",
            bg="#1a3a2a" if on else config.GUI_SUB,
            fg="#a6e3a1" if on else config.GUI_FG,
            relief="sunken" if on else "raised")

    def _toggle_speed_detect(self):
        val = not SharedState.get_speed_detect()
        SharedState.set_speed_detect(val)
        self._refresh_speed_detect_button()
        self._log("[速度検知] ON: Begin受理後にラウンド種別を判定します"
                  if val else "[速度検知] OFF")

    def _toggle_hands_free(self):
        # ONにはいつでもできるが、効くのはprivate系インスタンスに居る窓だけ。
        # 干し芋の窓とプラベの窓を同時に監視することがあるため、窓ごとに判定する。
        val = not SharedState.get_hands_free()
        SharedState.set_hands_free(val)
        if val:
            self.btn_hands_free.config(
                text="🤖 完全放置モード: ON（全窓共通）",
                bg="#3a1a1a", fg=config.GUI_RED, relief="sunken")
            self._log("[放置モード] ON: アイテムロスト無視・全ラウンド即自爆・アナウンス停止"
                      "（プライベート系の窓のみ）")
        else:
            self.btn_hands_free.config(
                text="🤖 完全放置モード: OFF（全窓共通）",
                bg=config.GUI_SUB, fg=config.GUI_FG, relief="raised")
            self._log("[放置モード] OFF")

    def _browse_tnl(self):
        p = filedialog.askopenfilename(
            title="tnlファイルを選択",
            filetypes=[("TNL files", "*.tnl"), ("All", "*.*")]
        )
        if p:
            self.v_tnl.set(p)
            self._load_tnl()

    def _load_tnl(self, show_error: bool = True):
        p = self.v_tnl.get().strip()
        if not p or not Path(p).exists():
            if show_error:
                messagebox.showerror("エラー", "tnlファイルが見つかりません")
            else:
                self._log(f"[TNL] 前回のtnlが見つかりません: {p}")
            return
        try:
            self.keepOn_set, meta = MatchTNL.load_tnl(p)
            total = sum(len(v) for v in self.keepOn_set.values())
            msg = f"[{meta['list_name']}] {len(self.keepOn_set)}ラウンド / {total}件 スキップ対象"
            self.lbl_tnl.config(text=msg, foreground=config.GUI_GRN)
            self._log(f"[TNL] {msg}")
            save_settings({**load_settings(), "tnl_path": p})
        except Exception as e:
            if show_error:
                messagebox.showerror("TNL読み込みエラー", str(e))
            else:
                self._log(f"[TNL] 読み込みエラー: {e}")

    def _load_saved_settings(self):
        """起動時: 前回選んだtnlを復元して即読み込む"""
        data = load_settings()
        self.v_vrchat_exe.set(data.get("vrchat_exe", ""))
        self.v_desktop_mode.set(bool(data.get("desktop_mode", config.LAUNCH_DESKTOP_MODE)))
        self.v_use_osc.set(bool(data.get("use_osc", config.OSC_ENABLED)))
        self.v_ton_entry.set(bool(data.get("ton_entry", config.TON_ENTRY_ENABLED)))
        self.v_ton_begin.set(bool(data.get("ton_begin", config.TON_ENTRY_BEGIN)))
        self.v_join_world.set(bool(data.get("join_world", False)))
        self.v_instance_link.set(data.get("instance_link", ""))
        self._saved_profiles = data.get("profiles", [])
        self._saved_freeze_8pages = data.get("freeze_8pages", [])
        self._saved_freeze_punish = data.get("freeze_punish", [])
        self._saved_freeze_rounds = data.get("freeze_rounds", [])
        self._apply_saved_profiles()
        tnl_path = data.get("tnl_path", "")
        if not tnl_path:
            return
        self.v_tnl.set(tnl_path)
        self._load_tnl(show_error=False)

    def _apply_saved_profiles(self):
        """保存済みの窓ごと設定（profile ID・フリーズ設定）を反映する"""
        for tab, pid in zip(self.tabs, getattr(self, "_saved_profiles", [])):
            try:
                tab.v_profile.set(int(pid))
            except (ValueError, tk.TclError):
                pass
        for name, saved in (("v_freeze_8pages", "_saved_freeze_8pages"),
                            ("v_freeze_punish", "_saved_freeze_punish")):
            for tab, val in zip(self.tabs, getattr(self, saved, [])):
                try:
                    getattr(tab, name).set(bool(val))
                except (ValueError, tk.TclError):
                    pass
        for tab, names in zip(self.tabs, getattr(self, "_saved_freeze_rounds", [])):
            for name, var in tab.v_freeze_rounds.items():
                try:
                    var.set(name in (names or []))
                except (ValueError, tk.TclError):
                    pass

    def _auto_detect_windows(self):
        """起動時: VRChatウィンドウ数を検出して窓数へ反映し、
        起動時刻を使ってHWNDとログを全窓ぶん自動割り当てする。"""
        windows = VRChatDiscovery.get_vrchat_windows_by_start_time(config.MAX_WINDOWS)
        if not windows:
            self._log("[起動時検出] VRChatウィンドウ未検出（窓数は手動で設定してください）")
            return
        n = len(windows)
        self.v_win_count.set(n)
        if len(self.tabs) != n:
            self._rebuild_tabs(n)
        self._log(f"[起動時検出] VRChatウィンドウを{n}窓検出 → 窓数を{n}に設定")
        self._assign_windows_and_logs(windows)

    def _assign_windows_and_logs(self, windows: list):
        """VRChatの起動時刻とログファイル名の時刻を突き合わせて
        HWNDとログを窓タブへ1対1で割り当てる（Zオーダーに依存しない）"""
        hwnds = [h for h, _t in windows]
        candidates = VRChatDiscovery.find_latest_logs(
            config.VRCHAT_LOG_DIR, config.LOG_MATCH_CANDIDATE_COUNT)
        matched = VRChatDiscovery.match_windows_to_logs(
            windows, candidates, config.LOG_MATCH_TOLERANCE_SEC)

        active_tabs = [tab for tab in self.tabs if tab.v_active.get()]
        for i, tab in enumerate(active_tabs):
            if i >= len(windows):
                break
            tab.set_hwnd_choices(hwnds, selected_hwnd=hwnds[i])
            log_path = matched[i]
            if log_path is None:
                self._log(f"[窓{tab.idx+1}] HWND={hwnds[i]:#010x} → 対応するログが見つかりません")
                continue
            tab.v_log.set(str(log_path))
            source = "起動時刻一致" if windows[i][1] is not None else "起動時刻不明のため順番で割当"
            self._log(f"[窓{tab.idx+1}] HWND={hwnds[i]:#010x} → {log_path.name}（{source}）")
            self._on_tab_log_selected(tab)

    def _on_tab_log_selected(self, tab: WindowTab):
        """ログ選択時: ログ末尾からインスタンスタイプを検出し、
        干し芋/焼き芋なら干し芋自動自爆を自動ONにする"""
        p = tab.v_log.get().strip()
        if not p:
            return
        itype = LogMonitor.LogMonitor.detect_instance_type_from_log(Path(p))
        if itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO) and not tab.v_hoshiimo.get():
            tab.v_hoshiimo.set(True)
            self._log(f"[窓{tab.idx+1}] 干し芋/焼き芋インスタンス検出 → 干し芋自動自爆をON")

    def _assign_logs(self):
        """
        VRChatの起動時刻とログの時刻を突き合わせてHWND・ログを一括割り当てる。
        窓の並び順（Zオーダー）に依存しないため、窓を切り替えた後でも正しく対応する。
        """
        windows = VRChatDiscovery.get_vrchat_windows_by_start_time(self.v_win_count.get())
        if not windows:
            self._log("[自動割り当て] VRChatウィンドウが見つかりません")
            return
        self._assign_windows_and_logs(windows)
        self._log(f"[自動割り当て] {min(len(windows), len(self.tabs))}窓に割り当てました")

    def _start(self):
        if not self.keepOn_set:
            # tnl無しでも開始できる。続行リストが空＝tnlからの続行は0件として動く
            # （霧ラウンドや3クラ解放など、tnl以外を根拠にした続行はそのまま効く）
            self._log("[起動] tnl未読み込み → tnlからの続行は0件として動作します")

        # 有効な窓のログが空なら自動割り当て
        active_tabs = [tab for tab in self.tabs if tab.v_active.get()]
        logs = VRChatDiscovery.find_latest_logs(config.VRCHAT_LOG_DIR, len(active_tabs))
        log_idx = 0
        for tab in active_tabs:
            if not tab.v_log.get().strip() and log_idx < len(logs):
                tab.v_log.set(str(logs[log_idx]))
                self._log(f"[窓{tab.idx+1}] ログを自動割り当て: {logs[log_idx].name}")
                self._on_tab_log_selected(tab)
            log_idx += 1

        self.monitors.clear()
        for tab in self.tabs:
            cfg, err = tab.get_config()
            if cfg is not None and cfg.hwnd:
                # OSC可否は起動時に1回だけ確定させる。このツールが --osc= を
                # 付けて起動した窓だけが該当ポートを掴んでいる。手動起動の
                # 2窓目以降はポート競合でOSCが無効なので従来方式になる。
                port, _out = OSCClient.ports_for_window(tab.idx)
                if OSCClient.osc_available_for(cfg.hwnd, tab.idx):
                    cfg.osc_port = port
                    self._log(f"[窓{tab.idx+1}] OSC利用可（ポート{port}）→ 移動はOSC、排他はクリックと自爆のみ")
                else:
                    cfg.osc_port = 0
                    self._log(f"[窓{tab.idx+1}] OSC利用不可 → 従来どおりキー操作（全体を排他）")
            if cfg is None:
                if err:
                    self._log(f"[窓{tab.idx+1}] スキップ: {err}")
                continue
            if cfg.hwnd == 0:
                self._log(f"[窓{tab.idx+1}] ⚠ HWNDが未選択です。窓タブで「🔄 更新」してVRChatウィンドウを選択してください")
                continue
            # 音声ファイルパスをAppのGUI設定から注入
            cfg.voice_continue     = self.v_voice_continue.get().strip()
            cfg.voice_fog          = self.v_voice_fog.get().strip()
            cfg.voice_item_lost    = self.v_voice_item_lost.get().strip()
            cfg.voice_intermission = self.v_voice_intermission.get().strip()
            cfg.voice_foxy          = self.v_voice_foxy.get().strip()
            cfg.voice_8pages        = self.v_voice_8pages.get().strip()
            cfg.voice_punish        = self.v_voice_punish.get().strip()
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
            text="⚠ 動作中です。窓数はマクロ停止後に変更できます", foreground=config.GUI_RED)
        self._log(f"[起動] {len(self.monitors)}窓の監視を開始")

    def _stop(self):
        self._entry_stop.set()                   # 入室時自動操作も中断する
        SharedState.equip_freeze_reset()         # フリーズ中でも確実に解除
        SharedState.continue_round_reset()       # 続行ラウンドフリーズも解除
        SharedState.speed_freeze_reset()         # 速度検知フリーズも解除
        SharedState.round_freeze_reset()         # ラウンド突入フリーズも解除
        for m in self.monitors:
            m.stop()
        self.monitors.clear()
        self._running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_win_warn.config(
            text="※ 窓数はマクロ起動前に設定してください", foreground=config.GUI_YLW)
        self._log("[停止] マクロを停止しました")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}\n"
        def _a():
            try:
                self._append_log_text(line)
                if self._overlay and not self._overlay._closed:
                    self._overlay.append(f"[{ts}] {msg}")
            except tk.TclError:
                pass
        try:
            self.after(0, _a)
        except tk.TclError:
            pass

    def _append_log_text(self, line: str):
        self.log_text.config(state="normal")
        try:
            self.log_text.insert("end", line)
            self._log_line_count += max(1, line.count("\n"))
            excess = self._log_line_count - max(1, config.GUI_LOG_MAX_LINES)
            if excess > 0:
                self.log_text.delete("1.0", f"{excess + 1}.0")
                self._log_line_count -= excess
            self.log_text.see("end")
        finally:
            self.log_text.config(state="disabled")

    # ── 自動アップデート ──────────────────────
    def _start_update_check(self):
        """起動時にGitHub Releasesの最新版をバックグラウンドで確認する"""
        if AutoUpdate.current_exe_path() is None:
            return  # 開発実行(python直起動)時は無効

        def worker():
            release = AutoUpdate.fetch_latest_release()
            if not release:
                return
            tag = release.get("tag_name", "")
            if not AutoUpdate.is_newer(tag, config.APP_VERSION):
                return
            asset = AutoUpdate.find_exe_asset(release)
            try:
                if asset is None:
                    self._log(f"[更新] 新バージョン {tag} を検出しましたが、"
                              f"リリースに {config.UPDATE_ASSET_NAME} が添付されていません")
                    return
                self.after(0, lambda: self._prompt_update(tag, asset))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _prompt_update(self, tag: str, asset: tuple):
        if self._running:
            self._log(f"[更新] 新バージョン {tag} があります（マクロ動作中のため更新は保留）")
            return
        ok = messagebox.askyesno(
            "アップデート",
            f"新しいバージョン {tag} があります。\n"
            f"（現在: {config.APP_VERSION}）\n\n"
            "ダウンロードして再起動しますか？")
        if not ok:
            self._log(f"[更新] {tag} への更新をスキップしました")
            return
        url, size = asset
        self._log(f"[更新] {tag} をダウンロード中…")

        def worker():
            tmp = AutoUpdate.download_to_temp(url, size)
            try:
                self.after(0, lambda: self._finish_update(tag, tmp))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update(self, tag: str, tmp):
        if tmp is None:
            self._log("[更新] ダウンロード失敗")
            messagebox.showerror(
                "アップデート失敗",
                "ダウンロードに失敗しました。\nGitHubのリリースページから手動で更新してください。")
            return
        exe = AutoUpdate.current_exe_path()
        if exe is None or not AutoUpdate.apply_update(tmp, exe):
            self._log("[更新] EXE置き換え失敗")
            messagebox.showerror(
                "アップデート失敗",
                "ファイルの置き換えに失敗しました。\nGitHubのリリースページから手動で更新してください。")
            return
        messagebox.showinfo("アップデート完了", f"{tag} へ更新しました。再起動します。")
        AutoUpdate.restart_to_new_exe(exe)
        self._on_close()

    # ── VRChat起動 ─────────────────────────────
    def _browse_vrchat_exe(self):
        p = filedialog.askopenfilename(
            title="VRChatの起動exeを選択（launch.exe）",
            filetypes=[("VRChatランチャー", "launch.exe"), ("実行ファイル", "*.exe"), ("All", "*.*")])
        if p:
            self.v_vrchat_exe.set(p)

    def _fill_instance_link_from_log(self):
        """最新ログの直近のJoining行から参加リンクを取り出す"""
        logs = VRChatDiscovery.find_latest_logs(config.VRCHAT_LOG_DIR, 1)
        if not logs:
            self._log("[起動] ログが見つかりません")
            return
        link = VRChatLauncher.instance_link_from_log(logs[0])
        if not link:
            self._log("[起動] %s に参加履歴が見つかりません" % logs[0].name)
            return
        self.v_instance_link.set(link)
        self._log("[起動] 参加リンクを取得: %s" % link)

    def _launch_vrchat(self):
        if self._running:
            messagebox.showwarning("警告", "マクロ動作中は起動できません。先に停止してください")
            return
        exe = VRChatLauncher.resolve_vrchat_exe(self.v_vrchat_exe.get())
        if exe is None:
            messagebox.showerror(
                "エラー",
                "VRChatのlaunch.exeが見つかりません。\n「起動exe」欄でパスを指定してください")
            return

        base_link = None
        if self.v_join_world.get():
            raw = self.v_instance_link.get().strip()
            if raw:
                base_link = VRChatLauncher.normalize_instance_link(raw)
                if base_link is None:
                    messagebox.showerror(
                        "エラー",
                        "参加リンクを解釈できません。\n"
                        "vrchat://launch?... 形式か wrld_xxx:12345~... 形式で指定してください")
                    return
            else:
                user_id = VRChatLauncher.latest_user_id(config.VRCHAT_LOG_DIR)
                if not user_id:
                    messagebox.showerror(
                        "エラー",
                        "自分のユーザーIDを検出できませんでした。\n"
                        "一度VRChatにログインするか、参加リンクを直接入力してください")
                    return
                base_link = VRChatLauncher.build_ton_link(user_id)
                self._log("[起動] ToNの新規インスタンスを生成します（%s）" % user_id)

        tabs = [tab for tab in self.tabs if tab.v_active.get()]
        if not tabs:
            messagebox.showerror("エラー", "有効な窓がありません")
            return

        desktop = self.v_desktop_mode.get()
        baseline = {h for h, _t in VRChatDiscovery.get_vrchat_windows_by_start_time(config.MAX_WINDOWS)}
        count = self._launch_count_value(len(tabs))
        if count <= 0:
            messagebox.showinfo(
                "情報",
                "起動する窓数が0です。\n"
                "すでに必要な数のVRChatが開いているか、起動する窓数を0にしています。")
            return
        # 既存の窓は先頭のタブに割り当てられるので、新しく開くのは後ろのタブ
        plan_tabs = tabs_to_launch(tabs, count)
        # Tkinter変数はメインスレッドでのみ読めるため、起動計画をここで確定させる
        # (表示用の窓番号, プロファイルID, OSCポート割り当て用のタブ番号)
        launch_plan = build_launch_plan(plan_tabs)
        # 入室時の自動操作は今回起動した窓だけに行う（既に入室済みの窓は対象外）
        self._launched_tab_indices = [tab.idx for tab in plan_tabs]
        existing = len(baseline)
        use_osc = self.v_use_osc.get()
        self.btn_launch.config(state="disabled")
        self.lbl_launch.config(text="起動中…", foreground=config.GUI_YLW)
        self._save_launch_settings()

        def worker():
            try:
                # まとめて投げると取りこぼす窓が出るので、1窓ずつ出現を待ってから次へ。
                # 待ち切れなくても次に進む（残りの窓まで巻き添えにしない）。
                for i, (window_no, profile_id, osc_index) in enumerate(launch_plan):
                    # 同じprivateインスタンスにはオーナー以外入れないため窓ごとに分ける
                    link = (VRChatLauncher.with_unique_instance(base_link, osc_index)
                            if base_link else None)
                    args = VRChatLauncher.launch_one(
                        exe, profile_id, desktop, link,
                        osc_index=osc_index if use_osc else None)
                    self._log("[起動] 窓%d: %s" % (window_no, " ".join(args[1:])))
                    appeared = VRChatLauncher.wait_for_windows(
                        baseline, i + 1, config.LAUNCH_EACH_WINDOW_TIMEOUT)
                    if len(appeared) < i + 1:
                        self._log("[起動] 窓%dのウィンドウが現れませんでした（先へ進みます）"
                                  % window_no)
                    if i < len(launch_plan) - 1:
                        time.sleep(config.LAUNCH_STAGGER_SEC)
                self._log("[起動] %d窓を起動。ウィンドウ出現を待っています…" % len(launch_plan))
                found = VRChatLauncher.wait_for_windows(
                    baseline, len(launch_plan), config.LAUNCH_WINDOW_TIMEOUT)
                n = len(found)
                self.after(0, lambda: self._on_launch_finished(n, len(launch_plan), existing))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._on_launch_error(msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_launch_finished(self, found: int, expected: int, existing: int = 0):
        self.btn_launch.config(state="normal")
        if found < expected:
            self.lbl_launch.config(
                text="%d/%d窓のみ検出" % (found, expected), foreground=config.GUI_ORG)
            self._log("[起動] %d/%d窓しか検出できませんでした" % (found, expected))
        else:
            self.lbl_launch.config(text="%d窓 起動完了" % found, foreground=config.GUI_GRN)
        self._sync_launch_count()   # 起動済みが増えたぶん既定値を引き直す
        self._log("[起動] ログ生成を待っています…")
        # ログが揃ったかは既存の窓も含めた合計で判定する
        self._wait_logs_then_assign(existing + found, 0.0)

    def _wait_logs_then_assign(self, expected: int, waited: float):
        """ログファイルが起動時刻で紐づけられるようになり次第、割り当てる。
        固定待ちだとウィンドウ出現から無駄に待つため、準備でき次第すぐ進める。"""
        windows = VRChatDiscovery.get_vrchat_windows_by_start_time(config.MAX_WINDOWS)
        candidates = VRChatDiscovery.find_latest_logs(
            config.VRCHAT_LOG_DIR, config.LOG_MATCH_CANDIDATE_COUNT)
        ready = VRChatDiscovery.count_time_matched_logs(
            windows, candidates, config.LOG_MATCH_TOLERANCE_SEC)
        if ready >= expected:
            self._log("[起動] ログ生成を確認（%.1f秒）" % waited)
            self._assign_logs()
            self._report_join_status()
            self._run_ton_entry()
            return
        if waited >= config.LAUNCH_LOG_TIMEOUT:
            self._log("[起動] ログ生成待ちがタイムアウト（%d/%d）" % (ready, expected))
            self._assign_logs()
            self._report_join_status()
            self._run_ton_entry()
            return
        self.after(int(config.LAUNCH_LOG_POLL_SEC * 1000),
                   lambda: self._wait_logs_then_assign(expected, waited + config.LAUNCH_LOG_POLL_SEC))

    def _report_join_status(self):
        """どの窓がToNに入れたかをログに出す。

        まとめて起動すると参加リンクを取りこぼす窓が出るため、
        入れていない窓を名指しで分かるようにする。
        """
        missing = []
        for tab in self.tabs:
            if not tab.v_active.get():
                continue
            p = tab.v_log.get().strip()
            if not p:
                continue
            if VRChatLauncher.joined_ton(p):
                self._log(f"[起動] 窓{tab.idx + 1} ToN入室を確認")
            else:
                missing.append(tab.idx + 1)
        if missing:
            self._log("[起動] ⚠ ToNに入れていない窓: "
                      + " / ".join(f"窓{n}" for n in missing)
                      + " → 手動でJoinするか、その窓だけ起動し直してください")
        return missing

    def _cancel_ton_entry(self):
        """入室時自動操作を中断する"""
        if not self._entry_stop.is_set():
            self._entry_stop.set()
            self._log("[入室操作] 中止を要求しました")
        self.btn_stop_entry.config(state="disabled")

    def _run_ton_entry(self):
        """入室後の選択画面を自動突破する（窓ごとに順番に実行）"""
        if not self.v_ton_entry.get():
            self._log("[入室操作] 設定がOFFのためスキップします")
            return
        launched = getattr(self, "_launched_tab_indices", None)
        targets = [(tab.idx + 1, tab._get_selected_hwnd())
                   for tab in self.tabs
                   if tab.v_active.get() and (launched is None or tab.idx in launched)]
        targets = [(no, h) for no, h in targets if h]
        if not targets:
            self._log("[入室操作] 対象の窓がありません")
            return
        press_begin = self.v_ton_begin.get()
        self._entry_stop.clear()
        self.btn_stop_entry.config(state="normal")
        self._log("[入室操作] 開始します（中止は「入室操作を中止」ボタンか %sキー）"
                  % config.EMERGENCY_STOP_KEY.upper())

        def worker():
            try:
                for window_no, hwnd in targets:
                    if self._entry_stop.is_set():
                        self._log("[入室操作] 中止しました")
                        return
                    entry = ToNEntry.ToNEntry(
                        hwnd,
                        window_index=window_no - 1,   # OSCポートの割り当てに使う
                        log=lambda m, n=window_no: self._log("[窓%d] %s" % (n, m)),
                        is_running=lambda: not self._entry_stop.is_set(),
                    )
                    try:
                        if entry.run() and press_begin and not self._entry_stop.is_set():
                            entry.press_begin()
                    except Exception as e:
                        self._log("[窓%d] 入室操作でエラー: %s" % (window_no, e))
                    finally:
                        entry.close()
                self._log("[入室操作] 完了")
            finally:
                try:
                    self.after(0, lambda: self.btn_stop_entry.config(state="disabled"))
                except tk.TclError:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_launch_error(self, msg: str):
        self.btn_launch.config(state="normal")
        self.lbl_launch.config(text="起動失敗", foreground=config.GUI_RED)
        self._log("[起動] 失敗: %s" % msg)
        messagebox.showerror("起動失敗", msg)

    def _save_launch_settings(self):
        save_settings({
            **load_settings(),
            "vrchat_exe":    self.v_vrchat_exe.get().strip(),
            "desktop_mode":  self.v_desktop_mode.get(),
            "use_osc":       self.v_use_osc.get(),
            "ton_entry":     self.v_ton_entry.get(),
            "ton_begin":     self.v_ton_begin.get(),
            "join_world":    self.v_join_world.get(),
            "instance_link": self.v_instance_link.get().strip(),
            "profiles":      [tab.v_profile.get() for tab in self.tabs],
            "freeze_8pages": [tab.v_freeze_8pages.get() for tab in self.tabs],
            "freeze_punish": [tab.v_freeze_punish.get() for tab in self.tabs],
            "freeze_rounds": [sorted(name for name, var in tab.v_freeze_rounds.items()
                                     if var.get())
                              for tab in self.tabs],
        })

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
        try:
            self.log_text.delete("1.0", "end")
            self._log_line_count = 0
        finally:
            self.log_text.config(state="disabled")

    def _on_close(self):
        self._stop()
        self.destroy()
