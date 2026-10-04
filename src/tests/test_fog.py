"""霧（看破・Enrage・Stunned・Joy・Foxy・オブジェクトなし→DTM）"""
from tests.support import *  # noqa: F401,F403




class TestInstanceAccess(unittest.TestCase):
    """看破してよいインスタンスか（Joining 行の公開範囲）"""

    def _access(self, tail):
        event = LogParser.parse("2026.09.21 16:30:39 Debug      -  [Behaviour] Joining "
                                "wrld_a5e9ec13-36b1-4e63-ae0c-dab9023401f9:94781"
                                + tail + "~region(jp)")
        return LogParser.instance_access(event.suffix)

    def test_the_nine_kinds(self):
        """看破は、霧看破のボタンが ON で Friends・Invite+・Invite のときだけ"""
        FogEarlyRead.set_early_read_enabled(True)
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)
        cases = {
            "~private(usr_0e01408a)": ("invite", True),
            "~private(usr_0e01408a)~canRequestInvite": ("invite_plus", True),
            "~friends(usr_0e01408a)": ("friends", True),
            "~group(grp_8f8ace13)~groupAccessType(members)": ("group_members", False),
            "~hidden(usr_0e01408a)": ("friends_plus", False),
            "~group(grp_8f8ace13)~groupAccessType(plus)": ("group_plus", False),
            "~group(grp_8f8ace13)~groupAccessType(public)": ("group_public", False),
            "": ("public", False),
        }
        for tail, (kind, allowed) in cases.items():
            access = self._access(tail)
            self.assertEqual(access, kind, tail)
            self.assertEqual(FogEarlyRead.early_read_allowed(access), allowed, tail)

    def test_what_cannot_be_read_is_ng(self):
        self.assertEqual(LogParser.instance_access(None), "unknown")
        self.assertFalse(FogEarlyRead.early_read_allowed("unknown"))
        self.assertFalse(FogEarlyRead.early_read_allowed(""), "Joining 行を見ていない")
        self.assertEqual(self._access("~group(grp_8f8ace13)"), "unknown")
        self.assertEqual(WindowState().instance_access, "", "既定は判定できない＝NG")

    def test_a_joining_line_sets_it_on_the_window(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None)
        monitor._process("2026.09.21 17:04:52 Debug      -  [Behaviour] Joining "
                         "wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd:54453~group("
                         "grp_8f8ace13-018b-47e6-a0f3-885831fd9bc8)~groupAccessType"
                         "(members)~region(jp)")

        self.assertEqual(monitor.st.instance_access, "group_members")




class TestEarlyReadLaunchFlag(unittest.TestCase):
    """起動方法（窓のログの先頭 8KB に --enable-sdk-log-levels があるか）"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _log(self, text):
        path = Path(self._dir.name) / "output_log.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_flag_at_the_head(self):
        path = self._log("2026.09.21 16:30:15 Debug      -  Arg: --enable-sdk-log-levels\n")
        self.assertTrue(FogEarlyRead.launched_for_early_read(path))

    def test_no_flag(self):
        path = self._log("2026.09.21 16:30:15 Debug      -  Arg: --enable-debug-gui\n")
        self.assertFalse(FogEarlyRead.launched_for_early_read(path))

    def test_the_flag_after_8kb_does_not_count(self):
        path = self._log("x" * (FogEarlyRead.LOG_HEAD_BYTES + 10)
                         + " Arg: --enable-sdk-log-levels\n")
        self.assertFalse(FogEarlyRead.launched_for_early_read(path))

    def test_an_unreadable_log_cannot(self):
        self.assertFalse(FogEarlyRead.launched_for_early_read(
            Path(self._dir.name) / "nope.txt"))

    def test_start_reads_it_once(self):
        path = self._log("Arg: --enable-sdk-log-levels\n")
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path), {}, lambda _m: None)
        with patch.object(monitor, "_start_daemon"), \
             patch.object(monitor._action, "start_velocity_receiver"):
            monitor.start()
        monitor.stop()

        self.assertTrue(monitor.early_read_capable)




class TestEarlyReadNames(unittest.TestCase):
    """看破の名前の取り出しと照合"""

    def _names(self, lines):
        return [LogParser.parse(line).player_name for line in lines]

    def test_the_real_log_table(self):
        """objects で 6件すべて決まり、どれも公開と一致する（食い違い0件）"""
        data = fog_early_read_terrors()
        for where, lines, _reveal, public, expected in FOG_EARLY_READ_ROUNDS:
            decided = {ReadJson.fog_terror_id_by_object_name(n, data)
                       for n in self._names(lines)} - {None}
            self.assertEqual(decided, {expected}, where)
            self.assertEqual(expected, public, f"{where}: 公開と一致")

    def test_the_bracket_is_not_the_id(self):
        """[7] THE SUN は Black Sun（6）。[29b] のように数字でないこともある"""
        names = self._names([FOG_EARLY_READ_ROUNDS[0][1][0],
                             FOG_EARLY_READ_ROUNDS[5][1][0]])
        self.assertEqual(names, ["THE SUN", "SmileyWalker"])

    def test_every_line_shape_gives_the_name(self):
        for line in FOG_EARLY_READ_ROUNDS[5][1]:
            self.assertEqual(LogParser.parse(line).player_name, "SmileyWalker", line)
        self.assertEqual(LogParser.network_object_name(
            "[NetworkProcessing] Setting [12] witchling (15) to request ownership"),
            "witchling (15)")
        self.assertEqual(LogParser.parse(
            "2026.09.21 20:46:00 Debug      -  [NetworkProcessing] Setting [29b] "
            "SmileyWalker to request ownership").player_name, "SmileyWalker")
        self.assertEqual(LogParser.network_object_name(
            "[NetworkProcessing] Transferred ownership of [3] Express Train to Hell to 20"),
            "Express Train to Hell", "名前の中の to で切らない")

    def test_the_log_time(self):
        at = LogParser.log_time("2026.09.21 22:04:41 Debug      -  x")
        self.assertEqual(datetime.fromtimestamp(at), datetime(2026, 9, 21, 22, 4, 41))
        self.assertEqual(LogParser.log_time("2026.09.21 22:04:43 Warning    -  y") - at, 2)
        for line in ("x", "", None, "2026.13.21 22:04:41 Debug      -  x"):
            self.assertIsNone(LogParser.log_time(line), line)

    def test_lines_without_an_object_are_not_events(self):
        self.assertIsNone(LogParser.parse("2026.09.21 16:38:23 Debug      -  "
                                          "[NetworkProcessing] Received ownership transfer of 12 from 3 to 4"))

    def test_a_bare_world_part_is_read_but_never_a_terror(self):
        """番号なしの「Transferred ownership of 名前 to 数字」は名前を取る（Waldo のため）。
        ワールドの部品の名前は objects に無いので、看破には使われない"""
        event = LogParser.parse("2026.09.21 16:38:23 Debug      -  "
                                "[NetworkProcessing] Transferred ownership of monsterDetectionBox to 9")
        self.assertEqual(event.player_name, "monsterDetectionBox")
        data = ReadJson.load_terrors(config.resource_path("terrors.json"))
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("monsterDetectionBox", data))

    def test_normalization(self):
        data = fog_early_read_terrors()
        for name in ("Paradise Bird", "PARADISE BIRD", "paradisebird",
                     "Paradise  Bird (12)", " Paradise Bird (1) "):
            self.assertEqual(ReadJson.fog_terror_id_by_object_name(name, data), 146, name)
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Paradise Bird (x)", data))
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("1", data))

    def test_a_name_for_two_ids_is_none(self):
        data = ReadJson.normalize_terrors({
            "classic": [{"id": 1, "name": "A", "terrors": [], "objects": ["Twin"]}],
            "alternate": [{"id": 140, "name": "B", "terrors": [], "objects": ["twin"]}]})
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Twin", data))

    def test_the_objects_names_match(self):
        data = fog_early_read_terrors()
        for name, tid in (("Kuro GuidingStar", 140), ("SmileyWalker", 142),
                          ("Innyume", 28), ("Twin1 (2)", 133), ("witchling (15)", 167)):
            self.assertEqual(ReadJson.fog_terror_id_by_object_name(name, data), tid, name)

    def test_display_and_individual_names_do_not_match(self):
        """objects に無い名前は、表示名でも個体名でも当たらない"""
        data = fog_early_read_terrors()
        for name in ("Smileghost",                      # 28 の表示名
                     "Malicious Twins", "Malicious Twin",   # 133 の表示名・個体名
                     "Smile Walker", "The Knight of Toren",
                     "Unknown Witch", "An Arbiter"):
            self.assertIsNone(ReadJson.fog_terror_id_by_object_name(name, data), name)

    def test_an_empty_or_missing_objects_matches_nothing(self):
        data = fog_early_read_terrors()
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Chomper", data), "139 は空")
        data = ReadJson.normalize_terrors({"classic": [
            {"id": 5, "name": "Nameless", "terrors": ["Nameless"]}]})
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Nameless", data), "objects が無い行")

    def test_unbound_objects_are_not_used(self):
        data = fog_early_read_terrors()
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Duke", data), "227 は unbound")
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Byte Horde", data))

    def test_objects_are_for_early_read_only(self):
        data = fog_early_read_terrors()
        self.assertEqual(ReadJson.fog_terror_id_by_object_name("Kuro GuidingStar", data), 140)
        self.assertIsNone(ReadJson.fog_terror_id_by_name("Kuro GuidingStar", data), "Enrage には使わない")
        self.assertIsNone(ReadJson.terror_id_by_name("Kuro GuidingStar", data))

    def test_broken_objects_are_ignored(self):
        data = ReadJson.normalize_terrors({"alternate": [
            {"id": 140, "name": "K", "terrors": [], "objects": "Kuro"},
            {"id": 141, "name": "T", "terrors": [], "objects": [3, None, " ", "Deal2"]}]})
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Kuro", data))
        self.assertEqual(ReadJson.fog_terror_id_by_object_name("Deal2", data), 141)

    def test_the_real_terrors_json_has_no_name_for_two_ids(self):
        """ガード: 本物の terrors.json の classic / alternate の objects に、2つ以上の ID に当たる名前が無い"""
        data = ReadJson.load_terrors(config.resource_path("terrors.json"))
        objects = data[ReadJson.OBJECTS_KEY]
        owners: dict = {}
        for category in ReadJson.FOG_CATEGORIES:
            for id_, names in objects[category].items():
                for name in names:
                    owners.setdefault(ReadJson.normalize_object_name(name), set()).add(int(id_))
        self.assertTrue(owners, "objects が読めていない")
        self.assertEqual({k: v for k, v in owners.items() if len(v) > 1}, {})




class TestFogEarlyReadUse(unittest.TestCase):
    """看破・Enrage 系の使い分け（インスタンス × 起動方法）。

    看破だけがインスタンスで分かれる。通常のログに出る Enrage / Joy / スタンは
    どのインスタンスでも表示して判定し、DB へも普通に送る。
    """

    FOG_KEY = "Fog/霧"
    SNAIL = 101
    SNAIL_LINE = FOG_EARLY_READ_ROUNDS[3][1][0]
    # 同じ名前がもう一度（1秒後）。看破は最初の名前で決まっているので、二重に判定しない
    SNAIL_AGAIN = line_after(SNAIL_LINE, 1, SNAIL_LINE.split(" -  ", 1)[1])
    SNAIL_REVEAL = FOG_EARLY_READ_ROUNDS[3][2]
    # objects に無い名前（表示名）の行。看破では決まらない
    UNDECIDED_LINE = ("2026.09.21 08:43:40 Debug      -  [NetworkProcessing] serim01 would "
                      "like to transfer [10] The Knight of Toren to すぅみ_suumi")
    UNKNOWN = ("2026.09.21 22:04:41 Debug      -  Killers is unknown - ??? // "
               "Will be revealed after 50 seconds // Round type is Fog")

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.trust = FogEarlyRead.NameTrust(Path(self._dir.name) / "names.json")
        for p in (patch.object(config, "TERRORS", fog_early_read_terrors()),
                  patch.object(FogEarlyRead, "trust", self.trust),
                  patch.object(config, "FOG_EARLY_READ_ENABLED", True)):
            p.start()
            self.addCleanup(p.stop)
        FogEarlyRead.set_early_read_enabled(True)          # 霧看破のボタン
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)
        self.send = self._start(patch.object(ConnectDB, "register_round"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self.printed = self._start(patch("builtins.print"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, access="invite", capable=True, keep=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep if keep is not None else {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.early_read_capable = capable
        monitor.st.in_round = True
        # Fog は特殊ラウンドなので、出た時点で3勝扱い（3クラ解放前の DTM 続行は効かない）
        monitor.st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._process(self.UNKNOWN)
        return monitor

    def _skipped(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs].count("do_skip")

    def _judged(self, monitor):
        """判定に使われたか（自爆・続行アナウンス・フリーズ・録画のどれか）"""
        return bool(self._skipped() or self.play.called or self.record.called
                    or monitor.st.is_continue_round
                    or SharedState.get_continue_round_count())

    # ── OK・看破できる ─────────────────────────
    def test_ok_capable_judges_by_early_read_and_skips(self):
        monitor = self._monitor()

        monitor._process(self.SNAIL_LINE)
        self.assertEqual(self._skipped(), 1, "最初の名前でその場で使う")
        monitor._process(self.SNAIL_AGAIN)

        self.assertEqual(self._skipped(), 1)
        self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True, instance_key=ANY, round_time=ANY)
        self.assertTrue(any("🔎 テラー判明(看破)" in m for m in monitor.logs), monitor.logs)

    def test_ok_capable_judges_by_early_read_and_continues(self):
        monitor = self._monitor(keep={self.FOG_KEY: {self.SNAIL}})

        monitor._process(self.SNAIL_LINE)
        monitor._process(self.SNAIL_AGAIN)

        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3")
        self.assertEqual(self._skipped(), 0)

    def test_ok_capable_falls_back_to_enrage(self):
        monitor = self._monitor()

        monitor._process(self.UNDECIDED_LINE)       # objects に無いので決まらない
        self.assertFalse(self._judged(monitor))
        monitor._on_enrage("Immortal Snail")

        self.assertEqual(self._skipped(), 1)
        self.assertEqual(self.send.call_count, 1)

    def test_an_enrage_after_the_early_read_is_not_used_again(self):
        monitor = self._monitor()
        monitor._process(self.SNAIL_LINE)
        monitor._process(self.SNAIL_AGAIN)
        self.assertEqual(monitor.st.early_read_tid, self.SNAIL)

        monitor._on_enrage("Black Sun")                 # Enrage 系が後から来ても
        monitor._process("2026.09.21 22:04:50 Debug      -  JOY WILL SOON AWAKEN...")
        monitor._process(self.SNAIL_REVEAL)

        self.assertEqual(self._skipped(), 1, "二重に自爆しない")
        self.assertEqual(self.send.call_count, 1)

    def test_an_early_read_after_the_enrage_is_not_used_again(self):
        """Enrage 系が先に決めたら、後から見えた看破は判定にも表示にも使わない"""
        monitor = self._monitor()
        monitor._on_enrage("Immortal Snail")

        monitor._process(self.SNAIL_LINE)

        self.assertEqual(self._skipped(), 1)
        self.assertEqual(self.send.call_count, 1)
        self.assertIsNone(monitor.st.early_read_tid)
        self.assertEqual(sum("🔎" in m for m in monitor.logs), 1, monitor.logs)

    # ── OK・看破できない ────────────────────────
    def test_ok_not_capable_ignores_the_lines_but_uses_the_enrage_family(self):
        for how in ("Enrage", "Joy", "Stunned"):
            self.thread.reset_mock()
            self.send.reset_mock()
            monitor = self._monitor(capable=False)

            monitor._process(self.SNAIL_LINE)
            self.assertEqual(self._skipped(), 0, f"{how}: 看破の行は読まない")
            self.send.assert_not_called()
            if how == "Enrage":
                monitor._on_enrage("Immortal Snail")
            elif how == "Joy":
                monitor._process("2026.09.21 22:04:50 Debug      -  JOY WILL SOON AWAKEN...")
            else:
                monitor._on_stunned("Immortal Snail")

            self.assertEqual(self._skipped(), 1, how)
            self.assertEqual(self.send.call_count, 1, how)
            self.assertFalse(self.send.call_args.kwargs["quiet"], f"{how}: 公開と同じく普通に送る")

    # ── 看破してよいのはインバイトだけ ─────────────────
    def test_the_button_off_is_like_ng_even_in_invite(self):
        """霧看破のボタンが OFF なら、インバイトでも NG と同じ（判定せず、DB に黙って送るだけ）"""
        FogEarlyRead.set_early_read_enabled(False)
        monitor = self._monitor(access="invite", keep={self.FOG_KEY: {self.SNAIL}})
        before = list(monitor.logs)

        monitor._process(self.SNAIL_LINE)

        self.assertFalse(self._judged(monitor))
        self.assertEqual(monitor.logs, before, "何も出さない")
        self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True,
                                          instance_key=ANY, round_time=ANY)

    def test_turning_the_button_on_works_from_the_next_decision(self):
        FogEarlyRead.set_early_read_enabled(False)
        monitor = self._monitor(access="friends")
        FogEarlyRead.set_early_read_enabled(True)          # 動作中に入れる

        monitor._process(self.SNAIL_LINE)

        self.assertEqual(self._skipped(), 1, "Friends でも看破で判定する")

    def test_only_friends_and_invite_instances_are_read_early(self):
        """ボタン ON で看破するのは Friends・Invite+・Invite だけ。ほかは DB に黙って送るだけ"""
        for access, allowed in (("invite", True), ("invite_plus", True), ("friends", True),
                                ("group_members", False), ("friends_plus", False),
                                ("group_plus", False), ("group_public", False),
                                ("public", False), ("unknown", False)):
            self.thread.reset_mock()
            self.play.reset_mock()
            self.record.reset_mock()
            self.send.reset_mock()
            SharedState.continue_round_reset()
            monitor = self._monitor(access=access, keep={self.FOG_KEY: {self.SNAIL}})
            before = list(monitor.logs)

            monitor._process(self.SNAIL_LINE)
            monitor._process(self.SNAIL_AGAIN)

            self.assertEqual(self._judged(monitor), allowed, access)
            self.assertEqual(any("🔎 テラー判明(看破)" in m for m in monitor.logs), allowed, access)
            if not allowed:
                self.assertEqual(monitor.logs, before, access)
            self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True, instance_key=ANY, round_time=ANY)

    # ── NG・看破できる ──────────────────────────
    def test_ng_writes_no_fog_names_to_the_debug_log(self):
        """NG のインスタンスでは、公開前に [NetworkProcessing] の名前もテラーの情報も
        debug.log に出ない（読んだイベントの行・画面の行のどちらにも）"""
        written = []
        with patch.object(DebugLog, "write", side_effect=written.append):
            monitor = self._monitor(access="friends_plus", keep={self.FOG_KEY: {self.SNAIL}})
            monitor._process(self.SNAIL_LINE)
            monitor._process(self.SNAIL_AGAIN)
            monitor._process(self.UNDECIDED_LINE)
        text = "\n".join(written)
        for word in ("Snail", "snail", "Knight", "Toren", str(self.SNAIL), "NetworkProcessing",
                     "network_object", "看破"):
            self.assertNotIn(word, text, word)
        self.assertIn("[事象] killers_unknown", text, "ほかのイベントは書いている")

    def test_ng_capable_sends_to_the_db_only(self):
        monitor = self._monitor(access="friends_plus",
                                keep={self.FOG_KEY: {self.SNAIL}})

        monitor._process(self.SNAIL_LINE)
        monitor._process(self.SNAIL_AGAIN)

        self.assertFalse(self._judged(monitor), "自爆・アナウンス・フリーズ・録画のどれもしない")
        self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True, instance_key=ANY, round_time=ANY)

    # ── NG・看破できない ────────────────────────
    JOY_LINE = "2026.09.21 22:04:50 Debug      -  JOY WILL SOON AWAKEN..."

    def _identify_by(self, monitor, how):
        if how == "Enrage":
            monitor._on_enrage("Immortal Snail")
        elif how == "Joy":
            monitor._process(self.JOY_LINE)
        else:
            monitor._process("2026.09.21 22:04:50 Debug      -  Immortal Snail was stunned.")

    def test_ng_not_capable_judges_by_the_enrage_family_and_skips(self):
        for how, tid in (("Enrage", self.SNAIL), ("Joy", config.JOY_ID),
                         ("Stunned", self.SNAIL)):
            self.thread.reset_mock()
            self.send.reset_mock()
            monitor = self._monitor(access="public", capable=False)

            self._identify_by(monitor, how)

            self.assertTrue(any(f"🔎 テラー判明({how})" in m for m in monitor.logs),
                            (how, monitor.logs))
            self.assertEqual(self._skipped(), 1, how)
            self.assertEqual(self.send.call_count, 1, how)
            self.assertEqual(self.send.call_args.args[1], [tid], how)
            self.assertFalse(self.send.call_args.kwargs["quiet"], how)

    def test_ng_not_capable_judges_by_the_enrage_family_and_continues(self):
        for how, tid in (("Enrage", self.SNAIL), ("Joy", config.JOY_ID),
                         ("Stunned", self.SNAIL)):
            self.thread.reset_mock()
            self.play.reset_mock()
            SharedState.continue_round_reset()
            monitor = self._monitor(access="public", capable=False,
                                    keep={self.FOG_KEY: {tid}})

            self._identify_by(monitor, how)

            self.assertTrue(monitor.st.is_continue_round, how)
            self.play.assert_called_once_with("continue.mp3")
            self.assertEqual(self._skipped(), 0, how)

    def test_ng_sends_only_once_per_round(self):
        monitor = self._monitor(access="group_plus")

        monitor._process(self.SNAIL_LINE)
        monitor._process(self.SNAIL_AGAIN)
        monitor._on_enrage("Immortal Snail")
        monitor._process(self.SNAIL_REVEAL)

        self.assertEqual(self.send.call_count, 1)

    def test_ng_the_enrage_after_a_silent_early_read_judges_once(self):
        """看破は黙って DB に送るだけ。後から来た Enrage でその場で判定し、公開で二重にしない"""
        monitor = self._monitor(access="unknown")
        monitor._process(self.SNAIL_LINE)
        monitor._process(self.SNAIL_AGAIN)
        self.assertEqual(self._skipped(), 0, "看破では判定しない")
        self.assertFalse(any("🔎" in m for m in monitor.logs), monitor.logs)
        self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True, instance_key=ANY, round_time=ANY)

        monitor._on_enrage("Immortal Snail")
        self.assertEqual(self._skipped(), 1, "Enrage でその場で自爆する")
        self.assertTrue(any("🔎 テラー判明(Enrage)" in m for m in monitor.logs), monitor.logs)

        monitor._process(self.SNAIL_REVEAL)
        self.assertEqual(self._skipped(), 1, "公開で二重に自爆しない")

    # ── ログを流さない ───────────────────────────
    def test_nothing_about_the_early_read_is_shown_in_ng(self):
        monitor = self._monitor(access="public",
                                keep={self.FOG_KEY: {self.SNAIL}})
        before = list(monitor.logs)

        monitor._process(self.SNAIL_LINE)
        monitor._process(FOG_EARLY_READ_ROUNDS[0][1][0])  # 別の名前（取り違え）も出さない

        self.assertEqual(monitor.logs, before, "画面のログ・オーバーレイに何も足されない")
        self.printed.assert_not_called()
        self.play.assert_not_called()

    def test_an_unknown_enrage_name_is_logged_in_ng(self):
        monitor = self._monitor(access="public")

        monitor._on_enrage("存在しないテラー名XYZ")

        self.assertTrue(any("Enrage: 存在しないテラー名XYZ" in m for m in monitor.logs),
                        monitor.logs)

    def test_the_early_read_db_send_is_quiet_even_in_ok(self):
        monitor = self._monitor()

        monitor._process(self.SNAIL_LINE)
        monitor._process(self.SNAIL_AGAIN)

        self.assertTrue(self.send.call_args.kwargs["quiet"])
        self.assertFalse(any("Supabase" in m or "送信" in m for m in monitor.logs))

    def test_a_normal_round_is_still_sent_loudly(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"
        monitor.st.fog = False

        monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        self.assertFalse(self.send.call_args.kwargs["quiet"])

    # ── 最初の名前ですぐ決める（保留しない） ──────────────
    def _object_line(self, sec, name):
        return line_after(self.SNAIL_LINE, sec, "[NetworkProcessing] Ignoring TrySetOwner "
                          f"attempt on [3] {name} because x already owner")

    def test_the_first_name_is_used_at_once(self):
        """時間を進めず、次の行も待たずに、最初の名前の行で判定が出る"""
        monitor = self._monitor()
        with patch.object(LogMonitor.time, "time", side_effect=AssertionError("時計を見ない")):
            monitor._process(self.SNAIL_LINE)

        self.assertEqual(self._skipped(), 1)
        self.assertEqual(monitor.st.early_read_tid, self.SNAIL)
        self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True, instance_key=ANY, round_time=ANY)
        self.assertEqual(sum("🔎 テラー判明(看破)" in m for m in monitor.logs), 1, monitor.logs)

    def test_the_same_terror_again_is_not_judged_twice(self):
        """OK の窓も、DB に送るだけの窓（テラーは不明のまま）も、看破は1回だけ"""
        for access, skips in (("invite", 1), ("public", 0)):
            self.thread.reset_mock()
            self.send.reset_mock()
            monitor = self._monitor(access=access)
            with patch.object(monitor, "_identify_fog_terror",
                              wraps=monitor._identify_fog_terror) as identify:
                monitor._process(self.SNAIL_LINE)
                for sec in (0, 1, 2, 3, 10):
                    monitor._process(line_after(self.SNAIL_LINE, sec,
                                                self.SNAIL_LINE.split(" -  ", 1)[1]))
                monitor._process(self._object_line(4, "Immortal Snail"))

            self.assertEqual(identify.call_count, 1, access)
            self.assertEqual(self._skipped(), skips, access)
            self.assertEqual(self.send.call_count, 1, access)
            self.assertEqual(sum("🔎" in m for m in monitor.logs), skips, monitor.logs)
            self.assertFalse(monitor.st.early_read_void, access)

    def test_another_terror_after_the_first_voids_but_does_not_undo(self):
        """別のテラーの名前が後から来たら void にしてログを出す。決めた判定は取り消さない。
        以後の名前も使わない"""
        for access in ("public", "invite"):
            self.thread.reset_mock()
            self.send.reset_mock()
            monitor = self._monitor(access=access)
            before = list(monitor.logs)

            monitor._process(self.SNAIL_LINE)
            judged = self._judged(monitor)
            monitor._process(self._object_line(1, "Paradise Bird"))
            monitor._process(self._object_line(2, "THE SUN"))
            monitor._process(line_after(self.SNAIL_LINE, 20))

            self.assertTrue(monitor.st.early_read_void, access)
            self.assertEqual(monitor.st.early_read_tid, self.SNAIL, access)
            self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True, instance_key=ANY, round_time=ANY)
            self.assertEqual(self._judged(monitor), judged, access)
            voided = [m for m in monitor.logs if "別々のテラー名が見えました" in m]
            if access == "public":
                self.assertFalse(judged, "NG は判定しない")
                self.assertEqual(monitor.logs, before, "NG では何も出さない")
            else:
                self.assertTrue(judged)
                self.assertEqual(self._skipped(), 1, "取り消さない・二重にしない")
                self.assertEqual(len(voided), 1, monitor.logs)

    def test_a_second_name_of_the_same_terror_is_fine(self):
        """同じテラーの別の名前は void にしない。二重にも看破しない
        （DB に送るだけの窓ではテラーが不明のままなので、決めたことを覚えていないと二重になる）"""
        for access in ("invite", "public"):
            self.send.reset_mock()
            monitor = self._monitor(access=access)
            with patch.object(monitor, "_identify_fog_terror",
                              wraps=monitor._identify_fog_terror) as identify:
                monitor._process(self._object_line(0, "WALPURGISNACHT"))
                self.assertEqual(monitor.st.early_read_tid, 167, "最初の名前で決まる")
                monitor._process(self._object_line(1, "witchling (15)"))
                monitor._process(line_after(self.SNAIL_LINE, 3))

            self.assertEqual(identify.call_count, 1, access)
            self.assertEqual(monitor.st.early_read_tid, 167, access)
            self.assertFalse(monitor.st.early_read_void, access)
            self.assertEqual(self.send.call_count, 1, access)

    def test_a_master_switch_before_the_first_name_voids_the_round(self):
        """切り替えの後は全オブジェクトの同期が走ることがある。保留しないので、
        その最初の名前で決めないよう、まだ決めていなければ void にする"""
        monitor = self._monitor()
        monitor._process(line_after(self.SNAIL_LINE, -1, "[Behaviour] OnMasterClientSwitched"))
        monitor._process(self.SNAIL_LINE)
        monitor._process(self.SNAIL_AGAIN)

        self.assertTrue(monitor.st.early_read_void)
        self.assertIsNone(monitor.st.early_read_tid)
        self.assertEqual(self._skipped(), 0)
        self.send.assert_not_called()
        self.assertEqual(sum("マスターが切り替わりました" in m for m in monitor.logs), 1,
                         monitor.logs)

    def test_a_master_switch_after_the_early_read_changes_nothing(self):
        """決めた後の切り替えでは何も変えない（判定は取り消せない）"""
        monitor = self._monitor()
        monitor._process(self.SNAIL_LINE)

        monitor._process(line_after(self.SNAIL_LINE, 1, "[Behaviour] OnMasterClientSwitched"))
        monitor._process(self.SNAIL_AGAIN)

        self.assertFalse(monitor.st.early_read_void)
        self.assertEqual(monitor.st.early_read_tid, self.SNAIL)
        self.assertEqual(self._skipped(), 1)
        self.assertFalse(any("マスターが切り替わりました" in m for m in monitor.logs),
                         monitor.logs)

    def test_a_master_switch_outside_the_fog_changes_nothing(self):
        monitor = self._monitor()
        monitor._process(line_after(self.SNAIL_LINE, 1, "Killers have been revealed - 101 0 0 "
                                                        "// Round type is Fog"))
        monitor._process(line_after(self.SNAIL_LINE, 2, "[Behaviour] OnMasterClientSwitched"))

        self.assertFalse(monitor.st.early_read_void)

    def test_an_enrage_right_after_the_early_read_does_not_judge_again(self):
        monitor = self._monitor()
        monitor._process(self.SNAIL_LINE)

        monitor._on_enrage("Black Sun")                  # 6
        monitor._process(self.SNAIL_AGAIN)

        self.assertEqual(monitor.st.early_read_tid, self.SNAIL)
        self.assertEqual(self._skipped(), 1)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[1], [self.SNAIL])
        self.assertEqual([m for m in monitor.logs if "🔎" in m],
                         [m for m in monitor.logs if "🔎 テラー判明(看破)" in m])

    def test_a_name_after_the_reveal_or_round_over_is_not_read(self):
        for end in (line_after(self.SNAIL_LINE, -1, "Killers have been revealed - 101 0 0 "
                                                    "// Round type is Fog"),
                    line_after(self.SNAIL_LINE, -1, "RoundOver")):
            self.thread.reset_mock()
            self.send.reset_mock()
            monitor = self._monitor()

            monitor._process(end)
            monitor._process(self.SNAIL_LINE)

            self.assertIsNone(monitor.st.early_read_tid, end)
            self.assertFalse(any("🔎 テラー判明(看破)" in m for m in monitor.logs), end)

    def test_nothing_about_the_hold_is_left(self):
        """保留の仕組み（定数・状態・tick）は残っていない"""
        import State
        for module in (LogMonitor, FogEarlyRead, State):
            src = Path(module.__file__).read_text(encoding="utf-8")
            for word in (r"(?<![A-Z_])HOLD_SEC", r"HOLD_TICK_MARGIN_SEC", r"early_read_hold",
                         r"_tick_early_read", r"_settle_early_read"):
                self.assertIsNone(re.search(word, src), (module.__name__, word))
        self.assertFalse(hasattr(FogEarlyRead, "HOLD_SEC"))
        self.assertFalse(hasattr(WindowState(), "early_read_holding"))

    # ── 同じラウンドで2種類 ─────────────────────────
    def test_two_different_ids_void_the_round(self):
        """決めた後に別のIDが見えたら、以後このラウンドの看破は使わない"""
        monitor = self._monitor(access="public")         # DB だけの窓で見る
        sun = FOG_EARLY_READ_ROUNDS[0][1][0]
        monitor._process(sun)                             # THE SUN → 6

        monitor._process(line_after(sun, 10, self.SNAIL_LINE.split(" -  ", 1)[1]))   # 101

        self.assertTrue(monitor.st.early_read_void)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[1], [6])

    def test_two_different_ids_before_use_are_not_used(self):
        monitor = self._monitor()
        monitor.st.early_read_hits = {"thesun": (6, "THE SUN"), "x": (101, "x")}
        monitor.st.early_read_void = True

        monitor._process(FOG_EARLY_READ_ROUNDS[3][1][0])

        self.assertFalse(self._judged(monitor))
        self.send.assert_not_called()

    # ── スイッチ ──────────────────────────────
    def test_the_switch_turns_the_lines_off_but_not_the_enrage_rules(self):
        with patch.object(config, "FOG_EARLY_READ_ENABLED", False):
            ok = self._monitor()
            ok._process(self.SNAIL_LINE)
            self.assertEqual(self._skipped(), 0, "看破の行は読まない")
            ok._on_enrage("Immortal Snail")
            self.assertEqual(self._skipped(), 1, "OK なら Enrage で判定")

            self.send.reset_mock()
            ng = self._monitor(access="public")
            ng._process(self.SNAIL_LINE)
            self.assertEqual(self._skipped(), 1, "看破の行は読まない")
            ng._on_enrage("Immortal Snail")
            self.assertEqual(self._skipped(), 2, "NG でも Enrage で判定")
            self.assertEqual(self.send.call_count, 1)




class TestFogNoObjectDtm(unittest.TestCase):
    """看破できる起動の霧で、Killers is unknown から5秒たっても objects の名前が
    1つも当たらなければ DTM（50）と判断する。時刻はログの行の時刻"""

    FOG_KEY = "Fog/霧"
    DTM = 50
    SNAIL = 101
    UNKNOWN = ("2026.09.21 22:04:41 Debug      -  Killers is unknown - ??? // "
               "Will be revealed after 50 seconds // Round type is Fog")
    SNAIL_AT_2 = ("2026.09.21 22:04:43 Warning    -  [NetworkProcessing] Ignoring TrySetOwner "
                  "attempt on [20] Immortal Snail because tsuki__2 already owner")
    OTHER_NAME_AT_2 = ("2026.09.21 22:04:43 Warning    -  [NetworkProcessing] Ignoring TrySetOwner "
                       "attempt on [10] Chair because tsuki__2 already owner")

    @staticmethod
    def _at(sec, text="[Behaviour] tick"):
        return f"2026.09.21 22:04:{41 + sec:02d} Debug      -  {text}"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.trust = FogEarlyRead.NameTrust(Path(self._dir.name) / "names.json")
        for p in (patch.object(FogEarlyRead, "trust", self.trust),
                  patch.object(config, "FOG_EARLY_READ_ENABLED", True),
                  patch.object(config, "FOG_NO_OBJECT_DTM_ENABLED", True),
                  patch.object(config, "FOG_NO_OBJECT_DTM_SEC", 5.0)):
            p.start()
            self.addCleanup(p.stop)
        FogEarlyRead.set_early_read_enabled(True)
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)
        self.send = self._start(patch.object(ConnectDB, "register_round"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self.debug = self._start(patch.object(LogMonitor.DebugLog, "write"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, access="invite", capable=True, keep=None):
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True, voice_continue="continue.mp3"),
                                        keep if keep is not None else {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.early_read_capable = capable
        monitor.st.in_round = True
        # Fog は特殊ラウンドなので、出た時点で3勝扱い（3クラ解放前の DTM 続行は効かない）
        monitor.st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._process(self.UNKNOWN)
        return monitor

    def _skipped(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs].count("do_skip")

    def _dtm_logs(self, monitor):
        return [m for m in monitor.logs if "オブジェクトなし" in m]

    def _assert_no_dtm(self, monitor):
        self.assertFalse(monitor.st.fog_no_object_dtm)
        self.assertEqual(self._dtm_logs(monitor), [], monitor.logs)
        for c in self.send.call_args_list:
            self.assertNotEqual(c.args[1], [self.DTM])

    # ── 判断する ─────────────────────────────
    def test_nothing_seen_for_5_seconds_is_judged_as_dtm(self):
        monitor = self._monitor()
        monitor._process(self._at(4))
        self.assertEqual(self._dtm_logs(monitor), [], "まだ5秒たっていない")
        self.send.assert_not_called()

        monitor._process(self._at(5))

        self.assertEqual(self._skipped(), 1, "判定に使う（リストに無いので自爆）")
        self.assertTrue(any("🔎 テラー判明(看破（オブジェクトなし）)" in m for m in monitor.logs),
                        monitor.logs)
        self.send.assert_called_once_with("Fog", [self.DTM], 0, None, quiet=True,
                                          instance_key=ANY, round_time=ANY)
        monitor._process(self._at(9))
        self.assertEqual(self._skipped(), 1, "1回だけ")

    def test_dtm_on_the_list_continues(self):
        monitor = self._monitor(keep={self.FOG_KEY: {self.DTM}})
        monitor._process(self._at(6))
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(self._skipped(), 0)

    def test_without_permission_it_only_goes_to_the_db(self):
        for access, button in (("friends_plus", True), ("public", True), ("invite", False)):
            self.send.reset_mock()
            self.thread.reset_mock()
            FogEarlyRead.set_early_read_enabled(button)
            monitor = self._monitor(access=access, keep={self.FOG_KEY: {self.DTM}})
            before = list(monitor.logs)

            monitor._process(self._at(6))

            self.assertEqual(monitor.logs, before, (access, button))
            self.assertEqual(self._skipped(), 0)
            self.assertFalse(monitor.st.is_continue_round)
            self.send.assert_called_once_with("Fog", [self.DTM], 0, None, quiet=True,
                                              instance_key=ANY, round_time=ANY)

    # ── 判断しない ────────────────────────────
    def test_an_early_read_name_first(self):
        monitor = self._monitor()
        monitor._process(self.SNAIL_AT_2)
        monitor._process(self._at(6))
        self._assert_no_dtm(monitor)
        self.assertEqual(self._skipped(), 1, "看破の1回だけ")

    def test_a_name_that_is_not_trusted_still_counts(self):
        self.trust.record(ReadJson.normalize_object_name("Immortal Snail"), False)
        with patch.object(config, "TERRORS", fog_early_read_terrors()):
            monitor = self._monitor()
            monitor._process(self.SNAIL_AT_2)
            self.assertIsNone(monitor.st.early_read_tid, "前提: 信用が足りないので看破には使わない")
            monitor._process(self._at(6))
        self._assert_no_dtm(monitor)
        self.assertEqual(self._skipped(), 0)

    def test_a_name_seen_while_void_still_counts(self):
        with patch.object(config, "TERRORS", fog_early_read_terrors()):
            monitor = self._monitor()
            monitor.st.early_read_void = True
            monitor._process(self.SNAIL_AT_2)
            monitor.st.early_read_void = False      # void だけでは止まらないことを分けて見る
            monitor._process(self._at(6))
        self.assertTrue(monitor.st.fog_object_seen)
        self._assert_no_dtm(monitor)

    def test_a_name_not_in_the_objects_does_not_count(self):
        monitor = self._monitor()
        monitor._process(self.OTHER_NAME_AT_2)
        monitor._process(self._at(6))
        self.assertTrue(monitor.st.fog_no_object_dtm)

    def test_void_does_not_judge(self):
        monitor = self._monitor()
        monitor._process(self._at(1, "[Behaviour] OnMasterClientSwitched"))
        self.assertTrue(monitor.st.early_read_void)
        monitor._process(self._at(6))
        self._assert_no_dtm(monitor)

    def test_decided_by_the_enrage_family_or_foxy_or_joy(self):
        for how in ("Enrage", "Foxy", "Joy", "Stunned"):
            self.send.reset_mock()
            monitor = self._monitor()
            if how == "Enrage":
                monitor._on_enrage("Immortal Snail")
            elif how == "Foxy":
                monitor._process(self._at(2, "foxy the pirate turned evil!"))
            elif how == "Joy":
                monitor._process(self._at(2, "JOY WILL SOON AWAKEN..."))
            else:
                monitor._process(self._at(2, "Immortal Snail was stunned."))
            monitor._process(self._at(6))
            self._assert_no_dtm(monitor)
            self.assertIsNotNone(monitor.st.enrage_identified, how)

    def test_not_capable_does_nothing(self):
        monitor = self._monitor(capable=False)
        monitor._process(self._at(6))
        self._assert_no_dtm(monitor)
        self.send.assert_not_called()

    def test_the_switch_off_does_nothing(self):
        with patch.object(config, "FOG_NO_OBJECT_DTM_ENABLED", False):
            monitor = self._monitor()
            monitor._process(self._at(6))
        self._assert_no_dtm(monitor)
        self.send.assert_not_called()

    def test_the_switch_turned_off_before_the_deadline(self):
        monitor = self._monitor()
        with patch.object(config, "FOG_NO_OBJECT_DTM_ENABLED", False):
            monitor._process(self._at(6))
        self._assert_no_dtm(monitor)

    def test_the_seconds_come_from_the_config(self):
        with patch.object(config, "FOG_NO_OBJECT_DTM_SEC", 8.0):
            monitor = self._monitor()
            monitor._process(self._at(6))
            self.assertFalse(monitor.st.fog_no_object_dtm)
            monitor._process(self._at(8))
        self.assertTrue(monitor.st.fog_no_object_dtm)

    def test_reveal_round_over_or_next_round_before_5_seconds(self):
        for line in ("Killers have been revealed - 101 0 0 // Round type is Fog",
                     "RoundOver",
                     "This round is taking place at Sewers (12) and the round type is Classic"):
            self.send.reset_mock()
            monitor = self._monitor()
            monitor._process(self._at(3, line))
            self.assertEqual(monitor.st.fog_no_object_deadline, 0.0, line)
            monitor._process(self._at(6))
            self._assert_no_dtm(monitor)

    def test_each_guard_on_its_own(self):
        """ほかの経路でも守られている条件を、1つずつ外から崩して確かめる"""
        for name, spoil in (("看破の期間の外", lambda st: setattr(st, "fog_reading", False)),
                            ("看破で決まっている", lambda st: setattr(st, "early_read_tid", 6))):
            self.send.reset_mock()
            monitor = self._monitor()
            spoil(monitor.st)
            monitor._process(self._at(6))
            self._assert_no_dtm(monitor)

    def test_the_switch_is_read_when_arming(self):
        with patch.object(config, "FOG_NO_OBJECT_DTM_ENABLED", False):
            monitor = self._monitor()
        monitor._process(self._at(6))
        self._assert_no_dtm(monitor)

    def test_a_non_fog_unknown_does_not_arm(self):
        monitor = self._monitor()
        self.assertTrue(monitor.st.fog_no_object_deadline, "前提: 霧の不明で構える")
        monitor = self._monitor(capable=False)
        self.assertFalse(monitor.st.fog_no_object_deadline)

    # ── 公開の後の答え合わせ ─────────────────────────
    REVEAL_SNAIL = "Killers have been revealed - 101 0 0 // Round type is Fog"
    REVEAL_DTM = "Killers have been revealed - 50 0 0 // Round type is Fog"

    def _debug_lines(self):
        return [c.args[0] for c in self.debug.call_args_list if "オブジェクトなし" in c.args[0]]

    def test_a_wrong_dtm_is_warned_after_the_reveal(self):
        monitor = self._monitor()
        monitor._process(self._at(6))
        monitor._process(self._at(9, self.REVEAL_SNAIL))

        warnings = [m for m in monitor.logs if "⚠ 看破（オブジェクトなし）が外れました（公開: " in m]
        self.assertEqual(len(warnings), 1, monitor.logs)
        self.assertIn("Immortal Snail", warnings[0])
        self.assertEqual(len(self._debug_lines()), 1)

    def test_a_wrong_dtm_in_an_ng_instance_goes_only_to_the_debug_log(self):
        monitor = self._monitor(access="public")
        monitor._process(self._at(6))
        monitor._process(self._at(9, self.REVEAL_SNAIL))
        self.assertFalse(any("外れました" in m for m in monitor.logs), monitor.logs)
        self.assertEqual(len(self._debug_lines()), 1)
        self.assertIn("外れました", self._debug_lines()[0])

    def test_a_right_dtm_says_nothing(self):
        monitor = self._monitor()
        monitor._process(self._at(6))
        monitor._process(self._at(9, self.REVEAL_DTM))
        self.assertFalse(any("外れました" in m for m in monitor.logs))
        self.assertEqual(self._debug_lines(), [])

    def test_no_warning_without_the_judgment(self):
        monitor = self._monitor()
        monitor._process(self.SNAIL_AT_2)
        monitor._process(self._at(9, "Killers have been revealed - 6 0 0 // Round type is Fog"))
        self.assertFalse(any("オブジェクトなし）が外れました" in m for m in monitor.logs))
        self.assertEqual(self._debug_lines(), [])

    def test_a_new_round_clears_the_state(self):
        monitor = self._monitor()
        monitor._process(self.SNAIL_AT_2)
        monitor._process(self._at(9, "This round is taking place at Sewers (12) and the round type is Fog"))
        monitor._process("2026.09.21 22:05:10 Debug      -  Killers is unknown - ??? // "
                         "Will be revealed after 50 seconds // Round type is Fog")
        monitor._process("2026.09.21 22:05:16 Debug      -  tick")
        self.assertTrue(monitor.st.fog_no_object_dtm, "前のラウンドで見えた名前は数えない")

    # ── Convict Squad（本物の terrors.json）─────────────────
    def test_convict_names_are_convict_squad(self):
        for name in ("Convict (Etrigan)", "Convict (Nami", "Convict (Darkgrey)", "Convict (Have"):
            self.assertEqual(ReadJson.fog_terror_id_by_object_name(name, config.TERRORS), 145, name)

    def test_a_convict_name_decides_convict_squad_and_not_dtm(self):
        monitor = self._monitor()
        monitor._process("2026.09.21 22:04:42 Warning    -  [NetworkProcessing] Ignoring "
                         "TrySetOwner attempt on [5] Convict (Nami because x already owner")
        monitor._process(self._at(6))
        self.assertEqual(monitor.st.early_read_tid, 145)
        self.assertEqual(monitor.st.enrage_identified, 145)
        self._assert_no_dtm(monitor)




class TestFogEarlyReadAnswerCheck(unittest.TestCase):
    """答え合わせ（一度でも公開と食い違った名前は以後使わない）"""

    UNKNOWN = TestFogEarlyReadUse.UNKNOWN

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "fog_object_names.json"
        self.trust = FogEarlyRead.NameTrust(self.path)
        for p in (patch.object(config, "TERRORS", fog_early_read_terrors()),
                  patch.object(FogEarlyRead, "trust", self.trust),
                  patch.object(ConnectDB, "register_round"),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_continue_start")):
            p.start()
            self.addCleanup(p.stop)
        self.send = ConnectDB.register_round

    def _monitor(self, access="invite"):
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True), {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.early_read_capable = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _round(self, monitor, lines, reveal):
        monitor._process("2026.09.21 22:04:00 Debug      -  This round is taking place "
                         "at Facility (12) and the round type is Fog")
        monitor._process(self.UNKNOWN)
        for line in lines:
            monitor._process(line)
        logs_before_reveal = list(monitor.logs)
        if reveal:
            monitor._process(reveal)
        return logs_before_reveal

    def test_the_real_rounds_all_match(self):
        """実ログの6件を流す。食い違いは0件"""
        monitor = self._monitor()
        for where, lines, reveal, public, _expected in FOG_EARLY_READ_ROUNDS:
            self._round(monitor, lines, reveal)
            self.assertEqual(monitor.st.early_read_tid, public, f"{where}: 公開の前に決まる")

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual({k: v.get("mismatch", 0) for k, v in data.items()},
                         {k: 0 for k in data})
        # 名前ごとに数える。Walpurgisnacht は WALPURGISNACHT と witchling の2つ
        self.assertEqual(sum(v["match"] for v in data.values()), 7)
        self.assertEqual(data["witchling"], {"match": 1})

    def test_an_alternate_reveal_is_offset_before_comparing(self):
        """33 は Fog (Alternate) なら 167 Walpurgisnacht。オフセット前で比べると Luigi 扱いになる"""
        monitor = self._monitor()
        row = FOG_EARLY_READ_ROUNDS[2]

        self._round(monitor, row[1], row[2])

        self.assertEqual(self.trust.counts("walpurgisnacht"), (1, 0))

    def test_a_match_is_counted(self):
        monitor = self._monitor()
        row = FOG_EARLY_READ_ROUNDS[3]

        self._round(monitor, row[1], row[2])
        self._round(monitor, row[1], row[2])

        self.assertEqual(self.trust.counts("immortalsnail"), (2, 0))

    def test_a_mismatch_retires_the_name_and_warns_after_the_reveal_only(self):
        monitor = self._monitor(access="public")
        snail = FOG_EARLY_READ_ROUNDS[3][1]
        wrong = ("2026.09.21 22:05:31 Debug      -  Killers have been revealed - "
                 "12 0 0 // Round type is Fog")

        before = self._round(monitor, snail, wrong)

        self.assertFalse(any("食い違い" in m for m in before), "公開の前には出さない")
        warnings = [m for m in monitor.logs if "看破の名前「Immortal Snail」" in m]
        self.assertEqual(len(warnings), 1, monitor.logs)
        self.assertFalse(self.trust.usable("immortalsnail"))

        self.send.reset_mock()
        ok = self._monitor()
        self._round(ok, snail, None)
        ok._process(line_after(snail[0], 1, snail[0].split(" -  ", 1)[1]))   # もう一度見えても
        self.send.assert_not_called()                  # DB にも使わない
        self.assertIsNone(ok.st.early_read_tid, "判定にも使わない")

    def test_it_survives_on_disk(self):
        self.trust.record("immortalsnail", False)

        again = FogEarlyRead.NameTrust(self.path)

        self.assertFalse(again.usable("immortalsnail"))
        self.assertTrue(again.usable("thesun"))

    def test_a_broken_file_is_empty(self):
        for text in ("{not json", "[1, 2]", '{"x": 3, "y": {"mismatch": "a"}}'):
            self.path.write_text(text, encoding="utf-8")
            trust = FogEarlyRead.NameTrust(self.path)

            self.assertTrue(trust.usable("thesun"), text)
            trust.record("thesun", True)                 # 落ちない
            self.assertEqual(trust.counts("thesun")[0], 1, text)

    def test_a_flood_of_names_after_the_reveal_is_not_read(self):
        """実ログ（2026-09-22 22:06:23）: 公開の約1分後に全オブジェクトの同期で数百の名前が
        一度に出た。看破は公開までなので、判定・DB・答え合わせのどれにも使わない"""
        # 公開前の名前を空にした並びなので、オブジェクトなし → DTMは止めて見る
        p = patch.object(config, "FOG_NO_OBJECT_DTM_ENABLED", False)
        p.start()
        self.addCleanup(p.stop)
        monitor = self._monitor()
        self._round(monitor, [], FOG_EARLY_READ_ROUNDS[3][2])
        self.send.reset_mock()
        before = self.trust.counts("immortalsnail")

        for name in ("Innyume", "Twin1 (2)", "Kuro GuidingStar", "SmileyWalker", "Arbiter"):
            monitor._process("2026.09.21 22:06:23 Debug      -  [NetworkProcessing] "
                             f"Ignoring TrySetOwner attempt on [3] {name} because x already owner")
        monitor._process("2026.09.21 22:07:30 Debug      -  RoundOver")

        self.send.assert_not_called()
        self.assertIsNone(monitor.st.early_read_tid)
        self.assertEqual(monitor.st.early_read_hits, {})
        self.assertFalse(monitor.st.early_read_void)
        self.assertEqual(self.trust.counts("immortalsnail"), before)
        self.assertFalse(self.path.exists() and "innyume" in self.path.read_text(encoding="utf-8"))

    def test_a_round_that_ends_before_the_reveal_is_not_checked(self):
        monitor = self._monitor()
        self._round(monitor, FOG_EARLY_READ_ROUNDS[3][1], None)

        monitor._process("2026.09.21 22:05:00 Debug      -  RoundOver")

        self.assertEqual(self.trust.counts("immortalsnail"), (0, 0))
        self.assertFalse(monitor.st.fog_reading)




class TestFogVariantsInNg(unittest.TestCase):
    """Variant の合図（Foxy など）は通常のログ。看破 NG の霧でもその場で出して判定する"""

    PREFIX = "2026.09.21 22:04:45 Debug      -  "
    SIGNALS = {
        "Foxy": "foxy the pirate turned evil!",
        "Bloodthirsty": "The creature is bloodthirsty today...",
        "Hungry Home Invader": "I hear strange sounds coming from the kitchen.",
        "Gigabytes": "The Gigabytes have come.",
        "Atrached": "Lets play a game...",
    }

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self.send = self._start(patch.object(ConnectDB, "register_round"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self.printed = self._start(patch("builtins.print"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, access, round_type="Fog", keep=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_foxy="foxy.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.logs = []
        monitor.logger = monitor.logs.append
        if round_type == "Fog":
            monitor._process(self.PREFIX + "Killers is unknown - ??? // Will be "
                             "revealed after 50 seconds // Round type is Fog")
        return monitor

    def _started(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs]

    # ── NG の霧・公開前 ─────────────────────────
    def test_ng_fog_signals_are_shown_on_the_spot(self):
        expected = {"Foxy": "🦊 Foxyが出た！", "Bloodthirsty": "の合図"}   # 霧が対象の合図
        for source, mark in expected.items():
            monitor = self._monitor("public")

            monitor._process(self.PREFIX + self.SIGNALS[source])

            self.assertTrue(any(mark in m for m in monitor.logs), (source, monitor.logs))

    def test_ng_fog_foxy_is_shown_and_judged_on_the_spot(self):
        monitor = self._monitor("group_plus")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_any_call("foxy.mp3")
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID], "Foxy で確定")
        self.assertEqual(self._started().count("do_skip"), 1)
        self.assertFalse(self.send.call_args.kwargs["quiet"])

        sanic_in_log = config.SANIC_ID - MatchTNL.ALTERNATE_OFFSET
        monitor._process(self.PREFIX + f"Killers have been revealed - {sanic_in_log} 0 0 "
                         "// Round type is Fog (Alternate)")
        self.assertEqual(self._started().count("do_skip"), 1, "公開で二重に自爆しない")

    def test_after_the_reveal_it_is_shown_as_before(self):
        monitor = self._monitor("public")
        monitor._process(self.PREFIX + "Killers have been revealed - 101 0 0 "
                         "// Round type is Fog")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_called_with("foxy.mp3")

    # ── OK の霧は今までどおり ─────────────────────
    def test_ok_fog_foxy_is_as_before(self):
        monitor = self._monitor("invite")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_any_call("foxy.mp3")
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID], "Foxy で確定")
        self.assertIn("do_skip", self._started())

    def test_ok_fog_bloodthirsty_signal_is_logged(self):
        monitor = self._monitor("friends")

        monitor._process(self.PREFIX + self.SIGNALS["Bloodthirsty"])

        self.assertTrue(any("の合図" in m for m in monitor.logs), monitor.logs)

    # ── 霧以外は今までどおり ──────────────────────
    def test_other_rounds_are_as_before_even_in_ng(self):
        expected = {"Gigabytes": "👾", "Atrached": "🎮",
                    "Hungry Home Invader": "の合図", "Bloodthirsty": "の合図"}
        for source, mark in expected.items():
            monitor = self._monitor("public", round_type="Classic")

            monitor._process(self.PREFIX + self.SIGNALS[source])

            self.assertTrue(any(mark in m for m in monitor.logs), (source, monitor.logs))

    def test_foxy_outside_fog_is_as_before_even_in_ng(self):
        monitor = self._monitor("public", round_type="Alternate")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_called_once_with("foxy.mp3")




class TestFogStunned(unittest.TestCase):
    """スタンによる霧のテラー判明。Enrage 系として使い分けに乗る"""

    FOG_KEY = "Fog/霧"
    WITCH = 167            # Walpurgisnacht（個体名 Unknown Witch）
    UNKNOWN = ("2026.09.21 17:31:15 Debug      -  Killers is unknown - ??? // "
               "Will be revealed after 50 seconds // Round type is Fog")
    STUN = "2026.09.21 17:31:40 Debug      -  Unknown Witch was stunned."

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        for p in (patch.object(config, "TERRORS", fog_early_read_terrors()),
                  patch.object(Recorder, "on_continue_start")):
            p.start()
            self.addCleanup(p.stop)
        self.send = self._start(patch.object(ConnectDB, "register_round"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, access, keep=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.st.in_round = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._process(self.UNKNOWN)
        return monitor

    def _skipped(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs].count("do_skip")

    def test_ok_instance_decides_by_the_stun(self):
        monitor = self._monitor("invite", keep={self.FOG_KEY: {self.WITCH}})

        monitor._process(self.STUN)

        self.assertEqual(monitor.st.enrage_identified, self.WITCH)
        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3")
        self.assertTrue(any("テラー判明(Stunned)" in m for m in monitor.logs), monitor.logs)
        self.assertFalse(self.send.call_args.kwargs["quiet"])

    def test_ng_instance_decides_by_the_stun_too(self):
        """スタンは通常のログ。看破 NG のインスタンスでも表示して判定する"""
        monitor = self._monitor("public", keep={self.FOG_KEY: {self.WITCH}})

        monitor._process(self.STUN)

        self.assertEqual(monitor.st.enrage_identified, self.WITCH)
        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3")
        self.assertTrue(any("テラー判明(Stunned)" in m for m in monitor.logs), monitor.logs)
        # Walpurgisnacht はオルタネイトのテラー。DB へは Fog (Alternate) で送る
        self.send.assert_called_once_with("Fog (Alternate)", [self.WITCH], 0, None, quiet=False,
                                          instance_key=ANY, round_time=ANY)
        self.assertEqual(monitor.st.round_type, "Fog", "st.round_type は変えない")

    def test_an_unknown_name_is_logged_in_any_instance(self):
        for access in ("invite", "public"):
            monitor = self._monitor(access)

            monitor._process("2026.09.21 17:31:40 Debug      -  存在しない名前XYZ was stunned.")

            self.assertTrue(any("Stunned: 存在しない名前XYZ" in m for m in monitor.logs),
                            (access, monitor.logs))

    def test_only_while_the_fog_terror_is_unknown(self):
        monitor = self._monitor("invite")
        monitor._process("2026.09.21 17:32:05 Debug      -  Killers have been revealed - "
                         "33 0 0 // Round type is Fog (Alternate)")
        self.thread.reset_mock()

        monitor._process(self.STUN)

        self.assertEqual(self._skipped(), 0, "公開の後のスタンで判定し直さない")

    def test_the_switch(self):
        with patch.object(config, "STUNNED_IDENTIFY_ENABLED", False):
            monitor = self._monitor("invite")
            monitor._process(self.STUN)

        self.assertIsNone(monitor.st.enrage_identified)
        self.send.assert_not_called()




class TestEnrageFogAlternate(unittest.TestCase):
    """Enrage の前倒しでもオルタネイト枠を伝える。

    `Killers is unknown` の行には `Fog (Alternate)` が出ない。焼き芋は
    オルタネイト枠のFogだけを続行リスト判定に回すので、枠を伝えないと
    「リストを見ずに自爆」になる。
    """

    WALPURGISNACHT = 167     # alternate
    HELL_BELL = 65           # classic。実ログでFogに74回出る
    FOG_ALT = GroupRound.FOG_ALTERNATE_ROUND_TYPE

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_YAKIIMO, keep_on=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = "Fog"
        monitor.st.fog = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _enrage(self, monitor, name):
        """グループのルールに渡った round_type を返す（引かれなければ None）"""
        seen = []
        real = monitor._group_decision
        with patch.object(monitor, "_group_decision",
                          side_effect=lambda rt, ids=None: (
                              seen.append(rt), real(rt, ids))[1]), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._on_enrage(name)
        return seen[0] if seen else None

    # ── 焼き芋（実害が出ていたところ） ─────────────
    def test_an_alternate_terror_reaches_the_group_rule_as_alternate(self):
        monitor = self._monitor()

        passed = self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(passed, self.FOG_ALT)

    def test_it_does_not_force_a_skip(self):
        """これが直したかった動き。枠を伝えないと問答無用の自爆になる"""
        monitor = self._monitor()
        self.assertEqual(monitor._group_decision("Fog"), GroupRound.SKIP,
                         "前提: 焼き芋は枠を伝えないとFogを自爆する")

        with patch.object(monitor, "_start_group_skip") as forced:
            self._enrage(monitor, "Walpurgisnacht")

        forced.assert_not_called()

    def test_a_classic_terror_still_forces_a_skip(self):
        """焼き芋の従来どおりの動き。巻き込んで弱めていないこと"""
        monitor = self._monitor()

        with patch.object(monitor, "_start_group_skip") as forced:
            self._enrage(monitor, "Hell Bell")

        forced.assert_called_once()

    def test_an_alternate_terror_in_the_list_continues(self):
        monitor = self._monitor(keep_on={"Fog/霧": {self.WALPURGISNACHT}})

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(monitor.st.is_continue_round)

    def test_a_classic_terror_is_unchanged(self):
        monitor = self._monitor()

        passed = self._enrage(monitor, "Hell Bell")

        self.assertEqual(passed, "Fog", "従来どおり")

    def test_the_hoshiimo_rule_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_HOSHIIMO)

        self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(monitor._group_decision("Fog"), GroupRound.CONTINUE)
        self.assertEqual(monitor._group_decision(self.FOG_ALT),
                         GroupRound.CONTINUE)

    # ── st.round_type は触らない ───────────────
    def test_the_round_type_is_not_rewritten(self):
        """書き換えると cfg.skip_rounds の「Fog」指定が黙って効かなくなる"""
        monitor = self._monitor()

        self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(monitor.st.round_type, "Fog")

    def test_the_round_skip_setting_still_works(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE)
        monitor.cfg.skip_rounds = {"Fog"}

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(monitor._should_skip_by_round(),
                        "Fog を自爆指定していたら、オルタでも自爆する")

    def test_it_is_logged(self):
        monitor = self._monitor()

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(any("オルタネイト確定" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_classic_terror_logs_nothing_about_alternate(self):
        monitor = self._monitor()

        self._enrage(monitor, "Hell Bell")

        self.assertFalse(any("オルタネイト確定" in m for m in monitor.logs),
                         monitor.logs)

    # ── IDを壊さない ──────────────────────────
    def test_the_offset_is_not_applied_twice(self):
        """167 が 301 にならないこと"""
        monitor = self._monitor()

        self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(monitor.st.terror_ids, [self.WALPURGISNACHT])
        self.assertEqual(monitor.st.enrage_identified, self.WALPURGISNACHT)

    def test_the_offset_helper_leaves_alternate_ids_alone(self):
        self.assertEqual(
            MatchTNL.apply_alternate_offset([self.WALPURGISNACHT], self.FOG_ALT),
            [self.WALPURGISNACHT])

    # ── private は変わらない ───────────────────
    def test_private_decisions_are_unchanged(self):
        """実ログに出た Fog の alternate テラーを、両方の枠で突き合わせる。
        LOG_TO_TNL がどちらも `Fog/霧` へ寄せているので結論は同じになる"""
        keep_on = {"Fog/霧": {137, 154, 163, 164, 168, 192, 315, 316}}
        for tid in (167, 157, 151, 159, 137, 161, 153, 142, 162, 156, 160):
            plain = RoundDecision.decide_killers(keep_on, [tid], "Fog", 0, True)
            alt = RoundDecision.decide_killers(keep_on, [tid], self.FOG_ALT,
                                               0, True)

            self.assertEqual(plain.is_continue_round, alt.is_continue_round, tid)

    def test_the_tnl_lookup_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE,
                                keep_on={"Fog/霧": {self.WALPURGISNACHT}})

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(monitor.st.is_continue_round,
                        "private は st.round_type で引くので従来どおり")

    # ── 対象を広げない ───────────────────────
    def test_other_rounds_still_do_nothing(self):
        monitor = self._monitor()
        monitor.st.round_type = "8 Pages"

        passed = self._enrage(monitor, "Walpurgisnacht")

        self.assertIsNone(passed)
        self.assertEqual(monitor.st.terror_ids, [])

    def test_the_switch_still_turns_it_off(self):
        monitor = self._monitor()

        with patch.object(config, "ENRAGE_IDENTIFY_ENABLED", False):
            passed = self._enrage(monitor, "Walpurgisnacht")

        self.assertIsNone(passed)
        self.assertEqual(monitor.logs, [])




class TestEnrageLine(unittest.TestCase):
    """Enrage 行の読み取り"""

    PREFIX = "2026.09.15 10:00:00 Debug      -  "

    def _parse(self, body):
        return LogParser.parse(self.PREFIX + body)

    def test_a_name_is_taken(self):
        event = self._parse("Teuthidatriggered an Enrage State!")

        self.assertEqual(event.kind, LogParser.EVENT_ENRAGE)
        self.assertEqual(event.player_name, "Teuthida")

    def test_the_later_stages_look_the_same(self):
        for stage in ("Enrage2", "Enrage3"):
            event = self._parse(f"Teuthidatriggered an {stage} State!")

            self.assertEqual(event.kind, LogParser.EVENT_ENRAGE, stage)
            self.assertEqual(event.player_name, "Teuthida", stage)

    def test_an_empty_name_is_not_an_event(self):
        """実データに46回ある"""
        self.assertIsNone(self._parse("triggered an Enrage State!"))

    def test_a_stun_line_is_an_event(self):
        """スタンされた名前でも霧のテラーを判明させる（実ログの形そのまま）"""
        for body, name in (("Teuthida was stunned.", "Teuthida"),
                           ("Unknown Witch was stunned.", "Unknown Witch"),
                           ("Mountain Of Smiling Bodies was stunned.",
                            "Mountain Of Smiling Bodies")):
            event = self._parse(body)
            self.assertEqual(event.kind, LogParser.EVENT_STUNNED, body)
            self.assertEqual(event.player_name, name, body)

    def test_a_stun_line_without_a_name_is_not_an_event(self):
        self.assertIsNone(self._parse("was stunned."))

    def test_a_name_with_spaces_survives(self):
        event = self._parse("GlaggleLand Disruptortriggered an Enrage State!")

        self.assertEqual(event.player_name, "GlaggleLand Disruptor")




class TestEnrageIdentify(unittest.TestCase):
    """Fog のテラー不明中に、Enrage から判定を前倒しする"""

    FOG_KEY = "Fog/霧"
    PURSUER = 99

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, keep_on=None,
                 round_type="Fog"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.st.fog = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _enrage(self, monitor, name="The Pursuer"):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_enrage(name)
        self.played = mock_play
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _revealed(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=True)
        self.played = mock_play
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 前倒しされる ────────────────────────
    def test_a_known_name_decides_early(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})

        self._enrage(monitor)

        self.assertEqual(monitor.st.terror_ids, [self.PURSUER])
        self.assertEqual(monitor.st.enrage_identified, self.PURSUER)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertTrue(any("テラー判明(Enrage)" in m for m in monitor.logs),
                        monitor.logs)

    def test_it_works_through_the_log_line(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("2026.09.15 10:00:00 Debug      -  "
                             "The Pursuertriggered an Enrage2 State!")

        self.assertEqual(monitor.st.terror_ids, [self.PURSUER])

    def test_an_unknown_name_waits(self):
        """どちらの表にも無い名前。現物の別名表は随時書き換わるので、
        実在しない名前を使う（Furnace などは表に載りうる）"""
        monitor = self._monitor()

        started = self._enrage(monitor, "存在しないテラー名XYZ")

        self.assertEqual(monitor.st.terror_ids, [])
        self.assertIsNone(monitor.st.enrage_identified)
        self.assertEqual(started, [])
        self.assertTrue(any("テラー表に無し" in m for m in monitor.logs),
                        monitor.logs)

    def test_only_the_first_enrage_counts(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})
        self._enrage(monitor)

        started = self._enrage(monitor, "Teuthida")

        self.assertEqual(started, [])
        self.assertEqual(monitor.st.terror_ids, [self.PURSUER])

    # ── 二重実行しない ──────────────────────
    def test_the_real_reveal_does_not_skip_twice(self):
        monitor = self._monitor()          # 続行リストは空 → 自爆する
        first = self._enrage(monitor)
        self.assertIn("do_skip", first)

        second = self._revealed(monitor, [self.PURSUER])

        self.assertNotIn("do_skip", second, "自爆は1回だけ")

    def test_the_real_reveal_does_not_announce_twice(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})
        self._enrage(monitor)
        self.assertTrue(monitor.st.is_continue_round)

        self._revealed(monitor, [self.PURSUER])

        self.played.assert_not_called()
        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_a_mismatch_is_logged_but_not_redecided(self):
        monitor = self._monitor()
        self._enrage(monitor)

        started = self._revealed(monitor, [42])

        self.assertTrue(any("食い違いました" in m for m in monitor.logs),
                        monitor.logs)
        self.assertEqual(started, [], "やり直さないこと")

    def test_a_matching_reveal_logs_nothing(self):
        monitor = self._monitor()
        self._enrage(monitor)
        monitor.logs.clear()

        self._revealed(monitor, [self.PURSUER])

        self.assertFalse(any("食い違いました" in m for m in monitor.logs),
                         monitor.logs)

    # ── 対象を広げない ───────────────────────
    def test_other_rounds_do_nothing(self):
        for round_type in ("Classic", "8 Pages", "Bloodbath"):
            monitor = self._monitor(round_type=round_type)

            started = self._enrage(monitor)

            self.assertEqual(monitor.st.terror_ids, [], round_type)
            self.assertEqual(started, [], round_type)
            self.assertEqual(monitor.logs, [], round_type)

    def test_a_known_terror_is_left_alone(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [42]

        self._enrage(monitor)

        self.assertEqual(monitor.st.terror_ids, [42])
        self.assertIsNone(monitor.st.enrage_identified)

    def test_the_switch_turns_it_off(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})

        with patch.object(config, "ENRAGE_IDENTIFY_ENABLED", False):
            started = self._enrage(monitor)

        self.assertEqual(monitor.st.terror_ids, [])
        self.assertEqual(started, [])
        self.assertEqual(monitor.logs, [])

    # ── 状態のクリア ────────────────────────
    def test_a_new_round_clears_it(self):
        monitor = self._monitor()
        self._enrage(monitor)
        self.assertIsNotNone(monitor.st.enrage_identified)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertIsNone(monitor.st.enrage_identified)

    def test_a_new_instance_clears_it(self):
        monitor = self._monitor()
        self._enrage(monitor)

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("2026.09.15 10:00:00 Debug      -  [Behaviour] "
                             "Joining wrld_1234:5678~private(usr_x)")

        self.assertIsNone(monitor.st.enrage_identified)

    # ── インスタンス種別 ──────────────────────
    def test_it_works_in_private_and_in_groups(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype, keep_on={self.FOG_KEY: {self.PURSUER}})

            self._enrage(monitor)

            self.assertEqual(monitor.st.terror_ids, [self.PURSUER], itype)
            self.assertEqual(monitor.st.enrage_identified, self.PURSUER, itype)




class TestFogFoxy(unittest.TestCase):
    """霧で Foxy が出たら Foxy(316) として判定する"""

    FOG_KEY = "Fog/霧"
    FOXY_LINE = "2026.09.18 10:00:00 Debug      -  foxy the pirate turned evil!"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    def _monitor(self, keep_on=None, *, instance_type=config.INSTANCE_PRIVATE,
                 skip_rounds=()):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = "Fog"
        monitor.st.fog = True
        monitor._running = True
        return monitor

    def _lines(self, monitor, *lines):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            for line in lines:
                monitor._process(line)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_it_is_judged_as_foxy(self):
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}})

        started = self._lines(monitor, self.FOXY_LINE)

        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_sanic_in_the_list_does_not_count(self):
        """以前は Sanic(136) として判定していた"""
        monitor = self._monitor({self.FOG_KEY: {config.SANIC_ID}})

        started = self._lines(monitor, self.FOXY_LINE)

        self.assertIn("do_skip", started)

    def test_the_round_type_stays_fog(self):
        monitor = self._monitor()

        self._lines(monitor, self.FOXY_LINE)

        self.assertEqual(monitor.st.round_type, "Fog")

    def test_the_fog_skip_designation_still_applies(self):
        """st.round_type を書き換えていた頃は、ここで自爆指定が外れていた"""
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}},
                                skip_rounds=("Fog",))

        self.assertTrue(monitor._should_skip_by_round([config.FOXY_ID], False))

    def test_yakiimo_judges_it_by_the_list(self):
        """焼き芋はオルタ枠の Fog だけをリスト判定に回す"""
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}},
                                instance_type=config.INSTANCE_YAKIIMO)

        started = self._lines(monitor, self.FOXY_LINE)

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_a_later_reveal_does_not_decide_again(self):
        """二重に自爆しないこと"""
        monitor = self._monitor()

        first = self._lines(monitor, self.FOXY_LINE)
        second = self._lines(
            monitor,
            "Killers have been revealed - 2 0 0 // Round type is Fog (Alternate)")

        self.assertEqual(first.count("do_skip"), 1)
        self.assertNotIn("do_skip", second)
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])

    def test_a_revealed_sanic_turns_into_foxy(self):
        """Foxy の行が revealed より後に来た場合"""
        monitor = self._monitor()
        self._lines(monitor,
                    "Killers have been revealed - 2 0 0 // Round type is Fog (Alternate)")
        self.assertEqual(monitor.st.terror_ids, [config.SANIC_ID])

        self._lines(monitor, self.FOXY_LINE)

        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])

    def test_a_revealed_sanic_waits_for_foxy_when_it_matters(self):
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}})

        started = self._lines(
            monitor,
            "Killers have been revealed - 2 0 0 // Round type is Fog (Alternate)")

        self.assertEqual(started, ["_delayed_decision"])




class TestWaldoBareName(unittest.TestCase):
    """霧の看破で、番号の付かないオブジェクト名（Waldo）も拾う"""

    TAG = "[NetworkProcessing] "

    def _name(self, rest):
        return LogParser.network_object_name(self.TAG + rest)

    def test_the_two_bare_forms_give_the_name(self):
        self.assertEqual(self._name("Ignoring TrySetOwner attempt on Waldo because serim01 already owner"),
                         "Waldo")
        self.assertEqual(self._name("Transferred ownership of Waldo to 3"), "Waldo")

    def test_the_numbered_forms_are_as_before(self):
        for rest, name in (
                ("Ignoring TrySetOwner attempt on [20] Immortal Snail because tsuki__2 already owner",
                 "Immortal Snail"),
                ("Transferred ownership of [86] WALPURGISNACHT to 20", "WALPURGISNACHT"),
                ("serim01 would like to transfer [10] Kuro GuidingStar to すぅみ_suumi", "Kuro GuidingStar"),
                ("Non-owner attempted to request ownership of [10] Kuro GuidingStar for someone else.",
                 "Kuro GuidingStar"),
                ("Setting [12] witchling (15) to request ownership", "witchling (15)"),
                ("Transferred ownership of [29b] SmileyWalker to 17", "SmileyWalker")):
            self.assertEqual(self._name(rest), name, rest)

    def test_other_bare_lines_are_not_read(self):
        for rest in ("Holding message 10 from 3", "Could not locate owner on Name_tag",
                     "Transferred ownership of Waldo to someone",
                     "serim01 would like to transfer Waldo to すぅみ_suumi"):
            self.assertEqual(self._name(rest), "", rest)

    def test_waldo_is_in_the_real_objects(self):
        data = ReadJson.load_terrors(config.resource_path("terrors.json"))
        self.assertEqual(ReadJson.fog_terror_id_by_object_name("Waldo", data), 131)
        self.assertEqual(ReadJson.fog_terror_id_by_object_name("Waldo", config.TERRORS), 131)

    def test_a_bare_waldo_line_reads_the_fog_early(self):
        """本物の行で: 霧のラウンド中に番号なしの Waldo → 看破で 131（本物の terrors.json）"""
        data = ReadJson.load_terrors(config.resource_path("terrors.json"))
        for p in (patch.object(config, "TERRORS", data),
                  patch.object(config, "FOG_EARLY_READ_ENABLED", True),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_continue_start"),
                  patch.object(ConnectDB, "register_round")):
            p.start()
            self.addCleanup(p.stop)
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True), {}, lambda _m: None,
                                        window_idx=1)
        monitor.logger = lambda _m: None
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = "invite"
        monitor.early_read_capable = True
        monitor.st.in_round = True
        prefix = "2026.09.30 21:22:00 Debug      -  "
        monitor._process(prefix + "Killers is unknown - ??? // Will be revealed after 50 seconds "
                                  "// Round type is Fog")

        monitor._process(prefix + "[NetworkProcessing] Transferred ownership of Waldo to 3")

        self.assertEqual(monitor.st.early_read_tid, 131)




class TestLogMonitorFogRound(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")   # グループ判定は主催リストが前提
        self._stats_patcher = patch.object(ConnectDB, "register_round")
        self._stats_patcher.start()
        # 続行になると前面化のスレッドが立つ。本物を走らせると、偽の hwnd で
        # 全窓共通ロックを掴んだまま待つので、ほかのテストの時間を狂わせる
        self._focus_patcher = patch.object(WindowOperator, "focus_window",
                                           return_value=True)
        self._focus_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        self._focus_patcher.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, keep_on: dict | None = None):
        cfg = WindowConfig(
            do_skip=True,
            voice_fog="fog.mp3",
            voice_continue="continue.mp3",
        )
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def test_fog_entry_does_not_freeze_the_other_windows(self):
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())

    def test_a_fog_skip_leaves_another_windows_freeze_alone(self):
        """入場で足していないので、判明時に引いてもいけない"""
        _freeze_other_continue()                    # 別の窓の本物の続行
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1,
                         "別の窓のフリーズが残っていること")
        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set())
        mock_thread.assert_called_once()        # 自爆

    def _release_delay_for(self, monitor) -> float:
        """_release_continue_freeze_after_delay が実際に眠る秒数を取り出す"""
        monitor._running = True     # 監視スレッド起動後と同じ状態にする
        slept: list[float] = []
        with patch.object(LogMonitor.time, "sleep", side_effect=slept.append):
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)
        return slept[0]

    def test_fog_freeze_releases_sooner_than_continue_round(self):
        """霧ラウンド（テラー不明のまま）の解除猶予は続行ラウンドより短い"""
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")
            monitor._process("Killers is unknown - ??? // ??? // Round type is Fog")

        self.assertTrue(monitor.st.fog)
        self.assertEqual(self._release_delay_for(monitor),
                         config.FOG_FREEZE_RELEASE_DELAY_SEC)
        self.assertFalse(monitor.st.is_continue_round, "解除まで走ること")

    def test_continue_round_freeze_uses_the_longer_delay(self):
        """テラーが判明した続行ラウンドは長い方の猶予を使う"""
        monitor = self._monitor(keep_on={"Fog/霧": {44}})
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertFalse(monitor.st.fog, "テラー判明後は霧扱いしない")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(self._release_delay_for(monitor),
                         config.CONTINUE_FREEZE_RELEASE_DELAY_SEC)

    def test_fog_reveal_continue_counts_the_freeze_once(self):
        """入場では張らず、続行と分かった時点で1回だけ張る"""
        monitor = self._monitor(keep_on={"Fog/霧": {44}})
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertEqual(SharedState.get_continue_round_count(), 0)

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1)
        mock_play.assert_called_once_with("continue.mp3")

    def test_the_fog_voice_is_off_by_default(self):
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertFalse(config.ANNOUNCE_FOG_ON_ENTRY)
        mock_play.assert_not_called()

    def test_the_fog_voice_can_be_turned_back_on(self):
        """仕組みは残してある"""
        monitor = self._monitor()
        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        mock_play.assert_called_once_with("fog.mp3")
        self.assertEqual(SharedState.get_continue_round_count(), 0,
                         "音声を戻してもフリーズは戻らない")

    def test_the_fog_voice_setting_is_still_there(self):
        self.assertTrue(config.VOICE_FOG.endswith("Fog.mp3"))
        self.assertEqual(WindowConfig().voice_fog, "")

    def test_fog_can_be_chosen_for_the_entry_freeze(self):
        self.assertIn("Fog", config.ROUND_FREEZE_SELECTABLE)

    def test_choosing_fog_freezes_every_window(self):
        SharedState.round_freeze_reset()
        SharedState.set_freeze_rounds({"Fog"})
        self.addCleanup(SharedState.set_freeze_rounds, set())
        self.addCleanup(SharedState.round_freeze_reset)
        monitor = self._monitor()

        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertTrue(monitor.st.round_freeze_held)
        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertEqual(SharedState.get_continue_round_count(), 0,
                         "続行フリーズとは別物")

    def test_fog_is_not_frozen_unless_chosen(self):
        SharedState.round_freeze_reset()
        SharedState.set_freeze_rounds(set())
        monitor = self._monitor()

        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertFalse(monitor.st.round_freeze_held)




class TestFogJoy(unittest.TestCase):
    """霧で「JOY WILL SOON AWAKEN...」が出たらテラーは Joy（alternate 164）"""

    FOG_KEY = "Fog/霧"
    LINE = "2026.09.13 19:51:40 Debug      -  JOY WILL SOON AWAKEN..."

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    def _monitor(self, keep_on=None, *, instance_type=config.INSTANCE_PRIVATE,
                 round_type="Fog"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _joy(self, monitor):
        seen = []
        real = monitor._on_killers

        def spy(ids, round_type, revealed):
            seen.append(round_type)
            return real(ids, round_type, revealed)

        with patch.object(monitor, "_on_killers", side_effect=spy), \
             patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._process(self.LINE)
        self.passed = seen
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_the_line_is_read(self):
        self.assertEqual(LogParser.parse(self.LINE).kind, LogParser.EVENT_JOY)

    def test_the_id_is_joy(self):
        self.assertEqual(ReadJson.terror_name(config.JOY_ID, config.TERRORS), "Joy")
        self.assertTrue(ReadJson.is_alternate_terror(config.JOY_ID, config.TERRORS))

    def test_fog_is_judged_as_joy(self):
        monitor = self._monitor({self.FOG_KEY: {config.JOY_ID}})

        started = self._joy(monitor)

        self.assertEqual(monitor.st.terror_ids, [config.JOY_ID])
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)
        self.assertTrue(any("テラー判明(Joy)" in m for m in monitor.logs),
                        monitor.logs)

    def test_an_unwanted_joy_is_skipped(self):
        monitor = self._monitor()

        self.assertIn("do_skip", self._joy(monitor))

    def test_the_alternate_slot_goes_through_the_argument(self):
        monitor = self._monitor()

        self._joy(monitor)

        self.assertEqual(self.passed, [GroupRound.FOG_ALTERNATE_ROUND_TYPE])
        self.assertEqual(monitor.st.round_type, "Fog")

    def test_yakiimo_falls_to_the_list(self):
        monitor = self._monitor({self.FOG_KEY: {config.JOY_ID}},
                                instance_type=config.INSTANCE_YAKIIMO)

        started = self._joy(monitor)

        self.assertNotIn("do_skip", started, "問答無用スキップにならないこと")
        self.assertTrue(monitor.st.is_continue_round)

    def test_other_rounds_do_nothing(self):
        """実ログ19回のうち18回は霧以外（Alternate/Midnight/Unbound）"""
        for round_type in ("Alternate", "Midnight", "Unbound", "8 Pages"):
            monitor = self._monitor(round_type=round_type)

            started = self._joy(monitor)

            self.assertEqual(started, [], round_type)
            self.assertEqual(self.passed, [], round_type)

    def test_a_known_terror_is_left_alone(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [42]

        self._joy(monitor)

        self.assertEqual(self.passed, [])
        self.assertEqual(monitor.st.terror_ids, [42])

    def test_only_once_per_round(self):
        monitor = self._monitor()
        self._joy(monitor)

        again = self._joy(monitor)

        self.assertEqual(again, [])
        self.assertEqual(self.passed, [])

    def test_a_later_reveal_does_not_decide_again(self):
        monitor = self._monitor()
        first = self._joy(monitor)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._process("Killers have been revealed - 30 0 0 // "
                             "Round type is Fog (Alternate)")

        self.assertEqual(first.count("do_skip"), 1)
        mock_thread.assert_not_called()

    def test_an_enrage_after_joy_does_nothing(self):
        monitor = self._monitor()
        self._joy(monitor)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_enrage("The Pursuer")

        mock_thread.assert_not_called()
        self.assertEqual(monitor.st.terror_ids, [config.JOY_ID])
