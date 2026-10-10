"""全続行・自爆するラウンドで Fog・Ghost・8 Pages を選んだら、Killers の行の
「… (Alternate)」にも効かせる（DM。依頼者 2026-10-10）"""
from tests.support import *  # noqa: F401,F403


class TestSelectableRoundName(unittest.TestCase):
    def test_the_alternate_of_a_selectable_round(self):
        S = RoundDecision.selectable_round_name
        self.assertEqual(S("Ghost (Alternate)"), "Ghost")
        self.assertEqual(S("Fog (Alternate)"), "Fog")
        self.assertEqual(S("8 Pages (Alternate)"), "8 Pages")

    def test_everything_else_is_as_it_is(self):
        S = RoundDecision.selectable_round_name
        for name in ("Alternate", "Ghost", "Fog", "Classic", "Midnight", "", None,
                     "Unknown (Alternate)", "Ghost(Alternate)", "Ghost (Alternate) "):
            self.assertEqual(S(name), name, name)

    def test_only_names_that_can_be_chosen(self):
        with patch.object(config, "SKIP_ROUND_SELECTABLE", ["Fog"]):
            self.assertEqual(RoundDecision.selectable_round_name("Ghost (Alternate)"), "Ghost (Alternate)")
            self.assertEqual(RoundDecision.selectable_round_name("Fog (Alternate)"), "Fog")


class TestTheChosenRoundsMatchTheAlternate(unittest.TestCase):
    def setUp(self):
        thread = patch.object(LogMonitor.threading, "Thread")
        self.thread = thread.start()
        self.addCleanup(thread.stop)
        for p in (patch.object(PlaySound, "play_sound"), patch.object(Recorder, "on_continue_start"),
                  patch.object(ConnectDB, "register_round")):
            p.start()
            self.addCleanup(p.stop)
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)

    def _monitor(self, keep=None, instance_type=config.INSTANCE_PRIVATE, **cfg):
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True, voice_continue="c.mp3", **cfg),
                                        keep or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.players_known = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _plan(self, monitor, round_type, ids=(1, 0, 0)):
        monitor.st.round_type = round_type
        monitor.st.terror_ids = list(ids)
        return monitor._plan(round_type, list(ids), False)

    def test_continue_rounds(self):
        for chosen, killers in (("Ghost", "Ghost (Alternate)"), ("Fog", "Fog (Alternate)"),
                                ("8 Pages", "8 Pages (Alternate)")):
            monitor = self._monitor(continue_rounds={chosen})
            self.assertEqual(self._plan(monitor, killers), ("continue_rounds",), killers)
            self.assertEqual(monitor.st.round_type, killers, "種類そのものは変えない")

    def test_skip_rounds(self):
        for chosen, killers in (("Ghost", "Ghost (Alternate)"), ("Fog", "Fog (Alternate)"),
                                ("8 Pages", "8 Pages (Alternate)")):
            monitor = self._monitor(skip_rounds={chosen})
            self.assertEqual(self._plan(monitor, killers), ("round_skip",), killers)

    def test_continue_still_comes_first(self):
        monitor = self._monitor(continue_rounds={"Ghost"}, skip_rounds={"Ghost"})
        self.assertEqual(self._plan(monitor, "Ghost (Alternate)"), ("continue_rounds",))

    def test_the_skip_exceptions_are_as_before(self):
        """自爆指定より上に来るもの（Variant など）は今どおり"""
        monitor = self._monitor(skip_rounds={"Ghost"})
        with patch.object(GroupRound, "is_variant", return_value=True):
            self.assertNotEqual(self._plan(monitor, "Ghost (Alternate)"), ("round_skip",))
        with patch.object(RoundDecision, "is_open_special_round_target", return_value=True):
            self.assertNotEqual(self._plan(monitor, "Ghost (Alternate)"), ("round_skip",))

    def test_not_chosen_goes_to_the_list_as_before(self):
        monitor = self._monitor(continue_rounds={"Fog"}, skip_rounds={"Classic"})
        with patch.object(monitor, "_list_plan", return_value=("list",)) as list_plan:
            self.assertEqual(self._plan(monitor, "Ghost (Alternate)"), ("list",))
        list_plan.assert_called_once()

    def test_the_single_alternate_is_not_ghost_or_fog(self):
        monitor = self._monitor(continue_rounds={"Ghost", "Fog"}, skip_rounds={"Ghost", "Fog"})
        with patch.object(monitor, "_list_plan", return_value=("list",)):
            self.assertEqual(self._plan(monitor, "Alternate"), ("list",))

    def test_only_private_as_before(self):
        monitor = self._monitor(continue_rounds={"Ghost"}, instance_type=config.INSTANCE_PUBLIC)
        self.assertEqual(self._plan(monitor, "Ghost (Alternate)"), ("restricted",))

    def test_the_whole_line_continues_and_does_not_skip(self):
        """Ghost を全続行 → `Ghost (Alternate)` の Killers の行で自爆しない（前は【スキップ】していた）"""
        monitor = self._monitor(continue_rounds={"Ghost"})
        monitor._process("2026.10.10 14:43:35 Debug      -  Killers have been set - 1 0 0 // "
                         "Round type is Ghost (Alternate)")
        started = [getattr(c.kwargs["target"], "__name__", "")
                   for c in self.thread.call_args_list if "target" in c.kwargs]
        self.assertNotIn("do_skip", started)
        self.assertEqual(monitor.st.round_type, "Ghost (Alternate)")
        self.assertTrue(any("ラウンド指定で続行: Ghost (Alternate)" in m for m in monitor.logs), monitor.logs)

    def test_the_db_name_is_unchanged(self):
        monitor = self._monitor(continue_rounds={"Ghost"})
        monitor.st.round_type = "Ghost (Alternate)"
        self.assertEqual(monitor._round_type_for_db([1, 0, 0]), "Ghost (Alternate)")
