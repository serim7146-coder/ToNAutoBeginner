import re
from dataclasses import dataclass
from datetime import datetime

import MatchTNL
import TerrorReplacement


EVENT_VERIFIED = "verified"      # `Verified` の行（受理か定期かは LogMonitor が決める）
EVENT_ROUND_START = "round_start"
EVENT_KILLERS_SET = "killers_set"
EVENT_YOU_DIED = "you_died"
EVENT_ROUND_OVER = "round_over"
EVENT_VERIFIED_END = "verified_end"
EVENT_KILLERS_UNKNOWN = "killers_unknown"
EVENT_KILLERS_REVEALED = "killers_revealed"
EVENT_JOINING = "joining"
EVENT_ITEM_EQUIP = "item_equip"
EVENT_LIVED = "lived"
EVENT_USER_AUTH = "user_auth"
EVENT_SUS_PLAYER = "sus_player"
EVENT_RESPAWN = "respawn"
EVENT_EVERYTHING_RECEIVED = "everything_received"
# 置き換えテラーの合図（Atrached・Bloodthirsty・Foxy など）。どれかは LogEvent.flag。
# 行と flag の対応は TerrorReplacement.SIGNALS
EVENT_REPLACEMENT = "replacement"
EVENT_MASTER_SWITCHED = "master_switched"
EVENT_ENRAGE = "enrage"
EVENT_STUNNED = "stunned"
EVENT_PLAYER_JOINED = "player_joined"
EVENT_JOY = "joy"
EVENT_PLAYER_LEFT = "player_left"
EVENT_PAGE_COLLECTED = "page_collected"


RE_ROUND_START = re.compile(r"This round is taking place at (.+) and the round type is (.+)")
ROUND_START_MARK = " and the round type is "      # 前絞りの印（USER_AUTH_MARK の説明を参照）
RE_MAP_ID = re.compile(r"\((\d+)\)$")
RE_KILLERS_SET = re.compile(r"Killers have been set - (\d+) (\d+) (\d+) // Round type is (.+)")
RE_KILLERS_UNKNOWN = re.compile(r"Killers is unknown - \?\?\? // .+ // Round type is (.+)")
RE_KILLERS_REVEALED = re.compile(r"Killers have been revealed - (\d+) (\d+) (\d+) // Round type is (.+)")
RE_LIVED = re.compile(r"^Lived in round[.]$")
LIVED_MARK = "Lived in round"
RE_YOU_DIED = re.compile(r"^You died[.]$")
RE_ROUND_OVER = re.compile(r"^RoundOver$")
RE_VERIFIED_END = re.compile(r"^Verified Round End$")
RE_VERIFIED = re.compile(r"^Verified$")
VERIFIED_MARK = "Verified"      # 前絞りの印（Verified Round End も含む）
ROUND_OVER_MARK = "RoundOver"
# ToN側の綴りどおり（recieved）。本物のVerifiedにだけ続く行
RE_EVERYTHING_RECEIVED = re.compile(r"^Everything recieved, looks good to meee~!$")
RE_ITEM_EQUIP = re.compile(r"^Equipping (\d+)[.](?: Was using (\d+))?")
ITEM_EQUIP_MARK = "Equipping "
# 8 Pages でページを取った（n 枚目）。持ち込めないアイテムはここでなくなる
RE_PAGE_COLLECTED = re.compile(r"^Page Collected - (\d)/8$")
PAGE_COLLECTED_MARK = "Page Collected - "
RE_USER_AUTH = re.compile(r"User Authenticated: (.+?) \((usr_[0-9a-f-]+)\)")
# 前絞りの印（*_MARK）。ログを末尾から遡る処理は、20万行を全部 parse() にかけると
# 1本1秒以上かかる。探す行は必ずこの文字列を含むので、含まない行は parse() に
# かけずに飛ばす。正規表現を直したら一緒に直すこと（グループを開く ( を除いた正規表現に
# 含まれていることをテストが見張っている）
USER_AUTH_MARK = "User Authenticated: "
# 入退室。`[PlayerLog] OnPlayerJoined: 名前 (VR=False)` という別形式も出るが
# usr_ID が無いので [Behaviour] の方だけを使う（両方拾うと二重に数える）。
# 名前に括弧が入りうるので、末尾の (usr_...) で区切る。
# OnPlayerJoinComplete / OnPlayerLeftRoom は直後の空白が無いので当たらない
RE_PLAYER_JOINED = re.compile(
    r"^\[Behaviour\] OnPlayerJoined (.+) \((usr_[0-9a-f-]+)\)$")
RE_PLAYER_LEFT = re.compile(
    r"^\[Behaviour\] OnPlayerLeft (.+) \((usr_[0-9a-f-]+)\)$")
PLAYER_JOINED_MARK = "OnPlayerJoined "
PLAYER_LEFT_MARK = "OnPlayerLeft "
RE_SUS_PLAYER = re.compile(r"^Sus player(?:\s+(\d+))?\s*=\s*(\d+)\s+(.+)$")
RE_RESPAWN_GENERIC = re.compile(r"^Player respawned, opted out!$")
RESPAWN_MARK = "Player respawned"
YOU_DIED_MARK = "You died"
SUS_PLAYER_MARK = "Sus player"
RE_LOG_PREFIX = re.compile(r"^\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+\s+-\s+")
RE_JOINING = re.compile(r"\[Behaviour\] Joining (wrld_[^:]+):\d+(.*?)(?:~region\(|$)")
JOINING_MARK = "Joining wrld_"
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
    terror_ids: list[int] | None = None
    raw_map: str = ""
    map_id: int = 0
    user_id: str = ""
    suffix: str = ""
    item_id: int = 0
    previous_item_id: int | None = None
    player_name: str = ""
    instance: str = ""      # 入室の行の wrld_… から後ろ全部（同じインスタンスなら誰でも同じ）
    page: int = 0           # Page Collected の n（今は使わない）
    flag: str = ""          # 置き換えの合図の WindowState の属性（EVENT_REPLACEMENT）


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
# 番号の付かない形（Waldo など）。この2つだけ拾う（ほかの番号なしの行はワールドの部品）:
#   Ignoring TrySetOwner attempt on Waldo because serim01 already owner
#   Transferred ownership of Waldo to 3
RE_NETWORK_OBJECT_BARE = (
    re.compile(r"^\[NetworkProcessing\] .*? attempt on (.+?) because .+ already owner$"),
    re.compile(r"^\[NetworkProcessing\] Transferred ownership of (.+) to \d+$"),
)


def network_object_name(line: str) -> str:
    """`[NetworkProcessing]` の行からオブジェクト名を取り出す。無ければ空文字"""
    m = RE_NETWORK_OBJECT.match(line)
    if not m:
        for bare in RE_NETWORK_OBJECT_BARE:      # 番号ありの形を優先する
            b = bare.match(line)
            if b:
                return b.group(1).strip()
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

    if RE_VERIFIED.match(line):
        return LogEvent(EVENT_VERIFIED)

    if RE_EVERYTHING_RECEIVED.match(line):
        return LogEvent(EVENT_EVERYTHING_RECEIVED)

    if RE_MASTER_SWITCHED.match(line):
        return LogEvent(EVENT_MASTER_SWITCHED)

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

    signal = TerrorReplacement.match_signal(line)
    if signal is not None:
        return LogEvent(EVENT_REPLACEMENT, flag=signal.flag)

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
        return LogEvent(EVENT_JOINING, suffix=m.group(2),
                        instance=line[m.start(1):].strip())

    m = RE_ITEM_EQUIP.match(line)
    if m:
        return LogEvent(
            EVENT_ITEM_EQUIP,
            item_id=int(m.group(1)),
            previous_item_id=int(m.group(2)) if m.group(2) is not None else None,
        )

    m = RE_PAGE_COLLECTED.match(line)
    if m:
        return LogEvent(EVENT_PAGE_COLLECTED, page=int(m.group(1)))

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
