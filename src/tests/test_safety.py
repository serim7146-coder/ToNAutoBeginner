"""テストの安全装置（本物の OSC・設定・ネットワークに触らない）"""
from tests.support import *  # noqa: F401,F403




class TestNoRealOsc(unittest.TestCase):
    """テストから本物の OSC を送らない・本物の受け口を開かない（窓1 の VRChat が 9000 で待っている）"""

    def setUp(self):
        self.sends, self.binds = list(_OscSocketTrap.sends), list(_OscSocketTrap.binds)

    def tearDown(self):
        _OscSocketTrap.sends[:] = self.sends                # このテストで足した分は全体の見張りに残さない
        _OscSocketTrap.binds[:] = self.binds

    def test_the_modules_use_the_trap(self):
        for mod in (OSCClient, OSCReceiver):
            self.assertIs(mod.socket, _OscSocketTrap.module, mod.__name__)

    def test_a_send_is_caught_not_sent(self):
        client = OSCClient.OSCClient(9000)
        self.assertTrue(client.send("/input/Jump", 0))
        self.assertEqual(len(_OscSocketTrap.sends), len(self.sends) + 1)
        self.assertEqual(_OscSocketTrap.sends[-1][0], ("127.0.0.1", 9000))
        with self.assertRaises(AssertionError):
            _OscSocketTrap.check()

    def test_a_bind_is_caught_not_opened(self):
        receiver = OSCReceiver.VelocityReceiver(9001)
        self.assertTrue(receiver.start())
        receiver.stop()
        self.assertEqual(_OscSocketTrap.binds[-1], ("127.0.0.1", 9001))
        with self.assertRaises(AssertionError):
            _OscSocketTrap.check()




class TestNoRealSettings(unittest.TestCase):
    """テスト中の設定の置き場所が、本物の %APPDATA%\\ToNAutoBeginner の下ではないこと"""

    def _assert_not_real(self, path):
        real = REAL_SETTINGS_DIR.resolve()
        self.assertNotEqual(Path(path).resolve().parent, real, path)
        self.assertNotIn(real, Path(path).resolve().parents, path)

    def test_the_settings_path_is_not_the_real_one(self):
        self._assert_not_real(config.SETTINGS_PATH)

    def test_the_fog_object_names_path_is_not_the_real_one(self):
        self._assert_not_real(config.FOG_OBJECT_NAMES_PATH)
        self._assert_not_real(FogEarlyRead.trust.path)

    def test_the_network_is_refused(self):
        """差し替え忘れの送信が本物の DB へ届かない"""
        with self.assertRaises(OSError):
            urllib.request.urlopen("https://example.invalid")
        with patch.object(ConnectDB.threading, "Thread",
                          RunNow), patch("builtins.print") as printed:
            ConnectDB.register_round("Classic", [1], 1, 1)
        self.assertIn("送信エラー", str(printed.call_args))

    def test_the_round_store_path_is_not_the_real_one(self):
        self._assert_not_real(config.ROUND_STORE_PATH)
        self._assert_not_real(RoundStore.default_store().path)

    def test_the_debug_log_path_is_not_the_real_one(self):
        self._assert_not_real(config.DEBUG_LOG_PATH)

    def test_saving_settings_does_not_touch_the_real_one(self):
        mainGUI.save_settings({"probe": True})
        self.assertTrue(config.SETTINGS_PATH.exists())
        self._assert_not_real(config.SETTINGS_PATH)
