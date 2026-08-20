"""
ToN入室直後の選択画面を自動で突破する

移動はOSC、クリックはマウスで行う。
  - OSCはフォーカスを奪わないので、移動中は他の作業を妨げない
  - VRChatにはデスクトップ用のインタラクト入力が無いため、クリックだけは
    フォーカスを取って実マウスを送る必要がある（OSCでは代替できない）

アバターを横移動させるとボタンが画面中央（クロスヘア）に来るので、
移動量さえ決まっていれば画像認識は不要。パネルの見かけの大きさは
プレイヤーとの距離で変わるため、画面座標を当てにする方式は使わない。

パネルの出現待ちだけは画面から判定する（ロード中に操作しないため）。
画面取得はEasy Anti-Cheat下でも PrintWindow で可能。
"""
import time

import win32gui
import win32ui
from ctypes import windll

import config
import OSCClient
import SharedState
import WindowOperator


class ToNEntry:
    def __init__(self, hwnd: int, osc_port: int = None, window_index: int = 0,
                 log=None, is_running=None):
        self._hwnd = hwnd
        self._log = log or (lambda _m: None)
        self._is_running = is_running or (lambda: True)
        if osc_port is None:
            osc_port, _out = OSCClient.ports_for_window(window_index)
        self._osc = OSCClient.OSCClient(osc_port)
        self._osc_port = osc_port

    # ── 画面取得 ──────────────────────────────

    def capture(self):
        """PrintWindowでウィンドウの画素を取得する。(bits, 幅, 高さ)"""
        left, top, right, bottom = win32gui.GetWindowRect(self._hwnd)
        w, h = right - left, bottom - top
        dc = win32gui.GetWindowDC(self._hwnd)
        mfc = win32ui.CreateDCFromHandle(dc)
        save = mfc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc, w, h)
        save.SelectObject(bmp)
        windll.user32.PrintWindow(self._hwnd, save.GetSafeHdc(), 2)
        bits = bmp.GetBitmapBits(True)
        win32gui.DeleteObject(bmp.GetHandle())
        save.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(self._hwnd, dc)
        return bits, w, h

    @staticmethod
    def _is_neon_red(b, g, r) -> bool:
        return r > 90 and g < r * 0.65 and b < r * 0.65

    def _is_loading_screen(self, bits, w, h) -> bool:
        """ロード画面か。ロード中は背景が青緑、ロビーはほぼ黒。

        ロード画面にもToNの赤いロゴが出るため、赤の量だけでは区別できない。
        """
        blue_corners = 0
        for x, y in ((60, 60), (w - 60, 60), (60, h - 60), (w - 60, h - 60)):
            i = (y * w + x) * 4
            b, g, r = bits[i], bits[i + 1], bits[i + 2]
            if b > r + 15 and b > 60:
                blue_corners += 1
        return blue_corners >= 3

    def _center_red_count(self, bits, w, h) -> int:
        """画面中央付近の赤画素数。パネルが出ているかの目安。"""
        n = 0
        for y in range(int(h * 0.30), int(h * 0.60), 3):
            base = y * w * 4
            for x in range(int(w * 0.30), int(w * 0.72), 3):
                i = base + x * 4
                if self._is_neon_red(bits[i], bits[i + 1], bits[i + 2]):
                    n += 1
        return n

    def wait_for_panel(self, timeout: float = None) -> bool:
        """選択パネルが出るまで待つ。

        位置や大きさは見ない（距離で変わるため）。ロード中でないことと、
        中央に十分な赤があり安定していることだけを確認する。
        """
        timeout = timeout or config.TON_ENTRY_PANEL_TIMEOUT
        deadline = time.time() + timeout
        stable = 0
        while time.time() < deadline and self._is_running():
            try:
                bits, w, h = self.capture()
                if self._is_loading_screen(bits, w, h):
                    stable = 0
                else:
                    count = self._center_red_count(bits, w, h)
                    if count >= config.TON_ENTRY_MIN_RED_PIXELS:
                        stable += 1
                        if stable >= config.TON_PANEL_STABLE_COUNT:
                            self._log(f"✅ 選択パネルを確認（中央の赤画素 {count}）")
                            return True
                    else:
                        stable = 0
            except Exception:
                stable = 0
            time.sleep(config.TON_ENTRY_POLL_SEC)
        self._log("⚠ 選択パネルが現れませんでした")
        return False

    # ── 操作 ──────────────────────────────────

    def move(self, direction: str, seconds: float) -> bool:
        """OSCでアバターを横移動させる（フォーカスは奪わない）"""
        address = {"right": "/input/MoveRight",
                   "left": "/input/MoveLeft",
                   "forward": "/input/MoveForward",
                   "back": "/input/MoveBackward"}.get(direction)
        if not address:
            return False
        self._log(f"OSC移動 {direction} {seconds}秒")
        ok = self._osc.press(address, seconds)
        self._osc.stop_all(repeat=1)     # 入力が残らないよう必ず解除
        return ok

    def click(self, label: str) -> bool:
        """クロスヘア位置をクリックする。

        フォーカスを奪うので他窓の操作と衝突する。ここだけ排他にする
        （移動はOSCでフォーカス不要なのでロックしない）。
        """
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running():
                return False
            if not WindowOperator.focus_window(self._hwnd):
                self._log(f"⚠ {label}: フォーカス取得失敗 → 中止")
                return False
            WindowOperator.click()
            self._log(f"クリック（{label}）")
            return True

    # ── 一連の流れ ────────────────────────────

    def run(self) -> bool:
        """入室時の選択画面を順に突破する。

        手順（実測で決めた移動量）:
            右0.4秒 → クリック（警告同意）
            左0.65秒 → クリック（難易度 Casual）
            右0.6秒 → クリック（BGM）→ クリック（LET ME PLAY）
        最後の2回は的がほぼ同じ位置にあるため移動せず続けて押す。
        """
        if not self.wait_for_panel():
            return False

        # パネルを検出しても描画や入力受付が整うまで間があるため、少し待つ
        self._log(f"操作開始まで{config.TON_ENTRY_START_DELAY_SEC}秒待ちます")
        time.sleep(config.TON_ENTRY_START_DELAY_SEC)

        for step in config.TON_ENTRY_STEPS:
            if not self._is_running():
                self._log("入室時操作を中止しました")
                self._osc.stop_all()
                return False
            direction, seconds, label = step["move"], step["sec"], step["label"]
            if direction and seconds:
                if not self.move(direction, seconds):
                    self._log(f"⚠ {label}: 移動に失敗 → 中止")
                    self._osc.stop_all()
                    return False
                time.sleep(config.TON_ENTRY_SETTLE_SEC)
            if not self.click(label):
                self._osc.stop_all()
                return False
            time.sleep(config.TON_ENTRY_STEP_WAIT_SEC)

        self._osc.stop_all()
        self._log("✅ 入室時の選択画面を突破しました")
        return True

    def press_begin(self) -> bool:
        """ロビーのBeginへ移動してクリックする。

        移動量は入室直後の位置を前提とした実測値。ラウンド終了後の
        BEGIN_FORWARD_SEC とは位置が違うので別の設定を使う。
        """
        if not self._is_running():
            return False
        self._log("Beginへ移動します")
        if not self.move("forward", config.TON_ENTRY_BEGIN_FORWARD_SEC):
            self._osc.stop_all()
            return False
        if not self.move("left", config.TON_ENTRY_BEGIN_LEFT_SEC):
            self._osc.stop_all()
            return False
        time.sleep(config.TON_ENTRY_SETTLE_SEC)
        ok = self.click("Begin")
        self._osc.stop_all()
        return ok

    def close(self):
        try:
            self._osc.stop_all()
            self._osc.close()
        except Exception:
            pass
