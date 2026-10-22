"""ツールの続行リスト（GN）: .tnl の写しをツールの中に持ち、画面で直し、生存したら自動で外す。

主催リスト（ToN ListTool 起動中）でないとき、判定の下のリストになる（mainGUI._apply_keep_on）。続行表（GH）の
続行無効・デフォ続行は、この上に重ねる（ContinueTable）。.tnl のファイルは書き換えず、ListTool のフォルダにも書かない。

保存先は %APPDATA%\\ToNAutoBeginner\\continue_list.json（{"version": 1, "list_name": …, "data": {キー: [番号]}}。
キーと番号は .tnl の data と同じ）。初めて起動したとき（ファイルが無いとき）だけ、設定の .tnl から作る。

中身は {キー: frozenset(番号)}。書き換えず、変えるときは新しい dict を作る（読む側はいつも完全な 1 つを見る）。
画面（Tk のスレッド）と LogMonitor（窓ごとのスレッド）の両方から変わるので、今の中身の入れ替えはロックの中で行う。
"""
import functools
import json
import os
import threading
from datetime import datetime
from pathlib import Path

import config
import DebugLog
import MatchTNL
import RoundDecision

VERSION = 1
FILE_NAME = "continue_list.json"
SLOTS_FILE = "assets/data/continue_slots.json"

# .tnl に書き出すときの先頭の項目（ToN ListTool 2.17 の .tnl と同じ）
TNL_FORMAT = "tnl_json"
TNL_VERSION = 105
TNL_CREATOR = "ToNAutoBeginner"

# Moon の 4 つのラウンド（画面の Moon と合わせる。生存したら 4 つのキーすべてから外す）
MOON_ROUNDS = ("Mystic Moon", "Blood Moon", "Twilight", "Solstice")


def list_path() -> Path:
    """保存先（settings.json と同じフォルダ。テストは config.SETTINGS_PATH を差し替える）"""
    return config.SETTINGS_PATH.parent / FILE_NAME


# ── 読み込み・保存 ────────────────────────────
def _ids_of(value) -> frozenset:
    """JSON の値（番号の配列）→ 番号の集合。整数でないものは捨てる"""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(v for v in value if isinstance(v, int) and not isinstance(v, bool))


def _data_of(raw) -> dict:
    """{キー: [番号]} → {キー: frozenset}。中身が空のキーは持たない"""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        ids = _ids_of(value)
        if isinstance(key, str) and ids:
            result[key] = ids
    return result


def _meta_of(list_name) -> dict:
    return {"list_name": list_name if isinstance(list_name, str) else ""}


def load(path=None):
    """(data, meta) か、ファイルが無いとき None（呼ぶ側が .tnl から作る）。
    壊れている・形が違うときは、空のリストを返す（debug.log に 1 行。勝手に .tnl から作り直さない）"""
    path = Path(path) if path is not None else list_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        DebugLog.write(f"[続行リスト] 読めないので空で始めます: {type(e).__name__}")
        return {}, _meta_of("")
    if not isinstance(raw, dict):
        DebugLog.write("[続行リスト] 形が違うので空で始めます")
        return {}, _meta_of("")
    return _data_of(raw.get("data")), _meta_of(raw.get("list_name"))


def save(data: dict, meta: dict | None = None, path=None) -> bool:
    """一時ファイルに書いてから置き換える（途中で止まっても前のファイルが残る）。書けたら True"""
    path = Path(path) if path is not None else list_path()
    body = {"version": VERSION,
            "list_name": (meta or {}).get("list_name", ""),
            "data": {key: sorted(ids) for key, ids in sorted(data.items()) if ids}}
    return _write_json(path, body, "続行リスト")


def _write_json(path: Path, body: dict, label: str) -> bool:
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as e:
        DebugLog.write(f"[{label}] 保存できませんでした: {type(e).__name__}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ── .tnl との行き来 ─────────────────────────────
def from_tnl(path) -> tuple:
    """.tnl → (data, meta)。読み方は判定のための読み込み（MatchTNL.load_tnl）と同じ
    （値が 0 でない番号だけ・HOST_SAVE_IGNORED_KEYS は除く）。読めなければ例外"""
    keep_on, meta = MatchTNL.load_tnl(str(path))
    data = {key: frozenset(ids) for key, ids in keep_on.items() if ids}
    return data, _meta_of(meta.get("list_name"))


@functools.lru_cache(maxsize=1)
def _slot_rounds() -> tuple:
    """((キー, (番号, ...)), ...)。assets/data/continue_slots.json の順。読めなければ空"""
    try:
        raw = json.loads(config.resource_path(SLOTS_FILE).read_text(encoding="utf-8"))
        return tuple((str(key), tuple(int(i) for i in ids)) for key, ids in raw["rounds"])
    except (OSError, ValueError, KeyError, TypeError):
        DebugLog.exception("ContinueList._slot_rounds")
        return ()


def _tnl_data(data: dict) -> dict:
    """.tnl の data: 枠（continue_slots.json）を全部並べて、入っているものを 1・無いものを 0 にする。
    枠に無いキー・番号が data にあるときも落とさず足す"""
    result = {}
    for key, ids in _slot_rounds():
        listed = data.get(key, ())
        result[key] = {str(i): int(i in listed) for i in ids}
    for key, listed in data.items():
        slots = result.setdefault(key, {})
        for tid in sorted(listed):
            slots[str(tid)] = 1
    return result


def write_tnl(data: dict, meta: dict | None, path) -> bool:
    """.tnl の形で書き出す（format・version・creator・list_name・created_at・data。値は 0/1）。
    MatchTNL.load_tnl で読み戻せる。書けたら True"""
    body = {"format": TNL_FORMAT, "version": TNL_VERSION, "creator": TNL_CREATOR,
            "list_name": (meta or {}).get("list_name", ""),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": _tnl_data(data)}
    return _write_json(Path(path), body, "続行リスト .tnl")


# ── 生存したら外す（純粋な関数）─────────────────────
def round_keys(round_type: str) -> list:
    """そのラウンドの種類が使う続行リストのキー。Moon（Mystic Moon・Blood Moon・Twilight・Solstice）は 4 つ"""
    if round_type in MOON_ROUNDS:
        return [MatchTNL.LOG_TO_TNL[name] for name in MOON_ROUNDS]
    return [MatchTNL.LOG_TO_TNL.get(round_type, round_type)]


def remove_survived(data: dict, round_type: str, ids) -> tuple:
    """生存したラウンドのテラー ids を、続行リストの、そのラウンドの種類のキーから外す。(新しい data, [(キー, 番号)])。
    外すものが無ければ data そのもの・空のリストを返す。

    - ラウンドのキー（Moon は 4 つすべて）に入っていれば、そこから外す
    - そのキーに無く Special/Moon にだけある番号は（Classic・Moon のとき）Special/Moon から外す
    - ほかのラウンドのキーは触らない。複数体のラウンドは、入っているものを全部外す
    - 外して空になったキーは持たない"""
    keys = round_keys(round_type)
    removed, new = [], dict(data)
    for tid in dict.fromkeys(ids or ()):
        hit = [key for key in keys if tid in new.get(key, ())]
        if (not hit and round_type in RoundDecision.SPECIAL_MOON_ROUNDS
                and tid in new.get(MatchTNL.SPECIAL_MOON_KEY, ())):
            hit = [MatchTNL.SPECIAL_MOON_KEY]
        for key in hit:
            left = new[key] - {tid}
            if left:
                new[key] = left
            else:
                del new[key]
            removed.append((key, tid))
    return (new, removed) if removed else (data, [])


# ── 画面が使う編集（新しい dict を返す）─────────────────
def has(data: dict, keys, tid: int) -> bool:
    """keys のどれかで tid が入っているか（Moon は 4 つのキーのどれか）"""
    return any(tid in data.get(key, ()) for key in keys)


def with_ids(data: dict, key: str, ids) -> dict:
    """key を ids に置き換えた新しい dict（空なら key を消す）"""
    new = dict(data)
    ids = frozenset(ids)
    if ids:
        new[key] = ids
    else:
        new.pop(key, None)
    return new


def with_ids_added(data: dict, keys, tids) -> dict:
    """keys のすべてに tids を足した新しい dict"""
    for key in keys:
        data = with_ids(data, key, set(data.get(key, ())) | set(tids))
    return data


def with_ids_removed(data: dict, keys, tids) -> dict:
    """keys のすべてから tids を外した新しい dict"""
    for key in keys:
        data = with_ids(data, key, set(data.get(key, ())) - set(tids))
    return data


def toggled(data: dict, keys, tid: int) -> dict:
    """入っていれば外し（keys のすべてから）、入っていなければ足す（keys のすべてへ）。Moon はどれかに入っていれば「入っている」"""
    if has(data, keys, tid):
        return with_ids_removed(data, keys, [tid])
    return with_ids_added(data, keys, [tid])


def counts(data: dict) -> tuple:
    """(ラウンドの数, 続行対象の件数)"""
    return len(data), sum(len(ids) for ids in data.values())


# ── 全窓・画面で共有する今の中身 ──────────────────────
_lock = threading.Lock()
_data: dict = {}
_meta: dict = _meta_of("")


def current() -> dict:
    """今のリスト（書き換えない）"""
    return _data


def current_meta() -> dict:
    return _meta


def set_current(data: dict, meta: dict | None = None):
    """今のリストを差し替える（meta を渡さなければ名前はそのまま）"""
    global _data, _meta
    with _lock:
        _data = data
        if meta is not None:
            _meta = meta


def update(change) -> dict:
    """今のリストを change(今の dict) の戻り値（新しい dict）にする。読んで書くまでをロックの中で行うので、
    画面の編集と生存の自動削除が重なっても、どちらの変更も失われない。新しい dict を返す"""
    global _data
    with _lock:
        _data = change(_data)
        return _data


def remove_survived_current(round_type: str, ids) -> list:
    """今のリストから生存したテラーを外す。外したもの [(キー, 番号)]（無ければ空）。外したら今の中身を差し替える"""
    global _data
    with _lock:
        new, removed = remove_survived(_data, round_type, ids)
        if removed:
            _data = new
        return removed


def save_current(path=None) -> bool:
    """今の中身を保存する"""
    with _lock:
        data, meta = _data, _meta
    return save(data, meta, path)
