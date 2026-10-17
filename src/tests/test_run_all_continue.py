"""FW（依頼者 2026-10-17）: 全続行で Run を選んだ窓は、Run でリスポーンしない。普通のラウンドの「全続行で、続行リストには
乗らない」と同じ扱い（ログ・all_continue_round。アナウンス・他窓フリーズ・録画は無し）。音量は Run のまま「その他」。
偽の action・偽の PlaySound。本物の入力・音は使わない"""
from tests.support import *  # noqa: F401,F403


class _Case(unittest.TestCase):
    LINE = "2026.10.10 13:03:20 Debug      -  This round is taking place at Facility (12) and the round type is {}"

    def setUp(self):
        SharedState.set_run_respawn(True)
        self.addCleanup(SharedState.set_run_respawn, False)
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)

    def _start(self, round_type="Run", continue_rounds=("Run",), instance=config.INSTANCE_PRIVATE):
        logs = []
        cfg = WindowConfig(hwnd=0x40, osc_port=9000, do_skip=True, voice_continue="continue.mp3",
                           continue_rounds=set(continue_rounds))
        monitor = LogMonitor.LogMonitor(cfg, {}, logs.append, window_idx=2)
        monitor.st.instance_type = instance
        monitor._running = True
        targets = []
        monitor._start_daemon = lambda target, *a: targets.append((getattr(target, "__name__", ""), a, target))
        with patch.object(ConnectDB, "register_round"), \
             patch.object(PlaySound, "play_sound") as play, \
             patch.object(SharedState, "continue_round_start") as freeze:
            monitor._process(self.LINE.format(round_type))
        monitor.logs, monitor.targets, monitor.play, monitor.freeze = logs, targets, play, freeze
        return monitor

    def _respawns(self, monitor):
        return [t[:2] for t in monitor.targets if t[0] == "do_run_respawn"]


class TestAllContinueRun(_Case):
    def test_it_does_not_respawn_and_behaves_like_an_unlisted_all_continue(self):
        monitor = self._start()
        st = monitor.st
        self.assertEqual(self._respawns(monitor), [])
        self.assertTrue(any("Round: Run 【全続行（リスポーンしない）】" in m for m in monitor.logs), monitor.logs)
        self.assertFalse(any("死亡待ち" in m for m in monitor.logs), monitor.logs)
        self.assertTrue(st.all_continue_round)
        self.assertFalse(st.is_continue_round)
        monitor.freeze.assert_not_called()
        monitor.play.assert_not_called()

    def test_the_afk_watch_still_runs(self):
        monitor = self._start()
        self.assertIn(monitor._action.do_afk_watch, [t[2] for t in monitor.targets])

    def test_the_volume_stays_other_for_run(self):
        """音量は Run のまま「その他」（依頼者 2026-10-17）。all_continue_round が True でも"""
        monitor = self._start()
        self.assertTrue(monitor.st.all_continue_round)
        self.assertEqual(WindowVolume.category_of(monitor.st), WindowVolume.OTHER)

    def test_round_over_puts_the_flag_back(self):
        monitor = self._start()
        with patch.object(ConnectDB, "register_round"):
            monitor._process("2026.10.10 13:05:00 Debug      -  RoundOver")
        self.assertFalse(monitor.st.all_continue_round)


class TestOtherCases(_Case):
    def test_without_run_in_the_list_it_respawns_as_before(self):
        monitor = self._start(continue_rounds=("Classic",))
        self.assertEqual(self._respawns(monitor), [("do_run_respawn", (monitor.st.round_seq,))])
        self.assertTrue(any("Round: Run 【死亡待ち・アイテム購入予定】" in m for m in monitor.logs), monitor.logs)
        self.assertFalse(monitor.st.all_continue_round)

    def test_with_the_setting_off_it_does_not_respawn_either_way(self):
        SharedState.set_run_respawn(False)
        monitor = self._start()
        self.assertEqual(self._respawns(monitor), [])
        self.assertTrue(any("Round: Run 【全続行（リスポーンしない）】" in m for m in monitor.logs), monitor.logs)
        monitor = self._start(continue_rounds=())
        self.assertEqual(self._respawns(monitor), [])
        self.assertTrue(any("死亡待ち" in m for m in monitor.logs), monitor.logs)

    def test_a_group_window_is_as_before(self):
        for instance in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._start(instance=instance)
            self.assertFalse(monitor._is_all_continue_round(), instance)
            self.assertEqual(self._respawns(monitor), [("do_run_respawn", (monitor.st.round_seq,))], instance)
            self.assertTrue(any("死亡待ち" in m for m in monitor.logs), instance)
            self.assertFalse(monitor.st.all_continue_round, instance)

    def test_other_rounds_are_untouched(self):
        monitor = self._start(round_type="Classic", continue_rounds=("Run",))
        self.assertEqual(self._respawns(monitor), [])
        self.assertFalse(any("全続行（リスポーンしない）" in m for m in monitor.logs), monitor.logs)


if __name__ == "__main__":
    unittest.main()
