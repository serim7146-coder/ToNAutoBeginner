"""自爆の長押し: 合図で離す（死亡・リスポーン・自爆キャンセル・ツール停止）＋ 秒数を設定できる（依頼者 2026-11-17）。

長押しは、死亡を見ていなかった（自爆キャンセルとツール停止だけ）。0.5 秒で死んでも残りの秒数はキーが入り続けた。
合図（threading.Event）で起こして、押した本人のスレッドがその場で離す。秒数は設定の窓で 0.5〜10.0（既定 3.0）。

時計・窓・キー入力・OSC は偽物。本物の入力・窓・DB・ネットワークは触らない。
"""
import inspect
import types

from tests.support import *  # noqa: F401,F403
from tests.test_respawn_as_death import _Case, DIED, RESPAWN


def _fake_clock():
    now = [1000.0]
    fake = types.SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0],
        sleep=lambda sec: now.__setitem__(0, now[0] + sec))
    return fake, now


class _Executor(unittest.TestCase):
    """ActionExecutor だけを立てる（do_skip・_died_after_skip を直接回す）"""

    def setUp(self):
        SharedState.set_suicide_key("^")
        SharedState.set_suicide_hold_sec(config.SUICIDE_HOLD_SEC)
        self.addCleanup(SharedState.set_suicide_hold_sec, config.SUICIDE_HOLD_SEC)
        self.addCleanup(SharedState.set_suicide_key, config.SELF_SUICIDE_KEY)
        for name, value in (("SUICIDE_CONFIRM_SEC", 0.05), ("SUICIDE_RETRY_MAX", 3)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _executor(self, **state):
        state.setdefault("in_round", True)
        state.setdefault("round_seq", 7)
        st = WindowState(**state)
        logs = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=123, do_skip=True), st,
                                           lambda: True, logs.append)
        return ex, st, logs


class TestTheSignalIsRaised(_Case):
    """死亡・リスポーン・自爆キャンセル・ツール停止で、自爆の合図が立つ"""

    def test_death_wakes_the_hold(self):
        monitor = self._monitor()
        with patch.object(monitor._action, "wake_skip_hold") as wake:
            monitor._process(DIED)
        wake.assert_called_once_with()
        self.assertTrue(monitor.st.died_this_round)

    def test_a_respawn_in_the_round_also_wakes_it(self):
        monitor = self._monitor()
        with patch.object(monitor._action, "wake_skip_hold") as wake:
            monitor._process(RESPAWN)
        wake.assert_called_once_with()
        self.assertTrue(monitor.st.died_this_round)

    def test_the_real_signal_is_set_by_a_death(self):
        monitor = self._monitor()
        self.assertFalse(monitor._action._skip_wake.is_set())
        monitor._process(DIED)
        self.assertTrue(monitor._action._skip_wake.is_set())

    def test_the_cancel_key_wakes_the_hold(self):
        monitor = self._monitor()
        ex = monitor._action
        monitor.st.suicide_seq = monitor.st.round_seq       # 自爆の長押しの最中
        self.assertFalse(ex._skip_wake.is_set())

        self.assertEqual(ex.cancel_suicide(), "stopped")

        self.assertTrue(ex._skip_wake.is_set())

    def test_the_cancel_key_outside_a_round_does_not_wake_it(self):
        monitor = self._monitor(in_round=False)
        ex = monitor._action
        self.assertIsNone(ex.cancel_suicide())
        self.assertFalse(ex._skip_wake.is_set(), "ラウンド外のキャンセルは持ち越さない")

    def test_stopping_the_tool_wakes_the_hold(self):
        monitor = self._monitor()
        for name in ("start_velocity_receiver", "stop_velocity_receiver"):
            patcher = patch.object(monitor._action, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        for p in (patch.object(monitor, "_flush_item_use_log"),
                  patch.object(ItemUse, "unregister")):
            p.start()
            self.addCleanup(p.stop)
        self.assertFalse(monitor._action._skip_wake.is_set())

        monitor.stop()

        self.assertTrue(monitor._action._skip_wake.is_set())


class TestTheHold(_Executor):
    def test_the_signal_is_cleared_at_the_start_of_the_round_not_each_attempt(self):
        ex, st, _logs = self._executor()
        ex.wake_skip_hold()             # 前のラウンドの名残り
        seen = []

        def hold(hwnd, key, sec, stop=None, wake=None):
            seen.append(wake.is_set())
            if len(seen) == 1:
                wake.set()              # 1 回目の最中に何か（死亡以外）で立った
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            ex.do_skip()

        self.assertEqual(seen[:2], [False, True],
                         "ラウンドの頭で 1 回だけ下ろす。やり直しの各回では下ろさない")
        self.assertEqual(inspect.getsource(ActionExecutor).count("_skip_wake.clear()"), 1)

    def test_the_suicide_hold_passes_wake_not_stop(self):
        ex, st, _logs = self._executor()
        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda *a, **k: setattr(st, "died_this_round", True) or True) as hold:
            ex.do_skip()
        hold.assert_called_once_with(123, "^", config.SUICIDE_HOLD_SEC, wake=ex._skip_wake)

    def test_a_death_in_the_hold_logs_it_to_debug_log_only(self):
        ex, st, logs = self._executor(window_idx=2)
        written = []

        def hold(hwnd, key, sec, stop=None, wake=None):
            st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold), \
             patch.object(DebugLog, "write", side_effect=written.append):
            ex.do_skip()

        mine = [m for m in written if "死亡の合図で長押しを中断" in m]
        self.assertEqual(len(mine), 1, written)
        self.assertIn("設定 3秒", mine[0])
        self.assertFalse(any("中断" in m for m in logs), "画面のログは増やさない")

    def test_the_hold_uses_the_setting(self):
        SharedState.set_suicide_hold_sec(6.0)
        ex, st, logs = self._executor()
        seconds = []

        def hold(hwnd, key, sec, stop=None, wake=None):
            seconds.append(sec)
            st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            ex.do_skip()

        self.assertEqual(seconds, [6.0])
        self.assertIn("自爆実行中 (6秒・背面)…", logs)

    def test_the_default_log_is_the_same_as_before(self):
        ex, st, logs = self._executor()

        def hold(hwnd, key, sec, stop=None, wake=None):
            st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            ex.do_skip()

        self.assertIn("自爆実行中 (3秒・背面)…", logs)

    def test_a_change_during_the_hold_applies_from_the_next_attempt(self):
        """始まった長押しはその時の秒数のまま。やり直しの頭で読み直す"""
        ex, st, _logs = self._executor()
        seconds = []

        def hold(hwnd, key, sec, stop=None, wake=None):
            seconds.append(sec)
            SharedState.set_suicide_hold_sec(5.0)
            if len(seconds) == 2:
                st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            ex.do_skip()

        self.assertEqual(seconds, [3.0, 5.0])


class TestTheConfirmWait(_Executor):
    def test_the_confirm_wait_returns_at_once_on_death(self):
        ex, st, _logs = self._executor()
        with patch.object(config, "SUICIDE_CONFIRM_SEC", 5.0):
            def die():
                st.died_this_round = True
                ex.wake_skip_hold()
            timer = threading.Timer(0.1, die)
            timer.start()
            started = time.monotonic()
            result = ex._died_after_skip(7)
            took = time.monotonic() - started
            timer.join()

        self.assertTrue(result)
        self.assertLess(took, 1.0, "5 秒待たない")

    def test_the_confirm_wait_returns_at_once_on_cancel(self):
        """キャンセルは合図で起こされ、空回りせず抜ける（wait の呼び出しは 2 回以下）"""
        ex, st, _logs = self._executor()
        waits = []

        def wait(timeout=None):
            waits.append(timeout)
            ex.cancel_suicide()         # 待っている間にキャンセルのキー
            return True

        with patch.object(config, "SUICIDE_CONFIRM_SEC", 5.0), \
             patch.object(ex._skip_wake, "wait", side_effect=wait):
            result = ex._died_after_skip(7)

        self.assertFalse(result)
        self.assertLessEqual(len(waits), 2)
        self.assertEqual(st.suicide_cancelled_round, 7)

    def test_a_cancel_that_is_already_set_does_not_wait_at_all(self):
        ex, st, _logs = self._executor()
        ex.cancel_suicide()
        with patch.object(config, "SUICIDE_CONFIRM_SEC", 5.0), \
             patch.object(ex._skip_wake, "wait") as wait:
            self.assertFalse(ex._died_after_skip(7))
        wait.assert_not_called()

    def test_a_stop_ends_the_wait(self):
        running = [True]
        st = WindowState(in_round=True, round_seq=7)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=123), st, lambda: running[0],
                                           lambda _m: None)

        def wait(timeout=None):
            running[0] = False
            return True

        with patch.object(config, "SUICIDE_CONFIRM_SEC", 5.0), \
             patch.object(ex._skip_wake, "wait", side_effect=wait) as w:
            self.assertFalse(ex._died_after_skip(7))
        self.assertEqual(w.call_count, 1)

    def test_the_confirm_wait_still_caps_at_1_5_seconds(self):
        ex, st, _logs = self._executor()
        fake, now = _fake_clock()
        waits = []

        def wait(timeout=None):
            waits.append(timeout)
            now[0] += timeout
            return False

        with patch.object(config, "SUICIDE_CONFIRM_SEC", 1.5), \
             patch.object(ActionExecutor, "time", fake), \
             patch.object(ex._skip_wake, "wait", side_effect=wait):
            result = ex._died_after_skip(7)

        self.assertFalse(result)
        self.assertEqual(now[0], 1001.5)
        self.assertEqual(waits, [1.5])

    def test_the_confirm_limit_constant_is_unchanged(self):
        self.assertEqual(inspect.getsource(config).count("SUICIDE_CONFIRM_SEC   = 1.5"), 1)


class TestTheSetting(unittest.TestCase):
    def setUp(self):
        self.addCleanup(SharedState.set_suicide_hold_sec, config.SUICIDE_HOLD_SEC)

    def test_the_default(self):
        SharedState.set_suicide_hold_sec(config.SUICIDE_HOLD_SEC)
        self.assertEqual(SharedState.get_suicide_hold_sec(), 3.0)
        self.assertEqual((config.SUICIDE_HOLD_MIN, config.SUICIDE_HOLD_MAX), (0.5, 10.0))

    def test_out_of_range_is_clamped(self):
        for value, expected in (("", 3.0), ("abc", 3.0), (None, 3.0),
                                (float("nan"), 3.0), (float("inf"), 3.0), (float("-inf"), 3.0),
                                ("nan", 3.0), ([], 3.0),
                                (0, 0.5), (0.1, 0.5), (-5, 0.5), (99, 10.0), ("1e9", 10.0),
                                (0.5, 0.5), (10, 10.0), ("6", 6.0), (" 4.5 ", 4.5), (6.25, 6.25)):
            SharedState.set_suicide_hold_sec(value)
            self.assertEqual(SharedState.get_suicide_hold_sec(), expected, repr(value))
            self.assertIsInstance(SharedState.get_suicide_hold_sec(), float)

    def test_the_constant_is_not_used_on_the_hot_path(self):
        """実行経路（ActionExecutor・LogMonitor）は SharedState の設定を読む。config の既定値は直接読まない"""
        for module in (ActionExecutor, LogMonitor):
            self.assertNotIn("config.SUICIDE_HOLD_SEC", inspect.getsource(module), module.__name__)
        self.assertIn("get_suicide_hold_sec()", inspect.getsource(ActionExecutor))
        self.assertIn("get_suicide_hold_sec()", inspect.getsource(LogMonitor))


class TestTheSuccessWindow(_Case):
    """「死亡が自爆の結果か」の判定窓は、長押しの秒数（設定）＋ SUICIDE_CONFIRM_SEC に追従する"""

    def setUp(self):
        super().setUp()
        self.addCleanup(SharedState.set_suicide_hold_sec, config.SUICIDE_HOLD_SEC)
        patcher = patch.object(config, "SUICIDE_CONFIRM_SEC", 1.5)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _success(self, hold, elapsed):
        SharedState.set_suicide_hold_sec(hold)
        monitor = self._monitor()
        monitor.st._skip_time = 1000.0
        with patch.object(LogMonitor.time, "time", return_value=1000.0 + elapsed):
            monitor._process(DIED)
        return "✅ 自爆成功" in self._lines(monitor)

    def test_the_success_window_follows_the_setting(self):
        self.assertTrue(self._success(6.0, 7.0), "6 秒なら 7.0 秒後の死亡も成功")
        self.assertTrue(self._success(6.0, 7.5), "窓は 7.5 秒")
        self.assertFalse(self._success(6.0, 7.6))
        self.assertFalse(self._success(3.0, 7.0), "3 秒のままなら成功にならない")

    def test_the_default_window_is_the_same_as_before(self):
        self.assertTrue(self._success(3.0, 4.0))
        self.assertFalse(self._success(3.0, 5.0))


class TestSavedAndLoaded(unittest.TestCase):
    def setUp(self):
        self.addCleanup(SharedState.set_suicide_hold_sec, config.SUICIDE_HOLD_SEC)

    def _app(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        return app

    def test_it_is_saved(self):
        SharedState.set_suicide_hold_sec(6.0)
        app = self._app()
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin", "v_join_world",
                     "v_ton_access", "v_emergency_key", "v_start_key", "v_obs_enabled", "v_obs_freeze",
                     "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, MagicMock(get=lambda: ""))
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}), \
             patch.object(SecretStore, "protect", return_value=""):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            app._window_freeze_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)
        self.assertEqual(saved["suicide_hold_sec"], 6.0)

    def _load(self, data):
        app = self._app()
        app.v_suicide_hold = MagicMock()
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin", "v_join_world",
                     "v_ton_access", "v_instance_link", "v_emergency_key", "v_start_key",
                     "v_tnl", "v_obs_enabled", "v_obs_freeze", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, MagicMock())
        for name in ("_add_tool_row", "_refresh_emergency_key_label", "_refresh_start_key_label",
                     "_apply_obs_settings", "_apply_saved_window_settings",
                     "_load_own_list", "_log", "_load_window_volume_settings", "_load_fog_early_read_setting",
                     "_load_launch_options_setting"):
            setattr(app, name, lambda *a, **k: None)
        with patch.object(mainGUI, "load_settings", return_value=dict(data)), \
             patch.object(mainGUI, "save_settings"):
            mainGUI.App._load_saved_settings(app)
        return app

    def test_the_saved_value_is_loaded_and_shown(self):
        for data, expected in (({"suicide_hold_sec": 6.0}, 6.0),
                               ({"suicide_hold_sec": 4}, 4.0),
                               ({}, 3.0),
                               ({"suicide_hold_sec": "x"}, 3.0),
                               ({"suicide_hold_sec": None}, 3.0),
                               ({"suicide_hold_sec": 99}, 10.0),
                               ({"suicide_hold_sec": 0}, 0.5)):
            SharedState.set_suicide_hold_sec(8.0)       # 前の値が残っていないことも見る
            app = self._load(data)
            self.assertEqual(SharedState.get_suicide_hold_sec(), expected, data)
            app.v_suicide_hold.set.assert_called_with(f"{expected:g}")

    def test_the_entry_rounds_and_writes_back(self):
        for typed, expected_text, expected in (("abc", "3", 3.0), ("", "3", 3.0), ("0.1", "0.5", 0.5),
                                               ("99", "10", 10.0), ("6", "6", 6.0), ("4.5", "4.5", 4.5)):
            app = type("FakeApp", (), {})()
            app._schedule_settings_save = MagicMock()
            app.v_suicide_hold = MagicMock(get=lambda typed=typed: typed)
            app._refresh_suicide_hold_entry = lambda app=app: mainGUI.App._refresh_suicide_hold_entry(app)

            mainGUI.App._on_suicide_hold_changed(app)

            self.assertEqual(SharedState.get_suicide_hold_sec(), expected, typed)
            app.v_suicide_hold.set.assert_called_once_with(expected_text)
            app._schedule_settings_save.assert_called_once()

    def test_the_entry_is_in_the_settings_window(self):
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('text="自爆の長押し:"', gui)
        self.assertIn("self.v_suicide_hold = tk.StringVar(", gui)
        for event in ('"<Return>"', '"<FocusOut>"'):
            self.assertIn(event, gui)
        self.assertIn("SUICIDE_HOLD_MIN", gui)
        self.assertIn("SUICIDE_HOLD_MAX", gui)


if __name__ == "__main__":
    unittest.main()
