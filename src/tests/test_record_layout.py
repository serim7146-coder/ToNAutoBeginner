"""OBS 録画で続行中の窓を並べる計算（純粋。OBS にはつながない）"""
from tests.support import *  # noqa: F401,F403
import math

import RecordLayout

# 実機（2026-11-17）の 4 窓。2×2 に並べてあり、サブモニタなので x が負になる
REAL_WINDOWS = [
    (-1927, 357, -953, 880),
    (-967, 357, 7, 880),
    (-1927, 873, -953, 1396),
    (-967, 873, 7, 1396),
]
# モニタの矩形は実機の値ではなく仮定（窓の並びから見て 1920 幅のサブモニタが x=-1920〜0 にある形）。
# 窓は両端で 7px はみ出している（見えない縁）
SUB_MONITOR = (-1920, 0, 0, 1440)


class TestGrid(unittest.TestCase):
    def test_grid_follows_the_table(self):
        table = {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2),
                 7: (3, 3), 8: (3, 3)}
        for n, expected in table.items():
            self.assertEqual(RecordLayout.grid_for(n), expected, n)

    def test_grid_never_has_fewer_cells_than_windows(self):
        for n in range(1, 17):
            cols, rows = RecordLayout.grid_for(n)
            self.assertGreaterEqual(cols * rows, n)
            self.assertEqual(cols, math.ceil(n / rows))


class TestCells(unittest.TestCase):
    def test_a_full_grid_covers_the_canvas_without_gaps_or_overlap(self):
        for n in (1, 2, 3, 4, 6, 9):
            frames = RecordLayout.cells(n, (1920, 1080))
            self.assertEqual(len(frames), n)
            self.assertEqual(sum(w * h for _x, _y, w, h in frames), 1920 * 1080, n)
            self.assertTrue(self._no_overlap(frames), n)

    def test_cells_never_overlap_even_when_the_last_row_is_short(self):
        for n in range(1, 13):
            frames = RecordLayout.cells(n, (1921, 1079))      # 割り切れない画布
            self.assertTrue(self._no_overlap(frames), n)
            for x, y, w, h in frames:
                self.assertGreater(w, 0)
                self.assertGreater(h, 0)
                self.assertLessEqual(x + w, 1921)
                self.assertLessEqual(y + h, 1079)

    def test_two_windows_are_split_left_and_right(self):
        self.assertEqual(RecordLayout.cells(2, (1920, 1080)),
                         [(0, 0, 960, 1080), (960, 0, 960, 1080)])

    def test_one_window_fills_the_canvas_and_four_make_two_by_two(self):
        self.assertEqual(RecordLayout.cells(1, (1920, 1080)), [(0, 0, 1920, 1080)])
        self.assertEqual(RecordLayout.cells(4, (1920, 1080)),
                         [(0, 0, 960, 540), (960, 0, 960, 540),
                          (0, 540, 960, 540), (960, 540, 960, 540)])

    @staticmethod
    def _no_overlap(frames):
        for i, (x1, y1, w1, h1) in enumerate(frames):
            for x2, y2, w2, h2 in frames[i + 1:]:
                if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                    return False
        return True


class TestItemTransform(unittest.TestCase):
    CELL = (0, 0, 960, 1080)

    def test_the_crop_is_the_window_position_inside_the_monitor(self):
        t = RecordLayout.item_transform(self.CELL, (0, 0, 2560, 1440), (100, 200, 1100, 900))

        self.assertEqual((t["cropLeft"], t["cropTop"], t["cropRight"], t["cropBottom"]),
                         (100, 200, 1460, 540))

    def test_the_item_fits_the_cell_inside_bounds_keeping_the_aspect(self):
        t = RecordLayout.item_transform((960, 0, 960, 1080), (0, 0, 2560, 1440),
                                        (100, 200, 1100, 900))

        self.assertEqual(t["boundsType"], "OBS_BOUNDS_SCALE_INNER")
        self.assertEqual((t["boundsWidth"], t["boundsHeight"]), (960.0, 1080.0))
        self.assertEqual((t["positionX"], t["positionY"]), (960.0, 0.0))
        self.assertEqual(t["alignment"], 5)

    def test_negative_monitor_coordinates_give_the_real_machines_crop(self):
        """サブモニタの x が負でも、窓 − モニタで正しく出る（実機の 4 窓）"""
        crops = []
        for window in REAL_WINDOWS:
            t = RecordLayout.item_transform(self.CELL, SUB_MONITOR, window)
            crops.append((t["cropLeft"], t["cropTop"], t["cropRight"], t["cropBottom"]))

        self.assertEqual(crops, [(0, 357, 953, 560), (953, 357, 0, 560),
                                 (0, 873, 953, 44), (953, 873, 0, 44)])

    def test_crop_is_never_negative_when_the_window_sticks_out(self):
        """実機の窓は左右で 7px はみ出している。縦も 1080 のモニタなら下へはみ出す"""
        for monitor in (SUB_MONITOR, (-1920, 0, 0, 1080)):
            for window in REAL_WINDOWS:
                crop = RecordLayout.crop_for(monitor, window)
                if crop is None:
                    continue
                self.assertTrue(all(c >= 0 for c in crop), (monitor, window, crop))

    def test_a_window_below_the_monitor_is_not_placed(self):
        self.assertIsNone(RecordLayout.item_transform(self.CELL, (-1920, 0, 0, 1080),
                                                      (-1927, 1100, -953, 1400)))

    def test_a_window_on_another_monitor_is_not_placed(self):
        self.assertIsNone(RecordLayout.item_transform(self.CELL, SUB_MONITOR,
                                                      (100, 100, 900, 700)))


class TestSourceNames(unittest.TestCase):
    def test_the_tool_recognises_only_its_own_sources(self):
        self.assertEqual(RecordLayout.source_name(2), config.OBS_SOURCE_NAME.format(idx=2))
        self.assertTrue(RecordLayout.is_tool_source(RecordLayout.source_name(12)))
        self.assertFalse(RecordLayout.is_tool_source("ゲーム画面"))
        self.assertFalse(RecordLayout.is_tool_source(RecordLayout.source_name(1) + "x"))
        self.assertFalse(RecordLayout.is_tool_source(None))


class TestPickMonitor(unittest.TestCase):
    ITEMS = [
        {"itemName": "Display 1: 2560x1440 @ 0,0 (Primary Monitor)", "itemValue": "MAIN",
         "itemEnabled": True},
        {"itemName": "Display 2: 1920x1440 @ -1920,0", "itemValue": "SUB", "itemEnabled": True},
    ]

    def test_it_picks_the_monitor_by_position_and_size(self):
        self.assertEqual(RecordLayout.pick_monitor(self.ITEMS, (-1920, 0, 0, 1440)), "SUB")
        self.assertEqual(RecordLayout.pick_monitor(self.ITEMS, (0, 0, 2560, 1440)), "MAIN")

    def test_a_size_mismatch_falls_back_to_the_position(self):
        self.assertEqual(RecordLayout.pick_monitor(self.ITEMS, (-1920, 0, 0, 1080)), "SUB")

    def test_no_match_is_none(self):
        self.assertIsNone(RecordLayout.pick_monitor(self.ITEMS, (5000, 0, 6000, 1000)))
        self.assertIsNone(RecordLayout.pick_monitor([], (0, 0, 1, 1)))
        self.assertIsNone(RecordLayout.pick_monitor([{"itemName": "?", "itemValue": 1}],
                                                    (0, 0, 1, 1)))

    def test_a_disabled_choice_is_skipped(self):
        items = [dict(self.ITEMS[1], itemEnabled=False)]
        self.assertIsNone(RecordLayout.pick_monitor(items, (-1920, 0, 0, 1440)))


class TestPickInputKind(unittest.TestCase):
    def test_it_searches_the_list_instead_of_assuming_a_name(self):
        self.assertEqual(RecordLayout.pick_input_kind(["image_source", "monitor_capture"]),
                         "monitor_capture")
        self.assertEqual(RecordLayout.pick_input_kind(["x", "monitor_capture_v2"]),
                         "monitor_capture_v2")
        self.assertEqual(RecordLayout.pick_input_kind(["display_capture"]), "display_capture")
        self.assertIsNone(RecordLayout.pick_input_kind(["image_source"]))
        self.assertIsNone(RecordLayout.pick_input_kind(None))


class TestPurity(unittest.TestCase):
    def test_layout_and_recorder_do_not_touch_windows_or_the_network(self):
        """RecordLayout は計算だけ。Recorder は窓を触らない（窓の矩形は注入で受け取る）"""
        forbidden = {"socket", "ctypes", "win32gui", "win32api", "win32con", "WindowOperator",
                     "keyboard", "pydirectinput", "subprocess"}
        for name in ("RecordLayout.py", "Recorder.py"):
            source = Path(name).read_text(encoding="utf-8")
            imports = set(re.findall(r"^(?:import|from) (\w+)", source, re.M))
            self.assertFalse(imports & forbidden, (name, imports & forbidden))


class TestMonitorOfWindow(unittest.TestCase):
    """win32 を差し替えて、窓の操作をせずに戻り値だけ見る"""

    def _patched(self, iconic=False, visible=True, exists=True):
        gui, api = MagicMock(), MagicMock()
        gui.IsWindow.return_value = exists
        gui.IsIconic.return_value = iconic
        gui.IsWindowVisible.return_value = visible
        gui.GetWindowRect.return_value = (-1927, 357, -953, 880)
        api.GetMonitorInfo.return_value = {"Monitor": (-1920, 0, 0, 1440), "Work": (-1920, 0, 0, 1400)}
        return (patch.object(WindowOperator, "win32gui", gui),
                patch.object(WindowOperator, "win32api", api))

    def test_it_returns_the_monitor_and_the_window_in_desktop_coordinates(self):
        a, b = self._patched()
        with a, b:
            self.assertEqual(WindowOperator.monitor_of_window(0x1234),
                             ((-1920, 0, 0, 1440), (-1927, 357, -953, 880)))

    def test_a_missing_minimized_or_hidden_window_gives_none(self):
        for kw in ({"exists": False}, {"iconic": True}, {"visible": False}):
            a, b = self._patched(**kw)
            with a, b:
                self.assertIsNone(WindowOperator.monitor_of_window(0x1234), kw)
        a, b = self._patched()
        with a, b:
            self.assertIsNone(WindowOperator.monitor_of_window(0))

    def test_an_exception_gives_none(self):
        a, b = self._patched()
        with a, b:
            WindowOperator.win32api.GetMonitorInfo.side_effect = OSError("gone")
            self.assertIsNone(WindowOperator.monitor_of_window(0x1234))


if __name__ == "__main__":
    unittest.main()
