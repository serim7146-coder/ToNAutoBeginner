"""入室操作の前にマクロを自動で開始する（ED）と、入室操作とマクロのぶつかり。本物の入力・OSC・窓は使わない"""
from tests.support import *  # noqa: F401,F403
import inspect


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _app(entry=True, begin=True, running=False):
    app = type("FakeApp", (), {})()
    app.v_ton_entry = _Var(entry)
    app.v_ton_begin = _Var(begin)
    app._running = running
    app._start_stop_busy = False
    app.logs = []
    app._log = app.logs.append
    app.calls = []
    app.after_calls = []
    app.after = lambda ms, fn, *a: app.after_calls.append((ms, fn))
    app._refresh_start_stop_button = lambda: app.calls.append(("refresh", app._start_stop_busy))
    app._rearm_start_stop_button = lambda: None
    for name in ("_auto_start_before_entry",):
        setattr(app, name, getattr(mainGUI.App, name).__get__(app))
    return app


class TestAutoStart(unittest.TestCase):
    def _start_ok(self, app):
        def start():
            app.calls.append(("start", getattr(app, "_start_quiet", False)))
            app._running = True
        app._start = start

    def test_both_on_starts_like_the_button(self):
        app = _app()
        self._start_ok(app)
        app._auto_start_before_entry()
        self.assertEqual(app.calls, [("refresh", True), ("start", True)], "押せない表示にしてから・止まる画面を出さない")
        self.assertEqual(app.after_calls, [(config.START_STOP_REARM_MS, app._rearm_start_stop_button)])
        self.assertFalse(app._start_quiet)
        self.assertIn("[起動] 入室操作の前にマクロを自動で開始しました", app.logs)

    def test_either_off_or_running_does_nothing(self):
        for kwargs in ({"entry": False}, {"begin": False}, {"running": True}):
            app = _app(**kwargs)
            self._start_ok(app)
            app._auto_start_before_entry()
            self.assertEqual(app.calls, [], kwargs)
            self.assertEqual(app.logs, [], kwargs)

    def test_busy_does_not_start(self):
        app = _app()
        self._start_ok(app)
        app._start_stop_busy = True
        app._auto_start_before_entry()
        self.assertEqual(app.calls, [])
        self.assertIn("[起動] マクロの開始・停止の途中のため、自動では開始しません", app.logs)

    def test_a_failed_start_only_logs(self):
        app = _app()
        app._start = lambda: None                       # 窓が無い（始まらない）
        app._auto_start_before_entry()
        self.assertIn("[起動] ⚠ マクロを自動で開始できませんでした → 入室操作だけ行います", app.logs)
        app = _app()
        app._start = MagicMock(side_effect=RuntimeError("boom"))
        app._auto_start_before_entry()
        self.assertIn("[起動] ⚠ マクロを自動で開始できませんでした（boom）→ 入室操作だけ行います", app.logs)
        self.assertEqual(len(app.after_calls), 1, "失敗しても押せるように戻す")
        self.assertFalse(app._start_quiet)


class TestTheLaunchOrder(unittest.TestCase):
    """ログが揃った（またはタイムアウトした）後の順番。判定は裏のスレッド、この 4 つは画面のスレッド（FP）"""

    def _run(self, kind):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        order = []
        for name in ("_assign_logs", "_report_join_status", "_auto_start_before_entry", "_run_ton_entry"):
            setattr(app, name, (lambda n: lambda: order.append(n))(name))
        mainGUI.App._finish_launch_wait(app, kind, 2, 2, 1.0)
        return order

    def test_after_assigning_and_before_the_entry(self):
        self.assertEqual(self._run("ready"),
                         ["_assign_logs", "_report_join_status", "_auto_start_before_entry", "_run_ton_entry"])

    def test_the_timeout_path_too(self):
        self.assertEqual(self._run("timeout"),
                         ["_assign_logs", "_report_join_status", "_auto_start_before_entry", "_run_ton_entry"])

    def test_waiting_starts_a_background_thread(self):
        app = type("FakeApp", (), {})()
        app._wait_logs_worker = MagicMock()
        with patch.object(mainGUI.TaskStats, "thread") as thread:
            mainGUI.App._wait_logs_then_assign(app, 2, 0.0)
        self.assertEqual(thread.call_args.args[0], "mainGUI._wait_logs")
        self.assertTrue(thread.call_args.kwargs.get("daemon"))
        thread.return_value.start.assert_called_once_with()
        app._wait_logs_worker.assert_not_called()       # 呼び出し元では待たない
        thread.call_args.args[1]()                       # スレッドの本体は worker
        app._wait_logs_worker.assert_called_once_with(2, 0.0)


class TestQuietStart(unittest.TestCase):
    def test_no_modal_error_when_quiet(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = True
        app.tabs = []
        app.monitors = []
        app.logs = []
        app._log = app.logs.append
        app._resolve_tab_ports = lambda: None
        app._apply_obs_settings = lambda: None
        with patch.object(mainGUI.messagebox, "showerror") as error:
            app._start_quiet = True
            mainGUI.App._start(app)
            error.assert_not_called()
            self.assertIn("[起動] ⚠ 有効な窓/ログが見つかりません", app.logs)
            app._start_quiet = False
            mainGUI.App._start(app)
            error.assert_called_once()


class TestStopStopsBoth(unittest.TestCase):
    def test_stop_also_stops_the_entry(self):
        app = type("FakeApp", (), {})()
        app._entry_stop = threading.Event()
        app.monitors = [MagicMock()]
        app.logs = []
        app._log = app.logs.append
        app._stop_window_volume = lambda: None
        app._release_suicide_keys = lambda hs: list(hs)
        app._show_own_windows_again = lambda: None
        app._refresh_start_stop_button = lambda: None
        app.lbl_win_warn = MagicMock()
        with patch.object(Recorder, "stop_all"):
            mainGUI.App._stop(app)
        self.assertTrue(app._entry_stop.is_set(), "停止キー・「■ 停止」で入室操作も止まる")
        self.assertFalse(app._running)


# ── ぶつかり: 入室の Begin の後の Verified（調べた結果: ぶつからない）─────────────
class TestTheEntryBeginAndVerified(unittest.TestCase):
    """マクロを自動で始めた直後の監視は、まだ Verified Round End を見ていない（起動したばかりのログ）。
    入室の Begin の後の Verified は「Verified Round End より前」として無視され、Begin の受理（begin_done）・
    速度検知の横移動・定期の位相のどれにもならない。Guidance Plush は自分でログの Verified を見る（DZ）"""
    VERIFIED = "2026.10.12 10:00:10 Debug      -  Verified"

    def setUp(self):
        self.addCleanup(SharedState.set_speed_detect, SharedState.get_speed_detect())   # 前の値に戻す
        self.addCleanup(SharedState.set_hands_free, SharedState.get_hands_free())
        SharedState.set_speed_detect(True)
        SharedState.set_hands_free(False)

    def _monitor(self, auto_begin=True):
        cfg = WindowConfig(hwnd=0x30, auto_begin=auto_begin, osc_port=9000)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.logs = []
        monitor.logger = monitor.logs.append
        self.started = []
        monitor._start_daemon = lambda target, *a: self.started.append(target.__name__)
        return monitor

    def test_the_entry_verified_is_ignored(self):
        for auto_begin in (True, False):
            monitor = self._monitor(auto_begin)
            monitor._process(self.VERIFIED)
            self.assertIn("[窓1] Verified を無視（Verified Round End より前）", monitor.logs, auto_begin)
            self.assertFalse(monitor.st.begin_done, "Begin の受理にしない")
            self.assertIsNone(monitor._verified.last_periodic, "定期の位相を覚えない")
            self.assertEqual(self.started, [], "横移動・アイテム取得などを始めない")

    def test_the_entry_and_the_macro_share_the_one_lock(self):
        """入室のクリック（ToNEntry.click）もマクロの操作も SharedState._GLOBAL_ACTION_LOCK"""
        self.assertIn("SharedState._GLOBAL_ACTION_LOCK", inspect.getsource(ToNEntry.ToNEntry.click))
        self.assertIn("SharedState._GLOBAL_ACTION_LOCK", inspect.getsource(ActionExecutor.ActionExecutor._fetch_in_front))


class TestTheReadme(unittest.TestCase):
    def test_line(self):
        readme = (Path(mainGUI.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("入室操作の前にマクロを自動で開始します", readme)
