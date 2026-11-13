"""手に持っている物と、その間の手のクリックの記録（ItemUse）。消費系・Maxwell のロストは test_item_consumed.py。
本物の OSC・入力・マウスフック・窓は使わない（support の偽物と、このファイルの偽のソケットだけ）"""
import ctypes
import types

from tests.support import *  # noqa: F401,F403
import ItemUse
import tests.test_mouse_buttons as _mb              # マウスフックの土台を借りる（ここでは回さない）
import tests.test_tab_click as _tab                 # keyboard・pydirectinput の偽物を借りる
import tests.test_ui_tidy as _ui_tidy               # 本物の App を立てる土台を借りる

CHARM = "BloodKatCharm"
PICK = ("2026.11.11 09:20:38 Debug      -  [Behaviour] Pickup object: 'BloodKatCharm' equipped = True, "
        "is AutoEquipType Pickup = True, last input method = Mouse, is AutoHold is enabled for this controller type")
DROP = ("2026.11.11 09:20:38 Debug      -  [Behaviour] Drop object: 'BloodKatCharm, was equipped = True' "
        "Throw on release, last input method = Mouse")
JOIN = ("2026.11.11 09:21:00 Debug      -  [Behaviour] Joining wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd:12345"
        "~private(usr_00000000-0000-0000-0000-000000000000)~region(jp)")
DIED = "2026.11.11 09:22:00 Debug      -  You died."
RESPAWN = "2026.11.11 09:22:05 Debug      -  Player respawned, opted out!"




# ── A LogParser ────────────────────────────────
class TestParsePickupDrop(unittest.TestCase):
    def test_pickup_line(self):
        event = LogParser.parse(PICK)
        self.assertEqual((event.kind, event.pickup_name), (LogParser.EVENT_PICKUP, CHARM))

    def test_drop_line(self):
        event = LogParser.parse(DROP)
        self.assertEqual((event.kind, event.pickup_name), (LogParser.EVENT_DROP, CHARM))

    def test_equipped_false_is_not_read(self):
        self.assertIsNone(LogParser.parse(PICK.replace("equipped = True", "equipped = False")))
        self.assertIsNone(LogParser.parse(DROP.replace("was equipped = True", "was equipped = False")))

    def test_a_line_joined_with_an_earlier_timestamp_is_read(self):
        joined = "2026.11.11 09:20:37 Debug      -  " + PICK
        self.assertEqual(LogParser.parse(joined).kind, LogParser.EVENT_PICKUP)
        self.assertEqual(LogParser.parse("2026.11.11 09:20:37 Debug      -  " + DROP).kind, LogParser.EVENT_DROP)

    def test_other_behaviour_lines_are_read_as_before(self):
        join = LogParser.parse("2026.11.11 09:20:00 Debug      -  [Behaviour] Joining wrld_abc:123~region(jp)")
        self.assertEqual(join.kind, LogParser.EVENT_JOINING)
        joined = LogParser.parse("2026.11.11 09:20:00 Debug      -  [Behaviour] OnPlayerJoined Alice "
                                 "(usr_00000000-0000-0000-0000-000000000001)")
        self.assertEqual(joined.kind, LogParser.EVENT_PLAYER_JOINED)
        self.assertIsNone(LogParser.parse("2026.11.11 09:20:00 Debug      -  [Behaviour] Something else"))

    def test_the_default_name_is_empty(self):
        self.assertEqual(LogParser.LogEvent(LogParser.EVENT_JOINING).pickup_name, "")

    def test_the_start_scan_does_not_look_for_pickups(self):
        for mark in LogMonitor.LogMonitor._START_SCAN_MARKS:
            self.assertNotIn("Pickup", mark)
            self.assertNotIn("Drop", mark)



# ── B UseTracker ───────────────────────────────
class TestNorm(unittest.TestCase):
    def test_spaces_and_case_are_ignored(self):
        for name in ("maxwell", "Maxwell", "MAXWELL", "max well", " Max\twell\n"):
            self.assertEqual(ItemUse.norm(name), "maxwell", repr(name))

    def test_none_and_empty_are_empty(self):
        self.assertEqual(ItemUse.norm(None), "")
        self.assertEqual(ItemUse.norm(""), "")
        self.assertEqual(ItemUse.norm("   "), "")


class TestUseTracker(unittest.TestCase):
    def setUp(self):
        self.tr = ItemUse.UseTracker(1)

    # 追跡
    def test_nothing_is_held_at_first(self):
        self.assertEqual(self.tr.held_name(), "")

    def test_any_name_is_held(self):
        for name in (CHARM, "Maxwell", "SomethingElse"):
            tr = ItemUse.UseTracker(1)
            tr.pickup(name)
            self.assertEqual(tr.held_name(), name)

    def test_the_same_pickup_again_does_nothing(self):
        self.tr.pickup(CHARM)
        self.tr.take_debug_lines()
        self.tr.pickup(CHARM)
        self.assertEqual(self.tr.take_debug_lines(), [])
        self.assertEqual(self.tr.held_name(), CHARM)

    def test_a_pickup_of_another_name_replaces_and_writes_both_lines(self):
        self.tr.pickup(CHARM)
        self.tr.take_debug_lines()
        self.tr.pickup("Maxwell")
        self.assertEqual(self.tr.held_name(), "Maxwell")
        self.assertEqual(self.tr.take_debug_lines(), [f"離した: {CHARM}", "持った: Maxwell"])

    def test_only_a_drop_of_the_same_name_releases(self):
        self.tr.pickup(CHARM)
        self.tr.drop("Maxwell")
        self.assertEqual(self.tr.held_name(), CHARM)
        self.tr.drop(CHARM)
        self.assertEqual(self.tr.held_name(), "")
        self.tr.drop(CHARM)                       # 持っていないときの Drop は何もしない
        self.assertEqual(self.tr.held_name(), "")

    def test_reset_releases_and_writes_the_reason_only_when_held(self):
        self.tr.reset("テスト")
        self.assertEqual(self.tr.take_debug_lines(), [])
        self.tr.pickup(CHARM)
        self.tr.take_debug_lines()
        self.tr.reset("テスト")
        self.assertEqual(self.tr.held_name(), "")
        self.assertEqual(self.tr.take_debug_lines(), ["持っていない: テスト"])

    def test_pickup_then_drop_lines(self):
        self.tr.pickup(CHARM)
        self.tr.drop(CHARM)
        self.assertEqual(self.tr.take_debug_lines(), [f"持った: {CHARM}", f"離した: {CHARM}"])

    # クリック
    def test_a_click_while_holding_is_true_and_without_holding_is_false(self):
        self.assertFalse(self.tr.use(ItemUse.SOURCE_HAND))
        self.assertIsNone(self.tr.take_use())
        self.tr.pickup(CHARM)
        self.assertTrue(self.tr.use(ItemUse.SOURCE_HAND))

    def test_take_use_returns_the_held_name_once(self):
        self.tr.pickup("Maxwell")
        self.tr.use(ItemUse.SOURCE_HAND)
        self.assertEqual(self.tr.take_use(), "Maxwell")
        self.assertIsNone(self.tr.take_use())

    def test_a_second_click_while_the_mark_remains_does_not_overwrite_it(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.pickup("Maxwell")                 # 置き換えても、取り出すまで印は残る
        self.tr.use(ItemUse.SOURCE_HAND)          # 残っている間の 2 回目（上書きしない）
        self.tr.use(ItemUse.SOURCE_HAND)
        self.assertEqual(self.tr.take_use(), CHARM)
        self.assertIsNone(self.tr.take_use(), "2 回分にはならない")

    def test_the_mark_remembers_the_name_held_at_the_click(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.assertEqual(self.tr.take_use(), CHARM)

    def test_a_drop_keeps_the_mark(self):
        """使うと無くなる物は、クリックの直後に Drop の行が来る。取り出す前に離しても印は残る"""
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.drop(CHARM)
        self.assertEqual(self.tr.held_name(), "")
        self.assertEqual(self.tr.take_use(), CHARM)
        self.assertIsNone(self.tr.take_use())

    def test_a_pickup_of_another_name_keeps_the_mark(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.pickup("Other")
        self.assertEqual(self.tr.take_use(), CHARM)

    def test_a_reset_clears_the_mark(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.reset("テスト")
        self.tr.pickup(CHARM)
        self.assertIsNone(self.tr.take_use())

    def test_a_pickup_of_the_same_name_keeps_the_mark(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.pickup(CHARM)
        self.assertEqual(self.tr.take_use(), CHARM)

    def test_a_burst_of_clicks_writes_no_debug_lines(self):
        self.tr.pickup(CHARM)
        self.tr.take_debug_lines()
        for _ in range(500):
            self.tr.use(ItemUse.SOURCE_HAND)
        self.assertEqual(self.tr.take_debug_lines(), [])

    def test_taking_the_lines_empties_them_and_they_are_capped(self):
        for i in range(ItemUse._DEBUG_LINES_MAX + 50):
            self.tr.pickup(f"item{i}")
        lines = self.tr.take_debug_lines()
        self.assertEqual(len(lines), ItemUse._DEBUG_LINES_MAX)
        self.assertEqual(self.tr.take_debug_lines(), [])

    def test_concurrent_clicks_leave_one_mark(self):
        self.tr.pickup(CHARM)
        threads = [threading.Thread(target=lambda: [self.tr.use(ItemUse.SOURCE_HAND) for _ in range(200)])
                   for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(self.tr.take_use(), CHARM)
        self.assertIsNone(self.tr.take_use())

    def test_the_constants(self):
        self.assertEqual(ItemUse.SOURCE_HAND, "手")


# ── C 登録表 ───────────────────────────────────
class TestRegistry(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.tr = ItemUse.UseTracker(1)
        self.tr.pickup(CHARM)

    def test_a_click_on_a_registered_window_is_a_use(self):
        ItemUse.register(0x40, self.tr)
        self.assertIs(ItemUse.tracker_for(0x40), self.tr)
        self.assertTrue(ItemUse.on_click(0x40, ItemUse.SOURCE_HAND))
        self.assertEqual(self.tr.take_use(), CHARM)

    def test_unregistered_and_zero_do_nothing(self):
        ItemUse.register(0x40, self.tr)
        self.assertIsNone(ItemUse.on_click(0x41, ItemUse.SOURCE_HAND))
        self.assertIsNone(ItemUse.on_click(0, ItemUse.SOURCE_HAND))
        self.assertIsNone(self.tr.take_use())
        ItemUse.register(0, self.tr)
        self.assertIsNone(ItemUse.tracker_for(0))

    def test_unregister_removes_only_the_same_tracker(self):
        other = ItemUse.UseTracker(2)
        ItemUse.register(0x40, self.tr)
        ItemUse.unregister(0x40, other)
        self.assertIs(ItemUse.tracker_for(0x40), self.tr)
        ItemUse.unregister(0x40, self.tr)
        self.assertIsNone(ItemUse.tracker_for(0x40))

    def test_reset_for_tests(self):
        ItemUse.register(0x40, self.tr)
        ItemUse.reset_for_tests()
        self.assertIsNone(ItemUse.tracker_for(0x40))


# ── D MouseButtons（左ボタン）────────────────────
@unittest.skipUnless(sys.platform == "win32", "Windows のフック")
class TestLeftButtonHook(_mb._MouseCase):
    def setUp(self):
        super().setUp()
        self.fake = MagicMock()
        self.fake.CallNextHookEx.return_value = 7
        p = patch.object(MouseButtons, "_user32", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.left, self.side = [], []
        MouseButtons.hook_left(self.left.append)
        MouseButtons.hook(self.side.append)

    def _call(self, wparam, flags=0):
        struct = MouseButtons.MSLLHOOKSTRUCT()
        struct.flags = flags
        return MouseButtons._proc(0, wparam, ctypes.addressof(struct))

    def test_the_constants(self):
        self.assertEqual(MouseButtons.WM_LBUTTONDOWN, 0x0201)
        self.assertEqual(MouseButtons.LLMHF_INJECTED, 1)

    def test_a_left_down_reaches_only_the_left_subscribers_and_is_passed_on(self):
        self.assertEqual(self._call(MouseButtons.WM_LBUTTONDOWN), 7)
        self.assertEqual(len(self.left), 1)
        self.assertEqual(self.side, [], "hook() の購読には流さない")
        self.fake.CallNextHookEx.assert_called_once()

    def test_an_injected_click_is_not_delivered(self):
        self.assertEqual(self._call(MouseButtons.WM_LBUTTONDOWN, MouseButtons.LLMHF_INJECTED), 7)
        self.assertEqual(self.left, [])
        self.assertEqual(self._call(MouseButtons.WM_LBUTTONDOWN, 0x20 | MouseButtons.LLMHF_INJECTED), 7)
        self.assertEqual(self.left, [])

    def test_left_up_and_move_are_not_delivered(self):
        for wparam in (0x0202, 0x0200):
            self.assertEqual(self._call(wparam), 7)
        self.assertEqual(self.left, [])

    def test_a_negative_ncode_is_not_delivered(self):
        struct = MouseButtons.MSLLHOOKSTRUCT()
        self.assertEqual(MouseButtons._proc(-1, MouseButtons.WM_LBUTTONDOWN, ctypes.addressof(struct)), 7)
        self.assertEqual(self.left, [])

    def test_a_subscriber_that_raises_does_not_stop_the_others(self):
        later = []
        MouseButtons.hook_left(lambda e: 1 / 0)
        MouseButtons.hook_left(later.append)
        with patch.object(DebugLog, "exception"):
            self.assertEqual(self._call(MouseButtons.WM_LBUTTONDOWN), 7)
        self.assertEqual(len(later), 1)


class TestLeftSubscriptions(_mb._MouseCase):
    def test_hook_unhook_and_reset(self):
        got = []
        handle = MouseButtons.hook_left(got.append)
        MouseButtons._on_left_down(0)
        self.assertEqual(len(got), 1)
        MouseButtons.unhook_left(handle)
        MouseButtons.unhook_left(handle)            # 二重に外しても何も起きない
        MouseButtons._on_left_down(0)
        self.assertEqual(len(got), 1)
        MouseButtons.hook_left(got.append)
        MouseButtons.reset_for_tests()
        self.assertEqual(MouseButtons._left_hooks, [])
        MouseButtons._on_left_down(0)
        self.assertEqual(len(got), 1)

    def test_the_side_buttons_do_not_reach_the_left_subscribers(self):
        got = []
        MouseButtons.hook_left(got.append)
        _mb.tap(1)
        self.assertEqual(got, [])

    def test_stop_clears_the_left_subscribers_too(self):
        MouseButtons.hook_left(print)
        MouseButtons._clear()
        self.assertEqual(MouseButtons._left_hooks, [])


class TestCaptureIgnoresLeftClicks(_mb._MouseCase):
    def setUp(self):
        super().setUp()
        kb = types.SimpleNamespace(callback=None, unhooked=[], KEY_DOWN="down", KEY_UP="up")

        def hook(callback, suppress=False):
            kb.callback = callback
            return "kb-hook"
        kb.hook = hook
        kb.unhook = kb.unhooked.append
        kb.get_hotkey_name = lambda names: "+".join(names)
        p = patch.object(HotKey, "keyboard", kb)
        p.start()
        self.addCleanup(p.stop)

    def test_left_clicks_during_a_capture_do_not_finish_it(self):
        MouseButtons.hook_left(lambda e: None)
        out = {}
        thread = threading.Thread(target=lambda: out.setdefault("key", HotKey.capture(3.0)), daemon=True)
        thread.start()
        self.assertTrue(_wait_until(lambda: len(MouseButtons._hooks) == 1))
        MouseButtons._on_left_down(0)
        MouseButtons._on_left_down(0)
        self.assertTrue(thread.is_alive(), "左クリックでは捕捉は終わらない")
        _mb.tap(2)
        thread.join(5.0)
        self.assertEqual(out["key"], "mouse:x2")

    def test_only_left_clicks_time_out(self):
        MouseButtons._on_left_down(0)
        self.assertIsNone(HotKey.capture(0.1))


# ── E mainGUI ──────────────────────────────────
class TestHandClick(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.tr = ItemUse.UseTracker(1)
        self.tr.pickup(CHARM)
        ItemUse.register(0x50, self.tr)

    def test_the_front_registered_window_gets_the_click(self):
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x50):
            mainGUI._on_hand_left_down()
            mainGUI._on_hand_left_down(types.SimpleNamespace(name="mouse:left", event_type="down"))
        self.assertEqual(self.tr.take_use(), CHARM)
        self.assertIsNone(self.tr.take_use())

    def test_another_front_window_does_nothing(self):
        for hwnd in (0x51, 0):
            with patch.object(WindowOperator, "foreground_hwnd", return_value=hwnd):
                mainGUI._on_hand_left_down()
        self.assertIsNone(self.tr.take_use())


class TestStartupAndCloseOfHandHook(_mb._MouseCase):
    def _app(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        for name in ("_hook_chase_keys", "_hook_move_keys", "_hook_suicide_cancel_key", "_hook_stop_start_keys"):
            setattr(app, name, lambda: None)
        return app

    def test_start_hooks_the_left_button_once(self):
        app = self._app()
        with patch.object(MouseButtons, "start", return_value=True):
            mainGUI.App._start_emergency_stop_polling(app)
        self.assertEqual(len(MouseButtons._left_hooks), 1)
        self.assertIs(app._hand_click_hook, MouseButtons._left_hooks[0])
        self.assertIs(app._hand_click_hook.callback, mainGUI._on_hand_left_down)
        self.assertEqual(MouseButtons._hooks, [], "左ボタンは hook() の購読に混ぜない")
        self.assertEqual(app.logs, [])

    def test_without_the_mouse_hook_the_screen_log_does_not_grow(self):
        app = self._app()
        with patch.object(MouseButtons, "start", return_value=False), \
             patch.object(DebugLog, "write") as written:
            mainGUI.App._start_emergency_stop_polling(app)
        self.assertEqual(app.logs, ["[キー] ⚠ マウスのサイドボタンは使えません（フックを張れませんでした）"],
                         "画面のログは今までの 1 行だけ")
        self.assertTrue(any("[アイテム]" in c.args[0] for c in written.call_args_list))

    def test_closing_removes_the_left_hook_before_stopping_the_mouse_hook(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.logs = []
        app._log = app.logs.append
        app.order = []
        app._save_settings_now = lambda: None
        app._stop = lambda: None
        app.destroy = lambda: app.order.append("destroy")
        app._stop_window_volume = lambda: None
        app._unhook_chase_keys = lambda: None
        app._unhook_move_keys = lambda: None
        app._unhook_stop_start_keys = lambda: None
        app._show_own_windows_again = lambda: None
        app._hand_click_hook = MouseButtons.hook_left(print)
        with patch.object(MouseButtons, "stop",
                          lambda: app.order.append(("mouse", len(MouseButtons._left_hooks)))):
            mainGUI.App._on_close(app)
        self.assertEqual(app.order, [("mouse", 0), "destroy"])
        self.assertIsNone(app._hand_click_hook)

    def test_closing_without_a_hand_hook_still_works(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app._log = lambda m: None
        app._save_settings_now = lambda: None
        app._stop = lambda: None
        app.destroy = lambda: None
        app._stop_window_volume = lambda: None
        for name in ("_unhook_chase_keys", "_unhook_move_keys", "_unhook_stop_start_keys",
                     "_show_own_windows_again"):
            setattr(app, name, lambda: None)
        mainGUI.App._on_close(app)


class TestOverlayHasNoItemPart(_ui_tidy._AppCase):
    def test_the_overlay_is_made_from_the_parent_only_and_the_log_part_works(self):
        overlay = mainGUI.LogOverlay(self.app)
        self.addCleanup(lambda: overlay.winfo_exists() and overlay.close())
        for name in ("_items", "_item_job", "_item_lines", "set_item_lines", "_refresh_items"):
            self.assertFalse(hasattr(overlay, name), name)
        overlay.append_many(["a", "b"])
        self.assertEqual(overlay.text.get("1.0", "end").strip(), "a\nb")
        overlay.close()
        self.assertTrue(overlay._closed)

    def test_toggle_overlay_makes_it_from_the_app_only(self):
        app = types.SimpleNamespace(_overlay=None)
        with patch.object(mainGUI, "LogOverlay") as overlay:
            mainGUI.App._toggle_overlay(app)
        overlay.assert_called_once_with(app)
        self.assertIs(app._overlay, overlay.return_value)


class TestLogMonitorItemUse(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.logs = []
        self.monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x31), {}, self.logs.append, window_idx=1)
        self.tracker = self.monitor.st.item_use

    def _held(self):
        return self.tracker.held_name() != ""

    def test_the_tracker_is_made_for_the_window(self):
        self.assertIsInstance(self.tracker, ItemUse.UseTracker)
        self.assertEqual(self.tracker.window_idx, 1)

    def test_pickup_and_drop_lines(self):
        self.monitor._process(PICK)
        self.assertTrue(self._held())
        self.monitor._process(DROP)
        self.assertFalse(self._held())

    def test_any_item_name_is_held(self):
        self.monitor._process(PICK.replace("BloodKatCharm", "SomethingElse"))
        self.assertEqual(self.tracker.held_name(), "SomethingElse")

    def test_losing_the_item_resets(self):
        for lose in (lambda: self.monitor._lose_held_item("テスト"),
                     lambda: self.monitor._mark_item_lost("テスト: アイテムロスト")):
            with self.subTest(lose=lose):
                self.monitor._process(PICK)
                self.assertTrue(self._held())
                lose()
                self.assertFalse(self._held())

    def test_losing_clears_the_click_mark_too(self):
        self.monitor._process(PICK)
        self.tracker.use(ItemUse.SOURCE_HAND)
        self.monitor._lose_held_item("テスト")
        self.monitor._process(PICK)
        self.assertIsNone(self.tracker.take_use())

    def test_joining_resets(self):
        self.monitor._process(PICK)
        self.monitor._process(JOIN)
        self.assertFalse(self._held())

    def test_death_and_respawn_alone_keep_it(self):
        self.monitor._process(PICK)
        self.monitor._process(DIED)
        self.assertTrue(self._held())
        self.monitor._process(RESPAWN)
        self.assertTrue(self._held())

    def test_pickup_and_drop_are_not_written_as_events(self):
        with patch.object(DebugLog, "write") as written:
            self.monitor._process(PICK)
            self.monitor._process(DROP)
        self.assertEqual([c.args[0] for c in written.call_args_list], [], "flush までは debug.log に書かない")

    def test_the_screen_log_never_gets_item_lines(self):
        with patch.object(DebugLog, "write"):
            self.monitor._process(PICK)
            self.tracker.use(ItemUse.SOURCE_HAND)
            self.monitor._process(DROP)
            self.monitor._flush_item_use_log()
        self.assertEqual([m for m in self.logs if CHARM in m or "アイテム]" in m], [])

    def test_flush_writes_the_collected_lines_to_the_debug_log_with_the_window(self):
        with patch.object(DebugLog, "write") as written:
            self.monitor._process(PICK)
            self.monitor._flush_item_use_log()
            self.monitor._flush_item_use_log()          # 二重に書かない
        self.assertEqual([c.args[0] for c in written.call_args_list], ["[窓1] [アイテム] 持った: BloodKatCharm"])

    def test_the_poll_loop_flushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output_log.txt"
            path.write_text("", encoding="utf-8")
            monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x32, log_path=path), {}, self.logs.append,
                                            window_idx=1)
            monitor.st.item_use.pickup(CHARM)
            monitor._running = True
            monitor._stop_event.set()                   # 1 周で抜ける
            with patch.object(DebugLog, "write") as written:
                monitor._run()
        self.assertIn("[窓1] [アイテム] 持った: BloodKatCharm", [c.args[0] for c in written.call_args_list])

    def _start(self):
        patches = (patch.object(self.monitor, "_start_daemon", return_value=None),
                   patch.object(self.monitor._action, "start_velocity_receiver"),
                   patch.object(self.monitor._action, "stop_velocity_receiver"))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.monitor.start()

    def test_start_registers_and_stop_unregisters_and_resets(self):
        self._start()
        self.assertIs(ItemUse.tracker_for(0x31), self.tracker)
        self.monitor._process(PICK)
        self.assertTrue(self._held())
        with patch.object(DebugLog, "write") as written:
            self.monitor.stop()
        self.assertIsNone(ItemUse.tracker_for(0x31))
        self.assertFalse(self._held())
        self.assertIn("[窓1] [アイテム] 持っていない: 監視の停止", [c.args[0] for c in written.call_args_list])

    def test_a_click_reaches_the_monitor_through_the_registry(self):
        self._start()
        self.monitor._process(PICK)
        ItemUse.on_click(0x31, ItemUse.SOURCE_HAND)
        self.assertEqual(self.tracker.take_use(), CHARM)
        self.monitor.stop()

    def test_stopping_an_unstarted_monitor_is_safe(self):
        self.monitor.stop()
        self.assertFalse(self._held())

# ── G ツール自身の入力は数えない ──────────────────
class _FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(bytes(data))
        return len(data)

    def close(self):
        pass


class TestToolInputsAreNotCounted(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.tracker = ItemUse.UseTracker(1)
        self.tracker.pickup(CHARM)
        ItemUse.register(0x77, self.tracker)

    def test_sending_use_right_by_osc_does_not_mark_a_click(self):
        st = WindowState()
        st.item_use = self.tracker
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x77, osc_port=9000), st, lambda: True,
                                           lambda _m: None)
        ex._osc._sock = _FakeSocket()                   # 本物のソケットには送らない
        self.addCleanup(ex._osc.close)
        with patch.object(OSCClient, "time", _module_time(lambda s: None)):
            ex._osc.press("/input/UseRight", 0.2)
        self.assertGreater(len(ex._osc._sock.sent), 2, "0・1・0 を送った")
        self.assertIsNone(self.tracker.take_use())

    def test_the_osc_client_takes_no_send_callback(self):
        client = OSCClient.OSCClient(9000)
        client._sock = _FakeSocket()
        self.assertTrue(client.send("/input/UseRight", 1))
        client.close()
        with self.assertRaises(TypeError):
            OSCClient.OSCClient(9000, on_send=lambda a, v: None)

    def test_a_failed_osc_send_returns_false(self):
        client = OSCClient.OSCClient(9000)
        client._sock = MagicMock()
        client._sock.sendto.side_effect = OSError("no route")
        with patch.object(DebugLog, "exception"):
            self.assertFalse(client.send("/input/UseRight", 1))

    def test_a_tool_click_does_not_mark_a_click(self):
        inputs = _tab._Inputs(self)
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x77):
            self.assertTrue(WindowOperator.click_with_tab(0.1))
            WindowOperator.mouse_click(0.1)
            WindowOperator.mouse_down()
            WindowOperator.mouse_up()
        self.assertIn(("down", False), inputs.calls, "クリックそのものは送られている")
        self.assertIsNone(self.tracker.take_use())
