"""「1 回だけ」の送信を鍵で守ること・解放ループを二重に立てないこと・止めた後に何もしないこと。

- 統計の送信とスマホ通知は、確認と印を鍵の中で行う（ログのスレッドと待ってから送るスレッドが重なっても 1 回）
- 3 クラ解放ラウンドの AFK 解除ループは、同じラウンドで判定が 2 回通っても 1 本だけ
- 監視を止めた後は、フリーズ解除のログも窓の前面化もしない

本物の DB・ネットワーク・窓・入力には触らない（register_round・PhoneNotify.send・borrow_front は偽物）"""
from tests.support import *  # noqa: F401,F403

import PhoneNotify
from State import WindowState

PREFIX = "2026.11.14 10:00:00 Debug      -  "
START = PREFIX + "This round is taking place at Sewers (12) and the round type is Classic"


class _RacyState(WindowState):
    """印を読む側（統計の印・通知の印）が、読んだ直後に少し待つ WindowState。

    鍵が無いと、2 本のスレッドが「まだ立っていない」を両方読んでから印を立てる（二重送信）。
    鍵があれば、読むところから立てるところまでが 1 本ずつになるので、待っても 1 回で済む"""
    _sent = False
    _seq = -1

    @property
    def statistics_sent(self):
        value = self._sent
        time.sleep(0.05)
        return value

    @statistics_sent.setter
    def statistics_sent(self, value):
        self._sent = value

    @property
    def phone_notified_seq(self):
        value = self._seq
        time.sleep(0.05)
        return value

    @phone_notified_seq.setter
    def phone_notified_seq(self, value):
        self._seq = value


def _monitor(racy=False, running=True):
    monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True), {}, lambda _m: None, window_idx=2)
    monitor.logs = []
    monitor.logger = monitor.logs.append
    monitor.st.instance_type = config.INSTANCE_PRIVATE
    monitor.st.players_known = True
    if racy:
        monitor.st.__class__ = _RacyState
    monitor._running = running
    monitor._start_daemon = MagicMock()
    return monitor


def _in_two_threads(function):
    """同じ関数を 2 本のスレッドから同時に呼ぶ（Barrier で揃える）"""
    gate = threading.Barrier(2)
    errors = []

    def run():
        try:
            gate.wait(5)
            function()
        except Exception as exc:        # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not errors, errors


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        self.send = patch.object(ConnectDB, "register_round").start()
        self.phone = patch.object(PhoneNotify, "send").start()
        patch.object(PlaySound, "play_sound").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_phone_notify, False)
        self.addCleanup(SharedState.set_phone_topic, "")


class TestStatisticsSentOnce(_Case):
    def test_two_threads_send_once(self):
        monitor = _monitor(racy=True)
        monitor.st.terror_ids = [99]
        _in_two_threads(monitor._send_round_statistics_once)
        self.assertEqual(self.send.call_count, 1)

    def test_two_threads_send_the_null_terror_round_once(self):
        monitor = _monitor(racy=True)
        monitor.st.round_type = "Run"
        _in_two_threads(monitor._send_null_terror_round)
        self.assertEqual(self.send.call_count, 1)

    def test_two_threads_send_the_early_statistics_once(self):
        monitor = _monitor(racy=True)
        _in_two_threads(lambda: monitor._send_early_statistics(99))
        self.assertEqual(self.send.call_count, 1)

    def test_the_three_sites_share_one_claim(self):
        monitor = _monitor()
        monitor.st.terror_ids = [99]
        monitor._send_early_statistics(99)
        monitor._send_round_statistics_once()
        monitor._send_null_terror_round()
        self.assertEqual(self.send.call_count, 1)

    def test_one_thread_sends_the_first_and_not_the_second(self):
        monitor = _monitor()
        monitor.st.terror_ids = [99]
        monitor._send_round_statistics_once()
        self.assertEqual(self.send.call_count, 1)
        self.assertTrue(monitor.st.statistics_sent)
        monitor._send_round_statistics_once()
        self.assertEqual(self.send.call_count, 1)

    def test_no_terrors_do_not_take_the_claim(self):
        monitor = _monitor()
        monitor._send_round_statistics_once()
        self.send.assert_not_called()
        self.assertFalse(monitor.st.statistics_sent)
        monitor.st.terror_ids = [99]
        monitor._send_round_statistics_once()
        self.assertEqual(self.send.call_count, 1)

    def test_the_send_itself_runs_outside_the_lock(self):
        """通信で待つ間、鍵を持ったままにしない"""
        monitor = _monitor()
        monitor.st.terror_ids = [99]
        held = []
        self.send.side_effect = lambda *a, **k: held.append(monitor._once_lock.locked())
        monitor._send_round_statistics_once()
        self.assertEqual(held, [False])


class TestPhoneNoticeOnce(_Case):
    def setUp(self):
        super().setUp()
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic("tonab-unit_test-topic-0123456789abcdef")

    def test_two_threads_notify_once(self):
        monitor = _monitor(racy=True)
        _in_two_threads(lambda: monitor._notify_phone(PhoneNotify.KIND_CONTINUE, [99]))
        self.assertEqual(self.phone.call_count, 1)

    def test_one_thread_notifies_the_first_and_not_the_second(self):
        monitor = _monitor()
        monitor._notify_phone(PhoneNotify.KIND_CONTINUE, [99])
        monitor._notify_phone(PhoneNotify.KIND_CONTINUE, [99])
        self.assertEqual(self.phone.call_count, 1)

    def test_the_next_round_notifies_again(self):
        monitor = _monitor()
        monitor._notify_phone(PhoneNotify.KIND_CONTINUE, [99])
        monitor.st.round_seq += 1
        monitor._notify_phone(PhoneNotify.KIND_CONTINUE, [99])
        self.assertEqual(self.phone.call_count, 2)


class TestOpenSpecialLoopOnce(_Case):
    DECIDED = ("list", True, True)      # 続行・DTM/Waldo の判定

    def _loops(self, monitor):
        loop = monitor._action.do_open_special_round_loop
        return [c for c in monitor._start_daemon.call_args_list if c.args and c.args[0] == loop]

    def _decide(self, monitor):
        monitor.st.terror_ids = [config.DTM_ID]
        monitor.st.round_type = "Classic"
        monitor._decide_with_keep_on_set("Classic", self.DECIDED)

    def _monitor(self):
        monitor = _monitor()
        monitor.st.open_special_round_wins = 0
        monitor._focus_for_freeze = MagicMock()
        return monitor

    def _lines(self, monitor):
        return [m for m in monitor.logs if "クラ解放ラウンド開始" in m]

    def test_two_decisions_in_one_round_start_one_loop(self):
        monitor = self._monitor()
        self._decide(monitor)
        self.assertTrue(monitor.st.is_open_special_round_round)
        self._decide(monitor)
        self.assertEqual(len(self._loops(monitor)), 1)
        self.assertEqual(len(self._lines(monitor)), 1, monitor.logs)

    def test_the_next_round_starts_one_again(self):
        monitor = self._monitor()
        self._decide(monitor)
        monitor._process(START)
        self.assertFalse(monitor.st.is_open_special_round_round, "ラウンド開始で戻る")
        self._decide(monitor)
        self.assertEqual(len(self._loops(monitor)), 2)
        self.assertEqual(len(self._lines(monitor)), 2, monitor.logs)


class TestAfterStop(_Case):
    def test_the_equip_wait_release_does_nothing_after_a_stop(self):
        monitor = _monitor(running=False)
        with patch.object(LogMonitor.time, "sleep"), \
                patch.object(SharedState, "equip_freeze_end") as end:
            monitor._release_equip_wait_after_delay()
            monitor._release_equip_wait_after_delay(monitor.st.round_seq)
        end.assert_not_called()
        self.assertEqual(monitor.logs, [])

    def test_the_equip_wait_release_still_works_while_running(self):
        monitor = _monitor()
        with patch.object(LogMonitor.time, "sleep"), \
                patch.object(SharedState, "equip_freeze_end") as end, \
                patch.object(SharedState, "get_equip_freeze_count", return_value=0):
            monitor._release_equip_wait_after_delay(monitor.st.round_seq)
        end.assert_called_once_with(monitor.st)
        self.assertTrue(any("全窓フリーズ解除" in m for m in monitor.logs), monitor.logs)

    def test_the_focus_does_nothing_after_a_stop(self):
        monitor = _monitor(running=False)
        with patch.object(WindowOperator, "borrow_front", return_value=(True, None)) as borrow:
            monitor._focus_this_window_for("続行ラウンド")
        borrow.assert_not_called()
        self.assertEqual(monitor.logs, [])

    def test_a_stop_while_waiting_for_the_global_lock(self):
        """鍵を待っている間に止まった（鍵の前ではなく、取った後に見る）"""
        monitor = _monitor()
        with patch.object(WindowOperator, "borrow_front", return_value=(True, None)) as borrow, \
                patch.object(SharedState, "keep_front_loan"):
            SharedState._GLOBAL_ACTION_LOCK.acquire()
            try:
                thread = threading.Thread(target=monitor._focus_this_window_for, args=("続行ラウンド",))
                thread.start()
                thread.join(0.2)
                self.assertTrue(thread.is_alive(), "鍵を待っている")
                monitor._running = False
            finally:
                SharedState._GLOBAL_ACTION_LOCK.release()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        borrow.assert_not_called()
        self.assertEqual(monitor.logs, [])

    def test_the_focus_still_works_while_running(self):
        monitor = _monitor()
        with patch.object(WindowOperator, "borrow_front", return_value=(True, "loan")) as borrow, \
                patch.object(SharedState, "keep_front_loan") as keep:
            monitor._focus_this_window_for("続行ラウンド")
        borrow.assert_called_once()
        keep.assert_called_once_with(monitor.st, "loan")
        self.assertTrue(any("この窓を前面に出しました" in m for m in monitor.logs), monitor.logs)


if __name__ == "__main__":
    unittest.main()
