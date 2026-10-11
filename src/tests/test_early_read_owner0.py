"""看破で「持ち主 0 へ移った」行を使わない（DU。2026-10-11 14:53 窓1 の不具合）"""
from tests.support import *  # noqa: F401,F403
from tests import test_fog


class _Base(test_fog.TestFogEarlyReadAnswerCheck):
    """setUp・_monitor だけ借りる（親のテストは回さない）"""


for _name in [n for n in dir(test_fog.TestFogEarlyReadAnswerCheck) if n.startswith("test")]:
    setattr(_Base, _name, None)

# 時刻は親の _round（22:04:00 開始・22:04:41 unknown）に合わせる。並びは 2026-10-11 14:53〜14:54 窓1
P = "2026.09.21 22:0"
WITHERED = f"{P}4:42 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [36] WitheredBooni because x already owner"
CBS_TO_0 = f"{P}4:43 Debug      -  [NetworkProcessing] Transferred ownership of [59] ChristianBrutralSniper to 0"
BOX_TO_0 = f"{P}4:43 Debug      -  [NetworkProcessing] Transferred ownership of monsterDetectionBox to 0"
BARE_CBS_TO_0 = f"{P}4:43 Debug      -  [NetworkProcessing] Transferred ownership of ChristianBrutralSniper to 0"
REVEAL = f"{P}4:50 Debug      -  Killers have been revealed - 35 0 0 // Round type is Fog"


def _terrors_with_cbs():
    """看破の表（terrors.json の値を写した）。14:53 の2つだけ"""
    return ReadJson.normalize_terrors({"classic": [
        {"id": 35, "name": "Withered Bonnie", "terrors": ["Withered Bonnie"],
         "objects": ["WitheredBooni", "Ruinborn"]},
        {"id": 58, "name": "Christian Brutal Sniper", "terrors": ["Christian Brutal Sniper"],
         "objects": ["ChristianBrutralSniper"]},
    ]})


class TestOwnerZeroLinesAreNotRead(_Base):
    def setUp(self):
        super().setUp()
        p = patch.object(config, "TERRORS", _terrors_with_cbs())
        p.start()
        self.addCleanup(p.stop)

    def _fog(self, monitor, lines, reveal=REVEAL):
        self._round(monitor, lines, reveal)

    def test_window_1_at_1453(self):
        """WitheredBooni の行 → 一斉の「to 0」に CBS → 看破は Withered Bonnie のまま・void にならない・CBS に食い違いを付けない"""
        monitor = self._monitor()
        self._fog(monitor, [WITHERED, CBS_TO_0] + [BOX_TO_0] * 13, reveal=None)
        self.assertEqual(monitor.st.early_read_tid, 35)
        self.assertFalse(monitor.st.early_read_void)
        self.assertEqual(set(monitor.st.early_read_hits), {"witheredbooni"})
        monitor._process(REVEAL)
        self.assertEqual(self.trust.counts("christianbrutralsniper"), (0, 0), "CBS は答え合わせに入らない")
        self.assertTrue(self.trust.usable("christianbrutralsniper"))
        self.assertEqual(self.trust.counts("witheredbooni"), (1, 0))
        self.assertFalse(any("ChristianBrutralSniper" in m for m in monitor.logs), monitor.logs)

    def test_a_bare_to_0_is_not_read_either(self):
        monitor = self._monitor()
        self._fog(monitor, [WITHERED, BARE_CBS_TO_0], reveal=None)
        self.assertFalse(monitor.st.early_read_void)
        self.assertEqual(set(monitor.st.early_read_hits), {"witheredbooni"})

    def test_a_to_0_alone_does_not_read(self):
        monitor = self._monitor()
        self._fog(monitor, [CBS_TO_0], reveal=None)
        self.assertIsNone(monitor.st.early_read_tid)
        self.assertEqual(monitor.st.early_read_hits, {})

    def test_other_transfers_are_read_as_before(self):
        for line in (f"{P}4:42 Debug      -  [NetworkProcessing] Transferred ownership of [59] ChristianBrutralSniper to 20",
                     f"{P}4:42 Debug      -  [NetworkProcessing] Transferred ownership of ChristianBrutralSniper to 3",
                     f"{P}4:42 Debug      -  [NetworkProcessing] Transferred ownership of [59] ChristianBrutralSniper to serim01"):
            with self.subTest(line=line):
                monitor = self._monitor()
                self._fog(monitor, [line], reveal=None)
                self.assertEqual(monitor.st.early_read_tid, 58, line)


class TestTheParser(unittest.TestCase):
    def test_to_0_gives_no_name(self):
        N = LogParser.network_object_name
        self.assertEqual(N("[NetworkProcessing] Transferred ownership of [59] ChristianBrutralSniper to 0"), "")
        self.assertEqual(N("[NetworkProcessing] Transferred ownership of monsterDetectionBox to 0"), "")
        self.assertEqual(N("[NetworkProcessing] Transferred ownership of [9] Express Train to Hell to 0"), "")
        self.assertIsNone(LogParser.parse(CBS_TO_0))

    def test_other_targets_as_before(self):
        N = LogParser.network_object_name
        self.assertEqual(N("[NetworkProcessing] Transferred ownership of [86] WALPURGISNACHT to 20"), "WALPURGISNACHT")
        self.assertEqual(N("[NetworkProcessing] Transferred ownership of Waldo to 3"), "Waldo")
        self.assertEqual(N("[NetworkProcessing] Transferred ownership of [9] Express Train to Hell to 10"),
                         "Express Train to Hell")
        self.assertEqual(N("[NetworkProcessing] Transferred ownership of [9] Express Train to Hell to 0x"),
                         "Express Train to Hell", "0 で終わる別の相手（名前）は今どおり")
        self.assertEqual(N("[NetworkProcessing] Ignoring TrySetOwner attempt on [36] WitheredBooni because x already owner"),
                         "WitheredBooni")
