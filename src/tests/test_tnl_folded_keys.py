"""tnl の読み込みで、ListTool にだけあるラウンドのキー（Fog (Alternate)・Ghost (Alternate)）を通常の Fog・Ghost へ畳む。
（以前は捨てていた＝FV。霧 (Alternate) のタブに付けた続行が効かなかったので改めた）
偽の tnl・偽の user_save.json を一時フォルダに作って読む。本物の ListTool のフォルダ・tnl は使わない"""
from tests.support import *  # noqa: F401,F403

_FOG_ALT = "Fog (Alternate)/霧 (Alternate)"
_GHOST_ALT = "Ghost (Alternate)/ゴースト (Alternate)"
_FOG = "Fog/霧"
_GHOST = "Ghost/ゴースト"
_CLASSIC = "Classic.exe/Classic.exe"

# ListTool の番号。134〜136 は独自の番号の 1000〜1002（Alternate のテラー）にあたる
_DATA = {
    _FOG_ALT: {"134": 1, "135": 1, "136": 1, "137": 0},
    _GHOST_ALT: {"134": 1, "135": 1},
    _FOG: {"1": 1, "2": 1, "3": 0},
    _CLASSIC: {"5": 1},
}


class TnlFoldedKeysTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _write(self, name, obj):
        path = self.dir / name
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def _tnl(self, data):
        return self._write("fake.tnl", {"list_name": "test", "data": data})

    def test_load_tnl_folds_alternate_slots_into_the_normal_keys(self):
        keep, _meta = MatchTNL.load_tnl(self._tnl(_DATA))
        self.assertNotIn(_FOG_ALT, keep)
        self.assertNotIn(_GHOST_ALT, keep)
        self.assertEqual(keep[_FOG], {1, 2, 1000, 1001, 1002}, "通常の Fog の分と Fog (Alternate) の分が両方残る")
        self.assertEqual(keep[_GHOST], {1000, 1001})
        self.assertEqual(keep[_CLASSIC], {5})
        self.assertEqual(len(keep), 3, "キーは増えない")

    def test_load_tnl_equals_host_own_list(self):
        keep_tnl, _meta = MatchTNL.load_tnl(self._tnl(_DATA))
        save = self._write("user_save.json", {
            "last_active": "TestAccount",
            "accounts": {"TestAccount": {"data": _DATA}},
        })
        keep_host: dict = {}
        wishes: dict = {}
        self.assertEqual(MatchTNL._load_host_own_list(save, keep_host, wishes),
                         "TestAccount")
        self.assertEqual(keep_tnl, keep_host)
        self.assertEqual(wishes["TestAccount"], keep_host)

    def test_load_tnl_without_alternate_slots_unchanged(self):
        data = {_FOG: {"1": 1, "2": 1}, _CLASSIC: {"5": 1}, _GHOST: {"7": 1}}
        keep, _meta = MatchTNL.load_tnl(self._tnl(data))
        self.assertEqual(keep, {_FOG: {1, 2}, _CLASSIC: {5}, _GHOST: {7}})

    def test_alternate_logs_are_judged_by_the_normal_fog_slot(self):
        keep, _meta = MatchTNL.load_tnl(self._tnl(_DATA))
        self.assertEqual(MatchTNL.LOG_TO_TNL["Fog (Alternate)"], _FOG)
        self.assertTrue(MatchTNL.should_continue(keep, _FOG, [1]))
        self.assertTrue(MatchTNL.should_continue(keep, _FOG, [1000]), "霧 (Alternate) の分も同じキーで当たる")
        self.assertFalse(MatchTNL.should_continue(keep, _FOG, [1003]))

    def test_fold_round_key_only_moves_the_two_listtool_only_keys(self):
        self.assertEqual(MatchTNL.fold_round_key(_FOG_ALT), _FOG)
        self.assertEqual(MatchTNL.fold_round_key(_GHOST_ALT), _GHOST)
        self.assertEqual(MatchTNL.fold_round_key(_FOG), _FOG)
        self.assertEqual(MatchTNL.fold_round_key(_CLASSIC), _CLASSIC)


if __name__ == "__main__":
    unittest.main()
