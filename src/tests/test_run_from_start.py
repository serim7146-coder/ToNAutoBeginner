"""Run の後ろへ・前後の直し・Begin 前の移動を「動き出し」から数え、動き出さなければ押し直す（EH）。
本物の OSC・入力は使わない（偽物の OSC・速さ・時計）"""
from tests.support import *  # noqa: F401,F403
import RunFace

F, B, L = "/input/MoveForward", "/input/MoveBackward", "/input/MoveLeft"
_IMG = __import__("numpy").zeros((702, 10, 3), "uint8")     # 偽の撮影（高さだけ使う）


class _World:
    """偽の時計・OSC・速さ。押すたび（_press_counted_from_start の 1 回ごと）に、その押し始めから lag 秒で
    動き出す（None なら動かない・no_data なら速さを受け取れていない）。前後の f は、押していて動いている間
    1 秒あたり rate だけ変わる（前へ増える。worse なら逆）"""

    def __init__(self, test, lag=0.06, no_data=False, f=1.0, rate=0.5, worse=False, lags=None):
        self.now = [0.0]
        self.sent = []
        self.lag, self.lags = lag, list(lags) if lags is not None else None
        self.no_data = no_data
        self.f, self.rate, self.worse = f, rate, worse
        self.call_start = 0.0
        self.held = {}
        self.measures = 0
        self.written = []
        self.saved = []
        self.active = True
        w = self
        fake_time = type(sys)("time_for_test")
        fake_time.time = fake_time.monotonic = lambda: w.now[0]
        fake_time.sleep = lambda sec: w.now.__setitem__(0, w.now[0] + sec)
        for target, name, value in ((ActionExecutor, "time", fake_time),
                                    (DebugLog, "write", self.written.append),
                                    (RunFace, "factor", lambda mark, h: mark[2]),
                                    (RunFace, "save_shot", lambda shot, i, why: self.saved.append(why))):
            p = patch.object(target, name, value)
            p.start()
            test.addCleanup(p.stop)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=3)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x40, osc_port=9000), st,
                                                lambda: True, lambda m: None)
        self.ex._osc = self
        self.ex._receiver = self
        self.ex._run_after_respawn = lambda seq: w.active
        self.ex._measure_face = self.measure
        self.ex._last_face = (_IMG, (433.0, 0.9, f))
        real = self.ex._press_counted_from_start

        def counted(*args, **kwargs):
            w.call_start = w.now[0]
            if w.lags is not None:
                w.lag = w.lags.pop(0) if w.lags else None
            return real(*args, **kwargs)
        self.ex._press_counted_from_start = counted

    # 速さの受信
    @property
    def speed(self):
        if self.no_data:
            return None
        return 4.0 if self._moving() else 0.0

    grounded_or_none = True

    def _moving(self):
        return self.lag is not None and self.now[0] >= self.call_start + self.lag

    # OSC
    def send(self, address, value):
        t = self.now[0]
        self.sent.append((round(t, 2), address, value))
        if value == 1:
            self.held[address] = t
        elif address in self.held:
            since = self.held.pop(address)
            if self.lag is not None and address in (F, B):
                moved = max(0.0, t - max(since, self.call_start + self.lag))
                sign = (1 if address == F else -1) * (-1 if self.worse else 1)
                self.f += sign * self.rate * moved
        return True

    def press(self, address, sec, stop=None):
        self.sent.append(("press", address, round(sec, 3)))
        return True

    def press_multi(self, holds, stop=None):
        self.sent.append(("press_multi", tuple(holds)))
        return True

    def stop_all(self, repeat=1):
        pass

    def measure(self):
        self.measures += 1
        return _IMG, (433.0, 0.9, self.f)

    # 調べる道具
    def events(self, address):
        return [(t, v) for t, a, v in (x for x in self.sent if len(x) == 3 and x[0] != "press") if a == address]

    def released(self, address):
        return [t for t, v in self.events(address) if v == 0][-1]

    def pressed_times(self, address):
        return [t for t, v in self.events(address) if v == 1]


class TestRunBackward(unittest.TestCase):
    def _run(self, w):
        return w.ex._press_counted_from_start(
            [(B, config.RUN_RESPAWN_BACK_SEC)], lambda: not w.active,
            config.BEGIN_USUAL_START_SEC, config.RUN_EXTEND_MAX_SEC, lambda m: w.written.append(m))

    def test_a_start_at_0_75_extends_by_0_67(self):
        """12:09 窓3: 動き出し 0.75 秒 → 0.67 秒延ばす（2.85 ＋ 0.67）。0.6 秒で 1 回押し直している"""
        w = _World(self, lag=0.75)      # 最初の押し始めから 0.75 秒で動き出す
        ok, extension, how, repressed = self._run(w)
        self.assertTrue(ok)
        self.assertAlmostEqual(extension, 0.67, delta=0.011)
        self.assertEqual((how, repressed), ("動き出し", 1))
        self.assertAlmostEqual(w.released(B), 2.85 + 0.67, delta=0.011)

    def test_the_extension_is_capped_at_1_0(self):
        """1 回の押しの中の遅れは 1.0 秒まで（押し直しを待つ長さを延ばして、1.5 秒で動き出す）"""
        w = _World(self, lag=1.5)
        with patch.object(config, "BEGIN_START_WAIT_SEC", 3.0):
            ok, extension, how, repressed = self._run(w)
        self.assertEqual((extension, repressed), (1.0, 0))
        self.assertAlmostEqual(w.released(B), 2.85 + 1.0, delta=0.011)
        self.assertEqual(config.RUN_EXTEND_MAX_SEC, 1.0)

    def test_time_lost_to_represses_is_added(self):
        """2 回押し直した後、2 回目の押し直しから 0.1 秒（最初から 1.5 秒）で動き出した → 延び 1.42
        （上限 1.0 ＋ 押し直しで失った 1.4 秒の中）。動いてからの長さは いつもと同じ 2.77 秒"""
        w = _World(self, lag=1.5)
        ok, extension, how, repressed = self._run(w)
        self.assertAlmostEqual(extension, 1.42, delta=0.011)
        self.assertEqual(repressed, 2)
        self.assertAlmostEqual(w.released(B) - 1.5, 2.85 - 0.08, delta=0.011)

    def test_a_usual_start_is_as_before(self):
        w = _World(self, lag=0.05)
        self._run(w)
        self.assertAlmostEqual(w.released(B), 2.85, delta=0.011)

    def test_the_respawn_flow_counts_from_the_start(self):
        w = _World(self, lag=0.75)
        w.ex._respawn_when_free = lambda seq: None
        w.ex._face_the_stairs = lambda seq: 0
        w.ex._fix_depth = lambda seq: None
        with patch.object(SharedState, "get_run_respawn", lambda: True), \
             patch.object(ActionExecutor.MotionSampler, "start", lambda s: None), \
             patch.object(ActionExecutor.MotionSampler, "release", lambda s: s._write("実測 距離 9.00" + s.suffix)):
            w.ex.do_run_respawn(1)
        self.assertAlmostEqual(w.released(B), 2.85 + 0.67, delta=0.011)
        self.assertIn(("press", "/input/LookLeft", 0.9), w.sent, "左へは今どおり 0.9 秒")
        self.assertIn("[操作] [窓3] Run: 後ろへ: 実測 距離 9.00・動き出しから数えた（+0.67 秒）・押し直し 1 回",
                      w.written)

    def test_the_respawn_flow_caps_at_1_0(self):
        """押し直しを待つ長さを延ばして 1.5 秒で動き出す → Run の上限は 1.0（Begin の 0.5 ではない）"""
        w = _World(self, lag=1.5)
        w.ex._respawn_when_free = lambda seq: None
        w.ex._face_the_stairs = lambda seq: 0
        w.ex._fix_depth = lambda seq: None
        with patch.object(SharedState, "get_run_respawn", lambda: True),              patch.object(config, "BEGIN_START_WAIT_SEC", 3.0),              patch.object(ActionExecutor.MotionSampler, "start", lambda s: None),              patch.object(ActionExecutor.MotionSampler, "release", lambda s: None):
            w.ex.do_run_respawn(1)
        self.assertAlmostEqual(w.released(B), 2.85 + 1.0, delta=0.011)

    def test_no_receiver_is_as_before(self):
        w = _World(self)
        w.ex._receiver = None
        w.ex._respawn_when_free = lambda seq: None
        w.ex._face_the_stairs = lambda seq: 0
        w.ex._fix_depth = lambda seq: None
        with patch.object(SharedState, "get_run_respawn", lambda: True):
            w.ex.do_run_respawn(1)
        self.assertEqual([x for x in w.sent if x[0] == "press"],
                         [("press", B, 2.85), ("press", "/input/LookLeft", 0.9)])

    def test_the_values_stay(self):
        self.assertEqual((config.RUN_RESPAWN_BACK_SEC, config.RUN_RESPAWN_TURN_SEC, config.BEGIN_FORWARD_SEC),
                         (2.85, 0.9, 2.08))
        self.assertEqual((RunFace.SCALES[0], RunFace.SCALES[-1]), (0.85, 1.15))


class TestRepress(unittest.TestCase):
    """0.6 秒動き出さなければ、離して 0.1 秒後に押し直す（最大 2 回）。3 つの動きとも"""

    def test_run_backward(self):
        w = _World(self, lag=None)
        ok, extension, how, repressed = w.ex._press_counted_from_start(
            [(B, 2.85)], lambda: False, config.BEGIN_USUAL_START_SEC, config.RUN_EXTEND_MAX_SEC,
            w.written.append)
        self.assertEqual(w.events(B)[:-1], [(0.0, 0), (0.0, 1), (0.6, 0), (0.7, 1), (1.3, 0), (1.4, 1)])
        self.assertAlmostEqual(w.released(B), 2.85, delta=0.011, msg="押し始めから数えた時間で離す")
        self.assertEqual((extension, how, repressed), (0.0, "動き出さない", 2))
        self.assertIn("動き出さないので押し直した（2 回）・それでも動かない", w.written)

    def test_begin_move(self):
        w = _World(self, lag=None)
        w.ex.move_forward_left(2.08, 0.15)
        self.assertEqual(w.pressed_times(F), [0.0, 0.7, 1.4])
        self.assertEqual(w.pressed_times(L), [0.0, 0.7, 1.4], "左も一緒に押し直す")
        self.assertIn((0.6, F, 0), w.sent)
        self.assertIn("[操作] [窓3] Begin前の移動: 動き出さないので押し直した（2 回）・それでも動かない", w.written)

    def test_begin_move_moving_after_represses_keeps_the_left(self):
        """2 回押し直して 1.5 秒で動き出した → 左も動き出しから 0.07 秒、前は 2.0 秒"""
        w = _World(self, lag=1.5)
        w.ex.move_forward_left(2.08, 0.15)
        self.assertAlmostEqual(w.released(L), 1.5 + 0.07, delta=0.011)
        self.assertAlmostEqual(w.released(F), 1.5 + 2.0, delta=0.011)

    def test_depth_fix(self):
        w = _World(self, lag=None, f=0.91)
        w.ex._depth_press(F, 0.2, 1, "[操作] [窓3] Run: 前後")
        self.assertEqual(w.pressed_times(F), [0.0, 0.7, 1.4])
        self.assertIn("[操作] [窓3] Run: 前後: 動き出さないので押し直した（2 回）・それでも動かない", w.written)

    def test_moving_after_one_repress_stops_repressing(self):
        w = _World(self, lag=0.9)
        w.ex._press_counted_from_start([(B, 2.85)], lambda: False, 0.08, 1.0, w.written.append)
        self.assertEqual(w.pressed_times(B), [0.0, 0.7])

    def test_no_speed_data_never_represses(self):
        w = _World(self, no_data=True)
        ok, extension, how, repressed = w.ex._press_counted_from_start(
            [(B, 2.85)], lambda: False, 0.08, 1.0, w.written.append)
        self.assertEqual(w.pressed_times(B), [0.0])
        self.assertAlmostEqual(w.released(B), 2.85, delta=0.011)
        self.assertEqual((extension, how, repressed), (0.0, "受信なし", 0))

    def test_a_stop_during_the_gap_releases(self):
        w = _World(self, lag=None)
        calls = []

        def stop():
            calls.append(w.now[0])
            return w.now[0] >= 0.65
        ok, _e, how, _n = w.ex._press_counted_from_start([(B, 2.85)], stop, 0.08, 1.0, w.written.append)
        self.assertFalse(ok)
        self.assertEqual(how, "止めた")
        self.assertEqual(w.pressed_times(B), [0.0], "止めたら押し直さない")
        self.assertEqual(w.events(B)[-1][1], 0)

    def test_the_values(self):
        self.assertEqual((config.MOVE_REPRESS_MAX, config.MOVE_REPRESS_GAP_SEC, config.BEGIN_START_WAIT_SEC),
                         (2, 0.1, 0.6))


class TestDepthFromStart(unittest.TestCase):
    def setUp(self):
        # 前後の直しは EJ で止めた。動くときの振る舞いは資料として残す（スイッチを True にして確かめる）
        p = patch.object(config, "RUN_DEPTH_ENABLED", True)
        p.start()
        self.addCleanup(p.stop)

    def test_a_press_is_counted_from_the_start(self):
        """0.2 秒は動き出してから 0.2 秒（動き出し 0.3 秒 → 0.5 秒で離す）"""
        w = _World(self, lag=0.3)
        self.assertIs(w.ex._depth_press(F, 0.2, 1, "h"), True)
        self.assertAlmostEqual(w.released(F), 0.5, delta=0.011)
        self.assertIn("h: /input/MoveForward・動き出しから数えた（+0.30 秒）", w.written)

    def test_not_moving_is_not_judged_and_pressed_again(self):
        """12:09 窓3: 0.2 秒では動き出さず、0.91 → 0.87（ぶれ）で戻していた。動かなかった押しでは判断しない"""
        w = _World(self, f=0.91, lags=[None, 0.05])
        w.ex._fix_depth(1)
        self.assertNotIn("depth_reverted", w.saved)
        self.assertFalse(any("戻して止める" in str(m) for m in w.written))
        self.assertIn("[操作] [窓3] Run: 前後: 動き出さなかった → 遠のいた・近づいたを判断しない（戻さない）",
                      w.written)
        backward = [x for x in w.sent if len(x) == 3 and x[1] == B]
        self.assertEqual(backward, [], "戻さない")
        self.assertEqual(w.measures, 1, "動かなかった押しの後は撮り直さない（動いた後の 1 回だけ）")
        self.assertGreater(w.f, 0.91, "2 回目は動いて近づいた")

    def test_moving_and_getting_worse_by_0_02_goes_back(self):
        w = _World(self, f=0.91, worse=True, rate=0.1, lag=0.05)    # 0.2 秒で 0.02 遠のく
        w.ex._fix_depth(1)
        self.assertEqual(w.saved, ["depth_reverted"])
        self.assertEqual([a for t, a, v in (x for x in w.sent if len(x) == 3) if v == 1], [F, B])

    def test_moving_and_getting_worse_by_0_01_does_not_go_back(self):
        w = _World(self, f=0.91, worse=True, rate=0.05, lag=0.05)   # 0.2 秒で 0.01 遠のく（ぶれ）
        w.ex._fix_depth(1)
        self.assertNotIn("depth_reverted", w.saved)
        self.assertNotIn(B, [a for t, a, v in (x for x in w.sent if len(x) == 3) if v == 1])

    def test_without_a_receiver_the_judgement_stays_but_with_0_02(self):
        w = _World(self, f=0.91)
        w.ex._receiver = None
        w.ex._fix_depth(1)
        self.assertEqual(w.sent[0], ("press", F, 0.2), "受信が無い窓は今どおりの押し方")

    def test_the_worse_value(self):
        self.assertEqual(config.RUN_DEPTH_WORSE, 0.02)


class TestTheDelayLine(unittest.TestCase):
    def test_the_delay_from_the_usual_line_is_written(self):
        w = _World(self, lag=0.05)
        w.ex._st.round_type = "Classic"
        def slow_start(sampler):        # 押し始めるまでに 7.28 秒かかった（12:23 窓4）。測るスレッドは立てない
            w.now[0] += 7.28
        with patch.object(ActionExecutor.MotionSampler, "start", slow_start), \
             patch.object(ActionExecutor.MotionSampler, "release", lambda s: None):
            w.ex._begin_move()
        line = next(m for m in w.written if "前進 2.08秒＋左 0.15秒（OSC）" in str(m))
        self.assertTrue(line.endswith("（OSC）・「通常」の行から 7.28 秒"), line)
        self.assertIsNone(w.ex._move_since, "使ったら消す")

    def test_other_callers_have_no_delay(self):
        w = _World(self, lag=0.05)
        w.ex.move_forward_left(2.08, 0.15)
        line = next(m for m in w.written if "前進 2.08秒＋左 0.15秒（OSC）" in str(m))
        self.assertTrue(line.endswith("（OSC）"), line)

    def test_the_mark_is_used_once(self):
        w = _World(self, lag=0.05)
        w.ex._move_since = ("通常", 0.0)
        w.ex.move_forward_left(2.08, 0.15)
        w.ex.move_forward_left(2.08, 0.15)
        lines = [m for m in w.written if "前進 2.08秒＋左 0.15秒（OSC）" in str(m)]
        self.assertIn("・「通常」の行から 0.00 秒", lines[0])
        self.assertTrue(lines[1].endswith("（OSC）"), lines[1])
