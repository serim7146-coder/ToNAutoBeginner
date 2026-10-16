"""入室時の Guidance Plush が取れない件（FN）。Plush のやり直しが控えている窓は速度検知の横移動をしない・
装備を確かめられなかったときの撮影を fetch_miss に残す。本物の OSC・キー・マウス・撮影は使わない
（偽の店・偽の撮影・偽の move）"""
from tests.support import *  # noqa: F401,F403
import inspect

import numpy as np

import FetchMiss
import ItemFetch
from tests.test_join_plush import PLUSH, _Shop, at

PRE, POST = "Plush_押す前", "Plush_押した後"


class TestSpeedStrafeWithPlushRetries(unittest.TestCase):
    """やり直しの移動が横移動と重なると柵を越えられない（15:25:46 の窓2）。控えている窓では横移動しない"""

    def setUp(self):
        SharedState.set_speed_detect(True)
        self.addCleanup(SharedState.set_speed_detect, config.SPEED_DETECT_ENABLED)

    def _executor(self, retries=0, **st_kw):
        cfg = WindowConfig(hwnd=123, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, **st_kw)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        ex._speed_ready.set()
        ex._receiver = MagicMock(ever_received=True)
        ex._plush_retries = retries
        return ex, st, logs

    def test_a_window_with_retries_left_does_not_strafe(self):
        for retries in (1, 2, config.PLUSH_RETRY_MAX):
            ex, _st, logs = self._executor(retries=retries)
            with patch.object(ex, "move") as move:
                ex.do_speed_strafe()
            move.assert_not_called()
            self.assertIn("Plush のやり直しのため速度検知の横移動はしません", logs, retries)

    def test_a_window_with_retries_left_sends_no_osc(self):
        ex, _st, _logs = self._executor(retries=config.PLUSH_RETRY_MAX)
        ex._osc = MagicMock()
        ex.do_speed_strafe()
        ex._osc.send.assert_not_called()

    def test_no_retries_left_strafes_as_before(self):
        ex, _st, logs = self._executor(retries=0)
        moves = []
        with patch.object(ex, "move", side_effect=lambda d, sec: moves.append((d, sec))):
            ex.do_speed_strafe()
        self.assertEqual(moves, [("right", config.SPEED_PROBE_RIGHT_SEC),
                                 ("left", config.SPEED_PROBE_LEFT_SEC)])
        self.assertFalse(any("横移動はしません" in m for m in logs))

    def test_waiting_for_the_item_keeps_its_own_message(self):
        ex, _st, logs = self._executor(retries=2, waiting_for_equip=True)
        with patch.object(ex, "move") as move:
            ex.do_speed_strafe()
        move.assert_not_called()
        self.assertIn("アイテムロスト後のため速度検知の横移動はしません", logs)
        self.assertNotIn("Plush のやり直しのため速度検知の横移動はしません", logs)

    def test_the_retry_count_is_what_it_looks_at(self):
        ex, _st, _logs = self._executor(retries=0)
        ex.request_plush_retry("test")
        self.assertEqual(ex._plush_retries, config.PLUSH_RETRY_MAX)
        with patch.object(ex, "move") as move:
            ex.do_speed_strafe()
        move.assert_not_called()
        ex._plush_retries = 0                   # 取れた・持った・使い切った
        with patch.object(ex, "move") as move:
            ex.do_speed_strafe()
        self.assertEqual(move.call_count, 2)


class TestPlushMissShots(unittest.TestCase):
    """装備を確かめられなかったとき、押す前と待った後の撮影を on_miss（FetchMiss）へ。店の画面だけ"""

    def _fetcher(self, equips, on_miss="keep"):
        shop = _Shop(self, equips=equips)
        self.shots = []                         # capture が返した撮影（順）
        real = shop.capture

        def capture():
            shot = real()
            self.shots.append(shot[0])
            return shot
        shop.capture = capture
        self.at_click = []                      # クリックした時点の撮影の枚数
        real_click = shop.click_at
        shop.click_at = lambda x, y: (self.at_click.append(len(self.shots)), real_click(x, y))[1]
        self.kept = []                          # (撮影, 理由, 保存した時点の capture 回数)
        f = shop.fetcher()
        f.on_miss = ((lambda bgr, why: self.kept.append((bgr, why, len(self.shots))))
                     if on_miss == "keep" else on_miss)
        self.shop = shop
        return f

    def test_a_failure_keeps_two_shots_before_and_after(self):
        f = self._fetcher(equips=(None, PLUSH))
        self.assertTrue(f.buy_plush(PLUSH))
        self.assertEqual([k[1] for k in self.kept], [PRE, POST], "1 回目の失敗の分だけ。2 回目は取れた")
        before, after = self.kept[0], self.kept[1]
        self.assertIsInstance(before[0], np.ndarray)
        self.assertEqual(before[0].shape, (1080, 1920, 3), "店の画面の撮影そのもの（切り出していない）")
        self.assertIs(before[0], self.shots[self.at_click[1] - 1], "押す前 = 行を押す直前の撮影（行を見つけた撮影）")
        self.assertIs(after[0], self.shots[after[2] - 1], "押した後 = Equip を待った後に撮り直した撮影")
        self.assertGreater(after[2], before[2], "待った後に撮り直している")

    def test_the_shots_are_taken_at_the_right_time(self):
        f = self._fetcher(equips=(None, None))
        self.assertFalse(f.buy_plush(PLUSH))
        events = self.shop.events
        row_clicks = [i for i, e in enumerate(events) if e[0] == "click"
                      and abs(e[2] - at(ItemFetch.PLUSH_ROW)[1]) < 1.5]
        self.assertEqual(len(row_clicks), 2, "行は 2 回押す")
        self.assertEqual([k[1] for k in self.kept], [PRE, POST, PRE, POST], "2 回とも失敗すれば 2 回分")
        # 押す前の撮影は、その回の行の撮影（行の緑が写っている）
        x, y = (int(v) for v in at(ItemFetch.PLUSH_ROW))
        for bgr, why, _n in self.kept:
            self.assertTrue((bgr[y, x] == (0, 200, 0)).all(), why)

    def test_the_click_points_are_unchanged(self):
        f = self._fetcher(equips=(None, None))
        f.buy_plush(PLUSH)
        f2 = self._fetcher(equips=(None, None), on_miss=None)
        shop_a = self.shop
        f2.buy_plush(PLUSH)
        self.assertEqual(shop_a.events, self.shop.events, "撮影を残しても残さなくても、押す点・順は同じ")
        clicks = [e for e in self.shop.events if e[0] == "click"]
        self.assertEqual(len(clicks), 5, "Event・行・Equip・行・Equip")
        ex, ey = at(ItemFetch.BUTTONS["Equip"])
        for e in (clicks[2], clicks[4]):
            self.assertAlmostEqual(e[1], ex, delta=3.0)
            self.assertAlmostEqual(e[2], ey, delta=3.0)
        for e in (clicks[1], clicks[3]):
            self.assertAlmostEqual(e[1], at(ItemFetch.PLUSH_ROW)[0], delta=1.5, msg="緑の重心")
            self.assertAlmostEqual(e[2], at(ItemFetch.PLUSH_ROW)[1], delta=1.5)

    def test_a_success_keeps_nothing(self):
        f = self._fetcher(equips=(PLUSH,))
        self.assertTrue(f.buy_plush(PLUSH))
        self.assertEqual(self.kept, [])

    def test_a_wrong_id_also_keeps_the_shots(self):
        f = self._fetcher(equips=(81, 81))
        self.assertFalse(f.buy_plush(PLUSH))
        self.assertEqual([k[1] for k in self.kept], [PRE, POST, PRE, POST])

    def test_a_row_not_found_keeps_only_the_old_miss_not_the_new_ones(self):
        shop = _Shop(self, no_row=True)
        f = shop.fetcher()
        kept = []
        f.on_miss = lambda bgr, why: kept.append(why)
        self.assertFalse(f.buy_plush(PLUSH))
        self.assertEqual(kept, ["Have Plush の行"], "行が見つからないときは今までどおり 1 枚")

    def test_a_failing_save_does_not_stop_the_fetch(self):
        saved = []

        def broken(bgr, why):
            saved.append(why)
            raise OSError("disk full")
        f = self._fetcher(equips=(None, PLUSH), on_miss=broken)
        self.assertTrue(f.buy_plush(PLUSH), "保存に失敗しても 2 回目の押し直しまで進んで取れる")
        self.assertEqual(saved, [PRE, POST], "どちらも試す")
        self.assertEqual(self.shop.kinds()[-1], "tab_up")

    def _break_the_capture_after_the_first_equip(self, f, how):
        """最初の Equip を押した直後の撮影（= 押した後の撮り直し）を 1 回だけ失敗させる"""
        real = self.shop.capture
        fired = []

        def capture():
            clicks = sum(1 for e in self.shop.events if e[0] == "click")
            if clicks == 3 and not fired:
                fired.append(1)
                return how()
            return real()
        f.capture = capture
        return fired

    def test_a_failing_capture_does_not_stop_the_fetch(self):
        f = self._fetcher(equips=(None, PLUSH))

        def boom():
            raise RuntimeError("capture")
        fired = self._break_the_capture_after_the_first_equip(f, boom)
        self.assertTrue(f.buy_plush(PLUSH))
        self.assertTrue(fired)
        self.assertEqual([k[1] for k in self.kept], [PRE], "撮れなかった分だけ残らない")

    def test_a_missing_capture_after_waiting_keeps_only_the_before_shot(self):
        f = self._fetcher(equips=(None, PLUSH))
        fired = self._break_the_capture_after_the_first_equip(f, lambda: None)
        self.assertTrue(f.buy_plush(PLUSH))
        self.assertTrue(fired)
        self.assertEqual([k[1] for k in self.kept], [PRE])

    def test_without_on_miss_no_extra_capture_is_taken(self):
        f = self._fetcher(equips=(None, None), on_miss=None)
        f.buy_plush(PLUSH)
        n_without = len(self.shots)
        f = self._fetcher(equips=(None, None))
        f.buy_plush(PLUSH)
        self.assertEqual(len(self.shots), n_without + 2, "撮り直しは失敗 1 回ごとに 1 枚（残す設定のときだけ）")

    def test_the_real_save_names_them_by_window_and_why(self):
        folder = FetchMiss.folder()
        if folder.is_dir():
            for p in folder.glob("*.jpg"):
                p.unlink()
        f = self._fetcher(equips=(None, None),
                          on_miss=lambda bgr, why: FetchMiss.save(bgr, 2, why))
        self.assertFalse(f.buy_plush(PLUSH))
        names = sorted(p.name for p in FetchMiss.folder().glob("*.jpg"))
        self.assertEqual(len(names), 4)
        self.assertEqual(sum(n.endswith("_窓2_Plush_押す前.jpg") for n in names), 2)
        self.assertEqual(sum(n.endswith("_窓2_Plush_押した後.jpg") for n in names), 2)
        self.assertTrue(set(FetchMiss.files_for(2)) == {f"fetch_miss/{n}" for n in names})

    def test_the_executor_still_saves_with_the_window(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor._fetch_item)
        self.assertIn("on_miss=lambda shot, why: FetchMiss.save(shot, st.window_idx, why)", src)
