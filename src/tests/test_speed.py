"""速度によるラウンド種別の先読み"""
from tests.support import *  # noqa: F401,F403




class TestHandsFreeSpeedDetect(unittest.TestCase):
    """完全放置モードが効く窓（private）では速度検知を始めない。効かない窓は今のまま"""

    def setUp(self):
        SharedState.set_speed_detect(True)
        self.addCleanup(SharedState.set_speed_detect, config.SPEED_DETECT_ENABLED)
        self.addCleanup(SharedState.set_hands_free, False)

    def _monitor(self, instance_type):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9000), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.round_end_seen = True
        monitor._verified.on_round_end_verified(0)
        monitor.st.last_begin_press_at = time.time()   # ツールが押した直後（受理されるのはこのときだけ）
        return monitor

    def _started(self, call):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            call()
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_the_probe(self):
        for hands_free, itype, expected in ((True, config.INSTANCE_PRIVATE, False),
                                            (True, config.INSTANCE_PUBLIC, True),
                                            (False, config.INSTANCE_PRIVATE, True)):
            SharedState.set_hands_free(hands_free)
            monitor = self._monitor(itype)
            started = self._started(monitor._start_speed_probe)
            self.assertEqual("do_speed_detect" in started, expected, (hands_free, itype))

    def test_the_strafe(self):
        for hands_free, expected in ((True, False), (False, True)):
            SharedState.set_hands_free(hands_free)
            monitor = self._monitor(config.INSTANCE_PRIVATE)
            started = self._started(lambda: monitor._process("Verified"))
            self.assertEqual("do_speed_strafe" in started, expected, hands_free)

    def _executor(self, itype):
        st = WindowState(instance_type=itype)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=123, osc_port=9000), st,
                                           lambda: True, lambda _m: None)
        ex._speed_ready.set()
        return ex, st

    def test_the_executor_does_nothing_in_hands_free(self):
        SharedState.set_hands_free(True)
        ex, _st = self._executor(config.INSTANCE_PRIVATE)
        ex._receiver = MagicMock()
        with patch.object(ex, "move") as move, patch.object(ex, "_sample_speed") as sample, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_speed_strafe()
            ex.do_speed_detect()
        move.assert_not_called()
        sample.assert_not_called()

    def test_the_executor_still_works_in_public(self):
        SharedState.set_hands_free(True)
        ex, st = self._executor(config.INSTANCE_PUBLIC)
        receiver = MagicMock()
        ex._receiver = receiver
        calls = []

        def sample(_r):
            calls.append(1)
            st.in_round = True                  # 1回で抜ける

        with patch.object(ex, "move") as move, patch.object(ex, "_sample_speed", side_effect=sample), \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_speed_strafe()
            ex.do_speed_detect()
        move.assert_not_called()                # 横移動（OSC）は private だけ（DF）。判定は今のまま
        self.assertEqual(calls, [1])




class TestSpeedDetectNeedsOsc(unittest.TestCase):
    """速度を受け取れるのは OSC の窓だけ。

    キー操作の窓は Verified Round End を待ってから Begin 前に歩くので、検知の
    最中に歩きが入るのではと心配された。そもそも受信を開かないので検知しない。
    """

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def test_a_key_window_opens_no_receiver(self):
        cfg = WindowConfig(hwnd=123, osc_port=0)
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(), lambda: True,
                                           lambda _m: None)

        with patch.object(ActionExecutor.OSCReceiver, "VelocityReceiver") as made:
            self.assertFalse(ex.start_velocity_receiver())

        made.assert_not_called()
        self.assertIsNone(ex._receiver)

    def test_a_key_window_does_not_sample_at_all(self):
        cfg = WindowConfig(hwnd=123, osc_port=0)
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(), lambda: True,
                                           lambda _m: None)
        ex.start_velocity_receiver()

        with patch.object(ex, "_sample_speed") as sample, \
             patch.object(ActionExecutor.time, "sleep") as nap:
            ex.do_speed_detect()

        sample.assert_not_called()
        nap.assert_not_called()

    def test_ordinary_walking_values_are_not_a_round_kind(self):
        """判定は定数との一致（±0.01）だけ。途中の値では決めない"""
        for speed in (0.0, 2.0, 3.99 - 0.02, 5.0, 6.45, 6.55, 7.0):
            self.assertEqual(ActionExecutor.classify_speed(speed), "", speed)




class TestSpeedDetectRunsUntilTheRound(unittest.TestCase):
    """速度検知は時間ではなくラウンド突入で止める"""

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _run(self, on_tick, advance=1.0):
        cfg = WindowConfig(hwnd=123, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
        # 値を返さない受信（判定では止まらない）
        ex._receiver = type("R", (), dict(stable_value=None, stable_for=0.0,
                                          grounded=True, ever_received=True,
                                          alive=True))()
        clock = {"t": 1000.0}

        def sleep(_sec):
            clock["t"] += advance
            on_tick(st, clock["t"] - 1000.0)

        with patch.object(ActionExecutor.time, "time",
                          side_effect=lambda: clock["t"]), \
             patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
            ex.do_speed_detect()
        return clock["t"] - 1000.0

    def test_it_stops_when_the_round_starts(self):
        def tick(st, elapsed):
            if elapsed >= 12:
                st.in_round = True

        self.assertEqual(self._run(tick), 12)

    def test_it_keeps_watching_past_twenty_seconds(self):
        """Begin から開始まで20秒を超えることがある（実測1.5%）"""
        def tick(st, elapsed):
            if elapsed >= 45:
                st.in_round = True

        self.assertEqual(self._run(tick), 45)

    def test_the_safety_limit_stops_it(self):
        """ラウンドが来ないまま回り続けない"""
        elapsed = self._run(lambda st, e: None)

        self.assertGreaterEqual(elapsed, config.SPEED_PROBE_MAX_SEC)
        self.assertLessEqual(elapsed, config.SPEED_PROBE_MAX_SEC + 1)

    def test_the_limit_is_longer_than_the_longest_wait_seen(self):
        """実ログの最大は 119 秒"""
        self.assertGreater(config.SPEED_PROBE_MAX_SEC, 119)

    def test_changing_instance_stops_it(self):
        def tick(st, elapsed):
            if elapsed >= 5:
                st.instance_seq += 1

        self.assertEqual(self._run(tick), 5)

    def test_the_old_timeout_is_gone(self):
        self.assertFalse(hasattr(config, "SPEED_PROBE_TIMEOUT_SEC"))




class TestSpeedClassify(unittest.TestCase):
    """速度からラウンド種別を引く（値の照合だけ。張り付き判定は受信側）"""

    def test_normal_speed(self):
        self.assertEqual(ActionExecutor.classify_speed(6.6), "normal")

    def test_eight_pages_speed(self):
        self.assertEqual(ActionExecutor.classify_speed(6.5), "8pages")

    def test_punish_speed(self):
        self.assertEqual(ActionExecutor.classify_speed(4.0), "punish")

    def test_in_between_value_is_unknown(self):
        """帯で判定していないことの回帰: 6.52 はどれでもない"""
        self.assertEqual(ActionExecutor.classify_speed(6.52), "")

    def test_far_value_is_unknown(self):
        self.assertEqual(ActionExecutor.classify_speed(0.0), "")

    def test_small_jitter_still_matches(self):
        """一致の許容内なら拾う"""
        self.assertEqual(
            ActionExecutor.classify_speed(6.6 + config.SPEED_MATCH_TOL / 2), "normal")




class TestVelocityStability(unittest.TestCase):
    """張り付き（値が変化していない時間）は受信側が持つ"""

    def _receiver(self):
        return OSCReceiver.VelocityReceiver(19999)

    def _feed(self, r, value, at):
        """指定時刻にVelocityMagnitudeを受信したことにする"""
        with patch.object(OSCReceiver.time, "time", return_value=at):
            r._handle(OSCReceiver.VELOCITY_MAGNITUDE, value)

    def test_unreceived_is_none_and_zero(self):
        r = self._receiver()

        self.assertIsNone(r.stable_value)
        self.assertEqual(r.stable_for, 0.0)

    def test_stable_for_grows_while_the_value_holds(self):
        r = self._receiver()
        self._feed(r, 6.6, 1000.0)

        with patch.object(OSCReceiver.time, "time", return_value=1000.5):
            self.assertAlmostEqual(r.stable_for, 0.5)
            self.assertEqual(r.stable_value, 6.6)

    def test_jitter_within_tolerance_does_not_reset(self):
        """許容内の揺らぎで _stable_since を戻さない（戻すと永久に張り付かない）"""
        r = self._receiver()
        self._feed(r, 6.6, 1000.0)
        self._feed(r, 6.6 + config.SPEED_STICK_TOL / 2, 1000.2)

        with patch.object(OSCReceiver.time, "time", return_value=1000.4):
            self.assertAlmostEqual(r.stable_for, 0.4, places=6)
            self.assertEqual(r.stable_value, 6.6, "基準値は変えない")

    def test_change_beyond_tolerance_resets(self):
        r = self._receiver()
        self._feed(r, 6.6, 1000.0)
        self._feed(r, 4.0, 1000.3)

        with patch.object(OSCReceiver.time, "time", return_value=1000.31):
            self.assertEqual(r.stable_value, 4.0)
            self.assertAlmostEqual(r.stable_for, 0.01, places=6)

    def test_speed_and_ever_received_still_track_every_packet(self):
        r = self._receiver()
        self.assertFalse(r.ever_received)

        self._feed(r, 6.6, 1000.0)

        self.assertTrue(r.ever_received)
        self.assertEqual(r.speed, 6.6)




class TestSpeedDetect(unittest.TestCase):
    """判定モジュール: 受信するだけ。どのインスタンスでも動く"""

    def setUp(self):
        SharedState.set_speed_detect(True)
        SharedState.set_hands_free(False)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)
        SharedState.set_hands_free(False)

    class FakeReceiver:
        def __init__(self, value=None, stable_for=1.0, grounded=True,
                     ever_received=True, alive=True):
            self.stable_value = value
            self.stable_for = stable_for
            self.grounded = grounded
            self.ever_received = ever_received
            self.alive = alive
            self.stopped = False

        def stop(self):
            self.stopped = True

    def _executor(self, instance_type=None):
        cfg = WindowConfig(hwnd=123, osc_port=9000,
                           voice_8pages="8p.mp3", voice_punish="pn.mp3")
        st = WindowState(instance_type=instance_type or config.INSTANCE_PRIVATE)
        logs = []
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append), st, logs

    def _run(self, receiver, instance_type=None):
        ex, st, logs = self._executor(instance_type)
        ex._receiver = receiver
        calls = {"n": 0}

        def stop_after_one(_sec):
            calls["n"] += 1
            st.in_round = True      # 1周で打ち切る

        with patch.object(ActionExecutor.time, "sleep", side_effect=stop_after_one), \
             patch.object(ActionExecutor.PlaySound, "play_sound") as mock_play:
            ex.do_speed_detect()
        return st, logs, [c.args[0] for c in mock_play.call_args_list]

    def test_stable_eight_pages_is_announced(self):
        st, _logs, played = self._run(self.FakeReceiver(6.5))

        self.assertEqual(st.speed_round_kind, "8pages")
        self.assertEqual(played, ["8p.mp3"])

    def test_stable_punish_is_announced(self):
        st, _logs, played = self._run(self.FakeReceiver(4.0))

        self.assertEqual(st.speed_round_kind, "punish")
        self.assertEqual(played, ["pn.mp3"])

    def test_normal_is_not_announced(self):
        st, _logs, played = self._run(self.FakeReceiver(6.6))

        self.assertEqual(st.speed_round_kind, "normal")
        self.assertEqual(played, [])

    def test_not_stable_long_enough_is_ignored(self):
        """SPEED_STABLE_SEC に満たないうちは判定しない"""
        st, _logs, played = self._run(
            self.FakeReceiver(6.5, stable_for=config.SPEED_STABLE_SEC / 2))

        self.assertEqual(st.speed_round_kind, "")
        self.assertEqual(played, [])

    def test_stale_value_after_the_stream_dies_is_ignored(self):
        """途絶後は判定しない。

        stable_for は時間経過だけで伸びるので、送信が止まると凍結値が
        いつまでも「安定値」として通ってしまう。
        """
        st, _logs, played = self._run(
            self.FakeReceiver(4.0, stable_for=99.0, alive=False))

        self.assertEqual(st.speed_round_kind, "", "凍結値で判定してはいけない")
        self.assertEqual(played, [])

    def test_live_stream_still_decides(self):
        """受信が生きていれば従来どおり判定する（ガードで塞ぎすぎていない）"""
        st, _logs, played = self._run(
            self.FakeReceiver(4.0, stable_for=config.SPEED_STABLE_SEC, alive=True))

        self.assertEqual(st.speed_round_kind, "punish")
        self.assertEqual(played, ["pn.mp3"])

    def test_airborne_samples_are_ignored(self):
        st, _logs, played = self._run(self.FakeReceiver(6.5, grounded=False))

        self.assertEqual(st.speed_round_kind, "")
        self.assertEqual(played, [])

    def test_unreceived_is_ignored(self):
        st, _logs, played = self._run(self.FakeReceiver(None, ever_received=False))

        self.assertEqual(st.speed_round_kind, "")
        self.assertTrue(any("速度を受信できない" in m for m in _logs))

    def test_detect_runs_in_any_instance(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_PUBLIC):
            st, _logs, played = self._run(self.FakeReceiver(6.5), instance_type=itype)
            self.assertEqual(st.speed_round_kind, "8pages", itype)

    def test_hands_free_does_not_detect(self):
        """完全放置モードが効く窓では速度検知を動かさない（判定・フリーズ・音声のどれも）"""
        SharedState.set_hands_free(True)
        st, _logs, played = self._run(self.FakeReceiver(6.5))

        self.assertEqual(st.speed_round_kind, "", "判定もしない")
        self.assertFalse(st.speed_freeze_held)
        self.assertEqual(played, [])

    def test_toggle_off_does_nothing(self):
        SharedState.set_speed_detect(False)
        ex, st, _logs = self._executor()
        ex._receiver = self.FakeReceiver(6.5)

        with patch.object(ActionExecutor.time, "sleep"):
            ex.do_speed_detect()

        self.assertEqual(st.speed_round_kind, "")

    def test_without_a_receiver_it_does_nothing(self):
        ex, st, _logs = self._executor()
        ex._receiver = None

        with patch.object(ActionExecutor.time, "sleep"):
            ex.do_speed_detect()

        self.assertEqual(st.speed_round_kind, "")

    def test_detect_does_not_stop_the_shared_receiver(self):
        """常時監視なのでプローブ終了で止めない"""
        receiver = self.FakeReceiver(6.6)
        self._run(receiver)

        self.assertFalse(receiver.stopped)




class TestVelocityReceiverLifecycle(unittest.TestCase):
    """受信器は監視の開始から停止まで生かす"""

    def _executor(self, osc_port=9000):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(), lambda: True, logs.append)
        return ex, logs

    class FakeReceiver:
        def __init__(self, port, log=None, on_param=None):
            self.port = port
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True
            return True

        def stop(self):
            self.stopped = True

    def test_monitor_start_and_stop_control_the_receiver(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9010), {},
                                        lambda _m: None, window_idx=1)

        with patch.object(OSCReceiver, "VelocityReceiver", self.FakeReceiver), \
             patch.object(LogMonitor.threading, "Thread"):
            monitor.start()
            receiver = monitor._action._receiver
            self.assertIsNotNone(receiver, "監視開始で受信を始めること")
            self.assertTrue(receiver.started)
            self.assertEqual(receiver.port, 9011, "VRChatが送ってくる側のポート")

            monitor.stop()

        self.assertTrue(receiver.stopped, "監視停止で止めること")
        self.assertIsNone(monitor._action._receiver)

    def test_start_is_idempotent(self):
        ex, _logs = self._executor()

        with patch.object(OSCReceiver, "VelocityReceiver", self.FakeReceiver):
            self.assertTrue(ex.start_velocity_receiver())
            first = ex._receiver
            self.assertTrue(ex.start_velocity_receiver())

        self.assertIs(ex._receiver, first, "二重にbindしない")

    def test_no_osc_port_is_harmless(self):
        ex, logs = self._executor(osc_port=0)

        with patch.object(OSCReceiver, "VelocityReceiver") as mock_recv:
            self.assertFalse(ex.start_velocity_receiver())

        mock_recv.assert_not_called()
        self.assertIsNone(ex._receiver)
        self.assertTrue(ex._speed_ready.is_set(), "横移動を待たせないこと")

    def test_bind_failure_is_reported_once_and_harmless(self):
        ex, logs = self._executor()

        class DeadReceiver:
            def __init__(self, port, log=None, on_param=None):
                pass

            def start(self):
                return False

        with patch.object(OSCReceiver, "VelocityReceiver", DeadReceiver):
            self.assertFalse(ex.start_velocity_receiver())

        self.assertIsNone(ex._receiver)
        self.assertTrue(ex._speed_ready.is_set())
        self.assertEqual(len([m for m in logs if "速度受信を開始できない" in m]), 1)

    def test_stop_without_start_is_safe(self):
        ex, _logs = self._executor()

        ex.stop_velocity_receiver()

        self.assertIsNone(ex._receiver)




class TestSpeedStrafe(unittest.TestCase):
    """横移動モジュール: マクロなのでprivateのみ（起動側で判定）"""

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _executor(self, osc_port=9000, **st_kw):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, **st_kw)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        ex._speed_ready.set()   # 受信の準備は済んでいる前提
        return ex, st, logs

    def test_moves_right_once_then_left_once(self):
        """右 → 左 の1往復だけ。繰り返さない"""
        ex, _st, _logs = self._executor()
        moves = []

        with patch.object(ex, "move", side_effect=lambda d, sec: moves.append((d, sec))):
            ex.do_speed_strafe()

        self.assertEqual(moves, [("right", config.SPEED_PROBE_RIGHT_SEC),
                                 ("left", config.SPEED_PROBE_LEFT_SEC)])

    def test_item_lost_round_does_not_move(self):
        ex, _st, logs = self._executor(waiting_for_equip=True)

        with patch.object(ex, "move") as mock_move:
            ex.do_speed_strafe()

        mock_move.assert_not_called()
        self.assertTrue(any("横移動はしません" in m for m in logs))

    def test_toggle_off_stops_the_strafe(self):
        SharedState.set_speed_detect(False)
        ex, _st, _logs = self._executor()

        with patch.object(ex, "move") as mock_move:
            ex.do_speed_strafe()

        mock_move.assert_not_called()

    def test_no_osc_port_stops_the_strafe(self):
        ex, _st, _logs = self._executor(osc_port=0)

        with patch.object(ex, "move") as mock_move:
            ex.do_speed_strafe()

        mock_move.assert_not_called()




class TestSpeedProbeOrder(unittest.TestCase):
    """横移動は受信のbindが終わってから始める

    VRChatは値が変わったときしか送らないので、bind前に動き出すと立ち上がりの
    サンプルを永久に取りこぼす。
    """

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _executor(self, osc_port=9000):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        return ex, st, logs

    def test_strafe_waits_until_the_receiver_is_ready(self):
        """準備できるまで move を呼ばない"""
        ex, _st, _logs = self._executor()
        moves = []
        done = threading.Event()

        def worker():
            with patch.object(ex, "move", side_effect=lambda d, sec: moves.append(d)):
                ex.do_speed_strafe()
            done.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.2)
        self.assertEqual(moves, [], "準備前に動いてはいけない")

        ex._speed_ready.set()

        self.assertTrue(done.wait(2.0))
        self.assertEqual(moves, ["right", "left"])

    def test_starting_the_receiver_makes_it_ready(self):
        """準備完了を伝えるのは受信の開始（監視開始時に1回）"""
        ex, _st, _logs = self._executor()

        class FakeReceiver:
            def __init__(self, port, log=None, on_param=None):
                pass

            def start(self):
                return True

            def stop(self):
                pass

        self.assertFalse(ex._speed_ready.is_set())
        with patch.object(OSCReceiver, "VelocityReceiver", FakeReceiver):
            ex.start_velocity_receiver()

        self.assertTrue(ex._speed_ready.is_set())

        ex.stop_velocity_receiver()
        self.assertFalse(ex._speed_ready.is_set(), "停止したら待ちに戻す")

    def test_strafe_moves_anyway_after_the_timeout(self):
        """待ち切れなくても移動する（受信が使えなくても横移動は他を壊さない）"""
        ex, _st, logs = self._executor()
        moves = []

        with patch.object(config, "SPEED_READY_TIMEOUT_SEC", 0.05), \
             patch.object(ex, "move", side_effect=lambda d, sec: moves.append(d)):
            ex.do_speed_strafe()

        self.assertEqual(moves, ["right", "left"], "止まってはいけない")
        self.assertTrue(any("準備を待てませんでした" in m for m in logs))
