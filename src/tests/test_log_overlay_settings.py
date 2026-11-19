"""ログのオーバーレイの位置・大きさ・透明度・開いていたかを settings.json に保存して、開き直したときに同じにする（依頼者 2026-11-19）。

本物の Tk の窓は作らない。LogOverlay・App のメソッドは、偽物の self に対して呼ぶ。
"""
from tests.support import *  # noqa: F401,F403


class TestAsOverlayRect(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(mainGUI._as_overlay_rect([10, 20, 600, 400]), (10, 20, 600, 400))

    def test_negative_position_is_kept(self):
        """左のモニターは負の座標になる"""
        self.assertEqual(mainGUI._as_overlay_rect([-1500, -20, 600, 400]), (-1500, -20, 600, 400))

    def test_broken_values_are_none(self):
        for value in (None, {}, {"x": 1}, [], [1, 2, 3], [1, 2, 3, 4, 5], [True, 0, 600, 400],
                      [1.5, 2, 600, 400], [1, 2, 600.0, 400], ["1", 2, 600, 400], "10,20,600,400",
                      (1, 2, 600, 400), [1, 2, 0, 400], [1, 2, 600, 0], [1, 2, 600, -1], [1, 2, -5, 400]):
            with self.subTest(value=value):
                self.assertIsNone(mainGUI._as_overlay_rect(value))


class TestAsOverlayAlpha(unittest.TestCase):
    def test_valid_values_are_kept(self):
        for value, expected in ((0.2, 0.2), (1.0, 1.0), (0.5, 0.5), (1, 1.0)):
            with self.subTest(value=value):
                result = mainGUI._as_overlay_alpha(value)
                self.assertEqual(result, expected)
                self.assertIsInstance(result, float)

    def test_broken_values_are_default(self):
        for value in (0.1, 1.5, 0, -1, True, False, "0.5", None, [0.5], {}):
            with self.subTest(value=value):
                self.assertEqual(mainGUI._as_overlay_alpha(value), 0.75)


class TestAsOverlaySetting(unittest.TestCase):
    def test_missing_or_not_dict_is_default(self):
        for value in (None, [], "x", 1, [10, 20, 600, 400]):
            with self.subTest(value=value):
                self.assertEqual(mainGUI._as_overlay_setting(value), (None, 0.75, False))

    def test_empty_dict_is_default(self):
        self.assertEqual(mainGUI._as_overlay_setting({}), (None, 0.75, False))

    def test_full_value(self):
        self.assertEqual(
            mainGUI._as_overlay_setting({"rect": [10, 20, 600, 400], "alpha": 0.5, "open": True}),
            ((10, 20, 600, 400), 0.5, True))

    def test_open_is_true_only_for_true(self):
        for value in (1, "true", "True", None, 0, [], False):
            with self.subTest(value=value):
                self.assertFalse(mainGUI._as_overlay_setting({"open": value})[2])
        self.assertTrue(mainGUI._as_overlay_setting({"open": True})[2])

    def test_each_field_is_checked_on_its_own(self):
        self.assertEqual(mainGUI._as_overlay_setting({"rect": "bad", "alpha": 0.4, "open": True}),
                         (None, 0.4, True))
        self.assertEqual(mainGUI._as_overlay_setting({"rect": [1, 2, 300, 200], "alpha": 9, "open": False}),
                         ((1, 2, 300, 200), 0.75, False))

    def test_round_trip(self):
        for rect, alpha, opened in (((10, 20, 600, 400), 0.5, True),
                                    ((-1500, 200, 600, 400), 1.0, False),
                                    (None, 0.75, False)):
            with self.subTest(rect=rect):
                saved = mainGUI.overlay_setting(rect, alpha, opened)
                self.assertEqual(mainGUI._as_overlay_setting(json.loads(json.dumps(saved))),
                                 (rect, alpha, opened))

    def test_overlay_setting_shape(self):
        self.assertEqual(mainGUI.overlay_setting((1, 2, 300, 200), 0.5, True),
                         {"rect": [1, 2, 300, 200], "alpha": 0.5, "open": True})
        self.assertEqual(mainGUI.overlay_setting(None, 0.75, False),
                         {"rect": None, "alpha": 0.75, "open": False})


class TestOverlayGeometry(unittest.TestCase):
    def test_no_rect_is_default(self):
        with patch.object(mainGUI.WindowOperator, "rect_on_screen") as on_screen:
            self.assertEqual(mainGUI.overlay_geometry(None), "520x320+10+10")
        on_screen.assert_not_called()

    def test_on_screen_uses_saved_position(self):
        with patch.object(mainGUI.WindowOperator, "rect_on_screen", return_value=True) as on_screen:
            self.assertEqual(mainGUI.overlay_geometry((-1500, 200, 600, 400)), "600x400+-1500+200")
        on_screen.assert_called_once_with((-1500, 200, -900, 600))

    def test_off_screen_resets_position_only(self):
        with patch.object(mainGUI.WindowOperator, "rect_on_screen", return_value=False):
            self.assertEqual(mainGUI.overlay_geometry((-1500, 200, 600, 400)), "600x400+10+10")


class TestLogOverlayCallbacks(unittest.TestCase):
    def _overlay(self, **kw):
        o = type("O", (), {})()
        o._closed = False
        o._on_geometry = kw.get("on_geometry")
        o._on_alpha = kw.get("on_alpha")
        o._on_close = kw.get("on_close")
        o.winfo_x = lambda: -1500
        o.winfo_y = lambda: 200
        o.winfo_width = lambda: 600
        o.winfo_height = lambda: 400
        o.destroyed = 0
        o.destroy = lambda: setattr(o, "destroyed", o.destroyed + 1)
        o.alpha_set = []
        o.attributes = lambda name, value: o.alpha_set.append((name, value))
        return o

    def test_report_geometry_passes_position_and_size(self):
        got = []
        o = self._overlay(on_geometry=got.append)
        mainGUI.LogOverlay._report_geometry(o)
        self.assertEqual(got, [(-1500, 200, 600, 400)])

    def test_report_geometry_without_callback_does_nothing(self):
        o = self._overlay()
        mainGUI.LogOverlay._report_geometry(o)

    def test_set_alpha_changes_window_and_reports(self):
        got = []
        o = self._overlay(on_alpha=got.append)
        mainGUI.LogOverlay._set_alpha(o, "0.55")
        self.assertEqual(o.alpha_set, [("-alpha", 0.55)])
        self.assertEqual(got, [0.55])

    def test_set_alpha_without_callback_still_changes_window(self):
        o = self._overlay()
        mainGUI.LogOverlay._set_alpha(o, "0.4")
        self.assertEqual(o.alpha_set, [("-alpha", 0.4)])

    def test_close_reports_once(self):
        closed = []
        o = self._overlay(on_close=lambda: closed.append(1))
        mainGUI.LogOverlay.close(o)
        mainGUI.LogOverlay.close(o)
        self.assertEqual(closed, [1])
        self.assertEqual(o.destroyed, 1)
        self.assertTrue(o._closed)

    def test_close_without_callback(self):
        o = self._overlay()
        mainGUI.LogOverlay.close(o)
        self.assertTrue(o._closed)
        self.assertEqual(o.destroyed, 1)


class _FakeOverlay:
    """LogOverlay の代わり。開いたときの引数を控える"""
    instances = []

    def __init__(self, parent, rect=None, alpha=0.75, on_geometry=None, on_alpha=None, on_close=None):
        self.parent = parent
        self.rect = rect
        self.alpha = alpha
        self.on_geometry = on_geometry
        self.on_alpha = on_alpha
        self.on_close = on_close
        self._closed = False
        self.close_calls = 0
        _FakeOverlay.instances.append(self)

    def close(self):
        self.close_calls += 1
        self._closed = True
        if self.on_close:
            self.on_close()


def _fake_app():
    app = type("FakeApp", (), {})()
    app._overlay = None
    app._overlay_rect = None
    app._overlay_alpha = 0.75
    app._overlay_open = False
    app.saves = 0
    app._schedule_settings_save = lambda: setattr(app, "saves", app.saves + 1)
    app._on_overlay_geometry = lambda rect: mainGUI.App._on_overlay_geometry(app, rect)
    app._on_overlay_alpha = lambda alpha: mainGUI.App._on_overlay_alpha(app, alpha)
    app._on_overlay_closed = lambda: mainGUI.App._on_overlay_closed(app)
    app._open_overlay = lambda: mainGUI.App._open_overlay(app)
    return app


class TestAppOverlayCallbacks(unittest.TestCase):
    def test_geometry_saved_only_when_changed(self):
        app = _fake_app()
        mainGUI.App._on_overlay_geometry(app, (1, 2, 600, 400))
        self.assertEqual(app._overlay_rect, (1, 2, 600, 400))
        self.assertEqual(app.saves, 1)
        mainGUI.App._on_overlay_geometry(app, (1, 2, 600, 400))
        self.assertEqual(app.saves, 1)
        mainGUI.App._on_overlay_geometry(app, (5, 2, 600, 400))
        self.assertEqual(app.saves, 2)

    def test_alpha_saved_only_when_changed(self):
        app = _fake_app()
        mainGUI.App._on_overlay_alpha(app, 0.5)
        self.assertEqual(app._overlay_alpha, 0.5)
        self.assertEqual(app.saves, 1)
        mainGUI.App._on_overlay_alpha(app, 0.5)
        self.assertEqual(app.saves, 1)

    def test_closed_saved_only_when_it_was_open(self):
        app = _fake_app()
        app._overlay_open = True
        mainGUI.App._on_overlay_closed(app)
        self.assertFalse(app._overlay_open)
        self.assertEqual(app.saves, 1)
        mainGUI.App._on_overlay_closed(app)
        self.assertEqual(app.saves, 1)


class TestAppToggleOverlay(unittest.TestCase):
    def setUp(self):
        _FakeOverlay.instances = []

    def test_open_passes_saved_values_and_callbacks(self):
        app = _fake_app()
        app._overlay_rect = (10, 20, 600, 400)
        app._overlay_alpha = 0.4
        with patch.object(mainGUI, "LogOverlay", _FakeOverlay):
            mainGUI.App._toggle_overlay(app)
        self.assertEqual(len(_FakeOverlay.instances), 1)
        o = _FakeOverlay.instances[0]
        self.assertIs(app._overlay, o)
        self.assertIs(o.parent, app)
        self.assertEqual(o.rect, (10, 20, 600, 400))
        self.assertEqual(o.alpha, 0.4)
        self.assertEqual(o.on_geometry, app._on_overlay_geometry)
        self.assertEqual(o.on_alpha, app._on_overlay_alpha)
        self.assertEqual(o.on_close, app._on_overlay_closed)
        self.assertTrue(app._overlay_open)
        self.assertEqual(app.saves, 1)

    def test_open_with_nothing_saved_uses_defaults(self):
        app = _fake_app()
        with patch.object(mainGUI, "LogOverlay", _FakeOverlay):
            mainGUI.App._toggle_overlay(app)
        o = _FakeOverlay.instances[0]
        self.assertIsNone(o.rect)
        self.assertEqual(o.alpha, 0.75)

    def test_toggle_open_then_close(self):
        app = _fake_app()
        with patch.object(mainGUI, "LogOverlay", _FakeOverlay):
            mainGUI.App._toggle_overlay(app)
            opened = app._overlay
            mainGUI.App._toggle_overlay(app)
        self.assertEqual(opened.close_calls, 1)
        self.assertIsNone(app._overlay)
        self.assertFalse(app._overlay_open)
        self.assertEqual(app.saves, 2)      # 開いたとき・閉じたとき 1 回ずつ
        self.assertEqual(len(_FakeOverlay.instances), 1)

    def test_toggle_reopens_with_values_changed_meanwhile(self):
        app = _fake_app()
        with patch.object(mainGUI, "LogOverlay", _FakeOverlay):
            mainGUI.App._toggle_overlay(app)
            first = app._overlay
            first.on_geometry((30, 40, 700, 500))
            first.on_alpha(0.6)
            mainGUI.App._toggle_overlay(app)
            mainGUI.App._toggle_overlay(app)
        second = _FakeOverlay.instances[1]
        self.assertEqual((second.rect, second.alpha), ((30, 40, 700, 500), 0.6))

    def test_closed_overlay_is_replaced_not_closed_again(self):
        app = _fake_app()
        old = _FakeOverlay(app)
        old._closed = True
        app._overlay = old
        with patch.object(mainGUI, "LogOverlay", _FakeOverlay):
            mainGUI.App._toggle_overlay(app)
        self.assertEqual(old.close_calls, 0)
        self.assertIsNot(app._overlay, old)


class TestOpenOverlayAtStartup(unittest.TestCase):
    def setUp(self):
        _FakeOverlay.instances = []

    def test_opens_when_it_was_open(self):
        app = _fake_app()
        app._overlay_open = True
        app._open_overlay = MagicMock()
        mainGUI.App._open_overlay_if_it_was_open(app)
        app._open_overlay.assert_called_once_with()

    def test_does_not_open_when_it_was_closed(self):
        app = _fake_app()
        app._open_overlay = MagicMock()
        mainGUI.App._open_overlay_if_it_was_open(app)
        app._open_overlay.assert_not_called()

    def test_open_at_startup_does_not_schedule_a_save(self):
        """読んだ値が「開いていた」のまま開くだけなので、保存の予約は要らない"""
        app = _fake_app()
        app._overlay_open = True
        with patch.object(mainGUI, "LogOverlay", _FakeOverlay):
            mainGUI.App._open_overlay_if_it_was_open(app)
        self.assertEqual(len(_FakeOverlay.instances), 1)
        self.assertEqual(app.saves, 0)


if __name__ == "__main__":
    unittest.main()
