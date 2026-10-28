"""GF: ToN ListTool 2.17.4 の保存先の移動に合わせる。

主催リストは `state\\host_state.sqlite3`・主催者自身のリストは `lists\\user_save.json` に移った。
新しい場所を先に、無ければ前の場所（%APPDATA%\\ToN ListTool の直下）を読む。
一時フォルダの偽のファイルだけを使う（本物の ToN ListTool のフォルダは読まない・書かない）。
プロセスの見張りは偽。
"""
from tests.support import *  # noqa: F401,F403
import types


class TestFirstExisting(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.new = str(Path(self._dir.name) / "state" / "host_state.sqlite3")
        self.legacy = str(Path(self._dir.name) / "host_state.sqlite3")

    def _touch(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")

    def test_only_the_new_place_exists_gives_the_new_place(self):
        self._touch(self.new)
        self.assertEqual(mainGUI._first_existing(self.new, self.legacy), self.new)

    def test_only_the_legacy_place_exists_gives_the_legacy_place(self):
        self._touch(self.legacy)
        self.assertEqual(mainGUI._first_existing(self.new, self.legacy), self.legacy)

    def test_both_exist_gives_the_new_place(self):
        self._touch(self.new)
        self._touch(self.legacy)
        self.assertEqual(mainGUI._first_existing(self.new, self.legacy), self.new)

    def test_neither_exists_gives_the_new_place(self):
        self.assertEqual(mainGUI._first_existing(self.new, self.legacy), self.new)


class TestReadHostSourcePlaces(unittest.TestCase):
    """_read_host_source が新しい場所を先に、無ければ前の場所を読む"""

    LOADED = ({"Classic/クラシック": {1}}, {"listed": 1, "participants": 1}, {})

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        root = Path(self._dir.name)
        self.new_db = root / "state" / "host_state.sqlite3"
        self.old_db = root / "host_state.sqlite3"
        self.new_user = root / "lists" / "user_save.json"
        self.old_user = root / "user_save.json"
        self.calls = []

    def _put(self, path, text="{}"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _fake_load(self, db, user, user_save_raw=None):
        self.calls.append((db, user))
        return self.LOADED

    def _read(self):
        app = types.SimpleNamespace(_process_cache=types.SimpleNamespace(watch=lambda name: True),
                                    _host_save_stamp=None, _user_save_cache=None)
        with patch.object(config, "HOST_STATE_PATH", str(self.new_db)), \
             patch.object(config, "HOST_STATE_LEGACY_PATH", str(self.old_db)), \
             patch.object(config, "USER_SAVE_PATH", str(self.new_user)), \
             patch.object(config, "USER_SAVE_LEGACY_PATH", str(self.old_user)), \
             patch.object(MatchTNL, "load_host_state", side_effect=self._fake_load):
            return mainGUI.App._read_host_source(app)

    def test_loaded_from_the_new_places(self):
        self._put(self.new_db, "db")
        self._put(self.new_user)
        kind, value = self._read()
        self.assertEqual(kind, "loaded")
        self.assertEqual(self.calls, [(str(self.new_db), str(self.new_user))])

    def test_loaded_from_the_legacy_places_when_only_they_exist(self):
        self._put(self.old_db, "db")
        self._put(self.old_user)
        kind, value = self._read()
        self.assertEqual(kind, "loaded")
        self.assertEqual(self.calls, [(str(self.old_db), str(self.old_user))])

    def test_the_new_places_win_when_both_exist(self):
        for p in (self.new_db, self.old_db):
            self._put(p, "db")
        for p in (self.new_user, self.old_user):
            self._put(p)
        kind, value = self._read()
        self.assertEqual(kind, "loaded")
        self.assertEqual(self.calls, [(str(self.new_db), str(self.new_user))])

    def test_lost_when_neither_place_has_the_db(self):
        kind, value = self._read()
        self.assertEqual(kind, "lost")
        self.assertIn("host_state.sqlite3 がありません", value)
        self.assertEqual(self.calls, [])


class TestConfigPaths(unittest.TestCase):
    def test_the_new_places_are_under_state_and_lists(self):
        self.assertTrue(config.HOST_STATE_PATH.endswith(os.path.join("state", "host_state.sqlite3")))
        self.assertTrue(config.USER_SAVE_PATH.endswith(os.path.join("lists", "user_save.json")))

    def test_the_legacy_places_are_directly_under_the_folder(self):
        self.assertEqual(os.path.dirname(config.HOST_STATE_LEGACY_PATH), os.path.dirname(os.path.dirname(config.HOST_STATE_PATH)))
        self.assertEqual(os.path.dirname(config.USER_SAVE_LEGACY_PATH), os.path.dirname(os.path.dirname(config.USER_SAVE_PATH)))
        self.assertEqual(os.path.basename(config.HOST_STATE_LEGACY_PATH), "host_state.sqlite3")
        self.assertEqual(os.path.basename(config.USER_SAVE_LEGACY_PATH), "user_save.json")


if __name__ == "__main__":
    unittest.main()
