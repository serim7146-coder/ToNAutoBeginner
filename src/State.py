from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WindowConfig:
    hwnd: int = 0
    log_path: Optional[Path] = None
    active: bool = True
    auto_begin: bool = True
    do_skip: bool = True
    cancel_afk: bool = True
    osc_port: int = 0       # 0 = OSC不可（従来のキーボード操作にフォールバック）
    # VRChatが値を送ってくるポート。0なら従来どおり osc_port+1 を使う
    # （--osc= の送信ポートは受信+1とは限らない）
    osc_out_port: int = 0
    voice_intermission: str = ""
    announce_intermission: bool = False
    # 問答無用で自爆するラウンド（privateのみ）。続行リストより優先する
    skip_rounds: set = field(default_factory=set)
    # 続行リストを見ずに自爆しない（privateのみ）。skip_rounds より優先する
    continue_rounds: set = field(default_factory=set)
    voice_continue: str = ""
    voice_fog: str = ""
    voice_item_lost: str = ""
    voice_foxy: str = ""
    voice_8pages: str = ""
    voice_punish: str = ""
    voice_list_lost: str = ""
    voice_unbound: str = ""
    voice_midnight: str = ""
    voice_alternate: str = ""
    voice_ghost: str = ""


@dataclass
class WindowState:
    instance_type: str = "public"
    # Joining 行の公開範囲（LogParser.ACCESS_*）。空＝判定できない（看破は NG 扱い）
    instance_access: str = ""
    # ── 霧の看破（ラウンドごと） ──
    fog_reading: bool = False             # Killers is unknown から公開/RoundOver まで
    early_read_hits: dict = field(default_factory=dict)   # 正規化した名前 → ID
    early_read_tid: Optional[int] = None  # 看破で使ったID
    early_read_void: bool = False         # 2種類以上のIDが出た → このラウンドは使わない
    eight_pages_unknown_logged: bool = False   # 未登録の 8 Pages 番号の案内は1回だけ
    early_read_holding: bool = False      # 最初に当たった名前を保留中（FogEarlyRead.HOLD_SEC）
    early_read_hold_log_t: Optional[float] = None   # 保留を始めた行のログ時刻
    early_read_hold_wall: float = 0.0     # 保留を始めた実時刻（tick で確定させる用）
    statistics_quiet: bool = False        # 看破の結果で送る統計は黙って送る
    log_pos: int = 0
    in_round: bool = False
    round_type: str = ""
    terror_ids: list[int] = field(default_factory=list)
    map_id: int = 0
    round_seq: int = 0
    round_over_time: float = 0.0   # RoundOverを受けた時刻（Begin移動の起点）
    round_end_seen: bool = False   # Verified Round End を受けたか（クリック可の合図）
    # 定期シグナル（約300秒周期のVerified）の追跡。ラウンドをまたぐのでROUND_STARTでは消さない
    # 時刻はどれもログの時刻（壁時計ではない。負荷で処理が遅れてもずれないため）
    log_now: float = 0.0                # 最後に読んだ行の時刻
    periodic_phase: float = 0.0         # 最後に「定期」と分かったVerifiedの時刻
    pending_verified_time: float = 0.0  # 本物として採用したVerifiedの時刻（ラウンド開始待ち）
    statistics_sent: bool = False
    transformed_uid: int | None = None
    local_player_name: str = ""
    local_user_id: str = ""
    # インスタンス内のプレイヤー（usr_ID。表示名は変わりうる）。自分も入る
    players: set = field(default_factory=set)
    # players を信用できるか。起動時に復元できなければ False のまま——
    # 「他の人がいる」側に倒す（他人の周回を自分の tnl で裁かないため）
    players_known: bool = False
    # 入室者の表示名（usr_ID → 名前）。主催リストの希望は表示名で持っている
    player_names: dict = field(default_factory=dict)
    # 希望が見つからなかった入室者（ログを1回だけ出すため）
    unmatched_logged: set = field(default_factory=set)
    fog: bool = False
    is_continue_round: bool = False
    _skip_time: float = 0.0
    begin_done: bool = False
    speed_round_kind: str = ""   # 速度から先読みしたラウンド種別（通知済みのもの）
    speed_probe_done: bool = False  # このラウンドで速度検知を起動したか
    speed_strafe_done: bool = False  # このラウンドで速度検知の横移動をしたか
    # 自爆（リトライ込み）が走っているラウンドの round_seq。流れを1本にするため
    suicide_seq: int = -1
    # 入室のたびに進める。インスタンスをまたいで動き続けるものを止めるため
    instance_seq: int = 0
    speed_freeze_held: bool = False  # この窓が速度検知フリーズを張っているか
    round_freeze_held: bool = False  # この窓がラウンド突入フリーズを張っているか
    speed_freeze_kind: str = ""      # "8pages" / "punish"。解除条件を覚えるため
    is_open_special_round_round: bool = False
    open_special_round_wins: int = 0
    item_id: int = 1
    item_id_at_round_start: int = 1
    waiting_for_equip: bool = False
    equip_freeze_held: bool = False
    item_lost_announced: bool = False
    item_lost_this_round: bool = False
    randomizer_item_changed: bool = False
    died_this_round: bool = False
    lived_this_round: bool = False
    item_equipped_after_death: bool = False
    pending_sabotage_murder: bool = False
    sabotage_murder_this_round: bool = False
    bloodthirsty_creature_variant: bool = False
    hungry_home_invader_variant: bool = False
    atrached_variant: bool = False
    gigabytes: bool = False
    # 置き換えの合図（TerrorReplacement.TABLE の flag）
    foxy: bool = False
    neo_pilot: bool = False
    # Sabotageで選出されたマーダーの表示名（Sus player / Sus player 2）。
    # Verified Round End でクリアする——ROUND_START と同じ秒に積まれるため
    sus_players: list[str] = field(default_factory=list)
    moon_repeat: bool = False   # このmoonが2回目以降か（ROUND_STARTで確定）
    # Enrage のログから前倒しで判明させたテラーID。ラウンドごとに落とす
    enrage_identified: int | None = None
    # 主催リスト喪失を知らせたか。ラウンドごとに鳴らさないための抑制
    list_lost_notified: bool = False
    list_lost_reason: str = ""      # "host"（主催リスト無し）/ "wishes"（希望無し）
