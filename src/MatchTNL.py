import gzip
import json


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
    "Special":           "Special/Moon",
    "Moon":              "Special/Moon",
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


def _load_host_own_list(path: str, keepOn_set: dict, wishes: dict) -> str | None:
    """主催者自身の続行リストを足す。足せた名前を返す（足せなければ None）。

    ToN ListTool の GUI に「主催者のリストを表示に含める」があるが、保存
    ファイルの participants には主催者が入らない。設定の在り処が分からない
    ので見ない——周回に参加しているなら常に足すのが正しい。

    ここは「足せたら足す」だけ。ファイルが無い・壊れている・last_active が
    accounts に無い、のいずれでも例外を投げずに諦める。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        name = raw.get("last_active")
        account = (raw.get("accounts") or {}).get(name)
        if not isinstance(name, str) or not name or not isinstance(account, dict):
            return None
        data = account.get("data")
        if not isinstance(data, dict):
            return None
    except Exception:
        return None

    _fold_wishes(data, keepOn_set, wishes.setdefault(name, {}))
    if not wishes[name]:
        wishes.pop(name, None)
    return name


def load_host_save(path: str,
                   user_save_path: str | None = None
                   ) -> tuple[dict[str, set[int]], dict, dict]:
    """ToN ListTool の主催リストを keepOn_set の形で読む。

    全タブの participants の続行希望を OR で畳む。waiting（待機列）は
    含めない——その場にいない人のために生き残ってしまうため。
    active_tab は見ない（UIの選択状態で判定が変わらないように）。

    3つ目に参加者別の希望 `{vrc_name: {round_key: set(ids)}}` も返す。
    畳んだ `keepOn_set` では「誰の希望か」が消えるため、Sabotage の
    マーダー判定には使えないため。

    `user_save_path` を渡すと主催者自身の希望も足す（participants には
    入らないため）。ただし **`meta["participants"]` には数えない**——
    そこが0人かどうかで .tnl へのフォールバックと、グループの窓を止めるか
    が決まるので、自分を数えると「開いているだけで1人」になってしまう。

    ToN ListTool の内部ファイルで公開仕様ではないので、想定外の形は
    黙って読み飛ばす。gzip/JSON として壊れている場合だけ例外を投げる
    （書き込み中を掴んだ可能性があるので、呼び出し側で握って再試行する）。
    """
    with gzip.open(path, "rb") as f:
        raw = json.loads(f.read().decode("utf-8"))

    keepOn_set: dict[str, set[int]] = {}
    wishes: dict[str, dict[str, set[int]]] = {}
    tabs = raw.get("tabs") if isinstance(raw, dict) else None
    tabs = tabs if isinstance(tabs, list) else []
    participants = 0
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        members = tab.get("participants")
        if not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, dict):
                continue
            participants += 1
            data = member.get("data")
            if not isinstance(data, dict):
                continue
            name = member.get("vrc_name")
            mine = wishes.setdefault(name, {}) if isinstance(name, str) and name else None
            _fold_wishes(data, keepOn_set, mine)

    host_self = None
    if user_save_path:
        host_self = _load_host_own_list(user_save_path, keepOn_set, wishes)

    meta = {"participants": participants,   # 自分は数えない
            "tabs": len(tabs),
            "host_self": host_self}
    return keepOn_set, meta, wishes

def should_continue(keepOn_set: dict, tnl_key: str, terror_ids: list[int]) -> bool:
    if tnl_key not in keepOn_set:
        return False
    return any(t in keepOn_set[tnl_key] for t in terror_ids)

def parse_terror_ids(a: str, b: str, c: str, round_type: str = "") -> list[int]:
    ids = [int(x) for x in (a, b, c)]
    if round_type in ("Midnight", "Bloodbath"):
        return ids[:3]
    elif round_type in ("Double Trouble", "8 Pages"):
        return ids[:2]
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