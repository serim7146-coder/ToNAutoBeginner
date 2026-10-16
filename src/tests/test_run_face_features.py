"""FK: Run の向き合わせを、絵の照合から特徴点（SIFT＋ホモグラフィ）に替える（依頼者 2026-10-16）。
目印は玉座の軸の x。特徴点で受け入れられなかったときだけ今の絵の照合（保険）へ進む。
資料は tests/data/run_face/（名前が写る所は黒く塗ってある。見つかるべき撮影と、見つかってはいけない撮影）。
本物の OSC・入力は使わない（偽の OSC・偽の撮影だけ）"""
from tests.support import *  # noqa: F401,F403
import RunFace

DATA = Path(__file__).parent / "data" / "run_face"

# 見つかるべき撮影: (ファイル, 目視の軸の x)。目視は、赤い紋章（玉座の上の赤い点）の重心と階段の真ん中から確かめた
# （紋章の x と検出した軸の差は 等倍の撮影で幅の 0.2〜0.4%・半分の大きさの撮影でも 2.2% 以内）。許すのは幅の ±2%
POSITIVES = (
    ("run_after_w5_867x703.png", 452),       # 依頼者の Run 後の撮影（851×664 の窓。題名バー込みで 867×703）
    ("run_after_w6_868x703.png", 447),
    ("run_after_w4_868x703.png", 431),       # 倍率 1.18（近い）
    ("run_after_w1_near_868x703.png", 417),  # 倍率 1.41（近い。いつもの 1.0 の絵の外）
    ("fov75_2000x1125.webp", 1008),          # 依頼者の FOV 75（2000×1125。名札・HUD の名前は黒く塗った）
    ("reporter_a_960x540.jpg", 504),         # 報告者（1920×1080 の窓の半分）
    ("reporter_b_960x540.jpg", 466),
    ("half_w2_434x352.jpg", 216),            # 絵の照合が見つけなかった半分の大きさの撮影（インライアが少ない側）
    ("half_w4_a_434x352.jpg", 218),
    ("half_w6_434x352.jpg", 213),
)
# 見つかってはいけない撮影: 台の前・店の画面・Begin の位置合わせの途中・壁を向いていた 19:38 窓1・ほか（tests/data にあるもの）
NEGATIVES = (
    "neg_begin_miss_w6_868x703.png", "neg_begin_miss_w4_868x703.png", "neg_shop_a_434x352.jpg",
    "neg_shop_b_434x352.jpg", "neg_begin_adjust_434x352.jpg", "neg_wall_1938_1280x720.jpg",
    "../begin/oblique_w6_852x660.png", "../saveload/oblique_33_empty_2000x1125.webp",
)


def _read(name):
    import cv2
    import numpy as np
    img = cv2.imdecode(np.fromfile(str(DATA / name), np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


def _raw_inliers(img):
    """受け入れの閾値を外して、ホモグラフィのインライア数（形の条件は通ったもの）を測る。見つからなければ None"""
    with patch.object(config, "RUN_FACE_FEATURE_MIN_INLIERS", 0):
        hit = RunFace.find_by_features(img)
    return None if hit is None else hit[1]


def _embed(scale, size=(703, 867), at=(293, 170)):
    """玉座の切り出し（throne.png）を scale 倍にして黒い撮影の at（左上 x, y）に貼る。軸は at.x + 135 × scale"""
    import cv2
    import numpy as np
    ref = cv2.imdecode(np.fromfile(str(config.resource_path(RunFace.THRONE_FILE)), np.uint8), cv2.IMREAD_COLOR)
    big = cv2.resize(ref, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    canvas = np.zeros((*size, 3), np.uint8)
    h, w = big.shape[:2]
    canvas[at[1]:at[1] + h, at[0]:at[0] + w] = big
    return canvas


class TestTheReference(unittest.TestCase):
    def test_the_picture_and_the_axis_agree(self):
        """切り出しは 270×270。軸（切り出し元の撮影の x 428 − 左端 293 = 135）は切り出しの中。高さ 703 は元の撮影"""
        import cv2
        import numpy as np
        path = Path(config.resource_path(RunFace.THRONE_FILE))
        self.assertTrue(path.is_file())
        self.assertTrue((Path(RunFace.__file__).parent.parent / RunFace.THRONE_FILE).is_file(), "assets の中（EK）")
        self.assertTrue(RunFace.THRONE_FILE.startswith("assets/"))
        img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape[:2], (270, 270))
        self.assertEqual((RunFace.THRONE_AXIS_X, RunFace.THRONE_REF_HEIGHT), (135, 703))
        self.assertEqual(RunFace.THRONE_AXIS_X, 428 - 293)
        self.assertTrue(0 < RunFace.THRONE_AXIS_X < img.shape[1])
        self.assertEqual(RunFace.BASE_HEIGHT, 702, "絵の照合の側は変えない")

    def test_the_picture_itself_is_found_on_its_axis(self):
        """切り出し元の撮影と同じ位置（x 293・y 170）に貼ると、軸は x 428・倍率は 1.0（いつもの位置・FOV 100）"""
        shot = _embed(1.0)
        hit = RunFace.find_by_features(shot)
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[0], 428, delta=1.0)
        self.assertGreater(hit[1], 200)
        mark = RunFace.find_mark(shot)
        self.assertAlmostEqual(RunFace.factor(mark, shot.shape[0]), 1.0, delta=0.03)

    def test_the_axis_and_the_factor_follow_the_size(self):
        """倍率を決めずに見つかる: 2 倍に貼った玉座（FOV が狭い・撮影が大きいのと同じ）の軸と倍率。
        撮影の高さが違っても、倍率は 撮影の高さ / 703 で割った見かけの大きさ"""
        shot = _embed(2.0, size=(1125, 2000), at=(700, 300))
        mark = RunFace.find_mark(shot)
        self.assertIsNotNone(mark)
        self.assertEqual(RunFace.how(mark), "features")
        self.assertAlmostEqual(mark[0], 700 + 135 * 2.0, delta=2.0)
        self.assertAlmostEqual(mark[2], 2.0 * 703 / 702, delta=0.06, msg="mark[2] は絵の照合と同じ単位（高さを掛けたもの）")
        self.assertAlmostEqual(RunFace.factor(mark, shot.shape[0]), 2.0 / (1125 / 703), delta=0.06)
        small = _embed(0.6, size=(540, 960), at=(300, 100))
        mark = RunFace.find_mark(small)
        self.assertIsNotNone(mark, "0.6 倍（絵の照合の 0.85〜1.15 の外）でも見つかる")
        self.assertAlmostEqual(mark[0], 300 + 135 * 0.6, delta=2.0)


class TestOnRealShots(unittest.TestCase):
    def test_every_positive_is_found_on_the_axis(self):
        for name, x in POSITIVES:
            img = _read(name)
            hit = RunFace.find_by_features(img)
            self.assertIsNotNone(hit, name)
            self.assertAlmostEqual(hit[0], x, delta=img.shape[1] * 0.02, msg=name)
            self.assertGreaterEqual(hit[1], config.RUN_FACE_FEATURE_MIN_INLIERS, name)

    def test_find_mark_gives_the_four_values(self):
        for name, x in POSITIVES:
            img = _read(name)
            mark = RunFace.find_mark(img)
            self.assertEqual(len(mark), 4, name)
            self.assertEqual(mark[3], "features", name)
            self.assertAlmostEqual(mark[0], x, delta=img.shape[1] * 0.02, msg=name)
            self.assertIsInstance(mark[1], int, name)
            self.assertGreater(mark[2], 0.2, name)
            self.assertEqual(RunFace.how(mark), "features")
            self.assertEqual(RunFace.describe(mark), f"特徴点 インライア {mark[1]}")

    def test_the_factor_follows_the_fov(self):
        """FOV 75 は FOV 100 の 1.55 倍に見える（依頼者）。いつもの位置に近い撮影は 1.0 前後"""
        fov = _read("fov75_2000x1125.webp")
        mark = RunFace.find_mark(fov)
        self.assertAlmostEqual(RunFace.factor(mark, fov.shape[0]), 1.55, delta=0.3)
        usual = RunFace.find_mark(_read("run_after_w5_867x703.png"))
        self.assertAlmostEqual(RunFace.factor(usual, 703), 1.0, delta=0.15)

    def test_no_negative_is_found(self):
        for name in NEGATIVES:
            img = _read(name)
            self.assertIsNone(RunFace.find_by_features(img), name)
            self.assertIsNone(RunFace.find_mark(img), name)

    def test_the_threshold_sits_between_the_two_sides(self):
        """受け入れの閾値（40）の根拠: 見つかるべき各枚のインライア数の最小と、見つかってはいけない各枚の最大の間。
        閾値の両側に 10 以上の余裕（記録は config のコメント）"""
        pos = {name: _raw_inliers(_read(name)) for name, _x in POSITIVES}
        neg = {name: _raw_inliers(_read(name)) for name in NEGATIVES}
        self.assertTrue(all(v is not None for v in pos.values()), pos)
        low = min(pos.values())
        high = max(v for v in neg.values() if v is not None)
        self.assertEqual(config.RUN_FACE_FEATURE_MIN_INLIERS, 40)
        self.assertGreaterEqual(low - config.RUN_FACE_FEATURE_MIN_INLIERS, 10, f"正例の最小 {low}（{pos}）")
        self.assertGreaterEqual(config.RUN_FACE_FEATURE_MIN_INLIERS - high, 10, f"負例の最大 {high}（{neg}）")

    def test_the_big_shots_have_plenty(self):
        """等倍・FOV 75・報告者の撮影は 100 以上（半分の大きさの撮影だけが 50 台）"""
        for name, _x in POSITIVES:
            if name.startswith("half_"):
                continue
            self.assertGreaterEqual(_raw_inliers(_read(name)), 100, name)

    def test_dark_empty_and_odd_inputs_are_none(self):
        import numpy as np
        for img in (np.zeros((664, 851, 3), np.uint8), np.zeros((10, 10, 3), np.uint8),
                    np.zeros((664, 851), np.uint8), np.full((664, 851, 3), 255, np.uint8), None, "x"):
            self.assertIsNone(RunFace.find_by_features(img))
        self.assertIsNone(RunFace.find_mark(np.zeros((664, 851, 3), np.uint8)))
        self.assertIsNone(RunFace.find_mark(None))

    def test_the_reference_is_made_once(self):
        first = RunFace._load_throne()
        self.assertTrue(first)
        self.assertIs(RunFace._load_throne(), first)
        with patch("cv2.SIFT_create", side_effect=AssertionError("参照は作り直さない")) as sift:
            sift.return_value = None
            self.assertIs(RunFace._load_throne(), first)

    def test_an_unreadable_picture_is_none_not_an_error(self):
        with patch.object(RunFace, "_throne", ()):
            self.assertIsNone(RunFace.find_by_features(_read("run_after_w5_867x703.png")))
        with patch.object(RunFace, "_throne", None), patch.object(config, "resource_path",
                                                                   return_value=DATA / "no_such_file.png"):
            self.assertIsNone(RunFace.find_by_features(_read("run_after_w5_867x703.png")))
            self.assertEqual(RunFace._load_throne(), ())


class _Homography:
    """本物の特徴点の対応は使い、ホモグラフィだけを決めた値に差し替える（受け入れの条件を 1 つずつ確かめる）"""

    def __init__(self, test, matrix, inliers=100):
        import numpy as np
        self.matrix = np.array(matrix, np.float64)
        self.inliers = inliers

        def fake(src, dst, method, threshold):
            mask = np.zeros((len(src), 1), np.uint8)
            mask[:self.inliers] = 1
            return self.matrix, mask
        p = patch("cv2.findHomography", side_effect=fake)
        p.start()
        test.addCleanup(p.stop)


def _shift(scale=1.0, tx=300, ty=100):
    return [[scale, 0, tx], [0, scale, ty], [0, 0, 1]]


class TestTheAcceptance(unittest.TestCase):
    """受け入れ: インライア ≥ 40・四隅が凸・枠の幅が撮影の幅の 10〜95%・軸の点が撮影の中"""

    def setUp(self):
        self.shot = _read("run_after_w5_867x703.png")           # 867×703

    def _hit(self, matrix, inliers=100, shot=None):
        _Homography(self, matrix, inliers)
        return RunFace.find_by_features(self.shot if shot is None else shot)

    def test_a_good_one_is_accepted(self):
        hit = self._hit(_shift(1.0, 300, 100))
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[0], 300 + 135, delta=1e-3)
        self.assertEqual(hit[1], 100)
        self.assertAlmostEqual(hit[2], 1.0 * 703 / 702, delta=1e-6)

    def test_the_scale_is_the_size_of_the_mapped_picture(self):
        for s in (0.4, 1.5, 2.5):
            hit = self._hit(_shift(s, 100, 50))
            self.assertAlmostEqual(hit[2], s * 703 / 702, delta=1e-6, msg=s)
        rotated = [[0.6, -0.8, 400], [0.8, 0.6, 100], [0, 0, 1]]        # 回しても 大きさは行列式から
        self.assertAlmostEqual(self._hit(rotated)[2], 703 / 702, delta=1e-6)

    def test_inliers_39_is_not_and_40_is(self):
        self.assertIsNone(self._hit(_shift(), 39))
        self.assertIsNotNone(self._hit(_shift(), 40))

    def test_inliers_alone_are_not_enough_a_collapsed_frame_is_refused(self):
        """2026-10-16 19:38 窓1: 玉座が写っていないのに「インライア 72」。写した四隅が 1 点につぶれていた（幅 0）"""
        self.assertIsNone(self._hit([[0, 0, 300], [0, 0, 100], [0, 0, 1]], 72))
        self.assertIsNone(self._hit([[1e-9, 0, 300], [0, 1e-9, 100], [0, 0, 1]], 72), "ほぼ 1 点")

    def test_the_frame_width_must_be_10_to_95_percent_of_the_shot(self):
        w = self.shot.shape[1]
        for s, ok in ((0.30, False), (0.31, False), (0.33, True), (0.40, True), (3.0, True), (3.1, False), (3.8, False)):
            hit = self._hit(_shift(s, 20 if s > 3 else 300, 20))
            self.assertEqual(hit is not None, ok, f"scale {s}（幅 {270 * s / w:.1%}）")

    def test_the_width_is_the_width_not_the_height(self):
        """幅の条件は横の長さ（縦に伸ばした細い枠は弾き、横に長い浅い枠は通す）"""
        self.assertIsNone(self._hit([[0.2, 0, 300], [0, 3, 20], [0, 0, 1]]), "幅 6%・高さ 810")
        self.assertIsNotNone(self._hit([[1.0, 0, 300], [0, 0.2, 100], [0, 0, 1]]), "幅 31%・高さ 54")

    def test_a_frame_that_is_not_convex_is_refused(self):
        """四隅が凸でない（ホモグラフィが地平線をまたぐ）→ 幅と軸の条件を外しても、これだけで弾く"""
        fov = _read("fov75_2000x1125.webp")
        bent = [[1, 0, 300], [0, 1, 100], [-0.004, 0, 1]]
        with patch.object(config, "RUN_FACE_FEATURE_WIDTH", (0.0, 1e9)):
            self.assertIsNone(self._hit(bent, shot=fov))
            _good = [[1, 0, 300], [0, 1, 100], [0.005, 0, 1]]
            self.assertIsNotNone(self._hit(_good, shot=fov), "同じ条件で凸なら受け入れる")

    def test_the_axis_must_be_inside_the_shot(self):
        w, h = self.shot.shape[1], self.shot.shape[0]
        self.assertIsNotNone(self._hit(_shift(1.0, w - 136, 100)), "軸 x = w − 1")
        self.assertIsNone(self._hit(_shift(1.0, w - 135, 100)), "軸 x = w")
        self.assertIsNone(self._hit(_shift(1.0, 800, 100)), "右へはみ出す")
        self.assertIsNone(self._hit(_shift(1.0, -136, 100)), "軸 x = −1")
        self.assertIsNotNone(self._hit(_shift(1.0, -135, 100)), "軸 x = 0")
        self.assertIsNone(self._hit(_shift(1.0, 300, h - 134)), "軸 y = h + 1（枠の中心の高さ 135）")
        self.assertIsNone(self._hit(_shift(1.0, 300, -136)), "軸 y = −1")

    def test_the_axis_follows_the_picture_not_the_frame(self):
        """軸は切り出しの x 135（真ん中の 135 でなく、軸の定数）を写した点"""
        with patch.object(RunFace, "THRONE_AXIS_X", 100):
            self.assertAlmostEqual(self._hit(_shift(1.0, 300, 100))[0], 400, delta=1e-3)

    def test_too_few_matches_is_none(self):
        _Homography(self, _shift())
        with patch.object(config, "RUN_FACE_FEATURE_MIN_MATCHES", 10 ** 6):
            self.assertIsNone(RunFace.find_by_features(self.shot))

    def test_no_homography_is_none(self):
        with patch("cv2.findHomography", return_value=(None, None)):
            self.assertIsNone(RunFace.find_by_features(self.shot))

    def test_the_ratio_test_uses_the_config_value(self):
        with patch.object(config, "RUN_FACE_FEATURE_RATIO", 0.0):
            self.assertIsNone(RunFace.find_by_features(self.shot), "どれも ratio を通らない")

    def test_the_values_are_pinned(self):
        """ハイパーパラメーター（根拠は config のコメントと 2.5 の表）。変えるなら資料で測り直す"""
        self.assertEqual((config.RUN_FACE_FEATURE_MIN_INLIERS, config.RUN_FACE_FEATURE_MIN_MATCHES,
                          config.RUN_FACE_FEATURE_RATIO, config.RUN_FACE_FEATURE_RANSAC_PX,
                          config.RUN_FACE_FEATURE_WIDTH), (40, 8, 0.75, 4.0, (0.10, 0.95)))
        self.assertEqual((RunFace.FEATURE_MIN_H, RunFace.FEATURE_REF_H, RunFace.FEATURE_CLAHE, RunFace.FEATURE_CONTRAST),
                         (1000, 600, (3.0, (8, 8)), 0.02))
        src = Path(config.__file__).read_text(encoding="utf-8")
        self.assertIn("閾値の根拠", src)
        self.assertIn("負例から +16・正例から −8", src)

    def test_the_homography_is_asked_for_with_magsac_4px(self):
        import cv2
        calls = []
        real = cv2.findHomography

        def spy(src, dst, method, threshold):
            calls.append((method, threshold))
            return real(src, dst, method, threshold)
        with patch("cv2.findHomography", side_effect=spy):
            RunFace.find_by_features(self.shot)
        self.assertEqual(calls, [(cv2.USAC_MAGSAC, 4.0)])

    def test_small_shots_are_enlarged_to_1000_and_big_ones_are_not(self):
        import numpy as np
        sizes = []
        real = RunFace._feature_gray

        def spy(gray, min_h):
            out = real(gray, min_h)
            sizes.append((gray.shape[0], out[0].shape[0], min_h))
            return out
        with patch.object(RunFace, "_feature_gray", side_effect=spy):
            RunFace.find_by_features(np.zeros((664, 851, 3), np.uint8))
            RunFace.find_by_features(np.zeros((1080, 1920, 3), np.uint8))
        self.assertEqual(sizes, [(664, 1000, 1000), (1080, 1080, 1000)])
        self.assertEqual(real(np.zeros((270, 270), np.uint8), RunFace.FEATURE_REF_H)[0].shape[0], 600, "参照は高さ 600")


class TestFallsBackToThePicture(unittest.TestCase):
    """特徴点で受け入れられなかったときだけ、今の絵の照合（0.85〜1.15・一致 0.8）へ進む"""

    def test_accepted_never_looks_at_the_picture(self):
        with patch.object(RunFace, "find_by_features", return_value=(300.5, 120, 1.2)), \
             patch.object(RunFace, "_search", side_effect=AssertionError("絵の照合は使わない")):
            self.assertEqual(RunFace.find_mark(object()), (300.5, 120, 1.2, "features"))

    def test_refused_goes_to_the_picture_with_the_same_settings_as_before(self):
        with patch.object(RunFace, "find_by_features", return_value=None), \
             patch.object(RunFace, "_search", return_value=(433.0, 0.93, 1.02)) as search:
            mark = RunFace.find_mark("shot", "tmpl")
        self.assertEqual(mark, (433.0, 0.93, 1.02, "template"))
        search.assert_called_once_with("shot", "tmpl", RunFace.SCALES, fine=True, threshold=True)
        self.assertEqual(RunFace.describe(mark), "一致 0.930")

    def test_both_none_is_none(self):
        with patch.object(RunFace, "find_by_features", return_value=None), \
             patch.object(RunFace, "_search", return_value=None):
            self.assertIsNone(RunFace.find_mark("shot"))

    def test_an_error_in_the_features_goes_to_the_picture(self):
        """特徴点の中で例外が出ても止めず、絵の照合へ（debug.log に書く）"""
        with patch.object(RunFace, "_load_throne", side_effect=RuntimeError("boom")), \
             patch.object(DebugLog, "exception") as logged, \
             patch.object(RunFace, "_search", return_value=(1.0, 0.9, 1.0)):
            mark = RunFace.find_mark(_read("run_after_w5_867x703.png"))
        self.assertEqual(mark, (1.0, 0.9, 1.0, "template"))
        logged.assert_called_once_with("RunFace.find_by_features")

    def test_the_picture_is_still_found_when_the_features_are_refused(self):
        """絵の照合の側は今まで通り（正面の撮影の帯）"""
        import numpy as np
        strip = _strip("face_now_867x702_rows_56_315.png")
        with patch.object(RunFace, "find_by_features", return_value=None):
            mark = RunFace.find_mark(strip)
        self.assertEqual(mark[3], "template")
        self.assertAlmostEqual(mark[0], 433, delta=5)
        self.assertEqual(len(np.shape(mark)), 1)

    def test_the_record_only_search_is_as_before(self):
        wide = RunFace.best_anywhere(_strip("face_now_867x702_rows_56_315.png"))
        self.assertEqual(len(wide), 3)
        self.assertEqual(RunFace.how(wide), "template")


def _strip(name):
    import cv2
    import numpy as np
    strip = cv2.imdecode(np.fromfile(str(DATA.parent / "run_respawn" / name), np.uint8), cv2.IMREAD_COLOR)
    img = np.zeros((702, 867, 3), np.uint8)
    img[56:315] = strip
    return img


class _Osc:
    def __init__(self):
        self.presses = []

    def press(self, address, sec, stop=None):
        self.presses.append((address, round(sec, 4)))

    def stop_all(self, repeat=1):
        pass


class TestTheFlowAndTheLog(unittest.TestCase):
    """ActionExecutor の「向き:」の流れは同じ。ログの「一致」だけ、特徴点なら「特徴点 インライア n」"""

    def setUp(self):
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, in_round=True, round_seq=5,
                              round_type="Run", window_idx=1)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x40, osc_port=9000), self.st, lambda: True,
                                                lambda _m: None)
        self.written = []
        self.saved = []
        fake_time = type(sys)("time_for_test")
        fake_time.time = time.time
        fake_time.monotonic = time.monotonic
        fake_time.sleep = lambda s: None
        for p in (patch.object(ActionExecutor, "time", fake_time),
                  patch.object(DebugLog, "write", side_effect=self.written.append),
                  patch.object(RunFace, "save_shot", side_effect=lambda shot, w, why: self.saved.append((w, why)))):
            p.start()
            self.addCleanup(p.stop)
        self.ex._osc = _Osc()

    def _face(self, shot, marks):
        marks = iter(marks)
        self.ex._capture_bgr = lambda: shot
        with patch.object(RunFace, "find_mark", side_effect=lambda _s: next(marks)):
            return self.ex._face_the_stairs(self.st.round_seq)

    def test_the_log_says_features_with_the_inliers(self):
        import numpy as np
        shot = np.zeros((703, 867, 3), np.uint8)
        self.assertEqual(self._face(shot, [(433.5, 123, 1.02, "features")]), 0)
        line = "[操作] [窓1] Run: 向き: 目印 x 433.5（幅 867・特徴点 インライア 123・倍率 1.02）・ずれ +0.0"
        self.assertIn(line, self.written)
        self.assertIn("[操作] [窓1] Run: 向き: 正面（直した 0 回）", self.written)
        self.assertFalse(any("一致" in str(m) for m in self.written), self.written)

    def test_the_log_for_the_picture_is_as_before(self):
        import numpy as np
        shot = np.zeros((703, 867, 3), np.uint8)
        self._face(shot, [(433.5, 0.9, 1.0, "template")])
        self.assertIn("[操作] [窓1] Run: 向き: 目印 x 433.5（幅 867・一致 0.900・倍率 1.00）・ずれ +0.0", self.written)
        self.written.clear()
        self._face(shot, [(433.5, 0.9, 1.0)])           # 4 つ目が無い形（今までの 3 つ組）も通る
        self.assertIn("[操作] [窓1] Run: 向き: 目印 x 433.5（幅 867・一致 0.900・倍率 1.00）・ずれ +0.0", self.written)

    def test_it_turns_by_the_axis_like_before(self):
        """ずれ → 回す向きと秒数は今までと同じ（軸が右なら LookRight・RUN_FACE_RATE × 幅で割る）"""
        import numpy as np
        shot = np.zeros((703, 867, 3), np.uint8)
        fixed = self._face(shot, [(433.5 + 80, 150, 1.0, "features"), (433.5 + 5, 150, 1.0, "features")])
        self.assertEqual(fixed, 1)
        self.assertEqual(self.ex._osc.presses, [("/input/LookRight", round(80 / (1.22 * 867), 4))])
        self.assertIn("[操作] [窓1] Run: 向き: 正面（直した 1 回）", self.written)
        self.assertEqual(self.ex._last_face[1][3], "features")

    def test_not_found_is_as_before(self):
        import numpy as np
        shot = np.zeros((703, 867, 3), np.uint8)
        self.assertEqual(self._face(shot, [None]), 0)
        self.assertIn("[操作] [窓1] Run: 向き: 目印が見つからない → 今の向きのまま", self.written)
        self.assertEqual(self.saved, [(1, "notfound")], "見つからなかったときの撮影は今どおり残す")
        self.assertEqual(self.ex._osc.presses, [])

    def test_the_depth_log_says_it_too(self):
        import numpy as np
        shot = np.zeros((703, 867, 3), np.uint8)
        self.ex._last_face = (shot, (433.5, 123, 1.55, "features"))
        self.assertIsNone(self.ex._fix_depth(5))
        f = RunFace.factor((433.5, 123, 1.55, "features"), 703)
        self.assertIn(f"[操作] [窓1] Run: 前後: 倍率 {f:.3f}（特徴点 インライア 123）（記録だけ・動かさない）", self.written)

    def test_a_real_shot_goes_through_measure_and_the_flow(self):
        """偽の撮影の代わりに本物の撮影（FOV 75）で 測る → 向きを見る（押さずに正面と見るか、ずれの向きに回す）"""
        shot = _read("fov75_2000x1125.webp")
        self.ex._capture_bgr = lambda: shot
        got_shot, mark = self.ex._measure_face()
        self.assertIs(got_shot, shot)
        self.assertEqual(mark[3], "features")
        dx = mark[0] - shot.shape[1] / 2
        self.assertLess(abs(dx), shot.shape[1] * 0.03, "依頼者の FOV 75 は正面を向いている")
        self.assertEqual(self._face_real(shot), 0)
        self.assertEqual(self.ex._osc.presses, [])
        self.assertTrue(any("特徴点 インライア" in str(m) for m in self.written), self.written)

    def _face_real(self, shot):
        self.ex._capture_bgr = lambda: shot
        return self.ex._face_the_stairs(self.st.round_seq)

    def test_a_turned_real_shot_turns_toward_the_front(self):
        """玉座が左へ寄った撮影（右へ 0.1 秒回した帯）→ 左へ回す（LookLeft）"""
        shot = _strip("face_calR_0.1_867x702_rows_56_315.png")      # 軸 x ≈ 324・幅 867 → 左
        got = self._face_real_limited(shot)
        self.assertEqual(got[0][0], "/input/LookLeft")

    def _face_real_limited(self, shot):
        """1 回だけ押して止める（撮影は同じ絵のまま）"""
        self.ex._capture_bgr = lambda: shot
        osc = self.ex._osc
        orig = osc.press

        def press(address, sec, stop=None):
            orig(address, sec, stop)
            self.ex._run_after_respawn = lambda seq: False
        osc.press = press
        self.ex._face_the_stairs(self.st.round_seq)
        return osc.presses
