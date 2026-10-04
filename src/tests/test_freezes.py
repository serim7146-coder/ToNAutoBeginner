"""全窓フリーズ（装備待ち・続行・速度検知・突入）とアイテムロスト"""
from tests.support import *  # noqa: F401,F403




class TestRoundFreezeFocus(unittest.TestCase):
    """ラウンド突入で全窓を止めた窓も前面化する（続行ラウンドと同じ作法）"""

    EVENTS = ("EQUIP_WAIT_EVENT", "CONTINUE_ROUND_EVENT",
              "SPEED_FREEZE_EVENT", "ROUND_FREEZE_EVENT")
    HWND = 888

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        SharedState.round_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.speed_freeze_reset()
        SharedState.equip_freeze_reset()
        for name in self.EVENTS:
            getattr(SharedState, name).set()
        self._rounds = patch.object(SharedState, "get_freeze_rounds",
                                   return_value={"Midnight"})
        self._rounds.start()
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self.addCleanup(self._rounds.stop)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.round_freeze_reset)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.speed_freeze_reset)
        self.addCleanup(SharedState.equip_freeze_reset)
        self.addCleanup(lambda: [getattr(SharedState, n).set()
                                 for n in self.EVENTS])

    def _monitor(self):
        cfg = WindowConfig(hwnd=self.HWND, do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        return monitor

    @staticmethod
    def _run_now(target=None, args=(), daemon=None, **_kw):
        if target is not None:
            target(*args)
        return MagicMock()

    def _enter(self, monitor, round_type="Midnight"):
        """そのラウンドへ突入する。前面化のスレッドは同期で走らせる"""
        line = ("2026.09.28 00:10:00 Debug      -  This round is taking place "
                f"at Facility (12) and the round type is {round_type}")
        with patch.object(WindowOperator, "focus_window",
                          return_value=True) as focus, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(LogMonitor.threading, "Thread",
                          side_effect=self._run_now):
            monitor._process(line)
        return focus

    # ── 1. 前面化する ─────────────────────────
    def test_entering_a_freeze_round_focuses_this_window(self):
        monitor = self._monitor()

        focus = self._enter(monitor)

        self.assertTrue(monitor.st.round_freeze_held, "フリーズは張る")
        focus.assert_called_once_with(self.HWND)

    def test_the_log_says_which_freeze(self):
        monitor = self._monitor()
        logs = []
        monitor.logger = logs.append

        self._enter(monitor)

        self.assertTrue(any("この窓を前面化しました（ラウンド突入フリーズ）" in m
                            for m in logs), logs)

    # ── 2. ほかの窓がフリーズ中なら前面化しない ────────────
    def test_another_windows_freeze_blocks_it(self):
        for name in self.EVENTS:
            monitor = self._monitor()
            getattr(SharedState, name).clear()

            focus = self._enter(monitor)

            self.assertFalse(focus.called, name)
            self.assertTrue(monitor.st.round_freeze_held, f"{name}: 張るのは張る")
            getattr(SharedState, name).set()
            SharedState.round_freeze_reset()
            monitor.st.round_freeze_held = False

    # ── 3. 放置モード ────────────────────────
    def test_hands_free_does_not_focus(self):
        monitor = self._monitor()
        SharedState.set_hands_free(True)

        focus = self._enter(monitor)

        self.assertFalse(focus.called)
        self.assertFalse(monitor.st.round_freeze_held, "放置中はフリーズも張らない")

    # ── 4. 対象外の種別では前面化しない ──────────────────
    def test_a_round_that_is_not_frozen_does_not_focus(self):
        """フリーズを張らないラウンドで前面を奪うと迷惑なだけ"""
        monitor = self._monitor()

        focus = self._enter(monitor, round_type="Classic")

        self.assertFalse(monitor.st.round_freeze_held)
        self.assertFalse(focus.called)

    # ── 5. 失敗してもフリーズは続く ──────────────────
    def test_a_failed_focus_keeps_the_freeze(self):
        monitor = self._monitor()
        logs = []
        monitor.logger = logs.append
        line = ("2026.09.28 00:10:00 Debug      -  This round is taking place "
                "at Facility (12) and the round type is Midnight")

        with patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(LogMonitor.threading, "Thread",
                          side_effect=self._run_now):
            monitor._process(line)

        self.assertTrue(monitor.st.round_freeze_held)
        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertTrue(any("⚠ 前面化に失敗（ラウンド突入フリーズは継続）" in m
                            for m in logs), logs)

    # ── 6. 数えるのは張る前 ────────────────────
    def test_the_count_is_read_before_it_freezes(self):
        monitor = self._monitor()
        seen = []

        def focus(hwnd):
            seen.append(SharedState.get_round_freeze_count())
            return True

        line = ("2026.09.28 00:10:00 Debug      -  This round is taking place "
                "at Facility (12) and the round type is Midnight")
        with patch.object(WindowOperator, "focus_window", side_effect=focus), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(LogMonitor.threading, "Thread",
                          side_effect=self._run_now):
            monitor._process(line)

        self.assertEqual(seen, [0], "数える前に見ていること")

    def test_the_source_focuses_before_it_freezes(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        block = src[src.index("if st.round_type in SharedState.get_freeze_rounds()"):]
        block = block[:block.index("\n            # ", 10)]

        self.assertLess(block.index('_focus_for_freeze("ラウンド突入フリーズ")'),
                        block.index("SharedState.round_freeze_start(st)"))

    # ── 7. 共通化しても続行側は壊れていない ─────────────────
    def test_the_two_labels_use_the_same_helper(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")

        self.assertEqual(src.count('_focus_for_freeze("続行ラウンド")'), 3,
                         "続行判定の3か所（Glorbo の続行）")
        self.assertEqual(src.count('_focus_for_freeze("ラウンド突入フリーズ")'), 1)
        self.assertIn('self._log(f"この窓を前面化しました（{label}）")', src)

    def test_the_continue_label_is_unchanged(self):
        monitor = self._monitor()
        logs = []
        monitor.logger = logs.append

        with patch.object(WindowOperator, "focus_window", return_value=True):
            monitor._focus_this_window_for("続行ラウンド")

        self.assertTrue(any("この窓を前面化しました（続行ラウンド）" in m
                            for m in logs), logs)   # 窓番号の接頭辞が付く




class TestContinueFreezeReleaseOnRoundOver(unittest.TestCase):
    """続行ラウンドを生き残ったときも、猶予のあとフリーズを解除する。

    2026-09-27 23:26 の実機の不具合: 解除の予約が `You died.` のときだけで、
    生き残った場合は入らなかった。そのため通常経路で解除されず
    `Verified Round End` の保険まで残り、他窓が RoundOver から13〜14秒も
    余計に止まっていた。続行ラウンドは遊んでいる＝生き残ることが多いので、
    ほぼ毎回これに当たっていた。
    """

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_hands_free, False)

    def _monitor(self, auto_begin=False):
        cfg = WindowConfig(hwnd=9, do_skip=True, auto_begin=auto_begin,
                           voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._running = True
        return monitor

    def _continuing(self):
        """この窓が続行フリーズを張っている状態"""
        monitor = self._monitor()
        monitor.st.is_continue_round = True
        SharedState.continue_round_start(monitor.st)
        self.assertEqual(SharedState.get_continue_round_count(), 1)
        return monitor

    @staticmethod
    def _booked(monitor, line):
        """その行で立つデーモンの名前を返す"""
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            monitor._process(line)
        return [c.kwargs["target"].__func__.__name__
                for c in thread.call_args_list if "target" in c.kwargs]

    ROUND_OVER = "2026.09.27 23:26:17 Debug      -  RoundOver"
    LIVED = "2026.09.27 23:26:17 Debug      -  Lived in round."
    DIED = "2026.09.27 23:26:12 Debug      -  You died."

    # ── 1. 今回の不具合 ──────────────────────
    def test_surviving_books_the_release_at_round_over(self):
        monitor = self._continuing()

        started = self._booked(monitor, self.ROUND_OVER)

        self.assertIn("_release_continue_freeze_after_delay", started, started)

    def test_the_release_actually_lifts_the_freeze(self):
        """予約が走ると、Verified Round End を待たずに解除される"""
        monitor = self._continuing()

        with patch.object(LogMonitor.time, "sleep") as sleep:
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)

        sleep.assert_called_once_with(config.CONTINUE_FREEZE_RELEASE_DELAY_SEC)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())
        self.assertFalse(monitor.st.is_continue_round)

    def test_the_lived_line_is_not_needed(self):
        """RoundOver を起点にしたので、Lived in round. が無くても効く"""
        monitor = self._continuing()

        started = self._booked(monitor, self.ROUND_OVER)

        self.assertIn("_release_continue_freeze_after_delay", started)
        self.assertFalse(monitor.st.lived_this_round, "生存の行は来ていない")

    # ── 2. 死亡のときはこれまでどおり ───────────────────
    def test_dying_still_books_from_the_death(self):
        monitor = self._continuing()

        started = self._booked(monitor, self.DIED)

        self.assertIn("_release_continue_freeze_after_delay", started, started)

    def test_a_double_booking_releases_only_once(self):
        """死亡と RoundOver の両方で予約されても、解除は1回きり"""
        monitor = self._continuing()
        other = WindowState()
        SharedState.continue_round_start(other)     # 別の窓も張っている
        self.assertEqual(SharedState.get_continue_round_count(), 2)

        with patch.object(LogMonitor.time, "sleep"):
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)

        self.assertEqual(SharedState.get_continue_round_count(), 1,
                         "自分のぶんだけ引く")
        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set(),
                         "他窓のフリーズは残る")

    # ── 3. DTM/Waldo は予約しない ───────────────────
    def test_a_dtm_window_books_nothing(self):
        monitor = self._monitor()
        monitor.st.is_continue_round = True        # 張らずに続行ラウンド

        started = self._booked(monitor, self.ROUND_OVER)

        self.assertNotIn("_release_continue_freeze_after_delay", started, started)

    def test_a_plain_round_books_nothing(self):
        monitor = self._monitor()

        started = self._booked(monitor, self.ROUND_OVER)

        self.assertNotIn("_release_continue_freeze_after_delay", started, started)

    # ── 4. 猶予中に次のラウンドが始まったら ─────────────────
    def test_a_new_round_during_the_delay_cancels_it(self):
        monitor = self._continuing()

        def bump(_sec):
            monitor.st.round_seq += 1

        with patch.object(LogMonitor.time, "sleep", side_effect=bump):
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)

        self.assertEqual(SharedState.get_continue_round_count(), 1, "解除しない")
        self.assertTrue(monitor.st.is_continue_round)

    def test_a_stop_during_the_delay_cancels_it(self):
        monitor = self._continuing()

        def stop(_sec):
            monitor._running = False

        with patch.object(LogMonitor.time, "sleep", side_effect=stop):
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)

        self.assertEqual(SharedState.get_continue_round_count(), 1)

    # ── 5. 保険は残っている ────────────────────
    def test_the_verified_round_end_safety_net_remains(self):
        """通常経路が両方とも取りこぼしたときの最後の砦"""
        monitor = self._continuing()
        logs = []
        monitor.logger = logs.append

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            monitor._process("2026.09.27 23:26:17 Debug      -  RoundOver")
            monitor._process("2026.09.27 23:26:31 Debug      -  Verified Round End")

        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(any("保険" in m for m in logs), logs)

    def test_the_booking_looks_at_the_hold_not_the_round(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        over = src[src.index("    def _on_round_over(self, event):"):]
        over = over[:over.index("\n    def ", 10)]

        self.assertIn("if st.continue_freeze_held:", over)
        self.assertIn("_release_continue_freeze_after_delay", over)



class TestContinueFreezeIsPerWindow(unittest.TestCase):
    """続行フリーズは窓ごとの保持。足していない窓が引かないこと。

    2026-09-27 21:16 の実機の不具合: 窓2 で Waldo をやっていて、それが終わった
    瞬間に、別の窓が続行ラウンド中なのに全窓のフリーズが解除されて Begin が
    走った。DTM/Waldo の窓は is_continue_round=True でも
    continue_round_start() を呼ばない（他窓を止めない仕様）のに、終了側が
    is_continue_round だけを見て無条件に引いていたため。
    """

    CLASSIC_KEY = "Classic/クラシック"
    DTM_KEY = "Double Trouble/ダブルトラブル"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_hands_free, False)

    def _monitor(self, keep_on=None, idx=1):
        cfg = WindowConfig(hwnd=100 + idx, do_skip=True,
                           voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=idx)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._running = True
        return monitor

    # ── 2〜4. API そのもの ─────────────────────
    def test_a_window_that_did_not_freeze_does_not_release(self):
        holder = WindowState()
        SharedState.continue_round_start(holder)
        bystander = WindowState()
        bystander.is_continue_round = True      # DTM/Waldo はこれだけ True

        SharedState.continue_round_end(bystander)

        self.assertEqual(SharedState.get_continue_round_count(), 1)
        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set(),
                         "他窓のフリーズを壊さない")

    def test_starting_twice_counts_once(self):
        st = WindowState()

        SharedState.continue_round_start(st)
        SharedState.continue_round_start(st)

        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_the_holder_can_release(self):
        st = WindowState()
        SharedState.continue_round_start(st)

        SharedState.continue_round_end(st)

        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())
        self.assertFalse(st.continue_freeze_held)

    def test_releasing_twice_does_not_go_negative(self):
        a, b = WindowState(), WindowState()
        SharedState.continue_round_start(a)
        SharedState.continue_round_start(b)

        SharedState.continue_round_end(a)
        SharedState.continue_round_end(a)        # 2回目は何もしない

        self.assertEqual(SharedState.get_continue_round_count(), 1)
        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set())

    def test_a_release_after_a_reset_does_not_go_negative(self):
        """強制リセットは窓ごとのフラグを消さない（他の3つと同じ）。

        止めたあとに古い WindowState が解除しても、カウントが負に回り込んで
        「0 ではない」ままになってしまわないこと
        """
        stale = WindowState()
        SharedState.continue_round_start(stale)
        SharedState.continue_round_reset()

        SharedState.continue_round_end(stale)

        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())

    def test_the_flag_is_separate_from_is_continue_round(self):
        st = WindowState()
        st.is_continue_round = True

        self.assertFalse(st.continue_freeze_held,
                         "DTM/Waldo は続行ラウンドだがフリーズは張らない")

    def test_reset_clears_everything(self):
        st = WindowState()
        SharedState.continue_round_start(st)

        SharedState.continue_round_reset()

        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())

    # ── 1・6. 実機の不具合そのもの ───────────────────
    def test_a_finished_dtm_round_leaves_another_windows_freeze_alone(self):
        """窓Aが続行フリーズ中に、窓B（Waldo）のラウンドが終わっても解除しない"""
        window_a = self._monitor({self.CLASSIC_KEY: {42}}, idx=1)
        window_a.st.terror_ids = [42]
        with patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_continue_start"), \
             patch.object(LogMonitor.threading, "Thread"):
            window_a._decide_with_keep_on_set("Classic")
        self.assertTrue(window_a.st.continue_freeze_held, "窓Aが張っている")
        self.assertEqual(SharedState.get_continue_round_count(), 1)

        window_b = self._monitor(idx=2)
        window_b.st.round_type = "Double Trouble"
        window_b.st.is_continue_round = True     # DTM/Waldo の窓
        window_b.st.is_open_special_round_round = True

        with patch.object(PlaySound, "play_sound"), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(Recorder, "on_round_over"):
            window_b._process("2026.09.27 21:16:48 Debug      -  "
                              "Verified Round End")
            window_b._process("2026.09.27 21:16:48 Debug      -  RoundOver")

        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set(),
                         "窓Aのフリーズが残っていること")
        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_a_dtm_window_never_freezes_the_others(self):
        """DTM/Waldo は他窓を止めない（この仕様は正しい。変えない）"""
        monitor = self._monitor({self.DTM_KEY: {42}}, idx=3)
        monitor.st.round_type = "Double Trouble"
        monitor.st.terror_ids = [42]

        with patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_continue_start"), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(monitor, "_list_plan",
                          return_value=("list", True, True)):
            monitor._decide_with_keep_on_set("Double Trouble")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertFalse(monitor.st.continue_freeze_held, "他窓は止めない")
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())

    def test_that_dtm_window_ending_releases_nothing(self):
        holder = WindowState()
        SharedState.continue_round_start(holder)
        monitor = self._monitor(idx=4)
        monitor.st.is_continue_round = True     # 張っていない DTM/Waldo の窓

        with patch.object(PlaySound, "play_sound"), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(Recorder, "on_round_over"):
            monitor._process("2026.09.27 21:16:48 Debug      -  RoundOver")

        self.assertEqual(SharedState.get_continue_round_count(), 1)
        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set())

    # ── 5. 解除の全経路が st を渡していること ────────────
    def test_every_release_passes_the_window(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")

        self.assertNotIn("SharedState.continue_round_end()", src,
                         "引数なしの解除が残っていないこと")
        self.assertNotIn("SharedState.continue_round_start()", src)
        self.assertEqual(src.count("SharedState.continue_round_end(st)"), 6,
                         "解除は6か所")
        self.assertEqual(src.count("SharedState.continue_round_start(st)"), 3,
                         "開始は3か所（Glorbo の続行）")

    def test_the_gui_reset_takes_no_window(self):
        """止める・始めるときは窓を問わず全部解く（begin_run が4種とも reset する）"""
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")

        self.assertEqual(src.count("SharedState.begin_run()"), 2, "停止と開始")
        st = WindowState()
        SharedState.continue_round_start(st)
        SharedState.begin_run()
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())
        self.assertEqual(SharedState.get_continue_round_count(), 0)




class TestOnlyOwnFreezeIsExempt(unittest.TestCase):
    """免除するのは自分が張ったフリーズだけ。

    以前は「自分が何か1つ張っていれば他窓の分も全部無視」だったので、
    アイテムロストした窓が他窓の続行ラウンド中でも Begin を押しに行き、
    （その窓を遊んでいて VRChat がアクティブなので）前面化＋クリックへ落ちて
    前面を奪っていた。
    """

    EVENTS = ("EQUIP_WAIT_EVENT", "CONTINUE_ROUND_EVENT",
              "SPEED_FREEZE_EVENT", "ROUND_FREEZE_EVENT")

    def setUp(self):
        for name in self.EVENTS:
            getattr(SharedState, name).set()
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.speed_freeze_reset()
        SharedState.round_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.speed_freeze_reset)
        self.addCleanup(SharedState.round_freeze_reset)
        self.addCleanup(lambda: [getattr(SharedState, n).set()
                                 for n in self.EVENTS])

    def _executor(self, logs=None):
        cfg = WindowConfig(hwnd=321, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=0)
        return ActionExecutor.ActionExecutor(
            cfg, st, lambda: True,
            logs.append if logs is not None else (lambda _m: None)), st

    @staticmethod
    def _other_window():
        return WindowState(instance_type=config.INSTANCE_PRIVATE)

    @staticmethod
    def _lose_item(st):
        """実機のロストと同じ形にする。waiting_for_equip も立つ。

        ここを立てないと、昔の一括免除（waiting_for_equip を見ていた）に
        戻す変異を検出できない
        """
        st.waiting_for_equip = True
        SharedState.equip_freeze_start(st)

    # ── 1. 今回の症状 ────────────────────────
    def test_a_lost_item_window_waits_for_another_windows_continue(self):
        ex, st = self._executor()
        self._lose_item(st)          # 自分はロストで張っている
        SharedState.continue_round_start(self._other_window())   # 他窓が続行

        eq_ok, con_ok, _spd, _rnd = ex._freezes_ok()

        self.assertTrue(eq_ok, "自分の装備待ちは免除")
        self.assertFalse(con_ok, "他窓の続行は待つ")

    def test_that_window_does_not_press(self):
        """待ちに入るので、フォーカスもカーソルも取らない"""
        ex, st = self._executor()
        self._lose_item(st)
        SharedState.continue_round_start(self._other_window())
        running = [True]
        ex._is_running = lambda: running.pop() if running else False

        with patch.object(WindowOperator, "focus_window") as focus, \
             patch.object(WindowOperator, "user32", FakeUser32()) as user32, \
             patch.object(SharedState.CONTINUE_ROUND_EVENT, "wait"):
            self.assertFalse(ex._wait_other_windows())

        focus.assert_not_called()
        self.assertEqual(user32.moves, [])

    def test_it_presses_once_the_continue_ends(self):
        ex, st = self._executor()
        self._lose_item(st)
        other = self._other_window()
        SharedState.continue_round_start(other)
        self.assertFalse(ex._freezes_ok()[1])

        SharedState.continue_round_end(other)

        self.assertTrue(all(ex._freezes_ok()))
        self.assertTrue(ex._wait_other_windows())

    # ── 3〜4. 装備待ちは無条件で免除 ───────────────────
    def test_its_own_equip_wait_alone_still_presses(self):
        ex, st = self._executor()
        self._lose_item(st)

        self.assertTrue(ex._wait_other_windows())

    def test_two_windows_both_waiting_to_equip_can_both_press(self):
        """複数窓が同時にロストしても互いに待たない（デッドロック回避）"""
        ex_a, st_a = self._executor()
        ex_b, st_b = self._executor()
        self._lose_item(st_a)
        self._lose_item(st_b)

        self.assertEqual(SharedState.get_equip_freeze_count(), 2)
        self.assertTrue(ex_a._wait_other_windows())
        self.assertTrue(ex_b._wait_other_windows())

    def test_another_windows_equip_wait_alone_is_waited_for(self):
        ex, _st = self._executor()
        SharedState.equip_freeze_start(self._other_window())

        self.assertFalse(ex._freezes_ok()[0], "自分は張っていないので待つ")

    # ── 5〜6. 続行・速度検知・突入は「自分だけなら免除」 ─────────
    def _sole_case(self, start, end, index, held_attr):
        ex, st = self._executor()
        start(st)
        self.assertTrue(ex._freezes_ok()[index], "自分だけなら進む")
        self.assertTrue(getattr(st, held_attr))

        start(self._other_window())
        self.assertFalse(ex._freezes_ok()[index], "他窓も張っていれば待つ")

        end(st)

    def test_the_continue_freeze_is_sole_only(self):
        self._sole_case(SharedState.continue_round_start,
                        SharedState.continue_round_end, 1, "continue_freeze_held")

    def test_the_speed_freeze_is_sole_only(self):
        self._sole_case(SharedState.speed_freeze_start,
                        SharedState.speed_freeze_end, 2, "speed_freeze_held")

    def test_the_round_freeze_is_sole_only(self):
        self._sole_case(SharedState.round_freeze_start,
                        SharedState.round_freeze_end, 3, "round_freeze_held")

    def test_nothing_held_and_nothing_frozen_is_fine(self):
        ex, _st = self._executor()

        self.assertTrue(all(ex._freezes_ok()))

    def test_the_rule_itself(self):
        ok = ActionExecutor.ActionExecutor._freeze_ok
        clear, frozen = threading.Event(), threading.Event()
        clear.set()

        self.assertTrue(ok(clear, False, 0, True), "誰も張っていない")
        self.assertFalse(ok(frozen, False, 1, True), "他窓の分は待つ")
        self.assertTrue(ok(frozen, True, 1, True), "自分だけなら進む")
        self.assertFalse(ok(frozen, True, 2, True), "他窓も居れば待つ")
        self.assertTrue(ok(frozen, True, 2, False), "装備待ちは他窓が居ても進む")

    # ── 7. _begin_precheck も同じ規則 ──────────────
    def test_the_precheck_stops_for_another_windows_equip_wait(self):
        """以前は装備待ちを見ていなかった"""
        logs = []
        ex, _st = self._executor(logs)
        SharedState.equip_freeze_start(self._other_window())

        self.assertFalse(ex._begin_precheck())
        self.assertTrue(any("他窓の装備待ち" in m for m in logs), logs)

    def test_the_precheck_stops_for_another_windows_continue(self):
        logs = []
        ex, st = self._executor(logs)
        self._lose_item(st)      # 自分はロスト中でも
        SharedState.continue_round_start(self._other_window())

        self.assertFalse(ex._begin_precheck())
        self.assertTrue(any("他窓のフリーズ" in m for m in logs), logs)

    def test_the_precheck_passes_when_only_this_window_holds(self):
        ex, st = self._executor()
        self._lose_item(st)
        SharedState.continue_round_start(st)

        self.assertTrue(ex._begin_precheck())

    def test_the_precheck_skips_the_freezes_when_asked(self):
        """移動の前は確認を省く（移動はフリーズ中でも行う）"""
        ex, _st = self._executor()
        SharedState.continue_round_start(self._other_window())

        self.assertTrue(ex._begin_precheck(check_freeze=False))




class TestNothingFrozen(unittest.TestCase):
    """前面化してよいかは「どのフリーズも張られていない」で決める。

    フリーズが張られている間、前面はそれを張った窓のもの。種別を問わず譲る
    （8 Pages の検知でフリーズ中に、続行になった窓が前面を奪う例が実機で出た）。
    """

    EVENTS = ("EQUIP_WAIT_EVENT", "CONTINUE_ROUND_EVENT",
              "SPEED_FREEZE_EVENT", "ROUND_FREEZE_EVENT")

    def setUp(self):
        for name in self.EVENTS:
            getattr(SharedState, name).set()
        self.addCleanup(lambda: [getattr(SharedState, n).set()
                                 for n in self.EVENTS])

    def test_all_clear_is_true(self):
        self.assertTrue(SharedState.nothing_frozen())

    def test_any_one_frozen_is_false(self):
        for name in self.EVENTS:
            getattr(SharedState, name).clear()
            self.assertFalse(SharedState.nothing_frozen(), name)
            getattr(SharedState, name).set()

    def test_it_looks_at_the_same_four_as_the_wait(self):
        """_wait_other_windows() と同じ4つを見ていること"""
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        wait = src[src.index("    def _wait_other_windows"):]
        wait = wait[:wait.index("\n    def ", 10)]
        for name in self.EVENTS:
            self.assertIn(name, wait, name)




class TestContinueRoundFocus(unittest.TestCase):
    """続行になった窓を前面化する（速度検知フリーズと同じ扱い）"""

    CLASSIC_KEY = "Classic/クラシック"
    FOG_KEY = "Fog/霧"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        for name in TestNothingFrozen.EVENTS:
            getattr(SharedState, name).set()
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(lambda: [getattr(SharedState, n).set()
                                 for n in TestNothingFrozen.EVENTS])

    def _monitor(self, keep_on=None, instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(hwnd=777, do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=2)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._running = True
        return monitor

    def _judge(self, monitor, ids=(42,), round_type="Classic"):
        """続行判定を通す。前面化のスレッドは同期で走らせて中身を見る"""
        monitor.st.terror_ids = list(ids)
        with patch.object(WindowOperator, "focus_window",
                          return_value=True) as focus, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_continue_start"), \
             patch.object(LogMonitor.threading, "Thread",
                          side_effect=self._run_now):
            monitor._decide_with_keep_on_set(round_type)
        return focus

    @staticmethod
    def _run_now(target=None, args=(), daemon=None, **_kw):
        """立てたスレッドをその場で走らせる（前面化の中身まで見るため）"""
        if target is not None:
            target(*args)
        return MagicMock()

    # ── 1. 前面化する ─────────────────────────
    def test_a_continue_focuses_this_window(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})

        focus = self._judge(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        focus.assert_called_once_with(777)

    def test_a_group_wanted_focuses_this_window(self):
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)
        monitor.st.terror_ids = [42]

        with patch.object(WindowOperator, "focus_window",
                          return_value=True) as focus, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_continue_start"), \
             patch.object(monitor, "_group_decision",
                          return_value=GroupRound.WANTED), \
             patch.object(LogMonitor.threading, "Thread",
                          side_effect=self._run_now):
            self.assertTrue(monitor._apply_group_decision("Classic"))

        self.assertTrue(monitor.st.is_continue_round)
        focus.assert_called_once_with(777)

    # ── 2〜5. 前面化しない ──────────────────────
    def test_an_ongoing_continue_does_not_focus_again(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        monitor.st.is_continue_round = True
        SharedState.continue_round_start(monitor.st)

        focus = self._judge(monitor)

        focus.assert_not_called()

    def test_another_windows_continue_freeze_blocks_it(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        _freeze_other_continue()                # ほかの窓が張っている

        focus = self._judge(monitor)

        self.assertTrue(monitor.st.is_continue_round, "続行の判定自体は通る")
        focus.assert_not_called()

    def test_another_windows_speed_freeze_does_not_block_it(self):
        """続行ラウンドは何よりも優先して前面化する（速度検知のフリーズでは止めない）"""
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        SharedState.SPEED_FREEZE_EVENT.clear()

        focus = self._judge(monitor)

        focus.assert_called_once_with(777)

    def test_another_windows_equip_wait_does_not_block_it(self):
        """窓6 22:23 の並び（ほかの窓が装備待ち）でもすぐ前面化する"""
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        SharedState.EQUIP_WAIT_EVENT.clear()

        focus = self._judge(monitor)

        focus.assert_called_once_with(777)

    def test_a_round_freeze_does_not_block_it(self):
        """ラウンド突入のフリーズでも止めない"""
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        SharedState.ROUND_FREEZE_EVENT.clear()

        focus = self._judge(monitor)

        focus.assert_called_once_with(777)

    def test_another_windows_continue_round_blocks_it(self):
        """止めるのは、ほかの窓が続行ラウンドをやっているときだけ（今どおり前面化しない）"""
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        other = WindowState()
        SharedState.continue_round_start(other)
        self.addCleanup(SharedState.continue_round_reset)
        monitor.logs = []
        monitor.logger = monitor.logs.append

        focus = self._judge(monitor)

        focus.assert_not_called()
        self.assertTrue(any("ほかの窓が続行ラウンド中なので前面化しません" in m for m in monitor.logs))

    def test_the_round_freeze_focus_still_waits_for_other_freezes(self):
        """続行ラウンドでない前面化（ラウンド突入フリーズ）は今どおり、ほかの窓のフリーズ中は奪わない"""
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        SharedState.EQUIP_WAIT_EVENT.clear()
        monitor.logs = []
        monitor.logger = monitor.logs.append
        with patch.object(monitor, "_start_daemon") as start:
            monitor._focus_for_freeze("ラウンド突入フリーズ")
        start.assert_not_called()

    # ── 7〜8. 放置モードとスキップ ───────────────────
    def test_hands_free_does_not_focus(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        SharedState.set_hands_free(True)

        focus = self._judge(monitor)

        focus.assert_not_called()

    def test_a_skip_does_not_focus(self):
        monitor = self._monitor({self.CLASSIC_KEY: {99}})

        focus = self._judge(monitor, ids=(42,))

        self.assertFalse(monitor.st.is_continue_round)
        focus.assert_not_called()

    # ── 9. 失敗しても続ける ────────────────────
    def test_a_failed_focus_keeps_the_freeze_and_the_announce(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        monitor.st.terror_ids = [42]
        logs = []
        monitor.logger = logs.append

        with patch.object(WindowOperator, "focus_window",
                          return_value=False), \
             patch.object(PlaySound, "play_sound") as play, \
             patch.object(Recorder, "on_continue_start") as record, \
             patch.object(LogMonitor.threading, "Thread",
                          side_effect=self._run_now):
            monitor._decide_with_keep_on_set("Classic")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1)
        play.assert_called_once_with("continue.mp3")
        record.assert_called_once_with(2)
        self.assertTrue(any("前面化に失敗" in m for m in logs), logs)

    def test_the_focus_runs_off_the_log_thread(self):
        """ログの読み込みを止めないよう別スレッドで前面化する"""
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        monitor.st.terror_ids = [42]

        with patch.object(WindowOperator, "focus_window", return_value=True), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_continue_start"), \
             patch.object(LogMonitor.threading, "Thread") as thread:
            monitor._decide_with_keep_on_set("Classic")

        started = [c.kwargs["target"].__func__.__name__
                   for c in thread.call_args_list if "target" in c.kwargs]
        self.assertIn("_focus_this_window_for", started, started)

    def test_the_count_is_read_before_it_freezes(self):
        """自分のフリーズを数えると必ず前面化しなくなる"""
        monitor = self._monitor({self.CLASSIC_KEY: {42}})
        monitor.st.terror_ids = [42]
        seen = []

        def focus(hwnd):
            seen.append(SharedState.get_continue_round_count())
            return True

        with patch.object(WindowOperator, "focus_window", side_effect=focus), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_continue_start"), \
             patch.object(LogMonitor.threading, "Thread",
                          side_effect=self._run_now):
            monitor._decide_with_keep_on_set("Classic")

        self.assertEqual(seen, [0], "数える前に見ていること")




class TestAttendToItemLoss(unittest.TestCase):
    """アイテムロストの窓で、フリーズ・前面化・音声を1つにまとめて出す。

    装備待ちフリーズは待たずにすぐ張る。前面化と音声だけが、ほかの窓の
    フリーズが解けるのを待ち、解けた瞬間に一緒に出る（依頼者: 「ちゃんとその
    窓をアクティブにするときに音声を流してね」）。
    """

    OTHER_FREEZES = ("CONTINUE_ROUND_EVENT", "SPEED_FREEZE_EVENT",
                     "ROUND_FREEZE_EVENT")

    def setUp(self):
        SharedState.set_hands_free(False)
        SharedState.set_item_begin_mode(False)
        SharedState.equip_freeze_reset()
        for name in TestNothingFrozen.EVENTS:
            getattr(SharedState, name).set()
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        self.addCleanup(SharedState.equip_freeze_reset)
        self.addCleanup(lambda: [getattr(SharedState, n).set()
                                 for n in TestNothingFrozen.EVENTS])

    def _executor(self, logs=None, running=None):
        cfg = WindowConfig(hwnd=555, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=0, round_seq=1)
        st.waiting_for_equip = True
        ex = ActionExecutor.ActionExecutor(
            cfg, st, running or (lambda: True),
            logs.append if logs is not None else (lambda _m: None))
        return ex, st

    def _attend(self, ex, start_threads=False):
        """_attend_to_item_loss を回す。見張りのスレッドは立てずに記録だけする"""
        events = []
        with patch.object(WindowOperator, "focus_window",
                          side_effect=lambda h: events.append("focus") or True), \
             patch.object(PlaySound, "play_sound",
                          side_effect=lambda p: events.append("sound")), \
             patch.object(ActionExecutor.threading, "Thread") as thread:
            ex._attend_to_item_loss()
        return events, thread

    # ── 1. ほかの窓が止まっていなければ、その場で一緒に ──────────
    def test_focus_and_voice_come_together_at_once(self):
        ex, _st = self._executor()

        events, thread = self._attend(ex)

        self.assertEqual(events, ["focus", "sound"], "前面化と同時に鳴らす")
        thread.assert_not_called()

    def test_the_focus_log_is_the_only_line(self):
        logs = []
        ex, _st = self._executor(logs)

        self._attend(ex)

        self.assertEqual(logs, ["この窓を前面化しました（アイテム装備待ち）"])

    # ── 2. ほかの窓が止まっていれば、どちらも出ない ────────────
    def test_nothing_shows_while_another_window_is_frozen(self):
        for name in self.OTHER_FREEZES:
            ex, _st = self._executor()
            getattr(SharedState, name).clear()

            events, thread = self._attend(ex)

            self.assertEqual(events, [], name)
            thread.assert_called_once()            # 見張りに回る
            getattr(SharedState, name).set()

    def test_another_windows_equip_wait_holds_it_back(self):
        ex, _st = self._executor()
        other = WindowState(instance_type=config.INSTANCE_PRIVATE)
        other.waiting_for_equip = True
        SharedState.equip_freeze_start(other)

        events, thread = self._attend(ex)

        self.assertEqual(events, [])
        thread.assert_called_once()

    def test_starting_to_wait_is_not_logged(self):
        """依頼者はログが増えるのを嫌う。出すのは前面化したときの1行だけ"""
        logs = []
        ex, _st = self._executor(logs)
        SharedState.CONTINUE_ROUND_EVENT.clear()

        self._attend(ex)

        self.assertEqual(logs, [])

    # ── 3〜5. 見張り ─────────────────────────
    def _watch(self, ex, st, each_tick):
        """見張りを同期で回す。each_tick(n) で状況を動かす"""
        events = []
        ticks = {"n": 0}

        def sleep(_sec):
            ticks["n"] += 1
            each_tick(ticks["n"])
            if ticks["n"] > 50:
                raise AssertionError("見張りが終わらない")

        with patch.object(WindowOperator, "focus_window",
                          side_effect=lambda h: events.append("focus") or True), \
             patch.object(PlaySound, "play_sound",
                          side_effect=lambda p: events.append("sound")), \
             patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
            ex._watch_item_loss(st.round_seq)
        return events

    def test_both_come_together_the_moment_the_freeze_lifts(self):
        ex, st = self._executor()
        SharedState.equip_freeze_start(st)
        SharedState.CONTINUE_ROUND_EVENT.clear()

        def tick(n):
            if n == 3:
                SharedState.CONTINUE_ROUND_EVENT.set()    # 続行ラウンドが終わった

        events = self._watch(ex, st, tick)

        self.assertEqual(events, ["focus", "sound"])

    def test_equipping_while_waiting_shows_nothing(self):
        ex, st = self._executor()
        SharedState.equip_freeze_start(st)
        SharedState.CONTINUE_ROUND_EVENT.clear()

        def tick(n):
            if n == 2:
                st.item_id = 7                           # 自分で拾った

        self.assertEqual(self._watch(ex, st, tick), [])

    def test_a_cleared_equip_wait_ends_the_watch(self):
        ex, st = self._executor()
        SharedState.CONTINUE_ROUND_EVENT.clear()

        def tick(n):
            if n == 2:
                st.waiting_for_equip = False

        self.assertEqual(self._watch(ex, st, tick), [])

    def test_a_started_round_ends_the_watch(self):
        for change in ("in_round", "round_seq"):
            ex, st = self._executor()
            SharedState.CONTINUE_ROUND_EVENT.clear()

            def tick(n, st=st, change=change):
                if n == 2:
                    if change == "in_round":
                        st.in_round = True
                    else:
                        st.round_seq += 1

            self.assertEqual(self._watch(ex, st, tick), [], change)

    def test_a_stop_ends_the_watch(self):
        running = [True, True]
        ex, st = self._executor(running=lambda: bool(running) and running.pop())
        SharedState.CONTINUE_ROUND_EVENT.clear()

        self.assertEqual(self._watch(ex, st, lambda n: None), [])

    # ── 6. 同じラウンドで二重に見張らない ───────────────────
    def test_the_watch_is_not_started_twice_in_a_round(self):
        ex, st = self._executor()
        SharedState.CONTINUE_ROUND_EVENT.clear()

        _e, first = self._attend(ex)
        _e, second = self._attend(ex)

        first.assert_called_once()
        second.assert_not_called()

    def test_the_next_round_can_watch_again(self):
        ex, st = self._executor()
        SharedState.CONTINUE_ROUND_EVENT.clear()
        self._attend(ex)
        st.round_seq += 1

        _e, thread = self._attend(ex)

        thread.assert_called_once()

    # ── 7. フリーズだけは待たずに張る（案1） ─────────────────
    def test_the_equip_wait_is_set_at_once_even_while_others_are_frozen(self):
        """3つとも一緒に待つ（案2）と、続行が解けてからこの窓が張るまでに隙間が
        でき、ほかの窓がそこで Begin へ走る。フリーズだけは待たない"""
        ex, st = self._executor()
        SharedState.CONTINUE_ROUND_EVENT.clear()

        events, _thread = self._attend(ex)

        self.assertTrue(st.equip_freeze_held, "装備待ちフリーズはもう張られている")
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(events, [], "前面化と音声だけが待つ")

    # ── 12. 放置モード ────────────────────────
    def test_hands_free_does_nothing(self):
        ex, st = self._executor()
        SharedState.set_hands_free(True)

        events, thread = self._attend(ex)

        self.assertEqual(events, [])
        thread.assert_not_called()
        self.assertFalse(st.equip_freeze_held)

    def test_a_failed_focus_still_speaks(self):
        """前面化に失敗しても装備待ちは続く。声は出す"""
        logs = []
        ex, _st = self._executor(logs)
        played = []

        with patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(PlaySound, "play_sound", side_effect=played.append), \
             patch.object(ActionExecutor.threading, "Thread"):
            ex._attend_to_item_loss()

        self.assertEqual(played, ["lost.mp3"])
        self.assertTrue(any("前面化に失敗" in m for m in logs), logs)

    # ── 13. 音声はここからしか鳴らない ──────────────────
    def test_the_voice_comes_only_from_here(self):
        """B・C の窓の音声は _attend_to_item_loss() だけ。_handle_item_lost() と
        Begin 直前からは鳴らなくなった。例外はアイテム自動取得で取りに行く前の音だけ"""
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        callers = [m.start() for m in re.finditer(r"self\.announce_item_lost_once\(\)", src)]
        show = src[src.index("    def _show_item_loss("):]
        show = show[:show.index("\n    def ", 10)]
        after = src[src.index("    def do_after_round("):]
        after = after[:after.index("\n    def ", 10)]

        self.assertEqual(len(callers), 2, "_show_item_loss と、自動取得で取りに行く前の2か所だけ")
        self.assertIn("self.announce_item_lost_once()", show)
        self.assertIn("if fetch:", after[:after.index("self.announce_item_lost_once()")][-1200:],
                      "自動取得で取りに行くときだけ")
        self.assertNotIn("announce_item_lost_if_needed", src, "押す直前の通知は消えた")

    def test_the_item_begin_mode_no_longer_speaks_while_waiting_to_equip(self):
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _handle_item_lost("):]
        body = body[:body.index("\n    def ", 10)]

        self.assertNotIn("announce_item_lost_once", body)

    # ── C: 押した後、受理されてから ────────────────────
    def _after_round(self, accept):
        """通常モードで Begin まで回し、前面化と音声がどこで出るかを見る"""
        cfg = WindowConfig(hwnd=555, osc_port=0, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=0, round_seq=1)
        st.waiting_for_equip = True
        order = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        def press(*_a, **_kw):
            order.append("press")
            if accept:
                st.begin_done = True
            return True

        def sleep(_sec):
            if "press" in order:                   # 押した後の装備待ちのループを抜ける
                st.waiting_for_equip = False

        with patch.object(ex, "_begin_move"), \
             patch.object(ex, "_wait_round_end", return_value=True), \
             patch.object(ex, "_handle_item_lost", return_value=True), \
             patch.object(ex, "_wait_other_windows", return_value=True), \
             patch.object(ex, "_begin_precheck", return_value=True), \
             patch.object(ex, "_press_begin", side_effect=press), \
             patch.object(ex, "_confirm_begin"), \
             patch.object(ActionExecutor.time, "sleep", side_effect=sleep), \
             patch.object(WindowOperator, "focus_window",
                          side_effect=lambda h: order.append("focus") or True), \
             patch.object(PlaySound, "play_sound",
                          side_effect=lambda p: order.append("sound")), \
             patch.object(ActionExecutor.threading, "Thread"):
            ex.do_after_round()
        return order

    def test_c_speaks_after_the_press_not_before(self):
        """押す前に前面化するとカーソル方式 Begin が壊れる（VRChat がカーソルを掴む）"""
        self.assertEqual(self._after_round(accept=True), ["press", "focus", "sound"])

    def test_c_shows_nothing_when_the_begin_was_not_accepted(self):
        """受理されなかったラウンドでは出さない（依頼者了承済み）"""
        self.assertEqual(self._after_round(accept=False), ["press"])




class TestItemLossAtRoundOver(unittest.TestCase):
    """アイテム取得→Begin モード（B）は RoundOver で出す。Verified Round End では
    前面化・フリーズ・音声を一切しない（そこにあった素の focus_window は、ほかの窓の
    続行中でも前面を奪っていた）。モードは自動 Begin が機能している窓でだけ効く"""

    def setUp(self):
        # RoundOver でも装備待ちフリーズを張るので、前のテストの分を残さない
        SharedState.equip_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)
        SharedState.set_hands_free(False)
        SharedState.set_item_begin_mode(False)
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        for name in TestNothingFrozen.EVENTS:
            getattr(SharedState, name).set()
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        self.addCleanup(SharedState.equip_freeze_reset)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(lambda: [getattr(SharedState, n).set()
                                 for n in TestNothingFrozen.EVENTS])

    def _monitor(self, auto_begin=True, itype=None):
        cfg = WindowConfig(hwnd=777, auto_begin=auto_begin, voice_item_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = itype or config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.item_id = 5
        monitor._running = True
        return monitor

    @staticmethod
    def _lose(monitor):
        monitor.st.item_lost_this_round = True
        monitor.st.item_id = 0

    def _feed(self, monitor, *lines):
        """行を流し、前面化と音声がどの行で起きたかを返す"""
        seen = []
        for line in lines:
            with patch.object(WindowOperator, "focus_window",
                              side_effect=lambda h, l=line: seen.append(("focus", l)) or True), \
                 patch.object(PlaySound, "play_sound",
                              side_effect=lambda p, l=line: seen.append(("sound", l))), \
                 patch.object(LogMonitor.threading, "Thread"), \
                 patch.object(ActionExecutor.threading, "Thread"), \
                 patch.object(Recorder, "on_round_over"):
                monitor._process(f"2026.09.29 00:00:00 Debug      -  {line}")
        return seen

    # ── B ─────────────────────────────────
    def test_b_freezes_focuses_and_speaks_at_round_over(self):
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor()
        self._lose(monitor)

        seen = self._feed(monitor, "RoundOver")

        self.assertEqual(seen, [("focus", "RoundOver"), ("sound", "RoundOver")])
        self.assertTrue(monitor.st.equip_freeze_held)
        self.assertTrue(monitor.st.waiting_for_equip)

    def test_b_does_not_focus_again_at_verified_round_end(self):
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor()
        self._lose(monitor)

        seen = self._feed(monitor, "RoundOver", "Verified Round End")

        self.assertNotIn(("focus", "Verified Round End"), seen)
        self.assertNotIn(("sound", "Verified Round End"), seen)

    def test_b_waits_while_another_window_is_frozen(self):
        """続行中の窓の前面を奪わない。フリーズだけはすぐ張る"""
        SharedState.set_item_begin_mode(True)
        other = WindowState()
        SharedState.continue_round_start(other)
        monitor = self._monitor()
        self._lose(monitor)

        seen = self._feed(monitor, "RoundOver", "Verified Round End")

        self.assertEqual(seen, [], "前面化も音声もまだ")
        self.assertTrue(monitor.st.equip_freeze_held, "フリーズは張っている")

    def test_b_is_only_for_windows_the_tool_runs(self):
        """自動 Begin が機能していない窓は A の扱い（RoundOver で前面化＋音声＋フリーズ）"""
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor(auto_begin=True, itype=config.INSTANCE_PUBLIC)
        self._lose(monitor)

        seen = self._feed(monitor, "RoundOver")

        self.assertEqual(seen, [("focus", "RoundOver"), ("sound", "RoundOver")], "A でも前面化")
        self.assertTrue(monitor.st.equip_freeze_held, "装備待ちフリーズを張る")

    def test_b_does_nothing_in_hands_free(self):
        SharedState.set_item_begin_mode(True)
        SharedState.set_hands_free(True)
        monitor = self._monitor()
        self._lose(monitor)

        seen = self._feed(monitor, "RoundOver", "Verified Round End")

        self.assertEqual(seen, [])

    def test_b_is_not_caught_at_verified_round_end(self):
        """RoundOver の時点で判定できなかったときも、Verified Round End では拾わない
        （依頼者: Verified Round End の前面化はいらない）。フリーズも音声も出さない"""
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor()
        self._feed(monitor, "RoundOver")               # まだ失っていない
        self._lose(monitor)

        seen = self._feed(monitor, "Verified Round End")

        self.assertEqual(seen, [])
        self.assertFalse(monitor.st.equip_freeze_held)

    def test_no_window_focuses_at_verified_round_end(self):
        """アイテム取得→Begin モードで、自動 Begin が機能している窓・していない窓の両方"""
        SharedState.set_item_begin_mode(True)
        for auto_begin, itype in ((True, None), (True, config.INSTANCE_PUBLIC),
                                  (False, None)):
            SharedState.equip_freeze_reset()
            monitor = self._monitor(auto_begin=auto_begin, itype=itype)
            self._lose(monitor)
            started = []
            with patch.object(WindowOperator, "focus_window") as focus,                  patch.object(monitor, "_start_daemon",
                              side_effect=lambda target, *a: started.append(target)):
                monitor.st.in_round = False
                self._feed(monitor, "Verified Round End")
                focus.assert_not_called()
            # _feed が focus_window を自前の偽物に差し替えるので、名前で見る
            names = [getattr(t, "__name__", getattr(t, "_mock_name", "")) for t in started]
            self.assertNotIn("focus_window", names, "素の前面化を裏で撃たない")
            self.assertEqual(started.count(monitor._action._attend_to_item_loss), 0)
            self.assertFalse(monitor.st.equip_freeze_held, (auto_begin, itype))

    def _run_without_auto_begin(self, mode, auto_begin, itype):
        SharedState.set_item_begin_mode(mode)
        SharedState.equip_freeze_reset()
        monitor = self._monitor(auto_begin=auto_begin, itype=itype)
        logs = []
        monitor.logger = logs.append
        self._lose(monitor)
        seen = self._feed(monitor, "RoundOver", "Verified Round End")
        st = monitor.st
        return (seen, logs, st.waiting_for_equip, st.equip_freeze_held,
                SharedState.get_equip_freeze_count(), SharedState.EQUIP_WAIT_EVENT.is_set())

    def test_windows_without_auto_begin_ignore_the_mode(self):
        """自動 Begin が機能していない窓は、モードが ON でも OFF と同じ動き
        （フリーズ・前面化・音声・ログのすべて）"""
        for auto_begin, itype in ((False, None), (True, config.INSTANCE_PUBLIC),
                                  (False, config.INSTANCE_PUBLIC)):
            off = self._run_without_auto_begin(False, auto_begin, itype)
            on = self._run_without_auto_begin(True, auto_begin, itype)
            self.assertEqual(on, off, (auto_begin, itype))

    def test_the_mode_is_decided_in_one_place(self):
        """LogMonitor はモードを _item_begin_mode_active() だけで見る"""
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("SharedState.get_item_begin_mode()"), 1)
        body = src[src.index("    def _item_begin_mode_active(self)"):]
        self.assertIn("SharedState.get_item_begin_mode() and self._auto_begin_active()",
                      body[:body.index("\n    def ", 10)])

    # ── A: 据え置き ─────────────────────────────
    def test_a_still_speaks_at_round_over(self):
        """Begin を押さない窓も RoundOver で前面化＋音声（1回）＋装備待ちフリーズ"""
        monitor = self._monitor(auto_begin=False)
        self._lose(monitor)

        seen = self._feed(monitor, "RoundOver")

        self.assertEqual(seen, [("focus", "RoundOver"), ("sound", "RoundOver")])
        self.assertTrue(monitor.st.equip_freeze_held)

    # ── C: RoundOver では何もしない ───────────────────
    def test_c_does_nothing_at_round_over(self):
        monitor = self._monitor()                       # 通常モード
        self._lose(monitor)

        seen = self._feed(monitor, "RoundOver", "Verified Round End")

        self.assertEqual(seen, [], "C は Begin を押した後に出す")




class TestEquipQueue(unittest.TestCase):
    """アイテムロストの前面化＋音声は、装備待ちを張った順に1窓ずつ。

    2窓が同じ瞬間に RoundOver を受けて両方が張ると、数で見ていた頃は両方が
    「ほかの窓の装備待ちがある」と見て、どちらも出さなかった。Begin を押す判定は
    順番待ちにしない（装備待ちの解除条件のせいでデッドロックする）
    """

    def setUp(self):
        SharedState.set_hands_free(False)
        SharedState.set_item_begin_mode(False)
        SharedState.equip_freeze_reset()
        for name in TestNothingFrozen.EVENTS:
            getattr(SharedState, name).set()
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        self.addCleanup(SharedState.equip_freeze_reset)
        self.addCleanup(lambda: [getattr(SharedState, n).set()
                                 for n in TestNothingFrozen.EVENTS])
        self.shown = []          # (前面化 or 音声, 窓)

    def _executor(self, hwnd):
        cfg = WindowConfig(hwnd=hwnd, osc_port=9000, voice_item_lost=f"lost{hwnd}.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=0, round_seq=1)
        st.waiting_for_equip = True
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None), st

    def _patches(self):
        return (patch.object(WindowOperator, "focus_window",
                             side_effect=lambda h: self.shown.append(("focus", h)) or True),
                patch.object(PlaySound, "play_sound",
                             side_effect=lambda p: self.shown.append(
                                 ("sound", int(p[4:-4])))))

    def _attend(self, ex):
        """_attend_to_item_loss を回す。見張りは立てずに、あとで回せるよう返す"""
        focus, sound = self._patches()
        with focus, sound, patch.object(ActionExecutor.threading, "Thread") as thread:
            ex._attend_to_item_loss()
        if not thread.called:
            return None
        kwargs = thread.call_args.kwargs
        return lambda: kwargs["target"](*kwargs["args"])

    def _watch(self, watcher, on_sleep):
        """見張りを回す。待つたびに on_sleep() を呼ぶ（前の窓が装備を終える、など）。
        いつまでも出さない不具合で止まらないよう、20回待ったら落とす"""
        waits = []

        def sleep(_s):
            waits.append(1)
            if len(waits) > 20:
                raise AssertionError("見張りが出さないまま待ち続けている")
            on_sleep()

        focus, sound = self._patches()
        with focus, sound, patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
            watcher()

    # ── 1. 同じ瞬間に2窓 → ちょうど1窓だけ ──────────────
    def test_two_windows_at_the_same_moment_show_exactly_one(self):
        x, xst = self._executor(0x10)
        y, yst = self._executor(0x20)
        SharedState.equip_freeze_start(xst)    # 両方が張ってから判定する（同じ瞬間）
        SharedState.equip_freeze_start(yst)

        watch_x = self._attend(x)
        watch_y = self._attend(y)

        self.assertEqual(self.shown, [("focus", 0x10), ("sound", 0x10)])
        self.assertIsNone(watch_x, "先頭はその場で出す")
        self.assertIsNotNone(watch_y, "2番目は見張りに回る")

    def test_the_order_does_not_depend_on_who_checks_first(self):
        """列は張った順。先に判定した窓が勝つのではない"""
        x, xst = self._executor(0x10)
        y, yst = self._executor(0x20)
        SharedState.equip_freeze_start(xst)
        SharedState.equip_freeze_start(yst)

        self._attend(y)
        self._attend(x)

        self.assertEqual(self.shown, [("focus", 0x10), ("sound", 0x10)])

    # ── 2. 先頭が装備を終えたら2番目 ──────────────────
    def test_the_second_window_shows_when_the_first_is_done(self):
        x, xst = self._executor(0x10)
        y, yst = self._executor(0x20)
        self._attend(x)
        watch_y = self._attend(y)
        self.shown.clear()

        def x_equips():
            self.assertEqual(self.shown, [], "前の窓が装備するまでは出さない")
            xst.item_id = 3
            xst.waiting_for_equip = False
            SharedState.equip_freeze_end(xst)

        self._watch(watch_y, x_equips)

        self.assertEqual(self.shown, [("focus", 0x20), ("sound", 0x20)])

    # ── 3. 3窓でも1窓ずつ ────────────────────────
    def test_three_windows_show_one_at_a_time_in_order(self):
        windows = [self._executor(h) for h in (0x10, 0x20, 0x30)]
        watchers = [self._attend(ex) for ex, _st in windows]
        self.assertEqual(self.shown, [("focus", 0x10), ("sound", 0x10)])
        self.assertIsNone(watchers[0])

        for done, (nxt, later) in ((0, (1, 2)), (1, (2, None))):
            self.shown.clear()
            _ex, st = windows[done]

            def equips(st=st):
                st.item_id = 3
                SharedState.equip_freeze_end(st)

            self._watch(watchers[nxt], equips)
            hwnd = windows[nxt][0]._cfg.hwnd
            self.assertEqual(self.shown, [("focus", hwnd), ("sound", hwnd)])
            if later is not None:
                self.assertFalse(SharedState.is_first_in_equip_queue(windows[later][1]))

    # ── 4. 列への追加は冪等 ───────────────────────
    def test_joining_the_queue_twice_counts_once(self):
        _x, xst = self._executor(0x10)
        _y, yst = self._executor(0x20)
        SharedState.equip_freeze_start(xst)
        SharedState.equip_freeze_start(xst)
        SharedState.equip_freeze_start(yst)
        SharedState.equip_freeze_start(xst)

        self.assertEqual([w is xst for w in SharedState._EQUIP_QUEUE], [True, False],
                         "列に2回入らない")
        SharedState.equip_freeze_end(xst)

        self.assertTrue(SharedState.is_first_in_equip_queue(yst), "x は1回で列から外れる")
        self.assertFalse(SharedState.is_first_in_equip_queue(xst))
        SharedState.equip_freeze_end(yst)
        self.assertFalse(SharedState.is_first_in_equip_queue(yst))
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())

    # ── 5. 停止で列を空にする ──────────────────────
    def test_a_reset_empties_the_queue(self):
        _x, xst = self._executor(0x10)
        SharedState.equip_freeze_start(xst)

        SharedState.equip_freeze_reset()

        self.assertFalse(SharedState.is_first_in_equip_queue(xst))
        _y, yst = self._executor(0x20)
        SharedState.equip_freeze_start(yst)
        self.assertTrue(SharedState.is_first_in_equip_queue(yst), "前の窓が列に残っていない")

    # ── 6. Begin は順番待ちにしない ──────────────────
    def test_begin_is_not_queued(self):
        x, xst = self._executor(0x10)
        y, yst = self._executor(0x20)
        SharedState.equip_freeze_start(xst)
        SharedState.equip_freeze_start(yst)

        self.assertFalse(SharedState.is_first_in_equip_queue(yst))
        self.assertEqual(y._freezes_ok(), (True, True, True, True),
                         "2番目の窓も Begin は押しに行く")
        self.assertEqual(x._freezes_ok(), (True, True, True, True))

    # ── 7. 先頭でも、ほかの窓の続行フリーズは待つ ───────────
    def test_the_head_still_waits_for_another_windows_continue_freeze(self):
        other = WindowState(instance_type=config.INSTANCE_PRIVATE)
        SharedState.continue_round_start(other)
        self.addCleanup(SharedState.continue_round_reset)
        x, xst = self._executor(0x10)

        watch_x = self._attend(x)

        self.assertTrue(SharedState.is_first_in_equip_queue(xst))
        self.assertEqual(self.shown, [])
        self.assertIsNotNone(watch_x)
        self._watch(watch_x, lambda: SharedState.continue_round_end(other))
        self.assertEqual(self.shown, [("focus", 0x10), ("sound", 0x10)])




class TestReturnFront(unittest.TestCase):
    """ツールが VRChat の窓を前面にしたら、理由が終わった時点でカーソルを戻し、
    元の窓を前面に戻す（依頼者: バックグラウンドにしていた窓に戻ると理想的）。

    Begin のフォールバックは離した直後、入室のクリックは離して
    FOCUS_RETURN_AFTER_RELEASE_SEC 後、フリーズの前面化はその窓のフリーズが解けたとき
    """

    VRC = 0x100          # ツールが前に出す VRChat の窓
    OTHER_VRC = 0x200    # 管理下の別の VRChat の窓
    EDITOR = 0x900       # 利用者が作業していた窓（VSCode など）
    CURSOR = (11, 22)

    def setUp(self):
        self.front = self.EDITOR
        self.alive = {self.VRC, self.OTHER_VRC, self.EDITOR, 0x901, 0x777}
        self.events = []
        SharedState.clear_window_hwnds()
        SharedState.register_window_hwnd(self.VRC)
        SharedState.register_window_hwnd(self.OTHER_VRC)
        for reset in (SharedState.clear_window_hwnds, SharedState.equip_freeze_reset,
                      SharedState.continue_round_reset, SharedState.speed_freeze_reset,
                      SharedState.round_freeze_reset):
            self.addCleanup(reset)
        user32 = MagicMock()
        user32.SetCursorPos.side_effect = (
            lambda x, y: self.events.append(("cursor", (x, y))) or True)

        def focus(hwnd):
            self.events.append(("focus", hwnd))
            self.front = hwnd
            return True

        for p in (patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: self.front),
                  patch.object(WindowOperator, "cursor_position", return_value=self.CURSOR),
                  patch.object(WindowOperator, "focus_window", side_effect=focus),
                  patch.object(WindowOperator, "user32", user32),
                  patch.object(WindowOperator.win32gui, "IsWindow",
                               side_effect=lambda h: h in self.alive),
                  patch.object(WindowOperator, "click",
                               side_effect=lambda: self.events.append(("click",))),
                  # 返しに行く裏のスレッドは、その場で回す
                  patch.object(SharedState, "_start_give_back",
                               side_effect=lambda loan: SharedState._give_back(loan)),
                  patch.object(PlaySound, "play_sound")):
            p.start()
            self.addCleanup(p.stop)

    def _executor(self, hwnd=None, auto_begin=False):
        cfg = WindowConfig(hwnd=hwnd or self.VRC, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=0)
        st.waiting_for_equip = True
        return ActionExecutor.ActionExecutor(
            cfg, st, lambda: True, lambda _m: None,
            auto_begin_active=lambda: auto_begin), st

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=self.VRC), {}, lambda _m: None,
                                        window_idx=1)
        monitor.logger = lambda _m: None
        return monitor

    RETURNED = [("cursor", CURSOR), ("focus", EDITOR)]

    def _returned(self):
        """前に出した後の出来事（前に出した瞬間より後ろ）"""
        first = self.events.index(("focus", self.VRC))
        return self.events[first + 1:]

    def _press_begin_by_fallback(self, ex):
        with patch.object(ex, "_begin_by_cursor", return_value=False), \
             patch.object(ActionExecutor.time, "sleep",
                          side_effect=lambda s: self.events.append(("sleep", s))):
            return ex._press_begin()

    # ── 1. Begin のフォールバック: 離した直後 ───────────────
    def test_the_begin_fallback_gives_back_right_after_the_click(self):
        ex, _st = self._executor()

        self.assertTrue(self._press_begin_by_fallback(ex))

        self.assertEqual(self.events, [("focus", self.VRC), ("click",)] + self.RETURNED,
                         "クリックと返すの間に待ちが無い")
        self.assertEqual(self.front, self.EDITOR)

    # ── 2. 入室のクリック: 離して0.3秒後 ──────────────────
    def test_the_entry_click_waits_after_the_release_before_giving_back(self):
        entry = ToNEntry.ToNEntry(self.VRC, osc_port=9000)
        with patch.object(ToNEntry.time, "sleep",
                          side_effect=lambda s: self.events.append(("sleep", s))):
            self.assertTrue(entry.click("警告同意"))

        self.assertEqual(self.events, [("focus", self.VRC), ("click",),
                                       ("sleep", config.FOCUS_RETURN_AFTER_RELEASE_SEC)]
                         + self.RETURNED)
        self.assertEqual(config.FOCUS_RETURN_AFTER_RELEASE_SEC, 0.3)

    # ── 3. フリーズの前面化: 解けたとき ───────────────────
    def _freeze_cases(self):
        """(名前, 張る, 前に出す, 解く)"""
        def by_monitor(label, start, end):
            monitor = self._monitor()
            return (lambda: start(monitor.st),
                    lambda: monitor._focus_this_window_for(label),
                    lambda: end(monitor.st))

        def by_executor(show, start, end):
            ex, st = self._executor()
            return (lambda: start(st), lambda: show(ex), lambda: end(st))

        return [
            ("続行", *by_monitor("続行ラウンド", SharedState.continue_round_start,
                                SharedState.continue_round_end)),
            ("突入", *by_monitor("ラウンド突入フリーズ", SharedState.round_freeze_start,
                                SharedState.round_freeze_end)),
            ("速度検知", *by_executor(lambda ex: ex._focus_for_speed_freeze(),
                                    SharedState.speed_freeze_start,
                                    SharedState.speed_freeze_end)),
            ("アイテムロスト", *by_executor(lambda ex: ex._show_item_loss(),
                                      SharedState.equip_freeze_start,
                                      SharedState.equip_freeze_end)),
        ]

    def test_a_freeze_focus_gives_back_when_the_freeze_ends(self):
        for name, start, show, end in self._freeze_cases():
            self.events.clear()
            self.front = self.EDITOR
            start()
            show()
            self.assertEqual(self.events, [("focus", self.VRC)], f"{name}: 前にした瞬間には返さない")

            end()

            self.assertEqual(self._returned(), self.RETURNED, name)

    def test_it_waits_until_every_freeze_of_the_window_is_gone(self):
        """続行で借りたまま装備待ちになったら、装備待ちが解けるまで返さない"""
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")
        SharedState.equip_freeze_start(monitor.st)

        SharedState.continue_round_end(monitor.st)
        self.assertEqual(self._returned(), [], "まだ装備待ちがある")

        SharedState.equip_freeze_end(monitor.st)
        self.assertEqual(self._returned(), self.RETURNED)

    # ── 4. 元の窓が VRChat なら返さない ───────────────────
    def test_nothing_is_given_back_to_a_vrchat_window(self):
        self.front = self.OTHER_VRC
        ex, _st = self._executor()
        self._press_begin_by_fallback(ex)
        self.assertEqual(self._returned(), [("click",)], "カーソルも動かさない")

        for name, start, show, end in self._freeze_cases():
            self.events.clear()
            self.front = self.OTHER_VRC
            start()
            show()
            end()
            self.assertEqual(self._returned(), [], name)

    def test_the_tools_own_window_is_given_back(self):
        """ツール自身の画面は管理下の VRChat ではない（作業していた窓）"""
        SharedState.register_own_window(self.EDITOR)
        self.addCleanup(SharedState.unregister_own_window, self.EDITOR)
        ex, _st = self._executor()
        self._press_begin_by_fallback(ex)
        self.assertEqual(self._returned(), [("click",)] + self.RETURNED)

    # ── 5. 返す瞬間に前面が変わっていたら返さない ───────────────
    def test_nothing_is_given_back_if_the_user_moved_on(self):
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")

        self.front = 0x777               # 利用者が自分で別の窓へ移った
        SharedState.continue_round_end(monitor.st)

        self.assertEqual(self._returned(), [], "引き戻さない・カーソルも動かさない")
        self.assertEqual(self.front, 0x777)

    # ── 6. 元の窓が消えていたら返さない ───────────────────
    def test_nothing_is_given_back_to_a_closed_window(self):
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")

        self.alive.discard(self.EDITOR)
        SharedState.continue_round_end(monitor.st)

        self.assertEqual(self._returned(), [])

    def test_no_previous_window_means_nothing_to_give_back(self):
        self.front = 0
        ex, _st = self._executor()
        self._press_begin_by_fallback(ex)
        self.assertEqual(self._returned(), [("click",)])

    # ── 7. カーソルが先、前面は後 ─────────────────────
    def test_the_cursor_goes_back_before_the_window(self):
        ex, st = self._executor()
        SharedState.speed_freeze_start(st)
        ex._focus_for_speed_freeze()
        SharedState.speed_freeze_end(st)

        returned = self._returned()
        self.assertEqual(returned[0], ("cursor", self.CURSOR))
        self.assertEqual(returned[1], ("focus", self.EDITOR))

    # ── アイテム取得→Begin モード: 元の窓へ返さず、アイテムロストの窓へ渡す ──────
    WIN_B = 0x300

    def _attend_later(self, ex):
        """_attend_to_item_loss を回す。見張りは立てずに、あとで回せるよう返す"""
        with patch.object(ActionExecutor.threading, "Thread") as thread:
            ex._attend_to_item_loss()
        if not thread.called:
            return None
        kwargs = thread.call_args.kwargs
        return lambda: kwargs["target"](*kwargs["args"])

    def _run_watcher(self, watcher):
        with patch.object(ActionExecutor.time, "sleep",
                          side_effect=AssertionError("見張りが出さないまま待っている")):
            watcher()

    def _mode(self, on):
        SharedState.set_item_begin_mode(on)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        SharedState.register_window_hwnd(self.WIN_B)

    def test_the_mode_hands_the_front_to_the_item_loss_window(self):
        """窓A の続行が解けても VSCode へは返さず、窓B が前に出る。窓B の装備が
        終わったら VSCode へ返る。VSCode への返却は1回だけ"""
        self._mode(True)
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")          # 札: VSCode
        b, bst = self._executor(self.WIN_B, auto_begin=True)
        watch_b = self._attend_later(b)
        self.assertIsNotNone(watch_b, "窓A の続行中は待つ")

        SharedState.continue_round_end(monitor.st)
        self.assertEqual(self._returned(), [], "VSCode を一瞬挟まない")
        self.assertEqual(self.front, self.VRC)

        self._run_watcher(watch_b)                              # 窓B の前面化＋音声
        self.assertEqual(self._returned(), [("focus", self.WIN_B)])
        PlaySound.play_sound.assert_called_once()

        SharedState.equip_freeze_end(bst)

        self.assertEqual(self._returned(), [("focus", self.WIN_B)] + self.RETURNED)
        self.assertEqual(self.events.count(("focus", self.EDITOR)), 1)
        self.assertEqual(self.front, self.EDITOR)

    def test_the_hand_over_does_not_depend_on_who_is_first(self):
        """窓B の見張りが先に前へ出ても（札は無い）、引き継いだ札で1回だけ返る"""
        self._mode(True)
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")
        b, bst = self._executor(self.WIN_B, auto_begin=True)
        self._attend_later(b)
        b._show_item_loss()                                     # 前は窓A（VRChat）→ 札なし
        SharedState.continue_round_end(monitor.st)
        self.assertNotIn(("focus", self.EDITOR), self.events)

        SharedState.equip_freeze_end(bst)

        self.assertEqual(self.events.count(("focus", self.EDITOR)), 1, self.events)

    def test_the_same_window_keeps_the_front_until_its_equip_wait_ends(self):
        self._mode(True)
        ex, st = self._executor(auto_begin=True)
        SharedState.continue_round_start(st)
        with patch.object(SharedState, "nothing_frozen", return_value=True):
            monitor = self._monitor()
            monitor.st = st
            monitor._focus_this_window_for("続行ラウンド")
        self._attend_later(ex)

        SharedState.continue_round_end(st)
        self.assertEqual(self._returned(), [], "装備待ちが残っている")

        SharedState.equip_freeze_end(st)
        self.assertEqual(self._returned(), self.RETURNED)

    def test_the_normal_mode_gives_back_then_borrows_again(self):
        """通常モード: 続行が解けたら返す → Begin の後の案内で再び前面化（札は VSCode）
        → 装備待ちが解けたら返す"""
        self._mode(False)
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")
        SharedState.continue_round_end(monitor.st)
        self.assertEqual(self._returned(), self.RETURNED)

        b, bst = self._executor(self.WIN_B, auto_begin=True)
        self.assertIsNone(self._attend_later(b), "ほかにフリーズが無いのでその場で出す")
        SharedState.equip_freeze_end(bst)

        self.assertEqual(self._returned(), self.RETURNED + [("focus", self.WIN_B)]
                         + self.RETURNED)

    def test_only_the_head_of_the_queue_takes_over(self):
        self._mode(True)
        SharedState.register_window_hwnd(0x400)
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")
        b, bst = self._executor(self.WIN_B, auto_begin=True)
        c, cst = self._executor(0x400, auto_begin=True)
        self._attend_later(b)
        self._attend_later(c)

        SharedState.continue_round_end(monitor.st)

        self.assertIsNotNone(bst.front_loan, "先頭の窓B が引き継ぐ")
        self.assertIsNone(cst.front_loan)
        self.assertEqual(bst.front_loan.hwnd, self.WIN_B)

    def test_a_finished_mode_equip_wait_does_not_take_over_later(self):
        """モードの装備待ちが解けたら印も消える。後で通常モードで並んでも引き継がない"""
        self._mode(True)
        b, bst = self._executor(self.WIN_B, auto_begin=True)
        self._attend_later(b)
        SharedState.equip_freeze_end(bst)
        SharedState.set_item_begin_mode(False)
        self.events.clear()
        self.front = self.EDITOR

        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")
        SharedState.equip_freeze_start(bst)             # 通常モードで並ぶ
        SharedState.continue_round_end(monitor.st)

        self.assertEqual(self._returned(), self.RETURNED)
        self.assertIsNone(bst.front_loan)

    def test_a_window_without_auto_begin_does_not_take_over(self):
        """モードが ON でも、自動 Begin が機能していない窓は引き継がない"""
        self._mode(True)
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")
        b, bst = self._executor(self.WIN_B, auto_begin=False)
        self._attend_later(b)
        self.assertTrue(SharedState.is_first_in_equip_queue(bst))

        SharedState.continue_round_end(monitor.st)

        self.assertEqual(self._returned(), self.RETURNED, "元の窓へ返す")
        self.assertIsNone(bst.front_loan)

    # ── 8. 停止では返さない ─────────────────────────
    def test_stopping_does_not_give_back(self):
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")

        monitor.stop()
        SharedState.continue_round_end(monitor.st)

        self.assertEqual(self._returned(), [])
        self.assertIsNone(monitor.st.front_loan, "札は捨てた")

    # ── 9. 同じ窓で二重に借りても、返すのは最初の札の1回だけ ──────────
    def test_a_second_loan_on_the_same_window_is_not_kept(self):
        ex, st = self._executor()
        SharedState.speed_freeze_start(st)
        ex._focus_for_speed_freeze()                 # 札: EDITOR
        self.front = 0x901                           # 利用者が別の窓へ
        SharedState.equip_freeze_start(st)
        ex._show_item_loss()                         # 札: 0x901（持たない）

        SharedState.speed_freeze_end(st)
        SharedState.equip_freeze_end(st)

        self.assertEqual(self.events.count(("focus", self.EDITOR)), 1, self.events)
        self.assertNotIn(("focus", 0x901), self.events)
        self.assertIsNone(st.front_loan)

    def test_giving_back_takes_the_action_lock(self):
        """前面化と同じ作法。ロックを持っている間は返しに行かない"""
        monitor = self._monitor()
        SharedState.continue_round_start(monitor.st)
        monitor._focus_this_window_for("続行ラウンド")
        started = []
        with patch.object(SharedState, "_start_give_back", side_effect=started.append):
            SharedState.continue_round_end(monitor.st)
        self.assertEqual(len(started), 1)
        self.assertEqual(self._returned(), [], "裏のスレッドに渡しただけ")

        acquired = []
        real_lock = SharedState._GLOBAL_ACTION_LOCK

        class Lock:
            def __enter__(self_inner):
                acquired.append("lock")
                return real_lock.__enter__()

            def __exit__(self_inner, *exc):
                return real_lock.__exit__(*exc)

        with patch.object(SharedState, "_GLOBAL_ACTION_LOCK", Lock()):
            SharedState._give_back(started[0])
        self.assertEqual(acquired, ["lock"])
        self.assertEqual(self._returned(), self.RETURNED)




class TestItemLostFreezeEveryWindow(unittest.TestCase):
    """ツールが Begin を押さない窓（グループ・public・自動Begin OFF）でも、アイテムロストの
    RoundOver で装備待ちフリーズ（前面化・音声1回・他窓を止める）。解除は装備（猶予の後）か
    ラウンド開始の早い方・インスタンス移動・停止。外れずに全窓が止まり続けないこと"""

    P = "2026.10.02 12:00:00 Debug      -  "
    KINDS = (("グループ（焼き芋）", config.INSTANCE_YAKIIMO, True),
             ("public", config.INSTANCE_PUBLIC, True),
             ("自動Begin OFF の private", config.INSTANCE_PRIVATE, False))

    def setUp(self):
        SharedState.equip_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)
        SharedState.set_hands_free(False)
        self.addCleanup(SharedState.set_hands_free, False)
        SharedState.set_item_begin_mode(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        self.play = patch.object(PlaySound, "play_sound").start()
        self.front = patch.object(WindowOperator, "borrow_front", return_value=(True, None)).start()
        patch.object(ConnectDB, "register_round").start()
        self.thread = patch.object(LogMonitor.threading, "Thread").start()
        self.addCleanup(patch.stopall)

    def _monitor(self, itype, auto_begin, item_id=0, lost=True):
        cfg = WindowConfig(hwnd=0x10, auto_begin=auto_begin, voice_item_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = itype
        monitor.st.in_round = True
        monitor.st.item_id = item_id or 7
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._start_daemon = lambda target, *args: target(*args)     # 解除の猶予はその場で
        if lost:
            monitor._mark_item_lost("リスポーン: アイテムロスト")
        return monitor

    def _line(self, monitor, body):
        with patch.object(LogMonitor.time, "sleep") as sleep:
            monitor._process(self.P + body)
        return sleep

    def _said(self, monitor, text):
        return any(text in m for m in monitor.logs)

    def test_round_over_freezes_with_focus_and_one_sound(self):
        for name, itype, auto in self.KINDS:
            SharedState.equip_freeze_reset()
            self.play.reset_mock()
            self.front.reset_mock()
            monitor = self._monitor(itype, auto)
            self._line(monitor, "RoundOver")
            self._line(monitor, "Verified Round End")
            self.assertTrue(monitor.st.equip_freeze_held, name)
            self.assertEqual(SharedState.get_equip_freeze_count(), 1, name)
            self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set(), f"{name}: 他窓を止める")
            self.front.assert_called_once_with(0x10)
            self.play.assert_called_once_with("lost.mp3")
            self.assertTrue(self._said(monitor, "RoundOver 【⚠ アイテムロスト → 全窓フリーズ（装備かラウンド開始で解除）】"),
                            (name, monitor.logs))

    def test_equipping_releases_after_the_delay_even_without_begin(self):
        for name, itype, auto in self.KINDS:
            SharedState.equip_freeze_reset()
            monitor = self._monitor(itype, auto)
            self._line(monitor, "RoundOver")
            self.assertFalse(monitor.st.begin_done, "Begin の受理は来ていない")
            sleep = self._line(monitor, "Equipping 29.")
            sleep.assert_any_call(config.EQUIP_RELEASE_DELAY_SEC)
            self.assertFalse(monitor.st.equip_freeze_held, name)
            self.assertEqual(SharedState.get_equip_freeze_count(), 0, name)
            self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set(), name)
            self.assertTrue(self._said(monitor, "✅ アイテム装備 → フリーズ解除"), (name, monitor.logs))

    def test_a_round_start_releases_at_once(self):
        for name, itype, auto in self.KINDS:
            SharedState.equip_freeze_reset()
            monitor = self._monitor(itype, auto)
            self._line(monitor, "RoundOver")
            self._line(monitor, "This round is taking place at Sewers (12) and the round type is Classic")
            self.assertFalse(monitor.st.equip_freeze_held, name)
            self.assertEqual(SharedState.get_equip_freeze_count(), 0, name)
            self.assertTrue(self._said(monitor, "ラウンド開始 → 装備待ちフリーズ解除"), (name, monitor.logs))

    def test_a_round_start_during_the_equip_delay_releases_at_once(self):
        """装備した後の猶予の間にラウンドが始まったら、猶予を待たずに外す（早い方）"""
        monitor = self._monitor(config.INSTANCE_PUBLIC, True)
        monitor._start_daemon = lambda target, *args: None          # 猶予はまだ終わらない
        self._line(monitor, "RoundOver")
        self._line(monitor, "Equipping 29.")
        self.assertTrue(monitor.st.equip_freeze_held, "前提: 猶予の間")
        self._line(monitor, "This round is taking place at Sewers (12) and the round type is Classic")
        self.assertFalse(monitor.st.equip_freeze_held)
        with patch.object(LogMonitor.time, "sleep"):
            monitor._release_equip_freeze_after_equip()     # 猶予が後から終わっても
        self.assertFalse(self._said(monitor, "✅ アイテム装備 → フリーズ解除"), "二重に外さない")

    def test_moving_instance_releases(self):
        monitor = self._monitor(config.INSTANCE_YAKIIMO, True)
        self._line(monitor, "RoundOver")
        self._line(monitor, "[Behaviour] Joining wrld_b:2~private(usr_me)~region(jp)")
        self.assertFalse(monitor.st.equip_freeze_held)
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)
        self.assertTrue(self._said(monitor, "インスタンス移動 → 装備待ちフリーズ解除"))

    def test_stopping_releases(self):
        monitor = self._monitor(config.INSTANCE_PUBLIC, True)
        self._line(monitor, "RoundOver")
        self.assertEqual(SharedState.get_equip_freeze_count(), 1)
        app = MagicMock()
        app._stop_reason = None
        app.monitors = [monitor]
        with patch.object(Recorder, "stop_all"):
            mainGUI.App._stop(app)
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())

    def test_keeping_the_item_does_nothing(self):
        for name, itype, auto in self.KINDS:
            monitor = self._monitor(itype, auto, lost=False)
            self._line(monitor, "RoundOver")
            self.assertFalse(monitor.st.equip_freeze_held, name)
        self.play.assert_not_called()
        self.front.assert_not_called()

    def test_hands_free_does_nothing(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(config.INSTANCE_PRIVATE, False)
        self._line(monitor, "RoundOver")
        self.assertFalse(monitor.st.equip_freeze_held)
        self.play.assert_not_called()

    def test_begin_windows_are_as_before(self):
        """ツールが Begin を押す窓（自動Begin ON の private）は RoundOver では張らず、Begin の流れに任せる"""
        monitor = self._monitor(config.INSTANCE_PRIVATE, True)
        monitor._start_daemon = MagicMock()
        self._line(monitor, "RoundOver")
        self.assertFalse(monitor.st.equip_freeze_held)
        self.play.assert_not_called()
        self.assertEqual(monitor._start_daemon.call_args.args[0].__func__.__name__, "do_after_round")
        self.assertFalse(self._said(monitor, "全窓フリーズ（装備かラウンド開始で解除）"))

    def test_begin_windows_keep_their_equip_release(self):
        """Begin を押す窓の装備の解除は今どおり（装備＋Begin 受理）"""
        monitor = self._monitor(config.INSTANCE_PRIVATE, True)
        SharedState.equip_freeze_start(monitor.st)
        monitor.st.waiting_for_equip = True
        released = []
        monitor._start_daemon = lambda target, *args: released.append(target.__func__.__name__)
        self._line(monitor, "Equipping 29.")
        self.assertEqual(released, [], "Begin の受理がまだなので外さない")
        monitor.st.begin_done = True
        monitor.st.item_id = 0
        self._line(monitor, "Equipping 30.")
        self.assertEqual(released, ["_release_equip_wait_after_delay"])




class TestItemLossVoice(unittest.TestCase):
    """自動取得で取りに行くときも、アイテムロストの音はいつものタイミングで（音だけ・前面化なし）。
    アイテム取得→Begin モードは RoundOver、既定のモードは Begin が通った後（取りに行く前）。1ラウンド1回。
    取りに行って失敗・時間切れでは何もしない"""

    ITEMS = {29: ItemCatalog.Item("Taser", "Survival", True)}

    def setUp(self):
        p = patch.object(config, "ITEMS", self.ITEMS)
        p.start()
        self.addCleanup(p.stop)
        SharedState.set_item_fetch(True)
        self.addCleanup(SharedState.set_item_fetch, False)
        SharedState.set_item_begin_mode(False)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        SharedState.set_hands_free(False)
        self.addCleanup(SharedState.set_hands_free, False)
        SharedState.equip_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)
        self.sounds = []
        self.focus = []
        for p in (patch.object(PlaySound, "play_sound", side_effect=self.sounds.append),
                  patch.object(WindowOperator, "borrow_front",
                               side_effect=lambda h: self.focus.append(h) or (True, None)),
                  patch.object(WindowOperator, "focus_window", side_effect=lambda h: self.focus.append(h) or True)):
            p.start()
            self.addCleanup(p.stop)

    def _monitor(self, lost=True):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10, osc_port=9000, auto_begin=True,
                                                     voice_item_lost="lost.mp3"),
                                        {}, lambda _m: None, window_idx=1)
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._start_daemon = lambda *a: None
        st.in_round = True
        st.held_item_id = 29
        if lost:
            st.item_id = 29
            monitor._mark_item_lost("リスポーン: アイテムロスト")
            monitor._lose_held_item("リスポーン")
        else:                                   # 前のラウンドでロストして未回収
            monitor._lose_held_item("リスポーン")
            st.item_id = 0
        return monitor

    def _round_over(self, monitor):
        monitor._process("2026.10.03 22:10:00 Debug      -  RoundOver")

    def test_item_begin_mode_is_as_before_without_fetching(self):
        """アイテム取得→Begin モード＋自動取得 ON: 自動取得を動かさない。RoundOver は昔どおり
        音・前面化・フリーズ（依頼者「このモードで自動取得が動くのは明らかなミス」）"""
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor()
        self.assertIsNone(monitor._action.item_fetch_target(), "取りに行かない")
        self._round_over(monitor)
        self.assertEqual(self.sounds, ["lost.mp3"], "音")
        self.assertEqual(self.focus, [0x10], "前面化")
        self.assertTrue(monitor.st.equip_freeze_held, "フリーズ")
        self.assertTrue(any("RoundOver 【⚠ アイテムロスト → 全窓フリーズ開始】" in m for m in monitor.logs),
                        monitor.logs)

    def test_item_begin_mode_does_not_fetch_an_unrecovered_item_either(self):
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor(lost=False)
        self.assertIsNone(monitor._action.item_fetch_target())
        with patch.object(monitor._action, "_fetch_item") as fetch, \
             patch.object(monitor._action, "_attend_to_item_loss",
                          side_effect=lambda: setattr(monitor.st, "waiting_for_equip", False)) as attend:
            self._after_round(monitor, "ok")
        fetch.assert_not_called()
        attend.assert_called_once_with()

    def test_the_default_mode_is_silent_at_round_over(self):
        """既定のモードは RoundOver では鳴らさない（続行ラウンドの途中で鳴らないように。今どおり）"""
        monitor = self._monitor()
        self._round_over(monitor)
        self.assertEqual(self.sounds, [])
        self.assertEqual(self.focus, [])

    def _after_round(self, monitor, outcome):
        """Begin まで回す（_fetch_item は偽物。呼ばれたら order に残す）"""
        ex, st = monitor._action, monitor.st
        st.in_round = False
        st.round_end_seen = True
        st.waiting_for_equip = True
        order = []

        def press(*_a, **_kw):
            order.append("press")
            st.begin_done = True
            return True

        def fetch(round_seq, shop, item_id):
            order.append(("fetch", list(self.sounds)))
            st.waiting_for_equip = False
            return outcome
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ex, "_begin_move"), patch.object(ex, "_start_use_spam", return_value=None), \
             patch.object(ex, "_wait_round_end", return_value=True), \
             patch.object(ex, "_handle_item_lost", return_value=True), \
             patch.object(ex, "_wait_other_windows", return_value=True), \
             patch.object(ex, "_begin_precheck", return_value=True), \
             patch.object(ex, "_press_begin", side_effect=press), patch.object(ex, "_confirm_begin"), \
             patch.object(ex, "_fetch_item", side_effect=fetch), \
             patch.object(ActionExecutor.threading, "Thread"), \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_after_round()
        return order

    def test_the_default_mode_rings_after_the_begin_before_fetching(self):
        for lost in (True, False):
            self.sounds.clear()
            monitor = self._monitor(lost=lost)
            order = self._after_round(monitor, "ok")
            self.assertEqual(order, ["press", ("fetch", ["lost.mp3"])], f"lost={lost}: 取りに行く前に鳴った")
            self.assertEqual(self.sounds, ["lost.mp3"], "1回")
            self.assertEqual(self.focus, [], "前面化しない")

    def test_failure_or_timeout_gives_nothing(self):
        for outcome in ("failed", "timeout", "ok"):
            self.sounds.clear()
            monitor = self._monitor()
            with patch.object(monitor._action, "_attend_to_item_loss") as attend:
                self._after_round(monitor, outcome)
            attend.assert_not_called()
            self.assertEqual(self.sounds, ["lost.mp3"], f"{outcome}: 取りに行く前の1回だけ")
            self.assertEqual(self.focus, [], outcome)
            self.assertFalse(monitor.st.equip_freeze_held, f"{outcome}: フリーズも張らない")

    def test_without_fetch_it_is_as_before(self):
        SharedState.set_item_fetch(False)
        monitor = self._monitor()
        with patch.object(monitor._action, "_attend_to_item_loss",
                          side_effect=lambda: setattr(monitor.st, "waiting_for_equip", False)) as attend:
            self._after_round(monitor, None)
        attend.assert_called_once_with()
        self.assertEqual(self.sounds, [], "音は _attend_to_item_loss（前面化と一緒）から")

    def test_it_rings_once_per_round(self):
        monitor = self._monitor()
        self._after_round(monitor, "failed")
        monitor._action.announce_item_lost_once()     # 同じラウンドでもう一度
        self.assertEqual(self.sounds, ["lost.mp3"], "1ラウンド1回")

    def test_hands_free_rings_nothing(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor()
        self._round_over(monitor)
        monitor._action.announce_item_lost_once()
        self.assertEqual(self.sounds, [])
        self.assertEqual(self.focus, [])




class TestItemLostAnnounceTiming(unittest.TestCase):
    """アイテムロストの通知は、Begin を押す直前には鳴らさない。

    音声は前面化と一緒にしか出さない（_attend_to_item_loss）。押す前に
    前面化すると VRChat がカーソルを掴み、カーソル方式 Begin が壊れるので、
    前面化と音声は押した後（受理されてから）になる
    """

    def setUp(self):
        _single_attempt(self)
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_item_begin_mode(False)

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_item_begin_mode(False)

    def _order_of_actions(self, osc_port: int) -> list[str]:
        """Verified Round End でロストが判明する流れの操作順を記録する"""
        cfg = WindowConfig(hwnd=123, osc_port=osc_port, voice_item_lost="lost.mp3")
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=False,   # RoundOver時点ではまだ判明していない
            round_end_seen=False,
            item_id=1,
        )
        order: list[str] = []

        def fake_sleep(_sec):
            # Round End 待ちの最中に Verified Round End が届く
            st.round_end_seen = True
            st.waiting_for_equip = True

        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor, "time", _module_time(fake_sleep)),              patch.object(ActionExecutor.SharedState, "equip_freeze_start",
                          side_effect=lambda state: order.append("freeze")),              patch.object(ActionExecutor.PlaySound, "play_sound",
                          side_effect=lambda _p: order.append("sound")),              patch.object(executor, "move",
                          side_effect=lambda d, sec: order.append("move")),              patch.object(executor, "move_forward_left",
                          side_effect=lambda f, l: order.append("move")),              patch.object(WindowOperator, "focus_window",
                          side_effect=lambda _h: order.append("focus") or True),              patch.object(WindowOperator, "click",
                          side_effect=lambda: order.append("click")):
            executor.do_after_round()
        return order

    def test_no_announce_right_before_the_click_osc(self):
        """OSC窓: 移動・フリーズの後、鳴らさずに押す

        カーソルを置けない窓（このテストでは矩形を作らない）は従来どおり
        前面化＋クリックへ落ちる。受理されないので、押した後にも鳴らない
        """
        order = self._order_of_actions(osc_port=9000)

        self.assertEqual(order, ["move", "freeze", "focus", "click"])

    def test_no_announce_right_before_the_click_no_osc(self):
        """非OSC窓も背面送信になったので、OSC窓と同じ順になる"""
        order = self._order_of_actions(osc_port=0)

        self.assertEqual(order, ["move", "freeze", "focus", "click"])


    def test_announce_is_skipped_when_item_is_kept(self):
        """アイテムを持ったまま終わったラウンドでは鳴らさない"""
        cfg = WindowConfig(hwnd=123, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=5)
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor, "time", _module_time(lambda _s: None)),              patch.object(ActionExecutor.PlaySound, "play_sound") as mock_play,              patch.object(executor, "move"),              patch.object(executor, "move_forward_left"),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click") as mock_click:
            executor.do_after_round()

        mock_click.assert_called_once()
        mock_play.assert_not_called()

    def test_item_begin_mode_does_not_announce_while_waiting_to_equip(self):
        """アイテム取得→Beginモードの「拾ってきて」の合図は、RoundOver の
        _attend_to_item_loss() で前面化と一緒に出す。装備を待つ処理からは
        もう鳴らさない（二重に鳴らさない）
        """
        SharedState.set_item_begin_mode(True)
        cfg = WindowConfig(hwnd=123, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         waiting_for_equip=True, item_id=0, round_end_seen=True)
        SharedState.equip_freeze_start(st)
        order: list[str] = []
        sleeps = {"n": 0}

        def fake_sleep(_sec):
            sleeps["n"] += 1
            if sleeps["n"] >= 3:
                st.item_id = 5      # プレイヤーが装備した

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor, "time", _module_time(fake_sleep)),              patch.object(ActionExecutor.PlaySound, "play_sound",
                          side_effect=lambda _p: order.append("sound")),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click",
                          side_effect=lambda: order.append("click")):
            executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
            with patch.object(executor, "move"), patch.object(executor, "move_forward_left"):
                executor.do_after_round()

        self.assertEqual(order, ["click"], "装備待ちからは鳴らさない")




class TestOscMoveDuringFreeze(unittest.TestCase):
    """全窓フリーズ中でもOSC移動は行い、フォーカスを要するクリックだけ待つ

    OSCはフォーカスを奪わないので他窓の操作を妨げない。フリーズ中に
    移動だけ済ませておけば、解除された瞬間にBeginを押せる。
    """

    def setUp(self):
        _single_attempt(self)
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_item_begin_mode(False)

    def _executor(self, st, osc_port=9000):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def _state(self):
        # Beginは Verified Round End の後にしか押さないので実機同様に立てる
        return WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True)

    def _run_frozen_begin(self, release, osc_port=9000):
        """フリーズ中に do_after_round を走らせ、移動とクリックの前後を観測する。

        release() を呼ぶまでフリーズは解除されない。
        戻り値: (moves, moved, clicked, thread)
        """
        st = self._state()
        ex = self._executor(st, osc_port=osc_port)
        moves: list[str] = []
        moved = threading.Event()
        clicked = threading.Event()

        def on_move(direction, seconds):
            moves.append(direction)
            moved.set()

        def on_move_fl(forward_sec, left_sec):
            # 前進を通しで押し、頭だけ左を重ねる（斜め→直進）1回の移動
            moves.append("forward+left")
            moved.set()

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ex, "move", side_effect=on_move), patch.object(ex, "move_forward_left", side_effect=on_move_fl),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click", side_effect=clicked.set):
            t = threading.Thread(target=ex.do_after_round, daemon=True)
            t.start()
            observed_move = moved.wait(3.0)
            clicked_while_frozen = clicked.wait(0.6)
            release()
            clicked_after_release = clicked.wait(3.0)
            t.join(timeout=3.0)
        return moves, observed_move, clicked_while_frozen, clicked_after_release

    def test_begin_move_runs_during_continue_freeze_and_click_waits(self):
        """他窓が続行ラウンド中でも、Begin前移動は進みクリックだけ待つ"""
        release = _freeze_other_continue()
        moves, observed_move, clicked_while_frozen, clicked_after_release =             self._run_frozen_begin(release)

        self.assertTrue(observed_move, "フリーズ中でもOSC移動は行うこと")
        self.assertEqual(moves, ["forward+left"])
        self.assertFalse(clicked_while_frozen, "フリーズ中にクリックしてはいけない")
        self.assertTrue(clicked_after_release, "解除後はクリックすること")

    def test_begin_move_runs_during_equip_freeze_and_click_waits(self):
        """他窓がアイテムロスト装備待ちでも、Begin前移動は進みクリックだけ待つ"""
        other = WindowState()
        SharedState.equip_freeze_start(other)
        moves, observed_move, clicked_while_frozen, clicked_after_release =             self._run_frozen_begin(lambda: SharedState.equip_freeze_end(other))

        self.assertTrue(observed_move, "フリーズ中でもOSC移動は行うこと")
        self.assertEqual(moves, ["forward+left"])
        self.assertFalse(clicked_while_frozen, "フリーズ中にクリックしてはいけない")
        self.assertTrue(clicked_after_release, "解除後はクリックすること")

    def test_a_keyboard_window_also_moves_during_the_freeze(self):
        """背面送信になったので、非OSC窓もフリーズ中に移動してよい。

        待つのはクリックの直前だけ（フォーカスを取るのはそこだけ）
        """
        release = _freeze_other_continue()
        moves, observed_move, clicked_while_frozen, clicked_after_release =             self._run_frozen_begin(release, osc_port=0)

        self.assertTrue(observed_move, "フリーズ中でも移動する")
        self.assertFalse(clicked_while_frozen, "クリックは解除まで待つ")
        self.assertTrue(clicked_after_release)
        self.assertEqual(moves, ["forward+left"])




class TestFreezeSettings(unittest.TestCase):
    """フリーズ設定は全窓共通（フリーズ自体が全窓を止めるため）"""

    def setUp(self):
        SharedState.set_freeze_on_8pages(False)
        SharedState.set_freeze_on_punish(False)
        SharedState.set_freeze_rounds(())

    tearDown = setUp

    def test_flags_round_trip(self):
        SharedState.set_freeze_on_8pages(True)
        SharedState.set_freeze_on_punish(True)

        self.assertTrue(SharedState.get_freeze_on_8pages())
        self.assertTrue(SharedState.get_freeze_on_punish())

    def test_rounds_round_trip(self):
        SharedState.set_freeze_rounds(["Alternate", "Ghost"])

        self.assertEqual(SharedState.get_freeze_rounds(), {"Alternate", "Ghost"})

    def test_rounds_accepts_any_iterable(self):
        SharedState.set_freeze_rounds(name for name in ("Midnight",))

        self.assertEqual(SharedState.get_freeze_rounds(), {"Midnight"})

    def test_getter_returns_a_copy(self):
        """返した set を書き換えても内部状態は変わらない"""
        SharedState.set_freeze_rounds(["Alternate"])

        got = SharedState.get_freeze_rounds()
        got.add("Unbound")
        got.discard("Alternate")

        self.assertEqual(SharedState.get_freeze_rounds(), {"Alternate"})

    def test_checkbox_order_follows_the_config_list(self):
        """ソートせず ROUND_FREEZE_SELECTABLE の順序で並べる"""
        made = mainGUI.freeze_round_vars(lambda: object())

        self.assertEqual(list(made), list(config.ROUND_FREEZE_SELECTABLE))




class TestFreezeSettingsMigration(unittest.TestCase):
    """旧形式（窓ごとの配列）の settings.json も読めること"""

    def test_flag_array_is_any(self):
        self.assertTrue(mainGUI._as_flag([False, True, False]))
        self.assertFalse(mainGUI._as_flag([False, False]))

    def test_flag_scalar_and_missing(self):
        self.assertTrue(mainGUI._as_flag(True))
        self.assertFalse(mainGUI._as_flag(None))
        self.assertFalse(mainGUI._as_flag([]))

    def test_round_arrays_are_unioned(self):
        value = [["Alternate"], [], ["Ghost", "Alternate"], ["Midnight"]]

        self.assertEqual(mainGUI._as_round_names(value),
                         {"Alternate", "Ghost", "Midnight"})

    def test_new_format_flat_list(self):
        self.assertEqual(mainGUI._as_round_names(["Alternate", "Punished"]),
                         {"Alternate", "Punished"})

    def test_missing_or_broken_falls_back_to_empty(self):
        self.assertEqual(mainGUI._as_round_names(None), set())
        self.assertEqual(mainGUI._as_round_names([]), set())
        self.assertEqual(mainGUI._as_round_names([None, 5]), set())




class TestSpeedFreeze(unittest.TestCase):
    """速度検知で 8 Pages / Punished を掴んだら全窓を止める"""

    def setUp(self):
        SharedState.speed_freeze_reset()
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_freeze_on_8pages(False)
        SharedState.set_freeze_on_punish(False)

    tearDown = setUp

    def _executor(self):
        cfg = WindowConfig(hwnd=123, osc_port=9000,
                           voice_8pages="8p.mp3", voice_punish="pn.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None), st

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _detect(self, ex, kind):
        with patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window", return_value=True):
            ex._announce_speed_kind(kind, 6.5)

    def test_eight_pages_freezes_when_enabled(self):
        SharedState.set_freeze_on_8pages(True)
        ex, st = self._executor()

        self._detect(ex, "8pages")

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertTrue(st.speed_freeze_held)
        self.assertEqual(st.speed_freeze_kind, "8pages")

    def test_eight_pages_does_not_freeze_when_disabled(self):
        ex, st = self._executor()

        self._detect(ex, "8pages")

        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertFalse(st.speed_freeze_held)

    def test_punish_freezes_when_enabled(self):
        SharedState.set_freeze_on_punish(True)
        ex, st = self._executor()

        self._detect(ex, "punish")

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertEqual(st.speed_freeze_kind, "punish")

    def test_normal_never_freezes(self):
        SharedState.set_freeze_on_8pages(True)
        SharedState.set_freeze_on_punish(True)
        ex, st = self._executor()

        self._detect(ex, "normal")

        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertFalse(st.speed_freeze_held)

    def test_first_window_is_brought_to_front(self):
        SharedState.set_freeze_on_8pages(True)
        ex, _st = self._executor()

        with patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window", return_value=True) as mock_focus:
            ex._announce_speed_kind("8pages", 6.5)

        mock_focus.assert_called_once_with(123)

    def test_second_window_does_not_steal_focus(self):
        SharedState.set_freeze_on_8pages(True)
        other = WindowState()
        SharedState.speed_freeze_start(other)
        ex, st = self._executor()

        with patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window") as mock_focus:
            ex._announce_speed_kind("8pages", 6.5)

        mock_focus.assert_not_called()
        self.assertTrue(st.speed_freeze_held, "フリーズ自体は張ること")

    def test_item_equip_releases_eight_pages_freeze(self):
        """猶予を置いてから解除する（アイテムロストの装備解除と同じ間）"""
        monitor = self._monitor()
        monitor._running = True
        monitor.st.speed_freeze_kind = "8pages"
        SharedState.speed_freeze_start(monitor.st)

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("Equipping 42.")
        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set(),
                         "取った瞬間には解除しない")

        with patch.object(LogMonitor.time, "sleep") as sleep:
            monitor._release_speed_freeze_after_delay(monitor.st.round_seq)

        sleep.assert_called_once_with(config.EQUIP_RELEASE_DELAY_SEC)
        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())

    def test_item_equip_does_not_release_punish_freeze(self):
        monitor = self._monitor()
        monitor.st.speed_freeze_kind = "punish"
        SharedState.speed_freeze_start(monitor.st)

        monitor._process("Equipping 42.")

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())

    def test_round_start_releases_any_freeze(self):
        """保険: アイテムを取らないままラウンドが始まっても必ず解除する"""
        for kind in ("8pages", "punish"):
            SharedState.speed_freeze_reset()
            monitor = self._monitor()
            monitor.st.speed_freeze_kind = kind
            SharedState.speed_freeze_start(monitor.st)

            with patch.object(ConnectDB, "register_round"), \
             patch.object(LogMonitor.threading, "Thread"):
                monitor._process("This round is taking place at Facility (12) "
                                 "and the round type is Classic")

            self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set(), kind)
            self.assertFalse(monitor.st.speed_freeze_held, kind)

    def test_two_windows_hold_the_freeze_independently(self):
        a, b = WindowState(), WindowState()
        SharedState.speed_freeze_start(a)
        SharedState.speed_freeze_start(b)

        SharedState.speed_freeze_end(a)
        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set(), "まだ止まっていること")

        SharedState.speed_freeze_end(b)
        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())

    def test_the_window_that_froze_does_not_wait_for_itself(self):
        SharedState.set_freeze_on_8pages(True)
        ex, _st = self._executor()
        self._detect(ex, "8pages")

        self.assertTrue(ex._wait_other_windows(), "自分が張ったフリーズで詰まらない")




class TestRoundFreezeVoice(unittest.TestCase):
    """ラウンド突入でフリーズを選んだラウンドに入ったら、そのラウンドの音声を鳴らす"""

    ROUND_LINE = ("This round is taking place at Facility (12) "
                  "and the round type is %s")
    VOICES = {"Fog": "fog.mp3", "Unbound": "unbound.mp3", "Midnight": "midnight.mp3",
              "Alternate": "alternate.mp3", "Ghost": "ghost.mp3"}

    def setUp(self):
        self._reset()
        self.addCleanup(self._reset)

    @staticmethod
    def _reset():
        SharedState.round_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_freeze_rounds(())

    def _monitor(self, rounds, **voices):
        SharedState.set_freeze_rounds(rounds)
        cfg = WindowConfig(voice_fog="fog.mp3", voice_unbound="unbound.mp3",
                           voice_midnight="midnight.mp3",
                           voice_alternate="alternate.mp3", voice_ghost="ghost.mp3",
                           voice_punish="punish.mp3", voice_8pages="8pages.mp3")
        for name, value in voices.items():
            setattr(cfg, name, value)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _enter(self, monitor, round_type):
        with patch.object(ConnectDB, "register_round"), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as played:
            monitor._process(self.ROUND_LINE % round_type)
        return [c.args[0] for c in played.call_args_list]

    def test_each_chosen_round_plays_its_own_voice(self):
        for round_type, voice in self.VOICES.items():
            self._reset()
            monitor = self._monitor([round_type])

            self.assertEqual(self._enter(monitor, round_type), [voice], round_type)

    def test_an_unchosen_round_is_silent(self):
        monitor = self._monitor(["Unbound"])

        self.assertEqual(self._enter(monitor, "Ghost"), [])

    def test_punished_and_eight_pages_are_not_announced(self):
        """依頼の5つに入っていない（その音声は速度検知用）"""
        for round_type in ("Punished", "8 Pages"):
            self._reset()
            monitor = self._monitor([round_type])

            self.assertEqual(self._enter(monitor, round_type), [], round_type)
            self.assertTrue(monitor.st.round_freeze_held, "フリーズ自体は張る")

    def test_hands_free_is_silent(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(["Unbound"])

        self.assertEqual(self._enter(monitor, "Unbound"), [])

    def test_an_empty_path_is_silent(self):
        monitor = self._monitor(["Midnight"], voice_midnight="")

        self.assertEqual(self._enter(monitor, "Midnight"), [])

    # ── 霧は二重に鳴らさない ───────────────────
    def test_fog_plays_once_even_with_the_entry_announcement_on(self):
        monitor = self._monitor(["Fog"])

        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True):
            played = self._enter(monitor, "Fog")

        self.assertEqual(played, ["fog.mp3"])

    def test_fog_unchosen_stays_silent_by_default(self):
        monitor = self._monitor([])

        self.assertEqual(self._enter(monitor, "Fog"), [])

    def test_fog_unchosen_still_uses_the_entry_announcement(self):
        monitor = self._monitor([])

        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True):
            self.assertEqual(self._enter(monitor, "Fog"), ["fog.mp3"])

    # ── 設定 ─────────────────────────────
    def test_the_voice_files_are_bundled(self):
        for path in (config.VOICE_UNBOUND, config.VOICE_MIDNIGHT,
                     config.VOICE_ALTERNATE, config.VOICE_GHOST, config.VOICE_FOG):
            self.assertTrue(Path(path).exists(), path)

    def test_the_gui_has_a_row_and_hands_it_to_the_monitor(self):
        gui = Path("mainGUI.py").read_text(encoding="utf-8")
        for name in ("unbound", "midnight", "alternate", "ghost"):
            self.assertIn(f"self.v_voice_{name}", gui, name)
            self.assertIn(f"cfg.voice_{name}", gui, name)
            self.assertIn(f"config.VOICE_{name.upper()}", gui, name)

    def test_the_window_config_defaults_to_silence(self):
        cfg = WindowConfig()
        for name in ("unbound", "midnight", "alternate", "ghost"):
            self.assertEqual(getattr(cfg, f"voice_{name}"), "", name)




class TestRoundFreeze(unittest.TestCase):
    """指定ラウンドに突入したら全窓を止める（張った窓自身は自爆できる）"""

    ROUND_LINE = ("This round is taking place at Facility (12) "
                  "and the round type is %s")

    def setUp(self):
        SharedState.round_freeze_reset()
        SharedState.speed_freeze_reset()
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_freeze_rounds(())

    tearDown = setUp

    def _monitor(self, rounds=("Alternate",)):
        SharedState.set_freeze_rounds(rounds)
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _start_round(self, monitor, round_type):
        with patch.object(ConnectDB, "register_round"), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process(self.ROUND_LINE % round_type)

    def test_selected_round_freezes_every_window(self):
        monitor = self._monitor(["Alternate"])

        self._start_round(monitor, "Alternate")

        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertTrue(monitor.st.round_freeze_held)

    def test_unselected_round_does_not_freeze(self):
        monitor = self._monitor(["Alternate"])

        self._start_round(monitor, "Classic")

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())

    def test_all_six_selectable_rounds_work(self):
        for name in config.ROUND_FREEZE_SELECTABLE:
            SharedState.round_freeze_reset()
            monitor = self._monitor([name])

            self._start_round(monitor, name)

            self.assertTrue(monitor.st.round_freeze_held, name)

    def test_freeze_happens_without_waiting_for_the_killers(self):
        monitor = self._monitor(["Midnight"])

        self._start_round(monitor, "Midnight")

        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertEqual(monitor.st.terror_ids, [], "テラーはまだ判明していない")

    def test_the_window_that_froze_can_still_suicide(self):
        """★自窓の自爆は止めない。止めるのは他窓だけ"""
        _single_attempt(self)
        cfg = WindowConfig(hwnd=123, do_skip=True)
        st = WindowState(in_round=True, round_freeze_held=True)
        SharedState.ROUND_FREEZE_EVENT.clear()
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
        held = []

        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda h, k, sec, stop=None: held.append(k) or True):
            ex.do_skip()

        self.assertTrue(held, "自窓は待たずに自爆すること")

    def test_next_round_releases_even_without_dying(self):
        """保険: 死なずにラウンドが終わっても永久フリーズしない"""
        monitor = self._monitor(["Alternate"])
        self._start_round(monitor, "Alternate")

        self._start_round(monitor, "Classic")

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertFalse(monitor.st.round_freeze_held)

    def test_death_releases_after_the_delay(self):
        monitor = self._monitor(["Alternate"])
        self._start_round(monitor, "Alternate")
        monitor._running = True

        with patch.object(LogMonitor.time, "sleep") as mock_sleep:
            monitor._release_round_freeze_after_delay(monitor.st.round_seq)

        mock_sleep.assert_called_once_with(config.FOG_FREEZE_RELEASE_DELAY_SEC)
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())

    def test_two_windows_hold_it_independently(self):
        a, b = WindowState(), WindowState()
        SharedState.round_freeze_start(a)
        SharedState.round_freeze_start(b)

        SharedState.round_freeze_end(a)
        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())

        SharedState.round_freeze_end(b)
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())

    def test_reset_forces_release(self):
        st = WindowState()
        SharedState.round_freeze_start(st)

        SharedState.round_freeze_reset()

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertEqual(SharedState.get_round_freeze_count(), 0)

    def test_hands_free_does_not_freeze(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(["Alternate"])

        self._start_round(monitor, "Alternate")

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())




class TestEquipFreezeCounter(unittest.TestCase):
    """装備待ちフリーズのカウンタ管理（複数窓同時アイテムロスト対応）"""

    def setUp(self):
        SharedState.equip_freeze_reset()

    def tearDown(self):
        SharedState.equip_freeze_reset()

    def test_freeze_persists_until_all_windows_release(self):
        """2窓同時ロスト時、片方の解除だけでは全窓フリーズを解除しない"""
        st_a = WindowState()
        st_b = WindowState()
        SharedState.equip_freeze_start(st_a)
        SharedState.equip_freeze_start(st_b)
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(SharedState.get_equip_freeze_count(), 2)

        SharedState.equip_freeze_end(st_a)
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())  # ← Bの装備待ちが残っている

        SharedState.equip_freeze_end(st_b)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)

    def test_double_start_and_end_are_idempotent(self):
        """同一窓の多重登録・多重解除はカウントに影響しない"""
        st = WindowState()
        SharedState.equip_freeze_start(st)
        SharedState.equip_freeze_start(st)
        self.assertEqual(SharedState.get_equip_freeze_count(), 1)

        SharedState.equip_freeze_end(st)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())
        SharedState.equip_freeze_end(st)
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)

    def test_end_without_start_is_noop(self):
        """フリーズ未保持の窓の解除呼び出しは他窓のフリーズに影響しない"""
        holder = WindowState()
        bystander = WindowState()
        SharedState.equip_freeze_start(holder)

        SharedState.equip_freeze_end(bystander)
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(SharedState.get_equip_freeze_count(), 1)




class TestLogMonitorItemLostVoice(unittest.TestCase):
    def setUp(self):
        # RoundOver でも装備待ちフリーズを張るので、前のテストの分を残さない
        SharedState.equip_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")   # グループ判定は主催リストが前提
        self._stats_patcher = patch.object(ConnectDB, "register_round")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, auto_begin: bool = False):
        cfg = WindowConfig(
            auto_begin=auto_begin,
            voice_item_lost="lost.mp3",
        )
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def test_hands_free_auto_begin_defers_voice_to_the_begin_click(self):
        """放置モード+自動Begin: Round Endでは鳴らさず、Beginクリック時に鳴らす"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(auto_begin=True)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play,              patch.object(ConnectDB, "register_round"),              patch.object(LogMonitor.threading, "Thread"):
            monitor._process("You died.")
            monitor._process("RoundOver")
            monitor._process("Verified Round End")

        mock_play.assert_not_called()
        self.assertFalse(monitor.st.item_lost_announced)
        # Beginクリック時の判定材料は残っていること
        self.assertTrue(monitor.st.item_lost_this_round)
        self.assertEqual(monitor.st.item_id, 0)

    def test_item_lost_voice_does_not_play_on_verified_end_when_auto_begin_disabled(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "register_round"):
            monitor._process("Verified Round End")

        mock_play.assert_not_called()
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)

    def test_item_lost_voice_plays_on_round_over_when_auto_begin_disabled(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "register_round"):
            monitor._process("You died.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertTrue(monitor.st.item_lost_announced)

    def test_run_survival_does_not_play_item_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "register_round"):
            monitor._process("Lived in round.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 7)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)
        self.assertTrue(monitor.st.lived_this_round)

    def test_run_death_marks_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        monitor._process("You died.")

        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)
        self.assertTrue(monitor.st.died_this_round)

    def test_run_without_death_does_not_play_item_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 7)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)

    def test_item_lost_voice_plays_on_round_over_without_verified_end(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("You died.")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.item_lost_announced)

    def test_death_does_not_mark_item_lost_by_itself(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Classic"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("You died.")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 7)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.died_this_round)
        self.assertFalse(monitor.st.item_equipped_after_death)

    def test_item_equip_after_death_prevents_round_over_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("You died.")
            monitor._process("Equipping 42.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 42)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.item_equipped_after_death)

    def test_sabotage_sus_player_self_marks_item_lost_on_round_start(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Sus player = 5 serim01")
            monitor._process("This round is taking place at Cheese Maze (59) and the round type is Sabotage")
            self.assertEqual(monitor.st.item_id, 0)
            self.assertTrue(monitor.st.sabotage_murder_this_round)
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertTrue(monitor.st.waiting_for_equip)

    def test_sabotage_sus_player_second_slot_can_mark_self(self):
        monitor = self._monitor(auto_begin=False)
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        monitor._process("Sus player = 5 other")
        monitor._process("Sus player 2 = 13 serim01")
        monitor._process("This round is taking place at Ancient (18) and the round type is Sabotage")

        self.assertTrue(monitor.st.sabotage_murder_this_round)
        self.assertEqual(monitor.st.item_id, 0)

    def test_sabotage_sus_player_other_does_not_mark_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Sus player = 5 other")
            monitor._process("This round is taking place at Cheese Maze (59) and the round type is Sabotage")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 10)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.sabotage_murder_this_round)

    def test_sabotage_murder_re_equip_prevents_round_over_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Sus player = 5 serim01")
            monitor._process("This round is taking place at Cheese Maze (59) and the round type is Sabotage")
            monitor._process("Equipping 42.")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 42)
        self.assertFalse(monitor.st.waiting_for_equip)

    def test_punished_marks_item_lost_on_round_start(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10

        monitor._process("This round is taking place at Astral (13) and the round type is Punished")

        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)

    def test_eight_pages_kept_item_does_not_mark_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10

        with patch.object(config, "EIGHT_PAGES_KEEP_ITEM_IDS", {10}):
            monitor._process("This round is taking place at Warehouse (0) and the round type is 8 Pages")

        self.assertEqual(monitor.st.item_id, 10)
        self.assertFalse(monitor.st.item_lost_this_round)

    def test_respawn_marks_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.in_round = True
        monitor.st.item_id = 10

        monitor._process("Player respawned, opted out!")
        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)

    def test_randomizer_item_change_warns_without_marking_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 41
        monitor._process("This round is taking place at Secret (5) and the round type is Randomizer")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Equipping 94. Was using 41")
            monitor._process("Verified Round End")
            self.assertEqual(monitor.st.item_id, 94)
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertEqual(monitor.st.item_id, 94)
        self.assertTrue(monitor.st.randomizer_item_changed)
        self.assertFalse(monitor.st.item_lost_this_round)
        self.assertTrue(monitor.st.waiting_for_equip)

    def test_randomizer_restoring_original_item_clears_warning(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 41
        monitor._process("This round is taking place at Secret (5) and the round type is Randomizer")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Equipping 94. Was using 41")
            monitor._process("Equipping 41. Was using 94")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 41)
        self.assertFalse(monitor.st.randomizer_item_changed)
        self.assertFalse(monitor.st.waiting_for_equip)

    def test_auto_begin_item_lost_voice_waits_until_begin_action(self):
        monitor = self._monitor(auto_begin=True)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "register_round"), \
             patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("You died.")
            monitor._process("RoundOver")          # Begin処理はここで起動する
            monitor._process("Verified Round End") # ロスト判定はここ

        mock_play.assert_not_called()
        started = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]
        # Begin 処理と、Verified Round End からの速度検知だけ。音声は出さない
        self.assertEqual(sorted(started), ["do_after_round", "do_speed_detect"])
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)

    def test_round_end_flag_gates_the_click(self):
        """クリックは Verified Round End を待つ。移動だけ先に進む。"""
        monitor = self._monitor(auto_begin=True)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"

        with patch.object(ConnectDB, "register_round"),              patch.object(LogMonitor.threading, "Thread"):
            monitor._process("RoundOver")
            self.assertFalse(monitor.st.round_end_seen,
                             "RoundOver時点ではまだクリックできない")
            monitor._process("Verified Round End")
            self.assertTrue(monitor.st.round_end_seen,
                            "Round Endでクリック可になる")

    def test_round_over_time_is_recorded(self):
        """Begin待ちの起点として RoundOver の時刻を記録する。

        RoundOverから待機し、待ち終わる頃に Verified Round End が出て
        クリックできる状態になる、という組み立てのため。
        """
        monitor = self._monitor(auto_begin=True)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"

        with patch.object(ConnectDB, "register_round"),              patch.object(LogMonitor.threading, "Thread"):
            monitor._process("RoundOver")
        self.assertGreater(monitor.st.round_over_time, 0,
                           "RoundOverの時刻を記録すること（Begin待ちの起点）")

    def test_yakiimo_plays_item_lost_voice_on_round_over_with_auto_begin_enabled(self):
        monitor = self._monitor(auto_begin=True)
        monitor.st.instance_type = config.INSTANCE_YAKIIMO
        monitor.st.in_round = True
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play,              patch.object(LogMonitor.threading, "Thread"):     # 本物の Begin のスレッドを残さない
            monitor._process("You died.")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.item_lost_announced)

    def test_item_lost_voice_is_not_duplicated_by_round_over(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "register_round"):
            monitor._process("You died.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")

    def test_round_start_resets_item_lost_voice_announcement(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_lost_announced = True
        monitor.st.item_lost_this_round = True
        monitor.st.randomizer_item_changed = True
        monitor.st.died_this_round = True
        monitor.st.lived_this_round = True
        monitor.st.item_equipped_after_death = True
        monitor.st.pending_sabotage_murder = True
        monitor.st.sabotage_murder_this_round = True

        monitor._process("This round is taking place at Facility (12) and the round type is Classic")

        self.assertFalse(monitor.st.item_lost_announced)
        self.assertFalse(monitor.st.item_lost_this_round)
        self.assertFalse(monitor.st.randomizer_item_changed)
        self.assertFalse(monitor.st.died_this_round)
        self.assertFalse(monitor.st.lived_this_round)
        self.assertFalse(monitor.st.item_equipped_after_death)
        self.assertFalse(monitor.st.pending_sabotage_murder)
        self.assertFalse(monitor.st.sabotage_murder_this_round)


class TestStopLeavesNoFreeze(unittest.TestCase):
    """停止→（停止中に装備）→再開で、アイテムロストのフリーズが残り続けていた。
    停止がフリーズを先に解いてから監視を止めていたので、ほかの窓のフリーズ明けを待っていた窓
    （_handle_item_lost）がその間に起きて自分のフリーズを張り、それが次の開始まで残っていた"""

    def setUp(self):
        SharedState.begin_run()
        self.addCleanup(SharedState.begin_run)

    def test_a_stale_window_cannot_freeze_the_next_run(self):
        old = WindowState(run_id=SharedState.current_run())
        SharedState.begin_run()                         # 停止
        SharedState.equip_freeze_start(old)             # 止まる前の窓が遅れて張った
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)

    def test_a_stale_window_cannot_release_the_next_runs_freeze(self):
        old = WindowState(run_id=SharedState.current_run())
        SharedState.equip_freeze_start(old)
        SharedState.begin_run()                         # 停止・再開
        new = WindowState(run_id=SharedState.current_run())
        SharedState.equip_freeze_start(new)
        SharedState.equip_freeze_end(old)               # 止まる前の窓が遅れて解いた
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set(), "今の回の分は残る")
        self.assertEqual(SharedState.get_equip_freeze_count(), 1)
        self.assertFalse(old.equip_freeze_held)

    def test_the_waiting_window_does_not_leave_a_freeze_after_stop(self):
        """窓A が装備待ち・窓B は A の装備待ちが解けるのを待っている → 停止"""
        running = {"on": True}
        a = WindowState(run_id=SharedState.current_run(), waiting_for_equip=True)
        b = WindowState(run_id=SharedState.current_run(), waiting_for_equip=True, item_id=0)
        SharedState.equip_freeze_start(a)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=2), b, lambda: running["on"],
                                           lambda _m: None)
        result = {}
        worker = threading.Thread(target=lambda: result.setdefault("ok", ex._handle_item_lost()))
        worker.start()
        time.sleep(0.05)                                # B は待っている

        running["on"] = False                           # 停止: 監視を先に止める
        SharedState.begin_run()                         # それからフリーズを解く
        worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertFalse(result["ok"])
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set(), "再開したらすぐ動ける")
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)

    def test_stop_stops_the_monitors_before_releasing(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        stop = src[src.index("    def _stop(self):"):]
        stop = stop[:stop.index("\n    def ")]
        self.assertLess(stop.index("m.stop()"), stop.index("SharedState.begin_run()"))
        start = src[src.index("    def _start(self):"):]
        start = start[:start.index("\n    def ")]
        self.assertLess(start.index("SharedState.begin_run()"), start.index("mon.start()"))

    def test_a_monitor_belongs_to_the_run_it_started_in(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None)
        with patch.object(monitor, "_start_daemon"), \
             patch.object(monitor._action, "start_velocity_receiver"):
            monitor.start()
        self.assertEqual(monitor.st.run_id, SharedState.current_run())
        monitor.stop()


class TestItemLossLogsWithoutAnItem(unittest.TestCase):
    """前のロストから未回収のまま、またアイテムロストのラウンドに入ったときのログ"""

    def _monitor(self, item_id):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, logs.append, window_idx=1)
        monitor.st.item_id = item_id
        return monitor, logs

    def test_a_held_item_is_lost_as_before(self):
        monitor, logs = self._monitor(29)
        monitor._mark_item_lost("Punished: ラウンド開始時にアイテムロスト")
        self.assertEqual(logs, ["[窓1] Punished: ラウンド開始時にアイテムロスト"])

    def test_nothing_held_is_not_called_a_loss(self):
        monitor, logs = self._monitor(0)
        monitor._mark_item_lost("Punished: ラウンド開始時にアイテムロスト")
        self.assertEqual(logs, ["[窓1] Punished: アイテムなし（前にロストしたものは未回収のまま）"])
        self.assertTrue(monitor.st.item_lost_this_round, "扱いは今までどおりロスト（装備待ち・取得の対象）")

    def test_the_wait_ending_at_the_round_start_does_not_say_equipped(self):
        logs = []
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True, item_id=0,
                         waiting_for_equip=True)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1), st, lambda: True, logs.append)

        def press(*_a, **_k):
            st.begin_done = True
            return True

        def sleep(_sec):
            if st.begin_done:                   # 押した後、装備しないまま次のラウンド
                st.in_round = True
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
             patch.object(ex, "item_fetch_target", return_value=None), \
             patch.object(ex, "_attend_to_item_loss"), \
             patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
            ex.do_after_round()
        self.assertNotIn("✅ アイテム装備確認 → 続行", logs)
        self.assertIn("ラウンドが始まったので装備待ちをやめます（アイテムは未回収のまま）", logs)
