"""FX: 完全放置モードで、Glorbo・EX の 3 体 DTM・EX の 3 体 Waldo（Plush あり）は、
3 クラの状態・「DTM/Waldo続行」・続行リストに関係なく続行する。
本物の入力・音・録画・窓は使わない（action・PlaySound・Recorder・SharedState・Thread は偽物）"""
from tests.support import *  # noqa: F401,F403

EX = LogParser.BLOODBATH_EX
DTM = LogMonitor.DTM_TERROR_ID
WALDO = config.WALDO_ID
OTHER = 5                                   # DTM でも Waldo でもないテラー
PLUSH = 70                                  # テスト中は item.json が空なので、Guidance Plush の番号を差し替える


class _Base(unittest.TestCase):
    def setUp(self):
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.continue_round_reset)
        self._start(patch.object(ConnectDB, "register_round"))
        self._start(patch.object(RoundDecision, "guidance_plush_id", return_value=PLUSH))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self.freeze = self._start(patch.object(SharedState, "continue_round_start"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, round_type, ids, hands_free=True, wins=config.OPEN_SPECIAL_ROUND_TARGET_WINS,
                 cancel_afk=False, plush=None, instance_type=config.INSTANCE_PRIVATE, item_id=0):
        """既定は 3 クラ済み・「DTM/Waldo続行」OFF・続行リスト空・アイテム無し"""
        SharedState.set_hands_free(hands_free)
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", cancel_afk=cancel_afk)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        st = monitor.st
        st.instance_type = instance_type
        st.in_round = True
        st.round_type = round_type
        st.terror_ids = list(ids)
        st.open_special_round_wins = wins
        st.item_id = item_id
        if plush is not None:
            st.held_item_id = PLUSH if plush else 12345
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._focus_for_freeze = MagicMock()
        return monitor

    def _started(self):
        return [(c.kwargs["target"].__func__.__name__, c.kwargs.get("args", ()))
                for c in self.thread.call_args_list if "target" in c.kwargs]

    def _assert_kept(self, monitor, label, round_type):
        st = monitor.st
        self.assertTrue(st.is_continue_round)
        self.assertFalse(st.open_special_continue, "音量は普通の続行")
        started = self._started()
        self.assertNotIn("do_skip", [n for n, _a in started])
        self.assertIn(("do_open_special_round_loop", (True,)), started, "glorbo=True の形")
        self.freeze.assert_called_once_with(st)
        self.play.assert_not_called()
        self.record.assert_not_called()
        line = f"判定: {label} / {round_type} 【プレイ(放置: 問答無用)】"
        self.assertTrue(any(line in m for m in monitor.logs), (line, monitor.logs))

    def _assert_skipped(self, monitor):
        self.assertFalse(monitor.st.is_continue_round)
        self.assertIn("do_skip", [n for n, _a in self._started()])
        self.assertNotIn("do_open_special_round_loop", [n for n, _a in self._started()])
        self.freeze.assert_not_called()


class TestHandsFreeKeep(_Base):
    def test_glorbo_continues_without_the_list_or_the_setting(self):
        monitor = self._monitor("Punished", [config.GLORBO_ID])
        self.assertEqual(monitor._plan("Punished", monitor.st.terror_ids, False),
                         ("hands_free_keep", "Glorbo"))
        monitor._decide("Punished")
        self._assert_kept(monitor, "Glorbo", "Punished")

    def test_ex_three_dtm_continues(self):
        monitor = self._monitor(EX, [DTM, DTM, DTM])
        monitor._decide(EX)
        self._assert_kept(monitor, "EX の DTM", EX)

    def test_ex_three_waldo_with_plush_continues(self):
        monitor = self._monitor(EX, [WALDO] * 3, plush=True)
        monitor._decide(EX)
        self._assert_kept(monitor, "EX の Waldo（Plush あり）", EX)

    def test_ex_three_waldo_with_unknown_plush_continues(self):
        """持っているか分からない（None）は今どおり続行"""
        monitor = self._monitor(EX, [WALDO] * 3)
        with patch.object(monitor, "_holds_plush", return_value=None):
            self.assertEqual(monitor._plan(EX, monitor.st.terror_ids, False)[0], "hands_free_keep")

    def test_ex_three_waldo_without_plush_suicides(self):
        monitor = self._monitor(EX, [WALDO] * 3, plush=False)
        self.assertEqual(monitor._plan(EX, monitor.st.terror_ids, False)[0], "hands_free")
        monitor._decide(EX)
        self._assert_skipped(monitor)

    def test_ex_three_other_terrors_suicides(self):
        monitor = self._monitor(EX, [OTHER] * 3)
        self.assertEqual(monitor._plan(EX, monitor.st.terror_ids, False)[0], "hands_free")
        monitor._decide(EX)
        self._assert_skipped(monitor)

    def test_ex_mixed_dtm_and_other_suicides(self):
        monitor = self._monitor(EX, [DTM, DTM, OTHER])
        self.assertEqual(monitor._plan(EX, monitor.st.terror_ids, False)[0], "hands_free")

    def test_ex_mixed_dtm_and_waldo_suicides(self):
        monitor = self._monitor(EX, [DTM, WALDO, WALDO], plush=True)
        self.assertEqual(monitor._plan(EX, monitor.st.terror_ids, False)[0], "hands_free")

    def test_plain_dtm_and_plain_waldo_stay_as_before_when_three_clears_are_done(self):
        for ids, plush in (([DTM], None), ([WALDO], True)):
            self.thread.reset_mock()
            monitor = self._monitor("Classic", ids, plush=plush, cancel_afk=True, item_id=5)
            self.assertEqual(monitor._plan("Classic", ids, False)[0], "hands_free", ids)
            monitor._decide("Classic")
            self._assert_skipped(monitor)

    def test_multi_terror_rounds_still_suicide(self):
        for rt in ("Double Trouble", "Bloodbath", "Midnight"):
            monitor = self._monitor(rt, [DTM, DTM])
            self.assertEqual(monitor._plan(rt, monitor.st.terror_ids, False)[0], "hands_free", rt)

    def test_plain_dtm_before_three_clears_takes_the_old_path(self):
        monitor = self._monitor("Classic", [DTM], wins=0, cancel_afk=True, item_id=5)
        plan = monitor._plan("Classic", [DTM], False)
        self.assertEqual(plan, ("list", True, True), "is_open_special_round_target が True の続行")
        monitor._decide("Classic")
        loops = [a for n, a in self._started() if n == "do_open_special_round_loop"]
        self.assertEqual(loops, [()], "glorbo=True ではない（3 勝で止まるループ）")
        self.assertTrue(monitor.st.open_special_continue)

    def test_not_hands_free_follows_the_list_only(self):
        # 放置モード OFF の private: Glorbo は続行リストがあるときだけ続行
        monitor = self._monitor("Punished", [config.GLORBO_ID], hands_free=False)
        plan = monitor._plan("Punished", monitor.st.terror_ids, False)
        self.assertEqual(plan[0], "list")
        self.assertFalse(plan[1])
        monitor._decide("Punished")
        self._assert_skipped(monitor)
        listed = self._monitor("Punished", [config.GLORBO_ID], hands_free=False)
        keep = {"Punished/パニッシュ": {config.GLORBO_ID}}
        with patch.object(listed, "_keep_on", return_value=keep):
            self.assertEqual(listed._plan("Punished", listed.st.terror_ids, False), ("glorbo",))

    def test_not_hands_free_ex_dtm_is_not_kept(self):
        monitor = self._monitor(EX, [DTM] * 3, hands_free=False)
        self.assertNotEqual(monitor._plan(EX, monitor.st.terror_ids, False)[0], "hands_free_keep")

    def test_public_window_is_not_operated(self):
        monitor = self._monitor("Punished", [config.GLORBO_ID], instance_type=config.INSTANCE_PUBLIC)
        self.assertEqual(monitor._plan("Punished", monitor.st.terror_ids, False), ("restricted",))

    def test_the_outcome_is_continue(self):
        self.assertEqual(LogMonitor.LogMonitor._outcome(("hands_free_keep", "Glorbo")), "continue")

    def test_the_loop_starts_once(self):
        monitor = self._monitor(EX, [DTM] * 3)
        monitor._decide(EX)
        monitor._decide(EX)
        loops = [a for n, a in self._started() if n == "do_open_special_round_loop"]
        self.assertEqual(loops, [(True,)])

    def test_following_utils_with_no_signal_continues_the_same_way(self):
        """Utils の判断を待って何も無かったとき（続行）も、同じ形で続行する"""
        monitor = self._monitor(EX, [DTM] * 3)
        plan = monitor._plan(EX, monitor.st.terror_ids, False)
        watch = MagicMock()
        watch.action_since.return_value = (None, 0.0)
        with patch.object(LogMonitor.UtilsWatch, "shared", return_value=watch),                 patch.object(monitor, "_round_still_active", return_value=True):
            monitor._wait_utils(EX, plan, monitor.st.round_seq, "src", 0.0, -10000.0)
        self._assert_kept(monitor, "EX の DTM", EX)


if __name__ == "__main__":
    unittest.main()
