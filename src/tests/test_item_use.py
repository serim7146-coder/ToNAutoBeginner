"""手に持ったアイテムの使用の判定（ItemUse。段階 1: Bloodstained Charm のクールタイム・効果の表示）。
本物の OSC・入力・マウスフック・窓は使わない（support の偽物と、このファイルの偽の時計・偽のソケットだけ）"""
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


def _tracker(t, players=None):
    return ItemUse.UseTracker(1, clock=lambda: t[0], players=players)


def _text(tracker, now=None):
    return ItemUse.status_text(1, tracker.status(now))


def _use_lines(tracker):
    return [line for line in tracker.take_debug_lines() if line.startswith("使用")]


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
class TestUseTracker(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.tr = _tracker(self.t)

    def test_not_holding(self):
        self.assertEqual(self.tr.use(ItemUse.SOURCE_HAND), "not_holding")
        self.assertIsNone(self.tr.status())
        self.assertEqual(ItemUse.status_text(1, self.tr.status()), "")

    def test_holding_is_ready(self):
        self.tr.pickup(CHARM)
        self.assertEqual(self.tr.status(), (CHARM, "Bloody Kat Charm", 0.0, 0.0))
        self.assertEqual(_text(self.tr), "窓1 Bloody Kat Charm  使用可")

    def test_timeline_of_one_use(self):
        self.tr.pickup(CHARM)
        self.assertEqual(self.tr.use(ItemUse.SOURCE_HAND), "used")
        self.assertEqual(_text(self.tr, 0.4), "窓1 Bloody Kat Charm  効果 1.6秒・CT 0.6秒")
        self.assertEqual(_text(self.tr, 1.2), "窓1 Bloody Kat Charm  効果 0.8秒・使用可")
        self.assertEqual(_text(self.tr, 2.0), "窓1 Bloody Kat Charm  使用可")

    def test_cooldown_only_state(self):
        """効果より CT が長い物は表に無いので、表示の書式だけを確かめる"""
        text = ItemUse.status_text(2, (CHARM, "Bloody Kat Charm", 0.0, 0.4))
        self.assertEqual(text, "窓2 Bloody Kat Charm  CT 0.4秒")

    def test_a_click_during_the_cooldown_is_not_a_use_and_does_not_extend_the_effect(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.t[0] = 0.5
        self.assertEqual(self.tr.use(ItemUse.SOURCE_HAND), "cooldown")
        status = self.tr.status()
        self.assertAlmostEqual(status[2], 1.5)          # 効果は 2.0 のまま（延びていない）
        self.assertAlmostEqual(status[3], 0.5)

    def test_exactly_at_the_end_of_the_cooldown_is_a_use(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.t[0] = 1.0
        self.assertEqual(self.tr.use(ItemUse.SOURCE_HAND), "used")

    def test_after_a_drop_it_is_not_holding(self):
        self.tr.pickup(CHARM)
        self.tr.drop(CHARM)
        self.assertEqual(self.tr.use(ItemUse.SOURCE_HAND), "not_holding")
        self.assertIsNone(self.tr.status())

    def test_pickup_then_drop_and_drop_then_pickup_at_the_same_time(self):
        self.tr.pickup(CHARM)
        self.tr.drop(CHARM)
        self.assertIsNone(self.tr.status())
        self.tr.drop(CHARM)
        self.tr.pickup(CHARM)
        self.assertIsNotNone(self.tr.status())

    def test_a_drop_of_another_name_does_nothing(self):
        self.tr.pickup(CHARM)
        self.tr.drop("Other")
        self.assertIsNotNone(self.tr.status())
        self.assertEqual(self.tr.take_debug_lines(), ["持った: BloodKatCharm"])

    def test_an_unknown_item_is_not_held_and_writes_nothing(self):
        self.tr.pickup("Some Player's Thing")
        self.assertIsNone(self.tr.status())
        self.assertEqual(self.tr.take_debug_lines(), [])

    def test_picking_up_an_unknown_item_replaces_the_charm(self):
        self.tr.pickup(CHARM)
        self.tr.take_debug_lines()
        self.tr.pickup("Other")
        self.assertIsNone(self.tr.status())
        self.assertEqual(self.tr.take_debug_lines(), ["離した: BloodKatCharm"])

    def test_a_pickup_without_a_drop_replaces(self):
        table = ItemUse.TABLE + (ItemUse.UseItem(ItemUse.KIND_CLICK_CD, "Second", "Second Item", 5.0, 0.0, 1.0),)
        with patch.object(ItemUse, "TABLE", table):
            self.tr.pickup(CHARM)
            self.tr.pickup("Second")
            self.assertEqual(self.tr.status()[0], "Second")
            self.assertEqual(self.tr.take_debug_lines(),
                             ["持った: BloodKatCharm", "離した: BloodKatCharm", "持った: Second"])
            self.tr.drop(CHARM)                     # 前のものの Drop は何もしない
            self.assertEqual(self.tr.status()[0], "Second")

    def test_reset_then_pickup_is_ready(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.reset("アイテムロスト")
        self.assertIsNone(self.tr.status())
        self.tr.pickup(CHARM)
        self.assertEqual(self.tr.status(), (CHARM, "Bloody Kat Charm", 0.0, 0.0))
        self.assertIn("持っていない: アイテムロスト", self.tr.take_debug_lines())

    def test_reset_when_not_holding_writes_nothing(self):
        self.tr.reset("インスタンス移動")
        self.assertEqual(self.tr.take_debug_lines(), [])

    def test_picking_it_up_again_during_the_cooldown_continues_it(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.drop(CHARM)
        self.t[0] = 0.3
        self.tr.pickup(CHARM)
        self.assertAlmostEqual(self.tr.status()[3], 0.7)
        self.assertEqual(self.tr.use(ItemUse.SOURCE_HAND), "cooldown")

    def test_a_kind_other_than_click_cd_is_not_usable(self):
        table = (ItemUse.UseItem(ItemUse.KIND_HOLD, "Held", "Held Item", 1.0, 0.0, 1.0),)
        with patch.object(ItemUse, "TABLE", table):
            self.tr.pickup("Held")
            self.assertEqual(self.tr.use(ItemUse.SOURCE_HAND), "not_usable")
        self.assertEqual(self.tr.take_debug_lines(), ["持った: Held"])

    def test_osc_press_state(self):
        self.tr.pickup(CHARM)
        self.tr.osc_use_input(False)                    # 押す前の 0
        self.assertEqual(self.tr.status()[2], 0.0)
        self.tr.osc_use_input(True)                     # 0→1 で使用
        self.assertEqual(len(_use_lines(self.tr)), 1)
        self.t[0] = 0.1
        self.tr.osc_use_input(True)                     # 1→1（送り直し）は数えない
        self.tr.osc_use_input(True)
        self.assertEqual(_use_lines(self.tr), [])
        self.assertEqual(self.tr._cd_presses, 0)
        self.tr.osc_use_input(False)
        self.t[0] = 1.5                                 # CT 明けにまた使用
        self.tr.osc_use_input(True)
        lines = _use_lines(self.tr)
        self.assertEqual(len(lines), 1)
        self.assertIn("OSC", lines[0])

    def test_osc_down_state_is_cleared_by_reset(self):
        self.tr.pickup(CHARM)
        self.tr.osc_use_input(True)
        self.tr.reset("アイテムロスト")
        self.tr.pickup(CHARM)
        self.tr.osc_use_input(True)                     # reset で戻っているので、これは押し始め
        self.assertEqual(len(_use_lines(self.tr)), 2)

    def test_a_burst_of_clicks(self):
        self.tr.pickup(CHARM)
        results = []
        for i in range(60):                             # 0.05 秒ごとに 3 秒
            self.t[0] = i * 0.05
            results.append(self.tr.use(ItemUse.SOURCE_HAND))
        self.assertEqual(results.count("used"), 3)
        lines = _use_lines(self.tr)
        self.assertEqual(len(lines), 3)
        self.assertNotIn("CT中の押下", lines[0])
        self.assertIn("（CT中の押下 19 回は数えず）", lines[1])
        self.assertIn("（CT中の押下 19 回は数えず）", lines[2])

    def test_the_use_line(self):
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_TOOL)
        self.assertEqual(self.tr.take_debug_lines(),
                         ["持った: BloodKatCharm", "使用: BloodKatCharm（ツール・CT 1.0秒・効果 2.0秒）"])

    def test_taking_the_lines_empties_them_and_they_are_capped(self):
        self.tr.pickup(CHARM)
        for _ in range(300):
            self.tr.drop(CHARM)
            self.tr.pickup(CHARM)
        lines = self.tr.take_debug_lines()
        self.assertEqual(len(lines), 100)
        self.assertEqual(self.tr.take_debug_lines(), [])

    def test_concurrent_uses_count_once(self):
        self.tr.pickup(CHARM)
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.tr.use(ItemUse.SOURCE_HAND)))
                   for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(2.0)
        self.assertEqual(sorted(results), ["cooldown"] * 7 + ["used"])

    def test_debug_lines_carry_no_coordinates_or_names(self):
        self.tr.pickup(CHARM)
        self.tr.pickup("usr_00000000-0000-0000-0000-000000000009")
        self.tr.pickup(CHARM)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.use(ItemUse.SOURCE_HAND)
        self.tr.drop(CHARM)
        self.tr.reset("アイテムロスト")
        text = "\n".join(self.tr.take_debug_lines())
        self.assertNotIn("usr_", text)
        self.assertNotRegex(text, r"\(\s*\d+\s*,\s*\d+\s*\)")


class TestCooldownByPlayers(unittest.TestCase):
    ROW = ItemUse.UseItem(ItemUse.KIND_CLICK_CD, "Scaled", "Scaled Item", 10.0, 2.0, 3.0)

    def _ct(self, players):
        t = [0.0]
        tr = _tracker(t, players=players)
        with patch.object(ItemUse, "TABLE", (self.ROW,)), patch.object(DebugLog, "exception"):
            tr.pickup("Scaled")
            tr.use(ItemUse.SOURCE_HAND)
            return tr.status(0.0)[3]

    def test_four_players(self):
        self.assertEqual(self._ct(lambda: 4), 16.0)

    def test_unknown_count_is_one_player(self):
        self.assertEqual(self._ct(lambda: None), 10.0)
        self.assertEqual(self._ct(None), 10.0)

    def test_an_error_falls_back_to_the_base(self):
        def boom():
            raise RuntimeError("x")
        self.assertEqual(self._ct(boom), 10.0)

    def test_cooldown_for(self):
        self.assertEqual(ItemUse.cooldown_for(self.ROW, 1), 10.0)
        self.assertEqual(ItemUse.cooldown_for(self.ROW, 4), 16.0)
        self.assertEqual(ItemUse.cooldown_for(self.ROW, None), 10.0)

    def test_the_charm_does_not_ask_for_the_count(self):
        asked = []
        t = [0.0]
        tr = _tracker(t, players=lambda: asked.append(1) or 5)
        tr.pickup(CHARM)
        tr.use(ItemUse.SOURCE_HAND)
        self.assertEqual(asked, [])
        self.assertEqual(tr.status(0.0)[3], 1.0)


class TestTable(unittest.TestCase):
    def test_names_are_unique_and_kinds_valid(self):
        names = [row.log_name for row in ItemUse.TABLE]
        self.assertEqual(len(names), len(set(names)))
        for row in ItemUse.TABLE:
            self.assertIn(row.kind, (ItemUse.KIND_CLICK_CD, ItemUse.KIND_HOLD, ItemUse.KIND_PASSIVE))

    def test_the_charm_row(self):
        row = ItemUse.item(CHARM)
        self.assertEqual((row.kind, row.cooldown_sec, row.cooldown_per_player, row.effect_sec),
                         (ItemUse.KIND_CLICK_CD, 1.0, 0.0, 2.0))
        self.assertIsNone(ItemUse.item("Nothing"))

    def test_click_cd_cooldowns_are_at_least_one_second(self):
        for row in ItemUse.TABLE:
            if row.kind == ItemUse.KIND_CLICK_CD:
                self.assertGreaterEqual(row.cooldown_sec, 1.0, row.log_name)

    def test_the_charm_display_name_is_the_shop_name(self):
        items = ItemCatalog.load_items(config.resource_path("assets/data/item.json"))
        self.assertEqual(ItemUse.item(CHARM).display_name, ItemCatalog.item_name(18, items))

    def test_the_sources_and_address(self):
        self.assertEqual((ItemUse.SOURCE_HAND, ItemUse.SOURCE_OSC, ItemUse.SOURCE_TOOL), ("手", "OSC", "ツール"))
        self.assertEqual(ItemUse.USE_ADDRESS, "/input/UseRight")


class TestStatusText(unittest.TestCase):
    def test_seconds_are_rounded_up_to_a_tenth(self):
        self.assertEqual(ItemUse.status_text(1, ("n", "Name", 0.04, 0.0)), "窓1 Name  効果 0.1秒・使用可")
        self.assertEqual(ItemUse.status_text(1, ("n", "Name", 0.0, 1.6000000000000001)), "窓1 Name  CT 1.6秒")
        self.assertEqual(ItemUse.status_text(1, ("n", "Name", 1.6000000000000001, 0.0)), "窓1 Name  効果 1.6秒・使用可")

    def test_a_tiny_remainder_counts_as_finished(self):
        self.assertEqual(ItemUse.status_text(3, ("n", "Name", 1e-7, 1e-7)), "窓3 Name  使用可")

    def test_nothing_held_is_empty(self):
        self.assertEqual(ItemUse.status_text(1, None), "")


# ── C 登録表 ───────────────────────────────────
class TestRegistry(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.t = [0.0]
        self.tr = _tracker(self.t)
        self.tr.pickup(CHARM)

    def test_a_click_on_a_registered_window_is_a_use(self):
        ItemUse.register(0x40, self.tr)
        self.assertIs(ItemUse.tracker_for(0x40), self.tr)
        ItemUse.on_click(0x40, ItemUse.SOURCE_HAND)
        self.assertEqual(len(_use_lines(self.tr)), 1)

    def test_unregistered_and_zero_do_nothing(self):
        ItemUse.register(0x40, self.tr)
        self.assertIsNone(ItemUse.on_click(0x41, ItemUse.SOURCE_HAND))
        self.assertIsNone(ItemUse.on_click(0, ItemUse.SOURCE_HAND))
        self.assertEqual(_use_lines(self.tr), [])
        ItemUse.register(0, self.tr)
        self.assertIsNone(ItemUse.tracker_for(0))

    def test_unregister_removes_only_the_same_tracker(self):
        other = _tracker(self.t)
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
        self.t = [0.0]
        self.tr = _tracker(self.t)
        self.tr.pickup(CHARM)
        ItemUse.register(0x50, self.tr)

    def test_the_front_registered_window_gets_the_use(self):
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x50):
            mainGUI._on_hand_left_down()
            mainGUI._on_hand_left_down(types.SimpleNamespace(name="mouse:left", event_type="down"))
        lines = _use_lines(self.tr)
        self.assertEqual(len(lines), 1)
        self.assertIn("手", lines[0])

    def test_another_front_window_does_nothing(self):
        for hwnd in (0x51, 0):
            with patch.object(WindowOperator, "foreground_hwnd", return_value=hwnd):
                mainGUI._on_hand_left_down()
        self.assertEqual(_use_lines(self.tr), [])


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


class TestOverlayItemLines(_ui_tidy._AppCase):
    def setUp(self):
        super().setUp()
        p = patch.object(config, "GUI_OVERLAY_ITEM_REFRESH_MS", 600000)     # 勝手に回らないように。更新は手で呼ぶ
        p.start()
        self.addCleanup(p.stop)
        self.lines = []

    def _overlay(self, item_lines="default"):
        overlay = mainGUI.LogOverlay(self.app, item_lines=(lambda: self.lines) if item_lines == "default"
                                     else item_lines)
        self.addCleanup(lambda: overlay.winfo_exists() and overlay.close())
        return overlay

    def test_no_lines_means_no_label(self):
        overlay = self._overlay()
        self.assertEqual(overlay._items.winfo_manager(), "")

    def test_without_item_lines_nothing_is_scheduled(self):
        overlay = self._overlay(None)
        self.assertEqual(overlay._items.winfo_manager(), "")
        self.assertIsNone(overlay._item_job)

    def test_lines_are_shown_joined_by_newlines_above_the_log(self):
        overlay = self._overlay()
        self.lines[:] = ["窓1 Bloody Kat Charm  使用可", "窓2 Bloody Kat Charm  CT 0.4秒"]
        overlay._refresh_items()
        self.assertEqual(overlay._items.winfo_manager(), "pack")
        self.assertEqual(overlay._items.cget("text"), "窓1 Bloody Kat Charm  使用可\n窓2 Bloody Kat Charm  CT 0.4秒")
        slaves = overlay.pack_slaves()
        self.assertLess(slaves.index(overlay._items), slaves.index(overlay.text))
        self.lines[:] = []
        overlay._refresh_items()
        self.assertEqual(overlay._items.winfo_manager(), "")

    def test_the_same_lines_are_not_set_again(self):
        overlay = self._overlay()
        self.lines[:] = ["窓1 X  使用可"]
        with patch.object(overlay._items, "config") as configured:
            overlay._refresh_items()
            overlay._refresh_items()
            overlay._refresh_items()
        configured.assert_called_once_with(text="窓1 X  使用可")
        self.lines[:] = ["窓1 X  CT 0.3秒"]
        with patch.object(overlay._items, "config") as configured:
            overlay._refresh_items()
        configured.assert_called_once_with(text="窓1 X  CT 0.3秒")

    def test_the_refresh_is_rescheduled_by_the_configured_interval(self):
        overlay = self._overlay()
        with patch.object(overlay, "after", return_value="job") as after:
            overlay._refresh_items()
        after.assert_called_once_with(600000, overlay._refresh_items)
        self.assertEqual(overlay._item_job, "job")

    def test_a_failing_provider_shows_nothing_and_keeps_going(self):
        def boom():
            raise RuntimeError("x")
        overlay = self._overlay(boom)
        with patch.object(DebugLog, "exception"):
            overlay._refresh_items()
        self.assertEqual(overlay._items.winfo_manager(), "")
        self.assertIsNotNone(overlay._item_job)

    def test_close_cancels_the_job_and_stops_rescheduling(self):
        overlay = self._overlay()
        job = overlay._item_job
        self.assertIsNotNone(job)
        with patch.object(overlay, "after_cancel", wraps=overlay.after_cancel) as cancelled:
            overlay.close()
        cancelled.assert_called_once_with(job)
        self.assertTrue(overlay._closed)
        with patch.object(overlay, "after") as after:
            overlay._refresh_items()
        after.assert_not_called()
        self.assertIsNone(overlay._item_job)

    def test_the_log_part_still_works(self):
        overlay = self._overlay()
        overlay.append_many(["a", "b"])
        self.assertEqual(overlay.text.get("1.0", "end").strip(), "a\nb")


class TestItemUseLinesOfTheApp(unittest.TestCase):
    @staticmethod
    def _monitor(idx, tracker):
        return types.SimpleNamespace(window_idx=idx, st=types.SimpleNamespace(item_use=tracker))

    def _held(self, holding=True):
        tracker = ItemUse.UseTracker(1, clock=lambda: 0.0)
        if holding:
            tracker.pickup(CHARM)
        return tracker

    def test_stopped_has_no_lines(self):
        app = types.SimpleNamespace(monitors=[])
        self.assertEqual(mainGUI.App._item_use_lines(app), [])

    def test_only_the_window_that_holds_it_has_a_line(self):
        app = types.SimpleNamespace(monitors=[self._monitor(1, self._held(False)), self._monitor(2, self._held())])
        self.assertEqual(mainGUI.App._item_use_lines(app), ["窓2 Bloody Kat Charm  使用可"])

    def test_windows_without_a_tracker_are_skipped_and_lines_follow_the_window_number(self):
        app = types.SimpleNamespace(monitors=[self._monitor(3, self._held()), self._monitor(1, None),
                                              self._monitor(2, self._held())])
        self.assertEqual(mainGUI.App._item_use_lines(app),
                         ["窓2 Bloody Kat Charm  使用可", "窓3 Bloody Kat Charm  使用可"])

    def test_toggle_overlay_passes_the_lines(self):
        app = types.SimpleNamespace(_overlay=None)
        app._item_use_lines = lambda: []
        with patch.object(mainGUI, "LogOverlay") as overlay:
            mainGUI.App._toggle_overlay(app)
        overlay.assert_called_once_with(app, item_lines=app._item_use_lines)
        self.assertIs(app._overlay, overlay.return_value)


# ── F LogMonitor ───────────────────────────────
class TestLogMonitorItemUse(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.logs = []
        self.monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x31), {}, self.logs.append, window_idx=1)
        self.tracker = self.monitor.st.item_use

    def _held(self):
        return self.tracker.status() is not None

    def test_the_tracker_is_made_and_given_the_player_count(self):
        self.assertIsInstance(self.tracker, ItemUse.UseTracker)
        self.assertEqual(self.tracker.window_idx, 1)
        self.assertEqual(self.tracker._players, self.monitor._player_count)

    def test_pickup_and_drop_lines(self):
        self.monitor._process(PICK)
        self.assertTrue(self._held())
        self.monitor._process(DROP)
        self.assertFalse(self._held())

    def test_losing_the_item_resets(self):
        for lose in (lambda: self.monitor._lose_held_item("テスト"),
                     lambda: self.monitor._mark_item_lost("テスト: アイテムロスト")):
            with self.subTest(lose=lose):
                self.monitor._process(PICK)
                self.assertTrue(self._held())
                lose()
                self.assertFalse(self._held())

    def test_losing_resets_the_times_too(self):
        self.monitor._process(PICK)
        self.tracker.use(ItemUse.SOURCE_HAND)
        self.monitor._lose_held_item("テスト")
        self.monitor._process(PICK)
        self.assertEqual(self.tracker.status()[3], 0.0)

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

    def test_an_item_not_in_the_table_is_not_held_and_writes_nothing(self):
        other = PICK.replace("BloodKatCharm", "SomethingElse")
        with patch.object(DebugLog, "write") as written:
            self.monitor._process(other)
            self.monitor._flush_item_use_log()
        self.assertFalse(self._held())
        self.assertFalse(any("SomethingElse" in c.args[0] for c in written.call_args_list))

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
        self.assertGreater(self.tracker.status()[2], 0.0)
        self.monitor.stop()

    def test_stopping_an_unstarted_monitor_is_safe(self):
        self.monitor.stop()
        self.assertFalse(self._held())


# ── G OSC ──────────────────────────────────────
class _FakeSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def sendto(self, data, addr):
        if self.fail:
            raise OSError("no route")
        self.sent.append(bytes(data))
        return len(data)

    def close(self):
        pass


class TestOscUse(unittest.TestCase):
    def _executor(self, tracker="default"):
        st = WindowState()
        if tracker == "default":
            tracker = ItemUse.UseTracker(1)
            tracker.pickup(CHARM)
        st.item_use = tracker
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x10, osc_port=9000), st, lambda: True,
                                           lambda _m: None)
        ex._osc._sock = _FakeSocket()                   # 本物のソケットには送らない
        self.addCleanup(ex._osc.close)
        return ex, tracker

    def _press(self, ex, address):
        with patch.object(OSCClient, "time", _module_time(lambda s: None)):
            ex._osc.press(address, 0.025)

    def test_use_right_press_is_a_use(self):
        ex, tracker = self._executor()
        self._press(ex, "/input/UseRight")
        lines = _use_lines(tracker)
        self.assertEqual(len(lines), 1)
        self.assertIn("OSC", lines[0])
        self.assertGreater(tracker.status()[2], 0.0)
        self.assertGreater(len(ex._osc._sock.sent), 2, "0・1・0 を送った")

    def test_another_address_is_not_a_use(self):
        ex, tracker = self._executor()
        self._press(ex, "/input/Jump")
        self.assertEqual(_use_lines(tracker), [])

    def test_a_window_without_a_tracker_does_not_fail(self):
        ex, _ = self._executor(tracker=None)
        self._press(ex, "/input/UseRight")

    def test_resends_while_held_count_once(self):
        ex, tracker = self._executor()
        with patch.object(OSCClient, "time", _module_time(lambda s: None)):
            ex._osc.press("/input/UseRight", 0.2)       # 送り直しの 1 が何回か入る
        self.assertEqual(len(_use_lines(tracker)), 1)

    def test_the_client_without_a_callback_works_as_before(self):
        client = OSCClient.OSCClient(9000)
        client._sock = _FakeSocket()
        self.assertTrue(client.send("/input/UseRight", 1))
        self.assertEqual(len(client._sock.sent), 1)
        client.close()

    def test_a_failed_send_does_not_call_the_callback(self):
        calls = []
        client = OSCClient.OSCClient(9000, on_send=lambda a, v: calls.append((a, v)))
        client._sock = _FakeSocket(fail=True)
        with patch.object(DebugLog, "exception"):
            self.assertFalse(client.send("/input/UseRight", 1))
        self.assertEqual(calls, [])
        client._sock = _FakeSocket()
        self.assertTrue(client.send("/input/UseRight", 1))
        self.assertEqual(calls, [("/input/UseRight", 1)])
        client.close()

    def test_a_raising_callback_does_not_break_the_send(self):
        client = OSCClient.OSCClient(9000, on_send=lambda a, v: 1 / 0)
        client._sock = _FakeSocket()
        with patch.object(DebugLog, "exception"):
            self.assertTrue(client.send("/input/UseRight", 1))
        client.close()


# ── H WindowOperator（ツールのクリック）────────────
class TestToolClickNotice(unittest.TestCase):
    def setUp(self):
        self.inputs = _tab._Inputs(self)
        for p in (patch.object(WindowOperator, "foreground_hwnd", return_value=0x77),
                  patch.object(ItemUse, "on_click",
                               side_effect=lambda hwnd, source: self.inputs.calls.append(("notify", hwnd, source)))):
            p.start()
            self.addCleanup(p.stop)

    def test_click_with_tab_notifies_right_after_the_mouse_down(self):
        self.assertTrue(WindowOperator.click_with_tab(0.1))
        self.assertEqual(self.inputs.calls,
                         [("press", _tab.TAB), ("sleep", config.CLICK_TAB_LEAD_SEC), ("down", True),
                          ("notify", 0x77, ItemUse.SOURCE_TOOL), ("sleep", 0.1), ("up", True),
                          ("release", _tab.TAB)])

    def test_mouse_click_notifies_once(self):
        WindowOperator.mouse_click(0.1)
        self.assertEqual(self.inputs.calls,
                         [("down", False), ("notify", 0x77, ItemUse.SOURCE_TOOL), ("sleep", 0.1), ("up", False)])

    def test_mouse_down_notifies_and_mouse_up_does_not(self):
        WindowOperator.mouse_down()
        self.assertEqual(self.inputs.calls, [("down", False), ("notify", 0x77, ItemUse.SOURCE_TOOL)])
        self.inputs.calls.clear()
        WindowOperator.mouse_up()
        self.assertEqual(self.inputs.calls, [("up", False)])

    def test_no_notice_when_the_click_was_cancelled(self):
        self.assertFalse(WindowOperator.click_with_tab(0.1, still_front=lambda: False))
        self.assertNotIn("notify", [c[0] for c in self.inputs.calls])

    def test_a_failing_notice_does_not_change_the_click_or_the_tab_release(self):
        ItemUse.on_click.side_effect = RuntimeError("boom")
        with patch.object(DebugLog, "exception"):
            self.assertTrue(WindowOperator.click_with_tab(0.1))
        self.assertEqual(self.inputs.calls, _tab._one_click())

    def test_the_notice_does_not_write_the_debug_log_or_press_keys(self):
        with patch.object(DebugLog, "write") as written:
            WindowOperator.mouse_click(0.1)
        self.assertEqual(written.call_args_list, [])
        self.assertEqual([c for c in self.inputs.calls if c[0] in ("press", "release")], [])


class TestToolClickReachesTheForegroundWindow(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        _tab._Inputs(self)
        self.front, self.back = _tracker([0.0]), _tracker([0.0])
        for tracker in (self.front, self.back):
            tracker.pickup(CHARM)
        ItemUse.register(0x77, self.front)
        ItemUse.register(0x78, self.back)

    def test_only_the_front_window_counts_a_tool_click(self):
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x77):
            WindowOperator.mouse_click(0.1)
        self.assertEqual(len(_use_lines(self.front)), 1)
        self.assertEqual(_use_lines(self.back), [])

    def test_the_source_is_the_tool(self):
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x77):
            WindowOperator.mouse_down()
        self.assertIn("ツール", _use_lines(self.front)[0])

