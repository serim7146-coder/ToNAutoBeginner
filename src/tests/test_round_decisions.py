"""ラウンドの判定（続行リスト・グループ・moon・Sabotage・自爆/続行の指定）"""
from tests.support import *  # noqa: F401,F403




class TestIsAlternateTerror(unittest.TestCase):
    """terrors.json のカテゴリでオルタネイト枠を判定する"""

    WALPURGISNACHT = 167     # alternate
    STARVED = 22             # classic
    SELF_INSERTS = 283       # unbound

    def test_the_categories_do_not_overlap(self):
        """重複があると、この判定そのものが成り立たない"""
        seen = {}
        for category in ("classic", "alternate", "unbound"):
            for id_ in (config.TERRORS.get(category) or {}):
                self.assertNotIn(int(id_), seen,
                                 f"{id_} が {seen.get(int(id_))} と重複")
                seen[int(id_)] = category

    def test_an_alternate_terror(self):
        self.assertTrue(ReadJson.is_alternate_terror(self.WALPURGISNACHT,
                                                     config.TERRORS))

    def test_a_classic_terror(self):
        self.assertFalse(ReadJson.is_alternate_terror(self.STARVED,
                                                      config.TERRORS))

    def test_an_unbound_terror(self):
        self.assertFalse(ReadJson.is_alternate_terror(self.SELF_INSERTS,
                                                      config.TERRORS))

    def test_junk_does_not_raise(self):
        for tid in (None, "167", 999999, -1, True, 1.5):
            self.assertFalse(ReadJson.is_alternate_terror(tid, config.TERRORS),
                             repr(tid))

    def test_a_table_without_the_category_does_not_raise(self):
        for data in ({}, {"classic": {"1": "A"}}, {"alternate": None}):
            self.assertFalse(ReadJson.is_alternate_terror(167, data), data)

    def test_every_alternate_id_is_above_the_offset_range(self):
        """135以下だと apply_alternate_offset が二重に足してしまう"""
        for id_ in (config.TERRORS.get("alternate") or {}):
            self.assertGreater(int(id_), MatchTNL.ALTERNATE_LOG_MAX, id_)




class TestGroupRoundTable(unittest.TestCase):
    """第1部: 干し芋/焼き芋のラウンド判定表"""

    HOSHIIMO = config.INSTANCE_HOSHIIMO
    YAKIIMO = config.INSTANCE_YAKIIMO
    SONIC = 40

    def _decide(self, instance_type, round_type, terror_ids=(1,), **kw):
        return GroupRound.decide(instance_type, round_type, list(terror_ids), **kw)

    def _both(self, round_type, terror_ids=(1,), **kw):
        return {self._decide(self.HOSHIIMO, round_type, terror_ids, **kw),
                self._decide(self.YAKIIMO, round_type, terror_ids, **kw)}

    def test_classic_without_a_variant_is_skipped(self):
        self.assertEqual(self._both("Classic", [self.SONIC]), {GroupRound.SKIP})

    def test_classic_with_each_variant_falls_through(self):
        for tid in (190, 191, 192, 314):
            self.assertEqual(self._both("Classic", [tid]), {GroupRound.NORMAL}, tid)

    def test_bloodbath_is_skipped(self):
        self.assertEqual(self._both("Bloodbath"), {GroupRound.SKIP})

    def test_classic_exe_and_randomizer_are_skipped_even_with_a_variant(self):
        """Variant例外なし"""
        for round_type in ("Classic.exe", "Randomizer"):
            self.assertEqual(self._both(round_type, [192]), {GroupRound.SKIP},
                             round_type)

    def test_double_trouble_uses_the_normal_judgement(self):
        self.assertEqual(self._both("Double Trouble"), {GroupRound.NORMAL})

    def test_eight_pages_run_and_bloodbath_ex_always_continue(self):
        for round_type in ("8 Pages", "Run", "Bloodbath EX"):
            self.assertEqual(self._both(round_type), {GroupRound.CONTINUE}, round_type)

    def test_hoshiimo_fog_always_continues(self):
        self.assertEqual(self._decide(self.HOSHIIMO, "Fog", [7]),
                         GroupRound.CONTINUE)

    def test_yakiimo_fog_revealed_as_alternate_is_judged(self):
        alternate = MatchTNL.ALTERNATE_OFFSET + 5

        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog", [alternate],
                         killers_round_type="Fog (Alternate)"),
            GroupRound.NORMAL)

    def test_yakiimo_fog_revealed_as_plain_fog_is_skipped(self):
        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog", [7], killers_round_type="Fog"),
            GroupRound.SKIP)

    def test_an_unknown_killers_round_type_does_not_skip(self):
        """判定材料が無いときは自爆しない側へ倒す（通常の経路では来ない）"""
        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog", [7], killers_round_type=""),
            GroupRound.CONTINUE)

    def test_yakiimo_fog_updated_by_foxy_is_still_the_fog_row(self):
        """Foxy検出で st.round_type が Fog (Alternate) に更新されることがある"""
        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog (Alternate)", [140],
                         killers_round_type="Fog (Alternate)"),
            GroupRound.NORMAL)
        self.assertEqual(
            self._decide(self.HOSHIIMO, "Fog (Alternate)", [140]),
            GroupRound.CONTINUE)

    def test_a_repeated_moon_is_skipped(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._both(moon, moon_repeat=True),
                             {GroupRound.SKIP}, moon)

    def test_yakiimo_skips_the_first_mystic_moon_and_solstice(self):
        for moon in ("Mystic Moon", "Solstice"):
            self.assertEqual(self._decide(self.YAKIIMO, moon), GroupRound.SKIP, moon)

    def test_yakiimo_plays_the_first_blood_moon_and_twilight(self):
        for moon in ("Blood Moon", "Twilight"):
            self.assertEqual(self._decide(self.YAKIIMO, moon),
                             GroupRound.CONTINUE, moon)

    def test_hoshiimo_plays_every_first_moon(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon),
                             GroupRound.CONTINUE, moon)

    def test_anything_else_uses_the_normal_judgement(self):
        for round_type in ("Midnight", "Punished", "Cracked", "Ghost", "Unbound"):
            self.assertEqual(self._both(round_type), {GroupRound.NORMAL}, round_type)

    def test_private_is_never_touched(self):
        for round_type in ("Classic", "Bloodbath", "8 Pages", "Fog", "Sabotage"):
            self.assertEqual(
                self._decide(config.INSTANCE_PRIVATE, round_type),
                GroupRound.NORMAL, round_type)

    def test_other_instances_are_never_touched(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP):
            self.assertEqual(self._decide(itype, "Classic"),
                             GroupRound.NORMAL, itype)




class TestSpecialMoonKey(unittest.TestCase):
    """ToN ListTool は Variant と Moon を Special/Moon の13枠にまとめて記録する。

    ラウンド別のキーは空のままなので、そちらだけ見ていると永久に空振りする。
    ただし置き換えてはいけない——ID192 はラウンド別のキーにも入っている。
    """

    SPECIAL = MatchTNL.SPECIAL_MOON_KEY
    CLASSIC = "Classic/クラシック"
    FOG = "Fog/霧"

    def _continue(self, round_type, terror_ids, keep_on, wins=99, cancel_afk=False):
        return RoundDecision.decide_killers(
            keep_on, list(terror_ids), round_type, wins, cancel_afk
        ).is_continue_round

    # ── Variant ─────────────────────────────
    def test_each_variant_is_found_in_the_special_slot(self):
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
                    config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID):
            self.assertTrue(
                self._continue("Classic", [tid], {self.SPECIAL: {tid}}), tid)

    def test_a_variant_missing_from_the_slot_does_not_continue(self):
        self.assertFalse(
            self._continue("Classic", [config.ATRACHED_ID],
                           {self.SPECIAL: {config.GIGABYTES_ID}}))

    def test_an_unmutated_terror_is_not_covered(self):
        """Sonic のまま（Variant確定前）なら Special/Moon に居ない"""
        self.assertFalse(
            self._continue("Classic", [config.SONIC_ID],
                           {self.SPECIAL: {config.ATRACHED_ID}}))

    def test_an_ordinary_terror_passes_straight_through(self):
        self.assertFalse(self._continue("Classic", [42], {self.SPECIAL: {191}}))

    # ── ラウンド別キーを取りこぼさない ────────────
    def test_classic_sees_bloodthirsty_in_the_special_slot(self):
        self.assertTrue(
            self._continue("Classic", [config.BLOODTHIRSTY_CREATURE_ID],
                           {self.SPECIAL: {config.BLOODTHIRSTY_CREATURE_ID}}))

    def test_a_round_key_still_wins_on_its_own(self):
        """ID192 は Fog などラウンド別のキーにも入っている。寄せると落ちる"""
        self.assertTrue(
            self._continue("Fog", [config.BLOODTHIRSTY_CREATURE_ID],
                           {self.FOG: {config.BLOODTHIRSTY_CREATURE_ID}}))

    def test_other_rounds_do_not_look_at_the_special_slot(self):
        """Special/Moon にだけ192を入れている人が、Fog などで誤続行しないこと"""
        for round_type in ("Fog", "Ghost", "Midnight", "Punished"):
            self.assertFalse(
                self._continue(round_type, [config.BLOODTHIRSTY_CREATURE_ID],
                               {self.SPECIAL: {config.BLOODTHIRSTY_CREATURE_ID}}),
                round_type)

    def test_being_in_both_is_fine(self):
        keep_on = {"Classic/クラシック": {config.BLOODTHIRSTY_CREATURE_ID},
                   self.SPECIAL: {config.BLOODTHIRSTY_CREATURE_ID}}

        self.assertTrue(
            self._continue("Classic", [config.BLOODTHIRSTY_CREATURE_ID], keep_on))

    def test_the_round_list_is_classic_and_the_four_moons(self):
        self.assertEqual(
            RoundDecision.SPECIAL_MOON_ROUNDS,
            {"Classic", "Mystic Moon", "Blood Moon", "Twilight", "Solstice"})

    def test_a_classic_round_key_still_works(self):
        self.assertTrue(self._continue("Classic", [42],
                                       {"Classic/クラシック": {42}}))

    def test_an_unrelated_round_key_is_untouched(self):
        self.assertTrue(self._continue("Fog", [7], {self.FOG: {7}}))
        self.assertFalse(self._continue("Fog", [7], {self.FOG: {8}}))

    # ── Moon ────────────────────────────────
    def test_moons_are_found_in_the_special_slot(self):
        for round_type, tid in (("Mystic Moon", 313), ("Blood Moon", 315),
                                ("Twilight", 316), ("Solstice", 317)):
            self.assertTrue(
                self._continue(round_type, [tid], {self.SPECIAL: {tid}}),
                round_type)

    def test_a_special_round_type_still_works(self):
        for round_type in ("Special", "Moon"):
            self.assertTrue(
                self._continue(round_type, [313], {self.SPECIAL: {313}}),
                round_type)
            self.assertFalse(
                self._continue(round_type, [313], {self.SPECIAL: {315}}),
                round_type)

    # ── 壊れない ────────────────────────────
    def test_a_missing_special_key_is_fine(self):
        self.assertFalse(self._continue("Classic", [191], {self.CLASSIC: {1}}))

    def test_an_empty_keep_list_is_fine(self):
        self.assertFalse(self._continue("Classic", [191], {}))

    def test_an_empty_terror_list_is_fine(self):
        self.assertFalse(self._continue("Classic", [], {self.SPECIAL: {191}}))

    def test_the_open_special_round_path_is_unchanged(self):
        dtm = LogMonitor.DTM_TERROR_ID
        decision = RoundDecision.decide_killers({}, [dtm], "Classic", 0, True)

        self.assertTrue(decision.is_open_special_round_target)
        self.assertTrue(decision.is_continue_round)

    def test_the_real_list_now_covers_atrached(self):
        """現物で再現していた不具合。Classic/クラシックは空、191はSpecial/Moon"""
        if not Path(config.HOST_STATE_PATH).exists():
            self.skipTest("host_state.sqlite3 が無い")
        keep_on, _meta, _wishes = MatchTNL.load_host_state(
            config.HOST_STATE_PATH, config.USER_SAVE_PATH)
        if config.ATRACHED_ID not in keep_on.get(self.SPECIAL, set()):
            self.skipTest("現物の Special/Moon に191が無い")

        self.assertNotIn(config.ATRACHED_ID, keep_on.get(self.CLASSIC, set()),
                         "ラウンド別キーには入っていない")
        self.assertTrue(
            self._continue("Classic", [config.ATRACHED_ID], keep_on))




class TestSpecialMoonKeyWiring(unittest.TestCase):
    """インスタンス種別によらず同じに効くこと"""

    SPECIAL = MatchTNL.SPECIAL_MOON_KEY

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

    def _monitor(self, instance_type):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(
            cfg, {self.SPECIAL: {config.ATRACHED_ID}}, lambda _m: None,
            window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.atrached_variant = True   # Variant は確定済み
        return monitor

    def test_a_classic_variant_continues_everywhere(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype)

            # 待ちを止めるのに st.gigabytes を立ててはいけない。_on_killers が
            # ids を [314] に差し替えるので、判定したいIDごと変わってしまう
            with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
                 patch.object(LogMonitor.LogMonitor,
                              "_replacement_changes_decision",
                              return_value=False), \
                 patch.object(PlaySound, "play_sound"):
                monitor._on_killers([config.ATRACHED_ID], "Classic", revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]

            self.assertTrue(monitor.st.is_continue_round, itype)
            self.assertNotIn("do_skip", started, itype)




class TestGroupMoonSkip(unittest.TestCase):
    """自爆リストの moon は干し芋/焼き芋でも効く（1回目でも自爆）"""

    HOSHIIMO = config.INSTANCE_HOSHIIMO
    YAKIIMO = config.INSTANCE_YAKIIMO

    def _decide(self, instance_type, moon, skip_moons=(), moon_repeat=False):
        return GroupRound.decide(instance_type, moon, [7],
                                 skip_moons=skip_moons, moon_repeat=moon_repeat)

    # ── 指定すれば自爆 ────────────────────────
    def test_hoshiimo_skips_every_checked_moon(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon, {moon}),
                             GroupRound.SKIP, moon)

    def test_a_checked_moon_skips_on_its_first_appearance(self):
        """消化済み扱いにはしない。指定されているから自爆する"""
        self.assertEqual(
            self._decide(self.HOSHIIMO, "Mystic Moon", {"Mystic Moon"},
                         moon_repeat=False),
            GroupRound.SKIP)

    def test_yakiimo_skips_a_checked_blood_moon(self):
        """焼き芋で実際に挙動が変わるのは Blood Moon / Twilight"""
        for moon in ("Blood Moon", "Twilight"):
            self.assertEqual(self._decide(self.YAKIIMO, moon, {moon}),
                             GroupRound.SKIP, moon)

    def test_only_the_named_moon_is_affected(self):
        decided = self._decide(self.HOSHIIMO, "Twilight", {"Blood Moon"})

        self.assertEqual(decided, GroupRound.CONTINUE)

    # ── 指定しなければ従来どおり ─────────────────
    def test_an_unchecked_first_moon_still_plays(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon),
                             GroupRound.CONTINUE, moon)

    def test_an_unchecked_repeat_still_skips(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon, moon_repeat=True),
                             GroupRound.SKIP, moon)

    def test_yakiimo_first_moons_are_unchanged(self):
        for moon in ("Mystic Moon", "Solstice"):
            self.assertEqual(self._decide(self.YAKIIMO, moon),
                             GroupRound.SKIP, moon)

    def test_omitting_the_argument_keeps_the_old_behaviour(self):
        self.assertEqual(
            GroupRound.decide(self.HOSHIIMO, "Blood Moon", [7]),
            GroupRound.CONTINUE)

    def test_a_non_moon_in_the_set_changes_nothing(self):
        decided = self._decide(self.HOSHIIMO, "Blood Moon",
                               {"Classic", "Bloodbath"})

        self.assertEqual(decided, GroupRound.CONTINUE)

    def test_the_earlier_branches_are_untouched(self):
        """moon の節より前の分岐に影響していないこと"""
        self.assertEqual(
            GroupRound.decide(self.HOSHIIMO, "Bloodbath", [1],
                              skip_moons={"Bloodbath", "Mystic Moon"}),
            GroupRound.SKIP)
        self.assertEqual(
            GroupRound.decide(self.HOSHIIMO, "8 Pages", [1],
                              skip_moons={"8 Pages", "Mystic Moon"}),
            GroupRound.CONTINUE)




class TestGroupMoonSkipWiring(unittest.TestCase):
    """LogMonitor から渡すのは moon だけ"""

    def _monitor(self, skip_rounds=()):
        cfg = WindowConfig(skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        monitor.st.round_type = "Twilight"
        monitor.st.terror_ids = [7]
        return monitor

    def _passed(self, monitor):
        with patch.object(GroupRound, "decide",
                          return_value=GroupRound.SKIP) as mock_decide:
            monitor._group_decision("Twilight")
        return mock_decide.call_args.kwargs["skip_moons"]

    def test_only_moons_are_passed(self):
        monitor = self._monitor({"Classic", "Twilight", "Bloodbath"})

        self.assertEqual(self._passed(monitor), {"Twilight"})

    def test_an_empty_list_passes_an_empty_set(self):
        monitor = self._monitor()

        self.assertEqual(self._passed(monitor), set())

    def test_every_moon_is_passed_through(self):
        monitor = self._monitor(set(RoundSequence.MOONS) | {"Classic"})

        self.assertEqual(self._passed(monitor), set(RoundSequence.MOONS))

    def test_a_checked_moon_skips_end_to_end(self):
        """干し芋の窓で、1回目の Twilight が自爆になること"""
        SharedState.set_list_source("host")
        try:
            monitor = self._monitor({"Twilight"})
            monitor.st.in_round = True
            with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
                 patch.object(PlaySound, "play_sound"), \
                 patch.object(ConnectDB, "register_round"):
                monitor._on_killers([7], "Twilight", revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]
        finally:
            SharedState.set_list_source(None)

        self.assertIn("do_skip", started)

    def test_the_round_sequence_is_not_touched(self):
        """消化済み扱いにしない。moon_done も is_moon_repeat も動かさない"""
        seq = RoundSequence.RoundSequence()
        monitor = self._monitor({"Twilight"})
        monitor.sequence = seq

        with patch.object(GroupRound, "decide", return_value=GroupRound.SKIP):
            monitor._group_decision("Twilight")

        self.assertFalse(any(seq.moon_done.values()))
        self.assertFalse(seq.is_moon_repeat("Twilight"))




class TestPrivateSabotageContinues(unittest.TestCase):
    """プラベ系の Sabotage は焼き芋と同じ判定にする。

    続行リストには `Sabotage star` と `Sabotage murder` の枠しかなく、
    `Sabotage` の枠はどのリストにも無い。リスト判定に落とすと毎回自爆になる
    （依頼者の報告: フレンド+以下でサボタージュのたびに自爆する）。
    """

    SABO = "Sabotage/サボタージュ"
    CLASSIC = "Classic/クラシック"
    MURDER = GroupRound.SABOTAGE_MURDER_KEY
    STAR = GroupRound.SABOTAGE_STAR_KEY
    TERROR = 7

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_list_source, None)
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self._start(patch.object(ConnectDB, "register_round"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, instance_type=None, keep=None, wishes=None,
                 murderer=None, **cfg_kwargs):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", **cfg_kwargs)
        monitor = LogMonitor.LogMonitor(cfg, keep or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type or config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.sus_players = [murderer] if murderer else []
        monitor.host_wishes = dict(wishes or {})
        # この窓にいる人の希望だけで判定する（_effective_wishes）
        monitor.st.players_known = True
        monitor.st.players = set(range(len(monitor.host_wishes)))
        monitor.st.player_names = dict(zip(monitor.st.players, monitor.host_wishes))
        monitor.host_participants = set(monitor.host_wishes)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _run(self, monitor, round_type="Sabotage", ids="7 0 0"):
        monitor._process(f"Killers have been set - {ids} // Round type is {round_type}")
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs]

    # ── 焼き芋と同じ判定 ───────────────────────
    def test_a_wish_from_someone_else_continues_and_announces(self):
        """マーダー以外が star 枠で希望 → 続行（WANTED と同じ扱い）"""
        monitor = self._monitor(
            murderer="マーダー",
            wishes={"ほかのひと": {self.STAR: {self.TERROR}}})

        started = self._run(monitor)

        self.assertNotIn("do_skip", started, "自爆しない")
        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3")
        self.assertEqual(SharedState.get_continue_round_count(), 1, "他窓フリーズ")
        self.record.assert_called_once_with(1)

    def test_the_murderers_own_wish_skips(self):
        """選ばれたマーダーがマーダー枠で希望 → 自爆（焼き芋と同じ）"""
        monitor = self._monitor(
            murderer="マーダー",
            wishes={"マーダー": {self.MURDER: {self.TERROR},
                                 self.STAR: {self.TERROR}}})

        started = self._run(monitor)

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)
        self.play.assert_not_called()

    def test_the_murderers_wish_beats_someone_elses_star(self):
        """マーダーが欲しがっているなら、ほかの人が star で欲しがっていても自爆"""
        monitor = self._monitor(
            murderer="マーダー",
            wishes={"マーダー": {self.MURDER: {self.TERROR}},
                    "ほかのひと": {self.STAR: {self.TERROR}}})

        started = self._run(monitor)

        self.assertIn("do_skip", started)
        self.play.assert_not_called()

    def test_no_wish_at_all_skips(self):
        monitor = self._monitor(
            murderer="マーダー",
            wishes={"ほかのひと": {self.STAR: {42}}})   # 別のテラーの希望

        started = self._run(monitor)

        self.assertIn("do_skip", started)

    def test_without_the_host_list_it_falls_back_to_the_list(self):
        """follow_host 無し（host_wishes が空）→ 今までどおり通常判定"""
        monitor = self._monitor(wishes={}, keep={self.CLASSIC: {self.TERROR}})

        started = self._run(monitor)

        self.assertIn("do_skip", started, "`Sabotage` の枠が無いので自爆のまま")
        self.assertTrue(any("判定" in m for m in monitor.logs), monitor.logs)

    def test_the_continue_list_is_not_used_when_the_wishes_decide(self):
        monitor = self._monitor(
            murderer="マーダー",
            wishes={"ほかのひと": {self.STAR: {self.TERROR}}},
            keep={self.SABO: set()})

        started = self._run(monitor)

        self.assertNotIn("do_skip", started)

    # ── 指定が勝つ ──────────────────────────────
    def test_the_skip_setting_still_wins(self):
        monitor = self._monitor(skip_rounds={"Sabotage"}, murderer="マーダー",
                                wishes={"ほかのひと": {self.STAR: {self.TERROR}}})

        started = self._run(monitor)

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)
        self.play.assert_not_called()

    def test_the_continue_setting_stays_quiet(self):
        monitor = self._monitor(continue_rounds={"Sabotage"}, murderer="マーダー",
                                wishes={"マーダー": {self.MURDER: {self.TERROR}}})

        started = self._run(monitor)

        self.assertNotIn("do_skip", started)
        self.play.assert_not_called()
        self.record.assert_not_called()

    def test_hands_free_still_skips_at_once(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(murderer="マーダー",
                                wishes={"ほかのひと": {self.STAR: {self.TERROR}}})
        monitor.st.item_id = 0

        started = self._run(monitor)

        self.assertIn("do_skip", started)
        self.assertTrue(any("放置モード" in m for m in monitor.logs), monitor.logs)

    # ── ほかは今までどおり ──────────────────────
    def test_a_group_sabotage_is_unchanged(self):
        for instance in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            self.thread.reset_mock()
            self.play.reset_mock()
            SharedState.continue_round_reset()
            monitor = self._monitor(instance_type=instance)

            with patch.object(monitor, "_group_decision",
                              return_value=GroupRound.SKIP) as group:
                started = self._run(monitor)

            group.assert_called()
            self.assertIn("do_skip", started, f"{instance}: グループの判定に従う")
            self.assertFalse(monitor.st.is_continue_round)

    def test_a_group_sabotage_without_a_wish_still_skips(self):
        """グループで選出者でもない Sabotage は、今までどおりリスト判定（自爆）"""
        monitor = self._monitor(instance_type=config.INSTANCE_HOSHIIMO, keep={})

        with patch.object(monitor, "_group_decision", return_value=GroupRound.NORMAL):
            started = self._run(monitor)

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)
        self.play.assert_not_called()

    def test_a_public_window_is_unchanged(self):
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC,
                                murderer="マーダー",
                                wishes={"ほかのひと": {self.STAR: {self.TERROR}}})

        started = self._run(monitor)

        self.assertEqual(started, [], "操作しない窓")
        self.assertFalse(monitor.st.is_continue_round)

    def test_other_rounds_still_use_the_list(self):
        monitor = self._monitor(keep={self.CLASSIC: {42}})

        self.assertIn("do_skip", self._run(monitor, "Classic", "7 0 0"), "リストに無い")

        self.thread.reset_mock()
        monitor.st.terror_ids = []
        monitor.st.round_type = ""
        self.assertNotIn("do_skip", self._run(monitor, "Classic", "42 0 0"), "リストにある")

    def test_the_sabotage_star_round_type_is_unchanged(self):
        """グループの Killers 行に出る Sabotage star は触らない。

        マーダー本人の star の希望は、Sabotage では数えない（自爆）が、
        Sabotage star のラウンドでは今までどおり希望として効く（続行）
        """
        wishes = {"マーダー": {self.STAR: {self.TERROR}}}
        star = self._monitor(murderer="マーダー", wishes=wishes)

        self.assertNotIn("do_skip", self._run(star, "Sabotage star", "7 0 0"))

        self.thread.reset_mock()
        SharedState.continue_round_reset()
        sabo = self._monitor(murderer="マーダー", wishes=wishes)
        self.assertIn("do_skip", self._run(sabo), "Sabotage では数えない")




class TestGroupRoundSabotage(unittest.TestCase):
    """第2部: Sabotage の選出者判定"""

    STAR = GroupRound.SABOTAGE_STAR_KEY
    MURDER = GroupRound.SABOTAGE_MURDER_KEY

    def _decide(self, instance_type, terror_ids=(5,), sus=(), wishes=None,
                follow_host=True):
        return GroupRound.decide(
            instance_type, "Sabotage", list(terror_ids),
            sus_players=list(sus),
            host_wishes=wishes if wishes is not None else {},
            follow_host=follow_host,
        )

    def test_a_murderer_missing_from_the_list_has_no_wish(self):
        """実測では28人中7人が未掲載。珍しくない"""
        wishes = {"ほかのひと": {self.STAR: {99}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["のってないひと"],
                         wishes=wishes),
            GroupRound.SKIP)

    def test_yakiimo_skips_when_a_murderer_wants_the_murder_slot(self):
        wishes = {"ソノア7": {self.MURDER: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.SKIP)

    def test_one_of_two_murderers_is_enough(self):
        wishes = {"ソノア7": {self.MURDER: {99}},
                  "ユウナ2858": {self.MURDER: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO,
                         sus=["ソノア7", "ユウナ2858"], wishes=wishes),
            GroupRound.SKIP)

    def test_a_non_murderer_wanting_the_star_slot_is_wanted(self):
        """誰かが欲しがっている続行。全続行とは別枠（アナウンスが要る）"""
        wishes = {"ソノア7": {self.MURDER: {99}},
                  "みているひと": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.WANTED)

    def test_the_murderers_own_star_wish_does_not_count(self):
        wishes = {"ソノア7": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.SKIP)

    def test_hoshiimo_ignores_the_murder_slot(self):
        """干し芋はマーダー側の判定をしない"""
        wishes = {"ソノア7": {self.MURDER: {5}},
                  "みているひと": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_HOSHIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.WANTED, "マーダー希望があっても star で続行になること")

    def test_hoshiimo_skips_without_a_star_wish(self):
        wishes = {"みているひと": {self.STAR: {99}}}

        self.assertEqual(
            self._decide(config.INSTANCE_HOSHIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.SKIP)

    def test_following_off_falls_back_to_the_normal_judgement(self):
        """追従OFFでは誰の希望か分からない。従来どおり keepOn_set で判定する"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            self.assertEqual(
                self._decide(itype, sus=["ソノア7"], wishes={}, follow_host=False),
                GroupRound.NORMAL, itype)

    def test_following_on_but_empty_list_also_falls_back(self):
        """0人のときは前のリストを保持しているので、希望が空なら判断できない"""
        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes={}),
            GroupRound.NORMAL)




class TestRoundSequence(unittest.TestCase):
    """ラウンド並び(N/S)の推定と moon の解放判定"""

    def _seq(self, *round_types):
        seq = RoundSequence.RoundSequence()
        for round_type in round_types:
            seq.on_round(round_type)
        return seq

    def _label(self, seq, index):
        return seq.labels()[index][1]

    def test_ghost_is_normal_when_followed_by_a_special(self):
        """Classic → Ghost → Midnight。Midnight(S)はN連が要るのでGhostはN"""
        seq = self._seq("Classic", "Ghost", "Midnight")

        self.assertEqual(self._label(seq, 1), "N")

    def test_ghost_is_special_when_followed_by_a_normal(self):
        """Classic → Ghost → Run。GhostもNだとN連3になるのでGhostはS"""
        seq = self._seq("Classic", "Ghost", "Run")

        self.assertEqual(self._label(seq, 1), "S")

    def test_two_overrides_in_a_row_are_resolved(self):
        seq = self._seq("Classic", "Unbound", "Ghost", "Bloodbath")

        self.assertEqual(self._label(seq, 1), "S", "Unbound")
        self.assertEqual(self._label(seq, 2), "N", "Ghost")

    def test_master_switch_allows_a_special_after_a_special(self):
        """通常なら S は連続しない。master切替の次だけ許す"""
        seq = self._seq("Classic", "Ghost", "Midnight")   # Midnight で run=0
        seq.on_master_switched()

        seq.on_round("Bloodbath")

        self.assertEqual(self._label(seq, 3), "S")
        self.assertTrue(seq._hyps, "矛盾で仮説が空になっていないこと")

    def test_master_switch_applies_only_to_the_next_round(self):
        seq = self._seq("Classic", "Ghost", "Midnight")
        seq.on_master_switched()
        seq.on_round("Bloodbath")

        self.assertFalse(seq.force_special, "1ラウンドで使い切ること")

    def test_classics_may_repeat_before_the_first_special(self):
        """3クラ未解放の間は Classic しか来ない。N連の上限を当てはめない"""
        seq = self._seq(*["Classic"] * 6)

        self.assertTrue(seq._hyps, "矛盾扱いにしないこと")
        self.assertFalse(seq.special_seen)
        self.assertEqual([lab for _t, lab in seq.labels()], ["N"] * 6)

    def test_first_moon_can_be_either_but_a_repeat_is_special(self):
        first = RoundSequence.RoundSequence()
        self.assertEqual(
            first._candidate_labels("Mystic Moon", False, True), ("N", "S"))

        repeat = RoundSequence.RoundSequence()
        repeat.moon_done["Mystic Moon"] = True
        self.assertEqual(
            repeat._candidate_labels("Mystic Moon", False, False), ("S",))

    def test_a_moon_flags_only_itself(self):
        seq = self._seq("Classic", "Mystic Moon")

        self.assertTrue(seq.is_moon_repeat("Mystic Moon"))
        for other in ("Blood Moon", "Twilight", "Solstice"):
            self.assertFalse(seq.is_moon_repeat(other), other)

    def test_the_flag_is_set_even_if_the_round_is_skipped(self):
        """焼き芋は1回目の Mystic Moon をスキップするが、出た事実は変わらない"""
        seq = RoundSequence.RoundSequence()
        seq.on_round("Classic")

        was_repeat = seq.is_moon_repeat("Mystic Moon")
        seq.on_round("Mystic Moon")     # スキップしても on_round は通す

        self.assertFalse(was_repeat, "判定時点では1回目")
        self.assertTrue(seq.is_moon_repeat("Mystic Moon"))

    def test_alternate_confirmed_normal_unlocks_all_four(self):
        seq = self._seq("Classic", "Alternate", "Midnight")

        self.assertEqual(self._label(seq, 1), "N")
        self.assertTrue(seq.moons_unlocked())

    def test_alternate_confirmed_special_unlocks_nothing(self):
        """直前にNが2つ続いていれば Alternate は S しか取れない"""
        seq = self._seq("Classic", "Ghost", "Bloodbath", "Classic", "Classic",
                        "Alternate")

        self.assertEqual(self._label(seq, 5), "S")
        self.assertFalse(seq.moons_unlocked())

    def test_an_undetermined_alternate_does_not_unlock(self):
        """未確定のうちは経路Bを発火させない（未解放扱い）"""
        seq = self._seq("Alternate", "Classic")

        self.assertEqual(self._label(seq, 0), "", "まだ決まらないこと")
        self.assertFalse(seq.moons_unlocked())

    def test_a_later_round_can_unlock_retroactively(self):
        seq = self._seq("Alternate")
        self.assertFalse(seq.moons_unlocked())

        seq.on_round("Midnight")

        self.assertEqual(self._label(seq, 0), "N")
        self.assertTrue(seq.moons_unlocked(), "確定した時点で4種立てること")

    def test_reset_clears_everything(self):
        seq = self._seq("Classic", "Mystic Moon", "Alternate", "Midnight")
        self.assertTrue(seq.moons_unlocked())

        seq.reset()

        self.assertFalse(any(seq.moon_done.values()))
        self.assertEqual(seq.labels(), [])
        self.assertFalse(seq.special_seen)
        self.assertEqual(seq._hyps, {(run, ()) for run in range(3)})

    def test_a_contradiction_falls_back_to_unknown(self):
        """前提が崩れても以後の判定を殺さない（仮説を空のままにしない）"""
        seq = RoundSequence.RoundSequence()
        seq.special_seen = True
        seq._hyps = {(2, ())}          # N連が上限。次にNは来られない

        seq.on_round("Classic")

        self.assertEqual(seq._hyps, {(run, ()) for run in range(3)})




class TestRoundSequenceWiring(unittest.TestCase):
    """LogMonitor 側の配線（ラウンド並びの前進とリセット）"""

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        return monitor

    def _round_start(self, monitor, round_type):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("This round is taking place at Facility (12) "
                             f"and the round type is {round_type}")

    def test_round_start_advances_the_sequence(self):
        monitor = self._monitor()

        self._round_start(monitor, "Classic")

        self.assertEqual(monitor.sequence.labels(), [("Classic", "N")])

    def test_moon_repeat_is_decided_before_the_flag_is_set(self):
        """1回目は moon_repeat=False、同じmoonの2回目で True"""
        monitor = self._monitor()

        self._round_start(monitor, "Classic")
        self._round_start(monitor, "Mystic Moon")
        first = monitor.st.moon_repeat
        self._round_start(monitor, "Mystic Moon")

        self.assertFalse(first, "出た本人のラウンドは1回目")
        self.assertTrue(monitor.st.moon_repeat)

    def test_master_switch_line_is_wired(self):
        monitor = self._monitor()

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("2026.09.11 23:50:50 Debug      -  "
                             "[Behaviour] OnMasterClientSwitched")

        self.assertTrue(monitor.sequence.force_special)

    def test_joining_resets_the_sequence(self):
        monitor = self._monitor()
        self._round_start(monitor, "Classic")
        self._round_start(monitor, "Mystic Moon")
        self.assertTrue(monitor.sequence.is_moon_repeat("Mystic Moon"))

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("2026.09.05 14:00:00 Debug      -  [Behaviour] "
                             "Joining wrld_1234:5678~group(grp_x)")

        self.assertFalse(monitor.sequence.is_moon_repeat("Mystic Moon"))
        self.assertEqual(monitor.sequence.labels(), [])




class TestEightPagesReleaseDelay(unittest.TestCase):
    """8 Pages でスキャナーを取ったあとの解除に、猶予を入れる。

    即座に解除すると間が短すぎる（依頼者の指摘）。アイテムロストの装備解除と
    同じ定数を使う（片方を変えれば両方変わる形にしておく）。
    """

    def setUp(self):
        SharedState.speed_freeze_reset()
        SharedState.set_hands_free(False)
        self.addCleanup(SharedState.speed_freeze_reset)
        self.addCleanup(SharedState.set_hands_free, False)

    def _monitor(self, kind="8pages"):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=5), {},
                                       lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "8 Pages"
        monitor._running = True
        monitor.st.speed_freeze_kind = kind
        SharedState.speed_freeze_start(monitor.st)
        return monitor

    @staticmethod
    def _equip(monitor, line="Equipping 42."):
        with patch.object(LogMonitor.threading, "Thread") as thread:
            monitor._process(line)
        return [c.kwargs["target"].__func__.__name__
                for c in thread.call_args_list if "target" in c.kwargs]

    # ── 1. すぐには解除しない ────────────────────
    def test_taking_the_scanner_does_not_release_at_once(self):
        monitor = self._monitor()

        started = self._equip(monitor)

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertTrue(monitor.st.speed_freeze_held, "まだ張っている")
        self.assertIn("_release_speed_freeze_after_delay", started, started)

    def test_it_releases_after_the_same_delay_as_the_equip_wait(self):
        monitor = self._monitor()
        self._equip(monitor)
        logs = []
        monitor.logger = logs.append

        with patch.object(LogMonitor.time, "sleep") as sleep:
            monitor._release_speed_freeze_after_delay(monitor.st.round_seq)

        sleep.assert_called_once_with(config.EQUIP_RELEASE_DELAY_SEC)
        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertFalse(monitor.st.speed_freeze_held)
        self.assertTrue(any(str(config.EQUIP_RELEASE_DELAY_SEC) in m
                            for m in logs), logs)

    def test_it_uses_the_equip_release_constant(self):
        """別の定数を作らない（片方を変えれば両方変わる形にしておく）"""
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        body = src[src.index("def _release_speed_freeze_after_delay"):]
        body = body[:body.index("\n    def ", 10)]

        self.assertIn("time.sleep(config.EQUIP_RELEASE_DELAY_SEC)", body,
                      "待つ長さが同じ定数であること")

    # ── 2. 種別はその場で消える ───────────────────
    def test_the_kind_is_cleared_at_once(self):
        monitor = self._monitor()

        self._equip(monitor)

        self.assertEqual(monitor.st.speed_freeze_kind, "")

    def test_a_second_equip_books_nothing(self):
        monitor = self._monitor()
        self._equip(monitor)

        started = self._equip(monitor, "Equipping 43.")

        self.assertNotIn("_release_speed_freeze_after_delay", started, started)

    # ── 3. 猶予中に次のラウンドが始まったら ─────────────
    def test_a_new_round_during_the_delay_cancels_it(self):
        monitor = self._monitor()
        self._equip(monitor)
        seq = monitor.st.round_seq

        def bump(_sec):
            monitor.st.round_seq += 1

        with patch.object(LogMonitor.time, "sleep", side_effect=bump):
            monitor._release_speed_freeze_after_delay(seq)

        self.assertTrue(monitor.st.speed_freeze_held, "解除しない")

    def test_a_stop_during_the_delay_cancels_it(self):
        monitor = self._monitor()
        self._equip(monitor)

        def stop(_sec):
            monitor._running = False

        with patch.object(LogMonitor.time, "sleep", side_effect=stop):
            monitor._release_speed_freeze_after_delay(monitor.st.round_seq)

        self.assertTrue(monitor.st.speed_freeze_held)

    # ── 4. ラウンド開始の無条件解除は残す ────────────────
    def test_a_round_start_still_releases_unconditionally(self):
        """スキャナーを取らないままラウンドが始まっても止まりっぱなしにしない"""
        monitor = self._monitor()

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertFalse(monitor.st.speed_freeze_held)

    def test_a_late_release_after_a_round_start_is_harmless(self):
        monitor = self._monitor()
        self._equip(monitor)
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        with patch.object(LogMonitor.time, "sleep"):
            monitor._release_speed_freeze_after_delay(monitor.st.round_seq)

        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertEqual(SharedState.get_speed_freeze_count(), 0)

    # ── 5. Punished は変えない ────────────────────
    def test_the_punish_freeze_is_untouched(self):
        monitor = self._monitor(kind="punish")

        started = self._equip(monitor)

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertEqual(monitor.st.speed_freeze_kind, "punish")
        self.assertNotIn("_release_speed_freeze_after_delay", started, started)




class TestEightPagesListId(unittest.TestCase):
    """8 Pages のログ番号（A）は専用の番号。terrors.json の list_id で
    続行リストのIDへ橋渡しする。B は別物なので捨てる"""

    PAGES = "8 Pages/8ページ"
    REAL = {50: 135, 59: 39, 55: 85, 46: 172}      # 実ログと ListTool の履歴で確かめた対応

    def setUp(self):
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)

    # ── 表 ────────────────────────────────
    def test_the_real_table_is_in_terrors_json(self):
        for page, list_id in self.REAL.items():
            self.assertEqual(ReadJson.eight_pages_list_id(page, config.TERRORS),
                             list_id, page)

    def test_an_unknown_or_null_page_is_none(self):
        # 本物の terrors.json は全部埋まっているので、テスト用の表で見る
        data = eight_pages_terrors({0: None, 50: 135})
        self.assertIsNone(ReadJson.eight_pages_list_id(0, data), "list_id が null")
        self.assertIsNone(ReadJson.eight_pages_list_id(44, data), "表に無い")
        self.assertIsNone(ReadJson.eight_pages_list_id("いろは", data))
        self.assertEqual(ReadJson.eight_pages_list_id(50, data), 135, "前提: 表は読めている")

    def test_a_missing_or_broken_table_does_not_raise(self):
        for raw in ({"classic": []}, {"8pages": "壊れている"},
                    {"8pages": [None, {"id": "x"}, {"id": 5, "list_id": "y"},
                                {"id": True, "list_id": 3}, {"id": "7", "list_id": 9}]}):
            data = ReadJson.normalize_terrors(raw)
            self.assertIsNone(ReadJson.eight_pages_list_id(5, data), raw)
        self.assertEqual(ReadJson.eight_pages_list_id(7, data), 9, "文字列のIDも読む")

    def test_only_the_first_number_is_parsed(self):
        self.assertEqual(MatchTNL.parse_terror_ids("50", "2", "0", "8 Pages"), [50])
        self.assertEqual(MatchTNL.parse_terror_ids("49", "23", "0",
                                                   "8 Pages (Alternate)"), [49])
        self.assertEqual(MatchTNL.parse_terror_ids("49", "23", "0",
                                                   "Double Trouble"), [49, 23])

    # ── 窓の動き ───────────────────────────
    def _monitor(self, keep, terrors=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", cancel_afk=True)
        monitor = LogMonitor.LogMonitor(cfg, keep, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "8 Pages"
        monitor.logs = []
        monitor.logger = monitor.logs.append
        if terrors is not None:
            p = patch.object(config, "TERRORS", terrors)
            p.start()
            self.addCleanup(p.stop)
        return monitor

    def _run(self, monitor, line="Killers have been set - 50 2 0 // Round type is 8 Pages"):
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(PlaySound, "play_sound") as play, \
             patch.object(Recorder, "on_continue_start"), \
             patch.object(ConnectDB, "register_round") as sent:
            monitor._process(line)
        started = [c.kwargs["target"].__func__.__name__
                   for c in thread.call_args_list if "target" in c.kwargs]
        return started, sent, play

    def test_the_list_id_decides_the_continue(self):
        monitor = self._monitor({self.PAGES: {135}})       # Whiteface

        started, sent, play = self._run(monitor)

        self.assertEqual(monitor.st.terror_ids, [135], "ログ番号 50 ではなく 135")
        self.assertNotIn("do_skip", started, "リストにあるので続行")
        play.assert_called_once_with("continue.mp3")
        self.assertEqual(sent.call_args.args[1], [135], "統計・DB も変換後")

    def test_the_log_number_alone_does_not_continue(self):
        monitor = self._monitor({self.PAGES: {50}})        # ログ番号を書いても当たらない

        started, _sent, _play = self._run(monitor)

        self.assertIn("do_skip", started)

    def test_the_second_number_is_dropped(self):
        monitor = self._monitor({self.PAGES: {2}})

        started, _sent, _play = self._run(monitor)

        self.assertEqual(monitor.st.terror_ids, [135])
        self.assertIn("do_skip", started, "B（2）だけがリストにあっても続行しない")

    def test_the_name_shown_is_the_real_terror(self):
        monitor = self._monitor({self.PAGES: {135}})

        self._run(monitor)

        self.assertTrue(any("Whiteface" in m for m in monitor.logs), monitor.logs)

    def test_an_unknown_number_is_logged_once_and_does_not_continue(self):
        # 44 は本物の表では登録済み。44 の無いテスト用の表で見る
        monitor = self._monitor({self.PAGES: {44, 135}},
                                terrors=eight_pages_terrors({50: 135}))

        started, sent, _play = self._run(
            monitor, "Killers have been set - 44 1 0 // Round type is 8 Pages")
        self._run(monitor, "Killers have been set - 44 1 0 // Round type is 8 Pages")

        self.assertEqual(monitor.st.terror_ids, [])
        self.assertIn("do_skip", started)
        warnings = [m for m in monitor.logs if "未登録の番号 44" in m]
        self.assertEqual(len(warnings), 1, monitor.logs)
        self.assertIn("terrors.json", warnings[0])

    def test_a_null_list_id_is_unknown_too(self):
        monitor = self._monitor({self.PAGES: {0}},
                                terrors=eight_pages_terrors({0: None, 50: 135}))

        started, _sent, _play = self._run(
            monitor, "Killers have been set - 0 8 0 // Round type is 8 Pages")

        self.assertEqual(monitor.st.terror_ids, [])
        self.assertIn("do_skip", started)
        self.assertTrue(any("未登録の番号 0" in m for m in monitor.logs), monitor.logs)

    def test_the_warning_comes_back_next_round(self):
        monitor = self._monitor({}, terrors=eight_pages_terrors({50: 135}))
        self._run(monitor, "Killers have been set - 44 1 0 // Round type is 8 Pages")

        monitor._process("This round is taking place at Facility (12) "
                         "and the round type is 8 Pages")
        self._run(monitor, "Killers have been set - 44 1 0 // Round type is 8 Pages")

        self.assertEqual(len([m for m in monitor.logs if "未登録の番号 44" in m]), 2)

    # ── DTM/Waldo の誤爆（ログ番号 50 は Don't Touch Me ではない） ──
    def test_the_log_number_is_not_taken_for_dtm(self):
        """放置モードは DTM/Waldo のラウンドだけ自爆しない。
        変換前は 8 Pages の 50（Whiteface）がその扱いになっていた"""
        self.assertIn(50, config.OPEN_SPECIAL_ROUND_TERROR_IDS, "前提: 50 は DTM")
        monitor = self._monitor({})
        self.assertIsNone(monitor._hands_free_skip_reason([50]), "前提: 50 なら自爆しない")

        self._run(monitor)

        self.assertEqual(monitor.st.terror_ids, [135])
        self.assertIsNotNone(monitor._hands_free_skip_reason(monitor.st.terror_ids),
                             "変換後は DTM 扱いにしない")

    def test_a_real_dtm_round_is_still_dtm(self):
        monitor = self._monitor({})
        monitor.st.round_type = "Classic"

        self._run(monitor, "Killers have been set - 50 0 0 // Round type is Classic")

        self.assertEqual(monitor.st.terror_ids, [50])
        self.assertIsNone(monitor._hands_free_skip_reason(monitor.st.terror_ids))

    # ── ほかのラウンドは今までどおり ────────────────
    def test_other_rounds_are_untouched(self):
        monitor = self._monitor({"Double Trouble/ダブルトラブル": {23}})
        monitor.st.round_type = "Double Trouble"

        started, _sent, _play = self._run(
            monitor, "Killers have been set - 49 23 0 // Round type is Double Trouble")

        self.assertEqual(monitor.st.terror_ids, [49, 23])
        self.assertNotIn("do_skip", started)

    def test_the_group_always_continue_still_wins(self):
        monitor = self._monitor({})
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        SharedState.set_list_source("host")

        started, _sent, play = self._run(
            monitor, "Killers have been set - 44 1 0 // Round type is 8 Pages")

        self.assertIn("8 Pages", GroupRound.ALWAYS_CONTINUE_ROUNDS)
        self.assertNotIn("do_skip", started, "未登録でもグループの全続行は効く")



# ═══════════════════════════════════════════════
#  RoundDecision.py
# ═══════════════════════════════════════════════
class TestRoundDecision(unittest.TestCase):
    def test_normalize_killer_ids_applies_alternate_and_unbound_offsets(self):
        self.assertEqual(RoundDecision.normalize_killer_ids([1], "Alternate"), [135])
        self.assertEqual(RoundDecision.normalize_killer_ids([1], "Classic", "Unbound"), [201])

    def test_decide_killers_uses_tnl_and_open_special_target(self):
        keep_on = {"Classic/クラシック": {42}}
        decision = RoundDecision.decide_killers(keep_on, [42], "Classic", 0, False)
        self.assertTrue(decision.is_continue_round)
        self.assertFalse(decision.is_open_special_round_target)

        target_id = next(iter(config.OPEN_SPECIAL_ROUND_TERROR_IDS))
        special = RoundDecision.decide_killers({}, [target_id], "Classic", 0, True)
        self.assertTrue(special.is_continue_round)
        self.assertTrue(special.is_open_special_round_target)




class TestGroupListStatePolled(unittest.TestCase):
    """主催リストの喪失/復帰は、ラウンドを待たずに監視ループで拾う"""

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
        SharedState.set_hands_free(False)
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_HOSHIIMO, voice="lost.mp3"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost=voice)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _tick(self, monitor):
        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._check_group_list_state()
        self.played = mock_play
        return mock_play

    def _lost(self, monitor):
        return [m for m in monitor.logs if "主催リストが取れません" in m]

    def _back(self, monitor):
        return [m for m in monitor.logs if "主催リストが戻りました" in m]

    def test_a_lost_list_is_announced_without_a_round(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)
        self.played.assert_called_once_with("lost.mp3")

    def test_ticking_again_stays_quiet(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        for _ in range(5):
            self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)
        self.played.assert_not_called()

    def test_recovery_is_announced(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        SharedState.set_list_source("host")
        self._tick(monitor)

        self.assertEqual(len(self._back(monitor)), 1, monitor.logs)
        self.assertFalse(monitor.st.list_lost_notified)

    def test_ticking_after_recovery_stays_quiet(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)
        SharedState.set_list_source("host")
        self._tick(monitor)

        for _ in range(5):
            self._tick(monitor)

        self.assertEqual(len(self._back(monitor)), 1, monitor.logs)

    def test_losing_it_twice_announces_twice(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)
        SharedState.set_list_source("host")
        self._tick(monitor)
        SharedState.set_list_source("tnl")

        self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 2, monitor.logs)
        self.played.assert_called_once_with("lost.mp3")

    def test_a_solo_private_window_is_untouched(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor(config.INSTANCE_PRIVATE)
        monitor.st.local_user_id = "usr_me"
        monitor.st.players = {"usr_me"}
        monitor.st.players_known = True

        for _ in range(3):
            self._tick(monitor)

        self.assertEqual(monitor.logs, [])
        self.played.assert_not_called()
        self.assertFalse(monitor.st.list_lost_notified)

    def test_it_fires_mid_round_too(self):
        """ラウンド進行中に落ちてもその場で鳴らす"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]

        self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)

    def test_hands_free_is_silent_but_still_logs(self):
        SharedState.set_hands_free(True)
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        with patch.object(LogMonitor.LogMonitor, "_hands_free", return_value=True):
            self._tick(monitor)

        self.played.assert_not_called()
        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)

    # ── _on_killers 側との関係 ───────────────
    def test_on_killers_does_not_announce_twice(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor.st.in_round = True
            monitor.st.round_type = "Bloodbath"
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)
        mock_play.assert_not_called()

    def test_the_on_killers_guard_still_stops_the_round(self):
        """通知を早めても、手を止めているのは判定時点のガードのまま"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor.st.in_round = True
            monitor.st.round_type = "Bloodbath"
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        self.assertEqual([c.kwargs["target"].__func__.__name__
                          for c in mock_thread.call_args_list
                          if "target" in c.kwargs], [])
        self.assertFalse(monitor.st.is_continue_round)

    # ── ループが止まらないこと ─────────────────
    def test_the_loop_survives_a_failing_check(self):
        monitor = self._monitor()
        monitor._running = True
        monitor._stop_event = threading.Event()
        ticks = {"n": 0}

        def boom():
            ticks["n"] += 1
            if ticks["n"] >= 3:
                monitor._running = False
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "output_log.txt"
            log.write_text("", encoding="utf-8")
            monitor.cfg.log_path = log
            with patch.object(config, "LOG_POLL_INTERVAL", 0), \
                 patch.object(monitor, "_check_group_list_state", boom), \
                 patch.object(monitor, "_detect_instance_from_log"):
                monitor._run()

        self.assertGreaterEqual(ticks["n"], 3, "例外のたびにループが回り続けること")
        self.assertTrue(any("主催リストの確認に失敗" in m for m in monitor.logs),
                        monitor.logs)

    def test_the_loop_calls_it_every_tick(self):
        monitor = self._monitor()
        monitor._running = True
        monitor._stop_event = threading.Event()
        calls = {"n": 0}

        def count():
            calls["n"] += 1
            if calls["n"] >= 3:
                monitor._running = False

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "output_log.txt"
            log.write_text("", encoding="utf-8")
            monitor.cfg.log_path = log
            with patch.object(config, "LOG_POLL_INTERVAL", 0), \
                 patch.object(monitor, "_check_group_list_state", count), \
                 patch.object(monitor, "_detect_instance_from_log"):
                monitor._run()

        self.assertEqual(calls["n"], 3)




class TestThreeWinsRestored(unittest.TestCase):
    """3クラ（DTM/Waldo 続行）の勝利数を、今のインスタンスに入ってからのログで取り戻す。

    どのラウンドでも生き残ったら1勝。Twilight 以外の特殊ラウンドが1回でもあるか、
    Twilight が2回以上なら3勝扱い。入り直すと0に戻る
    """

    ME = "usr_0e01408a"
    PREFIX = "2026.09.30 13:00:00 Debug      -  "
    OLD_JOIN = "[Behaviour] Joining wrld_old:1~private(usr_me)~region(jp)"
    JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"
    LIVED = "Lived in round."

    @staticmethod
    def _round(round_type):
        return f"This round is taking place at Facility (12) and the round type is {round_type}"

    def _write(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("\n".join(self.PREFIX + line for line in lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def _monitor(self, path=None, cancel_afk=True):
        cfg = WindowConfig(log_path=path, cancel_afk=cancel_afk)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _scan(self, after_join, before_join=(), cancel_afk=True, end=None):
        path = self._write([f"User Authenticated: serim01 ({self.ME})", *before_join,
                            self.JOIN, *after_join])
        monitor = self._monitor(path, cancel_afk)
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None):
            monitor._detect_instance_from_log(end=end)
        return monitor

    def _said(self, monitor):
        return [m for m in monitor.logs if "3クラ" in m or "3勝" in m or "生存数" in m]

    # ── 遡り ─────────────────────────────────
    def test_two_lives_and_no_special_round_is_two(self):
        monitor = self._scan([self._round("Classic"), self.LIVED,
                              self._round("Run"), self.LIVED, self._round("Classic")])

        self.assertEqual(monitor.st.open_special_round_wins, 2)
        self.assertEqual(self._said(monitor),
                         ["[窓1] 3クラ: 入室後のログから 生存2回・特殊ラウンドなし → 2/3"])

    def test_four_lives_stop_at_three(self):
        monitor = self._scan([self._round("Classic"), self.LIVED] * 4)
        self.assertEqual(monitor.st.open_special_round_wins, 3)

    def test_one_special_round_is_three(self):
        monitor = self._scan([self._round("Classic"), self._round("Fog")])

        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(self._said(monitor), ["[窓1] 3クラ: 入室後に Fog → 3勝扱い"])

    def test_one_twilight_keeps_the_lives(self):
        monitor = self._scan([self._round("Twilight"), self.LIVED])

        self.assertEqual(monitor.st.open_special_round_wins, 1)
        self.assertEqual(monitor.st.twilight_count, 1)
        self.assertEqual(self._said(monitor),
                         ["[窓1] 3クラ: 入室後のログから 生存1回・Twilight 1回 → 1/3"])

    def test_two_twilights_are_three(self):
        monitor = self._scan([self._round("Twilight"), self._round("Classic"),
                              self._round("Twilight")])

        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(monitor.st.twilight_count, 2)
        self.assertEqual(self._said(monitor), ["[窓1] 3クラ: 入室後に Twilight 2回 → 3勝扱い"])

    def test_nothing_before_the_last_join_counts(self):
        """入り直すと0に戻る。前のインスタンスの生存・特殊ラウンドは数えない"""
        monitor = self._scan([self._round("Classic")],
                             before_join=[self.OLD_JOIN, self._round("Fog"), self.LIVED,
                                          self.LIVED, self._round("Twilight")])

        self.assertEqual(monitor.st.open_special_round_wins, 0)
        self.assertEqual(monitor.st.twilight_count, 0)

    def test_a_log_without_a_join_leaves_zero(self):
        path = self._write([f"User Authenticated: serim01 ({self.ME})",
                            self._round("Fog"), self.LIVED])
        monitor = self._monitor(path)
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None):
            monitor._detect_instance_from_log()

        self.assertEqual(monitor.st.open_special_round_wins, 0)
        self.assertEqual(self._said(monitor), [])

    def test_the_scan_says_nothing_when_the_continue_is_off(self):
        monitor = self._scan([self._round("Classic"), self.LIVED], cancel_afk=False)

        self.assertEqual(monitor.st.open_special_round_wins, 1, "数えはする")
        self.assertEqual(self._said(monitor), [])

    def test_the_new_marks_are_part_of_their_regexes(self):
        self.assertIn(LogParser.ROUND_START_MARK, LogParser.RE_ROUND_START.pattern)
        self.assertIn(LogParser.LIVED_MARK, LogParser.RE_LIVED.pattern)
        self.assertIn(LogParser.ROUND_START_MARK, LogMonitor.LogMonitor._START_SCAN_MARKS)
        self.assertIn(LogParser.LIVED_MARK, LogMonitor.LogMonitor._START_SCAN_MARKS)

    # ── 遡りと監視で二重に数えない ─────────────────────
    def test_lines_after_the_start_position_are_left_to_the_watch(self):
        """監視はファイルの末尾（log_pos）から読む。遡りはそこまでしか数えない"""
        path = self._write([f"User Authenticated: serim01 ({self.ME})", self.JOIN,
                            self._round("Classic"), self.LIVED])
        start = path.stat().st_size
        with open(path, "a", encoding="utf-8") as f:       # 遡りの間に追記された
            f.write(self.PREFIX + self._round("Classic") + "\n" + self.PREFIX + self.LIVED + "\n")
        monitor = self._monitor(path)
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None):
            monitor._detect_instance_from_log(end=start)
        self.assertEqual(monitor.st.open_special_round_wins, 1)

        monitor._process(self.PREFIX + self.LIVED)          # 監視が読む追記分

        self.assertEqual(monitor.st.open_special_round_wins, 2, "追記分は1回だけ数える")

    def test_the_watch_passes_its_start_position_to_the_scan(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _run(self):"):]
        body = body[:body.index("\n    def ")]
        self.assertIn("self.st.log_pos = cfg.log_path.stat().st_size", body)
        self.assertIn("self._detect_instance_from_log(end=self.st.log_pos)", body)
        self.assertLess(body.index("self.st.log_pos = cfg.log_path.stat().st_size"),
                        body.index("self._detect_instance_from_log(end=self.st.log_pos)"))

    # ── 監視中 ────────────────────────────────
    def _live(self, monitor, *lines):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            for line in lines:
                monitor._process(self.PREFIX + line)

    def _twilight_round(self, monitor, killers_set_times=1):
        monitor.st.round_seq += 1
        monitor.st.round_type = "Twilight"
        for _ in range(killers_set_times):
            self._live(monitor, "Killers have been set - 1 0 0 // Round type is Twilight")

    def test_a_life_in_any_round_counts(self):
        monitor = self._monitor()
        monitor.st.is_open_special_round_round = False       # DTM/Waldo のラウンドではない

        self._live(monitor, self.LIVED)

        self.assertEqual(monitor.st.open_special_round_wins, 1)
        self.assertIn("[窓1] 生存数: 1/3", monitor.logs)
        self.assertFalse(monitor.st.is_open_special_round_round)

    def test_lives_stop_at_three_and_celebrate_once(self):
        monitor = self._monitor()

        self._live(monitor, self.LIVED, self.LIVED, self.LIVED, self.LIVED)

        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(sum("🎉 3勝達成" in m for m in monitor.logs), 1)
        self.assertEqual(sum("生存数" in m for m in monitor.logs), 3)

    def test_the_second_twilight_is_three(self):
        monitor = self._monitor()

        self._twilight_round(monitor)
        self.assertEqual(monitor.st.open_special_round_wins, 0, "1回目は数えない")
        self.assertEqual(self._said(monitor), [])
        self._twilight_round(monitor)

        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(self._said(monitor), [
            "[窓1] 特殊ラウンド（Twilight 2回目）を経験したので3勝扱い → 以降のDTM/Waldoはスキップします"])

    def test_a_twilight_from_the_log_plus_one_while_watching_is_three(self):
        monitor = self._scan([self._round("Twilight")])
        self.assertEqual(monitor.st.open_special_round_wins, 0)

        self._twilight_round(monitor)

        self.assertEqual(monitor.st.open_special_round_wins, 3)

    def test_two_killers_set_lines_in_one_round_are_one_twilight(self):
        monitor = self._monitor()

        self._twilight_round(monitor, killers_set_times=2)

        self.assertEqual(monitor.st.twilight_count, 1)
        self.assertEqual(monitor.st.open_special_round_wins, 0)

    def test_other_special_rounds_still_count_at_once(self):
        monitor = self._monitor()
        monitor.st.round_type = "Fog"
        self._live(monitor, "Killers have been set - 1 0 0 // Round type is Fog")
        self.assertEqual(monitor.st.open_special_round_wins, 3)

    def test_a_new_instance_starts_from_zero(self):
        monitor = self._monitor()
        self._live(monitor, self.LIVED, self.LIVED)
        monitor.st.twilight_count = 1

        self._live(monitor, "[Behaviour] Joining wrld_next:3~private(usr_me)~region(jp)")

        self.assertEqual(monitor.st.open_special_round_wins, 0)
        self.assertEqual(monitor.st.twilight_count, 0)
        self.assertIn("[窓1] 3クラ: 別のインスタンスに入ったので 0/3 に戻します", monitor.logs)

    def test_a_new_instance_from_zero_says_nothing(self):
        monitor = self._monitor()
        self._live(monitor, "[Behaviour] Joining wrld_next:3~private(usr_me)~region(jp)")
        self.assertFalse(any("別のインスタンス" in m for m in monitor.logs))

    def test_the_watch_says_nothing_when_the_continue_is_off(self):
        monitor = self._monitor(cancel_afk=False)

        self._live(monitor, self.LIVED, self.LIVED, self.LIVED)
        self._twilight_round(monitor)
        self._live(monitor, "[Behaviour] Joining wrld_next:3~private(usr_me)~region(jp)")

        self.assertEqual(self._said(monitor), [])




class TestSabotageStarAnnounces(unittest.TestCase):
    """Star側の続行希望は「誰かが欲しがっている」。アナウンスとフリーズを出す"""

    STAR = GroupRound.SABOTAGE_STAR_KEY
    MURDER = GroupRound.SABOTAGE_MURDER_KEY

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
        SharedState.set_hands_free(False)
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_HOSHIIMO, wishes=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(
            cfg, {}, lambda _m: None, window_idx=1,
            host_wishes=wishes if wishes is not None
            else {"みているひと": {self.STAR: {5}}})
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Sabotage"
        monitor.st.sus_players = ["ソノア7"]
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _apply(self, monitor, round_type=None):
        monitor.st.terror_ids = [5]
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            handled = monitor._apply_group_decision(
                round_type or monitor.st.round_type)
        self.played = mock_play
        return handled

    # ── WANTED の処理 ─────────────────────────
    def test_it_announces_once(self):
        monitor = self._monitor()

        self.assertTrue(self._apply(monitor))

        self.played.assert_called_once_with("continue.mp3")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_it_logs_as_play(self):
        monitor = self._monitor()

        self._apply(monitor)

        self.assertTrue(any("【プレイ】" in m for m in monitor.logs), monitor.logs)
        self.assertTrue(any("続行アナウンス再生" in m for m in monitor.logs))

    def test_twice_in_a_round_freezes_once(self):
        """同じラウンドで _on_killers が複数回来ても二重にフリーズしない"""
        monitor = self._monitor()

        self._apply(monitor)
        first = self.played.call_count
        self._apply(monitor)

        self.assertEqual(first, 1)
        self.played.assert_not_called()
        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_hands_free_is_silent(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(config.INSTANCE_PRIVATE)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.assertTrue(monitor._hands_free())
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO

        # 放置モードが効く窓を装って、鳴らさない側の分岐を通す
        with patch.object(LogMonitor.LogMonitor, "_hands_free",
                          return_value=True):
            self._apply(monitor)

        self.played.assert_not_called()
        self.assertTrue(monitor.st.is_continue_round, "続行状態自体は立てる")

    def test_both_group_types_behave_the_same(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype)

            self._apply(monitor)

            self.assertTrue(monitor.st.is_continue_round, itype)
            self.played.assert_called_once_with("continue.mp3")

    # ── CONTINUE は従来どおり無音 ───────────────
    def test_plain_continue_stays_silent(self):
        monitor = self._monitor()
        monitor.st.round_type = "8 Pages"

        self.assertTrue(self._apply(monitor, "8 Pages"))

        self.played.assert_not_called()
        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_hoshiimo_fog_stays_silent(self):
        monitor = self._monitor()
        monitor.st.round_type = "Fog"

        self._apply(monitor, "Fog")

        self.played.assert_not_called()
        self.assertFalse(monitor.st.is_continue_round)

    def test_yakiimo_fog_still_goes_to_the_normal_judgement(self):
        monitor = self._monitor(config.INSTANCE_YAKIIMO)
        monitor.st.round_type = "Fog"

        handled = self._apply(monitor, "Fog (Alternate)")

        self.assertFalse(handled, "NORMAL のまま（通常判定で鳴る）")

    # ── 後始末 ───────────────────────────────
    def _next_round(self, monitor, round_type, ids, wishes=None):
        monitor.st.round_type = round_type
        monitor.st.terror_ids = list(ids)
        if wishes is not None:
            monitor.host_wishes = wishes
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            return monitor._apply_group_decision(round_type)

    def test_a_following_skip_releases_the_freeze(self):
        monitor = self._monitor()
        self._apply(monitor)

        self._next_round(monitor, "Bloodbath", [1, 2, 3])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_a_following_normal_round_releases_the_freeze(self):
        monitor = self._monitor()
        self._apply(monitor)

        monitor.st.round_type = "Midnight"
        monitor.st.terror_ids = [99]
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._decide_with_keep_on_set("Midnight")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_a_following_plain_continue_releases_the_freeze(self):
        """張りっぱなしになると全窓が止まる。ここがいちばん危ない"""
        monitor = self._monitor()
        self._apply(monitor)
        self.assertEqual(SharedState.get_continue_round_count(), 1)

        self._next_round(monitor, "8 Pages", [1, 2])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_round_start_also_releases_it(self):
        """通常はこちらで落ちる（ラウンドの切れ目）"""
        monitor = self._monitor()
        self._apply(monitor)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is 8 Pages")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)




class TestContinueRoundsByType(unittest.TestCase):
    """privateの「全続行するラウンド」。自爆もせず通常判定にも落とさない"""

    CLASSIC_KEY = "Classic/クラシック"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, continue_rounds=("Classic",), skip_rounds=(),
                 do_skip=True, keep_on=None,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds),
                           continue_rounds=set(continue_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._running = True
        return monitor

    def _killers(self, monitor, ids=(99,)):
        """置き換え待ちに入ったら、合図が来ないまま待ち明けたものとして進める"""
        round_type = monitor.st.round_type
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers(list(ids), round_type, revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]
            if "_delayed_decision" in started:
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += [c.kwargs["target"].__func__.__name__
                            for c in mock_thread.call_args_list
                            if "target" in c.kwargs]
        self.played = mock_play
        return started

    def test_a_listed_round_does_not_self_destruct(self):
        started = self._killers(self._monitor())

        self.assertEqual(started, [])

    def test_nothing_else_happens_either(self):
        monitor = self._monitor()

        self._killers(monitor)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.played.assert_not_called()

    def test_it_does_not_fall_through_to_the_keep_list(self):
        """続行リストにテラーが無くても自爆しない"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {1}})

        started = self._killers(monitor, [99])

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.is_continue_round)

    def test_a_keep_listed_terror_still_announces_nothing(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})

        self._killers(monitor, [99])

        self.assertFalse(monitor.st.is_continue_round)
        self.played.assert_not_called()

    def test_continue_beats_skip_when_both_are_set(self):
        """settings.json を手編集された場合の保険。自爆しない側に倒す"""
        monitor = self._monitor(continue_rounds=("Classic",),
                                skip_rounds=("Classic",))

        started = self._killers(monitor)

        self.assertEqual(started, [])

    def test_no_variant_wait_for_a_listed_round(self):
        """先に return するので Variant 待ちにも入らない"""
        monitor = self._monitor(continue_rounds=("Classic",),
                                skip_rounds=("Classic",),
                                keep_on={self.CLASSIC_KEY: {config.GIGABYTES_ID}})

        started = self._killers(monitor)

        self.assertNotIn("_delayed_decision", started)

    def test_an_unlisted_round_uses_the_normal_judgement(self):
        monitor = self._monitor(continue_rounds=("Bloodbath",),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_auto_skip_off_changes_nothing(self):
        monitor = self._monitor(do_skip=False)

        self.assertEqual(self._killers(monitor), [])

    def test_a_stale_continue_round_is_cleared(self):
        monitor = self._monitor()
        monitor.st.is_continue_round = True
        SharedState.continue_round_start(monitor.st)

        self._killers(monitor)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_group_instances_are_untouched(self):
        """干し芋/焼き芋は GroupRound の結論が先に出る"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._monitor(continue_rounds=("Bloodbath",),
                                    instance_type=itype)
            monitor.st.round_type = "Bloodbath"

            started = self._killers(monitor, [1, 2, 3])

            self.assertIn("do_skip", started, itype)   # 問答無用スキップのまま

    def test_public_is_untouched(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP):
            monitor = self._monitor(instance_type=itype)

            self.assertEqual(self._killers(monitor), [], itype)

    def test_nothing_selected_keeps_the_old_behaviour(self):
        monitor = self._monitor(continue_rounds=(),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_the_window_config_defaults_to_nothing_selected(self):
        self.assertEqual(WindowConfig().continue_rounds, set())




class TestRoundListsAreExclusive(unittest.TestCase):
    """同じラウンドを両方に入れられないこと"""

    class Var:
        def __init__(self, value=False):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def test_checking_one_clears_the_other(self):
        skip, keep = self.Var(True), self.Var(True)

        mainGUI.exclusive_check(skip, keep)

        self.assertFalse(keep.get())
        self.assertTrue(skip.get())

    def test_unchecking_leaves_the_other_alone(self):
        skip, keep = self.Var(False), self.Var(True)

        mainGUI.exclusive_check(skip, keep)

        self.assertTrue(keep.get(), "外したときは相手を触らない")

    def test_it_works_in_both_directions(self):
        skip, keep = self.Var(True), self.Var(False)

        keep.set(True)
        mainGUI.exclusive_check(keep, skip)

        self.assertFalse(skip.get())

    def test_both_lists_use_the_same_round_order(self):
        self.assertEqual(list(mainGUI.skip_round_vars(lambda: object())),
                         config.SKIP_ROUND_SELECTABLE)




class TestSkipRoundsSettings(unittest.TestCase):
    """窓ごとのラウンド指定。**保存も復元もしない**——自爆設定の持ち越しは危ない"""

    class FakeVar:
        def __init__(self, value=False):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def _tab(self, names=(), exempt=False, keep=()):
        tab = type("FakeTab", (), {})()
        tab.v_profile = TestSkipRoundsSettings.FakeVar(0)
        tab.v_skip_rounds = {n: TestSkipRoundsSettings.FakeVar(n in names)
                             for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_continue_rounds = {n: TestSkipRoundsSettings.FakeVar(n in keep)
                                 for n in config.SKIP_ROUND_SELECTABLE}
        return tab

    def test_the_selectable_list_drives_the_variables(self):
        made = mainGUI.skip_round_vars(lambda: object())

        self.assertEqual(list(made), config.SKIP_ROUND_SELECTABLE)

    def _save(self, tabs, stored=None):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = tabs
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_freeze_8pages", "v_freeze_punish"):
            setattr(app, name, TestSkipRoundsSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestSkipRoundsSettings.FakeVar(""))
        app.tool_rows = []
        app._win_count_pref = None
        app.v_emergency_key = TestSkipRoundsSettings.FakeVar("p")
        app.v_start_key = TestSkipRoundsSettings.FakeVar("")
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings",
                          return_value=dict(stored or {})):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)
        return saved

    # ── 保存しない ───────────────────────────
    def test_the_round_lists_are_not_saved(self):
        saved = self._save([self._tab(("Classic", "Fog"), exempt=True,
                                      keep=("Run",)), self._tab()])

        for key in ("skip_rounds", "skip_variant_exempt", "continue_rounds"):
            self.assertNotIn(key, saved, key)

    def test_the_other_window_settings_are_still_saved(self):
        """巻き込んでいないこと"""
        saved = self._save([self._tab(), self._tab()])

        self.assertEqual(saved["profiles"], [0, 0])
        self.assertEqual(saved["tool_launchers"], [])
        self.assertEqual(saved["emergency_stop_key"], "p")

    def test_old_values_are_removed_from_the_file(self):
        """書かないだけでは足りない——マージするので前回の値が残り続ける"""
        saved = self._save([self._tab()], stored={
            "skip_rounds": [["Classic"]],
            "skip_variant_exempt": [True],
            "continue_rounds": [["Fog"]],
            "tnl_path": "C:/list/my.tnl",
        })

        for key in ("skip_rounds", "skip_variant_exempt", "continue_rounds"):
            self.assertNotIn(key, saved, key)
        self.assertEqual(saved["tnl_path"], "C:/list/my.tnl", "他は消さないこと")

    # ── 復元しない ───────────────────────────
    def test_the_round_lists_are_not_restored(self):
        """古い settings.json に値が残っていても、チェックは付かない"""
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = [self._tab(), self._tab()]
        app._saved_profiles = []
        app._saved_skip_rounds = [["Classic", "Fog"], []]
        app._saved_skip_variant_exempt = [True, False]
        app._saved_continue_rounds = [["Run"], []]

        mainGUI.App._apply_saved_window_settings(app)

        for tab in app.tabs:
            self.assertEqual(
                {n for n, v in tab.v_skip_rounds.items() if v.get()}, set())
            self.assertEqual(
                {n for n, v in tab.v_continue_rounds.items() if v.get()}, set())

    def test_the_profiles_are_still_restored(self):
        """巻き込んでいないこと"""
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = [self._tab(), self._tab()]
        app._saved_profiles = [3, 5]

        mainGUI.App._apply_saved_window_settings(app)

        self.assertEqual([tab.v_profile.get() for tab in app.tabs], [3, 5])

    def test_the_window_config_defaults_to_nothing_selected(self):
        cfg = WindowConfig()

        self.assertEqual(cfg.skip_rounds, set())

    def test_a_started_window_begins_with_nothing_selected(self):
        """起動直後のタブから作った設定にも何も入らない"""
        cfg = mainGUI.LogMonitor.WindowConfig(
            skip_rounds={n for n, v in self._tab().v_skip_rounds.items()
                         if v.get()},
            continue_rounds={n for n, v in self._tab().v_continue_rounds.items()
                             if v.get()})

        self.assertEqual(cfg.skip_rounds, set())
        self.assertEqual(cfg.continue_rounds, set())




class TestRoundSettingsAreNotLoaded(unittest.TestCase):
    """起動時、ラウンド指定3種が未チェックで始まること。

    _load_saved_settings を丸ごと回す——「復元しない」ことの検証なので、
    途中を差し替えると意味が無い。
    """

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def _tab(self):
        tab = type("FakeTab", (), {})()
        tab.v_profile = TestRoundSettingsAreNotLoaded.FakeVar(0)
        tab.v_skip_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                             for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_continue_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                                 for n in config.SKIP_ROUND_SELECTABLE}
        return tab

    def _load(self, data):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = [self._tab(), self._tab()]
        for name in ("v_desktop_mode", "v_use_osc",
                     "v_ton_entry", "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_instance_link", "v_emergency_key", "v_start_key", "v_freeze_8pages",
                     "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, TestRoundSettingsAreNotLoaded.FakeVar(""))
        app._apply_obs_settings = lambda: None
        app.v_freeze_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                               for n in config.SKIP_ROUND_SELECTABLE}
        app.added_tools = []
        app._add_tool_row = lambda p, save=True: app.added_tools.append(p)
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app._apply_saved_window_settings = \
            lambda: mainGUI.App._apply_saved_window_settings(app)

        with patch.object(mainGUI, "load_settings", return_value=data), \
             patch.object(HotKey, "is_valid", return_value=True):
            app._load_window_volume_settings = lambda _data: None
            app._load_fog_early_read_setting = lambda _data: None
            app._load_launch_options_setting = lambda _data: None
            mainGUI.App._load_saved_settings(app)
        return app

    FULL = {
        "skip_rounds": [["Classic", "Fog"], ["Run"]],
        "skip_variant_exempt": [True, True],
        "continue_rounds": [["8 Pages"], ["Midnight"]],
        "profiles": [2, 7],
        "emergency_stop_key": "f9",
        "tool_launchers": ["D:/tools/one.exe"],
        "freeze_8pages": True,
        "freeze_punish": True,
        "freeze_rounds": ["Classic"],
    }

    def test_skip_rounds_start_unchecked(self):
        app = self._load(dict(self.FULL))

        for tab in app.tabs:
            self.assertEqual(
                {n for n, v in tab.v_skip_rounds.items() if v.get()}, set())

    def test_an_old_variant_switch_in_the_file_is_ignored(self):
        """チェックボックスは廃止した。古いファイルに残っていても読まない"""
        app = self._load(dict(self.FULL))

        for tab in app.tabs:
            self.assertFalse(hasattr(tab, "v_skip_variant_exempt"))

    def test_continue_rounds_start_unchecked(self):
        app = self._load(dict(self.FULL))

        for tab in app.tabs:
            self.assertEqual(
                {n for n, v in tab.v_continue_rounds.items() if v.get()}, set())

    def test_the_profiles_are_restored(self):
        app = self._load(dict(self.FULL))

        self.assertEqual([tab.v_profile.get() for tab in app.tabs], [2, 7])

    def test_the_other_settings_are_restored(self):
        """freeze_* / emergency_stop_key / tool_launchers は従来どおり"""
        app = self._load(dict(self.FULL))

        self.assertEqual(app.v_emergency_key.get(), "f9")
        self.assertEqual(app.added_tools, ["D:/tools/one.exe"])
        self.assertTrue(app.v_freeze_8pages.get())
        self.assertTrue(app.v_freeze_punish.get())
        self.assertTrue(app.v_freeze_rounds["Classic"].get())




class TestRoundSettingsClearedOnInstanceChange(unittest.TestCase):
    """インスタンスが変わったらラウンド指定を解除する。

    監視が見る側（WindowConfig）と、利用者が見る側（GUIのチェック）の
    **両方**。片方だけだと表示と動きが食い違う。
    """

    JOIN = ("2026.09.15 10:00:00 Debug      -  [Behaviour] "
            "Joining wrld_1234:5678~private(usr_x)")

    def _monitor(self, skip=("Classic",), keep=(), callback="none"):
        cfg = WindowConfig(do_skip=True, skip_rounds=set(skip),
                           continue_rounds=set(keep))
        kwargs = {} if callback == "none" else \
            {"on_round_settings_cleared": callback}
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None,
                                        window_idx=1, **kwargs)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _join(self, monitor):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.JOIN)

    # ── 監視が見る側 ──────────────────────────
    def test_the_config_is_cleared(self):
        monitor = self._monitor(skip=("Classic", "Fog"), keep=("Run",))

        self._join(monitor)

        self.assertEqual(monitor.cfg.skip_rounds, set())
        self.assertEqual(monitor.cfg.continue_rounds, set())

    def test_it_stops_skipping_that_round(self):
        monitor = self._monitor(skip=("Classic",))
        monitor.st.round_type = "Classic"
        self.assertTrue(monitor._should_skip_by_round(), "前提")

        self._join(monitor)

        self.assertFalse(monitor._should_skip_by_round())

    def test_the_master_switch_is_left_alone(self):
        """「自動自爆」は従来どおり"""
        monitor = self._monitor()

        self._join(monitor)

        self.assertTrue(monitor.cfg.do_skip)

    def test_it_is_logged(self):
        monitor = self._monitor()

        self._join(monitor)

        self.assertTrue(any("ラウンド指定を解除" in m for m in monitor.logs),
                        monitor.logs)

    def test_nothing_selected_logs_nothing(self):
        """毎回の入室で流れると邪魔になる"""
        monitor = self._monitor(skip=())

        self._join(monitor)

        self.assertFalse(any("ラウンド指定を解除" in m for m in monitor.logs),
                         monitor.logs)

    # ── 利用者が見る側 ─────────────────────────
    def test_the_callback_gets_the_window_number(self):
        got = []
        monitor = self._monitor(callback=got.append)

        self._join(monitor)

        self.assertEqual(got, [1])

    def test_the_callback_is_called_even_with_nothing_selected(self):
        """監視開始の後でチェックを付けた分は cfg に入っていない"""
        got = []
        monitor = self._monitor(skip=(), callback=got.append)

        self._join(monitor)

        self.assertEqual(got, [1])

    def test_no_callback_still_works(self):
        monitor = self._monitor()

        self._join(monitor)      # 既定は None。落ちないこと

        self.assertEqual(monitor.cfg.skip_rounds, set())




class TestLogMonitorGroupRules(unittest.TestCase):
    """干し芋/焼き芋のラウンド判定を LogMonitor 越しに見る（第1部の統合側）

    「全続行」= 自爆しないだけ。続行アナウンスも他窓フリーズもしない。
    「通常判定」= 続行リストに無ければ自爆する。
    """

    DT_KEY = "Double Trouble/ダブルトラブル"
    FOG_KEY = "Fog/霧"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
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

    def _monitor(self, *, do_skip=True, keep_on=None,
                 instance_type=config.INSTANCE_HOSHIIMO, host_wishes=None):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None,
                                        window_idx=1, host_wishes=host_wishes)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        return monitor

    def _killers(self, monitor, ids, killers_round_type=None, revealed=False,
                 settle=False):
        """_on_killers を回して、起動したスレッドの target 名を返す。

        settle: 置き換え待ちに入ったら、合図が来ないまま待ち明けたものとして進める
        """
        round_type = killers_round_type or monitor.st.round_type
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type, revealed=revealed)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]
            if settle and "_delayed_decision" in started:
                monitor._running = True
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += [c.kwargs["target"].__func__.__name__
                            for c in mock_thread.call_args_list
                            if "target" in c.kwargs]
        return started

    def _round(self, monitor, round_type, ids=(99,), **kw):
        monitor.st.round_type = round_type
        return self._killers(monitor, ids, **kw)

    # ── 問答無用スキップ ──────────────────────
    def _classic_monitor(self, tid, keep_on=None,
                         instance_type=config.INSTANCE_HOSHIIMO):
        """テラーが確定した Classic ラウンド。

        Classicの1体構成は Gigabytes 待ちに入りうるので、判定は待ち明け
        （`_delayed_decision`）で出る。そこを直接動かして確かめる。
        """
        monitor = self._monitor(instance_type=instance_type, keep_on=keep_on)
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [tid]
        return monitor

    def test_classic_without_a_variant_is_skipped(self):
        """続行リストに載っていても問答無用でスキップする"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._classic_monitor(
                99, keep_on={"Classic/クラシック": {99}}, instance_type=itype)

            started = self._run_delayed(monitor)

            self.assertIn("do_skip", started, itype)
            self.assertFalse(monitor.st.is_continue_round, itype)

    def test_classic_with_each_variant_uses_the_keep_list(self):
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
                    config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID):
            monitor = self._classic_monitor(tid, keep_on={"Classic/クラシック": {tid}})

            started = self._run_delayed(monitor)

            self.assertTrue(monitor.st.is_continue_round, tid)
            self.assertNotIn("do_skip", started, tid)

    def test_classic_with_a_variant_still_skips_when_not_wanted(self):
        monitor = self._classic_monitor(config.ATRACHED_ID)

        started = self._run_delayed(monitor)

        self.assertIn("do_skip", started)

    def test_bloodbath_is_skipped(self):
        monitor = self._monitor()

        started = self._round(monitor, "Bloodbath", [1, 2, 3])

        self.assertIn("do_skip", started)

    def test_classic_exe_and_randomizer_are_skipped_even_with_a_variant(self):
        for round_type in ("Classic.exe", "Randomizer"):
            monitor = self._monitor()
            monitor.st.bloodthirsty_creature_variant = True

            started = self._round(monitor, round_type,
                                  [config.BLOODTHIRSTY_CREATURE_ID])

            self.assertIn("do_skip", started, round_type)

    # ── 全続行（自爆しないだけ） ───────────────
    def test_always_continue_rounds_do_nothing(self):
        for round_type in ("8 Pages", "Run", "Bloodbath EX"):
            monitor = self._monitor()

            with patch.object(PlaySound, "play_sound") as mock_play:
                started = self._round(monitor, round_type, [1, 2])

            self.assertEqual(started, [], round_type)
            self.assertFalse(monitor.st.is_continue_round, round_type)
            mock_play.assert_not_called()

    def test_always_continue_does_not_freeze_other_windows(self):
        monitor = self._monitor()

        self._round(monitor, "8 Pages", [1, 2])

        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_hoshiimo_fog_always_continues(self):
        monitor = self._monitor()

        started = self._round(monitor, "Fog", [7], killers_round_type="Fog",
                              revealed=True)

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.is_continue_round)

    def test_run_keeps_its_existing_round_start_behaviour(self):
        """「死亡待ち・アイテム購入予定」の既存挙動は変えない"""
        logs = []
        cfg = WindowConfig(do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        monitor.st.is_continue_round = True

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Run")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertTrue(any("死亡待ち・アイテム購入予定" in m for m in logs), logs)

    # ── moon ────────────────────────────────
    def test_a_repeated_moon_is_skipped(self):
        monitor = self._monitor()
        monitor.st.moon_repeat = True

        started = self._round(monitor, "Blood Moon", [7])

        self.assertIn("do_skip", started)

    def test_hoshiimo_plays_every_first_moon(self):
        for moon in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice"):
            monitor = self._monitor()

            started = self._round(monitor, moon, [7])

            self.assertEqual(started, [], moon)

    def test_yakiimo_skips_the_first_mystic_moon_and_solstice(self):
        for moon in ("Mystic Moon", "Solstice"):
            monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)

            started = self._round(monitor, moon, [7])

            self.assertIn("do_skip", started, moon)

    def test_yakiimo_plays_the_first_blood_moon_and_twilight(self):
        for moon in ("Blood Moon", "Twilight"):
            monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)

            started = self._round(monitor, moon, [7])

            self.assertEqual(started, [], moon)

    # ── 焼き芋 Fog ──────────────────────────
    def test_yakiimo_fog_revealed_as_alternate_uses_the_keep_list(self):
        alternate = MatchTNL.ALTERNATE_OFFSET + 3
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO,
                                keep_on={self.FOG_KEY: {alternate}})
        monitor.st.round_type = "Fog"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers([3], "Fog (Alternate)", revealed=True)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(_decision_threads(mock_thread), [], "自爆しない")

    def test_yakiimo_fog_revealed_as_alternate_but_unwanted_is_skipped(self):
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)
        monitor.st.round_type = "Fog"

        started = self._killers(monitor, [3], "Fog (Alternate)", revealed=True)

        self.assertIn("do_skip", started)

    def test_yakiimo_fog_revealed_as_plain_fog_is_skipped(self):
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO,
                                keep_on={self.FOG_KEY: {7}})
        monitor.st.round_type = "Fog"

        started = self._killers(monitor, [7], "Fog", revealed=True)

        self.assertIn("do_skip", started)

    def test_foxy_in_fog_still_follows_the_fog_rule(self):
        """Foxy検出はオルタ枠を引数で伝える。st.round_type は Fog のまま"""
        monitor = self._monitor()      # 干し芋 → Fog は全続行
        monitor.st.round_type = "Fog"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("foxy the pirate turned evil!")

        self.assertEqual(monitor.st.round_type, "Fog",
                         "書き換えると「Fog」の自爆指定が効かなくなる")
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])
        self.assertEqual([c.kwargs["target"].__func__.__name__
                          for c in mock_thread.call_args_list
                          if "target" in c.kwargs], [],
                         "全続行のまま。自爆も通常判定も走らせないこと")

    def test_killers_unknown_decides_nothing(self):
        """Fogは68ラウンド中63で revealed が来ない。その間は何もしない"""
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Killers is unknown - ??? // x // Round type is Fog")

        mock_thread.assert_not_called()
        self.assertEqual(monitor.st.round_type, "Fog")

    # ── 通常判定（続行リスト照合） ──────────────
    def test_normal_judgement_continues_and_announces(self):
        monitor = self._monitor(keep_on={self.DT_KEY: {42}})

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor.st.round_type = "Double Trouble"
            monitor._on_killers([42], "Double Trouble", revealed=False)

        self.assertTrue(monitor.st.is_continue_round)
        mock_play.assert_called_once_with("continue.mp3")

    def test_normal_judgement_skips_when_not_wanted(self):
        """挙動が変わるところ: グループでも続行リストに無ければ自爆する"""
        monitor = self._monitor(keep_on={self.DT_KEY: {42}})

        started = self._round(monitor, "Double Trouble", [99])

        self.assertIn("do_skip", started)

    def test_auto_skip_off_never_skips(self):
        monitor = self._monitor(do_skip=False)

        started = self._round(monitor, "Double Trouble", [99])

        self.assertNotIn("do_skip", started)

    def test_auto_skip_off_also_blocks_the_group_skip(self):
        """cfg.do_skip は全体スイッチ。問答無用スキップもここで止まる"""
        monitor = self._monitor(do_skip=False)

        started = self._round(monitor, "Bloodbath", [1, 2, 3])

        self.assertNotIn("do_skip", started)

    def test_private_is_unaffected(self):
        """private では新ルールが一切効かない（Classicでも続行リスト次第）"""
        monitor = self._monitor(instance_type=config.INSTANCE_PRIVATE,
                                keep_on={"Classic/クラシック": {99}})

        # Classic の1体構成は Gigabytes が来ると結論が変わるので待つ
        started = self._round(monitor, "Classic", [99], settle=True)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_public_still_only_gets_the_voice(self):
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC)

        started = self._round(monitor, "Classic", [99])

        self.assertNotIn("do_skip", started)

    def test_hands_free_stays_private_only(self):
        """放置モードの自動操作は private 限定のまま"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(keep_on={self.DT_KEY: {42}})

        started = self._round(monitor, "Double Trouble", [42])

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round, "放置モードを通っていないこと")

    # ── Variant判定待ち ────────────────────
    def test_a_single_terror_classic_waits_when_gigabytes_is_wanted(self):
        """元IDが毎回違うので、1体構成はどれも Gigabytes の候補"""
        monitor = self._monitor(keep_on={"Classic/クラシック": {config.GIGABYTES_ID}})

        started = self._round(monitor, "Classic", [99])

        self.assertEqual(started, ["_delayed_decision"])

    def test_a_single_terror_classic_skips_at_once_when_nothing_is_wanted(self):
        """置き換わってもリストに無ければ、どちらにしても自爆。待たない"""
        monitor = self._monitor()

        started = self._round(monitor, "Classic", [99])

        self.assertEqual(started, ["do_skip"])

    def test_rounds_whose_rule_ignores_the_terror_do_not_wait(self):
        """Bloodbath は問答無用スキップ、8 Pages は全続行。置き換わっても同じ"""
        for round_type, ids, expected in (
                ("Bloodbath", [config.CURIOUS_CREATURE_ID, 2, 3], ["do_skip"]),
                ("8 Pages", [config.CURIOUS_CREATURE_ID, 2], [])):
            monitor = self._monitor(
                keep_on={"Bloodbath/ブラッドバス": {config.BLOODTHIRSTY_CREATURE_ID}})

            started = self._round(monitor, round_type, ids)

            self.assertEqual(started, expected, round_type)

    def test_a_normal_round_waits_when_the_variant_is_wanted(self):
        monitor = self._monitor(
            keep_on={self.DT_KEY: {config.BLOODTHIRSTY_CREATURE_ID}})

        started = self._round(monitor, "Double Trouble",
                              [config.CURIOUS_CREATURE_ID, 5])

        self.assertEqual(started, ["_delayed_decision"])

    def test_the_wait_still_reaches_the_same_answer(self):
        """待ち明けの結論は待たない場合と同じ（Bloodbathは問答無用スキップ）"""
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Bloodbath"
        monitor.st.terror_ids = [config.CURIOUS_CREATURE_ID, 2, 3]

        self.assertIn("do_skip", self._run_delayed(monitor, "Bloodbath"))

    def test_the_classic_wait_is_about_a_second(self):
        """実測ではVariantの出現ログは Killers行と同じ秒に出る"""
        monitor = self._monitor()
        monitor.st.round_type = "Classic"

        self.assertLessEqual(monitor._variant_wait_sec(), 1.0)

    def _run_delayed(self, monitor, killers_round_type="Classic", wait_sec=0.0):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_decision(killers_round_type, wait_sec,
                                      monitor.st.round_seq)
        return _decision_threads(mock_thread)

    def test_the_wait_length_is_one_value(self):
        """待つのは Classic だけになったので、ラウンド種別で変えない"""
        monitor = self._monitor()
        waits = []
        for round_type in ("Classic", "Bloodbath", "未知のラウンド"):
            monitor.st.round_type = round_type
            waits.append(monitor._variant_wait_sec())

        self.assertEqual(set(waits), {config.TERROR_VARIANT_WAIT_SEC})
        self.assertLessEqual(config.TERROR_VARIANT_WAIT_SEC, 1.0,
                             "出現ログは Killers行と同じ秒に出る")

    def test_the_wait_ends_in_a_skip_when_no_marker_arrives(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]

        self.assertIn("do_skip", self._run_delayed(monitor))

    def test_the_wait_is_cancelled_by_the_gigabytes_line(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]
        monitor.keepOn_set["Classic/クラシック"] = {config.GIGABYTES_ID}
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("The Gigabytes have come.")

        self.assertEqual(self._run_delayed(monitor), [],
                         "問答無用スキップではなく通常判定に回ること")
        self.assertEqual(monitor.st.terror_ids, [config.GIGABYTES_ID])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_is_cancelled_by_the_atrached_line(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.SONIC_ID]
        monitor.keepOn_set["Classic/クラシック"] = {config.ATRACHED_ID}
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("Lets play a game...")
        monitor.st.gigabytes = True     # Gigabytes待ちは別。ここでは切り離す

        self.assertEqual(self._run_delayed(monitor), [],
                         "問答無用スキップではなく通常判定に回ること")
        self.assertEqual(monitor.st.terror_ids, [config.ATRACHED_ID])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_aborts_when_the_round_changed(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]
        monitor.st.round_seq = 5

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision("Classic", 0.0, 4)

        mock_thread.assert_not_called()

    def test_the_wait_falls_through_to_the_normal_judgement(self):
        """Variantが確定したら通常判定へ回すこと（待ちの間に抜けている）"""
        monitor = self._monitor(keep_on={"Classic/クラシック": {config.ATRACHED_ID}})
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.ATRACHED_ID]
        monitor.st.atrached_variant = True
        monitor.st.gigabytes = True     # _run_delayed は _on_killers を通らないので
                                        # ここでは ids の差し替えは起きない

        started = self._run_delayed(monitor)

        self.assertEqual(started, [])
        self.assertTrue(monitor.st.is_continue_round)

    # ── 既存の取りこぼし対策 ──────────────────
    def test_round_start_clears_stale_continue_state(self):
        """グループルールとは独立した既存挙動。ラウンド開始で続行状態を落とす"""
        monitor = self._monitor()
        monitor.st.is_continue_round = True

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(monitor.st.round_type, "Classic")

    def test_group_skip_clears_stale_continue_state(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"
        monitor.st.is_continue_round = True
        SharedState.continue_round_start(monitor.st)

        self._killers(monitor, [1, 2, 3])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)




class TestAlternateRoundNameForDb(unittest.TestCase):
    """Fog / Ghost で送るテラーが全部オルタネイトなら「(Alternate)」で送る（開始の行は常に Fog）"""

    ALT, ALT2, NORMAL = 167, 170, 101

    def setUp(self):
        terrors = {"alternate": {str(self.ALT): {}, str(self.ALT2): {}},
                   "classic": {str(self.NORMAL): {}}}
        for p in (patch.object(config, "TERRORS", terrors),):
            p.start()
            self.addCleanup(p.stop)
        self.monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        self.monitor.st.map_id = 12
        self.monitor.st.transformed_uid = 99

    def _sent(self, round_type, ids, early=False):
        st = self.monitor.st
        st.round_type = round_type
        st.statistics_sent = False
        st.terror_ids = list(ids)
        with patch.object(ConnectDB, "register_round") as send:
            if early:
                self.monitor._send_early_statistics(ids[0])
            else:
                self.monitor._send_round_statistics_once()
        send.assert_called_once()
        return ConnectDB.round_type_id(send.call_args.args[0])

    def test_fog_with_alternate_terrors_is_52(self):
        self.assertEqual(self._sent("Fog", [self.ALT]), 52)
        self.assertEqual(self._sent("Fog", [self.ALT, self.ALT2]), 52)

    def test_the_quiet_early_read_send_is_52_too(self):
        self.assertEqual(self._sent("Fog", [self.ALT], early=True), 52)

    def test_fog_with_normal_or_mixed_terrors_is_2(self):
        self.assertEqual(self._sent("Fog", [self.NORMAL]), 2)
        self.assertEqual(self._sent("Fog", [self.ALT, self.NORMAL]), 2)
        self.assertEqual(self._sent("Fog", [self.NORMAL], early=True), 2)

    def test_ghost_follows_the_same_rule(self):
        self.assertEqual(self._sent("Ghost", [self.ALT]), 53)
        self.assertEqual(self._sent("Ghost", [self.NORMAL]), 9)
        self.assertEqual(self._sent("Ghost (Alternate)", [self.ALT]), 53)

    def test_a_revealed_fog_alternate_stays_52(self):
        self.assertEqual(self._sent("Fog (Alternate)", [self.ALT]), 52)
        self.assertEqual(self._sent("Fog (Alternate)", [self.NORMAL]), 52)

    def test_other_rounds_are_not_touched(self):
        self.assertEqual(self._sent("Classic", [self.ALT]), 1)
        self.assertEqual(self._sent("Alternate", [self.ALT]), 51)

    def test_the_round_type_itself_stays_fog(self):
        """判定・ラウンド指定自爆が見ている st.round_type は変えない"""
        self._sent("Fog", [self.ALT])
        self._sent("Fog", [self.ALT], early=True)
        self.assertEqual(self.monitor.st.round_type, "Fog")

    def test_no_terrors_is_the_round_type(self):
        self.monitor.st.round_type = "Fog"
        self.assertEqual(self.monitor._round_type_for_db([]), "Fog")


class TestWaldoNeedsHavePlush(unittest.TestCase):
    """Waldo を続行するのは Have Plush を持っているときだけ。DTM は今までどおり。
    Have Plush の番号が分からない（item.json に無い）間は、前と同じく続行する"""

    PLUSH = 77

    def setUp(self):
        items = {self.PLUSH: ItemCatalog.Item("Have Plush", "Event", True),
                 1: ItemCatalog.Item("Glow Stick", "Survival", True)}
        p = patch.object(config, "ITEMS", items)
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self, held, wins=0, after_unlock=False):
        cfg = WindowConfig(cancel_afk=True, cancel_afk_after_unlock=after_unlock)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"
        monitor.st.held_item_id = held
        monitor.st.item_id = held or 0
        monitor.st.open_special_round_wins = wins
        return monitor

    def _continues(self, monitor, ids):
        _kind, is_continue, open_special = monitor._list_plan(ids, False)
        return is_continue, open_special

    def test_the_id_is_looked_up_by_name(self):
        self.assertEqual(ItemCatalog.item_id_by_name(" have plush ", config.ITEMS), self.PLUSH)
        self.assertIsNone(ItemCatalog.item_id_by_name("Have Plush", {}))

    def test_waldo_with_have_plush_continues(self):
        self.assertEqual(self._continues(self._monitor(self.PLUSH), [config.WALDO_ID]), (True, True))

    def test_waldo_with_another_item_is_skipped(self):
        self.assertEqual(self._continues(self._monitor(1), [config.WALDO_ID]), (False, False))

    def test_waldo_with_nothing_is_skipped(self):
        self.assertEqual(self._continues(self._monitor(0), [config.WALDO_ID]), (False, False))

    def test_dtm_does_not_need_it(self):
        self.assertEqual(self._continues(self._monitor(1), [config.DTM_ID]), (True, True))

    def test_without_the_id_waldo_is_as_before(self):
        with patch.object(config, "ITEMS", {}), patch.object(config, "HAVE_PLUSH_ITEM_ID", None):
            monitor = self._monitor(1)
            self.assertIsNone(monitor._holds_plush())
            self.assertEqual(self._continues(monitor, [config.WALDO_ID]), (True, True))

    def test_the_fallback_id_in_config_is_used(self):
        with patch.object(config, "ITEMS", {}), patch.object(config, "HAVE_PLUSH_ITEM_ID", 5):
            self.assertTrue(self._monitor(5)._holds_plush())
            self.assertFalse(self._monitor(1)._holds_plush())

    def test_hands_free_skips_waldo_without_it(self):
        monitor = self._monitor(1)
        self.assertIsNotNone(monitor._hands_free_skip_reason([config.WALDO_ID]))
        self.assertIsNone(self._monitor(self.PLUSH)._hands_free_skip_reason([config.WALDO_ID]))
        self.assertIsNone(monitor._hands_free_skip_reason([config.DTM_ID]))

    def test_the_pure_rule(self):
        self.assertEqual(RoundDecision.open_special_ids([config.WALDO_ID, config.DTM_ID], False),
                         [config.DTM_ID])
        self.assertEqual(RoundDecision.open_special_ids([config.WALDO_ID], None), [config.WALDO_ID])
        self.assertEqual(RoundDecision.open_special_ids([config.WALDO_ID], True), [config.WALDO_ID])


class TestOpenSpecialAfterUnlock(unittest.TestCase):
    """窓の設定「3クラ解放後も続行」: 3勝の後も DTM/Waldo を続行し、AFK 対策も回す"""

    def _monitor(self, after_unlock, wins=3):
        cfg = WindowConfig(cancel_afk=True, cancel_afk_after_unlock=after_unlock)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"
        monitor.st.item_id = 1
        monitor.st.open_special_round_wins = wins
        return monitor

    def test_off_skips_after_three_wins(self):
        _kind, is_continue, _open = self._monitor(False)._list_plan([config.DTM_ID], False)
        self.assertFalse(is_continue)

    def test_on_keeps_continuing(self):
        _kind, is_continue, open_special = self._monitor(True)._list_plan([config.DTM_ID], False)
        self.assertEqual((is_continue, open_special), (True, True))

    def test_hands_free_too(self):
        self.assertIsNotNone(self._monitor(False)._hands_free_skip_reason([config.DTM_ID]))
        self.assertIsNone(self._monitor(True)._hands_free_skip_reason([config.DTM_ID]))

    def test_the_afk_loop_starts_and_keeps_running(self):
        monitor = self._monitor(True)
        monitor.st.terror_ids = [config.DTM_ID]
        with patch.object(monitor, "_start_daemon") as daemon:
            monitor._decide_with_keep_on_set("Classic")
        self.assertIn(monitor._action.do_open_special_round_loop,
                      [c.args[0] for c in daemon.call_args_list])
        self.assertTrue(monitor.st.is_open_special_round_round)

    def test_the_afk_loop_does_not_stop_at_three_wins(self):
        st = WindowState(in_round=True, is_open_special_round_round=True, open_special_round_wins=3)
        moves = []
        for after_unlock, expected in ((False, 0), (True, 1)):
            moves.clear()
            ex = ActionExecutor.ActionExecutor(
                WindowConfig(cancel_afk_after_unlock=after_unlock), st, lambda: True, lambda _m: None)
            ticks = iter(range(1000))

            def sleep(_sec):
                if next(ticks) > config.OPEN_SPECIAL_ROUND_INTERVAL_SEC + 1:
                    st.in_round = False
            st.in_round = True
            with patch.object(ActionExecutor.time, "sleep", side_effect=sleep), \
                 patch.object(ex, "move", side_effect=lambda *a: moves.append(a)):
                ex.do_open_special_round_loop()
            self.assertEqual(len(moves), expected, after_unlock)

    def test_the_gui_has_the_switch(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('"cancel_afk_after_unlock": self.v_cancel_afk_after_unlock.get()', src)
        self.assertIn('text="3クラ解放後も続行"', src)

    def test_special_rounds_continue_except_multi_terror_ones(self):
        """3クラ解放後は Cracked などでも続行。複数体のラウンド（Double Trouble・Bloodbath・
        Midnight）だけは続行しない。Bloodbath EX は続行（依頼者 2026-10-04）"""
        for round_type, expected in (("Cracked", True), ("Bloodbath EX", True), ("Fog", True),
                                     ("Punished", True), ("Classic", True),
                                     ("Double Trouble", False), ("Bloodbath", False),
                                     ("Midnight", False)):
            monitor = self._monitor(True)
            monitor.st.round_type = round_type
            _kind, is_continue, open_special = monitor._list_plan([config.DTM_ID], False)
            self.assertEqual((is_continue, open_special), (expected, expected), round_type)
            self.assertEqual(monitor._hands_free_skip_reason([config.DTM_ID]) is None, expected,
                             round_type)

    def test_bloodbath_ex_is_told_apart_by_the_killers_line(self):
        """EX はラウンド開始では Bloodbath。Killers 行で3体が同じなら EX として続行する"""
        for killers, expected in (("50 50 50", True), ("50 7 9", False)):
            monitor = self._monitor(True)
            with patch.object(LogMonitor.threading, "Thread"), \
                 patch.object(ConnectDB, "register_round"), \
                 patch.object(PlaySound, "play_sound"):
                monitor._process("This round is taking place at Facility (12) "
                                 "and the round type is Bloodbath")
                monitor._process(f"Killers have been set - {killers} // Round type is Bloodbath")
            self.assertEqual(monitor.st.round_type, "Bloodbath EX" if expected else "Bloodbath")
            _kind, is_continue, _open = monitor._list_plan(monitor.st.terror_ids, False)
            self.assertEqual(is_continue, expected, killers)


class TestEquippingNothing(unittest.TestCase):
    """「Equipping 0」（外しただけ）では装備待ちを解かない"""

    def test_it_keeps_waiting(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=True), {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.waiting_for_equip = True
        monitor.st.begin_done = True
        with patch.object(monitor, "_auto_begin_active", return_value=True), \
             patch.object(monitor, "_start_daemon") as daemon:
            monitor._on_item_equip(LogParser.LogEvent(LogParser.EVENT_ITEM_EQUIP, item_id=0))
            self.assertTrue(monitor.st.waiting_for_equip)
            daemon.assert_not_called()
            monitor._on_item_equip(LogParser.LogEvent(LogParser.EVENT_ITEM_EQUIP, item_id=29))
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertIn(monitor._release_equip_wait_after_delay,
                      [c.args[0] for c in daemon.call_args_list])
