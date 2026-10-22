"""ツールの続行リスト（GN）: .tnl の写しをツールの中に持ち、画面で直し、生存したら自動で外す。

主催リスト（ToN ListTool 起動中）でないとき、判定の下のリストになる（mainGUI._apply_keep_on）。続行表（GH）の
続行無効・デフォ続行は、この上に重ねる（ContinueTable）。.tnl のファイルは書き換えず、ListTool のフォルダにも書かない。

保存先は %APPDATA%\\ToNAutoBeginner\\continue_list.json（{"version": 2, "list_name": …, "data": {キー: [番号]}}）。
キーは .tnl の data のキー（ただし Mystic Moon・Blood Moon・Twilight・Solstice・Special・Cold Night のラウンドは Special/Moon の
1 つのキー）、番号はツールの独自の番号（TerrorIds）。初めて起動したとき（ファイルが無いとき）だけ、設定の .tnl から作る。
前の版（v1。番号は ToN ListTool の形）のファイルは、新しい版で初めて読むとき 1 回だけ独自の番号に直して書き換える
（直す前のファイルは continue_list.json.listtool.bak に残す）。

中身は {キー: frozenset(番号)}。書き換えず、変えるときは新しい dict を作る（読む側はいつも完全な 1 つを見る）。
画面（Tk のスレッド）と LogMonitor（窓ごとのスレッド）の両方から変わるので、今の中身の入れ替えはロックの中で行う。
"""
import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

import config
import DebugLog
import MatchTNL
import TerrorIds

VERSION = 2
FILE_NAME = "continue_list.json"
BAK_SUFFIX = ".listtool.bak"        # 番号を独自の番号に直す前のファイルを残す名前（continue_list.json.listtool.bak）

# .tnl に書き出すときの先頭の項目（ToN ListTool 2.17 の .tnl と同じ）
TNL_FORMAT = "tnl_json"
TNL_VERSION = 105
TNL_CREATOR = "ToNAutoBeginner"


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


def migrate_legacy(data: dict) -> tuple:
    """前の版（番号は ToN ListTool の形）の {キー: 集合} → (独自の番号の {キー: frozenset}, 落とした [(キー, 番号)])。
    Mystic Moon・Blood Moon・Twilight・Solstice・Special/Moon の中身は Special/Moon と置き場所へ配り、
    そのラウンドに出ないテラー・表に無い番号は落とす。続行表（ContinueTable）の移行も同じ決まりを使う"""
    moved, dropped = TerrorIds.migrate_legacy(data)
    return MatchTNL.drop_not_appearing(MatchTNL.distribute_special_moon(moved), "移行"), dropped


def persist_migration(path: Path, body: dict, label: str) -> bool:
    """前の版のファイルを、新しい版の中身（body）に書き換える。書き換える前に path.name + ".listtool.bak" を残す
    （すでにあれば上書きしない）。残せなかったら書き換えない（False。呼び出し側はメモリの上だけで使う）"""
    bak = path.with_name(path.name + BAK_SUFFIX)
    if not bak.exists():
        tmp = bak.with_name(bak.name + ".tmp")
        try:
            shutil.copy2(path, tmp)
            os.replace(tmp, bak)
        except OSError as e:
            DebugLog.write(f"[{label}] 移す前のファイルを残せなかったので、書き換えません: {type(e).__name__}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return False
    return _write_json(path, body, label)


def note_migration(label: str, name: str, saved: bool, dropped: list, summary: str):
    """移行したことを debug.log に書く（1 行。落とした番号があればもう 1 行）"""
    DebugLog.write(f"[{label}] 独自の番号に移しました: {summary}"
                   + (f"（移す前は {name}{BAK_SUFFIX}）" if saved else "（書き換えは出来ませんでした）"))
    if dropped:
        shown = ", ".join(f"{key.split('/')[0]}:{number}" for key, number in dropped[:20])
        more = f" ほか {len(dropped) - 20} 件" if len(dropped) > 20 else ""
        DebugLog.write(f"[{label}] 表に無い番号を落としました: {len(dropped)} 件（{shown}{more}）")


def load(path=None):
    """(data, meta) か、ファイルが無いとき None（呼ぶ側が .tnl から作る）。
    壊れている・形が違うときは、空のリストを返す（debug.log に 1 行。勝手に .tnl から作り直さない）。
    前の版（v1）のファイルは、独自の番号に直して返す（初めて読むとき 1 回だけ書き換える。.bak は残す）"""
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
    data, meta = _data_of(raw.get("data")), _meta_of(raw.get("list_name"))
    version = raw.get("version")
    if (isinstance(version, int) and not isinstance(version, bool) and version >= VERSION) or not data:
        return data, meta           # 新しい版のファイル（直す番号が無い空のリストも、書き換えない）
    data, dropped = migrate_legacy(data)
    saved = persist_migration(path, _body(data, meta), "続行リスト")
    rounds, total = counts(data)
    note_migration("続行リスト", path.name, saved, dropped, f"{rounds}ラウンド / {total}件")
    return data, meta


def _body(data: dict, meta: dict | None) -> dict:
    return {"version": VERSION,
            "list_name": (meta or {}).get("list_name", ""),
            "data": {key: sorted(ids) for key, ids in sorted(data.items()) if ids}}


def save(data: dict, meta: dict | None = None, path=None) -> bool:
    """一時ファイルに書いてから置き換える（途中で止まっても前のファイルが残る）。書けたら True"""
    path = Path(path) if path is not None else list_path()
    return _write_json(path, _body(data, meta), "続行リスト")


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
    （値が 0 でない番号だけ・HOST_SAVE_IGNORED_KEYS は除く。番号は独自の番号に直し、Special/Moon などは
    置き場所へ配り、そのラウンドに出ないテラーは外す）。読めなければ例外"""
    keep_on, meta = MatchTNL.load_tnl(str(path))
    data = {key: frozenset(ids) for key, ids in keep_on.items() if ids}
    return data, _meta_of(meta.get("list_name"))


def _writable(key: str, tid: int) -> bool:
    """.tnl のそのキーに、その番号を書いてよいか: ListTool の番号があり、ListTool のそのキーにも枠があるもの。
    8 Pages のキーは 8 Pages の行（3000 番台）だけ、ほかのキーは 8 Pages の行を書かない
    （8 Pages の ListTool の番号は list_id で、ほかのキーの同じ番号とは別のテラー）"""
    if TerrorIds.to_listtool(tid) is None:
        return False
    if (TerrorIds.kind(tid) == "eight_pages") != (key == TerrorIds.EIGHT_PAGES_KEY):
        return False
    return MatchTNL.listtool_has_slot(key, tid)


def _tnl_data(data: dict) -> dict:
    """.tnl の data（ToN ListTool の番号）: 枠（continue_slots.json）を全部並べて、入っているものを 1・無いものを 0 にする。
    ListTool の番号が無いもの（Self Inserts (Bloodthirsty) など）・ListTool のそのキーに枠が無いもの（HHI・Atrached・
    Gigabytes・Glorbo など。ListTool は Special/Moon・Mystic Moon・Blood Moon にまとめて持つ）は、ラウンドのキーには書かない。
    ListTool の Special/Moon・Mystic Moon・Blood Moon には、ツールの Special/Moon の中身とそれらの置き場所の入っているものを
    書く（listtool_moon_content）。枠に無い番号が data にあっても、書いてよいものは落とさず足す"""
    result = {}
    for key, ids in MatchTNL.slots().rounds:
        if key == MatchTNL.SPECIAL_MOON_KEY:
            continue
        listed = data.get(key, ())
        result[key] = {str(TerrorIds.to_listtool(i)): int(i in listed) for i in ids if _writable(key, i)}
    moon = MatchTNL.listtool_moon_content(data)
    for key, ids in MatchTNL.slots().moon_keys:
        result[key] = {str(TerrorIds.to_listtool(i)): int(i in moon.get(key, ())) for i in ids
                       if TerrorIds.to_listtool(i) is not None}
    for key, listed in data.items():
        if key == MatchTNL.SPECIAL_MOON_KEY:
            continue
        slots = result.setdefault(key, {})
        for tid in sorted(listed):
            if _writable(key, tid):
                slots[str(TerrorIds.to_listtool(tid))] = 1
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
    """そのラウンドの種類が使う続行リストのキー（1 つ。Mystic Moon・Blood Moon・Twilight・Solstice・Special・Cold Night は
    Special/Moon）"""
    return [MatchTNL.LOG_TO_TNL.get(round_type, round_type)]


def remove_survived(data: dict, round_type: str, ids) -> tuple:
    """生存したラウンドのテラー ids（ツールの独自の番号）を、続行リストの、そのラウンドの種類のキーから外す。
    (新しい data, [(キー, 番号)])。外すものが無ければ data そのもの・空のリストを返す。

    - ラウンドのキーに入っていれば、そこから外す（Special/Moon のラウンドは Special/Moon から）
    - ほかのラウンドのキーは触らない。複数体のラウンドは、入っているものを全部外す
    - 外して空になったキーは持たない"""
    keys = round_keys(round_type)
    removed, new = [], dict(data)
    for tid in dict.fromkeys(ids or ()):
        hit = [key for key in keys if tid in new.get(key, ())]
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
    """keys のどれかで tid が入っているか"""
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
    """入っていれば外し（keys のすべてから）、入っていなければ足す（keys のすべてへ）"""
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
