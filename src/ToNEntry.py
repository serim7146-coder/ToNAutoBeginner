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

import config
import DebugLog
import LogParser
import OSCClient
import SaveLoad
import ScreenCapture
import SharedState
import WindowOperator


def wait_verified(path: str, offset: int, timeout: float, is_running=None,
                  sleep=time.sleep, clock=time.time) -> str:
    """VRChat のログの offset バイト目から後を見て、Begin を押した後の最初の Verified を待つ（DZ）。
    "verified"・"round"（ラウンドが始まった）・"timeout"・"stopped"。
    入室直後は定期の Verified と見分けにくい（依頼者も承知）。今は「押した後の最初の Verified」"""
    is_running = is_running or (lambda: True)
    deadline = clock() + timeout
    rest = b""
    while is_running():
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            data = b""
        offset += len(data)
        lines = (rest + data).split(b"\n")
        rest = lines.pop()              # 書きかけの行は次に
        for raw in lines:
            event = LogParser.parse(LogParser.strip_prefix(raw.decode("utf-8", "replace").strip()))
            if event is None:
                continue
            if event.kind == LogParser.EVENT_VERIFIED:
                return "verified"
            if event.kind == LogParser.EVENT_ROUND_START:
                return "round"
        if clock() >= deadline:
            return "timeout"
        sleep(config.PLUSH_VERIFIED_POLL_SEC)
    return "stopped"


class ToNEntry:
    def __init__(self, hwnd: int, osc_port: int = None, window_index: int = 0,
                 log=None, is_running=None, can_operate=None):
        self._hwnd = hwnd
        self._log = log or (lambda _m: None)
        self._is_running = is_running or (lambda: True)
        # この窓で移動・クリックしてよいか（private だけ。ActionExecutor.can_operate_in）。
        # 渡されなければ分からない → しない
        self._can_operate = can_operate or (lambda: False)
        if osc_port is None:
            osc_port, _out = OSCClient.ports_for_window(window_index)
        self._osc = OSCClient.OSCClient(osc_port)
        self._osc_port = osc_port

    # ── 画面取得 ──────────────────────────────

    def capture(self):
        """PrintWindowでウィンドウの画素を取得する。(bits, 幅, 高さ)"""
        return ScreenCapture.capture_window(self._hwnd)

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
                DebugLog.exception("ToNEntry.wait_for_panel")
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
        if not self._can_operate():
            return False
        self._log(f"OSC移動 {direction} {seconds}秒")
        ok = self._osc.press(address, seconds, stop=lambda: not self._is_running())
        self._osc.stop_all(repeat=1)     # 入力が残らないよう必ず解除
        return ok

    def click(self, label: str) -> bool:
        """クロスヘア位置をクリックする。

        フォーカスを奪うので他窓の操作と衝突する。ここだけ排他にする
        （移動はOSCでフォーカス不要なのでロックしない）。
        """
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or not self._can_operate():
                return False
            ok, loan = WindowOperator.borrow_front(self._hwnd)
            if not ok:
                self._log(f"⚠ {label}: フォーカス取得失敗 → 中止")
                return False
            WindowOperator.click()
            self._log(f"クリック（{label}）")
            if loan is not None:
                # 入室パネルは離した瞬間に判定されるので、離した後に待ってから返す
                time.sleep(config.FOCUS_RETURN_AFTER_RELEASE_SEC)
                WindowOperator.return_front(loan)
            return True

    # ── 一連の流れ ────────────────────────────

    def run(self) -> bool:
        """入室時の選択画面を順に突破する。

        手順は config.TON_ENTRY_STEPS（実測で決めた移動量）:
            右 → クリック（警告同意）
            左 → クリック（難易度 Casual）
            右 → クリック（BGM）→ クリック（LET ME PLAY）
        最後の2回は的がほぼ同じ位置にあるため移動せず続けて押す。
        """
        if not self._can_operate():
            self._log("入室時の自動操作はプライベートのインスタンスだけで行います → しません")
            return False
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

    # ── セーブコードの読み込み（DY）────────────────
    # セーブコードは、画面のログにも debug.log にも出さない（長さも）。Ctrl+V の後は撮らない

    def load_code(self, code: str, log_path: str) -> str:
        """入室の選択画面の後・Begin の前に、Save/Load のコード欄へ code を貼って Enter。
        "loaded"（ログで確かめた）・"unconfirmed"（貼ったがログに出ない）・"skipped"（貼っていない）。
        どれでも呼ぶ側は Begin を押す（依頼者）"""
        if not self._can_operate():
            return "skipped"
        if not code:
            self._log("⚠ クリップボードが空のためコードを読み込みません")
            return "skipped"
        bgr = self._capture_bgr()
        height = WindowOperator.client_height(self._hwnd) or 0
        found = SaveLoad.find_codebox(bgr, height) if bgr is not None else None
        if found is None:
            self._log("⚠ コード欄が見つかりません → コードを読み込みません")
            return "skipped"
        DebugLog.write(f"[コードの読み込み] コード欄 ({found[0]:.1f}, {found[1]:.1f})・一致 {found[2]:.3f}・"
                       f"倍率 {found[3]:.2f}")
        offset = SaveLoad.log_size(log_path) if log_path else 0
        result = self._paste_code(code, found)
        if result != "pasted":
            self._log(f"⚠ コードを読み込めませんでした（{result}）")
            return "skipped"
        # Enter が効かなかった窓がある（EC）。3 秒で出なければ、入力欄が開いたままか見て押し直す（最大 2 回）
        deadline = time.time() + config.SAVELOAD_LOG_WAIT_SEC
        retries = 0
        left_open = False
        while True:
            if self._wait_loaded(log_path, offset, min(deadline, time.time() + config.SAVELOAD_RETRY_WAIT_SEC)):
                self._log("✅ コードを読み込みました")
                return "loaded"
            if not self._is_running():
                break
            if retries >= config.SAVELOAD_ENTER_RETRIES:
                left_open = self._input_box_open()
                break
            state = self._press_enter_again()
            if state == "pressed":
                retries += 1
                self._log(f"Enter を押し直しました（{retries} 回目）")
                deadline = max(deadline, time.time() + config.SAVELOAD_RETRY_WAIT_SEC)
                continue
            if state == "closed":
                # 入力欄は閉じた（Enter は効いた）。残りの時間ログを待つ
                if self._wait_loaded(log_path, offset, deadline):
                    self._log("✅ コードを読み込みました")
                    return "loaded"
            else:
                left_open = state == "open"
            break
        self._log("⚠ コードの読み込みを確認できませんでした")
        if left_open:
            # Esc は押さない（閉じると貼ったコードが消えて、手で確定できなくなる）
            self._log("⚠ 入力欄が開いたままです（手で Enter を押してください）")
        return "unconfirmed"

    def _wait_loaded(self, log_path: str, offset: int, until: float) -> bool:
        """until（time.time）までに、ログの offset より後に「Loaded Data Successfully」が出たか"""
        while self._is_running():
            if log_path and SaveLoad.log_has_loaded_line(log_path, offset):
                return True
            if time.time() >= until:
                return False
            time.sleep(config.SAVELOAD_LOG_POLL_SEC)
        return False

    def _input_box_open(self) -> bool:
        """入力欄（送信の矢印）が開いたままか。撮影はメモリの中だけ（書き出さない・文字は読まない）。
        Ctrl+V の後に撮るのは、ここだけ（依頼者の了承。EC）"""
        bgr = self._capture_bgr()
        width, height = WindowOperator.client_size(self._hwnd) or (0, 0)
        shown = bgr is not None and SaveLoad.input_box_shown(bgr, width, height)
        del bgr                         # すぐ捨てる
        return shown

    def _press_enter_again(self) -> str:
        """入力欄が開いたままなら、鍵の中で前面を借りて Enter をもう 1 回。"pressed"・"closed"（閉じていた）・
        "open"（開いたままだが押せなかった：前面にできない・中止）"""
        if not self._input_box_open():
            return "closed"
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or not self._can_operate():
                return "open"
            cursor = WindowOperator.cursor_position()
            ok, loan = WindowOperator.borrow_front(self._hwnd)
            if not ok:
                return "open"
            try:
                if not self._still_ours():
                    return "open"
                WindowOperator.send_keys("enter")
                DebugLog.write("[コードの読み込み] 入力欄が開いたまま → Enter を押し直した")
                return "pressed"
            finally:
                try:
                    WindowOperator.return_front(loan)
                finally:
                    if cursor is not None:
                        WindowOperator.set_cursor_position(cursor)

    def _capture_bgr(self):
        bits, w, h = self.capture()
        if not bits or w <= 0 or h <= 0 or len(bits) < w * h * 4:
            return None
        import numpy as np
        bgra = np.frombuffer(bits, dtype=np.uint8)[:w * h * 4].reshape(h, w, 4)
        return np.ascontiguousarray(bgra[:, :, :3])       # 書き出さない（メモリの中だけ）

    def _still_ours(self) -> bool:
        return self._is_running() and WindowOperator.foreground_hwnd() == self._hwnd

    def _paste_code(self, code: str, found) -> str:
        """鍵の中で前面を借りて: Tab を押したまま → カーソルをコード欄へ → クリック → Tab を離す →
        入力欄が出るのを待つ → コードをクリップボードへ入れ直す → Ctrl+V → Enter。
        どの終わり方でも Tab を離し、前面とカーソルを返す。"pasted" か、貼らなかった理由"""
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or not self._can_operate():
                return "中止"
            cursor = WindowOperator.cursor_position()
            ok, loan = WindowOperator.borrow_front(self._hwnd)
            if not ok:
                return "前面にできない"
            tab = False
            try:
                origin = WindowOperator.window_origin(self._hwnd)
                if origin is None or not self._still_ours():
                    return "中止" if not self._is_running() else "前面でなくなった"
                point = (int(round(found[0] + origin[0])), int(round(found[1] + origin[1])))
                WindowOperator.press_tab()
                tab = True
                if config.CLICK_TAB_LEAD_SEC > 0:
                    time.sleep(config.CLICK_TAB_LEAD_SEC)
                if not self._place_cursor(point):
                    return "カーソルを置けない"
                if not self._still_ours():
                    return "中止" if not self._is_running() else "前面でなくなった"
                DebugLog.write(f"[コードの読み込み] コード欄をクリック {point}")
                WindowOperator.mouse_click(config.SAVELOAD_CLICK_SEC)
                WindowOperator.release_tab()
                tab = False
                if not self._wait_input_box():
                    return "入力欄が出ない" if self._is_running() else "中止"
                if not self._still_ours():
                    return "中止" if not self._is_running() else "前面でなくなった"
                if not SaveLoad.write_clipboard(code):
                    return "クリップボードに入れられない"
                # ここから後は撮らない（入力欄にコードが写る）。例外は Enter が効いたかを見る
                # _input_box_open だけ（メモリの中だけで矢印の形を見る。EC）
                WindowOperator.send_keys("ctrl+v")
                time.sleep(config.SAVELOAD_PASTE_ENTER_SEC)
                if not self._still_ours():
                    return "中止" if not self._is_running() else "前面でなくなった"
                WindowOperator.send_keys("enter")
                DebugLog.write("[コードの読み込み] 貼って Enter を押した")
                return "pasted"
            finally:
                try:
                    if tab:
                        WindowOperator.release_tab()
                finally:
                    try:
                        WindowOperator.return_front(loan)
                    finally:
                        if cursor is not None:
                            WindowOperator.set_cursor_position(cursor)

    def _place_cursor(self, point) -> bool:
        """カーソルを point へ置いて読み直す。ずれていれば置き直す（ItemFetch と同じ回数・幅）"""
        tol = config.FETCH_CURSOR_TOL_PX
        for _tries in range(config.FETCH_CURSOR_TRIES):
            if not self._still_ours():
                return False
            WindowOperator.set_cursor_position(point)
            time.sleep(config.FETCH_CURSOR_SETTLE_SEC)
            read = WindowOperator.cursor_position()
            if read is not None and abs(read[0] - point[0]) <= tol and abs(read[1] - point[1]) <= tol:
                return True
        DebugLog.write(f"[コードの読み込み] カーソルを {point} へ置けない")
        return False

    def _wait_input_box(self) -> bool:
        """入力欄（送信の矢印）が出るまで撮って見る（最大 SAVELOAD_BOX_WAIT_SEC）。撮影は書き出さない"""
        deadline = time.time() + config.SAVELOAD_BOX_WAIT_SEC
        while self._is_running():
            bgr = self._capture_bgr()
            width, height = WindowOperator.client_size(self._hwnd) or (0, 0)
            if bgr is not None and SaveLoad.input_box_shown(bgr, width, height):
                DebugLog.write("[コードの読み込み] 入力欄が出た")
                return True
            if time.time() >= deadline:
                DebugLog.write(f"[コードの読み込み] {config.SAVELOAD_BOX_WAIT_SEC:.0f}秒で入力欄が出ない")
                return False
            time.sleep(config.SAVELOAD_BOX_POLL_SEC)
        return False

    def close(self):
        try:
            self._osc.stop_all()
            self._osc.close()
        except Exception:
            DebugLog.exception("ToNEntry.close")
            pass
