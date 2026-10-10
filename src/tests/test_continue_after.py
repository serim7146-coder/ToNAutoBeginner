"""続行ラウンドの後（DJ）: アイテムを落とす（DropRight・private だけ）・窓を始まりの位置へ戻す"""
from tests.support import *  # noqa: F401,F403
import inspect

import win32con


START = (1699, 0, 2567, 702)            # 続行ラウンドの始まりの窓（実機の窓3）
CENTER = (846, 369, 1714, 1071)         # Alt+Enter で窓に戻ったとき（画面の中央・大きさは同じ）


class _World:
    """偽の窓・前面・カーソル・時計。起きたことは events に順に残す"""
    HWND = 0x30

    def __init__(self, test, state="normal", rect=START, windowed_after=3):
        self.state, self.rect = state, rect
        self.normal = START
        self.front = 0x900
        self.events = []
        self.now = [0.0]
        self.windowed_after = windowed_after    # Alt+Enter の後、この回数見たら窓に戻る（None は戻らない）
        self._polls = None
        self.written = []
        w = self

        def window_state(hwnd):
            if w._polls is not None:
                w._polls += 1
                if w.windowed_after is not None and w._polls >= w.windowed_after:
                    w.state, w.rect, w._polls = "normal", CENTER, None
            return w.state

        def borrow(hwnd):
            w.events.append("borrow")
            w.front = hwnd
            return True, "loan"

        def give_back(loan):
            w.events.append("return")
            w.front = 0x900
            return True

        def send_keys(keys):
            w.events.append(("keys", keys))
            w._polls = 0

        def move(hwnd, left, top):
            w.events.append(("move", left, top))
            w.rect = (left, top, left + w.rect[2] - w.rect[0], top + w.rect[3] - w.rect[1])
            return True

        def restore(hwnd):
            w.events.append("unmaximize")
            w.state, w.rect = "normal", w.normal
            return True

        def sleep(sec):
            w.now[0] += sec
        fake_time = type(sys)("time_for_test")
        fake_time.time = lambda: w.now[0]
        fake_time.monotonic = lambda: w.now[0]
        fake_time.sleep = sleep
        for p in (patch.object(WindowOperator, "window_state", side_effect=window_state),
                  patch.object(WindowOperator, "window_rect", side_effect=lambda h: w.rect),
                  patch.object(WindowOperator, "normal_rect", side_effect=lambda h: w.normal),
                  patch.object(WindowOperator, "move_window", side_effect=move),
                  patch.object(WindowOperator, "restore_without_activating", side_effect=restore),
                  patch.object(WindowOperator, "send_keys", side_effect=send_keys),
                  patch.object(WindowOperator, "borrow_front", side_effect=borrow),
                  patch.object(WindowOperator, "return_front", side_effect=give_back),
                  patch.object(WindowOperator, "cursor_position", return_value=(11, 22)),
                  patch.object(WindowOperator, "set_cursor_position",
                               side_effect=lambda p: w.events.append(("cursor", p)) or True),
                  patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: w.front),
                  patch.object(ActionExecutor, "time", fake_time),
                  patch.object(DebugLog, "write", side_effect=w.written.append)):
            p.start()
            test.addCleanup(p.stop)


class _Osc:
    def __init__(self, world):
        self.world = world

    def press(self, address, sec, stop=None):
        self.world.events.append(("osc", address, sec))
        return True


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_continue_drop_item(True)
        SharedState.set_continue_restore_window(True)
        self.addCleanup(SharedState.set_continue_drop_item, False)
        self.addCleanup(SharedState.set_continue_restore_window, False)

    def _executor(self, world, itype=config.INSTANCE_PRIVATE, osc=True, running=True):
        cfg = WindowConfig(hwnd=_World.HWND, osc_port=9000 if osc else 0)
        st = WindowState(instance_type=itype, window_idx=3)
        self.logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: running, self.logs.append)
        if osc:
            ex._osc = _Osc(world)
        return ex


class TestRememberAtTheStart(_Case):
    def test_a_normal_window(self):
        world = _World(self)
        ex = self._executor(world)
        ex.remember_continue_window()
        self.assertEqual(ex._continue_rect, START)
        self.assertIn(f"[操作] [窓3] 続行ラウンドの始まり: 窓の位置を覚えた {START}", world.written)

    def test_a_maximized_window_remembers_its_normal_rect(self):
        world = _World(self, state="maximized", rect=(-8, -8, 2568, 1400))
        ex = self._executor(world)
        ex.remember_continue_window()
        self.assertEqual(ex._continue_rect, START)

    def test_fullscreen_minimized_or_gone_is_not_remembered(self):
        for state in ("fullscreen", "minimized", None):
            world = _World(self, state=state)
            ex = self._executor(world)
            ex._continue_rect = (1, 2, 3, 4)            # 前の分は残さない
            ex.remember_continue_window()
            self.assertIsNone(ex._continue_rect, state)
            self.assertTrue(any("位置を覚えません" in m for m in world.written), state)

    def test_off_remembers_nothing(self):
        SharedState.set_continue_restore_window(False)
        world = _World(self)
        ex = self._executor(world)
        with patch.object(WindowOperator, "window_state") as state:
            ex.remember_continue_window()
        state.assert_not_called()
        self.assertIsNone(ex._continue_rect)


class TestAtTheEnd(_Case):
    def _end(self, world, **kw):
        ex = self._executor(world, **kw)
        ex._continue_rect = START
        ex.after_continue_round()
        return ex

    def test_fullscreen_drop_then_alt_enter_then_move_then_give_back(self):
        world = _World(self, state="fullscreen", rect=(0, 0, 2560, 1440))
        ex = self._end(world)
        self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1), "borrow", ("keys", "alt+enter"),
                                        ("move", 1699, 0), "return", ("cursor", (11, 22))])
        self.assertEqual(self.logs, ["続行ラウンドが終わったので、アイテムを落としました",
                                     "窓を元の位置に戻しました（フルスクリーンを解除）"])
        self.assertIsNone(ex._continue_rect, "1回戻したら忘れる")

    def test_only_moved_does_not_borrow_the_front(self):
        world = _World(self, rect=(100, 200, 968, 902))
        self._end(world)
        self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1), ("move", 1699, 0)])
        self.assertEqual(self.logs[-1], "窓を元の位置に戻しました")

    def test_not_moved_does_nothing_to_the_window(self):
        world = _World(self)
        self._end(world)
        self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1)])
        self.assertNotIn("窓を元の位置に戻しました", self.logs)

    def test_not_private_does_not_drop_but_restores(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_HOSHIIMO, config.INSTANCE_EMERALD_CITY):
            world = _World(self, state="fullscreen", rect=(0, 0, 2560, 1440))
            self._end(world, itype=itype)
            self.assertNotIn(("osc", "/input/DropRight", 0.1), world.events, itype)
            self.assertIn(("move", 1699, 0), world.events, itype)

    def test_a_window_without_osc_does_not_drop(self):
        world = _World(self, rect=(100, 200, 968, 902))
        self._end(world, osc=False)
        self.assertEqual(world.events, [("move", 1699, 0)])

    def test_not_back_to_a_window_moves_nothing_but_gives_the_front_back(self):
        world = _World(self, state="fullscreen", rect=(0, 0, 2560, 1440), windowed_after=None)
        self._end(world)
        self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1), "borrow", ("keys", "alt+enter"),
                                        "return", ("cursor", (11, 22))])
        self.assertTrue(any("窓に戻りません" in m for m in world.written), world.written)
        self.assertGreaterEqual(world.now[0], config.CONTINUE_WINDOWED_WAIT_SEC)
        self.assertLess(world.now[0], config.CONTINUE_WINDOWED_WAIT_SEC + 0.5)

    def test_a_maximized_window_is_restored_without_the_front(self):
        world = _World(self, state="maximized", rect=(-8, -8, 2568, 1400))
        world.normal = (1500, 0, 2368, 702)             # 元の矩形が、始まりの位置とずれている
        self._end(world)
        self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1), "unmaximize", ("move", 1699, 0)])
        self.assertEqual(world.front, 0x900, "前面は変えない")
        self.assertEqual(self.logs[-1], "窓を元の位置に戻しました（最大化を解除）")

    def test_a_maximized_window_already_in_place_is_only_restored(self):
        world = _World(self, state="maximized", rect=(-8, -8, 2568, 1400))
        self._end(world)
        self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1), "unmaximize"])
        self.assertEqual(self.logs[-1], "窓を元の位置に戻しました（最大化を解除）")

    def test_another_size_moves_only_the_position(self):
        world = _World(self, rect=(100, 200, 1100, 900))
        self._end(world)
        self.assertEqual(world.events[-1], ("move", 1699, 0))
        self.assertEqual(world.rect, (1699, 0, 2699, 700), "大きさはそのまま")
        self.assertTrue(any("大きさは変えずに位置だけ戻す" in m for m in world.written))

    def test_a_closed_or_minimized_window_is_left(self):
        for state in (None, "minimized"):
            world = _World(self, state=state, rect=(100, 200, 968, 902))
            self._end(world)
            self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1)], state)

    def test_nothing_remembered_nothing_restored(self):
        world = _World(self, rect=(100, 200, 968, 902))
        ex = self._executor(world)
        ex.after_continue_round()
        self.assertEqual(world.events, [("osc", "/input/DropRight", 0.1)])

    def test_stopped_does_nothing(self):
        world = _World(self, state="fullscreen", rect=(0, 0, 2560, 1440))
        self._end(world, running=False)
        self.assertEqual(world.events, [])

    def test_the_settings(self):
        for drop, restore, expected in (
                (False, True, [("move", 1699, 0)]),
                (True, False, [("osc", "/input/DropRight", 0.1)]),
                (False, False, [])):
            SharedState.set_continue_drop_item(drop)
            SharedState.set_continue_restore_window(restore)
            world = _World(self, rect=(100, 200, 968, 902))
            self._end(world)
            self.assertEqual(world.events, expected, (drop, restore))


class TestTheHooks(_Case):
    """続行フリーズを張った・外した瞬間（その窓だけ）。続行でないラウンドの終わり・止めたときは何もしない"""

    def setUp(self):
        super().setUp()
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)
        self.monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=_World.HWND, osc_port=9000), {},
                                             lambda _m: None, window_idx=3)
        self.monitor._running = True
        self.monitor.st.continue_hook = self.monitor._on_continue_freeze
        self.monitor._start_daemon = lambda target, *a: target(*a)
        self.calls = []
        self.monitor._action.remember_continue_window = lambda: self.calls.append("remember")
        self.monitor._action.after_continue_round = lambda: self.calls.append("after")

    def test_start_and_end(self):
        st = self.monitor.st
        SharedState.continue_round_start(st)
        SharedState.continue_round_start(st)            # 2回目は張らない
        self.assertEqual(self.calls, ["remember"])
        SharedState.continue_round_end(st)
        SharedState.continue_round_end(st)
        self.assertEqual(self.calls, ["remember", "after"])

    def test_a_round_that_was_not_continued(self):
        SharedState.continue_round_end(self.monitor.st)
        self.assertEqual(self.calls, [])

    def test_only_that_window(self):
        other = WindowState()
        other.continue_hook = lambda started: self.calls.append(("other", started))
        SharedState.continue_round_start(self.monitor.st)
        SharedState.continue_round_end(self.monitor.st)
        self.assertEqual(self.calls, ["remember", "after"])

    def test_a_stopped_monitor_does_not_restore(self):
        SharedState.continue_round_start(self.monitor.st)
        self.monitor._running = False
        SharedState.continue_round_end(self.monitor.st)
        self.assertEqual(self.calls, ["remember"])

    def test_a_window_of_the_previous_run_does_nothing(self):
        st = self.monitor.st
        SharedState.continue_round_start(st)
        st.run_id = -1                                  # 前の回の窓になった
        with patch.object(SharedState, "_stale", return_value=True):
            SharedState.continue_round_end(st)
        self.assertEqual(self.calls, ["remember"])

    def test_the_monitor_puts_its_hook_on_start(self):
        src = inspect.getsource(LogMonitor.LogMonitor.start)
        self.assertIn("self.st.continue_hook = self._on_continue_freeze", src)

    def test_the_continue_round_end_after_death_runs_it(self):
        """続行ラウンドの終わり（死亡から猶予の後に続行フリーズを外す）で動く"""
        st = self.monitor.st
        st.is_continue_round = True
        SharedState.continue_round_start(st)
        with patch.object(LogMonitor.time, "sleep"):
            self.monitor._release_continue_freeze_after_delay(st.round_seq)
        self.assertEqual(self.calls, ["remember", "after"])


class TestWindowOperatorParts(unittest.TestCase):
    """窓の状態・位置だけ動かす・前面を奪わない最大化の解除（win32 は偽物）"""
    HWND = 0x30

    def setUp(self):
        g = WindowOperator.win32gui
        self.patches = {name: patch.object(g, name) for name in
                        ("IsWindow", "IsIconic", "GetWindowPlacement", "GetWindowLong", "GetWindowRect",
                         "SetWindowPos", "SetWindowPlacement")}
        self.m = {name: p.start() for name, p in self.patches.items()}
        for p in self.patches.values():
            self.addCleanup(p.stop)
        self.m["IsWindow"].return_value = True
        self.m["IsIconic"].return_value = False
        self.m["GetWindowPlacement"].return_value = (0, win32con.SW_SHOWNORMAL, (0, 0), (0, 0), START)
        self.m["GetWindowLong"].return_value = win32con.WS_CAPTION
        self.m["GetWindowRect"].return_value = START
        for p in (patch.object(WindowOperator.win32api, "MonitorFromWindow", return_value=1),
                  patch.object(WindowOperator.win32api, "GetMonitorInfo",
                               return_value={"Monitor": (0, 0, 2560, 1440)}),
                  patch.object(DebugLog, "write")):
            p.start()
            self.addCleanup(p.stop)

    def test_the_states(self):
        self.assertEqual(WindowOperator.window_state(self.HWND), "normal")
        self.m["GetWindowLong"].return_value = 0
        self.assertEqual(WindowOperator.window_state(self.HWND), "normal", "タイトルバーなしでも大きさが違う")
        self.m["GetWindowRect"].return_value = (0, 0, 2560, 1440)
        self.assertEqual(WindowOperator.window_state(self.HWND), "fullscreen")
        self.m["GetWindowLong"].return_value = win32con.WS_CAPTION
        self.assertEqual(WindowOperator.window_state(self.HWND), "normal", "タイトルバーがあれば窓")
        self.m["GetWindowPlacement"].return_value = (0, win32con.SW_SHOWMAXIMIZED, (0, 0), (0, 0), START)
        self.assertEqual(WindowOperator.window_state(self.HWND), "maximized")
        self.m["IsIconic"].return_value = True
        self.assertEqual(WindowOperator.window_state(self.HWND), "minimized")
        self.m["IsWindow"].return_value = False
        self.assertIsNone(WindowOperator.window_state(self.HWND))
        self.assertIsNone(WindowOperator.window_state(0))

    def test_move_only_the_position(self):
        self.assertTrue(WindowOperator.move_window(self.HWND, 1699, 0))
        self.m["SetWindowPos"].assert_called_once_with(
            self.HWND, 0, 1699, 0, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)

    def test_unmaximize_without_activating(self):
        self.m["GetWindowPlacement"].return_value = (2, win32con.SW_SHOWMAXIMIZED, (1, 1), (-8, -8), START)
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x900), \
             patch.object(WindowOperator, "focus_window") as focus:
            self.assertTrue(WindowOperator.restore_without_activating(self.HWND))
        self.m["SetWindowPlacement"].assert_called_once_with(
            self.HWND, (2, win32con.SW_SHOWNOACTIVATE, (1, 1), (-8, -8), START))
        focus.assert_not_called()

    def test_if_it_took_the_front_it_is_given_back(self):
        fronts = iter([0x900, self.HWND])
        with patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: next(fronts)), \
             patch.object(WindowOperator, "focus_window") as focus:
            WindowOperator.restore_without_activating(self.HWND)
        focus.assert_called_once_with(0x900)

    def test_the_normal_rect(self):
        self.assertEqual(WindowOperator.normal_rect(self.HWND), START)


class TestTheSettings(unittest.TestCase):
    def _load(self, data):
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

    def setUp(self):
        self.addCleanup(SharedState.set_continue_drop_item, False)
        self.addCleanup(SharedState.set_continue_restore_window, False)

    def test_on_by_default_and_restored(self):
        for data, expected in (({}, (True, True)),
                               ({"continue_drop_item": "x", "continue_restore_window": None}, (True, True)),
                               ({"continue_drop_item": False}, (False, True)),
                               ({"continue_restore_window": False}, (True, False)),
                               ({"continue_drop_item": False, "continue_restore_window": False}, (False, False))):
            SharedState.set_continue_drop_item(not expected[0])
            SharedState.set_continue_restore_window(not expected[1])
            self._load(data)
            self.assertEqual((SharedState.get_continue_drop_item(), SharedState.get_continue_restore_window()),
                             expected, data)

    def test_the_defaults_and_the_checks(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_CONTINUE_DROP_ITEM = _Setting(True, bool)", src)
        self.assertIn("_CONTINUE_RESTORE_WINDOW = _Setting(True, bool)", src)
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn("self.v_continue_drop_item = tk.BooleanVar(value=True)", gui)
        self.assertIn("self.v_continue_restore_window = tk.BooleanVar(value=True)", gui)
        self.assertIn('text="続行ラウンドの後にアイテムを落とす"', gui)
        self.assertIn('text="続行ラウンドの後に窓を元の位置に戻す"', gui)

    def test_the_checks_change_the_setting(self):
        app = type("FakeApp", (), {})()
        app.v_continue_drop_item = MagicMock(get=lambda: False)
        app.v_continue_restore_window = MagicMock(get=lambda: True)
        app._schedule_settings_save = MagicMock()
        mainGUI.App._on_continue_after_changed(app)
        self.assertEqual((SharedState.get_continue_drop_item(), SharedState.get_continue_restore_window()),
                         (False, True))
        app._schedule_settings_save.assert_called_once()

    def test_they_are_saved(self):
        SharedState.set_continue_drop_item(False)
        SharedState.set_continue_restore_window(True)
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
        self.assertEqual((saved["continue_drop_item"], saved["continue_restore_window"]), (False, True))

    def test_the_readme(self):
        readme = (Path(mainGUI.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("続行ラウンドの後にアイテムを落とす・窓を元の位置に戻す", readme)


class TestTheDropIsNotALoss(unittest.TestCase):
    """DropRight で落としたことはツールのアイテムロストの判定に入らない（Drop object の行は読まない）"""

    def test_a_drop_line_changes_nothing(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=1), {}, lambda _m: None, window_idx=1)
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        st.item_id = st.held_item_id = 29
        before = (st.item_id, st.held_item_id, st.last_lost_item_id, st.waiting_for_equip,
                  st.item_lost_this_round, st.equip_freeze_held)
        monitor._process(LINE_DROP)
        self.assertEqual((st.item_id, st.held_item_id, st.last_lost_item_id, st.waiting_for_equip,
                          st.item_lost_this_round, st.equip_freeze_held), before)
        self.assertIsNone(LogParser.parse(LINE_DROP))


LINE_DROP = ("2026.10.10 08:05:12 Debug      -  [Behaviour] Drop object: 'GuidancePlush, was equipped = True' "
             "Throw on release")
