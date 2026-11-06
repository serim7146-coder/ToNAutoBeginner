"""ラウンドの判定（続行リスト・グループ・moon・Sabotage・自爆/続行の指定）"""
from tests.support import *  # noqa: F401,F403
import ContinueTable




class TestIsAlternateTerror(unittest.TestCase):
    """terrors.json のカテゴリでオルタネイト枠を判定する"""

    WALPURGISNACHT = 1033    # alternate（ログの 33）
    STARVED = 22             # classic
    SELF_INSERTS = 2083      # unbound

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

    def test_every_alternate_id_is_in_the_alternate_range_or_is_a_replacement(self):
        for id_ in (config.TERRORS.get("alternate") or {}):
            self.assertTrue(1000 <= int(id_) < 2000 or int(id_) in (TerrorIds.FOXY, TerrorIds.NEO_PILOT), id_)




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
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID, config.BLOODTHIRSTY_CREATURE_ID,
                    config.GIGABYTES_ID):
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
        """干し芋は 3 つとも全続行。焼き芋の 8 Pages だけは続行リストで決める（下の test_yakiimo_eight_pages_*）"""
        for round_type in ("8 Pages", "Run", "Bloodbath EX"):
            self.assertEqual(self._decide(self.HOSHIIMO, round_type), GroupRound.CONTINUE, round_type)
        for round_type in ("Run", "Bloodbath EX"):
            self.assertEqual(self._decide(self.YAKIIMO, round_type), GroupRound.CONTINUE, round_type)

    def test_yakiimo_eight_pages_is_judged_by_the_list(self):
        """焼き芋の 8 Pages は全続行ではなく通常判定（続行リストにあれば続行・無ければ自爆）。ID は問わない"""
        for ids in ([], [3031], [3032, 3033]):
            self.assertEqual(self._decide(self.YAKIIMO, "8 Pages", ids), GroupRound.NORMAL, ids)

    def test_eight_pages_decision_does_not_depend_on_other_arguments(self):
        for kwargs in ({"moon_repeat": True}, {"skip_moons": {"8 Pages"}}, {"killers_round_type": "8 Pages (Alternate)"}):
            self.assertEqual(self._decide(self.YAKIIMO, "8 Pages", [3031], **kwargs), GroupRound.NORMAL, kwargs)
            self.assertEqual(self._decide(self.HOSHIIMO, "8 Pages", [3031], **kwargs), GroupRound.CONTINUE, kwargs)

    def test_hoshiimo_fog_always_continues(self):
        self.assertEqual(self._decide(self.HOSHIIMO, "Fog", [7]),
                         GroupRound.CONTINUE)

    def test_yakiimo_fog_revealed_as_alternate_is_judged(self):
        alternate = 1005

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
            self._decide(self.YAKIIMO, "Fog (Alternate)", [1006],
                         killers_round_type="Fog (Alternate)"),
            GroupRound.NORMAL)
        self.assertEqual(
            self._decide(self.HOSHIIMO, "Fog (Alternate)", [1006]),
            GroupRound.CONTINUE)

    def test_a_repeated_moon_is_skipped(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._both(moon, moon_repeat=True),
                             {GroupRound.SKIP}, moon)

    def test_yakiimo_skips_the_first_mystic_moon_and_solstice(self):
        for moon in ("Mystic Moon", "Solstice"):
            self.assertEqual(self._decide(self.YAKIIMO, moon), GroupRound.SKIP, moon)

    def test_yakiimo_plays_the_first_blood_moon(self):
        """焼き芋の Twilight は全続行のチェックが無ければ 1 回目から自爆する（TestYakiimoTwilightOptIn）"""
        self.assertEqual(self._decide(self.YAKIIMO, "Blood Moon"), GroupRound.CONTINUE)

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

    ツールの中では、Mystic Moon・Blood Moon・Twilight・Solstice・Special・Cold Night が Special/Moon の 1 つのキーを使い、
    Classic などが Special/Moon を予備に見ることはない。ListTool の Special/Moon は読み込むときに置き場所の
    キー（Classic など）へ配ってある（MatchTNL.own_list_from_listtool）ので、同じ指定が今までと同じに効く。
    """

    SPECIAL = MatchTNL.SPECIAL_MOON_KEY
    CLASSIC = "Classic/クラシック"
    FOG = "Fog/霧"
    MOON_ROUNDS = ("Mystic Moon", "Blood Moon", "Twilight", "Solstice", "Special", "Cold Night")
    # ListTool の番号（このテストの見本は ListTool のファイルの形で書き、読み込みと同じ直し方で通す）
    HHI, ATRACHED, BLOODTHIRSTY, GIGABYTES, SONIC = 190, 191, 192, 314, 40

    def _continue(self, round_type, terror_ids, keep_on, wins=99, cancel_afk=False):
        return RoundDecision.decide_killers(
            keep_on, list(terror_ids), round_type, wins, cancel_afk
        ).is_continue_round

    def _loaded(self, listtool_data):
        return MatchTNL.own_list_from_listtool(listtool_data)

    # ── Variant ─────────────────────────────
    def test_each_variant_of_the_listtool_special_moon_is_found_by_classic(self):
        for tid in (self.HHI, self.ATRACHED, self.BLOODTHIRSTY, self.GIGABYTES):
            keep_on = self._loaded({self.SPECIAL: {tid}})
            self.assertTrue(self._continue("Classic", [flow_id(tid)], keep_on), tid)

    def test_a_variant_missing_from_the_slot_does_not_continue(self):
        self.assertFalse(
            self._continue("Classic", [flow_id(self.ATRACHED)],
                           self._loaded({self.SPECIAL: {self.GIGABYTES}})))

    def test_an_unmutated_terror_is_not_covered(self):
        """Sonic のまま（Variant確定前）なら Special/Moon に居ない"""
        self.assertFalse(
            self._continue("Classic", [flow_id(self.SONIC)],
                           self._loaded({self.SPECIAL: {self.ATRACHED}})))

    def test_an_ordinary_terror_passes_straight_through(self):
        self.assertFalse(self._continue("Classic", [42], self._loaded({self.SPECIAL: {191}})))

    # ── ラウンド別キーを取りこぼさない ────────────
    def test_classic_sees_bloodthirsty_of_the_listtool_special_moon(self):
        self.assertTrue(
            self._continue("Classic", [flow_id(self.BLOODTHIRSTY)],
                           self._loaded({self.SPECIAL: {self.BLOODTHIRSTY}})))

    def test_a_round_key_still_wins_on_its_own(self):
        """192 は Fog などラウンド別のキーにも入っている。そちらの指定は Special/Moon に寄せられて落ちたりしない"""
        self.assertTrue(
            self._continue("Fog", [flow_id(self.BLOODTHIRSTY)],
                           self._loaded({self.FOG: {self.BLOODTHIRSTY}})))

    def test_other_rounds_do_not_look_at_the_special_slot(self):
        """Special/Moon にだけ192を入れている人が、Fog などで誤続行しないこと（Classic だけに配られる）"""
        keep_on = self._loaded({self.SPECIAL: {self.BLOODTHIRSTY}})
        self.assertEqual(set(keep_on), {self.CLASSIC})
        for round_type in ("Fog", "Ghost", "Midnight", "Punished"):
            self.assertFalse(self._continue(round_type, [flow_id(self.BLOODTHIRSTY)], keep_on), round_type)

    def test_being_in_both_is_fine(self):
        keep_on = self._loaded({self.CLASSIC: {self.BLOODTHIRSTY}, self.SPECIAL: {self.BLOODTHIRSTY}})
        self.assertTrue(self._continue("Classic", [flow_id(self.BLOODTHIRSTY)], keep_on))

    def test_the_six_rounds_use_the_one_special_moon_key(self):
        for round_type in self.MOON_ROUNDS:
            self.assertEqual(MatchTNL.LOG_TO_TNL[round_type], self.SPECIAL, round_type)
        self.assertNotIn("Moon", MatchTNL.LOG_TO_TNL, "Moon はログに出ない")
        self.assertFalse(hasattr(RoundDecision, "SPECIAL_MOON_ROUNDS"), "予備に見る決まりは無くなった")

    def test_a_classic_round_key_still_works(self):
        self.assertTrue(self._continue("Classic", [42], {"Classic/クラシック": {42}}))

    def test_an_unrelated_round_key_is_untouched(self):
        self.assertTrue(self._continue("Fog", [7], {self.FOG: {7}}))
        self.assertFalse(self._continue("Fog", [7], {self.FOG: {8}}))

    # ── Moon ────────────────────────────────
    def test_moons_are_found_in_the_special_moon_key(self):
        for round_type, tid in (("Mystic Moon", 196), ("Blood Moon", 197), ("Twilight", 198), ("Solstice", 199)):
            self.assertTrue(
                self._continue(round_type, [flow_id(tid)], self._loaded({self.SPECIAL: {tid}})), round_type)

    def test_a_moon_item_ticked_in_one_listtool_moon_key_works_in_every_moon_round(self):
        """Mystic Moon のキーにだけ入れていたものが、ほかの Moon・Special・Cold Night でも効く（各テラーは自分の
        ラウンドにしか出ないので実際には同じ）"""
        keep_on = self._loaded({"Mystic Moon/ミスティックムーン": {196, 312}})
        self.assertEqual(set(keep_on), {self.SPECIAL})
        for round_type in self.MOON_ROUNDS:
            self.assertTrue(self._continue(round_type, [flow_id(196)], keep_on), round_type)

    def test_special_and_cold_night_use_the_special_moon_key(self):
        keep_on = self._loaded({self.SPECIAL: {313}})
        for round_type in ("Special", "Cold Night"):
            self.assertTrue(self._continue(round_type, [flow_id(313)], keep_on), round_type)
            self.assertFalse(self._continue(round_type, [flow_id(312)], keep_on), round_type)

    def test_cold_night_continues_for_rift_monsters(self):
        """今までは Cold Night に当たるキーが無く、いつも自爆だった（わざと変わるところ）"""
        self.assertTrue(self._continue("Cold Night", [flow_id(312)], self._loaded({self.SPECIAL: {312}})))

    # ── 壊れない ────────────────────────────
    def test_a_missing_special_key_is_fine(self):
        self.assertFalse(self._continue("Classic", [191], {self.CLASSIC: {1}}))

    def test_an_empty_keep_list_is_fine(self):
        self.assertFalse(self._continue("Classic", [191], {}))

    def test_an_empty_terror_list_is_fine(self):
        self.assertFalse(self._continue("Classic", [], self._loaded({self.SPECIAL: {191}})))

    def test_the_open_special_round_path_is_unchanged(self):
        dtm = config.DTM_ID
        decision = RoundDecision.decide_killers({}, [dtm], "Classic", 0, True)

        self.assertTrue(decision.is_open_special_round_target)
        self.assertTrue(decision.is_continue_round)


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
        # ListTool の Special/Moon に Atrached（191）を入れた人のリスト（読み込みで Classic に配られる）
        keep_on = MatchTNL.own_list_from_listtool({self.SPECIAL: {191}})
        monitor = LogMonitor.LogMonitor(cfg, keep_on, lambda _m: None, window_idx=1)
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
            # ids を Gigabytes に差し替えるので、判定したいIDごと変わってしまう
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




class TestYakiimoTwilightOptIn(unittest.TestCase):
    """焼き芋の Twilight は、その窓の全続行（continue_moons）にあるときだけ続行する（依頼者 2026-11-06）"""

    HOSHIIMO = config.INSTANCE_HOSHIIMO
    YAKIIMO = config.INSTANCE_YAKIIMO

    def _decide(self, instance_type, moon, continue_moons=(), moon_repeat=False, skip_moons=()):
        return GroupRound.decide(instance_type, moon, [7], continue_moons=continue_moons,
                                 moon_repeat=moon_repeat, skip_moons=skip_moons)

    def test_unchecked_skips_from_the_first_time(self):
        for repeat in (False, True):
            self.assertEqual(self._decide(self.YAKIIMO, "Twilight", moon_repeat=repeat),
                             GroupRound.SKIP, repeat)

    def test_unchecked_with_other_moons_checked_still_skips(self):
        self.assertEqual(self._decide(self.YAKIIMO, "Twilight", {"Blood Moon", "Mystic Moon"}),
                         GroupRound.SKIP)

    def test_checked_continues_the_first_and_later_times(self):
        for repeat in (False, True):
            self.assertEqual(self._decide(self.YAKIIMO, "Twilight", {"Twilight"}, moon_repeat=repeat),
                             GroupRound.CONTINUE, repeat)

    def test_hoshiimo_twilight_is_unchanged(self):
        for checked in ((), {"Twilight"}):
            self.assertEqual(self._decide(self.HOSHIIMO, "Twilight", checked),
                             GroupRound.CONTINUE, checked)
        self.assertEqual(self._decide(self.HOSHIIMO, "Twilight", {"Twilight"}, moon_repeat=True),
                         GroupRound.SKIP)

    def test_yakiimo_blood_moon_is_unchanged(self):
        for checked in ((), {"Blood Moon"}, {"Twilight"}):
            self.assertEqual(self._decide(self.YAKIIMO, "Blood Moon", checked),
                             GroupRound.CONTINUE, checked)
            self.assertEqual(self._decide(self.YAKIIMO, "Blood Moon", checked, moon_repeat=True),
                             GroupRound.SKIP, checked)

    def test_continue_moons_do_not_change_the_other_yakiimo_moons(self):
        for moon in ("Mystic Moon", "Solstice"):
            self.assertEqual(self._decide(self.YAKIIMO, moon, {moon, "Twilight"}),
                             GroupRound.SKIP, moon)


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
        self.play.assert_called_once_with("continue.mp3", kind="continue")
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




class TestPrivateSabotageContinueTables(unittest.TestCase):
    """private 系の Sabotage は、参加者の希望より先に、その窓の続行表（デフォ続行・続行無効）の Sabotage star を見る。

    実例: Mope Mope（18）がその窓のデフォ続行にあるのに、参加者の誰も希望していないと自爆していた。
    本物の %APPDATA% の続行表は読まない（ContinueTable.set_current で差し替える）。送る先は全部偽物
    """

    STAR = GroupRound.SABOTAGE_STAR_KEY
    MURDER = GroupRound.SABOTAGE_MURDER_KEY
    SABO = TestPrivateSabotageContinues.SABO
    MOPE = 18               # ツールの独自の番号（TerrorIds）。ログの 18 と同じ
    OTHER = 42
    UNRELATED = 99
    ITEM = 5

    _start = TestPrivateSabotageContinues._start
    _monitor = TestPrivateSabotageContinues._monitor

    def setUp(self):
        TestPrivateSabotageContinues.setUp(self)
        self.addCleanup(ContinueTable.set_current, ContinueTable.Store())
        ContinueTable.set_current(ContinueTable.Store())

    def _tables(self, disabled=None, default=None):
        """窓 1 の表（Sabotage star の枠）。disabled・default は番号の並び"""
        def body(ids):
            return {self.STAR: frozenset(ids)} if ids else {}
        ContinueTable.set_current(ContinueTable.Store(
            windows={1: ContinueTable.WindowTable(disabled=body(disabled), default=body(default))}))

    def _item_store(self, disabled=None, default=None):
        """窓 1 はアイテムで切り替える ON。ITEM を持っている間だけ、決まりのプリセットが窓の表の代わりになる"""
        def body(ids):
            return {self.STAR: frozenset(ids)} if ids else {}
        Rule = ContinueTable.ItemRule
        ContinueTable.set_current(ContinueTable.Store(
            disabled_presets={"無": body(disabled)}, default_presets={"足": body(default)},
            windows={1: ContinueTable.WindowTable(item_switch=True)},
            item_rules=(Rule(self.ITEM, "無" if disabled else ContinueTable.KEEP,
                             "足" if default else ContinueTable.KEEP),)))

    def _reset(self):
        self.thread.reset_mock()
        self.play.reset_mock()
        self.record.reset_mock()
        SharedState.continue_round_reset()

    def _run(self, monitor, round_type="Sabotage", ids=None):
        ids = ids or f"{self.MOPE} 0 0"
        monitor._process(f"Killers have been set - {ids} // Round type is {round_type}")
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs]

    def _wishing(self, *terrors):
        """マーダー以外の参加者が star 枠で terrors を希望している"""
        return {"ほかのひと": {self.STAR: set(terrors)}}

    @staticmethod
    def _plan(monitor):
        st = monitor.st
        return monitor._plan(st.round_type, st.terror_ids, False)

    def _plan_for(self, monitor):
        monitor.st.round_type = "Sabotage"
        monitor.st.terror_ids = [self.MOPE]
        return self._plan(monitor)

    def _assert_continued(self, monitor, started):
        self.assertNotIn("do_skip", started, "自爆しない")
        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3", kind="continue")
        self.assertEqual(SharedState.get_continue_round_count(), 1, "他窓フリーズ（普通の続行ラウンド）")
        self.record.assert_called_once_with(1)
        self.assertFalse(any("グループ自動自爆" in m for m in monitor.logs), monitor.logs)

    def _assert_skipped(self, monitor, started):
        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)
        self.play.assert_not_called()

    # ── 1・2: デフォ続行 ──────────────────────
    def test_a_default_continue_terror_continues_when_nobody_wishes_it(self):
        """実例（窓 1 のデフォ続行の Sabotage star に Mope Mope。参加者は誰も希望していない）"""
        self._tables(default=[self.MOPE])
        monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.OTHER))

        started = self._run(monitor)

        self.assertEqual(monitor.st.terror_ids, [self.MOPE])
        self.assertEqual(self._plan(monitor), ("list", True, False))
        self._assert_continued(monitor, started)
        self.assertTrue(any("判定" in m and "【プレイ】" in m for m in monitor.logs), monitor.logs)

    def test_without_the_table_the_same_round_self_destructs(self):
        """対照: 表が空ならこの構成は自爆（これが直す前の実例）"""
        monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.OTHER))

        started = self._run(monitor)

        self._assert_skipped(monitor, started)
        self.assertTrue(any("グループ自動自爆" in m for m in monitor.logs), monitor.logs)

    def test_a_default_continue_terror_beats_a_murderers_wish(self):
        """選出されたマーダーが Sabotage murder 枠で希望していても、デフォ続行が先"""
        self._tables(default=[self.MOPE])
        monitor = self._monitor(
            murderer="マーダー",
            wishes={"マーダー": {self.MURDER: {self.MOPE}}, "ほかのひと": {self.STAR: {self.OTHER}}})

        started = self._run(monitor)

        self._assert_continued(monitor, started)

    def test_the_murderers_wish_still_skips_without_the_default_table(self):
        """対照: 表が無ければ、マーダーの希望で自爆"""
        monitor = self._monitor(murderer="マーダー", wishes={"マーダー": {self.MURDER: {self.MOPE}}})

        self._assert_skipped(monitor, self._run(monitor))

    # ── 3・4: 続行無効 ───────────────────────
    def test_a_disabled_terror_does_not_continue_even_if_a_participant_wishes_it(self):
        monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.MOPE))
        self.assertNotIn("do_skip", self._run(monitor), "対照: 表が無ければ希望どおり続行")

        self._reset()
        self._tables(disabled=[self.MOPE])
        monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.MOPE))

        started = self._run(monitor)

        self.assertEqual(self._plan(monitor), ("group", GroupRound.SKIP))
        self._assert_skipped(monitor, started)

    def test_the_default_table_wins_when_a_terror_is_in_both(self):
        self._tables(disabled=[self.MOPE], default=[self.MOPE])
        monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.OTHER))

        started = self._run(monitor)

        self._assert_continued(monitor, started)

    # ── 5: どちらの表にも無い ─────────────────
    def test_a_terror_in_neither_table_follows_the_participants_wishes(self):
        for disabled, default in (([], []), ([self.UNRELATED], []), ([], [self.UNRELATED]),
                                  ([self.UNRELATED], [self.UNRELATED])):
            for label, wishes, skipped in (("WANTED", self._wishing(self.MOPE), False),
                                           ("SKIP", self._wishing(self.OTHER), True)):
                with self.subTest(disabled=disabled, default=default, result=label):
                    self._reset()
                    self._tables(disabled=disabled, default=default)
                    monitor = self._monitor(murderer="マーダー", wishes=wishes)

                    started = self._run(monitor)

                    self.assertEqual("do_skip" in started, skipped)
                    self.assertEqual(monitor.st.is_continue_round, not skipped)

    def test_empty_tables_and_unrelated_tables_give_the_same_plans(self):
        cases = (dict(wishes=self._wishing(self.MOPE)), dict(wishes=self._wishing(self.OTHER)),
                 dict(murderer="マーダー", wishes={"マーダー": {self.MURDER: {self.MOPE}}}),
                 dict(wishes={}), dict(wishes={}, keep={self.SABO: {self.MOPE}}))
        for kwargs in cases:
            monitor = self._monitor(**kwargs)
            self._tables()
            empty = self._plan_for(monitor)
            self._tables(disabled=[self.UNRELATED], default=[self.UNRELATED + 1])
            self.assertEqual(self._plan_for(monitor), empty, kwargs)

    # ── 6: 希望が分からない（NORMAL → 続行リスト）──────
    def test_a_disabled_terror_without_host_wishes_does_not_continue(self):
        self._tables(disabled=[self.MOPE])
        monitor = self._monitor(wishes={})

        started = self._run(monitor)

        self.assertEqual(self._plan(monitor), ("list", False, False))
        self._assert_skipped(monitor, started)

    def test_a_default_terror_without_host_wishes_continues(self):
        self._tables(default=[self.MOPE])
        monitor = self._monitor(wishes={})

        started = self._run(monitor)

        self.assertEqual(self._plan(monitor), ("list", True, False))
        self._assert_continued(monitor, started)

    def test_without_host_wishes_a_terror_in_neither_table_uses_the_list_as_before(self):
        self._tables(disabled=[self.UNRELATED], default=[self.UNRELATED])
        monitor = self._monitor(wishes={}, keep={self.SABO: {self.MOPE}})
        self.assertEqual(self._plan_for(monitor), ("list", True, False))
        monitor = self._monitor(wishes={}, keep={})
        self.assertEqual(self._plan_for(monitor), ("list", False, False))

    # ── 7: アイテムで切り替える ──────────────
    def test_the_table_chosen_by_the_held_item_applies_to_a_disabled_terror(self):
        self._item_store(disabled=[self.MOPE])
        for held, skipped in ((self.ITEM, True), (0, False), (self.ITEM + 1, False)):
            with self.subTest(held=held):
                self._reset()
                monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.MOPE))
                monitor.st.held_item_id = held

                self.assertEqual("do_skip" in self._run(monitor), skipped)

    def test_the_table_chosen_by_the_held_item_applies_to_a_default_terror(self):
        self._item_store(default=[self.MOPE])
        for held, continued in ((self.ITEM, True), (0, False), (self.ITEM + 1, False)):
            with self.subTest(held=held):
                self._reset()
                monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.OTHER))
                monitor.st.held_item_id = held

                self.assertEqual("do_skip" not in self._run(monitor), continued)

    # ── 8: 干し芋・焼き芋は表を使わない ────────
    def test_group_instances_ignore_the_tables(self):
        cases = (
            # (インスタンス, 参加者の希望, 結果が自爆か)
            (config.INSTANCE_HOSHIIMO, self._wishing(self.MOPE), False),
            (config.INSTANCE_HOSHIIMO, self._wishing(self.OTHER), True),
            (config.INSTANCE_YAKIIMO, self._wishing(self.MOPE), True),
            (config.INSTANCE_YAKIIMO, self._wishing(self.OTHER), True),
        )
        for instance, wishes, skipped in cases:
            for disabled, default in (([self.MOPE], []), ([], [self.MOPE]), ([self.MOPE], [self.MOPE])):
                with self.subTest(instance=instance, wishes=wishes, disabled=disabled, default=default):
                    self._reset()
                    self._tables(disabled=disabled, default=default)
                    monitor = self._monitor(instance_type=instance, murderer="マーダー", wishes=wishes)

                    started = self._run(monitor)

                    self.assertEqual("do_skip" in started, skipped)
                    self.assertEqual(monitor.st.is_continue_round, not skipped)
                    self.assertEqual(monitor._keep_on(), monitor._keep_on_base(), "表を重ねない")

    # ── 9: 先に効く決まり ────────────────────
    def test_the_skip_round_setting_still_comes_first(self):
        """「自爆する」の指定（デフォ続行に無いテラー）は、今までどおり参加者の希望より先に自爆"""
        for disabled, default in (([], []), ([self.MOPE], []), ([], [self.UNRELATED])):
            with self.subTest(disabled=disabled, default=default):
                self._reset()
                self._tables(disabled=disabled, default=default)
                monitor = self._monitor(skip_rounds={"Sabotage"}, murderer="マーダー",
                                        wishes=self._wishing(self.MOPE))

                started = self._run(monitor)

                self.assertEqual(self._plan(monitor), ("round_skip",))
                self._assert_skipped(monitor, started)

    def test_the_skip_round_setting_is_still_overridden_by_the_default_table(self):
        """既存の決まり（GS）: 自爆の指定でも、その窓のデフォ続行にあれば続行"""
        self._tables(default=[self.MOPE])
        monitor = self._monitor(skip_rounds={"Sabotage"}, murderer="マーダー", wishes=self._wishing(self.OTHER))

        started = self._run(monitor)

        self._assert_continued(monitor, started)
        self.assertTrue(any("（デフォ続行）" in m for m in monitor.logs), monitor.logs)

    def test_hands_free_still_self_destructs_at_once(self):
        SharedState.set_hands_free(True)
        self._tables(default=[self.MOPE])
        monitor = self._monitor(murderer="マーダー", wishes=self._wishing(self.MOPE))
        monitor.st.item_id = 0

        started = self._run(monitor)

        self.assertEqual(self._plan(monitor)[0], "hands_free")
        self.assertIn("do_skip", started)
        self.assertTrue(any("放置モード" in m for m in monitor.logs), monitor.logs)

    # ── 全続行の窓 ──────────────────────────
    def test_an_all_continue_window_never_self_destructs(self):
        for disabled, default in (([], []), ([self.MOPE], []), ([], [self.UNRELATED])):
            for wishes in (self._wishing(self.MOPE), self._wishing(self.OTHER)):
                with self.subTest(disabled=disabled, default=default, wishes=wishes):
                    self._reset()
                    self._tables(disabled=disabled, default=default)
                    monitor = self._monitor(continue_rounds={"Sabotage"}, murderer="マーダー", wishes=wishes)

                    started = self._run(monitor)

                    plan = self._plan(monitor)
                    self.assertNotEqual(LogMonitor.LogMonitor._outcome(plan), "skip", plan)
                    self.assertNotIn("do_skip", started)
                    if disabled:       # 続行無効のテラーは希望があっても続行リストでは続行にならない → 全続行（静かに）のまま
                        self.assertEqual(plan, ("continue_rounds",))
                    elif wishes == self._wishing(self.OTHER):
                        self.assertEqual(plan, ("continue_rounds",))
                        self.play.assert_not_called()
                    else:              # 希望が当たれば、他の全続行のラウンドと同じく続行リストで続行
                        self.assertEqual(plan, ("group", GroupRound.WANTED))

    def test_an_all_continue_window_continues_as_a_listed_round_on_a_default_hit(self):
        self._tables(default=[self.MOPE])
        monitor = self._monitor(continue_rounds={"Sabotage"}, murderer="マーダー", wishes=self._wishing(self.OTHER))

        started = self._run(monitor)

        self.assertEqual(self._plan(monitor), ("list", True, False))
        self._assert_continued(monitor, started)


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

    def test_a_non_murderer_wanting_the_star_slot_is_wanted_in_hoshiimo(self):
        """誰かが欲しがっている続行。全続行とは別枠（アナウンスが要る）。焼き芋は全自爆なので干し芋で見る"""
        wishes = {"ソノア7": {self.MURDER: {99}},
                  "みているひと": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_HOSHIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.WANTED)

    def test_yakiimo_always_skips_sabotage(self):
        """焼き芋の Sabotage は全自爆（GS）。マーダー以外が star を希望していても、希望が無くても自爆する"""
        star_wanted = {"ソノア7": {self.MURDER: {99}},
                       "みているひと": {self.STAR: {5}}}
        for wishes, follow in ((star_wanted, True), ({}, True), ({}, False), (star_wanted, False)):
            self.assertEqual(
                self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes, follow_host=follow),
                GroupRound.SKIP, (wishes, follow))

    def test_private_like_sabotage_still_follows_the_wishes(self):
        """プラベ系の Sabotage（_decide_sabotage）は今のまま"""
        star_wanted = {"ソノア7": {self.MURDER: {99}},
                       "みているひと": {self.STAR: {5}}}
        for itype in config.PRIVATE_LIKE_INSTANCES:
            self.assertEqual(self._decide(itype, sus=["ソノア7"], wishes=star_wanted), GroupRound.WANTED, itype)
            self.assertEqual(self._decide(itype, sus=["ソノア7"], wishes={"ソノア7": {self.MURDER: {5}}}),
                             GroupRound.SKIP, itype)
            self.assertEqual(self._decide(itype, sus=["ソノア7"], wishes={}), GroupRound.NORMAL, itype)

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
        """追従OFFでは誰の希望か分からない。従来どおり keepOn_set で判定する（焼き芋は全自爆なので除く）"""
        for itype in (config.INSTANCE_HOSHIIMO, *config.PRIVATE_LIKE_INSTANCES):
            self.assertEqual(
                self._decide(itype, sus=["ソノア7"], wishes={}, follow_host=False),
                GroupRound.NORMAL, itype)

    def test_following_on_but_empty_list_also_falls_back(self):
        """0人のときは前のリストを保持しているので、希望が空なら判断できない（焼き芋は全自爆なので干し芋で見る）"""
        self.assertEqual(
            self._decide(config.INSTANCE_HOSHIIMO, sus=["ソノア7"], wishes={}),
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




class TestEightPagesPages(unittest.TestCase):
    """8 Pages のログの番号（A）は専用のページ番号。入口で 3000＋A の独自の番号にする（Classic の同じテラーとも別の番号）。
    B は別物なので捨てる"""

    PAGES = "8 Pages/8ページ"
    REAL = {50: 135, 59: 39, 55: 85, 46: 172}      # ページ番号 → ToN ListTool の番号（実ログと ListTool の履歴で確かめた対応）

    def setUp(self):
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)

    # ── 表 ────────────────────────────────
    def test_the_real_pages_are_in_the_table(self):
        for page, listtool in self.REAL.items():
            row = TerrorIds.row(3000 + page)
            self.assertEqual((row.log, row.listtool), (page, listtool), page)

    def test_an_unregistered_page_flows_as_3000_plus_the_page(self):
        self.assertIsNone(TerrorIds.row(3070))
        self.assertEqual(TerrorIds.from_log([70, 5], "8 Pages"), [3070], "B（5）は捨てる")
        self.assertEqual(TerrorIds.name(3070), "#3070")

    def test_only_the_first_number_is_parsed(self):
        self.assertEqual(MatchTNL.parse_terror_ids("50", "2", "0", "8 Pages"), [50])
        self.assertEqual(MatchTNL.parse_terror_ids("49", "23", "0",
                                                   "8 Pages (Alternate)"), [49])
        self.assertEqual(MatchTNL.parse_terror_ids("49", "23", "0",
                                                   "Double Trouble"), [49, 23])

    # ── 窓の動き ───────────────────────────
    def _monitor(self, keep):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", cancel_afk=True)
        monitor = LogMonitor.LogMonitor(cfg, keep, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "8 Pages"
        monitor.logs = []
        monitor.logger = monitor.logs.append
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

    def test_the_page_decides_the_continue(self):
        monitor = self._monitor({self.PAGES: {3050}})       # Whiteface（8 Pages の 50 ページ目）

        started, sent, play = self._run(monitor)

        self.assertEqual(monitor.st.terror_ids, [3050], "ログの 50 は 3050")
        self.assertNotIn("do_skip", started, "リストにあるので続行")
        play.assert_called_once_with("continue.mp3", kind="continue")
        self.assertEqual(sent.call_args.args[1], [3050], "統計・DB も独自の番号")

    def test_a_classic_number_in_the_8_pages_list_does_not_continue(self):
        monitor = self._monitor({self.PAGES: {50}})        # 50 は Classic の DTM。8 Pages の 50 ページ目（3050）とは別

        started, _sent, _play = self._run(monitor)

        self.assertIn("do_skip", started)

    def test_the_second_number_is_dropped(self):
        monitor = self._monitor({self.PAGES: {3002}})

        started, _sent, _play = self._run(monitor)

        self.assertEqual(monitor.st.terror_ids, [3050])
        self.assertIn("do_skip", started, "B（2）のページだけがリストにあっても続行しない")

    def test_the_name_shown_is_the_real_terror(self):
        monitor = self._monitor({self.PAGES: {3050}})

        self._run(monitor)

        self.assertTrue(any("Whiteface" in m for m in monitor.logs), monitor.logs)

    def test_an_unregistered_page_flows_and_is_written_to_debug_log_once(self):
        TerrorIds._reported.discard(("own", 3070))
        monitor = self._monitor({self.PAGES: {3050}})
        line = "Killers have been set - 70 1 0 // Round type is 8 Pages"

        with patch.object(DebugLog, "write") as write:
            started, sent, _play = self._run(monitor, line)
            self._run(monitor, line)

        self.assertEqual(monitor.st.terror_ids, [3070])
        self.assertIn("do_skip", started, "リストに入れる手段が無いので続行にならない")
        self.assertTrue(any("Terror set: #3070 / 8 Pages" in m for m in monitor.logs), monitor.logs)
        self.assertFalse(any("未登録" in m for m in monitor.logs), "公開ログの案内はやめた")
        self.assertEqual(len([c for c in write.call_args_list if "表にない番号 3070" in c.args[0]]), 1,
                         "debug.log に 1 回だけ（[事象] の行はそのたびに出る）")
        self.assertEqual(sent.call_args.args[1], [3070], "統計・DB には残る")

    def test_the_page_number_is_not_taken_for_dtm(self):
        """放置モードは DTM/Waldo のラウンドだけ自爆しない。8 Pages の 50 ページ目（Whiteface）は DTM（50）ではない"""
        self.assertIn(50, config.OPEN_SPECIAL_ROUND_TERROR_IDS, "前提: 50 は DTM")
        monitor = self._monitor({})
        self.assertIsNone(monitor._hands_free_skip_reason([50]), "前提: 50 なら自爆しない")

        self._run(monitor)

        self.assertEqual(monitor.st.terror_ids, [3050])
        self.assertIsNotNone(monitor._hands_free_skip_reason(monitor.st.terror_ids),
                             "3050 は DTM 扱いにしない")

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

    def test_the_group_always_continue_still_wins_for_an_unregistered_page(self):
        monitor = self._monitor({})
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        SharedState.set_list_source("host")

        started, _sent, play = self._run(
            monitor, "Killers have been set - 70 1 0 // Round type is 8 Pages")

        self.assertIn("8 Pages", GroupRound.ALWAYS_CONTINUE_ROUNDS)
        self.assertNotIn("do_skip", started, "未登録でも干し芋の全続行は効く")



# ═══════════════════════════════════════════════
#  RoundDecision.py
# ═══════════════════════════════════════════════
class TestRoundDecision(unittest.TestCase):
    def test_the_entrance_makes_alternate_and_unbound_numbers(self):
        self.assertEqual(TerrorIds.from_log([1], "Alternate"), [1001])
        self.assertEqual(TerrorIds.from_log([1], "Classic", "Unbound"), [2001])
        self.assertFalse(hasattr(RoundDecision, "normalize_killer_ids"), "入口は LogMonitor._own_killer_ids の 1 か所だけ")
        self.assertFalse(hasattr(MatchTNL, "apply_alternate_offset"))

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
        self.played.assert_called_once_with("lost.mp3", kind="list_lost")

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
        self.played.assert_called_once_with("lost.mp3", kind="list_lost")

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

    def test_a_line_still_being_written_waits_for_its_newline(self):
        """書きかけの行（改行がまだ無い）は読まない。`Verified Round End` が `Verified` で切れて
        受理と取り違えないため。日本語の途中で切れても、次の読みとつないで 1 行にする"""
        monitor = self._monitor()
        monitor._running = True
        monitor._stop_event = threading.Event()
        seen = []
        prefix = "2026.10.12 12:00:00 Debug      -  "
        name = "すぅみ_suumi"
        joined = (prefix + f"[Behaviour] OnPlayerJoined {name} (usr_0e01408a)").encode("utf-8")
        ticks = {"n": 0}

        def tick():
            ticks["n"] += 1
            with open(log, "ab") as f:
                if ticks["n"] == 1:
                    f.write((prefix + "Verified").encode("utf-8"))          # 書きかけ
                elif ticks["n"] == 2:
                    f.write(b" Round End\r\n" + joined[:len(prefix) + 30])   # 日本語の途中まで
                elif ticks["n"] == 3:
                    f.write(joined[len(prefix) + 30:] + b"\n")
                else:
                    monitor._running = False

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "output_log.txt"
            log.write_text("", encoding="utf-8")
            monitor.cfg.log_path = log
            with patch.object(config, "LOG_POLL_INTERVAL", 0), \
                 patch.object(monitor, "_process", side_effect=seen.append), \
                 patch.object(monitor, "_check_group_list_state", tick), \
                 patch.object(monitor, "_detect_instance_from_log"):
                monitor._run()

        self.assertEqual(seen, [prefix + "Verified Round End", joined.decode("utf-8")])




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

        self.played.assert_called_once_with("continue.mp3", kind="continue")
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

    def test_hoshiimo_announces_but_yakiimo_always_skips(self):
        """干し芋は star 希望で続行する。焼き芋の Sabotage は全自爆（GS）で、アナウンスもフリーズも出さない"""
        monitor = self._monitor(config.INSTANCE_HOSHIIMO)
        self._apply(monitor)
        self.assertTrue(monitor.st.is_continue_round)
        self.played.assert_called_once_with("continue.mp3", kind="continue")

        SharedState.continue_round_reset()
        monitor = self._monitor(config.INSTANCE_YAKIIMO)
        with patch.object(monitor, "_start_group_skip") as skip:
            self._apply(monitor)
        skip.assert_called_once()
        self.assertFalse(monitor.st.is_continue_round)
        self.played.assert_not_called()

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
            started = _decision_threads(mock_thread)
            if "_delayed_decision" in started:
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += _decision_threads(mock_thread)
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

    def test_a_keep_listed_terror_becomes_a_continue_round(self):
        """FE: 全続行のラウンドでも続行リストを見る。続行なら普通の続行ラウンドと同じ（アナウンスも）"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})

        self._killers(monitor, [99])

        self.assertTrue(monitor.st.is_continue_round)
        self.played.assert_called()

    def test_an_unlisted_terror_is_still_quiet(self):
        """続行リストで続行にならなければ今どおり全続行（アナウンスなし）"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {12}})

        started = self._killers(monitor, [99])

        self.assertFalse(monitor.st.is_continue_round)
        self.played.assert_not_called()
        self.assertNotIn("do_skip", started)

    def test_continue_beats_skip_when_both_are_set(self):
        """settings.json を手編集された場合の保険。自爆しない側に倒す"""
        monitor = self._monitor(continue_rounds=("Classic",),
                                skip_rounds=("Classic",))

        started = self._killers(monitor)

        self.assertEqual(started, [])

    def test_a_listed_round_waits_for_the_variant_like_any_round(self):
        """FE: 全続行のラウンドでも続行リストを見るので、置き換えで続行かが変わるなら待つ（普通のラウンドと同じ）"""
        monitor = self._monitor(continue_rounds=("Classic",),
                                skip_rounds=("Classic",),
                                keep_on={self.CLASSIC_KEY: {TerrorIds.GIGABYTES}})

        started = self._killers(monitor)

        self.assertIn("_delayed_decision", started)
        self.assertNotIn("do_skip", started, "全続行は自爆の指定より前（自爆しない）")

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
                     "v_ton_begin", "v_join_world", "v_ton_access"):
            setattr(app, name, TestSkipRoundsSettings.FakeVar(""))
        for name in ("v_obs_enabled", "v_obs_freeze", "v_obs_host", "v_obs_port", "v_obs_password"):
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
            app._window_freeze_setting = lambda: {}
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

    def _tab(self, idx=0):
        tab = type("FakeTab", (), {})()
        tab.idx = idx
        tab.freeze_loaded = None            # 全窓フリーズは窓ごと: タブへ渡された値
        tab.set_freeze_values = lambda values, tab=tab: setattr(tab, "freeze_loaded", values)
        tab.v_profile = TestRoundSettingsAreNotLoaded.FakeVar(0)
        tab.v_skip_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                             for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_continue_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                                 for n in config.SKIP_ROUND_SELECTABLE}
        return tab

    def _load(self, data):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = [self._tab(0), self._tab(1)]
        for name in ("v_desktop_mode", "v_use_osc",
                     "v_ton_entry", "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_instance_link", "v_emergency_key", "v_start_key", "v_tnl", "v_obs_enabled", "v_obs_freeze", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, TestRoundSettingsAreNotLoaded.FakeVar(""))
        app._apply_obs_settings = lambda: None
        app.added_tools = []
        app._add_tool_row = lambda p, save=True: app.added_tools.append(p)
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._load_own_list = lambda: None
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
        for tab in app.tabs:        # 古い共通の値は全窓へ写る
            self.assertEqual(tab.freeze_loaded, {"freeze_8pages": True, "freeze_punish": True,
                                                 "freeze_rounds": ["Classic"]})




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
            started = _decision_threads(mock_thread)
            if settle and "_delayed_decision" in started:
                monitor._running = True
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += _decision_threads(mock_thread)
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
        for tid, listed in ((config.HUNGRY_HOME_INVADER_ID, TerrorIds.HUNGRY_HOME_INVADER),
                            (config.ATRACHED_ID, TerrorIds.ATRACHED),
                            (config.BLOODTHIRSTY_CREATURE_ID, TerrorIds.BLOODTHIRSTY_CREATURE),
                            (config.GIGABYTES_ID, TerrorIds.GIGABYTES)):
            monitor = self._classic_monitor(tid, keep_on={"Classic/クラシック": {listed}})

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

    def test_yakiimo_plays_the_first_blood_moon(self):
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)

        started = self._round(monitor, "Blood Moon", [7])

        self.assertEqual(started, [])

    # ── 焼き芋の Twilight は全続行のチェックを入れた回だけ続行 ──
    def _twilight_monitor(self, continue_rounds=(), keep_on=None,
                          instance_type=config.INSTANCE_YAKIIMO):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           continue_rounds=set(continue_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        return monitor

    def test_yakiimo_twilight_unchecked_self_destructs_the_first_time(self):
        monitor = self._twilight_monitor()

        started = self._round(monitor, "Twilight", [7])

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.all_continue_round)
        self.assertFalse(monitor.st.is_continue_round)

    def test_yakiimo_twilight_unchecked_self_destructs_even_if_the_terror_is_listed(self):
        monitor = self._twilight_monitor(
            keep_on={MatchTNL.SPECIAL_MOON_KEY: {99}})

        started = self._round(monitor, "Twilight", [99])

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)
        self.assertFalse(monitor.st.all_continue_round)

    def test_yakiimo_twilight_checked_is_all_continue(self):
        for repeat in (False, True):
            SharedState.continue_round_reset()
            monitor = self._twilight_monitor(continue_rounds={"Twilight"})
            monitor.st.moon_repeat = repeat

            started = self._round(monitor, "Twilight", [7])

            self.assertNotIn("do_skip", started, repeat)
            self.assertTrue(monitor.st.all_continue_round, repeat)
            self.assertFalse(monitor.st.is_continue_round, repeat)

    def test_yakiimo_twilight_checked_and_listed_is_a_continue_round(self):
        monitor = self._twilight_monitor(
            continue_rounds={"Twilight"}, keep_on={MatchTNL.SPECIAL_MOON_KEY: {99}})

        started = self._round(monitor, "Twilight", [99])

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_hoshiimo_twilight_is_unchanged_by_the_check(self):
        for checked in (set(), {"Twilight"}):
            SharedState.continue_round_reset()
            monitor = self._twilight_monitor(
                continue_rounds=checked, instance_type=config.INSTANCE_HOSHIIMO)

            started = self._round(monitor, "Twilight", [7])

            self.assertNotIn("do_skip", started, checked)

    # ── 焼き芋 Fog ──────────────────────────
    def test_yakiimo_fog_revealed_as_alternate_uses_the_keep_list(self):
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO,
                                keep_on={self.FOG_KEY: {TerrorIds.ALTERNATE_BASE + 3}})
        monitor.st.round_type = "Fog"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers([1003], "Fog (Alternate)", revealed=True)

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
        mock_play.assert_called_once_with("continue.mp3", kind="continue")

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
        monitor = self._monitor(keep_on={"Classic/クラシック": {TerrorIds.GIGABYTES}})

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
                keep_on={"Bloodbath/ブラッドバス": {TerrorIds.BLOODTHIRSTY_CREATURE}})

            started = self._round(monitor, round_type, ids)

            self.assertEqual(started, expected, round_type)

    def test_a_normal_round_waits_when_the_variant_is_wanted(self):
        monitor = self._monitor(
            keep_on={self.DT_KEY: {TerrorIds.BLOODTHIRSTY_CREATURE}})

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
        monitor.keepOn_set["Classic/クラシック"] = {TerrorIds.GIGABYTES}
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
        monitor.keepOn_set["Classic/クラシック"] = {TerrorIds.ATRACHED}
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
        monitor = self._monitor(keep_on={"Classic/クラシック": {TerrorIds.ATRACHED}})
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


class TestWaldoNeedsGuidancePlush(unittest.TestCase):
    """Waldo を続行するのは Guidance Plush を持っているときだけ。DTM は今までどおり。
    Guidance Plush の番号が分からない（item.json に無い）間は、前と同じく続行する"""

    PLUSH = 77

    def setUp(self):
        items = {self.PLUSH: ItemCatalog.Item("Guidance Plush", "Event", True),
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
        self.assertEqual(ItemCatalog.item_id_by_name(" guidance plush ", config.ITEMS), self.PLUSH)
        self.assertIsNone(ItemCatalog.item_id_by_name("Guidance Plush", {}))

    def test_waldo_with_guidance_plush_continues(self):
        self.assertEqual(self._continues(self._monitor(self.PLUSH), [config.WALDO_ID]), (True, True))

    def test_waldo_with_another_item_is_skipped(self):
        self.assertEqual(self._continues(self._monitor(1), [config.WALDO_ID]), (False, False))

    def test_waldo_with_nothing_is_skipped(self):
        self.assertEqual(self._continues(self._monitor(0), [config.WALDO_ID]), (False, False))

    def test_dtm_does_not_need_it(self):
        self.assertEqual(self._continues(self._monitor(1), [config.DTM_ID]), (True, True))

    def test_without_the_id_waldo_is_as_before(self):
        with patch.object(config, "ITEMS", {}), patch.object(config, "GUIDANCE_PLUSH_ITEM_ID", None):
            monitor = self._monitor(1)
            self.assertIsNone(monitor._holds_plush())
            self.assertEqual(self._continues(monitor, [config.WALDO_ID]), (True, True))

    def test_the_fallback_id_in_config_is_used(self):
        with patch.object(config, "ITEMS", {}), patch.object(config, "GUIDANCE_PLUSH_ITEM_ID", 5):
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
        st = WindowState(in_round=True, is_open_special_round_round=True, open_special_round_wins=3,
                         instance_type=config.INSTANCE_PRIVATE)
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
        self.assertIn('text="3クラ後もDTM/Waldo続行"', src)

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


class TestTwoClearTargetRule(unittest.TestCase):
    """窓の設定「DTM/Waldo続行 (2クラまで)」: 目標の勝数だけを変える純粋な規則"""

    # (2クラまで, 3クラまで, 3クラ後も) → 勝利数 0,1,2,3 のときの DTM/Waldo の続行
    TABLE = (
        ((True, False, False), (True, True, False, False)),     # 2クラまでだけ: 2 勝で止まる
        ((True, True, False), (True, True, False, False)),      # 3クラまでも ON: 2クラまでが優先
        ((True, True, True), (True, True, True, True)),         # 3クラ後もが最優先: 止まらない
        ((True, False, True), (True, True, True, True)),
        ((False, True, False), (True, True, True, False)),      # 2クラまで OFF: 今までどおり
        ((False, True, True), (True, True, True, True)),
        ((False, False, False), (False, False, False, False)),  # どちらも OFF: 続行しない
    )

    def test_the_target(self):
        self.assertEqual(RoundDecision.unlock_target(False), 3)
        self.assertEqual(RoundDecision.unlock_target(True), 2)
        self.assertEqual(config.OPEN_SPECIAL_ROUND_TARGET_WINS, 3)
        self.assertEqual(config.OPEN_SPECIAL_ROUND_TWO_TARGET_WINS, 2)

    def test_all_the_rows_through_decide_killers(self):
        for (two, three, after), expected in self.TABLE:
            for wins, want in enumerate(expected):
                decision = RoundDecision.decide_killers(
                    {}, [config.DTM_ID], "Classic", wins, bool(two or three),
                    after_unlock=after, target=RoundDecision.unlock_target(two))
                self.assertEqual(
                    (decision.is_open_special_round_target, decision.is_continue_round), (want, want),
                    (two, three, after, wins))

    def test_the_active_rule(self):
        self.assertEqual([RoundDecision.open_special_active(w, False, 2) for w in range(4)],
                         [True, True, False, False])
        self.assertEqual([RoundDecision.open_special_active(w, True, 2) for w in range(4)], [True] * 4)

    def test_omitting_the_target_is_as_before(self):
        for after in (False, True):
            for wins in range(4):
                today = after or wins < 3
                self.assertEqual(RoundDecision.open_special_active(wins, after), today)
                self.assertEqual(RoundDecision.open_special_active(wins, after, None), today)
                self.assertEqual(RoundDecision.open_special_active(wins, after, 3), today)
                for kwargs in ({}, {"target": None}, {"target": 3}):
                    self.assertEqual(RoundDecision.is_open_special_round_target(
                        [config.WALDO_ID], "Classic", wins, True, after, **kwargs), today)
                    decision = RoundDecision.decide_killers(
                        {}, [config.DTM_ID], "Classic", wins, True, after_unlock=after, **kwargs)
                    self.assertEqual(decision, RoundDecision.KillerDecision(today, today))
                    self.assertEqual(RoundDecision.decide_killers(
                        {}, [config.DTM_ID], "Classic", wins, False, after_unlock=after, **kwargs),
                        RoundDecision.KillerDecision(False, False))

    def test_the_list_still_continues_after_the_target(self):
        keep_on = {"Classic/クラシック": {config.DTM_ID}}
        decision = RoundDecision.decide_killers(keep_on, [config.DTM_ID], "Classic", 2, True, target=2)
        self.assertEqual((decision.is_open_special_round_target, decision.is_continue_round), (False, True))


class TestTwoClearWindow(unittest.TestCase):
    """「2クラまで」の窓を LogMonitor の本当の判定の道（_list_plan・自爆指定・放置モードの理由・AFK ループ）に通す"""

    def _monitor(self, two=False, three=False, after=False, wins=0):
        cfg = WindowConfig(cancel_afk=three, cancel_afk_two=two, cancel_afk_after_unlock=after)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"
        monitor.st.item_id = 1
        monitor.st.open_special_round_wins = wins
        return monitor

    def test_default_is_off(self):
        self.assertFalse(WindowConfig().cancel_afk_two)
        self.assertTrue(WindowConfig().cancel_afk)

    def test_all_the_rows_through_the_monitor(self):
        for (two, three, after), expected in TestTwoClearTargetRule.TABLE:
            for wins, want in enumerate(expected):
                monitor = self._monitor(two, three, after, wins)
                _kind, is_continue, open_special = monitor._list_plan([config.DTM_ID], False)
                self.assertEqual((is_continue, open_special), (want, want), (two, three, after, wins))
                self.assertEqual(monitor._hands_free_skip_reason([config.DTM_ID]) is None, want,
                                 (two, three, after, wins))

    def test_the_two_check_alone_enables_waldo_too(self):
        monitor = self._monitor(two=True, wins=1)
        monitor.st.held_item_id = RoundDecision.guidance_plush_id()
        _kind, is_continue, _open = monitor._list_plan([config.WALDO_ID], False)
        self.assertTrue(is_continue)

    def test_the_target_changes_the_round_skip_exception(self):
        """全自爆の指定より 3クラ解放が勝つのも、窓の目標まで"""
        for two, wins, expect_skip in ((True, 1, False), (True, 2, True), (False, 2, False), (False, 3, True)):
            monitor = self._monitor(two=two, three=not two, wins=wins)
            monitor.cfg.skip_rounds = {"Classic"}
            self.assertEqual(monitor._should_skip_by_round([config.DTM_ID], False), expect_skip, (two, wins))

    def _loop_moves(self, wins, **cfg):
        st = WindowState(in_round=True, is_open_special_round_round=True, open_special_round_wins=wins,
                         instance_type=config.INSTANCE_PRIVATE)
        moves = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(**cfg), st, lambda: True, lambda _m: None)
        ticks = iter(range(1000))

        def sleep(_sec):
            if next(ticks) > config.OPEN_SPECIAL_ROUND_INTERVAL_SEC + 1:
                st.in_round = False
        with patch.object(ActionExecutor.time, "sleep", side_effect=sleep), \
             patch.object(ex, "move", side_effect=lambda *a: moves.append(a)):
            ex.do_open_special_round_loop()
        return len(moves)

    def test_the_afk_loop_stops_at_two_only_for_a_two_clear_window(self):
        self.assertEqual(self._loop_moves(2, cancel_afk_two=True), 0)
        self.assertEqual(self._loop_moves(2, cancel_afk=True), 1, "3クラだけの窓は 2 勝では続ける")
        self.assertEqual(self._loop_moves(2, cancel_afk_two=True, cancel_afk_after_unlock=True), 1,
                         "3クラ後もが入っていれば止まらない")
        self.assertEqual(self._loop_moves(3, cancel_afk_two=True, cancel_afk_after_unlock=True), 1)

    def test_the_afk_loop_keeps_running_below_the_target(self):
        self.assertEqual(self._loop_moves(1, cancel_afk_two=True), 1)

    def test_the_round_start_log_uses_the_target(self):
        monitor = self._monitor(two=True, wins=1)
        monitor.st.terror_ids = [config.DTM_ID]
        with patch.object(monitor, "_start_daemon") as daemon:
            monitor._decide_with_keep_on_set("Classic")
        self.assertIn("[窓1] 2クラ解放ラウンド開始（勝利数: 1/2）", monitor.logs)
        self.assertIn(monitor._action.do_open_special_round_loop, [c.args[0] for c in daemon.call_args_list])

    def test_a_three_clear_window_keeps_its_old_round_start_log(self):
        monitor = self._monitor(three=True, wins=2)
        monitor.st.terror_ids = [config.DTM_ID]
        with patch.object(monitor, "_start_daemon"):
            monitor._decide_with_keep_on_set("Classic")
        self.assertIn("[窓1] 3クラ解放ラウンド開始（勝利数: 2/3）", monitor.logs)

    def test_the_already_reached_log_uses_the_target(self):
        for two, text in ((True, "DTM/Waldoラウンドだが2勝達成済み→AFK解除なし"),
                          (False, "DTM/Waldoラウンドだが3勝達成済み→AFK解除なし")):
            monitor = self._monitor(two=two, three=not two, wins=3)
            monitor.st.terror_ids = [config.DTM_ID]
            with patch.object(monitor, "_start_daemon"):
                monitor._decide_with_keep_on_set("Classic", ("list", True, True))
            self.assertIn("[窓1] " + text, monitor.logs)


class TestTwoClearLogs(unittest.TestCase):
    """「2クラまで」の窓のログは目標（2）で出し、3/2 とは出さない。内部の勝利数は 3 まで数える"""

    ME = TestThreeWinsRestored.ME
    PREFIX = TestThreeWinsRestored.PREFIX
    JOIN = TestThreeWinsRestored.JOIN
    OLD_JOIN = TestThreeWinsRestored.OLD_JOIN
    LIVED = TestThreeWinsRestored.LIVED
    NEXT_JOIN = "[Behaviour] Joining wrld_next:3~private(usr_me)~region(jp)"
    _round = staticmethod(TestThreeWinsRestored._round)

    def _monitor(self, path=None, two=True, three=False):
        cfg = WindowConfig(log_path=path, cancel_afk=three, cancel_afk_two=two)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _scan(self, after_join, two=True, three=False):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        lines = [f"User Authenticated: serim01 ({self.ME})", self.JOIN, *after_join]
        tmp.write("\n".join(self.PREFIX + line for line in lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = self._monitor(Path(tmp.name), two, three)
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None):
            monitor._detect_instance_from_log()
        return monitor

    def _live(self, monitor, *lines):
        with patch.object(LogMonitor.threading, "Thread"), patch.object(PlaySound, "play_sound"):
            for line in lines:
                monitor._process(self.PREFIX + line)

    @staticmethod
    def _said(monitor):
        return [m for m in monitor.logs if "クラ" in m or "勝" in m or "生存数" in m]

    def test_lives_show_one_and_two_and_the_achievement_fires_once(self):
        monitor = self._monitor()
        self._live(monitor, self.LIVED, self.LIVED)
        self.assertEqual(self._said(monitor), [
            "[窓1] 生存数: 1/2", "[窓1] 生存数: 2/2",
            "[窓1] 🎉 2勝達成！以降のDTM/Waldoラウンドはスキップします"])

    def test_a_third_life_counts_inside_but_never_prints_three_over_two(self):
        monitor = self._monitor()
        self._live(monitor, self.LIVED, self.LIVED, self.LIVED, self.LIVED)
        self.assertEqual(monitor.st.open_special_round_wins, 3, "内部は 3 まで数える")
        self.assertEqual(sum("生存数" in m for m in monitor.logs), 2)
        self.assertEqual(sum("🎉 2勝達成" in m for m in monitor.logs), 1)
        self.assertFalse(any("3/2" in m or "🎉 3勝" in m for m in monitor.logs))

    def test_the_achievement_with_the_after_unlock_text(self):
        monitor = self._monitor()
        monitor.cfg.cancel_afk_after_unlock = True
        self._live(monitor, self.LIVED, self.LIVED)
        self.assertIn("[窓1] 🎉 2勝達成！3クラ解放後も続行の設定なので、DTM/Waldoは続けます", monitor.logs)

    def test_a_rejoin_goes_back_to_zero_of_two(self):
        monitor = self._monitor()
        self._live(monitor, self.LIVED, self.LIVED)
        self._live(monitor, self.NEXT_JOIN)
        self.assertEqual(monitor.st.open_special_round_wins, 0)
        self.assertIn("[窓1] 2クラ: 別のインスタンスに入ったので 0/2 に戻します", monitor.logs)
        self.assertFalse(any("0/3" in m for m in monitor.logs))
        self._live(monitor, self.LIVED)
        self.assertIn("[窓1] 生存数: 1/2", monitor.logs)

    def test_a_rejoin_after_a_third_life_still_says_two(self):
        monitor = self._monitor()
        self._live(monitor, self.LIVED, self.LIVED, self.LIVED)
        self._live(monitor, self.NEXT_JOIN)
        self.assertEqual(monitor.st.open_special_round_wins, 0)
        self.assertIn("[窓1] 2クラ: 別のインスタンスに入ったので 0/2 に戻します", monitor.logs)

    def test_restoring_with_three_lives_never_prints_three_over_two(self):
        monitor = self._scan([self._round("Classic"), self.LIVED] * 3)
        self.assertEqual(monitor.st.open_special_round_wins, 3, "内部は 3 のまま")
        self.assertEqual(self._said(monitor), ["[窓1] 2クラ: 入室後のログから 生存3回・特殊ラウンドなし → 2/2"])
        self.assertFalse(any("3/2" in m for m in monitor.logs))

    def test_restoring_with_one_life(self):
        monitor = self._scan([self._round("Classic"), self.LIVED])
        self.assertEqual(monitor.st.open_special_round_wins, 1)
        self.assertEqual(self._said(monitor), ["[窓1] 2クラ: 入室後のログから 生存1回・特殊ラウンドなし → 1/2"])

    def test_restoring_with_a_special_round_and_two_twilights(self):
        monitor = self._scan([self._round("Classic"), self._round("Fog")])
        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(self._said(monitor), ["[窓1] 2クラ: 入室後に Fog → 2勝扱い"])
        monitor = self._scan([self._round("Twilight"), self._round("Classic"), self._round("Twilight")])
        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(self._said(monitor), ["[窓1] 2クラ: 入室後に Twilight 2回 → 2勝扱い"])

    def test_a_special_round_stops_a_two_clear_window_at_zero(self):
        monitor = self._monitor()
        monitor.st.round_type = "Fog"
        self._live(monitor, "Killers have been set - 1 0 0 // Round type is Fog")
        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(self._said(monitor), [
            "[窓1] 特殊ラウンド（Fog）を経験したので2勝扱い → 以降のDTM/Waldoはスキップします"])
        monitor.st.item_id = 1
        monitor.st.round_type = "Classic"
        _kind, is_continue, open_special = monitor._list_plan([config.DTM_ID], False)
        self.assertEqual((is_continue, open_special), (False, False))
        # 入り直すと 0 に戻り、また続行する
        self._live(monitor, self.NEXT_JOIN)
        self.assertEqual(monitor.st.open_special_round_wins, 0)
        _kind, is_continue, open_special = monitor._list_plan([config.DTM_ID], False)
        self.assertEqual((is_continue, open_special), (True, True))

    def test_a_special_round_at_two_wins_says_nothing(self):
        monitor = self._monitor()
        self._live(monitor, self.LIVED, self.LIVED)
        monitor.logs.clear()
        monitor.st.round_type = "Fog"
        self._live(monitor, "Killers have been set - 1 0 0 // Round type is Fog")
        self.assertEqual(monitor.st.open_special_round_wins, 3)
        self.assertEqual(self._said(monitor), [])

    def test_a_three_clear_window_keeps_every_log_text(self):
        monitor = self._monitor(two=False, three=True)
        self._live(monitor, self.LIVED, self.LIVED, self.LIVED, self.LIVED)
        self.assertEqual(self._said(monitor), [
            "[窓1] 生存数: 1/3", "[窓1] 生存数: 2/3", "[窓1] 生存数: 3/3",
            "[窓1] 🎉 3勝達成！以降のDTM/Waldoラウンドはスキップします"])
        self._live(monitor, self.NEXT_JOIN)
        self.assertIn("[窓1] 3クラ: 別のインスタンスに入ったので 0/3 に戻します", monitor.logs)
        monitor = self._monitor(two=False, three=True)
        monitor.st.round_type = "Fog"
        self._live(monitor, "Killers have been set - 1 0 0 // Round type is Fog")
        self.assertEqual(self._said(monitor), [
            "[窓1] 特殊ラウンド（Fog）を経験したので3勝扱い → 以降のDTM/Waldoはスキップします"])

    def test_with_both_off_nothing_is_said(self):
        monitor = self._monitor(two=False, three=False)
        self._live(monitor, self.LIVED, self.LIVED, self.LIVED)
        self.assertEqual(self._said(monitor), [])
