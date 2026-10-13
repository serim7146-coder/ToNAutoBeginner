"""EW: Begin の位置合わせの各段の撮影を毎回残す／Run の基準の撮影から暗い画面（死んだ画面など）を外す。
本物の撮影・前面・入力は使わない（撮影・照準・見つけた結果・移動を偽物に。残す先は support が差し替えた設定の置き場所の下）"""
from tests.support import *  # noqa: F401,F403
import inspect
import shutil
import numpy as np
import cv2
import BeginAdjustShots
import RunPos
from tests.test_run_pos import _world

W, H = 400, 240
AIM = (200, 120)


def _bits(value=90):
    img = np.full((H, W, 4), value, np.uint8)
    img[:, :, 3] = 255
    return img.tobytes()


def _hit(cx, w=60.0):
    return {"score": 0.9, "dark": 0.5, "cx": float(cx), "cy": 120.0, "w": float(w), "h": 16.0}


def _clear():
    d = BeginAdjustShots.folder()
    if d.is_dir():
        shutil.rmtree(d)


def _groups():
    d = BeginAdjustShots.folder()
    return sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def _read(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


class TestEachStageIsKept(unittest.TestCase):
    def setUp(self):
        _clear()
        self.addCleanup(_clear)
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=6, round_seq=1)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x60, osc_port=9000), self.st,
                                                lambda: True, lambda _m: None)
        self.moves = []
        self.misses = []
        for target, name, value in (
                (WindowOperator, "aim_in_window_image", lambda h: AIM),
                (ScreenCapture, "capture_window", lambda h: (_bits(), W, H)),
                (BeginDetect, "available", lambda: True),
                (BeginMiss, "save", lambda *a: self.misses.append(a[4])),
                (DebugLog, "write", lambda m: None),
                (ActionExecutor.time, "sleep", lambda s: None)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.ex._adjust_stopped = lambda *_a: False
        self.ex.move = lambda d, s: self.moves.append(d)

    def _adjust(self, finds):
        seq = list(finds)
        with patch.object(BeginDetect, "find", side_effect=lambda *a: seq.pop(0) if seq else _hit(200, 0.12 * H)):
            return self.ex._adjust_to_begin(1)

    def test_a_found_stage_is_kept_with_the_box_and_the_aim(self):
        """23:12 窓1 のように見つかった段も残る（右 +49px →動いた後）"""
        result = self._adjust([_hit(249), _hit(200, 0.12 * H)])
        self.assertEqual(result, "aimed")
        self.assertEqual(self.moves[0], "right")
        groups = _groups()
        self.assertEqual(len(groups), 1, "1 回の位置合わせで 1 組")
        self.assertTrue(groups[0].name.endswith("_窓6"), groups[0].name)
        names = sorted(p.name for p in groups[0].glob("*.jpg"))
        self.assertEqual(names[:2], ["01_最初.jpg", "02_動いた後.jpg"])
        img = _read(groups[0] / "01_最初.jpg")
        self.assertEqual(img.shape[:2], (H // 2, W // 2), "1/2")
        # 四角（緑）: 中心 249・幅 60・高さ 16 → 左端 219、上端 112 の 1/2
        left = img[int(120 / 2), int(219 / 2)]
        self.assertTrue(left[1] > 180 and left[0] < 120 and left[2] < 120, left)
        # 照準（黄の十字）: (200, 120) の 1/2
        aim = img[int(120 / 2), int(200 / 2) + 4]
        self.assertTrue(aim[1] > 180 and aim[2] > 180 and aim[0] < aim[1] - 60, aim)
        self.assertEqual(self.misses, [], "見つかった段は begin_miss には残さない（今のまま）")

    def test_a_missed_stage_is_kept_without_drawing_and_begin_miss_is_as_before(self):
        self._adjust([None] * 40)
        groups = _groups()
        self.assertEqual(len(groups), 1)
        names = sorted(p.name for p in groups[0].glob("*.jpg"))
        self.assertEqual(names[0], "01_最初_なし.jpg")
        self.assertTrue(any("_探す_なし" in n for n in names), names)
        img = _read(groups[0] / names[0])
        self.assertLess(int(np.abs(img.astype(int) - 90).max()), 12, "見つからない段は何も描かない")
        self.assertEqual(self.misses[0], "最初", "begin_miss は今のまま")
        self.assertEqual(len(names), len(self.misses), "begin_miss に残した段はどれもこちらにも残る")

    def test_each_adjust_is_its_own_group_and_nothing_outside_the_adjust(self):
        self._adjust([_hit(200, 0.12 * H)])
        self._adjust([_hit(200, 0.12 * H)])
        self.assertEqual(len(_groups()), 2)
        self.assertIsNone(self.ex._adjust_group)
        with patch.object(BeginDetect, "find", return_value=_hit(200)), \
             patch.object(BeginAdjustShots, "save") as save:
            self.ex._look_for_begin("最初")
        self.assertEqual(len(_groups()), 2, "位置合わせの外では残さない")
        save.assert_not_called()

    def test_the_group_is_closed_even_when_the_adjust_fails(self):
        with patch.object(self.ex, "_adjust_to_begin_inner", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                self.ex._adjust_to_begin(1)
        self.assertIsNone(self.ex._adjust_group)

    def test_a_save_failure_does_not_stop_the_adjust(self):
        with patch.object(Path, "write_bytes", side_effect=OSError("disk")), \
             patch.object(DebugLog, "exception") as ex:
            result = self._adjust([_hit(249), _hit(200, 0.12 * H)])
        self.assertEqual(result, "aimed")
        ex.assert_called()


class TestHowManyGroups(unittest.TestCase):
    def setUp(self):
        _clear()
        self.addCleanup(_clear)

    def test_ten_groups(self):
        self.assertEqual(BeginAdjustShots.KEEP, 10)
        names = [f"20261013_2300{i:02d}_000000_窓{1 + i % 2}" for i in range(12)]
        for n in names:
            BeginAdjustShots.save(n, 1, "最初", _bits(), W, H, None, AIM)
            BeginAdjustShots.save(n, 2, "動いた後", _bits(), W, H, _hit(200), AIM)
        self.assertEqual([p.name for p in _groups()], names[2:], "古い 2 組を消す")
        self.assertEqual(len(list(_groups()[-1].glob("*.jpg"))), 2)

    def test_the_folder_is_next_to_the_settings(self):
        self.assertEqual(BeginAdjustShots.folder(), Path(config.SETTINGS_PATH).parent / "begin_adjust")

    def test_a_broken_shot_is_not_saved(self):
        self.assertIsNone(BeginAdjustShots.save("g_窓1", 1, "最初", b"", W, H, None, AIM))
        self.assertEqual(_groups(), [])


class TestTheBugReport(unittest.TestCase):
    def setUp(self):
        _clear()
        self.addCleanup(_clear)

    def test_files_for_a_window(self):
        BeginAdjustShots.save("20261013_231200_000000_窓1", 1, "最初", _bits(), W, H, _hit(249), AIM)
        BeginAdjustShots.save("20261013_231300_000000_窓2", 1, "最初", _bits(), W, H, None, AIM)
        self.assertEqual(list(BeginAdjustShots.files_for(1)),
                         ["begin_adjust/20261013_231200_000000_窓1/01_最初.jpg"])
        self.assertEqual(len(BeginAdjustShots.files_for(0)), 2, "窓なしなら全部")
        self.assertEqual(BeginAdjustShots.files_for(3), {})

    def test_only_with_the_shots_check(self):
        import BugReport
        src = inspect.getsource(BugReport)
        i = src.index("if not include.get(BEGIN_MISS, False):")
        j = src.index("BeginAdjustShots.files_for")
        self.assertLess(i, j, "撮影を入れるチェックの中")
        self.assertIn("for files_for in (BeginMiss.files_for, BeginAdjustShots.files_for, RunFace.files_for, "
                      "FetchMiss.files_for):", src)


class TestTheDarkReference(unittest.TestCase):
    def _dark(self, w, value=13):
        real = w.capture

        def capture():
            img = real()
            img[:] = value
            return img
        w.ex._capture_bgr = capture

    def test_the_value(self):
        self.assertEqual(config.RUN_POS_DARK_MAX, 15.0)

    def test_a_dark_screen_is_not_a_reference_and_it_tries_next_round(self):
        w = _world(self)
        self._dark(w)
        for _ in range(3):
            w.round_over()
        self.assertEqual(w.files(), [], "死んだ画面（明るさ 13）は基準にしない")
        self.assertIn("[操作] [窓2] Run の位置: 基準: 撮らない（画面が暗い（死んだ画面など）。明るさ 13.0 < 15.0）",
                      w.written)
        w.ex._capture_bgr = w.capture               # 明るさ 40
        w.round_over()
        self.assertEqual(len(w.files("ref")), 1, "次のラウンドでまた試す")
        self.assertTrue(any("基準: 撮った" in m and "明るさ 40.0" in m for m in w.written), w.written)

    def test_just_at_the_line_is_taken(self):
        w = _world(self)
        self._dark(w, 15)
        for _ in range(3):
            w.round_over()
        self.assertEqual(len(w.files("ref")), 1, "15 は暗くない")
        w2 = _world(self)
        self._dark(w2, 14)
        for _ in range(3):
            w2.round_over()
        self.assertEqual(w2.files(), [])

    def test_an_unmeasurable_shot_is_not_a_reference(self):
        w = _world(self)
        with patch.object(RunPos, "brightness", return_value=None):
            for _ in range(3):
                w.round_over()
        self.assertEqual(w.files(), [])
        self.assertTrue(any("画面が暗い（死んだ画面など）。明るさ 測れない" in m for m in w.written), w.written)

    def test_a_dark_reference_already_there_is_taken_again(self):
        """前の版が残した暗い基準（今の窓1）があっても、起動ごとに撮り直し、新しい基準は明るい"""
        w = _world(self)
        RunPos.save(np.full((66, 86, 3), 13, np.uint8), 2, "ref", (867, 663))
        for _ in range(3):
            w.round_over()
        refs = sorted(RunPos.folder().glob("*_窓2_ref_*.png"))
        self.assertEqual(len(refs), 2, "撮り直した")
        lights = sorted(RunPos.brightness(_read(p)) for p in refs)
        self.assertEqual([round(v) for v in lights], [13, 40])

    def test_brightness(self):
        self.assertAlmostEqual(RunPos.brightness(np.full((4, 4, 3), 13, np.uint8)), 13.0)
        self.assertIsNone(RunPos.brightness(None))


if __name__ == "__main__":
    unittest.main()
