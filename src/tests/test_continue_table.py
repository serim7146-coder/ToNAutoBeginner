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
import ContinueTable
import GroupRound
from ContinueTable import Table, Store

KEY = "Classic/クラシック"
ALT = "Alternate/オルタネイト"
SPECIAL = MatchTNL.SPECIAL_MOON_KEY
DIS, DFL = ContinueTable.DISABLED, ContinueTable.DEFAULT
BASE = ContinueTable.DEFAULT_PRESET        # 「既定」


def _body(raw) -> dict:
    return {k: frozenset(v) for k, v in (raw or {}).items()}


def _table(disabled=None, default=None) -> Table:
    return Table(disabled=_body(disabled), default=_body(default))


def _store(disabled=None, default=None, use_disabled=True, use_default=True) -> Store:
    """「既定」のプリセットだけの保存内容。use_* が False の種類は窓 1 を「なし」にする"""
    windows = {}
    if not use_disabled:
        windows.setdefault(1, {})[DIS] = None
    if not use_default:
        windows.setdefault(1, {})[DFL] = None
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

    def test_the_special_moon_slot_counts_for_classic_and_moons(self):
        self.assertEqual(ContinueTable.overlay_label({SPECIAL: {192}}, {SPECIAL: set()}, "Classic", [192]),
                         "（続行無効）")
        self.assertEqual(ContinueTable.overlay_label({}, {SPECIAL: {192}}, "Blood Moon", [192]),
                         "（デフォ続行）")
        self.assertEqual(ContinueTable.overlay_label({}, {SPECIAL: {192}}, "Fog", [192]), "",
                         "Special/Moon を見るのは Classic と Moon だけ（RoundDecision と同じ）")

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

    def test_the_file_has_the_documented_v2_shape(self):
        store = Store(disabled_presets={BASE: _body({KEY: [9, 5]})}, default_presets={BASE: _body({ALT: [164]})},
                      windows={1: {DIS: BASE, DFL: None}})
        ContinueTable.save(store, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw, {"version": 2,
                               "disabled_presets": {BASE: {KEY: [5, 9]}},
                               "default_presets": {BASE: {ALT: [164]}},
                               "windows": {"1": {"disabled": BASE, "default": None}}})

    def test_a_missing_file_is_one_empty_preset_in_each_kind(self):
        store = ContinueTable.load(self.path)
        self.assertEqual(store, Store())
        self.assertEqual(store.disabled_presets, {BASE: {}})
        self.assertEqual(store.default_presets, {BASE: {}})
        self.assertEqual(store.windows, {})

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
        self.assertEqual(store.default_presets, {BASE: {}}, "読めなければ空の「既定」")
        self.assertEqual(store.windows, {1: {DIS: "ok"}, 3: {DFL: None}})

    def test_v2_with_no_presets_gets_an_empty_base_preset(self):
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
    """v1（表が 1 組だけ）→ 続行無効・デフォ続行それぞれの「既定」のプリセット"""

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

    def test_the_old_tables_become_the_base_presets(self):
        store = self._v1()
        self.assertEqual(store.disabled_presets, {BASE: _body({KEY: [5, 9]})})
        self.assertEqual(store.default_presets, {BASE: _body({ALT: [164]})})
        self.assertEqual(store.windows, {})
        for idx in (1, 4, 8):
            table = ContinueTable.table_for(store, idx)
            self.assertEqual(table.disabled, _body({KEY: [5, 9]}), idx)
            self.assertEqual(table.default, _body({ALT: [164]}), idx)

    def test_use_disabled_false_makes_every_window_none_for_that_kind_only(self):
        store = self._v1(use_disabled=False)
        for idx in range(1, config.MAX_WINDOWS + 1):
            table = ContinueTable.table_for(store, idx)
            self.assertEqual(table.disabled, {}, idx)
            self.assertEqual(table.default, _body({ALT: [164]}), idx)
        self.assertEqual(store.disabled_presets, {BASE: _body({KEY: [5, 9]})}, "表の中身は消さない")

    def test_use_default_false_makes_every_window_none_for_the_default_kind_only(self):
        store = self._v1(use_default=False)
        for idx in range(1, config.MAX_WINDOWS + 1):
            table = ContinueTable.table_for(store, idx)
            self.assertEqual(table.disabled, _body({KEY: [5, 9]}), idx)
            self.assertEqual(table.default, {}, idx)
        self.assertEqual(store.default_presets, {BASE: _body({ALT: [164]})})

    def test_a_v1_file_without_a_version_is_migrated_too_and_bad_entries_are_dropped(self):
        self.path.write_text(json.dumps({"disabled": {KEY: [1, "x", True, 3], "bad": "no"}, "default": "no"}),
                             encoding="utf-8")
        store = ContinueTable.load(self.path)
        self.assertEqual(store.disabled_presets, {BASE: _body({KEY: [1, 3]})})
        self.assertEqual(store.default_presets, {BASE: {}})

    def test_saving_after_loading_writes_v2_and_loads_back_the_same(self):
        store = self._v1(use_disabled=False)
        ContinueTable.save(store, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 2)
        self.assertNotIn("use_disabled", raw)
        self.assertEqual(ContinueTable.load(self.path), store)


class TestTableFor(unittest.TestCase):
    def test_each_window_gets_the_two_presets_it_chose(self):
        store = _two_presets()
        one, two = ContinueTable.table_for(store, 1), ContinueTable.table_for(store, 2)
        self.assertEqual((one.disabled, one.default), (_body({KEY: [7]}), _body({KEY: [9]})))
        self.assertEqual((two.disabled, two.default), (_body({KEY: [8]}), _body({KEY: [10]})))

    def test_a_window_that_chose_nothing_uses_the_base_preset(self):
        store = Store(disabled_presets={"A": _body({KEY: [1]}), BASE: _body({KEY: [2]})},
                      default_presets={BASE: _body({KEY: [3]})}, windows={1: {DIS: "A"}})
        self.assertEqual(ContinueTable.table_for(store, 1).disabled, _body({KEY: [1]}))
        self.assertEqual(ContinueTable.table_for(store, 1).default, _body({KEY: [3]}), "種類ごとに未設定は既定")
        self.assertEqual(ContinueTable.table_for(store, 5).disabled, _body({KEY: [2]}))

    def test_without_a_base_preset_an_unset_window_uses_the_first_one(self):
        store = Store(disabled_presets={"B": _body({KEY: [1]}), "A": _body({KEY: [2]})})
        self.assertEqual(ContinueTable.table_for(store, 1).disabled, _body({KEY: [1]}))

    def test_none_means_no_table_for_that_window_and_kind(self):
        store = Store(disabled_presets={BASE: _body({KEY: [1]})}, default_presets={BASE: _body({KEY: [2]})},
                      windows={1: {DIS: None}, 2: {DFL: None}})
        self.assertEqual(ContinueTable.table_for(store, 1), Table(default=_body({KEY: [2]})))
        self.assertEqual(ContinueTable.table_for(store, 2), Table(disabled=_body({KEY: [1]})))
        self.assertEqual(ContinueTable.choice_for(store, 1, DIS), None)
        self.assertEqual(ContinueTable.choice_for(store, 3, DIS), BASE)

    def test_a_choice_naming_a_missing_preset_falls_back(self):
        store = Store(disabled_presets={BASE: _body({KEY: [1]})}, windows={1: {DIS: "消えた"}})
        self.assertEqual(ContinueTable.choice_for(store, 1, DIS), BASE)

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

    def test_delete_sends_windows_that_used_it_to_the_base_preset(self):
        store = Store(disabled_presets={BASE: _body({KEY: [1]}), "A": {}, "B": {}},
                      windows={1: {DIS: "A"}, 2: {DIS: "B"}, 3: {DIS: None}})
        store = ContinueTable.with_deleted_preset(store, DIS, "A")
        self.assertEqual(ContinueTable.preset_names(store, DIS), [BASE, "B"])
        self.assertEqual(store.windows[1], {DIS: BASE})
        self.assertEqual(store.windows[2], {DIS: "B"})
        self.assertEqual(store.windows[3], {DIS: None}, "「なし」はそのまま")

    def test_deleting_the_base_preset_sends_windows_to_the_first_one(self):
        store = Store(disabled_presets={"A": {}, BASE: {}, "B": {}}, windows={1: {DIS: BASE}})
        store = ContinueTable.with_deleted_preset(store, DIS, BASE)
        self.assertEqual(store.windows[1], {DIS: "A"})
        self.assertEqual(ContinueTable.choice_for(store, 9, DIS), "A", "選んでいなかった窓も先頭")

    def test_the_last_preset_of_a_kind_cannot_be_deleted(self):
        store = Store(disabled_presets={"A": {}}, default_presets={"X": {}, "Y": {}})
        self.assertFalse(ContinueTable.can_delete(store, DIS))
        self.assertTrue(ContinueTable.can_delete(store, DFL))
        with self.assertRaises(ValueError):
            ContinueTable.with_deleted_preset(store, DIS, "A")
        with self.assertRaises(ValueError):
            ContinueTable.with_deleted_preset(store, DFL, "Z")

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
        self.assertEqual(ContinueTable.counts(Store()), (1, 1))
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
    """assets/icons・assets/data/continue_slots.json の中身"""

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
        self.assertTrue(self.icons["icons"])
        for tid, (sheet, col, row) in self.icons["icons"].items():
            h, w = sheets[sheet].shape[:2]
            self.assertLessEqual((col + 1) * cell, w, f"{tid}: {sheet} の幅")
            self.assertLessEqual((row + 1) * cell, h, f"{tid}: {sheet} の高さ")

    def test_every_slot_has_an_icon(self):
        """Glorbo（317）も「?」の絵を持つ（GK）。仮の番号 1283 は 192 の絵"""
        slots = {i for _key, ids in self.slots["rounds"] for i in ids}
        missing = sorted(slots - {int(t) for t in self.icons["icons"]})
        self.assertEqual(missing, [])

    def test_glorbo_is_the_question_mark_and_self_inserts_bloodthirsty_borrows_192(self):
        self.assertEqual(self.icons["icons"]["317"], ["terrors", 0, 0])
        self.assertEqual(self.icons["icons"][str(config.SELF_INSERTS_BLOODTHIRSTY_SLOT)],
                         self.icons["icons"]["192"])
        self.assertEqual(self.gui.terror_name(317), "Glorbo")
        self.assertEqual(self.gui.terror_name(config.SELF_INSERTS_BLOODTHIRSTY_SLOT),
                         "Self Inserts (Bloodthirsty)")

    def test_icons_json_has_276_icons_and_the_added_ones(self):
        self.assertEqual(len(self.icons["icons"]), 276)
        for tid in (193, 194, 196, 197, 198, 199, 312, 313, 315, 317, 1283):
            self.assertIn(str(tid), self.icons["icons"], tid)

    def test_names_in_icons_json_come_before_terrors_json(self):
        self.assertEqual(self.gui.icon_names(), {int(t): n for t, n in self.icons["names"].items()})
        self.assertEqual(self.gui.terror_name(193), "The Navigator", "terrors.json では Navigator")
        self.assertEqual(self.gui.terror_name(312), "Riftmonster")
        self.assertEqual(self.gui.terror_name(315), "Neo Pilot")
        self.assertEqual(self.gui.terror_name(192), "Wild Yet Bloodthirsty Creature")
        self.assertEqual(self.gui.terror_name(192, "Unbound/アンバウンド"),
                         "Pack of Wild Yet Bloodthirsty Creatures")
        self.assertEqual(self.gui.terror_name(192, "Classic/クラシック"), "Wild Yet Bloodthirsty Creature")

    def test_the_screen_leaves_out_the_special_round(self):
        keys = [key for key, _ids in self.gui.load_rounds()]
        self.assertEqual(len(keys), 17, "Mystic Moon・Blood Moon は 1 つの Moon にまとまる")
        self.assertNotIn(SPECIAL, keys)

    def test_the_slot_file_has_the_nineteen_rounds_with_distinct_keys(self):
        keys = [key for key, _ids in self.slots["rounds"]]
        self.assertEqual(len(keys), 19)
        self.assertEqual(len(set(keys)), 19)
        self.assertEqual(self.gui.load_rounds()[0][0], KEY)
        for key in keys:
            self.assertIn(key, set(MatchTNL.LOG_TO_TNL.values()) | {SPECIAL}, key)

    def test_an_icon_is_cut_and_enlarged_to_a_png(self):
        png = self.gui.icon_png(0)
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(img.shape[:2], (self.gui.ICON_SIZE, self.gui.ICON_SIZE))
        self.assertIsNotNone(self.gui.icon_png(193), "193 は GI で絵が付いた")
        self.assertIsNotNone(self.gui.icon_png(317), "317（Glorbo）は GK で「?」の絵が付いた")
        self.assertIsNone(self.gui.icon_png(99999), "絵が無い番号は None（名前の札になる）")

    def test_names_come_from_terrors_json_or_the_number(self):
        self.assertEqual(self.gui.terror_name(0), "Huggy")
        self.assertEqual(self.gui.terror_name(134), "Decayed Sponge")
        self.assertEqual(self.gui.terror_name(200), "Guidance & The Booboo's")
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
        monitor = self._monitor({ALT: set()}, round_type="Alternate", ids=[150])
        self.assertEqual(self._result_of(monitor), "skip")
        ContinueTable.set_current(_store(None, {ALT: [150]}))
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

    def test_the_special_moon_slot_is_overlaid_too(self):
        monitor = self._monitor({SPECIAL: {192}}, round_type="Classic", ids=[192])
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_store({SPECIAL: [192]}))
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
        dtm = LogMonitor.DTM_TERROR_ID
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

    def test_a_window_that_chose_nothing_uses_the_base_preset(self):
        store = Store(disabled_presets={BASE: _body({KEY: [7]}), "A": {}}, windows={1: {DIS: "A"}})
        ContinueTable.set_current(store)
        self.assertEqual(self._result_of(self._monitor({KEY: {7}}, window_idx=1)), "continue")
        self.assertEqual(self._result_of(self._monitor({KEY: {7}}, window_idx=3)), "skip")

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
    """Self Inserts (Bloodthirsty) の「続行無効の 1283」もその窓のプリセットで決まる"""

    UNBOUND = "Unbound/アンバウンド"
    SLOT = config.SELF_INSERTS_BLOODTHIRSTY_SLOT
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
        self.assertFalse(one._list_plan([self.SELF_INSERTS], True)[1], "窓 1: 283 がリストに無い → 自爆")
        self.assertTrue(two._list_plan([self.SELF_INSERTS], True)[1], "窓 2: 今までどおり必ず続行")
        self.assertTrue(none._list_plan([self.SELF_INSERTS], True)[1])
        self.assertTrue(one._should_skip_by_round(), "窓 1 は自爆指定が効く")
        self.assertFalse(two._should_skip_by_round())

    def test_group_instances_ignore_the_presets(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            self.assertFalse(self._monitor(1, itype)._self_inserts_table_disabled(), itype)
