import time
from datetime import datetime
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

import config
import SharedState
import WindowOperator
import PlaySound
import ConnectDB
import DebugLog
import ItemCatalog
import Recorder
import FogEarlyRead
import ReadJson
import VerifiedTracker
import LogParser
import MatchTNL
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


_UNSET = object()       # 「まだ計算していない」を None（対応づけできない）と分ける


def format_terror_ids(ids: list[int]) -> str:
    return ", ".join(_terror_name_cached(tid) for tid in ids)


# 所持アイテムをなくした理由（公開ログ）。Punished などのラウンド開始は「<種類> 開始」
HELD_LOST_PAGE = "ページ取得"
HELD_LOST_RESPAWN = "リスポーン"
HELD_LOST_RUN_DEATH = "Run 死亡"
HELD_LOST_SABOTAGE = "Sabotage マーダー"
HELD_LOST_INSTANCE = "インスタンス移動"


# ═══════════════════════════════════════════════
#  ログ監視ワーカー
#  ログ読み込み・イベント処理・ラウンド判定を担当する。
#  窓操作（自爆・Begin・AFK防止）は ActionExecutor に委譲する。
# ═══════════════════════════════════════════════
class LogMonitor:
    def __init__(self, cfg: WindowConfig, keepOn_set: dict, logger, window_idx: int = 0,
                 host_wishes: dict | None = None,
                 host_participants: set | None = None,
                 host_tabs: dict | None = None,
                 on_round_settings_cleared=None):
        self.cfg = cfg
        self.keepOn_set = keepOn_set
        # 参加者別の続行希望。追従OFFのときは空（＝Sabotageは通常判定へ落ちる）
        self.host_wishes = host_wishes if host_wishes is not None else {}
        # host_wishes のうち参加者（＋主催者本人）の名前。待機と区別するため。
        # None なら区別しない（host_wishes が参加者だけのとき）
        self.host_participants = host_participants
        # ToN ListTool の複窓対応のタブ。{"version": n, "tabs": {タブ: {...}}}
        # 全窓で共有する dict を掴む（mainGUI が in-place で入れ替える）
        self.host_tabs = host_tabs if host_tabs is not None else {}
        self._tab_key = None        # 対応づけを計算したときの (版, 在室者)
        self._tab_index = _UNSET    # 対応づいたタブ（_UNSET はまだ計算していない）
        self.logger = logger
        self.window_idx = window_idx
        # インスタンスが変わってラウンド指定を解除したことをGUIへ伝える。
        # 渡さなければ何もしない（監視だけで使うときはこれで足りる）
        self._on_round_settings_cleared = on_round_settings_cleared
        # この窓のログが看破できる起動方法か（--enable-sdk-log-levels）。start() で読む
        self.early_read_capable = False
        self.st = WindowState()
        self.st.window_idx = window_idx
        self.sequence = RoundSequence.RoundSequence()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 定期の Verified を見分ける（窓ごと。起動時の遡りで位相を取り戻す）
        self._verified = VerifiedTracker.VerifiedTracker()
        # 定期として無視した Verified（ログの時刻, 覚える前の定期の時刻）。すぐラウンドが始まったら
        # 本物の Begin だったので、覚えた位相を元に戻す（CU）
        self._ignored_verified = None
        self._action = ActionExecutor(
            cfg=cfg,
            st=self.st,
            is_running=lambda: self._running,
            log=self._log,
            auto_begin_active=self._auto_begin_active,
        )

    def _auto_begin_active(self) -> bool:
        """ツールが Begin を押している窓か（自動 Begin が ON で private）。

        RoundOver のアイテムロスト通知の切り分けと、速度検知の音声の出し分けが
        同じ判定を使う。1か所に置き、ActionExecutor には関数として渡す
        """
        return bool(self.cfg.auto_begin
                    and self.st.instance_type == config.INSTANCE_PRIVATE)

    def _item_begin_mode_active(self) -> bool:
        """アイテム取得→Begin モードが、この窓で効いているか。

        自動 Begin が機能していない窓では効かせない（モードが ON でも通常モードと
        同じ動き）。LogMonitor の中でモードを見るところは全部これを使う
        """
        return SharedState.get_item_begin_mode() and self._auto_begin_active()

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
        # 借りた前面は返さない（止めた瞬間に前面が飛ぶと驚くため）。札は捨てる
        SharedState.discard_front_loan(self.st)
        # 押しっぱなしのまま残さない（使っていた2つを離す）
        if self._action.chase_stop():
            self._log("チェイス停止（監視の停止）")
        self._running = False
        self._stop_event.set()
        self._action.stop_velocity_receiver()

    def on_chase_key(self, direction: str, key_label: str):
        """チェイスのキー（この窓が前面のときに押された）。ラウンド中だけ"""
        if not self.st.in_round:
            self._log("チェイスはラウンド中だけ使えます")
            return
        name = "時計回り" if direction == "cw" else "反時計回り"
        result = self._action.chase_key(direction)
        if result == "start":
            self._log(f"チェイス開始（{name}・{key_label}）")
        elif result == "stop":
            self._log(f"チェイス停止（{key_label}）")
        else:
            self._log(f"チェイスの向きを{name}に切り替え")

    def cancel_suicide(self):
        """自爆キャンセルのキー（CL）。ActionExecutor.cancel_suicide() の結果を返す"""
        return self._action.cancel_suicide()

    def _log(self, msg: str):
        self.logger(f"[窓{self.window_idx}] {msg}")

    def _debug_event(self, event):
        """debug.log へ: 読んだイベントを1行。[NetworkProcessing] の名前は書かない
        （NG のインスタンスで看破の材料が漏れないように。呼ぶ側で外している）。
        入退室の名前・URL も書かない（必要な情報だけ）"""
        if event.kind == LogParser.EVENT_VERIFIED:
            return          # 受理か無視かが決まってから1行（_debug_verified）
        parts = []
        if event.round_type:
            parts.append(f"種類={event.round_type}")
        if event.map_id:
            parts.append(f"マップ={event.map_id}")
        if event.terror_ids:
            parts.append(f"テラー={list(event.terror_ids)}")
        if event.kind == LogParser.EVENT_ITEM_EQUIP:
            parts.append(f"アイテム={event.item_id}")
        if event.kind == LogParser.EVENT_PAGE_COLLECTED:
            parts.append(f"ページ={event.page}")
        if event.kind == LogParser.EVENT_JOINING:
            parts.append(f"公開範囲={LogParser.instance_access(event.suffix)}")
        if event.kind in (LogParser.EVENT_ENRAGE, LogParser.EVENT_STUNNED):
            parts.append(f"名前={event.player_name}")       # 通常のログに出る行（公開の情報）
        self._debug(f"[事象] {event.kind}" + (" " + " ".join(parts) if parts else ""))

    def _debug_verified(self, result: str):
        """debug.log へ: `Verified` の行を読んだ記録に、判定の結果を付ける（CQ）"""
        self._debug(f"[事象] verified → {result}")

    def _debug(self, msg: str):
        """デバッグログへ（公開ログ＝logger には出さない）"""
        DebugLog.write(f"[窓{self.window_idx}] {msg}")

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

    def _focus_for_freeze(self, label: str):
        """フリーズを張る窓を前面化する。どの窓を操作すればよいか分かるように。

        速度検知フリーズ（ActionExecutor._focus_for_speed_freeze）と同じ扱い。
        付随機能なので、取れなくてもアナウンス・フリーズ・録画は続ける。
        label はログに出す名前（`続行ラウンド` / `ラウンド突入フリーズ`）。

        呼び出し側は自分がフリーズを張る**前**に呼ぶこと。後だと自分の分を
        数えてしまい、常に前面化しなくなる。
        """
        if self._hands_free():
            # 人が見ていないので意味が無く、ほかの窓のカーソル方式 Begin の
            # 邪魔になる。アナウンスと録画と同じ扱い
            return
        if label == "続行ラウンド":
            # 続行ラウンドは何よりも優先して前面化する（依頼者。CZ）。止めるのは、ほかの窓が続行ラウンドを
            # やっているときだけ（装備待ち・速度検知・突入のフリーズでは止めない。ほかの窓のアイテム取得は
            # 続行のフリーズを見て手を引く）。数える前に呼ぶので自分の分は入っていない
            if SharedState.get_continue_round_count() > 0:
                self._log("ほかの窓が続行ラウンド中なので前面化しません")
                return
        elif not SharedState.nothing_frozen():
            # フリーズを張った窓を操作している最中に前面を奪わない。種別は
            # 問わない（依頼者の指摘。続行フリーズだけ見ると 8 Pages の最中に奪う）。
            # 自分が張っているときも、自分の分で False になる
            self._log("ほかの窓がフリーズ中なので前面化しません")
            return
        self._start_daemon(self._focus_this_window_for, label)

    def _focus_this_window_for(self, label: str):
        with SharedState._GLOBAL_ACTION_LOCK:
            ok, loan = WindowOperator.borrow_front(self.cfg.hwnd)
            if ok:
                SharedState.keep_front_loan(self.st, loan)    # フリーズが解けたら返す
                self._log(f"この窓を前面化しました（{label}）")
            else:
                self._log(f"⚠ 前面化に失敗（{label}は継続）")

    def _release_continue_freeze_after_delay(self, round_seq: int):
        """死亡から一定時間後に続行ラウンドのフリーズを解除する。

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
            st.open_special_continue = False
            SharedState.continue_round_end(st)
            self._log(f"続行ラウンド終了 → 他窓フリーズ解除（死亡から{delay}秒）")

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
        if self._hands_free():
            return          # 完全放置モードの窓では速度検知を動かさない（ログも出さない）
        st.speed_probe_done = True
        self._log("速度検知 開始")
        self._start_daemon(self._action.do_speed_detect)

    def _round_entry_voice(self, round_type: str) -> str:
        """ラウンド突入でフリーズを選んだラウンドに入ったときの音声。対象外なら空文字"""
        return {"Fog": self.cfg.voice_fog,
                "Unbound": self.cfg.voice_unbound,
                "Midnight": self.cfg.voice_midnight,
                "Alternate": self.cfg.voice_alternate,
                "Ghost": self.cfg.voice_ghost}.get(round_type, "")

    def _undo_ignored_verified(self):
        """ラウンド開始: 定期として無視した Verified の後 VERIFIED_ROUND_START_WAIT_SEC 以内なら、それは
        本物の Begin だった。定期として覚えた分を取り消す（CU）"""
        ignored, self._ignored_verified = self._ignored_verified, None
        if ignored is None or not self.st.log_now:
            return
        at, phase_before = ignored
        if 0 <= self.st.log_now - at <= config.VERIFIED_ROUND_START_WAIT_SEC:
            self._verified.last_periodic = phase_before
            self._debug(f"[状態] 無視した Verified の {self.st.log_now - at:.1f}秒後にラウンド開始"
                        f" → 本物の Begin だった。定期の位相を戻す")

    def _check_pending_verified(self):
        """採用したVerifiedにラウンド開始が続かなければ、定期シグナルだった。

        位相を掴む唯一の手段。定期と分かった時刻を覚えて、次からは無視できる
        ようにする（横移動はもう終わっているので、ここでは止めない）。
        受理と取り違えていたので、Begin は通っていない。begin_done を戻し、
        ツールが Begin を押す窓なら押し直す（戻さないと誰も押さずに止まる。BW）
        """
        st = self.st
        if not st.pending_verified_time or not st.log_now:
            return
        if (st.log_now - st.pending_verified_time) <= config.VERIFIED_ROUND_START_WAIT_SEC:
            return
        self._verified.on_begin_not_followed(st.pending_verified_time)   # 保険
        st.pending_verified_time = 0.0
        self._log("直前の Verified は定期シグナルでした（ラウンド開始が来ない）")
        if not self._running or st.in_round or not st.begin_done:
            return
        st.begin_done = False
        self._log("Begin が通っていませんでした（定期の Verified でした）→ 押し直します")
        if self._auto_begin_active():
            self._start_daemon(self._action.do_begin_again, st.round_seq)

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

    def _release_speed_freeze_after_delay(self, round_seq: int):
        """8 Pages でスキャナーを取れたあと、猶予を置いてフリーズを解除する。

        待つ長さはアイテムロストの装備解除と同じ定数（依頼者の「同様にして」）。
        片方を変えれば両方変わるので、別の定数は作らない。
        待っている間に次のラウンドが始まっていたら何もしない
        （ROUND_START の無条件解除が済ませている）
        """
        time.sleep(config.EQUIP_RELEASE_DELAY_SEC)
        if not self._running or self.st.round_seq != round_seq:
            return
        SharedState.speed_freeze_end(self.st)
        self._log("✅ アイテム取得 → 速度検知フリーズ解除"
                  f"（{config.EQUIP_RELEASE_DELAY_SEC}秒後）")

    def _release_equip_freeze_after_equip(self):
        """Begin を押さない窓で装備した: 猶予の後に装備待ちフリーズを外す（もう外れていれば何もしない）"""
        time.sleep(config.EQUIP_RELEASE_DELAY_SEC)
        if not self.st.equip_freeze_held:
            return
        SharedState.equip_freeze_end(self.st)
        self._log("✅ アイテム装備 → フリーズ解除")

    def _release_equip_wait_after_delay(self):
        time.sleep(config.EQUIP_RELEASE_DELAY_SEC)
        SharedState.equip_freeze_end(self.st)
        if SharedState.get_equip_freeze_count() == 0:
            self._log("✅ 全窓フリーズ解除")
        else:
            self._log("✅ この窓の装備待ち解除（他窓の装備待ちが残っています）")

    @staticmethod
    def _iter_log_lines_reversed(path, chunk_size: int, end: Optional[int] = None):
        """末尾（end を渡せばその位置）から先頭へ向かって1行ずつ返す"""
        if chunk_size <= 0:
            chunk_size = 256 * 1024

        with open(path, "rb") as f:
            pos = f.seek(0, 2)
            if end is not None:
                pos = min(pos, max(0, end))
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
                if LogParser.JOINING_MARK not in line:
                    continue            # 前絞り（parse() は重い）
                event = LogParser.parse(line)
                if event and event.kind == LogParser.EVENT_JOINING:
                    return cls._parse_instance_type(event.suffix)
        except Exception:
            DebugLog.exception("LogMonitor.detect_instance_type_from_log")
            return None
        return None

    # 監視開始の遡りで探す行（ログイン・入室・入ってきた人・出ていった人・
    # ラウンド開始・生存・所持アイテムを取り戻すための行）
    _START_SCAN_MARKS = (LogParser.USER_AUTH_MARK, LogParser.JOINING_MARK,
                         LogParser.PLAYER_JOINED_MARK, LogParser.PLAYER_LEFT_MARK,
                         LogParser.ROUND_START_MARK, LogParser.LIVED_MARK,
                         LogParser.VERIFIED_MARK, LogParser.ROUND_OVER_MARK,
                         LogParser.ITEM_EQUIP_MARK, LogParser.PAGE_COLLECTED_MARK,
                         LogParser.RESPAWN_MARK, LogParser.YOU_DIED_MARK,
                         LogParser.SUS_PLAYER_MARK)
    # 所持アイテムを取り戻すために、入室後の分を古い順に流す行
    _HELD_ITEM_REPLAY_KINDS = (LogParser.EVENT_ITEM_EQUIP, LogParser.EVENT_ROUND_START,
                               LogParser.EVENT_ROUND_OVER, LogParser.EVENT_PAGE_COLLECTED,
                               LogParser.EVENT_RESPAWN, LogParser.EVENT_YOU_DIED,
                               LogParser.EVENT_SUS_PLAYER)
    # 定期の Verified の位相を取り戻すために集める行
    _VERIFIED_LEARN_KINDS = (LogParser.EVENT_VERIFIED, LogParser.EVENT_ROUND_START,
                             LogParser.EVENT_ROUND_OVER, LogParser.EVENT_VERIFIED_END)

    def _detect_instance_from_log(self, end: Optional[int] = None):
        """監視開始の遡り。end は監視を始める位置（それより後の行は監視が読む）"""
        problem = ItemCatalog.take_load_problem()
        if problem:
            self._debug(problem)
        if not self.cfg.log_path or not self.cfg.log_path.exists():
            return
        try:
            found_user     = False
            found_instance = False
            # 最後の Joining より後の、所持アイテムに関わる行（新しい順に溜まる）
            item_events = []
            # 最後の Joining より後の入退室。逆向きに読むので新しい順に溜まる
            player_events = []
            # 最後の Joining より後（今のインスタンス）の生存と特殊ラウンド（3クラ）
            lived = 0
            round_types = []
            # 定期の Verified の位相用（新しい順に溜まる。入室をまたいでよい）
            verified_events = []
            newest = None
            lines = self._iter_log_lines_reversed(
                self.cfg.log_path,
                config.LOG_START_SCAN_CHUNK_BYTES,
                end,
            )
            for line in lines:
                if not any(mark in line for mark in self._START_SCAN_MARKS):
                    continue            # 前絞り（parse() は重い）
                event = LogParser.parse(line)
                if not event:
                    continue

                if event.kind in self._VERIFIED_LEARN_KINDS:
                    at = LogParser.log_time(line)
                    if at is not None:
                        newest = at if newest is None else newest
                        if newest - at <= config.VERIFIED_LEARN_BACK_SEC:
                            verified_events.append((at, event.kind))

                if not found_instance and event.kind in (
                        LogParser.EVENT_PLAYER_JOINED,
                        LogParser.EVENT_PLAYER_LEFT):
                    player_events.append(event)
                if not found_instance and event.kind == LogParser.EVENT_LIVED:
                    lived += 1
                if not found_instance and event.kind in self._HELD_ITEM_REPLAY_KINDS:
                    item_events.append(event)
                if not found_instance and event.kind == LogParser.EVENT_ROUND_START:
                    round_types.append(event.round_type)    # 1ラウンド1回

                if not found_user and event.kind == LogParser.EVENT_USER_AUTH:
                    self._log(f"UserID検出: {event.user_id}")
                    self.st.local_player_name = event.player_name
                    self.st.local_user_id = event.user_id
                    # 統計用の通信（最大10秒）は待たない
                    self._start_daemon(self._fetch_transformed_uid, event.user_id)
                    found_user = True

                if not found_instance and event.kind == LogParser.EVENT_JOINING:
                    found_instance = True
                    self.st.instance_id = event.instance
                    self.st.instance_type = self._parse_instance_type(event.suffix)
                    self.st.instance_access = LogParser.instance_access(event.suffix)
                    self._log(f"インスタンスタイプ検出: {self.st.instance_type}")
                    # マクロを途中で始めても人数が分かるように。積み上げないと
                    # 空＝ソロ扱いになり、他人の周回を自分の tnl で裁く
                    self._restore_players(reversed(player_events))
                    self._restore_three_wins(lived, list(reversed(round_types)))

                if found_user and found_instance:
                    break
            self._learn_verified_phase(list(reversed(verified_events)))
            if found_instance:
                # 名前（Sabotage マーダーの判定）は入室より前の行で分かるので、最後に流す
                self.st.held_item_id = self._replay_held_item(reversed(item_events))
                self._log(f"所持アイテム（入室後のログから）: {self._held_item_text()}")
        except Exception as e:
            DebugLog.exception("LogMonitor._detect_instance_from_log")
            self._log(f"検出エラー: {e}")
        if self.st.players_known:
            self._log(f"インスタンス内の人数を復元: 自分以外 "
                      f"{len(self._other_players())}人")
        else:
            self._log("インスタンス内の人数を復元できません → 他の人がいる扱い")

    def _learn_verified_phase(self, events):
        """起動時: 過去の Verified・開始・RoundOver・Verified Round End を古い順に
        トラッカーへ流して、定期の位相を取り戻す（押した時刻はログに無いので False）。
        受理のあと15秒以内に開始が無ければ定期（監視中の保険と同じ）。結果は
        デバッグログにだけ書く"""
        tracker = self._verified
        pending = None
        last = None
        for at, kind in events:
            last = at
            if pending is not None and at - pending > config.VERIFIED_ROUND_START_WAIT_SEC:
                tracker.on_begin_not_followed(pending)
                pending = None
            if kind == LogParser.EVENT_ROUND_START:
                pending = None
                tracker.on_round_start(at)
            elif kind == LogParser.EVENT_ROUND_OVER:
                tracker.on_round_over(at)
            elif kind == LogParser.EVENT_VERIFIED_END:
                tracker.on_round_end_verified(at)
            elif tracker.on_verified(at, False) == VerifiedTracker.BEGIN:
                pending = at
        if (pending is not None and last is not None
                and last - pending > config.VERIFIED_ROUND_START_WAIT_SEC):
            tracker.on_begin_not_followed(pending)
        if tracker.last_periodic is None:
            self._debug("定期 Verified の位相: 過去のログに見つかりません")
        else:
            stamp = datetime.fromtimestamp(tracker.last_periodic).strftime("%H:%M:%S")
            self._debug(f"定期 Verified の位相を復元: 最後 {stamp}")

    def _restore_three_wins(self, lived: int, round_types: list):
        """監視開始の遡り: 今のインスタンスに入ってからの生存と特殊ラウンドで、
        3クラの勝利数を決める（再起動しても取り戻す。入り直すと0に戻るので
        最後の Joining より後だけ）。

        生存1回で1勝（3で打ち止め）。Twilight 以外の特殊ラウンドが1回でもあるか、
        Twilight が2回以上なら3勝扱い（生存数は自分で稼いだ前提なので、マルチでは
        足りないことがある）
        """
        st = self.st
        target = config.OPEN_SPECIAL_ROUND_TARGET_WINS
        proofs = [t for t in round_types if t in config.SPECIAL_ROUND
                  and t not in config.OPEN_SPECIAL_ROUND_NOT_PROOF]
        twilights = [t for t in round_types if t in config.OPEN_SPECIAL_ROUND_NOT_PROOF]
        st.twilight_count = len(twilights)
        if proofs:
            st.open_special_round_wins = target
            said = f"入室後に {proofs[0]} → 3勝扱い"
        elif len(twilights) >= 2:
            st.open_special_round_wins = target
            said = f"入室後に {twilights[0]} {len(twilights)}回 → 3勝扱い"
        else:
            st.open_special_round_wins = min(lived, target)
            specials = f"{twilights[0]} 1回" if twilights else "特殊ラウンドなし"
            said = (f"入室後のログから 生存{lived}回・{specials}"
                    f" → {st.open_special_round_wins}/{target}")
        if self.cfg.cancel_afk:
            self._log(f"3クラ: {said}")

    def _fetch_transformed_uid(self, user_id):
        """統計用のIDを取りに行く（裏のスレッドで）。

        届く前にラウンドが終われば、その統計は transformed_uid = None で送られる
        （通信に失敗したときと同じ）
        """
        try:
            self.st.transformed_uid = ConnectDB.send_Users(user_id)
        except Exception as e:
            DebugLog.exception("LogMonitor._fetch_transformed_uid")
            self._log(f"transformed_uid の取得に失敗: {e}")
            return
        self._log(f"transformed_uid: {self.st.transformed_uid}")

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

    def _window_tab(self) -> dict | None:
        """この窓に対応するタブのデータ。対応づけできなければ None。

        ToN ListTool の複窓対応では、窓ごとに別のタブへ参加者が振り分けられ、
        タブごとに続行リストの中身が違う。対応はどこにも記録されていないので、
        その窓にいる人の名前と、タブの参加者の名前の重なりで決める。
        在室者か主催リストが変わったときだけ計算し直す（毎ラウンド全タブを
        走査しない）
        """
        st = self.st
        tabs = (self.host_tabs or {}).get("tabs") or {}
        if not tabs or SharedState.get_list_source() != "host":
            return None
        if not st.players_known:
            return None
        present = self._present_names()
        # ソロの窓（自分しかいない）は、参加者0人のタブと区別できない
        if not (present - {st.local_player_name}):
            return None
        key = ((self.host_tabs or {}).get("version"), frozenset(present))
        if key != self._tab_key:
            self._tab_key = key
            index = MatchTNL.tab_for_window(tabs, present)
            if index != self._tab_index:
                self._tab_index = index
                if index is None:
                    self._log("[主催リスト] 対応するタブが見つかりません"
                              "（共有リストを使います）")
                else:
                    hit = len(present & set(tabs[index].get("participants") or ()))
                    self._log(f"[主催リスト] タブ{index + 1} に対応"
                              f"（一致 {hit}/{len(present)}人）")
        if self._tab_index is None or self._tab_index is _UNSET:
            return None
        return tabs.get(self._tab_index)

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
        tab = self._window_tab()
        # 対応づいた窓は、そのタブの人の希望だけを見る（別のタブは別の窓の周回）
        source = tab["wishes"] if tab is not None else self.host_wishes
        # 中身が空でもリストを持っている人は残す（全部 OFF＝全部自爆）
        return {name: wish for name, wish in source.items()
                if name in present}

    def _keep_on(self) -> dict:
        """この窓の続行リスト"""
        tab = self._window_tab()
        if tab is not None:
            # そのタブの参加者の希望だけ（主催者自身のぶんは読み込み側で足してある）
            return tab["keepOn"]
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
        self._detect_instance_from_log(end=self.st.log_pos)
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
                        DebugLog.exception("LogMonitor._run")
                        self._log(f"読み取りエラー: {e}")
                    self._check_pending_verified()
                    try:
                        self._check_group_list_state()
                    except Exception as e:
                        DebugLog.exception("LogMonitor._run")
                        # 通知に失敗してもログ監視は続ける（通知は補助的なもの）
                        self._log(f"主催リストの確認に失敗: {e}")
                    if self._stop_event.wait(config.LOG_POLL_INTERVAL):
                        break
        except Exception as e:
            DebugLog.exception("LogMonitor._run")
            self._log(f"読み取りエラー: {e}")

    def _mark_sabotage_murder(self):
        st = self.st
        if st.sabotage_murder_this_round:
            return
        st.sabotage_murder_this_round = True
        self._mark_item_lost("Sabotageマーダー判定: アイテムロスト")
        self._lose_held_item(HELD_LOST_SABOTAGE)

    # ── 所持アイテム（st.held_item_id）。今のロスト判定（st.item_id）とは別 ──
    def held_item(self) -> tuple:
        """今持っているアイテム (id, 名前 or None)。持っていなければ (0, None)"""
        held = self.st.held_item_id
        return held, (ItemCatalog.item_name(held, config.ITEMS) if held else None)

    def _held_item_text(self) -> str:
        held = self.st.held_item_id
        if not held:
            return "なし"
        name = ItemCatalog.item_name(held, config.ITEMS)
        return f"{name} (id={held})" if name else f"id={held}（表に無い）"

    def _hold_item(self, item_id: int):
        """装備した。同じアイテムの装備が続いたときはログを出さない"""
        if item_id == self.st.held_item_id:
            return
        self.st.held_item_id = item_id
        if item_id:
            self._log(f"所持アイテム: {self._held_item_text()}")

    def _lose_held_item(self, reason: str):
        held = self.st.held_item_id
        if not held:
            return
        self.st.held_item_id = 0
        if reason != HELD_LOST_INSTANCE:
            # アイテム自動取得（CM）が取りに行く。インスタンス移動は取りに行かない
            self.st.last_lost_item_id = held
        if reason == HELD_LOST_INSTANCE:
            self._log(f"所持アイテム: なし（{reason}）")
        else:
            self._log(f"所持アイテム: なし（{ItemCatalog.label(held, config.ITEMS)} を "
                      f"{reason} でロスト）")

    @staticmethod
    def _held_item_round_start_loss(round_type: str, sabotage_murder: bool):
        """ラウンド開始でなくす理由（なくさなければ None）。8 Pages の開始ではなくさない
        （持ち込めないアイテムはページを取ったときになくなる）"""
        if sabotage_murder:
            return HELD_LOST_SABOTAGE
        if round_type in config.ROUND_START_ITEM_LOSS_ROUNDS and round_type != "8 Pages":
            return f"{round_type} 開始"
        return None

    @staticmethod
    def _page_loses_held_item(round_type: str, held: int) -> bool:
        """8 Pages でページを取った: 表で 0 のアイテムだけなくす（1・表に無いはそのまま）"""
        return (round_type == "8 Pages" and bool(held)
                and ItemCatalog.eight_pages_allowed(held, config.ITEMS) is False)

    def _replay_held_item(self, events) -> int:
        """監視開始の遡り: 入室後の行（古い順）を監視中と同じ規則で流して、最後の所持を返す"""
        held = 0
        round_type = ""
        in_round = False
        pending_murder = False
        for event in events:
            kind = event.kind
            if kind == LogParser.EVENT_ITEM_EQUIP:
                held = event.item_id
            elif kind == LogParser.EVENT_ROUND_START:
                in_round = True
                round_type = event.round_type
                murder = round_type == "Sabotage" and pending_murder
                pending_murder = False
                if self._held_item_round_start_loss(round_type, murder):
                    held = 0
            elif kind == LogParser.EVENT_ROUND_OVER:
                in_round = False
            elif kind == LogParser.EVENT_SUS_PLAYER:
                if self._same_player_name(event.player_name, self.st.local_player_name):
                    if in_round and round_type == "Sabotage":
                        held = 0
                    else:
                        pending_murder = True
            elif kind == LogParser.EVENT_YOU_DIED:
                if round_type == "Run":
                    held = 0
            elif kind == LogParser.EVENT_RESPAWN:
                if in_round:
                    held = 0
            elif kind == LogParser.EVENT_PAGE_COLLECTED:
                if self._page_loses_held_item(round_type, held):
                    held = 0
        return held

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

    def _glorbo_pending(self) -> bool:
        """Glorbo の置き換えが起こりうるのに、合図がまだ来ていない"""
        return any(row.flag == "glorbo" for row in self._pending_replacements())

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
            st.open_special_continue = False
            SharedState.continue_round_end(st)

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
            st.open_special_continue = False     # グループの WANTED は普通の続行
            self._log(f"グループ判定: {st.round_type} 【プレイ】")
            if not was_continue_round:
                if not self._hands_free():
                    PlaySound.play_sound(self.cfg.voice_continue)
                    self._log("🎙 続行アナウンス再生")
                self._focus_for_freeze("続行ラウンド")           # 数える前に見る
                SharedState.continue_round_start(st)
                if not self._hands_free():          # 放置中は録らない
                    Recorder.on_continue_start(self.window_idx)
                self._log("⏸ 続行ラウンド中 → 他窓フリーズ開始")
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
        at = LogParser.log_time(line)
        if at is not None:
            st.log_now = at         # 判定はすべてこのログの時刻で行う
            if st.fog_no_object_deadline and at >= st.fog_no_object_deadline:
                self._check_fog_no_object()     # この行を読む前に（5秒までに出た行だけで）
        event = LogParser.parse(line)
        if not event:
            return

        if event.kind == LogParser.EVENT_NETWORK_OBJECT:
            self._on_network_object(event.player_name)
            return
        self._debug_event(event)

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
            if (st.fog_reading and st.early_read_tid is None
                    and not st.early_read_void):
                # マスターの切り替えで全オブジェクトの同期が走ることがある。
                # 保留しないので、その最初の名前でその場で決めてしまわないように
                if self._may_show_fog_info():
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

        if event.kind == LogParser.EVENT_VERIFIED:
            # `Verified` はBegin受理専用のログではない。定期シグナルでも同じ行が出る
            # （後ろの行では見分けられない）。見分けは VerifiedTracker（ログの時刻と、
            # 予定と重なった1回だけツールが直前に押したか）
            now = st.log_now
            pressed = (st.last_begin_press_at > 0 and time.time() - st.last_begin_press_at
                       <= config.BEGIN_PRESS_RECENT_SEC)
            phase_before = self._verified.last_periodic
            kind = self._verified.on_verified(now, pressed)
            if kind == VerifiedTracker.PERIODIC:
                self._ignored_verified = (now, phase_before)
                self._debug_verified("無視（定期）")
                self._log("Verified を無視（定期シグナル）")
                return
            if kind == VerifiedTracker.IGNORE:
                # Begin は Verified Round End の後にしか押せない
                self._debug_verified("無視（Verified Round End より前）")
                self._log("Verified を無視（Verified Round End より前）")
                return
            off_schedule = False
            if not pressed and self._auto_begin_active():
                if phase_before is None:
                    # CO: ツールが Begin を押す窓で、ツールがまだ押していないのに来た → 定期
                    # （起動直後で定期の位相を知らないと、Verified Round End の直後の定期を受理と取り違える）
                    self._verified.mark_periodic(now)
                    self._ignored_verified = (now, phase_before)
                    self._debug_verified("無視（ツールがまだ押していない）")
                    self._log("Verified を無視（ツールがまだ押していない → 定期）")
                    return
                # CU: 位相を知っていて予定（±TOL）に重ならない。定期は300秒に1回なので Begin
                # （背面でカーソルも外でも、連打の UseRight で押せていることがある）
                off_schedule = True

            # 本物として採用。Everything recieved が続くかで事後確認する
            st.pending_verified_time = now
            st.begin_done = True
            self._debug_verified("受理（予定の外）" if off_schedule else "受理")
            self._log("✅ Connecting")
            # 速度検知そのものは Verified Round End 側で始めている（Verified は
            # 自分が Begin を押したときしか出ないので、他人がインマスだと来ない）。
            # 横移動だけはここ。自分の Begin が通った後なので、ボタンから離れても
            # Begin を押し損ねない。インマスでなければ来ないので判定も要らない
            if (st.instance_type == config.INSTANCE_PRIVATE
                    and SharedState.get_speed_detect()
                    and not self._hands_free()
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

        if event.kind == LogParser.EVENT_GLORBO:
            # Punished の Arkus の置き換え
            self._log("🫠 Glorbo 出現（ArkusのVariant）")
            self._mark_replacement("glorbo")
            return

        if event.kind == LogParser.EVENT_ATRACHED:
            # SonicのVariant
            self._log("🎮 Atrached 出現（SonicのVariant）")
            self._mark_replacement("atrached_variant")
            return

        if event.kind == LogParser.EVENT_ROUND_START:
            # 採用した Verified にラウンド開始が続いた＝Begin 由来で確定。
            # 位相は動かさない（定期ではなかったため）
            st.pending_verified_time = 0.0
            self._undo_ignored_verified()
            if st.is_continue_round:
                st.is_continue_round = False
                st.open_special_continue = False
                SharedState.continue_round_end(st)
            st.in_round                    = True
            st.round_seq                  += 1
            self._verified.on_round_start(st.log_now)
            st.round_start_time            = st.log_now   # この行の時刻（DB v1 の time）
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
            # テラーを持たないラウンドは、この行（開始）でテラー無しで送る。後から
            # Killers の行（ムーンの 0 0 0 など）が来ても送り直さない（statistics_sent）
            if ConnectDB.round_type_id(event.round_type) in config.NULL_TERROR_ROUND_IDS:
                self._send_null_terror_round()
            st.fog_reading                 = False
            st.early_read_hits             = {}
            st.early_read_tid              = None
            st.early_read_void             = False
            st.fog_object_seen             = False
            st.fog_no_object_deadline      = 0.0
            st.fog_no_object_dtm           = False
            st.eight_pages_unknown_logged  = False
            st.fog                         = False
            st.begin_done                  = False
            st.speed_round_kind            = ""
            st.speed_probe_done            = False
            st.speed_strafe_done           = False
            st.glorbo_afk                  = False
            st.begin_move_done             = False
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
            if not self._auto_begin_active() and (st.waiting_for_equip or st.equip_freeze_held):
                # ツールが Begin を押さない窓: 装備待ちのまま・装備後の猶予の間でもすぐ外す
                held = st.equip_freeze_held
                st.waiting_for_equip = False
                SharedState.equip_freeze_end(st)
                if held:
                    self._log("ラウンド開始 → 装備待ちフリーズ解除")
            elif st.waiting_for_equip:
                st.waiting_for_equip = False
                SharedState.equip_freeze_end(st)
                self._log("一時的にアイテムロストフリーズを解除")

            if st.sabotage_murder_this_round:
                self._mark_item_lost("Sabotageマーダー開始: アイテムロスト")
            elif self._round_start_loses_item():
                self._mark_item_lost(f"{st.round_type}: ラウンド開始時にアイテムロスト")
            held_reason = self._held_item_round_start_loss(st.round_type,
                                                           st.sabotage_murder_this_round)
            if held_reason:
                self._lose_held_item(held_reason)

            # 指定ラウンドに突入したら全窓を止める。テラー判明は待たない。
            # 自窓の自爆は止めない（止めるのは他窓だけ）。放置モード中はFogに揃えて張らない。
            if st.round_type in SharedState.get_freeze_rounds() and not self._hands_free():
                self._focus_for_freeze("ラウンド突入フリーズ")   # 数える前に見る
                SharedState.round_freeze_start(st)
                self._log(f"⏸ {st.round_type} 突入 → 全窓フリーズ"
                          f"（死亡{config.FOG_FREEZE_RELEASE_DELAY_SEC}秒後に解除）")
                # 対象は依頼の5つだけ。Punished / 8 Pages の音声は速度検知用
                voice = self._round_entry_voice(st.round_type)
                if voice:
                    PlaySound.play_sound(voice)

            if st.round_type == "Run":
                st.is_continue_round = False
                st.open_special_continue = False
                self._log(f"Round: {st.round_type} 【死亡待ち・アイテム購入予定】")
                return

            if st.round_type == "Fog":
                # 既定では他窓を止めない。止めたいなら突入フリーズで Fog を選ぶ
                # （上の一般の経路で張られる）。
                # is_continue_round と continue_round_start(st) は必ずセットで外す。
                # 片方だけ残すと、判明時に continue_round_end(st) が自分の足して
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
            # 特殊ラウンドを経験したら3勝扱い。3クラ前にも出る Twilight は1回目は
            # 数えず、2回目で（過去のログの1回と監視中の1回も2回目）
            proof = None
            if st.round_type in config.OPEN_SPECIAL_ROUND_NOT_PROOF:
                if st.twilight_round_seq != st.round_seq:
                    st.twilight_round_seq = st.round_seq
                    st.twilight_count += 1
                    if st.twilight_count >= 2:
                        proof = f"{st.round_type} 2回目"
            elif st.round_type in config.SPECIAL_ROUND:
                proof = st.round_type
            if proof is not None:
                if (st.open_special_round_wins < config.OPEN_SPECIAL_ROUND_TARGET_WINS
                        and self.cfg.cancel_afk):
                    self._log(f"特殊ラウンド（{proof}）を経験したので3勝扱い"
                              " → 以降のDTM/Waldoはスキップします")
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
            # 続行ラウンドのフリーズは死亡から少し置いて解除する。
            # 猶予中に手動で視点調整などを挟めるようにするため。
            if st.is_continue_round:
                self._start_daemon(self._release_continue_freeze_after_delay,
                                   st.round_seq)
            if st.round_freeze_held:
                self._start_daemon(self._release_round_freeze_after_delay,
                                   st.round_seq)
            if st.round_type == "Run":
                self._mark_item_lost("Run死亡: アイテムロスト")
                self._lose_held_item(HELD_LOST_RUN_DEATH)
            return

        if event.kind == LogParser.EVENT_RESPAWN:
            if st.in_round:
                self._mark_item_lost("リスポーン: アイテムロスト")
                self._lose_held_item(HELD_LOST_RESPAWN)
            return

        if event.kind == LogParser.EVENT_PAGE_COLLECTED:
            if self._page_loses_held_item(st.round_type, st.held_item_id):
                self._lose_held_item(HELD_LOST_PAGE)
            return

        if event.kind == LogParser.EVENT_ROUND_OVER:
            st.in_round = False
            st.begin_move_done = False      # 次の Begin 前の移動はこれから
            self._verified.on_round_over(st.log_now)
            if self._action.chase_stop():
                self._log("チェイス停止（ラウンド終了）")
            st.fog_reading = False          # 公開前に終わった霧は答え合わせできない
            st.early_read_hits = {}
            st.fog_no_object_deadline = 0.0
            # 録画は RoundOver から少し後で止める（続行中でなければ何もしない）
            Recorder.on_round_over(self.window_idx)
            # Begin待ちの起点。実処理は Verified Round End 側で走るが、
            # 待ち時間はこの時刻から数える（RoundOver→Round End は実測約13秒）。
            st.round_over_time = time.time()
            # 続行フリーズの解除を予約する。死亡側だけだと、生き残ったときに
            # 予約が入らず Verified Round End の保険まで残り、他窓が
            # RoundOver から13〜14秒も余計に止まっていた。RoundOver は
            # 死亡・生存のどちらでも来るので、ここなら取りこぼさない。
            # 死亡は RoundOver より先に来るので、死亡から数える動きは保たれる
            # （解除は冪等なので二重予約は無害）
            if st.continue_freeze_held:
                self._start_daemon(self._release_continue_freeze_after_delay,
                                   st.round_seq)
            if self._waiting_for_terror_replacement():
                self._send_round_statistics_once()
            announce_on_round_over = not self._auto_begin_active()
            if announce_on_round_over and not self._hands_free():
                round_lost_item = self._round_lost_item()
                if self._round_item_warning():
                    if round_lost_item:
                        st.item_id = 0
                    st.waiting_for_equip = True
                elif not st.item_id:
                    st.waiting_for_equip = True
            if st.waiting_for_equip and announce_on_round_over and not self._hands_free():
                # ツールが Begin を押さない窓（グループ・public・自動Begin OFF）でも装備待ちで
                # 全窓を止める（前面化・音声1回）。解除は装備（猶予の後）かラウンド開始の早い方
                self._action._attend_to_item_loss()
                self._log("RoundOver 【⚠ アイテムロスト → 全窓フリーズ（装備かラウンド開始で解除）】")
            if (self._item_begin_mode_active()
                    and self._round_item_warning() and not self._hands_free()):
                # アイテム取得→Begin モード。RoundOver の時点でもう分かっている
                # （ロストは死亡などラウンド中に立つ）ので、ここで済ませる
                if self._round_lost_item():
                    st.item_id = 0
                st.waiting_for_equip = True
                # このモードでは自動取得を動かさない（item_fetch_target が None。CY）
                self._action._attend_to_item_loss()
                self._log("RoundOver 【⚠ アイテムロスト → 全窓フリーズ開始】")
            # Begin移動はここを起点に待つ。クリックとアイテムロスト通知は
            # Verified Round End を待ってから行う（RoundOver時点だと
            # 続行ラウンド中の可能性があり、音声が邪魔になるため）。
            if self.cfg.auto_begin:
                self._start_daemon(self._action.do_after_round)
            return

        if event.kind == LogParser.EVENT_VERIFIED_END:
            self._verified.on_round_end_verified(st.log_now)
            # 選出者のクリアはここ。ROUND_START でやると、同じ秒に先に積まれた
            # Sus player を消してしまう（ログ上は Sus player の方が前に来る）
            st.sus_players = []
            if self._waiting_for_terror_replacement():
                self._send_round_statistics_once()
            if st.is_continue_round:
                # 通常は RoundOver で解除済み。ここは取りこぼしの保険。
                st.is_continue_round = False
                st.open_special_continue = False
                SharedState.continue_round_end(st)
                self._log("続行ラウンド終了 → 他窓フリーズ解除（保険）")
            round_lost_item = self._round_lost_item()
            round_item_warning = self._round_item_warning()
            if not self._hands_free():
                if round_item_warning:
                    if round_lost_item:
                        st.item_id = 0
                    if not self.cfg.auto_begin and not st.waiting_for_equip:
                        self._log("ラウンド終了 【⚠ アイテムロスト → RoundOver時に通知予定】")
                    elif self._item_begin_mode_active():
                        # アイテム取得→Begin モードの前面化・フリーズ・音声は
                        # RoundOver の _attend_to_item_loss() で済ませている。
                        # ここでは何も出さない（ログだけ）
                        st.waiting_for_equip = True
                        self._log("ラウンド終了 【⚠ アイテムロスト → 全窓フリーズ中】")
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
            # 看破できる起動なら、5秒たっても objects の名前が出なければ DTM（ログの時刻で見る）
            if st.fog_reading and config.FOG_NO_OBJECT_DTM_ENABLED and st.log_now:
                st.fog_no_object_deadline = st.log_now + config.FOG_NO_OBJECT_DTM_SEC
            self._log("テラー不明 → revealed待ち")
            if self._hands_free():
                if st.round_type == "8 Pages":
                    # 8 Pages はテラーが出るまで自爆できない（debug.log: 開始時の3回は
                    # 1度も死なず、テラーが出た後はほぼ成功）。出た後の判定で自爆する
                    self._log(f"開始: {st.round_type} 【放置モード→テラーが出てから自爆】")
                else:
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
            st.fog_no_object_deadline = 0.0
            self._on_killers(event.terror_ids or [], event.round_type, revealed=True)
            # 答え合わせは公開の後（食い違いの警告はもう出してよい）
            self._check_early_read(event.terror_ids or [], event.round_type)
            self._check_no_object_dtm(event.terror_ids or [], event.round_type)
            return

        if event.kind == LogParser.EVENT_JOINING:
            # インスタンスを移動するとアイテムは消える（依頼者）
            self._lose_held_item(HELD_LOST_INSTANCE)
            st.last_lost_item_id = 0                # 前のインスタンスでロストした分は取りに行かない（CS）
            if st.equip_freeze_held and not self._auto_begin_active():
                # 移った先ではラウンド開始がすぐ来るとは限らない。全窓を止め続けないよう外す
                st.waiting_for_equip = False
                SharedState.equip_freeze_end(st)
                self._log("インスタンス移動 → 装備待ちフリーズ解除")
            st.instance_id = event.instance
            st.instance_type = self._parse_instance_type(event.suffix)
            st.instance_access = LogParser.instance_access(event.suffix)
            # 別インスタンスに入った。ラウンドの並びもmoonの消化状況も分からない
            self.sequence.reset()
            # 3クラはインスタンスに入り直すと0に戻る
            if st.open_special_round_wins and self.cfg.cancel_afk:
                self._log("3クラ: 別のインスタンスに入ったので 0/"
                          f"{config.OPEN_SPECIAL_ROUND_TARGET_WINS} に戻します")
            st.open_special_round_wins = 0
            st.twilight_count = 0
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
            st.equip_seen_id = event.item_id        # アイテム自動取得が Equip の結果を待つ
            st.equip_seen_seq += 1
            if event.item_id:
                st.last_lost_item_id = 0            # 装備した（手で別のアイテムでも）→ もう取りに行かない
            st.item_id = event.item_id
            self._hold_item(event.item_id)
            if st.speed_freeze_kind == "8pages":
                # 8 Pages はスキャナーを取れたら再開してよい。ただし即座に
                # 解除すると間が短すぎる（依頼者の指摘）。アイテムロスト側の
                # 装備解除と同じ猶予を置く。種別はその場で消す——2回
                # Equipping が来ても予約を二重にしないため
                st.speed_freeze_kind = ""
                self._start_daemon(self._release_speed_freeze_after_delay,
                                   st.round_seq)
            if st.died_this_round and st.item_id:
                st.item_equipped_after_death = True
            self._log(f"✅ アイテム装備 (id={st.item_id})")
            if (not self._auto_begin_active() and st.equip_freeze_held and st.item_id):
                # ツールが Begin を押さない窓: Begin の受理は来ないことがあるので待たない
                st.waiting_for_equip = False
                self._start_daemon(self._release_equip_freeze_after_equip)
            # 両条件（装備＋Begin）が揃ったら遅延フリーズ解除
            elif st.waiting_for_equip and st.begin_done:
                st.waiting_for_equip = False
                self._start_daemon(self._release_equip_wait_after_delay)
            return

        if event.kind == LogParser.EVENT_LIVED:
            st.lived_this_round = True
            # どのラウンドでも生き残ったら1勝（3で打ち止め）
            if st.open_special_round_wins < config.OPEN_SPECIAL_ROUND_TARGET_WINS:
                st.open_special_round_wins += 1
                if self.cfg.cancel_afk:
                    self._log(f"生存数: {st.open_special_round_wins}/"
                              f"{config.OPEN_SPECIAL_ROUND_TARGET_WINS}")
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

    def _eight_pages_ids(self, ids: list[int]) -> list[int]:
        """8 Pages のログ番号を続行リストのIDへ直す。

        `Killers have been set - A B 0` の A だけでテラーが決まる（B は別物）。
        A は 8 Pages 専用の番号なので、terrors.json の 8pages の `list_id` で
        橋渡しする。未登録なら空にする——分からないIDのまま続行判定・3クラ解放・
        統計に流すと、無関係なテラーとして扱われる
        """
        page = ids[0] if ids else None
        if page is None:
            return []
        tid = ReadJson.eight_pages_list_id(page, config.TERRORS)
        if tid is None:
            if not self.st.eight_pages_unknown_logged:
                self.st.eight_pages_unknown_logged = True
                self._log(f"8 Pages: 未登録の番号 {page}"
                          "（terrors.json の 8pages に list_id を足してください）")
            return []
        return [tid]

    def _on_killers(self, ids: list[int], round_type: str, revealed: bool):
        st = self.st
        st.fog = False

        ids = RoundDecision.normalize_killer_ids(ids, round_type, st.round_type)
        if round_type == "8 Pages":
            ids = self._eight_pages_ids(ids)
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
        # 放置モードは待たずに即自爆する。ただし Glorbo の合図を待つ間は自爆しない
        # （続行リストに Glorbo の続行があれば、放置モードでも続行するため）
        if ((not self._hands_free() or self._glorbo_pending())
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
        # Glorbo（Punished の Arkus の置き換え）は、続行リストに Glorbo の続行があれば
        # 放置モードの即自爆・ラウンド指定の自爆より優先して続行する（依頼者の決定）
        if (is_private and config.GLORBO_ID in ids
                and self._list_plan(ids, bloodthirsty)[1]):
            return ("glorbo",)
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
        # プラベ系の Sabotage は焼き芋と同じ判定にする（依頼者の決定）。ここへ
        # 置くのは、「自爆する」の指定と放置モードの即自爆を先に効かせるため
        if is_private and st.round_type == "Sabotage":
            decision = self._group_decision(round_type, ids)
            if decision != GroupRound.NORMAL:
                return ("group", decision)
        return self._list_plan(ids, bloodthirsty)

    def _list_plan(self, ids, bloodthirsty) -> tuple:
        """続行リストでの判定"""
        st = self.st
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
        if kind == "glorbo":
            return "continue"
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
                st.open_special_continue = False
                SharedState.continue_round_end(st)
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
        if kind == "glorbo":
            self._start_glorbo_continue(round_type)
            return
        self._decide_with_keep_on_set(round_type, plan)

    def _start_glorbo_continue(self, round_type: str):
        """Glorbo の続行: 普通の続行と同じくフリーズ・アナウンス・録画（放置モードでは
        アナウンスと録画なし）。AFK 対策の移動は 3 勝・cancel_afk に関係なく回す"""
        st = self.st
        was_continue_round = st.is_continue_round
        st.is_continue_round = True
        st.open_special_continue = False         # 音量は普通の続行
        self._log(f"判定: Glorbo / {round_type} 【プレイ(Glorbo)】")
        if not was_continue_round:
            if not self._hands_free():
                PlaySound.play_sound(self.cfg.voice_continue)
                self._log("🎙 続行アナウンス再生")
            self._focus_for_freeze("続行ラウンド")           # 数える前に見る
            SharedState.continue_round_start(st)
            if not self._hands_free():          # 放置中は録らない
                Recorder.on_continue_start(self.window_idx)
            self._log("⏸ 続行ラウンド中 → 他窓フリーズ開始")
        if not st.glorbo_afk:
            st.glorbo_afk = True
            self._log("AFK解除ループ開始（Glorbo）")
            self._start_daemon(self._action.do_open_special_round_loop, True)

    # ── 通常判定（続行リスト照合） ──────────────
    def _decide_with_keep_on_set(self, round_type: str, plan: tuple | None = None):
        st = self.st
        is_private = st.instance_type == config.INSTANCE_PRIVATE
        is_group = st.instance_type in GroupRound.GROUP_INSTANCES

        all_ids = st.terror_ids
        was_continue_round = st.is_continue_round
        if plan is None:        # 単体で呼ばれたとき（テスト・取りこぼしの保険）
            plan = self._list_plan(all_ids, st.bloodthirsty_creature_variant)
        # `_plan()` が決めたものをそのまま実行する。ここで判定し直すと、
        # 待つ判断（_plan）と本番が食い違う
        _kind, is_continue, is_open_special_round_target = plan
        st.is_continue_round = is_continue
        # DTM/Waldo による続行か（音量では通常扱い。WindowVolume.category_of）
        st.open_special_continue = bool(is_continue and is_open_special_round_target)

        if was_continue_round and not st.is_continue_round:
            SharedState.continue_round_end(st)

        tag  = "【プレイ(DTM/Waldo)】" if is_open_special_round_target else (
               "【プレイ】" if st.is_continue_round else "【スキップ】")
        self._log(f"判定: {format_terror_ids(all_ids)} / {round_type} {tag}")

        if st.is_continue_round:
            if not is_open_special_round_target and not was_continue_round:
                if not self._hands_free():
                    PlaySound.play_sound(self.cfg.voice_continue)
                    self._log("🎙 続行アナウンス再生")
                self._focus_for_freeze("続行ラウンド")           # 数える前に見る
                SharedState.continue_round_start(st)
                # 録画も同じ瞬間に始める。霧の前倒し判明（Enrage など）も
                # ここへ流れ込むので、判明の経路ごとには足さない。放置中は録らない
                if not self._hands_free():
                    Recorder.on_continue_start(self.window_idx)
                self._log("⏸ 続行ラウンド中 → 他窓フリーズ開始")
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
        ConnectDB.register_round(
            self._round_type_for_db(st.terror_ids),
            list(st.terror_ids),
            st.map_id,
            st.transformed_uid,
            quiet=st.statistics_quiet,
            instance_key=self._db_instance_key(),
            round_time=st.round_start_time,
        )

    def _send_null_terror_round(self):
        """テラーを持たないラウンド（config.NULL_TERROR_ROUND_IDS）を、テラー無しで1回だけ送る"""
        st = self.st
        if st.statistics_sent:
            return
        st.statistics_sent = True
        ConnectDB.register_round(
            st.round_type,
            [],
            st.map_id,
            st.transformed_uid,
            instance_key=self._db_instance_key(),
            round_time=st.round_start_time,
        )

    def _round_type_for_db(self, terror_ids) -> str:
        """DB へ送るラウンド名。Fog / Ghost で、送るテラーが全部オルタネイトなら
        「Fog (Alternate)」「Ghost (Alternate)」にする（開始の行は常に Fog で、
        (Alternate) は判明の行にしか出ない。看破・Enrage で先に分かったときも正しく送る）。
        st.round_type は変えない（判定・ラウンド指定自爆が見ている）"""
        round_type = self.st.round_type
        ids = list(terror_ids or [])
        if (round_type in ("Fog", "Ghost") and ids
                and all(ReadJson.is_alternate_terror(tid, config.TERRORS) for tid in ids)):
            return f"{round_type} (Alternate)"
        return round_type

    def _db_instance_key(self):
        """DB v1 でまとめる目印。ソロ（自分以外がいないと分かっている）なら None。
        人数が分からないときは送る"""
        st = self.st
        if st.players_known and not self._other_players():
            return None
        return ConnectDB.instance_key(st.instance_id)

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
        ConnectDB.register_round(
            self._round_type_for_db([tid]), [tid], st.map_id, st.transformed_uid, quiet=True,
            instance_key=self._db_instance_key(), round_time=st.round_start_time)

    def _on_network_object(self, name: str):
        """看破: 霧の間に [NetworkProcessing] に出たオブジェクト名を照合する。

        最初に当たった名前で、その場で看破する（保留しない。理由と残るリスクは
        FogEarlyRead の冒頭）。その後に別のテラーの名前が見えたら void にし、
        そのラウンドの看破はそれ以上使わない（決めた判定は取り消さない）
        """
        st = self.st
        if not st.fog_reading:
            return
        key = ReadJson.normalize_object_name(name)
        if not key or key in st.early_read_hits:
            return
        tid = ReadJson.fog_terror_id_by_object_name(name, config.TERRORS)
        if tid is not None:
            st.fog_object_seen = True       # 信用・void に関係なく、テラーの名前が見えた
        if tid is None or not FogEarlyRead.trust.usable(key):
            return
        st.early_read_hits[key] = (tid, name)
        if len({t for t, _n in st.early_read_hits.values()}) > 1:
            # 取り違えを避けるため、このラウンドの看破は使わない
            if not st.early_read_void and self._may_show_fog_info():
                self._log("看破: 別々のテラー名が見えました → このラウンドの看破は使いません")
            st.early_read_void = True
            return
        if st.early_read_tid is not None or st.early_read_void:
            return          # もう決めた・void（二重に判定しない）
        if not self._fog_terror_unknown():
            return          # Enrage 系で先に決まった（二重に判定しない）
        st.early_read_tid = tid
        self._identify_fog_terror(tid, "看破", name, early_read=True)

    def _check_fog_no_object(self):
        """看破できる起動の霧で、Killers is unknown から5秒たっても objects の名前が
        1つも当たらなかった → DTM と判断する（1回だけ）。看破と同じ入口を通すので、
        許可が無ければ DB に黙って送るだけ"""
        st = self.st
        st.fog_no_object_deadline = 0.0
        if not (config.FOG_NO_OBJECT_DTM_ENABLED and st.fog_reading
                and not st.fog_object_seen and st.early_read_tid is None
                and not st.early_read_void and self._fog_terror_unknown()):
            return
        st.fog_no_object_dtm = True
        st.early_read_tid = DTM_TERROR_ID       # 後から名前が見えても二重に判定しない
        self._identify_fog_terror(DTM_TERROR_ID, "看破（オブジェクトなし）", "",
                                  early_read=True)

    def _check_no_object_dtm(self, ids: list[int], round_type: str):
        """公開の後: オブジェクトなし → DTM が外れていたら知らせる"""
        st = self.st
        if not st.fog_no_object_dtm or not ids:
            return
        st.fog_no_object_dtm = False
        public = RoundDecision.normalize_killer_ids(list(ids)[:1], round_type)[0]
        if public == DTM_TERROR_ID:
            return
        message = f"看破（オブジェクトなし）が外れました（公開: {format_terror_ids([public])}）"
        if self._may_show_fog_info():
            self._log(f"⚠ {message}")
        self._debug(message)

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
