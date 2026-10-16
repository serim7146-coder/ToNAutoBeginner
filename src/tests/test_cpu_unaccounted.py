"""FS: [CPU] の行に「未計上」と「ほかのスレッド」を足す（10 分で 115 秒のうち、内訳に出るのは約 47 秒だった）。
未計上 ＝ 本体の CPU −（画面＋終わった＋生きている＋ほかのスレッド）。ほかのスレッド ＝ TaskStats が知らない
Python のスレッド（threading の名前）。本物のスレッドは立てるが、CPU の読み取り（ProcStats.thread_cpu）は偽物"""
from tests.support import *  # noqa: F401,F403
import ProcStats
import TaskStats


class _Case(unittest.TestCase):
    def setUp(self):
        p = patch.multiple(TaskStats, _finished={}, _live={}, _tk={}, _others={}, _last_wall=None,
                           _last_process_cpu=None, _last_tk_cpu=None, _last_peak_ws=None)
        p.start()
        self.addCleanup(p.stop)
        self.cpu = {}                   # native_id → CPU 秒（偽）
        p = patch.object(ProcStats, "thread_cpu", side_effect=lambda native_id: self.cpu.get(native_id))
        p.start()
        self.addCleanup(p.stop)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def thread(self, name):
        started = threading.Event()

        def body():
            started.set()
            self.release.wait(10)
        t = threading.Thread(target=body, name=name, daemon=True)
        t.start()
        self.assertTrue(started.wait(5))
        self.addCleanup(t.join, 5)
        return t


class TestUnaccounted(_Case):
    def test_it_is_the_process_minus_the_breakdown(self):
        live = self.thread("live-one")
        other = self.thread("zeroconf-ServiceBrowser")
        TaskStats._live[live.ident] = ["LogMonitor._run", live.native_id, 0.0]
        self.cpu = {live.native_id: 1.0, other.native_id: 2.0}
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        TaskStats._finished["do_after_round"] = [3, 10.0]
        self.cpu = {live.native_id: 6.0, other.native_id: 5.0}      # 生きている +5・ほかのスレッド +3
        snap = TaskStats.snapshot(20.0, 60.0, 1, wall_now=600.0)    # 画面 20
        self.assertEqual(snap["live"], [("LogMonitor._run", 1, 5.0)])
        self.assertEqual(snap["others"], [("zeroconf-ServiceBrowser", 1, 3.0)])
        self.assertAlmostEqual(snap["unaccounted"], 60.0 - (20.0 + 10.0 + 5.0 + 3.0))
        self.assertIn("・未計上 22.0秒・ほかのスレッド: zeroconf-ServiceBrowser 3.0秒", TaskStats.line(snap))

    def test_a_negative_result_is_zero(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        TaskStats._finished["a"] = [1, 50.0]
        snap = TaskStats.snapshot(10.0, 20.0, 1, wall_now=600.0)    # 内訳 60 > 本体 20
        self.assertEqual(snap["unaccounted"], 0.0)
        self.assertIn("・未計上 0.0秒", TaskStats.line(snap))

    def test_an_unknown_process_or_screen_cpu_is_a_question_mark(self):
        TaskStats.snapshot(1.0, None, 1, wall_now=0.0)
        snap = TaskStats.snapshot(2.0, 5.0, 1, wall_now=600.0)
        self.assertIsNone(snap["unaccounted"])
        self.assertIn("・未計上 ?", TaskStats.line(snap))
        snap = TaskStats.snapshot(None, 9.0, 1, wall_now=1200.0)
        self.assertIsNone(snap["unaccounted"], "画面が取れなくても出さない")
        self.assertTrue(TaskStats.line(snap).endswith("・未計上 ?"))

    def test_the_first_one_has_none(self):
        snap = TaskStats.snapshot(1.0, 10.0, 1, wall_now=0.0)
        self.assertIsNone(snap["unaccounted"])
        self.assertEqual(snap["others"], [])
        self.assertEqual(TaskStats.line(snap), "[CPU] 計測開始")

    def test_the_old_shape_of_dict_still_writes(self):
        snap = {"first": False, "elapsed": 600.0, "process_delta": 1.0, "tk_delta": 0.5, "finished": [], "live": []}
        self.assertTrue(TaskStats.line(snap).endswith("・生きている: なし・未計上 ?"))


class TestOtherThreads(_Case):
    def _two(self, first, second):
        self.cpu = first
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        self.cpu = second
        return TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)

    def test_an_unknown_thread_is_shown_by_its_name_with_the_difference(self):
        t = self.thread("zeroconf-ServiceBrowser")
        snap = self._two({t.native_id: 4.0}, {t.native_id: 44.1})
        self.assertEqual(len(snap["others"]), 1)
        name, n, cpu = snap["others"][0]
        self.assertEqual((name, n), ("zeroconf-ServiceBrowser", 1))
        self.assertAlmostEqual(cpu, 40.1)
        self.assertIn("・ほかのスレッド: zeroconf-ServiceBrowser 40.1秒", TaskStats.line(snap))

    def test_threads_of_the_same_name_are_bundled_and_ordered_by_cpu(self):
        a1, a2, b = self.thread("worker-a"), self.thread("worker-a"), self.thread("worker-b")
        snap = self._two({a1.native_id: 0.0, a2.native_id: 0.0, b.native_id: 0.0},
                         {a1.native_id: 1.0, a2.native_id: 1.0, b.native_id: 5.0})
        self.assertEqual(snap["others"], [("worker-b", 1, 5.0), ("worker-a", 2, 2.0)])
        self.assertIn("・ほかのスレッド: worker-b 5.0秒 / worker-a ×2 2.0秒", TaskStats.line(snap))

    def test_a_thread_taskstats_knows_is_not_shown_twice(self):
        t = self.thread("LogMonitor-thread")
        TaskStats._live[t.ident] = ["LogMonitor._run", t.native_id, 0.0]
        snap = self._two({t.native_id: 1.0}, {t.native_id: 3.0})
        self.assertEqual(snap["live"], [("LogMonitor._run", 1, 2.0)])
        self.assertEqual(snap["others"], [])
        self.assertNotIn("ほかのスレッド", TaskStats.line(snap))
        self.assertNotIn(t.ident, TaskStats._others)

    def test_the_main_thread_is_not_shown(self):
        """メインスレッド（Tk。main.py の app.mainloop()）は「画面」として別に数える"""
        main = threading.main_thread()
        self.cpu = {main.native_id: 1.0}
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        self.cpu = {main.native_id: 90.0}
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["others"], [])
        self.assertNotIn(main.ident, TaskStats._others)

    def test_a_thread_whose_cpu_cannot_be_read_is_skipped(self):
        t = self.thread("gone-soon")
        snap = self._two({}, {})
        self.assertEqual(snap["others"], [])
        self.assertNotIn(t.ident, TaskStats._others)

    def test_a_finished_thread_is_dropped_from_the_memory(self):
        t = self.thread("short-lived")
        self.cpu = {t.native_id: 1.0}
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        self.assertIn(t.ident, TaskStats._others)
        self.release.set()
        t.join(5)
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["others"], [])
        self.assertNotIn(t.ident, TaskStats._others)

    def test_a_thread_that_appears_later_starts_from_its_own_baseline(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        t = self.thread("late")
        self.cpu = {t.native_id: 7.0}                   # 立ち上がる前の CPU は無い（読んだ値が土台）
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["others"], [("late", 1, 0.0)])
        self.cpu = {t.native_id: 8.5}
        self.assertEqual(TaskStats.snapshot(0.0, 0.0, 1, wall_now=1200.0)["others"], [("late", 1, 1.5)])


class TestTheLine(unittest.TestCase):
    SNAP = {"first": False, "elapsed": 600.0, "process_delta": 115.1, "tk_delta": 8.8,
            "finished": [("do_after_round", 72, 18.4)], "live": [("LogMonitor._run", 1, 1.4)],
            "tk_top": [("_log", 1200, 3.1)], "unaccounted": 68.3, "others": [], "peak_ws": 0, "peak_delta": 0}
    HEAD = ("[CPU] 10分で 115.1秒（19.2%）・画面 8.8秒（上位: _log ×1200 3.1秒）・終わった: do_after_round ×72 18.4秒"
            "・生きている: LogMonitor._run 1.4秒")

    def test_the_new_items_are_added_at_the_end(self):
        self.assertEqual(TaskStats.line(self.SNAP), self.HEAD + "・未計上 68.3秒")
        snap = dict(self.SNAP, others=[("zeroconf-ServiceBrowser", 1, 40.1), ("asyncio_0", 2, 3.0)])
        self.assertEqual(TaskStats.line(snap), self.HEAD + "・未計上 68.3秒"
                         "・ほかのスレッド: zeroconf-ServiceBrowser 40.1秒 / asyncio_0 ×2 3.0秒")

    def test_only_the_top_few_with_a_mark_for_the_rest(self):
        rows = [(f"t{i}", 1, 10.0 - i) for i in range(4)]
        with patch.object(config, "MEMORY_LOG_TOP", 2):
            text = TaskStats.line(dict(self.SNAP, others=rows))
        self.assertTrue(text.endswith("・ほかのスレッド: t0 10.0秒 / t1 9.0秒 …"), text)

    def test_an_unknown_value_is_a_question_mark(self):
        self.assertTrue(TaskStats.line(dict(self.SNAP, unaccounted=None)).endswith("・生きている: LogMonitor._run 1.4秒・未計上 ?"))
