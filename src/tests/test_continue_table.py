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
from ContinueTable import Table

KEY = "Classic/クラシック"
ALT = "Alternate/オルタネイト"
SPECIAL = MatchTNL.SPECIAL_MOON_KEY


def _table(disabled=None, default=None, use_disabled=True, use_default=True) -> Table:
    return Table(
        disabled={k: frozenset(v) for k, v in (disabled or {}).items()},
        default={k: frozenset(v) for k, v in (default or {}).items()},
        use_disabled=use_disabled, use_default=use_default)


class _TableCase(unittest.TestCase):
    """今の表をテストごとに空へ戻す（全窓で共有する 1 つなので）"""

    def setUp(self):
        self.addCleanup(ContinueTable.set_current, Table())
        ContinueTable.set_current(Table())


class TestApplyTruthTable(_TableCase):
    """重ね方の表のすべての組み合わせ（下の続行リスト × 続行無効 × デフォ続行 × 2 つの ON/OFF）"""

    def test_every_combination(self):
        for in_base, in_disabled, in_default, use_disabled, use_default in itertools.product(
                (False, True), repeat=5):
            table = _table({KEY: [7]} if in_disabled else None,
                           {KEY: [7]} if in_default else None, use_disabled, use_default)
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

    def test_an_empty_or_off_table_returns_the_base_as_it_is(self):
        base = {KEY: {1}}
        self.assertIs(ContinueTable.apply(base, _table()), base)
        self.assertIs(ContinueTable.apply(base, _table({KEY: [1]}, {KEY: [2]}, False, False)), base)


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

    def test_round_trip(self):
        table = _table({KEY: [5, 9]}, {ALT: [164]}, use_disabled=False, use_default=True)
        self.assertTrue(ContinueTable.save(table, self.path))
        self.assertEqual(ContinueTable.load(self.path), table)

    def test_the_file_has_the_documented_shape(self):
        ContinueTable.save(_table({KEY: [9, 5]}, {ALT: [164]}), self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw, {"version": 1, "use_disabled": True, "use_default": True,
                               "disabled": {KEY: [5, 9]}, "default": {ALT: [164]}})

    def test_a_missing_file_is_an_empty_table(self):
        self.assertEqual(ContinueTable.load(self.path), Table())

    def test_a_broken_file_is_an_empty_table_and_one_line_in_the_debug_log(self):
        self.path.write_text("{ not json", encoding="utf-8")
        with patch.object(DebugLog, "write") as write:
            self.assertEqual(ContinueTable.load(self.path), Table())
        write.assert_called_once()
        self.assertIn("続行表", write.call_args.args[0])

    def test_a_file_of_the_wrong_shape_is_an_empty_table(self):
        self.path.write_text("[1, 2]", encoding="utf-8")
        with patch.object(DebugLog, "write"):
            self.assertEqual(ContinueTable.load(self.path), Table())

    def test_bad_entries_are_dropped_and_the_rest_kept(self):
        self.path.write_text(json.dumps({"disabled": {KEY: [1, "x", True, 2.5, 3], "bad": "no", ALT: []},
                                         "default": "no", "use_default": False}), encoding="utf-8")
        table = ContinueTable.load(self.path)
        self.assertEqual(table.disabled, {KEY: frozenset({1, 3})})
        self.assertEqual(table.default, {})
        self.assertFalse(table.use_default)
        self.assertTrue(table.use_disabled)

    def test_save_replaces_through_a_temporary_file(self):
        ContinueTable.save(_table({KEY: [1]}), self.path)
        with patch.object(ContinueTable.os, "replace", wraps=os.replace) as replace:
            ContinueTable.save(_table({KEY: [2]}), self.path)
        replace.assert_called_once()
        self.assertEqual(Path(replace.call_args.args[0]).parent, self.path.parent)
        self.assertNotEqual(Path(replace.call_args.args[0]), self.path)
        self.assertEqual(os.listdir(self.tmp.name), [self.path.name], "一時ファイルは残らない")

    def test_a_failed_save_keeps_the_old_file(self):
        ContinueTable.save(_table({KEY: [1]}), self.path)
        with patch.object(ContinueTable.os, "replace", side_effect=OSError("disk")), \
             patch.object(DebugLog, "write"):
            self.assertFalse(ContinueTable.save(_table({KEY: [2]}), self.path))
        self.assertEqual(ContinueTable.load(self.path).disabled, {KEY: frozenset({1})})
        self.assertEqual(os.listdir(self.tmp.name), [self.path.name])

    def test_the_default_path_follows_the_settings_folder(self):
        self.assertEqual(ContinueTable.table_path(), config.SETTINGS_PATH.parent / "continue_table.json")
        self.assertNotEqual(ContinueTable.table_path().parent, REAL_SETTINGS_DIR,
                            "テストは本物の %APPDATA% に書かない")


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
        table = _table({KEY: [1, 2]}, {ALT: [150]})
        self.assertEqual(ContinueTable.counts(table), (2, 1))
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
    def test_set_current_replaces_the_shared_table(self):
        self.addCleanup(ContinueTable.set_current, Table())
        table = _table({KEY: [1]})
        ContinueTable.set_current(table)
        self.assertIs(ContinueTable.current(), table)


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

    def test_only_the_ten_known_slots_have_no_icon(self):
        slots = {i for _key, ids in self.slots["rounds"] for i in ids}
        missing = sorted(slots - {int(t) for t in self.icons["icons"]})
        self.assertEqual(missing, [193, 194, 196, 197, 198, 199, 312, 313, 315, 317])

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
        self.assertIsNone(self.gui.icon_png(193), "絵が無い番号は None（名前の札になる）")

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
                 hands_free=False, wins=0):
        SharedState.set_hands_free(hands_free)
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on, lambda _m: None, window_idx=1)
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
        ContinueTable.set_current(_table({KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_the_default_table_makes_an_unlisted_terror_continue(self):
        monitor = self._monitor({KEY: {1}})
        self.assertEqual(self._result_of(monitor), "skip")
        ContinueTable.set_current(_table(None, {KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_the_default_table_wins_over_the_disabled_table(self):
        monitor = self._monitor({KEY: {7}})
        ContinueTable.set_current(_table({KEY: [7]}, {KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_a_turned_off_table_does_nothing(self):
        monitor = self._monitor({KEY: {7}})
        ContinueTable.set_current(_table({KEY: [7]}, None, use_disabled=False))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_a_terror_removed_from_the_host_list_comes_back_with_the_default_table(self):
        """生存済みで主催リストから消えた Alternate（キーはあるが空）を、デフォ続行で続行にする"""
        monitor = self._monitor({ALT: set()}, round_type="Alternate", ids=[150])
        self.assertEqual(self._result_of(monitor), "skip")
        ContinueTable.set_current(_table(None, {ALT: [150]}))
        self.assertEqual(self._result_of(monitor), "continue")

    def test_it_also_applies_to_the_host_list_of_a_tab(self):
        monitor = self._monitor({})
        monitor._window_tab = lambda: {"keepOn": {KEY: {7}}}
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_table({KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_it_also_applies_to_the_merged_per_person_wishes(self):
        monitor = self._monitor({})
        monitor._effective_wishes = lambda: {"a": {KEY: {7}}, "b": {KEY: {8}}}
        ContinueTable.set_current(_table({KEY: [7]}, {KEY: [9]}))
        self.assertEqual(monitor._keep_on(), {KEY: {8, 9}})

    def test_the_multi_terror_rule_uses_the_overlaid_list(self):
        monitor = self._monitor({"Bloodbath/ブラッドバス": {7, 8}}, round_type="Bloodbath", ids=[7, 8, 9])
        ContinueTable.set_current(_table({"Bloodbath/ブラッドバス": [7]}))
        self.assertEqual(self._result_of(monitor), "continue", "1 体が続行ならよい")
        ContinueTable.set_current(_table({"Bloodbath/ブラッドバス": [7, 8]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_the_special_moon_slot_is_overlaid_too(self):
        monitor = self._monitor({SPECIAL: {192}}, round_type="Classic", ids=[192])
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_table({SPECIAL: [192]}))
        self.assertEqual(self._result_of(monitor), "skip")

    def test_the_stored_list_is_not_touched(self):
        base = {KEY: {7}}
        monitor = self._monitor(base)
        ContinueTable.set_current(_table({KEY: [7]}, {KEY: [9]}))
        monitor._keep_on()
        self.assertEqual(base, {KEY: {7}})
        self.assertEqual(monitor.keepOn_set, {KEY: {7}})


class TestGroupInstancesAreNotOverlaid(_MonitorCase):
    def test_hoshiimo_and_yakiimo_get_the_list_as_it_is(self):
        for itype in sorted(GroupRound.GROUP_INSTANCES):
            base = {KEY: {7}}
            monitor = self._monitor(base, instance_type=itype)
            ContinueTable.set_current(_table({KEY: [7]}, {KEY: [9]}))
            self.assertIs(monitor._keep_on(), base, itype)

    def test_the_decision_in_a_group_ignores_the_tables(self):
        for itype in sorted(GroupRound.GROUP_INSTANCES):
            monitor = self._monitor({KEY: {7}}, instance_type=itype)
            before = monitor._list_plan([7], False)
            ContinueTable.set_current(_table({KEY: [7]}))
            self.assertEqual(monitor._list_plan([7], False), before, itype)
            self.assertTrue(monitor._list_plan([7], False)[1], itype)
            ContinueTable.set_current(_table(None, {KEY: [8]}))
            self.assertFalse(monitor._list_plan([8], False)[1], itype)

    def test_a_private_window_is_overlaid(self):
        monitor = self._monitor({KEY: {7}}, instance_type=config.INSTANCE_PRIVATE)
        ContinueTable.set_current(_table({KEY: [7]}))
        self.assertEqual(monitor._keep_on(), {KEY: set()})


class TestOtherRulesStayAsTheyAre(_MonitorCase):
    def test_a_hands_free_window_after_three_wins_still_skips_a_default_continue_terror(self):
        monitor = self._monitor({}, hands_free=True, wins=config.OPEN_SPECIAL_ROUND_TARGET_WINS)
        ContinueTable.set_current(_table(None, {KEY: [7]}))
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
        ContinueTable.set_current(_table(None, {KEY: [7]}))
        self.assertTrue(monitor._wishes_missing())

    def test_all_continue_rounds_follow_the_overlaid_list_and_otherwise_stay(self):
        monitor = self._monitor({KEY: {7}})
        monitor.cfg.continue_rounds = {"Classic"}
        self.assertEqual(self._result_of(monitor), "continue")
        ContinueTable.set_current(_table({KEY: [7]}))
        self.assertEqual(self._result_of(monitor), "quiet", "全続行はそのまま（続行リストで続行にならなければ全続行）")


class TestJudgementLabel(_MonitorCase):
    def test_default_continue_label(self):
        monitor = self._monitor({KEY: {1}})
        ContinueTable.set_current(_table(None, {KEY: [7]}))
        monitor._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】（デフォ続行）"),
                        monitor.logs)

    def test_disabled_label(self):
        monitor = self._monitor({KEY: {7}})
        ContinueTable.set_current(_table({KEY: [7]}))
        monitor._decide_with_keep_on_set("Classic")
        self.assertTrue(self._judgement_line(monitor).endswith("【スキップ】（続行無効）"),
                        monitor.logs)

    def test_no_label_when_the_overlay_changed_nothing(self):
        for base, expect in (({KEY: {7}}, "【プレイ】"), ({KEY: {1}}, "【スキップ】")):
            monitor = self._monitor(base)
            ContinueTable.set_current(_table({KEY: [99]}, {KEY: [98]}))
            monitor._decide_with_keep_on_set("Classic")
            self.assertTrue(self._judgement_line(monitor).endswith(expect), monitor.logs)

    def test_no_label_without_a_table(self):
        monitor = self._monitor({KEY: {7}})
        monitor._decide_with_keep_on_set("Classic")
        self.assertNotIn("（", self._judgement_line(monitor))

    def test_no_label_in_a_group_instance(self):
        monitor = self._monitor({KEY: {7}}, instance_type=config.INSTANCE_HOSHIIMO)
        ContinueTable.set_current(_table({KEY: [7]}))
        monitor._decide_with_keep_on_set("Classic")
        self.assertNotIn("（続行無効）", self._judgement_line(monitor))

    def test_no_label_when_another_rule_made_the_opposite_result(self):
        """続行無効で外れても、3クラ解放（DTM）で続行になったときは「続行無効」と書かない"""
        dtm = LogMonitor.DTM_TERROR_ID
        monitor = self._monitor({KEY: {dtm}}, ids=[dtm])
        monitor.cfg.cancel_afk = True
        ContinueTable.set_current(_table({KEY: [dtm]}))
        monitor._decide_with_keep_on_set("Classic")
        line = self._judgement_line(monitor)
        self.assertIn("【プレイ(DTM/Waldo)】", line)
        self.assertNotIn("（続行無効）", line)
