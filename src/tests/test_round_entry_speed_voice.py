"""ラウンド突入フリーズでも 8 Pages / Punished のアナウンスを鳴らす（依頼者 2026-11-17）。
速度検知と二重にならないよう、速度検知が働き、その種別のチェックが ON の窓では鳴らさない"""
from tests.support import *  # noqa: F401,F403


class _EntryCase(unittest.TestCase):
    HWND = 777

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        SharedState.set_speed_detect(True)
        SharedState.round_freeze_reset()
        SharedState.speed_freeze_reset()
        stats = patch.object(ConnectDB, "register_round")
        stats.start()
        self.addCleanup(stats.stop)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_speed_detect, config.SPEED_DETECT_ENABLED)
        self.addCleanup(SharedState.round_freeze_reset)
        self.addCleanup(SharedState.speed_freeze_reset)

    def _monitor(self, rounds=("8 Pages", "Punished"), **cfg):
        config_ = WindowConfig(hwnd=self.HWND, do_skip=True, freeze_rounds=set(rounds),
                               voice_8pages="8p.mp3", voice_punish="pn.mp3",
                               voice_fog="fog.mp3", voice_cracked="cr.mp3", **cfg)
        monitor = LogMonitor.LogMonitor(config_, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        return monitor

    @staticmethod
    def _run_now(target=None, args=(), daemon=None, **_kw):
        if target is not None:
            target(*args)
        return MagicMock()

    def _enter(self, monitor, round_type):
        """そのラウンドへ突入して、鳴らした (ファイル, 種類) の一覧を返す"""
        line = ("2026.09.28 00:10:00 Debug      -  This round is taking place "
                f"at Facility (12) and the round type is {round_type}")
        with patch.object(WindowOperator, "focus_window", return_value=True), \
             patch.object(PlaySound, "play_sound") as play, \
             patch.object(LogMonitor.threading, "Thread", side_effect=self._run_now):
            monitor._process(line)
        return [(c.args[0], c.kwargs.get("kind")) for c in play.call_args_list]


class TestRoundEntrySpeedVoice(_EntryCase):
    def test_8pages_entry_speaks_when_the_speed_check_is_off(self):
        monitor = self._monitor(freeze_8pages=False)
        self.assertEqual(monitor._round_entry_voice("8 Pages"), "8p.mp3")
        self.assertEqual(self._enter(monitor, "8 Pages"), [("8p.mp3", "8pages")])
        self.assertTrue(monitor.st.round_freeze_held)

    def test_punished_entry_speaks_when_the_speed_check_is_off(self):
        monitor = self._monitor(freeze_punish=False)
        self.assertEqual(monitor._round_entry_voice("Punished"), "pn.mp3")
        self.assertEqual(self._enter(monitor, "Punished"), [("pn.mp3", "punish")])

    def test_8pages_entry_is_silent_when_the_speed_check_is_on(self):
        monitor = self._monitor(freeze_8pages=True)
        self.assertEqual(monitor._round_entry_voice("8 Pages"), "")
        self.assertEqual(self._enter(monitor, "8 Pages"), [])
        self.assertTrue(monitor.st.round_freeze_held, "フリーズは張る（鳴らさないだけ）")

    def test_punished_entry_is_silent_when_the_speed_check_is_on(self):
        monitor = self._monitor(freeze_punish=True)
        self.assertEqual(monitor._round_entry_voice("Punished"), "")
        self.assertEqual(self._enter(monitor, "Punished"), [])
        self.assertTrue(monitor.st.round_freeze_held)

    def test_the_check_is_per_kind(self):
        """8 Pages のチェックだけ ON なら、Punished は鳴る"""
        monitor = self._monitor(freeze_8pages=True)
        self.assertEqual(monitor._round_entry_voice("8 Pages"), "")
        self.assertEqual(monitor._round_entry_voice("Punished"), "pn.mp3")

    def test_the_speed_check_does_not_matter_when_speed_detect_is_off(self):
        SharedState.set_speed_detect(False)
        monitor = self._monitor(freeze_8pages=True, freeze_punish=True)
        self.assertEqual(monitor._round_entry_voice("8 Pages"), "8p.mp3")
        self.assertEqual(monitor._round_entry_voice("Punished"), "pn.mp3")
        self.assertEqual(self._enter(monitor, "8 Pages"), [("8p.mp3", "8pages")])

    def test_the_other_rounds_are_unchanged(self):
        """ほかのラウンドは、速度検知のチェックに関わらず今まで通り鳴る"""
        monitor = self._monitor(rounds=("Fog", "Cracked"), freeze_8pages=True, freeze_punish=True)
        self.assertEqual(monitor._round_entry_voice("Fog"), "fog.mp3")
        self.assertEqual(monitor._round_entry_voice("Cracked"), "cr.mp3")
        self.assertEqual(monitor._round_entry_voice("Classic"), "")
        self.assertEqual(self._enter(monitor, "Fog"), [("fog.mp3", "fog")])

    def test_the_voice_kinds_include_the_two(self):
        kinds = LogMonitor.LogMonitor.ROUND_ENTRY_VOICE_KINDS
        self.assertEqual(kinds["8 Pages"], "8pages")
        self.assertEqual(kinds["Punished"], "punish")
        for kind in ("8pages", "punish"):
            self.assertIn(kind, PlaySound.VOICE_KINDS)

    def test_no_voice_without_the_round_entry_check(self):
        """突入フリーズで選んでいなければ、何も起きない"""
        monitor = self._monitor(rounds=("Fog",))
        self.assertEqual(self._enter(monitor, "8 Pages"), [])
        self.assertFalse(monitor.st.round_freeze_held)

    def test_hands_free_still_says_nothing(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor()
        self.assertEqual(self._enter(monitor, "8 Pages"), [])
        self.assertFalse(monitor.st.round_freeze_held, "放置モードでは突入フリーズを張らない")


class TestSpeedDetectAnnounceIsUnchanged(unittest.TestCase):
    """速度検知側の鳴らし方は今まで通り（ActionExecutor は変えていない）"""

    def setUp(self):
        SharedState.speed_freeze_reset()
        SharedState.set_hands_free(False)
        self.addCleanup(SharedState.speed_freeze_reset)

    def _executor(self, auto_begin_active, **freeze):
        cfg = WindowConfig(hwnd=123, osc_port=9000, voice_8pages="8p.mp3", voice_punish="pn.mp3", **freeze)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                             auto_begin_active=lambda: auto_begin_active)

    def _announce(self, ex, kind):
        with patch.object(ActionExecutor.PlaySound, "play_sound") as play, \
             patch.object(WindowOperator, "focus_window", return_value=True):
            ex._announce_speed_kind(kind, 6.5)
        return [(c.args[0], c.kwargs.get("kind")) for c in play.call_args_list]

    def test_speaks_when_the_check_is_on(self):
        self.assertEqual(self._announce(self._executor(True, freeze_8pages=True), "8pages"),
                         [("8p.mp3", "8pages")])
        self.assertEqual(self._announce(self._executor(True, freeze_punish=True), "punish"),
                         [("pn.mp3", "punish")])

    def test_silent_for_a_tool_driven_window_when_the_check_is_off(self):
        self.assertEqual(self._announce(self._executor(True), "8pages"), [])

    def test_speaks_for_a_human_window_even_when_the_check_is_off(self):
        self.assertEqual(self._announce(self._executor(False), "8pages"), [("8p.mp3", "8pages")])

    def test_normal_is_silent(self):
        self.assertEqual(self._announce(self._executor(False, freeze_8pages=True), "normal"), [])


if __name__ == "__main__":
    unittest.main()
