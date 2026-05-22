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

def terror_id(name: str, data: dict) -> int | None:
    for category in ("classic", "alternate", "unbound"):
        for id_, n in data[category].items():
            if n == name:
                return int(id_)
    return None