import time
from typing import Callable

import config
import BeginDetect
import ScreenCapture
import SharedState
import WindowOperator
import OSCClient
import PlaySound
from State import WindowConfig, WindowState


# ═══════════════════════════════════════════════
#  窓操作アクション実行クラス
#  自爆・Begin自動操作・AFK防止ループを担当する。
#  LogMonitor からロジック（判定）と操作（アクション）を分離するために存在する。
# ═══════════════════════════════════════════════
class ActionExecutor:
    def __init__(
        self,
        cfg: WindowConfig,
        st: WindowState,
        is_running: Callable[[], bool],
        log: Callable[[str], None],
    ):
        self._cfg = cfg
        self._st = st
        self._is_running = is_running
        self._log = log
        # OSCが使える窓では移動をOSCで行う。フォーカスを奪わないので
        # 排他ロックが不要になり、他窓と並行して動ける。
        self._osc = OSCClient.OSCClient(cfg.osc_port) if cfg.osc_port else None

    @property
    def uses_osc(self) -> bool:
        return self._osc is not None

    def move(self, direction: str, seconds: float):
        """移動する。OSCが使えるならフォーカスを奪わずに送る。

        使えない場合（手動起動の2窓目以降など）は従来どおりキーを押す。
        キー入力はフォーカスを要するため、呼び出し側がロックを取ること。

        OSC移動は他窓の操作を一切妨げないので、全窓フリーズ
        （装備待ち・続行ラウンド）中でも実行してよい。待ち合わせるのは
        フォーカスを要する操作（クリック・キー入力）だけ。
        """
        if seconds <= 0:
            return
        if self._osc is not None:
            address = {"forward": "/input/MoveForward",
                       "back": "/input/MoveBackward",
                       "left": "/input/MoveLeft",
                       "right": "/input/MoveRight"}.get(direction)
            if address:
                self._osc.press(address, seconds)
                self._osc.stop_all(repeat=1)
                return
        key = {"forward": "w", "back": "s", "left": "a", "right": "d"}.get(direction)
        if key:
            WindowOperator.hold_key(key, seconds)

    def move_forward_left(self, forward_sec: float, left_sec: float):
        """前進しながら、その前半だけ左にも寄る（斜め → 直進）。

        MoveForward を通しで1回押し、MoveLeft を頭から left_sec だけ重ねる。
        加速と減速が各1回で済むので、逐次に「前進 → 左」と押すより誤差が小さい。

        逐次だと前進の減速が終わらないうちに左移動が始まり、残留速度が
        混ざって到達点が毎回ズレる。同時押しならその結合が起きない。

        OSCが使えない窓はキー入力になるため同時押しができない。その場合は
        従来どおり逐次で動かす。
        """
        if self._osc is not None:
            self._osc.press_multi([("/input/MoveForward", forward_sec),
                                   ("/input/MoveLeft", left_sec)])
            self._osc.stop_all(repeat=1)
            return
        self.move("forward", forward_sec)
        self.move("left", left_sec)

    # ── ヘルパー ──────────────────────────────

    def _hands_free(self) -> bool:
        """この窓で放置モードが効いているか（private系インスタンスのみ）"""
        return (SharedState.get_hands_free()
                and self._st.instance_type == config.INSTANCE_PRIVATE)

    def announce_item_lost_once(self):
        """アイテムロスト音声を一度だけ再生する"""
        if self._hands_free():
            # 放置モード中は見ていないので鳴らさない
            return
        if self._st.item_lost_announced:
            return
        self._st.item_lost_announced = True
        PlaySound.play_sound(self._cfg.voice_item_lost)

    def announce_item_lost_if_needed(self):
        """アイテムを失っていればロストを伝える（Beginクリックの直前で呼ぶ）。

        ラウンド終了時ではなくクリックの瞬間に鳴らす。終了直後は移動や
        他窓のフリーズ解除待ちが残っていて、鳴らしても手を打てないため。
        判定条件は LogMonitor._round_item_warning() と揃えてある。
        """
        st = self._st
        if (st.waiting_for_equip
                or (st.item_lost_this_round and not st.item_id)
                or st.randomizer_item_changed):
            self.announce_item_lost_once()

    def focus(self) -> bool:
        """この窓にフォーカスを当てる。失敗したら False を返す。

        フォーカスを取れないまま操作を送ると、別のウィンドウにキーや
        クリックが飛ぶため、呼び出し側は必ず戻り値を確認すること。
        """
        hwnd = self._cfg.hwnd
        if WindowOperator.focus_window(hwnd):
            self._log(f"フォーカス切替 → HWND={hwnd:#010x}")
            return True
        self._log(f"⚠ フォーカス取得失敗 HWND={hwnd:#010x} → 操作を中止")
        return False

    # ── 自爆 ──────────────────────────────────

    def do_skip(self):
        """キー長押しで自爆する"""
        st = self._st
        if self._cfg.hwnd == 0:
            self._log("自爆キャンセル（HWND未選択）")
            return
        if st.waiting_for_equip:
            self._log("自爆キャンセル（アイテムロスト待ち中）")
            return
        # 他窓が装備待ち・続行ラウンド中はフリーズ（自分が続行ラウンド中は除く）
        if not st.is_continue_round:
            while self._is_running() and st.in_round:
                eq_ok  = SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
                con_ok = SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                if eq_ok and con_ok:
                    break
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or st.is_continue_round or not st.in_round:
                return
            if st.waiting_for_equip:
                self._log("自爆キャンセル（ロック取得後にアイテムロスト待ち検出）")
                return
            if not self.focus():
                return
            st._skip_time = time.time()
            self._log(f"自爆実行中 ({config.SUICIDE_HOLD_SEC}秒)…")
            time.sleep(config.SUICIDE_FOCUS_SETTLE_SEC)
            WindowOperator.hold_key(SharedState.get_suicide_key(), config.SUICIDE_HOLD_SEC)

    def _begin_precheck(self, check_freeze: bool = True) -> bool:
        """Begin実行前の中止条件を確認する。続行してよければTrue。

        check_freeze=False では他窓フリーズの確認を省く。OSC移動の前に
        呼ぶときに使う（移動はフリーズ中でも行うため）。
        """
        st = self._st
        if not self._is_running() or st.in_round:
            self._log("Begin キャンセル（停止 or 次のラウンドが開始）")
            if st.waiting_for_equip and st.in_round:
                SharedState.equip_freeze_end(st)
                self._log("ラウンド開始によりフリーズ解除 → 装備待ちへ")
            elif not st.waiting_for_equip:
                return False
        if check_freeze and not st.waiting_for_equip and not st.is_continue_round:
            if not SharedState.CONTINUE_ROUND_EVENT.is_set():
                self._log("Begin キャンセル（他窓のフリーズを検出）")
                return False
        return True

    def _wait_other_windows(self) -> bool:
        """他窓のフリーズ（装備待ち・続行ラウンド）解除を待つ。続行可ならTrue。

        自窓が続行ラウンド中／アイテムロスト中はフリーズの発生源が自分なので
        待たない（自分の張ったフリーズを自分で待つデッドロックになる）。

        OSC移動はこの待ちの対象外。フォーカスを奪わず他窓を妨げないため、
        フリーズ中でも移動は進めてよい。呼ぶのはフォーカスを要する操作
        （クリック・キー入力）の直前だけにすること。
        """
        st = self._st
        if st.is_continue_round or st.waiting_for_equip:
            return self._is_running()
        while self._is_running():
            eq_ok  = SharedState.EQUIP_WAIT_EVENT.is_set()
            con_ok = SharedState.CONTINUE_ROUND_EVENT.is_set()
            if eq_ok and con_ok:
                return True
            if not eq_ok:
                self._log("他窓の装備待ち中 → フリーズ")
            if not con_ok:
                self._log("他窓の続行/霧ラウンド中 → フリーズ")
            SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
            SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
        return False

    def _handle_item_lost(self) -> bool:
        """アイテムロスト時のフリーズ処理。続行してよければTrue。

        ロストの判定は Verified Round End で行われるため、必ず
        _wait_round_end() を通してから呼ぶこと。RoundOver時点では
        まだ waiting_for_equip が立っておらず、フリーズが張られない。
        """
        st = self._st
        if not st.waiting_for_equip:
            return True

        if SharedState.get_item_begin_mode():
            # アイテム取得→Beginモード:
            # フリーズ発生源は自窓なので他窓の解除待ちはせず、
            # 装備確認 → Begin の順で進む。ここで他窓解除待ちをすると
            # 自分が張ったフリーズを自分で待つデッドロックになる。
            SharedState.equip_freeze_start(st)
            self.announce_item_lost_once()
            self._log("⚠ アイテムロスト → 全窓フリーズ（装備するとBeginへ進みます）")
            while self._is_running() and not st.item_id and not st.in_round:
                time.sleep(0.3)
            if not self._is_running():
                SharedState.equip_freeze_end(st)
                return False
            if st.in_round:
                # 手動Begin等でラウンド開始 → ROUND_START側で解除済み
                return False
            self._log("✅ アイテム装備確認 → Beginへ向かいます")
        else:
            # Begin時フリーズモード: 他窓の解除を待ってから即フリーズ
            if not st.is_continue_round:
                while self._is_running():
                    if (SharedState.EQUIP_WAIT_EVENT.is_set()
                            and SharedState.CONTINUE_ROUND_EVENT.is_set()):
                        break
                    SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                if not self._is_running():
                    return False
            SharedState.equip_freeze_start(st)
            # 通知はここではなくBeginクリックの直前で行う
            self._log("⚠ アイテムロスト → 全窓フリーズ（Beginへ向かいます）")
        return True

    def _wait_round_end(self, timeout: float = 30.0) -> bool:
        """Verified Round End が来るまで待つ。これが来ないとBeginは押せない。"""
        st = self._st
        if st.round_end_seen:
            return True
        self._log("Verified Round End を待っています…")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._is_running() or st.in_round:
                return False
            if st.round_end_seen:
                return True
            time.sleep(0.2)
        self._log("Verified Round End が来ないためBeginを中止")
        return False

    # ── Begin画像認識 ─────────────────────────

    def _detect_begin(self):
        """中心に最も近い候補を (dx, dy, info) で返す。無ければ None。"""
        cands = self._detect_begin_all()
        if not cands:
            return None
        c = cands[0]
        return c["dx"], c["dy"], {"area": c["area"], "bbox": c["bbox"]}

    def _detect_begin_all(self) -> list:
        """画面のBEGIN候補を中心に近い順で返す。

        キャプチャはPrintWindowなのでフォーカスを要さない（背景の窓でも撮れる）。
        """
        bits, w, h = ScreenCapture.capture_window(self._cfg.hwnd)
        if not bits:
            return []
        return BeginDetect.detect_all(bits, w, h)

    def _is_rejected(self, c) -> bool:
        """撃っても効かなかった候補か"""
        r = config.BEGIN_REJECT_RADIUS_PX
        return any(abs(c["cx"] - rx) <= r and abs(c["cy"] - ry) <= r
                   for rx, ry in self._st.begin_reject)

    def _strafe_toward(self, target: dict):
        """対象が中心へ来る向きに横移動し、移動後の同じ対象を返す。

        px→秒の係数は固定値を置かず実測で求める。距離によって変わるので毎回更新する。
        移動後は別の塊が中心に来ている可能性があるため、係数は「中心に最も近い候補」
        ではなく「移動前の重心に最も近い候補」と比べて測る。
        旋回（look）は使わない。失敗したまま次のラウンドに入ると向きのズレが残り、
        次のラウンドのBegin前移動が明後日の方向へ行くため。
        """
        st = self._st
        dx = target["dx"]
        gain = st.begin_strafe_gain
        sec = config.BEGIN_PROBE_SEC if gain <= 0 else abs(dx) / gain
        sec = max(config.BEGIN_STRAFE_MIN_SEC, min(config.BEGIN_STRAFE_MAX_SEC, sec))
        # dxが0付近でも必ず最低量は動く。動かないと同じ対象を撃ち続けて固まる
        self.move("right" if dx > 0 else "left", sec)

        after = self._detect_begin_all()
        if not after:
            return None
        same = min(after, key=lambda c: abs(c["cx"] - target["cx"])
                                        + abs(c["cy"] - target["cy"]))
        moved = abs(dx - same["dx"])
        if moved > 5:   # ノイズを除く
            g = moved / sec
            if config.BEGIN_GAIN_MIN <= g <= config.BEGIN_GAIN_MAX:
                st.begin_strafe_gain = g
        return same

    def _guided_retry_click(self) -> bool:
        """候補を1つ選び、少し寄せてから撃つ。

        BEGINと壁のINTERMISSIONは画像では見分けられないので、効かなかった候補を
        除外して次を試す総当たりで解く。中心到達は待たない（BEGIN台は大きく、
        文字の重心がズレていてもクロスヘアは黒い天板に乗っているため）。

        戻り値は「リトライを続けてよいか」。Falseなら do_after_round を抜ける。
        """
        st = self._st
        # 前回撃った対象は効かなかった（ラウンドが始まっていない）→ 除外へ
        if st.begin_last_target is not None:
            st.begin_reject.append(st.begin_last_target)
            self._log("前回の対象は効かなかった → 除外（残り候補を試す）")
            st.begin_last_target = None

        cands = self._detect_begin_all()
        if not cands:
            self._log("BEGIN を検出できず → 今回のクリックは見送り")
            return True

        live = [c for c in cands if not self._is_rejected(c)]
        if not live:
            self._log("候補を一巡した → 除外をリセット")
            st.begin_reject = []
            live = cands

        target = live[0]
        # 必ず動く（無移動だと同じ場所を撃ち続けて固まる）
        self._strafe_toward(target)

        # 移動後に取り直す。取れなければ移動前の値でそのまま撃つ
        after = [c for c in self._detect_begin_all() if not self._is_rejected(c)]
        shot = after[0] if after else target

        if not self._is_running() or st.in_round or st.begin_done:
            return False
        if not self._wait_other_windows():
            return False
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or st.in_round or st.begin_done:
                return False
            if not self.focus():
                # フォーカス失敗でBeginを諦めない。他窓の自爆直後などは
                # SetForegroundWindow が一時的に拒否される
                return True
            self._log(f"Beginクリック（中心から {shot['dx']:+.0f}px, area={shot['area']}）")
            WindowOperator.click()
        st.begin_last_target = (shot["cx"], shot["cy"])
        return True

    def _retry_move(self, attempt: int):
        """Beginリトライ時の位置合わせ。左右交互に振れ幅を広げていく。

        1回目 左0.10 → 右0.08 → 左0.10 → 右0.12 → 左0.14 … と
        BEGIN_RETRY_STEP_INC_SEC ずつ増やし、当たりを外側へ探しに行く。
        """
        if attempt <= 1:
            self.move("left", config.BEGIN_RETRY_FIRST_LEFT_SEC)
            return
        direction = "right" if attempt % 2 == 0 else "left"
        sec = round(config.BEGIN_RETRY_STEP_START_SEC
                    + config.BEGIN_RETRY_STEP_INC_SEC * (attempt - 2), 3)
        self.move(direction, sec)

    def _begin_move(self):
        """Begin前の定位置移動。ラウンド種別で距離が変わる。"""
        st = self._st
        if st.round_type in config.LATE_ROUND:
            self.move_forward_left(config.BEGIN_FORWARD_SEC_LATER,
                                   config.BEGIN_LEFT_SEC_LATER)
        else:
            self.move_forward_left(config.BEGIN_FORWARD_SEC,
                                   config.BEGIN_LEFT_SEC)

    # ── Begin自動操作 ─────────────────────────

    def do_after_round(self):
        """
        ラウンド終了後: 待機 → [Begin前移動] → Beginクリック＆リトライ

        ロック戦略:
          - 移動・クリック: ロックを取って実行（他窓と干渉しない）
          - クリック後の「ラウンド開始待ち」: ロックを解放して待機
            → 待機中に他窓が操作できる
        """
        st = self._st
        # RoundOver から一定時間待ってから移動を始める。移動し終える頃に
        # Verified Round End が出てクリックできる状態になる想定。
        if config.BEGIN_WAIT_SEC > 0:
            elapsed = (time.time() - st.round_over_time) if st.round_over_time else 0.0
            remain = config.BEGIN_WAIT_SEC - elapsed
            if remain > 0:
                self._log(f"ラウンド終了から{config.BEGIN_WAIT_SEC}秒待機（残り{remain:.1f}秒）")
                time.sleep(remain)
        # Beginはフレ/フレ+/招待/招待+のみ
        if st.instance_type != config.INSTANCE_PRIVATE:
            return
        if not self._is_running() or st.in_round:
            self._log("Begin キャンセル（停止 or 次のラウンドが開始）")
            return

        # 他窓のフリーズ解除待ち。
        # OSC窓は移動でフォーカスを奪わないので、フリーズ中でもBegin前移動まで
        # 済ませておき、待つのはクリックの直前だけにする（解除された瞬間に
        # 押せる位置に居られる）。キー入力で移動する窓は移動にもフォーカスが
        # 要るため、従来どおり移動の前に待つ。
        if not self.uses_osc:
            if not self._wait_other_windows():
                return

        # ── フェーズ1: Begin前移動 + 初回クリック ──
        # OSCが使える窓は移動でフォーカスを奪わないため、ロックを取らずに
        # 移動できる（他窓と並行して動ける）。クリックだけロック内で行う。
        # OSCが使えない窓は移動もキー入力なので、従来どおり全体をロックする。
        if self.uses_osc:
            # 移動はフリーズ中でも行うので、ここでのフリーズ確認は省く
            if not self._begin_precheck(check_freeze=False):
                return
            if not st.in_round:
                self._begin_move()
                # ロスト判定は Verified Round End で行われるので、必ず
                # 待ってからフリーズ処理をする。RoundOver時点で判定すると
                # まだ立っておらずフリーズが張られない。
                if not self._wait_round_end():
                    return
                if not self._handle_item_lost():
                    return
                # ここから先はフォーカスを要するので他窓の解除を待つ
                if not self._wait_other_windows():
                    return
                if not self._begin_precheck():
                    return
                time.sleep(0.1)
                if not st.in_round:
                    with SharedState._GLOBAL_ACTION_LOCK:
                        if not self._is_running() or st.in_round:
                            return
                        if not self.focus():
                            return
                        self.announce_item_lost_if_needed()
                        self._log("Beginクリック")
                        WindowOperator.click()
        else:
            # キー入力での移動はフォーカスが要るのでロック内で行う。
            # ロスト処理はロックを取る前に済ませる（フリーズ待ちで
            # ロックを保持し続けると他窓が動けなくなるため）。
            if not self._wait_round_end():
                return
            if not self._handle_item_lost():
                return
            with SharedState._GLOBAL_ACTION_LOCK:
                if not self._begin_precheck():
                    return
                if not st.in_round:
                    if not self.focus():
                        return
                    self._begin_move()
                    time.sleep(0.1)
                    if not st.in_round:
                        self.announce_item_lost_if_needed()
                        self._log("Beginクリック")
                        WindowOperator.click()

        # ── フェーズ2a: アイテムロスト装備待ち（ロック外）──
        # 装備済み（アイテム取得→Beginモードで先に装備確認済み）の場合はスキップし、
        # フェーズ2のBegin確認・リトライへ進む（解除はBEGIN_DONEイベント側で行う）
        if st.waiting_for_equip and not st.item_id:
            self._log("アイテム装備を待っています… （装備すると自動再開）")
            while st.waiting_for_equip and self._is_running():
                time.sleep(0.3)
            if not self._is_running():
                SharedState.equip_freeze_end(st)
                return
            self._log("✅ アイテム装備確認 → 続行")
            if st.in_round:
                return

        # ── フェーズ2: Begin確認待ち＆リトライ（ロック外）──
        if st.begin_done:
            return
        for attempt in range(1, config.BEGIN_RETRY_MAX + 1):
            self._log(f"Begin確認待ち… [{attempt - 1}/{config.BEGIN_RETRY_MAX}]")
            waited = 0.0
            while waited < config.BEGIN_RETRY_WAIT_SEC:
                time.sleep(0.2)
                waited += 0.2
                if not self._is_running():
                    return
                if st.begin_done:
                    return
                # OSC窓は移動を止めないため、ここでは待たずクリック直前で待つ
                if not self.uses_osc and not st.is_continue_round and not st.waiting_for_equip:
                    if not SharedState.EQUIP_WAIT_EVENT.is_set():
                        self._log("Begin待機中に他窓がアイテムロスト → 一時フリーズ")
                        while self._is_running() and not SharedState.EQUIP_WAIT_EVENT.is_set():
                            SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    if not SharedState.CONTINUE_ROUND_EVENT.is_set():
                        self._log("Begin待機中に他窓が続行ラウンド開始 → 一時フリーズ")
                        while self._is_running() and not SharedState.CONTINUE_ROUND_EVENT.is_set():
                            SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
            if not self._is_running():
                return
            if attempt < config.BEGIN_RETRY_MAX:
                self._log(f"Verified未確認 → リトライ {attempt}/{config.BEGIN_RETRY_MAX}")
                if self.uses_osc and config.BEGIN_DETECT_ENABLED:
                    # 画面のBEGINを見て寄せてから撃つ。見えなければ撃たない。
                    if not self._guided_retry_click():
                        return
                elif self.uses_osc:
                    # 位置合わせはOSC（ロック不要・フリーズ中でも実行）、
                    # クリックだけ他窓の解除を待ってロック内で行う
                    self._retry_move(attempt)
                    time.sleep(0.1)
                    if st.in_round or st.begin_done:
                        return
                    if not self._wait_other_windows():
                        return
                    if st.in_round or st.begin_done:
                        return
                    with SharedState._GLOBAL_ACTION_LOCK:
                        if not self._is_running() or st.in_round or st.begin_done:
                            return
                        if not self.focus():
                            return
                        WindowOperator.click()
                else:
                    with SharedState._GLOBAL_ACTION_LOCK:
                        if not self._is_running() or st.in_round or st.begin_done:
                            return
                        if not self.focus():
                            return
                        self._retry_move(attempt)
                        time.sleep(0.1)
                        if st.in_round or st.begin_done:
                            return
                        WindowOperator.click()

        self._log(f"Begin {config.BEGIN_RETRY_MAX}回試行しましたがVerified未確認")

    # ── AFK防止ループ ─────────────────────────

    def do_open_special_round_loop(self):
        """
        ラウンド中60秒ごとに移動キーをわずかに押す（ジャンプ代替）。
        - フォーカス切り替えは SharedState._GLOBAL_ACTION_LOCK 内でのみ行う
          → 自爆・Begin操作中にフォーカスを奪わない
        - 停止条件: _running=False / in_round=False /
                    is_open_special_round_round=False / open_special_round_wins達成
        """
        st = self._st
        self._log(f"AFK解除ループ開始（{config.OPEN_SPECIAL_ROUND_INTERVAL_SEC}秒ごと）")
        elapsed = 0.0
        CHECK_INTERVAL = 1.0

        def _should_stop() -> bool:
            return (
                not self._is_running()
                or not st.in_round
                or not st.is_open_special_round_round
                or st.open_special_round_wins >= config.OPEN_SPECIAL_ROUND_TARGET_WINS
            )

        while not _should_stop():
            time.sleep(CHECK_INTERVAL)
            elapsed += CHECK_INTERVAL
            if elapsed < config.OPEN_SPECIAL_ROUND_INTERVAL_SEC:
                continue
            elapsed = 0.0
            if _should_stop():
                break
            if self.uses_osc:
                # OSCならフォーカス不要。他窓の操作を妨げない。
                self.move("forward", config.OPERATOR_WAIT_SEC)
            else:
                with SharedState._GLOBAL_ACTION_LOCK:
                    if _should_stop():
                        break
                    if not WindowOperator.focus_window(self._cfg.hwnd):
                        self._log("⚠ フォーカス取得失敗 → 今回のAFK解除をスキップ")
                        continue
                    WindowOperator.hold_key("w", config.OPERATOR_WAIT_SEC)
            self._log("移動キー送信（ジャンプ代替）")

        self._log("AFK解除ループ終了")
