import json
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import config


SIGNIFICANCE_LEVELS = (
    (0.001, "テーブル！"),
    (0.025, "かなり出やすい"),
    (0.05, "出やすい"),
)

MAPS_FILENAME = "assets/data/maps.json"
# ラウンド → maps.json の印。ほかのラウンドは "normal"
MAP_FLAG_BY_ROUND = {"Run": "run", "8 Pages": "8pages"}


@dataclass(frozen=True)
class TerrorStatistic:
    terror_id: int
    name: str
    count: int
    expected: float
    p_value: float
    label: str


# 二項定理における、ある点での確率の計算
# lgammaで階乗の対数を求め、nCkの巨大な中間値を作らずに計算する。
def binomial_pmf(n: int, k: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    if p == 0:
        return 1.0 if k == 0 else 0.0
    if p == 1:
        return 1.0 if k == n else 0.0

    log_prob = (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )
    return math.exp(log_prob)


def binomial_pmf_lower(n: int, k: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p == 0:
        return 1.0
    if p == 1:
        return 0.0

    q = 1 - p
    term = binomial_pmf(n, 0, p)
    total = term

    for i in range(0, k):
        term *= ((n - i) / (i + 1)) * (p / q)
        total += term
        if term == 0.0:
            break

    return min(total, 1.0)


# k回以上出る確率
# 隣り合う確率の比を使う漸化式により、毎回nCkを計算し直さない。
def binomial_pmf_upper(n: int, k: int, p: float) -> float:
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p == 0:
        return 0.0
    if p == 1:
        return 1.0

    if k <= n * p:
        return max(0.0, min(1.0, 1.0 - binomial_pmf_lower(n, k - 1, p)))

    q = 1 - p
    term = binomial_pmf(n, k, p)
    total = term

    for i in range(k, n):
        term *= ((n - i) / (i + 1)) * (p / q)
        total += term
        if term == 0.0:
            break

    return min(total, 1.0)


# 仮説検定
def hypothesis_testing(p: float, a: float) -> bool:
    return p <= a


def significance_label(p_value: float) -> str:
    for alpha, label in SIGNIFICANCE_LEVELS:
        if hypothesis_testing(p_value, alpha):
            return label
    return ""


@lru_cache(maxsize=1)
def map_entries() -> list[dict[str, Any]]:
    """maps.json の maps（同梱データ。置き場所は config.resource_path と同じ）"""
    try:
        data = json.loads(config.resource_path(MAPS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    maps = data.get("maps") if isinstance(data, dict) else None
    return [entry for entry in maps if isinstance(entry, dict)] if isinstance(maps, list) else []


@lru_cache(maxsize=1)
def map_entries_by_id() -> dict[int, list[dict[str, Any]]]:
    entries_by_id: dict[int, list[dict[str, Any]]] = {}
    for entry in map_entries():
        try:
            map_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        entries_by_id.setdefault(map_id, []).append(entry)
    return entries_by_id


def map_name_for_id(map_id: Any, round_name: str = "") -> str:
    if map_id is None:
        return "Unknown"
    try:
        normalized_id = int(map_id)
    except (TypeError, ValueError):
        return "Unknown"

    entries = map_entries_by_id().get(normalized_id, [])
    if not entries:
        return f"Map {normalized_id}"

    # 同じ番号のマップがあるのは Run のマップだけ（1: Dring King's Citadel と Sewers）。
    # maps.json の印（normal / 8pages / run。1 か 0）でラウンドに合う方を選ぶ
    flag = MAP_FLAG_BY_ROUND.get(round_name, "normal")
    entry = next((e for e in entries if e.get(flag) == 1),
                 next((e for e in entries if e.get("run") != 1), entries[0]))
    return str(entry.get("name") or f"Map {normalized_id}")


def round_summary_from_counts(entries) -> list[tuple[str, int, int]]:
    """集計済みの [(ラウンド名, ラウンド数, 枠数)] をまとめて並べる（統計画面 v1 は
    手元の SQLite の GROUP BY から渡す）。多い順、同数なら名前の順"""
    counts: dict[str, int] = {}
    slots: dict[str, int] = {}
    for round_name, count, slot in entries:
        name = str(round_name or "Unknown").strip() or "Unknown"
        counts[name] = counts.get(name, 0) + int(count or 0)
        slots[name] = slots.get(name, 0) + int(slot or 0)
    return sorted(
        ((round_name, counts[round_name], slots[round_name]) for round_name in counts),
        key=lambda item: (-item[1], item[0]),
    )


def _ids_for_category(terror_data: dict[str, dict[str, str]], category: str) -> set[int]:
    return {int(terror_id) for terror_id in terror_data.get(category, {})}


def _categories_for_round(round_name: str) -> set[str]:
    if round_name == "Unbound":
        return {"unbound"}
    if round_name == "Midnight":
        return {"classic", "alternate"}
    if "Alternate" in round_name:
        return {"alternate"}
    return {"classic"}


def candidate_ids_for_rounds(rounds: set[str], terror_data: dict[str, dict[str, str]]) -> set[int]:
    categories: set[str] = set()
    for round_name in rounds:
        categories.update(_categories_for_round(round_name))
    if not categories:
        categories.add("classic")

    ids: set[int] = set()
    for category in categories:
        ids.update(_ids_for_category(terror_data, category))
    return ids


def candidate_ids_for_category(category: str, terror_data: dict[str, dict[str, str]]) -> set[int]:
    return _ids_for_category(terror_data, category.lower())


def terror_name(terror_id: int, terror_data: dict[str, dict[str, str]]) -> str:
    sid = str(terror_id)
    for category in ("classic", "alternate", "unbound"):
        if sid in terror_data.get(category, {}):
            return terror_data[category][sid]
    return f"ID:{terror_id}"


def analyze_terror_counts(
    all_counts: dict[int, int],
    terror_data: dict[str, dict[str, str]],
    candidate_ids: set[int],
) -> tuple[int, int, list[TerrorStatistic]]:
    """{テラー: 出た枠数} から、候補ごとの出現回数と二項検定（統計画面 v1 は手元の
    SQLite の GROUP BY から渡す）。(枠の合計, 候補の数, p 値の小さい順の一覧)"""
    counts = {terror_id: int(all_counts.get(terror_id, 0) or 0) for terror_id in candidate_ids}
    total_slots = sum(counts.values())

    candidate_count = len(candidate_ids)
    if candidate_count == 0 or total_slots == 0:
        return total_slots, candidate_count, []

    p = 1 / candidate_count
    expected = total_slots * p
    stats = []
    for terror_id, count in counts.items():
        p_value = binomial_pmf_upper(total_slots, count, p)
        stats.append(TerrorStatistic(
            terror_id=terror_id,
            name=terror_name(terror_id, terror_data),
            count=count,
            expected=expected,
            p_value=p_value,
            label=significance_label(p_value),
        ))
    stats.sort(key=lambda item: (item.p_value, -item.count, item.name))
    return total_slots, candidate_count, stats


def map_counts_from_entries(entries) -> list[tuple[str, int]]:
    """[(map_id, ラウンド名, 回数)] をマップ名ごとにまとめて多い順に（同数なら名前の順）"""
    counts: dict[str, int] = {}
    for map_id, round_name, hits in entries:
        map_name = map_name_for_id(map_id, str(round_name or "").strip())
        counts[map_name] = counts.get(map_name, 0) + int(hits or 0)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
