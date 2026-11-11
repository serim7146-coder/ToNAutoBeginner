"""各 VRChat 窓の上に重ねる、手に持ったアイテムの表示（効果・CT の残り）。

- 窓ごとに小さな窓（WindowItemOverlay）を 1 つ。VRChat の窓のクライアント領域の隅に、全窓共通の
  「隅＋ずらし（px）」で置く。クリックを通し（WS_EX_TRANSPARENT）、前面・フォーカスを取らない（WS_EX_NOACTIVATE）
- 録画（OBS）からは当ツールのほかの窓と同じに外す（remember_own_window を渡してもらう）
- 画面のスレッド（Tk のメインスレッド）だけで動く。mainGUI が 0.1 秒ごとに refresh() を呼ぶ（引く形。ほかのスレッドは触らない）
- 表示する文字は ItemUse.status_line（窓の番号なし）。持っていない窓・窓が見えない（最小化など）ときは隠す
"""
import tkinter as tk

import config
import DebugLog
import UIFont
import WindowOperator


def place(client, size, corner, dx, dy, screen):
    """表示の左上の位置 (x, y)。client はクライアント領域 (左, 上, 右, 下)・size は表示の (幅, 高さ)・
    screen は全モニタの (左, 上, 右, 下)。dx・dy は隅から内側へ向けた px（負は外側）。
    窓が画面にまったくかかっていなければ None。かかっていれば、位置は画面の中へ収める"""
    left, top, right, bottom = client
    width, height = size
    s_left, s_top, s_right, s_bottom = screen
    if right <= s_left or left >= s_right or bottom <= s_top or top >= s_bottom:
        return None
    x = left + dx if corner.endswith("left") else right - width - dx
    y = top + dy if corner.startswith("top") else bottom - height - dy
    x = max(s_left, min(x, s_right - width))
    y = max(s_top, min(y, s_bottom - height))
    return x, y


class WindowItemOverlay(tk.Toplevel):
    """1 つの窓の上に出す小さな窓。最前面・半透明・枠なし・クリックを通す"""

    def __init__(self, parent, remember_own_window):
        super().__init__(parent)
        self.withdraw()                     # 位置が決まるまで出さない
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", config.ITEM_OVERLAY_ALPHA)
        self.configure(bg="#000000")
        self._label = tk.Label(self, text="", bg="#000000", fg="#f9e2af", justify="left", anchor="w",
                               font=(UIFont.UI, 9, "bold"), padx=6, pady=2)
        self._label.pack()
        self._remember_own_window = remember_own_window
        self._text = ""
        self._size = (1, 1)
        self._geometry = ""                 # 最後に設定した geometry（同じなら設定し直さない）
        self._visible = False
        self._registered = False            # 当ツールの窓として覚えたか（録画から外すため）
        self._styled_hwnd = 0               # クリックを通す・前面を取らない指定をした hwnd

    def measure(self, text: str) -> tuple:
        """text を出したときの大きさ (幅, 高さ)。文字が変わったときだけ設定し直す"""
        if text != self._text:
            self._label.config(text=text)
            self._text = text
            self.update_idletasks()
            self._size = (self._label.winfo_reqwidth(), self._label.winfo_reqheight())
        return self._size

    def show(self, text: str, x: int, y: int):
        """text を (x, y) に出す。出ていなければ出す。変わったところだけ設定する"""
        width, height = self.measure(text)
        # 座標の前の + は必ず付ける（x が負のときは "+-5"。"-5" だと右端からの距離になる）
        geometry = f"{width}x{height}+{x}+{y}"
        if geometry != self._geometry:
            self.geometry(geometry)
            self._geometry = geometry
        if not self._visible:
            self._prepare()
            self.deiconify()
            self._visible = True

    def _prepare(self):
        """出す前に、当ツールの窓として覚え（録画から外す）、クリックを通す・前面を取らない指定をする。
        最初の 1 回（hwnd が変わったときはもう一度）"""
        self.update_idletasks()
        if not self._registered:
            self._registered = True
            self._remember_own_window(self)
        hwnd = WindowOperator.own_window_hwnd(self)
        if hwnd and hwnd != self._styled_hwnd:
            if WindowOperator.set_overlay_style(hwnd):
                self._styled_hwnd = hwnd

    def hide(self):
        if self._visible:
            self._visible = False
            self.withdraw()

    def close(self):
        self._visible = False
        try:
            self.destroy()
        except tk.TclError:
            pass


class ItemOverlays:
    """窓ごとの WindowItemOverlay をまとめる。必要になった窓の分だけ作り、要らなくなったら壊す"""

    def __init__(self, parent, remember_own_window, factory=WindowItemOverlay):
        self._parent = parent
        self._remember_own_window = remember_own_window
        self._factory = factory
        self._overlays: dict = {}           # 窓の番号 → 表示の窓

    def refresh(self, entries, placement):
        """entries は [(窓の番号, hwnd, 文字)]（文字が空 = その窓は持っていない）。placement は (隅, 横, 縦)。
        entries に無い窓の表示は壊す。持っていない窓・窓が見えない（無い・最小化・大きさ 0）ときは隠す"""
        corner, dx, dy = placement
        wanted = {idx for idx, _hwnd, _text in entries}
        for idx in [idx for idx in self._overlays if idx not in wanted]:
            self._close_one(idx)
        screen = None
        for idx, hwnd, text in entries:
            overlay = self._overlays.get(idx)
            try:
                rect = WindowOperator.client_rect_on_screen(hwnd) if text else None
                if rect is None:
                    if overlay is not None:
                        overlay.hide()
                    continue
                if overlay is None:
                    overlay = self._overlays[idx] = self._factory(self._parent, self._remember_own_window)
                size = overlay.measure(text)
                if screen is None:
                    screen = WindowOperator.virtual_screen_rect()
                pos = place(rect, size, corner, dx, dy, screen)
                if pos is None:
                    overlay.hide()
                else:
                    overlay.show(text, pos[0], pos[1])
            except Exception:
                DebugLog.exception("ItemOverlay.ItemOverlays.refresh")

    def _close_one(self, idx):
        overlay = self._overlays.pop(idx, None)
        if overlay is not None:
            try:
                overlay.close()
            except Exception:
                DebugLog.exception("ItemOverlay.ItemOverlays._close_one")

    def close_all(self):
        for idx in list(self._overlays):
            self._close_one(idx)
