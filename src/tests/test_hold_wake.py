"""長押しを「合図（threading.Event）」で起こす（依頼者 2026-11-17）。

自爆・ジャンプ・移動の長押しは、一定間隔で stop() を聞きに行く作りだった。聞きに行くのが唯一の引き金なので、
最大で 1 間隔ぶん、離すのが遅れた。合図で起こしてもらう形に変える。ただし OSC の `1` の送り直し
（OSC_HOLD_RESEND_SEC の間隔・回数）は変えない。

偽物だけを使う: OSC の送信（send）・キー入力（user32）・時計。本物の入力・窓・ネットワークは触らない。
"""
import inspect
import types

from tests.support import *  # noqa: F401,F403
import RunFace


def _fake_clock():
    """time の代わり。sleep は時計を進めるだけ（眠らない）"""
    now = [1000.0]
    fake = types.SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0],
        sleep=lambda sec: now.__setitem__(0, now[0] + sec))
    return fake, now


def _run_in_thread(target):
    """別スレッドで走らせる。終わった時刻（time.monotonic）を done[0] に残す"""
    done = []

    def body():
        target()
        done.append(time.monotonic())
    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread, done


class TestKeyHoldWake(unittest.TestCase):
    """WindowOperator._wait_holding（OSC が無い窓の長押し）"""

    def test_the_key_hold_wakes_on_the_signal(self):
        wake = threading.Event()
        thread, done = _run_in_thread(lambda: WindowOperator._wait_holding(10.0, wake=wake))
        time.sleep(0.1)
        self.assertFalse(done, "10 秒の長押しは合図が無ければ続く")

        set_at = time.monotonic()
        wake.set()
        thread.join(2.0)

        self.assertFalse(thread.is_alive())
        self.assertLessEqual(done[0] - set_at, 0.05, "set() から 0.05 秒以内に返る")

    def test_a_signal_that_is_never_set_ends_at_the_time(self):
        """合図を立て忘れても、指定の秒数ぶん押し切って普通に終わる（固まらない）"""
        wake = threading.Event()
        started = time.monotonic()
        WindowOperator._wait_holding(0.2, wake=wake)
        self.assertGreaterEqual(time.monotonic() - started, 0.19)

    def test_the_key_hold_without_a_signal_still_polls(self):
        """stop だけ（合図を渡さない古い呼び出し）は、今まで通り HOLD_STOP_POLL_SEC ごとに stop() を見る"""
        fake, now = _fake_clock()
        calls = []

        def stop():
            calls.append(now[0])
            return len(calls) >= 4

        with patch.object(WindowOperator, "time", fake):
            WindowOperator._wait_holding(10.0, stop)

        self.assertEqual(len(calls), 4)
        gaps = [round(b - a, 3) for a, b in zip(calls, calls[1:])]
        self.assertEqual(gaps, [WindowOperator.HOLD_STOP_POLL_SEC] * 3)

    def test_neither_a_stop_nor_a_signal_just_sleeps(self):
        fake, now = _fake_clock()
        with patch.object(WindowOperator, "time", fake):
            WindowOperator._wait_holding(3.0)
        self.assertEqual(now[0], 1003.0)

    def test_the_key_hold_with_both_wakes_and_rechecks(self):
        """ジャンプ・移動の形。条件は stop、wake は目覚まし。set() の後に stop() を見直して抜ける"""
        wake = threading.Event()
        state = {"stop": False}
        calls = []

        def stop():
            calls.append(time.monotonic())
            return state["stop"]

        thread, done = _run_in_thread(lambda: WindowOperator._wait_holding(10.0, stop, wake))
        time.sleep(0.1)
        self.assertFalse(done)
        self.assertEqual(len(calls), 1, "起きていない間は stop() を聞きに行かない")

        state["stop"] = True
        set_at = time.monotonic()
        wake.set()
        thread.join(2.0)

        self.assertFalse(thread.is_alive())
        self.assertLessEqual(done[0] - set_at, 0.05)
        self.assertEqual(len(calls), 2, "起きた後に stop() を見直した")

    def test_the_key_hold_passes_the_signal_down_and_still_releases(self):
        """hold_key_background → _hold_key_background → _wait_holding へ wake が届く。押す・離すは変わらない"""
        wake = threading.Event()
        posted, waited = [], []
        user32 = MagicMock()
        user32.IsIconic.return_value = False
        user32.GetKeyboardState.return_value = 0
        user32.PostMessageW.side_effect = lambda h, msg, vk, lp: posted.append(msg)
        with patch.object(WindowOperator, "_background_key", return_value=(1, 0xDE, 0, 0)), \
             patch.object(WindowOperator, "user32", user32), \
             patch.object(WindowOperator, "kernel32", MagicMock()), \
             patch.object(WindowOperator, "_wait_holding",
                          side_effect=lambda *a, **k: waited.append((a, k))):
            ok = WindowOperator.hold_key_background(0x10, "^", 3.0, wake=wake)

        self.assertTrue(ok)
        self.assertEqual(posted, [WindowOperator.WM_KEYDOWN, WindowOperator.WM_KEYUP])
        self.assertEqual(waited, [((3.0, None, wake), {})])


class TestOscHoldWake(unittest.TestCase):
    """OSCClient._hold_and_release（OSC がある窓の長押し）"""

    def _client(self):
        client = OSCClient.OSCClient(9000)
        self.addCleanup(client.close)
        return client

    def test_the_osc_hold_wakes_on_the_signal(self):
        client = self._client()
        wake = threading.Event()
        state = {"stop": False}
        sent = []        # (時刻, アドレス, 値)
        with patch.object(client, "send",
                          side_effect=lambda a, v: sent.append((time.monotonic(), a, v)) or True):
            end = time.time() + 10.0
            thread, done = _run_in_thread(
                lambda: client._hold_and_release([("/input/Jump", end)],
                                                 stop=lambda: state["stop"], wake=wake))
            time.sleep(0.25)
            self.assertFalse(done, "10 秒の長押しは続く")
            self.assertTrue(sent and all(v == 1 for _t, _a, v in sent), "押している間は 1 を送り直す")

            state["stop"] = True
            set_at = time.monotonic()
            wake.set()
            thread.join(2.0)

        self.assertFalse(thread.is_alive())
        released = [t for t, _a, v in sent if v == 0]
        self.assertEqual(len(released), 1)
        self.assertLessEqual(released[0] - set_at, 0.05, "set() から 0.05 秒以内に 0（離す）を送る")

    def _press(self, wake):
        """偽の時計と偽の送信で press を回す。(送ったもの, 待ちの秒数) を返す"""
        client = self._client()
        sent, waits, now = [], [], [1000.0]

        def sleep(sec):
            now[0] += sec

        def wait(timeout=None):
            waits.append(timeout)
            sleep(timeout)
            return False

        fake_wake = None if wake is None else types.SimpleNamespace(wait=wait)
        with patch.object(client, "send", side_effect=lambda a, v: sent.append(
                              (round(now[0] - 1000.0, 3), a.split("/")[-1], v)) or True), \
             patch.object(OSCClient.time, "time", side_effect=lambda: now[0]), \
             patch.object(OSCClient.time, "sleep", side_effect=sleep):
            self.assertTrue(client.press("/input/MoveForward", 2.08, wake=fake_wake))
        return sent, waits

    def test_the_osc_resend_is_unchanged(self):
        """合図が来なければ、送り直し（1）の回数・間隔は、合図を渡さない今までの呼び方と同じ"""
        without, _ = self._press(None)
        with_wake, waits = self._press(True)

        self.assertEqual(with_wake, without)
        resends = [t for t, _a, v in with_wake[2:-1]]
        self.assertEqual(resends, [round(0.1 * k, 3) for k in range(1, 21)], "0.1 秒ごと 20 回")
        self.assertEqual(with_wake[-1], (2.08, "MoveForward", 0), "離す時刻も変わらない")
        self.assertEqual(len(waits), 21)
        self.assertTrue(all(w <= config.OSC_HOLD_RESEND_SEC + 1e-9 for w in waits),
                        "待つのは最大でも OSC_HOLD_RESEND_SEC")

    def test_press_multi_passes_the_signal_too(self):
        client = self._client()
        wake = threading.Event()
        with patch.object(client, "send", return_value=True), \
             patch.object(client, "_hold_and_release", return_value=True) as hold:
            client.press_multi([("/input/MoveForward", 1.0)], stop=None, wake=wake)
        self.assertIs(hold.call_args.args[2], wake)

    def test_a_call_without_a_signal_still_sleeps(self):
        """渡さない呼び出し（チェイスの press_multi など）は time.sleep の枝のまま"""
        client = self._client()
        sleeps = []
        now = [1000.0]
        with patch.object(client, "send", return_value=True), \
             patch.object(OSCClient.time, "time", side_effect=lambda: now[0]), \
             patch.object(OSCClient.time, "sleep",
                          side_effect=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))):
            client.press_multi([("/input/MoveForward", 0.35)])
        self.assertTrue(sleeps)
        self.assertTrue(all(s <= config.OSC_HOLD_RESEND_SEC + 1e-9 for s in sleeps))


class TestCallers(unittest.TestCase):
    """ジャンプ・移動は stop と wake の両方を渡す。press_axis は触らない"""

    def _executor(self, osc_port, **state):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, **state)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def test_jump_passes_stop_and_wake(self):
        ex = self._executor(9000)
        with patch.object(ex._osc, "press", return_value=True) as press:
            ex._run_jump()
        press.assert_called_once_with("/input/Jump", config.JUMP_PRESS_SEC,
                                      stop=ex._stopped, wake=ex._stop_wake)

        ex = self._executor(0)
        with patch.object(WindowOperator, "hold_key_background", return_value=True) as hold:
            ex._run_jump()
        hold.assert_called_once_with(123, " ", config.JUMP_PRESS_SEC,
                                     stop=ex._stopped, wake=ex._stop_wake)

    def test_move_passes_stop_and_wake(self):
        ex = self._executor(9000)
        with patch.object(ex._osc, "press", return_value=True) as press, \
             patch.object(ex._osc, "stop_all"):
            ex.move("left", 1.5)
        press.assert_called_once_with("/input/MoveLeft", 1.5,
                                      stop=ex._move_stopped, wake=ex._move_wake)

        ex = self._executor(0)
        with patch.object(WindowOperator, "hold_key_background", return_value=True) as hold:
            ex.move("right", 1.5)
        hold.assert_called_once_with(123, "d", 1.5, stop=ex._move_stopped, wake=ex._move_wake)

    def test_the_three_signals_are_separate(self):
        ex = self._executor(9000)
        self.assertIsNot(ex._stop_wake, ex._move_wake)
        self.assertIsNot(ex._skip_wake, ex._move_wake)
        self.assertIsNot(ex._skip_wake, ex._stop_wake)

    def test_press_axis_is_untouched(self):
        self.assertNotIn("wake", inspect.signature(OSCClient.OSCClient.press_axis).parameters)
        self.assertNotIn("wake", inspect.getsource(OSCClient.OSCClient.press_axis))

    def test_nobody_else_passes_a_signal(self):
        """渡すのは自爆・移動・ジャンプだけ。press_multi（チェイスなど）・press_axis（Run の向き直し）は渡さない"""
        for module in (ActionExecutor, RunFace):
            src = inspect.getsource(module)
            for call in re.findall(r"press_multi\([^)]*\)|press_axis\([^)]*\)", src, flags=re.S):
                self.assertNotIn("wake", call, call)

    def test_the_intervals_are_unchanged(self):
        self.assertEqual(config.OSC_HOLD_RESEND_SEC, 0.1)
        self.assertEqual(WindowOperator.HOLD_STOP_POLL_SEC, 0.05)


class TestStateSignals(unittest.TestCase):
    """_stop_wake / _move_wake は条件（_stopped / _move_stopped）の写し"""

    def _executor(self, running, instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(hwnd=123, osc_port=9000)
        st = WindowState(instance_type=instance_type)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: running[0], lambda _m: None)
        return ex, st

    def test_refresh_mirrors_the_conditions(self):
        running = [True]
        ex, st = self._executor(running)

        ex.refresh_hold_blocks()
        self.assertFalse(ex._stop_wake.is_set())
        self.assertFalse(ex._move_wake.is_set())

        running[0] = False
        ex.refresh_hold_blocks()
        self.assertTrue(ex._stop_wake.is_set(), "ツール停止でジャンプの合図が立つ")
        self.assertTrue(ex._move_wake.is_set(), "ツール停止で移動の合図が立つ")

        running[0] = True
        ex.refresh_hold_blocks()
        self.assertFalse(ex._stop_wake.is_set(), "写しなので下ろす")
        self.assertFalse(ex._move_wake.is_set())

    def test_the_move_signal_is_lowered_when_private_comes_back(self):
        running = [True]
        ex, st = self._executor(running, instance_type=config.INSTANCE_PUBLIC)

        ex.refresh_hold_blocks()
        self.assertTrue(ex._move_wake.is_set(), "private でない間は移動を押さない")
        self.assertFalse(ex._stop_wake.is_set(), "ジャンプは private を見ない")

        st.instance_type = config.INSTANCE_PRIVATE
        ex.refresh_hold_blocks()
        self.assertFalse(ex._move_wake.is_set(), "private に戻ったら下ろす（立てっぱなしにしない）")

    def test_wake_skip_hold_only_touches_the_skip_signal(self):
        ex, _st = self._executor([True])
        ex.wake_skip_hold()
        self.assertTrue(ex._skip_wake.is_set())
        self.assertFalse(ex._stop_wake.is_set())
        self.assertFalse(ex._move_wake.is_set())

    def test_the_waiter_never_clears_the_state_signals(self):
        """待っている側が合図を下ろすと、同じ窓で 2 本同時に走る移動の片方が取りこぼす"""
        for func in (WindowOperator._wait_holding, OSCClient.OSCClient._hold_and_release):
            self.assertNotIn(".clear(", inspect.getsource(func), func.__name__)

    def test_only_refresh_lowers_the_state_signals(self):
        src = inspect.getsource(ActionExecutor)
        self.assertNotIn("_stop_wake.clear", src)
        self.assertNotIn("_move_wake.clear", src)
        self.assertEqual(src.count("event.clear()"), 1, "下ろすのは refresh_hold_blocks の 1 か所だけ")


class TestLogMonitorRefreshes(unittest.TestCase):
    """条件が変わりうる所から refresh_hold_blocks を呼ぶ"""

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=9), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        for name in ("start_velocity_receiver", "stop_velocity_receiver"):
            patcher = patch.object(monitor._action, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        for p in (patch.object(monitor, "_start_daemon", return_value=MagicMock()),
                  patch.object(monitor, "_flush_item_use_log"),
                  patch.object(ItemUse, "register"), patch.object(ItemUse, "unregister")):
            p.start()
            self.addCleanup(p.stop)
        return monitor

    def test_stop_raises_the_signals_and_start_lowers_them(self):
        monitor = self._monitor()
        ex = monitor._action
        monitor._running = True

        monitor.stop()
        self.assertTrue(ex._skip_wake.is_set())
        self.assertTrue(ex._stop_wake.is_set())
        self.assertTrue(ex._move_wake.is_set())

    def test_start_lowers_the_signals_left_by_the_last_stop(self):
        monitor = self._monitor()
        ex = monitor._action
        monitor._running = True
        monitor.stop()

        monitor.start()

        self.assertTrue(monitor._running)
        self.assertFalse(ex._stop_wake.is_set(), "前回の停止で立った合図を下ろす")
        self.assertFalse(ex._move_wake.is_set())

    def test_entering_a_world_refreshes_the_move_signal(self):
        monitor = self._monitor()
        monitor._running = True
        ex = monitor._action
        monitor.st.in_ton_world = False
        ex._move_wake.set()                        # ToN の外にいた間の写し

        monitor._enter_world(config.TON_WORLD_ID)
        self.assertTrue(monitor.st.in_ton_world)
        self.assertFalse(ex._move_wake.is_set(), "ToN に入って private なら下ろす")

        monitor._enter_world("wrld_00000000-0000-0000-0000-000000000000")
        self.assertTrue(ex._move_wake.is_set(), "ToN 以外のワールドでは移動を押さない")


if __name__ == "__main__":
    unittest.main()
