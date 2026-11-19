"""ログのオーバーレイの重なり順を debug.log に記録する（依頼者 2026-11-19）。

長く使うとオーバーレイが VRChat の後ろに回る原因を見分けるための記録だけで、最前面のかけ直しはしない。
本物の Tk の窓は作らない。LogOverlay のメソッドは、偽物の self に対して呼ぶ。
"""
from tests.support import *  # noqa: F401,F403


def _above(pid, cls="UnityWndClass", topmost=False, foreground=False, rect=(-1927, 357, 974, 1039)):
    return {"hwnd": pid * 10, "pid": pid, "class": cls, "topmost": topmost, "rect": rect, "foreground": foreground}


class TestOverlayStackLine(unittest.TestCase):
    def test_no_window_above(self):
        self.assertEqual(
            mainGUI.overlay_stack_line(True, [], {}, 0),
            "[オーバーレイ] 重なり: 最前面の指定=あり・上に重なる窓なし（開いてから 0 分）")

    def test_one_foreground_window_above(self):
        above = [_above(100, foreground=True)]
        self.assertEqual(
            mainGUI.overlay_stack_line(False, above, {100: "VRChat.exe"}, 182),
            "[オーバーレイ] 重なり: 最前面の指定=なし・上に重なる窓: VRChat.exe UnityWndClass"
            "（最前面の指定なし・前面）(-1927, 357, 974, 1039)（開いてから 182 分）")

    def test_two_windows_are_joined_with_comma_in_order(self):
        above = [_above(100, "ToNUtilsClass", topmost=True, rect=(0, 0, 3840, 1080)),
                 _above(200, "UnityWndClass", foreground=True)]
        self.assertEqual(
            mainGUI.overlay_stack_line(False, above, {100: "ToNUtils.exe", 200: "VRChat.exe"}, 5),
            "[オーバーレイ] 重なり: 最前面の指定=なし・上に重なる窓: ToNUtils.exe ToNUtilsClass"
            "（最前面の指定あり）(0, 0, 3840, 1080)、VRChat.exe UnityWndClass"
            "（最前面の指定なし・前面）(-1927, 357, 974, 1039)（開いてから 5 分）")

    def test_unknown_process_name_is_question_mark(self):
        above = [_above(100), _above(200)]
        line = mainGUI.overlay_stack_line(True, above, {100: "VRChat.exe"}, 1)
        self.assertIn("VRChat.exe UnityWndClass", line)
        self.assertIn("、? UnityWndClass", line)

    def test_topmost_none_is_question_mark(self):
        self.assertEqual(
            mainGUI.overlay_stack_line(None, [], {}, 0),
            "[オーバーレイ] 重なり: 最前面の指定=?・上に重なる窓なし（開いてから 0 分）")


class TestCheckStack(unittest.TestCase):
    def _overlay(self, closed=False):
        o = type("O", (), {})()
        o._closed = closed
        o._hwnd = 100
        o._opened_at = time.monotonic()
        o._last_stack = None
        o._stack_job = None
        o.after_calls = []

        def after(ms, func):
            o.after_calls.append((ms, func))
            return f"job{len(o.after_calls)}"
        o.after = after
        o._check_stack = lambda: mainGUI.LogOverlay._check_stack(o)
        return o

    def _run(self, o, topmost=True, above=(), names=None):
        names = names or {}
        with patch.object(mainGUI.WindowOperator, "is_topmost", return_value=topmost), \
                patch.object(mainGUI.WindowOperator, "windows_above", return_value=list(above)), \
                patch.object(mainGUI.ProcessCheck, "process_name", side_effect=lambda pid: names.get(pid)), \
                patch.object(mainGUI.DebugLog, "write") as write:
            mainGUI.LogOverlay._check_stack(o)
        return write

    def test_first_check_writes_one_line(self):
        o = self._overlay()
        write = self._run(o, True, [])
        write.assert_called_once()
        self.assertEqual(write.call_args[0][0],
                         "[オーバーレイ] 重なり: 最前面の指定=あり・上に重なる窓なし（開いてから 0 分）")

    def test_same_state_does_not_write_again(self):
        o = self._overlay()
        above = [_above(100, foreground=True)]
        self._run(o, False, above, {100: "VRChat.exe"})
        write = self._run(o, False, above, {100: "VRChat.exe"})
        write.assert_not_called()

    def test_topmost_change_writes(self):
        o = self._overlay()
        self._run(o, True, [])
        write = self._run(o, False, [])
        write.assert_called_once()
        self.assertIn("最前面の指定=なし", write.call_args[0][0])

    def test_more_windows_above_writes(self):
        o = self._overlay()
        self._run(o, True, [])
        write = self._run(o, True, [_above(100)], {100: "VRChat.exe"})
        write.assert_called_once()
        self.assertIn("VRChat.exe UnityWndClass", write.call_args[0][0])

    def test_foreground_change_writes(self):
        o = self._overlay()
        self._run(o, True, [_above(100)], {100: "VRChat.exe"})
        write = self._run(o, True, [_above(100, foreground=True)], {100: "VRChat.exe"})
        write.assert_called_once()

    def test_rect_change_alone_does_not_write(self):
        o = self._overlay()
        self._run(o, False, [_above(100, rect=(0, 0, 500, 300))], {100: "VRChat.exe"})
        write = self._run(o, False, [_above(100, rect=(10, 20, 600, 400))], {100: "VRChat.exe"})
        write.assert_not_called()

    def test_minutes_are_elapsed_since_opened(self):
        o = self._overlay()
        o._opened_at = time.monotonic() - 183 * 60
        write = self._run(o, True, [])
        self.assertIn("（開いてから 183 分）", write.call_args[0][0])

    def test_closed_writes_nothing_and_schedules_nothing(self):
        o = self._overlay(closed=True)
        write = self._run(o, True, [])
        write.assert_not_called()
        self.assertEqual(o.after_calls, [])

    def test_open_schedules_the_next_check(self):
        o = self._overlay()
        self._run(o, True, [])
        self.assertEqual(len(o.after_calls), 1)
        self.assertEqual(o.after_calls[0][0], mainGUI.OVERLAY_STACK_CHECK_MS)
        self.assertEqual(o.after_calls[0][1], o._check_stack)
        self.assertEqual(o._stack_job, "job1")

    def test_exception_still_schedules_the_next_check(self):
        o = self._overlay()
        with patch.object(mainGUI.WindowOperator, "is_topmost", return_value=True), \
                patch.object(mainGUI.WindowOperator, "windows_above", side_effect=RuntimeError("x")), \
                patch.object(mainGUI.DebugLog, "exception") as exc, \
                patch.object(mainGUI.DebugLog, "write") as write:
            mainGUI.LogOverlay._check_stack(o)
        exc.assert_called_once_with("mainGUI.LogOverlay._check_stack")
        write.assert_not_called()
        self.assertEqual(len(o.after_calls), 1)


class TestOverlayCloseCancelsCheck(unittest.TestCase):
    def test_close_cancels_the_scheduled_check_and_keeps_old_behavior(self):
        o = type("O", (), {})()
        o._closed = False
        o._stack_job = "job1"
        o.cancelled, o.destroyed, o.closed_calls = [], [], []
        o.after_cancel = o.cancelled.append
        o.destroy = lambda: o.destroyed.append(True)
        o._on_close = lambda: o.closed_calls.append(True)
        mainGUI.LogOverlay.close(o)
        mainGUI.LogOverlay.close(o)
        self.assertEqual(o.cancelled, ["job1"])
        self.assertEqual(o.destroyed, [True])
        self.assertEqual(o.closed_calls, [True])

    def test_close_survives_after_cancel_error(self):
        o = type("O", (), {})()
        o._closed = False
        o._stack_job = "job1"
        o.destroyed = []

        def boom(_job):
            raise RuntimeError("x")
        o.after_cancel = boom
        o.destroy = lambda: o.destroyed.append(True)
        o._on_close = None
        mainGUI.LogOverlay.close(o)
        self.assertTrue(o._closed)
        self.assertEqual(o.destroyed, [True])


class TestWindowsAbove(unittest.TestCase):
    """重なり順（上へ）: 100（オーバーレイ）の上に 200 → 300 → 400 → 500 → 600 → 700 → 0"""
    OWN = (0, 0, 500, 300)
    CHAIN = {100: 200, 200: 300, 300: 400, 400: 500, 500: 600, 600: 700, 700: 0}
    RECTS = {
        200: (400, 100, 1400, 900),      # 重なる・最前面の指定あり
        300: (600, 0, 900, 300),         # 重なる辺が無い（左端 600 > 500）
        400: (0, 0, 400, 200),           # 非表示
        500: (0, 0, 400, 200),           # 最小化
        600: (0, 0, 400, 200),           # DWM で隠された
        700: (-1927, 100, 974, 1139),    # 重なる・前面
    }
    VISIBLE = {200: True, 300: True, 400: False, 500: True, 600: True, 700: True}
    ICONIC = {500: True}
    CLOAKED = {600: True}
    TOPMOST = {200: True}
    PID = {200: 11, 700: 22}
    CLASS = {200: "ToNUtilsClass", 700: "UnityWndClass"}

    def _run(self, foreground=700):
        rects = dict(self.RECTS)
        rects[100] = self.OWN
        w = WindowOperator.win32gui
        with patch.object(w, "GetWindowRect", side_effect=lambda h: rects[h]), \
                patch.object(w, "GetWindow", side_effect=lambda h, cmd: self.CHAIN[h]), \
                patch.object(w, "IsWindowVisible", side_effect=lambda h: self.VISIBLE[h]), \
                patch.object(w, "IsIconic", side_effect=lambda h: self.ICONIC.get(h, False)), \
                patch.object(w, "GetClassName", side_effect=lambda h: self.CLASS.get(h, "Other")), \
                patch.object(w, "GetWindowLong",
                             side_effect=lambda h, idx: WindowOperator.win32con.WS_EX_TOPMOST if self.TOPMOST.get(h) else 0), \
                patch.object(WindowOperator.win32process, "GetWindowThreadProcessId",
                             side_effect=lambda h: (1, self.PID.get(h, 0))), \
                patch.object(WindowOperator, "_is_cloaked", side_effect=lambda h: self.CLOAKED.get(h, False)), \
                patch.object(WindowOperator, "foreground_hwnd", return_value=foreground):
            return WindowOperator.windows_above(100)

    def test_only_visible_overlapping_windows_in_order_above(self):
        result = self._run()
        self.assertEqual([r["hwnd"] for r in result], [200, 700])

    def test_fields_topmost_and_foreground(self):
        first, second = self._run()
        self.assertEqual(first, {"hwnd": 200, "pid": 11, "class": "ToNUtilsClass", "topmost": True,
                                 "rect": (400, 100, 1000, 800), "foreground": False})
        self.assertEqual(second, {"hwnd": 700, "pid": 22, "class": "UnityWndClass", "topmost": False,
                                  "rect": (-1927, 100, 2901, 1039), "foreground": True})

    def test_rect_that_only_touches_does_not_count(self):
        """辺が接するだけ（共通部分の幅が 0）は重ならない"""
        with patch.dict(self.RECTS, {200: (500, 0, 900, 300)}):
            result = self._run()
        self.assertEqual([r["hwnd"] for r in result], [700])

    def test_get_window_rect_error_returns_empty(self):
        w = WindowOperator.win32gui
        with patch.object(w, "GetWindowRect", side_effect=RuntimeError("x")), \
                patch.object(WindowOperator.DebugLog, "exception") as exc:
            self.assertEqual(WindowOperator.windows_above(100), [])
        exc.assert_called_once_with("WindowOperator.windows_above")

    def test_step_limit_stops_a_loop(self):
        """GetWindow が輪になっていても、上限で止まる"""
        w = WindowOperator.win32gui
        with patch.object(w, "GetWindowRect", return_value=self.OWN), \
                patch.object(w, "GetWindow", return_value=100), \
                patch.object(w, "IsWindowVisible", return_value=False), \
                patch.object(WindowOperator, "foreground_hwnd", return_value=0):
            self.assertEqual(WindowOperator.windows_above(100), [])


class TestIsTopmost(unittest.TestCase):
    def test_flag_set(self):
        with patch.object(WindowOperator.win32gui, "GetWindowLong",
                          return_value=WindowOperator.win32con.WS_EX_TOPMOST | 0x100):
            self.assertIs(WindowOperator.is_topmost(100), True)

    def test_flag_not_set(self):
        with patch.object(WindowOperator.win32gui, "GetWindowLong", return_value=0x100):
            self.assertIs(WindowOperator.is_topmost(100), False)

    def test_error_is_none(self):
        with patch.object(WindowOperator.win32gui, "GetWindowLong", side_effect=RuntimeError("x")), \
                patch.object(WindowOperator.DebugLog, "exception") as exc:
            self.assertIsNone(WindowOperator.is_topmost(100))
        exc.assert_called_once_with("WindowOperator.is_topmost")


if __name__ == "__main__":
    unittest.main()
