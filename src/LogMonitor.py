import time
import threading
from functools import lru_cache
from pathlib import Path
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


DTM_TERROR_ID = ReadJson.terror_id("Don't Touch Me", config.TERRORS)


@lru_cache(maxsize=512)
def _terror_name_cached(tid: int) -> str:
    return ReadJson.terror_name(tid, config.TERRORS) or f"ID:{tid}"


def format_terror_ids(ids: list[int]) -> str:
    return ", ".join(_terror_name_cached(tid) for tid in ids)


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
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._action = ActionExecutor(
            cfg=cfg,
            st=self.st,
            is_running=lambda: self._running,
            log=self._log,
        )

    def start(self):
        self._running = True
        self._stop_event.clear()
        # 過去ログからインスタンスタイプを検出（ワールド入室後の起動に対応）
        self._detect_instance_from_log()
        self._thread = self._start_daemon(self._run)

    def stop(self):
        self._running = False
        self._stop_event.set()

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

    def _start_daemon(self, target, *args) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def _release_continue_freeze_after_delay(self, round_seq: int):
        """死亡から一定時間後に続行/霧ラウンドのフリーズを解除する。

        猶予を置くのは、解除前に手動操作を挟む余地を残すため。
        テラーが分からないまま終わった霧ラウンドは猶予を短くする
        （続行ラウンドほど手動で挟む用事がないため）。
        待機中に次のラウンドが始まったら（round_seqが変わったら）何もしない。
        """
        st = self.st
        # 眠っている間にテラーが判明する可能性があるので、待つ長さは先に決める
        delay = (config.FOG_FREEZE_RELEASE_DELAY_SEC if st.fog
                 else config.CONTINUE_FREEZE_RELEASE_DELAY_SEC)
        time.sleep(delay)
        if not self._running or st.round_seq != round_seq:
            return
        if st.is_continue_round:
            st.is_continue_round = False
            SharedState.continue_round_end()
            self._log(f"続行/霧ラウンド終了 → 他窓フリーズ解除（死亡から{delay}秒）")

    def _learn_periodic(self, t: float):
        """定期シグナルと判定した時刻から周期を学習する。

        周期は窓ごとに位相が違い、ドリフトもするので指数平滑で追従させる。
        極端な間隔（ラウンド由来の取り違えなど）は学習に混ぜない。
        """
        st = self.st
        if st.periodic_last:
            iv = t - st.periodic_last
            if config.VERIFIED_PERIODIC_MIN_SEC <= iv <= config.VERIFIED_PERIODIC_MAX_SEC:
                base = st.periodic_period or config.VERIFIED_PERIODIC_INIT_SEC
                st.periodic_period = ((1 - config.VERIFIED_PERIODIC_SMOOTH) * base
                                      + config.VERIFIED_PERIODIC_SMOOTH * iv)
        st.periodic_last = t

    def _check_pending_verified(self):
        """採用したVerifiedに Everything recieved が続かなければ定期シグナルだった。

        位相を掴む唯一の手段。棄却したものだけで学習すると最初の1件を拾えず、
        位相が永久に初期化されない。
        """
        st = self.st
        if not st.pending_verified_time:
            return
        if (time.time() - st.pending_verified_time) <= config.VERIFIED_RECV_TIMEOUT_SEC:
            return
        self._learn_periodic(st.pending_verified_time)
        st.pending_verified_time = 0.0

    def _release_equip_wait_after_delay(self):
        time.sleep(config.EQUIP_RELEASE_DELAY_SEC)
        SharedState.equip_freeze_end(self.st)
        if SharedState.get_equip_freeze_count() == 0:
            self._log("✅ 全窓フリーズ解除")
        else:
            self._log("✅ この窓の装備待ち解除（他窓の装備待ちが残っています）")

    @staticmethod
    def _iter_log_lines_reversed(path, chunk_size: int):
        if chunk_size <= 0:
            chunk_size = 256 * 1024

        with open(path, "rb") as f:
            pos = f.seek(0, 2)
            pending = b""
            while pos > 0:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + pending
                lines = data.splitlines()

                if pos > 0:
                    if lines:
                        pending = lines[0]
                        lines = lines[1:]
                    else:
                        pending = data
                        lines = []
                else:
                    pending = b""

                for raw_line in reversed(lines):
                    yield raw_line.decode("utf-8", errors="replace")

            if pending:
                yield pending.decode("utf-8", errors="replace")

    @classmethod
    def detect_instance_type_from_log(cls, log_path) -> Optional[str]:
        """ログ末尾からインスタンスタイプを検出する（GUIのログ選択時用）。
        最初に見つかったJoining行（＝最新の入室）で打ち切るため軽量。
        見つからなければNone。"""
        try:
            path = Path(log_path)
            if not path.exists():
                return None
            lines = cls._iter_log_lines_reversed(path, config.LOG_START_SCAN_CHUNK_BYTES)
            for line in lines:
                event = LogParser.parse(line)
                if event and event.kind == LogParser.EVENT_JOINING:
                    return cls._parse_instance_type(event.suffix)
        except Exception:
            return None
        return None

    def _detect_instance_from_log(self):
        if not self.cfg.log_path or not self.cfg.log_path.exists():
            return
        try:
            found_user     = False
            found_instance = False
            lines = self._iter_log_lines_reversed(
                self.cfg.log_path,
                config.LOG_START_SCAN_CHUNK_BYTES,
            )
            for line in lines:
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
        try:
            with open(cfg.log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.st.log_pos)
                while self._running:
                    try:
                        current_size = cfg.log_path.stat().st_size
                        if current_size < self.st.log_pos:
                            f.seek(0)
                            self.st.log_pos = 0

                        f.seek(self.st.log_pos)
                        chunk = f.read()
                        self.st.log_pos = f.tell()
                        if chunk:
                            for line in chunk.splitlines():
                                self._process(line)
                    except Exception as e:
                        self._log(f"読み取りエラー: {e}")
                    self._check_pending_verified()
                    if self._stop_event.wait(config.LOG_POLL_INTERVAL):
                        break
        except Exception as e:
            self._log(f"読み取りエラー: {e}")

    def _mark_sabotage_murder(self):
        st = self.st
        if st.sabotage_murder_this_round:
            return
        st.sabotage_murder_this_round = True
        self._mark_item_lost("Sabotageマーダー判定: アイテムロスト")

    def _hands_free(self) -> bool:
        """この窓で放置モードが効いているか。

        放置モードは全窓共通のトグルだが、効くのはprivate系インスタンスに居る窓だけ。
        干し芋の窓とプラベの窓を同時に監視することがあるため、窓ごとに判定する。
        """
        return (SharedState.get_hands_free()
                and self.st.instance_type == config.INSTANCE_PRIVATE)

    def _mark_item_lost(self, message: str = ""):
        st = self.st
        already_lost = st.item_lost_this_round and st.item_id == 0
        st.item_lost_this_round = True
        st.randomizer_item_changed = False
        st.item_id = 0
        if message and not already_lost:
            self._log(message)

    def _round_start_loses_item(self) -> bool:
        st = self.st
        if st.round_type not in config.ROUND_START_ITEM_LOSS_ROUNDS:
            return False
        if st.round_type == "8 Pages" and st.item_id in config.EIGHT_PAGES_KEEP_ITEM_IDS:
            return False
        return True

    def _round_lost_item(self) -> bool:
        st = self.st
        return st.item_lost_this_round and not st.item_id

    def _round_item_warning(self) -> bool:
        return self._round_lost_item() or self.st.randomizer_item_changed

    def _track_randomizer_item_change(self, event: LogParser.LogEvent):
        st = self.st
        if st.round_type != "Randomizer" or not st.in_round or not event.item_id:
            return

        was_changed = st.randomizer_item_changed
        original_item_id = st.item_id_at_round_start or event.previous_item_id
        if original_item_id and event.item_id == original_item_id:
            st.randomizer_item_changed = False
        elif original_item_id and event.item_id != original_item_id:
            st.randomizer_item_changed = True
        elif event.previous_item_id is not None and event.previous_item_id != event.item_id:
            st.randomizer_item_changed = True

        if st.randomizer_item_changed and not was_changed:
            self._log("Randomizer: アイテム差し替え警告")

    def _apply_bloodthirsty_creature_variant(self, ids: list[int]) -> list[int]:
        if not self.st.bloodthirsty_creature_variant:
            return ids
        return [
            config.BLOODTHIRSTY_CREATURE_ID if tid == config.CURIOUS_CREATURE_ID else tid
            for tid in ids
        ]

    def _apply_hungry_home_invader_variant(self, ids: list[int], round_type: str) -> list[int]:
        if not self.st.hungry_home_invader_variant or round_type != "Classic":
            return ids
        return [
            config.HUNGRY_HOME_INVADER_ID if tid == config.SLENDER_ID else tid
            for tid in ids
        ]

    def _waiting_for_bloodthirsty_creature_variant(self) -> bool:
        return (
            config.CURIOUS_CREATURE_ID in self.st.terror_ids
            and not self.st.bloodthirsty_creature_variant
        )

    def _waiting_for_hungry_home_invader_variant(self) -> bool:
        return (
            self.st.round_type == "Classic"
            and config.SLENDER_ID in self.st.terror_ids
            and not self.st.hungry_home_invader_variant
        )

    def _waiting_for_terror_variant(self) -> bool:
        return (
            self._waiting_for_bloodthirsty_creature_variant()
            or self._waiting_for_hungry_home_invader_variant()
        )

    def _mark_bloodthirsty_creature_variant(self):
        st = self.st
        st.bloodthirsty_creature_variant = True
        changed = False
        for i, tid in enumerate(st.terror_ids):
            if tid == config.CURIOUS_CREATURE_ID:
                st.terror_ids[i] = config.BLOODTHIRSTY_CREATURE_ID
                changed = True
        if changed:
            self._log("Wild Yet Curious Creature -> Wild Yet Bloodthirsty Creature")
            self._send_round_statistics_once()
        else:
            self._log("Wild Yet Bloodthirsty Creature variant detected")

    def _mark_hungry_home_invader_variant(self):
        st = self.st
        if st.round_type != "Classic":
            return
        st.hungry_home_invader_variant = True
        changed = False
        for i, tid in enumerate(st.terror_ids):
            if tid == config.SLENDER_ID:
                st.terror_ids[i] = config.HUNGRY_HOME_INVADER_ID
                changed = True
        if changed:
            self._log("Slender -> Hungry Home Invader")
            self._send_round_statistics_once()
        else:
            self._log("Hungry Home Invader variant detected")

    def _should_skip_group_round(self) -> bool:
        """干し芋/焼き芋の自動自爆をしてよいか。
        バリアントテラー(Bloodthirsty / Hungry Home Invader)なら自爆しない。
        干し芋・焼き芋の両方、かつ自爆対象ラウンド全種に適用する。"""
        st = self.st
        if config.BLOODTHIRSTY_CREATURE_ID in st.terror_ids:
            self._log("グループ自動自爆キャンセル: Wild Yet Bloodthirsty Creature")
            return False
        if config.HUNGRY_HOME_INVADER_ID in st.terror_ids:
            self._log("グループ自動自爆キャンセル: Hungry Home Invader")
            return False
        return True

    def _variant_wait_sec(self) -> float:
        return config.TERROR_VARIANT_WAIT_SEC.get(
            self.st.round_type, config.TERROR_VARIANT_WAIT_DEFAULT_SEC)

    def _round_still_active(self, round_seq: int) -> bool:
        return self._running and self.st.in_round and self.st.round_seq == round_seq

    def _start_group_skip(self):
        st = self.st
        self._log(f"干し芋自動自爆: {st.round_type}")
        if st.is_continue_round:
            st.is_continue_round = False
            SharedState.continue_round_end()
        self._start_daemon(self._action.do_skip)

    def _delayed_group_skip(self, wait_sec: float, round_seq: int):
        """テラー出現ログ(バリアント判定)を待ってから自動自爆の可否を決める。
        Bloodbath等は枠ごとに出現時刻がずれるためラウンド種別ごとに待ち時間を変える。"""
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if not self._round_still_active(round_seq):
                return
            if not self._waiting_for_terror_variant():
                break  # バリアント確定 → 残り時間を待たずに判断へ
            time.sleep(config.TERROR_VARIANT_POLL_SEC)
        if not self._round_still_active(round_seq):
            return
        if not self._should_skip_group_round():
            return
        self._start_group_skip()

    # ── ログ行処理 ────────────────────────────
    def _process(self, line: str):
        st = self.st
        event = LogParser.parse(line)
        if not event:
            return

        if event.kind == LogParser.EVENT_CREATURE_BLOODTHIRSTY:
            self._mark_bloodthirsty_creature_variant()
            return

        if event.kind == LogParser.EVENT_HUNGRY_HOME_INVADER:
            self._mark_hungry_home_invader_variant()
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
            # `Verified` はBegin受理専用のログではない。ラウンド結果の検証完了と
            # 約300秒周期の定期シグナルでも同じ行が出る。
            # 予測に使うのは「前回の“定期”からの間隔」であって、直前のVerifiedからの
            # 間隔ではない（定期の直前には数十秒間隔でラウンド由来のVerifiedが入る）。
            now = time.time()
            period = st.periodic_period or config.VERIFIED_PERIODIC_INIT_SEC
            if st.periodic_last and abs(now - (st.periodic_last + period)) <= config.VERIFIED_PERIODIC_TOL_SEC:
                self._learn_periodic(now)
                self._log("Verified を無視（定期シグナル）")
                return

            # 本物として採用。Everything recieved が続くかで事後確認する
            st.pending_verified_time = now
            st.begin_done = True
            self._log("✅ Connecting")
            # アイテムロスト中のBegin確認：装備済みなら遅延フリーズ解除
            if st.waiting_for_equip and st.item_id:
                st.waiting_for_equip = False
                self._start_daemon(self._release_equip_wait_after_delay)
            return

        if event.kind == LogParser.EVENT_EVERYTHING_RECEIVED:
            # 直前に採用したVerifiedは本物だった
            st.pending_verified_time = 0.0
            return

        if event.kind == LogParser.EVENT_ROUND_START:
            if st.is_continue_round:
                st.is_continue_round = False
                SharedState.continue_round_end()
            st.in_round                    = True
            st.round_seq                  += 1
            st.round_end_seen              = False
            st.round_type                  = event.round_type
            st.terror_ids                  = []
            st.map_id                      = event.map_id
            st.statistics_sent             = False
            st.fog                         = False
            st.begin_done                  = False
            st.begin_strafe_gain           = 0.0
            st.begin_reject                = []
            st.begin_last_target           = None
            st.is_open_special_round_round = False
            st.item_id_at_round_start      = st.item_id
            st.item_lost_announced         = False
            st.item_lost_this_round        = False
            st.randomizer_item_changed     = False
            st.died_this_round             = False
            st.lived_this_round            = False
            st.item_equipped_after_death   = False
            st.sabotage_murder_this_round  = (
                st.round_type == "Sabotage" and st.pending_sabotage_murder
            )
            st.pending_sabotage_murder     = False
            st.bloodthirsty_creature_variant = False
            st.hungry_home_invader_variant = False
            # アイテムロスト中にラウンドが始まったらフリーズ解除
            # （has_item=Falseのまま → 次のVerified Round Endで再フリーズ）
            if st.waiting_for_equip:
                st.waiting_for_equip = False
                SharedState.equip_freeze_end(st)
                self._log("一時的にアイテムロストフリーズを解除")

            if st.sabotage_murder_this_round:
                self._mark_item_lost("Sabotageマーダー開始: アイテムロスト")
            elif self._round_start_loses_item():
                self._mark_item_lost(f"{st.round_type}: ラウンド開始時にアイテムロスト")

            if st.round_type == "Run":
                st.is_continue_round = False
                self._log(f"Round: {st.round_type} 【死亡待ち・アイテム購入予定】")
                return

            if st.round_type == "Fog":
                if not self._hands_free():
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
            # 続行/霧ラウンドのフリーズは死亡から少し置いて解除する。
            # 猶予中に手動で視点調整などを挟めるようにするため。
            if st.is_continue_round:
                self._start_daemon(self._release_continue_freeze_after_delay,
                                   st.round_seq)
            if st.round_type == "Run":
                self._mark_item_lost("Run死亡: アイテムロスト")
            return

        if event.kind == LogParser.EVENT_RESPAWN:
            if st.in_round:
                self._mark_item_lost("リスポーン: アイテムロスト")
            return

        if event.kind == LogParser.EVENT_ROUND_OVER:
            st.in_round = False
            # Begin待ちの起点。実処理は Verified Round End 側で走るが、
            # 待ち時間はこの時刻から数える（RoundOver→Round End は実測約13秒）。
            st.round_over_time = time.time()
            if self._waiting_for_terror_variant():
                self._send_round_statistics_once()
            announce_on_round_over = (
                not self.cfg.auto_begin
                or st.instance_type != config.INSTANCE_PRIVATE
            )
            if announce_on_round_over and not self._hands_free():
                round_lost_item = self._round_lost_item()
                if self._round_item_warning():
                    if round_lost_item:
                        st.item_id = 0
                    st.waiting_for_equip = True
                elif not st.item_id:
                    st.waiting_for_equip = True
            if st.waiting_for_equip and announce_on_round_over:
                self._action.announce_item_lost_once()
            # Begin移動はここを起点に待つ。クリックとアイテムロスト通知は
            # Verified Round End を待ってから行う（RoundOver時点だと
            # 続行ラウンド中の可能性があり、音声が邪魔になるため）。
            if self.cfg.auto_begin:
                self._start_daemon(self._action.do_after_round)
            return

        if event.kind == LogParser.EVENT_VERIFIED_END:
            if self._waiting_for_terror_variant():
                self._send_round_statistics_once()
            if st.is_continue_round:
                # 通常は RoundOver で解除済み。ここは取りこぼしの保険。
                st.is_continue_round = False
                SharedState.continue_round_end()
                self._log("続行/霧ラウンド終了 → 他窓フリーズ解除（保険）")
            round_lost_item = self._round_lost_item()
            round_item_warning = self._round_item_warning()
            if not self._hands_free():
                if round_item_warning:
                    if round_lost_item:
                        st.item_id = 0
                    if not self.cfg.auto_begin and not st.waiting_for_equip:
                        self._log("ラウンド終了 【⚠ アイテムロスト → RoundOver時に通知予定】")
                    elif SharedState.get_item_begin_mode():
                        st.waiting_for_equip = True
                        SharedState.equip_freeze_start(st)
                        self._log("ラウンド終了 【⚠ アイテムロスト → フォーカス・全窓フリーズ開始】")
                        self._start_daemon(WindowOperator.focus_window, self.cfg.hwnd)
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
                if round_item_warning:
                    if round_lost_item:
                        st.item_id = 0
                    if not self.cfg.auto_begin:
                        st.waiting_for_equip = True
                    # auto_begin時の通知は ActionExecutor がBeginクリック直前に行う
                self._log("ラウンド終了")
            if self.cfg.announce_intermission and not self._hands_free():
                PlaySound.play_sound(self.cfg.voice_intermission)
            st.round_end_seen = True   # Beginのクリック待ちを解除する合図
            return

        if event.kind == LogParser.EVENT_KILLERS_UNKNOWN:
            st.fog = True
            st.round_type = event.round_type or "Fog"
            self._log("テラー不明 → revealed待ち")
            if self._hands_free():
                self._log(f"開始: {st.round_type} 【放置モード→即自爆】")
                self._start_daemon(self._action.do_skip)
            return

        if event.kind == LogParser.EVENT_FOXY:
            self._log("🦊 Foxyが出た！")
            if not self._hands_free():
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
            self._track_randomizer_item_change(event)
            st.item_id = event.item_id
            if st.died_this_round and st.item_id:
                st.item_equipped_after_death = True
            self._log(f"✅ アイテム装備 (id={st.item_id})")
            # 両条件（装備＋Begin）が揃ったら遅延フリーズ解除
            if st.waiting_for_equip and st.begin_done:
                st.waiting_for_equip = False
                self._start_daemon(self._release_equip_wait_after_delay)
            return

        if event.kind == LogParser.EVENT_LIVED:
            st.lived_this_round = True
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
        ids = self._apply_bloodthirsty_creature_variant(ids)
        ids = self._apply_hungry_home_invader_variant(ids, round_type)

        # テラーIDを累積（複数回Killers行が来るラウンド対応）
        for tid in ids:
            if tid not in st.terror_ids:
                st.terror_ids.append(tid)

        if not self._waiting_for_terror_variant():
            self._send_round_statistics_once()

        # インスタンス制限チェック
        itype        = st.instance_type
        is_private   = itype == config.INSTANCE_PRIVATE
        is_group_skip = itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO)
        can_decide   = is_private or is_group_skip

        # 干し芋グループ専用自動自爆
        if is_group_skip and self.cfg.hoshiimo_skip:
            if st.round_type in config.HOSHIIMO_SKIP_ROUNDS:
                # バリアント化しうるテラーがいる場合は出現ログを待ってから判断する
                if self._waiting_for_terror_variant():
                    wait = self._variant_wait_sec()
                    self._log(f"バリアント判定待ち({wait}秒): {st.round_type}")
                    self._start_daemon(self._delayed_group_skip, wait, st.round_seq)
                    return
                if self._should_skip_group_round():
                    self._start_group_skip()
                    return

        # 通常操作はフレ/フレ+/招待/招待+のみ。干し芋では判定と音声だけ通す。
        if not can_decide:
            if st.is_continue_round:
                st.is_continue_round = False
                SharedState.continue_round_end()
            self._log(f"インスタンス制限: 操作スキップ ({itype})")
            return

        # 放置モードの自動操作はprivateのみ（_hands_free が種別を見ている）。
        if self._hands_free():
            if st.open_special_round_wins >= config.OPEN_SPECIAL_ROUND_TARGET_WINS:
                self._log(f"放置モード(3クラ済み): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    self._start_daemon(self._action.do_skip)
                return
            if not st.item_id:
                has_dtm = self.cfg.cancel_afk and DTM_TERROR_ID in st.terror_ids
                if not has_dtm:
                    self._log(f"放置モード(アイテムなし・DTMなし): 即自爆 {st.terror_ids} / {st.round_type}")
                    if not st.is_continue_round and self.cfg.do_skip:
                        self._start_daemon(self._action.do_skip)
                    return
            has_cancel_afk = bool(
                config.OPEN_SPECIAL_ROUND_TERROR_IDS and
                any(t in config.OPEN_SPECIAL_ROUND_TERROR_IDS for t in st.terror_ids) and
                self.cfg.cancel_afk
            )
            if not has_cancel_afk:
                self._log(f"放置モード(DTM/Waldo以外): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    self._start_daemon(self._action.do_skip)
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
                if not self._hands_free():
                    PlaySound.play_sound(self.cfg.voice_continue)
                    self._log("🎙 続行アナウンス再生")
                SharedState.continue_round_start()
                self._log("⏸ 続行/霧ラウンド中 → 他窓フリーズ開始")
            if is_open_special_round_target and is_private and st.open_special_round_wins < config.OPEN_SPECIAL_ROUND_TARGET_WINS:
                st.is_open_special_round_round = True
                self._log(f"3クラ解放ラウンド開始（勝利数: {st.open_special_round_wins}/{config.OPEN_SPECIAL_ROUND_TARGET_WINS}）")
                self._start_daemon(self._action.do_open_special_round_loop)
            elif is_open_special_round_target and not is_private:
                self._log("DTM/WaldoラウンドだがAFK解除はprivateのみ")
            elif is_open_special_round_target:
                self._log("DTM/Waldoラウンドだが3勝達成済み→AFK解除なし")
        else:
            if self.cfg.do_skip and is_private:
                self._start_daemon(self._action.do_skip)

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
