"""統計（DB v1・手元の保存・統計画面）"""
from tests.support import *  # noqa: F401,F403




class TestConnectDbEnv(unittest.TestCase):
    def test_env_file_candidates_include_source_and_repo_locations(self):
        candidates = ConnectDB.env_file_candidates()
        repo_root = REPO_ROOT
        legacy_resource_dir = repo_root / "ToNAutoBeginner"

        self.assertTrue(any(path.parent.name == "src" and path.name == ".env" for path in candidates))
        self.assertTrue(any(path.parent == repo_root and path.name == ".env" for path in candidates))
        self.assertTrue(any(path.parent == legacy_resource_dir and path.name == ".env" for path in candidates))

    def test_env_file_candidates_prefers_nuitka_containing_dir(self):
        class Compiled:
            containing_dir = "C:/packed-app"

        ConnectDB.__compiled__ = Compiled()
        try:
            self.assertEqual(ConnectDB.env_file_candidates()[0], Path("C:/packed-app") / ".env")
        finally:
            del ConnectDB.__compiled__




class TestQuietStatistics(unittest.TestCase):
    """看破・Enrage 系の DB 送信は print も出さない"""

    class RunNow:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            self.target()

    def _send(self, quiet, configured=True, fail=True):
        with patch.object(ConnectDB, "_configured", return_value=configured), \
             patch.object(ConnectDB, "_url", return_value="https://example.invalid/x"), \
             patch.object(ConnectDB, "_headers", return_value={}), \
             patch.object(ConnectDB.threading, "Thread", self.RunNow), \
             patch.object(ConnectDB.urllib.request, "urlopen",
                          side_effect=OSError("offline") if fail else None), \
             patch("builtins.print") as printed:
            ConnectDB.register_round("Fog", [101], 12, 99, quiet=quiet)
        return printed

    def test_quiet_prints_nothing(self):
        self._send(True).assert_not_called()
        self._send(True, configured=False).assert_not_called()

    def test_loud_still_prints(self):
        self.assertTrue(self._send(False).called)
        self.assertTrue(self._send(False, configured=False).called)




class TestNullTerrorRounds(unittest.TestCase):
    """ムーン系4つ・Run・Special（100〜104・106）はラウンド開始の行でテラー無しで1回だけ送る"""

    P = "2026.10.01 12:00:00 Debug      -  "

    def setUp(self):
        self.send = patch.object(ConnectDB, "register_round").start()
        self.addCleanup(patch.stopall)
        patch.object(LogMonitor.threading, "Thread").start()

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.transformed_uid = -13
        monitor.st.players_known = True          # ソロ（instance_key は None）
        return monitor

    def _round(self, monitor, round_type, *lines):
        monitor._process(self.P + f"This round is taking place at Sewers (12) and the round type is {round_type}")
        for line in lines:
            monitor._process(self.P + line)

    def test_the_ids(self):
        self.assertEqual(config.NULL_TERROR_ROUND_IDS, {100, 101, 102, 103, 104, 106})
        self.assertEqual(ConnectDB.round_type_id("Special"), 106)
        self.assertEqual(ConnectDB.round_type_id("GIGABYTE"), 106)
        self.assertEqual(config.ROUND_TYPE_NAMES[106], "Special")
        self.assertEqual(ConnectDB.round_row({"round": 106})["round"], "Special")

    def test_sent_once_at_the_start_with_no_terrors(self):
        for name in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice", "Run", "Special"):
            self.send.reset_mock()
            monitor = self._monitor()
            self._round(monitor, name)
            self.send.assert_called_once_with(name, [], 12, -13, instance_key=None,
                                              round_time=monitor.st.round_start_time)
            self.assertTrue(monitor.st.round_start_time)
            self._round_tail(monitor, name)
            self.assertEqual(self.send.call_count, 1, f"{name}: 二重に送らない")

    def _round_tail(self, monitor, name):
        for line in (f"Killers have been set - 0 0 0 // Round type is {name}",
                     f"Killers have been revealed - 0 0 0 // Round type is {name}",
                     "You died.", "RoundOver", "Verified Round End"):
            monitor._process(self.P + line)

    def test_the_next_round_sends_again(self):
        monitor = self._monitor()
        self._round(monitor, "Run", "RoundOver")
        self._round(monitor, "Twilight")
        self.assertEqual([c.args[0] for c in self.send.call_args_list], ["Run", "Twilight"])

    def test_classic_zero_is_still_sent_as_zero(self):
        monitor = self._monitor()
        self._round(monitor, "Classic", "Killers have been set - 0 0 0 // Round type is Classic",
                    "RoundOver")
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[:2], ("Classic", [0]))

    def test_other_rounds_without_terrors_are_still_not_sent(self):
        for name in ("Cold Night", "8 Pages", "Mystery"):
            self.send.reset_mock()
            monitor = self._monitor()
            self._round(monitor, name, "RoundOver", "Verified Round End")
            self.send.assert_not_called()

    def test_the_own_row_is_null_too(self):
        with tempfile.TemporaryDirectory() as d:
            store = RoundStore.RoundStore(Path(d) / "r.sqlite")
            sent = []

            def urlopen(req, timeout=None):
                sent.append(json.loads(req.data.decode()))
                res = MagicMock()
                res.__enter__ = MagicMock(return_value=res)
                res.__exit__ = MagicMock(return_value=False)
                res.status = 204
                return res

            patch.stopall()
            with patch.object(ConnectDB, "_configured", return_value=True), \
                 patch.object(ConnectDB, "SUPABASE_URL", "https://example.invalid"), \
                 patch.object(ConnectDB, "_headers", return_value={}), \
                 patch.object(ConnectDB.threading, "Thread", TestDbV1.RunNow), \
                 patch.object(ConnectDB.urllib.request, "urlopen", side_effect=urlopen), \
                 patch.object(RoundStore, "default_store", return_value=store), \
                 patch.object(LogMonitor.LogMonitor, "_start_daemon"), \
                 patch("builtins.print"):
                monitor = self._monitor()
                self._round(monitor, "Blood Moon", "Killers have been set - 0 0 0 // Round type is Blood Moon")
            self.assertEqual(len(sent), 1)
            self.assertEqual((sent[0]["p_round"], sent[0]["p_t1"], sent[0]["p_t2"], sent[0]["p_t3"]),
                             (101, None, None, None))
            [row] = store.rows()
            self.assertEqual(row[1:6], (101, 12, None, None, None))




class TestTerrorDetailQueries(unittest.TestCase):
    """選んだテラーの「ラウンドの種類」と「出現ラウンド」の問い合わせ（一時ファイルの RoundStore）"""

    MINE, T = -13, 101

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = RoundStore.RoundStore(Path(self._dir.name) / "rounds.sqlite")
        self.store.add_own(50, 2, 12, self.T, None, None, self.MINE)          # 自分（1人目）
        self.rows = [
            {"time": 100, "round": 1, "map_id": 12, "terror1": self.T, "terror2": 6, "terror3": None,
             "transformed_uid": 7, "other_uids": [self.MINE, 9]},
            {"time": 200, "round": 2, "map_id": 3, "terror1": 6, "terror2": None, "terror3": None,
             "transformed_uid": 7, "other_uids": None},
            {"time": 300, "round": 2, "map_id": 3, "terror1": self.T, "terror2": None, "terror3": None,
             "transformed_uid": 8, "other_uids": None},
            {"time": 400, "round": 7, "map_id": 3, "terror1": self.T, "terror2": self.T, "terror3": None,
             "transformed_uid": 8, "other_uids": None},
        ]
        self.store.sync(lambda _since, _mine: list(self.rows))

    def test_round_counts(self):
        self.assertEqual(self.store.round_counts_for_terror(RoundStore.Filter(), self.T),
                         [(2, 2), (7, 2), (1, 1)], "枠の数・多い順（同じ数は番号順）")
        self.assertEqual(StatisticsGUI.round_kind_rows([(2, 2), (7, 2), (1, 1)]),
                         [("Fog", 2, "40.0"), ("Double Trouble", 2, "40.0"), ("Classic", 1, "20.0")])

    def test_round_counts_follow_the_filter(self):
        self.assertEqual(self.store.round_counts_for_terror(RoundStore.Filter(start=150, end=350), self.T),
                         [(2, 1)])
        self.assertEqual(self.store.round_counts_for_terror(
            RoundStore.Filter(rounds=frozenset({1})), self.T), [(1, 1)])
        self.assertEqual(self.store.round_counts_for_terror(RoundStore.Filter(mine=True), self.T),
                         [(1, 1), (2, 1)])

    def test_rounds_newest_first(self):
        total, rows = self.store.rounds_for_terror(RoundStore.Filter(), self.T, 500)
        self.assertEqual(total, 4)
        self.assertEqual([r[0] for r in rows], [400, 300, 100, 50])
        total, rows = self.store.rounds_for_terror(RoundStore.Filter(mine=True), self.T, 500)
        self.assertEqual((total, [r[0] for r in rows]), (2, [100, 50]), "自分の分")
        total, rows = self.store.rounds_for_terror(
            RoundStore.Filter(start=150, end=450, rounds=frozenset({2})), self.T, 500)
        self.assertEqual((total, [r[0] for r in rows]), (1, [300]), "期間・ラウンド")

    def test_the_limit_keeps_the_total(self):
        for i in range(12):
            self.store.add_own(1000 + i, 1, 1, self.T, None, None, self.MINE)
        total, rows = self.store.rounds_for_terror(RoundStore.Filter(), self.T, 5)
        self.assertEqual(total, 16)
        self.assertEqual([r[0] for r in rows], [1011, 1010, 1009, 1008, 1007])

    def test_the_columns(self):
        _total, rows = self.store.rounds_for_terror(RoundStore.Filter(), self.T, 500)
        shown = StatisticsGUI.appearance_rows(rows, self.T, self.store.my_uids(), config.TERRORS)
        name = Statistics.terror_name
        when = lambda t: datetime.fromtimestamp(config.DB_TIME_EPOCH + t).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(shown[0], (when(400), "Double Trouble", Statistics.map_name_for_id(3, "Double Trouble"),
                                    name(self.T, config.TERRORS), 1, ""), "同じテラーが2枠なら1つは一緒に出た扱い")
        self.assertEqual(shown[1][3:], ("", 1, ""))
        self.assertEqual(shown[2], (when(100), "Classic", Statistics.map_name_for_id(12, "Classic"),
                                    name(6, config.TERRORS), 3, "✓"), "other_uids に自分")
        self.assertEqual(shown[3][4:], (1, "✓"), "1人目が自分")




class TestTerrorSearch(unittest.TestCase):
    """テラーの検索（名前の一部・大文字小文字・ID。分類をまたぐ）"""

    def _row(self, tid, name, p, count=1):
        return Statistics.TerrorStatistic(tid, name, count, 1.0, p, "")

    def test_matching(self):
        row = self._row(101, "Immortal Snail", 0.5)
        for q, hit in (("snail", True), ("SNAIL", True), ("tal Sn", True), ("101", True), (" 101 ", True),
                       ("10", False), ("1011", False), ("dog", False), ("", True)):
            self.assertEqual(StatisticsGUI.terror_matches(row, q), hit, q)

    def test_across_categories_in_the_usual_order(self):
        found = StatisticsGUI.search_terror_rows(
            [("Classic", [self._row(1, "Huggy", 0.3), self._row(2, "Sonic", 0.1)]),
             ("Alternate", [self._row(140, "Hug Knight", 0.1, count=5)]),
             ("Unbound", [self._row(230, "Bug Hug", 0.9)])], "hug")
        self.assertEqual([(c, r.terror_id) for c, r in found],
                         [("Alternate", 140), ("Classic", 1), ("Unbound", 230)])




class TestStatisticsTerrorTab(unittest.TestCase):
    """統計画面のテラーのタブ（本物の Tk。通信は差し替え。手元は一時フォルダ）"""

    MINE = -13

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = RoundStore.RoundStore(Path(self._dir.name) / "rounds.sqlite")
        classic = sorted(int(i) for i in config.TERRORS["classic"])[:3]
        alternate = sorted(int(i) for i in config.TERRORS["alternate"])[:2]
        self.ids = classic + alternate
        for i, tid in enumerate(self.ids):
            self.store.add_own(100 + i, 6, 12, tid, None, None, self.MINE)    # Bloodbath（既定で選ばれる）
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(ConnectDB, "fetch_rounds", side_effect=lambda s, m: []), \
             patch.object(StatisticsGUI.threading, "Thread", TestDbV1.RunNow):
            self.window = StatisticsGUI.StatisticsWindow(self.root, store=self.store)
            for _ in range(400):
                self.root.update()
                if not self.window._rows_loading:
                    break
                time.sleep(0.005)
        self.root.update()

    def _listed(self):
        """[(見せている ID, 中の番号)]（分類の列は無くなり、ID はゲームの ID）"""
        tree = self.window.terror_tree
        return [(int(tree.item(i, "values")[0]), int(i)) for i in tree.get_children()]

    def test_search_crosses_categories_and_clearing_goes_back(self):
        self.window._set_terror_category("classic")
        before = self._listed()
        self.assertTrue(all(shown == tid for shown, tid in before), "Classic はそのまま")

        alt = self.ids[3]
        game = alt - MatchTNL.ALTERNATE_OFFSET
        self.window.v_terror_search.set(str(game))
        listed = self._listed()
        self.assertIn((game, alt), listed, "分類をまたいで、ゲームの ID で合う")
        tree = self.window.terror_tree
        for shown, tid in listed:     # ゲームの ID が合うか、名前に数字を含む行だけ
            self.assertTrue(shown == game or str(game) in tree.item(str(tid), "values")[1], (shown, tid))
        name = Statistics.terror_name(self.ids[0], config.TERRORS)
        self.window.v_terror_search.set(name[1:4].swapcase())
        self.assertIn((self.ids[0], self.ids[0]), self._listed())

        self.window.v_terror_search.set("")
        self.assertEqual(self._listed(), before)

    def test_selecting_fills_the_three_detail_tabs(self):
        self.window._set_terror_category("classic")
        tid = self.ids[0]
        self.window.terror_tree.selection_set(str(tid))
        self.window._on_terror_selected()
        self.assertEqual([self.window.terror_detail_tabs.tab(t, "text")
                          for t in self.window.terror_detail_tabs.tabs()],
                         ["出現ラウンド", "ラウンドタイプ", "マップ"])
        self.assertEqual(self.window.terror_detail_tabs.index("current"), 0, "最初に見えるのは出現ラウンド")
        kinds = [self.window.round_kind_tree.item(i, "values")
                 for i in self.window.round_kind_tree.get_children()]
        self.assertEqual(kinds, [("Bloodbath", "1", "100.0")])
        rounds = [self.window.terror_rounds_tree.item(i, "values")
                  for i in self.window.terror_rounds_tree.get_children()]
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0][-1], "✓")
        self.assertEqual(self.window.v_terror_rounds_note.get(), "")
        maps = [self.window.map_tree.item(i, "values") for i in self.window.map_tree.get_children()]
        self.assertEqual(len(maps), 1, "マップのタブにも入る")

    def test_more_than_500_shows_the_note(self):
        tid = self.ids[0]
        for i in range(StatisticsGUI.TERROR_ROUNDS_LIMIT + 1):
            self.store.add_own(1000 + i, 6, 12, tid, None, None, self.MINE)
        self.window._filter = None
        self.window._analyze()
        self.window._set_terror_category("classic")
        self.window.terror_tree.selection_set(str(tid))
        self.window._on_terror_selected()
        self.assertEqual(len(self.window.terror_rounds_tree.get_children()),
                         StatisticsGUI.TERROR_ROUNDS_LIMIT)
        self.assertEqual(self.window.v_terror_rounds_note.get(),
                         f"新しい順に500件を表示（全{StatisticsGUI.TERROR_ROUNDS_LIMIT + 2}件）")




class TestStatisticsFixes(unittest.TestCase):
    """統計画面の直し（選んだらすぐ集計・分類の列なし・ゲームの ID・チップの組）"""

    LOWER = ["Classic", "Run", "Mystic Moon", "Blood Moon", "Twilight", "Solstice", "Special"]

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = RoundStore.RoundStore(Path(self._dir.name) / "rounds.sqlite")
        for i, rid in enumerate((1, 2, 6, 11, 12, 100, 101, 102, 103, 104, 106, 107)):
            self.store.add_own(100 + i, rid, 12, None, None, None, -13)
        self.store.add_own(200, 6, 12, 135, None, None, -13)
        self.unbound = min(int(i) for i in config.TERRORS["unbound"])
        self.store.add_own(201, 10, 12, self.unbound, None, None, -13)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(ConnectDB, "fetch_rounds", side_effect=lambda s, m: []), \
             patch.object(StatisticsGUI.threading, "Thread", TestDbV1.RunNow):
            self.window = StatisticsGUI.StatisticsWindow(self.root, store=self.store)
            self.root.update()

    def _chips(self):
        """[(並び, 名前)]。区切り線は None"""
        out = []
        for child in sorted(self.window.round_chip_frame.grid_slaves(),
                            key=lambda w: (int(w.grid_info()["row"]), int(w.grid_info()["column"]))):
            out.append(getattr(child, "round_name", None) if isinstance(child, tk.Checkbutton) else None)
        return out

    def _pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)

    # ── 4. チップの組 ───────────────────────────
    def test_the_lower_group_and_its_order(self):
        chips = self._chips()
        line = chips.index(None)
        self.assertEqual(chips[line + 1:line + 1 + len(self.LOWER)], self.LOWER, "下の段の組と順番")
        upper = [c for c in chips[:line] if c]
        self.assertEqual(upper[-2:], ["Randomizer", "Classic.exe"], "上の段の最後")
        self.assertNotIn("Classic", upper)
        self.assertEqual(chips[-1], "Cold Night", "どの表にも無い名前は最後")

    def test_the_first_selection_leaves_the_lower_group_out(self):
        selected = self.window._selected_rounds()
        self.assertFalse(selected & set(self.LOWER), selected)
        self.assertIn("Fog", selected)
        self.assertIn("Cold Night", selected)

    def test_select_all_skips_the_lower_group_and_clear_clears_all(self):
        with patch.object(self.window, "_analyze"):
            self.window._clear_round_selection()
            self.window._select_all_rounds()
        selected = self.window._selected_rounds()
        self.assertFalse(selected & set(self.LOWER))
        self.assertTrue({"Fog", "Bloodbath", "Randomizer", "Classic.exe", "Cold Night"} <= selected)
        with patch.object(self.window, "_analyze"):
            self.window._clear_round_selection()
        self.assertEqual(self.window._selected_rounds(), set())

    def test_only_classic_and_run_are_always_shown(self):
        entries = StatisticsGUI._ordered_round_entries(["Fog"])
        names = {name for _label, name in entries if name}
        self.assertEqual(names, {"Fog", "Classic", "Run"})

    # ── 1. 選んだらすぐ集計 ─────────────────────────
    def test_chips_and_buttons_reanalyze_once_after_300ms(self):
        self.assertEqual(StatisticsGUI.ROUND_REANALYZE_DELAY_MS, 300)
        chip = next(w for w in self.window.round_chip_frame.winfo_children()
                    if isinstance(w, tk.Checkbutton) and getattr(w, "round_name", "") == "Fog")
        called = []
        with patch.object(self.window, "_analyze", side_effect=lambda: called.append(time.monotonic())):
            chip.invoke()
            self.window._select_all_rounds()
            self.window._clear_round_selection()
            self.window._select_all_rounds()
            last = time.monotonic()
            self._pump(0.7)
        self.assertEqual(len(called), 1, "続けて押しても1回")
        self.assertGreaterEqual(called[0] - last, 0.28, "最後に押してから 0.3 秒後")

    def test_each_kind_alone_reanalyzes(self):
        chip = next(w for w in self.window.round_chip_frame.winfo_children()
                    if isinstance(w, tk.Checkbutton))
        for action in (chip.invoke, self.window._select_all_rounds, self.window._clear_round_selection):
            with patch.object(self.window, "_analyze") as analyze:
                action()
                self._pump(0.45)
                analyze.assert_called_once()

    def test_closing_cancels_the_pending_reanalyze(self):
        with patch.object(StatisticsGUI.StatisticsWindow, "_analyze") as analyze:
            self.window._select_all_rounds()
            job = self.window._reanalyze_job
            self.assertIn(job, self.root.tk.splitlist(self.root.tk.call("after", "info")))
            self.window.destroy()
            self.assertNotIn(job, self.root.tk.splitlist(self.root.tk.call("after", "info")),
                             "予約を取り消す")
            self._pump(0.45)
        analyze.assert_not_called()

    # ── 2・3. 分類の列なし・ゲームの ID ──────────────────
    def test_no_category_column(self):
        self.assertNotIn("category", self.window.terror_tree["columns"])
        self.assertEqual(self.window.terror_tree["columns"][0], "id")

    def test_ids_are_game_ids(self):
        self.assertEqual(StatisticsGUI.game_terror_id("Classic", 140), 140, "Classic の 134 以上はそのまま")
        self.assertEqual(StatisticsGUI.game_terror_id("Alternate", 135), 1)
        self.assertEqual(StatisticsGUI.game_terror_id("Unbound", 205), 5)
        self.window._set_terror_category("alternate")
        tree = self.window.terror_tree
        shown = {int(i): int(tree.item(i, "values")[0]) for i in tree.get_children()}
        self.assertEqual(shown[135], 1)
        self.assertTrue(all(v == k - MatchTNL.ALTERNATE_OFFSET for k, v in shown.items()))
        self.window._set_terror_category("unbound")
        shown = {int(i): int(tree.item(i, "values")[0]) for i in tree.get_children()}
        self.assertTrue(shown)
        self.assertTrue(all(v == k - MatchTNL.UNBOUND_OFFSET for k, v in shown.items()))

    def test_search_by_game_id(self):
        row = lambda tid: Statistics.TerrorStatistic(tid, "Terror", 1, 1.0, 0.5, "")
        self.assertTrue(StatisticsGUI.terror_matches(row(205), "5", "Unbound"))
        self.assertFalse(StatisticsGUI.terror_matches(row(205), "205", "Unbound"))
        self.assertTrue(StatisticsGUI.terror_matches(row(139), "5", "Alternate"))
        self.assertTrue(StatisticsGUI.terror_matches(row(140), "140", "Classic"))
        found = StatisticsGUI.search_terror_rows(
            [("Classic", [row(5), row(140)]), ("Alternate", [row(139)]), ("Unbound", [row(205)])], "5")
        self.assertEqual(sorted(r.terror_id for _c, r in found), [5, 139, 205])




class TestStatisticsSelectButtons(unittest.TestCase):
    """「Classic出現」「Alternate出現」で、その組のラウンドだけを選ぶ"""

    CLASSIC = {"Fog", "Ghost", "Punished", "Sabotage", "Bloodbath", "Double Trouble", "Bloodbath EX",
               "Cracked", "Midnight", "Randomizer", "Classic.exe"}
    ALTERNATE = {"Alternate", "Fog (Alternate)", "Ghost (Alternate)", "Midnight"}

    _pump = TestStatisticsFixes._pump

    def setUp(self):
        TestStatisticsFixes.setUp(self)
        # すべてのラウンドを一覧に（Unbound・8 Pages・Classic など組に入らないものも）
        for i, rid in enumerate((3, 4, 5, 7, 8, 9, 10, 50, 51, 52, 53, 105)):
            self.store.add_own(300 + i, rid, 12, None, None, None, -13)
        self.window._populate_rounds()

    def _button(self, text):
        for frame in self.window.winfo_children():
            stack = [frame]
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                if isinstance(w, ttk.Button) and w.cget("text") == text:
                    return w
        self.fail(text)

    def test_each_button_selects_only_its_group(self):
        for text, expected in (("Classicテラー", self.CLASSIC), ("Alternateテラー", self.ALTERNATE)):
            with patch.object(self.window, "_analyze"):
                self.window._select_all_rounds()
                self._pump(0.45)                    # 全選択の分の集計は済ませておく
            with patch.object(self.window, "_analyze") as analyze:
                self._button(text).invoke()
                self.assertEqual(self.window._selected_rounds(), expected, text)
                self._pump(0.45)
                analyze.assert_called_once()
        self.assertFalse({"Classic", "8 Pages", "Unbound", "Run"} & self.CLASSIC)

    def test_missing_rounds_are_skipped(self):
        for name in ("Fog (Alternate)", "Ghost (Alternate)"):
            self.window.round_vars.pop(name, None)
        with patch.object(self.window, "_analyze"):
            self.window._select_only_rounds(StatisticsGUI.ROUND_SELECT_PRESETS[1][1])
        self.assertEqual(self.window._selected_rounds(), {"Alternate", "Midnight"})

    def test_aliases_are_selected_too(self):
        self.assertIn("Punish", StatisticsGUI._with_aliases({"Punished"}))
        self.assertIn("Fog(Alternate)", StatisticsGUI._with_aliases({"Fog (Alternate)"}))
        self.assertIn("Ghost Alternate", StatisticsGUI._with_aliases({"Ghost (Alternate)"}))
        self.window.round_vars["Punish"] = self.window.round_vars.pop("Punished")
        with patch.object(self.window, "_analyze"):
            self.window._select_only_rounds(StatisticsGUI.ROUND_SELECT_PRESETS[0][1])
        self.assertIn("Punish", self.window._selected_rounds())

    def test_the_presets(self):
        self.assertEqual([(label, set(names)) for label, names in StatisticsGUI.ROUND_SELECT_PRESETS],
                         [("Classicテラー", self.CLASSIC), ("Alternateテラー", self.ALTERNATE)])




class TestStatisticsColors(unittest.TestCase):
    """凡例の背景・ラウンドの色を固定・Unbound の5体を赤・ボタンの名前"""

    _pump = TestStatisticsFixes._pump

    def setUp(self):
        TestStatisticsFixes.setUp(self)

    def test_every_table_has_the_dark_background(self):
        """統計画面の表はすべて暗い背景（Legend.Treeview）"""
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup(StatisticsGUI.LEGEND_STYLE, "background"), StatisticsGUI.BG)
        self.assertEqual(style.lookup(StatisticsGUI.LEGEND_STYLE, "fieldbackground"), StatisticsGUI.BG)
        self.assertEqual(style.lookup(StatisticsGUI.LEGEND_STYLE, "foreground"), StatisticsGUI.FG,
                         "色の付いていない行は明るい文字")
        for tree in (self.window.round_legend, self.window.terror_tree, self.window.map_tree,
                     self.window.terror_rounds_tree, self.window.round_kind_tree):
            self.assertEqual(str(tree.cget("style")), StatisticsGUI.LEGEND_STYLE, tree)
        self.assertNotEqual(style.lookup("Treeview", "background"), StatisticsGUI.BG, "既定の Style は変えない")

    def test_the_colors_are_fixed_per_round(self):
        rows = [("Fog", 9, 9), ("Bloodbath", 5, 5), ("Cold Night", 3, 3), ("Punished", 2, 2), ("Mystery", 1, 1)]
        colors = dict(zip([r[0] for r in rows], StatisticsGUI.round_colors(rows)))
        self.assertEqual(colors["Fog"], "#9a9a9a")
        self.assertEqual(colors["Bloodbath"], "#ff4d4d")
        self.assertEqual(colors["Punished"], "#ffd43b", "表示名（Punish）で引く")
        self.assertEqual(colors["Cold Night"], StatisticsGUI.ROUND_CHART_COLORS[0], "表に無いものは今の色から順に")
        self.assertEqual(colors["Mystery"], StatisticsGUI.ROUND_CHART_COLORS[1])
        flipped = dict(zip([r[0] for r in reversed(rows)], StatisticsGUI.round_colors(list(reversed(rows)))))
        for name in ("Fog", "Bloodbath", "Punished"):
            self.assertEqual(flipped[name], colors[name], "回数の順に関係ない")

    def test_the_table(self):
        expected = {"Bloodbath": "#ff4d4d", "Midnight": "#a8102a", "Bloodbath EX": "#e8607a",
                    "Double Trouble": "#ff7a7a", "Fog": "#9a9a9a", "Fog(Alternate)": "#5f5f5f",
                    "Alternate": "#ffffff", "8 Pages": "#d4d4d4", "Punish": "#ffd43b", "Twilight": "#d9b44a",
                    "Ghost": "#7fdcff", "Ghost(Alternate)": "#2e8fbf", "Run": "#ff9900", "Unbound": "#ff8c1a",
                    "Blood Moon": "#7a1e1e", "Mystic Moon": "#2f55c8", "Solstice": "#4caf50",
                    "Classic": "#efe3c2", "Cracked": "#cba6f7", "Sabotage": "#a6e3a1",
                    "Randomizer": "#f5c2e7", "Classic.exe": "#b4befe", "Special": "#94e2d5"}
        self.assertEqual(StatisticsGUI.ROUND_COLORS, expected)

    def test_the_chart_and_the_legend_use_the_same_colors(self):
        legend = self.window.round_legend
        legend_colors = {legend.item(i, "values")[1]: str(legend.tag_configure(legend.item(i, "tags")[0], "foreground"))
                         for i in legend.get_children()}
        drawn = []
        chart = self.window.round_chart
        with patch.object(chart, "winfo_width", return_value=400),              patch.object(chart, "winfo_height", return_value=300),              patch.object(self.window, "_draw_round_slice",
                          side_effect=lambda *a: drawn.append(a[-1])):
            self.window._draw_round_chart()
        names = [StatisticsGUI._round_display_name(r[0]) for r in self.window._round_chart_rows]
        self.assertTrue(names)
        self.assertEqual(drawn, [legend_colors[n] for n in names])
        self.assertEqual(legend_colors.get("Fog"), "#9a9a9a")

    def test_the_button_names(self):
        self.assertEqual([label for label, _n in StatisticsGUI.ROUND_SELECT_PRESETS],
                         ["Classicテラー", "Alternateテラー"])




class TestTerrorColors(unittest.TestCase):
    """テラーの一覧の文字色の表・内訳の2つの表をラウンドの色に・Unbound ではラウンドタイプを隠す"""

    _pump = TestStatisticsFixes._pump

    def setUp(self):
        TestStatisticsFixes.setUp(self)
        for i, tid in enumerate((164, 168, 163)):
            self.store.add_own(400 + i, 51, 12, tid, None, None, -13)      # Alternate
        self.store.add_own(410, 2, 12, 135, None, None, -13)
        self.store.add_own(420, 6, 12, 5, None, None, -13)                 # Classic のテラー（Bloodbath）
        self.window._filter = None
        self.window._analyze()

    def _color(self, iid):
        tree = self.window.terror_tree
        tags = tree.item(str(iid), "tags")
        if not tags:
            return None, False
        font = str(tree.tag_configure(tags[0], "font"))
        return str(tree.tag_configure(tags[0], "foreground")), "bold" in font

    def test_the_table(self):
        self.assertEqual(StatisticsGUI.TERROR_TEXT_COLORS, {
            164: ("#ffe600", False), 168: ("#388e3c", False), 163: ("#d84315", False),
            209: ("#d00000", True), 211: ("#d00000", True), 231: ("#d00000", True),
            263: ("#d00000", True), 280: ("#d00000", True),
            264: ("#e57373", False), 240: ("#e57373", False), 246: ("#e57373", False),
            221: ("#e57373", False), 205: ("#e57373", False)})       # Triple Munci・Quadruple Sponge
        self.assertFalse(hasattr(StatisticsGUI, "HIGHLIGHT_UNBOUND_TERROR_IDS"), "表1つに寄せた")

    def test_alternate_colors_and_others_unchanged(self):
        self.window._set_terror_category("alternate")
        self.assertEqual(self._color(164), ("#ffe600", False))
        self.assertEqual(self._color(168), ("#388e3c", False))
        self.assertEqual(self._color(163), ("#d84315", False))
        self.assertEqual(self._color(135), (None, False), "ほかは今のまま")

    def test_unbound_colors_and_bold(self):
        self.window._set_terror_category("unbound")
        ids = {int(i) for i in self.window.terror_tree.get_children()}
        for tid in (209, 211, 231, 263, 280):
            if tid in ids:
                self.assertEqual(self._color(tid), ("#d00000", True), tid)
        for tid in (264, 240, 246, 221, 205):
            if tid in ids:
                self.assertEqual(self._color(tid), ("#e57373", False), tid)
        plain = sorted(ids - set(StatisticsGUI.TERROR_TEXT_COLORS))
        self.assertTrue(plain)
        self.assertEqual(self._color(plain[0]), (None, False))
        self.assertTrue({209, 264} <= ids, ids)

    def test_the_search_results_use_the_same_colors(self):
        self.window.v_terror_search.set(str(164 - MatchTNL.ALTERNATE_OFFSET))
        self.assertIn("164", self.window.terror_tree.get_children())
        self.assertEqual(self._color(164), ("#ffe600", False))
        self.window.v_terror_search.set(str(209 - MatchTNL.UNBOUND_OFFSET))
        self.assertEqual(self._color(209), ("#d00000", True))

    def test_the_two_detail_tables_are_dark_with_round_colors(self):
        for tree in (self.window.terror_rounds_tree, self.window.round_kind_tree):
            self.assertEqual(str(tree.cget("style")), StatisticsGUI.LEGEND_STYLE)
        self.assertEqual(str(self.window.map_tree.cget("style")), StatisticsGUI.LEGEND_STYLE, "マップも暗い")
        self._select("classic", 5)
        for tree, column in ((self.window.terror_rounds_tree, 1), (self.window.round_kind_tree, 0)):
            rows = tree.get_children()
            self.assertTrue(rows)
            names = [tree.item(i, "values")[column] for i in rows]
            colors = [str(tree.tag_configure(tree.item(i, "tags")[0], "foreground")) for i in rows]
            self.assertEqual(colors, StatisticsGUI.round_colors([(n, 0, 0) for n in names]))
            self.assertIn(StatisticsGUI.ROUND_COLORS[StatisticsGUI._round_display_name(names[0])], colors)

    def _select(self, category, iid):
        self.window._set_terror_category(category)
        self.window.terror_tree.selection_set(str(iid))
        self.window._on_terror_selected()

    def _tab_texts(self):
        tabs = self.window.terror_detail_tabs
        return [tabs.tab(t, "text") for t in tabs.tabs() if tabs.tab(t, "state") != "hidden"]

    def test_unbound_hides_round_type_and_others_bring_it_back(self):
        tabs = self.window.terror_detail_tabs
        tabs.select(self.window._kind_tab)
        self._select("unbound", 209)
        self.assertEqual(self._tab_texts(), ["出現ラウンド", "マップ"])
        self.assertEqual(str(tabs.select()), str(self.window._rounds_tab), "隠したら出現ラウンドへ")
        self._select("alternate", 164)
        self.assertEqual(self._tab_texts(), ["出現ラウンド", "ラウンドタイプ", "マップ"], "並びはそのまま")
        self._select("unbound", 209)
        self._select("classic", 5)
        self.assertEqual(self._tab_texts(), ["出現ラウンド", "ラウンドタイプ", "マップ"])

    def test_hiding_keeps_another_selected_tab(self):
        tabs = self.window.terror_detail_tabs
        map_tab = tabs.tabs()[2]
        tabs.select(map_tab)
        self._select("unbound", 209)
        self.assertEqual(str(tabs.select()), str(map_tab))




class TestDbV1(unittest.TestCase):
    """DB v1: ラウンドを "ToNRounds" へ関数 register_round で送る（通信は差し替え）"""

    class RunNow:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            self.target()

    def _send(self, *args, **kwargs):
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
             patch.object(ConnectDB.threading, "Thread", self.RunNow), \
             patch.object(ConnectDB.urllib.request, "urlopen", side_effect=urlopen), \
             patch("builtins.print"):
            ConnectDB.register_round(*args, **kwargs)
        self.assertEqual(len(sent), 1)
        return sent[0], json.loads(sent[0].data.decode())

    # 2026-01-01 00:00:10 UTC
    START = 1767225600 + 10

    def test_the_payload(self):
        req, body = self._send("Fog", [101, 7, 3], 12, -13, instance_key=42,
                               round_time=self.START + 0.9)

        self.assertEqual(req.full_url, "https://example.invalid/rest/v1/rpc/register_round")
        self.assertEqual(req.get_method(), "POST")
        self.assertNotIn("ToNRoundStatistics", req.full_url, "旧テーブルへは送らない")
        self.assertEqual(body, {"p_instance": 42, "p_time": 10, "p_round": 2, "p_map": 12,
                                "p_t1": 101, "p_t2": 7, "p_t3": 3, "p_uid": -13})

    def test_the_terrors_are_the_first_three(self):
        _r, one = self._send("Classic", [5], 1, 1, round_time=self.START)
        _r, four = self._send("Classic", [5, 6, 7, 8], 1, 1, round_time=self.START)
        _r, none = self._send("Classic", [], 1, 1, round_time=self.START)

        self.assertEqual((one["p_t1"], one["p_t2"], one["p_t3"]), (5, None, None))
        self.assertEqual((four["p_t1"], four["p_t2"], four["p_t3"]), (5, 6, 7))
        self.assertEqual((none["p_t1"], none["p_t2"], none["p_t3"]), (None, None, None))
        self.assertIsNone(one["p_instance"], "ソロ（instance_key なし）は NULL")

    def test_round_numbers(self):
        for name, number in (("Classic", 1), ("Sabotage", 4), ("Sabotage star", 4),
                             ("Sabotage murder", 4), ("Bloodbath EX", 8), ("Midnight", 50),
                             ("Twilight", 102), ("Run", 104), ("8 Pages", 105),
                             ("Cold Night", 107)):
            self.assertEqual(ConnectDB.round_type_id(name), number, name)

    def test_an_unknown_round_is_999_and_noted_in_the_debug_log(self):
        with patch.object(DebugLog, "write") as write:
            _r, body = self._send("Mystery", [1], 1, 1, round_time=self.START)   # Special は 106 になった
            _r, japanese = self._send("クラシック", [1], 1, 1, round_time=self.START)
            self._send("Classic", [1], 1, 1, round_time=self.START)

        self.assertEqual((body["p_round"], japanese["p_round"]), (999, 999))
        self.assertEqual(write.call_args_list,
                         [call("未知のラウンド名: 'Mystery'"), call("未知のラウンド名: 'クラシック'")])

    def test_the_instance_key(self):
        a = ConnectDB.instance_key("wrld_x:123~private(usr_a)~region(jp)")
        self.assertEqual(a, ConnectDB.instance_key("wrld_x:123~private(usr_a)~region(jp)"))
        self.assertNotEqual(a, ConnectDB.instance_key("wrld_x:124~private(usr_a)~region(jp)"))
        values = [ConnectDB.instance_key(f"wrld_x:{i}") for i in range(200)]
        self.assertTrue(all(-2 ** 63 <= v < 2 ** 63 for v in values))
        self.assertTrue(any(v < 0 for v in values), "符号つき")
        self.assertTrue(any(abs(v) >= 2 ** 40 for v in values), "8バイトを使っている")
        self.assertIsNone(ConnectDB.instance_key(""))

    def test_db_time(self):
        self.assertEqual(ConnectDB.db_time(1767225600), 0)
        self.assertEqual(ConnectDB.db_time(1767225600 + 3600.7), 3600)

    # ── 読む側 ────────────────────────────────
    def test_a_v1_row_is_turned_into_the_old_shape(self):
        row = ConnectDB.round_row({"time": 10, "round": 4, "map_id": 12, "terror1": 7,
                                   "terror2": None, "terror3": 9, "transformed_uid": -13,
                                   "other_uids": [-14, -15]})
        self.assertEqual(row, {"created_at": "2026-01-01T00:00:10Z", "round": "Sabotage",
                               "terror_ids": [7, 9], "map_id": 12, "transformed_uid": -13})
        self.assertEqual(ConnectDB.round_row({"time": 0, "round": 999})["round"], "999")
        self.assertEqual(Statistics.row_datetime(row).year, 2026)




class TestRoundStoreOwn(unittest.TestCase):
    """統計画面 v1: 自分が送ったラウンドを手元の SQLite に貯める（一時フォルダ）"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = RoundStore.RoundStore(Path(self._dir.name) / "rounds.sqlite")

    def test_an_own_row_is_stored_with_its_uid(self):
        self.assertTrue(self.store.add_own(100, 2, 12, 101, None, None, -13))

        self.assertEqual(self.store.rows(), [(100, 2, 12, 101, None, None, -13, None, "own")])
        self.assertEqual(self.store.my_uids(), {-13})

    def test_without_a_uid_nothing_is_stored(self):
        self.assertFalse(self.store.add_own(100, 2, 12, 101, None, None, None))
        self.assertEqual(self.store.rows(), [])
        self.assertEqual(self.store.my_uids(), set())

    def test_my_uids_collect_every_account(self):
        self.store.add_own(100, 1, 1, 5, None, None, -13)
        self.store.add_own(200, 1, 1, 5, None, None, 40)
        self.store.add_own(300, 1, 1, 5, None, None, -13)
        self.assertEqual(self.store.my_uids(), {-13, 40})

    def test_the_same_uid_and_second_in_two_windows_are_both_kept(self):
        """同じアカウントを複数の窓で動かすと、同じ秒に別々のラウンドが始まりうる"""
        self.store.add_own(100, 1, 12, 5, None, None, -13)
        self.store.add_own(100, 6, 12, 5, 6, 7, -13)          # 別のラウンド
        self.store.add_own(100, 1, 34, 5, None, None, -13)     # 別のマップ
        self.store.add_own(100, 1, 12, 9, None, None, -13)     # 別のテラー
        self.store.add_own(100, 1, 12, 5, None, None, -13)     # 全く同じ → 1行
        self.assertEqual(self.store.count(), 4)

    def test_a_broken_place_does_not_raise(self):
        blocker = Path(self._dir.name) / "file"
        blocker.write_text("x", encoding="utf-8")
        store = RoundStore.RoundStore(blocker / "rounds.sqlite")
        self.assertFalse(store.add_own(100, 1, 1, 1, None, None, 1))
        self.assertEqual(store.rows(), [])
        self.assertEqual(store.my_uids(), set())

    def test_the_real_place_is_next_to_settings(self):
        self.assertEqual(_real_paths["rounds"].name, "rounds.sqlite")
        self.assertEqual(_real_paths["rounds"].parent, _real_paths["settings"].parent)




class TestRegisterRoundKeepsOwnRows(unittest.TestCase):
    """register_round で DB へ送るとき、手元にも自分の行（送った値そのまま）"""

    RunNow = TestDbV1.RunNow

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        p = patch.object(config, "ROUND_STORE_PATH", Path(self._dir.name) / "rounds.sqlite")
        p.start()
        self.addCleanup(p.stop)
        self.store = RoundStore.default_store()

    def _send(self, *args, **kwargs):
        return TestDbV1._send(self, *args, **kwargs)

    def test_the_sent_values_are_kept(self):
        _req, body = self._send("Fog", [101, 7, 3, 9], 12, -13, instance_key=42,
                                round_time=1767225600 + 10)

        self.assertEqual(self.store.rows(), [(10, 2, 12, 101, 7, 3, -13, None, "own")])
        self.assertEqual((body["p_t1"], body["p_t2"], body["p_t3"]), (101, 7, 3))

    def test_a_quiet_send_is_kept_too(self):
        self._send("Fog", [101], 12, -13, quiet=True, round_time=1767225600 + 20)
        self.assertEqual(self.store.rows()[0][:4], (20, 2, 12, 101))

    def test_no_uid_keeps_nothing(self):
        self._send("Fog", [101], 12, None, round_time=1767225600 + 30)
        self.assertEqual(self.store.rows(), [])

    def test_a_store_failure_does_not_stop_the_send(self):
        with patch.object(RoundStore.RoundStore, "add_own", side_effect=None, return_value=False):
            req, _body = self._send("Fog", [101], 12, -13, round_time=1767225600 + 40)
        self.assertTrue(req.full_url.endswith("rpc/register_round"))




class TestFetchRounds(unittest.TestCase):
    """差分取り: time の昇順・件数でページ送り（offset なし）。自分の行は取らない"""

    def _pages(self, pages):
        responses = []
        for page in pages:
            res = MagicMock()
            res.__enter__ = MagicMock(return_value=res)
            res.__exit__ = MagicMock(return_value=False)
            res.read.return_value = json.dumps(page).encode()
            responses.append(res)
        return responses

    def _fetch(self, pages, since, mine, page_size=2):
        with patch.object(ConnectDB, "_configured", return_value=True), \
             patch.object(ConnectDB, "SUPABASE_URL", "https://example.invalid"), \
             patch.object(ConnectDB, "_headers", return_value={}), \
             patch.object(ConnectDB.urllib.request, "urlopen",
                          side_effect=self._pages(pages)) as urlopen:
            rows = ConnectDB.fetch_rounds(since, mine, page_size=page_size)
        return rows, [urllib.parse.unquote(c.args[0].full_url) for c in urlopen.call_args_list]

    def test_the_first_fetch_takes_everything_in_time_order(self):
        rows, urls = self._fetch([[{"time": 1}, {"time": 2}], [{"time": 3}]], None, set())

        self.assertEqual([r["time"] for r in rows], [1, 2, 3])
        self.assertIn("/rest/v1/ToNRounds?select=*&order=time.asc&limit=2", urls[0])
        self.assertNotIn("time=gte", urls[0])
        self.assertNotIn("or=(", urls[0], "初回は自分の行も取る")
        self.assertIn("time=gte.2", urls[1], "次のページは最後の time から")
        for url in urls:
            self.assertNotIn("offset", url)

    def test_later_fetches_skip_rows_that_are_only_mine(self):
        _rows, urls = self._fetch([[{"time": 50}]], 100, {-13, 40})

        self.assertIn("time=gte.100", urls[0])
        self.assertIn("or=(transformed_uid.not.in.(-13,40),other_uids.not.is.null)", urls[0])

    def test_a_full_page_of_one_second_moves_on(self):
        _rows, urls = self._fetch([[{"time": 7}, {"time": 7}], [{"time": 8}]], 7, set())
        self.assertIn("time=gte.8", urls[1], "同じ所を取り続けない")

    def test_not_configured_is_an_error(self):
        with patch.object(ConnectDB, "_configured", return_value=False):
            with self.assertRaises(RuntimeError):
                ConnectDB.fetch_rounds(None, set())




class TestRoundStoreSync(unittest.TestCase):
    """手元へ取り込む: 上書き・重複を消す・通信の失敗でも手元はそのまま"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = RoundStore.RoundStore(Path(self._dir.name) / "rounds.sqlite")
        self.calls = []
        self.answer = []

    def _fetch(self, since, mine):
        self.calls.append((since, set(mine)))
        return list(self.answer)

    @staticmethod
    def _row(t, uid, others=None, rnd=1, map_id=12, t1=5):
        return {"time": t, "round": rnd, "map_id": map_id, "terror1": t1, "terror2": None,
                "terror3": None, "transformed_uid": uid, "other_uids": others}

    def test_the_first_sync_is_full_then_only_the_difference(self):
        self.store.add_own(100, 1, 12, 5, None, None, -13)
        self.answer = [self._row(90, 7), self._row(2000, 8)]
        self.assertTrue(self.store.sync(self._fetch))
        self.assertTrue(self.store.sync(self._fetch))

        self.assertEqual(self.calls, [(None, set()), (2000 - 900, {-13})])
        self.assertEqual(self.store.get_meta("synced_to"), "2000")

    def test_a_new_uid_means_a_full_sync_again(self):
        self.store.add_own(100, 1, 12, 5, None, None, -13)
        self.answer = [self._row(90, 7)]
        self.store.sync(self._fetch)
        self.store.sync(self._fetch)
        self.assertEqual(self.calls[-1], (90 - 900, {-13}), "増えていなければ差分")
        self.store.add_own(200, 1, 12, 5, None, None, 40)       # 別のアカウント

        self.store.sync(self._fetch)

        self.assertEqual(self.calls[-1], (None, set()))

    def test_an_unfinished_first_sync_starts_from_everything(self):
        """synced_to が残っていても、初回の全件取得が済んでいなければ全件から"""
        self.store.add_own(100, 1, 12, 5, None, None, -13)
        self.store.set_meta("synced_to", "5000")
        self.store.sync(self._fetch)
        self.assertEqual(self.calls, [(None, set())])

    def test_a_refetched_row_is_overwritten_not_doubled(self):
        self.answer = [self._row(100, 7)]
        self.store.sync(self._fetch)
        self.answer = [self._row(100, 7, [8, 9])]
        self.store.sync(self._fetch)

        self.assertEqual(self.store.rows(), [(100, 1, 12, 5, None, None, 7, "8,9", "db")])

    def test_one_account_in_two_windows_keeps_both_rows(self):
        self.answer = [self._row(100, 7, rnd=1), self._row(100, 7, rnd=6),
                       self._row(100, 7, map_id=34)]
        self.store.sync(self._fetch)
        self.assertEqual(self.store.count(), 3)

    def test_an_own_row_seen_as_second_player_is_dropped(self):
        self.store.add_own(100, 1, 12, 5, None, None, -13)
        self.store.add_own(500, 1, 12, 5, None, None, -13)       # 別のラウンド（残る）
        self.answer = [self._row(97, 7, [-13])]                 # 3秒前に始まった同じラウンド

        self.store.sync(self._fetch)

        rows = self.store.rows()
        self.assertEqual([(r[0], r[6], r[8]) for r in rows],
                         [(97, 7, "db"), (500, -13, "own")])

    def test_a_near_row_that_differs_keeps_the_own_row(self):
        for differ in ({"rnd": 2}, {"map_id": 34}, {"t1": 9}):
            store = RoundStore.RoundStore(Path(self._dir.name) / f"s{len(differ)}{list(differ)[0]}.sqlite")
            store.add_own(100, 1, 12, 5, None, None, -13)
            self.answer = [self._row(100, 7, [-13], **differ)]
            store.sync(self._fetch)
            self.assertEqual(store.count(), 2, differ)

    def test_a_row_more_than_15_seconds_away_keeps_the_own_row(self):
        self.store.add_own(100, 1, 12, 5, None, None, -13)
        self.answer = [self._row(116, 7, [-13])]
        self.store.sync(self._fetch)
        self.assertEqual(self.store.count(), 2)

    def test_the_same_row_sent_first_by_me_becomes_the_db_row(self):
        self.store.add_own(100, 1, 12, 5, None, None, -13)
        self.answer = [self._row(100, -13)]
        self.store.sync(self._fetch)
        self.assertEqual(self.store.rows(), [(100, 1, 12, 5, None, None, -13, None, "db")])

    def test_a_failed_fetch_keeps_what_is_here(self):
        self.store.add_own(100, 1, 12, 5, None, None, -13)

        def broken(_since, _mine):
            raise OSError("offline")

        self.assertFalse(self.store.sync(broken))
        self.assertEqual(self.store.count(), 1)
        self.assertIsNone(self.store.get_meta("initial_done"), "次に開いたときも全件から")




class TestLogMonitorDbV1(unittest.TestCase):
    """開始の行の時刻とインスタンスの ID を覚えて送る。ソロなら目印は NULL"""

    PREFIX = "2026.09.30 13:00:05 Debug      -  "
    JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.transformed_uid = 99
        return monitor

    def test_the_instance_id_comes_from_the_join_line(self):
        monitor = self._monitor()
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.PREFIX + self.JOIN)
        self.assertEqual(monitor.st.instance_id, "wrld_now:2~private(usr_me)~region(jp)")

    def test_the_start_scan_also_knows_the_instance(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(self.PREFIX + "User Authenticated: a (usr_0e01408a)\n" + self.PREFIX + self.JOIN + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name)), {},
                                        lambda _m: None, window_idx=1)
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None):
            monitor._detect_instance_from_log()
        self.assertEqual(monitor.st.instance_id, "wrld_now:2~private(usr_me)~region(jp)")

    def test_solo_sends_no_instance_and_unknown_players_do(self):
        monitor = self._monitor()
        monitor.st.instance_id = "wrld_now:2"
        monitor.st.local_user_id = "usr_me"
        key = ConnectDB.instance_key("wrld_now:2")

        monitor.st.players_known = False
        self.assertEqual(monitor._db_instance_key(), key, "人数が分からないときは送る")
        monitor.st.players_known = True
        monitor.st.players = {"usr_me"}
        self.assertIsNone(monitor._db_instance_key(), "ソロ")
        monitor.st.players = {"usr_me", "usr_other"}
        self.assertEqual(monitor._db_instance_key(), key)

    def test_the_round_start_time_is_sent_not_the_send_time(self):
        monitor = self._monitor()
        monitor.st.instance_id = "wrld_now:2"
        monitor.st.players_known = False
        start = self.PREFIX + ("This round is taking place at Facility (12) "
                               "and the round type is Bloodbath")
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round") as send:
            monitor._process(start)
            monitor._process("2026.09.30 13:00:30 Debug      -  Killers have been set - "
                             "1 2 3 // Round type is Bloodbath")

        send.assert_called_once_with("Bloodbath", [1, 2, 3], 12, 99, quiet=False,
                                     instance_key=ConnectDB.instance_key("wrld_now:2"),
                                     round_time=LogParser.log_time(start))




class TestLogMonitorStatisticsRegistration(unittest.TestCase):
    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _msg: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.map_id = 12
        monitor.st.transformed_uid = 99
        return monitor

    def test_statistics_are_sent_when_killers_are_known(self):
        """Gigabytes の候補にならない構成（Bloodbath の3体）はすぐ送る"""
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        mock_send.assert_called_once_with("Bloodbath", [1, 2, 3], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)
        self.assertTrue(monitor.st.statistics_sent)

    def test_a_single_classic_waits_for_gigabytes(self):
        """元IDが不定なので、Classic の1体構成はどれも Gigabytes の候補"""
        monitor = self._monitor()

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)

        mock_send.assert_not_called()
        self.assertFalse(monitor.st.statistics_sent)

    def test_a_single_classic_is_sent_at_the_round_end(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)
            monitor._process("Verified Round End")

        mock_send.assert_called_once_with("Classic", [1], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_gigabytes_sends_the_replaced_id(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)
            monitor._process("The Gigabytes have come.")

        mock_send.assert_called_once_with("Classic", [config.GIGABYTES_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_statistics_are_sent_only_once_per_round(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)
            monitor._on_killers([4], "Bloodbath", revealed=True)

        mock_send.assert_called_once_with("Bloodbath", [1, 2, 3], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)
        self.assertEqual(monitor.st.terror_ids, [1, 2, 3, 4])

    def test_round_start_resets_statistics_sent_flag(self):
        monitor = self._monitor()
        monitor.st.statistics_sent = True
        monitor.st.bloodthirsty_creature_variant = True
        monitor.st.hungry_home_invader_variant = True

        monitor._process("This round is taking place at Facility (12) and the round type is Classic")

        self.assertFalse(monitor.st.statistics_sent)
        self.assertFalse(monitor.st.bloodthirsty_creature_variant)
        self.assertFalse(monitor.st.hungry_home_invader_variant)

    def test_bloodthirsty_log_before_killers_converts_curious_creature(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False
        monitor._process(TerrorReplacement.signal("bloodthirsty_creature_variant").sample)

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            # Classic の1体なので Gigabytes を待つ。送るのは終了時
            mock_send.assert_not_called()
            monitor._process("Verified Round End")

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_bloodthirsty_log_after_killers_updates_delayed_statistics(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            mock_send.assert_not_called()

            monitor._process(TerrorReplacement.signal("bloodthirsty_creature_variant").sample)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_bloodthirsty_variant_is_not_limited_to_classic(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Bloodbath", revealed=False)
            mock_send.assert_not_called()
            monitor._process(TerrorReplacement.signal("bloodthirsty_creature_variant").sample)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Bloodbath", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_hungry_home_invader_log_after_classic_slender_converts_id(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)
            mock_send.assert_not_called()
            monitor._process(TerrorReplacement.signal("hungry_home_invader_variant").sample)

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_send.assert_called_once_with("Classic", [config.HUNGRY_HOME_INVADER_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_hungry_home_invader_log_before_classic_slender_converts_id(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False
        monitor._process(TerrorReplacement.signal("hungry_home_invader_variant").sample)

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)
            mock_send.assert_not_called()       # Gigabytes 待ち
            monitor._process("Verified Round End")

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_send.assert_called_once_with("Classic", [config.HUNGRY_HOME_INVADER_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_hungry_home_invader_is_ignored_outside_classic(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Bloodbath", revealed=False)
            monitor._process(TerrorReplacement.signal("hungry_home_invader_variant").sample)

        self.assertEqual(monitor.st.terror_ids, [config.SLENDER_ID])
        self.assertFalse(monitor.st.hungry_home_invader_variant)
        mock_send.assert_called_once_with("Bloodbath", [config.SLENDER_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_curious_creature_statistics_send_on_round_end_if_not_bloodthirsty(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            mock_send.assert_not_called()

            monitor._process("Verified Round End")

        self.assertEqual(monitor.st.terror_ids, [config.CURIOUS_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.CURIOUS_CREATURE_ID], 12, 99, quiet=False, instance_key=ANY, round_time=ANY)

    def test_verified_end_does_not_send_statistics(self):
        """待つものが無い構成なら、終了時に改めて送らない"""
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"
        monitor.st.terror_ids = [1, 2, 3]

        with patch.object(ConnectDB, "register_round") as mock_send:
            monitor._process("Verified Round End")

        mock_send.assert_not_called()



# ═══════════════════════════════════════════════
#  Statistics.py
# ═══════════════════════════════════════════════
class TestStatistics(unittest.TestCase):
    def test_binomial_upper_matches_direct_sum(self):
        n = 12
        k = 4
        p = 0.2
        direct = sum(Statistics.binomial_pmf(n, i, p) for i in range(k, n + 1))
        self.assertAlmostEqual(Statistics.binomial_pmf_upper(n, k, p), direct, places=12)

    def test_binomial_upper_uses_lower_tail_for_common_side(self):
        n = 20
        k = 8
        p = 0.5
        direct = sum(Statistics.binomial_pmf(n, i, p) for i in range(k, n + 1))
        self.assertAlmostEqual(Statistics.binomial_pmf_upper(n, k, p), direct, places=12)

    def test_round_filtering_and_summary_strip_round_names(self):
        rows = [
            {"date": 20260526, "time": 100000, "round": " Fog ", "terror_ids": [1, 2], "map_id": 12},
            {"date": 20260526, "time": 110000, "round": "Unbound", "terror_ids": [201], "map_id": 49},
            {"date": 20260527, "time": 100000, "round": "Fog", "terror_ids": [3], "map_id": 13},
        ]
        filtered = Statistics.filter_rows(
            rows,
            datetime(2026, 5, 26, 0, 0, 0),
            datetime(2026, 5, 26, 23, 59, 59),
            {"Fog"},
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(Statistics.available_rounds(rows), ["Fog", "Unbound"])
        self.assertEqual(Statistics.round_summary(filtered), [("Fog", 1, 2)])

    def test_map_name_for_id_uses_round_to_resolve_duplicate_ids(self):
        self.assertEqual(Statistics.map_name_for_id(1, "Run"), "Dring King's Citadel")
        self.assertEqual(Statistics.map_name_for_id(1, "Classic"), "Sewers")
        self.assertEqual(Statistics.map_name_for_id(999, "Classic"), "Map 999")
        self.assertEqual(Statistics.map_name_for_id(1, "8 Pages"), "Sewers",
                         "8 Pages の印が無くても Run のマップにはしない")
        self.assertEqual(Statistics.map_name_for_id(1), "Sewers")

    def test_maps_json_has_the_three_flags(self):
        """rounds の一覧はやめて、normal / 8pages / run の印（1 か 0）だけ持つ"""
        entries = Statistics.map_entries()
        self.assertTrue(entries)
        for entry in entries:
            self.assertNotIn("rounds", entry)
            self.assertTrue(all(entry.get(k) in (0, 1) for k in ("normal", "8pages", "run")), entry)
        runs = [e["name"] for e in entries if e["run"] == 1]
        self.assertEqual(runs, ["Dring King's Citadel"])
        eight = {e["name"] for e in entries if e["8pages"] == 1}
        self.assertEqual(eight, {
            "Warehouse", "Hub", "Schoolhouse", "Pools", "Backrooms", "Secret", "Innyume", "Harvest",
            "Pizzeria", "Experimentation", "Forest", "SlashCo HQ", "Dust", "The Wall", "Park",
            "Tunnels", "The Fishbowl", "Hotel", "Space Colony"}, "8 Pages で選ばれる 19 マップ")

    def test_map_counts_for_terror_uses_map_names(self):
        rows = [
            {"round": "Run", "terror_ids": [1], "map_id": 1},
            {"round": "Classic", "terror_ids": [1, 1], "map_id": 1},
            {"round": "Classic", "terror_ids": [2], "map_id": 12},
        ]
        self.assertEqual(
            Statistics.map_counts_for_terror(rows, 1),
            [("Sewers", 2), ("Dring King's Citadel", 1)],
        )




class TestGuiRoundHelpers(unittest.TestCase):
    def test_ordered_round_entries_include_deferred_rounds_and_aliases(self):
        entries = StatisticsGUI._ordered_round_entries(["Unbound", "Fog", "Fog (Alternate)", "Ghost Alternate", "Mystic Moon"])

        self.assertIn(("Classic", "Classic"), entries)
        self.assertIn(("Run", "Run"), entries)
        self.assertLess(entries.index(("Fog", "Fog")), entries.index(("Fog(Alternate)", "Fog (Alternate)")))
        self.assertIn(("Ghost(Alternate)", "Ghost Alternate"), entries)

    def test_round_chart_colors_are_not_collapsed_to_one_color(self):
        self.assertGreater(len(set(StatisticsGUI.ROUND_CHART_COLORS)), 3)

    def test_round_count_sort_key_orders_by_count_desc_then_round_order(self):
        rows = [
            ("Unbound", 2, 2),
            ("Fog", 5, 5),
            ("Classic", 5, 5),
            ("Bloodbath", 1, 1),
        ]

        self.assertEqual(
            sorted(rows, key=StatisticsGUI._round_count_sort_key),
            [
                ("Fog", 5, 5),              # Classic は下の段の組へ移った（同じ回数なら後ろ）
                ("Classic", 5, 5),
                ("Unbound", 2, 2),
                ("Bloodbath", 1, 1),
            ],
        )

    def test_round_chart_draws_positive_extents_with_distinct_colors(self):
        class FakeCanvas:
            def __init__(self):
                self.polygons = []

            def delete(self, _target):
                pass

            def winfo_width(self):
                return 300

            def winfo_height(self):
                return 300

            def create_polygon(self, *args, **kwargs):
                self.polygons.append((args, kwargs))

            def create_oval(self, *args, **kwargs):
                pass

            def create_text(self, *args, **kwargs):
                pass

        window = type("FakeStatisticsWindow", (), {})()
        window.round_chart = FakeCanvas()
        window._round_chart_job = "job"
        window._round_chart_rows = [
            ("Bloodbath", 6, 6),
            ("Alternate", 4, 4),
            ("Randomizer", 2, 2),
        ]
        window._draw_round_slice = StatisticsGUI.StatisticsWindow._draw_round_slice.__get__(window)

        StatisticsGUI.StatisticsWindow._draw_round_chart(window)

        colors = [kwargs["fill"] for _args, kwargs in window.round_chart.polygons]
        # ラウンドごとに決めた色（回数の順ではない）
        self.assertEqual(colors, [StatisticsGUI.ROUND_COLORS["Bloodbath"], StatisticsGUI.ROUND_COLORS["Alternate"],
                                  StatisticsGUI.ROUND_COLORS["Randomizer"]])
        self.assertEqual(len(set(colors)), 3)
        self.assertTrue(all(len(args) >= 4 for args, _kwargs in window.round_chart.polygons))

    def test_the_terror_stats_are_cached_per_filter(self):
        """同じ条件なら手元の集計を撮り直さない。条件が変わったら捨てる"""
        window = type("FakeStatisticsWindow", (), {})()
        window._filter = None
        window._terror_stats_cache = {"unbound": (1, 1, [])}
        window._map_counts_cache = {1: [("Sewers", 1)]}
        flt = RoundStore.Filter(start=0, end=10, rounds=frozenset({10}))
        if flt != window._filter:
            window._filter = flt
            window._terror_stats_cache.clear()
            window._map_counts_cache.clear()
        self.assertEqual(window._terror_stats_cache, {})
        self.assertEqual(window._map_counts_cache, {})
        self.assertEqual(flt, RoundStore.Filter(start=0, end=10, rounds=frozenset({10})),
                         "条件は値で比べる")

    def test_terror_map_counts_are_cached_per_filter(self):
        class FakeTree:
            def __init__(self):
                self.inserted = []

            def selection(self):
                return ("1",)

            def insert(self, *args, **kwargs):
                self.inserted.append((args, kwargs))

        window = type("FakeStatisticsWindow", (), {})()
        window.terror_tree = FakeTree()
        window.map_tree = FakeTree()
        window._filter = None
        window._map_counts_cache = {}
        window._clear_tree = MagicMock()
        window._render_terror_rounds = MagicMock()      # 内訳のほかのタブ（別のテストで見る）
        window._terror_categories = {}
        window._show_round_type_tab = MagicMock()       # Unbound ではラウンドタイプを隠す（別のテストで見る）
        window.store = MagicMock()
        window.store.map_counts_for_terror.return_value = [(12, 1, 1)]

        StatisticsGUI.StatisticsWindow._on_terror_selected(window)
        StatisticsGUI.StatisticsWindow._on_terror_selected(window)

        window.store.map_counts_for_terror.assert_called_once_with(None, 1)
        self.assertEqual(window._clear_tree.call_count, 2)
        self.assertEqual(len(window.map_tree.inserted), 2)

    def test_load_rows_async_ignores_duplicate_request_while_loading(self):
        class FakeStatus:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        window = type("FakeStatisticsWindow", (), {})()
        window._rows_loading = True
        window.v_status = FakeStatus()

        with patch.object(StatisticsGUI.threading, "Thread") as mock_thread:
            StatisticsGUI.StatisticsWindow._load_rows_async(window)

        mock_thread.assert_not_called()
        self.assertIsNone(window.v_status.value)




# ═══════════════════════════════════════════════
#  ConnectDB.py
# ═══════════════════════════════════════════════
class TestRoundStoreAggregation(unittest.TestCase):
    """集計は手元の SQLite で。数え方は今の Statistics と同じ"""

    MINE, OTHER = -13, 7

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = RoundStore.RoundStore(Path(self._dir.name) / "rounds.sqlite")
        self.store.add_own(10, 1, 1, 1, None, None, self.MINE)             # 自分（1人目）
        self.rows = [
            {"time": 100, "round": 10, "map_id": 12, "terror1": 201, "terror2": None,
             "terror3": None, "transformed_uid": self.OTHER, "other_uids": [self.MINE, 9]},
            {"time": 200, "round": 6, "map_id": 1, "terror1": 5, "terror2": 6,
             "terror3": 7, "transformed_uid": self.OTHER, "other_uids": None},
            {"time": 300, "round": 6, "map_id": 12, "terror1": 6, "terror2": 5,
             "terror3": None, "transformed_uid": 8, "other_uids": None},
            {"time": 90000, "round": 2, "map_id": 12, "terror1": 5, "terror2": None,
             "terror3": None, "transformed_uid": 8, "other_uids": [9]},
        ]
        self.store.sync(lambda _since, _mine: list(self.rows))

    def _dicts(self):
        """今の画面が使っていた形（ConnectDB.round_row）"""
        return [ConnectDB.round_row({"time": t, "round": r, "map_id": m, "terror1": a,
                                     "terror2": b, "terror3": c, "transformed_uid": u,
                                     "other_uids": uids_list(o)})
                for t, r, m, a, b, c, u, o, _origin in self.store.rows()]

    def test_the_round_summary_matches_the_old_counting(self):
        new = Statistics.round_summary_from_counts(
            (StatisticsGUI._round_name(rid), n, s) for rid, n, s in self.store.round_summary(RoundStore.Filter()))
        self.assertEqual(new, Statistics.round_summary(self._dicts()))

    def test_the_terror_statistics_match_the_old_counting(self):
        for category in ("classic", "alternate", "unbound"):
            ids = Statistics.candidate_ids_for_category(category, config.TERRORS)
            new = Statistics.analyze_terror_counts(self.store.terror_counts(RoundStore.Filter()),
                                                   config.TERRORS, ids)
            self.assertEqual(new, Statistics.analyze_terrors(self._dicts(), config.TERRORS, ids), category)

    def test_the_map_counts_match_the_old_counting(self):
        for tid in (5, 6, 201):
            new = Statistics.map_counts_from_entries(
                (m, StatisticsGUI._round_name(r), h)
                for m, r, h in self.store.map_counts_for_terror(RoundStore.Filter(), tid))
            self.assertEqual(new, Statistics.map_counts_for_terror(self._dicts(), tid), tid)

    def test_mine_is_first_player_or_in_other_uids(self):
        mine = RoundStore.Filter(mine=True)
        self.assertEqual(sorted(r for r, _n, _s in self.store.round_summary(mine)), [1, 10])
        self.assertEqual(sum(n for _r, n, _s in self.store.round_summary(RoundStore.Filter())), 5)

    def test_mine_without_any_uid_is_empty(self):
        store = RoundStore.RoundStore(Path(self._dir.name) / "empty.sqlite")
        store.sync(lambda _since, _mine: list(self.rows))
        self.assertEqual(store.round_summary(RoundStore.Filter(mine=True)), [])

    def test_period_and_rounds_filter(self):
        flt = RoundStore.Filter(start=150, end=350, rounds=frozenset({6}))
        self.assertEqual(self.store.round_summary(flt), [(6, 2, 5)])
        self.assertEqual(self.store.round_summary(RoundStore.Filter(rounds=frozenset())), [])

    def test_the_start_of_the_period_excludes_earlier_rounds(self):
        self.assertEqual(sum(n for _r, n, _s in self.store.round_summary(RoundStore.Filter(start=250))), 2)
        self.assertEqual(sum(n for _r, n, _s in self.store.round_summary(RoundStore.Filter(end=250))), 3)

    def test_only_candidate_terrors_make_the_slots(self):
        """二項検定の枠数は、そのカテゴリの候補のテラーだけ（数え方の本体を直接確かめる）"""
        ids = Statistics.candidate_ids_for_category("classic", config.TERRORS)
        counts = self.store.terror_counts(RoundStore.Filter())
        expected = sum(n for tid, n in counts.items() if tid in ids)
        total, _candidates, _rows = Statistics.analyze_terror_counts(counts, config.TERRORS, ids)
        self.assertEqual(total, expected)
        self.assertLess(total, sum(counts.values()), "候補外（Unbound の 201）は入らない")

    def test_the_same_round_name_or_map_is_added_up(self):
        self.assertEqual(Statistics.round_summary_from_counts([("Fog", 2, 2), ("Fog", 3, 1)]),
                         [("Fog", 5, 3)])
        name = Statistics.map_name_for_id(12, "Classic")
        self.assertEqual(Statistics.map_counts_from_entries([(12, "Classic", 1), (12, "Classic", 2)]),
                         [(name, 3)])

    def test_the_time_series_in_local_time(self):
        day = self.store.time_series(RoundStore.Filter(), "day", 5)
        first_day = datetime.fromtimestamp(config.DB_TIME_EPOCH + 10).strftime("%Y-%m-%d")
        self.assertEqual(day[0], (first_day, 4, 2), "その日のラウンド数と、テラー5の枠数")
        hours = dict((k, n) for k, n, _h in self.store.time_series(RoundStore.Filter(), "hour"))
        self.assertEqual(sum(hours.values()), 5)
        self.assertEqual(hours[datetime.fromtimestamp(config.DB_TIME_EPOCH + 90000).hour], 1)

    def test_the_time_range(self):
        self.assertEqual(self.store.time_range(), (10, 90000))




class TestStatisticsWindowV1(unittest.TestCase):
    """統計画面を本物の Tk で開く（通信は差し替え。手元は一時フォルダ）"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = RoundStore.RoundStore(Path(self._dir.name) / "rounds.sqlite")
        self.store.add_own(100, 6, 12, 5, 6, None, -13)
        self.store.add_own(200, 10, 12, 201, None, None, -13)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def _open(self, fetch):
        # 取り込みのスレッドはその場で回す（mainloop が無いと、別スレッドからの after を Tk が断る）
        with patch.object(ConnectDB, "fetch_rounds", side_effect=fetch),              patch.object(StatisticsGUI.threading, "Thread", TestDbV1.RunNow):
            window = StatisticsGUI.StatisticsWindow(self.root, store=self.store)
            for _ in range(400):
                self.root.update()
                if not window._rows_loading:
                    break
                time.sleep(0.005)
        self.root.update()
        return window

    def test_it_has_the_three_tabs(self):
        """「組み合わせ」・「マルチ」のタブは無くした"""
        window = self._open(lambda s, m: [])
        self.assertEqual([window.tabs.tab(t, "text") for t in window.tabs.tabs()],
                         ["ラウンド", "テラー", "時間の流れ"])
        self.assertFalse(hasattr(window, "multi_tree"))
        self.assertFalse(hasattr(RoundStore.RoundStore, "player_counts"))
        self.assertFalse(hasattr(window, "pair_tree"))
        self.assertFalse(hasattr(RoundStore.RoundStore, "terror_pairs"))
        self.assertFalse(hasattr(RoundStore.RoundStore, "map_terror_counts"))

    def test_a_failed_update_still_shows_what_is_here(self):
        def offline(_since, _mine):
            raise OSError("offline")

        window = self._open(offline)

        self.assertEqual(window.v_status.get(), "更新できませんでした（手元の分を表示）")
        self.assertEqual(len(window.round_legend.get_children()), 2, "手元の2ラウンド")
        self.assertIn("手元 2件", window.v_info.get())

    def _update(self, window):
        with patch.object(ConnectDB, "fetch_rounds", return_value=[]), \
             patch.object(StatisticsGUI.threading, "Thread", TestDbV1.RunNow):
            window._load_rows_async()
            for _ in range(400):
                self.root.update()
                if not window._rows_loading:
                    break
                time.sleep(0.005)
        self.root.update()

    def test_rounds_played_after_opening_show_up_on_update(self):
        """開いた後の Unbound（Garden Rejects）が「更新」で出る。前は開き直すまで出なかった:
        期間の終わりが開いたときのまま・テラーの集計が絞り込みが同じなら使い回し"""
        window = self._open(lambda s, m: [])
        self.assertEqual(window._terror_stats("Unbound")[0], 1, "201 の1枠")
        later = 200 + 3 * 3600
        self.store.add_own(later, 10, 12, 209, None, None, -13)

        self._update(window)

        self.assertEqual(window._picker_datetime("end"),
                         StatisticsGUI._local(later).replace(minute=59, second=59))
        self.assertEqual(window._terror_stats("Unbound")[0], 2, "209 も数える")
        self.assertTrue(window.v_status.get().startswith("3ラウンド"), window.v_status.get())

    def test_an_end_the_user_chose_is_kept(self):
        window = self._open(lambda s, m: [])
        chosen = window._picker_datetime("end")
        window._set_picker_datetime("end", chosen.replace(hour=(chosen.hour + 1) % 24))
        chosen = window._picker_datetime("end")
        self.store.add_own(200 + 30 * 3600, 10, 12, 209, None, None, -13)

        self._update(window)

        self.assertEqual(window._picker_datetime("end"), chosen)




class TestGetTransformedUid(unittest.TestCase):
    @staticmethod
    def _res(body, status=200):
        res = MagicMock()
        res.__enter__ = MagicMock(return_value=res)
        res.__exit__ = MagicMock(return_value=False)
        res.read.return_value = json.dumps(body).encode()
        res.status = status
        return res

    def _configured(self):
        for name, value in (("SUPABASE_URL", "https://db.example"), ("SUPABASE_KEY", "key")):
            p = patch.object(ConnectDB, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_send_Users_asks_the_db_function(self):
        """Users は直接読まない。関数 get_transformed_uid に uid を渡して番号をもらう"""
        self._configured()
        with patch('urllib.request.urlopen', return_value=self._res(123)) as mock_urlopen:
            result = ConnectDB.send_Users("usr_new")

        self.assertEqual(result, 123)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/rest/v1/rpc/get_transformed_uid"), req.full_url)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"p_vrchat_uid": "usr_new"})

    def test_send_Users_none_from_the_db_is_none(self):
        """割り当てられない（ほぼ埋まっている）"""
        self._configured()
        with patch('urllib.request.urlopen', return_value=self._res(None)):
            self.assertIsNone(ConnectDB.send_Users("usr_new"))

    def test_a_db_without_the_function_falls_back_to_the_old_way(self):
        """関数がまだ DB に無い（404）間だけ前のやり方"""
        self._configured()
        missing = urllib.error.HTTPError("u", 404, "not found", {}, None)
        with patch('urllib.request.urlopen', side_effect=missing), \
             patch.object(ConnectDB, "_send_Users_direct", return_value=7) as old:
            self.assertEqual(ConnectDB.send_Users("usr_new"), 7)
        old.assert_called_once_with("usr_new")

    def test_other_http_errors_do_not_fall_back(self):
        self._configured()
        denied = urllib.error.HTTPError("u", 401, "denied", {}, None)
        with patch('urllib.request.urlopen', side_effect=denied), \
             patch.object(ConnectDB, "_send_Users_direct") as old:
            with self.assertRaises(urllib.error.HTTPError):
                ConnectDB.send_Users("usr_new")
        old.assert_not_called()

    def test_the_old_way_registers_a_new_user(self):
        """前のやり方: 存在確認 → 既存の番号の一覧 → POST 登録"""
        responses = [self._res([]), self._res([{"transformed_uid": 123}]), self._res(None, 201)]
        with patch('urllib.request.urlopen', side_effect=responses):
            result = ConnectDB._send_Users_direct("usr_new")
        self.assertNotEqual(result, 123)
        self.assertIsNotNone(result)

    def test_the_old_way_returns_an_existing_user(self):
        res = self._res([{"VRChat_uid": "usr-existing", "transformed_uid": 123}])
        with patch('urllib.request.urlopen', return_value=res):
            self.assertEqual(ConnectDB._send_Users_direct("usr-existing"), 123)

    def test_the_old_way_when_full(self):
        """ユーザー登録限界"""
        existing = [{"VRChat_uid": "usr-existing", "transformed_uid": i} for i in range(-32768, 32767)]
        with patch('urllib.request.urlopen', side_effect=[self._res([]), self._res(existing)]):
            self.assertIsNone(ConnectDB._send_Users_direct("usr_new"))

    def test_get_transformed_uid_is_send_users(self):
        self._configured()
        with patch.object(ConnectDB, 'send_Users', return_value=123) as mock_send:
            self.assertEqual(ConnectDB.get_transformed_uid("usr_new"), 123)
        mock_send.assert_called_once_with("usr_new")

    def test_error_returns_none(self):
        """エラー時はNoneを返す"""
        self._configured()
        with patch('urllib.request.urlopen', side_effect=Exception("network error")):
            result = ConnectDB.get_transformed_uid("usr_abc123")
            self.assertIsNone(result)

    def test_round_row_is_one_row_even_with_other_uids(self):
        """"ToNRounds" の1行を今の形に直す。other_uids（同じラウンドを見たほかの人）があっても1件"""
        v1 = {"time": 12696896, "round": 10, "map_id": 2, "terror1": 201, "terror2": None,
              "terror3": None, "transformed_uid": 123, "other_uids": [5, 6]}
        self.assertEqual(ConnectDB.round_row(v1),
                         {"created_at": "2026-05-27T22:54:56Z", "round": "Unbound",
                          "terror_ids": [201], "map_id": 2, "transformed_uid": 123})
