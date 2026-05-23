import time
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config
import SharedState
import WindowOperator
import PlaySound
import ConnectDB
import MatchTNL


# ═══════════════════════════════════════════════
#  ログ正規表現
# ═══════════════════════════════════════════════
RE_ROUND_START      = re.compile(r"This round is taking place at (.+) and the round type is (.+)") # ラウンド突入(180s段階)
RE_MAP_ID           = re.compile(r"\((\d+)\)$")   # マップ名からID抽出
RE_KILLERS_SET      = re.compile(r"Killers have been set - (\d+) (\d+) (\d+) // Round type is (.+)") # テラー判明ログ
RE_KILLERS_UNKNOWN  = re.compile(r"Killers is unknown - \?\?\? // .+ // Round type is (.+)") # 霧ラウンド時に最初のログ
RE_KILLERS_REVEALED = re.compile(r"Killers have been revealed - (\d+) (\d+) (\d+) // Round type is (.+)") # 霧ラウンド時のテラー判明時のログ
RE_FOXY             = re.compile(r"foxy the pirate turned evil!", re.IGNORECASE)  # Foxyの出現ログ
RE_LIVED            = re.compile(r"^Lived in round[.]$") # 生存
RE_YOU_DIED         = re.compile(r"^You died[.]$")  # 死亡
RE_ROUND_OVER       = re.compile(r"^RoundOver$") # ラウンド終了
RE_VERIFIED_END     = re.compile(r"^Verified Round End$") # intermission突入
RE_BEGIN_DONE       = re.compile(r"^Verified$") # connecting突入
RE_ITEM_EQUIP       = re.compile(r"^Equipping (\d+)[.]")   # アイテム装備検出


RE_USER_AUTH        = re.compile(r"User Authenticated: \S+ \((usr_[0-9a-f-]+)\)")
RE_LOG_PREFIX       = re.compile(r"^\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+\s+-\s+")
RE_JOINING          = re.compile(r"\[Behaviour\] Joining (wrld_[^:]+):\d+(.*?)(?:~region\(|$)") # インスタンスタイプを取得するためのJoinログ


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


# ═══════════════════════════════════════════════
#  窓ごとの設定
# ═══════════════════════════════════════════════
@dataclass
class WindowConfig:
    hwnd: int = 0
    log_path: Path = None
    active: bool = True
    auto_begin: bool = True
    do_skip: bool = True
    cancel_afk: bool = True      # DTM/Waldo続行（3クラまで）
    hoshiimo_skip: bool = False  # 干し芋自動自爆
    voice_intermission: str = "" # Intermission音声
    announce_intermission: bool = False  # Intermissionアナウンス
    voice_continue: str = ""   # 続行ラウンド音声ファイルパス
    voice_fog: str = ""        # 霧ラウンド音声ファイルパス
    voice_item_lost: str = ""  # アイテムロスト音声ファイルパス
    voice_foxy: str = ""  # Foxy出現音声ファイルパス
    

# ═══════════════════════════════════════════════
#  1窓の実行状態
# ═══════════════════════════════════════════════
@dataclass
class WindowState:
    log_pos: int = 0
    # Round情報
    in_round: bool = False
    round_type: str = ""
    terror_ids: list = field(default_factory=list)
    map_id: int = 0                    # マップID（括弧内の数字）
    transformed_uid: str = ""
    # 自爆関係
    fog: bool = False
    is_continue_round: bool = False  # 続行/霧ラウンド中（他窓フリーズ中）
    _skip_time: float = 0.0        # 自爆実行時刻（RoundOver判定用）
    begin_done: bool = False   # Beginが正常に押されたか(Connecting)
    # 3クラ開け
    is_OpenSpecialRound_round: bool = False     # 現在のラウンドが特殊ラウンドを開けるラウンドかどうか
    OpenSpecialRound_wins: int = 0              # 窓ごとの勝利数
    # アイテム関係
    item_id: int = 1             # 何のアイテムを所持しているか(未所持は0となる)
    waiting_for_equip: bool = False  # アイテム装備待ち（操作権限を譲渡中）


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
                line = RE_LOG_PREFIX.sub("", line).strip()

                if not found_user:
                    m = RE_USER_AUTH.search(line)
                    if m:
                        VRChat_uid = m.group(1)
                        self._log(f"UserID検出: {VRChat_uid}")
                        threading.Thread(target=ConnectDB.send_Users(VRChat_uid), args=(VRChat_uid,), daemon=True).start()
                        found_user = True

                if not found_instance:
                    m = RE_JOINING.search(line)
                    if m:
                        suffix = m.group(2)
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
                        line = RE_LOG_PREFIX.sub("", line).strip()
                        self._process(line)
            except Exception as e:
                self._log(f"読み取りエラー: {e}")
            time.sleep(config.LOG_POLL_INTERVAL)

    # ── ログ行処理 ────────────────────────────
    def _process(self, line: str):
        st = self.st

        # アイテム装備検出（操作権限譲渡中の再開トリガー）
        m = RE_ITEM_EQUIP.match(line)
        if m:
            st.item_id = int(m.group(1))
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

        m = RE_ROUND_START.match(line)
        if m:
            st.in_round        = True
            st.round_type      = m.group(2).strip()
            st.terror_ids      = []
            st.map_id          = int(RE_MAP_ID.search(m.group(1).strip()).group(1))
            st.fog             = False
            st.begin_done      = False
            st.is_OpenSpecialRound_round   = False
            # アイテムロスト中にラウンドが始まったらフリーズ解除
            # （has_item=Falseのまま → 次のVerified Round Endで再フリーズ）
            if st.waiting_for_equip:
                st.waiting_for_equip = False
                _EQUIP_WAIT_EVENT.set()
                self._log("一時的にアイテムロストフリーズを解除")

            if st.round_type in "Run":
                # Runは死亡してアイテムロスト対応へ
                st.is_continue_round = False
                self._log(f"Round: {st.round_type} 【死亡待ち・アイテム購入予定】")
                return

            if st.round_type in "Fog":
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

        m = RE_KILLERS_SET.match(line)
        if m:
            # 特殊ラウンドを経験したら3勝扱い（OpenSpecialRound_completed=True）
            if st.round_type in config.SPECIAL_ROUND:
                st.OpenSpecialRound_wins = config.OpenSpecialRound_TARGET_WINS
            if not (st.round_type == "Alternate" and m.group(4).strip() == "Classic"): # AF期間中は極まれに偽Classicがある
                st.round_type = m.group(4).strip()
            self._on_killers(
                MatchTNL.parse_terror_ids(m.group(1), m.group(2), m.group(3), st.round_type)
                , st.round_type, revealed=False
            )
            return

        # Fogラウンド突入
        if RE_KILLERS_UNKNOWN.match(line):
            st.fog  = True
            st.round_type = "fog"
            self._log(f"テラー不明 → revealed待ち")
            if get_hands_free():
                self._log(f"開始: {st.round_type} 【放置モード→即自爆】")
                threading.Thread(target=self._do_skip, daemon=True).start()
            return

        if RE_FOXY.search(line):
            # 「foxy the pirate turned evil!」→ Alternate ID2（+134=136）確定
            self._log("🦊 Foxyが出た！")
            PlaySound.play_sound(self.cfg.voice_foxy)
            
            # もし霧なら自爆するか判定する(他はRE_KILLERS_SETから行う)
            if st.round_type == "fog":
                self._on_killers([2], st.round_type, revealed=True)
            return

        m = RE_KILLERS_REVEALED.match(line)
        if m:
            self._on_killers(
                MatchTNL.parse_terror_ids(m.group(1), m.group(2), m.group(3), m.group(4).strip()),
                m.group(4).strip(), revealed=True)
            return

        m = RE_JOINING.search(line)
        if m:
            suffix = m.group(2)
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

        if RE_ROUND_OVER.match(line):
            st.in_round = False
            # アイテムロスト音声: auto_beginなしの場合はRoundOverで流す
            if st.waiting_for_equip and not self.cfg.auto_begin:
                PlaySound.play_sound(self.cfg.voice_item_lost)
            return

        if RE_LIVED.match(line):
            if st.is_OpenSpecialRound_round:
                st.OpenSpecialRound_wins += 1
                self._log(f"生存数: {st.OpenSpecialRound_wins}/{config.OpenSpecialRound_TARGET_WINS}")
                if st.OpenSpecialRound_wins >= config.OpenSpecialRound_TARGET_WINS:
                    self._log("🎉 3勝達成！以降のDTM/Waldoラウンドはスキップします")
            st.is_OpenSpecialRound_round = False
            return

        if RE_YOU_DIED.match(line):
            if st._skip_time > 0 and (time.time() - st._skip_time) <= 3.0:
                self._log("✅ 自爆成功")
                st._skip_time = 0.0
            st.is_OpenSpecialRound_round = False
            return

        if RE_BEGIN_DONE.match(line):
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

        if RE_VERIFIED_END.match(line):
            # 続行/霧ラウンドのフリーズ解除
            if st.is_continue_round:
                st.is_continue_round = False
                _continue_round_end()
                self._log("▶ 続行/霧ラウンド終了 → 他窓フリーズ解除")
            # アイテムロスト判定（放置モード中はフリーズしない）
            if not get_hands_free():
                if st.round_type in config.ITEM_LOSS_ROUNDS:
                    st.item_id = 0
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
                    self._log("ラウンド終了 【⚠ アイテム未回収 → Begin時に再フリーズ】")
                else:
                    self._log("ラウンド終了")
            else:
                if st.round_type in config.ITEM_LOSS_ROUNDS:
                    st.item_id = 0
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
        
        # ユーザーIDを取得
        m = RE_USER_AUTH.search(line)
        if m:
            VRChat_uid = m.group(1)
            self._log(f"UserID検出: {VRChat_uid}")
            threading.Thread(target=lambda: ConnectDB.send_Users(VRChat_uid), daemon=True).start()
            return

    # ── テラー確定処理 ────────────────────────
    def _on_killers(self, ids: list[int], round_type: str, revealed: bool):
        st = self.st
        st.fog = False

        # Alternate枠のオフセット補正（round_type で判定）
        ids = MatchTNL.apply_alternate_offset(ids, round_type)

        # Unboundラウンドのオフセット補正: ログID + 200 = tnlID
        if st.round_type == "Unbound":
            ids = [tid + MatchTNL.UNBOUND_OFFSET for tid in ids]

        # テラーIDを累積（複数回Killers行が来るラウンド対応）
        for tid in ids:
            if tid not in st.terror_ids:
                st.terror_ids.append(tid)
        # インスタンス制限チェック
        itype = SharedState.get_instance_type()
        is_allowed = itype in config.INSTANCE_PRIVATE
        is_hoshiimo = itype == config.INSTANCE_HOSHIIMO

        # 干し芋グループ専用自動自爆
        if is_hoshiimo and self.cfg.hoshiimo_skip:
            if st.round_type in config.HOSHIIMO_SKIP_ROUNDS:
                self._log(f"干し芋自動自爆: {st.round_type}")
                if not st.is_continue_round:
                    threading.Thread(target=self._do_skip, daemon=True).start()
                return
            else:
                # 干し芋グループだがスキップ対象外→何もしない
                return

        # 通常機能はフレ/フレ+/招待/招待+のみ
        # ただし完全放置モードはインスタンス問わず動作
        if not is_allowed:
            self._log(f"インスタンス制限: 操作スキップ ({itype})")
            return

        # 放置モードの処理
        if get_hands_free():
            # 特殊ラウンド経験済み → 全ラウンド即自爆
            if st.OpenSpecialRound_wins >= config.OpenSpecialRound_TARGET_WINS:
                self._log(f"放置モード(3クラ済み): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    threading.Thread(target=self._do_skip, daemon=True).start()
                return
            # アイテムなし → DTMのみ続行、Waldo含むそれ以外は自爆
            if not st.item_id:
                # DTM(50)はアイテム不要なので続行可、Waldo(131)はアイテム必要なので自爆
                DTM_ONLY_IDS = {50}
                has_dtm = bool(
                    self.cfg.cancel_afk and
                    any(t in DTM_ONLY_IDS for t in st.terror_ids)
                )
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

        # 累積テラーIDで判定（複数体ラウンド対応）
        all_ids = st.terror_ids  # すでに累積済み
        is_special_round = st.round_type in config.SPECIAL_ROUND

        is_OpenSpecialRound_target = (
            bool(all_ids and config.OpenSpecialRound_TERROR_IDS and
                 any(t in config.OpenSpecialRound_TERROR_IDS for t in all_ids))
            and not is_special_round
            and st.OpenSpecialRound_wins < config.OpenSpecialRound_TARGET_WINS
            and self.cfg.cancel_afk   # 窓ごとの設定
        )

        # 続行判定: 1体でも続行希望があれば続行
        # 特殊ラウンド中・3勝後のDTM/WaldoはtnlのみでkeepOn判定
        st.is_continue_round = MatchTNL.should_continue(self.keepOn_set, MatchTNL.LOG_TO_TNL.get(round_type, round_type), all_ids) or is_OpenSpecialRound_target

        verb = "revealed" if revealed else "set"
        if is_OpenSpecialRound_target:
            tag = "【プレイ(DTM/Waldo)】"
        else:
            tag = "【プレイ】" if st.is_continue_round else "【スキップ】"
        self._log(f"テラー{verb}: {all_ids} / {round_type} {tag}")

        if st.is_continue_round:
            # 続行ラウンドの音声アナウンス＋他窓フリーズ（DTM/Waldo以外）
            if not is_OpenSpecialRound_target:
                PlaySound.play_sound(self.cfg.voice_continue)
                self._log("🎙 続行アナウンス再生")
                _continue_round_start()
                self._log("⏸ 続行/霧ラウンド中 → 他窓フリーズ開始")
            # 3クラ開け続行開始
            if is_OpenSpecialRound_target and st.OpenSpecialRound_wins < config.OpenSpecialRound_TARGET_WINS:
                st.is_OpenSpecialRound_round = True
                self._log(f"3クラ解放ラウンド開始（勝利数: {st.OpenSpecialRound_wins}/{config.OpenSpecialRound_TARGET_WINS}）")
                t = threading.Thread(target=self._do_OpenSpecialRound_loop, daemon=True)
                t.start()
            elif is_OpenSpecialRound_target:
                self._log("DTM/Waldoラウンドだが3勝達成済み→AFK解除なし")
        else:
            # 全テラーがスキップ対象 → 自爆（まだ自爆していなければ）
            if self.cfg.do_skip and not st.is_continue_round:
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
        if SharedState.get_instance_type() not in config.INSTANCE_PRIVATE:
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
                    PlaySound.play_sound(cfg.voice_item_lost)
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