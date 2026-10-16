"""FC（依頼者 2026-10-15「メモリが 50MB → 200MB」）: 別のスレッドから Tk を呼ばない。Tk のスレッドでないスレッドが
_tkinter を呼ぶと、スレッド 1 本ごとに Tcl の Event とスレッドごとのデータが残っていた。別のスレッドは受付箱（UiInbox）に
入れるだけ、Tk のスレッドが 50ms ごとに取り出して実行する。本物の入力・窓の操作は使わない（App は隠したまま）"""
from tests.support import *  # noqa: F401,F403
import inspect
import re
import types
import tkinter
import UiInbox
import ProcStats
import TaskStats
import StatisticsGUI
from tests.support import REAL_STATS_OFF_GUI


class _Watch:
    """tkinter.Misc.after / after_idle を見張る（呼んだスレッドが Tk のスレッドか）"""

    def __init__(self, test):
        self.calls = []
        real_after, real_idle = tkinter.Misc.after, tkinter.Misc.after_idle
        watch = self

        def after(widget, ms, func=None, *args):
            watch.calls.append(threading.current_thread() is threading.main_thread())
            return real_after(widget, ms, func, *args)

        def after_idle(widget, func, *args):
            watch.calls.append(threading.current_thread() is threading.main_thread())
            return real_idle(widget, func, *args)
        for name, fake in (("after", after), ("after_idle", after_idle)):
            p = patch.object(tkinter.Misc, name, fake)
            p.start()
            test.addCleanup(p.stop)

    def off_thread(self):
        return [c for c in self.calls if not c]


def _in_thread(fn, *args):
    t = threading.Thread(target=fn, args=args)
    t.start()
    t.join(5)


class TestTheInbox(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.inbox = UiInbox.UiInbox(self.root)

    def test_in_the_order_put(self):
        done = []
        for i in range(5):
            _in_thread(self.inbox.put, done.append, i)
        self.assertEqual(done, [], "入れただけでは動かない")
        self.inbox.drain()
        self.assertEqual(done, [0, 1, 2, 3, 4])

    def test_an_error_does_not_stop_it(self):
        done = []

        def boom():
            raise ValueError("x")
        self.inbox.put(boom)
        self.inbox.put(done.append, "next")
        with patch.object(DebugLog, "exception") as exc:
            self.inbox.drain()
        self.assertEqual(done, ["next"])
        exc.assert_called_once()

    def test_closing_runs_the_rest_then_ignores(self):
        done = []
        self.inbox.start()
        self.inbox.put(done.append, "last log")
        self.inbox.close()
        self.assertEqual(done, ["last log"], "閉じる前に残りを実行")
        self.inbox.put(done.append, "after close")
        self.inbox.drain()
        self.assertEqual(done, ["last log"], "閉じた後は捨てる")
        self.assertIsNone(self.inbox._after_id, "予約も取り消す")

    def test_it_runs_every_50_ms_on_the_tk_thread(self):
        self.assertEqual(UiInbox.INTERVAL_MS, 50)
        where = []
        self.inbox.start()
        _in_thread(self.inbox.put, lambda: where.append(threading.current_thread() is threading.main_thread()))
        deadline = time.time() + 2
        while not where and time.time() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(where, [True])
        self.assertIsNotNone(self.inbox._after_id, "次の回も予約している")

    def test_what_is_put_while_running_waits_for_the_next_round(self):
        done = []
        self.inbox.put(lambda: (done.append(1), self.inbox.put(done.append, 2)))
        self.inbox.drain()
        self.assertEqual(done, [1])
        self.inbox.drain()
        self.assertEqual(done, [1, 2])


class TestTheApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.watch = _Watch(self)

    def test_log_from_another_thread(self):
        _in_thread(self.app._log, "別のスレッドから FC")
        self.assertEqual(self.watch.off_thread(), [], "別のスレッドから Tk を呼ばない")
        self.app._inbox.drain()
        self.assertIn("別のスレッドから FC", self.app.log_text.get("1.0", "end"))

    def test_after_from_worker_and_hook_and_the_monitor(self):
        where = []
        mark = lambda: where.append(threading.current_thread() is threading.main_thread())  # noqa: E731
        _in_thread(self.app._after_from_worker, mark)
        _in_thread(self.app._after_from_hook, mark)
        with patch.object(self.app, "_do_clear_tab_round_settings", side_effect=lambda i: mark()):
            _in_thread(self.app._clear_tab_round_settings, 1)
            self.assertEqual(self.watch.off_thread(), [])
            self.app._inbox.drain()
        self.assertEqual(where, [True, True, True], "Tk のスレッドで動く")

    def test_no_after_0_is_left(self):
        for module in (mainGUI, StatisticsGUI):
            src = inspect.getsource(module)
            self.assertEqual(re.findall(r"after\(0,", src), [], f"{module.__name__}: UiInbox.call_soon を通す")
        self.assertIn("UiInbox.call_soon(self.app, lambda: self._done(ok, message))",
                      inspect.getsource(mainGUI.ReportDialog))

    def test_on_the_tk_thread_it_is_after_0_as_before(self):
        """Tk のスレッドから呼んだら今までどおり after(0)（受付箱を通さない）"""
        with patch.object(self.app, "after") as after:
            UiInbox.call_soon(self.app, print, "x")
        after.assert_called_once_with(0, print, "x")
        self.assertTrue(all(self.watch.calls))


class TestClosingTheApp(unittest.TestCase):
    def test_the_last_logs_are_shown_before_it_closes(self):
        app = mainGUI.App()
        app.withdraw()
        shown = []
        real_destroy = app.destroy

        def destroy():
            shown.append(app.log_text.get("1.0", "end"))
            real_destroy()
        with patch.object(app, "_save_settings_now"), \
             patch.object(app, "_stop", side_effect=lambda: _in_thread(app._log, "[停止] 最後のログ")), \
             patch.object(app, "destroy", side_effect=destroy):
            app._on_close()
        self.assertIn("[停止] 最後のログ", shown[0])


class TestTheStatisticsWindow(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.watch = _Watch(self)
        self.win = type("W", (), {})()
        self.win._inbox = UiInbox.UiInbox(self.root)
        self.win.after = self.root.after

    def test_the_heavy_work_comes_back_through_the_inbox(self):
        got = []
        real_thread = threading.Thread

        class JoinNow:
            def __init__(self, target, daemon=None):
                self.t = real_thread(target=target)

            def start(self):
                self.t.start()
                self.t.join(5)
        with patch.object(StatisticsGUI.threading, "Thread", JoinNow):
            REAL_STATS_OFF_GUI(self.win, lambda: 42, lambda r: got.append(
                (r, threading.current_thread() is threading.main_thread())))
        self.assertEqual(self.watch.off_thread(), [])
        self.win._inbox.drain()
        self.assertEqual(got, [(42, True)])

    def test_the_rows_loaded(self):
        got = []
        self.win.store = type("S", (), {"sync": lambda _s, fetch: True})()
        self.win._on_rows_loaded = lambda ok: got.append(ok)
        _in_thread(StatisticsGUI.StatisticsWindow._load_rows_worker, self.win)
        self.assertEqual(self.watch.off_thread(), [])
        self.win._inbox.drain()
        self.assertEqual(got, [True])


class TestTheRealStatisticsWindow(unittest.TestCase):
    def test_it_has_its_own_inbox_and_closes_it(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = RoundStore.RoundStore(Path(tmp.name) / "rounds.sqlite")

        class RunNow:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()
        with patch.object(ConnectDB, "fetch_rounds", side_effect=lambda s, m: []), \
             patch.object(StatisticsGUI.threading, "Thread", RunNow):
            window = StatisticsGUI.StatisticsWindow(root, store=store)
        inbox = window._inbox
        self.assertIsInstance(inbox, UiInbox.UiInbox)
        self.assertIs(inbox.owner, threading.current_thread())
        window.destroy()
        self.assertTrue(inbox._closed, "閉じたら受付箱も止める")


class TestTheMemoryLine(unittest.TestCase):
    def test_the_line(self):
        line = ProcStats.line()
        self.assertRegex(line, r"^\[メモリ\] WS \d+\.\dMB・Private \d+\.\dMB・ハンドル \d+・スレッド \d+・最大 WS \d+MB（初回）$")

    def test_every_10_minutes_and_at_start(self):
        self.assertEqual(config.MEMORY_LOG_SEC, 600.0)
        fake = type("A", (), {})()
        fake.after = MagicMock()
        fake._log_memory = "次の予約"
        with patch.object(DebugLog, "write") as write,              patch.multiple(TaskStats, _finished={}, _live={}, _last_wall=None, _last_process_cpu=None,
                            _last_tk_cpu=None, _last_peak_ws=None):
            mainGUI.App._log_memory(fake)
        self.assertTrue(write.call_args_list[0].args[0].startswith("[メモリ] WS "))     # 2 行目は [CPU]（test_task_stats）
        fake.after.assert_called_once_with(600_000, fake._log_memory)
        self.assertIn("self._log_memory()", inspect.getsource(mainGUI.App.__init__), "起動時にも 1 行")


if __name__ == "__main__":
    unittest.main()
