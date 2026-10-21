"""Run のリスポーンの後、撮影で向きを測って真正面に直す・ボタンの探し方の直し（DO・DQ）"""
from tests.support import *  # noqa: F401,F403
import RespawnButton
import RunFace

DATA = Path(__file__).parent / "data" / "run_respawn"


def _read(name):
    import cv2
    import numpy as np
    return cv2.imdecode(np.fromfile(str(DATA / name), np.uint8), cv2.IMREAD_COLOR)


def _frame_from_strip(name):
    """帯（高さ 8〜45%）だけを切り出した実際の撮影から、元の大きさの撮影を作る（帯の外は黒）"""
    import numpy as np
    m = re.search(r"_(\d+)x(\d+)_rows_(\d+)_(\d+)\.png", name)
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




class TestTheMarkOnRealShots(unittest.TestCase):
    """目印＝階段の上の構造の絵（scratchpad/respawn の 093311・093342・093343・093508・093511・093431・093537 の
    高さ 8〜45% の帯）。FK: find_mark は先に特徴点で探すが、ここは絵の照合（保険）を確かめるので、特徴点は無しにする"""

    def setUp(self):
        patcher = patch.object(RunFace, "find_by_features", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _x(self, name):
        found = RunFace.find_mark(_frame_from_strip(name))
        return None if found is None else found[0]

    def test_facing_the_front(self):
        """正面（093311）。右上の自分のアバターの画像（赤い服）は拾わない"""
        found = RunFace.find_mark(_frame_from_strip("face_now_867x702_rows_56_315.png"))
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found[0], 433, delta=5)
        self.assertGreaterEqual(found[1], 0.95)

    def test_turned_shots(self):
        """右へ 0.1・0.15 秒回した撮影（見込みは −106・−171）と、ずらして直した途中の撮影（目で確かめた位置）"""
        for name, x in (("face_calR_0.1_867x702_rows_56_315.png", 327),
                        ("face_calR_0.15_867x702_rows_56_315.png", 262),
                        ("face_fix2_093508_867x702_rows_56_315.png", 567),
                        ("face_fix2_093511_867x702_rows_56_315.png", 295)):
            got = self._x(name)
            self.assertIsNotNone(got, name)
            self.assertAlmostEqual(got, x, delta=10, msg=name)

    def test_another_window_size(self):
        """窓の大きさが違っても（正面の撮影を 1294×1399 相当に広げた）、絵を撮影の高さに合わせて探す"""
        import cv2
        frame = _frame_from_strip("face_now_867x702_rows_56_315.png")
        k = 1399 / 702
        big = cv2.resize(frame, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR)
        found = RunFace.find_mark(big)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found[0], 438 * k, delta=10)
        self.assertAlmostEqual(found[2], k, delta=0.2)

    def test_at_the_edge_or_outside_it_is_not_found(self):
        for name in ("face_fix2_093431_867x702_rows_56_315.png",     # 一致 0.786
                     "face_fix2_093537_867x702_rows_56_315.png"):    # 赤い膜の中（0.592）
            self.assertIsNone(self._x(name), name)

    def test_the_picture_and_the_values(self):
        self.assertTrue(Path(config.resource_path(RunFace.TEMPLATE_FILE)).is_file())
        self.assertTrue((Path(RunFace.__file__).parent.parent / RunFace.TEMPLATE_FILE).is_file(), "assets の中（EK）")
        self.assertIn("--include-data-dir=assets/templates=assets/templates", _load_build_script().BASE_ARGS)
        self.assertEqual((RunFace.BAND, RunFace.BASE_HEIGHT, RunFace.SCALES[0], RunFace.SCALES[-1]),
                         ((0.08, 0.45), 702, 0.85, 1.15))
        self.assertEqual(config.RUN_FACE_MATCH_MIN, 0.8)

    def test_the_red_blob_is_gone(self):
        self.assertFalse(hasattr(RunFace, "find_light"))
        src = Path(RunFace.__file__).read_text(encoding="utf-8")
        self.assertNotIn("COLOR_BGR2HSV", src)

    def test_nothing_is_none(self):
        import numpy as np
        self.assertIsNone(RunFace.find_mark(np.zeros((702, 867, 3), np.uint8)))
        self.assertIsNone(RunFace.find_mark(None))


class _World:
    """回すと目印が横へ動く偽の撮影（目印の位置を偽の find_mark が返す）。LookLeft は景色を右へ。
    ボタン（press）は RUN_FACE_RATE・軸（press_axis。GG）は RUN_FACE_FINE_RATE で動く（gain / axis_gain 倍で、
    見込みと違う回り方も作る）。presses はボタンだけ・axis は軸だけ・calls は順番の全部"""

    def __init__(self, x, width=867, gain=1.0, lose_after=None, axis_gain=None):
        self.x, self.width, self.gain = x, width, gain
        self.axis_gain = gain if axis_gain is None else axis_gain
        self.lose_after = lose_after        # この回数押したら目印が消える
        self.presses = []                   # (address, sec)
        self.axis = []                      # (address, value, sec)
        self.calls = []                     # ("press", address, sec) / ("axis", address, value, sec)
        self.stops = 0                      # stop_all の回数
        self.stop_args = []                 # 渡された stop（呼べるか見る）
        self._last_dir = None               # 直前の押しの向き（+1 右・-1 左）と秒数

    def shot(self):
        import numpy as np
        return np.zeros((702, self.width, 3), np.uint8)

    def find(self, _shot):
        if self.x is None or (self.lose_after is not None and len(self.calls) >= self.lose_after):
            return None
        return (self.x, 0.9, 1.0)

    def _turn(self, direction, sec, moved):
        reverting = self._last_dir is not None and self._last_dir == (-direction, round(sec, 4))
        self._last_dir = (direction, round(sec, 4))
        if self.x is not None:
            self.x -= direction * moved     # 右へ回すと景色は左へ（目印の x が減る）
        if reverting and self.lose_after is not None:
            self.lose_after = None          # 戻したら目印はまた見える

    def press(self, address, sec, stop=None):
        self.stop_args.append(stop)
        self.presses.append((address, round(sec, 4)))
        self.calls.append(("press", address, round(sec, 4)))
        self._turn(1 if address == "/input/LookRight" else -1, sec,
                   sec * config.RUN_FACE_RATE * self.width * self.gain)
        return True

    def press_axis(self, address, value, sec, stop=None):
        self.stop_args.append(stop)
        self.axis.append((address, value, round(sec, 4)))
        self.calls.append(("axis", address, value, round(sec, 4)))
        self._turn(1 if value > 0 else -1, sec,
                   sec * config.RUN_FACE_FINE_RATE * self.width * self.axis_gain)
        return True

    def stop_all(self, repeat=1):
        self.stops += 1


class TestTurningToTheFront(unittest.TestCase):
    def setUp(self):
        self.running = {"on": True}
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, in_round=True, round_seq=5,
                              round_type="Run", window_idx=1)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x40, osc_port=9000), self.st,
                                                lambda: self.running["on"], lambda _m: None)
        self.slept = []
        self.saved = []
        fake_time = type(sys)("time_for_test")
        fake_time.time = time.time
        fake_time.monotonic = time.monotonic
        fake_time.sleep = self.slept.append
        for p in (patch.object(ActionExecutor, "time", fake_time), patch.object(DebugLog, "write"),
                  patch.object(RunFace, "save_shot",
                               side_effect=lambda shot, window, why: self.saved.append((window, why)))):
            p.start()
            self.addCleanup(p.stop)

    def _face(self, world):
        self.ex._osc = world
        self.ex._capture_bgr = world.shot
        with patch.object(RunFace, "find_mark", side_effect=world.find):
            return self.ex._face_the_stairs(self.st.round_seq)

    def test_the_values(self):
        self.assertEqual((config.RUN_FACE_TOL, config.RUN_FACE_RATE, config.RUN_FACE_MIN_SEC, config.RUN_FACE_MAX_SEC,
                          config.RUN_FACE_SETTLE_SEC, config.RUN_FACE_TRIES), (0.01, 1.22, 0.02, 0.10, 0.35, 6))
        self.assertEqual((config.RUN_FACE_FINE_MAX, config.RUN_FACE_FINE_AXIS, config.RUN_FACE_FINE_RATE,
                          config.RUN_FACE_FINE_MIN_SEC, config.RUN_FACE_FINE_MAX_SEC), (0.03, 0.55, 0.09, 0.02, 0.35))

    def test_a_press_is_never_longer_than_0_10(self):
        world = _World(433.5 - 300)
        self._face(world)
        self.assertTrue(world.presses)
        self.assertTrue(all(sec <= 0.10 for _a, sec in world.presses), world.presses)
        self.assertEqual(world.presses[0], ("/input/LookLeft", 0.1))

    def test_the_length_follows_the_offset(self):
        world = _World(433.5 + 80)
        self.assertEqual(self._face(world), 1)
        self.assertEqual(world.presses, [("/input/LookRight", round(80 / (1.22 * 867), 4))])
        self.assertEqual(self.slept[0], config.RUN_FACE_SETTLE_SEC, "回し終えてから撮る")

    def test_a_small_button_press_is_at_least_0_02(self):
        """ボタンの下限（軸に分けなければ 1.7% のずれは 0.014 秒 → 0.02 秒）"""
        world = _World(433.5 + 15, gain=0.5)
        with patch.object(config, "RUN_FACE_FINE_MAX", 0.0):
            self._face(world)
        self.assertEqual(world.presses[0], ("/input/LookRight", 0.02))

    def test_shrinking_goes_on_until_it_is_within(self):
        world = _World(433.5 - 300)
        fixed = self._face(world)
        self.assertGreater(fixed, 2)
        self.assertLessEqual(abs(world.x - 433.5), 867 * config.RUN_FACE_TOL)
        self.assertTrue(all(a == "/input/LookLeft" for a, _s in world.presses))
        self.assertTrue(all(a == "/input/LookHorizontal" and v == -0.55 for a, v, _s in world.axis), world.axis)
        self.assertEqual(self.saved, [])

    def test_within_does_nothing(self):
        """幅の 1% 以内（0.92%）は正面。3% 前提だった 25px（2.9%）は今は軸で直す"""
        for dx in (0, 8, -8):
            world = _World(433.5 + dx)
            self.assertEqual(self._face(world), 0, dx)
            self.assertEqual(world.calls, [])

    def test_worse_after_a_press_turns_back_the_same_and_stops(self):
        """押したらずれが大きくなった（回りすぎて反対側へ）→ 逆向きを同じ秒数押して戻し、止める"""
        world = _World(433.5 - 80, gain=3.0)           # 見込みの 3 倍回る → −80 から +160 へ
        fixed = self._face(world)
        sec = round(80 / (1.22 * 867), 4)
        self.assertEqual(world.presses, [("/input/LookLeft", sec), ("/input/LookRight", sec)])
        self.assertEqual(fixed, 0, "戻したので直した回数に数えない")
        self.assertAlmostEqual(world.x, 433.5 - 80, delta=0.01, msg="押す前の向きに戻った")
        self.assertEqual(self.saved, [(1, "reverted")])

    def test_the_mark_lost_after_a_press_turns_back_and_stops(self):
        world = _World(433.5 - 200, lose_after=2)       # 2 回目を押したら目印が消えた
        fixed = self._face(world)
        self.assertEqual(len(world.presses), 3)
        self.assertEqual(world.presses[1][1], world.presses[2][1])
        self.assertEqual((world.presses[1][0], world.presses[2][0]), ("/input/LookLeft", "/input/LookRight"))
        self.assertEqual(fixed, 1)
        self.assertEqual(self.saved, [(1, "reverted")])

    def test_not_found_at_first_does_not_press(self):
        world = _World(None)
        self.assertEqual(self._face(world), 0)
        self.assertEqual(world.presses, [])
        self.assertEqual(self.saved, [(1, "notfound")])

    def test_it_gives_up_after_6(self):
        world = _World(433.5 - 400, gain=0.1)           # 少しずつしか縮まない
        self.assertEqual(self._face(world), 6)
        self.assertEqual(len(world.presses), 6)
        self.assertEqual(self.saved, [(1, "tries")])

    def test_stopping_stops_it(self):
        world = _World(433.5 - 300, gain=0.1)
        orig = world.press

        def press(address, sec, stop=None):
            orig(address, sec, stop)
            self.running["on"] = False
            self.assertTrue(stop())
        world.press = press
        self._face(world)
        self.assertEqual(len(world.presses), 1)

    def test_the_next_round_stops_it(self):
        world = _World(433.5 - 300, gain=0.1)
        orig = world.press

        def press(address, sec, stop=None):
            orig(address, sec, stop)
            self.st.round_seq += 1
        world.press = press
        self._face(world)
        self.assertEqual(len(world.presses), 1)

    def test_stopped_while_taking_the_shot_does_not_press(self):
        world = _World(433.5 - 300)
        orig = world.shot

        def shot():
            self.running["on"] = False
            return orig()
        world.shot = shot
        self._face(world)
        self.assertEqual(world.presses, [])

    def test_it_comes_after_the_turn_in_the_run_respawn(self):
        SharedState.set_run_respawn(True)
        self.addCleanup(SharedState.set_run_respawn, False)
        logs = []
        self.ex._log = logs.append
        world = _World(433.5 - 80)
        sent = []

        def press(address, sec, stop=None):
            sent.append((address, round(sec, 4)))
            if address in ("/input/MoveBackward",) or (address == "/input/LookLeft" and sec == config.RUN_RESPAWN_TURN_SEC):
                return True
            return world.press(address, sec, stop)
        self.ex._osc = MagicMock(press=press)
        self.ex._capture_bgr = world.shot
        with patch.object(self.ex, "_respawn_when_free", return_value=None), \
             patch.object(RunFace, "find_mark", side_effect=world.find):
            self.ex.do_run_respawn(self.st.round_seq)
        self.assertEqual([a for a, _s in sent], ["/input/MoveBackward", "/input/LookLeft", "/input/LookLeft"])
        self.assertEqual(logs, ["Run: リスポーンして正面を向きました（向きを 1 回直しました）"])


    # ── GG: 幅の 3% 以下は軸 /input/LookHorizontal の ±0.55 で細かく回す（1% まで詰める） ──

    def _logs(self):
        return [c.args[0] for c in DebugLog.write.call_args_list]

    def test_2_percent_right_turns_the_axis_plus(self):
        """ずれ +2%（幅 654 で +13px）→ 軸 +0.55・13 / (0.09 × 654) = 0.221 秒 → 撮り直しで 0.3% → 正面（直した 1 回）"""
        world = _World(327 + 13, width=654, axis_gain=0.85)           # 11px 回って 2px（0.3%）残る
        self.assertEqual(self._face(world), 1)
        self.assertEqual(world.presses, [], "ボタンは使わない")
        self.assertEqual(world.axis, [("/input/LookHorizontal", 0.55, 0.2209)])
        self.assertAlmostEqual(13 / (0.09 * 654), 0.2209, places=4)
        self.assertIn("[操作] [窓1] Run: 向き: /input/LookHorizontal +0.55 0.221秒（細かく）", self._logs())
        self.assertIn("[操作] [窓1] Run: 向き: 正面（直した 1 回）", self._logs())
        self.assertFalse(any("/input/LookRight" in m or "/input/LookLeft" in m for m in self._logs()))
        self.assertEqual(world.stops, 1, "回した後に stop_all(repeat=1)")
        self.assertEqual(self.slept[0], config.RUN_FACE_SETTLE_SEC, "回し終えてから撮る")

    def test_2_percent_left_turns_the_axis_minus(self):
        world = _World(327 - 13, width=654, axis_gain=0.85)
        self.assertEqual(self._face(world), 1)
        self.assertEqual(world.axis, [("/input/LookHorizontal", -0.55, 0.2209)])
        self.assertEqual(world.presses, [])
        self.assertIn("[操作] [窓1] Run: 向き: /input/LookHorizontal -0.55 0.221秒（細かく）", self._logs())

    def test_5_percent_uses_the_buttons_then_the_axis(self):
        """ずれ 5% → 今のボタン（LookRight・今の秒数）→ 2% → 軸 → 0.4% → 正面（直した 2 回）"""
        dx = 0.05 * 654
        world = _World(327 + dx, width=654, gain=0.6, axis_gain=0.8)
        self.assertEqual(self._face(world), 2)
        self.assertEqual(world.presses, [("/input/LookRight", round(dx / (1.22 * 654), 4))])
        self.assertEqual([c[0] for c in world.calls], ["press", "axis"], "ボタンが先・軸が後")
        self.assertEqual(world.axis[0][:2], ("/input/LookHorizontal", 0.55))
        self.assertLessEqual(abs(world.x - 327), 654 * 0.01)
        self.assertEqual(world.stops, 2)
        logs = self._logs()
        self.assertIn("[操作] [窓1] Run: 向き: /input/LookRight 0.041秒", logs, "ボタンのログは今のまま")
        self.assertTrue(any("/input/LookHorizontal +0.55" in m and m.endswith("秒（細かく）") for m in logs), logs)
        self.assertIn("[操作] [窓1] Run: 向き: 正面（直した 2 回）", logs)

    def test_0_8_percent_does_nothing(self):
        world = _World(327 + 0.008 * 654, width=654)
        self.assertEqual(self._face(world), 0)
        self.assertEqual(world.calls, [])
        self.assertIn("[操作] [窓1] Run: 向き: 正面（直した 0 回）", self._logs())

    def test_just_over_1_percent_is_turned(self):
        world = _World(327 + 0.0115 * 654, width=654, axis_gain=1.0)
        self.assertEqual(self._face(world), 1)
        self.assertEqual(len(world.axis), 1)

    def test_the_border_3_percent_is_the_axis_and_over_it_the_button(self):
        """ちょうど 3% は軸・それより大きい（3.1%）はボタン"""
        world = _World(500 + 30, width=1000)
        self._face(world)
        self.assertEqual(world.calls[0][0], "axis")
        self.assertEqual(world.calls[0][2:], (0.55, round(30 / (0.09 * 1000), 4)))
        world = _World(500 + 31, width=1000)
        self._face(world)
        self.assertEqual(world.calls[0], ("press", "/input/LookRight", round(31 / (1.22 * 1000), 4)))

    def test_the_axis_length_is_clamped(self):
        """軸の秒数は 0.02〜0.35 秒"""
        world = _World(500 + 0.5, width=1000)             # 0.05% → 0.0056 秒 → 0.02 秒
        with patch.object(config, "RUN_FACE_TOL", 0.0001):
            self._face(world)
        self.assertEqual(world.axis[0][2], 0.02)
        world = _World(500 + 80, width=1000)              # 8% を軸で（分け目を 10% に）→ 0.89 秒 → 0.35 秒
        with patch.object(config, "RUN_FACE_FINE_MAX", 0.10):
            self._face(world)
        self.assertEqual(world.axis[0][2], 0.35)

    def test_the_axis_length_for_3_percent_fits(self):
        """ちょうど 3% でも上限の 0.35 秒に収まる（0.333 秒）"""
        world = _World(500 + 30, width=1000)
        self._face(world)
        self.assertLessEqual(world.axis[0][2], config.RUN_FACE_FINE_MAX_SEC)
        self.assertAlmostEqual(world.axis[0][2], 0.3333, places=4)

    def test_worse_after_the_axis_turns_back_with_the_opposite_sign_and_stops(self):
        """軸で回したら大きくなった → 同じ秒数で −value の軸を送って戻し、止める（ボタンは使わない）"""
        world = _World(327 + 13, width=654, axis_gain=3.0)             # 13px のつもりが 39px 回って −26px
        fixed = self._face(world)
        self.assertEqual(world.axis, [("/input/LookHorizontal", 0.55, 0.2209), ("/input/LookHorizontal", -0.55, 0.2209)])
        self.assertEqual(world.presses, [])
        self.assertEqual(fixed, 0, "戻したので直した回数に数えない")
        self.assertAlmostEqual(world.x, 327 + 13, delta=0.01, msg="押す前の向きに戻った")
        self.assertEqual(self.saved, [(1, "reverted")])
        self.assertEqual(world.stops, 2)
        self.assertTrue(any("→ /input/LookHorizontal -0.55 0.221秒で戻して止める" in m for m in self._logs()), self._logs())

    def test_worse_after_the_axis_on_the_left_turns_back_plus(self):
        world = _World(327 - 13, width=654, axis_gain=3.0)
        self._face(world)
        self.assertEqual([v for _a, v, _s in world.axis], [-0.55, 0.55])

    def test_the_mark_lost_after_the_axis_turns_back_and_stops(self):
        world = _World(327 + 13, width=654, lose_after=1)
        fixed = self._face(world)
        self.assertEqual(world.axis, [("/input/LookHorizontal", 0.55, 0.2209), ("/input/LookHorizontal", -0.55, 0.2209)])
        self.assertEqual(fixed, 0)
        self.assertEqual(self.saved, [(1, "reverted")])
        self.assertTrue(any("目印が消えた → /input/LookHorizontal -0.55" in m for m in self._logs()), self._logs())

    def test_it_gives_up_after_6_with_the_axis_too(self):
        world = _World(327 + 19, width=654, axis_gain=0.05)            # 3% 以内（2.9%）だが少しずつしか縮まない
        self.assertEqual(self._face(world), 6)
        self.assertEqual(len(world.axis), 6)
        self.assertEqual(world.presses, [])
        self.assertEqual(self.saved, [(1, "tries")])
        self.assertTrue(any("6 回直しても ずれ" in m and "やめる" in m for m in self._logs()), self._logs())

    def test_the_axis_turn_stops_with_the_round_or_the_stop(self):
        """軸に渡す stop は、ラウンドが進んだ・止めたで True（今のボタンと同じ）"""
        world = _World(327 + 13, width=654, axis_gain=0.1)
        orig = world.press_axis

        def press_axis(address, value, sec, stop=None):
            self.assertFalse(stop())
            orig(address, value, sec, stop)
            self.st.round_seq += 1
            self.assertTrue(stop(), "ラウンドが進んだ")
        world.press_axis = press_axis
        self._face(world)
        self.assertEqual(len(world.axis), 1, "止まったので撮り直さない")
        self.st.round_seq = 5
        self.assertFalse(world.stop_args[0]())
        self.running["on"] = False
        self.assertTrue(world.stop_args[0]())

    def test_stopping_during_the_axis_does_not_measure_again(self):
        world = _World(327 + 13, width=654, axis_gain=0.1)
        orig = world.press_axis

        def press_axis(address, value, sec, stop=None):
            orig(address, value, sec, stop)
            self.running["on"] = False
        world.press_axis = press_axis
        self._face(world)
        self.assertEqual(len(world.axis), 1)


class TestPressAxis(unittest.TestCase):
    """OSCClient.press_axis（GG）: 軸は float のまま送り直す（press の int の 1 は全速になる）。実際には送らない"""

    def setUp(self):
        self.client = OSCClient.OSCClient(9000)
        self.addCleanup(self.client.close)
        self.sent = []
        self.now = [100.0]
        self.slept = []
        fake_time = type(sys)("time_for_test")

        def perf_counter():
            self.now[0] += 0.001                # 短い待ち（回しながら待つ）でも時間が進む
            return self.now[0]

        def sleep(sec):
            self.slept.append(sec)
            self.now[0] += sec
        fake_time.perf_counter = perf_counter
        fake_time.sleep = sleep
        fake_time.time = time.time
        for p in (patch.object(self.client, "send", side_effect=lambda a, v: self.sent.append((a, v)) or True),
                  patch.object(OSCClient, "time", fake_time)):
            p.start()
            self.addCleanup(p.stop)

    def test_zero_then_value_then_resend_the_same_float_then_zero(self):
        ok = self.client.press_axis("/input/LookHorizontal", 0.55, 0.25)
        self.assertTrue(ok)
        values = [v for _a, v in self.sent]
        self.assertEqual(values[:2], [0.0, 0.55], "押す前に 0.0 を送ってから value")
        self.assertEqual(values[-1], 0.0, "最後は 0.0")
        self.assertTrue(all(a == "/input/LookHorizontal" for a, _v in self.sent))
        self.assertGreaterEqual(values.count(0.55), 3, "押し始めのあと、0.1 秒ごとに同じ値を送り直す")
        self.assertTrue(all(isinstance(v, float) for v in values), values)
        self.assertEqual(sorted(set(values)), [0.0, 0.55], "int の 1 やほかの値を送らない")

    def test_the_value_goes_out_as_an_osc_float(self):
        self.client.press_axis("/input/LookHorizontal", -0.55, 0.0)
        for _a, v in self.sent:
            msg = OSCClient.OSCClient.build_message("/input/LookHorizontal", v)
            self.assertIn(b",f", msg)
            self.assertNotIn(b",i", msg)

    def test_an_int_value_is_sent_as_float(self):
        self.client.press_axis("/input/LookHorizontal", 1, 0.0)
        self.assertEqual([type(v) for _a, v in self.sent], [float, float, float])

    def test_stop_releases_at_once(self):
        calls = []

        def stop():
            calls.append(1)
            return len(calls) >= 2                  # 1 回目は押している・2 回目で止める
        self.client.press_axis("/input/LookHorizontal", 0.55, 5.0, stop=stop)
        self.assertEqual([v for _a, v in self.sent][-1], 0.0)
        self.assertLess(self.now[0], 100.0 + 0.5, "5 秒待たずに離す")

    def test_stop_already_true_sends_zero_value_zero(self):
        self.client.press_axis("/input/LookHorizontal", 0.55, 1.0, stop=lambda: True)
        self.assertEqual([v for _a, v in self.sent], [0.0, 0.55, 0.0])

    def test_the_hold_is_accurate_for_a_short_press(self):
        """残りが 0.02 秒を切ったら sleep せず perf_counter で待つ（sleep は 0.02 秒ぶん手前まで）"""
        start = self.now[0]
        self.client.press_axis("/input/LookHorizontal", 0.55, 0.12)
        held = self.now[0] - start
        self.assertGreaterEqual(held, 0.12)
        self.assertLess(held, 0.12 + 0.01)
        self.assertTrue(all(s <= config.OSC_HOLD_RESEND_SEC + 1e-9 for s in self.slept), self.slept)
        self.assertLessEqual(sum(self.slept), 0.12 - 0.02 + 0.01, "最後の 0.02 秒は sleep しない")

    def test_a_very_short_press_does_not_sleep(self):
        self.client.press_axis("/input/LookHorizontal", 0.55, 0.015)
        self.assertEqual(self.slept, [])
        self.assertEqual([v for _a, v in self.sent], [0.0, 0.55, 0.0])

    def test_stop_all_zeroes_the_axis_with_a_float(self):
        """stop_all は /input/LookHorizontal も float の 0.0 で止める（ボタンは int の 0）"""
        self.client.stop_all(repeat=1)
        sent = dict(self.sent)
        self.assertEqual(sent["/input/LookHorizontal"], 0.0)
        self.assertIsInstance(sent["/input/LookHorizontal"], float)
        self.assertIsInstance(sent["/input/LookLeft"], int)


class TestTheShotsAreKept(unittest.TestCase):
    """うまくいかなかったときの撮影は %APPDATA%\\ToNAutoBeginner\\run_face\\ に最新 20 枚まで（1/2 の JPEG）"""

    def setUp(self):
        import shutil
        self.addCleanup(lambda: shutil.rmtree(RunFace.folder(), ignore_errors=True))
        p = patch.object(DebugLog, "write")
        p.start()
        self.addCleanup(p.stop)

    def _shot(self):
        import numpy as np
        return np.full((702, 867, 3), 80, np.uint8)

    def test_the_folder_is_next_to_the_settings(self):
        self.assertEqual(RunFace.folder(), Path(config.SETTINGS_PATH).parent / "run_face")
        self.assertNotEqual(RunFace.folder().parent.resolve(), REAL_SETTINGS_DIR.resolve(), "テストでは本物に書かない")

    def test_a_half_size_jpeg(self):
        import cv2
        import numpy as np
        path = RunFace.save_shot(self._shot(), 3, "reverted")
        self.assertTrue(path.name.endswith("_窓3_reverted.jpg"))
        img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape[:2], (351, 434))

    def test_only_the_newest_20(self):
        names = []
        for i in range(25):
            with patch.object(RunFace, "datetime") as dt:
                dt.now.return_value = datetime(2026, 10, 11, 9, 0, i)
                names.append(RunFace.save_shot(self._shot(), 1, "tries").name)
        kept = sorted(p.name for p in RunFace.folder().glob("*.jpg"))
        self.assertEqual(kept, names[5:])

    def test_they_go_into_the_report_with_the_shots_check(self):
        RunFace.save_shot(self._shot(), 2, "notfound")
        RunFace.save_shot(self._shot(), 4, "reverted")
        self.assertEqual(len(RunFace.files_for(2)), 1)
        self.assertEqual(len(RunFace.files_for(0)), 2, "窓なしなら全部の窓の分")
        include = {k: False for k, _l in BugReport.ATTACHMENTS}
        include[BugReport.BEGIN_MISS] = True
        name, data = BugReport.build_report("x", 2, include, "", None, {}, datetime(2026, 10, 11), version="v")
        files = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertEqual([f for f in files if f.startswith("run_face/")], list(RunFace.files_for(2)))
        include[BugReport.BEGIN_MISS] = False
        name, data = BugReport.build_report("x", 2, include, "", None, {}, datetime(2026, 10, 11), version="v")
        self.assertFalse([f for f in zipfile.ZipFile(io.BytesIO(data)).namelist() if f.startswith("run_face/")],
                         "チェックが無ければ入れない（ほかのプレイヤーの名前が写りうる）")
