"""ラウンド中のリスポーン（Player respawned, opted out!）を、死亡とほぼ同じに扱う（HA。依頼者 2026-10-31）。

続行ラウンドで自分でリスポーンすると「You died.」が出ず、死亡で止まるもの（AFK 回避の見張り・DTM/Waldo と
Glorbo の AFK 解除ループ・このラウンドの自爆・続行フリーズとラウンド突入フリーズの解除予約）が止まらなかった。
リスポーンは死亡と同じ後始末をする。ただし自爆成功の判定・Run のロストの二重書き・ラウンド終わりに落とす処理はしない。
時計（time.sleep）・スレッド・窓・音・OSC はすべて偽物で、本物の入力は送らない。
"""
from tests.support import *  # noqa: F401,F403
import tests.support as support

T = "2026.10.31 12:00:00 Debug      -  "
DIED = T + "You died."
RESPAWN = T + "Player respawned, opted out!"
EQUIP = T + "Equipping 29."


def _clock():
    now = [1000.0]
    fake = type(sys)("time_for_test")
    fake.time = fake.monotonic = lambda: now[0]
    fake.sleep = lambda sec: now.__setitem__(0, now[0] + sec)
    return fake, now


class _Case(unittest.TestCase):
    """予約した解除は走らせずに溜め、テストが好きなときに走らせる"""

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
        for p in (patch.object(config, "ITEMS", _item_table()),
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
        monitor.st.round_seq = 4
        monitor.booked = []         # (予約した関数, 引数)
        monitor._start_daemon = lambda target, *a: monitor.booked.append((target, a))
        return monitor

    @staticmethod
    def _names(monitor):
        return [t.__name__ for t, _a in monitor.booked]

    @staticmethod
    def _run(monitor, index):
        """溜めた予約の index 番目を、眠らずに走らせる。眠った秒数を返す"""
        target, args = monitor.booked[index]
        with patch.object(LogMonitor.time, "sleep") as sleep:
            target(*args)
        sleep.assert_called_once()
        return sleep.call_args.args[0]

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

    @staticmethod
    def _lines(monitor):
        return [m.split("] ", 1)[1] if "] " in m else m for m in monitor.logs]


class TestRespawnMarksOutOfRound(_Case):
    def test_the_marks_are_the_same_as_a_death(self):
        for line in (DIED, RESPAWN):
            monitor = self._monitor()
            monitor.st.item_equipped_after_death = True
            monitor.st.is_open_special_round_round = True
            monitor.st.glorbo_afk = True

            monitor._process(line)

            st = monitor.st
            self.assertTrue(st.died_this_round, line)
            self.assertFalse(st.item_equipped_after_death, line)
            self.assertFalse(st.is_open_special_round_round, line)
            self.assertFalse(st.glorbo_afk, line)

    def test_the_old_respawn_handling_stays(self):
        monitor = self._monitor()
        monitor._process(EQUIP)
        self.assertEqual(monitor.st.held_item_id, 29)
        monitor.logs.clear()

        monitor._process(RESPAWN)

        st = monitor.st
        self.assertEqual(st.respawn_seen_seq, 1)
        self.assertTrue(st.item_lost_this_round)
        self.assertEqual((st.item_id, st.held_item_id), (0, 0))
        self.assertIn("リスポーン: アイテムロスト", self._lines(monitor))
        self.assertTrue(any("リスポーン → 死亡と同じ扱い" in m and "[窓1]" in m for m in self.debug), self.debug)

    def test_a_respawn_in_the_lobby_does_nothing_new(self):
        monitor = self._monitor(in_round=False)
        monitor.st.is_continue_round = True
        monitor.st.glorbo_afk = True
        SharedState.round_freeze_start(monitor.st)
        self.debug.clear()

        monitor._process(RESPAWN)

        st = monitor.st
        self.assertEqual(st.respawn_seen_seq, 1, "数えるのは今までどおり")
        self.assertFalse(st.died_this_round)
        self.assertTrue(st.glorbo_afk)
        self.assertFalse(st.item_lost_this_round)
        self.assertEqual(monitor.booked, [])
        self.assertEqual(monitor.logs, [])
        self.assertFalse(any("死亡と同じ扱い" in m for m in self.debug), self.debug)

    def test_a_death_clears_the_glorbo_mark_now(self):
        monitor = self._monitor()
        monitor.st.glorbo_afk = True
        monitor._process(DIED)
        self.assertFalse(monitor.st.glorbo_afk)


class TestReleasesAfterRespawn(_Case):
    def test_the_continue_freeze_release_is_booked_with_the_death_delay(self):
        monitor = self._continuing()
        monitor._process(RESPAWN)
        self.assertEqual(self._names(monitor), ["_release_continue_freeze_after_delay"])
        self.assertEqual(monitor.booked[0][1], (4, 0, "リスポーン"))

        slept = self._run(monitor, 0)

        self.assertEqual(slept, config.CONTINUE_FREEZE_RELEASE_DELAY_SEC, "死亡と同じ遅れ")
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertFalse(monitor.st.is_continue_round)
        self.assertIn(f"続行ラウンド終了 → 他窓フリーズ解除（リスポーンから{slept}秒）", self._lines(monitor))

    def test_a_death_keeps_its_wording(self):
        monitor = self._continuing()
        monitor._process(DIED)
        self.assertEqual(monitor.booked[0][1], (4, 0), "死亡の予約の引数は今までどおり")
        slept = self._run(monitor, 0)
        self.assertIn(f"続行ラウンド終了 → 他窓フリーズ解除（死亡から{slept}秒）", self._lines(monitor))

    def test_the_old_call_without_a_label_still_says_death(self):
        monitor = self._continuing()
        with patch.object(LogMonitor.time, "sleep"):
            monitor._release_continue_freeze_after_delay(4)
        self.assertTrue(any("（死亡から" in m for m in monitor.logs), monitor.logs)

    def test_the_foggy_delay_is_the_same_as_a_death(self):
        monitor = self._continuing()
        monitor.st.fog = True
        monitor._process(RESPAWN)
        self.assertEqual(self._run(monitor, 0), config.FOG_FREEZE_RELEASE_DELAY_SEC)

    def test_the_round_freeze_release_is_booked_and_says_respawn(self):
        monitor = self._round_frozen()
        monitor._process(RESPAWN)
        self.assertEqual(self._names(monitor), ["_release_round_freeze_after_delay"])
        self.assertEqual(monitor.booked[0][1], (4, 0, "リスポーン"))

        slept = self._run(monitor, 0)

        self.assertEqual(slept, config.FOG_FREEZE_RELEASE_DELAY_SEC)
        self.assertFalse(monitor.st.round_freeze_held)
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertIn(f"ラウンド突入フリーズ解除（リスポーンから{config.FOG_FREEZE_RELEASE_DELAY_SEC}秒）",
                      self._lines(monitor))

    def test_nothing_is_booked_without_a_freeze(self):
        monitor = self._monitor()
        monitor._process(RESPAWN)
        self.assertEqual(monitor.booked, [])

    def test_a_reborn_after_the_respawn_cancels_the_release_like_a_death(self):
        monitor = self._continuing()
        monitor._process(RESPAWN)
        monitor._process(T + "LOL JK, REBORN!")
        self._run(monitor, 0)
        self.assertEqual(SharedState.get_continue_round_count(), 1, "復活の番号が変わったので解除しない")


class TestWhatTheRespawnDoesNotDo(_Case):
    def test_no_suicide_success_even_right_after_the_hold(self):
        for line, expected in ((DIED, True), (RESPAWN, False)):
            monitor = self._monitor()
            monitor.st._skip_time = time.time()      # 長押しの直後
            monitor._process(line)
            self.assertEqual("✅ 自爆成功" in self._lines(monitor), expected, line)
            self.assertEqual(monitor.st._skip_time == 0.0, expected, line)

    def test_the_item_is_not_dropped_at_the_respawn(self):
        SharedState.set_continue_drop_item(True)
        monitor = self._monitor()
        monitor._process(RESPAWN)
        self.assertNotIn("drop_at_round_end", self._names(monitor))
        self.assertNotEqual(monitor.st.dropped_round_seq, monitor.st.round_seq)

        monitor = self._monitor()
        monitor._process(DIED)
        self.assertIn("drop_at_round_end", self._names(monitor), "死亡は今までどおり落とす")

    def test_run_does_not_write_the_item_loss_twice(self):
        monitor = self._monitor(round_type="Run")
        monitor._process(EQUIP)
        monitor.logs.clear()
        monitor._process(RESPAWN)
        lines = self._lines(monitor)
        self.assertEqual(lines.count("リスポーン: アイテムロスト"), 1)
        self.assertFalse(any("Run死亡" in m for m in lines), lines)
        self.assertEqual(monitor.st.held_item_id, 0)

        monitor = self._monitor(round_type="Run")
        monitor._process(EQUIP)
        monitor.logs.clear()
        monitor._process(DIED)
        self.assertEqual(self._lines(monitor).count("Run死亡: アイテムロスト"), 1, "死亡は今までどおり")


class TestTheAfkWatchStopsAtTheRespawn(_Case):
    def _watch(self, line=None, at=40.0, end_at=150.0):
        """見張りを偽の時計で回す。at 秒で line をログに流す。end_at 秒でラウンドが終わる"""
        monitor = self._monitor()
        ex = monitor._action
        st = monitor.st
        moves = []
        ex.move = lambda d, sec: moves.append((now[0] - start, d, sec))
        clock, now = _clock()
        start = now[0]
        ex._receiver = type("R", (), {"speed": property(lambda _s: 0.0),
                                      "ever_received": True, "last_received_at": 0.0})()
        steps = [(end_at, lambda: setattr(st, "in_round", False))]
        if line:
            steps.append((at, lambda: monitor._process(line)))
        steps.sort(key=lambda s: s[0])
        real_sleep = clock.sleep

        def sleep(sec):
            real_sleep(sec)
            while steps and now[0] - start >= steps[0][0]:
                steps.pop(0)[1]()
        clock.sleep = sleep
        sounds = []
        with patch.object(ActionExecutor, "time", clock), \
             patch.object(PlaySound, "play_sound", side_effect=lambda p, kind=None: sounds.append(kind)):
            support.REAL_AFK_WATCH(ex, st.round_seq)
        return moves, sounds

    def test_without_a_respawn_it_moves(self):
        moves, _sounds = self._watch()
        self.assertEqual([round(t) for t, *_ in moves], [70, 140])

    def test_a_respawn_ends_it_without_a_move_or_a_warning(self):
        moves, sounds = self._watch(RESPAWN)
        self.assertEqual((moves, sounds), ([], []))

    def test_a_death_ends_it_too(self):
        moves, sounds = self._watch(DIED)
        self.assertEqual((moves, sounds), ([], []))


class TestTheAfkLoopsStopAtTheRespawn(_Case):
    def _loop(self, line, glorbo, at):
        """AFK 解除ループを偽の時計で回す。at 回目の待ちのあとに line をログに流す。移動した回数と待った回数を返す"""
        monitor = self._monitor()
        st = monitor.st
        ex = monitor._action
        st.open_special_round_wins = 0
        st.is_open_special_round_round = not glorbo
        st.glorbo_afk = glorbo
        naps = []

        def nap(sec):
            naps.append(sec)
            if line and len(naps) == at:
                monitor._process(line)
            if len(naps) >= 1000:
                st.in_round = False       # 止まらなかったときの保険

        with patch.object(ActionExecutor.time, "sleep", side_effect=nap), \
             patch.object(ex, "move") as move:
            ex.do_open_special_round_loop(glorbo=glorbo)
        return move.call_count, len(naps)

    def test_the_dtm_waldo_loop_stops_at_a_respawn(self):
        moves, naps = self._loop(RESPAWN, glorbo=False, at=90)
        self.assertEqual((moves, naps), (1, 90), "60 秒で 1 回動いたあと、90 秒で止まる")

    def test_the_dtm_waldo_loop_stops_at_a_respawn_before_the_first_move(self):
        self.assertEqual(self._loop(RESPAWN, glorbo=False, at=10), (0, 10))

    def test_the_glorbo_loop_stops_at_a_respawn(self):
        self.assertEqual(self._loop(RESPAWN, glorbo=True, at=10), (0, 10))

    def test_the_glorbo_loop_stops_at_a_death_now(self):
        self.assertEqual(self._loop(DIED, glorbo=True, at=10), (0, 10))

    def test_the_loops_without_a_respawn_run_until_the_end(self):
        moves, naps = self._loop(None, glorbo=True, at=0)
        self.assertEqual(naps, 1000)
        self.assertGreater(moves, 0)


class TestTheSuicideStopsAtTheRespawn(_Case):
    def test_should_skip_is_false_after_a_respawn(self):
        monitor = self._monitor()
        ex = monitor._action
        self.assertTrue(ex._should_skip(4), "リスポーン前は自爆してよい")

        monitor._process(RESPAWN)

        self.assertFalse(ex._should_skip(4))

    def test_should_skip_is_still_true_after_a_respawn_in_the_lobby(self):
        monitor = self._monitor(in_round=False)
        monitor._process(RESPAWN)
        monitor.st.in_round = True
        self.assertTrue(monitor._action._should_skip(4))
