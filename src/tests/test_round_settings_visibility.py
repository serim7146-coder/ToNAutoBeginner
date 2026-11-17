"""効かない全自爆・全続行のチェックを、窓タブで押せなくする。

- GroupRound.setting_applies の表（4 種類 × 20 名前 × 2 側）
- 要: setting_applies が True の組と、GroupRound.decide の結果が変わる組が食い違わないこと
  （画面が嘘をついていないこと）
- 窓タブ（隠した Tk）: 押せない・外れる・None なら全部押せる・引き直し・Ctrl の写しの門・控え
- 1 行のラベル
- LogMonitor が入室でインスタンスタイプを知らせる / App が窓タブへ渡す

本物の入力・OSC・窓・ネットワークは使わない。窓は作った直後に隠す。
"""
from tests.support import *  # noqa: F401,F403
import GroupRound
import LogMonitor
import UiInbox
import config
import mainGUI
from LogMonitor import WindowConfig

NAMES = config.SKIP_ROUND_SELECTABLE
SKIP, CONT = GroupRound.SETTING_SKIP, GroupRound.SETTING_CONTINUE
MOONS = ("Mystic Moon", "Blood Moon", "Twilight", "Solstice")
ALL_TYPES = (config.INSTANCE_PRIVATE, config.INSTANCE_SEER, config.INSTANCE_ACHIEVEMENT,
             config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO,
             config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP, config.INSTANCE_EMERALD_CITY)
PRIVATE_LIKE = (config.INSTANCE_PRIVATE, config.INSTANCE_SEER, config.INSTANCE_ACHIEVEMENT)
NOTHING = (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP, config.INSTANCE_EMERALD_CITY)


class TestSettingApplies(unittest.TestCase):
    """決まりの表（GroupRound.setting_applies）"""

    def _names(self, instance_type, which):
        return [n for n in NAMES if GroupRound.setting_applies(instance_type, which, n)]

    def test_the_twenty_names_are_what_the_table_assumes(self):
        self.assertEqual(len(NAMES), 20)
        for moon in MOONS:
            self.assertIn(moon, NAMES)

    def test_private_like_instances_take_everything(self):
        for itype in PRIVATE_LIKE:
            for which in (SKIP, CONT):
                self.assertEqual(self._names(itype, which), list(NAMES), (itype, which))

    def test_yakiimo_skips_only_blood_moon_and_continues_only_twilight(self):
        # Mystic Moon・Solstice は YAKIIMO_SKIP_FIRST_MOONS で 1 回目から自爆（指定しても同じ結果）なので False
        self.assertEqual(self._names(config.INSTANCE_YAKIIMO, SKIP), ["Blood Moon"])
        self.assertEqual(self._names(config.INSTANCE_YAKIIMO, CONT), ["Twilight"])

    def test_yakiimo_twilight_only_continue_is_read(self):
        self.assertTrue(GroupRound.setting_applies(config.INSTANCE_YAKIIMO, CONT, "Twilight"))
        self.assertFalse(GroupRound.setting_applies(config.INSTANCE_YAKIIMO, SKIP, "Twilight"))

    def test_yakiimo_first_time_skip_moons_do_not_apply(self):
        for moon in ("Mystic Moon", "Solstice"):
            self.assertFalse(GroupRound.setting_applies(config.INSTANCE_YAKIIMO, SKIP, moon), moon)
            self.assertFalse(GroupRound.setting_applies(config.INSTANCE_YAKIIMO, CONT, moon), moon)

    def test_non_moon_names_never_apply_to_groups(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            for which in (SKIP, CONT):
                for name in NAMES:
                    if name not in MOONS:
                        self.assertFalse(GroupRound.setting_applies(itype, which, name), (itype, which, name))

    def test_the_whole_table(self):
        """結果の表を丸ごと書き写したもの。setting_applies は decide を呼んで決める（手書きの表が無い）ので、
        decide を変えるとここが落ちる。落ちたら、画面の表示が変わるということ。差分を見て、変わってよいか確かめること"""
        Y, H = config.INSTANCE_YAKIIMO, config.INSTANCE_HOSHIIMO
        table = {
            (Y, SKIP): ["Blood Moon"],
            (Y, CONT): ["Twilight"],
            (H, SKIP): ["Mystic Moon", "Blood Moon", "Twilight", "Solstice"],
            (H, CONT): [],
        }
        for itype in PRIVATE_LIKE:
            for which in (SKIP, CONT):
                table[(itype, which)] = list(NAMES)
        for itype in NOTHING:
            for which in (SKIP, CONT):
                table[(itype, which)] = []
        self.assertEqual(len(table), len(ALL_TYPES) * 2)
        for (itype, which), want in table.items():
            self.assertEqual(self._names(itype, which), want, (itype, which))

    def test_hoshiimo_skips_four_moons_and_continues_nothing(self):
        self.assertEqual(self._names(config.INSTANCE_HOSHIIMO, SKIP), list(MOONS))
        self.assertEqual(self._names(config.INSTANCE_HOSHIIMO, CONT), [])
        for moon in MOONS:
            self.assertFalse(GroupRound.setting_applies(config.INSTANCE_HOSHIIMO, CONT, moon), moon)

    def test_the_rest_takes_nothing(self):
        for itype in NOTHING:
            for which in (SKIP, CONT):
                self.assertEqual(self._names(itype, which), [], (itype, which))

    def test_a_name_outside_the_list_never_applies_to_groups(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO, config.INSTANCE_PUBLIC):
            for which in (SKIP, CONT):
                self.assertFalse(GroupRound.setting_applies(itype, which, "No Such Round"))


class TestMatchesTheRealDecision(unittest.TestCase):
    """要。画面が押せると言うものは、本物の判定で効く。押せないと言うものは、入れても何も変わらない。
    GroupRound.decide（と is_all_continue_regardless_of_terror）を、指定を入れた場合と入れない場合で呼び、
    結果が変わる組を調べる。これが落ちたら、画面の表示が嘘をついている"""

    GROUPS = (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO)

    @staticmethod
    def _decide(itype, name, repeat, skip=(), cont=()):
        return GroupRound.decide(itype, name, [], moon_repeat=repeat,
                                 skip_moons=skip, continue_moons=cont)

    @staticmethod
    def _all_continue(itype, name, repeat, skip=(), cont=()):
        return GroupRound.is_all_continue_regardless_of_terror(
            itype, name, moon_repeat=repeat, skip_moons=skip, continue_moons=cont)

    def _effective(self, itype, which, name):
        """その指定を入れると、1 回目か 2 回目以降のどちらかで結果が変わるか"""
        for repeat in (False, True):
            if which == SKIP:
                args = dict(skip=(name,))
            else:
                args = dict(cont=(name,))
            if self._decide(itype, name, repeat) != self._decide(itype, name, repeat, **args):
                return True
            if self._all_continue(itype, name, repeat) != self._all_continue(itype, name, repeat, **args):
                return True
        return False

    def test_groups_every_name_every_side(self):
        for itype in self.GROUPS:
            for which in (SKIP, CONT):
                for name in NAMES:
                    with self.subTest(itype=itype, which=which, name=name):
                        effective = self._effective(itype, which, name)
                        applies = GroupRound.setting_applies(itype, which, name)
                        self.assertEqual(
                            applies, effective,
                            "画面の押せる・押せないが、本物の判定と食い違っている")

    def test_a_disabled_item_never_hides_a_working_setting(self):
        """押せないと言うものを入れても、結果は絶対に変わらない（押せないせいで効く指定が使えなくなることは無い）"""
        for itype in self.GROUPS:
            for which in (SKIP, CONT):
                for name in NAMES:
                    if not GroupRound.setting_applies(itype, which, name):
                        self.assertFalse(self._effective(itype, which, name), (itype, which, name))

    def test_the_comparison_can_see_a_difference_at_all(self):
        """比べ方が常に False を返す、という穴の確認（比べる側が壊れていないこと）"""
        self.assertTrue(self._effective(config.INSTANCE_HOSHIIMO, SKIP, "Blood Moon"))
        self.assertTrue(self._effective(config.INSTANCE_YAKIIMO, CONT, "Twilight"))
        self.assertFalse(self._effective(config.INSTANCE_YAKIIMO, SKIP, "Twilight"))
        self.assertFalse(self._effective(config.INSTANCE_HOSHIIMO, CONT, "Blood Moon"))

    def test_other_instances_decide_does_not_read_the_moon_sets(self):
        """グループでない型は decide が NORMAL を返す（指定は decide では効かない）。
        private 系の指定は LogMonitor の _plan が見る（decide の外。setting_applies は True を返す）"""
        for itype in NOTHING:
            for which in (SKIP, CONT):
                for name in NAMES:
                    self.assertFalse(GroupRound.setting_applies(itype, which, name))
                    self.assertEqual(self._decide(itype, name, False),
                                     self._decide(itype, name, False, skip=(name,), cont=(name,)))


class _TabCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def _tab(self, **kwargs):
        return mainGUI.WindowTab(self.root, 1, **kwargs)

    @staticmethod
    def _disabled(tab, which):
        boxes = tab.cb_skip_rounds if which == SKIP else tab.cb_continue_rounds
        return {n for n, b in boxes.items() if b.instate(["disabled"])}

    @staticmethod
    def _checked(tab, which):
        vars_ = tab.v_skip_rounds if which == SKIP else tab.v_continue_rounds
        return {n for n, v in vars_.items() if v.get()}


class TestTabDisablesWhatDoesNotApply(_TabCase):
    def test_the_boxes_are_kept_for_every_name(self):
        tab = self._tab()
        self.assertEqual(list(tab.cb_skip_rounds), list(NAMES))
        self.assertEqual(list(tab.cb_continue_rounds), list(NAMES))

    def test_unknown_type_everything_is_pressable(self):
        tab = self._tab()
        self.assertIsNone(tab._instance_type)
        self.assertEqual(self._disabled(tab, SKIP), set())
        self.assertEqual(self._disabled(tab, CONT), set())

    def test_none_applied_again_everything_is_pressable(self):
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_PUBLIC)
        tab.apply_instance_type(None)
        self.assertEqual(self._disabled(tab, SKIP), set())
        self.assertEqual(self._disabled(tab, CONT), set())

    def test_every_type_disables_exactly_what_the_table_says(self):
        for itype in ALL_TYPES:
            tab = self._tab()
            tab.apply_instance_type(itype)
            for which in (SKIP, CONT):
                want = {n for n in NAMES if not GroupRound.setting_applies(itype, which, n)}
                self.assertEqual(self._disabled(tab, which), want, (itype, which))
            tab.destroy()

    def test_yakiimo_example(self):
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_YAKIIMO)
        self.assertEqual(self._disabled(tab, SKIP), set(NAMES) - {"Blood Moon"})
        self.assertEqual(self._disabled(tab, CONT), set(NAMES) - {"Twilight"})

    def test_disabled_ones_are_unchecked(self):
        tab = self._tab()
        for var in (*tab.v_skip_rounds.values(), *tab.v_continue_rounds.values()):
            var.set(False)
        tab.v_skip_rounds["Classic"].set(True)
        tab.v_skip_rounds["Blood Moon"].set(True)
        tab.v_continue_rounds["Run"].set(True)
        tab.v_continue_rounds["Twilight"].set(True)

        tab.apply_instance_type(config.INSTANCE_YAKIIMO)

        self.assertEqual(self._checked(tab, SKIP), {"Blood Moon"})
        self.assertEqual(self._checked(tab, CONT), {"Twilight"})

    def test_a_pressable_one_stays_checked(self):
        tab = self._tab()
        tab.v_skip_rounds["Solstice"].set(True)
        tab.apply_instance_type(config.INSTANCE_HOSHIIMO)
        self.assertTrue(tab.v_skip_rounds["Solstice"].get())

    def test_public_clears_everything(self):
        tab = self._tab()
        tab.v_skip_rounds["Classic"].set(True)
        tab.v_continue_rounds["Run"].set(True)
        tab.apply_instance_type(config.INSTANCE_PUBLIC)
        self.assertEqual(self._checked(tab, SKIP), set())
        self.assertEqual(self._checked(tab, CONT), set())

    def test_the_disabled_box_does_not_react_to_invoke(self):
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_YAKIIMO)
        tab.cb_skip_rounds["Classic"].invoke()
        self.assertFalse(tab.v_skip_rounds["Classic"].get())

    def test_a_pressable_box_still_works(self):
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_YAKIIMO)
        tab.cb_skip_rounds["Blood Moon"].invoke()
        self.assertTrue(tab.v_skip_rounds["Blood Moon"].get())

    def test_applying_twice_and_changing_type_comes_back(self):
        """焼き芋 → private で、押せるように戻る。2 回呼んでも壊れない"""
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_YAKIIMO)
        tab.apply_instance_type(config.INSTANCE_YAKIIMO)
        self.assertNotEqual(self._disabled(tab, SKIP), set())
        tab.apply_instance_type(config.INSTANCE_PRIVATE)
        tab.apply_instance_type(config.INSTANCE_PRIVATE)
        self.assertEqual(self._disabled(tab, SKIP), set())
        self.assertEqual(self._disabled(tab, CONT), set())
        tab.cb_skip_rounds["Classic"].invoke()
        self.assertTrue(tab.v_skip_rounds["Classic"].get())

    def test_unchecking_notifies_the_settings_watcher(self):
        """外れた結果は監視へ伝わる（効かない指定が消えるだけ）"""
        seen = []
        tab = self._tab(on_settings_changed=seen.append)
        tab.v_skip_rounds["Classic"].set(True)
        seen.clear()
        tab.apply_instance_type(config.INSTANCE_PUBLIC)
        self.assertTrue(seen)
        self.assertNotIn("Classic", tab.live_settings()["skip_rounds"])


class TestRuleLabel(_TabCase):
    def _text(self, itype):
        tab = self._tab()
        tab.apply_instance_type(itype)
        return tab.lbl_round_rule.cget("text")

    def test_the_four_kinds(self):
        self.assertEqual(
            self._text(config.INSTANCE_YAKIIMO),
            "インスタンス: 焼き芋 → 全自爆は Blood Moon だけ効きます、"
            "全続行は Twilight だけ効きます")
        self.assertEqual(
            self._text(config.INSTANCE_HOSHIIMO),
            "インスタンス: 干し芋 → 全自爆は Mystic Moon / Blood Moon / Twilight / Solstice だけ効きます、"
            "全続行は効きません")
        self.assertEqual(
            self._text(config.INSTANCE_PRIVATE),
            "インスタンス: private → 全自爆は全部効きます、全続行は全部効きます")
        self.assertEqual(
            self._text(config.INSTANCE_PUBLIC),
            "インスタンス: public → 全自爆は効きません、全続行は効きません")

    def test_unknown(self):
        tab = self._tab()
        self.assertEqual(tab.lbl_round_rule.cget("text"), "インスタンスがまだ分かりません（すべて押せます）")

    def test_it_is_built_from_the_rule(self):
        """手書きの文言ではなく、決まりから出る: 押せる名前が全部出ていて、押せない名前は出ていない"""
        for itype in ALL_TYPES:
            text = self._text(itype)
            for which, title in ((SKIP, "全自爆"), (CONT, "全続行")):
                names = [n for n in NAMES if GroupRound.setting_applies(itype, which, n)]
                part = text.split("、")[0 if which == SKIP else 1]
                self.assertTrue(part.endswith(("効きません", "全部効きます", "だけ効きます")), part)
                if 0 < len(names) < len(NAMES):
                    for n in NAMES:
                        self.assertEqual(n in part, n in names, (itype, which, n))

    def test_the_colour_is_the_note_colour(self):
        tab = self._tab()
        self.assertEqual(str(tab.lbl_round_rule.cget("foreground")), config.GUI_YLW)

    def test_the_heading_is_no_longer_private_only(self):
        tab = self._tab()
        heads = []
        stack = [tab.sections["rounds"]]
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Label):
                heads.append(w.cget("text"))
            stack.extend(w.winfo_children())
        self.assertIn("ラウンドごとの自爆設定", heads)
        self.assertFalse([h for h in heads if "プライベートインスタンスのみ" in h])


class TestSetRoundGate(_TabCase):
    def test_a_disabled_item_is_not_written(self):
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_YAKIIMO)
        tab.set_round(SKIP, "Classic", True)
        tab.set_round(CONT, "Blood Moon", True)
        self.assertFalse(tab.v_skip_rounds["Classic"].get())
        self.assertFalse(tab.v_continue_rounds["Blood Moon"].get())

    def test_a_pressable_item_is_written(self):
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_YAKIIMO)
        tab.set_round(SKIP, "Blood Moon", True)
        tab.set_round(CONT, "Twilight", True)
        self.assertTrue(tab.v_skip_rounds["Blood Moon"].get())
        self.assertTrue(tab.v_continue_rounds["Twilight"].get())

    def test_unknown_type_writes_everything(self):
        tab = self._tab()
        tab.set_round(SKIP, "Classic", True)
        self.assertTrue(tab.v_skip_rounds["Classic"].get())

    def test_a_rejected_write_does_not_clear_the_other_side(self):
        """入れなかったなら、反対側も触らない"""
        tab = self._tab()
        tab.apply_instance_type(config.INSTANCE_HOSHIIMO)
        tab.v_skip_rounds["Blood Moon"].set(True)
        tab.set_round(CONT, "Blood Moon", True)
        self.assertTrue(tab.v_skip_rounds["Blood Moon"].get())
        self.assertFalse(tab.v_continue_rounds["Blood Moon"].get())


class TestSnapshotRestore(_TabCase):
    def test_the_type_is_kept(self):
        a = self._tab()
        a.apply_instance_type(config.INSTANCE_YAKIIMO)
        saved = a.snapshot()
        self.assertEqual(saved["instance_type"], config.INSTANCE_YAKIIMO)

        b = self._tab()
        b.restore(saved)
        self.assertEqual(b._instance_type, config.INSTANCE_YAKIIMO)
        self.assertEqual(self._disabled(b, SKIP), self._disabled(a, SKIP))
        self.assertEqual(b.lbl_round_rule.cget("text"), a.lbl_round_rule.cget("text"))

    def test_a_stale_save_cannot_bring_back_a_disabled_item(self):
        """古い控えから効かない項目が True で入ってきても、最後に外れる"""
        a = self._tab()
        saved = a.snapshot()
        saved["skip_rounds"] = {"Classic", "Blood Moon"}
        saved["continue_rounds"] = {"Run", "Twilight"}
        saved["instance_type"] = config.INSTANCE_YAKIIMO

        b = self._tab()
        b.restore(saved)

        self.assertEqual(self._checked(b, SKIP), {"Blood Moon"})
        self.assertEqual(self._checked(b, CONT), {"Twilight"})

    def test_an_old_save_without_the_type_restores_as_unknown(self):
        a = self._tab()
        saved = a.snapshot()
        del saved["instance_type"]
        b = self._tab()
        b.apply_instance_type(config.INSTANCE_PUBLIC)
        b.restore(saved)
        self.assertIsNone(b._instance_type)
        self.assertEqual(self._disabled(b, SKIP), set())


class TestMonitorTellsTheType(unittest.TestCase):
    """LogMonitor は入室でインスタンスタイプを知らせる（窓ごとのスレッドから呼ぶ。画面は触らない）"""

    JOIN = ("2026.09.15 10:00:00 Debug      -  [Behaviour] "
            "Joining wrld_1234:5678~private(usr_x)")

    def _monitor(self, **kwargs):
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True), {}, lambda _m: None,
                                        window_idx=3, **kwargs)
        return monitor

    def _join(self, monitor):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.JOIN)

    def test_the_callback_gets_the_window_and_the_type(self):
        calls = []
        monitor = self._monitor(on_instance_type=lambda idx, t: calls.append((idx, t)))
        self._join(monitor)
        self.assertEqual(calls, [(3, config.INSTANCE_PRIVATE)])

    def test_a_group_type_is_passed_as_it_is(self):
        calls = []
        monitor = self._monitor(on_instance_type=lambda idx, t: calls.append((idx, t)))
        with patch.object(monitor, "_parse_instance_type", return_value=config.INSTANCE_YAKIIMO):
            self._join(monitor)
        self.assertEqual(calls, [(3, config.INSTANCE_YAKIIMO)])

    def test_it_comes_after_the_clear(self):
        """解除（全部外す）のあとに引き直す。逆だと、引き直した結果をまた外してしまう"""
        order = []
        monitor = self._monitor(on_round_settings_cleared=lambda idx: order.append("clear"),
                                on_instance_type=lambda idx, t: order.append("type"))
        self._join(monitor)
        self.assertEqual(order, ["clear", "type"])

    def test_without_the_callback_nothing_happens(self):
        monitor = self._monitor()
        self._join(monitor)                 # 落ちないこと
        self.assertEqual(monitor.st.instance_type, config.INSTANCE_PRIVATE)

    def test_the_decision_is_not_touched(self):
        """知らせの口を付けても、判定の入力（st・cfg）は変わらない"""
        a = self._monitor()
        b = self._monitor(on_instance_type=lambda idx, t: None)
        self._join(a)
        self._join(b)
        self.assertEqual(a.st.instance_type, b.st.instance_type)
        self.assertEqual(a.cfg.skip_rounds, b.cfg.skip_rounds)


class TestAppHandsTheTypeToTheTab(unittest.TestCase):
    """App: 監視のスレッドからは受付箱（UiInbox）越し。窓タブを引き直す"""

    class FakeTab:
        def __init__(self, idx):
            self.idx = idx
            self.got = []

        def apply_instance_type(self, itype):
            self.got.append(itype)

    def _app(self, tabs):
        app = type("FakeApp", (), {})()
        app.tabs = tabs
        app.after = MagicMock()
        app._apply_tab_instance_type = lambda idx, t: mainGUI.App._apply_tab_instance_type(app, idx, t)
        return app

    def test_it_goes_through_the_main_thread(self):
        tabs = [self.FakeTab(0)]
        app = self._app(tabs)

        mainGUI.App._set_tab_instance_type(app, 1, config.INSTANCE_YAKIIMO)

        app.after.assert_called_once()
        self.assertEqual(tabs[0].got, [], "after を待たずに触らないこと")
        _delay, func = app.after.call_args[0]
        func()
        self.assertEqual(tabs[0].got, [config.INSTANCE_YAKIIMO])

    def test_it_uses_the_inbox_from_another_thread(self):
        tabs = [self.FakeTab(0)]
        app = self._app(tabs)
        calls = []
        with patch.object(mainGUI.UiInbox, "call_soon", lambda widget, func, *a: calls.append(func)):
            mainGUI.App._set_tab_instance_type(app, 1, config.INSTANCE_PUBLIC)
        self.assertEqual(len(calls), 1)
        app.after.assert_not_called()

    def test_only_that_window(self):
        tabs = [self.FakeTab(0), self.FakeTab(1)]
        app = self._app(tabs)
        mainGUI.App._apply_tab_instance_type(app, 2, config.INSTANCE_HOSHIIMO)
        self.assertEqual(tabs[0].got, [])
        self.assertEqual(tabs[1].got, [config.INSTANCE_HOSHIIMO])

    def test_an_unknown_window_is_ignored(self):
        tabs = [self.FakeTab(0)]
        app = self._app(tabs)
        mainGUI.App._apply_tab_instance_type(app, 9, config.INSTANCE_HOSHIIMO)
        self.assertEqual(tabs[0].got, [])

    def test_a_destroyed_window_does_not_raise(self):
        tab = self.FakeTab(0)
        tab.apply_instance_type = MagicMock(side_effect=tk.TclError("destroyed"))
        app = self._app([tab])
        mainGUI.App._apply_tab_instance_type(app, 1, config.INSTANCE_PUBLIC)

    def test_after_outside_the_mainloop_does_not_raise_from_the_thread(self):
        app = self._app([self.FakeTab(0)])
        app.after = MagicMock(side_effect=RuntimeError("main thread is not in main loop"))
        mainGUI.App._set_tab_instance_type(app, 1, config.INSTANCE_PUBLIC)
        app.after = MagicMock(side_effect=tk.TclError("destroyed"))
        mainGUI.App._set_tab_instance_type(app, 1, config.INSTANCE_PUBLIC)

    def test_the_log_selection_also_applies_it(self):
        tab = self.FakeTab(0)
        tab.v_log = MagicMock()
        tab.v_log.get.return_value = "output_log_dummy.txt"
        app = type("FakeApp", (), {})()
        app._log = MagicMock()
        with patch.object(LogMonitor.LogMonitor, "detect_instance_type_from_log",
                          return_value=config.INSTANCE_HOSHIIMO):
            mainGUI.App._on_tab_log_selected(app, tab)
        self.assertEqual(tab.got, [config.INSTANCE_HOSHIIMO])

    def test_the_log_selection_with_no_join_line_applies_none(self):
        tab = self.FakeTab(0)
        tab.v_log = MagicMock()
        tab.v_log.get.return_value = "output_log_dummy.txt"
        app = type("FakeApp", (), {})()
        app._log = MagicMock()
        with patch.object(LogMonitor.LogMonitor, "detect_instance_type_from_log", return_value=None):
            mainGUI.App._on_tab_log_selected(app, tab)
        self.assertEqual(tab.got, [None])


if __name__ == "__main__":
    unittest.main()
