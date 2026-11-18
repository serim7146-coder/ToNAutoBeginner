"""アイテム自動取得: 店の見本は英語・日本語の 2 枚。ログで分かった表示言語で試す見本を決める。
本物の画面は撮らない。見本の画像ファイルを読んで、別の画像に当てるだけ"""
from tests.support import *  # noqa: F401,F403

import ItemFetch

JAPANESE_LINE = "This round is taking place at Facility (12) and the round type is クラシック"
ENGLISH_LINE = "This round is taking place at Facility (12) and the round type is Classic"


def _read_color(name):
    import cv2
    import numpy as np
    data = np.fromfile(str(config.resource_path(name)), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _screen_showing(name):
    """見本を 1920x1080 の x760・y245 に置いた画面（英語の見本の出どころと同じ枠）"""
    import numpy as np
    canvas = np.zeros((1080, 1920, 3), np.uint8)
    canvas[245:505, 760:1140] = _read_color(name)
    return canvas


class _Base(unittest.TestCase):
    def setUp(self):
        # 見本の読み込み結果・日本語の確定はプロセス全体の状態。変えたら戻す
        self.addCleanup(setattr, ItemFetch, "_templates", ItemFetch._templates)
        self.addCleanup(LogParser.forget_japanese)
        LogParser.forget_japanese()
        ItemFetch._templates = None
        if not _cv2_usable():
            self.skipTest("SIFT が使えない")

    def _confirm_japanese(self):
        LogParser.parse(JAPANESE_LINE)
        self.assertTrue(LogParser.japanese_seen())


def _cv2_usable():
    try:
        import cv2
        import numpy  # noqa: F401
        cv2.SIFT_create()
        return True
    except Exception:
        return False


class TestTemplateFiles(_Base):
    def test_the_two_files_exist_and_are_the_same_size(self):
        import cv2
        self.assertEqual(ItemFetch.TEMPLATE_FILES, (ItemFetch.TEMPLATE_EN, ItemFetch.TEMPLATE_JA))
        for name in ItemFetch.TEMPLATE_FILES:
            with self.subTest(name=name):
                self.assertTrue(Path(config.resource_path(name)).is_file())
                img = _read_color(name)
                self.assertEqual((img.shape[1], img.shape[0]), ItemFetch.TEMPLATE_SIZE)
        self.assertEqual(ItemFetch.TEMPLATE_SIZE, (380, 260))

    def test_both_load(self):
        loaded = ItemFetch._load_templates()
        self.assertEqual(set(loaded), set(ItemFetch.TEMPLATE_FILES))

    def test_available(self):
        self.assertTrue(ItemFetch.available())

    def test_the_material_cutout_is_not_shipped(self):
        """材料の切り抜き（見本ではない）を exe に入れない（build.py がフォルダごと入れる）"""
        shop = Path(config.resource_path(ItemFetch.TEMPLATE_EN)).parent
        self.assertFalse((shop / "shop_main_jp.png").exists())

    def test_the_old_single_template_name_is_gone(self):
        self.assertFalse(hasattr(ItemFetch, "TEMPLATE_FILE"))


class TestLocate(_Base):
    POINT = (106, 129)      # BUTTONS["Enkephalin"]

    def test_an_english_screen_is_found(self):
        got = ItemFetch.locate(_screen_showing(ItemFetch.TEMPLATE_EN), self.POINT)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got[0], self.POINT[0] + 760, delta=1.0)
        self.assertAlmostEqual(got[1], self.POINT[1] + 245, delta=1.0)

    def test_a_japanese_screen_is_found(self):
        """要。英語の見本では外れ、日本語の見本で当たる（言語が分からないうち）"""
        got = ItemFetch.locate(_screen_showing(ItemFetch.TEMPLATE_JA), self.POINT)
        self.assertIsNotNone(got)
        self.assertEqual(len(got), 6)
        self.assertAlmostEqual(got[0], self.POINT[0] + 760, delta=1.0)
        self.assertAlmostEqual(got[1], self.POINT[1] + 245, delta=1.0)
        self.assertTrue(callable(got[5]))

    def test_a_japanese_screen_is_found_in_every_button_point(self):
        screen = _screen_showing(ItemFetch.TEMPLATE_JA)
        for name, (x, y) in ItemFetch.BUTTONS.items():
            with self.subTest(name=name):
                got = ItemFetch.locate(screen, (x, y))
                self.assertIsNotNone(got)
                self.assertAlmostEqual(got[0], x + 760, delta=1.0)
                self.assertAlmostEqual(got[1], y + 245, delta=1.0)

    def test_a_template_passed_in_is_the_only_one_used(self):
        loaded = ItemFetch._load_templates()
        japanese_screen = _screen_showing(ItemFetch.TEMPLATE_JA)
        self.assertIsNone(ItemFetch.locate(japanese_screen, self.POINT, template=loaded[ItemFetch.TEMPLATE_EN]))
        self.assertIsNotNone(ItemFetch.locate(japanese_screen, self.POINT, template=loaded[ItemFetch.TEMPLATE_JA]))

    def test_nothing_loaded_means_unavailable_and_nothing_found(self):
        ItemFetch._templates = {}
        self.assertFalse(ItemFetch.available())
        self.assertIsNone(ItemFetch.locate(_screen_showing(ItemFetch.TEMPLATE_EN), self.POINT))

    def test_a_blank_screen_finds_nothing(self):
        import numpy as np
        self.assertIsNone(ItemFetch.locate(np.zeros((1080, 1920, 3), np.uint8), self.POINT))

    def test_the_shot_features_are_computed_once_however_many_templates(self):
        import numpy as np
        noise = np.random.RandomState(0).randint(0, 255, (1080, 1920, 3)).astype(np.uint8)
        with patch.object(ItemFetch, "_features", wraps=ItemFetch._features) as features, \
             patch.object(ItemFetch, "_match_one", return_value=None) as match:
            ItemFetch._load_templates()         # 見本を読む回の呼び出しは数えない
            features.reset_mock()
            self.assertIsNone(ItemFetch.locate(noise, self.POINT))
        self.assertEqual(features.call_count, 1)
        self.assertEqual(match.call_count, 2, "言語が分からないので 2 枚とも試す")

    def test_the_first_hit_stops_the_search(self):
        with patch.object(ItemFetch, "_match_one", wraps=ItemFetch._match_one) as match:
            self.assertIsNotNone(ItemFetch.locate(_screen_showing(ItemFetch.TEMPLATE_EN), self.POINT))
        self.assertEqual(match.call_count, 1, "英語が先に当たる")

    def test_confirmed_japanese_tries_only_the_japanese_template(self):
        """英語の画面に当てても英語の見本は試さない（日本語の見本 1 枚だけを照合する）"""
        loaded = ItemFetch._load_templates()
        self._confirm_japanese()
        with patch.object(ItemFetch, "_match_one", wraps=ItemFetch._match_one) as match:
            ItemFetch.locate(_screen_showing(ItemFetch.TEMPLATE_EN), self.POINT)
        self.assertEqual(match.call_count, 1)
        self.assertIs(match.call_args.args[0], loaded[ItemFetch.TEMPLATE_JA][0])
        self.assertIsNot(match.call_args.args[0], loaded[ItemFetch.TEMPLATE_EN][0])

    def test_confirmed_japanese_still_finds_a_japanese_screen(self):
        self._confirm_japanese()
        self.assertIsNotNone(ItemFetch.locate(_screen_showing(ItemFetch.TEMPLATE_JA), self.POINT))


class TestTemplatesToTry(_Base):
    EN = ("en-keypoints", "en-descriptors")
    JA = ("ja-keypoints", "ja-descriptors")

    def test_unknown_language_tries_english_first_then_japanese(self):
        ItemFetch._templates = {ItemFetch.TEMPLATE_EN: self.EN, ItemFetch.TEMPLATE_JA: self.JA}
        self.assertEqual(ItemFetch._templates_to_try(), [self.EN, self.JA])

    def test_confirmed_japanese_tries_only_japanese(self):
        ItemFetch._templates = {ItemFetch.TEMPLATE_EN: self.EN, ItemFetch.TEMPLATE_JA: self.JA}
        self._confirm_japanese()
        self.assertEqual(ItemFetch._templates_to_try(), [self.JA])

    def test_confirmed_japanese_without_the_japanese_template_falls_back_to_english(self):
        ItemFetch._templates = {ItemFetch.TEMPLATE_EN: self.EN}
        self._confirm_japanese()
        self.assertEqual(ItemFetch._templates_to_try(), [self.EN])

    def test_forgetting_brings_both_back(self):
        ItemFetch._templates = {ItemFetch.TEMPLATE_EN: self.EN, ItemFetch.TEMPLATE_JA: self.JA}
        self._confirm_japanese()
        LogParser.forget_japanese()
        self.assertEqual(ItemFetch._templates_to_try(), [self.EN, self.JA])

    def test_english_round_name_does_not_narrow_it(self):
        ItemFetch._templates = {ItemFetch.TEMPLATE_EN: self.EN, ItemFetch.TEMPLATE_JA: self.JA}
        LogParser.parse(ENGLISH_LINE)
        self.assertEqual(ItemFetch._templates_to_try(), [self.EN, self.JA])

    def test_only_japanese_loaded_without_confirmation_still_works(self):
        ItemFetch._templates = {ItemFetch.TEMPLATE_JA: self.JA}
        self.assertEqual(ItemFetch._templates_to_try(), [self.JA])
        self.assertTrue(ItemFetch.available())

    def test_nothing_loaded_tries_nothing(self):
        ItemFetch._templates = {}
        self.assertEqual(ItemFetch._templates_to_try(), [])
        self._confirm_japanese()
        self.assertEqual(ItemFetch._templates_to_try(), [])

    def test_a_template_that_cannot_be_read_is_skipped_quietly(self):
        real = config.resource_path
        missing = lambda name: Path("nope") / name if name == ItemFetch.TEMPLATE_JA else real(name)  # noqa: E731
        with patch.object(config, "resource_path", side_effect=missing):
            loaded = ItemFetch._load_templates()
        self.assertEqual(list(loaded), [ItemFetch.TEMPLATE_EN])


if __name__ == "__main__":
    unittest.main()
