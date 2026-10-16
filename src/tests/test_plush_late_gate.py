"""Guidance Plush の店の操作は、Connecting から一定時間以内のときだけ前面を借りる（FR）。
本物の入力・OSC・前面の操作は使わない（_fetch_in_front_locked・_borrow_front は偽）"""
from tests.support import *  # noqa: F401,F403
import inspect
from datetime import datetime

import ItemFetch

NOW = 90_000.0


def _executor(accepted_at=0.0, retries=0):
    st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=5, round_seq=9)
    st.begin_accepted_at = accepted_at
    logs = []
    ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x50, osc_port=9000, auto_begin=True), st,
                                       lambda: True, logs.append, auto_begin_active=lambda: True)
    ex._plush_retries = retries
    return ex, st, logs


class TestTheGateInFront(unittest.TestCase):
    def _run(self, since, plush=True, accepted=True):
        ex, st, _logs = _executor(accepted_at=(NOW - since) if accepted else 0.0)
        fetcher = MagicMock()
        self.written = []
        with patch.object(ActionExecutor.time, "time", return_value=NOW), \
             patch.object(DebugLog, "write", self.written.append), \
             patch.object(ex, "_fetch_in_front_locked", return_value="ok") as locked, \
             patch.object(ex, "_borrow_front", return_value=(True, "loan")) as borrow:
            outcome = ex._fetch_in_front(fetcher, "Event", 70, plush)
        return outcome, locked, borrow

    def test_later_than_the_limit_is_skipped(self):
        outcome, locked, borrow = self._run(6.0)
        self.assertEqual(outcome, "late")
        locked.assert_not_called()
        borrow.assert_not_called()
        self.assertIn("[操作] [窓5] アイテム取得: Connecting から 6.0 秒（上限 5.0 秒）→ この回は店の操作を見送る",
                      self.written)

    def test_within_the_limit_goes(self):
        outcome, locked, _b = self._run(3.0)
        self.assertEqual(outcome, "ok")
        locked.assert_called_once()

    def test_exactly_the_limit_goes(self):
        outcome, locked, _b = self._run(config.ITEM_FETCH_FRONT_LATEST_SEC)
        self.assertEqual(outcome, "ok")
        locked.assert_called_once()

    def test_just_over_the_limit_is_skipped(self):
        outcome, locked, _b = self._run(config.ITEM_FETCH_FRONT_LATEST_SEC + 0.01)
        self.assertEqual(outcome, "late")
        locked.assert_not_called()

    def test_unknown_connecting_time_goes(self):
        outcome, locked, _b = self._run(99.0, accepted=False)
        self.assertEqual(outcome, "ok")
        locked.assert_called_once()

    def test_not_plush_is_not_judged(self):
        outcome, locked, _b = self._run(6.0, plush=False)
        self.assertEqual(outcome, "ok")
        locked.assert_called_once()
        self.assertFalse(any("見送る" in m for m in self.written), self.written)

    def test_the_lock_is_taken_before_the_judgement(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor._fetch_in_front)
        self.assertLess(src.index("with SharedState._GLOBAL_ACTION_LOCK"),
                        src.index("ITEM_FETCH_FRONT_LATEST_SEC"))
        self.assertLess(src.index("fetcher._check()"), src.index("ITEM_FETCH_FRONT_LATEST_SEC"))

    def test_the_lock_is_released_when_skipping(self):
        self._run(6.0)
        self.assertTrue(SharedState._GLOBAL_ACTION_LOCK.acquire(blocking=False))
        SharedState._GLOBAL_ACTION_LOCK.release()

    def test_the_value_and_the_comment(self):
        self.assertEqual(config.ITEM_FETCH_FRONT_LATEST_SEC, 5.0)
        src = inspect.getsource(config)
        i = src.index("ITEM_FETCH_FRONT_LATEST_SEC = ")
        self.assertIn("ハイパーパラメーター", src[max(0, i - 600):i])


class TestTheScreenLog(unittest.TestCase):
    def test_late_says_it_is_skipped(self):
        ex, st, logs = _executor()
        fetcher = MagicMock()
        fetcher.move_to_shop.return_value = True
        ex._osc = MagicMock()
        with patch.object(ItemFetch, "Fetcher", return_value=fetcher), \
             patch.object(ex, "_fetch_in_front", return_value="late"), \
             patch.object(DebugLog, "write"):
            outcome = ex._fetch_item(9, "Event", 70, plush=True)
        self.assertEqual(outcome, "late")
        self.assertIn("⚠ アイテム取得: 順番待ちで間に合わないため、この回は見送ります（次のラウンドでまた）", logs)
        self.assertFalse(any("やめます" in m for m in logs), logs)
        ex._osc.stop_all.assert_called()


class TestTheRetryCount(unittest.TestCase):
    def _retry(self, outcome, retries=3):
        ex, st, _logs = _executor(retries=retries)
        ex._osc = MagicMock()
        ex._receiver = MagicMock()
        written = []
        with patch.object(RoundDecision, "guidance_plush_id", lambda: 70), \
             patch.object(SharedState, "get_item_fetch", lambda: True), \
             patch.object(SharedState, "get_item_begin_mode", lambda: False), \
             patch.object(ItemFetch, "available", lambda: True), \
             patch.object(DebugLog, "write", written.append), \
             patch.object(ex, "_fetch_item", return_value=outcome):
            ex._retry_plush(9)
        return ex, written

    def test_late_does_not_use_a_try(self):
        ex, written = self._retry("late")
        self.assertEqual(ex._plush_retries, 3)
        self.assertTrue(any("見送ったので回数は戻す（残り 3 回）" in m for m in written), written)

    def test_failed_uses_a_try(self):
        ex, written = self._retry("failed")
        self.assertEqual(ex._plush_retries, 2)
        self.assertFalse(any("見送ったので" in m for m in written), written)

    def test_ok_ends_it(self):
        ex, _w = self._retry("ok")
        self.assertEqual(ex._plush_retries, 0)

    def test_late_keeps_going_across_rounds(self):
        ex, _w = self._retry("late", retries=1)
        self.assertEqual(ex._plush_retries, 1)


class TestTheEntry(unittest.TestCase):
    def test_late_asks_for_a_retry_with_the_reason(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app._running = True
        app._entry_stop = threading.Event()
        action = MagicMock()
        action._receiver = MagicMock()
        action.fetch_guidance_plush.return_value = "late"
        monitor = MagicMock()
        monitor.cfg.hwnd = 0x40
        monitor._action = action
        app.monitors = [monitor]
        with patch.object(ToNEntry, "wait_verified", return_value="verified"):
            mainGUI.App._plush_after_entry(app, 4, 0x40, "log", 0)
        action.request_plush_retry.assert_called_once_with("順番待ちで間に合わない")


class TestTheConnectingTime(unittest.TestCase):
    CLOCK = 60_000.0

    def setUp(self):
        self.phase = datetime(2026, 10, 13, 9, 33, 17).timestamp()
        self.monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=True, hwnd=0x100, osc_port=9000), {},
                                             lambda _m: None, window_idx=5)
        self.monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.monitor.logger = lambda _m: None
        self.monitor._auto_begin_active = lambda: True
        self.monitor._verified.last_periodic = self.phase
        p = patch.object(DebugLog, "write")
        p.start()
        self.addCleanup(p.stop)

    def _stamp(self, at):
        return datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  "

    def _feed(self, at, body, clock):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(SharedState, "get_speed_detect", return_value=True), \
             patch.object(LogMonitor.time, "time", return_value=clock), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            self.monitor._process(self._stamp(at) + body)

    def _accept(self):
        st = self.monitor.st
        self._feed(self.phase + 250, "RoundOver", self.CLOCK)
        self._feed(self.phase + 263, "Verified Round End", self.CLOCK)
        st.last_begin_press_at = self.CLOCK - 0.05
        self._feed(self.phase + 270, "Verified", self.CLOCK)
        self.assertTrue(st.begin_done)

    def test_an_accepted_verified_stamps_the_wall_clock(self):
        self.assertEqual(self.monitor.st.begin_accepted_at, 0.0)
        self._accept()
        self.assertEqual(self.monitor.st.begin_accepted_at, self.CLOCK, "log_now ではなく time.time()")

    def test_a_periodic_one_does_not_stamp(self):
        st = self.monitor.st
        self._feed(self.phase + 263, "Verified Round End", self.CLOCK)
        st.last_begin_press_at = 0.0
        self._feed(self.phase + 300, "Verified", self.CLOCK)
        self.assertFalse(st.begin_done)
        self.assertEqual(st.begin_accepted_at, 0.0)

    def test_found_to_be_periodic_clears_it(self):
        self._accept()
        st = self.monitor.st
        st.log_now = self.phase + 270 + config.VERIFIED_ROUND_START_WAIT_SEC + 1
        self.monitor._running = True
        with patch.object(LogMonitor.threading, "Thread"):
            self.monitor._check_pending_verified()
        self.assertFalse(st.begin_done)
        self.assertEqual(st.begin_accepted_at, 0.0)

    def test_the_round_start_clears_it(self):
        self._accept()
        self._feed(self.phase + 282, "This round is taking place at Facility (12) and the round type is Classic",
                   self.CLOCK + 12)
        self.assertEqual(self.monitor.st.begin_accepted_at, 0.0)
