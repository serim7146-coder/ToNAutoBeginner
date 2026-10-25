"""GT（依頼者 2026-10-25）: グループ「実績・アイテム集会」。Group+・グループオンリーのインスタンスでは、判定は private と同じ
（続行リスト・窓ごとの表・ラウンド指定・3クラ・Glorbo）にして、実操作は自動自爆（do_skip）だけ。霧の看破は許可しない。
ほかのアクセス（公開・分からない）は「その他のグループ」のまま全面禁止"""
from tests.support import *  # noqa: F401,F403
import FogEarlyRead
import ContinueTable
from tests import test_fog
from tests.test_continue_table import _MonitorCase, KEY, BASE, DFL, DIS, _body

ACH = config.INSTANCE_ACHIEVEMENT
GID = config.ACHIEVEMENT_GROUP_ID
JOIN = "[Behaviour] Joining wrld_5b6e3b1f-a8c7-4b3d-9a0e-1f2a3b4c5d6e:12345~group({gid})~groupAccessType({access})~region(jp)"


def _parse(suffix):
    return LogMonitor.LogMonitor._parse_instance_type(suffix)


class TestTheType(unittest.TestCase):
    def test_plus_and_members_are_achievement(self):
        for access in ("plus", "members"):
            self.assertEqual(_parse(f"~group({GID})~groupAccessType({access})"), ACH, access)

    def test_public_and_unknown_are_other_group(self):
        for access in ("public", "nothing"):
            self.assertEqual(_parse(f"~group({GID})~groupAccessType({access})"), config.INSTANCE_OTHER_GROUP, access)
        self.assertEqual(_parse(f"~group({GID})"), config.INSTANCE_OTHER_GROUP, "公開範囲が分からない")

    def test_another_group_with_plus_is_other_group(self):
        other = "grp_0000ffff-0000-0000-0000-000000000000"
        for access in ("plus", "members", "public"):
            self.assertEqual(_parse(f"~group({other})~groupAccessType({access})"), config.INSTANCE_OTHER_GROUP, access)

    def test_the_others_are_unchanged(self):
        self.assertEqual(_parse(f"~group({config.HOSHIIMO_GROUP_ID})~groupAccessType(plus)"), config.INSTANCE_HOSHIIMO)
        self.assertEqual(_parse(f"~group({config.YAKIIMO_GROUP_ID})~groupAccessType(plus)"), config.INSTANCE_YAKIIMO)
        self.assertEqual(_parse(f"~group({config.EMERALD_CITY_GROUP_ID})~groupAccessType(plus)"),
                         config.INSTANCE_EMERALD_CITY)
        self.assertEqual(_parse(f"~group({config.SEER_GROUP_ID})~groupAccessType(members)"), config.INSTANCE_SEER)
        self.assertEqual(_parse(f"~group({config.SEER_GROUP_ID})~groupAccessType(plus)"), config.INSTANCE_OTHER_GROUP)
        self.assertEqual(_parse("~private(usr_x)"), config.INSTANCE_PRIVATE)
        self.assertEqual(_parse(""), config.INSTANCE_PUBLIC)

    def test_the_sets(self):
        self.assertEqual((ACH, GID), ("実績・アイテム集会", "grp_4066ddbc-28e6-4e02-9148-2d5d912f9a4a"))
        self.assertEqual(config.JUDGE_LIKE_INSTANCES, config.PRIVATE_LIKE_INSTANCES | {ACH})
        self.assertNotIn(ACH, config.PRIVATE_LIKE_INSTANCES, "実操作は private だけ")
        self.assertNotIn(ACH, GroupRound.GROUP_INSTANCES, "干し芋・焼き芋のグループ判定には入れない")

    def test_the_joining_line(self):
        for access, expected in (("plus", ACH), ("members", ACH), ("public", config.INSTANCE_OTHER_GROUP)):
            logs = []
            monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10), {}, logs.append, window_idx=1)
            monitor._running = True
            with patch.object(LogMonitor.threading, "Thread"):
                monitor._process(JOIN.format(gid=GID, access=access))
            self.assertEqual(monitor.st.instance_type, expected, access)
            self.assertFalse(monitor._action.can_operate(), access)
            if expected == ACH:
                self.assertIn(f"[窓1] インスタンスタイプ: {ACH}（Group+／グループオンリー → 自動自爆だけ）", logs)
            else:
                self.assertIn("[窓1] インスタンスタイプ: その他のグループ", logs)


class _AchCase(_MonitorCase):
    """判定をそのまま走らせ、始めたスレッドの仕事の名前を集める"""

    def setUp(self):
        super().setUp()
        self.started = []

    def _ach(self, keep_on=None, itype=ACH, **kw):
        monitor = self._monitor(keep_on if keep_on is not None else {}, instance_type=itype, **kw)
        monitor._start_daemon = lambda target, *a: self.started.append(getattr(target, "__name__", str(target)))
        return monitor

    def _decide(self, monitor):
        with patch.object(PlaySound, "play_sound") as play:
            monitor._decide(monitor.st.round_type)
        return play


class TestJudging(_AchCase):
    def test_unlisted_terror_is_skipped_by_the_key_hold(self):
        monitor = self._ach({KEY: {1}})
        self._decide(monitor)
        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(self.started, ["do_skip"])

    def test_the_skip_follows_do_skip_off(self):
        monitor = self._ach({KEY: {1}})
        monitor.cfg.do_skip = False
        self._decide(monitor)
        self.assertEqual(self.started, [])

    def test_listed_terror_continues_with_announcement_and_no_operation(self):
        monitor = self._ach({KEY: {7}})
        play = self._decide(monitor)
        self.assertTrue(monitor.st.is_continue_round)
        play.assert_called_once_with("continue.mp3", kind="continue")
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】"), monitor.logs)
        SharedState.continue_round_start.assert_called_once()        # 他窓フリーズは干し芋と同じ
        self.assertEqual(self.started, [], "Begin・アイテム取得・移動などの仕事は始めない")

    def test_it_is_the_same_plan_as_private(self):
        for keep_on in ({KEY: {7}}, {KEY: {1}}):
            ach = self._ach(keep_on)
            private = self._ach(keep_on, itype=config.INSTANCE_PRIVATE)
            self.assertEqual(ach._plan("Classic", [7], False), private._plan("Classic", [7], False))

    def test_round_skip_setting(self):
        monitor = self._ach({KEY: {7}})
        monitor.cfg.skip_rounds = {"Classic"}
        self.assertEqual(monitor._plan("Classic", [7], False), ("round_skip",))
        self._decide(monitor)
        self.assertEqual(self.started, ["do_skip"])
        self.assertTrue(any("ラウンド指定自爆:" in m for m in monitor.logs), monitor.logs)

    def test_round_all_continue_setting(self):
        monitor = self._ach({KEY: {1}})
        monitor.cfg.continue_rounds = {"Classic"}
        self.assertEqual(monitor._plan("Classic", [7], False), ("continue_rounds",))
        self._decide(monitor)
        self.assertTrue(monitor.st.all_continue_round)
        self.assertEqual(self.started, [])

    def test_default_continue_overrides_the_round_skip(self):
        ContinueTable.set_current(choice_store(default_presets={BASE: _body({KEY: [7]})},
                                               windows={1: {DIS: BASE, DFL: BASE}}))
        monitor = self._ach({KEY: {1}})
        monitor.cfg.skip_rounds = {"Classic"}
        play = self._decide(monitor)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(self.started, [])
        play.assert_called_once()
        self.assertTrue(self._judgement_line(monitor).endswith("【プレイ】（デフォ続行）"), monitor.logs)

    def test_default_continue_and_disabled_tables_apply(self):
        ContinueTable.set_current(choice_store(disabled_presets={BASE: _body({KEY: [7]})},
                                               default_presets={BASE: _body({KEY: [8]})},
                                               windows={1: {DIS: BASE, DFL: BASE}}))
        self.assertEqual(self._result_of(self._ach({KEY: {7}})), "skip", "続行無効")
        self.assertEqual(self._result_of(self._ach({KEY: {1}}, ids=(8,))), "continue", "デフォ続行")

    def test_dtm_continues_but_does_not_move(self):
        monitor = self._ach({KEY: {1}}, ids=(config.DTM_ID,))
        monitor.cfg.cancel_afk = True
        plan = monitor._plan("Classic", [config.DTM_ID], False)
        self.assertEqual(plan, ("list", True, True))
        self._decide(monitor)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(self.started, [], "3クラの AFK 解除ループ（移動）は private だけ")
        self.assertFalse(monitor.st.is_open_special_round_round)

    def test_glorbo_continues_without_the_afk_loop(self):
        monitor = self._ach({KEY: {config.GLORBO_ID}}, ids=(config.GLORBO_ID,))
        self.assertEqual(monitor._plan("Classic", [config.GLORBO_ID], False), ("glorbo",))
        monitor.cfg.skip_rounds = {"Classic"}
        play = self._decide(monitor)
        self.assertTrue(monitor.st.is_continue_round)
        play.assert_called_once()
        self.assertEqual(self.started, [], "AFK 解除ループ（移動）は private だけ")
        # private なら回る（比較）
        private = self._ach({KEY: {config.GLORBO_ID}}, itype=config.INSTANCE_PRIVATE, ids=(config.GLORBO_ID,))
        self._decide(private)
        self.assertEqual(self.started, ["do_open_special_round_loop"])

    def test_sabotage_uses_the_same_rule_as_private(self):
        args = dict(round_type="Sabotage", terror_ids=[5], killers_round_type="Sabotage", sus_players=[])
        self.assertEqual(GroupRound.decide(ACH, **args), GroupRound.decide(config.INSTANCE_PRIVATE, **args))
        wishes = {"A": {GroupRound.SABOTAGE_MURDER_KEY: {5}}}
        args.update(sus_players=["A"], host_wishes=wishes, follow_host=True)
        self.assertEqual(GroupRound.decide(ACH, **args), GroupRound.SKIP)
        self.assertEqual(GroupRound.decide(ACH, **args), GroupRound.decide(config.INSTANCE_PRIVATE, **args))

    def test_the_host_list_is_required_before_skipping_with_others_present(self):
        for itype in (config.INSTANCE_PRIVATE, ACH, config.INSTANCE_OTHER_GROUP):
            monitor = self._ach({}, itype=itype)
            monitor._others_present = lambda: True
            self.assertEqual(monitor._needs_host_list(), itype != config.INSTANCE_OTHER_GROUP, itype)

    def test_hands_free_is_not_effective(self):
        SharedState.set_hands_free(True)
        monitor = self._ach({KEY: {7}})
        self.assertFalse(monitor._hands_free())
        self.assertFalse(monitor._action._hands_free())
        self.assertEqual(self._result_of(monitor), "continue")
        self.assertNotEqual(monitor._plan("Classic", [1], False)[0], "hands_free")

    def test_the_suicide_cancel_key_still_works(self):
        monitor = self._ach({KEY: {1}})
        monitor.st.in_round = True
        monitor.st.round_seq = 3
        self.assertEqual(monitor._action.cancel_suicide(), "marked")
        self.assertEqual(monitor.st.suicide_cancelled_round, 3)


class TestRestrictedUnchanged(_AchCase):
    def test_other_group_public_emerald_stay_restricted(self):
        for itype in (config.INSTANCE_OTHER_GROUP, config.INSTANCE_PUBLIC, config.INSTANCE_EMERALD_CITY):
            monitor = self._ach({KEY: {7}}, itype=itype)
            self.assertEqual(monitor._plan("Classic", [7], False), ("restricted",), itype)
            monitor.cfg.continue_rounds = {"Classic"}
            self.assertEqual(monitor._plan("Classic", [7], False), ("restricted",), itype)
        monitor = self._ach({KEY: {1}}, itype=config.INSTANCE_OTHER_GROUP)
        self._decide(monitor)
        self.assertEqual(self.started, [])


class TestNoOperation(unittest.TestCase):
    def _executor(self):
        st = WindowState(instance_type=ACH, round_seq=4)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x10, osc_port=9000), st, lambda: True,
                                           lambda _m: None, auto_begin_active=lambda: True)
        return st, ex

    def test_cannot_operate_and_auto_begin_is_off(self):
        self.assertFalse(ActionExecutor.can_operate_in(ACH))
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10, auto_begin=True), {}, lambda m: None, window_idx=1)
        monitor.st.instance_type = ACH
        self.assertFalse(monitor._auto_begin_active())
        self.assertFalse(monitor._item_begin_mode_active())

    def test_moves_item_fetch_run_respawn_and_drop_send_nothing(self):
        st, ex = self._executor()

        class Osc:
            def __init__(self):
                self.sent = []

            def __getattr__(self, name):
                return lambda *a, **k: self.sent.append(name) or True
        osc = Osc()
        ex._osc = osc
        ex._speed_ready.set()
        SharedState.set_item_fetch(True)
        SharedState.set_run_respawn(True)
        SharedState.set_continue_drop_item(True)
        self.addCleanup(SharedState.set_item_fetch, False)
        self.addCleanup(SharedState.set_run_respawn, False)
        self.addCleanup(SharedState.set_continue_drop_item, False)
        st.last_lost_item_id = 29
        st.item_id, st.waiting_for_equip = 0, True
        with patch.object(ItemFetch, "shop_for", return_value="Survival"), \
             patch.object(ItemFetch, "available", return_value=True), \
             patch.object(WindowOperator, "click") as click, \
             patch.object(WindowOperator, "hold_key_background") as hold, \
             patch.object(WindowOperator, "borrow_front") as borrow:
            self.assertFalse(ex.move("forward", 1.0))
            self.assertIsNone(ex.item_fetch_target(), "アイテム自動取得")
            ex.do_run_respawn(st.round_seq)
            ex.drop_at_round_end(st.round_seq, "死んだ")
            ex.do_after_round()
        self.assertEqual(osc.sent, [])
        click.assert_not_called()
        hold.assert_not_called()
        borrow.assert_not_called()

    def test_the_window_position_is_remembered_like_any_instance(self):
        """窓の位置を戻すのはどのインスタンスでも行う（依頼者 2026-10-25）。続行ラウンドの始まりで窓の位置を覚える"""
        SharedState.set_continue_restore_window(True)
        self.addCleanup(SharedState.set_continue_restore_window, False)
        rect = (10, 20, 500, 400)
        for itype in (ACH, config.INSTANCE_HOSHIIMO):
            st, ex = self._executor()
            st.instance_type = itype
            with patch.object(WindowOperator, "window_state", return_value="normal"), \
                 patch.object(WindowOperator, "window_rect", return_value=rect):
                ex.remember_continue_window()
            self.assertEqual(ex._continue_rect, rect, itype)


class TestFogEarlyReadIsNotAllowed(test_fog.TestFogEarlyReadAnswerCheck):
    """看破の行を読んでも判定に使わない（霧の看破のボタンが ON でも）"""

    def setUp(self):
        super().setUp()
        FogEarlyRead.set_early_read_enabled(True)
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)

    def _monitor(self, access="group_plus"):
        monitor = super()._monitor(access)
        monitor.st.instance_type = ACH
        return monitor

    def test_allowed_is_false_for_both_accesses(self):
        for access in (LogParser.ACCESS_GROUP_PLUS, LogParser.ACCESS_GROUP_MEMBERS):
            self.assertFalse(FogEarlyRead.early_read_allowed(access, ACH), access)
            monitor = self._monitor(access)
            self.assertFalse(monitor._may_show_fog_info(), access)

    def test_the_read_lines_are_not_used_for_the_decision(self):
        for access in (LogParser.ACCESS_GROUP_PLUS, LogParser.ACCESS_GROUP_MEMBERS):
            monitor = self._monitor(access)
            where, lines, reveal, public, _expected = FOG_EARLY_READ_ROUNDS[0]
            logs_before_reveal = self._round(monitor, lines, reveal)
            self.assertFalse(any("判定:" in m or "看破" in m or "自爆" in m for m in logs_before_reveal),
                             f"{access}: 公開の前に何も出さない・判定しない {logs_before_reveal}")
            self.assertFalse(monitor.st.is_continue_round, access)

    def test_private_with_the_same_lines_does_read(self):
        """対照: 同じ行を private（招待）で読むと、公開の前に看破の行が出る"""
        monitor = super()._monitor("invite")
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        where, lines, reveal, public, _expected = FOG_EARLY_READ_ROUNDS[0]
        logs_before_reveal = self._round(monitor, lines, reveal)
        self.assertTrue(any("看破" in m for m in logs_before_reveal), logs_before_reveal)


for _name in [n for n in dir(test_fog.TestFogEarlyReadAnswerCheck) if n.startswith("test")]:
    if _name not in ("test_allowed_is_false_for_both_accesses", "test_the_read_lines_are_not_used_for_the_decision",
                     "test_private_with_the_same_lines_does_read"):
        setattr(TestFogEarlyReadIsNotAllowed, _name, None)


if __name__ == "__main__":
    unittest.main()
