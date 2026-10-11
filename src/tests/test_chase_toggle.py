"""チェイス（F1・F2）を使わない設定（DT）。本物のキー・フックは使わない（keyboard は偽物）"""
from tests.support import *  # noqa: F401,F403


class _App:
    """チェイスのキーまわりだけを本物の App の関数で動かす偽物"""

    def __init__(self, test):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app._running = True
        app._capturing_key = False
        app.logs = []
        app._log = app.logs.append
        app.v_emergency_key = MagicMock(get=lambda: "f12")
        app.v_start_key = MagicMock(get=lambda: "")
        test.a = MagicMock()
        test.a.cfg.hwnd = 0xA
        test.a._action.chase_stop.return_value = True
        app.monitors = [test.a]
        app.after = lambda _ms, fn, *args: fn(*args)
        app._schedule_settings_save = MagicMock()
        for name in ("_hook_chase_keys", "_unhook_chase_keys", "_on_chase_key", "_chase_key_down",
                     "_on_chase_enabled_changed", "_suicide_cancel_key_conflict"):
            setattr(app, name, (lambda n: lambda *a: getattr(mainGUI.App, n)(app, *a))(name))
        app.v_chase_enabled = MagicMock()
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
        p = patch.object(WindowOperator, "foreground_hwnd", return_value=0xA)
        p.start()
        test.addCleanup(p.stop)


class TestChaseToggle(unittest.TestCase):
    def setUp(self):
        SharedState.set_chase_enabled(True)
        self.addCleanup(SharedState.set_chase_enabled, True)
        self.app = _App(self).app

    def _press(self, key):
        self.handlers[key](type("Event", (), {"event_type": "down"})())
        self.handlers[key](type("Event", (), {"event_type": "up"})())

    def _set(self, on):
        self.app.v_chase_enabled.get = lambda: on
        self.app._on_chase_enabled_changed()

    def test_on_by_default_hooks_f1_and_f2(self):
        self.assertTrue(SharedState.get_chase_enabled())
        self.app._hook_chase_keys()
        self.assertEqual([c.args[0] for c in self.keyboard.hook_key.call_args_list],
                         [config.CHASE_CW_KEY, config.CHASE_CCW_KEY])
        self._press(config.CHASE_CW_KEY)
        self.a.on_chase_key.assert_called_once()

    def test_off_unhooks_and_f1_does_nothing(self):
        self.app._hook_chase_keys()
        hooks = list(self.app._chase_hooks)
        self._set(False)
        self.assertEqual([c.args[0] for c in self.keyboard.unhook.call_args_list], hooks, "フックを外した")
        self.assertEqual(self.app._chase_hooks, [])
        self.assertFalse(SharedState.get_chase_enabled())
        self._press(config.CHASE_CW_KEY)            # 外す前に積まれていた通知が来ても
        self.a.on_chase_key.assert_not_called()
        self.assertIn("[チェイス] チェイス（F1・F2）を使いません", self.app.logs)
        self.app._schedule_settings_save.assert_called()

    def test_starting_while_off_does_not_hook(self):
        SharedState.set_chase_enabled(False)
        self.app._hook_chase_keys()
        self.keyboard.hook_key.assert_not_called()

    def test_on_again_hooks_again_once(self):
        self.app._hook_chase_keys()
        self._set(False)
        self.keyboard.hook_key.reset_mock()
        self._set(True)
        self.assertEqual([c.args[0] for c in self.keyboard.hook_key.call_args_list],
                         [config.CHASE_CW_KEY, config.CHASE_CCW_KEY])
        self.assertIn("[チェイス] チェイス（F1・F2）を使います", self.app.logs)
        self.keyboard.hook_key.reset_mock()
        before = list(self.app._chase_hooks)
        self.keyboard.unhook.reset_mock()
        self.app._hook_chase_keys()                 # もう一度呼んでも二重に拾わない
        self.assertEqual([c.args[0] for c in self.keyboard.unhook.call_args_list], before,
                         "前のフックを外してから拾い直す")
        self.assertEqual(len(self.app._chase_hooks), 2)
        self._press(config.CHASE_CCW_KEY)
        self.a.on_chase_key.assert_called_once()

    def test_a_running_chase_is_stopped_and_released(self):
        self.app._hook_chase_keys()
        self._set(False)
        self.a._action.chase_stop.assert_called_once()
        self.a._log.assert_called_once_with("チェイス停止（チェイスを使わない設定）")

    def test_the_real_chase_lets_go(self):
        """回っている窓（本物の ActionExecutor）は止まり、使っていた 2 つを離す"""
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, in_round=True)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0xA, osc_port=9000), st, lambda: True,
                                           lambda _m: None)
        sent = []
        ex._osc = MagicMock(send=lambda a, v: sent.append((a, v)) or True)
        with patch.object(config, "CHASE_RESEND_SEC", 0.01):
            ex.chase_key("cw")
            _wait_until(lambda: ("/input/MoveLeft", 1) in sent)
            self.a._action = ex
            self._set(False)
        self.assertIsNone(ex.chase_direction)
        self.assertIn(("/input/MoveLeft", 0), sent)
        self.assertIn(("/input/LookRight", 0), sent)

    def test_off_frees_f1_and_f2_for_other_keys(self):
        self.assertEqual(self.app._suicide_cancel_key_conflict(config.CHASE_CW_KEY), "チェイス")
        SharedState.set_chase_enabled(False)
        self.assertIsNone(self.app._suicide_cancel_key_conflict(config.CHASE_CW_KEY))
        self.assertIsNone(self.app._suicide_cancel_key_conflict(config.CHASE_CCW_KEY))
        self.assertEqual(self.app._suicide_cancel_key_conflict("f12"), "緊急停止", "ほかのキーの確認は今どおり")


class TestTheSetting(unittest.TestCase):
    def setUp(self):
        self.addCleanup(SharedState.set_chase_enabled, True)

    def test_on_by_default_and_the_check(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_CHASE_ENABLED = _Setting(True, bool)", src)
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn("self.v_chase_enabled = tk.BooleanVar(value=True)", gui)
        self.assertIn('text="チェイスを使う"', gui)         # DV2: キーの表示はキーの行に任せる
        readme = (Path(mainGUI.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("「チェイスを使う」", readme)

    def test_load(self):
        for data, expected in (({}, True), ({"chase_enabled": "x"}, True), ({"chase_enabled": False}, False)):
            SharedState.set_chase_enabled(not expected)
            app = _with_cancel_key(type("FakeApp", (), {})())
            app.tabs = []
            for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin", "v_join_world",
                         "v_ton_access", "v_instance_link", "v_emergency_key", "v_start_key",
                         "v_freeze_8pages", "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                         "v_obs_port", "v_obs_password"):
                setattr(app, name, MagicMock())
            app.v_freeze_rounds = {}
            for name in ("_add_tool_row", "_refresh_emergency_key_label", "_refresh_start_key_label",
                         "_apply_freeze_settings", "_apply_obs_settings", "_apply_saved_window_settings",
                         "_load_tnl", "_log", "_load_window_volume_settings", "_load_fog_early_read_setting",
                         "_load_launch_options_setting"):
                setattr(app, name, lambda *a, **k: None)
            with patch.object(mainGUI, "load_settings", return_value=dict(data)), \
                 patch.object(mainGUI, "save_settings"):
                mainGUI.App._load_saved_settings(app)
            self.assertEqual(SharedState.get_chase_enabled(), expected, data)

    def test_saved(self):
        SharedState.set_chase_enabled(False)
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin", "v_join_world",
                     "v_ton_access", "v_freeze_8pages", "v_freeze_punish", "v_emergency_key",
                     "v_start_key", "v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, MagicMock(get=lambda: ""))
        app.v_freeze_rounds = {}
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}), \
             patch.object(SecretStore, "protect", return_value=""):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)
        self.assertIs(saved["chase_enabled"], False)
