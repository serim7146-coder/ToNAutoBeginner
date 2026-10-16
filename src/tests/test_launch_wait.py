"""起動時のログ待ちを裏のスレッドへ・判定の中身を debug.log へ（FP）、
mDNS の広告に失敗していたら OSCQuery を待たずに固定ポート（FP）。本物の窓・ログフォルダ・mDNS・UDP は使わない"""
from tests.support import *  # noqa: F401,F403
import inspect

import VRChatDiscovery
from tests.test_oscquery import _Case
from tests.test_oscquery_first import _FirstCase


def _stamp(second=40):
    return datetime(2026, 10, 16, 15, 21, second).timestamp()


def _log_name(second):
    return Path(f"output_log_2026-10-16_15-21-{second:02d}.txt")


class TestLaunchLogStatus(unittest.TestCase):
    def test_the_count_is_the_same_as_count_time_matched_logs(self):
        windows = [(0x10, _stamp()), (0x20, _stamp(50)), (0x30, None)]
        logs = [_log_name(38), _log_name(51), Path("output_log_2026-10-16_17-00-00.txt")]
        ready, _lines = VRChatDiscovery.launch_log_status(windows, logs, 120.0)
        self.assertEqual(ready, VRChatDiscovery.count_time_matched_logs(windows, logs, 120.0))
        self.assertEqual(ready, 2)
        ready, _lines = VRChatDiscovery.launch_log_status(windows, logs, 0.5)
        self.assertEqual(ready, VRChatDiscovery.count_time_matched_logs(windows, logs, 0.5))
        self.assertEqual(ready, 0)

    def test_a_line_per_window_with_the_nearest_log(self):
        ready, lines = VRChatDiscovery.launch_log_status(
            [(0x8fb1566, _stamp())], [_log_name(38), _log_name(10)], 120.0)
        self.assertEqual(ready, 1)
        self.assertEqual(lines, ["  窓 hwnd=0x8fb1566: 起動 15:21:40 → 最も近いログ "
                                 "output_log_2026-10-16_15-21-38.txt（差 -2 秒）"])

    def test_a_log_after_the_window_has_a_positive_difference(self):
        _ready, lines = VRChatDiscovery.launch_log_status([(0x10, _stamp())], [_log_name(43)], 120.0)
        self.assertIn("（差 +3 秒）", lines[0])

    def test_the_difference_is_rounded_to_whole_seconds(self):
        _ready, lines = VRChatDiscovery.launch_log_status(
            [(0x10, _stamp() + 0.6)], [_log_name(40)], 120.0)
        self.assertIn("（差 -1 秒）", lines[0])
        _ready, lines = VRChatDiscovery.launch_log_status(
            [(0x10, _stamp() - 0.4)], [_log_name(40)], 120.0)
        self.assertIn("（差 +0 秒）", lines[0])

    def test_a_window_without_a_start_time_is_unknown(self):
        ready, lines = VRChatDiscovery.launch_log_status([(0x10, None)], [_log_name(38)], 120.0)
        self.assertEqual(ready, 0)
        self.assertEqual(lines, ["  窓 hwnd=0x10: 起動 不明"])

    def test_no_candidate(self):
        ready, lines = VRChatDiscovery.launch_log_status([(0x10, _stamp())], [], 120.0)
        self.assertEqual(ready, 0)
        self.assertEqual(lines, ["  窓 hwnd=0x10: 起動 15:21:40 → 候補なし"])
        # ログ時刻の取れない名前だけでも「候補なし」
        _ready, lines = VRChatDiscovery.launch_log_status([(0x10, _stamp())], [Path("other.txt")], 120.0)
        self.assertEqual(lines, ["  窓 hwnd=0x10: 起動 15:21:40 → 候補なし"])

    def test_no_windows(self):
        self.assertEqual(VRChatDiscovery.launch_log_status([], [_log_name(38)], 120.0), (0, []))


def _app():
    app = type("FakeApp", (), {})()
    app._live_candidates = lambda c: c
    return app


class TestPollLaunchLogs(unittest.TestCase):
    def _poll(self, ready, waited, expected=2):
        app = _app()
        windows = [(0x10, _stamp()), (0x20, None)]
        logs = [_log_name(38), _log_name(39), _log_name(40)]
        with patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time", return_value=windows), \
             patch.object(VRChatDiscovery, "find_latest_logs", return_value=logs), \
             patch.object(VRChatDiscovery, "launch_log_status", return_value=(ready, ["  窓 x"])):
            return mainGUI.App._poll_launch_logs(app, expected, waited)

    def test_ready(self):
        self.assertEqual(self._poll(ready=2, waited=1.0), ("ready", 2, 2, 1, 3, ["  窓 x"]))

    def test_ready_wins_even_at_the_timeout(self):
        self.assertEqual(self._poll(ready=2, waited=config.LAUNCH_LOG_TIMEOUT)[0], "ready")

    def test_timeout(self):
        self.assertEqual(self._poll(ready=1, waited=config.LAUNCH_LOG_TIMEOUT)[:2], ("timeout", 1))

    def test_still_waiting(self):
        self.assertEqual(self._poll(ready=1, waited=config.LAUNCH_LOG_TIMEOUT - 1)[:2], (None, 1))


class TestWaitLogsWorker(unittest.TestCase):
    def _run(self, finish_at, call_soon=None):
        """finish_at 秒で揃う偽の判定で worker を流す。DebugLog の行・call_soon の呼びを返す"""
        app = _app()
        polled = []

        def poll(expected, waited):
            polled.append(waited)
            kind = "ready" if waited >= finish_at else None
            return kind, 1, 2, 2, 3, ["  窓 hwnd=0x10: 起動 15:21:40 → 候補なし"]
        app._poll_launch_logs = poll
        app._finish_launch_wait = MagicMock()
        with patch.object(DebugLog, "write") as write, \
             patch.object(mainGUI.time, "sleep") as sleep, \
             patch.object(mainGUI.UiInbox, "call_soon", side_effect=call_soon) as soon:
            mainGUI.App._wait_logs_worker(app, 2, 0.0)
        return app, polled, [c.args[0] for c in write.call_args_list], sleep, soon

    def test_the_diagnosis_comes_at_the_first_every_10_seconds_and_the_last(self):
        _app_, polled, lines, sleep, _soon = self._run(finish_at=12.0)
        self.assertEqual(polled, [float(s) for s in range(13)], "1 秒ごとに判定する")
        self.assertEqual(sleep.call_count, 12)
        sleep.assert_called_with(config.LAUNCH_LOG_POLL_SEC)
        summaries = [l for l in lines if l.startswith("[起動] ログ待ち")]
        self.assertEqual(summaries, ["[起動] ログ待ち 0秒: 窓 2（起動時刻あり 2）・候補 3 本・一致 1/2",
                                     "[起動] ログ待ち 10秒: 窓 2（起動時刻あり 2）・候補 3 本・一致 1/2",
                                     "[起動] ログ待ち 12秒: 窓 2（起動時刻あり 2）・候補 3 本・一致 1/2"])
        self.assertEqual(len(lines), 6, "要約のあとに窓ごとの 1 行")
        self.assertEqual(lines[1], "  窓 hwnd=0x10: 起動 15:21:40 → 候補なし")

    def test_it_is_not_written_between(self):
        _app_, _polled, lines, _sleep, _soon = self._run(finish_at=9.0)
        waits = [l for l in lines if l.startswith("[起動] ログ待ち")]
        self.assertEqual([w.split(":")[0] for w in waits], ["[起動] ログ待ち 0秒", "[起動] ログ待ち 9秒"])

    def test_the_lengths_are_unchanged(self):
        self.assertEqual(config.LAUNCH_LOG_DIAG_SEC, 10.0)
        self.assertEqual((config.LAUNCH_LOG_TIMEOUT, config.LAUNCH_LOG_POLL_SEC,
                          config.LOG_MATCH_TOLERANCE_SEC), (60.0, 1.0, 120.0))

    def test_it_hands_the_result_to_the_screen_thread_once(self):
        app, _polled, _lines, _sleep, soon = self._run(finish_at=2.0)
        self.assertEqual(soon.call_count, 1)
        self.assertIs(soon.call_args.args[0], app)
        soon.call_args.args[1]()                    # 画面のスレッドで走るのは _finish_launch_wait
        app._finish_launch_wait.assert_called_once_with("ready", 1, 2, 2.0)

    def test_the_diagnosis_stays_out_of_the_screen_log(self):
        app = _app()
        app._log = MagicMock()
        app._poll_launch_logs = lambda e, w: ("ready", 2, 2, 2, 3, ["  窓 hwnd=0x10: 起動 不明"])
        with patch.object(DebugLog, "write"), patch.object(mainGUI.UiInbox, "call_soon"):
            mainGUI.App._wait_logs_worker(app, 2, 0.0)
        app._log.assert_not_called()

    def test_closing_the_tool_while_waiting_does_not_crash(self):
        _app_, _polled, _lines, _sleep, soon = self._run(
            finish_at=1.0, call_soon=tk.TclError("application has been destroyed"))
        self.assertEqual(soon.call_count, 1)


class TestWaitLogsSource(unittest.TestCase):
    def test_the_wait_does_not_use_the_screen_thread(self):
        src = inspect.getsource(mainGUI.App._wait_logs_then_assign)
        self.assertNotIn("self.after(", src)
        self.assertIn('TaskStats.thread("mainGUI._wait_logs"', src)

    def test_the_worker_hands_back_through_the_inbox(self):
        src = inspect.getsource(mainGUI.App._wait_logs_worker)
        self.assertIn("UiInbox.call_soon(", src)
        self.assertNotIn("self.after(", src)
        self.assertNotIn("self._log(", src, "判定の中身は画面のログに出さない")

    def test_the_caller_is_unchanged(self):
        src = inspect.getsource(mainGUI.App._on_launch_finished)
        self.assertIn("self._wait_logs_then_assign(existing + found, 0.0)", src)

    def test_the_screen_messages(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        for name in ("_assign_logs", "_report_join_status", "_auto_start_before_entry", "_run_ton_entry"):
            setattr(app, name, lambda: None)
        mainGUI.App._finish_launch_wait(app, "ready", 2, 2, 3.0)
        mainGUI.App._finish_launch_wait(app, "timeout", 1, 2, 60.0)
        self.assertEqual(app.logs, ["[起動] ログ生成を確認（3.0秒）", "[起動] ログ生成待ちがタイムアウト（1/2）"])


# ── ② mDNS の広告の結果 ──
class TestAdvertisedResult(_Case):
    def _service(self):
        self.assertTrue(OSCQuery.ensure())
        return OSCQuery.current()

    def test_advertised(self):
        service = self._service()
        self.assertTrue(service._advert_done.wait(2))
        self.assertTrue(service.advertised)
        self.assertIs(OSCQuery.wait_advertised(1.0), True)

    def test_a_failed_advert_is_a_result_too(self):
        with patch.object(OSCQuery, "_advertise", side_effect=RuntimeError("KeyError: '__pyx_vtable__'")), \
             patch.object(DebugLog, "exception"):
            service = self._service()
            self.assertTrue(service._advert_done.wait(2), "失敗でも結果は出る（待たせ続けない）")
        self.assertFalse(service.advertised)
        self.assertIs(OSCQuery.wait_advertised(1.0), False)

    def test_the_result_is_set_even_when_the_inside_raises(self):
        service = OSCQuery.Service()
        with patch.object(service, "_do_advertise", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                service._advertise()
        self.assertTrue(service._advert_done.is_set())
        self.assertFalse(service.advertised)

    def test_no_service_is_none(self):
        self.assertIsNone(OSCQuery.current())
        self.assertIsNone(OSCQuery.wait_advertised(0.01))

    def test_the_wait_is_bounded_by_the_timeout(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def slow(name, http_port, osc_port):
            gate.wait(5)
            return ("zc", name)
        with patch.object(OSCQuery, "_advertise", side_effect=slow):
            service = self._service()
            started = time.time()
            self.assertIs(OSCQuery.wait_advertised(0.05), False, "結果が出る前は False")
            self.assertLess(time.time() - started, 1.0)
            gate.set()
            self.assertTrue(service._advert_done.wait(2))
            self.assertTrue(service.advertised)


class TestFixedPortWithoutAdvert(_FirstCase):
    LONG = 60

    def _failing(self):
        for p in (patch.object(OSCQuery, "_advertise", side_effect=RuntimeError("mDNS")),
                  patch.object(DebugLog, "exception")):
            p.start()
            self.addCleanup(p.stop)

    def test_a_failed_advert_opens_the_port_at_once(self):
        self._failing()
        with patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", self.LONG), \
             patch.object(DebugLog, "write") as write:
            r = self._receiver(11, 9011, "窓1")
            started = time.time()
            r.start()
            self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
            self.assertLess(time.time() - started, 1.0, "15 秒（ここでは 60 秒）待たない")
            self.assertEqual(self.ports.binds, [("127.0.0.1", 9011)])
        lines = [c.args[0] for c in write.call_args_list]
        self.assertIn("[速度受信] 窓1: mDNS の広告が無い（OSCQuery では届かない）→ 固定ポート 9011 で受信", lines)

    def test_a_successful_advert_still_waits(self):
        with patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", self.LONG):
            r = self._receiver(11, 9011, "窓1")
            r.start()
            service = OSCQuery.current()
            self.assertTrue(service._advert_done.wait(2))
            self.assertTrue(service.advertised)
            time.sleep(0.2)
            self.assertEqual(r.mode, OSCReceiver.MODE_WAITING, "広告できていれば OSCQuery で届くのを待つ")
            self.assertEqual(self.ports.binds, [])

    def test_a_successful_advert_falls_back_after_the_wait_with_the_old_line(self):
        r = self._receiver(11, 9011, "窓1")
        with patch.object(DebugLog, "write") as write:
            r.start()
            self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
        lines = [c.args[0] for c in write.call_args_list]
        self.assertIn("[速度受信] 窓1: OSCQuery で 0秒届かない → 固定ポート 9011 で受信", lines)
        self.assertFalse(any("mDNS の広告が無い" in l for l in lines))

    def test_the_advert_time_is_taken_off_the_wait(self):
        """広告を待った分は引いた残りだけ待つ（合計で OSCQUERY_CONNECT_WAIT_SEC）"""
        gate = threading.Event()
        self.addCleanup(gate.set)

        def slow(name, http_port, osc_port):
            gate.wait(5)
            return ("zc", name)
        with patch.object(OSCQuery, "_advertise", side_effect=slow), \
             patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", 0.6):
            r = self._receiver(11, 9011, "窓1")
            started = time.time()
            r.start()
            time.sleep(0.4)
            gate.set()                              # 0.4 秒で広告できた
            self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
            self.assertLess(time.time() - started, 0.6 + 0.3, "0.4 + 0.6 = 1.0 秒ではなく、合計 0.6 秒の近く")

    def test_stopping_while_waiting_for_the_advert_does_not_open_the_port(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def slow(name, http_port, osc_port):
            gate.wait(5)
            raise RuntimeError("mDNS")              # 止めたあとに広告が失敗しても
        with patch.object(OSCQuery, "_advertise", side_effect=slow), \
             patch.object(DebugLog, "exception"), \
             patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", self.LONG):
            r = self._receiver(11, 9011, "窓1")
            r.start()
            time.sleep(0.05)
            r.stop()
            gate.set()
            r._wait_thread.join(2.0)
            self.assertFalse(r._wait_thread.is_alive())
            self.assertEqual(self.ports.binds, [], "止めたら固定ポートを開かない")
            self.assertEqual(r.mode, OSCReceiver.MODE_WAITING)

    def test_no_service_is_still_an_immediate_fixed_port(self):
        with patch.object(OSCQuery, "acquire", return_value=False):
            r = self._receiver(11, 9011, "窓1")
            r.start()
        self.assertEqual(r.mode, OSCReceiver.MODE_PORT)
        self.assertEqual(self.ports.binds, [("127.0.0.1", 9011)])

    def test_the_wait_length_is_unchanged(self):
        src = Path(config.__file__).read_text(encoding="utf-8")
        self.assertIn("OSCQUERY_CONNECT_WAIT_SEC = 15.0", src)


if __name__ == "__main__":
    unittest.main()
