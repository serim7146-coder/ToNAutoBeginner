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
    voice_intermission: str = ""
    announce_intermission: bool = False
    voice_continue: str = ""
    voice_fog: str = ""
    voice_item_lost: str = ""
    voice_foxy: str = ""


@dataclass
class WindowState:
    log_pos: int = 0
    in_round: bool = False
    round_type: str = ""
    terror_ids: list[int] = field(default_factory=list)
    map_id: int = 0
    transformed_uid: int | None = None
    fog: bool = False
    is_continue_round: bool = False
    _skip_time: float = 0.0
    begin_done: bool = False
    is_OpenSpecialRound_round: bool = False
    OpenSpecialRound_wins: int = 0
    item_id: int = 1
    waiting_for_equip: bool = False
