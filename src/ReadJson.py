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


def load_terror_aliases(path, terrors: dict, log=None) -> dict:
    """terror_aliases.json を読んで `{Enrageに出る名前: テラーID}` を返す。

    利用者が手で書くファイルなので、おかしな記述は黙って捨てずに知らせる。
    ファイルが無い・壊れている場合は空 dict——例外は投げない（表が無くても
    terrors.json だけで従来どおり動く）。

    同じ名前が複数のテラーに書かれていたら、値を None にして**引けなく**する。
    曖昧なまま使うと取り違えて自爆する。
    """
    say = log or (lambda _m: None)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        say(f"⚠ terror_aliases: 読めませんでした（{e}）")
        return {}
    if not isinstance(raw, dict):
        say("⚠ terror_aliases: 形が違います（辞書ではありません）")
        return {}

    index = _name_index(terrors)
    aliases: dict = {}
    owner: dict = {}
    for key, values in raw.items():
        if not isinstance(key, str) or key not in index:
            say(f"⚠ terror_aliases: 知らないテラー名 {key!r}")
            continue
        tid = index[key]
        if tid is None:
            say(f"⚠ terror_aliases: {key!r} は terrors.json に複数あります")
            continue
        if not isinstance(values, (list, tuple)):
            say(f"⚠ terror_aliases: {key!r} の値が配列ではありません")
            continue
        for alias in values:
            if not isinstance(alias, str) or not alias.strip():
                say(f"⚠ terror_aliases: {key!r} に空の名前が混ざっています")
                continue
            alias = alias.strip()
            if alias in owner:
                if owner[alias] != tid and aliases.get(alias) is not None:
                    say(f"⚠ terror_aliases: {alias!r} が複数のテラーに"
                        "書かれています（無視します）")
                    aliases[alias] = None
                continue
            owner[alias] = tid
            aliases[alias] = tid
    return aliases


def is_alternate_terror(tid, data: dict) -> bool:
    """そのIDが terrors.json の alternate カテゴリに属するか。

    カテゴリ間でIDは重複しないので、これだけで枠を判定できる。
    `Killers is unknown` の行には `Fog (Alternate)` が出ないので、
    Enrage で判明したテラーからオルタネイト枠を決めるのに使う。
    """
    if not isinstance(tid, int) or isinstance(tid, bool):
        return False
    return str(tid) in (data.get("alternate") or {})


def terror_id_by_name(name, data: dict, aliases: dict | None = None) -> int | None:
    """名前から ID を引く。完全一致・一意のときだけ返す。

    `aliases`（terror_aliases.json）を先に見る。Fog の Enrage 行は
    classic 10 を `[CENSORED]` と表示するなど、terrors.json の名前と
    食い違うことがあるので、表に書いたほうを優先する。
    曖昧な名前は表で None になっていて、そこで止まる（terrors.json へ
    落ちて別のテラーに当たらないように）。

    部分一致や大文字小文字の吸収はしない——`Mona` が
    `Mona & Mona & Mona & Mona` に当たるような取り違えを避けるため。
    個体名が表にも無ければ None。それでよく、呼び出し側は revealed を待つ。
    """
    if not isinstance(name, str) or not name:
        return None
    if aliases and name in aliases:
        return aliases[name]
    return _name_index(data).get(name)


def terror_id(name: str, data: dict) -> int | None:
    for category in ("classic", "alternate", "unbound"):
        for id_, n in data[category].items():
            if n == name:
                return int(id_)
    return None