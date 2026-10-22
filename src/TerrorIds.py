"""ツールの独自のテラー番号（assets/data/terror_ids.json）。

ToN ListTool の番号（Alternate は＋134・Unbound は＋200 …）から切り離した、ツール自身の番号:

    0〜999    Classic            ゲームのログの番号そのまま
    1000〜    Alternate          1000＋ログの番号（Alternate の枠に来た番号は 36 以上も）
    2000〜    Unbound            2000＋ログの番号
    3000〜    8 Pages            3000＋8 Pages のログの番号（Classic の同じテラーとも別の番号）
    4000〜    固定の表           Variant・Moon・Special・Glorbo など。足すときは後ろへ（一度決めた番号は動かさない）

ログの番号を独自の番号に直すのは入口の 1 か所（LogMonitor._own_killer_ids → from_log）だけで、その先は
すべて独自の番号。ToN ListTool の番号を使うのは .tnl・.tnnl・主催リストとのやり取り（list_from_listtool・
list_to_listtool）だけ、前の版の番号（legacy）は移行（continue_list.json・continue_table.json・rounds.sqlite・
DB の無印 ToNRounds）にだけ使う。

import するのは config だけ（DebugLog は関数の中で読む）。表は import のときに読み、読めなければ例外
（番号が引けないまま動かさない）。
"""
import json
import threading
from dataclasses import dataclass

import config

FILE = "assets/data/terror_ids.json"

ALTERNATE_BASE, UNBOUND_BASE, EIGHT_PAGES_BASE, FIXED_BASE = 1000, 2000, 3000, 4000
EIGHT_PAGES_KEY = "8 Pages/8ページ"          # MatchTNL.LOG_TO_TNL["8 Pages"] と同じ（テストで確かめる）
EIGHT_PAGES_ROUND_ID = 105                    # config.ROUND_TYPE_IDS["8 Pages"] と同じ（テスト）
EIGHT_PAGES_ROUND = "8 Pages"

# ログの番号が Alternate の枠に来るラウンド（None は全枠、リストはその枠だけ。0 始まり）。
# 通常の Fog / Ghost / 8 Pages には Classic のテラーしか出ない
ALTERNATE_SLOT_POSITIONS: dict[str, list[int] | None] = {
    "Alternate":         None,
    "Midnight":          [2],
    "Fog (Alternate)":   None,
    "Ghost (Alternate)": None,
}

# 4000 番台の固定の番号（名前を付けて使う側が番号を直に書かないための定数。表と合うことはテストで確かめる）
HUNGRY_HOME_INVADER = 4000
ATRACHED = 4001
BLOODTHIRSTY_CREATURE = 4002
MYSTIC_MOON, BLOOD_MOON, TWILIGHT, SOLSTICE = 4003, 4004, 4005, 4006
RIFT_MONSTERS = 4007
MEATBALL_MAN = 4008
GIGABYTES = 4009
NEO_PILOT = 4010
FOXY = 4011              # 表示名は Evil Purple Foxy
GLORBO = 4012
SELF_INSERTS_BLOODTHIRSTY = 4013

KINDS = ("classic", "alternate", "unbound", "eight_pages", "variant", "moon", "special")
_FIXED_KINDS = ("variant", "moon", "special")


@dataclass(frozen=True)
class Terror:
    id: int                      # 独自の番号
    kind: str                    # KINDS のどれか。千の位と合う
    category: str | None         # 統計の分類（classic・alternate・unbound）。無ければ None
    name: str
    log: int | None              # その枠でのログの番号（8 Pages はページの番号）。無ければ None
    listtool: int | None         # ToN ListTool の番号（8 Pages の行は list_id）。無ければ None
    legacy: int | None           # 前の版のツールの番号（移行にだけ使う。8 Pages の行は list_id）
    icon: tuple | None           # (シートの名前, 列, 段)


@dataclass(frozen=True)
class Table:
    rows: dict                   # {独自の番号: Terror}
    by_listtool: dict            # {ListTool の番号: 独自の番号}（8 Pages の行は含めない）
    by_listtool_8p: dict         # {list_id: 3000 番台の番号}（8 Pages の場面だけで引く）
    by_legacy: dict
    by_legacy_8p: dict


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _kind_matches(own: int, kind: str) -> bool:
    if kind == "classic":
        return 0 <= own < ALTERNATE_BASE
    if kind == "alternate":
        return ALTERNATE_BASE <= own < UNBOUND_BASE
    if kind == "unbound":
        return UNBOUND_BASE <= own < EIGHT_PAGES_BASE
    if kind == "eight_pages":
        return EIGHT_PAGES_BASE <= own < FIXED_BASE
    return kind in _FIXED_KINDS and own >= FIXED_BASE


def _debug(message: str):
    import DebugLog
    DebugLog.write(f"[テラー番号] {message}")


def _row_of(item) -> Terror | None:
    if not isinstance(item, dict):
        return None
    own, kind, name = item.get("id"), item.get("kind"), item.get("name")
    if _int_or_none(own) is None or kind not in KINDS or not isinstance(name, str) or not name:
        return None
    if not _kind_matches(own, kind):
        return None
    category = item.get("category")
    if category not in (None, "classic", "alternate", "unbound"):
        return None
    icon = item.get("icon")
    if isinstance(icon, (list, tuple)) and len(icon) == 3 and isinstance(icon[0], str):
        icon = (icon[0], int(icon[1]), int(icon[2]))
    else:
        icon = None
    return Terror(own, kind, category, name, _int_or_none(item.get("log")),
                  _int_or_none(item.get("listtool")), _int_or_none(item.get("legacy")), icon)


def _index(rows, attribute: str, eight_pages: bool, label: str) -> dict:
    """{attribute の値: 独自の番号}。同じ値が 2 つの行にあれば、その値は索引に入れない（debug.log に 1 行）"""
    found: dict = {}
    clash: set = set()
    for row in rows:
        if (row.kind == "eight_pages") != eight_pages:
            continue
        value = getattr(row, attribute)
        if value is None:
            continue
        if value in found:
            clash.add(value)
        found[value] = row.id
    for value in sorted(clash):
        del found[value]
        _debug(f"{label} の {value} が重なっているので引きません")
    return found


def parse(raw) -> Table:
    """terror_ids.json の中身 → Table。悪い行・千の位と kind の食い違い・番号の重複した行は捨てて、
    捨てた件数を debug.log に 1 行（読めない形なら ValueError）"""
    items = raw.get("terrors") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("terror_ids.json に terrors がありません")
    rows: dict = {}
    bad = 0
    for item in items:
        row = _row_of(item)
        if row is None or row.id in rows:
            bad += 1
            continue
        rows[row.id] = row
    if bad:
        _debug(f"terror_ids.json の使えない行を捨てました: {bad} 行")
    ordered = [rows[own] for own in sorted(rows)]
    return Table(rows=rows,
                 by_listtool=_index(ordered, "listtool", False, "ListTool の番号"),
                 by_listtool_8p=_index(ordered, "listtool", True, "8 Pages の ListTool の番号"),
                 by_legacy=_index(ordered, "legacy", False, "前の番号"),
                 by_legacy_8p=_index(ordered, "legacy", True, "8 Pages の前の番号"))


def _load() -> Table:
    return parse(json.loads(config.resource_path(FILE).read_text(encoding="utf-8")))


TABLE = _load()


# ── 引く ───────────────────────────────────────
def row(own) -> Terror | None:
    return TABLE.rows.get(own)


def name(own) -> str:
    """ログ・画面に出す名前。表に無ければ `#番号`"""
    found = TABLE.rows.get(own)
    return found.name if found is not None else f"#{own}"


def icon(own):
    found = TABLE.rows.get(own)
    return found.icon if found is not None else None


def kind(own) -> str | None:
    found = TABLE.rows.get(own)
    return found.kind if found is not None else None


def category(own) -> str | None:
    found = TABLE.rows.get(own)
    return found.category if found is not None else None


def ids() -> list:
    """表の独自の番号（昇順）"""
    return sorted(TABLE.rows)


def eight_pages_ids() -> frozenset:
    """8 Pages に出るテラーの番号（3000 番台）"""
    return frozenset(own for own, found in TABLE.rows.items() if found.kind == "eight_pages")


def game_id(own) -> int:
    """ゲームのログの番号（Alternate は 0〜35 など）。ログの番号が無い行（4000 番台など）は独自の番号のまま"""
    found = TABLE.rows.get(own)
    return found.log if found is not None and found.log is not None else own


def statistics_id(own) -> int:
    """統計で数えるときの番号。8 Pages の行（3000 番台）は、ListTool の番号を通常の索引で引いた同じテラーに
    足して数える（8 Pages の Huggy は Huggy の数に入る）。通常の番号が無い 8 Pages 限定のテラー
    （Baldi など）は 3000 番台のまま"""
    found = TABLE.rows.get(own)
    if found is None or found.kind != "eight_pages" or found.listtool is None:
        return own
    return TABLE.by_listtool.get(found.listtool, own)


_stat_members: dict | None = None


def statistics_ids(own) -> frozenset:
    """own として数える番号の集合: own と、own にまとめて数える 8 Pages の番号。1 テラーの内訳を引くときに使う"""
    global _stat_members
    if _stat_members is None:
        members: dict = {}
        for number in TABLE.rows:
            members.setdefault(statistics_id(number), set()).add(number)
        _stat_members = {key: frozenset(value) for key, value in members.items()}
    return _stat_members.get(own) or frozenset({own})


_reported: set = set()
_reported_lock = threading.Lock()


def _report_once(kind_key: str, number, message: str):
    """同じ番号は debug.log に 1 回だけ書く（主催リストは数秒ごとに読むので、毎回書かない）"""
    with _reported_lock:
        if (kind_key, number) in _reported:
            return
        _reported.add((kind_key, number))
    _debug(message)


def report_unknown(own):
    """表に無い独自の番号が流れてきたことを、番号ごとに 1 回だけ debug.log に書く"""
    _report_once("own", own, f"表にない番号 {own} が流れてきました（名前は #{own} で出します）")


# ── 入口: ゲームのログの番号 → 独自の番号 ──────────────────
def from_log(log_ids, round_type: str, state_round_type: str = "") -> list:
    """`Killers have been set/revealed` のログの番号 → 独自の番号（入口はここだけ）。

    - 8 Pages: 先頭の番号だけ使い、3000＋ページの番号（2 つ目以降は捨てる）。表に無いページも落とさない
    - ツールの今のラウンドが Unbound（state_round_type）: すべて 2000＋番号
    - Alternate の枠（ALTERNATE_SLOT_POSITIONS）: 1000＋番号。36 以上も Alternate
    - それ以外: 番号そのまま（Classic）。表に無い番号も流す（名前は `#番号`）
    """
    log_ids = list(log_ids or ())
    if round_type == EIGHT_PAGES_ROUND:
        return [EIGHT_PAGES_BASE + tid for tid in log_ids[:1]]
    if state_round_type == "Unbound":
        return [UNBOUND_BASE + tid for tid in log_ids]
    if round_type in ALTERNATE_SLOT_POSITIONS:
        positions = ALTERNATE_SLOT_POSITIONS[round_type]
        return [ALTERNATE_BASE + tid if positions is None or index in positions else tid
                for index, tid in enumerate(log_ids)]
    return log_ids


# ── 出入り口: ToN ListTool の番号（.tnl・.tnnl・主催リスト）────────
def _is_eight_pages_key(key) -> bool:
    return key == EIGHT_PAGES_KEY


def _lookup(lt, key: str) -> tuple:
    """(独自の番号 | None, 状態)。状態は "ok"・"not_appearing"（8 Pages のキーにだけある、8 Pages に出ない番号）・
    "unknown"（表に無い）"""
    if _is_eight_pages_key(key):
        if lt in TABLE.by_listtool_8p:
            return TABLE.by_listtool_8p[lt], "ok"
        return None, ("not_appearing" if lt in TABLE.by_listtool else "unknown")
    if lt in TABLE.by_listtool:
        return TABLE.by_listtool[lt], "ok"
    return None, "unknown"


def from_listtool(lt, key: str, source: str = "") -> int | None:
    """ListTool の番号 → 独自の番号。8 Pages のキーは 8 Pages の索引だけを引く（8 Pages に出ない番号は黙って None）。
    表に無い番号は None（番号ごとに 1 回だけ debug.log。主催リストは数秒ごとに読むため）"""
    own, state = _lookup(lt, key)
    if state == "unknown":
        _report_once("listtool", lt, f"{source or 'ListTool'} の番号 {lt} が表にないので捨てます")
    return own


def to_listtool(own) -> int | None:
    """独自の番号 → ListTool の番号。ListTool の番号が無いものは None（.tnl に書かない）"""
    found = TABLE.rows.get(own)
    return found.listtool if found is not None else None


def list_from_listtool(data, source: str = "", strict: bool = False) -> dict:
    """{キー: ListTool の番号の集合} → {キー: frozenset(独自の番号)}。空になったキーは持たない。

    8 Pages のキーで 8 Pages に出ない番号（ListTool の枠にだけある 116 個）は捨て、件数を debug.log に 1 行
    （読み込みごと）。表に無い番号は番号ごとに 1 回だけ debug.log（strict なら ValueError。テストが使う）"""
    result: dict = {}
    not_appearing = 0
    for key, numbers in (data or {}).items():
        mapped = set()
        for lt in numbers:
            own, state = _lookup(lt, key)
            if state == "unknown":
                if strict:
                    raise ValueError(f"ListTool の番号 {lt} が表にありません（キー {key}）")
                from_listtool(lt, key, source)
            elif state == "not_appearing":
                not_appearing += 1
            if own is not None:
                mapped.add(own)
        if mapped:
            result[key] = frozenset(mapped)
    if not_appearing:
        _debug(f"{source or 'ListTool'} の 8 Pages に出ないテラー {not_appearing} 件を捨てました")
    return result


def list_to_listtool(data) -> dict:
    """{キー: 独自の番号の集合} → {キー: [ListTool の番号（昇順）]}。ListTool の番号が無いものは書かない"""
    return {key: sorted({lt for lt in (to_listtool(own) for own in numbers) if lt is not None})
            for key, numbers in (data or {}).items()}


# ── 移行: 前の版のツールの番号 → 独自の番号 ──────────────────
def own_from_legacy(x, eight_pages: bool = False) -> int | None:
    """前の版のツールの番号 → 独自の番号。8 Pages の場面（eight_pages）は 8 Pages の索引（list_id）が先、
    当たらなければ通常の索引。どちらにも無ければ None"""
    if eight_pages and x in TABLE.by_legacy_8p:
        return TABLE.by_legacy_8p[x]
    return TABLE.by_legacy.get(x)


def own_from_legacy_round(x, round_id) -> int | None:
    """ラウンドの番号つきで引く（DB の行・rounds.sqlite の行）。round_id が 8 Pages（105）なら 8 Pages の場面"""
    if x is None:
        return None
    return own_from_legacy(x, eight_pages=(round_id == EIGHT_PAGES_ROUND_ID))


def migrate_legacy(data) -> tuple:
    """{キー: 前の番号の集合} → ({キー: frozenset(独自の番号)}, 落とした [(キー, 番号)])。

    8 Pages のキーは 8 Pages の索引（list_id）だけで引く（8 Pages に出ない番号は表にあっても落とす）。
    ほかのキーは通常の索引。表に無い番号は落とす。空になったキーは持たない"""
    result: dict = {}
    dropped: list = []
    for key, numbers in (data or {}).items():
        mapped = set()
        for number in sorted(n for n in numbers if isinstance(n, int)):
            if _is_eight_pages_key(key):
                own = TABLE.by_legacy_8p.get(number)
            else:
                own = TABLE.by_legacy.get(number)
            if own is None:
                dropped.append((key, number))
            else:
                mapped.add(own)
        if mapped:
            result[key] = frozenset(mapped)
    return result, dropped
