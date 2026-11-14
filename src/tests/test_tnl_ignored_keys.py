"""FV: tnl の読み込みでも Fog (Alternate)・Ghost (Alternate) の枠を読み飛ばす（件数を主催リストと合わせる）。
偽の tnl・偽の user_save.json を一時フォルダに作って読む。本物の ListTool のフォルダ・tnl は使わない"""
from tests.support import *  # noqa: F401,F403

_FOG_ALT = "Fog (Alternate)/霧 (Alternate)"
_GHOST_ALT = "Ghost (Alternate)/ゴースト (Alternate)"
_FOG = "Fog/霧"
_CLASSIC = "Classic.exe/Classic.exe"

_DATA = {
    _FOG_ALT: {"10": 1, "11": 1, "12": 1, "13": 0},
    _GHOST_ALT: {"20": 1, "21": 1, "22": 1},
    _FOG: {"1": 1, "2": 1, "3": 0},
    _CLASSIC: {"5": 1},
}


class TnlIgnoredKeysTest(unittest.TestCase):
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

    def test_load_tnl_skips_alternate_slots(self):
        keep, _meta = MatchTNL.load_tnl(self._tnl(_DATA))
        self.assertNotIn(_FOG_ALT, keep)
        self.assertNotIn(_GHOST_ALT, keep)
        self.assertEqual(keep[_FOG], {1, 2})
        self.assertEqual(keep[_CLASSIC], {5})
        self.assertEqual(len(keep), 2)
        self.assertEqual(sum(len(v) for v in keep.values()), 3)

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
        for key in set(keep_tnl) | set(keep_host):
            self.assertEqual(keep_tnl.get(key), keep_host.get(key))
        self.assertEqual(wishes["TestAccount"], keep_host)

    def test_load_tnl_without_alternate_slots_unchanged(self):
        data = {_FOG: {"1": 1, "2": 1}, _CLASSIC: {"5": 1}, "Ghost/ゴースト": {"7": 1}}
        keep, _meta = MatchTNL.load_tnl(self._tnl(data))
        self.assertEqual(keep, {_FOG: {1, 2}, _CLASSIC: {5}, "Ghost/ゴースト": {7}})

    def test_alternate_logs_still_judged_by_normal_fog_slot(self):
        keep, _meta = MatchTNL.load_tnl(self._tnl(_DATA))
        self.assertEqual(MatchTNL.LOG_TO_TNL["Fog (Alternate)"], _FOG)
        self.assertTrue(MatchTNL.should_continue(keep, _FOG, [1]))
        self.assertFalse(MatchTNL.should_continue(keep, _FOG, [10]))


if __name__ == "__main__":
    unittest.main()
