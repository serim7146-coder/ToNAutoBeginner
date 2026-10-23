"""続行表（GH）: 今の続行リスト（tnl・主催リスト）の上に重ねる 2 つの表。

- 続行無効: チェックが付いたテラーは続行しない
- デフォ続行: チェックが付いたテラーは続行する（続行無効にあってもこちらが優先）

重ねた後の続行リスト = (下の続行リスト − 続行無効) ∪ デフォ続行。
表は {ラウンドのキー: 番号の集合}。キーは続行リストと同じ（Mystic Moon・Blood Moon・Twilight・Solstice・Special・
Cold Night のラウンドは Special/Moon の 1 つ）、番号はツールの独自の番号（TerrorIds）。重ねるのは続行リストの dict だけで、
3クラ済み・完全放置モードの自爆・全続行・全自爆などは今のまま（LogMonitor._keep_on）。

表（GL）は続行無効・デフォ続行それぞれ、名前を付けたプリセットをいくつでも持つ（Store）。最初は 0 個で、
特別なプリセットは無い（最後の 1 つも消せる）。どのプリセットを使うかは窓ごと（メイン画面の窓の設定）。
「なし」を選んだ窓・選んでいない窓・選んだプリセットが消えた窓はその表を重ねない。窓の判定は
table_for(store, 窓の番号) の Table で見る。

アイテムで切り替え（GM）: 全窓共通の決まりの並び（Store.item_rules）。窓が今持っているアイテム
（0 は持っていない）で上から最初に当てはまる決まりが、その窓の選んだプリセットを種類ごとに
上書きする（窓のまま／なし／プリセット名）。当てはまらなければ窓の選択だけ。

保存先は %APPDATA%\\ToNAutoBeginner\\continue_table.json（v4）。無い・壊れているときはプリセット 0 個。
前の版（v1・v2。番号は ToN ListTool の形）のファイルは、新しい版で初めて読むとき 1 回だけ独自の番号に直して書き換える
（直す前のファイルは continue_table.json.listtool.bak に残す。窓ごとの選びとアイテムの決まりは触らない）。
v1（表が 1 組だけ）は中身があれば「デフォルト」というプリセットへ移す。v3 までの「既定」という特別なプリセットは、
読むとき 1 回だけ片づけて v4 にする（空なら消す。中身があれば「デフォルト」に名前を変える。窓の選びと
アイテムの決まりの中の名前も追う。その種類にもう「デフォルト」があれば「既定」はそのまま）。
"""
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import config
import ContinueList
import DebugLog
import MatchTNL
import RoundDecision

VERSION = 4
FILE_NAME = "continue_table.json"

# 表の種類（Table の属性名と同じ）
DISABLED = "disabled"
DEFAULT = "default"

# プリセットの名前
LEGACY_PRESET = "既定"         # v3 までにあった特別なプリセット（v4 へ移すときだけ見る）
RENAMED_PRESET = "デフォルト"   # 中身のあった「既定」の移し先の名前（v1 の表 1 組もこれ）
NONE_LABEL = "なし"            # 窓の選択肢での「その表を重ねない」。プリセットの名前にはできない

# アイテムの決まり
ITEM_NONE = 0                  # 決まりのアイテム 0 = 持っていない
ITEM_NONE_LABEL = "持っていない"
KEEP_LABEL = "窓のまま"        # 決まりの「その種類は窓の選択のまま」（保存では項目を書かない）

# 判定の行の後ろに付ける文字（重ねた表で続行の判定が変わったときだけ）
LABEL_DEFAULT = "（デフォ続行）"
LABEL_DISABLED = "（続行無効）"


@dataclass(frozen=True)
class Table:
    """窓が重ねる 1 組の表（その窓が選んだ 2 つのプリセットの中身）。
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
    """アイテムで切り替える決まり 1 つ。disabled・default はそれぞれ KEEP（窓のまま）・None（なし）・プリセット名"""
    item: int = ITEM_NONE      # アイテムの id。0 = 持っていない
    disabled: object = KEEP
    default: object = KEEP


@dataclass(frozen=True)
class Store:
    """保存するもの全部。中の dict は書き換えない（変えるときは新しい Store を作る）"""
    disabled_presets: dict = field(default_factory=dict)   # {名前: {キー: frozenset(番号)}}。最初は 0 個
    default_presets: dict = field(default_factory=dict)
    # {窓の番号(1 始まり): {DISABLED: 名前|None, DEFAULT: 名前|None}}。None は「なし」。
    # 載っていない窓・種類は選んでいない（→ 「なし」）
    windows: dict = field(default_factory=dict)
    # 上から順に見て、最初に当てはまるものを使う（全窓共通）
    item_rules: tuple = ()


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


def first_name(store: Store, which: str) -> str | None:
    """プリセットの先頭の名前（画面が編集の対象を選ぶのに使う）。プリセットが 0 なら None"""
    return next(iter(presets_of(store, which)), None)


def choice_for(store: Store, window_idx: int, which: str) -> str | None:
    """窓の which のプリセットの名前。None は「なし」（その表を重ねない）。選んでいない窓・消えたプリセットを
    選んでいた窓も「なし」"""
    name = store.windows.get(window_idx, {}).get(which)
    return name if name in presets_of(store, which) else None


def rule_for(store: Store, held_item: int | None) -> ItemRule | None:
    """持っているアイテム held_item に当てはまる最初の決まり。held_item が None（見ない）・当てはまらなければ None"""
    if held_item is None:
        return None
    return next((rule for rule in store.item_rules if rule.item == held_item), None)


def effective_choice(store: Store, window_idx: int, which: str, held_item: int | None = None) -> str | None:
    """窓が which に使うプリセットの名前（None は「なし」）。決まりが当てはまれば、窓の選択より決まりが先。
    決まりが「窓のまま」・消えたプリセットを指すときは窓の選択"""
    rule = rule_for(store, held_item)
    if rule is not None:
        pick = getattr(rule, which)
        if pick is None:
            return None
        if pick is not KEEP and pick in presets_of(store, which):
            return pick
    return choice_for(store, window_idx, which)


def rule_changes_table(store: Store, window_idx: int, held_item: int | None) -> bool:
    """決まりが当てはまり、少なくとも 1 つの種類で窓の選択と違うプリセット（「なし」を含む）になるか"""
    if rule_for(store, held_item) is None:
        return False
    return any(effective_choice(store, window_idx, which, held_item) != choice_for(store, window_idx, which)
               for which in (DISABLED, DEFAULT))


def table_for(store: Store, window_idx: int, held_item: int | None = None) -> Table:
    """窓が重ねる表。「なし」の種類は空（＝重ならない）。held_item（今持っているアイテム。None は見ない）に
    当てはまる決まりがあれば、種類ごとにその決まりのプリセットで上書きする"""
    def content(which):
        name = effective_choice(store, window_idx, which, held_item)
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
    if name in (NONE_LABEL, KEEP_LABEL):
        return f"「{name}」は使えない名前です。"
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
    rules = _rules_map(store.item_rules, which, lambda pick: new if pick == old else pick)
    return replace(_with_presets(store, which, renamed, windows), item_rules=rules)


def can_delete(store: Store, which: str) -> bool:
    """1 つでもあれば消せる（最後の 1 つも）"""
    return len(presets_of(store, which)) >= 1


def with_deleted_preset(store: Store, which: str, name: str) -> Store:
    """name を消した新しい Store。name を選んでいた窓は「なし」へ"""
    presets = presets_of(store, which)
    if name not in presets:
        raise ValueError(f"プリセット「{name}」がありません")
    remaining = {n: body for n, body in presets.items() if n != name}
    after = _with_presets(store, which, remaining)
    windows = {idx: {k: (None if (k == which and v == name) else v) for k, v in kinds.items()}
               for idx, kinds in store.windows.items()}
    rules = _rules_map(store.item_rules, which, lambda pick: KEEP if pick == name else pick)
    return replace(after, windows=windows, item_rules=rules)


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


def with_edited_table(store: Store, names: dict, table: Table) -> Store:
    """names（{DISABLED: 名前, DEFAULT: 名前}）のプリセットの中身を table の中身にした新しい Store。
    変わらない種類は触らない（名前が None＝プリセットが 0 個の種類も触らない）"""
    for which in (DISABLED, DEFAULT):
        body = getattr(table, which)
        if names[which] is not None and presets_of(store, which).get(names[which]) != body:
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
    """{名前: {キー: [番号]}} → {名前: {キー: frozenset}}。使えない名前は捨てる。1 つも無ければ空"""
    presets = {}
    if isinstance(raw, dict):
        for name, body in raw.items():
            if isinstance(name, str) and name.strip() and name != NONE_LABEL:
                presets[name] = _table_of(body)
    return presets


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


def _from_v1(raw: dict) -> Store:
    """v1（表が 1 組）→ それぞれ「既定」のプリセット（v3 までの形。読むときに drop_legacy_preset が空なら消し、
    中身があれば「デフォルト」にする）。use_* が false だった種類は全窓「なし」"""
    windows = {}
    for which, flag in ((DISABLED, "use_disabled"), (DEFAULT, "use_default")):
        if raw.get(flag) is False:
            for idx in range(1, config.MAX_WINDOWS + 1):
                windows.setdefault(idx, {})[which] = None
    return Store(disabled_presets={LEGACY_PRESET: _table_of(raw.get("disabled"))},
                 default_presets={LEGACY_PRESET: _table_of(raw.get("default"))},
                 windows=windows)


def drop_legacy_preset(store: Store) -> tuple:
    """v3 までの特別なプリセット「既定」の片づけ → (新しい Store, debug.log に書く行 [str, ...])。
    種類ごとに: 空なら消す（それを選んでいた窓・決まりは「なし」。空の表を重ねるのと「なし」は同じ結果）。
    中身があれば「デフォルト」に名前を変える（窓の選びと決まりの中の名前も追う）。その種類にもう「デフォルト」が
    あれば「既定」はそのまま残す。中身が残るときは、選んでいなかった窓（今まで「既定」を使っていた窓）が
    使い続けるように、その窓の選びとして書く（窓 1〜MAX_WINDOWS。「なし」を選んでいた窓はそのまま）。
    「既定」が無ければ何もしない"""
    notes = []
    for which in (DISABLED, DEFAULT):
        presets = presets_of(store, which)
        if LEGACY_PRESET not in presets:
            continue
        if not presets[LEGACY_PRESET]:
            remaining = {n: body for n, body in presets.items() if n != LEGACY_PRESET}
            windows = {idx: {k: (None if (k == which and v == LEGACY_PRESET) else v) for k, v in kinds.items()}
                       for idx, kinds in store.windows.items()}
            rules = _rules_map(store.item_rules, which, lambda pick: None if pick == LEGACY_PRESET else pick)
            store = replace(_with_presets(store, which, remaining, windows), item_rules=rules)
            notes.append(f"{which}: 空の「{LEGACY_PRESET}」を消しました")
        else:
            if RENAMED_PRESET in presets:
                kept = LEGACY_PRESET
                notes.append(f"{which}: 「{RENAMED_PRESET}」があるので、「{LEGACY_PRESET}」の名前はそのままです")
            else:
                store = with_renamed_preset(store, which, LEGACY_PRESET, RENAMED_PRESET)
                kept = RENAMED_PRESET
                notes.append(f"{which}: 「{LEGACY_PRESET}」を「{RENAMED_PRESET}」に変えました")
            windows = {idx: dict(kinds) for idx, kinds in store.windows.items()}
            for idx in range(1, config.MAX_WINDOWS + 1):
                windows.setdefault(idx, {}).setdefault(which, kept)       # 選んでいなかった窓だけ
            store = replace(store, windows=windows)
    return store, notes


def migrate_legacy(store: Store) -> tuple:
    """前の版（v1・v2。番号は ToN ListTool の形）の Store → (独自の番号の Store, 落とした [(キー, 番号)])。
    プリセットの中身を続行リストと同じ決まりで直す（ContinueList.migrate_legacy）。窓ごとの選びとアイテムの決まりは触らない"""
    dropped: list = []

    def moved(presets: dict) -> dict:
        out = {}
        for name, body in presets.items():
            out[name], lost = ContinueList.migrate_legacy(body)
            dropped.extend(lost)
        return out
    return replace(store, disabled_presets=moved(store.disabled_presets),
                   default_presets=moved(store.default_presets)), dropped


def load(path=None) -> Store:
    """無い・壊れている → プリセット 0 個の Store（壊れていたら debug.log に 1 行）。v1 は今の形へ移し、
    前の版（v1・v2）は独自の番号に直し、v3 までの「既定」は片づけて返す（初めて読むとき 1 回だけ v4 に書き換える。
    番号を直したときだけ .bak を残す）"""
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
        store = _from_v1(raw)
    else:
        disabled_presets = _presets_from(raw.get("disabled_presets"))
        default_presets = _presets_from(raw.get("default_presets"))
        store = Store(disabled_presets=disabled_presets, default_presets=default_presets,
                      windows=_windows_from(raw.get("windows")),
                      item_rules=_rules_from(raw.get("item_rules"), disabled_presets, default_presets))
    version = raw.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= VERSION:
        return store
    own_numbers = isinstance(version, int) and not isinstance(version, bool) and version >= 3     # v3 は独自の番号
    has_ids = any(body for presets in (store.disabled_presets, store.default_presets) for body in presets.values())
    dropped = []
    if has_ids and not own_numbers:
        store, dropped = migrate_legacy(store)
    store, notes = drop_legacy_preset(store)
    for note in notes:
        DebugLog.write(f"[続行表] {note}")
    if has_ids and not own_numbers:
        saved = ContinueList.persist_migration(path, _to_json(store), "続行表")
        total = sum(len(ids) for presets in (store.disabled_presets, store.default_presets)
                    for body in presets.values() for ids in body.values())
        ContinueList.note_migration("続行表", path.name, saved, dropped, f"{sum(counts(store))}プリセット / {total}件")
    else:
        save(store, path)           # v4 にする（番号を直したのではないので .bak は残さない）
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


def _to_json(store: Store) -> dict:
    return {
        "version": VERSION,
        "disabled_presets": {n: _body_json(b) for n, b in store.disabled_presets.items()},
        "default_presets": {n: _body_json(b) for n, b in store.default_presets.items()},
        "windows": {str(idx): dict(kinds) for idx, kinds in sorted(store.windows.items())},
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
