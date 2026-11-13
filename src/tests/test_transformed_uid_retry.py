"""統計用の番号（transformed_uid）の取得のやり直し（LogMonitor._fetch_transformed_uid）

通信に失敗（例外）したら、config.TRANSFORMED_UID_RETRY_SEC の間隔で（30・60・120 秒）3 回までやり直す。
待ちは self._stop_event.wait（止めたらすぐ終わる）。待ちは差し替えて実時間を待たない。
UID・表示名は作り物。DB（ConnectDB.send_Users）は本当につながない
"""
from tests.support import *  # noqa: F401,F403


ME = "usr_00000000-0000-0000-0000-0000000000a1"
OTHER = "usr_00000000-0000-0000-0000-0000000000b2"


class TestTransformedUidRetry(unittest.TestCase):
    def _monitor(self, running=True):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.local_user_id = ME
        monitor.st.local_player_name = "tester"
        monitor._running = running
        return monitor

    def _run(self, monitor, results, on_wait=None, stopped_after=None):
        """_fetch_transformed_uid を流す。results は send_Users が順に返す値（例外ならそれを投げる）。
        on_wait(秒) は待ちのたびに呼ぶ（その返り値が wait の返り値＝止められたか。None なら False）。
        戻り値は (呼んだ回数, 待った秒の一覧, debug.log に書いた行)"""
        waits = []
        written = []
        queue = list(results)

        def send(_uid, _name=None):
            value = queue.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        def wait(seconds=None):
            waits.append(seconds)
            return bool(on_wait(seconds)) if on_wait else False

        fake_send = MagicMock(side_effect=send)
        with patch.object(ConnectDB, "send_Users", fake_send), \
             patch.object(monitor._stop_event, "wait", side_effect=wait), \
             patch.object(LogMonitor.time, "sleep", side_effect=AssertionError("time.sleep は使わない")), \
             patch.object(DebugLog, "write", side_effect=written.append), \
             patch.object(DebugLog, "exception"):
            monitor._fetch_transformed_uid(ME, "tester")
        return fake_send.call_count, waits, written

    def test_the_intervals_are_a_tuple_in_config(self):
        self.assertEqual(config.TRANSFORMED_UID_RETRY_SEC, (30.0, 60.0, 120.0))

    def test_success_on_the_first_try_waits_for_nothing(self):
        monitor = self._monitor()
        calls, waits, _ = self._run(monitor, [7])
        self.assertEqual((calls, waits), (1, []))
        self.assertEqual(monitor.st.transformed_uid, 7)
        self.assertEqual(monitor.logs, ["[窓1] transformed_uid: 7"])

    def test_the_second_try_gets_it(self):
        monitor = self._monitor()
        calls, waits, written = self._run(monitor, [OSError("offline"), 9])
        self.assertEqual(calls, 2)
        self.assertEqual(waits, [30.0])
        self.assertEqual(monitor.st.transformed_uid, 9)
        failures = [m for m in monitor.logs if "取得に失敗" in m]
        self.assertEqual(len(failures), 1, "最初の失敗で 1 行")
        self.assertIn("やり直", failures[0])
        self.assertEqual(monitor.logs[-1], "[窓1] transformed_uid: 9")
        self.assertEqual(len(monitor.logs), 2)
        self.assertFalse(any("あきらめ" in m for m in monitor.logs))

    def test_it_gives_up_after_three_retries_with_one_line(self):
        monitor = self._monitor()
        calls, waits, written = self._run(monitor, [OSError("a"), OSError("b"), OSError("c"), OSError("d")])
        self.assertEqual(calls, 4, "最初の 1 回＋やり直し 3 回")
        self.assertEqual(waits, [30.0, 60.0, 120.0])
        self.assertIsNone(monitor.st.transformed_uid)
        self.assertEqual(sum("取得に失敗" in m for m in monitor.logs), 1)
        self.assertEqual(sum("あきらめ" in m for m in monitor.logs), 1)
        self.assertEqual(len(monitor.logs), 2, "画面は最初の失敗と最後の 1 行だけ")
        self.assertIn("あきらめ", monitor.logs[-1])
        retries = [w for w in written if "やり直し" in str(w)]
        self.assertEqual(len(retries), 3, "途中のやり直しは debug.log だけ")
        for text in monitor.logs:
            self.assertNotIn("[窓1] [窓1]", text)

    def test_the_third_retry_can_still_succeed(self):
        monitor = self._monitor()
        calls, waits, _ = self._run(monitor, [OSError("a"), OSError("b"), OSError("c"), 12])
        self.assertEqual((calls, waits), (4, [30.0, 60.0, 120.0]))
        self.assertEqual(monitor.st.transformed_uid, 12)
        self.assertFalse(any("あきらめ" in m for m in monitor.logs))

    def test_the_intervals_come_from_config(self):
        monitor = self._monitor()
        with patch.object(config, "TRANSFORMED_UID_RETRY_SEC", (1.0, 2.0)):
            calls, waits, _ = self._run(monitor, [OSError("a")] * 3)
        self.assertEqual((calls, waits), (3, [1.0, 2.0]))
        self.assertEqual(sum("あきらめ" in m for m in monitor.logs), 1)

    def test_a_none_answer_is_not_retried(self):
        """send_Users が例外なしで None を返す（設定なし・DB に関数がない・割り当てられない）今までの扱い"""
        monitor = self._monitor()
        monitor.st.transformed_uid = 5
        calls, waits, _ = self._run(monitor, [None])
        self.assertEqual((calls, waits), (1, []))
        self.assertIsNone(monitor.st.transformed_uid)
        self.assertEqual(monitor.logs, ["[窓1] transformed_uid: None"])

    def test_the_uid_changing_while_waiting_stops_the_retries(self):
        monitor = self._monitor()

        def switch(_seconds):
            monitor.st.local_user_id = OTHER        # 待つ間にアカウントが替わった
            return False

        calls, waits, _ = self._run(monitor, [OSError("a"), 9], on_wait=switch)
        self.assertEqual((calls, waits), (1, [30.0]), "やり直さない")
        self.assertIsNone(monitor.st.transformed_uid, "古い結果を入れない")
        self.assertEqual(sum("transformed_uid:" in m for m in monitor.logs), 0)
        self.assertFalse(any("あきらめ" in m for m in monitor.logs))

    def test_an_old_answer_that_arrives_after_the_uid_changed_is_dropped(self):
        """取りに行っている間にアカウントが替わり、古い方が後から返っても、新しい番号を上書きしない"""
        monitor = self._monitor()
        monitor.st.transformed_uid = None

        def send(_uid, _name=None):
            monitor.st.local_user_id = OTHER
            monitor.st.transformed_uid = 33         # 新しい方が先に取れた
            return 11                                # 古い方が後から返る

        with patch.object(ConnectDB, "send_Users", side_effect=send), \
             patch.object(DebugLog, "write"):
            monitor._fetch_transformed_uid(ME, "tester")
        self.assertEqual(monitor.st.transformed_uid, 33)
        self.assertFalse(any("transformed_uid:" in m for m in monitor.logs))

    def test_a_failure_after_the_uid_changed_logs_nothing_and_stops(self):
        monitor = self._monitor()

        def send(_uid, _name=None):
            monitor.st.local_user_id = OTHER
            raise OSError("offline")

        with patch.object(ConnectDB, "send_Users", side_effect=send) as fake, \
             patch.object(monitor._stop_event, "wait", return_value=False) as wait, \
             patch.object(DebugLog, "write"), patch.object(DebugLog, "exception"):
            monitor._fetch_transformed_uid(ME, "tester")
        self.assertEqual(fake.call_count, 1)
        wait.assert_not_called()
        self.assertEqual(monitor.logs, [])

    def test_stopping_the_monitor_ends_the_wait_at_once(self):
        """止めたら wait が True を返す。待ちを打ち切って、やり直さず・あきらめの行も出さない"""
        monitor = self._monitor()
        calls, waits, _ = self._run(monitor, [OSError("a"), 9], on_wait=lambda _s: True)
        self.assertEqual((calls, waits), (1, [30.0]))
        self.assertIsNone(monitor.st.transformed_uid)
        self.assertFalse(any("あきらめ" in m for m in monitor.logs))

    def test_a_stopped_monitor_does_not_retry_or_store(self):
        """待ちが終わった時点で _running が偽（止めた）なら、やり直さない"""
        monitor = self._monitor()

        def stop(_seconds):
            monitor._running = False
            return False

        calls, waits, _ = self._run(monitor, [OSError("a"), 9], on_wait=stop)
        self.assertEqual(calls, 1)
        self.assertIsNone(monitor.st.transformed_uid)

    def test_a_stopped_monitor_does_not_store_a_late_answer(self):
        monitor = self._monitor()

        def send(_uid, _name=None):
            monitor._running = False
            return 21

        with patch.object(ConnectDB, "send_Users", side_effect=send), patch.object(DebugLog, "write"):
            monitor._fetch_transformed_uid(ME, "tester")
        self.assertIsNone(monitor.st.transformed_uid)
        self.assertEqual(monitor.logs, [])

    def test_the_real_event_wait_is_used_so_a_stop_is_immediate(self):
        """本物の Event.wait で、止めると待ちがすぐ終わる（実時間は 1 秒もかけない）"""
        monitor = self._monitor()
        with patch.object(ConnectDB, "send_Users", side_effect=OSError("offline")) as fake, \
             patch.object(DebugLog, "write"), patch.object(DebugLog, "exception"):
            monitor._stop_event.set()
            started = time.monotonic()
            monitor._fetch_transformed_uid(ME, "tester")
            took = time.monotonic() - started
        self.assertLess(took, 1.0)
        self.assertEqual(fake.call_count, 1)


if __name__ == "__main__":
    unittest.main()
