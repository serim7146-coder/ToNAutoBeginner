import json
import math
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any


SIGNIFICANCE_LEVELS = (
    (0.001, "テーブル！"),
    (0.025, "かなり出やすい"),
    (0.05, "出やすい"),
)

MAPS_FILENAME = "maps.json"


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


def parse_datetime(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError("日時は YYYY-MM-DD HH:MM 形式で入力してください。")


def row_datetime(row: dict[str, Any]) -> datetime | None:
    created_at = row.get("created_at")
    if created_at:
        text = str(created_at).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except ValueError:
            pass

    if row.get("date") is None:
        return None
    try:
        date = int(row.get("date"))
        time = int(row.get("time") or 0)
        return datetime(
            date // 10000,
            (date // 100) % 100,
            date % 100,
            time // 10000,
            (time // 100) % 100,
            time % 100,
        )
    except (TypeError, ValueError):
        return None


def format_datetime(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _resource_candidates(filename: str) -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = [here / filename, here.parent / filename]
    compiled = globals().get("__compiled__")
    if compiled is not None:
        candidates.append(Path(compiled.containing_dir) / filename)
    return candidates


@lru_cache(maxsize=1)
def map_entries() -> list[dict[str, Any]]:
    for path in _resource_candidates(MAPS_FILENAME):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        maps = data.get("maps")
        if isinstance(maps, list):
            return [entry for entry in maps if isinstance(entry, dict)]
    return []


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

    if round_name:
        for entry in entries:
            rounds = entry.get("rounds")
            if isinstance(rounds, list) and round_name in rounds:
                return str(entry.get("name") or f"Map {normalized_id}")

    non_run_entries = [
        entry
        for entry in entries
        if not (isinstance(entry.get("rounds"), list) and entry.get("rounds") == ["Run"])
    ]
    entry = non_run_entries[0] if non_run_entries else entries[0]
    return str(entry.get("name") or f"Map {normalized_id}")


def available_rounds(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row.get("round") or "").strip() for row in rows if str(row.get("round") or "").strip()})


def filter_rows(
    rows: list[dict[str, Any]],
    start_at: datetime | None,
    end_at: datetime | None,
    rounds: set[str],
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        round_name = str(row.get("round") or "").strip()
        if rounds and round_name not in rounds:
            continue
        dt = row_datetime(row)
        if start_at and (dt is None or dt < start_at):
            continue
        if end_at and (dt is None or dt > end_at):
            continue
        result.append(row)
    return result


def round_summary(rows: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    counts: dict[str, int] = {}
    slots: dict[str, int] = {}
    for row in rows:
        round_name = str(row.get("round") or "Unknown").strip() or "Unknown"
        counts[round_name] = counts.get(round_name, 0) + 1
        terror_ids = row.get("terror_ids")
        if isinstance(terror_ids, list):
            slots[round_name] = slots.get(round_name, 0) + len(terror_ids)
        else:
            slots[round_name] = slots.get(round_name, 0)
    return sorted(
        ((round_name, counts[round_name], slots.get(round_name, 0)) for round_name in counts),
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


def analyze_terrors(
    rows: list[dict[str, Any]],
    terror_data: dict[str, dict[str, str]],
    candidate_ids: set[int],
) -> tuple[int, int, list[TerrorStatistic]]:
    counts = {terror_id: 0 for terror_id in candidate_ids}
    total_slots = 0
    for row in rows:
        terror_ids = row.get("terror_ids")
        if not isinstance(terror_ids, list):
            continue
        for terror_id in terror_ids:
            try:
                tid = int(terror_id)
            except (TypeError, ValueError):
                continue
            if tid not in candidate_ids:
                continue
            counts[tid] = counts.get(tid, 0) + 1
            total_slots += 1

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


def map_counts_for_terror(rows: list[dict[str, Any]], terror_id: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        terror_ids = row.get("terror_ids")
        if not isinstance(terror_ids, list):
            continue
        hits = 0
        for tid in terror_ids:
            try:
                if int(tid) == terror_id:
                    hits += 1
            except (TypeError, ValueError):
                pass
        if hits == 0:
            continue
        map_name = map_name_for_id(row.get("map_id"), str(row.get("round") or "").strip())
        counts[map_name] = counts.get(map_name, 0) + hits
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
