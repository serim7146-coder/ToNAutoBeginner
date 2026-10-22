"""ラウンドごとの人数（ラウンド開始の瞬間のインスタンスの人数。自分を含む。分からなければ None）。
手元の rounds.sqlite の players の列。本物の DB へは送らない"""
from tests.support import *  # noqa: F401,F403

P = "2026.09.20 11:55:47 Debug      -  "
JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"
ME = "usr_0e01408a"


def _joined(name, uid):
    return f"[Behaviour] OnPlayerJoined {name} ({uid})"


def _left(name, uid):
    return f"[Behaviour] OnPlayerLeft {name} ({uid})"


def _start(round_type):
    return f"This round is taking place at Sewers (12) and the round type is {round_type}"


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        self.send = patch.object(ConnectDB, "register_round").start()
        self.thread = patch.object(LogMonitor.threading, "Thread").start()
        self.play = patch.object(PlaySound, "play_sound").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)

    def _monitor(self, known=True):
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True), {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.local_user_id = ME
        monitor.st.local_player_name = "serim01"
        if known:
            monitor._process(P + JOIN)            # 入室した瞬間から入退室が見える → 人数を信用できる
        return monitor

    def _feed(self, monitor, *lines):
        for line in lines:
            monitor._process(P + line)

    def _players(self):
        return self.send.call_args.kwargs["players"]


class TestRoundPlayers(_Case):
    def test_the_count_includes_me_when_my_join_line_is_missing(self):
        monitor = self._monitor()
        self._feed(monitor, _joined("A", "usr_a"), _joined("B", "usr_b"), _start("Fog"),
                   "Killers have been set - 5 0 0 // Round type is Fog")
        self.assertEqual(self._players(), 3, "A・B と自分")

    def test_my_own_join_line_is_not_counted_twice(self):
        monitor = self._monitor()
        self._feed(monitor, _joined("serim01", ME), _joined("A", "usr_a"), _start("Fog"),
                   "Killers have been set - 5 0 0 // Round type is Fog")
        self.assertEqual(self._players(), 2)

    def test_alone_is_one(self):
        monitor = self._monitor()
        self._feed(monitor, _start("Fog"), "Killers have been set - 5 0 0 // Round type is Fog")
        self.assertEqual(self._players(), 1)

    def test_it_is_the_count_at_the_start_not_at_the_killers_line(self):
        monitor = self._monitor()
        self._feed(monitor, _joined("A", "usr_a"), _joined("B", "usr_b"), _start("Fog"),
                   _left("B", "usr_b"), _joined("C", "usr_c"), _joined("D", "usr_d"),
                   "Killers have been set - 5 0 0 // Round type is Fog")
        self.assertEqual(self._players(), 3, "開始の瞬間は A・B と自分")

    def test_each_round_has_its_own_count(self):
        monitor = self._monitor()
        self._feed(monitor, _joined("A", "usr_a"), _start("Fog"),
                   "Killers have been set - 5 0 0 // Round type is Fog")
        self.assertEqual(self._players(), 2)
        self._feed(monitor, _joined("B", "usr_b"), "RoundOver", _start("Fog"),
                   "Killers have been set - 6 0 0 // Round type is Fog")
        self.assertEqual(self._players(), 3)

    def test_moving_to_another_instance_starts_over(self):
        monitor = self._monitor()
        self._feed(monitor, _joined("A", "usr_a"), _joined("B", "usr_b"), JOIN, _start("Fog"),
                   "Killers have been set - 5 0 0 // Round type is Fog")
        self.assertEqual(self._players(), 1)

    def test_unknown_when_the_joins_could_not_be_trusted(self):
        monitor = self._monitor(known=False)
        self.assertFalse(monitor.st.players_known)
        self._feed(monitor, _joined("A", "usr_a"), _start("Fog"),
                   "Killers have been set - 5 0 0 // Round type is Fog")
        self.assertIsNone(self._players())

    def test_the_rounds_without_terrors_are_sent_with_the_count_too(self):
        monitor = self._monitor()
        self._feed(monitor, _joined("A", "usr_a"), _start("Mystic Moon"))
        self.send.assert_called_once()
        self.assertEqual(self._players(), 2)

    def test_the_fog_early_read_sends_the_count_too(self):
        monitor = self._monitor()
        monitor.st.round_type = "Fog"
        monitor.st.in_round = True
        monitor.st.round_players = 4
        monitor._send_early_statistics(5)
        self.assertEqual(self._players(), 4)


class TestPlayersReachTheStore(unittest.TestCase):
    """ConnectDB.register_round → 手元の rounds.sqlite（送れたときだけ貯める）"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = RoundStore.RoundStore(Path(tmp.name) / "rounds.sqlite")

    def _send(self, players):
        res = MagicMock()
        res.__enter__ = MagicMock(return_value=res)
        res.__exit__ = MagicMock(return_value=False)
        res.status = 204
        with patch.object(ConnectDB, "_configured", return_value=True), \
             patch.object(ConnectDB, "_headers", return_value={}), \
             patch.object(ConnectDB.TaskStats, "thread", lambda _n, target, daemon=True: RunNow(target)), \
             patch.object(ConnectDB.urllib.request, "urlopen", return_value=res), \
             patch.object(RoundStore, "default_store", return_value=self.store), \
             patch("builtins.print"):
            ConnectDB.register_round("Classic", [5], 12, 7, round_time=1767225610, players=players)

    def _column(self):
        con = sqlite3.connect(str(self.store.path))
        try:
            return con.execute("select players from rounds").fetchall()
        finally:
            con.close()

    def test_the_count_is_stored(self):
        self._send(3)
        self.assertEqual(self._column(), [(3,)])

    def test_an_unknown_count_is_stored_as_null(self):
        self._send(None)
        self.assertEqual(self._column(), [(None,)])

    def test_add_own_without_a_count_is_null(self):
        self.assertTrue(self.store.add_own(10, 1, 12, 5, None, None, 7))
        self.assertEqual(self._column(), [(None,)])

    def test_the_rows_shape_for_the_screens_is_unchanged(self):
        self.store.add_own(10, 1, 12, 5, None, None, 7, players=2)
        self.assertEqual(self.store.rows(), [(10, 1, 12, 5, None, None, 7, None, "own")])


class TestPlayersColumn(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "rounds.sqlite"

    def _columns(self):
        con = sqlite3.connect(str(self.path))
        try:
            return [row[1] for row in con.execute("pragma table_info(rounds)")]
        finally:
            con.close()

    def test_a_new_store_has_the_column(self):
        RoundStore.RoundStore(self.path).count()
        self.assertIn("players", self._columns())

    def test_a_store_made_by_the_previous_version_gets_the_column_and_keeps_its_rows(self):
        con = sqlite3.connect(str(self.path))
        con.executescript("""
            create table rounds (time integer not null, round integer not null, map_id integer,
                terror1 integer, terror2 integer, terror3 integer, transformed_uid integer,
                other_uids text, origin text not null);
            create unique index rounds_key on rounds(time, ifnull(transformed_uid, -99999), round,
                ifnull(map_id, -1), ifnull(terror1, -1));
            create table meta (key text primary key, value text);
            insert into rounds values (10, 1, 12, 5, NULL, NULL, 7, NULL, 'own');""")
        con.commit()
        con.close()
        store = RoundStore.RoundStore(self.path)
        self.assertEqual(store.count(), 1)
        self.assertIn("players", self._columns())
        con = sqlite3.connect(str(self.path))
        self.assertEqual(con.execute("select players from rounds").fetchall(), [(None,)], "今までの行は NULL")
        con.close()
        self.assertTrue(store.add_own(20, 1, 12, 6, None, None, 7, players=4))
        self.assertEqual(RoundStore.RoundStore(self.path).count(), 2, "もう一度開いても壊れない")

    def test_the_sync_writes_the_count_of_a_row_that_has_one(self):
        store = RoundStore.RoundStore(self.path)
        rows = [{"time": 5, "round": 1, "map_id": 12, "terror1": 5, "terror2": None, "terror3": None,
                 "transformed_uid": 9, "other_uids": None, "players": 6},
                {"time": 6, "round": 1, "map_id": 12, "terror1": 6, "terror2": None, "terror3": None,
                 "transformed_uid": 9, "other_uids": None}]
        self.assertTrue(store.sync(lambda since, mine: iter(rows)))
        con = sqlite3.connect(str(self.path))
        self.assertEqual(con.execute("select time, players from rounds order by time").fetchall(),
                         [(5, 6), (6, None)])
        con.close()
