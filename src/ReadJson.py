import re
import json
from pathlib import Path


MAIN_CATEGORIES = ("classic", "alternate", "unbound")
# 霧に出るのはこの2つだけ。看破の照合もこの2つの "objects" だけを使う
FOG_CATEGORIES = ("classic", "alternate")
# 新しい形の "terrors"（Enrage に出る個体名）を分けて持つキー
INDIVIDUALS_KEY = "individuals"
# 8 Pages。ログの `Killers have been set - A B 0` の A は 8 Pages 専用の番号で、
# 続行リストのIDとは別体系。行の `"list_id"` がその橋渡し（無い・null は未登録）
EIGHT_PAGES_KEY = "8pages"
EIGHT_PAGES_INDEX_KEY = "8pages_index"      # {"ページ番号": list_id | None}
# 看破（[NetworkProcessing] に実際に出たオブジェクト名）`"objects": [...]` を持つキー。
# 看破の照合はこれだけで行う。Enrage・スタン・terror_id_by_name() には使わない
OBJECTS_KEY = "objects"


def load_terrors(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return normalize_terrors(json.load(f))


def normalize_terrors(raw: dict) -> dict:
    """新旧どちらの形でも `{カテゴリ: {"ID": 名前}}` にそろえる。

    新しい形は `[{"id": 0, "name": "Huggy", "terrors": [...]}]`。そろえて
    おけば、統計画面などIDと名前しか見ない側は形の違いを知らずに済む。
    個体名は `INDIVIDUALS_KEY` に `{カテゴリ: {"ID": [個体名...]}}` で持つ。
    看破のオブジェクト名は `OBJECTS_KEY` に同じ形で持つ。
    ID が文字列の "8pages" などはそのまま残す（通常のID引きには混ぜない）。
    """
    data: dict = {}
    individuals: dict = {}
    objects: dict = {}
    pages: dict = {}
    for entry in (raw.get(EIGHT_PAGES_KEY) if isinstance(raw, dict) else None) or []:
        if not isinstance(entry, dict):
            continue
        page = entry.get("id")
        if isinstance(page, str) and page.isdigit():
            page = int(page)
        if isinstance(page, bool) or not isinstance(page, int):
            continue
        list_id = entry.get("list_id")
        if isinstance(list_id, bool) or not isinstance(list_id, int):
            list_id = None                  # 未登録（まだ分かっていない番号）
        pages[str(page)] = list_id
    for category, value in raw.items():
        if category not in MAIN_CATEGORIES or not isinstance(value, list):
            data[category] = value
            continue
        names: dict = {}
        members: dict = {}
        seen: dict = {}
        for entry in value:
            if not isinstance(entry, dict):
                continue
            tid, name = entry.get("id"), entry.get("name")
            if isinstance(tid, bool) or not isinstance(name, str):
                continue
            if isinstance(tid, str) and tid.isdigit():
                tid = int(tid)
            if not isinstance(tid, int):
                continue
            names[str(tid)] = name
            members[str(tid)] = [t for t in (entry.get("terrors") or [])
                                 if isinstance(t, str) and t.strip()]
            found = entry.get(OBJECTS_KEY)
            if isinstance(found, list):
                found = [t for t in found if isinstance(t, str) and t.strip()]
                if found:
                    seen[str(tid)] = found
        data[category] = names
        individuals[category] = members
        objects[category] = seen
    if individuals:
        data[INDIVIDUALS_KEY] = individuals
        data[OBJECTS_KEY] = objects
    data[EIGHT_PAGES_INDEX_KEY] = pages
    return data


def eight_pages_list_id(page_id, data: dict):
    """8 Pages のログ番号 → 続行リストのID。未登録・表に無い番号は None"""
    index = data.get(EIGHT_PAGES_INDEX_KEY) or {}
    return index.get(str(page_id))


def terror_name(id: int, data: dict) -> str | None:
    sid = str(id)
    for category in MAIN_CATEGORIES:
        names = data.get(category) or {}
        if sid in names:
            return names[sid]
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
    for category in MAIN_CATEGORIES:
        for id_, n in (data.get(category) or {}).items():
            index[n] = None if n in index else int(id_)
    _NAME_INDEX_SOURCE, _NAME_INDEX = data, index
    return index


def is_alternate_terror(tid, data: dict) -> bool:
    """そのIDが terrors.json の alternate カテゴリに属するか。

    カテゴリ間でIDは重複しないので、これだけで枠を判定できる。
    `Killers is unknown` の行には `Fog (Alternate)` が出ないので、
    Enrage で判明したテラーからオルタネイト枠を決めるのに使う。
    """
    if not isinstance(tid, int) or isinstance(tid, bool):
        return False
    return str(tid) in (data.get("alternate") or {})


def terror_id_by_name(name, data: dict) -> int | None:
    """テラー名から ID を引く。完全一致・一意のときだけ返す。

    部分一致や大文字小文字の吸収はしない——`Mona` が
    `Mona & Mona & Mona & Mona` に当たるような取り違えを避けるため。
    霧の Enrage の個体名は `fog_terror_id_by_name()` を使う。
    """
    if not isinstance(name, str) or not name:
        return None
    return _name_index(data).get(name)


def fog_terror_id_by_name(name, data: dict) -> int | None:
    """霧の Enrage に出た名前からテラーを引く。決まらなければ None。

    1. classic と alternate の、テラー名と個体名（"terrors"）から候補を集める
    2. 一意ならそれ。複数ならテラー名そのものと一致するものを優先する
       （The Observation の The Guidance は65秒後にしか出ないので、判明前の
       The Guidance は classic の The Guidance）。それでも決まらなければ
       None——取り違えて自爆するより revealed を待つ
    """
    if not isinstance(name, str) or not name:
        return None
    individuals = data.get(INDIVIDUALS_KEY) or {}
    by_name, by_member = set(), set()
    for category in FOG_CATEGORIES:
        members = individuals.get(category) or {}
        for id_, n in (data.get(category) or {}).items():
            if n == name:
                by_name.add(int(id_))
            elif name in members.get(id_, ()):
                by_member.add(int(id_))
    candidates = by_name | by_member
    if len(candidates) == 1:
        return candidates.pop()
    if len(by_name) == 1:
        return by_name.pop()
    return None


RE_DUPLICATE_SUFFIX = re.compile(r" \(\d+\)$")      # Unity の複製の番号 `Paradise Bird (1)`
RE_SPACES = re.compile(r"\s+")


def normalize_object_name(name) -> str:
    """看破の名前の正規化。末尾の ` (数字)` を外し、空白を除き、casefold"""
    if not isinstance(name, str):
        return ""
    return RE_SPACES.sub("", RE_DUPLICATE_SUFFIX.sub("", name.strip())).casefold()


_OBJECT_INDEX_SOURCE = None
_OBJECT_INDEX: dict = {}


def _object_index(data: dict) -> dict:
    """{正規化した名前: {ID, ...}}。classic と alternate の "objects" だけ。

    表示名・個体名は使わない（実ログに出た名前でないと取り違える）
    """
    global _OBJECT_INDEX_SOURCE, _OBJECT_INDEX
    if _OBJECT_INDEX_SOURCE is data:
        return _OBJECT_INDEX
    index: dict = {}
    objects = data.get(OBJECTS_KEY) or {}
    for category in FOG_CATEGORIES:
        for id_, names in (objects.get(category) or {}).items():
            try:
                tid = int(id_)
            except (TypeError, ValueError):
                continue
            for name in names:
                key = normalize_object_name(name)
                if key:
                    index.setdefault(key, set()).add(tid)
    _OBJECT_INDEX_SOURCE, _OBJECT_INDEX = data, index
    return index


def fog_terror_id_by_object_name(name, data: dict) -> int | None:
    """看破で見えたオブジェクト名からテラーを引く。ちょうど1つに決まるときだけ"""
    key = normalize_object_name(name)
    if not key:
        return None
    ids = _object_index(data).get(key) or set()
    return next(iter(ids)) if len(ids) == 1 else None


def terror_id(name: str, data: dict) -> int | None:
    for category in MAIN_CATEGORIES:
        for id_, n in (data.get(category) or {}).items():
            if n == name:
                return int(id_)
    return None