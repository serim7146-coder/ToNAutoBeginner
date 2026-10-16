"""Have Plush のつまみのドラッグを調べるための記録（FQ）。引く前・途中・後のつまみの帯を測って debug.log に書き、
行が見つからなかったときだけ「引く前」「引いている途中」の撮影を残す。引く点・長さ・回数は変えない。
本物の入力・OSC・撮影は使わない（偽の店・偽の撮影・偽の Windows。画像は numpy で作る）"""
from tests.support import *  # noqa: F401,F403

import numpy as np

import ActionExecutor
import ItemFetch
from tests.test_join_plush import PLUSH, _Shop, at

BEFORE_LOG = "アイテム取得: つまみ 実測（引く前）: "
AFTER_LOG = "アイテム取得: つまみ 実測（引いた後）: "


# ── knob_band ────────────────────────────────────
class TestKnobBand(unittest.TestCase):
    def _img(self):
        return np.full((300, 400, 3), 5, np.uint8)             # 溝（0〜10）

    def test_finds_the_gray_band(self):
        img = self._img()
        img[100:141, 198:204] = 60                              # つまみ（50〜80）
        self.assertEqual(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)), (200.5, 100, 140))

    def test_no_band_is_none(self):
        self.assertIsNone(ItemFetch.knob_band(self._img(), (200, 120), (1.0, 1.0)))
        self.assertIsNone(ItemFetch.knob_band(None, (200, 120), (1.0, 1.0)))
        self.assertIsNone(ItemFetch.knob_band("not an image", (200, 120), (1.0, 1.0)))

    def test_a_dark_band_is_not_the_knob(self):
        img = self._img()
        img[100:141, 198:204] = 20                              # KNOB_GRAY_MIN (30) 未満
        self.assertIsNone(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)))

    def test_a_saturated_band_is_not_picked(self):
        img = self._img()
        img[100:141, 198:204] = (0, 200, 0)                     # 緑の文字（BGR）
        self.assertIsNone(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)))

    def test_a_band_outside_the_window_is_not_picked(self):
        img = self._img()
        img[100:141, 258:264] = 60                              # 横 +58（窓は ±20）
        self.assertIsNone(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)))
        img = self._img()
        img[260:296, 198:204] = 60                              # 縦 +140（窓は ±60）
        self.assertIsNone(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)))

    def test_the_scale_widens_the_window(self):
        img = self._img()
        img[100:141, 228:234] = 60                              # 横 +28〜+33
        self.assertIsNone(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)))
        found = ItemFetch.knob_band(img, (200, 120), (2.0, 2.0))
        self.assertEqual(found, (230.5, 100, 140))

    def test_the_band_is_cut_at_the_window_edge(self):
        img = self._img()
        img[50:200, 198:204] = 60                               # 窓（縦 ±60 → 60〜180）より長い
        self.assertEqual(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)), (200.5, 60, 180))

    def test_a_thin_side_column_does_not_widen_the_band(self):
        img = self._img()
        img[100:141, 198:204] = 60
        img[100:103, 190] = 60                                  # 最大（41）の 6 割に満たない列
        self.assertEqual(ItemFetch.knob_band(img, (200, 120), (1.0, 1.0)), (200.5, 100, 140))

    def test_the_constants(self):
        self.assertEqual((ItemFetch.PLUSH_KNOB_WINDOW, ItemFetch.KNOB_GRAY_MIN, ItemFetch.KNOB_SAT_MAX,
                          ItemFetch.KNOB_MOVED_PX), ((20, 60), 30, 40, 5))


# ── 偽の店（つまみの帯を描く・on_mid を受ける）──────────────────
class _KnobShop(_Shop):
    """_Shop のつまみの帯つき。ドラッグ前は一番上・ドラッグ後は knob（または after_y）に帯を描く"""

    def __init__(self, test, moves=True, after_y=None, hide=(), **kw):
        super().__init__(test, **kw)
        self.moves = moves              # False なら、離しても帯は一番上のまま
        self.after_y = after_y          # 引いた後の帯の中心 y（None なら knob の y）
        self.hide = set(hide)           # {"before", "after"} の帯は描かない
        self.shots = []                 # (撮影, ドラッグの最中か)（順）
        self.in_drag = False
        self.shots_at_drag = []         # ドラッグを始めた時点の撮影の枚数
        self.mids = []                  # ドラッグごとに on_mid を渡されたか

    def capture(self):
        img, aim = super().capture()
        top = at(ItemFetch.PLUSH_KNOB_TOP)
        phase = "before" if self.knob == top else "after"
        if phase not in self.hide:
            if phase == "before" or not self.moves:
                cx, cy = top
            else:
                cx, cy = self.knob[0], (self.knob[1] if self.after_y is None else self.after_y)
            x, y = int(cx), int(cy)
            img[y - 20:y + 21, x - 3:x + 3] = 60                # 帯: 列 x-3〜x+2・行 y-20〜y+20
        self.shots.append((img, self.in_drag))
        return img, aim

    def drag(self, start, end, steps, step_sec, on_mid=None):
        self.shots_at_drag.append(len(self.shots))
        self.mids.append(on_mid is not None)
        if on_mid is not None:
            self.in_drag = True
            try:
                on_mid()
            finally:
                self.in_drag = False
        return super().drag(start, end, steps, step_sec)


def _keeper(f):
    kept = []
    f.on_miss = lambda bgr, why: kept.append((bgr, why))
    return kept


def _band_text(y_center):
    """_KnobShop が描く帯（列 cx-3〜cx+2・行 y-20〜y+20）を knob_band が読んだログの形"""
    cx = int(at(ItemFetch.PLUSH_KNOB_TOP)[0])
    return f"x {cx - 0.5:.1f}・y {int(y_center) - 20}〜{int(y_center) + 20}"


class TestKnobLogs(unittest.TestCase):
    def test_before_and_after_when_the_band_moved(self):
        shop = _KnobShop(self)
        self.assertTrue(shop.fetcher().buy_plush(PLUSH))
        top = at(ItemFetch.PLUSH_KNOB_TOP)
        target = at(ItemFetch.PLUSH_KNOB_TARGET)
        est = f"（見積もり ({top[0]:.1f}, {top[1]:.1f})）"
        before = BEFORE_LOG + _band_text(top[1]) + est
        after = AFTER_LOG + f"y {int(target[1]) - 20}〜{int(target[1]) + 20}（動いた {int(target[1]) - int(top[1])} px）"
        self.assertIn(before, shop.logs)
        self.assertIn(after, shop.logs)
        drag_i = next(i for i, m in enumerate(shop.logs) if m.startswith("アイテム取得: つまみをドラッグ"))
        self.assertLess(shop.logs.index(before), drag_i, "引く前の行はドラッグのログより前")
        self.assertGreater(shop.logs.index(after), drag_i, "引いた後の行はドラッグのログより後")

    def test_not_moved(self):
        shop = _KnobShop(self, moves=False)
        shop.fetcher().buy_plush(PLUSH)
        top = at(ItemFetch.PLUSH_KNOB_TOP)
        self.assertIn(AFTER_LOG + f"y {int(top[1]) - 20}〜{int(top[1]) + 20}（動いていない）", shop.logs)
        self.assertFalse([m for m in shop.logs if "動いた" in m])

    def test_a_move_under_the_threshold_is_not_moved(self):
        top = at(ItemFetch.PLUSH_KNOB_TOP)
        # しきい値は 5 px × 縦の倍率 2 = 10 px
        shop = _KnobShop(self, after_y=int(top[1]) + 9)
        shop.fetcher().buy_plush(PLUSH)
        self.assertTrue([m for m in shop.logs if m.startswith(AFTER_LOG) and m.endswith("（動いていない）")])
        shop = _KnobShop(self, after_y=int(top[1]) + 10)
        shop.fetcher().buy_plush(PLUSH)
        self.assertTrue([m for m in shop.logs if m.startswith(AFTER_LOG) and "（動いた 10 px）" in m])

    def test_cannot_compare_when_one_side_is_missing(self):
        top = at(ItemFetch.PLUSH_KNOB_TOP)
        est = f"（見積もり ({top[0]:.1f}, {top[1]:.1f})）"
        shop = _KnobShop(self, hide=("before",))
        shop.fetcher().buy_plush(PLUSH)
        self.assertIn(BEFORE_LOG + "見つからない" + est, shop.logs)
        self.assertIn(AFTER_LOG + "比べられない", shop.logs)
        shop = _KnobShop(self, hide=("after",))
        shop.fetcher().buy_plush(PLUSH)
        self.assertIn(BEFORE_LOG + _band_text(top[1]) + est, shop.logs)
        self.assertIn(AFTER_LOG + "比べられない", shop.logs)

    def test_a_missing_capture_measures_nothing_and_does_not_stop(self):
        shop = _KnobShop(self)
        f = shop.fetcher()
        f.capture = lambda: None
        f.knob_at = None
        self.assertIsNone(f._show_plush_row(lambda pt: (*at(pt), 2.0, 2.0)))
        self.assertTrue([m for m in shop.logs if m.startswith(BEFORE_LOG + "見つからない")])
        self.assertIn(AFTER_LOG + "比べられない", shop.logs)
        self.assertIsNone(f.before_shot)

    def test_the_moves_are_unchanged_by_the_record(self):
        a, b = _KnobShop(self, row_shift=8), _Shop(self, row_shift=8)
        self.assertTrue(a.fetcher().buy_plush(PLUSH))
        self.assertTrue(b.fetcher().buy_plush(PLUSH))
        self.assertEqual(a.events, b.events, "記録があってもなくても、引く点・長さ・押す点は同じ")


# ── 失敗したときだけ撮影を残す ────────────────────────
class TestKnobShots(unittest.TestCase):
    def test_a_failure_keeps_the_last_before_and_mid_shots_in_order(self):
        shop = _KnobShop(self, no_row=True)
        f = shop.fetcher()
        kept = _keeper(f)
        self.assertFalse(f.buy_plush(PLUSH))
        self.assertEqual([k[1] for k in kept], ["Have Plush の行", "Plush_引く前", "Plush_引いている途中"])
        n = shop.shots_at_drag[-1]                              # 最後のずらしのドラッグを始めた時点
        self.assertIs(kept[1][0], shop.shots[n - 1][0], "引く前 = 最後のずらしのドラッグの直前の撮影")
        self.assertIs(kept[2][0], shop.shots[n][0], "途中 = 最後のずらしのドラッグの最中の撮影")
        self.assertTrue(shop.shots[n][1], "途中の撮影はドラッグの最中（on_mid）で撮られた")
        self.assertFalse(shop.shots[n - 1][1])

    def test_a_success_keeps_nothing(self):
        shop = _KnobShop(self)
        f = shop.fetcher()
        kept = _keeper(f)
        self.assertTrue(f.buy_plush(PLUSH))
        self.assertEqual(kept, [])

    def test_the_mid_shot_is_taken_in_every_shift_drag_but_not_in_the_return_drags(self):
        shop = _KnobShop(self, no_row=True)
        f = shop.fetcher()
        _keeper(f)
        f.buy_plush(PLUSH)
        self.assertEqual(shop.mids, [True, False, True, False, True], "戻すドラッグには on_mid を渡さない")
        self.assertEqual(sum(1 for _img, mid in shop.shots if mid), 3, "途中の撮影は引くドラッグごとに 1 枚")

    def test_the_return_drag_is_not_measured(self):
        shop = _KnobShop(self, no_row=True)
        shop.fetcher().buy_plush(PLUSH)
        self.assertEqual(len([m for m in shop.logs if m.startswith(BEFORE_LOG)]), 3, "引くドラッグごとに 1 回（戻すは測らない）")
        self.assertEqual(len([m for m in shop.logs if m.startswith(AFTER_LOG)]), 3)

    def test_a_failing_save_does_not_stop_the_failure_path(self):
        shop = _KnobShop(self, no_row=True)
        f = shop.fetcher()
        saved = []

        def broken(bgr, why):
            saved.append(why)
            raise OSError("disk full")
        f.on_miss = broken
        self.assertFalse(f.buy_plush(PLUSH))
        self.assertEqual(saved, ["Have Plush の行", "Plush_引く前", "Plush_引いている途中"], "どれも試す")
        self.assertEqual(shop.kinds()[-1], "tab_up")

    def test_a_missing_capture_keeps_nothing_for_it(self):
        shop = _KnobShop(self, no_row=True)
        f = shop.fetcher()
        kept = _keeper(f)
        f.capture = lambda: None
        f.knob_at = None
        self.assertIsNone(f._show_plush_row(lambda pt: (*at(pt), 2.0, 2.0)))
        self.assertEqual(kept, [])

    def test_a_mouse_without_on_mid_still_works(self):
        shop = _Shop(self, no_row=True)                         # drag は 4 引数のまま
        f = shop.fetcher()
        kept = []
        f.on_miss = lambda bgr, why: kept.append(why)
        self.assertFalse(f.buy_plush(PLUSH))
        self.assertEqual(kept, ["Have Plush の行", "Plush_引く前"], "途中は撮らない（残すのは引く前だけ）")
        self.assertTrue([m for m in shop.logs if m.startswith(BEFORE_LOG + "見つからない")])
        self.assertIn(AFTER_LOG + "比べられない", shop.logs)
        shop = _Shop(self)
        self.assertTrue(shop.fetcher().buy_plush(PLUSH))

    def test_counting_mouse_passes_on_mid_only_when_the_inner_takes_it(self):
        calls = []

        class Takes:
            def drag(self, start, end, steps, step_sec, on_mid=None):
                calls.append(("takes", on_mid))
                return True

        class Plain:
            def drag(self, start, end, steps, step_sec):
                calls.append(("plain",))
                return True

        def mid():
            pass
        self.assertTrue(ItemFetch.CountingMouse(Takes()).drag((0, 0), (0, 1), 20, 0.03, on_mid=mid))
        self.assertTrue(ItemFetch.CountingMouse(Takes()).drag((0, 0), (0, 1), 20, 0.03))
        self.assertTrue(ItemFetch.CountingMouse(Plain()).drag((0, 0), (0, 1), 20, 0.03, on_mid=mid))
        self.assertEqual(calls, [("takes", mid), ("takes", None), ("plain",)])


# ── _FrontOnlyMouse.drag の on_mid ──────────────────────
class TestOnMidInTheDrag(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.front = 0x30
        for name, fake in (("window_origin", lambda h: (1000, 500)),
                           ("foreground_hwnd", lambda: self.front),
                           ("set_cursor_position", lambda p: self.events.append(("cursor", p)) or True),
                           ("mouse_down", lambda pause=False: self.events.append(("down",))),
                           ("mouse_up", lambda pause=False: self.events.append(("up",)))):
            p = patch.object(WindowOperator, name, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(ActionExecutor.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_on_mid_is_called_once_at_the_middle_step(self):
        mouse = ActionExecutor._FrontOnlyMouse(0x30)
        self.assertTrue(mouse.drag((10, 20), (10, 60), 20, 0.03, on_mid=lambda: self.events.append(("mid",))))
        self.assertEqual(self.events.count(("mid",)), 1)
        i = self.events.index(("mid",))
        moves_before = [e for e in self.events[:i] if e[0] == "cursor"]
        self.assertEqual(len(moves_before), 1 + 10, "最初に置いた分 ＋ 10 段目まで")
        self.assertEqual(self.events[i - 1], ("cursor", (1010, 540)), "10 段目 = 真ん中の点")
        self.assertEqual(self.events[-1], ("up",))
        self.assertEqual(len([e for e in self.events if e[0] == "cursor"]), 21)

    def test_without_on_mid_nothing_changes(self):
        mouse = ActionExecutor._FrontOnlyMouse(0x30)
        self.assertTrue(mouse.drag((10, 20), (10, 60), 20, 0.03))
        self.assertEqual(len(self.events), 1 + 1 + 20 + 1)

    def test_a_raising_on_mid_still_releases_the_button_and_goes_on(self):
        mouse = ActionExecutor._FrontOnlyMouse(0x30)

        def boom():
            raise RuntimeError("shot")
        with patch.object(ActionExecutor.DebugLog, "exception") as logged:
            self.assertTrue(mouse.drag((10, 20), (10, 60), 20, 0.03, on_mid=boom))
        logged.assert_called_once()
        self.assertEqual(len([e for e in self.events if e[0] == "cursor"]), 21, "最後の段まで動かす")
        self.assertEqual(self.events[-1], ("up",))
        self.assertEqual(self.events.count(("up",)), 1)

    def test_losing_the_front_still_releases_and_skips_on_mid_after_it(self):
        mouse = ActionExecutor._FrontOnlyMouse(0x30)
        called = []
        calls = [0]
        real = WindowOperator.set_cursor_position.side_effect

        def lose(p):
            calls[0] += 1
            if calls[0] == 5:
                self.front = 0x99
            return real(p)
        WindowOperator.set_cursor_position.side_effect = lose
        with self.assertRaises(ItemFetch.Stopped):
            mouse.drag((0, 0), (0, 40), 20, 0.03, on_mid=lambda: called.append(1))
        self.assertEqual(called, [], "真ん中の前に止まれば呼ばない")
        self.assertEqual(self.events[-1], ("up",))


if __name__ == "__main__":
    unittest.main()
