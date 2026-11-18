"""入室の選択画面の四角（EntryPanel）。合成した絵だけを使う（実物の撮影は使わない・置かない）"""
from tests.support import *  # noqa: F401,F403
import cv2
import numpy as np

import EntryPanel

GROUND = (0, 0, 16)          # 地（実測 BGR）
FRAME = (116, 117, 194)      # 枠（実測 BGR）


def blank(height):
    width = int(round(height * 16 / 9))
    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:, :] = GROUND
    return img


def frame(img, x, y, w, h, thick):
    cv2.rectangle(img, (x, y), (x + w, y + h), FRAME, thick)


def scene(height, boxes):
    """1080 を基準にした boxes（x, y, w, h）を、高さ height へ縮めて描く"""
    k = height / 1080.0
    img = blank(height)
    thick = 2 if height >= 800 else 1
    for x, y, w, h in boxes:
        frame(img, int(x * k), int(y * k), int(w * k), int(h * k), thick)
    return img


THREE = [(560, 500, 220, 90), (860, 500, 220, 90), (1160, 500, 220, 90)]


class TestEntryPanel(unittest.TestCase):
    def test_three_in_a_row_are_picked_by_index(self):
        img = scene(1080, THREE)
        for index, (x, y, w, h) in enumerate(THREE):
            with self.subTest(index=index):
                point = EntryPanel.pick(img, 1080, 3, index)
                self.assertIsNotNone(point)
                self.assertLessEqual(abs(point[0] - (x + w / 2)), 2)
                self.assertLessEqual(abs(point[1] - (y + h / 2)), 2)

    def test_expect_mismatch_returns_none(self):
        img = scene(1080, THREE[:2])
        self.assertIsNone(EntryPanel.pick(img, 1080, 3, 0))
        self.assertIsNotNone(EntryPanel.pick(img, 1080, 2, 0))

    def test_index_out_of_range_returns_none(self):
        self.assertIsNone(EntryPanel.pick(scene(1080, THREE), 1080, 3, 5))

    def test_empty_picture_returns_none(self):
        self.assertIsNone(EntryPanel.pick(blank(1080), 1080, None, 0))

    def test_grid_picks_the_top_left_cell(self):
        # 枠どうしが繋がった格子（4 列 5 段）。expect=None なら個数は見ない
        x0, y0, cw, ch = 700, 300, 120, 60
        img = blank(1080)
        for row in range(5):
            for col in range(4):
                frame(img, x0 + col * cw, y0 + row * ch, cw, ch, 2)
        point = EntryPanel.pick(img, 1080, None, 0)
        self.assertIsNotNone(point)
        self.assertLessEqual(abs(point[0] - (x0 + cw / 2)), 2)
        self.assertLessEqual(abs(point[1] - (y0 + ch / 2)), 2)

    def test_filled_rect_is_not_picked(self):
        img = blank(1080)
        cv2.rectangle(img, (600, 500), (820, 590), FRAME, -1)
        # 文字で埋まった想定の小さい穴（最小辺より小さい）
        for i in range(8):
            cv2.rectangle(img, (610 + i * 24, 530), (613 + i * 24, 540), GROUND, -1)
        self.assertEqual(EntryPanel.find_rects(img, 1080), [])
        self.assertIsNone(EntryPanel.pick(img, 1080, None, 0))

    def test_diagonal_line_rect_is_not_picked(self):
        img = blank(1080)
        frame(img, 600, 500, 220, 90, 2)
        cv2.line(img, (600, 590), (820, 500), FRAME, 2)       # 左の JP の枠のような斜線
        self.assertEqual(EntryPanel.find_rects(img, 1080), [])

    def test_double_size_rect_is_not_mixed_in(self):
        # 同じ行に 2 倍の四角が 1 個 ＋ 選択肢が 3 個
        img = scene(1080, [(60, 455, 440, 180)] + THREE)
        group = EntryPanel.best_group(img, 1080)
        self.assertEqual(len(group), 3)
        self.assertTrue(all(abs(r["w"] - 220) <= 6 for r in group))
        self.assertIsNotNone(EntryPanel.pick(img, 1080, 3, 2))

    def test_same_count_at_two_window_heights(self):
        counts = {}
        for height in (1080, 466):
            counts[height] = len(EntryPanel.best_group(scene(height, THREE), height))
        self.assertEqual(counts, {1080: 3, 466: 3})

    def test_row_tolerance(self):
        def rect(y):
            return {"x": 0, "y": y, "w": 200, "h": 80, "cx": 100.0, "cy": y + 40.0}
        base = rect(500)
        same = [base, {**rect(500 + 80 * 0.3), "cx": 400.0}]
        other = [base, {**rect(500 + 80 * 1.2), "cx": 400.0}]
        self.assertEqual(max(len(g) for g in EntryPanel.group_rows(same)), 2)
        self.assertEqual(max(len(g) for g in EntryPanel.group_rows(other)), 1)

    def test_miss_numbers_has_numbers_only(self):
        text = EntryPanel.miss_numbers(scene(1080, THREE[:2]), 1080, 3)
        rest = text
        for word in ("四角", "条件を満たす", "いちばん多い組", "個", "大きさ", "期待"):
            rest = rest.replace(word, "")
        self.assertRegex(rest, r"^[0-9x/ ]*$", text)
        self.assertIn("期待 3", text)
        self.assertNotIn("usr_", text)


if __name__ == "__main__":
    unittest.main()
