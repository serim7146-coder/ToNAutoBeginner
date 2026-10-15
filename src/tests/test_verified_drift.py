"""EY: 定期 Verified の予定を外さない・店に行った後の押し直しは Begin の前に戻ってから（依頼者 2026-10-15
「窓3の Begin が止まった」）。09:23 窓3: 09:13:00.38 の定期を予定（+300）から 1.45 秒ずれて受理と取り違え、
09:23:00.52 も予定を 300 秒後だけで見て受理 → 店へ → 定期と分かって押し直したのは店の前 → あきらめたまま。
本物の OSC・入力・撮影・前面は使わない"""
from tests.support import *  # noqa: F401,F403
from datetime import datetime
from tests.test_equip_freeze_timing import _Case, _module_clock


def _t(hms: str) -> float:
    return datetime.strptime(f"2026-10-15 {hms}", "%Y-%m-%d %H:%M:%S.%f").timestamp()


class TestWindow3At0923(unittest.TestCase):
    def test_0913_and_0923_are_periodic(self):
        tr = VerifiedTracker.VerifiedTracker()
        tr.last_periodic = _t("09:07:58.93")
        tr.on_round_over(_t("09:12:46.000"))
        tr.on_round_end_verified(_t("09:12:59.000"))
        self.assertEqual(tr.on_verified(_t("09:13:00.38"), False), VerifiedTracker.PERIODIC,
                         "予定 09:12:58.93 から +1.45 秒")
        self.assertEqual(tr.last_periodic, _t("09:13:00.38"))
        tr.on_round_start(_t("09:13:12.000"))
        self.assertEqual(tr.on_verified(_t("09:18:00.400"), False), VerifiedTracker.IGNORE, "ラウンド中")
        self.assertEqual(tr.last_periodic, _t("09:13:00.38"), "ラウンド中の定期は位相を動かさない")
        tr.on_round_over(_t("09:22:46.976"))
        tr.on_round_end_verified(_t("09:23:00.217"))
        self.assertEqual(tr.on_verified(_t("09:23:00.52"), False), VerifiedTracker.PERIODIC,
                         "09:13:00.38 + 600 秒（k=2）から +0.14 秒")
        self.assertAlmostEqual(tr.schedule_due, _t("09:23:00.38"), places=3)

    def test_with_the_old_rule_both_were_taken_as_begin(self):
        """前の決まり（+300 だけ・±1.0）では 2 回とも受理（直す前の再現）"""
        with patch.object(config, "VERIFIED_PERIODIC_TOL_SEC", 1.0):
            tr = VerifiedTracker.VerifiedTracker()
            tr.last_periodic = _t("09:07:58.93")
            tr.on_round_end_verified(_t("09:12:59.000"))
            self.assertEqual(tr.on_verified(_t("09:13:00.38"), False), VerifiedTracker.BEGIN)


class TestTheSchedule(unittest.TestCase):
    def _tracker(self):
        tr = VerifiedTracker.VerifiedTracker()
        tr.last_periodic = 1000.0
        tr.on_round_end_verified(999.0)
        return tr

    def test_the_nearest_multiple(self):
        tr = self._tracker()
        self.assertEqual(tr.nearest_due(1100.0), 1300.0, "k は 1 から")
        self.assertEqual(tr.nearest_due(1449.0), 1300.0)
        self.assertEqual(tr.nearest_due(1451.0), 1600.0)
        self.assertEqual(tr.nearest_due(1000.0 + 300 * 7 + 2.0), 1000.0 + 300 * 7)
        self.assertIsNone(VerifiedTracker.VerifiedTracker().nearest_due(1100.0), "位相を知らない")
        self.assertIsNone(tr.nearest_due(None))

    def test_any_multiple_within_the_tolerance(self):
        for k in (1, 2, 3, 6, 12):
            for off, expected in ((-2.5, VerifiedTracker.PERIODIC), (2.4, VerifiedTracker.PERIODIC),
                                  (-2.6, VerifiedTracker.BEGIN), (2.6, VerifiedTracker.BEGIN),
                                  (150.0, VerifiedTracker.BEGIN)):
                tr = self._tracker()
                self.assertEqual(tr.on_verified(1000.0 + 300 * k + off, False), expected, (k, off))

    def test_the_round_over_rule_is_as_before(self):
        """RoundOver 直後の遅れた定期は今どおり（+300 がラウンド中に来ていたら定期）"""
        tr = self._tracker()
        tr.on_round_start(1250.0)
        tr.on_round_over(1340.0)
        self.assertEqual(tr.on_verified(1340.5, False), VerifiedTracker.PERIODIC)
        tr = self._tracker()
        tr.on_round_start(1050.0)
        tr.on_round_over(1200.0)
        self.assertEqual(tr.on_verified(1200.5, False), VerifiedTracker.IGNORE, "予定はまだ")

    def test_the_value(self):
        self.assertEqual(config.VERIFIED_PERIODIC_TOL_SEC, 2.5)


class TestPressingAgainAfterTheShop(_Case):
    """「定期でした → 押し直します」: そのラウンドに店へ行っていたら、リスポーン → Begin 前の移動 → 押す"""

    def _again(self, fetched=True, faced=True, move_done=True):
        ex, st = self._executor(fetch=True)
        st.begin_done = False
        st.begin_move_done = move_done
        ex._fetched_round = st.round_seq if fetched else -1
        order = []

        def respawn(seq, label):
            order.append(("respawn", label, ex._respawn_lobby))
            return faced
        ex._respawn_and_face = respawn
        ex._begin_move = lambda: order.append("move")
        ex._press_begin = lambda again=False, click_only=False: order.append("press") or True
        ex._confirm_begin = lambda seq: order.append("confirm")
        ex._retry_after_giving_up = MagicMock()
        ex.do_begin_again(st.round_seq)
        return ex, st, order

    def test_after_the_shop_it_respawns_and_moves_first(self):
        ex, st, order = self._again()
        self.assertEqual(order, [("respawn", "店に行った後", True), "move", "press", "confirm"])
        self.assertFalse(ex._respawn_lobby)
        self.assertEqual(ex._fetched_round, -1, "1 回だけ（次の押し直しでまたリスポーンしない）")
        self.assertIn("Begin の押し直し: 店に行った後なので、リスポーンして Begin の前へ戻ります", self.logs)
        self.assertTrue(any("Begin の押し直しを開始（店に行った後: リスポーンから）" in m for m in self.written))

    def test_without_the_shop_it_is_as_before(self):
        _ex, _st, order = self._again(fetched=False)
        self.assertEqual(order, ["press", "confirm"], "移動済み")
        _ex, _st, order = self._again(fetched=False, move_done=False)
        self.assertEqual(order, ["move", "press", "confirm"], "移動から")

    def test_a_failed_respawn_does_not_move(self):
        _ex, _st, order = self._again(faced=False)
        self.assertEqual(order, [("respawn", "店に行った後", True), "press", "confirm"],
                         "店の前から動くと別の所へ行く。押す所の位置合わせ・探すに任せる")

    def test_begin_passed_during_the_respawn(self):
        ex, st = self._executor(fetch=True)
        st.begin_done = False
        ex._fetched_round = st.round_seq
        pressed = []

        def respawn(seq, label):
            st.begin_done = True
            return True
        ex._respawn_and_face = respawn
        ex._press_begin = lambda again=False, click_only=False: pressed.append(1) or True
        ex._begin_move = lambda: pressed.append("move")
        ex.do_begin_again(st.round_seq)
        self.assertEqual(pressed, [], "動かない・押さない")

    def test_the_fetch_remembers_the_round(self):
        ex, st = self._executor(fetch=True)
        st.round_seq = 12
        fetcher = MagicMock()
        fetcher.move_to_shop.return_value = False
        with patch.object(ActionExecutor.ItemFetch, "Fetcher", return_value=fetcher):
            ex._fetch_item(12, "Survival", 29)
        self.assertEqual(ex._fetched_round, 12)


class TestTheLobbyRespawn(_Case):
    def test_the_lobby_checks(self):
        ex, st = self._executor()
        st.in_round = False
        st.begin_done = False
        ex._respawn_lobby = True
        self.assertTrue(ex._run_still(7))
        self.assertTrue(ex._run_after_respawn(7))
        st.begin_done = True
        self.assertFalse(ex._run_still(7), "Begin が通ったらやめる")
        st.begin_done = False
        st.in_round = True
        self.assertFalse(ex._run_after_respawn(7), "ラウンドが始まったらやめる")
        st.in_round = False
        self.assertFalse(ex._run_still(8), "次のラウンド")
        ex._respawn_lobby = False
        self.assertFalse(ex._run_still(7), "Run のときはラウンドの中だけ")

    def _respawn(self, lobby):
        ex, st = self._executor()
        st.in_round = not lobby
        st.begin_done = False
        ex._respawn_lobby = lobby
        ex._respawn_when_free = lambda seq: None
        ex._receiver = None
        ex._face_the_stairs = lambda seq: 0
        ex._fix_depth = lambda seq: None
        ex._take_pos_run = MagicMock()
        return ex, ex._respawn_and_face(7, "店に行った後" if lobby else "Run")

    def test_the_same_moves_as_after_run_but_no_run_shot(self):
        ex, faced = self._respawn(lobby=True)
        self.assertTrue(faced)
        sent = [c.args[0] for c in ex._osc.press.call_args_list]
        self.assertEqual(sent, ["/input/MoveBackward", "/input/LookLeft"], "Run の後と同じ")
        ex._take_pos_run.assert_not_called()
        self.assertIn("店に行った後: リスポーンして正面を向きました", self.logs)
        self.assertTrue(ex._run_respawn_idle.is_set())
        ex, faced = self._respawn(lobby=False)
        self.assertTrue(faced)
        ex._take_pos_run.assert_called_once()

    def test_a_failed_respawn_says_so(self):
        ex, st = self._executor()
        ex._respawn_lobby = True
        ex._respawn_when_free = lambda seq: "リスポーンの行が来ない"
        self.assertFalse(ex._respawn_and_face(7, "店に行った後"))
        self.assertIn("店に行った後: リスポーンできませんでした（リスポーンの行が来ない）", self.logs)


class TestNotGivingUpAfterPressingAgain(_Case):
    def test_giving_up_in_the_again_flow_tries_once_more_30_s_later(self):
        ex, st = self._executor(fetch=False)
        st.begin_done = False
        ex._fetched_round = -1
        st.begin_move_done = True
        presses = []

        def press(again=False, click_only=False):
            presses.append(self.now[0])
            return True

        def confirm(seq):
            ex._begin_gave_up_round = seq          # 3 回押して受理されなかった
        ex._press_begin = press
        ex._confirm_begin = confirm
        del ex._retry_after_giving_up               # 本物（30 秒・1 ラウンドに 1 回）
        ex._should_retry_begin = lambda seq: True
        ex.do_begin_again(st.round_seq)
        self.assertEqual(len(presses), 2, "押し直し → あきらめた 30 秒後に 1 回だけ")
        self.assertAlmostEqual(presses[1] - presses[0], config.BEGIN_GIVE_UP_RETRY_SEC, delta=1.0)
        self.assertIn(f"Begin をもう一度押します（あきらめてから {config.BEGIN_GIVE_UP_RETRY_SEC:.0f} 秒）", self.logs)

    def test_not_given_up_no_retry(self):
        ex, st = self._executor(fetch=False)
        st.begin_done = False
        st.begin_move_done = True
        ex._confirm_begin = lambda seq: None
        ex._retry_after_giving_up = MagicMock()
        ex.do_begin_again(st.round_seq)
        ex._retry_after_giving_up.assert_not_called()


class TestTheEquipWaitLoop(_Case):
    """受理と見た Verified が定期だった → 装備待ちのループで待ち続けず、Begin が通ってから装備を待つ"""

    def _run(self, script):
        ex, st = self._executor(fetch=True, accept_on=1)
        ex._fetch_item = lambda *a, **k: "failed"
        clock, now = _module_clock()
        start = now[0]
        real_sleep = clock.sleep

        def sleep(sec):
            real_sleep(sec)
            for at, change in list(script):
                if now[0] - start >= at:
                    change(st)
                    script.remove((at, change))
        clock.sleep = sleep
        with patch.object(ActionExecutor, "time", clock):
            ex.do_after_round()
        return ex, st

    def test_it_waits_for_begin_then_for_the_item(self):
        def equip(st):
            st.waiting_for_equip = False
            st.item_id = 29
        ex, st = self._run([(3, lambda st: setattr(st, "begin_done", False)),
                            (10, lambda st: setattr(st, "begin_done", True)),
                            (15, equip)])
        waits = [m for m in self.logs if m in ("アイテム装備を待っています… （装備すると自動再開）",
                                              "Begin が通っていません → Begin が通ってから装備を待ちます")]
        self.assertEqual(waits, ["アイテム装備を待っています… （装備すると自動再開）",
                                 "Begin が通っていません → Begin が通ってから装備を待ちます",
                                 "アイテム装備を待っています… （装備すると自動再開）"])
        self.assertIn("✅ アイテム装備確認 → 続行", self.logs)

    def test_a_round_start_alone_ends_the_wait(self):
        """ラウンド開始で装備待ちが外れなくても、待ち続けない（ループが回り続けない）"""
        self._run([(3, lambda st: setattr(st, "begin_done", False)), (8, lambda st: setattr(st, "in_round", True))])
        self.assertEqual(self.logs.count("Begin が通っていません → Begin が通ってから装備を待ちます"), 1)

    def test_a_round_start_while_waiting_for_begin(self):
        def start(st):
            st.in_round = True
            st.waiting_for_equip = False
        self._run([(3, lambda st: setattr(st, "begin_done", False)), (8, start)])
        self.assertIn("Begin が通っていません → Begin が通ってから装備を待ちます", self.logs)
        self.assertIn("ラウンドが始まったので装備待ちをやめます（アイテムは未回収のまま）", self.logs)


if __name__ == "__main__":
    unittest.main()
