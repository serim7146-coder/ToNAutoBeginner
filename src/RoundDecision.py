from dataclasses import dataclass

import config
import MatchTNL


@dataclass(frozen=True)
class KillerDecision:
    is_open_special_round_target: bool
    is_continue_round: bool


def normalize_killer_ids(ids: list[int], round_type: str, state_round_type: str = "") -> list[int]:
    normalized = MatchTNL.apply_alternate_offset(ids, round_type)
    if state_round_type == "Unbound":
        normalized = [tid + MatchTNL.UNBOUND_OFFSET for tid in normalized]
    return normalized


def is_open_special_round_target(
    terror_ids: list[int],
    round_type: str,
    wins: int,
    cancel_afk: bool,
) -> bool:
    return (
        bool(terror_ids and config.OPEN_SPECIAL_ROUND_TERROR_IDS)
        and any(tid in config.OPEN_SPECIAL_ROUND_TERROR_IDS for tid in terror_ids)
        and round_type not in config.SPECIAL_ROUND
        and wins < config.OPEN_SPECIAL_ROUND_TARGET_WINS
        and cancel_afk
    )


def decide_killers(
    keep_on_set: dict,
    terror_ids: list[int],
    round_type: str,
    wins: int,
    cancel_afk: bool,
) -> KillerDecision:
    open_special = is_open_special_round_target(terror_ids, round_type, wins, cancel_afk)
    tnl_key = MatchTNL.LOG_TO_TNL.get(round_type, round_type)
    should_continue = MatchTNL.should_continue(keep_on_set, tnl_key, terror_ids) or open_special
    return KillerDecision(open_special, should_continue)
