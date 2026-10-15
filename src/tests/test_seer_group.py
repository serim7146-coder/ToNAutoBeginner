"""FD（依頼者 2026-10-15）: グループ「Seer's Apostle」。公開範囲が「グループ（メンバーのみ）」なら private とすべて
同じ（操作・ラウンドの判断・霧の看破）。グループ+・公開・分からないなら、マクロ・霧の看破とも全面禁止（その他のグループ）"""
from tests.support import *  # noqa: F401,F403
import re
import FogEarlyRead

SEER = config.SEER_GROUP_ID
JOIN = "[Behaviour] Joining wrld_5b6e3b1f-a8c7-4b3d-9a0e-1f2a3b4c5d6e:12345~group({gid})~groupAccessType({access})~region(jp)"


def _parse(suffix):
    return LogMonitor.LogMonitor._parse_instance_type(suffix)


class TestTheType(unittest.TestCase):
    def test_members_is_seer_and_the_rest_are_other_group(self):
        self.assertEqual(_parse(f"~group({SEER})~groupAccessType(members)"), config.INSTANCE_SEER)
        for access in ("plus", "public", "nothing"):
            self.assertEqual(_parse(f"~group({SEER})~groupAccessType({access})"), config.INSTANCE_OTHER_GROUP, access)
        self.assertEqual(_parse(f"~group({SEER})"), config.INSTANCE_OTHER_GROUP, "公開範囲が分からない")

    def test_the_others_are_unchanged(self):
        self.assertEqual(_parse(f"~group({config.HOSHIIMO_GROUP_ID})~groupAccessType(members)"),
                         config.INSTANCE_HOSHIIMO)
        self.assertEqual(_parse(f"~group({config.YAKIIMO_GROUP_ID})~groupAccessType(plus)"), config.INSTANCE_YAKIIMO)
        self.assertEqual(_parse(f"~group({config.EMERALD_CITY_GROUP_ID})~groupAccessType(members)"),
                         config.INSTANCE_EMERALD_CITY)
        self.assertEqual(_parse("~group(grp_0000ffff-0000-0000-0000-000000000000)~groupAccessType(members)"),
                         config.INSTANCE_OTHER_GROUP, "ほかのグループはメンバーのみでも今どおり")
        self.assertEqual(_parse("~private(usr_x)"), config.INSTANCE_PRIVATE)
        self.assertEqual(_parse(""), config.INSTANCE_PUBLIC)

    def test_the_values(self):
        self.assertEqual((config.INSTANCE_SEER, config.INSTANCE_OTHER_GROUP), ("Seer's Apostle", "その他のグループ"))
        self.assertEqual(config.SEER_GROUP_ID, "grp_71fee430-5156-41e0-9623-e36860058af8")
        self.assertEqual(config.PRIVATE_LIKE_INSTANCES, {config.INSTANCE_PRIVATE, config.INSTANCE_SEER})
        self.assertNotIn(config.INSTANCE_SEER, GroupRound.GROUP_INSTANCES, "自爆のグループ判定には入れない")

    def test_no_check_compares_with_private_directly(self):
        """private と同じに扱うところは PRIVATE_LIKE_INSTANCES で見る（直接 == INSTANCE_PRIVATE で見ない）"""
        for path in Path(config.__file__).parent.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"[=!]= *config\.INSTANCE_PRIVATE|in \(config\.INSTANCE_PRIVATE", src), [],
                             path.name)


class TestTheJoiningLine(unittest.TestCase):
    def _join(self, access):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10), {}, logs.append, window_idx=1)
        monitor._running = True
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(JOIN.format(gid=SEER, access=access))
        return monitor, logs

    def test_members(self):
        monitor, logs = self._join("members")
        self.assertEqual(monitor.st.instance_type, config.INSTANCE_SEER)
        self.assertTrue(monitor._action.can_operate())
        self.assertIn("[窓1] インスタンスタイプ: Seer's Apostle（グループオンリー → private と同じに扱う）", logs)

    def test_plus_public_unknown(self):
        for access in ("plus", "public", "x"):
            monitor, logs = self._join(access)
            self.assertEqual(monitor.st.instance_type, config.INSTANCE_OTHER_GROUP, access)
            self.assertFalse(monitor._action.can_operate(), access)
            self.assertIn("[窓1] インスタンスタイプ: その他のグループ（Seer's Apostle・グループオンリーでない → "
                          "マクロ・霧の看破はしない）", logs)

    def test_a_plain_group_has_no_note(self):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10), {}, logs.append, window_idx=1)
        monitor._running = True
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(JOIN.format(gid="grp_0000ffff-0000-0000-0000-000000000000", access="members"))
        self.assertIn("[窓1] インスタンスタイプ: その他のグループ", logs)


class TestTheFogEarlyRead(unittest.TestCase):
    def setUp(self):
        FogEarlyRead.set_early_read_enabled(True)
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)

    def test_members_is_allowed_like_private(self):
        self.assertTrue(FogEarlyRead.early_read_allowed(LogParser.ACCESS_GROUP_MEMBERS, config.INSTANCE_SEER))
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10), {}, lambda m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_SEER
        monitor.st.instance_access = LogParser.ACCESS_GROUP_MEMBERS
        self.assertTrue(monitor._may_show_fog_info(), "窓の看破の判定も通る")
        monitor.st.instance_type = config.INSTANCE_OTHER_GROUP
        self.assertFalse(monitor._may_show_fog_info())

    def test_the_rest_is_not(self):
        for access in (LogParser.ACCESS_GROUP_PLUS, LogParser.ACCESS_GROUP_PUBLIC, LogParser.ACCESS_UNKNOWN):
            self.assertFalse(FogEarlyRead.early_read_allowed(access, config.INSTANCE_OTHER_GROUP), access)
        self.assertFalse(FogEarlyRead.early_read_allowed(LogParser.ACCESS_GROUP_MEMBERS, config.INSTANCE_EMERALD_CITY),
                         "ほかのグループのメンバーのみは今どおり")
        self.assertTrue(FogEarlyRead.early_read_allowed(LogParser.ACCESS_FRIENDS, config.INSTANCE_PRIVATE), "今どおり")
        FogEarlyRead.set_early_read_enabled(False)
        self.assertFalse(FogEarlyRead.early_read_allowed(LogParser.ACCESS_GROUP_MEMBERS, config.INSTANCE_SEER),
                         "許可のボタンが OFF なら看破しない")


class TestTheDecisionsAreLikePrivate(unittest.TestCase):
    """private で通る判断を Seer's Apostle（メンバーのみ）でも通す。その他のグループは操作しない"""

    def _monitor(self, itype, **cfg):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10, do_skip=True, auto_begin=True, **cfg), {},
                                        lambda m: None, window_idx=1)
        monitor.st.instance_type = itype
        monitor.st.round_type = "Classic"
        return monitor

    def test_round_settings(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_SEER):
            m = self._monitor(itype, continue_rounds={"Classic"})
            self.assertEqual(m._plan("Classic", [5], False), ("continue_rounds",), itype)
            m = self._monitor(itype, skip_rounds={"Classic"})
            self.assertEqual(m._plan("Classic", [5], False), ("round_skip",), itype)
        m = self._monitor(config.INSTANCE_OTHER_GROUP, continue_rounds={"Classic"})
        self.assertEqual(m._plan("Classic", [5], False), ("restricted",), "その他のグループは操作しない")

    def test_sabotage_is_like_private(self):
        args = dict(round_type="Sabotage", terror_ids=[5], killers_round_type="Sabotage", sus_players=[])
        self.assertEqual(GroupRound.decide(config.INSTANCE_SEER, **args), GroupRound.decide(config.INSTANCE_PRIVATE, **args))
        self.assertEqual(GroupRound.decide(config.INSTANCE_OTHER_GROUP, **args), GroupRound.NORMAL)

    def test_operating_auto_begin_and_hands_free(self):
        SharedState.set_hands_free(True)
        self.addCleanup(SharedState.set_hands_free, False)
        for itype, expected in ((config.INSTANCE_PRIVATE, True), (config.INSTANCE_SEER, True),
                                (config.INSTANCE_OTHER_GROUP, False), (config.INSTANCE_EMERALD_CITY, False),
                                (config.INSTANCE_PUBLIC, False)):
            m = self._monitor(itype)
            self.assertEqual(ActionExecutor.can_operate_in(itype), expected, itype)
            self.assertEqual(m._action._hands_free(), expected, itype)
            self.assertEqual(m._auto_begin_active(), expected, itype)


if __name__ == "__main__":
    unittest.main()
