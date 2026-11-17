"""窓位置リセットの時点を設定で選ぶ（依頼者 2026-11-18）: ON（既定）はラウンド終了（RoundOver）から
config.CONTINUE_RESTORE_DELAY_SEC 後、OFF は今まで通り続行フリーズが外れた瞬間（死亡から）。
ほかの窓のフリーズ解除の時点はどちらでも変わらない。
本物の窓・OSC・時計は使わない（窓を戻す処理・待ち・予約はすべて偽物）"""
from tests.support import *  # noqa: F401,F403

T = "2026.11.18 10:00:00 Debug      -  "
DIED = T + "You died."
ROUND_OVER = T + "RoundOver"
START = (1699, 0, 2567, 702)            # 続行ラウンドの始まりの窓


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_continue_restore_window(True)
        SharedState.set_continue_restore_at_round_end(True)
        SharedState.continue_round_reset()
        for undo in (SharedState.set_continue_restore_window, SharedState.set_continue_restore_at_round_end):
            self.addCleanup(undo, False)
        self.addCleanup(SharedState.continue_round_reset)
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)
        # ActionExecutor の待ち（sleep）は秒数を残すだけで待たない
        self.sleeps = []
        fake_time = type(sys)("time_for_test")
        fake_time.time = fake_time.monotonic = lambda: 1000.0
        fake_time.sleep = self.sleeps.append
        p = patch.object(ActionExecutor, "time", fake_time)
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self, running=True):
        """続行フリーズを張った窓。窓を戻す処理・予約は偽物（予約は積むだけで、_run で走らせる）"""
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x30, osc_port=0), {},
                                        lambda _m: None, window_idx=3)
        monitor._running = running
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        st.in_round = True
        st.round_type = "Classic"
        st.is_continue_round = True
        st.continue_hook = monitor._on_continue_freeze
        self.daemons = []
        monitor._start_daemon = lambda target, *a: self.daemons.append((target.__name__, target, a))
        monitor._log = lambda _m: None
        self.restored = []
        action = monitor._action
        action._is_running = lambda: running
        action._log = lambda _m: None
        action._restore_continue_window = self.restored.append
        action.remember_continue_window = lambda: setattr(action, "_continue_rect", START)
        SharedState.continue_round_start(st)        # 続行フリーズを張る（窓の位置を覚える）
        self.assertEqual(action._continue_rect, START)
        return monitor

    def _names(self):
        return [name for name, _t, _a in self.daemons]

    def _run(self, *names):
        """積んだ予約のうち、名前が合うものを積んだ順に走らせる（走らせたものは外す）"""
        todo = [d for d in self.daemons if d[0] in names]
        self.daemons = [d for d in self.daemons if d[0] not in names]
        for _name, target, args in todo:
            target(*args)

    def _run_freeze_release(self, monitor):
        """死亡（または RoundOver）から数える続行フリーズの解除を走らせる。待ちは秒数だけ残す"""
        sleeps = []
        with patch.object(LogMonitor.time, "sleep", side_effect=sleeps.append):
            self._run("_release_continue_freeze_after_delay")
        self._run("after_continue_round")
        return sleeps


class TestOn(_Case):
    def test_round_over_restores_after_the_delay(self):
        monitor = self._monitor()
        monitor._process(DIED)
        monitor._process(ROUND_OVER)
        self.assertIn("restore_continue_window_at_round_end", self._names())
        self._run("restore_continue_window_at_round_end")
        self.assertEqual(self.sleeps, [config.CONTINUE_RESTORE_DELAY_SEC], "RoundOver から 5 秒待つ")
        self.assertEqual(self.restored, [START])
        self.assertIsNone(monitor._action._continue_rect, "戻したら忘れる")

    def test_the_delay_is_five_seconds(self):
        self.assertEqual(config.CONTINUE_RESTORE_DELAY_SEC, 5.0)

    def test_death_alone_does_not_restore(self):
        monitor = self._monitor()
        monitor._process(DIED)
        self.assertNotIn("restore_continue_window_at_round_end", self._names(), "死亡では予約しない")
        self.assertNotIn("restore_continue_window_now", self._names())
        self._run_freeze_release(monitor)           # 死亡から 5 秒後の続行フリーズの解除まで進めても
        self.assertEqual(self.restored, [])
        self.assertEqual(monitor._action._continue_rect, START, "まだ覚えている（RoundOver で戻す）")

    def test_the_freeze_release_after_death_does_not_restore(self):
        monitor = self._monitor()
        monitor._action.after_continue_round()
        self.assertEqual(self.restored, [])
        self.assertEqual(monitor._action._continue_rect, START, "矩形も忘れない")

    def test_a_new_round_during_the_wait_cancels_it(self):
        monitor = self._monitor()
        action, st = monitor._action, monitor.st
        seq = st.round_seq
        self.sleeps.clear()
        fake_time = type(sys)("time_for_test")
        fake_time.sleep = lambda sec: setattr(st, "round_seq", st.round_seq + 1)   # 待っている間に次のラウンド
        with patch.object(ActionExecutor, "time", fake_time):
            action.restore_continue_window_at_round_end(seq)
        self.assertEqual(self.restored, [])
        self.assertEqual(action._continue_rect, START, "次の続行ラウンドで覚え直すまでそのまま")

    def test_round_over_twice_restores_once(self):
        monitor = self._monitor()
        monitor._process(ROUND_OVER)
        monitor._process(ROUND_OVER)
        self._run("restore_continue_window_at_round_end")
        self.assertEqual(self.restored, [START])

    def test_finish_round_as_over_restores_at_once(self):
        """RoundOver が来ないまま片づけた（インスタンス移動・窓が消えた）→ その場で戻る（待たない）"""
        monitor = self._monitor()
        monitor._abandon_round("インスタンス移動")
        self.assertIn("restore_continue_window_now", self._names())
        self.assertNotIn("restore_continue_window_at_round_end", self._names())
        self._run("restore_continue_window_now")
        self.assertEqual(self.restored, [START])
        self.assertEqual(self.sleeps, [], "待たない")

    def test_the_teardown_and_the_wait_do_not_both_restore(self):
        monitor = self._monitor()
        action, st = monitor._action, monitor.st
        action.restore_continue_window_now()                                # 片づけ
        action.restore_continue_window_at_round_end(st.round_seq)           # 同じラウンドの RoundOver からの予約
        self.assertEqual(self.restored, [START], "1 回だけ")
        # 逆の順でも 1 回だけ
        self.restored.clear()
        action._continue_rect = START
        action.restore_continue_window_at_round_end(st.round_seq)
        action.restore_continue_window_now()
        action.after_continue_round()
        self.assertEqual(self.restored, [START])

    def test_two_threads_take_the_rect_once(self):
        """3 つの入口が同時に来ても、矩形を取れるのは 1 つだけ（鍵の中で取り出して忘れる）"""
        monitor = self._monitor()
        action = monitor._action
        for _ in range(200):
            action._continue_rect = START
            got, start = [], threading.Event()

            def take():
                start.wait()
                got.append(action._take_continue_rect())
            threads = [threading.Thread(target=take) for _ in range(4)]
            for t in threads:
                t.start()
            start.set()
            for t in threads:
                t.join()
            self.assertEqual([g for g in got if g is not None], [START])

    def test_switching_the_check_while_waiting_restores_once(self):
        """待っている間にチェックを OFF にしても、先に来た方が 1 回だけ戻す"""
        monitor = self._monitor()
        action, st = monitor._action, monitor.st
        monitor._process(ROUND_OVER)
        SharedState.set_continue_restore_at_round_end(False)
        self._run_freeze_release(monitor)                                   # OFF の入口（フリーズ解除）が先に来る
        self._run("restore_continue_window_at_round_end")                   # ON のときに積んだ予約が後から来る
        self.assertEqual(self.restored, [START])

    def test_the_continue_end_is_noop_but_remember_is_kept_for_the_wait(self):
        monitor = self._monitor()
        monitor._process(ROUND_OVER)
        self._run_freeze_release(monitor)
        self.assertEqual(self.restored, [], "フリーズ解除（RoundOver から 5 秒）では戻さない")
        self._run("restore_continue_window_at_round_end")
        self.assertEqual(self.restored, [START])


class TestOff(_Case):
    def setUp(self):
        super().setUp()
        SharedState.set_continue_restore_at_round_end(False)

    def test_off_restores_on_the_freeze_release(self):
        monitor = self._monitor()
        monitor._process(DIED)
        self.assertEqual(self.restored, [])
        self._run_freeze_release(monitor)
        self.assertEqual(self.restored, [START], "続行フリーズが外れた瞬間に戻る")

    def test_off_schedules_nothing_at_round_over(self):
        monitor = self._monitor()
        monitor._process(ROUND_OVER)
        self.assertNotIn("restore_continue_window_at_round_end", self._names())
        self.assertNotIn("restore_continue_window_now", self._names())
        self.assertEqual(self.sleeps, [])

    def test_off_finish_round_as_over_uses_the_freeze_release(self):
        monitor = self._monitor()
        monitor._abandon_round("インスタンス移動")
        self.assertNotIn("restore_continue_window_now", self._names(), "OFF は片づけで予約しない")
        self._run("after_continue_round")                                   # フリーズ解除の合図
        self.assertEqual(self.restored, [START], "二重に戻さない")


class TestCommon(_Case):
    def test_the_other_windows_freeze_release_is_unchanged(self):
        """ほかの窓のフリーズ解除は、ON / OFF とも死亡から CONTINUE_FREEZE_RELEASE_DELAY_SEC（霧は 2 秒）"""
        for at_round_end in (True, False):
            for fog, expected in ((False, config.CONTINUE_FREEZE_RELEASE_DELAY_SEC),
                                  (True, config.FOG_FREEZE_RELEASE_DELAY_SEC)):
                with self.subTest(at_round_end=at_round_end, fog=fog):
                    SharedState.continue_round_reset()
                    SharedState.set_continue_restore_at_round_end(at_round_end)
                    monitor = self._monitor()
                    monitor.st.fog = fog
                    monitor._process(DIED)
                    self.assertIn("_release_continue_freeze_after_delay", self._names())
                    sleeps = []
                    with patch.object(LogMonitor.time, "sleep", side_effect=sleeps.append):
                        self._run("_release_continue_freeze_after_delay")
                    self.assertEqual(sleeps, [expected])
                    self.assertFalse(monitor.st.continue_freeze_held, "ほかの窓のフリーズは外れる")
        self.assertEqual(config.CONTINUE_FREEZE_RELEASE_DELAY_SEC, 5.0)

    def test_restore_window_off_wins(self):
        for at_round_end in (True, False):
            SharedState.set_continue_restore_at_round_end(at_round_end)
            monitor = self._monitor()
            SharedState.set_continue_restore_window(False)
            monitor._process(ROUND_OVER)
            self._run("restore_continue_window_at_round_end", "restore_continue_window_now")
            monitor._action.after_continue_round()
            monitor._action.restore_continue_window_now()
            self.assertEqual(self.restored, [], at_round_end)
            SharedState.set_continue_restore_window(True)
            SharedState.continue_round_reset()

    def test_stopped_restores_nothing(self):
        for at_round_end in (True, False):
            SharedState.set_continue_restore_at_round_end(at_round_end)
            monitor = self._monitor(running=False)
            monitor._action.after_continue_round()
            monitor._action.restore_continue_window_now()
            monitor._action.restore_continue_window_at_round_end(monitor.st.round_seq)
            self.assertEqual(self.restored, [], at_round_end)
            SharedState.continue_round_reset()

    def test_nothing_remembered_restores_nothing(self):
        monitor = self._monitor()
        monitor._action._continue_rect = None
        monitor._action.restore_continue_window_now()
        monitor._action.restore_continue_window_at_round_end(monitor.st.round_seq)
        self.assertEqual(self.restored, [])


if __name__ == "__main__":
    unittest.main()
