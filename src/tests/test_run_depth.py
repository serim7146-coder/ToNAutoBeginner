"""Run のリスポーンの後、前後の位置を目印の大きさで直す（EE・技術選定の決定 B）。本物の OSC・入力は使わない"""
from tests.support import *  # noqa: F401,F403
import RunFace

DATA = Path(__file__).parent / "data"
_IMG = __import__("numpy").zeros((702, 10, 3), "uint8")     # 偽の撮影（高さだけ使う）


def _far_shot():
    """2026-10-12 07:25 窓2 の notfound（1/2 の JPEG）の上 46% を元の大きさに戻した撮影。右の看板は消してある"""
    import cv2
    import numpy as np
    keep = cv2.imdecode(np.fromfile(str(DATA / "run_face" / "far_0p8_top.png"), np.uint8), cv2.IMREAD_COLOR)
    full = np.zeros((704, 868, 3), np.uint8)
    big = cv2.resize(keep, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    full[:big.shape[0], :big.shape[1]] = big
    return full


def _strip_shot(name):
    import cv2
    import numpy as np
    strip = cv2.imdecode(np.fromfile(str(DATA / "run_respawn" / name), np.uint8), cv2.IMREAD_COLOR)
    img = np.zeros((702, 867, 3), np.uint8)
    img[56:315] = strip
    return img


class TestTheMarkSize(unittest.TestCase):
    def test_the_range_stays(self):
        self.assertEqual((RunFace.SCALES[0], RunFace.SCALES[-1]), (0.85, 1.15), "範囲は広げない（決定 B）")

    def test_the_far_shot_is_far(self):
        shot = _far_shot()
        mark = RunFace.find_mark(shot)
        self.assertIsNotNone(mark, "0.85 の端で見つかる")
        f = RunFace.factor(mark, shot.shape[0])
        self.assertAlmostEqual(f, 0.85, delta=0.02)
        self.assertLess(f, 1.0 - config.RUN_DEPTH_TOL, "遠い → 前へ")

    def test_the_fine_search_stays_in_the_range(self):
        shot = _strip_shot("face_fix2_093431_867x702_rows_56_315.png")
        self.assertIsNone(RunFace.find_mark(shot), "端の撮影は今までどおり見つからない（範囲の外の偽物を拾わない）")
        far = RunFace.find_mark(_far_shot())
        self.assertGreaterEqual(RunFace.factor(far, 704), 0.85 - 1e-9, "細かい探し直しも範囲の中だけ")

    def test_the_front_shot_is_one(self):
        shot = _strip_shot("face_now_867x702_rows_56_315.png")
        self.assertAlmostEqual(RunFace.factor(RunFace.find_mark(shot), 702), 1.0, delta=0.011)

    def test_the_fine_search_gives_the_hundredths(self):
        """正面の撮影を 0.93 倍に縮めた → 0.05 刻みでは 0.95・0.9 だが、細かく探し直して 0.93"""
        import cv2
        import numpy as np
        shot = _strip_shot("face_now_867x702_rows_56_315.png")
        small = cv2.resize(shot, None, fx=0.93, fy=0.93, interpolation=cv2.INTER_AREA)
        frame = np.zeros_like(shot)
        y0, x0 = (shot.shape[0] - small.shape[0]) // 2, (shot.shape[1] - small.shape[1]) // 2
        frame[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
        mark = RunFace.find_mark(frame)
        self.assertAlmostEqual(RunFace.factor(mark, frame.shape[0]), 0.93, delta=0.011)   # 0.90・0.95 ではない

    def test_the_factor_is_for_any_window_height(self):
        """撮影の高さが違っても（1294×1399 相当に広げた正面）、いつもの位置は 1.0"""
        import cv2
        shot = _strip_shot("face_now_867x702_rows_56_315.png")
        k = 1399 / 702
        big = cv2.resize(shot, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR)
        mark = RunFace.find_mark(big)
        self.assertAlmostEqual(mark[2], k, delta=0.05, msg="find_mark の倍率は高さを掛けたもの")
        self.assertAlmostEqual(RunFace.factor(mark, big.shape[0]), 1.0, delta=0.011)

    def test_best_anywhere_is_only_a_record(self):
        shot = _far_shot()
        wide = RunFace.best_anywhere(shot)
        self.assertAlmostEqual(RunFace.factor(wide, shot.shape[0]), 0.80, delta=0.03)
        self.assertEqual(RunFace.WIDE_SCALES[0], 0.7)
        self.assertEqual(RunFace.WIDE_SCALES[-1], 1.3)


class _World:
    """偽の前後: 前へ 1 秒で f が rate 増える（後ろへは減る）。測るたびに今の f を返す"""

    def __init__(self, test, f, rate=0.5, lose_after=None, worse=False):
        self.f, self.rate = f, rate
        self.presses = []
        self.measures = 0
        self.lose_after = lose_after
        self.worse = worse
        self.saved = []
        self.written = []
        self.active = True
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=2)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: True, lambda m: None)
        ex._osc = self
        ex._run_after_respawn = lambda seq: self.active
        ex._measure_face = self.measure
        ex._last_face = (_IMG, (433.0, 0.9, self.f))
        self.ex = ex
        for target, name, value in ((RunFace, "factor", lambda mark, h: mark[2]),
                                    (RunFace, "save_shot", lambda shot, w, why: self.saved.append(why)),
                                    (DebugLog, "write", self.written.append)):
            p = patch.object(target, name, value)
            p.start()
            test.addCleanup(p.stop)

    def press(self, address, sec, stop=None):
        self.presses.append((address, round(sec, 3)))
        sign = 1 if address == "/input/MoveForward" else -1
        if self.worse:
            sign = -sign
        self.f += sign * self.rate * sec
        return True

    def stop_all(self, repeat=1):
        pass

    def measure(self):
        self.measures += 1
        if self.lose_after is not None and self.measures >= self.lose_after:
            return _IMG, None
        return _IMG, (433.0, 0.9, self.f)


class TestFixDepth(unittest.TestCase):
    def setUp(self):
        # 前後の直しは EJ で止めた。動くときの振る舞いは資料として残す（スイッチを True にして確かめる）
        p = patch.object(config, "RUN_DEPTH_ENABLED", True)
        p.start()
        self.addCleanup(p.stop)

    def test_far_moves_forward_and_learns_the_rate(self):
        w = _World(self, f=0.85, rate=0.5)
        got = w.ex._fix_depth(1)
        self.assertEqual(w.presses[0], ("/input/MoveForward", 0.2), "最初は 0.2 秒")
        # 0.2 秒で 0.10 → 0.5/秒。残り 0.05 → 0.1 秒
        self.assertEqual(w.presses[1], ("/input/MoveForward", 0.1))
        self.assertLessEqual(abs(w.f - 1.0), config.RUN_DEPTH_TOL)
        self.assertEqual(got, "遠い → 前へ 0.3 秒")
        self.assertEqual(w.saved, ["depth"], "直した後の撮影も残す")

    def test_near_moves_backward(self):
        w = _World(self, f=1.12, rate=0.5)
        self.assertEqual(w.ex._fix_depth(1), "近い → 後ろへ 0.2 秒")
        self.assertEqual(w.presses, [("/input/MoveBackward", 0.2)])

    def test_inside_the_tolerance_does_not_move(self):
        for f in (0.96, 1.0, 1.04):
            w = _World(self, f=f)
            self.assertIsNone(w.ex._fix_depth(1))
            self.assertEqual(w.presses, [], f)

    def test_the_length_is_clamped(self):
        w = _World(self, f=0.85, rate=0.05)          # ほとんど変わらない → 長く（上限 0.6）
        w.ex._fix_depth(1)
        self.assertEqual(w.presses[1][1], config.RUN_DEPTH_MAX_SEC)
        w = _World(self, f=0.70, rate=10.0)          # 0.2 秒で行き過ぎない程度に大きく変わる
        w.f = 0.70
        w.ex._last_face = (_IMG, (433.0, 0.9, 0.70))
        w.rate = 1.1                                  # 0.2 秒で 0.92 → 残り 0.08 / 1.1 = 0.073
        w.ex._fix_depth(1)
        self.assertGreaterEqual(min(sec for _a, sec in w.presses), config.RUN_DEPTH_MIN_SEC)

    def test_getting_worse_goes_back_and_stops(self):
        w = _World(self, f=0.85, worse=True)
        self.assertIsNone(w.ex._fix_depth(1), "戻したので残していない")
        self.assertEqual(w.presses, [("/input/MoveForward", 0.2), ("/input/MoveBackward", 0.2)])
        self.assertEqual(w.saved, ["depth_reverted"])

    def test_a_lost_mark_goes_back(self):
        w = _World(self, f=0.85, lose_after=1)
        w.ex._fix_depth(1)
        self.assertEqual(w.presses, [("/input/MoveForward", 0.2), ("/input/MoveBackward", 0.2)])

    def test_at_most_four(self):
        w = _World(self, f=0.60, rate=0.05)
        w.ex._fix_depth(1)
        self.assertEqual(len(w.presses), config.RUN_DEPTH_TRIES)
        self.assertEqual(config.RUN_DEPTH_TRIES, 4)

    def test_stopped_or_round_changed_does_not_move(self):
        w = _World(self, f=0.85)
        w.active = False
        self.assertIsNone(w.ex._fix_depth(1))
        self.assertEqual(w.presses, [])

    def test_stops_between_moves(self):
        w = _World(self, f=0.60, rate=0.05)
        press = w.press

        def once(address, sec, stop=None):
            w.active = False
            return press(address, sec, stop)
        w.press = once
        w.ex._osc = w
        w.ex._fix_depth(1)
        self.assertEqual(len(w.presses), 1)

    def test_no_mark_does_not_move(self):
        w = _World(self, f=0.85)
        w.ex._last_face = (_IMG, None)
        self.assertIsNone(w.ex._fix_depth(1))
        self.assertEqual(w.presses, [])

    def test_it_uses_the_shot_of_the_facing_fix(self):
        w = _World(self, f=0.97)
        w.ex._fix_depth(1)
        self.assertEqual(w.measures, 0, "向きを直し終えた撮影を使い、撮り直さない")


class TestTheRespawnFlow(unittest.TestCase):
    def _ex(self, receiver=True):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=2)
        self.logs = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: True,
                                           self.logs.append)
        ex._osc = MagicMock()
        ex._receiver = MagicMock(speed=1.0, grounded_or_none=True) if receiver else None
        ex._capture_bgr = lambda: None      # 本物の撮影は使わない（EQ: Run の後に 1 枚撮る）
        ex._respawn_when_free = lambda seq: None
        ex._run_after_respawn = lambda seq: True
        self.written = []
        # 後ろへは動き出しから数える（EH）。本物の時計で 2.85 秒待たない
        now = [0.0]
        fake_time = type(sys)("time_for_test")
        fake_time.time = fake_time.monotonic = lambda: now[0]
        fake_time.sleep = lambda sec: now.__setitem__(0, now[0] + sec)
        for target, name, value in ((DebugLog, "write", self.written.append),
                                    (ActionExecutor, "time", fake_time),
                                    (SharedState, "get_run_respawn", lambda: True),
                                    (ActionExecutor.MotionSampler, "start", lambda s: self.written.append("start")),
                                    (ActionExecutor.MotionSampler, "release",
                                     lambda s: s._write("実測 距離 1.00"))):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        return ex

    def test_the_backward_move_is_measured(self):
        ex = self._ex()
        ex._face_the_stairs = lambda seq: 0
        ex._fix_depth = lambda seq: None
        ex.do_run_respawn(1)
        self.assertIn("[操作] [窓2] Run: 後ろへ: 実測 距離 1.00", self.written)
        self.assertEqual(self.written.count("start"), 1, "後ろへだけ（左へは測らない）")

    def test_no_receiver_writes_nothing(self):
        ex = self._ex(receiver=False)
        ex._face_the_stairs = lambda seq: 0
        ex._fix_depth = lambda seq: None
        ex.do_run_respawn(1)
        self.assertFalse(any("後ろへ:" in str(m) for m in self.written))

    def test_after_moving_it_faces_again(self):
        ex = self._ex()
        faced = []
        ex._face_the_stairs = lambda seq: faced.append(1) or 1
        ex._fix_depth = lambda seq: "遠い → 前へ 0.3 秒"
        ex.do_run_respawn(1)
        self.assertEqual(len(faced), 2, "前後に動かしたら向きをもう 1 周")
        self.assertIn("Run: 位置を直しました（遠い → 前へ 0.3 秒）", self.logs)
        self.assertIn("Run: リスポーンして正面を向きました（向きを 2 回直しました）", self.logs)

    def test_not_moving_does_not_face_again(self):
        ex = self._ex()
        faced = []
        ex._face_the_stairs = lambda seq: faced.append(1) or 0
        ex._fix_depth = lambda seq: None
        ex.do_run_respawn(1)
        self.assertEqual(len(faced), 1)
        self.assertFalse(any("位置を直しました" in m for m in self.logs))

    def test_not_found_records_the_best_anywhere(self):
        ex = self._ex()
        shot = _far_shot()
        ex._measure_face = lambda: (shot, None)
        with patch.object(RunFace, "save_shot"):
            ex._face_the_stairs(1)
        line = next(m for m in self.written if "範囲の外も含めた一番よい一致" in str(m))
        self.assertIn("倍率 0.80", line)

    def test_the_backward_and_left_values_stay(self):
        self.assertEqual((config.RUN_RESPAWN_BACK_SEC, config.RUN_RESPAWN_TURN_SEC), (2.85, 0.9))


class TestDepthIsOff(unittest.TestCase):
    """EJ: 目印の大きさは距離を表していなかった（13:53 窓1: 正しい位置で 1.12 → 後ろへ直して数 m ずれた）。
    倍率は測って debug.log に書くだけで、前後には動かさない"""

    def test_the_switch_is_off(self):
        self.assertIs(config.RUN_DEPTH_ENABLED, False)

    def test_no_move_for_any_factor(self):
        for f in (1.12, 0.86, 0.5, 1.5):
            w = _World(self, f=f)
            self.assertIsNone(w.ex._fix_depth(1), f)
            self.assertEqual(w.presses, [], f)
            self.assertEqual(w.measures, 0, "撮り直さない")
            self.assertEqual(w.saved, [], "前後の撮影は残さない（向きの撮影は今どおり）")
            self.assertIn(f"[操作] [窓2] Run: 前後: 倍率 {f:.3f}（一致 0.900）（記録だけ・動かさない）", w.written)

    def test_no_mark_is_as_before(self):
        w = _World(self, f=1.12)
        w.ex._last_face = (_IMG, None)
        self.assertIsNone(w.ex._fix_depth(1))
        self.assertIn("[操作] [窓2] Run: 前後: 目印が見つからない → 直さない", w.written)

    def test_the_switch_on_moves_like_before(self):
        w = _World(self, f=1.12)
        with patch.object(config, "RUN_DEPTH_ENABLED", True):
            self.assertEqual(w.ex._fix_depth(1), "近い → 後ろへ 0.2 秒")
        self.assertEqual(w.presses, [("/input/MoveBackward", 0.2)])

    def test_the_respawn_flow_faces_once_and_does_not_move(self):
        """向きの直しは今どおり（1 周）。前後は動かないので、向きをもう 1 周しない。後ろへ 2.85・左へ 0.9 は今どおり"""
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=1)
        logs = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: True, logs.append)
        ex._osc = MagicMock()
        ex._receiver = None
        ex._capture_bgr = lambda: None      # 本物の撮影は使わない（EQ）
        ex._respawn_when_free = lambda seq: None
        ex._run_after_respawn = lambda seq: True
        faced = []

        def face(seq):
            faced.append(1)
            ex._last_face = (_IMG, (433.0, 0.9, 1.12))
            return 1
        ex._face_the_stairs = face
        written = []
        with patch.object(DebugLog, "write", written.append), \
             patch.object(SharedState, "get_run_respawn", lambda: True), \
             patch.object(RunFace, "factor", lambda mark, h: mark[2]):
            ex.do_run_respawn(1)
        self.assertEqual(len(faced), 1)
        self.assertEqual([c.args[:2] for c in ex._osc.press.call_args_list],
                         [("/input/MoveBackward", 2.85), ("/input/LookLeft", 0.9)])
        self.assertFalse(any("位置を直しました" in m for m in logs))
        self.assertIn("Run: リスポーンして正面を向きました（向きを 1 回直しました）", logs)
        self.assertIn("[操作] [窓1] Run: 前後: 倍率 1.120（一致 0.900）（記録だけ・動かさない）", written)
