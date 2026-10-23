"""続行表（GH）: 今の続行リスト（tnl・主催リスト）の上に重ねる、窓ごとの 2 つの表。

- 続行無効: チェックが付いたテラーは続行しない
- デフォ続行: チェックが付いたテラーは続行する（続行無効にあってもこちらが優先）

重ねた後の続行リスト = (下の続行リスト − 続行無効) ∪ デフォ続行。
表は {ラウンドのキー: 番号の集合}。キーは続行リストと同じ（Mystic Moon・Blood Moon・Twilight・Solstice・Special・
Cold Night のラウンドは Special/Moon の 1 つ）、番号はツールの独自の番号（TerrorIds）。重ねるのは続行リストの dict だけで、
3クラ済み・完全放置モードの自爆・全続行・全自爆などは今のまま（LogMonitor._keep_on）。

窓ごとの表（GR）: 続行無効・デフォ続行の表は窓ごとに自分のものを持つ（WindowTable。Store.windows）。
プリセットは名前を付けた「ひな形」（Store.disabled_presets・default_presets。最初は 0 個で、特別なものは無い）。
窓のドロップダウンでプリセットを選ぶと、中身を窓の表に**写す**（以後、窓の表とプリセットは別もの。「なし」は窓の表を空にする）。
どのプリセットを写したかは *_from に覚える。表示は display_name: 表が空なら「なし」、最後に写したプリセットの中身が
窓の表と同じならその名前、違えば中身が同じ先頭のプリセット、無ければ「-」。窓の判定は table_for(store, 窓の番号, 持っているアイテム)。

アイテムで切り替え（GM）: 全窓共通の決まりの並び（Store.item_rules）。窓ごとの「アイテムで切り替える」（WindowTable.item_switch。
最初は OFF）が ON の窓だけ、窓が今持っているアイテム（0 は持っていない）で上から最初に当てはまる決まりが、種類ごとに
窓の表の代わりに使うものを決める（窓のまま／なし／プリセット名）。窓の表は書き換えない。

保存先は %APPDATA%\\ToNAutoBeginner\\continue_table.json（v5）。無い・壊れているときはプリセット 0 個・窓の表は空。
前の版のファイルは、新しい版で初めて読むとき 1 回だけ今の形に直して書き換える（直す前のファイルは残す。すでに残してあれば
上書きしない。残せなかったら書き換えない）:
- v1・v2（番号は ToN ListTool の形）は独自の番号に直す（continue_table.json.listtool.bak）。v1（表が 1 組だけ）は中身があれば
  「デフォルト」というプリセットへ移す。
- v3 までの「既定」という特別なプリセットは、空なら消し、中身があれば「デフォルト」に名前を変える（窓の選びとアイテムの決まりの
  中の名前も追う。その種類にもう「デフォルト」があれば「既定」はそのまま。選んでいなかった窓は、使っていた中身が残る名前を選んだ状態にする）。
- v4（窓ごとにプリセット名を選ぶ形）までは、窓の選んだプリセットの中身をその窓の表に写す（選んでいない・「なし」は空）。
  アイテムで切り替えるは全窓 OFF（continue_table.json.v4.bak）。
"""
import json
import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

import config
import ContinueList
import DebugLog
import MatchTNL
import RoundDecision

VERSION = 5
BAK_V4_SUFFIX = ".v4.bak"       # v4 までのファイルを新しい形に書き換える前に残す
FILE_NAME = "continue_table.json"

# 表の種類（Table の属性名と同じ）
DISABLED = "disabled"
DEFAULT = "default"

# プリセットの名前
LEGACY_PRESET = "既定"         # v3 までにあった特別なプリセット（読み込みの移行のときだけ見る）
RENAMED_PRESET = "デフォルト"   # 中身のあった「既定」の移し先の名前（v1 の表 1 組もこれ）
NONE_LABEL = "なし"            # 窓の選択肢での「窓の表を空にする」。プリセットの名前にはできない
DASH_LABEL = "-"              # 窓の表がどのプリセットとも同じでないときの表示。プリセットの名前にはできない

# アイテムの決まり
ITEM_NONE = 0                  # 決まりのアイテム 0 = 持っていない
ITEM_NONE_LABEL = "持っていない"
KEEP_LABEL = "窓のまま"        # 決まりの「その種類は窓の表のまま」（保存では項目を書かない）

# 判定の行の後ろに付ける文字（重ねた表で続行の判定が変わったときだけ）
LABEL_DEFAULT = "（デフォ続行）"
LABEL_DISABLED = "（続行無効）"


@dataclass(frozen=True)
class Table:
    """窓が重ねる 1 組の表（判定に使う 2 つの中身。窓の表か、アイテムの決まりのプリセット）。
    中の dict・集合は書き換えない（変えるときは新しい Table を作る）"""
    disabled: dict = field(default_factory=dict)      # {キー: frozenset(番号)}
    default: dict = field(default_factory=dict)


class _Keep:
    """決まりの「窓のまま」（None の「なし」と区別するための目印）"""
    __slots__ = ()

    def __repr__(self):
        return "KEEP"


KEEP = _Keep()


@dataclass(frozen=True)
class ItemRule:
    """アイテムで切り替える決まり 1 つ。disabled・default はそれぞれ KEEP（窓の表のまま）・None（なし）・プリセット名"""
    item: int = ITEM_NONE      # アイテムの id。0 = 持っていない
    disabled: object = KEEP
    default: object = KEEP


@dataclass(frozen=True)
class WindowTable:
    """1 つの窓が持つ表（GR）。中の dict・集合は書き換えない（変えるときは新しいものを作る）"""
    disabled: dict = field(default_factory=dict)      # {キー: frozenset(番号)}
    default: dict = field(default_factory=dict)
    disabled_from: str | None = None                  # 最後に写したプリセットの名前（表示の優先に使う）
    default_from: str | None = None
    item_switch: bool = False                         # アイテムで切り替える（最初は OFF）


@dataclass(frozen=True)
class Store:
    """保存するもの全部。中の dict は書き換えない（変えるときは新しい Store を作る）"""
    disabled_presets: dict = field(default_factory=dict)   # {名前: {キー: frozenset(番号)}}。最初は 0 個
    default_presets: dict = field(default_factory=dict)
    # {窓の番号(1 始まり): WindowTable}。載っていない窓は空の表・アイテムで切り替える OFF
    windows: dict = field(default_factory=dict)
    # 上から順に見て、最初に当てはまるものを使う（全窓共通）
    item_rules: tuple = ()


_PRESET_FIELD = {DISABLED: "disabled_presets", DEFAULT: "default_presets"}
_EMPTY_WINDOW = WindowTable()


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


# ── プリセット（ひな形）と窓の表 ──────────────────────────
def presets_of(store: Store, which: str) -> dict:
    """which（DISABLED / DEFAULT）のプリセット {名前: {キー: frozenset}}"""
    return getattr(store, _PRESET_FIELD[which])


def preset_names(store: Store, which: str) -> list:
    """プリセットの名前（作った順）"""
    return list(presets_of(store, which))


def window_table(store: Store, window_idx: int) -> WindowTable:
    """窓の表。載っていない窓は空"""
    return store.windows.get(window_idx, _EMPTY_WINDOW)


def body_of(store: Store, window_idx: int, which: str) -> dict:
    """窓の which の表の中身 {キー: frozenset}"""
    return getattr(window_table(store, window_idx), which)


def _normal(body: dict) -> dict:
    return {k: frozenset(v) for k, v in body.items() if v}


def _with_window(store: Store, window_idx: int, **changes) -> Store:
    windows = dict(store.windows)
    windows[window_idx] = replace(window_table(store, window_idx), **changes)
    return replace(store, windows=windows)


def with_window_body(store: Store, window_idx: int, which: str, body: dict) -> Store:
    """窓の which の表の中身を body にした新しい Store（空のキーは持たない。最後に写したプリセットの名前はそのまま）"""
    return _with_window(store, window_idx, **{which: _normal(body)})


def with_window_table(store: Store, window_idx: int, table: "Table") -> Store:
    """窓の 2 つの表の中身を table にした新しい Store。変わらない種類は触らない"""
    for which in (DISABLED, DEFAULT):
        body = _normal(getattr(table, which))
        if body_of(store, window_idx, which) != body:
            store = with_window_body(store, window_idx, which, body)
    return store


def with_applied_preset(store: Store, window_idx: int, which: str, name: str | None) -> Store:
    """窓の which の表に、プリセット name の中身を写した新しい Store（name は覚える）。None は表を空にする"""
    if name is None:
        return _with_window(store, window_idx, **{which: {}, f"{which}_from": None})
    if name not in presets_of(store, which):
        raise ValueError(f"プリセット「{name}」がありません")
    return _with_window(store, window_idx, **{which: _normal(presets_of(store, which)[name]),
                                              f"{which}_from": name})


def with_item_switch(store: Store, window_idx: int, on: bool) -> Store:
    """窓の「アイテムで切り替える」を on/off にした新しい Store"""
    return _with_window(store, window_idx, item_switch=bool(on))


def display_name(store: Store, window_idx: int, which: str) -> str:
    """窓のドロップダウンに出す文字。表が空なら「なし」。最後に写したプリセットの中身が窓の表と同じならその名前、
    違えば中身が同じ先頭のプリセットの名前、どれとも同じでなければ「-」"""
    wt = window_table(store, window_idx)
    body = _normal(getattr(wt, which))
    if not body:
        return NONE_LABEL
    presets = presets_of(store, which)
    last = getattr(wt, f"{which}_from")
    if last in presets and _normal(presets[last]) == body:
        return last
    for name, content in presets.items():
        if _normal(content) == body:
            return name
    return DASH_LABEL


def rule_for(store: Store, held_item: int | None) -> ItemRule | None:
    """持っているアイテム held_item に当てはまる最初の決まり。held_item が None（見ない）・当てはまらなければ None"""
    if held_item is None:
        return None
    return next((rule for rule in store.item_rules if rule.item == held_item), None)


def override(store: Store, window_idx: int, which: str, held_item: int | None) -> tuple | None:
    """アイテムの決まりが窓の which の表の代わりになるか。ならないなら None（アイテムで切り替えるが OFF・
    held_item が None・当てはまる決まりが無い・「窓のまま」・消えたプリセットを指す）。なるなら (プリセット名,)
    （「なし」は (None,)）"""
    if held_item is None or not window_table(store, window_idx).item_switch:
        return None
    rule = rule_for(store, held_item)
    if rule is None:
        return None
    pick = getattr(rule, which)
    if pick is None:
        return (None,)
    if pick is not KEEP and pick in presets_of(store, which):
        return (pick,)
    return None


def effective_body(store: Store, window_idx: int, which: str, held_item: int | None = None) -> dict:
    """窓が which に使う表の中身。決まりが代わりになるときはその中身（「なし」は空）、ならないときは窓の表"""
    over = override(store, window_idx, which, held_item)
    if over is None:
        return body_of(store, window_idx, which)
    return presets_of(store, which).get(over[0], {}) if over[0] is not None else {}


def rule_changes_table(store: Store, window_idx: int, held_item: int | None) -> bool:
    """決まりが代わりになり、少なくとも 1 つの種類で窓の表と中身が違うものになるか"""
    return any(override(store, window_idx, which, held_item) is not None
               and effective_body(store, window_idx, which, held_item) != body_of(store, window_idx, which)
               for which in (DISABLED, DEFAULT))


def table_for(store: Store, window_idx: int, held_item: int | None = None) -> Table:
    """窓が重ねる表。held_item（今持っているアイテム。None は見ない）に当てはまる決まりがあり、その窓のアイテムで
    切り替えるが ON なら、種類ごとにその決まりのプリセットの中身（「なし」は空）で置き換える"""
    return Table(disabled=effective_body(store, window_idx, DISABLED, held_item),
                 default=effective_body(store, window_idx, DEFAULT, held_item))


def name_error(store: Store, which: str, name: str, ignore: str | None = None) -> str:
    """プリセットの名前として使えなければ理由、使えれば ""。ignore は名前変更で今の名前（同じ名前に戻すのは可）"""
    if not name.strip():
        return "名前を入力してください。"
    if name in (NONE_LABEL, KEEP_LABEL, DASH_LABEL):
        return f"「{name}」は使えない名前です。"
    if name in presets_of(store, which) and name != ignore:
        return "同じ名前のプリセットがあります。"
    return ""


def _with_presets(store: Store, which: str, presets: dict, windows=None) -> Store:
    changes = {_PRESET_FIELD[which]: presets}
    if windows is not None:
        changes["windows"] = windows
    return replace(store, **changes)


def with_saved_preset(store: Store, which: str, name: str, body: dict, window_idx: int | None = None) -> Store:
    """body の写しをプリセット name にした新しい Store。name が無ければ末尾に足し（使えない名前は ValueError）、
    あれば上書きする（並びはそのまま）。window_idx の窓の表は、その名前を最後に写したものにする。
    上書きしたとき、同じプリセットを写していたほかの窓は中身が変わらないので、表示が「-」になる"""
    name = name.strip()
    if name not in presets_of(store, which):
        error = name_error(store, which, name)
        if error:
            raise ValueError(error)
    store = _with_presets(store, which, {**presets_of(store, which), name: _normal(body)})
    if window_idx is not None:
        store = _with_window(store, window_idx, **{f"{which}_from": name})
    return store


def with_renamed_preset(store: Store, which: str, old: str, new: str) -> Store:
    """old を new に変えた新しい Store（並びはそのまま。窓の最後に写した名前・アイテムの決まりの中の名前も追う）"""
    new = new.strip()
    presets = presets_of(store, which)
    if old not in presets:
        raise ValueError(f"プリセット「{old}」がありません")
    error = name_error(store, which, new, ignore=old)
    if error:
        raise ValueError(error)
    renamed = {(new if name == old else name): body for name, body in presets.items()}
    windows = {idx: (replace(wt, **{f"{which}_from": new}) if getattr(wt, f"{which}_from") == old else wt)
               for idx, wt in store.windows.items()}
    rules = _rules_map(store.item_rules, which, lambda pick: new if pick == old else pick)
    return replace(_with_presets(store, which, renamed, windows), item_rules=rules)


def can_delete(store: Store, which: str) -> bool:
    """1 つでもあれば消せる（最後の 1 つも）"""
    return len(presets_of(store, which)) >= 1


def with_deleted_preset(store: Store, which: str, name: str) -> Store:
    """name を消した新しい Store。窓の表は変えない（最後に写した名前は忘れる）。それを指していた決まりは「窓のまま」"""
    presets = presets_of(store, which)
    if name not in presets:
        raise ValueError(f"プリセット「{name}」がありません")
    remaining = {n: body for n, body in presets.items() if n != name}
    windows = {idx: (replace(wt, **{f"{which}_from": None}) if getattr(wt, f"{which}_from") == name else wt)
               for idx, wt in store.windows.items()}
    after = _with_presets(store, which, remaining, windows)
    rules = _rules_map(store.item_rules, which, lambda pick: KEEP if pick == name else pick)
    return replace(after, item_rules=rules)


def _rules_map(rules: tuple, which: str, convert) -> tuple:
    """決まりの which の値を convert(値) に置き換えた新しい決まりの並び（KEEP・None は convert に渡さない）"""
    def pick_of(rule):
        pick = getattr(rule, which)
        return pick if pick is None or pick is KEEP else convert(pick)
    return tuple(replace(rule, **{which: pick_of(rule)}) for rule in rules)


# ── アイテムの決まり（GM）──────────────────────────
_UNSET = object()


def _check_pick(store: Store, which: str, pick):
    """決まりの which の値として使えなければ ValueError（KEEP・None・あるプリセット名は可）"""
    if pick is None or pick is KEEP:
        return
    if not isinstance(pick, str) or pick not in presets_of(store, which):
        raise ValueError(f"プリセット「{pick}」がありません")


def with_rule_added(store: Store, item: int = ITEM_NONE, disabled=KEEP, default=KEEP) -> Store:
    """決まりを末尾に足した新しい Store（初めは「持っていない」・どちらも窓のまま）"""
    _check_pick(store, DISABLED, disabled)
    _check_pick(store, DEFAULT, default)
    return replace(store, item_rules=(*store.item_rules, ItemRule(item, disabled, default)))


def with_rule_changed(store: Store, index: int, item=_UNSET, disabled=_UNSET, default=_UNSET) -> Store:
    """index 番目の決まりの、渡した項目だけ変えた新しい Store（範囲外は IndexError・使えない名前は ValueError）"""
    if not 0 <= index < len(store.item_rules):
        raise IndexError(f"{index} 番目の決まりはありません")
    changes = {name: value for name, value in (("item", item), ("disabled", disabled), ("default", default))
               if value is not _UNSET}
    for which in (DISABLED, DEFAULT):
        if which in changes:
            _check_pick(store, which, changes[which])
    rules = list(store.item_rules)
    rules[index] = replace(rules[index], **changes)
    return replace(store, item_rules=tuple(rules))


def with_rule_moved(store: Store, index: int, delta: int) -> Store:
    """index 番目の決まりを delta（-1 で上・+1 で下）動かした新しい Store。端を越えるときは今のまま"""
    target = index + delta
    if not (0 <= index < len(store.item_rules) and 0 <= target < len(store.item_rules)):
        return store
    rules = list(store.item_rules)
    rules[index], rules[target] = rules[target], rules[index]
    return replace(store, item_rules=tuple(rules))


def with_rule_deleted(store: Store, index: int) -> Store:
    """index 番目の決まりを消した新しい Store（範囲外は今のまま）"""
    if not 0 <= index < len(store.item_rules):
        return store
    return replace(store, item_rules=store.item_rules[:index] + store.item_rules[index + 1:])


def shadowed_rules(store: Store) -> list:
    """決まりごとの「上に同じアイテムの決まりがあって、使われない」の真偽（編集の欄で薄く出す）"""
    seen, result = set(), []
    for rule in store.item_rules:
        result.append(rule.item in seen)
        seen.add(rule.item)
    return result


def with_preset_body(store: Store, which: str, name: str, body: dict) -> Store:
    """name の中身を body（{キー: 番号}）に置き換えた新しい Store（空のキーは持たない）"""
    body = {k: frozenset(v) for k, v in body.items() if v}
    return _with_presets(store, which, {**presets_of(store, which), name: body})


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
    """{名前: {キー: [番号]}} → {名前: {キー: frozenset}}。使えない名前は捨てる。1 つも無ければ空"""
    presets = {}
    if isinstance(raw, dict):
        for name, body in raw.items():
            if isinstance(name, str) and name.strip() and name not in (NONE_LABEL, DASH_LABEL):
                presets[name] = _table_of(body)
    return presets


def _choices_from(raw) -> dict:
    """（v4 までの形）{"1": {"disabled": 名前|null, "default": ...}} → {1: {...}}。読めないものは捨てる"""
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


def _name_or_none(value):
    return value if isinstance(value, str) and value else None


def _window_tables_from(raw) -> dict:
    """（v5）{"1": {"disabled": {キー: [番号]}, "default": {...}, "disabled_from": 名前|null, ...,
    "item_switch": false}} → {1: WindowTable}。読めないものは捨てる"""
    windows = {}
    if not isinstance(raw, dict):
        return windows
    for key, row in raw.items():
        if not (isinstance(key, str) and key.isdigit() and int(key) >= 1 and isinstance(row, dict)):
            continue
        windows[int(key)] = WindowTable(
            disabled=_table_of(row.get("disabled")), default=_table_of(row.get("default")),
            disabled_from=_name_or_none(row.get("disabled_from")),
            default_from=_name_or_none(row.get("default_from")),
            item_switch=row.get("item_switch") is True)
    return windows


def _rule_pick(row: dict, which: str, names: dict):
    """決まりの行の which の値 → (値, 指す先が無かったか)。キー無し→KEEP・null→None・あるプリセット名→その名前・
    無い名前→KEEP（無かった印つき）。文字列・null 以外は ValueError（その行は捨てる）"""
    if which not in row:
        return KEEP, False
    value = row[which]
    if value is None:
        return None, False
    if not isinstance(value, str):
        raise ValueError(which)
    if value in names:
        return value, False
    return KEEP, True


def _rules_from(raw, disabled_names: dict, default_names: dict) -> tuple:
    """"item_rules"（[{"item": 70, "default": "名前"}, ...]）→ 決まりの並び。読めない行は捨てる。
    無いプリセットを指す項目は「窓のまま」にして、debug.log に 1 行"""
    if not isinstance(raw, list):
        return ()
    rules, missing = [], 0
    for row in raw:
        item = row.get("item") if isinstance(row, dict) else None
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            continue
        try:
            disabled, lost_d = _rule_pick(row, DISABLED, disabled_names)
            default, lost_f = _rule_pick(row, DEFAULT, default_names)
        except ValueError:
            continue
        missing += lost_d + lost_f
        rules.append(ItemRule(item, disabled, default))
    if missing:
        DebugLog.write(f"[続行表] アイテムの決まりが指すプリセットが無いので「{KEEP_LABEL}」にしました: {missing} 件")
    return tuple(rules)


@dataclass(frozen=True)
class _V4:
    """v4 までの形（窓ごとにプリセットの名前を選ぶ）。読み込みの途中だけで使い、今の Store に直す"""
    disabled_presets: dict = field(default_factory=dict)
    default_presets: dict = field(default_factory=dict)
    windows: dict = field(default_factory=dict)      # {窓: {DISABLED: 名前|None, DEFAULT: 名前|None}}
    item_rules: tuple = ()


def _from_v1(raw: dict) -> _V4:
    """v1（表が 1 組）→ それぞれ「既定」のプリセット（読むときに drop_legacy_preset が空なら消し、
    中身があれば「デフォルト」にする）。use_* が false だった種類は全窓「なし」"""
    windows = {}
    for which, flag in ((DISABLED, "use_disabled"), (DEFAULT, "use_default")):
        if raw.get(flag) is False:
            for idx in range(1, config.MAX_WINDOWS + 1):
                windows.setdefault(idx, {})[which] = None
    return _V4(disabled_presets={LEGACY_PRESET: _table_of(raw.get("disabled"))},
               default_presets={LEGACY_PRESET: _table_of(raw.get("default"))},
               windows=windows)


def drop_legacy_preset(old: _V4) -> tuple:
    """v3 までの特別なプリセット「既定」の片づけ → (新しい _V4, debug.log に書く行 [str, ...])。
    種類ごとに: 空なら消す（それを選んでいた窓・決まりは「なし」。空の表を重ねるのと「なし」は同じ結果）。
    中身があれば「デフォルト」に名前を変える（窓の選びと決まりの中の名前も追う）。その種類にもう「デフォルト」が
    あれば「既定」はそのまま残す。中身が残るときは、選んでいなかった窓（今まで「既定」を使っていた窓）が
    使い続けるように、その窓の選びとして書く（窓 1〜MAX_WINDOWS。「なし」を選んでいた窓はそのまま）。
    「既定」が無ければ何もしない"""
    notes = []
    for which in (DISABLED, DEFAULT):
        field_name = _PRESET_FIELD[which]
        presets = getattr(old, field_name)
        if LEGACY_PRESET not in presets:
            continue

        def moved(new_name):
            nonlocal old
            body = presets[LEGACY_PRESET]
            renamed = {(new_name if n == LEGACY_PRESET else n): b for n, b in presets.items()}
            windows = {idx: {k: (new_name if (k == which and v == LEGACY_PRESET) else v) for k, v in kinds.items()}
                       for idx, kinds in old.windows.items()}
            rules = _rules_map(old.item_rules, which, lambda pick: new_name if pick == LEGACY_PRESET else pick)
            old = replace(old, **{field_name: renamed}, windows=windows, item_rules=rules)
            return body

        if not presets[LEGACY_PRESET]:
            remaining = {n: b for n, b in presets.items() if n != LEGACY_PRESET}
            windows = {idx: {k: (None if (k == which and v == LEGACY_PRESET) else v) for k, v in kinds.items()}
                       for idx, kinds in old.windows.items()}
            rules = _rules_map(old.item_rules, which, lambda pick: None if pick == LEGACY_PRESET else pick)
            old = replace(old, **{field_name: remaining}, windows=windows, item_rules=rules)
            notes.append(f"{which}: 空の「{LEGACY_PRESET}」を消しました")
            continue
        if RENAMED_PRESET in presets:
            kept = LEGACY_PRESET
            notes.append(f"{which}: 「{RENAMED_PRESET}」があるので、「{LEGACY_PRESET}」の名前はそのままです")
        else:
            moved(RENAMED_PRESET)
            kept = RENAMED_PRESET
            notes.append(f"{which}: 「{LEGACY_PRESET}」を「{RENAMED_PRESET}」に変えました")
        windows = {idx: dict(kinds) for idx, kinds in old.windows.items()}
        for idx in range(1, config.MAX_WINDOWS + 1):
            windows.setdefault(idx, {}).setdefault(which, kept)       # 選んでいなかった窓だけ
        old = replace(old, windows=windows)
    return old, notes


def migrate_legacy(old: _V4) -> tuple:
    """前の版（v1・v2。番号は ToN ListTool の形）→ (独自の番号の _V4, 落とした [(キー, 番号)])。
    プリセットの中身を続行リストと同じ決まりで直す（ContinueList.migrate_legacy）。窓ごとの選びとアイテムの決まりは触らない"""
    dropped: list = []

    def moved(presets: dict) -> dict:
        out = {}
        for name, body in presets.items():
            out[name], lost = ContinueList.migrate_legacy(body)
            dropped.extend(lost)
        return out
    return replace(old, disabled_presets=moved(old.disabled_presets),
                   default_presets=moved(old.default_presets)), dropped


def from_v4(old: _V4) -> Store:
    """v4 までの形 → 今の Store。窓の選んだプリセットの中身をその窓の表に写す（選んでいない・「なし」・消えた名前は空）。
    アイテムで切り替えるは全窓 OFF。プリセットとアイテムの決まりはそのまま"""
    windows = {}
    for idx, kinds in old.windows.items():
        fields = {}
        for which in (DISABLED, DEFAULT):
            name = kinds.get(which)
            presets = getattr(old, _PRESET_FIELD[which])
            if name in presets:
                fields[which] = _normal(presets[name])
                fields[f"{which}_from"] = name
        if fields:
            windows[idx] = WindowTable(**fields)
    return Store(disabled_presets=old.disabled_presets, default_presets=old.default_presets,
                 windows=windows, item_rules=old.item_rules)


def _persist_v5(path: Path, store: Store) -> bool:
    """v4 までのファイルを今の形に書き換える。書き換える前に path.name + BAK_V4_SUFFIX を残す（すでにあれば上書きしない）。
    残せなかったら書き換えない（False。呼び出し側はメモリの上だけで使う）"""
    bak = path.with_name(path.name + BAK_V4_SUFFIX)
    if not bak.exists():
        tmp = bak.with_name(bak.name + ".tmp")
        try:
            shutil.copy2(path, tmp)
            os.replace(tmp, bak)
        except OSError as e:
            DebugLog.write(f"[続行表] 移す前のファイルを残せなかったので、書き換えません: {type(e).__name__}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return False
    return save(store, path)


def load(path=None) -> Store:
    """無い・壊れている → プリセット 0 個の Store（壊れていたら debug.log に 1 行）。前の版（v1〜v4）は今の形に直して返し、
    初めて読むとき 1 回だけ v5 に書き換える（直す前のファイルは .bak に残す。冒頭の説明）"""
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
    version = raw.get("version")
    is_int = isinstance(version, int) and not isinstance(version, bool)
    if is_int and version >= VERSION:
        disabled_presets = _presets_from(raw.get("disabled_presets"))
        default_presets = _presets_from(raw.get("default_presets"))
        return Store(disabled_presets=disabled_presets, default_presets=default_presets,
                     windows=_window_tables_from(raw.get("windows")),
                     item_rules=_rules_from(raw.get("item_rules"), disabled_presets, default_presets))
    if "disabled_presets" not in raw and "default_presets" not in raw and version != 2:
        old = _from_v1(raw)
    else:
        disabled_presets = _presets_from(raw.get("disabled_presets"))
        default_presets = _presets_from(raw.get("default_presets"))
        old = _V4(disabled_presets=disabled_presets, default_presets=default_presets,
                  windows=_choices_from(raw.get("windows")),
                  item_rules=_rules_from(raw.get("item_rules"), disabled_presets, default_presets))
    own_numbers = is_int and version >= 3     # v3 から独自の番号
    has_ids = any(body for presets in (old.disabled_presets, old.default_presets) for body in presets.values())
    dropped = []
    if has_ids and not own_numbers:
        old, dropped = migrate_legacy(old)
    notes = []
    if not (is_int and version >= 4):            # v4 から「既定」は特別ではない
        old, notes = drop_legacy_preset(old)
    for note in notes:
        DebugLog.write(f"[続行表] {note}")
    store = from_v4(old)
    if has_ids and not own_numbers:
        saved = ContinueList.persist_migration(path, _to_json(store), "続行表")
        total = sum(len(ids) for presets in (store.disabled_presets, store.default_presets)
                    for body in presets.values() for ids in body.values())
        ContinueList.note_migration("続行表", path.name, saved, dropped, f"{sum(counts(store))}プリセット / {total}件")
    else:
        _persist_v5(path, store)
    return store


def _body_json(body: dict) -> dict:
    return {k: sorted(v) for k, v in sorted(body.items()) if v}


def _rule_json(rule: ItemRule) -> dict:
    """{"item": 70, "default": "名前"}。窓のまま（KEEP）の項目は書かない・なし（None）は null"""
    row = {"item": rule.item}
    for which in (DISABLED, DEFAULT):
        pick = getattr(rule, which)
        if pick is not KEEP:
            row[which] = pick
    return row


def _window_json(wt: WindowTable) -> dict:
    return {"disabled": _body_json(wt.disabled), "default": _body_json(wt.default),
            "disabled_from": wt.disabled_from, "default_from": wt.default_from,
            "item_switch": wt.item_switch}


def _to_json(store: Store) -> dict:
    return {
        "version": VERSION,
        "disabled_presets": {n: _body_json(b) for n, b in store.disabled_presets.items()},
        "default_presets": {n: _body_json(b) for n, b in store.default_presets.items()},
        "windows": {str(idx): _window_json(wt) for idx, wt in sorted(store.windows.items())},
        "item_rules": [_rule_json(rule) for rule in store.item_rules],
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
    判定は RoundDecision と同じ（ラウンドのキーだけを見る）"""
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
    """ListTool の強制スキップ（.tnnl）→ {キー: frozenset}。targets_dict の番号（ListTool の番号）を独自の番号に直し、
    Special/Moon などは置き場所へ配り、そのラウンドに出ないテラーは外す"""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    targets = raw.get("targets_dict") if isinstance(raw, dict) else None
    if not isinstance(targets, dict):
        raise ValueError("targets_dict がありません")
    table = _table_of(targets)
    listed = {key: ids for key, ids in table.items() if key not in MatchTNL.HOST_SAVE_IGNORED_KEYS}
    return {key: frozenset(ids) for key, ids in MatchTNL.own_list_from_listtool(listed, ".tnnl").items()}


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
