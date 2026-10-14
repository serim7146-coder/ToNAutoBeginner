"""EX: 斜めから見たコード欄を、特徴点（SIFT＋ホモグラフィ）で見つける。2026-10-14 15:44 窓1・15:45 窓2 は
Save/Load の板が画面の右端に斜めに見え、今の照合（一致 0.628）で見つからずにコードを読み込まなかった。

資料は依頼者のスクショ 33（2560×1440 の窓を 2000×1125 に縮めたもの。**コード欄は空**。写っている名前は依頼者
本人のものだけ）と、今の照合の見本（正面の切り出し。欄は空）。コードが写った撮影は使わない。
本物の撮影・前面・入力は使わない"""
from tests.support import *  # noqa: F401,F403
import re
import cv2
import numpy as np
import SaveLoad
from tests.test_saveload import _World, CODE, FOUND

DATA = Path(__file__).parent / "data"
# スクショ 33 の上のコード欄の四隅（左上・右上・右下・左下。右は画面の端 x=2000 で切れている）
BOX_33 = np.float32([[1493, 537], [2000, 521.5], [2000, 613.5], [1493, 581]])


def _read(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def _shot33():
    return _read(DATA / "saveload" / "oblique_33_empty_2000x1125.webp")


def _inside(point, quad):
    return cv2.pointPolygonTest(np.float32(quad).reshape(-1, 1, 2), (float(point[0]), float(point[1])), False) > 0


def _front(w, h):
    """正面: 今の照合の見本（2560×1440 の x1900〜2150・y700〜840）を黒い画面の元の場所に貼り、w×h に縮める。
    見本の中の欄は x 15〜（右は見本の外）・y 35〜92"""
    tpl = _read(Path(config.resource_path(SaveLoad.CODEBOX_FILE)))
    img = np.zeros((1440, 2560, 3), np.uint8)
    img[700:840, 1900:2150] = tpl
    k = w / 2560
    box = np.float32([[1915, 735], [2150, 735], [2150, 792], [1915, 792]]) * k
    return (img if k == 1 else cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)), box


class TestTheObliqueShot(unittest.TestCase):
    def test_33_gives_a_point_in_the_left_of_the_real_box(self):
        x, y, inliers, s, how = SaveLoad.find_codebox(_shot33(), 1125)
        self.assertEqual(how, "features")
        self.assertTrue(_inside((x, y), BOX_33), (x, y))
        self.assertLess(x, 1493 + 0.2 * (2000 - 1493), "左寄り（画面の外に出ている右側は押さない）")
        self.assertGreaterEqual(inliers, SaveLoad.PANEL_MIN_INLIERS)

    def test_the_template_alone_misses_it(self):
        """今の照合だけでは 0.8 に届かない（15:44 の窓1 と同じ）"""
        hit = SaveLoad._template(cv2.resize(_shot33(), (2560, 1440), interpolation=cv2.INTER_CUBIC), 1440)
        self.assertLess(hit[2], SaveLoad.MATCH_MIN)

    def test_other_window_sizes(self):
        for w, h in ((2560, 1440), (1280, 720), (852, 479)):
            img = cv2.resize(_shot33(), (w, h), interpolation=cv2.INTER_CUBIC if w > 2000 else cv2.INTER_AREA)
            found = SaveLoad.find_codebox(img, h)
            self.assertIsNotNone(found, (w, h))
            self.assertEqual(found[4], "features")
            self.assertTrue(_inside(found[:2], BOX_33 * (w / 2000)), (w, h, found[:2]))

    def test_another_eye_height(self):
        """目の高さが違う見え方の代わりに、33 を上下に台形にゆがめる（依頼者の身長の見立て）"""
        img = _shot33()
        h, w = img.shape[:2]
        for k in (-0.12, -0.06, 0.06, 0.12):
            d = abs(k) * w
            src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
            dst = (np.float32([[-d, 0], [w + d, 0], [w, h], [0, h]]) if k > 0
                   else np.float32([[0, 0], [w, 0], [w - d, h], [d, h]]))
            m = cv2.getPerspectiveTransform(src, dst)
            found = SaveLoad.find_codebox(cv2.warpPerspective(img, m, (w, h)), h)
            self.assertIsNotNone(found, k)
            self.assertEqual(found[4], "features", k)
            box = cv2.perspectiveTransform(BOX_33.reshape(-1, 1, 2), m).reshape(-1, 2)
            self.assertTrue(_inside(found[:2], box), (k, found[:2]))


class TestTheFrontShot(unittest.TestCase):
    def test_features_find_the_front_box_too(self):
        for w, h in ((2560, 1440), (1280, 720)):
            img, box = _front(w, h)
            found = SaveLoad.find_codebox(img, h)
            self.assertIsNotNone(found, (w, h))
            self.assertEqual(found[4], "features", (w, h))
            self.assertTrue(_inside(found[:2], box), (w, h, found[:2]))

    def test_when_features_are_not_accepted_the_template_is_tried(self):
        img, box = _front(1280, 720)
        with patch.object(SaveLoad, "_features", return_value=(None, 3)):
            found = SaveLoad.find_codebox(img, 720)
        self.assertEqual(found[4], "template")
        self.assertGreaterEqual(found[2], SaveLoad.MATCH_MIN)
        self.assertTrue(_inside(found[:2], box), found[:2])

    def test_when_features_are_accepted_the_template_is_not_tried(self):
        with patch.object(SaveLoad, "_template") as template:
            found = SaveLoad.find_codebox(_shot33(), 1125)
        self.assertEqual(found[4], "features")
        template.assert_not_called()

    def test_without_the_panel_file_the_template_still_works(self):
        before = SaveLoad._panel
        self.addCleanup(setattr, SaveLoad, "_panel", before)
        SaveLoad._panel = None
        with patch.object(SaveLoad, "PANEL_FILE", "assets/templates/saveload/nope.png"), \
             patch.object(DebugLog, "exception"):
            img, box = _front(1280, 720)
            found = SaveLoad.find_codebox(img, 720)
        self.assertEqual(found[4], "template")
        SaveLoad._panel = None


class TestNoFalseFinds(unittest.TestCase):
    """板の無い撮影（Begin・Run の向き・Run のリスポーン・入力欄の撮影）では見つけない。板の下半分だけ写った
    入力欄の撮影は、クリックの点が画面の外なのではじく"""

    def test_the_shots_without_the_box(self):
        paths = sorted(p for d in ("begin", "run_face", "run_respawn", "saveload") for p in (DATA / d).glob("*.png"))
        self.assertGreaterEqual(len(paths), 13)
        for p in paths:
            img = _read(p)
            self.assertIsNone(SaveLoad._features(img)[0], p.name)

    def test_noise_and_black(self):
        rng = np.random.default_rng(3)
        self.assertIsNone(SaveLoad._features((rng.random((720, 1280, 3)) * 255).astype(np.uint8))[0])
        self.assertIsNone(SaveLoad.find_codebox(np.zeros((720, 1280, 3), np.uint8), 720))

    def test_a_point_out_of_the_shot_is_none(self):
        """板の上が画面の外（欄が写っていない）: 33 の欄より下だけを切り出す"""
        img = _shot33()[600:, :]
        self.assertIsNone(SaveLoad._features(img)[0])


class TestEachCheck(unittest.TestCase):
    """受け入れの条件を 1 つずつ: 欄の四隅をどこへ写すかを決めて、その条件だけで落ちるようにする（撮影 1000×1000）"""
    W = H = 1000

    def _hit(self, tl, tr, br, bl):
        x0, y0, x1, y1 = SaveLoad.PANEL_BOX
        homo = cv2.getPerspectiveTransform(np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]),
                                           np.float32([tl, tr, br, bl]))
        return SaveLoad._panel_hit(homo, 50, self.W, self.H)

    def test_a_plain_box_is_accepted(self):
        hit = self._hit((100, 500), (700, 480), (700, 580), (100, 540))
        self.assertIsNotNone(hit)
        self.assertEqual(hit[2], 50)
        self.assertAlmostEqual(hit[3], 40 / 60)

    def test_a_twisted_box(self):
        self.assertIsNone(self._hit((100, 500), (700, 560), (700, 480), (100, 540)), "凸でない")

    def test_a_flat_box(self):
        self.assertIsNone(self._hit((100, 500), (700, 498), (700, 512), (100, 510)), "左端の高さ 10/1000 < 0.02")
        self.assertIsNone(self._hit((100, 300), (700, 280), (700, 420), (100, 400)), "100/1000 > 0.08")

    def test_a_mirrored_or_leaning_box(self):
        self.assertIsNone(self._hit((700, 500), (100, 480), (100, 580), (700, 540)), "左右が逆")
        self.assertIsNone(self._hit((100, 500), (700, 500), (680, 540), (80, 540)), "左端が 0.5 より寝ている")

    def test_the_point_out_of_the_shot(self):
        self.assertIsNone(self._hit((-200, 500), (400, 480), (400, 580), (-200, 540)), "欄の左端も点も画面の外")
        self.assertIsNone(self._hit((990, 500), (1590, 480), (1590, 580), (990, 540)), "点が右の外")

    def test_the_best_of_the_two_sizes_does_not_depend_on_the_order(self):
        img = _shot33()
        normal = SaveLoad.find_codebox(img, 1125)
        before = SaveLoad._load_panel()
        self.addCleanup(setattr, SaveLoad, "_panel", before)
        SaveLoad._panel = list(reversed(before))
        self.assertEqual(SaveLoad.find_codebox(img, 1125), normal, "インライアの多い方")


class TestTheValues(unittest.TestCase):
    def test_the_values(self):
        self.assertEqual((SaveLoad.PANEL_SCALES, SaveLoad.PANEL_UP, SaveLoad.PANEL_MAX_H, SaveLoad.PANEL_RATIO,
                          SaveLoad.PANEL_RANSAC_PX, SaveLoad.PANEL_MIN_INLIERS, SaveLoad.PANEL_LEFT_H),
                         ((1.0, 0.6), 2.0, 1500, 0.75, 5.0, 10, (0.02, 0.08)))
        self.assertEqual((SaveLoad.PANEL_BOX, SaveLoad.PANEL_CLICK), ((40, 300, 520, 360), (100, 330)))
        self.assertEqual((config.SAVELOAD_FIND_SEC, config.SAVELOAD_FIND_POLL_SEC), (3.0, 0.5))
        self.assertEqual(SaveLoad.MATCH_MIN, 0.8, "今の照合は今どおり")

    def test_the_panel_file_has_an_empty_box(self):
        """見本の欄の中は暗い（コードが写っていない）"""
        panel = _read(Path(config.resource_path(SaveLoad.PANEL_FILE)))
        x0, y0, x1, y1 = SaveLoad.PANEL_BOX
        inner = cv2.cvtColor(panel[y0 + 8:y1 - 8, x0 + 8:x1 - 8], cv2.COLOR_BGR2GRAY)
        self.assertLess(float(inner.mean()), 20)
        self.assertLess(int(inner.max()), 80, "文字が無い")


class TestLookingAgain(unittest.TestCase):
    def test_it_looks_again_for_three_seconds_then_writes_numbers_only(self):
        world = _World(self)
        with patch.object(SaveLoad, "find_codebox", return_value=None):
            self.assertEqual(world.entry().load_code(CODE, world.log_path), "skipped")
        self.assertEqual(world.captures, 7, "0・0.5・…・3.0 秒")
        self.assertEqual(world.now[0], 3.0)
        self.assertIn("⚠ コード欄が見つかりません → コードを読み込みません", world.logs)
        miss = [m for m in world.written if "コード欄が見つからない" in m]
        self.assertEqual(miss, ["[コードの読み込み] コード欄が見つからない（7 回）: 撮影なし"])
        self.assertEqual(world.events, [], "何も押さない")

    def test_found_on_the_third_look(self):
        world = _World(self)
        found = iter([None, None, FOUND])
        with patch.object(SaveLoad, "find_codebox", side_effect=lambda bgr, h: next(found)):
            self.assertEqual(world.entry().load_code(CODE, world.log_path), "loaded")
        self.assertNotIn("⚠ コード欄が見つかりません → コードを読み込みません", world.logs)
        self.assertFalse(any("コード欄が見つからない" in m for m in world.written))

    def test_a_stop_ends_the_looking(self):
        world = _World(self)

        def find(bgr, h):
            world.running = False
            return None
        with patch.object(SaveLoad, "find_codebox", side_effect=find):
            world.entry().load_code(CODE, world.log_path)
        self.assertEqual(world.captures, 1)

    def test_the_numbers(self):
        img, _box = _front(1280, 720)
        text = SaveLoad.miss_numbers(np.zeros((720, 1280, 3), np.uint8), 720)
        self.assertRegex(text, r"^照合 一致 -?\d\.\d{3}・倍率 \d\.\d{2}・位置 \(\d+, \d+\)／特徴点 インライア \d+／明るさ 0\.0$")
        text = SaveLoad.miss_numbers(_shot33(), 1125)
        m = re.match(r"^照合 一致 (\d\.\d{3})・倍率 (\d\.\d{2})・位置 \((\d+), (\d+)\)／特徴点 インライア (\d+)／明るさ ([\d.]+)$",
                     text)
        self.assertIsNotNone(m, text)
        self.assertLess(float(m.group(1)), 0.8)
        self.assertGreater(int(m.group(5)), 100)

    def test_the_log_after_a_find_says_how(self):
        world = _World(self)
        with patch.object(SaveLoad, "find_codebox", return_value=(500.0, 300.0, 87, 0.82, "features")):
            world.entry().load_code(CODE, world.log_path)
        self.assertIn("[コードの読み込み] コード欄 (500.0, 300.0)・特徴点 インライア 87・倍率 0.82", world.written)

    def test_the_code_is_never_written(self):
        world = _World(self)
        with patch.object(SaveLoad, "find_codebox", return_value=None):
            world.entry().load_code(CODE, world.log_path)
        text = "\n".join(map(str, world.logs + world.written + world.events))
        self.assertNotIn(CODE, text)
        self.assertNotIn(str(len(CODE)), text)


if __name__ == "__main__":
    unittest.main()
