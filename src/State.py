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
    voice_intermission: str = ""
    announce_intermission: bool = False
    # 問答無用で自爆するラウンド（privateのみ）。続行リストより優先する
    skip_rounds: set = field(default_factory=set)
    skip_variant_exempt: bool = False   # Variant/Gigabytesは自爆しない
    # 続行リストを見ずに自爆しない（privateのみ）。skip_rounds より優先する
    continue_rounds: set = field(default_factory=set)
    voice_continue: str = ""
    voice_fog: str = ""
    voice_item_lost: str = ""
    voice_foxy: str = ""
    voice_8pages: str = ""
    voice_punish: str = ""


@dataclass
class WindowState:
    instance_type: str = "public"
    log_pos: int = 0
    in_round: bool = False
    round_type: str = ""
    terror_ids: list[int] = field(default_factory=list)
    map_id: int = 0
    round_seq: int = 0
    round_over_time: float = 0.0   # RoundOverを受けた時刻（Begin移動の起点）
    round_end_seen: bool = False   # Verified Round End を受けたか（クリック可の合図）
    # 定期シグナル（約300秒周期のVerified）の追跡。ラウンドをまたぐのでROUND_STARTでは消さない
    periodic_last: float = 0.0          # 最後に「定期」と判定したVerifiedの時刻
    periodic_period: float = 0.0        # 推定周期（0ならconfigの初期値を使う）
    pending_verified_time: float = 0.0  # 本物として採用したVerifiedの時刻（Everything recieved待ち）
    statistics_sent: bool = False
    transformed_uid: int | None = None
    local_player_name: str = ""
    fog: bool = False
    is_continue_round: bool = False
    _skip_time: float = 0.0
    begin_done: bool = False
    speed_round_kind: str = ""   # 速度から先読みしたラウンド種別（通知済みのもの）
    speed_probe_done: bool = False  # このラウンドで速度検知を起動したか
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
    # Sabotageで選出されたマーダーの表示名（Sus player / Sus player 2）。
    # Verified Round End でクリアする——ROUND_START と同じ秒に積まれるため
    sus_players: list[str] = field(default_factory=list)
    moon_repeat: bool = False   # このmoonが2回目以降か（ROUND_STARTで確定）
