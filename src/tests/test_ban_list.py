"""指定 UID の締め出し（BanList）。終わらせる処理（_TERMINATE）は必ず偽物にする。
一覧のファイルはこのテストの一時フォルダに作る（本物の ban_uids.txt・%APPDATA% には触れない）"""
from tests.support import *  # noqa: F401,F403

import BanList

BANNED = "usr_aaaaaaaa-1111-2222-3333-444444444444"
OTHER = "usr_bbbbbbbb-1111-2222-3333-444444444444"
PREFIX = "2026.10.31 12:40:19 Debug      -  "


class _Terminated(BaseException):
    """_TERMINATE の代わりに投げる目印。本物の os._exit は except Exception で捕まえられないので BaseException"""


class _BanFileCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        self.path = self.dir / "ban_uids.txt"
        p = patch.object(config, "BAN_UIDS_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)
        self.terminate = MagicMock()
        p = patch.object(BanList, "_TERMINATE", self.terminate)
        p.start()
        self.addCleanup(p.stop)

    def _write(self, text):
        self.path.write_text(text, encoding="utf-8")


class TestBanList(_BanFileCase):
    def test_a_banned_uid_terminates_once(self):
        self._write(BANNED + "\n")
        self.assertTrue(BanList.is_banned(BANNED))
        BanList.enforce(BANNED)
        self.terminate.assert_called_once_with(0)

    def test_others_do_not_terminate(self):
        self._write(BANNED + "\n")
        for uid in (OTHER, None, ""):
            self.assertFalse(BanList.is_banned(uid), uid)
            BanList.enforce(uid)
        self.terminate.assert_not_called()

    def test_a_missing_file_is_an_empty_list(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(BanList.load_banned(), frozenset())
        for uid in (BANNED, OTHER, None, ""):
            BanList.enforce(uid)
        self.terminate.assert_not_called()

    def test_an_unreadable_path_is_an_empty_list(self):
        self.path.mkdir()                           # ファイルではなくフォルダ → 読めない
        self.assertEqual(BanList.load_banned(), frozenset())
        BanList.enforce(BANNED)
        self.terminate.assert_not_called()

    def test_comments_and_blank_lines_are_ignored(self):
        self._write(f"# 荒らし\n\n   \n{BANNED}\n# {OTHER}\n")
        self.assertEqual(BanList.load_banned(), frozenset({BANNED}))
        self.assertFalse(BanList.is_banned(OTHER))
        self.assertFalse(BanList.is_banned("# " + OTHER))

    def test_whitespace_is_trimmed(self):
        self._write(f"   {BANNED}\t \r\n")
        self.assertEqual(BanList.load_banned(), frozenset({BANNED}))
        self.assertTrue(BanList.is_banned(f"  {BANNED}  "))

    def test_case_is_ignored_both_ways(self):
        self._write(BANNED.upper() + "\n")
        self.assertTrue(BanList.is_banned(BANNED.lower()))
        self._write(BANNED.lower() + "\n")
        self.assertTrue(BanList.is_banned(BANNED.upper()))

    def test_an_explicit_path_is_read(self):
        other = self.dir / "other.txt"
        other.write_text(OTHER + "\n", encoding="utf-8")
        self.assertEqual(BanList.load_banned(other), frozenset({OTHER}))

    def test_enforce_writes_nothing_anywhere(self):
        self._write(BANNED + "\n")
        written = []
        with patch.object(DebugLog, "write", side_effect=written.append), \
             patch("builtins.print") as printed, \
             contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            BanList.enforce(BANNED)
        self.terminate.assert_called_once_with(0)
        self.assertEqual((written, out.getvalue(), err.getvalue()), ([], "", ""))
        printed.assert_not_called()

    def test_the_production_hook_is_os_exit(self):
        import importlib
        fresh = importlib.reload(BanList)           # 差し替え前の本来の値を見る
        try:
            self.assertIs(fresh._TERMINATE, os._exit)
        finally:
            BanList._TERMINATE = self.terminate     # 念のため偽物に戻す


class TestBanInTheMonitor(_BanFileCase):
    """監視の起動時のログの読み込み（_detect_instance_from_log → _apply_user_auth）で止める"""

    def _log_file(self, lines):
        path = self.dir / "output_log.txt"
        path.write_text("\n".join(PREFIX + line for line in lines) + "\n", encoding="utf-8")
        return path

    def _monitor(self, path):
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    LOGIN = "User Authenticated: tester ({})"
    POST_LOGIN = "[Authentication - abc] Preforming post login checks for tester ({})"

    def _scan(self, line, head=True):
        monitor = self._monitor(self._log_file([line]))
        with patch.object(monitor, "_start_daemon") as daemon,              patch.object(DebugLog, "write") as written:
            if head:
                monitor._detect_instance_from_log()
            else:
                with patch.object(monitor, "_user_from_log_head", return_value=False):
                    monitor._detect_instance_from_log()
        return monitor, daemon, written

    def test_a_banned_login_terminates_before_anything_is_logged(self):
        self._write(BANNED + "\n")
        self.terminate.side_effect = _Terminated
        for fmt in (self.LOGIN, self.POST_LOGIN):
            for head in (True, False):
                self.terminate.reset_mock()
                monitor = self._monitor(self._log_file([fmt.format(BANNED)]))
                with patch.object(monitor, "_start_daemon") as daemon,                      patch.object(DebugLog, "write") as written,                      self.assertRaises(_Terminated):
                    if head:
                        monitor._detect_instance_from_log()
                    else:
                        with patch.object(monitor, "_user_from_log_head", return_value=False):
                            monitor._detect_instance_from_log()
                self.terminate.assert_called_once_with(0)
                self.assertFalse(any("UserID検出" in m for m in monitor.logs), (fmt, head))
                self.assertFalse(any(BANNED in m for m in monitor.logs), (fmt, head))
                self.assertEqual(monitor.st.local_user_id, "", (fmt, head))
                daemon.assert_not_called()
                written.assert_not_called()

    def test_a_normal_login_goes_on(self):
        self._write(BANNED + "\n")
        for head in (True, False):
            self.terminate.reset_mock()
            monitor, daemon, _written = self._scan(self.LOGIN.format(OTHER), head=head)
            self.terminate.assert_not_called()
            self.assertEqual(monitor.st.local_user_id, OTHER, head)
            self.assertTrue(any("UserID検出" in m for m in monitor.logs), head)
            daemon.assert_called_once()

    def test_a_banned_login_line_while_running_terminates(self):
        """動作中に流れてくるログイン行（_process → _on_user_auth）でも止める"""
        self._write(BANNED + "\n")
        self.terminate.side_effect = _Terminated
        for fmt in (self.LOGIN, self.POST_LOGIN):
            self.terminate.reset_mock()
            monitor = self._monitor(Path("unused.txt"))
            with patch.object(DebugLog, "write") as written, self.assertRaises(_Terminated):
                monitor._process(PREFIX + fmt.format(BANNED))
            self.terminate.assert_called_once_with(0)
            self.assertEqual(monitor.st.local_user_id, "", fmt)
            self.assertEqual(monitor.st.local_player_name, "", fmt)
            self.assertFalse(any(BANNED in m for m in monitor.logs), fmt)
            self.assertFalse(any(BANNED in str(c) for c in written.call_args_list), fmt)   # 種類だけの行は出る。UID は出ない

    def test_a_normal_login_line_while_running_goes_on(self):
        self._write(BANNED + "\n")
        for fmt in (self.LOGIN, self.POST_LOGIN):
            self.terminate.reset_mock()
            monitor = self._monitor(Path("unused.txt"))
            monitor._process(PREFIX + fmt.format(OTHER))
            self.terminate.assert_not_called()
            self.assertEqual(monitor.st.local_user_id, OTHER, fmt)


class TestBanAtStartup(_BanFileCase):
    def test_a_banned_latest_user_terminates(self):
        self._write(BANNED + "\n")
        with patch.object(VRChatLauncher, "latest_user_id", return_value=BANNED) as latest:
            BanList.enforce_startup()
        latest.assert_called_once_with(config.VRCHAT_LOG_DIR)
        self.terminate.assert_called_once_with(0)

    def test_others_and_none_do_not_terminate(self):
        self._write(BANNED + "\n")
        for uid in (OTHER, None):
            with patch.object(VRChatLauncher, "latest_user_id", return_value=uid):
                BanList.enforce_startup()
        self.terminate.assert_not_called()

    def test_a_read_failure_neither_crashes_nor_terminates(self):
        self._write(BANNED + "\n")
        with patch.object(VRChatLauncher, "latest_user_id", side_effect=OSError("x")):
            BanList.enforce_startup()
        self.terminate.assert_not_called()


class TestBanNeverLeaks(_BanFileCase):
    def test_the_uid_is_not_in_the_bug_report(self):
        self._write(f"# 荒らし\n{BANNED}\n")
        home = r"C:\Users\Alice"
        debug = self.dir / "debug.log"
        debug.write_text("debug line\n", encoding="utf-8")
        vrchat = self.dir / "output_log.txt"
        vrchat.write_text("vrchat line\n", encoding="utf-8")
        with patch.object(BugReport.os.path, "expanduser", return_value=home), \
             patch.object(config, "SETTINGS_PATH", self.dir / "app" / "settings.json"), \
             patch.object(BeginMiss, "folder", return_value=self.dir / "begin_miss"):
            _name, data = BugReport.build_report(
                "動かない", 2, {k: True for k, _l in BugReport.ATTACHMENTS}, "画面\n",
                str(vrchat), {"win_count": 6}, datetime(2026, 10, 1, 12, 34, 56),
                debug_log_path=debug, version="v9.9.9")
        self.assertNotIn(BANNED.encode(), data)
        z = zipfile.ZipFile(io.BytesIO(data))
        for name in z.namelist():
            self.assertNotIn(BANNED, z.read(name).decode("utf-8", "replace"), name)
            self.assertNotIn("ban_uids", name)


if __name__ == "__main__":
    unittest.main()
