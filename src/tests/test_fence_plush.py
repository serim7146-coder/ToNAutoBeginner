"""店へ行く途中の柵越えを速さで（ER 1）・見つからなかったときの撮影（ER 2）・入室時の Guidance Plush の
やり直し（ER 3）。本物の OSC・撮影・入力は使わない（偽の時計・速さ・接地・OSC）"""
from tests.support import *  # noqa: F401,F403
import inspect
import numpy as np
import FetchMiss
import ItemFetch


class _Fence:
    """偽の世界。速さ・接地は「押し始めからの時刻」と「最後に跳んだ時刻」で決まる。
    walk_at: 歩き出す時刻（None なら速さが出ない）。hit_at: 柵に当たる時刻。
    clears: 跳んだ回数 → 越えられるか（越えられないと、跳んで 0.3 秒で柵の手前に落ちて張り付く）"""

    def __init__(self, test, walk_at=0.05, hit_at=1.2, clears=(True,), flicker=False, speed=True):
        self.now = [0.0]
        self.sent = []
        self.jumps = []
        self.walk_at, self.hit_at, self.clears, self.flicker = walk_at, hit_at, list(clears), flicker
        self.logs = []
        w = self
        osc = MagicMock()
        osc.send.side_effect = lambda a, v: (w.sent.append((round(w.now[0], 2), a, v)),
                                             w.jumps.append(w.now[0]) if (a == "/input/Jump" and v == 1) else None)
        self.osc = osc
        self.fetcher = ItemFetch.Fetcher(
            osc=osc, grounded=self.grounded, capture=lambda: None, mouse=MagicMock(),
            equip_seen=lambda: (0, 0), stopped=lambda: None, log=self.logs.append,
            sleep=lambda sec: w.now.__setitem__(0, w.now[0] + sec), clock=lambda: w.now[0],
            speed=self.speed if speed else None)

    def _phase(self):
        """('walk'|'fence'|'air1'|'top'|'air2'|'over'|'stuck', 跳んでからの秒)"""
        t = self.now[0]
        if not self.jumps:
            return "walk", None
        j = self.jumps[-1]
        dt = t - j
        ok = self.clears[min(len(self.jumps), len(self.clears)) - 1]
        if not ok:
            return ("air1", dt) if dt < 0.3 else ("stuck", dt)
        if dt < 0.1:
            return "fence", dt          # 跳んだ直後は、まだ地面にいて速さも低い（ここで跳び直さないこと）
        if dt < 0.25:
            return "air1", dt
        if dt < 0.33:
            return "top", dt
        if dt < 0.9:
            return "air2", dt
        return "over", dt

    def speed(self):
        t = self.now[0]
        phase, _dt = self._phase()
        if phase == "walk":
            if self.walk_at is None or t < self.walk_at:
                return 0.0
            return 0.21 if self.hit_at is not None and t >= self.hit_at else 6.0   # 柵に当たると 0.21
        if phase == "stuck":
            return 0.1
        return 2.0 if phase in ("air1", "top", "air2") else (6.0 if phase == "over" else 0.21)

    def grounded(self):
        t = self.now[0]
        phase, _dt = self._phase()
        if phase == "walk":
            # 窓1 の実測: 柵に当たる前の段差で 0→1 が短く 2 回
            if self.flicker and (0.40 <= t < 0.45 or 0.55 <= t < 0.6):
                return False
            return True
        return phase not in ("air1", "air2")

    def left_presses(self):
        return [t for t, a, v in self.sent if a == "/input/MoveLeft" and v == 1]

    def crossed_at(self):
        m = next((l for l in self.logs if l.startswith("アイテム取得: 柵を越えた")), None)
        return float(m.split("柵を越えた ")[1].rstrip("秒")) if m else None


class TestTheFence(unittest.TestCase):
    def test_the_measured_crossing(self):
        """速さ 6 → 0.21 で跳び、接地 0→1（柵の上）→0→1（柵の向こう）で越えた"""
        w = _Fence(self)
        self.assertTrue(w.fetcher.move_to_shop())
        self.assertEqual(len(w.jumps), 1)
        self.assertAlmostEqual(w.jumps[0], 1.2, delta=0.02, msg="柵に当たってから跳ぶ")
        self.assertAlmostEqual(w.crossed_at(), 1.2 + 0.9, delta=0.03)
        self.assertTrue(any(l.startswith("アイテム取得: 柵に当たった 1.2") and "（速さ 0.21）→ 跳ぶ" in l
                            for l in w.logs), w.logs)
        self.assertTrue(any(l.startswith("アイテム取得: 着地 0.2") and "跳んだ 1 回" in l for l in w.logs), w.logs)
        after = [a for _t, a, v in w.sent if v == 1]
        self.assertEqual(after[-3:], ["/input/MoveBackward", "/input/MoveLeft", "/input/LookLeft"],
                         "越えた後は今どおり 後ろ → 後ろ＋左 → 左へ回る")

    def test_no_fixed_jump_at_0_30(self):
        w = _Fence(self, hit_at=2.0)
        w.fetcher.move_to_shop()
        self.assertGreater(w.jumps[0], 1.9, "0.30 秒の決め打ちでは跳ばない")

    def test_walking_flickers_before_the_jump_are_not_landings(self):
        w = _Fence(self, flicker=True)
        self.assertTrue(w.fetcher.move_to_shop())
        self.assertAlmostEqual(w.crossed_at(), 1.2 + 0.9, delta=0.03, msg="跳ぶ前の浮きは数えない")

    def test_stuck_before_the_second_landing_jumps_again(self):
        w = _Fence(self, clears=(False, True))
        self.assertTrue(w.fetcher.move_to_shop())
        self.assertEqual(len(w.jumps), 2)
        self.assertTrue(any("越えられずに張り付いた" in l and "跳び直す（2 回目）" in l for l in w.logs), w.logs)
        self.assertAlmostEqual(w.crossed_at(), w.jumps[1] + 0.9, delta=0.03, msg="跳び直した後から数え直す")

    def test_at_most_three_jumps_then_fail_at_3_seconds(self):
        w = _Fence(self, hit_at=0.5, clears=(False,))
        self.assertFalse(w.fetcher.move_to_shop())
        self.assertEqual(len(w.jumps), ItemFetch.FENCE_JUMPS)
        self.assertEqual(ItemFetch.FENCE_JUMPS, 3)
        self.assertAlmostEqual(w.now[0], 3.0, delta=0.03)
        self.assertTrue(any(l.startswith("アイテム取得: 柵を越えられません（3.0秒・跳んだ 3 回") for l in w.logs), w.logs)
        self.assertEqual(w.sent[-1][1:], ("/input/Jump", 0), "後ろへは進まない")

    def test_no_speed_does_not_jump_and_presses_again(self):
        w = _Fence(self, walk_at=None)
        self.assertFalse(w.fetcher.move_to_shop())
        self.assertEqual(w.jumps, [], "跳ばない")
        presses = w.left_presses()
        self.assertEqual(len(presses), 3, "最初＋押し直し 2 回")
        self.assertAlmostEqual(presses[1], 0.7, delta=0.03)
        self.assertTrue(any("柵を越えられません" in l and "速さが出ない" in l for l in w.logs), w.logs)

    def test_a_late_start_after_a_repress_still_crosses(self):
        w = _Fence(self, walk_at=0.9, hit_at=1.8)
        self.assertTrue(w.fetcher.move_to_shop())
        self.assertEqual(len(w.left_presses()), 3, "0.6 秒で押し直した（1 回）のち、越えた後の後ろ＋左")

    def test_a_long_walk_fails_at_3_seconds(self):
        w = _Fence(self, hit_at=None)
        self.assertFalse(w.fetcher.move_to_shop())
        self.assertEqual(w.jumps, [])
        self.assertAlmostEqual(w.now[0], ItemFetch.FENCE_LIMIT_SEC, delta=0.03)

    def test_no_receiver_is_the_fixed_jump(self):
        w = _Fence(self, speed=False)
        w.fetcher.move_to_shop()
        self.assertAlmostEqual(w.jumps[0], ItemFetch.JUMP_AFTER_SEC, delta=0.01)
        self.assertAlmostEqual(w.jumps[1] - w.jumps[0], ItemFetch.JUMP_GAP_SEC + ItemFetch.JUMP_HOLD_SEC, delta=0.01)

    def test_no_data_yet_is_the_fixed_jump(self):
        w = _Fence(self)
        w.fetcher.speed = lambda: None
        w.fetcher.move_to_shop()
        self.assertAlmostEqual(w.jumps[0], ItemFetch.JUMP_AFTER_SEC, delta=0.01)

    def test_right_after_the_jump_is_not_stuck(self):
        """跳んだ直後（まだ地面にいる・速さ 0.21）は張り付きと見ない（跳び直さない）"""
        w = _Fence(self)
        self.assertTrue(w.fetcher.move_to_shop())
        self.assertEqual(len(w.jumps), 1)
        self.assertFalse(any("張り付いた" in l for l in w.logs), w.logs)

    def test_the_values(self):
        self.assertEqual((ItemFetch.FENCE_MOVE_SPEED, ItemFetch.FENCE_HIT_SPEED, ItemFetch.FENCE_LIMIT_SEC,
                          ItemFetch.FENCE_START_WAIT_SEC, ItemFetch.FENCE_REPRESS_MAX), (1.0, 0.5, 3.0, 0.6, 2))
        self.assertEqual((ItemFetch.BACK_SEC, ItemFetch.BACK_LEFT_SEC, ItemFetch.TURN_SEC), (0.25, 0.40, 0.50),
                         "越えた後の動きは変えない")

    def test_the_executor_passes_the_speed(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor._fetch_item)
        self.assertIn("speed=self._receiver_speed if receiver is not None else None", src)


class TestTheMissShots(unittest.TestCase):
    def setUp(self):
        folder = FetchMiss.folder()
        if folder.is_dir():
            for f in folder.glob("*.jpg"):
                f.unlink()
        self.img = np.zeros((40, 60, 3), np.uint8)

    def test_the_panel_not_found_keeps_the_shot(self):
        missed = []
        f = ItemFetch.Fetcher(osc=MagicMock(), grounded=lambda: True, capture=lambda: (self.img, (30, 20)),
                              mouse=MagicMock(), equip_seen=lambda: (0, 0), stopped=lambda: None,
                              log=lambda _m: None, locate_fn=lambda *_a: None, sleep=lambda _s: None,
                              clock=lambda: 0.0, on_miss=lambda shot, why: missed.append((shot, why)))
        self.assertIsNone(f.frame_panel("Survival"))
        self.assertEqual(len(missed), 1)
        self.assertIs(missed[0][0], self.img)
        self.assertEqual(missed[0][1], "店の画面")

    def test_the_plush_row_not_found_keeps_the_shot(self):
        missed = []
        f = ItemFetch.Fetcher(osc=MagicMock(), grounded=lambda: True, capture=lambda: None, mouse=MagicMock(),
                              equip_seen=lambda: (0, 0), stopped=lambda: None, log=lambda _m: None,
                              sleep=lambda _s: None, clock=lambda: 0.0,
                              on_miss=lambda shot, why: missed.append(why))
        framed = (self.img, (30, 20), (0, 0, 1, 1, 30, lambda pt: (pt[0], pt[1], 1, 1)))

        def no_row(_project):
            f.last_shot = self.img
            return None
        with patch.object(f, "_frame", return_value=framed), patch.object(f, "press_shop", return_value=True), \
             patch.object(f, "_show_plush_row", side_effect=no_row):
            self.assertFalse(f.buy_plush(70))
        self.assertEqual(missed, ["Have Plush の行"])

    def test_a_shot_from_the_row_search_is_remembered(self):
        src = inspect.getsource(ItemFetch.Fetcher._show_plush_row)
        self.assertIn("self.last_shot = shot[0]", src)

    def test_twenty_are_kept_per_folder_and_named_by_window_and_why(self):
        for i in range(25):
            FetchMiss.save(self.img, 1 + i % 2, "店の画面" if i % 2 else "Have Plush の行")
        names = sorted(p.name for p in FetchMiss.folder().glob("*.jpg"))
        self.assertEqual(len(names), FetchMiss.SHOT_KEEP)
        self.assertEqual(FetchMiss.SHOT_KEEP, 20)
        self.assertTrue(any("_窓2_店の画面.jpg" in n for n in names))
        self.assertTrue(any("_窓1_Have Plush の行.jpg" in n for n in names))
        self.assertEqual(FetchMiss.folder(), Path(config.SETTINGS_PATH).parent / "fetch_miss")

    def test_the_bug_report_takes_them_only_with_the_shots_check(self):
        FetchMiss.save(self.img, 3, "店の画面")
        self.assertTrue(all(k.startswith("fetch_miss/") for k in FetchMiss.files_for(3)))
        self.assertEqual(FetchMiss.files_for(4), {})
        import BugReport
        src = inspect.getsource(BugReport)
        self.assertIn("for files_for in (BeginMiss.files_for, RunFace.files_for, FetchMiss.files_for):", src)
        i = src.index("if not include.get(BEGIN_MISS, False):")
        self.assertLess(i, src.index("FetchMiss.files_for):"))

    def test_the_executor_saves_with_the_window(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor._fetch_item)
        self.assertIn("on_miss=lambda shot, why: FetchMiss.save(shot, st.window_idx, why)", src)


class TestThePlushRetry(unittest.TestCase):
    PLUSH = 70

    def setUp(self):
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=4, round_seq=9)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x40, osc_port=9000, auto_begin=True), self.st,
                                                lambda: True, lambda _m: None, auto_begin_active=lambda: True)
        self.ex._osc = MagicMock()
        self.ex._receiver = MagicMock()
        self.calls = []
        self.outcomes = []
        for target, name, value in (
                (RoundDecision, "guidance_plush_id", lambda: self.PLUSH),
                (SharedState, "get_item_fetch", lambda: True),
                (SharedState, "get_item_begin_mode", lambda: False),
                (ItemFetch, "available", lambda: True),
                (self.ex, "_fetch_item", lambda seq, shop, item, plush=False, extra_stop=None:
                 self.calls.append((seq, shop, item, plush)) or (self.outcomes.pop(0) if self.outcomes else "failed"))):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.written = []
        p = patch.object(DebugLog, "write", self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def test_up_to_three_times_by_the_usual_path(self):
        self.ex.request_plush_retry("店の画面が見つからない")
        self.assertEqual(config.PLUSH_RETRY_MAX, 3)
        for _ in range(5):
            self.ex._retry_plush(9)
        self.assertEqual(self.calls, [(9, "Event", self.PLUSH, True)] * 3)
        self.assertTrue(any("入室時に取れなかった（店の画面が見つからない）" in m for m in self.written), self.written)

    def test_success_stops_retrying(self):
        self.ex.request_plush_retry("x")
        self.outcomes = ["ok"]
        self.ex._retry_plush(9)
        self.ex._retry_plush(9)
        self.assertEqual(len(self.calls), 1)

    def test_holding_another_item_stops_it(self):
        self.ex.request_plush_retry("x")
        self.st.held_item_id = 29
        self.ex._retry_plush(9)
        self.st.held_item_id = 0
        self.ex._retry_plush(9)
        self.assertEqual(self.calls, [])
        self.assertTrue(any("別のアイテム（id=29）を持っている → やり直さない" in m for m in self.written), self.written)

    def test_conditions_not_met_skip_without_using_a_try(self):
        self.ex.request_plush_retry("x")
        with patch.object(SharedState, "get_item_fetch", lambda: False):
            self.ex._retry_plush(9)
        self.assertEqual(self.calls, [])
        for _ in range(3):
            self.ex._retry_plush(9)
        self.assertEqual(len(self.calls), 3, "回数は減らしていない")

    def test_it_is_tried_after_the_begin_in_the_round_flow(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor.do_after_round)
        i = src.index("if (self._plush_retries and st.begin_done and not st.waiting_for_equip")
        self.assertLess(src.index("self._confirm_begin(round_seq)"), i)
        self.assertLess(i, src.index("# ── フェーズ2"))

    def test_without_a_request_nothing_happens(self):
        self.ex._retry_plush(9)
        self.assertEqual(self.calls, [])


class TestTheEntryAsksForARetry(unittest.TestCase):
    def _app(self, result, outcome="failed", receiver=True, running=True):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app._running = running
        app._entry_stop = threading.Event()
        action = MagicMock()
        action._receiver = MagicMock() if receiver else None
        action.fetch_guidance_plush.return_value = outcome
        monitor = MagicMock()
        monitor.cfg.hwnd = 0x40
        monitor._action = action
        app.monitors = [monitor]
        with patch.object(ToNEntry, "wait_verified", return_value=result):
            mainGUI.App._plush_after_entry(app, 4, 0x40, "log", 0)
        return action

    def test_a_failed_fetch_asks(self):
        for outcome in ("failed", "timeout", "skipped", "round", "front_lost"):
            action = self._app("verified", outcome)
            action.request_plush_retry.assert_called_once_with(outcome)

    def test_ok_or_stopped_does_not_ask(self):
        for outcome in ("ok", "stopped"):
            self._app("verified", outcome).request_plush_retry.assert_not_called()

    def test_no_verified_or_no_receiver_asks(self):
        self._app("timeout").request_plush_retry.assert_called_once_with("Begin の受理を確認できない")
        self._app("round").request_plush_retry.assert_called_once_with("ラウンドが始まった")
        self._app("verified", receiver=False).request_plush_retry.assert_called_once_with("受信が無い")

    def test_a_stopped_macro_cannot_ask(self):
        action = self._app("timeout", running=False)
        action.request_plush_retry.assert_not_called()
