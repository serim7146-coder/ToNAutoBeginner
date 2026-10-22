"""統計・DB を独自のテラー番号にする: 手元の rounds.sqlite の移行・DB v2 への送信・無印との一時的な取り込み・
8 Pages の数え方。通信は差し替え。本物の DB・本物の rounds.sqlite には触らない"""
from tests.support import *  # noqa: F401,F403

import LegacyRoundsTable

OLD_SCHEMA = """
create table rounds (time integer not null, round integer not null, map_id integer,
    terror1 integer, terror2 integer, terror3 integer, transformed_uid integer,
    other_uids text, origin text not null);
create unique index rounds_key on rounds(time, ifnull(transformed_uid, -99999), round,
    ifnull(map_id, -1), ifnull(terror1, -1));
create index rounds_time on rounds(time);
create table meta (key text primary key, value text);
"""


def make_old_store(path, rows, meta=None):
    """前の版（番号は ListTool の形・players の列なし）が作った店"""
    con = sqlite3.connect(str(path))
    con.executescript(OLD_SCHEMA)
    con.executemany("insert into rounds values (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    for key, value in (meta or {}).items():
        con.execute("insert into meta values (?, ?)", (key, value))
    con.commit()
    con.close()


def dump(path):
    con = sqlite3.connect(str(path))
    try:
        return con.execute("select time, round, map_id, terror1, terror2, terror3, transformed_uid, "
                           "other_uids, origin, players from rounds order by time, rowid").fetchall()
    finally:
        con.close()


def meta_of(path):
    con = sqlite3.connect(str(path))
    try:
        return dict(con.execute("select key, value from meta").fetchall())
    finally:
        con.close()


class _TempCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "rounds.sqlite"
        self.bak = self.dir / "rounds.sqlite.listtool.bak"


class TestStoreMigration(_TempCase):
    ROWS = [
        # 8 Pages（105）は list_id → 3000＋ページ: 0 → 3014（Huggy）・135 → 3050（Whiteface）・172 → 3046（Baldi）
        (10, 105, 12, 0, None, None, 7, None, "own"),
        (20, 105, 12, 135, None, None, 7, None, "own"),
        (30, 105, 12, 172, None, None, 7, None, "db"),
        # ほかのラウンド: 135 → 1001・283 → 2083・317 → 4012・192 → 4002・知らない 175 → NULL・NULL は NULL
        (40, 51, 12, 135, None, None, 7, None, "own"),
        (50, 10, 12, 283, 192, 317, 7, "8,9", "db"),
        (60, 1, 12, 175, None, None, 7, None, "own"),
        (70, 6, 12, 5, 175, 6, 7, None, "own"),
        (80, 1, 12, None, None, None, 7, None, "own"),
    ]

    def test_the_numbers_move_and_the_original_is_kept(self):
        make_old_store(self.path, self.ROWS, {"my_uids": "7", "initial_done": "1", "synced_to": "80",
                                              "synced_uids": "7"})
        store = RoundStore.RoundStore(self.path)
        self.assertEqual(store.count(), 8)
        self.assertEqual(dump(self.path), [
            (10, 105, 12, 3014, None, None, 7, None, "own", None),
            (20, 105, 12, 3050, None, None, 7, None, "own", None),
            (30, 105, 12, 3046, None, None, 7, None, "db", None),
            (40, 51, 12, 1001, None, None, 7, None, "own", None),
            (50, 10, 12, 2083, 4002, 4012, 7, "8,9", "db", None),
            (60, 1, 12, None, None, None, 7, None, "own", None),
            (70, 6, 12, 5, None, 6, 7, None, "own", None),
            (80, 1, 12, None, None, None, 7, None, "own", None)])
        self.assertTrue(self.bak.exists())
        con = sqlite3.connect(str(self.bak))
        self.assertEqual(con.execute("select time, round, map_id, terror1, terror2, terror3, transformed_uid,"
                                     " other_uids, origin from rounds order by time").fetchall(),
                         [tuple(r) for r in self.ROWS], "元の番号のまま残っている")
        con.close()

    def test_the_sync_records_move_to_the_legacy_names(self):
        make_old_store(self.path, self.ROWS, {"my_uids": "7", "initial_done": "1", "synced_to": "80",
                                              "synced_uids": "7"})
        RoundStore.RoundStore(self.path).count()
        meta = meta_of(self.path)
        self.assertEqual(meta["terror_ids"], "own")
        self.assertEqual((meta["initial_done:legacy"], meta["synced_to:legacy"], meta["synced_uids:legacy"]),
                         ("1", "80", "7"))
        for key in ("initial_done", "synced_to", "synced_uids"):
            self.assertNotIn(key, meta)
        self.assertNotIn("initial_done:v2", meta, "v2 は最初に全部取る")
        self.assertEqual(meta["my_uids"], "7")

    def test_the_second_time_does_nothing(self):
        make_old_store(self.path, self.ROWS, {"my_uids": "7"})
        RoundStore.RoundStore(self.path).count()
        first = dump(self.path)
        bak = self.bak.read_bytes()
        RoundStore.RoundStore(self.path).count()
        self.assertEqual(dump(self.path), first)
        self.assertEqual(self.bak.read_bytes(), bak)

    def test_rows_that_become_the_same_row_are_one(self):
        """元では別の鍵で、変換後に同じ鍵になる行（変換できない 175 と 176 はどちらも NULL になる）は 1 行にまとまる"""
        make_old_store(self.path, [(10, 1, 12, 316, None, None, 7, None, "own")])
        con = sqlite3.connect(str(self.path))
        con.execute("insert into rounds values (10, 1, 12, 175, NULL, NULL, 7, NULL, 'own')")
        con.execute("insert into rounds values (10, 1, 12, 176, NULL, NULL, 7, NULL, 'db')")
        con.commit()
        con.close()
        with patch.object(DebugLog, "write") as write:
            store = RoundStore.RoundStore(self.path)
            self.assertEqual(store.count(), 2, "175 と 176 はどちらも NULL → 1 行にまとまる")
        self.assertTrue(any("重なって 1 行になった 1 行" in c.args[0] for c in write.call_args_list))

    def test_an_empty_store_gets_the_mark_and_no_backup(self):
        store = RoundStore.RoundStore(self.path)
        self.assertEqual(store.count(), 0)
        self.assertEqual(meta_of(self.path)["terror_ids"], "own")
        self.assertFalse(self.bak.exists())

    def test_an_existing_backup_is_not_overwritten(self):
        make_old_store(self.path, self.ROWS)
        self.bak.write_bytes(b"older backup")
        RoundStore.RoundStore(self.path).count()
        self.assertEqual(self.bak.read_bytes(), b"older backup")
        self.assertEqual(dump(self.path)[0][3], 3014, "移すのは移す")

    def test_when_the_backup_fails_nothing_is_rewritten_and_the_store_is_not_used(self):
        make_old_store(self.path, self.ROWS)
        store = RoundStore.RoundStore(self.path)
        with patch.object(RoundStore.os, "replace", side_effect=OSError("disk full")):
            self.assertFalse(store.add_own(100, 1, 12, 5, None, None, 7), "入れない")
        con = sqlite3.connect(str(self.path))
        self.assertEqual(con.execute("select terror1 from rounds order by time").fetchall(),
                         [(r[3],) for r in self.ROWS], "番号は元のまま")
        self.assertEqual(con.execute("select count(*) from rounds").fetchone()[0], len(self.ROWS))
        con.close()
        self.assertFalse(self.bak.exists())
        # 直れば次に移る
        self.assertTrue(store.add_own(100, 1, 12, 5, None, None, 7))
        self.assertTrue(self.bak.exists())

    def test_a_failure_in_the_middle_rolls_back(self):
        make_old_store(self.path, self.ROWS)
        store = RoundStore.RoundStore(self.path)
        with patch.object(DebugLog, "write", side_effect=RuntimeError("boom")):
            self.assertEqual(store.count(), 0, "失敗は count の既定値")
        con = sqlite3.connect(str(self.path))
        self.assertEqual(con.execute("select terror1 from rounds order by time").fetchall(),
                         [(r[3],) for r in self.ROWS])
        self.assertNotIn("terror_ids", dict(con.execute("select key, value from meta").fetchall()))
        con.close()

    def test_the_statistics_do_not_count_an_eight_pages_huggy_as_a_classic_one_in_the_raw_store(self):
        """店の中では 8 Pages の Huggy は 3014（Huggy の 0 とは別）。統計の側でまとめて数える"""
        make_old_store(self.path, self.ROWS)
        store = RoundStore.RoundStore(self.path)
        counts = store.terror_counts(RoundStore.Filter())
        self.assertEqual(counts[3014], 1)
        self.assertNotIn(0, counts)
        folded = Statistics.fold_counts(counts)
        self.assertEqual(folded[0], 1, "統計の側では Huggy に足す")
        self.assertNotIn(3014, folded)
        self.assertEqual(folded[1001], 2, "Whiteface: Alternate の 1 と 8 Pages の 1")
        self.assertEqual(folded[3046], 1, "8 Pages 限定の Baldi は 3000 番台のまま")


class TestEightPagesStatistics(unittest.TestCase):
    def test_the_same_terror_is_counted_together(self):
        self.assertEqual(TerrorIds.statistics_id(3014), 0)
        self.assertEqual(TerrorIds.statistics_id(3050), 1001)
        self.assertEqual(TerrorIds.statistics_id(3046), 3046)
        self.assertEqual(TerrorIds.statistics_id(5), 5)
        self.assertEqual(TerrorIds.statistics_id(99999), 99999)
        self.assertEqual(TerrorIds.statistics_ids(0), frozenset({0, 3014}))
        self.assertEqual(TerrorIds.statistics_ids(3046), frozenset({3046}))
        self.assertEqual(TerrorIds.statistics_ids(99999), frozenset({99999}))

    def test_no_eight_pages_row_is_a_candidate(self):
        for category in ("classic", "alternate", "unbound"):
            self.assertFalse(Statistics.candidate_ids_for_category(category) & TerrorIds.eight_pages_ids())

    def test_the_detail_queries_take_a_set_of_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RoundStore.RoundStore(Path(tmp) / "rounds.sqlite")
            store.add_own(10, 1, 12, 0, None, None, 7)             # Classic の Huggy
            store.add_own(20, 105, 12, 3014, None, None, 7)         # 8 Pages の Huggy
            store.add_own(30, 1, 12, 5, 3014, None, 7)
            flt = RoundStore.Filter()
            same = TerrorIds.statistics_ids(0)
            self.assertEqual(store.round_counts_for_terror(flt, same), [(1, 2), (105, 1)])
            self.assertEqual(store.round_counts_for_terror(flt, 0), [(1, 1)], "1 つだけ渡したときは今まで通り")
            total, rows = store.rounds_for_terror(flt, same, 10)
            self.assertEqual((total, [r[0] for r in rows]), (3, [30, 20, 10]))
            self.assertEqual(sorted(store.map_counts_for_terror(flt, same)), [(12, 1, 2), (12, 105, 1)])
            day = store.time_series(flt, "day", same)
            self.assertEqual(sum(hits for _k, _n, hits in day), 3)
            self.assertEqual(sum(hits for _k, _n, hits in store.time_series(flt, "day")), 0)

    def test_the_appearance_rows_remove_the_selected_terror_and_its_eight_pages_twin(self):
        rows = [(10, 105, 12, 3014, None, None, 7, None, "own"),
                (20, 1, 12, 5, 0, None, 7, None, "own")]
        shown = StatisticsGUI.appearance_rows(rows, 0, {7})
        self.assertEqual([row[3] for row in shown], ["", TerrorIds.name(5)], "8 Pages の Huggy だけ → 一緒に出たものは無い")


class TestRegisterRoundV2(unittest.TestCase):
    """新しい版は register_round_v2 へ、独自の番号と人数で送る（本物へは送らない）"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = RoundStore.RoundStore(Path(tmp.name) / "rounds.sqlite")

    def _send(self, round_name, ids, players=None):
        sent = []

        def urlopen(req, timeout=None):
            sent.append(req)
            res = MagicMock()
            res.__enter__ = MagicMock(return_value=res)
            res.__exit__ = MagicMock(return_value=False)
            res.status = 204
            return res

        with patch.object(ConnectDB, "_configured", return_value=True), \
             patch.object(ConnectDB, "SUPABASE_URL", "https://example.invalid"), \
             patch.object(ConnectDB, "_headers", return_value={}), \
             patch.object(ConnectDB.TaskStats, "thread", lambda _n, target, daemon=True: RunNow(target)), \
             patch.object(ConnectDB.urllib.request, "urlopen", side_effect=urlopen), \
             patch.object(RoundStore, "default_store", return_value=self.store), \
             patch("builtins.print"):
            ConnectDB.register_round(round_name, ids, 12, 7, round_time=1767225610, players=players)
        self.assertEqual(len(sent), 1)
        return sent[0], json.loads(sent[0].data.decode())

    def test_it_goes_to_v2_with_the_players(self):
        req, body = self._send("Classic", [5], players=3)
        self.assertTrue(req.full_url.endswith("/rest/v1/rpc/register_round_v2"))
        self.assertNotIn("ToNRounds_v2", req.full_url)
        self.assertEqual(body["p_players"], 3)
        self.assertEqual(self.store.rows(), [(10, 1, 12, 5, None, None, 7, None, "own")])
        self.assertEqual(dump(self.store.path)[0][9], 3)

    def test_an_unknown_count_is_null(self):
        _req, body = self._send("Classic", [5])
        self.assertIsNone(body["p_players"])

    def test_the_numbers_are_the_tools_own(self):
        _req, body = self._send("Alternate", [135])
        self.assertEqual(body["p_t1"], 1001)
        _req, body = self._send("Unbound", [283])
        self.assertEqual(body["p_t1"], 2083)
        _req, body = self._send("8 Pages", [135])        # 8 Pages の場面の 135 は Whiteface のページ
        self.assertEqual(body["p_t1"], 3050)
        _req, body = self._send("Classic", [314])
        self.assertEqual(body["p_t1"], 4009)
        self.assertEqual(self.store.rows()[-1][3], 4009, "手元にも独自の番号で貯める")

    def test_a_number_that_cannot_be_converted_is_not_sent(self):
        _req, body = self._send("Classic", [175, 5])
        self.assertEqual((body["p_t1"], body["p_t2"], body["p_t3"]), (5, None, None))

    def test_the_constants(self):
        self.assertEqual(ConnectDB.REGISTER_ROUND_RPC, "rpc/register_round_v2")
        self.assertEqual(ConnectDB.ROUNDS_TABLE, "ToNRounds_v2")


class TestFetchTable(unittest.TestCase):
    def test_the_table_is_chosen_by_the_caller(self):
        def pages(table):
            res = MagicMock()
            res.__enter__ = MagicMock(return_value=res)
            res.__exit__ = MagicMock(return_value=False)
            res.read.return_value = b"[]"
            with patch.object(ConnectDB, "_configured", return_value=True), \
                 patch.object(ConnectDB, "SUPABASE_URL", "https://example.invalid"), \
                 patch.object(ConnectDB, "_headers", return_value={}), \
                 patch.object(ConnectDB.urllib.request, "urlopen", return_value=res) as urlopen:
                kwargs = {} if table is None else {"table": table}
                list(ConnectDB.fetch_rounds(None, set(), **kwargs))
            return urlopen.call_args.args[0].full_url

        self.assertIn("/rest/v1/ToNRounds_v2?select", pages(None))
        self.assertIn("/rest/v1/ToNRounds?select", pages("ToNRounds"))
        self.assertIn("/rest/v1/ToNRounds?select", pages(LegacyRoundsTable.TABLE))


class TestLegacyRows(unittest.TestCase):
    def test_convert_changes_the_numbers_and_empties_the_count(self):
        row = {"time": 5, "round": 105, "map_id": 12, "terror1": 0, "terror2": None, "terror3": None,
               "transformed_uid": 9, "other_uids": [4], "players": 99}
        converted = LegacyRoundsTable.convert(row)
        self.assertEqual((converted["terror1"], converted["terror2"], converted["terror3"]), (3014, None, None))
        self.assertIsNone(converted["players"])
        self.assertEqual((converted["time"], converted["transformed_uid"], converted["other_uids"]), (5, 9, [4]))
        self.assertEqual(row["terror1"], 0, "元の行は変えない")
        other = LegacyRoundsTable.convert({"round": 6, "terror1": 192, "terror2": 316, "terror3": 175})
        self.assertEqual((other["terror1"], other["terror2"], other["terror3"]), (4002, 4011, None))

    def test_the_table_name(self):
        self.assertEqual((LegacyRoundsTable.NAME, LegacyRoundsTable.TABLE), ("legacy", "ToNRounds"))


class TestTwoSources(_TempCase):
    """v2 と無印を別々に進める。無印が消えて 404 になっても v2 は進む"""

    @staticmethod
    def _row(t, uid, t1=5, rnd=1, others=None, players=None):
        row = {"time": t, "round": rnd, "map_id": 12, "terror1": t1, "terror2": None, "terror3": None,
               "transformed_uid": uid, "other_uids": others}
        if players is not None:
            row["players"] = players
        return row

    def _sources(self, v2, legacy):
        return (RoundStore.Source("v2", v2),
                RoundStore.Source(LegacyRoundsTable.NAME, legacy, LegacyRoundsTable.convert))

    def test_each_source_has_its_own_records(self):
        store = RoundStore.RoundStore(self.path)
        calls = []

        def v2(since, mine):
            calls.append(("v2", since, set(mine)))
            return [self._row(100, 1, 1001, players=4)]

        def legacy(since, mine):
            calls.append(("legacy", since, set(mine)))
            return [self._row(200, 2, 135, rnd=51)]

        self.assertTrue(store.sync_all(self._sources(v2, legacy)))
        meta = meta_of(self.path)
        self.assertEqual((meta["synced_to:v2"], meta["synced_to:legacy"]), ("100", "200"))
        self.assertEqual((meta["initial_done:v2"], meta["initial_done:legacy"]), ("1", "1"))
        self.assertEqual(calls, [("v2", None, set()), ("legacy", None, set())])
        self.assertEqual([(r[0], r[3], r[9]) for r in dump(self.path)], [(100, 1001, 4), (200, 1001, None)],
                         "無印の 135（Alternate）は 1001・人数は NULL")
        store.sync_all(self._sources(v2, legacy))
        self.assertEqual([c[:2] for c in calls[2:]], [("v2", 100 - 900), ("legacy", 200 - 900)], "次は差分")

    def test_a_legacy_failure_does_not_stop_v2(self):
        store = RoundStore.RoundStore(self.path)

        def gone(since, mine):
            raise urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {}, None)

        with patch.object(DebugLog, "write") as write:
            self.assertTrue(store.sync_all(self._sources(lambda s, m: [self._row(100, 1)], gone)))
            self.assertTrue(store.sync_all(self._sources(lambda s, m: [self._row(300, 1, 6)], gone)))
        self.assertEqual(store.count(), 2, "v2 の分は入る")
        meta = meta_of(self.path)
        self.assertEqual(meta["synced_to:v2"], "300")
        self.assertNotIn("synced_to:legacy", meta, "無印の記録は変えない")
        self.assertNotIn("initial_done:legacy", meta)
        self.assertEqual(len([c for c in write.call_args_list if "legacy" in c.args[0]]), 1,
                         "無印の失敗は 1 回だけ書く")

    def test_a_legacy_failure_in_the_middle_of_the_pages_adds_nothing_from_legacy(self):
        store = RoundStore.RoundStore(self.path)

        def broken(since, mine):
            yield self._row(10, 1)
            raise OSError("cut")

        self.assertTrue(store.sync_all(self._sources(lambda s, m: [self._row(100, 1, 6)], broken)))
        self.assertEqual([r[0] for r in dump(self.path)], [100], "その回に入れた無印の行は戻る")
        self.assertNotIn("synced_to:legacy", meta_of(self.path))

    def test_the_return_value_is_the_v2_result(self):
        store = RoundStore.RoundStore(self.path)

        def offline(since, mine):
            raise OSError("offline")

        self.assertFalse(store.sync_all(self._sources(offline, lambda s, m: [])))
        self.assertTrue(store.sync_all(self._sources(lambda s, m: [], offline)))

    def test_the_same_round_from_both_is_one_row(self):
        """依頼者が無印の行を v2 へ写したあとの重なり（同じ鍵）は 1 行に上書きされる"""
        store = RoundStore.RoundStore(self.path)
        self.assertTrue(store.sync_all(self._sources(
            lambda s, m: [self._row(100, 1, 1001, rnd=51, players=3)],
            lambda s, m: [self._row(100, 1, 135, rnd=51)])))
        self.assertEqual(store.count(), 1)

    def test_a_legacy_row_with_me_as_second_player_replaces_my_own_row(self):
        store = RoundStore.RoundStore(self.path)
        store.add_own(100, 51, 12, 1001, None, None, -13, players=2)
        legacy_row = self._row(98, 7, 135, rnd=51, others=[-13])
        self.assertTrue(store.sync_all(self._sources(lambda s, m: [], lambda s, m: [legacy_row])))
        self.assertEqual([(r[0], r[6]) for r in store.rows()], [(98, 7)], "変換後の番号で自分の行と合わさる")

    def test_the_default_sources_are_v2_then_legacy(self):
        names = [s.name for s in RoundStore.sources()]
        self.assertEqual(names, ["v2", "legacy"])
        self.assertIs(RoundStore.sources()[0].fetch, ConnectDB.fetch_rounds)
        self.assertIs(RoundStore.sources()[1].fetch, LegacyRoundsTable.fetch)
        self.assertIs(RoundStore.sources()[1].convert, LegacyRoundsTable.convert)

    def test_the_legacy_fetch_reads_the_old_table(self):
        with patch.object(ConnectDB, "fetch_rounds", return_value=iter([])) as fetch:
            LegacyRoundsTable.fetch(5, {1})
        fetch.assert_called_once_with(5, {1}, table="ToNRounds")

    def test_the_window_syncs_both(self):
        """統計画面の取り込みは sync_all"""
        window = type("W", (), {})()
        window.store = MagicMock()
        window.store.sync_all.return_value = True
        window._on_rows_loaded = MagicMock()
        with patch.object(StatisticsGUI.UiInbox, "call_soon", lambda _w, f: f()):
            StatisticsGUI.StatisticsWindow._load_rows_worker(window)
        window.store.sync_all.assert_called_once_with()
        window._on_rows_loaded.assert_called_once_with(True)
