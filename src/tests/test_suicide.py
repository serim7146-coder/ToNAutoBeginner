"""自爆・放置モード・チェイス"""
from tests.support import *  # noqa: F401,F403




class TestSuicideKeyFixed(unittest.TestCase):
    """自爆キーは config 固定。GUIの入力欄は消えている"""

    def tearDown(self):
        SharedState.set_suicide_key(config.SELF_SUICIDE_KEY)

    def test_the_gui_has_no_input_for_it(self):
        self.assertNotIn("v_suicide_key",
                         Path("mainGUI.py").read_text(encoding="utf-8"),
            "入力欄と適用ボタンは削除されていること")

    def test_the_default_comes_from_config(self):
        self.assertEqual(SharedState.get_suicide_key(), config.SELF_SUICIDE_KEY)

    def test_the_setter_is_kept_for_tests(self):
        """GUIから呼ばれなくなるだけ。テストが4箇所で使っている"""
        SharedState.set_suicide_key("q")

        self.assertEqual(SharedState.get_suicide_key(), "q")




class TestSuicideRetry(unittest.TestCase):
    """死ななければ自爆をやり直す（背面送信のまま。最大 SUICIDE_RETRY_MAX 回）"""

    def setUp(self):
        SharedState.set_suicide_key("^")
        for name, value in (("SUICIDE_CONFIRM_SEC", 0.05), ("SUICIDE_RETRY_MAX", 3)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _executor(self, **state):
        cfg = WindowConfig(hwnd=123, do_skip=True)
        st = WindowState(in_round=True, round_seq=7, **state)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        return ex, st, logs

    def _skip(self, ex, st, die_on=None, before=None):
        """die_on 回目の長押しで死ぬ。before(n) は n 回目の長押しの直前に呼ぶ"""
        sent = []

        def hold(hwnd, key, sec, stop=None):
            sent.append(hwnd)
            if before:
                before(len(sent))
            if die_on is not None and len(sent) >= die_on:
                st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold), \
             patch.object(WindowOperator, "focus_window") as focus, \
             patch.object(WindowOperator, "hold_key") as foreground:
            ex.do_skip()
        focus.assert_not_called()
        foreground.assert_not_called()
        return len(sent)

    def test_it_tries_again_when_still_alive(self):
        ex, st, logs = self._executor()

        self.assertEqual(self._skip(ex, st, die_on=2), 2)
        self.assertTrue(any("やり直し（2/3回目）" in m for m in logs), logs)

    def test_a_death_stops_it(self):
        ex, st, _logs = self._executor()

        self.assertEqual(self._skip(ex, st, die_on=1), 1)

    def test_it_gives_up_after_three(self):
        ex, st, logs = self._executor()

        self.assertEqual(self._skip(ex, st), 3)
        self.assertTrue(any("⚠ 自爆を3回試しましたが死にませんでした" in m
                            for m in logs), logs)

    def test_a_continue_round_stops_the_retry(self):
        ex, st, logs = self._executor()

        def turn_into_continue(n):
            if n == 1:
                st.is_continue_round = True     # 1回目の最中に続行と分かった

        self.assertEqual(self._skip(ex, st, before=turn_into_continue), 1)
        self.assertFalse(any("試しましたが" in m for m in logs), "警告は出さない")

    def test_a_new_round_stops_the_old_retry(self):
        ex, st, logs = self._executor()

        def next_round(n):
            if n == 1:
                st.round_seq += 1

        self.assertEqual(self._skip(ex, st, before=next_round), 1)
        self.assertFalse(any("試しましたが" in m for m in logs))

    def test_the_round_ending_stops_it(self):
        ex, st, _logs = self._executor()

        def round_over(n):
            if n == 1:
                st.in_round = False

        self.assertEqual(self._skip(ex, st, before=round_over), 1)

    def test_its_own_item_wait_stops_it(self):
        ex, st, _logs = self._executor()

        def lost(n):
            if n == 1:
                st.waiting_for_equip = True

        self.assertEqual(self._skip(ex, st, before=lost), 1)

    def test_a_death_just_after_the_hold_counts(self):
        """長押しが終わった直後の死亡も成功として拾う（やり直さない）"""
        ex, st, _logs = self._executor()
        sent = []
        naps = []

        def nap(_sec):
            naps.append(_sec)
            st.died_this_round = True     # 確認の待ちの間に死亡が届いた

        with patch.object(config, "SUICIDE_CONFIRM_SEC", 5.0), \
             patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda *a, **_: sent.append(1) or True), \
             patch.object(ActionExecutor.time, "sleep", side_effect=nap):
            ex.do_skip()

        self.assertEqual(len(sent), 1)
        self.assertEqual(len(naps), 1, "死亡を見たらすぐ抜けること")

    def test_an_unsendable_key_is_not_retried(self):
        """最小化などで送れないものは、やり直しても送れない"""
        ex, st, logs = self._executor()

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=False) as bg:
            ex.do_skip()

        bg.assert_called_once()
        self.assertTrue(any("自爆できませんでした" in m for m in logs), logs)

    def test_one_flow_per_round(self):
        """同じラウンドで2本目が来ても、やり直しの流れは1本"""
        ex, st, _logs = self._executor()
        sent = []

        def hold(hwnd, key, sec, stop=None):
            sent.append(1)
            if len(sent) == 1:
                ex.do_skip()             # 1本目の最中に2本目が来た
            st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            ex.do_skip()

        self.assertEqual(len(sent), 1)
        self.assertEqual(st.suicide_seq, -1, "終わったら次を受け付ける")

    def test_the_success_log_covers_the_end_of_the_hold(self):
        """_skip_time は長押しの開始時。3.0秒ちょうどで切ると終わり際を落とす"""
        for elapsed, expected in ((2.0, True), (4.0, True), (5.0, False)):
            cfg = WindowConfig(do_skip=True)
            monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
            logs = []
            monitor.logger = logs.append
            monitor.st._skip_time = 1000.0

            # この組の setUp は確認の待ちを縮めている。ここは本来の値で見る
            with patch.object(config, "SUICIDE_CONFIRM_SEC", 1.5),                  patch.object(LogMonitor.time, "time", return_value=1000.0 + elapsed):
                monitor._process("You died.")

            self.assertEqual(any("自爆成功" in m for m in logs), expected, elapsed)




class TestChaseExecutor(unittest.TestCase):
    """F1 = 時計回り（MoveLeft＋LookRight）、F2 = 反時計回り（MoveRight＋LookLeft）を押し続ける"""

    def setUp(self):
        p = patch.object(config, "CHASE_RESEND_SEC", 0.01)
        p.start()
        self.addCleanup(p.stop)
        self.logs = []
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x100, osc_port=9000),
                                                WindowState(in_round=True, instance_type=config.INSTANCE_PRIVATE),
                                                lambda: True,
                                                self.logs.append)
        self.osc = _ChaseOsc(self)
        self.ex._osc = self.osc
        self.addCleanup(self.ex.chase_stop)

    def _ones(self, address):
        return self.osc.sent.count((address, 1))

    def test_f1_presses_move_left_and_look_right_and_resends(self):
        self.assertEqual(self.ex.chase_key("cw"), "start")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveLeft") >= 3))

        self.assertEqual({a for a, v in self.osc.sent if v == 1},
                         {"/input/MoveLeft", "/input/LookRight"})
        self.assertEqual([v for _a, v in self.osc.sent], [1] * len(self.osc.sent), "離さずに送り直す")

    def test_f2_presses_move_right_and_look_left(self):
        self.ex.chase_key("ccw")
        self.assertTrue(_wait_until(lambda: self._ones("/input/LookLeft") >= 1))
        self.ex.chase_stop()
        self.assertEqual({a for a, _v in self.osc.sent}, {"/input/MoveRight", "/input/LookLeft"})

    def test_the_same_key_stops_and_releases_only_its_two(self):
        self.ex.chase_key("cw")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveLeft") >= 2))

        self.assertEqual(self.ex.chase_key("cw"), "stop")

        self.assertEqual(sorted(self.osc.sent[-2:]),
                         [("/input/LookRight", 0), ("/input/MoveLeft", 0)])
        self.assertIsNone(self.ex.chase_direction)
        count = len(self.osc.sent)
        time.sleep(0.05)
        self.assertEqual(len(self.osc.sent), count, "止めた後は送らない")

    def test_the_other_key_switches_without_stopping(self):
        self.ex.chase_key("cw")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveLeft") >= 1))

        self.assertEqual(self.ex.chase_key("ccw"), "switch")
        self.assertTrue(_wait_until(lambda: self._ones("/input/MoveRight") >= 1))

        sent = self.osc.sent
        released = max(sent.index(("/input/MoveLeft", 0)), sent.index(("/input/LookRight", 0)))
        self.assertLess(released, sent.index(("/input/MoveRight", 1)), "古い2つを離してから新しい2つ")
        self.assertEqual(self.ex.chase_direction, "ccw")

    def test_stop_when_not_chasing_does_nothing(self):
        self.assertFalse(self.ex.chase_stop())
        self.assertEqual(self.osc.sent, [])

    def test_a_window_without_osc_holds_the_keys(self):
        self.ex._osc = None
        held = []

        def hold(hwnd, keys, stop, resend):
            held.append((hwnd, tuple(keys), resend))
            stop.wait(2.0)
            return True

        with patch.object(WindowOperator, "hold_keys_background", side_effect=hold):
            self.ex.chase_key("cw")
            self.assertTrue(_wait_until(lambda: held))
            self.ex.chase_key("ccw")
            self.assertTrue(_wait_until(lambda: len(held) == 2))
            self.ex.chase_stop()

        self.assertEqual(held, [(0x100, ("a", "."), 0.01), (0x100, ("d", ","), 0.01)])

    def test_keys_that_cannot_be_sent_stop_it_and_say_so(self):
        self.ex._osc = None
        with patch.object(WindowOperator, "hold_keys_background", return_value=False):
            self.ex.chase_key("cw")
            self.assertTrue(_wait_until(lambda: self.ex.chase_direction is None))
        self.assertTrue(any("キーを送れません" in m for m in self.logs), self.logs)




class TestChaseKeysResolve(unittest.TestCase):
    def test_comma_and_period_are_plain_keys(self):
        """「,」「.」が Shift なしの VK に直せる（直せなければ None になり送れない）"""
        tid = WindowOperator.kernel32.GetCurrentThreadId()
        with patch.object(WindowOperator.user32, "GetWindowThreadProcessId", return_value=tid):
            for key, vk in ((",", 0xBC), (".", 0xBE), ("a", 0x41), ("d", 0x44)):
                params = WindowOperator._background_key(0x100, key)
                self.assertIsNotNone(params, key)
                self.assertEqual(params[1], vk, key)




class TestChaseMonitor(unittest.TestCase):
    """ラウンド中だけ。RoundOver・監視の停止で止まる"""

    def setUp(self):
        p = patch.object(config, "CHASE_RESEND_SEC", 0.01)
        p.start()
        self.addCleanup(p.stop)
        self.monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x100, osc_port=9000), {},
                                             lambda _m: None, window_idx=2)
        self.logs = []
        self.monitor.logger = self.logs.append
        self.osc = _ChaseOsc(self)
        self.monitor._action._osc = self.osc
        self.monitor.st.in_round = True
        self.monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.addCleanup(self.monitor._action.chase_stop)

    def test_outside_a_round_it_only_says_so(self):
        self.monitor.st.in_round = False

        self.monitor.on_chase_key("cw", "F1")

        self.assertIsNone(self.monitor._action.chase_direction)
        self.assertEqual(self.logs, ["[窓2] チェイスはラウンド中だけ使えます"])
        time.sleep(0.03)
        self.assertEqual(self.osc.sent, [])

    def test_start_switch_and_stop_are_logged(self):
        self.monitor.on_chase_key("cw", "F1")
        self.monitor.on_chase_key("ccw", "F2")
        self.monitor.on_chase_key("ccw", "F2")

        self.assertEqual(self.logs, ["[窓2] チェイス開始（時計回り・F1）",
                                     "[窓2] チェイスの向きを反時計回りに切り替え",
                                     "[窓2] チェイス停止（F2）"])

    def test_round_over_stops_it(self):
        self.monitor.on_chase_key("cw", "F1")
        self.assertTrue(_wait_until(lambda: self.osc.sent))

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(Recorder, "on_round_over"), \
             patch.object(PlaySound, "play_sound"):
            self.monitor._process("2026.09.30 13:00:00 Debug      -  RoundOver")

        self.assertIsNone(self.monitor._action.chase_direction)
        self.assertEqual(sorted(self.osc.sent[-2:]), [("/input/LookRight", 0), ("/input/MoveLeft", 0)])
        self.assertIn("[窓2] チェイス停止（ラウンド終了）", self.logs)

    def test_stopping_the_monitor_stops_it(self):
        self.monitor.on_chase_key("ccw", "F2")
        self.assertTrue(_wait_until(lambda: self.osc.sent))

        self.monitor.stop()

        self.assertIsNone(self.monitor._action.chase_direction)
        self.assertEqual(sorted(self.osc.sent[-2:]), [("/input/LookLeft", 0), ("/input/MoveRight", 0)])




class TestChaseKeysInTheApp(unittest.TestCase):
    """F1/F2 は押した瞬間の通知で拾う（200ms の見張りでは短い押下を取りこぼした）。
    押しっぱなしの繰り返しは離すまで1回。GUI の処理は after で Tk のスレッドへ"""

    def _app(self, running=True):
        app = type("FakeApp", (), {})()
        app._running = running
        app._capturing_key = False
        app.logs = []
        app._log = app.logs.append
        self.a, self.b = MagicMock(), MagicMock()
        self.a.cfg.hwnd, self.b.cfg.hwnd = 0xA, 0xB
        app.monitors = [self.a, self.b]
        app.scheduled = []
        app.after = lambda _ms, fn, *args: app.scheduled.append((fn, args))
        app._on_chase_key = lambda d, k: mainGUI.App._on_chase_key(app, d, k)
        app._chase_key_down = lambda d, k: mainGUI.App._chase_key_down(app, d, k)
        self.keyboard = MagicMock()
        self.handlers = {}

        def register(key, callback, suppress):
            # 1キーに1つのフック（hook_key）。押した・離したは event_type で分かれる
            self.handlers[("down", key)] = lambda _e: callback(
                type("Event", (), {"event_type": "down"})())
            self.handlers[("up", key)] = lambda _e: callback(
                type("Event", (), {"event_type": "up"})())
            return ("hook", key)

        self.keyboard.hook_key.side_effect = register
        with patch.object(mainGUI, "keyboard", self.keyboard):
            mainGUI.App._hook_chase_keys(app)
        return app

    def _run_scheduled(self, app, front=0xB):
        with patch.object(mainGUI.WindowOperator, "foreground_hwnd", return_value=front):
            while app.scheduled:
                fn, args = app.scheduled.pop(0)
                fn(*args)

    def _tap(self, app, key, times=1, repeats=1, front=0xB):
        """押して離す（押しっぱなしなら押下の通知が repeats 回来る）"""
        for _ in range(times):
            for _ in range(repeats):
                self.handlers[("down", key)](None)
            self.handlers[("up", key)](None)
            self._run_scheduled(app, front)

    def test_the_keys_are_hooked_once_without_suppressing(self):
        self._app()
        self.assertEqual([c.args[0] for c in self.keyboard.hook_key.call_args_list],
                         ["f1", "f2"], "1キーに1つ")
        self.keyboard.on_press_key.assert_not_called()
        self.keyboard.on_release_key.assert_not_called()
        for call in self.keyboard.hook_key.call_args_list:
            self.assertIs(call.kwargs["suppress"], False)

    def test_the_front_watched_window_gets_the_key(self):
        app = self._app()

        self._tap(app, "f1")

        self.b.on_chase_key.assert_called_once_with("cw", "F1")
        self.a.on_chase_key.assert_not_called()

    def test_f2_is_counter_clockwise(self):
        app = self._app()
        self._tap(app, "f2", front=0xA)
        self.a.on_chase_key.assert_called_once_with("ccw", "F2")

    def test_every_short_press_is_seen(self):
        """押して 0.05秒で離す短い押下でも、1回ずつ反応する"""
        app = self._app()
        for _ in range(3):
            self.handlers[("down", "f2")](None)
            time.sleep(0.05)
            self.handlers[("up", "f2")](None)
            self._run_scheduled(app)
        self.assertEqual(self.b.on_chase_key.call_count, 3)

    def test_holding_the_key_reacts_once_until_released(self):
        app = self._app()

        self._tap(app, "f1", repeats=5)
        self._tap(app, "f1", repeats=3)

        self.assertEqual(self.b.on_chase_key.call_count, 2)

    def test_the_gui_work_goes_through_after(self):
        app = self._app()

        self.handlers[("down", "f1")](None)

        self.b.on_chase_key.assert_not_called()
        self.assertEqual(len(app.scheduled), 1, "keyboard のスレッドでは何もしない")
        self._run_scheduled(app)
        self.b.on_chase_key.assert_called_once()

    def test_nothing_while_capturing_a_key(self):
        app = self._app()
        app._capturing_key = True

        self._tap(app, "f1")

        self.assertEqual(app.scheduled, [])
        self.b.on_chase_key.assert_not_called()

    def test_an_unwatched_front_window_does_nothing(self):
        app = self._app()
        self._tap(app, "f1", front=0x999)
        self._tap(app, "f2", front=0)
        self.a.on_chase_key.assert_not_called()
        self.b.on_chase_key.assert_not_called()

    def test_a_key_while_not_running_is_ignored(self):
        app = self._app(running=False)
        self._tap(app, "f1")
        self.b.on_chase_key.assert_not_called()

    def test_without_keyboard_nothing_is_hooked(self):
        app = type("FakeApp", (), {})()
        with patch.object(mainGUI, "keyboard", None):
            mainGUI.App._hook_chase_keys(app)
        self.assertEqual(app._chase_hooks, [])

    def test_closing_unhooks_the_keys(self):
        app = self._app()
        with patch.object(mainGUI, "keyboard", self.keyboard):
            mainGUI.App._unhook_chase_keys(app)
        self.assertEqual([c.args[0] for c in self.keyboard.unhook.call_args_list],
                         [("hook", "f1"), ("hook", "f2")])
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        close = src[src.index("    def _on_close(self):"):]
        if "\n    def " in close:              # 最後のメソッドなら末尾まで
            close = close[:close.index("\n    def ")]
        self.assertIn("self._unhook_chase_keys()", close)

    def test_the_keys_are_hooked_at_startup_not_polled(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        start = src[src.index("    def _start_emergency_stop_polling(self):"):]
        start = start[:start.index("\n    def ")]
        self.assertIn("self._hook_chase_keys()", start)
        self.assertIn("self._hook_stop_start_keys()", start)
        check = src[src.index("    def _on_stop_start_key_event(self, event=None):"):]
        check = check[:check.index("\n    def ")]
        self.assertNotIn("chase", check, "停止・開始の通知では見ない（二重に反応しない）")
        self.assertNotIn("_poll_chase_keys", src)
        self.assertNotIn("_poll_emergency_stop_key", src, "200ms の見張りはもう無い")

    def test_the_keys_are_f1_and_f2(self):
        self.assertEqual((config.CHASE_CW_KEY, config.CHASE_CCW_KEY), ("f1", "f2"))
        self.assertEqual(config.CHASE_RESEND_SEC, 0.2)




class TestChaseKeysEndToEnd(unittest.TestCase):
    """押下と離上の通知を順に流して、実際の窓（LogMonitor）まで届くこと"""

    def setUp(self):
        p = patch.object(config, "CHASE_RESEND_SEC", 0.01)
        p.start()
        self.addCleanup(p.stop)
        self.keys = TestChaseKeysInTheApp()
        self.app = self.keys._app()
        self.monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0xB, osc_port=9000), {},
                                             lambda _m: None, window_idx=2)
        self.logs = []
        self.monitor.logger = self.logs.append
        self.monitor._action._osc = _ChaseOsc(self)
        self.monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.app.monitors = [self.monitor]
        self.addCleanup(self.monitor._action.chase_stop)

    def test_start_stop_start_with_short_presses(self):
        self.monitor.st.in_round = True

        self.keys._tap(self.app, "f2", times=3)

        self.assertEqual(self.logs, ["[窓2] チェイス開始（反時計回り・F2）",
                                     "[窓2] チェイス停止（F2）",
                                     "[窓2] チェイス開始（反時計回り・F2）"])

    def test_outside_a_round_every_press_says_so(self):
        self.monitor.st.in_round = False

        self.keys._tap(self.app, "f2", times=2)

        self.assertEqual(self.logs, ["[窓2] チェイスはラウンド中だけ使えます"] * 2)




class TestLiveSkipFromTheNextRound(unittest.TestCase):
    """ラウンド中に自動自爆を入れても、そのラウンドでは自爆しない／次のラウンドから効く"""

    def setUp(self):
        # ラウンド指定自爆のテストの道具を借りる（そのテストは流さない）
        self.helper = TestSkipRoundsByType("test_a_listed_round_is_skipped")
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self._monitor = self.helper._monitor
        self._killers = self.helper._killers

    def test_turning_skip_on_mid_round_waits_for_the_next_round(self):
        monitor = self._monitor(do_skip=False)
        self.assertNotIn("do_skip", self._killers(monitor))

        monitor.cfg.do_skip = True                     # 動作中に入れた（Tk のスレッドから代入）
        with patch.object(LogMonitor.threading, "Thread") as thread:
            monitor._process("2026.10.01 12:00:30 Debug      -  Verified")
        self.assertNotIn("do_skip", _decision_threads(thread), "そのラウンドはやり直さない")

        monitor.st.round_seq += 1                      # 次のラウンド
        self.assertIn("do_skip", self._killers(monitor))




class TestSuicideBackgroundRouting(unittest.TestCase):
    """do_skip の送信経路（背面だけ。フォーカス方式への落とし先は廃止）"""

    def setUp(self):
        _single_attempt(self)
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.speed_freeze_reset()
        SharedState.round_freeze_reset()
        SharedState.set_suicide_key("^")

    tearDown = setUp

    def _executor(self):
        cfg = WindowConfig(hwnd=123, do_skip=True)
        st = WindowState(in_round=True)
        logs = []
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append), st, logs

    def test_background_success_does_not_take_focus(self):
        ex, st, _logs = self._executor()

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=True) as mock_bg, \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()

        mock_bg.assert_called_once_with(123, "^", config.SUICIDE_HOLD_SEC, stop=ANY)
        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertGreater(st._skip_time, 0, "死亡判定用の時刻は残すこと")

    def test_background_failure_does_not_fall_back(self):
        """落とすとロック無しでプレイ中の窓に自爆キーが押されうる"""
        ex, st, logs = self._executor()

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=False), \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()

        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertTrue(any("自爆できませんでした" in m for m in logs), logs)
        self.assertEqual(st._skip_time, 0.0, "自爆成功と誤判定しないこと")

    def test_the_old_switches_are_gone(self):
        self.assertFalse(hasattr(config, "SUICIDE_BACKGROUND"))
        self.assertFalse(hasattr(config, "SUICIDE_FOCUS_SETTLE_SEC"))




class TestSuicideIsolation(unittest.TestCase):
    """自爆はロックもフリーズも見ない。前面の窓を切り替えないので要らない"""

    def setUp(self):
        _single_attempt(self)
        self._reset()
        SharedState.set_suicide_key("^")

    def tearDown(self):
        self._reset()

    @staticmethod
    def _reset():
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.speed_freeze_reset()
        SharedState.round_freeze_reset()

    def _executor(self, hwnd=123, **state):
        cfg = WindowConfig(hwnd=hwnd, do_skip=True)
        st = WindowState(in_round=True, **state)
        logs = []
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append), st

    def _skip(self, ex, timeout=2.0):
        """別スレッドで回す。止まってしまったら False"""
        sent = []
        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda h, k, sec, stop=None: sent.append(h) or True):
            t = threading.Thread(target=ex.do_skip, daemon=True)
            t.start()
            t.join(timeout)
        return (not t.is_alive()), sent

    # ── ロック ──────────────────────────────
    def test_it_does_not_wait_for_the_lock(self):
        """Begin やクリックがロックを握っている間でも自爆する"""
        ex, _st = self._executor()
        with SharedState._GLOBAL_ACTION_LOCK:
            finished, sent = self._skip(ex)

        self.assertTrue(finished, "ロックを待って止まった")
        self.assertEqual(sent, [123])

    def test_it_never_holds_the_lock(self):
        ex, _st = self._executor()
        held = []
        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda *a, **_: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked()) or True):
            ex.do_skip()

        self.assertEqual(held, [False])

    # ── フリーズ ─────────────────────────────
    def test_another_windows_continue_freeze_does_not_stop_it(self):
        _freeze_other_continue()
        ex, _st = self._executor()

        finished, sent = self._skip(ex)

        self.assertTrue(finished)
        self.assertEqual(sent, [123])

    def test_another_windows_item_wait_does_not_stop_it(self):
        SharedState.EQUIP_WAIT_EVENT.clear()
        ex, _st = self._executor()

        finished, sent = self._skip(ex)

        self.assertTrue(finished)
        self.assertEqual(sent, [123])

    def test_speed_and_entry_freezes_do_not_stop_it(self):
        SharedState.SPEED_FREEZE_EVENT.clear()
        SharedState.ROUND_FREEZE_EVENT.clear()
        ex, _st = self._executor()

        finished, sent = self._skip(ex)

        self.assertTrue(finished)
        self.assertEqual(sent, [123])

    # ── 自窓の状態では止まる ─────────────────────
    def test_its_own_continue_round_stops_it(self):
        ex, _st = self._executor(is_continue_round=True)

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    def test_its_own_item_wait_stops_it(self):
        ex, _st = self._executor(waiting_for_equip=True)

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    def test_outside_a_round_it_does_nothing(self):
        ex, st = self._executor()
        st.in_round = False

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    def test_a_stopped_monitor_does_nothing(self):
        cfg = WindowConfig(hwnd=123, do_skip=True)
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(in_round=True),
                                           lambda: False, lambda _m: None)

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    # ── 同時に ──────────────────────────────
    def test_two_windows_skip_at_the_same_time(self):
        a, _ = self._executor(hwnd=0xA)
        b, _ = self._executor(hwnd=0xB)
        inside = []
        peak = []
        gate = threading.Barrier(2, timeout=2.0)
        lock = threading.Lock()

        def hold(hwnd, key, sec, stop=None):
            with lock:
                inside.append(hwnd)
                peak.append(len(inside))
            gate.wait()                 # 2つとも中に入るまで待つ
            with lock:
                inside.remove(hwnd)
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            threads = [threading.Thread(target=ex.do_skip, daemon=True)
                       for ex in (a, b)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(3.0)

        self.assertFalse(any(t.is_alive() for t in threads), "片方が待たされた")
        self.assertEqual(max(peak), 2, "2窓が同時に送っていること")




class TestActionExecutorSkip(unittest.TestCase):
    def setUp(self):
        _single_attempt(self)
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()
        SharedState.set_suicide_key(config.SELF_SUICIDE_KEY)

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()
        SharedState.set_suicide_key(config.SELF_SUICIDE_KEY)
        SharedState.set_item_begin_mode(False)

    def test_do_skip_cancels_when_hwnd_missing(self):
        cfg = WindowConfig(hwnd=0)
        st = WindowState(in_round=True)
        logs: list[str] = []
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)

        with patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold:
            executor.do_skip()

        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertTrue(any("HWND" in msg for msg in logs))

    def test_do_skip_sends_the_current_key_in_the_background(self):
        cfg = WindowConfig(hwnd=123)
        st = WindowState(in_round=True)
        SharedState.set_suicide_key("x")
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _msg: None)

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=True) as mock_bg, \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(ActionExecutor.time, "sleep") as mock_sleep:
            executor.do_skip()

        mock_bg.assert_called_once_with(123, "x", config.SUICIDE_HOLD_SEC, stop=ANY)
        mock_focus.assert_not_called()
        mock_sleep.assert_not_called()

    def test_do_after_round_uses_window_instance_not_global_instance(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        cfg = WindowConfig(hwnd=123)
        # Begin は Verified Round End の後にしか押さないので、実機同様に立てる
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True)
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _msg: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ActionExecutor.time, "sleep"), \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key"), \
             patch.object(WindowOperator, "click"):
            executor.do_after_round()

        mock_focus.assert_called()

    def test_do_after_round_item_begin_mode_no_deadlock_when_already_equipped(self):
        """アイテム取得→Beginモード: 自窓がクリアしたEQUIP_WAIT_EVENTを
        自分で待つデッドロックにならず、Beginクリックまで到達する"""
        SharedState.set_item_begin_mode(True)
        cfg = WindowConfig(hwnd=123)
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=True,
            item_id=5,  # フリーズ中に装備済み
            round_end_seen=True,
        )
        SharedState.equip_freeze_start(st)  # Verified End時に自窓がフリーズを張った状態
        calls = {"n": 0}

        def is_running():
            # デッドロック時（旧実装）はここがFalseになりclick未達でテスト失敗する
            calls["n"] += 1
            return calls["n"] < 30

        executor = ActionExecutor.ActionExecutor(cfg, st, is_running, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window"), \
             patch.object(WindowOperator, "hold_key"), \
             patch.object(WindowOperator, "click") as mock_click:
            executor.do_after_round()

        mock_click.assert_called()

    def test_do_after_round_item_begin_mode_waits_for_equip_then_begins(self):
        """アイテム取得→Beginモード: 未装備なら装備を待ち、装備後にBeginへ進む"""
        SharedState.set_item_begin_mode(True)
        cfg = WindowConfig(hwnd=123)
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=True,
            item_id=0,  # 未装備
            round_end_seen=True,
        )
        SharedState.equip_freeze_start(st)
        logs: list[str] = []
        sleep_count = {"n": 0}

        def fake_sleep(_sec):
            # 装備待ちループ数周後に装備完了をシミュレート
            sleep_count["n"] += 1
            if sleep_count["n"] >= 3:
                st.item_id = 5

        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)

        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep), \
             patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window"), \
             patch.object(WindowOperator, "hold_key"), \
             patch.object(WindowOperator, "click") as mock_click:
            executor.do_after_round()

        mock_click.assert_called()
        self.assertTrue(any("装備確認" in msg for msg in logs))


    def _run_late_item_lost(self, osc_port: int):
        """Verified Round End で初めてロストが判明する実機どおりの順序を再現する。

        do_after_round は RoundOver+11秒で始まるため、開始時点では
        waiting_for_equip はまだ False。Round End 待ちの最中に立つ。
        """
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=False,   # RoundOver時点ではまだ判明していない
            round_end_seen=False,
            item_id=1,
        )
        order: list[str] = []

        def fake_sleep(_sec):
            # Round End 待ちの最中に Verified Round End が届く
            st.round_end_seen = True
            st.waiting_for_equip = True

        def fake_freeze_start(state):
            order.append("freeze")
            _real_freeze_start(state)

        _real_freeze_start = SharedState.equip_freeze_start
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor, "time", _module_time(fake_sleep)),              patch.object(ActionExecutor.SharedState, "equip_freeze_start",
                          side_effect=fake_freeze_start),              patch.object(ActionExecutor.PlaySound, "play_sound") as mock_sound,              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "hold_key"),              patch.object(WindowOperator, "click",
                          side_effect=lambda: order.append("click")):
            executor.do_after_round()

        return st, order, mock_sound

    def test_do_after_round_freezes_when_item_lost_found_at_round_end_osc(self):
        """OSC窓: RoundOver後に判明したアイテムロストでもフリーズが張られる"""
        with patch.object(ActionExecutor.OSCClient, "OSCClient", return_value=MagicMock()):
            st, order, mock_sound = self._run_late_item_lost(osc_port=9000)

        self.assertTrue(st.equip_freeze_held, "フリーズが張られていない")
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(order, ["freeze", "click"], "フリーズはBeginクリック前")
        # 音声は前面化と一緒にしか出さない。押す直前には鳴らさない
        mock_sound.assert_not_called()

    def test_do_after_round_freezes_when_item_lost_found_at_round_end_no_osc(self):
        """非OSC窓: 同上（キーボード操作経路でもフリーズが張られる）"""
        st, order, mock_sound = self._run_late_item_lost(osc_port=0)

        self.assertTrue(st.equip_freeze_held, "フリーズが張られていない")
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(order, ["freeze", "click"], "フリーズはBeginクリック前")
        # 音声は前面化と一緒にしか出さない。押す直前には鳴らさない
        mock_sound.assert_not_called()




class TestHandsFreePerWindow(unittest.TestCase):
    """放置モードのトグルは全窓共通だが、効くのはprivate系の窓だけ

    干し芋の窓とプラベの窓を同時に監視する運用があるため、窓ごとに判定する。
    """

    def setUp(self):
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.equip_freeze_reset()

    def tearDown(self):
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.equip_freeze_reset()

    def _monitor(self, instance_type):
        cfg = WindowConfig(
            do_skip=True,
            voice_continue="continue.mp3",
            voice_fog="fog.mp3",
            voice_item_lost="lost.mp3",
            voice_foxy="foxy.mp3",
            voice_intermission="intermission.mp3",
            announce_intermission=True,
        )
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        return monitor

    def test_gate_is_per_window(self):
        SharedState.set_hands_free(True)
        self.assertTrue(self._monitor(config.INSTANCE_PRIVATE)._hands_free())
        self.assertFalse(self._monitor(config.INSTANCE_HOSHIIMO)._hands_free())
        self.assertFalse(self._monitor(config.INSTANCE_PUBLIC)._hands_free())

    def test_gate_is_off_when_the_toggle_is_off(self):
        self.assertFalse(self._monitor(config.INSTANCE_PRIVATE)._hands_free())

    def _announce_calls(self, instance_type):
        monitor = self._monitor(instance_type)
        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "register_round"), \
             patch.object(LogMonitor.threading, "Thread"):
            monitor._process("foxy the pirate turned evil!")
            monitor._process("Verified Round End")
        return [c.args[0] for c in mock_play.call_args_list]

    def test_private_window_is_silent_while_hands_free(self):
        SharedState.set_hands_free(True)
        self.assertEqual(self._announce_calls(config.INSTANCE_PRIVATE), [])

    def test_hoshiimo_window_still_announces_while_hands_free(self):
        """同時に監視している干し芋の窓は影響を受けない"""
        SharedState.set_hands_free(True)
        calls = self._announce_calls(config.INSTANCE_HOSHIIMO)

        self.assertIn("foxy.mp3", calls)
        self.assertIn("intermission.mp3", calls)

    def test_unknown_killers_suicide_only_in_private(self):
        """霧のテラー不明での即自爆もprivateの窓だけ"""
        SharedState.set_hands_free(True)
        line = "Killers is unknown - ??? // ??? // Round type is Fog"

        for itype, expected in ((config.INSTANCE_PRIVATE, True),
                                (config.INSTANCE_HOSHIIMO, False)):
            monitor = self._monitor(itype)
            with patch.object(LogMonitor.threading, "Thread") as mock_thread:
                monitor._process(line)
            self.assertEqual(mock_thread.called, expected, itype)

    def test_item_lost_announcement_follows_the_same_gate(self):
        """アイテムロストの通知もprivateの窓だけ黙る"""
        SharedState.set_hands_free(True)
        played = []

        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO):
            cfg = WindowConfig(hwnd=123, voice_item_lost="lost.mp3")
            st = WindowState(instance_type=itype, waiting_for_equip=True, item_id=0)
            ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
            with patch.object(PlaySound, "play_sound") as mock_play:
                ex.announce_item_lost_once()
            played.append(mock_play.called)

        self.assertEqual(played, [False, True], "privateだけ黙ること")

    def test_toggle_can_be_turned_on_regardless_of_instance(self):
        """GUIのトグル自体はインスタンスに関係なくONにできる"""
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app.btn_hands_free = MagicMock()

        mainGUI.App._toggle_hands_free(app)

        self.assertTrue(SharedState.get_hands_free())




class TestSkipRoundsByType(unittest.TestCase):
    """privateのラウンド指定自爆（続行リストより優先、3クラ解放は例外）"""

    CLASSIC_KEY = "Classic/クラシック"
    DTM = LogMonitor.DTM_TERROR_ID

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, skip_rounds=("Classic",), do_skip=True,
                 cancel_afk=True, keep_on=None,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=do_skip, cancel_afk=cancel_afk,
                           voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._running = True
        return monitor

    def _killers(self, monitor, ids=(99,), round_type=None, settle=True):
        """settle: 置き換え待ちに入ったら、合図が来ないまま0.3秒経ったものとして
        その場で判定まで進める（Classic の1体構成は Gigabytes 待ちに入りうる）"""
        round_type = round_type or monitor.st.round_type
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type, revealed=False)
            started = _decision_threads(mock_thread)
            if settle and "_delayed_decision" in started:
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += _decision_threads(mock_thread)
        return started

    # ── 基本 ────────────────────────────────
    def test_an_unlisted_round_uses_the_normal_judgement(self):
        monitor = self._monitor(skip_rounds=("Bloodbath",),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_a_listed_round_is_skipped(self):
        monitor = self._monitor()

        started = self._killers(monitor)

        self.assertIn("do_skip", started)

    def test_the_skip_beats_the_keep_list(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)

    def test_auto_skip_off_does_not_skip(self):
        monitor = self._monitor(do_skip=False)

        started = self._killers(monitor)

        self.assertNotIn("do_skip", started)

    def test_no_announce_and_no_freeze(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})
        monitor.st.is_continue_round = True
        SharedState.continue_round_start(monitor.st)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers([99], "Classic", revealed=False)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        mock_play.assert_not_called()

    def _run_delayed(self, monitor):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_decision("Classic", 0.0, monitor.st.round_seq)
        return _decision_threads(mock_thread)

    # ── Variant例外（常に効く。切り替えは廃止） ───────────
    def test_the_variant_exemption_falls_through(self):
        """Variantなら自爆指定より優先して通常判定へ"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.ATRACHED_ID}})
        monitor.st.terror_ids = [config.ATRACHED_ID]

        started = self._run_delayed(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_the_exemption_covers_all_four_variants(self):
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
                    config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID):
            monitor = self._monitor(keep_on={self.CLASSIC_KEY: {tid}})
            monitor.st.terror_ids = [tid]

            started = self._run_delayed(monitor)

            self.assertNotIn("do_skip", started, tid)
            self.assertTrue(monitor.st.is_continue_round, tid)

    def test_the_exemption_still_skips_a_plain_terror(self):
        # 待ちを止めるのに st.gigabytes を立ててはいけない。_on_killers が
        # ids を [314] に差し替えるので、テラーIDごと変わってしまう
        monitor = self._monitor()

        with patch.object(LogMonitor.LogMonitor,
                          "_replacement_changes_decision", return_value=False):
            started = self._killers(monitor)

        self.assertIn("do_skip", started)
        self.assertEqual(monitor.st.terror_ids, [99], "IDが差し替わっていないこと")

    def test_a_variant_is_never_skipped_by_the_designation(self):
        """以前は切り替え次第で自爆していた。いまは常にリストで判定する"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.ATRACHED_ID}})
        monitor.st.atrached_variant = True

        started = self._killers(monitor, [config.ATRACHED_ID])

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_config_has_no_switch_any_more(self):
        self.assertFalse(hasattr(WindowConfig(), "skip_variant_exempt"))

    # ── 3クラ解放が勝つ ───────────────────────
    def test_the_three_classic_unlock_wins(self):
        """DTMが出たラウンドは自爆指定を無視する（3クラ稼ぎを壊さない）"""
        monitor = self._monitor()

        started = self._killers(monitor, [self.DTM])

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_skip_applies_once_three_wins_are_done(self):
        monitor = self._monitor()
        monitor.st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS

        started = self._killers(monitor, [self.DTM])

        self.assertIn("do_skip", started)

    # ── 特殊ラウンドを経験したら3勝扱い（Twilight は除く） ──────────
    def _special_round(self, monitor, round_type):
        """その種別のラウンドで Killers have been set を受ける。出たログを返す"""
        logs = []
        monitor.logger = logs.append
        monitor.st.round_type = round_type
        with patch.object(LogMonitor.threading, "Thread"),              patch.object(PlaySound, "play_sound"):
            monitor._process("2026.09.30 13:36:11 Debug      -  Killers have been set - "
                             f"1 0 0 // Round type is {round_type}")
        monitor.st.round_type = "Classic"            # 次のラウンド
        return [m for m in logs if "3勝扱い" in m]

    def test_twilight_does_not_count_as_three_wins(self):
        """Twilight は特殊ラウンドだが3クラ前にも出る（2026-09-30 窓2: 0勝で DTM を自爆した）"""
        monitor = self._monitor()

        told = self._special_round(monitor, "Twilight")

        self.assertEqual(monitor.st.open_special_round_wins, 0)
        self.assertEqual(told, [])
        started = self._killers(monitor, [self.DTM])
        self.assertNotIn("do_skip", started, "続く DTM は続行（3クラ解放）")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertIn("Twilight", config.SPECIAL_ROUND, "特殊ラウンドであることは変わらない")

    def test_other_special_rounds_still_count_and_say_so_once(self):
        for round_type in ("Fog", "Mystic Moon", "Blood Moon"):
            monitor = self._monitor()

            told = self._special_round(monitor, round_type)

            self.assertEqual(monitor.st.open_special_round_wins,
                             config.OPEN_SPECIAL_ROUND_TARGET_WINS, round_type)
            self.assertEqual(told, [f"[窓1] 特殊ラウンド（{round_type}）を経験したので3勝扱い"
                                    " → 以降のDTM/Waldoはスキップします"], round_type)
            self.assertIn("do_skip", self._killers(monitor, [self.DTM]), round_type)

    def test_nothing_is_said_when_three_wins_are_already_done(self):
        monitor = self._monitor()
        monitor.st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS

        self.assertEqual(self._special_round(monitor, "Fog"), [])
        self.assertEqual(monitor.st.open_special_round_wins,
                         config.OPEN_SPECIAL_ROUND_TARGET_WINS)

    def test_nothing_is_said_when_the_dtm_waldo_continue_is_off(self):
        monitor = self._monitor(cancel_afk=False)

        self.assertEqual(self._special_round(monitor, "Fog"), [])
        self.assertEqual(monitor.st.open_special_round_wins,
                         config.OPEN_SPECIAL_ROUND_TARGET_WINS, "3勝扱いにはなる")

    def test_the_skip_applies_when_cancel_afk_is_off(self):
        monitor = self._monitor(cancel_afk=False)

        started = self._killers(monitor, [self.DTM])

        self.assertIn("do_skip", started)

    # ── 他のインスタンス ──────────────────────
    def test_group_instances_are_untouched(self):
        """干し芋/焼き芋は GroupRound の結論が先に出る"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._monitor(skip_rounds=("8 Pages",),
                                    instance_type=itype)
            monitor.st.round_type = "8 Pages"

            started = self._killers(monitor, [1, 2])

            self.assertEqual(started, [], itype)   # 全続行のまま

    def test_public_is_untouched(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP):
            monitor = self._monitor(instance_type=itype)

            started = self._killers(monitor)

            self.assertEqual(started, [], itype)

    def test_hands_free_still_decides_first(self):
        """放置モードの3分岐はこの判定より前。そのまま"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(skip_rounds=())
        monitor.st.item_id = 0

        started = self._killers(monitor)

        self.assertIn("do_skip", started, "放置モードの即自爆が効いていること")

    # ── 置き換え待ち ───────────────────────
    def test_a_designated_round_waits_when_a_variant_would_change_it(self):
        """Gigabytes が来ればリスト判定で続行になる。来るまで待つ"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.GIGABYTES_ID}})

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["_delayed_decision"])

    def test_no_wait_when_neither_side_continues(self):
        """置き換わってもリストに無ければ、どちらにしても自爆"""
        monitor = self._monitor()

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["do_skip"])

    def test_an_undesignated_round_waits_too(self):
        """自爆指定が無くても待つ（以前は指定があるときしか待たなかった）"""
        monitor = self._monitor(skip_rounds=("Bloodbath",),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["_delayed_decision"])

    def test_nothing_selected_and_nothing_listed_does_not_wait(self):
        monitor = self._monitor(skip_rounds=())

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["do_skip"])

    def test_the_wait_ends_in_a_skip_without_a_variant(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [99]

        self.assertIn("do_skip", self._run_delayed(monitor))

    def test_the_wait_falls_through_when_a_variant_arrives(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.GIGABYTES_ID}})
        monitor.st.terror_ids = [config.GIGABYTES_ID]
        monitor.st.gigabytes = True     # _run_delayed は _on_killers を通らないので
                                        # ここでは ids の差し替えは起きない

        started = self._run_delayed(monitor)

        self.assertEqual(started, [])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_aborts_when_the_round_changed(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [99]
        monitor.st.round_seq = 5

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision("Classic", 0.0, 4)

        mock_thread.assert_not_called()
