"""OSC 操作・クリック操作は private のインスタンスだけ（DF。前面化・グループの自爆は今どおり）"""
from tests.support import *  # noqa: F401,F403


NOT_PRIVATE = (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP, config.INSTANCE_EMERALD_CITY,
               config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO)
LINE = "2026.10.01 12:00:00 Debug      -  "


class TestCanOperate(unittest.TestCase):
    def test_only_private(self):
        self.assertTrue(ActionExecutor.can_operate_in(config.INSTANCE_PRIVATE))
        for itype in NOT_PRIVATE + ("", None):
            with self.subTest(itype=itype):
                self.assertFalse(ActionExecutor.can_operate_in(itype))

    def test_a_new_window_is_not_private_until_it_joins(self):
        """入室前（種類が分からない）は不可。今までの private の判定と同じ"""
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1), WindowState(), lambda: True,
                                           lambda _m: None)
        self.assertFalse(ex.can_operate())


class TestChaseOnlyInPrivate(unittest.TestCase):
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
        self.addCleanup(self.monitor._action.chase_stop)

    def test_f1_and_f2_send_nothing_outside_private(self):
        for itype in NOT_PRIVATE:
            for direction, key in (("cw", "F1"), ("ccw", "F2")):
                with self.subTest(itype=itype, key=key):
                    self.logs.clear()
                    self.monitor.st.instance_type = itype
                    self.monitor.on_chase_key(direction, key)
                    time.sleep(0.03)
                    self.assertIsNone(self.monitor._action.chase_direction)
                    self.assertEqual(self.osc.sent, [])
                    self.assertEqual(self.logs, ["[窓2] チェイスはプライベートのインスタンスだけで使えます"])

    def test_outside_a_round_the_instance_is_told_first(self):
        """ラウンド外でも、private でなければそちらを言う（ラウンドを待っても使えないため）"""
        self.monitor.st.in_round = False
        self.monitor.st.instance_type = config.INSTANCE_PUBLIC
        with patch.object(self.monitor._action, "chase_key") as chase_key:
            self.monitor.on_chase_key("cw", "F1")
        chase_key.assert_not_called()
        self.assertEqual(self.logs, ["[窓2] チェイスはプライベートのインスタンスだけで使えます"])

    def test_the_executor_itself_refuses(self):
        """入口の関数を見ているのは LogMonitor だけではない（キーを経ずに呼ばれても送らない）"""
        self.monitor.st.instance_type = config.INSTANCE_PUBLIC
        self.assertEqual(self.monitor._action.chase_key("cw"), "denied")
        time.sleep(0.03)
        self.assertEqual(self.osc.sent, [])

    def test_a_window_without_osc_holds_no_keys_outside_private(self):
        self.monitor._action._osc = None
        self.monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        with patch.object(WindowOperator, "hold_keys_background") as hold:
            self.monitor.on_chase_key("cw", "F1")
            time.sleep(0.03)
        hold.assert_not_called()

    def test_private_still_runs(self):
        self.monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.monitor.on_chase_key("cw", "F1")
        self.assertTrue(_wait_until(lambda: ("/input/MoveLeft", 1) in self.osc.sent))
        self.assertEqual(self.logs, ["[窓2] チェイス開始（時計回り・F1）"])

    def _start_in_private(self):
        self.monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.monitor.on_chase_key("cw", "F1")
        self.assertTrue(_wait_until(lambda: ("/input/MoveLeft", 1) in self.osc.sent))
        self.logs.clear()

    def _released(self):
        return {("/input/MoveLeft", 0), ("/input/LookRight", 0)} <= set(self.osc.sent)

    def test_joining_a_non_private_instance_stops_and_releases(self):
        self._start_in_private()
        self.monitor._process(LINE + "[Behaviour] Joining wrld_x:1~region(jp)")
        self.assertIsNone(self.monitor._action.chase_direction)
        self.assertTrue(self._released())
        time.sleep(0.05)
        stopped = [m for m in self.logs if "チェイス停止" in m]
        self.assertEqual(stopped, ["[窓2] チェイス停止（プライベートのインスタンスではなくなりました）"])
        sent = len(self.osc.sent)
        time.sleep(0.05)
        self.assertEqual(len(self.osc.sent), sent, "止めた後は送らない")

    def test_joining_another_private_instance_keeps_it(self):
        self._start_in_private()
        self.monitor._process(LINE + "[Behaviour] Joining wrld_x:2~private(usr_me)~region(jp)")
        self.assertEqual(self.monitor._action.chase_direction, "cw")

    def test_the_resend_loop_notices_by_itself(self):
        """入室の行を経ずに種類が変わっても、送り直しの度に見て止めて離す"""
        self._start_in_private()
        self.monitor.st.instance_type = config.INSTANCE_YAKIIMO
        self.assertTrue(_wait_until(lambda: self.monitor._action.chase_direction is None))
        self.assertTrue(self._released())
        self.assertEqual([m for m in self.logs if "チェイス停止" in m],
                         ["[窓2] チェイス停止（プライベートのインスタンスではなくなりました）"])


class _Osc:
    """OSC の送信を全部記録する偽物"""

    def __init__(self):
        self.sent = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.sent.append((name, args))
            return True
        return record


class TestOperationsOnlyInPrivate(unittest.TestCase):
    """Begin・押し直し・位置合わせ・速度検知の横移動・アイテム取得・AFK 対策・視点の戻し"""

    def setUp(self):
        self.osc = _Osc()
        self.st = WindowState(instance_type=config.INSTANCE_PUBLIC, round_seq=4)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x10, osc_port=9000), self.st,
                                                lambda: True, lambda _m: None,
                                                auto_begin_active=lambda: True)
        self.ex._osc = self.osc
        self.ex._speed_ready.set()
        self.inputs = []
        for name in ("click", "cursor_over_window", "hold_key_background", "hold_keys_background",
                     "focus_window"):
            p = patch.object(WindowOperator, name,
                             side_effect=lambda *a, n=name, **k: self.inputs.append(n))
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(ActionExecutor._FetchMouse, "move_rel",
                         side_effect=lambda *a: self.inputs.append("mouse"))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(WindowOperator, "click_with_tab",
                         side_effect=lambda *a, **k: self.inputs.append("mouse_click") or True)
        p.start()
        self.addCleanup(p.stop)

    def _nothing_sent(self):
        self.assertEqual(self.osc.sent, [])
        self.assertEqual(self.inputs, [])

    def test_moves(self):
        for itype in NOT_PRIVATE:
            self.st.instance_type = itype
            self.assertFalse(self.ex.move("forward", 1.0))
            self.assertFalse(self.ex.move_forward_left(1.0, 0.5))
        self.ex._osc = None                         # キーの窓も
        self.assertFalse(self.ex.move("forward", 1.0))
        self.assertFalse(self.ex.move_forward_left(1.0, 0.5))
        self._nothing_sent()

    def test_a_move_in_progress_lets_go_when_the_instance_changes(self):
        self.st.instance_type = config.INSTANCE_PRIVATE
        stop = []
        self.osc.press_multi = lambda presses, stop=None: stop_seen(stop)

        def stop_seen(fn):
            stop.append(fn())
            self.st.instance_type = config.INSTANCE_PUBLIC
            stop.append(fn())
            return True
        self.ex.move_forward_left(1.0, 0.5)
        self.assertEqual(stop, [False, True])

    def test_begin_after_round_and_again(self):
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(self.ex, "_begin_move") as begin_move, \
             patch.object(self.ex, "_wait_round_end") as wait_end, \
             patch.object(self.ex, "_wait_other_windows") as wait_others:
            self.ex.do_after_round()
            self.ex.do_begin_again(self.st.round_seq)
        begin_move.assert_not_called()      # 動いていないのに「最後までやった」にしない
        wait_end.assert_not_called()
        wait_others.assert_not_called()
        self.assertFalse(self.st.begin_move_done)
        self._nothing_sent()

    def test_use_right_spam(self):
        self.assertIsNone(self.ex._start_use_spam(self.st.round_seq))
        self.st.round_over_time = time.time() - 60   # もう送り始める時刻
        stop = threading.Event()
        timer = threading.Timer(0.3, stop.set)      # 回ってしまっても止まるように
        timer.start()
        self.addCleanup(timer.cancel)
        self.ex._spam_use_right_loop(stop, self.st.round_seq)
        self.assertFalse(stop.is_set(), "回らずにすぐ返る")
        self._nothing_sent()

    def test_cursor_dip_and_click(self):
        self.assertFalse(self.ex._dip_cursor_for_begin(""))
        entered = []

        class Lock:                                 # 送らない窓はほかの窓を待たせない
            def __enter__(lock):
                entered.append(1)
            def __exit__(lock, *a):
                return False
        with patch.object(WindowOperator, "borrow_front") as borrow, \
             patch.object(SharedState, "_GLOBAL_ACTION_LOCK", Lock()):
            self.assertFalse(self.ex._press_begin())
            self.assertFalse(self.ex._press_begin(again=True, click_only=True))
        borrow.assert_not_called()
        self.assertEqual(entered, [])
        self.assertFalse(self.ex._should_retry_begin(self.st.round_seq))
        self._nothing_sent()

    def test_the_click_is_checked_again_inside_the_lock(self):
        """差し込みを見送った後、ロックを待つ間に private でなくなったらクリックしない"""
        self.st.instance_type = config.INSTANCE_PRIVATE
        self.ex._osc = None                         # 差し込みは使わない窓（すぐクリックへ）

        class Lock:
            def __enter__(lock):
                self.st.instance_type = config.INSTANCE_PUBLIC
            def __exit__(lock, *a):
                return False
        with patch.object(SharedState, "_GLOBAL_ACTION_LOCK", Lock()), \
             patch.object(WindowOperator, "borrow_front") as borrow:
            self.assertFalse(self.ex._press_begin())
        borrow.assert_not_called()
        self._nothing_sent()

    def test_the_speed_strafe(self):
        SharedState.set_speed_detect(True)
        self.addCleanup(SharedState.set_speed_detect, config.SPEED_DETECT_ENABLED)
        with patch.object(self.ex, "move") as move:
            self.ex.do_speed_strafe()
        move.assert_not_called()

    def test_item_fetch(self):
        SharedState.set_item_fetch(True)
        self.addCleanup(SharedState.set_item_fetch, False)
        self.st.last_lost_item_id = 29
        self.st.item_id, self.st.waiting_for_equip = 0, True
        with patch.object(ItemFetch, "shop_for", return_value="Survival"), \
             patch.object(ItemFetch, "available", return_value=True):
            self.assertIsNone(self.ex.item_fetch_target())
            self.st.instance_type = config.INSTANCE_PRIVATE
            self.assertEqual(self.ex.item_fetch_target(), ("Survival", 29))
            self.st.instance_type = config.INSTANCE_PUBLIC
        self.assertEqual(self.ex._fetch_stopped(self.st.round_seq, time.time() + 60), "not_private")
        self.st.instance_type = config.INSTANCE_PRIVATE
        self.assertIsNone(self.ex._fetch_stopped(self.st.round_seq, time.time() + 60))

    def test_the_fetch_mouse_does_not_send_even_when_restoring_the_view(self):
        allowed = {"on": True}
        mouse = ActionExecutor._FrontOnlyMouse(0x10, allowed=lambda: allowed["on"])
        mouse.restoring = True
        with patch.object(WindowOperator, "foreground_hwnd", return_value=0x10):
            mouse.move_rel(0, 6)
            self.assertEqual(self.inputs, ["mouse"])
            allowed["on"] = False
            with self.assertRaises(ItemFetch.Stopped) as cm:
                mouse.move_rel(0, 6)
            self.assertEqual(cm.exception.args[0], "not_private")
            with self.assertRaises(ItemFetch.Stopped):
                mouse.click_at(10, 10)
        self.assertEqual(self.inputs, ["mouse"])

    def test_the_view_left_over_is_kept_not_sent(self):
        self.ex._pending_view_dy = 30
        self.ex._restore_pending_view()
        self.assertEqual(self.ex._pending_view_dy, 30)
        self._nothing_sent()

    def test_the_afk_loop(self):
        st = self.st
        st.in_round, st.is_open_special_round_round = True, True
        ticks = iter(range(1000))

        def sleep(_sec):
            if next(ticks) > config.OPEN_SPECIAL_ROUND_INTERVAL_SEC * 2 + 1:
                st.in_round = False
        with patch.object(ActionExecutor.time, "sleep", side_effect=sleep), \
             patch.object(self.ex, "move") as move:
            self.ex.do_open_special_round_loop()
            self.ex.do_open_special_round_loop(glorbo=True)
        move.assert_not_called()

    def test_bringing_the_window_to_the_front_still_works(self):
        """前面化はどのインスタンスでも今どおり（マウス・OSC は送らない）"""
        self.ex._pending_view_dy = 30
        with patch.object(WindowOperator, "borrow_front", return_value=(True, None)) as borrow, \
             patch.object(SharedState, "keep_front_loan"), patch.object(PlaySound, "play_sound"):
            self.ex._show_item_loss()
            ok, _loan = self.ex._borrow_front()
        self.assertTrue(ok)
        self.assertEqual(borrow.call_count, 2)
        self.assertEqual(self.ex._pending_view_dy, 30)
        self._nothing_sent()

    def test_group_suicide_is_not_affected(self):
        """干し芋・焼き芋の自爆（背面のキー）は今どおり"""
        self.st.instance_type = config.INSTANCE_HOSHIIMO
        self.st.in_round = True
        sent = []
        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda *a, **k: sent.append(a[1]) or True), \
             patch.object(self.ex, "_died_after_skip", return_value=True), \
             patch.object(ActionExecutor.time, "sleep"):
            self.ex.do_skip()
        self.assertTrue(sent)


class TestToNEntryOnlyInPrivate(unittest.TestCase):
    def _entry(self, can_operate=None, logs=None):
        return ToNEntry.ToNEntry(0x1234, osc_port=19990, can_operate=can_operate,
                                 log=(logs.append if logs is not None else None))

    def test_nothing_is_sent_outside_private(self):
        for can_operate in (None, lambda: False):     # 渡されない＝分からない → しない
            logs = []
            entry = self._entry(can_operate, logs)
            with patch.object(entry, "wait_for_panel") as wait, \
                 patch.object(entry._osc, "press") as press, patch.object(entry._osc, "stop_all"), \
                 patch.object(WindowOperator, "focus_window") as focus, \
                 patch.object(WindowOperator, "click") as click:
                self.assertFalse(entry.run())
                self.assertFalse(entry.move("right", 0.4))
                self.assertFalse(entry.click("Begin"))
                self.assertFalse(entry.press_begin())
            wait.assert_not_called()
            press.assert_not_called()
            focus.assert_not_called()
            click.assert_not_called()
            self.assertIn("入室時の自動操作はプライベートのインスタンスだけで行います → しません", logs)

    def test_the_gui_asks_the_log_of_that_window(self):
        joined = {"x.txt": "[Behaviour] Joining wrld_x:1~private(usr_me)~region(jp)",
                  "y.txt": "[Behaviour] Joining wrld_x:1~region(jp)"}
        with tempfile.TemporaryDirectory() as d:
            for name, line in joined.items():
                Path(d, name).write_text(LINE + line + "\n", encoding="utf-8")
            src = Path(mainGUI.__file__).read_text(encoding="utf-8")
            m = re.search(r"can_operate=(lambda p=log_path: .*?\)\)),\n", src, re.S)
            self.assertIsNotNone(m, "入室操作に can_operate を渡す")
            check = eval(m.group(1), {"ActionExecutor": ActionExecutor, "LogMonitor": LogMonitor,
                                      "log_path": ""})
            self.assertTrue(check(str(Path(d, "x.txt"))))
            self.assertFalse(check(str(Path(d, "y.txt"))))
            self.assertFalse(check(""))
            self.assertFalse(check(str(Path(d, "none.txt"))))
