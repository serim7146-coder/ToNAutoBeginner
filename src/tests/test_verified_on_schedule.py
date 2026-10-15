"""予定と重なった Verified は、押していても定期（EP 1'。2026-10-13 09:38 窓5）／
Run のリスポーンの後 0.5 秒待ってから後ろへ（EP 2）。本物の OSC・入力は使わない"""
from tests.support import *  # noqa: F401,F403
from datetime import datetime
import tests.test_run_respawn as _respawn_tests   # Run の偽の世界を借りる（ここでは回さない）


class TestWindow5At0938(unittest.TestCase):
    """位相 09:33:17（正しく定期と判断した）→ 前面で UseRight を連打している間の 09:38:17（予定ぴったり）"""

    def setUp(self):
        self.phase = datetime(2026, 10, 13, 9, 33, 17).timestamp()
        self.monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=True, hwnd=0x100, osc_port=9000), {},
                                             lambda _m: None, window_idx=5)
        self.monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.monitor.logs = []
        self.monitor.logger = self.monitor.logs.append
        self.monitor._auto_begin_active = lambda: True
        self.monitor._verified.last_periodic = self.phase
        self.written = []
        p = patch.object(DebugLog, "write", self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def _stamp(self, at):
        return datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  "

    def _feed(self, at, body, clock=60_000.0):
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(SharedState, "get_speed_detect", return_value=True), \
             patch.object(LogMonitor.time, "time", return_value=clock), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            self.monitor._process(self._stamp(at) + body)
        return [c.kwargs["target"].__func__.__name__ for c in thread.call_args_list
                if "target" in c.kwargs and hasattr(c.kwargs["target"], "__func__")]

    def _to_0938(self):
        st = self.monitor.st
        self._feed(self.phase + 180, "This round is taking place at Facility (12) and the round type is Run")
        self._feed(self.phase + 250, "RoundOver")
        self._feed(self.phase + 263, "Verified Round End")
        st.last_begin_press_at = 60_000.0 - 0.05             # 前面の連打（直前に押したと記録されている）
        return self._feed(self.phase + 300, "Verified", clock=60_000.0)

    def test_it_is_periodic_and_no_item_fetch(self):
        started = self._to_0938()
        st = self.monitor.st
        self.assertFalse(st.begin_done, "受理にしない（アイテム取得に行かない）")
        self.assertNotIn("do_speed_strafe", started)
        self.assertEqual(self.monitor._verified.last_periodic, self.phase + 300, "位相は今どおり進める")
        self.assertIn("[窓5] Verified: 予定（09:38:17）と重なった → 定期（ツールが直前に押していた）", self.written)
        self.assertIn("[窓5] Verified を無視（定期シグナル）", self.monitor.logs)

    def test_off_by_the_tolerance_is_still_periodic(self):
        tol = config.VERIFIED_PERIODIC_TOL_SEC
        self.assertEqual(tol, 2.5)                     # EY: 1.0 → 2.5
        st = self.monitor.st
        self._feed(self.phase + 263, "Verified Round End")
        st.last_begin_press_at = 60_000.0
        self._feed(self.phase + 300 + tol, "Verified", clock=60_000.0)
        self.assertFalse(st.begin_done)

    def test_a_real_begin_on_the_schedule_is_known_by_the_round_start(self):
        """本物の受理が予定と重なった → 12 秒後にラウンド開始 → 本物だったと分かる。位相はそのまま"""
        self._to_0938()
        self._feed(self.phase + 312, "This round is taking place at Facility (12) and the round type is Classic")
        self.assertTrue(any("本物の Begin だった。予定と重なった1回なので定期の位相はそのまま" in m
                            for m in self.written), self.written)
        self.assertEqual(self.monitor._verified.last_periodic, self.phase + 300,
                         "次の予定（09:43:17）を見失わない")

    def test_an_off_schedule_ignored_one_still_rolls_the_phase_back(self):
        """予定の外で「まだ押していない → 定期」とした分は、今どおり位相を戻す"""
        st = self.monitor.st
        self._feed(self.phase + 263, "Verified Round End")
        st.last_begin_press_at = 0.0
        self.monitor._verified.last_periodic = None             # 位相を知らない
        self._feed(self.phase + 270, "Verified", clock=60_000.0)
        self._feed(self.phase + 282, "This round is taking place at Facility (12) and the round type is Classic")
        self.assertIsNone(self.monitor._verified.last_periodic)
        self.assertTrue(any("定期の位相を戻す" in m for m in self.written), self.written)


class TestRunWaitsAfterTheRespawn(_respawn_tests._Case):
    """「Player respawned」から RUN_BACK_DELAY_SEC 待ってから後ろへ（直後の速さ 1.2 を動き出しと取り違えた）"""

    def test_it_waits_half_a_second(self):
        self.assertEqual(config.RUN_BACK_DELAY_SEC, 0.5)
        line, back_at = [], []
        real_click = WindowOperator.mouse_click.side_effect

        def click(sec, pause=False):
            real_click(sec, pause)
            line.append(self.w.now[0])          # 押した瞬間に行が来る（偽の世界）
        self.osc.on_press = lambda address, stop: (back_at.append(self.w.now[0])
                                                   if address == "/input/MoveBackward" else None)
        with patch.object(WindowOperator, "mouse_click", side_effect=click):
            self._run()
        gap = back_at[0] - line[0]
        self.assertGreaterEqual(gap, config.RUN_BACK_DELAY_SEC - 1e-9)
        self.assertLess(gap, config.RUN_BACK_DELAY_SEC + 0.2)
        self.assertEqual(self.w.events, self.WHOLE, "押す順・値（2.85・0.9）は今どおり")

    def _stop_after_the_front_is_back(self, spoil):
        returned = []
        real_return = WindowOperator.return_front.side_effect
        with patch.object(WindowOperator, "return_front",
                          side_effect=lambda loan: returned.append(1) or real_return(loan)):
            self.w.on_sleep = lambda: (spoil() if returned else None)
            self._run()
        self.assertFalse([e for e in self.w.events if e[0] == "osc"], "後ろへも左へも押さない")

    def test_stopping_while_waiting_does_not_back_off(self):
        self._stop_after_the_front_is_back(lambda: self.running.update(on=False))

    def test_the_next_round_while_waiting_does_not_back_off(self):
        self._stop_after_the_front_is_back(lambda: setattr(self.st, "round_seq", self.st.round_seq + 1))


class TestTwoLinesInTheSameSecond(TestWindow5At0938):
    """受理と定期が重なると Verified は 2 行来る（VRChat のログ 80 本で、その後ラウンドが始まったもののうち
    2 秒以内に 2 行が 64 回・1 行が 2 回）。1 行目は予定と重なって定期、2 行目は予定がもう 300 秒先なので受理"""

    def _two_lines(self, round_starts=True):
        st = self.monitor.st
        self._feed(self.phase + 250, "RoundOver")
        self._feed(self.phase + 263, "Verified Round End")
        st.last_begin_press_at = 60_000.0 - 0.05
        first = self._feed(self.phase + 300, "Verified", clock=60_000.0)
        after_first = (st.begin_done, self.monitor._verified.last_periodic)
        second = self._feed(self.phase + 300, "Verified", clock=60_000.1)
        if round_starts:
            self._feed(self.phase + 312, "This round is taking place at Facility (12) and the round type is Classic")
        return first, second, after_first

    def test_the_first_is_periodic_the_second_is_begin(self):
        first, second, (done_after_first, phase_after_first) = self._two_lines(round_starts=False)
        self.assertFalse(done_after_first, "1 行目は定期")
        self.assertEqual(phase_after_first, self.phase + 300, "1 行目で位相を進める")
        self.assertNotIn("do_speed_strafe", first)
        self.assertTrue(self.monitor.st.begin_done, "2 行目は受理")
        self.assertIn("do_speed_strafe", second)
        self.assertIn("[窓5] Verified: 予定（09:38:17）と重なった → 定期（ツールが直前に押していた）", self.written)
        self.assertIn("[窓5] [事象] verified → 受理", self.written)

    def test_the_round_start_keeps_the_phase(self):
        """2 行のとき、1 行目は本当に定期だった。ラウンドが始まっても位相（09:38:17）はそのまま"""
        self._two_lines()
        self.assertEqual(self.monitor._verified.last_periodic, self.phase + 300)
        self.assertFalse(any("定期の位相を戻す" in m for m in self.written), self.written)

    def test_the_next_periodic_is_on_schedule(self):
        """位相を見失わない: 300 秒後（09:43:17）の定期は予定どおり定期"""
        self._two_lines()
        st = self.monitor.st
        self._feed(self.phase + 500, "RoundOver")
        self._feed(self.phase + 513, "Verified Round End")
        st.last_begin_press_at = 0.0
        self._feed(self.phase + 600, "Verified", clock=70_000.0)
        self.assertFalse(st.begin_done)
        self.assertIn("[窓5] Verified: 予定（09:43:17）と重なった → 定期", self.written)


class TestTheReasonAndThePhase(unittest.TestCase):
    def test_the_schedule_is_forgotten_on_the_next_verified(self):
        tr = VerifiedTracker.VerifiedTracker()
        tr.last_periodic = 1000.0
        tr.on_round_end_verified(1200.0)
        self.assertEqual(tr.on_verified(1300.0, True), VerifiedTracker.PERIODIC)
        self.assertEqual(tr.schedule_due, 1300.0)
        tr.on_round_start(1310.0)
        tr.on_round_over(1700.0)                    # 予定 1600 はラウンド中 → RoundOver 直後に遅れて出る
        self.assertEqual(tr.on_verified(1700.5, False), VerifiedTracker.PERIODIC)
        self.assertIsNone(tr.schedule_due, "予定と重なっていない（遅れて出た定期）")


class TestALatePeriodicIsStillRolledBack(TestWindow5At0938):
    def test_a_late_periodic_then_a_round_start_rolls_the_phase_back(self):
        """予定と重ならない定期（RoundOver 直後に遅れて出た）の後にラウンド開始 → 今どおり位相を戻す"""
        self.monitor._verified.last_periodic = self.phase - 100      # 予定 phase+200 はラウンド中
        self._feed(self.phase + 100, "This round is taking place at Facility (12) and the round type is Classic")
        self._feed(self.phase + 250, "RoundOver")
        self._feed(self.phase + 250, "Verified")                     # 遅れて出た定期
        self.assertEqual(self.monitor._verified.last_periodic, self.phase + 250)
        self.assertFalse(any("予定（" in m for m in self.written))
        self._feed(self.phase + 262, "This round is taking place at Facility (12) and the round type is Classic")
        self.assertEqual(self.monitor._verified.last_periodic, self.phase - 100)
        self.assertTrue(any("定期の位相を戻す" in m for m in self.written), self.written)


class TestRunStopsWaitingAtOnce(TestRunWaitsAfterTheRespawn):
    def test_stopping_while_waiting_ends_at_once(self):
        returned_at = []
        real_return = WindowOperator.return_front.side_effect

        def give_back(loan):
            returned_at.append(self.w.now[0])
            return real_return(loan)
        with patch.object(WindowOperator, "return_front", side_effect=give_back):
            self.w.on_sleep = lambda: (self.running.update(on=False) if returned_at else None)
            self._run()
        self.assertLess(self.w.now[0] - returned_at[-1], 0.25, "0.5 秒待ち切らずにやめる")
