"""受理した Verified にラウンドが続かないかを、壁時計でも見る（LogMonitor._check_pending_verified）

今まではログの時刻（st.log_now）だけで 15 秒（VERIFIED_ROUND_START_WAIT_SEC）を測っていた。log_now は
ログの行の時刻なので、その窓のログに次の行が来ないと進まず、定期の Verified を受理と取り違えた窓は、
次の行が来るまで（最長で次の定期の約 300 秒後）Begin を押し直さなかった。受理した時点の壁時計
（st.begin_accepted_at。pending_verified_time と同じ所で立つ）からも測る。時計は差し替える
"""
from tests.support import *  # noqa: F401,F403


BASE = datetime(2026, 10, 1, 14, 23, 9).timestamp()      # ログの時刻
CLOCK = 50_000.0                                          # 壁時計（受理した時点）
WAIT = config.VERIFIED_ROUND_START_WAIT_SEC
ROUND_START = "This round is taking place at Facility (12) and the round type is Classic"


def stamp(at):
    return datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  "


class TestPendingVerifiedWallClock(unittest.TestCase):
    def _monitor(self, auto_begin=True, instance_type=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=auto_begin), {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        self.started = []
        patcher = patch.object(monitor, "_start_daemon",
                               side_effect=lambda f, *a: self.started.append((f.__name__, a)))
        patcher.start()
        self.addCleanup(patcher.stop)
        return monitor

    def _process(self, monitor, at, body, clock=CLOCK):
        with patch.object(LogMonitor.time, "time", return_value=clock), \
             patch.object(SharedState, "get_speed_detect", return_value=False), \
             patch.object(PlaySound, "play_sound"), patch.object(Recorder, "on_round_over"):
            monitor._process(stamp(at) + body)

    def _accept(self, monitor):
        """位相を知らない起動直後: Verified Round End と同じ秒の定期の Verified を、受理と取り違える
        （壁時計は CLOCK で受理。log_now は BASE + 13）"""
        monitor.st.last_begin_press_at = CLOCK
        self._process(monitor, BASE, "RoundOver")
        self._process(monitor, BASE + 13, "Verified Round End")
        self._process(monitor, BASE + 13, "Verified")
        self.assertTrue(monitor.st.begin_done, "前提: 受理と判断")
        self.assertEqual(monitor.st.pending_verified_time, BASE + 13)
        self.assertEqual(monitor.st.begin_accepted_at, CLOCK, "pending と同じ所で壁時計も立つ")

    def _check(self, monitor, clock):
        with patch.object(LogMonitor.time, "time", return_value=clock):
            monitor._check_pending_verified()

    def _again(self):
        return [args for name, args in self.started if name == "do_begin_again"]

    def test_the_wall_clock_alone_moves_it_on_when_no_next_line_comes(self):
        monitor = self._monitor()
        self._accept(monitor)
        seq = monitor.st.round_seq
        log_now = monitor.st.log_now

        self._check(monitor, CLOCK + WAIT)
        self.assertTrue(monitor.st.begin_done, "15 秒ちょうどではまだ待つ")
        self.assertEqual(self._again(), [])
        self.assertEqual(monitor.st.pending_verified_time, BASE + 13)

        with patch.object(monitor._verified, "on_begin_not_followed",
                          wraps=monitor._verified.on_begin_not_followed) as not_followed:
            self._check(monitor, CLOCK + WAIT + 0.5)

        self.assertEqual(monitor.st.log_now, log_now, "ログの時刻は進んでいない（次の行が来ていない）")
        not_followed.assert_called_once_with(BASE + 13)            # 渡す時刻は今までどおりログの時刻
        self.assertEqual(monitor._verified.last_periodic, BASE + 13)
        self.assertEqual(monitor.st.pending_verified_time, 0.0)
        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(monitor.st.begin_accepted_at, 0.0)
        self.assertTrue(any("直前の Verified は定期シグナルでした" in m for m in monitor.logs), monitor.logs)
        self.assertTrue(any("押し直します" in m for m in monitor.logs), monitor.logs)
        self.assertEqual(self._again(), [(seq,)])

        self._check(monitor, CLOCK + 600)
        self.assertEqual(len(self._again()), 1, "1 回だけ")

    def test_within_15_seconds_nothing_happens(self):
        monitor = self._monitor()
        self._accept(monitor)
        for elapsed in (0.0, 1.0, WAIT - 0.1, WAIT):
            self._check(monitor, CLOCK + elapsed)
            self.assertTrue(monitor.st.begin_done, elapsed)
            self.assertEqual(monitor.st.pending_verified_time, BASE + 13, elapsed)
        self.assertEqual(self._again(), [])
        self.assertEqual(monitor.logs[-1:], ["[窓1] ✅ Connecting"])

    def test_the_old_way_by_the_log_time_still_works(self):
        monitor = self._monitor()
        self._accept(monitor)
        monitor.st.log_now = BASE + 13 + WAIT
        self._check(monitor, CLOCK + 1)                  # 壁時計は 1 秒しかたっていない
        self.assertTrue(monitor.st.begin_done, "ログの時刻でも 15 秒ちょうどではまだ待つ")
        monitor.st.log_now = BASE + 13 + WAIT + 1
        self._check(monitor, CLOCK + 1)
        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(len(self._again()), 1)
        self.assertEqual(monitor._verified.last_periodic, BASE + 13)

    def test_a_round_start_that_came_does_nothing_even_after_a_long_wall_time(self):
        monitor = self._monitor()
        self._accept(monitor)
        self._process(monitor, BASE + 20, ROUND_START, clock=CLOCK + 7)
        self.assertEqual(monitor.st.pending_verified_time, 0.0)
        self._check(monitor, CLOCK + 600)
        self.assertIsNone(monitor._verified.last_periodic, "本物の Begin。位相は動かさない")
        self.assertEqual(self._again(), [])
        self.assertFalse(any("押し直します" in m or "定期シグナルでした" in m for m in monitor.logs))

    def test_a_late_read_does_not_decide_periodic_when_the_round_start_is_already_in_the_file(self):
        """読み取りが遅れて、ラウンド開始の行はもう書かれているがまだ読んでいなかった。_run は行を処理して
        から _check_pending_verified を呼ぶので、その回に読んだ行が先に反映される"""
        monitor = self._monitor()
        self._accept(monitor)
        line = (stamp(BASE + 20) + ROUND_START).encode("utf-8")
        with patch.object(LogMonitor.time, "time", return_value=CLOCK + 40), \
             patch.object(SharedState, "get_speed_detect", return_value=False), \
             patch.object(PlaySound, "play_sound"), patch.object(Recorder, "on_round_over"):
            monitor._process_lines([line])              # _run の 1 周: 行を処理して…
            monitor._check_pending_verified()           # …そのあとで確認する
        self.assertEqual(monitor.st.pending_verified_time, 0.0)
        self.assertIsNone(monitor._verified.last_periodic)
        self.assertEqual(self._again(), [])
        self.assertTrue(monitor.st.in_round)

    def test_run_loop_checks_after_processing_the_lines(self):
        """順番を変えない: _run の 1 周で _process_lines のあとに _check_pending_verified"""
        import inspect
        body = inspect.getsource(LogMonitor.LogMonitor._run)
        self.assertLess(body.index("self._process_lines(lines)"), body.index("self._check_pending_verified()"))

    def test_a_stopped_monitor_or_one_in_a_round_only_clears_the_pending(self):
        for spoil in ("stopped", "in_round"):
            monitor = self._monitor()
            self._accept(monitor)
            if spoil == "stopped":
                monitor._running = False
            else:
                monitor.st.in_round = True
            self._check(monitor, CLOCK + 30)
            self.assertEqual(monitor.st.pending_verified_time, 0.0, spoil)
            self.assertTrue(monitor.st.begin_done, spoil)
            self.assertEqual(self._again(), [], spoil)

    def test_windows_the_tool_does_not_begin_reset_but_do_not_press(self):
        for auto_begin, itype in ((False, config.INSTANCE_PRIVATE), (True, config.INSTANCE_PUBLIC)):
            monitor = self._monitor(auto_begin=auto_begin, instance_type=itype)
            monitor.st.begin_done = True
            monitor.st.pending_verified_time = BASE + 13
            monitor.st.begin_accepted_at = CLOCK
            monitor.st.log_now = BASE + 13
            self._check(monitor, CLOCK + 30)
            self.assertFalse(monitor.st.begin_done, (auto_begin, itype))
            self.assertEqual(self._again(), [], (auto_begin, itype))

    def test_without_a_wall_clock_stamp_only_the_log_time_counts(self):
        """begin_accepted_at が立っていない（0）なら、壁時計では決めない（今までの動き）"""
        monitor = self._monitor()
        monitor.st.begin_done = True
        monitor.st.pending_verified_time = BASE + 13
        monitor.st.begin_accepted_at = 0.0
        monitor.st.log_now = BASE + 13
        self._check(monitor, CLOCK + 99999)
        self.assertEqual(monitor.st.pending_verified_time, BASE + 13)
        self.assertTrue(monitor.st.begin_done)

    def test_nothing_pending_does_nothing(self):
        monitor = self._monitor()
        monitor.st.begin_done = True
        monitor.st.begin_accepted_at = CLOCK            # 受理の印は残っているが pending は無い
        monitor.st.log_now = BASE
        self._check(monitor, CLOCK + 99999)
        self.assertTrue(monitor.st.begin_done)
        self.assertEqual(monitor.logs, [])
        self.assertEqual(self._again(), [])


if __name__ == "__main__":
    unittest.main()
