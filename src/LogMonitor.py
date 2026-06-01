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
from ActionExecutor import ActionExecutor
from State import WindowConfig, WindowState


def format_terror_ids(ids: list[int]) -> str:
    names = []
    for tid in ids:
        name = ReadJson.terror_name(tid, config.TERRORS)
        names.append(name if name else f"ID:{tid}")
    return ", ".join(names)


# ═══════════════════════════════════════════════
#  ログ監視ワーカー
#  ログ読み込み・イベント処理・ラウンド判定を担当する。
#  窓操作（自爆・Begin・AFK防止）は ActionExecutor に委譲する。
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
        self._action = ActionExecutor(
            cfg=cfg,
            st=self.st,
            is_running=lambda: self._running,
            log=self._log,
        )

    def start(self):
        self._running = True
        # 過去ログからインスタンスタイプを検出（ワールド入室後の起動に対応）
        self._detect_instance_from_log()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _log(self, msg: str):
        self.logger(f"[窓{self.window_idx}] {msg}")

    @staticmethod
    def _parse_instance_type(suffix: str) -> str:
        suffix = suffix or ""
        if f"group({config.HOSHIIMO_GROUP_ID})" in suffix:
            return config.INSTANCE_HOSHIIMO
        if f"group({config.YAKIIMO_GROUP_ID})" in suffix:
            return config.INSTANCE_YAKIIMO
        if "~group(" in suffix:
            return config.INSTANCE_OTHER_GROUP
        if any(marker in suffix for marker in ("~private", "~friends", "~hidden", "~canRequestInvite")):
            return config.INSTANCE_PRIVATE
        return config.INSTANCE_PUBLIC

    @staticmethod
    def _same_player_name(a: str, b: str) -> bool:
        return bool(a and b and a.strip() == b.strip())

    def _detect_instance_from_log(self):
        if not self.cfg.log_path or not self.cfg.log_path.exists():
            return
        try:
            with open(self.cfg.log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            found_user     = False
            found_instance = False
            for line in reversed(lines):
                event = LogParser.parse(line)
                if not event:
                    continue

                if not found_user and event.kind == LogParser.EVENT_USER_AUTH:
                    self._log(f"UserID検出: {event.user_id}")
                    self.st.local_player_name = event.player_name
                    self.st.transformed_uid = ConnectDB.send_Users(event.user_id)
                    self._log(f"transformed_uid: {self.st.transformed_uid}")
                    found_user = True

                if not found_instance and event.kind == LogParser.EVENT_JOINING:
                    found_instance = True
                    self.st.instance_type = self._parse_instance_type(event.suffix)
                    self._log(f"インスタンスタイプ検出: {self.st.instance_type}")

                if found_user and found_instance:
                    return
        except Exception as e:
            self._log(f"検出エラー: {e}")

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

    def _mark_sabotage_murder(self):
        st = self.st
        if st.sabotage_murder_this_round:
            return
        st.sabotage_murder_this_round = True
        st.item_id = 0
        self._log("Sabotageマーダー判定: アイテムロスト")

    def _round_lost_item(self) -> bool:
        st = self.st
        item_loss_round = (
            st.round_type in config.ITEM_LOSS_ROUNDS
            and not st.item_equipped_after_death
        )
        sabotage_murder_loss = st.sabotage_murder_this_round and not st.item_id
        return item_loss_round or sabotage_murder_loss

    # ── ログ行処理 ────────────────────────────
    def _process(self, line: str):
        st = self.st
        event = LogParser.parse(line)
        if not event:
            return

        if event.kind == LogParser.EVENT_USER_AUTH:
            st.local_player_name = event.player_name
            return

        if event.kind == LogParser.EVENT_SUS_PLAYER:
            if self._same_player_name(event.player_name, st.local_player_name):
                if st.in_round and st.round_type == "Sabotage":
                    self._mark_sabotage_murder()
                else:
                    st.pending_sabotage_murder = True
                self._log(f"Sus player一致: {event.player_name}")
            return

        if event.kind == LogParser.EVENT_BEGIN_DONE:
            st.begin_done = True
            self._log("✅ Connecting")
            # アイテムロスト中のBegin確認：装備済みなら遅延フリーズ解除
            if st.waiting_for_equip and st.item_id:
                st.waiting_for_equip = False
                def _delayed_release(self=self):
                    time.sleep(config.EQUIP_RELEASE_DELAY_SEC)
                    SharedState.EQUIP_WAIT_EVENT.set()
                    self._log("✅ 全窓フリーズ解除")
                threading.Thread(target=_delayed_release, daemon=True).start()
            return

        if event.kind == LogParser.EVENT_ROUND_START:
            if st.is_continue_round:
                st.is_continue_round = False
                SharedState.continue_round_end()
            st.in_round                    = True
            st.round_type                  = event.round_type
            st.terror_ids                  = []
            st.map_id                      = event.map_id
            st.statistics_sent             = False
            st.fog                         = False
            st.begin_done                  = False
            st.is_open_special_round_round = False
            st.item_lost_announced         = False
            st.died_this_round             = False
            st.item_equipped_after_death   = False
            st.sabotage_murder_this_round  = (
                st.round_type == "Sabotage" and st.pending_sabotage_murder
            )
            st.pending_sabotage_murder     = False
            # アイテムロスト中にラウンドが始まったらフリーズ解除
            # （has_item=Falseのまま → 次のVerified Round Endで再フリーズ）
            if st.waiting_for_equip:
                st.waiting_for_equip = False
                SharedState.EQUIP_WAIT_EVENT.set()
                self._log("一時的にアイテムロストフリーズを解除")

            if st.sabotage_murder_this_round:
                st.item_id = 0
                self._log("Sabotageマーダー開始: アイテムロスト")

            if st.round_type == "Run":
                st.is_continue_round = False
                self._log(f"Round: {st.round_type} 【死亡待ち・アイテム購入予定】")
                return

            if st.round_type == "Fog":
                if not SharedState.get_hands_free():
                    st.is_continue_round = True
                    SharedState.continue_round_start()
                    PlaySound.play_sound(self.cfg.voice_fog)
                    self._log(f"開始: {st.round_type} 【他窓フリーズ開始】")
                return

            self._log(f"開始: {st.round_type}")
            return

        if event.kind == LogParser.EVENT_KILLERS_SET:
            # 特殊ラウンドを経験したら3勝扱い
            if st.round_type in config.SPECIAL_ROUND:
                st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS
            if not (st.round_type == "Alternate" and event.round_type == "Classic"):  # AF期間中は極まれに偽Classicがある
                st.round_type = event.round_type
            self._on_killers(event.terror_ids or [], st.round_type, revealed=False)
            return

        if event.kind == LogParser.EVENT_YOU_DIED:
            if st._skip_time > 0 and (time.time() - st._skip_time) <= 3.0:
                self._log("✅ 自爆成功")
                st._skip_time = 0.0
            st.died_this_round = True
            st.item_equipped_after_death = False
            st.is_open_special_round_round = False
            return

        if event.kind == LogParser.EVENT_ROUND_OVER:
            st.in_round = False
            if not self.cfg.auto_begin and not SharedState.get_hands_free():
                round_lost_item = self._round_lost_item()
                if round_lost_item:
                    st.item_id = 0
                    st.waiting_for_equip = True
                elif not st.item_id:
                    st.waiting_for_equip = True
            if st.waiting_for_equip and not self.cfg.auto_begin:
                self._action.announce_item_lost_once()
            return

        if event.kind == LogParser.EVENT_VERIFIED_END:
            if st.is_continue_round:
                st.is_continue_round = False
                SharedState.continue_round_end()
                self._log("▶ 続行/霧ラウンド終了 → 他窓フリーズ解除")
            round_lost_item = self._round_lost_item()
            if not SharedState.get_hands_free():
                if round_lost_item:
                    st.item_id = 0
                    if not self.cfg.auto_begin and not st.waiting_for_equip:
                        self._log("ラウンド終了 【⚠ アイテムロスト → RoundOver時に通知予定】")
                    elif SharedState.get_item_begin_mode():
                        st.waiting_for_equip = True
                        SharedState.EQUIP_WAIT_EVENT.clear()
                        self._log("ラウンド終了 【⚠ アイテムロスト → フォーカス・全窓フリーズ開始】")
                        threading.Thread(target=lambda: WindowOperator.focus_window(self.cfg.hwnd), daemon=True).start()
                    else:
                        st.waiting_for_equip = True
                        self._log("ラウンド終了 【⚠ アイテムロスト → Begin時にフリーズ開始】")
                elif not st.item_id:
                    if not self.cfg.auto_begin and not st.waiting_for_equip:
                        self._log("ラウンド終了 【⚠ アイテム未回収 → RoundOver時に通知予定】")
                    else:
                        st.waiting_for_equip = True
                        self._log("ラウンド終了 【⚠ アイテム未回収 → Begin時に再フリーズ】")
                else:
                    self._log("ラウンド終了")
            else:
                if round_lost_item:
                    st.item_id = 0
                    if self.cfg.auto_begin:
                        self._action.announce_item_lost_once()
                    else:
                        st.waiting_for_equip = True
                self._log("ラウンド終了")
            if self.cfg.announce_intermission:
                PlaySound.play_sound(self.cfg.voice_intermission)
            if self.cfg.auto_begin:
                threading.Thread(target=self._action.do_after_round, daemon=True).start()
            return

        if event.kind == LogParser.EVENT_KILLERS_UNKNOWN:
            st.fog = True
            st.round_type = event.round_type or "Fog"
            self._log("テラー不明 → revealed待ち")
            if SharedState.get_hands_free():
                self._log(f"開始: {st.round_type} 【放置モード→即自爆】")
                threading.Thread(target=self._action.do_skip, daemon=True).start()
            return

        if event.kind == LogParser.EVENT_FOXY:
            self._log("🦊 Foxyが出た！")
            PlaySound.play_sound(self.cfg.voice_foxy)
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
            st.instance_type = self._parse_instance_type(event.suffix)
            self._log(f"インスタンスタイプ: {st.instance_type}")
            return

        if event.kind == LogParser.EVENT_ITEM_EQUIP:
            st.item_id = event.item_id
            if st.died_this_round and st.item_id:
                st.item_equipped_after_death = True
            self._log(f"✅ アイテム装備 (id={st.item_id})")
            # 両条件（装備＋Begin）が揃ったら遅延フリーズ解除
            if st.waiting_for_equip and st.begin_done:
                st.waiting_for_equip = False
                def _delayed_release(self=self):
                    time.sleep(config.EQUIP_RELEASE_DELAY_SEC)
                    SharedState.EQUIP_WAIT_EVENT.set()
                    self._log("✅ 全窓フリーズ解除")
                threading.Thread(target=_delayed_release, daemon=True).start()
            return

        if event.kind == LogParser.EVENT_LIVED:
            if st.is_open_special_round_round:
                st.open_special_round_wins += 1
                self._log(f"生存数: {st.open_special_round_wins}/{config.OPEN_SPECIAL_ROUND_TARGET_WINS}")
                if st.open_special_round_wins >= config.OPEN_SPECIAL_ROUND_TARGET_WINS:
                    self._log("🎉 3勝達成！以降のDTM/Waldoラウンドはスキップします")
            st.is_open_special_round_round = False
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

        self._send_round_statistics_once()

        # インスタンス制限チェック
        itype        = st.instance_type
        is_private   = itype == config.INSTANCE_PRIVATE
        is_group_skip = itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO)
        can_decide   = is_private or is_group_skip

        # 干し芋グループ専用自動自爆
        if is_group_skip and self.cfg.hoshiimo_skip:
            if st.round_type in config.HOSHIIMO_SKIP_ROUNDS:
                self._log(f"干し芋自動自爆: {st.round_type}")
                if st.is_continue_round:
                    st.is_continue_round = False
                    SharedState.continue_round_end()
                threading.Thread(target=self._action.do_skip, daemon=True).start()
                return

        # 通常操作はフレ/フレ+/招待/招待+のみ。干し芋では判定と音声だけ通す。
        if not can_decide:
            if st.is_continue_round:
                st.is_continue_round = False
                SharedState.continue_round_end()
            self._log(f"インスタンス制限: 操作スキップ ({itype})")
            return

        # 放置モードの自動操作はprivateのみ。
        if SharedState.get_hands_free() and is_private:
            if st.open_special_round_wins >= config.OPEN_SPECIAL_ROUND_TARGET_WINS:
                self._log(f"放置モード(3クラ済み): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    threading.Thread(target=self._action.do_skip, daemon=True).start()
                return
            if not st.item_id:
                DTM_ID = ReadJson.terror_id("Don't Touch Me", config.TERRORS)
                has_dtm = self.cfg.cancel_afk and DTM_ID in st.terror_ids
                if not has_dtm:
                    self._log(f"放置モード(アイテムなし・DTMなし): 即自爆 {st.terror_ids} / {st.round_type}")
                    if not st.is_continue_round and self.cfg.do_skip:
                        threading.Thread(target=self._action.do_skip, daemon=True).start()
                    return
            has_cancel_afk = bool(
                config.OPEN_SPECIAL_ROUND_TERROR_IDS and
                any(t in config.OPEN_SPECIAL_ROUND_TERROR_IDS for t in st.terror_ids) and
                self.cfg.cancel_afk
            )
            if not has_cancel_afk:
                self._log(f"放置モード(DTM/Waldo以外): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    threading.Thread(target=self._action.do_skip, daemon=True).start()
                return

        all_ids = st.terror_ids
        was_continue_round = st.is_continue_round
        decision = RoundDecision.decide_killers(
            self.keepOn_set,
            all_ids,
            st.round_type,
            st.open_special_round_wins,
            self.cfg.cancel_afk,
        )
        is_open_special_round_target = decision.is_open_special_round_target
        st.is_continue_round = decision.is_continue_round

        if was_continue_round and not st.is_continue_round:
            SharedState.continue_round_end()

        verb = "revealed" if revealed else "set"
        tag  = "【プレイ(DTM/Waldo)】" if is_open_special_round_target else (
               "【プレイ】" if st.is_continue_round else "【スキップ】")
        self._log(f"テラー{verb}: {format_terror_ids(all_ids)} / {round_type} {tag}")

        if st.is_continue_round:
            if not is_open_special_round_target and not was_continue_round:
                PlaySound.play_sound(self.cfg.voice_continue)
                self._log("🎙 続行アナウンス再生")
                SharedState.continue_round_start()
                self._log("⏸ 続行/霧ラウンド中 → 他窓フリーズ開始")
            if is_open_special_round_target and is_private and st.open_special_round_wins < config.OPEN_SPECIAL_ROUND_TARGET_WINS:
                st.is_open_special_round_round = True
                self._log(f"3クラ解放ラウンド開始（勝利数: {st.open_special_round_wins}/{config.OPEN_SPECIAL_ROUND_TARGET_WINS}）")
                threading.Thread(target=self._action.do_open_special_round_loop, daemon=True).start()
            elif is_open_special_round_target and not is_private:
                self._log("DTM/WaldoラウンドだがAFK解除はprivateのみ")
            elif is_open_special_round_target:
                self._log("DTM/Waldoラウンドだが3勝達成済み→AFK解除なし")
        else:
            if self.cfg.do_skip and is_private:
                threading.Thread(target=self._action.do_skip, daemon=True).start()

    def _send_round_statistics_once(self):
        st = self.st
        if st.statistics_sent or not st.terror_ids:
            return
        st.statistics_sent = True
        ConnectDB.send_ToNRoundStatistics(
            st.round_type,
            list(st.terror_ids),
            st.map_id,
            st.transformed_uid,
        )
