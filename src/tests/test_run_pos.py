"""Run の位置合わせのためのデータ集め（EQ。撮って残すだけ）。本物の撮影・受信・前面は使わない
（撮影・速さ・時計・窓の大きさを偽物に。残す先は support が差し替えた設定の置き場所の下）"""
from tests.support import *  # noqa: F401,F403
import numpy as np
import RunPos
import inspect


class _World:
    def __init__(self, test, speed=0.0, size=(867, 663)):
        self.now = [1000.0]
        self.speed = speed              # 数か、時刻 → 速さ の関数
        self.size = size
        self.shots = 0
        self.change = 0                 # 2 枚目を変える画素の値（向きを変えた）
        self.fronts = []
        w = self
        fake_time = type(sys)("time_for_test")
        fake_time.time = fake_time.monotonic = lambda: w.now[0]
        fake_time.sleep = lambda sec: w.now.__setitem__(0, w.now[0] + sec)
        for target, name, value in ((ActionExecutor, "time", fake_time),
                                    (WindowOperator, "client_size", lambda h: w.size),
                                    (WindowOperator, "borrow_front", lambda *a: w.fronts.append("borrow") or (True, None)),
                                    (WindowOperator, "focus_vrchat", lambda *a: w.fronts.append("focus")),
                                    (WindowOperator, "send_keys", lambda *a: w.fronts.append("keys")),
                                    (WindowOperator, "mouse_click", lambda *a, **k: w.fronts.append("click"))):
            p = patch.object(target, name, value)
            p.start()
            test.addCleanup(p.stop)
        self.written = []
        p = patch.object(DebugLog, "write", self.written.append)
        p.start()
        test.addCleanup(p.stop)
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=2)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x20, osc_port=9000), self.st,
                                                lambda: True, lambda _m: None)
        self.ex._capture_bgr = self.capture
        self.ex._osc = MagicMock()
        folder = RunPos.folder()
        if folder.is_dir():
            for f in folder.glob("*.png"):
                f.unlink()

    def capture(self):
        self.shots += 1
        img = np.full((self.size[1] // 10, self.size[0] // 10, 3), 40, np.uint8)
        if self.shots % 2 == 0 and self.change:
            img[:] = 40 + self.change
        return img

    def speed_now(self):
        return self.speed(self.now[0]) if callable(self.speed) else self.speed

    def round_over(self, round_type="Classic", continued=False):
        """RoundOver の後: 終わったラウンドの種類を決めて、待ちの中で撮るか決め、撮る（待ちは 9 秒）"""
        self.st.round_type = round_type
        self.st.is_continue_round = continued
        self.st.in_round = False
        if self.ex._pos_ref_plan():
            self.ex._take_pos_ref(self.now[0] + config.BEGIN_WAIT_SEC)

    def files(self, kind=None):
        folder = RunPos.folder()
        names = sorted(p.name for p in folder.glob("*.png")) if folder.is_dir() else []
        return [n for n in names if kind is None or f"_{kind}_" in n]


def _world(test, **kw):
    w = _World(test, **kw)
    rec = type("R", (), {"speed": property(lambda _s: w.speed_now())})()
    w.ex._receiver = rec
    return w


class TestTheReferenceShot(unittest.TestCase):
    def test_after_the_third_round_once(self):
        w = _world(self)
        w.round_over()
        w.round_over()
        self.assertEqual(w.files(), [], "2 回目まではまだ")
        self.assertIn("[操作] [窓2] Run の位置: 基準: 撮らない（普通に終わったラウンド 2/3）", w.written)
        w.round_over()
        refs = w.files("ref")
        self.assertEqual(len(refs), 1, "3 回目の後に撮る")
        self.assertIn("_窓2_ref_867x663.png", refs[0])
        self.assertEqual(w.shots, 2, "0.5 秒あけた 2 枚")
        self.assertTrue(any("基準: 撮った" in m and "2 枚の差 0.00" in m for m in w.written), w.written)
        before = len(w.written)
        w.round_over()
        w.round_over()
        self.assertEqual(len(w.files("ref")), 1, "2 回目以降は撮らない")
        self.assertEqual(w.shots, 2)
        self.assertEqual(len(w.written), before, "撮ってあれば毎ラウンドは書かない")

    def test_continue_run_not_private_or_no_receiver_do_not_shoot(self):
        cases = (("続行ラウンドの後", lambda w: w.round_over(continued=True)),
                 ("Run の後", lambda w: w.round_over("Run")))
        for why, last in cases:
            w = _world(self)
            w.round_over()
            w.round_over()
            last(w)
            self.assertEqual(w.files(), [], why)
            self.assertIn(f"[操作] [窓2] Run の位置: 基準: 撮らない（{why}）", w.written)
            w.round_over()
            self.assertEqual(len(w.files("ref")), 1, f"{why} は数えない（次の普通のラウンドで 3 回目）")
        for why, spoil in (("private でない", lambda w: setattr(w.st, "instance_type", config.INSTANCE_PUBLIC)),
                           ("速さの受信が無い", lambda w: setattr(w.ex, "_receiver", None))):
            w = _world(self)
            spoil(w)
            for _ in range(4):
                w.round_over()
            self.assertEqual(w.files(), [], why)
            self.assertIn(f"[操作] [窓2] Run の位置: 基準: 撮らない（{why}）", w.written)
            self.assertEqual(w.shots, 0)

    def test_moving_does_not_shoot_and_tries_next_round(self):
        w = _world(self, speed=0.1)                 # 0.1 は「止まっている」に入らない
        for _ in range(3):
            w.round_over()
        self.assertEqual(w.files(), [])
        self.assertEqual(w.shots, 0)
        self.assertTrue(any("撮らない（Begin 前の移動までに止まらなかった。最高の速さ 0.10）" in m
                            for m in w.written), w.written)
        w.speed = 0.09
        w.round_over()
        self.assertEqual(len(w.files("ref")), 1, "次のラウンドでまた試す")

    def test_still_must_last_one_second(self):
        start = [None]

        def speed(now):
            if start[0] is None:
                start[0] = now
            return 4.0 if now - start[0] < 7.0 else 0.0  # 待ち 9 秒のうち 7 秒動いて止まる → 1.0 秒＋0.5 で 8.5 秒
        w = _world(self, speed=speed)
        for _ in range(2):
            w.round_over()
        w.speed = speed
        w.round_over()
        self.assertEqual(len(w.files("ref")), 1)
        start[0] = None

        def speed_late(now):
            if start[0] is None:
                start[0] = now
            return 4.0 if now - start[0] < 7.6 else 0.0  # 止まって 1.0 秒たつ前に、0.5 秒を残せない
        w2 = _world(self, speed=speed_late)
        for _ in range(3):
            w2.round_over()
        self.assertEqual(w2.files(), [], "待ちの中で間に合わなければやめる")

    def test_a_big_difference_does_not_shoot(self):
        w = _world(self)
        w.change = 30                               # 2 枚目が変わった（向きを変えた）
        for _ in range(3):
            w.round_over()
        self.assertEqual(w.files(), [])
        self.assertTrue(any("撮らない（2 枚の差 30.00 > 3.0" in m for m in w.written), w.written)
        w.change = 2                                # 2.00 は小さい
        w.round_over()
        self.assertEqual(len(w.files("ref")), 1)

    def test_moving_between_the_two_shots_does_not_shoot(self):
        w = _world(self)
        real = w.capture

        def capture():
            img = real()
            if w.shots == 1:
                w.speed = 2.0                       # 1 枚目の後に動いた
            return img
        w.ex._capture_bgr = capture
        for _ in range(3):
            w.round_over()
        self.assertEqual(w.files(), [])
        self.assertTrue(any("撮らない（2 枚の間に動いた" in m for m in w.written), w.written)

    def test_a_new_window_size_shoots_again(self):
        w = _world(self)
        for _ in range(3):
            w.round_over()
        w.size = (1280, 720)
        w.round_over()
        refs = w.files("ref")
        self.assertEqual(len(refs), 2)
        # 名前の並びでは見ない（テストの中の 2 枚は同じ時刻の名前になりうる。そのとき 1280 < 867 と並ぶ）
        self.assertEqual(sorted(n.rsplit("_", 1)[1] for n in refs), ["1280x720.png", "867x663.png"])
        self.assertIn("[操作] [窓2] Run の位置: 基準: 窓の大きさが変わった（(867, 663) → (1280, 720)）→ 撮り直す",
                      w.written)
        w.round_over()
        self.assertEqual(len(w.files("ref")), 2, "同じ大きさならもう撮らない")

    def test_the_window_size_change_waits_for_a_normal_round(self):
        w = _world(self)
        for _ in range(3):
            w.round_over()
        w.size = (1280, 720)
        w.round_over("Run")
        self.assertEqual(len(w.files("ref")), 1, "Run の後には撮らない")
        w.round_over()
        self.assertEqual(len(w.files("ref")), 2)

    def test_it_never_takes_the_front_or_presses(self):
        w = _world(self)
        for _ in range(3):
            w.round_over()
        w.ex._take_pos_run()
        self.assertEqual(w.fronts, [])
        w.ex._osc.press.assert_not_called()
        w.ex._osc.send.assert_not_called()


class TestTheBeginWaitIsNotDelayed(unittest.TestCase):
    """撮っても・間に合わなくても、Begin 前の移動は RoundOver から BEGIN_WAIT_SEC で始まる"""

    def _after_round(self, w):
        started = []
        w.st.round_over_time = w.now[0]
        with patch.object(w.ex, "_wait_run_respawn", return_value=True), \
             patch.object(w.ex, "_begin_precheck", side_effect=lambda **k: started.append(w.now[0]) or False):
            w.ex.do_after_round()
        return started[0] - w.st.round_over_time

    def test_shooting_or_not(self):
        for speed, shots in ((0.0, 2), (4.0, 0)):
            w = _world(self, speed=speed)
            w.ex._pos_rounds = 2
            w.st.round_type, w.st.is_continue_round = "Classic", False
            self.assertAlmostEqual(self._after_round(w), config.BEGIN_WAIT_SEC, delta=0.06, msg=speed)
            self.assertEqual(w.shots, shots, speed)

    def test_the_round_is_counted_before_the_wait(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor.do_after_round)
        self.assertLess(src.index("pos_ref = self._pos_ref_plan()"), src.index("if config.BEGIN_WAIT_SEC > 0:"))


class TestAfterTheRun(unittest.TestCase):
    def test_one_shot_after_facing(self):
        w = _world(self)
        w.ex._take_pos_run()
        runs = w.files("run")
        self.assertEqual(len(runs), 1)
        self.assertIn("_窓2_run_867x663.png", runs[0])
        self.assertTrue(any("Run の位置: Run の後 撮った" in m for m in w.written), w.written)

    def test_it_is_taken_at_the_end_of_the_respawn(self):
        # 中身は _respawn_and_face（EY: 店に行った後の押し直しでも使う。そのときは撮らない）
        src = inspect.getsource(ActionExecutor.ActionExecutor._respawn_and_face)
        self.assertLess(src.index('self._log(f"{label}: リスポーンして正面を向きました"'), src.index("self._take_pos_run()"))
        self.assertLess(src.index("fixed = self._face_the_stairs(round_seq)"), src.index("self._take_pos_run()"))


class TestHowManyAreKept(unittest.TestCase):
    def setUp(self):
        folder = RunPos.folder()
        if folder.is_dir():
            for f in folder.glob("*.png"):
                f.unlink()
        self.img = np.zeros((4, 4, 3), np.uint8)

    def _names(self):
        return sorted(p.name for p in RunPos.folder().glob("*.png"))

    def test_three_refs_per_window_and_thirty_runs(self):
        self.assertEqual((config.RUN_POS_REF_KEEP, config.RUN_POS_RUN_KEEP), (3, 30))
        for i in range(5):
            RunPos.save(self.img, 1, "ref", (100, 80))
            RunPos.save(self.img, 2, "ref", (100, 80))
        for i in range(35):
            RunPos.save(self.img, 1 + i % 3, "run", (100, 80))
        names = self._names()
        self.assertEqual(len([n for n in names if "_窓1_ref_" in n]), 3)
        self.assertEqual(len([n for n in names if "_窓2_ref_" in n]), 3)
        self.assertEqual(len([n for n in names if "_run_" in n]), 30)

    def test_it_is_a_full_size_png(self):
        import cv2
        img = np.random.default_rng(1).integers(0, 255, (30, 40, 3), dtype=np.uint8)
        path = RunPos.save(img, 3, "run", (40, 30))
        back = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(back.shape, img.shape)
        self.assertTrue((back == img).all(), "等倍・劣化なし")

    def test_the_folder_is_next_to_the_settings(self):
        self.assertEqual(RunPos.folder(), Path(config.SETTINGS_PATH).parent / "run_pos")

    def test_the_values(self):
        self.assertEqual((config.RUN_POS_AFTER_ROUNDS, config.RUN_POS_STILL_SPEED, config.RUN_POS_STILL_SEC,
                          config.RUN_POS_GAP_SEC), (3, 0.1, 1.0, 0.5))

    def test_the_difference(self):
        a = np.full((4, 4, 3), 10, np.uint8)
        b = np.full((4, 4, 3), 13, np.uint8)
        self.assertAlmostEqual(RunPos.diff(a, b), 3.0, delta=0.01)
        self.assertIsNone(RunPos.diff(a, np.zeros((2, 2, 3), np.uint8)))
        self.assertIsNone(RunPos.diff(None, a))
