"""EZ（依頼者 2026-10-15）: 生き残るラウンドの AFK 回避・統計の 999 を「未分類」・突入フリーズに Sabotage など・
「全続行」の音量。本物の OSC・音・入力・窓は使わない（移動・音・前面化・時計は偽物）"""
from tests.support import *  # noqa: F401,F403
import tests.support as support
import StatisticsGUI


def _clock():
    now = [1000.0]
    fake = type(sys)("time_for_test")
    fake.time = fake.monotonic = lambda: now[0]
    fake.sleep = lambda sec: now.__setitem__(0, now[0] + sec)
    return fake, now


class _Afk(unittest.TestCase):
    def _watch(self, private=True, speed=None, end_at=150.0, setup=None, script=()):
        """見張りを偽の時計で回す。end_at 秒でラウンドが終わる。script: [(秒, st を変える関数)]"""
        st = WindowState(instance_type=config.INSTANCE_PRIVATE if private else config.INSTANCE_PUBLIC,
                         window_idx=3, round_seq=8, in_round=True)
        cfg = WindowConfig(hwnd=0x30, osc_port=9000, voice_afk="afk.mp3")
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        ex._osc = MagicMock()
        moves = []
        ex.move = lambda d, sec: moves.append((now[0] - start, d, sec))
        if speed is None:
            speed = lambda _t: 0.0                      # 0 を受けたまま（止まっている。FG: 受けていない窓は鳴らさない）
        if speed != "none":
            ex._receiver = type("R", (), {"speed": property(lambda _s: speed(now[0] - start)),
                                          "ever_received": True, "last_received_at": 0.0})()
        if setup:
            setup(st, ex)
        clock, now = _clock()
        start = now[0]
        steps = sorted(script) + [(end_at, lambda s: setattr(s, "in_round", False))]
        real_sleep = clock.sleep

        def sleep(sec):
            real_sleep(sec)
            while steps and now[0] - start >= steps[0][0]:
                steps.pop(0)[1](st)
        clock.sleep = sleep
        sounds = []
        with patch.object(ActionExecutor, "time", clock), \
             patch.object(PlaySound, "play_sound", side_effect=lambda p, kind=None: sounds.append((now[0] - start, p, kind))):
            support.REAL_AFK_WATCH(ex, 8)
        return moves, sounds, logs


class TestTheAfkWatch(_Afk):
    def test_private_moves_every_70_seconds_still(self):
        moves, sounds, logs = self._watch()
        self.assertEqual([round(t) for t, *_ in moves], [70, 140])
        self.assertEqual(moves[0][1:], ("forward", config.OPERATOR_WAIT_SEC), "DTM/Waldo と同じ動かし方")
        self.assertEqual(sounds, [])
        self.assertEqual(logs.count("AFK 回避: 70 秒動いていない → 微移動"), 2)

    def test_not_private_plays_the_warning(self):
        moves, sounds, logs = self._watch(private=False)
        self.assertEqual(moves, [])
        self.assertEqual([(round(t), p, k) for t, p, k in sounds], [(70, "afk.mp3", "afk"), (140, "afk.mp3", "afk")])
        self.assertIn("AFK 回避: 70 秒動いていない → 警告音", logs)

    def test_moving_starts_the_count_again(self):
        moves, _s, _l = self._watch(speed=lambda t: 3.0 if t < 30 else 0.0)
        self.assertEqual([round(t) for t, *_ in moves], [99], "最後に動いていた 29 秒（1 秒ごとに見る）から 70 秒")

    def test_a_window_that_never_got_a_speed_does_nothing(self):
        """FG（依頼者の判断 A）: 速さを一度も受けていない（受信器が無い・届かない）窓は鳴らさない・動かさない"""
        moves, sounds, logs = self._watch(speed="none")
        self.assertEqual((moves, sounds), ([], []))
        moves, sounds, logs = self._watch(private=False, speed=lambda _t: None)
        self.assertEqual((moves, sounds), ([], []))

    def test_69_seconds_does_nothing(self):
        moves, sounds, _l = self._watch(end_at=69.5)
        self.assertEqual((moves, sounds), ([], []))

    def test_dead_round_over_or_stopped_ends_it(self):
        moves, _s, _l = self._watch(script=[(40, lambda st: setattr(st, "died_this_round", True))], end_at=500)
        self.assertEqual(moves, [], "死んだ")
        moves, _s, _l = self._watch(end_at=50)
        self.assertEqual(moves, [], "ラウンドが終わった")
        moves, _s, _l = self._watch(script=[(40, lambda st: setattr(st, "round_seq", 9))], end_at=500)
        self.assertEqual(moves, [], "次のラウンド")

    def test_a_stop_ends_it(self):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=8, in_round=True)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30), st, lambda: False, lambda m: None)
        ex.move = MagicMock()
        clock, _now = _clock()
        with patch.object(ActionExecutor, "time", clock):
            support.REAL_AFK_WATCH(ex, 8)
        ex.move.assert_not_called()

    def test_the_dtm_waldo_or_glorbo_loop_is_left_alone(self):
        for flag in ("is_open_special_round_round", "glorbo_afk"):
            moves, sounds, _l = self._watch(setup=lambda st, ex, f=flag: setattr(st, f, True))
            self.assertEqual((moves, sounds), ([], []), flag)

    def test_a_round_the_tool_suicides_is_not_watched(self):
        moves, _s, _l = self._watch(setup=lambda st, ex: setattr(st, "suicide_seq", 8))
        self.assertEqual(moves, [], "自爆するラウンド")
        moves, _s, _l = self._watch(setup=lambda st, ex: (setattr(st, "suicide_seq", 8),
                                                           setattr(st, "suicide_cancelled_round", 8)))
        self.assertEqual(len(moves), 2, "自爆をキャンセルした（生き残る）")

    def test_the_values(self):
        self.assertEqual((config.AFK_STILL_SEC, config.AFK_POLL_SEC, config.AFK_MOVING_SPEED), (70.0, 1.0, 0.1))
        self.assertTrue(config.VOICE_AFK.endswith("AFK.mp3"))


class TestTheWatchStartsEveryRound(unittest.TestCase):
    def test_every_round_start_starts_it(self):
        cfg = WindowConfig(hwnd=0x10, do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        started = []
        watch = monitor._action.do_afk_watch
        with patch.object(monitor, "_start_daemon", side_effect=lambda t, *a: started.append((t, a))):
            for round_type in ("Classic", "Run", "Fog"):
                monitor._process(f"This round is taking place at Facility (12) and the round type is {round_type}")
        self.assertEqual([a for t, a in started if t == watch], [(1,), (2,), (3,)])


class TestTheUnknownRound(unittest.TestCase):
    def test_999_is_unclassified_in_the_lower_row(self):
        self.assertEqual(StatisticsGUI._round_display_name("999"), "未分類")
        self.assertIn("999", StatisticsGUI.DEFAULT_EXCLUDED_ROUNDS, "最初の選択で選ばない")
        self.assertIn("999", StatisticsGUI._round_names_in_group(StatisticsGUI.SELECT_ALL_SKIPPED_GROUP),
                      "全選択で選ばない")
        entries = StatisticsGUI._ordered_round_entries(["Fog", "999"])
        self.assertEqual(entries[-1], ("未分類", "999"), "下の段の最後")
        self.assertIn((None, None), entries[:entries.index(("未分類", "999"))])

    def test_the_db_value_is_unchanged(self):
        self.assertEqual(config.ROUND_TYPE_UNKNOWN_ID, 999)
        self.assertEqual(ConnectDB.round_type_id("ぜんぜん知らない名前"), 999, "DB へ送る値は 999 のまま")

    def test_unclassified_everywhere(self):
        """FB: 統計の画面で 999 と出るところは全部「未分類」（表・チップ・グラフ・出現ラウンド）"""
        self.assertEqual(StatisticsGUI._round_name(999), "未分類")
        self.assertEqual(StatisticsGUI._round_name(1), "Classic", "ほかは今どおり")
        rows = StatisticsGUI.round_kind_rows([(1, 3), (999, 1)])
        self.assertEqual([r[0] for r in rows], ["Classic", "未分類"], "ラウンドタイプのタブ")
        self.assertEqual(StatisticsGUI._round_display_name("未分類"), "未分類")
        self.assertIn("未分類", StatisticsGUI.DEFAULT_EXCLUDED_ROUNDS)
        self.assertIn("未分類", StatisticsGUI._round_names_in_group(StatisticsGUI.SELECT_ALL_SKIPPED_GROUP))
        entries = StatisticsGUI._ordered_round_entries([StatisticsGUI._round_name(999), "Fog"])
        self.assertEqual(entries[-1], ("未分類", "未分類"))
        out = StatisticsGUI.appearance_rows([(0, 999, 1, 5, None, None, "u", "", "")], 5, set(), {})
        self.assertEqual(out[0][1], "未分類", "出現ラウンドのタブ")


class _Freeze(unittest.TestCase):
    def setUp(self):
        SharedState.round_freeze_reset()
        self.addCleanup(SharedState.round_freeze_reset)
        self.addCleanup(SharedState.set_freeze_rounds, [])

    def _monitor(self, freeze):
        SharedState.set_freeze_rounds(list(freeze))
        cfg = WindowConfig(hwnd=0x10, do_skip=False, voice_murder="murder.mp3", voice_sabotage="sabotage.mp3",
                           voice_cracked="cracked.mp3", voice_double_trouble="dt.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")
        return monitor

    def _feed(self, monitor, lines):
        with patch.object(WindowOperator, "focus_window", return_value=True), \
             patch.object(PlaySound, "play_sound") as play, \
             patch.object(monitor, "_start_daemon"):
            for line in lines:
                monitor._process(line)
        return [(c.args[0], c.kwargs.get("kind")) for c in play.call_args_list]

    START = "This round is taking place at Cheese Maze (59) and the round type is {}"


class TestTheNewFreezeRounds(_Freeze):
    def test_sabotage_murder_and_star(self):
        """Sus player の行（ラウンド開始より前）で分ける"""
        monitor = self._monitor({"Sabotage murder"})
        played = self._feed(monitor, ["Sus player = 5 serim01", self.START.format("Sabotage")])
        self.assertTrue(monitor.st.round_freeze_held)
        self.assertIn(("murder.mp3", "murder"), played)

        monitor = self._monitor({"Sabotage murder"})
        played = self._feed(monitor, ["Sus player = 5 other", self.START.format("Sabotage")])
        self.assertFalse(monitor.st.round_freeze_held, "マーダーでない → star（選んでいない）")

        monitor = self._monitor({"Sabotage star"})
        played = self._feed(monitor, ["Sus player = 5 other", self.START.format("Sabotage")])
        self.assertTrue(monitor.st.round_freeze_held)
        self.assertIn(("sabotage.mp3", "sabotage"), played)

        monitor = self._monitor({"Sabotage star"})
        self._feed(monitor, ["Sus player = 5 serim01", self.START.format("Sabotage")])
        self.assertFalse(monitor.st.round_freeze_held, "マーダー → murder（選んでいない）")

    def test_murder_known_after_the_start(self):
        """開始の後に Sus player が来た（手元のログには無い）→ そのとき張る"""
        monitor = self._monitor({"Sabotage murder"})
        self._feed(monitor, [self.START.format("Sabotage")])
        self.assertFalse(monitor.st.round_freeze_held)
        played = self._feed(monitor, ["Sus player = 5 serim01"])
        self.assertTrue(monitor.st.round_freeze_held)
        self.assertIn(("murder.mp3", "murder"), played)

    def test_one_freeze_and_one_voice_per_round(self):
        """star で張った後にマーダーと分かっても、張り直さない・2 回目の音声は鳴らさない"""
        monitor = self._monitor({"Sabotage murder", "Sabotage star"})
        played = self._feed(monitor, [self.START.format("Sabotage"), "Sus player = 5 serim01"])
        self.assertTrue(monitor.st.round_freeze_held)
        self.assertEqual(played, [("sabotage.mp3", "sabotage")])
        self.assertEqual(SharedState.get_round_freeze_count(), 1)

    def test_cracked_and_double_trouble(self):
        for round_type, voice in (("Cracked", ("cracked.mp3", "cracked")),
                                  ("Double Trouble", ("dt.mp3", "double_trouble"))):
            monitor = self._monitor({round_type})
            played = self._feed(monitor, [self.START.format(round_type)])
            self.assertTrue(monitor.st.round_freeze_held, round_type)
            self.assertIn(voice, played, round_type)
            SharedState.round_freeze_reset()

    def test_bloodbath_ex_is_not_frozen(self):
        """FB: Bloodbath EX は外した。古い設定に残っていても張らない・鳴らさない・落ちない"""
        monitor = self._monitor({"Bloodbath EX"})
        played = self._feed(monitor, [self.START.format("Bloodbath"),
                                      "Killers have been set - 5 5 5 // Round type is Bloodbath"])
        self.assertFalse(monitor.st.round_freeze_held)
        self.assertEqual(played, [])
        self.assertNotIn("Bloodbath EX", LogMonitor.LogMonitor.ROUND_ENTRY_VOICE_KINDS)
        self.assertNotIn("bloodbath_ex", PlaySound.VOICE_KINDS)
        self.assertFalse(hasattr(WindowConfig(), "voice_bloodbath_ex"))
        self.assertFalse(hasattr(config, "VOICE_BLOODBATH_EX"))
        self.assertTrue((Path(config.resource_path("assets/voice/EX.mp3"))).is_file(), "ファイルは残す")

    def test_not_selected_or_hands_free(self):
        monitor = self._monitor(set())
        self._feed(monitor, [self.START.format("Cracked")])
        self.assertFalse(monitor.st.round_freeze_held)
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        monitor = self._monitor({"Cracked"})
        self._feed(monitor, [self.START.format("Cracked")])
        self.assertFalse(monitor.st.round_freeze_held, "放置中は張らない（今どおり）")

    def test_the_list(self):
        self.assertEqual(config.ROUND_FREEZE_SELECTABLE[-4:],
                         ["Sabotage murder", "Sabotage star", "Cracked", "Double Trouble"])
        self.assertNotIn("Bloodbath EX", config.ROUND_FREEZE_SELECTABLE, "FB")
        for name, path in (("Murder", config.VOICE_MURDER), ("Sabotage", config.VOICE_SABOTAGE),
                           ("Cracked", config.VOICE_CRACKED), ("DoubleTrouble", config.VOICE_DOUBLE_TROUBLE),
                           ("AFK", config.VOICE_AFK)):
            self.assertTrue(path.endswith(f"{name}.mp3"), path)
            self.assertTrue(Path(path).is_file(), path)


class TestTheAllContinueVolume(unittest.TestCase):
    def test_the_categories_and_the_order(self):
        cat = WindowVolume.category_of
        self.assertEqual(cat(WindowState(all_continue_round=True)), WindowVolume.ALL_CONTINUE)
        self.assertEqual(cat(WindowState(round_freeze_held=True)), WindowVolume.ALL_CONTINUE, "突入フリーズ")
        self.assertEqual(cat(WindowState(is_continue_round=True, all_continue_round=True)), WindowVolume.CONTINUE,
                         "続行 ＞ 全続行")
        self.assertEqual(cat(WindowState(all_continue_round=True, equip_freeze_held=True)),
                         WindowVolume.ALL_CONTINUE, "全続行 ＞ フリーズ窓")
        self.assertEqual(cat(WindowState(round_freeze_held=True, speed_freeze_held=True)),
                         WindowVolume.ALL_CONTINUE)
        self.assertEqual(cat(WindowState(equip_freeze_held=True)), WindowVolume.FREEZE)
        self.assertEqual(cat(WindowState(round_type="Run", round_freeze_held=True)), WindowVolume.OTHER,
                         "Run は今どおりその他")
        self.assertEqual(cat(WindowState(is_continue_round=True, open_special_continue=True)),
                         WindowVolume.OTHER, "DTM/Waldo の続行は今どおりその他")
        self.assertEqual(WindowVolume.CATEGORY_LABELS[WindowVolume.ALL_CONTINUE], "全続行")

    def test_the_controller_uses_the_level(self):
        st = WindowState(all_continue_round=True)
        audio = type("A", (), {})()
        ctl = WindowVolume.VolumeController(lambda: [(1, 0xA, st)], lambda m: None)
        ctl.set_levels(100, 90, 0, all_continue=40)
        self.assertEqual(ctl._levels[WindowVolume.ALL_CONTINUE], 40)
        ctl.set_levels(100, 90, 0)
        self.assertEqual(ctl._levels[WindowVolume.ALL_CONTINUE], 40, "渡さなければそのまま")

    def test_old_settings_give_the_default(self):
        _enabled, levels = WindowVolume.levels_from_settings({"window_volume_continue": 80})
        self.assertEqual(levels[WindowVolume.ALL_CONTINUE], 100)
        self.assertEqual(config.DEFAULT_WINDOW_VOLUME_ALL_CONTINUE, config.DEFAULT_WINDOW_VOLUME_CONTINUE)
        _enabled, levels = WindowVolume.levels_from_settings({"window_volume_all_continue": 35})
        self.assertEqual(levels[WindowVolume.ALL_CONTINUE], 35)

    def test_the_all_continue_round_flag(self):
        cfg = WindowConfig(hwnd=0x10, do_skip=True, continue_rounds={"Cracked"})
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        with patch.object(monitor, "_start_daemon"), patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Cracked")
            monitor._run_plan("Cracked", ("continue_rounds",))     # private のラウンド指定で続行
            self.assertTrue(monitor.st.all_continue_round)
            monitor._process("Verified Round End")
            self.assertFalse(monitor.st.all_continue_round)
            monitor._process("This round is taking place at Facility (12) and the round type is Cracked")
            monitor.st.all_continue_round = True
            monitor._process("This round is taking place at Facility (12) and the round type is Classic")
            self.assertFalse(monitor.st.all_continue_round, "ラウンド開始で落とす")


if __name__ == "__main__":
    unittest.main()
