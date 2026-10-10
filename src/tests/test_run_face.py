"""Run のリスポーンの後、撮影で向きを測って真正面に直す・ボタンの探し方の直し（DO）"""
from tests.support import *  # noqa: F401,F403
import RespawnButton
import RunFace

DATA = Path(__file__).parent / "data" / "run_respawn"


def _read(name):
    import cv2
    import numpy as np
    return cv2.imdecode(np.fromfile(str(DATA / name), np.uint8), cv2.IMREAD_COLOR)


def _frame_from_strip(name):
    """帯（高さ 15〜35%）だけを切り出した実際の撮影から、元の大きさの撮影を作る（帯の外は黒）"""
    import numpy as np
    m = re.match(r"light_(\d+)x(\d+)_turn[\d.]+_rows_(\d+)_(\d+)\.png", name)
    w, h, y0, y1 = (int(v) for v in m.groups())
    frame = np.zeros((h, w, 3), np.uint8)
    frame[y0:y1] = _read(name)
    return frame


class TestTheButtonOnRealShots(unittest.TestCase):
    """実際の撮影（scratchpad/respawn の 124819_w2_menu・003758_seq_menu のボタンの周り）"""

    def test_window_1294x1399_at_1_0(self):
        found = RespawnButton.find(_read("menu_1294x1399_at_358_923.png"))
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found[0], 578 - 358, delta=2)
        self.assertAlmostEqual(found[1], 1033 - 923, delta=2)
        self.assertEqual(found[3], 1.0)

    def test_window_867x702_at_0_51(self):
        found = RespawnButton.find(_read("menu_867x702_at_248_455.png"))
        self.assertIsNotNone(found, "いつもの6窓の大きさでも見つかる")
        self.assertAlmostEqual(found[3], 0.51, delta=0.011)
        self.assertGreaterEqual(found[2], RespawnButton.MATCH_MIN)
        self.assertAlmostEqual(found[0], 397.5 - 248, delta=3)
        self.assertAlmostEqual(found[1], 530 - 455, delta=3)

    def test_the_old_coarse_steps_missed_it(self):
        """0.05 刻みのままだと 867×702 では見つからない（DL の不具合）"""
        with patch.object(RespawnButton, "FINE_SPAN", 0.0):
            self.assertIsNone(RespawnButton.find(_read("menu_867x702_at_248_455.png")))

    def test_the_fine_steps(self):
        self.assertEqual((RespawnButton.FINE_SPAN, RespawnButton.FINE_STEP), (0.05, 0.01))


class TestTheLightOnRealShots(unittest.TestCase):
    """実際の撮影の階段の上の赤い光（scratchpad/respawn の 130329・130357・130232・003902）"""

    def test_window_2(self):
        for name, x in (("light_1294x1399_turn0.8_rows_209_489.png", 466),
                        ("light_1294x1399_turn0.9_rows_209_489.png", 673),
                        ("light_1294x1399_turn1.0_rows_209_489.png", 891)):
            found = RunFace.find_light(_frame_from_strip(name))
            self.assertIsNotNone(found, name)
            self.assertAlmostEqual(found[0], x, delta=10, msg=name)

    def test_window_1_after_0_8(self):
        found = RunFace.find_light(_frame_from_strip("light_867x702_turn0.8_rows_105_245.png"))
        self.assertIsNotNone(found)
        self.assertTrue(313 - 10 <= found[0] <= 355 + 10, found)

    def test_nothing_red_is_none(self):
        import numpy as np
        self.assertIsNone(RunFace.find_light(np.zeros((702, 867, 3), np.uint8)))
        self.assertIsNone(RunFace.find_light(None))

    def test_a_small_red_is_not_the_light(self):
        import numpy as np
        frame = np.zeros((1399, 1294, 3), np.uint8)
        frame[300:304, 100:104] = (0, 0, 255)      # 小さな赤（遠くの灯り。膨らませても 144 画素）
        self.assertIsNone(RunFace.find_light(frame))

    def test_red_outside_the_band_is_not_used(self):
        import numpy as np
        frame = np.zeros((702, 867, 3), np.uint8)
        frame[30:60, 400:440] = (0, 0, 255)        # 帯の上
        frame[500:530, 400:440] = (0, 0, 255)      # 帯の下
        self.assertIsNone(RunFace.find_light(frame))


class _World:
    """回すと光が横へ動く偽の撮影。LookLeft は景色を右へ、LookRight は左へ動かす"""

    def __init__(self, x, size=(867, 702), gain=1.0, follows=True):
        self.x, self.size, self.gain, self.follows = x, size, gain, follows
        self.presses = []
        self.shots = 0

    def shot(self):
        import numpy as np
        self.shots += 1
        w, h = self.size
        frame = np.zeros((h, w, 3), np.uint8)
        if self.x is not None:
            y = int(h * 0.25)
            x = int(round(self.x))
            frame[y - 10:y + 10, max(0, x - 10):min(w, x + 10)] = (0, 0, 255)
        return frame

    def press(self, address, sec, stop=None):
        self.presses.append((address, round(sec, 4)))
        if self.follows and self.x is not None and address in ("/input/LookLeft", "/input/LookRight"):
            moved = sec * config.RUN_FACE_RATE * self.size[0] * self.gain
            self.x += moved if address == "/input/LookLeft" else -moved
        return True

    def stop_all(self, repeat=1):
        pass


class TestTurningToTheFront(unittest.TestCase):
    def setUp(self):
        self.running = {"on": True}
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, in_round=True, round_seq=5,
                              round_type="Run", window_idx=1)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x40, osc_port=9000), self.st,
                                                lambda: self.running["on"], lambda _m: None)
        self.slept = []
        fake_time = type(sys)("time_for_test")
        fake_time.time = time.time
        fake_time.monotonic = time.monotonic
        fake_time.sleep = self.slept.append
        for p in (patch.object(ActionExecutor, "time", fake_time), patch.object(DebugLog, "write")):
            p.start()
            self.addCleanup(p.stop)

    def _face(self, world):
        self.ex._osc = world
        self.ex._capture_bgr = world.shot
        return self.ex._face_the_stairs(self.st.round_seq)

    def test_the_values(self):
        self.assertEqual((config.RUN_FACE_TOL, config.RUN_FACE_RATE, config.RUN_FACE_MIN_SEC,
                          config.RUN_FACE_SETTLE_SEC, config.RUN_FACE_TRIES), (0.03, 1.27, 0.02, 0.35, 4))

    def test_light_on_the_left_turns_left_once_like_the_real_one(self):
        """実機: 0.8 秒だけ回した後（ずれ -120）→ LookLeft 0.109 秒 → +6 で入った"""
        world = _World(433.5 - 120)
        self.assertEqual(self._face(world), 1)
        self.assertEqual(world.presses, [("/input/LookLeft", round(120 / (1.27 * 867), 4))])
        self.assertAlmostEqual(world.presses[0][1], 0.109, delta=0.001)
        self.assertEqual(self.slept[0], config.RUN_FACE_SETTLE_SEC, "回し終えてから撮る")

    def test_light_on_the_right_turns_right(self):
        world = _World(433.5 + 80)
        self.assertEqual(self._face(world), 1)
        self.assertEqual(world.presses[0][0], "/input/LookRight")
        self.assertAlmostEqual(world.presses[0][1], 80 / (1.27 * 867), delta=0.001)

    def test_a_small_offset_is_at_least_0_02(self):
        """下限は、ずれが幅の 2.54% 未満で効く（許しが 3% の今の値では効かない。許しを 1% にして確かめる）"""
        world = _World(433.5 + 15, gain=0.5)
        with patch.object(config, "RUN_FACE_TOL", 0.01):
            self._face(world)
        self.assertEqual(world.presses[0], ("/input/LookRight", 0.02))

    def test_within_the_tolerance_does_nothing(self):
        for dx in (0, 25, -25):
            world = _World(433.5 + dx)
            self.assertEqual(self._face(world), 0, dx)
            self.assertEqual(world.presses, [])

    def test_it_settles_as_the_offset_shrinks(self):
        world = _World(433.5 - 300, gain=0.6)     # 回る量が見込みの 6 割（実機と違う感度）
        fixed = self._face(world)
        self.assertGreater(fixed, 1)
        self.assertLessEqual(fixed, config.RUN_FACE_TRIES)
        self.assertLessEqual(abs(world.x - 433.5), 867 * config.RUN_FACE_TOL)
        self.assertTrue(all(a == "/input/LookLeft" for a, _s in world.presses))

    def test_it_gives_up_after_4(self):
        world = _World(433.5 - 200, follows=False)  # 回しても変わらない
        self.assertEqual(self._face(world), 4)
        self.assertEqual(len(world.presses), 4)

    def test_no_light_does_nothing(self):
        world = _World(None)
        self.assertEqual(self._face(world), 0)
        self.assertEqual(world.presses, [])

    def test_stopping_stops_it(self):
        world = _World(433.5 - 300, follows=False)
        orig = world.press

        def press(address, sec, stop=None):
            orig(address, sec, stop)
            self.running["on"] = False
            self.assertTrue(stop(), "押している最中にも止まる")
        world.press = press
        self._face(world)
        self.assertEqual(len(world.presses), 1)

    def test_stopped_while_taking_the_shot_does_not_press(self):
        world = _World(433.5 - 300)
        orig = world.shot

        def shot():
            self.running["on"] = False              # 撮っている間に止められた
            return orig()
        world.shot = shot
        self._face(world)
        self.assertEqual(world.presses, [])

    def test_the_next_round_stops_it(self):
        world = _World(433.5 - 300, follows=False)
        orig = world.press

        def press(address, sec, stop=None):
            orig(address, sec, stop)
            self.st.round_seq += 1
        world.press = press
        self._face(world)
        self.assertEqual(len(world.presses), 1)

    def test_it_comes_after_the_turn_in_the_run_respawn(self):
        """リスポーン → 後ろへ → 左へ 0.9 → 撮って直す、の順。直した回数を公開ログに足す"""
        SharedState.set_run_respawn(True)
        self.addCleanup(SharedState.set_run_respawn, False)
        logs = []
        self.ex._log = logs.append
        world = _World(None)
        sent = []

        def press(address, sec, stop=None):
            sent.append((address, round(sec, 4)))
            if address == "/input/LookLeft" and sec == config.RUN_RESPAWN_TURN_SEC:
                world.x = 433.5 - 120               # 0.9 秒回したが少し足りない
                return True
            return world.press(address, sec, stop) if world.x is not None else True
        world_osc = MagicMock(press=press)
        self.ex._osc = world_osc
        self.ex._capture_bgr = world.shot
        with patch.object(self.ex, "_respawn_when_free", return_value=None):
            self.ex.do_run_respawn(self.st.round_seq)
        self.assertEqual([a for a, _s in sent], ["/input/MoveBackward", "/input/LookLeft", "/input/LookLeft"])
        self.assertEqual(sent[1], ("/input/LookLeft", 0.9))
        self.assertEqual(logs, ["Run: リスポーンして正面を向きました（向きを 1 回直しました）"])

    def test_no_correction_keeps_the_log(self):
        SharedState.set_run_respawn(True)
        self.addCleanup(SharedState.set_run_respawn, False)
        logs = []
        self.ex._log = logs.append
        world = _World(433.5)
        self.ex._osc = world
        self.ex._capture_bgr = world.shot
        with patch.object(self.ex, "_respawn_when_free", return_value=None):
            self.ex.do_run_respawn(self.st.round_seq)
        self.assertEqual(logs, ["Run: リスポーンして正面を向きました"])
