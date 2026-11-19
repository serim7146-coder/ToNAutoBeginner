"""参加していないラウンド（「Not opted in. Not joining this round.」の次のラウンド。依頼者 2026-11-19）。

リスポーンの後、「opted in」が来ないままだと、次のラウンドの開始の直前に「Not opted in. …」が出て、そのラウンドには
参加しない。ツールはこの行を読まず、ロビーにいるのに自爆キーの長押し・突入フリーズ・AFK 回避・続行と判定して
ほかの窓を止める、をしていた。開始の時点から死亡と同じに扱い、判定の行だけを出す。
時計・スレッド・窓・音・OSC・DB は偽物で、本物の入力は送らない。
"""
from tests.support import *  # noqa: F401,F403
import Recorder
import PhoneNotify

T = "2026.11.19 12:00:00 Debug      -  "
NOT_OPTED_IN = T + "Not opted in. Not joining this round."
OPTED_IN = T + "opted in"
RESPAWN = T + "Player respawned, opted out!"
ROUND_OVER = T + "RoundOver"
VERIFIED_END = T + "Verified Round End"
DIED = T + "You died."
REBORN = T + "LOL JK, REBORN!"
EQUIP = T + "Equipping 29."
SUS = T + "Sus player = 1 serim01"


def _start(round_type="Classic"):
    return T + f"This round is taking place at Sewers (12) and the round type is {round_type}"


NOTE = LogMonitor.NOT_JOINED_NOTE
SKIP_NOTE = LogMonitor.NOT_JOINED_SKIP_NOTE
LINE = LogMonitor.NOT_JOINED_LINE
KEY = "Classic/クラシック"
LISTED, UNLISTED = 99, 12
BASIS = "（共有リスト）"      # 続行リストの根拠（主催リストの窓で、誰がいるか分からないとき）


def _name_of(target) -> str:
    """予約した関数の名前。support が do_afk_watch を何もしない物に差し替えているので、それは本来の名前で返す"""
    if getattr(target, "__func__", target) is ActionExecutor.ActionExecutor.do_afk_watch:
        return "do_afk_watch"
    return target.__name__


class TestParsing(unittest.TestCase):
    def test_the_two_lines_are_read(self):
        self.assertEqual(LogParser.parse(NOT_OPTED_IN).kind, LogParser.EVENT_NOT_OPTED_IN)
        self.assertEqual(LogParser.parse(OPTED_IN).kind, LogParser.EVENT_OPTED_IN)

    def test_the_neighbours_are_not_mistaken(self):
        self.assertEqual(LogParser.parse(RESPAWN).kind, LogParser.EVENT_RESPAWN)
        for line in (T + "opted in!", T + "opted out", T + "Not opted in.", T + "Not opted in. Not joining this round",
                     T + "opted in. Not joining this round.", T + "Player opted in"):
            self.assertIsNone(LogParser.parse(line), line)
        self.assertNotEqual(LogParser.parse(NOT_OPTED_IN).kind, LogParser.EVENT_OPTED_IN)
        self.assertNotEqual(LogParser.parse(OPTED_IN).kind, LogParser.EVENT_NOT_OPTED_IN)

    def test_a_joined_line_is_read_too(self):
        joined = ("2026.11.19 12:00:00 Debug      -  2026.11.19 12:00:03 Debug      -  "
                  "Not opted in. Not joining this round.")
        self.assertEqual(LogParser.parse(joined).kind, LogParser.EVENT_NOT_OPTED_IN)
        joined = ("2026.11.19 12:00:00 Debug      -  2026.11.19 12:00:03 Debug      -  opted in")
        self.assertEqual(LogParser.parse(joined).kind, LogParser.EVENT_OPTED_IN)

    def test_the_marks_and_the_handlers_are_registered(self):
        self.assertIn(LogParser.NOT_OPTED_IN_MARK, LogMonitor.LogMonitor._START_SCAN_MARKS)
        self.assertNotIn(LogParser.OPTED_IN_MARK, LogMonitor.LogMonitor._START_SCAN_MARKS)
        self.assertIn(LogParser.EVENT_NOT_OPTED_IN, LogMonitor.LogMonitor._HELD_ITEM_REPLAY_KINDS)
        handlers = LogMonitor.LogMonitor._HANDLERS
        self.assertEqual(handlers[LogParser.EVENT_NOT_OPTED_IN], "_on_not_opted_in")
        self.assertEqual(handlers[LogParser.EVENT_OPTED_IN], "_on_opted_in")
        for name in handlers.values():
            self.assertTrue(hasattr(LogMonitor.LogMonitor, name), name)


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.set_continue_drop_item(False)
        SharedState.set_run_respawn(False)
        SharedState.set_follow_utils(False)
        SharedState.set_no_begin_after_continue(False)
        SharedState.set_item_begin_mode(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        resets = (SharedState.continue_round_reset, SharedState.round_freeze_reset,
                  SharedState.speed_freeze_reset, SharedState.equip_freeze_reset)
        for reset in resets:
            reset()
            self.addCleanup(reset)
        for undo in (SharedState.set_run_respawn, SharedState.set_follow_utils,
                     SharedState.set_no_begin_after_continue, SharedState.set_continue_drop_item,
                     SharedState.set_item_begin_mode):
            self.addCleanup(undo, False)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.debug = []
        self.register = patch.object(ConnectDB, "register_round").start()
        self.play = patch.object(PlaySound, "play_sound").start()
        self.on_continue = patch.object(Recorder, "on_continue_start").start()
        self.on_round_freeze = patch.object(Recorder, "on_round_freeze_start").start()
        patch.object(Recorder, "on_round_over").start()
        self.focus = patch.object(WindowOperator, "focus_window", return_value=True).start()
        self.borrow = patch.object(WindowOperator, "borrow_front", return_value=(True, None)).start()
        self.phone = patch.object(PhoneNotify, "send").start()
        patch.object(config, "ITEMS", _item_table()).start()
        patch.object(DebugLog, "write", side_effect=self.debug.append).start()
        self.addCleanup(patch.stopall)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, keep_on=None, **cfg):
        cfg.setdefault("auto_begin", False)
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=9, **cfg), keep_on if keep_on is not None else {},
                                        lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.booked = []
        monitor._start_daemon = lambda target, *a: monitor.booked.append((_name_of(target), a))
        monitor._action.remember_continue_window = lambda: None
        monitor.st.instance_type = instance_type
        monitor.st.round_seq = 3
        monitor.st.item_id = 0
        monitor.st.held_item_id = 0
        return monitor

    @staticmethod
    def _lines(monitor):
        return [m.split("] ", 1)[1] if "] " in m else m for m in monitor.logs]

    def _not_joined_round(self, monitor, round_type="Classic"):
        monitor._process(NOT_OPTED_IN)
        monitor._process(_start(round_type))
        return monitor

    def _quiet(self, monitor):
        """判定の後も、続行の印・フリーズ・音・録画・通知・前面化・自爆・ループが何も起きていない"""
        st = monitor.st
        self.assertFalse(st.is_continue_round)
        self.assertFalse(st.all_continue_round)
        self.assertFalse(st.open_special_continue)
        self.assertFalse(st.is_open_special_round_round)
        self.assertFalse(st.glorbo_afk)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        # 統計の待ち（_send_statistics_after_wait）は判定と関係ないので数えない
        self.assertEqual([n for n, _a in monitor.booked if n != "_send_statistics_after_wait"], [])
        self.play.assert_not_called()
        self.on_continue.assert_not_called()
        self.phone.assert_not_called()
        self.focus.assert_not_called()
        self.borrow.assert_not_called()


class TestRoundStart(_Case):
    def test_the_round_is_a_death_from_the_start(self):
        monitor = self._monitor()
        monitor._process(NOT_OPTED_IN)
        self.assertTrue(monitor.st.not_opted_in_pending)
        self.assertFalse(monitor._not_joined(), "まだ開始の行が来ていない")

        monitor._process(_start())

        st = monitor.st
        self.assertTrue(st.died_this_round)
        self.assertTrue(monitor._not_joined())
        self.assertFalse(st.not_opted_in_pending, "使って下ろす")
        self.assertEqual(st.not_joined_seq, st.round_seq)
        lines = self._lines(monitor)
        self.assertEqual(lines.count("開始: Classic"), 1, lines)
        self.assertEqual(lines.count(LINE), 1, lines)
        self.assertLess(lines.index("開始: Classic"), lines.index(LINE))
        self.assertTrue(any("Not opted in の次のラウンド" in m for m in self.debug), self.debug)

    def test_no_afk_watch_and_no_run_respawn(self):
        SharedState.set_run_respawn(True)
        for round_type in ("Classic", "Run"):
            monitor = self._monitor()
            self._not_joined_round(monitor, round_type)
            self.assertEqual(monitor.booked, [], round_type)
        control = self._monitor()
        control._process(_start("Run"))
        self.assertIn("do_run_respawn", [n for n, _a in control.booked], "参加するラウンドは今までどおり")
        self.assertIn("do_afk_watch", [n for n, _a in control.booked])

    def test_no_run_all_continue_either(self):
        monitor = self._monitor(continue_rounds={"Run"})
        self._not_joined_round(monitor, "Run")
        self.assertFalse(monitor.st.all_continue_round)
        self.assertFalse(any("全続行" in m for m in monitor.logs), monitor.logs)
        self.phone.assert_not_called()

    def test_no_entry_freeze_no_voice_no_recording(self):
        monitor = self._monitor(freeze_rounds={"Classic"})
        self._not_joined_round(monitor)
        self.assertFalse(monitor.st.round_freeze_held)
        self.assertEqual(SharedState.get_round_freeze_count(), 0)
        self.on_round_freeze.assert_not_called()
        self.play.assert_not_called()
        self.focus.assert_not_called()
        self.borrow.assert_not_called()

        control = self._monitor(freeze_rounds={"Classic"})
        control._process(_start())
        self.assertTrue(control.st.round_freeze_held, "前提: 参加するラウンドは張る")
        self.assertEqual(SharedState.get_round_freeze_count(), 1)

    def test_no_fog_entry_voice(self):
        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True):
            monitor = self._monitor()
            self._not_joined_round(monitor, "Fog")
        self.play.assert_not_called()

    def test_the_round_start_item_loss_is_skipped(self):
        for round_type in sorted(config.ROUND_START_ITEM_LOSS_ROUNDS | {"8 Pages", "Punished"}):
            monitor = self._monitor()
            monitor.st.item_id = monitor.st.held_item_id = 29
            self._not_joined_round(monitor, round_type)
            st = monitor.st
            self.assertEqual((st.item_id, st.held_item_id), (29, 29), round_type)
            self.assertFalse(st.item_lost_this_round, round_type)

        control = self._monitor()
        control.st.item_id = control.st.held_item_id = 29
        control._process(_start("Punished"))
        self.assertEqual((control.st.item_id, control.st.held_item_id), (0, 0), "前提: 参加するなら失う")

    def test_a_sabotage_murder_is_not_made(self):
        monitor = self._monitor()
        monitor.st.item_id = monitor.st.held_item_id = 29
        monitor.st.local_player_name = "serim01"
        monitor._process(SUS)
        self.assertTrue(monitor.st.pending_sabotage_murder)
        self._not_joined_round(monitor, "Sabotage")
        st = monitor.st
        self.assertFalse(st.sabotage_murder_this_round)
        self.assertFalse(st.pending_sabotage_murder, "使って下ろす")
        self.assertEqual((st.item_id, st.held_item_id), (29, 29))
        self.assertFalse(st.item_lost_this_round)

    def test_an_own_sus_player_in_the_round_loses_nothing_and_freezes_nothing(self):
        monitor = self._monitor(freeze_rounds={"Sabotage murder"})
        monitor.st.item_id = monitor.st.held_item_id = 29
        monitor.st.local_player_name = "serim01"
        self._not_joined_round(monitor, "Sabotage")
        monitor.logs.clear()

        monitor._process(SUS)

        st = monitor.st
        self.assertFalse(st.sabotage_murder_this_round)
        self.assertEqual((st.item_id, st.held_item_id), (29, 29))
        self.assertFalse(st.round_freeze_held)
        self.assertIn("Sus player一致: serim01", self._lines(monitor))

    def test_the_next_round_without_the_line_is_joined_as_usual(self):
        monitor = self._monitor()
        self._not_joined_round(monitor)
        monitor._process(ROUND_OVER)
        monitor._process(_start())
        st = monitor.st
        self.assertFalse(st.died_this_round)
        self.assertFalse(monitor._not_joined())
        self.assertIn("do_afk_watch", [n for n, _a in monitor.booked])

    def test_the_stats_and_the_terror_line_stay(self):
        monitor = self._monitor()
        self._not_joined_round(monitor)
        with patch.object(monitor, "_waiting_for_terror_replacement", return_value=False):
            monitor._on_killers([UNLISTED], "Classic", revealed=False)
        self.register.assert_called_once()
        self.assertTrue(any(m.startswith("Terror set: ") for m in self._lines(monitor)), monitor.logs)

    def test_a_death_line_or_a_cat_does_not_bring_it_back(self):
        monitor = self._monitor()
        self._not_joined_round(monitor)
        monitor._process(REBORN)
        self.assertTrue(monitor.st.died_this_round)
        self.assertEqual(monitor.st.reborn_seq, 0)

    def test_the_item_is_not_dropped_at_the_end(self):
        SharedState.set_continue_drop_item(True)
        monitor = self._monitor()
        self._not_joined_round(monitor)
        monitor._drop_when_round_ends("死んだ")
        self.assertNotIn("drop_at_round_end", [n for n, _a in monitor.booked])
        self.assertNotEqual(monitor.st.dropped_round_seq, monitor.st.round_seq)

        control = self._monitor()
        control._process(_start())
        control._drop_when_round_ends("死んだ")
        self.assertIn("drop_at_round_end", [n for n, _a in control.booked], "参加するラウンドは今までどおり")


class TestDecisionLines(_Case):
    """判定は行だけ。各経路の今の 1 行目に注記を付ける"""

    def _decided(self, plan, round_type="Classic", instance_type=config.INSTANCE_PRIVATE, **cfg):
        monitor = self._monitor(instance_type=instance_type, **cfg)
        self._not_joined_round(monitor, round_type)
        monitor.st.terror_ids = [UNLISTED]
        monitor.logs.clear()
        with patch.object(monitor, "_plan", return_value=plan):
            monitor._decide(round_type)
        self._quiet(monitor)
        return monitor

    def test_every_plan_prints_only_its_line(self):
        names = LogMonitor.format_terror_ids([UNLISTED])
        cases = [
            (("restricted",), config.INSTANCE_PRIVATE,
             f"インスタンス制限: 操作スキップ ({config.INSTANCE_PRIVATE}){NOTE}"),
            (("hands_free", "放置モード(3クラ済み): 即自爆 X / Classic"), config.INSTANCE_PRIVATE,
             f"放置モード(3クラ済み): 即自爆 X / Classic{SKIP_NOTE}"),
            (("continue_rounds",), config.INSTANCE_PRIVATE, f"ラウンド指定で続行: Classic{NOTE}"),
            (("round_skip",), config.INSTANCE_PRIVATE, f"ラウンド指定自爆: Classic{SKIP_NOTE}"),
            (("glorbo",), config.INSTANCE_PRIVATE, f"判定: Glorbo / Classic 【プレイ(Glorbo)】{NOTE}"),
            (("hands_free_keep", "Glorbo"), config.INSTANCE_PRIVATE,
             f"判定: Glorbo / Classic 【プレイ(放置: 問答無用)】{NOTE}"),
            (("group", GroupRound.SKIP), config.INSTANCE_YAKIIMO, f"グループ自動自爆: Classic{SKIP_NOTE}"),
            (("group", GroupRound.WANTED), config.INSTANCE_YAKIIMO, f"グループ判定: Classic 【プレイ】{NOTE}"),
            (("group", GroupRound.CONTINUE_LISTED), config.INSTANCE_YAKIIMO,
             f"グループ判定: Classic 【全続行 → 続行リストで続行】{NOTE}"),
            (("group", GroupRound.CONTINUE), config.INSTANCE_YAKIIMO, f"グループ判定: Classic 【全続行】{NOTE}"),
            (("list", True, False), config.INSTANCE_PRIVATE, f"判定: {names} / Classic 【プレイ】{NOTE}{BASIS}"),
            (("list", True, True), config.INSTANCE_PRIVATE,
             f"判定: {names} / Classic 【プレイ(DTM/Waldo)】{NOTE}{BASIS}"),
            (("list", False, False), config.INSTANCE_PRIVATE,
             f"判定: {names} / Classic 【スキップ】{SKIP_NOTE}{BASIS}"),
        ]
        for plan, itype, expected in cases:
            with self.subTest(plan=plan):
                monitor = self._decided(plan, instance_type=itype)
                self.assertEqual(self._lines(monitor), [expected])

    def test_the_real_list_plan_prints_one_line(self):
        for wanted, tag in ((LISTED, "【プレイ】" + NOTE + BASIS), (UNLISTED, "【スキップ】" + SKIP_NOTE + BASIS)):
            monitor = self._monitor(keep_on={KEY: {LISTED}})
            self._not_joined_round(monitor)
            monitor.logs.clear()
            monitor._on_killers([wanted], "Classic", revealed=False)
            lines = self._lines(monitor)
            self.assertEqual(len(lines), 2, lines)
            self.assertTrue(lines[0].startswith("Terror set: "), lines)
            self.assertEqual(lines[1], f"判定: {LogMonitor.format_terror_ids([wanted])} / Classic {tag}")
            self._quiet(monitor)

    def test_the_dtm_waldo_continue_does_not_start_the_loop(self):
        monitor = self._monitor()
        self._not_joined_round(monitor)
        monitor.st.terror_ids = [config.DTM_ID]
        monitor.st.open_special_round_wins = 0
        monitor.logs.clear()
        with patch.object(monitor, "_plan", return_value=("list", True, True)):
            monitor._decide("Classic")
        self._quiet(monitor)
        self.assertFalse(any("AFK" in m or "クラ解放" in m for m in monitor.logs), monitor.logs)

    def test_the_utils_follow_is_not_waited_for(self):
        SharedState.set_follow_utils(True)
        monitor = self._monitor()
        self._not_joined_round(monitor)
        monitor.st.terror_ids = [UNLISTED]
        monitor.logs.clear()
        with patch.object(monitor, "_follow_utils", return_value=True) as follow, \
                patch.object(monitor, "_wait_utils") as wait:
            monitor._decide("Classic")
        follow.assert_not_called()
        wait.assert_not_called()
        self.assertEqual(len(self._lines(monitor)), 1, monitor.logs)
        self.assertFalse(any("Utils" in m for m in monitor.logs), monitor.logs)

    def test_a_replacement_in_the_air_does_not_make_it_wait(self):
        monitor = self._monitor(keep_on={KEY: {LISTED}})
        self._not_joined_round(monitor)
        monitor.logs.clear()
        with patch.object(monitor, "_replacement_changes_decision", return_value=True):
            monitor._on_killers([LISTED], "Classic", revealed=False)
        lines = self._lines(monitor)
        self.assertFalse(any("Variant判定待ち" in m for m in lines), lines)
        self.assertEqual(sum(m.startswith("判定: ") for m in lines), 1, lines)
        self.assertNotIn("_delayed_decision", [n for n, _a in monitor.booked])

        control = self._monitor(keep_on={KEY: {LISTED}})
        control._process(_start())
        with patch.object(control, "_replacement_changes_decision", return_value=True):
            control._on_killers([LISTED], "Classic", revealed=False)
        self.assertTrue(any("Variant判定待ち" in m for m in control.logs), "前提: 参加するラウンドは待つ")

    def test_the_continue_does_not_change_the_volume_class_or_the_after_round(self):
        SharedState.set_no_begin_after_continue(True)
        monitor = self._monitor(auto_begin=True, keep_on={KEY: {LISTED}})
        self._not_joined_round(monitor)
        monitor._on_killers([LISTED], "Classic", revealed=False)
        self.assertEqual(WindowVolume.category_of(monitor.st), WindowVolume.OTHER)
        self._quiet(monitor)

        monitor._process(ROUND_OVER)

        self.assertEqual(monitor.st.begin_skipped_seq, -1, "続行の後は自動Beginしない、が効かない")
        self.assertIn("do_after_round", [n for n, _a in monitor.booked])

    def test_a_fog_early_all_continue_line_is_not_repeated(self):
        monitor = self._monitor(continue_rounds={"Fog"})
        self._not_joined_round(monitor, "Fog")
        monitor.logs.clear()
        monitor._on_killers_unknown(LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_UNKNOWN, round_type="Fog"))
        self.assertEqual(self._lines(monitor), ["テラー不明 → revealed待ち", f"ラウンド指定で続行: Fog{NOTE}"])
        self.assertEqual(monitor.st.all_continue_early_seq, monitor.st.round_seq)
        self.assertFalse(monitor.st.all_continue_round)

        monitor.logs.clear()
        monitor._on_killers([UNLISTED], "Fog", revealed=True)

        lines = self._lines(monitor)
        self.assertFalse(any(m.startswith("ラウンド指定で続行") for m in lines), lines)
        self._quiet(monitor)

    def test_the_hands_free_instant_skip_is_a_line_only(self):
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        monitor = self._monitor()
        self._not_joined_round(monitor, "Fog")
        monitor.logs.clear()
        monitor._on_killers_unknown(LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_UNKNOWN, round_type="Fog"))
        self.assertEqual(self._lines(monitor)[-1], f"開始: Fog 【放置モード→即自爆】{SKIP_NOTE}")
        self.assertEqual(monitor.booked, [])

        control = self._monitor()
        control._process(_start("Fog"))
        control._on_killers_unknown(LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_UNKNOWN, round_type="Fog"))
        self.assertEqual(self._lines(control)[-1], "開始: Fog 【放置モード→即自爆】")
        self.assertIn("do_skip", [n for n, _a in control.booked], "前提: 参加するラウンドは今までどおり")

    def test_the_fog_identified_early_prints_one_line(self):
        monitor = self._monitor(keep_on={KEY: {LISTED}})
        self._not_joined_round(monitor, "Fog")
        monitor.logs.clear()
        monitor._identify_fog_terror(UNLISTED, "Enrage", "X")
        monitor._on_killers([UNLISTED], "Fog", revealed=True)
        lines = self._lines(monitor)
        self.assertEqual(sum(m.startswith("判定: ") for m in lines), 1, lines)
        self._quiet(monitor)


class TestRoundEnd(_Case):
    def test_after_the_round_nothing_special_is_announced(self):
        monitor = self._monitor(auto_begin=True)
        monitor.st.item_id = monitor.st.held_item_id = 29
        monitor.st.waiting_for_equip = False
        self._not_joined_round(monitor)
        monitor._process(ROUND_OVER)
        monitor._process(VERIFIED_END)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertIn("do_after_round", [n for n, _a in monitor.booked])
        self.assertEqual(monitor.st.begin_skipped_seq, -1)


class TestBoundaries(_Case):
    def test_a_round_over_before_the_start_takes_the_mark_down(self):
        monitor = self._monitor()
        monitor._process(NOT_OPTED_IN)
        monitor._process(ROUND_OVER)
        self.assertFalse(monitor.st.not_opted_in_pending)
        self.assertTrue(any("開始が来ませんでした" in m for m in self.debug), self.debug)
        monitor._process(_start())
        self.assertFalse(monitor._not_joined())
        self.assertFalse(monitor.st.died_this_round)

    def test_joining_another_instance_takes_the_mark_down(self):
        monitor = self._monitor()
        monitor._process(NOT_OPTED_IN)
        monitor._process(T + "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)")
        self.assertFalse(monitor.st.not_opted_in_pending)
        monitor._process(_start())
        self.assertFalse(monitor._not_joined())

    def test_the_window_vanishing_takes_the_mark_down(self):
        monitor = self._monitor()
        monitor._process(NOT_OPTED_IN)
        monitor._abandon_round("VRChat の窓が消えた")
        self.assertFalse(monitor.st.not_opted_in_pending)

    def test_an_opted_in_in_the_middle_does_not_cancel_it(self):
        monitor = self._monitor()
        self._not_joined_round(monitor)
        monitor._process(OPTED_IN)
        self.assertTrue(monitor._not_joined())
        self.assertTrue(monitor.st.died_this_round)

    def test_the_mark_ends_with_the_round(self):
        monitor = self._monitor()
        self._not_joined_round(monitor)
        seq = monitor.st.round_seq
        monitor._abandon_round("インスタンス移動")
        self.assertNotEqual(monitor.st.round_seq, seq)
        self.assertFalse(monitor._not_joined())


class _ReplayCase(unittest.TestCase):
    ME = "usr_0e01408a"
    JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        patch.object(ConnectDB, "register_round").start()
        patch.object(ConnectDB, "send_Users", return_value=7).start()
        patch.object(PlaySound, "play_sound").start()
        patch.object(config, "ITEMS", _item_table()).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)

    @staticmethod
    def _at(second):
        return f"2026.11.19 12:00:{second:02d} Debug      -  "

    def _started(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        rows = [(1, f"User Authenticated: serim01 ({self.ME})")] + list(lines)
        tmp.write("\n".join(self._at(sec) + line for sec, line in rows) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name), do_skip=True), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.booked = []
        monitor._start_daemon = lambda target, *a: monitor.booked.append((_name_of(target), a))
        monitor._detect_instance_from_log()
        return monitor

    @staticmethod
    def _round(round_type="Classic"):
        return f"This round is taking place at Sewers (12) and the round type is {round_type}"

    NOT_OPTED = "Not opted in. Not joining this round."
    OVER = "RoundOver"


class TestReplayOfTheMarks(_ReplayCase):
    def test_started_in_the_middle_of_a_not_joined_round(self):
        monitor = self._started([(2, self.JOIN), (10, self.NOT_OPTED), (10, self._round())])
        st = monitor.st
        self.assertTrue(monitor._not_joined())
        self.assertTrue(st.died_this_round)
        self.assertFalse(st.not_opted_in_pending)
        self.assertFalse(st.in_round, "in_round は今までどおり戻さない")
        self.assertIn(LINE + "（監視開始の時点で進んでいたラウンド）", self._lines(monitor))

    def test_the_killers_afterwards_print_the_decision_line_only(self):
        monitor = self._started([(2, self.JOIN), (10, self.NOT_OPTED), (10, self._round())])
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.logs.clear()
        monitor._on_killers([UNLISTED], "Classic", revealed=False)
        lines = self._lines(monitor)
        self.assertTrue(any(m.startswith("判定: ") and m.endswith(SKIP_NOTE) for m in lines), lines)
        self.assertEqual([n for n, _a in monitor.booked if n != "_send_statistics_after_wait"], [])

    def test_started_in_the_middle_of_a_joined_round(self):
        monitor = self._started([(2, self.JOIN), (10, self._round())])
        self.assertFalse(monitor._not_joined())
        self.assertFalse(monitor.st.died_this_round)
        self.assertFalse(monitor.st.not_opted_in_pending)

    def test_an_older_not_opted_in_does_not_leak_into_the_running_round(self):
        monitor = self._started([(2, self.JOIN), (5, self.NOT_OPTED), (5, self._round()), (7, self.OVER),
                                 (8, "Verified Round End"), (12, self._round())])
        self.assertFalse(monitor._not_joined())
        self.assertFalse(monitor.st.not_opted_in_pending)

    def test_a_not_opted_in_just_before_the_end_of_the_log_is_pending(self):
        monitor = self._started([(2, self.JOIN), (5, self._round()), (7, self.OVER), (8, "Verified Round End"),
                                 (12, self.NOT_OPTED)])
        self.assertTrue(monitor.st.not_opted_in_pending)
        self.assertFalse(monitor._not_joined())
        monitor._process(self._at(12) + self._round())
        self.assertTrue(monitor._not_joined())
        self.assertTrue(monitor.st.died_this_round)

    def test_a_not_opted_in_before_a_join_is_not_pending(self):
        monitor = self._started([(2, self.NOT_OPTED), (3, self.JOIN)])
        self.assertFalse(monitor.st.not_opted_in_pending)

    @staticmethod
    def _lines(monitor):
        return [m.split("] ", 1)[1] if "] " in m else m for m in monitor.logs]


class TestReplayOfTheHeldItem(_ReplayCase):
    EQUIP = "Equipping 29."
    RESPAWN = "Player respawned, opted out!"

    def _held(self, lines):
        return self._started([(2, self.JOIN), (3, self.EQUIP)] + lines).st.held_item_id

    def test_punished_without_joining_keeps_the_item(self):
        self.assertEqual(self._held([(10, self.NOT_OPTED), (10, self._round("Punished"))]), 29)

    def test_punished_with_joining_loses_it(self):
        self.assertEqual(self._held([(10, self._round("Punished"))]), 0)

    def test_the_mark_is_for_one_round_only(self):
        self.assertEqual(self._held([(10, self.NOT_OPTED), (10, self._round("Classic")), (20, self.OVER),
                                     (30, self._round("Punished"))]), 0)

    def test_a_respawn_in_the_lobby_loses_it(self):
        self.assertEqual(self._held([(10, self.RESPAWN)]), 0)

    def test_a_respawn_in_the_round_loses_it(self):
        self.assertEqual(self._held([(10, self._round()), (11, self.RESPAWN)]), 0)

    def test_a_reborn_in_a_not_joined_round_keeps_it(self):
        self.assertEqual(self._held([(10, self.NOT_OPTED), (10, self._round()), (12, "LOL JK, REBORN!")]), 29)
        self.assertEqual(self._held([(10, self._round()), (12, "LOL JK, REBORN!")]), 0)

    def test_run_death_in_a_not_joined_round_keeps_it(self):
        self.assertEqual(self._held([(10, self.NOT_OPTED), (10, self._round("Run")), (12, "You died.")]), 29)
        self.assertEqual(self._held([(10, self._round("Run")), (12, "You died.")]), 0)


if __name__ == "__main__":
    unittest.main()
