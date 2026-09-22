import re
from dataclasses import dataclass
from datetime import datetime

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
EVENT_EVERYTHING_RECEIVED = "everything_received"
EVENT_STRING_DOWNLOAD = "string_download"
EVENT_GIGABYTES = "gigabytes"
EVENT_ATRACHED = "atrached"
EVENT_MASTER_SWITCHED = "master_switched"
EVENT_ENRAGE = "enrage"
EVENT_STUNNED = "stunned"
EVENT_PLAYER_JOINED = "player_joined"
EVENT_JOY = "joy"
EVENT_PLAYER_LEFT = "player_left"


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
# ToN側の綴りどおり（recieved）。本物のVerifiedにだけ続く行
RE_EVERYTHING_RECEIVED = re.compile(r"^Everything recieved, looks good to meee~!$")
# Beginが押されるとラウンドデータの取得が始まる。誰が押しても出る。
# 同じ [String Download] で始まる "Clearing string download queue" と区別するため、
# "Attempting to load String from URL" まで含めてマッチさせる。
# The Gigabytes はテラーIDでは判別できない（実測6件でIDが毎回異なる）。
# この行だけが固有の手がかり。Killers have been set と同じ秒に出る。
RE_GIGABYTES = re.compile(r"^The Gigabytes have come[.]$")
# Sonic(classic 40)のVariant。同IDで稀に差し替わるためIDでは判別できない。
RE_ATRACHED = re.compile(r"^Lets play a game[.][.][.]$")
RE_STRING_DOWNLOAD = re.compile(
    r"^\[String Download\] Attempting to load String from URL '(.+)'")
RE_ITEM_EQUIP = re.compile(r"^Equipping (\d+)[.](?: Was using (\d+))?")
RE_USER_AUTH = re.compile(r"User Authenticated: (.+?) \((usr_[0-9a-f-]+)\)")
# 入退室。`[PlayerLog] OnPlayerJoined: 名前 (VR=False)` という別形式も出るが
# usr_ID が無いので [Behaviour] の方だけを使う（両方拾うと二重に数える）。
# 名前に括弧が入りうるので、末尾の (usr_...) で区切る。
# OnPlayerJoinComplete / OnPlayerLeftRoom は直後の空白が無いので当たらない
RE_PLAYER_JOINED = re.compile(
    r"^\[Behaviour\] OnPlayerJoined (.+) \((usr_[0-9a-f-]+)\)$")
RE_PLAYER_LEFT = re.compile(
    r"^\[Behaviour\] OnPlayerLeft (.+) \((usr_[0-9a-f-]+)\)$")
RE_SUS_PLAYER = re.compile(r"^Sus player(?:\s+(\d+))?\s*=\s*(\d+)\s+(.+)$")
RE_CREATURE_BLOODTHIRSTY = re.compile(r"^The creature is bloodthirsty today[.][.][.]$")
RE_HUNGRY_HOME_INVADER = re.compile(r"^I hear strange sounds coming from the kitchen[.]$")
RE_RESPAWN_GENERIC = re.compile(r"^Player respawned, opted out!$")
RE_LOG_PREFIX = re.compile(r"^\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+\s+-\s+")
RE_JOINING = re.compile(r"\[Behaviour\] Joining (wrld_[^:]+):\d+(.*?)(?:~region\(|$)")
RE_MASTER_SWITCHED = re.compile(r"^\[Behaviour\] OnMasterClientSwitched$")
# 名前と triggered の間に空白が無い。Enrage / Enrage2 / Enrage3 は
# 段階が違うだけで名前は同じなので、まとめて扱う
RE_ENRAGE = re.compile(r"^(.*?)triggered an Enrage\d* State!\s*$")
RE_STUNNED = re.compile(r"^(.*?)was stunned[.]$")
# Joy が出るときの行。霧ではテラー不明のまま進むので、これで判明させる
RE_JOY = re.compile(r"^JOY WILL SOON AWAKEN[.][.][.]$")


@dataclass(frozen=True)
class LogEvent:
    kind: str = ""
    round_type: str = ""
    url: str = ""
    terror_ids: list[int] | None = None
    raw_map: str = ""
    map_id: int = 0
    user_id: str = ""
    suffix: str = ""
    item_id: int = 0
    previous_item_id: int | None = None
    player_name: str = ""


RE_LOG_TIME = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")


def log_time(line: str) -> float | None:
    """行頭の時刻（1秒刻み）を epoch 秒で。時刻の無い行・壊れた時刻は None"""
    m = RE_LOG_TIME.match(line or "")
    if not m:
        return None
    try:
        return datetime(*map(int, m.groups())).timestamp()
    except ValueError:
        return None


def strip_prefix(line: str) -> str:
    return RE_LOG_PREFIX.sub("", line).strip()


# ── 看破（--enable-sdk-log-levels 付きのログだけに出る行） ──
# `[NetworkProcessing] … [番号] 名前 …`。番号はテラーIDではない（[7] THE SUN は
# Black Sun = 6）ので使わない。数字とも限らない（[29b] SmileyWalker）。
# 名前だけを取り出す。実ログで見えた形:
#   Ignoring TrySetOwner attempt on [20] Immortal Snail because tsuki__2 already owner
#   Transferred ownership of [86] WALPURGISNACHT to 20
#   serim01 would like to transfer [10] Kuro GuidingStar to すぅみ_suumi
#   Non-owner attempted to request ownership of [10] Kuro GuidingStar for someone else.
#   Setting [12] witchling (15) to request ownership
#   Transferred ownership of [29b] SmileyWalker to 17
EVENT_NETWORK_OBJECT = "network_object"
NETWORK_PROCESSING_TAG = "[NetworkProcessing] "
RE_NETWORK_OBJECT = re.compile(r"^\[NetworkProcessing\] .*?\[[^\]]+\] (.+)$")


def network_object_name(line: str) -> str:
    """`[NetworkProcessing]` の行からオブジェクト名を取り出す。無ければ空文字"""
    m = RE_NETWORK_OBJECT.match(line)
    if not m:
        return ""
    rest = m.group(1)
    for tail in (" to request ownership", " for someone else."):
        if rest.endswith(tail):
            return rest[:-len(tail)].strip()
    if rest.endswith(" already owner") and " because " in rest:
        return rest.rsplit(" because ", 1)[0].strip()
    # 「… to 20」「… to 相手の名前」。名前に " to " を含むテラー（Express
    # Train to Hell）があるので、最後の " to " で切る
    if " to " in rest:
        return rest.rsplit(" to ", 1)[0].strip()
    return ""


# ── インスタンスの公開範囲（Joining 行の suffix から） ──
ACCESS_INVITE = "invite"
ACCESS_INVITE_PLUS = "invite_plus"
ACCESS_FRIENDS = "friends"
ACCESS_FRIENDS_PLUS = "friends_plus"
ACCESS_GROUP_MEMBERS = "group_members"
ACCESS_GROUP_PLUS = "group_plus"
ACCESS_GROUP_PUBLIC = "group_public"
ACCESS_PUBLIC = "public"
ACCESS_UNKNOWN = "unknown"
RE_GROUP_ACCESS = re.compile(r"~groupAccessType\((\w+)\)")


def instance_access(suffix) -> str:
    """Joining 行の suffix（`~private(usr_…)~canRequestInvite` など）から公開範囲を返す"""
    if not isinstance(suffix, str):
        return ACCESS_UNKNOWN
    if "~private(" in suffix:
        return ACCESS_INVITE_PLUS if "~canRequestInvite" in suffix else ACCESS_INVITE
    if "~friends(" in suffix:
        return ACCESS_FRIENDS
    if "~hidden(" in suffix:
        return ACCESS_FRIENDS_PLUS
    if "~group(" in suffix:
        m = RE_GROUP_ACCESS.search(suffix)
        return {"members": ACCESS_GROUP_MEMBERS, "plus": ACCESS_GROUP_PLUS,
                "public": ACCESS_GROUP_PUBLIC}.get(m.group(1) if m else "",
                                                   ACCESS_UNKNOWN)
    return ACCESS_PUBLIC


def parse(line: str) -> LogEvent | None:
    line = strip_prefix(line)

    if line.startswith(NETWORK_PROCESSING_TAG):
        name = network_object_name(line)
        return LogEvent(EVENT_NETWORK_OBJECT, player_name=name) if name else None

    if RE_BEGIN_DONE.match(line):
        return LogEvent(EVENT_BEGIN_DONE)

    if RE_EVERYTHING_RECEIVED.match(line):
        return LogEvent(EVENT_EVERYTHING_RECEIVED)

    if RE_GIGABYTES.match(line):
        return LogEvent(EVENT_GIGABYTES)

    if RE_ATRACHED.match(line):
        return LogEvent(EVENT_ATRACHED)

    if RE_MASTER_SWITCHED.match(line):
        return LogEvent(EVENT_MASTER_SWITCHED)

    m = RE_STRING_DOWNLOAD.match(line)
    if m:
        # URLはログ用。判定には使わない（ラウンドデータのURLは複数あるため）
        return LogEvent(EVENT_STRING_DOWNLOAD, url=m.group(1))

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

    if RE_JOY.match(line):
        return LogEvent(EVENT_JOY)

    m = RE_ENRAGE.match(line)
    if m:
        name = m.group(1).strip()
        # 名前が空の行が実データに46回ある。取れないものはイベントにしない
        return LogEvent(EVENT_ENRAGE, player_name=name) if name else None
    
    m = RE_STUNNED.match(line)
    if m:
        name = m.group(1).strip()
        # 名前が空の行が実データに46回ある。取れないものはイベントにしない
        return LogEvent(EVENT_STUNNED, player_name=name) if name else None

    m = RE_PLAYER_JOINED.match(line)
    if m:
        return LogEvent(EVENT_PLAYER_JOINED, user_id=m.group(2),
                        player_name=m.group(1).strip())

    m = RE_PLAYER_LEFT.match(line)
    if m:
        return LogEvent(EVENT_PLAYER_LEFT, user_id=m.group(2),
                        player_name=m.group(1).strip())

    m = RE_USER_AUTH.search(line)
    if m:
        return LogEvent(EVENT_USER_AUTH, user_id=m.group(2), player_name=m.group(1).strip())

    return None
