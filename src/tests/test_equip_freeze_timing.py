"""装備待ちフリーズを張るタイミング（EV。依頼者 2026-10-13「謎のフリーズ」）。23:12 窓1・09:47 窓2 は Begin を押す前
（Verified Round End）に張り、Begin が通らずに取りに行けず、全窓が止まり続けた。
本物の OSC・入力・撮影は使わない"""
from tests.support import *  # noqa: F401,F403
import inspect


def _module_clock():
    now = [1000.0]
    fake = type(sys)("time_for_test")
    fake.time = fake.monotonic = lambda: now[0]
    fake.sleep = lambda sec: now.__setitem__(0, now[0] + sec)
    return fake, now


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_item_begin_mode(False)
        for reset in (SharedState.equip_freeze_reset, SharedState.continue_round_reset):
            self.addCleanup(reset)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        self.logs = []
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def _executor(self, fetch=True, accept_on=None):
        """自動 Begin の窓。fetch: 取りに行く窓か（item_fetch_target）。accept_on 回目の押しで受理"""
        cfg = WindowConfig(hwnd=0x10, osc_port=9000, auto_begin=True)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=1, round_seq=7,
                         round_end_seen=True, waiting_for_equip=True, item_id=0)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, self.logs.append,
                                           auto_begin_active=lambda: True)
        ex._osc = MagicMock()                      # 本物の OSC は送らない
        presses = []

        def press(again=False, click_only=False):
            presses.append(SharedState.EQUIP_WAIT_EVENT.is_set())
            if accept_on is not None and len(presses) >= accept_on:
                st.begin_done = True
            return True
        self.presses = presses
        ex._press_begin = press
        ex._adjust_to_begin = lambda seq: "aimed"
        ex._begin_move = lambda: None
        ex._start_use_spam = lambda seq: None
        ex._wait_other_windows = lambda: True
        ex._begin_precheck = lambda check_freeze=True: True
        ex._show_item_loss = lambda: self.logs.append("前面化と音声")
        ex.item_fetch_target = lambda: ("Survival", 29) if fetch else None
        ex._retry_after_giving_up = lambda seq: self.logs.append(f"30秒後に押し直す {seq}")
        clock, self.now = _module_clock()
        p = patch.object(ActionExecutor, "time", clock)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(config, "BEGIN_WAIT_SEC", 0)
        p.start()
        self.addCleanup(p.stop)
        return ex, st


class TestWindow1At2312(_Case):
    def test_begin_never_accepted_sets_no_freeze(self):
        """取りに行く窓で Begin が 3 回とも通らない → 装備待ちフリーズを張らない・ほかの窓は止まらない"""
        ex, st = self._executor(fetch=True)
        fetched = []
        ex._fetch_item = lambda *a, **k: fetched.append(a) or "ok"
        st.waiting_for_equip = True

        def stop_waiting():
            st.waiting_for_equip = False            # 装備待ちの待ちを抜ける（ラウンド開始の代わり）
            st.in_round = True
        ex._handle_item_lost()
        self.assertFalse(st.equip_freeze_held, "Verified Round End の後・Begin の前に張らない")
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set(), "ほかの窓は止まらない")
        with patch.object(ex, "_is_running", side_effect=[True] * 200 + [False] * 10):
            ex.do_after_round()
        self.assertEqual(fetched, [], "Begin が通らなければ取りに行かない")
        self.assertFalse(st.equip_freeze_held)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertTrue(all(self.presses), "押すときも、ほかの窓は止まっていない")
        self.assertIn("30秒後に押し直す 7", self.logs, "あきらめたら 30 秒後に押し直す")


class TestTheFetchingWindow(_Case):
    """取りに行く窓: 柵越え・移動の間は張らず、店で前面を借りる直前に張り、Equip の後に解く"""

    class _Fetcher:
        def __init__(self, test, buy_result=True, buy_raises=None):
            self.test, self.buy_result, self.buy_raises = test, buy_result, buy_raises
            self.mouse = type("M", (), {"inner": type("I", (), {"restoring": False})(), "total": [0, 0]})()
            self.measured_gain = None
            self.events = test.events

        def move_to_shop(self):
            self.events.append(("move", SharedState.EQUIP_WAIT_EVENT.is_set()))
            return True

        def _check(self):
            pass

        def sleep(self, _sec):
            pass

        def buy(self, shop, item_id):
            self.events.append(("buy", SharedState.EQUIP_WAIT_EVENT.is_set()))
            if self.buy_raises:
                raise self.buy_raises
            return self.buy_result

        def restore_view(self):
            pass

    def _fetch(self, **kw):
        ex, st = self._executor(fetch=True, accept_on=1)
        st.begin_done = True
        self.events = []
        fetcher = self._Fetcher(self, **kw)
        with patch.object(ItemFetch, "Fetcher", return_value=fetcher), \
             patch.object(WindowOperator, "cursor_position", return_value=(1, 2)), \
             patch.object(WindowOperator, "borrow_front",
                          side_effect=lambda h: self.events.append(("borrow", SharedState.EQUIP_WAIT_EVENT.is_set()))
                          or (True, "loan")), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(WindowOperator, "foreground_hwnd", return_value=0x99), \
             patch.object(WindowOperator, "set_cursor_position", return_value=True), \
             patch.object(ItemFetch, "available", return_value=True):
            result = ex._fetch_item(st.round_seq, "Survival", 29)
        return result, st

    def test_frozen_only_while_pressing_in_the_shop(self):
        result, st = self._fetch()
        self.assertEqual(result, "ok")
        self.assertEqual(self.events, [("move", True), ("borrow", False), ("buy", False)],
                         "移動の間は張らない（ほかの窓は止まらない）。前面を借りる時にはもう張っている")
        self.assertFalse(st.equip_freeze_held, "Equip の後に解く")
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())

    def test_released_when_it_could_not_buy(self):
        result, st = self._fetch(buy_result=False)
        self.assertEqual(result, "failed")
        self.assertFalse(st.equip_freeze_held)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())

    def test_released_when_it_ran_out_of_time(self):
        result, st = self._fetch(buy_raises=ItemFetch.Stopped("timeout"))
        self.assertEqual(result, "timeout")
        self.assertFalse(st.equip_freeze_held)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())

    def test_the_entry_plush_does_not_freeze(self):
        """装備待ちでない取得（入室時の Guidance Plush など）は張らない"""
        ex, st = self._executor(fetch=True)
        st.waiting_for_equip = False
        self.events = []
        fetcher = self._Fetcher(self)
        with patch.object(WindowOperator, "cursor_position", return_value=None), \
             patch.object(WindowOperator, "borrow_front",
                          side_effect=lambda h: self.events.append(("borrow", SharedState.EQUIP_WAIT_EVENT.is_set()))
                          or (True, "loan")), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(WindowOperator, "foreground_hwnd", return_value=0x99):
            ex._fetch_in_front(fetcher, "Survival", 29)
        self.assertEqual(self.events[0], ("borrow", True))


class TestTheManualWindow(_Case):
    """取りに行かない窓（人が手で装備する）: Verified Round End では張らず、Begin が通ったときに張る"""

    def _round(self, accept_on):
        ex, st = self._executor(fetch=False, accept_on=accept_on)
        released = []

        def wait_loop_exit(*_a):
            released.append(1)
        with patch.object(ex, "_is_running", side_effect=[True] * 60 + [False] * 20):
            ex.do_after_round()
        return ex, st

    def test_accepted_begin_freezes(self):
        ex, st = self._round(accept_on=1)
        self.assertEqual(self.presses, [True], "押すときはまだ張っていない")
        self.assertIn("前面化と音声", self.logs, "Begin が通ったら前面化・音声")

    def test_freeze_is_set_when_begin_is_accepted(self):
        ex, st = self._executor(fetch=False, accept_on=1)
        seen = []
        real = SharedState.equip_freeze_start
        with patch.object(ActionExecutor.SharedState, "equip_freeze_start",
                          side_effect=lambda s: seen.append(st.begin_done) or real(s)), \
             patch.object(ex, "_is_running", side_effect=[True] * 60 + [False] * 20):
            ex.do_after_round()
        self.assertEqual(seen, [True], "張るのは Begin が通った後の 1 回だけ")

    def test_not_accepted_does_not_freeze(self):
        ex, st = self._executor(fetch=False, accept_on=None)
        with patch.object(ActionExecutor.SharedState, "equip_freeze_start") as start, \
             patch.object(ex, "_is_running", side_effect=[True] * 60 + [False] * 20):
            ex.do_after_round()
        start.assert_not_called()
        self.assertNotIn("前面化と音声", self.logs)


class TestItemBeginModeIsAsBefore(_Case):
    def test_it_still_freezes_before_begin(self):
        ex, st = self._executor(fetch=False)
        SharedState.set_item_begin_mode(True)
        st.item_id = 0

        def equip(_sec):
            st.item_id = 29                       # 装備した
        with patch.object(ActionExecutor.time, "sleep", side_effect=equip):
            self.assertTrue(ex._handle_item_lost())
        self.assertTrue(st.equip_freeze_held, "アイテム取得→Begin モードは今どおり Begin の前に張る")


class TestGiveUpRetry(_Case):
    """「3回押しましたが受理されませんでした」の後、30 秒おいて押し直しの流れを 1 回"""

    def _ex(self):
        ex, st = self._executor(fetch=False)
        del ex._retry_after_giving_up             # 本物を使う
        again = []
        ex.do_begin_again = lambda seq: again.append((seq, self.now[0]))
        self.again = again
        return ex, st

    def test_after_30_seconds_once(self):
        ex, st = self._ex()
        start = self.now[0]
        ex._retry_after_giving_up(7)
        ex._retry_after_giving_up(7)
        self.assertEqual(len(self.again), 1, "1 ラウンドに 1 回")
        self.assertAlmostEqual(self.again[0][1] - start, config.BEGIN_GIVE_UP_RETRY_SEC, delta=0.6)
        self.assertEqual(config.BEGIN_GIVE_UP_RETRY_SEC, 30.0)
        self.assertIn("Begin をもう一度押します（あきらめてから 30 秒）", self.logs)

    def test_a_round_start_or_stop_or_acceptance_cancels(self):
        for spoil in (lambda st: setattr(st, "in_round", True), lambda st: setattr(st, "begin_done", True),
                      lambda st: setattr(st, "round_seq", 8)):
            ex, st = self._ex()
            real_sleep = ActionExecutor.time.sleep

            def sleep(sec, st=st, spoil=spoil):
                real_sleep(sec)
                if self.now[0] > 1010:
                    spoil(st)
            with patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
                ex._retry_after_giving_up(7)
            self.assertEqual(self.again, [], spoil)
        ex, st = self._ex()
        with patch.object(ex, "_is_running", side_effect=[True] * 5 + [False] * 200):
            ex._retry_after_giving_up(7)
        self.assertEqual(self.again, [], "止めた")

    def test_called_only_after_giving_up(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor.do_after_round)
        self.assertIn("if self._begin_gave_up_round == round_seq:\n                self._retry_after_giving_up(round_seq)", src)
        src = inspect.getsource(ActionExecutor.ActionExecutor._confirm_begin)
        self.assertIn("self._begin_gave_up_round = round_seq", src)


class TestTheWords(unittest.TestCase):
    def _monitor(self, fetch):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10, osc_port=9000, auto_begin=True), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.logs = []
        monitor._log = monitor.logs.append
        monitor._action.item_fetch_target = lambda: ("Survival", 29) if fetch else None
        monitor._round_lost_item = lambda: True
        monitor._round_item_warning = lambda: True
        monitor._start_daemon = lambda *a: None
        return monitor

    def test_the_round_end_line(self):
        for fetch, words in ((True, "ラウンド終了 【⚠ アイテムロスト → Begin が通ったら取りに行く】"),
                             (False, "ラウンド終了 【⚠ アイテムロスト → Begin が通ったらフリーズ】")):
            monitor = self._monitor(fetch)
            with patch.object(PlaySound, "play_sound"):
                monitor._process("2026.10.13 23:12:33 Debug      -  Verified Round End")
            self.assertIn(words, monitor.logs, fetch)
            self.assertFalse(any("Begin時にフリーズ開始" in m for m in monitor.logs))
