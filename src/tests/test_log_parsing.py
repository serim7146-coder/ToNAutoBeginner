"""ログの行の解析と terrors.json"""
from tests.support import *  # noqa: F401,F403




class TestTerrorIdByName(unittest.TestCase):
    """名前 → ID の逆引き。完全一致・一意のときだけ"""

    def _data(self, classic):
        return {"classic": classic, "alternate": {}, "unbound": {}}

    def test_an_exact_name_resolves(self):
        self.assertEqual(
            ReadJson.terror_id_by_name("The Pursuer", config.TERRORS), 99)

    def test_the_fixed_spelling_resolves(self):
        """依頼者が terrors.json を直した箇所。SOS → S.O.S"""
        self.assertEqual(ReadJson.terror_id_by_name("S.O.S", config.TERRORS), 97)

    def test_a_partial_name_does_not_resolve(self):
        """Mona が Mona & Mona & Mona & Mona に当たらないこと"""
        self.assertIsNone(ReadJson.terror_id_by_name("Mona", config.TERRORS))
        self.assertEqual(
            ReadJson.terror_id_by_name("Mona & Mona & Mona & Mona",
                                       config.TERRORS), 233)

    def test_an_individual_name_does_not_resolve(self):
        """terrors.json だけを見た場合。別名表は渡していない"""
        for name in ("Furnace", "BooBooBaby", "Deal", "Stringman",
                     "GlaggleLand Disruptor", "[REDACTED]"):
            self.assertIsNone(ReadJson.terror_id_by_name(name, config.TERRORS),
                              name)

    def test_a_duplicate_name_resolves_to_nothing(self):
        data = self._data({"1": "Twin", "2": "Twin", "3": "Solo"})

        self.assertIsNone(ReadJson.terror_id_by_name("Twin", data))
        self.assertEqual(ReadJson.terror_id_by_name("Solo", data), 3)

    def test_junk_resolves_to_nothing(self):
        for name in ("", "   ", None, 123):
            self.assertIsNone(ReadJson.terror_id_by_name(name, config.TERRORS),
                              repr(name))

    def test_the_case_is_not_absorbed(self):
        self.assertIsNone(ReadJson.terror_id_by_name("the pursuer",
                                                     config.TERRORS))

    def test_the_index_follows_a_new_table(self):
        first = self._data({"1": "A"})
        second = self._data({"5": "B"})

        self.assertEqual(ReadJson.terror_id_by_name("A", first), 1)
        self.assertEqual(ReadJson.terror_id_by_name("B", second), 5)
        self.assertIsNone(ReadJson.terror_id_by_name("A", second))




class TestTerrorsJsonFormat(unittest.TestCase):
    """terrors.json の新しい形（カテゴリごとの配列）と旧い形（辞書）"""

    NEW = {
        "classic": [
            {"id": 0, "name": "Huggy", "terrors": ["Huggy"]},
            {"id": 1, "name": "Corrupted Toys", "terrors": ["Corrupted Woody"]},
            {"id": 22, "name": "Starved", "terrors": ["Starved", "Furnace"]},
            {"id": 24, "name": "The Guidance", "terrors": ["The Guidance"]},
            {"id": 26, "name": "Nextbots", "terrors": ["Bear5", "Obunga"]},
            {"id": 49, "name": "Mona", "terrors": ["Mona"]},
            {"id": 191, "name": "Atrached", "terrors": ["Atrached"]},
        ],
        "alternate": [
            {"id": 149, "name": "Feddys", "terrors": ["Bear5", "Freddy"]},
            {"id": 151, "name": "The Observation",
             "terrors": ["The Observation", "The Guidance"]},
            {"id": 167, "name": "Walpurgisnacht", "terrors": ["Walpurgisnacht"]},
        ],
        "unbound": [
            {"id": 200, "name": "Guidance & The Booboo's",
             "terrors": ["The Guidance", "BooBooBabies"]},
            {"id": 281, "name": "Atrached Pack", "terrors": ["Atrached"]},
        ],
        "8pages": [
            {"id": "49 23 0", "name": "SM64.Z64", "terrors": ["SM64.Z64"]},
        ],
    }

    def _data(self):
        return ReadJson.normalize_terrors(
            {k: [dict(e) for e in v] for k, v in self.NEW.items()})

    def _fog(self, name):
        return ReadJson.fog_terror_id_by_name(name, self._data())

    # ── 読める ─────────────────────────────
    def test_the_real_file_loads(self):
        """中身は固定しない——依頼者が随時書き換えるため"""
        for category in ReadJson.MAIN_CATEGORIES:
            self.assertIsInstance(config.TERRORS.get(category), dict, category)
            for id_, name in config.TERRORS[category].items():
                self.assertTrue(id_.isdigit(), (category, id_))
                self.assertIsInstance(name, str, (category, id_))

    def test_ids_and_names_resolve_in_the_new_shape(self):
        data = self._data()

        self.assertEqual(ReadJson.terror_name(1, data), "Corrupted Toys")
        self.assertEqual(ReadJson.terror_name(167, data), "Walpurgisnacht")
        self.assertEqual(ReadJson.terror_id("Starved", data), 22)
        self.assertIsNone(ReadJson.terror_name(9999, data))

    def test_the_old_shape_still_works(self):
        old = {"classic": {"1": "Corrupted Toys"}, "alternate": {"167": "Walpurgisnacht"},
               "unbound": {}}

        data = ReadJson.normalize_terrors(old)

        self.assertEqual(ReadJson.terror_name(1, data), "Corrupted Toys")
        self.assertEqual(ReadJson.terror_id("Walpurgisnacht", data), 167)
        self.assertEqual(ReadJson.fog_terror_id_by_name("Walpurgisnacht", data), 167)

    def test_statistics_see_the_same_shape(self):
        """統計画面は {カテゴリ: {"ID": 名前}} を前提にしている"""
        data = self._data()

        self.assertEqual(Statistics.candidate_ids_for_category("Alternate", data),
                         {149, 151, 167})
        self.assertEqual(Statistics.terror_name(24, data), "The Guidance")

    def test_string_ids_stay_out_of_the_id_lookup(self):
        """8pages の "49 23 0" が classic 49 の引きを壊さない"""
        data = self._data()

        self.assertEqual(ReadJson.terror_name(49, data), "Mona")
        self.assertEqual(data["8pages"][0]["id"], "49 23 0", "読めるようにだけしておく")

    def test_broken_entries_are_skipped(self):
        data = ReadJson.normalize_terrors({"classic": [
            {"id": 1, "name": "Ok"}, "junk", {"id": True, "name": "Bool"},
            {"id": "x", "name": "NotANumber"}, {"id": "7", "name": "Digits"},
            {"id": 8}, {"id": 9, "name": "NoIndividuals", "terrors": None},
        ]})

        self.assertEqual(data["classic"], {"1": "Ok", "7": "Digits",
                                           "9": "NoIndividuals"})

    # ── 霧の個体名 ───────────────────────────
    def test_an_individual_name_resolves(self):
        self.assertEqual(self._fog("Corrupted Woody"), 1)
        self.assertEqual(self._fog("Furnace"), 22)

    def test_a_terror_name_still_resolves(self):
        self.assertEqual(self._fog("Walpurgisnacht"), 167)

    def test_unbound_is_not_consulted(self):
        """unbound まで引くと Atrached が 191/281 で決まらなくなる"""
        self.assertEqual(self._fog("Atrached"), 191)
        self.assertIsNone(self._fog("BooBooBabies"))

    def test_the_terror_name_wins_a_tie(self):
        """The Observation の The Guidance は65秒後。判明前なら classic 24"""
        self.assertEqual(self._fog("The Guidance"), 24)

    def test_two_individuals_do_not_resolve(self):
        """Bear5 はどちらも個体名。決めずに revealed を待つ"""
        self.assertIsNone(self._fog("Bear5"))

    def test_no_partial_match(self):
        self.assertIsNone(self._fog("Bear"))
        self.assertIsNone(self._fog("corrupted woody"))

    def test_the_alias_file_is_no_longer_read(self):
        """terror_aliases.json は廃止した。依頼者が後で消す"""
        self.assertFalse(hasattr(config, "TERROR_ALIASES"))
        self.assertFalse(hasattr(config, "TERROR_ALIASES_PATH"))
        self.assertFalse(hasattr(ReadJson, "load_terror_aliases"))

    def test_the_build_no_longer_bundles_it(self):
        build = Path("main.py").read_text(encoding="utf-8")

        self.assertNotIn("terror_aliases.json", build)
        self.assertIn("--include-data-files=terrors.json=terrors.json", build)

    def test_junk_resolves_to_nothing(self):
        for name in ("", None, 123):
            self.assertIsNone(self._fog(name), repr(name))

    def test_the_enrage_uses_the_individual_names(self):
        cfg = WindowConfig(do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = "Fog"

        with patch.object(config, "TERRORS", self._data()), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._on_enrage("Corrupted Woody")

        self.assertEqual(monitor.st.terror_ids, [1])



# ═══════════════════════════════════════════════
#  LogParser.py
# ═══════════════════════════════════════════════
class TestLogParser(unittest.TestCase):
    def test_round_start_extracts_round_and_map_id(self):
        event = LogParser.parse("This round is taking place at Facility (12) and the round type is Fog")
        self.assertEqual(event.kind, LogParser.EVENT_ROUND_START)
        self.assertEqual(event.round_type, "Fog")
        self.assertEqual(event.map_id, 12)
        self.assertEqual(event.raw_map, "Facility (12)")

    def test_round_start_without_map_id_uses_zero(self):
        event = LogParser.parse("This round is taking place at Unknown Map and the round type is Run")
        self.assertEqual(event.kind, LogParser.EVENT_ROUND_START)
        self.assertEqual(event.map_id, 0)

    def test_killers_set_parses_terror_ids(self):
        event = LogParser.parse("Killers have been set - 1 2 3 // Round type is Double Trouble")
        self.assertEqual(event.kind, LogParser.EVENT_KILLERS_SET)
        self.assertEqual(event.round_type, "Double Trouble")
        self.assertEqual(event.terror_ids, [1, 2])

    def test_user_auth_and_joining_parse_after_prefix(self):
        prefix = "2026.05.24 10:00:00 Log - "
        user = LogParser.parse(prefix + "User Authenticated: tester (usr_12345678-1234-1234-1234-123456789abc)")
        joining = LogParser.parse(prefix + "[Behaviour] Joining wrld_abc:12345~friends~region(us)")
        self.assertEqual(user.kind, LogParser.EVENT_USER_AUTH)
        self.assertEqual(user.user_id, "usr_12345678-1234-1234-1234-123456789abc")
        self.assertEqual(user.player_name, "tester")
        self.assertEqual(joining.kind, LogParser.EVENT_JOINING)
        self.assertIn("~friends", joining.suffix)

    def test_sus_player_parses_name_and_second_slot(self):
        first = LogParser.parse("Sus player = 5 serim01")
        second = LogParser.parse("Sus player 2 = 13 urichata")

        self.assertEqual(first.kind, LogParser.EVENT_SUS_PLAYER)
        self.assertEqual(first.player_name, "serim01")

        self.assertEqual(second.kind, LogParser.EVENT_SUS_PLAYER)
        self.assertEqual(second.player_name, "urichata")

    def test_bloodthirsty_creature_log_parses(self):
        event = LogParser.parse(TerrorReplacement.signal("bloodthirsty_creature_variant").sample)

        self.assertEqual((event.kind, event.flag),
                         (LogParser.EVENT_REPLACEMENT, "bloodthirsty_creature_variant"))

    def test_hungry_home_invader_log_parses(self):
        event = LogParser.parse(TerrorReplacement.signal("hungry_home_invader_variant").sample)

        self.assertEqual((event.kind, event.flag),
                         (LogParser.EVENT_REPLACEMENT, "hungry_home_invader_variant"))

    def test_item_equip_parses_previous_item_id(self):
        event = LogParser.parse("Equipping 94. Was using 41")

        self.assertEqual(event.kind, LogParser.EVENT_ITEM_EQUIP)
        self.assertEqual(event.item_id, 94)
        self.assertEqual(event.previous_item_id, 41)

    def test_respawn_logs_parse(self):
        generic = LogParser.parse("Player respawned, opted out!")

        self.assertEqual(generic.kind, LogParser.EVENT_RESPAWN)
        self.assertEqual(generic.player_name, "")
        self.assertIsNone(LogParser.parse("[DEATH][serim01] serim01 was forcefully respawned."))




class TestStartScanPrefilter(unittest.TestCase):
    """ログを末尾から遡る2つの処理は、印の文字列を含む行だけを parse() にかける。

    20万行のログを全部 parse() にかけると1本1秒以上かかり、起動（GUIスレッド）と
    マクロ開始が固まっていた。読む方向・止まる条件・結果は変えない
    """

    ME = "usr_0e01408a"
    PREFIX = "2026.09.18 16:56:57 Debug      -  "
    LINES = [
        "[Behaviour] Joining wrld_old:1~private(usr_me)~region(jp)",
        f"User Authenticated: serim01 ({ME})",
        "[Behaviour] OnPlayerJoined ghost (usr_9057)",
        "[Behaviour] Joining or Creating Room: Terrors of Nowhere",
        "[Behaviour] Joining wrld_now:2~group(grp_x)~groupAccessType(plus)~region(jp)",
        "Killers have been set - 1 3 0 // Round type is Classic",
        "[Behaviour] OnPlayerJoined a (usr_a)",
        "[PlayerLog] OnPlayerJoined: a (VR=False)",
        "[Behaviour] OnPlayerJoinComplete a",
        "[Behaviour] OnPlayerJoined b (usr_b)",
        "Verified Round End",
        f"[Behaviour] OnPlayerJoined serim01 ({ME})",
        "[Behaviour] OnPlayerLeft b (usr_b)",
        "[Behaviour] OnPlayerLeftRoom",
        "Equipping 3.",
        "The Gigabytes have come.",
    ]

    def _log_file(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        tmp.write("\n".join(self.PREFIX + line for line in lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def _monitor(self, path):
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _start_scan(self, path, marks=None):
        monitor = self._monitor(path)
        with patch.object(ConnectDB, "send_Users", return_value=7), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: f(*a)):
            if marks is None:
                monitor._detect_instance_from_log()
            else:
                with patch.object(LogMonitor.LogMonitor, "_START_SCAN_MARKS", marks):
                    monitor._detect_instance_from_log()
        st = monitor.st
        return (st.local_user_id, st.local_player_name, st.transformed_uid,
                st.instance_type, st.instance_access, st.players_known,
                st.players, st.player_names, monitor.logs)

    # ── 1. 印は正規表現の一部そのもの ─────────────
    def test_each_mark_is_part_of_its_regex(self):
        """正規表現を直して前絞りが取りこぼすようになったら、ここで落ちる。
        RE_JOINING は Joining と wrld_ の間に捕獲グループの ( があるので、
        グループを開く ( を除いた形で比べる"""
        def literal(pattern):
            return re.sub(r"(?<!\\)\((?:\?:)?", "", pattern)

        for mark, rx in ((LogParser.JOINING_MARK, LogParser.RE_JOINING),
                         (LogParser.USER_AUTH_MARK, LogParser.RE_USER_AUTH),
                         (LogParser.PLAYER_JOINED_MARK, LogParser.RE_PLAYER_JOINED),
                         (LogParser.PLAYER_LEFT_MARK, LogParser.RE_PLAYER_LEFT)):
            self.assertIn(mark, literal(rx.pattern), rx.pattern)

    def test_every_line_the_scans_look_for_carries_its_mark(self):
        """parse() がその種類を返した行は、必ず印を含む"""
        kinds = {LogParser.EVENT_JOINING: LogParser.JOINING_MARK,
                 LogParser.EVENT_USER_AUTH: LogParser.USER_AUTH_MARK,
                 LogParser.EVENT_PLAYER_JOINED: LogParser.PLAYER_JOINED_MARK,
                 LogParser.EVENT_PLAYER_LEFT: LogParser.PLAYER_LEFT_MARK}
        seen = set()
        for line in self.LINES:
            event = LogParser.parse(self.PREFIX + line)
            if event and event.kind in kinds:
                seen.add(event.kind)
                self.assertIn(kinds[event.kind], line)
        self.assertEqual(seen, set(kinds), "4種類とも試せていること")

    # ── 2. 結果は前絞りの前後で同じ ──────────────
    def test_the_start_scan_gives_the_same_result(self):
        path = self._log_file(self.LINES)
        result = self._start_scan(path)
        self.assertEqual(result, self._start_scan(path, marks=("",)))
        self.assertEqual(result[0], self.ME)
        self.assertEqual(result[6], {"usr_a", self.ME})

    def test_the_instance_type_detection_gives_the_same_result(self):
        for lines in (self.LINES, self.LINES[:3], self.LINES[5:9], []):
            path = self._log_file(lines)
            filtered = LogMonitor.LogMonitor.detect_instance_type_from_log(path)
            with patch.object(LogParser, "JOINING_MARK", ""):
                full = LogMonitor.LogMonitor.detect_instance_type_from_log(path)
            self.assertEqual(filtered, full, lines)
        self.assertIsNotNone(LogMonitor.LogMonitor.detect_instance_type_from_log(
            self._log_file(self.LINES)))

    # ── 3. 4種類以外の行は parse() にかけない ──────────
    def test_other_lines_are_not_parsed(self):
        marks = LogMonitor.LogMonitor._START_SCAN_MARKS
        # Joining の行が無いので末尾から先頭まで全部遡る
        path = self._log_file([l for l in self.LINES if "Joining wrld_" not in l])
        with patch.object(LogParser, "parse", wraps=LogParser.parse) as parse:
            self._start_scan(path)
        parsed = [c.args[0] for c in parse.call_args_list]
        self.assertTrue(parsed)
        for line in parsed:
            self.assertTrue(any(m in line for m in marks), line)

        with patch.object(LogParser, "parse", wraps=LogParser.parse) as parse:
            LogMonitor.LogMonitor.detect_instance_type_from_log(path)
        self.assertEqual(parse.call_count, 0, "Joining の無いログは1行も parse() しない")

    # ── 4/5. 統計用の通信は待たない ───────────────
    def test_page_collected_line(self):
        event = LogParser.parse("2026.09.20 11:55:47 Debug      -  Page Collected - 3/8")
        self.assertEqual((event.kind, event.page), (LogParser.EVENT_PAGE_COLLECTED, 3))
        self.assertIsNone(LogParser.parse("Page Collected - 3/9"))

    def test_the_held_item_marks_are_in_the_patterns_and_the_scan(self):
        for mark, rx in ((LogParser.ITEM_EQUIP_MARK, LogParser.RE_ITEM_EQUIP),
                         (LogParser.PAGE_COLLECTED_MARK, LogParser.RE_PAGE_COLLECTED),
                         (LogParser.RESPAWN_MARK, LogParser.RE_RESPAWN_GENERIC),
                         (LogParser.YOU_DIED_MARK, LogParser.RE_YOU_DIED),
                         (LogParser.SUS_PLAYER_MARK, LogParser.RE_SUS_PLAYER)):
            self.assertIn(mark, rx.pattern.replace("(", ""))
            self.assertIn(mark, LogMonitor.LogMonitor._START_SCAN_MARKS)

    def test_the_scan_does_not_wait_for_the_statistics_id(self):
        path = self._log_file(self.LINES)
        monitor = self._monitor(path)
        done = threading.Event()

        def slow(_uid):
            time.sleep(2.0)
            done.set()
            return 42

        with patch.object(ConnectDB, "send_Users", side_effect=slow):
            started = time.monotonic()
            monitor._detect_instance_from_log()
            took = time.monotonic() - started
            self.assertLess(took, 1.0, "通信（2秒）を待たずに返る")
            self.assertTrue(monitor.st.players_known, "遡りは済んでいる")
            self.assertIsNone(monitor.st.transformed_uid, "まだ届いていない")
            self.assertTrue(done.wait(5.0))
            for _ in range(100):
                if monitor.st.transformed_uid is not None:
                    break
                time.sleep(0.01)

        self.assertEqual(monitor.st.transformed_uid, 42, "届けば入る")
        self.assertTrue(any("transformed_uid: 42" in m for m in monitor.logs), monitor.logs)

    def test_a_failed_request_leaves_the_id_empty(self):
        monitor = self._monitor(self._log_file(self.LINES))

        def offline(_uid):
            raise OSError("offline")    # 毎回作る（使い回すと例外がログを掴んだままになる）

        with patch.object(ConnectDB, "send_Users", side_effect=offline), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: f(*a)):
            monitor._detect_instance_from_log()

        self.assertIsNone(monitor.st.transformed_uid)
        self.assertTrue(monitor.st.players_known, "遡りは最後まで済む")
        self.assertTrue(any("transformed_uid の取得に失敗" in m for m in monitor.logs),
                        monitor.logs)
