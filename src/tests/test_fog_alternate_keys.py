"""霧 (Alternate)・ゴースト (Alternate) のタブに付けた続行が効く（以前は読むときに捨てていた）。
ListTool にだけあるキーを、読み込むときに通常の Fog・Ghost のキーへ畳む。主催リスト（SQLite）・.tnl・.tnnl の 3 経路。
偽のファイルを一時フォルダに作って読むだけ。本物の ListTool のフォルダ・%APPDATA% には触れない"""
from tests.support import *  # noqa: F401,F403
import ContinueTable
import RoundDecision

FOG_ALT = "Fog (Alternate)/霧 (Alternate)"
GHOST_ALT = "Ghost (Alternate)/ゴースト (Alternate)"
FOG = "Fog/霧"
GHOST = "Ghost/ゴースト"
CLASSIC = "Classic/クラシック"

LT_1000 = 134       # ListTool の 134 は、どのキーでも独自の番号 1000
LT_4010 = 315       # ListTool の 315 は、独自の番号 4010（Fog・Midnight の枠）


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _host(self, rounds):
        """主催リスト（SQLite）を作って読む。rounds は {ラウンドのキー: {ListTool の番号}}。1 人・参加者"""
        db = self.dir / "host_state.sqlite3"
        write_host_state(db, [[(0, "ひと", rounds)]])
        keep_on, _meta, wishes = MatchTNL.load_host_state(str(db))
        return keep_on, wishes

    def _tnl(self, data):
        path = self.dir / "a.tnl"
        path.write_text(json.dumps({"list_name": "x", "data": data}), encoding="utf-8")
        return str(path)

    def _tnnl(self, targets):
        path = self.dir / "a.tnnl"
        path.write_text(json.dumps({"targets_dict": targets}), encoding="utf-8-sig")
        return str(path)


class TestFogAlternateTab(_Case):
    """① 霧 (Alternate) のタブに 134 がある → Fog/霧 に 1000 が入り、続行する"""

    def test_host_list_puts_1000_into_the_normal_fog_key(self):
        keep_on, wishes = self._host({FOG_ALT: {LT_1000}})
        self.assertEqual(keep_on, {FOG: {1000}})
        self.assertEqual(wishes["ひと"], {FOG: {1000}})

    def test_host_list_continues_by_either_round_name(self):
        keep_on, _wishes = self._host({FOG_ALT: {LT_1000}})
        self.assertTrue(RoundDecision.list_continues(keep_on, [1000], "Fog (Alternate)"))
        self.assertTrue(RoundDecision.list_continues(keep_on, [1000], "Fog"), "通常のキーで引いても同じ答え")
        self.assertFalse(RoundDecision.list_continues(keep_on, [1001], "Fog (Alternate)"), "付けていない番号は続行しない")

    def test_tnl_gives_the_same(self):
        keep_on, _meta = MatchTNL.load_tnl(self._tnl({FOG_ALT: {str(LT_1000): 1}}))
        self.assertEqual(keep_on, {FOG: {1000}})
        self.assertTrue(RoundDecision.list_continues(keep_on, [1000], "Fog (Alternate)"))
        self.assertTrue(RoundDecision.list_continues(keep_on, [1000], "Fog"))

    def test_tnnl_gives_the_same(self):
        got = ContinueTable.from_tnnl(self._tnnl({FOG_ALT: [LT_1000]}))
        self.assertEqual({k: set(v) for k, v in got.items()}, {FOG: {1000}})
        self.assertTrue(RoundDecision.list_continues(got, [1000], "Fog (Alternate)"))
        self.assertTrue(RoundDecision.list_continues(got, [1000], "Fog"))

    def test_tnl_import_into_the_table_gives_the_same(self):
        got = ContinueTable.from_tnl(self._tnl({FOG_ALT: {str(LT_1000): 1}}))
        self.assertEqual({k: set(v) for k, v in got.items()}, {FOG: {1000}})

    def test_the_listtool_only_key_does_not_remain(self):
        """畳んだ結果、キーが増えない（Fog (Alternate) のキーが読み込み後のデータに残らない）"""
        keep_on, wishes = self._host({FOG_ALT: {LT_1000}, FOG: {5}})
        self.assertNotIn(FOG_ALT, keep_on)
        self.assertNotIn(FOG_ALT, wishes["ひと"])
        self.assertEqual(set(keep_on), {FOG})
        tnl, _meta = MatchTNL.load_tnl(self._tnl({FOG_ALT: {str(LT_1000): 1}, FOG: {"5": 1}}))
        self.assertEqual(set(tnl), {FOG})
        tnnl = ContinueTable.from_tnnl(self._tnnl({FOG_ALT: [LT_1000], FOG: [5]}))
        self.assertEqual(set(tnnl), {FOG})


class TestMergeNotOverwrite(_Case):
    """⑤ 寄せ先のキーにもともと番号があるとき、両方が残る"""

    def test_host_list_keeps_both(self):
        keep_on, wishes = self._host({FOG: {LT_4010}, FOG_ALT: {LT_1000}})
        self.assertEqual(keep_on, {FOG: {4010, 1000}})
        self.assertEqual(wishes["ひと"], {FOG: {4010, 1000}})

    def test_host_list_keeps_both_whatever_the_row_order(self):
        keep_on, _wishes = self._host({FOG_ALT: {LT_1000}, FOG: {LT_4010}})
        self.assertEqual(keep_on, {FOG: {4010, 1000}})

    def test_tnl_keeps_both(self):
        keep_on, _meta = MatchTNL.load_tnl(self._tnl({FOG: {str(LT_4010): 1}, FOG_ALT: {str(LT_1000): 1}}))
        self.assertEqual(keep_on, {FOG: {4010, 1000}})
        keep_on, _meta = MatchTNL.load_tnl(self._tnl({FOG_ALT: {str(LT_1000): 1}, FOG: {str(LT_4010): 1}}))
        self.assertEqual(keep_on, {FOG: {4010, 1000}})

    def test_tnnl_keeps_both(self):
        got = ContinueTable.from_tnnl(self._tnnl({FOG: [LT_4010], FOG_ALT: [LT_1000]}))
        self.assertEqual({k: set(v) for k, v in got.items()}, {FOG: {4010, 1000}})

    def test_two_people_are_merged_too(self):
        db = self.dir / "host_state.sqlite3"
        write_host_state(db, [[(0, "A", {FOG_ALT: {LT_1000}}), (0, "B", {FOG: {LT_4010}})]])
        keep_on, _meta, wishes = MatchTNL.load_host_state(str(db))
        self.assertEqual(keep_on, {FOG: {4010, 1000}})
        self.assertEqual(wishes["A"], {FOG: {1000}})
        self.assertEqual(wishes["B"], {FOG: {4010}})


class TestGhostAlternateTab(_Case):
    """⑥ ゴースト側も同じ"""

    def test_host_list(self):
        keep_on, _wishes = self._host({GHOST_ALT: {LT_1000}})
        self.assertEqual(keep_on, {GHOST: {1000}})
        self.assertTrue(RoundDecision.list_continues(keep_on, [1000], "Ghost (Alternate)"))
        self.assertTrue(RoundDecision.list_continues(keep_on, [1000], "Ghost"))

    def test_tnl_and_tnnl(self):
        keep_on, _meta = MatchTNL.load_tnl(self._tnl({GHOST_ALT: {str(LT_1000): 1}}))
        self.assertEqual(keep_on, {GHOST: {1000}})
        got = ContinueTable.from_tnnl(self._tnnl({GHOST_ALT: [LT_1000]}))
        self.assertEqual({k: set(v) for k, v in got.items()}, {GHOST: {1000}})

    def test_merge_with_the_normal_ghost(self):
        keep_on, _wishes = self._host({GHOST: {LT_1000 - 133}, GHOST_ALT: {LT_1000}})    # 1 は Classic の番号
        self.assertEqual(keep_on, {GHOST: {1, 1000}})

    def test_fog_and_ghost_stay_apart(self):
        keep_on, _wishes = self._host({FOG_ALT: {LT_1000}, GHOST_ALT: {LT_1000 + 1}})
        self.assertEqual(keep_on, {FOG: {1000}, GHOST: {1001}})


class TestNormalFogUnchanged(_Case):
    """⑦ 通常の Fog の判定が変わらない"""

    def test_a_normal_fog_list_reads_as_before(self):
        keep_on, _wishes = self._host({FOG: {5, 7}})
        self.assertEqual(keep_on, {FOG: {5, 7}})
        self.assertTrue(RoundDecision.list_continues(keep_on, [5], "Fog"))
        self.assertFalse(RoundDecision.list_continues(keep_on, [6], "Fog"))
        self.assertFalse(RoundDecision.list_continues(keep_on, [1000], "Fog (Alternate)"), "付けていない 1000 は続行しない")

    def test_a_list_without_the_alternate_keys_reads_as_before_in_all_three(self):
        keep_on, _meta = MatchTNL.load_tnl(self._tnl({FOG: {"5": 1, "6": 0}, CLASSIC: {"3": 1}}))
        self.assertEqual(keep_on, {FOG: {5}, CLASSIC: {3}})
        got = ContinueTable.from_tnnl(self._tnnl({FOG: [5], CLASSIC: [3]}))
        self.assertEqual({k: set(v) for k, v in got.items()}, {FOG: {5}, CLASSIC: {3}})

    def test_an_all_off_alternate_key_leaves_nothing(self):
        keep_on, _meta = MatchTNL.load_tnl(self._tnl({FOG_ALT: {str(LT_1000): 0}, FOG: {"5": 1}}))
        self.assertEqual(keep_on, {FOG: {5}})


if __name__ == "__main__":
    unittest.main()
