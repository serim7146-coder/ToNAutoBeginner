"""ラウンド中にインスタンスを抜ける・VRChat が落ちる・ToN 以外のワールドに入る

入室の行（Joining）が来たら、その窓のラウンドを「終わった」として片づける（LogMonitor._abandon_round）:
フリーズ（続行・突入・速度検知・装備待ち）を外す・統計は送らない・ラウンドの印と Begin の受理の印を戻す・
録画を止める・遅れた予約と Begin の待ちは番号（round_seq）で止まる。ToN 以外のワールドでは操作しない。
VRChat の窓が消えたときも同じ片づけをする。ユーザー ID・名前は作り物
"""
from tests.support import *  # noqa: F401,F403
import ActionExecutor

P = "2026.11.14 12:00:00 Debug      -  "
OTHER_WORLD = "wrld_00000000-0000-0000-0000-00000000ffff"
JOIN_TON = f"[Behaviour] Joining {TON_WORLD}:111~private(usr_me)~region(jp)"
JOIN_TON_2 = f"[Behaviour] Joining {TON_WORLD}:222~private(usr_me)~region(jp)"
JOIN_OTHER = f"[Behaviour] Joining {OTHER_WORLD}:333~private(usr_me)~region(jp)"
JOIN_OTHER_2 = f"[Behaviour] Joining {OTHER_WORLD}:444~private(usr_me)~region(jp)"
START = "This round is taking place at Sewers (12) and the round type is Fog"
KILLERS = "Killers have been set - 5 0 0 // Round type is Fog"

FREEZES = (SharedState.equip_freeze_reset, SharedState.continue_round_reset,
           SharedState.speed_freeze_reset, SharedState.round_freeze_reset)
EVENTS = ("EQUIP_WAIT_EVENT", "CONTINUE_ROUND_EVENT", "SPEED_FREEZE_EVENT", "ROUND_FREEZE_EVENT")


class _Base(unittest.TestCase):
    """全窓フリーズの状態を毎回きれいにして、監視を作る。本物の窓・入力・OSC・DB・音・録画は使わない"""

    def setUp(self):
        SharedState.begin_run()
        for reset in FREEZES:
            self.addCleanup(reset)
        self.addCleanup(lambda: [getattr(SharedState, n).set() for n in EVENTS])
        SharedState.set_hands_free(False)
        SharedState.set_item_begin_mode(False)
        self.addCleanup(SharedState.set_hands_free, False)
        self.play = patch.object(PlaySound, "play_sound").start()
        self.recorder = patch.object(Recorder, "on_round_over").start()
        self.register = patch.object(ConnectDB, "register_round").start()
        self.addCleanup(patch.stopall)

    def _monitor(self, idx=1, auto_begin=True, window_alive=None, in_round=True):
        cfg = WindowConfig(hwnd=0x100 + idx, auto_begin=auto_begin)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=idx, window_alive=window_alive)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.pending = []                                    # 遅れて走るはずだった予約（_start_daemon の代わり）
        monitor._start_daemon = lambda target, *args: monitor.pending.append((target, args))
        monitor._running = True
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        st.in_round = in_round
        return monitor

    def _line(self, monitor, body):
        monitor._process(P + body)

    def _said(self, monitor, text):
        return [m for m in monitor.logs if text in m]

    @staticmethod
    def _hold_all(monitor, kind="8pages"):
        """この窓が 4 種のフリーズを全部張っている状態にする"""
        st = monitor.st
        st.is_continue_round = True
        SharedState.continue_round_start(st)
        st.speed_freeze_kind = kind
        SharedState.speed_freeze_start(st)
        SharedState.round_freeze_start(st)
        st.waiting_for_equip = True
        SharedState.equip_freeze_start(st)

    @staticmethod
    def _counts():
        return (SharedState.get_equip_freeze_count(), SharedState.get_continue_round_count(),
                SharedState.get_speed_freeze_count(), SharedState.get_round_freeze_count())


class TestLeaveRoundReleasesFreezes(_Base):
    def test_every_freeze_is_released_for_both_kinds_of_window(self):
        """ラウンド中に入室: 続行・突入・速度検知・装備待ちが、Begin を押す窓でも押さない窓でも外れる"""
        for auto_begin in (True, False):
            monitor = self._monitor(auto_begin=auto_begin)
            st = monitor.st
            self._hold_all(monitor)
            self.assertEqual(self._counts(), (1, 1, 1, 1))

            self._line(monitor, JOIN_TON)

            self.assertEqual(self._counts(), (0, 0, 0, 0), auto_begin)
            for held in ("equip_freeze_held", "continue_freeze_held", "speed_freeze_held", "round_freeze_held",
                         "is_continue_round", "waiting_for_equip"):
                self.assertFalse(getattr(st, held), (auto_begin, held))
            self.assertEqual(st.speed_freeze_kind, "")
            self.assertTrue(all(getattr(SharedState, n).is_set() for n in EVENTS))
            self.assertFalse(st.in_round)

    def test_the_screen_log_says_each_release_with_the_instance_move_label(self):
        monitor = self._monitor()
        self._hold_all(monitor)

        self._line(monitor, JOIN_TON)

        for text in ("インスタンス移動 → ラウンドの途中でした。終わったものとして片づけます（このラウンドの統計は送りません）",
                     "インスタンス移動 → 続行ラウンドのフリーズ解除",
                     "インスタンス移動 → 速度検知フリーズ解除",
                     "インスタンス移動 → ラウンド突入フリーズ解除",
                     "インスタンス移動 → 装備待ちフリーズ解除"):
            self.assertEqual(len(self._said(monitor, text)), 1, (text, monitor.logs))

    def test_freezes_held_by_other_windows_stay(self):
        monitor = self._monitor()
        other = self._monitor(idx=2)
        self._hold_all(other)
        self._hold_all(monitor)
        self.assertEqual(self._counts(), (2, 2, 2, 2))

        self._line(monitor, JOIN_TON)

        self.assertEqual(self._counts(), (1, 1, 1, 1), "自分の分だけ引く")
        self.assertTrue(other.st.equip_freeze_held and other.st.continue_freeze_held
                        and other.st.speed_freeze_held and other.st.round_freeze_held)
        self.assertFalse(any(getattr(SharedState, n).is_set() for n in EVENTS), "ほかの窓の分は止めたまま")

    def test_the_continue_end_hook_is_called(self):
        """続行フリーズを外すと、既存の「続行を終える」流れ（after_continue_round）が動く"""
        monitor = self._monitor()
        monitor.st.continue_hook = monitor._on_continue_freeze
        self._hold_all(monitor)

        self._line(monitor, JOIN_TON)

        self.assertIn(monitor._action.after_continue_round, [t for t, _a in monitor.pending])

    def test_a_dtm_waldo_continue_without_a_freeze_is_cleared_too(self):
        """DTM/Waldo の続行（印だけで、他窓は止めていない）も戻す"""
        monitor = self._monitor()
        st = monitor.st
        st.is_continue_round = True
        st.open_special_continue = True

        self._line(monitor, JOIN_TON)

        self.assertFalse(st.is_continue_round)
        self.assertFalse(st.open_special_continue)
        self.assertEqual(self._said(monitor, "続行ラウンドのフリーズ解除"), [], "張っていないので言わない")

    def test_the_continue_count_stays_the_same_source_count(self):
        """続行の解除は _clear_stale_continue_round を使い回す（解除の呼び出しを増やさない）"""
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("SharedState.continue_round_end(st)"), 6)


class TestLeaveRoundResetsMarks(_Base):
    def test_the_round_marks_come_back_and_the_seq_moves_by_one(self):
        monitor = self._monitor()
        st = monitor.st
        st.round_type = "Sabotage"
        st.terror_ids = [5]
        st.statistics_sent = False
        st.sus_players = ["x"]
        st.pending_sabotage_murder = True
        st.sabotage_murder_this_round = True
        st.fog = True
        st.fog_reading = True
        st.early_read_hits = {"a": 1}
        st.fog_no_object_deadline = 99.0
        st.all_continue_round = True
        st.after_round_over = False
        st.is_open_special_round_round = True
        st.glorbo_afk = True
        st.begin_done = True
        st.begin_accepted_at = 5.0
        st.round_end_seen = True
        st.pending_verified_time = 7.0
        st.begin_move_done = True
        st.speed_probe_done = True
        st.speed_strafe_done = True
        st.speed_round_kind = "8pages"
        monitor._ignored_verified = (1.0, 2.0)
        seq = st.round_seq

        self._line(monitor, JOIN_TON)

        self.assertEqual(st.round_seq, seq + 1)
        self.assertFalse(st.in_round)
        self.assertTrue(st.statistics_sent)
        self.assertEqual(st.terror_ids, [])
        self.assertEqual(st.round_type, "")
        self.assertEqual(st.sus_players, [])
        for name in ("pending_sabotage_murder", "sabotage_murder_this_round", "fog", "fog_reading",
                     "all_continue_round", "is_open_special_round_round", "glorbo_afk",
                     "begin_done", "round_end_seen", "begin_move_done", "speed_probe_done", "speed_strafe_done"):
            self.assertFalse(getattr(st, name), name)
        self.assertEqual(st.early_read_hits, {})
        self.assertEqual(st.fog_no_object_deadline, 0.0)
        self.assertTrue(st.after_round_over)
        self.assertEqual(st.begin_accepted_at, 0.0)
        self.assertEqual(st.pending_verified_time, 0.0)
        self.assertEqual(st.speed_round_kind, "")
        self.assertIsNone(monitor._ignored_verified)

    def test_it_does_not_touch_the_instance_fields_of_the_join(self):
        monitor = self._monitor()
        st = monitor.st
        st.item_id = 7
        st.twilight_count = 2
        st.log_now = 123.0
        self._line(monitor, JOIN_TON)
        self.assertEqual(st.item_id, 7, "item_id は入室の処理が別に扱う（触らない）")
        self.assertEqual(st.instance_seq, 1)
        self.assertEqual(st.twilight_count, 0, "入室の処理がそのまま 0 に戻す")

    def test_the_recorder_is_stopped(self):
        monitor = self._monitor()
        self._hold_all(monitor)
        self._line(monitor, JOIN_TON)
        self.recorder.assert_called_once_with(monitor.window_idx)

    def test_the_recorder_is_stopped_for_a_held_freeze_even_between_rounds(self):
        monitor = self._monitor(in_round=False)
        SharedState.round_freeze_start(monitor.st)
        self._line(monitor, JOIN_TON)
        self.recorder.assert_called_once_with(monitor.window_idx)

    def test_nothing_to_clean_up_says_nothing(self):
        """最初の入室など、片づけるものが無いとき: 片づけの行も録画の停止も無い。今の行は変わらない"""
        monitor = self._monitor(in_round=False)
        monitor.st.open_special_round_wins = 2
        monitor.cfg.skip_rounds = {"Fog"}
        monitor.cfg.cancel_afk = True

        self._line(monitor, JOIN_TON)

        self.assertEqual(self._said(monitor, "片づけ"), [])
        self.assertEqual(self._said(monitor, "フリーズ解除"), [])
        self.recorder.assert_not_called()
        self.assertTrue(self._said(monitor, "ラウンド指定を解除しました"), monitor.logs)
        self.assertTrue(self._said(monitor, "インスタンスタイプ:"), monitor.logs)
        self.assertFalse(self._said(monitor, "ToN 以外"), "ToN の入室では出さない")

    def test_three_win_line_and_round_setting_line_still_come(self):
        monitor = self._monitor()
        monitor.st.open_special_round_wins = 2
        monitor.cfg.cancel_afk = True
        monitor.cfg.skip_rounds = {"Fog"}
        self._line(monitor, JOIN_TON)
        self.assertTrue(self._said(monitor, "別のインスタンスに入ったので 0/"), monitor.logs)
        self.assertEqual(monitor.st.open_special_round_wins, 0)
        self.assertEqual(monitor.cfg.skip_rounds, set())

    def test_calling_twice_does_not_double_the_lines_or_go_negative(self):
        monitor = self._monitor()
        self._hold_all(monitor)

        first = monitor._abandon_round("インスタンス移動")
        lines = list(monitor.logs)
        second = monitor._abandon_round("インスタンス移動")

        self.assertTrue(first)
        self.assertFalse(second, "2 回目は片づけるものが無い")
        self.assertEqual(monitor.logs, lines, "行は二重にならない")
        self.assertEqual(self._counts(), (0, 0, 0, 0))
        self.assertTrue(all(getattr(SharedState, n).is_set() for n in EVENTS))

    def test_back_to_back_joins_do_not_go_negative(self):
        monitor = self._monitor()
        self._hold_all(monitor)
        self._line(monitor, JOIN_TON)
        self._line(monitor, JOIN_TON_2)
        self.assertEqual(self._counts(), (0, 0, 0, 0))
        self.assertEqual(len(self._said(monitor, "ラウンドの途中でした")), 1)


class TestLeaveRoundStatistics(_Base):
    def _unsent_round(self, monitor):
        st = monitor.st
        st.round_type = "Classic"
        st.terror_ids = [5]
        st.statistics_sent = False
        st.map_id = 12
        st.in_round = True

    def test_an_unsent_round_is_not_sent_by_any_path(self):
        monitor = self._monitor()
        self._unsent_round(monitor)
        old_seq = monitor.st.round_seq

        self._line(monitor, JOIN_TON)
        self.register.assert_not_called()

        with patch.object(LogMonitor.time, "sleep"):
            monitor._send_statistics_after_wait(old_seq)             # 遅れて送る予約
        monitor._send_round_statistics_once()                        # 取りこぼしの保険
        self._line(monitor, "RoundOver")                              # 移った先の RoundOver
        self._line(monitor, "Verified Round End")                    # 移った先の Verified Round End

        self.register.assert_not_called()

    def test_a_round_already_sent_at_killers_is_not_undone(self):
        monitor = self._monitor()
        self._line(monitor, START)
        self._line(monitor, KILLERS)
        self.assertEqual(self.register.call_count, 1, "前提: Killers の行で送り済み")

        self._line(monitor, JOIN_TON)

        self.assertEqual(self.register.call_count, 1, "取り消しも送り直しもしない")

    def test_the_next_round_sends_again_as_usual(self):
        monitor = self._monitor()
        self._unsent_round(monitor)
        self._line(monitor, JOIN_TON)

        self._line(monitor, START)
        self._line(monitor, KILLERS)

        self.assertEqual(self.register.call_count, 1)


class TestLeaveRoundStaleReservations(_Base):
    def test_delayed_releases_do_nothing_and_say_nothing_after_the_cleanup(self):
        """遅れて解く予約（待ちは差し替える）は、片づけの後は何もしない。ほかの窓が張った分にも触らない"""
        monitor = self._monitor()
        other = self._monitor(idx=2)
        self._hold_all(other)
        self._hold_all(monitor)
        old = monitor.st.round_seq
        self._line(monitor, JOIN_TON)
        logs = list(monitor.logs)
        before = self._counts()

        # 続行・突入・速度検知は、ほかの窓が張った分があっても触らない。装備待ちも同じ
        monitor.st.is_continue_round = True            # 次のラウンドの続行が始まっていても、番号が違えば触らない
        with patch.object(LogMonitor.time, "sleep"):
            monitor._release_continue_freeze_after_delay(old)
            monitor._release_round_freeze_after_delay(old)
            monitor._release_speed_freeze_after_delay(old)
            monitor._release_equip_wait_after_delay(old)
        monitor.st.is_continue_round = False

        self.assertEqual(monitor.logs, logs, "何も出さない")
        self.assertEqual(self._counts(), before)
        self.assertTrue(other.st.equip_freeze_held and other.st.speed_freeze_held)

    def test_the_equip_wait_without_a_seq_still_works_as_before(self):
        monitor = self._monitor()
        monitor.st.equip_freeze_held = False
        SharedState.equip_freeze_start(monitor.st)
        with patch.object(LogMonitor.time, "sleep"):
            monitor._release_equip_wait_after_delay()
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)
        self.assertTrue(self._said(monitor, "全窓フリーズ解除"))

    def test_the_callers_pass_the_seq(self):
        monitor = self._monitor()
        monitor.st.waiting_for_equip = True
        monitor.st.begin_done = True
        monitor.st.equip_freeze_held = False
        self._line(monitor, "Equipping 29.")
        self.assertEqual([(t.__name__, a) for t, a in monitor.pending if t.__name__ == "_release_equip_wait_after_delay"],
                         [("_release_equip_wait_after_delay", (monitor.st.round_seq,))])

    def test_the_other_windows_wait_ends_right_after_the_join(self):
        """ほかの窓の _wait_other_windows は、入室のあとすぐ抜ける"""
        holder = self._monitor(idx=1)
        self._hold_all(holder)
        waiter_st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=2)
        waiter = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x202), waiter_st, lambda: True, lambda _m: None)
        result = []
        thread = threading.Thread(target=lambda: result.append(waiter._wait_other_windows()), daemon=True)
        thread.start()
        thread.join(0.3)
        self.assertTrue(thread.is_alive(), "前提: ほかの窓が張っている間は待つ")

        self._line(holder, JOIN_TON)

        thread.join(3.0)
        self.assertFalse(thread.is_alive(), "入室のあと待ちが終わっていない")
        self.assertEqual(result, [True])


class TestDoAfterRoundStopsOnTheSeq(_Base):
    def _executor(self, **st_kw):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=4, round_end_seen=True,
                         round_over_time=time.time(), **st_kw)
        logs = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x300, auto_begin=True), st, lambda: True, logs.append)
        ex.logs = logs
        for name, value in (("_pos_ref_plan", False), ("_begin_move", None), ("_start_use_spam", None),
                            ("_press_begin", False), ("_confirm_begin", None), ("_handle_item_lost", True)):
            setattr(ex, name, MagicMock(return_value=value))
        return ex, st

    def test_the_seq_moving_inside_the_other_windows_wait_blocks_the_press(self):
        ex, st = self._executor()

        def wait_then_leave():
            st.round_seq += 1                  # 待っている間にインスタンスを抜けた
            return True
        ex._wait_other_windows = MagicMock(side_effect=wait_then_leave)

        with patch.object(config, "BEGIN_WAIT_SEC", 0), patch.object(ActionExecutor.time, "sleep"):
            ex.do_after_round()

        ex._wait_other_windows.assert_called_once()
        ex._press_begin.assert_not_called()
        ex._confirm_begin.assert_not_called()

    def test_without_a_leave_it_presses(self):
        ex, st = self._executor()
        ex._wait_other_windows = MagicMock(return_value=True)
        ex._press_begin.return_value = False
        with patch.object(config, "BEGIN_WAIT_SEC", 0), patch.object(ActionExecutor.time, "sleep"):
            ex.do_after_round()
        ex._press_begin.assert_called_once()

    def test_the_seq_moving_during_the_begin_wait_stops_it_quietly(self):
        ex, st = self._executor()
        ex._wait_other_windows = MagicMock(return_value=True)

        def leave(_sec):
            st.round_seq += 1
        with patch.object(config, "BEGIN_WAIT_SEC", 5), patch.object(ActionExecutor.time, "sleep", side_effect=leave):
            ex.do_after_round()

        ex._begin_move.assert_not_called()
        ex._press_begin.assert_not_called()
        self.assertEqual(ex.logs, [], "黙って終わる")

    def test_wait_round_end_returns_false_silently_when_the_seq_moves(self):
        ex, st = self._executor()
        st.round_end_seen = False

        def leave(_sec):
            st.round_seq += 1
        with patch.object(ActionExecutor.time, "sleep", side_effect=leave):
            self.assertFalse(ex._wait_round_end(round_seq=4))

        self.assertFalse(any("来ないため" in m for m in ex.logs), ex.logs)

    def test_wait_round_end_without_a_seq_is_as_before(self):
        ex, st = self._executor()
        st.round_end_seen = False
        with patch.object(ActionExecutor.time, "sleep", side_effect=lambda _s: setattr(st, "round_end_seen", True)):
            self.assertTrue(ex._wait_round_end())


class TestNotTonWorld(_Base):
    def test_default_is_ton_so_the_old_behaviour_stays_until_a_join_is_seen(self):
        monitor = self._monitor(in_round=False)
        self.assertTrue(monitor.st.in_ton_world)
        self.assertTrue(monitor._action.can_operate())

    def test_another_world_stops_operating_and_says_it_once(self):
        monitor = self._monitor(in_round=False)
        self._line(monitor, JOIN_TON)
        self.assertTrue(monitor._action.can_operate())
        self.assertEqual(monitor.logs.count("[窓1] ToN 以外のワールドに入りました → この窓は操作しません"), 0)

        self._line(monitor, JOIN_OTHER)
        self.assertFalse(monitor._action.can_operate(), "private でも ToN 以外は操作しない")
        self.assertEqual(monitor.logs.count("[窓1] ToN 以外のワールドに入りました → この窓は操作しません"), 1)

        self._line(monitor, JOIN_OTHER_2)
        self.assertFalse(monitor._action.can_operate())
        self.assertEqual(len(self._said(monitor, "ToN 以外のワールド")), 1, "別の ToN 以外へ移っても行は増えない")

    def test_back_to_ton_resumes_with_one_line(self):
        monitor = self._monitor(in_round=False)
        self._line(monitor, JOIN_OTHER)
        self._line(monitor, JOIN_TON)
        self.assertTrue(monitor._action.can_operate())
        self.assertEqual(monitor.logs.count("[窓1] ToN のワールドに入りました → この窓の操作を再開します"), 1)

        self._line(monitor, JOIN_TON_2)
        self.assertEqual(len(self._said(monitor, "ToN のワールドに入りました")), 1, "ToN から ToN では出さない")

    def test_a_join_line_without_a_world_keeps_the_value(self):
        monitor = self._monitor(in_round=False)
        monitor.st.in_ton_world = False
        monitor._enter_world("")
        self.assertFalse(monitor.st.in_ton_world)
        self.assertEqual(monitor.logs, [])

    def test_the_chase_stops_when_the_world_is_not_ton(self):
        monitor = self._monitor(in_round=False)
        monitor._action.chase_stop = MagicMock(return_value=True)
        self._line(monitor, JOIN_OTHER)
        monitor._action.chase_stop.assert_called_once()

    def test_the_other_world_join_also_cleans_up_the_round(self):
        monitor = self._monitor()
        self._hold_all(monitor)
        self._line(monitor, JOIN_OTHER)
        self.assertEqual(self._counts(), (0, 0, 0, 0))
        self.assertFalse(monitor.st.in_round)


class TestStartScanWorld(_Base):
    def _scan(self, world):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(P + f"[Behaviour] Joining {world}:1~private(usr_me)~region(jp)\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name)), {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._detect_instance_from_log()
        return monitor

    def test_the_last_join_in_another_world_is_not_operated(self):
        monitor = self._scan(OTHER_WORLD)
        self.assertFalse(monitor.st.in_ton_world)
        self.assertFalse(monitor._action.can_operate())
        self.assertEqual(monitor.logs.count("[窓1] ToN 以外のワールドにいます → この窓は操作しません"), 1)

    def test_the_last_join_in_ton_is_operated(self):
        monitor = self._scan(TON_WORLD)
        self.assertTrue(monitor.st.in_ton_world)
        self.assertTrue(monitor._action.can_operate())
        self.assertEqual(self._said(monitor, "ToN 以外"), [])


class TestParserWorld(unittest.TestCase):
    def test_the_world_id_is_read(self):
        line = P + f"[Behaviour] Joining {TON_WORLD}:12345~private(usr_x)~region(jp)"
        event = LogParser.parse(line)
        self.assertEqual(event.kind, LogParser.EVENT_JOINING)
        self.assertEqual(event.world, TON_WORLD)
        self.assertEqual(event.instance, f"{TON_WORLD}:12345~private(usr_x)~region(jp)")

    def test_another_world(self):
        event = LogParser.parse(P + "[Behaviour] Joining wrld_abc:7~region(jp)")
        self.assertEqual(event.world, "wrld_abc")

    def test_other_events_have_no_world(self):
        self.assertEqual(LogParser.parse(P + "RoundOver").world, "")


class TestWindowGone(_Base):
    def _alive(self, values):
        calls = []

        def alive():
            calls.append(1)
            value = values[min(len(calls), len(values)) - 1]
            if isinstance(value, Exception):
                raise value
            return value
        alive.calls = calls
        return alive

    @staticmethod
    def _again(monitor):
        """間引きの時間を過ぎたことにする"""
        monitor._window_checked_at -= config.WINDOW_ALIVE_CHECK_SEC + 1.0

    def test_alive_then_gone_cleans_up_once_with_one_line(self):
        alive = self._alive([True, False, False, False])
        monitor = self._monitor(window_alive=alive)
        self._hold_all(monitor)

        monitor._check_window_alive()
        self.assertTrue(monitor.st.in_round, "窓がある間は何もしない")
        self.assertEqual(self._counts(), (1, 1, 1, 1))

        self._again(monitor)
        monitor._check_window_alive()
        self.assertFalse(monitor.st.in_round)
        self.assertEqual(self._counts(), (0, 0, 0, 0))
        seq = monitor.st.round_seq
        self.assertEqual(len(self._said(monitor, "窓が消えた → ラウンドの途中でした")), 1, monitor.logs)
        self.assertEqual(len(self._said(monitor, "⚠ VRChat の窓が見つかりません（落ちた・閉じた）→ この窓のラウンドを片づけました")), 1)

        for _ in range(3):                      # 繰り返さない
            self._again(monitor)
            monitor._check_window_alive()
        self.assertEqual(monitor.st.round_seq, seq)
        self.assertEqual(len(self._said(monitor, "窓が見つかりません")), 1)
        self.assertEqual(len(alive.calls), 2, "消えたあとはもう調べない")
        self.recorder.assert_called_once_with(monitor.window_idx)

    def test_the_line_comes_once_even_with_nothing_to_clean_up(self):
        monitor = self._monitor(window_alive=self._alive([False]), in_round=False)
        monitor._check_window_alive()
        self.assertEqual(len(self._said(monitor, "窓が見つかりません")), 1)
        self.assertEqual(self._said(monitor, "ラウンドの途中でした"), [])

    def test_the_check_is_thinned_out(self):
        alive = self._alive([True])
        monitor = self._monitor(window_alive=alive)
        for _ in range(5):
            monitor._check_window_alive()
        self.assertEqual(len(alive.calls), 1)
        self._again(monitor)
        monitor._check_window_alive()
        self.assertEqual(len(alive.calls), 2)

    def test_without_window_alive_nothing_is_looked_at(self):
        monitor = self._monitor()
        self._hold_all(monitor)
        monitor._check_window_alive()
        self.assertTrue(monitor.st.in_round)
        self.assertEqual(self._counts(), (1, 1, 1, 1))
        self.assertEqual(monitor.logs, [])

    def test_an_exception_counts_as_alive(self):
        monitor = self._monitor(window_alive=self._alive([RuntimeError("x")]))
        with patch.object(DebugLog, "exception"):
            monitor._check_window_alive()
        self.assertTrue(monitor.st.in_round)
        self.assertFalse(monitor._window_gone)
        self.assertEqual(monitor.logs, [])

    def test_the_loop_calls_the_check(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        run = src[src.index("    def _run(self):"):src.index("    def _process_lines")]
        self.assertIn("self._check_window_alive()", run)

    def test_the_other_window_wait_ends_after_the_window_is_gone(self):
        holder = self._monitor(idx=1, window_alive=self._alive([False]))
        self._hold_all(holder)
        waiter_st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=2)
        waiter = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x202), waiter_st, lambda: True, lambda _m: None)
        self.assertFalse(waiter._freezes_ok()[1], "前提: 止まっている")
        holder._check_window_alive()
        self.assertTrue(all(waiter._freezes_ok()))


class TestWindowExists(unittest.TestCase):
    def test_hwnd_zero_is_not_there(self):
        self.assertFalse(WindowOperator.window_exists(0))

    def test_it_follows_iswindow(self):
        with patch.object(WindowOperator.win32gui, "IsWindow", return_value=True):
            self.assertTrue(WindowOperator.window_exists(5))
        with patch.object(WindowOperator.win32gui, "IsWindow", return_value=False):
            self.assertFalse(WindowOperator.window_exists(5))

    def test_an_exception_is_not_gone(self):
        with patch.object(WindowOperator.win32gui, "IsWindow", side_effect=OSError("x")), \
             patch.object(DebugLog, "exception"):
            self.assertTrue(WindowOperator.window_exists(5))


class TestMainGuiPassesWindowAlive(unittest.TestCase):
    def test_the_monitor_gets_window_alive(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        start = src.index("mon = LogMonitor.LogMonitor(")
        call = src[start:src.index("self.monitors.append(mon)", start)]
        self.assertIn("window_alive=lambda h=cfg.hwnd: WindowOperator.window_exists(h)", call)

    def test_the_constant(self):
        self.assertEqual(config.WINDOW_ALIVE_CHECK_SEC, 2.0)
