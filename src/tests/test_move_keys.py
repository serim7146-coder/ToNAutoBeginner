"""移動のキー（GV: 前進・左・右・ジャンプ）。押している間、監視中の全窓が動く。ジャンプは 1 回押しで 1 回。
本物のキー・フック・OSC・窓は使わない（keyboard は偽物、OSC は記録する偽物、背面のキー送信は patch）"""
from tests.support import *  # noqa: F401,F403
import tests.test_item_fetch as _item_fetch_tests     # 読み込みの偽の画面を借りる（ここでは回さない）
import tests.test_ui_tidy as _ui_tidy                 # 本物の App を立てる土台を借りる（ここでは回さない）

TARGETS = ("forward", "left", "right", "jump")
NOT_PRIVATE = (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP, config.INSTANCE_EMERALD_CITY,
               config.INSTANCE_ACHIEVEMENT, "", None)


def _executor(test, osc=True, instance_type=config.INSTANCE_PRIVATE, running=True):
    """本物の ActionExecutor。OSC は記録する偽物（osc=False なら OSC の無い窓）"""
    ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x100, osc_port=9000 if osc else None),
                                       WindowState(in_round=False, instance_type=instance_type),
                                       lambda: running, test.logs.append)
    if osc:
        ex._osc = _ChaseOsc(test)
    test.addCleanup(ex.move_keys_release_all)
    return ex


class _FakeApp:
    """移動のキーまわりだけを本物の App の関数で動かす偽物"""

    def __init__(self, test):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app._running = True
        app._capturing_key = False
        app.logs = []
        app._log = app.logs.append
        app.MOVE_KEY_FIELDS = mainGUI.App.MOVE_KEY_FIELDS
        test.monitors = [MagicMock(), MagicMock()]
        app.monitors = test.monitors
        app.after = lambda _ms, fn, *args: fn(*args)
        for name in ("_hook_move_keys", "_unhook_move_keys", "_move_key_down", "_move_key_up", "_on_move_key"):
            setattr(app, name, (lambda n: lambda *a: getattr(mainGUI.App, n)(app, *a))(name))
        app._move_hooks = []
        app._move_keys_down = set()
        self.app = app
        test.keyboard = MagicMock()
        test.handlers = {}

        def register(key, callback, suppress):
            test.handlers[key] = callback
            return ("hook", key)
        test.keyboard.hook_key.side_effect = register
        p = patch.object(mainGUI, "keyboard", test.keyboard)
        p.start()
        test.addCleanup(p.stop)


class TestMoveKeyHooks(unittest.TestCase):
    def setUp(self):
        self.app = _FakeApp(self).app

    def _event(self, key, kind):
        self.handlers[key](type("Event", (), {"event_type": kind})())

    def _hooked(self):
        return [c.args[0] for c in self.keyboard.hook_key.call_args_list]

    def test_unset_keys_are_not_hooked(self):
        self.app._hook_move_keys()
        self.keyboard.hook_key.assert_not_called()
        self.assertEqual(self.app._move_hooks, [])

    def test_only_the_keys_that_are_set_are_hooked_without_suppress(self):
        self.app.v_move_left_key.set("f4")
        self.app.v_jump_key.set("f6")
        self.app._hook_move_keys()
        self.assertEqual(self._hooked(), ["f4", "f6"])
        self.assertTrue(all(c.kwargs["suppress"] is False for c in self.keyboard.hook_key.call_args_list),
                        "suppress しない（チェイスと同じ）")

    def test_clearing_a_key_unhooks_it(self):
        self.app.v_move_forward_key.set("f3")
        self.app.v_move_left_key.set("f4")
        self.app._hook_move_keys()
        before = list(self.app._move_hooks)
        self.app.v_move_forward_key.set("")
        self.keyboard.hook_key.reset_mock()
        self.app._hook_move_keys()
        self.assertEqual([c.args[0] for c in self.keyboard.unhook.call_args_list], before, "前のフックを全部外す")
        self.assertEqual(self._hooked(), ["f4"], "残りだけ拾い直す")
        self.assertEqual(len(self.app._move_hooks), 1)

    def test_changing_a_key_rehooks_without_doubling(self):
        self.app.v_move_forward_key.set("f3")
        self.app._hook_move_keys()
        self.app.v_move_forward_key.set("f5")
        self.keyboard.unhook.reset_mock()
        self.app._hook_move_keys()
        self.keyboard.unhook.assert_called_once_with(("hook", "f3"))
        self.assertEqual(self.app._move_hooks, [("hook", "f5")])
        self.app._hook_move_keys()                  # 何度呼んでも二重にならない
        self.assertEqual(len(self.app._move_hooks), 1)

    def test_unhook_removes_everything_and_survives_a_missing_hook(self):
        self.app.v_move_forward_key.set("f3")
        self.app.v_jump_key.set("f6")
        self.app._hook_move_keys()
        self.keyboard.unhook.side_effect = KeyError
        self.app._unhook_move_keys()
        self.assertEqual(self.app._move_hooks, [])
        self.assertEqual(self.keyboard.unhook.call_count, 2)

    def test_hooking_works_without_a_keyboard_module(self):
        self.app.v_move_forward_key.set("f3")
        with patch.object(mainGUI, "keyboard", None):
            self.app._hook_move_keys()
        self.assertEqual(self.app._move_hooks, [])

    def test_the_hook_is_not_gated_by_the_chase_setting(self):
        self.app.v_move_forward_key.set("f3")
        SharedState.set_chase_enabled(False)
        self.addCleanup(SharedState.set_chase_enabled, True)
        self.app._hook_move_keys()
        self.assertEqual(self._hooked(), ["f3"])

    # ── 押す・離す ──────────────────────────
    def test_down_then_up_reach_every_window(self):
        self.app.v_move_forward_key.set("f3")
        self.app._hook_move_keys()
        self._event("f3", "down")
        for monitor in self.monitors:
            monitor.on_move_key.assert_called_once_with("forward", True)
        self._event("f3", "up")
        for monitor in self.monitors:
            self.assertEqual(monitor.on_move_key.call_args_list,
                             [call("forward", True), call("forward", False)])
            monitor.on_jump_key.assert_not_called()

    def test_each_direction_goes_with_its_own_name(self):
        for target, key in (("forward", "f3"), ("left", "f4"), ("right", "f5")):
            self.app._move_key_var(target).set(key)
        self.app._hook_move_keys()
        for key in ("f3", "f4", "f5"):
            self._event(key, "down")
        self.assertEqual([c.args for c in self.monitors[0].on_move_key.call_args_list],
                         [("forward", True), ("left", True), ("right", True)], "同時に押せる")

    def test_a_held_key_repeating_counts_once(self):
        self.app.v_move_left_key.set("f4")
        self.app._hook_move_keys()
        for _ in range(5):
            self._event("f4", "down")                   # OS の押しっぱなしの繰り返し
        self.monitors[0].on_move_key.assert_called_once_with("left", True)
        self._event("f4", "up")
        self._event("f4", "down")                       # 離した後に押せばまた 1 回
        self.assertEqual(self.monitors[0].on_move_key.call_count, 3)

    def test_jump_is_one_press_per_key_down_and_nothing_on_up(self):
        self.app.v_jump_key.set("f6")
        self.app._hook_move_keys()
        self._event("f6", "down")
        self._event("f6", "down")
        self._event("f6", "down")
        for monitor in self.monitors:
            monitor.on_jump_key.assert_called_once_with()
        self._event("f6", "up")
        for monitor in self.monitors:
            monitor.on_jump_key.assert_called_once_with()
            monitor.on_move_key.assert_not_called()
        self._event("f6", "down")
        self.assertEqual(self.monitors[0].on_jump_key.call_count, 2)

    def test_nothing_happens_when_the_macro_is_not_running(self):
        self.app.v_move_forward_key.set("f3")
        self.app.v_jump_key.set("f6")
        self.app._hook_move_keys()
        self.app._running = False
        for kind in ("down", "up"):
            self._event("f3", kind)
            self._event("f6", kind)
        for monitor in self.monitors:
            monitor.on_move_key.assert_not_called()
            monitor.on_jump_key.assert_not_called()

    def test_nothing_happens_without_monitors(self):
        self.app.v_move_forward_key.set("f3")
        self.app._hook_move_keys()
        self.app.monitors = []
        self._event("f3", "down")
        self._event("f3", "up")
        for monitor in self.monitors:
            monitor.on_move_key.assert_not_called()

    def test_no_reaction_while_a_key_is_being_captured(self):
        self.app.v_move_forward_key.set("f3")
        self.app.v_jump_key.set("f6")
        self.app._hook_move_keys()
        self.app._capturing_key = True
        self._event("f3", "down")
        self._event("f6", "down")
        for monitor in self.monitors:
            monitor.on_move_key.assert_not_called()
            monitor.on_jump_key.assert_not_called()

    def test_a_release_is_not_swallowed_by_capturing(self):
        """押してから設定を始めても、離す通知は通す（押したまま残さない）"""
        self.app.v_move_forward_key.set("f3")
        self.app._hook_move_keys()
        self._event("f3", "down")
        self.app._capturing_key = True
        self._event("f3", "up")
        self.assertEqual(self.monitors[0].on_move_key.call_args_list[-1], call("forward", False))

    def test_a_late_notice_after_closing_is_ignored(self):
        self.app.v_move_forward_key.set("f3")
        self.app._hook_move_keys()
        with patch.object(mainGUI.UiInbox, "call_soon", side_effect=tk.TclError):
            self._event("f3", "down")
            self._event("f3", "up")                     # 例外にしない


class TestMoveHoldsOsc(unittest.TestCase):
    """OSC の窓: 押している間 1 を送り直し、離したら 0。stop_all() は使わない"""

    def setUp(self):
        p = patch.object(config, "CHASE_RESEND_SEC", 0.01)
        p.start()
        self.addCleanup(p.stop)
        self.logs = []
        self.ex = _executor(self)
        self.osc = self.ex._osc

    def _ones(self, address):
        return self.osc.sent.count((address, 1))

    def test_down_sends_one_resends_and_up_sends_zero(self):
        self.assertFalse(self.ex.move_key_down("forward"), "新しく押した")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveForward") >= 3), "離すまで送り直す")
        thread = self.ex._move_holds["forward"][1]
        self.ex.move_key_up("forward")
        self.assertFalse(thread.is_alive(), "スレッドが終わる")
        self.assertEqual(self.osc.sent[-1], ("/input/MoveForward", 0))
        self.assertEqual({v for _a, v in self.osc.sent[:-1]}, {1}, "途中は離さない")
        self.assertEqual({a for a, _v in self.osc.sent}, {"/input/MoveForward"})
        count = len(self.osc.sent)
        time.sleep(0.05)
        self.assertEqual(len(self.osc.sent), count, "離した後は送らない")
        self.assertEqual(self.ex._move_holds, {})

    def test_each_direction_has_its_own_address(self):
        for direction, address in (("forward", "/input/MoveForward"), ("left", "/input/MoveLeft"),
                                   ("right", "/input/MoveRight")):
            self.ex.move_key_down(direction)
            self.assertTrue(_wait_until(lambda: self._ones(address) >= 1), direction)
            self.ex.move_key_up(direction)
            self.assertEqual(self.osc.sent[-1], (address, 0))

    def test_a_repeated_down_does_not_start_a_second_hold(self):
        self.assertFalse(self.ex.move_key_down("left"))
        self.assertTrue(self.ex.move_key_down("left"), "すでに押している（押しっぱなしの繰り返し）")
        self.assertEqual(len(self.ex._move_holds), 1)
        self.ex.move_key_up("left")
        self.assertEqual(self.osc.sent.count(("/input/MoveLeft", 0)), 1)

    def test_two_directions_can_be_held_at_once(self):
        self.ex.move_key_down("forward")
        self.ex.move_key_down("left")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveForward") >= 1
                                    and self._ones("/input/MoveLeft") >= 1))
        self.ex.move_key_up("forward")
        self.assertIn(("/input/MoveForward", 0), self.osc.sent)
        self.assertNotIn(("/input/MoveLeft", 0), self.osc.sent, "離していない方は押したまま")
        before = self._ones("/input/MoveLeft")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveLeft") > before), "もう一方は送り続ける")
        self.ex.move_key_up("left")
        self.assertIn(("/input/MoveLeft", 0), self.osc.sent)

    def test_it_sends_in_every_instance_type(self):
        """人が押す操作なので、private でなくても送る（can_operate は見ない）"""
        for itype in NOT_PRIVATE:
            with self.subTest(instance_type=itype):
                ex = _executor(self, instance_type=itype)
                self.assertFalse(ex.can_operate(), "private ではない（それでも送る）")
                ex.move_key_down("forward")
                self.assertTrue(_wait_until(lambda: ("/input/MoveForward", 1) in ex._osc.sent))
                ex.move_key_up("forward")
                self.assertEqual(ex._osc.sent[-1], ("/input/MoveForward", 0))

    def test_it_sends_outside_a_round_too(self):
        self.assertFalse(self.ex._st.in_round)
        self.ex.move_key_down("right")
        self.assertTrue(_wait_until(lambda: ("/input/MoveRight", 1) in self.osc.sent))

    def test_release_all_lets_go_of_everything(self):
        self.ex.move_key_down("forward")
        self.ex.move_key_down("right")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveForward") >= 1
                                    and self._ones("/input/MoveRight") >= 1))
        threads = [held[1] for held in self.ex._move_holds.values()]
        self.assertTrue(self.ex.move_keys_release_all())
        self.assertIn(("/input/MoveForward", 0), self.osc.sent)
        self.assertIn(("/input/MoveRight", 0), self.osc.sent)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(self.ex._move_holds, {})
        self.assertFalse(self.ex.move_keys_release_all(), "何も押していなければ False")

    def test_up_without_down_does_nothing(self):
        self.ex.move_key_up("forward")
        self.assertEqual(self.osc.sent, [])

    def test_press_and_release_go_to_debug_log_not_to_the_screen(self):
        with patch.object(DebugLog, "write") as write:
            self.ex.move_key_down("forward")
            self.ex.move_key_up("forward")
            self.ex.move_key_down("left")
            self.ex.move_keys_release_all()
        lines = [c.args[0] for c in write.call_args_list]
        self.assertTrue(any("移動キー 押す forward" in m for m in lines), lines)
        self.assertTrue(any("移動キー 離す forward" in m for m in lines), lines)
        self.assertTrue(any("移動キー 離す left" in m for m in lines), lines)
        self.assertEqual(self.logs, [], "画面のログには出さない")

    def test_the_hold_does_not_take_the_global_action_lock(self):
        """_ChaseOsc が送るたびに確かめる（前面を奪わないので鍵は取らない）"""
        self.ex.move_key_down("forward")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveForward") >= 2))
        self.ex.move_key_up("forward")


class TestMoveHoldsWithoutOsc(unittest.TestCase):
    """OSC の無い窓: 背面キー W・A・D の長押し（前面にしない）"""

    def setUp(self):
        p = patch.object(config, "CHASE_RESEND_SEC", 0.01)
        p.start()
        self.addCleanup(p.stop)
        self.logs = []
        self.ex = _executor(self, osc=False)
        self.held = []

    def _hold(self, result=True):
        def hold(hwnd, keys, stop, resend):
            self.held.append((hwnd, list(keys), stop, resend))
            if result:
                stop.wait(2.0)
            return result
        return patch.object(WindowOperator, "hold_keys_background", side_effect=hold)

    def test_each_direction_holds_its_key_with_the_stop_event(self):
        with self._hold():
            for direction, key in (("forward", "w"), ("left", "a"), ("right", "d")):
                self.held.clear()
                self.ex.move_key_down(direction)
                self.assertTrue(_wait_until(lambda: self.held), direction)
                hwnd, keys, stop, resend = self.held[0]
                self.assertEqual((hwnd, keys, resend), (0x100, [key], 0.01))
                self.assertIs(stop, self.ex._move_holds[direction][0])
                thread = self.ex._move_holds[direction][1]
                self.ex.move_key_up(direction)
                self.assertTrue(stop.is_set(), "離すと止める印が立つ")
                self.assertFalse(thread.is_alive())

    def test_it_holds_in_a_public_instance_too(self):
        ex = _executor(self, osc=False, instance_type=config.INSTANCE_PUBLIC)
        with self._hold():
            ex.move_key_down("forward")
            self.assertTrue(_wait_until(lambda: self.held))
            ex.move_key_up("forward")
        self.assertEqual(self.held[0][1], ["w"])

    def test_release_all_stops_the_background_holds(self):
        with self._hold():
            self.ex.move_key_down("forward")
            self.ex.move_key_down("right")
            self.assertTrue(_wait_until(lambda: len(self.held) == 2))
            self.assertTrue(self.ex.move_keys_release_all())
        self.assertTrue(all(h[2].is_set() for h in self.held))
        self.assertEqual(self.ex._move_holds, {})

    def test_a_window_that_cannot_receive_warns_once_per_press(self):
        with self._hold(result=False):
            self.ex.move_key_down("forward")
            self.assertTrue(_wait_until(lambda: self.ex._move_holds == {}), "送れなければ項目を消す")
            self.assertTrue(_wait_until(lambda: self.logs))
            self.ex.move_key_down("forward")                # 押している間に何度も試されても
            self.assertTrue(_wait_until(lambda: len(self.held) == 2 and self.ex._move_holds == {}))
            self.ex.move_key_down("forward")
            self.assertTrue(_wait_until(lambda: len(self.held) == 3 and self.ex._move_holds == {}))
            self.assertEqual(self.logs, ["⚠ 移動キーを送れません（最小化中など）"], "⚠ は 1 回だけ")
            self.ex.move_key_up("forward")                  # 離す
            self.ex.move_key_down("forward")                # 押し直すとまた 1 回
            self.assertTrue(_wait_until(lambda: len(self.logs) == 2))
            self.ex.move_key_up("forward")
        self.assertEqual(self.logs, ["⚠ 移動キーを送れません（最小化中など）"] * 2)

    def test_the_warning_is_per_direction(self):
        with self._hold(result=False):
            self.ex.move_key_down("forward")
            self.assertTrue(_wait_until(lambda: len(self.logs) == 1))
            self.ex.move_key_down("left")
            self.assertTrue(_wait_until(lambda: len(self.logs) == 2))


class TestJump(unittest.TestCase):
    def setUp(self):
        self.logs = []

    def test_osc_presses_jump_for_the_configured_time(self):
        ex = _executor(self)
        ex._osc.press = MagicMock(return_value=True)
        self.assertTrue(ex.jump())
        self.assertTrue(_wait_until(lambda: ex._osc.press.called))
        args, kwargs = ex._osc.press.call_args
        self.assertEqual(args, ("/input/Jump", config.JUMP_PRESS_SEC))
        self.assertEqual(kwargs["stop"], ex._stopped, "マクロの停止で押し終える")
        self.assertEqual(self.logs, [])

    def test_a_window_without_osc_holds_space_in_the_background(self):
        ex = _executor(self, osc=False)
        with patch.object(WindowOperator, "hold_key_background", return_value=True) as hold:
            ex.jump()
            self.assertTrue(_wait_until(lambda: hold.called))
        args, kwargs = hold.call_args
        self.assertEqual(args, (0x100, "space", config.JUMP_PRESS_SEC))
        self.assertEqual(kwargs["stop"], ex._stopped)

    def test_it_jumps_in_every_instance_type(self):
        for itype in NOT_PRIVATE:
            with self.subTest(instance_type=itype):
                ex = _executor(self, instance_type=itype)
                ex._osc.press = MagicMock(return_value=True)
                ex.jump()
                self.assertTrue(_wait_until(lambda: ex._osc.press.called))

    def test_a_jump_that_cannot_be_sent_warns_each_press(self):
        ex = _executor(self, osc=False)
        with patch.object(WindowOperator, "hold_key_background", return_value=False) as hold:
            ex.jump()
            self.assertTrue(_wait_until(lambda: len(self.logs) == 1))
            ex.jump()
            self.assertTrue(_wait_until(lambda: len(self.logs) == 2))
        self.assertEqual(self.logs, ["⚠ ジャンプを送れません（最小化中など）"] * 2)

    def test_an_osc_send_failure_warns_too(self):
        ex = _executor(self)
        ex._osc.press = MagicMock(return_value=False)
        ex.jump()
        self.assertTrue(_wait_until(lambda: self.logs))
        self.assertEqual(self.logs, ["⚠ ジャンプを送れません（最小化中など）"])

    def test_the_jump_is_traced_in_the_debug_log(self):
        ex = _executor(self)
        ex._osc.press = MagicMock(return_value=True)
        with patch.object(DebugLog, "write") as write:
            ex.jump()
        self.assertTrue(any("ジャンプ" in c.args[0] for c in write.call_args_list))

    def test_it_does_not_block_the_caller(self):
        """押している間も呼び出し元（keyboard のスレッド・Tk）を塞がない"""
        ex = _executor(self)
        gate = threading.Event()
        ex._osc.press = MagicMock(side_effect=lambda *a, **k: gate.wait(2.0) or True)
        self.addCleanup(gate.set)
        began = time.time()
        ex.jump()
        self.assertLess(time.time() - began, 0.5)
        gate.set()


class TestLogMonitor(unittest.TestCase):
    def setUp(self):
        p = patch.object(config, "CHASE_RESEND_SEC", 0.01)
        p.start()
        self.addCleanup(p.stop)
        self.logs = []
        self.monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x100, osc_port=9000), {},
                                             lambda _m: None, window_idx=2)
        self.monitor.logger = self.logs.append
        self.osc = _ChaseOsc(self)
        self.monitor._action._osc = self.osc
        self.addCleanup(self.monitor._action.move_keys_release_all)

    def test_stop_releases_the_move_keys(self):
        self.monitor._action = MagicMock()
        self.monitor._action.chase_stop.return_value = False
        self.monitor.stop()
        self.monitor._action.move_keys_release_all.assert_called_once_with()

    def test_stop_really_lets_go(self):
        self.monitor.st.instance_type = config.INSTANCE_PUBLIC
        self.monitor.on_move_key("forward", True)
        self.assertTrue(_wait_until(lambda: ("/input/MoveForward", 1) in self.osc.sent))
        self.monitor.stop()
        self.assertEqual(self.osc.sent[-1], ("/input/MoveForward", 0))
        self.assertEqual(self.monitor._action._move_holds, {})
        self.assertEqual(self.logs, [], "画面のログには出さない")

    def test_on_move_key_goes_to_the_executor(self):
        self.monitor._action = MagicMock()
        self.monitor.on_move_key("left", True)
        self.monitor._action.move_key_down.assert_called_once_with("left")
        self.monitor.on_move_key("left", False)
        self.monitor._action.move_key_up.assert_called_once_with("left")

    def test_on_jump_key_goes_to_the_executor(self):
        self.monitor._action = MagicMock()
        self.monitor.on_jump_key()
        self.monitor._action.jump.assert_called_once_with()

    def test_it_works_in_any_instance_and_outside_a_round(self):
        for itype in NOT_PRIVATE:
            with self.subTest(instance_type=itype):
                self.osc.sent.clear()
                self.monitor.st.instance_type = itype
                self.monitor.st.in_round = False
                self.monitor.on_move_key("right", True)
                self.assertTrue(_wait_until(lambda: ("/input/MoveRight", 1) in self.osc.sent))
                self.monitor.on_move_key("right", False)
        self.assertEqual(self.logs, [])


class TestKeyScreens(_ui_tidy._AppCase):
    """キーバインドの画面・メインの 1 行・重なりの確認・保存と読み込み（本物の App。キーのフックは偽物）"""

    def setUp(self):
        super().setUp()
        for target in TARGETS:
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
        self.addCleanup(lambda: [self.app._move_key_var(t).set("") for t in TARGETS])
        self.addCleanup(self.app._refresh_move_key_labels)

    def _capture(self, target, key):
        self._dialog()
        with patch.object(HotKey, "capture", return_value=key):
            getattr(self.app, mainGUI.App.KEY_BUTTONS[target]["capture"]).invoke()

    def _clear(self, target):
        self._dialog()
        getattr(self.app, mainGUI.App.KEY_BUTTONS[target]["clear"]).invoke()

    def _moves(self):
        return [self.app._move_key_var(t).get() for t in TARGETS]

    # ── 画面 ──────────────────────────────
    def test_the_dialog_has_four_rows_below_the_chase_rows(self):
        self._dialog()
        grid = self.app.lbl_start_key.master
        labels = sorted(((int(w.grid_info()["row"]), int(w.grid_info()["column"])), w.cget("text"))
                        for w in grid.grid_slaves() if isinstance(w, ttk.Label))
        self.assertEqual(labels[4:], [((4, 0), "チェイス（反時計回り）: F2"),
                                      ((5, 0), "移動（前進）: 未設定"),
                                      ((6, 0), "移動（左）: 未設定"),
                                      ((7, 0), "移動（右）: 未設定"),
                                      ((8, 0), "ジャンプ: 未設定")])
        for target in TARGETS:
            for kind in ("capture", "clear"):
                self.assertTrue(hasattr(self.app, mainGUI.App.KEY_BUTTONS[target][kind]), (target, kind))

    def test_the_summary_wording(self):
        self.assertEqual(self.app._move_keys_summary(), "移動: 未設定")
        for target, key in zip(TARGETS, ("f3", "f4", "f5", "f6")):
            self.app._move_key_var(target).set(key)
        self.assertEqual(self.app._move_keys_summary(), "移動: 前進 F3・左 F4・右 F5・ジャンプ F6")
        self.app.v_move_forward_key.set("")
        self.app.v_move_right_key.set("")
        self.assertEqual(self.app._move_keys_summary(), "移動: 左 F4・ジャンプ F6", "設定済みだけ並べる")
        self.app.v_move_left_key.set("")
        self.assertEqual(self.app._move_keys_summary(), "移動: ジャンプ F6")

    def test_the_main_line_ends_with_the_move_summary(self):
        self.app._refresh_key_summary()
        self.assertTrue(self.app.lbl_keys.cget("text").endswith("   チェイス: F1/F2   移動: 未設定"),
                        self.app.lbl_keys.cget("text"))
        self.app.v_move_left_key.set("f4")
        self.app.v_jump_key.set("f6")
        self.app._refresh_move_key_labels()
        self.assertTrue(self.app.lbl_keys.cget("text").endswith("   移動: 左 F4・ジャンプ F6"),
                        self.app.lbl_keys.cget("text"))

    def test_the_row_text(self):
        self.app.v_move_forward_key.set("f3")
        self.assertEqual(self.app._move_key_text("forward"), "移動（前進）: F3")
        self.assertEqual(self.app._move_key_text("jump"), "ジャンプ: 未設定")

    # ── 変更・解除 ──────────────────────────
    def test_a_key_can_be_set_and_is_hooked(self):
        self._capture("forward", "f3")
        self.assertEqual(self._moves(), ["f3", "", "", ""])
        self.hooks["_hook_move_keys"].assert_called_once_with()
        self.assertEqual(self.app.lbl_move_forward_key.cget("text"), "移動（前進）: F3")
        self.assertIn("[移動] 前進を F3 に変更しました", self.app.logs)
        self.assertTrue(self.app.lbl_keys.cget("text").endswith("   移動: 前進 F3"))
        self._capture("jump", "f6")
        self.assertEqual(self.app.lbl_jump_key.cget("text"), "ジャンプ: F6")
        self.assertIn("[移動] ジャンプを F6 に変更しました", self.app.logs)

    def test_a_key_that_could_not_be_read_changes_nothing(self):
        self.app.v_move_left_key.set("f4")
        self._capture("left", None)
        self.assertEqual(self.app.v_move_left_key.get(), "f4")
        self.hooks["_hook_move_keys"].assert_not_called()
        self.assertTrue(any("[移動] ⚠ 左のキーを取れませんでした" in m for m in self.app.logs), self.app.logs)

    def test_a_key_can_be_cleared(self):
        self.app.v_move_right_key.set("f5")
        self._clear("right")
        self.assertEqual(self.app.v_move_right_key.get(), "")
        self.hooks["_hook_move_keys"].assert_called_once_with()
        self.assertIn("[移動] 右のキーを解除しました", self.app.logs)
        self.assertEqual(self.app.lbl_move_right_key.cget("text"), "移動（右）: 未設定")
        self.hooks["_hook_move_keys"].reset_mock()
        self._clear("right")
        self.assertIn("[移動] すでに未設定です", self.app.logs)
        self.hooks["_hook_move_keys"].assert_not_called()

    # ── 重なり ──────────────────────────
    def test_a_move_key_cannot_take_another_keys_key(self):
        self.app.v_start_key.set("f9")
        for key, other in ((config.EMERGENCY_STOP_KEY, "緊急停止"), ("f9", "マクロ開始"),
                           (config.SUICIDE_CANCEL_KEY, "自爆キャンセル"), (config.CHASE_CW_KEY, "チェイス"),
                           (config.CHASE_CCW_KEY, "チェイス")):
            for target in TARGETS:
                with self.subTest(key=key, target=target):
                    self.app.logs.clear()
                    self._capture(target, key)
                    self.assertEqual(self.app._move_key_var(target).get(), "")
                    self.assertTrue(any(f"{other}のキーと同じ" in m for m in self.app.logs), self.app.logs)
        self.hooks["_hook_move_keys"].assert_not_called()

    def test_move_keys_cannot_share_a_key(self):
        self._capture("forward", "f3")
        self.app.logs.clear()
        self._capture("left", "f3")
        self.assertEqual(self._moves(), ["f3", "", "", ""])
        self.assertTrue(any("移動（前進）のキーと同じ" in m for m in self.app.logs), self.app.logs)
        self._capture("jump", "f3")
        self.assertEqual(self.app.v_jump_key.get(), "")
        self._capture("forward", "f3")              # 自分と同じキーに戻すのは重なりではない
        self.assertEqual(self.app.v_move_forward_key.get(), "f3")

    def test_the_other_keys_cannot_take_a_move_key(self):
        for target, key in zip(TARGETS, ("f3", "f4", "f5", "f6")):
            self.app._move_key_var(target).set(key)
        names = dict(zip(("f3", "f4", "f5", "f6"), ("移動（前進）", "移動（左）", "移動（右）", "ジャンプ")))
        for dialog_target, var in (("stop", self.app.v_emergency_key), ("start", self.app.v_start_key),
                                   ("cancel", self.app.v_suicide_cancel_key), ("cw", self.app.v_chase_cw_key),
                                   ("ccw", self.app.v_chase_ccw_key)):
            for key, name in names.items():
                with self.subTest(target=dialog_target, key=key):
                    before = var.get()
                    self.app.logs.clear()
                    self._capture(dialog_target, key)
                    self.assertEqual(var.get(), before)
                    self.assertTrue(any(f"{name}のキーと同じ" in m for m in self.app.logs), self.app.logs)

    def test_with_chase_off_f1_can_be_a_move_key(self):
        self._capture("forward", config.CHASE_CW_KEY)
        self.assertEqual(self.app.v_move_forward_key.get(), "", "チェイスを使うなら重なる")
        SharedState.set_chase_enabled(False)
        self._capture("forward", config.CHASE_CW_KEY)
        self.assertEqual(self.app.v_move_forward_key.get(), config.CHASE_CW_KEY)

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

    def test_the_four_keys_round_trip(self):
        for target, key in zip(TARGETS, ("f3", "f4", "f5", "f6")):
            self.app._move_key_var(target).set(key)
        stored = self._saved()
        self.assertEqual([stored[n] for n in ("move_forward_key", "move_left_key", "move_right_key", "jump_key")],
                         ["f3", "f4", "f5", "f6"])
        for target in TARGETS:
            self.app._move_key_var(target).set("")
        self._load({n: stored[n] for n in ("move_forward_key", "move_left_key", "move_right_key", "jump_key")})
        self.assertEqual(self._moves(), ["f3", "f4", "f5", "f6"])
        self.assertEqual(self.app.lbl_keys.cget("text").split("   ")[-1], "移動: 前進 F3・左 F4・右 F5・ジャンプ F6")

    def test_an_old_file_gives_unset(self):
        self.app.v_move_forward_key.set("f3")
        self._load({})
        self.assertEqual(self._moves(), ["", "", "", ""])

    def test_broken_values_become_unset(self):
        self._load({"move_forward_key": "zzz", "move_left_key": 5, "move_right_key": "f5", "jump_key": ""})
        self.assertEqual(self._moves(), ["", "", "f5", ""])

    def test_overlapping_values_become_unset(self):
        self._load({"emergency_stop_key": "f3", "start_key": "f4", "chase_cw_key": "f6", "chase_ccw_key": "f7",
                    "move_forward_key": "f3", "move_left_key": "f4", "move_right_key": "f6", "jump_key": "f7"})
        self.assertEqual(self._moves(), ["", "", "", ""], "停止・マクロ開始・チェイスと重なれば未設定")
        self.assertEqual((self.app.v_emergency_key.get(), self.app.v_start_key.get()), ("f3", "f4"))
        self._load({"move_forward_key": "f3", "move_left_key": "f3", "move_right_key": "f5", "jump_key": "f5"})
        self.assertEqual(self._moves(), ["f3", "", "f5", ""], "移動どうしが重なれば後の方を未設定")

    def test_a_cancel_key_that_overlaps_a_move_key_falls_back(self):
        self._load({"move_forward_key": "f3", "suicide_cancel_key": "f3"})
        self.assertEqual(self.app.v_move_forward_key.get(), "f3")
        self.assertEqual(self.app.v_suicide_cancel_key.get(), config.SUICIDE_CANCEL_KEY, "自爆キャンセルを既定へ")


class TestStaleKeys(unittest.TestCase):
    """「半角/全角」を外す既存の仕組みが、移動のキーにも効く"""

    def _load(self, data):
        saves = []
        original = mainGUI.App._report_stale_keys

        def report(app, fields):
            app._schedule_settings_save = lambda: saves.append(1)
            original(app, fields)
        with patch.object(mainGUI.App, "_report_stale_keys", report):
            app = _item_fetch_tests.TestStartKeySettings("test_it_is_saved")._load(data)
        app.saves = saves
        return app

    def test_the_stale_word_is_removed_from_every_move_key(self):
        app = self._load({"move_forward_key": "f3+半角/全角", "move_left_key": "f4+半角/全角",
                          "move_right_key": "f5+半角/全角", "jump_key": "f6+半角/全角"})
        self.assertEqual([app._move_key_var(t).get() for t in TARGETS], ["f3", "f4", "f5", "f6"])
        self.assertEqual([m for m in app.logs if "[キー]" in m], [
            "[キー] 移動（前進）のキーから「半角/全角」を外しました（F3）",
            "[キー] 移動（左）のキーから「半角/全角」を外しました（F4）",
            "[キー] 移動（右）のキーから「半角/全角」を外しました（F5）",
            "[キー] ジャンプのキーから「半角/全角」を外しました（F6）"])
        self.assertEqual(app.saves, [1], "まとめて 1 回保存")

    def test_only_the_stale_word_means_unset(self):
        app = self._load({"jump_key": "半角/全角"})
        self.assertEqual(app.v_jump_key.get(), "")
        self.assertIn("[キー] ジャンプのキーから「半角/全角」を外しました（未設定）", app.logs)


class TestAppClose(unittest.TestCase):
    def test_closing_unhooks_the_move_keys(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _on_close(self):"):]
        body = body[:body.index("self.destroy()")]
        self.assertIn("self._unhook_move_keys()", body)

    def test_the_start_hooks_the_move_keys(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _start_emergency_stop_polling(self):"):]
        body = body[:body.index("    # ──")]
        self.assertIn("self._hook_move_keys()", body)

    def test_the_readme_lists_the_four_keys(self):
        readme = (Path(mainGUI.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        for name in ("前進", "左", "右", "ジャンプ"):
            self.assertRegex(readme, rf"\| {name} \| 未設定 \|")
