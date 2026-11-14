"""ToN ListTool 2.21.3 以降の host_local_state.bin（人ごとの続行リストの新しい保存先）を読む。

一時フォルダの偽のファイルだけを使う（本物の ToN ListTool のフォルダは読まない・書かない）。
DPAPI は本物を呼ばない（MatchTNL._dpapi_unprotect を、偽のヘッダを外すだけの関数に差し替える）。プロセスの見張りは偽。
"""
from tests.support import *  # noqa: F401,F403
import types

CLASSIC = "Classic/クラシック"
FOG = "Fog/霧"
RANDOMIZER = "Randomizer/Randomizer"
RANDOMIZER_NEW = "Randomizer/ランダマイザー"
FOG_ALT = "Fog (Alternate)/霧 (Alternate)"
GHOST_ALT = "Ghost (Alternate)/ゴースト (Alternate)"


def bits_text(ids) -> str:
    """ListTool の wish_bits の書き方（64 ビット整数を上位桁から 16 進 16 桁にして ":" 区切り。
    バイト列は下位バイトから並ぶので、8 バイトごとに逆順にしてから 16 進にする）"""
    raw = wish_bits(ids)
    return ":".join(raw[i:i + 8][::-1].hex() for i in range(0, 40, 8))


def person(name, rounds, **extra):
    entry = {"vrc_name": name, "original_name": name, "memo": "", "created_at": "2026-11-14 11:30:42",
             "is_visible": False,
             "wish_bits": {key: (ids if isinstance(ids, str) else bits_text(ids))
                           for key, ids in rounds.items()}}
    entry.update(extra)
    return entry


class LocalStateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bin = str(self.dir / "host_local_state.bin")
        patcher = patch.object(MatchTNL, "_dpapi_unprotect", side_effect=fake_dpapi_unprotect)
        self.unprotect = patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, tabs, **kw):
        write_host_local_state(self.bin, {"version": 6, "tabs": tabs}, **kw)

    def load(self, tabs):
        self.write(tabs)
        return MatchTNL.load_host_local_state(self.bin)


class TestLoadHostLocalState(LocalStateTestCase):
    def test_participants_and_waiting_build_the_same_result_as_the_sqlite_reader(self):
        """正常: 参加者・待機・wish_bits から keepOn_set と wishes が組め、meta のキーも sqlite 版と同じ"""
        members = [(0, "alice_test", {CLASSIC: {1, 2}, FOG: {3}}),
                   (0, "bob_test", {CLASSIC: {2, 5}}),
                   (1, "carol_test", {FOG: {4}}),
                   (0, "dave_test", {CLASSIC: set()})]       # 全部 OFF でも「リストを持つ人」
        db = str(self.dir / "host_state.sqlite3")
        write_host_state(db, [members])
        expected = MatchTNL.load_host_state(db)

        def entries(section):
            return [person(name, {k: v for k, v in rounds.items()})
                    for s, name, rounds in members if s == section]
        got = self.load([{"name": "タブ 1", "participants": entries(0), "waiting": entries(1)}])

        self.assertEqual(got[0], expected[0])
        self.assertEqual(got[2], expected[2])
        self.assertEqual(got[1], expected[1])
        self.assertEqual(set(got[1]), {"participants", "listed", "participant_names", "tabs",
                                       "tabs_data", "host_self"})
        self.assertEqual(got[0], {CLASSIC: {1, 2, 5}, FOG: {3}})        # 待機は畳まない
        self.assertEqual(got[1]["participants"], 3)
        self.assertEqual(got[1]["listed"], 4)
        self.assertEqual(got[2]["carol_test"], {FOG: {4}})
        self.assertEqual(got[2]["dave_test"], {})

    def test_the_tab_data_is_split_per_tab(self):
        got = self.load([
            {"participants": [person("a_test", {CLASSIC: {1}})], "waiting": []},
            {"participants": [person("b_test", {CLASSIC: {2}})],
             "waiting": [person("c_test", {FOG: {3}})]}])
        tabs_data = got[1]["tabs_data"]
        self.assertEqual(got[1]["tabs"], 2)
        self.assertEqual(tabs_data[0]["participants"], {"a_test"})
        self.assertEqual(tabs_data[1]["participants"], {"b_test"})
        self.assertEqual(tabs_data[1]["waiting"], {"c_test"})
        self.assertEqual(tabs_data[1]["keepOn"], {CLASSIC: {2}})
        self.assertEqual(tabs_data[1]["wishes"], {"b_test": {CLASSIC: {2}}, "c_test": {FOG: {3}}})

    def test_the_hex_text_becomes_40_bytes_and_known_bits_become_known_ids(self):
        text = ("0000008000180002:0400000000200000:0000000011003000:"
                "0000000000000000:0000000000000000")
        raw = MatchTNL._wish_bits_to_bytes(text)
        self.assertEqual(len(raw), 40)
        self.assertEqual(MatchTNL._wish_ids(raw), {1, 19, 20, 39, 85, 122, 140, 141, 152, 156})
        got = self.load([{"participants": [person("a_test", {CLASSIC: text})]}])
        # ListTool の 140・141・152・156 は独自の番号では Alternate の 1006・1007・1018・1022（The Knight of Toren・
        # Tragedy・Lisa・Ambush）。Classic のラウンドの枠に無いので外れ、1〜122 の 6 つが残る
        self.assertEqual(got[0], {CLASSIC: {1, 19, 20, 39, 85, 122}})

    def test_the_lowest_bit_of_the_64_bit_word_is_id_0_not_56(self):
        """要素は 64 ビット整数（上位桁から）。"…0001" の最下位ビットは番号 0 で、バイト列の並びを取り違えると 56 になる"""
        text = ":".join(["0000000000000001"] + ["0000000000000000"] * 4)
        self.assertEqual(MatchTNL._wish_ids(MatchTNL._wish_bits_to_bytes(text)), {0})

    def test_the_new_randomizer_key_goes_into_the_randomizer_slot(self):
        got = self.load([{"participants": [person("a_test", {RANDOMIZER_NEW: {5, 7}}),
                                           person("b_test", {RANDOMIZER: {7, 9}})]}])
        self.assertEqual(got[0], {RANDOMIZER: {5, 7, 9}})
        self.assertNotIn(RANDOMIZER_NEW, got[0])
        self.assertEqual(got[2]["a_test"], {RANDOMIZER: {5, 7}})

    def test_the_alias_applies_to_tnl_and_sqlite_readers_too(self):
        tnl = str(self.dir / "a.tnl")
        Path(tnl).write_text(json.dumps({"data": {RANDOMIZER_NEW: {"5": 1}}}), encoding="utf-8")
        self.assertEqual(MatchTNL.load_tnl(tnl)[0], {RANDOMIZER: {5}})
        db = str(self.dir / "host_state.sqlite3")
        write_host_state(db, [[(0, "a_test", {RANDOMIZER_NEW: {5}})]])
        self.assertEqual(MatchTNL.load_host_state(db)[0], {RANDOMIZER: {5}})

    def test_the_host_own_list_also_maps_the_new_randomizer_key(self):
        """主催者自身の user_save.json（_load_host_own_list 経由）にも 2.21.3 の新しいキーが来る"""
        user = self.dir / "user_save.json"
        user.write_text(json.dumps({"last_active": "host_test", "accounts": {
            "host_test": {"data": {RANDOMIZER_NEW: {"5": 1, "7": 1}, CLASSIC: {"1": 1}}}}}),
            encoding="utf-8")
        self.write([{"participants": [person("a_test", {CLASSIC: {2}})]}])
        keep_on, meta, wishes = MatchTNL.load_host_local_state(self.bin, str(user))
        self.assertEqual(keep_on[RANDOMIZER], {5, 7})
        self.assertNotIn(RANDOMIZER_NEW, keep_on)
        self.assertEqual(wishes["host_test"][RANDOMIZER], {5, 7})
        self.assertEqual(meta["host_self"], "host_test")
        self.assertEqual(meta["tabs_data"][0]["keepOn"][RANDOMIZER], {5, 7})

    def test_the_alternate_keys_are_ignored_as_before(self):
        got = self.load([{"participants": [person("a_test", {FOG_ALT: {10, 11}, GHOST_ALT: {20},
                                                              FOG: {1}})]}])
        self.assertEqual(got[0], {FOG: {1}})
        self.assertEqual(got[2]["a_test"], {FOG: {1}})
        self.assertEqual(got[1]["listed"], 1)

    def test_a_row_with_a_wrong_length_is_dropped_but_the_rest_is_read(self):
        short = bits_text({1})[:-2]                                    # 39 バイト
        got = self.load([{"participants": [
            person("a_test", {CLASSIC: {1, 2}, FOG: short}),           # 1 行だけ捨てる
            person("b_test", {CLASSIC: {3}}),
            person("c_test", {CLASSIC: short, FOG: "zz"})]}])          # 読める行が無い人
        self.assertEqual(got[0], {CLASSIC: {1, 2, 3}})
        self.assertEqual(got[1]["listed"], 2)
        self.assertEqual(got[1]["participants"], 3)
        self.assertNotIn("c_test", got[2])

    def test_the_dropped_count_is_written_to_the_debug_log_once(self):
        short = bits_text({1})[:-2]
        tabs = [{"participants": [person("a_test", {CLASSIC: short})]}]
        with patch.object(TerrorIds, "_debug") as debug, \
             patch.object(TerrorIds, "_reported", set()):
            self.load(tabs)
            self.load(tabs)
        self.assertEqual(debug.call_count, 1)
        self.assertNotIn("a_test", debug.call_args[0][0])

    def test_the_file_is_not_written_and_the_directory_gets_nothing_else(self):
        self.write([{"participants": [person("a_test", {CLASSIC: {1}})]}])
        before = Path(self.bin).read_bytes()
        MatchTNL.load_host_local_state(self.bin)
        self.assertEqual(Path(self.bin).read_bytes(), before)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["host_local_state.bin"])


class TestUnseal(LocalStateTestCase):
    def test_a_wrong_magic_is_a_value_error(self):
        self.write([], magic=b"XXXX")
        with self.assertRaises(ValueError):
            MatchTNL.load_host_local_state(self.bin)
        self.unprotect.assert_not_called()

    def test_a_file_without_the_dpapi_guid_is_a_value_error(self):
        Path(self.bin).write_bytes(b"LTHS\x01" + b"no provider guid here")
        with self.assertRaises(ValueError):
            MatchTNL.load_host_local_state(self.bin)

    def test_the_guid_is_searched_so_a_longer_header_still_reads(self):
        raw = b"LTHS\x01" + b"EXTRA-HEADER" + struct.pack("<I", 1) + MatchTNL._DPAPI_GUID + \
            gzip.compress(json.dumps({"tabs": []}).encode("utf-8"))
        Path(self.bin).write_bytes(raw)
        self.assertEqual(MatchTNL.load_host_local_state(self.bin)[1]["tabs"], 0)
        self.assertEqual(self.unprotect.call_args[0][0][:4], struct.pack("<I", 1))

    def test_a_dpapi_failure_is_a_value_error_and_is_not_swallowed(self):
        class FakeDpapiError(Exception):
            pass
        self.write([{"participants": [person("secret_name_test", {CLASSIC: {1}})]}])
        self.unprotect.side_effect = FakeDpapiError("(-2146893813, 'unprotect', 'secret_name_test')")
        with self.assertRaises(ValueError) as cm:
            MatchTNL.load_host_local_state(self.bin)
        self.assertIn("FakeDpapiError", str(cm.exception))
        self.assertNotIn("secret_name_test", str(cm.exception))      # 中身・名前をメッセージに入れない
        self.assertNotIn(self.bin, str(cm.exception))

    def test_a_broken_gzip_or_json_is_a_value_error(self):
        self.write([], seal=lambda body: b"not gzip")
        with self.assertRaises(ValueError):
            MatchTNL.load_host_local_state(self.bin)
        self.write([], seal=lambda body: gzip.compress(b"{broken json"))
        with self.assertRaises(ValueError):
            MatchTNL.load_host_local_state(self.bin)
        self.write([], seal=lambda body: gzip.compress(b"[1, 2]"))
        with self.assertRaises(ValueError):
            MatchTNL.load_host_local_state(self.bin)

    def test_the_wish_bits_text_must_be_40_bytes_of_hex(self):
        for bad in ("", "00", "zz" * 40, bits_text({1})[:-2], bits_text({1}) + ":00", None, 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                MatchTNL._wish_bits_to_bytes(bad)


class TestReadHostSource(LocalStateTestCase):
    """mainGUI._read_host_source: .bin があれば新形式、無ければ sqlite。失敗した stamp は覚えて解析し直さない"""

    def setUp(self):
        super().setUp()
        self.db = str(self.dir / "state" / "host_state.sqlite3")
        Path(self.db).parent.mkdir()
        for target, value in (("HOST_LOCAL_STATE_PATH", self.bin), ("HOST_STATE_PATH", self.db),
                              ("HOST_STATE_LEGACY_PATH", str(self.dir / "no_legacy.sqlite3")),
                              ("USER_SAVE_PATH", str(self.dir / "no_user_save.json")),
                              ("USER_SAVE_LEGACY_PATH", str(self.dir / "no_user_save_legacy.json"))):
            patcher = patch.object(config, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = types.SimpleNamespace(_process_cache=types.SimpleNamespace(watch=lambda name: True),
                                         _host_save_stamp=None, _user_save_cache=None)

    def test_the_bin_is_read_when_it_exists(self):
        write_host_state(self.db, [[(0, "old_sqlite_test", {CLASSIC: {9}})]])
        self.write([{"participants": [person("new_bin_test", {CLASSIC: {1}})]}])
        kind, value = mainGUI.App._read_host_source(self.app)
        self.assertEqual(kind, "loaded")
        keep_on, meta, wishes, stamp = value
        self.assertEqual(keep_on, {CLASSIC: {1}})
        self.assertEqual(meta["participant_names"], {"new_bin_test"})
        self.assertIsNone(stamp[3], "新形式に -wal は無い")

    def test_the_sqlite_is_read_when_the_bin_is_missing(self):
        write_host_state(self.db, [[(0, "old_sqlite_test", {CLASSIC: {9}})]])
        kind, value = mainGUI.App._read_host_source(self.app)
        self.assertEqual(kind, "loaded")
        self.assertEqual(value[0], {CLASSIC: {9}})
        self.assertEqual(value[1]["participant_names"], {"old_sqlite_test"})

    def test_the_bin_wins_even_if_the_sqlite_still_has_wishes(self):
        write_host_state(self.db, [[(0, "old_sqlite_test", {CLASSIC: {9}})]])
        self.write([{"participants": [person("new_bin_test", {CLASSIC: {1}})]}])
        with patch.object(MatchTNL, "load_host_state") as sqlite_load:
            mainGUI.App._read_host_source(self.app)
        sqlite_load.assert_not_called()

    def test_an_empty_list_in_the_bin_is_lost_so_it_falls_back_after_the_grace(self):
        self.write([{"participants": [], "waiting": []}])
        self.assertEqual(mainGUI.App._read_host_source(self.app),
                         ("lost", "続行リストを持つ人がいません（参加者・待機とも）"))

    def test_a_failed_stamp_is_remembered_and_not_parsed_again(self):
        self.write([], magic=b"XXXX")
        with patch.object(MatchTNL, "load_host_local_state",
                          wraps=MatchTNL.load_host_local_state) as load:
            first = mainGUI.App._read_host_source(self.app)
            second = mainGUI.App._read_host_source(self.app)
            third = mainGUI.App._read_host_source(self.app)
        self.assertEqual(first[0], "error")
        self.assertEqual(second, first)
        self.assertEqual(third, first)
        self.assertEqual(load.call_count, 1, "同じ stamp では 1 回しか解析しない")
        self.unprotect.assert_not_called()

        # 中身（大きさ・更新時刻）が変われば解析し直し、読めれば覚えを消す
        self.write([{"participants": [person("new_bin_test", {CLASSIC: {1}})]}])
        with patch.object(MatchTNL, "load_host_local_state",
                          wraps=MatchTNL.load_host_local_state) as load:
            kind, _value = mainGUI.App._read_host_source(self.app)
        self.assertEqual(kind, "loaded")
        self.assertEqual(load.call_count, 1)
        self.assertIsNone(self.app._host_local_fail_stamp)

    def test_a_dpapi_failure_in_the_bin_is_an_error_and_is_not_retried_on_the_same_stamp(self):
        self.write([{"participants": [person("a_test", {CLASSIC: {1}})]}])
        self.unprotect.side_effect = OSError("別の PC の鍵")
        first = mainGUI.App._read_host_source(self.app)
        second = mainGUI.App._read_host_source(self.app)
        self.assertEqual(first[0], "error")
        self.assertEqual(second, first)
        self.assertEqual(self.unprotect.call_count, 1)


if __name__ == "__main__":
    unittest.main()
