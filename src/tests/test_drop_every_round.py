"""どのラウンドの後でもアイテムを落とす（DW）。続行フリーズを張ったラウンドは今どおり続行の終わりで1回。
本物の OSC は送らない（OSC は偽物。窓・時計も偽物）"""
from tests.support import *  # noqa: F401,F403
import inspect

ROUND_OVER = "2026.10.12 10:00:00 Debug      -  RoundOver"


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

    def _executor(self, itype=config.INSTANCE_PRIVATE, osc=True, running=True):
        cfg = WindowConfig(hwnd=0x30, osc_port=9000 if osc else 0)
        st = WindowState(instance_type=itype, window_idx=3)
        self.logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: running, self.logs.append)
        self.osc = _Osc()
        ex._osc = self.osc if osc else None
        return ex

    def _monitor(self, auto_begin=False, itype=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(
            WindowConfig(hwnd=0x30, osc_port=9000, auto_begin=auto_begin), {},
            lambda _m: None, window_idx=3)
        monitor._running = True
        monitor.st.instance_type = itype
        monitor.st.continue_hook = monitor._on_continue_freeze
        monitor._start_daemon = lambda target, *a: target(*a)    # その場で走らせる
        self.osc = _Osc()
        monitor._action._osc = self.osc
        monitor._action._is_running = lambda: True
        monitor.logs = []
        monitor._log = monitor.logs.append
        monitor._action._log = monitor.logs.append
        return monitor

    def _drops(self):
        return [a for a, _s in self.osc.sent if a == "/input/DropRight"]


class TestDropAfterRound(_Case):
    """ActionExecutor.drop_after_round（RoundOver で決めたラウンドだけ、1回）"""

    def _pending(self, ex, seq=4):
        ex._st.round_seq = seq
        ex._st.drop_after_round_seq = seq

    def test_drops_once(self):
        ex = self._executor()
        self._pending(ex)
        ex.drop_after_round()
        self.assertEqual(self.osc.sent, [("/input/DropRight", config.CONTINUE_DROP_PRESS_SEC)])
        self.assertEqual(self.logs, ["ラウンドが終わったので、アイテムを落としました"])
        ex.drop_after_round()                                   # もう決めていない
        self._pending(ex)                                       # 同じラウンドでもう一度決めても
        ex.drop_after_round()
        self.assertEqual(len(self._drops()), 1, "1ラウンド1回")

    def test_not_decided_does_nothing(self):
        ex = self._executor()
        ex.drop_after_round()
        self.assertEqual(self.osc.sent, [])

    def test_not_private_no_osc_or_off(self):
        for kwargs, setting, reason in (({"itype": config.INSTANCE_PUBLIC}, True, "private でない"),
                                        ({"osc": False}, True, "OSC が使えない窓"),
                                        ({}, False, None)):
            SharedState.set_continue_drop_item(setting)
            self.written.clear()
            ex = self._executor(**kwargs)
            self._pending(ex)
            ex.drop_after_round()
            self.assertEqual(self.osc.sent, [], kwargs)
            self.assertEqual(self.logs, [], kwargs)
            if reason:
                self.assertTrue(any(reason in m for m in self.written), self.written)

    def test_the_next_round_already_started(self):
        ex = self._executor()
        self._pending(ex)
        ex._st.in_round = True
        ex.drop_after_round()
        ex._st.in_round = False
        ex._st.drop_after_round_seq = 4
        ex._st.round_seq = 5
        ex.drop_after_round()
        self.assertEqual(self.osc.sent, [])

    def test_waits_for_the_run_respawn(self):
        """Run のリスポーン（後ろへ・正面へ）が走っている間は送らない。終わってから"""
        ex = self._executor()
        self._pending(ex)
        ex._run_respawn_idle.clear()
        t = threading.Thread(target=ex.drop_after_round, daemon=True)
        t.start()
        t.join(0.35)
        self.assertTrue(t.is_alive())
        self.assertEqual(self.osc.sent, [], "リスポーンの移動と同時に送らない")
        ex._run_respawn_idle.set()
        t.join(2)
        self.assertEqual(len(self._drops()), 1)

    def test_a_stop_while_waiting_does_not_drop(self):
        running = [True]
        ex = self._executor()
        ex._is_running = lambda: running[0]
        self._pending(ex)
        ex._run_respawn_idle.clear()
        t = threading.Thread(target=ex.drop_after_round, daemon=True)
        t.start()
        running[0] = False
        t.join(2)
        self.assertEqual(self.osc.sent, [])

    def test_before_the_begin_move_and_the_fetch(self):
        """自動 Begin の窓: Run のリスポーンを待つ → 落とす → Begin 前の移動（アイテム自動取得は
        その後の同じ流れ）。同じスレッドで順に行うので同時にならない"""
        ex = self._executor()
        order = []
        ex._wait_run_respawn = lambda: order.append("wait") or True
        ex.drop_after_round = lambda: order.append("drop")
        ex._begin_move = lambda *a, **k: order.append("move")
        ex._begin_precheck = lambda **k: True
        ex._start_use_spam = lambda seq: order.append("spam") or None
        ex.item_fetch_target = lambda: order.append("fetch?") or None
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ex, "_is_running", side_effect=[True] + [False] * 50):
            try:
                ex.do_after_round()
            except Exception:
                pass
        self.assertEqual(order[:3], ["wait", "drop", "move"], order)

    def test_the_continue_end_uses_the_same_press(self):
        ex = self._executor()
        ex._continue_rect = None
        ex.after_continue_round()
        self.assertEqual(self.osc.sent, [("/input/DropRight", config.CONTINUE_DROP_PRESS_SEC)])
        self.assertEqual(self.logs, ["続行ラウンドが終わったので、アイテムを落としました"])


class TestRoundOverDecides(_Case):
    """LogMonitor: RoundOver で落とすか決める。続行フリーズを張ったラウンドは続行の終わりで1回"""

    def test_a_normal_round_drops_at_round_over(self):
        monitor = self._monitor()
        monitor.st.in_round = True
        monitor._process(ROUND_OVER)
        self.assertEqual(len(self._drops()), 1)
        self.assertIn("ラウンドが終わったので、アイテムを落としました", monitor.logs)

    def test_a_continue_round_drops_once_at_its_end(self):
        monitor = self._monitor()
        st = monitor.st
        st.in_round = True
        st.is_continue_round = True
        SharedState.continue_round_start(st)             # 続行フリーズを張った
        run = monitor._start_daemon                      # 猶予の後に外す予約は、ここでは走らせない
        monitor._start_daemon = lambda target, *a: (
            None if target == monitor._release_continue_freeze_after_delay else run(target, *a))
        monitor._process(ROUND_OVER)
        self.assertEqual(self._drops(), [], "RoundOver では落とさない")
        self.assertEqual(st.drop_after_round_seq, -1)
        SharedState.continue_round_end(st)               # 続行の終わり（死亡・生存から猶予の後）
        self.assertEqual(len(self._drops()), 1)
        self.assertIn("続行ラウンドが終わったので、アイテムを落としました", monitor.logs)

    def test_the_freeze_released_before_round_over_still_drops_once(self):
        """死亡から猶予の後に外れて、RoundOver がその後に来ても二重にしない"""
        monitor = self._monitor()
        st = monitor.st
        st.in_round = True
        SharedState.continue_round_start(st)
        SharedState.continue_round_end(st)
        monitor._process(ROUND_OVER)
        self.assertEqual(len(self._drops()), 1)

    def test_a_dtm_waldo_continue_without_the_freeze_drops_at_round_over(self):
        """DTM/Waldo の続行（他窓フリーズを張らない）は続行の終わりが来ないので RoundOver で"""
        monitor = self._monitor()
        monitor.st.in_round = True
        monitor.st.is_continue_round = True
        monitor._process(ROUND_OVER)
        self.assertEqual(len(self._drops()), 1)

    def test_the_next_round_is_decided_again(self):
        monitor = self._monitor()
        st = monitor.st
        SharedState.continue_round_start(st)
        SharedState.continue_round_end(st)
        st.round_seq += 1                                # 次のラウンド（普通）
        monitor._process(ROUND_OVER)
        self.assertEqual(len(self._drops()), 2)

    def test_auto_begin_drops_inside_the_after_round_flow(self):
        monitor = self._monitor(auto_begin=True)
        calls = []
        monitor._action.do_after_round = lambda: calls.append("after_round")
        monitor._action.drop_after_round = lambda: calls.append("drop_alone")
        monitor._process(ROUND_OVER)
        self.assertEqual(calls, ["after_round"], "Begin の流れの頭で落とす（別に走らせない）")
        self.assertEqual(monitor.st.drop_after_round_seq, monitor.st.round_seq)
        src = inspect.getsource(ActionExecutor.ActionExecutor.do_after_round)
        self.assertLess(src.index("self._wait_run_respawn()"), src.index("self.drop_after_round()"))
        self.assertLess(src.index("self.drop_after_round()"), src.index("self._begin_move()"))

    def test_public_or_off_does_not_drop(self):
        monitor = self._monitor(itype=config.INSTANCE_PUBLIC)
        monitor._process(ROUND_OVER)
        SharedState.set_continue_drop_item(False)
        monitor2 = self._monitor()
        monitor2._process(ROUND_OVER)
        self.assertEqual(self._drops(), [])


class TestTheSetting(_Case):
    def test_the_saved_name_and_default_are_the_same(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_CONTINUE_DROP_ITEM = _Setting(True, bool)", src)
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('"continue_drop_item":      SharedState.get_continue_drop_item(),', gui)
