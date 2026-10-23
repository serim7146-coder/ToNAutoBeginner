"""マクロの停止を速く（EG）: 監視を先に止め、音量の見張りは「戻し終えた」まで（最大 1 秒）だけ待つ。
COM の片付けは待たない。本物の音量・COM は使わない"""
from tests.support import *  # noqa: F401,F403
import WindowVolume
import inspect


class _Audio:
    """偽の音量。release_com は gate が開くまで止まる（実機で 3 秒止まっていた片付けの代わり）"""

    def __init__(self, restore_sec=0.0):
        self.gate = threading.Event()
        self.restore_sec = restore_sec
        self.calls = []

    def read_volumes(self, pids):
        time.sleep(self.restore_sec)
        return {pid: 0.5 for pid in pids}

    def set_volume(self, pid, level):
        self.calls.append(("set", pid, level))
        return True

    def pid_of(self, hwnd):
        return 100 + hwnd

    def take_error(self):
        return None

    def release_com(self):
        self.calls.append("release")
        self.gate.wait(5)


class TestVolumeStop(unittest.TestCase):
    def _controller(self, audio):
        st = WindowState()
        logs = []
        ctl = WindowVolume.VolumeController(lambda: [(1, 1, st)], logs.append, audio=audio)
        ctl.set_enabled(True)
        ctl._original = {101: 0.8}                  # 前に変えた窓（戻す相手がある）
        self.addCleanup(audio.gate.set)
        return ctl, logs

    def test_stop_returns_after_restoring_even_if_com_cleanup_hangs(self):
        audio = _Audio()
        ctl, logs = self._controller(audio)
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01):
            ctl.start()
            started = time.monotonic()
            ctl.stop()
            took = time.monotonic() - started
        self.assertLess(took, 0.5, "片付け（止まっている）を待たない")
        self.assertIn("[停止] VRChat の音量を元に戻しました（1窓）", logs)
        self.assertIn(("set", 101, 0.8), audio.calls)
        self.assertIsNone(ctl._thread)

    def test_slow_restoring_waits_at_most_one_second(self):
        audio = _Audio(restore_sec=2.0)
        ctl, _logs = self._controller(audio)
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01), \
             patch.object(DebugLog, "write") as write:
            ctl.start()
            time.sleep(0.05)
            started = time.monotonic()
            ctl.stop()
            took = time.monotonic() - started
        self.assertGreaterEqual(took, config.WINDOW_VOLUME_STOP_WAIT_SEC - 0.05)
        self.assertLess(took, config.WINDOW_VOLUME_STOP_WAIT_SEC + 0.5)
        self.assertTrue(any("元に戻すのを 1.0 秒待ちました" in str(c) for c in write.call_args_list))

    def test_the_com_cleanup_time_is_logged(self):
        audio = _Audio()
        audio.gate.set()
        ctl, _logs = self._controller(audio)
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01), \
             patch.object(DebugLog, "write") as write:
            ctl.start()
            thread = ctl._thread
            ctl.stop()
            thread.join(2)
        self.assertTrue(any("[音量] COM を片付けた（" in str(c) for c in write.call_args_list))

    def test_restoring_is_signalled_even_on_an_error(self):
        audio = _Audio()
        audio.read_volumes = MagicMock(side_effect=RuntimeError("x"))
        ctl, _logs = self._controller(audio)
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01), patch.object(DebugLog, "exception"):
            ctl.start()
            started = time.monotonic()
            ctl.stop()
        self.assertLess(time.monotonic() - started, 0.5)

    def test_a_restart_waits_for_its_own_restoring(self):
        """同じ見張りをもう一度始めたら、前の回の「戻し終えた」は使わない"""
        audio = _Audio()
        ctl, logs = self._controller(audio)
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01):
            ctl.start()
            ctl.stop()
            audio.gate.set()                        # 前の回のスレッドを終わらせる
            audio.restore_sec = 0.3
            ctl._original = {101: 0.8}
            ctl.start()
            ctl.stop()
        self.assertEqual(logs.count("[停止] VRChat の音量を元に戻しました（1窓）"), 2, "2 回目も戻し終えてから返る")

    def test_the_wait_value(self):
        self.assertEqual(config.WINDOW_VOLUME_STOP_WAIT_SEC, 1.0)


class TestStopOrder(unittest.TestCase):
    def _app(self):
        app = type("FakeApp", (), {})()
        app.order = []
        app._entry_stop = threading.Event()
        m = MagicMock()
        m.cfg.hwnd = 0xA
        m.stop.side_effect = lambda: app.order.append("monitor")
        app.monitors = [m]
        app.logs = []
        app._log = app.logs.append
        app._stop_window_volume = lambda: app.order.append("volume")
        app._release_suicide_keys = lambda hs: list(hs)
        app._show_own_windows_again = lambda: None
        app._refresh_start_stop_button = lambda: None
        app._refresh_continue_choices = lambda *a, **k: None
        app.lbl_win_warn = MagicMock()
        return app

    def test_monitors_first_then_volume(self):
        app = self._app()
        with patch.object(Recorder, "stop_all"), \
             patch.object(SharedState, "clear_window_hwnds", side_effect=lambda: app.order.append("hwnds")):
            mainGUI.App._stop(app)
        self.assertEqual(app.order, ["hwnds", "monitor", "volume"])
        self.assertTrue(app._entry_stop.is_set())

    def test_the_whole_stop_is_quick_with_a_hanging_com_cleanup(self):
        """停止の要求から「停止しました」まで 0.5 秒以内（片付けが止まっていても）"""
        app = self._app()
        audio = _Audio()
        self.addCleanup(audio.gate.set)
        ctl = WindowVolume.VolumeController(lambda: [], lambda _m: None, audio=audio)
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01):
            ctl.start()
        app._window_volume = ctl
        app._stop_window_volume = lambda: mainGUI.App._stop_window_volume(app)
        with patch.object(Recorder, "stop_all"):
            started = time.monotonic()
            mainGUI.App._stop(app)
            took = time.monotonic() - started
        self.assertLess(took, 0.5)
        self.assertEqual(app.logs[-1], "[停止] マクロを停止しました")

    def test_closing_uses_the_same_stop(self):
        src = inspect.getsource(mainGUI.App._on_close)
        self.assertIn("self._stop()", src)
