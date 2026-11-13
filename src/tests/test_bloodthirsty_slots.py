"""干し芋・焼き芋の Bloodbath の Bloodthirsty と、複数枠のラウンドの置き換え待ち（依頼者 2026-11-10）。

A. 干し芋・焼き芋の Bloodbath（EX を除く）: 1 枠目が Bloodthirsty のときだけ、その窓の続行リストの Bloodbath の枠で
   決める（あれば続行・無ければ自爆）。1 枠目が Bloodthirsty 以外なら 2・3 枠目にいても自爆。
B. 合図は 1 枠目が Killers の行とほぼ同時・2 枠目が約 5 秒後・3 枠目が約 10 秒後。Bloodbath・Midnight・Double Trouble で
   Curious がいる枠に応じて置き換え待ちの上限を 0.3 / 6 / 11 秒にする（Bloodthirsty が判定を変えるときだけ）。
"""
from tests.support import *  # noqa: F401,F403

BLOODBATH_KEY = "Bloodbath/ブラッドバス"
MIDNIGHT_KEY = "Midnight/ミッドナイト"
DT_KEY = "Double Trouble/ダブルトラブル"
CURIOUS = config.CURIOUS_CREATURE_ID
BLOODTHIRSTY = config.BLOODTHIRSTY_CREATURE_ID
SIGNAL_FLAG = "bloodthirsty_creature_variant"
OTHER = 3          # リストにも置き換えにも関係ないテラー
GROUPS = (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO)


class TestGroupRoundBloodthirstyFirstSlot(unittest.TestCase):
    """`GroupRound.decide` の単体"""

    def _decide(self, itype, round_type, ids, listed=False):
        return GroupRound.decide(itype, round_type, list(ids), bloodthirsty_listed=listed)

    def test_first_slot_bloodthirsty_follows_the_list(self):
        for itype in GROUPS:
            self.assertEqual(self._decide(itype, "Bloodbath", [BLOODTHIRSTY, 2, 3], True),
                             GroupRound.WANTED, itype)
            self.assertEqual(self._decide(itype, "Bloodbath", [BLOODTHIRSTY, 2, 3], False),
                             GroupRound.SKIP, itype)

    def test_bloodthirsty_in_a_later_slot_is_still_skipped(self):
        for itype in GROUPS:
            for ids in ([2, BLOODTHIRSTY, 3], [2, 3, BLOODTHIRSTY]):
                for listed in (True, False):
                    self.assertEqual(self._decide(itype, "Bloodbath", ids, listed),
                                     GroupRound.SKIP, (itype, ids, listed))

    def test_first_slot_other_terror_is_skipped(self):
        for itype in GROUPS:
            for listed in (True, False):
                self.assertEqual(self._decide(itype, "Bloodbath", [2, 3, 4], listed),
                                 GroupRound.SKIP, (itype, listed))

    def test_curious_is_skipped(self):
        """置き換え前の Curious は Bloodthirsty ではない"""
        for itype in GROUPS:
            self.assertEqual(self._decide(itype, "Bloodbath", [CURIOUS, 2, 3], True),
                             GroupRound.SKIP, itype)

    def test_private_stays_normal(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_ACHIEVEMENT):
            self.assertEqual(self._decide(itype, "Bloodbath", [BLOODTHIRSTY, 2, 3], True),
                             GroupRound.NORMAL, itype)

    def test_bloodbath_ex_is_unchanged(self):
        for itype in GROUPS:
            for listed in (True, False):
                self.assertEqual(self._decide(itype, "Bloodbath EX", [BLOODTHIRSTY, 2, 3], listed),
                                 GroupRound.CONTINUE, (itype, listed))

    def test_other_always_skip_rounds_are_unchanged(self):
        for itype in GROUPS:
            for round_type in ("Classic.exe", "Randomizer"):
                self.assertEqual(self._decide(itype, round_type, [BLOODTHIRSTY], True),
                                 GroupRound.SKIP, (itype, round_type))


class _Base(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        for p in (patch.object(ConnectDB, "register_round"),
                  patch.object(PlaySound, "play_sound")):
            self.addCleanup(p.stop)
            mock = p.start()
            if p.attribute == "play_sound":
                self.play = mock
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, keep_on=None, round_type="Bloodbath",
                 ids=(), do_skip=True):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.st.terror_ids = list(ids)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids, round_type=None):
        """_on_killers を回す。起動した判定のスレッド名と、「Variant判定待ち(N秒)」のログを返す"""
        round_type = round_type or monitor.st.round_type
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers(list(ids), round_type, revealed=False)
            started = _decision_threads(mock_thread)
        waits = [m for m in monitor.logs if "Variant判定待ち" in m]
        return started, waits

    def _run_delayed(self, monitor, round_type, wait_sec=0.0):
        """待ち明けの判定を直接動かして、起動した判定のスレッド名を返す"""
        monitor._running = True
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision(round_type, wait_sec, monitor.st.round_seq)
        return _decision_threads(mock_thread)


class TestGroupBloodbathBloodthirsty(_Base):
    """a〜d・i: 干し芋・焼き芋の Bloodbath を LogMonitor 越しに見る"""

    LISTED = {BLOODBATH_KEY: {BLOODTHIRSTY}}

    def _group(self, itype, keep_on=None):
        monitor = self._monitor(itype, keep_on=keep_on)
        monitor._running = True
        return monitor

    def test_a_listed_bloodthirsty_in_the_first_slot_continues_on_the_signal(self):
        for itype in GROUPS:
            SharedState.continue_round_reset()
            self.play.reset_mock()
            monitor = self._group(itype, self.LISTED)

            started, waits = self._killers(monitor, [CURIOUS, 2, OTHER])
            self.assertEqual(started, ["_delayed_decision"], itype)
            self.assertIn("0.3秒", waits[0], itype)
            self.assertFalse(monitor.st.is_continue_round, "合図の前は決めない")

            monitor._mark_replacement(SIGNAL_FLAG)           # 合図が来た
            self.assertEqual(monitor.st.terror_ids[0], BLOODTHIRSTY)
            started = self._run_delayed(monitor, "Bloodbath", 1.0)

            self.assertTrue(monitor.st.is_continue_round, itype)
            self.assertNotIn("do_skip", started, itype)
            self.play.assert_called_once_with("continue.mp3", kind="continue")
            self.assertEqual(SharedState.get_continue_round_count(), 1, "他窓フリーズも通常の続行と同じ")

    def test_b_bloodthirsty_not_in_the_list_skips_without_waiting(self):
        for itype in GROUPS:
            for keep_on in ({}, {BLOODBATH_KEY: {OTHER}}):
                monitor = self._group(itype, keep_on)

                started, waits = self._killers(monitor, [CURIOUS, 2, OTHER])

                self.assertEqual(started, ["do_skip"], (itype, keep_on))
                self.assertEqual(waits, [])
                self.assertFalse(monitor.st.is_continue_round)

    def test_c_only_a_second_slot_terror_listed_skips(self):
        """1 枠目が Bloodthirsty でも、リストにあるのが 2 枠目のテラーだけなら自爆（通常判定にしない）"""
        for itype in GROUPS:
            monitor = self._group(itype, {BLOODBATH_KEY: {2}})
            monitor.st.bloodthirsty_creature_variant = True

            started, waits = self._killers(monitor, [BLOODTHIRSTY, 2, OTHER])

            self.assertEqual(started, ["do_skip"], itype)
            self.assertFalse(monitor.st.is_continue_round, itype)

    def test_c_a_listed_bloodthirsty_in_the_first_slot_continues_at_once(self):
        """合図が先に来ていて 1 枠目がもう Bloodthirsty なら、待たずに続行"""
        for itype in GROUPS:
            monitor = self._group(itype, self.LISTED)
            monitor.st.bloodthirsty_creature_variant = True

            started, waits = self._killers(monitor, [BLOODTHIRSTY, 2, OTHER])

            self.assertEqual(started, [], itype)
            self.assertTrue(monitor.st.is_continue_round, itype)

    def test_d_no_signal_skips_when_the_wait_ends(self):
        for itype in GROUPS:
            monitor = self._group(itype, self.LISTED)
            self._killers(monitor, [CURIOUS, 2, OTHER])

            started = self._run_delayed(monitor, "Bloodbath", 0.0)

            self.assertEqual(started, ["do_skip"], itype)
            self.assertFalse(monitor.st.is_continue_round, itype)

    def test_i_second_and_third_slots_do_not_wait(self):
        for itype in GROUPS:
            for slot in (1, 2):
                ids = [2, 4, 5]
                ids[slot] = CURIOUS
                monitor = self._group(itype, self.LISTED)

                started, waits = self._killers(monitor, ids)

                self.assertEqual(started, ["do_skip"], (itype, slot))
                self.assertEqual(waits, [], (itype, slot))
                self.assertEqual(monitor._variant_wait_sec(), config.TERROR_VARIANT_WAIT_SEC)


class TestSlotWaitLength(_Base):
    """e・f・g・h: Curious がいる枠ごとの待ち時間"""

    PRIVATE = config.INSTANCE_PRIVATE

    def _wait(self, round_type, ids, keep_on, itype=None):
        monitor = self._monitor(itype or self.PRIVATE, keep_on=keep_on, round_type=round_type, ids=ids)
        return monitor._variant_wait_sec()

    def test_e_the_config_values(self):
        self.assertEqual(config.BLOODTHIRSTY_SLOT_WAIT_SEC, (config.TERROR_VARIANT_WAIT_SEC, 6.0, 11.0))
        self.assertEqual(config.TERROR_VARIANT_WAIT_SEC, 0.3)
        self.assertEqual(config.BLOODTHIRSTY_SLOT_ROUNDS, {"Bloodbath", "Midnight", "Double Trouble"})

    def test_e_bloodbath_waits_by_slot(self):
        keep_on = {BLOODBATH_KEY: {BLOODTHIRSTY}}
        for slot, expected in enumerate((0.3, 6.0, 11.0)):
            ids = [2, 4, 5]
            ids[slot] = CURIOUS
            self.assertEqual(self._wait("Bloodbath", ids, keep_on), expected, slot)

    def test_e_midnight_and_double_trouble_second_slot(self):
        self.assertEqual(self._wait("Midnight", [2, CURIOUS, 5], {MIDNIGHT_KEY: {BLOODTHIRSTY}}), 6.0)
        self.assertEqual(self._wait("Double Trouble", [2, CURIOUS], {DT_KEY: {BLOODTHIRSTY}}), 6.0)

    def test_e_midnight_and_double_trouble_first_slot_stays_short(self):
        self.assertEqual(self._wait("Midnight", [CURIOUS, 2, 5], {MIDNIGHT_KEY: {BLOODTHIRSTY}}), 0.3)
        self.assertEqual(self._wait("Double Trouble", [CURIOUS, 2], {DT_KEY: {BLOODTHIRSTY}}), 0.3)

    def test_e_on_killers_logs_the_slot_wait(self):
        monitor = self._monitor(self.PRIVATE, {BLOODBATH_KEY: {BLOODTHIRSTY}})
        monitor._running = True

        started, waits = self._killers(monitor, [2, CURIOUS, OTHER], "Bloodbath")

        self.assertEqual(started, ["_delayed_decision"])
        self.assertIn("6.0秒", waits[0])

    def test_f_no_wait_when_bloodthirsty_does_not_change_the_decision(self):
        """Curious も Bloodthirsty もリストに無い（今までどおりすぐ自爆）・他のテラーで続行が決まっている"""
        for keep_on in ({}, {BLOODBATH_KEY: {2}}, {BLOODBATH_KEY: {OTHER}}):
            monitor = self._monitor(self.PRIVATE, keep_on)
            monitor._running = True

            started, waits = self._killers(monitor, [2, CURIOUS, OTHER], "Bloodbath")

            self.assertEqual(monitor._variant_wait_sec(), 0.3, keep_on)
            self.assertNotIn("_delayed_decision", started, keep_on)
            self.assertEqual(waits, [], keep_on)

    def test_f_the_same_for_other_slot_rounds(self):
        for round_type, key, ids in (("Midnight", MIDNIGHT_KEY, [2, CURIOUS, 5]),
                                     ("Double Trouble", DT_KEY, [2, CURIOUS])):
            for keep_on in ({}, {key: {2}}):
                monitor = self._monitor(self.PRIVATE, keep_on, round_type)
                monitor._running = True

                started, waits = self._killers(monitor, ids, round_type)

                self.assertEqual(monitor._variant_wait_sec(), 0.3, (round_type, keep_on))
                self.assertNotIn("_delayed_decision", started, (round_type, keep_on))

    def test_f_a_signal_that_already_arrived_does_not_wait(self):
        monitor = self._monitor(self.PRIVATE, {BLOODBATH_KEY: {BLOODTHIRSTY}}, ids=[2, CURIOUS, OTHER])
        monitor.st.bloodthirsty_creature_variant = True

        self.assertEqual(monitor._variant_wait_sec(), 0.3)

    @patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True)
    def test_g_foxy_alone_waits_for_the_third_slot_signal(self):
        """Midnight の Curious が 2 枠目でも、判定を変えるのが Foxy（3 枠目の Sanic の置き換え）だけなら、
        Bloodthirsty の 6 秒ではなく Foxy の 11 秒"""
        monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {config.FOXY_ID}}, "Midnight")
        monitor._running = True

        started, waits = self._killers(monitor, [2, CURIOUS, config.SANIC_ID], "Midnight")

        self.assertTrue(monitor._replacement_changes_decision("Midnight"), "Foxy で結論が変わる前提")
        self.assertFalse(monitor._bloodthirsty_changes_decision("Midnight"))
        self.assertEqual(started, ["_delayed_decision"])
        self.assertIn("11.0秒", waits[0])
        self.assertEqual(monitor._variant_wait_sec("Midnight"), 11.0)

    @patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True)
    def test_g_both_changing_uses_the_longer_wait(self):
        monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {config.FOXY_ID, BLOODTHIRSTY}}, "Midnight",
                                ids=[2, CURIOUS, config.SANIC_ID])

        self.assertEqual(monitor._variant_wait_sec("Midnight"), 11.0)

    def test_g_bloodthirsty_alone_keeps_its_slot_wait_with_a_third_slot_terror(self):
        monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {BLOODTHIRSTY}}, "Midnight",
                                ids=[2, CURIOUS, config.SANIC_ID])

        self.assertEqual(monitor._variant_wait_sec("Midnight"), 6.0)

    @patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True)
    def test_m_midnight_third_slot_sanic_with_foxy_listed(self):
        self.assertEqual(self._wait("Midnight", [2, 4, config.SANIC_ID], {MIDNIGHT_KEY: {config.FOXY_ID}}), 11.0)

    @patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True)
    def test_m_midnight_third_slot_fusion_pilot_with_neo_pilot_listed(self):
        self.assertEqual(self._wait("Midnight", [2, 4, config.FUSION_PILOT_ID],
                                    {MIDNIGHT_KEY: {config.NEO_PILOT_ID}}), 11.0)

    @patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True)
    def test_m_sanic_listed_but_foxy_not_listed_changes_to_a_skip(self):
        """Sanic が続行で、Foxy に置き換わると自爆になるので待つ"""
        self.assertEqual(self._wait("Midnight", [2, 4, config.SANIC_ID], {MIDNIGHT_KEY: {config.SANIC_ID}}), 11.0)

    def test_m_the_alternate_wait_is_off_by_default(self):
        """依頼者 2026-11-13: Winterfest まで止める。既定では 3 枠目が Sanic / Fusion Pilot で結論が変わる構成でも 0.3 秒"""
        self.assertFalse(config.MIDNIGHT_ALTERNATE_WAIT_ENABLED)
        for tid, listed in ((config.SANIC_ID, config.FOXY_ID), (config.FUSION_PILOT_ID, config.NEO_PILOT_ID)):
            monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {listed}}, "Midnight", ids=[2, 4, tid])
            self.assertTrue(monitor._midnight_alternate_changes_decision("Midnight"), "結論が変わる前提")
            self.assertEqual(monitor._variant_wait_sec("Midnight"), config.TERROR_VARIANT_WAIT_SEC, tid)
        for itype in GROUPS:
            self.assertEqual(self._wait("Midnight", [2, 4, config.SANIC_ID], {MIDNIGHT_KEY: {config.FOXY_ID}}, itype),
                             config.TERROR_VARIANT_WAIT_SEC, itype)

    def test_m_the_alternate_wait_off_keeps_the_bloodthirsty_slot_wait(self):
        monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {config.FOXY_ID, BLOODTHIRSTY}}, "Midnight",
                                ids=[2, CURIOUS, config.SANIC_ID])

        self.assertEqual(monitor._variant_wait_sec("Midnight"), 6.0)

    def test_m_no_wait_when_the_decision_does_not_change(self):
        for keep_on in ({}, {MIDNIGHT_KEY: {2}}, {MIDNIGHT_KEY: {config.FOXY_ID, config.SANIC_ID}}):
            monitor = self._monitor(self.PRIVATE, keep_on, "Midnight")
            monitor._running = True

            started, waits = self._killers(monitor, [2, 4, config.SANIC_ID], "Midnight")

            self.assertEqual(monitor._variant_wait_sec("Midnight"), 0.3, keep_on)
            self.assertNotIn("_delayed_decision", started, keep_on)
            self.assertEqual(waits, [], keep_on)

    def test_m_alternate_and_fog_stay_short(self):
        for round_type, key in (("Alternate", "Alternate/オルタネイト"), ("Fog", "Fog/霧")):
            for tid, listed in ((config.SANIC_ID, config.FOXY_ID), (config.FUSION_PILOT_ID, config.NEO_PILOT_ID)):
                monitor = self._monitor(self.PRIVATE, {key: {listed}}, round_type, ids=[tid])
                self.assertTrue(monitor._replacement_changes_decision(round_type), "Foxy / Neo で結論が変わる前提")
                self.assertEqual(monitor._variant_wait_sec(round_type), 0.3, (round_type, tid))

    @patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True)
    def test_m_group_midnight_third_slot(self):
        for itype in GROUPS:
            self.assertEqual(self._wait("Midnight", [2, 4, config.SANIC_ID],
                                        {MIDNIGHT_KEY: {config.FOXY_ID}}, itype), 11.0, itype)
            self.assertEqual(self._wait("Midnight", [2, 4, config.SANIC_ID], {}, itype), 0.3, itype)

    def test_m_hands_free_still_does_not_wait(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {config.FOXY_ID}}, "Midnight")
        monitor._running = True

        started, waits = self._killers(monitor, [2, 4, config.SANIC_ID], "Midnight")

        self.assertNotIn("_delayed_decision", started)

    @patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True)
    def test_m_the_signal_ends_the_wait_with_a_decision(self):
        """合図（Foxy）が来たら残り時間を待たずに判定して続行する。実時間では待たない"""
        monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {config.FOXY_ID}}, "Midnight",
                                ids=[2, 4, config.SANIC_ID])
        monitor._running = True
        clock = [1000.0]

        def fake_sleep(sec):
            clock[0] += sec
            monitor._mark_replacement("foxy")

        fake_time = MagicMock()
        fake_time.time.side_effect = lambda: clock[0]
        fake_time.sleep.side_effect = fake_sleep
        with patch.object(LogMonitor, "time", fake_time),              patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision("Midnight", monitor._variant_wait_sec("Midnight"), monitor.st.round_seq)
            started = _decision_threads(mock_thread)

        self.assertEqual(fake_time.sleep.call_count, 1)
        self.assertEqual(monitor.st.terror_ids[2], config.FOXY_ID)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_m_without_the_signal_it_decides_with_the_original_terror(self):
        monitor = self._monitor(self.PRIVATE, {MIDNIGHT_KEY: {config.FOXY_ID}}, "Midnight",
                                ids=[2, 4, config.SANIC_ID])
        started = self._run_delayed(monitor, "Midnight", 0.0)

        self.assertEqual(started, ["do_skip"])

    def test_h_group_midnight_and_double_trouble_second_slot(self):
        for itype in GROUPS:
            for round_type, key, ids in (("Midnight", MIDNIGHT_KEY, [2, CURIOUS, 5]),
                                         ("Double Trouble", DT_KEY, [2, CURIOUS])):
                listed = self._wait(round_type, ids, {key: {BLOODTHIRSTY}}, itype)
                unlisted = self._wait(round_type, ids, {}, itype)
                self.assertEqual(listed, 6.0, (itype, round_type))
                self.assertEqual(unlisted, 0.3, (itype, round_type))

    def test_h_group_midnight_waits_in_on_killers(self):
        for itype in GROUPS:
            monitor = self._monitor(itype, {MIDNIGHT_KEY: {BLOODTHIRSTY}}, "Midnight")
            monitor._running = True

            started, waits = self._killers(monitor, [2, CURIOUS, OTHER], "Midnight")

            self.assertEqual(started, ["_delayed_decision"], itype)
            self.assertIn("6.0秒", waits[0], itype)

    def test_hands_free_still_does_not_wait(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(self.PRIVATE, {BLOODBATH_KEY: {BLOODTHIRSTY}})
        monitor._running = True

        started, waits = self._killers(monitor, [2, CURIOUS, OTHER], "Bloodbath")

        self.assertNotIn("_delayed_decision", started)
        self.assertEqual(waits, [])


class TestDelayedDecisionLoop(_Base):
    """j・k: 待っている間の振る舞い。実時間では待たない（time を差し替える）"""

    def _waiting_monitor(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE, {BLOODBATH_KEY: {BLOODTHIRSTY}},
                                "Bloodbath", ids=[2, CURIOUS, OTHER])
        monitor._running = True
        return monitor

    def _run(self, monitor, on_sleep, wait_sec=6.0):
        """time.time は sleep のたびに poll 秒進む。on_sleep(monitor) が sleep の中で呼ばれる"""
        clock = [1000.0]
        sleeps = []

        def fake_sleep(sec):
            sleeps.append(sec)
            clock[0] += sec
            on_sleep(monitor, len(sleeps))

        fake_time = MagicMock()
        fake_time.time.side_effect = lambda: clock[0]
        fake_time.sleep.side_effect = fake_sleep
        with patch.object(LogMonitor, "time", fake_time), \
             patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(monitor, "_decide") as mock_decide:
            monitor._delayed_decision("Bloodbath", wait_sec, monitor.st.round_seq)
            started = _decision_threads(mock_thread)
        return sleeps, started, mock_decide

    def test_j_dying_during_the_wait_decides_nothing(self):
        monitor = self._waiting_monitor()

        def die(m, n):
            m.st.died_this_round = True

        sleeps, started, decide = self._run(monitor, die)

        self.assertEqual(len(sleeps), 1, "死んだら次のループに入らない")
        decide.assert_not_called()
        self.assertNotIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(monitor.logs, [], "ログは足さない")

    def test_j_dying_as_the_deadline_passes_decides_nothing(self):
        """最後の sleep で死んだ（ループを抜けたあとの確認）"""
        monitor = self._waiting_monitor()

        def die_at_the_end(m, n):
            if n == 2:
                m.st.died_this_round = True

        sleeps, started, decide = self._run(monitor, die_at_the_end, wait_sec=2 * config.TERROR_VARIANT_POLL_SEC)

        self.assertEqual(len(sleeps), 2)
        decide.assert_not_called()
        self.assertNotIn("do_skip", started)

    def test_j_dying_before_it_starts_decides_nothing(self):
        monitor = self._waiting_monitor()
        monitor.st.died_this_round = True

        sleeps, started, decide = self._run(monitor, lambda m, n: None)

        self.assertEqual(sleeps, [])
        decide.assert_not_called()

    def test_j_the_real_decision_does_not_skip_a_dead_round(self):
        """_decide を差し替えない形でも、do_skip も続行も起きない"""
        monitor = self._waiting_monitor()
        monitor.st.died_this_round = True

        started = self._run_delayed(monitor, "Bloodbath", 0.0)

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.is_continue_round)

    def test_k_the_signal_ends_the_wait_early(self):
        monitor = self._waiting_monitor()

        def signal(m, n):
            m._mark_replacement(SIGNAL_FLAG)

        with patch.object(LogMonitor.threading, "Thread"):
            sleeps, started, decide = self._run(monitor, signal, wait_sec=11.0)

        self.assertEqual(len(sleeps), 1, "合図が来たら残り時間を待たない")
        decide.assert_called_once_with("Bloodbath")

    def test_k_the_signal_ends_the_wait_with_a_continue(self):
        monitor = self._waiting_monitor()
        clock = [1000.0]

        def fake_sleep(sec):
            clock[0] += sec
            monitor._mark_replacement(SIGNAL_FLAG)

        fake_time = MagicMock()
        fake_time.time.side_effect = lambda: clock[0]
        fake_time.sleep.side_effect = fake_sleep
        with patch.object(LogMonitor, "time", fake_time), \
             patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision("Bloodbath", 11.0, monitor.st.round_seq)
            started = _decision_threads(mock_thread)

        self.assertEqual(fake_time.sleep.call_count, 1)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_without_the_signal_it_waits_to_the_limit_then_decides(self):
        monitor = self._waiting_monitor()

        sleeps, started, decide = self._run(monitor, lambda m, n: None, wait_sec=1.0)

        self.assertAlmostEqual(sum(sleeps), 1.0, delta=config.TERROR_VARIANT_POLL_SEC)
        decide.assert_called_once_with("Bloodbath")


class TestWaitBloodthirstyCheck(_Base):
    """チェック「Bloodthirsty を待つ」（全窓共通・保存・既定 ON）。OFF なら 2 枠目・3 枠目の合図を待たない"""

    PRIVATE = config.INSTANCE_PRIVATE

    def setUp(self):
        super().setUp()
        self.addCleanup(SharedState.set_wait_bloodthirsty, True)

    def _wait(self, round_type, ids, keep_on):
        return self._monitor(self.PRIVATE, keep_on, round_type, ids=ids)._variant_wait_sec()

    def _waits_by_slot(self):
        keep_on = {BLOODBATH_KEY: {BLOODTHIRSTY}}
        waits = []
        for slot in range(3):
            ids = [2, 4, 5]
            ids[slot] = CURIOUS
            waits.append(self._wait("Bloodbath", ids, keep_on))
        return waits

    def test_on_by_default_keeps_the_slot_waits(self):
        self.assertTrue(SharedState.get_wait_bloodthirsty())
        self.assertEqual(self._waits_by_slot(), [0.3, 6.0, 11.0])
        self.assertEqual(self._wait("Midnight", [2, CURIOUS, 5], {MIDNIGHT_KEY: {BLOODTHIRSTY}}), 6.0)

    def test_off_does_not_wait_in_any_slot(self):
        SharedState.set_wait_bloodthirsty(False)
        self.assertEqual(self._waits_by_slot(), [config.TERROR_VARIANT_WAIT_SEC] * 3)
        self.assertEqual(self._wait("Midnight", [2, CURIOUS, 5], {MIDNIGHT_KEY: {BLOODTHIRSTY}}), 0.3)
        self.assertEqual(self._wait("Double Trouble", [2, CURIOUS], {DT_KEY: {BLOODTHIRSTY}}), 0.3)

    def test_off_decides_at_killers_without_the_long_wait(self):
        SharedState.set_wait_bloodthirsty(False)
        monitor = self._monitor(self.PRIVATE, {BLOODBATH_KEY: {BLOODTHIRSTY}})
        monitor._running = True

        started, waits = self._killers(monitor, [2, 4, CURIOUS], "Bloodbath")

        self.assertNotIn("6.0秒", "".join(waits))
        self.assertNotIn("11.0秒", "".join(waits))
        self.assertEqual(monitor._variant_wait_sec(), config.TERROR_VARIANT_WAIT_SEC)

    def test_off_does_not_touch_the_midnight_alternate_wait(self):
        """Foxy の 11 秒待ち（MIDNIGHT_ALTERNATE_WAIT_ENABLED）はこのチェックとは別"""
        SharedState.set_wait_bloodthirsty(False)
        with patch.object(config, "MIDNIGHT_ALTERNATE_WAIT_ENABLED", True):
            self.assertEqual(self._wait("Midnight", [2, 4, config.SANIC_ID], {MIDNIGHT_KEY: {config.FOXY_ID}}), 11.0)

    def test_a_change_of_the_check_changes_the_setting_and_saves(self):
        app = type("FakeApp", (), {})()
        app._schedule_settings_save = MagicMock()
        for value in (False, True):
            app.v_wait_bloodthirsty = MagicMock(get=lambda value=value: value)
            mainGUI.App._on_wait_bloodthirsty_changed(app)
            self.assertEqual(SharedState.get_wait_bloodthirsty(), value)
        self.assertEqual(app._schedule_settings_save.call_count, 2)

    def test_the_check_follows_the_setting(self):
        app = type("FakeApp", (), {})()
        app.v_wait_bloodthirsty = MagicMock()
        SharedState.set_wait_bloodthirsty(False)
        mainGUI.App._refresh_wait_bloodthirsty_check(app)
        app.v_wait_bloodthirsty.set.assert_called_once_with(False)

    def _load(self, data):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin", "v_join_world",
                     "v_ton_access", "v_instance_link", "v_emergency_key", "v_start_key",
                     "v_tnl", "v_obs_enabled", "v_obs_freeze", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, MagicMock())
        for name in ("_add_tool_row", "_refresh_emergency_key_label", "_refresh_start_key_label",
                     "_apply_obs_settings", "_apply_saved_window_settings",
                     "_load_own_list", "_log", "_load_window_volume_settings", "_load_fog_early_read_setting",
                     "_load_launch_options_setting"):
            setattr(app, name, lambda *a, **k: None)
        with patch.object(mainGUI, "load_settings", return_value=dict(data)),              patch.object(mainGUI, "save_settings"):
            mainGUI.App._load_saved_settings(app)

    def test_the_saved_value_is_loaded(self):
        for data, expected in (({"wait_bloodthirsty": False}, False),
                               ({"wait_bloodthirsty": True}, True),
                               ({}, True),
                               ({"wait_bloodthirsty": "x"}, True),
                               ({"wait_bloodthirsty": None}, True),
                               ({"wait_bloodthirsty": 0}, True)):
            SharedState.set_wait_bloodthirsty(not expected)
            self._load(data)
            self.assertEqual(SharedState.get_wait_bloodthirsty(), expected, data)

    def test_it_is_saved(self):
        SharedState.set_wait_bloodthirsty(False)
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin", "v_join_world",
                     "v_ton_access", "v_emergency_key",
                     "v_start_key", "v_obs_enabled", "v_obs_freeze", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, MagicMock(get=lambda: ""))
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update),              patch.object(mainGUI, "load_settings", return_value={}),              patch.object(SecretStore, "protect", return_value=""):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            app._window_freeze_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)
        self.assertIs(saved["wait_bloodthirsty"], False)

    def test_the_check_is_in_the_row_and_on_by_default(self):
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn("self.v_wait_bloodthirsty = tk.BooleanVar(value=True)", gui)
        self.assertIn('text="Bloodthirsty を待つ"', gui)
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_WAIT_BLOODTHIRSTY = _Setting(True, bool)", src)


if __name__ == "__main__":
    unittest.main()
