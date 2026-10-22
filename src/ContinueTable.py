"""続行表（GH）: 今の続行リスト（tnl・主催リスト）の上に重ねる 2 つの表。

- 続行無効: チェックが付いたテラーは続行しない
- デフォ続行: チェックが付いたテラーは続行する（続行無効にあってもこちらが優先）

重ねた後の続行リスト = (下の続行リスト − 続行無効) ∪ デフォ続行（ON の表だけ）。
表は {ラウンドのキー: 番号の集合}。キーと番号は続行リスト（.tnl）と同じ枠
（Alternate は +134・Unbound は +200 の番号）。重ねるのは続行リストの dict だけで、
3クラ済み・完全放置モードの自爆・全続行・全自爆などは今のまま（LogMonitor._keep_on）。

保存先は %APPDATA%\\ToNAutoBeginner\\continue_table.json。無い・壊れているときは空の表。
"""
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import config
import DebugLog
import MatchTNL
import RoundDecision

VERSION = 1
FILE_NAME = "continue_table.json"

# 表の種類（Table の属性名と同じ）
DISABLED = "disabled"
DEFAULT = "default"

# 判定の行の後ろに付ける文字（重ねた表で続行の判定が変わったときだけ）
LABEL_DEFAULT = "（デフォ続行）"
LABEL_DISABLED = "（続行無効）"


@dataclass(frozen=True)
class Table:
    """続行表。中の dict・集合は書き換えない（変えるときは新しい Table を作る）"""
    disabled: dict = field(default_factory=dict)      # {キー: frozenset(番号)}
    default: dict = field(default_factory=dict)
    use_disabled: bool = True
    use_default: bool = True


def table_path() -> Path:
    """保存先（settings.json と同じフォルダ。テストは config.SETTINGS_PATH を差し替える）"""
    return config.SETTINGS_PATH.parent / FILE_NAME


# ── 全窓・画面で共有する今の表 ──────────────────────
# 中身は書き換えず、差し替え（set_current）だけで更新する。読む側はいつも完全な 1 つを見る
_current = Table()


def current() -> Table:
    return _current


def set_current(table: Table):
    global _current
    _current = table


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


def load(path=None) -> Table:
    """無い・壊れている → 空の Table（壊れていたら debug.log に 1 行）"""
    path = Path(path) if path is not None else table_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Table()
    except (OSError, ValueError) as e:
        DebugLog.write(f"[続行表] 読めないので空で始めます: {type(e).__name__}")
        return Table()
    if not isinstance(raw, dict):
        DebugLog.write("[続行表] 形が違うので空で始めます")
        return Table()
    return Table(
        disabled=_table_of(raw.get("disabled")),
        default=_table_of(raw.get("default")),
        use_disabled=raw.get("use_disabled") is not False,
        use_default=raw.get("use_default") is not False,
    )


def _to_json(table: Table) -> dict:
    return {
        "version": VERSION,
        "use_disabled": bool(table.use_disabled),
        "use_default": bool(table.use_default),
        "disabled": {k: sorted(v) for k, v in sorted(table.disabled.items()) if v},
        "default": {k: sorted(v) for k, v in sorted(table.default.items()) if v},
    }


def save(table: Table, path=None) -> bool:
    """一時ファイルに書いてから置き換える（途中で止まっても前のファイルが残る）。書けたら True"""
    path = Path(path) if path is not None else table_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(_to_json(table), ensure_ascii=False, indent=1),
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
    """{キー: set} の続行リストに重ねた新しい dict。(base − 続行無効) ∪ デフォ続行（ON の表だけ）。

    base にあったキーは空になっても残す（「リストがあって続行なし」を保つ）。
    デフォ続行にだけあるキーは足す。base は書き換えない。重ねる中身が無い（表が空か OFF）ときは
    base をそのまま返す（コピーしない。呼び出し側も返ったものを書き換えない）"""
    if not ((table.use_disabled and table.disabled) or (table.use_default and table.default)):
        return base
    result = {key: set(ids) for key, ids in base.items()}
    if table.use_disabled:
        for key, ids in table.disabled.items():
            if key in result:
                result[key] -= ids
    if table.use_default:
        for key, ids in table.default.items():
            if ids:
                result.setdefault(key, set()).update(ids)
    return result


def disables(table: Table, key: str, slot: int) -> bool:
    """続行無効（ON のとき）の key に slot があるか。Self Inserts (Bloodthirsty) の必ず続行を外すかの判定が見る"""
    return bool(table.use_disabled) and slot in table.disabled.get(key, ())


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
def counts(table: Table) -> tuple[int, int]:
    """(続行無効の件数, デフォ続行の件数)"""
    return (sum(len(v) for v in table.disabled.values()),
            sum(len(v) for v in table.default.values()))


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
