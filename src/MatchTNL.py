import functools
import gzip
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

import config
import DebugLog
import TerrorIds


# ═══════════════════════════════════════════════
#  全窓で共有する続行リスト一式
# ═══════════════════════════════════════════════
class SharedLists:
    """続行リスト・参加者別の希望・参加者・タブ。画面（mainGUI）と全窓の LogMonitor が
    同じ1つを持つ。

    中の dict は書き換えない。差し替えるときは新しい dict を作って属性へ代入する。
    clear() → update() で入れ替えると、その間に判定した窓が空のリスト（＝続行なし→自爆）を
    見てしまう。代入なら、読む側はいつも古いか新しいかのどちらか一方の完全なものを見る
    """

    def __init__(self, keep_on=None, wishes=None, participants=None, tabs=None):
        self.keep_on: dict = keep_on if keep_on is not None else {}
        # 参加者別の続行希望 {vrc_name: {round_key: set(ids)}}
        self.wishes: dict = wishes if wishes is not None else {}
        # wishes のうち参加者（＋主催者本人）の名前。None なら区別しない
        self.participants: set | None = participants
        # ToN ListTool の複窓対応のタブ {"version": n, "tabs": {タブ: {...}}}
        self.tabs: dict = tabs if tabs is not None else {"version": 0, "tabs": {}}


# ═══════════════════════════════════════════════
#  ラウンドタイプ → TNLキー
# ═══════════════════════════════════════════════
# ToN ListTool は Variant と Moon を、ラウンド別ではなくこの13枠にまとめて記録する（190/191/192/196〜199/312〜317）。
# ツールの中では、Mystic Moon・Blood Moon・Twilight・Solstice・Special・Cold Night の 6 つのラウンドが
# この 1 つのキー（Special/Moon）を使う。ListTool のキーとの行き来（読むとき配る・書くとき作り直す）は
# distribute_special_moon・listtool_moon_content
SPECIAL_MOON_KEY = "Special/Moon"
# ListTool にだけあるキー（Mystic Moon・Blood Moon）と、前の版のツールが持っていた Moon のキー（Twilight・Solstice）。
# ツールの中には持たない。読み込むときに Special/Moon へ寄せる
LISTTOOL_MOON_KEYS = ("Mystic Moon/ミスティックムーン", "Blood Moon/ブラッドムーン")
LEGACY_MOON_KEYS = LISTTOOL_MOON_KEYS + ("Twilight/トワイライト", "Solstice/ソルスティス")

LOG_TO_TNL = {
    "Classic":           "Classic/クラシック",
    "Classic.exe":       "Classic.exe/Classic.exe",
    "Randomizer":        "Randomizer/Randomizer",
    "8 Pages":           "8 Pages/8ページ",
    "Fog":               "Fog/霧",
    "Ghost":             "Ghost/ゴースト",
    "Punished":          "Punished/パニッシュ",
    "Sabotage":          "Sabotage/サボタージュ",
    "Bloodbath":         "Bloodbath/ブラッドバス",
    "Double Trouble":    "Double Trouble/ダブルトラブル",
    "Bloodbath EX":      "Bloodbath EX/ブラッドバスEX",
    "Cracked":           "Cracked/狂気",
    "Alternate":         "Alternate/オルタネイト",
    "Midnight":          "Midnight/ミッドナイト",
    "Unbound":           "Unbound/アンバウンド",
    "Run":               "Run/走れ！",
    "Mystic Moon":       SPECIAL_MOON_KEY,
    "Blood Moon":        SPECIAL_MOON_KEY,
    "Twilight":          SPECIAL_MOON_KEY,
    "Solstice":          SPECIAL_MOON_KEY,
    "Fog (Alternate)":   "Fog/霧",             # tnl照合は通常Fogスロット
    "Ghost (Alternate)": "Ghost/ゴースト",     # tnl照合は通常Ghostスロット
    "Sabotage star":     "Sabotage star/サボタージュスター",
    "Sabotage murder":   "Sabotage murder/サボタージュマーダー",
    "Special":           SPECIAL_MOON_KEY,
    "Cold Night":        SPECIAL_MOON_KEY,
}


# ═══════════════════════════════════════════════
#  ツールの枠（assets/data/continue_slots.json）
# ═══════════════════════════════════════════════
SLOTS_FILE = "assets/data/continue_slots.json"
CLASSIC_KEY = LOG_TO_TNL["Classic"]
UNBOUND_KEY = LOG_TO_TNL["Unbound"]


@dataclass(frozen=True)
class Slots:
    """ラウンドごとの枠（ツールの独自の番号）。そのラウンドに出ないテラーは外してある"""
    rounds: tuple          # ((キー, (番号, ...)), ...)。画面の並び。Special/Moon もここ
    by_key: dict           # {キー: frozenset(番号)}
    variants: dict         # {番号: frozenset(キー)}: 4000 番台のうち、ListTool のそのキーにも枠があるもの
    moon_keys: tuple       # ((ListTool のキー, (番号, ...)), ...): ListTool にだけあるキーの枠（書き出すときだけ使う）


_NO_SLOTS = Slots((), {}, {}, ())


@functools.lru_cache(maxsize=1)
def slots() -> Slots:
    """枠の表。読めなければ空（debug.log に例外）"""
    try:
        raw = json.loads(config.resource_path(SLOTS_FILE).read_text(encoding="utf-8"))
        rounds = tuple((str(key), tuple(int(i) for i in ids)) for key, ids in raw["rounds"])
        listtool = raw.get("listtool") or {}
        variants = {int(own): frozenset(str(k) for k in keys)
                    for own, keys in (listtool.get("variants") or {}).items()}
        moon_keys = tuple((str(key), tuple(int(i) for i in ids)) for key, ids in (listtool.get("moon_keys") or ()))
        return Slots(rounds, {key: frozenset(ids) for key, ids in rounds}, variants, moon_keys)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        DebugLog.exception("MatchTNL.slots")
        return _NO_SLOTS


def placements(own: int) -> tuple:
    """4000 番台のテラーが枠にあるキー（ツールの置き場所。Special/Moon も含む）"""
    if own < TerrorIds.FIXED_BASE:
        return ()
    return tuple(key for key, ids in slots().rounds if own in ids)


def listtool_has_slot(key: str, own: int) -> bool:
    """ListTool のそのキーにも、そのテラーの枠があるか（.tnl に書いてよいか）。4000 番台のうち、ListTool の
    ラウンドのキーに枠があるのは 192（Bloodthirsty）と Fog・Midnight の 315・316 だけ"""
    return own < TerrorIds.FIXED_BASE or key in slots().variants.get(own, ())


# ═══════════════════════════════════════════════
#  ListTool のリスト → ツールの独自の番号
# ═══════════════════════════════════════════════
def _moon_targets(source: str, own: int) -> tuple:
    """ListTool の Special/Moon（source）・Mystic Moon・Blood Moon の番号 1 つの行き先のキー。
    - Special/Moon: 4000 番台は置き場所のすべてのキーへ（Moon 4 体・Rift Monsters・Meatball Man は Special/Moon、
      Gigabytes は Special/Moon と Classic、Neo Pilot・Evil Purple Foxy は Alternate・Fog・Midnight、Glorbo は Punished）。
      Bloodthirsty（192）だけは Classic のみ（前の版が Special/Moon を見たのは Classic と Moon で、Moon に
      Bloodthirsty は出ない。各ラウンドの Bloodthirsty は各ラウンドのキー）
    - Mystic Moon・Blood Moon（と前の版の Twilight・Solstice）: Moon の中身なので Special/Moon だけ"""
    if source != SPECIAL_MOON_KEY:
        return (SPECIAL_MOON_KEY,) if own in slots().by_key.get(SPECIAL_MOON_KEY, ()) else ()
    if own < TerrorIds.FIXED_BASE:
        return ()
    if own == TerrorIds.BLOODTHIRSTY_CREATURE:
        return (CLASSIC_KEY,)
    return placements(own)


def distribute_special_moon(data: dict) -> dict:
    """ListTool（と前の版のツール）の Special/Moon・Mystic Moon・Blood Moon（と Twilight・Solstice）の中身を、
    ツールの置き場所へ配る。ほかのキーはそのまま。入力は独自の番号の {キー: 集合}。
    ツールの中のリスト（continue_list.json）には使わない——ツールの Special/Moon は ListTool の同じ名前のキーと
    中身が違う（ListTool のはツールの置き場所 4000 番台の寄せ集め）"""
    sources = (SPECIAL_MOON_KEY,) + LEGACY_MOON_KEYS
    out = {key: set(ids) for key, ids in data.items() if key not in sources}
    for source in sources:
        for own in data.get(source, ()):
            for target in _moon_targets(source, own):
                out.setdefault(target, set()).add(own)
    return {key: frozenset(ids) for key, ids in out.items() if ids}


def drop_not_appearing(data: dict, source: str = "", once: bool = False) -> dict:
    """そのキーの枠に無い番号（そのラウンドに出ないテラー）を外す。枠の表に無いキーはそのまま。
    外した件数は debug.log に 1 行（読み込みごと。once なら同じ件数の行は 1 回だけ）。空になったキーは持たない"""
    by_key = slots().by_key
    out: dict = {}
    dropped = 0
    for key, ids in data.items():
        allowed = by_key.get(key)
        kept = frozenset(ids) if allowed is None else frozenset(i for i in ids if i in allowed)
        dropped += len(ids) - len(kept)
        if kept:
            out[key] = kept
    if dropped:
        message = f"{source or 'リスト'} の、そのラウンドに出ないテラー {dropped} 件を外しました"
        if once:
            TerrorIds._report_once("dropped", (source, dropped), message)
        else:
            DebugLog.write(f"[続行リスト] {message}")
    return out


def own_list_from_listtool(data: dict, source: str = "", once: bool = False) -> dict:
    """ListTool の {キー: ListTool の番号の集合} → ツールの {キー: frozenset(独自の番号)}。
    ListTool の番号を独自の番号に直し（8 Pages のキーは 8 Pages の索引・表に無い番号は捨てる）、
    Special/Moon・Mystic Moon・Blood Moon の中身を置き場所へ配り、そのラウンドに出ないテラーを外す"""
    own = TerrorIds.list_from_listtool(data, source, once=once)
    return drop_not_appearing(distribute_special_moon(own), source, once=once)


def listtool_moon_content(data: dict) -> dict:
    """ツールのリストから、ListTool の Special/Moon・Mystic Moon・Blood Moon に書く中身（独自の番号）を作る:
    ツールの Special/Moon の中身と、ほかのキーに置いてある 4000 番台（どこか 1 つでも入っていれば。Bloodthirsty は
    Classic に入っているときだけ）のうち、そのキーの ListTool の枠にあるもの"""
    mine = set(data.get(SPECIAL_MOON_KEY, ()))
    for key, ids in data.items():
        if key == SPECIAL_MOON_KEY:
            continue
        for own in ids:
            if own >= TerrorIds.FIXED_BASE and key in placements(own) and own not in mine:
                if own != TerrorIds.BLOODTHIRSTY_CREATURE or key == CLASSIC_KEY:
                    mine.add(own)
    return {key: {own for own in ids if own in mine} for key, ids in slots().moon_keys}


# ═══════════════════════════════════════════════
#  TNLデータ
# ═══════════════════════════════════════════════
def _listtool_ids(slot_values) -> set:
    """tnl の 1 ラウンドの {"番号": 0/1} → ON の ListTool の番号の集合"""
    return {int(k) for k, v in slot_values.items() if isinstance(v, int) and v != 0}


def load_tnl(path: str) -> tuple[dict[str, set[int]], dict]:
    """.tnl を読む。番号は独自の番号に直す（own_list_from_listtool）"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data", {})
    listed: dict[str, set[int]] = {}
    for round_key, slot_values in data.items():
        round_key = HOST_SAVE_KEY_ALIASES.get(round_key, round_key)
        if not isinstance(slot_values, dict) or round_key in HOST_SAVE_IGNORED_KEYS:
            continue
        ids = _listtool_ids(slot_values)
        if ids:
            listed[round_key] = ids
    keepOn_set = {key: set(ids) for key, ids in own_list_from_listtool(listed, ".tnl").items()}
    meta = {k: raw.get(k, "") for k in ("list_name", "creator", "created_at")}
    return keepOn_set, meta


# host_save 側にだけ存在するラウンドキー。ログ側は LOG_TO_TNL で通常の
# Fog/Ghost に寄せているので、ここで畳むと既存の判定が変わる。無視する。
# tnl の読み込みでも同じ（FV。件数・ラウンド数を主催リストと合わせる）。
# 2026-11-14 に通常の Fog/Ghost へ畳む形を試したが、主催リストの実データでは
# このキーの中身は全員同じ「Classic 全部 ON」の固定の塊で、希望が入っていなかった
# （ListTool が機械的に書いている）ため戻した。オルタネイトの希望は Fog/霧 のタブ（134〜169）に入っている。
HOST_SAVE_IGNORED_KEYS = frozenset({
    "Fog (Alternate)/霧 (Alternate)",
    "Ghost (Alternate)/ゴースト (Alternate)",
})


# 2.21.3 の wish_bits はラウンド名の日本語側が変わっているものがある。
# 枠の表（continue_slots.json）は従来のキーなので、読むときに寄せる
HOST_SAVE_KEY_ALIASES = {
    "Randomizer/ランダマイザー": "Randomizer/Randomizer",
}


def _fold_wishes(data, keepOn_set: dict, mine: dict | None):
    """1人ぶんの `data` を続行リストへ畳み込む。participants も主催者も同じ扱い。番号は独自の番号に直す"""
    if not isinstance(data, dict):
        return
    listed = {}
    for round_key, slot_values in data.items():
        round_key = HOST_SAVE_KEY_ALIASES.get(round_key, round_key)
        if not isinstance(slot_values, dict) or round_key in HOST_SAVE_IGNORED_KEYS:
            continue
        ids = _listtool_ids(slot_values)
        if ids:
            listed[round_key] = ids
    for round_key, ids in own_list_from_listtool(listed, "主催者のリスト", once=True).items():
        keepOn_set.setdefault(round_key, set()).update(ids)
        if mine is not None:
            # 同名が複数タブにいることがある。畳んで持つ
            mine.setdefault(round_key, set()).update(ids)


HOST_STATE_SIDECARS = ("-wal",)     # -shm は写さない（写した -wal から SQLite が作り直す）
HOST_STATE_COPY_ATTEMPTS = 3        # 写している最中に元が変わったときの、写しの回数の上限
PARTICIPANT_SECTION = 0        # 1 は待機


@functools.lru_cache(maxsize=16384)
def _wish_ids_cached(bits: bytes) -> frozenset:
    return frozenset(i * 8 + j for i, byte in enumerate(bits) for j in range(8) if byte >> j & 1)


def _wish_ids(bits) -> frozenset:
    """ビット列をテラーIDの集合へ。下位ビットから順に id = i*8 + j。同じビット列は覚えた結果（FT）"""
    if not isinstance(bits, (bytes, bytearray)):
        return frozenset()
    return _wish_ids_cached(bytes(bits))


def _source_stamp(path: str):
    """元ファイルの (大きさ, 更新時刻ns)。無ければ None（-wal の有無も見分ける）"""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_size, st.st_mtime_ns


def _copy_host_state(path: str, copy: Path) -> None:
    """本体と -wal を copy の隣へ写す。写す前後で元の stat が変わったら写し直す。
    HOST_STATE_COPY_ATTEMPTS 回とも変わったら例外（呼び出し側が次の周期で再試行する）"""
    sources = [(str(path), str(copy))]
    sources += [(str(path) + suffix, str(copy) + suffix) for suffix in HOST_STATE_SIDECARS]
    for _attempt in range(HOST_STATE_COPY_ATTEMPTS):
        before = [_source_stamp(src) for src, _dst in sources]
        changed = False
        for (src, dst), stamp in zip(sources, before):
            Path(dst).unlink(missing_ok=True)       # 前の回の写しを残さない
            if stamp is None and dst != str(copy):
                continue                            # -wal が無い
            try:
                shutil.copy2(src, dst)
            except FileNotFoundError:
                if dst == str(copy):
                    raise                           # 本体が無いのは今までどおり例外
                changed = True                      # -wal が写す間に消えた（checkpoint）
        if not changed and before == [_source_stamp(src) for src, _dst in sources]:
            return
    raise sqlite3.OperationalError(
        f"主催リストを写している間に元が変わり続けました（{HOST_STATE_COPY_ATTEMPTS} 回）")


def load_host_state(path: str,
                    user_save_path: str | None = None,
                    user_save_raw: bytes | None = None
                    ) -> tuple[dict[str, set[int]], dict, dict]:
    """ToN ListTool の主催リスト（SQLite 版）を keepOn_set の形で読む。

    section は 0 が参加者・1 が待機。畳んだ keepOn_set には参加者だけを足し、
    待機は名前ごとの wishes にだけ入れる（以前の JSON 版と同じ方針）。

    ToN ListTool の複窓対応では、窓ごとに別のタブへ参加者が振り分けられ、
    タブごとに続行リストの中身が違う。畳んだものだけだと別の窓の希望が混ざる
    ので、タブごとの内訳も `meta["tabs_data"]` に入れて返す（畳んだ戻り値は
    そのまま。呼び出し側は対応づけできたときだけ内訳を使う）。

    ListTool が書いている最中を掴まないよう、本体と -wal（あれば）だけを一時フォルダへ
    写してから読む。-shm は写さない（別の瞬間に写した -shm は写した -wal と食い違い、
    WAL の新しい分が見えなくなる。-shm は WAL の索引なので、写した -wal から SQLite が
    作り直す）。そのため写しは読み書きで開くが、書くのは一時フォルダの写しだけで、
    SQL は select だけ。ListTool のフォルダには一切書かず、元のファイルを sqlite では
    開かない。写している間に元の本体か -wal の大きさ・更新時刻が変わったら写し直し
    （HOST_STATE_COPY_ATTEMPTS 回まで）。それでも変わり続けた・壊れている・途中だった
    場合は例外を投げる（呼び出し側の再試行に任せる。以前の JSON 版と同じ）。
    """
    with tempfile.TemporaryDirectory() as work:
        copy = Path(work) / "host_state.sqlite3"
        _copy_host_state(path, copy)
        con = sqlite3.connect(str(copy))
        try:
            members = con.execute(
                "select id, tab_index, section, vrc_name from participants").fetchall()
            rows = con.execute(
                "select participant_id, round_name, bits from wishes").fetchall()
            tabs = con.execute("select count(*) from tabs").fetchone()[0]
        finally:
            con.close()

    keepOn_set: dict[str, set[int]] = {}
    wishes: dict[str, dict[str, set[int]]] = {}
    by_id = {pid: (tab, section, name) for pid, tab, section, name in members}
    participants = 0
    participant_names: set = set()
    tabs_data: dict = {}
    for _pid, tab, section, name in members:
        data = tabs_data.setdefault(tab, {"participants": set(), "waiting": set(),
                                          "keepOn": {}, "wishes": {}})
        if section == PARTICIPANT_SECTION:
            participants += 1
            if isinstance(name, str) and name:
                participant_names.add(name)
                data["participants"].add(name)
        elif isinstance(name, str) and name:
            data["waiting"].add(name)

    listed_ids = set()
    # 人ごとの ListTool の番号 {pid: {ラウンドのキー: 番号の集合}}。Special/Moon の配り方は人ごとの中身で決まるので、
    # 人ごとにまとめてから独自の番号に直す
    per_person: dict = {}
    for pid, round_key, bits in rows:
        tab, section, name = by_id.get(pid, (None, None, None))
        if section is None:
            continue
        # 行が1つでもあれば「リストを持っている人」。全部OFFでも {} で残すのは
        # 以前の JSON 版と同じ（「続行したいものが無い」と「リストが無い」を分ける）
        listed_ids.add(pid)
        if isinstance(name, str) and name:
            wishes.setdefault(name, {})
        round_key = HOST_SAVE_KEY_ALIASES.get(round_key, round_key)
        if round_key in HOST_SAVE_IGNORED_KEYS:
            continue
        ids = _wish_ids(bits)
        if ids:
            per_person.setdefault(pid, {}).setdefault(round_key, set()).update(ids)
    for pid, listtool_ids in per_person.items():
        tab, section, name = by_id[pid]
        mine = wishes.get(name) if isinstance(name, str) and name else None
        tab_data = tabs_data.get(tab)
        _fold_person_wishes(listtool_ids, section, name, mine, tab_data, keepOn_set)

    return _finish_host_state(keepOn_set, wishes, tabs_data, participants, participant_names,
                              len(listed_ids), tabs, user_save_path, user_save_raw)


def _fold_person_wishes(listtool_ids: dict, section, name, mine, tab_data, keepOn_set: dict):
    """1 人ぶんの {キー: ListTool の番号の集合} を独自の番号に直し、keepOn_set・その人の希望・タブの内訳へ足す"""
    # 数秒ごとに読むので、捨てた番号・外した件数の行は同じものを何度も書かない（once）
    for round_key, ids in own_list_from_listtool(listtool_ids, "主催リスト", once=True).items():
        if section == PARTICIPANT_SECTION:
            keepOn_set.setdefault(round_key, set()).update(ids)
            if tab_data is not None:
                tab_data["keepOn"].setdefault(round_key, set()).update(ids)
        if mine is not None:
            # 同名が複数タブにいることがある。畳んで持つ
            mine.setdefault(round_key, set()).update(ids)
        if tab_data is not None and isinstance(name, str) and name:
            tab_data["wishes"].setdefault(name, {}).setdefault(
                round_key, set()).update(ids)


def _finish_host_state(keepOn_set, wishes, tabs_data, participants, participant_names,
                       listed, tabs, user_save_path, user_save_raw):
    """主催者自身のリストを足し、meta を組む（SQLite 版・新形式で共通）"""
    host_self = None
    if user_save_path:
        host_self = _load_host_own_list(user_save_path, keepOn_set, wishes, raw=user_save_raw)
    if host_self:
        participant_names.add(host_self)
        # 主催者はどのタブの窓にいても自分のリストで判定する。タブごとの
        # 内訳にも足しておく（meta["participants"] の人数には数えない。
        # ListTool の保存ファイルのタブの参加者には主催者が入らないので、窓の
        # 「一致 N/M人」が 1 人少なく出ていた。数え終えたあとに足す）。
        # 全タブに足すので、tab_for_window は host_self を数えない
        own = wishes.get(host_self) or {}
        for data in tabs_data.values():
            data["participants"].add(host_self)
            data["host_self"] = host_self
            data["wishes"].setdefault(host_self, {})
            for round_key, ids in own.items():
                data["keepOn"].setdefault(round_key, set()).update(ids)
                data["wishes"][host_self].setdefault(round_key, set()).update(ids)
    # listed は「続行リストを持っている人」。ListTool は全部OFFの行を書かないので、
    # 行が1件でもある人を数える（JSON 版の「data を持つ人」と同じ意味）
    meta = {"participants": participants,   # 自分は数えない
            "listed": listed,
            "participant_names": participant_names,
            "tabs": tabs,
            # 窓ごとの対応づけに使うタブの内訳。
            # {tab_index: {"participants": {名前}, "waiting": {名前},
            #              "keepOn": {ラウンド: {ID}}, "wishes": {名前: {...}}}}
            "tabs_data": tabs_data,
            "host_self": host_self}
    return keepOn_set, meta, wishes


CRYPTPROTECT_UI_FORBIDDEN = 0x1
_DPAPI_GUID = bytes.fromhex("d08c9ddf0115d1118c7a00c04fc297eb")
LOCAL_STATE_WISH_BYTES = 40         # 320 ビット（sqlite の wishes.bits と同じ並び）


def _dpapi_unprotect(blob: bytes) -> bytes:
    """DPAPI の暗号文を復号する。テストはこれを差し替える（テストから DPAPI は呼ばない）"""
    import win32crypt
    _description, data = win32crypt.CryptUnprotectData(
        blob, None, None, None, CRYPTPROTECT_UI_FORBIDDEN)
    return data


def _unseal_local_state(raw: bytes) -> dict:
    """"LTHS" + 版 + DPAPI → gzip → JSON。形が違えば ValueError。
    DPAPI の暗号文は、プロバイダ GUID の 4 バイト前（dwVersion）から始まる。
    ヘッダが伸びても壊れないよう、固定の位置ではなく GUID を探す"""
    if not raw.startswith(config.HOST_LOCAL_STATE_MAGIC):
        raise ValueError("host_local_state: 先頭の印が違います")
    pos = raw.find(_DPAPI_GUID)
    if pos < 4:
        raise ValueError("host_local_state: DPAPI の暗号文が見つかりません")
    try:
        data = json.loads(gzip.decompress(_dpapi_unprotect(raw[pos - 4:])))
    except Exception as e:
        # 中身・パス・名前はメッセージに入れない（型名だけ）
        raise ValueError(f"host_local_state を開けません（{type(e).__name__}）") from None
    if not isinstance(data, dict):
        raise ValueError("host_local_state: 中身が辞書ではありません")
    return data


def _wish_bits_to_bytes(text: str) -> bytes:
    """"xxxx:xxxx:…" → 40 バイト。要素が 16 桁の 16 進 5 個でなければ ValueError。
    ":" 区切りの各要素は 64 ビット整数（上位桁から）なので、8 バイトごとに逆順にする。
    sqlite の wishes.bits は下位バイトから並んでいたので、並びが逆"""
    if not isinstance(text, str):
        raise ValueError("wish_bits が文字列ではありません")
    parts = text.split(":")
    if len(parts) != LOCAL_STATE_WISH_BYTES // 8:
        raise ValueError(f"wish_bits の要素数が違います（{len(parts)} 個）")
    out = bytearray()
    for part in parts:
        if len(part) != 16:
            raise ValueError(f"wish_bits の要素の桁数が違います（{len(part)} 桁）")
        try:
            word = bytes.fromhex(part)
        except ValueError:
            raise ValueError("wish_bits が 16 進ではありません") from None
        out += word[::-1]
    return bytes(out)


def load_host_local_state(path: str,
                          user_save_path: str | None = None,
                          user_save_raw: bytes | None = None
                          ) -> tuple[dict[str, set[int]], dict, dict]:
    """ToN ListTool 2.21.3 以降の主催リスト（host_local_state.bin）を読む。戻り値は load_host_state と同じ形。

    ListTool は一時ファイルへ書いて os.replace で差し替えるので、一気に読んで閉じる
    （一時フォルダへ写さない）。ListTool のフォルダには書かない。
    各人の wish_bits が 40 バイトにならない行は、その行だけ捨てる（debug.log に件数を 1 回）。
    壊れた容れ物（印・版・DPAPI・gzip・JSON）は ValueError（呼び出し側が猶予のあと .tnl へ）"""
    with open(path, "rb") as f:
        raw = f.read()
    data = _unseal_local_state(raw)
    tab_list = data.get("tabs")
    if not isinstance(tab_list, list):
        raise ValueError("host_local_state: tabs がありません")

    keepOn_set: dict[str, set[int]] = {}
    wishes: dict[str, dict[str, set[int]]] = {}
    participants = 0
    participant_names: set = set()
    tabs_data: dict = {}
    listed = 0
    dropped = 0
    for tab_index, tab in enumerate(tab_list):
        if not isinstance(tab, dict):
            continue
        for section, key in ((PARTICIPANT_SECTION, "participants"), (1, "waiting")):
            members = tab.get(key)
            for member in members if isinstance(members, list) else ():
                if not isinstance(member, dict):
                    continue
                tab_data = tabs_data.setdefault(tab_index, {"participants": set(), "waiting": set(),
                                                            "keepOn": {}, "wishes": {}})
                name = member.get("vrc_name")
                has_name = isinstance(name, str) and bool(name)
                if section == PARTICIPANT_SECTION:
                    participants += 1
                    if has_name:
                        participant_names.add(name)
                        tab_data["participants"].add(name)
                elif has_name:
                    tab_data["waiting"].add(name)
                bits_by_round = member.get("wish_bits")
                if not isinstance(bits_by_round, dict):
                    continue
                person: dict = {}
                rows = 0
                for round_key, text in bits_by_round.items():
                    try:
                        bits = _wish_bits_to_bytes(text)
                    except ValueError:
                        dropped += 1
                        continue
                    rows += 1
                    round_key = HOST_SAVE_KEY_ALIASES.get(round_key, round_key)
                    if round_key in HOST_SAVE_IGNORED_KEYS:
                        continue
                    ids = _wish_ids(bits)
                    if ids:
                        person.setdefault(round_key, set()).update(ids)
                if not rows:
                    continue            # 読めた行が 1 つも無い人は「リストを持つ人」に数えない
                # 行が 1 つでもあれば「リストを持っている人」（全部 OFF でも {} で残す。sqlite 版と同じ）
                listed += 1
                if has_name:
                    wishes.setdefault(name, {})
                mine = wishes.get(name) if has_name else None
                _fold_person_wishes(person, section, name, mine, tab_data, keepOn_set)
    if dropped:
        TerrorIds._report_once("host_local_bits", dropped,
                               f"[主催リスト] 読めない wish_bits を {dropped} 件捨てました")

    return _finish_host_state(keepOn_set, wishes, tabs_data, participants, participant_names,
                              listed, len(tab_list), user_save_path, user_save_raw)


def _tab_overlaps(tabs_data: dict, present_names) -> list:
    """[(タブの index, 重なった人数)] をタブ順で返す。

    主催者自身（`data["host_self"]`）は全タブに足してあるので数えない
    """
    present = set(present_names or ())
    return [(tab, len(present & (set(data.get("participants") or ()) - {data.get("host_self")})))
            for tab, data in sorted((tabs_data or {}).items(), key=lambda kv: str(kv[0]))]


def tab_match_detail(tabs_data: dict, present_names) -> str:
    """対応づけに失敗した理由を、debug.log 用の 1 行にする（数字だけ）。

    名前は出さない（タブ番号と人数だけ）。タブ番号は「タブ1 に対応」と同じく
    1 始まり。最大の重なりが 0 人なら「重なり最大0人」、複数のタブが同じ最大で
    並ぶなら「タブ2・タブ5 が3人で同数」
    """
    present = set(present_names or ())
    overlaps = _tab_overlaps(tabs_data, present)
    head = f"タブ{len(overlaps)}個 / 在室{len(present)}人"
    best = max((n for _tab, n in overlaps), default=0)
    if best <= 0:
        return f"{head} / 重なり最大0人"
    tied = sorted(tab for tab, n in overlaps if n == best)
    return f"{head} / " + "・".join(f"タブ{tab + 1}" for tab in tied) + f" が{best}人で同数"


def tab_for_window(tabs_data: dict, present_names) -> int | None:
    """その窓にいる人の名前から、対応するタブを選ぶ。

    ListTool は窓とタブの対応をどこにも記録しないので、参加者の名前の重なりで
    決める。重なりが最大のタブ。1人も重ならない・同点が複数あるときは None
    （対応づけできない＝呼び出し側は全タブを畳んだ共有リストへ落とす）。
    待機の名前では選ばない——待機はその場にいない人なので、別の窓の人が
    混ざっている。主催者自身（`data["host_self"]`）は全タブに足してあるので
    数えない（どのタブとも重なるだけで、選び方を変えてしまう）
    """
    present = set(present_names or ())
    if not present or not tabs_data:
        return None
    best, best_overlap, tied = None, 0, False
    for tab, overlap in _tab_overlaps(tabs_data, present):
        if overlap > best_overlap:
            best, best_overlap, tied = tab, overlap, False
        elif overlap == best_overlap and overlap > 0:
            tied = True
    # best は重なりが1人以上あるタブでしか埋まらない（重なり0なら None のまま）
    return None if tied else best


def _load_host_own_list(path: str, keepOn_set: dict, wishes: dict,
                        raw: bytes | None = None) -> str | None:
    """このPCのアカウントの続行リストを足す。last_active の名前を返す。

    ToN ListTool の GUI に「主催者のリストを表示に含める」があるが、保存
    ファイルの participants には主催者が入らない。設定の在り処が分からない
    ので見ない——周回に参加しているなら常に足すのが正しい。

    希望（wishes）には**全アカウント**を名前ごとに入れる。窓ごとにアカウントが
    違う（サブ垢でソロを回す窓がある）ので、窓は自分のアカウント名で引く。
    畳んだ keepOn_set には従来どおり last_active の分だけを足す（GUI の件数表示用）。

    ここは「足せたら足す」だけ。ファイルが無い・壊れている、のいずれでも
    例外を投げずに諦める。

    raw（呼び出し側が読んだバイト列）が渡されたら、それを解析してファイルは開かない。
    無ければ開いて f.read() で一気に読み、閉じてから解析する（ToN ListTool の保存と重ならないよう、
    掴む時間と回数を減らす: GU）。
    """
    try:
        if raw is None:
            with open(path, "rb") as f:
                raw = f.read()
        raw = json.loads(raw)
        accounts = raw.get("accounts") or {}
        active = raw.get("last_active")
        if not isinstance(accounts, dict):
            return None
    except Exception:
        return None

    loaded = set()
    for name, account in accounts.items():
        if not isinstance(name, str) or not name or not isinstance(account, dict):
            continue
        data = account.get("data")
        if not isinstance(data, dict):
            continue
        # 畳んだ一覧に足すのは last_active だけ。他のアカウントは希望だけ
        target = keepOn_set if name == active else {}
        # 全部 OFF でも {} で残す。「リストがあって続行したいものが無い」
        # （＝全部自爆）と「リストが無い」（＝分からないので止める）を分けるため。
        # 周回の参加者も同じ扱い（load_host_state）
        _fold_wishes(data, target, wishes.setdefault(name, {}))
        loaded.add(name)
    return active if active in loaded else None


def should_continue(keepOn_set: dict, tnl_key: str, terror_ids: list[int]) -> bool:
    if tnl_key not in keepOn_set:
        return False
    return any(t in keepOn_set[tnl_key] for t in terror_ids)

def parse_terror_ids(a: str, b: str, c: str, round_type: str = "") -> list[int]:
    ids = [int(x) for x in (a, b, c)]
    if round_type in ("Midnight", "Bloodbath"):
        return ids[:3]
    elif round_type == "Double Trouble":
        return ids[:2]
    elif round_type == "8 Pages":
        # A だけでテラーが決まる。B は別物（マップIDでもない）
        return ids[:1]
    else:
        return ids[:1]
