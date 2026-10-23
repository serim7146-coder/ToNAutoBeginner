"""GH: 続行表（続行無効・デフォ続行）。今の続行リストの上に重ねる 2 つの表。

- 重ね方: (下の続行リスト − 続行無効) ∪ デフォ続行（ON の表だけ）
- 保存・読み込み・取り込み（.tnl・.tnnl）・アイコンの座標・ビルドの引数
- LogMonitor: 窓の続行リストに重なる／干し芋・焼き芋では重ならない／3クラ済みの完全放置は自爆のまま／判定の行の後ろの文字
本物の入力・音・録画・窓・ネットワークは使わない。保存は一時フォルダだけ
"""
from tests.support import *  # noqa: F401,F403
import itertools
import cv2
import numpy as np
import ContinueList
import ContinueTable
import GroupRound
from ContinueTable import Table, Store

KEY = "Classic/クラシック"
ALT = "Alternate/オルタネイト"
SPECIAL = MatchTNL.SPECIAL_MOON_KEY
DIS, DFL = ContinueTable.DISABLED, ContinueTable.DEFAULT
BASE = "基本"                  # テストで使うプリセットの名前（特別な名前は無い）
BOTH = {DIS: BASE, DFL: BASE}  # 窓が両方の種類で BASE を選んでいる


def _body(raw) -> dict:
    return {k: frozenset(v) for k, v in (raw or {}).items()}


def _table(disabled=None, default=None) -> Table:
    return Table(disabled=_body(disabled), default=_body(default))


def _store(disabled=None, default=None, use_disabled=True, use_default=True) -> Store:
    """BASE のプリセットだけの保存内容。窓 1 は BASE を選んでいる（use_* が False の種類は「なし」）"""
    windows = {1: {DIS: BASE if use_disabled else None, DFL: BASE if use_default else None}}
    return Store(disabled_presets={BASE: _body(disabled)}, default_presets={BASE: _body(default)},
                 windows=windows)


def _two_presets() -> Store:
    """続行無効 A・B、デフォ続行 X・Y を持ち、窓 1 は A／X、窓 2 は B／Y"""
    return Store(
        disabled_presets={"A": _body({KEY: [7]}), "B": _body({KEY: [8]})},
        default_presets={"X": _body({KEY: [9]}), "Y": _body({KEY: [10]})},
        windows={1: {DIS: "A", DFL: "X"}, 2: {DIS: "B", DFL: "Y"}})


class _TableCase(unittest.TestCase):
    """今の保存内容をテストごとに空へ戻す（全窓で共有する 1 つなので）"""

    def setUp(self):
        self.addCleanup(ContinueTable.set_current, Store())
        ContinueTable.set_current(Store())


class TestApplyTruthTable(_TableCase):
    """重ね方の表のすべての組み合わせ（下の続行リスト × 続行無効 × デフォ続行 × 窓の 2 つの「なし」）"""

    def test_every_combination(self):
        for in_base, in_disabled, in_default, use_disabled, use_default in itertools.product(
                (False, True), repeat=5):
            store = _store({KEY: [7]} if in_disabled else None,
                           {KEY: [7]} if in_default else None, use_disabled, use_default)
            table = ContinueTable.table_for(store, 1)
            base = {KEY: {7, 8} if in_base else {8}}
            got = 7 in ContinueTable.apply(base, table)[KEY]
            expected = (use_default and in_default) or (
                in_base and not (use_disabled and in_disabled))
            self.assertEqual(got, expected,
                             f"base={in_base} 無効={in_disabled} デフォ={in_default} "
                             f"使う(無効,デフォ)=({use_disabled},{use_default})")

    def test_the_named_rows_of_the_spec(self):
        self.assertTrue(7 in ContinueTable.apply({KEY: {7}}, _table())[KEY])                      # 続行・なし・なし
        self.assertFalse(7 in ContinueTable.apply({KEY: {7}}, _table({KEY: [7]}))[KEY])           # 続行・無効
        self.assertTrue(7 in ContinueTable.apply({KEY: {7}}, _table({KEY: [7]}, {KEY: [7]}))[KEY])  # デフォが勝つ
        self.assertTrue(7 in ContinueTable.apply({KEY: set()}, _table(None, {KEY: [7]}))[KEY])
        self.assertFalse(7 in ContinueTable.apply({KEY: set()}, _table())[KEY])                   # 続行しない・なし・なし

    def test_a_key_that_was_in_the_base_stays_even_when_it_gets_empty(self):
        got = ContinueTable.apply({KEY: {7}}, _table({KEY: [7]}))
        self.assertIn(KEY, got, "「リストがあって続行なし」を保つ（キーが無いと別の扱いになる）")
        self.assertEqual(got[KEY], set())
        self.assertFalse(MatchTNL.should_continue(got, KEY, [7]))

    def test_a_key_only_in_the_default_table_is_added(self):
        got = ContinueTable.apply({KEY: {1}}, _table(None, {ALT: [150]}))
        self.assertEqual(got[ALT], {150})
        self.assertEqual(got[KEY], {1})

    def test_a_disabled_key_that_the_base_does_not_have_is_not_added(self):
        self.assertNotIn(ALT, ContinueTable.apply({KEY: {1}}, _table({ALT: [150]})))

    def test_the_base_is_not_touched(self):
        base = {KEY: {1, 2}}
        got = ContinueTable.apply(base, _table({KEY: [1]}, {KEY: [9]}))
        self.assertEqual(base, {KEY: {1, 2}})
        self.assertEqual(got[KEY], {2, 9})
        self.assertIsNot(got[KEY], base[KEY])

    def test_an_empty_or_none_table_returns_the_base_as_it_is(self):
        base = {KEY: {1}}
        self.assertIs(ContinueTable.apply(base, _table()), base)
        off = ContinueTable.table_for(_store({KEY: [1]}, {KEY: [2]}, False, False), 1)
        self.assertIs(ContinueTable.apply(base, off), base, "両方「なし」の窓")


class TestOverlayLabel(unittest.TestCase):
    def test_no_change_has_no_label(self):
        base = {KEY: {7}}
        self.assertEqual(ContinueTable.overlay_label(base, dict(base), "Classic", [7]), "")
        self.assertEqual(ContinueTable.overlay_label({KEY: {7}}, {KEY: {7, 9}}, "Classic", [7]), "")
        self.assertEqual(ContinueTable.overlay_label({KEY: set()}, {KEY: set()}, "Classic", [7]), "")

    def test_a_terror_removed_by_the_disabled_table(self):
        self.assertEqual(ContinueTable.overlay_label({KEY: {7}}, {KEY: set()}, "Classic", [7]),
                         "（続行無効）")

    def test_a_terror_added_by_the_default_table(self):
        self.assertEqual(ContinueTable.overlay_label({KEY: set()}, {KEY: {7}}, "Classic", [7]),
                         "（デフォ続行）")
        self.assertEqual(ContinueTable.overlay_label({}, {KEY: {7}}, "Classic", [7]), "（デフォ続行）")

    def test_several_terrors_look_at_any_one(self):
        self.assertEqual(ContinueTable.overlay_label({KEY: {7}}, {KEY: {7, 9}}, "Classic", [8, 9]),
                         "（デフォ続行）")
        self.assertEqual(ContinueTable.overlay_label({KEY: {7, 9}}, {KEY: {9}}, "Classic", [7, 9]), "",
                         "もう 1 体が続行なら変わらない")

    def test_the_special_moon_key_is_the_key_of_the_special_and_moon_rounds_only(self):
        moon = own(196)
        for round_type in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice", "Special", "Cold Night"):
            self.assertEqual(ContinueTable.overlay_label({SPECIAL: {moon}}, {SPECIAL: set()}, round_type,
                                                         [flow_id(196)]), "（続行無効）", round_type)
            self.assertEqual(ContinueTable.overlay_label({}, {SPECIAL: {moon}}, round_type, [flow_id(196)]),
                             "（デフォ続行）", round_type)
        for round_type in ("Classic", "Fog"):
            self.assertEqual(ContinueTable.overlay_label({}, {SPECIAL: {own(192)}}, round_type, [flow_id(192)]), "",
                             "ほかのラウンドは Special/Moon を予備に見ない（RoundDecision と同じ）")

    def test_the_log_round_name_is_mapped_to_the_tnl_key(self):
        self.assertEqual(ContinueTable.overlay_label({}, {"Fog/霧": {7}}, "Fog (Alternate)", [7]),
                         "（デフォ続行）")


class TestSaveLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "continue_table.json"

    def _write(self, raw):
        self.path.write_text(json.dumps(raw), encoding="utf-8")

    def test_round_trip(self):
        store = _two_presets()
        self.assertTrue(ContinueTable.save(store, self.path))
        self.assertEqual(ContinueTable.load(self.path), store)

    def test_round_trip_keeps_none_choices_and_the_order_of_presets(self):
        store = Store(disabled_presets={"後": {}, "先": _body({KEY: [1]})},
                      default_presets={BASE: {}},
                      windows={3: {DIS: None, DFL: BASE}})
        ContinueTable.save(store, self.path)
        loaded = ContinueTable.load(self.path)
        self.assertEqual(loaded, store)
        self.assertEqual(list(loaded.disabled_presets), ["後", "先"])

    def test_the_file_has_the_documented_v4_shape(self):
        store = Store(disabled_presets={BASE: _body({KEY: [9, 5]})}, default_presets={BASE: _body({ALT: [1030]})},
                      windows={1: {DIS: BASE, DFL: None}})
        ContinueTable.save(store, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw, {"version": 4,
                               "disabled_presets": {BASE: {KEY: [5, 9]}},
                               "default_presets": {BASE: {ALT: [1030]}},
                               "windows": {"1": {"disabled": BASE, "default": None}},
                               "item_rules": []})

    def test_a_missing_file_has_no_presets(self):
        store = ContinueTable.load(self.path)
        self.assertEqual(store, Store())
        self.assertEqual(store.disabled_presets, {})
        self.assertEqual(store.default_presets, {})
        self.assertEqual(store.windows, {})
        self.assertFalse(self.path.exists(), "読んだだけでは作らない")

    def test_a_new_store_has_no_presets_and_every_window_is_none(self):
        store = Store()
        self.assertEqual((store.disabled_presets, store.default_presets, store.windows), ({}, {}, {}))
        for idx in (1, 4, 8):
            for which in (DIS, DFL):
                self.assertIsNone(ContinueTable.choice_for(store, idx, which))
            self.assertEqual(ContinueTable.table_for(store, idx), Table())

    def test_a_broken_file_is_an_empty_store_and_one_line_in_the_debug_log(self):
        self.path.write_text("{ not json", encoding="utf-8")
        with patch.object(DebugLog, "write") as write:
            self.assertEqual(ContinueTable.load(self.path), Store())
        write.assert_called_once()
        self.assertIn("続行表", write.call_args.args[0])

    def test_a_file_of_the_wrong_shape_is_an_empty_store(self):
        self.path.write_text("[1, 2]", encoding="utf-8")
        with patch.object(DebugLog, "write"):
            self.assertEqual(ContinueTable.load(self.path), Store())

    def test_bad_entries_are_dropped_and_the_rest_kept_in_v2(self):
        self._write({"version": 2,
                     "disabled_presets": {"ok": {KEY: [1, "x", True, 2.5, 3], "bad": "no", ALT: []},
                                          "": {KEY: [1]}, "なし": {KEY: [2]}, "worse": "no"},
                     "default_presets": "no",
                     "windows": {"1": {"disabled": "ok", "default": 5}, "x": {"disabled": "ok"},
                                 "0": {"disabled": None}, "2": "no", "3": {"default": None}}})
        store = ContinueTable.load(self.path)
        self.assertEqual(store.disabled_presets, {"ok": {KEY: frozenset({1, 3})}, "worse": {}})
        self.assertEqual(store.default_presets, {}, "読めなければプリセット 0 個")
        self.assertEqual(store.windows, {1: {DIS: "ok"}, 3: {DFL: None}})

    def test_v2_with_no_presets_stays_without_presets(self):
        self._write({"version": 2, "disabled_presets": {}, "default_presets": {}, "windows": {}})
        self.assertEqual(ContinueTable.load(self.path), Store())

    def test_save_replaces_through_a_temporary_file(self):
        ContinueTable.save(_store({KEY: [1]}), self.path)
        with patch.object(ContinueTable.os, "replace", wraps=os.replace) as replace:
            ContinueTable.save(_store({KEY: [2]}), self.path)
        replace.assert_called_once()
        self.assertEqual(Path(replace.call_args.args[0]).parent, self.path.parent)
        self.assertNotEqual(Path(replace.call_args.args[0]), self.path)
        self.assertEqual(os.listdir(self.tmp.name), [self.path.name], "一時ファイルは残らない")

    def test_a_failed_save_keeps_the_old_file(self):
        ContinueTable.save(_store({KEY: [1]}), self.path)
        with patch.object(ContinueTable.os, "replace", side_effect=OSError("disk")), \
             patch.object(DebugLog, "write"):
            self.assertFalse(ContinueTable.save(_store({KEY: [2]}), self.path))
        self.assertEqual(ContinueTable.load(self.path).disabled_presets, {BASE: _body({KEY: [1]})})
        self.assertEqual(os.listdir(self.tmp.name), [self.path.name])


    def test_the_default_path_follows_the_settings_folder(self):
        self.assertEqual(ContinueTable.table_path(), config.SETTINGS_PATH.parent / "continue_table.json")
        self.assertNotEqual(ContinueTable.table_path().parent, REAL_SETTINGS_DIR,
                            "テストは本物の %APPDATA% に書かない")


class TestMigrationFromV1(unittest.TestCase):
    """v1（表が 1 組だけ）→ 中身のある種類は「デフォルト」のプリセット（空の種類は作らない）。窓は今まで通り使い続ける"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "continue_table.json"

    def _v1(self, **extra):
        raw = {"version": 1, "use_disabled": True, "use_default": True,
               "disabled": {KEY: [5, 9]}, "default": {ALT: [164]}}
        raw.update(extra)
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        return ContinueTable.load(self.path)

    def test_the_old_tables_become_the_default_named_presets(self):
        store = self._v1()
        self.assertEqual(store.disabled_presets, {"デフォルト": _body({KEY: [5, 9]})})
        self.assertEqual(store.default_presets, {"デフォルト": _body({ALT: [own(164)]})}, "番号は独自の番号に移る")
        self.assertEqual(store.windows, {idx: {DIS: "デフォルト", DFL: "デフォルト"}
                                         for idx in range(1, config.MAX_WINDOWS + 1)}, "今まで全窓が使っていた")
        for idx in (1, 4, 8):
            table = ContinueTable.table_for(store, idx)
            self.assertEqual(table.disabled, _body({KEY: [5, 9]}), idx)
            self.assertEqual(table.default, _body({ALT: [own(164)]}), idx)

    def test_use_disabled_false_makes_every_window_none_for_that_kind_only(self):
        store = self._v1(use_disabled=False)
        for idx in range(1, config.MAX_WINDOWS + 1):
            table = ContinueTable.table_for(store, idx)
            self.assertEqual(table.disabled, {}, idx)
            self.assertEqual(table.default, _body({ALT: [own(164)]}), idx)
        self.assertEqual(store.disabled_presets, {"デフォルト": _body({KEY: [5, 9]})}, "表の中身は消さない")

    def test_use_default_false_makes_every_window_none_for_the_default_kind_only(self):
        store = self._v1(use_default=False)
        for idx in range(1, config.MAX_WINDOWS + 1):
            table = ContinueTable.table_for(store, idx)
            self.assertEqual(table.disabled, _body({KEY: [5, 9]}), idx)
            self.assertEqual(table.default, {}, idx)
        self.assertEqual(store.default_presets, {"デフォルト": _body({ALT: [own(164)]})})

    def test_a_v1_file_without_a_version_is_migrated_too_and_bad_entries_are_dropped(self):
        self.path.write_text(json.dumps({"disabled": {KEY: [1, "x", True, 3], "bad": "no"}, "default": "no"}),
                             encoding="utf-8")
        store = ContinueTable.load(self.path)
        self.assertEqual(store.disabled_presets, {"デフォルト": _body({KEY: [1, 3]})})
        self.assertEqual(store.default_presets, {}, "空の表はプリセットにしない")

    def test_saving_after_loading_writes_v4_and_loads_back_the_same(self):
        store = self._v1(use_disabled=False)
        ContinueTable.save(store, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 4)
        self.assertNotIn("use_disabled", raw)
        self.assertEqual(ContinueTable.load(self.path), store)


class TestTableFor(unittest.TestCase):
    def test_each_window_gets_the_two_presets_it_chose(self):
        store = _two_presets()
        one, two = ContinueTable.table_for(store, 1), ContinueTable.table_for(store, 2)
        self.assertEqual((one.disabled, one.default), (_body({KEY: [7]}), _body({KEY: [9]})))
        self.assertEqual((two.disabled, two.default), (_body({KEY: [8]}), _body({KEY: [10]})))

    def test_a_window_that_chose_nothing_uses_none(self):
        store = Store(disabled_presets={"A": _body({KEY: [1]}), BASE: _body({KEY: [2]})},
                      default_presets={BASE: _body({KEY: [3]})}, windows={1: {DIS: "A"}})
        self.assertEqual(ContinueTable.table_for(store, 1).disabled, _body({KEY: [1]}))
        self.assertEqual(ContinueTable.table_for(store, 1).default, {}, "種類ごとに、選んでいなければ「なし」")
        self.assertIsNone(ContinueTable.choice_for(store, 1, DFL))
        self.assertEqual(ContinueTable.table_for(store, 5), Table(), "窓ごとに「なし」")

    def test_the_first_preset_has_no_special_meaning_for_an_unset_window(self):
        store = Store(disabled_presets={"B": _body({KEY: [1]}), "A": _body({KEY: [2]})})
        self.assertEqual(ContinueTable.table_for(store, 1).disabled, {})
        self.assertEqual(ContinueTable.first_name(store, DIS), "B", "画面が編集の対象に選ぶだけ")
        self.assertIsNone(ContinueTable.first_name(store, DFL))

    def test_none_means_no_table_for_that_window_and_kind(self):
        store = Store(disabled_presets={BASE: _body({KEY: [1]})}, default_presets={BASE: _body({KEY: [2]})},
                      windows={1: {DIS: None, DFL: BASE}, 2: {DIS: BASE, DFL: None}})
        self.assertEqual(ContinueTable.table_for(store, 1), Table(default=_body({KEY: [2]})))
        self.assertEqual(ContinueTable.table_for(store, 2), Table(disabled=_body({KEY: [1]})))
        self.assertEqual(ContinueTable.choice_for(store, 1, DIS), None)
        self.assertEqual(ContinueTable.choice_for(store, 3, DIS), None, "選んでいない窓も「なし」")

    def test_a_choice_naming_a_missing_preset_is_none(self):
        store = Store(disabled_presets={BASE: _body({KEY: [1]})}, windows={1: {DIS: "消えた"}})
        self.assertIsNone(ContinueTable.choice_for(store, 1, DIS))
        self.assertEqual(ContinueTable.table_for(store, 1), Table())

    def test_with_choice_changes_one_window_and_kind_and_rejects_unknown_names(self):
        store = _two_presets()
        changed = ContinueTable.with_choice(store, 1, DIS, "B")
        self.assertEqual(ContinueTable.choice_for(changed, 1, DIS), "B")
        self.assertEqual(ContinueTable.choice_for(changed, 1, DFL), "X")
        self.assertEqual(ContinueTable.choice_for(store, 1, DIS), "A", "元の Store は変えない")
        self.assertEqual(ContinueTable.choice_for(ContinueTable.with_choice(store, 7, DFL, None), 7, DFL), None)
        with self.assertRaises(ValueError):
            ContinueTable.with_choice(store, 1, DIS, "X")      # X はデフォ続行の名前


class TestPresetOperations(unittest.TestCase):
    def test_new_makes_an_empty_preset_at_the_end(self):
        store = ContinueTable.with_new_preset(_two_presets(), DIS, " C ")
        self.assertEqual(ContinueTable.preset_names(store, DIS), ["A", "B", "C"], "前後の空白は除く")
        self.assertEqual(store.disabled_presets["C"], {})
        self.assertEqual(ContinueTable.preset_names(store, DFL), ["X", "Y"], "もう片方の種類は変えない")

    def test_a_name_must_be_new_not_empty_and_not_the_none_label(self):
        store = _two_presets()
        for bad in ("A", "", "  ", ContinueTable.NONE_LABEL):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ContinueTable.with_new_preset(store, DIS, bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                ContinueTable.with_copied_preset(store, DIS, "A", bad)
        ContinueTable.with_new_preset(store, DIS, "X")          # 別の種類の名前は使える

    def test_copy_has_the_same_content_and_is_independent(self):
        store = ContinueTable.with_copied_preset(_two_presets(), DIS, "A", "A2")
        self.assertEqual(store.disabled_presets["A2"], _body({KEY: [7]}))
        store = ContinueTable.with_preset_body(store, DIS, "A2", {KEY: [1]})
        self.assertEqual(store.disabled_presets["A"], _body({KEY: [7]}))
        with self.assertRaises(ValueError):
            ContinueTable.with_copied_preset(store, DIS, "なにか", "Z")

    def test_unique_name_numbers_a_taken_name(self):
        store = _two_presets()
        self.assertEqual(ContinueTable.unique_name(store, DIS, "A のコピー"), "A のコピー")
        store = ContinueTable.with_new_preset(store, DIS, "A のコピー")
        self.assertEqual(ContinueTable.unique_name(store, DIS, "A のコピー"), "A のコピー 2")

    def test_rename_keeps_the_order_and_the_windows_follow(self):
        store = ContinueTable.with_renamed_preset(_two_presets(), DIS, "A", "A改")
        self.assertEqual(ContinueTable.preset_names(store, DIS), ["A改", "B"])
        self.assertEqual(store.disabled_presets["A改"], _body({KEY: [7]}))
        self.assertEqual(ContinueTable.choice_for(store, 1, DIS), "A改")
        self.assertEqual(ContinueTable.choice_for(store, 2, DIS), "B")
        self.assertEqual(ContinueTable.choice_for(store, 1, DFL), "X", "もう片方の種類の同名は変えない")

    def test_rename_to_a_taken_name_is_refused_and_to_itself_is_allowed(self):
        store = _two_presets()
        with self.assertRaises(ValueError):
            ContinueTable.with_renamed_preset(store, DIS, "A", "B")
        self.assertEqual(ContinueTable.with_renamed_preset(store, DIS, "A", "A"), store)

    def test_rename_does_not_touch_the_other_kind_with_the_same_name(self):
        store = Store(disabled_presets={"P": {}, "Q": {}}, default_presets={"P": {}},
                      windows={1: {DIS: "P", DFL: "P"}})
        store = ContinueTable.with_renamed_preset(store, DIS, "P", "R")
        self.assertEqual(store.windows, {1: {DIS: "R", DFL: "P"}})

    def test_delete_sends_windows_that_used_it_to_none(self):
        store = Store(disabled_presets={BASE: _body({KEY: [1]}), "A": {}, "B": {}},
                      windows={1: {DIS: "A"}, 2: {DIS: "B"}, 3: {DIS: None}})
        store = ContinueTable.with_deleted_preset(store, DIS, "A")
        self.assertEqual(ContinueTable.preset_names(store, DIS), [BASE, "B"])
        self.assertEqual(store.windows[1], {DIS: None})
        self.assertEqual(store.windows[2], {DIS: "B"})
        self.assertEqual(store.windows[3], {DIS: None}, "「なし」はそのまま")

    def test_a_window_that_used_a_deleted_preset_has_none(self):
        store = Store(disabled_presets={"A": {}, BASE: {}, "B": {}}, windows={1: {DIS: BASE}})
        store = ContinueTable.with_deleted_preset(store, DIS, BASE)
        self.assertIsNone(ContinueTable.choice_for(store, 1, DIS))
        self.assertIsNone(ContinueTable.choice_for(store, 9, DIS), "選んでいなかった窓も「なし」")

    def test_the_last_preset_of_a_kind_can_be_deleted(self):
        store = Store(disabled_presets={"A": _body({KEY: [7]})}, default_presets={"X": {}, "Y": {}},
                      windows={1: {DIS: "A", DFL: "X"}})
        self.assertTrue(ContinueTable.can_delete(store, DIS))
        self.assertTrue(ContinueTable.can_delete(store, DFL))
        store = ContinueTable.with_deleted_preset(store, DIS, "A")
        self.assertEqual(store.disabled_presets, {}, "プリセットが 0 個になる")
        self.assertEqual(store.windows[1], {DIS: None, DFL: "X"}, "もう片方の種類は変えない")
        self.assertEqual(ContinueTable.table_for(store, 1), Table())
        self.assertFalse(ContinueTable.can_delete(store, DIS), "0 個では消すものが無い")
        with self.assertRaises(ValueError):
            ContinueTable.with_deleted_preset(store, DIS, "A")
        with self.assertRaises(ValueError):
            ContinueTable.with_deleted_preset(store, DFL, "Z")

    def test_a_kind_with_no_presets_can_start_again(self):
        store = ContinueTable.with_new_preset(Store(), DIS, "最初")
        self.assertEqual(ContinueTable.preset_names(store, DIS), ["最初"])
        self.assertEqual(ContinueTable.preset_names(store, DFL), [])
        self.assertIsNone(ContinueTable.choice_for(store, 1, DIS), "作っただけでは窓は選ばない")
        self.assertEqual(ContinueTable.with_edited_table(Store(), {DIS: None, DFL: None}, Table(disabled=_body({KEY: [1]}))),
                         Store(), "選んでいるプリセットが無い種類は書かない")

    def test_with_edited_table_writes_only_the_kinds_that_changed(self):
        store = _two_presets()
        table = Table(disabled=_body({KEY: [7, 70]}), default=store.default_presets["X"])
        edited = ContinueTable.with_edited_table(store, {DIS: "A", DFL: "X"}, table)
        self.assertEqual(edited.disabled_presets["A"], _body({KEY: [7, 70]}))
        self.assertIs(edited.default_presets, store.default_presets, "変わらない種類は同じもの")
        self.assertEqual(store.disabled_presets["A"], _body({KEY: [7]}))


class TestImports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data) -> Path:
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_from_tnl_takes_the_nonzero_ids_and_skips_the_ignored_keys(self):
        ignored = next(iter(MatchTNL.HOST_SAVE_IGNORED_KEYS))
        path = self._write("a.tnl", {"list_name": "x", "data": {
            KEY: {"5": 1, "6": 0, "7": 1}, ALT: {"150": 0}, ignored: {"1": 1}, "bad": 3}})
        self.assertEqual(ContinueTable.from_tnl(path), {KEY: frozenset({5, 7})})

    def test_from_tnnl_takes_targets_dict(self):
        ignored = next(iter(MatchTNL.HOST_SAVE_IGNORED_KEYS))
        path = self._write("a.tnnl", {"targets_dict": {KEY: [3, 1], ALT: [], ignored: [9]},
                                      "metadata": {"Name": "x"}})
        self.assertEqual(ContinueTable.from_tnnl(path), {KEY: frozenset({1, 3})})

    def test_from_tnnl_without_targets_dict_is_an_error(self):
        with self.assertRaises(ValueError):
            ContinueTable.from_tnnl(self._write("b.tnnl", {"metadata": {}}))

    def test_a_non_json_file_is_an_error_for_both(self):
        path = Path(self.tmp.name) / "c.tnl"
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            ContinueTable.from_tnl(path)
        with self.assertRaises(ValueError):
            ContinueTable.from_tnnl(path)


class TestBuilders(unittest.TestCase):
    def test_counts_toggle_and_replace(self):
        self.assertEqual(ContinueTable.counts(_two_presets()), (2, 2), "プリセットの数")
        self.assertEqual(ContinueTable.counts(Store()), (0, 0))
        table = _table({KEY: [1, 2]}, {ALT: [150]})
        t2 = ContinueTable.with_toggled(table, ContinueTable.DISABLED, KEY, 2)
        self.assertEqual(t2.disabled, {KEY: frozenset({1})})
        self.assertEqual(table.disabled, {KEY: frozenset({1, 2})}, "元の表は変えない")
        t3 = ContinueTable.with_toggled(t2, ContinueTable.DISABLED, KEY, 1)
        self.assertEqual(t3.disabled, {}, "空になったキーは持たない")
        t4 = ContinueTable.with_toggled(t3, ContinueTable.DEFAULT, ALT, 151)
        self.assertEqual(t4.default[ALT], frozenset({150, 151}))
        t5 = ContinueTable.with_all(t4, ContinueTable.DEFAULT, {KEY: {4}, ALT: set()})
        self.assertEqual(t5.default, {KEY: frozenset({4})})


class TestCurrent(unittest.TestCase):
    def test_set_current_replaces_the_shared_store(self):
        self.addCleanup(ContinueTable.set_current, Store())
        store = _store({KEY: [1]})
        ContinueTable.set_current(store)
        self.assertIs(ContinueTable.current(), store)


class TestIconData(unittest.TestCase):
    """assets/icons・assets/data/terror_ids.json・assets/data/continue_slots.json の中身"""

    @classmethod
    def setUpClass(cls):
        import ContinueTableGUI
        cls.gui = ContinueTableGUI
        root = REPO_ROOT / "assets"
        cls.icons = json.loads((root / "icons" / "icons.json").read_text(encoding="utf-8"))
        cls.slots = json.loads((root / "data" / "continue_slots.json").read_text(encoding="utf-8"))

    def test_every_coordinate_is_inside_its_sheet(self):
        cell = self.icons["cell"]
        sheets = {}
        for name, file in self.icons["sheets"].items():
            data = np.fromfile(str(REPO_ROOT / "assets" / "icons" / file), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
            self.assertIsNotNone(img, file)
            sheets[name] = img
        self.assertTrue(TerrorIds.ids())
        for tid in TerrorIds.ids():
            sheet, col, row = TerrorIds.icon(tid)
            h, w = sheets[sheet].shape[:2]
            self.assertLessEqual((col + 1) * cell, w, f"{tid}: {sheet} の幅")
            self.assertLessEqual((row + 1) * cell, h, f"{tid}: {sheet} の高さ")

    def test_icons_json_only_has_how_to_cut_the_sheets(self):
        self.assertEqual(set(self.icons), {"cell", "sheets", "note"}, "アイコンの位置・名前は terror_ids.json にある")

    def test_every_slot_has_an_icon(self):
        """Glorbo（4012）も「?」の絵を持つ（GK）。Self Inserts (Bloodthirsty) は Bloodthirsty の絵"""
        slots = {i for _key, ids in self.slots["rounds"] for i in ids}
        missing = sorted(i for i in slots if TerrorIds.icon(i) is None)
        self.assertEqual(missing, [])

    def test_glorbo_is_the_question_mark_and_self_inserts_bloodthirsty_borrows_the_bloodthirsty_one(self):
        self.assertEqual(TerrorIds.icon(TerrorIds.GLORBO), ("terrors", 0, 0))
        self.assertEqual(TerrorIds.icon(TerrorIds.SELF_INSERTS_BLOODTHIRSTY),
                         TerrorIds.icon(TerrorIds.BLOODTHIRSTY_CREATURE))
        self.assertEqual(self.gui.terror_name(TerrorIds.GLORBO), "Glorbo")
        self.assertEqual(self.gui.terror_name(TerrorIds.SELF_INSERTS_BLOODTHIRSTY), "Self Inserts (Bloodthirsty)")

    def test_every_terror_in_the_table_has_a_picture(self):
        self.assertEqual(len(TerrorIds.ids()), 330)
        self.assertEqual(sum(1 for i in TerrorIds.ids() if TerrorIds.icon(i) is not None), 330)
        for tid in (3056, 3058, 4003, 4004, 4005, 4006, 4007, 4008, 4010, 4012, 4013):
            self.assertIsNotNone(TerrorIds.icon(tid), tid)

    def test_the_names_come_from_the_table(self):
        self.assertEqual(self.gui.terror_name(3056), "The Navigator")
        self.assertEqual(self.gui.terror_name(TerrorIds.RIFT_MONSTERS), "Rift Monsters")
        self.assertEqual(self.gui.terror_name(TerrorIds.NEO_PILOT), "Neo Pilot")
        self.assertEqual(self.gui.terror_name(TerrorIds.FOXY), "Evil Purple Foxy")
        self.assertEqual(self.gui.terror_name(TerrorIds.BLOODTHIRSTY_CREATURE), "Wild Yet Bloodthirsty Creature")
        self.assertEqual(self.gui.terror_name(TerrorIds.BLOODTHIRSTY_CREATURE, "Unbound/アンバウンド"),
                         "Pack of Wild Yet Bloodthirsty Creatures")
        self.assertEqual(self.gui.terror_name(TerrorIds.BLOODTHIRSTY_CREATURE, "Classic/クラシック"),
                         "Wild Yet Bloodthirsty Creature")

    def test_the_screen_has_special_moon_as_one_tab(self):
        keys = [key for key, _ids in self.gui.load_rounds()]
        self.assertEqual(len(keys), 17)
        self.assertIn(SPECIAL, keys)
        self.assertEqual(self.gui.round_display_name(SPECIAL), "Special/Moon")
        for key in keys:
            if key != SPECIAL:
                self.assertEqual(self.gui.round_display_name(key), key.split("/")[0])
        self.assertNotIn("Moon", [self.gui.round_display_name(k) for k in keys], "Moon のタブは Special/Moon になった")

    def test_the_slot_file_has_the_seventeen_rounds_with_distinct_keys(self):
        keys = [key for key, _ids in self.slots["rounds"]]
        self.assertEqual(len(keys), 17)
        self.assertEqual(len(set(keys)), 17)
        self.assertEqual(self.gui.load_rounds()[0][0], KEY)
        self.assertEqual(self.slots["version"], 2)
        for key in keys:
            self.assertIn(key, set(MatchTNL.LOG_TO_TNL.values()) | {SPECIAL}, key)
        for key in (MatchTNL.LOG_TO_TNL["Mystic Moon"], MatchTNL.LOG_TO_TNL["Cold Night"]):
            self.assertEqual(key, SPECIAL)

    def test_an_icon_is_cut_and_enlarged_to_a_png(self):
        png = self.gui.icon_png(0)
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(img.shape[:2], (self.gui.ICON_SIZE, self.gui.ICON_SIZE))
        self.assertIsNotNone(self.gui.icon_png(3056), "The Navigator は GI で絵が付いた")
        self.assertIsNotNone(self.gui.icon_png(TerrorIds.GLORBO), "Glorbo は GK で「?」の絵が付いた")
        self.assertIsNone(self.gui.icon_png(99999), "絵が無い番号は None（名前の札になる）")

    def test_names_come_from_the_table_or_the_number(self):
        self.assertEqual(self.gui.terror_name(0), "Huggy")
        self.assertEqual(self.gui.terror_name(1000), "Decayed Sponge")
        self.assertEqual(self.gui.terror_name(2000), "Guidance & The Booboo's")
        self.assertEqual(self.gui.terror_name(99999), "#99999")
        self.assertEqual(self.gui.round_display_name(KEY), "Classic")


class TestBuildArguments(unittest.TestCase):
    def test_the_icons_and_the_slot_file_are_in_build_and_main_in_the_same_order(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_for_gh_test", REPO_ROOT / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        data = [a for a in build.BASE_ARGS if a.startswith("--include-data")]
        self.assertIn("--include-data-dir=assets/icons=assets/icons", data)
        self.assertIn("--include-data-files=assets/data/continue_slots.json=assets/data/continue_slots.json",
                      data)
        main = SRC_DIR.joinpath("main.py").read_text(encoding="utf-8")
        line = next(l for l in main.splitlines() if l.startswith("python -m nuitka src/main.py"))
        self.assertEqual([a for a in line.split() if a.startswith("--include-data")], data)


# ═══════════════════════════════════════════════
#  LogMonitor
# ═══════════════════════════════════════════════
class _MonitorCase(_TableCase):
    def setUp(self):
        super().setUp()
        SharedState.set_list_source("host")             # 続行リストは渡したもの（ほかのテストと同じ）
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.continue_round_reset)
        for p in (patch.object(ConnectDB, "register_round"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_continue_start"),
                  patch.object(SharedState, "continue_round_start"),
                  patch.object(LogMonitor.threading, "Thread")):
            p.start()
            self.addCleanup(p.stop)

    def _monitor(self, keep_on, round_type="Classic", ids=(7,), instance_type=config.INSTANCE_PRIVATE,
                 hands_free=False, wins=0, window_idx=1):
        SharedState.set_hands_free(hands_free)
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on, lambda _m: None, window_idx=window_idx)
        st = monitor.st
        st.instance_type = instance_type
        st.in_round = True
        st.round_type = round_type
        st.terror_ids = list(ids)
        st.open_special_round_wins = wins
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._focus_for_freeze = MagicMock()
        return monitor

    @staticmethod
    def _result_of(monitor):
        st = monitor.st
        return LogMonitor.LogMonitor._outcome(monitor._plan(st.round_type, st.terror_ids, False))

    def _judgement_line(self, monitor):
        lines = [m for m in monitor.logs if "判定:" in m]
        self.assertEqual(len(lines), 1, monitor.logs)
        return lines[0]


class TestWindowList(_MonitorCase):
    def test_the_disabled_table_makes_a_listed_terror_skip(self):
        monitor = self._monitor({KEY: {7}})
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_store({KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_the_default_table_makes_an_unlisted_terror_continue(self):
        monitor = self._monitor({KEY: {1}})
        self.assertEqual(self._result_of(monitor), "skip")
        ContinueTable.set_current(_store(None, {KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_the_default_table_wins_over_the_disabled_table(self):
        monitor = self._monitor({KEY: {7}})
        ContinueTable.set_current(_store({KEY: [7]}, {KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_a_turned_off_table_does_nothing(self):
        monitor = self._monitor({KEY: {7}})
        ContinueTable.set_current(_store({KEY: [7]}, None, use_disabled=False))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_a_terror_removed_from_the_host_list_comes_back_with_the_default_table(self):
        """生存済みで主催リストから消えた Alternate（キーはあるが空）を、デフォ続行で続行にする"""
        monitor = self._monitor({ALT: set()}, round_type="Alternate", ids=[flow_id(150)])
        self.assertEqual(self._result_of(monitor), "skip")
        ContinueTable.set_current(_store(None, {ALT: [own(150)]}))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_it_also_applies_to_the_host_list_of_a_tab(self):
        monitor = self._monitor({})
        monitor._window_tab = lambda: {"keepOn": {KEY: {7}}}
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_store({KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_it_also_applies_to_the_merged_per_person_wishes(self):
        monitor = self._monitor({})
        monitor._effective_wishes = lambda: {"a": {KEY: {7}}, "b": {KEY: {8}}}
        ContinueTable.set_current(_store({KEY: [7]}, {KEY: [9]}))
        self.assertEqual(monitor._keep_on(), {KEY: {8, 9}})

    def test_the_multi_terror_rule_uses_the_overlaid_list(self):
        monitor = self._monitor({"Bloodbath/ブラッドバス": {7, 8}}, round_type="Bloodbath", ids=[7, 8, 9])
        ContinueTable.set_current(_store({"Bloodbath/ブラッドバス": [7]}))
        self.assertEqual(self._result_of(monitor), "continue", "1 体が続行ならよい")
        ContinueTable.set_current(_store({"Bloodbath/ブラッドバス": [7, 8]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_the_special_moon_key_is_overlaid_too(self):
        monitor = self._monitor({SPECIAL: {own(196)}}, round_type="Mystic Moon", ids=[flow_id(196)])
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_store({SPECIAL: [own(196)]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_the_stored_list_is_not_touched(self):
        base = {KEY: {7}}
        monitor = self._monitor(base)
        ContinueTable.set_current(_store({KEY: [7]}, {KEY: [9]}))
        monitor._keep_on()
        self.assertEqual(base, {KEY: {7}})
        self.assertEqual(monitor.keepOn_set, {KEY: {7}})


class TestGroupInstancesAreNotOverlaid(_MonitorCase):
    def test_hoshiimo_and_yakiimo_get_the_list_as_it_is(self):
        for itype in sorted(GroupRound.GROUP_INSTANCES):
            base = {KEY: {7}}
            monitor = self._monitor(base, instance_type=itype)
            ContinueTable.set_current(_store({KEY: [7]}, {KEY: [9]}))
            self.assertIs(monitor._keep_on(), base, itype)

    def test_the_decision_in_a_group_ignores_the_tables(self):
        for itype in sorted(GroupRound.GROUP_INSTANCES):
            monitor = self._monitor({KEY: {7}}, instance_type=itype)
            before = monitor._list_plan([7], False)
            ContinueTable.set_current(_store({KEY: [7]}))
            self.assertEqual(monitor._list_plan([7], False), before, itype)
            self.assertTrue(monitor._list_plan([7], False)[1], itype)
            ContinueTable.set_current(_store(None, {KEY: [8]}))
            self.assertFalse(monitor._list_plan([8], False)[1], itype)

    def test_a_private_window_is_overlaid(self):
        monitor = self._monitor({KEY: {7}}, instance_type=config.INSTANCE_PRIVATE)
        ContinueTable.set_current(_store({KEY: [7]}))
        self.assertEqual(monitor._keep_on(), {KEY: set()})


class TestOtherRulesStayAsTheyAre(_MonitorCase):
    def test_a_hands_free_window_after_three_wins_still_skips_a_default_continue_terror(self):
        monitor = self._monitor({}, hands_free=True, wins=config.OPEN_SPECIAL_ROUND_TARGET_WINS)
        ContinueTable.set_current(_store(None, {KEY: [7]}))
        plan = monitor._plan("Classic", [7], False)
        self.assertEqual(plan[0], "hands_free", plan)
        self.assertEqual(self._result_of(monitor), "skip")

    def test_the_list_missing_check_looks_at_the_list_before_the_overlay(self):
        """主催リストの希望が 1 つも無い窓は、デフォ続行があっても「リストが無いので止める」"""
        monitor = self._monitor({}, instance_type=config.INSTANCE_PRIVATE)
        monitor.lists.wishes = {"someone": {KEY: {1}}}
        monitor.lists.participants = {"someone"}
        monitor.st.players_known = True
        monitor._present_names = lambda: {"nobody"}
        ContinueTable.set_current(_store(None, {KEY: [7]}))
        self.assertTrue(monitor._wishes_missing())

    def test_all_continue_rounds_follow_the_overlaid_list_and_otherwise_stay(self):
        monitor = self._monitor({KEY: {7}})
        monitor.cfg.continue_rounds = {"Classic"}
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_store({KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "quiet", "全続行はそのまま（続行リストで続行にならなければ全続行）")


class TestJudgementLabel(_MonitorCase):
    def test_default_continue_label(self):
        monitor = self._monitor({KEY: {1}})
        ContinueTable.set_current(_store(None, {KEY: [7]}))
        monitor._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】（デフォ続行）"),
                        monitor.logs)

    def test_disabled_label(self):
        monitor = self._monitor({KEY: {7}})
        ContinueTable.set_current(_store({KEY: [7]}))
        monitor._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(monitor).endswith("【スキップ】（続行無効）"),
                        monitor.logs)

    def test_no_label_when_the_overlay_changed_nothing(self):
        for base, expect in (({KEY: {7}}, "【プレイ】"), ({KEY: {1}}, "【スキップ】")):
            monitor = self._monitor(base)
            ContinueTable.set_current(_store({KEY: [99]}, {KEY: [98]}))
            monitor._decide_with_keep_on_set("Classic")
            self.assertTrue(self._judgement_line(monitor).endswith(expect), monitor.logs)

    def test_no_label_without_a_table(self):
        monitor = self._monitor({KEY: {7}})
        monitor._decide_with_keep_on_set("Classic")
        self.assertNotIn("（", self._judgement_line(monitor))

    def test_no_label_in_a_group_instance(self):
        monitor = self._monitor({KEY: {7}}, instance_type=config.INSTANCE_HOSHIIMO)
        ContinueTable.set_current(_store({KEY: [7]}))
        monitor._decide_with_keep_on_set("Classic")
        self.assertNotIn("（続行無効）", self._judgement_line(monitor))

    def test_no_label_when_another_rule_made_the_opposite_result(self):
        """続行無効で外れても、3クラ解放（DTM）で続行になったときは「続行無効」と書かない"""
        dtm = config.DTM_ID
        monitor = self._monitor({KEY: {dtm}}, ids=[dtm])
        monitor.cfg.cancel_afk = True
        ContinueTable.set_current(_store({KEY: [dtm]}))
        monitor._decide_with_keep_on_set("Classic")
        line = self._judgement_line(monitor)
        self.assertIn("【プレイ(DTM/Waldo)】", line)
        self.assertNotIn("（続行無効）", line)



class TestPerWindowPresets(_MonitorCase):
    """GL: 窓ごとに選んだプリセットで判定する（窓 1 は A／X、窓 2 は B／Y。_two_presets）"""

    def setUp(self):
        super().setUp()
        ContinueTable.set_current(_two_presets())

    def test_two_windows_decide_the_same_terror_differently(self):
        # 続行リストは 7 も 8 も続行。窓 1 の続行無効 A は 7 を、窓 2 の B は 8 を外す
        one = self._monitor({KEY: {7, 8}}, ids=[7], window_idx=1)
        two = self._monitor({KEY: {7, 8}}, ids=[7], window_idx=2)
        self.assertEqual(self._result_of(one), "skip")
        self.assertEqual(self._result_of(two), "continue")
        one, two = (self._monitor({KEY: {7, 8}}, ids=[8], window_idx=i) for i in (1, 2))
        self.assertEqual((self._result_of(one), self._result_of(two)), ("continue", "skip"))

    def test_the_default_preset_also_follows_the_window(self):
        one = self._monitor({KEY: {1}}, ids=[10], window_idx=1)
        two = self._monitor({KEY: {1}}, ids=[10], window_idx=2)
        self.assertEqual(self._result_of(one), "skip", "窓 1 のデフォ続行 X は 9")
        self.assertEqual(self._result_of(two), "continue", "窓 2 のデフォ続行 Y は 10")

    def test_the_judgement_line_label_differs_per_window(self):
        one = self._monitor({KEY: {7, 8}}, ids=[7], window_idx=1)
        two = self._monitor({KEY: {7, 8}}, ids=[7], window_idx=2)
        one._decide_with_keep_on_set("Classic")
        two._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(one).endswith("【スキップ】（続行無効）"), one.logs)
        self.assertTrue(self._judgement_line(two).endswith("【プレイ】"), two.logs)
        one, two = (self._monitor({KEY: {1}}, ids=[10], window_idx=i) for i in (1, 2))
        one._decide_with_keep_on_set("Classic")
        two._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(one).endswith("【スキップ】"), one.logs)
        self.assertTrue(self._judgement_line(two).endswith("【プレイ】（デフォ続行）"), two.logs)

    def test_none_for_a_window_turns_that_table_off_for_that_window_only(self):
        store = ContinueTable.with_choice(_two_presets(), 1, DIS, None)
        ContinueTable.set_current(store)
        one = self._monitor({KEY: {7, 8}}, ids=[7], window_idx=1)
        self.assertEqual(self._result_of(one), "continue", "窓 1 の続行無効は「なし」")
        ContinueTable.set_current(ContinueTable.with_choice(store, 2, DIS, "A"))
        two = self._monitor({KEY: {7, 8}}, ids=[7], window_idx=2)
        self.assertEqual(self._result_of(two), "skip", "窓 2 は A に変えた")

    def test_a_window_that_chose_nothing_uses_no_table(self):
        store = Store(disabled_presets={BASE: _body({KEY: [7]}), "A": {}}, windows={1: {DIS: "A"}})
        ContinueTable.set_current(store)
        self.assertEqual(self._result_of(self._monitor({KEY: {7}}, window_idx=1)), "continue")
        self.assertEqual(self._result_of(self._monitor({KEY: {7}}, window_idx=3)), "continue", "選んでいない窓は「なし」")

    def test_a_change_of_the_choice_reaches_a_running_monitor(self):
        monitor = self._monitor({KEY: {7, 8}}, ids=[7], window_idx=2)
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(ContinueTable.with_choice(ContinueTable.current(), 2, DIS, "A"))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_group_instances_still_never_overlay_in_any_window(self):
        for itype in sorted(GroupRound.GROUP_INSTANCES):
            for idx in (1, 2):
                base = {KEY: {7}}
                self.assertIs(self._monitor(base, instance_type=itype, window_idx=idx)._keep_on(), base)


class TestPerWindowSelfInserts(unittest.TestCase):
    """Self Inserts (Bloodthirsty) の「続行無効の 4013」もその窓のプリセットで決まる"""

    UNBOUND = "Unbound/アンバウンド"
    SLOT = TerrorIds.SELF_INSERTS_BLOODTHIRSTY
    SELF_INSERTS = config.SELF_INSERTS_ID

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        self.addCleanup(ContinueTable.set_current, Store())
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)
        ContinueTable.set_current(Store(
            disabled_presets={"有": _body({self.UNBOUND: [self.SLOT]}), "無": {}},
            windows={1: {DIS: "有"}, 2: {DIS: "無"}, 3: {DIS: None}}))

    def _monitor(self, window_idx, instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", skip_rounds={"Unbound"})
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=window_idx)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Unbound"
        monitor.st.terror_ids = [self.SELF_INSERTS]
        monitor.st.bloodthirsty_creature_variant = True
        monitor._keep_on = lambda: {}
        return monitor

    def test_only_the_window_with_the_slot_stops_the_forced_continue(self):
        one, two, none = (self._monitor(i) for i in (1, 2, 3))
        self.assertTrue(one._self_inserts_table_disabled())
        self.assertFalse(two._self_inserts_table_disabled())
        self.assertFalse(none._self_inserts_table_disabled(), "「なし」の窓は重ねない")
        self.assertFalse(one._list_plan([self.SELF_INSERTS], True)[1], "窓 1: Self Inserts がリストに無い → 自爆")
        self.assertTrue(two._list_plan([self.SELF_INSERTS], True)[1], "窓 2: 今までどおり必ず続行")
        self.assertTrue(none._list_plan([self.SELF_INSERTS], True)[1])
        self.assertTrue(one._should_skip_by_round(), "窓 1 は自爆指定が効く")
        self.assertFalse(two._should_skip_by_round())

    def test_group_instances_ignore_the_presets(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            self.assertFalse(self._monitor(1, itype)._self_inserts_table_disabled(), itype)


# ── GM: 所持アイテムで続行表のプリセットを切り替える ─────────────────────
KEEP = ContinueTable.KEEP
Rule = ContinueTable.ItemRule
PLUSH = 70                      # item.json の Guidance Plush
OTHER_ITEM = 5                  # item.json の Radar


def _rule_store(*rules, windows=None) -> Store:
    """窓 1・2 は何も選んでいない（＝「なし」）。プリセットは 続行無効 BASE・無7・無8、デフォ続行 BASE・足9・足10"""
    return Store(
        disabled_presets={BASE: {}, "無7": _body({KEY: [7]}), "無8": _body({KEY: [8]})},
        default_presets={BASE: {}, "足9": _body({KEY: [9]}), "足10": _body({KEY: [10]})},
        windows=windows or {}, item_rules=tuple(rules))


class TestItemRuleLookup(unittest.TestCase):
    def test_the_first_matching_rule_wins_and_unmatched_items_have_none(self):
        store = _rule_store(Rule(PLUSH, "無7"), Rule(OTHER_ITEM, "無8"), Rule(PLUSH, "無8"))
        self.assertEqual(ContinueTable.rule_for(store, PLUSH), Rule(PLUSH, "無7", KEEP))
        self.assertEqual(ContinueTable.rule_for(store, OTHER_ITEM).disabled, "無8")
        self.assertIsNone(ContinueTable.rule_for(store, 999))

    def test_not_holding_anything_is_item_zero_and_none_means_do_not_look(self):
        store = _rule_store(Rule(0, "無7"), Rule(PLUSH, "無8"))
        self.assertEqual(ContinueTable.rule_for(store, 0).disabled, "無7")
        self.assertIsNone(ContinueTable.rule_for(store, None), "None は見ない（0 の決まりにも当てはめない）")
        self.assertEqual(ContinueTable.table_for(store, 1, None), ContinueTable.table_for(store, 1))

    def test_a_matching_rule_overrides_the_window_choice_per_kind(self):
        store = _rule_store(Rule(PLUSH, "無7", "足10"), windows={1: {DIS: "無8", DFL: "足9"}})
        plain = ContinueTable.table_for(store, 1)
        self.assertEqual((plain.disabled, plain.default), (_body({KEY: [8]}), _body({KEY: [9]})))
        held = ContinueTable.table_for(store, 1, PLUSH)
        self.assertEqual((held.disabled, held.default), (_body({KEY: [7]}), _body({KEY: [10]})))
        other = ContinueTable.table_for(store, 1, 999)
        self.assertEqual(other, plain, "当てはまらなければ窓の選択だけ")

    def test_keep_uses_the_window_choice_for_that_kind_only(self):
        store = _rule_store(Rule(PLUSH, KEEP, "足10"), windows={1: {DIS: "無8", DFL: "足9"}, 2: {DIS: "無7"}})
        one, two = ContinueTable.table_for(store, 1, PLUSH), ContinueTable.table_for(store, 2, PLUSH)
        self.assertEqual((one.disabled, one.default), (_body({KEY: [8]}), _body({KEY: [10]})))
        self.assertEqual((two.disabled, two.default), (_body({KEY: [7]}), _body({KEY: [10]})))

    def test_none_in_a_rule_turns_that_kind_off_even_if_the_window_chose_a_preset(self):
        store = _rule_store(Rule(PLUSH, None, None), windows={1: {DIS: "無8", DFL: "足9"}})
        self.assertEqual(ContinueTable.table_for(store, 1, PLUSH), Table())
        self.assertEqual(ContinueTable.table_for(store, 1, 0).disabled, _body({KEY: [8]}))

    def test_a_rule_can_pick_a_preset_when_the_window_chose_none(self):
        store = _rule_store(Rule(PLUSH, "無7"), windows={1: {DIS: None}})
        self.assertEqual(ContinueTable.table_for(store, 1).disabled, {})
        self.assertEqual(ContinueTable.table_for(store, 1, PLUSH).disabled, _body({KEY: [7]}))

    def test_a_rule_naming_a_missing_preset_falls_back_to_the_window_choice(self):
        store = _rule_store(Rule(PLUSH, "消えた", "消えた"), windows={1: {DIS: "無8"}})
        held = ContinueTable.table_for(store, 1, PLUSH)
        self.assertEqual(held, ContinueTable.table_for(store, 1))
        self.assertFalse(ContinueTable.rule_changes_table(store, 1, PLUSH))

    def test_the_rule_is_global_so_every_window_that_holds_the_item_gets_it(self):
        store = _rule_store(Rule(PLUSH, "無7"), windows={2: {DIS: "無8"}})
        self.assertEqual(ContinueTable.table_for(store, 1, PLUSH).disabled, _body({KEY: [7]}))
        self.assertEqual(ContinueTable.table_for(store, 2, PLUSH).disabled, _body({KEY: [7]}))
        self.assertEqual(ContinueTable.table_for(store, 2, 0).disabled, _body({KEY: [8]}))

    def test_rule_changes_table_is_true_only_when_a_kind_differs_from_the_window(self):
        store = _rule_store(Rule(PLUSH, "無7"), Rule(OTHER_ITEM, BASE, KEEP), Rule(3, None), Rule(4, KEEP, KEEP),
                            windows={1: {DIS: "無8"}, 2: {DIS: "無7"}, 3: {DIS: None}})
        changes = ContinueTable.rule_changes_table
        self.assertTrue(changes(store, 1, PLUSH))
        self.assertFalse(changes(store, 2, PLUSH), "窓が同じプリセットを選んでいた")
        self.assertTrue(changes(store, 1, OTHER_ITEM), "既定へ")
        self.assertFalse(changes(store, 3, 3), "窓がもう「なし」")
        self.assertTrue(changes(store, 1, 3))
        self.assertFalse(changes(store, 1, 4), "窓のまま 2 つ")
        self.assertFalse(changes(store, 1, 999), "当てはまる決まりが無い")
        self.assertFalse(changes(store, 1, None))


class TestItemRuleOperations(unittest.TestCase):
    def test_add_appends_a_keep_rule_for_not_holding(self):
        store = ContinueTable.with_rule_added(_rule_store())
        self.assertEqual(store.item_rules, (Rule(0, KEEP, KEEP),))
        store = ContinueTable.with_rule_added(store, PLUSH, "無7", None)
        self.assertEqual(store.item_rules[1], Rule(PLUSH, "無7", None))

    def test_add_and_change_reject_unknown_preset_names(self):
        store = _rule_store(Rule(1))
        with self.assertRaises(ValueError):
            ContinueTable.with_rule_added(store, 1, "足9")        # 足9 はデフォ続行の名前
        with self.assertRaises(ValueError):
            ContinueTable.with_rule_changed(store, 0, default="無7")
        with self.assertRaises(IndexError):
            ContinueTable.with_rule_changed(store, 1, item=2)

    def test_change_replaces_only_the_given_fields_and_keeps_the_original(self):
        store = _rule_store(Rule(PLUSH, "無7", "足9"), Rule(3))
        changed = ContinueTable.with_rule_changed(store, 0, disabled=None)
        self.assertEqual(changed.item_rules, (Rule(PLUSH, None, "足9"), Rule(3)))
        changed = ContinueTable.with_rule_changed(changed, 1, item=OTHER_ITEM, default="足10")
        self.assertEqual(changed.item_rules[1], Rule(OTHER_ITEM, KEEP, "足10"))
        self.assertEqual(store.item_rules[0], Rule(PLUSH, "無7", "足9"), "元の Store は変えない")

    def test_move_swaps_with_the_neighbour_and_stays_at_the_ends(self):
        store = _rule_store(Rule(1), Rule(2), Rule(3))
        self.assertEqual([r.item for r in ContinueTable.with_rule_moved(store, 2, -1).item_rules], [1, 3, 2])
        self.assertEqual([r.item for r in ContinueTable.with_rule_moved(store, 0, +1).item_rules], [2, 1, 3])
        self.assertIs(ContinueTable.with_rule_moved(store, 0, -1), store)
        self.assertIs(ContinueTable.with_rule_moved(store, 2, +1), store)
        self.assertIs(ContinueTable.with_rule_moved(store, 9, -1), store)

    def test_delete_removes_one_and_ignores_a_bad_index(self):
        store = _rule_store(Rule(1), Rule(2), Rule(3))
        self.assertEqual([r.item for r in ContinueTable.with_rule_deleted(store, 1).item_rules], [1, 3])
        self.assertIs(ContinueTable.with_rule_deleted(store, 3), store)

    def test_shadowed_rules_marks_the_ones_under_the_same_item(self):
        store = _rule_store(Rule(PLUSH), Rule(0), Rule(PLUSH), Rule(0), Rule(5))
        self.assertEqual(ContinueTable.shadowed_rules(store), [False, False, True, True, False])
        self.assertEqual(ContinueTable.shadowed_rules(_rule_store()), [])

    def test_preset_operations_keep_the_rules(self):
        store = _rule_store(Rule(PLUSH, "無7", "足9"))
        store = ContinueTable.with_new_preset(store, DIS, "新")
        store = ContinueTable.with_preset_body(store, DIS, "新", {KEY: [1]})
        store = ContinueTable.with_choice(store, 1, DIS, "新")
        self.assertEqual(store.item_rules, (Rule(PLUSH, "無7", "足9"),))


class TestItemRuleSaveLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "continue_table.json"

    def test_round_trip_keeps_keep_none_and_names_in_order(self):
        store = _rule_store(Rule(PLUSH, KEEP, "足9"), Rule(0, None, None), Rule(OTHER_ITEM, "無7", KEEP),
                            Rule(PLUSH, "無8"))
        self.assertTrue(ContinueTable.save(store, self.path))
        self.assertEqual(ContinueTable.load(self.path), store)

    def test_the_file_omits_keep_and_writes_null_for_none(self):
        store = _rule_store(Rule(PLUSH, KEEP, "足9"), Rule(0, None, KEEP))
        ContinueTable.save(store, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["item_rules"], [{"item": PLUSH, "default": "足9"}, {"item": 0, "disabled": None}])

    def test_a_file_without_rules_has_none(self):
        self.path.write_text(json.dumps({"version": 2, "disabled_presets": {BASE: {}},
                                         "default_presets": {BASE: {}}, "windows": {}}), encoding="utf-8")
        self.assertEqual(ContinueTable.load(self.path).item_rules, ())

    def test_bad_rows_are_skipped_and_good_rows_kept(self):
        self.path.write_text(json.dumps({
            "version": 2, "disabled_presets": {BASE: {}, "無7": {}}, "default_presets": {BASE: {}},
            "item_rules": [{"item": 70, "disabled": "無7"}, "no", {"disabled": "無7"}, {"item": "70"},
                           {"item": True}, {"item": -1}, {"item": 70.5}, {"item": 5, "disabled": 3},
                           {"item": 6, "default": ["x"]}, None, {"item": 0, "default": None}]}), encoding="utf-8")
        self.assertEqual(ContinueTable.load(self.path).item_rules,
                         (Rule(70, "無7", KEEP), Rule(0, KEEP, None)))

    def test_item_rules_of_the_wrong_type_are_ignored(self):
        self.path.write_text(json.dumps({"version": 2, "item_rules": {"item": 1}}), encoding="utf-8")
        self.assertEqual(ContinueTable.load(self.path).item_rules, ())

    def test_a_rule_naming_a_missing_preset_loads_as_keep_with_one_log_line(self):
        self.path.write_text(json.dumps({
            "version": 3, "disabled_presets": {BASE: {}, "無7": {}}, "default_presets": {BASE: {}},
            "item_rules": [{"item": 70, "disabled": "消えた", "default": "これも"},
                           {"item": 5, "disabled": "無7", "default": "なし"}]}), encoding="utf-8")
        with patch.object(DebugLog, "write") as write:
            rules = ContinueTable.load(self.path).item_rules
        self.assertEqual(rules, (Rule(70, KEEP, KEEP), Rule(5, "無7", KEEP)))
        write.assert_called_once()
        self.assertIn("続行表", write.call_args.args[0])

    def test_no_log_line_when_every_name_exists(self):
        ContinueTable.save(_rule_store(Rule(PLUSH, "無7", "足9")), self.path)
        with patch.object(DebugLog, "write") as write:
            ContinueTable.load(self.path)
        write.assert_not_called()


class TestItemRulesFollowPresets(unittest.TestCase):
    def test_rename_follows_in_the_same_kind_only(self):
        store = Store(disabled_presets={"P": {}, "Q": {}}, default_presets={"P": {}},
                      item_rules=(Rule(1, "P", "P"), Rule(2, "Q", None), Rule(3, KEEP, KEEP)))
        store = ContinueTable.with_renamed_preset(store, DIS, "P", "R")
        self.assertEqual(store.item_rules, (Rule(1, "R", "P"), Rule(2, "Q", None), Rule(3)))
        store = ContinueTable.with_renamed_preset(store, DFL, "P", "S")
        self.assertEqual(store.item_rules[0], Rule(1, "R", "S"))

    def test_delete_turns_rules_naming_it_into_keep(self):
        store = Store(disabled_presets={BASE: {}, "A": {}, "B": {}}, default_presets={BASE: {}, "A": {}},
                      item_rules=(Rule(1, "A", "A"), Rule(2, "B", None)))
        after = ContinueTable.with_deleted_preset(store, DIS, "A")
        self.assertEqual(after.item_rules, (Rule(1, KEEP, "A"), Rule(2, "B", None)))
        after = ContinueTable.with_deleted_preset(after, DFL, "A")
        self.assertEqual(after.item_rules, (Rule(1, KEEP, KEEP), Rule(2, "B", None)))

    def test_renaming_to_the_same_name_changes_nothing(self):
        store = _rule_store(Rule(PLUSH, "無7"))
        self.assertEqual(ContinueTable.with_renamed_preset(store, DIS, "無7", "無7"), store)

    def test_the_keep_label_cannot_be_a_preset_name(self):
        with self.assertRaises(ValueError):
            ContinueTable.with_new_preset(_rule_store(), DIS, ContinueTable.KEEP_LABEL)


class TestItemRuleJudgement(_MonitorCase):
    """同じテラーでも、持っているアイテムで窓ごとに判定が変わる"""

    def _held(self, item, window_idx=1, ids=(7,), **kw):
        monitor = self._monitor({KEY: {7, 8}}, ids=ids, window_idx=window_idx, **kw)
        monitor.st.held_item_id = item
        return monitor

    def setUp(self):
        super().setUp()
        patcher = patch.object(config, "ITEMS", {PLUSH: ItemCatalog.Item("Guidance Plush", "Event", True)})
        patcher.start()                  # テストは本物の item.json を使わない（support）
        self.addCleanup(patcher.stop)
        ContinueTable.set_current(_rule_store(Rule(PLUSH, "無7"), Rule(0, KEEP, "足10")))

    def test_two_windows_decide_the_same_terror_by_what_they_hold(self):
        plush, empty = self._held(PLUSH, 1), self._held(0, 2)
        self.assertEqual(self._result_of(plush), "skip", "Plush を持つ窓は 無7 で 7 を外す")
        self.assertEqual(self._result_of(empty), "continue", "持っていない窓は 7 のまま")
        self.assertEqual(self._result_of(self._held(OTHER_ITEM, 3)), "continue", "当てはまらない")

    def test_the_decision_follows_the_item_when_it_changes(self):
        monitor = self._held(PLUSH)
        self.assertEqual(self._result_of(monitor), "skip")
        monitor.st.held_item_id = 0            # ロストした
        self.assertEqual(self._result_of(monitor), "continue")

    def test_the_default_kind_of_the_not_holding_rule_applies(self):
        self.assertEqual(self._result_of(self._held(0, ids=(10,))), "continue", "足10 が重なる")
        self.assertEqual(self._result_of(self._held(OTHER_ITEM, ids=(10,))), "skip")

    def test_the_judgement_line_ends_with_the_item_rule_label(self):
        plush = self._held(PLUSH)
        plush._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(plush).endswith("【スキップ】（続行無効）〔Guidance Plush の決まり〕"),
                        plush.logs)

    def test_the_label_is_added_even_when_the_result_did_not_change(self):
        empty = self._held(0)
        empty._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(empty).endswith("【プレイ】〔持っていない の決まり〕"), empty.logs)

    def test_no_label_when_no_rule_matches_or_it_changes_nothing(self):
        other = self._held(OTHER_ITEM)
        other._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(other).endswith("【プレイ】"), other.logs)
        ContinueTable.set_current(_rule_store(Rule(PLUSH, BASE, KEEP), windows={1: {DIS: BASE}}))      # 窓も BASE
        plush = self._held(PLUSH)
        plush._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(plush).endswith("【プレイ】"), plush.logs)

    def test_an_item_missing_from_the_table_is_shown_by_id(self):
        ContinueTable.set_current(_rule_store(Rule(9999, "無7")))
        monitor = self._held(9999)
        monitor._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(monitor).endswith("〔#9999 の決まり〕"), monitor.logs)

    def test_the_item_label_follows_the_table_label_in_the_same_line(self):
        ContinueTable.set_current(_rule_store(Rule(PLUSH, KEEP, "足9")))
        monitor = self._held(PLUSH, ids=(9,))
        monitor._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】（デフォ続行）〔Guidance Plush の決まり〕"),
                        monitor.logs)

    def test_group_instances_do_not_use_the_rules(self):
        for itype in sorted(GroupRound.GROUP_INSTANCES):
            base = {KEY: {7}}
            monitor = self._monitor(base, instance_type=itype)
            monitor.st.held_item_id = PLUSH
            self.assertIs(monitor._keep_on(), base, itype)
            monitor.logs.clear()
            monitor._decide_with_keep_on_set("Classic")
            self.assertNotIn("の決まり", self._judgement_line(monitor), itype)

    def test_hands_free_after_three_wins_still_skips_whatever_the_rule_says(self):
        ContinueTable.set_current(_rule_store(Rule(PLUSH, KEEP, "足9")))
        monitor = self._monitor({}, ids=(9,), hands_free=True, wins=config.OPEN_SPECIAL_ROUND_TARGET_WINS)
        monitor.st.held_item_id = PLUSH
        self.assertEqual(self._result_of(monitor), "skip")


class TestItemRuleSelfInserts(unittest.TestCase):
    """Self Inserts (Bloodthirsty) の「続行無効の 4013」も、決まりで選ばれたプリセットで見る"""

    UNBOUND = "Unbound/アンバウンド"
    SLOT = TerrorIds.SELF_INSERTS_BLOODTHIRSTY

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        self.addCleanup(ContinueTable.set_current, Store())
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)
        ContinueTable.set_current(Store(
            disabled_presets={"有": _body({self.UNBOUND: [self.SLOT]}), "無": {}},
            windows={1: {DIS: "無"}, 2: {DIS: "有"}},
            item_rules=(Rule(PLUSH, "有"), Rule(0, "無"))))

    def _monitor(self, window_idx, held, instance_type=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=window_idx)
        monitor.st.instance_type = instance_type
        monitor.st.held_item_id = held
        return monitor

    def test_the_rule_selected_preset_decides(self):
        self.assertTrue(self._monitor(1, PLUSH)._self_inserts_table_disabled(), "窓は 無 だが Plush の決まりは 有")
        self.assertFalse(self._monitor(2, 0)._self_inserts_table_disabled(), "窓は 有 だが持っていない決まりは 無")
        self.assertFalse(self._monitor(1, OTHER_ITEM)._self_inserts_table_disabled(), "決まり無し → 窓の 無")
        self.assertTrue(self._monitor(2, OTHER_ITEM)._self_inserts_table_disabled(), "決まり無し → 窓の 有")

    def test_group_instances_ignore_the_rules(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            self.assertFalse(self._monitor(1, PLUSH, itype)._self_inserts_table_disabled(), itype)


class TestMigrationToV4(unittest.TestCase):
    """v3 までの特別なプリセット「既定」の片づけ（初めて読むとき 1 回だけ。v4 にする）"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "continue_table.json"
        patcher = patch.object(DebugLog, "write")
        self.log = patcher.start()
        self.addCleanup(patcher.stop)

    def _load(self, raw):
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        return ContinueTable.load(self.path)

    def _raw(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _lines(self):
        return [c.args[0] for c in self.log.call_args_list]

    def test_the_users_file_loses_only_the_two_empty_base_presets(self):
        emeco = {KEY: [3, 5, 7, 9, 11, 13, 15]}
        store = self._load({
            "version": 3, "disabled_presets": {"既定": {}},
            "default_presets": {"既定": {}, "エメコ": emeco},
            "windows": {"1": {"default": "エメコ"}, "2": {"default": "エメコ"}},
            "item_rules": [{"item": 29, "default": "エメコ"}]})
        self.assertEqual(store.disabled_presets, {})
        self.assertEqual(store.default_presets, {"エメコ": _body(emeco)})
        self.assertEqual(store.windows, {1: {DFL: "エメコ"}, 2: {DFL: "エメコ"}})
        self.assertEqual(store.item_rules, (Rule(29, KEEP, "エメコ"),))
        for idx in (1, 2, 3):
            self.assertIsNone(ContinueTable.choice_for(store, idx, DIS), "続行無効は「なし」")
        self.assertEqual(ContinueTable.choice_for(store, 1, DFL), "エメコ")
        self.assertIsNone(ContinueTable.choice_for(store, 3, DFL), "選んでいなかった窓は「なし」（空の既定と同じ結果）")
        raw = self._raw()
        self.assertEqual(raw["version"], 4)
        self.assertEqual((raw["disabled_presets"], list(raw["default_presets"])), ({}, ["エメコ"]))
        self.assertEqual(raw["windows"], {"1": {"default": "エメコ"}, "2": {"default": "エメコ"}})
        self.assertEqual(raw["item_rules"], [{"item": 29, "default": "エメコ"}])
        self.assertEqual(os.listdir(self.tmp.name), [self.path.name], ".bak などは作らない")

    def test_a_base_preset_with_content_becomes_the_default_named_preset_and_everything_follows(self):
        store = self._load({
            "version": 3, "disabled_presets": {"既定": {KEY: [7]}, "A": {}},
            "default_presets": {"既定": {KEY: [9]}},
            "windows": {"1": {"disabled": "既定"}, "2": {"disabled": "A", "default": None}},
            "item_rules": [{"item": 5, "disabled": "既定", "default": "既定"}, {"item": 6, "disabled": None}]})
        self.assertEqual(list(store.disabled_presets), ["デフォルト", "A"], "並びはそのまま")
        self.assertEqual(store.disabled_presets["デフォルト"], _body({KEY: [7]}))
        self.assertEqual(store.default_presets, {"デフォルト": _body({KEY: [9]})})
        self.assertEqual(store.windows[1][DIS], "デフォルト", "窓の選びが追う")
        self.assertEqual(store.windows[2], {DIS: "A", DFL: None}, "選んでいた窓はそのまま")
        self.assertEqual(store.item_rules, (Rule(5, "デフォルト", "デフォルト"), Rule(6, None, KEEP)), "決まりが追う")

    def test_windows_that_chose_nothing_keep_using_a_base_preset_that_has_content(self):
        """選んでいなかった窓は今まで「既定」を使っていた。中身が残る間は、その窓の選びとして書いて使い続ける"""
        store = self._load({"version": 3, "disabled_presets": {"既定": {KEY: [7]}}, "default_presets": {"既定": {}},
                            "windows": {"2": {"disabled": None}}})
        for idx in range(1, config.MAX_WINDOWS + 1):
            expected = None if idx == 2 else "デフォルト"
            self.assertEqual(ContinueTable.choice_for(store, idx, DIS), expected, idx)
            self.assertIsNone(ContinueTable.choice_for(store, idx, DFL), idx)

    def test_an_existing_default_named_preset_keeps_the_base_preset_as_it_is(self):
        store = self._load({"version": 3, "disabled_presets": {"既定": {KEY: [7]}, "デフォルト": {KEY: [8]}},
                            "default_presets": {"既定": {KEY: [9]}},
                            "windows": {"1": {"disabled": "既定"}, "2": {"disabled": "デフォルト"}}})
        self.assertEqual(list(store.disabled_presets), ["既定", "デフォルト"], "名前を変えない")
        self.assertEqual(store.disabled_presets["既定"], _body({KEY: [7]}))
        self.assertEqual(store.windows[1][DIS], "既定")
        self.assertEqual(store.windows[2][DIS], "デフォルト")
        self.assertEqual(ContinueTable.choice_for(store, 3, DIS), "既定", "選んでいなかった窓は今まで通り「既定」の中身")
        self.assertEqual(list(store.default_presets), ["デフォルト"], "もう片方の種類は別に片づく")
        self.assertTrue(any("デフォルト" in line and "既定" in line for line in self._lines()), self._lines())

    def test_the_second_load_does_nothing(self):
        self._load({"version": 3, "disabled_presets": {"既定": {}}, "default_presets": {"既定": {KEY: [9]}},
                    "windows": {}})
        first_bytes = self.path.read_bytes()
        self.log.reset_mock()
        with patch.object(ContinueTable, "save") as save, \
             patch.object(ContinueList, "persist_migration") as persist:
            again = ContinueTable.load(self.path)
        save.assert_not_called()
        persist.assert_not_called()
        self.assertEqual(self.path.read_bytes(), first_bytes)
        self.assertEqual(again.default_presets, {"デフォルト": _body({KEY: [9]})})
        self.assertEqual(self._lines(), [], "debug.log にも何も書かない")

    def test_a_v4_file_with_a_user_made_base_named_preset_is_left_alone(self):
        store = self._load({"version": 4, "disabled_presets": {"既定": {}}, "default_presets": {}, "windows": {}})
        self.assertEqual(store.disabled_presets, {"既定": {}}, "v4 のあとに作った名前は触らない")

    def test_v2_and_v1_reach_the_same_place(self):
        v2 = self._load({"version": 2, "disabled_presets": {"既定": {KEY: [5]}}, "default_presets": {"既定": {}},
                         "windows": {}})
        self.assertEqual(v2.disabled_presets, {"デフォルト": _body({KEY: [5]})})
        self.assertEqual(v2.default_presets, {})
        self.assertEqual(self._raw()["version"], 4)
        self.assertTrue((Path(self.tmp.name) / "continue_table.json.listtool.bak").exists(), "番号を直したので .bak を残す")
        self.path.unlink()
        v1 = self._load({"version": 1, "use_disabled": True, "use_default": True,
                         "disabled": {KEY: [5]}, "default": {}})
        self.assertEqual(v1.disabled_presets, {"デフォルト": _body({KEY: [5]})})
        self.assertEqual(v1.default_presets, {}, "空の表はプリセットにしない")
        self.assertEqual(self._raw()["version"], 4)

    def test_a_v1_file_with_empty_tables_gets_no_presets(self):
        store = self._load({"version": 1, "use_disabled": True, "use_default": True, "disabled": {}, "default": {}})
        self.assertEqual((store.disabled_presets, store.default_presets), ({}, {}))
        self.assertEqual(self._raw()["version"], 4)

    def test_a_v3_file_without_a_base_preset_only_gets_the_new_version(self):
        store = self._load({"version": 3, "disabled_presets": {"A": {KEY: [1]}}, "default_presets": {},
                            "windows": {"1": {"disabled": "A"}}})
        self.assertEqual(store.disabled_presets, {"A": _body({KEY: [1]})})
        self.assertEqual(store.windows, {1: {DIS: "A"}})
        self.assertEqual(self._raw()["version"], 4)
        self.assertEqual(os.listdir(self.tmp.name), [self.path.name])

    def test_a_save_that_fails_still_gives_the_migrated_store(self):
        self.path.write_text(json.dumps({"version": 3, "disabled_presets": {"既定": {}}, "default_presets": {},
                                         "windows": {}}), encoding="utf-8")
        with patch.object(ContinueTable.os, "replace", side_effect=OSError("disk")):
            store = ContinueTable.load(self.path)
        self.assertEqual(store.disabled_presets, {})
