import re
from dataclasses import dataclass

import MatchTNL


EVENT_BEGIN_DONE = "begin_done"
EVENT_ROUND_START = "round_start"
EVENT_KILLERS_SET = "killers_set"
EVENT_YOU_DIED = "you_died"
EVENT_ROUND_OVER = "round_over"
EVENT_VERIFIED_END = "verified_end"
EVENT_KILLERS_UNKNOWN = "killers_unknown"
EVENT_FOXY = "foxy"
EVENT_KILLERS_REVEALED = "killers_revealed"
EVENT_JOINING = "joining"
EVENT_ITEM_EQUIP = "item_equip"
EVENT_LIVED = "lived"
EVENT_USER_AUTH = "user_auth"
EVENT_SUS_PLAYER = "sus_player"
EVENT_CREATURE_BLOODTHIRSTY = "creature_bloodthirsty"
EVENT_HUNGRY_HOME_INVADER = "hungry_home_invader"
EVENT_RESPAWN = "respawn"


RE_ROUND_START = re.compile(r"This round is taking place at (.+) and the round type is (.+)")
RE_MAP_ID = re.compile(r"\((\d+)\)$")
RE_KILLERS_SET = re.compile(r"Killers have been set - (\d+) (\d+) (\d+) // Round type is (.+)")
RE_KILLERS_UNKNOWN = re.compile(r"Killers is unknown - \?\?\? // .+ // Round type is (.+)")
RE_KILLERS_REVEALED = re.compile(r"Killers have been revealed - (\d+) (\d+) (\d+) // Round type is (.+)")
RE_FOXY = re.compile(r"foxy the pirate turned evil!", re.IGNORECASE)
RE_LIVED = re.compile(r"^Lived in round[.]$")
RE_YOU_DIED = re.compile(r"^You died[.]$")
RE_ROUND_OVER = re.compile(r"^RoundOver$")
RE_VERIFIED_END = re.compile(r"^Verified Round End$")
RE_BEGIN_DONE = re.compile(r"^Verified$")
RE_ITEM_EQUIP = re.compile(r"^Equipping (\d+)[.](?: Was using (\d+))?")
RE_USER_AUTH = re.compile(r"User Authenticated: (.+?) \((usr_[0-9a-f-]+)\)")
RE_SUS_PLAYER = re.compile(r"^Sus player(?:\s+(\d+))?\s*=\s*(\d+)\s+(.+)$")
RE_CREATURE_BLOODTHIRSTY = re.compile(r"^The creature is bloodthirsty today[.][.][.]$")
RE_HUNGRY_HOME_INVADER = re.compile(r"^I hear strange sounds coming from the kitchen[.]$")
RE_RESPAWN_GENERIC = re.compile(r"^Player respawned, opted out!$")
RE_LOG_PREFIX = re.compile(r"^\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+\s+-\s+")
RE_JOINING = re.compile(r"\[Behaviour\] Joining (wrld_[^:]+):\d+(.*?)(?:~region\(|$)")


@dataclass(frozen=True)
class LogEvent:
    kind: str = ""
    round_type: str = ""
    terror_ids: list[int] | None = None
    raw_map: str = ""
    map_id: int = 0
    user_id: str = ""
    suffix: str = ""
    item_id: int = 0
    previous_item_id: int | None = None
    player_name: str = ""


def strip_prefix(line: str) -> str:
    return RE_LOG_PREFIX.sub("", line).strip()


def parse(line: str) -> LogEvent | None:
    line = strip_prefix(line)

    if RE_BEGIN_DONE.match(line):
        return LogEvent(EVENT_BEGIN_DONE)

    m = RE_ROUND_START.match(line)
    if m:
        raw_map = m.group(1).strip()
        map_match = RE_MAP_ID.search(raw_map)
        return LogEvent(
            EVENT_ROUND_START,
            round_type=m.group(2).strip(),
            raw_map=raw_map,
            map_id=int(map_match.group(1)) if map_match else 0,
        )

    m = RE_KILLERS_SET.match(line)
    if m:
        round_type = m.group(4).strip()
        return LogEvent(
            EVENT_KILLERS_SET,
            round_type=round_type,
            terror_ids=MatchTNL.parse_terror_ids(m.group(1), m.group(2), m.group(3), round_type),
        )

    if RE_YOU_DIED.match(line):
        return LogEvent(EVENT_YOU_DIED)
    if RE_ROUND_OVER.match(line):
        return LogEvent(EVENT_ROUND_OVER)
    if RE_VERIFIED_END.match(line):
        return LogEvent(EVENT_VERIFIED_END)

    m = RE_KILLERS_UNKNOWN.match(line)
    if m:
        return LogEvent(EVENT_KILLERS_UNKNOWN, round_type=m.group(1).strip())

    if RE_FOXY.search(line):
        return LogEvent(EVENT_FOXY)

    if RE_CREATURE_BLOODTHIRSTY.match(line):
        return LogEvent(EVENT_CREATURE_BLOODTHIRSTY)

    if RE_HUNGRY_HOME_INVADER.match(line):
        return LogEvent(EVENT_HUNGRY_HOME_INVADER)

    m = RE_KILLERS_REVEALED.match(line)
    if m:
        round_type = m.group(4).strip()
        return LogEvent(
            EVENT_KILLERS_REVEALED,
            round_type=round_type,
            terror_ids=MatchTNL.parse_terror_ids(m.group(1), m.group(2), m.group(3), round_type),
        )

    m = RE_JOINING.search(line)
    if m:
        return LogEvent(EVENT_JOINING, suffix=m.group(2))

    m = RE_ITEM_EQUIP.match(line)
    if m:
        return LogEvent(
            EVENT_ITEM_EQUIP,
            item_id=int(m.group(1)),
            previous_item_id=int(m.group(2)) if m.group(2) is not None else None,
        )

    m = RE_SUS_PLAYER.match(line)
    if m:
        return LogEvent(
            EVENT_SUS_PLAYER,
            player_name=m.group(3).strip(),
        )

    if RE_LIVED.match(line):
        return LogEvent(EVENT_LIVED)

    if RE_RESPAWN_GENERIC.match(line):
        return LogEvent(EVENT_RESPAWN)

    m = RE_USER_AUTH.search(line)
    if m:
        return LogEvent(EVENT_USER_AUTH, user_id=m.group(2), player_name=m.group(1).strip())

    return None
