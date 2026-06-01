import time
import threading
from typing import Callable

import config
import SharedState
import WindowOperator
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

    # ── ヘルパー ──────────────────────────────

    def announce_item_lost_once(self):
        """アイテムロスト音声を一度だけ再生する"""
        if self._st.item_lost_announced:
            return
        self._st.item_lost_announced = True
        PlaySound.play_sound(self._cfg.voice_item_lost)

    def focus(self):
        """この窓にフォーカスを当てる"""
        hwnd = self._cfg.hwnd
        WindowOperator.focus_window(hwnd)
        self._log(f"フォーカス切替 → HWND={hwnd:#010x}")

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
            st._skip_time = time.time()
            self._log(f"自爆実行中 ({config.SUICIDE_HOLD_SEC}秒)…")
            self.focus()
            time.sleep(config.SUICIDE_FOCUS_SETTLE_SEC)
            WindowOperator.hold_key(SharedState.get_suicide_key(), config.SUICIDE_HOLD_SEC)

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
        time.sleep(config.BEGIN_WAIT_SEC)
        # Beginはフレ/フレ+/招待/招待+のみ
        if st.instance_type != config.INSTANCE_PRIVATE:
            return
        if not self._is_running() or st.in_round:
            self._log("Begin キャンセル（停止 or 次のラウンドが開始）")
            return

        # 他窓のフリーズ解除を待つ（自分がアイテムロスト中の場合は除く）
        if not st.is_continue_round and not st.waiting_for_equip:
            while self._is_running():
                eq_ok  = SharedState.EQUIP_WAIT_EVENT.is_set()
                con_ok = SharedState.CONTINUE_ROUND_EVENT.is_set()
                if eq_ok and con_ok:
                    break
                if not eq_ok:
                    self._log("他窓の装備待ち中 → フリーズ")
                if not con_ok:
                    self._log("他窓の続行/霧ラウンド中 → フリーズ")
                SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
                SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
            if not self._is_running():
                return

        # アイテムロスト状態なら他窓の解除を待ってから即フリーズ
        if st.waiting_for_equip:
            if not st.is_continue_round:
                while self._is_running():
                    if SharedState.EQUIP_WAIT_EVENT.is_set() and SharedState.CONTINUE_ROUND_EVENT.is_set():
                        break
                    SharedState.EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    SharedState.CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                if not self._is_running():
                    return
            SharedState.EQUIP_WAIT_EVENT.clear()
            self._log("⚠ アイテムロスト → 全窓フリーズ（Beginへ向かいます）")

        # ── フェーズ1: Begin前移動 + 初回クリック（ロック内）──
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._is_running() or st.in_round:
                self._log("Begin キャンセル（ロック待ち中に停止 or 次のラウンドが開始）")
                if st.waiting_for_equip and st.in_round:
                    SharedState.EQUIP_WAIT_EVENT.set()
                    self._log("ラウンド開始によりフリーズ解除 → 装備待ちへ")
                elif not st.waiting_for_equip:
                    return
            if not st.waiting_for_equip:
                if not st.is_continue_round:
                    if not SharedState.CONTINUE_ROUND_EVENT.is_set():
                        self._log("Begin キャンセル（ロック取得後にフリーズ検出）")
                        return
            if not st.in_round:
                self.focus()
                if st.waiting_for_equip:
                    self.announce_item_lost_once()
                if not (st.round_type in config.LATE_ROUND):
                    WindowOperator.hold_key("w", config.BEGIN_FORWARD_SEC)
                    WindowOperator.hold_key("a", config.BEGIN_LEFT_SEC)
                else:
                    WindowOperator.hold_key("w", config.BEGIN_FORWARD_SEC_LATER)
                    WindowOperator.hold_key("a", config.BEGIN_LEFT_SEC_LATER)
                time.sleep(0.1)
                if not st.in_round:
                    self._log("Beginクリック")
                    WindowOperator.click()

        # ── フェーズ2a: アイテムロスト装備待ち（ロック外）──
        if st.waiting_for_equip:
            self._log("アイテム装備を待っています… （装備すると自動再開）")
            while st.waiting_for_equip and self._is_running():
                time.sleep(0.3)
            if not self._is_running():
                SharedState.EQUIP_WAIT_EVENT.set()
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
                if not st.is_continue_round and not st.waiting_for_equip:
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
                with SharedState._GLOBAL_ACTION_LOCK:
                    if not self._is_running() or st.in_round or st.begin_done:
                        return
                    self.focus()
                    if attempt % 2 == 1:
                        WindowOperator.hold_key("a", config.BEGIN_RETRY_LEFT_SEC)
                    else:
                        WindowOperator.hold_key("d", config.BEGIN_RETRY_RIGHT_SEC)
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
            with SharedState._GLOBAL_ACTION_LOCK:
                if _should_stop():
                    break
                WindowOperator.focus_window(self._cfg.hwnd)
                WindowOperator.hold_key("w", config.OPERATOR_WAIT_SEC)
            self._log("移動キー送信（ジャンプ代替）")

        self._log("AFK解除ループ終了")
