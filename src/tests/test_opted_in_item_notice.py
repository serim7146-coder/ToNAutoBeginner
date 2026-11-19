"""リスポーンでアイテムが外れたときの案内（依頼者 2026-11-19）。

リスポーンの並びは「Player respawned, opted out!」→ 同じ秒にアイテムが外れる → 約 3 秒後に「opted in」→ 利用者が装備し直す。
ラウンドの外のリスポーンは、アイテムが外れていたら、ラウンドの外で「opted in」が来た時点で、今のアイテムロストの案内
（その窓を前面化・音声・全窓フリーズ、解除は装備）をその場で出す。ラウンド中のリスポーンは今までどおり RoundOver の経路。
時計・スレッド・窓・音・OSC は偽物で、本物の入力は送らない。
"""
from tests.support import *  # noqa: F401,F403
import Recorder

T = "2026.11.19 12:00:00 Debug      -  "
RESPAWN = T + "Player respawned, opted out!"
OPTED_IN = T + "opted in"
NOT_OPTED_IN = T + "Not opted in. Not joining this round."
ROUND_OVER = T + "RoundOver"
VERIFIED_END = T + "Verified Round End"
ROUND_START = T + "This round is taking place at Sewers (12) and the round type is Classic"
EQUIP = T + "Equipping 29."
JOINING = T + "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"

NOTICE = LogMonitor.RESPAWN_ITEM_NOTICE
GIVEN = "リスポーンで外れたアイテムは案内済み・装備待ち"
ROUND_OVER_LOST = "RoundOver 【⚠ アイテムロスト → 全窓フリーズ（装備かラウンド開始で解除）】"
MODE_LOST = "RoundOver 【⚠ アイテムロスト → 全窓フリーズ開始】"


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.set_item_begin_mode(False)
        SharedState.set_item_fetch(False)
        SharedState.set_no_begin_after_continue(False)
        for reset in (SharedState.equip_freeze_reset, SharedState.continue_round_reset,
                      SharedState.round_freeze_reset, SharedState.speed_freeze_reset):
            reset()
            self.addCleanup(reset)
        for undo in (SharedState.set_hands_free, SharedState.set_item_begin_mode, SharedState.set_item_fetch):
            self.addCleanup(undo, False)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.debug = []
        patch.object(ConnectDB, "register_round").start()
        self.play = patch.object(PlaySound, "play_sound").start()
        self.borrow = patch.object(WindowOperator, "borrow_front", return_value=(True, None)).start()
        patch.object(Recorder, "on_round_over").start()
        patch.object(config, "ITEMS", _item_table()).start()
        patch.object(DebugLog, "write", side_effect=self.debug.append).start()
        self.thread = patch.object(LogMonitor.threading, "Thread").start()
        self.addCleanup(patch.stopall)

    def _monitor(self, auto_begin=False, in_round=False, item=29):
        cfg = WindowConfig(hwnd=0x10, auto_begin=auto_begin, voice_item_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.booked = []
        monitor._start_daemon = lambda target, *a: monitor.booked.append((target.__name__, a))
        monitor._action.remember_continue_window = lambda: None
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        st.in_round = in_round
        st.round_type = "Classic"
        st.round_seq = 7
        st.item_id = st.held_item_id = item
        return monitor

    @staticmethod
    def _lines(monitor):
        return [m.split("] ", 1)[1] if "] " in m else m for m in monitor.logs]

    def _count(self, monitor, text):
        return sum(1 for m in self._lines(monitor) if text in m)

    def _announced(self):
        """前面化の回数と、アイテムロストの音声の回数"""
        voices = [c for c in self.play.call_args_list if c.kwargs.get("kind") == "item_lost"]
        return self.borrow.call_count, len(voices)


class TestRespawnInTheLobby(_Case):
    def test_the_item_is_lost_and_the_notice_waits_for_opted_in(self):
        monitor = self._monitor()
        monitor._process(RESPAWN)

        st = monitor.st
        self.assertEqual((st.item_id, st.held_item_id), (0, 0))
        self.assertEqual(st.last_lost_item_id, 29)
        self.assertTrue(st.item_lost_this_round)
        self.assertTrue(st.respawn_item_pending)
        self.assertFalse(st.waiting_for_equip)
        self.assertEqual(self._announced(), (0, 0), "リスポーンの行ではまだ案内しない")
        self.assertIn("リスポーン: アイテムロスト", self._lines(monitor))

    def test_the_notice_comes_with_opted_in(self):
        monitor = self._monitor()
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)

        st = monitor.st
        self.assertTrue(st.waiting_for_equip)
        self.assertTrue(st.equip_freeze_held)
        self.assertFalse(st.respawn_item_pending)
        self.assertEqual(st.respawn_notice_seq, st.round_seq)
        self.assertEqual(self._announced(), (1, 1))
        self.assertEqual(self._count(monitor, NOTICE), 1, monitor.logs)

    def test_no_item_no_notice(self):
        monitor = self._monitor(item=0)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        st = monitor.st
        self.assertFalse(st.respawn_item_pending)
        self.assertFalse(st.waiting_for_equip)
        self.assertEqual(self._announced(), (0, 0))
        self.assertEqual(monitor.logs, [])

    def test_an_opted_in_without_a_respawn_does_nothing(self):
        monitor = self._monitor()
        monitor._process(OPTED_IN)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertEqual(self._announced(), (0, 0))

    def test_the_voice_plays_even_if_it_played_in_the_same_round_number(self):
        monitor = self._monitor()
        monitor.st.item_lost_announced = True
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (1, 1))

    def test_the_respawn_the_tool_pressed_in_the_lobby_is_the_same(self):
        """店の後・Begin のやり直しでツールが押した分（_respawn_lobby が True の間）"""
        monitor = self._monitor(auto_begin=True)
        monitor._action._respawn_lobby = True
        monitor._process(RESPAWN)
        monitor._action._respawn_lobby = False
        monitor._process(OPTED_IN)
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertEqual(self._announced(), (1, 1))

    def test_a_hands_free_window_is_not_told_but_the_tracking_changes(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(auto_begin=True)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        st = monitor.st
        self.assertEqual((st.item_id, st.held_item_id), (0, 0))
        self.assertEqual(st.last_lost_item_id, 29)
        self.assertFalse(st.respawn_item_pending)
        self.assertFalse(st.waiting_for_equip)
        self.assertEqual(self._announced(), (0, 0))

    def test_an_auto_fetch_window_is_not_told(self):
        monitor = self._monitor(auto_begin=True)
        monitor._action.item_fetch_target = lambda: ("Survival", 29)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        st = monitor.st
        self.assertFalse(st.respawn_item_pending, "案内しないと決めたので下ろす")
        self.assertFalse(st.waiting_for_equip)
        self.assertEqual(st.respawn_notice_seq, -1)
        self.assertEqual(self._announced(), (0, 0))
        self.assertEqual(st.last_lost_item_id, 29, "Begin の後に取りに行く材料は残る")

    def test_an_item_begin_mode_window_is_told(self):
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor(auto_begin=True)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertEqual(self._announced(), (1, 1))

    def test_the_item_equipped_before_opted_in_is_not_told(self):
        monitor = self._monitor()
        monitor._process(RESPAWN)
        monitor._process(EQUIP)
        self.assertFalse(monitor.st.respawn_item_pending)
        monitor._process(OPTED_IN)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertEqual(self._announced(), (0, 0))

    def test_a_second_opted_in_does_not_tell_twice(self):
        monitor = self._monitor()
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (1, 1))
        self.assertEqual(self._count(monitor, NOTICE), 1)

    def test_joining_another_instance_drops_the_wait(self):
        monitor = self._monitor()
        monitor._process(RESPAWN)
        monitor._process(JOINING)
        self.assertFalse(monitor.st.respawn_item_pending)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (0, 0))


class TestAfterTheNotice(_Case):
    def _told(self, auto_begin=False):
        monitor = self._monitor(auto_begin=auto_begin)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        self.assertTrue(monitor.st.equip_freeze_held, "前提: 案内した")
        return monitor

    def test_equipping_releases_after_the_grace_even_without_a_begin(self):
        for auto_begin in (True, False):
            with self.subTest(auto_begin=auto_begin):
                SharedState.equip_freeze_reset()
                monitor = self._told(auto_begin)
                self.assertFalse(monitor.st.begin_done)

                monitor._process(EQUIP)

                self.assertFalse(monitor.st.waiting_for_equip)
                names = [n for n, _a in monitor.booked]
                self.assertEqual(names.count("_release_equip_freeze_after_equip"), 1, names)
                self.assertTrue(monitor.st.equip_freeze_held, "猶予の間は止めたまま")
                with patch.object(LogMonitor.time, "sleep") as sleep:
                    monitor._release_equip_freeze_after_equip()
                sleep.assert_called_once_with(config.EQUIP_RELEASE_DELAY_SEC)
                self.assertFalse(monitor.st.equip_freeze_held)
                self.assertEqual(SharedState.get_equip_freeze_count(), 0)

    def test_a_round_over_after_the_notice_does_not_tell_again(self):
        monitor = self._told()
        monitor._process(ROUND_OVER)
        self.assertEqual(self._announced(), (1, 1))
        self.assertEqual(self._count(monitor, ROUND_OVER_LOST), 0)
        self.assertEqual(self._count(monitor, f"RoundOver（{GIVEN}）"), 1, monitor.logs)
        self.assertTrue(monitor.st.waiting_for_equip, "装備待ちは続ける")
        self.assertFalse(monitor.st.respawn_item_pending)

    def test_a_verified_round_end_after_the_notice_does_not_tell_again(self):
        for auto_begin in (True, False):
            with self.subTest(auto_begin=auto_begin):
                self.play.reset_mock()
                self.borrow.reset_mock()
                SharedState.equip_freeze_reset()
                monitor = self._told(auto_begin)
                monitor._process(VERIFIED_END)
                self.assertEqual(self._announced(), (1, 1))
                self.assertEqual(self._count(monitor, f"ラウンド終了（{GIVEN}）"), 1, monitor.logs)
                self.assertFalse(any("⚠ アイテムロスト" in m for m in monitor.logs), monitor.logs)
                self.assertTrue(monitor.st.waiting_for_equip)
                self.assertFalse(monitor.st.respawn_item_pending)

    def test_the_second_half_of_do_after_round_does_not_tell_again(self):
        for notice, expected in ((True, 0), (False, 1)):
            with self.subTest(notice=notice):
                cfg = WindowConfig(hwnd=0x10, osc_port=9000, auto_begin=True)
                st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=1, round_seq=7,
                                 round_end_seen=True, waiting_for_equip=True, item_id=0, begin_done=True)
                if notice:
                    st.respawn_notice_seq = 7
                ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                                   auto_begin_active=lambda: True)
                ex._osc = MagicMock()
                ex._press_begin = lambda again=False, click_only=False: True
                ex._begin_move = lambda: None
                ex._start_use_spam = lambda seq: None
                ex._wait_other_windows = lambda: True
                ex._begin_precheck = lambda check_freeze=True: True
                ex.item_fetch_target = lambda: None
                attended = []
                ex._attend_to_item_loss = lambda: attended.append(1)
                with patch.object(config, "BEGIN_WAIT_SEC", 0), \
                        patch.object(ActionExecutor.time, "sleep"), \
                        patch.object(ex, "_is_running", side_effect=[True] * 12 + [False] * 5):
                    ex.do_after_round()
                self.assertEqual(len(attended), expected)


class TestRespawnInTheRound(_Case):
    def test_a_respawn_in_the_round_is_as_before(self):
        monitor = self._monitor(in_round=True)
        monitor._process(RESPAWN)
        st = monitor.st
        self.assertTrue(st.died_this_round)
        self.assertFalse(st.respawn_item_pending, "ラウンド中は opted in の案内を待たない")
        self.assertEqual((st.item_id, st.held_item_id), (0, 0))
        monitor._process(OPTED_IN)
        self.assertFalse(st.waiting_for_equip)
        self.assertEqual(self._announced(), (0, 0), "ラウンド中の opted in では案内しない")

        monitor._process(ROUND_OVER)
        self.assertEqual(self._count(monitor, ROUND_OVER_LOST), 1, monitor.logs)
        self.assertEqual(self._announced(), (1, 1), "RoundOver の今の経路で案内")
        self.assertTrue(st.waiting_for_equip)

    def test_a_respawn_in_a_round_then_verified_round_end_is_as_before(self):
        monitor = self._monitor(in_round=True, auto_begin=True)
        monitor._process(RESPAWN)
        monitor._process(ROUND_OVER)
        monitor._process(VERIFIED_END)
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertEqual(self._count(monitor, "ラウンド終了 【⚠ アイテムロスト → Begin が通ったらフリーズ】"), 1, monitor.logs)

    def test_a_respawn_in_a_not_joined_round_is_told_at_the_round_over(self):
        monitor = self._monitor()
        monitor._process(NOT_OPTED_IN)
        monitor._process(ROUND_START)
        monitor.st.item_id = monitor.st.held_item_id = 29     # 参加していないので失われていない
        monitor._process(RESPAWN)
        st = monitor.st
        self.assertTrue(st.respawn_item_pending)
        self.assertTrue(st.item_lost_this_round)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (0, 0), "ラウンドの中で来た opted in では案内しない")
        self.assertTrue(st.respawn_item_pending, "RoundOver の経路まで残す")

        monitor._process(ROUND_OVER)

        self.assertEqual(self._count(monitor, ROUND_OVER_LOST), 1, monitor.logs)
        self.assertEqual(self._announced(), (1, 1))
        self.assertFalse(st.respawn_item_pending)

    def test_an_opted_in_after_the_round_start_waits_for_the_round_over(self):
        monitor = self._monitor()
        monitor._process(RESPAWN)
        monitor._process(ROUND_START)
        monitor._process(OPTED_IN)
        st = monitor.st
        self.assertEqual(self._announced(), (0, 0))
        self.assertFalse(st.waiting_for_equip)
        self.assertEqual(st.respawn_notice_seq, -1)
        monitor._process(ROUND_OVER)
        self.assertEqual(self._announced(), (1, 1), "RoundOver の今の経路で案内")
        self.assertEqual(self._count(monitor, ROUND_OVER_LOST), 1, monitor.logs)
        self.assertFalse(st.respawn_item_pending)


class TestNoDoubleNotice(_Case):
    def test_without_opted_in_the_round_over_tells_as_before_and_a_late_opted_in_does_not_repeat(self):
        monitor = self._monitor(in_round=True)
        monitor._process(NOT_OPTED_IN)       # 参加していないラウンドの扱いにしたい: 開始の行を通す
        monitor.st.in_round = False
        monitor._process(ROUND_START)
        monitor.st.item_id = monitor.st.held_item_id = 29
        monitor._process(RESPAWN)
        monitor._process(ROUND_OVER)
        self.assertEqual(self._announced(), (1, 1))
        monitor._process(VERIFIED_END)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (1, 1), "後から opted in が来ても二重にしない")
        self.assertEqual(self._count(monitor, NOTICE), 0)

    def test_a_respawn_between_round_over_and_verified_round_end(self):
        """自動Begin の窓: Verified Round End の経路で案内する。後の opted in は重ねない"""
        monitor = self._monitor(in_round=True, auto_begin=True)
        monitor._process(ROUND_OVER)
        monitor._process(RESPAWN)
        monitor._process(VERIFIED_END)
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.respawn_item_pending)
        monitor._process(OPTED_IN)
        self.assertEqual(self._count(monitor, NOTICE), 0)

    def test_a_respawn_between_round_over_and_verified_round_end_for_a_window_that_does_not_begin(self):
        """自動Begin OFF の窓: Verified Round End は「RoundOver 時に通知予定」とだけ言い、案内は出ない。
        取りこぼさないよう、ロビーで opted in が来たら案内する"""
        monitor = self._monitor(in_round=True, auto_begin=False)
        monitor._process(ROUND_OVER)
        monitor._process(RESPAWN)
        monitor._process(VERIFIED_END)
        self.assertEqual(self._announced(), (0, 0))
        self.assertTrue(monitor.st.respawn_item_pending)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (1, 1))
        self.assertEqual(self._count(monitor, NOTICE), 1)

    def test_a_notice_already_given_by_the_round_over_is_not_given_again(self):
        """RoundOver の経路が先に案内していたら（装備待ちを張っている）、opted in では重ねない"""
        monitor = self._monitor()
        monitor.st.waiting_for_equip = True
        SharedState.equip_freeze_start(monitor.st)
        monitor.st.respawn_item_pending = True
        monitor.st.item_id = 0
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (0, 0))
        self.assertEqual(self._count(monitor, NOTICE), 0)


class TestTheWatchForTheSameRoundNumber(_Case):
    def _executor(self):
        cfg = WindowConfig(hwnd=0x10, osc_port=9000, auto_begin=True, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=1, round_seq=7,
                         waiting_for_equip=True, item_id=0)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                           auto_begin_active=lambda: True)
        return ex, st

    def test_the_second_notice_in_the_same_round_number_starts_a_watch_again(self):
        other = WindowState(window_idx=2)
        SharedState.continue_round_start(other)         # ほかの窓が止まっている → 見張る
        ex, st = self._executor()
        with patch.object(ActionExecutor.TaskStats, "thread") as thread:
            ex._attend_to_item_loss()
            self.assertEqual(thread.call_count, 1)
            ex._attend_to_item_loss()
            self.assertEqual(thread.call_count, 1, "もう見張っている間は増やさない")

            st.waiting_for_equip = False            # 装備できた → 見張りは何もせず終わる
            ex._watch_item_loss(7)
            self.assertEqual(ex._item_loss_watch_seq, -1, "終わったら印を戻す")

            st.waiting_for_equip = True
            ex._attend_to_item_loss()
            self.assertEqual(thread.call_count, 2, "同じラウンドの番号でも 2 回目の案内で見張りが立つ")

    def test_a_watch_of_an_older_round_does_not_clear_the_newer_mark(self):
        ex, st = self._executor()
        ex._item_loss_watch_seq = 8
        st.waiting_for_equip = False
        ex._watch_item_loss(7)
        self.assertEqual(ex._item_loss_watch_seq, 8)


if __name__ == "__main__":
    unittest.main()
