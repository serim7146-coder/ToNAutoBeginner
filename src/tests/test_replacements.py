"""置き換えテラー（Variant・Gigabytes・Glorbo・Self Inserts など）"""
from tests.support import *  # noqa: F401,F403




class TestSelfInsertsBloodthirsty(unittest.TestCase):
    """Unbound の Self Inserts に Bloodthirsty が出たら最優先で続行する。

    ToN ListTool に「Bloodthirsty 入りの Self Inserts」を指定する手段が無いので、
    リストにも自爆指定にも頼れない。
    """

    UNBOUND_KEY = "Unbound/アンバウンド"
    SELF_INSERTS = config.SELF_INSERTS_ID          # 283
    PACK = 265                                     # Pack of Wild Yet Curious

    def _decide(self, round_type, terror_ids, keep_on=None,
                bloodthirsty=False, wins=99, cancel_afk=False):
        return RoundDecision.decide_killers(
            keep_on or {}, list(terror_ids), round_type, wins, cancel_afk,
            bloodthirsty_variant=bloodthirsty)

    # ── 強制続行 ────────────────────────────
    def test_self_inserts_with_bloodthirsty_continues(self):
        decided = self._decide("Unbound", [self.SELF_INSERTS], bloodthirsty=True)

        self.assertTrue(decided.is_continue_round)

    def test_without_bloodthirsty_it_follows_the_list(self):
        self.assertFalse(
            self._decide("Unbound", [self.SELF_INSERTS]).is_continue_round)
        self.assertTrue(
            self._decide("Unbound", [self.SELF_INSERTS],
                         {self.UNBOUND_KEY: {self.SELF_INSERTS}}).is_continue_round)

    def test_the_listed_case_is_unchanged(self):
        """リストに283があれば Bloodthirsty でなくても続行（従来どおり）"""
        decided = self._decide("Unbound", [self.SELF_INSERTS],
                               {self.UNBOUND_KEY: {self.SELF_INSERTS}})

        self.assertTrue(decided.is_continue_round)

    # ── 対象を広げない ───────────────────────
    def test_the_pack_is_not_covered(self):
        """Pack of Wild Yet Curious(265) はリストで指定できるので対象外"""
        self.assertFalse(
            self._decide("Unbound", [self.PACK], bloodthirsty=True).is_continue_round)
        self.assertTrue(
            self._decide("Unbound", [self.PACK], {self.UNBOUND_KEY: {self.PACK}},
                         bloodthirsty=True).is_continue_round)

    def test_other_rounds_are_not_covered(self):
        for round_type, ids in (("Classic", [config.BLOODTHIRSTY_CREATURE_ID]),
                                ("Fog", [config.BLOODTHIRSTY_CREATURE_ID]),
                                ("Midnight", [self.SELF_INSERTS])):
            self.assertFalse(
                self._decide(round_type, ids, bloodthirsty=True).is_continue_round,
                round_type)

    def test_another_unbound_group_is_not_covered(self):
        self.assertFalse(
            self._decide("Unbound", [200], bloodthirsty=True).is_continue_round)

    def test_omitting_the_flag_keeps_the_old_behaviour(self):
        decided = RoundDecision.decide_killers(
            {}, [self.SELF_INSERTS], "Unbound", 99, False)

        self.assertFalse(decided.is_continue_round)

    # ── 3クラ解放と混ざらない ──────────────────
    def test_it_does_not_set_the_open_special_flag(self):
        """混ぜると3クラ解放の AFK 解除が誤って走る"""
        decided = self._decide("Unbound", [self.SELF_INSERTS], bloodthirsty=True)

        self.assertTrue(decided.is_continue_round)
        self.assertFalse(decided.is_open_special_round_target)

    def test_the_helper_is_precise(self):
        ok = RoundDecision.is_self_inserts_bloodthirsty
        self.assertTrue(ok([self.SELF_INSERTS], "Unbound", True))
        self.assertFalse(ok([self.SELF_INSERTS], "Unbound", False))
        self.assertFalse(ok([self.PACK], "Unbound", True))
        self.assertFalse(ok([self.SELF_INSERTS], "Classic", True))
        self.assertFalse(ok([], "Unbound", True))




class TestSelfInsertsBloodthirstyWiring(unittest.TestCase):
    """LogMonitor 側。自爆指定より優先されること"""

    SELF_INSERTS = config.SELF_INSERTS_ID

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, skip_rounds=(),
                 bloodthirsty=True):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Unbound"
        monitor.st.terror_ids = [self.SELF_INSERTS]
        monitor.st.bloodthirsty_creature_variant = bloodthirsty
        return monitor

    def test_the_skip_list_does_not_win(self):
        """private で Unbound を自爆指定していても続行する"""
        monitor = self._monitor(skip_rounds=("Unbound",))

        self.assertFalse(monitor._should_skip_by_round())

    def test_the_skip_list_still_wins_without_bloodthirsty(self):
        monitor = self._monitor(skip_rounds=("Unbound",), bloodthirsty=False)

        self.assertTrue(monitor._should_skip_by_round())

    def test_it_continues_in_every_instance_type(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype)

            with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
                 patch.object(PlaySound, "play_sound"):
                monitor._on_killers([self.SELF_INSERTS], "Unbound", revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]

            self.assertTrue(monitor.st.is_continue_round, itype)
            self.assertNotIn("do_skip", started, itype)

    def test_the_flag_reaches_decide_killers(self):
        monitor = self._monitor()

        with patch.object(RoundDecision, "decide_killers",
                          return_value=RoundDecision.KillerDecision(False, True)
                          ) as mock_decide, \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._decide_with_keep_on_set("Unbound")

        self.assertIs(mock_decide.call_args.kwargs["bloodthirsty_variant"], True)

    def test_the_106_row_does_not_cover_unbound(self):
        """Unbound の terror_ids は [283]。待つのは 106 の行ではなく Self Inserts の行"""
        monitor = self._monitor(bloodthirsty=False)

        names = [r.name for r in monitor._pending_replacements()]
        self.assertNotIn("Wild Yet Bloodthirsty Creature", names)
        self.assertIn("Self Inserts (Bloodthirsty)", names)

        monitor.st.terror_ids = [config.CURIOUS_CREATURE_ID]
        self.assertIn("Wild Yet Bloodthirsty Creature",
                      [r.name for r in monitor._pending_replacements()],
                      "106 がいるラウンドでは従来どおり待つこと")




class TestTerrorReplacementTable(unittest.TestCase):
    """置き換えテラーの表。待ち方と差し替えはすべてここを引く"""

    def _row(self, name):
        rows = [r for r in TerrorReplacement.TABLE if r.name == name]
        self.assertEqual(len(rows), 1, name)
        return rows[0]

    def test_the_rows_from_the_requester(self):
        classic = frozenset({"Classic"})
        cases = {
            "Atrached": (config.SONIC_ID, config.ATRACHED_ID, classic),
            "Hungry Home Invader": (config.SLENDER_ID,
                                    config.HUNGRY_HOME_INVADER_ID, classic),
            "Wild Yet Bloodthirsty Creature": (config.CURIOUS_CREATURE_ID,
                                               config.BLOODTHIRSTY_CREATURE_ID,
                                               None),
            "The Gigabytes": (None, config.GIGABYTES_ID, classic),
            "Foxy": (config.SANIC_ID, config.FOXY_ID, None),
        }
        for name, (source, target, rounds) in cases.items():
            row = self._row(name)
            self.assertEqual((row.source, row.target_id(), row.rounds),
                             (source, target, rounds), name)
            self.assertTrue(row.enabled, name)

    def test_the_ids_match_terrors_json(self):
        """IDを書き間違えると、別のテラーで待つ・差し替える"""
        names = {
            config.SONIC_ID: "Sonic", config.ATRACHED_ID: "Atrached",
            config.SLENDER_ID: "Slenderwheels",
            config.HUNGRY_HOME_INVADER_ID: "Hungry Home Invader",
            config.CURIOUS_CREATURE_ID: "Wild Yet Curious Creature",
            config.BLOODTHIRSTY_CREATURE_ID: "Wild Yet Bloodthirsty Creature",
            config.GIGABYTES_ID: "The Gigabytes", config.SANIC_ID: "Sanic",
            config.FOXY_ID: "Foxy", config.FUSION_PILOT_ID: "Fusion Pilot",
            config.SELF_INSERTS_ID: "Self Inserts",
        }
        for tid, name in names.items():
            self.assertEqual(ReadJson.terror_name(tid, config.TERRORS), name, tid)

    def test_the_foxy_row_is_the_alternate_sanic(self):
        """いまのコードは霧の Foxy を 2+134=136 として判定していた（Sanic）"""
        self.assertTrue(ReadJson.is_alternate_terror(config.SANIC_ID, config.TERRORS))
        self.assertTrue(ReadJson.is_alternate_terror(config.FOXY_ID, config.TERRORS))

    def test_self_inserts_only_raises_a_flag(self):
        row = self._row("Self Inserts (Bloodthirsty)")

        self.assertFalse(row.changes_id)
        self.assertEqual(row.apply([config.SELF_INSERTS_ID]),
                         [config.SELF_INSERTS_ID])
        self.assertEqual(row.flag, "bloodthirsty_creature_variant")

    def test_gigabytes_replaces_the_whole_line_up(self):
        row = self._row("The Gigabytes")

        self.assertEqual(row.apply([99]), [config.GIGABYTES_ID])
        self.assertTrue(row.could_apply([99], "Classic"))
        self.assertFalse(row.could_apply([1, 2], "Classic"), "1体構成だけ")
        self.assertFalse(row.could_apply([99], "Bloodbath"))

    def test_every_flag_exists_on_the_state(self):
        st = WindowState()
        for flag in TerrorReplacement.flags():
            self.assertIs(getattr(st, flag), False, flag)

    def test_every_target_counts_as_a_variant_except_foxy(self):
        """Variant例外（自爆指定より優先）の対象は従来の4つのまま"""
        self.assertEqual(config.VARIANT_TERROR_IDS, frozenset({
            config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
            config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID}))




class TestNeoPilotSlot(unittest.TestCase):
    """Neo Pilot は枠だけ。IDと合図が分かるまで無効"""

    def _row(self):
        return [r for r in TerrorReplacement.TABLE if r.name == "Neo Pilot"][0]

    def _with_neo_pilot(self):
        data = {k: dict(v) if isinstance(v, dict) else v
                for k, v in config.TERRORS.items()}
        data["alternate"]["999"] = "Neo Pilot"
        return patch.object(config, "TERRORS", data)

    def test_it_is_disabled_now(self):
        row = self._row()

        self.assertEqual(row.source, config.FUSION_PILOT_ID)
        self.assertIsNone(row.target_id(), "terrors.json にまだ無い")
        self.assertFalse(row.enabled)

    def test_it_never_waits_while_disabled(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.round_type = "Alternate"
        monitor.st.terror_ids = [config.FUSION_PILOT_ID]

        self.assertEqual(monitor._pending_replacements(), [])

    def test_the_id_alone_does_not_enable_it(self):
        """合図のログが分からないまま待つと、毎回0.3秒むだに待つだけ"""
        with self._with_neo_pilot():
            row = self._row()
            self.assertEqual(row.target_id(), 999)
            self.assertFalse(row.enabled)

    def test_the_id_and_the_signal_enable_it(self):
        import dataclasses
        with self._with_neo_pilot():
            row = dataclasses.replace(self._row(), wired=True)

            self.assertTrue(row.enabled)
            self.assertEqual(row.apply([config.FUSION_PILOT_ID]), [999])

    def test_the_signal_alone_does_not_enable_it(self):
        import dataclasses
        row = dataclasses.replace(self._row(), wired=True)

        self.assertFalse(row.enabled, "IDが無い間は表から外れる")




class TestReplacementWait(unittest.TestCase):
    """判定のための待ちは「置き換えが起きると結論が変わるときだけ」"""

    DT_KEY = "Double Trouble/ダブルトラブル"
    CRACKED_KEY = "Cracked/狂気"
    CURIOUS = config.CURIOUS_CREATURE_ID
    BLOODTHIRSTY = config.BLOODTHIRSTY_CREATURE_ID

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self.sent = self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source(None)

    def _monitor(self, round_type, keep_on=None, *, skip_rounds=(),
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor._running = True
        return monitor

    def _killers(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _delayed(self, monitor, wait_sec=0.0):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_decision(monitor.st.round_type, wait_sec,
                                      monitor.st.round_seq)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 待つ・待たない ───────────────────────
    def test_it_waits_when_the_replacement_is_wanted(self):
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.BLOODTHIRSTY}})

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]),
                         ["_delayed_decision"])

    def test_it_does_not_wait_when_neither_is_wanted(self):
        monitor = self._monitor("Double Trouble")

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]), ["do_skip"])

    def test_it_waits_when_only_the_original_is_wanted(self):
        """続行→自爆に変わりうる。依頼者の言葉の裏返しも拾う"""
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.CURIOUS}})

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]),
                         ["_delayed_decision"])

    def test_it_does_not_wait_when_both_are_wanted(self):
        monitor = self._monitor("Double Trouble",
                                {self.DT_KEY: {self.CURIOUS, self.BLOODTHIRSTY}})

        started = self._killers(monitor, [self.CURIOUS, 5])

        self.assertNotIn("_delayed_decision", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_private_waits_without_a_designation(self):
        """依頼者の報告: Cracked の自爆指定は無かったのに、待たずに自爆した"""
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})

        started = self._killers(monitor, [self.CURIOUS])

        self.assertEqual(started, ["_delayed_decision"])
        self.assertNotIn("do_skip", started)

    def test_it_works_in_all_three_instance_types(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            monitor = self._monitor("Double Trouble",
                                    {self.DT_KEY: {self.BLOODTHIRSTY}},
                                    instance_type=itype)

            self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]),
                             ["_delayed_decision"], itype)

    def test_hands_free_skips_at_once(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        monitor.st.item_id = 0

        started = self._killers(monitor, [self.CURIOUS])

        self.assertEqual(started, ["do_skip"], "放置モードは待たない")

    def test_a_public_window_never_waits(self):
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.BLOODTHIRSTY}},
                                instance_type=config.INSTANCE_PUBLIC)

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]), [])

    # ── 待ち明け ────────────────────────────
    def test_the_signal_during_the_wait_continues(self):
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        self._killers(monitor, [self.CURIOUS])
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("The creature is bloodthirsty today...")

        started = self._delayed(monitor)

        self.assertEqual(monitor.st.terror_ids, [self.BLOODTHIRSTY])
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_no_signal_ends_in_the_original_judgement(self):
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        self._killers(monitor, [self.CURIOUS])

        self.assertIn("do_skip", self._delayed(monitor))

    def test_the_signal_ends_the_wait_early(self):
        """残り時間を待たずに判定する"""
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        self._killers(monitor, [self.CURIOUS])
        naps = []

        def nap(_sec):
            naps.append(_sec)
            monitor._process("The creature is bloodthirsty today...")

        with patch.object(LogMonitor.time, "sleep", side_effect=nap):
            started = self._delayed(monitor, wait_sec=60.0)

        self.assertEqual(len(naps), 1, "合図の直後に抜けること")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    # ── 統計の待ち ──────────────────────────
    def test_statistics_wait_even_when_the_decision_does_not(self):
        monitor = self._monitor("Double Trouble")

        started = self._killers(monitor, [self.CURIOUS, 5])

        self.assertEqual(started, ["do_skip"], "判定は待たない")
        self.sent.assert_not_called()

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("The creature is bloodthirsty today...")

        self.sent.assert_called_once()
        self.assertEqual(self.sent.call_args.args[1], [self.BLOODTHIRSTY, 5])

    def test_statistics_do_not_wait_for_self_inserts(self):
        """IDが変わらないので待つ意味が無い"""
        monitor = self._monitor("Unbound")

        self._killers(monitor, [config.SELF_INSERTS_ID])

        self.sent.assert_called_once()

    def test_statistics_do_not_wait_without_a_candidate(self):
        monitor = self._monitor("Double Trouble")

        self._killers(monitor, [1, 2])

        self.sent.assert_called_once()

    # ── 判定の比較は本番と同じ関数 ──────────────────
    def test_the_comparison_uses_the_real_decision(self):
        """待つかの判断が本番の判定とずれないこと"""
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.BLOODTHIRSTY}})
        monitor.st.terror_ids = [self.CURIOUS, 5]
        calls = []
        real = monitor._plan

        def spy(*args):
            calls.append(args)
            return real(*args)

        with patch.object(monitor, "_plan", side_effect=spy):
            self.assertTrue(monitor._replacement_changes_decision("Double Trouble"))
            with patch.object(LogMonitor.threading, "Thread"), \
                 patch.object(PlaySound, "play_sound"):
                monitor._decide("Double Trouble")

        self.assertIn(("Double Trouble", [self.CURIOUS, 5], False), calls)
        self.assertIn(("Double Trouble", [self.BLOODTHIRSTY, 5], True), calls)

    def test_every_plan_kind_has_an_outcome(self):
        for plan in (("group", GroupRound.SKIP), ("group", GroupRound.CONTINUE),
                     ("group", GroupRound.WANTED), ("restricted",),
                     ("hands_free", "x"), ("continue_rounds",), ("round_skip",),
                     ("list", True, False), ("list", True, True),
                     ("list", False, False)):
            self.assertIn(LogMonitor.LogMonitor._outcome(plan),
                          {"skip", "quiet", "continue", "open_special"}, plan)




class TestSelfInsertsBloodthirstyWait(unittest.TestCase):
    """Self Inserts は Bloodthirsty 行を待ってから判定する。

    実測: Variant の出現ログは Killers 行の**後**に出る（手元ログの
    Gigabytes 12件・Atrached 3件がすべて後）。待たないと、Self Inserts の
    強制続行がまさにその場面で発火しない。
    """

    SELF_INSERTS = config.SELF_INSERTS_ID
    UNBOUND_KEY = "Unbound/アンバウンド"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, skip_rounds=(),
                 keep_on=None, terror_ids=(config.SELF_INSERTS_ID,)):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Unbound"
        monitor.st.terror_ids = list(terror_ids)
        monitor._running = True
        return monitor

    def _started(self, monitor):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(monitor.st.terror_ids), "Unbound",
                                revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    @staticmethod
    def _pending(monitor):
        return any(r.name == "Self Inserts (Bloodthirsty)"
                   for r in monitor._pending_replacements())

    def _run_wait(self, monitor):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_decision("Unbound", 0.0, monitor.st.round_seq)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 待つかどうか ─────────────────────────
    def test_it_waits_for_self_inserts(self):
        monitor = self._monitor()

        self.assertTrue(self._pending(monitor))

    def test_it_stops_waiting_once_the_line_arrives(self):
        monitor = self._monitor()
        monitor.st.bloodthirsty_creature_variant = True

        self.assertFalse(self._pending(monitor))

    def test_it_does_not_wait_for_other_groups(self):
        for ids in ([265], [200], [config.CURIOUS_CREATURE_ID]):
            monitor = self._monitor(terror_ids=ids)

            self.assertFalse(self._pending(monitor), ids)

    def test_it_does_not_wait_in_other_rounds(self):
        for round_type in ("Classic", "Fog", "Midnight"):
            monitor = self._monitor()
            monitor.st.round_type = round_type

            self.assertFalse(self._pending(monitor),
                             round_type)

    # ── 待ちに入ること ───────────────────────
    def test_the_round_enters_the_wait(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            monitor = self._monitor(itype)

            self.assertEqual(self._started(monitor),
                             ["_delayed_decision"], itype)

    def test_a_round_that_already_saw_the_line_does_not_wait(self):
        monitor = self._monitor(keep_on={self.UNBOUND_KEY: {self.SELF_INSERTS}})
        monitor.st.bloodthirsty_creature_variant = True

        started = self._started(monitor)

        self.assertNotIn("_delayed_decision", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_other_rounds_gain_no_latency(self):
        monitor = self._monitor(terror_ids=[265])

        started = self._started(monitor)

        self.assertNotIn("_delayed_decision", started)

    # ── 待ち明けの判断 ───────────────────────
    def test_the_line_arriving_during_the_wait_continues(self):
        monitor = self._monitor()
        monitor.st.bloodthirsty_creature_variant = True   # 待っている間に来た

        started = self._run_wait(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_without_the_line_it_follows_the_list(self):
        monitor = self._monitor()

        started = self._run_wait(monitor)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertIn("do_skip", started, "private なのでリストに無ければ自爆")

    def test_the_skip_list_is_still_honoured_after_the_wait(self):
        """Bloodthirsty が来なければ、自爆指定はそのまま効く"""
        monitor = self._monitor(skip_rounds=("Unbound",))

        started = self._run_wait(monitor)

        self.assertIn("do_skip", started)

    def test_the_line_beats_the_skip_list_after_the_wait(self):
        monitor = self._monitor(skip_rounds=("Unbound",))
        monitor.st.bloodthirsty_creature_variant = True

        started = self._run_wait(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_the_skip_list_does_not_decide_before_the_wait(self):
        """待たずに自爆指定を見ると、フラグが立つ前に自爆が決まってしまう"""
        monitor = self._monitor(skip_rounds=("Unbound",))

        started = self._started(monitor)

        self.assertEqual(started, ["_delayed_decision"])

    def test_the_wait_aborts_when_the_round_changed(self):
        monitor = self._monitor()
        monitor.st.round_seq = 5

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision("Unbound", 0.0, 4)

        mock_thread.assert_not_called()

    def test_the_wait_uses_the_shared_length(self):
        monitor = self._monitor()

        self.assertEqual(monitor._variant_wait_sec(),
                         config.TERROR_VARIANT_WAIT_SEC)




class TestReplacementSignalsAreOneTable(unittest.TestCase):
    """置き換えテラーの合図は TerrorReplacement.SIGNALS の1か所。行 → flag → 差し替え"""

    def test_every_sample_line_parses_to_its_flag(self):
        for row in TerrorReplacement.SIGNALS:
            event = LogParser.parse("2026.10.01 12:00:00 Log        -  " + row.sample)
            self.assertIsNotNone(event, row.flag)
            self.assertEqual((event.kind, event.flag),
                             (LogParser.EVENT_REPLACEMENT, row.flag))

    def test_every_flag_is_a_window_state_field_cleared_each_round(self):
        st = WindowState()
        for row in TerrorReplacement.SIGNALS:
            self.assertIs(getattr(st, row.flag), False, row.flag)
            self.assertIn(row.flag, TerrorReplacement.flags(), "ラウンド開始で落とす")

    def test_every_flag_replaces_something(self):
        table_flags = {r.flag for r in TerrorReplacement.TABLE}
        for row in TerrorReplacement.SIGNALS:
            self.assertIn(row.flag, table_flags, row.flag)

    def test_each_signal_is_announced_the_same_way(self):
        """Atrached・Hungry Home Invader なども同じ流れ（知らせる → 差し替える）"""
        for row in TerrorReplacement.SIGNALS:
            logs = []
            monitor = LogMonitor.LogMonitor(WindowConfig(), {}, logs.append, window_idx=1)
            with patch.object(PlaySound, "play_sound"):
                monitor._process("2026.10.01 12:00:00 Log        -  " + row.sample)
            self.assertTrue(any(row.announce in m for m in logs), (row.flag, logs))

    def test_every_handler_name_exists(self):
        for kind, name in LogMonitor.LogMonitor._HANDLERS.items():
            self.assertTrue(callable(getattr(LogMonitor.LogMonitor, name, None)), (kind, name))




class TestGlorboContinue(unittest.TestCase):
    """Glorbo は続行リストにあれば、放置モード・ラウンド指定の自爆より優先して続行する。
    フリーズ・アナウンス・録画は普通の続行と同じ。AFK 対策は 3 勝でも回す"""

    PUNISHED_KEY = "Punished/パニッシュ"
    GLORBO_LINE = "2026.09.27 21:05:11 Debug      -  the real g has appeared"

    def setUp(self):
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.continue_round_reset)
        self._start(patch.object(ConnectDB, "register_round"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self.freeze = self._start(patch.object(SharedState, "continue_round_start"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, glorbo_on_list=True, instance_type=config.INSTANCE_PRIVATE, skip_rounds=()):
        keep = {self.PUNISHED_KEY: {config.GLORBO_ID}} if glorbo_on_list else {}
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Punished"
        monitor.st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS   # Punished は特殊
        monitor.st.item_id = 5
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._focus_for_freeze = MagicMock()
        return monitor

    def _started(self):
        return [(c.kwargs["target"].__func__.__name__, c.kwargs.get("args", ()))
                for c in self.thread.call_args_list if "target" in c.kwargs]

    def _glorbo_round(self, monitor):
        """Killers に Arkus → 合図を待つ → the real g has appeared → 判定"""
        monitor._on_killers([config.ARKUS_ID], "Punished", revealed=False)
        waited = [n for n, _a in self._started() if n == "_delayed_decision"]
        monitor._process(self.GLORBO_LINE)
        monitor._decide("Punished")                 # 待ちが終わったときの判定
        return waited

    def _assert_glorbo_continue(self, monitor, hands_free=False):
        st = monitor.st
        self.assertTrue(st.is_continue_round)
        self.assertFalse(st.open_special_continue, "音量は普通の続行")
        started = self._started()
        self.assertNotIn("do_skip", [n for n, _a in started])
        self.assertIn(("do_open_special_round_loop", (True,)), started)
        self.freeze.assert_called_once_with(st)
        monitor._focus_for_freeze.assert_called_once_with("続行ラウンド")
        if hands_free:
            self.play.assert_not_called()
            self.record.assert_not_called()
        else:
            self.play.assert_called_once_with("continue.mp3")
            self.record.assert_called_once_with(1)
        for line in ("判定: Glorbo / Punished 【プレイ(Glorbo)】", "⏸ 続行ラウンド中 → 他窓フリーズ開始",
                     "AFK解除ループ開始（Glorbo）"):
            self.assertTrue(any(line in m for m in monitor.logs), (line, monitor.logs))

    def test_glorbo_on_the_list_continues_and_freezes(self):
        monitor = self._monitor()
        waited = self._glorbo_round(monitor)
        self.assertTrue(waited, "Glorbo の行を待つ（自爆しない）")
        self.assertEqual(monitor.st.terror_ids, [config.GLORBO_ID])
        self._assert_glorbo_continue(monitor)

    def test_hands_free_still_continues_and_waits_for_the_line(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor()
        waited = self._glorbo_round(monitor)
        self.assertTrue(waited, "放置モードでも Glorbo の行を待つ")
        self._assert_glorbo_continue(monitor, hands_free=True)

    def test_a_round_skip_on_punished_still_continues(self):
        monitor = self._monitor(skip_rounds={"Punished"})
        self._glorbo_round(monitor)
        self._assert_glorbo_continue(monitor)

    def test_off_the_list_is_as_before(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(glorbo_on_list=False)
        monitor._on_killers([config.ARKUS_ID], "Punished", revealed=False)
        names = [n for n, _a in self._started()]
        self.assertIn("do_skip", names, "放置モードは待たずに即自爆")
        self.assertNotIn("_delayed_decision", names)
        SharedState.set_hands_free(False)
        for skip in ((), {"Punished"}):
            self.thread.reset_mock()
            monitor = self._monitor(glorbo_on_list=False, skip_rounds=skip)
            monitor.st.terror_ids = [config.GLORBO_ID]
            monitor.st.glorbo = True
            plan = monitor._plan("Punished", monitor.st.terror_ids, False)
            self.assertEqual(plan[0], "round_skip" if skip else "list", skip)
            monitor._decide("Punished")
            self.assertIn("do_skip", [n for n, _a in self._started()])
            self.assertFalse(monitor.st.is_continue_round)

    def test_public_does_not_operate(self):
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC)
        monitor.st.terror_ids = [config.GLORBO_ID]
        monitor.st.glorbo = True
        self.assertEqual(monitor._plan("Punished", monitor.st.terror_ids, False), ("restricted",))
        monitor._decide("Punished")
        self.freeze.assert_not_called()
        self.assertNotIn("do_open_special_round_loop", [n for n, _a in self._started()])

    def test_the_outcome_is_continue(self):
        self.assertEqual(LogMonitor.LogMonitor._outcome(("glorbo",)), "continue")

    def test_the_loop_starts_once_and_the_mark_resets_at_round_start(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [config.GLORBO_ID]
        monitor.st.glorbo = True
        monitor._decide("Punished")
        monitor._decide("Punished")
        loops = [a for n, a in self._started() if n == "do_open_special_round_loop"]
        self.assertEqual(loops, [(True,)], "1回だけ")
        monitor._process("2026.09.27 21:10:00 Debug      -  This round is taking place at Sewers (3) "
                         "and the round type is Punished")
        self.assertFalse(monitor.st.glorbo_afk)




class TestGlorboAfkLoop(unittest.TestCase):
    """Glorbo の AFK 対策は 3 勝・cancel_afk に関係なく 60 秒ごとに動き、ラウンド終了で止まる"""

    def _run(self, glorbo, stop_after):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, in_round=True,
                         open_special_round_wins=config.OPEN_SPECIAL_ROUND_TARGET_WINS,
                         is_open_special_round_round=False, glorbo_afk=True)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1, osc_port=9000, cancel_afk=False), st,
                                           lambda: True, lambda _m: None)
        naps = []

        def nap(sec):
            naps.append(sec)
            if len(naps) >= stop_after:
                st.in_round = False

        with patch.object(ActionExecutor.time, "sleep", side_effect=nap), \
             patch.object(ex, "move") as move:
            ex.do_open_special_round_loop(glorbo=glorbo)
        return move, naps

    def test_it_moves_every_60_seconds_even_with_three_wins(self):
        move, naps = self._run(True, 2 * config.OPEN_SPECIAL_ROUND_INTERVAL_SEC + 5)
        self.assertEqual(move.call_count, 2)
        move.assert_called_with("forward", config.OPERATOR_WAIT_SEC)
        self.assertEqual(len(naps), 2 * config.OPEN_SPECIAL_ROUND_INTERVAL_SEC + 5, "ラウンド終了で止まる")

    def test_dtm_waldo_loop_still_stops_at_three_wins(self):
        move, naps = self._run(False, 1000)
        move.assert_not_called()
        self.assertEqual(naps, [], "今どおり 3 勝で止まる")




class TestGigabytesDetect(unittest.TestCase):
    """The Gigabytes はテラーIDで判別できないのでログ行で拾う"""

    LINE = "2026.09.05 14:29:35 Debug      -  The Gigabytes have come."

    def test_line_is_parsed(self):
        event = LogParser.parse(self.LINE)

        self.assertIsNotNone(event)
        self.assertEqual((event.kind, event.flag), (LogParser.EVENT_REPLACEMENT, "gigabytes"))

    def test_similar_lines_do_not_match(self):
        """同じラウンドに出る紛らわしい行を拾わないこと"""
        for line in ("2026.09.05 14:29:35 Debug      -  The Gigabytes have come",
                     "2026.09.05 14:29:35 Debug      -  The Gigabytes have come. now"):
            self.assertIsNone(LogParser.parse(line), line)

        # Enrage 行は Enrage として拾う。Gigabytes の出現行ではない
        enrage = LogParser.parse("2026.09.05 14:29:35 Debug      -  "
                                 "BLUE GIGABYTEtriggered an Enrage State!")
        self.assertEqual(enrage.kind, LogParser.EVENT_ENRAGE)
        self.assertEqual(enrage.player_name, "BLUE GIGABYTE")

    def test_handler_logs_once(self):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process(self.LINE)

        hits = [m for m in logs if "The Gigabytes 出現" in m]
        self.assertEqual(len(hits), 1, logs)
        mock_thread.assert_not_called()
        mock_play.assert_not_called()

    def test_only_the_gigabytes_flag_is_set(self):
        """立てるのは gigabytes だけ。他の状態は動かさない"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.round_type = "Classic"     # Gigabytes は Classic でしか起きない
        before = dict(vars(monitor.st))

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        after = dict(vars(monitor.st))
        self.assertTrue(after.pop("gigabytes"))
        before.pop("gigabytes")
        for state in (after, before):
            state.pop("log_now")        # 行の時刻は毎行覚える（判定用）
        self.assertEqual(after, before, "gigabytes 以外は変えないこと")

    def test_terror_ids_are_replaced_wholesale(self):
        """元IDが毎回違うので「置換」ではなく差し替える"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [91]

        with patch.object(LogMonitor.threading, "Thread"),              patch.object(ConnectDB, "register_round"):
            monitor._process(self.LINE)

        self.assertEqual(monitor.st.terror_ids, [config.GIGABYTES_ID])

    def test_line_before_killers_leaves_ids_to_on_killers(self):
        """Killers 行より先に来ることがある。空のまま差し替えて統計を送らない"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread"),              patch.object(ConnectDB, "register_round") as mock_send:
            monitor._process(self.LINE)

        self.assertEqual(monitor.st.terror_ids, [])
        mock_send.assert_not_called()

        with patch.object(LogMonitor.threading, "Thread"),              patch.object(ConnectDB, "register_round"):
            monitor._on_killers([91], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.GIGABYTES_ID])




class TestAtrachedDetect(unittest.TestCase):
    """Atrached は Sonic のVariant。IDでは判別できないのでログ行で拾う"""

    LINE = "2026.09.12 20:23:15 Debug      -  Lets play a game..."

    def test_line_is_parsed(self):
        event = LogParser.parse(self.LINE)

        self.assertIsNotNone(event)
        self.assertEqual((event.kind, event.flag), (LogParser.EVENT_REPLACEMENT, "atrached_variant"))

    def test_similar_lines_do_not_match(self):
        """アポストロフィ有り・ピリオドの数違いは拾わない"""
        prefix = "2026.09.12 20:23:15 Debug      -  "
        for body in ("Let's play a game...",
                     "Lets play a game.",
                     "Lets play a game",
                     "Lets play a game....",
                     "Lets play a game... now"):
            self.assertIsNone(LogParser.parse(prefix + body), body)

    def test_handler_logs_once(self):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process(self.LINE)

        hits = [m for m in logs if "Atrached 出現" in m]
        self.assertEqual(len(hits), 1, logs)
        mock_thread.assert_not_called()
        mock_play.assert_not_called()

    def test_logged_even_when_auto_skip_is_off(self):
        """自爆オフでも出す（早期returnより前に置いてあること）"""
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=False), {},
                                        logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PUBLIC

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        self.assertTrue(any("Atrached 出現" in m for m in logs), logs)

    def test_sonic_is_replaced_with_atrached(self):
        """HHI(47->190)と同じ形。Sonic(40) を 191 に置換する"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.SONIC_ID]

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process(self.LINE)

        self.assertTrue(monitor.st.atrached_variant)
        self.assertEqual(monitor.st.terror_ids, [config.ATRACHED_ID])

    def test_line_before_killers_still_marks_the_variant(self):
        """Killers 行より先に来ても、後から来たIDに適用されること"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "register_round") as mock_send:
            monitor._process(self.LINE)
        mock_send.assert_not_called()

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "register_round"):
            monitor._on_killers([config.SONIC_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.ATRACHED_ID])

    def test_other_round_types_are_ignored(self):
        """Classic 以外では置換しない（HHIと同じ扱い）"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Midnight"
        monitor.st.terror_ids = [config.SONIC_ID]

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        self.assertFalse(monitor.st.atrached_variant)
        self.assertEqual(monitor.st.terror_ids, [config.SONIC_ID])




class TestGlorboDetect(unittest.TestCase):
    """Punished の Sewers で、Arkus が低確率で Glorbo に置き換わる。

    IDでは判別できないので合図のログで拾う（Atrached / Gigabytes と同じ形）。
    この行はまだ実ログで観測できていないので、正規表現を緩く受けている。
    """

    PREFIX = "2026.09.27 21:05:11 Debug      -  "
    LINE = PREFIX + "the real g has appeared"
    PUNISHED_KEY = "Punished/パニッシュ"

    def setUp(self):
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_hands_free, False)
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)

    def _monitor(self, keep_on=None, round_type="Punished"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor._running = True
        return monitor

    # ── ログ行 ────────────────────────────────
    def test_the_line_is_parsed(self):
        event = LogParser.parse(self.LINE)

        self.assertIsNotNone(event)
        self.assertEqual((event.kind, event.flag), (LogParser.EVENT_REPLACEMENT, "glorbo"))

    def test_the_case_and_the_full_stop_do_not_matter(self):
        """実物の行が取れていないので、大文字小文字と句点は問わない"""
        for body in ("the real g has appeared",
                     "the real g has appeared.",
                     "The Real G Has Appeared",
                     "THE REAL G HAS APPEARED.",
                     "The real G has appeared."):
            event = LogParser.parse(self.PREFIX + body)
            self.assertIsNotNone(event, body)
            self.assertEqual((event.kind, event.flag), (LogParser.EVENT_REPLACEMENT, "glorbo"), body)

    def test_similar_lines_do_not_match(self):
        for body in ("the real g has appeared now",
                     "so the real g has appeared",
                     "the real g has appeared..",
                     "the real g appeared"):
            self.assertIsNone(LogParser.parse(self.PREFIX + body), body)

    # ── 差し替え ───────────────────────────────
    def test_arkus_is_replaced_with_glorbo(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [config.ARKUS_ID]

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        self.assertTrue(monitor.st.glorbo)
        self.assertEqual(monitor.st.terror_ids, [config.GLORBO_ID])

    def test_without_the_line_it_stays_arkus(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [config.ARKUS_ID]

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.PREFIX + "Nothing to see here")

        self.assertFalse(monitor.st.glorbo)
        self.assertEqual(monitor.st.terror_ids, [config.ARKUS_ID])

    def test_other_round_types_are_ignored(self):
        """Punished 以外では、同じ合図が来ても差し替えない"""
        for round_type in ("Classic", "Midnight", "Alternate", "Sabotage"):
            monitor = self._monitor(round_type=round_type)
            monitor.st.terror_ids = [config.ARKUS_ID]

            with patch.object(LogMonitor.threading, "Thread"):
                monitor._process(self.LINE)

            self.assertFalse(monitor.st.glorbo, round_type)
            self.assertEqual(monitor.st.terror_ids, [config.ARKUS_ID], round_type)

    def test_a_line_up_without_arkus_is_left_alone(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [42, 43]

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        self.assertEqual(monitor.st.terror_ids, [42, 43])

    def test_only_arkus_is_replaced_in_a_group(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [42, config.ARKUS_ID]

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        self.assertEqual(monitor.st.terror_ids, [42, config.GLORBO_ID])

    def test_the_line_before_the_killers_still_applies(self):
        """合図が Killers 行より先に来ても、後から来たIDに効くこと"""
        monitor = self._monitor()

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process(self.LINE)
            monitor._on_killers([config.ARKUS_ID], "Punished", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.GLORBO_ID])

    def test_the_handler_logs_once(self):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, logs.append,
                                        window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Punished"

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as play:
            monitor._process(self.LINE)

        self.assertEqual(len([m for m in logs if "Glorbo 出現" in m]), 1, logs)
        play.assert_not_called()      # 専用の音声は作らない

    # ── 続行・自爆（既存の _plan 経由）─────────────
    def _plan(self, keep_on, ids):
        monitor = self._monitor(keep_on)
        monitor.st.terror_ids = list(ids)
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)
        return monitor, monitor._plan("Punished", monitor.st.terror_ids, False)

    def test_glorbo_on_the_list_continues(self):
        monitor, plan = self._plan({self.PUNISHED_KEY: {config.GLORBO_ID}},
                                   [config.ARKUS_ID])

        self.assertEqual(monitor.st.terror_ids, [config.GLORBO_ID])
        self.assertEqual(plan, ("glorbo",), "続行（Glorbo の手順）")

    def test_glorbo_off_the_list_skips(self):
        """Arkus が続行指定でも、Glorbo になったら自爆する"""
        monitor, plan = self._plan({self.PUNISHED_KEY: {config.ARKUS_ID}},
                                   [config.ARKUS_ID])

        self.assertEqual(monitor.st.terror_ids, [config.GLORBO_ID])
        self.assertEqual(plan, ("list", False, False), "自爆")

    def test_arkus_on_the_list_continues_without_the_line(self):
        monitor = self._monitor({self.PUNISHED_KEY: {config.ARKUS_ID}})
        monitor.st.terror_ids = [config.ARKUS_ID]

        plan = monitor._plan("Punished", monitor.st.terror_ids, False)

        self.assertEqual(plan, ("list", True, False), "Arkus のままなら続行")

    def test_it_waits_for_the_line_when_that_changes_the_decision(self):
        """合図を待たずに判定すると、差し替えが間に合わない（117dc82 の 0.3 秒）"""
        monitor = self._monitor({self.PUNISHED_KEY: {config.ARKUS_ID}})

        with patch.object(LogMonitor.threading, "Thread") as thread,              patch.object(PlaySound, "play_sound"):
            monitor._on_killers([config.ARKUS_ID], "Punished", revealed=False)

        started = [c.kwargs["target"].__func__.__name__
                   for c in thread.call_args_list if "target" in c.kwargs]
        self.assertIn("_delayed_decision", started, started)

    # ── 表と後始末 ──────────────────────────────
    def test_the_flag_is_cleared_when_a_round_starts(self):
        monitor = self._monitor()
        monitor.st.glorbo = True

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.PREFIX + "This round is taking place at "
                                           "Sewers(20) and the round type is Punished")

        self.assertFalse(monitor.st.glorbo)

    def test_the_row_is_in_the_table(self):
        rows = TerrorReplacement.rows_for_flag("glorbo")

        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row.source, row.target_id()),
                         (config.ARKUS_ID, config.GLORBO_ID))
        self.assertEqual(row.rounds, frozenset({"Punished"}))
        self.assertTrue(row.enabled)
        self.assertIn("glorbo", TerrorReplacement.flags())

    def test_it_comes_before_the_gigabytes(self):
        """Gigabytes は構成ごと差し替えるので最後のまま"""
        flags = [row.flag for row in TerrorReplacement.TABLE]

        self.assertLess(flags.index("glorbo"), flags.index("gigabytes"))

    def test_glorbo_is_a_classic_terror_not_an_alternate(self):
        """置き換え元の Arkus と同じカテゴリに置く。punished カテゴリは無い"""
        self.assertEqual(ReadJson.terror_name(config.ARKUS_ID, config.TERRORS),
                         "Arkus", "置き換え元のIDが Arkus を指していること")
        self.assertEqual(ReadJson.terror_name(config.GLORBO_ID, config.TERRORS),
                         "Glorbo")
        self.assertFalse(ReadJson.is_alternate_terror(config.GLORBO_ID,
                                                      config.TERRORS))
        self.assertNotIn(config.GLORBO_ID, config.VARIANT_TERROR_IDS)
