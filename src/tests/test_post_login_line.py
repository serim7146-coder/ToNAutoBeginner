"""ログインの行 `[Authentication - 記号] Preforming post login checks for 名前 (usr_…)`

2026-10-29 の VRChat の更新で User Authenticated の行が出なくなった。代わりに出る
この行を、同じ意味（EVENT_USER_AUTH・名前と usr_ID）として読む。
実ログは使わない（名前も ID も作り物）
"""
from tests.support import *  # noqa: F401,F403


PREFIX = "2026.10.31 12:40:19 Debug      -  "
ME = "usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3"
TAG = "ÎÎÍÏÎÏÌÎÏÎÍÌÌÏÌÌÍÎÌÏÏÏÎ"      # 23 文字の記号。毎回同じとは限らない


def post_login(name="serim01", user=ME, tag=TAG, word="Preforming"):
    return f"[Authentication - {tag}] {word} post login checks for {name} ({user})"


class TestParsePostLogin(unittest.TestCase):
    def test_the_line_is_read_as_user_auth(self):
        event = LogParser.parse(PREFIX + post_login())
        self.assertEqual(event.kind, LogParser.EVENT_USER_AUTH)
        self.assertEqual((event.user_id, event.player_name), (ME, "serim01"))

    def test_the_tag_does_not_matter(self):
        for tag in ("abc", "x" * 23, "あ-い", TAG):
            event = LogParser.parse(PREFIX + post_login(tag=tag))
            self.assertEqual(event.user_id, ME, tag)

    def test_a_name_with_parentheses_is_split_at_the_line_end(self):
        event = LogParser.parse(PREFIX + post_login(name="a (b) (usr_dead)"))
        self.assertEqual((event.user_id, event.player_name), (ME, "a (b) (usr_dead)"))

    def test_the_fixed_spelling_is_read_too(self):
        event = LogParser.parse(PREFIX + post_login(word="Performing"))
        self.assertEqual(event.kind, LogParser.EVENT_USER_AUTH)

    def test_a_line_ending_with_cr_is_read(self):
        event = LogParser.parse(PREFIX + post_login() + "\r")
        self.assertEqual(event.user_id, ME)

    def test_the_old_line_is_still_read(self):
        event = LogParser.parse(PREFIX + f"User Authenticated: serim01 ({ME})")
        self.assertEqual((event.kind, event.user_id, event.player_name),
                         (LogParser.EVENT_USER_AUTH, ME, "serim01"))

    def test_unrelated_lines_are_not_read(self):
        for line in ('Initialized PlayerAPI "serim01" is remote',
                     f"[Authentication - {TAG}] Something else for serim01 ({ME})",
                     f"[Authentication - {TAG}] Preforming post login checks for serim01",
                     f"[Authentication - {TAG}] Preforming post login checks for  (no id)",
                     "post login checks for serim01 (usr_x) trailing"):
            self.assertIsNone(LogParser.parse(PREFIX + line), line)


class TestPostLoginInTheMonitor(unittest.TestCase):
    LINES = [
        "[Behaviour] Joining wrld_old:1~private(usr_me)~region(jp)",
        post_login(),
        "[Behaviour] OnPlayerJoined a (usr_a)",
        "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
        "[Behaviour] OnPlayerJoined b (usr_b)",
    ]

    def _log_file(self, lines, crlf=False):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8", newline="")
        eol = "\r\n" if crlf else "\n"
        tmp.write(eol.join(PREFIX + line for line in lines) + eol)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def _monitor(self, path):
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _scan(self, path, head=True):
        monitor = self._monitor(path)
        fetch = MagicMock(return_value=7)
        with patch.object(ConnectDB, "send_Users", fetch), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: f(*a)):
            if head:
                monitor._detect_instance_from_log()
            else:
                with patch.object(monitor, "_user_from_log_head", return_value=False):
                    monitor._detect_instance_from_log()
            monitor._start_transformed_uid_fetch()      # サーバーの確認が通った後に _run が行う
        return monitor, fetch

    def test_the_head_read_takes_the_id_and_the_name_once(self):
        for crlf in (False, True):
            monitor, fetch = self._scan(self._log_file(self.LINES, crlf=crlf))
            self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name),
                             (ME, "serim01"), crlf)
            self.assertEqual(fetch.call_count, 1, crlf)
            fetch.assert_called_once_with(ME, "serim01")
            self.assertEqual(sum("UserID検出" in m for m in monitor.logs), 1, crlf)

    def test_the_backward_scan_finds_it_without_the_head_read(self):
        """先頭の読み込みの外（大きなログの途中）にある行も、遡りの前絞りで拾える"""
        for crlf in (False, True):
            monitor, fetch = self._scan(self._log_file(self.LINES, crlf=crlf), head=False)
            self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name),
                             (ME, "serim01"), crlf)
            self.assertEqual(fetch.call_count, 1, crlf)
            fetch.assert_called_once_with(ME, "serim01")

    def test_the_mark_is_in_the_scan_marks_and_the_line(self):
        self.assertIn(LogParser.POST_LOGIN_MARK, LogMonitor.LogMonitor._START_SCAN_MARKS)
        self.assertIn(LogParser.POST_LOGIN_MARK, post_login())

    def test_normal_monitoring_picks_it_up(self):
        monitor = self._monitor(Path("unused.txt"))
        monitor._process(PREFIX + post_login())
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name),
                         (ME, "serim01"))

    def test_the_name_is_not_written_to_any_log(self):
        """画面のログにも debug.log にも表示名を書かない（成功・失敗の両方）"""
        name = "テスト名"
        failure = urllib.error.HTTPError("u", 401, "denied", {}, None)
        for outcome in (MagicMock(return_value=7), MagicMock(side_effect=failure)):
            monitor = self._monitor(self._log_file([post_login(name=name)]))
            written = []
            with patch.object(ConnectDB, "send_Users", outcome), \
                 patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: f(*a)), \
                 patch.object(DebugLog, "write", side_effect=written.append), \
                 patch("builtins.print") as printed:
                monitor._detect_instance_from_log()
                monitor._start_transformed_uid_fetch()      # サーバーの確認が通った後に _run が行う
            outcome.assert_called_once_with(ME, name)
            self.assertTrue(any("UserID検出" in m for m in monitor.logs))
            texts = (monitor.logs + [str(w) for w in written]
                     + [str(c) for c in printed.call_args_list])
            for text in texts:
                self.assertNotIn(name, text)


class TestLatestUserIdPostLogin(unittest.TestCase):
    OLD = "usr_00000000-0000-0000-0000-00000000000a"
    NEW = "usr_00000000-0000-0000-0000-00000000000b"

    def _write(self, d, stamp, lines, eol="\n"):
        (Path(d) / f"output_log_{stamp}.txt").write_bytes(
            (eol.join(lines) + eol).encode("utf-8"))

    def test_only_the_new_line(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "2026-10-31_10-00-00",
                        [PREFIX + "other", PREFIX + post_login(user=self.NEW)], eol="\r\n")
            self.assertEqual(VRChatLauncher.latest_user_id(d), self.NEW)

    def test_only_the_old_line(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "2026-10-31_10-00-00",
                        [PREFIX + f"User Authenticated: a ({self.OLD})"])
            self.assertEqual(VRChatLauncher.latest_user_id(d), self.OLD)

    def test_both_kinds_in_different_logs_take_the_newest_log(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "2026-10-20_10-00-00",
                        [PREFIX + f"User Authenticated: a ({self.OLD})"])
            self._write(d, "2026-10-31_10-00-00",
                        [PREFIX + post_login(user=self.NEW)])
            self.assertEqual(VRChatLauncher.latest_user_id(d), self.NEW)

    def test_no_login_line_gives_none(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "2026-10-31_10-00-00", [PREFIX + "other"])
            self.assertIsNone(VRChatLauncher.latest_user_id(d))


class TestOldStyleUidAndLastParenthesis(unittest.TestCase):
    """UID は `usr_`＋16進とハイフン、または古い形（英数字ちょうど 10 文字）。どの行も行末の (…) で取る"""

    OLD_FORM = "AbCd123456"            # 作り物（英数字 10 文字）
    ME2 = "usr_00000000-0000-0000-0000-00000000000c"

    def _lines(self, name, uid):
        return {
            "User Authenticated": f"User Authenticated: {name} ({uid})",
            "post login": post_login(name=name, user=uid),
            "joined": f"[Behaviour] OnPlayerJoined {name} ({uid})",
            "left": f"[Behaviour] OnPlayerLeft {name} ({uid})",
        }

    def test_the_old_form_is_read_in_every_line(self):
        kinds = {"User Authenticated": LogParser.EVENT_USER_AUTH, "post login": LogParser.EVENT_USER_AUTH,
                 "joined": LogParser.EVENT_PLAYER_JOINED, "left": LogParser.EVENT_PLAYER_LEFT}
        for label, line in self._lines("tester", self.OLD_FORM).items():
            event = LogParser.parse(PREFIX + line)
            self.assertIsNotNone(event, label)
            self.assertEqual((event.kind, event.user_id, event.player_name),
                             (kinds[label], self.OLD_FORM, "tester"), label)

    def test_the_new_form_is_still_read(self):
        for label, line in self._lines("tester", self.ME2).items():
            event = LogParser.parse(PREFIX + line)
            self.assertEqual((event.user_id, event.player_name), (self.ME2, "tester"), label)

    def test_a_name_that_contains_a_uid_shaped_parenthesis_takes_the_last_one(self):
        for name in ("a (usr_a)", "x (usr_a) y", f"p ({self.OLD_FORM})", "q (b) (c)"):
            for label, line in self._lines(name, self.ME2).items():
                event = LogParser.parse(PREFIX + line)
                self.assertEqual((event.user_id, event.player_name), (self.ME2, name), (label, name))
        for label, line in self._lines("a (usr_a)", self.OLD_FORM).items():
            event = LogParser.parse(PREFIX + line)
            self.assertEqual((event.user_id, event.player_name), (self.OLD_FORM, "a (usr_a)"), label)

    def test_a_parenthesis_that_is_not_a_uid_is_not_read(self):
        for bad in ("VR=False", "AbCd12345", "AbCd1234567", "AbCd_12345", "usr_", "usr_xyz", "", "Quest"):
            for label, line in self._lines("tester", bad).items():
                self.assertIsNone(LogParser.parse(PREFIX + line), (label, bad))

    def test_text_after_the_parenthesis_is_not_a_login(self):
        self.assertIsNone(LogParser.parse(PREFIX + f"User Authenticated: tester ({self.ME2}) extra"))

    def test_the_latest_user_id_reads_both_forms_through_the_parser(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "output_log_2026-10-31_10-00-00.txt").write_bytes(
                (PREFIX + f"User Authenticated: o (usr_a) ({self.OLD_FORM})\r\n").encode("utf-8"))
            self.assertEqual(VRChatLauncher.latest_user_id(d), self.OLD_FORM)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "output_log_2026-10-31_10-00-00.txt").write_bytes(
                (PREFIX + post_login(name="b (c)", user=self.ME2) + "\r\n").encode("utf-8"))
            self.assertEqual(VRChatLauncher.latest_user_id(d), self.ME2)

    def test_the_launcher_no_longer_has_its_own_login_pattern(self):
        self.assertFalse(hasattr(VRChatLauncher, "RE_USER_AUTH"))
