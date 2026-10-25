"""GS: ラウンド指定自爆（cfg.skip_rounds）の上に、その窓のデフォ続行を重ねる（依頼者 2026-10-25）。

プラベ系の窓で、自爆に指定したラウンドでも、その窓のデフォ続行（アイテムで切り替え込み）にそのテラーが
あれば指定自爆をやめて続行の判定へ流す（重ねた表で続行になる）。無ければ今までどおり自爆。
Sabotage は「Sabotage star」のキーだけを見る。干し芋・焼き芋・テラー不明・3クラ解放などの例外は今のまま。
"""
from tests.support import *  # noqa: F401,F403
import ContinueTable
from tests.test_continue_table import _MonitorCase, _body, _rule_store, BASE, DFL, DIS, KEY, PLUSH

Store = choice_store

FOG = "Fog/霧"
BLOODBATH = "Bloodbath/ブラッドバス"
UNBOUND = "Unbound/アンバウンド"
STAR = GroupRound.SABOTAGE_STAR_KEY
MURDER = GroupRound.SABOTAGE_MURDER_KEY


def _defaults(default=None, disabled=None, windows=None):
    """窓 1 が BASE のプリセットを選んでいる保存内容（デフォ続行 = default、続行無効 = disabled）"""
    return choice_store(
        disabled_presets={BASE: _body(disabled)}, default_presets={BASE: _body(default)},
        windows=windows or {1: {DIS: BASE, DFL: BASE}})


class _SkipCase(_MonitorCase):
    def _skipping(self, skip_rounds, keep_on=None, round_type="Classic", ids=(7,), **kw):
        monitor = self._monitor(keep_on or {}, round_type=round_type, ids=ids, **kw)
        monitor.cfg.skip_rounds = set(skip_rounds)
        return monitor

    def _plan_of(self, monitor, bloodthirsty=False):
        st = monitor.st
        return monitor._plan(st.round_type, st.terror_ids, bloodthirsty)

    def _run(self, monitor):
        """判定を実行する。(続行アナウンスの再生 mock) を返す"""
        with patch.object(PlaySound, "play_sound") as play:
            monitor._decide(monitor.st.round_type)
        return play

    def _skip_started(self, monitor):
        return any("ラウンド指定自爆:" in m for m in monitor.logs)


class TestDefaultContinueOverridesTheRoundSkip(_SkipCase):
    def test_without_a_table_the_round_is_skipped_as_before(self):
        monitor = self._skipping({"Classic"})
        self.assertEqual(self._plan_of(monitor), ("round_skip",))

    def test_a_default_continue_terror_continues_instead(self):
        ContinueTable.set_current(_defaults({KEY: [7]}))
        monitor = self._skipping({"Classic"})

        plan = self._plan_of(monitor)

        self.assertEqual(plan[0], "list", plan)
        self.assertEqual(LogMonitor.LogMonitor._outcome(plan), "continue")

    def test_it_runs_as_a_normal_continue_round_with_the_label(self):
        ContinueTable.set_current(_defaults({KEY: [7]}))
        monitor = self._skipping({"Classic"})

        play = self._run(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        play.assert_called_once_with("continue.mp3", kind="continue")
        self.assertFalse(self._skip_started(monitor), monitor.logs)
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】（デフォ続行）"), monitor.logs)

    def test_the_label_is_there_even_if_the_list_already_continues_it(self):
        """リストにも 7 がある。表で変わった結果ではないが、指定自爆をデフォ続行で上書きしたのでラベルを付ける"""
        ContinueTable.set_current(_defaults({KEY: [7]}))
        monitor = self._skipping({"Classic"}, keep_on={KEY: {7}})

        self._run(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】（デフォ続行）"), monitor.logs)

    def test_another_terror_in_the_default_does_not_matter(self):
        ContinueTable.set_current(_defaults({KEY: [8]}))
        monitor = self._skipping({"Classic"})

        self.assertEqual(self._plan_of(monitor), ("round_skip",))
        self._run(monitor)
        self.assertTrue(self._skip_started(monitor), monitor.logs)
        self.assertFalse(monitor.st.is_continue_round)

    def test_the_same_terror_under_another_round_key_does_not_matter(self):
        ContinueTable.set_current(_defaults({FOG: [7]}))
        monitor = self._skipping({"Classic"})

        self.assertEqual(self._plan_of(monitor), ("round_skip",))

    def test_a_disabled_entry_alone_leaves_the_round_skip(self):
        """続行無効は、自爆のラウンドでは見る必要が無い（自爆のまま）"""
        ContinueTable.set_current(_defaults(None, {KEY: [7]}))
        monitor = self._skipping({"Classic"}, keep_on={KEY: {7}})

        self.assertEqual(self._plan_of(monitor), ("round_skip",))

    def test_a_round_not_in_skip_rounds_is_untouched(self):
        ContinueTable.set_current(_defaults({KEY: [7]}))
        monitor = self._skipping({"Fog"})            # Classic は自爆指定に無い

        plan = self._plan_of(monitor)

        self.assertEqual(LogMonitor.LogMonitor._outcome(plan), "continue", "表の通常どおりのデフォ続行")

    def test_one_terror_of_several_is_enough(self):
        ContinueTable.set_current(_defaults({BLOODBATH: [8]}))
        monitor = self._skipping({"Bloodbath"}, round_type="Bloodbath", ids=(7, 8, 9))
        self.assertEqual(LogMonitor.LogMonitor._outcome(self._plan_of(monitor)), "continue")
        monitor = self._skipping({"Bloodbath"}, round_type="Bloodbath", ids=(7, 9, 10))
        self.assertEqual(self._plan_of(monitor), ("round_skip",))

    def test_an_unknown_terror_is_not_affected(self):
        ContinueTable.set_current(_defaults({KEY: [7]}))
        monitor = self._skipping({"Classic"}, ids=())

        self.assertEqual(self._plan_of(monitor), ("round_skip",))

    def test_it_follows_the_window(self):
        """窓ごとの表。窓 2 は何も選んでいない"""
        ContinueTable.set_current(_defaults({KEY: [7]}))
        one = self._skipping({"Classic"}, window_idx=1)
        two = self._skipping({"Classic"}, window_idx=2)

        self.assertEqual(LogMonitor.LogMonitor._outcome(self._plan_of(one)), "continue")
        self.assertEqual(self._plan_of(two), ("round_skip",))

    def test_it_follows_the_held_item_rule(self):
        """アイテムで切り替える: Guidance Plush を持っている間だけ別のデフォ続行（足9）を使う"""
        ContinueTable.set_current(_rule_store(ContinueTable.ItemRule(PLUSH, ContinueTable.KEEP, "足9"),
                                              windows={1: {}}))
        held = self._skipping({"Classic"}, ids=(9,))
        held.st.held_item_id = PLUSH
        free = self._skipping({"Classic"}, ids=(9,))
        free.st.held_item_id = 0

        self.assertEqual(LogMonitor.LogMonitor._outcome(self._plan_of(held)), "continue")
        self.assertEqual(self._plan_of(free), ("round_skip",))


class TestSabotageLooksAtTheStarKeyOnly(_SkipCase):
    def _sabotage(self, **kw):
        monitor = self._skipping({"Sabotage"}, round_type="Sabotage", **kw)
        monitor.st.sus_players = ["だれか"]
        return monitor

    def test_star_in_the_default_continues(self):
        ContinueTable.set_current(_defaults({STAR: [7]}))
        monitor = self._sabotage()

        plan = self._plan_of(monitor)
        self.assertEqual(LogMonitor.LogMonitor._outcome(plan), "continue", plan)

        play = self._run(monitor)
        self.assertTrue(monitor.st.is_continue_round)
        play.assert_called_once_with("continue.mp3", kind="continue")
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】（デフォ続行）"), monitor.logs)

    def test_murder_in_the_default_does_not(self):
        ContinueTable.set_current(_defaults({MURDER: [7]}))
        monitor = self._sabotage()

        self.assertEqual(self._plan_of(monitor), ("round_skip",))
        self._run(monitor)
        self.assertTrue(self._skip_started(monitor), monitor.logs)

    def test_star_in_the_default_continues_even_with_host_wishes_against_it(self):
        ContinueTable.set_current(_defaults({STAR: [7]}))
        monitor = self._sabotage()
        monitor.host_wishes = {"みているひと": {STAR: {99}}}

        self.assertEqual(LogMonitor.LogMonitor._outcome(self._plan_of(monitor)), "continue")

    def test_a_sabotage_window_without_the_skip_setting_keeps_the_sabotage_path(self):
        """Sabotage を指定自爆に入れていない窓は、今までの _decide_sabotage の流れのまま"""
        ContinueTable.set_current(_defaults({STAR: [7]}))
        monitor = self._monitor({}, round_type="Sabotage", ids=(7,))
        monitor.cfg.skip_rounds = {"Classic"}
        monitor.host_wishes = {"みているひと": {STAR: {99}}}
        monitor.st.sus_players = ["だれか"]
        before = self._plan_of(monitor)

        ContinueTable.set_current(Store())
        monitor_without_table = self._monitor({}, round_type="Sabotage", ids=(7,))
        monitor_without_table.cfg.skip_rounds = {"Classic"}
        monitor_without_table.host_wishes = {"みているひと": {STAR: {99}}}
        monitor_without_table.st.sus_players = ["だれか"]

        self.assertEqual(before, self._plan_of(monitor_without_table))
        self.assertNotEqual(before, ("round_skip",))
        self.assertEqual(LogMonitor.LogMonitor._outcome(before), "skip", "Sabotage star のデフォ続行は、この経路では見ない（今のまま）")


class TestTheOtherRulesStayAsTheyAre(_SkipCase):
    def test_group_instances_do_not_look_at_the_table(self):
        ContinueTable.set_current(_defaults({KEY: [7], "Special/Moon": [7]}))
        for itype in sorted(GroupRound.GROUP_INSTANCES):
            monitor = self._skipping({"Blood Moon"}, round_type="Blood Moon", instance_type=itype)
            monitor.st.moon_repeat = True            # 2 回目の moon はグループでは必ず自爆
            self.assertEqual(self._plan_of(monitor), ("group", GroupRound.SKIP), itype)
            self.assertFalse(monitor._round_skip_overridden([7], False), itype)

    def _same_with_and_without_the_skip_setting(self, round_type, ids, bloodthirsty=False):
        """自爆指定の有る無しで手順が同じ（＝例外が自爆指定より上に来ている）。表はデフォ続行に ids を入れておく"""
        key = MatchTNL.LOG_TO_TNL[round_type]
        ContinueTable.set_current(_defaults({key: list(ids)}))
        skipping = self._skipping({round_type}, round_type=round_type, ids=ids)
        plain = self._skipping(set(), round_type=round_type, ids=ids)
        plan = self._plan_of(skipping, bloodthirsty)
        self.assertEqual(plan, self._plan_of(plain, bloodthirsty))
        self.assertNotEqual(plan, ("round_skip",))
        return plan

    def test_unlocked_three_class_rounds_stay_as_they_were(self):
        """3クラ解放（DTM）は自爆指定より上"""
        plan = self._same_with_and_without_the_skip_setting("Classic", (config.DTM_ID,))
        self.assertEqual(plan, ("list", True, True))

    def test_variant_stays_as_it_was(self):
        variant = sorted(config.VARIANT_TERROR_IDS)[0]
        self._same_with_and_without_the_skip_setting("Classic", (variant,))

    def test_self_inserts_bloodthirsty_stays_as_it_was(self):
        plan = self._same_with_and_without_the_skip_setting("Unbound", (config.SELF_INSERTS_ID,), bloodthirsty=True)
        self.assertEqual(plan, ("list", True, False))

    def test_a_hands_free_instant_skip_stays_first(self):
        ContinueTable.set_current(_defaults({KEY: [7]}))
        monitor = self._skipping({"Classic"}, hands_free=True, wins=config.OPEN_SPECIAL_ROUND_TARGET_WINS)

        self.assertEqual(self._plan_of(monitor)[0], "hands_free")

    def test_all_continue_rounds_stay_first(self):
        ContinueTable.set_current(_defaults({KEY: [7]}))
        monitor = self._skipping({"Classic"})
        monitor.cfg.continue_rounds = {"Classic"}

        plan = self._plan_of(monitor)

        self.assertEqual(LogMonitor.LogMonitor._outcome(plan), "continue", "リストで続行になる全続行のまま")
        self.assertEqual(plan[0], "list")
