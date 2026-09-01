import threading
import time
from typing import Callable

import config
import SharedState
import WindowOperator
import OSCClient
import OSCReceiver
import PlaySound
from State import WindowConfig, WindowState


def classify_speed(value: float) -> str:
    """速度からラウンド種別を返す。どれとも一致しなければ空文字。

    帯（6.45〜6.55なら8 Pages 等）では判定しない。平常時でも壁擦りや加速途中で
    6.5付近の値が出るため誤検知する。ここは定数との一致だけを見て、
    「一定値のまま変化していないか」の判断は受信側（stable_for）が持つ。
    """
    for kind, target in (("punish", config.SPEED_PUNISH),
                         ("8pages", config.SPEED_8PAGES),
                         ("normal", config.SPEED_NORMAL)):
        if abs(value - target) <= config.SPEED_MATCH_TOL:
            return kind
    return ""


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
        self._speed_recv_warned = False   # 速度未受信の警告を出したか
        # 受信の準備（bind）が済んだことを横移動側へ伝える。
        # VRChatは値が変わったときしか送らないので、bind前に動き出すと
        # 立ち上がりのサンプルを永久に取りこぼす。
        self._speed_ready = threading.Event()
        self._receiver = None             # 監視中ずっと生かす速度受信器

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
        # 他窓が装備待ち・続行ラウンド・速度検知フリーズ中は止まる
        # （自分が続行ラウンド中／自分がフリーズの発生源のときは除く）
        if not st.is_continue_round:
            while self._is_running() and st.in_round:
                eq_ok  = SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
                con_ok = SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                spd_ok = (st.speed_freeze_held
                          or SharedState.SPEED_FREEZE_EVENT.wait(timeout=1.0))
                # 自窓が張ったラウンド突入フリーズでは自爆を止めない
                rnd_ok = (st.round_freeze_held
                          or SharedState.ROUND_FREEZE_EVENT.wait(timeout=1.0))
                if eq_ok and con_ok and spd_ok and rnd_ok:
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
            if not st.speed_freeze_held and not SharedState.SPEED_FREEZE_EVENT.is_set():
                self._log("Begin キャンセル（速度検知フリーズを検出）")
                return False
            if not st.round_freeze_held and not SharedState.ROUND_FREEZE_EVENT.is_set():
                self._log("Begin キャンセル（ラウンド突入フリーズを検出）")
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
        if (st.is_continue_round or st.waiting_for_equip
                or st.speed_freeze_held or st.round_freeze_held):
            return self._is_running()
        while self._is_running():
            eq_ok  = SharedState.EQUIP_WAIT_EVENT.is_set()
            con_ok = SharedState.CONTINUE_ROUND_EVENT.is_set()
            spd_ok = SharedState.SPEED_FREEZE_EVENT.is_set()
            rnd_ok = SharedState.ROUND_FREEZE_EVENT.is_set()
            if eq_ok and con_ok and spd_ok and rnd_ok:
                return True
            if not eq_ok:
                self._log("他窓の装備待ち中 → フリーズ")
            if not con_ok:
                self._log("他窓の続行/霧ラウンド中 → フリーズ")
            if not spd_ok:
                self._log("他窓の速度検知フリーズ中 → フリーズ")
            if not rnd_ok:
                self._log("他窓のラウンド突入フリーズ中 → フリーズ")
            SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
            SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
            SharedState.SPEED_FREEZE_EVENT.wait(timeout=1.0)
            SharedState.ROUND_FREEZE_EVENT.wait(timeout=1.0)
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
                            and SharedState.CONTINUE_ROUND_EVENT.is_set()
                            and (st.speed_freeze_held
                                 or SharedState.SPEED_FREEZE_EVENT.is_set())
                            and (st.round_freeze_held
                                 or SharedState.ROUND_FREEZE_EVENT.is_set())):
                        break
                    SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                    if not st.speed_freeze_held:
                        SharedState.SPEED_FREEZE_EVENT.wait(timeout=1.0)
                    if not st.round_freeze_held:
                        SharedState.ROUND_FREEZE_EVENT.wait(timeout=1.0)
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
        ラウンド終了後: 待機 → [Begin前移動] → Beginクリック

        ロック戦略:
          - OSC窓: 移動はロック外（フォーカスを奪わない）、クリックだけロック内
          - 非OSC窓: 移動もキー入力なので全体をロック内で行う
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

        # ── フェーズ2: アイテムロスト装備待ち（ロック外）──
        # 装備済み（アイテム取得→Beginモードで先に装備確認済み）の場合は何もしない
        # （フリーズ解除はBEGIN_DONEイベント側で行う）
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

    # ── 速度によるラウンド種別の検知 ────────────
    #  判定（do_speed_detect）と横移動（do_speed_strafe）は独立している。
    #  判定は受信するだけなのでどのインスタンスでも動く。
    #  横移動はマクロなのでprivate系インスタンスでのみ動かす。

    def _speed_voice(self, kind: str) -> str:
        return {"8pages": self._cfg.voice_8pages,
                "punish": self._cfg.voice_punish}.get(kind, "")

    def start_velocity_receiver(self) -> bool:
        """速度受信を始める（監視開始時に1回だけ）。

        プローブのたびに bind/close すると開き直しの間の値を取りこぼす。
        VRChatは送信専用なのでポートを持ち続けても競合しない。
        """
        if not self._cfg.osc_port:
            self._speed_ready.set()   # 機能は使えないが待たせない
            return False
        if self._receiver is not None:
            self._speed_ready.set()
            return True
        receiver = OSCReceiver.VelocityReceiver(self._cfg.osc_port + 1, self._log)
        if not receiver.start():
            self._log("速度受信を開始できないため種別検知を無効化します")
            self._speed_ready.set()
            return False
        self._receiver = receiver
        self._speed_ready.set()
        return True

    def stop_velocity_receiver(self):
        """速度受信を止める（監視停止時）"""
        receiver, self._receiver = self._receiver, None
        self._speed_ready.clear()
        if receiver is not None:
            receiver.stop()

    def do_speed_detect(self):
        """Begin受理からラウンド開始までの移動速度でラウンド種別を判定する。

        ToNはラウンド種別で移動速度を変える。8 Pagesは横移動だけ 6.5、
        Punishedは前後左右すべてが 4.0 になる。水平速度の大きさを見るので、
        横移動でも前後移動でも Punished は 4.0 として拾える。

        こちらからは動かさない（受信のみ）。どのインスタンスでも動かしてよい。
        """
        st = self._st
        if not SharedState.get_speed_detect():
            return
        receiver = self._receiver
        if receiver is None:
            return
        round_seq = st.round_seq
        deadline = time.time() + config.SPEED_PROBE_TIMEOUT_SEC
        try:
            while (self._is_running() and not st.in_round
                   and st.round_seq == round_seq and time.time() <= deadline):
                self._sample_speed(receiver)
                time.sleep(0.05)
        finally:
            # 常時監視なので ever_received はセッションを通じて溜まる。
            # 一度でも受信できていれば以後この警告は出ない。
            if not receiver.ever_received and not self._speed_recv_warned:
                self._speed_recv_warned = True
                self._log("速度を受信できないため種別検知を無効化します"
                          "（アバターに VelocityMagnitude がありません）")

    def do_speed_strafe(self):
        """Begin受理後に横移動する（判定用の動きを作るマクロ）。

        右 → 左 の1往復だけ。繰り返さない。
        OSCなのでフォーカスもロックも要らず、他窓を妨げない。
        アイテムロスト後は動かさない（拾いに行く操作の邪魔をしないため）。
        """
        st = self._st
        if not SharedState.get_speed_detect() or not self._cfg.osc_port:
            return
        if st.waiting_for_equip:
            self._log("アイテムロスト後のため速度検知の横移動はしません")
            return
        # 受信のbindが済むまで待つ。待てなくても移動はする（受信が使えなくても
        # 横移動そのものは他を壊さない）。
        if not self._speed_ready.wait(timeout=config.SPEED_READY_TIMEOUT_SEC):
            self._log("速度受信の準備を待てませんでした（移動は行います）")
        round_seq = st.round_seq
        for direction, sec in (("right", config.SPEED_PROBE_RIGHT_SEC),
                               ("left", config.SPEED_PROBE_LEFT_SEC)):
            if not self._is_running() or st.in_round or st.round_seq != round_seq:
                return
            self.move(direction, sec)

    def _sample_speed(self, receiver):
        """速度を1つ拾って判定する。

        VelocityMagnitude は落下・ジャンプのY成分も含む3次元の大きさなので、
        接地していないサンプルは水平速度と一致しない。捨てる。
        """
        speed = receiver.stable_value
        if speed is None or receiver.stable_for < config.SPEED_STABLE_SEC:
            return
        if not receiver.grounded:
            return
        kind = classify_speed(speed)
        if kind and kind != self._st.speed_round_kind:
            self._st.speed_round_kind = kind
            self._announce_speed_kind(kind, speed)

    def _announce_speed_kind(self, kind: str, speed: float):
        """判定した種別を知らせる。平常時は鳴らさない（毎ラウンドは邪魔）。"""
        label = {"normal": "平常", "8pages": "8 Pages", "punish": "Punished"}.get(kind, kind)
        self._log(f"⏩ 速度{speed:.2f} → {label}")
        self._freeze_for_speed_kind(kind)
        if kind == "normal" or self._hands_free():
            return
        PlaySound.play_sound(self._speed_voice(kind))

    def _freeze_for_speed_kind(self, kind: str):
        """設定がONの種別なら全窓を止め、アイテムを取りに行く時間を作る。

        解除条件は種別で違う（8 Pages=アイテム取得 / Punished=ラウンド開始）ので、
        どちらで張ったかを覚えておく。平常では止めない。
        """
        st = self._st
        if kind == "8pages" and self._cfg.freeze_on_8pages:
            message = "⏸ 8 Pages 検知 → 全窓フリーズ（アイテム取得で解除）"
        elif kind == "punish" and self._cfg.freeze_on_punish:
            message = "⏸ Punished 検知 → 全窓フリーズ（ラウンド開始で解除）"
        else:
            return
        st.speed_freeze_kind = kind
        # 前面化するのは最初に張った窓だけ。後から張った窓が奪うと、
        # プレイヤーが操作している最中の窓を横取りしてしまう。
        first = SharedState.get_speed_freeze_count() == 0
        SharedState.speed_freeze_start(st)
        self._log(message)
        if first:
            self._focus_for_speed_freeze()

    def _focus_for_speed_freeze(self):
        """どの窓を操作すればよいか分かるように前面化する。

        付随機能なので、フォーカスを取れなくてもフリーズは維持する。
        既存の作法どおりロックを取ってから切り替える（数秒待たされてもよい）。
        """
        with SharedState._GLOBAL_ACTION_LOCK:
            if WindowOperator.focus_window(self._cfg.hwnd):
                self._log("この窓を前面化しました（速度検知フリーズ）")
            else:
                self._log("⚠ 前面化に失敗（フリーズは継続）")

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
