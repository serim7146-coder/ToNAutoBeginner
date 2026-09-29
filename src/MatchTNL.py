import json
import shutil
import sqlite3
import tempfile
from pathlib import Path


# ═══════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════
# Alternate枠テラーのオフセット設定
# ログID 0〜35 のテラーは Alternate枠 → +134 してtnlスロットIDに変換する
# tnlのAlternateスロットは134〜169として登録されているため
ALTERNATE_OFFSET  = 134
ALTERNATE_LOG_MAX = 35   # ログIDが0〜35 = Alternate枠テラー

# Unboundラウンドのオフセット: ログID + 200 = tnlスロットID
UNBOUND_OFFSET = 200

ALTERNATE_SLOT_POSITIONS: dict[str, list[int] | None] = {
    "Alternate":           None,  # 全枠Alternate → +134
    "Midnight":            [2],   # 3枠目のみAlternate → +134
    "Fog (Alternate)":     None,  # 全枠Alternate → +134、tnl照合はFog
    "Ghost (Alternate)":   None,  # 全枠Alternate → +134、tnl照合はGhost
    # 通常の Fog/Ghost/8Pages はClassicテラーのみ → オフセット不要
}

# April Fool期間中の特例: Alternate ID→tnlID の特別マッピング
# キー=ログに出るID(オフセット適用後), 値=tnlでの実際のID
# ALTERNATE_ID_OVERRIDE: dict[int, int] = {
#     136: 316,   # April Fool: Alternate ログID2(+134=136) → tnl316
# }


# ═══════════════════════════════════════════════
#  ラウンドタイプ → TNLキー
# ═══════════════════════════════════════════════
# ToN ListTool は Variant と Moon を、ラウンド別ではなくこの13枠に
# まとめて記録する（190/191/192/196〜199/312〜317）。ラウンド別のキーは
# 空のままなので、ここも見ないと Variant と Moon の続行指定が空振りする。
SPECIAL_MOON_KEY = "Special/Moon"

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
    "Mystic Moon":       "Mystic Moon/ミスティックムーン",
    "Blood Moon":        "Blood Moon/ブラッドムーン",
    "Twilight":          "Twilight/トワイライト",
    "Solstice":          "Solstice/ソルスティス",
    "Fog (Alternate)":   "Fog/霧",             # tnl照合は通常Fogスロット
    "Ghost (Alternate)": "Ghost/ゴースト",     # tnl照合は通常Ghostスロット
    "Sabotage star":     "Sabotage star/サボタージュスター",
    "Sabotage murder":   "Sabotage murder/サボタージュマーダー",
    "Special":           SPECIAL_MOON_KEY,
    "Moon":              SPECIAL_MOON_KEY,
}


# ═══════════════════════════════════════════════
#  TNLデータ
# ═══════════════════════════════════════════════
def load_tnl(path: str) -> tuple[dict[str, set[int]], dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data", {})
    keepOn_set: dict[str, set[int]] = {}
    for round_key, slots in data.items():
        if not isinstance(slots, dict):
            continue
        ids = {int(k) for k, v in slots.items() if isinstance(v, int) and v != 0}
        if ids:
            keepOn_set[round_key] = ids
    meta = {k: raw.get(k, "") for k in ("list_name", "creator", "created_at")}
    return keepOn_set, meta


# host_save 側にだけ存在するラウンドキー。ログ側は LOG_TO_TNL で通常の
# Fog/Ghost に寄せているので、ここで畳むと既存の判定が変わる。無視する。
HOST_SAVE_IGNORED_KEYS = frozenset({
    "Fog (Alternate)/霧 (Alternate)",
    "Ghost (Alternate)/ゴースト (Alternate)",
})


def _fold_wishes(data, keepOn_set: dict, mine: dict | None):
    """1人ぶんの `data` を続行リストへ畳み込む。participants も主催者も同じ扱い"""
    if not isinstance(data, dict):
        return
    for round_key, slots in data.items():
        if not isinstance(slots, dict) or round_key in HOST_SAVE_IGNORED_KEYS:
            continue
        ids = {int(k) for k, v in slots.items()
               if isinstance(v, int) and v != 0}
        if ids:
            keepOn_set.setdefault(round_key, set()).update(ids)
            if mine is not None:
                # 同名が複数タブにいることがある。畳んで持つ
                mine.setdefault(round_key, set()).update(ids)


HOST_STATE_SIDECARS = ("-wal", "-shm")
PARTICIPANT_SECTION = 0        # 1 は待機


def _wish_ids(bits) -> set:
    """ビット列をテラーIDの集合へ。下位ビットから順に id = i*8 + j"""
    if not isinstance(bits, (bytes, bytearray)):
        return set()
    return {i * 8 + j for i, byte in enumerate(bits)
            for j in range(8) if byte >> j & 1}


def load_host_state(path: str,
                    user_save_path: str | None = None
                    ) -> tuple[dict[str, set[int]], dict, dict]:
    """ToN ListTool の主催リスト（SQLite 版）を keepOn_set の形で読む。

    section は 0 が参加者・1 が待機。畳んだ keepOn_set には参加者だけを足し、
    待機は名前ごとの wishes にだけ入れる（以前の JSON 版と同じ方針）。

    ToN ListTool の複窓対応では、窓ごとに別のタブへ参加者が振り分けられ、
    タブごとに続行リストの中身が違う。畳んだものだけだと別の窓の希望が混ざる
    ので、タブごとの内訳も `meta["tabs_data"]` に入れて返す（畳んだ戻り値は
    そのまま。呼び出し側は対応づけできたときだけ内訳を使う）。

    ListTool が書いている最中を掴まないよう、本体と -wal / -shm を一時フォルダへ
    写してから読む。ListTool のフォルダには一切書かない。壊れている・途中だった
    場合は例外を投げる（呼び出し側の再試行に任せる。以前の JSON 版と同じ）。
    """
    with tempfile.TemporaryDirectory() as work:
        copy = Path(work) / "host_state.sqlite3"
        shutil.copy2(path, copy)
        for suffix in HOST_STATE_SIDECARS:
            side = Path(str(path) + suffix)
            if side.exists():
                shutil.copy2(side, str(copy) + suffix)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
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
    for pid, round_key, bits in rows:
        tab, section, name = by_id.get(pid, (None, None, None))
        if section is None:
            continue
        # 行が1つでもあれば「リストを持っている人」。全部OFFでも {} で残すのは
        # 以前の JSON 版と同じ（「続行したいものが無い」と「リストが無い」を分ける）
        listed_ids.add(pid)
        mine = (wishes.setdefault(name, {})
                if isinstance(name, str) and name else None)
        if round_key in HOST_SAVE_IGNORED_KEYS:
            continue
        ids = _wish_ids(bits)
        if not ids:
            continue
        tab_data = tabs_data.get(tab)
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

    host_self = None
    if user_save_path:
        host_self = _load_host_own_list(user_save_path, keepOn_set, wishes)
    if host_self:
        participant_names.add(host_self)
        # 主催者はどのタブの窓にいても自分のリストで判定する。タブごとの
        # 内訳にも足しておく（参加者の人数には数えない）
        own = wishes.get(host_self) or {}
        for data in tabs_data.values():
            data["wishes"].setdefault(host_self, {})
            for round_key, ids in own.items():
                data["keepOn"].setdefault(round_key, set()).update(ids)
                data["wishes"][host_self].setdefault(round_key, set()).update(ids)
    # listed は「続行リストを持っている人」。ListTool は全部OFFの行を書かないので、
    # 行が1件でもある人を数える（JSON 版の「data を持つ人」と同じ意味）
    meta = {"participants": participants,   # 自分は数えない
            "listed": len(listed_ids),
            "participant_names": participant_names,
            "tabs": tabs,
            # 窓ごとの対応づけに使うタブの内訳。
            # {tab_index: {"participants": {名前}, "waiting": {名前},
            #              "keepOn": {ラウンド: {ID}}, "wishes": {名前: {...}}}}
            "tabs_data": tabs_data,
            "host_self": host_self}
    return keepOn_set, meta, wishes


def tab_for_window(tabs_data: dict, present_names) -> int | None:
    """その窓にいる人の名前から、対応するタブを選ぶ。

    ListTool は窓とタブの対応をどこにも記録しないので、参加者の名前の重なりで
    決める。重なりが最大のタブ。1人も重ならない・同点が複数あるときは None
    （対応づけできない＝呼び出し側は全タブを畳んだ共有リストへ落とす）。
    待機の名前では選ばない——待機はその場にいない人なので、別の窓の人が
    混ざっている
    """
    present = set(present_names or ())
    if not present or not tabs_data:
        return None
    best, best_overlap, tied = None, 0, False
    for tab, data in sorted(tabs_data.items(), key=lambda kv: str(kv[0])):
        overlap = len(present & set(data.get("participants") or ()))
        if overlap > best_overlap:
            best, best_overlap, tied = tab, overlap, False
        elif overlap == best_overlap and overlap > 0:
            tied = True
    # best は重なりが1人以上あるタブでしか埋まらない（重なり0なら None のまま）
    return None if tied else best


def _load_host_own_list(path: str, keepOn_set: dict, wishes: dict) -> str | None:
    """このPCのアカウントの続行リストを足す。last_active の名前を返す。

    ToN ListTool の GUI に「主催者のリストを表示に含める」があるが、保存
    ファイルの participants には主催者が入らない。設定の在り処が分からない
    ので見ない——周回に参加しているなら常に足すのが正しい。

    希望（wishes）には**全アカウント**を名前ごとに入れる。窓ごとにアカウントが
    違う（サブ垢でソロを回す窓がある）ので、窓は自分のアカウント名で引く。
    畳んだ keepOn_set には従来どおり last_active の分だけを足す（GUI の件数表示用）。

    ここは「足せたら足す」だけ。ファイルが無い・壊れている、のいずれでも
    例外を投げずに諦める。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
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

def apply_alternate_offset(ids: list[int], round_type: str) -> list[int]:
    """
    Alternate枠テラーのログID（0〜35）を +134 してtnlスロットIDに変換する。
    round_type = ログの "Round type is XXX" の XXX 部分
    - Midnight: 3枠目（index=2）のみAlternate
    - Alternate/Fog(Alternate)/Ghost(Alternate)/8Pages(Alternate): 全枠Alternate
    - その他（通常8Pages/Fog/Ghost等）: オフセットなし
    """
    if round_type not in ALTERNATE_SLOT_POSITIONS:
        return ids
    positions = ALTERNATE_SLOT_POSITIONS[round_type]  # None=全枠, list=指定枠のみ
    result = []
    for i, tid in enumerate(ids):
        is_alt_slot = (positions is None) or (i in positions)
        if is_alt_slot and 0 <= tid <= ALTERNATE_LOG_MAX:
            converted = tid + ALTERNATE_OFFSET
            # April Fool等の特例マッピング
            # result.append(ALTERNATE_ID_OVERRIDE.get(converted, converted))
            result.append(converted)
        else:
            result.append(tid)
    return result