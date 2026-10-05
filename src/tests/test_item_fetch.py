"""アイテム自動取得（店で Equip）"""
from tests.support import *  # noqa: F401,F403




class TestItemCatalog(unittest.TestCase):
    """item.json（テスト用の一時ファイル。本物は読まない）"""

    TEXT = ("// 8pagesに持ち込めるアイテムを1、持ち込めないアイテムを0としている。\n"
            "{\"Survival\": {\"29\": [\"Emerald Coil\", 0], \"36\": [\"Hamburger\", 1]},\n"
            "  // 途中のコメントも飛ばす\n"
            " \"Enkephalin\": {\"5\": [\"Radar\", 1]}}\n")

    def _file(self, text):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "item.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_comment_lines_are_skipped_and_the_table_is_read(self):
        items = ItemCatalog.load_items(self._file(self.TEXT))
        self.assertEqual(ItemCatalog.item_name(29, items), "Emerald Coil")
        self.assertEqual(ItemCatalog.item_name(5, items), "Radar")
        self.assertEqual(items[36].category, "Survival")
        self.assertIs(ItemCatalog.eight_pages_allowed(29, items), False)
        self.assertIs(ItemCatalog.eight_pages_allowed(36, items), True)
        self.assertIsNone(ItemCatalog.eight_pages_allowed(99, items), "表に無い")
        self.assertIsNone(ItemCatalog.item_name(99, items))

    def test_the_real_file_reads_and_is_bundled(self):
        ItemCatalog.take_load_problem()
        items = ItemCatalog.load_items(REPO_ROOT / "item.json")
        self.assertIsNone(ItemCatalog.take_load_problem())
        self.assertTrue(items)
        self.assertEqual(ItemCatalog.item_id_by_name(config.GUIDANCE_PLUSH_NAME, items), 70,
                         "Waldo の続行条件のアイテムを名前で引ける")
        names = [item.name for item in items.values()]
        self.assertEqual(len(names), len(set(names)), "同じ名前が2つあると名前で引けない")
        build =(REPO_ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("--include-data-files=item.json=item.json", build,
                      "exe に入れないと空の表で動く")

    def test_a_missing_file_is_an_empty_table(self):
        ItemCatalog.take_load_problem()
        items = ItemCatalog.load_items(Path(tempfile.gettempdir()) / "no_such_dir_bo" / "item.json")
        self.assertEqual(items, {})
        self.assertIn("item.json", ItemCatalog.take_load_problem())
        self.assertIsNone(ItemCatalog.take_load_problem(), "1回だけ")

    def test_a_broken_file_is_an_empty_table(self):
        for text in ("{\"Survival\": {\"29\": [\"Emerald Coil\", 0]", "[1, 2]", ""):
            self.assertEqual(ItemCatalog.load_items(self._file(text)), {}, text)

    def test_rows_of_the_wrong_shape_are_skipped(self):
        items = ItemCatalog.load_items(self._file(
            "{\"A\": {\"1\": [\"Ok\", 1], \"2\": [\"Two\", 2], \"3\": \"x\", \"x\": [\"Bad id\", 0],"
            " \"4\": [\"Short\"], \"6\": [\"Bool\", true]}, \"B\": [1]}"))
        self.assertEqual(set(items), {1})

    def test_config_reads_it_once_like_terrors(self):
        src = Path(config.__file__).read_text(encoding="utf-8")
        self.assertIn('ITEMS = ItemCatalog.load_items(resource_path("item.json"))', src)




class TestItemFetchSpeed(unittest.TestCase):
    """保存した感度（同じ送る間隔）があれば測らない。合わなければ測り直して1回やり直す。
    送る間隔は 0.005 秒（測り・合わせ・戻し）。感度は間隔と一緒に保存し、間隔が違えば測る"""

    TRUE_GAIN = (0.9, 0.55)

    def setUp(self):
        self.now = [0.0]
        self.sleeps = []
        SharedState.set_item_fetch_gain(None)
        self.addCleanup(SharedState.set_item_fetch_gain, None)

    def _sleep(self, sec):
        self.sleeps.append(sec)
        self.now[0] += sec

    def _fetcher(self, saved_gain=None, gain=TRUE_GAIN, start=(1300.0, 300.0)):
        world = {"mouse": [0, 0]}

        def locate(_img, _pt):
            return (start[0] - gain[0] * world["mouse"][0], start[1] - gain[1] * world["mouse"][1])
        self.mouse = TestItemFetch.FakeMouse(world)
        self.logs = []
        self.locate = locate
        return ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=lambda: ("shot", (960.0, 540.0)),
            mouse=self.mouse, equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            locate_fn=locate, sleep=self._sleep, clock=lambda: self.now[0], saved_gain=saved_gain)

    def _buy(self, f):
        with patch.object(f, "calibrate", wraps=f.calibrate) as calibrate, \
             patch.object(f, "equip", return_value=True) as equip:
            ok = f.buy("Survival", 29)
        return ok, calibrate, equip

    def test_a_saved_gain_skips_the_measure(self):
        f = self._fetcher(saved_gain=(0.85, 0.6))
        ok, calibrate, equip = self._buy(f)
        self.assertTrue(ok)
        calibrate.assert_not_called()
        equip.assert_called_once_with(29)
        self.assertIn("アイテム取得: 感度 保存した値を使う（横 0.850・縦 0.600）", self.logs)
        x, y = self.locate(None, None)
        self.assertLessEqual(abs(x - 960), 6)
        self.assertLessEqual(abs(y - 540), 4)

    def test_no_saved_gain_measures(self):
        ok, calibrate, _equip = self._buy(self._fetcher(saved_gain=None))
        self.assertTrue(ok)
        calibrate.assert_called_once_with("Survival")

    def test_a_wrong_saved_gain_measures_again_once(self):
        f = self._fetcher(saved_gain=(0.3, 0.2))        # 本当は 0.9・0.55（3 倍ずれている）
        ok, calibrate, equip = self._buy(f)
        self.assertTrue(ok)
        calibrate.assert_called_once_with("Survival")
        equip.assert_called_once_with(29)
        self.assertIn("アイテム取得: 感度 測り直し（保存した値で合わない）", self.logs)
        self.assertTrue(any("保存した感度で合いません" in m for m in self.logs), self.logs)
        self.assertAlmostEqual(f.aimer.gain[0], 0.9, places=1)
        self.assertIsNone(f.saved_gain, "合わなかった値で測りの量を決めない")

    def test_it_measures_again_only_once(self):
        f = self._fetcher(saved_gain=(0.3, 0.2))
        with patch.object(f, "calibrate", return_value=True) as calibrate, \
             patch.object(f, "aim_click", return_value=False) as aim, \
             patch.object(f, "equip", return_value=True):
            self.assertFalse(f.buy("Survival", 29))
        calibrate.assert_called_once()
        self.assertEqual(aim.call_count, 2, "保存した値で1回・測り直して1回")

    def test_the_mismatch_is_noticed_early(self):
        f = self._fetcher(saved_gain=(0.3, 0.2))
        f.aimer = ItemFetch.Aimer((0.3, 0.2))
        shots = []
        real = f.locate
        f.locate = lambda i, p: shots.append(1) or real(i, p)
        self.assertFalse(f.aim_click("Survival", trust_check=True))
        self.assertLess(len(shots), ItemFetch.AIM_TRIES, "12 回やりきる前にやめる")
        self.assertGreaterEqual(f.aimer.rejected, ItemFetch.TRUST_REJECTS)

    def test_without_the_check_it_keeps_aiming(self):
        f = self._fetcher()
        f.aimer = ItemFetch.Aimer((0.3, 0.2))
        f.aim_click("Survival")
        self.assertFalse(any("保存した感度で合いません" in m for m in self.logs))

    def test_the_step_is_5ms_everywhere(self):
        self.assertEqual(ItemFetch.STEP_SEC, 0.005)
        f = self._fetcher()
        self.assertTrue(f.calibrate("Survival"))
        self.assertTrue(f.aim_click("Survival"))
        f.restore_view()
        steps = [s for s in self.sleeps if s < 0.05]
        self.assertTrue(steps)
        self.assertEqual(set(steps), {0.005}, "測り・合わせ・戻しの刻みはすべて 0.005 秒")

    # ── 保存（ActionExecutor）──
    def _executor(self):
        cfg = WindowConfig(hwnd=0x55, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=3)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def test_only_a_gain_saved_with_the_same_step_is_used(self):
        ex = self._executor()
        SharedState.set_item_fetch_gain((0.9, 0.6, ItemFetch.STEP_SEC, "calib"))
        self.assertEqual(ex._saved_fetch_gain(), (0.9, 0.6))
        SharedState.set_item_fetch_gain((0.9, 0.6, 0.025, "calib"))
        self.assertIsNone(ex._saved_fetch_gain(), "前の間隔で保存した感度は使わない（測る）")
        SharedState.set_item_fetch_gain(None)
        self.assertIsNone(ex._saved_fetch_gain())

    def test_the_rejections_are_counted(self):
        aimer = ItemFetch.Aimer((0.81, 0.59))
        aimer.move_for((1041.0, 540.0), (960.0, 540.0))
        aimer.observe((1041.0 - 300.0, 540.0))              # 3.0 は 2 倍の外
        self.assertEqual(aimer.rejected, 1)
        aimer.move_for((1041.0, 540.0), (960.0, 540.0))
        aimer.observe((1041.0 - 90.0, 540.0))               # 0.9 は内
        self.assertEqual(aimer.rejected, 1)




class TestItemFetchAim(unittest.TestCase):
    """押してよいずれを見本の px で持ち、locate の倍率で画面の px に写す（Equip 横±8・縦±3、店のボタン
    横±14・縦±10、最小 1px）。保存する感度は calibrate で測った値だけ"""

    def setUp(self):
        self.now = [0.0]
        SharedState.set_item_fetch_gain(None)
        self.addCleanup(SharedState.set_item_fetch_gain, None)

    def _sleep(self, sec):
        self.now[0] += sec

    def _fetcher(self, locate, saved_gain=None):
        self.mouse = TestItemFetch.FakeMouse()
        self.logs = []
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=lambda: ("shot", (960.0, 540.0)),
            mouse=self.mouse, equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            locate_fn=locate, sleep=self._sleep, clock=lambda: self.now[0], saved_gain=saved_gain)
        f.aimer = ItemFetch.Aimer((0.9, 0.55))
        return f

    def test_the_tolerances(self):
        self.assertEqual(ItemFetch.BUTTON_TOL["Equip"], (8, 3))
        for shop in ("Enkephalin", "Survival", "Event"):
            self.assertEqual(ItemFetch.BUTTON_TOL[shop], (14, 10))
        equip = ItemFetch.screen_tol("Equip", (0.4, 0.4))
        self.assertAlmostEqual(equip[0], 3.2)
        self.assertAlmostEqual(equip[1], 1.2)
        shop = ItemFetch.screen_tol("Survival", (0.4, 0.4))
        self.assertGreater(shop[0], equip[0])
        self.assertGreater(shop[1], equip[1], "店のボタンは同じ倍率で Equip より大きい")
        self.assertEqual(ItemFetch.screen_tol("Equip", (0.1, 0.1)), (1.0, 1.0), "最小 1px")

    def test_far_and_small_equip_needs_to_be_closer(self):
        """倍率 0.4（遠い・小さい窓）: 縦ずれ 2px は押さない（許し 1.2px）・0.8px は押す"""
        f = self._fetcher(lambda _i, _p: (960.0, 542.0, 0.4, 0.4))
        self.assertFalse(f.aim_click("Equip"))
        self.assertEqual(self.mouse.clicks, 0)
        f = self._fetcher(lambda _i, _p: (960.0, 540.8, 0.4, 0.4))
        self.assertTrue(f.aim_click("Equip"))
        self.assertEqual(self.mouse.clicks, 1)

    def test_near_equip_allows_2px(self):
        f = self._fetcher(lambda _i, _p: (960.0, 542.0, 1.0, 1.0))
        self.assertTrue(f.aim_click("Equip"), "倍率 1.0 なら縦の許しは 3px")

    def test_the_shop_button_is_looser(self):
        f = self._fetcher(lambda _i, _p: (965.0, 543.0, 0.4, 0.4))   # Equip なら外・店なら内
        self.assertTrue(f.aim_click("Survival"))
        f = self._fetcher(lambda _i, _p: (965.0, 543.0, 0.4, 0.4))
        self.assertFalse(f.aim_click("Equip"))

    def test_the_log_has_the_offset_and_the_tolerance(self):
        f = self._fetcher(lambda _i, _p: (961.2, 540.8, 0.4, 0.4))
        f.aim_click("Equip")
        self.assertIn("アイテム取得: Equip クリック（合わせ 1 回・座標・ずれ 横 1.2・縦 0.8 ／ 許し 横 3.2・縦 1.2）",
                      self.logs)

    def test_locate_gives_the_scale(self):
        """本物の SIFT: 見本を半分の大きさで置くと倍率 0.5"""
        import cv2
        import numpy as np
        if not ItemFetch.available():
            self.skipTest("SIFT か見本が使えない")
        data = np.fromfile(str(config.resource_path(ItemFetch.TEMPLATE_FILE)), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        for scale in (1.0, 0.5):
            small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            canvas = np.zeros((1080, 1920, 3), np.uint8)
            canvas[300:300 + small.shape[0], 700:700 + small.shape[1]] = small
            x, y, sx, sy, inliers, project = ItemFetch.locate(canvas, ItemFetch.BUTTONS["Equip"])
            self.assertGreaterEqual(inliers, ItemFetch.MIN_INLIERS, "手がかりの数も返す")
            self.assertEqual(project(ItemFetch.BUTTONS["Equip"]), (x, y, sx, sy), "同じ写し方")
            px, py, _psx, _psy = project(ItemFetch.BUTTONS["Survival"])
            self.assertAlmostEqual(px, 700 + 266 * scale, delta=1.5)
            self.assertAlmostEqual(py, 300 + 95 * scale, delta=1.5)
            self.assertAlmostEqual(sx, scale, delta=0.05)
            self.assertAlmostEqual(sy, scale, delta=0.05)
            self.assertAlmostEqual(x, 700 + 180 * scale, delta=1.5)
            self.assertAlmostEqual(y, 300 + 177 * scale, delta=1.5)

    # ── 保存するのは測った値だけ ──
    def test_the_measured_gain_is_kept_apart_from_the_aim_corrections(self):
        world = {"mouse": [0, 0]}

        def locate(_img, _pt):
            # 最初は 0.9/0.55 で動き、合わせの途中から 1.1/0.65 になる（直した値が測った値と違う）
            g = (0.9, 0.55) if abs(world["mouse"][0]) < 60 else (1.1, 0.65)
            return (1300.0 - g[0] * world["mouse"][0], 300.0 - g[1] * world["mouse"][1], 1.0, 1.0)
        self.mouse = TestItemFetch.FakeMouse(world)
        self.logs = []
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=lambda: ("shot", (960.0, 540.0)),
            mouse=self.mouse, equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            locate_fn=locate, sleep=self._sleep, clock=lambda: self.now[0])
        self.assertTrue(f.calibrate("Survival"))
        measured = f.measured_gain
        self.assertAlmostEqual(measured[0], 0.9)
        f.aim_click("Survival")
        self.assertNotEqual(tuple(f.aimer.gain), measured, "合わせの途中で直した")
        self.assertEqual(f.measured_gain, measured, "測った値は変わらない")

    def test_using_the_saved_gain_measures_nothing(self):
        f = self._fetcher(lambda _i, _p: (960.0, 540.0, 1.0, 1.0), saved_gain=(0.9, 0.55))
        with patch.object(f, "equip", return_value=True):
            self.assertTrue(f.buy("Survival", 29))
        self.assertIsNone(f.measured_gain, "測っていない回は保存するものが無い")

    def test_only_the_measured_gain_is_saved(self):
        SharedState.set_item_fetch_gain((0.8, 0.6, ItemFetch.STEP_SEC, "calib"))
        cfg = WindowConfig(hwnd=0x55, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=3, item_id=0,
                         waiting_for_equip=True)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                           auto_begin_active=lambda: True)

        def buy(fetcher, shop, item_id):
            fetcher.aimer = ItemFetch.Aimer((1.4, 0.9))     # 保存した値で合わせ、途中で直した（測っていない）
            return True
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, return_value=True), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(ex, "_borrow_front", return_value=(True, None)), \
             patch.object(ex._osc, "stop_all"), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(WindowOperator, "foreground_hwnd", return_value=0), \
             patch.object(ActionExecutor.time, "sleep"):
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29), "ok")
        self.assertEqual(SharedState.get_item_fetch_gain(), (0.8, 0.6, ItemFetch.STEP_SEC, "calib"),
                         "合わせで直した値は保存しない")

    def test_an_unmarked_save_is_dropped(self):
        SharedState.set_item_fetch_gain((0.9, 0.6, ItemFetch.STEP_SEC))
        self.assertIsNone(SharedState.get_item_fetch_gain())




class TestEquipGreenCenter(unittest.TestCase):
    """Equip は locate の点の近く（見本の px で 横 ±30・縦 ±15）の緑の画素の重心（緑の文字のど真ん中）を
    狙う。緑が無ければ座標（直した (180, 177)）。作った画像（黒地に緑の矩形）で確かめる"""

    GREEN = (0, 200, 0)                 # BGR。HSV で H 60・S 255・V 200

    def setUp(self):
        self.now = [0.0]

    def _image(self, *rects):
        """rects は (中心 x, 中心 y, 幅, 高さ)"""
        import numpy as np
        img = np.zeros((1080, 1920, 3), np.uint8)
        for cx, cy, w, h in rects:
            img[int(round(cy - h / 2)):int(round(cy + h / 2)), int(round(cx - w / 2)):int(round(cx + w / 2))] = self.GREEN
        return img

    def _fetcher(self, img, aim, point, scale):
        self.mouse = TestItemFetch.FakeMouse()
        self.logs = []
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=lambda: (img, aim),
            mouse=self.mouse, equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            locate_fn=lambda _i, _p: (point[0], point[1], scale, scale),
            sleep=lambda s: self.now.__setitem__(0, self.now[0] + s), clock=lambda: self.now[0])
        f.aimer = ItemFetch.Aimer((0.9, 0.55))
        return f

    def test_the_constants_and_the_fixed_point(self):
        self.assertEqual(ItemFetch.BUTTONS["Equip"], (180, 177))
        self.assertEqual(ItemFetch.GREEN_WINDOW, (30, 15))
        self.assertEqual((ItemFetch.GREEN_HSV_LOW, ItemFetch.GREEN_HSV_HIGH), ((40, 80, 90), (90, 255, 255)))

    def test_it_aims_at_the_middle_of_the_green_text(self):
        """狙いの点から右下 (+6, +5)（見本の px）の緑の矩形 → その中心を狙う
        （照準は離れた所。照準の真下に緑が無いので、「照準の真下の緑」ではなくこちらで狙う）"""
        for s in (0.44, 1.0, 1.9):
            point = (900.0, 500.0)
            green = (point[0] + 6 * s, point[1] + 5 * s)
            img = self._image((green[0], green[1], 30 * s, 8 * s))
            f = self._fetcher(img, aim=(1300.0, 800.0), point=point, scale=s)
            f.aim_click("Equip")
            line = [m for m in self.logs if "合わせ 1 回目" in m][0]
            x, y = (float(v) for v in re.search(r"位置 \(([\d.]+), ([\d.]+)\)", line).groups())
            self.assertAlmostEqual(x, green[0], delta=1.0, msg=s)     # 画素に丸めた矩形の中心
            self.assertAlmostEqual(y, green[1], delta=1.0, msg=s)
            self.assertIn("狙い 緑の文字", line, s)
            self.assertIn("動かす", line, s)

    def test_without_green_it_aims_at_the_fixed_point(self):
        point = (900.0, 500.0)
        f = self._fetcher(self._image(), aim=point, point=point, scale=1.0)
        self.assertTrue(f.aim_click("Equip"))
        self.assertIn("アイテム取得: Equip クリック（合わせ 1 回・座標・ずれ 横 0.0・縦 0.0 ／ 許し 横 8.0・縦 3.0）",
                      self.logs)

    def test_green_outside_the_window_is_not_used(self):
        point = (900.0, 500.0)
        for s in (0.44, 1.9):
            above = (point[0], point[1] - 25 * s, 40 * s, 8 * s)        # 上のアイテム名
            left = (point[0] - 45 * s, point[1], 20 * s, 8 * s)         # 左の一覧
            img = self._image(above, left)
            self.assertIsNone(ItemFetch.green_center(img, point, (s, s)), s)

    def test_the_window_follows_the_scale(self):
        point = (900.0, 500.0)
        for s in (0.44, 1.9):
            inside = self._image((point[0] + 25 * s, point[1] + 12 * s, 4 * s, 3 * s))   # 見本の px で窓の中
            outside = self._image((point[0] + 35 * s, point[1], 4 * s, 3 * s))          # 見本の px で窓の外
            self.assertIsNotNone(ItemFetch.green_center(inside, point, (s, s)), s)
            self.assertIsNone(ItemFetch.green_center(outside, point, (s, s)), s)

    def test_a_few_green_pixels_are_not_enough(self):
        point = (900.0, 500.0)
        img = self._image()
        img[500, 905:907] = self.GREEN                                  # 2 画素（3 未満）
        self.assertIsNone(ItemFetch.green_center(img, point, (1.0, 1.0)))
        img[501, 905] = self.GREEN                                      # 3 画素
        self.assertEqual(ItemFetch.green_center(img, point, (1.0, 1.0)), (905 + 1 / 3, 500 + 1 / 3))

    def test_not_green_colours_are_not_used(self):
        import numpy as np
        point = (900.0, 500.0)
        for bgr in ((200, 0, 0), (0, 0, 200), (60, 70, 60), (0, 60, 0)):    # 青・赤・灰（S 低）・暗い緑（V 低）
            img = np.zeros((1080, 1920, 3), np.uint8)
            img[495:505, 890:910] = bgr
            self.assertIsNone(ItemFetch.green_center(img, point, (1.0, 1.0)), bgr)

    def test_the_shop_buttons_are_as_before(self):
        point = (900.0, 500.0)
        green = (point[0] + 6, point[1] + 5)
        f = self._fetcher(self._image((green[0], green[1], 30, 8)), aim=point, point=point, scale=1.0)
        self.assertTrue(f.aim_click("Survival"))
        line = [m for m in self.logs if "クリック" in m][0]
        self.assertNotIn("緑の文字", line)
        self.assertIn("ずれ 横 0.0・縦 0.0", line, "店のボタンは座標のまま")




class TestEquipUnderReticle(unittest.TestCase):
    """Equip の合わせの各回で、まず照準の周りの緑の文字を見て、そのずれが Equip の許しに入っていれば
    locate に関係なく動かさずに押す。照準合わせの1回ごとに debug.log に1行（作った画像・偽の locate）"""

    AIM = (960.0, 540.0)
    GREEN = (0, 200, 0)

    def setUp(self):
        self.now = [0.0]

    def _image(self, *rects):
        import numpy as np
        img = np.zeros((1080, 1920, 3), np.uint8)
        for cx, cy, w, h in rects:
            img[int(round(cy - h / 2)):int(round(cy + h / 2)), int(round(cx - w / 2)):int(round(cx + w / 2))] = self.GREEN
        return img

    def _fetcher(self, images, found):
        """images・found は回ごとの撮影と locate の結果（最後のものを繰り返す）"""
        images, found = list(images), list(found)
        self.mouse = TestItemFetch.FakeMouse()
        self.logs = []
        shots = iter(images + [images[-1]] * 20)
        results = iter(found + [found[-1]] * 20)
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=lambda: (next(shots), self.AIM),
            mouse=self.mouse, equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            locate_fn=lambda _i, _p: next(results),
            sleep=lambda s: self.now.__setitem__(0, self.now[0] + s), clock=lambda: self.now[0])
        f.aimer = ItemFetch.Aimer((0.9, 0.55))
        return f

    def _lines(self, n):
        return [m for m in self.logs if f"合わせ {n} 回目" in m]

    def test_green_under_the_reticle_is_pressed_even_if_locate_is_far(self):
        """照準の真下に緑・locate は 60 px 離れた点 → 1回目でも動かさずに押す"""
        img = self._image((self.AIM[0] + 1, self.AIM[1] + 1, 30, 8))
        f = self._fetcher([img], [(self.AIM[0] + 60, self.AIM[1] - 60, 1.0, 1.0, 14)])
        self.assertTrue(f.aim_click("Equip"))
        self.assertEqual(self.mouse.clicks, 1)
        self.assertEqual(self.mouse.moves, [])
        line = self._lines(1)[0]
        self.assertIn("アイテム取得: Equip 合わせ 1 回目: 手がかり 14 点・位置 (960.5, 540.5)・倍率 1.00/1.00・"
                      "狙い 照準の真下の緑・ずれ 横 0.5・縦 0.5・押す", line)
        self.assertTrue(any("クリック（合わせ 1 回・照準の真下の緑・" in m for m in self.logs))

    def test_green_under_the_reticle_but_outside_the_tolerance_is_not_pressed(self):
        img = self._image((self.AIM[0], self.AIM[1] + 6, 30, 4))           # 縦 6 px（許し 3 px の外）
        f = self._fetcher([img], [(self.AIM[0] + 60, self.AIM[1] - 60, 1.0, 1.0, 14)])
        f.aim_click("Equip")
        line = self._lines(1)[0]
        self.assertIn("狙い 座標", line, "今どおり locate の点（周りに緑が無いので座標）")
        self.assertIn("・動かす 横", line)
        self.assertEqual(self.mouse.clicks, 0)

    def test_green_under_the_reticle_but_too_far_sideways_is_not_pressed(self):
        img = self._image((self.AIM[0] + 12, self.AIM[1], 6, 4))           # 横 12 px（許し 8 px の外・窓 30 の内）
        f = self._fetcher([img], [(self.AIM[0] + 60, self.AIM[1] - 60, 1.0, 1.0, 14)])
        f.aim_click("Equip")
        self.assertIn("・動かす 横", self._lines(1)[0])
        self.assertEqual(self.mouse.clicks, 0)

    def test_no_green_under_the_reticle_is_as_before(self):
        point = (self.AIM[0] + 60, self.AIM[1] - 60)
        img = self._image((point[0], point[1], 30, 8))                      # 緑は locate の点の周りだけ
        f = self._fetcher([img], [(point[0], point[1], 1.0, 1.0, 20)])
        f.aim_click("Equip")
        line = self._lines(1)[0]
        self.assertIn("手がかり 20 点", line)
        self.assertIn("狙い 緑の文字・ずれ 横 59.5・縦 -60.5・動かす", line, "矩形の中心（画素に丸めた）")
        self.assertEqual(self.mouse.clicks, 0)

    def test_the_shop_buttons_do_not_look_under_the_reticle(self):
        img = self._image((self.AIM[0], self.AIM[1], 30, 8))
        f = self._fetcher([img], [(self.AIM[0] + 60, self.AIM[1] - 60, 1.0, 1.0, 30)])
        f.aim_click("Survival")
        self.assertEqual(self.mouse.clicks, 0, "店のボタンは照準の真下の緑を見ない")
        self.assertIn("狙い 座標・ずれ 横 60.0・縦 -60.0・動かす", self._lines(1)[0])

    def test_the_first_try_waits_for_locate(self):
        """倍率がまだ無い 1回目は、照準の真下に緑があっても locate を待つ。2回目からは前の回の倍率で見る"""
        img = self._image((self.AIM[0], self.AIM[1], 30, 8))
        f = self._fetcher([img], [None, (self.AIM[0] + 60, self.AIM[1] - 60, 1.0, 1.0, 9)])
        self.assertTrue(f.aim_click("Equip"))
        self.assertIn("アイテム取得: Equip 合わせ 1 回目: 手がかり - 点・見つからない", self.logs)
        self.assertIn("狙い 照準の真下の緑", self._lines(2)[0])

    def test_the_previous_scale_is_used_when_locate_fails(self):
        far = (self.AIM[0] + 60, self.AIM[1] - 60)
        empty = self._image()
        under = self._image((self.AIM[0], self.AIM[1], 30 * 0.5, 8 * 0.5))
        f = self._fetcher([empty, under], [(far[0], far[1], 0.5, 0.5, 11), None])
        self.assertTrue(f.aim_click("Equip"))
        line = self._lines(2)[0]
        self.assertIn("手がかり - 点", line)
        self.assertIn("倍率 0.50/0.50・狙い 照準の真下の緑", line)

    def test_an_unknown_count_shows_a_dash(self):
        img = self._image()
        f = self._fetcher([img], [(self.AIM[0] + 3, self.AIM[1] + 1, 1.0, 1.0)])
        f.aim_click("Survival")
        self.assertIn("アイテム取得: Survival 合わせ 1 回目: 手がかり - 点・位置 (963.0, 541.0)・倍率 1.00/1.00・"
                      "狙い 座標・ずれ 横 3.0・縦 1.0・押す", self.logs)




class TestEquipPredict(unittest.TestCase):
    """店のボタンを押す回の撮影で Equip の位置（照準からの差 d）と倍率を覚え、店の中では locate を使わず
    照準の真下の緑 → 予測の近くの緑 → 予測で合わせる。緑が3回続けて無いときだけ locate（倍率がおかしい結果は
    捨てる）。作った画像（緑の矩形は視点を回すと逆へ動く）と偽の locate"""

    AIM = (960.0, 540.0)
    GAIN = (0.9, 0.55)
    S = 0.47                            # 店のボタンの回の倍率（窓2 07:15）
    GREEN = (0, 200, 0)

    def setUp(self):
        self.now = [0.0]
        self.equip_locates = []

    def _world(self, equip_d, green=True, bad=(1100.0, 300.0, 0.74, 1.51, 12)):
        """equip_d: 店のボタンを押した時点の Equip の照準からの差（本当の値）"""
        import numpy as np
        world = {"mouse": [0, 0], "in_shop": False}
        s = self.S

        def equip_pos():
            return (self.AIM[0] + equip_d[0] - self.GAIN[0] * world["mouse"][0],
                    self.AIM[1] + equip_d[1] - self.GAIN[1] * world["mouse"][1])

        def capture():
            img = np.zeros((1080, 1920, 3), np.uint8)
            if world["in_shop"] and green:
                x, y = equip_pos()
                w, h = 30 * s, 8 * s
                img[int(round(y - h / 2)):int(round(y + h / 2)), int(round(x - w / 2)):int(round(x + w / 2))] = self.GREEN
            return img, self.AIM

        def project(pt):
            assert pt == ItemFetch.BUTTONS["Equip"]
            x, y = equip_pos()
            return x, y, s, s

        def locate(_img, pt):
            if pt == ItemFetch.BUTTONS["Equip"]:
                self.equip_locates.append(1)
                return bad                                  # 店の中: 手がかりが少なく外れる
            return self.AIM[0], self.AIM[1], s, s, 71, project  # 店のボタン: 照準の上（正しい）

        def on_click():
            world["in_shop"] = True
        world["on_click"] = on_click
        self.mouse = TestItemFetch.FakeMouse(world)
        self.logs = []
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=capture,
            mouse=self.mouse, equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            locate_fn=locate, sleep=lambda x: self.now.__setitem__(0, self.now[0] + x),
            clock=lambda: self.now[0])
        f.aimer = ItemFetch.Aimer(self.GAIN)
        self.world = world
        self.equip_pos = equip_pos
        return f

    def _equip_lines(self):
        return [m for m in self.logs if "Equip 合わせ" in m]

    def test_window_2_does_not_follow_a_bad_locate(self):
        """窓2 07:15: 店のボタンの回は正しい・店の中の locate は外れる → locate を呼ばず、予測の近くの緑で押す"""
        f = self._world(equip_d=(-38.4, 20.0))
        self.assertTrue(f.aim_click("Event"))
        self.assertIsNotNone(f.equip_hint)
        self.assertAlmostEqual(f.equip_hint["d"][0], -38.4)
        self.assertEqual(f.equip_hint["scale"], (self.S, self.S))
        self.assertTrue(f.aim_click("Equip"))
        self.assertEqual(self.equip_locates, [], "店の中では locate を呼ばない")
        self.assertEqual(self.mouse.clicks, 2)
        self.assertTrue(all(abs(dx) <= 6 and abs(dy) <= 6 for dx, dy in self.mouse.moves))
        self.assertLess(abs(sum(dx for dx, _ in self.mouse.moves)), 50, "外れた位置（横 +140）へ振れない")
        lines = self._equip_lines()
        self.assertIn("狙い 予測の近くの緑", lines[0])
        x, y = self.equip_pos()
        self.assertLessEqual(abs(x - self.AIM[0]), 8 * self.S + 0.5)
        self.assertLessEqual(abs(y - self.AIM[1]), 3 * self.S + 0.5)

    def test_without_green_it_moves_to_the_prediction_and_advances_d(self):
        f = self._world(equip_d=(-38.4, 20.0), green=False)
        f.aim_click("Event")
        f.aim_click("Equip")
        first = self._equip_lines()[0]
        self.assertIn("位置 (921.6, 560.0)・倍率 0.47/0.47・狙い 予測・ずれ 横 -38.4・縦 20.0・動かす", first)
        sent = (sum(dx for dx, _ in self.mouse.moves[:20]), sum(dy for _, dy in self.mouse.moves[:20]))
        self.assertEqual(sent, (-43, 36))
        second = self._equip_lines()[1]
        self.assertIn("位置 (960.3, 540.2)", second, "d が 送った量 × 感度 だけ進んだ（-38.4+43×0.9・20-36×0.55）")
        self.assertIn("狙い 予測", second)
        self.assertEqual(self.equip_locates, [])

    def test_three_misses_use_locate_and_bad_scales_are_dropped(self):
        f = self._world(equip_d=(-400.0, 0.0), green=False)
        f.aim_click("Event")
        f.aim_click("Equip")
        lines = self._equip_lines()
        self.assertIn("狙い 予測", lines[0])
        self.assertIn("狙い 予測", lines[1])
        self.assertEqual(len(self.equip_locates) > 0, True, "3回目から locate")
        self.assertIn("手がかり 12 点", lines[2])
        self.assertIn("狙い 写真（捨てた: 倍率 0.74/1.51）", lines[2])
        self.assertEqual(f.equip_hint["d"][0] < 0, True, "捨てた結果の位置（横 +140）は使わない")

    def test_a_sane_locate_is_used_as_the_last_resort(self):
        f = self._world(equip_d=(-400.0, 0.0), green=False, bad=(1000.0, 540.0, 0.5, 0.45, 16))
        f.aim_click("Event")
        f.aim_click("Equip")
        line = self._equip_lines()[2]
        self.assertIn("手がかり 16 点・位置 (1000.0, 540.0)・倍率 0.47/0.47・狙い 写真（最後の手段）", line)

    def test_the_sanity_check(self):
        f = self._world(equip_d=(0, 0))
        s = (0.47, 0.47)
        self.assertTrue(f._locate_is_sane((0, 0, 0.52, 0.40), s))
        self.assertFalse(f._locate_is_sane((0, 0, 0.57, 0.47), s), "2割以上")
        self.assertFalse(f._locate_is_sane((0, 0, 0.37, 0.47), s), "2割以上（小さい）")
        self.assertFalse(f._locate_is_sane((0, 0, 0.53, 0.40), s), "横と縦の比が 1.3 超")
        self.assertFalse(f._locate_is_sane((0, 0, 0.0, 0.0), s))
        self.assertFalse(f._locate_is_sane(None, s))
        self.assertEqual((ItemFetch.GREEN_MISSES_FOR_LOCATE, ItemFetch.LOCATE_SCALE_TOL,
                          ItemFetch.LOCATE_ASPECT_MAX), (3, 0.2, 1.3))

    def test_without_the_shop_buttons_position_locate_is_used_as_before(self):
        f = self._world(equip_d=(-38.4, 20.0), bad=(960.0, 540.0, 0.47, 0.47, 30))
        self.assertIsNone(f.equip_hint)
        f.aim_click("Equip")
        self.assertTrue(self.equip_locates, "今どおり locate で狙う")

    def test_d_is_from_the_reticle_not_from_the_shop_button(self):
        f = self._world(equip_d=(-38.4, 20.0))
        project = f.locate(None, ItemFetch.BUTTONS["Event"])[5]
        f.locate = lambda _i, _p: (self.AIM[0] + 5, self.AIM[1] + 3, self.S, self.S, 60, project)  # 許しの内
        self.assertTrue(f.aim_click("Event"))
        self.assertAlmostEqual(f.equip_hint["d"][0], -38.4, msg="照準からの差（店のボタンの位置からではない）")
        self.assertAlmostEqual(f.equip_hint["d"][1], 20.0)

    def test_a_found_green_resets_the_miss_count(self):
        """緑が無い → 無い → ある → 無い → 無い: 続けて3回ではないので locate を呼ばない"""
        f = self._world(equip_d=(-600.0, 0.0))
        f.aim_click("Event")
        shots = {"n": 0}
        real_capture = f.capture

        def capture():
            img, aim = real_capture()
            shots["n"] += 1
            if shots["n"] != 3:
                img[:] = 0                  # 3 回目だけ緑が見える
            return img, aim
        f.capture = capture
        f.aim_click("Equip")
        lines = self._equip_lines()
        self.assertIn("狙い 予測の近くの緑", lines[2])
        self.assertNotIn("写真", lines[3])
        self.assertNotIn("写真", lines[4])
        self.assertIn("写真", lines[5], "そこから3回続けて無ければ locate")

    def test_green_under_the_reticle_wins_over_a_wrong_prediction(self):
        f = self._world(equip_d=(1.0, 0.5))
        f.aim_click("Event")
        f.equip_hint["d"] = (-200.0, 0.0)          # 予測が外れていても
        self.assertTrue(f.aim_click("Equip"))
        self.assertEqual(self.mouse.moves, [], "照準の真下の緑で動かさずに押す")
        self.assertIn("狙い 照準の真下の緑", self._equip_lines()[0])

    def test_a_locate_without_project_leaves_no_hint(self):
        f = self._world(equip_d=(0, 0))
        f.locate = lambda _i, _p: (self.AIM[0], self.AIM[1], 0.5, 0.5, 30)
        self.assertTrue(f.aim_click("Survival"))
        self.assertIsNone(f.equip_hint)




class TestFetchYield(unittest.TestCase):
    """自動取得は、ほかの窓の続行・速度検知・突入のフリーズが張られたらその場でやめる（frozen）。
    マウスを送る前・クリックの前に前面がこの窓かを確かめ、違えばやめる（front_lost）。戻せなかった縦の視点は、
    次にツールがこの窓を前面にしたとき最初に戻す"""

    HWND = 0x55
    AIM = (960.0, 540.0)

    def setUp(self):
        for reset in (SharedState.equip_freeze_reset, SharedState.continue_round_reset,
                      SharedState.speed_freeze_reset, SharedState.round_freeze_reset):
            reset()
            self.addCleanup(reset)
        SharedState.set_item_fetch_gain(None)
        self.addCleanup(SharedState.set_item_fetch_gain, None)
        self.front = {"hwnd": self.HWND}
        self.sent = []
        self.clicks = []
        self.world = {"mouse": [0, 0]}

        def move(dx, dy):
            self.sent.append((dx, dy))
            self.world["mouse"][0] += dx
            self.world["mouse"][1] += dy
            if self.on_move:
                self.on_move(len(self.sent))
        self.on_move = None
        for p in (patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: self.front["hwnd"]),
                  patch.object(ActionExecutor._FetchMouse, "move_rel", side_effect=move),
                  patch.object(ActionExecutor._FetchMouse, "click", side_effect=lambda: self.clicks.append(1)),
                  patch.object(ActionExecutor.time, "sleep"),
                  patch.object(ItemFetch.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)

    def _executor(self):
        cfg = WindowConfig(hwnd=self.HWND, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=3, window_idx=3,
                         item_id=0, waiting_for_equip=True)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                           auto_begin_active=lambda: True)
        ex._osc.stop_all = lambda repeat=2: None
        return ex, st

    @staticmethod
    def _other_continue():
        SharedState.continue_round_start(WindowState())

    # ── 1・2. ほかの窓のフリーズで止まる・前面化しない ──
    def test_window_3_stops_when_another_window_starts_a_continue_round(self):
        """窓3 22:23: 取得の移動中にほかの窓の続行フリーズ → 前面化しない・マウスを送らない・frozen"""
        ex, st = self._executor()

        def move(fetcher):
            self._other_continue()          # 窓6 が続行ラウンド
            fetcher._check()
            return True
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, side_effect=move), \
             patch.object(ex, "_borrow_front") as borrow, \
             patch.object(PlaySound, "play_sound") as sound, \
             patch.object(DebugLog, "write") as write:
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29), "frozen")
        borrow.assert_not_called()
        sound.assert_not_called()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.clicks, [])
        self.assertTrue(any("結果 frozen" in c.args[0] for c in write.call_args_list))

    def test_a_freeze_while_waiting_for_the_lock_does_not_front(self):
        ex, st = self._executor()
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True,
                          side_effect=lambda f: self._other_continue() or True), \
             patch.object(ex, "_borrow_front") as borrow:
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29), "frozen")
        borrow.assert_not_called()

    def test_speed_and_round_freezes_stop_it_too(self):
        for start in (SharedState.speed_freeze_start, SharedState.round_freeze_start):
            SharedState.speed_freeze_reset()
            SharedState.round_freeze_reset()
            ex, st = self._executor()
            start(WindowState())
            self.assertEqual(ex._fetch_stopped(st.round_seq, time.time() + 10), "frozen", start.__name__)

    def test_its_own_equip_wait_does_not_stop_it(self):
        ex, st = self._executor()
        SharedState.equip_freeze_start(st)
        self.assertIsNone(ex._fetch_stopped(st.round_seq, time.time() + 10))

    def test_frozen_gives_no_notice(self):
        ex, st = self._executor()
        st.in_round = False
        st.round_end_seen = True
        SharedState.set_item_fetch(True)
        self.addCleanup(SharedState.set_item_fetch, False)

        def press(*_a, **_kw):
            st.begin_done = True
            return True

        def fetch(*_a):
            st.waiting_for_equip = False
            return "frozen"
        with patch.object(config, "BEGIN_WAIT_SEC", 0), patch.object(config, "ITEMS",
                                                                     {29: ItemCatalog.Item("T", "Survival", True)}), \
             patch.object(ex, "_begin_move"), patch.object(ex, "_start_use_spam", return_value=None), \
             patch.object(ex, "_wait_round_end", return_value=True), \
             patch.object(ex, "_handle_item_lost", return_value=True), \
             patch.object(ex, "_wait_other_windows", return_value=True), \
             patch.object(ex, "_begin_precheck", return_value=True), \
             patch.object(ex, "_press_begin", side_effect=press), patch.object(ex, "_confirm_begin"), \
             patch.object(ex, "_fetch_item", side_effect=fetch), \
             patch.object(ex, "_attend_to_item_loss") as attend, \
             patch.object(PlaySound, "play_sound"):
            st.last_lost_item_id = 29
            ex.do_after_round()
        attend.assert_not_called()

    # ── 3. 照準合わせの途中 ──
    def _aiming(self, start=(1500.0, 300.0)):
        """前面だけ確かめるマウスで店のボタンを合わせる Fetcher（照準から遠いので何回も動かす）"""
        ex, st = self._executor()
        deadline = time.time() + 10

        def locate(_img, _pt):
            return (start[0] - 0.9 * self.world["mouse"][0], start[1] - 0.55 * self.world["mouse"][1], 1.0, 1.0)
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=lambda: ("shot", self.AIM),
            mouse=ActionExecutor._FrontOnlyMouse(self.HWND, lambda: ex._fetch_stopped(st.round_seq, deadline)),
            equip_seen=lambda: (0, 0), stopped=lambda: ex._fetch_stopped(st.round_seq, deadline),
            log=lambda _m: None, locate_fn=locate)
        f.aimer = ItemFetch.Aimer((0.9, 0.55))
        return f

    def test_a_continue_freeze_while_aiming_sends_no_more(self):
        f = self._aiming()
        self.on_move = lambda n: self._other_continue() if n == 2 else None
        with self.assertRaises(ItemFetch.Stopped) as caught:
            f.aim_click("Survival")
        self.assertEqual(caught.exception.args[0], "frozen")
        self.assertEqual(len(self.sent), 2, "次のマウスを送らない")
        self.assertEqual(self.clicks, [])

    def test_losing_the_front_while_aiming_sends_no_more(self):
        f = self._aiming()
        self.on_move = lambda n: self.front.update(hwnd=0x66) if n == 2 else None
        with self.assertRaises(ItemFetch.Stopped) as caught:
            f.aim_click("Survival")
        self.assertEqual(caught.exception.args[0], "front_lost")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.clicks, [])

    def test_no_click_without_the_front(self):
        f = self._aiming(start=self.AIM)            # もう合っている
        self.front["hwnd"] = 0x66
        with self.assertRaises(ItemFetch.Stopped):
            f.aim_click("Survival")
        self.assertEqual(self.clicks, [], "クリックも送らない")

    def test_the_equip_aim_by_hint_is_guarded_too(self):
        f = self._aiming()
        f.equip_hint = {"d": (300.0, 0.0), "scale": (1.0, 1.0)}
        self.on_move = lambda n: self.front.update(hwnd=0x66) if n == 1 else None
        with self.assertRaises(ItemFetch.Stopped):
            f.aim_click("Equip")
        self.assertEqual(len(self.sent), 1)

    # ── 4. 戻せなかった縦の視点 ──
    def test_a_view_left_behind_is_restored_soon_by_borrowing_the_front(self):
        """ほかの窓に前面を取られて戻せなかった縦の視点を、ラウンド突入までに戻しに行く。
        ほかの窓が誰もフリーズしていなければ、前面を一瞬借りて戻して返す"""
        ex, _st = self._executor()
        ex._pending_view_dy = 20
        self.front["hwnd"] = 0x66
        borrowed = []

        def borrow(hwnd):
            borrowed.append(hwnd)
            self.front["hwnd"] = hwnd
            return True, "loan"
        with patch.object(WindowOperator, "borrow_front", side_effect=borrow), \
             patch.object(WindowOperator, "return_front") as give:
            ex._restore_view_loop()
        self.assertEqual(borrowed, [self.HWND])
        self.assertEqual(self.sent, [(0, 5)] * 4)
        give.assert_called_once_with("loan")
        self.assertEqual(ex._pending_view_dy, 0)

    def test_it_waits_while_another_window_is_frozen(self):
        """人がほかの窓を操作している（フリーズ中）あいだは前面を借りない"""
        ex, _st = self._executor()
        ex._pending_view_dy = 20
        self.front["hwnd"] = 0x66
        other = WindowState()
        SharedState.continue_round_start(other)
        borrowed = []
        waits = []

        def sleep(_sec):
            waits.append(list(borrowed))
            if len(waits) == 3:
                SharedState.continue_round_end(other)   # 続行ラウンドが終わった
        with patch.object(ActionExecutor.time, "sleep", side_effect=sleep), \
             patch.object(WindowOperator, "borrow_front",
                          side_effect=lambda h: borrowed.append(h) or (True, None)), \
             patch.object(WindowOperator, "return_front"):
            ex._restore_view_loop()
        self.assertTrue(all(w == [] for w in waits[:3]), "フリーズの間は借りない")
        self.assertEqual(borrowed, [self.HWND])

    def test_it_restores_at_once_when_the_window_is_in_front(self):
        ex, _st = self._executor()
        ex._pending_view_dy = 20
        SharedState.continue_round_start(WindowState())  # ほかの窓がフリーズ中でも、前面ならその場で
        with patch.object(WindowOperator, "borrow_front") as borrow:
            ex._restore_view_loop()
        borrow.assert_not_called()
        self.assertEqual(self.sent, [(0, 5)] * 4)

    def test_a_fetch_that_could_not_restore_starts_it(self):
        ex, st = self._executor()

        def buy(fetcher, shop, item_id):
            fetcher.mouse.move_rel(0, -20)
            self.front["hwnd"] = 0x66                   # 人がほかの窓へ（ラウンド突入までに取れない）
            raise ItemFetch.Stopped("front_lost")
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, return_value=True), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(WindowOperator, "borrow_front", return_value=(True, None)), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(ActionExecutor.ActionExecutor, "_restore_view_soon") as soon:
            ex._fetch_item(st.round_seq, "Survival", 29)
        soon.assert_called_once()

    def test_the_vertical_view_left_behind_is_restored_on_the_next_front(self):
        ex, st = self._executor()

        def buy(fetcher, shop, item_id):
            for _ in range(3):
                fetcher.mouse.move_rel(6, -6)           # 縦 -18 送った
            fetcher.mouse.move_rel(0, -2)
            self.front["hwnd"] = 0x66                   # 人がほかの窓へ
            return True
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, return_value=True), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(WindowOperator, "borrow_front", return_value=(True, None)), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(ActionExecutor.ActionExecutor, "_restore_view_soon"):   # 裏で戻すのは別に見る
            ex._fetch_item(st.round_seq, "Survival", 29)
        self.assertEqual(ex._pending_view_dy, 20)
        self.sent.clear()
        self.front["hwnd"] = self.HWND
        with patch.object(WindowOperator, "borrow_front", return_value=(True, None)), \
             patch.object(DebugLog, "write") as write:
            ex._borrow_front()                          # 次にこの窓を前面にした（Begin のクリックなど）
            first = list(self.sent)
            ex._borrow_front()
        self.assertEqual(first, [(0, 5)] * 4, "最初に縦だけ戻す（6px 以下の刻み）")
        self.assertEqual(self.sent, first, "2回目は戻さない（もう覚えていない）")
        self.assertEqual(ex._pending_view_dy, 0)
        self.assertIn("[操作] [窓3] アイテム取得: 残っていた縦の視点を戻した（縦 20）",
                      [c.args[0] for c in write.call_args_list])

    def test_a_restored_view_leaves_nothing_behind(self):
        ex, st = self._executor()

        def buy(fetcher, shop, item_id):
            fetcher.mouse.move_rel(0, -12)
            return True
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, return_value=True), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(WindowOperator, "borrow_front", return_value=(True, None)), \
             patch.object(WindowOperator, "return_front"):
            ex._fetch_item(st.round_seq, "Survival", 29)
        self.assertEqual(ex._pending_view_dy, 0, "前面のまま戻せたので覚えない")
        self.assertEqual(sum(dy for _dx, dy in self.sent), 0)

    def test_a_frozen_fetch_still_restores_the_view_when_in_front(self):
        ex, st = self._executor()

        def buy(fetcher, shop, item_id):
            fetcher.mouse.move_rel(0, -12)
            self._other_continue()
            fetcher._check()
            return True
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, return_value=True), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(WindowOperator, "borrow_front", return_value=(True, None)), \
             patch.object(WindowOperator, "return_front"):
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29), "frozen")
        self.assertEqual(sum(dy for _dx, dy in self.sent), 0, "止まった後でも、前面なら縦は戻す")
        self.assertEqual(ex._pending_view_dy, 0)




class TestItemFetch(unittest.TestCase):
    """アイテム自動取得。ツールが Begin を押す OSC の窓でアイテムロストのとき、Begin が通った後に
    店の前へ移動 → 前面化 → 特徴点で店の画面を見つけて照準を合わせてクリック（店 → Equip）→ ログの
    Equipping で確かめる。OSC・接地・撮影・マウス・ログは偽物"""

    GAIN = (0.81, 0.59)         # 測った感度（依頼者の PC の実測）として使う

    ITEMS = {29: ItemCatalog.Item("Taser", "Survival", True),
             70: ItemCatalog.Item("Coil", "Enkephalin", True),
             81: ItemCatalog.Item("Lantern", "Event", True),
             5: ItemCatalog.Item("Glove", "Others", True)}

    def setUp(self):
        for p in (patch.object(config, "ITEMS", self.ITEMS),):
            p.start()
            self.addCleanup(p.stop)
        SharedState.set_item_fetch(False)
        self.addCleanup(SharedState.set_item_fetch, False)
        SharedState.set_item_begin_mode(False)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        SharedState.set_hands_free(False)
        SharedState.equip_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)
        self.now = [0.0]

    # ── 偽物 ──────────────────────────────────
    def _sleep(self, sec):
        self.now[0] += sec

    def _clock(self):
        return self.now[0]

    class FakeOsc:
        def __init__(self, test):
            self.test = test
            self.sent = []
            self.stopped = 0

        def send(self, address, value):
            self.sent.append((round(self.test.now[0], 2), address, value))

        def stop_all(self, repeat=2):
            self.stopped += 1

    class FakeMouse:
        def __init__(self, world=None):
            self.world = world
            self.moves = []
            self.clicks = 0

        def move_rel(self, dx, dy):
            self.moves.append((dx, dy))
            if self.world is not None:
                self.world["mouse"][0] += dx
                self.world["mouse"][1] += dy

        def click(self):
            self.clicks += 1
            if self.world is not None and self.world.get("on_click"):
                self.world["on_click"]()

    def _fetcher(self, grounded=lambda: True, stopped=lambda: None, osc=None, mouse=None,
                 locate=None, equip_seen=lambda: (0, 0), capture=None, aimed=True, saved_gain=None):
        self.osc = osc or self.FakeOsc(self)
        self.mouse = mouse or self.FakeMouse()
        self.logs = []
        fetcher = ItemFetch.Fetcher(
            osc=self.osc, grounded=grounded,
            capture=capture or (lambda: ("shot", (960.0, 540.0))),
            mouse=self.mouse, equip_seen=equip_seen, stopped=stopped,
            log=self.logs.append, locate_fn=locate or (lambda _img, _pt: None),
            sleep=self._sleep, clock=self._clock, saved_gain=saved_gain)
        if aimed:
            fetcher.aimer = ItemFetch.Aimer(self.GAIN)      # 感度は測った後
        return fetcher

    @staticmethod
    def _two_landings(t):
        """柵の上（0.9 秒）と柵の向こう（1.3 秒）に着地する"""
        if t < 0.6:
            return True
        if t < 0.9:
            return False
        if t < 1.0:
            return True
        if t < 1.295:                   # 0.01 秒刻みの足し算の誤差で 1.30 を越えないよう
            return False
        return True

    # ── 1. 移動 ──────────────────────────────
    def test_the_moves_and_their_timing(self):
        f = self._fetcher(grounded=lambda: self._two_landings(self.now[0]))
        self.assertTrue(f.move_to_shop())
        self.assertEqual(self.osc.sent, [
            (0.0, "/input/MoveLeft", 1),
            (0.3, "/input/Jump", 1), (0.36, "/input/Jump", 0),
            (0.5, "/input/Jump", 1), (0.56, "/input/Jump", 0),
            (1.3, "/input/MoveLeft", 0),                     # 2回目の着地で左を離す
            (1.3, "/input/MoveBackward", 1),
            (1.55, "/input/MoveLeft", 1),                    # 後ろ 0.25 秒の後に左も重ねる
            (1.95, "/input/MoveBackward", 0), (1.95, "/input/MoveLeft", 0),   # 後ろ＋左 0.40 秒
            (1.95, "/input/LookLeft", 1), (2.45, "/input/LookLeft", 0),       # 左へ 0.50 秒
        ])
        self.assertEqual(self.osc.stopped, 1, "最後に全部離す")
        self.assertTrue(any("柵を越えた 1.30秒" in m for m in self.logs), self.logs)

    def test_no_landing_fails_and_releases(self):
        for grounded in (lambda: True, lambda: None, lambda: False):
            self.now[0] = 0.0
            f = self._fetcher(grounded=grounded)
            self.assertFalse(f.move_to_shop())
            self.assertNotIn("/input/MoveBackward", [a for _t, a, _v in self.osc.sent])
            self.assertEqual(self.osc.stopped, 1)
            self.assertLessEqual(self.now[0], 0.56 + 2.5 + 0.02, "上限 2.5 秒")

    def test_one_landing_is_not_enough(self):
        f = self._fetcher(grounded=lambda: self._two_landings(min(self.now[0], 1.1)))
        self.assertFalse(f.move_to_shop())
        self.assertTrue(any("着地 1 回" in m for m in self.logs), self.logs)

    def test_a_stop_while_moving_releases_the_keys(self):
        f = self._fetcher(grounded=lambda: self._two_landings(self.now[0]),
                          stopped=lambda: "round" if self.now[0] >= 1.4 else None)
        with self.assertRaises(ItemFetch.Stopped) as caught:
            f.move_to_shop()
        self.assertEqual(caught.exception.args[0], "round")
        self.assertEqual(self.osc.stopped, 1, "押したままにしない")
        self.assertNotIn("/input/LookLeft", [a for _t, a, _v in self.osc.sent])

    # ── 2. 照準 ──────────────────────────────
    def test_the_aimer_tolerance_cap_and_gain(self):
        aimer = ItemFetch.Aimer(self.GAIN)
        aim = (960.0, 540.0)
        self.assertIsNone(aimer.move_for((966.0, 544.0), aim), "横 ±6・縦 ±4 なら押す")
        self.assertEqual(aimer.move_for((967.0, 540.0), aim), (7 / 0.81, 0.0))
        self.assertEqual(aimer.move_for((960.0, 545.0), aim), (0.0, 5 / 0.59))
        self.assertEqual(aimer.move_for((1960.0, -460.0), aim), (160, -160), "上限 ±160")

    def test_the_gain_is_measured_again(self):
        aimer = ItemFetch.Aimer(self.GAIN)
        aimer.move_for((1041.0, 540.0), (960.0, 540.0))     # 送る 100（横）
        aimer.observe((951.0, 540.0))                        # 90px 動いた → 0.9
        self.assertAlmostEqual(aimer.gain[0], 0.5 * 0.81 + 0.5 * 0.9)
        self.assertEqual(aimer.gain[1], 0.59, "送っていない軸は変えない")

    def test_a_wild_gain_is_thrown_away(self):
        for moved in (200.0, 30.0):                          # 2.0・0.3 は範囲外
            aimer = ItemFetch.Aimer(self.GAIN)
            aimer.move_for((1041.0, 540.0), (960.0, 540.0))
            aimer.observe((1041.0 - moved, 540.0))
            self.assertEqual(aimer.gain[0], 0.81, moved)
        aimer = ItemFetch.Aimer(self.GAIN)
        aimer.move_for((960.0, 600.0), (960.0, 540.0))       # 縦に 101.7
        aimer.observe((960.0, 600.0 - 1.3 * 101.69))          # 1.3 は測った 0.59 の 2 倍（1.18）の外
        self.assertEqual(aimer.gain[1], 0.59)

    def test_the_keep_range_is_half_to_double_of_the_measured_gain(self):
        aimer = ItemFetch.Aimer((2.0, 0.3))                  # 感度は人によって違う
        self.assertEqual(aimer.limits, ((1.0, 4.0), (0.15, 0.6)))
        aimer.move_for((960.0 + 2.0 * 100, 540.0), (960.0, 540.0))   # 送る 100
        aimer.observe((960.0 + 2.0 * 100 - 350.0, 540.0))             # 3.5（4.0 の内）
        self.assertAlmostEqual(aimer.gain[0], 2.75)

    def test_a_small_move_does_not_measure(self):
        aimer = ItemFetch.Aimer(self.GAIN)
        aimer.move_for((966.4 + 0.5, 540.0), (960.0, 540.0))  # 送る 8 未満
        aimer.observe((960.0, 540.0))
        self.assertEqual(aimer.gain[0], 0.81)

    def test_moves_are_split_into_6_pixel_steps(self):
        steps = ItemFetch.split_move(100.4, -13.0)
        self.assertTrue(all(abs(dx) <= 6 and abs(dy) <= 6 for dx, dy in steps), steps)
        self.assertEqual((sum(d for d, _ in steps), sum(d for _, d in steps)), (100, -13))
        self.assertEqual(ItemFetch.split_move(3.0, 0.0), [(3, 0)])

    def _world(self, start, gain=(0.9, 0.55)):
        """視点を回すとボタンが逆へ動く世界（本当の gain は初期値と違う）"""
        world = {"mouse": [0, 0]}

        def locate(_img, _pt):
            return (start[0] - gain[0] * world["mouse"][0], start[1] - gain[1] * world["mouse"][1])
        return world, locate

    def test_the_aim_reaches_the_button_and_clicks(self):
        world, locate = self._world((1300.0, 300.0))
        mouse = self.FakeMouse(world)
        f = self._fetcher(mouse=mouse, locate=locate)
        self.assertTrue(f.aim_click("Survival"))
        x, y = locate(None, None)
        self.assertLessEqual(abs(x - 960), 6)
        self.assertLessEqual(abs(y - 540), 4)
        self.assertEqual(mouse.clicks, 1)
        self.assertTrue(all(abs(dx) <= 6 and abs(dy) <= 6 for dx, dy in mouse.moves))
        self.assertTrue(any("Survival クリック（合わせ" in m for m in self.logs), self.logs)

    def test_no_shop_screen_gives_up_after_6_retakes(self):
        calls = []
        f = self._fetcher(locate=lambda _i, _p: calls.append(1))
        self.assertFalse(f.aim_click("Equip"))
        self.assertEqual(len(calls), 7, "1回＋撮り直し6回")
        self.assertEqual(self.mouse.clicks, 0)

    def test_it_gives_up_after_12_aims(self):
        shots = []
        f = self._fetcher(locate=lambda _i, _p: shots.append(1) or (1500.0, 540.0))   # 動かない
        self.assertFalse(f.aim_click("Equip"))
        self.assertEqual(self.mouse.clicks, 0)
        self.assertEqual(len(shots), 12, "1つのボタンにつき 12 回まで")
        self.assertTrue(any("合わせきれません" in m for m in self.logs))

    def test_the_button_points_in_the_template(self):
        """実機で合った見本の中の座標（仕様書の表）"""
        self.assertEqual(ItemFetch.BUTTONS, {"Enkephalin": (106, 129), "Survival": (266, 95),
                                             "Event": (266, 189), "Equip": (180, 177)})

    def test_the_template_itself_maps_onto_the_button_points(self):
        """見本自身を撮影に見立てると、各ボタンの位置が見本の座標に一致する（本物の SIFT）"""
        import cv2
        import numpy as np
        if not ItemFetch.available():
            self.skipTest("SIFT か見本が使えない")
        data = np.fromfile(str(config.resource_path(ItemFetch.TEMPLATE_FILE)), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        canvas = np.zeros((1080, 1920, 3), np.uint8)
        canvas[245:505, 760:1140] = img
        for name, (x, y) in ItemFetch.BUTTONS.items():
            got = ItemFetch.locate(canvas, (x, y))
            self.assertIsNotNone(got, name)
            self.assertAlmostEqual(got[0], x + 760, delta=1.0, msg=name)
            self.assertAlmostEqual(got[1], y + 245, delta=1.0, msg=name)
        self.assertIsNone(ItemFetch.locate(np.zeros((1080, 1920, 3), np.uint8), (0, 0)))

    # ── 感度（gain）を測る（人によって違う）────────────
    def _calibrating(self, gain, saved_gain=None, found=lambda: True):
        world, locate = self._world((1000.0, 500.0), gain=gain)
        mouse = self.FakeMouse(world)
        f = self._fetcher(mouse=mouse, aimed=False, saved_gain=saved_gain,
                          locate=lambda i, p: locate(i, p) if found() else None)
        return f, mouse

    def test_the_gain_is_measured_on_each_axis(self):
        for gain in ((0.81, 0.59), (2.4, 1.7), (0.2, 0.12)):
            f, mouse = self._calibrating(gain)
            self.assertTrue(f.calibrate("Survival"), gain)
            self.assertAlmostEqual(f.aimer.gain[0], gain[0], places=6)
            self.assertAlmostEqual(f.aimer.gain[1], gain[1], places=6)
            self.assertEqual(mouse.moves, [(6, 0)] * 4 + [(0, 6)] * 4, "24 単位を 6px×4 刻み")

    def test_a_saved_gain_sets_the_size_of_the_measuring_move(self):
        f, mouse = self._calibrating((2.0, 2.0), saved_gain=(2.0, 0.5))
        self.assertTrue(f.calibrate("Survival"))
        self.assertEqual(sum(dx for dx, _ in mouse.moves), 10, "約 20px（20/2.0）")
        self.assertEqual(sum(dy for _, dy in mouse.moves), 40, "約 20px（20/0.5）")
        self.assertAlmostEqual(f.aimer.gain[0], 2.0)

    def test_an_insane_gain_is_measured_once_more_then_fails(self):
        for gain in ((0.0, 0.59), (0.81, 30.0), (-0.8, 0.59)):
            f, mouse = self._calibrating(gain)
            self.assertFalse(f.calibrate("Survival"), gain)
            self.assertIsNone(f.aimer)
            self.assertTrue(any("2/2回目" in m for m in self.logs), self.logs)

    def test_one_bad_measure_then_a_good_one(self):
        world = {"mouse": [0, 0]}
        bad = {"left": 1}

        def locate(_img, _pt):
            if bad["left"] and world["mouse"][0]:
                bad["left"] -= 1
                return (1000.0, 500.0)                       # 動かなかったように見えた
            return (1000.0 - 0.9 * world["mouse"][0], 500.0 - 0.55 * world["mouse"][1])
        f = self._fetcher(mouse=self.FakeMouse(world), aimed=False, locate=locate)
        self.assertTrue(f.calibrate("Survival"))
        self.assertAlmostEqual(f.aimer.gain[0], 0.9)

    def test_no_shop_screen_fails_the_measure(self):
        f, _mouse = self._calibrating((0.81, 0.59), found=lambda: False)
        self.assertFalse(f.calibrate("Survival"))
        self.assertTrue(any("感度を測れません" in m for m in self.logs))

    def test_an_insane_measure_falls_back_to_the_saved_gain(self):
        """2回とも範囲外でも、前にうまくいった値があればそれで続ける（その軸だけ）"""
        f, _mouse = self._calibrating((0.0, 0.55), saved_gain=(0.8, 0.6))
        self.assertTrue(f.calibrate("Survival"))
        self.assertEqual(f.aimer.gain[0], 0.8, "横は保存した値")
        self.assertAlmostEqual(f.aimer.gain[1], 0.55, msg="縦は測った値")
        self.assertTrue(any("保存した値 0.800 で続けます" in m for m in self.logs), self.logs)

    def test_a_lost_screen_does_not_fall_back(self):
        f, _mouse = self._calibrating((0.81, 0.59), saved_gain=(0.8, 0.6), found=lambda: False)
        self.assertFalse(f.calibrate("Survival"), "店の画面が見つからなければ失敗（保存値でも続けない）")

    def test_losing_the_shop_screen_after_the_measuring_move_fails(self):
        found = iter([True] + [False] * 20)                # 動かす前は見えた・後は見えない
        f, _mouse = self._calibrating((0.81, 0.59), found=lambda: next(found))
        self.assertFalse(f.calibrate("Survival"))
        self.assertIsNone(f.aimer)

    def test_a_failed_measure_presses_nothing(self):
        f = self._fetcher(aimed=False, locate=lambda _i, _p: (960.0, 540.0))
        with patch.object(f, "calibrate", return_value=False):
            self.assertFalse(f.buy("Survival", 29))
        self.assertEqual(self.mouse.clicks, 0)

    def test_the_whole_buy_with_another_sensitivity(self):
        """依頼者と違う感度（横 2.2・縦 0.3）でも、測ってから店のボタンへ合わせて押せる"""
        world, locate = self._world((1300.0, 300.0), gain=(2.2, 0.3))
        world["on_click"] = None
        mouse = self.FakeMouse(world)
        f = self._fetcher(mouse=mouse, aimed=False, locate=locate)
        with patch.object(f, "equip", return_value=True):
            self.assertTrue(f.buy("Survival", 29))
        x, y = locate(None, None)
        tol = ItemFetch.screen_tol("Survival", (1.0, 1.0))
        self.assertLessEqual(abs(x - 960), tol[0])
        self.assertLessEqual(abs(y - 540), tol[1])

    def test_the_gain_setting_is_checked(self):
        self.addCleanup(SharedState.set_item_fetch_gain, None)
        SharedState.set_item_fetch_gain([0.9, 0.6, 0.005, "calib"])
        self.assertEqual(SharedState.get_item_fetch_gain(), (0.9, 0.6, 0.005, "calib"),
                         "感度・送った間隔・測った値の印")
        for broken in (None, "x", [1], [0.9, 0.6], [0.9, 0.6, 0.005], [0.9, 0.6, 0.005, "aim"],
                       [0.01, 0.5, 0.005, "calib"], [0.5, 25, 0.005, "calib"],
                       ["a", 1, 0.005, "calib"], [0.9, 0.6, 0, "calib"]):
            SharedState.set_item_fetch_gain(broken)
            self.assertIsNone(SharedState.get_item_fetch_gain(), broken)

    # ── 3. 店とアイテム ──────────────────────────
    def test_the_shop_from_the_category(self):
        S = ItemFetch.shop_for
        self.assertEqual(S(29, self.ITEMS), "Survival")
        self.assertEqual(S(70, self.ITEMS), "Enkephalin")
        self.assertEqual(S(81, self.ITEMS), "Event")
        self.assertIsNone(S(5, self.ITEMS), "Others は取らない")
        self.assertIsNone(S(999, self.ITEMS), "表に無い")
        self.assertIsNone(S(0, self.ITEMS), "番号が分からない")

    # ── 4. Equip とログ ──────────────────────────
    def _equip(self, answers, target=29):
        """Equip を押すたびに answers の次の id の Equipping が来る（None は来ない）"""
        seen = {"seq": 0, "id": 0}
        answers = list(answers)

        def on_click():
            got = answers.pop(0) if answers else None
            if got is not None:
                seen["seq"] += 1
                seen["id"] = got
        world = {"mouse": [0, 0], "on_click": on_click}
        mouse = self.FakeMouse(world)
        f = self._fetcher(mouse=mouse, locate=lambda _i, _p: (960.0, 540.0),
                          equip_seen=lambda: (seen["seq"], seen["id"]))
        return f.equip(target), mouse.clicks

    def test_the_target_id_succeeds_at_once(self):
        self.assertEqual(self._equip([29]), (True, 1))
        self.assertTrue(any("Equip → Equipping 29" in m for m in self.logs), self.logs)

    def test_zero_or_nothing_or_another_id_presses_again(self):
        self.assertEqual(self._equip([0, 29]), (True, 2))
        self.assertEqual(self._equip([None, 29]), (True, 2))
        self.assertEqual(self._equip([70, 29]), (True, 2))

    def test_three_presses_at_most(self):
        self.assertEqual(self._equip([0, None, 0, 29]), (False, 3))

    def test_the_equip_wait_is_0_8_seconds(self):
        self.now[0] = 0.0
        self._equip([None, None, None])
        self.assertLess(self.now[0], 3 * (0.8 + 0.1) + 0.1, "1回 0.8 秒ほど")
        self.assertGreater(self.now[0], 3 * 0.8)

    def test_buy_presses_the_shop_then_equip(self):
        f = self._fetcher(locate=lambda _i, _p: (960.0, 540.0))
        order = []
        with patch.object(f, "calibrate", side_effect=lambda n: order.append(("calibrate", n)) or True), \
             patch.object(f, "equip", side_effect=lambda i: order.append(("equip", i)) or True):
            self.assertTrue(f.buy("Event", 81))
        self.assertEqual(order, [("calibrate", "Event"), ("equip", 81)], "最初のボタンの前に測る")
        self.assertEqual(self.mouse.clicks, 1)
        self.assertTrue(any("Event クリック" in m for m in self.logs))

    # ── 5. 組み込み（ActionExecutor）──────────────────
    def _executor(self, osc_port=9000, auto_begin=True, lost=29, **state):
        cfg = WindowConfig(hwnd=0x55, osc_port=osc_port, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True, item_id=0,
                         round_seq=3, last_lost_item_id=lost,
                         window_idx=2, **state)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append,
                                           auto_begin_active=lambda: auto_begin)
        return ex, st, logs

    def test_the_target_and_when_not_to_fetch(self):
        ex, _st, _ = self._executor()
        self.assertIsNone(ex.item_fetch_target(), "設定 OFF（既定）")
        SharedState.set_item_fetch(True)
        self.assertEqual(ex.item_fetch_target(), ("Survival", 29))
        self.assertIsNone(self._executor(osc_port=0)[0].item_fetch_target(), "OSC でない")
        self.assertIsNone(self._executor(auto_begin=False)[0].item_fetch_target(), "Begin を押さない窓")
        self.assertIsNone(self._executor(lost=5)[0].item_fetch_target(), "Others")
        self.assertIsNone(self._executor(lost=999)[0].item_fetch_target(), "表に無い")
        self.assertIsNone(self._executor(lost=0)[0].item_fetch_target(), "ロストしたものが無い（装備した・インスタンス移動）")
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        with patch.object(RoundDecision, "guidance_plush_id", return_value=29):
            self.assertEqual(self._executor()[0].item_fetch_target(), ("Survival", 29),
                             "完全放置モードでも Guidance Plush は取りに行く")
        with patch.object(RoundDecision, "guidance_plush_id", return_value=70):
            self.assertIsNone(self._executor()[0].item_fetch_target(),
                              "完全放置モードでは Guidance Plush 以外は取りに行かない")

    def _hands_free_round(self, ex, st, outcome="ok"):
        order = []

        def press(*_a, **_kw):
            order.append("press")
            st.begin_done = True
            return True
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ex, "_begin_move"), \
             patch.object(ex, "_start_use_spam", return_value=None), \
             patch.object(ex, "_wait_round_end", return_value=True), \
             patch.object(ex, "_wait_other_windows", return_value=True), \
             patch.object(ex, "_begin_precheck", return_value=True), \
             patch.object(ex, "_press_begin", side_effect=press), \
             patch.object(ex, "_confirm_begin"), \
             patch.object(ex, "_fetch_item",
                          side_effect=lambda seq, shop, item: order.append(("fetch", shop, item)) or outcome), \
             patch.object(ex, "_attend_to_item_loss", side_effect=lambda: order.append("attend")), \
             patch.object(PlaySound, "play_sound", side_effect=lambda _p: order.append("sound")):
            ex.do_after_round()
        return order

    def test_hands_free_fetches_after_the_begin_without_freeze_or_sound(self):
        """完全放置モードでも取りに行く。装備待ち（フリーズ・前面化・音声）は無い"""
        SharedState.set_item_fetch(True)
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        ex, st, _ = self._executor()
        with patch.object(RoundDecision, "guidance_plush_id", return_value=29):
            self.assertEqual(self._hands_free_round(ex, st), ["press", ("fetch", "Survival", 29)])
        self.assertFalse(st.equip_freeze_held)
        ex, st, _ = self._executor()
        with patch.object(RoundDecision, "guidance_plush_id", return_value=70):
            self.assertEqual(self._hands_free_round(ex, st), ["press"], "Guidance Plush 以外")

    def test_hands_free_does_not_fetch_when_holding_or_off(self):
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        SharedState.set_item_fetch(True)
        ex, st, _ = self._executor()
        st.item_id = 1
        self.assertEqual(self._hands_free_round(ex, st), ["press"], "持っている")
        SharedState.set_item_fetch(False)
        ex, st, _ = self._executor()
        self.assertEqual(self._hands_free_round(ex, st), ["press"], "設定 OFF")

    def _after_round(self, ex, st, outcome, accept=True):
        """Begin まで回し、受理の後に何が起きるかを見る"""
        st.waiting_for_equip = True
        order = []

        def press(*_a, **_kw):
            order.append("press")
            if accept:
                st.begin_done = True
            return True

        def fetch(round_seq, shop, item_id):
            order.append(("fetch", shop, item_id))
            if outcome == "ok":
                st.item_id = item_id
                st.waiting_for_equip = False
            return outcome

        def attend():
            order.append("attend")
            st.waiting_for_equip = False

        def sleep(_sec):
            if "press" in order:
                st.waiting_for_equip = False
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ex, "_begin_move"), \
             patch.object(ex, "_start_use_spam", return_value=None), \
             patch.object(ex, "_wait_round_end", return_value=True), \
             patch.object(ex, "_handle_item_lost", return_value=True), \
             patch.object(ex, "_wait_other_windows", return_value=True), \
             patch.object(ex, "_begin_precheck", return_value=True), \
             patch.object(ex, "_press_begin", side_effect=press), \
             patch.object(ex, "_confirm_begin"), \
             patch.object(ex, "_fetch_item", side_effect=fetch), \
             patch.object(ex, "_attend_to_item_loss", side_effect=attend), \
             patch.object(PlaySound, "play_sound", side_effect=lambda _p: order.append("sound")), \
             patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
            ex.do_after_round()
        return order

    def test_after_the_begin_it_fetches_instead_of_waiting(self):
        """Begin が通った後（いつもの案内のタイミング）に音だけ鳴らしてから取りに行く"""
        SharedState.set_item_fetch(True)
        ex, st, _ = self._executor()
        self.assertEqual(self._after_round(ex, st, "ok"), ["press", "sound", ("fetch", "Survival", 29)])

    def test_a_failure_or_timeout_gives_nothing_more(self):
        """取りに行って失敗・時間切れ → 何もしない（前面化・フリーズ・2回目の音なし）"""
        SharedState.set_item_fetch(True)
        for outcome in ("failed", "timeout"):
            ex, st, _ = self._executor()
            self.assertEqual(self._after_round(ex, st, outcome),
                             ["press", "sound", ("fetch", "Survival", 29)], outcome)

    def test_a_round_start_or_stop_gives_no_notice(self):
        SharedState.set_item_fetch(True)
        for outcome in ("round", "stopped"):
            ex, st, logs = self._executor()
            self.assertEqual(self._after_round(ex, st, outcome),
                             ["press", "sound", ("fetch", "Survival", 29)], outcome)
            waiting = any("アイテム装備を待っています" in m for m in logs)
            self.assertEqual(waiting, outcome == "round", f"{outcome}: 停止ならそこで終わる")

    def test_not_accepted_does_not_fetch(self):
        SharedState.set_item_fetch(True)
        ex, st, _ = self._executor()
        self.assertEqual(self._after_round(ex, st, "ok", accept=False), ["press"])

    def test_off_or_others_is_as_before(self):
        ex, st, _ = self._executor()
        self.assertEqual(self._after_round(ex, st, "ok"), ["press", "attend"], "OFF")
        SharedState.set_item_fetch(True)
        for kw in ({"osc_port": 0}, {"lost": 5}):
            ex, st, _ = self._executor(**kw)
            self.assertEqual(self._after_round(ex, st, "ok"), ["press", "attend"], kw)

    def test_the_item_begin_mode_does_not_fetch(self):
        """アイテム取得→Begin モードでは自動取得を動かさない（自動取得 ON でも今どおり装備を待つ）"""
        SharedState.set_item_fetch(True)
        SharedState.set_item_begin_mode(True)
        ex, st, _ = self._executor()
        self.assertIsNone(ex.item_fetch_target())
        st.waiting_for_equip = True
        result = []
        running = [True]
        ex._is_running = lambda: running[0]
        worker = threading.Thread(target=lambda: result.append(ex._handle_item_lost()), daemon=True)
        worker.start()
        worker.join(0.5)
        self.assertEqual(result, [], "今どおり装備を待つ")
        running[0] = False
        worker.join(3.0)

    def test_the_item_begin_mode_still_holds_without_fetch(self):
        SharedState.set_item_begin_mode(True)
        ex, st, _ = self._executor()
        st.waiting_for_equip = True
        result = []
        running = [True]
        ex._is_running = lambda: running[0]
        worker = threading.Thread(target=lambda: result.append(ex._handle_item_lost()), daemon=True)
        worker.start()
        worker.join(0.5)
        self.assertEqual(result, [], "今どおり装備を待つ")
        running[0] = False
        worker.join(3.0)

    def test_the_speed_strafe_does_not_move_while_waiting_for_the_item(self):
        SharedState.set_speed_detect(True)
        self.addCleanup(SharedState.set_speed_detect, config.SPEED_DETECT_ENABLED)
        ex, st, logs = self._executor()
        st.waiting_for_equip = True
        with patch.object(ex, "move") as move:
            ex.do_speed_strafe()
        move.assert_not_called()

    def _fetch(self, ex, st, move=None, buy=True, front=True):
        """_fetch_item を回す。move(fetcher) は移動の代わり"""
        calls = []

        def fake_move(fetcher):
            calls.append("move")
            return move(fetcher) if move else True

        def fake_buy(fetcher, shop, item_id):
            calls.append(("buy", shop, item_id))
            return buy

        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, side_effect=fake_move), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=fake_buy), \
             patch.object(ex, "_borrow_front", side_effect=lambda: calls.append("front") or (front, "loan")), \
             patch.object(WindowOperator, "return_front", side_effect=lambda loan: calls.append(("back", loan))), \
             patch.object(ex._osc, "stop_all", side_effect=lambda repeat=2: calls.append("release")), \
             patch.object(ActionExecutor.time, "sleep"):
            outcome = ex._fetch_item(st.round_seq, "Survival", 29)
        return outcome, calls

    def test_a_good_gain_is_kept_and_passed_next_time(self):
        self.addCleanup(SharedState.set_item_fetch_gain, None)
        SharedState.set_item_fetch_gain(None)
        ex, st, _ = self._executor()
        seen = []

        def buy(fetcher, shop, item_id):
            seen.append(fetcher.saved_gain)
            fetcher.measured_gain = (1.5, 0.7)                  # calibrate で測った
            fetcher.aimer = ItemFetch.Aimer((1.6, 0.9))         # 合わせの途中で直した（保存しない）
            return True
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, return_value=True), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(ex, "_borrow_front", return_value=(True, None)), \
             patch.object(ex._osc, "stop_all"), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(ActionExecutor.time, "sleep"):
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29), "ok")
            self.assertEqual(SharedState.get_item_fetch_gain(), (1.5, 0.7, ItemFetch.STEP_SEC, "calib"))
            ex._fetch_item(st.round_seq, "Survival", 29)
        self.assertEqual(seen, [None, (1.5, 0.7)], "次回はうまくいった値を渡す")

    def test_a_failed_buy_does_not_keep_the_gain(self):
        self.addCleanup(SharedState.set_item_fetch_gain, None)
        SharedState.set_item_fetch_gain((0.8, 0.6, ItemFetch.STEP_SEC, "calib"))
        ex, st, _ = self._executor()

        def buy(fetcher, shop, item_id):
            fetcher.measured_gain = (5.0, 5.0)
            fetcher.aimer = ItemFetch.Aimer((5.0, 5.0))
            return False
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, return_value=True), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(ex, "_borrow_front", return_value=(True, None)), \
             patch.object(ex._osc, "stop_all"), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(ActionExecutor.time, "sleep"):
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29), "failed")
        self.assertEqual(SharedState.get_item_fetch_gain(), (0.8, 0.6, ItemFetch.STEP_SEC, "calib"))

    def test_fetching_moves_then_fronts_and_gives_the_front_back(self):
        ex, st, logs = self._executor()
        outcome, calls = self._fetch(ex, st)
        self.assertEqual(outcome, "ok")
        self.assertEqual(calls, ["move", "front", ("buy", "Survival", 29), ("back", "loan"), "release"])
        self.assertIn("アイテム取得: 装備できました", " ".join(logs))

    def test_a_failed_buy_is_failed(self):
        ex, st, _ = self._executor()
        self.assertEqual(self._fetch(ex, st, buy=False)[0], "failed")
        ex, st, _ = self._executor()
        outcome, calls = self._fetch(ex, st, front=False)
        self.assertEqual(outcome, "failed")
        self.assertNotIn(("buy", "Survival", 29), calls)

    def test_a_round_start_stop_or_timeout_ends_it_and_releases(self):
        cases = (("round", lambda ex, st: setattr(st, "in_round", True)),
                 ("stopped", lambda ex, st: setattr(ex, "_is_running", lambda: False)),
                 ("timeout", None))
        for expected, happen in cases:
            ex, st, logs = self._executor()

            def move(fetcher, ex=ex, st=st, happen=happen):
                if happen:
                    happen(ex, st)
                fetcher._check()
                return True
            if happen is None:
                with patch.object(config, "ITEM_FETCH_LIMIT_SEC", -1.0):
                    outcome, calls = self._fetch(ex, st, move=move)
            else:
                outcome, calls = self._fetch(ex, st, move=move)
            self.assertEqual(outcome, expected)
            self.assertNotIn("front", calls, "前面化しない")
            self.assertEqual(calls[-1], "release", "押しているものを離す")
            self.assertTrue(any("→ やめます" in m for m in logs), logs)

    def test_the_limit_is_10_seconds(self):
        self.assertEqual(config.ITEM_FETCH_LIMIT_SEC, 10.0)

    def test_equipping_by_hand_while_fetching_is_fine(self):
        ex, st, _ = self._executor()

        def move(fetcher):
            st.item_id = 29                     # 手で装備した（ログの流れが解除した）
            fetcher._check()
            return True
        outcome, calls = self._fetch(ex, st, move=move)
        self.assertEqual(outcome, "ok")
        self.assertNotIn("front", calls, "もう押しに行かない")

    # ── 6. ログ（LogMonitor）──────────────────────
    def _monitor(self, **cfg):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10, **cfg), {}, lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def test_the_lost_item_is_remembered_with_its_round(self):
        monitor = self._monitor()
        monitor.st.held_item_id = 29
        monitor.st.round_seq = 4
        monitor._lose_held_item("リスポーン")
        self.assertEqual(monitor.st.last_lost_item_id, 29)
        monitor.st.held_item_id = 70
        monitor._lose_held_item(LogMonitor.HELD_LOST_INSTANCE)
        self.assertEqual(monitor.st.last_lost_item_id, 29, "インスタンス移動は取りに行かない")

    def test_each_equipping_is_counted(self):
        monitor = self._monitor()
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("2026.10.03 05:01:26 Debug      -  Equipping 0. Was using 70")
            monitor._process("2026.10.03 05:01:27 Debug      -  Equipping 29.")
        self.assertEqual((monitor.st.equip_seen_seq, monitor.st.equip_seen_id), (2, 29))

    def test_the_item_begin_mode_round_over_is_as_before(self):
        """アイテム取得→Begin モードでは自動取得 ON でも、RoundOver は昔どおり _attend_to_item_loss"""
        SharedState.set_item_fetch(True)
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor(auto_begin=True, osc_port=9000)
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        st.in_round = True
        st.item_id = 29
        st.held_item_id = 29
        monitor._mark_item_lost("リスポーン: アイテムロスト")
        monitor._lose_held_item("リスポーン")
        monitor._start_daemon = lambda *a: None
        with patch.object(monitor._action, "_attend_to_item_loss") as attend:
            monitor._process("2026.10.03 05:00:00 Debug      -  RoundOver")
        attend.assert_called_once_with()
        self.assertTrue(st.waiting_for_equip)
        self.assertTrue(any("RoundOver 【⚠ アイテムロスト → 全窓フリーズ開始】" in m for m in monitor.logs),
                        monitor.logs)
        self.assertFalse(any("自動で取りに行きます" in m for m in monitor.logs))

    # ── 7. 設定 ──────────────────────────────────
    def test_the_setting_is_off_by_default_saved_and_restored(self):
        self.assertFalse(SharedState.get_item_fetch())
        loader = TestStartKeySettings("test_it_is_saved")
        loader._load({"item_fetch": True})
        self.assertTrue(SharedState.get_item_fetch())
        loader._load({})
        self.assertFalse(SharedState.get_item_fetch(), "無ければ OFF")
        loader._load({"item_fetch": "yes"})
        self.assertFalse(SharedState.get_item_fetch(), "壊れた値は OFF")
        self.addCleanup(SharedState.set_item_fetch_gain, None)
        loader._load({"item_fetch_gain": [0.9, 0.6, 0.005, "calib"]})
        self.assertEqual(SharedState.get_item_fetch_gain(), (0.9, 0.6, 0.005, "calib"))
        loader._load({"item_fetch_gain": [0.9, 0.6]})
        self.assertIsNone(SharedState.get_item_fetch_gain(), "前の形（間隔なし）は読み捨てる")
        loader._load({"item_fetch_gain": [0.9, 0.6, 0.005]})
        self.assertIsNone(SharedState.get_item_fetch_gain(), "前の形（印なし）は読み捨てる")
        loader._load({"item_fetch_gain": "broken"})
        self.assertIsNone(SharedState.get_item_fetch_gain())
        SharedState.set_item_fetch_gain((1.2, 0.4, 0.005, "calib"))
        SharedState.set_item_fetch(True)
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_freeze_8pages", "v_freeze_punish",
                     "v_emergency_key", "v_start_key", "v_big_key", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, _CancelKeyVar(""))
        app.v_freeze_rounds = {}
        app._window_volume_settings = lambda: {}
        app._fog_early_read_setting = lambda: {}
        app._launch_options_setting = lambda: {}
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(app)
        self.assertIs(saved["item_fetch"], True)
        self.assertEqual(saved["item_fetch_gain"], [1.2, 0.4, 0.005, "calib"])

    def test_the_toggle(self):
        app = type("FakeApp", (), {})()
        app.btn_item_fetch = MagicMock()
        app.logs = []
        app._log = app.logs.append
        app._schedule_settings_save = MagicMock()
        app._refresh_item_fetch_button = lambda: mainGUI.App._refresh_item_fetch_button(app)
        mainGUI.App._toggle_item_fetch(app)
        self.assertTrue(SharedState.get_item_fetch())
        self.assertEqual(app.btn_item_fetch.config.call_args.kwargs["text"], "アイテム自動取得: ON")
        app._schedule_settings_save.assert_called_once_with()
        mainGUI.App._toggle_item_fetch(app)
        self.assertEqual(app.btn_item_fetch.config.call_args.kwargs["text"], "アイテム自動取得: OFF")

    def test_the_template_is_built_into_the_exe(self):
        build = _load_build_script()
        self.assertIn("--include-data-dir=shop_templates=shop_templates", build.BASE_ARGS)
        self.assertTrue((Path(config.resource_path(ItemFetch.TEMPLATE_FILE))).is_file())




class TestItemFetchViewRestore(unittest.TestCase):
    """自動取得の後、送ったマウスの相対移動の縦の合計（測り・照準合わせ）を逆向きに同じ刻み
    （6px・STEP_SEC）で送って視点を戻す（成功・失敗・時間切れ・ラウンド開始・停止のどれでも）。前面を返す前・
    排他の中。横と、左へ回した向き（LookRight）は戻さない（マップが変わると向きはそろう）"""

    def setUp(self):
        self.now = [0.0]
        SharedState.set_item_fetch_gain(None)
        self.addCleanup(SharedState.set_item_fetch_gain, None)

    def _sleep(self, sec):
        self.now[0] += sec

    # ── ItemFetch ──
    def _fetcher(self, locate, mouse, osc=None, grounded=lambda: True):
        self.logs = []
        return ItemFetch.Fetcher(
            osc=osc or MagicMock(), grounded=grounded, capture=lambda: ("shot", (960.0, 540.0)),
            mouse=mouse, equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            locate_fn=locate, sleep=self._sleep, clock=lambda: self.now[0])

    def test_the_measure_and_the_aim_are_both_sent_back(self):
        world = {"mouse": [0, 0]}

        def locate(_img, _pt):
            return (1300.0 - 0.9 * world["mouse"][0], 300.0 - 0.55 * world["mouse"][1])
        mouse = TestItemFetch.FakeMouse(world)
        f = self._fetcher(locate, mouse)
        self.assertTrue(f.calibrate("Survival"))
        self.assertTrue(f.aim_click("Survival"))
        sent = (sum(dx for dx, _ in mouse.moves), sum(dy for _, dy in mouse.moves))
        self.assertNotEqual(sent, (0, 0))
        self.assertEqual(tuple(f.mouse.total), sent, "測りの動きも数える")
        self.assertNotEqual(sent[1], 0)
        before = len(mouse.moves)
        f.restore_view()
        back = mouse.moves[before:]
        self.assertEqual((sum(dx for dx, _ in back), sum(dy for _, dy in back)), (0, -sent[1]), "縦だけ")
        self.assertTrue(all(dx == 0 and abs(dy) <= 6 for dx, dy in back), "6px 刻み・横は送らない")
        self.assertEqual(world["mouse"], [sent[0], 0], "縦は元の高さ・横はそのまま")
        self.assertIn(f"アイテム取得: 視点を戻した（縦 {-sent[1]}）", self.logs)

    def test_the_steps_are_5ms_apart(self):
        self.assertEqual(ItemFetch.STEP_SEC, 0.005)
        mouse = TestItemFetch.FakeMouse()
        f = self._fetcher(lambda *_: None, mouse)
        f.mouse.move_rel(24, -24)
        self.now[0] = 0.0
        f.restore_view()
        self.assertEqual(mouse.moves[1:], [(0, 6)] * 4)
        self.assertAlmostEqual(self.now[0], 4 * 0.005)

    def test_only_horizontal_sends_nothing_back(self):
        mouse = TestItemFetch.FakeMouse()
        f = self._fetcher(lambda *_: None, mouse)
        f.mouse.move_rel(30, 0)
        f.restore_view()
        self.assertEqual(mouse.moves, [(30, 0)])
        self.assertEqual(self.logs, [])

    def test_nothing_sent_nothing_back(self):
        mouse = TestItemFetch.FakeMouse()
        f = self._fetcher(lambda *_: None, mouse)
        f.restore_view()
        self.assertEqual(mouse.moves, [])
        self.assertEqual(self.logs, [])

    def _turning(self, stop_at=None):
        osc = TestItemFetch.FakeOsc(self)
        f = self._fetcher(lambda *_: None, TestItemFetch.FakeMouse(), osc=osc,
                          grounded=lambda: TestItemFetch._two_landings(self.now[0]))
        if stop_at is not None:
            f.stopped = lambda: "round" if self.now[0] >= stop_at else None
        try:
            f.move_to_shop()
        except ItemFetch.Stopped:
            pass
        return f, osc

    def test_the_turn_is_not_turned_back(self):
        """LookLeft の 90 度は戻さない（LookRight を送らない）"""
        f, osc = self._turning()
        self.assertNotIn("/input/LookRight", [a for _t, a, _v in osc.sent])
        self.assertFalse(hasattr(f, "restore_turn"))
        self.assertFalse(hasattr(f, "turned_sec"))

    def test_a_stop_during_the_turn_releases_it(self):
        f, osc = self._turning(stop_at=2.2)             # 回し始め 1.95 → 2.45 の途中で止める合図
        self.assertNotIn("/input/LookRight", [a for _t, a, _v in osc.sent])
        self.assertEqual(osc.stopped, 1, "離す（stop_all）")

    # ── 組み込み（ActionExecutor）──
    def _run(self, buy_does, foreground=True, move_raises=None):
        """buy_does(fetcher) を店で行う。送ったマウス・戻したマウス・前面を返した順を見る"""
        SharedState.set_item_fetch(True)
        self.addCleanup(SharedState.set_item_fetch, False)
        cfg = WindowConfig(hwnd=0x55, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=3, window_idx=2,
                         item_id=0, waiting_for_equip=True)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                           auto_begin_active=lambda: True)
        order = []

        def move(fetcher):
            if move_raises:
                raise ItemFetch.Stopped(move_raises)
            return True

        def buy(fetcher, shop, item_id):
            return buy_does(fetcher)

        osc_sent = []
        with patch.object(ItemFetch.Fetcher, "move_to_shop", autospec=True, side_effect=move), \
             patch.object(ItemFetch.Fetcher, "buy", autospec=True, side_effect=buy), \
             patch.object(ActionExecutor._FetchMouse, "move_rel",
                          side_effect=lambda dx, dy: order.append(("mouse", dx, dy))), \
             patch.object(ex, "_borrow_front", return_value=(True, "loan")), \
             patch.object(WindowOperator, "foreground_hwnd", return_value=0x55 if foreground else 0x99), \
             patch.object(WindowOperator, "return_front", side_effect=lambda loan: order.append("give back")), \
             patch.object(ex._osc, "send", side_effect=lambda a, v: osc_sent.append((a, v))), \
             patch.object(ex._osc, "stop_all"), \
             patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ItemFetch.time, "sleep"):
            outcome = ex._fetch_item(st.round_seq, "Survival", 29)
        return outcome, order, osc_sent

    @staticmethod
    def _moves(fetcher, raise_as=None, result=True):
        for _ in range(5):
            fetcher.mouse.move_rel(6, -4)          # 測り・照準合わせで送った
        fetcher.mouse.move_rel(3, 0)
        if raise_as:
            raise ItemFetch.Stopped(raise_as)
        return result

    def test_every_ending_sends_the_view_back_before_giving_the_front_back(self):
        cases = (("ok", lambda f: self._moves(f)),
                 ("failed", lambda f: self._moves(f, result=False)),
                 ("timeout", lambda f: self._moves(f, raise_as="timeout")),
                 ("round", lambda f: self._moves(f, raise_as="round")),
                 ("stopped", lambda f: self._moves(f, raise_as="stopped")))
        for expected, does in cases:
            outcome, order, osc_sent = self._run(does)
            self.assertEqual(outcome, expected)
            give_back = order.index("give back")
            back = [m for m in order[6:give_back] if m != "give back"]
            self.assertEqual(sum(m[1] for m in back), 0, f"{expected}: 横は戻さない")
            self.assertEqual(sum(m[2] for m in back), 20, expected)
            self.assertTrue(all(m[1] == 0 and abs(m[2]) <= 6 for m in back), expected)
            self.assertEqual(order[-1], "give back", "前面を返すのは戻した後")
            self.assertNotIn("/input/LookRight", [a for a, _v in osc_sent], expected)

    def test_an_error_while_buying_still_sends_the_view_back(self):
        def boom(f):
            f.mouse.move_rel(6, -6)
            raise RuntimeError("x")
        outcome, order, _ = self._run(boom)
        self.assertEqual(outcome, "failed")
        self.assertEqual(order, [("mouse", 6, -6), ("mouse", 0, 6), "give back"])

    def test_a_failing_restore_still_gives_the_front_back(self):
        with patch.object(ItemFetch.Fetcher, "restore_view", side_effect=RuntimeError("x")), \
             patch.object(ActionExecutor.ActionExecutor, "_restore_view_soon"):   # 後で戻すのは別に見る
            outcome, order, _ = self._run(lambda f: self._moves(f))
        self.assertEqual(outcome, "ok")
        self.assertEqual(order[-1], "give back")

    def test_not_in_front_does_not_send_the_view_back(self):
        """窓が前に無い（閉じた・奪われた）ときは、ほかの窓へ送らない（最初の1通から送らない）"""
        outcome, order, _ = self._run(lambda f: self._moves(f, raise_as="stopped"), foreground=False)
        self.assertEqual(outcome, "front_lost")
        self.assertEqual([m for m in order if m != "give back"], [], "マウスは1通も送らない")

    def test_a_stop_while_moving_sends_nothing_back(self):
        outcome, order, osc_sent = self._run(lambda f: True, move_raises="round")
        self.assertEqual(outcome, "round")
        self.assertEqual(osc_sent, [], "LookRight も送らない")
        self.assertEqual(order, [], "店へ着く前なのでマウスは送っていない・前面化もしていない")

    def test_the_restore_is_logged(self):
        with patch.object(DebugLog, "write") as write:
            self._run(lambda f: self._moves(f))
        lines = [c.args[0] for c in write.call_args_list]
        self.assertIn("[操作] [窓2] アイテム取得: 視点を戻した（縦 20）", lines)
        self.assertFalse(any("向きを戻した" in l for l in lines))




class TestKeepFetchingTheLostItem(unittest.TestCase):
    """取りに行くのは「最後にロストしたアイテム」。装備したとき・インスタンスが変わったときまで覚え、
    その間の「アイテム未回収」のラウンドでも Begin の受理の後に取りに行く"""

    ITEMS = {29: ItemCatalog.Item("Taser", "Survival", True),
             70: ItemCatalog.Item("Coil", "Enkephalin", True)}
    P = "2026.10.03 16:39:00 Debug      -  "

    def setUp(self):
        p = patch.object(config, "ITEMS", self.ITEMS)
        p.start()
        self.addCleanup(p.stop)
        SharedState.set_item_fetch(True)
        self.addCleanup(SharedState.set_item_fetch, False)
        SharedState.set_hands_free(False)

    def _lost_last_round(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x22, osc_port=9000, auto_begin=True), {},
                                        lambda _m: None, window_idx=2)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._running = True
        monitor.st.round_seq = 3
        monitor.st.held_item_id = 29
        monitor._lose_held_item("リスポーン")     # ラウンド3でロストして、取れなかった
        monitor.st.round_seq = 4                 # 次の「アイテム未回収」のラウンド
        monitor.st.item_id = 0
        return monitor

    def _line(self, monitor, body):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.P + body)

    def test_the_next_unrecovered_round_still_fetches(self):
        monitor = self._lost_last_round()
        self.assertEqual(monitor._action.item_fetch_target(), ("Survival", 29))

    def test_the_next_round_fetches_after_the_begin(self):
        monitor = self._lost_last_round()
        ex, st = monitor._action, monitor.st
        st.round_end_seen = True
        st.waiting_for_equip = True
        fetched = []

        def press(*_a, **_kw):
            st.begin_done = True
            return True

        def fetch(round_seq, shop, item_id):
            fetched.append((shop, item_id))
            st.waiting_for_equip = False
            return "ok"
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ex, "_begin_move"), patch.object(ex, "_start_use_spam", return_value=None), \
             patch.object(ex, "_wait_round_end", return_value=True), \
             patch.object(ex, "_handle_item_lost", return_value=True), \
             patch.object(ex, "_wait_other_windows", return_value=True), \
             patch.object(ex, "_begin_precheck", return_value=True), \
             patch.object(ex, "_press_begin", side_effect=press), patch.object(ex, "_confirm_begin"), \
             patch.object(ex, "_fetch_item", side_effect=fetch), \
             patch.object(ex, "_attend_to_item_loss") as attend, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_after_round()
        self.assertEqual(fetched, [("Survival", 29)])
        attend.assert_not_called()

    def test_equipping_ends_it(self):
        for line in ("Equipping 70. Was using 0", "Equipping 29."):
            monitor = self._lost_last_round()
            self._line(monitor, line)
            self.assertIsNone(monitor._action.item_fetch_target(), line)

    def test_equipping_nothing_does_not_end_it(self):
        monitor = self._lost_last_round()
        self._line(monitor, "Equipping 0. Was using 70")
        self.assertEqual(monitor._action.item_fetch_target(), ("Survival", 29))

    def test_a_new_instance_ends_it(self):
        monitor = self._lost_last_round()
        self._line(monitor, "[Behaviour] Joining wrld_b:2~private(usr_me)~region(jp)")
        self.assertIsNone(monitor._action.item_fetch_target())

    def test_hands_free_off_and_others_are_as_before(self):
        monitor = self._lost_last_round()
        SharedState.set_item_fetch(False)
        self.assertIsNone(monitor._action.item_fetch_target(), "設定 OFF")
        SharedState.set_item_fetch(True)
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        self.assertIsNone(monitor._action.item_fetch_target(),
                          "完全放置モードでは Guidance Plush（ここでは表に無い）以外は取りに行かない")
        with patch.object(RoundDecision, "guidance_plush_id", return_value=29):
            self.assertEqual(monitor._action.item_fetch_target(), ("Survival", 29))
        SharedState.set_hands_free(False)
        monitor.st.last_lost_item_id = 999
        self.assertIsNone(monitor._action.item_fetch_target(), "表に無い")




class TestSuicideCancel(unittest.TestCase):
    """自爆キャンセルのキー（既定 ^）。押すと全部の窓の自爆を止め（長押し中はその場で離す・
    やり直さない）、そのラウンドはもう自爆しない（後のきっかけでも）。次のラウンドからは今どおり。
    ロビーで押しても持ち越さない"""

    def setUp(self):
        SharedState.set_suicide_key("^")
        for name, value in (("SUICIDE_CONFIRM_SEC", 0.05), ("SUICIDE_RETRY_MAX", 3)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _executor(self, hwnd=123, **state):
        state.setdefault("in_round", True)
        state.setdefault("round_seq", 7)
        st = WindowState(**state)
        logs = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=hwnd, do_skip=True), st,
                                           lambda: True, logs.append)
        return ex, st, logs

    def _skip(self, ex, during=None):
        """長押しの回数を返す。during(n, stop) は n 回目の長押しの最中に呼ぶ（死なない）"""
        sent = []

        def hold(hwnd, key, sec, stop=None):
            sent.append(key)
            if during:
                during(len(sent), stop)
            return True
        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            ex.do_skip()
        return len(sent)

    # ── 止め方 ──────────────────────────────
    def test_a_cancel_during_the_hold_stops_it_and_does_not_retry(self):
        ex, st, logs = self._executor()
        seen = []

        def during(n, stop):
            seen.append(stop())                 # 押す前は止めない
            self.assertEqual(ex.cancel_suicide(), "stopped")
            seen.append(stop())                 # 押した後は止める（その場で離す）
        with patch.object(ex, "_died_after_skip", return_value=False) as wait:
            self.assertEqual(self._skip(ex, during), 1, "やり直さない")
        wait.assert_not_called()                # 死亡を待たずに終わる
        self.assertEqual(seen, [False, True])
        self.assertTrue(any("自爆キャンセル → このラウンドは自爆しません" in m for m in logs), logs)
        self.assertFalse(any("試しましたが" in m for m in logs))

    def test_a_cancel_while_waiting_to_retry_does_not_retry(self):
        ex, st, _logs = self._executor()

        def died(round_seq):
            ex.cancel_suicide()                 # 長押しの後、死亡を待っている間に押した
            return False
        with patch.object(ex, "_died_after_skip", side_effect=died):
            self.assertEqual(self._skip(ex), 1)

    def test_the_hold_is_released_at_once(self):
        """長押しの途中で止める合図が来たら、3秒待たずにキーを離す"""
        flag = {"stop": False}
        posted = []
        user32 = MagicMock()
        user32.IsIconic.return_value = False
        user32.GetKeyboardState.return_value = 0
        user32.PostMessageW.side_effect = lambda h, msg, vk, lp: posted.append(msg)
        with patch.object(WindowOperator, "_background_key", return_value=(1, 0xDE, 0, 0)), \
             patch.object(WindowOperator, "user32", user32), \
             patch.object(WindowOperator, "kernel32", MagicMock()):
            threading.Timer(0.1, lambda: flag.update(stop=True)).start()
            started = time.monotonic()
            ok = WindowOperator.hold_key_background(0x10, "^", 3.0, stop=lambda: flag["stop"])
            took = time.monotonic() - started
        self.assertTrue(ok)
        self.assertLess(took, 1.0, "3秒待たない")
        self.assertEqual(posted, [WindowOperator.WM_KEYDOWN, WindowOperator.WM_KEYUP], "離した")

    def test_without_a_stop_it_holds_the_whole_time(self):
        with patch.object(WindowOperator.time, "sleep") as sleep:
            WindowOperator._wait_holding(3.0, None)
        sleep.assert_called_once_with(3.0)

    # ── そのラウンドはもう自爆しない・次のラウンドは今どおり ──────────
    def test_a_later_trigger_in_the_same_round_does_not_suicide(self):
        ex, st, logs = self._executor()
        self.assertEqual(ex.cancel_suicide(), "marked")
        self.assertEqual(self._skip(ex), 0, "8 Pages の公開後などの後のきっかけでも自爆しない")
        self.assertTrue(any("自爆キャンセル済み → このラウンドは自爆しません" in m for m in logs), logs)

    def test_the_next_round_suicides_as_before(self):
        ex, st, _logs = self._executor()
        ex.cancel_suicide()
        st.round_seq += 1
        self.assertEqual(self._skip(ex), 3, "次のラウンドは今どおり（死ななければ3回）")

    def test_a_press_in_the_lobby_is_not_carried_over(self):
        ex, st, _logs = self._executor(in_round=False)
        self.assertIsNone(ex.cancel_suicide())
        st.round_seq += 1                       # 次のラウンドが始まる
        st.in_round = True
        self.assertEqual(self._skip(ex), 3)

    # ── 全部の窓 ──────────────────────────────
    def _app(self, monitors, running=True):
        app = type("FakeApp", (), {})()
        app._running = running
        app.monitors = monitors
        app.logs = []
        app._log = app.logs.append
        return app

    def _monitor(self, ex):
        monitor = type("FakeMonitor", (), {})()
        monitor._action = ex
        monitor.cancel_suicide = lambda: LogMonitor.LogMonitor.cancel_suicide(monitor)
        return monitor

    def test_every_window_is_stopped(self):
        suiciding, st1, _ = self._executor(hwnd=1, suicide_seq=7)
        idle, st2, _ = self._executor(hwnd=2)
        lobby, st3, _ = self._executor(hwnd=3, in_round=False)
        app = self._app([self._monitor(e) for e in (suiciding, idle, lobby)])
        mainGUI.App._on_suicide_cancel_key(app, "^")
        self.assertEqual(st1.suicide_cancelled_round, 7)
        self.assertEqual(st2.suicide_cancelled_round, 7, "自爆していない窓もこのラウンドは自爆しない")
        self.assertEqual(st3.suicide_cancelled_round, -1, "ロビーの窓は何もしない")
        self.assertEqual(app.logs, ["[自爆キャンセル] ^キーが押されました → 1窓の自爆を止めました"])

    def test_no_window_suiciding_is_said(self):
        idle, _st, _ = self._executor()
        app = self._app([self._monitor(idle)])
        mainGUI.App._on_suicide_cancel_key(app, "^")
        self.assertEqual(app.logs, ["[自爆キャンセル] ^キーが押されました → "
                                    "自爆中の窓はありません（このラウンドは自爆しません）"])

    def test_only_the_lobby_does_nothing(self):
        lobby, st, _ = self._executor(in_round=False)
        app = self._app([self._monitor(lobby)])
        mainGUI.App._on_suicide_cancel_key(app, "^")
        self.assertEqual(st.suicide_cancelled_round, -1)
        self.assertEqual(app.logs, ["[自爆キャンセル] ^キーが押されました → ラウンド中の窓はありません（何もしません）"])

    def test_nothing_when_not_running(self):
        ex, st, _ = self._executor()
        app = self._app([self._monitor(ex)], running=False)
        mainGUI.App._on_suicide_cancel_key(app, "^")
        self.assertEqual(st.suicide_cancelled_round, -1)
        self.assertEqual(app.logs, [])

    # ── キーの受け取り ──────────────────────────
    def _hooked_app(self, key="^"):
        app = _with_cancel_key(type("FakeApp", (), {})(), key)
        app._unhook_suicide_cancel_key = lambda: mainGUI.App._unhook_suicide_cancel_key(app)
        app._suicide_cancel_key_down = lambda k: mainGUI.App._suicide_cancel_key_down(app, k)
        app._capturing_key = False
        app._suicide_cancel_down = False
        app.after_calls = []
        app.after = lambda ms, fn, *args: app.after_calls.append(args)
        app._on_suicide_cancel_key = None
        app.logs = []
        app._log = app.logs.append
        return app

    def _event(self, kind):
        return type("Event", (), {"event_type": kind})()

    def test_the_key_is_hooked_and_a_press_counts_once(self):
        app = self._hooked_app()
        hooks = []
        with patch.object(mainGUI.keyboard, "hook_key",
                          side_effect=lambda key, cb, suppress: hooks.append((key, cb, suppress)) or "h"):
            mainGUI.App._hook_suicide_cancel_key(app)
        self.assertEqual([(k, s) for k, _cb, s in hooks], [("^", False)], "suppress しない")
        on_key = hooks[0][1]
        for kind in ("down", "down", "down", "up", "down"):   # 押しっぱなしの繰り返しは1回
            on_key(self._event(kind))
        self.assertEqual(app.after_calls, [("^",), ("^",)])

    def test_no_reaction_while_setting_a_key(self):
        app = self._hooked_app()
        app._capturing_key = True
        mainGUI.App._suicide_cancel_key_down(app, "^")
        self.assertEqual(app.after_calls, [])

    def test_changing_the_key_hooks_the_new_one(self):
        app = self._hooked_app()
        app._suicide_cancel_hook = "old"
        app._refresh_suicide_cancel_key_label = lambda: None
        app._hook_suicide_cancel_key = lambda: mainGUI.App._hook_suicide_cancel_key(app)
        app.v_emergency_key = _CancelKeyVar("p")
        app.v_start_key = _CancelKeyVar("")
        app.v_big_key = _CancelKeyVar("")
        with patch.object(mainGUI.keyboard, "hook_key", return_value="new") as hook, \
             patch.object(mainGUI.keyboard, "unhook") as unhook:
            mainGUI.App._finish_capture_cancel_key(app, "f8")
        unhook.assert_called_once_with("old")
        self.assertEqual(hook.call_args.args[0], "f8")
        self.assertEqual(app._suicide_cancel_hook, "new")
        self.assertEqual(app.v_suicide_cancel_key.get(), "f8")

    def test_the_tools_own_background_key_does_not_react(self):
        """ツールが自分で送る背面の ^（PostMessage）は低レベルのフックに来ない。
        同じフックに入力の通知（実際のキーと同じ通り道）を入れると反応する（対照）"""
        # このテストの中では keyboard を MagicMock にしてある。本物のフックは別のプロセスで
        # 掛ける（掛けたまま終わると、Python の終了処理でプロセスがときどき落ちるため。
        # 子は結果を出してから os._exit で終わり、終了コードではなく出力で判定する）
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-c", self._REAL_HOOK_CHILD, tmp],
                               cwd=os.path.dirname(os.path.abspath(__file__)),
                               capture_output=True, text=True, timeout=60,
                               stdin=subprocess.DEVNULL)
        lines = [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
        if not lines and "NO_KEYBOARD" in r.stdout:
            self.skipTest("keyboard が無い")
        self.assertTrue(lines, (r.returncode, r.stdout, r.stderr[-2000:]))
        result = json.loads(lines[-1][len("RESULT "):])
        self.assertTrue(result["sent"], "背面で送れた")
        self.assertEqual(result["background"], [], "背面の ^ では反応しない")
        self.assertEqual(result["control"], ["down"], "対照: 入力の通知の通り道に入れると届く")

    _REAL_HOOK_CHILD = r"""
import json, os, sys, time
from pathlib import Path
import config
tmp = Path(sys.argv[1])
config.DEBUG_LOG_PATH = tmp / "debug.log"       # 本物の %LOCALAPPDATA% に書かない
config.SETTINGS_PATH = tmp / "settings.json"
try:
    import keyboard
except ImportError:
    print("NO_KEYBOARD", flush=True)
    os._exit(0)
import tkinter as tk
import WindowOperator
root = tk.Tk()
root.withdraw()
root.update()
events = []
keyboard.hook_key("^", lambda e: events.append(e.event_type), suppress=False)
sent = WindowOperator._hold_key_background(int(root.winfo_id()), "^", 0.1)
deadline = time.time() + 0.5
while time.time() < deadline:
    root.update()
    time.sleep(0.02)
background = list(events)
keyboard._listener.queue.put(keyboard.KeyboardEvent("down", keyboard.key_to_scan_codes("^")[0], "^"))
deadline = time.time() + 2.0
while len(events) == len(background) and time.time() < deadline:
    time.sleep(0.02)
print("RESULT " + json.dumps({"sent": sent, "background": background,
                              "control": events[len(background):]}), flush=True)
os._exit(0)
"""

    # ── 設定 ──────────────────────────────────
    def test_the_default_is_caret(self):
        self.assertEqual(config.SUICIDE_CANCEL_KEY, "^")
        self.assertTrue(HotKey.is_valid(config.SUICIDE_CANCEL_KEY))

    def _load(self, data):
        loader = TestStartKeySettings("test_it_is_saved")
        return loader._load(data)

    def test_it_is_restored(self):
        app = self._load({"emergency_stop_key": "p", "suicide_cancel_key": "f8"})
        self.assertEqual(app.v_suicide_cancel_key.get(), "f8")

    def test_a_missing_or_broken_value_is_the_default(self):
        self.assertEqual(self._load({}).v_suicide_cancel_key.get(), "^")
        loader = TestStartKeySettings("test_it_is_saved")
        app = loader._load({"emergency_stop_key": "p", "suicide_cancel_key": "nokey"}, valid=False)
        self.assertEqual(app.v_suicide_cancel_key.get(), "^")

    def test_a_saved_value_equal_to_another_key_is_the_default(self):
        for other in ({"emergency_stop_key": "f8"}, {"start_key": "f8"}, {}):
            data = dict(other, suicide_cancel_key="f8" if other else "f1")
            self.assertEqual(self._load(data).v_suicide_cancel_key.get(), "^", data)

    def test_it_is_saved(self):
        app = _with_cancel_key(type("FakeApp", (), {})(), "f8")
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_freeze_8pages", "v_freeze_punish",
                     "v_emergency_key", "v_start_key", "v_big_key", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, _CancelKeyVar(""))
        app.v_freeze_rounds = {}
        app._window_volume_settings = lambda: {}
        app._fog_early_read_setting = lambda: {}
        app._launch_options_setting = lambda: {}
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(app)
        self.assertEqual(saved["suicide_cancel_key"], "f8")

    def _capture_app(self, cancel="^", emergency="p", start="f9"):
        app = _with_cancel_key(type("FakeApp", (), {})(), cancel)
        app.v_emergency_key = _CancelKeyVar(emergency)
        app.v_start_key = _CancelKeyVar(start)
        app.v_big_key = _CancelKeyVar("")
        app._hook_suicide_cancel_key = MagicMock()
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._start_key_pressed = False
        app.logs = []
        app._log = app.logs.append
        return app

    def test_the_same_key_as_another_cannot_be_chosen(self):
        for key, name in (("p", "緊急停止"), ("f9", "マクロ開始"), ("f1", "チェイス"), ("f2", "チェイス")):
            app = self._capture_app()
            mainGUI.App._finish_capture_cancel_key(app, key)
            self.assertEqual(app.v_suicide_cancel_key.get(), "^", key)
            app._hook_suicide_cancel_key.assert_not_called()
            self.assertIn(f"[自爆キャンセル] ⚠ {name}のキーと同じキーは使えません", app.logs[-1])

    def test_a_broken_or_missing_key_keeps_the_setting(self):
        for key in (None, "nokey"):
            app = self._capture_app()
            with patch.object(HotKey, "is_valid", side_effect=lambda k: k != "nokey"):
                mainGUI.App._finish_capture_cancel_key(app, key)
            self.assertEqual(app.v_suicide_cancel_key.get(), "^", key)
            app._hook_suicide_cancel_key.assert_not_called()

    def test_a_new_key_is_set_and_hooked(self):
        app = self._capture_app()
        mainGUI.App._finish_capture_cancel_key(app, "f8")
        self.assertEqual(app.v_suicide_cancel_key.get(), "f8")
        app._hook_suicide_cancel_key.assert_called_once_with()
        self.assertEqual(app.logs[-1], "[自爆キャンセル] F8キーに変更しました")

    def test_the_other_keys_cannot_take_the_cancel_key(self):
        app = self._capture_app()
        mainGUI.App._finish_capture_key(app, "^", "stop")
        self.assertEqual(app.v_emergency_key.get(), "p")
        app = self._capture_app()
        mainGUI.App._finish_capture_start_key(app, "^")
        self.assertEqual(app.v_start_key.get(), "f9")




class TestStartKeySettings(unittest.TestCase):
    """settings.json への保存と復元。壊れた値で勝手に動き出さないこと"""

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

        def set(self, value):
            self._v = value

    def _load(self, data, valid=True):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_instance_link",
                     "v_emergency_key", "v_start_key", "v_big_key", "v_freeze_8pages",
                     "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, self.FakeVar(""))
        app.v_freeze_rounds = {}
        app._add_tool_row = lambda p, save=True: None
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._apply_obs_settings = lambda: None
        app._apply_saved_window_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app.logs = []
        app._log = app.logs.append
        with patch.object(mainGUI, "load_settings", return_value=dict(data)), \
             patch.object(mainGUI, "save_settings", lambda _d: None):
            if valid:
                app._load_window_volume_settings = lambda _data: None
                app._load_fog_early_read_setting = lambda _data: None
                app._load_launch_options_setting = lambda _data: None
                mainGUI.App._load_saved_settings(app)
            else:
                with patch.object(HotKey, "is_valid",
                                  side_effect=lambda k: k == "p"):
                    app._load_window_volume_settings = lambda _data: None
                    app._load_fog_early_read_setting = lambda _data: None
                    app._load_launch_options_setting = lambda _data: None
                    mainGUI.App._load_saved_settings(app)
        return app

    def test_it_is_saved(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_freeze_8pages",
                     "v_freeze_punish", "v_emergency_key", "v_obs_enabled",
                     "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, self.FakeVar(""))
        app.v_start_key = self.FakeVar("f9")
        app.v_big_key = self.FakeVar("")
        app.v_freeze_rounds = {}
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["start_key"], "f9")

    def test_it_is_restored(self):
        app = self._load({"start_key": "f9"})

        self.assertEqual(app.v_start_key.get(), "f9")

    def test_an_empty_value_stays_unset(self):
        app = self._load({"start_key": ""})

        self.assertEqual(app.v_start_key.get(), "")

    def test_a_broken_value_is_disabled_not_defaulted(self):
        app = self._load({"start_key": "zzz"}, valid=False)

        self.assertEqual(app.v_start_key.get(), "")
        self.assertNotEqual(app.v_start_key.get(), config.EMERGENCY_STOP_KEY)

    def test_the_same_key_as_the_stop_key_is_disabled(self):
        app = self._load({"emergency_stop_key": "f9", "start_key": "f9"})

        self.assertEqual(app.v_emergency_key.get(), "f9", "停止キーは残す")
        self.assertEqual(app.v_start_key.get(), "", "開始キーを無効にする")

    def test_a_legacy_file_without_the_key_is_fine(self):
        app = self._load({"tnl_path": "C:/list/my.tnl"})

        self.assertEqual(app.v_start_key.get(), "")
