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
    高さ 8〜45% の帯）"""

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
    """回すと目印が横へ動く偽の撮影（目印の位置を偽の find_mark が返す）。LookLeft は景色を右へ"""

    def __init__(self, x, width=867, gain=1.0, lose_after=None):
        self.x, self.width, self.gain = x, width, gain
        self.lose_after = lose_after        # この回数押したら目印が消える
        self.presses = []

    def shot(self):
        import numpy as np
        return np.zeros((702, self.width, 3), np.uint8)

    def find(self, _shot):
        if self.x is None or (self.lose_after is not None and len(self.presses) >= self.lose_after
                              and self.presses[-1][0] != "back"):
            return None
        return (self.x, 0.9, 1.0)

    def press(self, address, sec, stop=None):
        moved = sec * config.RUN_FACE_RATE * self.width * self.gain
        reverting = bool(self.presses) and self.presses[-1][1] == round(sec, 4) and address != self.presses[-1][0]
        self.presses.append((address, round(sec, 4)))
        if self.x is not None:
            self.x += moved if address == "/input/LookLeft" else -moved
        if reverting and self.lose_after is not None:
            self.lose_after = None          # 戻したら目印はまた見える
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
                          config.RUN_FACE_SETTLE_SEC, config.RUN_FACE_TRIES), (0.03, 1.22, 0.02, 0.10, 0.35, 6))

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

    def test_a_small_offset_is_at_least_0_02(self):
        world = _World(433.5 + 15, gain=0.5)
        with patch.object(config, "RUN_FACE_TOL", 0.01):
            self._face(world)
        self.assertEqual(world.presses[0], ("/input/LookRight", 0.02))

    def test_shrinking_goes_on_until_it_is_within(self):
        world = _World(433.5 - 300)
        fixed = self._face(world)
        self.assertGreater(fixed, 2)
        self.assertLessEqual(abs(world.x - 433.5), 867 * config.RUN_FACE_TOL)
        self.assertTrue(all(a == "/input/LookLeft" for a, _s in world.presses))
        self.assertEqual(self.saved, [])

    def test_within_does_nothing(self):
        for dx in (0, 25, -25):
            world = _World(433.5 + dx)
            self.assertEqual(self._face(world), 0, dx)
            self.assertEqual(world.presses, [])

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
