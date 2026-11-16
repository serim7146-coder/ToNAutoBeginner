"""設定「Beginは連打だけ」（依頼者 2026-11-16）: ON のとき Begin は /input/UseRight の連打だけ。
差し込み（カーソル）・前面化・クリックは 1 回もしない。本物の OSC・入力・窓・カーソルは使わない"""
from tests.support import *  # noqa: F401,F403


class _Case(unittest.TestCase):
    WAITING = "Begin: 連打だけで押します（前面かカーソルがこの窓の上のときに押せます）"

    def setUp(self):
        SharedState.set_begin_use_spam_only(False)
        self.addCleanup(SharedState.set_begin_use_spam_only, False)
        self.logs = []
        self.presses = []

    def _executor(self, osc_port=9000):
        cfg = WindowConfig(hwnd=0x10, osc_port=osc_port, auto_begin=True)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=1, round_seq=7,
                         round_end_seen=True, round_over_time=time.time() - 1000.0)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, self.logs.append,
                                           auto_begin_active=lambda: True)
        if ex._osc is not None:
            ex._osc = MagicMock()                      # 本物の OSC は送らない
            ex._osc.press.side_effect = lambda address, *_a, **_k: self.presses.append(address) or True
        return ex, st

    def _guard(self, ex):
        """Begin の周りの重い所（移動・待ち）を無効にする。カーソル・前面・クリックは偽物にして数える"""
        self.mocks = {}
        for name in ("set_cursor_position", "focus_window", "focus_vrchat", "click", "mouse_click"):
            p = patch.object(WindowOperator, name)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)
        for target, name, value in (
                (ex, "_begin_move", None), (ex, "_wait_other_windows", True),
                (ex, "_begin_precheck", True), (ex, "_wait_round_end", True),
                (ex, "_handle_item_lost", True), (ex, "_wait_run_respawn", True),
                (ex, "_pos_ref_plan", False), (ex, "_freezes_ok", (True, True, True, True)),
                (ex, "_use_right_reaches_begin", False), (ex, "item_fetch_target", None),
                (ex, "_hands_free", False), (WindowOperator, "foreground_hwnd", 0)):
            p = patch.object(target, name, new=lambda *_a, _v=value, **_k: _v)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (("BEGIN_WAIT_SEC", 0), ("BEGIN_USE_SPAM_START_SEC", 0.0)):
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _uses_right(self):
        return [a for a in self.presses if a == "/input/UseRight"]

    def _run_until(self, ex, condition, effect, timeout=10.0):
        """do_after_round を回す。UseRight が 2 通以上送られて condition を満たしたら effect を起こす。
        本物の連打スレッドを使う（_start_use_spam はそのまま）。効かなければ保険で止める"""
        def helper():
            deadline = time.time() + timeout
            while time.time() < deadline:
                # 連打だけの待ちに入った（ログが出た）あとで起こす。入る前に起こすと、その待ちを通らない
                if (self.WAITING in self.logs and len(self._uses_right()) >= 2 and condition()):
                    break
                time.sleep(0.005)
            effect()
        thread = threading.Thread(target=helper, daemon=True)
        thread.start()
        ex.do_after_round()
        thread.join(timeout)


class TestSetting(unittest.TestCase):
    def test_it_is_off_by_default(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_BEGIN_USE_SPAM_ONLY = _Setting(False, bool)", src)
        self.assertIs(SharedState.get_begin_use_spam_only(), False)

    def test_it_takes_only_booleans(self):
        self.addCleanup(SharedState.set_begin_use_spam_only, False)
        SharedState.set_begin_use_spam_only(True)
        self.assertIs(SharedState.get_begin_use_spam_only(), True)


class TestDoAfterRoundWithTheSettingOn(_Case):
    def setUp(self):
        super().setUp()
        self.ex, self.st = self._executor()
        self._guard(self.ex)
        SharedState.set_begin_use_spam_only(True)

    def _round_starts(self):
        self.st.in_round = True

    def test_it_only_spams_use_right_and_never_touches_the_cursor_the_front_or_the_mouse(self):
        ex, st = self.ex, self.st
        press_begin = MagicMock(return_value=True)
        dip = MagicMock(return_value=True)
        with patch.object(ex, "_press_begin", press_begin), \
                patch.object(ex, "_dip_cursor_for_begin", dip):
            self._run_until(ex, lambda: len(self._uses_right()) >= 3, self._round_starts)
        self.assertGreaterEqual(len(self._uses_right()), 3, "/input/UseRight は送られる")
        press_begin.assert_not_called()
        dip.assert_not_called()
        for name, mock in self.mocks.items():
            mock.assert_not_called()
        self.assertIn("Begin: 連打だけで押します（前面かカーソルがこの窓の上のときに押せます）", self.logs)
        self.assertEqual([m for m in self.logs if m.startswith("Begin:")].count(
            "Begin: 連打だけで押します（前面かカーソルがこの窓の上のときに押せます）"), 1, "その回に 1 回だけ")

    def test_the_real_press_begin_is_never_reached(self):
        """本物の _press_begin のまま通しても、カーソル・前面・クリックは呼ばれない"""
        self._run_until(self.ex, lambda: True, self._round_starts)
        for name, mock in self.mocks.items():
            mock.assert_not_called()
        self.assertTrue(self._uses_right())
        self.assertIn(self.WAITING, self.logs, "連打だけの待ちを通った（空振りのテストにしない）")

    def test_it_returns_when_the_round_starts_and_stops_the_spam(self):
        self._run_until(self.ex, lambda: True, self._round_starts)
        self.assertTrue(self.st.in_round)
        time.sleep(0.3)             # 連打スレッドが自分から終わるのを待つ
        sent = len(self._uses_right())
        time.sleep(0.3)
        self.assertEqual(len(self._uses_right()), sent, "戻ったあとは UseRight を送り続けない")

    def test_it_returns_when_begin_is_accepted(self):
        def accepted():
            self.st.begin_done = True
        self._run_until(self.ex, lambda: True, accepted)
        self.assertTrue(self.st.begin_done)
        self.assertFalse(self.st.in_round, "受理で戻る（ラウンド開始を待たない）")

    def test_it_returns_when_the_macro_is_stopped(self):
        running = [True]
        self.ex._is_running = lambda: running[0]

        def stop():
            running[0] = False
        self._run_until(self.ex, lambda: True, stop)
        self.assertFalse(running[0])
        self.assertFalse(self.st.in_round)

    def test_it_returns_when_the_round_number_changes(self):
        def leave():
            self.st.round_seq = 8
        self._run_until(self.ex, lambda: True, leave)
        self.assertEqual(self.st.round_seq, 8)
        self.assertFalse(self.st.in_round)

    def test_the_confirm_and_retry_paths_are_not_taken(self):
        ex = self.ex
        confirm, giveup = MagicMock(), MagicMock(return_value=False)
        with patch.object(ex, "_confirm_begin", confirm), patch.object(ex, "_gave_up_begin", giveup), \
                patch.object(ex, "_retry_after_giving_up") as retry:
            self._run_until(ex, lambda: True, self._round_starts)
        confirm.assert_not_called()
        giveup.assert_not_called()
        retry.assert_not_called()


class TestWithoutOsc(_Case):
    def test_a_window_without_osc_does_nothing_but_log_one_line(self):
        ex, st = self._executor(osc_port=0)
        self.assertFalse(ex.uses_osc)
        self._guard(ex)
        SharedState.set_begin_use_spam_only(True)
        press_begin = MagicMock(return_value=True)
        with patch.object(ex, "_press_begin", press_begin), patch.object(ActionExecutor.time, "sleep") as sleep:
            ex.do_after_round()
        press_begin.assert_not_called()
        for name, mock in self.mocks.items():
            mock.assert_not_called()
        self.assertNotIn(0.025, [c.args[0] for c in sleep.call_args_list], "連打の待ちには入らない")
        self.assertEqual([m for m in self.logs if m.startswith("Begin:")],
                         ["Begin: この窓は OSC が使えないので、連打だけの設定では押せません"])

    def test_wait_use_spam_only_directly(self):
        ex, st = self._executor(osc_port=0)
        ex._wait_use_spam_only(7, None)
        self.assertEqual(self.logs, ["Begin: この窓は OSC が使えないので、連打だけの設定では押せません"])


class TestWithTheSettingOff(_Case):
    def test_off_goes_the_usual_way_through_press_begin(self):
        ex, st = self._executor()
        self._guard(ex)
        press_begin = MagicMock(return_value=False)
        wait = MagicMock()
        with patch.object(ex, "_press_begin", press_begin), patch.object(ex, "_wait_use_spam_only", wait), \
                patch.object(ex, "_start_use_spam", return_value=None), \
                patch.object(ex, "_gave_up_begin", return_value=False):
            ex.do_after_round()
        press_begin.assert_called_once_with()
        wait.assert_not_called()

    def test_begin_by_cursor_is_not_changed_by_the_setting(self):
        """_start_use_spam の入口（_begin_by_cursor）は ON でも True のまま（False にすると連打が始まらない）"""
        ex, st = self._executor()
        self.addCleanup(SharedState.set_begin_use_spam_only, False)
        for on in (False, True):
            SharedState.set_begin_use_spam_only(on)
            self.assertTrue(ex._begin_by_cursor(), on)
            with patch.object(ActionExecutor.threading, "Thread") as thread:
                self.assertIsNotNone(ex._start_use_spam(7), on)
            thread.assert_called_once()
