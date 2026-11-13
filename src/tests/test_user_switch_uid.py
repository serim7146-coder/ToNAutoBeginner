"""監視中のログインで UID が変わったら、統計用の番号（transformed_uid）を取り直す（LogMonitor._on_user_auth）

同じ VRChat のままアカウントを替えると、前のアカウントの番号のまま統計が送られていた。
UID が前と違うときだけ番号を None に戻して取り直す。取りに行くのは、サーバーの確認（AccessCheck.enforce）が
ALLOWED のときだけ。UID・名前は作り物。DB は差し替える
"""
from tests.support import *  # noqa: F401,F403


PREFIX = "2026.10.31 12:40:19 Debug      -  "
ME = "usr_00000000-0000-0000-0000-0000000000a1"
OTHER = "usr_00000000-0000-0000-0000-0000000000b2"


def login(uid, name="tester"):
    return PREFIX + f"User Authenticated: {name} ({uid})"


class _Terminated(Exception):
    pass


class TestUserSwitchRefetch(unittest.TestCase):
    def _monitor(self, uid=ME, number=7):
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path("unused.txt")), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.local_user_id = uid
        monitor.st.local_player_name = "old"
        monitor.st.transformed_uid = number
        monitor._running = True
        self.started = []
        patcher = patch.object(monitor, "_start_daemon",
                               side_effect=lambda f, *a: self.started.append((f.__name__, a)))
        patcher.start()
        self.addCleanup(patcher.stop)
        return monitor

    def _process(self, monitor, uid, result=AccessCheck.ALLOWED, name="tester"):
        with patch.object(AccessCheck, "enforce", return_value=result) as enforce:
            monitor._process(login(uid, name))
        enforce.assert_called_once_with(uid)

    def test_a_different_uid_resets_the_number_and_fetches_again(self):
        monitor = self._monitor()
        self._process(monitor, OTHER, name="newer")
        self.assertIsNone(monitor.st.transformed_uid, "前のアカウントの番号は捨てる")
        self.assertEqual(monitor.st.local_user_id, OTHER)
        self.assertEqual(monitor.st.local_player_name, "newer")
        self.assertEqual(self.started, [("_fetch_transformed_uid", (OTHER, "newer"))])
        self.assertEqual(sum("UserID検出" in m for m in monitor.logs), 1)

    def test_the_new_number_goes_in_when_the_fetch_finishes(self):
        monitor = self._monitor()
        with patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: f(*a)), \
             patch.object(ConnectDB, "send_Users", return_value=21) as fetch:
            self._process(monitor, OTHER)
        fetch.assert_called_once_with(OTHER, "tester")
        self.assertEqual(monitor.st.transformed_uid, 21)

    def test_the_same_uid_does_nothing(self):
        monitor = self._monitor()
        self._process(monitor, ME, name="renamed")
        self.assertEqual(monitor.st.transformed_uid, 7, "取り直さない・消さない")
        self.assertEqual(self.started, [])
        self.assertEqual(monitor.logs, [], "ログも出さない")
        self.assertEqual(monitor.st.local_player_name, "renamed", "名前は今までどおり更新する")

    def test_the_same_uid_twice_in_a_row_does_nothing_the_second_time(self):
        monitor = self._monitor(uid="", number=None)
        self._process(monitor, ME)
        self._process(monitor, ME)
        self.assertEqual(len(self.started), 1)
        self.assertEqual(sum("UserID検出" in m for m in monitor.logs), 1)

    def test_an_empty_previous_uid_counts_as_changed(self):
        """まだ UID が読めていなかった窓（ログインの行を待っていた）が、読めたとき"""
        monitor = self._monitor(uid="", number=None)
        self._process(monitor, ME)
        self.assertEqual(self.started, [("_fetch_transformed_uid", (ME, "tester"))])
        self.assertEqual(monitor.st.local_user_id, ME)

    def test_without_the_servers_ok_it_does_not_fetch_and_the_number_stays_none(self):
        for result in (AccessCheck.UNREACHABLE, AccessCheck.NO_UID):
            monitor = self._monitor()       # started は作り直される
            self._process(monitor, OTHER, result=result)
            self.assertIsNone(monitor.st.transformed_uid, result)
            self.assertEqual(self.started, [], result)
            self.assertFalse(any(OTHER in m for m in monitor.logs), "確認が通らない UID は画面に出さない")
            self.assertEqual(monitor.st.local_user_id, OTHER, result)

    def test_the_same_uid_without_the_servers_ok_does_not_fetch_either(self):
        monitor = self._monitor()
        self._process(monitor, ME, result=AccessCheck.UNREACHABLE)
        self.assertEqual(monitor.st.transformed_uid, 7)
        self.assertEqual(self.started, [])

    def test_a_banned_uid_ends_the_process_before_anything_changes(self):
        monitor = self._monitor()
        with patch.object(AccessCheck, "enforce", side_effect=_Terminated), \
             patch.object(DebugLog, "write") as written, \
             self.assertRaises(_Terminated):
            monitor._process(login(OTHER))
        self.assertEqual(monitor.st.local_user_id, ME)
        self.assertEqual(monitor.st.transformed_uid, 7)
        self.assertEqual(self.started, [])
        self.assertEqual(monitor.logs, [])
        self.assertFalse(any(OTHER in str(c) for c in written.call_args_list))

    def test_the_post_login_line_switches_too(self):
        monitor = self._monitor()
        line = PREFIX + f"[Authentication - abc] Preforming post login checks for tester ({OTHER})"
        with patch.object(AccessCheck, "enforce", return_value=AccessCheck.ALLOWED):
            monitor._process(line)
        self.assertIsNone(monitor.st.transformed_uid)
        self.assertEqual(self.started, [("_fetch_transformed_uid", (OTHER, "tester"))])


if __name__ == "__main__":
    unittest.main()
