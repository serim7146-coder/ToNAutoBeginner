"""LogMonitor のそのほか（インスタンス・所持アイテムなど）"""
from tests.support import *  # noqa: F401,F403




class TestDetectInstanceTypeFromLog(unittest.TestCase):
    """ログ選択時のインスタンスタイプ検出（GUI用）"""

    PREFIX = "2026.07.13 10:00:00 Log        -  "

    def _write_log(self, tmpdir: str, lines: list[str]) -> Path:
        path = Path(tmpdir) / "output_log_test.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_detects_latest_joining_hoshiimo(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_log(d, [
                self.PREFIX + "[Behaviour] Joining wrld_aaa:11111~friends~region(jp)",
                self.PREFIX + "some other line",
                self.PREFIX + f"[Behaviour] Joining wrld_bbb:22222~group({config.HOSHIIMO_GROUP_ID})~groupAccessType(members)~region(jp)",
            ])
            self.assertEqual(
                LogMonitor.LogMonitor.detect_instance_type_from_log(path),
                config.INSTANCE_HOSHIIMO,
            )

    def test_detects_yakiimo(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_log(d, [
                self.PREFIX + f"[Behaviour] Joining wrld_bbb:22222~group({config.YAKIIMO_GROUP_ID})~groupAccessType(members)~region(jp)",
            ])
            self.assertEqual(
                LogMonitor.LogMonitor.detect_instance_type_from_log(path),
                config.INSTANCE_YAKIIMO,
            )

    def test_returns_none_without_joining_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_log(d, [self.PREFIX + "no joining here"])
            self.assertIsNone(LogMonitor.LogMonitor.detect_instance_type_from_log(path))

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(
            LogMonitor.LogMonitor.detect_instance_type_from_log(Path("Z:/no/such/log.txt"))
        )




class TestEmeraldCityInstance(unittest.TestCase):
    """Emerald City は識別だけ。判定にも自爆にも入れない"""

    REAL_LINE = ("2026.09.15 14:32:17 Debug      -  [Behaviour] Joining "
                 "wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd:95351"
                 "~group(grp_8f8ace13-018b-47e6-a0f3-885831fd9bc8)"
                 "~groupAccessType(members)~region(jp)")

    def _parse(self, suffix):
        return LogMonitor.LogMonitor._parse_instance_type(suffix)

    def test_the_group_is_recognised(self):
        suffix = f"~group({config.EMERALD_CITY_GROUP_ID})~groupAccessType(members)"

        self.assertEqual(self._parse(suffix), config.INSTANCE_EMERALD_CITY)

    def test_the_real_log_line_is_recognised(self):
        event = LogParser.parse(self.REAL_LINE)

        self.assertEqual(event.kind, LogParser.EVENT_JOINING)
        self.assertEqual(self._parse(event.suffix), config.INSTANCE_EMERALD_CITY)

    def test_it_is_not_a_group_round_instance(self):
        """ここに入れると自爆が走る。依頼と逆になる"""
        self.assertNotIn(config.INSTANCE_EMERALD_CITY, GroupRound.GROUP_INSTANCES)

    def test_the_group_rules_do_not_apply(self):
        for round_type in ("Classic", "Bloodbath", "Fog", "Mystic Moon",
                           "Sabotage", "8 Pages"):
            self.assertEqual(
                GroupRound.decide(config.INSTANCE_EMERALD_CITY, round_type, [99]),
                GroupRound.NORMAL, round_type)

    def test_the_window_does_not_self_destruct(self):
        """other_group と同じく、判定も自爆も走らない"""
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_EMERALD_CITY
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._on_killers([99], "Classic", revealed=False)
        started = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.is_continue_round)

    def test_an_unknown_group_is_still_other_group(self):
        suffix = "~group(grp_0000ffff-0000-0000-0000-000000000000)"

        self.assertEqual(self._parse(suffix), config.INSTANCE_OTHER_GROUP)

    def test_the_imo_groups_are_unchanged(self):
        self.assertEqual(self._parse(f"~group({config.HOSHIIMO_GROUP_ID})"),
                         config.INSTANCE_HOSHIIMO)
        self.assertEqual(self._parse(f"~group({config.YAKIIMO_GROUP_ID})"),
                         config.INSTANCE_YAKIIMO)

    def test_private_and_public_are_unchanged(self):
        for marker in ("~private", "~friends", "~hidden", "~canRequestInvite"):
            self.assertEqual(self._parse(marker), config.INSTANCE_PRIVATE, marker)
        self.assertEqual(self._parse(""), config.INSTANCE_PUBLIC)
        self.assertEqual(self._parse("~region(jp)"), config.INSTANCE_PUBLIC)

    def test_the_group_id_is_not_empty(self):
        """空だと group() がどのグループにも一致してしまう"""
        self.assertTrue(config.EMERALD_CITY_GROUP_ID.strip())
        self.assertTrue(config.EMERALD_CITY_GROUP_ID.startswith("grp_"))




class TestSusPlayers(unittest.TestCase):
    """Sabotage の選出者を貯める（リセットは Verified Round End）"""

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        monitor.st.local_player_name = "わたし"
        return monitor

    def _feed(self, monitor, line):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process(line)

    def test_one_murderer_is_collected(self):
        monitor = self._monitor()

        self._feed(monitor, "Sus player = 3 ソノア7")

        self.assertEqual(monitor.st.sus_players, ["ソノア7"])

    def test_two_murderers_are_collected(self):
        monitor = self._monitor()

        self._feed(monitor, "Sus player = 3 ソノア7")
        self._feed(monitor, "Sus player 2 = 0 ユウナ2858")

        self.assertEqual(monitor.st.sus_players, ["ソノア7", "ユウナ2858"])

    def test_the_same_name_is_not_doubled(self):
        monitor = self._monitor()

        self._feed(monitor, "Sus player = 3 ソノア7")
        self._feed(monitor, "Sus player = 3 ソノア7")

        self.assertEqual(monitor.st.sus_players, ["ソノア7"])

    def test_round_start_does_not_clear_them(self):
        """ROUND_START は Sus player と同じ秒に来る。ここで消すと選出者が消える"""
        monitor = self._monitor()
        self._feed(monitor, "Sus player = 3 ソノア7")

        self._feed(monitor, "This round is taking place at Facility (12) "
                            "and the round type is Sabotage")

        self.assertEqual(monitor.st.sus_players, ["ソノア7"])

    def test_verified_round_end_clears_them(self):
        monitor = self._monitor()
        self._feed(monitor, "Sus player = 3 ソノア7")

        self._feed(monitor, "Verified Round End")

        self.assertEqual(monitor.st.sus_players, [])

    def test_the_existing_self_check_still_works(self):
        """自分がマーダーかの記録（アイテムロスト判定用）は残すこと"""
        monitor = self._monitor()
        monitor.st.in_round = True
        monitor.st.round_type = "Sabotage"

        self._feed(monitor, "Sus player = 3 わたし")

        self.assertTrue(monitor.st.sabotage_murder_this_round)
        self.assertEqual(monitor.st.sus_players, ["わたし"])




class TestVolumeTargetsInRounds(unittest.TestCase):
    """Run と DTM/Waldo の続行は、音量では通常（その他）。本物の行で確かめる"""

    CLASSIC_KEY = "Classic/クラシック"
    PREFIX = "2026.09.30 13:00:00 Debug      -  "

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        for p in (patch.object(ConnectDB, "register_round"),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_continue_start")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)

    def _monitor(self, keep_on=None):
        cfg = WindowConfig(do_skip=True, cancel_afk=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None, window_idx=1)
        monitor.logger = lambda _m: None
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        return monitor

    def _round(self, monitor, round_type, ids):
        monitor._process(self.PREFIX + "This round is taking place at Facility (12) "
                         f"and the round type is {round_type}")
        monitor._process(self.PREFIX + "Killers have been set - "
                         + " ".join(str(i) for i in (list(ids) + [0, 0])[:3])
                         + f" // Round type is {round_type}")
        if not monitor.st.is_continue_round and monitor.st.terror_ids:
            # 置き換え待ちに入っていれば、合図が来ないまま時間が過ぎたものとして判定まで進める
            monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)

    def test_a_dtm_continue_is_other(self):
        monitor = self._monitor()

        self._round(monitor, "Classic", [LogMonitor.DTM_TERROR_ID])

        self.assertTrue(monitor.st.is_continue_round)
        self.assertTrue(monitor.st.open_special_continue)
        self.assertEqual(WindowVolume.category_of(monitor.st), WindowVolume.OTHER)

    def test_a_normal_continue_is_continue_and_the_next_round_clears_it(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})

        self._round(monitor, "Classic", [99])

        self.assertTrue(monitor.st.is_continue_round)
        self.assertFalse(monitor.st.open_special_continue)
        self.assertEqual(WindowVolume.category_of(monitor.st), WindowVolume.CONTINUE)

    def test_the_dtm_mark_does_not_survive_into_the_next_round(self):
        monitor = self._monitor()
        self._round(monitor, "Classic", [LogMonitor.DTM_TERROR_ID])
        self.assertTrue(monitor.st.open_special_continue)

        monitor._process(self.PREFIX + "This round is taking place at Facility (12) "
                         "and the round type is Classic")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertFalse(monitor.st.open_special_continue)

    def test_every_place_that_ends_a_continue_also_drops_the_mark(self):
        """is_continue_round を落とす所では、必ず一緒に落とす（次のラウンドに残さない）"""
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(src):
            if line.strip() == "st.is_continue_round = False":
                self.assertEqual(src[i + 1].strip(), "st.open_special_continue = False",
                                 f"{i + 1}行目")

    def test_a_group_wanted_is_a_normal_continue(self):
        monitor = self._monitor()
        monitor.st.open_special_continue = True       # 前のラウンドの残りがあっても
        with patch.object(LogMonitor.LogMonitor, "_start_daemon"),              patch.object(monitor, "_group_decision", return_value=LogMonitor.GroupRound.WANTED):
            self.assertTrue(monitor._apply_group_decision("Classic"))
        self.assertTrue(monitor.st.is_continue_round)
        self.assertFalse(monitor.st.open_special_continue)
        self.assertEqual(WindowVolume.category_of(monitor.st), WindowVolume.CONTINUE)




class TestHeldItem(unittest.TestCase):
    """所持アイテム（st.held_item_id）。今のロスト判定とは別"""

    P = "2026.09.20 11:55:47 Debug      -  "

    def setUp(self):
        # RoundOver でも装備待ちフリーズを張るので、前のテストの分を残さない
        SharedState.equip_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        for p in (patch.object(config, "ITEMS", _item_table()),
                  patch.object(ConnectDB, "register_round"),
                  patch.object(LogMonitor.threading, "Thread")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)

    def _monitor(self, round_type="", in_round=False):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.local_player_name = "serim01"
        monitor.st.round_type = round_type
        monitor.st.in_round = in_round
        return monitor

    def _feed(self, monitor, *lines):
        for line in lines:
            monitor._process(self.P + line)

    def _held_logs(self, monitor):
        return [m.split("] ", 1)[1] for m in monitor.logs if "所持アイテム" in m]

    @staticmethod
    def _start(round_type):
        return f"This round is taking place at Sewers (12) and the round type is {round_type}"

    def test_equipping_holds_it_and_logs_the_name_once(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", "Equipping 29. Was using 29", "Equipping 77.")
        self.assertEqual(self._held_logs(monitor),
                         ["所持アイテム: Emerald Coil (id=29)", "所持アイテム: id=77（表に無い）"])
        self.assertEqual(monitor.held_item(), (77, None))
        self._feed(monitor, "Equipping 36.")
        self.assertEqual(monitor.held_item(), (36, "Hamburger"))

    def test_nothing_held_at_first(self):
        self.assertEqual(self._monitor().held_item(), (0, None))

    def test_respawn_in_a_round_loses_it(self):
        monitor = self._monitor("Classic", in_round=True)
        self._feed(monitor, "Equipping 29.", "Player respawned, opted out!")
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(self._held_logs(monitor)[-1],
                         "所持アイテム: なし（Emerald Coil を リスポーン でロスト）")

    def test_respawn_outside_a_round_keeps_it(self):
        monitor = self._monitor("Classic", in_round=False)
        self._feed(monitor, "Equipping 29.", "Player respawned, opted out!")
        self.assertEqual(monitor.st.held_item_id, 29)

    def test_run_death_loses_it_other_deaths_do_not(self):
        monitor = self._monitor("Run", in_round=True)
        self._feed(monitor, "Equipping 77.", "You died.")
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(self._held_logs(monitor)[-1],
                         "所持アイテム: なし（id=77 を Run 死亡 でロスト）")
        monitor = self._monitor("Classic", in_round=True)
        self._feed(monitor, "Equipping 29.", "You died.")
        self.assertEqual(monitor.st.held_item_id, 29)

    def test_punished_start_loses_it(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 36.", self._start("Punished"))
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(self._held_logs(monitor)[-1],
                         "所持アイテム: なし（Hamburger を Punished 開始 でロスト）")

    def test_a_classic_start_keeps_it(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", self._start("Classic"))
        self.assertEqual(monitor.st.held_item_id, 29)

    def test_sabotage_murder_during_the_round_loses_it(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", self._start("Sabotage"), "Sus player = 1 serim01")
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(self._held_logs(monitor)[-1],
                         "所持アイテム: なし（Emerald Coil を Sabotage マーダー でロスト）")

    def test_sabotage_murder_before_the_round_loses_it_at_the_start(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", "Sus player = 1 serim01")
        self.assertEqual(monitor.st.held_item_id, 29, "まだ始まっていない")
        self._feed(monitor, self._start("Sabotage"))
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(self._held_logs(monitor)[-1],
                         "所持アイテム: なし（Emerald Coil を Sabotage マーダー でロスト）")

    def test_someone_else_as_murderer_keeps_it(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", self._start("Sabotage"), "Sus player = 1 other")
        self.assertEqual(monitor.st.held_item_id, 29)

    # ── 8 Pages ─────────────────────────────
    def test_eight_pages_start_keeps_it_even_while_the_loss_judgment_drops_item_id(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", self._start("8 Pages"))
        self.assertEqual(monitor.st.held_item_id, 29)
        self.assertEqual(monitor.st.item_id, 0, "今のロスト判定はそのまま（開始で0）")

    def test_a_page_loses_an_item_marked_0(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", self._start("8 Pages"), "Page Collected - 1/8")
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(self._held_logs(monitor)[-1],
                         "所持アイテム: なし（Emerald Coil を ページ取得 でロスト）")

    def test_a_page_keeps_an_item_marked_1_or_not_in_the_table(self):
        for item in (36, 77):
            monitor = self._monitor()
            self._feed(monitor, f"Equipping {item}.", self._start("8 Pages"),
                       "Page Collected - 1/8", "Page Collected - 2/8")
            self.assertEqual(monitor.st.held_item_id, item, item)

    def test_equipping_a_0_item_again_is_lost_at_the_next_page(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", self._start("8 Pages"), "Page Collected - 1/8",
                   "Equipping 29.")
        self.assertEqual(monitor.st.held_item_id, 29)
        self._feed(monitor, "Page Collected - 2/8")
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(sum("ページ取得 でロスト" in m for m in monitor.logs), 2)

    def test_a_page_line_outside_eight_pages_keeps_it(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", self._start("Classic"), "Page Collected - 1/8")
        self.assertEqual(monitor.st.held_item_id, 29)

    def test_an_empty_table_never_loses_on_a_page(self):
        with patch.object(config, "ITEMS", {}):
            monitor = self._monitor()
            self._feed(monitor, "Equipping 29.", self._start("8 Pages"), "Page Collected - 1/8")
            self.assertEqual(monitor.st.held_item_id, 29)
            self.assertIn("所持アイテム: id=29（表に無い）", self._held_logs(monitor))

    # ── インスタンス移動 ─────────────────────────
    def test_moving_to_another_instance_loses_it(self):
        monitor = self._monitor()
        self._feed(monitor, "Equipping 29.", "[Behaviour] Joining wrld_b:2~private(usr_me)~region(jp)")
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(self._held_logs(monitor), ["所持アイテム: Emerald Coil (id=29)",
                                                    "所持アイテム: なし（インスタンス移動）"])

    def test_moving_without_an_item_logs_nothing(self):
        monitor = self._monitor()
        self._feed(monitor, "[Behaviour] Joining wrld_b:2~private(usr_me)~region(jp)")
        self.assertEqual(self._held_logs(monitor), [])

    # ── 今のロスト判定が変わらない ─────────────────────
    LOSS_LINES = [
        "[Behaviour] Joining wrld_a:1~private(usr_me)~region(jp)",
        "Equipping 29.",
        "This round is taking place at Sewers (12) and the round type is Run",
        "You died.",
        "RoundOver",
        "Verified Round End",
        "Equipping 29.",
        "This round is taking place at Sewers (12) and the round type is 8 Pages",
        "Page Collected - 1/8",
        "Equipping 36.",
        "Page Collected - 2/8",
        "RoundOver",
        "Verified Round End",
        "Equipping 36.",
        "This round is taking place at Sewers (12) and the round type is Punished",
        "Player respawned, opted out!",
        "RoundOver",
        "Verified Round End",
        "Equipping 5.",
        "Sus player = 1 serim01",
        "This round is taking place at Sewers (12) and the round type is Sabotage",
        "RoundOver",
        "Verified Round End",
    ]
    # 変更前（76590e1）の LogMonitor で同じ並びを流して記録した値。その後、Begin を押さない窓は
    # 装備したら waiting_for_equip を解くようになった（2か所。案内の回数は同じ）
    # (st.item_id, waiting_for_equip, item_lost_this_round, 案内の回数)
    LOSS_BEFORE = [(1, False, False, 0), (29, False, False, 0), (29, False, False, 0),
                   (0, False, True, 0), (0, True, True, 1), (0, True, True, 1),
                   (29, False, True, 1), (0, False, True, 1), (0, False, True, 1),   # 装備で待ちを解く
                   (36, False, True, 1), (36, False, True, 1), (36, False, True, 1),
                   (36, False, True, 1), (36, False, True, 1), (0, False, True, 1),
                   (0, False, True, 1), (0, True, True, 2), (0, True, True, 2),
                   (5, False, True, 2), (5, False, True, 2), (0, False, True, 2),   # 同上
                   (0, True, True, 3), (0, True, True, 3)]

    def _loss_trajectory(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.equip_freeze_reset()        # 前の流しの装備待ちフリーズを残さない
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=False, voice_item_lost="lost.mp3"),
                                        {}, lambda _m: None, window_idx=1)
        monitor.st.local_player_name = "serim01"
        monitor._action.announce_item_lost_once = MagicMock()
        out = []
        for line in self.LOSS_LINES:
            monitor._process(self.P + line)
            monitor.st.instance_type = config.INSTANCE_PRIVATE
            out.append((monitor.st.item_id, monitor.st.waiting_for_equip,
                        monitor.st.item_lost_this_round,
                        monitor._action.announce_item_lost_once.call_count))
        return out, monitor

    def test_the_current_loss_judgment_is_unchanged(self):
        with_held, monitor = self._loss_trajectory()
        self.assertEqual(with_held, self.LOSS_BEFORE)
        self.assertEqual(monitor.st.held_item_id, 0, "所持の方は Sabotage マーダーでなくしている")
        with patch.object(LogMonitor.LogMonitor, "_hold_item", lambda self, _i: None), \
             patch.object(LogMonitor.LogMonitor, "_lose_held_item", lambda self, _r: None):
            without_held, _ = self._loss_trajectory()
        self.assertEqual(with_held, without_held)




class TestHeldItemRestored(unittest.TestCase):
    """監視開始の遡りで、入室後のログから所持アイテムを取り戻す"""

    PREFIX = "2026.09.20 11:55:47 Debug      -  "
    ME = "usr_0e01408a"
    JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"

    def setUp(self):
        p = patch.object(config, "ITEMS", _item_table())
        p.start()
        self.addCleanup(p.stop)

    def _restore(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("\n".join(self.PREFIX + line for line in
                            [f"User Authenticated: serim01 ({self.ME})"] + lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name)), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        with patch.object(ConnectDB, "send_Users", return_value=7), \
             patch.object(monitor, "_start_daemon"):
            monitor._detect_instance_from_log()
        restored = [m for m in monitor.logs if "所持アイテム" in m]
        self.assertEqual(len(restored), 1, monitor.logs)
        return monitor, restored[0].split("] ", 1)[1]

    @staticmethod
    def _start(round_type):
        return f"This round is taking place at Sewers (12) and the round type is {round_type}"

    def test_the_last_item_after_joining(self):
        monitor, log = self._restore([self.JOIN, "Equipping 36.", "Equipping 29."])
        self.assertEqual(monitor.st.held_item_id, 29)
        self.assertEqual(log, "所持アイテム（入室後のログから）: Emerald Coil (id=29)")

    def test_an_item_before_joining_is_not_used(self):
        monitor, log = self._restore(["Equipping 29.", self.JOIN, self._start("Classic")])
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(log, "所持アイテム（入室後のログから）: なし")

    def test_an_item_not_in_the_table(self):
        monitor, log = self._restore([self.JOIN, "Equipping 77."])
        self.assertEqual(log, "所持アイテム（入室後のログから）: id=77（表に無い）")

    def test_the_same_rules_as_while_watching(self):
        cases = [
            ([self._start("8 Pages"), "Equipping 29.", "Page Collected - 1/8"], 0),
            ([self._start("8 Pages"), "Equipping 36.", "Page Collected - 1/8"], 36),
            ([self._start("8 Pages"), "Equipping 77.", "Page Collected - 1/8"], 77),
            (["Equipping 29.", self._start("8 Pages")], 29),
            ([self._start("8 Pages"), "Equipping 29.", "Page Collected - 1/8",
              "Equipping 29.", "Page Collected - 2/8"], 0),
            ([self._start("Classic"), "Equipping 29.", "Page Collected - 1/8"], 29),
            (["Equipping 29.", self._start("Punished")], 0),
            (["Equipping 29.", self._start("Classic")], 29),
            ([self._start("Run"), "Equipping 29.", "You died."], 0),
            ([self._start("Classic"), "Equipping 29.", "You died."], 29),
            ([self._start("Classic"), "Equipping 29.", "Player respawned, opted out!"], 0),
            ([self._start("Classic"), "RoundOver", "Equipping 29.",
              "Player respawned, opted out!"], 29),
            (["Equipping 29.", "Sus player = 1 serim01", self._start("Sabotage")], 0),
            (["Equipping 29.", self._start("Sabotage"), "Sus player = 1 serim01"], 0),
            (["Equipping 29.", self._start("Sabotage"), "Sus player = 1 other"], 29),
            (["Equipping 29.", "Sus player = 1 serim01", self._start("Classic"),
              "RoundOver", self._start("Sabotage")], 29),
            ([self._start("Punished"), "Equipping 36."], 36),
        ]
        for lines, expected in cases:
            monitor, _log = self._restore([self.JOIN] + lines)
            self.assertEqual(monitor.st.held_item_id, expected, lines)




class TestTerrorNameAlwaysLogged(unittest.TestCase):
    """テラー名は判定より先に出す（早期returnの手前）

    _on_killers には設定・インスタンス種別による early return が5つあり、
    以前は判定まで到達しないと名前が一切残らなかった。
    """

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()

    def _monitor(self, instance_type=None, keep_on=None, **cfg_kw):
        cfg = WindowConfig(voice_continue="continue.mp3", **cfg_kw)
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type or config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _on_killers(self, monitor, ids=(99,), round_type="Classic", revealed=False):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type, revealed=revealed)
        return monitor.logs

    def test_logged_when_auto_skip_is_off(self):
        logs = self._on_killers(self._monitor(do_skip=False))

        self.assertTrue(any("Terror set:" in m for m in logs), logs)

    def test_logged_under_instance_restriction(self):
        """publicなど操作しないインスタンスでも名前は残す"""
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC)

        logs = self._on_killers(monitor)

        self.assertTrue(any("Terror set:" in m for m in logs), logs)
        self.assertTrue(any("インスタンス制限" in m for m in logs), logs)

    def test_logged_when_hoshiimo_skip_decides(self):
        monitor = self._monitor(instance_type=config.INSTANCE_HOSHIIMO)

        logs = self._on_killers(monitor)

        self.assertTrue(any("Terror set:" in m for m in logs), logs)

    def test_logged_in_hands_free(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(do_skip=True)

        logs = self._on_killers(monitor)

        self.assertTrue(any("Terror set:" in m for m in logs), logs)

    def test_unknown_id_shows_the_raw_id(self):
        """terrors.json に無いIDは ID:xxxxx の形で出る"""
        logs = self._on_killers(self._monitor(), ids=(99999,))

        self.assertTrue(any("ID:99999" in m for m in logs), logs)

    def test_decision_line_has_no_name(self):
        """判定行は名前を落としてタグだけ"""
        logs = self._on_killers(self._monitor())

        decision = [m for m in logs if "判定:" in m]
        self.assertEqual(len(decision), 1, logs)
        self.assertIn("【スキップ】", decision[0])
        self.assertNotIn("テラー", decision[0])

    def test_revealed_uses_the_other_verb(self):
        logs = self._on_killers(self._monitor(), revealed=True)

        self.assertTrue(any("Terror revealed:" in m for m in logs), logs)

    def test_name_comes_before_the_decision(self):
        logs = self._on_killers(self._monitor())
        names = [i for i, m in enumerate(logs) if "Terror set:" in m]
        decisions = [i for i, m in enumerate(logs) if "判定:" in m]

        self.assertTrue(names and decisions)
        self.assertLess(names[0], decisions[0], "名前を先に出すこと")




class TestStringDownloadTrigger(unittest.TestCase):
    """速度検知の起点は Verified Round End。ラウンドデータの取得は使わない

    String Download は Begin 以外でも定期的に出るので、区間の最初の1件という
    前提が崩れる。Verified Round End はラウンドごとに1回で、誰が Begin しても出る。
    """

    END = "Verified Round End"

    DL = ("[String Download] Attempting to load String from URL "
          "'https://pastebin.com/raw/E36sLedn'")
    DL_ALT = ("[String Download] Attempting to load String from URL "
              "'https://pastebin.com/raw/apPVH4KD'")

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _monitor(self, instance_type=None):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9000), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type or config.INSTANCE_PRIVATE
        monitor.st.round_end_seen = True
        monitor._verified.on_round_end_verified(0)   # Begin を押せる（トラッカーにも）
        monitor.st.last_begin_press_at = time.time()   # ツールが押した直後（受理されるのはこのときだけ）
        return monitor

    def _started(self, monitor, line=None):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process(line or self.DL)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_download_line_is_no_event(self):
        """使っていないので読まない（debug.log の行の約 9% を占めていた）"""
        for line in (self.DL, self.DL_ALT):
            self.assertIsNone(LogParser.parse("2026.08.23 17:53:12 Debug      -  " + line), line)
        self.assertFalse(hasattr(LogParser, "EVENT_STRING_DOWNLOAD"))

    def test_clearing_queue_line_does_not_match(self):
        """同じ [String Download] で始まる別の行を拾わないこと"""
        event = LogParser.parse("2026.08.23 17:53:12 Debug      -  "
                                "[String Download] Clearing string download queue")

        self.assertIsNone(event)

    def test_a_download_starts_nothing(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO):
            monitor = self._monitor(itype)

            self.assertEqual(self._started(monitor), [], itype)
            self.assertFalse(monitor.st.speed_probe_done, itype)

    def test_the_round_end_starts_detection_in_every_instance(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO, config.INSTANCE_PUBLIC):
            monitor = self._monitor(itype)
            monitor.st.round_end_seen = False
            monitor._verified.on_round_start(0)          # RoundOver の後・Verified Round End の前
            monitor._verified.on_round_over(0)

            started = self._started(monitor, self.END)

            self.assertIn("do_speed_detect", started, itype)
            self.assertNotIn("do_speed_strafe", started,
                             f"{itype}: 横移動は Verified で")
            self.assertTrue(monitor.st.speed_probe_done, itype)

    def test_download_before_round_end_starts_nothing(self):
        monitor = self._monitor()
        monitor.st.round_end_seen = False
        monitor._verified.on_round_start(0)          # RoundOver の後・Verified Round End の前
        monitor._verified.on_round_over(0)

        self.assertEqual(self._started(monitor), [])
        self.assertFalse(monitor.st.speed_probe_done)

    def test_it_starts_once_per_round(self):
        monitor = self._monitor()

        first = self._started(monitor, self.END)
        second = self._started(monitor, self.END)
        downloads = self._started(monitor)

        self.assertIn("do_speed_detect", first)
        self.assertNotIn("do_speed_detect", second)
        self.assertEqual(downloads, [])

    def test_neither_url_starts_it(self):
        for line in (self.DL, self.DL_ALT):
            self.assertEqual(self._started(self._monitor(), line), [], line)

    def test_toggle_off_starts_nothing(self):
        SharedState.set_speed_detect(False)

        self.assertNotIn("do_speed_detect", self._started(self._monitor(), self.END))

    def test_round_start_allows_the_next_round(self):
        monitor = self._monitor()
        self._started(monitor, self.END)
        self.assertTrue(monitor.st.speed_probe_done)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertFalse(monitor.st.speed_probe_done)

    def test_verified_does_not_start_the_detection(self):
        """判定の起点はラウンドデータの取得。Verified は横移動だけ"""
        monitor = self._monitor()

        started = self._started(monitor, "Verified")

        self.assertNotIn("do_speed_detect", started)
        self.assertFalse(monitor.st.speed_probe_done)

    def test_verified_still_marks_begin_done(self):
        """Verified の既存動作（自分がBeginを押せたかの記録）は残す"""
        monitor = self._monitor()

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("Verified")

        self.assertTrue(monitor.st.begin_done)

    def test_verified_periodic_guard_still_works(self):
        monitor = self._monitor()
        monitor.st.last_begin_press_at = 0.0          # 押していない（定期）
        base = datetime(2026, 9, 26, 12, 0, 0).timestamp()
        monitor._verified.last_periodic = base
        stamp = datetime.fromtimestamp(base + config.VERIFIED_PERIODIC_SEC)

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(stamp.strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  Verified")

        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(monitor._verified.last_periodic, base + config.VERIFIED_PERIODIC_SEC)


    def test_verified_round_end_guard_still_works(self):
        monitor = self._monitor()
        monitor.st.round_end_seen = False
        monitor._verified.on_round_start(0)          # RoundOver の後・Verified Round End の前
        monitor._verified.on_round_over(0)

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("Verified")

        self.assertFalse(monitor.st.begin_done)

    def test_equip_wait_release_still_uses_begin_done(self):
        """装備待ちフリーズの解除は自分のBegin（Verified）を条件に残す"""
        monitor = self._monitor()
        monitor.st.waiting_for_equip = True
        monitor.st.begin_done = False

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Equipping 42.")
        targets = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]
        self.assertNotIn("_release_equip_wait_after_delay", targets,
                         "自分のBeginが無いうちは解除しないこと")

        monitor.st.begin_done = True
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Equipping 42.")
        targets = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]
        self.assertIn("_release_equip_wait_after_delay", targets)




class TestLogMonitorInstanceParsing(unittest.TestCase):
    def test_private_instance_suffixes(self):
        cases = [
            "~private",
            "~private(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)",
            "~private(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)~canRequestInvite",
            "~friends",
            "~friends(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)",
            "~hidden",
            "~hidden(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)",
            "~canRequestInvite",
        ]

        for suffix in cases:
            with self.subTest(suffix=suffix):
                self.assertEqual(LogMonitor.LogMonitor._parse_instance_type(suffix), config.INSTANCE_PRIVATE)

    def test_group_instance_suffixes(self):
        self.assertEqual(
            LogMonitor.LogMonitor._parse_instance_type(f"~group({config.HOSHIIMO_GROUP_ID})~groupAccessType(members)"),
            config.INSTANCE_HOSHIIMO,
        )
        self.assertEqual(
            LogMonitor.LogMonitor._parse_instance_type(f"~group({config.YAKIIMO_GROUP_ID})~groupAccessType(members)"),
            config.INSTANCE_YAKIIMO,
        )
        self.assertEqual(
            LogMonitor.LogMonitor._parse_instance_type("~group(grp_other)~groupAccessType(public)"),
            config.INSTANCE_OTHER_GROUP,
        )




class TestLogMonitorRuntimeHelpers(unittest.TestCase):
    def test_iter_log_lines_reversed_handles_chunk_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output_log.txt"
            path.write_text("first\nsecond\nthird\n", encoding="utf-8")

            lines = list(LogMonitor.LogMonitor._iter_log_lines_reversed(path, 5))

        self.assertEqual(lines, ["third", "second", "first"])

    def test_stop_sets_running_false_and_wakes_poll_wait(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _msg: None, window_idx=1)
        monitor._running = True
        monitor._stop_event.clear()

        monitor.stop()

        self.assertFalse(monitor._running)
        self.assertTrue(monitor._stop_event.is_set())

    def test_format_terror_ids_caches_name_lookup(self):
        LogMonitor._terror_name_cached.cache_clear()
        with patch.object(LogMonitor.ReadJson, "terror_name", return_value="Cached Terror") as mock_name:
            first = LogMonitor.format_terror_ids([9999])
            second = LogMonitor.format_terror_ids([9999])

        self.assertEqual(first, "Cached Terror")
        self.assertEqual(second, "Cached Terror")
        mock_name.assert_called_once_with(9999, config.TERRORS)




class TestLogMonitorPerWindowInstanceType(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")   # グループ判定は主催リストが前提
        self._stats_patcher = patch.object(ConnectDB, "register_round")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def test_private_skip_is_not_blocked_by_hoshiimo_global_state(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        cfg = WindowConfig(do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Classic", revealed=False)

        mock_thread.assert_called_once()
