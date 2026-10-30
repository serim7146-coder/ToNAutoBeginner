"""2026-10-29 の VRChat の更新で User Authenticated の行が出なくなった対応。

自分の usr_ID は `[API] Requesting Get users/usr_…/clientConfig` の行から、
自分の名前は `[Behaviour] Initialized PlayerAPI "名前" is local` の行から取る。
古い User Authenticated の行も今まで通り読む。偽のログは一時フォルダに作る
（本物の VRChat のログは読まない）
"""
from tests.support import *  # noqa: F401,F403


PREFIX = "2026.10.30 13:03:28 Debug      -  "
ME = "usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3"
OTHER = "usr_11111111-2222-3333-4444-555555555555"
ME_NAME = "serim01"
CONFIG_LINE = "[API] Requesting Get users/{uid}/clientConfig {{}} retryCount: 2"
SENDING_LINE = ("[API] [11] Sending Get request to "
                "https://api.vrchat.cloud/api/1/users/{uid}/clientConfig")
LOCAL_LINE = '[Behaviour] Initialized PlayerAPI "{name}" is local'
JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"


def config_line(uid=ME):
    return CONFIG_LINE.format(uid=uid)


def local_line(name=ME_NAME):
    return LOCAL_LINE.format(name=name)


def joined_line(name, uid):
    return f"[Behaviour] OnPlayerJoined {name} ({uid})"


class TestParse(unittest.TestCase):
    def test_client_config_line_gives_the_user_id(self):
        event = LogParser.parse(PREFIX + config_line())
        self.assertEqual((event.kind, event.user_id, event.player_name),
                         (LogParser.EVENT_USER_ID, ME, ""))

    def test_the_bare_line_without_a_prefix_parses_too(self):
        self.assertEqual(LogParser.parse(config_line()).user_id, ME)

    def test_the_sending_request_line_is_not_a_user_id(self):
        """同じ /clientConfig を含むが Requesting が無い"""
        self.assertIsNone(LogParser.parse(PREFIX + SENDING_LINE.format(uid=ME)))

    def test_is_local_line_gives_the_own_name(self):
        event = LogParser.parse(PREFIX + local_line())
        self.assertEqual((event.kind, event.player_name, event.user_id),
                         (LogParser.EVENT_LOCAL_PLAYER, ME_NAME, ""))

    def test_a_name_with_quotes_and_parentheses_is_kept_whole(self):
        for name in ('a "b" c', "x (y) z", "名前 (usr_x)", 'q"'):
            event = LogParser.parse(PREFIX + local_line(name))
            self.assertEqual(event.kind, LogParser.EVENT_LOCAL_PLAYER, name)
            self.assertEqual(event.player_name, name)

    def test_is_remote_line_gives_nothing(self):
        self.assertIsNone(LogParser.parse(
            PREFIX + '[Behaviour] Initialized PlayerAPI "someone" is remote'))

    def test_an_empty_name_gives_nothing(self):
        self.assertIsNone(LogParser.parse(
            PREFIX + '[Behaviour] Initialized PlayerAPI "" is local'))

    def test_the_old_user_authenticated_line_still_parses(self):
        event = LogParser.parse(PREFIX + f"User Authenticated: {ME_NAME} ({ME})")
        self.assertEqual((event.kind, event.user_id, event.player_name),
                         (LogParser.EVENT_USER_AUTH, ME, ME_NAME))

    def test_the_marks_are_in_the_lines_and_the_regexes(self):
        self.assertIn(LogParser.USER_ID_MARK, LogParser.RE_USER_ID.pattern)
        self.assertIn(LogParser.LOCAL_PLAYER_MARK, LogParser.RE_LOCAL_PLAYER.pattern)
        self.assertIn(LogParser.USER_ID_MARK, config_line())
        self.assertIn(LogParser.LOCAL_PLAYER_MARK, local_line('a "b" c'))


class _LogFileCase(unittest.TestCase):
    """偽のログを一時フォルダに作って、監視開始の遡りに読ませる"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _log_file(self, lines, name="output_log_2026-10-30_13-03-00.txt"):
        path = Path(self.dir.name) / name
        path.write_text("\n".join(PREFIX + line for line in lines) + "\n",
                        encoding="utf-8")
        return path

    def _monitor(self, path=None):
        cfg = WindowConfig(log_path=path) if path else WindowConfig(hwnd=0x10, do_skip=False)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _scan(self, lines):
        """遡りを走らせる。(monitor, 統計用の番号を取りに行った呼び出し)"""
        monitor = self._monitor(self._log_file(lines))
        with patch.object(ConnectDB, "send_Users", return_value=7) as send, \
             patch.object(monitor, "_start_daemon",
                          side_effect=lambda f, *a: f(*a)):
            monitor._detect_instance_from_log()
        return monitor, send.call_args_list


class TestStartScan(_LogFileCase):
    NEW_FORMAT = [
        "Some startup line",
        config_line(),
        SENDING_LINE.format(uid=ME),
        JOIN,
        local_line(),
        joined_line(ME_NAME, ME),
        joined_line("other", OTHER),
    ]

    def test_new_format_gives_the_id_the_name_and_the_present_names(self):
        monitor, sends = self._scan(self.NEW_FORMAT)
        st = monitor.st
        self.assertEqual((st.local_user_id, st.local_player_name), (ME, ME_NAME))
        self.assertEqual(monitor._present_names(), {ME_NAME, "other"})
        self.assertEqual(st.transformed_uid, 7)

    def test_the_transformed_uid_is_fetched_only_once(self):
        """先頭の読みと遡りの両方で同じ行が見えても、取りに行くのは 1 回"""
        monitor, sends = self._scan(self.NEW_FORMAT)
        self.assertEqual(sends, [call(ME)])
        self.assertEqual(
            len([m for m in monitor.logs if "UserID検出" in m]), 1, monitor.logs)

    def test_without_an_is_local_line_the_own_joined_line_fills_the_name(self):
        lines = [l for l in self.NEW_FORMAT if "is local" not in l]
        monitor, _ = self._scan(lines)
        self.assertEqual(monitor.st.local_player_name, ME_NAME)
        self.assertEqual(monitor._present_names(), {ME_NAME, "other"})

    def test_the_joined_line_does_not_overwrite_a_known_name(self):
        lines = list(self.NEW_FORMAT)
        lines[-2] = joined_line("renamed", ME)
        monitor, _ = self._scan(lines)
        self.assertEqual(monitor.st.local_player_name, ME_NAME)

    def test_another_players_joined_line_does_not_fill_the_name(self):
        lines = [JOIN, config_line(), joined_line("other", OTHER)]
        monitor, _ = self._scan(lines)
        self.assertEqual(monitor.st.local_player_name, "")
        self.assertEqual(monitor.st.local_user_id, ME)

    def test_a_second_login_with_another_id_wins(self):
        lines = [config_line(ME), JOIN, local_line(ME_NAME), "relogin",
                 config_line(OTHER), JOIN, local_line("second")]
        monitor, sends = self._scan(lines)
        st = monitor.st
        self.assertEqual((st.local_user_id, st.local_player_name), (OTHER, "second"))
        self.assertEqual(sends[-1], call(OTHER))

    def test_old_format_still_gives_the_id_and_the_name(self):
        lines = [f"User Authenticated: {ME_NAME} ({ME})", JOIN,
                 joined_line(ME_NAME, ME), joined_line("other", OTHER)]
        monitor, sends = self._scan(lines)
        st = monitor.st
        self.assertEqual((st.local_user_id, st.local_player_name), (ME, ME_NAME))
        self.assertEqual(monitor._present_names(), {ME_NAME, "other"})
        self.assertEqual(sends, [call(ME)])

    def test_old_format_relogin_still_wins(self):
        lines = [f"User Authenticated: first ({ME})", JOIN,
                 f"User Authenticated: second ({OTHER})", JOIN]
        monitor, _ = self._scan(lines)
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name),
                         (OTHER, "second"))

    def test_no_login_line_leaves_both_empty(self):
        monitor, sends = self._scan([JOIN, joined_line("other", OTHER)])
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name),
                         ("", ""))
        self.assertEqual(sends, [])

    def test_the_log_head_reads_the_new_id_line(self):
        monitor = self._monitor(self._log_file(self.NEW_FORMAT))
        with patch.object(monitor, "_start_daemon"):
            self.assertTrue(monitor._user_from_log_head())
        self.assertEqual(monitor.st.local_user_id, ME)

    def test_the_log_head_skips_the_sending_request_line(self):
        monitor = self._monitor(self._log_file([SENDING_LINE.format(uid=ME)]))
        with patch.object(monitor, "_start_daemon"):
            self.assertFalse(monitor._user_from_log_head())
        self.assertEqual(monitor.st.local_user_id, "")

    def test_the_start_scan_marks_include_the_new_lines(self):
        marks = LogMonitor.LogMonitor._START_SCAN_MARKS
        self.assertIn(LogParser.USER_ID_MARK, marks)
        self.assertIn(LogParser.LOCAL_PLAYER_MARK, marks)


class TestLiveMonitoring(_LogFileCase):
    def test_a_client_config_line_sets_the_id_and_fetches_once(self):
        monitor = self._monitor()
        with patch.object(ConnectDB, "send_Users", return_value=5) as send, \
             patch.object(monitor, "_start_daemon",
                          side_effect=lambda f, *a: f(*a)):
            monitor._process(PREFIX + config_line())
            monitor._process(PREFIX + config_line())        # 同じ ID は取り直さない
        self.assertEqual(monitor.st.local_user_id, ME)
        self.assertEqual(monitor.st.transformed_uid, 5)
        self.assertEqual(send.call_args_list, [call(ME)])
        self.assertEqual(
            len([m for m in monitor.logs if "UserID検出: " + ME in m]), 1)

    def test_a_new_id_is_fetched_again(self):
        monitor = self._monitor()
        with patch.object(ConnectDB, "send_Users", return_value=5) as send, \
             patch.object(monitor, "_start_daemon",
                          side_effect=lambda f, *a: f(*a)):
            monitor._process(PREFIX + config_line(ME))
            monitor._process(PREFIX + config_line(OTHER))
        self.assertEqual(monitor.st.local_user_id, OTHER)
        self.assertEqual(send.call_args_list, [call(ME), call(OTHER)])

    def test_an_is_local_line_sets_the_name_without_a_public_log(self):
        monitor = self._monitor()
        with patch.object(DebugLog, "write") as debug:
            monitor._process(PREFIX + local_line())
        self.assertEqual(monitor.st.local_player_name, ME_NAME)
        self.assertEqual(monitor.logs, [])
        self.assertTrue(any("自分の名前を検出" in c.args[0] for c in debug.call_args_list))

    def test_the_same_name_again_is_not_written_twice(self):
        monitor = self._monitor()
        with patch.object(DebugLog, "write") as debug:
            monitor._process(PREFIX + local_line())
            monitor._process(PREFIX + local_line())
        found = [c for c in debug.call_args_list if "自分の名前を検出" in c.args[0]]
        self.assertEqual(len(found), 1)

    def test_an_is_remote_line_does_not_touch_the_name(self):
        monitor = self._monitor()
        monitor._process(PREFIX + '[Behaviour] Initialized PlayerAPI "x" is remote')
        self.assertEqual(monitor.st.local_player_name, "")

    def test_the_own_joined_line_fills_a_missing_name(self):
        monitor = self._monitor()
        monitor.st.local_user_id = ME
        monitor._process(PREFIX + joined_line(ME_NAME, ME))
        self.assertEqual(monitor.st.local_player_name, ME_NAME)
        monitor._process(PREFIX + joined_line("renamed", ME))
        self.assertEqual(monitor.st.local_player_name, ME_NAME)

    def test_the_old_user_authenticated_line_still_sets_both(self):
        monitor = self._monitor()
        monitor._process(PREFIX + f"User Authenticated: {ME_NAME} ({ME})")
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name),
                         (ME, ME_NAME))

    def test_the_debug_event_lines(self):
        monitor = self._monitor()
        with patch.object(DebugLog, "write") as debug, \
             patch.object(monitor, "_start_daemon"):
            monitor._process(PREFIX + config_line())
            monitor._process(PREFIX + local_line())
        said = " ".join(c.args[0] for c in debug.call_args_list)
        self.assertIn(f"[事象] user_id ID={ME}", said)
        self.assertIn(f"[事象] local_player 名前={ME_NAME}", said)

    def test_the_sus_player_line_matches_the_name_from_is_local(self):
        """Sabotage の選出者の一致（既存の経路）が、is local の名前で効く"""
        monitor = self._monitor()
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        monitor.st.item_id = 10
        with patch.object(monitor, "_start_daemon"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process(PREFIX + config_line())
            monitor._process(PREFIX + local_line())
            monitor._process(PREFIX + f"Sus player = 5 {ME_NAME}")
            monitor._process(PREFIX + "This round is taking place at Cheese Maze (59) "
                             "and the round type is Sabotage")
        self.assertTrue(monitor.st.sabotage_murder_this_round)
        self.assertEqual(monitor.st.item_id, 0)


class TestLatestUserId(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _write(self, stamp, lines):
        (Path(self.dir.name) / f"output_log_{stamp}.txt").write_text(
            "\n".join(PREFIX + line for line in lines) + "\n", encoding="utf-8")

    def test_new_format_only(self):
        self._write("2026-10-30_13-03-00",
                    ["startup", config_line(), SENDING_LINE.format(uid=ME)])
        self.assertEqual(VRChatLauncher.latest_user_id(self.dir.name), ME)

    def test_old_format_only(self):
        self._write("2026-10-28_19-44-00", [f"User Authenticated: a ({ME})"])
        self.assertEqual(VRChatLauncher.latest_user_id(self.dir.name), ME)

    def test_the_newer_log_wins_when_the_formats_differ(self):
        self._write("2026-10-28_19-44-00", [f"User Authenticated: a ({OTHER})"])
        self._write("2026-10-30_13-03-00", [config_line(ME)])
        self.assertEqual(VRChatLauncher.latest_user_id(self.dir.name), ME)

    def test_the_last_login_in_one_log_wins(self):
        self._write("2026-10-30_13-03-00", [config_line(OTHER), "x", config_line(ME)])
        self.assertEqual(VRChatLauncher.latest_user_id(self.dir.name), ME)

    def test_the_sending_request_line_alone_is_not_enough(self):
        self._write("2026-10-30_13-03-00", [SENDING_LINE.format(uid=ME)])
        self.assertIsNone(VRChatLauncher.latest_user_id(self.dir.name))

    def test_no_login_line_gives_none(self):
        self._write("2026-10-30_13-03-00", ["startup"])
        self.assertIsNone(VRChatLauncher.latest_user_id(self.dir.name))


if __name__ == "__main__":
    unittest.main()
