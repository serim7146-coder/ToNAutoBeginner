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
import Recorder
import FogEarlyRead
import ReadJson
import LogParser
import RoundDecision
import RoundSequence
import TerrorReplacement
import GroupRound
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
    def __init__(self, cfg: WindowConfig, keepOn_set: dict, logger, window_idx: int = 0,
                 host_wishes: dict | None = None,
                 host_participants: set | None = None,
                 on_round_settings_cleared=None):
        self.cfg = cfg
        self.keepOn_set = keepOn_set
        # 参加者別の続行希望。追従OFFのときは空（＝Sabotageは通常判定へ落ちる）
        self.host_wishes = host_wishes if host_wishes is not None else {}
        # host_wishes のうち参加者（＋主催者本人）の名前。待機と区別するため。
        # None なら区別しない（host_wishes が参加者だけのとき）
        self.host_participants = host_participants
        self.logger = logger
        self.window_idx = window_idx
        # インスタンスが変わってラウンド指定を解除したことをGUIへ伝える。
        # 渡さなければ何もしない（監視だけで使うときはこれで足りる）
        self._on_round_settings_cleared = on_round_settings_cleared
        # この窓のログが看破できる起動方法か（--enable-sdk-log-levels）。start() で読む
        self.early_read_capable = False
        self.st = WindowState()
        self.sequence = RoundSequence.RoundSequence()
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
        self.early_read_capable = bool(
            self.cfg.log_path
            and FogEarlyRead.launched_for_early_read(self.cfg.log_path))
        # 速度受信は監視中ずっと生かす（プローブごとに開き直すと取りこぼす）
        self._action.start_velocity_receiver()
        self._thread = self._start_daemon(self._run)

    def stop(self):
        self._running = False
        self._stop_event.set()
        self._action.stop_velocity_receiver()

    def _log(self, msg: str):
        self.logger(f"[窓{self.window_idx}] {msg}")

    @staticmethod
    def _parse_instance_type(suffix: str) -> str:
        suffix = suffix or ""
        if f"group({config.HOSHIIMO_GROUP_ID})" in suffix:
            return config.INSTANCE_HOSHIIMO
        if f"group({config.YAKIIMO_GROUP_ID})" in suffix:
            return config.INSTANCE_YAKIIMO
        # 一般の ~group( より前に置くこと。後ろだと other_group に吸われる
        if f"group({config.EMERALD_CITY_GROUP_ID})" in suffix:
            return config.INSTANCE_EMERALD_CITY
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

    def _start_speed_probe(self):
        """次のラウンドの種別を速度で見始める（Verified Round End から）。

        Verified Round End はラウンドごとに1回で、誰が Begin を押しても出る。
        止めるのはラウンド突入（do_speed_detect 側）。横移動は本物の
        Verified で別に始まるので、必ずこの検知の最中に入る。
        速度を受け取れるのは OSC の窓だけ（受信が無ければ即座に抜ける）。
        """
        st = self.st
        if st.speed_probe_done or not SharedState.get_speed_detect():
            return
        st.speed_probe_done = True
        self._log("速度検知 開始")
        self._start_daemon(self._action.do_speed_detect)

    def _round_entry_voice(self, round_type: str) -> str:
        """突入で全窓停止を選んだラウンドに入ったときの音声。対象外なら空文字"""
        return {"Fog": self.cfg.voice_fog,
                "Unbound": self.cfg.voice_unbound,
                "Midnight": self.cfg.voice_midnight,
                "Alternate": self.cfg.voice_alternate,
                "Ghost": self.cfg.voice_ghost}.get(round_type, "")

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

    def _release_round_freeze_after_delay(self, round_seq: int):
        """死亡から一定時間後にラウンド突入フリーズを解除する。

        猶予は霧ラウンドと同じ。待機中に次のラウンドが始まっていたら何もしない
        （ROUND_START 側で解除済み）。
        """
        time.sleep(config.FOG_FREEZE_RELEASE_DELAY_SEC)
        st = self.st
        if not self._running or st.round_seq != round_seq:
            return
        if st.round_freeze_held:
            SharedState.round_freeze_end(st)
            self._log(f"ラウンド突入フリーズ解除（死亡から"
                      f"{config.FOG_FREEZE_RELEASE_DELAY_SEC}秒）")

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
            # 最後の Joining より後の入退室。逆向きに読むので新しい順に溜まる
            player_events = []
            lines = self._iter_log_lines_reversed(
                self.cfg.log_path,
                config.LOG_START_SCAN_CHUNK_BYTES,
            )
            for line in lines:
                event = LogParser.parse(line)
                if not event:
                    continue

                if not found_instance and event.kind in (
                        LogParser.EVENT_PLAYER_JOINED,
                        LogParser.EVENT_PLAYER_LEFT):
                    player_events.append(event)

                if not found_user and event.kind == LogParser.EVENT_USER_AUTH:
                    self._log(f"UserID検出: {event.user_id}")
                    self.st.local_player_name = event.player_name
                    self.st.local_user_id = event.user_id
                    self.st.transformed_uid = ConnectDB.send_Users(event.user_id)
                    self._log(f"transformed_uid: {self.st.transformed_uid}")
                    found_user = True

                if not found_instance and event.kind == LogParser.EVENT_JOINING:
                    found_instance = True
                    self.st.instance_type = self._parse_instance_type(event.suffix)
                    self.st.instance_access = LogParser.instance_access(event.suffix)
                    self._log(f"インスタンスタイプ検出: {self.st.instance_type}")
                    # マクロを途中で始めても人数が分かるように。積み上げないと
                    # 空＝ソロ扱いになり、他人の周回を自分の tnl で裁く
                    self._restore_players(reversed(player_events))

                if found_user and found_instance:
                    break
        except Exception as e:
            self._log(f"検出エラー: {e}")
        if self.st.players_known:
            self._log(f"インスタンス内の人数を復元: 自分以外 "
                      f"{len(self._other_players())}人")
        else:
            self._log("インスタンス内の人数を復元できません → 他の人がいる扱い")

    def _restore_players(self, events):
        """Joining 以降の入退室を古い順に積み上げる"""
        st = self.st
        st.players = set()
        st.player_names = {}
        for event in events:
            self._apply_player_event(event)
        st.players_known = True

    def _apply_player_event(self, event):
        st = self.st
        if event.kind == LogParser.EVENT_PLAYER_JOINED:
            st.players.add(event.user_id)
            if event.player_name:
                st.player_names[event.user_id] = event.player_name
        elif event.kind == LogParser.EVENT_PLAYER_LEFT:
            st.players.discard(event.user_id)
            st.player_names.pop(event.user_id, None)

    def _present_names(self) -> set:
        """このインスタンスにいる人の表示名（自分を含む）"""
        st = self.st
        names = {st.player_names[uid] for uid in st.players if uid in st.player_names}
        if st.local_player_name:
            names.add(st.local_player_name)
        return names

    def _effective_wishes(self) -> dict | None:
        """この窓の判定に使う、参加者別の希望。絞り込めなければ None。

        周回の参加者の希望は全窓で共有しているので、そのまま使うと焼き芋の窓の
        参加者の希望でソロの窓まで判定してしまう。この窓のインスタンスにいる人
        （自分のアカウントを含む）の希望だけにする。

        None（＝従来どおり共有の続行リスト）になるのは、tnl のとき・参加者別の
        希望が無いとき・誰がいるか分からないとき（起動時に復元できなかった）。
        """
        if SharedState.get_list_source() != "host" or not self.host_wishes:
            return None
        if not self.st.players_known:
            return None
        present = self._present_names()
        # 中身が空でもリストを持っている人は残す（全部 OFF＝全部自爆）
        return {name: wish for name, wish in self.host_wishes.items()
                if name in present}

    def _keep_on(self) -> dict:
        """この窓の続行リスト"""
        wishes = self._effective_wishes()
        if wishes is None:
            return self.keepOn_set
        merged: dict = {}
        for wish in wishes.values():
            for round_key, ids in wish.items():
                merged.setdefault(round_key, set()).update(ids)
        return merged

    def _report_unmatched_players(self):
        """希望が見つからない入室者を1回だけ知らせる（表示名の食い違いに気づけるように）"""
        st = self.st
        if self._effective_wishes() is None:
            return
        others = {st.player_names[uid] for uid in self._other_players()
                  if uid in st.player_names}
        missing = sorted(name for name in others - st.unmatched_logged
                         if name not in self.host_wishes)
        if missing:
            st.unmatched_logged.update(missing)
            self._log(f"[主催リスト] 希望が見つからない入室者: {', '.join(missing)}")

    def _other_players(self) -> set:
        return {uid for uid in self.st.players if uid != self.st.local_user_id}

    def _others_present(self) -> bool:
        """このインスタンスに自分以外がいるか。分からなければ「いる」"""
        if not self.st.players_known:
            return True
        return bool(self._other_players())

    # ── メインループ ──────────────────────────
    def _run(self):
        cfg = self.cfg
        if not cfg.log_path or not cfg.log_path.exists():
            self._log(f"ログが見つかりません: {cfg.log_path}")
            return
        self.st.log_pos = cfg.log_path.stat().st_size
        # 過去ログからインスタンスタイプを検出（ワールド入室後の起動に対応）。
        # log_pos を確定させた後に行う。先に検出すると、その間にVRChatが追記した行が
        # 「起動前からあった行」として読み飛ばされる。
        self._detect_instance_from_log()
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
                    self._tick_early_read()
                    try:
                        self._check_group_list_state()
                    except Exception as e:
                        # 通知に失敗してもログ監視は続ける（通知は補助的なもの）
                        self._log(f"主催リストの確認に失敗: {e}")
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

    def _apply_replacements(self, ids: list[int], round_type: str) -> list[int]:
        """合図がもう来ている置き換えを Killers 行のIDに当てる"""
        for row in TerrorReplacement.TABLE:
            if (row.enabled and getattr(self.st, row.flag)
                    and row.matches_round(round_type)):
                ids = row.apply(ids)
        return ids

    def _pending_replacements(self, ids=None) -> list:
        """起こりうるのに、合図がまだ来ていない置き換え"""
        st = self.st
        ids = st.terror_ids if ids is None else ids
        return [row for row in TerrorReplacement.TABLE
                if not getattr(st, row.flag)
                and row.could_apply(ids, st.round_type)]

    def _waiting_for_terror_replacement(self) -> bool:
        """テラーIDがまだ確定していないか。統計登録の待ち合わせに使う。

        IDが変わらない行（Self Inserts）は含めない。統計に送るIDは同じなので
        待つ意味が無い。Gigabytes は元IDが不定なので、Classicの1体構成は
        すべて候補になり、統計はGigabytesの行かラウンド終了まで遅れる。
        """
        return any(row.changes_id for row in self._pending_replacements())

    def _replacement_changes_decision(self, round_type: str) -> bool:
        """置き換えが起きると結論が変わるか。変わるときだけ判定を待つ。

        比べるのは本番と同じ `_plan()`。置き換え後のIDと合図のフラグを
        当てた構成で引き直し、自爆するか・続行を知らせるかが変わるかを見る。
        """
        st = self.st
        pending = self._pending_replacements()
        if not pending:
            return False
        bloodthirsty = st.bloodthirsty_creature_variant
        before = self._outcome(self._plan(round_type, st.terror_ids, bloodthirsty))
        for row in pending:
            after_ids = row.apply(list(st.terror_ids))
            after_bt = bloodthirsty or row.flag == "bloodthirsty_creature_variant"
            after = self._outcome(self._plan(round_type, after_ids, after_bt))
            if after != before:
                return True
        return False

    def _mark_replacement(self, flag: str):
        """合図のログが来た。フラグを立て、判明済みのIDを差し替える"""
        st = self.st
        rows = [row for row in TerrorReplacement.rows_for_flag(flag)
                if row.matches_round(st.round_type)]
        if not rows:
            return          # 対象外のラウンド（Atrached などは Classic だけ）
        setattr(st, flag, True)
        changed = []
        for row in rows:
            if not row.changes_id or not st.terror_ids:
                continue
            if row.source is None and st.terror_ids == [row.target_id()]:
                continue
            replaced = row.apply(st.terror_ids)
            if replaced != st.terror_ids:
                st.terror_ids = replaced
                changed.append(row)
        if changed:
            for row in changed:
                self._log(f"{row.name} に差し替え")
            self._send_round_statistics_once()
        else:
            self._log(f"{' / '.join(row.name for row in rows)} の合図")

    def _variant_wait_sec(self) -> float:
        return config.TERROR_VARIANT_WAIT_SEC

    def _round_still_active(self, round_seq: int) -> bool:
        return self._running and self.st.in_round and self.st.round_seq == round_seq

    def _clear_stale_continue_round(self):
        """問答無用スキップの前に、前のラウンドの続行状態を落とす"""
        st = self.st
        if st.is_continue_round:
            st.is_continue_round = False
            SharedState.continue_round_end()

    def _start_group_skip(self):
        st = self.st
        self._log(f"グループ自動自爆: {st.round_type}")
        self._clear_stale_continue_round()
        # cfg.do_skip は全体スイッチ。OFFなら判定だけ出して自爆はしない
        if self.cfg.do_skip:
            self._start_daemon(self._action.do_skip)

    def _group_decision(self, killers_round_type: str, ids=None) -> str:
        """このラウンドをグループのルールでどう扱うか"""
        st = self.st
        return GroupRound.decide(
            st.instance_type,
            st.round_type,
            st.terror_ids if ids is None else ids,
            killers_round_type=killers_round_type,
            moon_repeat=st.moon_repeat,
            # 自爆リストのうち moon だけがグループでも効く。他の項目
            # （Classic など）は private 限定のまま
            skip_moons={name for name in self.cfg.skip_rounds
                        if name in GroupRound.MOONS},
            sus_players=st.sus_players,
            host_wishes=self._group_wishes(),
            follow_host=bool(self._group_wishes()),
        )

    def _apply_group_decision(self, killers_round_type: str) -> bool:
        """スキップ/全続行なら処理してTrueを返す。通常判定に回すならFalse。"""
        decision = self._group_decision(killers_round_type)
        if decision == GroupRound.SKIP:
            self._start_group_skip()
            return True
        if decision == GroupRound.WANTED:
            # 誰かがそのテラーを欲しがっている。通常判定で続行になったときと
            # 同じ扱いにする（アナウンスと他窓フリーズを出す）
            st = self.st
            was_continue_round = st.is_continue_round
            st.is_continue_round = True
            self._log(f"グループ判定: {st.round_type} 【プレイ】")
            if not was_continue_round:
                if not self._hands_free():
                    PlaySound.play_sound(self.cfg.voice_continue)
                    self._log("🎙 続行アナウンス再生")
                SharedState.continue_round_start()
                if not self._hands_free():          # 放置中は録らない
                    Recorder.on_continue_start(self.window_idx)
                self._log("⏸ 続行/霧ラウンド中 → 他窓フリーズ開始")
            return True
        if decision == GroupRound.CONTINUE:
            # 「全続行」は自爆しないだけ。続行アナウンスも他窓フリーズもしない。
            # 前のラウンドのフリーズが残っていたら落とす——通常は ROUND_START が
            # 落としているが、張りっぱなしは全窓が止まるので念のため
            self._clear_stale_continue_round()
            self._log(f"グループ判定: {self.st.round_type} 【全続行】")
            return True
        return False

    def _needs_host_list(self) -> bool:
        """主催リストが無いと判定してはいけない状態か。

        ソロなら自分の tnl で判定してよい。他の人がいるのに自分の tnl で
        裁くと、他人の周回を自分のリストで自爆させる。自動自爆OFFなら
        自爆しないので要らない。操作しないインスタンス（public など）は
        判定そのものをしないので、ここでも求めない。
        """
        if self.st.instance_type not in (config.INSTANCE_PRIVATE,
                                         *GroupRound.GROUP_INSTANCES):
            return False
        return self.cfg.do_skip and self._others_present()

    def _host_list_missing(self) -> bool:
        return (self._needs_host_list()
                and SharedState.get_list_source() != "host")

    def _wishes_missing(self) -> bool:
        """主催リストはあるが、この窓にいる誰もリストを持っていないか。

        リストが空（全部 OFF）の人がいれば「続行したいものが無い」と分かって
        いるので止めない（全部自爆する）。誰もリストを持っていないときは、
        続行が無いのか分からないので止める。
        """
        if self.st.instance_type not in (config.INSTANCE_PRIVATE,
                                         *GroupRound.GROUP_INSTANCES):
            return False
        if not self.cfg.do_skip:
            return False
        wishes = self._effective_wishes()
        return wishes is not None and not wishes

    def _group_wishes(self) -> dict:
        """Sabotage の参加者別判定に使う希望。

        誰がいるか分からないときは、参加者（＋自分）の希望だけにする（従来どおり）。
        host_wishes には待機（その場にいない人）も入っているので、そのまま使うと
        いない人の希望で続行になる。
        """
        wishes = self._effective_wishes()
        if wishes is not None:
            return wishes
        if self.host_participants is None:
            return self.host_wishes
        return {name: wish for name, wish in self.host_wishes.items()
                if name in self.host_participants}

    def _shared_list_empty(self) -> bool:
        """誰がいるか分からず、共有リストも空か。

        ToN ListTool が全員を待機へ移した瞬間は共有リスト（参加者だけ）が空に
        なる。人数を復元できなかった窓がそれで判定すると、全ラウンド自爆する。
        tnl のときは従来どおり（空の tnl で判定するのは利用者の選択）。
        """
        if self.st.instance_type not in (config.INSTANCE_PRIVATE,
                                         *GroupRound.GROUP_INSTANCES):
            return False
        if not self.cfg.do_skip or SharedState.get_list_source() != "host":
            return False
        # 人ごとの希望はあるのに共有リストだけが空＝全員が待機へ移された。
        # 人ごとの希望も無いなら従来どおり（周回が無ければ GUI 側で tnl へ倒れる）
        return (self._effective_wishes() is None and bool(self.host_wishes)
                and not self.keepOn_set)

    def _list_block_reason(self) -> str:
        """判定してはいけない理由（"host" / "wishes"）。無ければ空文字"""
        if self._host_list_missing():
            return "host"
        if self._wishes_missing() or self._shared_list_empty():
            return "wishes"
        return ""

    def _check_group_list_state(self):
        """
        主催リストの喪失/復帰を拾う。`_on_killers()` と同じ条件で見る。
        """
        reason = self._list_block_reason()
        if reason:
            self._notify_group_list_lost(reason)
        else:
            self._notify_group_list_back()

    def _notify_group_list_lost(self, reason: str = "host"):
        """状態が変わったときだけ1回。ラウンドごとに鳴らさない"""
        st = self.st
        if st.list_lost_notified:
            return
        st.list_lost_notified = True
        st.list_lost_reason = reason
        if reason == "wishes":
            self._log("⚠ この窓にいる人の続行リストがありません → この窓の自爆を停止します")
        else:
            self._log("⚠ 主催リストが取れません → この窓の自爆を停止します")
        if not self._hands_free():
            PlaySound.play_sound(self.cfg.voice_list_lost)

    def _notify_group_list_back(self):
        st = self.st
        if not st.list_lost_notified:
            return
        st.list_lost_notified = False
        if st.list_lost_reason == "wishes":
            self._log("続行リストが見つかりました → 自爆を再開します")
        else:
            self._log("主催リストが戻りました → 自爆を再開します")
        st.list_lost_reason = ""

    def _should_skip_by_round(self, ids=None, bloodthirsty=None) -> bool:
        """privateで「このラウンドは問答無用で自爆」に当たるか。

        続行リストより優先する。ただし自爆指定より上に来るものが3つある——
        3クラ解放（Classicを指定していると3クラ稼ぎが黙って壊れる）、
        Variant（置き換え後のテラーはいつもリストで判定する）、そして
        Self Inserts の Bloodthirsty（リストで表現できないので、リストにも
        自爆指定にも頼れない）。
        """
        st = self.st
        ids = st.terror_ids if ids is None else ids
        if bloodthirsty is None:
            bloodthirsty = st.bloodthirsty_creature_variant
        if st.round_type not in self.cfg.skip_rounds:
            return False
        if RoundDecision.is_open_special_round_target(
                ids, st.round_type, st.open_special_round_wins,
                self.cfg.cancel_afk):
            return False        # 3クラ解放が勝つ。通常判定へ落とす
        if GroupRound.is_variant(ids):
            return False        # Variantは自爆しない。通常判定へ落とす
        if RoundDecision.is_self_inserts_bloodthirsty(
                ids, st.round_type, bloodthirsty):
            # リストで指定する手段が無いので、自爆指定より優先して続行する
            return False
        return True

    def _start_round_skip(self):
        st = self.st
        self._log(f"ラウンド指定自爆: {st.round_type}")
        self._clear_stale_continue_round()
        if self.cfg.do_skip:
            self._start_daemon(self._action.do_skip)

    def _delayed_decision(self, killers_round_type: str, wait_sec: float,
                          round_seq: int):
        """置き換えの合図を待ってから判定する。

        合図が来るか、もう結論が変わらなくなったら残り時間を待たずに進む。
        """
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if not self._round_still_active(round_seq):
                return
            if not self._replacement_changes_decision(killers_round_type):
                break
            time.sleep(config.TERROR_VARIANT_POLL_SEC)
        if not self._round_still_active(round_seq):
            return
        self._decide(killers_round_type)

    # ── ログ行処理 ────────────────────────────
    def _process(self, line: str):
        st = self.st
        if st.early_read_holding:
            self._settle_early_read_by_line(line)
        event = LogParser.parse(line)
        if not event:
            return

        if event.kind == LogParser.EVENT_NETWORK_OBJECT:
            self._on_network_object(event.player_name, LogParser.log_time(line))
            return

        if event.kind == LogParser.EVENT_CREATURE_BLOODTHIRSTY:
            self._mark_replacement("bloodthirsty_creature_variant")
            return

        if event.kind == LogParser.EVENT_HUNGRY_HOME_INVADER:
            self._mark_replacement("hungry_home_invader_variant")
            return

        if event.kind == LogParser.EVENT_ENRAGE:
            self._on_enrage(event.player_name)
            return
        
        if event.kind == LogParser.EVENT_STUNNED:
            self._on_stunned(event.player_name)
            return

        if event.kind == LogParser.EVENT_JOY:
            if self._fog_terror_unknown():
                self._identify_fog_terror(config.JOY_ID, "Joy",
                                          "JOY WILL SOON AWAKEN")
            return

        if event.kind == LogParser.EVENT_MASTER_SWITCHED:
            # 次のラウンドは連続N数の制約を無視して強制的に特殊(S)になる
            self.sequence.on_master_switched()
            if st.early_read_holding:
                # マスターの切り替えで全オブジェクトの同期が走ることがある
                st.early_read_holding = False
                if not st.early_read_void and self._may_show_fog_info():
                    self._log("看破: マスターが切り替わりました → このラウンドの看破は使いません")
                st.early_read_void = True
            return

        if event.kind == LogParser.EVENT_USER_AUTH:
            st.local_player_name = event.player_name
            st.local_user_id = event.user_id
            return

        if event.kind in (LogParser.EVENT_PLAYER_JOINED,
                          LogParser.EVENT_PLAYER_LEFT):
            self._apply_player_event(event)
            return

        if event.kind == LogParser.EVENT_SUS_PLAYER:
            # 選出者は全員ぶん貯める。ROUND_START と同じ秒に来るので、
            # クリアは Verified Round End 側（ROUND_START では消さない）
            if event.player_name not in st.sus_players:
                st.sus_players.append(event.player_name)
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

            # Begin は Verified Round End の後にしか押せない。それより前の Verified は
            # 定義上 Begin 受理ではありえない（RoundOver直後のラウンド結果検証のもの）。
            if not st.round_end_seen:
                self._log("Verified を無視（Verified Round End より前）")
                return

            # 本物として採用。Everything recieved が続くかで事後確認する
            st.pending_verified_time = now
            st.begin_done = True
            self._log("✅ Connecting")
            # 速度検知そのものは EVENT_STRING_DOWNLOAD 側で始めている（Verified は
            # 自分が Begin を押したときしか出ないので、他人がインマスだと来ない）。
            # 横移動だけはここ。自分の Begin が通った後なので、ボタンから離れても
            # Begin を押し損ねない。インマスでなければ来ないので判定も要らない
            if (st.instance_type == config.INSTANCE_PRIVATE
                    and SharedState.get_speed_detect()
                    and not st.speed_strafe_done):
                st.speed_strafe_done = True
                self._start_daemon(self._action.do_speed_strafe)
            # アイテムロスト中のBegin確認：装備済みなら遅延フリーズ解除
            if st.waiting_for_equip and st.item_id:
                st.waiting_for_equip = False
                self._start_daemon(self._release_equip_wait_after_delay)
            return

        if event.kind == LogParser.EVENT_GIGABYTES:
            self._log("👾 The Gigabytes 出現")
            self._mark_replacement("gigabytes")
            return

        if event.kind == LogParser.EVENT_ATRACHED:
            # SonicのVariant
            self._log("🎮 Atrached 出現（SonicのVariant）")
            self._mark_replacement("atrached_variant")
            return

        if event.kind == LogParser.EVENT_STRING_DOWNLOAD:
            # 速度検知の起点にはしない。Begin 以外でも定期的に出るので、
            # 「区間の最初の1件」という前提が崩れる。起点は Verified Round End
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
            # moonが2回目以降かは on_round() でフラグが立つ前に見ておく
            st.moon_repeat                 = self.sequence.is_moon_repeat(
                event.round_type)
            self.sequence.on_round(event.round_type)
            st.terror_ids                  = []
            st.enrage_identified           = None
            st.map_id                      = event.map_id
            st.statistics_sent             = False
            st.statistics_quiet            = False
            st.fog_reading                 = False
            st.early_read_hits             = {}
            st.early_read_tid              = None
            st.early_read_void             = False
            st.early_read_holding          = False
            st.fog                         = False
            st.begin_done                  = False
            st.speed_round_kind            = ""
            st.speed_probe_done            = False
            st.speed_strafe_done           = False
            # 速度検知フリーズは種別に関わらずここで必ず解除する。
            # Punishedの正規の解除条件であると同時に、アイテムを取らないまま
            # ラウンドが始まった8 Pagesの保険でもある（無いと全窓が永久に止まる）。
            if st.speed_freeze_held:
                st.speed_freeze_kind = ""
                SharedState.speed_freeze_end(st)
                self._log("ラウンド開始 → 速度検知フリーズ解除")
            # ラウンド突入フリーズも、種別を判定する前に必ず解除する。
            # 死亡せずに終わった場合（生存・切断など）の永久フリーズを防ぐ保険。
            if st.round_freeze_held:
                SharedState.round_freeze_end(st)
                self._log("ラウンド開始 → ラウンド突入フリーズ解除")
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
            for flag in TerrorReplacement.flags():
                setattr(st, flag, False)
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

            # 指定ラウンドに突入したら全窓を止める。テラー判明は待たない。
            # 自窓の自爆は止めない（止めるのは他窓だけ）。放置モード中はFogに揃えて張らない。
            if st.round_type in SharedState.get_freeze_rounds() and not self._hands_free():
                SharedState.round_freeze_start(st)
                self._log(f"⏸ {st.round_type} 突入 → 全窓フリーズ"
                          f"（死亡{config.FOG_FREEZE_RELEASE_DELAY_SEC}秒後に解除）")
                # 対象は依頼の5つだけ。Punished / 8 Pages の音声は速度検知用
                voice = self._round_entry_voice(st.round_type)
                if voice:
                    PlaySound.play_sound(voice)

            if st.round_type == "Run":
                st.is_continue_round = False
                self._log(f"Round: {st.round_type} 【死亡待ち・アイテム購入予定】")
                return

            if st.round_type == "Fog":
                # 既定では他窓を止めない。止めたいなら突入フリーズで Fog を選ぶ
                # （上の一般の経路で張られる）。
                # is_continue_round と continue_round_start() は必ずセットで外す。
                # 片方だけ残すと、判明時に continue_round_end() が自分の足して
                # いない分を引き、別の窓の本物の続行フリーズを解除してしまう
                # 突入フリーズで Fog を選んでいれば、上で鳴らしている（二重にしない）
                if (config.ANNOUNCE_FOG_ON_ENTRY and not self._hands_free()
                        and "Fog" not in SharedState.get_freeze_rounds()):
                    PlaySound.play_sound(self.cfg.voice_fog)
                self._log(f"開始: {st.round_type}")
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
            # 長押しの終わり際の死亡も拾う（_skip_time は長押しの開始時）
            if st._skip_time > 0 and (time.time() - st._skip_time) <= (
                    config.SUICIDE_HOLD_SEC + config.SUICIDE_CONFIRM_SEC):
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
            if st.round_freeze_held:
                self._start_daemon(self._release_round_freeze_after_delay,
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
            st.fog_reading = False          # 公開前に終わった霧は答え合わせできない
            st.early_read_hits = {}
            # 録画は RoundOver から少し後で止める（続行中でなければ何もしない）
            Recorder.on_round_over(self.window_idx)
            # Begin待ちの起点。実処理は Verified Round End 側で走るが、
            # 待ち時間はこの時刻から数える（RoundOver→Round End は実測約13秒）。
            st.round_over_time = time.time()
            if self._waiting_for_terror_replacement():
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
            # 選出者のクリアはここ。ROUND_START でやると、同じ秒に先に積まれた
            # Sus player を消してしまう（ログ上は Sus player の方が前に来る）
            st.sus_players = []
            if self._waiting_for_terror_replacement():
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
            self._start_speed_probe()
            return

        if event.kind == LogParser.EVENT_KILLERS_UNKNOWN:
            st.fog = True
            st.round_type = event.round_type or "Fog"
            # 看破の行を読むのはここから公開/RoundOver まで
            st.fog_reading = (config.FOG_EARLY_READ_ENABLED
                              and self.early_read_capable)
            self._log("テラー不明 → revealed待ち")
            if self._hands_free():
                self._log(f"開始: {st.round_type} 【放置モード→即自爆】")
                self._start_daemon(self._action.do_skip)
            return

        if event.kind == LogParser.EVENT_FOXY:
            self._log("🦊 Foxyが出た！")
            if not self._hands_free():
                PlaySound.play_sound(self.cfg.voice_foxy)
            self._mark_replacement("foxy")
            if (st.round_type in GroupRound.FOG_ROUND_TYPES and not st.terror_ids
                    and st.enrage_identified is None):
                # 霧でテラー不明のまま Foxy が出た。Foxy で確定する。
                # st.round_type は書き換えない（「Fog」の自爆指定が効かなくなる）。
                # オルタ枠であることは引数で伝える（焼き芋の判定が見る）
                self._on_killers([config.FOXY_ID],
                                 GroupRound.FOG_ALTERNATE_ROUND_TYPE,
                                 revealed=True)
                # 後から revealed が来ても判定し直さない（二重に自爆する）
                st.enrage_identified = config.FOXY_ID
            return

        if event.kind == LogParser.EVENT_KILLERS_REVEALED:
            st.fog_reading = False
            self._on_killers(event.terror_ids or [], event.round_type, revealed=True)
            # 答え合わせは公開の後（食い違いの警告はもう出してよい）
            self._check_early_read(event.terror_ids or [], event.round_type)
            return

        if event.kind == LogParser.EVENT_JOINING:
            st.instance_type = self._parse_instance_type(event.suffix)
            st.instance_access = LogParser.instance_access(event.suffix)
            # 別インスタンスに入った。ラウンドの並びもmoonの消化状況も分からない
            self.sequence.reset()
            st.enrage_identified = None
            # 入室した瞬間からの入退室はすべて見えるので、ここからは信用できる
            st.players = set()
            st.player_names = {}
            st.unmatched_logged = set()
            st.players_known = True
            st.instance_seq += 1
            # 自爆設定の持ち越しは危ない。インスタンスが変わったら毎回外す
            if self.cfg.skip_rounds or self.cfg.continue_rounds:
                self.cfg.skip_rounds = set()
                self.cfg.continue_rounds = set()
                self._log("インスタンスが変わりました → ラウンド指定を解除しました")
            # GUIのチェックは監視開始後でも変えられるので、設定が空でも呼ぶ。
            # 片方だけ外れていると、表示と動きが食い違う
            if self._on_round_settings_cleared:
                self._on_round_settings_cleared(self.window_idx)
            self._log(f"インスタンスタイプ: {st.instance_type}")
            return

        if event.kind == LogParser.EVENT_ITEM_EQUIP:
            self._track_randomizer_item_change(event)
            st.item_id = event.item_id
            if st.speed_freeze_kind == "8pages":
                # 8 Pages はスキャナーを取れたら再開してよい
                st.speed_freeze_kind = ""
                SharedState.speed_freeze_end(st)
                self._log("✅ アイテム取得 → 速度検知フリーズ解除")
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
    def _on_enrage(self, name: str):
        """Fog のテラー不明中に、Enrage の名前からテラーを判明させる。

        名前が terrors.json に一意に一致したときだけ使う。個体名（Furnace
        など）は表に無いので、そのときは従来どおり revealed を待つ。
        """
        if not config.ENRAGE_IDENTIFY_ENABLED:
            return
        if not self._fog_terror_unknown():
            return
        tid = ReadJson.fog_terror_id_by_name(name, config.TERRORS)
        if tid is None:
            self._log(f"Enrage: {name}（テラー表に無し→revealed待ち）")
            return
        self._identify_fog_terror(tid, "Enrage", name)
        
    def _on_stunned(self, name: str):
        """
        Fog のテラー不明中に、スタンされた名前からテラーを判明させる。
        名前が terrors.json に一意に一致したときだけ使う。表に無いものは、そのときは従来どおり revealed を待つ。
        """
        if not config.STUNNED_IDENTIFY_ENABLED:
            return
        if not self._fog_terror_unknown():
            return
        tid = ReadJson.fog_terror_id_by_name(name, config.TERRORS)
        if tid is None:
            self._log(f"Stunned: {name}（テラー表に無し→revealed待ち）")
            return
        self._identify_fog_terror(tid, "Stunned", name)

    def _fog_terror_unknown(self) -> bool:
        """霧でテラーがまだ分からず、このラウンドで前倒しもしていないか"""
        st = self.st
        return (st.round_type in GroupRound.FOG_ROUND_TYPES   # 8 Pages は対象外
                and not st.terror_ids
                and st.enrage_identified is None)

    def _identify_fog_terror(self, tid: int, how: str, name: str,
                             early_read: bool = False):
        """霧のテラーを前倒しで判明させ、判定に回す（看破 / Enrage / Joy / スタン）。

        使い分けはここの入口だけで決める。
        - 通常のログに出る行（Enrage / Joy / スタン）: どのインスタンスでも
          表示して判定する。DB へも公開のときと同じく普通に送る
        - 看破（early_read。--enable-sdk-log-levels 由来の行）: 看破してよい
          インスタンス（Invite / Invite+ / Friends / Group Only）でなければ、
          判定には一切使わず DB に黙って送るだけ（公開まで何も出さない）
        """
        st = self.st
        if early_read:
            if not self._may_show_fog_info():
                self._send_early_statistics(tid)
                return
            # 判定＋DB。DB への送信は黙って行う（看破した情報なので）
            st.statistics_quiet = True
        # `Killers is unknown` の行には「Fog (Alternate)」が出ない。焼き芋は
        # オルタネイト枠のFogだけを続行リスト判定に回すので、枠を伝えないと
        # リストを見ずに自爆する。alternate のテラーが出た時点で枠は確定する
        round_type = st.round_type
        if (ReadJson.is_alternate_terror(tid, config.TERRORS)
                and round_type != GroupRound.FOG_ALTERNATE_ROUND_TYPE):
            self._log(f"オルタネイト確定: {round_type} → "
                      f"{GroupRound.FOG_ALTERNATE_ROUND_TYPE}")
            round_type = GroupRound.FOG_ALTERNATE_ROUND_TYPE
        # st.round_type は書き換えない。ラウンド指定自爆（cfg.skip_rounds）が
        # 「Fog」で持っているので、書き換えるとその指定が黙って効かなくなる
        self._log(f"🔎 テラー判明({how}): {name} → {format_terror_ids([tid])}")
        # 判定を通してからフラグを立てる。先に立てると、この呼び出し自身が
        # 「前倒し済み」と見なされて素通りしてしまう
        self._on_killers([tid], round_type, revealed=True)
        st.enrage_identified = tid

    def _on_killers(self, ids: list[int], round_type: str, revealed: bool):
        st = self.st
        st.fog = False

        ids = RoundDecision.normalize_killer_ids(ids, round_type, st.round_type)
        ids = self._apply_replacements(ids, round_type)

        # テラーIDを累積（複数回Killers行が来るラウンド対応）
        for tid in ids:
            if tid not in st.terror_ids:
                st.terror_ids.append(tid)

        # 名前は判定より先に出す。この下には設定・インスタンス種別による
        # early return が5つあり、そこを通ると何が出たのか分からなくなるため。
        verb = "revealed" if revealed else "set"
        self._log(f"Terror {verb}: {format_terror_ids(st.terror_ids)} / {round_type}")

        if not self._waiting_for_terror_replacement():
            self._send_round_statistics_once()

        if revealed and st.enrage_identified is not None:
            # Enrage で前倒し済み。やり直すと二重にアナウンスして二重に自爆する。
            # 食い違いは残す——前倒しの精度は半分なので、外したときに分かるように
            if st.enrage_identified not in ids:
                self._log(
                    f"⚠ Enrageの判明({format_terror_ids([st.enrage_identified])})と "
                    f"revealed({format_terror_ids(ids)})が食い違いました")
            return

        # インスタンス制限チェック
        itype        = st.instance_type
        is_private   = itype == config.INSTANCE_PRIVATE
        is_group_skip = itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO)
        can_decide   = is_private or is_group_skip

        # 他の人がいるのに主催リストが取れない窓、この窓にいる誰の希望も
        # 無い窓はここで止める。
        reason = self._list_block_reason()
        if reason:
            self._notify_group_list_lost(reason)
            return
        self._notify_group_list_back()
        self._report_unmatched_players()

        # 置き換えで結論が変わるときだけ待つ（自爆指定の有無に関係なく）。
        # 放置モードは待たずに即自爆する
        if (not self._hands_free()
                and self._replacement_changes_decision(round_type)):
            wait = self._variant_wait_sec()
            self._log(f"Variant判定待ち({wait}秒): {st.round_type}")
            self._start_daemon(self._delayed_decision, round_type,
                               wait, st.round_seq)
            return
        self._decide(round_type)

    # ── 判定 ──────────────────────────────
    def _plan(self, round_type: str, ids, bloodthirsty: bool) -> tuple:
        """このテラー構成をどう扱うかを決める。副作用なし。

        `_decide()` がこれをそのまま実行し、置き換え待ちの判断も
        これで前後を比べる。同じ関数なので、待つ判断と本番の判定が
        食い違わない。
        """
        st = self.st
        itype = st.instance_type
        is_private = itype == config.INSTANCE_PRIVATE
        is_group = itype in GroupRound.GROUP_INSTANCES
        if is_group:
            decision = self._group_decision(round_type, ids)
            if decision != GroupRound.NORMAL:
                return ("group", decision)
        # 通常操作はフレ/フレ+/招待/招待+のみ。干し芋では判定と音声だけ通す。
        if not (is_private or is_group):
            return ("restricted",)
        # 放置モードの自動操作はprivateのみ（_hands_free が種別を見ている）。
        if self._hands_free():
            reason = self._hands_free_skip_reason(ids)
            if reason:
                return ("hands_free", reason)
        # privateのラウンド指定。全続行が先。続行リストは見ない
        if is_private and st.round_type in self.cfg.continue_rounds:
            return ("continue_rounds",)
        # privateのラウンド指定自爆。続行リストより優先する
        if (is_private and self.cfg.skip_rounds
                and self._should_skip_by_round(ids, bloodthirsty)):
            return ("round_skip",)
        decision = RoundDecision.decide_killers(
            self._keep_on(), ids, st.round_type,
            st.open_special_round_wins, self.cfg.cancel_afk,
            bloodthirsty_variant=bloodthirsty)
        return ("list", decision.is_continue_round,
                decision.is_open_special_round_target)

    @staticmethod
    def _outcome(plan: tuple) -> str:
        """手順から、利用者に見える結果だけを取り出す（待つかの比較用）"""
        kind = plan[0]
        if kind == "group":
            return {GroupRound.SKIP: "skip", GroupRound.CONTINUE: "quiet",
                    GroupRound.WANTED: "continue"}[plan[1]]
        if kind in ("hands_free", "round_skip"):
            return "skip"
        if kind in ("restricted", "continue_rounds"):
            return "quiet"
        _kind, is_continue, open_special = plan
        if not is_continue:
            return "skip"
        return "open_special" if open_special else "continue"

    def _hands_free_skip_reason(self, ids) -> str | None:
        """放置モードで即自爆するならその理由。DTM/Waldo は例外"""
        st = self.st
        if st.open_special_round_wins >= config.OPEN_SPECIAL_ROUND_TARGET_WINS:
            return f"放置モード(3クラ済み): 即自爆 {ids} / {st.round_type}"
        if not st.item_id:
            has_dtm = self.cfg.cancel_afk and DTM_TERROR_ID in ids
            if not has_dtm:
                return (f"放置モード(アイテムなし・DTMなし): 即自爆 {ids} / "
                        f"{st.round_type}")
        has_cancel_afk = bool(
            config.OPEN_SPECIAL_ROUND_TERROR_IDS and
            any(t in config.OPEN_SPECIAL_ROUND_TERROR_IDS for t in ids) and
            self.cfg.cancel_afk
        )
        if not has_cancel_afk:
            return f"放置モード(DTM/Waldo以外): 即自爆 {ids} / {st.round_type}"
        return None

    def _decide(self, round_type: str):
        """`_plan()` が決めた手順を実行する"""
        st = self.st
        plan = self._plan(round_type, st.terror_ids,
                          st.bloodthirsty_creature_variant)
        kind = plan[0]
        if kind == "group":
            self._apply_group_decision(round_type)
            return
        if kind == "restricted":
            if st.is_continue_round:
                st.is_continue_round = False
                SharedState.continue_round_end()
            self._log(f"インスタンス制限: 操作スキップ ({st.instance_type})")
            return
        if kind == "hands_free":
            self._log(plan[1])
            if not st.is_continue_round and self.cfg.do_skip:
                self._start_daemon(self._action.do_skip)
            return
        if kind == "continue_rounds":
            self._log(f"ラウンド指定で続行: {st.round_type}")
            self._clear_stale_continue_round()
            return
        if kind == "round_skip":
            self._start_round_skip()
            return
        self._decide_with_keep_on_set(round_type)

    # ── 通常判定（続行リスト照合） ──────────────
    def _decide_with_keep_on_set(self, round_type: str):
        st = self.st
        is_private = st.instance_type == config.INSTANCE_PRIVATE
        is_group = st.instance_type in GroupRound.GROUP_INSTANCES

        all_ids = st.terror_ids
        was_continue_round = st.is_continue_round
        decision = RoundDecision.decide_killers(
            self._keep_on(),
            all_ids,
            st.round_type,
            st.open_special_round_wins,
            self.cfg.cancel_afk,
            bloodthirsty_variant=st.bloodthirsty_creature_variant,
        )
        is_open_special_round_target = decision.is_open_special_round_target
        st.is_continue_round = decision.is_continue_round

        if was_continue_round and not st.is_continue_round:
            SharedState.continue_round_end()

        tag  = "【プレイ(DTM/Waldo)】" if is_open_special_round_target else (
               "【プレイ】" if st.is_continue_round else "【スキップ】")
        self._log(f"判定: {format_terror_ids(all_ids)} / {round_type} {tag}")

        if st.is_continue_round:
            if not is_open_special_round_target and not was_continue_round:
                if not self._hands_free():
                    PlaySound.play_sound(self.cfg.voice_continue)
                    self._log("🎙 続行アナウンス再生")
                SharedState.continue_round_start()
                # 録画も同じ瞬間に始める。霧の前倒し判明（Enrage など）も
                # ここへ流れ込むので、判明の経路ごとには足さない。放置中は録らない
                if not self._hands_free():
                    Recorder.on_continue_start(self.window_idx)
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
            # 続行にならなければ自爆する。グループもここに含める
            if self.cfg.do_skip and (is_private or is_group):
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
            quiet=st.statistics_quiet,
        )

    # ── 霧の看破 ─────────────────────────────
    def _may_show_fog_info(self) -> bool:
        """公開前の霧の情報を判定・表示に使ってよいインスタンスか"""
        return FogEarlyRead.early_read_allowed(self.st.instance_access)

    def _send_early_statistics(self, tid: int):
        """DB にだけ送る。判定には使わない。送信について何も出さない"""
        st = self.st
        if st.statistics_sent:
            return
        st.statistics_sent = True
        ConnectDB.send_ToNRoundStatistics(
            st.round_type, [tid], st.map_id, st.transformed_uid, quiet=True)

    def _on_network_object(self, name: str, at: Optional[float] = None):
        """看破: 霧の間に [NetworkProcessing] に出たオブジェクト名を照合する。

        最初に当たった名前はすぐには使わず、FogEarlyRead.HOLD_SEC のあいだ保留する。
        そのあいだに当たった ID が1種類だけなら使い、2種類以上なら void にする。
        at はその行のログ時刻（無ければ tick で確定させる）
        """
        st = self.st
        if not st.fog_reading:
            return
        key = ReadJson.normalize_object_name(name)
        if not key or key in st.early_read_hits:
            return
        tid = ReadJson.fog_terror_id_by_object_name(name, config.TERRORS)
        if tid is None or not FogEarlyRead.trust.usable(key):
            return
        st.early_read_hits[key] = (tid, name)
        if len({t for t, _n in st.early_read_hits.values()}) > 1:
            # 取り違えを避けるため、このラウンドの看破は使わない
            if not st.early_read_void and self._may_show_fog_info():
                self._log("看破: 別々のテラー名が見えました → このラウンドの看破は使いません")
            st.early_read_void = True
            return
        if st.early_read_tid is not None or st.early_read_void or st.early_read_holding:
            return
        if not self._fog_terror_unknown():
            return          # Enrage 系で先に決まった（二重に判定しない）
        st.early_read_holding = True
        st.early_read_hold_log_t = at
        st.early_read_hold_wall = time.time()

    def _settle_early_read_by_line(self, line: str):
        """保留の開始から HOLD_SEC を過ぎた時刻の行が来たら、その行より先に確定させる"""
        start = self.st.early_read_hold_log_t
        at = LogParser.log_time(line)
        if start is not None and at is not None and at - start > FogEarlyRead.HOLD_SEC:
            self._settle_early_read()

    def _tick_early_read(self):
        """行が来ないまま時間が過ぎたときも、保留を確定させる（監視ループから呼ぶ）"""
        st = self.st
        wait = FogEarlyRead.HOLD_SEC + FogEarlyRead.HOLD_TICK_MARGIN_SEC
        if st.early_read_holding and time.time() - st.early_read_hold_wall >= wait:
            self._settle_early_read()

    def _settle_early_read(self):
        """保留を終える。そのあいだの ID が1種類だけ（void でない）なら、それで看破する。

        保留を捨てる条件はここにまとめる: 2種類目の ID・マスター切り替え（void）、
        公開・RoundOver（fog_reading が落ちる）、Enrage 系で先に判明（テラー不明でない）
        """
        st = self.st
        st.early_read_holding = False
        if st.early_read_void or not st.fog_reading or not self._fog_terror_unknown():
            return
        tid, name = next(iter(st.early_read_hits.values()))
        st.early_read_tid = tid
        self._identify_fog_terror(tid, "看破", name, early_read=True)

    def _check_early_read(self, ids: list[int], round_type: str):
        """答え合わせ。看破で見えた名前ごとに、公開と一致したかを記録する。

        公開の ID はオルタネイトなら +134 してから比べる（33 → 167 Walpurgisnacht）。
        一度でも食い違った名前は以後使わない。
        """
        st = self.st
        hits, st.early_read_hits = st.early_read_hits, {}
        if not hits or not ids:
            return
        public = RoundDecision.normalize_killer_ids(list(ids)[:1], round_type)[0]
        for key, (tid, name) in hits.items():
            matched = tid == public
            FogEarlyRead.trust.record(key, matched)
            if not matched:
                self._log(f"⚠ 看破の名前「{name}」が公開と食い違いました。"
                          "以後この名前は使いません")
