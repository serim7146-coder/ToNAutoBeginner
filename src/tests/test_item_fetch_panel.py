"""アイテム自動取得の店の中（DH）: 店の画面の全体を映す → Tab を押したままカーソルをボタンへ置いて
クリック（店のボタン・Equip）。カーソルは取得前の位置へ戻す"""
from tests.support import *  # noqa: F401,F403


class _Panel:
    """店の画面（見本）が撮影のどこに写っているかの偽の世界。視点を回すと逆へ動く（本当の感度 gain）。
    locate は撮った瞬間の写し方（project）を付けて返す"""

    GREEN = (0, 200, 0)

    def __init__(self, left=770.0, top=410.0, scale=1.0, gain=(0.9, 0.55), size=(1920, 1080)):
        import numpy as np
        self.left0, self.top0, self.scale, self.gain = left, top, scale, gain
        self.img = np.zeros((size[1], size[0], 3), np.uint8)
        self.aim = (size[0] / 2, size[1] / 2)
        self.mouse = [0, 0]
        self.moves = []
        self.clicks = []
        self.events = []            # Tab・クリックの順番
        self.locates = 0
        self.found = True
        self.on_click = None
        self.click_ok = True

    def origin(self):
        return (self.left0 - self.gain[0] * self.mouse[0], self.top0 - self.gain[1] * self.mouse[1])

    def locate(self, _img, point):
        self.locates += 1
        if not self.found:
            return None
        ox, oy = self.origin()
        s = self.scale

        def project(pt):
            return ox + s * pt[0], oy + s * pt[1], s, s
        return (*project(point), 30, project)

    def capture(self):
        return self.img, self.aim

    def move_rel(self, dx, dy):
        self.moves.append((dx, dy))
        self.mouse[0] += dx
        self.mouse[1] += dy

    def tab_down(self):
        self.events.append("tab_down")

    def tab_up(self):
        self.events.append("tab_up")

    def click_at(self, x, y):
        self.events.append(("click", round(x, 1), round(y, 1)))
        self.clicks.append((round(x, 1), round(y, 1)))
        if self.on_click:
            self.on_click()
        return self.click_ok

    def green(self, cx, cy, w=30, h=8):
        self.img[int(round(cy - h / 2)):int(round(cy + h / 2)),
                 int(round(cx - w / 2)):int(round(cx + w / 2))] = self.GREEN

    def inside(self):
        corners = ItemFetch.panel_corners(lambda pt: (self.origin()[0] + self.scale * pt[0],
                                                      self.origin()[1] + self.scale * pt[1]))
        return ItemFetch.panel_outside(corners, (self.img.shape[1], self.img.shape[0])) == (False, False)


class _FetchCase(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.logs = []
        self.seen = {"seq": 0, "id": 0}

    def _sleep(self, sec):
        self.now[0] += sec

    def _fetcher(self, panel, saved_gain=(0.9, 0.55), answers=(29,)):
        """Equip を押すたびに answers の次の id の Equipping が来る（None は来ない）"""
        answers = list(answers)

        def on_click():
            if len(panel.clicks) < 2:
                return                              # 店のボタン
            got = answers.pop(0) if answers else None
            if got is not None:
                self.seen["seq"] += 1
                self.seen["id"] = got
        panel.on_click = on_click
        return ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=panel.capture, mouse=panel,
            equip_seen=lambda: (self.seen["seq"], self.seen["id"]), stopped=lambda: None,
            log=self.logs.append, locate_fn=panel.locate, sleep=self._sleep,
            clock=lambda: self.now[0], saved_gain=saved_gain)


class TestShowTheWholePanel(_FetchCase):
    def test_the_template_size(self):
        import cv2
        import numpy as np
        data = np.fromfile(str(config.resource_path(ItemFetch.TEMPLATE_FILE)), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        self.assertEqual((img.shape[1], img.shape[0]), ItemFetch.TEMPLATE_SIZE)
        self.assertEqual((ItemFetch.PANEL_MARGIN, ItemFetch.PANEL_TRIES), (0.02, 6))

    def test_the_corners_and_the_margin(self):
        corners = ItemFetch.panel_corners(lambda pt: (10 + pt[0], 20 + 2 * pt[1], 1, 2))
        self.assertEqual(corners, [(10, 20), (390, 20), (10, 540), (390, 540)])
        size = (1000, 1000)                         # 余白は 20px
        self.assertEqual(ItemFetch.panel_outside([(20, 20), (980, 980)], size), (False, False))
        self.assertEqual(ItemFetch.panel_outside([(19, 500)], size), (True, False))
        self.assertEqual(ItemFetch.panel_outside([(981, 500)], size), (True, False))
        self.assertEqual(ItemFetch.panel_outside([(500, 19)], size), (False, True))
        self.assertEqual(ItemFetch.panel_outside([(500, 981)], size), (False, True))

    def test_inside_does_not_move_or_measure(self):
        panel = _Panel()
        f = self._fetcher(panel, saved_gain=None)
        with patch.object(f, "calibrate") as calibrate:
            self.assertTrue(f.buy("Survival", 29))
        calibrate.assert_not_called()
        self.assertEqual(panel.moves, [])
        self.assertEqual(panel.clicks[0], (770 + 266, 410 + 95), "店のボタンの点（撮影）")
        self.assertEqual(f.equip_point, ((770 + 180, 410 + 177), (1.0, 1.0)))
        self.assertTrue(any("四隅 入っている" in m for m in self.logs), self.logs)

    def test_sideways_only_moves_sideways(self):
        panel = _Panel(left=1700.0)                 # 右へはみ出す
        f = self._fetcher(panel, answers=(81,))
        self.assertTrue(f.buy("Event", 81))
        self.assertTrue(panel.inside())
        self.assertTrue(panel.moves)
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0, "縦は動かさない")
        ox, oy = panel.origin()
        self.assertAlmostEqual(panel.clicks[0][0], ox + 266, delta=0.1)
        self.assertAlmostEqual(panel.clicks[0][1], oy + 189, delta=0.1)

    def test_up_and_down_only_when_needed_and_after_sideways(self):
        panel = _Panel(left=-300.0, top=900.0)       # 左と下へはみ出す
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        self.assertTrue(panel.inside())
        first_vertical = next(i for i, (_dx, dy) in enumerate(panel.moves) if dy)
        self.assertTrue(all(dy == 0 for _dx, dy in panel.moves[:first_vertical]))
        self.assertTrue(all(dx == 0 for dx, _dy in panel.moves[first_vertical:]), "横が先")
        self.assertLess(sum(dx for dx, _ in panel.moves), 0, "左へはみ出す → 左へ回す")
        self.assertGreater(sum(dy for _, dy in panel.moves), 0)
        self.assertTrue(all(abs(dx) <= ItemFetch.STEP and abs(dy) <= ItemFetch.STEP
                            for dx, dy in panel.moves))

    def test_without_a_saved_gain_it_measures_before_moving(self):
        panel = _Panel(left=1700.0, gain=(2.2, 0.3))
        f = self._fetcher(panel, saved_gain=None)
        self.assertTrue(f.buy("Survival", 29))
        self.assertAlmostEqual(f.measured_gain[0], 2.2)
        self.assertTrue(panel.inside())

    def _measure_fixes_it(self, panel, f):
        """測ると合う（ここでは世界の側を戻して、測った後は入るようにする）"""
        def calibrate(_name):
            panel.gain = (0.9, 0.55)
            f.gain = f.measured_gain = (0.9, 0.55)
            return True
        return calibrate

    def test_a_wrong_saved_gain_is_measured_again_once(self):
        panel = _Panel(left=1700.0, gain=(0.0001, 0.55))  # 保存した 0.9 で動かしても入らない
        f = self._fetcher(panel)
        with patch.object(f, "calibrate", side_effect=self._measure_fixes_it(panel, f)) as calibrate:
            self.assertTrue(f.buy("Survival", 29))
        calibrate.assert_called_once_with("Survival")
        self.assertIn("アイテム取得: 感度 保存した値を使う（横 0.900・縦 0.550）", self.logs)
        self.assertIn("アイテム取得: 感度 測り直し（保存した値で店の画面が入りきらない）", self.logs)
        self.assertIsNone(f.saved_gain)
        self.assertTrue(panel.inside())

    def test_it_measures_again_only_once(self):
        panel = _Panel(left=1700.0, gain=(0.0001, 0.55))
        f = self._fetcher(panel)
        with patch.object(f, "calibrate", side_effect=lambda _n: setattr(f, "gain", (0.9, 0.55))
                          or setattr(f, "measured_gain", (0.9, 0.55)) or True) as calibrate:
            self.assertFalse(f.buy("Survival", 29))
        calibrate.assert_called_once()
        self.assertEqual(panel.clicks, [])

    def test_a_failed_measure_presses_nothing(self):
        panel = _Panel(left=1700.0)
        f = self._fetcher(panel, saved_gain=None)
        with patch.object(f, "calibrate", return_value=False):
            self.assertFalse(f.buy("Survival", 29))
        self.assertEqual((panel.moves, panel.clicks), ([], []))

    def test_it_gives_up_after_6_moves(self):
        panel = _Panel(left=1700.0, gain=(0.0001, 0.55))  # 動かしても変わらない
        f = self._fetcher(panel, saved_gain=None)
        with patch.object(f, "calibrate", side_effect=lambda _n: setattr(f, "gain", (0.9, 0.55))
                          or setattr(f, "measured_gain", (0.9, 0.55)) or True):
            self.assertFalse(f.buy("Survival", 29))
        self.assertIn("アイテム取得: 店の画面が入りきりません（6 回動かした）", self.logs)
        self.assertEqual(panel.clicks, [])

    def test_a_panel_too_big_to_fit(self):
        panel = _Panel(left=-500.0, top=0.0, scale=7.0)   # 2660px 幅
        f = self._fetcher(panel)
        with patch.object(panel, "origin", return_value=(960 - 7.0 * 190, 540 - 7.0 * 130)):
            self.assertFalse(f.buy("Survival", 29))
        self.assertIn("アイテム取得: 店の画面が入りきりません（真ん中に寄せても はみ出す）", self.logs)
        self.assertEqual(panel.moves, [])
        self.assertEqual(panel.clicks, [])

    def test_no_panel_gives_up_after_6_retakes(self):
        panel = _Panel()
        panel.found = False
        f = self._fetcher(panel)
        self.assertFalse(f.buy("Survival", 29))
        self.assertEqual(panel.locates, ItemFetch.MISS_TRIES + 1)
        self.assertIn("アイテム取得: 店の画面が見つかりません", self.logs)
        self.assertEqual(panel.clicks, [])


class TestEquipByTheRememberedPoint(_FetchCase):
    def test_the_green_text_around_the_remembered_point(self):
        panel = _Panel()
        equip = (770 + 180, 410 + 177)
        panel.green(equip[0] + 6, equip[1] + 5)
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        self.assertEqual(len(panel.clicks), 2)
        self.assertAlmostEqual(panel.clicks[1][0], equip[0] + 6, delta=1.0)
        self.assertAlmostEqual(panel.clicks[1][1], equip[1] + 5, delta=1.0)
        self.assertTrue(any("Equip: 点" in m and "緑の文字" in m for m in self.logs), self.logs)
        self.assertEqual(panel.locates, 1, "店の中では locate しない（覚えた点）")

    def test_without_green_the_remembered_point_after_waiting(self):
        panel = _Panel()
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        self.assertEqual(panel.clicks[1], (770 + 180, 410 + 177))
        self.assertGreaterEqual(self.now[0], ItemFetch.SHOP_OPEN_WAIT_SEC, "店が開くのを待った")
        self.assertTrue(any("覚えた点" in m for m in self.logs))

    def test_the_green_appearing_while_waiting(self):
        panel = _Panel()
        equip = (770 + 180, 410 + 177)
        f = self._fetcher(panel)
        real = panel.capture
        shots = []

        def capture():
            shots.append(1)
            if len(shots) == 3:
                panel.green(equip[0] - 4, equip[1])
            return real()
        panel.capture = capture
        f.capture = capture
        self.assertTrue(f.buy("Survival", 29))
        self.assertAlmostEqual(panel.clicks[1][0], equip[0] - 4, delta=1.0)
        self.assertLess(self.now[0], ItemFetch.SHOP_OPEN_WAIT_SEC + ItemFetch.EQUIP_WAIT_SEC)

    def test_three_presses_at_most_each_retaken(self):
        for answers, ok, presses in (([29], True, 1), ([0, 29], True, 2), ([None, 70, 29], True, 3),
                                     ([None, None, None, 29], False, 3)):
            panel = _Panel()
            self.seen = {"seq": 0, "id": 0}
            f = self._fetcher(panel, answers=answers)
            self.assertEqual(f.buy("Survival", 29), ok, answers)
            self.assertEqual(len(panel.clicks) - 1, presses, answers)

    def test_a_click_that_could_not_be_made_stops(self):
        panel = _Panel()
        panel.click_ok = False
        f = self._fetcher(panel)
        self.assertFalse(f.buy("Survival", 29))
        self.assertEqual(len(panel.clicks), 1, "店のボタンを押せなければ Equip へ進まない")


class TestTheViewIsSentBack(_FetchCase):
    def test_the_vertical_moves_are_sent_back(self):
        panel = _Panel(top=900.0)
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        dy = sum(d for _, d in panel.moves)
        self.assertGreater(dy, 0)
        f.restore_view()
        self.assertEqual(sum(d for _, d in panel.moves), 0)

    def test_nothing_moved_nothing_sent_back(self):
        panel = _Panel()
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        f.restore_view()
        self.assertEqual(panel.moves, [])


class TestTheOldAimIsGone(unittest.TestCase):
    def test_no_aiming_by_turning_the_view(self):
        for name in ("aim_click", "_aim_equip_by_hint", "_locate_is_sane", "_aim_line"):
            self.assertFalse(hasattr(ItemFetch.Fetcher, name), name)
        for name in ("Aimer", "screen_tol", "BUTTON_TOL", "TOL_X", "TOL_Y", "TOL_MIN_PX", "AIM_TRIES",
                     "GREEN_MISSES_FOR_LOCATE", "LOCATE_SCALE_TOL", "LOCATE_ASPECT_MAX", "TRUST_REJECTS",
                     "GAIN_KEEP", "GAIN_MIN_SENT"):
            self.assertFalse(hasattr(ItemFetch, name), name)
        src = Path(ItemFetch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("equip_hint", src)
        self.assertNotIn(".click()", src, "押すのは click_at だけ")

    def test_the_shop_button_is_not_aimed_at_by_the_view(self):
        """店のボタンが照準から遠くても、四隅が入っていれば視点を回さずにカーソルで押す"""
        panel = _Panel(left=100.0, top=100.0)       # ボタンは照準から 横 -594・縦 -345
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=panel.capture, mouse=panel,
            equip_seen=lambda: (1, 29), stopped=lambda: None, log=lambda _m: None,
            locate_fn=panel.locate, sleep=lambda _s: None, clock=time.monotonic, saved_gain=(0.9, 0.55))
        framed = f.frame_panel("Survival")
        self.assertIsNotNone(framed)
        self.assertTrue(f.press_shop("Survival", framed))
        self.assertEqual(panel.moves, [])
        self.assertEqual(panel.clicks, [(366.0, 195.0)])


class TestTabHeldThroughTheShop(_FetchCase):
    """Tab は店の操作の間に1回だけ押したまま: 押す → 0.1 秒 → 店のボタン → Equip（押し直しも）→ 離す"""

    def _sleeps(self):
        slept = []

        def sleep(sec):
            slept.append(sec)
            self.now[0] += sec
        return slept, sleep

    def test_the_order(self):
        panel = _Panel()
        f = self._fetcher(panel)
        slept, f.sleep = self._sleeps()
        before_tab = []
        real = panel.tab_down
        panel.tab_down = lambda: before_tab.append(len(slept)) or real()
        self.assertTrue(f.buy("Survival", 29))
        self.assertEqual(panel.events, ["tab_down", ("click", 1036.0, 505.0), ("click", 950.0, 587.0),
                                        "tab_up"])
        self.assertEqual(slept[before_tab[0]], config.FETCH_TAB_SETTLE_SEC, "押してすぐ 0.1 秒待つ")
        self.assertEqual(config.FETCH_TAB_SETTLE_SEC, 0.1)

    def test_presses_again_keep_tab_held(self):
        panel = _Panel()
        f = self._fetcher(panel, answers=(None, 0, 29))
        self.assertTrue(f.buy("Survival", 29))
        self.assertEqual(panel.events.count("tab_down"), 1)
        self.assertEqual(panel.events.count("tab_up"), 1)
        self.assertEqual(panel.events[0], "tab_down")
        self.assertEqual(panel.events[-1], "tab_up")
        self.assertEqual(len(panel.clicks), 4, "店のボタン＋Equip 3回")

    def test_the_panel_moves_are_done_before_tab(self):
        panel = _Panel(left=1700.0)
        real_move, real_tab = panel.move_rel, panel.tab_down
        panel.move_rel = lambda dx, dy: panel.events.append("move") or real_move(dx, dy)
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        first_tab = panel.events.index("tab_down")
        self.assertTrue(all(e == "move" for e in panel.events[:first_tab]))
        self.assertNotIn("move", panel.events[first_tab:], "Tab を押している間は視点を動かさない")
        self.assertIsNotNone(real_tab)

    def test_every_ending_lets_tab_go(self):
        def stop_on(n, error):
            def click(x, y):
                panel.events.append("click")
                if len([e for e in panel.events if e == "click"]) == n:
                    raise error
                return True
            return click
        for n, error in ((1, ItemFetch.Stopped("front_lost")), (2, ItemFetch.Stopped("frozen")),
                         (2, ItemFetch.Stopped("not_private")), (1, ValueError("x"))):
            panel = _Panel()
            f = self._fetcher(panel)
            panel.click_at = stop_on(n, error)
            with self.assertRaises(type(error)):
                f.buy("Survival", 29)
            self.assertEqual(panel.events[-1], "tab_up", error)
            self.assertEqual(panel.events.count("tab_up"), 1)

    def test_a_stop_while_waiting_after_tab_lets_it_go(self):
        panel = _Panel()
        f = self._fetcher(panel)
        f.stopped = lambda: "round" if "tab_down" in panel.events else None
        with self.assertRaises(ItemFetch.Stopped):
            f.buy("Survival", 29)
        self.assertEqual(panel.events, ["tab_down", "tab_up"])

    def test_failures_let_it_go(self):
        panel = _Panel()
        panel.click_ok = False
        f = self._fetcher(panel)
        self.assertFalse(f.buy("Survival", 29))
        self.assertEqual(panel.events[-1], "tab_up")
        panel = _Panel()
        f = self._fetcher(panel, answers=(None, None, None))
        self.assertFalse(f.buy("Survival", 29))
        self.assertEqual(panel.events[-1], "tab_up")

    def test_no_panel_no_tab(self):
        panel = _Panel()
        panel.found = False
        f = self._fetcher(panel)
        self.assertFalse(f.buy("Survival", 29))
        self.assertEqual(panel.events, [])


class TestCursorClickInTheWindow(unittest.TestCase):
    """_FrontOnlyMouse: Tab（前面の窓だけ）と、カーソルを置いて読み直してからのクリック"""
    HWND = 0x55

    def setUp(self):
        self.calls = []
        self.front = {"hwnd": self.HWND}
        self.reads = []                 # 読み直しで返す位置（空なら置いた点）
        self.placed = None
        rec = self.calls

        class Keyboard:
            def press(self, key):
                rec.append(("press", key))

            def release(self, key):
                rec.append(("release", key))

        class Mouse:
            def mouseDown(self, _pause=True):
                rec.append(("down",))

            def mouseUp(self, _pause=True):
                rec.append(("up",))

        def sleep(sec):
            rec.append(("sleep", sec))
            if self.on_sleep:
                self.on_sleep()

        def put(point):
            rec.append(("cursor", tuple(point)))
            self.placed = tuple(point)
            return True

        def read():
            got = self.reads.pop(0) if self.reads else self.placed
            rec.append(("read", got))
            return got
        self.on_sleep = None
        self.written = []
        for p in (patch.object(WindowOperator, "keyboard", Keyboard()),
                  patch.object(WindowOperator, "pydirectinput", Mouse()),
                  patch.object(WindowOperator, "time", _module_time(sleep)),
                  patch.object(ActionExecutor, "time", _module_time(sleep)),
                  patch.object(WindowOperator, "set_cursor_position", side_effect=put),
                  patch.object(WindowOperator, "cursor_position", side_effect=read),
                  patch.object(WindowOperator, "window_origin", return_value=(100, 50)),
                  patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: self.front["hwnd"]),
                  patch.object(DebugLog, "write", side_effect=self.written.append)):
            p.start()
            self.addCleanup(p.stop)
        self.allowed = {"on": True}
        self.mouse = ActionExecutor._FrontOnlyMouse(self.HWND, allowed=lambda: self.allowed["on"])

    def _click(self):
        return [("sleep", ItemFetch.CLICK_SEC)]

    def test_the_constants(self):
        self.assertEqual((config.FETCH_CURSOR_SETTLE_SEC, config.FETCH_CURSOR_TRIES, config.FETCH_CURSOR_TOL_PX),
                         (0.05, 3, 2))

    def test_place_wait_read_then_click(self):
        self.assertTrue(self.mouse.click_at(1035.6, 505.2))
        self.assertEqual(self.calls, [("cursor", (1136, 555)), ("sleep", 0.05), ("read", (1136, 555)),
                                      ("down",), ("sleep", ItemFetch.CLICK_SEC), ("up",)])
        self.assertIn("[操作] カーソル: 置いた (1136, 555) → 読み直し (1136, 555)（置き直し 0 回）",
                      self.written)

    def test_within_2px_is_placed(self):
        self.reads = [(1138, 553)]
        self.assertTrue(self.mouse.click_at(1035.6, 505.2))
        self.assertEqual(self.calls.count(("down",)), 1)
        self.assertEqual(len([c for c in self.calls if c[0] == "cursor"]), 1)

    def test_3px_off_is_placed_again(self):
        for off in ((3, 0), (0, -3)):
            self.calls.clear()
            self.reads = [(1136 + off[0], 555 + off[1])]
            self.assertTrue(self.mouse.click_at(1035.6, 505.2))
            self.assertEqual(len([c for c in self.calls if c[0] == "cursor"]), 2, off)

    def test_moved_back_to_the_middle_is_placed_again(self):
        self.reads = [(1280, 719), (1280, 719)]          # VRChat に真ん中へ戻された
        self.assertTrue(self.mouse.click_at(1035.6, 505.2))
        self.assertEqual([c for c in self.calls if c[0] in ("cursor", "read")],
                         [("cursor", (1136, 555)), ("read", (1280, 719)),
                          ("cursor", (1136, 555)), ("read", (1280, 719)),
                          ("cursor", (1136, 555)), ("read", (1136, 555))])
        self.assertEqual(self.calls.count(("down",)), 1)
        self.assertIn("[操作] カーソル: 置いた (1136, 555) → 読み直し (1136, 555)（置き直し 2 回）",
                      self.written)

    def test_three_misses_do_not_click(self):
        self.reads = [(1280, 719)] * 3
        self.assertFalse(self.mouse.click_at(1035.6, 505.2))
        self.assertNotIn(("down",), self.calls)
        self.assertEqual(len([c for c in self.calls if c[0] == "cursor"]), 3)
        self.assertIn("[操作] クリックしない（カーソルを (1136, 555) へ 3 回置けない）", self.written)

    def test_an_unreadable_cursor_is_not_placed(self):
        self.reads = [None] * 3
        self.assertFalse(self.mouse.click_at(10, 10))
        self.assertNotIn(("down",), self.calls)

    def test_the_front_lost_while_waiting(self):
        self.on_sleep = lambda: self.front.update(hwnd=0x66)
        with self.assertRaises(ItemFetch.Stopped) as cm:
            self.mouse.click_at(10, 10)
        self.assertEqual(cm.exception.args[0], "front_lost")
        self.assertNotIn(("down",), self.calls)

    def test_tab_only_to_the_window_in_front_and_in_private(self):
        self.mouse.tab_down()
        self.mouse.tab_up()
        self.assertEqual(self.calls, [("press", "tab"), ("release", "tab")])
        self.calls.clear()
        self.front["hwnd"] = 0x66
        with self.assertRaises(ItemFetch.Stopped):
            self.mouse.tab_down()
        self.front["hwnd"] = self.HWND
        self.allowed["on"] = False
        with self.assertRaises(ItemFetch.Stopped):
            self.mouse.tab_down()
        self.assertEqual(self.calls, [])
        self.assertIn("[操作] Tab を押した（離すまで押したまま）", self.written)

    def test_not_in_front_or_not_private_sends_nothing(self):
        self.front["hwnd"] = 0x66
        with self.assertRaises(ItemFetch.Stopped):
            self.mouse.click_at(10, 10)
        self.front["hwnd"] = self.HWND
        self.allowed["on"] = False
        with self.assertRaises(ItemFetch.Stopped):
            self.mouse.click_at(10, 10)
        self.assertEqual(self.calls, [])

    def test_no_window_rect_stops(self):
        with patch.object(WindowOperator, "window_origin", return_value=None):
            with self.assertRaises(ItemFetch.Stopped):
                self.mouse.click_at(10, 10)
        self.assertEqual(self.calls, [])

    def test_begin_and_entry_clicks_are_as_before(self):
        """Begin・ToN 入室のクリック（DG）は今どおり Tab を1回ごとに押して離す"""
        WindowOperator.click()
        self.assertEqual([c for c in self.calls if c[0] != "sleep"],
                         [("press", "tab"), ("down",), ("up",), ("release", "tab")])


class TestTabIsLetGoBeforeTheFrontIsGivenBack(unittest.TestCase):
    """_fetch_in_front: Tab を離す → 視点を戻す → 前面を返す → カーソルを戻す"""
    HWND = 0x55

    def setUp(self):
        self.events = []
        self.front = {"hwnd": 0x900}

        def borrow(hwnd):
            self.front["hwnd"] = hwnd
            return True, "loan"

        def give_back(_loan):
            self.events.append("return")
            self.front["hwnd"] = 0x900
            return True
        for p in (patch.object(WindowOperator, "cursor_position", return_value=(11, 22)),
                  patch.object(WindowOperator, "borrow_front", side_effect=borrow),
                  patch.object(WindowOperator, "return_front", side_effect=give_back),
                  patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: self.front["hwnd"]),
                  patch.object(WindowOperator, "set_cursor_position",
                               side_effect=lambda p: self.events.append("cursor") or True),
                  patch.object(DebugLog, "write")):
            p.start()
            self.addCleanup(p.stop)
        cfg = WindowConfig(hwnd=self.HWND, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=1)
        self.ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def test_tab_up_comes_first(self):
        for error in (None, ItemFetch.Stopped("front_lost"), ValueError("x")):
            self.events.clear()
            panel = _Panel(top=900.0)                # 縦を動かすので、視点を戻す動きもある
            panel.mouse_events = self.events
            real_tab_up, real_move = panel.tab_up, panel.move_rel
            panel.tab_up = lambda: self.events.append("tab_up") or real_tab_up()
            panel.move_rel = lambda dx, dy: self.events.append("move") or real_move(dx, dy)
            panel.restoring = False

            def click(x, y, error=error):
                if error is not None:
                    raise error
                return True
            panel.click_at = click
            f = ItemFetch.Fetcher(
                osc=MagicMock(), grounded=lambda: True, capture=panel.capture, mouse=panel,
                equip_seen=lambda c=itertools.count(): (next(c), 29), stopped=lambda: None,
                log=lambda _m: None, locate_fn=panel.locate, sleep=lambda _s: None, clock=time.monotonic,
                saved_gain=(0.9, 0.55))
            self.front["hwnd"] = self.HWND
            if error is None:
                self.ex._fetch_in_front(f, "Survival", 29)
            else:
                with self.assertRaises(type(error)):
                    self.ex._fetch_in_front(f, "Survival", 29)
            after = self.events[self.events.index("tab_up"):]
            self.assertEqual(after[0], "tab_up", error)
            self.assertIn("move", after, "視点を戻すのは Tab を離した後")
            self.assertEqual(after[-2:], ["return", "cursor"], error)


class TestTheCursorGoesBack(unittest.TestCase):
    """取得の前にあった Windows のカーソルの位置へ、前面を返した後に戻す"""
    HWND = 0x55
    BEFORE = (11, 22)

    def setUp(self):
        self.events = []
        self.front = {"hwnd": 0x900}

        def borrow(hwnd):
            self.events.append("borrow")
            self.front["hwnd"] = hwnd
            return True, "loan"

        def give_back(loan):
            self.events.append("return")
            if self.gives_back:
                self.front["hwnd"] = 0x900
            return self.gives_back
        self.gives_back = True
        for p in (patch.object(WindowOperator, "cursor_position",
                               side_effect=lambda: self.events.append("read") or self.BEFORE),
                  patch.object(WindowOperator, "borrow_front", side_effect=borrow),
                  patch.object(WindowOperator, "return_front", side_effect=give_back),
                  patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: self.front["hwnd"]),
                  patch.object(WindowOperator, "set_cursor_position",
                               side_effect=lambda p: self.events.append(("cursor", p)) or True)):
            p.start()
            self.addCleanup(p.stop)
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)
        cfg = WindowConfig(hwnd=self.HWND, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=1)
        self.ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def _fetcher(self, buy):
        f = MagicMock()
        f.buy.side_effect = buy
        f.measured_gain = None
        f.mouse.total = [0, 0]
        return f

    def test_every_ending_puts_it_back_after_giving_the_front_back(self):
        def stopped(*_a):
            raise ItemFetch.Stopped("round")

        def error(*_a):
            raise ValueError("x")
        for buy, expect in ((lambda *_a: True, "ok"), (lambda *_a: False, "failed"),
                            (stopped, ItemFetch.Stopped), (error, ValueError)):
            self.events.clear()
            f = self._fetcher(buy)
            if isinstance(expect, str):
                self.assertEqual(self.ex._fetch_in_front(f, "Survival", 29), expect)
            else:
                with self.assertRaises(expect):
                    self.ex._fetch_in_front(f, "Survival", 29)
            self.assertEqual(self.events, ["read", "borrow", "return", ("cursor", self.BEFORE)], expect)

    def test_an_error_while_giving_the_front_back_still_puts_it_back(self):
        def give_back(_loan):
            self.events.append("return")
            self.front["hwnd"] = 0x900                  # 返した後に例外
            raise OSError("x")
        with patch.object(WindowOperator, "return_front", side_effect=give_back):
            with self.assertRaises(OSError):
                self.ex._fetch_in_front(self._fetcher(lambda *_a: True), "Survival", 29)
        self.assertEqual(self.events, ["read", "borrow", "return", ("cursor", self.BEFORE)])

    def test_still_in_front_does_not_put_it_back(self):
        self.gives_back = False
        self.assertEqual(self.ex._fetch_in_front(self._fetcher(lambda *_a: True), "Survival", 29), "ok")
        self.assertEqual(self.events, ["read", "borrow", "return"])
        self.assertIn("[操作] [窓1] アイテム取得: この窓がまだ前面なので、カーソルを取得前の位置 (11, 22) "
                      "へ戻しません", self.written)

    def test_no_front_no_cursor(self):
        with patch.object(WindowOperator, "borrow_front", return_value=(False, None)):
            self.assertEqual(self.ex._fetch_in_front(self._fetcher(lambda *_a: True), "Survival", 29),
                             "failed")
        self.assertEqual(self.events, ["read"], "前面を借りられなければカーソルは動かしていない")


class TestKeepTheButtonsOffTheHud(_FetchCase):
    """押す点（店のボタン・Equip）が照準の左下の HUD（Intermission／Time の枠）に重なるなら、
    店の画面の真ん中を照準へ寄せてから押す（DK。2026-10-10 08:17 の窓2）"""

    def test_the_range(self):
        self.assertEqual((ItemFetch.HUD_X, ItemFetch.HUD_Y), ((-0.23, -0.07), (0.05, 0.15)))
        aim = (433.5, 362.5)
        # 6窓の撮影の HUD の明るい画素の範囲（窓1）の四隅は入る
        for x, y in ((295, 407), (371, 407), (295, 450), (371, 450)):
            self.assertTrue(ItemFetch.in_hud((x, y), aim, 663), (x, y))
        # 照準・照準の右・上は入らない
        for x, y in (aim, (500, 420), (380, 330), (433.5 - 0.05 * 663, 420), (350, 362.5 + 0.17 * 663)):
            self.assertFalse(ItemFetch.in_hud((x, y), aim, 663), (x, y))

    def test_the_range_follows_the_height(self):
        for height in (663, 1440, 331):
            aim = (height, height / 2)
            inside = (aim[0] - 0.13 * height, aim[1] + 0.076 * height)     # 08:17 の Equip の割合
            outside = (aim[0] - 0.05 * height, aim[1] + 0.076 * height)
            self.assertTrue(ItemFetch.in_hud(inside, aim, height), height)
            self.assertFalse(ItemFetch.in_hud(outside, aim, height), height)

    def _window2_0817(self, size=(1920, 1080), scale=1.0):
        """08:17 の窓2 の並び（高さで割った割合を、この大きさに直す）: 四隅は入っていて、
        真ん中のずれ 横 -0.12・縦 +0.03（高さ比）、Equip が照準から (-0.130, +0.076)"""
        h = size[1]
        aim = (size[0] / 2, h / 2)
        equip = (aim[0] - 0.130 * h, aim[1] + 0.076 * h)
        panel = _Panel(left=equip[0] - 180 * scale, top=equip[1] - 177 * scale, scale=scale, size=size)
        return panel

    def test_window_2_at_0817_is_moved_before_pressing(self):
        panel = self._window2_0817()
        self.assertTrue(panel.inside(), "四隅は入っている（前は動かさずに押していた）")
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        self.assertTrue(panel.moves, "視点を動かした")
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0, "横だけで外れる")
        equip = panel.clicks[1]
        self.assertFalse(ItemFetch.in_hud(equip, panel.aim, 1080), equip)
        self.assertTrue(any("HUD に重なる点 Equip" in m for m in self.logs), self.logs)
        self.assertTrue(any("HUD に重なる点 なし" in m for m in self.logs), "寄せた後にもう一度見た")

    def test_the_same_at_another_window_size(self):
        panel = self._window2_0817(size=(851, 663), scale=0.6)
        f = self._fetcher(panel)
        f.client_height = lambda: 663
        self.assertTrue(f.buy("Survival", 29))
        self.assertTrue(panel.moves)
        self.assertFalse(ItemFetch.in_hud(panel.clicks[1], panel.aim, 663))

    def test_the_client_height_not_the_shot_height(self):
        """撮影は窓の画像（タイトルバー込み 702）。割るのはクライアントの高さ（663）。境目の点で違いが出る"""
        panel = _Panel(size=(867, 702))
        equip = (panel.aim[0] - 0.072 * 663, panel.aim[1] + 0.10 * 663)   # 663 なら中・702 なら外
        self.assertTrue(ItemFetch.in_hud(equip, panel.aim, 663))
        self.assertFalse(ItemFetch.in_hud(equip, panel.aim, 702))
        panel.left0, panel.top0 = equip[0] - 180, equip[1] - 177
        self.assertTrue(panel.inside())
        f = self._fetcher(panel)
        f.client_height = lambda: 663
        self.assertTrue(f.buy("Survival", 29))
        self.assertTrue(panel.moves)

    def test_the_shop_button_in_the_hud_is_moved_too(self):
        h = 1080
        aim = (960, 540)
        button = (aim[0] - 0.15 * h, aim[1] + 0.07 * h)            # Survival の点が HUD の中
        panel = _Panel(left=button[0] - 266, top=button[1] - 95)
        self.assertTrue(panel.inside())
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        self.assertTrue(panel.moves)
        self.assertFalse(ItemFetch.in_hud(panel.clicks[0], aim, h))
        self.assertTrue(any("HUD に重なる点 店のボタン" in m for m in self.logs), self.logs)

    def test_both_outside_the_hud_do_not_move(self):
        panel = _Panel(left=640.0, top=380.0)       # 真ん中のずれ 横 -130 でも、点は HUD の外
        f = self._fetcher(panel)
        self.assertTrue(f.buy("Survival", 29))
        self.assertEqual(panel.moves, [])
        self.assertTrue(all(not ItemFetch.in_hud(c, panel.aim, 1080) for c in panel.clicks))

    def test_still_in_the_hud_when_centred_fails(self):
        panel = _Panel(left=960 - 190, top=540 - 130)               # 真ん中が照準の上
        with patch.object(ItemFetch, "HUD_X", (-0.05, 0.05)), patch.object(ItemFetch, "HUD_Y", (0.0, 0.1)):
            f = self._fetcher(panel)
            self.assertFalse(f.buy("Survival", 29))
        self.assertEqual(panel.clicks, [])
        self.assertIn("アイテム取得: 押す点が HUD に重なります（真ん中に寄せても Equip）", self.logs)

    def test_it_gives_up_after_6_moves_like_the_overflow(self):
        panel = self._window2_0817()
        panel.gain = (0.0001, 0.55)                 # 動かしても変わらない
        f = self._fetcher(panel, saved_gain=None)
        with patch.object(f, "calibrate", side_effect=lambda _n: setattr(f, "gain", (0.9, 0.55))
                          or setattr(f, "measured_gain", (0.9, 0.55)) or True):
            self.assertFalse(f.buy("Survival", 29))
        self.assertIn("アイテム取得: 店の画面が入りきりません（6 回動かした）", self.logs)

    def test_up_and_down_when_sideways_is_already_centred(self):
        """横は照準に合っていても、Enkephalin（真ん中から左へ 84）が HUD に入る → 縦だけ寄せる。
        動かした縦は、今どおり後で戻す"""
        panel = _Panel(left=960 - 190, top=540 + 108 - 130)       # 真ん中が照準の真下 0.1×高さ
        self.assertTrue(panel.inside())
        self.assertTrue(ItemFetch.in_hud((770 + 106, 518 + 129), panel.aim, 1080))
        f = self._fetcher(panel, answers=(70,))
        self.assertTrue(f.buy("Enkephalin", 70))
        self.assertTrue(panel.moves)
        self.assertTrue(all(dx == 0 for dx, _dy in panel.moves), "横はもう合っている")
        self.assertFalse(ItemFetch.in_hud(panel.clicks[0], panel.aim, 1080))
        f.restore_view()
        self.assertEqual(sum(dy for _dx, dy in panel.moves), 0, "縦は戻す")

    def test_the_window_height_is_asked_from_the_window(self):
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x55, osc_port=9000),
                                           WindowState(instance_type=config.INSTANCE_PRIVATE),
                                           lambda: True, lambda _m: None)
        made = {}

        class Stop(Exception):
            pass

        def fetcher(**kw):
            made.update(kw)
            raise Stop
        with patch.object(ItemFetch, "Fetcher", side_effect=fetcher), \
             patch.object(WindowOperator, "client_height", return_value=663) as height, \
             patch.object(DebugLog, "write"):
            with self.assertRaises(Stop):
                ex._fetch_item(ex._st.round_seq, "Survival", 29)
            self.assertEqual(made["client_height"](), 663)
        height.assert_called_with(0x55)

    def test_client_height(self):
        with patch.object(WindowOperator.win32gui, "GetClientRect", return_value=(0, 0, 851, 663)):
            self.assertEqual(WindowOperator.client_height(0x55), 663)
        with patch.object(WindowOperator.win32gui, "GetClientRect", return_value=(0, 0, 0, 0)):
            self.assertIsNone(WindowOperator.client_height(0x55))
        with patch.object(WindowOperator.win32gui, "GetClientRect", side_effect=OSError), \
             patch.object(DebugLog, "exception"):
            self.assertIsNone(WindowOperator.client_height(0x55))
