import threading
import time
from typing import Callable

import BeginDetect
import BeginAdjustShots
import BeginMiss
import config
import DebugLog
import FetchMiss
import ItemFetch
import ScreenCapture
import SharedState
import TaskStats
import WindowOperator
import OSCClient
import OSCReceiver
import PlaySound
import RespawnButton
import RunFace
import RunPos
import RoundDecision
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
# アイテムロストの窓が、ほかの窓のフリーズが解けるのを見張る間隔。解けた瞬間に
# 前面化＋音声を出すので、短い方が「続行が終わった直後」に近くなる
ITEM_LOSS_WATCH_SEC = 0.2
# アイテム取得で戻せなかった視点を戻しに行くとき、前面・フリーズを見直す間隔
VIEW_RESTORE_POLL_SEC = 0.2


def _width_ratio(hit) -> float | None:
    """文字の幅÷窓の高さ（w/H）。窓の高さが分からなければ None（前後は見ない）"""
    height = hit.get("H") if hit else None
    return hit["w"] / height if height else None


def depth_direction(ratio) -> str | None:
    """w/H から前後の向き: 押せる範囲（BEGIN_ADJUST_DEPTH_OK）より小さい（遠い）→ "forward"、
    大きい（近い）→ "back"、範囲内 → None"""
    if ratio is None:
        return None
    low, high = config.BEGIN_ADJUST_DEPTH_OK
    if ratio < low:
        return "forward"
    if ratio > high:
        return "back"
    return None


def depth_target(direction: str) -> float:
    """前後に合わせる先（秒数の計算にだけ使う。止まるのは押せる範囲に入ったとき）"""
    return (config.BEGIN_ADJUST_FORWARD_TARGET_W if direction == "forward"
            else config.BEGIN_ADJUST_BACK_TARGET_W)


def can_operate_in(instance_type: str) -> bool:
    """このインスタンスで OSC 操作・クリック操作（移動・マウス・カーソル・チェイス）をしてよいか。
    private（招待・招待+・フレンド・フレンド+）だけ（依頼者）。種類が分からない（入室前・
    ログに入室の行が無い）ときは今までの private の判定と同じく不可。Seer's Apostle の「グループ（メンバー
    のみ）」も private と同じ（FD）。前面化・窓の切り替え・音・判定・グループの自爆（キー）はこれを見ない"""
    return instance_type in config.PRIVATE_LIKE_INSTANCES


class ActionExecutor:
    # 検出器が使えないことは、アプリ起動中に1回だけ告げる
    _detector_unavailable_logged = False

    def __init__(
        self,
        cfg: WindowConfig,
        st: WindowState,
        is_running: Callable[[], bool],
        log: Callable[[str], None],
        auto_begin_active: Callable[[], bool] = None,
    ):
        self._cfg = cfg
        self._st = st
        self._is_running = is_running
        self._log = log
        # ツールが Begin を押している窓か。判定は LogMonitor が持つ（書き写さない）。
        # 渡されなければ「機能していない」＝速度検知の音声は鳴らす側
        self._auto_begin_active = auto_begin_active or (lambda: False)
        self._pending_view_dy = 0   # 戻せなかった縦の視点（次に前面にしたとき戻す）
        self._view_restore_thread = None    # それを裏で戻しに行くスレッド（_restore_view_soon）
        # アイテムロストの前面化＋音声を見張っているラウンド（二重に立てない）
        self._item_loss_watch_seq = -1
        # OSCが使える窓では移動をOSCで行う。フォーカスを奪わないので
        # 排他ロックが不要になり、他窓と並行して動ける。
        self._osc = OSCClient.OSCClient(cfg.osc_port) if cfg.osc_port else None
        self._chase_lock = threading.Lock()
        self._chase_dir = None            # 回っている向き（"cw" / "ccw"）。止まっていれば None
        self._chase_stop = None           # 送り直しのスレッドを止める合図
        self._chase_thread = None
        self._speed_recv_warned = False   # 速度未受信の警告を出したか
        # カーソル方式を見送った理由は、同じラウンドで同じものを出さない
        self._cursor_reason_round = -1
        self._cursor_reasons: set = set()
        self._spam_paused_round = -1      # 連打の休止を告げたラウンド
        self._begin_given_up_round = -1   # BEGIN が見つからずあきらめたラウンド
        self._begin_gave_up_round = -1    # 3 回押して受理されなかったラウンド（EV）
        self._give_up_retry_round = -1    # あきらめて、30 秒後の最初のやり直しに入ったラウンド（EV・FL）
        self._give_up_loop_round = -1     # あきらめた後のやり直しのループが回っているラウンド（FL。二重に回さない）
        self._give_up_retries = 0         # そのラウンドで、あきらめた後にやり直した回数（FL）
        self._fetched_round = -1          # アイテム取得で店へ行ったラウンド（EY: 押し直しは Begin の前へ戻ってから）
        self._last_press = "click"        # 直前の回で何で押したか（"dip" / "click"）
        self._dip_landed = False          # 直前の差し込みでカーソルを置けたか
        # 受信の準備（bind）が済んだことを横移動側へ伝える。
        # VRChatは値が変わったときしか送らないので、bind前に動き出すと
        # 立ち上がりのサンプルを永久に取りこぼす。
        self._speed_ready = threading.Event()
        self._receiver = None             # 監視中ずっと生かす速度受信器
        self._continue_rect = None        # 続行ラウンドの始まりの窓の矩形（窓を戻す先）
        # Run のリスポーン（後ろへ・正面へまで）が終わっているか。動き出すこの窓のほかの操作
        # （Begin 前の移動・アイテム取得・押し直し）は、終わるまで待つ
        self._run_respawn_idle = threading.Event()
        self._run_respawn_idle.set()
        self._noted_round_over_after_respawn = False   # debug.log に1回だけ
        # Run の位置合わせのためのデータ集め（EQ）。この監視（マクロの 1 回の開始）の間だけ覚える
        self._pos_rounds = 0              # 普通に終わったラウンドの数
        self._pos_ref_size = None         # 基準を撮った時のクライアントの大きさ（撮っていなければ None）
        self._plush_retries = 0           # 入室時に取れなかった Guidance Plush をやり直す残りの回数（ER）
        self._respawn_menu_open = False       # Run のリスポーンで Esc のメニューを開いたまま押しをまたぐか
        self._respawn_lobby = False           # ラウンドの外（店に行った後の押し直し。EY）でリスポーンしている間

    @property
    def uses_osc(self) -> bool:
        return self._osc is not None

    def can_operate(self) -> bool:
        """この窓で OSC 操作・クリック操作をしてよいか（can_operate_in）。送る入口は全部これを見る"""
        return can_operate_in(self._st.instance_type)

    # ── チェイス（押し続けて回る）──────────────────
    # 向き → (OSC のアドレス2つ, OSC が使えない窓のキー2つ)。Shift なし
    CHASE_INPUTS = {
        "cw": (("/input/MoveLeft", "/input/LookRight"), ("a", ".")),
        "ccw": (("/input/MoveRight", "/input/LookLeft"), ("d", ",")),
    }

    def chase_key(self, direction: str) -> str:
        """チェイスのキー。"start" / "stop"（同じ向き）/ "switch"（反対の向き）/
        "denied"（private でない。回っていれば止める）。

        どのスレッドから呼んでもよい（鍵）。前面を奪わないので
        _GLOBAL_ACTION_LOCK は取らない
        """
        with self._chase_lock:
            if not self.can_operate():
                if self._chase_dir is not None:
                    self._stop_chase_locked()
                return "denied"
            current = self._chase_dir
            if current == direction:
                self._stop_chase_locked()
                return "stop"
            if current is not None:
                self._stop_chase_locked()    # 古い2つを離してから新しい2つを押す
            self._start_chase_locked(direction)
            return "switch" if current is not None else "start"

    def chase_stop(self) -> bool:
        """回っていれば止める（使っていた2つだけ離す）。止めたら True"""
        with self._chase_lock:
            if self._chase_dir is None:
                return False
            self._stop_chase_locked()
            return True

    @property
    def chase_direction(self):
        return self._chase_dir

    def _start_chase_locked(self, direction: str):
        self._trace(f"[操作] チェイス開始 {direction}")
        stop = threading.Event()
        self._chase_dir = direction
        self._chase_stop = stop
        self._chase_thread = TaskStats.thread("_run_chase", self._run_chase, direction, stop, daemon=True)
        self._chase_thread.start()

    def _stop_chase_locked(self):
        self._trace("[操作] チェイス終了")
        stop, thread = self._chase_stop, self._chase_thread
        self._chase_dir = self._chase_stop = self._chase_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(config.CHASE_RESEND_SEC * 10)   # 離し終えてから次へ

    def _run_chase(self, direction: str, stop):
        addresses, keys = self.CHASE_INPUTS[direction]
        osc = self._osc
        if osc is not None:
            try:
                while self.can_operate():
                    for address in addresses:
                        osc.send(address, 1)
                    if stop.wait(config.CHASE_RESEND_SEC):
                        return
            finally:
                for address in addresses:       # stop_all() は使わない
                    osc.send(address, 0)
            # private でなくなった（インスタンスの移動）。入室の処理が止めている最中なら
            # そちらに任せる（鍵を待たない・2回言わない）
            if stop.is_set():
                return
            with self._chase_lock:
                if self._chase_stop is not stop:
                    return
                self._chase_dir = self._chase_stop = self._chase_thread = None
            self._log("チェイス停止（プライベートのインスタンスではなくなりました）")
            return
        if not WindowOperator.hold_keys_background(self._cfg.hwnd, keys, stop,
                                                   config.CHASE_RESEND_SEC):
            with self._chase_lock:
                if self._chase_stop is stop:
                    self._chase_dir = self._chase_stop = self._chase_thread = None
            self._log("⚠ チェイス: キーを送れません（最小化中など）→ 止めました")

    def _stopped(self) -> bool:
        """マクロが止められたか。押している最中の移動・長押しはこれでその場で離す"""
        return not self._is_running()

    def _move_stopped(self) -> bool:
        """移動を押している最中にやめるか。止めた・private でなくなった（自爆は見ない）"""
        return self._stopped() or not self.can_operate()

    def _trace(self, msg: str):
        """debug.log へ（公開ログには出さない）。窓の番号を付ける"""
        DebugLog.write(f"[窓{self._st.window_idx}] {msg}")

    def move(self, direction: str, seconds: float):
        """移動する。フォーカスは奪わない。

        OSCが使える窓はOSCで送る。使えない窓（手動起動の2窓目以降など）は
        背面へのキー送信（自爆と同じ経路）で送る。キーはメッセージでも
        読まれるので、前面化しなくても前進できる（WindowOperator.click() の
        docstring の実測を参照）。

        どちらもフォーカスを奪わないので、他窓の操作を妨げない。全窓フリーズ
        （装備待ち・続行ラウンド）中でも実行してよい。待ち合わせるのは
        フォーカスを要する操作（Beginのクリック）だけ。
        """
        if seconds <= 0:
            return
        if not self.can_operate():
            self._trace(f"[操作] 移動 {direction} しない（private でない）")
            return False
        self._trace(f"[操作] 移動 {direction} {seconds:.2f}秒（{'OSC' if self._osc is not None else 'キー'}）")
        if self._osc is not None:
            address = {"forward": "/input/MoveForward",
                       "back": "/input/MoveBackward",
                       "left": "/input/MoveLeft",
                       "right": "/input/MoveRight"}.get(direction)
            if address:
                ok = self._osc.press(address, seconds, stop=self._move_stopped)
                self._osc.stop_all(repeat=1)
                return ok is not False
        key = {"forward": "w", "back": "s", "left": "a", "right": "d"}.get(direction)
        if key and not WindowOperator.hold_key_background(
                self._cfg.hwnd, key, seconds, stop=self._move_stopped):
            self._log("⚠ 移動キーを送れませんでした（窓が最小化されている等）")
            return False
        return True

    def move_forward_left(self, forward_sec: float, left_sec: float):
        """前進しながら、その前半だけ左にも寄る（斜め → 直進）。

        MoveForward を通しで1回押し、MoveLeft を頭から left_sec だけ重ねる。
        加速と減速が各1回で済むので、逐次に「前進 → 左」と押すより誤差が小さい。

        逐次だと前進の減速が終わらないうちに左移動が始まり、残留速度が
        混ざって到達点が毎回ズレる。同時押しならその結合が起きない。

        OSCが使えない窓は背面へのキー送信になるため同時押しができない。
        その場合は従来どおり逐次で動かす（前進 → 左）。
        self._move_since ＝ (行の名前, time.time()) があれば、その行から押し始めるまでの遅れを debug.log の行に
        足す（EH。_begin_move が置く。1 回使ったら消す）
        """
        since, self._move_since = getattr(self, "_move_since", None), None
        if not self.can_operate():
            self._trace("[操作] 前進＋左 しない（private でない）")
            return False
        delay = f"・「{since[0]}」の行から {time.time() - since[1]:.2f} 秒" if since is not None else ""
        self._trace(f"[操作] 前進 {forward_sec:.2f}秒＋左 {left_sec:.2f}秒"
                    f"（{'OSC' if self._osc is not None else 'キー'}）{delay}")
        self._move_extension = None
        if self._osc is not None:
            if self._receiver is not None:
                ok = self._press_from_movement_start(forward_sec, left_sec)
            else:
                ok = self._osc.press_multi([("/input/MoveForward", forward_sec),
                                            ("/input/MoveLeft", left_sec)], stop=self._move_stopped)
            self._osc.stop_all(repeat=1)
            return ok is not False
        ok = self.move("forward", forward_sec) is not False
        return (self.move("left", left_sec) is not False) and ok

    def _press_from_movement_start(self, forward_sec: float, left_sec: float) -> bool:
        """前と左を同時に押し、押している時間を「動き出し」から数える（EF）。動き出しが遅れた（重い）
        ときに、遅れた分だけ距離が足りなくなっていた（10:22 窓4: 動き出し 0.34 秒・距離 10.58）。
        延びの上限は BEGIN_EXTEND_MAX_SEC。動き出すまで左は押したまま。結果は
        self._move_extension = (延び, どう決めたか, 押し直した回数)"""
        ok, extension, how, repressed = self._press_counted_from_start(
            [("/input/MoveForward", forward_sec), ("/input/MoveLeft", left_sec)], self._move_stopped,
            config.BEGIN_USUAL_START_SEC, config.BEGIN_EXTEND_MAX_SEC,
            lambda m: DebugLog.write(f"[操作] [窓{self._st.window_idx}] Begin前の移動: {m}"))
        self._move_extension = (extension, how, repressed)
        return ok

    def _press_counted_from_start(self, holds, stop, usual_start: float, extend_max: float, write):
        """holds（[(アドレス, 秒)]）を同時に押し、押している時間を「動き出し」（受信の速さが MOTION_START_SPEED を
        超えた時）から数える（EF・EH）。離すのは 最初の押し始め ＋ 秒 ＋ 延び。延び ＝ 動き出しまで（最初の押し始め
        から）− usual_start（0 未満にはしない＝今より早くは離さない）。上限は extend_max ＋ 押し直しで失った時間
        （上限は 1 回の押しの中の遅れにかける。押し直す前の押しは動かなかったので、その分は足す）。決まるまでは押したまま。
        速さを受け取れているのに BEGIN_START_WAIT_SEC 動き出さなければ、いったん離して（0 を送る）
        MOVE_REPRESS_GAP_SEC 後に押し直す（MOVE_REPRESS_MAX 回まで。EH: 12:23 窓4 は押した 2.08 秒の間 0 m）。
        それでも動かなければ延ばさない（最初の押し始めから数えた時間で離す）。速さを受け取れていない間は延ばさず、
        押し直さない（今どおり）。stop() が真なら離して止める。
        (送れた, 延び, どう決めたか, 押し直した回数) を返す。どう決めたか: "動き出し"・"動き出さない"・
        "受信なし"（一度も速さを受け取れなかった）・"止めた" """
        osc = self._osc
        ok = True
        for address, _sec in holds:
            ok = osc.send(address, 0) and ok        # 0→1 の変化で反応する入力があるため
        for address, _sec in holds:
            ok = osc.send(address, 1) and ok
        started = time.time()
        pressed_at = started        # 今の押し（押し直したらその時）の始め
        extension = None            # 決まった延び
        how = ""
        repressed = 0
        received = False
        released = set()
        try:
            while len(released) < len(holds):
                if stop():
                    how = "止めた"
                    return False, 0.0, how, repressed
                now = time.time()
                elapsed = now - started
                speed = self._receiver_speed()
                received = received or speed is not None
                cap = extend_max + (pressed_at - started)
                if extension is None and speed is not None:
                    if speed > MOTION_START_SPEED:
                        extension = min(cap, max(0.0, elapsed - usual_start))
                        how = "動き出し"
                    elif now - pressed_at >= config.BEGIN_START_WAIT_SEC:
                        if repressed < config.MOVE_REPRESS_MAX:
                            held = [a for a, _sec in holds if a not in released]
                            for address in held:
                                ok = osc.send(address, 0) and ok
                            time.sleep(config.MOVE_REPRESS_GAP_SEC)
                            if stop():
                                how = "止めた"
                                return False, 0.0, how, repressed
                            for address in held:
                                ok = osc.send(address, 1) and ok
                            repressed += 1
                            pressed_at = time.time()
                            continue
                        extension, how = 0.0, "動き出さない"
                        write(f"動き出さないので押し直した（{repressed} 回）・それでも動かない")
                # 決まるまでは、速さを受け取れていれば「今 動き出した」とみなして押し続ける
                pending = (min(cap, max(0.0, elapsed - usual_start))
                           if extension is None and speed is not None else 0.0)
                hold_more = extension if extension is not None else pending
                for address, sec in holds:
                    if address not in released and elapsed >= sec + hold_more:
                        ok = osc.send(address, 0) and ok
                        released.add(address)
                time.sleep(config.BEGIN_START_POLL_SEC)
        finally:
            for address, _sec in holds:
                if address not in released:
                    osc.send(address, 0)
        if extension is None:
            extension, how = 0.0, ("動き出さない" if received else "受信なし")
        return ok, extension, how, repressed

    def _receiver_speed(self):
        try:
            return self._receiver.speed if self._receiver is not None else None
        except Exception:
            return None

    # ── ヘルパー ──────────────────────────────

    def _hands_free(self) -> bool:
        """この窓で放置モードが効いているか（private系インスタンスのみ）"""
        return (SharedState.get_hands_free()
                and self._st.instance_type in config.PRIVATE_LIKE_INSTANCES)

    def announce_item_lost_once(self):
        """アイテムロスト音声を一度だけ再生する"""
        if self._hands_free():
            # 放置モード中は見ていないので鳴らさない
            return
        if self._st.item_lost_announced:
            return
        self._st.item_lost_announced = True
        PlaySound.play_sound(self._cfg.voice_item_lost, kind="item_lost")

    def _attend_to_item_loss(self):
        """アイテムロストの窓で、フリーズ・前面化・音声をまとめて出す。

        装備待ちフリーズは**待たずにすぐ張る**。前面化と音声だけが、ほかの窓の
        フリーズが解けるのを待つ。3つとも一緒に待つと、続行ラウンドのフリーズが
        解けてからこの窓が張るまでに隙間ができ、ほかの窓がそこで Begin へ走る。
        装備待ちどうしは張った順に1窓ずつ出す（_nothing_frozen_but_mine()）。

        前面化と音声は必ず一緒に出す（別々の場所で出すと片方だけ止まる場面が
        生まれる）。音声はここからしか鳴らさない。
        ほかの窓がフリーズ中なら別スレッドで見張り、解けた瞬間に出す。装備できた・
        ラウンドが始まった・止めた、で見張りをやめる。待ち始めたことはログに
        出さない（ログを増やさない）
        """
        st = self._st
        if self._hands_free():
            return
        if SharedState.get_item_begin_mode() and self._auto_begin_active():
            # アイテム取得→Begin モード: 列の先頭にいるあいだ、ほかの窓が返す
            # 前面の札を引き継ぐ（元の窓を一瞬挟まない）
            st.equip_front_hwnd = self._cfg.hwnd
        SharedState.equip_freeze_start(st)          # 待たずに張る
        if self._nothing_frozen_but_mine():
            self._show_item_loss()
            return
        if self._item_loss_watch_seq == st.round_seq:
            return                                  # もう見張っている
        self._item_loss_watch_seq = st.round_seq
        TaskStats.thread("_watch_item_loss", self._watch_item_loss, st.round_seq, daemon=True).start()

    def _watch_item_loss(self, round_seq: int):
        """ほかの窓のフリーズが解けるのを待って、前面化＋音声を出す"""
        st = self._st
        while self._is_running():
            if st.item_id or not st.waiting_for_equip:
                return                              # 装備できた
            if st.in_round or st.round_seq != round_seq:
                return                              # ラウンドが始まった
            if self._nothing_frozen_but_mine():
                self._show_item_loss()
                return
            time.sleep(ITEM_LOSS_WATCH_SEC)

    def _show_item_loss(self):
        """この窓を前面化し、同時に音声を鳴らす。借りた前面は装備待ちが解けたら返す"""
        with SharedState._GLOBAL_ACTION_LOCK:
            ok, loan = WindowOperator.borrow_front(self._cfg.hwnd)
            if ok:
                SharedState.keep_front_loan(self._st, loan)
                self._log("この窓を前面化しました（アイテム装備待ち）")
            else:
                self._log("⚠ 前面化に失敗（装備待ちは継続）")
        self.announce_item_lost_once()

    def _borrow_front(self) -> tuple:
        """この窓にフォーカスを当てる。(取れたか, 返すための札) を返す。

        フォーカスを取れないまま操作を送ると、別のウィンドウにキーや
        クリックが飛ぶため、呼び出し側は必ず取れたかを確認すること。
        """
        hwnd = self._cfg.hwnd
        ok, loan = WindowOperator.borrow_front(hwnd)
        if ok:
            self._log(f"フォーカス切替 → HWND={hwnd:#010x}")
            self._restore_pending_view()
        else:
            self._log(f"⚠ フォーカス取得失敗 HWND={hwnd:#010x} → 操作を中止")
        return ok, loan

    def _restore_view_soon(self):
        """アイテム取得で戻せなかった縦の視点を、裏で戻しに行く（1窓に1本だけ）。

        この窓が前面になったらその場で戻す。そうでなくても、ほかの窓がフリーズしていない
        （人がほかの窓を操作していない）なら、前面を一瞬借りて戻して返す。止めた・
        インスタンスが変わった・戻し終えた、で終わる"""
        if self._view_restore_thread is not None and self._view_restore_thread.is_alive():
            return
        self._view_restore_thread = TaskStats.thread("_restore_view_loop", self._restore_view_loop, daemon=True)
        self._view_restore_thread.start()

    def _restore_view_loop(self):
        st = self._st
        instance = st.instance_seq
        hwnd = self._cfg.hwnd
        while self._pending_view_dy and self._is_running() and st.instance_seq == instance:
            in_front = WindowOperator.foreground_hwnd() == hwnd
            if in_front or self._nobody_else_frozen():
                with SharedState._GLOBAL_ACTION_LOCK:
                    if not self._pending_view_dy or not self._is_running():
                        return
                    if WindowOperator.foreground_hwnd() == hwnd:
                        self._restore_pending_view()
                        return
                    ok, loan = WindowOperator.borrow_front(hwnd)
                    if ok:
                        try:
                            self._restore_pending_view()
                        finally:
                            WindowOperator.return_front(loan)
                        return
            time.sleep(VIEW_RESTORE_POLL_SEC)

    def _nobody_else_frozen(self) -> bool:
        """ほかの窓がどのフリーズも張っていないか（自分の分は数えない。装備待ちも1窓ずつ）。
        張っている窓は人が操作しているので、その間は前面を借りない"""
        st = self._st
        return all(self._freeze_ok(event, held, count, True) for event, held, count in (
            (SharedState.EQUIP_WAIT_EVENT, st.equip_freeze_held, SharedState.get_equip_freeze_count()),
            (SharedState.CONTINUE_ROUND_EVENT, st.continue_freeze_held,
             SharedState.get_continue_round_count()),
            (SharedState.SPEED_FREEZE_EVENT, st.speed_freeze_held, SharedState.get_speed_freeze_count()),
            (SharedState.ROUND_FREEZE_EVENT, st.round_freeze_held, SharedState.get_round_freeze_count()),
        ))

    def _restore_pending_view(self):
        """前に無くて戻せなかった縦の視点（アイテム取得）を、前面にした直後に何より先に戻す"""
        if not self.can_operate():
            return                  # マウスは送らない（残りは覚えたまま）
        dy, self._pending_view_dy = self._pending_view_dy, 0
        if not dy:
            return
        mouse = _FetchMouse()
        for sx, sy in ItemFetch.split_move(0, dy):
            mouse.move_rel(sx, sy)
            time.sleep(ItemFetch.STEP_SEC)
        DebugLog.write(f"[操作] [窓{self._st.window_idx}] アイテム取得: 残っていた縦の視点を戻した（縦 {dy}）")

    # ── 自爆 ──────────────────────────────────

    def do_skip(self):
        """自爆キーを背面で長押しする。

        前面の窓を切り替えないので、ロックもフリーズも要らない。ロックは
        「前面を切り替えてよいのは1窓だけ」、フリーズは「続行中の窓から
        フォーカスを奪わない」ためのもの。だから他窓の続行中・Begin 中・
        アイテム取得中でも、複数の窓が同時にでも自爆できる。

        フォーカス方式への落とし先は廃止した。ロックを取らずにそれが走ると、
        プレイ中の窓で自爆キーが押されうる。背面で送れなければ（最小化中・
        Shift併用キーなど）自爆せずに知らせる。
        """
        st = self._st
        if self._cfg.hwnd == 0:
            self._log("自爆キャンセル（HWND未選択）")
            return
        round_seq = st.round_seq
        if st.suicide_cancelled_round == round_seq:
            self._log("自爆キャンセル済み → このラウンドは自爆しません")
            return
        if st.suicide_seq == round_seq:
            return          # このラウンドの自爆はもう走っている。流れは1本
        st.suicide_seq = round_seq
        try:
            self._skip_until_dead(round_seq)
        finally:
            if st.suicide_seq == round_seq:
                st.suicide_seq = -1

    def _skip_until_dead(self, round_seq: int):
        """死ぬまで自爆する（最大 SUICIDE_RETRY_MAX 回）。

        長押しが終わってから少し待ち、死亡が来ていなければやり直す。
        やり直すたびに「まだやるべきか」を見直す。
        """
        st = self._st
        limit = config.SUICIDE_RETRY_MAX
        for attempt in range(1, limit + 1):
            if not self._should_skip(round_seq):
                return
            if attempt > 1:
                self._log(f"自爆が効いていません → やり直し（{attempt}/{limit}回目）")
            key = SharedState.get_suicide_key()
            st._skip_time = time.time()
            self._log(f"自爆実行中 ({config.SUICIDE_HOLD_SEC}秒・背面)…")
            if not WindowOperator.hold_key_background(
                    self._cfg.hwnd, key, config.SUICIDE_HOLD_SEC,
                    stop=lambda: self._suicide_cancelled(round_seq) or self._stopped()):
                # 送れないもの（最小化・Shift併用キー）はやり直しても送れない
                st._skip_time = 0.0
                self._log("⚠ 自爆できませんでした（窓が最小化されている等）")
                return
            if self._suicide_cancelled(round_seq):
                return              # 長押しの途中で離した。やり直さない
            if self._died_after_skip(round_seq):
                return
        if self._should_skip(round_seq):
            self._log(f"⚠ 自爆を{limit}回試しましたが死にませんでした")

    def _should_skip(self, round_seq: int) -> bool:
        """いま自爆してよいか。やり直すたびに見直す"""
        st = self._st
        if not self._is_running() or not st.in_round:
            return False
        if st.round_seq != round_seq:
            return False            # 次のラウンドになった
        if st.is_continue_round or st.died_this_round:
            return False
        if self._suicide_cancelled(round_seq):
            return False            # 自爆キャンセルのキー
        if st.waiting_for_equip:
            # 自窓のアイテムロスト待ち。他窓の待ちでは止まらない
            self._log("自爆キャンセル（アイテムロスト待ち中）")
            return False
        return True

    def _suicide_cancelled(self, round_seq: int) -> bool:
        return self._st.suicide_cancelled_round == round_seq

    def cancel_suicide(self) -> str | None:
        """自爆キャンセルのキー。ラウンドの中なら、このラウンドはもう自爆しない
        （長押し中ならその場で離す・やり直さない・後のきっかけでも始めない）。
        "stopped"＝自爆の途中だった、"marked"＝自爆していなかった、None＝ラウンド外（持ち越さない）"""
        st = self._st
        if not st.in_round:
            return None
        round_seq = st.round_seq
        st.suicide_cancelled_round = round_seq
        self._log("自爆キャンセル → このラウンドは自爆しません")
        return "stopped" if st.suicide_seq == round_seq else "marked"

    def _died_after_skip(self, round_seq: int) -> bool:
        """長押しが終わってから、死亡を少し待つ。死ねば True"""
        st = self._st
        deadline = time.time() + config.SUICIDE_CONFIRM_SEC
        while True:
            if st.died_this_round:
                return True
            if (time.time() >= deadline or not self._is_running()
                    or st.round_seq != round_seq or not st.in_round):
                return False
            time.sleep(0.1)

    def _confirm_begin(self, round_seq: int):
        """1回目を押した後。受理されなければ押し直す。1ラウンドで押すのは
        BEGIN_RETRY_MAX 回（1回目を含む）で終わり、その後に押し直しは続かない。

        差し込み → 位置合わせ → 差し込み → 前面化＋クリック
        （最初から前面化＋クリックの窓は クリック → 位置合わせ → クリック → クリック）。
        各回の手段は _press_begin() が決める。3回目は必ず前面化＋クリック。
        位置合わせは1回目と2回目の間に1回だけ。BEGIN が画面に無ければ、窓を前に
        出すだけにして、そのラウンドの Begin はあきらめる（利用者が直す）。
        Begin 前の移動はやり直さない。受理は本物の Verified（st.begin_done）で見る。
        """
        limit = config.BEGIN_RETRY_MAX
        for attempt in range(2, limit + 1):
            if self._accepted_after_press(round_seq):
                return
            if not self._should_retry_begin(round_seq):
                return
            if attempt == 2:
                if self._adjust_to_begin(round_seq) == "not_found":
                    self._show_window_without_begin(round_seq)
                    return
                if not self._should_retry_begin(round_seq):
                    return          # 位置合わせの間に受理された・止めた
            self._log(f"Begin が受理されていません → 押し直し（{attempt}/{limit}回目）")
            if not self._click_begin_again(round_seq, click_only=attempt == limit):
                return
        if not self._accepted_after_press(round_seq) and self._should_retry_begin(round_seq):
            self._log(f"⚠ Begin を{limit}回押しましたが受理されませんでした")
            self._begin_gave_up_round = round_seq      # do_after_round が 30 秒後に押し直す（EV）

    def _gave_up_begin(self, round_seq: int) -> bool:
        """このラウンドの Begin をあきらめたか（BEGIN が見つからない・BEGIN_RETRY_MAX 回押して受理されない）"""
        return round_seq in (self._begin_gave_up_round, self._begin_given_up_round)

    def _retry_after_giving_up(self, round_seq: int):
        """Begin をあきらめた窓のやり直し。受理・ラウンド開始・停止でやめる。

        3 回押して受理されなかった窓（EV）: BEGIN_GIVE_UP_RETRY_SEC おいて、押し直しの流れ
        （do_begin_again。位置合わせ・特徴点を含む）を 1 回。通らなければ BEGIN_GIVE_UP_REPEAT_SEC おきの繰り返しへ。
        BEGIN が見つからずあきらめた窓（FL）: 繰り返しの 1 回目（BEGIN_GIVE_UP_RETRY_SEC 後）から。
        繰り返しは 1 つのラウンドに 1 本だけ（押し直しの流れの中で、またあきらめても入れ子にしない）"""
        if self._give_up_loop_round == round_seq:
            return
        self._give_up_loop_round = round_seq
        try:
            first = True
            if self._give_up_retry_round != round_seq:
                self._give_up_retry_round = round_seq
                self._give_up_retries = 0
                if self._begin_gave_up_round == round_seq:
                    first = False
                    if not self._wait_give_up(round_seq, config.BEGIN_GIVE_UP_RETRY_SEC):
                        return
                    self._give_up_retries += 1
                    self._log(f"Begin をもう一度押します（あきらめてから {config.BEGIN_GIVE_UP_RETRY_SEC:.0f} 秒）")
                    self.do_begin_again(round_seq)
            else:
                first = False           # 30 秒後の分はこのラウンドでもう使った（受理と取り違えた後にまたあきらめた）
            self._retry_loop_after_giving_up(round_seq, first)
        finally:
            if self._give_up_loop_round == round_seq:
                self._give_up_loop_round = -1

    def _begin_retry_pending(self, round_seq: int) -> bool:
        """あきらめた後のやり直しを続けてよいか（あきらめた印は見ない。_should_retry_begin との違い）"""
        st = self._st
        return (self._is_running() and not st.in_round and not st.begin_done
                and st.round_seq == round_seq and self.can_operate())

    def _wait_give_up(self, round_seq: int, sec: float) -> bool:
        """sec 待つ（BEGIN_GIVE_UP_POLL_SEC ごとに続けてよいか見る）。待ち終えたら True、
        受理・ラウンド開始・停止などで続ける意味が無くなったら False"""
        deadline = time.time() + sec
        while time.time() < deadline:
            if not self._begin_retry_pending(round_seq):
                return False
            time.sleep(config.BEGIN_GIVE_UP_POLL_SEC)
        return self._begin_retry_pending(round_seq)

    def _retry_loop_after_giving_up(self, round_seq: int, first: bool):
        """あきらめた窓のやり直し（FL）: リスポーン → Begin 前の移動 → 押す（do_begin_again）を、1 回目は
        BEGIN_GIVE_UP_RETRY_SEC 後、それからは BEGIN_GIVE_UP_REPEAT_SEC おきに、通る・ラウンドが始まる・止めるまで
        繰り返す。回数の上限は無い（1 窓だとほかにラウンドを始める窓が無く、止まり続けるため）。
        他窓のフリーズ中は待つ（_wait_other_windows）。Run リスポーンの設定が OFF でもリスポーンする（EY と同じ）"""
        while True:
            wait = config.BEGIN_GIVE_UP_RETRY_SEC if first else config.BEGIN_GIVE_UP_REPEAT_SEC
            if not self._wait_give_up(round_seq, wait):
                return
            self._give_up_retries += 1
            if first:
                self._log(f"Begin: あきらめてから {config.BEGIN_GIVE_UP_RETRY_SEC:.0f} 秒 → "
                          "リスポーンして Begin 前からやり直します")
            else:
                self._log(f"Begin: {config.BEGIN_GIVE_UP_REPEAT_SEC / 60:.0f} 分おきにやり直します"
                          f"（{self._give_up_retries} 回目）")
            # あきらめた印は「次のやり直しまで」の印。戻さないと _should_retry_begin が偽のままで、押し直しが動かない
            self._begin_given_up_round = -1
            self._begin_gave_up_round = -1
            if not self._wait_other_windows():
                return
            if not self._begin_retry_pending(round_seq):
                return
            if not self._respawn_and_move_for_retry(round_seq):
                return
            self.do_begin_again(round_seq)
            first = False

    def _respawn_and_move_for_retry(self, round_seq: int) -> bool:
        """あきらめた後のやり直し（FL）: リスポーン → Begin 前の移動（_back_to_begin_after_fetch と同じ流れ）。
        リスポーンできなければ移動しない（押す所の位置合わせ・探すに任せる）。OSC が使えない窓はリスポーンできない
        ので、その場から押す。止めた・ラウンドが始まった・Begin が通った → False"""
        st = self._st
        if self._osc is None:
            self._log("Begin: OSC が使えない窓なのでリスポーンしません → その場から押します")
            return True
        self._respawn_lobby = True
        try:
            faced = self._respawn_and_face(round_seq, "Begin のやり直し")
        finally:
            self._respawn_lobby = False
        self._fetched_round = -1        # do_begin_again が店の後のリスポーンをもう一度しない
        if not self._is_running() or st.in_round or st.round_seq != round_seq or st.begin_done:
            return False
        if faced:
            if not self._begin_precheck(check_freeze=False):
                return False
            self._begin_move()
        else:
            self._log("Begin: リスポーンできなかった → その場から押します")
        return True

    def _accepted_after_press(self, round_seq: int) -> bool:
        """直前の回が受理されたか。差し込みは中で受理を待ち終えているので待たない。
        クリックの後は _begin_accepted() で待つ"""
        if self._last_press == "dip":
            return self._st.begin_done
        return self._begin_accepted(round_seq)

    def _begin_accepted(self, round_seq: int) -> bool:
        """受理を待つ。受理されたか、もう待つ意味が無くなったら返る"""
        st = self._st
        deadline = time.time() + config.BEGIN_RETRY_WAIT_SEC
        while not st.begin_done:
            if time.time() >= deadline or not self._should_retry_begin(round_seq):
                break
            time.sleep(0.1)
        return st.begin_done

    def _should_retry_begin(self, round_seq: int) -> bool:
        st = self._st
        return (self._is_running() and not st.in_round and not st.begin_done
                and st.round_seq == round_seq
                and self._begin_given_up_round != round_seq
                and self.can_operate())

    def _click_begin_again(self, round_seq: int, click_only: bool = False) -> bool:
        """押すところだけやり直す。他窓の解除を待ってから押す"""
        if not self._wait_other_windows():
            return False
        if not self._should_retry_begin(round_seq):
            return False
        if not self._begin_precheck():
            return False
        return self._press_begin(again=True, click_only=click_only)

    # ── Begin の位置合わせ（画像で BEGIN を探して横移動で照準に寄せる）──
    def _adjust_to_begin(self, round_seq: int) -> str:
        """照準（クライアント領域の中央）と BEGIN の文字のずれを、横移動と前後移動で詰める。

        横: 照準と文字の中心の横のずれ（文字の幅の何倍か）。前後: 文字の幅÷窓の高さ
        （w/H。押せる範囲 BEGIN_ADJUST_DEPTH_OK より小さい＝遠い→前へ、大きい＝近い→後ろへ）。
        横を先に合わせ、前後を動かした後に横がずれたら横をもう一度合わせる。どちらも
        1回目は少し動いて速さを測り（横は文字幅/秒、前後は w/H 毎秒）、2回目からは
        その速さで秒数を出す。回数・合計時間の上限は横と前後で別々に持つ。
        最初の撮影で BEGIN が無ければ、後ろ→前の順に探す（_search_for_begin）。動いた後に
        見失ったら、もう1回撮り直し、それでも無ければ探し直す（1回の位置合わせで1度だけ）。
        移動の直前と撮影の後に、受理・ラウンド開始・停止を見て、来ていればその場で終わる。

        "aimed": 許容内にした・動かずに済んだ・途中で打ち切った（BEGIN は見えていた）／
        "not_found": 探しても BEGIN が無い／"skip": 使えない・撮れない・中止。
        視点は回さない。_GLOBAL_ACTION_LOCK は取らない（撮影も移動も前面を奪わない）
        """
        # 各段の撮影を残す組（EW）。_look_for_begin が使う
        self._adjust_group = BeginAdjustShots.new_group(self._st.window_idx)
        self._adjust_index = 0
        try:
            return self._adjust_to_begin_inner(round_seq)
        finally:
            self._adjust_group = None

    def _adjust_to_begin_inner(self, round_seq: int) -> str:
        """_adjust_to_begin の中身（撮影の組を作った後）"""
        if not BeginDetect.available():
            if not ActionExecutor._detector_unavailable_logged:
                ActionExecutor._detector_unavailable_logged = True
                self._log("Begin: 画像での位置合わせは使えません（OpenCV か見本が読めません）")
            return "skip"
        if self._adjust_stopped(round_seq):
            return "skip"
        seen = self._look_for_begin("最初")
        if seen is None:
            return "skip"
        hit, aim_x = seen
        if hit is None:
            seen = self._search_for_begin(round_seq)
            if seen == "skip":
                return "skip"
            if seen is None:
                return "not_found"
            hit, aim_x = seen
        researched = False      # 見失って探し直したか（1度だけ）
        x_rate = None           # 横の速さ（文字幅/秒）
        d_rate = None           # 前後の速さ（w/H 毎秒）
        x_total = d_total = 0.0
        x_steps = d_steps = 0
        while True:
            dx = hit["cx"] - aim_x
            x_ok = abs(dx) <= hit["w"] * config.BEGIN_ADJUST_TOL
            ratio = _width_ratio(hit)
            depth = depth_direction(ratio)
            if x_ok and depth is None:
                if x_steps or d_steps:
                    self._log(f"Begin: 位置合わせ済み（横 {dx:+.0f}px"
                              + (f"・幅 {ratio:.3f}" if d_steps else "") + "）")
                else:
                    self._log(f"Begin: BEGIN は照準の上にあります（横 {dx:+.0f}px）"
                              "→ そのまま2回目を押します")
                return "aimed"
            move = None
            if not x_ok:
                sec = (config.BEGIN_ADJUST_PROBE_SEC if x_rate is None
                       else abs(dx) / hit["w"] / x_rate)
                sec = min(max(sec, config.BEGIN_ADJUST_MIN_SEC), config.BEGIN_ADJUST_MAX_SEC)
                sec = min(sec, config.BEGIN_ADJUST_MAX_TOTAL_SEC - x_total)
                if x_steps < config.BEGIN_ADJUST_MAX_STEPS and sec >= config.BEGIN_ADJUST_MIN_SEC:
                    move = "x"
            if move is None and depth is not None:
                sec = (config.BEGIN_ADJUST_DEPTH_PROBE_SEC if d_rate is None
                       else abs(ratio - depth_target(depth)) / d_rate)
                sec = min(max(sec, config.BEGIN_ADJUST_DEPTH_MIN_SEC), config.BEGIN_ADJUST_DEPTH_MAX_SEC)
                sec = min(sec, config.BEGIN_ADJUST_DEPTH_MAX_TOTAL_SEC - d_total)
                if (d_steps < config.BEGIN_ADJUST_DEPTH_MAX_STEPS
                        and sec >= config.BEGIN_ADJUST_DEPTH_MIN_SEC):
                    move = "depth"
            if move is None:
                return self._adjust_gave_up(
                    f"横 {x_steps}回 {x_total:.2f}秒・前後 {d_steps}回 {d_total:.2f}秒動いた。"
                    f"横 {dx:+.0f}px" + (f"・幅 {ratio:.3f}" if ratio is not None else ""))
            if self._adjust_stopped(round_seq):
                return "skip"
            if move == "x":
                direction, label = ("right", "右") if dx > 0 else ("left", "左")
                self._log(f"Begin: 位置合わせ（照準から横 {dx:+.0f}px）→ {label}へ {sec:.2f}秒")
                x_total += sec
                x_steps += 1
            else:
                direction, label = ("forward", "前") if depth == "forward" else ("back", "後ろ")
                self._log(f"Begin: 位置合わせ（前後: 幅 {ratio:.3f} → 目標 "
                          f"{depth_target(depth):.3f}）→ {label}へ {sec:.2f}秒")
                d_total += sec
                d_steps += 1
            self.move(direction, sec)
            time.sleep(config.BEGIN_ADJUST_SETTLE_SEC)
            if self._adjust_stopped(round_seq):
                return "skip"
            seen = self._look_for_begin("動いた後")
            if self._adjust_stopped(round_seq):
                return "skip"
            if seen is None or seen[0] is None:
                # 見失った: 同じ位置でもう1回撮る。それでも無ければ探し直す（1度だけ）
                seen = self._look_for_begin("撮り直し")
                if self._adjust_stopped(round_seq):
                    return "skip"
                if seen is None or seen[0] is None:
                    if researched:
                        return self._adjust_gave_up("BEGIN を見失った")
                    researched = True
                    self._log("Begin: BEGIN を見失いました → 探し直します")
                    seen = self._search_for_begin(round_seq)
                    if seen == "skip":
                        return "skip"
                    if seen is None:
                        return "not_found"
                    hit, aim_x = seen       # 探し直した位置から合わせ直す（速さは測り直す）
                    x_rate = d_rate = None
                    continue
            new_hit, aim_x = seen
            if move == "x":
                new_dx = new_hit["cx"] - aim_x
                moved = abs(dx - new_dx)
                if moved <= config.BEGIN_ADJUST_MIN_MOVE_PX:
                    return self._adjust_gave_up(f"動いていない（{moved:.0f}px）")
                x_rate = abs(dx / hit["w"] - new_dx / new_hit["w"]) / sec
            else:
                new_ratio = _width_ratio(new_hit)
                change = abs(new_ratio - ratio) if new_ratio is not None else 0.0
                if change <= config.BEGIN_ADJUST_DEPTH_MIN_CHANGE:
                    return self._adjust_gave_up(f"前後に動いていない（幅 {ratio:.3f}）")
                d_rate = change / sec
            hit = new_hit

    def _search_for_begin(self, round_seq: int):
        """BEGIN が見えない。後ろへ（近すぎる）→ 戻して前へ（遠すぎる）の順に探す。
        見つかれば (文字, 照準の x)、見つからなければ None（必要なら動いた分を戻す）。
        各移動の直前と撮影の後に受理・ラウンド開始・停止を見て、来ていれば「skip」（戻しもしない）。

        前への試しは、押した秒数でなく速さの受信（MotionSampler.distance_so_far。Begin 前の移動の実測と同じ積分）で
        実際に動いた量を測る（FM）。前へ BEGIN_SEARCH_FORWARD_SEC 秒押して BEGIN_SEARCH_BLOCKED_DIST 未満しか
        動けなければ壁（台座など）で、残りの前への試しをやめ、代わりに後ろへ BEGIN_SEARCH_BLOCKED_BACK_SEC 秒
        動いて見る（それで無ければあきらめる。さらに戻さない）。壁でなく見つからなければ、最後に後ろへ戻すのは
        前へ動けた量だけ（BEGIN_SEARCH_RETURN_MIN 未満なら戻さない）。速さを受け取れない窓は今までどおり
        押した秒数で戻す。最後に実測を debug.log へ 1 行"""
        sampler = None
        if self._receiver is not None and self._osc is not None:
            # 探す動きの間ずっと速さを読む。MotionSampler の実測の行はここでは書かない（下で自分の行を書く）
            sampler = MotionSampler(self._receiver, lambda m: None)
            sampler.start()
        stats = {"pushes": 0, "push_sec": 0.0, "moved": 0.0, "blocked": 0,
                 "returned": None, "wall_back": None}
        try:
            return self._search_for_begin_moves(
                round_seq, sampler.distance_so_far if sampler is not None else None, stats)
        finally:
            if sampler is not None:
                sampler.release()
            if stats["pushes"]:
                returned = stats["returned"]
                wall = stats["wall_back"]
                self._log(
                    f"Begin: 探す動きの実測: 前へ {stats['pushes']} 回・押した {stats['push_sec']:.2f}秒・"
                    + ("動いた量は測れず（速さを受信していません）" if sampler is None
                       else f"動いた {stats['moved']:.2f}")
                    + f"・動けなかった {stats['blocked']} 回"
                    + (f"・後ろへ戻した {returned[0]:.2f}秒で {returned[1]:.2f}" if returned is not None else "")
                    + (f"・壁で後ろへ {wall[0]:.2f}秒動いた {wall[1]:.2f}" if wall is not None else ""))

    def _search_for_begin_moves(self, round_seq: int, distance, stats: dict):
        """_search_for_begin の動き。distance は「今までに動いた距離」を返す関数（速さを受け取れない窓は None）。
        stats に実測（前へ押した回数と秒数・動いた量・動けなかった回数・戻した秒数と量・壁で後ろへ動いた秒数と量）を入れる"""
        stopped = lambda: self._adjust_stopped(round_seq)

        def look():
            time.sleep(config.BEGIN_ADJUST_SETTLE_SEC)
            if stopped():
                return "skip"
            seen = self._look_for_begin("探す")
            if stopped():
                return "skip"
            return seen if seen is not None and seen[0] is not None else None

        back = config.BEGIN_SEARCH_BACK_SEC
        if stopped():
            return "skip"
        self._log(f"Begin: BEGIN が見つかりません → 後ろへ {back:.2f}秒動いて探します")
        self.move("back", back)
        seen = look()
        if seen is not None:
            return seen
        if stopped():
            return "skip"
        self.move("forward", back)                  # 戻す
        measured = False                            # 前へ動いた量を 1 回でも測れたか
        for i in range(config.BEGIN_SEARCH_FORWARD_TRIES):
            sec = config.BEGIN_SEARCH_FORWARD_SEC
            if stopped():
                return "skip"
            self._log(f"Begin: BEGIN が見つかりません → 前へ {sec:.2f}秒動いて探します"
                      f"（{i + 1}/{config.BEGIN_SEARCH_FORWARD_TRIES}）")
            before = distance() if distance is not None else None
            self.move("forward", sec)
            stats["pushes"] += 1
            stats["push_sec"] += sec
            moved = None                            # この押しで動いた量（測れなければ None）
            after = distance() if before is not None else None
            if after is not None:
                moved = after - before
                measured = True
                stats["moved"] += moved
            seen = look()
            if seen is not None:
                return seen
            if moved is not None and moved < config.BEGIN_SEARCH_BLOCKED_DIST:
                # 壁（台座など）か、途中で速さが届かなくなった（区別がつかない。押しても同じなので同じに扱う）
                stats["blocked"] += 1
                wall_sec = config.BEGIN_SEARCH_BLOCKED_BACK_SEC
                self._log(f"Begin: 前へ動けない（{sec:.2f}秒押して {moved:.2f} しか動かない。壁か、速さが届いていない）"
                          f"→ 前へ探すのをやめて、後ろへ {wall_sec:.2f}秒動いて探します")
                if stopped():
                    return "skip"
                wall_before = distance()
                self.move("back", wall_sec)
                wall_after = distance()
                stats["wall_back"] = (wall_sec, (wall_after - wall_before)
                                      if wall_before is not None and wall_after is not None else 0.0)
                seen = look()
                if seen is not None:
                    return seen
                self._log("Begin: 後ろへ動いても BEGIN が見つかりません → あきらめます（さらに戻しません）")
                return None
        if stopped():
            return "skip"
        pushed = stats["push_sec"]
        if not measured:
            self._log(f"Begin: 探しても BEGIN が見つかりません → 後ろへ {pushed:.2f}秒戻します")
            self.move("back", pushed)               # 動いた量を測れない窓は、押した秒数で戻す
            return None
        moved_total = stats["moved"]
        if moved_total < config.BEGIN_SEARCH_RETURN_MIN:
            self._log(f"Begin: 探しても BEGIN が見つかりません。前へ {moved_total:.2f} しか動けていないので戻しません")
            return None
        self._log(f"Begin: 探しても BEGIN が見つかりません → 前へ動いた {moved_total:.2f} だけ後ろへ戻します"
                  f"（上限 {pushed:.2f}秒）")
        stats["returned"] = self._press_back_distance(moved_total, pushed, distance)
        return None

    def _press_back_distance(self, target: float, cap_sec: float, distance,
                             clock=time.time, sleep=time.sleep):
        """後ろへ、動いた距離（distance()。MotionSampler.distance_so_far）が target になるまで押す（FM）。
        MOTION_INTERVAL_SEC ごとに見て、cap_sec 秒たったら離す。止めた・private でなくなったらすぐ離す。
        (押した秒数, 動いた量) を返す"""
        if not self.can_operate():
            self._trace("[操作] 距離を決めた後ろへの移動 しない（private でない）")
            return 0.0, 0.0
        address = "/input/MoveBackward"
        osc = self._osc
        start = distance()
        self._trace(f"[操作] 移動 back 距離 {target:.2f} まで（上限 {cap_sec:.2f}秒・OSC）")
        osc.send(address, 0)                        # 0→1 の変化で反応する入力があるため
        osc.send(address, 1)
        began = last_sent = clock()
        moved = 0.0
        try:
            while True:
                now = clock()
                here = distance()
                if start is not None and here is not None:
                    moved = here - start
                if moved >= target or now - began >= cap_sec or self._move_stopped():
                    break
                if now - last_sent >= config.OSC_HOLD_RESEND_SEC:
                    osc.send(address, 1)            # 途中で入力が消えても戻るように送り直す
                    last_sent = now
                sleep(MOTION_INTERVAL_SEC)
        finally:
            osc.send(address, 0)
            osc.stop_all(repeat=1)
        return clock() - began, moved

    def _adjust_gave_up(self, why: str) -> str:
        self._log(f"Begin: 位置合わせを打ち切り（{why}）→ 2回目を押します")
        return "aimed"

    def _adjust_stopped(self, round_seq: int) -> bool:
        st = self._st
        return (not self._is_running() or st.in_round or st.round_seq != round_seq
                or st.begin_done)

    def _look_for_begin(self, stage: str = ""):
        """撮って BEGIN を探す。(見つけた文字 or None, 照準の x)。撮れなければ None。
        見つからなかった撮影は窓ごとに最新2枚を残す（stage はどの段の撮影か）"""
        hwnd = self._cfg.hwnd
        aim = WindowOperator.aim_in_window_image(hwnd)
        if aim is None:
            return None
        bits, w, h = ScreenCapture.capture_window(hwnd)
        if not bits or w <= 0 or h <= 0:
            return None
        hit = BeginDetect.find(bits, w, h)
        if hit is not None:
            if hit.get("rule") == "features":
                # 斜めから見た天板を特徴点で見つけた（ET。あとで見直せるように）
                DebugLog.write(f"[窓{self._st.window_idx}] Begin: 斜めの BEGIN を特徴点で見つけた"
                               f"（{stage or '位置合わせ'}・中心 ({hit['cx']:.0f}, {hit['cy']:.0f})・"
                               f"幅 {hit['w']:.0f}・インライア {hit['inliers']}）")
            hit = {**hit, "H": h}           # 前後は文字の幅÷窓の高さで見る
        else:
            BeginMiss.save(self._st.window_idx, bits, w, h, stage or "位置合わせ")
        self._keep_adjust_shot(stage, bits, w, h, hit, aim)
        return hit, aim[0]

    def _keep_adjust_shot(self, stage, bits, w, h, hit, aim):
        """位置合わせの各段の撮影を、見つかった・見つからなかったに関係なく残す（EW）。位置合わせの外では残さない"""
        group = getattr(self, "_adjust_group", None)
        if not group:
            return
        self._adjust_index = getattr(self, "_adjust_index", 0) + 1
        BeginAdjustShots.save(group, self._adjust_index, stage or "位置合わせ", bits, w, h, hit, aim)

    def _show_window_without_begin(self, round_seq: int):
        """BEGIN が画面に無い。クリックしても当たらないので、窓を前に出すだけにして
        そのラウンドの Begin はあきらめる。元の窓へは返さない（利用者が直す場面
        なので、どの窓か分かるように前に残す）。音声は出さない"""
        self._begin_given_up_round = round_seq
        self._log("Begin: 画面に BEGIN が見つかりません → 窓を前に出すだけにします（クリックしない）")
        if not self._wait_other_windows():
            return
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or self._st.in_round:
                return
            WindowOperator.focus_vrchat(self._cfg.hwnd)

    def _vrchat_is_in_front(self) -> bool:
        """VRChat の窓が前面か。前面ならカーソルを触らずフォールバックする。

        速さのためだけではない。前面の VRChat はマウスを掴んでいるので、こちらの
        SetCursorPos がその窓から見て「マウスを動かされた」＝カメラが回ることが
        ある。操作中の窓の照準が Begin から外れかねないので、**触る前に**判定して
        避ける。前面のときは、そもそもほかの窓へカーソルを持って行けない（実測）。

        その窓自身が前面のときも同じ扱いにする。「前面なら連打だけで押せるはず」は
        確かめられていないので前提にしない。フォールバックの focus() は既に前面なら
        何もしないので、クリックだけが走る。
        """
        front = WindowOperator.foreground_hwnd()
        if front and front in SharedState.managed_hwnds():
            self._log("Begin: VRChatが前面なのでカーソルを使いません")
            return True
        return False

    def _begin_by_cursor(self) -> bool:
        """カーソルを使う新方式を使ってよい窓か。OSCが使えるなら使う。

        持ち物では分岐しない。st.item_id はアイテムショップで買って装備して
        いるか（`Equipping <id>.`）であって、手に持っているかではない。手に
        持つ／離すは `[Behaviour] Pickup object:` / `[Behaviour] Drop object:`
        で出るが、こちらは読んでいない。

        2026-09-21 に Emerald Coil が使われたのは手に持っていたときの話で、
        ショップの装備の有無とは関係が無い。ロビーで拾い物を手に持っている
        ことは無い想定なので、判定せずに背面で押す（依頼者の判断）。

        以前ここで st.item_id を見ていたため、ショップでアイテムを買っている
        窓では /input/UseRight が1発も送られていなかった。
        """
        return bool(self.uses_osc and config.BEGIN_BY_CURSOR)

    def _start_use_spam(self, round_seq: int):
        """UseRight の連打を始める（カーソルは動かさない）。

        RoundOver + BEGIN_USE_SPAM_START_SEC から送り始める。Verified Round End
        が出た時点でもう押せるので、その瞬間にカーソルを一瞬差し込むだけで
        Begin が押される。スレッドは、押せた・停止・次のラウンドが始まった、
        のいずれかで自分から終わる
        """
        if not self._begin_by_cursor() or not self.can_operate():
            return None
        stop = threading.Event()
        self._trace("[操作] UseRight の連打を開始")
        thread = TaskStats.thread("_spam_use_right", self._spam_use_right, stop, round_seq, daemon=True)
        thread.start()
        return stop

    def _spam_use_right(self, stop, round_seq: int):
        try:
            self._spam_use_right_loop(stop, round_seq)
        finally:
            self._trace("[操作] UseRight の連打を終了")

    def _spam_use_right_loop(self, stop, round_seq: int):
        st = self._st
        start = (st.round_over_time or time.time()) + config.BEGIN_USE_SPAM_START_SEC
        reach_at, reach = 0.0, False    # 連打で押せる状態か（前面か、カーソルがこの窓の上）
        while not stop.is_set():
            if (not self._is_running() or st.begin_done or st.in_round
                    or st.round_seq != round_seq or not self.can_operate()):
                return
            if time.time() < start:
                stop.wait(0.1)
                continue
            if not all(self._freezes_ok()):
                # 押す見込みが無い間は送らない。飛んでいる UseRight は、利用者の
                # カーソルがその窓へ来た瞬間に Begin を押してしまう（固定が
                # 外れる状態が実在する）。通数も毎秒60通×窓数あるので止める。
                # スレッドは終わらせない——解ければそのまま送り始める
                self._log_spam_paused()
                stop.wait(config.BEGIN_USE_PULSE_SEC)
                continue
            now = time.time()
            if now - reach_at >= config.BEGIN_USE_REACH_CHECK_SEC:
                reach_at, reach = now, self._use_right_reaches_begin()
            if reach:
                # この窓が前面か、カーソルがこの窓の上: 連打の UseRight で Begin が押される。
                # 背面でカーソルも外なら記録しない（その間に来た定期を受理しないため）
                st.last_begin_press_at = now
            self._osc.press("/input/UseRight", config.BEGIN_USE_PULSE_SEC)
            stop.wait(config.BEGIN_USE_PULSE_SEC)

    def _use_right_reaches_begin(self) -> bool:
        """UseRight がこの窓の Begin に届く状態か（前面か、カーソルがクライアント領域の上）"""
        hwnd = self._cfg.hwnd
        return (WindowOperator.foreground_hwnd() == hwnd
                or WindowOperator.cursor_in_client(hwnd))

    def _dip_cursor_for_begin(self, tail: str) -> bool:
        """カーソルをその窓へ一瞬だけ置き、受理を待つ。押せたら True。

        「差し込み → 受理を待つ」を BEGIN_CURSOR_DIPS 回まで試す（1回目＋やり直し）。
        やり直すのは一時的な失敗だけ: 受理が来なかった・SetCursorPos が効かなかった・
        読み返しがずれた（利用者の手がちょうどマウスを動かした瞬間など）。
        最小化・クライアント領域が0・画面外は何度やっても同じなので、その場で
        False を返し、呼び出し側が従来方式（前面化＋クリック）へ落とす。

        差し込めたら、答え（Verified）が来るまで待つ。往復は0.05秒で終わるのに
        受理はその後に来るので、待たずに判定すると押せていても必ず
        「押せなかった」ことになり、毎ラウンド前面を奪っていた。
        連打は _start_use_spam() のスレッドが送り続けている。
        """
        st = self._st
        round_seq = st.round_seq
        self._dip_landed = False        # 1度でもカーソルを置けたか（_press_begin が見る）
        for attempt in range(config.BEGIN_CURSOR_DIPS):
            if st.begin_done:
                return True             # 間に受理されていた
            if (not self._is_running() or st.in_round
                    or st.round_seq != round_seq or not self.can_operate()):
                return False
            if attempt:
                # 1回目との間に VRChat が前面になっていたら、カーソルに触らない
                # （前面の窓はマウスを掴んでいて、SetCursorPos でカメラが回る）
                if self._vrchat_is_in_front():
                    return False
                self._log(f"Begin: カーソルをもう一度合わせる{tail}")
            with SharedState._GLOBAL_ACTION_LOCK:
                with WindowOperator.cursor_over_window(
                        self._cfg.hwnd, self._log_cursor_reason) as over:
                    if over:
                        st.last_begin_press_at = time.time()   # 置けた瞬間に（dwell の間に届く）
                        if not attempt:
                            self._log(f"Begin: カーソルを一瞬合わせる{tail}")
                        time.sleep(config.BEGIN_CURSOR_DWELL_SEC)
            if not over:
                if WindowOperator.cursor_target(self._cfg.hwnd)[0] is None:
                    return False        # 点が出せない（最小化・画面外など）
                time.sleep(config.BEGIN_CURSOR_GAP_SEC)
                continue
            self._dip_landed = True
            st.last_begin_press_at = time.time()   # カーソルを置けた＝押した
            if self._wait_begin_accepted(round_seq):
                return True
        return False

    def _wait_begin_accepted(self, round_seq: int) -> bool:
        """受理（Verified）を BEGIN_RETRY_WAIT_SEC まで待つ。来なければ False。

        来なければ呼び出し側が従来方式（前面化＋クリック）へ落とす
        """
        st = self._st
        deadline = time.time() + config.BEGIN_RETRY_WAIT_SEC
        while not st.begin_done:
            if (time.time() >= deadline or not self._is_running()
                    or st.in_round or st.round_seq != round_seq):
                return False
            time.sleep(0.05)
        return True

    def _log_spam_paused(self):
        """連打を休めていることを1ラウンド1回だけ出す（0.05秒ごとに出すと埋まる）"""
        st = self._st
        if self._spam_paused_round == st.round_seq:
            return
        self._spam_paused_round = st.round_seq
        self._log("Begin: 他窓のフリーズ中なので連打を止めています")

    def _log_cursor_reason(self, reason: str):
        """カーソル方式を見送った理由を出す。同じラウンドで同じ理由は1回だけ"""
        st = self._st
        if self._cursor_reason_round != st.round_seq:
            self._cursor_reason_round = st.round_seq
            self._cursor_reasons = set()
        if reason in self._cursor_reasons:
            return
        self._cursor_reasons.add(reason)
        self._log(f"Begin: {reason}")

    def _press_begin(self, again: bool = False, click_only: bool = False) -> bool:
        """Begin を1回押す。押した（差し込んだ・クリックした）ら True。

        1回で使う手段は1つ: 差し込めたら（受理が来なくても）この回はそこまで。
        差し込みが「置けない」（1度もカーソルを置けなかった）で終わった回だけ、
        その回のうちに前面化＋クリックで押す。click_only は3回目（必ず前面化＋クリック）。
        何で押したかは self._last_press に残す（"dip" / "click"）。

        OSCが使える窓は、連打している /input/UseRight に合わせて、カーソルを
        Begin のボタンの上（＝照準＝クライアント領域の中央）へ一瞬だけ置く。
        矩形の中ならどこでもよいわけではない（WindowOperator.click() 参照）。
        前面化しないので他窓の前面を奪わず、カーソルを奪う時間も 0.05 秒ずつで
        済む。最小化などでカーソルを置けない窓・OSCが使えない窓は、従来どおり
        前面化＋クリック。

        全窓共通のロックは、カーソルを動かしている間・フォーカスを取っている
        間だけ取る（窓どうしでカーソルと前面を取り合わないため）。
        """
        st = self._st
        tail = "（押し直し）" if again else ""
        round_seq = st.round_seq
        self._last_press = "click"
        if not self.can_operate():
            return False
        if (not click_only and self._begin_by_cursor()
                and not self._vrchat_is_in_front()):
            stop = self._start_use_spam(st.round_seq) if again else None
            try:
                if self._dip_cursor_for_begin(tail):
                    self._last_press = "dip"
                    return True
            finally:
                if stop is not None:
                    stop.set()
            if self._dip_landed:
                # 差し込めた（受理は来なかった）。この回はここまで。止めた・
                # ラウンドが始まった・変わったなら、押していない扱い
                self._last_press = "dip"
                return (self._is_running() and not st.in_round
                        and st.round_seq == round_seq)
        if st.begin_done:
            # 差し込みで押せていた。ロック待ちにも入らない
            self._log("Begin: 受理されたので前面化しません")
            return True
        with SharedState._GLOBAL_ACTION_LOCK:
            if st.begin_done:
                # 6窓では他窓のクリックを待つ間に受理が届く。ここで気づかないと
                # 受理済みの Begin をもう一度押して前面を奪う
                self._log("Begin: 受理されたので前面化しません")
                return True
            if not self._is_running() or st.in_round or not self.can_operate():
                return False
            ok, loan = self._borrow_front()
            if not ok:
                return False
            self._log(f"Beginクリック{tail}")
            # 押す前に記録する。Verified はクリックの最中（押してから 0.14 秒ほど）に届く
            st.last_begin_press_at = time.time()
            WindowOperator.click()
            st.last_begin_press_at = time.time()
            # 離した直後に元の窓へ返す。Begin は押した瞬間に判定され、クリックの
            # 押す→離すの間で足りているので、別に待たない
            WindowOperator.return_front(loan)
        return True

    def _begin_precheck(self, check_freeze: bool = True) -> bool:
        """Begin実行前の中止条件を確認する。続行してよければTrue。

        check_freeze=False では他窓フリーズの確認を省く。OSC移動の前に
        呼ぶときに使う（移動はフリーズ中でも行うため）。
        """
        st = self._st
        if not self._is_running():
            # 装備待ちでも止める（以前は装備待ちの窓だけ、止めた後も Begin へ進んでいた）
            self._log("Begin キャンセル（停止）")
            return False
        if st.in_round:
            self._log("Begin キャンセル（次のラウンドが開始）")
            if st.waiting_for_equip and st.in_round:
                SharedState.equip_freeze_end(st)
                self._log("ラウンド開始によりフリーズ解除 → 装備待ちへ")
            elif not st.waiting_for_equip:
                return False
        if check_freeze:
            # 免除は自分が張った分だけ（_wait_other_windows と同じ規則）。
            # 装備待ちも見る——以前は見ておらず、他窓のロスト中でも押しに行った
            eq_ok, con_ok, spd_ok, rnd_ok = self._freezes_ok()
            if not eq_ok:
                self._log("Begin キャンセル（他窓の装備待ちを検出）")
                return False
            if not con_ok:
                self._log("Begin キャンセル（他窓のフリーズを検出）")
                return False
            if not spd_ok:
                self._log("Begin キャンセル（速度検知フリーズを検出）")
                return False
            if not rnd_ok:
                self._log("Begin キャンセル（ラウンド突入フリーズを検出）")
                return False
        return True

    @staticmethod
    def _freeze_ok(event, held: bool, count: int, sole_only: bool) -> bool:
        """このフリーズを無視して進んでよいか。

        免除するのは「自分が張った分」だけ。以前は「自分が何か1つ張っていれば
        他窓の分も全部無視」で、アイテムロスト窓が他窓の続行ラウンド中でも
        押しに行き、前面を奪っていた。

        sole_only=False は「自分が張っていれば、他窓が張っていても進む」。
        装備待ちだけがこれ。解除条件が `waiting_for_equip and begin_done` で、
        押さないと解けないので、複数窓が同時にロストすると互いに待って
        デッドロックする。他の3つは押下に依存せず解けるので待てる。
        """
        if event.is_set():
            return True             # 誰も張っていない
        if not held:
            return False            # 他窓の分。待つ
        return count <= 1 if sole_only else True

    def _freezes_ok(self) -> tuple:
        """4種別それぞれ、進んでよいかを返す（装備待ち・続行・速度検知・突入）"""
        st = self._st
        return (
            # 「張っているか」は登録の有無（equip_freeze_held）で見る。
            # waiting_for_equip は「ロストした」で、登録より先に立つ
            self._freeze_ok(SharedState.EQUIP_WAIT_EVENT, st.equip_freeze_held,
                            SharedState.get_equip_freeze_count(), False),
            self._freeze_ok(SharedState.CONTINUE_ROUND_EVENT,
                            st.continue_freeze_held,
                            SharedState.get_continue_round_count(), True),
            self._freeze_ok(SharedState.SPEED_FREEZE_EVENT, st.speed_freeze_held,
                            SharedState.get_speed_freeze_count(), True),
            self._freeze_ok(SharedState.ROUND_FREEZE_EVENT, st.round_freeze_held,
                            SharedState.get_round_freeze_count(), True),
        )

    def _wait_other_windows(self) -> bool:
        """他窓のフリーズ解除を待つ。続行可ならTrue。

        免除するのは自分が張った分だけ（_freeze_ok 参照）。自分の張った
        フリーズを自分で待つデッドロックは避けつつ、他窓が張った分は待つ。

        OSC移動はこの待ちの対象外。フォーカスを奪わず他窓を妨げないため、
        フリーズ中でも移動は進めてよい。呼ぶのはフォーカスを要する操作
        （クリック・キー入力）の直前だけにすること。
        """
        while self._is_running():
            eq_ok, con_ok, spd_ok, rnd_ok = self._freezes_ok()
            if eq_ok and con_ok and spd_ok and rnd_ok:
                return True
            if not eq_ok:
                self._log("他窓の装備待ち中 → フリーズ")
            if not con_ok:
                self._log("他窓の続行ラウンド中 → フリーズ")
            if not spd_ok:
                self._log("他窓の速度検知フリーズ中 → フリーズ")
            if not rnd_ok:
                self._log("他窓のラウンド突入フリーズ中 → フリーズ")
            # 待つのは通っていないものだけ。自分が張っているイベントは
            # clear のままなので、待つと無駄に1秒止まる
            for ok, event in ((eq_ok, SharedState.EQUIP_WAIT_EVENT),
                              (con_ok, SharedState.CONTINUE_ROUND_EVENT),
                              (spd_ok, SharedState.SPEED_FREEZE_EVENT),
                              (rnd_ok, SharedState.ROUND_FREEZE_EVENT)):
                if not ok:
                    event.wait(timeout=1.0)
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
            # アイテム取得→Beginモード（自動取得は動かさない）:
            # フリーズ発生源は自窓なので他窓の解除待ちはせず、
            # 装備確認 → Begin の順で進む。ここで他窓解除待ちをすると
            # 自分が張ったフリーズを自分で待つデッドロックになる。
            # フリーズ・前面化・音声は RoundOver の _attend_to_item_loss() で
            # 済ませている（張るのは冪等なので念のため残す）
            SharedState.equip_freeze_start(st)
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
            # ここでは張らない（EV: 23:12 窓1・09:47 窓2 は Begin を押す前に張り、Begin が通らずに取りに
            # 行けず、全窓が止まり続けた）。取りに行く窓は店でボタンを押す間だけ（_fetch_in_front）、
            # 取りに行かない窓は Begin が通ったとき（do_after_round のフェーズ 2）に張る
            self._log("⚠ アイテムロスト → Beginへ向かいます（フリーズは Begin が通ってから）")
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
        """Begin前の定位置移動。ラウンド種別で距離が変わる。
        どちらの移動か・最後までやったかを debug.log に必ず残す（後から確かめるため）"""
        st = self._st
        late = st.round_type in config.LATE_ROUND
        forward = config.BEGIN_FORWARD_SEC_LATER if late else config.BEGIN_FORWARD_SEC
        left = config.BEGIN_LEFT_SEC_LATER if late else config.BEGIN_LEFT_SEC
        head = f"[操作] [窓{st.window_idx}] Begin前の移動"
        label = 'Punished後' if late else '通常'
        DebugLog.write(f"{head}: {label} 前進{forward}秒・左{left}秒（round_type={st.round_type}）")
        self._move_since = (label, time.time())  # この行から押し始めるまでの遅れを測る（EH: 12:23 窓4 は 7.3 秒）
        # 実際に動いた量を debug.log へ（記録だけ。離してから少し後まで別スレッドで読む）
        sampler = MotionSampler(self._receiver, lambda m: DebugLog.write(f"{head}: {m}"))
        sampler.start()
        try:
            ok = self.move_forward_left(forward, left)
        except Exception as e:
            DebugLog.write(f"{head}: 途中で止めた（{type(e).__name__}）")
            raise
        finally:
            self._move_since = None             # 使われなかった（止めた・偽物）ときも次へ持ち越さない
            extension = getattr(self, "_move_extension", None)
            if extension is not None:
                sampler.suffix = counted_note(*extension)
            sampler.release()
        if ok is False:
            DebugLog.write(f"{head}: 途中で止めた（{'OSC を送れない' if self._osc is not None else '移動キーを送れない'}）")
        else:
            DebugLog.write(f"{head}: 最後までやった")
        st.begin_move_done = True

    def do_begin_again(self, round_seq: int):
        """定期の Verified を受理と取り違えた後に、Begin をもう一度押す。

        もう Verified Round End の後なので待ち（BEGIN_WAIT_SEC）は要らない。
        移動は時間で押す相対の移動なので2回やると行き過ぎる。このラウンドで
        済んでいれば押すところから、まだなら移動から。押し方・他窓のフリーズ待ち・
        押し直しの回数の上限は今の Begin と同じ（受理・開始・停止で止まる）
        """
        st = self._st
        if not self.can_operate():
            return
        if not self._wait_run_respawn():
            return
        if (not self._is_running() or st.in_round or st.begin_done
                or st.round_seq != round_seq):
            return
        fetched = self._fetched_round == round_seq
        self._trace("[状態] Begin の押し直しを開始（"
                    + ("店に行った後: リスポーンから" if fetched
                       else f"移動{'済み' if st.begin_move_done else 'から'}") + "）")
        if fetched:
            # 自動取得で柵を越えて店の前にいる。「移動済み」とみなさず、Run の後と同じくリスポーン → Begin 前の
            # 移動（EY: 09:23 窓3 は店の前で押し直し、BEGIN が見つからず受理されなかった）
            self._fetched_round = -1
            if not self._back_to_begin_after_fetch(round_seq):
                return
        elif not st.begin_move_done:
            if not self._begin_precheck(check_freeze=False):
                return
            self._begin_move()
        if not self._wait_other_windows():
            return
        if not self._begin_precheck():
            return
        if st.in_round or st.begin_done or st.round_seq != round_seq:
            return
        if self._press_begin(again=True):
            self._confirm_begin(round_seq)
            if self._gave_up_begin(round_seq):
                # 押し直しの流れであきらめたときも、30 秒おいてやり直す（EY・FL）
                self._retry_after_giving_up(round_seq)

    def _back_to_begin_after_fetch(self, round_seq: int) -> bool:
        """店に行った後の押し直し（EY）: Run の後と同じ流れ（リスポーン → 後ろへ → 左へ → 向きの直し。EN・EH）
        で戻ってから Begin 前の移動。リスポーンできなければ移動しない（店の前から動くと別の所へ行く。押す所の
        位置合わせ・探すに任せる）。止めた・ラウンドが始まった・Begin が通った → False"""
        st = self._st
        self._log("Begin の押し直し: 店に行った後なので、リスポーンして Begin の前へ戻ります")
        self._respawn_lobby = True
        try:
            faced = self._respawn_and_face(round_seq, "店に行った後")
        finally:
            self._respawn_lobby = False
        if not self._is_running() or st.in_round or st.round_seq != round_seq or st.begin_done:
            return False
        if faced:
            if not self._begin_precheck(check_freeze=False):
                return False
            self._begin_move()
        return True

    # ── Begin自動操作 ─────────────────────────

    def do_after_round(self):
        """
        ラウンド終了後: 待機 → [Begin前移動] → Beginクリック

        ロック戦略: 移動はどちらの窓もロック外（フォーカスを奪わない）。
        ロックを取るのは押すところだけ（OSC窓はカーソル、非OSC窓は前面化＋クリック）
        """
        st = self._st
        round_seq = st.round_seq
        clicked = False
        if not self._wait_run_respawn():
            return
        # アイテムを落とすのは死んだ・生き残ったとき（EU。LogMonitor → drop_at_round_end）。ここでは落とさない
        self._trace("[状態] Begin 待ちを開始（RoundOver から）")
        spam = None             # UseRight を連打しているスレッドの停止フラグ
        # RoundOver から一定時間待ってから移動を始める。移動し終える頃に
        # Verified Round End が出てクリックできる状態になる想定。
        pos_ref = self._pos_ref_plan()          # 待つ前に（終わったラウンドの種類を見る。EQ）
        if config.BEGIN_WAIT_SEC > 0:
            elapsed = (time.time() - st.round_over_time) if st.round_over_time else 0.0
            remain = config.BEGIN_WAIT_SEC - elapsed
            if remain > 0:
                if pos_ref:
                    # 待ちの中で基準を撮る。Begin 前の移動は遅らせない（待ちの終わりで打ち切る）
                    deadline = time.time() + remain
                    self._take_pos_ref(deadline)
                    remain = deadline - time.time()
                if remain > 0:
                    time.sleep(remain)
        # Beginはフレ/フレ+/招待/招待+のみ
        if not self.can_operate():
            return
        if not self._is_running() or st.in_round:
            self._log("Begin キャンセル（停止 or 次のラウンドが開始）")
            return

        # ── フェーズ1: Begin前移動 + 初回クリック ──
        # 移動はどちらの窓もフォーカスを奪わないので、ロックを取らずに動ける
        # （他窓と並行して進む）。他窓の解除を待つのは押す直前だけ。
        # 移動はフリーズ中でも行うので、ここでのフリーズ確認は省く
        if not self._begin_precheck(check_freeze=False):
            return
        if not st.in_round:
            self._begin_move()
            # Verified Round End が出た瞬間には、もう Begin が押せる。
            # 連打はその前から回しておき、待ち終わったらカーソルを一瞬
            # 差し込むだけにする（カーソルを奪う時間を最小にする）
            spam = self._start_use_spam(round_seq)
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
                clicked = self._press_begin()

        if clicked:
            self._confirm_begin(round_seq)
            if self._gave_up_begin(round_seq):
                if spam is not None:
                    spam.set()      # やり直しは何時間も続きうる。その間 UseRight を送り続けない（リスポーンのメニューにも当たる）
                    spam = None
                self._retry_after_giving_up(round_seq)
        if spam is not None:
            spam.set()      # 連打を止める（条件が変われば自分でも終わる）

        # 完全放置モード: 装備待ち（フリーズ・前面化・音声）は無いが、Begin が通った後に
        # ロストしたアイテムを取りに行く（依頼者）。取れなくても何もしない（次のラウンドでまた）
        if (self._hands_free() and st.begin_done and not st.item_id
                and not st.in_round and self._is_running()):
            fetch = self.item_fetch_target()
            if fetch:
                self._fetch_item(round_seq, *fetch)

        # 入室時に Guidance Plush を取れなかった窓（ER）。ロストの装備待ちが無いラウンドで、Begin が通った後に
        if (self._plush_retries and st.begin_done and not st.waiting_for_equip
                and not st.in_round and self._is_running()):
            self._retry_plush(round_seq)

        # ── フェーズ2: アイテムロスト装備待ち（ロック外）──
        # 装備済み（アイテム取得→Beginモードで先に装備確認済み）の場合は何もしない
        # （フリーズ解除はBEGIN_DONEイベント側で行う）
        if st.waiting_for_equip and not st.item_id:
            # 押した後に出す。押す前に前面化すると VRChat がアクティブになり、
            # カーソルを他の窓へ動かせなくなる（この窓のカーソル方式 Begin が
            # 壊れる）。受理されなかったラウンドでは出さない（依頼者了承済み）
            if st.begin_done:
                fetch = self.item_fetch_target()
                if fetch:
                    # 自動取得: いつもの案内のタイミング（Begin が通った後）で音だけ鳴らし、取りに行く。
                    # 「音は前面化と一緒に _show_item_loss からだけ」の例外。前面化すると取りに行く
                    # 前面化・カーソルの差し込みの邪魔になる。手で装備するならこの音で気づける。
                    # アイテム取得→Begin モードで RoundOver に鳴らした回は鳴らない（1ラウンド1回）
                    self.announce_item_lost_once()
                outcome = self._fetch_item(round_seq, *fetch) if fetch else None
                if outcome is None:
                    self._attend_to_item_loss()     # 取りに行けない: 今のアイテムロストの案内（前面化・音声）
                elif outcome == "stopped":
                    return
                # 取りに行って失敗・時間切れ・ラウンド開始: 何もしない（音・前面化・フリーズなし）。
                # 次のラウンドでまた取りに行く
            self._log("アイテム装備を待っています… （装備すると自動再開）")
            while st.waiting_for_equip and self._is_running():
                if not st.begin_done:
                    # 受理と見た Verified が定期だった（押し直しは LogMonitor → do_begin_again）。ここで待ち続けず、
                    # Begin が通ってから装備を待つ（EY: 09:23 窓3 はこのループにいて、押し直しの後もあきらめた
                    # ままになった）
                    self._log("Begin が通っていません → Begin が通ってから装備を待ちます")
                    while (not st.begin_done and st.waiting_for_equip and self._is_running()
                           and not st.in_round):
                        time.sleep(0.3)
                    if st.in_round:
                        break                       # ラウンドが始まった（装備待ちはふつうラウンド開始で外れる）
                    if st.begin_done and st.waiting_for_equip and self._is_running():
                        self._log("アイテム装備を待っています… （装備すると自動再開）")
                    continue
                time.sleep(0.3)
            if not self._is_running():
                SharedState.equip_freeze_end(st)
                return
            if st.item_id:
                self._log("✅ アイテム装備確認 → 続行")
            else:
                # 装備しないまま次のラウンドが始まった（装備待ちはラウンド開始で外れる）
                self._log("ラウンドが始まったので装備待ちをやめます（アイテムは未回収のまま）")
            if st.in_round:
                return

    # ── アイテム自動取得 ──────────────────
    def item_fetch_target(self) -> tuple | None:
        """自動取得するなら (店, アイテムの番号)。設定 OFF・OSC でない・ツールが Begin を
        押さない窓・このラウンドにロストしたアイテムが分からない・Others や
        表に無いアイテム → None（今どおり）。完全放置モードでも取りに行くが、
        Guidance Plush だけ（依頼者）"""
        st = self._st
        if SharedState.get_item_begin_mode():
            return None     # アイテム取得→Begin モードでは自動取得を動かさない（依頼者）
        if (not SharedState.get_item_fetch() or self._osc is None
                or not self._auto_begin_active() or not self.can_operate()):
            return None
        item_id = st.last_lost_item_id      # 最後にロストしたもの（装備かインスタンス変更で消える）
        if self._hands_free() and item_id != RoundDecision.guidance_plush_id():
            return None
        shop = ItemFetch.shop_for(item_id, config.ITEMS)
        if shop is None or not ItemFetch.available():
            return None
        return shop, item_id

    def _fetch_stopped(self, round_seq: int, deadline: float, want: int | None = None,
                       extra_stop=None) -> str | None:
        """want: Guidance Plush を取りに行くとき（DZ）の番号。そのときは、ほかのアイテムを持っていても
        続け、Guidance Plush を持ったら equipped。extra_stop: 入室操作の中止など（True で stopped）"""
        st = self._st
        if not self._is_running() or (extra_stop is not None and extra_stop()):
            return "stopped"
        if not self.can_operate():
            return "not_private"
        if st.in_round or st.round_seq != round_seq:
            return "round"
        if want is not None:
            if st.item_id == want:
                return "equipped"
        elif st.item_id and not st.waiting_for_equip:
            return "equipped"               # 手で装備した
        _eq_ok, con_ok, spd_ok, rnd_ok = self._freezes_ok()
        if not (con_ok and spd_ok and rnd_ok):
            # ほかの窓の続行ラウンド・速度検知・突入のフリーズ。その窓を人が操作し始めるので手を引く。
            # 装備待ちは自分の取得のためのものなので見ない
            return "frozen"
        if time.time() > deadline:
            return "timeout"
        return None

    def _fetch_item(self, round_seq: int, shop: str, item_id: int, plush: bool = False,
                    extra_stop=None) -> str:
        """Begin が通った後に店へ取りに行く。"ok"・"failed"・"timeout"・"round"・"stopped"・"equipped"。
        移動は OSC（ロック外）。照準とクリックは前面化が要るので排他の中で行い、終わったら前面を返す。
        plush: Guidance Plush の取り方（DZ。Event → スクロール → Have Plush → Equip）"""
        st = self._st
        head = f"[操作] [窓{st.window_idx}]"
        deadline = time.time() + config.ITEM_FETCH_LIMIT_SEC
        receiver = self._receiver
        want = item_id if plush else None

        def stopped():
            return self._fetch_stopped(round_seq, deadline, want, extra_stop)

        def grounded():
            return receiver.grounded_or_none if receiver is not None else None

        def capture():
            bits, w, h = ScreenCapture.capture_window(self._cfg.hwnd)
            aim = WindowOperator.aim_in_window_image(self._cfg.hwnd)
            if not bits or w <= 0 or h <= 0 or len(bits) < w * h * 4 or aim is None:
                return None
            import numpy as np
            bgra = np.frombuffer(bits, dtype=np.uint8)[:w * h * 4].reshape(h, w, 4)
            return np.ascontiguousarray(bgra[:, :, :3]), aim

        fetcher = ItemFetch.Fetcher(
            osc=self._osc, grounded=grounded, capture=capture,
            mouse=_FrontOnlyMouse(self._cfg.hwnd, stopped, allowed=self.can_operate),
            equip_seen=lambda: (st.equip_seen_seq, st.equip_seen_id),
            stopped=stopped,
            log=lambda m: DebugLog.write(f"{head} {m}"),
            saved_gain=self._saved_fetch_gain(),
            client_height=lambda: WindowOperator.client_height(self._cfg.hwnd),
            speed=self._receiver_speed if receiver is not None else None,
            on_miss=lambda shot, why: FetchMiss.save(shot, st.window_idx, why))
        name = f"{shop} Shop の id={item_id}"
        self._fetched_round = st.round_seq          # 柵を越えて店へ行く（EY: 押し直しは Begin の前へ戻ってから）
        self._log(f"アイテム取得: {name} を取りに行きます")
        DebugLog.write(f"{head} アイテム取得: 開始（{name}）")
        outcome = "failed"
        try:
            if fetcher.move_to_shop():
                outcome = self._fetch_in_front(fetcher, shop, item_id, plush)
        except ItemFetch.Stopped as e:
            outcome = e.args[0]
        except Exception:
            DebugLog.exception("ActionExecutor._fetch_item")
            outcome = "failed"
        finally:
            if self._osc is not None:
                self._osc.stop_all(repeat=1)
        DebugLog.write(f"{head} アイテム取得: 結果 {outcome}")
        if self._pending_view_dy:
            # 前面を取られるなどで視点を戻し切れなかった。ラウンド突入までに戻しに行く
            self._restore_view_soon()
        if outcome == "ok":
            self._log("アイテム取得: 装備できました")
        elif outcome != "equipped":
            why = {"failed": "取れませんでした", "timeout": f"{config.ITEM_FETCH_LIMIT_SEC:.0f}秒で間に合いません",
                   "round": "ラウンドが始まりました", "stopped": "停止しました",
                   "frozen": "ほかの窓がフリーズしました",
                   "not_private": "プライベートのインスタンスではなくなりました",
                   "front_lost": "この窓が前面でなくなりました"}.get(outcome, outcome)
            self._log(f"⚠ アイテム取得: {why} → やめます")
        return "ok" if outcome == "equipped" else outcome

    def request_plush_retry(self, why: str):
        """入室時に Guidance Plush を取れなかった（ER）。次の普通のラウンドの後から、最大 PLUSH_RETRY_MAX 回
        ふだんの自動取得の道で取りに行く"""
        self._plush_retries = config.PLUSH_RETRY_MAX
        DebugLog.write(f"[操作] [窓{self._st.window_idx}] Guidance Plush: 入室時に取れなかった（{why}）→ "
                       f"次のラウンドの後に取りに行く（最大 {config.PLUSH_RETRY_MAX} 回）")

    def _retry_plush(self, round_seq: int):
        """入室時に取れなかった Guidance Plush を取りに行く 1 回（ER）。別のアイテムを持っていればやめる。
        アイテム自動取得の条件（設定・OSC・ツールが Begin を押す窓・private・見本・アイテム取得→Begin モードでない）
        を満たさなければ、その回は行かない（回数は減らさない）"""
        st = self._st
        if self._plush_retries <= 0:
            return
        head = f"[操作] [窓{st.window_idx}] Guidance Plush:"
        plush = RoundDecision.guidance_plush_id()
        held = st.held_item_id          # 持っているアイテム（0 = 持っていない）
        if held:
            self._plush_retries = 0
            DebugLog.write(f"{head} "
                           + ("もう持っている" if held == plush else f"別のアイテム（id={held}）を持っている")
                           + " → やり直さない")
            return
        if (not plush or SharedState.get_item_begin_mode() or not SharedState.get_item_fetch()
                or self._osc is None or not self._auto_begin_active() or not self.can_operate()
                or self._receiver is None or not ItemFetch.available()):
            DebugLog.write(f"{head} アイテム自動取得の条件がそろわないので、このラウンドは取りに行かない")
            return
        self._plush_retries -= 1
        DebugLog.write(f"{head} 入室時に取れなかった分を取りに行く（残り {self._plush_retries} 回）")
        outcome = self._fetch_item(round_seq, "Event", plush, plush=True)
        if outcome == "ok":
            self._plush_retries = 0

    def fetch_guidance_plush(self, extra_stop=None) -> str:
        """入室の Begin が受理された後に Guidance Plush を取りに行く（DZ）。マクロの監視中で、この窓の
        速さの受信（接地を見る）があるときだけ。"skipped"（行かない）か _fetch_item の結果"""
        st = self._st
        plush = RoundDecision.guidance_plush_id()
        why = ("Guidance Plush の番号が分からない" if not plush
               else "OSC が使えない窓" if self._osc is None
               else "プライベートのインスタンスでない" if not self.can_operate()
               else "受信が無い" if self._receiver is None
               else "店の見本が使えない" if not ItemFetch.available() else None)
        if why:
            self._log(f"Guidance Plush を取りに行きません（{why}）")
            return "skipped"
        return self._fetch_item(st.round_seq, "Event", plush, plush=True, extra_stop=extra_stop)

    @staticmethod
    def _saved_fetch_gain():
        """保存した感度 (横, 縦)。今の送る間隔で保存したものだけ（間隔が違えば測る）"""
        saved = SharedState.get_item_fetch_gain()
        if saved and abs(saved[2] - ItemFetch.STEP_SEC) < 1e-9:
            return saved[:2]
        return None

    def _fetch_in_front(self, fetcher, shop: str, item_id: int, plush: bool = False) -> str:
        st = self._st
        with SharedState._GLOBAL_ACTION_LOCK:
            fetcher._check()                # ロックを待つ間にラウンドが始まったら押さない
            # ロストしたアイテムを取りに行くときだけ、店でボタンを押す間は装備待ちフリーズを張る（EV。
            # 柵越え・移動の間は張らない。Equip が済んだら解く）。入室時の Guidance Plush・放置モードは張らない
            froze = st.waiting_for_equip and not self._hands_free()
            if froze:
                SharedState.equip_freeze_start(st)
                DebugLog.write(f"[操作] [窓{st.window_idx}] アイテム取得: 店で押す間 装備待ちフリーズを張る")
            try:
                return self._fetch_in_front_locked(fetcher, shop, item_id, plush)
            finally:
                if froze:
                    SharedState.equip_freeze_end(st)
                    DebugLog.write(f"[操作] [窓{st.window_idx}] アイテム取得: 装備待ちフリーズを解いた（Equip の後）")

    def _fetch_in_front_locked(self, fetcher, shop: str, item_id: int, plush: bool) -> str:
        """鍵の中: 前面を借りて店で押し、視点を戻して前面を返す"""
        cursor = WindowOperator.cursor_position()   # 前面を借りる前の Windows のカーソル
        ok, loan = self._borrow_front()
        if not ok:
            return "failed"
        try:
            fetcher.sleep(config.ITEM_FETCH_FOCUS_SEC)
            bought = fetcher.buy_plush(item_id) if plush else fetcher.buy(shop, item_id)
            if not bought:
                return "failed"
            if fetcher.measured_gain is not None:
                # 次回に使う。保存するのは測った値だけ（合わせで直した値はその回の中だけ）
                SharedState.set_item_fetch_gain(
                    (*fetcher.measured_gain, ItemFetch.STEP_SEC, SharedState.ITEM_FETCH_GAIN_MARK))
            return "ok"
        finally:
            # 視点を戻してから前面を返す（どの終わり方でも。戻さないと次の Begin が押せない）。
            # 窓が前に無い（閉じた・奪われた）ときは、ほかの窓へ送らないよう戻さない
            try:
                fetcher.mouse.inner.restoring = True   # 止まった後でも、前面なら視点は戻す
                if WindowOperator.foreground_hwnd() == self._cfg.hwnd:
                    fetcher.restore_view()
                elif fetcher.mouse.total != [0, 0]:
                    DebugLog.write(f"[操作] [窓{self._st.window_idx}] アイテム取得: "
                                   f"窓が前に無いので視点を戻せません（{fetcher.mouse.total}）")
            except ItemFetch.Stopped:
                pass                            # 戻す途中で前面でなくなった（残りは下で覚える）
            except Exception:
                DebugLog.exception("ActionExecutor._fetch_in_front.restore_view")
            # 戻せなかった縦の視点は、次にツールがこの窓を前面にしたとき最初に戻す
            try:
                self._pending_view_dy += -fetcher.mouse.total[1]
                WindowOperator.return_front(loan)
            finally:
                self._put_cursor_back(cursor)

    def _put_cursor_back(self, cursor, what: str = "アイテム取得", when: str = "取得前"):
        """アイテム取得（など）で動かした Windows のカーソルを、前にあった場所へ戻す（依頼者）。
        前面を返した後、この窓が前面でなくなってから（前面の VRChat はマウスを掴んでいて、Tab を
        押していないときに置くと視点が回る）。この窓がまだ前面なら戻さず debug.log に残す"""
        head = f"[操作] [窓{self._st.window_idx}] {what}:"
        if cursor is None:
            return
        if WindowOperator.foreground_hwnd() == self._cfg.hwnd:
            DebugLog.write(f"{head} この窓がまだ前面なので、カーソルを{when}の位置 {cursor} へ戻しません")
            return
        if WindowOperator.set_cursor_position(cursor):
            DebugLog.write(f"{head} カーソルを{when}の位置 {cursor} へ戻した")
        else:
            DebugLog.write(f"{head} カーソルを{when}の位置 {cursor} へ戻せません")

    # ── Run のリスポーン → 後ろへ → 正面へ ──────────────────
    def _wait_run_respawn(self) -> bool:
        """Run のリスポーン（後ろへ・正面へまで）が走っていれば終わるのを待つ。止めたら False"""
        while not self._run_respawn_idle.wait(config.RUN_RESPAWN_POLL_SEC):
            if not self._is_running():
                return False
        return True

    def _lobby_still(self, round_seq: int) -> bool:
        """店に行った後の押し直しのリスポーン（EY）: まだラウンドの外で、Begin も通っていないか"""
        st = self._st
        return (self._is_running() and not st.in_round and st.round_seq == round_seq
                and not st.begin_done and self.can_operate())

    def _run_still(self, round_seq: int) -> bool:
        """リスポーンする前: まだ Run のラウンドの中で、続けてよいか（停止・private でない・次のラウンド・
        Run が終わった → False）。店に行った後の押し直し（EY）では _lobby_still"""
        if self._respawn_lobby:
            return self._lobby_still(round_seq)
        st = self._st
        return (self._is_running() and st.in_round and st.round_seq == round_seq
                and self.can_operate())

    def _run_after_respawn(self, round_seq: int) -> bool:
        """リスポーンした後（後ろへ・左へ・向きの直し）: 続けてよいか。ToN はリスポーン（opt out）で
        その人のラウンドをその場で終わらせる（同じ秒に RoundOver）ので in_round は見ない。
        停止・次のラウンドが始まった（round_seq が変わった）・private でない → False（DP）。
        店に行った後の押し直し（EY）では _lobby_still"""
        if self._respawn_lobby:
            return self._lobby_still(round_seq)
        st = self._st
        if self._is_running() and st.round_seq == round_seq and not st.in_round                 and not self._noted_round_over_after_respawn:
            self._noted_round_over_after_respawn = True
            DebugLog.write(f"[操作] [窓{st.window_idx}] Run: リスポーンの後に RoundOver が来た → 後ろへ・正面へは続ける")
        return self._is_running() and st.round_seq == round_seq and self.can_operate()

    def do_run_respawn(self, round_seq: int):
        """Run のラウンドに入ったとき（依頼者 2026-10-10）: リスポーン（前面・鍵の中）→
        後ろへ RUN_RESPAWN_BACK_SEC → 左へ RUN_RESPAWN_TURN_SEC（背面・OSC）。private・OSC の窓だけ"""
        head = f"[操作] [窓{self._st.window_idx}] Run:"
        if not SharedState.get_run_respawn() or self._osc is None or not self.can_operate():
            DebugLog.write(f"{head} リスポーンしません（{'設定 OFF' if not SharedState.get_run_respawn() else 'OSC が使えない窓' if self._osc is None else 'private でない'}）")
            return
        self._respawn_and_face(round_seq, "Run")

    def _respawn_and_face(self, round_seq: int, label: str) -> bool:
        """リスポーン → 後ろへ → 左へ → 向きの直し（do_run_respawn の中身。店に行った後の押し直しでも使う。EY）。
        最後（正面を向く）まで続けられたら True"""
        head = f"[操作] [窓{self._st.window_idx}] Run:"
        self._run_respawn_idle.clear()
        self._noted_round_over_after_respawn = False
        self._respawn_menu_open = False         # Esc のメニューを開いているか（_respawn_press が持つ）
        try:
            why = self._respawn_when_free(round_seq)
            if why:
                self._log(f"{label}: リスポーンできませんでした（{why}）")
                return False
            # 「Player respawned」から RUN_BACK_DELAY_SEC 待ってから後ろへ（EP: 直後の速さが残っていて、
            # 動き出しから数えるのが 0 秒と取り違えていた）。待つ間に止めた・ラウンドが変わったらやめる
            deadline = time.time() + config.RUN_BACK_DELAY_SEC
            while time.time() < deadline:
                if not self._run_after_respawn(round_seq):
                    DebugLog.write(f"{head} 止めた・ラウンドが変わった → 移動をやめる")
                    return False
                time.sleep(min(config.RUN_RESPAWN_POLL_SEC, max(0.0, deadline - time.time())))
            for address, sec in (("/input/MoveBackward", config.RUN_RESPAWN_BACK_SEC),
                                 ("/input/LookLeft", config.RUN_RESPAWN_TURN_SEC)):
                if not self._run_after_respawn(round_seq):
                    DebugLog.write(f"{head} 止めた・ラウンドが変わった → 移動をやめる")
                    return False
                DebugLog.write(f"{head} {address} {sec:.2f}秒")
                # 下がる動きは実際に動いた量を debug.log へ（EE。受信が無ければ書かない）
                sampler = None
                if address == "/input/MoveBackward" and self._receiver is not None:
                    sampler = MotionSampler(self._receiver, lambda m: DebugLog.write(f"{head} 後ろへ: {m}"))
                    sampler.start()
                try:
                    if sampler is not None:
                        # 動き出しから数える（EH: 12:09 窓3 は動き出し 0.75 秒で下がり足りなかった）
                        _ok, extension, how, repressed = self._press_counted_from_start(
                            [(address, sec)], lambda: not self._run_after_respawn(round_seq),
                            config.BEGIN_USUAL_START_SEC, config.RUN_EXTEND_MAX_SEC,
                            lambda m: DebugLog.write(f"{head} 後ろへ: {m}"))
                        sampler.suffix = counted_note(extension, how, repressed)
                    else:
                        self._osc.press(address, sec, stop=lambda: not self._run_after_respawn(round_seq))
                finally:
                    if sampler is not None:
                        sampler.release()
                self._osc.stop_all(repeat=1)            # 途中でやめても押したままにしない
            fixed = self._face_the_stairs(round_seq)
            moved = self._fix_depth(round_seq)
            if moved:
                self._log(f"Run: 位置を直しました（{moved}）")
                fixed += self._face_the_stairs(round_seq)     # 前後に動かしたら向きをもう 1 周
            if not self._run_after_respawn(round_seq):
                return False
            self._log(f"{label}: リスポーンして正面を向きました"
                      + (f"（向きを {fixed} 回直しました）" if fixed else ""))
            if not self._respawn_lobby:
                self._take_pos_run()            # Run の後の撮影（EQ）。店に行った後には撮らない
            return True
        finally:
            self._run_respawn_idle.set()

    def _face_the_stairs(self, round_seq: int) -> int:
        """撮影で階段の上の構造の絵（RunFace）を探し、真ん中からずれていれば短く回して直す（背面・OSC。
        前面は借りない。ほかの窓のフリーズ中でもよい）。目印が左（回し足りない）→ LookLeft、右 → LookRight を
        min(RUN_FACE_MAX_SEC, max(RUN_FACE_MIN_SEC, |ずれ| / (RUN_FACE_RATE × 幅))) 秒。押した後に撮り直して、
        ずれが大きくなった・目印が消えた → 直前の分を逆向きに同じ秒数押して戻し、止める（悪い向きのまま
        手放さない。依頼者）。許しの中 → 止める。縮んだ → 続ける（RUN_FACE_TRIES 回まで）。
        最初から目印が見つからない → 押さない。直した（残した）回数を返す"""
        idx = self._st.window_idx
        head = f"[操作] [窓{idx}] Run: 向き"
        if not self._run_after_respawn(round_seq):
            return 0
        shot, mark = self._measure_face()
        self._last_face = (shot, mark)
        if mark is None:
            DebugLog.write(f"{head}: 目印が見つからない → 今の向きのまま")
            wide = RunFace.best_anywhere(shot) if shot is not None else None
            if wide is not None:
                # 次に起きたときの材料（記録だけ。範囲の外は目印でない所とも合うので動きには使わない。EE）
                DebugLog.write(f"{head}: 範囲の外も含めた一番よい一致 {wide[1]:.3f}・"
                               f"倍率 {RunFace.factor(wide, shot.shape[0]):.2f}・x {wide[0]:.1f}")
            RunFace.save_shot(shot, idx, "notfound")
            return 0
        fixed = 0
        while True:
            width = shot.shape[1]
            dx = mark[0] - width / 2
            DebugLog.write(f"{head}: 目印 x {mark[0]:.1f}（幅 {width}・{RunFace.describe(mark)}・倍率 {mark[2]:.2f}）"
                           f"・ずれ {dx:+.1f}")
            if abs(dx) <= width * config.RUN_FACE_TOL:
                DebugLog.write(f"{head}: 正面（直した {fixed} 回）")
                return fixed
            if fixed >= config.RUN_FACE_TRIES:
                DebugLog.write(f"{head}: {fixed} 回直しても ずれ {dx:+.1f} → やめる")
                RunFace.save_shot(shot, idx, "tries")
                return fixed
            if not self._run_after_respawn(round_seq):
                return fixed
            address = "/input/LookLeft" if dx < 0 else "/input/LookRight"
            sec = min(config.RUN_FACE_MAX_SEC,
                      max(config.RUN_FACE_MIN_SEC, abs(dx) / (config.RUN_FACE_RATE * width)))
            DebugLog.write(f"{head}: {address} {sec:.3f}秒")
            self._osc.press(address, sec, stop=lambda: not self._run_after_respawn(round_seq))
            self._osc.stop_all(repeat=1)
            fixed += 1
            if not self._run_after_respawn(round_seq):
                return fixed
            new_shot, new_mark = self._measure_face()
            if new_mark is None or abs(new_mark[0] - new_shot.shape[1] / 2) > abs(dx):
                back = "/input/LookRight" if address == "/input/LookLeft" else "/input/LookLeft"
                why = ("目印が消えた" if new_mark is None
                       else f"ずれが大きくなった（{new_mark[0] - new_shot.shape[1] / 2:+.1f}）")
                DebugLog.write(f"{head}: {why} → {back} {sec:.3f}秒で戻して止める（ずれ {dx:+.1f} の向き）")
                self._osc.press(back, sec, stop=lambda: not self._run_after_respawn(round_seq))
                self._osc.stop_all(repeat=1)
                RunFace.save_shot(new_shot if new_shot is not None else shot, idx, "reverted")
                return fixed - 1
            shot, mark = new_shot, new_mark
            self._last_face = (shot, mark)

    def _fix_depth(self, round_seq: int) -> str | None:
        """向きを直した後、目印の倍率 f（いつもの位置で 1.0）が 1.0 ± RUN_DEPTH_TOL の外なら前後に動かす（EE）。
        遠い（f が小さい）→ MoveForward、近い → MoveBackward。最初は RUN_DEPTH_FIRST_SEC、その後は撮り直した
        f の変わり方（秒あたり）で残りを割った長さ（RUN_DEPTH_MIN_SEC〜MAX_SEC）。f が 1.0 から遠のいた・
        目印が消えた → 同じ長さだけ逆へ戻して止める。RUN_DEPTH_TRIES 回まで。背面・OSC（前面は借りない）。
        速さを受け取れる窓は、押す長さを動き出しから数え、動き出さなかった押しでは遠のいた・近づいたを判断しない
        （戻さない・撮り直さない。同じ長さでもう一度押す）。遠のいたと見るのは RUN_DEPTH_WORSE 以上のとき（EH）。
        動かした（戻した分を除いて残した）なら画面のログ用の説明、動かさなければ None。
        config.RUN_DEPTH_ENABLED が False（EJ。今の既定）なら倍率を debug.log に書くだけで動かさない"""
        idx = self._st.window_idx
        head = f"[操作] [窓{idx}] Run: 前後"
        # 向きを直し終えた撮影を使う（撮り直さない。向きは動かしていない）。止めた・ラウンドが変わったかは
        # 押す前ごとに見る
        shot, mark = getattr(self, "_last_face", (None, None))
        self._last_face = (None, None)
        if mark is None or shot is None:
            DebugLog.write(f"{head}: 目印が見つからない → 直さない")
            return None
        f = RunFace.factor(mark, shot.shape[0])
        if not config.RUN_DEPTH_ENABLED:
            # EJ: 目印の大きさは距離を表していなかった。測って書くだけで動かさない
            DebugLog.write(f"{head}: 倍率 {f:.3f}（{RunFace.describe(mark)}）（記録だけ・動かさない）")
            return None
        rate = None                     # 秒あたりの f の変化
        kept = []                       # 残した (向き, 秒)
        tries = 0
        while True:
            DebugLog.write(f"{head}: 倍率 {f:.3f}（{RunFace.describe(mark)}・{tries} 回動かした）")
            if abs(f - 1.0) <= config.RUN_DEPTH_TOL:
                break
            if tries >= config.RUN_DEPTH_TRIES:
                DebugLog.write(f"{head}: {tries} 回動かしても倍率 {f:.3f} → やめる")
                break
            if not self._run_after_respawn(round_seq):
                break
            forward = f < 1.0
            address = "/input/MoveForward" if forward else "/input/MoveBackward"
            sec = (config.RUN_DEPTH_FIRST_SEC if rate is None
                   else min(config.RUN_DEPTH_MAX_SEC, max(config.RUN_DEPTH_MIN_SEC, abs(1.0 - f) / rate)))
            DebugLog.write(f"{head}: {address} {sec:.3f}秒")
            moved = self._depth_press(address, sec, round_seq, head)
            tries += 1
            if not self._run_after_respawn(round_seq):
                break
            if moved is False:
                DebugLog.write(f"{head}: 動き出さなかった → 遠のいた・近づいたを判断しない（戻さない）")
                continue
            new_shot, new_mark = self._measure_face()
            new_f = RunFace.factor(new_mark, new_shot.shape[0]) if new_mark is not None else None
            if new_f is None or abs(new_f - 1.0) - abs(f - 1.0) >= config.RUN_DEPTH_WORSE - 1e-9:
                back = "/input/MoveBackward" if forward else "/input/MoveForward"
                why = "目印が消えた" if new_f is None else f"倍率が遠のいた（{new_f:.3f}）"
                DebugLog.write(f"{head}: {why} → {back} {sec:.3f}秒で戻して止める")
                self._depth_press(back, sec, round_seq, head)
                RunFace.save_shot(new_shot if new_shot is not None else shot, idx, "depth_reverted")
                break
            # 近づいたときだけ速さを覚える（RUN_DEPTH_WORSE 未満で遠のいたのは測りのぶれとして残す）
            if abs(new_f - f) > 1e-3 and abs(new_f - 1.0) < abs(f - 1.0):
                rate = abs(new_f - f) / sec
            kept.append((forward, sec))
            shot, mark, f = new_shot, new_mark, new_f
        if not kept:
            return None
        RunFace.save_shot(shot, idx, "depth")      # 直した後の撮影も残す
        first = kept[0][0]
        total = sum(sec if fw == first else -sec for fw, sec in kept)
        return f"{'遠い → 前へ' if first else '近い → 後ろへ'} {total:.1f} 秒"

    def _depth_press(self, address: str, sec: float, round_seq: int, head: str):
        """前後の直しの 1 回の押し。速さを受け取れる窓は動き出しから sec（EH）。動いた True・
        動き出さなかった False・分からない（受信が無い）None"""
        stop = lambda: not self._run_after_respawn(round_seq)  # noqa: E731
        if self._receiver is None:
            self._osc.press(address, sec, stop=stop)
            self._osc.stop_all(repeat=1)
            return None
        _ok, extension, how, repressed = self._press_counted_from_start(
            [(address, sec)], stop, 0.0, config.RUN_EXTEND_MAX_SEC, lambda m: DebugLog.write(f"{head}: {m}"))
        self._osc.stop_all(repeat=1)
        DebugLog.write(f"{head}: {address}{counted_note(extension, how, repressed)}")
        return {"動き出し": True, "動き出さない": False}.get(how)

    def _measure_face(self):
        """回し終えるのを待って撮り、目印を探す。(撮影 or None, 目印 or None)"""
        time.sleep(config.RUN_FACE_SETTLE_SEC)
        shot = self._capture_bgr()
        return shot, (RunFace.find_mark(shot) if shot is not None else None)

    # ── Run の位置合わせのためのデータ集め（EQ。撮って残すだけ）──────────
    def _pos_ref_plan(self) -> bool:
        """RoundOver の後（Begin 前の移動の待ちの前）。終わったラウンドを数え、今回の待ちの中で基準を撮るか。
        撮らない理由（続行ラウンド・Run の後・private でない・受信なし・まだ数えていない）は debug.log に。
        撮った後は、窓の大きさが変わったときだけまた撮る"""
        st = self._st
        head = f"[操作] [窓{st.window_idx}] Run の位置: 基準"
        if st.round_type == "Run":
            why = "Run の後"
        elif st.is_continue_round or st.open_special_continue:
            why = "続行ラウンドの後"
        else:
            self._pos_rounds += 1
            why = None
        if self._pos_ref_size is not None:
            size = WindowOperator.client_size(self._cfg.hwnd)
            if size == self._pos_ref_size:
                return False                # 撮ってある（毎ラウンドは書かない）
            if why is None:
                DebugLog.write(f"{head}: 窓の大きさが変わった（{self._pos_ref_size} → {size}）→ 撮り直す")
        if why is None and self._pos_rounds < config.RUN_POS_AFTER_ROUNDS:
            why = f"普通に終わったラウンド {self._pos_rounds}/{config.RUN_POS_AFTER_ROUNDS}"
        if why is None and not self.can_operate():
            why = "private でない"
        if why is None and self._receiver is None:
            why = "速さの受信が無い"
        if why:
            DebugLog.write(f"{head}: 撮らない（{why}）")
            return False
        return True

    def _take_pos_ref(self, deadline: float):
        """deadline（Begin 前の移動を始める時刻）までに、止まっている（速さ RUN_POS_STILL_SPEED 未満が
        RUN_POS_STILL_SEC 続く）のを待って 2 枚を RUN_POS_GAP_SEC あけて撮り、差が RUN_POS_DIFF_MAX 以下なら
        残す。間に合わない・動いた・差が大きい → 撮らずに次のラウンドで。背面のまま撮る（前面・入力は使わない）"""
        st = self._st
        head = f"[操作] [窓{st.window_idx}] Run の位置: 基準"
        still_since = None
        top = 0.0
        while True:
            if not self._is_running() or st.in_round:
                return
            now = time.time()
            if now + config.RUN_POS_GAP_SEC >= deadline:
                DebugLog.write(f"{head}: 撮らない（Begin 前の移動までに止まらなかった。最高の速さ {top:.2f}）")
                return
            speed = self._receiver_speed()
            if speed is not None:
                top = max(top, speed)
            if speed is None or speed >= config.RUN_POS_STILL_SPEED:
                still_since = None
            elif still_since is None:
                still_since = now
            elif now - still_since >= config.RUN_POS_STILL_SEC:
                break
            time.sleep(config.RUN_POS_POLL_SEC)
        size = WindowOperator.client_size(self._cfg.hwnd)
        first = self._capture_bgr()
        time.sleep(config.RUN_POS_GAP_SEC)
        speed = self._receiver_speed()
        second = self._capture_bgr()
        if speed is None or speed >= config.RUN_POS_STILL_SPEED:
            DebugLog.write(f"{head}: 撮らない（2 枚の間に動いた。速さ {speed}）")
            return
        d = RunPos.diff(first, second)
        if d is None:
            DebugLog.write(f"{head}: 撮らない（撮れない・2 枚の大きさが違う）")
            return
        if d > config.RUN_POS_DIFF_MAX:
            DebugLog.write(f"{head}: 撮らない（2 枚の差 {d:.2f} > {config.RUN_POS_DIFF_MAX}。向きを変えた？）")
            return
        light = RunPos.brightness(second)
        if light is None or light < config.RUN_POS_DARK_MAX:
            DebugLog.write(f"{head}: 撮らない（画面が暗い（死んだ画面など）。明るさ "
                           + ("測れない" if light is None else f"{light:.1f} < {config.RUN_POS_DARK_MAX}") + "）")
            return
        path = RunPos.save(second, st.window_idx, "ref", size)
        if path is None:
            return
        self._pos_ref_size = size
        DebugLog.write(f"{head}: 撮った {path.name}（2 枚の差 {d:.2f}・明るさ {light:.1f}・窓 {size}）")

    def _take_pos_run(self):
        """Run のリスポーン → 後ろへ → 左へ → 向きの直し、の後に 1 枚（背面のまま。等倍）"""
        st = self._st
        size = WindowOperator.client_size(self._cfg.hwnd)
        path = RunPos.save(self._capture_bgr(), st.window_idx, "run", size)
        DebugLog.write(f"[操作] [窓{st.window_idx}] Run の位置: Run の後 "
                       + (f"撮った {path.name}（窓 {size}）" if path else "撮れない"))

    def _respawn_when_free(self, round_seq: int) -> str | None:
        """ほかの窓のフリーズが無いときに、前面を借りてリスポーンする。できたら None、だめなら理由。
        ほかの窓がフリーズを張っている間は前面を借りずに待つ（自分の分は数えない）。前面を借りている
        途中でほかの窓が張ったら、メニューを閉じて前面を返し、解けたらやり直す"""
        head = f"[操作] [窓{self._st.window_idx}] Run:"
        while True:
            while not self._nobody_else_frozen():
                if not self._run_still(round_seq):
                    DebugLog.write(f"{head} ほかの窓のフリーズが解けないまま Run が終わった → やらない")
                    return "ほかの窓のフリーズ中に Run が終わった"
                time.sleep(config.RUN_RESPAWN_POLL_SEC)
            if not self._run_still(round_seq):
                return "止めた・ラウンドが変わった"
            result = self._respawn_in_front(round_seq)
            if result != "frozen":
                return result
            DebugLog.write(f"{head} ほかの窓がフリーズを張った → 前面を返して、解けたらやり直す")

    def _respawn_stop(self, round_seq: int) -> str | None:
        """前面を借りている間の見張り。"frozen"（ほかの窓のフリーズ）・理由（止めた等）・None（続けてよい）"""
        if not self._run_still(round_seq):
            return "止めた・ラウンドが変わった"
        if not self._nobody_else_frozen():
            return "frozen"
        if WindowOperator.foreground_hwnd() != self._cfg.hwnd:
            return "この窓が前面でなくなった"
        return None

    def _respawn_wait(self, sec: float, round_seq: int) -> str | None:
        """sec 待つ（見張りながら）"""
        deadline = time.time() + sec
        while time.time() < deadline:
            stop = self._respawn_stop(round_seq)
            if stop:
                return stop
            time.sleep(min(config.RUN_RESPAWN_POLL_SEC, max(0.0, deadline - time.time())))
        return self._respawn_stop(round_seq)

    def _capture_bgr(self):
        bits, w, h = ScreenCapture.capture_window(self._cfg.hwnd)
        if not bits or w <= 0 or h <= 0 or len(bits) < w * h * 4:
            return None
        import numpy as np
        bgra = np.frombuffer(bits, dtype=np.uint8)[:w * h * 4].reshape(h, w, 4)
        return np.ascontiguousarray(bgra[:, :, :3])

    def _respawn_in_front(self, round_seq: int) -> str | None:
        """前面を借りて Esc のメニューからリスポーンする。None（できた）・"frozen"（ほかの窓のフリーズ）・理由。

        鍵（_GLOBAL_ACTION_LOCK）と前面を持つのは「メニューを開く → ボタンを探す → 押す → 行を最大
        RESPAWN_HOLD_FRONT_SEC 待つ」の間（_respawn_press）。押した直後に前面とカーソルを返すと、VRChat が
        クリックを処理する前に前面が移って押せていなかった（EN）。HOLD の間に来なければ前面を返して鍵を離し、
        残り（押してから RESPAWN_LOG_WAIT_SEC まで）は鍵の外で待つ（ほかの窓の Begin のクリックを
        最大 RESPAWN_LOG_WAIT_SEC × 2 止めない）。来なければもう一度
        前面を借り、ボタンがまだ写っていれば押し直す（1 回）。それでも来なければメニューを閉じる。
        メニューが開いているか（self._respawn_menu_open）は押しをまたいで覚える——ほかの窓のフリーズで
        やり直すとき、開いたままのメニューに Esc を送って閉じてしまわないため"""
        st = self._st
        head = f"[操作] [窓{st.window_idx}] Run:"
        seen = st.respawn_seen_seq
        for presses in (1, 2):
            self._respawn_pressed_at = None
            self._respawn_came = False
            outcome = self._respawn_press(round_seq, presses, head, seen)
            if outcome is not None:
                return outcome
            pressed_at = self._respawn_pressed_at
            came = self._respawn_came
            where = "前面のまま"
            if not came:
                # 前面は返した。残りを鍵の外で待つ
                came = self._wait_respawn_line(seen, round_seq, since=pressed_at)
                where = "前面を返した後"
            if came:
                self._respawn_menu_open = False         # 押せた（メニューは閉じている）
                DebugLog.write(f"{head} 「Player respawned」を受けた（押してから "
                               f"{time.time() - pressed_at:.2f} 秒・{where}）")
                return None
            DebugLog.write(f"{head} {config.RESPAWN_LOG_WAIT_SEC:.0f}秒で「Player respawned」が来ない")
        self._respawn_close_menu(head)
        return "リスポーンの行が来ない"

    def _respawn_press(self, round_seq: int, presses: int, head: str, seen: int) -> str | None:
        """鍵の中で前面を借りて: （メニューが開いていなければ Esc →）撮る → ボタンを探す → カーソルを置いて
        読み直す → クリック → 鍵・前面・カーソルをそのままにして「Player respawned」を最大
        RESPAWN_HOLD_FRONT_SEC 待つ（来たかは self._respawn_came、押し終えた時刻は self._respawn_pressed_at）。
        押したら None。押せなければ理由（"frozen" を含む）を返し、メニューが開いて
        いれば Esc で閉じる。どの終わり方でも前面を返し、カーソルを戻す。
        2 回目（presses == 2）はボタンがまだ写っているかを 1 回だけ見て、写っていなければ押し直さない
        （メニューは閉じている＝押せていたかもしれない。DQ）"""
        st = self._st
        hwnd = self._cfg.hwnd
        with SharedState._GLOBAL_ACTION_LOCK:
            stop = self._respawn_stop_before_front(round_seq)
            if stop:
                return stop
            cursor = WindowOperator.cursor_position()
            ok, loan = WindowOperator.borrow_front(hwnd)
            if not ok:
                return "前面にできない"
            pressed = False
            try:
                stop = self._respawn_stop(round_seq)
                if stop:
                    return stop
                if not self._respawn_menu_open:
                    WindowOperator.send_keys("esc")
                    self._respawn_menu_open = True
                    stop = self._respawn_wait(config.RESPAWN_MENU_WAIT_SEC, round_seq)
                    if stop:
                        return stop
                found = None
                tries = config.RESPAWN_FIND_TRIES if presses == 1 else 1
                for n in range(1, tries + 1):
                    shot = self._capture_bgr()
                    found = RespawnButton.find(shot) if shot is not None else None
                    DebugLog.write(f"{head} ボタンを探す {n} 回目: "
                                   + ("見つからない" if found is None else
                                      f"中心 ({found[0]:.1f}, {found[1]:.1f})・一致 {found[2]:.3f}・倍率 {found[3]:.2f}"))
                    if found is not None:
                        break
                    if n < tries:
                        stop = self._respawn_wait(config.RESPAWN_FIND_RETRY_SEC, round_seq)
                        if stop:
                            return stop
                if found is None:
                    if presses == 1:
                        return "リスポーンのボタンが見つからない"
                    DebugLog.write(f"{head} ボタンがもう写っていない → 押し直さない")
                    self._respawn_menu_open = False     # 閉じている（Esc を送らない）
                    return "リスポーンの行が来ない"
                origin = WindowOperator.window_origin(hwnd)
                if origin is None:
                    return "窓が無い"
                point = (int(round(found[0] + origin[0])), int(round(found[1] + origin[1])))
                if not self._place_cursor(point, head):
                    return "カーソルを置けない"
                stop = self._respawn_stop(round_seq)
                if stop:
                    return stop
                DebugLog.write(f"{head} リスポーンのボタンを押す {point}（{presses} 回目）")
                WindowOperator.mouse_click(config.RESPAWN_CLICK_SEC)
                pressed = True
                self._respawn_pressed_at = time.time()
                self._respawn_came = self._wait_respawn_line(seen, round_seq, config.RESPAWN_HOLD_FRONT_SEC)
                return None
            finally:
                try:
                    if not pressed:
                        self._respawn_esc_if_open(head)     # 押せずに終わる: メニューを閉じて返す
                finally:
                    try:
                        WindowOperator.return_front(loan)
                    finally:
                        self._put_cursor_back(cursor, "Run", "前面を借りる前")

    def _respawn_esc_if_open(self, head: str):
        """メニューが開いていて、この窓が前面なら Esc で閉じる（前面でなければ送らない）"""
        if self._respawn_menu_open and WindowOperator.foreground_hwnd() == self._cfg.hwnd:
            WindowOperator.send_keys("esc")
            self._respawn_menu_open = False
            DebugLog.write(f"{head} メニューを閉じた（Esc）")

    def _wait_respawn_line(self, seen: int, round_seq: int, sec: float | None = None,
                           since: float | None = None) -> bool:
        """押した後、「Player respawned」の行を待つ。上限は since（押し終えた時刻。無ければ今）から sec
        （既定 RESPAWN_LOG_WAIT_SEC）。来たら True。止めた・ラウンドが変わった・private でなくなったら False"""
        st = self._st
        deadline = (time.time() if since is None else since) + (config.RESPAWN_LOG_WAIT_SEC if sec is None else sec)
        while st.respawn_seen_seq == seen:
            if time.time() >= deadline or not self._is_running() or st.round_seq != round_seq \
                    or not self.can_operate():
                return False
            time.sleep(config.RUN_RESPAWN_POLL_SEC)
        return True

    def _respawn_close_menu(self, head: str):
        """2 回押しても行が来なかった後始末: 鍵の中で前面を借りてメニューを閉じる（開いていれば）。
        ほかの窓がフリーズ中なら前面を借りない（開いたまま。debug.log に残す）"""
        if not self._respawn_menu_open:
            return
        hwnd = self._cfg.hwnd
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._nobody_else_frozen():
                DebugLog.write(f"{head} ほかの窓のフリーズ中なのでメニューを閉じません（開いたまま）")
                return
            cursor = WindowOperator.cursor_position()
            ok, loan = WindowOperator.borrow_front(hwnd)
            if not ok:
                DebugLog.write(f"{head} 前面にできないのでメニューを閉じません（開いたまま）")
                return
            try:
                self._respawn_esc_if_open(head)
            finally:
                try:
                    WindowOperator.return_front(loan)
                finally:
                    self._put_cursor_back(cursor, "Run", "前面を借りる前")

    def _respawn_stop_before_front(self, round_seq: int) -> str | None:
        """鍵を取った後、前面を借りる前の見張り（前面かどうかはまだ見ない）"""
        if not self._run_still(round_seq):
            return "止めた・ラウンドが変わった"
        if not self._nobody_else_frozen():
            return "frozen"
        return None

    def _place_cursor(self, point, head) -> bool:
        """カーソルを置いて FETCH_CURSOR_SETTLE_SEC 待って読み直す。±FETCH_CURSOR_TOL_PX の外なら置き直す
        （FETCH_CURSOR_TRIES 回まで。アイテム取得 DI と同じ考え）。置けたら True"""
        tol = config.FETCH_CURSOR_TOL_PX
        for tries in range(1, config.FETCH_CURSOR_TRIES + 1):
            WindowOperator.set_cursor_position(point)
            time.sleep(config.FETCH_CURSOR_SETTLE_SEC)
            read = WindowOperator.cursor_position()
            placed = (read is not None and abs(read[0] - point[0]) <= tol and abs(read[1] - point[1]) <= tol)
            DebugLog.write(f"{head} カーソル: 置いた {point} → 読み直し {read}（置き直し {tries - 1} 回）"
                           + ("" if placed else " ずれている"))
            if placed:
                return True
        return False

    # ── 続行ラウンドの後（アイテムを落とす・窓を戻す）──────────
    def remember_continue_window(self):
        """続行ラウンドの始まり（この窓が続行フリーズを張った瞬間）。窓を戻す設定が ON なら、
        戻す先の矩形を覚える。フルスクリーン・最小化なら覚えない（終わりでも戻さない）。
        最大化なら元の矩形（rcNormalPosition。実機で並べた位置だった）を覚える"""
        self._continue_rect = None
        hwnd = self._cfg.hwnd
        if not SharedState.get_continue_restore_window() or not hwnd:
            return
        head = f"[操作] [窓{self._st.window_idx}] 続行ラウンドの始まり:"
        state = WindowOperator.window_state(hwnd)
        if state in (None, "minimized", "fullscreen"):
            DebugLog.write(f"{head} 窓が{ {None: '無い', 'minimized': '最小化', 'fullscreen': 'フルスクリーン'}[state]}"
                           "ので位置を覚えません（終わりでも戻しません）")
            return
        rect = (WindowOperator.normal_rect(hwnd) if state == "maximized"
                else WindowOperator.window_rect(hwnd))
        if rect is None:
            return
        self._continue_rect = rect
        DebugLog.write(f"{head} 窓の位置を覚えた {rect}"
                       + ("（最大化の元の矩形）" if state == "maximized" else ""))

    def after_continue_round(self):
        """続行ラウンドの終わり（この窓の続行フリーズが外れた瞬間。別スレッド）: 窓を始まりの位置へ戻す
        （覚えた矩形があるときだけ。どのインスタンスでも）。アイテムはここでは落とさない（EU: 死んだ・
        生き残ったときに落とす。drop_at_round_end）"""
        rect, self._continue_rect = self._continue_rect, None
        if not self._is_running():
            return
        if rect is not None and SharedState.get_continue_restore_window():
            self._restore_continue_window(rect)

    def drop_at_round_end(self, round_seq: int, why: str):
        """死んだ・生き残った行が来たラウンドで 1 回（EU。LogMonitor._drop_when_round_ends が 1 回にする）:
        /input/DropRight を押して離す。設定 OFF・OSC が使えない・private でない・次のラウンドになった・
        止めた → 落とさない"""
        st = self._st
        head = f"[操作] [窓{st.window_idx}] {why}:"
        if not self._is_running() or not SharedState.get_continue_drop_item():
            return
        if st.round_seq != round_seq:
            DebugLog.write(f"{head} アイテムを落としません（次のラウンドが始まった）")
            return
        self._press_drop(head, f"{why}ので、アイテムを落としました")

    def _press_drop(self, head: str, message: str):
        """/input/DropRight を押して離す。OSC の窓・private のときだけ。持っているかは見ない"""
        if self._osc is None or not self.can_operate():
            DebugLog.write(f"{head} アイテムを落としません（{'OSC が使えない窓' if self._osc is None else 'private でない'}）")
            return
        self._osc.press("/input/DropRight", config.CONTINUE_DROP_PRESS_SEC, stop=self._move_stopped)
        DebugLog.write(f"{head} /input/DropRight を押して離した")
        self._log(message)

    def _restore_continue_window(self, rect):
        """窓を rect（始まりの矩形）の位置へ戻す。フルスクリーンなら前面を借りて Alt+Enter で窓に
        戻してから。最大化なら前面を奪わずに元に戻してから。大きさは変えない（位置だけ）"""
        hwnd = self._cfg.hwnd
        head = f"[操作] [窓{self._st.window_idx}] 続行ラウンドの終わり:"
        state = WindowOperator.window_state(hwnd)
        if state in (None, "minimized"):
            DebugLog.write(f"{head} 窓が{'無い' if state is None else '最小化'}ので戻しません")
            return
        if state == "fullscreen":
            if self._leave_fullscreen_and_move(rect):
                self._log("窓を元の位置に戻しました（フルスクリーンを解除）")
            return
        how = ""
        if state == "maximized":
            if not WindowOperator.restore_without_activating(hwnd):
                return
            how = "（最大化を解除）"
        if self._move_window_back(rect) or how:
            self._log(f"窓を元の位置に戻しました{how}")

    def _leave_fullscreen_and_move(self, rect) -> bool:
        """前面を借りて Alt+Enter → 窓に戻るのを待つ → 位置だけ戻す → 前面を返す。窓に戻ったら True"""
        hwnd = self._cfg.hwnd
        head = f"[操作] [窓{self._st.window_idx}] 続行ラウンドの終わり:"
        with SharedState._GLOBAL_ACTION_LOCK:
            cursor = WindowOperator.cursor_position()
            ok, loan = WindowOperator.borrow_front(hwnd)
            if not ok:
                DebugLog.write(f"{head} 前面にできないのでフルスクリーンを解除しません")
                return False
            try:
                WindowOperator.send_keys("alt+enter")
                deadline = time.time() + config.CONTINUE_WINDOWED_WAIT_SEC
                while WindowOperator.window_state(hwnd) == "fullscreen":
                    if time.time() >= deadline:
                        DebugLog.write(f"{head} Alt+Enter の後 {config.CONTINUE_WINDOWED_WAIT_SEC:.0f} 秒で"
                                       "窓に戻りません → 位置は動かしません")
                        return False
                    time.sleep(config.CONTINUE_WINDOWED_POLL_SEC)
                DebugLog.write(f"{head} フルスクリーンを解除した（Alt+Enter）")
                self._move_window_back(rect)
                return True
            finally:
                try:
                    WindowOperator.return_front(loan)
                finally:
                    self._put_cursor_back(cursor, "続行ラウンドの終わり", "前面を借りる前")

    def _move_window_back(self, rect) -> bool:
        """位置が rect の左上と違えば、位置だけ戻す（大きさは変えない）。動かしたら True"""
        hwnd = self._cfg.hwnd
        head = f"[操作] [窓{self._st.window_idx}] 続行ラウンドの終わり:"
        now = WindowOperator.window_rect(hwnd)
        if now is None:
            return False
        if (now[0], now[1]) == (rect[0], rect[1]):
            DebugLog.write(f"{head} 窓の位置は始まりのまま {now}")
            return False
        size, was = (now[2] - now[0], now[3] - now[1]), (rect[2] - rect[0], rect[3] - rect[1])
        if size != was:
            DebugLog.write(f"{head} 窓の大きさが始まりと違う（始まり {was[0]}x{was[1]}・今 {size[0]}x{size[1]}）"
                           " → 大きさは変えずに位置だけ戻す")
        return WindowOperator.move_window(hwnd, rect[0], rect[1])

    # ── 速度によるラウンド種別の検知 ────────────
    #  判定（do_speed_detect）と横移動（do_speed_strafe）は独立している。
    #  判定は受信するだけなのでどのインスタンスでも動く。ラウンドデータの
    #  取得（誰が Begin を押しても出る）で始めて、ラウンド突入まで見続ける。
    #  横移動はマクロなので private で、自分の Begin が通った（本物の
    #  Verified）ときだけ動かす。Begin 前に動くと Begin を押せなくなる。

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
        # まず OSCQuery で待ち、届かなければ VRChat の送信ポート（ログの --osc= から取れていればそれ。
        # 受信+1 とは限らない。ToNUtils が立てた窓は 9003/9004 だった）を開く（DS）
        out_port = self._cfg.osc_out_port or self._cfg.osc_port + 1
        receiver = OSCReceiver.VelocityReceiver(self._cfg.hwnd, out_port, self._log,
                                                window_label=f"窓{self._st.window_idx}")
        if not receiver.start():
            self._log("速度受信を開始できないため種別検知を無効化します")
            self._speed_ready.set()
            return False
        self._receiver = receiver
        self._speed_ready.set()
        return True

    @property
    def speed_receiving(self) -> bool:
        """この窓の速さを受け取れているか（1件でも受けた）。受け取れていない間は速度検知の
        横移動をしない・「速度検知 開始」と言わない（DN: 横移動するのに検知できない、を出さない）"""
        receiver = self._receiver
        return receiver is not None and receiver.ever_received

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
        if not SharedState.get_speed_detect() or self._hands_free():
            return          # 放置モードの窓では動かさない（途中でトグルを入れた場合の念のため）
        receiver = self._receiver
        if receiver is None:
            return
        round_seq = st.round_seq
        instance_seq = st.instance_seq
        # 止めるのはラウンド突入。横移動は後から来る Verified で始まるので、
        # 時間で打ち切ると取りこぼす。上限は暴走防止だけ
        deadline = time.time() + config.SPEED_PROBE_MAX_SEC
        try:
            while (self._is_running() and not st.in_round
                   and st.round_seq == round_seq
                   and st.instance_seq == instance_seq
                   and time.time() <= deadline):
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
        アイテムロスト後・入室時の Plush のやり直しが控えているときは動かさない
        （拾いに行く操作の邪魔をしないため。やり直しが横移動と重なると柵を越えられない。FN）。
        """
        st = self._st
        if (not SharedState.get_speed_detect() or not self._cfg.osc_port or self._hands_free()
                or not self.can_operate()):
            return
        if st.waiting_for_equip:
            self._log("アイテムロスト後のため速度検知の横移動はしません")
            return
        if self._plush_retries > 0:
            self._log("Plush のやり直しのため速度検知の横移動はしません")
            return
        # 受信の準備が済むまで待つ
        if not self._speed_ready.wait(timeout=config.SPEED_READY_TIMEOUT_SEC):
            self._log("速度受信の準備を待てませんでした（移動は行います）")
        if not self.speed_receiving:
            # 速さが届いていない窓で動いても検知できない（DN。依頼者に届いた不具合の報告）
            self._trace("[操作] 速度検知の横移動をしない（速さを受け取れていない）")
            return
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
        # 受信が途絶えると stable_for は時間経過だけで伸びるため、凍結した値が
        # 「安定値」として通ってしまう。読む前に弾く。
        if not receiver.alive:
            return
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
        if self._auto_begin_active() and not self._freeze_enabled_for(kind):
            # ツールが回している窓で、フリーズしない種別なら知らせる必要が無い
            # （アイテムを取りに行く時間を作らない＝人が動く場面ではない）。
            # 人が遊んでいる窓では、フリーズ設定に関わらず鳴らす
            return
        PlaySound.play_sound(self._speed_voice(kind), kind=kind)

    @staticmethod
    def _freeze_enabled_for(kind: str) -> bool:
        """その種別でフリーズする設定か。音声とフリーズで同じものを見るための1か所"""
        if kind == "8pages":
            return SharedState.get_freeze_on_8pages()
        if kind == "punish":
            return SharedState.get_freeze_on_punish()
        return False

    def _freeze_for_speed_kind(self, kind: str):
        """設定がONの種別なら全窓を止め、アイテムを取りに行く時間を作る。

        解除条件は種別で違う（8 Pages=アイテム取得 / Punished=ラウンド開始）ので、
        どちらで張ったかを覚えておく。平常では止めない。
        """
        st = self._st
        if not self._freeze_enabled_for(kind):
            return
        if kind == "8pages":
            message = "⏸ 8 Pages 検知 → 全窓フリーズ（アイテム取得で解除）"
        else:
            message = "⏸ Punished 検知 → 全窓フリーズ（ラウンド開始で解除）"
        st.speed_freeze_kind = kind
        # 前面化するのは最初に張った窓だけ。後から張った窓が奪うと、
        # プレイヤーが操作している最中の窓を横取りしてしまう。
        first = SharedState.get_speed_freeze_count() == 0
        SharedState.speed_freeze_start(st)
        self._log(message)
        if first:
            self._focus_for_speed_freeze()

    def _nothing_frozen_but_mine(self) -> bool:
        """アイテムロストの前面化＋音声を出してよいか（Begin の判定には使わない）。

        続行・速度検知・突入のフリーズは、ほかの窓が張っているあいだ待つ。
        装備待ちは張った順に1窓ずつ: 自分が列の先頭なら出してよい。数で見ると、
        2窓が同じ瞬間に張ったとき両方が「ほかにもいる」と見て、どちらも出さなかった。
        自分でフリーズを張ってから見ているので、nothing_frozen() は使えない
        """
        if not (SharedState.CONTINUE_ROUND_EVENT.is_set()
                and SharedState.SPEED_FREEZE_EVENT.is_set()
                and SharedState.ROUND_FREEZE_EVENT.is_set()):
            return False
        if SharedState.EQUIP_WAIT_EVENT.is_set():
            return True
        return SharedState.is_first_in_equip_queue(self._st)

    def _focus_for_speed_freeze(self):
        """どの窓を操作すればよいか分かるように前面化する。

        付随機能なので、フォーカスを取れなくてもフリーズは維持する。
        既存の作法どおりロックを取ってから切り替える（数秒待たされてもよい）。
        """
        with SharedState._GLOBAL_ACTION_LOCK:
            ok, loan = WindowOperator.borrow_front(self._cfg.hwnd)
            if ok:
                SharedState.keep_front_loan(self._st, loan)   # 速度検知が解けたら返す
                self._log("この窓を前面化しました（速度検知フリーズ）")
            else:
                self._log("⚠ 前面化に失敗（フリーズは継続）")

    # ── AFK防止ループ ─────────────────────────

    def _afk_not_mine(self, round_seq: int) -> bool:
        """AFK 回避の見張りが手を出さない: このラウンドを自爆する（キャンセルしていない）・DTM/Waldo や Glorbo の
        AFK 解除ループが動いている（二重にしない）"""
        st = self._st
        suicide = st.suicide_seq == round_seq and st.suicide_cancelled_round != round_seq
        return suicide or st.glorbo_afk or st.is_open_special_round_round

    def _afk_speed_note(self, speed, peak, now) -> str:
        """AFK 回避で動かす・鳴らすときの速さの記録（FG: 動いているのに鳴った件を追うため）"""
        receiver = self._receiver
        if receiver is None:
            return "速さの受信器なし"
        try:
            ever = receiver.ever_received
            last = receiver.last_received_at
        except Exception:
            return "速さの受信器の値を読めない"
        since = f"{now - last:.1f} 秒" if last else "なし"
        moved = self._receiver_moved_at()
        return (f"最後に受けた速さ {speed if speed is None else f'{speed:.2f}'}・最後に受けてから {since}・"
                f"受けたことが{'ある' if ever else 'ない'}・この {config.AFK_STILL_SEC:.0f} 秒の最大 "
                f"{'なし' if peak is None else f'{peak:.2f}'}・最後に動いたと見てから "
                f"{f'{now - moved:.1f} 秒' if moved else 'なし'}")

    def _receiver_moved_at(self) -> float:
        """受信器が最後に動いたと見た時刻（無ければ 0.0）"""
        try:
            return float(getattr(self._receiver, "last_moved_at", 0.0) or 0.0)
        except Exception:
            return 0.0

    def do_afk_watch(self, round_seq: int):
        """ツールが自爆しないラウンドの AFK 回避（EZ。依頼者 2026-10-15）。生きている間、速さ（OSC の受信。ツールが
        動かした分も動いたに数える）が AFK_MOVING_SPEED 未満のまま AFK_STILL_SEC たったら、private の窓は微移動
        （DTM/Waldo の AFK 解除ループと同じ動かし方）、それ以外は警告音。どちらの後も数え直す。速さを一度も
        受けていない窓は鳴らさない・動かさない（FG）。死んだ・ラウンドが終わった・止めた → やめる"""
        st = self._st
        still_since = last_check = time.time()
        peak = None                         # この 70 秒で見た速さの最大（FG: 記録の行）
        while True:
            time.sleep(config.AFK_POLL_SEC)
            if (not self._is_running() or not st.in_round or st.round_seq != round_seq
                    or st.died_this_round):
                return
            now = time.time()
            speed = self._receiver_speed()
            # 前の確認から今までに動いたか（FG: 跳んで着地すると、確認の瞬間には 0 に戻っている）
            moved = self._receiver_moved_at() > last_check
            last_check = now
            if (self._afk_not_mine(round_seq) or moved
                    or (speed is not None and speed >= config.AFK_MOVING_SPEED)):
                still_since, peak = now, None
                continue
            if speed is not None:
                peak = speed if peak is None else max(peak, speed)
            if now - still_since < config.AFK_STILL_SEC:
                continue
            still_since = now
            DebugLog.write(f"[操作] [窓{st.window_idx}] AFK 回避: {self._afk_speed_note(speed, peak, now)}")
            peak = None
            if speed is None:
                # 速さを一度も受けていない窓（受信器が無い・届かない）は、動いているか分からないので鳴らさない・
                # 動かさない（FG: 依頼者の判断 A。0 が届いた後に何も来ないのは止まっているのと同じなので、そちらは鳴らす）
                DebugLog.write(f"[操作] [窓{st.window_idx}] AFK 回避: 速さを受けていない → 鳴らさない・動かさない")
                continue
            if self.can_operate():
                self.move("forward", config.OPERATOR_WAIT_SEC)
                self._log(f"AFK 回避: {config.AFK_STILL_SEC:.0f} 秒動いていない → 微移動")
            else:
                PlaySound.play_sound(getattr(self._cfg, "voice_afk", ""), kind="afk")
                self._log(f"AFK 回避: {config.AFK_STILL_SEC:.0f} 秒動いていない → 警告音")

    def do_open_special_round_loop(self, glorbo: bool = False):
        """
        ラウンド中60秒ごとに移動キーをわずかに押す（ジャンプ代替）。
        - フォーカス切り替えは SharedState._GLOBAL_ACTION_LOCK 内でのみ行う
          → 自爆・Begin操作中にフォーカスを奪わない
        - 停止条件: _running=False / in_round=False /
                    is_open_special_round_round=False / open_special_round_wins達成
        - glorbo=True（Glorbo の続行）は 3 勝・is_open_special_round_round を見ない。
          止まるのはラウンド終了・停止・st.glorbo_afk が外れたとき
        """
        st = self._st
        self._log(f"AFK解除ループ開始（{config.OPEN_SPECIAL_ROUND_INTERVAL_SEC}秒ごと）")
        elapsed = 0.0
        CHECK_INTERVAL = 1.0

        def _should_stop() -> bool:
            if not self._is_running() or not st.in_round:
                return True
            if glorbo:
                return not st.glorbo_afk
            return (
                not st.is_open_special_round_round
                or not RoundDecision.open_special_active(
                    st.open_special_round_wins,
                    getattr(self._cfg, "cancel_afk_after_unlock", False))
            )

        while not _should_stop():
            time.sleep(CHECK_INTERVAL)
            elapsed += CHECK_INTERVAL
            if elapsed < config.OPEN_SPECIAL_ROUND_INTERVAL_SEC:
                continue
            elapsed = 0.0
            if _should_stop():
                break
            if not self.can_operate():
                continue            # private でなければ送らない（ループは条件どおりに終わる）
            # OSCでも背面キーでもフォーカス不要。他窓の操作を妨げない
            self.move("forward", config.OPERATOR_WAIT_SEC)
            self._log("移動キー送信（ジャンプ代替）")

        self._log("AFK解除ループ終了")


class _FetchMouse:
    """アイテム自動取得のマウス。前面の窓（この窓）へ相対移動と左クリックを送る"""

    @staticmethod
    def move_rel(dx: int, dy: int):
        import pydirectinput
        pydirectinput.moveRel(dx, dy, relative=True, _pause=False)



# ── Begin 前の移動の実測（記録だけ）──────────────────
MOTION_INTERVAL_SEC = 0.05      # 速さと接地を読む間隔
MOTION_TAIL_SEC = 0.6           # 離してからこれだけ後まで読む
MOTION_START_SPEED = 1.0        # 動き出し（これを超えた最初の時刻）
MOTION_STALL_SPEED = 0.3        # 止まり（入力中にこれ未満）
MOTION_SERIES_STEP = 0.1        # 速さの列の間隔
MOTION_SERIES_MAX = 40


def summarize_motion(samples, released_at) -> str:
    """samples は [(送り始めからの秒, 速さ or None, 接地 or None)]、released_at は離した時刻（同じ基準）。
    debug.log の1行の「実測 …」を返す"""
    got = [(t, s, g) for t, s, g in samples if s is not None]
    if not got:
        return "実測なし（速さを受信していません）"
    distance = 0.0
    for (t0, s0, _), (t1, _s1, _) in zip(got, got[1:]):
        distance += s0 * (t1 - t0)
    top = max(s for _, s, _ in got)
    start = next((t for t, s, _ in got if s > MOTION_START_SPEED), None)
    stalls = []
    if start is not None:
        begun = None
        for t, s, _ in got:
            if t <= start or t > released_at:
                continue
            if s < MOTION_STALL_SPEED:
                begun = t if begun is None else begun
            elif begun is not None:
                stalls.append((begun, t))
                begun = None
        if begun is not None:
            stalls.append((begun, released_at))
    changes = []
    last = None
    for t, _s, g in samples:
        if g is None:
            continue
        if last is not None and g != last:
            changes.append(f"{t:.2f}秒→{'1' if g else '0'}")
        last = g
    series = []
    next_t = 0.0
    for t, s, _ in got:
        if t + 1e-9 >= next_t and len(series) < MOTION_SERIES_MAX:
            series.append(round(float(s), 1))
            next_t += MOTION_SERIES_STEP
            while next_t <= t:
                next_t += MOTION_SERIES_STEP
    return (f"実測 距離 {distance:.2f}・最高 {top:.2f}・"
            f"動き出し {'なし' if start is None else f'{start:.2f}秒'}・"
            f"止まり {'、'.join(f'{a:.2f}〜{b:.2f}秒' for a, b in stalls) or 'なし'}・"
            f"接地 {'、'.join(changes) or 'なし'}・"
            f"速さ {series}")


def counted_note(extension: float, how: str, repressed: int = 0) -> str:
    """押している時間をどう数えたか（実測の行の後ろに足す）"""
    text = (f"・動き出しから数えた（+{extension:.2f} 秒）" if how == "動き出し"
            else f"・押し始めから数えた（{how}）")
    return text + (f"・押し直し {repressed} 回" if repressed else "")


class MotionSampler:
    """移動の間、速度受信器から速さと接地を読み、離してから MOTION_TAIL_SEC 後に1行書く。
    移動のスレッドは待たせない（start / release を呼ぶだけ）"""

    def __init__(self, receiver, write, clock=time.monotonic, sleep=time.sleep,
                 interval=None, tail=None):
        self._receiver = receiver
        self._write = write
        self._clock = clock
        self._sleep = sleep
        self._interval = MOTION_INTERVAL_SEC if interval is None else interval
        self._tail = MOTION_TAIL_SEC if tail is None else tail
        self._started = None
        self._released = None
        self.suffix = ""            # 実測の行の後ろに足す（EF: 動き出しから数えた延び）
        self.samples = []
        self.thread = None

    def start(self):
        self._started = self._clock()
        if self._receiver is None:
            return              # OSC の窓でない（release で「実測なし」を書く）
        self.thread = TaskStats.thread("MotionSampler._run", self._run, daemon=True)
        self.thread.start()

    def release(self):
        self._released = self._clock()
        if self.thread is None:
            self._write(summarize_motion([], 0.0) + self.suffix)

    def distance_so_far(self):
        """送り始めから今までに動いた距離（速さ × 経過の積分。summarize_motion の「距離」と同じ測り方）。
        速さを 1 件も受け取れていなければ None。最後に読んでから今までは、最後の速さで動いたとみなす
        （読む間隔の分を取りこぼさない。読むスレッドが止まっても増え続けないよう、延ばすのは間隔の 2 回分まで）"""
        got = [(t, s) for t, s, _g in list(self.samples) if s is not None]
        if not got:
            return None
        distance = sum(s0 * (t1 - t0) for (t0, s0), (t1, _s1) in zip(got, got[1:]))
        last_t, last_s = got[-1]
        gap = self._clock() - self._started - last_t
        return distance + last_s * min(max(0.0, gap), self._interval * 2)

    def _read(self):
        try:
            return self._receiver.speed, self._receiver.grounded_or_none
        except Exception:
            return None, None

    def _run(self):
        try:
            while True:
                now = self._clock()
                speed, grounded = self._read()
                self.samples.append((now - self._started, speed, grounded))
                released = self._released
                if released is not None and now >= released + self._tail:
                    break
                if now - self._started > 60:
                    break           # 離した記録が来ないまま（念のため）
                self._sleep(self._interval)
            released = self._released if self._released is not None else self._clock()
            self._write(summarize_motion(self.samples, released - self._started) + self.suffix)
        except Exception:
            DebugLog.exception("ActionExecutor.MotionSampler")


class _FrontOnlyMouse(_FetchMouse):
    """アイテム取得のマウス。送る前・クリックの前に前面がこの窓かを確かめ、違えば送らずにやめる。
    相対移動とクリックは前面の窓に届くので、ほかの窓（人が操作している続行ラウンドなど）の視点を回さない"""

    def __init__(self, hwnd: int, stopped=None, allowed=None):
        self._hwnd = hwnd
        self._stopped = stopped     # 送る前にも止まる条件（ほかの窓のフリーズなど）を見る
        self._allowed = allowed     # 送ってよいインスタンスか（ActionExecutor.can_operate）。戻すときも見る
        self.restoring = False      # 視点を戻すときは、止まる条件は見ない（前面かだけ見る）

    def _check_front(self):
        if self._allowed is not None and not self._allowed():
            raise ItemFetch.Stopped("not_private")
        if self._stopped is not None and not self.restoring:
            reason = self._stopped()
            if reason:
                raise ItemFetch.Stopped(reason)
        if WindowOperator.foreground_hwnd() != self._hwnd:
            raise ItemFetch.Stopped("front_lost")

    def move_rel(self, dx: int, dy: int):
        self._check_front()
        _FetchMouse.move_rel(dx, dy)

    def tab_down(self):
        """Tab を押す。前面でない窓には押さない（Tab は前面の窓へ届く）"""
        self._check_front()
        WindowOperator.press_tab()

    def tab_up(self):
        WindowOperator.release_tab()

    def drag(self, start, end, steps: int, step_sec: float, on_mid=None) -> bool:
        """（Tab を押したまま）撮影の start へカーソルを置いて左ボタンを押し、steps 段で end まで
        動かして離す（DZ: 店の一覧のスクロールバー）。左ボタンは何があっても離す。前面でなくなれば
        front_lost（離してから）。on_mid は真ん中の段（steps // 2）で 1 回呼ぶ（記録の撮影用。
        例外は DebugLog に残して続ける）"""
        self._check_front()
        origin = WindowOperator.window_origin(self._hwnd)
        if origin is None:
            raise ItemFetch.Stopped("front_lost")
        sx, sy = start[0] + origin[0], start[1] + origin[1]
        ex, ey = end[0] + origin[0], end[1] + origin[1]
        WindowOperator.set_cursor_position((int(round(sx)), int(round(sy))))
        time.sleep(config.FETCH_CURSOR_SETTLE_SEC)
        self._check_front()
        WindowOperator.mouse_down()
        try:
            for i in range(1, steps + 1):
                self._check_front()
                t = i / steps
                WindowOperator.set_cursor_position((int(round(sx + (ex - sx) * t)), int(round(sy + (ey - sy) * t))))
                time.sleep(step_sec)
                if on_mid is not None and i == steps // 2:
                    try:
                        on_mid()
                    except Exception:
                        DebugLog.exception("ActionExecutor._FrontOnlyMouse.drag.on_mid")
        finally:
            WindowOperator.mouse_up()
        return True

    def click_at(self, x: float, y: float) -> bool:
        """（Tab を押したまま）撮影の (x, y)（＋窓の左上 ＝ 画面の点）へカーソルを置き、
        FETCH_CURSOR_SETTLE_SEC 待って読み直す。±FETCH_CURSOR_TOL_PX の外（VRChat に真ん中へ
        戻されたなど）なら置き直す（FETCH_CURSOR_TRIES 回まで）。置けていれば前面を確かめてクリック。
        置けなければクリックせずに False。前面でなくなっていれば front_lost"""
        self._check_front()
        origin = WindowOperator.window_origin(self._hwnd)
        if origin is None:
            raise ItemFetch.Stopped("front_lost")
        point = (int(round(x + origin[0])), int(round(y + origin[1])))
        tol = config.FETCH_CURSOR_TOL_PX
        read = None
        for tries in range(1, config.FETCH_CURSOR_TRIES + 1):
            self._check_front()
            WindowOperator.set_cursor_position(point)
            time.sleep(config.FETCH_CURSOR_SETTLE_SEC)
            read = WindowOperator.cursor_position()
            placed = (read is not None and abs(read[0] - point[0]) <= tol
                      and abs(read[1] - point[1]) <= tol)
            DebugLog.write(f"[操作] カーソル: 置いた {point} → 読み直し {read}（置き直し {tries - 1} 回）"
                           + ("" if placed else " ずれている"))
            if placed:
                break
        else:
            DebugLog.write(f"[操作] クリックしない（カーソルを {point} へ {config.FETCH_CURSOR_TRIES} 回置けない）")
            return False
        self._check_front()             # 待つ間に前面が変わったら押さない
        DebugLog.write(f"[操作] クリック（Tab を押したまま {point}）")
        WindowOperator.mouse_click(ItemFetch.CLICK_SEC, pause=False)
        return True
