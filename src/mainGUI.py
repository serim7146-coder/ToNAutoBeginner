import os
import json
import platform
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, filedialog, messagebox
from datetime import datetime
from typing import Optional

import config
import DebugLog
import UIFont
from config import resource_path
import ActionExecutor
import AutoUpdate
import LogMonitor
import SharedState
import PlaySound
import MatchTNL
import Migration
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
import FogEarlyRead
import BugReport
import WindowVolume
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
        DebugLog.exception("mainGUI.load_settings")
        return {}


def save_settings(data: dict):
    """設定ファイルへ保存する（失敗しても動作継続）"""
    try:
        config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        DebugLog.exception("mainGUI.save_settings")
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
    DebugLog.add_secret(password)            # debug.log に書かない
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
    # 全窓でそろえる折りたたみ（App が枠ごとの開閉を持って全タブへ配る）
    SECTIONS = ("assign", "rounds")

    def __init__(self, parent, idx: int, on_log_selected=None, on_settings_changed=None,
                 section_collapsed=None, on_section_toggled=None):
        super().__init__(parent)
        self.idx = idx
        self._section_collapsed = dict(section_collapsed or {})
        self._on_section_toggled = on_section_toggled
        self.sections: dict = {}
        self._hwnd_map: dict[str, int] = {}
        # 窓↔ログの確定で分かったOSCポート（0ならOSC不可）。GUIには出さない
        self.osc_in = 0
        self.osc_out = 0
        self._on_log_selected = on_log_selected
        self._on_settings_changed = on_settings_changed
        self._build()
        self._watch_settings()

    def _section(self, parent, key: str, text: str) -> "CollapsibleFrame":
        """全窓でそろえる折りたたみを作る（既定は閉じた状態）"""
        def toggled(collapsed):
            if self._on_section_toggled is not None:
                self._on_section_toggled(key, collapsed)

        frame = CollapsibleFrame(parent, text=text,
                                 collapsed=self._section_collapsed.get(key, True),
                                 on_toggle=toggled)
        self.sections[key] = frame
        return frame

    def set_section_collapsed(self, key: str, collapsed: bool):
        frame = self.sections.get(key)
        if frame is not None:
            frame.set_collapsed(collapsed)

    def _watch_settings(self):
        """窓ごとの設定のチェックが変わったら知らせる（動作中の監視へ渡すため）"""
        if self._on_settings_changed is None:
            return
        for var in (self.v_auto_begin, self.v_do_skip, self.v_cancel_afk,
                    self.v_cancel_afk_after_unlock, self.v_announce_intermission, *self.v_skip_rounds.values(),
                    *self.v_continue_rounds.values()):
            var.trace_add("write", lambda *_a: self._on_settings_changed(self))

    def live_settings(self) -> dict:
        """動作中の監視（WindowConfig）へ渡す、窓ごとの設定"""
        return {
            "auto_begin": self.v_auto_begin.get(),
            "do_skip": self.v_do_skip.get(),
            "cancel_afk": self.v_cancel_afk.get(),
            "cancel_afk_after_unlock": self.v_cancel_afk_after_unlock.get(),
            "announce_intermission": self.v_announce_intermission.get(),
            "skip_rounds": {name for name, var in self.v_skip_rounds.items() if var.get()},
            "continue_rounds": {name for name, var in self.v_continue_rounds.items() if var.get()},
        }

    def snapshot(self) -> dict:
        """窓数を変えてタブを作り直すときに引き継ぐ、この窓の状態"""
        return {
            **self.live_settings(),
            "profile": self.v_profile.get(),
            "log": self.v_log.get(),
            "hwnd_choices": list(self._hwnd_map.items()),
            "hwnd_sel": self.v_hwnd_sel.get(),
            "osc": (self.osc_in, self.osc_out),
        }

    def restore(self, saved: dict):
        """snapshot() で控えた状態へ戻す"""
        for key, var in (("auto_begin", self.v_auto_begin), ("do_skip", self.v_do_skip),
                         ("cancel_afk", self.v_cancel_afk),
                         ("cancel_afk_after_unlock", self.v_cancel_afk_after_unlock),
                         ("announce_intermission", self.v_announce_intermission)):
            var.set(saved[key])
        for key, vars_ in (("skip_rounds", self.v_skip_rounds),
                           ("continue_rounds", self.v_continue_rounds)):
            for name, var in vars_.items():
                var.set(name in saved[key])
        self.v_profile.set(saved["profile"])
        self.v_log.set(saved["log"])
        self._hwnd_map = dict(saved["hwnd_choices"])
        self.cb_hwnd["values"] = list(self._hwnd_map)
        self.v_hwnd_sel.set(saved["hwnd_sel"])
        self.osc_in, self.osc_out = saved["osc"]

    def _build(self):
        p = self

        # HWNDとログは自動割り当てで決まるので既定は畳んでおく。
        # 開閉は全窓でそろえる（App._on_tab_section_toggled）
        assign = self._section(p, "assign", text="窓の割り当て")
        assign.pack(fill="x")
        a = assign.content

        def section(text):
            ttk.Label(a, text=text, background=config.GUI_BG, foreground=config.GUI_ACC,
                      font=(UIFont.UI, 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))

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
        self.v_cancel_afk_after_unlock = tk.BooleanVar(value=False)
        self.v_announce_intermission = tk.BooleanVar(value=False)
        ttk.Checkbutton(cf, text="自動Begin",                variable=self.v_auto_begin).pack(side="left")
        ttk.Checkbutton(cf, text="自動自爆",                 variable=self.v_do_skip).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="DTM/Waldo続行 (3クラまで)", variable=self.v_cancel_afk).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="3クラ解放後も続行",        variable=self.v_cancel_afk_after_unlock).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(cf, text="Intermissionアナウンス",    variable=self.v_announce_intermission).pack(side="left", padx=(12, 0))

        # ── ラウンドごとの自爆設定（privateのみ） ──
        rounds = self._section(
            p, "rounds", text="ラウンドごとの自爆設定（プライベートインスタンスのみ）")
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
            **self.live_settings(),
        ), None


# ── ログオーバーレイ ──────────────────────────────
def _valid_win_count(value):
    """settings.json の win_count を検証する。使えなければ None。

    壊れた値（文字列・0・上限超え・真偽値）で起動が止まらないように、
    ここで捨てる。None は「保存値なし」＝既定値へ
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= config.MAX_WINDOWS:
        return None
    return value


def _remember_own_window(widget):
    """当ツールの窓を覚える。録画中はここに入っている窓を隠す。

    VRChat の窓（SharedState.managed_hwnds）とは別の入れ物に入れること。
    混ぜると _vrchat_is_in_front() が誤判定して Begin が毎回フォールバックする
    """
    hwnd = WindowOperator.own_window_hwnd(widget)
    if not hwnd:
        return
    SharedState.register_own_window(hwnd)
    if SharedState.own_windows_hidden():
        WindowOperator.set_capture_excluded(hwnd, True)     # 録画の途中で開いた窓も外す

    def forget(event, _hwnd=hwnd, _widget=widget):
        if event.widget is _widget:      # 子ウィジェットの Destroy は無視
            SharedState.unregister_own_window(_hwnd)
            WindowOperator.set_capture_excluded(_hwnd, False)

    try:
        widget.bind("<Destroy>", forget, add="+")
    except tk.TclError:
        pass


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
        _remember_own_window(self)

        # ヘッダ（ドラッグ用）
        hf = tk.Frame(self, bg="#1e1e2e", cursor="fleur")
        hf.pack(fill="x")
        tk.Label(hf, text="ToNAutoBeginner Log", bg="#1e1e2e", fg="#89b4fa",
                 font=(UIFont.UI, 9, "bold")).pack(side="left", padx=6)
        tk.Button(hf, text="✕", bg="#1e1e2e", fg="#f38ba8",
                  font=(UIFont.UI, 9), relief="flat", bd=0,
                  command=self.close).pack(side="right", padx=4)
        # 透明度スライダー
        tk.Label(hf, text="α:", bg="#1e1e2e", fg="#cdd6f4",
                 font=(UIFont.UI, 8)).pack(side="right")
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
            font=(UIFont.UI, 9), state="disabled",
            relief="flat", bd=0, wrap="word",
            insertbackground="#cdd6f4"
        )
        self.text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # リサイズグリップ
        grip = tk.Label(self, text="⠿", bg="#000000", fg="#444444",
                        cursor="size_nw_se", font=(UIFont.UI, 10))
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
    def __init__(self, parent, text: str, collapsed: bool = False, on_toggle=None, **kwargs):
        super().__init__(parent, bg=config.GUI_BG, **kwargs)
        self._collapsed = collapsed
        self._on_toggle = on_toggle       # クリックで開閉したら on_toggle(collapsed)
        # ヘッダ行
        hf = tk.Frame(self, bg=config.GUI_BG)
        hf.pack(fill="x")
        self._toggle_btn = tk.Button(
            hf, text="▶" if collapsed else "▼",
            bg=config.GUI_BG, fg=config.GUI_ACC, font=(UIFont.UI, 9),
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle)
        self._toggle_btn.pack(side="left")
        tk.Label(hf, text=text, bg=config.GUI_BG, fg=config.GUI_ACC,
                 font=(UIFont.UI, 10, "bold")).pack(side="left", padx=4)
        # 区切り線
        tk.Frame(hf, bg=config.GUI_SUB, height=1).pack(side="left", fill="x", expand=True, pady=6)
        # コンテンツ領域
        self.content = tk.Frame(self, bg=config.GUI_BG)
        if not collapsed:
            self.content.pack(fill="x", padx=8, pady=(0, 4))

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def _toggle(self):
        self.set_collapsed(not self._collapsed)
        if self._on_toggle is not None:
            self._on_toggle(self._collapsed)

    def set_collapsed(self, collapsed: bool):
        """外から開閉を決める（on_toggle は呼ばない）"""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if self._collapsed:
            self.content.pack_forget()
            self._toggle_btn.config(text="▶")
        else:
            self.content.pack(fill="x", padx=8, pady=(0, 4))
            self._toggle_btn.config(text="▼")


class ReportDialog(tk.Toplevel):
    """不具合の報告。要件・窓・添付を選んで Discord の Webhook へ送る"""

    REPORT_NOTE = ("VRChat のログには、一緒にいた人の名前やユーザー ID、あなたの表示名が"
                   "含まれます。画面の撮影には一緒にいた人の名前が写ることがあります。"
                   "送り先は開発者の Discord です")

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("不具合の報告")
        self.configure(bg=config.GUI_BG)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="何が起きましたか（必須）").pack(anchor="w")
        self.text = tk.Text(body, width=60, height=8, bg="#181825", fg=config.GUI_FG,
                            insertbackground=config.GUI_FG, font=(UIFont.UI, 9))
        self.text.pack(fill="x", pady=(2, 6))
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="窓:").pack(side="left")
        windows = ["なし"] + [f"窓{tab.idx + 1}" for tab in app.tabs]
        self.v_window = tk.StringVar(value=app._current_tab_label())
        ttk.Combobox(row, textvariable=self.v_window, values=windows, state="readonly",
                     width=8).pack(side="left", padx=(6, 0))
        self.v_include = {}
        checks = ttk.Frame(body)
        checks.pack(fill="x")
        for key, label in BugReport.ATTACHMENTS:
            self.v_include[key] = tk.BooleanVar(value=True)
            ttk.Checkbutton(checks, text=label, variable=self.v_include[key]).pack(side="left", padx=(0, 10))
        ttk.Label(body, text=self.REPORT_NOTE, foreground=config.GUI_YLW,
                  wraplength=440).pack(anchor="w", pady=(6, 6))
        self.v_status = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.v_status, foreground=config.GUI_ORG,
                  wraplength=440).pack(anchor="w")
        self.btn_send = ttk.Button(body, text="送信", command=self._send, width=12)
        self.btn_send.pack(anchor="e", pady=(6, 0))
        self._refresh_cooldown()

    def _window_number(self) -> int:
        label = self.v_window.get()
        return int(label[1:]) if label.startswith("窓") and label[1:].isdigit() else 0

    def _refresh_cooldown(self):
        """送れてから REPORT_COOLDOWN_SEC は送信を押せない"""
        remain = self.app._report_cooldown_remaining()
        if remain > 0:
            self.btn_send.config(state="disabled")
            self.v_status.set(f"続けては送れません（あと{int(remain) + 1}秒）")
            self.after(1000, self._refresh_cooldown)
        elif not getattr(self, "_sending", False):
            self.btn_send.config(state="normal")
            if self.v_status.get().startswith("続けては送れません"):
                self.v_status.set("")

    def _send(self):
        requirement = self.text.get("1.0", "end").strip()
        if not requirement:
            self.v_status.set("何が起きたかを書いてください")
            return
        if self.app._report_cooldown_remaining() > 0:
            self._refresh_cooldown()
            return
        url = BugReport.webhook_url()
        if not url:
            self.v_status.set("送り先が設定されていません")
            self.app._log("[報告] 送り先が設定されていません")
            return
        window = self._window_number()
        include = {key: var.get() for key, var in self.v_include.items()}
        # Tk の値はメインスレッドで取っておく
        gui_log = self.app.log_text.get("1.0", "end")
        vrchat_log = self.app._tab_log_path(window)
        settings = load_settings()
        self._sending = True
        self.btn_send.config(state="disabled")
        self.v_status.set("送信中…")

        def worker():
            try:
                now = datetime.now()
                name, data = BugReport.build_report(requirement, window, include, gui_log,
                                                    vrchat_log, settings, now)
                ok, message = BugReport.send(url, BugReport.content(config.APP_VERSION, window,
                                                                    requirement, now), name, data)
            except Exception as e:
                DebugLog.exception("mainGUI.ReportDialog.worker")
                ok, message = False, f"送れませんでした（{type(e).__name__}）"
            try:
                self.app.after(0, lambda: self._done(ok, message))
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, ok: bool, message: str):
        self._sending = False
        self.app._log(f"[報告] {message}")
        if ok:
            self.app._report_sent_at = time.monotonic()
            try:
                self.destroy()
            except tk.TclError:
                pass
            return
        try:
            self.v_status.set(message)
            self.btn_send.config(state="normal")
        except tk.TclError:
            pass


class App(tk.Tk):
    # ── 続行リスト一式（self.lists）の窓口。代入すると全窓へ差し替わる ──
    def _shared_lists(self) -> MatchTNL.SharedLists:
        lists = self.__dict__.get("lists")
        if lists is None:
            lists = self.__dict__["lists"] = MatchTNL.SharedLists(participants=set())
        return lists

    @property
    def keepOn_set(self) -> dict:
        return self._shared_lists().keep_on

    @keepOn_set.setter
    def keepOn_set(self, value: dict):
        self._shared_lists().keep_on = value

    @property
    def host_wishes(self) -> dict:
        return self._shared_lists().wishes

    @host_wishes.setter
    def host_wishes(self, value: dict):
        self._shared_lists().wishes = value

    @property
    def host_participants(self):
        return self._shared_lists().participants

    @host_participants.setter
    def host_participants(self, value):
        self._shared_lists().participants = value

    @property
    def host_tabs(self) -> dict:
        return self._shared_lists().tabs

    @host_tabs.setter
    def host_tabs(self, value: dict):
        self._shared_lists().tabs = value

    def __init__(self):
        super().__init__()

        icon_path = resource_path("ToNAutoBeginnerIcon.ico")
        if icon_path.exists():
            self.iconbitmap(default=str(icon_path))

        # 画面のフォントはこの PC に在るものから選ぶ（root ができた後でないと見えない）
        UIFont.resolve(self)

        self.title("ToNAutoBeginner")
        # 大きさは中身に任せる。固定すると折りたたんでも縦が縮まない
        self.minsize(880, 600)
        self.configure(bg=config.GUI_BG)
        self.v_tnl       = tk.StringVar()
        self.v_win_count = tk.IntVar(value=4)
        # 手で変えた窓数（settings.json の win_count）。自動検出で入った値は
        # ここに入れない——VRChat を3窓だけ開いていた日に保存すると、次に
        # 開かずに起動したとき3が出てしまう。無ければ None
        self._win_count_pref: int | None = None
        # 続行リスト一式。全窓の LogMonitor が同じものを持つ。中身は差し替えで更新する
        # （keepOn_set・host_wishes・host_participants・host_tabs はこの属性の窓口）。
        # 参加者（＋自分）の名前は host_wishes に待機も入るので区別する
        self.lists = MatchTNL.SharedLists(participants=set())
        self._host_save_stamp: tuple | None = None   # (st_mtime, st_size)
        self._dropped_logs: tuple | None = None      # 候補から外したログ（同じ内容なら黙る）
        self._capture_warned = False                 # キャプチャ除外の警告は1度だけ
        self._host_save_warned = False               # 一時的な失敗の警告は1回だけ
        self._host_loss_since: float | None = None   # 主催リストが取れなくなった時刻
        self.monitors: list[LogMonitor.LogMonitor] = []
        self._running = False
        self._overlay: LogOverlay | None = None
        self._log_line_count = 0
        self._emergency_stop_key_pressed = False
        self._start_key_pressed = False
        # config の定数は既定値として読むだけ。実行時の値はこちらで持つ
        self.v_emergency_key = tk.StringVar(value=config.EMERGENCY_STOP_KEY)
        self.v_start_key = tk.StringVar(value=config.START_KEY)
        # キーの通知（keyboard のスレッド）が読む写し。StringVar は Tk のスレッドでしか読まない
        self._watched_keys = (config.EMERGENCY_STOP_KEY, config.START_KEY)
        for var in (self.v_emergency_key, self.v_start_key):
            var.trace_add("write", lambda *_: self._remember_watched_keys())
        self.v_suicide_cancel_key = tk.StringVar(value=config.SUICIDE_CANCEL_KEY)
        self._suicide_cancel_hook = None
        self._suicide_cancel_down = False
        self._capturing_key = False
        self._entry_stop = threading.Event()   # 入室時自動操作の中断フラグ
        self._launched_tab_indices: list[int] | None = None  # 今回起動した窓タブ
        self._stop_reason: str | None = None
        DebugLog.install_hooks(self)          # 例外をトレースバックごと debug.log へ
        self._build_ui()
        self._log(f"[画面] {UIFont.describe(self)}")
        self._load_saved_settings()
        self._auto_detect_windows()
        self._sync_launch_count()
        AutoUpdate.cleanup_old_exe()
        # 古い版の onefile の展開先を消す（exe のときだけ。消すのに時間がかかるので裏で）
        threading.Thread(target=AutoUpdate.cleanup_old_extract_dirs, daemon=True).start()
        self._cleanup_migrated_exe()
        # exe 単体ならインストーラー版へ移す。移すときは更新の確認はしない（Setup が最新）
        if not self._start_migration():
            self._start_update_check()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        _remember_own_window(self)       # 録画中だけキャプチャから外すため
        Recorder.set_window_hider(self._set_own_windows_hidden)
        self._start_emergency_stop_polling()
        self._start_host_save_polling()
        self._debug_environment()
        self._start_tool_poll()

    def _start_emergency_stop_polling(self):
        self._hook_chase_keys()
        self._hook_suicide_cancel_key()
        self._hook_stop_start_keys()

    # ── 緊急停止・マクロ開始のキー（押した瞬間の通知で拾う）──────────
    # 以前は 200ms ごとに is_pressed() を見ていた。それだと短く押したキーが見に行く合間に
    # 収まって取りこぼし（長押しが要った）、Tk のスレッドが重い間は検知も遅れる。
    # 押した・離した通知のたびに、keyboard のスレッドでその瞬間の状態を見る
    def _hook_stop_start_keys(self):
        """アプリ起動時に1回。keyboard が無ければ何もしない"""
        self._stop_start_hook = None
        if keyboard is None:
            return
        try:
            self._stop_start_hook = keyboard.hook(self._on_stop_start_key_event)
        except Exception as e:
            DebugLog.exception("mainGUI._hook_stop_start_keys")
            self._log(f"[緊急停止] ⚠ キーの通知を受けられません（{e}）")

    def _unhook_stop_start_keys(self):
        hook, self._stop_start_hook = getattr(self, "_stop_start_hook", None), None
        if hook is None:
            return
        try:
            keyboard.unhook(hook)
        except KeyError:
            pass                    # もう外れている
        except Exception:
            DebugLog.exception("mainGUI._unhook_stop_start_keys")

    def _remember_watched_keys(self):
        """Tk のスレッドで呼ぶ。キーを変えたら通知の側が読む写しも替える（代入1回で差し替える）"""
        self._watched_keys = (self.v_emergency_key.get(), self.v_start_key.get())

    def _on_stop_start_key_event(self, event=None):
        """keyboard のスレッドから、キーを押す・離すたびに呼ばれる。

        組み合わせ（ctrl+p など）もあるので、どのキーの通知かではなく、通知が来た瞬間に
        設定のキーが押されているかを見る。押されていなかった→押された、の1回だけ反応する
        （押しっぱなしの繰り返しでは増えない）。GUI の処理は Tk のスレッドで行う
        """
        if self._capturing_key:
            # 設定しようとしているキーで停止や開始がかかると困る
            self._emergency_stop_key_pressed = False
            self._start_key_pressed = False
            return
        stop_key, start_key = self._watched_keys
        self._check_stop_key(stop_key, event)
        self._check_start_key(start_key, event)

    @staticmethod
    def _key_down_now(key: str, event=None) -> bool:
        """そのキー（組み合わせも）がいま押されているか。

        keyboard は押された状態の表を通知より先に更新し、通知は別のスレッドで少し遅れて
        届く。遅れている間に離されると、表ではもう離れている。押した通知そのものが
        そのキーなら（組み合わせのほかのキーは表で見て）押されたものとして数える
        """
        if keyboard.is_pressed(key):
            return True
        if event is None or getattr(event, "event_type", None) != "down":
            return False
        code = getattr(event, "scan_code", None)
        steps = keyboard.parse_hotkey(key)
        if len(steps) != 1:
            return False                    # 順に押す形（a, b）は扱わない
        parts = steps[0]
        if not any(code in part for part in parts):
            return False
        return all(any(keyboard.is_pressed(c) for c in part)
                   for part in parts if code not in part)

    def _check_stop_key(self, key: str, event=None):
        try:
            now = self._key_down_now(key, event)
        except Exception:
            DebugLog.exception("mainGUI._check_stop_key")
            self._emergency_stop_key_pressed = False
            # 不正なキーだと is_pressed が投げる。握り潰すと緊急停止が黙って
            # 死ぬので、既定値へ戻して知らせる
            self._after_from_hook(self._fall_back_to_default_key,
                                  f"[緊急停止] ⚠ {key!r} は使えないキーです")
            return
        if now and not self._emergency_stop_key_pressed:
            self._after_from_hook(self._on_stop_key, key)
        self._emergency_stop_key_pressed = now

    def _check_start_key(self, key: str, event=None):
        """マクロ開始のキー。未設定なら何もしない（`is_pressed("")` を呼ばない）。不正なキーだった
        ときは、別のキーへ倒さずに無効へ戻す——勝手に動き出す方が危ないので、
        停止キーの `_fall_back_to_default_key()` とは逆に振る"""
        if not key:
            self._start_key_pressed = False
            return
        try:
            now = self._key_down_now(key, event)
        except Exception:
            DebugLog.exception("mainGUI._check_start_key")
            self._start_key_pressed = False
            self._after_from_hook(self._disable_broken_start_key, key)
            return
        if now and not self._start_key_pressed:
            self._after_from_hook(self._on_start_key, key)
        self._start_key_pressed = now

    def _after_from_hook(self, func, *args):
        try:
            self.after(0, func, *args)
        except (tk.TclError, RuntimeError):
            pass                    # 閉じた後に通知が来た

    def _on_stop_key(self, key: str):
        self._log(f"[緊急停止] {HotKey.display(key)}キーが押されました")
        self._stop_reason = "緊急停止キー"      # debug.log の停止の理由
        self._stop()

    def _on_start_key(self, key: str):
        if self._start_button_disabled():
            # 黙って無視すると「キーが効かない」と見える。押された瞬間だけ
            # 出すので、押し続けても増えない
            self._log("[マクロ開始] いま押せません（動作中か起動中）")
            return
        self._log(f"[マクロ開始] {HotKey.display(key)}キーが押されました")
        self._start()

    def _disable_broken_start_key(self, key: str):
        if self.v_start_key.get() != key:
            return                  # もう別のキーに変わっている
        self.v_start_key.set("")
        self._start_key_pressed = False
        self._refresh_start_key_label()
        self._log(f"[マクロ開始] ⚠ {key!r} は使えないキーです。解除しました")

    # ── チェイスのキー（押した瞬間の通知で拾う）──────────────
    # 200ms ごとの is_pressed() では、短く押した F1/F2（0.1秒前後）が見に行く
    # 合間に収まって取りこぼす。押した・離した通知で拾う（suppress しない）
    def _hook_chase_keys(self):
        """アプリ起動時に1回。keyboard が無ければ何もしない"""
        self._chase_keys_down = set()
        self._chase_hooks = []
        if keyboard is None:
            return
        for direction, key in (("cw", config.CHASE_CW_KEY), ("ccw", config.CHASE_CCW_KEY)):
            # 1キーに1つのフック。keyboard は同じキーのフックを1つの表の項目で持つので、
            # 押す・離すを別々に登録すると、外すときに2つ目が KeyError になる（離す方が
            # 外れずに残る）。押した・離したは event_type で分ける
            def on_key(event, d=direction, k=key):
                if getattr(event, "event_type", None) == "down":
                    self._chase_key_down(d, k)
                else:
                    self._chase_keys_down.discard(k)
            try:
                self._chase_hooks.append(keyboard.hook_key(key, on_key, suppress=False))
            except Exception as e:
                DebugLog.exception("mainGUI._hook_chase_keys")
                self._log(f"[チェイス] ⚠ {HotKey.display(key)}キーを登録できません（{e}）")

    def _chase_key_down(self, direction: str, key: str):
        """keyboard のスレッドから呼ばれる。押しっぱなしの繰り返しは離すまで1回。
        GUI の処理は Tk のスレッドで行う"""
        if key in self._chase_keys_down:
            return
        self._chase_keys_down.add(key)
        if self._capturing_key:
            return                  # キーの設定中は反応しない
        try:
            self.after(0, self._on_chase_key, direction, key)
        except (tk.TclError, RuntimeError):
            pass                    # 閉じた後に通知が来た

    # ── 自爆キャンセルのキー（押した瞬間の通知で拾う）──────────
    # ツール自身が背面で送る自爆の ^ は PostMessage なので、低レベルのフックには来ない
    def _hook_suicide_cancel_key(self):
        """今のキーで受け直す（起動時と、キーを変えたとき）"""
        self._unhook_suicide_cancel_key()
        key = self.v_suicide_cancel_key.get()
        if keyboard is None or not key:
            return

        def on_key(event, k=key):
            if getattr(event, "event_type", None) == "down":
                self._suicide_cancel_key_down(k)
            else:
                self._suicide_cancel_down = False
        try:
            self._suicide_cancel_hook = keyboard.hook_key(key, on_key, suppress=False)
        except Exception as e:
            DebugLog.exception("mainGUI._hook_suicide_cancel_key")
            self._log(f"[自爆キャンセル] ⚠ {HotKey.display(key)}キーを登録できません（{e}）")

    def _unhook_suicide_cancel_key(self):
        hook, self._suicide_cancel_hook = getattr(self, "_suicide_cancel_hook", None), None
        self._suicide_cancel_down = False
        if hook is None:
            return
        try:
            keyboard.unhook(hook)
        except KeyError:
            pass                    # もう外れている
        except Exception:
            DebugLog.exception("mainGUI._unhook_suicide_cancel_key")

    def _suicide_cancel_key_down(self, key: str):
        """keyboard のスレッドから呼ばれる。押しっぱなしの繰り返しは離すまで1回"""
        if self._suicide_cancel_down:
            return
        self._suicide_cancel_down = True
        if self._capturing_key:
            return                  # キーの設定中は反応しない
        try:
            self.after(0, self._on_suicide_cancel_key, key)
        except (tk.TclError, RuntimeError):
            pass                    # 閉じた後に通知が来た

    def _on_suicide_cancel_key(self, key: str):
        """全部の窓の自爆を止め、そのラウンドはもう自爆しない。ラウンド外の窓は何もしない"""
        if not self._running or not self.monitors:
            return
        results = [monitor.cancel_suicide() for monitor in self.monitors]
        stopped = results.count("stopped")
        head = f"[自爆キャンセル] {HotKey.display(key)}キーが押されました"
        if stopped:
            self._log(f"{head} → {stopped}窓の自爆を止めました")
        elif "marked" in results:
            self._log(f"{head} → 自爆中の窓はありません（このラウンドは自爆しません）")
        else:
            self._log(f"{head} → ラウンド中の窓はありません（何もしません）")

    def _unhook_chase_keys(self):
        """アプリ終了時に外す"""
        for hook in getattr(self, "_chase_hooks", []):   # 登録できたものだけ
            try:
                keyboard.unhook(hook)
            except KeyError:
                pass                    # もう外れている。例外として書かない
            except Exception:
                DebugLog.exception("mainGUI._unhook_chase_keys")
        self._chase_hooks = []

    def _on_chase_key(self, direction: str, key: str):
        """押した瞬間に前面の、監視している窓だけを回す。それ以外は何もしない
        （ほかのアプリで F1 を使っていることがある）"""
        if not self._running or not self.monitors:
            return
        front = WindowOperator.foreground_hwnd()
        for monitor in self.monitors:
            if front and monitor.cfg.hwnd == front:
                monitor.on_chase_key(direction, HotKey.display(key))
                return

    def _start_button_disabled(self) -> bool:
        """「▶ マクロ開始」が押せない状態か（動作中・起動中など）。

        走っているかを自前で持たず、ボタンの状態で見る。押せない場面は
        ボタンを disabled にすることで表しているため
        """
        try:
            return str(self.btn_start.cget("state")) == "disabled"
        except (tk.TclError, AttributeError):
            return True

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
                text=f"緊急停止: {HotKey.display(self.v_emergency_key.get())}キー")
        except (tk.TclError, AttributeError):
            pass

    def _refresh_suicide_cancel_key_label(self):
        key = self.v_suicide_cancel_key.get()
        text = ("自爆キャンセル: 未設定" if not key
                else f"自爆キャンセル: {HotKey.display(key)}キー")
        try:
            self.lbl_suicide_cancel_key.config(text=text)
        except (tk.TclError, AttributeError):
            pass

    def _suicide_cancel_key_conflict(self, key) -> str | None:
        """自爆キャンセルのキーと重なるほかのキーの名前。重ならなければ None"""
        for name, other in (("緊急停止", self.v_emergency_key.get()),
                            ("マクロ開始", self.v_start_key.get()),
                            ("チェイス", config.CHASE_CW_KEY),
                            ("チェイス", config.CHASE_CCW_KEY)):
            if other and key == other:
                return name
        return None

    def _refresh_start_key_label(self):
        key = self.v_start_key.get()
        text = ("マクロ開始: 未設定" if not key
                else f"マクロ開始: {HotKey.display(key)}キー")
        try:
            self.lbl_start_key.config(text=text)
        except (tk.TclError, AttributeError):
            pass

    def _clear_start_key(self):
        """マクロ開始のキーを消して無効に戻す"""
        if not self.v_start_key.get():
            self._log("[マクロ開始] すでに未設定です")
            return
        self.v_start_key.set("")
        self._start_key_pressed = False
        self._refresh_start_key_label()
        self._log("[マクロ開始] 解除しました")

    def _capture_button(self, target: str):
        if target == "cancel":
            return self.btn_capture_cancel_key
        return self.btn_capture_start_key if target == "start" else self.btn_capture_key

    def _begin_capture_key(self, target: str = "stop"):
        """「キーを押して設定」。捕捉は別スレッド——read_hotkey は待つのでGUIが固まる。

        target は "stop"（緊急停止）か "start"（マクロ開始）。捕捉中は
        _capturing_key で両方の検知を止める（設定しようとした打鍵で
        停止したり動き出したりしないため）
        """
        if self._capturing_key:
            return
        self._capturing_key = True
        try:
            self._capture_button(target).config(text="キーを押してください…",
                                                state="disabled")
        except (tk.TclError, AttributeError):
            pass

        def worker():
            key = HotKey.capture(config.EMERGENCY_KEY_CAPTURE_SEC)
            try:
                self.after(0, lambda: self._finish_capture_key(key, target))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_capture_key(self, key, target: str = "stop"):
        self._capturing_key = False
        self._emergency_stop_key_pressed = False
        self._start_key_pressed = False
        try:
            self._capture_button(target).config(text="キーを押して設定",
                                                state="normal")
        except (tk.TclError, AttributeError):
            pass
        if target == "start":
            self._finish_capture_start_key(key)
            return
        if target == "cancel":
            self._finish_capture_cancel_key(key)
            return
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
        if key == self.v_start_key.get():
            self._log("[緊急停止] ⚠ マクロ開始キーと同じキーは使えません。"
                      "設定は変えていません")
            return
        if key == self.v_suicide_cancel_key.get():
            self._log("[緊急停止] ⚠ 自爆キャンセルのキーと同じキーは使えません。"
                      "設定は変えていません")
            return
        self.v_emergency_key.set(key)
        self._refresh_emergency_key_label()
        self._log(f"[緊急停止] {HotKey.display(key)}キーに変更しました")

    def _finish_capture_start_key(self, key):
        """マクロ開始のキーを設定する。

        不正なときは既定値へ倒さない（勝手に動き出す方が危ない）。取れなかった
        ときも不正なときも、いまの設定をそのまま残す
        """
        if key is None:
            self._log("[マクロ開始] ⚠ キーを取れませんでした。設定は変えていません")
            return
        if not HotKey.is_valid(key):
            self._log(f"[マクロ開始] ⚠ {key!r} は使えないキーです。"
                      "設定は変えていません")
            return
        if key == self.v_emergency_key.get():
            self._log("[マクロ開始] ⚠ 緊急停止キーと同じキーは使えません。"
                      "設定は変えていません")
            return
        if key == self.v_suicide_cancel_key.get():
            self._log("[マクロ開始] ⚠ 自爆キャンセルのキーと同じキーは使えません。"
                      "設定は変えていません")
            return
        self.v_start_key.set(key)
        self._start_key_pressed = False
        self._refresh_start_key_label()
        self._log(f"[マクロ開始] {HotKey.display(key)}キーに変更しました")

    def _finish_capture_cancel_key(self, key):
        """自爆キャンセルのキーを設定する。取れない・不正・ほかのキーと同じなら今のまま"""
        if key is None:
            self._log("[自爆キャンセル] ⚠ キーを取れませんでした。設定は変えていません")
            return
        if not HotKey.is_valid(key):
            self._log(f"[自爆キャンセル] ⚠ {key!r} は使えないキーです。設定は変えていません")
            return
        other = self._suicide_cancel_key_conflict(key)
        if other:
            self._log(f"[自爆キャンセル] ⚠ {other}のキーと同じキーは使えません。"
                      "設定は変えていません")
            return
        self.v_suicide_cancel_key.set(key)
        self._refresh_suicide_cancel_key_label()
        self._hook_suicide_cancel_key()
        self._log(f"[自爆キャンセル] {HotKey.display(key)}キーに変更しました")

    def _build_ui(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",            background=config.GUI_BG)
        s.configure("TLabel",            background=config.GUI_BG, foreground=config.GUI_FG, font=(UIFont.UI, 10))
        s.configure("TButton",           font=(UIFont.UI, 10, "bold"), padding=4)
        s.configure("TCheckbutton",      background=config.GUI_BG, foreground=config.GUI_FG, font=(UIFont.UI, 10))
        s.map("TCheckbutton", background=[("active", config.GUI_BG)])
        s.configure("TLabelframe",       background=config.GUI_BG, foreground=config.GUI_ACC)
        s.configure("TLabelframe.Label", background=config.GUI_BG, foreground=config.GUI_ACC,
                    font=(UIFont.UI, 10, "bold"))
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
        ttk.Label(lf2, text="※ 外すとホームで起動します（立っているインスタンスへ自分で入る用）",
                  foreground=config.GUI_YLW).pack(side="left", padx=(8, 0))

        lf22 = ttk.Frame(f2_launch)
        lf22.pack(fill="x", pady=(4, 0))
        ttk.Label(lf22, text="インスタンスタイプ:").pack(side="left")
        self.v_ton_access = tk.StringVar(value=config.TON_INSTANCE_ACCESS_DEFAULT)
        ttk.Radiobutton(lf22, text="インバイト", variable=self.v_ton_access,
                        value=config.TON_INSTANCE_ACCESS_INVITE).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(lf22, text="インバイト+", variable=self.v_ton_access,
                        value=config.TON_INSTANCE_ACCESS_INVITE_PLUS).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(lf22, text="フレンド", variable=self.v_ton_access,
                        value=config.TON_INSTANCE_ACCESS_FRIENDS).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(lf22, text="フレンド+", variable=self.v_ton_access,
                        value=config.TON_INSTANCE_ACCESS_FRIENDS_PLUS).pack(side="left", padx=(6, 0))
        ttk.Label(lf22, text="※ 窓ごとにToNの新規インスタンスを作ります",
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

        # 起動オプション（config.LAUNCH_OPTION の後ろに足す。空白で区切る。既定は空）
        lf26 = ttk.Frame(f2_launch)
        lf26.pack(fill="x", pady=(4, 0))
        ttk.Label(lf26, text="起動オプション:").pack(side="left")
        self.v_launch_options = tk.StringVar(value="")
        ttk.Entry(lf26, textvariable=self.v_launch_options, width=60).pack(side="left", padx=(6, 0))


        # ③ 窓タブ
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="x", expand=False, padx=12, pady=4)
        self.tabs: list[WindowTab] = []
        # 窓タブの折りたたみの開閉（True = 閉じている）。全タブで共通。保存しない
        self._tab_sections = {key: True for key in WindowTab.SECTIONS}
        self._rebuild_tabs(self.v_win_count.get())

        # 音声ファイル設定
        fv_wrap = CollapsibleFrame(self, text="③ サウンド設定", collapsed=True)
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
        # 音声ファイルの12行は「アナウンス」に畳む（長すぎるので。既定は閉じた状態。
        # ② の「VRChat起動の詳細設定」と同じ入れ子）。音量の2つはこの下（外）に残す
        self._announce_frame = CollapsibleFrame(fv, text="アナウンス", collapsed=True)
        self._announce_frame.pack(fill="x", pady=(0, 2))
        fva = self._announce_frame.content
        voice_row(fva, "続行ラウンド:", self.v_voice_continue)
        voice_row(fva, "Alternate:", self.v_voice_alternate)
        voice_row(fva, "Midnight:", self.v_voice_midnight)
        voice_row(fva, "Unbound:", self.v_voice_unbound)
        voice_row(fva, "Fog:", self.v_voice_fog)
        voice_row(fva, "Ghost:", self.v_voice_ghost)
        voice_row(fva, "8 Pages(速度検知):", self.v_voice_8pages)
        voice_row(fva, "Punish(速度検知):", self.v_voice_punish)
        voice_row(fva, "アイテムロスト:", self.v_voice_item_lost)
        voice_row(fva, "Intermission:", self.v_voice_intermission)
        voice_row(fva, "Foxy:", self.v_voice_foxy)
        voice_row(fva, "主催リスト喪失:", self.v_voice_list_lost)

        # 音量スライダー
        volf = ttk.Frame(fv)
        volf.pack(fill="x", pady=(6, 0))
        ttk.Label(volf, text="ツールの声の音量:").pack(side="left")
        self.v_volume = tk.DoubleVar(value=1.0)
        ttk.Scale(volf, from_=0.0, to=1.0, variable=self.v_volume,
                  orient="horizontal", length=160,
                  command=lambda v: PlaySound.set_sound_volume(float(v))).pack(side="left", padx=(6, 4))
        self.lbl_volume = ttk.Label(volf, text="100%")
        self.lbl_volume.pack(side="left")
        self.v_volume.trace_add("write", lambda *_: self.lbl_volume.config(
            text=f"{int(self.v_volume.get()*100)}%"))
        self._build_window_volume(fv)

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
        ttk.Button(fc, text="報告",
                   command=self._open_report, width=8).pack(side="left", padx=6)

        # キー設定の行: マクロ開始のキー、続けて緊急停止のキー（依頼者の決定）。
        # ボタンの行（fc）には入れない（窓の幅を広げると別のPCで崩れる。47aefa4 / 73e577c）
        fsk = ttk.Frame(self)
        fsk.pack(pady=(0, 4))
        self.lbl_start_key = ttk.Label(fsk, text="", foreground=config.GUI_ORG)
        self.lbl_start_key.pack(side="left")
        self.btn_capture_start_key = ttk.Button(
            fsk, text="キーを押して設定", width=16,
            command=lambda: self._begin_capture_key("start"))
        self.btn_capture_start_key.pack(side="left", padx=(6, 0))
        self.btn_clear_start_key = ttk.Button(fsk, text="解除", width=6,
                                              command=self._clear_start_key)
        self.btn_clear_start_key.pack(side="left", padx=(4, 0))
        self._refresh_start_key_label()
        self.lbl_emergency = ttk.Label(fsk, text="",
                                       foreground=config.GUI_ORG)
        self.lbl_emergency.pack(side="left", padx=(16, 0))
        self.btn_capture_key = ttk.Button(fsk, text="キーを押して設定", width=16,
                                          command=self._begin_capture_key)
        self.btn_capture_key.pack(side="left", padx=(6, 0))
        self._refresh_emergency_key_label()
        # 自爆キャンセルのキーは行の最後
        self.lbl_suicide_cancel_key = ttk.Label(fsk, text="", foreground=config.GUI_ORG)
        self.lbl_suicide_cancel_key.pack(side="left", padx=(16, 0))
        self.btn_capture_cancel_key = ttk.Button(
            fsk, text="キーを押して設定", width=16,
            command=lambda: self._begin_capture_key("cancel"))
        self.btn_capture_cancel_key.pack(side="left", padx=(6, 0))
        self._refresh_suicide_cancel_key_label()

        # 完全放置モード（全窓共通）
        fhf = ttk.Frame(self)
        fhf.pack(pady=(0, 4))
        self.btn_hands_free = tk.Button(
            fhf, text="完全放置モード: OFF",
            bg=config.GUI_SUB, fg=config.GUI_FG, font=(UIFont.UI, 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_hands_free)
        self.btn_hands_free.pack(side="left")

        # アイテム取得→Begin
        self.btn_item_get_begin = tk.Button(
            fhf, text="アイテム取得→Begin: OFF",
            bg=config.GUI_SUB, fg=config.GUI_FG, font=(UIFont.UI, 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_item_get_begin)
        self.btn_item_get_begin.pack(side="left", padx=(8, 0))

        # 速度によるラウンド種別の検知（全窓共通）
        self.btn_speed_detect = tk.Button(
            fhf, text="", bg=config.GUI_SUB, fg=config.GUI_FG,
            font=(UIFont.UI, 11, "bold"), relief="raised", padx=12, pady=5,
            command=self._toggle_speed_detect)
        self.btn_speed_detect.pack(side="left", padx=(8, 0))
        self._refresh_speed_detect_button()

        # アイテム自動取得（全窓共通・保存・既定 OFF）
        self.btn_item_fetch = tk.Button(
            fhf, text="", bg=config.GUI_SUB, fg=config.GUI_FG,
            font=(UIFont.UI, 11, "bold"), relief="raised", padx=12, pady=5,
            command=self._toggle_item_fetch)
        self.btn_item_fetch.pack(side="left", padx=(8, 0))
        self._refresh_item_fetch_button()

        # フリーズ設定（全窓共通）。フリーズは全窓を止める仕組みなので窓ごとに分けない
        ffz = ttk.Frame(self)
        ffz.pack(pady=(0, 4))
        self.v_freeze_8pages = tk.BooleanVar(value=False)
        self.v_freeze_punish = tk.BooleanVar(value=False)
        ttk.Checkbutton(ffz, text="8 Pages検知でフリーズ", variable=self.v_freeze_8pages,
                        command=lambda: self._on_speed_freeze_changed("8 Pages")
                        ).pack(side="left")
        ttk.Checkbutton(ffz, text="Punished検知でフリーズ", variable=self.v_freeze_punish,
                        command=lambda: self._on_speed_freeze_changed("Punished")
                        ).pack(side="left", padx=(12, 0))
        # 霧看破の許可（ツール全体で1つ。既定は切る）
        self.v_fog_early_read = tk.BooleanVar(value=False)
        ttk.Checkbutton(ffz, text="霧を即時判定する（Friends・Invite+・Invite のみ）",
                        variable=self.v_fog_early_read,
                        command=self._on_fog_early_read_changed).pack(side="left", padx=(12, 0))

        # 続行ラウンドの後（全窓共通・保存・既定 ON）
        fcr = ttk.Frame(self)
        fcr.pack(pady=(0, 4))
        self.v_continue_drop_item = tk.BooleanVar(value=True)
        self.v_continue_restore_window = tk.BooleanVar(value=True)
        ttk.Checkbutton(fcr, text="続行ラウンドの後にアイテムを落とす",
                        variable=self.v_continue_drop_item,
                        command=self._on_continue_after_changed).pack(side="left")
        ttk.Checkbutton(fcr, text="続行ラウンドの後に窓を元の位置に戻す",
                        variable=self.v_continue_restore_window,
                        command=self._on_continue_after_changed).pack(side="left", padx=(12, 0))

        ffr = ttk.Frame(self)
        ffr.pack(pady=(0, 4))
        ttk.Label(ffr, text="ラウンド突入でフリーズ:").pack(side="left")
        self.v_freeze_rounds = freeze_round_vars(lambda: tk.BooleanVar(value=False))
        for name, var in self.v_freeze_rounds.items():
            ttk.Checkbutton(ffr, text=name, variable=var,
                            command=self._apply_freeze_settings).pack(side="left", padx=(8, 0))

        # ログ
        fl = ttk.LabelFrame(self, text="ログ出力", padding=4)
        fl.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(
            fl, height=16, bg="#181825", fg=config.GUI_FG, width=80,
            font=(UIFont.UI, 9), state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(fl, text="クリア", command=self._clear_log).pack(anchor="e", pady=(2, 0))

        # クレジット表示
        tk.Label(self, text="Credit: VOICEVOX冥鳴ひまり",
                 bg=config.GUI_BG, fg=config.GUI_SUB, font=(UIFont.UI, 8)).pack(anchor="e", padx=12)

    def _rebuild_tabs(self, count: int):
        # 窓数を変えても、窓ごとの設定（自動Begin・自動自爆・ラウンド指定・割り当てなど）は
        # そのまま。減らした窓の分も覚えておき、また増やしたら戻す（このツールを閉じるまで）
        memory = getattr(self, "_tab_memory", None)
        if memory is None:
            memory = self._tab_memory = {}
        for tab in self.tabs:
            memory[tab.idx] = tab.snapshot()
        for tab in self.tabs:
            try:
                self.nb.forget(tab)
            except tk.TclError:
                pass
            tab.destroy()
        self.tabs.clear()
        for i in range(count):
            tab = WindowTab(self.nb, i, on_log_selected=self._on_tab_log_selected,
                            on_settings_changed=self._apply_tab_settings_live,
                            section_collapsed=self._tab_sections,
                            on_section_toggled=self._on_tab_section_toggled)
            self.nb.add(tab, text=f"窓{i + 1}")
            self.tabs.append(tab)
        self._apply_saved_window_settings()
        for tab in self.tabs:
            if tab.idx in memory:
                tab.restore(memory[tab.idx])

    def _on_tab_section_toggled(self, key: str, collapsed: bool):
        """窓タブの折りたたみを1つ開閉したら、全タブの同じ枠をそろえる（保存はしない）"""
        self._tab_sections[key] = collapsed
        for tab in self.tabs:
            tab.set_section_collapsed(key, collapsed)

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
            # 変わっていない。スピンボックスからフォーカスが外れただけでも
            # ここへ来るので、自動検出の値を「手で選んだ値」として覚えない
            return
        self._win_count_pref = n
        self._schedule_settings_save()   # 変えた直後に落ちても残るように
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

    # ── VRChat の窓の音量 ──────────────────────
    def _build_window_volume(self, parent):
        """枠「VRChat の窓の音量」。変えたらその場で見張りへ渡し、保存する"""
        self._window_volume = None
        box = ttk.LabelFrame(parent, text="VRChat の窓の音量")
        box.pack(fill="x", pady=(6, 0))
        self.v_wvol_enabled = tk.BooleanVar(value=config.DEFAULT_WINDOW_VOLUME_ENABLED)
        ttk.Checkbutton(box, text="窓の状態で VRChat の音量を変える",
                        variable=self.v_wvol_enabled,
                        command=self._on_window_volume_changed).pack(anchor="w")
        ttk.Label(box, text="続行ラウンドの窓・フリーズを張った窓・それ以外で、Windows の"
                            "音量ミキサーの音量を切り替えます。停止で元に戻します",
                  wraplength=420).pack(anchor="w")
        self.v_wvol = {}
        for key, label, default in (
                (WindowVolume.CONTINUE, "続行", config.DEFAULT_WINDOW_VOLUME_CONTINUE),
                (WindowVolume.FREEZE, "フリーズ窓", config.DEFAULT_WINDOW_VOLUME_FREEZE),
                (WindowVolume.OTHER, "その他", config.DEFAULT_WINDOW_VOLUME_OTHER)):
            row = ttk.Frame(box)
            row.pack(fill="x")
            ttk.Label(row, text=f"{label}:", width=10).pack(side="left")
            var = tk.IntVar(value=default)
            ttk.Scale(row, from_=0, to=100, variable=var, orient="horizontal", length=160,
                      command=lambda _v, x=var: (x.set(int(float(_v))),
                                                 self._on_window_volume_changed())
                      ).pack(side="left", padx=(6, 4))
            shown = ttk.Label(row, text=f"{default}%")
            shown.pack(side="left")
            var.trace_add("write", lambda *_a, x=var, s=shown: s.config(text=f"{x.get()}%"))
            self.v_wvol[key] = var

    def _window_volume_values(self) -> tuple:
        return (bool(self.v_wvol_enabled.get()),
                {key: int(var.get()) for key, var in self.v_wvol.items()})

    def _on_window_volume_changed(self):
        self._apply_window_volume_settings()
        self._schedule_settings_save()

    def _apply_window_volume_settings(self):
        """動作中なら見張りへ渡す（次の回で効く）"""
        controller = getattr(self, "_window_volume", None)
        if controller is None:
            return
        enabled, levels = self._window_volume_values()
        controller.set_levels(levels[WindowVolume.CONTINUE], levels[WindowVolume.FREEZE],
                              levels[WindowVolume.OTHER])
        controller.set_enabled(enabled)

    def _load_window_volume_settings(self, data: dict):
        enabled, levels = WindowVolume.levels_from_settings(data)
        self.v_wvol_enabled.set(enabled)
        for key, var in self.v_wvol.items():
            var.set(levels[key])

    def _window_volume_settings(self) -> dict:
        enabled, levels = self._window_volume_values()
        return {"window_volume_enabled": enabled,
                "window_volume_continue": levels[WindowVolume.CONTINUE],
                "window_volume_freeze": levels[WindowVolume.FREEZE],
                "window_volume_other": levels[WindowVolume.OTHER]}

    def _start_window_volume(self):
        monitors = list(self.monitors)
        self._window_volume = WindowVolume.VolumeController(
            lambda: [(m.window_idx, m.cfg.hwnd, m.st) for m in monitors], self._log)
        self._apply_window_volume_settings()
        self._window_volume.start()

    def _stop_window_volume(self):
        """見張りを止めて元の音量に戻す（止めるのは監視より先）"""
        controller, self._window_volume = getattr(self, "_window_volume", None), None
        if controller is not None:
            controller.stop()

    def _on_fog_early_read_changed(self):
        FogEarlyRead.set_early_read_enabled(self.v_fog_early_read.get())   # 次の判定から効く
        self._schedule_settings_save()

    def _on_continue_after_changed(self):
        SharedState.set_continue_drop_item(self.v_continue_drop_item.get())
        SharedState.set_continue_restore_window(self.v_continue_restore_window.get())
        self._schedule_settings_save()

    def _refresh_continue_after_checks(self):
        """チェックの表示を SharedState（保存・読み込みの元）に合わせる"""
        try:
            self.v_continue_drop_item.set(SharedState.get_continue_drop_item())
            self.v_continue_restore_window.set(SharedState.get_continue_restore_window())
        except (tk.TclError, AttributeError):
            pass

    def _load_fog_early_read_setting(self, data: dict):
        enabled = data.get("fog_early_read_enabled", False)
        enabled = enabled if isinstance(enabled, bool) else False   # 古い・壊れた値は切る
        self.v_fog_early_read.set(enabled)
        FogEarlyRead.set_early_read_enabled(enabled)

    def _load_launch_options_setting(self, data: dict):
        extra = data.get("launch_extra_options", "")
        self.v_launch_options.set(extra if isinstance(extra, str) else "")   # 壊れた値は空

    def _launch_options_setting(self) -> dict:
        return {"launch_extra_options": str(self.v_launch_options.get())}

    def _fog_early_read_setting(self) -> dict:
        return {"fog_early_read_enabled": bool(self.v_fog_early_read.get())}

    def _on_speed_freeze_changed(self, round_name: str):
        """速度検知でフリーズにチェックを入れたら、ラウンド突入でフリーズの同じラウンドも
        入れる。入れるときだけ（外しても突入側はそのまま）。起動時の読み込みでは呼ばない"""
        var = self.v_freeze_8pages if round_name == "8 Pages" else self.v_freeze_punish
        if var.get() and round_name in self.v_freeze_rounds:
            self.v_freeze_rounds[round_name].set(True)
        self._apply_freeze_settings()
        self._schedule_settings_save()

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

    def _refresh_item_fetch_button(self):
        on = SharedState.get_item_fetch()
        try:
            self.btn_item_fetch.config(
                text=f"アイテム自動取得: {'ON' if on else 'OFF'}",
                bg="#1a3a2a" if on else config.GUI_SUB,
                fg="#a6e3a1" if on else config.GUI_FG,
                relief="sunken" if on else "raised")
        except (tk.TclError, AttributeError):
            pass

    def _toggle_item_fetch(self):
        val = not SharedState.get_item_fetch()
        SharedState.set_item_fetch(val)
        self._refresh_item_fetch_button()
        self._log("[アイテム自動取得] ON: アイテムロストのとき、Begin が通った後に店で装備します"
                  "（ツールが Begin を押す OSC の窓だけ）" if val else "[アイテム自動取得] OFF")
        self._schedule_settings_save()

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
        """続行リストを差し替える（新しい dict の代入。走行中の全窓に即座に効く）。

        中身の入れ替え（clear → update）はしない。その間に判定した窓が空のリストを
        見て、続行すべきラウンドで自爆してしまう（MatchTNL.SharedLists）
        """
        self.keepOn_set = dict(new_set)

    def _apply_host_tabs(self, tabs_data: dict):
        """タブごとの内訳を差し替える。版を上げて、窓側の対応づけ（名前の重なり）を計算し直させる"""
        version = (self.host_tabs or {}).get("version", 0) + 1
        self.host_tabs = {"version": version, "tabs": dict(tabs_data or {})}

    def _apply_host_wishes(self, new_wishes: dict, participants=()):
        """参加者別の希望と参加者の名前を差し替える"""
        self.host_wishes = dict(new_wishes)
        if getattr(self, "host_participants", None) is not None:
            self.host_participants = set(participants)

    def _start_host_save_polling(self):
        self.after(int(config.HOST_SAVE_POLL_SEC * 1000), self._poll_host_save)

    def _poll_host_save(self):
        """HOST_SAVE_POLL_SEC ごと。プロセスとファイルを読むのは裏のスレッド、結果を当てるのは
        Tk のスレッド（緊急停止・画面を止めない）。次の予約は当て終えてから（読みが重なる
        ことは無い）"""
        def work():
            try:
                result = self._read_host_source()
            except Exception:
                DebugLog.exception("mainGUI._poll_host_save")
                result = None
            self._after_from_worker(self._finish_host_poll, result)
        self._off_gui(work)

    def _finish_host_poll(self, result):
        try:
            if result is not None:
                self._apply_host_source(result)
        except Exception:
            DebugLog.exception("mainGUI._finish_host_poll")
        try:
            self.after(int(config.HOST_SAVE_POLL_SEC * 1000), self._poll_host_save)
        except tk.TclError:
            pass

    @staticmethod
    def _off_gui(work):
        """Tk のスレッドを止めずに work を走らせる（テストでは同じスレッドで走らせる）"""
        threading.Thread(target=work, daemon=True).start()

    def _after_from_worker(self, func, *args):
        """裏のスレッドから Tk のスレッドへ渡す。閉じた後なら捨てる"""
        try:
            self.after(0, func, *args)
        except (tk.TclError, RuntimeError):
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
        self._apply_host_tabs({})
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
        ツールを閉じても保存ファイルはディスクに残るので、鮮度は
        mtime ではなくプロセスの生死で見る（古いファイルを掴まないため）。

        ToN ListTool 2.13 以降の host_state.sqlite3 だけを読む。無ければ
        取れないものとして扱う（猶予のあと .tnl へ）。古い host_save.json.gz へは
        切り替えない——ディスクに何日も前のものが残っていることがあり、
        SQLite が一瞬無いだけで古い参加者と古い続行リストで判定してしまう。
        """
        self._apply_host_source(self._read_host_source())

    def _read_host_source(self) -> tuple:
        """主催リストの元を読む。プロセスとファイルだけを見て、画面と状態には触らない
        （裏のスレッドで呼んでよい）。戻り値は (種類, 中身):
        ("lost", 理由) / ("same", None) / ("error", 文言) / ("loaded", (keep_on, meta, wishes, stamp))
        """
        if not ProcessCheck.is_process_running(config.TON_LISTTOOL_PROCESS):
            return "lost", "ToN ListTool が起動していません"

        path, load = config.HOST_STATE_PATH, MatchTNL.load_host_state
        name = os.path.basename(path)
        if not os.path.exists(path):
            return "lost", f"{name} がありません"
        try:
            stat = os.stat(path)
        except OSError as e:
            return "lost", f"{name} が読めません: {e}"

        # 自分のリストだけ更新されたときも読み直す。SQLite は本体を触らずに
        # -wal だけ伸びることがあるので、そちらも見る
        stamp = (stat.st_mtime, stat.st_size,   # 同じ秒内の書き換えを取りこぼさない
                 _file_stamp(config.USER_SAVE_PATH),
                 _file_stamp(path + "-wal"))
        if stamp == self._host_save_stamp and SharedState.get_list_source() == "host":
            return "same", None

        try:
            keep_on, meta, wishes = load(path, config.USER_SAVE_PATH)
        except Exception as e:
            DebugLog.exception("mainGUI._read_host_source")
            # 別プロセスが書いている最中を掴みうる。ここで tnl へ倒すと3秒ごとに
            # 往復しかねないので、前の値を保持して次のtickで再試行する
            return "error", f"[主催リスト] ⚠ 読み込み失敗（前のリストを使います）: {e}"

        if not meta["listed"]:
            # 参加者にも待機にも続行リストを持つ人がいない＝周回そのものが無い。
            # 「参加者0人」だけでは切り替えない——ToN ListTool は複窓だと全員を
            # 待機へ移すことがあり、それでも希望は待機に残っている
            return "lost", "続行リストを持つ人がいません（参加者・待機とも）"
        return "loaded", (keep_on, meta, wishes, stamp)

    def _apply_host_source(self, result: tuple):
        """_read_host_source() の結果を当てる（Tk のスレッドで）"""
        kind, value = result
        if kind == "lost":
            self._host_list_lost(value)
            return
        if kind == "same":
            self._host_loss_since = None        # 取れている
            return
        if kind == "error":
            self._warn_host_save_once(value)
            return
        keep_on, meta, wishes, stamp = value
        self._host_loss_since = None
        self._host_save_stamp = stamp
        self._host_save_warned = False
        switched = SharedState.get_list_source() != "host"
        SharedState.set_list_source("host")
        changed = keep_on != self.keepOn_set
        self._apply_keep_on(keep_on)
        self._apply_host_wishes(wishes, meta.get("participant_names", ()))
        self._apply_host_tabs(meta.get("tabs_data") or {})
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
            DebugLog.exception("mainGUI._load_tnl")
            if show_error:
                messagebox.showerror("TNL読み込みエラー", str(e))
            else:
                self._log(f"[TNL] 読み込みエラー: {e}")

    def _load_saved_settings(self):
        """起動時: 前回選んだtnlを復元して即読み込む"""
        data = load_settings()
        self.v_desktop_mode.set(bool(data.get("desktop_mode", config.LAUNCH_DESKTOP_MODE)))
        self.v_use_osc.set(bool(data.get("use_osc", config.OSC_ENABLED)))
        self.v_ton_entry.set(bool(data.get("ton_entry", config.TON_ENTRY_ENABLED)))
        self.v_join_world.set(bool(data.get("join_world", False)))
        self.v_ton_begin.set(bool(data.get("ton_begin", config.TON_ENTRY_BEGIN)))
        access = data.get("ton_instance_access")
        if access not in config.TON_INSTANCE_ACCESS_CHOICES:
            access = config.TON_INSTANCE_ACCESS_DEFAULT     # 無い・壊れた値は既定
        self.v_ton_access.set(access)
        self._load_launch_options_setting(data)
        self._saved_profiles = data.get("profiles", [])
        # 窓数は控えるだけ。反映するのは _auto_detect_windows() の「未検出」の
        # ときだけ（開いている窓があればその数を優先する）
        self._win_count_pref = _valid_win_count(data.get("win_count"))
        # ラウンド指定（skip_rounds / continue_rounds）は
        # 復元しない。持ち越した自爆設定は、別のインスタンスでは危ない
        # 手編集や別バージョンで壊れた値が入りうる。読むときも検証する——
        # 不正なキーのままだと緊急停止が黙って効かなくなる
        key = data.get("emergency_stop_key", config.EMERGENCY_STOP_KEY)
        if not HotKey.is_valid(key):
            key = config.EMERGENCY_STOP_KEY
        self.v_emergency_key.set(key)
        self._refresh_emergency_key_label()
        # マクロ開始のキーは、不正なら既定値へ倒さずに無効（未設定）にする。
        # 停止キーと同じキーも無効にする（どちらか一方しか働かないため）
        start_key = data.get("start_key", config.START_KEY)
        if not HotKey.is_valid(start_key) or start_key == key:
            start_key = ""
        self.v_start_key.set(start_key)
        self._refresh_start_key_label()
        # 自爆キャンセルのキー。不正・ほかのキーと同じなら既定（^）へ。既定も重なれば未設定
        cancel_key = data.get("suicide_cancel_key", config.SUICIDE_CANCEL_KEY)
        if not HotKey.is_valid(cancel_key) or self._suicide_cancel_key_conflict(cancel_key):
            cancel_key = config.SUICIDE_CANCEL_KEY
            if self._suicide_cancel_key_conflict(cancel_key):
                cancel_key = ""
        self.v_suicide_cancel_key.set(cancel_key)
        self._refresh_suicide_cancel_key_label()
        # 古い settings.json にはキーが無い。無くても落ちないこと
        for path in data.get("tool_launchers", []) or []:
            if isinstance(path, str) and path.strip():
                self._add_tool_row(path, save=False)
        # 旧形式は窓ごとの配列。全窓共通へ移したので畳んで読む
        self.v_freeze_8pages.set(_as_flag(data.get("freeze_8pages")))
        SharedState.set_item_fetch(data.get("item_fetch") is True)    # 無い・壊れた値は OFF（マウスを動かすので true だけ）
        SharedState.set_item_fetch_gain(data.get("item_fetch_gain"))   # 壊れた値は無し
        self._refresh_item_fetch_button()
        self.v_freeze_punish.set(_as_flag(data.get("freeze_punish")))
        for name, var in self.v_freeze_rounds.items():
            var.set(name in _as_round_names(data.get("freeze_rounds")))
        self._apply_freeze_settings()
        self.v_obs_enabled.set(_as_flag(data.get("obs_record")))
        self.v_obs_host.set(str(data.get("obs_host") or config.OBS_DEFAULT_HOST))
        self.v_obs_port.set(str(data.get("obs_port") or config.OBS_DEFAULT_PORT))
        password, state = load_obs_password(data)
        DebugLog.add_secret(password)        # debug.log に書かない
        self.v_obs_password.set(password)
        if state == "undecryptable":
            self._log("[OBS] 保存されたパスワードを復号できません（別のPCや別のユーザーの"
                      "設定など）→ OBS のパスワードを入れ直してください")
        elif state == "migrate":
            # 旧形式の平文をその場で暗号化して書き直す（タブの有無に関係なく）
            save_settings(with_obs_password(load_settings(), password))
        self._apply_obs_settings()
        self._load_window_volume_settings(data)    # 古い settings.json でも既定値
        self._load_fog_early_read_setting(data)
        # 続行ラウンドの後: 無い（古い settings.json）・壊れた値は ON（既定）
        SharedState.set_continue_drop_item(data.get("continue_drop_item") is not False)
        SharedState.set_continue_restore_window(data.get("continue_restore_window") is not False)
        self._refresh_continue_after_checks()
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

    def _apply_tab_settings_live(self, tab):
        """動作中なら、その窓の監視の設定（WindowConfig）を書き換える。次の判定から効き、
        決めたことはやり直さない。止まっているときは何もしない（次の開始で写る）。
        Tk のスレッドから1回の代入で差し替えるだけ（監視のスレッドは読むだけ）"""
        if not getattr(self, "_running", False):
            return
        settings = tab.live_settings()
        for monitor in getattr(self, "monitors", []):
            if monitor.window_idx == tab.idx + 1:
                for key, value in settings.items():
                    setattr(monitor.cfg, key, value)

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
                DebugLog.exception("mainGUI._release_suicide_keys")
                self._log(f"自爆キーを離せませんでした（{e}）")

    def _auto_detect_windows(self):
        """起動時: VRChatウィンドウ数を検出して窓数へ反映し、
        起動時刻を使ってHWNDとログを全窓ぶん自動割り当てする。"""
        windows = VRChatDiscovery.get_vrchat_windows_by_start_time(config.MAX_WINDOWS)
        if not windows:
            # 開いている窓が無い → 前回手で選んだ窓数 → 既定値、の順
            n = self._win_count_pref
            if n is None:
                self._log("[起動時検出] VRChatウィンドウ未検出"
                          "（窓数は手動で設定してください）")
                return
            self.v_win_count.set(n)
            if len(self.tabs) != n:
                self._rebuild_tabs(n)
            self._sync_launch_count()
            self._log(f"[起動時検出] VRChatウィンドウ未検出 → 前回の窓数{n}を使います")
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

    # 「候補から除外」のログで、理由ごとの言い方（理由ごとに1行にまとめる）
    DROPPED_LOG_LABELS = {
        "読めません": "読めないログ",
        "更新が止まっています": "更新が止まっているログ",
        "ToN を離れています": "ToN を離れているログ",
    }

    @classmethod
    def _dropped_log_lines(cls, dropped) -> list:
        """外したログを理由ごとに1行にする。理由の並びは初めて出てきた順（受け取った順
        ＝新しい順）。1件だけの理由はファイル名を添え、2件以上は件数だけ"""
        groups: dict = {}
        for path, why in dropped:
            groups.setdefault(why, []).append(Path(path).name)
        lines = []
        for why, names in groups.items():
            label = cls.DROPPED_LOG_LABELS.get(why)
            text = f"{label} {len(names)}件" if label else f"{why}: {len(names)}件"
            if len(names) == 1:
                text += f"（{names[0]}）"
            lines.append(f"[割り当て] 候補から除外: {text}")
        return lines

    def _live_candidates(self, candidates: list) -> list:
        """終わったログ・ToN を離れたログを候補から外す。

        全部外れたら絞り込む前の一覧を使う（割り当て不能にしない）。
        外した顔ぶれが変わったときだけログを出す（理由ごとに1行）
        """
        kept, dropped = VRChatDiscovery.live_ton_logs(candidates)
        reasons = tuple(sorted((Path(p).name, why) for p, why in dropped))
        if not kept and candidates:
            if self._dropped_logs != reasons:
                self._dropped_logs = reasons
                self._log("[割り当て] 生きているログがありません → 全部を候補にします")
            return candidates
        if reasons != self._dropped_logs:
            self._dropped_logs = reasons
            for line in self._dropped_log_lines(dropped):
                self._log(line)
        return kept

    def _resolve_windows(self, windows: list) -> list:
        """窓↔ログ↔OSCポートを確定する。netstatは1回だけ撃つ"""
        candidates = self._live_candidates(VRChatDiscovery.find_latest_logs(
            config.VRCHAT_LOG_DIR, config.LOG_MATCH_CANDIDATE_COUNT))
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
        # 前の回の窓が止めた後に張ったフリーズが残っていても、ここで全部解いて回を進める
        SharedState.begin_run()
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
            # 前面が VRChat の窓かを見るために覚えておく（Begin のカーソル判定）
            SharedState.register_window_hwnd(cfg.hwnd)
            mon = LogMonitor.LogMonitor(
                cfg, None, self._log,
                window_idx=tab.idx + 1,
                lists=self.lists,
                on_round_settings_cleared=self._clear_tab_round_settings)
            self.monitors.append(mon)
            mon.start()

        if not self.monitors:
            messagebox.showerror("エラー", "有効な窓/ログが見つかりません")
            return

        self._start_window_volume()
        self._running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_win_warn.config(
            text="⚠ 動作中です。窓数はマクロ停止後に変更できます", foreground=config.GUI_RED)
        self._log(f"[起動] {len(self.monitors)}窓の監視を開始")
        self._debug_start()

    # ── 不具合の報告 ────────────────────────
    def _open_report(self):
        dialog = getattr(self, "_report_dialog", None)
        try:
            if dialog is not None and dialog.winfo_exists():
                dialog.lift()
                return
        except tk.TclError:
            pass
        self._report_dialog = ReportDialog(self)

    def _current_tab_label(self) -> str:
        """メイン画面でいま選ばれている窓のタブ（「窓N」）。無ければ「なし」"""
        try:
            return f"窓{self.nb.index(self.nb.select()) + 1}" if self.tabs else "なし"
        except (tk.TclError, AttributeError):
            return "なし"

    def _tab_log_path(self, window: int) -> str:
        """その窓に割り当てたログのパス（無ければ空）"""
        if not window or window > len(self.tabs):
            return ""
        return self.tabs[window - 1].v_log.get().strip()

    def _report_cooldown_remaining(self) -> float:
        sent = getattr(self, "_report_sent_at", None)
        if sent is None:
            return 0.0
        return max(0.0, config.REPORT_COOLDOWN_SEC - (time.monotonic() - sent))

    def _debug_environment(self):
        """debug.log へ: 起動時の環境（版・exe か python か・Windows・画面・設定）"""
        try:
            data = {k: v for k, v in load_settings().items()
                    if k not in config.REPORT_SETTINGS_EXCLUDE_KEYS}
            DebugLog.write(
                f"[環境] 起動 版={config.APP_VERSION} 実行={'exe' if '__compiled__' in globals() else 'python'}"
                f" Windows={platform.platform()} Python={sys.version.split()[0]}"
                f" 画面={self.winfo_screenwidth()}x{self.winfo_screenheight()}")
            DebugLog.write("[環境] 設定 " + json.dumps(data, ensure_ascii=False, sort_keys=True))
        except Exception:
            DebugLog.exception("mainGUI._debug_environment")

    def _debug_start(self):
        """debug.log へ: マクロ開始のときの窓ごとの様子と全窓共通の設定"""
        try:
            for mon in self.monitors:
                cfg = mon.cfg
                DebugLog.write(
                    f"[環境] 開始 窓{mon.window_idx} hwnd={int(cfg.hwnd):#x}"
                    f" 位置={WindowOperator.window_rect(cfg.hwnd)} ログ={cfg.log_path}"
                    f" OSC={cfg.osc_port}/{cfg.osc_out_port} 自動Begin={cfg.auto_begin}"
                    f" 自爆={cfg.do_skip} DTM/Waldo続行={cfg.cancel_afk}"
                    f"（3クラ後も={cfg.cancel_afk_after_unlock}）"
                    f" Intermission={cfg.announce_intermission}"
                    f" 自爆ラウンド={sorted(cfg.skip_rounds)} 全続行={sorted(cfg.continue_rounds)}")
            DebugLog.write(
                f"[環境] 開始 共通 放置={SharedState.get_hands_free()}"
                f" 速度検知={SharedState.get_speed_detect()}"
                f" アイテム取得→Begin={SharedState.get_item_begin_mode()}"
                f" 8Pagesフリーズ={SharedState.get_freeze_on_8pages()}"
                f" Punishedフリーズ={SharedState.get_freeze_on_punish()}"
                f" 突入フリーズ={sorted(SharedState.get_freeze_rounds())}"
                f" 霧の即時判定={FogEarlyRead.early_read_enabled()}"
                f" 音量={self._window_volume_settings()}")
        except Exception:
            DebugLog.exception("mainGUI._debug_start")

    def _stop(self):
        reason, self._stop_reason = (getattr(self, "_stop_reason", None) or "ボタン"), None
        DebugLog.write(f"[環境] 停止（理由: {reason}）")
        self._stop_window_volume()               # 元の音量に戻す（監視を止める前に）
        self._entry_stop.set()                   # 入室時自動操作も中断する
        SharedState.clear_window_hwnds()         # 掴んでいる窓の記録も消す
        # 先に監視を止めてからフリーズを解く。逆だと、ほかの窓のフリーズが解けるのを待っていた
        # 窓（アイテムロストの装備待ちなど）が、止まる前に起きて自分のフリーズを張り、
        # それが次の開始まで残って全窓が止まったままになっていた
        for m in self.monitors:
            m.stop()
        SharedState.begin_run()                  # フリーズを全部解く（前の回の窓はもう張れない）
        # 自爆の長押し中に止めると、daemon の自爆スレッドが KEYUP を送る前に
        # 終わりうる（終了時はそのままプロセスが消える）。先に離しておく
        self._release_suicide_keys(m.cfg.hwnd for m in self.monitors)
        # このツールが始めた録画だけ止める（手動の録画には触らない）。
        # 終了時はワーカーごと消えるので、送り終えるまで少しだけ待つ
        Recorder.stop_all(wait_sec=config.OBS_TIMEOUT_SEC * 2)
        self.monitors.clear()
        self._show_own_windows_again()
        self._running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_win_warn.config(
            text="※ 窓数はマクロ起動前に設定してください", foreground=config.GUI_YLW)
        self._log("[停止] マクロを停止しました")

    def _log(self, msg: str):
        # 画面のログは全部 debug.log にも。もう [画面] で始まる行には二重に付けない
        DebugLog.write(msg if msg.startswith("[画面]") else f"[画面] {msg}")
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

    # ── インストーラー版への移行（Migration）───────────────
    MIGRATION_RETRY_MS = 30_000      # マクロ動作中は止まるまでこの間隔で待つ

    def _start_migration(self) -> bool:
        """exe 単体で動いていたら、インストーラー版へ移す準備を裏で始める。始めたら True"""
        exe = AutoUpdate.current_exe_path()
        if not Migration.needs_migration(exe, Migration.installed_dir()):
            return False
        self._log("[移行] インストーラー版へ移行します（設定・統計はそのまま引き継ぎます）。"
                  "インストーラーをダウンロード中…")

        def worker():
            release = AutoUpdate.fetch_latest_release()
            asset = AutoUpdate.find_exe_asset(release, config.SETUP_ASSET_NAME) if release else None
            tmp = AutoUpdate.download_to_temp(*asset) if asset else None
            self._after_from_worker(self._finish_migration, exe, tmp, asset is not None)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _finish_migration(self, exe, tmp, found: bool = True):
        """Setup を落とせたら、古い exe の場所を書いてから Setup を裏で動かし、ツールを終える"""
        if tmp is None:
            self._log("[移行] " + ("インストーラーのダウンロードに失敗しました" if found
                                  else f"リリースに {config.SETUP_ASSET_NAME} がありません")
                      + "。今回はこのまま使えます（次の起動でやり直します）")
            return
        if self._running:
            self._log("[移行] マクロ動作中のため、止まってから移行します")
            self.after(self.MIGRATION_RETRY_MS, lambda: self._finish_migration(exe, tmp, found))
            return
        setup = Path(tmp).with_name(config.SETUP_ASSET_NAME)
        try:
            setup.unlink(missing_ok=True)
            Path(tmp).rename(setup)
        except OSError:
            setup = Path(tmp)
        save_settings({**load_settings(), Migration.SETTINGS_KEY: str(exe)})
        # 入れる場所は古い exe のフォルダ（入れられない場所なら Setup の既定）
        install_dir = Migration.target_dir(Path(exe), Migration.installed_dir())
        messagebox.showinfo(
            "インストーラー版へ移行",
            "インストーラー版へ移行するため、いったん終了します。\n"
            "設定と統計はそのまま引き継がれます。\n"
            "1分ほどで自動で起動します（スタートメニューからも起動できます）。")
        if not Migration.launch_setup(setup, Path(exe), install_dir):
            self._log("[移行] インストーラーを起動できませんでした。今回はこのまま使えます")
            messagebox.showwarning("インストーラー版へ移行",
                                   "インストーラーを起動できませんでした。今回はこのまま使えます"
                                   "（次の起動でやり直します）。")
            return
        DebugLog.write(f"[環境] インストーラー版へ移行（元の exe: {exe}、入れる場所: "
                       f"{install_dir or 'Setup の既定'}）")
        self._on_close()

    def _cleanup_migrated_exe(self):
        """インストーラー版の最初の起動で、移す前の exe を消す（settings.json の印を見る）"""
        data = load_settings()
        old = data.get(Migration.SETTINGS_KEY)
        if not old:
            return
        exe = AutoUpdate.current_exe_path()
        if exe is None or Migration.needs_migration(exe, Migration.installed_dir()):
            return              # まだ移っていない（Setup が途中で止まった）。次の起動で
        if Migration.remove_old_exe(Path(old), exe):
            data.pop(Migration.SETTINGS_KEY, None)
            save_settings(data)
            self._log("[移行] インストーラー版へ移行しました。前の exe を消しました"
                      "（設定・統計はそのまま）")

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

    def _refresh_tool_row(self, row, running: bool | None = None):
        """ボタンの名前と「起動中」表示を合わせる。見た目だけ。
        running を渡さなければ、ここで見る（プロセスの一覧を取るので Tk のスレッドでは重い）"""
        exe = row.v_path.get().strip()
        label = ToolLauncher.button_label(exe) or "起動"
        if running is None:
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
            DebugLog.exception("mainGUI._launch_tool")
            self._log(f"[外部ツール] 起動に失敗: {e}")
        self._refresh_tool_row(row)

    def _start_tool_poll(self):
        self.after(int(config.TOOL_LAUNCH_POLL_SEC * 1000), self._poll_tool_buttons)

    def _poll_tool_buttons(self):
        """TOOL_LAUNCH_POLL_SEC ごと。プロセスの一覧は裏のスレッドで1回だけ取り
        （ツールの数だけ取らない）、ボタンは Tk のスレッドで合わせる"""
        rows = [(row, row.v_path.get().strip()) for row in list(self.tool_rows)]

        def work():
            states = {}
            try:
                names = ProcessCheck.running_names()
                states = {row: bool(exe) and ToolLauncher.is_running(exe, names)
                          for row, exe in rows}
            except Exception:
                DebugLog.exception("mainGUI._poll_tool_buttons")
            self._after_from_worker(self._finish_tool_poll, states)
        self._off_gui(work)

    def _finish_tool_poll(self, states: dict):
        try:
            for row, running in states.items():
                if row in self.tool_rows:       # 待っている間に消された行は触らない
                    self._refresh_tool_row(row, running)
        except Exception:
            DebugLog.exception("mainGUI._finish_tool_poll")
        try:
            self.after(int(config.TOOL_LAUNCH_POLL_SEC * 1000),
                       self._poll_tool_buttons)
        except tk.TclError:
            pass

    def _launch_vrchat(self):
        if self._running:
            messagebox.showwarning("警告", "マクロ動作中は起動できません。先に停止してください")
            return
        exe = VRChatLauncher.find_vrchat_exe()
        if exe is None:
            messagebox.showerror(
                "エラー",
                "Steamのライブラリから VRChat の launch.exe を見つけられませんでした")
            return

        join_ton = self.v_join_world.get()
        user_id = None
        ton_access = self.v_ton_access.get()
        extra_options = self.v_launch_options.get()     # その時点の欄の値
        if join_ton:
            user_id = VRChatLauncher.latest_user_id(config.VRCHAT_LOG_DIR)
            if not user_id:
                messagebox.showerror(
                    "エラー",
                    "自分のユーザーIDを検出できませんでした。\n"
                    "一度VRChatにログインしてください")
                return
            self._log("[起動] ToNの新規インスタンスを生成します（%s）" % user_id)
        else:
            self._log("[起動] ホームで起動します（ToNへは入りません）")

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
                    link = (VRChatLauncher.build_ton_link(
                        user_id, index=osc_index, access=ton_access)
                        if join_ton else None)
                    args = VRChatLauncher.launch_one(
                        exe, profile_id, desktop, link,
                        osc_index=osc_index if use_osc else None,
                        extra_options=extra_options)
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
                DebugLog.exception("mainGUI.worker")
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
        candidates = self._live_candidates(VRChatDiscovery.find_latest_logs(
            config.VRCHAT_LOG_DIR, config.LOG_MATCH_CANDIDATE_COUNT))
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
        targets = [(tab.idx + 1, tab._get_selected_hwnd(), tab.v_log.get().strip())
                   for tab in self.tabs
                   if launched is None or tab.idx in launched]
        targets = [(no, h, log) for no, h, log in targets if h]
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
                for window_no, hwnd, log_path in targets:
                    if self._entry_stop.is_set():
                        self._log("[入室操作] 中止しました")
                        return
                    entry = ToNEntry.ToNEntry(
                        hwnd,
                        window_index=window_no - 1,   # OSCポートの割り当てに使う
                        log=lambda m, n=window_no: self._log("[窓%d] %s" % (n, m)),
                        is_running=lambda: not self._entry_stop.is_set(),
                        # 移動・クリックは private だけ（ログの最後の入室で見る。分からなければしない）
                        can_operate=lambda p=log_path: bool(p) and ActionExecutor.can_operate_in(
                            LogMonitor.LogMonitor.detect_instance_type_from_log(p)),
                    )
                    try:
                        if entry.run() and press_begin and not self._entry_stop.is_set():
                            entry.press_begin()
                    except Exception as e:
                        DebugLog.exception("mainGUI.worker")
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
            DebugLog.exception("mainGUI._save_settings_now")
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
            "desktop_mode":  self.v_desktop_mode.get(),
            "use_osc":       self.v_use_osc.get(),
            "ton_entry":     self.v_ton_entry.get(),
            "ton_begin":     self.v_ton_begin.get(),
            "join_world":    self.v_join_world.get(),
            "ton_instance_access": self.v_ton_access.get(),
            "profiles":      [tab.v_profile.get() for tab in self.tabs],
            "win_count":     self._win_count_pref,
            "tool_launchers": [p for p in (row.v_path.get().strip()
                                           for row in self.tool_rows) if p],
            "emergency_stop_key": self.v_emergency_key.get(),
            "start_key":     self.v_start_key.get(),
            "suicide_cancel_key": self.v_suicide_cancel_key.get(),
            "freeze_8pages": self.v_freeze_8pages.get(),
            "item_fetch":    SharedState.get_item_fetch(),
            "item_fetch_gain": (list(SharedState.get_item_fetch_gain())
                                if SharedState.get_item_fetch_gain() else None),
            "freeze_punish": self.v_freeze_punish.get(),
            "freeze_rounds": sorted(name for name, var in self.v_freeze_rounds.items()
                                    if var.get()),
            "obs_record":    self.v_obs_enabled.get(),
            "obs_host":      self.v_obs_host.get().strip(),
            "obs_port":      self.v_obs_port.get().strip(),
            **self._window_volume_settings(),
            **self._fog_early_read_setting(),
            "continue_drop_item":      SharedState.get_continue_drop_item(),
            "continue_restore_window": SharedState.get_continue_restore_window(),
            **self._launch_options_setting(),
        }
        # load_settings() をマージしているので、書かないだけでは前回の値が
        # ファイルに残り続ける。危ない設定は明示的に消す
        # skip_variant_exempt はチェックボックスごと廃止した。古いファイルに
        # 残っていても読まないが、ついでに消しておく。big_window_key / enlarge_window_key は
        # 窓を大きくするキーの名残（機能ごと消した）
        for key in ("skip_rounds", "skip_variant_exempt", "continue_rounds", "big_window_key",
                    "enlarge_window_key"):
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

    def _set_own_windows_hidden(self, hidden: bool):
        """当ツールの窓を画面キャプチャから外す／戻す。

        Recorder のワーカースレッドから呼ばれるので Tk には触らない
        （hwnd は窓を作ったときに控えた値）。取れない環境では1度だけ知らせて、
        録画はそのまま続ける
        """
        SharedState.set_own_windows_hidden(hidden)
        for hwnd in SharedState.own_windows():
            ok = WindowOperator.set_capture_excluded(hwnd, hidden)
            DebugLog.write(f"[画面] 録画{'から外す' if hidden else 'に戻す'} hwnd={int(hwnd):#x}"
                           f" → {'OK' if ok else '失敗'}")
            if ok or not hidden or self._capture_warned:
                continue            # 1つ失敗しても、残りの窓は外しに行く
            self._capture_warned = True
            self._log("⚠ 録画からツールの窓を隠せませんでした"
                      "（Windows 10 2004 以降が必要です）。録画は続けます")

    def _show_own_windows_again(self):
        """録画中に隠したままにしない。止めたら必ず戻す"""
        SharedState.set_own_windows_hidden(False)
        for hwnd in SharedState.own_windows():
            WindowOperator.set_capture_excluded(hwnd, False)

    def _on_close(self):
        # VRChat を起動しないまま閉じても設定が残るように。destroy() の後は
        # Tk 変数を読めないので必ず先に、停止が長引いても保存は済ませたいので
        # _stop() より前に書く
        self._save_settings_now()
        self._stop_reason = "ウィンドウを閉じた"      # debug.log の停止の理由
        self._stop()
        self._unhook_chase_keys()
        self._unhook_suicide_cancel_key()
        self._unhook_stop_start_keys()
        self._show_own_windows_again()
        self.destroy()
