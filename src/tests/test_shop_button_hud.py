"""店のボタン・Equip が HUD に掛かるとき、下を見ず、横に 1 回ずらして帯の外へ出す（2026-11-17）。
本物の入力・OSC・撮影は使わない（偽の板 _Panel・numpy の画像）"""
from tests.support import *  # noqa: F401,F403

import ItemFetch
from tests.test_item_fetch_panel import _FetchCase, _Panel
from tests.test_plush_row_hud import _panel_with_row_at

H = 1080
AIM = (960.0, 540.0)                        # _Panel(size=(1920, 1080)) の照準
BTN = ItemFetch.BUTTONS
# 感度（画面px／マウス1）。160px の横ずらしが MOVE_CAP（160）以内の量で 1 回で届く値（0.9 だと 178 でキャップに掛かる）。
# 240px までは 1 回で届く
GAIN = (1.5, 0.55)


def _panel_with_button_at(shop, rx, ry, height=H, size=(1920, 1080), scale=1.0, **kw):
    """店のボタンの点が、照準から 横 rx・縦 ry（height で割る）に写る _Panel"""
    aim = (size[0] / 2, size[1] / 2)
    bx, by = BTN[shop]
    return _Panel(left=aim[0] + rx * height - bx * scale, top=aim[1] + ry * height - by * scale,
                  scale=scale, size=size, **kw)


def _boxes(shop_x, equip_x, y=500.0):
    """button_box と同じ形（縦 2 点）の偽の箱 [(名前, [(x, y)×2])]"""
    return [("店のボタン", [(shop_x, y - 15), (shop_x, y + 15)]),
            ("Equip", [(equip_x, y - 15), (equip_x, y + 15)])]


# ── 純粋な関数 ─────────────────────────────────────
class TestHudShiftX(unittest.TestCase):
    AIM = (500.0, 400.0)

    def test_the_constants(self):
        self.assertEqual(ItemFetch.BUTTON_SAFE_X, 0.070)
        self.assertAlmostEqual(ItemFetch.BUTTON_SAFE_X, (266 - 190) / 1080, places=3, msg="Survival・Event のボタンの位置")
        self.assertEqual((ItemFetch.HUD_X, ItemFetch.HUD_Y), ((-0.27, -0.03), (0.02, 0.21)), "HUD の値は変えない")

    def test_one_point_goes_to_the_safe_x(self):
        boxes = _boxes(self.AIM[0] - 0.078 * H, self.AIM[0] + 0.5 * H)
        shift = ItemFetch.hud_shift_x(boxes, ["店のボタン"], self.AIM, H)
        self.assertAlmostEqual(shift, (-0.078 - ItemFetch.BUTTON_SAFE_X) * H, places=6)
        self.assertLess(shift, 0, "負 ＝ 視点を左へ ＝ 板が右へ")

    def test_two_points_use_the_left_one(self):
        left, right = self.AIM[0] - 0.20 * H, self.AIM[0] - 0.10 * H
        want = (-0.20 - ItemFetch.BUTTON_SAFE_X) * H
        self.assertAlmostEqual(ItemFetch.hud_shift_x(_boxes(right, left), ["店のボタン", "Equip"], self.AIM, H),
                               want, places=6, msg="Equip が左")
        self.assertAlmostEqual(ItemFetch.hud_shift_x(_boxes(left, right), ["店のボタン", "Equip"], self.AIM, H),
                               want, places=6, msg="店のボタンが左")

    def test_only_the_named_points_count(self):
        left, right = self.AIM[0] - 0.20 * H, self.AIM[0] - 0.10 * H
        shift = ItemFetch.hud_shift_x(_boxes(left, right), ["Equip"], self.AIM, H)
        self.assertAlmostEqual(shift, (-0.10 - ItemFetch.BUTTON_SAFE_X) * H, places=6, msg="左の店のボタンは数えない")

    def test_no_point_is_zero(self):
        boxes = _boxes(100.0, 200.0)
        self.assertEqual(ItemFetch.hud_shift_x(boxes, [], self.AIM, H), 0.0)
        self.assertEqual(ItemFetch.hud_shift_x([], ["Equip"], self.AIM, H), 0.0)

    def test_the_ratio_is_divided_by_the_height(self):
        boxes = _boxes(self.AIM[0] - 0.078 * 720, self.AIM[0] + 400)
        shift = ItemFetch.hud_shift_x(boxes, ["店のボタン"], self.AIM, 720)
        self.assertAlmostEqual(shift, (-0.078 - ItemFetch.BUTTON_SAFE_X) * 720, places=6)


class TestShiftedOutside(unittest.TestCase):
    SIZE = (1000, 1000)                     # 余白は 20px

    def test_moving_the_panel_right_overflows_at_the_right_edge(self):
        corners = [(100.0, 100.0), (900.0, 100.0), (100.0, 400.0), (900.0, 400.0)]
        self.assertEqual(ItemFetch.shifted_outside(corners, 0.0, self.SIZE), (False, False))
        self.assertEqual(ItemFetch.shifted_outside(corners, -80.0, self.SIZE), (False, False), "右の端 980 ちょうど")
        self.assertEqual(ItemFetch.shifted_outside(corners, -81.0, self.SIZE), (True, False), "負は板が右へ")

    def test_moving_the_panel_left_overflows_at_the_left_edge(self):
        corners = [(100.0, 100.0), (900.0, 100.0)]
        self.assertEqual(ItemFetch.shifted_outside(corners, 80.0, self.SIZE), (False, False))
        self.assertEqual(ItemFetch.shifted_outside(corners, 81.0, self.SIZE), (True, False), "正は板が左へ")

    def test_the_vertical_is_not_moved(self):
        corners = [(500.0, 10.0), (500.0, 990.0)]
        self.assertEqual(ItemFetch.shifted_outside(corners, -50.0, self.SIZE), (False, True))
        self.assertEqual(ItemFetch.shifted_outside(corners, 0.0, self.SIZE), (False, True))

    def test_same_as_panel_outside_for_the_moved_corners(self):
        corners = [(100.0, 100.0), (900.0, 100.0), (100.0, 400.0), (900.0, 400.0)]
        for shift in (-200.0, -81.0, -10.0, 0.0, 50.0, 200.0):
            self.assertEqual(ItemFetch.shifted_outside(corners, shift, self.SIZE),
                             ItemFetch.panel_outside([(x - shift, y) for x, y in corners], self.SIZE), shift)


# ── frame_panel ───────────────────────────────────
class TestFramePanelShiftsSideways(_FetchCase):
    def _moves(self):
        return [m for m in self.logs if m.startswith("アイテム取得: 店の画面: 視点を動かす")]

    def _hud_logs(self):
        return [m for m in self.logs if m.startswith("アイテム取得: 店の画面: 手がかり")]

    def _restored(self):
        return [m for m in self.logs if "視点を戻しました" in m]

    def test_eph_real_layout_moves_sideways_once_and_never_down(self):
        """2026-11-17 18:49:28 窓4: Enkephalin のボタンが 横 -0.075・縦 +0.060。板は全部見えている"""
        panel = _panel_with_button_at("Enkephalin", -0.075, 0.060, gain=GAIN)
        self.assertTrue(panel.inside())
        f = self._fetcher(panel, saved_gain=GAIN)
        self.assertIsNotNone(f.frame_panel("Enkephalin"))
        self.assertTrue(panel.moves)
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0, "縦は 0")
        self.assertTrue(all(dx < 0 for dx, _dy in panel.moves), "視点を左へ（板が右へ）")
        ox, oy = panel.origin()
        self.assertAlmostEqual((ox + BTN["Enkephalin"][0] - AIM[0]) / H, ItemFetch.BUTTON_SAFE_X, delta=0.002)
        self.assertAlmostEqual((oy + BTN["Enkephalin"][1] - AIM[1]) / H, 0.060, delta=1e-6, msg="縦は動いていない")
        self.assertEqual(len(self._moves()), 1, "動かしたのは 1 回だけ")
        hud = self._hud_logs()
        self.assertEqual(len(hud), 2, "動かす前と、撮り直した後")
        self.assertIn("HUD に重なる点 店のボタン・", hud[0])
        self.assertIn("HUD に重なる点 なし・", hud[1])
        f.restore_view()
        self.assertEqual(self._restored(), [], "戻さない")

    def test_the_log_is_the_old_line_ending_with_vertical_zero(self):
        panel = _panel_with_button_at("Enkephalin", -0.075, 0.060, gain=GAIN)
        f = self._fetcher(panel, saved_gain=GAIN)
        f.frame_panel("Enkephalin")
        moves = self._moves()
        self.assertEqual(len(moves), 1)
        self.assertTrue(moves[0].endswith("・縦 0.0"), moves[0])
        units = (-0.075 - ItemFetch.BUTTON_SAFE_X) * H / GAIN[0]
        self.assertTrue(moves[0].startswith(f"アイテム取得: 店の画面: 視点を動かす 横 {units:.1f}・"), moves[0])

    def test_with_the_ordinary_gain_the_cap_applies_but_the_button_still_leaves_the_hud(self):
        """感度 0.9 だと 160px は 178 でキャップ（MOVE_CAP 160）に掛かる。+0.070 には 1 回では届かないが、
        帯（右端 −0.03）の外へは出る。縦は動かさない・戻さない"""
        panel = _panel_with_button_at("Enkephalin", -0.075, 0.060)
        f = self._fetcher(panel)
        self.assertIsNotNone(f.frame_panel("Enkephalin"))
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0)
        self.assertTrue(all(abs(dx) <= ItemFetch.MOVE_CAP for dx, _dy in panel.moves))
        ox, _oy = panel.origin()
        self.assertGreater((ox + BTN["Enkephalin"][0] - AIM[0]) / H, ItemFetch.HUD_X[1])
        f.restore_view()
        self.assertEqual(self._restored(), [])

    def test_buy_in_the_eph_layout_presses_both_outside_the_hud(self):
        panel = _panel_with_button_at("Enkephalin", -0.075, 0.060, gain=GAIN)
        f = self._fetcher(panel, saved_gain=GAIN, answers=(70,))
        self.assertTrue(f.buy("Enkephalin", 70))
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0)
        self.assertAlmostEqual((panel.clicks[0][0] - AIM[0]) / H, ItemFetch.BUTTON_SAFE_X, delta=0.002)
        self.assertFalse(ItemFetch.in_hud(panel.clicks[0], AIM, H))
        self.assertFalse(ItemFetch.in_hud(panel.clicks[1], AIM, H), "Equip も外")
        f.restore_view()
        self.assertEqual(self._restored(), [])

    def test_only_equip_in_the_hud_moves_sideways_in_a_small_window(self):
        """851×663（高さ 663）。Survival のボタンは帯の外（縦 +0.002）・Equip だけが (-0.130, +0.076)"""
        h, size, scale = 663, (851, 663), 0.6
        aim = (size[0] / 2, size[1] / 2)
        ex, ey = BTN["Equip"]
        panel = _Panel(left=aim[0] - 0.130 * h - ex * scale, top=aim[1] + 0.076 * h - ey * scale,
                       scale=scale, size=size, gain=GAIN)
        self.assertTrue(panel.inside())
        f = self._fetcher(panel, saved_gain=GAIN)
        f.client_height = lambda: h
        self.assertIsNotNone(f.frame_panel("Survival"))
        hud = self._hud_logs()
        self.assertIn("HUD に重なる点 Equip・", hud[0])
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0)
        self.assertEqual(len(self._moves()), 1)
        ox, _oy = panel.origin()
        self.assertAlmostEqual((ox + ex * scale - aim[0]) / h, ItemFetch.BUTTON_SAFE_X, delta=0.002)
        self.assertIn("HUD に重なる点 なし・", hud[-1])

    def test_both_in_the_hud_uses_the_left_one_and_clears_both(self):
        # Enkephalin（ボタンが Equip の左 74px）: ボタン 横 -0.110・縦 +0.060 → Equip は 横 -0.042・縦 +0.104。どちらも帯の中
        panel = _panel_with_button_at("Enkephalin", -0.110, 0.060, gain=GAIN)
        f = self._fetcher(panel, saved_gain=GAIN)
        self.assertIsNotNone(f.frame_panel("Enkephalin"))
        self.assertIn("HUD に重なる点 店のボタン・Equip・", self._hud_logs()[0])
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0)
        self.assertEqual(len(self._moves()), 1)
        ox, oy = panel.origin()
        self.assertAlmostEqual((ox + BTN["Enkephalin"][0] - AIM[0]) / H, ItemFetch.BUTTON_SAFE_X, delta=0.002)
        for shop_or_equip in ("Enkephalin", "Equip"):
            box = ItemFetch.button_box(lambda pt: (ox + pt[0], oy + pt[1], 1, 1), BTN[shop_or_equip])
            self.assertFalse(ItemFetch.box_in_hud(box, AIM, H), shop_or_equip)

    def test_both_in_the_hud_with_equip_on_the_left_uses_equip(self):
        # Event（Equip がボタンの左 86px）: Equip を 横 -0.140・縦 +0.050 → ボタンは 横 -0.060・縦 +0.061。どちらも帯の中
        ex, ey = BTN["Equip"]
        panel = _Panel(left=AIM[0] - 0.140 * H - ex, top=AIM[1] + 0.050 * H - ey, gain=GAIN)
        f = self._fetcher(panel, saved_gain=GAIN)
        self.assertIsNotNone(f.frame_panel("Event"))
        self.assertIn("HUD に重なる点 店のボタン・Equip・", self._hud_logs()[0])
        ox, oy = panel.origin()
        self.assertAlmostEqual((ox + ex - AIM[0]) / H, ItemFetch.BUTTON_SAFE_X, delta=0.002, msg="左の Equip が基準")
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0)
        for name in ("Event", "Equip"):
            box = ItemFetch.button_box(lambda pt: (ox + pt[0], oy + pt[1], 1, 1), BTN[name])
            self.assertFalse(ItemFetch.box_in_hud(box, AIM, H), name)

    def test_a_shift_that_overflows_falls_back_to_centre_then_vertical(self):
        # 細い窓（700 幅）: ボタンが 横 -0.078 → +0.070 へずらすと板が右へはみ出す。今までどおり: 真ん中（横は合っている）→ 縦
        panel = _panel_with_button_at("Enkephalin", -0.078, 0.060, size=(700, 1080), gain=GAIN)
        self.assertTrue(panel.inside())
        f = self._fetcher(panel, saved_gain=GAIN)
        self.assertIsNotNone(f.frame_panel("Enkephalin"))
        self.assertTrue(panel.moves)
        self.assertTrue(all(dx == 0 for dx, _dy in panel.moves), "横は動かさない（真ん中は合っている）")
        self.assertNotEqual(sum(dy for _dx, dy in panel.moves), 0, "縦が動く")
        f.restore_view()
        self.assertTrue(self._restored(), "縦は今までどおり戻す")

    def test_a_side_overflow_is_centred_first_like_before(self):
        # 420 幅の窓: 板の左端がはみ出す（横）。はみ出しが先 → 新しい枝には入らず、真ん中を照準へ寄せる
        panel = _panel_with_button_at("Enkephalin", -0.090, 0.060, size=(420, 1080), gain=GAIN)
        self.assertFalse(panel.inside())
        f = self._fetcher(panel, saved_gain=GAIN)
        f.frame_panel("Enkephalin")
        first = self._hud_logs()[0]
        self.assertIn("はみ出す（横）", first)
        self.assertIn("HUD に重なる点 店のボタン・", first)
        dx = panel.left0 + ItemFetch.TEMPLATE_SIZE[0] / 2 - panel.aim[0]
        self.assertTrue(self._moves()[0].startswith(
            f"アイテム取得: 店の画面: 視点を動かす 横 {dx / GAIN[0]:.1f}・縦 0.0"), (self._moves()[0], dx))
        shift_units = (-0.090 - ItemFetch.BUTTON_SAFE_X) * H / GAIN[0]
        self.assertNotAlmostEqual(dx / GAIN[0], shift_units, delta=5.0, msg="横ずらしの量ではない")

    def test_a_vertical_overflow_is_centred_vertically_like_before(self):
        # 低い窓（高さ 400）: 板の下端がはみ出す（縦）＋ボタンが HUD。はみ出しが先 → 今までどおり縦だけ寄せる
        h = 400
        panel = _panel_with_button_at("Enkephalin", -0.078, 0.20, height=h, size=(1920, h), gain=GAIN)
        self.assertFalse(panel.inside())
        f = self._fetcher(panel, saved_gain=GAIN)
        f.client_height = lambda: h
        f.frame_panel("Enkephalin")
        first = self._hud_logs()[0]
        self.assertIn("はみ出す（縦）", first)
        self.assertIn("HUD に重なる点 店のボタン・", first)
        self.assertTrue(panel.moves)
        self.assertTrue(all(dx == 0 for dx, _dy in panel.moves[:1]), "横は動かさない")
        dy = panel.top0 + ItemFetch.TEMPLATE_SIZE[1] / 2 - panel.aim[1]
        self.assertTrue(self._moves()[0].startswith(
            f"アイテム取得: 店の画面: 視点を動かす 横 0.0・縦 {dy / GAIN[1]:.1f}"), (self._moves()[0], dy))

    def test_survival_and_event_stay_at_zero_moves(self):
        for shop in ("Survival", "Event"):
            for ry in (0.0, 0.05, 0.12, -0.2):
                panel = _panel_with_button_at(shop, 0.073, ry, gain=GAIN)
                f = self._fetcher(panel, saved_gain=GAIN)
                self.assertTrue(f.buy(shop, 29), (shop, ry))
                self.assertEqual(panel.moves, [], (shop, ry))
        self.assertEqual(self._moves(), [])

    def test_the_plush_row_alone_is_the_old_behavior(self):
        panel = _panel_with_row_at(-0.095, 0.04, gain=GAIN)
        f = self._fetcher(panel, saved_gain=GAIN)
        self.assertIsNotNone(f.frame_panel("Event", plush_row=True))
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0)
        ox, _oy = panel.origin()
        self.assertAlmostEqual((ox + ItemFetch.PLUSH_ROW[0] - AIM[0]) / H, ItemFetch.PLUSH_ROW_SAFE_X, delta=0.002)
        first = self._hud_logs()[0]
        self.assertIn("HUD に重なる点 Plush の行・", first)
        self.assertNotIn("HUD に重なる点 店のボタン", first)
        self.assertEqual(len(self._moves()), 1)

    def test_a_shift_within_the_settle_distance_falls_through_to_the_old_branches(self):
        # ずらす量が PANEL_SETTLE_PX 以下（寄せ先を現在の位置のすぐ隣にして作る）→ 新しい枝には入らず、今までの枝
        # （板の真ん中を照準へ。ここでは真ん中のずれ 横 約 +3px）
        with patch.object(ItemFetch, "BUTTON_SAFE_X", -0.076):
            panel = _panel_with_button_at("Enkephalin", -0.075, 0.060, gain=GAIN)
            f = self._fetcher(panel, saved_gain=GAIN)
            f.frame_panel("Enkephalin")
        dx = panel.left0 + ItemFetch.TEMPLATE_SIZE[0] / 2 - panel.aim[0]
        self.assertLess(abs((-0.075 + 0.076) * H), ItemFetch.PANEL_SETTLE_PX)
        self.assertTrue(self._moves()[0].startswith(
            f"アイテム取得: 店の画面: 視点を動かす 横 {dx / GAIN[0]:.1f}・縦 0.0"), (self._moves()[0], dx))
