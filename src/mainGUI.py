import os
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
import ProcessCheck
import VRChatDiscovery
import VRChatLauncher
import WindowOperator
import ToolLauncher
import HotKey
import ToNEntry
import OSCClient
import OBSClient
import Recorder
import SecretStore
from StatisticsGUI import StatisticsWindow

try:
    import keyboard
except ImportError:
    keyboard = None


# ── 設定ファイル（前回のtnlパス等の永続化） ──────
def _file_stamp(path):
    """変更検出用の (mtime, size)。読めなければ None（無いことも状態のうち）"""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def exclusive_check(checked, other):
    """片方を入れたら、もう片方の同じラウンドを外す"""
    if checked.get():
        other.set(False)


def skip_round_vars(make_var) -> dict:
    """ラウンド指定自爆のチェックボックス用の変数を作る。

    並び順は config.SKIP_ROUND_SELECTABLE のまま。ソートも独自順序も作らない。
    """
    return {name: make_var() for name in config.SKIP_ROUND_SELECTABLE}


def freeze_round_vars(make_var) -> dict:
    """突入フリーズのチェックボックス用の変数を作る。

    並び順は config.ROUND_FREEZE_SELECTABLE のまま。ソートも独自順序も作らない
    （ユーザーがこのリストの順序を編集して表示順を変えられるようにするため）。
    """
    return {name: make_var() for name in config.ROUND_FREEZE_SELECTABLE}


def _as_flag(value) -> bool:
    """旧形式（窓ごとの配列）なら、いずれかがONならON"""
    if isinstance(value, (list, tuple)):
        return any(bool(v) for v in value)
    return bool(value)


def _as_round_names(value) -> set:
    """旧形式（窓ごとの配列の配列）なら全窓の和集合を採る"""
    names: set = set()
    if not value:
        return names
    for item in value:
        if isinstance(item, (list, tuple, set)):
            names.update(str(v) for v in item)
        elif isinstance(item, str):
            names.add(item)
    return names


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


OBS_PASSWORD_KEY = "obs_password_dpapi"     # DPAPI で暗号化した値（base64）
OBS_PASSWORD_PLAIN_KEY = "obs_password"     # 旧形式（平文）。読んだら暗号化して消す


def load_obs_password(data: dict) -> tuple[str, str]:
    """settings から OBS のパスワードを取り出す。(パスワード, 状態)。

    状態は "" / "migrate"（平文が残っていた。暗号化して保存し直す）/
    "undecryptable"（別のPC・別のユーザーの値など。空として扱う）。
    """
    stored = data.get(OBS_PASSWORD_KEY)
    if stored:
        password = SecretStore.unprotect(str(stored))
        if password is None:
            return "", "undecryptable"
        return password, ""
    if OBS_PASSWORD_PLAIN_KEY in data:
        return str(data.get(OBS_PASSWORD_PLAIN_KEY) or ""), "migrate"
    return "", ""


def with_obs_password(data: dict, password: str) -> dict:
    """保存する dict に OBS のパスワードを暗号化して入れる。平文は書かない"""
    out = {k: v for k, v in data.items() if k != OBS_PASSWORD_PLAIN_KEY}
    # 暗号化できなければ保存しない（平文へ落とさない）。次回は入れ直しになる
    out[OBS_PASSWORD_KEY] = (SecretStore.protect(password) or "") if password else ""
    return out


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
        # 窓↔ログの確定で分かったOSCポート（0ならOSC不可）。GUIには出さない
        self.osc_in = 0
        self.osc_out = 0
        self._on_log_selected = on_log_selected
        self._build()

    def _build(self):
        p = self

        # HWNDとログは自動割り当てで決まるので既定は畳んでおく。
        # タブごとに別インスタンスなので、窓ごとに独立して開閉する
        assign = CollapsibleFrame(p, text="窓の割り当て", collapsed=True)
        assign.pack(fill="x")
        a = assign.content

        def section(text):
            ttk.Label(a, text=text, background=config.GUI_BG, foreground=config.GUI_ACC,
                      font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))

        # ── VRChatウィンドウ ──
        section("■ VRChatウィンドウ")
        self.v_hwnd_sel = tk.StringVar(value="未選択")
        hf = ttk.Frame(a)
        hf.pack(fill="x", padx=10, pady=2)
        self.cb_hwnd = ttk.Combobox(hf, textvariable=self.v_hwnd_sel,
                                     state="readonly", width=36)
        self.cb_hwnd.pack(side="left", padx=(0, 4))
        ttk.Button(hf, text="🔄 更新", command=lambda: self._refresh_hwnds(1)).pack(side="left") # refresh_hwndsに1を入力することで、最新でアクティブになった窓を選択する

        # ── ログファイル ──
        section("■ ログファイル")
        self.v_log = tk.StringVar()
        lf = ttk.Frame(a)
        lf.pack(fill="x", padx=10, pady=2)
        ttk.Entry(lf, textvariable=self.v_log, width=80).pack(side="left", padx=(0, 4))
        ttk.Button(lf, text="…", width=3, command=self._browse_log).pack(side="left")

        # ── 起動プロファイル ──
        section("■ 起動プロファイル（VRChat起動時に使用）")
        pf = ttk.Frame(a)
        pf.pack(fill="x", padx=10, pady=2)
        ttk.Label(pf, text="--profile=").pack(side="left")
        self.v_profile = tk.IntVar(value=0)
        ttk.Spinbox(pf, from_=0, to=15, textvariable=self.v_profile, width=4).pack(side="left", padx=(2, 8))
        ttk.Label(pf, text="※ 同じ番号の窓は同じアカウント設定を共有します",
                  foreground=config.GUI_YLW).pack(side="left")

        # ── ON/OFF ──（畳む対象の外。常に見える）
        ttk.Separator(p, orient="horizontal").pack(fill="x", padx=10, pady=8)
        cf = ttk.Frame(p)
        cf.pack(fill="x", padx=10)
        self.v_auto_begin  = tk.BooleanVar(value=True)
        self.v_do_skip     = tk.BooleanVar(value=True)
        self.v_cancel_afk  = tk.BooleanVar(value=True)
        self.v_announce_intermission = tk.BooleanVar(value=False)
        ttk.Checkbutton(cf, text="自動Begin",                variable=self.v_auto_begin).pack(side="left")
        ttk.Checkbutton(cf, text="自動自爆",                 variable=self.v_do_skip).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="DTM/Waldo続行 (3クラまで)", variable=self.v_cancel_afk).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="Intermissionアナウンス",    variable=self.v_announce_intermission).pack(side="left", padx=(12, 0))

        # ── ラウンドごとの扱い（privateのみ） ──
        rounds = CollapsibleFrame(
            p, text="ラウンドごとの扱い（プライベートインスタンスのみ）",
            collapsed=True)
        rounds.pack(fill="x", pady=(4, 0))
        self.v_skip_rounds = skip_round_vars(lambda: tk.BooleanVar(value=False))
        self.v_continue_rounds = skip_round_vars(lambda: tk.BooleanVar(value=False))

        self._round_grid(rounds.content, "■ 自爆する", self.v_skip_rounds,
                         self.v_continue_rounds)
        # Variant（置き換え後のテラー）はいつもリストで判定する。
        # 以前ここにあった切り替えは廃止した
        self._round_grid(rounds.content, "■ 全続行する（続行リストを見ずに生き残る）",
                         self.v_continue_rounds, self.v_skip_rounds,
                         pady=(8, 0))

    def _round_grid(self, parent, title: str, own: dict, other: dict, pady=(0, 0)):
        """ラウンド一覧のチェックボックスを1つ並べる。

        同じラウンドを両方に入れられると意味が矛盾するので、片方を入れたら
        もう片方を外す。並び順は config.SKIP_ROUND_SELECTABLE のまま。
        """
        ttk.Label(parent, text=title, foreground=config.GUI_ACC).pack(
            anchor="w", padx=10, pady=pady)
        grid = ttk.Frame(parent)
        grid.pack(fill="x", padx=10)
        for i, (name, var) in enumerate(own.items()):
            ttk.Checkbutton(
                grid, text=name, variable=var,
                command=lambda v=var, o=other[name]: exclusive_check(v, o)
            ).grid(row=i // config.SKIP_ROUND_COLUMNS,
                   column=i % config.SKIP_ROUND_COLUMNS,
                   sticky="w", padx=(0, 12), pady=1)


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
            announce_intermission=self.v_announce_intermission.get(),
            skip_rounds={name for name, var in self.v_skip_rounds.items()
                         if var.get()},
            continue_rounds={name for name, var in self.v_continue_rounds.items()
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
        # 大きさは中身に任せる。固定すると折りたたんでも縦が縮まない
        self.minsize(880, 600)
        self.configure(bg=config.GUI_BG)
        self.v_tnl       = tk.StringVar()
        self.v_win_count = tk.IntVar(value=4)
        # LogMonitor がこの dict をそのまま掴むので、以後は差し替えず中身を入れ替える
        self.keepOn_set: dict = {}
        # 参加者別の続行希望 {vrc_name: {round_key: set(ids)}}。
        # Sabotage のマーダー判定に使う。これも in-place で入れ替える
        self.host_wishes: dict = {}
        # 参加者（＋自分）の名前。host_wishes には待機も入るので区別する。これも in-place
        self.host_participants: set = set()
        self._host_save_stamp: tuple | None = None   # (st_mtime, st_size)
        self._host_save_warned = False               # 一時的な失敗の警告は1回だけ
        self._host_loss_since: float | None = None   # 主催リストが取れなくなった時刻
        self.monitors: list[LogMonitor.LogMonitor] = []
        self._running = False
        self._overlay: LogOverlay | None = None
        self._log_line_count = 0
        self._emergency_stop_key_pressed = False
        # config の定数は既定値として読むだけ。実行時の値はこちらで持つ
        self.v_emergency_key = tk.StringVar(value=config.EMERGENCY_STOP_KEY)
        self._capturing_key = False
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
        self._start_host_save_polling()
        self._start_tool_poll()

    def _start_emergency_stop_polling(self):
        if keyboard is None:
            return
        self.after(config.EMERGENCY_STOP_POLL_MS, self._poll_emergency_stop_key)

    def _poll_emergency_stop_key(self):
        if self._capturing_key:
            # 設定しようとしているキーで停止がかかると困る
            self._emergency_stop_key_pressed = False
            self._reschedule_emergency_poll()
            return
        key = self.v_emergency_key.get()
        try:
            now = keyboard.is_pressed(key)
            if now and not self._emergency_stop_key_pressed:
                self._log(f"[緊急停止] {HotKey.display(key)}キーが押されました")
                self.after(0, self._stop)
            self._emergency_stop_key_pressed = now
        except Exception:
            # 不正なキーだと is_pressed が投げる。握り潰すと緊急停止が黙って
            # 死ぬので、既定値へ戻して知らせる
            self._fall_back_to_default_key(
                f"[緊急停止] ⚠ {key!r} は使えないキーです")

        try:
            self.after(config.EMERGENCY_STOP_POLL_MS, self._poll_emergency_stop_key)
        except tk.TclError:
            pass

    def _reschedule_emergency_poll(self):
        try:
            self.after(config.EMERGENCY_STOP_POLL_MS, self._poll_emergency_stop_key)
        except tk.TclError:
            pass

    def _fall_back_to_default_key(self, reason: str):
        """不正なキーは既定値へ倒す。効かない緊急停止を抱えたままにしない"""
        if self.v_emergency_key.get() == config.EMERGENCY_STOP_KEY:
            return
        self.v_emergency_key.set(config.EMERGENCY_STOP_KEY)
        self._emergency_stop_key_pressed = False
        self._log(reason)
        self._log(f"[緊急停止] {HotKey.display(config.EMERGENCY_STOP_KEY)}"
                  "キーに戻しました")

    def _refresh_emergency_key_label(self):
        try:
            self.lbl_emergency.config(
                text=f"緊急停止: {HotKey.display(self.v_emergency_key.get())}キー長押し")
        except (tk.TclError, AttributeError):
            pass

    def _begin_capture_key(self):
        """「キーを押して設定」。捕捉は別スレッド——read_hotkey は待つのでGUIが固まる"""
        if self._capturing_key:
            return
        self._capturing_key = True
        try:
            self.btn_capture_key.config(text="キーを押してください…", state="disabled")
        except tk.TclError:
            pass

        def worker():
            key = HotKey.capture(config.EMERGENCY_KEY_CAPTURE_SEC)
            try:
                self.after(0, lambda: self._finish_capture_key(key))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_capture_key(self, key):
        self._capturing_key = False
        self._emergency_stop_key_pressed = False
        try:
            self.btn_capture_key.config(text="キーを押して設定", state="normal")
        except tk.TclError:
            pass
        if key is None:
            self._log("[緊急停止] ⚠ キーを取れませんでした。設定は変えていません")
            return
        if not HotKey.is_valid(key):
            self._log(f"[緊急停止] ⚠ {key!r} は使えないキーです")
            self.v_emergency_key.set(config.EMERGENCY_STOP_KEY)
            self._refresh_emergency_key_label()
            self._log(f"[緊急停止] {HotKey.display(config.EMERGENCY_STOP_KEY)}"
                      "キーに戻しました")
            return
        self.v_emergency_key.set(key)
        self._refresh_emergency_key_label()
        self._log(f"[緊急停止] {HotKey.display(key)}キーに変更しました")

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
        
        self.lbl_win_warn = ttk.Label(
            f2, text="※ 窓数はマクロ起動前に設定してください",
            foreground=config.GUI_YLW)
        self.lbl_win_warn.pack(anchor="w")

        # ── VRChat起動 ──
        # 起動ボタンは常に見えているべきなので、折りたたみの外に置く
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
        # ttk.Label(lf1, text="※ 既定は「窓数 − 起動済みの窓数」",
        #           foreground=config.GUI_YLW).pack(side="left", padx=(4, 0))
        # デスクトップモードとOSCポート割り当ては常時有効（設定不要のため非表示）
        self.v_desktop_mode = tk.BooleanVar(value=True)
        self.v_use_osc = tk.BooleanVar(value=True)
        self.btn_stop_entry = ttk.Button(
            lf1, text="■ 入室操作を中止", command=self._cancel_ton_entry, state="disabled")
        self.btn_stop_entry.pack(side="left", padx=(10, 0))
        self.lbl_launch = ttk.Label(lf1, text="", foreground=config.GUI_GRN)
        self.lbl_launch.pack(side="left", padx=(10, 0))

        # 詳細はボタンの下へ畳む。毎回触るものではないので既定は畳んだ状態
        f2_launch = CollapsibleFrame(f2, text="VRChat起動の詳細設定", collapsed=True)
        f2_launch.pack(fill="x", pady=(6, 0))
        f2_launch = f2_launch.content

        lf2 = ttk.Frame(f2_launch)
        lf2.pack(fill="x")
        self.v_join_world = tk.BooleanVar(value=False)
        ttk.Checkbutton(lf2, text="ToNへ自動的にJoin",
                        variable=self.v_join_world).pack(side="left")
        self.v_instance_link = tk.StringVar()
        ttk.Entry(lf2, textvariable=self.v_instance_link, width=50).pack(side="left", padx=(6, 4))
        ttk.Button(lf2, text="最新ログから取得",
                   command=self._fill_instance_link_from_log).pack(side="left")
        ttk.Label(lf2, text="※ 空欄ならToNの新規インスタンスを自動生成（窓ごとに別インスタンス）",
                  foreground=config.GUI_YLW).pack(side="left", padx=(8, 0))

        lf22 = ttk.Frame(f2_launch)
        lf22.pack(fill="x", pady=(4, 0))
        ttk.Label(lf22, text="新規インスタンスの公開範囲:").pack(side="left")
        self.v_ton_access = tk.StringVar(value=config.TON_INSTANCE_ACCESS_DEFAULT)
        ttk.Radiobutton(lf22, text="インバイト", variable=self.v_ton_access,
                        value=config.TON_INSTANCE_ACCESS_INVITE).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(lf22, text="インバイト+", variable=self.v_ton_access,
                        value=config.TON_INSTANCE_ACCESS_INVITE_PLUS).pack(side="left", padx=(6, 0))
        ttk.Label(lf22, text="※ インバイト+ では霧の看破は働きません",
                  foreground=config.GUI_YLW).pack(side="left", padx=(10, 0))

        lf25 = ttk.Frame(f2_launch)
        lf25.pack(fill="x", pady=(4, 0))
        self.v_ton_entry = tk.BooleanVar(value=config.TON_ENTRY_ENABLED)
        ttk.Checkbutton(lf25, text="入室後の選択画面を自動突破",
                        variable=self.v_ton_entry).pack(side="left")
        self.v_ton_begin = tk.BooleanVar(value=config.TON_ENTRY_BEGIN)
        ttk.Checkbutton(lf25, text="続けてBeginまで押す",
                        variable=self.v_ton_begin).pack(side="left", padx=(12, 0))
        ttk.Label(lf25, text="※ 警告同意→Casual→BGMあり→LET ME PLAY の順に押します",
                  foreground=config.GUI_YLW).pack(side="left", padx=(10, 0))

        lf3 = ttk.Frame(f2_launch)
        lf3.pack(fill="x", pady=(2, 0))
        ttk.Label(lf3, text="起動exe:").pack(side="left")
        self.v_vrchat_exe = tk.StringVar()
        ttk.Entry(lf3, textvariable=self.v_vrchat_exe, width=56).pack(side="left", padx=(4, 4))
        ttk.Button(lf3, text="…", width=3, command=self._browse_vrchat_exe).pack(side="left")
        ttk.Label(lf3, text="※ 空欄ならSteamから自動検出",
                  foreground=config.GUI_YLW).pack(side="left", padx=(6, 0))


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
        self.v_voice_alternate    = tk.StringVar(value=config.VOICE_ALTERNATE)
        self.v_voice_midnight     = tk.StringVar(value=config.VOICE_MIDNIGHT)
        self.v_voice_unbound      = tk.StringVar(value=config.VOICE_UNBOUND)
        self.v_voice_fog          = tk.StringVar(value=config.VOICE_FOG)
        self.v_voice_ghost        = tk.StringVar(value=config.VOICE_GHOST)
        self.v_voice_8pages       = tk.StringVar(value=config.VOICE_8PAGES)
        self.v_voice_punish       = tk.StringVar(value=config.VOICE_PUNISH)
        self.v_voice_item_lost    = tk.StringVar(value=config.VOICE_ITEM_LOST)
        self.v_voice_intermission = tk.StringVar(value=config.VOICE_INTERMISSION)
        self.v_voice_foxy         = tk.StringVar(value=config.VOICE_FOXY)
        self.v_voice_list_lost    = tk.StringVar(value=config.VOICE_LIST_LOST)
        voice_row(fv, "続行ラウンド:", self.v_voice_continue)
        voice_row(fv, "Alternate:", self.v_voice_alternate)
        voice_row(fv, "Midnight:", self.v_voice_midnight)
        voice_row(fv, "Unbound:", self.v_voice_unbound)
        voice_row(fv, "Fog:", self.v_voice_fog)
        voice_row(fv, "Ghost:", self.v_voice_ghost)
        voice_row(fv, "8 Pages(速度検知):", self.v_voice_8pages)
        voice_row(fv, "Punish(速度検知):", self.v_voice_punish)
        voice_row(fv, "アイテムロスト:", self.v_voice_item_lost)
        voice_row(fv, "Intermission:", self.v_voice_intermission)
        voice_row(fv, "Foxy:", self.v_voice_foxy)
        voice_row(fv, "主催リスト喪失:", self.v_voice_list_lost)

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

        # ④ 外部ツール起動（Appに1つだけ。窓タブの枚数とは無関係）
        f4_wrap = CollapsibleFrame(self, text="④ 外部ツール起動", collapsed=True)
        f4_wrap.pack(fill="x", padx=12, pady=4)
        self.tool_frame = ttk.Frame(f4_wrap.content)
        self.tool_frame.pack(fill="x")
        self.tool_rows: list = []
        ttk.Button(f4_wrap.content, text="＋ 追加",
                   command=self._add_tool_row).pack(anchor="w", pady=(4, 0))

        # ⑤ OBS自動録画（続行アナウンスが鳴る種類の続行ラウンドを録る）
        f5_wrap = CollapsibleFrame(self, text="⑤ OBS自動録画", collapsed=True)
        f5_wrap.pack(fill="x", padx=12, pady=4)
        f5 = f5_wrap.content
        of1 = ttk.Frame(f5)
        of1.pack(fill="x", pady=2)
        self.v_obs_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(of1, text="続行ラウンドを録画する", variable=self.v_obs_enabled,
                        command=self._apply_obs_settings).pack(side="left")
        ttk.Label(of1, text="※ OBSの「ツール → WebSocketサーバー設定」で有効にしてください。"
                            "シーンは切り替えません",
                  foreground=config.GUI_YLW).pack(side="left", padx=(10, 0))
        of2 = ttk.Frame(f5)
        of2.pack(fill="x", pady=2)
        ttk.Label(of2, text="ホスト:").pack(side="left")
        self.v_obs_host = tk.StringVar(value=config.OBS_DEFAULT_HOST)
        ttk.Entry(of2, textvariable=self.v_obs_host, width=16).pack(side="left", padx=(4, 10))
        ttk.Label(of2, text="ポート:").pack(side="left")
        self.v_obs_port = tk.StringVar(value=str(config.OBS_DEFAULT_PORT))
        ttk.Entry(of2, textvariable=self.v_obs_port, width=7).pack(side="left", padx=(4, 10))
        ttk.Label(of2, text="パスワード:").pack(side="left")
        self.v_obs_password = tk.StringVar()
        ttk.Entry(of2, textvariable=self.v_obs_password, width=20,
                  show="*").pack(side="left", padx=(4, 10))
        ttk.Button(of2, text="接続テスト",
                   command=self._test_obs_connection).pack(side="left")

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
        self.lbl_emergency = ttk.Label(fc, text="",
                                       foreground=config.GUI_ORG)
        self.lbl_emergency.pack(side="left", padx=(10, 0))
        self.btn_capture_key = ttk.Button(fc, text="キーを押して設定", width=16,
                                          command=self._begin_capture_key)
        self.btn_capture_key.pack(side="left", padx=(6, 0))
        self._refresh_emergency_key_label()

        # 完全放置モード（全窓共通）
        fhf = ttk.Frame(self)
        fhf.pack(pady=(0, 4))
        self.btn_hands_free = tk.Button(
            fhf, text="完全放置モード: OFF",
            bg=config.GUI_SUB, fg=config.GUI_FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_hands_free)
        self.btn_hands_free.pack(side="left")

        # アイテム取得→Begin
        self.btn_item_get_begin = tk.Button(
            fhf, text="アイテム取得→Begin: OFF",
            bg=config.GUI_SUB, fg=config.GUI_FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_item_get_begin)
        self.btn_item_get_begin.pack(side="left", padx=(8, 0))

        # 速度によるラウンド種別の検知（全窓共通）
        self.btn_speed_detect = tk.Button(
            fhf, text="", bg=config.GUI_SUB, fg=config.GUI_FG,
            font=("Segoe UI", 11, "bold"), relief="raised", padx=12, pady=5,
            command=self._toggle_speed_detect)
        self.btn_speed_detect.pack(side="left", padx=(8, 0))
        self._refresh_speed_detect_button()

        # フリーズ設定（全窓共通）。フリーズは全窓を止める仕組みなので窓ごとに分けない
        ffz = ttk.Frame(self)
        ffz.pack(pady=(0, 4))
        self.v_freeze_8pages = tk.BooleanVar(value=False)
        self.v_freeze_punish = tk.BooleanVar(value=False)
        ttk.Checkbutton(ffz, text="8 Pages検知でフリーズ", variable=self.v_freeze_8pages,
                        command=self._apply_freeze_settings).pack(side="left")
        ttk.Checkbutton(ffz, text="Punished検知でフリーズ", variable=self.v_freeze_punish,
                        command=self._apply_freeze_settings).pack(side="left", padx=(12, 0))

        ffr = ttk.Frame(self)
        ffr.pack(pady=(0, 4))
        ttk.Label(ffr, text="突入で全窓停止:").pack(side="left")
        self.v_freeze_rounds = freeze_round_vars(lambda: tk.BooleanVar(value=False))
        for name, var in self.v_freeze_rounds.items():
            ttk.Checkbutton(ffr, text=name, variable=var,
                            command=self._apply_freeze_settings).pack(side="left", padx=(8, 0))

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
        self._apply_saved_window_settings()

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
                text="アイテム取得→Begin: ON",
                bg="#1a3a5a", fg="#89b4fa", relief="sunken")
            self._log("[アイテム取得→Begin] ON: ラウンド開始時にフォーカス＆フリーズ")
        else:
            self.btn_item_get_begin.config(
                text="アイテム取得→Begin: OFF",
                bg=config.GUI_SUB, fg=config.GUI_FG, relief="raised")
            self._log("[アイテム取得→Begin] OFF")

    def _apply_freeze_settings(self):
        """GUIのフリーズ設定を全窓共通の状態へ反映する"""
        SharedState.set_freeze_on_8pages(self.v_freeze_8pages.get())
        SharedState.set_freeze_on_punish(self.v_freeze_punish.get())
        SharedState.set_freeze_rounds(
            name for name, var in self.v_freeze_rounds.items() if var.get())

    def _refresh_speed_detect_button(self):
        on = SharedState.get_speed_detect()
        self.btn_speed_detect.config(
            text=f"速度検知: {'ON' if on else 'OFF'}",
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
                text="完全放置モード: ON",
                bg="#3a1a1a", fg=config.GUI_RED, relief="sunken")
            self._log("[放置モード] ON: アイテムロスト無視・全ラウンド即自爆・アナウンス停止"
                      "（プライベート系の窓のみ）")
        else:
            self.btn_hands_free.config(
                text="完全放置モード: OFF",
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

    def _apply_keep_on(self, new_set: dict):
        """keepOn_set の中身を入れ替える。

        LogMonitor は生成時に渡された dict を掴んだままなので、差し替え
        （再代入）ではなく in-place で更新する。走行中の全窓に即座に効く。
        """
        self.keepOn_set.clear()
        self.keepOn_set.update(new_set)

    def _apply_host_wishes(self, new_wishes: dict, participants=()):
        """参加者別の希望も LogMonitor が同じ dict を掴む。in-place で更新する"""
        self.host_wishes.clear()
        self.host_wishes.update(new_wishes)
        names = getattr(self, "host_participants", None)
        if names is not None:
            names.clear()
            names.update(participants)

    def _start_host_save_polling(self):
        self.after(int(config.HOST_SAVE_POLL_SEC * 1000), self._poll_host_save)

    def _poll_host_save(self):
        try:
            self._refresh_host_source()
        except Exception:
            pass
        try:
            self.after(int(config.HOST_SAVE_POLL_SEC * 1000), self._poll_host_save)
        except tk.TclError:
            pass

    def _warn_host_save_once(self, msg: str):
        """3秒ごとに同じ行が流れるので、失敗が続く間は最初の1回だけ出す"""
        if not self._host_save_warned:
            self._host_save_warned = True
            self._log(msg)

    def _fall_back_to_tnl(self, reason: str):
        """続行リストの供給元を .tnl に戻す。切り替わったときだけログを出す"""
        if SharedState.get_list_source() == "tnl":
            return
        SharedState.set_list_source("tnl")
        self._host_save_stamp = None
        self._host_save_warned = False
        self._log(f"[続行リスト] tnlへ切替（{reason}）")
        # 参加者別の希望を残すと Sabotage の判定に古い希望が効いてしまう
        self._apply_host_wishes({})
        # tnlが未設定だと _load_tnl は何もせず、主催リストが居座る。それでは
        # 「古いリストで判定しない」という目的を果たせないので先に空にする
        # （tnlが読めればこの直後に上書きされる。読めなければ続行0件）
        self._apply_keep_on({})
        self._load_tnl(show_error=False)

    def _host_list_lost(self, reason: str):
        """主催リストが取れない。続いたときだけ tnl へ切り替える。

        取れない理由（プロセスが見えない・host_save を stat できない・参加者0人）は
        どれも一瞬だけ起きうる。1回で切り替えると、他の人がいる窓でだけ
        「主催リストが取れません」が鳴る。猶予の間は前の主催リストを使い続ける。
        まだ主催リストを使っていなければ、守るものが無いのですぐ切り替える。
        """
        if SharedState.get_list_source() != "host":
            self._fall_back_to_tnl(reason)
            return
        now = time.monotonic()
        if getattr(self, "_host_loss_since", None) is None:
            self._host_loss_since = now
            if config.HOST_LIST_LOSS_GRACE_SEC > 0:
                self._log(f"[主催リスト] 一時的に取れません（猶予中）: {reason}")
        if now - self._host_loss_since < config.HOST_LIST_LOSS_GRACE_SEC:
            return
        self._host_loss_since = None
        self._fall_back_to_tnl(reason)

    def _refresh_host_source(self):
        """続行リストの供給元を状況から決める。

        ToN ListTool が動いていて参加者がいれば主催リスト、それ以外は .tnl。
        ツールを閉じても host_save.json.gz はディスクに残るので、鮮度は
        mtime ではなくプロセスの生死で見る（古いファイルを掴まないため）。
        """
        if not ProcessCheck.is_process_running(config.TON_LISTTOOL_PROCESS):
            self._host_list_lost("ToN ListTool が起動していません")
            return

        path = config.HOST_SAVE_PATH
        try:
            stat = os.stat(path)
        except OSError as e:
            self._host_list_lost(f"host_save が読めません: {e}")
            return

        # 自分のリストだけ更新されたときも読み直す
        stamp = (stat.st_mtime, stat.st_size,   # 同じ秒内の書き換えを取りこぼさない
                 _file_stamp(config.USER_SAVE_PATH))
        if stamp == self._host_save_stamp and SharedState.get_list_source() == "host":
            self._host_loss_since = None        # 取れている
            return

        try:
            keep_on, meta, wishes = MatchTNL.load_host_save(
                path, config.USER_SAVE_PATH)
        except Exception as e:
            # 別プロセスが書いている最中を掴みうる。ここで tnl へ倒すと3秒ごとに
            # 往復しかねないので、前の値を保持して次のtickで再試行する
            self._warn_host_save_once(f"[主催リスト] ⚠ 読み込み失敗（前のリストを使います）: {e}")
            return

        if not meta["listed"]:
            # 参加者にも待機にも続行リストを持つ人がいない＝周回そのものが無い。
            # 「参加者0人」だけでは切り替えない——ToN ListTool は複窓だと全員を
            # 待機へ移すことがあり、それでも希望は待機に残っている
            self._host_list_lost("続行リストを持つ人がいません（参加者・待機とも）")
            return

        self._host_loss_since = None
        self._host_save_stamp = stamp
        self._host_save_warned = False
        switched = SharedState.get_list_source() != "host"
        SharedState.set_list_source("host")
        changed = keep_on != self.keepOn_set
        self._apply_keep_on(keep_on)
        self._apply_host_wishes(wishes, meta.get("participant_names", ()))
        if switched:
            self._log(f"[続行リスト] 主催リストへ切替（参加者{meta['participants']}人）")
        if changed or switched:
            total = sum(len(v) for v in self.keepOn_set.values())
            mine = "(+自分)" if meta.get("host_self") else ""
            msg = (f"[主催リスト] 参加者{meta['participants']}人{mine} / "
                   f"{len(self.keepOn_set)}ラウンド / {total}件 続行対象")
            self.lbl_tnl.config(text=msg, foreground=config.GUI_GRN)
            self._log(msg)

    def _load_tnl(self, show_error: bool = True):
        p = self.v_tnl.get().strip()
        if not p or not Path(p).exists():
            if show_error:
                messagebox.showerror("エラー", "tnlファイルが見つかりません")
            else:
                self._log(f"[TNL] 前回のtnlが見つかりません: {p}")
            return
        try:
            keep_on, meta = MatchTNL.load_tnl(p)
            self._apply_keep_on(keep_on)
            total = sum(len(v) for v in self.keepOn_set.values())
            msg = f"[{meta['list_name']}] {len(self.keepOn_set)}ラウンド / {total}件 続行対象"
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
        access = data.get("ton_instance_access")
        if access not in config.TON_INSTANCE_ACCESS_CHOICES:
            access = config.TON_INSTANCE_ACCESS_DEFAULT     # 無い・壊れた値は既定
        self.v_ton_access.set(access)
        self.v_instance_link.set(data.get("instance_link", ""))
        self._saved_profiles = data.get("profiles", [])
        # ラウンド指定（skip_rounds / continue_rounds）は
        # 復元しない。持ち越した自爆設定は、別のインスタンスでは危ない
        # 手編集や別バージョンで壊れた値が入りうる。読むときも検証する——
        # 不正なキーのままだと緊急停止が黙って効かなくなる
        key = data.get("emergency_stop_key", config.EMERGENCY_STOP_KEY)
        if not HotKey.is_valid(key):
            key = config.EMERGENCY_STOP_KEY
        self.v_emergency_key.set(key)
        self._refresh_emergency_key_label()
        # 古い settings.json にはキーが無い。無くても落ちないこと
        for path in data.get("tool_launchers", []) or []:
            if isinstance(path, str) and path.strip():
                self._add_tool_row(path, save=False)
        # 旧形式は窓ごとの配列。全窓共通へ移したので畳んで読む
        self.v_freeze_8pages.set(_as_flag(data.get("freeze_8pages")))
        self.v_freeze_punish.set(_as_flag(data.get("freeze_punish")))
        for name, var in self.v_freeze_rounds.items():
            var.set(name in _as_round_names(data.get("freeze_rounds")))
        self._apply_freeze_settings()
        self.v_obs_enabled.set(_as_flag(data.get("obs_record")))
        self.v_obs_host.set(str(data.get("obs_host") or config.OBS_DEFAULT_HOST))
        self.v_obs_port.set(str(data.get("obs_port") or config.OBS_DEFAULT_PORT))
        password, state = load_obs_password(data)
        self.v_obs_password.set(password)
        if state == "undecryptable":
            self._log("[OBS] 保存されたパスワードを復号できません（別のPCや別のユーザーの"
                      "設定など）→ OBS のパスワードを入れ直してください")
        elif state == "migrate":
            # 旧形式の平文をその場で暗号化して書き直す（タブの有無に関係なく）
            save_settings(with_obs_password(load_settings(), password))
        self._apply_obs_settings()
        self._apply_saved_window_settings()
        tnl_path = data.get("tnl_path", "")
        if not tnl_path:
            return
        self.v_tnl.set(tnl_path)
        self._load_tnl(show_error=False)

    def _apply_saved_window_settings(self):
        """保存済みの窓ごと設定（profile ID）を反映する。

        ラウンド指定はここで扱わない——毎回すべて未チェックで始める。
        """
        for tab, pid in zip(self.tabs, getattr(self, "_saved_profiles", [])):
            try:
                tab.v_profile.set(int(pid))
            except (ValueError, tk.TclError):
                pass

    def _clear_tab_round_settings(self, window_idx: int):
        """監視スレッドから呼ばれる。Tk変数はメインスレッドでしか触れない"""
        try:
            self.after(0, lambda: self._do_clear_tab_round_settings(window_idx))
        except (tk.TclError, RuntimeError):
            # 破棄後は TclError、mainloop の外なら RuntimeError。どちらも
            # 監視スレッドで投げると、そのスレッドごと黙って止まる
            pass

    def _do_clear_tab_round_settings(self, window_idx: int):
        """インスタンスが変わった窓のチェックを外す。メインスレッドで動く。

        監視が見る側（WindowConfig）は LogMonitor が自分で消している。ここは
        利用者が見る側で、片方だけだと表示と動きが食い違う。
        """
        tab = next((t for t in getattr(self, "tabs", [])
                    if t.idx == window_idx - 1), None)
        if tab is None:
            return              # 窓数を減らした後などに届いた
        try:
            for var in tab.v_skip_rounds.values():
                var.set(False)
            for var in tab.v_continue_rounds.values():
                var.set(False)
        except tk.TclError:
            pass        # ウィンドウ破棄後に after が発火した

    def _release_suicide_keys(self, hwnds):
        """押されたままかもしれない自爆キーを離す。押していなくても無害。

        失敗しても止めない（止めると停止・終了そのものができなくなる）。
        """
        key = SharedState.get_suicide_key()
        for hwnd in hwnds:
            if not hwnd:
                continue
            try:
                WindowOperator.release_key_background(hwnd, key)
            except Exception as e:
                self._log(f"自爆キーを離せませんでした（{e}）")

    def _auto_detect_windows(self):
        """起動時: VRChatウィンドウ数を検出して窓数へ反映し、
        起動時刻を使ってHWNDとログを全窓ぶん自動割り当てする。"""
        windows = VRChatDiscovery.get_vrchat_windows_by_start_time(config.MAX_WINDOWS)
        if not windows:
            self._log("[起動時検出] VRChatウィンドウ未検出（窓数は手動で設定してください）")
            return
        # 前回このツールが自爆の長押し中に落ちていたら、キーが押されたまま
        # 残っている。見つかった窓すべてで離しておく
        self._release_suicide_keys(h for h, _t in windows)
        n = len(windows)
        self.v_win_count.set(n)
        if len(self.tabs) != n:
            self._rebuild_tabs(n)
        self._log(f"[起動時検出] VRChatウィンドウを{n}窓検出 → 窓数を{n}に設定")
        self._assign_windows_and_logs(windows)

    def _resolve_windows(self, windows: list) -> list:
        """窓↔ログ↔OSCポートを確定する。netstatは1回だけ撃つ"""
        candidates = VRChatDiscovery.find_latest_logs(
            config.VRCHAT_LOG_DIR, config.LOG_MATCH_CANDIDATE_COUNT)
        ports_by_pid = OSCClient.udp_ports_by_pid()
        if ports_by_pid is None:
            self._log("[割り当て] UDPポート一覧を取得できません（netstat失敗）"
                      "→ 起動時刻だけで割り当てます")
        return VRChatDiscovery.assign_windows(
            windows, candidates, ports_by_pid, config.LOG_MATCH_TOLERANCE_SEC)

    @staticmethod
    def _assign_source(a) -> str:
        """割り当ての根拠（ログに出す）"""
        if a.osc_in:
            return f"OSC {a.osc_in}"
        return "起動時刻一致" if a.start_time is not None else "起動時刻不明のため順番で割当"

    def _assign_windows_and_logs(self, windows: list):
        """HWNDとログを窓タブへ1対1で割り当てる（Zオーダーに依存しない）。

        OSCの受信ポートで確定した窓をポートの昇順に窓1・窓2…へ入れ、
        確定しなかった窓は従来どおり起動時刻で残りのタブへ入れる。
        """
        assigned = self._resolve_windows(windows)
        hwnds = [a.hwnd for a in assigned]

        active_tabs = list(self.tabs)
        for i, tab in enumerate(active_tabs):
            if i >= len(assigned):
                break
            a = assigned[i]
            tab.set_hwnd_choices(hwnds, selected_hwnd=a.hwnd)
            tab.osc_in, tab.osc_out = a.osc_in, a.osc_out
            if a.log is None:
                self._log(f"[窓{tab.idx+1}] HWND={a.hwnd:#010x} → 対応するログが見つかりません")
                continue
            tab.v_log.set(str(a.log))
            self._log(f"[窓{tab.idx+1}] HWND={a.hwnd:#010x} → {a.log.name}"
                      f"（{self._assign_source(a)}）")
            self._on_tab_log_selected(tab)

    def _resolve_tab_ports(self):
        """開始時の割り当て。_assign_windows_and_logs と同じ確定処理を通す。

        こちらは窓の並びを変えない（利用者がタブでHWNDを選び直していることが
        ある）。HWNDごとにログとポートを引き当て、ログが空のタブだけ埋める。
        """
        active_tabs = list(self.tabs)
        windows = VRChatDiscovery.get_vrchat_windows_by_start_time(len(active_tabs))
        assigned = {a.hwnd: a for a in self._resolve_windows(windows)}
        for tab in active_tabs:
            a = assigned.get(tab._get_selected_hwnd())
            tab.osc_in = a.osc_in if a else 0
            tab.osc_out = a.osc_out if a else 0
            if tab.v_log.get().strip() or a is None or a.log is None:
                continue        # 手で選んだログは上書きしない
            tab.v_log.set(str(a.log))
            self._log(f"[窓{tab.idx+1}] ログを自動割り当て: {a.log.name}"
                      f"（{self._assign_source(a)}）")
            self._on_tab_log_selected(tab)

    def _on_tab_log_selected(self, tab: WindowTab):
        """ログ選択時: ログ末尾からインスタンスタイプを検出して知らせる。

        干し芋/焼き芋のルールはインスタンス種別だけで常時適用されるので、
        ここで設定を触ることはしない。"""
        p = tab.v_log.get().strip()
        if not p:
            return
        itype = LogMonitor.LogMonitor.detect_instance_type_from_log(Path(p))
        if itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            self._log(f"[窓{tab.idx+1}] {itype}インスタンス検出 → グループ判定を適用します")

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

        # ログが空なら自動割り当て（ついでにOSCポートも確定する）
        self._resolve_tab_ports()
        self._apply_obs_settings()

        self.monitors.clear()
        for tab in self.tabs:
            cfg, err = tab.get_config()
            if cfg is not None and cfg.hwnd:
                # OSC可否は起動時に1回だけ確定させる。ポートは決め打ちせず、
                # その窓が実際に掴んでいて、かつログの --osc= と一致したものを
                # 使う（ToNUtilsが立てた窓は9003で、刻み10に乗っていなかった）
                if tab.osc_in:
                    cfg.osc_port = tab.osc_in
                    cfg.osc_out_port = tab.osc_out
                    self._log(f"[窓{tab.idx+1}] OSC利用可"
                              f"（受信{tab.osc_in}／送信{tab.osc_out}）"
                              "→ 移動はOSC、排他はクリックと自爆のみ")
                else:
                    cfg.osc_port = 0
                    cfg.osc_out_port = 0
                    self._log(f"[窓{tab.idx+1}] OSC利用不可"
                              "（この窓が掴んでいるUDPポートとログの--oscが"
                              "一致しません）→ 従来どおりキー操作（全体を排他）")
            if cfg is None:
                if err:
                    self._log(f"[窓{tab.idx+1}] スキップ: {err}")
                continue
            if cfg.hwnd == 0:
                self._log(f"[窓{tab.idx+1}] ⚠ HWNDが未選択です。窓タブで「🔄 更新」してVRChatウィンドウを選択してください")
                continue
            # 音声ファイルパスをAppのGUI設定から注入
            cfg.voice_continue     = self.v_voice_continue.get().strip()
            cfg.voice_alternate     = self.v_voice_alternate.get().strip()
            cfg.voice_midnight      = self.v_voice_midnight.get().strip()
            cfg.voice_unbound       = self.v_voice_unbound.get().strip()
            cfg.voice_fog          = self.v_voice_fog.get().strip()
            cfg.voice_ghost         = self.v_voice_ghost.get().strip()
            cfg.voice_8pages        = self.v_voice_8pages.get().strip()
            cfg.voice_punish        = self.v_voice_punish.get().strip()
            cfg.voice_item_lost    = self.v_voice_item_lost.get().strip()
            cfg.voice_intermission = self.v_voice_intermission.get().strip()
            cfg.voice_foxy          = self.v_voice_foxy.get().strip()
            cfg.voice_list_lost     = self.v_voice_list_lost.get().strip()
            self._log(f"[窓{tab.idx+1}] HWND={cfg.hwnd:#010x}  ログ={cfg.log_path.name}")
            mon = LogMonitor.LogMonitor(
                cfg, self.keepOn_set, self._log,
                window_idx=tab.idx + 1,
                host_wishes=self.host_wishes,
                host_participants=self.host_participants,
                on_round_settings_cleared=self._clear_tab_round_settings)
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
        # 自爆の長押し中に止めると、daemon の自爆スレッドが KEYUP を送る前に
        # 終わりうる（終了時はそのままプロセスが消える）。先に離しておく
        self._release_suicide_keys(m.cfg.hwnd for m in self.monitors)
        # このツールが始めた録画だけ止める（手動の録画には触らない）。
        # 終了時はワーカーごと消えるので、送り終えるまで少しだけ待つ
        Recorder.stop_all(wait_sec=config.OBS_TIMEOUT_SEC * 2)
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
    def _add_tool_row(self, path: str = "", save: bool = True):
        """exe 1本ぶんの行を足す。パスを変えるとボタンの名前も追従する。

        `save=False` は設定の復元中に使う（読み込んだ端から書き戻さない）。
        """
        row = ttk.Frame(self.tool_frame)
        row.pack(fill="x", pady=1)
        row.v_path = tk.StringVar(value=path)
        ttk.Entry(row, textvariable=row.v_path, width=52).pack(side="left")
        ttk.Button(row, text="…", width=3,
                   command=lambda r=row: self._browse_tool_exe(r)
                   ).pack(side="left", padx=(4, 4))
        row.btn = ttk.Button(row, text="起動", width=22,
                             command=lambda r=row: self._launch_tool(r))
        row.btn.pack(side="left")
        ttk.Button(row, text="✕", width=3,
                   command=lambda r=row: self._remove_tool_row(r)
                   ).pack(side="left", padx=(4, 0))
        row.v_path.trace_add("write", lambda *_a, r=row: self._on_tool_path_changed(r))
        self.tool_rows.append(row)
        self._refresh_tool_row(row)
        if save:
            self._save_settings_now()
        return row

    def _on_tool_path_changed(self, row):
        self._refresh_tool_row(row)
        self._schedule_settings_save()

    def _remove_tool_row(self, row):
        if row in self.tool_rows:
            self.tool_rows.remove(row)
        row.destroy()
        self._save_settings_now()

    def _browse_tool_exe(self, row):
        p = filedialog.askopenfilename(
            title="起動するexeを選択",
            filetypes=[("実行ファイル", "*.exe"), ("All", "*.*")])
        if p:
            row.v_path.set(p)

    def _refresh_tool_row(self, row):
        """ボタンの名前と「起動中」表示を合わせる。見た目だけ"""
        exe = row.v_path.get().strip()
        label = ToolLauncher.button_label(exe) or "起動"
        running = bool(exe) and ToolLauncher.is_running(exe)
        try:
            row.btn.config(text=f"起動中: {label}" if running else f"▶ {label}",
                           state="disabled" if running else "normal")
        except tk.TclError:
            pass

    def _launch_tool(self, row):
        """二重起動を実際に止めているのはここ。ボタンの無効化は見た目で、
        ポーリングが遅れている隙に押されうる"""
        exe = row.v_path.get().strip()
        label = ToolLauncher.button_label(exe) or exe
        if ToolLauncher.is_running(exe):
            self._log(f"[外部ツール] {label} はすでに起動しています")
            return
        try:
            ToolLauncher.launch(exe)
            self._log(f"[外部ツール] {label} を起動しました")
        except Exception as e:
            self._log(f"[外部ツール] 起動に失敗: {e}")
        self._refresh_tool_row(row)

    def _start_tool_poll(self):
        self.after(int(config.TOOL_LAUNCH_POLL_SEC * 1000), self._poll_tool_buttons)

    def _poll_tool_buttons(self):
        try:
            for row in list(self.tool_rows):
                self._refresh_tool_row(row)
        except Exception:
            pass
        try:
            self.after(int(config.TOOL_LAUNCH_POLL_SEC * 1000),
                       self._poll_tool_buttons)
        except tk.TclError:
            pass

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
                base_link = VRChatLauncher.build_ton_link(
                    user_id, access=self.v_ton_access.get())
                self._log("[起動] ToNの新規インスタンスを生成します（%s）" % user_id)

        tabs = list(self.tabs)
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
                   if launched is None or tab.idx in launched]
        targets = [(no, h) for no, h in targets if h]
        if not targets:
            self._log("[入室操作] 対象の窓がありません")
            return
        press_begin = self.v_ton_begin.get()
        self._entry_stop.clear()
        self.btn_stop_entry.config(state="normal")
        self._log("[入室操作] 開始します（中止は「入室操作を中止」ボタンか %sキー）"
                  % HotKey.display(self.v_emergency_key.get()))

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

    def _save_settings_now(self):
        """いまのGUIの状態を settings.json へ書く。

        タブが0枚のときは書かない——`profiles` などが空配列で上書きされる。
        保存に失敗してもここで止める（呼び出し元を巻き込まない）。
        """
        if not getattr(self, "tabs", None):
            return
        try:
            self._save_launch_settings()
        except Exception as e:
            self._log(f"設定の保存に失敗: {e}")

    def _schedule_settings_save(self):
        """少し待ってからまとめて保存する。手打ちの1文字ごとに書かないため"""
        job = getattr(self, "_settings_save_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self._settings_save_job = self.after(
                config.SETTINGS_SAVE_DEBOUNCE_MS, self._run_scheduled_save)
        except tk.TclError:
            self._settings_save_job = None

    def _run_scheduled_save(self):
        self._settings_save_job = None
        try:
            self._save_settings_now()
        except tk.TclError:
            pass        # ウィンドウ破棄後にタイマーが発火した

    def _save_launch_settings(self):
        data = {
            **load_settings(),
            "vrchat_exe":    self.v_vrchat_exe.get().strip(),
            "desktop_mode":  self.v_desktop_mode.get(),
            "use_osc":       self.v_use_osc.get(),
            "ton_entry":     self.v_ton_entry.get(),
            "ton_begin":     self.v_ton_begin.get(),
            "join_world":    self.v_join_world.get(),
            "ton_instance_access": self.v_ton_access.get(),
            "instance_link": self.v_instance_link.get().strip(),
            "profiles":      [tab.v_profile.get() for tab in self.tabs],
            "tool_launchers": [p for p in (row.v_path.get().strip()
                                           for row in self.tool_rows) if p],
            "emergency_stop_key": self.v_emergency_key.get(),
            "freeze_8pages": self.v_freeze_8pages.get(),
            "freeze_punish": self.v_freeze_punish.get(),
            "freeze_rounds": sorted(name for name, var in self.v_freeze_rounds.items()
                                    if var.get()),
            "obs_record":    self.v_obs_enabled.get(),
            "obs_host":      self.v_obs_host.get().strip(),
            "obs_port":      self.v_obs_port.get().strip(),
        }
        # load_settings() をマージしているので、書かないだけでは前回の値が
        # ファイルに残り続ける。危ない設定は明示的に消す
        # skip_variant_exempt はチェックボックスごと廃止した。古いファイルに
        # 残っていても読まないが、ついでに消しておく
        for key in ("skip_rounds", "skip_variant_exempt", "continue_rounds"):
            data.pop(key, None)
        save_settings(with_obs_password(data, self.v_obs_password.get()))

    # ── OBS自動録画 ───────────────────────────
    def _obs_endpoint(self) -> tuple[str, int]:
        host = self.v_obs_host.get().strip() or config.OBS_DEFAULT_HOST
        try:
            port = int(self.v_obs_port.get().strip())
        except ValueError:
            port = config.OBS_DEFAULT_PORT
        return host, port

    def _apply_obs_settings(self):
        """GUIの設定を録画係へ渡す。パスワードはログに出さない"""
        host, port = self._obs_endpoint()
        enabled = self.v_obs_enabled.get()
        Recorder.configure(enabled, host, port,
                           self.v_obs_password.get(), log=self._log)
        if not enabled:
            # 録画中にOFFにしたら、このツールが始めた録画をその場で止める
            # （手動の録画には触らない）。GUI操作なので待たない
            Recorder.stop_all()

    def _test_obs_connection(self):
        """Identify まで通るかと、録画状態が取れるかを見る。録画はしない"""
        host, port = self._obs_endpoint()
        password = self.v_obs_password.get()
        self._log(f"[OBS] 接続テスト: {host}:{port}"
                  + ("（パスワードあり）" if password else "（パスワードなし）"))

        def worker():
            client = OBSClient.OBSClient(host, port, password)
            try:
                ok, reason = client.connect()
                if not ok:
                    self._log(f"[OBS] ❌ {reason}")
                    return
                ok, data, reason = client.request("GetRecordStatus")
                if not ok:
                    self._log(f"[OBS] ❌ 接続はできましたが録画状態が取れません: {reason}")
                    return
                state = "録画中" if data.get("outputActive") else "録画していません"
                self._log(f"[OBS] ✅ 接続できました（いまは{state}）")
            finally:
                client.close()

        threading.Thread(target=worker, daemon=True).start()
        self._save_settings_now()

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
        # VRChat を起動しないまま閉じても設定が残るように。destroy() の後は
        # Tk 変数を読めないので必ず先に、停止が長引いても保存は済ませたいので
        # _stop() より前に書く
        self._save_settings_now()
        self._stop()
        self.destroy()
