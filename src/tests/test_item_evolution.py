"""進化アイテムの自動取得（依頼者 2026-11-17「Equip ではなく、横にある小さな色文字をクリックする」）。

失くしたのが進化アイテムなら、Equip（装備する）ではなく、その右に並ぶ色文字を、色だけで選んで 1 つ押す。
本物の画面は撮らない。帯の画像はこのファイルの中で合成する（色の付いた四角・細い縦線を置くだけ）。
本物の OSC・入力・マウスは使わない（マウスは記録するだけの偽物）"""
from tests.support import *  # noqa: F401,F403

import numpy as np

try:
    import cv2
except Exception:                                   # pragma: no cover
    cv2 = None

EQUIP = (50, 100)           # 合成した画面の Equip の中心（1 倍のとき）

# 金属バットの色文字（Equip からの差 (横, 縦)・(色相, 彩度, 明るさ)）。実測の値
BAT = {
    31: ((59, -5), (0, 148, 255)),          # 血濡れ
    43: ((57, 5), (135, 169, 216)),         # カラー
    42: ((98, -7), (93, 104, 255)),         # ジャスティティア
    49: ((98, 6), (109, 25, 205)),          # メタルパイプ（白い文字）
    82: ((139, 0), (178, 172, 56)),         # ゴースト（暗い）
}

# 元のアイテムごとの、色文字の (番号: 色相, 彩度, 明るさ)。表の元の値（実測）
GROUPS = {
    "radar": {93: (0, 155, 234)},
    "medkit": {46: (0, 173, 241)},
    "have wine": {87: (25, 117, 209)},
    "glow coil": {45: (119, 148, 242)},
    "speed coil": {33: (0, 154, 251)},
    "regen coil": {76: (90, 76, 255), 80: (67, 136, 246)},
    "metal bat": {n: v[1] for n, v in BAT.items()},
    "kat charm": {18: (0, 154, 242), 72: (126, 98, 233)},
    "chaos coil": {44: (24, 61, 233)},
    "corkscrew": {85: (0, 184, 233)},
    "revolver": {66: (0, 155, 227), 38: (90, 80, 255)},
    "teleporter": {69: (90, 74, 255), 32: (0, 155, 232), 91: (140, 163, 246), 39: (75, 143, 211),
                   86: (25, 117, 210)},
    "burger": {74: (109, 25, 209)},
    "beyond plush": {11: (143, 140, 242)},
    "darkgrey plush": {73: (109, 25, 211)},
    "have plush": {75: (20, 146, 234)},
}


def _bgr(h, s, v):
    return cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0]


def _page(items, scale=1.0, bg=15, thin=False, size=(30, 7)):
    """Equip の右に色文字を並べた画面。items は [((横, 縦), (色相, 彩度, 明るさ))]。
    thin: 太い四角ではなく、細い縦線を並べる（字ごとに分かれた本物の文字に近い）"""
    img = np.full((int(200 * scale), int(400 * scale), 3), bg, np.uint8)
    w, h = int(size[0] * scale), int(size[1] * scale)
    for (dx, dy), hsv in items:
        cx, cy = int((EQUIP[0] + dx) * scale), int((EQUIP[1] + dy) * scale)
        color = _bgr(*hsv)
        if thin:
            for k in range(0, w, int(5 * scale)):
                img[cy - h // 2:cy + h // 2 + 1, cx - w // 2 + k:cx - w // 2 + k + max(2, int(2 * scale))] = color
        else:
            img[cy - h // 2:cy - h // 2 + h, cx - w // 2:cx - w // 2 + w] = color
    return img


def _bat_page(**kw):
    return _page(list(BAT.values()), **kw)


def _labels(img, scale=1.0):
    return ItemFetch.evolution_labels(img, (EQUIP[0] * scale, EQUIP[1] * scale), (scale, scale))


@unittest.skipUnless(_cv2_available(), "OpenCV / numpy が無い")
class TestEvolutionTable(unittest.TestCase):
    def test_every_entry_is_well_formed(self):
        for item_id, (hue, sat, rel) in ItemFetch.EVOLUTIONS.items():
            self.assertTrue(0 <= hue < 180 and 0 <= sat <= 255 and 0 < rel <= 1.0, item_id)

    def test_the_table_has_the_23_measured_items_plus_the_rest(self):
        self.assertEqual(len(ItemFetch.EVOLUTIONS), 27)
        self.assertEqual(sum(len(g) for g in GROUPS.values()), len(ItemFetch.EVOLUTIONS))
        self.assertEqual({n for g in GROUPS.values() for n in g}, set(ItemFetch.EVOLUTIONS))

    def test_the_bat_pair_that_shares_a_hue_is_told_apart_only_by_brightness(self):
        blood, ghost = ItemFetch.EVOLUTIONS[31], ItemFetch.EVOLUTIONS[82]
        self.assertLessEqual(ItemFetch._hue_gap(blood[0], ghost[0]), 2)
        self.assertGreater(blood[2] - ghost[2], 2 * ItemFetch.VREL_TOLERANCE)


@unittest.skipUnless(_cv2_available(), "OpenCV / numpy が無い")
class TestEvolutionLabels(unittest.TestCase):
    def test_a_dark_label_is_found_without_a_fixed_brightness_threshold(self):
        """ゴーストは明るさ 56（ほかは 200 超）。固定の閾値（V > 110 など）だと落ちる"""
        found = _labels(_page([((139, 0), (178, 172, 56))]))
        self.assertEqual(len(found), 1)
        x, y, hue, sat, val = found[0]
        self.assertAlmostEqual(val, 56, delta=2)
        self.assertLessEqual(ItemFetch._hue_gap(hue, 178), 2)
        self.assertAlmostEqual(sat, 172, delta=3)
        self.assertAlmostEqual(x, EQUIP[0] + 139, delta=1.5)
        self.assertAlmostEqual(y, EQUIP[1], delta=1.5)

    def test_a_dark_label_next_to_bright_ones_is_found_too(self):
        """明るい文字が帯の 12% を超えても、暗い文字を落とさない（太い四角 5 つで帯の約 3 割）"""
        for thin in (False, True):
            with self.subTest(thin=thin):
                vals = sorted(round(f[4]) for f in _labels(_bat_page(thin=thin)))
                self.assertEqual(len(vals), 5, vals)
                self.assertAlmostEqual(vals[0], 56, delta=2)

    def test_the_colour_is_measured_on_the_bright_pixels_only(self):
        """文字の箱の中に暗い画素が混ざっても、色は文字の画素だけで測る（箱ごとだと暗く出る）"""
        img = _page([((59, -5), (0, 148, 255))])
        cx, cy = EQUIP[0] + 59, EQUIP[1] - 5
        for k in range(1, 30, 3):                           # 3 列のうち 2 列を暗くする（箱の 3 分の 2 が暗い）
            img[cy - 3:cy + 4, cx - 15 + k:cx - 15 + k + 2] = 20
        found = _labels(img)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0][4], 255, delta=2)
        self.assertAlmostEqual(found[0][3], 148, delta=3)

    def test_a_white_label_is_found_because_saturation_does_not_filter(self):
        found = _labels(_page([((98, 6), (109, 25, 205))]))
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0][3], 25, delta=3)

    def test_red_that_straddles_the_hue_wrap_is_read_as_red(self):
        """赤は色相 0 と 179 の両側に割れる（半分ずつ混ぜる）"""
        img = _page([((59, -5), (0, 148, 255))])
        cx, cy = EQUIP[0] + 59, EQUIP[1] - 5
        img[cy - 3:cy + 4, cx - 15:cx:2] = _bgr(178, 148, 255)
        found = _labels(img)
        self.assertEqual(len(found), 1)
        self.assertLessEqual(ItemFetch._hue_gap(found[0][2], 0), 3)

    def test_labels_are_found_at_another_scale(self):
        found = _labels(_bat_page(scale=1.5), scale=1.5)
        self.assertEqual(len(found), 5)
        self.assertAlmostEqual(min(f[0] for f in found), (EQUIP[0] + 57) * 1.5, delta=3)

    def test_nothing_in_the_band_is_empty(self):
        self.assertEqual(_labels(np.full((200, 400, 3), 15, np.uint8)), [])
        self.assertEqual(_labels(None), [])
        self.assertEqual(ItemFetch.evolution_labels(np.zeros((5, 5, 3), np.uint8), (300, 300), (1.0, 1.0)), [])

    def test_text_outside_the_band_is_not_a_label(self):
        """Equip 自身（左 −5〜+5）や、帯の右（+160 より外）の文字は拾わない"""
        near = _page([((0, 0), (60, 200, 200))])
        far = _page([((175, 0), (0, 148, 255))])
        self.assertEqual(_labels(near), [])
        self.assertEqual(_labels(far), [])

    def test_a_speck_and_a_tall_block_are_not_labels(self):
        speck = _page([((59, 0), (0, 148, 255))], size=(3, 3))
        tall = _page([((59, 0), (0, 148, 255))], size=(30, 26))
        self.assertEqual(_labels(speck), [])
        self.assertEqual(_labels(tall), [])


@unittest.skipUnless(_cv2_available(), "OpenCV / numpy が無い")
class TestPickEvolution(unittest.TestCase):
    @staticmethod
    def _label(x, hsv):
        return (float(x), 100.0) + tuple(float(v) for v in hsv)

    def test_a_single_matching_colour_is_picked(self):
        labels = [self._label(110, (0, 154, 242))]
        self.assertIs(ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[18]), labels[0])

    def test_a_colour_that_does_not_match_is_none(self):
        labels = [self._label(110, (126, 98, 233)), self._label(150, (67, 136, 246))]
        self.assertIsNone(ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[18]))
        self.assertIsNone(ItemFetch.pick_evolution([], ItemFetch.EVOLUTIONS[18]))

    def test_white_is_picked_among_two_colours(self):
        """白（彩度 25）を狙うときは白い文字だけが候補（色相が近くても彩度の高い色は選ばない）"""
        white, red, blue = self._label(100, (109, 25, 240)), self._label(60, (109, 150, 240)), \
            self._label(140, (0, 150, 255))
        self.assertIs(ItemFetch.pick_evolution([red, white, blue], ItemFetch.EVOLUTIONS[74]), white)

    def test_a_coloured_target_never_picks_a_white_label(self):
        labels = [self._label(100, (0, 25, 240))]
        self.assertIsNone(ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[18]))

    def test_twilight_coil_with_saturation_61_is_on_the_white_side(self):
        labels = [self._label(110, (24, 61, 233))]
        self.assertIs(ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[44]), labels[0])

    def test_two_labels_of_the_same_colour_are_none(self):
        """間違えて押すより押さない"""
        labels = [self._label(110, (0, 154, 242)), self._label(150, (0, 150, 240))]
        self.assertIsNone(ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[18]))

    def test_the_lone_ghost_lookalike_is_not_taken_for_the_blood_bat(self):
        """血濡れ（明るい）を狙って、暗いゴースト（H=178）だけが写っているときは None"""
        labels = [self._label(110, (178, 172, 56)), self._label(150, (135, 169, 216))]
        self.assertEqual(ItemFetch._hue_gap(178, 0), 2)
        self.assertIsNone(ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[31]))

    def test_the_hue_wrap_around_zero_matches(self):
        labels = [self._label(110, (177, 150, 240))]
        self.assertIs(ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[18]), labels[0])

    def test_all_table_entries_are_told_apart_inside_each_source_item(self):
        """表の全件（元のアイテムごとの実測値）を、同じページの全部の文字と並べても取り違えない"""
        for group, members in GROUPS.items():
            labels = [self._label(100 + 40 * i, hsv) for i, hsv in enumerate(members.values())]
            for index, item_id in enumerate(members):
                with self.subTest(group=group, item=item_id):
                    # 明るさの比はページの一番明るい文字を 1.0 にした値。実測の V から作り直す
                    picked = ItemFetch.pick_evolution(labels, ItemFetch.EVOLUTIONS[item_id])
                    self.assertIs(picked, labels[index])


class _Mouse:
    """押した点を記録するだけの偽物（本物のカーソルは動かさない）"""

    def __init__(self):
        self.clicks = []

    def click_at(self, x, y):
        self.clicks.append((round(x, 1), round(y, 1)))
        return True

    def move_rel(self, dx, dy):
        pass

    def tab_down(self):
        pass

    def tab_up(self):
        pass


@unittest.skipUnless(_cv2_available(), "OpenCV / numpy が無い")
class TestEvolutionSpot(unittest.TestCase):
    """Fetcher._evolution_spot と equip。画面はコードの中で合成する"""

    def _fetcher(self, img, equipping=None, scale=1.0):
        """img を撮り続ける Fetcher。equipping: 押すたびに返す (回数, 番号) の並び（最後を繰り返す）"""
        self.shots = []
        self.logs = []
        self.mouse = _Mouse()
        self.now = [0.0]
        presses = self.mouse

        def capture():
            self.shots.append(1)
            return img, (30, 20)

        def equip_seen():
            if not equipping:
                return 0, 0
            n = len(presses.clicks)
            return equipping[min(n, len(equipping)) - 1] if n else (0, 0)

        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=capture, mouse=self.mouse,
            equip_seen=equip_seen, stopped=lambda: None, log=self.logs.append,
            sleep=lambda sec: self.now.__setitem__(0, self.now[0] + sec),
            clock=lambda: self.now[0])
        f.equip_point = ((EQUIP[0] * scale, EQUIP[1] * scale), (scale, scale))
        return f

    @staticmethod
    def _with_equip_text(img):
        """Equip の緑の文字（装備する）を足す。押す点を決める green_center が見つける"""
        img = img.copy()
        img[EQUIP[1] - 3:EQUIP[1] + 3, EQUIP[0] - 8:EQUIP[0] + 8] = _bgr(60, 200, 200)
        return img

    def test_an_item_that_is_not_in_the_table_does_not_even_take_a_picture(self):
        f = self._fetcher(_bat_page())
        self.assertIsNone(f._evolution_spot(29, EQUIP))
        self.assertEqual(self.shots, [], "撮らない・動きが何も変わらない")

    def test_a_single_coloured_label_gives_its_position(self):
        f = self._fetcher(_page([((110 - EQUIP[0], 0), (0, 154, 242))]))
        x, y = f._evolution_spot(18, EQUIP)
        self.assertAlmostEqual(x, 110, delta=1.5)
        self.assertAlmostEqual(y, EQUIP[1], delta=1.5)

    def test_a_different_colour_only_gives_none(self):
        f = self._fetcher(_page([((60, 0), (126, 98, 233))]))
        self.assertIsNone(f._evolution_spot(18, EQUIP))
        self.assertTrue(any("選べません" in line for line in self.logs))

    def test_no_label_or_no_picture_gives_none(self):
        self.assertIsNone(self._fetcher(np.full((200, 400, 3), 15, np.uint8))._evolution_spot(18, EQUIP))
        f = self._fetcher(None)
        f.capture = lambda: None
        self.assertIsNone(f._evolution_spot(18, EQUIP))

    def test_the_bat_is_told_apart_from_the_ghost(self):
        """要のテスト: 金属バットの 5 つを合成。31 を狙うと血濡れ・82 を狙うとゴースト（色相が 2 しか違わない）"""
        for thin in (False, True):
            f = self._fetcher(_bat_page(thin=thin))
            for item_id, ((dx, dy), _hsv) in BAT.items():
                with self.subTest(thin=thin, item=item_id):
                    x, y = f._evolution_spot(item_id, EQUIP)
                    self.assertAlmostEqual(x, EQUIP[0] + dx, delta=2)
                    self.assertAlmostEqual(y, EQUIP[1] + dy, delta=2)

    def test_the_bat_is_told_apart_at_another_scale_and_with_noise(self):
        rng = np.random.default_rng(7)
        img = _bat_page(scale=1.5).astype(np.float32)
        img += rng.normal(0, 4, img.shape)
        img = np.clip(img, 0, 255).astype(np.uint8)
        f = self._fetcher(img, scale=1.5)
        for item_id, ((dx, dy), _hsv) in BAT.items():
            x, y = f._evolution_spot(item_id, (EQUIP[0] * 1.5, EQUIP[1] * 1.5))
            self.assertAlmostEqual(x, (EQUIP[0] + dx) * 1.5, delta=4, msg=str(item_id))
            self.assertAlmostEqual(y, (EQUIP[1] + dy) * 1.5, delta=4, msg=str(item_id))

    def test_the_ghost_is_not_picked_when_only_the_blood_bat_is_visible(self):
        """ゴースト（82）を狙って、血濡れ（H=0）しか写っていないとき、色相が近くても押さない"""
        f = self._fetcher(_page([BAT[31]]))
        self.assertIsNone(f._evolution_spot(82, EQUIP))

    def test_equip_presses_the_coloured_label_only_for_an_evolution(self):
        img = self._with_equip_text(_bat_page())
        # 進化（血濡れ 31）: 色文字の点を 1 回押し、Equipping 31 で成功
        f = self._fetcher(img, equipping=[(1, 31)])
        self.assertTrue(f.equip(31))
        self.assertEqual(len(self.mouse.clicks), 1)
        x, y = self.mouse.clicks[0]
        self.assertAlmostEqual(x, EQUIP[0] + 59, delta=2)
        self.assertAlmostEqual(y, EQUIP[1] - 5, delta=2)
        self.assertTrue(any("進化" in line and "Equip:" in line for line in self.logs))
        # 表に無い番号: 今までどおり Equip の緑の文字（撮影は 1 回だけ）
        f = self._fetcher(img, equipping=[(1, 29)])
        self.assertTrue(f.equip(29))
        self.assertEqual(len(self.mouse.clicks), 1)
        x, y = self.mouse.clicks[0]
        self.assertAlmostEqual(x, EQUIP[0] - 0.5, delta=1.5)
        self.assertAlmostEqual(y, EQUIP[1] - 0.5, delta=1.5)
        self.assertEqual(len(self.shots), 1, "表に無い番号は撮影を増やさない")
        self.assertFalse(any("進化" in line for line in self.logs))

    def test_equip_falls_back_to_equip_when_the_colour_is_not_found(self):
        """色文字が見つからなければ Equip を押す（今までどおり。間違ったものは押さない）"""
        img = self._with_equip_text(np.full((200, 400, 3), 15, np.uint8))
        f = self._fetcher(img, equipping=[(1, 31)])
        self.assertTrue(f.equip(31))
        x, y = self.mouse.clicks[0]
        self.assertAlmostEqual(x, EQUIP[0] - 0.5, delta=1.5)
        self.assertAlmostEqual(y, EQUIP[1] - 0.5, delta=1.5)

    def test_equip_does_not_brute_force_the_labels(self):
        """違う番号が来ても、別の色文字を順に試さない。同じ 1 つを EQUIP_TRIES 回までやり直して終わる"""
        img = self._with_equip_text(_bat_page())
        f = self._fetcher(img, equipping=[(1, 12), (2, 12), (3, 12)])
        self.assertFalse(f.equip(31))
        self.assertEqual(len(self.mouse.clicks), ItemFetch.EQUIP_TRIES)
        self.assertEqual(len(set(self.mouse.clicks)), 1, "押した色文字は 1 つだけ")

    def test_the_check_after_pressing_is_unchanged(self):
        """押した後は Equipping <狙った番号> で確かめる（来ない・違う番号はやり直し）"""
        img = self._with_equip_text(_bat_page())
        f = self._fetcher(img, equipping=[(0, 0), (1, 31)])
        self.assertTrue(f.equip(31))
        self.assertEqual(len(self.mouse.clicks), 2, "1 回目は Equipping が来ない→押し直す")
        self.assertEqual(len(set(self.mouse.clicks)), 1)

    def test_buy_plush_path_does_not_use_the_evolution_branch(self):
        """Guidance Plush の流れ（_equip_once）は進化の分岐を通らない（進化しないため）"""
        import inspect
        self.assertNotIn("_evolution_spot", inspect.getsource(ItemFetch.Fetcher._equip_once))
        self.assertNotIn("_evolution_spot", inspect.getsource(ItemFetch.Fetcher.buy_plush))


if __name__ == "__main__":
    unittest.main()
