"""最大 WS の急な増えを見張る（MemoryWatch）。増えたとき、動いていた仕事を debug.log に 1 行書く。
本物のスレッド・待ち・Tk は使わない（スレッドを立てる所は偽物・待ちは差し替え・after は呼ばれたら失敗）。
DebugLog.write は偽物にして、本物の debug.log にも書かない"""
from tests.support import *  # noqa: F401,F403
import tkinter
import MemoryWatch
import ProcStats
import TaskStats
import UiInbox

MB = 1024 * 1024


def _fresh(test):
    """TaskStats の累計・生きている一覧と、MemoryWatch の「立てたスレッド」を空にする（終われば元に戻す）"""
    p = patch.multiple(TaskStats, _finished={}, _live={}, _finished_total={}, _tk_current=None, _tk={})
    p.start()
    test.addCleanup(p.stop)
    q = patch.object(MemoryWatch, "_thread", None)
    q.start()
    test.addCleanup(q.stop)


class _Stop(BaseException):
    """_loop を止めるための合図（Exception ではないので _loop の except に捕まらない）"""


class TestJumpLine(unittest.TestCase):
    def line(self, prev, peak, **kw):
        args = dict(ws=257.1 * MB, private=230.8 * MB, live=[("Foo", 1)], finished=[("Bar", 3)], others=[("Baz", 1)],
                    tk_name="_pump")
        args.update(kw)
        return MemoryWatch.jump_line(prev, peak, **args)

    def test_below_threshold_is_none(self):
        self.assertIsNone(self.line(297 * MB, 396 * MB))
        self.assertIsNone(self.line(297 * MB, 297 * MB))
        self.assertIsNone(self.line(297 * MB, 100 * MB))        # 減った

    def test_missing_peak_is_none(self):
        self.assertIsNone(self.line(None, 1190 * MB))
        self.assertIsNone(self.line(297 * MB, None))

    def test_at_threshold_has_all_parts(self):
        text = self.line(297 * MB, 397 * MB)                    # ちょうど 100MB
        self.assertEqual(
            text, "[メモリ] 急増 +100MB（最大 WS 297→397MB）・WS 257.1MB・Private 230.8MB"
                  "・動いている: Foo・この間に終わった: Bar ×3・ほかのスレッド: Baz・画面: _pump")

    def test_empty_sections_and_unknown_memory(self):
        text = self.line(297 * MB, 1190 * MB, ws=None, private=None, live=[], finished=[], others=[], tk_name=None)
        self.assertIn("+893MB（最大 WS 297→1190MB）", text)
        self.assertIn("WS ?・Private ?", text)
        self.assertIn("動いている: なし・この間に終わった: なし・ほかのスレッド: なし・画面: なし", text)

    def test_duplicates_show_count_and_names_are_capped(self):
        rows = [(f"T{i}", 2) for i in range(5)]
        with patch.object(config, "MEMORY_LOG_TOP", 2):
            text = self.line(0, 200 * MB, live=rows, finished=rows, others=rows)
        self.assertIn("動いている: T0 ×2 / T1 ×2 …・", text)
        self.assertIn("この間に終わった: T0 ×2 / T1 ×2 …・", text)
        self.assertIn("ほかのスレッド: T0 ×2 / T1 ×2 …・", text)


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(config.MEMORY_JUMP_CHECK_SEC, 5.0)
        self.assertEqual(config.MEMORY_JUMP_MB, 100)


class TestCheckOnce(unittest.TestCase):
    def setUp(self):
        _fresh(self)
        self.write = patch.object(MemoryWatch.DebugLog, "write").start()
        self.addCleanup(patch.stopall)
        self.peaks = []
        patch.object(ProcStats, "peak_working_set", side_effect=lambda: self.peaks.pop(0)).start()
        patch.object(ProcStats, "memory", return_value=(257 * MB, 230 * MB)).start()
        self.state = {}

    def test_first_check_only_sets_baseline(self):
        self.peaks = [5000 * MB]
        self.assertIsNone(MemoryWatch._check_once(self.state))
        self.write.assert_not_called()
        self.assertEqual(self.state["peak"], 5000 * MB)

    def test_none_peak_does_nothing(self):
        self.peaks = [None]
        self.assertIsNone(MemoryWatch._check_once(self.state))
        self.write.assert_not_called()
        self.assertNotIn("peak", self.state)
        self.peaks = [300 * MB, None, 1000 * MB]
        MemoryWatch._check_once(self.state)
        MemoryWatch._check_once(self.state)                     # None は土台も動かさない
        self.assertEqual(self.state["peak"], 300 * MB)
        self.assertIn("急増 +700MB", MemoryWatch._check_once(self.state))

    def test_small_rise_writes_nothing(self):
        self.peaks = [300 * MB, 399 * MB]
        MemoryWatch._check_once(self.state)
        self.assertIsNone(MemoryWatch._check_once(self.state))
        self.write.assert_not_called()

    def test_jump_writes_one_line_with_live_finished_and_tk(self):
        self.peaks = [297 * MB, 1190 * MB]
        MemoryWatch._check_once(self.state)
        before = TaskStats.register_live("LiveWorker")
        self.addCleanup(TaskStats.unregister_live, before)
        TaskStats.run("RoundThread", lambda: None)
        TaskStats.run("RoundThread", lambda: None)
        other_stop = threading.Event()
        other = threading.Thread(target=other_stop.wait, name="OtherPyThread", daemon=True)
        other.start()
        self.addCleanup(other.join, 2)
        self.addCleanup(other_stop.set)
        before_tk = TaskStats.tk_enter("_poll_host_save")
        self.addCleanup(TaskStats.tk_exit, before_tk)
        text = MemoryWatch._check_once(self.state)
        self.write.assert_called_once_with(text)
        self.assertIn("急増 +893MB（最大 WS 297→1190MB）", text)
        self.assertIn("LiveWorker", text)
        self.assertIn("RoundThread ×2", text)
        self.assertIn("OtherPyThread", text)
        self.assertIn("画面: _poll_host_save", text)

    def test_finished_only_covers_the_interval(self):
        self.peaks = [100 * MB, 120 * MB, 400 * MB]
        MemoryWatch._check_once(self.state)
        TaskStats.run("Old", lambda: None)                      # 1 回目と 2 回目の間
        MemoryWatch._check_once(self.state)
        TaskStats.run("New", lambda: None)                      # 2 回目と 3 回目の間
        text = MemoryWatch._check_once(self.state)
        self.assertIn("この間に終わった: New ×1", text)
        self.assertNotIn("Old", text)

    def test_never_calls_tk(self):
        self.peaks = [100 * MB, 900 * MB]

        def fail(*_a, **_k):
            raise AssertionError("見張りが Tk の after を呼んだ")
        with patch.object(tkinter.Misc, "after", fail), patch.object(tkinter.Misc, "after_idle", fail):
            MemoryWatch._check_once(self.state)
            self.assertIsNotNone(MemoryWatch._check_once(self.state))

    def test_gui_log_is_not_touched(self):
        # 見張りのモジュールは画面（mainGUI・LogMonitor など）を読み込まない
        import inspect
        source = inspect.getsource(MemoryWatch)
        for name in ("mainGUI", "tkinter", "UiInbox", "ActionExecutor", "SharedState"):
            self.assertNotIn(f"import {name}", source)


class TestLoop(unittest.TestCase):
    def setUp(self):
        _fresh(self)
        self.addCleanup(patch.stopall)

    def run_loop(self, rounds, check):
        sleeps = []

        def fake_sleep(sec):
            sleeps.append(sec)
            if len(sleeps) > rounds:
                raise _Stop
        patch.object(MemoryWatch, "_sleep", fake_sleep).start()
        patch.object(MemoryWatch, "_check_once", check).start()
        with self.assertRaises(_Stop):
            MemoryWatch._loop()
        return sleeps

    def test_sleeps_check_interval_then_checks(self):
        calls = []
        sleeps = self.run_loop(3, lambda state: calls.append(state))
        self.assertEqual(sleeps, [5.0] * 4)
        self.assertEqual(len(calls), 3)
        self.assertIs(calls[0], calls[1], "state は持ち回る")

    def test_exception_is_logged_and_loop_continues(self):
        calls = []

        def check(state):
            calls.append(1)
            raise ValueError("boom")
        with patch.object(MemoryWatch.DebugLog, "exception") as exception:
            self.run_loop(2, check)
        self.assertEqual(len(calls), 2)
        self.assertEqual(exception.call_args_list, [call("MemoryWatch")] * 2)


class TestStart(unittest.TestCase):
    def setUp(self):
        _fresh(self)
        self.addCleanup(patch.stopall)

    def test_start_twice_makes_one_thread(self):
        fake = MagicMock()
        with patch.object(TaskStats, "thread", return_value=fake) as make:
            MemoryWatch.start()
            MemoryWatch.start()
        make.assert_called_once_with("MemoryWatch", MemoryWatch._loop, thread_name="MemoryWatch")
        fake.start.assert_called_once_with()

    def test_real_thread_is_named_daemon_and_registered(self):
        # 待ちを差し替えて、立てた本物のスレッドを 1 周で止める
        reached = threading.Event()
        release = threading.Event()

        def fake_sleep(_sec):
            reached.set()
            release.wait(5)
            raise SystemExit        # スレッドの終わりの合図（Thread は黙って終わる）
        patch.object(MemoryWatch, "_sleep", fake_sleep).start()
        MemoryWatch.start()
        thread = MemoryWatch._thread
        try:
            self.assertTrue(reached.wait(5))
            self.assertEqual(thread.name, "MemoryWatch")
            self.assertTrue(thread.daemon)
            self.assertIn("MemoryWatch", [entry[0] for entry in TaskStats._live.values()])
            self.assertEqual(sum(1 for t in threading.enumerate() if t.name == "MemoryWatch"), 1)
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())


class TestTkMarker(unittest.TestCase):
    def setUp(self):
        _fresh(self)
        self.addCleanup(patch.stopall)

    def test_tk_timed_sets_name_inside_and_restores(self):
        seen = []

        @TaskStats.tk_timed
        def _job():
            seen.append(TaskStats.current_tk())
        self.assertIsNone(TaskStats.current_tk())
        _job()
        self.assertEqual(seen, ["_job"])
        self.assertIsNone(TaskStats.current_tk())

    def test_tk_timed_restores_on_exception(self):
        @TaskStats.tk_timed
        def _bad():
            raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            _bad()
        self.assertIsNone(TaskStats.current_tk())

    def test_nested_restores_outer(self):
        seen = []

        @TaskStats.tk_timed
        def _inner():
            seen.append(("inner", TaskStats.current_tk()))

        @TaskStats.tk_timed
        def _outer():
            seen.append(("before", TaskStats.current_tk()))
            _inner()
            seen.append(("after", TaskStats.current_tk()))
        _outer()
        self.assertEqual(seen, [("before", "_outer"), ("inner", "_inner"), ("after", "_outer")])
        self.assertIsNone(TaskStats.current_tk())

    def test_uiinbox_sets_name_and_restores(self):
        seen = []

        def _from_worker():
            seen.append(TaskStats.current_tk())

        def _raises():
            seen.append(TaskStats.current_tk())
            raise RuntimeError("x")
        inbox = UiInbox.UiInbox(MagicMock())
        inbox.put(_from_worker)
        inbox.put(_raises)
        inbox.put(lambda: seen.append(TaskStats.current_tk()))
        with patch.object(UiInbox.DebugLog, "exception"):
            inbox.drain()
        self.assertEqual(seen, ["_from_worker", "_raises", "<lambda>"])
        self.assertIsNone(TaskStats.current_tk())

    def test_uiinbox_job_calling_timed_function_restores_outer(self):
        seen = []

        @TaskStats.tk_timed
        def _timed():
            seen.append(TaskStats.current_tk())

        def _job():
            _timed()
            seen.append(TaskStats.current_tk())
        inbox = UiInbox.UiInbox(MagicMock())
        inbox.put(_job)
        inbox.drain()
        self.assertEqual(seen, ["_timed", "_job"])
        self.assertIsNone(TaskStats.current_tk())


class TestFinishedSince(unittest.TestCase):
    def setUp(self):
        _fresh(self)
        self.addCleanup(patch.stopall)

    def test_none_mark_is_baseline(self):
        TaskStats.run("A", lambda: None)
        diff, mark = TaskStats.finished_since(None)
        self.assertEqual(diff, [])
        self.assertEqual(mark, {"A": 1})

    def test_only_the_difference_sorted_by_count(self):
        TaskStats.run("A", lambda: None)
        _diff, mark = TaskStats.finished_since(None)
        TaskStats.run("A", lambda: None)
        for _ in range(3):
            TaskStats.run("B", lambda: None)
        TaskStats.run("C", lambda: None)
        diff, mark2 = TaskStats.finished_since(mark)
        self.assertEqual(diff, [("B", 3), ("A", 1), ("C", 1)])
        self.assertEqual(TaskStats.finished_since(mark2)[0], [])

    def test_snapshot_semantics_unchanged(self):
        TaskStats.run("A", lambda: None)
        TaskStats.run("A", lambda: None)
        _diff, mark = TaskStats.finished_since(None)
        TaskStats.finished_since(mark)
        with patch.object(ProcStats, "thread_cpu", return_value=0.0):
            TaskStats.snapshot(0.0, 0.0, None, wall_now=1.0)        # 初回（終わった仕事は捨てる）
            TaskStats.run("A", lambda: None)
            snap = TaskStats.snapshot(0.0, 0.0, None, wall_now=2.0)
        self.assertEqual([(n, c) for n, c, _cpu in snap["finished"]], [("A", 1)])    # 累計ではなく前回からの分
        self.assertEqual(TaskStats._finished_total["A"], 3, "snapshot は累計を空にしない")

    def test_nested_live_is_counted_once_per_unregister(self):
        before = TaskStats.register_live("Outer")
        inner = TaskStats.register_live("Inner")
        TaskStats.unregister_live(inner)
        TaskStats.unregister_live(before)
        self.assertEqual(TaskStats._finished_total, {"Inner": 1, "Outer": 1})


class TestAliveNames(unittest.TestCase):
    def setUp(self):
        _fresh(self)
        self.addCleanup(patch.stopall)

    def test_named_with_counts_and_others_exclude_main_and_named(self):
        stop = threading.Event()

        def named():
            TaskStats.run("Worker", stop.wait)
        threads = [threading.Thread(target=named, daemon=True) for _ in range(2)]
        threads.append(threading.Thread(target=stop.wait, name="PlainPy", daemon=True))
        for t in threads:
            t.start()
        try:
            deadline = time.monotonic() + 5
            while len(TaskStats._live) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            live, others = TaskStats.alive_names()
            self.assertEqual(live, [("Worker", 2)])
            names = [n for n, _c in others]
            self.assertIn("PlainPy", names)
            self.assertNotIn(threading.main_thread().name, names)
            self.assertNotIn("Worker", names)
        finally:
            stop.set()
            for t in threads:
                t.join(5)


class TestWiring(unittest.TestCase):
    def test_app_starts_watch_after_log_memory(self):
        import inspect
        import mainGUI
        source = inspect.getsource(mainGUI.App.__init__)
        self.assertIn("MemoryWatch.start()", source)
        self.assertLess(source.index("self._log_memory()"), source.index("MemoryWatch.start()"))


if __name__ == "__main__":
    unittest.main()
