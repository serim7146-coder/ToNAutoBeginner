"""Guidance Plush の一覧（GD）: Have Plush の行の点が HUD に隠れるなら視点を横にずらしてから押す・
緑の上下の行の色の並び（赤／緑／黄）で Have Plush を確かめる。本物の入力・OSC・撮影は使わない
（偽の店・偽の撮影。画像は numpy で作る）"""
from tests.support import *  # noqa: F401,F403

import cv2
import numpy as np

import ItemFetch
from tests.test_item_fetch_panel import _FetchCase, _Panel
from tests.test_join_plush import PLUSH, SCALE, _Shop, at

H = 1080                                    # 偽の撮影の高さ（client_height が無いときの高さ）
AIM = (960.0, 540.0)                        # _Panel の照準
ROW = ItemFetch.PLUSH_ROW
GREEN, RED, YELLOW, WHITE = (0, 200, 0), (0, 0, 200), (0, 200, 200), (255, 255, 255)   # BGR


def _panel_with_row_at(rx, ry, **kw):
    """Have Plush の行の点が、照準から 横 rx・縦 ry（高さで割る）に写る _Panel（倍率 1）"""
    return _Panel(left=AIM[0] + rx * H - ROW[0], top=AIM[1] + ry * H - ROW[1], **kw)


# ── row_blocked ───────────────────────────────────
class TestRowBlocked(unittest.TestCase):
    def _blocked(self, rx, ry, height=H):
        aim = (500.0, 400.0)
        return ItemFetch.row_blocked((aim[0] + rx * height, aim[1] + ry * height), aim, height)

    def test_left_of_the_limit_and_inside_hud_y_is_blocked(self):
        self.assertTrue(self._blocked(-0.095, 0.04))

    def test_right_of_the_limit_is_not_blocked(self):
        self.assertFalse(self._blocked(-0.075, 0.04))
        self.assertFalse(self._blocked(-0.07, 0.04))
        self.assertFalse(self._blocked(-0.079, 0.04))

    def test_the_limit_itself_is_not_blocked(self):
        self.assertFalse(self._blocked(ItemFetch.PLUSH_ROW_HUD_X_MAX, 0.04))
        self.assertTrue(self._blocked(ItemFetch.PLUSH_ROW_HUD_X_MAX - 0.001, 0.04))

    def test_above_or_below_hud_y_is_not_blocked(self):
        self.assertFalse(self._blocked(-0.095, -0.01), "HUD より上")
        self.assertFalse(self._blocked(-0.095, 0.30), "HUD より下")
        self.assertTrue(self._blocked(-0.095, ItemFetch.HUD_Y[0]))
        self.assertTrue(self._blocked(-0.095, ItemFetch.HUD_Y[1]))

    def test_the_ratio_is_divided_by_the_height(self):
        self.assertTrue(self._blocked(-0.095, 0.04, height=720))
        self.assertFalse(self._blocked(-0.075, 0.04, height=720))

    def test_the_constants(self):
        self.assertEqual((ItemFetch.PLUSH_ROW_HUD_X_MAX, ItemFetch.PLUSH_ROW_SAFE_X, ItemFetch.PLUSH_ROW_PITCH),
                         (-0.080, -0.070, 16))
        self.assertEqual((ItemFetch.HUD_X, ItemFetch.HUD_Y), ((-0.27, -0.03), (0.02, 0.21)), "HUD の値は変えない")
        self.assertEqual(ItemFetch.PLUSH_SHIFTS, (0, 8, -8))


# ── frame_panel(plush_row) ─────────────────────────
class TestFramePanelPlushRow(_FetchCase):
    def _hud_log(self):
        return [m for m in self.logs if m.startswith("アイテム取得: 店の画面: 手がかり")]

    def test_a_blocked_row_moves_the_view_sideways_only_then_returns(self):
        panel = _panel_with_row_at(-0.095, 0.04)
        f = self._fetcher(panel)
        framed = f.frame_panel("Event", plush_row=True)
        self.assertIsNotNone(framed)
        want = (-0.095 - ItemFetch.PLUSH_ROW_SAFE_X) * H            # 画面の px（負 = 視点を左へ = 板が右へ）
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0, "縦は動かさない")
        self.assertAlmostEqual(sum(dx for dx, _dy in panel.moves), want / panel.gain[0], delta=1.0)
        self.assertLess(sum(dx for dx, _dy in panel.moves), 0)
        ox, oy = panel.origin()
        self.assertAlmostEqual((ox + ROW[0] - AIM[0]) / H, ItemFetch.PLUSH_ROW_SAFE_X, delta=0.002)
        self.assertAlmostEqual((oy + ROW[1] - AIM[1]) / H, 0.04, delta=1e-6)
        hud = self._hud_log()
        self.assertEqual(len(hud), 2, "動かす前と、撮り直した後")
        self.assertIn("HUD に重なる点 Plush の行・", hud[0])
        self.assertIn("見た箱 ", hud[0])
        self.assertIn("／Plush の行 横 -0.095・縦 +0.040", hud[0])
        self.assertIn("HUD に重なる点 なし・", hud[1])
        self.assertIn("／Plush の行 横 -0.070・縦 +0.040", hud[1])
        self.assertTrue([m for m in self.logs if m.startswith("アイテム取得: 店の画面: 視点を動かす 横 -30.")
                         and m.endswith("・縦 0.0")], self.logs)

    def test_the_same_layout_is_not_moved_without_plush_row(self):
        panel = _panel_with_row_at(-0.095, 0.04)
        f = self._fetcher(panel)
        self.assertIsNotNone(f.frame_panel("Event"))
        self.assertIsNotNone(f.frame_panel("Event", plush_row=False))
        self.assertEqual(panel.moves, [])
        self.assertFalse([m for m in self.logs if "Plush の行" in m], "ふつうの取得は行を見ない（ログにも出さない）")
        self.assertIn("HUD に重なる点 なし・", self._hud_log()[0])

    def test_a_row_at_the_safe_side_is_not_moved(self):
        panel = _panel_with_row_at(-0.075, 0.04)
        f = self._fetcher(panel)
        self.assertIsNotNone(f.frame_panel("Event", plush_row=True))
        self.assertEqual(panel.moves, [])
        self.assertIn("／Plush の行 横 -0.075・縦 +0.040", self._hud_log()[0])

    def test_a_row_above_the_hud_is_not_moved(self):
        panel = _panel_with_row_at(-0.095, -0.01)
        f = self._fetcher(panel)
        self.assertIsNotNone(f.frame_panel("Event", plush_row=True))
        self.assertEqual(panel.moves, [])

    def test_the_buttons_come_first(self):
        # 行の点も Equip の箱も HUD に掛かる配置。先に決めるのはボタン（板の真ん中を照準へ）で、行の点の量ではない
        panel = _Panel(left=700.0, top=500.0)
        f = self._fetcher(panel)
        f.frame_panel("Event", plush_row=True)
        first = self._hud_log()[0]
        self.assertIn("HUD に重なる点 Equip・Plush の行・", first)
        dx = (700 + ItemFetch.TEMPLATE_SIZE[0] / 2) - AIM[0]
        moves = [m for m in self.logs if m.startswith("アイテム取得: 店の画面: 視点を動かす")]
        self.assertTrue(moves[0].startswith(f"アイテム取得: 店の画面: 視点を動かす 横 {dx / panel.gain[0]:.1f}・縦 0.0"),
                        (moves[0], dx))
        row_only = (700 + ROW[0] - AIM[0] - ItemFetch.PLUSH_ROW_SAFE_X * H) / panel.gain[0]
        self.assertNotAlmostEqual(dx / panel.gain[0], row_only, delta=1.0, msg="2 つの量は違う")

    def test_a_button_in_the_hud_without_the_row_is_the_old_behavior(self):
        # Equip の箱だけが HUD に掛かる（行の点は HUD の縦の範囲より上）。今までと同じに板の真ん中を照準へ寄せる
        panel = _Panel(left=672.0, top=430.0)
        f = self._fetcher(panel)
        f.frame_panel("Event", plush_row=True)
        first = self._hud_log()[0]
        self.assertIn("HUD に重なる点 Equip・", first)
        self.assertNotIn("Plush の行・", first)
        dx = (672 + ItemFetch.TEMPLATE_SIZE[0] / 2) - AIM[0]
        moves = [m for m in self.logs if m.startswith("アイテム取得: 店の画面: 視点を動かす")]
        self.assertTrue(moves[0].startswith(f"アイテム取得: 店の画面: 視点を動かす 横 {dx / panel.gain[0]:.1f}・縦 0.0"),
                        (moves[0], dx))

    def test_the_move_count_is_shared_with_the_panel_tries(self):
        panel = _panel_with_row_at(-0.095, 0.04, gain=(0.0, 0.0))   # 動かしても板が動かない
        f = self._fetcher(panel)
        self.assertIsNone(f.frame_panel("Event", plush_row=True))
        moves = [m for m in self.logs if m.startswith("アイテム取得: 店の画面: 視点を動かす")]
        self.assertEqual(len(moves), ItemFetch.PANEL_TRIES)
        self.assertIn(f"アイテム取得: 店の画面が入りきりません（{ItemFetch.PANEL_TRIES} 回動かした）", self.logs)

    def test_too_close_to_move_fails_like_a_button(self):
        # 高さが小さい窓: 限界より左でも、寄せる量が PANEL_SETTLE_PX 以内なら動かせない
        panel = _Panel(left=AIM[0] - 0.082 * 150 - ROW[0], top=AIM[1] + 0.04 * 150 - ROW[1])
        f = self._fetcher(panel)
        f.client_height = lambda: 150
        self.assertIsNone(f.frame_panel("Event", plush_row=True))
        self.assertEqual(panel.moves, [])
        self.assertIn("アイテム取得: 押す点が HUD に重なります（Plush の行）", self.logs)

    def test_underscore_frame_passes_plush_row_through(self):
        f = self._fetcher(_Panel())
        with patch.object(f, "frame_panel", return_value=None) as fp:
            f._frame("Event", plush_row=True)
            fp.assert_called_with("Event", True)
            f._frame("Survival")
            fp.assert_called_with("Survival", False)


# ── buy_plush と buy（偽の店。照準を行が隠れる位置にする）──────────
class TestPassThrough(unittest.TestCase):
    BLOCKED_AIM = (1800.0, 100.0)           # 行（撮影 (246, 242)）が照準の左 −1.4・縦 +0.13 → HUD の縦の範囲の中

    def _shop(self, **kw):
        shop = _Shop(self, **kw)
        real = shop.capture
        shop.capture = lambda: (real()[0], self.BLOCKED_AIM)
        return shop

    def test_buy_plush_looks_at_the_row(self):
        shop = self._shop()
        f = shop.fetcher()
        self.assertFalse(f.buy_plush(PLUSH), "偽の店は視点を動かしても写り方が変わらない → 動かした回数の上限で失敗")
        moves = [m for m in shop.logs if m.startswith("アイテム取得: 店の画面: 視点を動かす")]
        self.assertEqual(len(moves), ItemFetch.PANEL_TRIES)
        self.assertTrue([m for m in shop.logs if "HUD に重なる点 Plush の行・" in m])
        self.assertNotIn("click", shop.kinds(), "Event も押さない")

    def test_buy_does_not_look_at_the_row(self):
        shop = self._shop()
        f = shop.fetcher()
        self.assertTrue(f.buy("Survival", PLUSH))
        self.assertFalse([e for e in shop.events if e[0] == "move"], "ふつうの取得は行を見ない（視点を動かさない）")
        self.assertFalse([m for m in shop.logs if "Plush の行" in m])


# ── 色の並び ─────────────────────────────────────
def _bar(img, cx, cy, color, w=40, h=7):
    img[cy - h // 2:cy - h // 2 + h, cx - w // 2:cx - w // 2 + w] = color


def _rows(up, mid=GREEN, down=YELLOW, scale=1):
    """黒地に 緑（真ん中）・上と下の PLUSH_ROW_PITCH 離れた行に横棒を描いた画像と、緑の重心"""
    img = np.zeros((300 * scale, 400 * scale, 3), np.uint8)
    cx, cy, pitch = 200 * scale, 150 * scale, ItemFetch.PLUSH_ROW_PITCH * scale
    for dy, color in ((-pitch, up), (0, mid), (pitch, down)):
        if color is not None:
            _bar(img, cx, cy + dy, color, w=40 * scale, h=7 * scale)
    return img, (float(cx), float(cy))


class TestRowOrder(unittest.TestCase):
    def test_red_green_yellow_is_have_plush(self):
        img, center = _rows(RED, GREEN, YELLOW)
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (True, "赤／黄"))

    def test_the_order_also_works_scaled(self):
        img, center = _rows(RED, GREEN, YELLOW, scale=2)
        self.assertEqual(ItemFetch.row_order_ok(img, center, (2.0, 2.0)), (True, "赤／黄"))

    def test_yellow_above_is_jailbird(self):
        img, center = _rows(YELLOW, GREEN, YELLOW)
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (False, "上 黄・下 黄"))

    def test_red_above_and_white_below_is_rot_lounge(self):
        img, center = _rows(RED, GREEN, WHITE)
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (False, "上 赤・下 なし"))

    def test_red_above_but_not_yellow_below(self):
        img, center = _rows(RED, GREEN, GREEN)
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (False, "上 赤・下 緑"))

    def test_the_wrapped_red_hue_counts(self):
        # H 175（Paradise Lost）は 165〜179 の側
        red = tuple(int(v) for v in cv2.cvtColor(np.uint8([[[175, 230, 230]]]), cv2.COLOR_HSV2BGR)[0][0])
        img, center = _rows(red, GREEN, YELLOW)
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (True, "赤／黄"))

    def test_nothing_above_or_below(self):
        img, center = _rows(None, GREEN, None)
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (False, "上 なし・下 なし"))

    def test_too_few_pixels_do_not_count(self):
        img, center = _rows(None, GREEN, YELLOW)
        img[int(center[1]) - ItemFetch.PLUSH_ROW_PITCH, 200:202] = RED          # 2 画素（最小は 3）
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (False, "上 なし・下 黄"))
        img[int(center[1]) - ItemFetch.PLUSH_ROW_PITCH, 200:203] = RED          # 3 画素
        self.assertEqual(ItemFetch.row_order_ok(img, center, (1.0, 1.0)), (True, "赤／黄"))

    def test_no_capture_is_not_ok(self):
        self.assertEqual(ItemFetch.row_order_ok(None, (10.0, 10.0), (1.0, 1.0)), (False, "上 なし・下 なし"))
        self.assertEqual(ItemFetch.row_order_ok("x", (10.0, 10.0), (1.0, 1.0)), (False, "上 なし・下 なし"))

    def test_the_color_area_counts_pixels_in_the_ranges(self):
        img, center = _rows(RED, GREEN, YELLOW)
        pos_up = (center[0], center[1] - ItemFetch.PLUSH_ROW_PITCH)
        win = ItemFetch.PLUSH_ROW_WINDOW
        self.assertEqual(ItemFetch.color_area(img, pos_up, (1.0, 1.0), ItemFetch.RED_HSV_RANGES, win), 40 * 7)
        self.assertEqual(ItemFetch.color_area(img, pos_up, (1.0, 1.0), (ItemFetch.YELLOW_HSV,), win), 0)
        self.assertEqual(ItemFetch.color_area(None, pos_up, (1.0, 1.0), ItemFetch.RED_HSV_RANGES, win), 0)
        self.assertEqual(ItemFetch.color_area(img, (-500.0, -500.0), (1.0, 1.0), ItemFetch.RED_HSV_RANGES, win), 0)


# ── _show_plush_row ──────────────────────────────
class _WrongFirstShop(_Shop):
    """ずらし 0 では緑の行の上下が Jailbird の並び（黄・黄）・ずらし +8 では Have Plush の並び（赤・黄）"""

    def capture(self):
        img, aim = super().capture()
        if self.knob == at(ItemFetch.PLUSH_KNOB_TARGET):
            x, y = (int(v) for v in at(ItemFetch.PLUSH_ROW))
            pitch = int(ItemFetch.PLUSH_ROW_PITCH * SCALE)
            img[y - 3:y + 4, x - 20:x + 21] = GREEN
            img[y - pitch - 3:y - pitch + 4, x - 20:x + 21] = YELLOW
            img[y + pitch - 3:y + pitch + 4, x - 20:x + 21] = YELLOW
        return img, aim


class TestShowPlushRow(unittest.TestCase):
    def test_a_wrong_order_goes_to_the_next_shift(self):
        shop = _WrongFirstShop(self, row_shift=8)
        f = shop.fetcher()
        self.assertTrue(f.buy_plush(PLUSH))
        top = at(ItemFetch.PLUSH_KNOB_TOP)
        t0, t8 = at(ItemFetch.PLUSH_KNOB_TARGET), at((ItemFetch.PLUSH_KNOB_TARGET[0], ItemFetch.PLUSH_KNOB_TARGET[1] + 8))
        r = lambda pt: tuple(round(v, 1) for v in pt)
        drags = [(e[1], e[2]) for e in shop.events if e[0] == "drag"]
        self.assertEqual(drags, [(r(top), r(t0)), (r(t0), r(top)), (r(top), r(t8))], "並び違いで 2 回目のドラッグへ")
        rows = [m for m in shop.logs if m.startswith("アイテム取得: Have Plush の行 (")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].endswith("・並びが違う（上 黄・下 黄）"), rows[0])
        self.assertIn(": 緑 (", rows[0])
        self.assertTrue(rows[1].endswith("・並び 赤／黄"), rows[1])
        click = [e for e in shop.events if e[0] == "click"][1]
        self.assertAlmostEqual(click[2], at(ItemFetch.PLUSH_ROW)[1], delta=1.5, msg="押すのは合った行")

    def test_a_wrong_order_every_time_fails_without_pressing_the_row(self):
        shop = _Shop(self)
        real = shop.capture

        def capture():
            img, aim = real()
            x, y = (int(v) for v in at(ItemFetch.PLUSH_ROW))
            pitch = int(ItemFetch.PLUSH_ROW_PITCH * SCALE)
            img[y - pitch - 3:y - pitch + 4, x - 20:x + 21] = YELLOW          # 上が黄（Jailbird）
            return img, aim
        shop.capture = capture
        f = shop.fetcher()
        self.assertFalse(f.buy_plush(PLUSH))
        self.assertEqual(len([e for e in shop.events if e[0] == "drag"]), 5, "上→0・戻す・上→+8・戻す・上→-8")
        self.assertEqual(shop.kinds().count("click"), 1, "Event だけ。行も Equip も押さない")
        self.assertIn("アイテム取得: Have Plush の行が見つかりません", shop.logs)
        self.assertTrue([m for m in shop.logs if "・並びが違う（上 黄・下 黄）" in m])

    def test_the_right_order_logs_it_and_presses(self):
        shop = _Shop(self)
        self.assertTrue(shop.fetcher().buy_plush(PLUSH))
        rows = [m for m in shop.logs if m.startswith("アイテム取得: Have Plush の行 (")]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].endswith("・並び 赤／黄"), rows[0])

    def test_no_green_still_logs_the_old_text(self):
        shop = _Shop(self, no_row=True)
        shop.fetcher().buy_plush(PLUSH)
        rows = [m for m in shop.logs if m.startswith("アイテム取得: Have Plush の行 (")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(m.endswith(": 緑なし") for m in rows), rows)
