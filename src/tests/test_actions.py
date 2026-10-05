"""窓の操作（ActionExecutor・WindowOperator・OSC）"""
from tests.support import *  # noqa: F401,F403



# ═══════════════════════════════════════════════
#  WindowOperator.py
# ═══════════════════════════════════════════════
class TestFocusWindow(unittest.TestCase):
    """前面化は成否を返す。失敗を握り潰すと別の窓へ入力が飛ぶため。"""

    def test_hwnd_zero_returns_false(self):
        with patch('win32gui.SetForegroundWindow') as mock:
            self.assertFalse(WindowOperator.focus_window(0))
            mock.assert_not_called()

    def test_returns_true_without_raising_when_already_foreground(self):
        """既に前面ならSetForegroundWindowを呼ばずTrue"""
        with patch('win32gui.GetForegroundWindow', return_value=123), \
             patch('win32gui.IsIconic', return_value=False), \
             patch('win32gui.SetForegroundWindow') as mock:
            self.assertTrue(WindowOperator.focus_window(123))
            mock.assert_not_called()

    def test_returns_true_when_focus_is_taken(self):
        with patch('win32gui.IsIconic', return_value=False), \
             patch('win32gui.GetForegroundWindow', side_effect=[0, 0, 123, 123]), \
             patch('win32gui.BringWindowToTop'), \
             patch('win32gui.SetForegroundWindow'), \
             patch.object(WindowOperator.time, "sleep"):
            self.assertTrue(WindowOperator.focus_window(123))

    def test_retries_then_returns_false_when_focus_refused(self):
        """Windowsに前面化を拒否され続けたらFalse（呼び出し側が中止できる）"""
        with patch('win32gui.IsIconic', return_value=False), \
             patch('win32gui.GetForegroundWindow', return_value=999), \
             patch('win32gui.BringWindowToTop'), \
             patch('win32gui.SetForegroundWindow') as mock, \
             patch.object(WindowOperator.time, "sleep"):
            self.assertFalse(WindowOperator.focus_window(123))
            self.assertEqual(mock.call_count, config.FOCUS_RETRY_MAX)




class TestLockCursor(unittest.TestCase):
    """VRChat を前に出したとき、カーソルが浮いていたら Tab を押して離して固定する（依頼者の実測）"""

    class _User32:
        def __init__(self, showing):
            self.showing = showing

        def GetCursorInfo(self, ref):
            ref._obj.flags = WindowOperator.CURSOR_SHOWING if self.showing else 0
            return 1

    def _lock(self, showing, front=55):
        with patch.object(WindowOperator, "user32", self._User32(showing)), \
             patch.object(WindowOperator, "foreground_hwnd", return_value=front), \
             patch.object(WindowOperator, "_hold_key") as key:
            pressed = WindowOperator.lock_cursor(55)
        return pressed, key

    def test_a_floating_cursor_is_locked_with_tab(self):
        pressed, key = self._lock(showing=True)
        self.assertTrue(pressed)
        key.assert_called_once_with("tab", config.CURSOR_LOCK_PRESS_SEC)

    def test_a_locked_cursor_is_left_alone(self):
        pressed, key = self._lock(showing=False)
        self.assertFalse(pressed, "押すと外れるキーかもしれないので押さない")
        key.assert_not_called()

    def test_only_when_that_window_is_in_front(self):
        pressed, key = self._lock(showing=True, front=99)
        self.assertFalse(pressed)
        key.assert_not_called()

    def test_off_when_the_key_is_empty(self):
        with patch.object(config, "CURSOR_LOCK_KEY", ""):
            pressed, key = self._lock(showing=True)
        self.assertFalse(pressed)

    def test_borrowing_the_front_locks_it(self):
        with patch.object(WindowOperator, "foreground_hwnd", return_value=7), \
             patch.object(WindowOperator, "cursor_position", return_value=(1, 2)), \
             patch.object(WindowOperator, "focus_window", return_value=True), \
             patch.object(WindowOperator, "lock_cursor") as lock:
            WindowOperator.borrow_front(55)
        lock.assert_called_once_with(55)
        with patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(WindowOperator, "lock_cursor") as lock:
            self.assertFalse(WindowOperator.focus_vrchat(55))
        lock.assert_not_called()


class TestActionExecutorFocusFailure(unittest.TestCase):
    """フォーカスを取れない時は操作を送らない"""

    def setUp(self):
        _no_accept_wait(self)
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()

    def test_do_skip_never_takes_focus(self):
        """自爆は背面送信だけ。送れなくても前面の窓へは押さない"""
        cfg = WindowConfig(hwnd=123)
        st = WindowState(in_round=True)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        with patch.object(WindowOperator, "hold_key_background",
                          return_value=False), \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()
        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertTrue(any("自爆できませんでした" in m for m in logs), logs)



class TestOSCClient(unittest.TestCase):
    """OSC送信（多重起動で窓ごとにポートを分ける）"""

    def test_ports_are_distinct_per_window(self):
        seen = [OSCClient.ports_for_window(i) for i in range(4)]
        self.assertEqual(seen[0], (config.OSC_BASE_IN_PORT, config.OSC_BASE_IN_PORT + 1))
        flat = [p for pair in seen for p in pair]
        self.assertEqual(len(flat), len(set(flat)), "窓ごとにポートが重複してはいけない")

    def test_launch_arg_format(self):
        self.assertEqual(OSCClient.osc_launch_arg(0), "--osc=9000:127.0.0.1:9001")
        self.assertEqual(OSCClient.osc_launch_arg(2), "--osc=9020:127.0.0.1:9021")

    def test_message_is_valid_osc(self):
        """OSCは4バイト境界に揃っている必要がある"""
        for addr, val in [("/input/MoveForward", 1),
                          ("/input/LookHorizontal", 1.0),
                          ("/input/Jump", True)]:
            msg = OSCClient.OSCClient.build_message(addr, val)
            with self.subTest(addr=addr):
                self.assertEqual(len(msg) % 4, 0)
                self.assertTrue(msg.startswith(addr.encode()))

    def test_message_type_tags(self):
        self.assertIn(b",i", OSCClient.OSCClient.build_message("/x", 1))
        self.assertIn(b",f", OSCClient.OSCClient.build_message("/x", 1.0))

    def test_bool_is_sent_as_int_not_bool_tag(self):
        """ブール型タグ(,T/,F)は引数を持たない。VRChatへ送るとメモリ上の
        ゴミを読まれて巨大な値が入力に固定される事故が起きたため禁止。"""
        for value in (True, False):
            msg = OSCClient.OSCClient.build_message("/input/MoveForward", value)
            with self.subTest(value=value):
                self.assertIn(b",i", msg)
                self.assertNotIn(b",T", msg)
                self.assertNotIn(b",F", msg)
                self.assertEqual(len(msg) % 4, 0)

    def test_stop_all_includes_analog_axes(self):
        """Vertical/Horizontal を漏らすと移動が残り続ける"""
        client = OSCClient.OSCClient(9000)
        sent = []
        with patch.object(client, "send", side_effect=lambda a, v: sent.append(a) or True):
            client.stop_all()
        for address in ("/input/Vertical", "/input/Horizontal",
                        "/input/LookHorizontal", "/input/LookVertical",
                        "/input/MoveForward", "/input/MoveRight"):
            with self.subTest(address=address):
                self.assertIn(address, sent)

    def test_press_sends_reset_then_press_then_release(self):
        """0→1→0 で送る。0から1への変化で反応する入力があるため"""
        client = OSCClient.OSCClient(9000)
        sent = []
        now = [1000.0]
        with patch.object(client, "send", side_effect=lambda a, v: sent.append((a, v)) or True), \
             patch.object(OSCClient.time, "time", side_effect=lambda: now[0]), \
             patch.object(OSCClient.time, "sleep", side_effect=lambda s: now.__setitem__(0, now[0] + s)):
            client.press("/input/MoveForward", 0.5)
        # 押している間は 0.1 秒ごとに 1 を送り直す（0.1・0.2・0.3・0.4 の4回）
        self.assertEqual([v for _a, v in sent], [0, 1, 1, 1, 1, 1, 0])

    def test_launch_args_include_osc_when_index_given(self):
        args = VRChatLauncher.build_launch_args(
            Path("C:/launch.exe"), 0, True, None, osc_index=1)
        self.assertIn("--osc=9010:127.0.0.1:9011", args)

    def test_launch_args_omit_osc_when_index_none(self):
        args = VRChatLauncher.build_launch_args(Path("C:/launch.exe"), 0, True, None)
        self.assertFalse(any(a.startswith("--osc=") for a in args))




class TestOscBranching(unittest.TestCase):
    """OSCが使えるかで移動手段と排他の粒度を変える"""

    def _executor(self, osc_port):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState()
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def test_uses_osc_only_when_port_assigned(self):
        self.assertTrue(self._executor(9000).uses_osc)
        self.assertFalse(self._executor(0).uses_osc)

    def test_move_uses_osc_when_available(self):
        """OSCが使える窓はキーを押さない（フォーカスを奪わない）"""
        ex = self._executor(9000)
        with patch.object(ex._osc, "press", return_value=True) as mock_press, \
             patch.object(ex._osc, "stop_all"), \
             patch.object(WindowOperator, "hold_key") as mock_key:
            ex.move("forward", 2.1)
        mock_press.assert_called_once_with("/input/MoveForward", 2.1, stop=ex._stopped)
        mock_key.assert_not_called()

    def test_move_falls_back_to_the_background_key_without_osc(self):
        """OSCが無い窓もフォーカスを奪わない（自爆と同じ背面送信）"""
        ex = self._executor(0)
        with patch.object(WindowOperator, "hold_key_background",
                          return_value=True) as mock_key,              patch.object(WindowOperator, "focus_window") as focus:
            ex.move("forward", 2.1)

        mock_key.assert_called_once_with(ex._cfg.hwnd, "w", 2.1, stop=ex._stopped)
        focus.assert_not_called()

    def test_a_failed_background_key_is_logged(self):
        logs = []
        ex = self._executor(0)
        ex._log = logs.append
        with patch.object(WindowOperator, "hold_key_background", return_value=False):
            ex.move("forward", 1.0)

        self.assertTrue(any("移動キーを送れませんでした" in m for m in logs), logs)


    def test_move_ignores_zero_duration(self):
        ex = self._executor(9000)
        with patch.object(ex._osc, "press") as mock_press:
            ex.move("forward", 0)
        mock_press.assert_not_called()

    def test_osc_move_does_not_hold_the_global_lock(self):
        """OSC移動中は他窓が操作できる（ロックを取らない）"""
        ex = self._executor(9000)
        held = []
        with patch.object(ex._osc, "press",
                          side_effect=lambda a, s, stop=None: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked()) or True), \
             patch.object(ex._osc, "stop_all"):
            ex.move("forward", 1.0)
        self.assertEqual(held, [False], "OSC移動はロックを保持してはいけない")

    def test_a_keyboard_move_does_not_hold_the_global_lock_either(self):
        """背面送信になったので、非OSC窓の移動も他窓を妨げない"""
        ex = self._executor(0)
        held = []
        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda *_a, **_k: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked()) or True):
            ex.move("forward", 1.0)

        self.assertEqual(held, [False], "移動はロックを保持してはいけない")

    def test_the_begin_move_is_outside_the_lock(self):
        """Begin の流れでも、移動の間はロックを持たない（押すところだけ取る）"""
        ex = self._executor(0)
        ex._st.instance_type = config.INSTANCE_PRIVATE
        held = []

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor.time, "sleep"),              patch.object(ex, "_begin_precheck", return_value=True),              patch.object(ex, "_wait_round_end", return_value=False),              patch.object(ex, "move_forward_left",
                          side_effect=lambda *_a: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked())),              patch.object(ex, "move",
                          side_effect=lambda *_a: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked())):
            ex.do_after_round()

        self.assertTrue(held, "移動していない")
        self.assertEqual(set(held), {False})




class TestOscAvailability(unittest.TestCase):
    """OSC可否の判定（起動時に1回だけ確定させる）"""

    def test_available_when_process_holds_expected_port(self):
        with patch.object(OSCClient, "udp_ports_of_process", return_value={9010, 5353}), \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertTrue(OSCClient.osc_available_for(0x1234, 1))

    def test_unavailable_when_port_missing(self):
        """手動起動の2窓目はポート競合でOSCが無効"""
        with patch.object(OSCClient, "udp_ports_of_process", return_value={5353}), \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertFalse(OSCClient.osc_available_for(0x1234, 1))

    def test_unavailable_when_pid_unknown(self):
        with patch("win32process.GetWindowThreadProcessId", return_value=(0, 0)):
            self.assertFalse(OSCClient.osc_available_for(0x1234, 0))




class TestUdpPortsByPid(unittest.TestCase):
    """UDPの待ち受け表は netstat 1回で全PIDぶん取る"""

    NETSTAT = (
        chr(10).join([
            "",
            "アクティブな接続",
            "",
            "  プロトコル  ローカル アドレス      外部アドレス            PID",
            "  UDP         0.0.0.0:9000           *:*                     4321",
            "  UDP         0.0.0.0:9010           *:*                     4321",
            "  UDP         127.0.0.1:5353         *:*                     999",
            "  UDP         [::]:9020              *:*                     555",
            "  TCP         0.0.0.0:80             0.0.0.0:0    LISTENING  111",
        ])
    )

    def _run_result(self, stdout):
        result = MagicMock()
        result.stdout = stdout
        return result

    def test_output_is_split_per_pid(self):
        with patch.object(OSCClient.subprocess, "run",
                          return_value=self._run_result(self.NETSTAT)) as mock_run:
            table = OSCClient.udp_ports_by_pid()

        mock_run.assert_called_once()
        self.assertEqual(table[4321], {9000, 9010})
        self.assertEqual(table[999], {5353})
        self.assertEqual(table[555], {9020}, "IPv6表記も拾うこと")
        self.assertNotIn(111, table, "TCPは含めないこと")

    def test_failure_returns_none(self):
        """「失敗」と「成功したがポート無し」を区別する"""
        with patch.object(OSCClient.subprocess, "run", side_effect=OSError("boom")):
            self.assertIsNone(OSCClient.udp_ports_by_pid())

    def test_of_process_delegates(self):
        with patch.object(OSCClient, "udp_ports_by_pid",
                          return_value={4321: {9000}}):
            self.assertEqual(OSCClient.udp_ports_of_process(4321), {9000})
            self.assertEqual(OSCClient.udp_ports_of_process(9999), set())

    def test_of_process_survives_a_failure(self):
        with patch.object(OSCClient, "udp_ports_by_pid", return_value=None):
            self.assertEqual(OSCClient.udp_ports_of_process(4321), set())

    def test_passing_the_table_does_not_run_netstat(self):
        table = {4321: {9010}}
        with patch.object(OSCClient.subprocess, "run") as mock_run, \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertTrue(OSCClient.osc_available_for(0x1234, 1, table))

        mock_run.assert_not_called()

    def test_missing_pid_in_the_table_is_unavailable_without_falling_back(self):
        """辞書にPIDが無いのは「ポートを持っていない」＝利用不可。撃ち直さない"""
        with patch.object(OSCClient.subprocess, "run") as mock_run, \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertFalse(OSCClient.osc_available_for(0x1234, 1, {999: {9010}}))

        mock_run.assert_not_called()

    def test_none_table_falls_back_per_window(self):
        """netstat失敗時は窓ごとの個別取得へ落ちる（全窓を巻き添えにしない）"""
        with patch.object(OSCClient, "udp_ports_of_process",
                          return_value={9010}) as mock_ports, \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertTrue(OSCClient.osc_available_for(0x1234, 1, None))

        mock_ports.assert_called_once_with(4321)




class TestHoldKeyBackground(unittest.TestCase):
    """自爆キーをフォーカス無しで送る（送り切れたらTrue）"""

    VK = 0xDE       # ^ (JIS)
    SCAN = 0x0D

    def _user32(self, **over):
        """正常系の user32 モック。over で個別の戻り値を差し替える"""
        u = MagicMock()
        u.GetWindowThreadProcessId.return_value = 4321
        u.IsIconic.return_value = 0
        u.GetKeyboardLayout.return_value = 0x04110411
        u.VkKeyScanExW.return_value = self.VK          # シフト状態 0
        u.MapVirtualKeyExW.return_value = self.SCAN
        u.AttachThreadInput.return_value = 1
        u.GetKeyboardState.return_value = 1
        for name, value in over.items():
            getattr(u, name).return_value = value
        return u

    def _send(self, u, key="^", sec=0.0, hwnd=0x1234, kernel=None):
        k = kernel or MagicMock()
        if kernel is None:
            k.GetCurrentThreadId.return_value = 99
        with patch.object(WindowOperator, "user32", u), \
             patch.object(WindowOperator, "kernel32", k), \
             patch.object(WindowOperator.time, "sleep"):
            return WindowOperator.hold_key_background(hwnd, key, sec or 3.0)

    def test_unmappable_key_is_refused(self):
        u = self._user32(VkKeyScanExW=-1)

        self.assertFalse(self._send(u))
        u.PostMessageW.assert_not_called()

    def test_shifted_key_is_refused(self):
        """Shift併用キーは対象外。黙って別のキーを送らない"""
        u = self._user32(VkKeyScanExW=(1 << 8) | self.VK)

        self.assertFalse(self._send(u))
        u.PostMessageW.assert_not_called()

    def test_unmappable_scan_code_is_refused(self):
        u = self._user32(MapVirtualKeyExW=0)

        self.assertFalse(self._send(u))
        u.PostMessageW.assert_not_called()

    def test_minimized_window_is_refused(self):
        """最小化中は送れない。ここで復元すると背面化の意味が消える"""
        u = self._user32(IsIconic=1)

        self.assertFalse(self._send(u))
        u.AttachThreadInput.assert_not_called()
        u.PostMessageW.assert_not_called()

    def test_unknown_thread_is_refused(self):
        u = self._user32(GetWindowThreadProcessId=0)

        self.assertFalse(self._send(u))
        u.AttachThreadInput.assert_not_called()

    def test_multi_char_key_is_refused(self):
        """"f13" のような複数文字キーは VkKeyScanExW で解決できない"""
        u = self._user32()

        self.assertFalse(self._send(u, key="f13"))
        u.PostMessageW.assert_not_called()

    def test_happy_path_order(self):
        u = self._user32()
        order = []
        u.AttachThreadInput.side_effect = lambda a, b, f: order.append(
            "attach" if f else "detach") or 1
        u.SetKeyboardState.side_effect = lambda *_a: order.append("state") or 1
        u.PostMessageW.side_effect = lambda h, msg, *_a: order.append(
            "down" if msg == WindowOperator.WM_KEYDOWN else "up") or 1

        self.assertTrue(self._send(u))

        self.assertEqual(order[0], "attach")
        self.assertEqual(order[-1], "detach")
        self.assertEqual([o for o in order if o in ("down", "up")], ["down", "up"])
        self.assertIn("state", order[:3], "押下はキー状態にも書き込むこと")

    def test_lparam_values(self):
        u = self._user32()

        self.assertTrue(self._send(u))

        posts = [c.args for c in u.PostMessageW.call_args_list]
        self.assertEqual(len(posts), 2)
        (_h1, msg_down, vk_down, lp_down) = posts[0]
        (_h2, msg_up, vk_up, lp_up) = posts[1]
        self.assertEqual((msg_down, vk_down, lp_down),
                         (WindowOperator.WM_KEYDOWN, self.VK, 0x000D0001))
        self.assertEqual((msg_up, vk_up, lp_up),
                         (WindowOperator.WM_KEYUP, self.VK, 0xC00D0001))

    def test_detach_runs_even_on_error(self):
        """アタッチしたまま抜けるとユーザーの操作が対象窓へ流れ込む"""
        u = self._user32()
        u.PostMessageW.side_effect = OSError("boom")

        self.assertFalse(self._send(u))

        detaches = [c.args for c in u.AttachThreadInput.call_args_list
                    if not c.args[2]]
        self.assertEqual(len(detaches), 1, u.AttachThreadInput.call_args_list)

    def test_used_win32_apis_exist_on_real_dlls(self):
        """user32/kernel32 の取り違えはモックでは出ない。実物で名前だけ確かめる"""
        u = ctypes.WinDLL("user32")
        k = ctypes.WinDLL("kernel32")

        for name in ("GetWindowThreadProcessId", "IsIconic", "GetKeyboardLayout",
                     "VkKeyScanExW", "MapVirtualKeyExW", "AttachThreadInput",
                     "SendMessageTimeoutW", "SetFocus", "GetKeyboardState",
                     "SetKeyboardState", "PostMessageW"):
            self.assertTrue(hasattr(u, name), "user32." + name)
        self.assertTrue(hasattr(k, "GetCurrentThreadId"), "kernel32.GetCurrentThreadId")

    def test_thread_id_failure_does_not_escape(self):
        """スレッドID取得で落ちても False を返す（例外を投げるとフォールバックが走らない）"""
        u = self._user32()
        k = MagicMock()
        k.GetCurrentThreadId.side_effect = AttributeError("not found")

        self.assertFalse(self._send(u, kernel=k))
        u.PostMessageW.assert_not_called()
        u.AttachThreadInput.assert_not_called()

    def test_key_state_is_restored(self):
        u = self._user32()

        self.assertTrue(self._send(u))

        # 最後の SetKeyboardState では対象キーが離された状態に戻っている
        last = u.SetKeyboardState.call_args_list[-1].args[0]
        self.assertEqual(last._obj[self.VK], 0)




class TestHideOwnWindowsWhileRecording(unittest.TestCase):
    """録画中だけ、当ツールの窓を画面キャプチャから外す。

    物理モニタには見えたまま・操作もできて、録画とスクリーンショットからだけ
    消える（SetWindowDisplayAffinity の WDA_EXCLUDEFROMCAPTURE）。
    """

    def setUp(self):
        for hwnd in SharedState.own_windows():
            SharedState.unregister_own_window(hwnd)
        self.addCleanup(lambda: [SharedState.unregister_own_window(h)
                                 for h in SharedState.own_windows()])

    def _plan(self, calls):
        """録画の段取り。隠す関数は差し込む（Recorder は窓を触らない）"""
        client = MagicMock()
        client.connect.return_value = (True, "")
        client.request.return_value = (True, {"outputActive": False}, "")
        return Recorder.RecordPlan(lambda: client, lambda _m: None,
                                   clock=lambda: 0.0,
                                   hide_windows=calls.append)

    # ── 1〜2. 開始で隠し、停止で戻す ───────────────────
    def test_recording_hides_and_stopping_restores(self):
        calls = []
        plan = self._plan(calls)

        plan.continue_start(1)
        self.assertEqual(calls, [True], "開始で隠す")

        plan.stop_all()
        self.assertEqual(calls, [True, False], "停止で戻す")

    def test_a_recording_we_did_not_start_changes_nothing(self):
        """手動の録画には触らない（隠しもしない）"""
        calls = []
        client = MagicMock()
        client.connect.return_value = (True, "")
        client.request.return_value = (True, {"outputActive": True}, "")
        plan = Recorder.RecordPlan(lambda: client, lambda _m: None,
                                   clock=lambda: 0.0, hide_windows=calls.append)

        plan.continue_start(1)

        self.assertEqual(calls, [])

    # ── 3. 設定で切れる ────────────────────────
    def test_the_switch_turns_it_off(self):
        calls = []
        plan = self._plan(calls)

        with patch.object(config, "HIDE_OWN_WINDOWS_WHILE_RECORDING", False):
            plan.continue_start(1)
            plan.stop_all()

        self.assertEqual(calls, [])

    # ── 4. 失敗しても録画は続く ────────────────────
    def test_a_failure_does_not_stop_the_recording(self):
        def boom(_hidden):
            raise OSError("古い Windows")

        client = MagicMock()
        client.connect.return_value = (True, "")
        client.request.return_value = (True, {"outputActive": False}, "")
        plan = Recorder.RecordPlan(lambda: client, lambda _m: None,
                                   clock=lambda: 0.0, hide_windows=boom)

        plan.continue_start(1)

        self.assertTrue(plan.we_started, "録画は始まっている")

    def test_the_warning_is_logged_once(self):
        logs = []
        app = type("FakeApp", (), {})()
        app._log = logs.append
        app._capture_warned = False
        SharedState.register_own_window(11)
        SharedState.register_own_window(22)

        with patch.object(WindowOperator, "set_capture_excluded",
                          return_value=False):
            mainGUI.App._set_own_windows_hidden(app, True)
            mainGUI.App._set_own_windows_hidden(app, True)

        self.assertEqual(len([m for m in logs if "隠せませんでした" in m]), 1, logs)

    # ── 5. 開け閉めで登録が出入りする ──────────────────
    def test_registering_and_unregistering(self):
        SharedState.register_own_window(31)
        SharedState.register_own_window(32)
        self.assertEqual(SharedState.own_windows(), frozenset({31, 32}))

        SharedState.unregister_own_window(31)

        self.assertEqual(SharedState.own_windows(), frozenset({32}))

    def test_a_zero_hwnd_is_not_registered(self):
        SharedState.register_own_window(0)

        self.assertEqual(SharedState.own_windows(), frozenset())

    def test_the_windows_unregister_themselves_when_destroyed(self):
        """オーバーレイと統計窓は開け閉めする。閉じたら外れること"""
        for name in ("mainGUI.py", "StatisticsGUI.py"):
            src = Path(name).read_text(encoding="utf-8")
            self.assertIn("SharedState.unregister_own_window", src, name)
            self.assertIn('bind("<Destroy>"', src, name)
            self.assertIn("event.widget is", src, name)

    # ── 6. VRChat の窓とは混ぜない ───────────────────
    def test_our_windows_never_land_in_the_vrchat_set(self):
        """混ぜると _vrchat_is_in_front() が誤判定して Begin が毎回フォールバックする"""
        SharedState.clear_window_hwnds()
        self.addCleanup(SharedState.clear_window_hwnds)
        SharedState.register_own_window(41)

        self.assertEqual(SharedState.managed_hwnds(), frozenset())
        self.assertNotIn(41, SharedState.managed_hwnds())

        SharedState.register_window_hwnd(42)          # VRChat の窓

        self.assertEqual(SharedState.own_windows(), frozenset({41}))
        self.assertEqual(SharedState.managed_hwnds(), frozenset({42}))

    def test_clearing_the_vrchat_set_keeps_our_windows(self):
        SharedState.register_own_window(51)
        SharedState.register_window_hwnd(52)

        SharedState.clear_window_hwnds()              # マクロ停止

        self.assertEqual(SharedState.own_windows(), frozenset({51}),
                         "GUI は開いたままなので残す")
        self.assertEqual(SharedState.managed_hwnds(), frozenset())

    def test_our_window_in_front_does_not_look_like_vrchat(self):
        cfg = WindowConfig(hwnd=61, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
        SharedState.clear_window_hwnds()
        self.addCleanup(SharedState.clear_window_hwnds)
        SharedState.register_own_window(99)           # 当ツールの窓が前面

        with patch.object(WindowOperator, "foreground_hwnd", return_value=99):
            self.assertFalse(ex._vrchat_is_in_front())

    # ── 7. 停止・終了で戻す ────────────────────
    def test_stopping_restores_every_window(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        SharedState.register_own_window(71)
        SharedState.register_own_window(72)

        with patch.object(WindowOperator, "set_capture_excluded",
                          return_value=True) as excluded:
            mainGUI.App._show_own_windows_again(app)

        self.assertEqual(sorted(c.args for c in excluded.call_args_list),
                         [(71, False), (72, False)])

    def test_the_stop_and_the_close_both_restore(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        stop = src[src.index("    def _stop(self):"):
                   src.index("    def _log(self, msg: str):")]
        close = src[src.index("    def _on_close(self):"):]
        close = close[:close.index("\n    def ", 10)] if "\n    def " in close else close

        self.assertIn("_show_own_windows_again()", stop)
        self.assertIn("_show_own_windows_again()", close)

    def test_the_app_gives_the_recorder_its_hider(self):
        """差し込まないと、録画中に隠す相手が居ない"""
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        init = src[src.index("        self._build_ui()"):
                   src.index("    def _start_emergency_stop_polling(self):")]

        self.assertIn("Recorder.set_window_hider(self._set_own_windows_hidden)",
                      init)

    # ── Win32 の呼び方 ──────────────────────
    def test_the_affinity_values(self):
        self.assertEqual(WindowOperator.WDA_EXCLUDEFROMCAPTURE, 0x11)
        self.assertEqual(WindowOperator.WDA_NONE, 0)

    def test_it_asks_windows_to_exclude_the_window(self):
        user32 = MagicMock()
        user32.SetWindowDisplayAffinity.return_value = 1

        with patch.object(WindowOperator, "user32", user32):
            self.assertTrue(WindowOperator.set_capture_excluded(123, True))
            self.assertTrue(WindowOperator.set_capture_excluded(123, False))

        self.assertEqual([c.args for c in
                          user32.SetWindowDisplayAffinity.call_args_list],
                         [(123, 0x11), (123, 0)])

    def test_an_old_windows_returns_false(self):
        user32 = MagicMock()
        user32.SetWindowDisplayAffinity.side_effect = OSError("未対応")

        with patch.object(WindowOperator, "user32", user32):
            self.assertFalse(WindowOperator.set_capture_excluded(123, True))

    def test_no_hwnd_is_false(self):
        self.assertFalse(WindowOperator.set_capture_excluded(0, True))

    def test_the_toplevel_hwnd_is_the_root_ancestor(self):
        """Tk の winfo_id() は子ウィンドウ。そのままでは窓全体に効かない。親の鎖の一番上を使う"""
        user32 = MagicMock()
        user32.GetAncestor.return_value = 300

        with patch.object(WindowOperator, "user32", user32):
            self.assertEqual(WindowOperator.toplevel_hwnd(100), 300)
        user32.GetAncestor.assert_called_once_with(100, WindowOperator.GA_ROOT)
        user32.GetParent.assert_not_called()

    def test_the_owner_of_a_popup_is_not_followed(self):
        """枠なしのオーバーレイ（ポップアップ）では GetParent が持ち主（メイン画面）を返す。
        それをたどるとオーバーレイの代わりにメイン画面を覚え、オーバーレイが録画に映っていた"""
        user32 = MagicMock()
        user32.GetAncestor.return_value = 500     # オーバーレイ自身
        user32.GetParent.return_value = 900       # 持ち主（メイン画面）

        with patch.object(WindowOperator, "user32", user32):
            self.assertEqual(WindowOperator.toplevel_hwnd(400), 500)

    def test_a_toplevel_without_an_ancestor_is_itself(self):
        user32 = MagicMock()
        user32.GetAncestor.return_value = 0

        with patch.object(WindowOperator, "user32", user32):
            self.assertEqual(WindowOperator.toplevel_hwnd(100), 100)

    def test_a_broken_getancestor_falls_back_to_the_child(self):
        user32 = MagicMock()
        user32.GetAncestor.side_effect = OSError("取れない")

        with patch.object(WindowOperator, "user32", user32):
            self.assertEqual(WindowOperator.toplevel_hwnd(100), 100)

    def test_a_window_opened_while_recording_is_hidden_at_once(self):
        """録画の途中で開いたオーバーレイ・統計画面も、開いたその場で外す"""
        app = type("FakeApp", (), {})()
        app._log = lambda _m: None
        app._capture_warned = False
        widget = MagicMock()
        self.addCleanup(SharedState.set_own_windows_hidden, False)
        self.addCleanup(SharedState.unregister_own_window, 81)
        with patch.object(WindowOperator, "set_capture_excluded", return_value=True) as excluded, \
             patch.object(WindowOperator, "own_window_hwnd", return_value=81):
            mainGUI.App._set_own_windows_hidden(app, True)      # 録画開始
            mainGUI._remember_own_window(widget)                # その後でオーバーレイを開いた
        self.assertIn((81, True), [c.args for c in excluded.call_args_list])

    def test_a_window_opened_while_not_recording_is_left_alone(self):
        widget = MagicMock()
        SharedState.set_own_windows_hidden(False)
        self.addCleanup(SharedState.unregister_own_window, 82)
        with patch.object(WindowOperator, "set_capture_excluded") as excluded, \
             patch.object(WindowOperator, "own_window_hwnd", return_value=82):
            mainGUI._remember_own_window(widget)
        excluded.assert_not_called()

    def test_one_failure_does_not_leave_the_others_shown(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app._capture_warned = False
        self.addCleanup(SharedState.set_own_windows_hidden, False)
        for h in (91, 92, 93):
            SharedState.register_own_window(h)
            self.addCleanup(SharedState.unregister_own_window, h)
        with patch.object(WindowOperator, "set_capture_excluded",
                          side_effect=lambda h, _x: h != 91) as excluded:
            mainGUI.App._set_own_windows_hidden(app, True)
        self.assertEqual(sorted(c.args[0] for c in excluded.call_args_list), [91, 92, 93])
        self.assertEqual(len([m for m in app.logs if "隠せませんでした" in m]), 1)

    def test_stopping_the_recording_clears_the_state(self):
        app = type("FakeApp", (), {})()
        app._log = lambda _m: None
        app._capture_warned = False
        with patch.object(WindowOperator, "set_capture_excluded", return_value=True):
            mainGUI.App._set_own_windows_hidden(app, True)
            self.assertTrue(SharedState.own_windows_hidden())
            mainGUI.App._set_own_windows_hidden(app, False)
        self.assertFalse(SharedState.own_windows_hidden())

    def test_the_recorder_keeps_its_dependencies(self):
        """窓の操作は Recorder に持ち込まない（標準ライブラリ＋OBSClient だけ）"""
        src = Path(Recorder.__file__).read_text(encoding="utf-8")

        self.assertNotIn("import WindowOperator", src)
        self.assertNotIn("import SharedState", src)




class TestAimInWindowImage(unittest.TestCase):
    def test_the_aim_is_in_window_image_coordinates(self):
        """窓表示（タイトルバーあり）: クライアントの左上は窓の左上から (8, 31) ずれる。
        照準はクライアントの中央を、窓の画像（GetWindowRect 基準）の座標で返す"""
        with patch.object(WindowOperator.win32gui, "GetWindowRect",
                          return_value=(100, 50, 1100, 850)), \
             patch.object(WindowOperator.win32gui, "ClientToScreen",
                          return_value=(108, 81)), \
             patch.object(WindowOperator.win32gui, "GetClientRect",
                          return_value=(0, 0, 984, 761)):
            self.assertEqual(WindowOperator.aim_in_window_image(0x100), (500.0, 411.5))

    def test_no_client_area_is_none(self):
        with patch.object(WindowOperator.win32gui, "GetWindowRect", return_value=(0, 0, 10, 10)), \
             patch.object(WindowOperator.win32gui, "ClientToScreen", return_value=(0, 0)), \
             patch.object(WindowOperator.win32gui, "GetClientRect", return_value=(0, 0, 0, 0)):
            self.assertIsNone(WindowOperator.aim_in_window_image(0x100))




class TestHoldKeysBackground(unittest.TestCase):
    """止める合図が来るまで、複数のキーを背面へ押し続ける"""

    DOWN = {0x41: 0x1E0001, 0xBE: 0x340001}
    UP = {vk: lp | (1 << 30) | (1 << 31) for vk, lp in DOWN.items()}

    def setUp(self):
        self.calls = []
        u = MagicMock()
        u.IsIconic.return_value = 0
        u.AttachThreadInput.side_effect = lambda a, b, on: self.calls.append(("attach", on)) or 1
        def pressed_state(ref):
            for vk in self.DOWN:            # いまは押されている（押したキーの状態）
                ref._obj[vk] = 0x80
            return 1

        u.GetKeyboardState.side_effect = pressed_state
        u.SetKeyboardState.side_effect = lambda ref: self.calls.append(
            ("state", tuple(ref._obj[vk] for vk in self.DOWN)))
        u.PostMessageW.side_effect = lambda h, msg, vk, lp: self.calls.append((msg, vk, lp))
        u.SendMessageTimeoutW.return_value = 1
        k = MagicMock()
        k.GetCurrentThreadId.return_value = 7
        params = {"a": (9, 0x41, self.DOWN[0x41], self.UP[0x41]),
                  ".": (9, 0xBE, self.DOWN[0xBE], self.UP[0xBE])}
        for p in (patch.object(WindowOperator, "user32", u),
                  patch.object(WindowOperator, "kernel32", k),
                  patch.object(WindowOperator, "_background_key",
                               side_effect=lambda h, key: params.get(key))):
            p.start()
            self.addCleanup(p.stop)
        self.u = u

    class Stop:
        """wait() が2回 False（送り直し）→ 3回目で True（止める合図）"""

        def __init__(self, resends=2):
            self.left = resends

        def wait(self, _sec):
            self.left -= 1
            return self.left < 0

    def _posts(self, msg):
        return [c for c in self.calls if c[0] == msg]

    def test_it_resends_with_the_repeat_bit_then_releases(self):
        ok = WindowOperator.hold_keys_background(0x100, ("a", "."), self.Stop(2), 0.2)

        self.assertTrue(ok)
        downs = self._posts(WindowOperator.WM_KEYDOWN)
        self.assertEqual(len(downs), 6, "3回（最初＋送り直し2回）× 2キー")
        self.assertEqual(downs[:2], [(WindowOperator.WM_KEYDOWN, 0x41, self.DOWN[0x41]),
                                     (WindowOperator.WM_KEYDOWN, 0xBE, self.DOWN[0xBE])])
        for _m, vk, lp in downs[2:]:
            self.assertEqual(lp, self.DOWN[vk] | (1 << 30), "2回目以降は押しっぱなしの繰り返し")
        self.assertEqual(self._posts(WindowOperator.WM_KEYUP),
                         [(WindowOperator.WM_KEYUP, 0x41, self.UP[0x41]),
                          (WindowOperator.WM_KEYUP, 0xBE, self.UP[0xBE])])
        self.assertEqual([c for c in self.calls if c[0] == "state"][-1], ("state", (0, 0)),
                         "キー状態から押下を消す")

    def test_it_attaches_and_detaches_for_every_resend(self):
        WindowOperator.hold_keys_background(0x100, ("a", "."), self.Stop(2), 0.2)

        attaches = [c[1] for c in self.calls if c[0] == "attach"]
        self.assertEqual(attaches, [True, False] * 4, "送り直し3回＋離す1回。毎回外す")

    def test_an_exception_still_releases_and_detaches(self):
        self.u.SetFocus.side_effect = OSError("boom")

        self.assertFalse(WindowOperator.hold_keys_background(0x100, ("a", "."), self.Stop(2), 0.2))

        self.assertEqual(len(self._posts(WindowOperator.WM_KEYUP)), 2)
        attaches = [c[1] for c in self.calls if c[0] == "attach"]
        self.assertEqual(attaches.count(True), attaches.count(False))

    def test_a_minimised_window_is_not_touched(self):
        self.u.IsIconic.return_value = 1

        self.assertFalse(WindowOperator.hold_keys_background(0x100, ("a", "."), self.Stop(), 0.2))
        self.assertEqual(self.calls, [])

    def test_an_unsendable_key_is_refused(self):
        self.assertFalse(WindowOperator.hold_keys_background(0x100, ("a", "?"), self.Stop(), 0.2))
        self.assertEqual(self.calls, [])




class TestOptimization(unittest.TestCase):
    """8 Pages はテラーが出てから自爆・チェイスのキーの外し方・管理者権限の窓・[画面] の二重"""

    P = "2026.10.02 12:00:00 Debug      -  "

    def setUp(self):
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        for p in (patch.object(ConnectDB, "register_round"), patch.object(PlaySound, "play_sound")):
            p.start()
            self.addCleanup(p.stop)
        self.thread = patch.object(LogMonitor.threading, "Thread").start()
        self.addCleanup(patch.stopall)

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.item_id = 5
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _skips(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs].count("do_skip")

    # ── 1. 8 Pages ─────────────────────────────
    def test_eight_pages_waits_for_the_terror_then_skips(self):
        monitor = self._monitor()
        monitor._process(self.P + "This round is taking place at Sewers (12) and the round type is 8 Pages")
        monitor._process(self.P + "Killers is unknown - ??? // Will be revealed after 40 seconds // Round type is 8 Pages")
        self.assertEqual(self._skips(), 0, "テラー不明のうちは自爆しない")
        self.assertTrue(any("開始: 8 Pages 【放置モード→テラーが出てから自爆】" in m for m in monitor.logs),
                        monitor.logs)
        monitor._process(self.P + "Killers have been revealed - 50 2 0 // Round type is 8 Pages")
        self.assertEqual(self._skips(), 1, "テラーが出た後に自爆")
        self.assertEqual(config.SUICIDE_RETRY_MAX, 3, "やり直しは今の3回")

    def test_fog_still_skips_at_once(self):
        monitor = self._monitor()
        monitor._process(self.P + "This round is taking place at Sewers (12) and the round type is Fog")
        monitor._process(self.P + "Killers is unknown - ??? // Will be revealed after 50 seconds // Round type is Fog")
        self.assertEqual(self._skips(), 1)
        self.assertTrue(any("開始: Fog 【放置モード→即自爆】" in m for m in monitor.logs))

    # ── 2. チェイスのキー ───────────────────────────
    def _keys_app(self, fail_on=None):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        keyboard = MagicMock()

        def hook_key(key, callback, suppress):
            if key == fail_on:
                raise ValueError("bad key")
            return ("hook", key)

        keyboard.hook_key.side_effect = hook_key
        with patch.object(mainGUI, "keyboard", keyboard):
            mainGUI.App._hook_chase_keys(app)
        return app, keyboard

    def test_only_registered_hooks_are_removed_and_key_error_is_quiet(self):
        app, keyboard = self._keys_app(fail_on="f2")
        self.assertEqual(app._chase_hooks, [("hook", "f1")], "登録できたものだけ")
        keyboard.unhook.side_effect = KeyError("f1")
        with patch.object(mainGUI, "keyboard", keyboard), \
             patch.object(DebugLog, "exception") as exception:
            mainGUI.App._unhook_chase_keys(app)
        self.assertEqual([c.args[0] for c in keyboard.unhook.call_args_list], [("hook", "f1")])
        exception.assert_not_called()
        self.assertEqual(app._chase_hooks, [])

    def test_other_unhook_errors_are_still_written(self):
        app, keyboard = self._keys_app()
        keyboard.unhook.side_effect = OSError("x")
        with patch.object(mainGUI, "keyboard", keyboard), \
             patch.object(DebugLog, "exception") as exception:
            mainGUI.App._unhook_chase_keys(app)
        self.assertEqual(exception.call_count, 2)

    def test_a_stop_without_hooks_does_nothing(self):
        app = type("FakeApp", (), {})()
        keyboard = MagicMock()
        with patch.object(mainGUI, "keyboard", keyboard):
            mainGUI.App._unhook_chase_keys(app)
        keyboard.unhook.assert_not_called()

    def test_the_real_keyboard_shape_unhooks_without_key_error(self):
        """keyboard の本物と同じく、同じキーの項目は表に1つ（2つ目を外すと KeyError になる形）"""
        hooks = {}

        def hook_key(key, callback, suppress=False):
            def remove():
                del hooks[callback]
                del hooks[key]
                del hooks[remove]
            hooks[callback] = hooks[key] = hooks[remove] = remove
            return remove

        keyboard = MagicMock()
        keyboard.hook_key.side_effect = hook_key
        keyboard.unhook.side_effect = lambda remove: hooks[remove]()
        app = type("FakeApp", (), {})()
        app._log = lambda _m: None
        with patch.object(mainGUI, "keyboard", keyboard), \
             patch.object(DebugLog, "exception") as exception:
            mainGUI.App._hook_chase_keys(app)
            mainGUI.App._unhook_chase_keys(app)
        exception.assert_not_called()
        self.assertEqual(hooks, {}, "全部外れる")

    # ── 3. 管理者権限の窓 ────────────────────────────
    def test_an_elevated_window_is_not_given_back(self):
        loan = WindowOperator.FrontLoan(hwnd=0x10, previous=0x20, cursor=(1, 2))
        written = []
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x10), \
             patch.object(WindowOperator.win32gui, "IsWindow", return_value=True), \
             patch.object(WindowOperator, "can_bring_to_front", return_value=False), \
             patch.object(WindowOperator, "focus_window") as focus, \
             patch.object(WindowOperator.user32, "SetCursorPos") as cursor, \
             patch.object(DebugLog, "write", side_effect=written.append):
            self.assertFalse(WindowOperator.return_front(loan))
        focus.assert_not_called()
        cursor.assert_not_called()
        self.assertIn("[操作] 前面を返す → 相手が管理者権限のため返しません", written)

    def test_can_bring_to_front(self):
        def run(me, target):
            values = {None: me}

            def elevated(pid):
                return me if pid is None else target

            with patch.object(WindowOperator, "_process_elevated", side_effect=elevated), \
                 patch.object(WindowOperator.user32, "GetWindowThreadProcessId",
                              side_effect=lambda _h, ref: setattr(ref._obj, "value", 1234) or 1):
                return WindowOperator.can_bring_to_front(0x20)

        self.assertFalse(run(False, True), "相手が昇格")
        self.assertFalse(run(False, None), "相手を開けない")
        self.assertTrue(run(False, False), "相手もふつう")
        self.assertTrue(run(True, True), "ツール自身が昇格していれば今のまま")

    def test_the_real_probe_answers(self):
        self.assertIn(WindowOperator._process_elevated(None), (True, False), "このプロセスは調べられる")
        self.assertIn(WindowOperator._process_elevated(os.getpid()), (True, False))

    # ── 4. [画面] の二重 ────────────────────────────
    def test_the_screen_tag_is_not_doubled(self):
        app = type("FakeApp", (), {})()
        app.after = lambda _ms, _fn=None: None
        written = []
        with patch.object(DebugLog, "write", side_effect=written.append):
            mainGUI.App._log(app, "[画面] フォント: Meiryo UI")
            mainGUI.App._log(app, "[窓1] ✅ Connecting")
        self.assertEqual(written, ["[画面] フォント: Meiryo UI", "[画面] [窓1] ✅ Connecting"])




class TestOscHoldResend(unittest.TestCase):
    """OSC で押している間、まだ押しているアドレスへ 1 を OSC_HOLD_RESEND_SEC ごとに送り直す。
    押す長さ・最初の 0→1・最後の 0 は今どおり（偽の送信と時計）"""

    def _client(self):
        client = OSCClient.OSCClient(9000)
        self.addCleanup(client.close)
        self.sent = []
        self.now = [1000.0]
        for p in (patch.object(client, "send", side_effect=lambda a, v: self.sent.append(
                      (round(self.now[0] - 1000.0, 3), a.split("/")[-1], v)) or True),
                  patch.object(OSCClient.time, "time", side_effect=lambda: self.now[0]),
                  patch.object(OSCClient.time, "sleep",
                               side_effect=lambda s: self.now.__setitem__(0, self.now[0] + s))):
            p.start()
            self.addCleanup(p.stop)
        return client

    def test_the_interval(self):
        self.assertEqual(config.OSC_HOLD_RESEND_SEC, 0.1)

    def test_press_resends_and_keeps_its_length(self):
        client = self._client()
        self.assertTrue(client.press("/input/MoveForward", 2.08))
        self.assertEqual(self.sent[:2], [(0.0, "MoveForward", 0), (0.0, "MoveForward", 1)], "最初の 0→1")
        resends = self.sent[2:-1]
        self.assertEqual([t for t, _a, _v in resends], [round(0.1 * k, 3) for k in range(1, 21)],
                         "0.1 秒ごと（2.0 秒まで）")
        self.assertTrue(all(v == 1 for _t, _a, v in resends))
        self.assertEqual(self.sent[-1], (2.08, "MoveForward", 0), "2.08 秒で離す（長さは変わらない）")

    def test_press_multi_stops_resending_what_was_released(self):
        client = self._client()
        self.assertTrue(client.press_multi([("/input/MoveForward", 2.08), ("/input/MoveLeft", 0.15)]))
        self.assertEqual(self.sent[:4], [(0.0, "MoveForward", 0), (0.0, "MoveLeft", 0),
                                         (0.0, "MoveForward", 1), (0.0, "MoveLeft", 1)])
        self.assertIn((0.1, "MoveForward", 1), self.sent)
        self.assertIn((0.1, "MoveLeft", 1), self.sent, "左も離すまでは送り直す")
        self.assertIn((0.15, "MoveLeft", 0), self.sent, "0.15 秒で左を離す")
        after = [s for s in self.sent if s[0] > 0.15]
        self.assertTrue(after)
        self.assertFalse([s for s in after if s[1] == "MoveLeft"], "離した左は送り直さない")
        self.assertEqual(after[-1], (2.08, "MoveForward", 0))
        forward = [t for t, a, v in after[:-1] if a == "MoveForward" and v == 1]
        self.assertEqual(forward, [round(0.25 + 0.1 * k, 3) for k in range(19)], "左を離した後も 0.1 秒ごと")

    def test_a_short_pulse_is_not_resent(self):
        client = self._client()
        client.press("/input/UseRight", 0.025)
        self.assertEqual([v for _t, _a, v in self.sent], [0, 1, 0])

    def test_no_resend_after_the_release_time(self):
        client = self._client()
        client.press("/input/MoveForward", 0.3)
        self.assertEqual([(t, v) for t, _a, v in self.sent],
                         [(0.0, 0), (0.0, 1), (0.1, 1), (0.2, 1), (0.3, 0)], "離す時刻ちょうどには送り直さない")




class TestHoldKeyBackgroundInParallel(unittest.TestCase):
    """並行に呼んでも、各呼び出しは自分の対象にだけアタッチし、必ず外す"""

    def test_each_call_attaches_only_to_its_own_window(self):
        targets = {0xA: 1001, 0xB: 1002}
        calls = []
        record = threading.Lock()
        gate = threading.Barrier(2, timeout=2.0)

        u = MagicMock()
        u.GetWindowThreadProcessId.side_effect = lambda h, _p: targets[h]
        u.IsIconic.return_value = 0
        u.GetKeyboardLayout.return_value = 0x04110411
        u.VkKeyScanExW.return_value = 0xDE
        u.MapVirtualKeyExW.return_value = 0x0D
        u.GetKeyboardState.return_value = 1

        def attach(me, target, on):
            with record:
                calls.append((me, target, on))
            return 1

        u.AttachThreadInput.side_effect = attach
        k = MagicMock()
        k.GetCurrentThreadId.side_effect = threading.get_ident

        results = {}

        def run(hwnd):
            results[hwnd] = WindowOperator.hold_key_background(hwnd, "^", 3.0)

        with patch.object(WindowOperator, "user32", u), \
             patch.object(WindowOperator, "kernel32", k), \
             patch.object(WindowOperator.time, "sleep",
                          side_effect=lambda _s: gate.wait()):
            threads = [threading.Thread(target=run, args=(h,)) for h in targets]
            for t in threads:
                t.start()
            for t in threads:
                t.join(3.0)

        self.assertEqual(results, {0xA: True, 0xB: True})
        attached = [(me, t) for me, t, on in calls if on]
        detached = [(me, t) for me, t, on in calls if not on]
        self.assertEqual(sorted(attached), sorted(detached), "必ず外すこと")
        self.assertEqual(sorted(t for _me, t in attached), [1001, 1002])
        self.assertEqual(len({me for me, _t in attached}), 2,
                         "呼び出したスレッドごとに別々にアタッチしていること")




class TestReleaseKeyBackground(unittest.TestCase):
    """押されたままかもしれない自爆キーを離す"""

    VK = 0xDE
    SCAN = 0x0D

    def _user32(self, pressed=True, **over):
        u = MagicMock()
        u.GetWindowThreadProcessId.return_value = 4321
        u.GetKeyboardLayout.return_value = 0x04110411
        u.VkKeyScanExW.return_value = self.VK
        u.MapVirtualKeyExW.return_value = self.SCAN
        u.AttachThreadInput.return_value = 1

        def get_state(ref):
            if pressed:
                ref._obj[self.VK] = 0x80
            return 1

        u.GetKeyboardState.side_effect = get_state
        for name, value in over.items():
            getattr(u, name).return_value = value
        return u

    def _release(self, u, key="^", hwnd=0x1234):
        k = MagicMock()
        k.GetCurrentThreadId.return_value = 99
        with patch.object(WindowOperator, "user32", u), \
             patch.object(WindowOperator, "kernel32", k):
            return WindowOperator.release_key_background(hwnd, key)

    def test_the_key_up_is_posted(self):
        u = self._user32()

        self.assertTrue(self._release(u))

        msg = u.PostMessageW.call_args.args
        self.assertEqual(msg[:3], (0x1234, WindowOperator.WM_KEYUP, self.VK))
        self.assertTrue(msg[3] & (1 << 31), "離上の lParam")

    def test_a_held_key_state_is_cleared(self):
        u = self._user32(pressed=True)
        written = []
        u.SetKeyboardState.side_effect = lambda ref: written.append(
            ref._obj[self.VK]) or 1

        self._release(u)

        self.assertEqual(written, [0])

    def test_an_unpressed_key_leaves_the_state_alone(self):
        """押していなくても無害"""
        u = self._user32(pressed=False)

        self.assertTrue(self._release(u))

        u.SetKeyboardState.assert_not_called()

    def test_it_detaches(self):
        u = self._user32()

        self._release(u)

        self.assertEqual([c.args[2] for c in u.AttachThreadInput.call_args_list],
                         [True, False])

    def test_a_minimized_window_still_gets_it(self):
        """離すだけなら窓を出す必要は無い"""
        u = self._user32(IsIconic=1)

        self.assertTrue(self._release(u))
        u.PostMessageW.assert_called_once()

    def test_an_unsendable_key_does_nothing(self):
        for over in (dict(VkKeyScanExW=-1), dict(VkKeyScanExW=(1 << 8) | self.VK),
                     dict(GetWindowThreadProcessId=0)):
            u = self._user32(**over)

            self.assertFalse(self._release(u), over)
            u.PostMessageW.assert_not_called()

    def test_a_failure_does_not_raise(self):
        u = self._user32()
        u.PostMessageW.side_effect = OSError("gone")

        self.assertFalse(self._release(u))
        self.assertEqual(u.AttachThreadInput.call_args.args[2], False,
                         "失敗してもデタッチすること")

    def test_the_real_dll_has_what_it_uses(self):
        """名前を間違えると AttributeError で黙って止まる（以前の実例あり）"""
        import ctypes as real_ctypes
        u32 = real_ctypes.WinDLL("user32")
        k32 = real_ctypes.WinDLL("kernel32")
        for name in ("GetWindowThreadProcessId", "GetKeyboardLayout",
                     "VkKeyScanExW", "MapVirtualKeyExW", "AttachThreadInput",
                     "GetKeyboardState", "SetKeyboardState", "PostMessageW"):
            self.assertTrue(hasattr(u32, name), name)
        self.assertTrue(hasattr(k32, "GetCurrentThreadId"))




class TestHoldKey(unittest.TestCase):
    def test_zero_sec_skips(self):
        """sec=0の時は何もしない"""
        with patch('keyboard.press') as mock:
            WindowOperator.hold_key('w', 0.0)
            mock.assert_not_called()

    def test_holds_key(self):
        """press→sleep→releaseの順で呼ばれる"""
        with patch('keyboard.press') as mock_press, \
             patch('keyboard.release') as mock_release:
            WindowOperator.hold_key('w', 0.1)
            mock_press.assert_called_once_with('w')
            mock_release.assert_called_once_with('w')



class TestClickAt(unittest.TestCase):
    def test_click(self):
        """mouseDown→mouseUpの順で呼ばれる"""
        with patch('pydirectinput.mouseDown') as mock_down, \
             patch('pydirectinput.mouseUp') as mock_up:
            WindowOperator.click()
            mock_down.assert_called_once()
            mock_up.assert_called_once()




class TestOSCReceiverParse(unittest.TestCase):
    """VRChatからのOSCメッセージの読み取り"""

    def test_float_message(self):
        msg = OSCClient.OSCClient.build_message("/avatar/parameters/VelocityX", 1.5)
        self.assertEqual(OSCReceiver.parse_message(msg),
                         ("/avatar/parameters/VelocityX", 1.5))

    def test_broken_message_is_ignored(self):
        self.assertIsNone(OSCReceiver.parse_message(b"garbage"))

    def test_magnitude_message(self):
        msg = OSCClient.OSCClient.build_message(OSCReceiver.VELOCITY_MAGNITUDE, 6.6)
        address, value = OSCReceiver.parse_message(msg)

        self.assertEqual(address, OSCReceiver.VELOCITY_MAGNITUDE)
        self.assertAlmostEqual(value, 6.6, places=4)

    def test_grounded_defaults_to_true_when_never_received(self):
        """Groundedを持たないアバターでも機能を止めない"""
        self.assertTrue(OSCReceiver.VelocityReceiver(19999).grounded)

    def test_alive_is_true_right_after_start(self):
        """起動直後は受信0が正常。猶予内はTrueを返す"""
        r = OSCReceiver.VelocityReceiver(19999)
        r._started = time.time()

        self.assertTrue(r.alive)
        self.assertFalse(r.ever_received)

    def test_alive_is_false_after_the_grace_period(self):
        r = OSCReceiver.VelocityReceiver(19999)
        r._started = time.time() - config.SPEED_RECV_TIMEOUT_SEC - 1

        self.assertFalse(r.alive)

    def test_alive_follows_the_last_packet_once_received(self):
        r = OSCReceiver.VelocityReceiver(19999)
        r._started = time.time() - 999
        r._last_recv = time.time()

        self.assertTrue(r.alive, "受信が続いていれば生きている")

        r._last_recv = time.time() - config.SPEED_RECV_TIMEOUT_SEC - 1
        self.assertFalse(r.alive, "途絶したら死んでいる")

    def test_speed_is_none_before_receiving(self):
        self.assertIsNone(OSCReceiver.VelocityReceiver(19999).speed)




class TestPressRecord(unittest.TestCase):
    """押した記録の漏れ（定期を受理と取り違えた不具合）。押す前に記録する（クリック・カーソルの差し込み）。UseRight の連打で
    押せる状態（前面か、カーソルがその窓の上）なら記録する。背面でカーソルも外なら記録しない"""

    VERIFIED = "2026.10.03 15:54:06 Debug      -  Verified"

    def _monitor(self, hwnd=0x22):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=hwnd, osc_port=9000, auto_begin=True), {},
                                        lambda _m: None, window_idx=2)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_end_seen = True
        monitor._verified.on_round_end_verified(0)
        monitor._running = True
        monitor._action._is_running = lambda: True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _verified(self, monitor):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.VERIFIED)

    def test_a_verified_during_the_click_is_accepted(self):
        monitor = self._monitor()
        ex = monitor._action
        with patch.object(ex, "_borrow_front", return_value=(True, None)), \
             patch.object(WindowOperator, "click", side_effect=lambda: self._verified(monitor)), \
             patch.object(WindowOperator, "return_front"):
            self.assertTrue(ex._press_begin(click_only=True))
        self.assertTrue(monitor.st.begin_done, "mouseDown と mouseUp の間に届いた")
        self.assertIn("[窓2] ✅ Connecting", monitor.logs)

    def test_a_verified_during_the_dwell_is_accepted(self):
        monitor = self._monitor()
        ex = monitor._action

        @contextlib.contextmanager
        def over(_hwnd, _say=None):
            yield True

        dwells = []

        def sleep(sec):
            if sec == config.BEGIN_CURSOR_DWELL_SEC:
                dwells.append(sec)
                self._verified(monitor)             # 置いている 0.05 秒の間に届いた
        with patch.object(WindowOperator, "cursor_over_window", side_effect=over), \
             patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
            self.assertTrue(ex._dip_cursor_for_begin(""))
        self.assertTrue(monitor.st.begin_done)
        self.assertEqual(len(dwells), 1, "1回目の差し込みで受理（やり直しで通ったのではない）")

    class _Stop:
        """1回目の UseRight の後に Verified を届け、その次で止める"""
        def __init__(self, on_wait, limit=3):
            self.on_wait, self.n, self.limit = on_wait, 0, limit

        def is_set(self):
            return self.n >= self.limit

        def wait(self, _sec):
            self.n += 1
            if self.n == 1:
                self.on_wait()

    def _spam(self, monitor, front, cursor_in):
        st = monitor.st
        st.round_over_time = time.time() - 60       # もう送り始める時刻
        with patch.object(WindowOperator, "foreground_hwnd", return_value=front), \
             patch.object(WindowOperator, "cursor_in_client", return_value=cursor_in), \
             patch.object(OSCClient.OSCClient, "press", return_value=True) as press:
            monitor._action._spam_use_right_loop(self._Stop(lambda: self._verified(monitor)), st.round_seq)
        return press

    def test_window_2_front_and_spamming_is_accepted_before_the_click(self):
        """窓2 15:54:06: 前面で連打中、クリックの前に Verified → 受理。位置合わせ・探す・撮影の保存は走らない"""
        monitor = self._monitor()
        self._spam(monitor, front=0x22, cursor_in=False)
        self.assertTrue(monitor.st.begin_done)
        ex = monitor._action
        with patch.object(ex, "_adjust_to_begin") as adjust, \
             patch.object(BeginMiss, "save") as save, \
             patch.object(WindowOperator, "click") as click:
            self.assertTrue(ex._press_begin(click_only=True))
            ex._confirm_begin(monitor.st.round_seq)
        adjust.assert_not_called()
        save.assert_not_called()
        click.assert_not_called()

    def test_the_cursor_over_the_window_counts_too(self):
        monitor = self._monitor()
        self._spam(monitor, front=0x99, cursor_in=True)
        self.assertTrue(monitor.st.begin_done)

    def test_behind_with_the_cursor_outside_is_ignored_as_before(self):
        """窓4 05:45: 背面でカーソルも外の連打中に来た定期は受理しない"""
        monitor = self._monitor()
        press = self._spam(monitor, front=0x99, cursor_in=False)
        self.assertTrue(press.called, "連打はしている")
        self.assertFalse(monitor.st.begin_done)
        self.assertIn("[窓2] Verified を無視（ツールがまだ押していない → 定期）", monitor.logs)

    def test_the_reach_is_checked_every_half_second(self):
        monitor = self._monitor()
        clock = [time.time()]

        class Stop:
            n = 0

            def is_set(self):
                return self.n >= 40

            def wait(self, sec):
                self.n += 1
                clock[0] += sec
        monitor.st.round_over_time = clock[0] - 60
        with patch.object(ActionExecutor.time, "time", side_effect=lambda: clock[0]), \
             patch.object(monitor._action, "_use_right_reaches_begin", return_value=False) as reach, \
             patch.object(OSCClient.OSCClient, "press", return_value=True):
            monitor._action._spam_use_right_loop(Stop(), monitor.st.round_seq)
        self.assertEqual(reach.call_count, 2, "40 回 × 0.025 秒 = 1 秒で 2 回")

    def test_the_reach_helper(self):
        ex = self._monitor(hwnd=0x22)._action
        for front, inside, expected in ((0x22, False, True), (0x99, True, True), (0x99, False, False)):
            with patch.object(WindowOperator, "foreground_hwnd", return_value=front), \
                 patch.object(WindowOperator, "cursor_in_client", return_value=inside):
                self.assertEqual(ex._use_right_reaches_begin(), expected, (front, inside))

    def test_cursor_in_client(self):
        with patch.object(WindowOperator, "cursor_position", return_value=(150, 120)), \
             patch.object(WindowOperator.win32gui, "IsIconic", return_value=False), \
             patch.object(WindowOperator.win32gui, "GetClientRect", return_value=(0, 0, 100, 50)), \
             patch.object(WindowOperator.win32gui, "ClientToScreen", return_value=(100, 100)):
            self.assertTrue(WindowOperator.cursor_in_client(0x22))
        for point in ((99, 120), (200, 120), (150, 150)):
            with patch.object(WindowOperator, "cursor_position", return_value=point), \
                 patch.object(WindowOperator.win32gui, "IsIconic", return_value=False), \
                 patch.object(WindowOperator.win32gui, "GetClientRect", return_value=(0, 0, 100, 50)), \
                 patch.object(WindowOperator.win32gui, "ClientToScreen", return_value=(100, 100)):
                self.assertFalse(WindowOperator.cursor_in_client(0x22), point)
        with patch.object(WindowOperator, "cursor_position", return_value=(150, 120)), \
             patch.object(WindowOperator.win32gui, "IsIconic", return_value=True), \
             patch.object(WindowOperator.win32gui, "GetClientRect", return_value=(0, 0, 100, 50)), \
             patch.object(WindowOperator.win32gui, "ClientToScreen", return_value=(100, 100)):
            self.assertFalse(WindowOperator.cursor_in_client(0x22), "最小化（矩形の中でも）")




class TestRoundTypeObservation(unittest.TestCase):
    """BG の観測（ToN_RoundType とラウンド名の対応づくり）は、番号の一覧が取れたので外した。
    DebugLog と LogMonitor._debug は残す（未知のラウンド名の行で使う）"""

    def setUp(self):
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self, path=None):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9000, osc_out_port=9001,
                                                     log_path=path), {},
                                        lambda _m: None, window_idx=3)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def test_the_receiver_no_longer_watches_round_type(self):
        self.assertFalse(hasattr(OSCReceiver, "WATCHED_PARAMS"))
        with self.assertRaises(TypeError):
            OSCReceiver.VelocityReceiver(0, on_param=lambda n, v: None)
        r = OSCReceiver.VelocityReceiver(0)
        r._handle("/avatar/parameters/ToN_RoundType", 5)     # 捨てる（落ちない）
        r._handle(OSCReceiver.VELOCITY_MAGNITUDE, 4.0)
        r._handle(OSCReceiver.GROUNDED, False)
        self.assertEqual(r.speed, 4.0)
        self.assertFalse(r.grounded)
        self.assertEqual(self.written, [])

    def test_the_executor_is_not_wired_to_the_debug_log(self):
        monitor = self._monitor()
        with patch.object(OSCReceiver, "VelocityReceiver") as receiver:
            receiver.return_value.start.return_value = True
            monitor._action.start_velocity_receiver()
        self.assertNotIn("on_param", receiver.call_args.kwargs)

    def test_a_round_start_writes_nothing(self):
        monitor = self._monitor()
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("2026.09.30 13:00:00 Debug      -  This round is taking place "
                             "at Facility (12) and the round type is Classic")

        # 読んだイベントは [事象] で書く。ラウンド種別の観測の行（round type =）は書かない
        self.assertFalse(any("round type =" in m for m in self.written), self.written)
        self.assertTrue(all("[事象]" in m for m in self.written), self.written)
        self.assertFalse(any("round type =" in m for m in monitor.logs), monitor.logs)

    def test_debug_never_calls_the_public_logger(self):
        monitor = self._monitor()
        monitor.logger = MagicMock()

        monitor._debug("secret")

        monitor.logger.assert_not_called()
        self.assertEqual(self.written, ["[窓3] secret"])

    def test_the_start_scan_does_not_write(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("\n".join("2026.09.30 13:00:00 Debug      -  " + line for line in (
            "User Authenticated: a (usr_0e01408a)",
            "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
            "This round is taking place at Facility (12) and the round type is Classic",
        )) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = self._monitor(Path(tmp.name))
        ItemCatalog.take_load_problem()     # item.json が無い案内（1回だけ出る）は前のテストのもの
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None):
            monitor._detect_instance_from_log()

        # 書いてよいのは定期 Verified の位相の1行だけ。ラウンドの行は書かない
        self.assertFalse(any("round type" in m for m in self.written), self.written)
        self.assertEqual(len(self.written), 1, self.written)
        self.assertIn("定期 Verified の位相", self.written[0])


class TestStopReleasesHolds(unittest.TestCase):
    """マクロを止めたら、押している最中の移動（Begin 前の移動など）もその場で離す"""

    def test_osc_hold_releases_on_stop(self):
        client = OSCClient.OSCClient(9000)
        sent = []
        client.send = lambda address, value: sent.append((address, value)) or True
        clock = {"t": 0.0}
        stopped = {"on": False}

        def sleep(sec):
            clock["t"] += sec
            if clock["t"] >= 0.3:
                stopped["on"] = True                    # 0.3秒で止められた
        with patch.object(OSCClient.time, "time", side_effect=lambda: clock["t"]), \
             patch.object(OSCClient.time, "sleep", side_effect=sleep):
            client.press_multi([("/input/MoveForward", 2.0), ("/input/MoveLeft", 1.0)],
                               stop=lambda: stopped["on"])
        self.assertLess(clock["t"], 0.5, "2秒待たない")
        self.assertIn(("/input/MoveForward", 0), sent)
        self.assertIn(("/input/MoveLeft", 0), sent)
        self.assertEqual(set(sent[-2:]), {("/input/MoveForward", 0), ("/input/MoveLeft", 0)},
                         "最後は両方を離す")

    def test_the_begin_move_uses_the_stop(self):
        running = {"on": True}
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1, osc_port=9000), WindowState(),
                                           lambda: running["on"], lambda _m: None)
        with patch.object(ex._osc, "press_multi") as multi, patch.object(ex._osc, "stop_all"):
            ex.move_forward_left(2.0, 0.1)
        stop = multi.call_args.kwargs["stop"]
        self.assertFalse(stop())
        running["on"] = False
        self.assertTrue(stop())

    def test_the_begin_precheck_stops_even_while_waiting_for_equip(self):
        st = WindowState(waiting_for_equip=True)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1), st, lambda: False, lambda _m: None)
        self.assertFalse(ex._begin_precheck())
        self.assertFalse(ex._begin_precheck(check_freeze=False))
