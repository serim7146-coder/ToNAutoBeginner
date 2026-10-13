"""アイテムを落とすのは、死んだ・生き残ったとき（EU。依頼者 2026-10-13「デスした時か勝った時」。前は RoundOver
の後（DW）と続行の終わり）。ラウンドごとに 1 回。続行ラウンドも同じで、続行の終わりでは落とさない（窓は戻す）。
本物の OSC は送らない（OSC は偽物。窓・時計も偽物）"""
from tests.support import *  # noqa: F401,F403
import inspect

T = "2026.10.13 10:00:00 Debug      -  "
DIED = T + "You died."
LIVED = T + "Lived in round."
ROUND_OVER = T + "RoundOver"


class _Osc:
    def __init__(self):
        self.sent = []

    def press(self, address, sec, stop=None):
        self.sent.append((address, sec))
        return True


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_continue_drop_item(True)
        self.addCleanup(SharedState.set_continue_drop_item, False)
        SharedState.set_continue_restore_window(False)
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self, auto_begin=False, itype=config.INSTANCE_PRIVATE, osc=True):
        monitor = LogMonitor.LogMonitor(
            WindowConfig(hwnd=0x30, osc_port=9000, auto_begin=auto_begin), {},
            lambda _m: None, window_idx=3)
        monitor._running = True
        monitor.st.instance_type = itype
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.continue_hook = monitor._on_continue_freeze
        monitor._start_daemon = lambda target, *a: target(*a)    # その場で走らせる
        self.osc = _Osc()
        monitor._action._osc = self.osc if osc else None
        monitor._action._is_running = lambda: True
        monitor.logs = []
        monitor._log = monitor.logs.append
        monitor._action._log = monitor.logs.append
        return monitor

    def _drops(self):
        return [a for a, _s in self.osc.sent if a == "/input/DropRight"]


class TestDropOnDeathOrLife(_Case):
    def test_dying_drops_once(self):
        monitor = self._monitor()
        monitor._process(DIED)
        self.assertEqual(self.osc.sent, [("/input/DropRight", config.CONTINUE_DROP_PRESS_SEC)])
        self.assertIn("死んだので、アイテムを落としました", monitor.logs)
        self.assertTrue(any("[操作] [窓3] 死んだ: /input/DropRight を押して離した" in m for m in self.written))

    def test_living_drops_once(self):
        monitor = self._monitor()
        monitor._process(LIVED)
        self.assertEqual(len(self._drops()), 1)
        self.assertIn("生き残ったので、アイテムを落としました", monitor.logs)

    def test_only_once_a_round(self):
        monitor = self._monitor()
        monitor._process(DIED)
        monitor._process(LIVED)
        monitor._process(DIED)
        monitor._process(ROUND_OVER)
        self.assertEqual(len(self._drops()), 1)

    def test_the_next_round_drops_again(self):
        monitor = self._monitor()
        monitor._process(DIED)
        monitor.st.round_seq += 1
        monitor._process(LIVED)
        self.assertEqual(len(self._drops()), 2)

    def test_round_over_alone_does_not_drop(self):
        """死んだ・生き残った行が来ないラウンド（Run のリスポーンなど）は落とさない"""
        for auto_begin in (False, True):
            monitor = self._monitor(auto_begin=auto_begin)
            monitor.st.round_type = "Run"
            monitor._action.do_after_round = lambda: None
            monitor._process(ROUND_OVER)
            self.assertEqual(self._drops(), [], auto_begin)

    def test_a_continue_round_drops_on_death_and_not_at_its_end(self):
        monitor = self._monitor()
        st = monitor.st
        st.is_continue_round = True
        SharedState.continue_round_start(st)             # 続行フリーズを張った
        run = monitor._start_daemon                      # 猶予の後に外す予約は、ここでは走らせない
        monitor._start_daemon = lambda target, *a: (
            None if target == monitor._release_continue_freeze_after_delay else run(target, *a))
        monitor._process(DIED)
        self.assertEqual(len(self._drops()), 1, "死んだときに落とす")
        monitor._process(ROUND_OVER)
        SharedState.continue_round_end(st)               # 続行の終わり（死亡から猶予の後）
        self.assertEqual(len(self._drops()), 1, "続行の終わりでは落とさない")
        self.assertNotIn("続行ラウンドが終わったので、アイテムを落としました", monitor.logs)

    def test_the_continue_end_still_restores_the_window(self):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=3)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: True, lambda _m: None)
        ex._osc = _Osc()
        ex._continue_rect = (10, 20, 30, 40)
        SharedState.set_continue_restore_window(True)
        self.addCleanup(SharedState.set_continue_restore_window, False)
        with patch.object(ex, "_restore_continue_window") as restore:
            ex.after_continue_round()
        restore.assert_called_once_with((10, 20, 30, 40))
        self.assertEqual(ex._osc.sent, [], "落とさない")

    def test_off_not_private_or_no_osc_does_not_drop(self):
        for kwargs, setting, reason in (({"itype": config.INSTANCE_PUBLIC}, True, "private でない"),
                                        ({"osc": False}, True, "OSC が使えない窓"),
                                        ({}, False, None)):
            SharedState.set_continue_drop_item(setting)
            self.written.clear()
            monitor = self._monitor(**kwargs)
            monitor._process(DIED)
            self.assertEqual(self.osc.sent, [], kwargs)
            self.assertFalse(any("アイテムを落としました" in m for m in monitor.logs), kwargs)
            if reason:
                self.assertTrue(any(reason in m for m in self.written), self.written)

    def test_a_late_drop_after_the_next_round_started_is_skipped(self):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=3, round_seq=5)
        logs = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: True, logs.append)
        ex._osc = _Osc()
        ex.drop_at_round_end(4, "死んだ")
        self.assertEqual(ex._osc.sent, [])
        self.assertTrue(any("次のラウンドが始まった" in m for m in self.written))

    def test_turned_off_before_the_drop_runs(self):
        """死んだ行を受けてから落とすまでの間に設定を切ったら落とさない"""
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=3, round_seq=4)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: True, lambda _m: None)
        ex._osc = _Osc()
        SharedState.set_continue_drop_item(False)
        ex.drop_at_round_end(4, "死んだ")
        self.assertEqual(ex._osc.sent, [])

    def test_a_stop_does_not_drop(self):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=3, round_seq=4)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: False, lambda _m: None)
        ex._osc = _Osc()
        ex.drop_at_round_end(4, "死んだ")
        self.assertEqual(ex._osc.sent, [])


class TestNoClash(_Case):
    def test_dropping_is_not_an_item_loss(self):
        """落としてもログに出来事は出ない（パーサが見るのは Equipping だけ）。ロストと数えない"""
        monitor = self._monitor()
        st = monitor.st
        st.item_id = st.item_id_at_round_start = 29
        st.held_item_id = 29
        monitor._process(LIVED)
        self.assertEqual(len(self._drops()), 1)
        self.assertEqual((st.item_id, st.held_item_id, st.waiting_for_equip), (29, 29, False))

    def test_a_suicide_death_still_counts_and_drops(self):
        """自爆の長押しの後の死亡: 「自爆成功」も、落とすのも、両方ある"""
        monitor = self._monitor()
        monitor.st._skip_time = time.time()
        monitor._process(DIED)
        self.assertIn("✅ 自爆成功", monitor.logs)
        self.assertEqual(len(self._drops()), 1)
        self.assertTrue(monitor.st.died_this_round)

    def test_the_old_drops_are_gone(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor)
        self.assertNotIn("def drop_after_round", src)
        self.assertNotIn("_drop_item_after_continue", src)
        self.assertNotIn("drop_after_round", inspect.getsource(LogMonitor.LogMonitor._on_round_over))
        self.assertNotIn("drop", inspect.getsource(ActionExecutor.ActionExecutor.do_after_round).split(
            "self._wait_run_respawn()")[1].split("self._begin_move()")[0].replace("drop_at_round_end", ""))


class TestTheSetting(_Case):
    def test_the_saved_name_and_default_are_the_same(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_CONTINUE_DROP_ITEM = _Setting(True, bool)", src)
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('"continue_drop_item":      SharedState.get_continue_drop_item(),', gui)
