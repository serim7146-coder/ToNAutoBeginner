"""マウスのサイドボタンをキーバインドに使う（GW。マウス4 = mouse:x1・マウス5 = mouse:x2）。
本物のマウスフック（SetWindowsHookEx）は張らない。ボタンの押した・離したは MouseButtons._on_hook_event(wParam, mouseData)
で起こす（support が MouseButtons.start / stop を偽物にしている）。本物のキー・OSC・窓は使わない（keyboard は偽物）"""
import ctypes
import types

from tests.support import *  # noqa: F401,F403
import tests.test_chase_toggle as _chase_tests        # チェイスの偽の App を借りる（ここでは回さない）
import tests.test_move_keys as _move_tests            # 移動の偽の App・本物の App の土台を借りる（ここでは回さない）
import tests.test_ui_tidy as _ui_tidy                 # 本物の App を立てる土台を借りる（ここでは回さない）

X1_DATA = MouseButtons.XBUTTON1 << 16
X2_DATA = MouseButtons.XBUTTON2 << 16
DOWN = MouseButtons.WM_XBUTTONDOWN
UP = MouseButtons.WM_XBUTTONUP
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201


def press(button, kind="down"):
    """サイドボタンの通知を起こす（button は 1 か 2）"""
    MouseButtons._on_hook_event(DOWN if kind == "down" else UP, button << 16)


def tap(button):
    press(button, "down")
    press(button, "up")


class _MouseCase(unittest.TestCase):
    """どのテストも、購読と押下の記録を空にして始め、空にして終わる"""

    def setUp(self):
        MouseButtons.reset_for_tests()
        self.addCleanup(MouseButtons.reset_for_tests)


class TestNames(_MouseCase):
    def test_the_two_side_buttons(self):
        self.assertEqual((MouseButtons.X1, MouseButtons.X2), ("mouse:x1", "mouse:x2"))
        self.assertTrue(MouseButtons.is_mouse("mouse:x1"))
        self.assertTrue(MouseButtons.is_mouse("mouse:x2"))
        self.assertEqual(MouseButtons.display("mouse:x1"), "マウス4")
        self.assertEqual(MouseButtons.display("mouse:x2"), "マウス5")

    def test_the_other_buttons_and_non_strings_are_not_mouse(self):
        for name in ("mouse:left", "mouse:right", "mouse:middle", "mouse:x3", "mouse", "x1", "", "ctrl+mouse:x1",
                     None, 1, MagicMock()):
            with self.subTest(name=name):
                self.assertFalse(MouseButtons.is_mouse(name))

    def test_hotkey_accepts_and_shows_them(self):
        self.assertTrue(HotKey.is_valid("mouse:x1"))
        self.assertTrue(HotKey.is_valid("mouse:x2"))
        self.assertEqual(HotKey.display("mouse:x1"), "マウス4")
        self.assertEqual(HotKey.display("mouse:x2"), "マウス5")

    def test_hotkey_accepts_them_without_the_keyboard_module(self):
        with patch.object(HotKey, "keyboard", None):
            self.assertTrue(HotKey.is_valid("mouse:x1"))
            self.assertFalse(HotKey.is_valid("p"), "キーは今まで通り（keyboard が無ければ False）")

    def test_hotkey_rejects_the_unsupported_ones(self):
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        with patch.object(HotKey, "keyboard", real):
            for name in ("mouse:left", "mouse:x3", "ctrl+mouse:x1"):
                with self.subTest(name=name):
                    self.assertFalse(HotKey.is_valid(name))
            self.assertTrue(HotKey.is_valid("ctrl+p"), "キーの検証は今まで通り")
        self.assertEqual(HotKey.display("ctrl+p"), "Ctrl+P")


class TestEvents(_MouseCase):
    def test_down_and_up_reach_a_subscriber_of_everything(self):
        got = []
        MouseButtons.hook(got.append)
        press(1, "down")
        press(1, "up")
        press(2, "down")
        self.assertEqual([(e.name, e.event_type) for e in got],
                         [("mouse:x1", "down"), ("mouse:x1", "up"), ("mouse:x2", "down")])

    def test_the_event_has_the_attributes_of_a_keyboard_event_but_no_scan_code(self):
        got = []
        MouseButtons.hook(got.append)
        press(1)
        self.assertEqual((got[0].name, got[0].event_type), ("mouse:x1", "down"))
        self.assertFalse(hasattr(got[0], "scan_code"), "キーの判定（scan_code）に混ざらない")

    def test_a_button_subscription_only_hears_that_button(self):
        got1, got2 = [], []
        MouseButtons.hook_key("mouse:x2", got2.append)
        MouseButtons.hook_key("mouse:x1", got1.append)
        tap(1)
        self.assertEqual([e.event_type for e in got1], ["down", "up"])
        self.assertEqual(got2, [], "x1 では x2 の購読は呼ばれない")
        tap(2)
        self.assertEqual([e.name for e in got2], ["mouse:x2", "mouse:x2"])

    def test_suppress_is_accepted_and_ignored(self):
        got = []
        MouseButtons.hook_key("mouse:x1", got.append, suppress=True)
        press(1)
        self.assertEqual(len(got), 1)

    def test_unhook_stops_the_events_and_a_second_unhook_is_harmless(self):
        got = []
        handle = MouseButtons.hook_key("mouse:x1", got.append)
        press(1)
        MouseButtons.unhook(handle)
        press(1, "up")
        press(1)
        self.assertEqual(len(got), 1)
        MouseButtons.unhook(handle)                 # もう外れている
        MouseButtons.unhook(object())               # 知らない物

    def test_is_pressed_follows_down_and_up(self):
        self.assertFalse(MouseButtons.is_pressed("mouse:x1"))
        press(1, "down")
        self.assertTrue(MouseButtons.is_pressed("mouse:x1"))
        self.assertFalse(MouseButtons.is_pressed("mouse:x2"))
        press(1, "up")
        self.assertFalse(MouseButtons.is_pressed("mouse:x1"))

    def test_is_pressed_is_already_updated_when_the_callback_runs(self):
        seen = []
        MouseButtons.hook(lambda e: seen.append((e.event_type, MouseButtons.is_pressed(e.name))))
        tap(2)
        self.assertEqual(seen, [("down", True), ("up", False)])

    def test_other_buttons_and_other_messages_are_ignored(self):
        got = []
        MouseButtons.hook(got.append)
        for wparam, data in ((DOWN, 0), (UP, 0), (DOWN, 3 << 16), (DOWN, 0xFFFF << 16),
                             (WM_MOUSEMOVE, X1_DATA), (WM_LBUTTONDOWN, X1_DATA), (0, X1_DATA)):
            with self.subTest(wparam=hex(wparam), data=hex(data)):
                MouseButtons._on_hook_event(wparam, data)
        self.assertEqual(got, [])
        self.assertFalse(MouseButtons.is_pressed("mouse:x1") or MouseButtons.is_pressed("mouse:x2"))

    def test_the_low_word_of_mouse_data_does_not_matter(self):
        got = []
        MouseButtons.hook(got.append)
        MouseButtons._on_hook_event(DOWN, X2_DATA | 0x1234)
        self.assertEqual([e.name for e in got], ["mouse:x2"])

    def test_an_exception_in_a_subscriber_does_not_stop_the_next_one(self):
        got = []
        MouseButtons.hook(lambda e: 1 / 0)
        MouseButtons.hook(got.append)
        with patch.object(DebugLog, "exception") as logged:
            press(1)
        self.assertEqual(len(got), 1)
        logged.assert_called_once()

    def test_a_subscriber_may_unhook_itself_while_it_is_called(self):
        got = []
        holder = {}
        holder["h"] = MouseButtons.hook(lambda e: MouseButtons.unhook(holder["h"]))
        MouseButtons.hook(got.append)
        press(1)
        press(1, "up")
        self.assertEqual(len(got), 2)

    def test_subscribing_works_when_the_hook_is_not_installed(self):
        self.assertFalse(MouseButtons.available())
        got = []
        MouseButtons.hook_key("mouse:x1", got.append)       # 張れていなくても受ける（通知が来ないだけ）
        self.assertEqual(got, [])


@unittest.skipUnless(sys.platform == "win32", "Windows のフック")
class TestHookProcedure(_MouseCase):
    """フックの手続き（_proc）。CallNextHookEx は偽物（本物のフックは張らない）"""

    def setUp(self):
        super().setUp()
        self.fake = MagicMock()
        self.fake.CallNextHookEx.return_value = 7
        p = patch.object(MouseButtons, "_user32", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.got = []
        MouseButtons.hook(self.got.append)

    def _call(self, n_code, wparam, data):
        struct = MouseButtons.MSLLHOOKSTRUCT()
        struct.mouseData = data
        return MouseButtons._proc(n_code, wparam, ctypes.addressof(struct)), struct

    def test_a_side_button_is_delivered_and_passed_on(self):
        result, _struct = self._call(0, DOWN, X1_DATA)
        self.assertEqual(result, 7, "CallNextHookEx の戻りをそのまま返す（suppress しない）")
        self.assertEqual([(e.name, e.event_type) for e in self.got], [("mouse:x1", "down")])
        self.fake.CallNextHookEx.assert_called_once()
        self.assertEqual(self.fake.CallNextHookEx.call_args.args[:3], (None, 0, DOWN))

    def test_other_messages_are_passed_on_untouched(self):
        for n_code, wparam in ((0, WM_MOUSEMOVE), (0, WM_LBUTTONDOWN), (-1, DOWN), (-1, UP)):
            with self.subTest(n_code=n_code, wparam=hex(wparam)):
                self.fake.CallNextHookEx.reset_mock()
                result, _struct = self._call(n_code, wparam, X1_DATA)
                self.assertEqual(result, 7)
                self.fake.CallNextHookEx.assert_called_once()
        self.assertEqual(self.got, [], "nCode < 0 の XBUTTON と、ほかのメッセージは通知しない")

    def test_even_when_the_subscriber_raises_it_is_passed_on(self):
        MouseButtons.hook(lambda e: 1 / 0)
        with patch.object(DebugLog, "exception"):
            result, _struct = self._call(0, UP, X2_DATA)
        self.assertEqual(result, 7)

    def test_even_when_the_notification_itself_fails_it_is_passed_on(self):
        with patch.object(MouseButtons, "_on_hook_event", side_effect=RuntimeError("boom")), \
             patch.object(DebugLog, "exception") as logged:
            result, _struct = self._call(0, DOWN, X1_DATA)
        self.assertEqual(result, 7)
        logged.assert_called_once()


class _FakeUser32:
    """スレッドの動きを確かめる偽の user32。GetMessageW は PostThreadMessageW が来るまで待つ"""

    def __init__(self, hook_ok=True):
        self.hook_ok = hook_ok
        self.quit = threading.Event()
        self.hooked, self.unhooked, self.posted = [], [], []

    def SetWindowsHookExW(self, *args):
        self.hooked.append(args)
        return 0x1234 if self.hook_ok else 0

    def PeekMessageW(self, *args):
        return 0

    def GetMessageW(self, *args):
        self.quit.wait(3.0)
        return 0

    def UnhookWindowsHookEx(self, handle):
        self.unhooked.append(handle)
        return 1

    def PostThreadMessageW(self, tid, msg, wparam, lparam):
        self.posted.append((tid, msg))
        self.quit.set()
        return 1

    def CallNextHookEx(self, *args):
        return 0


@unittest.skipUnless(sys.platform == "win32", "Windows のフック")
class TestStartAndStop(_MouseCase):
    """本物の start / stop（support が偽物にしている物）を、偽の user32 で動かす。本物のフックは張らない"""

    def setUp(self):
        super().setUp()
        self.user32 = _FakeUser32()
        p = patch.object(MouseButtons, "_user32", self.user32)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._finish)

    def _finish(self):
        self.user32.quit.set()
        thread = MouseButtons._thread
        if thread is not None:
            thread.join(2.0)
        MouseButtons._thread = None
        MouseButtons._available = False

    def test_start_installs_one_low_level_mouse_hook_and_stop_removes_it(self):
        self.assertTrue(REAL_MOUSE_START())
        self.assertTrue(MouseButtons.available())
        self.assertEqual(len(self.user32.hooked), 1)
        self.assertEqual(self.user32.hooked[0][0], MouseButtons.WH_MOUSE_LL)
        self.assertTrue(MouseButtons._thread.daemon)
        handle = MouseButtons.hook(lambda e: None)
        REAL_MOUSE_STOP()
        self.assertEqual([msg for _tid, msg in self.user32.posted], [MouseButtons.WM_QUIT])
        self.assertEqual(self.user32.unhooked, [0x1234])
        self.assertFalse(MouseButtons.available())
        self.assertEqual(MouseButtons._hooks, [], "購読も空にする")
        self.assertIsNotNone(handle)

    def test_starting_twice_keeps_one_hook(self):
        self.assertTrue(REAL_MOUSE_START())
        self.assertTrue(REAL_MOUSE_START())
        self.assertEqual(len(self.user32.hooked), 1)
        REAL_MOUSE_STOP()

    def test_a_hook_that_cannot_be_installed_returns_false_and_does_nothing(self):
        self.user32.hook_ok = False
        with patch.object(DebugLog, "write") as written:
            self.assertFalse(REAL_MOUSE_START())
        self.assertFalse(MouseButtons.available())
        self.assertTrue(any("フックを張れませんでした" in c.args[0] for c in written.call_args_list))
        got = []
        MouseButtons.hook_key("mouse:x1", got.append)       # 購読は受ける（通知が来ないだけ）
        REAL_MOUSE_STOP()                                   # 張れていなくても落ちない
        self.assertEqual(self.user32.posted, [])

    def test_stop_without_start_is_harmless(self):
        REAL_MOUSE_STOP()
        self.assertEqual(self.user32.posted, [])


class TestSupportFakes(unittest.TestCase):
    def test_start_and_stop_are_fakes_in_the_tests(self):
        self.assertIsNot(MouseButtons.start, REAL_MOUSE_START)
        self.assertIsNot(MouseButtons.stop, REAL_MOUSE_STOP)
        self.assertTrue(MouseButtons.start())
        MouseButtons.stop()


# ── 捕捉 ─────────────────────────────────────
class TestCapture(_MouseCase):
    def setUp(self):
        super().setUp()
        kb = types.SimpleNamespace(callback=None, unhooked=[], KEY_DOWN="down", KEY_UP="up")

        def hook(callback, suppress=False):
            kb.callback = callback
            kb.suppress = suppress
            return "kb-hook"
        kb.hook = hook
        kb.unhook = kb.unhooked.append
        kb.get_hotkey_name = lambda names: "+".join(names)
        self.kb = kb
        p = patch.object(HotKey, "keyboard", kb)
        p.start()
        self.addCleanup(p.stop)

    def _key(self, kind, name, code):
        self.kb.callback(types.SimpleNamespace(event_type=kind, name=name, scan_code=code))

    def _capture(self, script, timeout=3.0):
        """捕捉を別のスレッドで始め、両方のフックが張られてから script を流す"""
        out = {}
        thread = threading.Thread(target=lambda: out.setdefault("key", HotKey.capture(timeout)), daemon=True)
        thread.start()
        self.assertTrue(_wait_until(lambda: self.kb.callback is not None and len(MouseButtons._hooks) == 1),
                        "キーとマウスの両方を張る")
        script()
        thread.join(5.0)
        self.assertFalse(thread.is_alive())
        return out["key"]

    def test_pressing_and_releasing_a_side_button_gives_its_name(self):
        self.assertEqual(self._capture(lambda: tap(1)), "mouse:x1")
        self.assertEqual(self._capture(lambda: tap(2)), "mouse:x2")

    def test_the_down_alone_does_not_finish_it(self):
        def script():
            press(1, "down")
            press(2, "down")
            press(2, "up")                  # 押した方が離れて決まる
        self.assertEqual(self._capture(script), "mouse:x2")

    def test_a_release_of_a_button_pressed_before_the_capture_is_not_counted(self):
        def script():
            press(1, "up")                  # 捕捉の前から押していた分の離した通知
            tap(2)
        self.assertEqual(self._capture(script), "mouse:x2")

    def test_a_key_that_finishes_first_wins(self):
        def script():
            self._key("down", "p", 25)
            self._key("up", "p", 25)
            tap(1)                          # 後から来た分は無視
        self.assertEqual(self._capture(script), "p")

    def test_a_side_button_that_finishes_first_wins_over_a_key(self):
        def script():
            tap(2)
            self._key("down", "p", 25)
            self._key("up", "p", 25)
        self.assertEqual(self._capture(script), "mouse:x2")

    def test_no_combination_with_a_held_key(self):
        def script():
            self._key("down", "ctrl", 29)
            tap(1)
        self.assertEqual(self._capture(script), "mouse:x1", "キーを押したままでもサイドボタンだけ")

    def test_both_hooks_are_removed_whatever_the_ending(self):
        self._capture(lambda: tap(1))
        self.assertEqual(self.kb.unhooked, ["kb-hook"])
        self.assertEqual(MouseButtons._hooks, [])
        self.kb.unhooked.clear()
        self.kb.callback = None
        self._capture(lambda: (self._key("down", "p", 25), self._key("up", "p", 25)))
        self.assertEqual(self.kb.unhooked, ["kb-hook"])
        self.assertEqual(MouseButtons._hooks, [])

    def test_a_timeout_is_none_and_removes_both_hooks(self):
        self.assertIsNone(HotKey.capture(0.1))
        self.assertEqual(self.kb.unhooked, ["kb-hook"])
        self.assertEqual(MouseButtons._hooks, [])
        self.assertIs(self.kb.suppress, False, "suppress しない")

    def test_a_side_button_pressed_but_never_released_times_out(self):
        self.kb.callback = None

        def run():
            self.assertTrue(_wait_until(lambda: self.kb.callback is not None and MouseButtons._hooks))
            press(1, "down")
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertIsNone(HotKey.capture(0.3))
        thread.join(2.0)
        self.assertEqual(MouseButtons._hooks, [])

    def test_the_keyboard_hook_failing_leaves_no_mouse_hook(self):
        def broken(callback, suppress=False):
            raise RuntimeError("no hook")
        self.kb.hook = broken
        with patch.object(DebugLog, "exception"):
            self.assertIsNone(HotKey.capture(0.1))
        self.assertEqual(MouseButtons._hooks, [])


class TestHotKeyDispatch(_MouseCase):
    """HotKey.hook_key / unhook / is_pressed の振り分け"""

    def test_a_mouse_name_goes_to_mouse_buttons(self):
        kb = MagicMock()
        got = []
        handle = HotKey.hook_key("mouse:x1", got.append, suppress=False, kb=kb)
        self.assertIsInstance(handle, MouseButtons._Hook)
        kb.hook_key.assert_not_called()
        press(1)
        self.assertEqual(len(got), 1)
        HotKey.unhook(handle, kb=kb)
        kb.unhook.assert_not_called()
        press(1, "up")
        self.assertEqual(len(got), 1)

    def test_a_key_name_goes_to_the_keyboard(self):
        kb = MagicMock()
        kb.hook_key.return_value = "kb-handle"
        callback = MagicMock()
        self.assertEqual(HotKey.hook_key("f9", callback, suppress=False, kb=kb), "kb-handle")
        kb.hook_key.assert_called_once_with("f9", callback, suppress=False)
        HotKey.unhook("kb-handle", kb=kb)
        kb.unhook.assert_called_once_with("kb-handle")
        self.assertEqual(MouseButtons._hooks, [])

    def test_the_keyboards_key_error_comes_out_for_the_caller(self):
        kb = MagicMock()
        kb.unhook.side_effect = KeyError
        with self.assertRaises(KeyError):
            HotKey.unhook("kb-handle", kb=kb)

    def test_is_pressed(self):
        kb = MagicMock()
        kb.is_pressed.return_value = True
        self.assertFalse(HotKey.is_pressed("mouse:x2", kb=kb))
        press(2)
        self.assertTrue(HotKey.is_pressed("mouse:x2", kb=kb))
        kb.is_pressed.assert_not_called()
        self.assertTrue(HotKey.is_pressed("f9", kb=kb))
        kb.is_pressed.assert_called_once_with("f9")

    def test_the_default_keyboard_is_the_one_the_module_has(self):
        kb = MagicMock()
        with patch.object(HotKey, "keyboard", kb):
            HotKey.hook_key("f9", print)
            HotKey.unhook("h")
            HotKey.is_pressed("f9")
        kb.hook_key.assert_called_once_with("f9", print, suppress=False)
        kb.unhook.assert_called_once_with("h")
        kb.is_pressed.assert_called_once_with("f9")


# ── 停止・マクロ開始 ─────────────────────────
def _stop_start_app(stop="mouse:x2", start=""):
    """停止・マクロ開始のキーまわりだけを本物の App の関数で動かす偽物。反応は calls に貯める"""
    app = type("FakeApp", (), {})()
    app._capturing_key = False
    app._emergency_stop_key_pressed = False
    app._start_key_pressed = False
    app._watched_keys = (stop, start)
    app.calls = []
    app.logs = []
    app._log = app.logs.append
    app._key_down_now = mainGUI.App._key_down_now
    app._check_stop_key = lambda key, event=None: mainGUI.App._check_stop_key(app, key, event)
    app._check_start_key = lambda key, event=None: mainGUI.App._check_start_key(app, key, event)
    app._on_stop_start_key_event = lambda event=None: mainGUI.App._on_stop_start_key_event(app, event)
    app._after_from_hook = lambda func, *args: func(*args)
    app._on_stop_key = lambda key: app.calls.append(("stop", key))
    app._on_start_key = lambda key: app.calls.append(("start", key))
    return app


class TestStopAndStartKeys(_MouseCase):
    def setUp(self):
        super().setUp()
        self.keyboard = MagicMock()
        p = patch.object(mainGUI, "keyboard", self.keyboard)
        p.start()
        self.addCleanup(p.stop)

    def _hooked(self, **kwargs):
        app = _stop_start_app(**kwargs)
        mainGUI.App._hook_stop_start_keys(app)
        self.addCleanup(mainGUI.App._unhook_stop_start_keys, app)
        return app

    def test_the_stop_key_stops_once_per_press(self):
        app = self._hooked(stop="mouse:x2")
        press(2, "down")
        self.assertEqual(app.calls, [("stop", "mouse:x2")])
        for _ in range(4):
            press(2, "down")                # 押しっぱなしの繰り返し
        self.assertEqual(app.calls, [("stop", "mouse:x2")], "増えない")
        press(2, "up")
        press(2, "down")                    # 離してまた押せばもう 1 回
        self.assertEqual(app.calls, [("stop", "mouse:x2")] * 2)

    def test_a_quick_tap_whose_state_is_already_released_still_counts(self):
        """通知が遅れて届き、表ではもう離れていても、押した通知そのものがそのボタンなら止まる"""
        app = self._hooked(stop="mouse:x2")
        app._on_stop_start_key_event(MouseButtons.Event("mouse:x2", "down"))     # is_pressed は False のまま
        self.assertEqual(app.calls, [("stop", "mouse:x2")])

    def test_the_start_key_starts_the_same_way(self):
        app = self._hooked(stop="p", start="mouse:x1")
        self.keyboard.is_pressed.return_value = False
        press(1, "down")
        press(1, "down")
        self.assertEqual(app.calls, [("start", "mouse:x1")])
        press(1, "up")
        press(1, "down")
        self.assertEqual(app.calls, [("start", "mouse:x1")] * 2)

    def test_the_other_side_button_does_nothing(self):
        app = self._hooked(stop="mouse:x2", start="mouse:x1")
        press(1, "down")
        self.assertEqual(app.calls, [("start", "mouse:x1")])
        press(1, "up")
        press(2, "down")
        self.assertEqual(app.calls, [("start", "mouse:x1"), ("stop", "mouse:x2")])

    def test_nothing_happens_while_the_key_is_being_captured(self):
        app = self._hooked(stop="mouse:x2")
        app._capturing_key = True
        press(2, "down")
        self.assertEqual(app.calls, [])

    def test_a_cleared_key_does_nothing(self):
        app = self._hooked(stop="", start="")
        tap(1)
        tap(2)
        self.assertEqual(app.calls, [])

    def test_a_keyboard_notice_does_not_re_fire_a_held_side_button(self):
        app = self._hooked(stop="mouse:x2")
        press(2, "down")
        app._on_stop_start_key_event(types.SimpleNamespace(event_type="down", name="a", scan_code=30))
        app._on_stop_start_key_event(types.SimpleNamespace(event_type="up", name="a", scan_code=30))
        self.assertEqual(app.calls, [("stop", "mouse:x2")], "押している間はキーの通知が来ても増えない")

    def test_unhooking_removes_the_mouse_subscription(self):
        app = _stop_start_app(stop="mouse:x2")
        mainGUI.App._hook_stop_start_keys(app)
        self.assertEqual(len(MouseButtons._hooks), 1)
        mainGUI.App._unhook_stop_start_keys(app)
        self.assertEqual(MouseButtons._hooks, [])
        press(2, "down")
        self.assertEqual(app.calls, [])
        mainGUI.App._unhook_stop_start_keys(app)            # 二度呼んでも落ちない

    def test_without_the_keyboard_module_nothing_is_hooked(self):
        app = _stop_start_app()
        with patch.object(mainGUI, "keyboard", None):
            mainGUI.App._hook_stop_start_keys(app)
        self.assertEqual(MouseButtons._hooks, [])

    def test_key_down_now_for_a_side_button(self):
        down = MouseButtons.Event("mouse:x1", "down")
        up = MouseButtons.Event("mouse:x1", "up")
        self.assertTrue(mainGUI.App._key_down_now("mouse:x1", down))
        self.assertFalse(mainGUI.App._key_down_now("mouse:x1", up))
        self.assertFalse(mainGUI.App._key_down_now("mouse:x1", None))
        self.assertFalse(mainGUI.App._key_down_now("mouse:x2", down), "別のボタンの通知では押されたことにならない")
        press(1, "down")
        self.assertTrue(mainGUI.App._key_down_now("mouse:x1", None), "表で押されていれば通知が無くても True")
        self.keyboard.is_pressed.assert_not_called()

    def test_a_side_button_notice_does_not_disturb_the_key_judgement(self):
        self.keyboard.is_pressed.return_value = False
        self.keyboard.parse_hotkey.return_value = (((67,),),)
        event = MouseButtons.Event("mouse:x1", "down")          # scan_code は無い
        self.assertFalse(mainGUI.App._key_down_now("f9", event))
        key_event = types.SimpleNamespace(event_type="down", scan_code=67)
        self.assertTrue(mainGUI.App._key_down_now("f9", key_event), "キーの判定は今まで通り")


class TestStartupAndClose(_MouseCase):
    def _app(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app.hooked = []
        for name in ("_hook_chase_keys", "_hook_move_keys", "_hook_suicide_cancel_key", "_hook_stop_start_keys"):
            setattr(app, name, (lambda n: lambda: app.hooked.append(n))(name))
        return app

    def test_a_hook_that_cannot_be_installed_is_logged_once_and_the_keys_still_work(self):
        app = self._app()
        with patch.object(MouseButtons, "start", return_value=False) as started:
            mainGUI.App._start_emergency_stop_polling(app)
        started.assert_called_once_with()
        self.assertEqual(app.logs, ["[キー] ⚠ マウスのサイドボタンは使えません（フックを張れませんでした）"])
        self.assertEqual(app.hooked, ["_hook_chase_keys", "_hook_move_keys", "_hook_suicide_cancel_key",
                                      "_hook_stop_start_keys"], "キーのフックは今まで通り張る")

    def test_a_working_hook_logs_nothing(self):
        app = self._app()
        with patch.object(MouseButtons, "start", return_value=True):
            mainGUI.App._start_emergency_stop_polling(app)
        self.assertEqual(app.logs, [])
        self.assertEqual(len(app.hooked), 4)

    def test_closing_removes_the_mouse_hook_after_the_key_hooks(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.logs = []
        app._log = app.logs.append
        app.order = []
        app._save_settings_now = lambda: None
        app._stop = lambda: None
        app.destroy = lambda: app.order.append("destroy")
        app._stop_window_volume = lambda: None
        app._unhook_chase_keys = lambda: app.order.append("chase")
        app._unhook_move_keys = lambda: app.order.append("move")
        app._unhook_stop_start_keys = lambda: app.order.append("stop_start")
        app._show_own_windows_again = lambda: None
        with patch.object(MouseButtons, "stop", lambda: app.order.append("mouse")):
            mainGUI.App._on_close(app)
        self.assertEqual(app.order, ["chase", "move", "stop_start", "mouse", "destroy"])


# ── チェイス・移動・自爆キャンセル ─────────────
class TestChaseMouseKeys(_MouseCase):
    def setUp(self):
        super().setUp()
        SharedState.set_chase_enabled(True)
        self.addCleanup(SharedState.set_chase_enabled, True)
        self.app = _chase_tests._App(self).app
        self.app.v_chase_cw_key.set("mouse:x1")
        self.app.v_chase_ccw_key.set("mouse:x2")

    def test_the_side_buttons_are_hooked_through_mouse_buttons_not_the_keyboard(self):
        self.app._hook_chase_keys()
        self.keyboard.hook_key.assert_not_called()
        self.assertEqual(len(MouseButtons._hooks), 2)
        self.assertEqual(sorted(h.name for h in MouseButtons._hooks), ["mouse:x1", "mouse:x2"])

    def test_each_press_reaches_the_front_window_once(self):
        self.app._hook_chase_keys()
        press(1, "down")
        press(1, "down")                            # 押しっぱなしの繰り返し
        press(1, "up")
        self.a.on_chase_key.assert_called_once_with("cw", "マウス4")
        press(2, "down")
        press(2, "up")
        self.assertEqual(self.a.on_chase_key.call_args_list[-1].args, ("ccw", "マウス5"))

    def test_unhooking_removes_them(self):
        self.app._hook_chase_keys()
        self.app._unhook_chase_keys()
        self.assertEqual(MouseButtons._hooks, [])
        self.assertEqual(self.app._chase_hooks, [])
        press(1, "down")
        self.a.on_chase_key.assert_not_called()

    def test_a_key_and_a_side_button_can_be_mixed(self):
        self.app.v_chase_cw_key.set("f1")
        self.app._hook_chase_keys()
        self.assertEqual([c.args[0] for c in self.keyboard.hook_key.call_args_list], ["f1"])
        self.assertEqual([h.name for h in MouseButtons._hooks], ["mouse:x2"])


class TestMoveMouseKeys(_MouseCase):
    def setUp(self):
        super().setUp()
        self.app = _move_tests._FakeApp(self).app
        self.addCleanup(self.app._unhook_move_keys)

    def test_down_moves_every_window_and_up_stops_them(self):
        self.app.v_move_forward_key.set("mouse:x1")
        self.app._hook_move_keys()
        self.keyboard.hook_key.assert_not_called()
        press(1, "down")
        for monitor in self.monitors:
            monitor.on_move_key.assert_called_once_with("forward", True)
        press(1, "down")                            # 押しっぱなしの繰り返し
        for monitor in self.monitors:
            self.assertEqual(monitor.on_move_key.call_count, 1)
        press(1, "up")
        for monitor in self.monitors:
            self.assertEqual(monitor.on_move_key.call_args_list, [call("forward", True), call("forward", False)])

    def test_jump_is_once_per_press(self):
        self.app.v_jump_key.set("mouse:x2")
        self.app._hook_move_keys()
        press(2, "down")
        press(2, "down")
        press(2, "up")
        for monitor in self.monitors:
            monitor.on_jump_key.assert_called_once_with()
            monitor.on_move_key.assert_not_called()
        press(2, "down")
        for monitor in self.monitors:
            self.assertEqual(monitor.on_jump_key.call_count, 2)

    def test_rehooking_does_not_double_and_unhook_removes(self):
        self.app.v_move_left_key.set("mouse:x1")
        self.app._hook_move_keys()
        self.app._hook_move_keys()
        self.assertEqual(len(MouseButtons._hooks), 1)
        self.app._unhook_move_keys()
        self.assertEqual(MouseButtons._hooks, [])
        self.assertEqual(self.app._move_hooks, [])

    def test_a_key_and_a_side_button_for_different_directions(self):
        self.app.v_move_forward_key.set("f3")
        self.app.v_move_right_key.set("mouse:x2")
        self.app._hook_move_keys()
        self.assertEqual([c.args[0] for c in self.keyboard.hook_key.call_args_list], ["f3"])
        press(2, "down")
        for monitor in self.monitors:
            monitor.on_move_key.assert_called_once_with("right", True)


class TestCancelMouseKey(_MouseCase):
    def _app(self):
        app = type("FakeApp", (), {})()
        app._capturing_key = False
        app._suicide_cancel_down = False
        app._suicide_cancel_hook = None
        app.logs = []
        app._log = app.logs.append
        app.cancelled = []
        app.after = lambda _ms, fn, *args: fn(*args)
        app.v_suicide_cancel_key = _CancelKeyVar("mouse:x1")
        app._unhook_suicide_cancel_key = lambda: mainGUI.App._unhook_suicide_cancel_key(app)
        app._suicide_cancel_key_down = lambda k: mainGUI.App._suicide_cancel_key_down(app, k)
        app._on_suicide_cancel_key = lambda k: app.cancelled.append(k)
        return app

    def test_one_press_cancels_once_even_when_held(self):
        app = self._app()
        keyboard = MagicMock()
        with patch.object(mainGUI, "keyboard", keyboard):
            mainGUI.App._hook_suicide_cancel_key(app)
        keyboard.hook_key.assert_not_called()
        press(1, "down")
        press(1, "down")
        self.assertEqual(app.cancelled, ["mouse:x1"])
        press(1, "up")
        press(1, "down")
        self.assertEqual(app.cancelled, ["mouse:x1"] * 2)

    def test_unhooking_removes_it(self):
        app = self._app()
        with patch.object(mainGUI, "keyboard", MagicMock()):
            mainGUI.App._hook_suicide_cancel_key(app)
            mainGUI.App._unhook_suicide_cancel_key(app)
        self.assertEqual(MouseButtons._hooks, [])
        press(1, "down")
        self.assertEqual(app.cancelled, [])


# ── 画面・設定・重なり ─────────────────────────
class TestMouseKeysInTheApp(_ui_tidy._AppCase):
    """本物の App（キーのフックは偽物）。全部のキーに登録でき、保存・読み込み・重なりの検査が名前に関係なく効く"""

    TARGETS = ("stop", "start", "cancel", "cw", "ccw", "forward", "left", "right", "jump")

    def setUp(self):
        super().setUp()
        MouseButtons.reset_for_tests()
        self.addCleanup(MouseButtons.reset_for_tests)
        for target in _move_tests.TARGETS:
            self.app._move_key_var(target).set("")
        for target, kwargs in ((mainGUI.threading, {"Thread": RunNow}),
                               (self.app, {"after": lambda _ms, fn, *a: fn(*a)})):
            for name, value in kwargs.items():
                p = patch.object(target, name, value)
                p.start()
                self.addCleanup(p.stop)
        self.hooks = {}
        for name in ("_hook_suicide_cancel_key", "_hook_chase_keys", "_hook_move_keys"):
            p = patch.object(self.app, name)
            self.hooks[name] = p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: [self.app._move_key_var(t).set("") for t in _move_tests.TARGETS])
        self.addCleanup(self.app._refresh_move_key_labels)

    def _var(self, target):
        return {"stop": self.app.v_emergency_key, "start": self.app.v_start_key,
                "cancel": self.app.v_suicide_cancel_key, "cw": self.app.v_chase_cw_key,
                "ccw": self.app.v_chase_ccw_key}.get(target) or self.app._move_key_var(target)

    def _capture(self, target, key):
        self._dialog()
        with patch.object(HotKey, "capture", return_value=key):
            getattr(self.app, mainGUI.App.KEY_BUTTONS[target]["capture"]).invoke()

    def test_every_key_of_the_dialog_accepts_a_side_button(self):
        for target in self.TARGETS:
            with self.subTest(target=target):
                var = self._var(target)
                before = var.get()
                self.app.logs.clear()
                self._capture(target, "mouse:x1")
                self.assertEqual(var.get(), "mouse:x1")
                self.assertTrue(any("マウス4" in m and "変更しました" in m for m in self.app.logs), self.app.logs)
                var.set(before)

    def test_the_labels_show_the_button_name(self):
        self.app.v_start_key.set("mouse:x1")
        self.app.v_emergency_key.set("mouse:x2")
        self.app._refresh_start_key_label()
        self.app._refresh_emergency_key_label()
        self.assertEqual(self.app._start_key_text(), "マクロ開始: マウス4")
        self.assertIn("停止: マウス5", self.app.lbl_keys.cget("text"))
        self.assertIn("マクロ開始: マウス4", self.app.lbl_keys.cget("text"))
        self.app.v_jump_key.set("mouse:x1")
        self.assertEqual(self.app._move_key_text("jump"), "ジャンプ: マウス4")

    def test_the_same_side_button_cannot_be_registered_twice(self):
        self._capture("stop", "mouse:x1")
        self.assertEqual(self.app.v_emergency_key.get(), "mouse:x1")
        for target in ("start", "cancel", "cw", "ccw", "forward", "left", "right", "jump"):
            with self.subTest(target=target):
                before = self._var(target).get()
                self.app.logs.clear()
                self._capture(target, "mouse:x1")
                self.assertEqual(self._var(target).get(), before, "重なりは今までの決まりで断る")
                self.assertTrue(any("同じキーは使えません" in m for m in self.app.logs), self.app.logs)

    # ── 保存・読み込み ────────────────────────
    def _load(self, data):
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        with patch.object(HotKey, "keyboard", real), patch.object(mainGUI, "load_settings", return_value=data):
            self.app._load_saved_settings()

    def _saved(self):
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        return stored

    def test_the_side_buttons_round_trip(self):
        self.app.v_emergency_key.set("mouse:x1")
        self.app.v_start_key.set("mouse:x2")
        self.app.v_chase_cw_key.set("")
        self.app.v_chase_ccw_key.set("")
        self.app.v_suicide_cancel_key.set("")
        stored = self._saved()
        self.assertEqual((stored["emergency_stop_key"], stored["start_key"]), ("mouse:x1", "mouse:x2"))
        self.app.v_emergency_key.set("p")
        self.app.v_start_key.set("")
        self._load({"emergency_stop_key": stored["emergency_stop_key"], "start_key": stored["start_key"],
                    "chase_cw_key": "", "chase_ccw_key": "", "suicide_cancel_key": ""})
        self.assertEqual((self.app.v_emergency_key.get(), self.app.v_start_key.get()), ("mouse:x1", "mouse:x2"))

    def test_move_and_chase_and_cancel_side_buttons_load(self):
        self._load({"emergency_stop_key": "p", "start_key": "", "chase_cw_key": "mouse:x1", "chase_ccw_key": "",
                    "move_forward_key": "mouse:x2", "move_left_key": "", "move_right_key": "", "jump_key": "",
                    "suicide_cancel_key": ""})
        self.assertEqual(self.app.v_chase_cw_key.get(), "mouse:x1")
        self.assertEqual(self.app.v_move_forward_key.get(), "mouse:x2")
        self.assertEqual(self.app.lbl_keys.cget("text").split("   ")[-1], "移動: 前進 マウス5")
        stored = self._saved()
        self.assertEqual((stored["chase_cw_key"], stored["move_forward_key"]), ("mouse:x1", "mouse:x2"))

    def test_the_same_side_button_for_a_move_key_and_the_stop_key_is_unset_on_load(self):
        self._load({"emergency_stop_key": "mouse:x1", "move_forward_key": "mouse:x1", "move_left_key": "mouse:x2",
                    "start_key": "", "chase_cw_key": "", "chase_ccw_key": "", "suicide_cancel_key": ""})
        self.assertEqual(self.app.v_emergency_key.get(), "mouse:x1")
        self.assertEqual(self.app.v_move_forward_key.get(), "", "停止と重なる移動は未設定（既存の決まり）")
        self.assertEqual(self.app.v_move_left_key.get(), "mouse:x2")

    def test_a_broken_mouse_name_falls_back_like_any_broken_key(self):
        self._load({"emergency_stop_key": "mouse:left", "start_key": "mouse:x3", "chase_cw_key": "",
                    "chase_ccw_key": "", "suicide_cancel_key": ""})
        self.assertEqual(self.app.v_emergency_key.get(), config.EMERGENCY_STOP_KEY)
        self.assertEqual(self.app.v_start_key.get(), "")


class TestReadme(unittest.TestCase):
    def test_the_readme_mentions_the_side_buttons(self):
        readme = (SRC_DIR.parent / "README.md").read_text(encoding="utf-8")
        section = readme[readme.index("## キー操作"):]
        section = section[:section.index("## 詳しい仕様")]
        self.assertIn("マウスのサイドボタン（マウス4・マウス5）も登録できます（キーとの組み合わせは不可）", section)
