"""窓を大きくする（ほぼフルスクリーン）と、続行ラウンドの後に窓を戻す（WindowLayout）"""
from tests.support import *  # noqa: F401,F403

import WindowLayout


class _Screen:
    """窓の矩形を持つ偽の画面。SetWindowPos を受けて動かす"""

    WORK = (0, 0, 2560, 1400)           # タスクバーを除いた作業領域
    MONITOR = (0, 0, 2560, 1440)
    CAPTION = 31                        # クライアント上端までの高さ（タイトルバー）
    BORDER = 8                          # 左右・下の枠

    def __init__(self, rects):
        self.rects = dict(rects)        # {hwnd: (左, 上, 右, 下)}
        self.fullscreen: set = set()
        self.calls = []

    def client(self, hwnd):
        l, t, r, b = self.rects[hwnd]
        if hwnd in self.fullscreen:
            return (l, t, r, b)
        return (l + self.BORDER, t + self.CAPTION, r - self.BORDER, b - self.BORDER)

    def set_pos(self, hwnd, _after, x, y, w, h, flags):
        self.calls.append((hwnd, x, y, w, h, flags))
        l, t, r, b = self.rects[hwnd]
        if flags & WindowLayout.SWP_NOSIZE:
            w, h = r - l, b - t
        self.rects[hwnd] = (x, y, x + w, y + h)

    def patches(self, order):
        return [
            patch.object(WindowLayout, "_vrchat_windows", return_value=list(order)),
            patch.object(WindowLayout, "_window_rect", side_effect=lambda h: self.rects.get(h)),
            patch.object(WindowLayout, "_client_rect", side_effect=self.client),
            patch.object(WindowLayout, "_monitor",
                         return_value={"Work": self.WORK, "Monitor": self.MONITOR}),
            patch.object(WindowLayout, "is_fullscreen", side_effect=lambda h: h in self.fullscreen),
            patch.object(WindowLayout, "_window_exists", side_effect=lambda h: h in self.rects),
            patch.object(WindowLayout.win32gui, "SetWindowPos", side_effect=self.set_pos),
            patch.object(WindowLayout.win32gui, "IsZoomed", return_value=False),
            patch.object(WindowLayout.win32gui, "IsIconic", return_value=False),
            patch.object(WindowLayout.WindowOperator, "focus_vrchat", return_value=True),
        ]


class TestBigWindow(unittest.TestCase):
    BIG, HIDDEN1, HIDDEN2, OTHER_MONITOR = 0x10, 0x20, 0x30, 0x40

    def setUp(self):
        WindowLayout._big = None
        WindowLayout._saved.clear()
        self.addCleanup(WindowLayout._saved.clear)
        self.addCleanup(setattr, WindowLayout, "_big", None)
        self.logs = []
        WindowLayout.set_logger(self.logs.append)
        self.addCleanup(WindowLayout.set_logger, None)
        self.screen = _Screen({
            self.BIG: (-7, 0, 860, 702),
            self.HIDDEN1: (853, 0, 1720, 702),
            self.HIDDEN2: (0, 702, 867, 1404 - 10),
            self.OTHER_MONITOR: (2600, 0, 3467, 702),      # 右のモニター
        })
        for p in self.screen.patches([self.OTHER_MONITOR, self.HIDDEN2, self.HIDDEN1, self.BIG]):
            p.start()
            self.addCleanup(p.stop)

    def test_the_plan_leaves_a_strip_and_staggers_the_hidden_windows(self):
        big, moves = WindowLayout.plan((0, 0, 2560, 1400), 2, (8, 31, 8, 8),
                                       [(1, (0, 0, 867, 702), 31), (2, (0, 0, 867, 702), 31)])
        self.assertEqual(big, (-8, 0, 2568, 1398), "左右の枠は外へ。下は 2px 空ける")
        self.assertEqual(moves[1], (0, 1398 - 31), "描画の上端がすき間に来る")
        self.assertEqual(moves[2], (2560 - 867, 1398 - 31), "上の窓ほど右へ")

    def test_the_front_vrchat_is_enlarged_and_hidden_ones_peek_out(self):
        self.assertTrue(WindowLayout.toggle_big())

        l, t, r, b = self.screen.rects[self.BIG]
        self.assertEqual((t, b), (0, 1400 - config.BIG_WINDOW_GAP_PX))
        self.assertLessEqual(l, 0)
        self.assertGreaterEqual(r, 2560)
        for hidden in (self.HIDDEN1, self.HIDDEN2):
            client_top = self.screen.client(hidden)[1]
            self.assertEqual(client_top, 1400 - config.BIG_WINDOW_GAP_PX,
                             "描画の上端が下端のすき間に見える")
        self.assertNotEqual(self.screen.client(self.HIDDEN1)[0], self.screen.client(self.HIDDEN2)[0],
                            "重ならないよう横にずらす")
        self.assertEqual(self.screen.rects[self.OTHER_MONITOR], (2600, 0, 3467, 702),
                         "隠れない窓は動かさない")
        self.assertEqual(self.screen.rects[self.HIDDEN1][2] - self.screen.rects[self.HIDDEN1][0], 867,
                         "大きさは変えない")

    def test_pressing_again_puts_everything_back(self):
        before = dict(self.screen.rects)
        WindowLayout.toggle_big()
        self.assertFalse(WindowLayout.toggle_big())
        self.assertEqual(self.screen.rects, before)

    def test_a_fullscreen_window_is_left_alone(self):
        self.screen.fullscreen.add(self.BIG)
        self.assertFalse(WindowLayout.toggle_big())
        self.assertEqual(self.screen.calls, [])

    def _end(self, hwnd):
        WindowLayout.restore_after_continue(hwnd)

    def test_after_a_continue_round_the_enlarged_windows_go_back(self):
        before = dict(self.screen.rects)
        WindowLayout.on_continue_start(self.BIG)
        WindowLayout.toggle_big()
        self._end(self.BIG)
        self.assertEqual(self.screen.rects, before)
        self.assertIsNone(WindowLayout._big)
        self.assertIn("続行ラウンドが終わったので", self.logs[-1])

    def test_enlarged_before_the_continue_round_also_goes_back(self):
        before = dict(self.screen.rects)
        WindowLayout.toggle_big()
        WindowLayout.on_continue_start(self.BIG)        # もう大きい
        self._end(self.BIG)
        self.assertEqual(self.screen.rects, before)

    def test_a_window_resized_by_hand_goes_back_too(self):
        before = self.screen.rects[self.HIDDEN1]
        WindowLayout.on_continue_start(self.HIDDEN1)
        self.screen.rects[self.HIDDEN1] = (100, 100, 2000, 1300)    # 手で広げた
        self._end(self.HIDDEN1)
        self.assertEqual(self.screen.rects[self.HIDDEN1], before)

    def test_fullscreen_is_left_with_alt_enter_first(self):
        before = self.screen.rects[self.HIDDEN1]
        WindowLayout.on_continue_start(self.HIDDEN1)
        self.screen.rects[self.HIDDEN1] = self.screen.MONITOR
        self.screen.fullscreen.add(self.HIDDEN1)
        order = []

        def leave(hwnd):
            order.append(("alt+enter", hwnd))
            self.screen.fullscreen.discard(hwnd)
        with patch.object(WindowLayout, "_leave_fullscreen", side_effect=leave):
            self._end(self.HIDDEN1)
        self.assertEqual(order, [("alt+enter", self.HIDDEN1)])
        self.assertEqual(self.screen.rects[self.HIDDEN1], before)

    def test_an_unchanged_window_is_not_touched(self):
        WindowLayout.on_continue_start(self.HIDDEN1)
        self._end(self.HIDDEN1)
        self.assertEqual(self.screen.calls, [])

    def test_leaving_fullscreen_presses_alt_enter_in_front(self):
        with patch.object(WindowLayout.WindowOperator, "borrow_front", return_value=(True, None)), \
             patch.object(WindowLayout.WindowOperator, "hold_key") as key, \
             patch.object(WindowLayout.time, "sleep"):
            WindowLayout._leave_fullscreen(self.BIG)
        key.assert_called_once_with("alt+enter", config.CURSOR_LOCK_PRESS_SEC)


class TestContinueHooks(unittest.TestCase):
    """続行フリーズの始まり・終わりで、その窓の hwnd を渡す（1回ずつ）"""

    def setUp(self):
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)
        self.calls = []
        SharedState.set_continue_hooks(lambda h: self.calls.append(("start", h)),
                                       lambda h: self.calls.append(("end", h)))
        self.addCleanup(SharedState.set_continue_hooks, None, None)

    def test_start_and_end_call_the_hooks_once(self):
        st = WindowState(window_idx=1, run_id=SharedState.current_run(), hwnd=0x55)
        SharedState.continue_round_start(st)
        SharedState.continue_round_start(st)
        SharedState.continue_round_end(st)
        SharedState.continue_round_end(st)
        self.assertEqual(self.calls, [("start", 0x55), ("end", 0x55)])

    def test_the_monitor_puts_its_hwnd_on_the_state(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x77), {}, lambda _m: None, window_idx=1)
        with patch.object(LogMonitor.threading, "Thread"):
            monitor.start()
        self.addCleanup(monitor.stop)
        self.assertEqual(monitor.st.hwnd, 0x77)

    def test_the_end_hook_restores_in_the_background(self):
        with patch.object(WindowLayout.threading, "Thread") as thread:
            WindowLayout.on_continue_end(0x55)
        self.assertIs(thread.call_args.kwargs["target"], WindowLayout.restore_after_continue)
        self.assertEqual(thread.call_args.kwargs["args"], (0x55,))


class TestBigWindowKey(unittest.TestCase):
    """窓を大きくするキー（mainGUI）。押した瞬間に1回だけ切り替える。未設定なら何もしない"""

    def _app(self):
        app = MagicMock()
        app._big_key_pressed = False
        return app

    def test_one_press_toggles_once(self):
        app = self._app()
        with patch.object(mainGUI.App, "_key_down_now", side_effect=[True, True, False]), \
             patch.object(mainGUI.threading, "Thread") as thread:
            for _ in range(3):
                mainGUI.App._check_big_key(app, "f3")
        self.assertEqual(thread.call_count, 1, "押しっぱなしでは増えない")
        self.assertIs(thread.call_args.kwargs["target"], WindowLayout.toggle_big)

    def test_unset_does_nothing(self):
        app = self._app()
        with patch.object(mainGUI.App, "_key_down_now") as down, \
             patch.object(mainGUI.threading, "Thread") as thread:
            mainGUI.App._check_big_key(app, "")
        down.assert_not_called()
        thread.assert_not_called()

    def test_it_is_saved_and_conflicts_are_refused(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('"big_window_key": self.v_big_key.get()', src)
        app = MagicMock()
        app.v_emergency_key.get.return_value = "p"
        app.v_start_key.get.return_value = "f9"
        app.v_suicide_cancel_key.get.return_value = "^"
        self.assertEqual(mainGUI.App._big_key_conflict(app, "p"), "緊急停止")
        self.assertEqual(mainGUI.App._big_key_conflict(app, config.CHASE_CW_KEY), "チェイス")
        self.assertIsNone(mainGUI.App._big_key_conflict(app, "f3"))
