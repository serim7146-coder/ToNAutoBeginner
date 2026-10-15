"""FF: テストの土台の見張り（tests/input_guard.py）自体のテスト。本物の関数の代わりに偽物（MagicMock）を包み、
見張りが本物を呼ばずに記録すること・そのテストを失敗にすることを確かめる。本物の入力は送らない
（見張りが本物を止めるので、包んだ本物の関数を呼んでも届かない）"""
from tests.support import *  # noqa: F401,F403
import socket
import win32api
from tests import input_guard
from tests.input_guard import Guarded, expect_hits

_user32 = ctypes.WinDLL("user32")
_user32.GetDesktopWindow.restype = ctypes.c_void_p


class TestEachKind(unittest.TestCase):
    def test_always_blocked_ones_are_recorded_and_not_called(self):
        for name in sorted(input_guard.ALWAYS):
            real = MagicMock()
            with expect_hits() as got:
                result = Guarded(name, real)(0, 0, 0, 0)
            real.assert_not_called()
            self.assertEqual([h[1] for h in got], [name])
            self.assertEqual(result, 0 if name.startswith("mciSendString") else (0 if name == "SendInput" else 1), name)

    def test_a_window_of_another_process(self):
        desktop = _user32.GetDesktopWindow()
        self.assertTrue(input_guard.real_foreign_window(desktop), "デスクトップの窓はほかのプロセス")
        for name in sorted(input_guard.TO_WINDOW):
            real = MagicMock()
            with expect_hits() as got:
                Guarded(name, real)(desktop, 0, 0, 0)
            real.assert_not_called()
            self.assertEqual([h[1] for h in got], [name])

    def test_our_own_tk_window_and_no_window_pass(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.update_idletasks()
        own = int(root.wm_frame(), 16)
        self.assertFalse(input_guard.real_foreign_window(own))
        for hwnd in (own, 0, 0x7FFFFFF0):           # このプロセスの窓・無い窓
            real = MagicMock(return_value="real")
            with expect_hits() as got:
                self.assertEqual(Guarded("ShowWindow", real)(hwnd, 0), "real")
            self.assertEqual(got, [])

    def test_osc(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        real = MagicMock(return_value=5)
        with patch.object(input_guard, "_orig_sendto", real), expect_hits() as got:
            for host, port in (("127.0.0.1", 9000), ("localhost", 9100), ("127.0.0.1", 9055)):
                self.assertEqual(sock.sendto(b"abc", (host, port)), 3)
            real.assert_not_called()
            sock.sendto(b"abc", ("127.0.0.1", 9101))          # 範囲の外は本物へ（ここでは偽物）
            real.assert_called_once()
        self.assertEqual([h[1] for h in got], ["OSC"] * 3)

    def test_any_windll_gives_the_guarded_function(self):
        for dll, name in ((ctypes.WinDLL("user32"), "SetCursorPos"), (ctypes.windll.user32, "keybd_event"),
                          (ctypes.WinDLL("winmm"), "mciSendStringW"), (ctypes.windll.user32, "PostMessageW")):
            self.assertIsInstance(getattr(dll, name), Guarded, name)
        self.assertNotIsInstance(ctypes.WinDLL("user32").GetCursorPos, Guarded, "読むだけのものは包まない")
        self.assertIsInstance(WindowOperator.user32.SetCursorPos, Guarded, "モジュールが読み込みのときに引いたものも")

    def test_argtypes_go_to_the_real_function(self):
        func = ctypes.WinDLL("user32").ClipCursor
        func.argtypes = [ctypes.c_void_p]
        self.assertEqual(func._func.argtypes, [ctypes.c_void_p])

    def test_pywin32(self):
        self.assertIsInstance(win32api.SetCursorPos, input_guard._PyFunc)
        real = MagicMock()
        with expect_hits() as got:
            input_guard._PyFunc("SetCursorPos", real)((1, 2))
        real.assert_not_called()
        self.assertEqual([h[1] for h in got], ["SetCursorPos"])


class TestItFailsTheTest(unittest.TestCase):
    def _run_inner(self, body, allow=None):
        class Inner(unittest.TestCase):
            def test_it(self):
                body()
        if allow:
            Inner.test_it = input_guard.allow_real_input(allow)(Inner.test_it)
        result = unittest.TestResult()
        Inner("test_it").run(result)
        return result

    def test_a_hit_in_the_test_fails_it(self):
        real = MagicMock()
        result = self._run_inner(lambda: Guarded("keybd_event", real)(0, 0, 0, 0))
        self.assertEqual(len(result.failures), 1)
        self.assertIn("keybd_event", result.failures[0][1])
        real.assert_not_called()

    def test_a_hit_from_another_thread_is_not_missed(self):
        real = MagicMock()

        def body():
            t = threading.Thread(target=lambda: Guarded("SendInput", real)(1, None, 0), name="裏のスレッド")
            t.start()
            t.join()
        result = self._run_inner(body)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("裏のスレッド", result.failures[0][1])
        real.assert_not_called()

    def test_outside_a_test_it_fails_at_the_module_end(self):
        with patch.dict(input_guard._state, {"test": None}):
            input_guard._hit("SetCursorPos", "(テストの外)")
        with self.assertRaises(AssertionError):
            input_guard.check_outside()
        input_guard.check_outside()                 # 一度出したら消える

    def test_the_mark_lets_one_test_through(self):
        real = MagicMock(return_value="real")
        result = self._run_inner(lambda: Guarded("SetCursorPos", real)(1, 2), allow="本物を確かめる")
        self.assertEqual(result.failures, [])
        real.assert_called_once_with(1, 2)

    def test_a_clean_test_passes(self):
        result = self._run_inner(lambda: None)
        self.assertEqual((result.failures, result.errors), ([], []))


class TestNoTestTurnsItOff(unittest.TestCase):
    def test_no_test_uses_the_mark(self):
        """今は見張りを外すテストは無い（外すなら理由を書いて、報告の一覧に足す）"""
        here = Path(__file__).parent
        marked = [p.name for p in here.glob("test_*.py") if p.name != Path(__file__).name
                  and "allow_real_input(" in p.read_text(encoding="utf-8")]
        self.assertEqual(marked, [])


if __name__ == "__main__":
    unittest.main()
