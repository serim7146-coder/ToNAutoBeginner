"""アイテムの表（item.json）。ID → 名前・分類・8 Pages に持ち込めるか。

item.json の形（依頼者のファイル。形を変えない）:
    // 8pagesに持ち込めるアイテムを1、持ち込めないアイテムを0としている。
    {"分類": {"ID": ["名前", 1 or 0], ...}, ...}
`//` で始まる行は飛ばして読む（JSON にコメントは書けないため）。ほかは普通の JSON。
無い・読めない・形が違うときは空の表（例外を外へ出さない）。
"""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Item:
    name: str
    category: str
    eight_pages_allowed: bool       # 8 Pages に持ち込めるか（表の 1 / 0）


# 読めなかった理由（1回だけデバッグログへ出すため。take_load_problem）
_load_problem: str | None = None


def load_items(path) -> dict[int, Item]:
    global _load_problem
    try:
        with open(Path(path), "r", encoding="utf-8-sig") as f:
            text = "".join(line for line in f if not line.lstrip().startswith("//"))
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("一番外が {} ではありません")
    except Exception as e:
        _load_problem = f"item.json を読めません（空の表で動きます）: {e}"
        return {}
    items: dict[int, Item] = {}
    for category, entries in raw.items():
        if not isinstance(entries, dict):
            continue
        for key, value in entries.items():
            try:
                item_id = int(key)
            except (TypeError, ValueError):
                continue
            if (not isinstance(value, list) or len(value) < 2
                    or not isinstance(value[0], str)
                    or isinstance(value[1], bool) or value[1] not in (0, 1)):
                continue                    # 形が違う行は飛ばす
            items[item_id] = Item(value[0], str(category), value[1] == 1)
    _load_problem = None
    return items


def take_load_problem() -> str | None:
    """読めなかった理由を1回だけ返す（2回目からは None）"""
    global _load_problem
    problem, _load_problem = _load_problem, None
    return problem


def item_name(item_id, items: dict) -> str | None:
    item = items.get(item_id)
    return item.name if item else None


def eight_pages_allowed(item_id, items: dict) -> bool | None:
    """1 → True、0 → False、表に無い → None"""
    item = items.get(item_id)
    return item.eight_pages_allowed if item else None


def label(item_id, items: dict) -> str:
    """ログ用の呼び名。表に無ければ id=…"""
    return item_name(item_id, items) or f"id={item_id}"
