import json
from pathlib import Path


def load_terrors(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def terror_name(id: int, data: dict) -> str | None:
    sid = str(id)
    for category in ("classic", "alternate", "unbound"):
        if sid in data[category]:
            return data[category][sid]
    return None

_NAME_INDEX_SOURCE = None
_NAME_INDEX: dict = {}


def _name_index(data: dict) -> dict:
    """名前 → ID。同名が複数あれば None を入れる（曖昧なものは使わせない）。

    terrors.json は実行中に書き換わらないので、同じ dict なら作り直さない。
    """
    global _NAME_INDEX_SOURCE, _NAME_INDEX
    if data is _NAME_INDEX_SOURCE:
        return _NAME_INDEX
    index: dict = {}
    for category in ("classic", "alternate", "unbound"):
        for id_, n in (data.get(category) or {}).items():
            index[n] = None if n in index else int(id_)
    _NAME_INDEX_SOURCE, _NAME_INDEX = data, index
    return index


def terror_id_by_name(name, data: dict) -> int | None:
    """terrors.json の名前から ID を引く。完全一致・一意のときだけ返す。

    部分一致や大文字小文字の吸収はしない——`Mona` が
    `Mona & Mona & Mona & Mona` に当たるような取り違えを避けるため。
    個体名（`Furnace` など）は表に無いので None になる。それでよく、
    呼び出し側は従来どおり revealed を待つ。
    """
    if not isinstance(name, str) or not name:
        return None
    return _name_index(data).get(name)


def terror_id(name: str, data: dict) -> int | None:
    for category in ("classic", "alternate", "unbound"):
        for id_, n in data[category].items():
            if n == name:
                return int(id_)
    return None