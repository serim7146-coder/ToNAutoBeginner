"""Begin 前の移動を「動き出し」から数える（EF）。本物の OSC は使わない（偽物・時計も偽物）"""
from tests.support import *  # noqa: F401,F403
import inspect

F, L = "/input/MoveForward", "/input/MoveLeft"


class _World:
    """偽の時計・OSC・速さの受信。start_at（秒）から速さ 4.0。None なら速さを受け取れていない"""

    def __init__(self, test, start_at=0.06, no_data=False, stop_at=None):
        self.now = [0.0]
        self.sent = []
        self.start_at = start_at
        self.no_data = no_data
        self.stop_at = stop_at
        w = self
        fake_time = type(sys)("time_for_test")
        fake_time.time = fake_time.monotonic = lambda: w.now[0]
        fake_time.sleep = lambda sec: w.now.__setitem__(0, w.now[0] + sec)
        p = patch.object(ActionExecutor, "time", fake_time)
        p.start()
        test.addCleanup(p.stop)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=4)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x40, osc_port=9000), st,
                                                lambda: True, lambda m: None)
        self.ex._osc = self
        self.ex._receiver = self
        self.ex._move_stopped = lambda: w.stop_at is not None and w.now[0] >= w.stop_at

    @property
    def speed(self):
        if self.no_data:
            return None
        return 4.0 if self.start_at is not None and self.now[0] >= self.start_at else 0.0

    def send(self, address, value):
        self.sent.append((round(self.now[0], 2), address, value))
        return True

    def press_multi(self, holds, stop=None):
        self.sent.append(("press_multi", tuple(holds)))
        return True

    def stop_all(self, repeat=1):
        pass

    def released(self, address):
        times = [t for t, a, v in (x for x in self.sent if len(x) == 3) if a == address and v == 0]
        return times[-1]


class TestFromTheStart(unittest.TestCase):
    def test_a_late_start_extends_both_by_the_delay(self):
        """10:22 窓4: 動き出し 0.34 秒 → 0.26 秒延びる（前も左も）"""
        w = _World(self, start_at=0.34)
        self.assertTrue(w.ex.move_forward_left(2.08, 0.15))
        self.assertAlmostEqual(w.released(F), 2.08 + 0.26, delta=0.011)
        self.assertAlmostEqual(w.released(L), 0.15 + 0.26, delta=0.011)
        self.assertAlmostEqual(w.ex._move_extension[0], 0.26, delta=0.011)
        self.assertEqual(w.ex._move_extension[1], "動き出し")

    def test_a_usual_start_releases_at_the_same_time_as_before(self):
        for start in (0.05, 0.08):
            w = _World(self, start_at=start)
            w.ex.move_forward_left(2.08, 0.15)
            self.assertAlmostEqual(w.released(F), 2.08, delta=0.011, msg=start)
            self.assertAlmostEqual(w.released(L), 0.15, delta=0.011, msg=start)
            self.assertEqual(w.ex._move_extension[0], 0.0, "今より早くは離さない")

    def test_not_moving_after_two_represses_counts_from_the_press(self):
        """EH: 0.6 秒ごとに 2 回押し直しても動かなければ、押し始めから数えて離す"""
        w = _World(self, start_at=None)
        w.ex.move_forward_left(2.08, 0.15)
        self.assertAlmostEqual(w.released(F), 2.08, delta=0.011)
        self.assertEqual(w.ex._move_extension, (0.0, "動き出さない", 2))
        self.assertAlmostEqual(w.released(L), 2.0, delta=0.011,
                               msg="左は諦めるまで押したまま（動いていないので寄らない）")

    def test_a_start_after_a_repress_is_counted(self):
        """EH: 0.6 秒で押し直し、1.0 秒で動き出した → 延び 0.92（上限 0.5 ＋ 押し直しで失った 0.7 の中）"""
        w = _World(self, start_at=1.0)
        w.ex.move_forward_left(2.08, 0.15)
        self.assertAlmostEqual(w.released(F), 2.08 + 0.92, delta=0.011)
        self.assertEqual(w.ex._move_extension[1:], ("動き出し", 1))

    def test_the_extension_is_capped_at_0_5(self):
        w = _World(self, start_at=0.59)
        w.ex.move_forward_left(2.08, 0.15)
        self.assertAlmostEqual(w.released(F), 2.08 + 0.5, delta=0.011)
        self.assertAlmostEqual(w.ex._move_extension[0], 0.5, delta=1e-9)

    def test_after_punished_too(self):
        w = _World(self, start_at=0.34)
        w.ex.move_forward_left(config.BEGIN_FORWARD_SEC_LATER, config.BEGIN_LEFT_SEC_LATER)
        self.assertAlmostEqual(w.released(F), 3.2 + 0.26, delta=0.011)
        self.assertAlmostEqual(w.released(L), 0.16 + 0.26, delta=0.011)

    def test_no_speed_data_is_as_before(self):
        w = _World(self, no_data=True)
        w.ex.move_forward_left(2.08, 0.15)
        self.assertAlmostEqual(w.released(F), 2.08, delta=0.011)
        self.assertAlmostEqual(w.released(L), 0.15, delta=0.011, msg="受け取れていなければ左も今どおり")

    def test_no_receiver_uses_the_old_press(self):
        w = _World(self)
        w.ex._receiver = None
        w.ex.move_forward_left(2.08, 0.15)
        self.assertEqual(w.sent, [("press_multi", ((F, 2.08), (L, 0.15)))])
        self.assertIsNone(w.ex._move_extension)

    def test_both_are_pressed_together_after_a_zero(self):
        w = _World(self, start_at=0.34)
        w.ex.move_forward_left(2.08, 0.15)
        self.assertEqual(w.sent[:4], [(0.0, F, 0), (0.0, L, 0), (0.0, F, 1), (0.0, L, 1)])

    def test_a_stop_releases_both(self):
        w = _World(self, start_at=0.34, stop_at=1.0)
        self.assertFalse(w.ex.move_forward_left(2.08, 0.15))
        self.assertAlmostEqual(w.released(F), 1.0, delta=0.011)
        self.assertIn(L, [a for t, a, v in (x for x in w.sent if len(x) == 3) if v == 0])

    def test_the_values_stay(self):
        self.assertEqual((config.BEGIN_FORWARD_SEC, config.BEGIN_LEFT_SEC), (2.08, 0.15))
        self.assertEqual((config.BEGIN_USUAL_START_SEC, config.BEGIN_EXTEND_MAX_SEC,
                          config.BEGIN_START_WAIT_SEC), (0.08, 0.5, 0.6))


class TestTheMeasureLine(unittest.TestCase):
    def test_the_sampler_adds_the_extension(self):
        lines = []
        sampler = ActionExecutor.MotionSampler(None, lines.append)
        sampler.start()
        sampler.suffix = "・動き出しから数えた（+0.26 秒）"
        sampler.release()
        self.assertTrue(lines[0].endswith("・動き出しから数えた（+0.26 秒）"), lines)

    def test_the_begin_move_writes_it(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor)
        self.assertIn("sampler.suffix = counted_note(*extension)", src)
        i = src.index("ok = self.move_forward_left(forward, left)")
        self.assertLess(i, src.index("sampler.suffix = counted_note(*extension)"), "移動の後に決めて、release の前に付ける")
        self.assertLess(src.index("sampler.suffix = counted_note(*extension)"), src.index("            sampler.release()", i))
        self.assertEqual(ActionExecutor.counted_note(0.26, "動き出し"), "・動き出しから数えた（+0.26 秒）")
        self.assertEqual(ActionExecutor.counted_note(0.0, "受信なし"), "・押し始めから数えた（受信なし）")
