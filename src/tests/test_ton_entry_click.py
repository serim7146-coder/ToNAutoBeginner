"""入室の選択画面を、四角を探して Tab を押したままクリックする流れ（ToNEntry.run・_click_point）。
OSC・窓・マウス・キー・撮影・待ちは全部差し替える（実入力・実窓操作はしない）"""
from tests.support import *  # noqa: F401,F403
import numpy as np

import EntryPanel

HWND = 0x1234
ORIGIN = (100, 50)


class _Rig:
    """偽の窓の周り。呼ばれた順を events に残す"""

    def __init__(self, test, flavor=None, music=None, running=None):
        self.events = []
        self.picks = []                 # (expect, index)
        self.cursor = (5, 5)
        self.running = [True] if running is None else running
        self.pick_result = lambda n, expect, index: (10.0, 20.0)
        self.logs = []

        def pick(bgr, height, expect, index):
            self.picks.append((expect, index))
            return self.pick_result(len(self.picks), expect, index)

        def set_cursor(point):
            self.cursor = tuple(point)
            self.events.append(("set_cursor", tuple(point)))
            return True

        def ev(name):
            return lambda *a, **k: self.events.append((name,))

        for target, name, fake in (
                (EntryPanel, "pick", pick),
                (EntryPanel, "available", lambda: True),
                (WindowOperator, "client_size", lambda h: (1920, 1080)),
                (WindowOperator, "window_origin", lambda h: ORIGIN),
                (WindowOperator, "foreground_hwnd", lambda: HWND),
                (WindowOperator, "cursor_position", lambda: self.cursor),
                (WindowOperator, "set_cursor_position", set_cursor),
                (WindowOperator, "borrow_front", lambda h: (True, "loan")),
                (WindowOperator, "return_front", lambda loan: self.events.append(("return_front",))),
                (WindowOperator, "press_tab", ev("press_tab")),
                (WindowOperator, "release_tab", ev("release_tab")),
                (WindowOperator, "mouse_click", lambda sec, pause=False: self.events.append(("click", sec))),
                (ToNEntry.DebugLog, "write", lambda *a: None),
                (ToNEntry.DebugLog, "exception", lambda *a: None),
                (ToNEntry.time, "sleep", lambda *a: None),
        ):
            p = patch.object(target, name, side_effect=fake)
            p.start()
            test.addCleanup(p.stop)
        self.entry = ToNEntry.ToNEntry(
            HWND, osc_port=19990, log=self.logs.append, is_running=lambda: self.running[0],
            can_operate=lambda: True, flavor=flavor, music=music)
        self.entry._osc = MagicMock()
        test.addCleanup(self.entry.close)
        self.entry._capture_bgr = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.entry.wait_for_panel = lambda timeout=None: True
        self.entry.move = MagicMock(return_value=True)

    def names(self):
        return [e[0] for e in self.events]


class TestRun(unittest.TestCase):
    def test_four_steps_pick_with_the_table(self):
        rig = _Rig(self)
        self.assertTrue(rig.entry.run())
        self.assertEqual(rig.picks, [(None, 0), (3, 0), (2, 1), (2, 1)])
        self.assertEqual(rig.names().count("click"), 4)

    def test_click_point_is_screen_point(self):
        rig = _Rig(self)
        rig.entry.run()
        placed = [e[1] for e in rig.events if e[0] == "set_cursor"]
        self.assertIn((10 + ORIGIN[0], 20 + ORIGIN[1]), placed)

    def test_missing_button_stops_without_clicking(self):
        rig = _Rig(self)
        rig.pick_result = lambda n, expect, index: None if n >= 2 else (10.0, 20.0)
        with patch.object(config, "TON_ENTRY_STEP_FIND_SEC", 0.0):
            self.assertFalse(rig.entry.run())
        self.assertEqual(rig.names().count("click"), 1, "2 手順目は押さない")
        self.assertEqual(len(rig.picks), 2)
        rig.entry._osc.stop_all.assert_called()

    def test_run_never_moves(self):
        rig = _Rig(self)
        self.assertTrue(rig.entry.run())
        rig.entry.move.assert_not_called()
        rig.entry._osc.press.assert_not_called()

    def test_flavor_and_music_choose_the_index(self):
        rig = _Rig(self, flavor=2, music=0)
        self.assertTrue(rig.entry.run())
        self.assertEqual(rig.picks[1], (3, 2))
        self.assertEqual(rig.picks[2], (2, 0))

    def test_stops_when_cancelled_midway(self):
        rig = _Rig(self)

        def click_then_cancel(sec, pause=False):
            rig.events.append(("click", sec))
            rig.running[0] = False
        with patch.object(WindowOperator, "mouse_click", side_effect=click_then_cancel):
            self.assertFalse(rig.entry.run())
        self.assertEqual(rig.names().count("click"), 1)
        rig.entry._osc.stop_all.assert_called()


class TestClickPoint(unittest.TestCase):
    def test_always_releases_tab_and_returns_front_on_error(self):
        rig = _Rig(self)
        with patch.object(WindowOperator, "mouse_click", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                rig.entry._click_point((300, 400), "テスト")
        names = rig.names()
        self.assertIn("release_tab", names)
        self.assertIn("return_front", names)
        self.assertEqual(rig.events[-1], ("set_cursor", (5, 5)), "カーソルも元へ")

    def test_no_tab_without_focus(self):
        rig = _Rig(self)
        with patch.object(WindowOperator, "borrow_front", return_value=(False, None)):
            self.assertFalse(rig.entry._click_point((300, 400), "テスト"))
        self.assertNotIn("press_tab", rig.names())
        self.assertNotIn("release_tab", rig.names())
        self.assertTrue(any("フォーカス取得失敗" in m for m in rig.logs))

    def test_cursor_is_placed_after_tab(self):
        rig = _Rig(self)
        self.assertTrue(rig.entry._click_point((300, 400), "テスト"))
        names = rig.names()
        self.assertLess(names.index("press_tab"), names.index("set_cursor"))
        self.assertEqual(rig.events[names.index("set_cursor")], ("set_cursor", (300, 400)))
        self.assertLess(names.index("set_cursor"), names.index("click"))
        self.assertLess(names.index("click"), names.index("release_tab"))

    def test_takes_the_global_lock(self):
        rig = _Rig(self)
        held = []
        with patch.object(WindowOperator, "mouse_click",
                          side_effect=lambda sec, pause=False: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked())):
            rig.entry._click_point((300, 400), "テスト")
        self.assertEqual(held, [True])
        self.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked())


if __name__ == "__main__":
    unittest.main()
