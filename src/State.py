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
    hoshiimo_skip: bool = False
    osc_port: int = 0       # 0 = OSC不可（従来のキーボード操作にフォールバック）
    voice_intermission: str = ""
    announce_intermission: bool = False
    voice_continue: str = ""
    voice_fog: str = ""
    voice_item_lost: str = ""
    voice_foxy: str = ""


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
    statistics_sent: bool = False
    transformed_uid: int | None = None
    local_player_name: str = ""
    fog: bool = False
    is_continue_round: bool = False
    _skip_time: float = 0.0
    begin_done: bool = False
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
