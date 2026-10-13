"""斜めから見た BEGIN を特徴点で見つける（ET。2026-10-13 16:12 窓6）。本物の窓・撮影は使わない。
資料: tests/data/begin/oblique_w6_852x660.png（依頼者のスクショの窓の部分。INTERMISSION の板の小さな文字と
HUD の表は黒く消してある。BEGIN の天板には掛けていない）"""
from tests.support import *  # noqa: F401,F403
import inspect

DATA = Path(__file__).parent / "data"
TRUE_CENTER = (563, 333)            # 依頼者のスクショの BEGIN の本当の中心（およそ）


def _read(path):
    import cv2
    import numpy as np
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def _oblique():
    return _read(DATA / "begin" / "oblique_w6_852x660.png")


def _strip(name):
    import numpy as np
    strip = _read(DATA / "run_respawn" / name)
    img = np.zeros((702, 867, 3), np.uint8)
    img[56:315] = strip
    return img


class TestTheObliqueBegin(unittest.TestCase):
    def test_the_users_shot_is_found_near_the_true_centre(self):
        hit = BeginDetect.find_in_bgr(_oblique())
        self.assertIsNotNone(hit)
        self.assertEqual(hit["rule"], "features")
        self.assertLessEqual(abs(hit["cx"] - TRUE_CENTER[0]), 15, hit)
        self.assertLessEqual(abs(hit["cy"] - TRUE_CENTER[1]), 15, hit)
        self.assertGreaterEqual(hit["inliers"], BeginDetect.PANEL_MIN_INLIERS)
        self.assertTrue(40 <= hit["w"] <= 90, "文字「[ BEGIN ]」の幅（天板や点線の幅ではない）")

    def test_the_template_match_alone_misses_it(self):
        """今の照合だけでは見つからない（それで足した）"""
        self.assertIsNone(BeginDetect._find(_oblique(), BeginDetect._load_templates()))

    def test_it_is_the_same_every_time(self):
        """インライアが少ないので、RANSAC だと回すたびに変わった。MAGSAC で毎回同じ"""
        hits = [BeginDetect._find_by_features(_oblique()) for _ in range(3)]
        self.assertTrue(all(h is not None for h in hits))
        self.assertEqual(len({(round(h["cx"], 3), round(h["cy"], 3), h["inliers"]) for h in hits}), 1)


class TestNoFalseBegin(unittest.TestCase):
    def test_the_same_scene_without_the_panel(self):
        img = _oblique()
        img[295:395, 470:660] = 0           # BEGIN の天板を黒く消す
        self.assertIsNone(BeginDetect._find_by_features(img))

    def test_run_shots_menus_black_and_noise(self):
        import numpy as np
        shots = [_strip(p.name) for p in sorted((DATA / "run_respawn").glob("face_*.png"))]
        shots += [_read(p) for p in sorted((DATA / "run_respawn").glob("menu_*.png"))]
        shots += [_read(DATA / "run_face" / "far_0p8_top.png")]
        shots += [np.zeros((704, 868, 3), np.uint8),
                  np.random.default_rng(3).integers(0, 256, (704, 868, 3), dtype=np.uint8)]
        self.assertGreaterEqual(len(shots), 10)
        for i, shot in enumerate(shots):
            self.assertIsNone(BeginDetect._find_by_features(shot), i)

    def test_every_check_is_needed(self):
        """受け入れの条件を 1 つずつ外すと、BEGIN の無い撮影で何かを拾いうる。条件が全部そろっているか"""
        src = inspect.getsource(BeginDetect._panel_hit)
        for needle in ("cv2.isContourConvex(quad)", "0 <= cx < width and 0 <= cy < height",
                       "PANEL_ASPECT[0] <= tw / th <= PANEL_ASPECT[1]", "PANEL_WIDTH[0] <= tw / height <= PANEL_WIDTH[1]",
                       "< PANEL_RED_MIN"):
            self.assertIn(needle, src)


class TestTheUsualMatchStaysFirst(unittest.TestCase):
    def test_a_begin_found_by_the_template_does_not_use_features(self):
        import cv2
        import numpy as np
        data = np.fromfile(str(config.resource_path(BeginDetect.TEMPLATE_FILES[0])), dtype=np.uint8)
        t = cv2.resize(cv2.imdecode(data, cv2.IMREAD_COLOR), None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        img[480:480 + t.shape[0], 700:700 + t.shape[1]] = t
        with patch.object(BeginDetect, "_find_by_features", side_effect=AssertionError("特徴点を使った")):
            hit = BeginDetect.find_in_bgr(img)
        self.assertIsNotNone(hit)
        self.assertNotEqual(hit["rule"], "features")

    def test_features_only_when_the_template_finds_nothing(self):
        src = inspect.getsource(BeginDetect.find_in_bgr)
        self.assertIn("hit = _find(bgr, tmpl)\n        if hit is None:\n            hit = _find_by_features(bgr)", src)

    def test_the_panel_is_bundled(self):
        self.assertTrue(config.resource_path(BeginDetect.PANEL_FILE).is_file())
        self.assertTrue(BeginDetect.PANEL_FILE.startswith("assets/templates/begin/"))
        self.assertTrue(BeginDetect._load_panel())
        self.assertEqual(tuple(BeginDetect._panel_size), (319, 879))

    def test_a_missing_panel_finds_nothing_by_features(self):
        before = BeginDetect._panel
        self.addCleanup(setattr, BeginDetect, "_panel", before)
        BeginDetect._panel = None
        with patch.object(BeginDetect, "PANEL_FILE", "assets/templates/begin/nope.png"):
            self.assertIsNone(BeginDetect._find_by_features(_oblique()))
        BeginDetect._panel = None


class TestItGoesOnToTheAdjust(unittest.TestCase):
    """見つけた点は今の形（中心・幅）で、今の位置合わせ・押す流れへ渡る"""

    def setUp(self):
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=6, round_seq=1)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x60, osc_port=9000), self.st,
                                                lambda: True, lambda _m: None)
        self.written = []
        p = patch.object(DebugLog, "write", self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def test_the_hit_is_logged_and_gets_the_height(self):
        img = _oblique()
        bits = np.concatenate([img, np.full(img.shape[:2] + (1,), 255, np.uint8)], axis=2).tobytes() \
            if (np := __import__("numpy")) else b""
        with patch.object(WindowOperator, "aim_in_window_image", return_value=(426, 330)), \
             patch.object(ScreenCapture, "capture_window", return_value=(bits, 852, 660)), \
             patch.object(BeginMiss, "save"):
            hit, aim_x = self.ex._look_for_begin("最初")
        self.assertEqual(hit["rule"], "features")
        self.assertEqual(hit["H"], 660)
        self.assertEqual(aim_x, 426)
        self.assertTrue(any("[窓6] Begin: 斜めの BEGIN を特徴点で見つけた（最初・中心 (" in m for m in self.written),
                        self.written)

    def test_the_adjust_moves_toward_it(self):
        """照準（426）より右に BEGIN（553）→ 今の位置合わせが右へ寄せる"""
        hit = {"score": 0.11, "dark": None, "cx": 553.0, "cy": 335.0, "w": 61.0, "h": 9.0,
               "rule": "features", "inliers": 11, "H": 660}
        moves = []
        seen = [hit]

        def look(*_a):
            # 1 回目は右寄り。動いた後は照準の上・幅も押せる範囲（0.12）
            return (seen.pop(0) if seen else {**hit, "cx": 428.0, "w": 0.12 * 660}), 426
        with patch.object(BeginDetect, "available", return_value=True), \
             patch.object(self.ex, "_look_for_begin", side_effect=look), \
             patch.object(self.ex, "_adjust_stopped", return_value=False), \
             patch.object(self.ex, "move", side_effect=lambda d, s: moves.append(d)), \
             patch.object(ActionExecutor.time, "sleep"):
            result = self.ex._adjust_to_begin(1)
        self.assertEqual(moves[0], "right")
        self.assertEqual(result, "aimed")


class TestTheChosenValues(unittest.TestCase):
    """値は手元の撮影 63 枚（BEGIN が写っている 10・無い 51・端 2）で比べて決めた（ほかのプレイヤーが写りうるので
    資料には入れていない）。グレー・2 倍・見本 1.0 と 0.75・比 0.75・インライア 6・MAGSAC で、写っている 3 枚を拾い
    誤検出 0。RANSAC は回すたびに結果が変わり、見本 1 つ・拡大なしは拾う数が減った"""

    def test_the_values(self):
        self.assertEqual((BeginDetect.PANEL_SCALES, BeginDetect.PANEL_UP, BeginDetect.PANEL_MAX_H,
                          BeginDetect.PANEL_RATIO, BeginDetect.PANEL_RANSAC_PX, BeginDetect.PANEL_MIN_INLIERS),
                         ((1.0, 0.75), 2.0, 1500, 0.75, 5.0, 6))
        self.assertEqual((BeginDetect.PANEL_RED_MIN, BeginDetect.PANEL_ASPECT, BeginDetect.PANEL_WIDTH),
                         (0.15, (2.0, 12.0), (0.03, 1.0)))
        self.assertEqual(BeginDetect.PANEL_TEXT, (155, 161, 629, 232))

    def test_magsac_not_ransac(self):
        src = inspect.getsource(BeginDetect._find_by_features)
        self.assertIn("cv2.USAC_MAGSAC", src)
        self.assertNotIn("cv2.RANSAC", src)
