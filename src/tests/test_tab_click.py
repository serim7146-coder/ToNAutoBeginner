"""ツールのクリックは Tab を押したまま押す（DG。押す → 待つ → mouseDown → mouseUp → 離す）"""
from tests.support import *  # noqa: F401,F403


TAB = config.CLICK_TAB_KEY


class _Inputs:
    """keyboard・pydirectinput・time.sleep を1本の記録にする（本物は送らない）"""

    def __init__(self, test, front=None):
        self.calls = []
        self.front = front
        rec = self

        class Keyboard:
            def press(self, key):
                rec.calls.append(("press", key))

            def release(self, key):
                rec.calls.append(("release", key))

        class Mouse:
            def mouseDown(self, _pause=True):
                rec.calls.append(("down", _pause))
                if rec.on_down:
                    rec.on_down()

            def mouseUp(self, _pause=True):
                rec.calls.append(("up", _pause))
                if rec.on_up:
                    rec.on_up()

        def sleep(sec):
            self.calls.append(("sleep", sec))
            if self.on_sleep:
                self.on_sleep()
        self.on_down = self.on_up = self.on_sleep = None
        for p in (patch.object(WindowOperator, "keyboard", Keyboard()),
                  patch.object(WindowOperator, "pydirectinput", Mouse()),
                  patch.object(WindowOperator, "time", _module_time(sleep))):
            p.start()
            test.addCleanup(p.stop)

    def keys(self):
        return [c for c in self.calls if c[0] in ("press", "release", "down", "up")]


def _one_click(pause=True, hold=0.1):
    return [("press", TAB), ("sleep", config.CLICK_TAB_LEAD_SEC), ("down", pause),
            ("sleep", hold), ("up", pause), ("release", TAB)]


class TestClickWithTab(unittest.TestCase):
    def setUp(self):
        self.inputs = _Inputs(self)

    def test_the_constants(self):
        self.assertEqual(TAB, "tab")
        self.assertEqual(config.CLICK_TAB_LEAD_SEC, 0.03)

    def test_click_presses_tab_waits_clicks_then_lets_tab_go(self):
        WindowOperator.click()
        self.assertEqual(self.inputs.calls, _one_click() + [("sleep", config.OPERATOR_WAIT_SEC)])

    def test_tab_is_let_go_when_the_mouse_fails(self):
        for where in ("down", "up"):
            with self.subTest(where=where):
                self.inputs.calls.clear()

                def boom():
                    raise OSError("x")
                self.inputs.on_down = boom if where == "down" else None
                self.inputs.on_up = boom if where == "up" else None
                with self.assertRaises(OSError):
                    WindowOperator.click_with_tab(0.1)
                self.assertEqual(self.inputs.calls[-1], ("release", TAB))
                self.assertEqual(self.inputs.calls.count(("release", TAB)), 1)

    def test_tab_is_let_go_when_the_wait_is_interrupted(self):
        def boom():
            raise KeyboardInterrupt
        self.inputs.on_sleep = boom
        with self.assertRaises(KeyboardInterrupt):
            WindowOperator.click_with_tab(0.1)
        self.assertEqual(self.inputs.keys(), [("press", TAB), ("release", TAB)])

    def test_the_lead_can_be_zero(self):
        with patch.object(config, "CLICK_TAB_LEAD_SEC", 0):
            WindowOperator.click_with_tab(0.1)
        self.assertEqual(self.inputs.calls, [("press", TAB), ("down", True), ("sleep", 0.1),
                                             ("up", True), ("release", TAB)])

    def test_no_click_when_the_front_changed_during_the_wait(self):
        self.assertFalse(WindowOperator.click_with_tab(0.1, still_front=lambda: False))
        self.assertEqual(self.inputs.keys(), [("press", TAB), ("release", TAB)])

    def test_the_front_is_checked_after_the_wait(self):
        seen = []
        WindowOperator.click_with_tab(0.1, still_front=lambda: seen.append(list(self.inputs.calls)) or True)
        self.assertEqual(seen, [[("press", TAB), ("sleep", config.CLICK_TAB_LEAD_SEC)]])

    def test_the_debug_log_says_tab(self):
        with patch.object(DebugLog, "write") as write:
            WindowOperator.click()
        self.assertIn("[操作] クリック（Tab の後）", [c.args[0] for c in write.call_args_list])


class TestBeginAndEntryClicks(unittest.TestCase):
    def setUp(self):
        self.inputs = _Inputs(self)
        for p in (patch.object(WindowOperator, "borrow_front", return_value=(True, None)),
                  patch.object(WindowOperator, "return_front")):
            p.start()
            self.addCleanup(p.stop)

    def _executor(self, itype):
        st = WindowState(instance_type=itype)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x10), st, lambda: True, lambda _m: None)
        return ex

    def test_the_begin_click(self):
        self.assertTrue(self._executor(config.INSTANCE_PRIVATE)._press_begin())
        self.assertEqual(self.inputs.keys(), [("press", TAB), ("down", True), ("up", True),
                                              ("release", TAB)])

    def test_the_entry_click(self):
        entry = ToNEntry.ToNEntry(0x10, osc_port=9000, can_operate=lambda: True)
        with patch.object(ToNEntry.time, "sleep"):
            self.assertTrue(entry.click("警告同意"))
        self.assertEqual(self.inputs.keys(), [("press", TAB), ("down", True), ("up", True),
                                              ("release", TAB)])

    def test_nothing_outside_private(self):
        self.assertFalse(self._executor(config.INSTANCE_PUBLIC)._press_begin())
        entry = ToNEntry.ToNEntry(0x10, osc_port=9000, can_operate=lambda: False)
        self.assertFalse(entry.click("警告同意"))
        self.assertEqual(self.inputs.calls, [])
