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
ALTERNATE_ID_OVERRIDE: dict[int, int] = {
    136: 316,   # April Fool: Alternate ログID2(+134=136) → tnl316
}


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
            result.append(ALTERNATE_ID_OVERRIDE.get(converted, converted))
        else:
            result.append(tid)
    return result