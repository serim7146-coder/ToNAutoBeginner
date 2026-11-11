"""各 VRChat 窓の上の手持ちアイテムの表示（段階 2 A）: 置く位置の計算・窓の矩形・スタイル指定・窓ごとの表示のまとめ・設定と 0.1 秒ごとの更新。
本物の窓・画面・Win32 は使わない（偽の win32gui・user32・偽の表示の窓。表示の窓そのものは support で隠れる Toplevel）"""
import types

from tests.support import *  # noqa: F401,F403
import ItemOverlay
import tests.test_ui_tidy as _ui_tidy               # 本物の App を立てる土台を借りる

CHARM = "BloodKatCharm"
SCREEN = (0, 0, 1920, 1080)
CLIENT = (100, 100, 500, 400)
SIZE = (80, 20)


# ── 置く位置（純粋な計算）─────────────────────────────
class TestPlace(unittest.TestCase):
    def _place(self, corner, dx=10, dy=5, client=CLIENT, size=SIZE, screen=SCREEN):
        return ItemOverlay.place(client, size, corner, dx, dy, screen)

    def test_each_corner_with_an_offset(self):
        self.assertEqual(self._place("top_left"), (110, 105))
        self.assertEqual(self._place("top_right"), (410, 105))
        self.assertEqual(self._place("bottom_left"), (110, 375))
        self.assertEqual(self._place("bottom_right"), (410, 375))

    def test_zero_offset_sits_in_the_corner(self):
        self.assertEqual(self._place("top_left", 0, 0), (100, 100))
        self.assertEqual(self._place("bottom_right", 0, 0), (420, 380))

    def test_a_negative_offset_goes_outside_the_window(self):
        self.assertEqual(self._place("top_left", -30, -10), (70, 90))
        self.assertEqual(self._place("bottom_right", -30, -10), (450, 390))

    def test_a_position_outside_the_screen_is_clamped_in(self):
        self.assertEqual(self._place("top_left", 10, 10, client=(-100, 100, 300, 400)), (0, 110))
        self.assertEqual(self._place("bottom_left", 10, 10, client=(100, 900, 500, 1500)), (110, 1060))
        self.assertEqual(self._place("top_right", 10, 10, client=(1700, -50, 2300, 300)), (1840, 0))

    def test_a_virtual_screen_that_starts_left_of_zero(self):
        screen = (-1920, 0, 1920, 1080)
        self.assertEqual(self._place("top_left", 10, 5, client=(-1800, 100, -1000, 600), screen=screen),
                         (-1790, 105))

    def test_a_client_that_is_not_on_the_screen_at_all_gives_none(self):
        for client in ((2000, 0, 2400, 300), (-500, 0, -100, 300), (0, 1200, 400, 1500), (0, -400, 400, -10)):
            self.assertIsNone(self._place("top_left", client=client), client)

    def test_a_client_touching_only_the_edge_is_off_the_screen(self):
        self.assertIsNone(self._place("top_left", client=(1920, 0, 2300, 300)))

    def test_a_client_partly_on_the_screen_is_placed(self):
        self.assertIsNotNone(self._place("top_left", client=(1800, 0, 2300, 300)))


# ── 窓のクライアント領域・画面の矩形 ─────────────────────
class TestClientRect(unittest.TestCase):
    def _fake(self, exists=True, visible=True, iconic=False, size=(800, 600), origin=(100, 50)):
        return types.SimpleNamespace(
            IsWindow=lambda h: exists, IsWindowVisible=lambda h: visible, IsIconic=lambda h: iconic,
            GetClientRect=lambda h: (0, 0, *size), ClientToScreen=lambda h, pt: origin)

    def _rect(self, **kwargs):
        with patch.object(WindowOperator, "win32gui", self._fake(**kwargs)):
            return WindowOperator.client_rect_on_screen(0x77)

    def test_a_visible_window_gives_its_client_rect_on_the_screen(self):
        self.assertEqual(self._rect(), (100, 50, 900, 650))

    def test_gone_hidden_minimized_and_empty_windows_give_none(self):
        self.assertIsNone(self._rect(exists=False))
        self.assertIsNone(self._rect(visible=False))
        self.assertIsNone(self._rect(iconic=True))
        self.assertIsNone(self._rect(size=(0, 600)))
        self.assertIsNone(self._rect(size=(800, 0)))

    def test_no_hwnd_gives_none(self):
        self.assertIsNone(WindowOperator.client_rect_on_screen(0))

    def test_an_error_gives_none_and_is_logged(self):
        fake = self._fake()
        fake.GetClientRect = lambda h: (_ for _ in ()).throw(OSError("gone"))
        with patch.object(WindowOperator, "win32gui", fake), patch.object(DebugLog, "exception") as logged:
            self.assertIsNone(WindowOperator.client_rect_on_screen(0x77))
        logged.assert_called_once()

    def test_only_read_only_calls_are_used(self):
        """窓へメッセージを送る呼び出しは使わない（偽物に 5 つの読み取りしか無くても動く）"""
        self.assertIsNotNone(self._rect())

    def test_the_virtual_screen_rect(self):
        metrics = {76: -1920, 77: 0, 78: 3840, 79: 1080}
        fake = types.SimpleNamespace(GetSystemMetrics=lambda i: metrics[i])
        with patch.object(WindowOperator, "user32", fake):
            self.assertEqual(WindowOperator.virtual_screen_rect(), (-1920, 0, 1920, 1080))


# ── スタイル指定（クリックを通す・前面を取らない）─────────────
class TestOverlayStyle(unittest.TestCase):
    def _user32(self, current=0x100, fail=False):
        calls = []

        def get(handle, index):
            calls.append(("get", handle.value, index))
            if fail:
                raise OSError("no")
            return current

        def set_(handle, index, value):
            calls.append(("set", handle.value, index, value))
            return current
        return types.SimpleNamespace(GetWindowLongW=get, SetWindowLongW=set_), calls

    def test_it_adds_the_four_flags_and_keeps_the_rest(self):
        fake, calls = self._user32(current=0x100)
        with patch.object(WindowOperator, "user32", fake):
            self.assertTrue(REAL_SET_OVERLAY_STYLE(0x1234))
        self.assertEqual(calls[0], ("get", 0x1234, -20))
        kind, hwnd, index, value = calls[1]
        self.assertEqual((kind, hwnd, index), ("set", 0x1234, -20))
        for flag in (0x08000000, 0x20, 0x80000, 0x80):
            self.assertTrue(value & flag, hex(flag))
        self.assertTrue(value & 0x100, "元のスタイルは残す")

    def test_the_flag_values(self):
        self.assertEqual((WindowOperator.WS_EX_NOACTIVATE, WindowOperator.WS_EX_TRANSPARENT,
                          WindowOperator.WS_EX_LAYERED, WindowOperator.WS_EX_TOOLWINDOW,
                          WindowOperator.GWL_EXSTYLE), (0x08000000, 0x20, 0x80000, 0x80, -20))

    def test_a_failure_gives_false_and_is_logged(self):
        fake, _calls = self._user32(fail=True)
        with patch.object(WindowOperator, "user32", fake), patch.object(DebugLog, "exception") as logged:
            self.assertFalse(REAL_SET_OVERLAY_STYLE(0x1234))
        logged.assert_called_once()

    def test_no_hwnd_does_nothing(self):
        fake, calls = self._user32()
        with patch.object(WindowOperator, "user32", fake):
            self.assertFalse(REAL_SET_OVERLAY_STYLE(0))
        self.assertEqual(calls, [])

    def test_the_support_stub_is_in_place_for_the_other_tests(self):
        self.assertIsNot(WindowOperator.set_overlay_style, REAL_SET_OVERLAY_STYLE)
        self.assertTrue(WindowOperator.set_overlay_style(0x1234))


# ── 窓ごとの表示のまとめ（偽の表示の窓）──────────────────
class _FakeOverlay:
    made = []

    def __init__(self, parent, remember):
        self.calls = []
        self.visible = False
        self.closed = False
        _FakeOverlay.made.append(self)

    def measure(self, text):
        self.calls.append(("measure", text))
        return (100, 20)

    def show(self, text, x, y):
        self.calls.append(("show", text, x, y))
        self.visible = True

    def hide(self):
        self.calls.append(("hide",))
        self.visible = False

    def close(self):
        self.closed = True


class TestItemOverlays(unittest.TestCase):
    PLACEMENT = ("top_left", 10, 5)

    def setUp(self):
        _FakeOverlay.made = []
        self.rects = {0x10: (100, 100, 500, 400), 0x20: (600, 100, 1000, 400)}
        self.asked = []

        def rect(hwnd):
            self.asked.append(hwnd)
            return self.rects.get(hwnd)
        for p in (patch.object(WindowOperator, "client_rect_on_screen", rect),
                  patch.object(WindowOperator, "virtual_screen_rect", lambda: SCREEN)):
            p.start()
            self.addCleanup(p.stop)
        self.overlays = ItemOverlay.ItemOverlays(None, lambda w: None, factory=_FakeOverlay)

    def _refresh(self, entries):
        self.overlays.refresh(entries, self.PLACEMENT)

    def test_only_windows_holding_an_item_show(self):
        self._refresh([(1, 0x10, "Taser  使用可"), (2, 0x20, "")])
        self.assertEqual(len(_FakeOverlay.made), 1, "持っていない窓の表示は作らない")
        self.assertIn(("show", "Taser  使用可", 110, 105), _FakeOverlay.made[0].calls)

    def test_no_win32_call_for_a_window_that_holds_nothing(self):
        self._refresh([(1, 0x10, "Taser  使用可"), (2, 0x20, "")])
        self.assertEqual(self.asked, [0x10])

    def test_a_window_that_stops_holding_is_hidden_without_win32(self):
        self._refresh([(1, 0x10, "Taser  使用可")])
        self.asked.clear()
        self._refresh([(1, 0x10, "")])
        self.assertEqual(self.asked, [])
        self.assertFalse(_FakeOverlay.made[0].visible)

    def test_it_follows_a_moved_window(self):
        self._refresh([(1, 0x10, "A")])
        self.rects[0x10] = (300, 200, 700, 500)
        self._refresh([(1, 0x10, "A")])
        self.assertEqual(_FakeOverlay.made[0].calls[-1], ("show", "A", 310, 205))

    def test_a_minimized_or_gone_window_hides_its_overlay(self):
        self._refresh([(1, 0x10, "A")])
        del self.rects[0x10]
        self._refresh([(1, 0x10, "A")])
        self.assertFalse(_FakeOverlay.made[0].visible)
        self.assertFalse(_FakeOverlay.made[0].closed)
        self.rects[0x10] = (100, 100, 500, 400)
        self._refresh([(1, 0x10, "A")])
        self.assertTrue(_FakeOverlay.made[0].visible)

    def test_a_window_that_is_off_screen_hides_its_overlay(self):
        self._refresh([(1, 0x10, "A")])
        self.rects[0x10] = (5000, 100, 5400, 400)
        self._refresh([(1, 0x10, "A")])
        self.assertFalse(_FakeOverlay.made[0].visible)

    def test_a_window_missing_from_the_entries_is_destroyed(self):
        self._refresh([(1, 0x10, "A"), (2, 0x20, "B")])
        first, second = _FakeOverlay.made
        self._refresh([(2, 0x20, "B")])
        self.assertTrue(first.closed)
        self.assertFalse(second.closed)
        self._refresh([])
        self.assertTrue(second.closed)

    def test_close_all(self):
        self._refresh([(1, 0x10, "A"), (2, 0x20, "B")])
        self.overlays.close_all()
        self.assertTrue(all(o.closed for o in _FakeOverlay.made))
        self.assertEqual(self.overlays._overlays, {})

    def test_the_overlay_is_reused_for_the_same_window(self):
        self._refresh([(1, 0x10, "A")])
        self._refresh([(1, 0x10, "B")])
        self.assertEqual(len(_FakeOverlay.made), 1)

    def test_the_text_is_passed_as_given_with_no_window_number(self):
        self._refresh([(3, 0x10, "Bloody Kat Charm  使用可")])
        shows = [c for c in _FakeOverlay.made[0].calls if c[0] == "show"]
        self.assertEqual(shows[0][1], "Bloody Kat Charm  使用可")
        self.assertNotIn("窓", shows[0][1])

    def test_a_failing_window_does_not_stop_the_others(self):
        self._refresh([(1, 0x10, "A"), (2, 0x20, "B")])
        _FakeOverlay.made[0].show = lambda *a: 1 / 0
        with patch.object(DebugLog, "exception") as logged:
            self._refresh([(1, 0x10, "A2"), (2, 0x20, "B2")])
        logged.assert_called_once()
        self.assertEqual(_FakeOverlay.made[1].calls[-1][:2], ("show", "B2"))


# ── 表示の窓（本物の Toplevel。support で画面には出ない）──────────
class TestWindowItemOverlay(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.registered = []
        self.hwnd = [0x5151]
        for p in (patch.object(WindowOperator, "own_window_hwnd", lambda widget: self.hwnd[0]),
                  patch.object(WindowOperator, "set_capture_excluded", return_value=True),
                  patch.object(WindowOperator, "set_overlay_style", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.style = WindowOperator.set_overlay_style
        self.addCleanup(SharedState.unregister_own_window, 0x5151)
        self.overlay = ItemOverlay.WindowItemOverlay(self.root, mainGUI._remember_own_window)
        self.addCleanup(lambda: self.overlay.winfo_exists() and self.overlay.destroy())

    def test_it_is_created_hidden_and_not_yet_registered(self):
        self.assertEqual(self.overlay.state(), "withdrawn")
        self.style.assert_not_called()
        self.assertNotIn(0x5151, SharedState.own_windows())

    def test_a_negative_position_is_written_with_a_plus_before_each_coordinate(self):
        width, height = self.overlay.measure("Taser  使用可")
        with patch.object(self.overlay, "geometry") as geometry:
            self.overlay.show("Taser  使用可", -5, 10)
        geometry.assert_called_once_with(f"{width}x{height}+-5+10")

    def test_the_size_follows_the_text(self):
        small = self.overlay.measure("A")
        large = self.overlay.measure("Bloody Kat Charm  効果 1.2秒・CT 0.3秒")
        self.assertGreater(large[0], small[0])

    def test_geometry_is_set_only_when_it_changed(self):
        self.overlay.show("A", 10, 20)
        with patch.object(self.overlay, "geometry") as geometry:
            self.overlay.show("A", 10, 20)
            self.overlay.show("A", 10, 20)
        geometry.assert_not_called()
        with patch.object(self.overlay, "geometry") as geometry:
            self.overlay.show("A", 11, 20)
        geometry.assert_called_once()

    def test_the_same_text_is_not_set_again(self):
        self.overlay.show("A", 10, 20)
        with patch.object(self.overlay._label, "config") as configured:
            self.overlay.show("A", 30, 40)
            self.overlay.measure("A")
        configured.assert_not_called()
        with patch.object(self.overlay._label, "config") as configured:
            self.overlay.show("B", 30, 40)
        configured.assert_called_once_with(text="B")

    def test_it_is_registered_as_an_own_window_once_and_unregistered_on_close(self):
        self.overlay.show("A", 10, 20)
        self.overlay.hide()
        self.overlay.show("A", 10, 20)
        self.assertIn(0x5151, SharedState.own_windows())
        self.overlay.close()
        self.root.update_idletasks()
        self.assertNotIn(0x5151, SharedState.own_windows())

    def test_the_style_is_applied_once_and_again_only_when_the_hwnd_changes(self):
        self.overlay.show("A", 10, 20)
        self.overlay.hide()
        self.overlay.show("A", 10, 20)
        self.style.assert_called_once_with(0x5151)
        self.hwnd[0] = 0x6262
        self.overlay.hide()
        self.overlay.show("A", 10, 20)
        self.assertEqual(self.style.call_args_list, [call(0x5151), call(0x6262)])
        self.addCleanup(SharedState.unregister_own_window, 0x6262)

    def test_a_failed_style_is_tried_again_next_time(self):
        self.style.return_value = False
        self.overlay.show("A", 10, 20)
        self.overlay.hide()
        self.overlay.show("A", 10, 20)
        self.assertEqual(self.style.call_count, 2)

    def test_hide_and_show_follow_each_other(self):
        self.overlay.show("A", 10, 20)
        with patch.object(self.overlay, "withdraw") as withdraw:
            self.overlay.hide()
            self.overlay.hide()
        withdraw.assert_called_once()
        with patch.object(self.overlay, "deiconify") as deiconify:
            self.overlay.show("A", 10, 20)
            self.overlay.show("A", 10, 20)
        deiconify.assert_called_once()

    def test_the_window_has_no_frame_and_is_topmost_semi_transparent(self):
        self.assertTrue(self.overlay.overrideredirect())
        self.assertEqual(float(self.overlay.attributes("-alpha")), config.ITEM_OVERLAY_ALPHA)

    def test_it_never_shows_on_screen_in_the_tests(self):
        self.overlay.show("A", 10, 20)
        self.assertFalse(self.overlay.winfo_viewable())

    def test_closing_twice_is_safe(self):
        self.overlay.close()
        self.overlay.close()


# ── 設定と 0.1 秒ごとの更新（本物の App）────────────────────
class TestOverlaySettingsAndTick(_ui_tidy._AppCase):
    def setUp(self):
        super().setUp()
        p = patch.object(config, "GUI_OVERLAY_ITEM_REFRESH_MS", 600000)     # 勝手に回らないように。更新は手で呼ぶ
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)
        self.app._item_overlays = None
        self.app._item_overlay_job = None
        self.app._load_item_overlay_settings({})
        self.app._schedule_settings_save.reset_mock()

    def _clean(self):
        self.app._stop_item_overlays()
        self.app._running = False
        self.app.monitors = []

    def _save(self):
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        return stored

    # 設定の既定・壊れた値
    def test_the_defaults(self):
        self.assertEqual(self.app._item_overlay_placement(), ("bottom_left", 10, 10))
        self.assertEqual(self.app.v_item_overlay_corner.get(), "左下")
        self.assertEqual((config.ITEM_OVERLAY_CORNER_DEFAULT, config.ITEM_OVERLAY_OFFSET_DEFAULT),
                         ("bottom_left", (10, 10)))

    def test_loading_valid_values(self):
        self.app._load_item_overlay_settings({"item_overlay_corner": "top_right", "item_overlay_x": -20,
                                              "item_overlay_y": 300})
        self.assertEqual(self.app._item_overlay_placement(), ("top_right", -20, 300))
        self.assertEqual(self.app.v_item_overlay_corner.get(), "右上")

    def test_broken_values_fall_back_to_the_defaults(self):
        for data in ({"item_overlay_corner": "middle"}, {"item_overlay_corner": 5}, {"item_overlay_corner": None},
                     {"item_overlay_x": "5"}, {"item_overlay_x": True}, {"item_overlay_x": 5.5},
                     {"item_overlay_y": None}, {"item_overlay_y": [1]}, {"item_overlay_y": False}):
            with self.subTest(data=data):
                self.app._load_item_overlay_settings(data)
                self.assertEqual(self.app._item_overlay_placement(), ("bottom_left", 10, 10))

    def test_offsets_are_clamped(self):
        limit = config.ITEM_OVERLAY_OFFSET_MAX
        self.app._load_item_overlay_settings({"item_overlay_x": 10 ** 9, "item_overlay_y": -10 ** 9})
        self.assertEqual(self.app._item_overlay_placement(), ("bottom_left", limit, -limit))

    def test_the_helpers(self):
        self.assertEqual(mainGUI._valid_overlay_corner("top_left"), "top_left")
        self.assertEqual(mainGUI._valid_overlay_corner("x"), "bottom_left")
        self.assertEqual(mainGUI._valid_overlay_offset(7, 10), 7)
        self.assertEqual(mainGUI._valid_overlay_offset(True, 10), 10)
        self.assertEqual(mainGUI._valid_overlay_offset("7", 10), 10)
        self.assertEqual(mainGUI._valid_overlay_offset(0, 10), 0)

    def test_loading_does_not_schedule_a_save(self):
        self.app._load_item_overlay_settings({"item_overlay_corner": "top_right", "item_overlay_x": 3})
        self.app._schedule_settings_save.assert_not_called()

    def test_save_writes_the_three_keys(self):
        self.app._load_item_overlay_settings({"item_overlay_corner": "top_left", "item_overlay_x": 12,
                                              "item_overlay_y": -7})
        stored = self._save()
        self.assertEqual((stored["item_overlay_corner"], stored["item_overlay_x"], stored["item_overlay_y"]),
                         ("top_left", 12, -7))

    def test_the_report_exclusion_list_is_unchanged(self):
        self.assertEqual(config.REPORT_SETTINGS_EXCLUDE_KEYS, ("obs_password_dpapi", "obs_password", "phone_notify_topic"))

    # 画面の欄
    def _section(self):
        for child in self.app.winfo_children():
            if isinstance(child, mainGUI.CollapsibleFrame):
                labels = [w.cget("text") for w in _descendants(child) if isinstance(w, tk.Label)]
                if "⑥ アイテムの表示" in labels:
                    return child
        self.fail("⑥ アイテムの表示 の枠が無い")

    def test_the_controls_are_in_section_six_which_is_collapsed(self):
        section = self._section()
        inside = list(_descendants(section))
        self.assertIn(self.app.cb_item_overlay_corner, inside)
        self.assertIn(self.app.sp_item_overlay_x, inside)
        self.assertIn(self.app.sp_item_overlay_y, inside)
        self.assertTrue(section._collapsed)
        self.assertEqual(list(self.app.cb_item_overlay_corner["values"]), ["左上", "右上", "左下", "右下"])
        self.assertEqual(str(self.app.cb_item_overlay_corner.cget("state")), "readonly")
        self.assertEqual(int(self.app.sp_item_overlay_x.cget("width")), 6)
        texts = [w.cget("text") for w in _descendants(section) if isinstance(w, ttk.Label)]
        for word in ("窓の上の表示:", "横", "縦"):
            self.assertIn(word, texts)
        self.assertEqual(texts.count("px"), 2)

    def test_section_six_comes_after_section_five_and_before_the_buttons(self):
        titles = []
        for child in self.app.pack_slaves():
            if isinstance(child, mainGUI.CollapsibleFrame):
                titles += [w.cget("text") for w in _descendants(child)
                           if isinstance(w, tk.Label) and w.cget("text").startswith(("⑤", "⑥"))]
        self.assertEqual(titles, ["⑤ OBS自動録画", "⑥ アイテムの表示"])

    def test_changing_a_control_schedules_a_save(self):
        self.app.v_item_overlay_corner.set("右下")
        self.assertEqual(self.app._schedule_settings_save.call_count, 1)
        self.app.v_item_overlay_x.set("30")
        self.app.v_item_overlay_y.set("40")
        self.assertEqual(self.app._schedule_settings_save.call_count, 3)
        self.assertEqual(self.app._item_overlay_placement(), ("bottom_right", 30, 40))

    def test_an_invalid_spinbox_keeps_the_last_valid_value(self):
        self.app.v_item_overlay_x.set("25")
        self.assertEqual(self.app._item_overlay_placement(), ("bottom_left", 25, 10))
        for bad in ("abc", "", "1.5", "-"):
            self.app.v_item_overlay_x.set(bad)
            self.assertEqual(self.app._item_overlay_placement(), ("bottom_left", 25, 10), repr(bad))
        self.app.v_item_overlay_x.set("9999999")
        self.assertEqual(self.app._item_overlay_placement()[1], config.ITEM_OVERLAY_OFFSET_MAX)

    # 動作の開始・停止・更新
    def test_start_schedules_and_stop_cancels_and_closes(self):
        with patch.object(self.app, "after", return_value="job") as after:
            self.app._start_item_overlays()
            self.app._start_item_overlays()
        after.assert_called_once_with(600000, self.app._tick_item_overlays)
        self.assertEqual(self.app._item_overlay_job, "job")
        overlays = self.app._item_overlays = MagicMock()
        with patch.object(self.app, "after_cancel") as cancel:
            self.app._stop_item_overlays()
        cancel.assert_called_once_with("job")
        overlays.close_all.assert_called_once()
        self.assertIsNone(self.app._item_overlay_job)
        self.assertIsNone(self.app._item_overlays)

    def test_stopping_when_never_started_is_safe(self):
        self.app._stop_item_overlays()
        self.app._stop_item_overlays()

    def test_the_tick_with_no_monitors_hides_everything(self):
        overlays = self.app._item_overlays = MagicMock()
        self.app._running = True
        self.app.monitors = []
        with patch.object(self.app, "after", return_value="next"):
            self.app._tick_item_overlays()
        overlays.refresh.assert_called_once_with([], ("bottom_left", 10, 10))
        self.assertEqual(self.app._item_overlay_job, "next")

    def test_the_tick_builds_a_line_per_window_without_the_window_number(self):
        def monitor(idx, hwnd, held):
            tracker = ItemUse.UseTracker(idx, clock=lambda: 0.0)
            if held:
                tracker.pickup(CHARM)
            return types.SimpleNamespace(window_idx=idx, cfg=types.SimpleNamespace(hwnd=hwnd),
                                         st=types.SimpleNamespace(item_use=tracker))
        overlays = self.app._item_overlays = MagicMock()
        self.app._running = True
        self.app.monitors = [monitor(1, 0x10, False), monitor(2, 0x20, True),
                             types.SimpleNamespace(window_idx=3, cfg=types.SimpleNamespace(hwnd=0x30),
                                                   st=types.SimpleNamespace(item_use=None))]
        with patch.object(self.app, "after", return_value="next"):
            self.app._tick_item_overlays()
        entries, placement = overlays.refresh.call_args.args
        self.assertEqual(entries, [(1, 0x10, ""), (2, 0x20, "Bloody Kat Charm  使用可"), (3, 0x30, "")])
        self.assertEqual(placement, ("bottom_left", 10, 10))

    def test_the_tick_creates_the_overlays_lazily_with_the_own_window_hook(self):
        self.app._running = True
        self.app.monitors = []
        with patch.object(ItemOverlay, "ItemOverlays") as made, patch.object(self.app, "after", return_value="n"):
            self.app._tick_item_overlays()
        made.assert_called_once_with(self.app, mainGUI._remember_own_window)
        made.return_value.refresh.assert_called_once()

    def test_the_tick_does_nothing_and_does_not_rearm_when_stopped(self):
        overlays = self.app._item_overlays = MagicMock()
        self.app._running = False
        with patch.object(self.app, "after") as after:
            self.app._tick_item_overlays()
        overlays.refresh.assert_not_called()
        after.assert_not_called()
        self.assertIsNone(self.app._item_overlay_job)

    def test_a_failing_refresh_is_logged_and_the_tick_keeps_going(self):
        overlays = self.app._item_overlays = MagicMock()
        overlays.refresh.side_effect = RuntimeError("boom")
        self.app._running = True
        with patch.object(DebugLog, "exception") as logged, patch.object(self.app, "after", return_value="n"):
            self.app._tick_item_overlays()
        logged.assert_called_once()
        self.assertEqual(self.app._item_overlay_job, "n")

    def test_the_tick_is_rearmed_by_the_configured_interval(self):
        self.app._item_overlays = MagicMock()
        self.app._running = True
        with patch.object(self.app, "after", return_value="n") as after:
            self.app._tick_item_overlays()
        after.assert_called_once_with(600000, self.app._tick_item_overlays)

    def test_start_and_stop_of_the_app_call_them(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn("App._start_item_overlays(self)", src)
        self.assertIn("App._stop_item_overlays(self)", src)
        self.assertLess(src.index("App._stop_item_overlays(self)"), src.index("self.monitors.clear()\n        self._show_own_windows_again()"))


class TestStartupLoad(unittest.TestCase):
    """起動のとき（App の作成）に、保存した設定が欄へ入る"""

    def _app(self, data):
        with patch.object(mainGUI, "load_settings", return_value=dict(data)),              patch.object(mainGUI.App, "_start_host_save_polling"):
            app = mainGUI.App()
        self.addCleanup(app.destroy)
        return app

    def test_saved_values_are_in_the_controls_and_the_placement(self):
        app = self._app({"item_overlay_corner": "top_right", "item_overlay_x": 44, "item_overlay_y": -3})
        self.assertEqual(app.v_item_overlay_corner.get(), "右上")
        self.assertEqual(app._item_overlay_placement(), ("top_right", 44, -3))

    def test_no_saved_values_give_the_defaults(self):
        app = self._app({})
        self.assertEqual(app._item_overlay_placement(), ("bottom_left", 10, 10))

    def test_the_saved_item_auto_click_reaches_the_tabs(self):
        app = self._app({"item_auto_click": {"1": True, "2": "yes"}})
        self.assertEqual(app._window_item_auto_click, {1: True, 2: False})
        self.assertTrue(app.tabs[0].v_item_auto_click.get())
        self.assertFalse(app.tabs[1].v_item_auto_click.get())


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)
