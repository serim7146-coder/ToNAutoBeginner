import os
import sys
import tkinter as tk
import win32gui
import threading
import glob
from pathlib import Path
from tkinter import ttk, scrolledtext, filedialog, messagebox
from datetime import datetime
from typing import Optional

import config
import LogMonitor
import PlaySound
import MatchTNL

# ═══════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════
VRCHAT_LOG_DIR = (
    Path(os.environ.get("APPDATA", "~")) / ".." / "LocalLow" / "VRChat" / "VRChat"
).resolve()

VRCHAT_WINDOW_CLASS  = "UnityWndClass"


# ═══════════════════════════════════════════════
#  最新ログを読み取るの処理
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
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        
        icon_path = resource_path("ToNAutoBeginnerIcon.ico")
        if icon_path.exists():
            self.iconbitmap(default=str(icon_path))
            
        self.title("ToNAutoBeginner")
        self.geometry("780x700")
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
