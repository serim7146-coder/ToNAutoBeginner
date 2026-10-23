"""途中から起動したラウンドの人数と時刻（DB の p_players・p_time）。監視開始の遡りが、今進んでいるラウンドの
開始の行の時刻と、その時点の人数（自分を含む。開始より前の入退室だけ）を覚えて、DB に送るときだけに使う。
本物の DB へは送らない"""
from tests.support import *  # noqa: F401,F403
from datetime import datetime

ME = "usr_0e01408a"
JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"
KILLERS = "Killers have been set - 5 0 0 // Round type is Fog"


def _at(second):
    return f"2026.09.20 11:55:{second:02d} Debug      -  "


def _epoch(second):
    return datetime(2026, 9, 20, 11, 55, second).timestamp()


def _joined(name, uid):
    return f"[Behaviour] OnPlayerJoined {name} ({uid})"


def _left(name, uid):
    return f"[Behaviour] OnPlayerLeft {name} ({uid})"


def _start(round_type="Fog"):
    return f"This round is taking place at Sewers (12) and the round type is {round_type}"


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        self.send = patch.object(ConnectDB, "register_round").start()
        patch.object(LogMonitor.threading, "Thread").start()
        patch.object(PlaySound, "play_sound").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)

    def _started(self, lines):
        """lines = [(秒, 行), ...] の入ったログで、監視を始める（遡り）"""
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        rows = [(1, f"User Authenticated: serim01 ({ME})")] + list(lines)
        tmp.write("\n".join(_at(sec) + line for sec, line in rows) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name), do_skip=True), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        with patch.object(ConnectDB, "send_Users", return_value=7), \
             patch.object(monitor, "_start_daemon"):
            monitor._detect_instance_from_log()
        return monitor

    def _feed(self, monitor, second, line):
        monitor._process(_at(second) + line)

    def _sent(self):
        return self.send.call_args.kwargs


class TestStartedInTheMiddleOfARound(_Case):
    def test_the_count_and_the_time_are_those_of_the_start_line(self):
        monitor = self._started([
            (2, JOIN), (3, _joined("A", "usr_a")), (4, _joined("B", "usr_b")),
            (10, _start()),
            (11, _left("B", "usr_b")), (12, _joined("C", "usr_c")), (13, _joined("D", "usr_d")),
        ])
        self._feed(monitor, 20, KILLERS)
        self.send.assert_called_once()
        self.assertEqual(self._sent()["players"], 3, "開始の時点は A・B と自分（開始の後の入退室は数えない）")
        self.assertEqual(self._sent()["round_time"], _epoch(10))

    def test_started_after_the_killers_line_is_the_same(self):
        """Killers の行の後に起動しても同じ（開始の行の時刻・人数。送るのは次にテラーが分かったとき）"""
        monitor = self._started([
            (2, JOIN), (3, _joined("A", "usr_a")), (10, _start()), (11, _joined("B", "usr_b")), (12, KILLERS),
        ])
        monitor.st.round_type = "Fog"
        monitor._send_early_statistics(5)
        self.assertEqual(self._sent()["players"], 2)
        self.assertEqual(self._sent()["round_time"], _epoch(10))

    def test_the_rounds_without_terrors_use_them_too(self):
        monitor = self._started([(2, JOIN), (3, _joined("A", "usr_a")), (10, _start("Mystic Moon")),
                                 (11, _joined("B", "usr_b"))])
        monitor.st.round_type = "Mystic Moon"
        monitor._send_null_terror_round()
        self.assertEqual(self._sent()["players"], 2)
        self.assertEqual(self._sent()["round_time"], _epoch(10))

    def test_alone_is_one(self):
        monitor = self._started([(2, JOIN), (10, _start()), (11, _joined("A", "usr_a"))])
        self._feed(monitor, 20, KILLERS)
        self.assertEqual(self._sent()["players"], 1)

    def test_only_the_joins_after_the_last_joining_count(self):
        monitor = self._started([
            (2, _joined("X", "usr_x")), (3, JOIN), (4, _joined("A", "usr_a")), (10, _start()),
        ])
        self._feed(monitor, 20, KILLERS)
        self.assertEqual(self._sent()["players"], 2)

    def test_the_next_round_is_counted_as_usual(self):
        monitor = self._started([(2, JOIN), (3, _joined("A", "usr_a")), (10, _start())])
        self._feed(monitor, 20, "RoundOver")
        self._feed(monitor, 25, _joined("B", "usr_b"))
        self._feed(monitor, 30, _start())
        self._feed(monitor, 31, KILLERS)
        self.assertEqual(self._sent()["players"], 3)
        self.assertEqual(self._sent()["round_time"], _epoch(30))


class TestStartedBetweenRounds(_Case):
    def test_nothing_is_remembered_between_rounds(self):
        monitor = self._started([(2, JOIN), (3, _joined("A", "usr_a")), (10, _start()), (11, "RoundOver"),
                                 (12, _joined("B", "usr_b"))])
        self.assertIsNone(monitor.st.round_start_time)
        self.assertIsNone(monitor.st.round_players)

    def test_a_round_before_the_joining_is_not_the_current_one(self):
        monitor = self._started([(2, _start()), (3, JOIN), (4, _joined("A", "usr_a"))])
        self.assertIsNone(monitor.st.round_start_time)
        self.assertIsNone(monitor.st.round_players)

    def test_the_next_round_after_such_a_start_is_usual(self):
        monitor = self._started([(2, JOIN), (10, _start()), (11, "RoundOver"), (12, _joined("B", "usr_b"))])
        self._feed(monitor, 30, _start())
        self._feed(monitor, 31, KILLERS)
        self.assertEqual(self._sent()["players"], 2)
        self.assertEqual(self._sent()["round_time"], _epoch(30))


class TestWithoutAJoiningLine(_Case):
    def test_the_count_stays_unknown(self):
        """入室の行が遡りで見つからない窓は人数を復元できない（NULL）。時刻は覚えない（送った時刻のまま）"""
        monitor = self._started([(10, _start()), (11, _joined("A", "usr_a"))])
        self.assertFalse(monitor.st.players_known)
        self.assertIsNone(monitor.st.round_players)
        self._feed(monitor, 20, KILLERS)
        self.assertIsNone(self._sent()["players"])


if __name__ == "__main__":
    unittest.main()
