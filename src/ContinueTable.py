"""続行表（GH）: 今の続行リスト（tnl・主催リスト）の上に重ねる 2 つの表。

- 続行無効: チェックが付いたテラーは続行しない
- デフォ続行: チェックが付いたテラーは続行する（続行無効にあってもこちらが優先）

重ねた後の続行リスト = (下の続行リスト − 続行無効) ∪ デフォ続行。
表は {ラウンドのキー: 番号の集合}。キーと番号は続行リスト（.tnl）と同じ枠
（Alternate は +134・Unbound は +200 の番号）。重ねるのは続行リストの dict だけで、
3クラ済み・完全放置モードの自爆・全続行・全自爆などは今のまま（LogMonitor._keep_on）。

表（GL）は続行無効・デフォ続行それぞれ、名前を付けたプリセットをいくつでも持つ（Store）。
どのプリセットを使うかは窓ごと（メイン画面の窓の設定）。「なし」を選んだ窓はその表を重ねない。
選んでいない窓は「既定」（無ければ先頭）。窓の判定は table_for(store, 窓の番号) の Table で見る。

保存先は %APPDATA%\\ToNAutoBeginner\\continue_table.json（v2）。v1（表が 1 組だけ）は
読むときに「既定」というプリセットへ移す。無い・壊れているときは空の「既定」だけ。
"""
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import config
import DebugLog
import MatchTNL
import RoundDecision

VERSION = 2
FILE_NAME = "continue_table.json"

# 表の種類（Table の属性名と同じ）
DISABLED = "disabled"
DEFAULT = "default"

# プリセットの名前
DEFAULT_PRESET = "既定"       # 移行で付く名前。窓が選んでいないときもこれを使う
NONE_LABEL = "なし"            # 窓の選択肢での「その表を重ねない」。プリセットの名前にはできない

# 判定の行の後ろに付ける文字（重ねた表で続行の判定が変わったときだけ）
LABEL_DEFAULT = "（デフォ続行）"
LABEL_DISABLED = "（続行無効）"


@dataclass(frozen=True)
class Table:
    """窓が重ねる 1 組の表（その窓が選んだ 2 つのプリセットの中身）。
    中の dict・集合は書き換えない（変えるときは新しい Table を作る）"""
    disabled: dict = field(default_factory=dict)      # {キー: frozenset(番号)}
    default: dict = field(default_factory=dict)


def _empty_presets() -> dict:
    return {DEFAULT_PRESET: {}}


@dataclass(frozen=True)
class Store:
    """保存するもの全部。中の dict は書き換えない（変えるときは新しい Store を作る）"""
    disabled_presets: dict = field(default_factory=_empty_presets)   # {名前: {キー: frozenset(番号)}}
    default_presets: dict = field(default_factory=_empty_presets)
    # {窓の番号(1 始まり): {DISABLED: 名前|None, DEFAULT: 名前|None}}。None は「なし」。
    # 載っていない窓・種類は選んでいない（→ fallback_name）
    windows: dict = field(default_factory=dict)


_PRESET_FIELD = {DISABLED: "disabled_presets", DEFAULT: "default_presets"}


def table_path() -> Path:
    """保存先（settings.json と同じフォルダ。テストは config.SETTINGS_PATH を差し替える）"""
    return config.SETTINGS_PATH.parent / FILE_NAME


# ── 全窓・画面で共有する今の保存内容 ──────────────────────
# 中身は書き換えず、差し替え（set_current）だけで更新する。読む側はいつも完全な 1 つを見る
_current = Store()


def current() -> Store:
    return _current


def set_current(store: Store):
    global _current
    _current = store


# ── プリセットと窓の選び ──────────────────────────
def presets_of(store: Store, which: str) -> dict:
    """which（DISABLED / DEFAULT）のプリセット {名前: {キー: frozenset}}"""
    return getattr(store, _PRESET_FIELD[which])


def preset_names(store: Store, which: str) -> list:
    """プリセットの名前（作った順）"""
    return list(presets_of(store, which))


def fallback_name(store: Store, which: str) -> str | None:
    """選んでいない窓・消えたプリセットを選んでいた窓の行き先: 「既定」、無ければ先頭、プリセットが 0 なら None"""
    names = presets_of(store, which)
    if DEFAULT_PRESET in names:
        return DEFAULT_PRESET
    return next(iter(names), None)


def choice_for(store: Store, window_idx: int, which: str) -> str | None:
    """窓の which のプリセットの名前。None は「なし」（その表を重ねない）"""
    saved = store.windows.get(window_idx, {})
    if which in saved:
        name = saved[which]
        if name is None:
            return None
        if name in presets_of(store, which):
            return name
    return fallback_name(store, which)


def table_for(store: Store, window_idx: int) -> Table:
    """窓が重ねる表。「なし」の種類は空（＝重ならない）"""
    def content(which):
        name = choice_for(store, window_idx, which)
        return presets_of(store, which).get(name, {}) if name is not None else {}
    return Table(disabled=content(DISABLED), default=content(DEFAULT))


def with_choice(store: Store, window_idx: int, which: str, name: str | None) -> Store:
    """窓の which のプリセットを name（None は「なし」）にした新しい Store"""
    if name is not None and name not in presets_of(store, which):
        raise ValueError(f"プリセット「{name}」がありません")
    windows = {idx: dict(kinds) for idx, kinds in store.windows.items()}
    windows.setdefault(window_idx, {})[which] = name
    return replace(store, windows=windows)


def name_error(store: Store, which: str, name: str, ignore: str | None = None) -> str:
    """プリセットの名前として使えなければ理由、使えれば ""。ignore は名前変更で今の名前（同じ名前に戻すのは可）"""
    if not name.strip():
        return "名前を入力してください。"
    if name == NONE_LABEL:
        return f"「{NONE_LABEL}」は使えない名前です。"
    if name in presets_of(store, which) and name != ignore:
        return "同じ名前のプリセットがあります。"
    return ""


def unique_name(store: Store, which: str, base: str) -> str:
    """base が空いていればそのまま、使われていれば末尾に 2・3… を付けた名前（複製の既定の名前用）"""
    names = presets_of(store, which)
    name, n = base, 2
    while name in names:
        name, n = f"{base} {n}", n + 1
    return name


def _with_presets(store: Store, which: str, presets: dict, windows=None) -> Store:
    changes = {_PRESET_FIELD[which]: presets}
    if windows is not None:
        changes["windows"] = windows
    return replace(store, **changes)


def with_new_preset(store: Store, which: str, name: str) -> Store:
    """空のプリセットを足した新しい Store（名前が使えなければ ValueError）"""
    name = name.strip()
    error = name_error(store, which, name)
    if error:
        raise ValueError(error)
    return _with_presets(store, which, {**presets_of(store, which), name: {}})


def with_copied_preset(store: Store, which: str, source: str, name: str) -> Store:
    """source の写しを name で足した新しい Store"""
    name = name.strip()
    error = name_error(store, which, name)
    if error:
        raise ValueError(error)
    presets = presets_of(store, which)
    if source not in presets:
        raise ValueError(f"プリセット「{source}」がありません")
    return _with_presets(store, which, {**presets, name: dict(presets[source])})


def with_renamed_preset(store: Store, which: str, old: str, new: str) -> Store:
    """old を new に変えた新しい Store（並びはそのまま。old を選んでいた窓は new を選ぶ）"""
    new = new.strip()
    presets = presets_of(store, which)
    if old not in presets:
        raise ValueError(f"プリセット「{old}」がありません")
    error = name_error(store, which, new, ignore=old)
    if error:
        raise ValueError(error)
    renamed = {(new if name == old else name): body for name, body in presets.items()}
    windows = {idx: {k: (new if (k == which and v == old) else v) for k, v in kinds.items()}
               for idx, kinds in store.windows.items()}
    return _with_presets(store, which, renamed, windows)


def can_delete(store: Store, which: str) -> bool:
    """最後の 1 つは消せない"""
    return len(presets_of(store, which)) > 1


def with_deleted_preset(store: Store, which: str, name: str) -> Store:
    """name を消した新しい Store。name を選んでいた窓は「既定」へ（既定が無ければ先頭）"""
    presets = presets_of(store, which)
    if name not in presets:
        raise ValueError(f"プリセット「{name}」がありません")
    if not can_delete(store, which):
        raise ValueError("最後の 1 つは削除できません。")
    remaining = {n: body for n, body in presets.items() if n != name}
    after = _with_presets(store, which, remaining)
    goto = fallback_name(after, which)
    windows = {idx: {k: (goto if (k == which and v == name) else v) for k, v in kinds.items()}
               for idx, kinds in store.windows.items()}
    return replace(after, windows=windows)


def with_preset_body(store: Store, which: str, name: str, body: dict) -> Store:
    """name の中身を body（{キー: 番号}）に置き換えた新しい Store（空のキーは持たない）"""
    body = {k: frozenset(v) for k, v in body.items() if v}
    return _with_presets(store, which, {**presets_of(store, which), name: body})


def with_edited_table(store: Store, names: dict, table: Table) -> Store:
    """names（{DISABLED: 名前, DEFAULT: 名前}）のプリセットの中身を table の中身にした新しい Store。
    変わらない種類は触らない"""
    for which in (DISABLED, DEFAULT):
        body = getattr(table, which)
        if presets_of(store, which).get(names[which]) != body:
            store = with_preset_body(store, which, names[which], body)
    return store


# ── 保存・読み込み ────────────────────────────
def _ids_of(value) -> frozenset:
    """JSON の値（番号の配列）→ 番号の集合。整数でないものは捨てる"""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(v for v in value if isinstance(v, int) and not isinstance(v, bool))


def _table_of(raw) -> dict:
    """{キー: 番号の配列} → {キー: frozenset}。中身が空のキーは持たない"""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        ids = _ids_of(value)
        if isinstance(key, str) and ids:
            result[key] = ids
    return result


def _presets_from(raw) -> dict:
    """{名前: {キー: [番号]}} → {名前: {キー: frozenset}}。使えない名前は捨てる。1 つも無ければ空の「既定」"""
    presets = {}
    if isinstance(raw, dict):
        for name, body in raw.items():
            if isinstance(name, str) and name.strip() and name != NONE_LABEL:
                presets[name] = _table_of(body)
    return presets or _empty_presets()


def _windows_from(raw) -> dict:
    """{"1": {"disabled": 名前|null, "default": ...}} → {1: {...}}。読めないものは捨てる"""
    windows = {}
    if not isinstance(raw, dict):
        return windows
    for key, kinds in raw.items():
        if not (isinstance(key, str) and key.isdigit() and int(key) >= 1 and isinstance(kinds, dict)):
            continue
        kept = {which: kinds[which] for which in (DISABLED, DEFAULT)
                if which in kinds and (kinds[which] is None or isinstance(kinds[which], str))}
        if kept:
            windows[int(key)] = kept
    return windows


def _from_v1(raw: dict) -> Store:
    """v1（表が 1 組）→ それぞれ「既定」のプリセット。use_* が false だった種類は全窓「なし」"""
    windows = {}
    for which, flag in ((DISABLED, "use_disabled"), (DEFAULT, "use_default")):
        if raw.get(flag) is False:
            for idx in range(1, config.MAX_WINDOWS + 1):
                windows.setdefault(idx, {})[which] = None
    return Store(disabled_presets={DEFAULT_PRESET: _table_of(raw.get("disabled"))},
                 default_presets={DEFAULT_PRESET: _table_of(raw.get("default"))},
                 windows=windows)


def load(path=None) -> Store:
    """無い・壊れている → 空の「既定」だけの Store（壊れていたら debug.log に 1 行）。v1 は v2 の形へ移して返す"""
    path = Path(path) if path is not None else table_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Store()
    except (OSError, ValueError) as e:
        DebugLog.write(f"[続行表] 読めないので空で始めます: {type(e).__name__}")
        return Store()
    if not isinstance(raw, dict):
        DebugLog.write("[続行表] 形が違うので空で始めます")
        return Store()
    if "disabled_presets" not in raw and "default_presets" not in raw and raw.get("version") != 2:
        return _from_v1(raw)
    return Store(disabled_presets=_presets_from(raw.get("disabled_presets")),
                 default_presets=_presets_from(raw.get("default_presets")),
                 windows=_windows_from(raw.get("windows")))


def _body_json(body: dict) -> dict:
    return {k: sorted(v) for k, v in sorted(body.items()) if v}


def _to_json(store: Store) -> dict:
    return {
        "version": VERSION,
        "disabled_presets": {n: _body_json(b) for n, b in store.disabled_presets.items()},
        "default_presets": {n: _body_json(b) for n, b in store.default_presets.items()},
        "windows": {str(idx): dict(kinds) for idx, kinds in sorted(store.windows.items())},
    }


def save(store: Store, path=None) -> bool:
    """一時ファイルに書いてから置き換える（途中で止まっても前のファイルが残る）。書けたら True"""
    path = Path(path) if path is not None else table_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(_to_json(store), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as e:
        DebugLog.write(f"[続行表] 保存できませんでした: {type(e).__name__}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ── 重ねる ──────────────────────────────
def apply(base: dict, table: Table) -> dict:
    """{キー: set} の続行リストに重ねた新しい dict。(base − 続行無効) ∪ デフォ続行。

    base にあったキーは空になっても残す（「リストがあって続行なし」を保つ）。
    デフォ続行にだけあるキーは足す。base は書き換えない。重ねる中身が無い（表が空）ときは
    base をそのまま返す（コピーしない。呼び出し側も返ったものを書き換えない）"""
    if not (table.disabled or table.default):
        return base
    result = {key: set(ids) for key, ids in base.items()}
    for key, ids in table.disabled.items():
        if key in result:
            result[key] -= ids
    for key, ids in table.default.items():
        if ids:
            result.setdefault(key, set()).update(ids)
    return result


def disables(table: Table, key: str, slot: int) -> bool:
    """続行無効の key に slot があるか。Self Inserts (Bloodthirsty) の必ず続行を外すかの判定が見る"""
    return slot in table.disabled.get(key, ())


def overlay_label(base: dict, effective: dict, round_type: str, ids) -> str:
    """重ねる前と後で続行の判定が変わったら '（デフォ続行）' か '（続行無効）'。変わらなければ ''。
    判定は RoundDecision と同じ（ラウンドのキー → 無ければ Special/Moon 枠も見る）"""
    before = RoundDecision.list_continues(base, ids, round_type)
    after = RoundDecision.list_continues(effective, ids, round_type)
    if before == after:
        return ""
    return LABEL_DEFAULT if after else LABEL_DISABLED


# ── 取り込み ────────────────────────────
def from_tnl(path) -> dict:
    """ListTool の続行リスト（.tnl）→ {キー: frozenset}。値が 0 でない番号だけ
    （HOST_SAVE_IGNORED_KEYS は除く。続行リストの読み込みと同じ）"""
    keep_on, _meta = MatchTNL.load_tnl(str(path))
    return {key: frozenset(ids) for key, ids in keep_on.items() if ids}


def from_tnnl(path) -> dict:
    """ListTool の強制スキップ（.tnnl）→ {キー: frozenset}。targets_dict の番号"""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    targets = raw.get("targets_dict") if isinstance(raw, dict) else None
    if not isinstance(targets, dict):
        raise ValueError("targets_dict がありません")
    table = _table_of(targets)
    return {key: ids for key, ids in table.items()
            if key not in MatchTNL.HOST_SAVE_IGNORED_KEYS}


# ── 表の組み立て（画面が使う）──────────────────────
def counts(store: Store) -> tuple[int, int]:
    """(続行無効のプリセットの数, デフォ続行のプリセットの数)"""
    return len(store.disabled_presets), len(store.default_presets)


def with_ids(table: Table, which: str, key: str, ids) -> Table:
    """which（DISABLED / DEFAULT）の key を ids に置き換えた新しい Table（空なら key を消す）"""
    kind = dict(getattr(table, which))
    ids = frozenset(ids)
    if ids:
        kind[key] = ids
    else:
        kind.pop(key, None)
    return replace(table, **{which: kind})


def with_toggled(table: Table, which: str, key: str, tid: int) -> Table:
    """which の key の tid を入れる／外す"""
    ids = set(getattr(table, which).get(key, ()))
    ids.symmetric_difference_update({tid})
    return with_ids(table, which, key, ids)


def with_all(table: Table, which: str, new: dict) -> Table:
    """which の表を丸ごと new に置き換えた新しい Table"""
    return replace(table, **{which: {k: frozenset(v) for k, v in new.items() if v}})
