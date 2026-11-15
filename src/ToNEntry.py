"""
ToN入室直後の選択画面を自動で突破する

選択画面の 4 つは、画面から四角（ボタンの枠。EntryPanel）を探し、Tab を押したまま
カーソルをその上へ置いてクリックする。
  - VRChatにはデスクトップ用のインタラクト入力が無いため、クリックはフォーカスを取って
    実マウスを送る必要がある（OSCでは代替できない）
  - Tab を押している間はカーソルが自由に動く（離すと照準へ戻る）
  - 選択中は OSC で動かない。選び終わるとワールド側で共通の座標へ移される

パネルの出現待ちも、四角が見つかるかで判定する（ロード画面は四隅の青で除外）。
画面取得はEasy Anti-Cheat下でも PrintWindow で可能。
ロビーの Begin へ向かう移動（press_begin）とセーブコードの読み込み（load_code）は別。
"""
import time

import win32gui

import config
import DebugLog
import EntryPanel
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
    def __init__(self, hwnd: int, osc_port: int,
                 log=None, is_running=None, can_operate=None,
                 flavor: int = None, music: int = None):
        self._hwnd = hwnd
        self._log = log or (lambda _m: None)
        self._is_running = is_running or (lambda: True)
        # この窓で移動・クリックしてよいか（private だけ。ActionExecutor.can_operate_in）。
        # 渡されなければ分からない → しない
        self._can_operate = can_operate or (lambda: False)
        self._osc = OSCClient.OSCClient(osc_port)
        self._osc_port = osc_port
        # 難易度（0 Casual・1 Mild・2 Spicy）と音楽（0 Yeah・1 Nope）。None なら既定
        self._flavor = config.TON_ENTRY_FLAVOR_DEFAULT if flavor is None else flavor
        self._music = config.TON_ENTRY_MUSIC_DEFAULT if music is None else music

    # ── 画面取得 ──────────────────────────────

    def capture(self):
        """PrintWindowでウィンドウの画素を取得する。(bits, 幅, 高さ)"""
        return ScreenCapture.capture_window(self._hwnd)

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

    def wait_for_panel(self, timeout: float = None) -> bool:
        """選択パネルが出るまで待つ。

        撮って、四角（ボタンの枠）が 1 個以上見つかれば出たとみなす。ロード画面は見送る
        （四隅の青しか見ないので、季節のロゴの色と関係しない）。
        """
        timeout = timeout or config.TON_ENTRY_PANEL_TIMEOUT
        deadline = time.time() + timeout
        while time.time() < deadline and self._is_running():
            try:
                bits, w, h = self.capture()
                if not self._is_loading_screen(bits, w, h):
                    bgr = self._to_bgr(bits, w, h)
                    height = (WindowOperator.client_size(self._hwnd) or (0, h))[1]
                    if bgr is not None and EntryPanel.pick(bgr, height, None, 0) is not None:
                        self._log("✅ 選択パネルを確認")
                        return True
            except Exception:
                DebugLog.exception("ToNEntry.wait_for_panel")
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

    def _find_button(self, step: dict):
        """1 画面ぶん、四角が出るのを TON_ENTRY_STEP_FIND_SEC の間待ち、押す点（画面の座標）を返す。
        step は label・expect・pick（押す番号。整数）。見つからなければ数字だけを debug.log に書いて None
        （今の移動方式には戻さない）"""
        label = step["label"]
        deadline = time.time() + config.TON_ENTRY_STEP_FIND_SEC
        bgr = None
        height = 0
        while self._is_running():
            bgr = self._capture_bgr()
            size = WindowOperator.client_size(self._hwnd)
            origin = WindowOperator.window_origin(self._hwnd)
            if size is None or origin is None:
                return None
            height = size[1]
            point = (EntryPanel.pick(bgr, height, step["expect"], step["pick"])
                     if bgr is not None else None)
            if point is not None:
                return (int(round(point[0] + origin[0])), int(round(point[1] + origin[1])))
            if time.time() + config.TON_ENTRY_POLL_SEC > deadline:
                break
            time.sleep(config.TON_ENTRY_POLL_SEC)
        if not self._is_running():
            return None
        DebugLog.write(f"[入室操作] {label}: "
                       + (EntryPanel.miss_numbers(bgr, height, step["expect"]) if bgr is not None else "撮影なし"))
        return None

    def _click_point(self, point, label: str) -> bool:
        """鍵の中で前面を借りて: Tab を押したまま → カーソルを point（画面の座標）へ → クリック。
        どの終わり方でも Tab を離し、前面とカーソルを返す。Tab を押すとカーソルが照準へ戻るので、
        置くのは必ず Tab の後"""
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or not self._can_operate():
                return False
            cursor = WindowOperator.cursor_position()
            ok, loan = WindowOperator.borrow_front(self._hwnd)
            if not ok:
                self._log(f"⚠ {label}: フォーカス取得失敗 → 中止")
                return False
            tab = False
            try:
                WindowOperator.press_tab()
                tab = True
                if config.CLICK_TAB_LEAD_SEC > 0:
                    time.sleep(config.CLICK_TAB_LEAD_SEC)
                if not self._place_cursor(point):
                    return False
                if not self._still_ours():
                    return False
                WindowOperator.mouse_click(config.TON_ENTRY_CLICK_SEC)
                self._log(f"クリック（{label}）")
                return True
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

    def _step_index(self, step: dict) -> int:
        """step["pick"] を番号にする。整数はそのまま・"flavor" は難易度・"music" は音楽"""
        pick = step["pick"]
        if pick == "flavor":
            return self._flavor
        if pick == "music":
            return self._music
        return pick

    def run(self) -> bool:
        """入室時の選択画面を順に突破する。

        手順は config.TON_ENTRY_STEPS（警告の了解・難易度・音楽・LET ME PLAY）。
        各画面で四角を探し、Tab を押したままカーソルを置いてクリックする。
        四角が見つからなければ押さずに止める。移動（OSC）はしない。
        """
        if not self._can_operate():
            self._log("入室時の自動操作はプライベートのインスタンスだけで行います → しません")
            return False
        if not EntryPanel.available():
            self._log("⚠ 画像の判定が使えません → 入室操作をしません")
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
            point = self._find_button({**step, "pick": self._step_index(step)})
            if point is None:
                self._log(f"⚠ {step['label']}のボタンが見つかりません → 入室操作を止めます")
                self._osc.stop_all()
                return False
            if not self._click_point(point, step["label"]):
                self._osc.stop_all()
                return False
            time.sleep(config.TON_ENTRY_STEP_WAIT_SEC)

        self._osc.stop_all()
        self._log("✅ 入室時の選択画面を進めました")
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
        # 読めず、入力欄が閉じている（Enter は効いたが貼り付けが空振りした）なら、コード欄を探すところから
        # やり直す（SAVELOAD_TRIES 回まで。ES）。入力欄が開いたままならやり直さない（貼ったコードを消さない）
        for attempt in range(1, config.SAVELOAD_TRIES + 1):
            result = self._load_once(code, log_path)
            if result == "loaded":
                return "loaded"
            if result == "skipped" and attempt == 1:
                return "skipped"
            if result != "closed" or attempt == config.SAVELOAD_TRIES or not self._is_running():
                break
            self._log("コードの読み込みをやり直します")
        self._log("⚠ コードの読み込みを確認できませんでした")
        if result == "open":
            # Esc は押さない（閉じると貼ったコードが消えて、手で確定できなくなる）
            self._log("⚠ 入力欄が開いたままです（手で Enter を押してください）")
        return "unconfirmed"

    def _load_once(self, code: str, log_path: str) -> str:
        """コード欄を探して貼って Enter、ログを待つ 1 回分。"loaded"・"closed"（読めず、入力欄は閉じている）・
        "open"（読めず、入力欄が開いたまま）・"skipped"（コード欄が無い・貼れない。理由は画面のログへ）"""
        found = self._find_codebox()
        if found is None:
            self._log("⚠ コード欄が見つかりません → コードを読み込みません")
            return "skipped"
        if len(found) > 4 and found[4] == "features":
            DebugLog.write(f"[コードの読み込み] コード欄 ({found[0]:.1f}, {found[1]:.1f})・特徴点 インライア "
                           f"{found[2]}・倍率 {found[3]:.2f}")
        else:
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
        return "open" if left_open else "closed"

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
                DebugLog.write("[コードの読み込み] 入力欄が開いたまま → Enter を押し直しました")
                return "pressed"
            finally:
                try:
                    WindowOperator.return_front(loan)
                finally:
                    if cursor is not None:
                        WindowOperator.set_cursor_position(cursor)

    def _find_codebox(self):
        """撮ってコード欄を探す。見つからなければ SAVELOAD_FIND_SEC の間 SAVELOAD_FIND_POLL_SEC おきに撮り直す
        （EX: 入室直後は描画が遅れることがある）。それでも無ければ、数字だけを debug.log に書いて None。
        撮影はメモリの中だけ（コード欄にはセーブコードが出ていることがあるので書き出さない）"""
        deadline = time.time() + config.SAVELOAD_FIND_SEC
        shots = 0
        while True:
            bgr = self._capture_bgr()
            shots += 1
            height = WindowOperator.client_height(self._hwnd) or 0
            found = SaveLoad.find_codebox(bgr, height) if bgr is not None else None
            if found is not None:
                return found
            if not self._is_running() or time.time() + config.SAVELOAD_FIND_POLL_SEC > deadline:
                break
            time.sleep(config.SAVELOAD_FIND_POLL_SEC)
        DebugLog.write(f"[コードの読み込み] コード欄が見つからない（{shots} 回）: "
                       + SaveLoad.miss_numbers(bgr, height))
        return None

    def _capture_bgr(self):
        bits, w, h = self.capture()
        return self._to_bgr(bits, w, h)

    @staticmethod
    def _to_bgr(bits, w, h):
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
                # 出た直後はまだ文字を受け付けないことがある（ES）。少し待ってから貼る
                time.sleep(config.SAVELOAD_SETTLE_SEC)
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
                DebugLog.write("[コードの読み込み] 貼って Enter を押しました")
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
        DebugLog.write(f"[コードの読み込み] カーソルを {point} へ置けません")
        return False

    def _wait_input_box(self) -> bool:
        """入力欄（送信の矢印）が出るまで撮って見る（最大 SAVELOAD_BOX_WAIT_SEC）。撮影は書き出さない"""
        deadline = time.time() + config.SAVELOAD_BOX_WAIT_SEC
        while self._is_running():
            bgr = self._capture_bgr()
            width, height = WindowOperator.client_size(self._hwnd) or (0, 0)
            if bgr is not None and SaveLoad.input_box_shown(bgr, width, height):
                DebugLog.write("[コードの読み込み] 入力欄が出ました")
                return True
            if time.time() >= deadline:
                DebugLog.write(f"[コードの読み込み] {config.SAVELOAD_BOX_WAIT_SEC:.0f}秒で入力欄が出ません")
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
