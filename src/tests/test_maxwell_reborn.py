"""猫（Maxwell）で復活したら、死亡を取り消してアイテムロストにする（GE）。

猫を持って死ぬと、ログは同じ秒に「You died.」→「LOL JK, REBORN!」と来て、生きている（猫は消える）。
死亡で予約した解除（続行フリーズ 5 秒・ラウンド突入フリーズ 2 秒）をしない・死亡の印を戻す・
アイテムロストにする。時計（time.sleep）・スレッド・窓・音・OSC はすべて偽物で、本物の入力は送らない。
"""
from tests.support import *  # noqa: F401,F403

T = "2026.10.21 08:58:10 Debug      -  "
DIED = T + "You died."
REBORN = T + "LOL JK, REBORN!"
ROUND_OVER = T + "RoundOver"
EQUIP_CAT = T + "Equipping 41."
MAXWELL = 41


def _items():
    table = _item_table()
    table[MAXWELL] = ItemCatalog.Item("Maxwell", "Survival", True)
    return table


class TestRebornParsing(unittest.TestCase):
    def test_the_reborn_line_is_a_reborn_event(self):
        for line in ("LOL JK, REBORN!", REBORN):
            event = LogParser.parse(line)
            self.assertIsNotNone(event, line)
            self.assertEqual(event.kind, LogParser.EVENT_REBORN, line)

    def test_you_died_is_still_a_death(self):
        for line in ("You died.", DIED):
            self.assertEqual(LogParser.parse(line).kind, LogParser.EVENT_YOU_DIED, line)

    def test_similar_lines_are_not_a_reborn(self):
        for line in ("LOL JK, REBORN", "LOL JK, REBORN! again", "lol jk, reborn!"):
            event = LogParser.parse(line)
            self.assertTrue(event is None or event.kind != LogParser.EVENT_REBORN, line)

    def test_the_mark_finds_the_line(self):
        self.assertIn(LogParser.REBORN_MARK, REBORN)


class _Case(unittest.TestCase):
    """死亡で予約した解除は走らせずに溜め、テストが好きなときに走らせる"""

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.set_continue_drop_item(False)
        for reset in (SharedState.continue_round_reset, SharedState.round_freeze_reset,
                      SharedState.speed_freeze_reset, SharedState.equip_freeze_reset):
            reset()
            self.addCleanup(reset)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.set_continue_drop_item, False)
        self.debug = []
        for p in (patch.object(config, "ITEMS", _items()),
                  patch.object(ConnectDB, "register_round"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_round_over"),
                  patch.object(DebugLog, "write", side_effect=self.debug.append)):
            p.start()
            self.addCleanup(p.stop)

    def _monitor(self, in_round=True, round_type="Classic"):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=9, auto_begin=False), {}, lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = in_round
        monitor.st.round_type = round_type
        monitor.booked = []         # (予約した関数の名前, 引数)
        monitor._start_daemon = lambda target, *a: monitor.booked.append((target, a))
        return monitor

    @staticmethod
    def _names(monitor):
        return [t.__name__ for t, _a in monitor.booked]

    def _run(self, monitor, index):
        """溜めた予約の index 番目を、眠らずに走らせる"""
        target, args = monitor.booked[index]
        with patch.object(LogMonitor.time, "sleep") as sleep:
            target(*args)
        sleep.assert_called_once()

    def _continuing(self):
        monitor = self._monitor()
        monitor.st.is_continue_round = True
        SharedState.continue_round_start(monitor.st)
        self.assertEqual(SharedState.get_continue_round_count(), 1)
        return monitor

    def _round_frozen(self):
        monitor = self._monitor()
        SharedState.round_freeze_start(monitor.st)
        self.assertTrue(monitor.st.round_freeze_held)
        return monitor


class TestContinueFreezeAfterReborn(_Case):
    def test_a_reborn_cancels_the_release_booked_by_the_death(self):
        monitor = self._continuing()
        monitor._process(DIED)
        self.assertEqual(self._names(monitor), ["_release_continue_freeze_after_delay"])

        monitor._process(REBORN)
        self._run(monitor, 0)

        self.assertEqual(SharedState.get_continue_round_count(), 1, "解除しない")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertTrue(monitor.st.continue_freeze_held)
        self.assertTrue(any("復活したので、死亡からの解除はしません" in m for m in self.debug), self.debug)

    def test_a_death_without_a_reborn_still_releases(self):
        monitor = self._continuing()
        monitor._process(DIED)

        self._run(monitor, 0)

        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertFalse(monitor.st.is_continue_round)

    def test_the_release_booked_at_round_over_still_works_after_a_reborn(self):
        monitor = self._continuing()
        monitor._process(DIED)
        monitor._process(REBORN)
        with patch.object(monitor._action, "_attend_to_item_loss"):
            monitor._process(ROUND_OVER)
        self.assertEqual(self._names(monitor), ["_release_continue_freeze_after_delay"] * 2)
        self.assertEqual(monitor.booked[1][1], (monitor.st.round_seq,),
                         "RoundOver からの予約は復活の番号を持たない")

        self._run(monitor, 0)
        self.assertEqual(SharedState.get_continue_round_count(), 1, "死亡からの予約は効かない")
        self._run(monitor, 1)

        self.assertEqual(SharedState.get_continue_round_count(), 0, "RoundOver からの予約は解除する")
        self.assertFalse(monitor.st.is_continue_round)

    def test_a_second_real_death_after_the_reborn_releases(self):
        monitor = self._continuing()
        monitor._process(DIED)
        monitor._process(REBORN)
        monitor._process(DIED)
        self.assertEqual(len(monitor.booked), 2)
        self.assertEqual(monitor.booked[0][1][1], 0)
        self.assertEqual(monitor.booked[1][1][1], 1, "復活の後の死亡は新しい番号を持つ")

        self._run(monitor, 0)
        self.assertEqual(SharedState.get_continue_round_count(), 1)
        self._run(monitor, 1)

        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertFalse(monitor.st.is_continue_round)

    def test_a_release_without_a_number_ignores_the_reborn_count(self):
        monitor = self._continuing()
        monitor.st.reborn_seq = 3

        with patch.object(LogMonitor.time, "sleep"):
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)

        self.assertEqual(SharedState.get_continue_round_count(), 0)


class TestRoundFreezeAfterReborn(_Case):
    def test_a_reborn_cancels_the_round_freeze_release_booked_by_the_death(self):
        monitor = self._round_frozen()
        monitor._process(DIED)
        self.assertEqual(self._names(monitor), ["_release_round_freeze_after_delay"])

        monitor._process(REBORN)
        self._run(monitor, 0)

        self.assertTrue(monitor.st.round_freeze_held)
        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set(), "他窓は止まったまま")
        self.assertTrue(any("復活したので、死亡からの解除はしません" in m for m in self.debug), self.debug)

    def test_a_death_without_a_reborn_still_releases(self):
        monitor = self._round_frozen()
        monitor._process(DIED)

        self._run(monitor, 0)

        self.assertFalse(monitor.st.round_freeze_held)
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())

    def test_a_second_real_death_after_the_reborn_releases(self):
        monitor = self._round_frozen()
        monitor._process(DIED)
        monitor._process(REBORN)
        monitor._process(DIED)
        self.assertEqual(self._names(monitor), ["_release_round_freeze_after_delay"] * 2)

        self._run(monitor, 0)
        self.assertTrue(monitor.st.round_freeze_held)
        self._run(monitor, 1)

        self.assertFalse(monitor.st.round_freeze_held)
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())


class TestRebornCancelsTheDeath(_Case):
    def test_the_state_is_reset_and_the_item_is_lost(self):
        monitor = self._monitor()
        monitor._process(EQUIP_CAT)
        self.assertEqual((monitor.st.item_id, monitor.st.held_item_id), (MAXWELL, MAXWELL))
        monitor.logs.clear()

        monitor._process(DIED)
        self.assertTrue(monitor.st.died_this_round)
        monitor._process(REBORN)

        st = monitor.st
        self.assertEqual(st.reborn_seq, 1)
        self.assertFalse(st.died_this_round)
        self.assertTrue(st.item_lost_this_round)
        self.assertEqual(st.item_id, 0)
        self.assertEqual(st.held_item_id, 0)
        self.assertEqual(st.last_lost_item_id, MAXWELL)
        lines = [m.split("] ", 1)[1] for m in monitor.logs]
        self.assertIn("猫消費: アイテムロスト", lines)
        self.assertIn("所持アイテム: なし（Maxwell を 猫消費 でロスト）", lines)
        self.assertFalse(any("死亡を取り消し" in m for m in lines), "画面には出さない")
        self.assertTrue(any("猫消費 → 死亡を取り消し" in m and "[窓1]" in m for m in self.debug), self.debug)

    def test_the_drop_on_death_can_be_used_again(self):
        SharedState.set_continue_drop_item(True)
        monitor = self._monitor()
        monitor.st.round_seq = 5
        monitor.st.dropped_round_seq = 5            # 死んだときに落とした
        monitor._process(REBORN)
        self.assertEqual(monitor.st.dropped_round_seq, -1)

    def test_the_drop_mark_of_another_round_is_left_alone(self):
        monitor = self._monitor()
        monitor.st.round_seq = 5
        monitor.st.dropped_round_seq = 4
        monitor._process(REBORN)
        self.assertEqual(monitor.st.dropped_round_seq, 4)

    def test_dying_then_a_reborn_then_a_real_death_drops_twice(self):
        SharedState.set_continue_drop_item(True)
        monitor = self._monitor()
        monitor._process(DIED)
        monitor._process(REBORN)
        monitor._process(DIED)
        drops = [a for t, a in monitor.booked if t.__name__ == "drop_at_round_end"]
        self.assertEqual(len(drops), 2)

    def test_the_round_end_treats_it_as_an_item_loss(self):
        monitor = self._monitor()
        monitor._process(EQUIP_CAT)
        monitor._process(DIED)
        monitor._process(REBORN)
        self.assertTrue(monitor._round_item_warning())

        with patch.object(monitor._action, "_attend_to_item_loss") as attend:
            monitor._process(ROUND_OVER)

        attend.assert_called_once()
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertTrue(any("アイテムロスト" in m and "RoundOver" in m for m in monitor.logs), monitor.logs)

    def test_without_a_reborn_a_classic_death_is_not_an_item_loss(self):
        monitor = self._monitor()
        monitor._process(EQUIP_CAT)
        monitor._process(DIED)
        self.assertFalse(monitor._round_item_warning())
        self.assertEqual(monitor.st.held_item_id, MAXWELL)

    def test_a_reborn_outside_a_round_does_nothing(self):
        monitor = self._monitor(in_round=False)
        monitor._process(EQUIP_CAT)
        monitor.st.died_this_round = True
        monitor.logs.clear()
        self.debug.clear()

        monitor._process(REBORN)

        st = monitor.st
        self.assertEqual(st.reborn_seq, 0)
        self.assertTrue(st.died_this_round)
        self.assertFalse(st.item_lost_this_round)
        self.assertEqual((st.item_id, st.held_item_id, st.last_lost_item_id),
                         (MAXWELL, MAXWELL, 0))
        self.assertEqual(monitor.logs, [])
        self.assertFalse(any("死亡を取り消し" in m for m in self.debug), self.debug)

    def test_a_reborn_without_a_held_item_says_so(self):
        """もともと持っていなかった（前のロストから未回収）ときは、既存の書き方に従う"""
        monitor = self._monitor()
        monitor.st.item_id = 0
        monitor._process(REBORN)
        self.assertTrue(monitor.st.item_lost_this_round)
        self.assertTrue(any("猫消費: アイテムなし" in m for m in monitor.logs), monitor.logs)


class TestRebornReplay(unittest.TestCase):
    """監視開始の遡り: 猫を消費した後なら、所持は 0 と分かる"""

    PREFIX = "2026.10.21 08:50:00 Debug      -  "
    ME = "usr_0e01408a"
    JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"
    START = "This round is taking place at Sewers (12) and the round type is Classic"

    def setUp(self):
        p = patch.object(config, "ITEMS", _items())
        p.start()
        self.addCleanup(p.stop)

    def _restore(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("\n".join(self.PREFIX + line for line in
                            [f"User Authenticated: serim01 ({self.ME})"] + lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name)), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        with patch.object(ConnectDB, "send_Users", return_value=7), \
             patch.object(monitor, "_start_daemon"), \
             patch.object(DebugLog, "write"):
            monitor._detect_instance_from_log()
        return monitor

    def test_equip_start_death_reborn_leaves_nothing_held(self):
        monitor = self._restore([self.JOIN, "Equipping 41.", self.START,
                                 "You died.", "LOL JK, REBORN!"])
        self.assertEqual(monitor.st.held_item_id, 0)

    def test_without_the_reborn_the_cat_is_still_held(self):
        monitor = self._restore([self.JOIN, "Equipping 41.", self.START, "You died."])
        self.assertEqual(monitor.st.held_item_id, MAXWELL)

    def test_a_reborn_outside_a_round_keeps_it(self):
        monitor = self._restore([self.JOIN, "Equipping 41.", self.START, "RoundOver",
                                 "LOL JK, REBORN!"])
        self.assertEqual(monitor.st.held_item_id, MAXWELL)

    def test_the_cat_equipped_after_the_reborn_is_held(self):
        monitor = self._restore([self.JOIN, "Equipping 41.", self.START, "You died.",
                                 "LOL JK, REBORN!", "Equipping 41."])
        self.assertEqual(monitor.st.held_item_id, MAXWELL)

    def test_the_scan_sees_the_reborn_line(self):
        self.assertIn(LogParser.REBORN_MARK, LogMonitor.LogMonitor._START_SCAN_MARKS)
        self.assertIn(LogParser.EVENT_REBORN, LogMonitor.LogMonitor._HELD_ITEM_REPLAY_KINDS)
