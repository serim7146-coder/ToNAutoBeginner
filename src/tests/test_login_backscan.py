"""監視開始の遡り（LogMonitor._detect_instance_from_log）は、遡りで最初に見えたログインの行だけを使う

先頭が A・あとで B に入り直したログで、遡りが先頭まで届くと、B を当てたあとにさらに遡って先頭の A に
当たり、A で上書きしていた。最新（遡りで最初に見えた）の 1 行だけを使う。UID・名前は作り物
"""
from tests.support import *  # noqa: F401,F403


PREFIX = "2026.10.31 12:40:19 Debug      -  "
USER_A = "usr_00000000-0000-0000-0000-0000000000a1"
USER_B = "usr_00000000-0000-0000-0000-0000000000b2"
USER_C = "usr_00000000-0000-0000-0000-0000000000c3"


def auth(name, uid):
    return f"User Authenticated: {name} ({uid})"


def post_login(name, uid):
    return f"[Authentication - abc] Preforming post login checks for {name} ({uid})"


class TestLoginBackScan(unittest.TestCase):
    def _log_file(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("\n".join(PREFIX + line for line in lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def _scan(self, lines, head=True):
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=self._log_file(lines)), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        applied = []
        real = monitor._apply_user_auth

        def spy(event):
            applied.append(event.user_id)
            real(event)

        with patch.object(monitor, "_apply_user_auth", side_effect=spy):
            if head:
                monitor._detect_instance_from_log()
            else:
                with patch.object(monitor, "_user_from_log_head", return_value=False):
                    monitor._detect_instance_from_log()
        return monitor, applied

    def test_a_then_b_keeps_b_when_the_scan_reaches_the_head(self):
        """全体が短く、遡りが先頭まで届く。B のあとに先頭の A に当たっても A で上書きしない"""
        lines = [
            auth("first", USER_A),
            "[Behaviour] Joining wrld_old:1~private(usr_me)~region(jp)",
            post_login("second", USER_B),
            "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
        ]
        monitor, applied = self._scan(lines)
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name), (USER_B, "second"))
        self.assertEqual(applied, [USER_A, USER_B], "先頭から読んだ A のあと、遡りの最初の B だけ当てる")

    def test_a_then_b_keeps_b_without_the_head_read(self):
        lines = [
            auth("first", USER_A),
            "[Behaviour] Joining wrld_old:1~private(usr_me)~region(jp)",
            auth("second", USER_B),
            "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
        ]
        monitor, applied = self._scan(lines, head=False)
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name), (USER_B, "second"))
        self.assertEqual(applied, [USER_B], "先頭が読めなくても、遡りで最初に見えた B だけ")

    def test_three_logins_keep_the_newest(self):
        lines = [
            auth("n1", USER_A),
            auth("n2", USER_B),
            post_login("n3", USER_C),
            "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
        ]
        for head in (True, False):
            monitor, applied = self._scan(lines, head=head)
            self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name),
                             (USER_C, "n3"), head)
            self.assertEqual(applied.count(USER_C), 1, head)
            self.assertEqual(applied[-1], USER_C, "最後に当てたのが最新")

    def test_a_single_login_is_applied_once_as_before(self):
        lines = [
            "[Behaviour] Joining wrld_old:1~private(usr_me)~region(jp)",
            auth("only", USER_A),
            "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
        ]
        monitor, applied = self._scan(lines)
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name), (USER_A, "only"))
        self.assertEqual(applied, [USER_A], "先頭から読んだものと同じ UID は当て直さない")

    def test_a_single_login_found_only_by_the_scan_is_applied(self):
        lines = [auth("only", USER_A), "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"]
        monitor, applied = self._scan(lines, head=False)
        self.assertEqual(monitor.st.local_user_id, USER_A)
        self.assertEqual(applied, [USER_A])

    def test_no_login_leaves_the_user_empty(self):
        monitor, applied = self._scan(["[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"])
        self.assertEqual(monitor.st.local_user_id, "")
        self.assertEqual(applied, [])

    def test_the_instance_is_still_detected_with_two_logins(self):
        """ログインの扱いを変えても、入室（インスタンス）の検出は今までどおり"""
        lines = [
            auth("first", USER_A),
            "[Behaviour] Joining wrld_old:1~private(usr_me)~region(jp)",
            auth("second", USER_B),
            "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
        ]
        monitor, _ = self._scan(lines)
        self.assertTrue(monitor.st.instance_id.startswith("wrld_now:2"), monitor.st.instance_id)


if __name__ == "__main__":
    unittest.main()
