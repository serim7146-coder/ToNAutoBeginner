"""FI（依頼者 2026-10-16「可視化はしていいと思いますね」）: CPU の使い道を仕事ごとに debug.log へ記録する（TaskStats）。
本物の入力・窓の操作は使わない（スレッドは使う。App は隠したまま。スレッドを立てる所は TaskStats.thread を偽物にして、
名前と渡し方だけ確かめる）"""
from tests.support import *  # noqa: F401,F403
import functools
import types
import ProcStats
import TaskStats
from tests.test_oscquery import _Case as _OSCQueryCase


def _fresh_state(test):
    """TaskStats の持ち物を空にする（テストが終われば元に戻す）"""
    p = patch.multiple(TaskStats, _finished={}, _live={}, _tk={}, _last_wall=None, _last_process_cpu=None,
                       _last_tk_cpu=None, _last_peak_ws=None)
    p.start()
    test.addCleanup(p.stop)


def _fake_clock(test, *values):
    """TaskStats の time.thread_time() を、決めた値の順にする"""
    seq = iter(values)
    fake = types.SimpleNamespace(thread_time=lambda: next(seq), monotonic=time.monotonic)
    p = patch.object(TaskStats, "time", fake)
    p.start()
    test.addCleanup(p.stop)


class _Spy:
    """TaskStats.thread の代わり。呼ばれた名前・target・渡し方を覚え、スレッドは立てない（run_now ならその場で走らせる）"""

    def __init__(self, test, run_now=False):
        self.calls, self.threads = [], []
        self.run_now = run_now

        def fake(name, target, *args, **kwargs):
            self.calls.append((name, target, args, kwargs))
            thread = MagicMock()
            if self.run_now:
                thread.start.side_effect = lambda: target(*args, **{k: v for k, v in kwargs.items()
                                                                     if k not in ("daemon", "thread_name")})
            self.threads.append(thread)
            return thread
        p = patch.object(TaskStats, "thread", side_effect=fake)
        p.start()
        test.addCleanup(p.stop)

    @property
    def names(self):
        return [c[0] for c in self.calls]

    def started(self):
        return [t.start.called for t in self.threads]


# ── run / thread ─────────────────────────────
class TestRun(unittest.TestCase):
    def setUp(self):
        _fresh_state(self)

    def test_a_finished_job_is_counted_by_name(self):
        _fake_clock(self, 0.0, 2.5, 0.0, 1.0)
        self.assertEqual(TaskStats.run("job", lambda: "done"), "done")
        TaskStats.run("job", lambda: None)
        self.assertEqual(TaskStats._finished, {"job": [2, 3.5]})

    def test_arguments_go_to_the_target(self):
        got = []
        TaskStats.run("job", lambda *a, **k: got.append((a, k)), 1, 2, x=3)
        self.assertEqual(got, [((1, 2), {"x": 3})])

    def test_an_error_is_counted_and_goes_out(self):
        _fake_clock(self, 0.0, 1.5)

        def fail():
            raise ValueError("boom")
        with self.assertRaises(ValueError):
            TaskStats.run("job", fail)
        self.assertEqual(TaskStats._finished, {"job": [1, 1.5]})
        self.assertEqual(TaskStats._live, {})

    def test_it_is_live_only_while_it_runs(self):
        seen = {}

        def look():
            seen["inside"] = {k: (v[0], v[1]) for k, v in TaskStats._live.items()}
        TaskStats.run("job", look)
        self.assertEqual(seen["inside"], {threading.get_ident(): ("job", threading.get_native_id())},
                         "GetThreadTimes で読むための OS のスレッド番号も覚える")
        self.assertEqual(TaskStats._live, {})

    def test_a_run_inside_a_run_leaves_the_outer_one_live(self):
        seen = []

        def outer():
            TaskStats.run("inner", lambda: None)
            seen.append({v[0] for v in TaskStats._live.values()})
        TaskStats.run("outer", outer)
        self.assertEqual(seen, [{"outer"}])
        self.assertEqual(sorted(TaskStats._finished), ["inner", "outer"])

    def test_the_cpu_of_a_real_thread_is_counted(self):
        def busy():
            end = time.perf_counter() + 0.4
            while time.perf_counter() < end:
                pass
        t = TaskStats.thread("busy", busy)
        t.start()
        t.join(10)
        count, cpu = TaskStats._finished["busy"]
        self.assertEqual(count, 1)
        self.assertGreater(cpu, 0.05, "0.4 秒回し続けた CPU")
        self.assertLess(cpu, 2.0)


class TestThread(unittest.TestCase):
    def setUp(self):
        _fresh_state(self)

    def test_it_returns_a_daemon_thread_not_started(self):
        t = TaskStats.thread("job", lambda: None)
        self.assertIsInstance(t, threading.Thread)
        self.assertTrue(t.daemon)
        self.assertIsNone(t.ident, "start は呼び出し側")
        self.assertFalse(t.is_alive())
        self.assertEqual(TaskStats._finished, {})
        self.assertFalse(TaskStats.thread("job", lambda: None, daemon=False).daemon)

    def test_the_target_runs_through_run_when_started(self):
        got = []
        t = TaskStats.thread("job", lambda *a, **k: got.append((a, k)), 1, 2, key="v")
        t.start()
        t.join(5)
        self.assertEqual(got, [((1, 2), {"key": "v"})])
        self.assertEqual(TaskStats._finished["job"][0], 1)

    def test_an_error_in_the_thread_is_not_swallowed(self):
        errors = []
        old = threading.excepthook
        threading.excepthook = lambda args: errors.append(args.exc_type)
        self.addCleanup(setattr, threading, "excepthook", old)

        def fail():
            raise KeyError("x")
        t = TaskStats.thread("job", fail)
        t.start()
        t.join(5)
        self.assertEqual(errors, [KeyError], "握らない（スレッドの例外は今までどおり excepthook へ）")
        self.assertEqual(TaskStats._finished["job"][0], 1)

    def test_it_is_called_the_same_way_as_a_plain_thread(self):
        """Thread への渡し方は今までと同じ（target・あれば args・daemon）。テストの偽物の Thread がそのまま効く"""
        def job(a, b):
            pass
        with patch.object(TaskStats.threading, "Thread") as thread:
            TaskStats.thread("job", job)
            TaskStats.thread("job", job, 1, 2, daemon=True)
            TaskStats.thread("job", job, daemon=False, thread_name="OBSRecorder")
        first, second, third = thread.call_args_list
        self.assertEqual(set(first.kwargs), {"target", "daemon"})
        self.assertEqual(set(second.kwargs), {"target", "args", "daemon"})
        self.assertEqual(second.kwargs["args"], (1, 2))
        self.assertEqual((third.kwargs["name"], third.kwargs["daemon"]), ("OBSRecorder", False))

    def test_the_target_looks_like_the_original(self):
        class K:
            def method(self):
                return "m"
        k = K()
        with patch.object(TaskStats.threading, "Thread") as thread:
            TaskStats.thread("n", k.method)
            TaskStats.thread("n", len)
        target = thread.call_args_list[0].kwargs["target"]
        self.assertEqual((target.__name__, target.__func__.__name__, target.__self__), ("method", "method", k))
        self.assertEqual(target(), "m")
        self.assertEqual(TaskStats._finished["n"][0], 1, "呼ぶと run を通る")
        with self.assertRaises(AttributeError):
            thread.call_args_list[1].kwargs["target"].__func__     # 組み込み関数には無い
        with self.assertRaises(AttributeError):
            TaskStats._Tracked.__new__(TaskStats._Tracked)._target     # 空のコピーでも自分を呼び続けない


# ── snapshot / line ─────────────────────────────
class TestSnapshot(unittest.TestCase):
    def setUp(self):
        _fresh_state(self)
        self.cpu = {}
        p = patch.object(ProcStats, "thread_cpu", side_effect=lambda native_id: self.cpu.get(native_id))
        p.start()
        self.addCleanup(p.stop)

    def _live(self, ident, name, native_id, last=0.0):
        TaskStats._live[ident] = [name, native_id, last]

    def test_the_first_one_has_no_difference(self):
        TaskStats._finished["old"] = [3, 9.0]
        snap = TaskStats.snapshot(1.0, 10.0, 100, wall_now=50.0)
        self.assertTrue(snap["first"])
        self.assertEqual((snap["finished"], snap["live"], snap["process_delta"], snap["tk_delta"],
                          snap["peak_delta"]), ([], [], None, None, None))
        self.assertEqual(TaskStats.line(snap), "[CPU] 計測開始")
        self.assertEqual(TaskStats._finished, {}, "始まる前の分は捨てる")

    def test_the_second_one_has_the_differences(self):
        TaskStats.snapshot(1.0, 10.0, 100 * 2 ** 20, wall_now=1000.0)
        snap = TaskStats.snapshot(23.1, 88.2, 105 * 2 ** 20, wall_now=1600.0)
        self.assertFalse(snap["first"])
        self.assertAlmostEqual(snap["elapsed"], 600.0)
        self.assertAlmostEqual(snap["process_delta"], 78.2)
        self.assertAlmostEqual(snap["tk_delta"], 22.1)
        self.assertEqual((snap["peak_ws"], snap["peak_delta"]), (105 * 2 ** 20, 5 * 2 ** 20))

    def test_finished_jobs_are_in_order_of_cpu(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        TaskStats._finished.update({"a": [5, 1.0], "b": [1, 9.0], "c": [72, 3.5], "d": [2, 3.5]})
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["finished"], [("b", 1, 9.0), ("c", 72, 3.5), ("d", 2, 3.5), ("a", 5, 1.0)],
                         "CPU の多い順（同じなら名前の順）")

    def test_it_is_reset_each_time(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        TaskStats._finished["a"] = [1, 1.0]
        self.assertEqual(TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)["finished"], [("a", 1, 1.0)])
        self.assertEqual(TaskStats.snapshot(0.0, 0.0, 1, wall_now=1200.0)["finished"], [])

    def test_nothing_finished_and_nothing_live(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertTrue(TaskStats.line(snap).endswith("・終わった: なし・生きている: なし・未計上 0.0秒"))

    def test_live_threads_are_the_difference_from_last_time(self):
        self._live(1, "LogMonitor._run", 101)
        self._live(2, "LogMonitor._run", 102)
        self._live(3, "WindowVolume._run", 103)
        self.cpu = {101: 5.0, 102: 3.0, 103: 0.5}
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)                    # 土台（5.0・3.0・0.5）
        self.cpu = {101: 7.5, 102: 3.5, 103: 0.75}
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["live"], [("LogMonitor._run", 2, 3.0), ("WindowVolume._run", 1, 0.25)])
        self.cpu = {101: 7.5, 102: 3.5, 103: 0.75}
        self.assertEqual([row[2] for row in TaskStats.snapshot(0.0, 0.0, 1, wall_now=1200.0)["live"]],
                         [0.0, 0.0], "増えなければ 0")

    def test_a_thread_that_cannot_be_opened_is_skipped(self):
        self._live(1, "gone", 101)
        self._live(2, "here", 102)
        self.cpu = {102: 1.0}
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        self.cpu = {102: 2.0}
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["live"], [("here", 1, 1.0)])
        self.assertEqual(TaskStats._live[1][2], 0.0, "読めなかった分は土台を動かさない")

    def test_a_job_that_ends_after_being_read_is_not_counted_twice(self):
        """生きている側で読んだ分は、終わるときに引く（合わせて 4.0 秒。8 秒にも 7 秒にもならない）"""
        _fake_clock(self, 0.0, 4.0)
        started = threading.Event()
        release = threading.Event()
        done = []

        def job():
            started.set()
            release.wait(5)
        t = threading.Thread(target=lambda: (TaskStats.run("long", job), done.append(1)))
        t.start()
        self.assertTrue(started.wait(5))
        native_id = TaskStats._live[t.ident][1]
        self.cpu = {native_id: 0.0}
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        self.cpu = {native_id: 3.0}
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["live"], [("long", 1, 3.0)])
        release.set()
        t.join(5)
        self.assertEqual(done, [1])
        later = TaskStats.snapshot(0.0, 0.0, 1, wall_now=1200.0)
        self.assertEqual(later["live"], [])
        self.assertEqual(later["finished"], [("long", 1, 1.0)], "終わりまでの 4.0 から読んだ 3.0 を引いた残り")

    def test_values_that_could_not_be_read(self):
        TaskStats.snapshot(1.0, None, None, wall_now=0.0)
        snap = TaskStats.snapshot(2.0, 5.0, None, wall_now=600.0)
        self.assertEqual((snap["process_delta"], snap["peak_delta"]), (None, None))
        text = TaskStats.line(snap)
        self.assertIn("10分で ?（?）・画面 1.0秒", text)
        snap = TaskStats.snapshot(3.0, 9.0, 7, wall_now=1200.0)
        self.assertAlmostEqual(snap["process_delta"], 4.0)
        self.assertIsNone(snap["peak_delta"], "前回が取れていなければ差は出さない")


class TestLine(unittest.TestCase):
    SNAP = {"first": False, "elapsed": 600.0, "process_delta": 78.2, "tk_delta": 22.1,
            "finished": [("do_after_round", 72, 18.4), ("_fetch_item", 6, 9.7)],
            "live": [("LogMonitor._run", 6, 12.0), ("WindowVolume._run", 1, 0.3)],
            "peak_ws": 0, "peak_delta": 0}

    def test_the_format(self):
        self.assertEqual(
            TaskStats.line(self.SNAP),
            "[CPU] 10分で 78.2秒（13.0%）・画面 22.1秒・終わった: do_after_round ×72 18.4秒 / _fetch_item ×6 9.7秒"
            "・生きている: LogMonitor._run ×6 12.0秒 / WindowVolume._run 0.3秒・未計上 ?")

    def test_only_the_top_few_with_a_mark_for_the_rest(self):
        self.assertEqual(config.MEMORY_LOG_TOP, 8)
        rows = [(f"job{i}", i + 1, 10.0 - i) for i in range(10)]
        snap = dict(self.SNAP, finished=rows, live=rows[:3])
        text = TaskStats.line(snap)
        self.assertIn("job7 ×8 3.0秒 …・生きている:", text)
        self.assertNotIn("job8", text.split("生きている")[0])
        with patch.object(config, "MEMORY_LOG_TOP", 2):
            text = TaskStats.line(snap)
        self.assertIn("終わった: job0 ×1 10.0秒 / job1 ×2 9.0秒 …・生きている: job0 10.0秒 / job1 ×2 9.0秒 …", text)

    def test_a_short_span_is_in_seconds(self):
        self.assertTrue(TaskStats.line(dict(self.SNAP, elapsed=30.0, process_delta=3.0)).startswith(
            "[CPU] 30秒で 3.0秒（10.0%）・"))

    def test_names_are_not_numbered_by_window(self):
        """窓の番号は入れない（名前で束ねて、本数で分かる）"""
        snap = dict(self.SNAP, live=[("OSCReceiver._run", 6, 6.1)])
        self.assertIn("生きている: OSCReceiver._run ×6 6.1秒", TaskStats.line(snap))


# ── ProcStats ─────────────────────────────
class TestProcStats(unittest.TestCase):
    def test_the_numbers_of_this_process(self):
        for value in (ProcStats.process_cpu(), ProcStats.peak_working_set(),
                      ProcStats.thread_cpu(threading.get_native_id())):
            self.assertIsNotNone(value)
        self.assertGreater(ProcStats.peak_working_set(), 1024 * 1024)
        self.assertGreaterEqual(ProcStats.peak_working_set(), ProcStats.memory()[0])
        self.assertGreaterEqual(ProcStats.process_cpu(), ProcStats.thread_cpu(threading.get_native_id()))

    def test_a_thread_that_cannot_be_opened_is_none(self):
        self.assertIsNone(ProcStats.thread_cpu(0xFFFFFFF0))

    def test_another_threads_cpu_is_read_by_its_native_id(self):
        box = {}
        stop = threading.Event()

        def busy():
            box["id"] = threading.get_native_id()
            end = time.perf_counter() + 0.3
            while time.perf_counter() < end:
                pass
            stop.wait(5)
        t = threading.Thread(target=busy, daemon=True)
        t.start()
        time.sleep(0.45)
        mine = time.thread_time()
        other = ProcStats.thread_cpu(box["id"])
        stop.set()
        t.join(5)
        self.assertGreater(other, 0.05)
        self.assertLess(ProcStats.thread_cpu(threading.get_native_id()) - mine, 0.5)

    def test_it_matches_the_threads_own_clock(self):
        own = ProcStats.thread_cpu(threading.get_native_id())
        self.assertAlmostEqual(own, time.thread_time(), delta=0.1)

    def test_the_memory_line_ends_with_the_peak(self):
        self.assertRegex(ProcStats.line(),
                         r"^\[メモリ\] WS \d+\.\dMB・Private \d+\.\dMB・ハンドル \d+・スレッド \d+・最大 WS \d+MB（初回）$")
        mb = 2 ** 20
        self.assertTrue(ProcStats.line(1034 * mb, 0).endswith("・最大 WS 1034MB（前回から +0）"))
        self.assertTrue(ProcStats.line(1034 * mb, 60 * mb).endswith("・最大 WS 1034MB（前回から +60）"))


# ── _log_memory ─────────────────────────────
class TestLogMemory(unittest.TestCase):
    def setUp(self):
        _fresh_state(self)
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)
        self.app = type("A", (), {})()
        self.app.after = MagicMock()
        self.app._log_memory = "次の予約"

    def test_two_lines_and_the_first_time_only_starts_the_count(self):
        mainGUI.App._log_memory(self.app)
        self.assertEqual(len(self.written), 2)
        self.assertTrue(self.written[0].startswith("[メモリ] WS "))
        self.assertTrue(self.written[0].endswith("（初回）"))
        self.assertEqual(self.written[1], "[CPU] 計測開始")
        self.app.after.assert_called_once_with(600_000, "次の予約")

    def test_the_second_time_has_the_differences_and_the_peak_rise(self):
        mb = 2 ** 20
        with patch.object(ProcStats, "peak_working_set", side_effect=[100 * mb, 160 * mb]):
            mainGUI.App._log_memory(self.app)
            mainGUI.App._log_memory(self.app)
        self.assertEqual(len(self.written), 4)
        self.assertTrue(self.written[2].endswith("・最大 WS 160MB（前回から +60）"), self.written[2])
        self.assertRegex(self.written[3], r"^\[CPU\] \d+秒で \d+\.\d秒（[\d.]+%）・画面 \d+\.\d秒(（上位: .*）)?・終わった: .*・生きている: ")
        self.assertEqual(self.app.after.call_count, 2)

    def test_a_failure_is_logged_and_the_next_one_is_still_booked(self):
        with patch.object(TaskStats, "snapshot", side_effect=RuntimeError("x")), \
             patch.object(DebugLog, "exception") as exception:
            mainGUI.App._log_memory(self.app)
        exception.assert_called_once_with("mainGUI._log_memory")
        self.app.after.assert_called_once_with(600_000, "次の予約")

    def test_the_screen_thread_cpu_is_the_calling_threads(self):
        with patch.object(TaskStats, "snapshot", wraps=TaskStats.snapshot) as snapshot,              patch.object(mainGUI, "time", types.SimpleNamespace(thread_time=lambda: 123.5)):
            mainGUI.App._log_memory(self.app)
        self.assertEqual(snapshot.call_args.args[0], 123.5, "画面の CPU は time.thread_time()（呼んだスレッド＝Tk のスレッド）")


# ── スレッドを立てる所 ─────────────────────────────
class TestSitesGoThroughTaskStats(unittest.TestCase):
    def setUp(self):
        _fresh_state(self)
        self.spy = _Spy(self)
        p = patch.object(DebugLog, "write")
        p.start()
        self.addCleanup(p.stop)

    def assertOne(self, name, target=None, args=(), started=True):
        self.assertEqual(self.spy.names, [name])
        _name, got_target, got_args, kwargs = self.spy.calls[0]
        if target is not None:
            self.assertEqual(got_target, target)
        self.assertEqual(got_args, tuple(args))
        self.assertIs(kwargs.get("daemon"), True)
        self.assertEqual(self.spy.started(), [started])

    def test_log_monitor_start_daemon_names_it_by_the_target(self):
        def do_after_round(a, b):
            pass

        def _run():
            pass
        monitor = MagicMock()
        thread = LogMonitor.LogMonitor._start_daemon(monitor, do_after_round, 1, 2)
        self.assertOne("do_after_round", do_after_round, (1, 2))
        self.assertIs(thread, self.spy.threads[0])
        self.spy.calls.clear(), self.spy.threads.clear()
        LogMonitor.LogMonitor._start_daemon(monitor, _run)
        self.assertOne("LogMonitor._run", _run)
        self.spy.calls.clear(), self.spy.threads.clear()
        LogMonitor.LogMonitor._start_daemon(monitor, functools.partial(do_after_round, 1), 2)      # __name__ が無い呼び出し物
        self.assertOne("partial", args=(2,))

    def test_log_monitor_start_daemon_with_a_bound_method(self):
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1, osc_port=9000), WindowState(), lambda: True,
                                           lambda _m: None)
        LogMonitor.LogMonitor._start_daemon(MagicMock(), ex.do_skip)
        self.assertOne("do_skip", ex.do_skip)

    def test_off_gui(self):
        def work():
            pass
        mainGUI.App._off_gui(work)
        self.assertOne("work", work)
        self.spy.calls.clear(), self.spy.threads.clear()
        mainGUI.App._off_gui(work, "mainGUI._poll_x")
        self.assertOne("mainGUI._poll_x", work)

    def test_the_polls_give_their_own_names(self):
        app = MagicMock()
        app.tool_rows = []
        mainGUI.App._poll_host_save(app)
        mainGUI.App._poll_tool_buttons(app)
        self.assertEqual([c.args[1] for c in app._off_gui.call_args_list],
                         ["mainGUI._poll_host_save", "mainGUI._poll_tool_buttons"])

    def test_the_gui_workers(self):
        """画面から立てる裏の仕事。名前は用途"""
        app = MagicMock()
        app._capturing_key = False
        mainGUI.App._begin_capture_key(app)
        self.assertEqual(self.spy.names, ["mainGUI._begin_capture_key"])
        app = MagicMock()
        app._arranging = False
        app._arrange_targets.return_value = []
        app._window_layout = {}
        mainGUI.App._arrange_windows(app)
        app = MagicMock()
        app._obs_endpoint.return_value = ("127.0.0.1", 4455)
        app.v_obs_password.get.return_value = ""
        mainGUI.App._test_obs_connection(app)
        app = MagicMock()
        app._running = False
        with patch.object(mainGUI, "messagebox") as box:
            box.askyesno.return_value = True
            mainGUI.App._prompt_update(app, "v9.9.9", ("http://x.invalid/a.exe", 1))
        with patch.object(AutoUpdate, "current_exe_path", return_value=Path("D:/x/ToNAutoBeginner.exe")):
            mainGUI.App._start_update_check(MagicMock())
            with patch.object(Migration, "installed_dir", return_value=None):
                mainGUI.App._start_migration(MagicMock(_running=False))
        self.assertEqual(self.spy.names, [
            "mainGUI._begin_capture_key", "mainGUI._arrange_windows", "mainGUI._test_obs_connection",
            "mainGUI._prompt_update", "mainGUI._start_update_check", "mainGUI._start_migration"])
        self.assertEqual(self.spy.started(), [True] * 6)

    def test_launch_vrchat(self):
        class Var:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value
        app = _with_cancel_key(type("FakeApp", (), {})())
        app._running = False
        app.v_join_world, app.v_desktop_mode, app.v_use_osc = Var(True), Var(True), Var(True)
        app.v_ton_access = Var(config.TON_INSTANCE_ACCESS_INVITE_PLUS)
        app.v_launch_options, app.v_launch_count = Var(""), Var(1)
        tab = type("FakeTab", (), {})()
        tab.idx, tab.v_profile = 0, Var(0)
        app.tabs = [tab]
        app.btn_launch = app.lbl_launch = MagicMock()
        app._launch_count_value = lambda n: 1
        app._save_launch_settings = lambda: None
        app.after = lambda _ms, fn=None: None
        app._log = lambda _m: None
        with patch.object(mainGUI, "messagebox"), \
             patch.object(VRChatLauncher, "find_vrchat_exe", return_value=Path("C:/VRChat/launch.exe")), \
             patch.object(VRChatLauncher, "latest_user_id", return_value="usr_me"), \
             patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time", return_value=[]), \
             patch.object(OSCQuery, "ensure"):
            mainGUI.App._launch_vrchat(app)
        self.assertOne("mainGUI._launch_vrchat")

    def test_the_entry_flow_and_the_plush_wait(self):
        """入室の仕事と、次の窓を待たせない Plush の待ち（窓ごとに別のスレッド）"""
        self.spy.run_now = True
        entry = MagicMock()
        entry.run.return_value = entry.press_begin.return_value = True
        app = MagicMock()
        app.v_ton_entry.get.return_value = True
        app.v_ton_begin.get.return_value = True
        app.v_ton_entry_plush.get.return_value = True
        app._launched_tab_indices = None
        tab = MagicMock()
        tab.idx = 0
        tab._get_selected_hwnd.return_value = 0x100
        tab.v_log.get.return_value = "C:/logs/output_log_0.txt"
        app.tabs = [tab]
        app._entry_stop = threading.Event()
        with patch.object(ToNEntry, "ToNEntry", return_value=entry), \
             patch.object(SaveLoad, "read_clipboard", return_value=""), \
             patch.object(SaveLoad, "log_size", return_value=7):
            mainGUI.App._run_ton_entry(app)
        self.assertEqual(self.spy.names, ["mainGUI._run_ton_entry", "mainGUI._plush_after_entry"])
        plush = self.spy.calls[1]
        self.assertEqual((plush[1], plush[2]), (app._plush_after_entry, (1, 0x100, "C:/logs/output_log_0.txt", 7)))

    def test_the_bug_report_send(self):
        app = mainGUI.App()
        self.addCleanup(app.destroy)
        app.withdraw()
        app._open_report()
        dialog = app._report_dialog
        dialog.text.insert("1.0", "止まった")
        with patch.object(BugReport, "webhook_url", return_value="https://x.example/h"):
            dialog._send()
        self.assertOne("ReportDialog._send")
        dialog.destroy()

    def test_connect_db_send(self):
        with patch.object(ConnectDB, "_configured", return_value=True):
            ConnectDB.register_round("Classic", [1, 2, 3], 1, None, quiet=True)
        self.assertOne("ConnectDB._send")

    def test_play_sound(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.mp3"
            path.write_bytes(b"x")
            PlaySound.play_sound(str(path))
        self.assertOne("PlaySound._play_sound")

    def test_give_back_the_front(self):
        loan = object()
        SharedState._start_give_back(loan)
        self.assertOne("SharedState._give_back", SharedState._give_back, (loan,))

    def test_statistics_window(self):
        window = MagicMock()
        window._rows_loading = False
        StatisticsGUI.StatisticsWindow._load_rows_async(window)
        self.assertOne("StatisticsGUI._load_rows_worker", window._load_rows_worker)
        self.spy.calls.clear(), self.spy.threads.clear()
        REAL_STATS_OFF_GUI(object(), lambda: 1, lambda r: None)
        self.assertOne("StatisticsGUI.run")

    def test_action_executor(self):
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=555, osc_port=9000),
                                           WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=4),
                                           lambda: True, lambda _m: None)
        ex._start_chase_locked("cw")
        self.assertEqual(self.spy.names, ["_run_chase"])
        self.assertEqual(self.spy.calls[0][2][0], "cw")
        self.assertIs(ex._chase_thread, self.spy.threads[0])
        with patch.object(ex, "_hands_free", return_value=False), \
             patch.object(ex, "_nothing_frozen_but_mine", return_value=False), \
             patch.object(SharedState, "equip_freeze_start"):
            ex._attend_to_item_loss()
        self.assertEqual(self.spy.calls[1][:1] + self.spy.calls[1][2:3], ("_watch_item_loss", (4,)))
        ex._restore_view_soon()
        self.assertEqual(self.spy.names[2], "_restore_view_loop")
        with patch.object(ex, "_begin_by_cursor", return_value=True), patch.object(ex, "can_operate", return_value=True):
            stop = ex._start_use_spam(9)
        self.assertEqual(self.spy.names[3], "_spam_use_right")
        self.assertEqual(self.spy.calls[3][2], (stop, 9))
        self.assertEqual(self.spy.names, ["_run_chase", "_watch_item_loss", "_restore_view_loop", "_spam_use_right"])
        self.assertEqual(self.spy.started(), [True] * 4)

    def test_motion_sampler(self):
        ActionExecutor.MotionSampler(MagicMock(), lambda _m: None).start()
        self.assertOne("MotionSampler._run")

    def test_recorder_and_window_volume(self):
        Recorder.Recorder()._post(("stop_all", None))
        self.assertEqual(self.spy.names, ["Recorder._run"])
        self.assertEqual(self.spy.calls[0][3], {"daemon": True, "thread_name": "OBSRecorder"})
        WindowVolume.VolumeController([], lambda _m: None, audio=MagicMock()).start()
        self.assertEqual(self.spy.names, ["Recorder._run", "WindowVolume._run"])

    def test_the_receiver(self):
        receiver = OSCReceiver.VelocityReceiver(0x10, port=9999)
        with patch.object(OSCQuery, "acquire", return_value=True):
            receiver.start()
        self.assertEqual(self.spy.names, ["OSCReceiver._wait_then_fall_back"])
        with patch.object(OSCReceiver, "socket", MagicMock()):
            receiver.fall_back()
        self.assertEqual(self.spy.names, ["OSCReceiver._wait_then_fall_back", "OSCReceiver._run"])
        receiver.stop()


class TestTheQueryService(_OSCQueryCase):
    def test_its_three_threads(self):
        spy = _Spy(self)
        service = OSCQuery.Service()
        self.addCleanup(service._close_sockets)
        self.assertTrue(service.start())
        self.assertEqual(spy.names, ["OSCQuery.serve_forever", "OSCQuery._run", "OSCQuery._advertise"])
        self.assertEqual(spy.started(), [True] * 3)


class TestMoreSitesAreNotMissed(unittest.TestCase):
    def test_no_thread_is_made_without_a_name_except_the_known_ones(self):
        """スレッドを直接立てている所は、更新後の掃除の 1 つだけ（これは CPU が小さい 1 回きりの仕事で、
        TaskStats の表に無い）"""
        direct = []
        for path in sorted(Path(mainGUI.__file__).parent.glob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "threading.Thread(" in line and not line.lstrip().startswith(("#", '"""')) \
                        and path.name not in ("TaskStats.py",):
                    direct.append((path.name, line.strip()))
        self.assertEqual(direct, [("mainGUI.py", "threading.Thread(target=AutoUpdate.cleanup_old_extract_dirs, "
                                                 "daemon=True).start()")])


if __name__ == "__main__":
    unittest.main()
