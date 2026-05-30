import time
import threading
from typing import Optional

import config
import SharedState
import WindowOperator
import PlaySound
import ConnectDB
import ReadJson
import LogParser
import RoundDecision
from State import WindowConfig, WindowState


# ═══════════════════════════════════════════════
#  排他制御
# ═══════════════════════════════════════════════
# 自爆キー（GUIから変更可能）
_SUICIDE_KEY = config.SELF_SUICIDE_KEY
_SUICIDE_KEY_LOCK = threading.Lock()

def get_suicide_key() -> str:
    with _SUICIDE_KEY_LOCK:
        return _SUICIDE_KEY

def set_suicide_key(key: str):
    global _SUICIDE_KEY
    with _SUICIDE_KEY_LOCK:
        _SUICIDE_KEY = key

# 装備待ちイベント（アイテムロストラウンド後のBegin押下から装備まで全窓フリーズ）
# set() 状態 = 通常動作可能、clear() 状態 = 装備待ち中（他窓のアクションをブロック）
_EQUIP_WAIT_EVENT = threading.Event()
_EQUIP_WAIT_EVENT.set()   # 初期値は通常動作可能

_ITEM_GET_BEGIN_MODE = False
_ITEM_GET_BEGIN_LOCK = threading.Lock()

def get_item_get_begin_mode() -> bool:
    with _ITEM_GET_BEGIN_LOCK:
        return _ITEM_GET_BEGIN_MODE

def set_item_get_begin_mode(val: bool):
    global _ITEM_GET_BEGIN_MODE
    with _ITEM_GET_BEGIN_LOCK:
        _ITEM_GET_BEGIN_MODE = val

_HANDS_FREE = False
_HANDS_FREE_LOCK = threading.Lock()

def get_hands_free() -> bool:
    with _HANDS_FREE_LOCK:
        return _HANDS_FREE

def set_hands_free(val: bool):
    global _HANDS_FREE
    with _HANDS_FREE_LOCK:
        _HANDS_FREE = val

# 続行・霧ラウンド中フリーズイベント
# set() = 通常動作可能、clear() = 続行/霧ラウンド中（他窓をブロック）
_CONTINUE_ROUND_EVENT = threading.Event()
_CONTINUE_ROUND_EVENT.set()   # 初期値は通常動作可能
_CONTINUE_ROUND_COUNT = 0     # 続行ラウンド中の窓数カウンター
_CONTINUE_ROUND_LOCK  = threading.Lock()  # カウンター操作用ロック

def _continue_round_start():
    """続行ラウンド開始：カウンターを増やしてフリーズ"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT += 1 # 続行が二つ来た時、片方が死んだらフリーズ解除される現象があり、それを回避するため
        _CONTINUE_ROUND_EVENT.clear()

def _continue_round_end():
    """続行ラウンド終了：カウンターを減らし、0になったらフリーズ解除"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT = max(0, _CONTINUE_ROUND_COUNT - 1)
        if _CONTINUE_ROUND_COUNT == 0:
            _CONTINUE_ROUND_EVENT.set()

def _continue_round_reset():
    """停止時など強制リセット"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT = 0
        _CONTINUE_ROUND_EVENT.set()
        
def format_terror_ids(ids: list[int]) -> str:
    names = []
    for tid in ids:
        name = ReadJson.terror_name(tid, config.TERRORS)
        names.append(name if name else f"ID:{tid}")
    return ", ".join(names)


# ═══════════════════════════════════════════════
#  ログ監視ワーカー
# ═══════════════════════════════════════════════
class LogMonitor:
    def __init__(self, cfg: WindowConfig, keepOn_set: dict, logger, window_idx: int = 0):
        self.cfg = cfg
        self.keepOn_set = keepOn_set
        self.logger = logger
        self.window_idx = window_idx
        self.st = WindowState()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        # 過去ログからインスタンスタイプを検出（ワールド入室後の起動に対応）
        self._detect_instance_from_log()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _detect_instance_from_log(self):
        if not self.cfg.log_path or not self.cfg.log_path.exists():
            return
        try:
            with open(self.cfg.log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            found_user = False
            found_instance = False
            for line in reversed(lines):
                event = LogParser.parse(line)

                if not found_user and event and event.kind == LogParser.EVENT_USER_AUTH:
                    self._log(f"UserID検出: {event.user_id}")
                    self.st.transformed_uid = ConnectDB.send_Users(event.user_id)
                    self._log(f"transformed_uid: {self.st.transformed_uid}")
                    found_user = True

                if not found_instance and event and event.kind == LogParser.EVENT_JOINING:
                    suffix = event.suffix
                    if f"group({config.HOSHIIMO_GROUP_ID})" in suffix:
                        itype = config.INSTANCE_HOSHIIMO
                    elif "~group(" in suffix:
                        itype = config.INSTANCE_OTHER_GROUP
                    elif "~friends" in suffix or "~hidden" in suffix or "~private" in suffix:
                        itype = config.INSTANCE_PRIVATE
                    else:
                        itype = config.INSTANCE_PUBLIC
                    SharedState.set_instance_type(itype)
                    self._log(f"インスタンスタイプ検出: {itype}")
                    found_instance = True

                if found_user and found_instance:
                    return
        except Exception as e:
            self._log(f"検出エラー: {e}")

    def stop(self):
        self._running = False

    def _log(self, msg: str):
        self.logger(f"[窓{self.window_idx}] {msg}")

    def _announce_item_lost_once(self):
        if self.st.item_lost_announced:
            return
        self.st.item_lost_announced = True
        PlaySound.play_sound(self.cfg.voice_item_lost)

    # ── メインループ ──────────────────────────
    def _run(self):
        cfg = self.cfg
        if not cfg.log_path or not cfg.log_path.exists():
            self._log(f"ログが見つかりません: {cfg.log_path}")
            return
        self.st.log_pos = cfg.log_path.stat().st_size
        self._log("監視開始")
        while self._running:
            try:
                with open(cfg.log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self.st.log_pos)
                    chunk = f.read()
                    self.st.log_pos = f.tell()
                if chunk:
                    for line in chunk.splitlines():
                        self._process(line)
            except Exception as e:
                self._log(f"読み取りエラー: {e}")
            time.sleep(config.LOG_POLL_INTERVAL)

    # ── ログ行処理 ────────────────────────────
    def _process(self, line: str):
        st = self.st
        event = LogParser.parse(line)
        if not event:
            return

        if event.kind == LogParser.EVENT_BEGIN_DONE:
            st.begin_done = True
            self._log("✅ Connecting")
            # アイテムロスト中のBegin確認
            if st.waiting_for_equip:
                if st.item_id:
                    st.waiting_for_equip = False
                    def _delayed_release(self=self):
                        time.sleep(2.0)
                        _EQUIP_WAIT_EVENT.set()
                        self._log("✅ 全窓フリーズ解除")
                    threading.Thread(target=_delayed_release, daemon=True).start()
            return

        if event.kind == LogParser.EVENT_ROUND_START:
            if st.is_continue_round:
                st.is_continue_round = False
                _continue_round_end()
            st.in_round        = True
            st.round_type      = event.round_type
            st.terror_ids      = []
            st.map_id          = event.map_id
            st.fog             = False
            st.begin_done      = False
            st.is_OpenSpecialRound_round   = False
            st.item_lost_announced = False
            # アイテムロスト中にラウンドが始まったらフリーズ解除
            # （has_item=Falseのまま → 次のVerified Round Endで再フリーズ）
            if st.waiting_for_equip:
                st.waiting_for_equip = False
                _EQUIP_WAIT_EVENT.set()
                self._log("一時的にアイテムロストフリーズを解除")

            if st.round_type == "Run":
                # Runは死亡してアイテムロスト対応へ
                st.is_continue_round = False
                self._log(f"Round: {st.round_type} 【死亡待ち・アイテム購入予定】")
                return

            if st.round_type == "Fog":
                if not get_hands_free():
                    # Fog：taking place時点で即続行確定・他窓フリーズ開始
                    st.is_continue_round = True
                    _continue_round_start()
                    PlaySound.play_sound(self.cfg.voice_fog)
                    self._log(f"開始: {st.round_type} 【他窓フリーズ開始】")
                return
            
            else:
                self._log(f"開始: {st.round_type}")
            return

        if event.kind == LogParser.EVENT_KILLERS_SET:
            # 特殊ラウンドを経験したら3勝扱い（OpenSpecialRound_completed=True）
            if st.round_type in config.SPECIAL_ROUND:
                st.OpenSpecialRound_wins = config.OpenSpecialRound_TARGET_WINS
            if not (st.round_type == "Alternate" and event.round_type == "Classic"): # AF期間中は極まれに偽Classicがある
                st.round_type = event.round_type
            self._on_killers(event.terror_ids or [], st.round_type, revealed=False)
            return

        if event.kind == LogParser.EVENT_YOU_DIED:
            if st._skip_time > 0 and (time.time() - st._skip_time) <= 3.0:
                self._log("✅ 自爆成功")
                st._skip_time = 0.0
            st.is_OpenSpecialRound_round = False
            return

        if event.kind == LogParser.EVENT_ROUND_OVER:
            st.in_round = False
            # アイテムロスト音声: auto_beginなしの場合はRoundOverで流す
            if st.waiting_for_equip and not self.cfg.auto_begin:
                self._announce_item_lost_once()
            return

        if event.kind == LogParser.EVENT_VERIFIED_END:
            # 続行/霧ラウンドのフリーズ解除
            if st.is_continue_round:
                st.is_continue_round = False
                _continue_round_end()
                self._log("▶ 続行/霧ラウンド終了 → 他窓フリーズ解除")
            # アイテムロスト判定（放置モード中はフリーズしない）
            if not get_hands_free():
                if st.round_type in config.ITEM_LOSS_ROUNDS:
                    st.item_id = 0
                    self._announce_item_lost_once()
                    if get_item_get_begin_mode():
                        # アイテム取得→Beginモード: ラウンド終了時点でフォーカス・全窓フリーズ
                        st.waiting_for_equip = True
                        _EQUIP_WAIT_EVENT.clear()
                        self._log("ラウンド終了 【⚠ アイテムロスト → フォーカス・全窓フリーズ開始】")
                        threading.Thread(target=lambda: WindowOperator.focus_window(self.cfg.hwnd), daemon=True).start()
                    else:
                        st.waiting_for_equip = True
                        self._log("ラウンド終了 【⚠ アイテムロスト → Begin時にフリーズ開始】")
                elif not st.item_id:
                    st.waiting_for_equip = True
                    self._announce_item_lost_once()
                    self._log("ラウンド終了 【⚠ アイテム未回収 → Begin時に再フリーズ】")
                else:
                    self._log("ラウンド終了")
            else:
                if st.round_type in config.ITEM_LOSS_ROUNDS:
                    st.item_id = 0
                    self._announce_item_lost_once()
                self._log("ラウンド終了")
            # CSVにラウンド結果を記録（重複排除・ユーザーID付き）
            ConnectDB.send_ToNRoundStatistics(st.round_type, st.terror_ids, st.map_id, st.transformed_uid)
            # Intermissionアナウンス
            if self.cfg.announce_intermission:
                PlaySound.play_sound(self.cfg.voice_intermission)
            # アイテムロスト音声はRoundOverで流す（auto_begin=Falseの場合）
            if self.cfg.auto_begin:
                threading.Thread(target=self._do_after_round, daemon=True).start()
            return

        # Fogラウンド突入
        if event.kind == LogParser.EVENT_KILLERS_UNKNOWN:
            st.fog  = True
            st.round_type = event.round_type or "Fog"
            self._log(f"テラー不明 → revealed待ち")
            if get_hands_free():
                self._log(f"開始: {st.round_type} 【放置モード→即自爆】")
                threading.Thread(target=self._do_skip, daemon=True).start()
            return

        if event.kind == LogParser.EVENT_FOXY:
            # 「foxy the pirate turned evil!」→ Alternate ID2（+134=136）確定
            self._log("🦊 Foxyが出た！")
            PlaySound.play_sound(self.cfg.voice_foxy)

            # もし霧なら自爆するか判定する(他はRE_KILLERS_SETから行う)
            if st.round_type == "Fog":
                # FoxyはAlternate枠テラーなのでFog(Alternate)に更新
                # → apply_alternate_offset が ID2+134=136→316 に正しく変換される
                st.round_type = "Fog (Alternate)"
                self._on_killers([2], "Fog (Alternate)", revealed=True)
            return

        if event.kind == LogParser.EVENT_KILLERS_REVEALED:
            self._on_killers(event.terror_ids or [], event.round_type, revealed=True)
            return

        if event.kind == LogParser.EVENT_JOINING:
            suffix = event.suffix
            if f"group({config.HOSHIIMO_GROUP_ID})" in suffix:
                itype = config.INSTANCE_HOSHIIMO
            elif "~group(" in suffix:
                itype = config.INSTANCE_OTHER_GROUP
            elif "~friends(hidden)~" in suffix or "~hidden(" in suffix or "~friends~" in suffix or "~private(" in suffix:
                itype = config.INSTANCE_PRIVATE
            else:
                itype = config.INSTANCE_PUBLIC
            SharedState.set_instance_type(itype)
            self._log(f"インスタンスタイプ: {itype}")
            return
        
        # アイテム装備検出（操作権限譲渡中の再開トリガー）
        if event.kind == LogParser.EVENT_ITEM_EQUIP:
            st.item_id = event.item_id
            self._log(f"✅ アイテム装備 (id={st.item_id})")
            if st.waiting_for_equip:
                # 両条件（装備＋Begin）が揃ったら2秒後に解除
                if st.begin_done:
                    st.waiting_for_equip = False
                    def _delayed_release(self=self):
                        time.sleep(2.0)
                        _EQUIP_WAIT_EVENT.set()
                        self._log("✅ 全窓フリーズ解除")
                    threading.Thread(target=_delayed_release, daemon=True).start()
            return
        
        if event.kind == LogParser.EVENT_LIVED:
            if st.is_OpenSpecialRound_round:
                st.OpenSpecialRound_wins += 1
                self._log(f"生存数: {st.OpenSpecialRound_wins}/{config.OpenSpecialRound_TARGET_WINS}")
                if st.OpenSpecialRound_wins >= config.OpenSpecialRound_TARGET_WINS:
                    self._log("🎉 3勝達成！以降のDTM/Waldoラウンドはスキップします")
            st.is_OpenSpecialRound_round = False
            return


    # ── テラー確定処理 ────────────────────────
    def _on_killers(self, ids: list[int], round_type: str, revealed: bool):
        st = self.st
        st.fog = False

        ids = RoundDecision.normalize_killer_ids(ids, round_type, st.round_type)

        # テラーIDを累積（複数回Killers行が来るラウンド対応）
        for tid in ids:
            if tid not in st.terror_ids:
                st.terror_ids.append(tid)

        # インスタンス制限チェック
        itype = SharedState.get_instance_type()
        is_private = itype == config.INSTANCE_PRIVATE
        is_hoshiimo = itype == config.INSTANCE_HOSHIIMO
        can_decide = is_private or is_hoshiimo

        # 干し芋グループ専用自動自爆
        if is_hoshiimo and self.cfg.hoshiimo_skip:
            if st.round_type in config.HOSHIIMO_SKIP_ROUNDS:
                self._log(f"干し芋自動自爆: {st.round_type}")
                if st.is_continue_round:
                    st.is_continue_round = False
                    _continue_round_end()
                threading.Thread(target=self._do_skip, daemon=True).start()
                return

        # 通常操作はフレ/フレ+/招待/招待+のみ。干し芋では判定と音声だけ通す。
        if not can_decide:
            if st.is_continue_round:
                st.is_continue_round = False
                _continue_round_end()
            self._log(f"インスタンス制限: 操作スキップ ({itype})")
            return

        # 放置モードの自動操作はprivateのみ。干し芋では判定と音声だけ通す。
        if get_hands_free() and is_private:
            # 特殊ラウンド経験済み → 全ラウンド即自爆
            if st.OpenSpecialRound_wins >= config.OpenSpecialRound_TARGET_WINS:
                self._log(f"放置モード(3クラ済み): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    threading.Thread(target=self._do_skip, daemon=True).start()
                return
            # アイテムなし → DTMのみ続行、Waldo含むそれ以外は自爆
            if not st.item_id:
                # DTM(50)はアイテム不要なので続行可、Waldo(131)はアイテム必要なので自爆
                DTM_ID = ReadJson.terror_id("Don't Touch Me", config.TERRORS)
                has_dtm = self.cfg.cancel_afk and DTM_ID in st.terror_ids
                if not has_dtm:
                    self._log(f"放置モード(アイテムなし・DTMなし): 即自爆 {st.terror_ids} / {st.round_type}")
                    if not st.is_continue_round and self.cfg.do_skip:
                        threading.Thread(target=self._do_skip, daemon=True).start()
                    return
                # DTMありなので通常判定へ fall through
            # アイテムあり・特殊ラウンド未経験 → DTM/Waldoのみ続行、他は即自爆
            has_cancel_afk = bool(
                config.OpenSpecialRound_TERROR_IDS and
                any(t in config.OpenSpecialRound_TERROR_IDS for t in st.terror_ids) and
                self.cfg.cancel_afk
            )
            if not has_cancel_afk:
                self._log(f"放置モード(DTM/Waldo以外): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    threading.Thread(target=self._do_skip, daemon=True).start()
                return
            # DTM/Waldobなので通常判定へ（is_OpenSpecialRound_target で続行）

        all_ids = st.terror_ids  # すでに累積済み
        was_continue_round = st.is_continue_round
        decision = RoundDecision.decide_killers(
            self.keepOn_set,
            all_ids,
            st.round_type,
            st.OpenSpecialRound_wins,
            self.cfg.cancel_afk,
        )
        is_OpenSpecialRound_target = decision.is_open_special_round_target
        st.is_continue_round = decision.is_continue_round

        if was_continue_round and not st.is_continue_round:
            _continue_round_end()

        verb = "revealed" if revealed else "set"
        if is_OpenSpecialRound_target:
            tag = "【プレイ(DTM/Waldo)】"
        else:
            tag = "【プレイ】" if st.is_continue_round else "【スキップ】"
        self._log(f"テラー{verb}: {format_terror_ids(all_ids)} / {round_type} {tag}")

        if st.is_continue_round:
            # 続行ラウンドの音声アナウンス＋他窓フリーズ（DTM/Waldo以外）
            if not is_OpenSpecialRound_target and not was_continue_round:
                PlaySound.play_sound(self.cfg.voice_continue)
                self._log("🎙 続行アナウンス再生")
                _continue_round_start()
                self._log("⏸ 続行/霧ラウンド中 → 他窓フリーズ開始")
            # 3クラ開け続行開始
            if is_OpenSpecialRound_target and is_private and st.OpenSpecialRound_wins < config.OpenSpecialRound_TARGET_WINS:
                st.is_OpenSpecialRound_round = True
                self._log(f"3クラ解放ラウンド開始（勝利数: {st.OpenSpecialRound_wins}/{config.OpenSpecialRound_TARGET_WINS}）")
                t = threading.Thread(target=self._do_OpenSpecialRound_loop, daemon=True)
                t.start()
            elif is_OpenSpecialRound_target and not is_private:
                self._log("DTM/WaldoラウンドだがAFK解除はprivateのみ")
            elif is_OpenSpecialRound_target:
                self._log("DTM/Waldoラウンドだが3勝達成済み→AFK解除なし")
        else:
            # 全テラーがスキップ対象 → 自爆（通常操作が許可されたインスタンスのみ）
            if self.cfg.do_skip and is_private and not st.is_continue_round:
                threading.Thread(target=self._do_skip, daemon=True).start()

    # ═════════════════════════════════════════
    #  アクション（全てグローバルロックで排他）
    # ═════════════════════════════════════════

    def _focus(self):
        """この窓にフォーカスを当てる"""
        hwnd = self.cfg.hwnd
        WindowOperator.focus_window(hwnd)
        self._log(f"フォーカス切替 → HWND={hwnd:#010x}")

    def _do_skip(self):
        """通常スキップ: ^ キー長押しで自爆"""
        # 自分の窓がアイテムロスト待ち中は自爆しない
        if self.st.waiting_for_equip:
            self._log("自爆キャンセル（アイテムロスト待ち中）")
            return
        # 他窓が装備待ち・続行ラウンド中はフリーズ（自分が続行ラウンド中は除く）
        if not self.st.is_continue_round:
            while self._running and self.st.in_round:
                eq_ok  = _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                con_ok = _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                if eq_ok and con_ok:
                    break
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._running or self.st.is_continue_round or not self.st.in_round:
                return
            # ロック取得後も再チェック
            if self.st.waiting_for_equip:
                self._log("自爆キャンセル（ロック取得後にアイテムロスト待ち検出）")
                return
            self.st._skip_time = time.time()   # 自爆実行時刻を記録
            self._log(f"自爆実行中 ({config.SUICIDE_HOLD_SEC}秒)…")
            self._focus()
            WindowOperator.hold_key(get_suicide_key(), config.SUICIDE_HOLD_SEC)

    def _do_after_round(self):
        """
        ラウンド終了後: 待機 → [購入+Begin前移動] → Beginクリック＆リトライ

        ロック戦略:
          - 購入・移動・クリック: ロックを取って実行（他窓と干渉しない）
          - クリック後の「ラウンド開始待ち」: ロックを解放して待機
            → 待機中に他窓が操作できる
        """
        time.sleep(config.BEGIN_WAIT_SEC)
        # Beginはフレ/フレ+/招待/招待+のみ
        if SharedState.get_instance_type() != config.INSTANCE_PRIVATE:
            return
        if not self._running or self.st.in_round:
            self._log("Begin キャンセル（停止 or 次のラウンドが開始）")
            return
        # 他窓が装備待ち・続行ラウンド中はフリーズ（自分が続行ラウンド中は除く）
        # 両イベントが同時にセット状態になるまで待つ（複合ケース対応）
        cfg = self.cfg
        st  = self.st

        # 他窓のフリーズ解除を待つ（自分がアイテムロスト中の場合は除く）
        if not st.is_continue_round and not st.waiting_for_equip:
            while self._running:
                eq_ok  = _EQUIP_WAIT_EVENT.is_set()
                con_ok = _CONTINUE_ROUND_EVENT.is_set()
                if eq_ok and con_ok:
                    break
                if not eq_ok:
                    self._log("他窓の装備待ち中 → フリーズ")
                if not con_ok:
                    self._log("他窓の続行/霧ラウンド中 → フリーズ")
                _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
            if not self._running:
                return

        # アイテムロスト状態なら他窓の解除を待ってから即フリーズ
        if st.waiting_for_equip:
            if not st.is_continue_round:
                while self._running:
                    if _EQUIP_WAIT_EVENT.is_set() and _CONTINUE_ROUND_EVENT.is_set():
                        break
                    _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                if not self._running:
                    return
            _EQUIP_WAIT_EVENT.clear()
            self._log("⚠ アイテムロスト → 全窓フリーズ（Beginへ向かいます）")

        # ── フェーズ1: Begin前移動 + 初回クリック（ロック内）──
        with SharedState._GLOBAL_ACTION_LOCK:
            if not self._running or st.in_round:
                self._log("Begin キャンセル（ロック待ち中に停止 or 次のラウンドが開始）")
                # アイテムロスト中でかつラウンドに入った → フリーズ解除してフェーズ2aへ
                if st.waiting_for_equip and st.in_round:
                    _EQUIP_WAIT_EVENT.set()
                    self._log("ラウンド開始によりフリーズ解除 → 装備待ちへ")
                elif not st.waiting_for_equip:
                    return
            if not st.waiting_for_equip:
                # 通常のフリーズチェック（アイテムロスト中は自分がフリーズ主体なのでスキップ）
                if not st.is_continue_round:
                    if not _CONTINUE_ROUND_EVENT.is_set():
                        self._log("Begin キャンセル（ロック取得後にフリーズ検出）")
                        return
            if not st.in_round:
                self._focus()
                # アイテムロスト時はBeginへ向かう直前に音声（フォーカス後）
                if st.waiting_for_equip:
                    self._announce_item_lost_once()
                WindowOperator.hold_key("w", config.BEGIN_FORWARD_SEC)
                WindowOperator.hold_key("a", config.BEGIN_LEFT_SEC)
                time.sleep(0.1)
                if not st.in_round:
                    self._log("Beginクリック")
                    WindowOperator.click()

        # ── フェーズ2a: アイテムロスト装備待ち（ロック外）──
        if st.waiting_for_equip:
            self._log("アイテム装備を待っています… （装備すると自動再開）")
            while st.waiting_for_equip and self._running:
                time.sleep(0.3)
            if not self._running:
                _EQUIP_WAIT_EVENT.set()
                return
            self._log("✅ アイテム装備確認 → 続行")
            # 装備完了後はラウンドが始まっていれば終了（Beginは不要）
            if st.in_round:
                return

        # ── フェーズ2: Begin確認待ち＆リトライ（ロック外）──
        # "Verified" ログ = Begin正常押下確認。即座に検出してリトライ。
        if st.begin_done:
            return
        for attempt in range(1, config.BEGIN_RETRY_MAX+1):
            self._log(f"Begin確認待ち… [{attempt-1}/{config.BEGIN_RETRY_MAX}]")
            waited = 0.0
            while waited < config.BEGIN_RETRY_WAIT_SEC:
                time.sleep(0.2)
                waited += 0.2
                if not self._running:
                    return
                if st.begin_done:
                    return
                # フリーズチェック
                if not st.is_continue_round and not st.waiting_for_equip:
                    if not _EQUIP_WAIT_EVENT.is_set():
                        self._log("Begin待機中に他窓がアイテムロスト → 一時フリーズ")
                        while self._running and not _EQUIP_WAIT_EVENT.is_set():
                            _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    if not _CONTINUE_ROUND_EVENT.is_set():
                        self._log("Begin待機中に他窓が続行ラウンド開始 → 一時フリーズ")
                        while self._running and not _CONTINUE_ROUND_EVENT.is_set():
                            _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
            if not self._running:
                return
            # Verified未確認 → リトライクリック
            if attempt < config.BEGIN_RETRY_MAX:
                self._log(f"Verified未確認 → リトライ {attempt}/{config.BEGIN_RETRY_MAX}")
                with SharedState._GLOBAL_ACTION_LOCK:
                    if not self._running or st.in_round or st.begin_done:
                        return
                    self._focus()
                    if attempt % 2 == 1:
                        WindowOperator.hold_key("a", config.BEGIN_RETRY_LEFT_SEC)
                    else:
                        WindowOperator.hold_key("d", config.BEGIN_RETRY_RIGHT_SEC)
                    time.sleep(0.1)
                    if st.in_round or st.begin_done:
                        return
                    WindowOperator.click()

        self._log(f"Begin {config.BEGIN_RETRY_MAX}回試行しましたがVerified未確認")

    def _do_OpenSpecialRound_loop(self):
        """
        ラウンド中60秒ごとに移動キーをわずかに押す（ジャンプ代替）。
        - フォーカス切り替えは SharedState._GLOBAL_ACTION_LOCK 内でのみ行う
          → 自爆・Begin操作中にフォーカスを奪わない
          → ロック待ちになることで自爆完了後に実行される
        - 停止条件: _running=False / in_round=False /
                    is_OpenSpecialRound_round=False / OpenSpecialRound_completed=True
        """
        st = self.st
        self._log(f"AFK解除ループ開始（{config.OpenSpecialRound_INTERVAL_SEC}秒ごと）")
        elapsed = 0.0
        CHECK_INTERVAL = 1.0
        while True:
            if not self._running or not st.in_round or not st.is_OpenSpecialRound_round or st.OpenSpecialRound_wins >= config.OpenSpecialRound_TARGET_WINS:
                break
            time.sleep(CHECK_INTERVAL)
            elapsed += CHECK_INTERVAL
            if elapsed >= config.OpenSpecialRound_INTERVAL_SEC:
                elapsed = 0.0
                if not self._running or not st.in_round or not st.is_OpenSpecialRound_round or st.OpenSpecialRound_wins >= config.OpenSpecialRound_TARGET_WINS:
                    break
                # ロックを取ってフォーカス＆キー送信
                # 自爆・Begin中はロック待ちになるので操作が重ならない
                with SharedState._GLOBAL_ACTION_LOCK:
                    if not self._running or not st.in_round or not st.is_OpenSpecialRound_round or st.OpenSpecialRound_wins >= config.OpenSpecialRound_TARGET_WINS:
                        break
                    WindowOperator.focus_window(self.cfg.hwnd)
                    WindowOperator.hold_key("w", 0.05)
                self._log("移動キー送信（ジャンプ代替）")
        self._log("AFK解除ループ終了")
