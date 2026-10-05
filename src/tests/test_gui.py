"""画面（mainGUI）と設定・キー・外部ツール"""
from tests.support import *  # noqa: F401,F403




class TestLiveLogCandidates(unittest.TestCase):
    """終わったログ・ToN を離れたログは、窓への割り当ての候補から外す。

    掴むと死んだログを読み続けて、その窓は永久に何も検出しなくなる。
    """

    TON = None      # setUp で config から取る

    def setUp(self):
        self.TON = config.TON_WORLD_ID
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.now = time.time()

    def _log(self, name, world=TON, quiet_for=0.0, joining=True):
        path = Path(self._dir.name) / name
        line = ""
        if joining:
            world = self.TON if world is None else world
            line = (f"2026.09.26 12:00:00 Debug      -  [Behaviour] Joining "
                    f"{world}:12345~private(usr_x)~region(jp)\n")
        path.write_text("2026.09.26 11:59:00 Debug      -  start\n" + line, encoding="utf-8")
        os.utime(path, (self.now - quiet_for, self.now - quiet_for))
        return str(path)

    def _keep(self, *paths):
        kept, dropped = VRChatDiscovery.live_ton_logs(paths, now=self.now)
        return [Path(p).name for p in kept], [(Path(p).name, why) for p, why in dropped]

    def test_a_quiet_log_is_dropped(self):
        live = self._log("live.txt")
        dead = self._log("dead.txt", quiet_for=config.LOG_LIVE_GRACE_SEC + 10)

        kept, dropped = self._keep(live, dead)

        self.assertEqual(kept, ["live.txt"])
        self.assertEqual(dropped, [("dead.txt", "更新が止まっています")])

    def test_the_grace_is_the_boundary(self):
        just = self._log("just.txt", quiet_for=config.LOG_LIVE_GRACE_SEC - 1)

        kept, _dropped = self._keep(just)

        self.assertEqual(kept, ["just.txt"], "猶予の内なら残す")

    def test_a_log_that_left_ton_is_dropped(self):
        ton = self._log("ton.txt")
        away = self._log("away.txt", world="wrld_somewhere-else")

        kept, dropped = self._keep(ton, away)

        self.assertEqual(kept, ["ton.txt"])
        self.assertEqual(dropped, [("away.txt", "ToN を離れています")])

    def test_a_fresh_log_without_joining_is_kept(self):
        """起動直後でまだ Joining が無い。窓が立ち上がっている最中"""
        starting = self._log("starting.txt", joining=False)

        kept, dropped = self._keep(starting)

        self.assertEqual(kept, ["starting.txt"])
        self.assertEqual(dropped, [])

    def test_a_missing_file_is_dropped(self):
        kept, dropped = self._keep(str(Path(self._dir.name) / "nope.txt"))

        self.assertEqual(kept, [])
        self.assertEqual(dropped, [("nope.txt", "読めません")])

    # ── 窓への割り当て ───────────────────────
    def _app(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app._dropped_logs = None
        app._live_candidates = lambda c: mainGUI.App._live_candidates(app, c)
        app._dropped_log_lines = mainGUI.App._dropped_log_lines
        return app

    def test_the_assignment_uses_only_live_logs(self):
        app = self._app()
        live = self._log("live.txt")
        dead = self._log("dead.txt", quiet_for=config.LOG_LIVE_GRACE_SEC + 10)

        kept = app._live_candidates([live, dead])

        self.assertEqual(kept, [live])
        self.assertEqual(app.logs,
                         ["[割り当て] 候補から除外: 更新が止まっているログ 1件（dead.txt）"])

    def test_all_dead_falls_back_to_every_log(self):
        """全部外れたら絞り込む前の一覧を使う（割り当て不能にしない）"""
        app = self._app()
        dead = self._log("dead.txt", quiet_for=config.LOG_LIVE_GRACE_SEC + 10)
        away = self._log("away.txt", world="wrld_somewhere-else")

        kept = app._live_candidates([dead, away])

        self.assertEqual(kept, [dead, away])
        self.assertTrue(any("生きているログがありません" in m for m in app.logs), app.logs)

    def test_it_logs_only_when_the_dropped_set_changes(self):
        app = self._app()
        live = self._log("live.txt")
        dead = self._log("dead.txt", quiet_for=config.LOG_LIVE_GRACE_SEC + 10)

        for _ in range(3):
            app._live_candidates([live, dead])
        self.assertEqual(len(app.logs), 1, app.logs)

        away = self._log("away.txt", world="wrld_somewhere-else")
        app._live_candidates([live, dead, away])

        self.assertEqual(len(app.logs), 3, "顔ぶれが変わったら出す")

    def test_the_reasons_come_newest_first(self):
        """理由の並びは初めて出てきた順（受け取った順＝新しい順）"""
        app = self._app()
        self._log("output_log_2026-09-26_12-00-00.txt")
        self._log("output_log_2026-09-26_09-00-00.txt",
                  quiet_for=config.LOG_LIVE_GRACE_SEC + 10)
        self._log("output_log_2026-09-26_10-00-00.txt",
                  quiet_for=config.LOG_LIVE_GRACE_SEC + 10)
        self._log("output_log_2026-09-26_11-00-00.txt", world="wrld_somewhere-else")

        app._live_candidates(VRChatDiscovery.find_latest_logs(Path(self._dir.name), 20))

        self.assertEqual(app.logs, [
            "[割り当て] 候補から除外: ToN を離れているログ 1件（output_log_2026-09-26_11-00-00.txt）",
            "[割り当て] 候補から除外: 更新が止まっているログ 2件",
        ])

    def test_twelve_quiet_logs_make_one_line(self):
        app = self._app()
        live = self._log("live.txt")
        dead = [self._log(f"dead{i:02d}.txt", quiet_for=config.LOG_LIVE_GRACE_SEC + 10)
                for i in range(12)]

        app._live_candidates([live, *dead])

        self.assertEqual(app.logs, ["[割り当て] 候補から除外: 更新が止まっているログ 12件"])

    def test_two_reasons_make_two_lines(self):
        app = self._app()
        live = self._log("live.txt")
        dead = [self._log(f"dead{i}.txt", quiet_for=config.LOG_LIVE_GRACE_SEC + 10)
                for i in range(3)]
        away = self._log("away.txt", world="wrld_somewhere-else")

        app._live_candidates([live, *dead, away])
        app._live_candidates([live, *dead, away])

        self.assertEqual(app.logs, [
            "[割り当て] 候補から除外: 更新が止まっているログ 3件",
            "[割り当て] 候補から除外: ToN を離れているログ 1件（away.txt）",
        ], "顔ぶれが同じなら2回目は出さない")

    def test_an_unknown_reason_is_shown_as_it_is(self):
        self.assertEqual(
            mainGUI.App._dropped_log_lines([("a.txt", "読めません"), ("b.txt", "謎"),
                                            ("c.txt", "謎")]),
            ["[割り当て] 候補から除外: 読めないログ 1件（a.txt）",
             "[割り当て] 候補から除外: 謎: 2件"])

    def test_nothing_dropped_says_nothing(self):
        app = self._app()
        live = self._log("live.txt")

        self.assertEqual(app._live_candidates([live]), [live])
        self.assertEqual(app.logs, [])




class TestOSCPortsFromAssignment(unittest.TestCase):
    """GUI: 割り当てで得たポートを使う（9000 + idx*10 の決め打ちをやめる）"""

    class FakeVar:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeTab:
        def __init__(self, idx, hwnd=0x1000, log=""):
            self.idx = idx
            self.hwnd = hwnd
            self.osc_in = 0
            self.osc_out = 0
            self.v_log = TestOSCPortsFromAssignment.FakeVar(log)
            self.choices = None

        def set_hwnd_choices(self, hwnds, selected_hwnd=None):
            self.choices = list(hwnds)
            self.hwnd = selected_hwnd

        def _get_selected_hwnd(self):
            return self.hwnd

    def _app(self, tabs, assigned):
        app = type("FakeApp", (), {})()
        app.tabs = tabs
        app.logs = []
        app._log = app.logs.append
        app._on_tab_log_selected = lambda tab: None
        app._resolve_windows = lambda windows: assigned
        app._assign_source = mainGUI.App._assign_source
        return app

    @staticmethod
    def _assignment(hwnd, name, osc_in=0, osc_out=0, start_time=1.0):
        return VRChatDiscovery.WindowAssignment(
            hwnd, start_time, Path(f"C:/logs/{name}"), osc_in, osc_out)

    # ── 一括割り当て ───────────────────────────
    def test_the_tabs_take_the_ports_and_the_reason_is_logged(self):
        tabs = [self.FakeTab(0), self.FakeTab(1)]
        assigned = [self._assignment(0xA, "a.txt", 9003, 9004),
                    self._assignment(0xB, "b.txt", start_time=None)]
        app = self._app(tabs, assigned)

        mainGUI.App._assign_windows_and_logs(app, [(0xA, 1.0), (0xB, None)])

        self.assertEqual([(t.hwnd, t.osc_in, t.osc_out) for t in tabs],
                         [(0xA, 9003, 9004), (0xB, 0, 0)])
        self.assertEqual([t.v_log.get() for t in tabs],
                         [str(Path("C:/logs/a.txt")), str(Path("C:/logs/b.txt"))])
        self.assertTrue(any("OSC 9003" in m for m in app.logs), app.logs)
        self.assertTrue(any("起動時刻不明" in m for m in app.logs), app.logs)

    def test_the_hwnd_list_follows_the_new_order(self):
        """タブの[1][2]表示と中身がずれないこと"""
        tabs = [self.FakeTab(0), self.FakeTab(1)]
        assigned = [self._assignment(0xB, "b.txt", 9000, 9001),
                    self._assignment(0xA, "a.txt", 9010, 9011)]
        app = self._app(tabs, assigned)

        mainGUI.App._assign_windows_and_logs(app, [(0xA, 1.0), (0xB, 2.0)])

        self.assertEqual(tabs[0].choices, [0xB, 0xA])

    # ── 開始ボタンの経路 ─────────────────────────
    def _start_paths(self, tabs, assigned):
        app = self._app(tabs, assigned)
        with patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time",
                          return_value=[]):
            mainGUI.App._resolve_tab_ports(app)
        return app

    def test_start_fills_only_the_empty_tabs(self):
        """手で選んだログは上書きしない"""
        tabs = [self.FakeTab(0, hwnd=0xA, log=r"C:\mine\chosen.txt"),
                self.FakeTab(1, hwnd=0xB)]
        app = self._start_paths(tabs, [self._assignment(0xA, "a.txt", 9000, 9001),
                                       self._assignment(0xB, "b.txt", 9010, 9011)])

        self.assertEqual(tabs[0].v_log.get(), r"C:\mine\chosen.txt")
        self.assertEqual(tabs[1].v_log.get(), str(Path("C:/logs/b.txt")))
        self.assertTrue(any("b.txt" in m for m in app.logs), app.logs)

    def test_start_keeps_the_ports_of_the_hwnd_the_user_picked(self):
        """開始時は窓の並びを変えない。HWNDで引き当てる"""
        tabs = [self.FakeTab(0, hwnd=0xB), self.FakeTab(1, hwnd=0xA)]
        self._start_paths(tabs, [self._assignment(0xA, "a.txt", 9000, 9001),
                                 self._assignment(0xB, "b.txt", 9010, 9011)])

        self.assertEqual([(t.osc_in, t.osc_out) for t in tabs],
                         [(9010, 9011), (9000, 9001)])

    def test_a_window_that_is_not_in_the_assignment_has_no_osc(self):
        tabs = [self.FakeTab(0, hwnd=0xC)]
        tabs[0].osc_in, tabs[0].osc_out = 9000, 9001      # 前回の値が残っていても
        self._start_paths(tabs, [self._assignment(0xA, "a.txt", 9000, 9001)])

        self.assertEqual((tabs[0].osc_in, tabs[0].osc_out), (0, 0))
        self.assertEqual(tabs[0].v_log.get(), "")

    def test_a_netstat_failure_is_logged(self):
        app = self._app([], [])
        app._dropped_logs = None
        app._live_candidates = lambda c: mainGUI.App._live_candidates(app, c)
        app._dropped_log_lines = mainGUI.App._dropped_log_lines
        with patch.object(mainGUI.OSCClient, "udp_ports_by_pid", return_value=None), \
             patch.object(VRChatDiscovery, "find_latest_logs", return_value=[]):
            mainGUI.App._resolve_windows(app, [])

        self.assertTrue(any("netstat失敗" in m for m in app.logs), app.logs)

    # ── 速度受信のポート ─────────────────────────
    def test_the_receiver_uses_the_out_port_from_the_log(self):
        """送信ポートは受信+1とは限らない"""
        for out_port, expected in ((9004, 9004), (0, 9001)):
            cfg = WindowConfig(hwnd=123, osc_port=9000, osc_out_port=out_port)
            ex = ActionExecutor.ActionExecutor(cfg, WindowState(),
                                               lambda: True, lambda _m: None)

            with patch.object(ActionExecutor.OSCReceiver, "VelocityReceiver") as mock_recv:
                mock_recv.return_value.start.return_value = True
                ex.start_velocity_receiver()

            self.assertEqual(mock_recv.call_args.args[0], expected, out_port)




class TestOBSSettingsInTheGui(unittest.TestCase):
    """GUI: 設定の受け渡しと接続テスト"""

    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _app(self, enabled=True, host="127.0.0.1", port="4455", password="hunter2"):
        app = type("FakeApp", (), {})()
        app.v_obs_enabled = self.FakeVar(enabled)
        app.v_obs_host = self.FakeVar(host)
        app.v_obs_port = self.FakeVar(port)
        app.v_obs_password = self.FakeVar(password)
        app.logs = []
        app._log = app.logs.append
        app._obs_endpoint = lambda: mainGUI.App._obs_endpoint(app)
        app._save_settings_now = lambda: None
        return app

    def test_the_settings_reach_the_recorder(self):
        app = self._app()
        with patch.object(Recorder, "configure") as mock_configure:
            mainGUI.App._apply_obs_settings(app)

        args = mock_configure.call_args
        self.assertEqual(args.args[:4], (True, "127.0.0.1", 4455, "hunter2"))

    # ── 録画中にOFF ───────────────────────────
    def test_turning_it_off_stops_without_waiting(self):
        app = self._app(enabled=False)
        with patch.object(Recorder, "configure"),              patch.object(Recorder, "stop_all") as mock_stop:
            mainGUI.App._apply_obs_settings(app)

        mock_stop.assert_called_once_with()          # wait_sec を渡さない＝待たない

    def test_turning_it_on_does_not_stop(self):
        app = self._app(enabled=True)
        with patch.object(Recorder, "configure"),              patch.object(Recorder, "stop_all") as mock_stop:
            mainGUI.App._apply_obs_settings(app)

        mock_stop.assert_not_called()

    def _recorder_after_turning_off(self, obs):
        rec = Recorder.Recorder(client_factory=obs.factory)
        rec.configure(True, log=lambda _m: None)
        rec.on_continue_start(1)
        rec.configure(False)
        rec.stop_all()                                # GUIのOFF操作（待たない）
        rec.stop_all(wait_sec=3)                      # 先の分を処理し終えるまで待つ（キューは順番どおり）
        return rec

    def test_turning_it_off_stops_our_recording_once(self):
        obs = FakeOBS()

        self._recorder_after_turning_off(obs)

        self.assertEqual(obs.count("StartRecord"), 1)
        self.assertEqual(obs.count("StopRecord"), 1)

    def test_turning_it_off_leaves_a_hand_started_recording_alone(self):
        obs = FakeOBS(recording=True)

        self._recorder_after_turning_off(obs)

        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertTrue(obs.recording)

    def test_turning_it_off_does_not_block_on_a_hanging_obs(self):
        release = threading.Event()
        self.addCleanup(release.set)
        entered = threading.Event()

        class Hanging:
            def connect(self):
                entered.set()
                release.wait(5)
                return True, ""

            def request(self, *_a):
                return True, {"outputActive": False}, ""

            def close(self):
                pass

        rec = Recorder.Recorder(client_factory=Hanging)
        rec.configure(True, log=lambda _m: None)
        rec.on_continue_start(1)
        entered.wait(3)                               # ワーカーが OBS 待ちで固まっている

        rec.configure(False)
        t0 = time.monotonic()
        rec.stop_all()
        self.assertLess(time.monotonic() - t0, 0.1)

    def test_a_bad_port_falls_back_to_the_default(self):
        app = self._app(port="abc", host="")

        self.assertEqual(mainGUI.App._obs_endpoint(app),
                         (config.OBS_DEFAULT_HOST, config.OBS_DEFAULT_PORT))

    def test_the_connection_test_does_not_record_or_log_the_password(self):
        app = self._app()
        server = FakeOBSServer(password="hunter2", record_active=False)
        self.addCleanup(server.close)
        app.v_obs_port.set(str(server.port))

        mainGUI.App._test_obs_connection(app)
        deadline = time.monotonic() + 5
        while not any("✅" in m or "❌" in m for m in app.logs) \
                and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertTrue(any("✅" in m for m in app.logs), app.logs)
        self.assertEqual(server.requests, ["GetRecordStatus"], "録画はしない")
        self.assertFalse(any("hunter2" in m for m in app.logs), app.logs)

    def test_stopping_the_macro_stops_our_recording(self):
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        stop = source[source.index("    def _stop(self):"):
                      source.index("    def _log(self, msg: str):")]
        self.assertIn("Recorder.stop_all(", stop)

    def test_the_password_entry_hides_the_text(self):
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        self.assertRegex(source, r"textvariable=self\.v_obs_password[^)]*show=\"\*\"")

    def test_no_new_dependency(self):
        """標準ライブラリだけで書いている"""
        for name in ("OBSClient.py", "Recorder.py"):
            source = Path(name).read_text(encoding="utf-8")
            imports = set(re.findall(r"^(?:import|from) (\w+)", source, re.M))
            self.assertLessEqual(imports, {"base64", "hashlib", "json", "os",
                                           "socket", "struct", "time", "queue",
                                           "threading", "typing", "config",
                                           "OBSClient", "DebugLog"}, name)   # 例外を debug.log へ




class TestOBSPasswordStorage(unittest.TestCase):
    """OBS のパスワードは DPAPI で暗号化して保存する。平文は書かない"""

    PASSWORD = "hunter2-ｐａｓｓ"

    class FakeVar:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    # ── 暗号化そのもの（本物の DPAPI） ─────────────────
    def test_it_round_trips(self):
        stored = SecretStore.protect(self.PASSWORD)

        self.assertIsNotNone(stored)
        self.assertNotIn(self.PASSWORD, stored)
        self.assertNotIn(self.PASSWORD,
                         base64.b64decode(stored).decode("utf-8", "replace"))
        self.assertEqual(SecretStore.unprotect(stored), self.PASSWORD)

    def test_a_foreign_or_broken_value_is_none(self):
        """別のPC・別のユーザーの値は復号できない。壊れた値と同じく None"""
        for value in ("", "not base64!!", base64.b64encode(b"x" * 200).decode()):
            self.assertIsNone(SecretStore.unprotect(value), value)

    # ── 保存 ─────────────────────────────────
    def _save(self, password, stored=None):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_freeze_8pages", "v_freeze_punish", "v_emergency_key", "v_start_key", "v_big_key",
                     "v_obs_enabled", "v_obs_host", "v_obs_port"):
            setattr(app, name, self.FakeVar(""))
        app.v_obs_password = self.FakeVar(password)
        app.v_freeze_rounds = {}
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update),              patch.object(mainGUI, "load_settings", return_value=dict(stored or {})):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)
        return saved

    def test_the_saved_file_has_no_plaintext(self):
        saved = self._save(self.PASSWORD)

        self.assertNotIn(self.PASSWORD, json.dumps(saved, ensure_ascii=False))
        self.assertNotIn("obs_password", saved)
        self.assertEqual(SecretStore.unprotect(saved["obs_password_dpapi"]),
                         self.PASSWORD)

    def test_an_old_plaintext_key_is_dropped_on_save(self):
        saved = self._save(self.PASSWORD, stored={"obs_password": "old-plain"})

        self.assertNotIn("obs_password", saved)
        self.assertNotIn("old-plain", json.dumps(saved, ensure_ascii=False))

    def test_an_empty_password_is_stored_empty(self):
        self.assertEqual(self._save("")["obs_password_dpapi"], "")

    def test_a_failed_encryption_never_falls_back_to_plaintext(self):
        with patch.object(SecretStore, "protect", return_value=None):
            saved = self._save(self.PASSWORD)

        self.assertEqual(saved["obs_password_dpapi"], "")
        self.assertNotIn(self.PASSWORD, json.dumps(saved, ensure_ascii=False))

    # ── 読み込み ─────────────────────────────────
    def _load(self, data):
        """_load_saved_settings を回す（他の項目は既存テストと同じ偽物）"""
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        for name in ("v_desktop_mode", "v_use_osc",
                     "v_ton_entry", "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_instance_link", "v_emergency_key", "v_start_key", "v_big_key", "v_freeze_8pages",
                     "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, self.FakeVar(""))
        app.v_freeze_rounds = {}
        app._add_tool_row = lambda p, save=True: None
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._apply_obs_settings = lambda: None
        app._apply_saved_window_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app.logs = []
        app._log = app.logs.append
        written = []
        with patch.object(mainGUI, "load_settings", return_value=dict(data)),              patch.object(mainGUI, "save_settings", written.append):
            app._load_window_volume_settings = lambda _data: None
            app._load_fog_early_read_setting = lambda _data: None
            app._load_launch_options_setting = lambda _data: None
            mainGUI.App._load_saved_settings(app)
        return app, written

    def test_it_is_restored(self):
        app, written = self._load({"obs_password_dpapi": SecretStore.protect(self.PASSWORD)})

        self.assertEqual(app.v_obs_password.get(), self.PASSWORD)
        self.assertEqual(written, [], "書き直さない")
        self.assertEqual(app.logs, [])

    def test_an_old_plaintext_password_is_migrated(self):
        app, written = self._load({"obs_password": self.PASSWORD, "tnl_path": ""})

        self.assertEqual(app.v_obs_password.get(), self.PASSWORD)
        self.assertEqual(len(written), 1, "その場で書き直す")
        self.assertNotIn("obs_password", written[0])
        self.assertNotIn(self.PASSWORD, json.dumps(written[0], ensure_ascii=False))
        self.assertEqual(SecretStore.unprotect(written[0]["obs_password_dpapi"]),
                         self.PASSWORD)

    def test_an_undecryptable_password_is_empty_and_asked_for_once(self):
        foreign = base64.b64encode(b"from another PC" * 10).decode()

        app, written = self._load({"obs_password_dpapi": foreign})

        self.assertEqual(app.v_obs_password.get(), "")
        messages = [m for m in app.logs if "入れ直してください" in m]
        self.assertEqual(len(messages), 1, app.logs)
        self.assertEqual(written, [])

    def test_the_exe_build_includes_the_module(self):
        """Nuitka のビルド行は win32 系を明示している。漏れると exe でだけ落ちる"""
        self.assertIn("--include-module=win32crypt",
                      Path("main.py").read_text(encoding="utf-8"))




class TestSettingsPersistence(unittest.TestCase):
    """前回tnlパスの保存・復元"""

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            settings_path = Path(d) / "sub" / "settings.json"
            with patch.object(config, "SETTINGS_PATH", settings_path):
                mainGUI.save_settings({"tnl_path": "C:/list/my.tnl"})
                self.assertEqual(mainGUI.load_settings().get("tnl_path"), "C:/list/my.tnl")

    def test_load_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(config, "SETTINGS_PATH", Path(d) / "none.json"):
                self.assertEqual(mainGUI.load_settings(), {})

    def test_load_broken_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            broken = Path(d) / "settings.json"
            broken.write_text("{not json", encoding="utf-8")
            with patch.object(config, "SETTINGS_PATH", broken):
                self.assertEqual(mainGUI.load_settings(), {})




class TestAppLogLimit(unittest.TestCase):
    def test_append_log_text_keeps_recent_lines_only(self):
        class FakeLogText:
            def __init__(self):
                self.lines = []
                self.state = None
                self.seen = None

            def config(self, **kwargs):
                self.state = kwargs.get("state", self.state)

            def insert(self, index, line):
                self.lines.append(line)

            def delete(self, start, end):
                end_line = int(end.split(".", 1)[0])
                del self.lines[:end_line - 1]

            def see(self, index):
                self.seen = index

        app = type("FakeApp", (), {})()
        app.log_text = FakeLogText()
        app._log_line_count = 0

        with patch.object(config, "GUI_LOG_MAX_LINES", 3):
            for i in range(5):
                mainGUI.App._append_log_text(app, f"line {i}\n")

        self.assertEqual(app.log_text.lines, ["line 2\n", "line 3\n", "line 4\n"])
        self.assertEqual(app._log_line_count, 3)
        self.assertEqual(app.log_text.seen, "end")
        self.assertEqual(app.log_text.state, "disabled")


class TestApplyKeepOn(unittest.TestCase):
    """続行リストは差し替え（新しい dict の代入）。全窓が同じ SharedLists を持つので走行中にも効く。
    clear → update の入れ替えだと、その間に判定した窓が空のリストを見て自爆する"""

    def _app(self):
        app = type("FakeApp", (), {})()
        app.lists = MatchTNL.SharedLists({"Classic/クラシック": {1}})
        app._shared_lists = lambda: app.lists
        for name in ("keepOn_set", "host_wishes", "host_participants", "host_tabs"):
            setattr(type(app), name, getattr(mainGUI.App, name))
        return app

    def test_replaces_the_dict(self):
        app = self._app()
        before = app.keepOn_set

        mainGUI.App._apply_keep_on(app, {"Fog/霧": {7}})

        self.assertIsNot(app.keepOn_set, before, "新しい dict を代入すること")
        self.assertEqual(before, {"Classic/クラシック": {1}}, "読んでいる途中の dict は空にしない")
        self.assertEqual(app.keepOn_set, {"Fog/霧": {7}})

    def test_running_monitor_sees_the_new_list(self):
        app = self._app()
        monitor = LogMonitor.LogMonitor(WindowConfig(), None,
                                        lambda _m: None, window_idx=1, lists=app.lists)

        mainGUI.App._apply_keep_on(app, {"Fog/霧": {7}})

        self.assertEqual(monitor.keepOn_set, {"Fog/霧": {7}},
                         "走行中のモニタにも効くこと")




class TestProcessCheck(unittest.TestCase):
    """プロセスの生死。実プロセスには依存させない（kernel32 を差し替える）"""

    def _kernel32(self, names, snapshot=1234):
        """指定した名前のプロセスが並んでいる ToolHelp スナップショットを装う"""
        state = {"i": 0}
        k = MagicMock()
        k.CreateToolhelp32Snapshot.return_value = snapshot

        def fill(_snap, ref):
            entry = ref._obj
            if state["i"] >= len(names):
                return 0
            entry.szExeFile = names[state["i"]]
            state["i"] += 1
            return 1

        k.Process32FirstW.side_effect = fill
        k.Process32NextW.side_effect = fill
        return k

    def _running(self, k, name="ton_listtool.exe"):
        with patch.object(ProcessCheck, "kernel32", k):
            return ProcessCheck.is_process_running(name)

    def test_a_listed_process_is_found(self):
        k = self._kernel32(["explorer.exe", "ToN_ListTool.exe", "VRChat.exe"])

        self.assertTrue(self._running(k))

    def test_the_comparison_is_case_insensitive(self):
        """実物は ToN_ListTool.exe。小文字化して完全一致で見る"""
        k = self._kernel32(["TON_LISTTOOL.EXE"])

        self.assertTrue(self._running(k))

    def test_a_partial_name_does_not_match(self):
        k = self._kernel32(["ton_listtool_updater.exe", "ton-gui.exe"])

        self.assertFalse(self._running(k))

    def test_an_absent_process_is_not_found(self):
        k = self._kernel32(["explorer.exe", "VRChat.exe"])

        self.assertFalse(self._running(k))

    def test_a_failed_snapshot_is_false(self):
        """True に倒すと、呼び出し側が古いファイルを読む側へ倒れる"""
        k = self._kernel32([], snapshot=0)

        self.assertFalse(self._running(k))

    def test_an_invalid_handle_is_false(self):
        k = self._kernel32([], snapshot=ProcessCheck.INVALID_HANDLE_VALUE)

        self.assertFalse(self._running(k))

    def test_an_exception_is_false(self):
        k = MagicMock()
        k.CreateToolhelp32Snapshot.side_effect = OSError("boom")

        self.assertFalse(self._running(k))

    def test_an_empty_name_never_matches(self):
        k = self._kernel32(["explorer.exe"])

        self.assertFalse(self._running(k, name=""))
        k.CreateToolhelp32Snapshot.assert_not_called()

    def test_the_snapshot_is_closed(self):
        k = self._kernel32(["ToN_ListTool.exe"])

        self._running(k)

        k.CloseHandle.assert_called_once_with(1234)

    def test_the_snapshot_is_closed_even_on_error(self):
        k = self._kernel32(["ToN_ListTool.exe"])
        k.Process32FirstW.side_effect = OSError("boom")

        self.assertFalse(self._running(k))
        k.CloseHandle.assert_called_once_with(1234)

    # ── running_names（いくつもの名前を1回のスナップショットで）──
    def _names(self, k):
        with patch.object(ProcessCheck, "kernel32", k):
            return ProcessCheck.running_names()

    def test_all_names_come_from_one_snapshot(self):
        k = self._kernel32(["explorer.exe", "ToN_ListTool.exe", "VRChat.exe"])

        self.assertEqual(self._names(k),
                         frozenset({"explorer.exe", "ton_listtool.exe", "vrchat.exe"}))
        k.CreateToolhelp32Snapshot.assert_called_once()
        k.CloseHandle.assert_called_once_with(1234)

    def test_a_failed_snapshot_gives_none(self):
        """空（＝何も動いていない）と取り違えない"""
        k = self._kernel32([], snapshot=0)

        self.assertIsNone(self._names(k))

    def test_an_error_midway_gives_none_and_closes(self):
        k = self._kernel32(["ToN_ListTool.exe"])
        k.Process32FirstW.side_effect = OSError("boom")

        self.assertIsNone(self._names(k))
        k.CloseHandle.assert_called_once_with(1234)

    def test_the_tool_launcher_can_use_the_list(self):
        names = frozenset({"ton_listtool.exe"})
        with patch.object(ProcessCheck, "is_process_running") as single:
            self.assertTrue(ToolLauncher.is_running("D:/x/ToN_ListTool.exe", names))
            self.assertFalse(ToolLauncher.is_running("D:/x/SaveManager.exe", names))
        single.assert_not_called()




class TestUIFont(unittest.TestCase):
    """画面のフォントは、その PC に在るものから選ぶ。

    "Segoe UI" を名指しすると、配布した exe を別の PC で動かしたときに漢字が
    出ないことがある（実際に依頼者の配布先で起きた）。
    """

    class FakeRoot:
        def winfo_fpixels(self, _unit):
            return 144.0

        def winfo_screenwidth(self):
            return 1920

        def winfo_screenheight(self):
            return 1080

    def setUp(self):
        self.ui = UIFont.UI
        self.addCleanup(self._restore)

    def _restore(self):
        UIFont.UI = self.ui

    # ── 選び方 ────────────────────────────────
    def test_the_first_available_candidate_wins(self):
        for families, expected in (
                (["Yu Gothic UI", "Yu Gothic", "Meiryo UI"], "Yu Gothic UI"),
                (["Yu Gothic", "Meiryo UI", "ＭＳ ゴシック"], "Yu Gothic"),
                (["Arial", "Segoe UI", "Consolas", "ＭＳ ゴシック"], "既定")):
            self.assertEqual(
                UIFont.pick_font(UIFont.UI_CANDIDATES, families, "既定"), expected, families)

    def test_no_candidate_falls_back(self):
        self.assertEqual(UIFont.pick_font(UIFont.UI_CANDIDATES, [], "既定"), "既定")
        self.assertEqual(UIFont.pick_font(UIFont.UI_CANDIDATES, None, "既定"), "既定")

    def test_the_fallback_is_the_named_font(self):
        with patch.object(UIFont.tkfont, "families", return_value=["Arial"]), \
             patch.object(UIFont, "named_family", side_effect=lambda n: f"<{n}>"):
            self.assertEqual(UIFont.resolve(None), "<TkDefaultFont>")

    def test_the_log_uses_the_same_font(self):
        """等幅は分けない。ログで桁をそろえているのは時刻だけで、
        Yu Gothic UI は数字がどれも同じ幅なので揃う"""
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")

        self.assertNotIn("UIFont.MONO", src)
        self.assertFalse(hasattr(UIFont, "MONO_CANDIDATES"), "等幅の候補は持たない")
        with patch.object(UIFont.tkfont, "families",
                          return_value=["Yu Gothic UI", "ＭＳ ゴシック", "Consolas"]):
            self.assertEqual(UIFont.resolve(None), "Yu Gothic UI")

    def test_resolve_sets_the_global(self):
        with patch.object(UIFont.tkfont, "families",
                          return_value=["Yu Gothic", "Segoe UI"]):
            picked = UIFont.resolve(None)

        self.assertEqual(picked, "Yu Gothic")
        self.assertEqual(UIFont.UI, picked)

    # ── 名指しが残っていないこと ───────────────────
    def test_no_font_is_named_in_the_gui(self):
        """family を直書きしない。1か所でも名指しがあると、そこだけ字が出ない"""
        for module in (mainGUI, StatisticsGUI):
            src = Path(module.__file__).read_text(encoding="utf-8")
            families = re.findall(r"font=\(([^,)]+)", src)
            self.assertTrue(families, module.__name__)
            self.assertEqual(set(families), {"UIFont.UI"}, module.__name__)

    # ── 起動時のログ ────────────────────────────
    def test_the_startup_line_has_what_is_needed_to_tell(self):
        UIFont.UI = "Yu Gothic"

        line = UIFont.describe(self.FakeRoot())

        for part in ("Yu Gothic", "1920x1080", "150%", "144"):
            self.assertIn(part, line, line)

    def test_the_startup_line_survives_a_root_that_cannot_answer(self):
        line = UIFont.describe(object())

        self.assertIn(UIFont.UI, line)

    def test_the_app_resolves_and_logs_before_building(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        start = src.index("class App(tk.Tk):")
        init = src[start:src.index("    def _start_emergency_stop_polling", start)]
        self.assertLess(init.index("UIFont.resolve(self)"), init.index("self._build_ui()"),
                        "フォントを決めてから画面を作る")
        self.assertIn('self._log(f"[画面] {UIFont.describe(self)}")', init)




class TestLaunchWidgetsStillReachable(unittest.TestCase):
    """折りたたみの親を変えても、他のメソッドが触る属性が残っていること"""

    NAMES = ("btn_launch", "btn_stop_entry", "lbl_launch", "lbl_win_warn")

    def setUp(self):
        self.source = Path("mainGUI.py").read_text(encoding="utf-8")

    def test_every_widget_is_still_assigned(self):
        for name in self.NAMES:
            self.assertIn(f"self.{name} = ", self.source, name)

    def test_they_are_still_configured_elsewhere(self):
        """config(state=...) される側なので、参照が消えていないこと"""
        for name in self.NAMES:
            uses = self.source.count(f"self.{name}")
            self.assertGreater(uses, 1, f"{name} が代入だけになっている")

    def test_the_launch_button_is_outside_the_collapsible(self):
        """畳んでも「🚀 VRChatを起動」が見えていること"""
        self.assertIn("self.btn_launch = ttk.Button(lf1", self.source)
        self.assertIn("lf1 = ttk.Frame(f2)", self.source)
        self.assertIn('CollapsibleFrame(f2, text="VRChat起動の詳細設定"',
                      self.source)

    def test_the_details_are_inside_the_collapsible(self):
        for frame in ("lf22", "lf25"):
            self.assertIn(f"{frame} = ttk.Frame(f2_launch)", self.source, frame)

    def test_the_window_count_warning_is_outside(self):
        """※ 窓数はマクロ起動前に… は窓数の話なので畳まない"""
        self.assertIn("self.lbl_win_warn = ttk.Label(\n            f2, ", self.source)




class TestWindowCountIsRemembered(unittest.TestCase):
    """窓数を保存する。起動時は 開いている窓の数 → 保存値 → 4 の順で決める。

    保存するのは手で変えた値だけ。自動検出の値を保存すると、3窓だけ開いていた
    日の次に、VRChat を開かずに起動したとき3が出てしまう。
    """

    class FakeVar:
        def __init__(self, value=0):
            self._v = value

        def get(self):
            return self._v

        def set(self, value):
            self._v = value

    def _app(self, pref=None, tabs=4):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.v_win_count = self.FakeVar(tabs)
        app.tabs = [object()] * tabs
        app._win_count_pref = pref
        app._running = False
        app.logs = []
        app._log = app.logs.append
        app.rebuilt = []

        def rebuild(n):
            app.rebuilt.append(n)
            app.tabs = [object()] * n

        app._rebuild_tabs = rebuild
        app._sync_launch_count = lambda: None
        app._release_suicide_keys = lambda hs: list(hs)
        app._assign_windows_and_logs = lambda ws: None
        app.saves = []
        app._schedule_settings_save = lambda: app.saves.append(1)
        return app

    @staticmethod
    def _detect(app, windows):
        with patch.object(mainGUI.VRChatDiscovery,
                          "get_vrchat_windows_by_start_time", return_value=windows):
            mainGUI.App._auto_detect_windows(app)

    # ── 1〜3. 起動時の決め方 ───────────────────
    def test_open_windows_win_over_the_saved_value(self):
        app = self._app(pref=6)

        self._detect(app, [(1, 0.0), (2, 0.0), (3, 0.0)])

        self.assertEqual(app.v_win_count.get(), 3, "開いている数を使う")
        self.assertEqual(app._win_count_pref, 6, "保存値は変えない")

    def test_no_open_windows_uses_the_saved_value(self):
        app = self._app(pref=6)

        self._detect(app, [])

        self.assertEqual(app.v_win_count.get(), 6)
        self.assertEqual(app.rebuilt, [6], "タブも作り直す")
        self.assertTrue(any("前回の窓数6" in m for m in app.logs), app.logs)

    def test_no_saved_value_stays_at_four(self):
        app = self._app(pref=None)

        self._detect(app, [])

        self.assertEqual(app.v_win_count.get(), 4)
        self.assertEqual(app.rebuilt, [])
        self.assertTrue(any("手動で設定" in m for m in app.logs), app.logs)

    def test_the_default_is_four(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")

        self.assertIn("self.v_win_count = tk.IntVar(value=4)", src)

    # ── 4〜5. 何を保存するか ─────────────────────
    def test_an_auto_detected_count_is_not_remembered(self):
        app = self._app(pref=None)

        self._detect(app, [(1, 0.0), (2, 0.0), (3, 0.0)])

        self.assertIsNone(app._win_count_pref)

    def test_a_manual_change_is_remembered_and_saved(self):
        app = self._app(pref=None, tabs=4)
        app.v_win_count.set(6)

        mainGUI.App._on_win_count_change(app)

        self.assertEqual(app._win_count_pref, 6)
        self.assertEqual(app.saves, [1], "変えた直後にも保存する")

    def test_leaving_the_spinbox_without_a_change_remembers_nothing(self):
        """フォーカスが外れただけでも呼ばれる。自動検出の値を覚えないこと"""
        app = self._app(pref=6, tabs=3)
        app.v_win_count.set(3)                   # 自動検出で入った値のまま

        mainGUI.App._on_win_count_change(app)

        self.assertEqual(app._win_count_pref, 6)
        self.assertEqual(app.saves, [])

    def test_the_value_is_clamped_before_it_is_remembered(self):
        app = self._app(pref=None, tabs=4)
        app.v_win_count.set(99)

        mainGUI.App._on_win_count_change(app)

        self.assertEqual(app._win_count_pref, config.MAX_WINDOWS)

    def test_nothing_changes_while_running(self):
        app = self._app(pref=None, tabs=4)
        app._running = True
        app.v_win_count.set(6)

        mainGUI.App._on_win_count_change(app)

        self.assertIsNone(app._win_count_pref)

    def test_the_count_is_written_to_the_file(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_freeze_8pages",
                     "v_freeze_punish", "v_emergency_key", "v_start_key", "v_big_key",
                     "v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, self.FakeVar(""))
        app.v_freeze_rounds = {}
        app._win_count_pref = 6
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["win_count"], 6)

    # ── 6〜7. 読み込みの検証 ────────────────────
    def test_a_valid_value_is_read(self):
        for n in (1, 4, config.MAX_WINDOWS):
            self.assertEqual(mainGUI._valid_win_count(n), n, n)

    def test_broken_values_are_ignored(self):
        for value in ("6", "abc", 0, -1, config.MAX_WINDOWS + 1, 2.5, None,
                      True, False, [], {}):
            self.assertIsNone(mainGUI._valid_win_count(value), repr(value))

    def test_loading_a_broken_value_does_not_stop_the_start(self):
        app = self._load({"win_count": "abc"})

        self.assertIsNone(app._win_count_pref)

    def test_an_old_settings_file_without_the_key_is_fine(self):
        app = self._load({"tnl_path": "C:/list/my.tnl"})

        self.assertIsNone(app._win_count_pref)

    def test_loading_a_good_value_only_notes_it(self):
        """読み込みでは控えるだけ。窓数への反映は自動検出の後"""
        app = self._load({"win_count": 6})

        self.assertEqual(app._win_count_pref, 6)
        self.assertEqual(app.v_win_count.get(), "", "ここでは窓数を変えない")

    def _load(self, data):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_instance_link",
                     "v_emergency_key", "v_start_key", "v_big_key", "v_freeze_8pages",
                     "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password", "v_win_count"):
            setattr(app, name, self.FakeVar(""))
        app.v_freeze_rounds = {}
        app._add_tool_row = lambda p, save=True: None
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._apply_obs_settings = lambda: None
        app._apply_saved_window_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app.logs = []
        app._log = app.logs.append
        with patch.object(mainGUI, "load_settings", return_value=dict(data)), \
             patch.object(mainGUI, "save_settings", lambda _d: None):
            app._load_window_volume_settings = lambda _data: None
            app._load_fog_early_read_setting = lambda _data: None
            app._load_launch_options_setting = lambda _data: None
            mainGUI.App._load_saved_settings(app)
        return app

    def test_load_comes_before_detection(self):
        """読み込みで控えた値を、自動検出の「未検出」で使う順番であること"""
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")

        self.assertLess(src.index("        self._load_saved_settings()\n"),
                        src.index("        self._auto_detect_windows()\n"))




class TestWindowVolumeInTheApp(unittest.TestCase):
    """開始で見張りを作り、停止の頭（監視を止める前）で止めて元に戻す"""

    def test_start_watches_every_monitor(self):
        app = type("FakeApp", (), {})()
        m1, m2 = MagicMock(window_idx=1), MagicMock(window_idx=2)
        m1.cfg.hwnd, m2.cfg.hwnd = 0xA, 0xB
        app.monitors = [m1, m2]
        app._log = lambda _m: None
        app._apply_window_volume_settings = MagicMock()
        with patch.object(WindowVolume, "VolumeController") as ctl:
            mainGUI.App._start_window_volume(app)
        windows = ctl.call_args.args[0]()
        self.assertEqual([(i, h) for i, h, _st in windows], [(1, 0xA), (2, 0xB)])
        app._apply_window_volume_settings.assert_called_once()
        ctl.return_value.start.assert_called_once()

    def test_start_begins_watching_after_the_monitors_exist(self):
        """_start は重いので、呼ぶ場所をソースで見る: 監視を作り終え、窓が無ければ
        抜けた後（見張る窓が決まってから）"""
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _start(self):"):src.index("    def _stop(self):")]
        self.assertIn("self._start_window_volume()", body)
        self.assertLess(body.index("if not self.monitors:"),
                        body.index("self._start_window_volume()"))

    def test_stop_restores_before_the_monitors_stop(self):
        order = []
        app = TestSuicideKeysReleasedByTheApp._app(self)
        controller = MagicMock()
        controller.stop.side_effect = lambda: order.append("volume")
        app._window_volume = controller
        app._stop_window_volume = lambda: mainGUI.App._stop_window_volume(app)
        for m in app.monitors:
            m.stop.side_effect = lambda: order.append("monitor")
        with patch.object(mainGUI.WindowOperator, "release_key_background", return_value=True):
            mainGUI.App._stop(app)
        self.assertEqual(order[0], "volume")
        self.assertIsNone(app._window_volume)




class TestSettingsLive(unittest.TestCase):
    """霧看破のボタンの保存・表示の名前・窓ごとの設定を動作中も反映"""

    class Var:
        def __init__(self, value=False):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def setUp(self):
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)

    # ── 霧看破のボタンの保存と読み込み ─────────────────
    def _fog_app(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.v_fog_early_read = self.Var(False)
        return app

    def test_the_button_is_loaded_and_saved(self):
        for data, expected in (({}, False), ({"fog_early_read_enabled": True}, True),
                               ({"fog_early_read_enabled": "yes"}, False),
                               ({"fog_early_read_enabled": False}, False)):
            app = self._fog_app()
            mainGUI.App._load_fog_early_read_setting(app, data)
            self.assertEqual(app.v_fog_early_read.get(), expected, data)
            self.assertEqual(FogEarlyRead.early_read_enabled(), expected, data)
            self.assertEqual(mainGUI.App._fog_early_read_setting(app),
                             {"fog_early_read_enabled": expected})

    def test_the_default_is_off(self):
        """起動したての値（ほかのテストが触る前）を、新しいプロセスで見る"""
        out = subprocess.run([sys.executable, "-c", "import FogEarlyRead as F; "
                              "print(F.early_read_enabled(), F.early_read_allowed('invite'))"],
                             cwd=str(Path(FogEarlyRead.__file__).parent), capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "False False", out.stderr)

    def test_changing_the_button_applies_and_saves(self):
        app = self._fog_app()
        app.v_fog_early_read.set(True)
        app._schedule_settings_save = MagicMock()
        mainGUI.App._on_fog_early_read_changed(app)
        self.assertTrue(FogEarlyRead.early_read_allowed("friends"))
        self.assertFalse(FogEarlyRead.early_read_allowed("friends_plus"))
        app._schedule_settings_save.assert_called_once()

    def test_the_save_includes_the_key(self):
        stored = {}
        fake = _with_cancel_key(type("FakeApp", (), {})())
        fake.tabs, fake.tool_rows, fake._win_count_pref = [], [], None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_freeze_8pages", "v_freeze_punish",
                     "v_emergency_key", "v_start_key", "v_big_key", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(fake, name, self.Var(""))
        fake.v_freeze_rounds = {}
        fake._window_volume_settings = lambda: {}
        fake.v_fog_early_read = self.Var(True)
        fake._fog_early_read_setting = lambda: mainGUI.App._fog_early_read_setting(fake)
        fake._launch_options_setting = lambda: {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(fake)
        self.assertIs(stored["fog_early_read_enabled"], True)

    # ── 表示の名前 ─────────────────────────────
    def test_the_two_renamed_labels(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('text="ラウンド突入でフリーズ:"', src)
        self.assertIn('text="ラウンドごとの自爆設定（プライベートインスタンスのみ）"', src)
        self.assertNotIn("突入で全窓停止", src)
        self.assertNotIn("ラウンドごとの扱い", src)
        self.assertIn('text="霧を即時判定する（Friends・Invite+・Invite のみ）"', src)

    # ── 窓ごとの設定を動作中も反映 ─────────────────────
    def _live_app(self, running=True):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app._running = running
        self.cfgs = [WindowConfig(auto_begin=True, do_skip=True, cancel_afk=True,
                                  announce_intermission=False) for _ in range(2)]
        app.monitors = []
        for idx, cfg in enumerate(self.cfgs):
            m = MagicMock()
            m.window_idx = idx + 1
            m.cfg = cfg
            app.monitors.append(m)
        return app

    def _tab(self, app):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        return mainGUI.WindowTab(root, 1, on_settings_changed=lambda t: mainGUI.App._apply_tab_settings_live(app, t))

    def test_each_kind_reaches_only_that_windows_monitor(self):
        app = self._live_app()
        tab = self._tab(app)                          # 窓2（idx 1）
        before = copy.deepcopy(self.cfgs[0])

        tab.v_auto_begin.set(False)
        tab.v_do_skip.set(False)
        tab.v_cancel_afk.set(False)
        tab.v_announce_intermission.set(True)
        tab.v_skip_rounds["Classic"].set(True)
        tab.v_continue_rounds["Fog"].set(True)

        cfg = self.cfgs[1]
        self.assertEqual((cfg.auto_begin, cfg.do_skip, cfg.cancel_afk, cfg.announce_intermission),
                         (False, False, False, True))
        self.assertEqual(cfg.skip_rounds, {"Classic"})
        self.assertEqual(cfg.continue_rounds, {"Fog"})
        self.assertEqual(self.cfgs[0], before, "ほかの窓は変わらない")

    def test_clearing_the_round_checks_reaches_the_monitor(self):
        """入室で GUI のチェックが外れる → 監視へも空が渡る（食い違わない）"""
        app = self._live_app()
        tab = self._tab(app)
        tab.v_skip_rounds["Classic"].set(True)
        tab.v_skip_rounds["Classic"].set(False)
        self.assertEqual(self.cfgs[1].skip_rounds, set())

    def test_nothing_happens_while_stopped(self):
        app = self._live_app(running=False)
        tab = self._tab(app)
        tab.v_do_skip.set(False)
        self.assertTrue(self.cfgs[1].do_skip, "次の開始で写る")

    def test_the_tabs_are_built_with_the_hook(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn("on_settings_changed=self._apply_tab_settings_live", src)




class TestGuiTweaks(unittest.TestCase):
    """画面の調整（本物の App を1つ作って見る。見た目そのものは依頼者が実機で確かめる）"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.save = patch.object(self.app, "_schedule_settings_save")
        self.saved = self.save.start()
        self.addCleanup(self.save.stop)
        for var in (self.app.v_freeze_8pages, self.app.v_freeze_punish,
                    *self.app.v_freeze_rounds.values()):
            var.set(False)
        self.app._apply_freeze_settings()
        self.addCleanup(SharedState.set_freeze_rounds, [])
        self.addCleanup(SharedState.set_freeze_on_8pages, False)
        self.addCleanup(SharedState.set_freeze_on_punish, False)

    # ── 1・2. 名前 ───────────────────────────
    def test_the_renamed_labels(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('text="③ サウンド設定"', src)
        self.assertNotIn("音声アナウンス設定", src)
        self.assertIn('text="霧を即時判定する（Friends・Invite+・Invite のみ）"', src)
        self.assertNotIn("霧看破を使う", src)

    # ── 3. 速度検知でフリーズ → 突入でフリーズも入れる ─────────────
    def _speed_check(self, text):
        for row in self.app.winfo_children():
            for child in row.winfo_children():
                if isinstance(child, ttk.Checkbutton) and child.cget("text") == text:
                    return child
        self.fail(text)

    def test_checking_a_speed_freeze_checks_the_same_round_entry_freeze(self):
        for text, name, other in (("8 Pages検知でフリーズ", "8 Pages", "Punished"),
                                  ("Punished検知でフリーズ", "Punished", "8 Pages")):
            for var in self.app.v_freeze_rounds.values():
                var.set(False)
            self.saved.reset_mock()
            self._speed_check(text).invoke()                 # 入れる

            self.assertTrue(self.app.v_freeze_rounds[name].get(), name)
            self.assertFalse(self.app.v_freeze_rounds[other].get(), "もう一方は変えない")
            self.assertIn(name, SharedState.get_freeze_rounds(), "全窓の状態へ反映")
            self.saved.assert_called()

            self._speed_check(text).invoke()                 # 外す
            self.assertTrue(self.app.v_freeze_rounds[name].get(), "外しても突入側はそのまま")

    def test_unchecking_does_not_check_the_round_entry_side(self):
        self.app.v_freeze_8pages.set(True)          # 読み込みなどで入っていた
        self._speed_check("8 Pages検知でフリーズ").invoke()      # 外す
        self.assertFalse(self.app.v_freeze_8pages.get())
        self.assertFalse(self.app.v_freeze_rounds["8 Pages"].get(), "外すときは連動しない")

    def test_the_round_entry_side_does_not_touch_the_speed_side(self):
        self.app.v_freeze_rounds["8 Pages"].set(True)
        self.app._apply_freeze_settings()
        self.assertFalse(self.app.v_freeze_8pages.get())

    def test_loading_does_not_link(self):
        data = {"freeze_8pages": True, "freeze_punish": True, "freeze_rounds": []}
        with patch.object(mainGUI, "load_settings", return_value=data), \
             patch.object(self.app, "_load_tnl", lambda show_error=True: None), \
             patch.object(self.app, "_add_tool_row", lambda p, save=True: None):
            self.app._load_saved_settings()
        self.assertTrue(self.app.v_freeze_8pages.get())
        self.assertFalse(self.app.v_freeze_rounds["8 Pages"].get())
        self.assertFalse(self.app.v_freeze_rounds["Punished"].get())

    def test_the_launch_options_are_loaded_at_start(self):
        self.addCleanup(self.app.v_launch_options.set, "")
        with patch.object(mainGUI, "load_settings", return_value={"launch_extra_options": "--x"}),              patch.object(self.app, "_load_tnl", lambda show_error=True: None),              patch.object(self.app, "_add_tool_row", lambda p, save=True: None):
            self.app._load_saved_settings()
        self.assertEqual(self.app.v_launch_options.get(), "--x")

    # ── 4. 並び ─────────────────────────────
    def test_eight_pages_comes_before_punished(self):
        rounds = list(config.ROUND_FREEZE_SELECTABLE)
        self.assertLess(rounds.index("8 Pages"), rounds.index("Punished"))
        self.assertEqual(list(self.app.v_freeze_rounds), rounds)

    # ── 5. インスタンスタイプ ───────────────────────
    def test_four_instance_types_in_this_order(self):
        self.assertEqual(config.TON_INSTANCE_ACCESS_CHOICES,
                         ("invite", "invite_plus", "friends", "friends_plus"))
        self.assertEqual(config.TON_INSTANCE_ACCESS_DEFAULT, "invite_plus")
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        order = [src.index(f'ttk.Radiobutton(lf22, text="{label}"')
                 for label in ("インバイト", "インバイト+", "フレンド", "フレンド+")]
        self.assertEqual(order, sorted(order))

    # ── 6. 起動オプション ────────────────────────
    def test_the_launch_options_are_saved_and_restored(self):
        self.app.v_launch_options.set("--foo --bar")
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        self.assertEqual(saved["launch_extra_options"], "--foo --bar")
        for data, expected in (({"launch_extra_options": "--x"}, "--x"), ({}, ""),
                               ({"launch_extra_options": 3}, ""),
                               ({"launch_extra_options": ["--x"]}, ""),
                               ({"launch_extra_options": None}, "")):
            self.app.v_launch_options.set("前の値")
            self.app._load_launch_options_setting(data)
            self.assertEqual(self.app.v_launch_options.get(), expected, data)

    def test_the_launch_options_start_empty(self):
        self.assertEqual(mainGUI.App._launch_options_setting(
            type("A", (), {"v_launch_options": tk.StringVar(self.app, value="")})()),
            {"launch_extra_options": ""})

    # ── 7. 窓タブの折りたたみ ──────────────────────
    def _state(self, key):
        return [tab.sections[key].collapsed for tab in self.app.tabs]

    def _shown(self, key):
        return [tab.sections[key].content.winfo_manager() == "pack" for tab in self.app.tabs]

    def test_opening_one_opens_all_tabs_and_only_that_section(self):
        count = len(self.app.tabs)
        self.addCleanup(self.app._rebuild_tabs, count)
        self.app._rebuild_tabs(3)
        self.assertEqual(self._state("rounds"), [True] * 3, "既定は閉じる")

        self.app.tabs[1].sections["rounds"]._toggle()

        self.assertEqual(self._state("rounds"), [False] * 3)
        self.assertEqual(self._shown("rounds"), [True] * 3)
        self.assertEqual(self._state("assign"), [True] * 3, "もう一方の枠は変わらない")

        self.app.tabs[2].sections["rounds"]._toggle()
        self.assertEqual(self._state("rounds"), [True] * 3)
        self.assertEqual(self._shown("rounds"), [False] * 3)

    def test_rebuilt_tabs_keep_the_state(self):
        count = len(self.app.tabs)
        self.addCleanup(self.app._rebuild_tabs, count)
        self.app._rebuild_tabs(2)
        self.addCleanup(self.app._tab_sections.update, {"assign": True, "rounds": True})
        self.app.tabs[0].sections["assign"]._toggle()
        self.assertEqual(self._state("assign"), [False] * 2)
        self.assertEqual(self._state("rounds"), [True] * 2, "もう一方の枠は変わらない")

        self.app._rebuild_tabs(4)

        self.assertEqual(self._state("assign"), [False] * 4)
        self.assertEqual(self._shown("assign"), [True] * 4)
        self.assertEqual(self._state("rounds"), [True] * 4)

    def test_changing_the_window_count_keeps_each_windows_settings(self):
        count = len(self.app.tabs)
        self.addCleanup(self.app._rebuild_tabs, count)
        self.addCleanup(setattr, self.app, "_tab_memory", {})
        self.app._tab_memory = {}
        self.app._rebuild_tabs(2)
        second = self.app.tabs[1]
        second.v_do_skip.set(False)
        second.v_auto_begin.set(False)
        second.v_cancel_afk_after_unlock.set(True)
        second.v_skip_rounds["Cracked"].set(True)
        second.v_log.set("C:/logs/output_log_2.txt")
        second.set_hwnd_choices([0x2222], selected_hwnd=0x2222)

        self.app._rebuild_tabs(3)
        self.app._rebuild_tabs(1)
        self.app._rebuild_tabs(2)

        tab = self.app.tabs[1]
        self.assertFalse(tab.v_do_skip.get())
        self.assertFalse(tab.v_auto_begin.get())
        self.assertTrue(tab.v_cancel_afk_after_unlock.get())
        self.assertEqual(tab.live_settings()["skip_rounds"], {"Cracked"})
        self.assertEqual(tab.v_log.get(), "C:/logs/output_log_2.txt")
        self.assertEqual(tab._get_selected_hwnd(), 0x2222)
        self.assertTrue(self.app.tabs[0].v_do_skip.get(), "触っていない窓は既定のまま")

    def test_other_collapsibles_are_not_linked(self):
        frame = mainGUI.CollapsibleFrame(self.app, text="x", collapsed=True)
        self.addCleanup(frame.destroy)
        frame._toggle()
        self.assertFalse(frame.collapsed)
        self.assertEqual(self._state("assign"), [True] * len(self.app.tabs))

    # ── 8. 緊急停止のキーの行 ───────────────────────
    def test_the_emergency_key_moved_to_the_start_key_row(self):
        row = self.app.lbl_start_key.master
        self.assertEqual(row.pack_slaves()[:5],          # マクロ開始が先・緊急停止が後
                         [self.app.lbl_start_key, self.app.btn_capture_start_key,
                          self.app.btn_clear_start_key, self.app.lbl_emergency, self.app.btn_capture_key])
        self.assertNotIn(self.app.lbl_emergency, self.app.btn_start.master.pack_slaves())




class TestSuicideKeysReleasedByTheApp(unittest.TestCase):
    """停止・終了・起動のときに自爆キーを離す"""

    def setUp(self):
        SharedState.set_suicide_key("^")

    def _app(self, hwnds=(0xA, 0xB)):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.order = []
        app.monitors = []
        for h in hwnds:
            m = MagicMock()
            m.cfg.hwnd = h
            m.stop.side_effect = lambda h=h: app.order.append(("stop", h))
            app.monitors.append(m)
        app._entry_stop = threading.Event()
        app.btn_start = MagicMock()
        app.btn_stop = MagicMock()
        app.lbl_win_warn = MagicMock()
        app.logs = []
        app._log = app.logs.append
        app._release_suicide_keys = \
            lambda hs: mainGUI.App._release_suicide_keys(app, hs)
        app._show_own_windows_again = lambda: None
        return app

    def _released(self, app):
        return patch.object(
            mainGUI.WindowOperator, "release_key_background",
            side_effect=lambda h, k: app.order.append(("release", h, k)) or True)

    def test_stopping_releases_every_watched_window(self):
        app = self._app()

        with self._released(app):
            app._stop_window_volume = lambda: None
            mainGUI.App._stop(app)

        self.assertIn(("release", 0xA, "^"), app.order)
        self.assertIn(("release", 0xB, "^"), app.order)

    def test_it_releases_after_the_monitors_stop(self):
        """止める前に離すと、止まるまでの間にまた押されうる"""
        app = self._app()

        with self._released(app):
            app._stop_window_volume = lambda: None
            mainGUI.App._stop(app)

        kinds = [e[0] for e in app.order]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "stop"),
                        min(i for i, k in enumerate(kinds) if k == "release"))

    def test_closing_releases_before_destroying(self):
        app = self._app()
        app._save_settings_now = lambda: None
        app._stop = lambda: mainGUI.App._stop(app)
        app.destroy = lambda: app.order.append(("destroy",))

        with self._released(app):
            app._stop_window_volume = lambda: None
            app._unhook_chase_keys = lambda: None
            app._unhook_stop_start_keys = lambda: None
            mainGUI.App._on_close(app)

        kinds = [e[0] for e in app.order]
        self.assertIn("release", kinds)
        self.assertLess(kinds.index("release"), kinds.index("destroy"))

    def test_startup_releases_every_vrchat_window(self):
        """前回このツールが長押しの最中に落ちていた場合の回収"""
        app = self._app(hwnds=())
        app.v_win_count = MagicMock()
        app.tabs = [object(), object()]
        app._rebuild_tabs = MagicMock()
        app._assign_windows_and_logs = MagicMock()

        with self._released(app), \
             patch.object(mainGUI.VRChatDiscovery,
                          "get_vrchat_windows_by_start_time",
                          return_value=[(0x11, 1.0), (0x22, 2.0)]):
            mainGUI.App._auto_detect_windows(app)

        self.assertEqual([e[1] for e in app.order if e[0] == "release"],
                         [0x11, 0x22])

    def test_a_failure_does_not_stop_the_stop(self):
        app = self._app()

        with patch.object(mainGUI.WindowOperator, "release_key_background",
                          side_effect=OSError("gone")):
            app._stop_window_volume = lambda: None
            mainGUI.App._stop(app)          # 落ちないこと

        self.assertFalse(app._running)
        self.assertTrue(any("離せませんでした" in m for m in app.logs), app.logs)

    def test_an_unset_window_is_skipped(self):
        app = self._app(hwnds=(0, 0xA))

        with self._released(app):
            app._stop_window_volume = lambda: None
            mainGUI.App._stop(app)

        self.assertEqual([e[1] for e in app.order if e[0] == "release"], [0xA])




class TestLaunchWindowCount(unittest.TestCase):
    """起動する窓数（既定は「窓数 − 起動済みの窓数」）"""

    class FakeVar:
        def __init__(self, value=0):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeTab:
        def __init__(self, idx, profile):
            self.idx = idx
            self.v_profile = TestLaunchWindowCount.FakeVar(profile)

    def test_count_is_win_count_minus_already_open(self):
        self.assertEqual(mainGUI.launch_window_count(4, 2), 2)
        self.assertEqual(mainGUI.launch_window_count(4, 0), 4)

    def test_count_never_goes_negative(self):
        """必要数より多く開いていても0で止める"""
        self.assertEqual(mainGUI.launch_window_count(2, 5), 0)
        self.assertEqual(mainGUI.launch_window_count(4, 4), 0)

    def test_launch_targets_are_taken_from_the_back(self):
        """既存の窓は先頭タブに割り当てられるので、起動するのは後ろのタブ"""
        tabs = [self.FakeTab(i, i + 1) for i in range(4)]

        picked = mainGUI.tabs_to_launch(tabs, 2)

        self.assertEqual([t.idx for t in picked], [2, 3])
        self.assertEqual(mainGUI.tabs_to_launch(tabs, 0), [])
        self.assertEqual([t.idx for t in mainGUI.tabs_to_launch(tabs, 9)], [0, 1, 2, 3])

    def test_plan_uses_tab_index_for_osc_and_profile(self):
        """OSC割当は監視開始時と同じタブ番号を使う（ポートがずれないように）"""
        tabs = [self.FakeTab(i, 10 + i) for i in range(4)]

        plan = mainGUI.build_launch_plan(mainGUI.tabs_to_launch(tabs, 2))

        self.assertEqual(plan, [(3, 12, 2), (4, 13, 3)])

    def test_sync_uses_detected_window_count(self):
        app = type("FakeApp", (), {})()
        app._running = False
        app.v_win_count = self.FakeVar(4)
        app.v_launch_count = self.FakeVar(0)

        with patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time",
                          return_value=[(1, None), (2, None)]):
            mainGUI.App._sync_launch_count(app)

        self.assertEqual(app.v_launch_count.get(), 2)

    def test_sync_is_skipped_while_running(self):
        """マクロ動作中は窓数を触らせないので既定値も更新しない"""
        app = type("FakeApp", (), {})()
        app._running = True
        app.v_win_count = self.FakeVar(4)
        app.v_launch_count = self.FakeVar(7)

        mainGUI.App._sync_launch_count(app)

        self.assertEqual(app.v_launch_count.get(), 7)

    def test_manual_value_is_clamped_to_the_tab_count(self):
        app = type("FakeApp", (), {})()
        app.v_launch_count = self.FakeVar(9)

        self.assertEqual(mainGUI.App._launch_count_value(app, 4), 4)
        self.assertEqual(app.v_launch_count.get(), 4, "丸めた値をGUIにも戻すこと")

        app.v_launch_count = self.FakeVar(-3)
        self.assertEqual(mainGUI.App._launch_count_value(app, 4), 0)




class TestStartWithoutTnl(unittest.TestCase):
    """tnl未読み込みでも開始できる（続行リストは0件として扱う）"""

    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _app(self, keep_on):
        app = type("FakeApp", (), {})()
        app.keepOn_set = keep_on
        app.tabs = []
        app.monitors = []
        app.logs = []
        app._log = app.logs.append
        app._running = False
        app.btn_start = MagicMock()
        app.btn_stop = MagicMock()
        app.lbl_win_warn = MagicMock()
        for name in ("v_voice_continue", "v_voice_fog", "v_voice_item_lost",
                     "v_voice_intermission", "v_voice_foxy"):
            setattr(app, name, self.FakeVar(""))
        app._dropped_logs = None
        for name in ("_resolve_tab_ports", "_resolve_windows", "_live_candidates"):
            setattr(app, name, getattr(mainGUI.App, name).__get__(app))
        app._dropped_log_lines = mainGUI.App._dropped_log_lines
        app._apply_obs_settings = lambda: None
        app._assign_source = mainGUI.App._assign_source
        return app

    def test_start_is_not_blocked_without_tnl(self):
        """警告で止めず、そのまま窓の準備へ進む"""
        app = self._app({})

        with patch.object(mainGUI.messagebox, "showwarning") as mock_warn, \
             patch.object(mainGUI.messagebox, "showerror") as mock_error, \
             patch.object(VRChatDiscovery, "find_latest_logs", return_value=[]), \
             patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time",
                          return_value=[]), \
             patch.object(mainGUI.OSCClient, "udp_ports_by_pid", return_value=None):
            mainGUI.App._start(app)

        mock_warn.assert_not_called()
        # 窓が無いので最後は「有効な窓/ログが見つかりません」で止まる＝tnlでは止まっていない
        mock_error.assert_called_once()
        self.assertTrue(any("tnl未読み込み" in m for m in app.logs))

    def test_empty_keep_on_set_never_continues(self):
        """tnlが空なら、どのテラーでも続行にならない"""
        for round_type in ("Classic", "Alternate", "Midnight", "Double Trouble"):
            decision = RoundDecision.decide_killers(
                {}, [42, 7, 3], round_type, wins=0, cancel_afk=False)
            self.assertFalse(decision.is_continue_round, round_type)

    def test_tnl_entries_still_continue(self):
        """読み込んだ場合は従来どおり続行する（止めすぎていないことの確認）"""
        decision = RoundDecision.decide_killers(
            {"Classic/クラシック": {42}}, [42], "Classic", wins=0, cancel_afk=False)

        self.assertTrue(decision.is_continue_round)




class TestLegacySettings(unittest.TestCase):
    """「干し芋自動自爆」チェックを消した後の古い settings.json"""

    LEGACY = {
        "tnl_path": "C:/list/my.tnl",
        "hoshiimo_skip": True,          # 消したキー
        "profiles": [1, 2],
        "freeze_8pages": True,
    }

    def test_a_legacy_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(self.LEGACY), encoding="utf-8")

            with patch.object(config, "SETTINGS_PATH", path):
                data = mainGUI.load_settings()

            self.assertEqual(data.get("tnl_path"), "C:/list/my.tnl")
            self.assertTrue(data.get("hoshiimo_skip"), "読めること自体は変わらない")

    def test_saving_drops_the_removed_key_without_failing(self):
        """保存時に書き直されるだけ。落ちない"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(self.LEGACY), encoding="utf-8")

            with patch.object(config, "SETTINGS_PATH", path):
                mainGUI.save_settings({**mainGUI.load_settings(),
                                       "tnl_path": "C:/list/other.tnl"})
                data = mainGUI.load_settings()

            self.assertEqual(data.get("tnl_path"), "C:/list/other.tnl")

    def test_window_config_has_no_hoshiimo_switch(self):
        self.assertFalse(hasattr(WindowConfig(), "hoshiimo_skip"))
        with self.assertRaises(TypeError):
            WindowConfig(hoshiimo_skip=True)




class TestHotKey(unittest.TestCase):
    """緊急停止キーの検証・表記・捕捉"""

    @classmethod
    def setUpClass(cls):
        cls.real = _real_keyboard()

    def setUp(self):
        if self.real is None:
            self.skipTest("keyboard が入っていない")
        self._kb = patch.object(HotKey, "keyboard", self.real)
        self._kb.start()

    def tearDown(self):
        self._kb.stop()

    def test_real_key_names_are_accepted(self):
        for name in ("p", "f9", "esc", "ctrl+p", "scroll lock", "ctrl+shift+f12"):
            self.assertTrue(HotKey.is_valid(name), name)

    def test_junk_is_rejected(self):
        for name in ("zzz", "", "   ", None, 123, object()):
            self.assertFalse(HotKey.is_valid(name), repr(name))

    def test_without_keyboard_nothing_is_valid(self):
        with patch.object(HotKey, "keyboard", None):
            self.assertFalse(HotKey.is_valid("p"))       # 例外を投げないこと

    def test_the_display_capitalises_each_part(self):
        self.assertEqual(HotKey.display("p"), "P")
        self.assertEqual(HotKey.display("ctrl+p"), "Ctrl+P")
        self.assertEqual(HotKey.display("ctrl+shift+f12"), "Ctrl+Shift+F12")
        self.assertEqual(HotKey.display("scroll lock"), "Scroll Lock")

    def test_the_display_falls_back_for_junk(self):
        for name in ("", "   ", None, 123):
            self.assertEqual(HotKey.display(name),
                             HotKey.display(config.EMERGENCY_STOP_KEY), repr(name))

    def test_capture_returns_what_was_pressed(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        with patch.object(HotKey.keyboard, "read_hotkey", return_value="f9"):
            self.assertEqual(HotKey.capture(2.0), "f9")

    def test_capture_never_suppresses(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        """押したキーがVRChatや他のアプリに届かなくなる"""
        with patch.object(HotKey.keyboard, "read_hotkey",
                          return_value="p") as mock_read:
            HotKey.capture(2.0)

        self.assertEqual(mock_read.call_args.kwargs.get("suppress"), False)

    def test_capture_gives_up_on_a_timeout(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        started = threading.Event()

        def never(**_kw):
            started.set()
            time.sleep(5)

        with patch.object(HotKey.keyboard, "read_hotkey", never):
            self.assertIsNone(HotKey.capture(0.2))
        self.assertTrue(started.wait(2), "読み取りは始まっていること")

    def test_capture_swallows_errors(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        with patch.object(HotKey.keyboard, "read_hotkey",
                          side_effect=RuntimeError("boom")):
            self.assertIsNone(HotKey.capture(2.0))

    def test_capture_without_keyboard_is_none(self):
        with patch.object(HotKey, "keyboard", None):
            self.assertIsNone(HotKey.capture(2.0))




class TestAnnounceFold(unittest.TestCase):
    """③ の音声ファイル12行は「アナウンス」（既定で閉じる）に畳む。音量の2つは外（下）"""

    LABELS = ["続行ラウンド:", "Alternate:", "Midnight:", "Unbound:", "Fog:", "Ghost:",
              "8 Pages(速度検知):", "Punish(速度検知):", "アイテムロスト:", "Intermission:",
              "Foxy:", "主催リスト喪失:"]
    VARS = ["v_voice_continue", "v_voice_alternate", "v_voice_midnight", "v_voice_unbound",
            "v_voice_fog", "v_voice_ghost", "v_voice_8pages", "v_voice_punish",
            "v_voice_item_lost", "v_voice_intermission", "v_voice_foxy", "v_voice_list_lost"]

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    @staticmethod
    def _inside(widget, container) -> bool:
        while widget is not None:
            if widget is container:
                return True
            widget = widget.master
        return False

    def _all(self, root=None):
        root = root or self.app
        for child in root.winfo_children():
            yield child
            yield from self._all(child)

    def _bound_to(self, option, var):
        return [w for w in self._all()
                if option in w.keys() and str(w.cget(option)) == str(var)]

    def test_the_twelve_entries_are_in_the_fold_which_starts_closed(self):
        fold = self.app._announce_frame
        for name in self.VARS:
            entries = self._bound_to("textvariable", getattr(self.app, name))
            self.assertEqual(len(entries), 1, name)
            self.assertTrue(self._inside(entries[0], fold.content), name)
        self.assertEqual(fold.content.winfo_manager(), "", "既定で閉じている")

    def test_the_two_volumes_stay_outside_below_the_fold(self):
        fold = self.app._announce_frame
        tool = self._bound_to("variable", self.app.v_volume)
        window = self._bound_to("variable", self.app.v_wvol_enabled)
        self.assertTrue(tool and window)
        for widget in tool + window:
            self.assertFalse(self._inside(widget, fold), widget)
        parent = fold.master
        order = parent.pack_slaves()

        def slot(widget):
            while widget.master is not parent:
                widget = widget.master
            return order.index(widget)

        self.assertLess(order.index(fold), slot(tool[0]))
        self.assertLess(slot(tool[0]), slot(window[0]), "並びは今のまま")

    def test_opening_shows_the_rows_in_the_same_order(self):
        fold = self.app._announce_frame
        fold._toggle()
        self.addCleanup(fold._toggle)
        self.assertEqual(fold.content.winfo_manager(), "pack")
        labels = [row.winfo_children()[0].cget("text") for row in fold.content.pack_slaves()]
        self.assertEqual(labels, self.LABELS)




class TestEmergencyKeyGui(unittest.TestCase):
    """GUI 側。App を1つ立てて確かめる"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        self._kb = patch.object(HotKey, "keyboard", real)
        self._kb.start()
        self.addCleanup(self._kb.stop)
        self.app.v_emergency_key.set(config.EMERGENCY_STOP_KEY)
        self.app.v_start_key.set("")
        self.app._capturing_key = False
        self.app._emergency_stop_key_pressed = False
        self.app._start_key_pressed = False
        self.app.logs = []
        self._log = patch.object(mainGUI.App, "_log",
                                 lambda _s, m: self.app.logs.append(m))
        self._log.start()

    def tearDown(self):
        self._log.stop()
        self.app._capturing_key = False
        self.app.v_emergency_key.set(config.EMERGENCY_STOP_KEY)

    def test_the_default_is_p(self):
        self.assertEqual(self.app.v_emergency_key.get(), "p")

    def test_a_valid_key_is_adopted(self):
        self.app._finish_capture_key("f9")

        self.assertEqual(self.app.v_emergency_key.get(), "f9")
        self.assertIn("F9", self.app.lbl_emergency.cget("text"))

    def test_an_invalid_key_falls_back_to_the_default(self):
        """打ち間違いを抱えたままだと緊急停止が黙って効かなくなる"""
        self.app.v_emergency_key.set("f9")

        self.app._finish_capture_key("zzz")

        self.assertEqual(self.app.v_emergency_key.get(), "p")
        self.assertTrue(any("使えないキー" in m for m in self.app.logs),
                        self.app.logs)

    def test_a_timeout_leaves_the_key_alone(self):
        self.app.v_emergency_key.set("f9")

        self.app._finish_capture_key(None)

        self.assertEqual(self.app.v_emergency_key.get(), "f9")
        self.assertTrue(any("取れませんでした" in m for m in self.app.logs))

    def test_the_key_is_ignored_while_capturing(self):
        """設定しようとしたキーで停止がかかると困る"""
        self.app._capturing_key = True

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mainGUI.App._on_stop_start_key_event(self.app)

        mock_keyboard.is_pressed.assert_not_called()

    def test_the_key_is_seen_again_after_capturing(self):
        self.app._capturing_key = True
        self.app._finish_capture_key("f9")

        self.assertFalse(self.app._capturing_key)
        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mock_keyboard.is_pressed.return_value = False
            mainGUI.App._on_stop_start_key_event(self.app)

        mock_keyboard.is_pressed.assert_called_once_with("f9")

    def test_capturing_runs_off_the_gui_thread(self):
        """read_hotkey は待つので、GUIスレッドで呼ぶと固まる"""
        seen = {}

        def slow(_timeout):
            seen["thread"] = threading.current_thread()
            return "f9"

        with patch.object(HotKey, "capture", slow), \
             patch.object(self.app, "after") as mock_after:
            self.app._begin_capture_key()
            for _ in range(50):
                if "thread" in seen:
                    break
                time.sleep(0.02)

        self.assertIn("thread", seen, "捕捉が始まっていること")
        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertTrue(mock_after.called, "結果はGUIスレッドへ戻すこと")
        self.app._capturing_key = False

    def test_the_configured_key_stops_the_macro(self):
        self.app.v_emergency_key.set("f9")
        self.app._emergency_stop_key_pressed = False

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after") as mock_after:
            mock_keyboard.is_pressed.return_value = True
            mainGUI.App._on_stop_start_key_event(self.app)

        mock_keyboard.is_pressed.assert_called_once_with("f9")
        self.assertIn(self.app._on_stop_key, [c.args[1] for c in mock_after.call_args_list
                                              if len(c.args) > 1])

    def test_a_short_tap_is_not_missed(self):
        """通知の瞬間に見るので、200ms の見張りの合間に収まる短い押下でも止まる（長押し不要）"""
        self.app.v_emergency_key.set("f9")
        self.app._emergency_stop_key_pressed = False
        handed = []

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after", lambda _ms, f, *a: handed.append(f)):
            mock_keyboard.is_pressed.return_value = True     # 押した通知
            mainGUI.App._on_stop_start_key_event(self.app)
            mock_keyboard.is_pressed.return_value = False    # すぐ離した通知
            mainGUI.App._on_stop_start_key_event(self.app)

        self.assertEqual(handed, [self.app._on_stop_key])

    def test_a_tap_released_before_the_notice_is_handled_still_stops(self):
        """通知が遅れて届き、表ではもう離れていても、押した通知そのものがそのキーなら止まる"""
        self.app.v_emergency_key.set("f9")
        self.app._emergency_stop_key_pressed = False
        handed = []
        down = type("Event", (), {"event_type": "down", "scan_code": 67})()

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after", lambda _ms, f, *a: handed.append(f)):
            mock_keyboard.is_pressed.return_value = False        # もう離されている
            mock_keyboard.parse_hotkey.return_value = (((67,),),)
            mainGUI.App._on_stop_start_key_event(self.app, down)

        self.assertEqual(handed, [self.app._on_stop_key])

    def test_another_key_released_late_does_not_stop(self):
        self.app.v_emergency_key.set("f9")
        self.app._emergency_stop_key_pressed = False
        handed = []
        down = type("Event", (), {"event_type": "down", "scan_code": 30})()

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after", lambda _ms, f, *a: handed.append(f)):
            mock_keyboard.is_pressed.return_value = False
            mock_keyboard.parse_hotkey.return_value = (((67,),),)
            mainGUI.App._on_stop_start_key_event(self.app, down)

        self.assertEqual(handed, [])

    def test_holding_it_stops_only_once(self):
        """押しっぱなしの繰り返しの通知では増えない"""
        self.app.v_emergency_key.set("f9")
        self.app._emergency_stop_key_pressed = False
        handed = []

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after", lambda _ms, f, *a: handed.append(f)):
            mock_keyboard.is_pressed.return_value = True
            for _ in range(5):
                mainGUI.App._on_stop_start_key_event(self.app)

        self.assertEqual(handed, [self.app._on_stop_key])

    def test_the_default_key_does_not_stop_once_changed(self):
        self.app.v_emergency_key.set("f9")

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mock_keyboard.is_pressed.return_value = False
            mainGUI.App._on_stop_start_key_event(self.app)

        mock_keyboard.is_pressed.assert_called_once_with("f9")
        self.assertNotIn("p", [c.args[0] for c in
                               mock_keyboard.is_pressed.call_args_list])

    def test_a_broken_key_at_poll_time_falls_back(self):
        """is_pressed が投げたら握り潰さずに既定値へ倒す"""
        self.app.v_emergency_key.set("zzz")

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after", lambda _ms, f, *a: f(*a)):
            mock_keyboard.is_pressed.side_effect = ValueError("bad")
            mainGUI.App._on_stop_start_key_event(self.app)

        self.assertEqual(self.app.v_emergency_key.get(), "p")
        self.assertTrue(any("使えないキー" in m for m in self.app.logs),
                        self.app.logs)

    def test_the_label_follows_the_key(self):
        self.app.v_emergency_key.set("ctrl+p")

        self.app._refresh_emergency_key_label()

        self.assertIn("Ctrl+P", self.app.lbl_emergency.cget("text"))

    def test_no_hardcoded_p_key_remains(self):
        source = Path("mainGUI.py").read_text(encoding="utf-8")

        self.assertNotIn("Pキー", source)
        self.assertNotIn("EMERGENCY_STOP_KEY.upper()", source)

    def test_a_missing_keyboard_module_does_not_break_startup(self):
        with patch.object(mainGUI, "keyboard", None):
            mainGUI.App._start_emergency_stop_polling(self.app)   # 落ちないこと




class TestEmergencyKeySettings(unittest.TestCase):
    """保存と復元。壊れた値で緊急停止を失わないこと"""

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def _app(self, key="p"):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.v_emergency_key = TestEmergencyKeySettings.FakeVar(key)
        app.v_start_key = TestEmergencyKeySettings.FakeVar("")
        app.v_big_key = TestEmergencyKeySettings.FakeVar("")
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        return app

    def _load(self, app, data):
        """_load_saved_settings のうち緊急停止キーの復元部分だけを回す"""
        key = data.get("emergency_stop_key", config.EMERGENCY_STOP_KEY)
        if not HotKey.is_valid(key):
            key = config.EMERGENCY_STOP_KEY
        app.v_emergency_key.set(key)
        app._refresh_emergency_key_label()

    def test_the_key_is_saved(self):
        app = self._app("f9")
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_freeze_8pages", "v_freeze_punish"):
            setattr(app, name, TestEmergencyKeySettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestEmergencyKeySettings.FakeVar(""))
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["emergency_stop_key"], "f9")

    def test_a_saved_key_is_restored(self):
        app = self._app()

        self._load(app, {"emergency_stop_key": "ctrl+p"})

        self.assertEqual(app.v_emergency_key.get(), "ctrl+p")

    def test_a_broken_saved_key_falls_back(self):
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        self._kb = patch.object(HotKey, "keyboard", real)
        self._kb.start()
        self.addCleanup(self._kb.stop)
        app = self._app()

        self._load(app, {"emergency_stop_key": "zzz"})

        self.assertEqual(app.v_emergency_key.get(), "p")

    def test_a_legacy_file_without_the_key_is_fine(self):
        app = self._app()

        self._load(app, {"tnl_path": "C:/list/my.tnl"})

        self.assertEqual(app.v_emergency_key.get(), "p")




class TestStartKeyGui(unittest.TestCase):
    """マクロ開始のキー。停止キーと違い、既定は未設定で、不正なら無効にする。

    停止は効かないと危ないので既定値へ倒すが、開始は勝手に動き出す方が危ない。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        self._kb = patch.object(HotKey, "keyboard", real)
        self._kb.start()
        self.addCleanup(self._kb.stop)
        self.app.v_emergency_key.set(config.EMERGENCY_STOP_KEY)
        self.app.v_start_key.set("")
        self.app._capturing_key = False
        self.app._emergency_stop_key_pressed = False
        self.app._start_key_pressed = False
        self.app.btn_start.config(state="normal")
        self.app.logs = []
        self._log = patch.object(mainGUI.App, "_log",
                                 lambda _s, m: self.app.logs.append(m))
        self._log.start()
        self.addCleanup(self._log.stop)
        self.addCleanup(lambda: self.app.v_start_key.set(""))
        self.addCleanup(lambda: setattr(self.app, "_capturing_key", False))

    def _poll(self, pressed=True, side_effect=None):
        """キーの通知を1回流す（Tk へ渡す分はその場で走らせる）。停止キーは押されていない扱い。
        started は走った _start / _stop（本物は呼ばない）"""
        def is_pressed(key):
            if side_effect is not None and key == self.app.v_start_key.get():
                raise side_effect
            return pressed and key == self.app.v_start_key.get()

        real_start, real_stop = self.app._start, self.app._stop
        started = []
        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after", lambda _ms, f, *a: f(*a)), \
             patch.object(self.app, "_start", lambda: started.append(real_start)), \
             patch.object(self.app, "_stop", lambda: started.append(real_stop)):
            mock_keyboard.is_pressed.side_effect = is_pressed
            mainGUI.App._on_stop_start_key_event(self.app)
        called = [c.args[0] for c in mock_keyboard.is_pressed.call_args_list]
        return called, started

    # ── 既定 ─────────────────────────────────
    def test_the_default_is_unset(self):
        self.assertEqual(config.START_KEY, "", "既定は未設定＝無効")
        self.assertIn("未設定", self.app.lbl_start_key.cget("text"))

    def test_an_unset_key_is_never_asked_about(self):
        """is_pressed("") を呼ばないこと"""
        called, started = self._poll()

        self.assertNotIn("", called)
        self.assertEqual(called, [config.EMERGENCY_STOP_KEY], called)
        self.assertNotIn(self.app._start, started)

    # ── 押されたとき ────────────────────────────
    def test_the_configured_key_starts_the_macro(self):
        self.app.v_start_key.set("f9")

        called, started = self._poll()

        self.assertIn("f9", called)
        self.assertIn(self.app._start, started)

    def test_holding_it_starts_only_once(self):
        self.app.v_start_key.set("f9")
        self._poll()

        _called, started = self._poll()

        self.assertNotIn(self.app._start, started, "押し続けても1回だけ")

    def test_it_does_nothing_while_the_button_is_disabled(self):
        """動作中・起動中は「▶ マクロ開始」が disabled になっている"""
        self.app.v_start_key.set("f9")
        self.app.btn_start.config(state="disabled")
        self.addCleanup(lambda: self.app.btn_start.config(state="normal"))

        _called, started = self._poll()

        self.assertNotIn(self.app._start, started)

    def test_it_says_why_nothing_happened(self):
        """黙って無視すると「キーが効かない」と見える"""
        self.app.v_start_key.set("f9")
        self.app.btn_start.config(state="disabled")
        self.addCleanup(lambda: self.app.btn_start.config(state="normal"))

        _called, started = self._poll()

        self.assertEqual([m for m in self.app.logs if "いま押せません" in m],
                         ["[マクロ開始] いま押せません（動作中か起動中）"],
                         self.app.logs)
        self.assertNotIn(self.app._start, started)

    def test_holding_it_says_so_only_once(self):
        self.app.v_start_key.set("f9")
        self.app.btn_start.config(state="disabled")
        self.addCleanup(lambda: self.app.btn_start.config(state="normal"))

        for _ in range(3):
            self._poll()

        self.assertEqual(len([m for m in self.app.logs if "いま押せません" in m]), 1,
                         self.app.logs)

    def test_it_says_nothing_when_it_can_be_pressed(self):
        self.app.v_start_key.set("f9")

        self._poll()

        self.assertFalse(any("いま押せません" in m for m in self.app.logs),
                         self.app.logs)

    def test_a_broken_key_at_poll_time_is_disabled(self):
        """既定値へ倒さない。勝手に動き出す方が危ない"""
        self.app.v_start_key.set("zzz")

        self._poll(side_effect=ValueError("bad"))

        self.assertEqual(self.app.v_start_key.get(), "")
        self.assertNotEqual(self.app.v_start_key.get(), config.EMERGENCY_STOP_KEY)
        self.assertTrue(any("使えないキー" in m for m in self.app.logs), self.app.logs)
        self.assertIn("未設定", self.app.lbl_start_key.cget("text"))

    def test_the_stop_key_still_stops(self):
        """開始キーを足しても停止キーの挙動は変わらない"""
        self.app.v_start_key.set("f9")

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after") as mock_after:
            mock_keyboard.is_pressed.return_value = True
            mainGUI.App._on_stop_start_key_event(self.app)

        started = [c.args[1] for c in mock_after.call_args_list if len(c.args) > 1]
        self.assertIn(self.app._on_stop_key, started)

    # ── 捕捉 ─────────────────────────────────
    def test_a_valid_key_is_adopted(self):
        self.app._finish_capture_key("f9", "start")

        self.assertEqual(self.app.v_start_key.get(), "f9")
        self.assertIn("F9", self.app.lbl_start_key.cget("text"))

    def test_an_invalid_key_leaves_it_unset(self):
        self.app._finish_capture_key("zzz", "start")

        self.assertEqual(self.app.v_start_key.get(), "")
        self.assertTrue(any("使えないキー" in m for m in self.app.logs), self.app.logs)

    def test_an_invalid_key_keeps_the_previous_one(self):
        self.app.v_start_key.set("f9")

        self.app._finish_capture_key("zzz", "start")

        self.assertEqual(self.app.v_start_key.get(), "f9", "別のキーへ倒さない")

    def test_a_timeout_leaves_the_key_alone(self):
        self.app.v_start_key.set("f9")

        self.app._finish_capture_key(None, "start")

        self.assertEqual(self.app.v_start_key.get(), "f9")
        self.assertTrue(any("取れませんでした" in m for m in self.app.logs))

    def test_the_stop_key_cannot_be_reused_for_starting(self):
        self.app.v_emergency_key.set("f9")

        self.app._finish_capture_key("f9", "start")

        self.assertEqual(self.app.v_start_key.get(), "", "設定しない")
        self.assertTrue(any("緊急停止キーと同じ" in m for m in self.app.logs),
                        self.app.logs)

    def test_the_start_key_cannot_be_reused_for_stopping(self):
        """逆向きも断る。どちらか一方しか働かないため"""
        self.app.v_start_key.set("f9")
        self.app.v_emergency_key.set("p")

        self.app._finish_capture_key("f9")

        self.assertEqual(self.app.v_emergency_key.get(), "p", "設定しない")
        self.assertTrue(any("マクロ開始キーと同じ" in m for m in self.app.logs),
                        self.app.logs)

    def test_neither_key_is_seen_while_capturing(self):
        self.app.v_start_key.set("f9")
        self.app._capturing_key = True

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mainGUI.App._on_stop_start_key_event(self.app)

        mock_keyboard.is_pressed.assert_not_called()

    def test_capturing_the_start_key_uses_its_own_button(self):
        with patch.object(HotKey, "capture", return_value="f9"), \
             patch.object(self.app, "after"):
            self.app._begin_capture_key("start")

        self.assertEqual(str(self.app.btn_capture_start_key.cget("state")),
                         "disabled")
        self.assertEqual(str(self.app.btn_capture_key.cget("state")), "normal",
                         "停止側のボタンは触らない")
        self.app._finish_capture_key("f9", "start")
        self.assertEqual(str(self.app.btn_capture_start_key.cget("state")),
                         "normal")

    # ── 解除 ─────────────────────────────────
    def test_it_can_be_cleared(self):
        self.app.v_start_key.set("f9")

        self.app._clear_start_key()

        self.assertEqual(self.app.v_start_key.get(), "")
        self.assertIn("未設定", self.app.lbl_start_key.cget("text"))
        self.assertTrue(any("解除" in m for m in self.app.logs), self.app.logs)

    def test_clearing_twice_is_harmless(self):
        self.app._clear_start_key()

        self.assertEqual(self.app.v_start_key.get(), "")

    # ── 窓の幅 ────────────────────────────────
    def test_the_emergency_key_is_on_the_start_key_row(self):
        """緊急停止のキー設定はマクロ開始キーと同じ行の先頭。ボタンの行には置かない
        （別のPCで崩れた件 47aefa4 / 73e577c があるので、ボタンの行は広げない）"""
        row = self.app.lbl_start_key.master
        self.assertIs(self.app.lbl_emergency.master, row)
        self.assertIs(self.app.btn_capture_key.master, row)
        self.assertIsNot(self.app.btn_start.master, row)
        order = row.pack_slaves()
        # マクロ開始が先、緊急停止が後
        self.assertEqual(order[:5], [self.app.lbl_start_key, self.app.btn_capture_start_key,
                                     self.app.btn_clear_start_key, self.app.lbl_emergency,
                                     self.app.btn_capture_key])




class TestToolLauncher(unittest.TestCase):
    """登録した exe を起動する（GUI 不要の部分）"""

    EXE = r"D:\tools\ToN_ListTool.exe"

    def test_the_label_is_the_file_stem(self):
        self.assertEqual(ToolLauncher.button_label(self.EXE), "ToN_ListTool")

    def test_an_empty_path_has_no_label(self):
        for value in ("", "   ", None):
            self.assertEqual(ToolLauncher.button_label(value), "", repr(value))

    def test_a_odd_path_does_not_raise(self):
        for value in (123, object(), "::::"):
            ToolLauncher.button_label(value)   # 例外を投げないこと

    def test_is_running_asks_by_file_name(self):
        with patch.object(ProcessCheck, "is_process_running",
                          return_value=True) as mock_running:
            self.assertTrue(ToolLauncher.is_running(self.EXE))

        mock_running.assert_called_once_with("ToN_ListTool.exe")

    def test_an_empty_path_is_not_running(self):
        with patch.object(ProcessCheck, "is_process_running") as mock_running:
            self.assertFalse(ToolLauncher.is_running(""))

        mock_running.assert_not_called()

    def test_launch_starts_it_in_its_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "ToN_ListTool.exe"
            exe.write_bytes(b"")

            with patch.object(ToolLauncher.subprocess, "Popen") as mock_popen:
                ToolLauncher.launch(str(exe))

            mock_popen.assert_called_once_with([str(exe)], cwd=str(exe.parent))

    def test_launch_does_not_hide_the_window(self):
        """GUIアプリなので CREATE_NO_WINDOW は付けない"""
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "app.exe"
            exe.write_bytes(b"")

            with patch.object(ToolLauncher.subprocess, "Popen") as mock_popen:
                ToolLauncher.launch(str(exe))

            self.assertNotIn("creationflags", mock_popen.call_args.kwargs)

    def test_an_empty_path_raises(self):
        with self.assertRaises(ValueError):
            ToolLauncher.launch("   ")

    def test_a_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            ToolLauncher.launch(r"D:\nope\missing.exe")




class TestToolLauncherRows(unittest.TestCase):
    """GUI 側の行の扱い。App を1つ立てて確かめる"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        for row in list(self.app.tool_rows):
            mainGUI.App._remove_tool_row(self.app, row)
        self.app.logs = []
        self._log = patch.object(mainGUI.App, "_log",
                                 lambda _s, m: self.app.logs.append(m))
        self._log.start()

    def tearDown(self):
        self._log.stop()
        for row in list(self.app.tool_rows):
            mainGUI.App._remove_tool_row(self.app, row)

    def _add(self, path=""):
        row = self.app._add_tool_row(path)
        self.app.update_idletasks()
        return row

    def test_adding_a_row(self):
        self._add()

        self.assertEqual(len(self.app.tool_rows), 1)

    def test_removing_a_row(self):
        row = self._add()

        self.app._remove_tool_row(row)

        self.assertEqual(self.app.tool_rows, [])

    def test_the_label_follows_the_path(self):
        row = self._add()

        with patch.object(ToolLauncher, "is_running", return_value=False):
            row.v_path.set(r"D:\tools\ToNSaveManager.exe")
            self.app.update_idletasks()

        self.assertIn("ToNSaveManager", row.btn.cget("text"))

    def test_an_empty_path_shows_the_default_label(self):
        row = self._add()

        self.assertEqual(row.btn.cget("text"), "▶ 起動")

    def test_the_button_launches(self):
        row = self._add(r"D:\tools\ToN_ListTool.exe")

        with patch.object(ToolLauncher, "is_running", return_value=False), \
             patch.object(ToolLauncher, "launch") as mock_launch:
            self.app._launch_tool(row)

        mock_launch.assert_called_once_with(r"D:\tools\ToN_ListTool.exe")
        self.assertTrue(any("を起動しました" in m for m in self.app.logs))

    def test_a_running_tool_is_not_launched_again(self):
        """止めているのはここ。ボタンの無効化は見た目でしかない"""
        row = self._add(r"D:\tools\ToN_ListTool.exe")

        with patch.object(ToolLauncher, "is_running", return_value=True), \
             patch.object(ToolLauncher, "launch") as mock_launch:
            self.app._launch_tool(row)

        mock_launch.assert_not_called()
        self.assertTrue(any("すでに起動しています" in m for m in self.app.logs),
                        self.app.logs)

    def test_a_failing_launch_is_logged(self):
        row = self._add(r"D:\nope\missing.exe")

        with patch.object(ToolLauncher, "is_running", return_value=False), \
             patch.object(ToolLauncher, "launch",
                          side_effect=FileNotFoundError("ない")):
            self.app._launch_tool(row)      # 落ちないこと

        self.assertTrue(any("起動に失敗" in m for m in self.app.logs), self.app.logs)

    def test_a_running_tool_disables_the_button(self):
        row = self._add(r"D:\tools\ToN_ListTool.exe")

        with patch.object(ToolLauncher, "is_running", return_value=True):
            self.app._refresh_tool_row(row)

        self.assertEqual(str(row.btn.cget("state")), "disabled")

    def _poll_now(self, calls):
        """_poll_tool_buttons を1回まわす。裏のスレッドの代わりにその場で、Tk へ渡す分もその場で。
        次の tick の予約は calls に貯める"""
        def after(ms, func, *args):
            if ms == 0:
                func(*args)                 # 裏のスレッドから Tk へ渡された分
            else:
                calls.append((ms, func))
        with patch.object(self.app, "after", after), \
             patch.object(self.app, "_off_gui", lambda work: work()):
            mainGUI.App._poll_tool_buttons(self.app)

    def test_the_poll_survives_a_failure(self):
        self._add(r"D:\tools\ToN_ListTool.exe")
        calls = []
        with patch.object(mainGUI.App, "_refresh_tool_row",
                          side_effect=RuntimeError("boom")):
            self._poll_now(calls)

        self.assertEqual(len(calls), 1, "次のtickが予約されること")

    def test_the_poll_takes_one_process_list_for_all_tools(self):
        """プロセスの一覧はツールの数だけ取らない（1回で済ませる）"""
        first = self._add("D:/tools/ToN_ListTool.exe")
        second = self._add("D:/tools/SaveManager.exe")
        calls = []
        with patch.object(ProcessCheck, "running_names",
                          return_value=frozenset({"ton_listtool.exe"})) as names, \
             patch.object(ProcessCheck, "is_process_running") as single:
            self._poll_now(calls)

        names.assert_called_once()
        single.assert_not_called()
        self.assertEqual(str(first.btn.cget("state")), "disabled")
        self.assertEqual(str(second.btn.cget("state")), "normal")

    def test_the_poll_is_not_the_host_save_one(self):
        """続行リストの供給元判定とボタンの見た目は無関係"""
        source = Path("mainGUI.py").read_text(encoding="utf-8")

        self.assertIn("_poll_tool_buttons", source)
        start = source.index("def _poll_host_save")
        end = source.index("def ", start + 10)
        self.assertNotIn("_refresh_tool_row", source[start:end])

    def test_the_section_is_not_on_the_window_tab(self):
        """窓タブを何枚開いてもセクションは1つだけ"""
        self.assertFalse(hasattr(mainGUI.WindowTab, "_add_tool_row"))
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        tab = source[source.index("class WindowTab"):source.index("class App")]
        self.assertNotIn("tool_rows", tab)
        self.assertNotIn("ToolLauncher", tab)




class TestLaunchAlwaysMakesNewInstances(unittest.TestCase):
    """起動は常に「窓ごとのToN新規インスタンス」。exeは自動検出だけ"""

    class FakeVar:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeWidget:
        def config(self, **kwargs):
            pass

    def _app(self, access=None, windows=2, join=True):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app._running = False
        app.v_join_world = self.FakeVar(join)
        app.v_desktop_mode = self.FakeVar(True)
        app.v_use_osc = self.FakeVar(True)
        app.v_ton_access = self.FakeVar(access or config.TON_INSTANCE_ACCESS_INVITE_PLUS)
        app.v_launch_options = self.FakeVar("")
        app.v_launch_count = self.FakeVar(windows)
        app.tabs = []
        for i in range(windows):
            tab = type("FakeTab", (), {})()
            tab.idx = i
            tab.v_profile = self.FakeVar(i)
            app.tabs.append(tab)
        app.btn_launch = self.FakeWidget()
        app.lbl_launch = self.FakeWidget()
        app._launch_count_value = lambda n: windows
        app._save_launch_settings = lambda: None
        app.after = lambda _ms, fn=None: None
        app.logs = []
        app._log = app.logs.append
        return app

    def _launch(self, app, exe=Path("C:/VRChat/launch.exe"), user_id="usr_me"):
        """worker スレッドは同じスレッドで走らせて、起動引数を集める"""
        launched = []

        def fake_thread(target=None, daemon=None):
            thread = MagicMock()
            thread.start = target
            return thread

        with patch.object(mainGUI, "messagebox") as box, \
             patch.object(VRChatLauncher, "find_vrchat_exe", return_value=exe), \
             patch.object(VRChatLauncher, "latest_user_id", return_value=user_id), \
             patch.object(VRChatLauncher, "launch_one",
                          side_effect=lambda *a, **kw: launched.append((a, kw)) or ["exe"]), \
             patch.object(VRChatLauncher, "wait_for_windows", return_value=set()), \
             patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time", return_value=[]), \
             patch.object(mainGUI.threading, "Thread", side_effect=fake_thread), \
             patch.object(mainGUI.time, "sleep"):
            mainGUI.App._launch_vrchat(app)
        return launched, box

    def _links(self, launched):
        return [a[3] for a, _kw in launched]

    def test_the_field_value_is_passed(self):
        """起動のたびに、その時点の起動オプションの欄の値を渡す"""
        app = self._app(windows=2)
        app.v_launch_options = self.FakeVar("--foo")
        launched, _box = self._launch(app)
        self.assertEqual([kw.get("extra_options") for _a, kw in launched], ["--foo", "--foo"])

    def test_each_window_gets_its_own_new_instance(self):
        launched, box = self._launch(self._app(windows=3))

        links = self._links(launched)
        self.assertEqual(len(links), 3)
        self.assertEqual(len(set(links)), 3, "窓ごとに別インスタンス")
        for link in links:
            self.assertIn(config.TON_WORLD_ID, link)
            self.assertIn("~private(usr_me)", link)
        box.showerror.assert_not_called()

    def test_the_instance_access_setting_is_used(self):
        for access, plus in ((config.TON_INSTANCE_ACCESS_INVITE, False),
                             (config.TON_INSTANCE_ACCESS_INVITE_PLUS, True)):
            launched, _box = self._launch(self._app(access=access))

            for link in self._links(launched):
                self.assertEqual("canRequestInvite" in link, plus, (access, link))

    def test_without_an_exe_nothing_is_launched(self):
        launched, box = self._launch(self._app(), exe=None)

        self.assertEqual(launched, [])
        self.assertIn("launch.exe", box.showerror.call_args.args[1])
        self.assertNotIn("起動exe", box.showerror.call_args.args[1])

    def test_without_a_user_id_nothing_is_launched(self):
        launched, box = self._launch(self._app(), user_id=None)

        self.assertEqual(launched, [])
        self.assertIn("ユーザーID", box.showerror.call_args.args[1])
        self.assertNotIn("参加リンク", box.showerror.call_args.args[1])

    # ── Join を外したとき（ホームで起動して、自分でインスタンスへ入る） ──
    def test_without_the_join_check_no_link_is_passed(self):
        with patch.object(VRChatLauncher, "build_ton_link") as build:
            launched, box = self._launch(self._app(windows=3, join=False))

        self.assertEqual(self._links(launched), [None, None, None])
        build.assert_not_called()           # リンクを組み立てもしない
        box.showerror.assert_not_called()

    def test_without_the_join_check_a_missing_user_id_is_fine(self):
        """リンクを作らないので、ユーザーIDは要らない"""
        launched, box = self._launch(self._app(join=False), user_id=None)

        self.assertEqual(len(launched), 2)
        box.showerror.assert_not_called()

    def test_without_the_join_check_a_missing_exe_still_stops(self):
        launched, box = self._launch(self._app(join=False), exe=None)

        self.assertEqual(launched, [])
        self.assertIn("launch.exe", box.showerror.call_args.args[1])

    # ── 消した設定 ──────────────────────────
    def test_the_gui_has_no_link_or_exe_fields(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        for gone in ("v_instance_link", "v_vrchat_exe",
                     "最新ログから取得", "起動exe",
                     "with_unique_instance", "normalize_instance_link",
                     "instance_link_from_log", "resolve_vrchat_exe"):
            self.assertNotIn(gone, src, gone)

    def test_an_old_settings_file_still_loads(self):
        """古い settings.json に残っているキーは無視する（落ちない）"""
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_emergency_key", "v_start_key", "v_big_key", "v_freeze_8pages",
                     "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, self.FakeVar(""))
        app.v_freeze_rounds = {}
        app._add_tool_row = lambda p, save=True: None
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._apply_obs_settings = lambda: None
        app._apply_saved_window_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app._log = lambda _m: None
        old = {"vrchat_exe": "C:/old/launch.exe", "join_world": True,   # join_world は戻した
               "instance_link": "vrchat://launch?ref=vrchat.com&id=wrld_x:1~region(jp)",
               "ton_instance_access": config.TON_INSTANCE_ACCESS_INVITE}

        with patch.object(mainGUI, "load_settings", return_value=dict(old)), \
             patch.object(mainGUI, "save_settings", lambda _d: None):
            app._load_window_volume_settings = lambda _data: None
            app._load_fog_early_read_setting = lambda _data: None
            app._load_launch_options_setting = lambda _data: None
            mainGUI.App._load_saved_settings(app)

        self.assertEqual(app.v_ton_access.get(), config.TON_INSTANCE_ACCESS_INVITE)
        self.assertTrue(app.v_join_world.get(), "join_world は復元する")




class TestTonInstanceAccessSetting(unittest.TestCase):
    """起動時に作るインスタンスの公開範囲（全窓で共通。既定はインバイト+）"""

    class FakeVar:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _app(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_freeze_8pages", "v_freeze_punish", "v_emergency_key", "v_start_key", "v_big_key", "v_tnl",
                     "v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, self.FakeVar(""))
        app.v_freeze_rounds = {}
        return app

    def _load(self, data, var="v_ton_access"):
        app = self._app()
        app._add_tool_row = lambda p, save=True: None
        app._refresh_emergency_key_label = lambda: None
        app._refresh_start_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._apply_obs_settings = lambda: None
        app._apply_saved_window_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app._log = lambda _m: None
        with patch.object(mainGUI, "load_settings", return_value=dict(data)), \
             patch.object(mainGUI, "save_settings", lambda _d: None):
            app._load_window_volume_settings = lambda _data: None
            app._load_fog_early_read_setting = lambda _data: None
            app._load_launch_options_setting = lambda _data: None
            mainGUI.App._load_saved_settings(app)
        return getattr(app, var).get()

    def test_all_four_are_restored_and_others_are_the_default(self):
        for access in ("invite", "invite_plus", "friends", "friends_plus"):
            self.assertEqual(self._load({"ton_instance_access": access}), access)
        for bad in ("hidden", "public", 1, None):
            self.assertEqual(self._load({"ton_instance_access": bad}),
                             config.TON_INSTANCE_ACCESS_DEFAULT, bad)

    def test_the_join_check_is_saved_and_restored(self):
        app = self._app()
        app.v_join_world.set(True)
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)

        self.assertIs(saved["join_world"], True)
        self.assertIs(self._load({"join_world": True}, "v_join_world"), True)
        self.assertIs(self._load({"join_world": False}, "v_join_world"), False)
        self.assertIs(self._load({}, "v_join_world"), False, "既定は外れている")

    def test_it_is_saved(self):
        app = self._app()
        app.v_ton_access.set(config.TON_INSTANCE_ACCESS_INVITE)
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["ton_instance_access"], config.TON_INSTANCE_ACCESS_INVITE)

    def test_it_is_restored(self):
        for access in config.TON_INSTANCE_ACCESS_CHOICES:
            self.assertEqual(self._load({"ton_instance_access": access}), access)

    def test_a_missing_or_broken_value_is_invite_plus(self):
        for data in ({}, {"ton_instance_access": ""}, {"ton_instance_access": "public"},
                     {"ton_instance_access": None}, {"ton_instance_access": 3}):
            self.assertEqual(self._load(data), config.TON_INSTANCE_ACCESS_INVITE_PLUS, data)



    # 設定がランチャーに渡ることは TestLaunchAlwaysMakesNewInstances で動きとして確かめる


class TestSettingsArePersisted(unittest.TestCase):
    """設定は VRChat を起動しなくても保存されること。

    保存の仕組み自体は前からあったが、呼ばれるのが「VRChatを起動」ボタンの
    中だけだった。このツールから起動しない人には何も残らなかった。
    """

    def _app(self, tabs=1, save=None):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = [object()] * tabs
        app.logs = []
        app._log = app.logs.append
        app._save_launch_settings = save or MagicMock()
        app._save_settings_now = lambda: mainGUI.App._save_settings_now(app)
        app._show_own_windows_again = lambda: None
        return app

    # ── 終了時 ──────────────────────────────
    def test_closing_saves(self):
        app = self._app()
        app._stop = MagicMock()
        app.destroy = MagicMock()

        app._stop_window_volume = lambda: None
        app._unhook_chase_keys = lambda: None
        app._unhook_stop_start_keys = lambda: None
        mainGUI.App._on_close(app)

        app._save_launch_settings.assert_called_once()

    def test_it_saves_before_destroying(self):
        """destroy() の後は Tk 変数を読めない"""
        order = []
        app = self._app(save=lambda: order.append("save"))
        app._stop = lambda: order.append("stop")
        app.destroy = lambda: order.append("destroy")

        app._stop_window_volume = lambda: None
        app._unhook_chase_keys = lambda: None
        app._unhook_stop_start_keys = lambda: None
        mainGUI.App._on_close(app)

        self.assertLess(order.index("save"), order.index("destroy"))

    def test_it_saves_before_stopping(self):
        """停止が長引いても保存は済ませる"""
        order = []
        app = self._app(save=lambda: order.append("save"))
        app._stop = lambda: order.append("stop")
        app.destroy = lambda: order.append("destroy")

        app._stop_window_volume = lambda: None
        app._unhook_chase_keys = lambda: None
        app._unhook_stop_start_keys = lambda: None
        mainGUI.App._on_close(app)

        self.assertLess(order.index("save"), order.index("stop"))

    def test_a_failing_save_still_closes_the_window(self):
        """閉じられなくなるほうが困る"""
        app = self._app(save=MagicMock(side_effect=OSError("disk full")))
        app._stop = MagicMock()
        app.destroy = MagicMock()

        app._stop_window_volume = lambda: None
        app._unhook_chase_keys = lambda: None
        app._unhook_stop_start_keys = lambda: None
        mainGUI.App._on_close(app)

        app.destroy.assert_called_once()
        self.assertTrue(any("保存に失敗" in m for m in app.logs), app.logs)

    def test_no_tabs_means_no_save(self):
        """profiles などが空配列で上書きされる事故を避ける"""
        app = self._app(tabs=0)

        mainGUI.App._save_settings_now(app)

        app._save_launch_settings.assert_not_called()

    # ── 保存する項目は増減していない ────────────────
    def test_the_saved_keys_are_unchanged(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_freeze_8pages", "v_freeze_punish", "v_emergency_key", "v_start_key", "v_big_key"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(set(saved), {
            "desktop_mode", "use_osc", "ton_entry", "ton_begin", "join_world",
            "ton_instance_access", "profiles", "freeze_8pages",
            "freeze_punish", "freeze_rounds", "emergency_stop_key", "start_key",
            "big_window_key",
            "suicide_cancel_key", "item_fetch", "item_fetch_gain",
            "win_count",
            "tool_launchers", "obs_record", "obs_host", "obs_port", "obs_password_dpapi",
        }, "ラウンド指定3種は保存しない")

    def test_other_keys_in_the_file_survive(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = []
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_freeze_8pages", "v_freeze_punish", "v_emergency_key", "v_start_key", "v_big_key"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings",
                          return_value={"tnl_path": "C:/list/my.tnl"}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["tnl_path"], "C:/list/my.tnl")




class TestToolRowsSaveOnChange(unittest.TestCase):
    """外部ツールの行は、追加・削除・パス変更で保存される"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        for row in list(self.app.tool_rows):
            mainGUI.App._remove_tool_row(self.app, row)
        job = getattr(self.app, "_settings_save_job", None)
        if job is not None:
            self.app.after_cancel(job)
            self.app._settings_save_job = None
        self._save = patch.object(mainGUI.App, "_save_launch_settings")
        self.saved = self._save.start()

    def tearDown(self):
        self._save.stop()
        for row in list(self.app.tool_rows):
            self.app.tool_rows.remove(row)
            row.destroy()

    def _settle(self):
        """デバウンスのタイマーを起こす"""
        for _ in range(60):
            self.app.update()
            if self.saved.called:
                return
            time.sleep(0.02)
        self.app.update()

    def test_adding_a_row_saves(self):
        self.app._add_tool_row()

        self.saved.assert_called_once()

    def test_restoring_does_not_save(self):
        """読み込んだ端から書き戻さない"""
        self.app._add_tool_row(r"D:\a\one.exe", save=False)

        self.saved.assert_not_called()

    def test_removing_a_row_saves(self):
        row = self.app._add_tool_row(save=False)

        self.app._remove_tool_row(row)

        self.saved.assert_called_once()

    def test_changing_the_path_saves_after_the_debounce(self):
        row = self.app._add_tool_row(save=False)

        row.v_path.set(r"D:\a\one.exe")
        self.assertFalse(self.saved.called, "すぐには書かないこと")
        self._settle()

        self.saved.assert_called_once()

    def test_typing_saves_only_once(self):
        row = self.app._add_tool_row(save=False)

        for text in ("D", "D:", r"D:\a", r"D:\a\one.exe"):
            row.v_path.set(text)
            self.app.update()
        self._settle()

        self.assertEqual(self.saved.call_count, 1, "まとめて1回だけ")

    def test_a_late_timer_after_destroy_does_not_raise(self):
        """ウィンドウを閉じた後にタイマーが発火しても落ちないこと"""
        app = type("FakeApp", (), {})()
        app._settings_save_job = "予約済み"
        app._save_settings_now = MagicMock(
            side_effect=tk.TclError("application has been destroyed"))

        mainGUI.App._run_scheduled_save(app)      # 例外を投げないこと

        self.assertIsNone(app._settings_save_job, "予約は消しておくこと")

    def test_scheduling_after_destroy_does_not_raise(self):
        app = type("FakeApp", (), {})()
        app._settings_save_job = None
        app._run_scheduled_save = lambda: None
        app.after = MagicMock(side_effect=tk.TclError("destroyed"))
        app.after_cancel = MagicMock()

        mainGUI.App._schedule_settings_save(app)

        self.assertIsNone(app._settings_save_job)

    def test_the_debounce_is_rescheduled_not_stacked(self):
        row = self.app._add_tool_row(save=False)

        row.v_path.set("a")
        first = self.app._settings_save_job
        row.v_path.set("ab")
        second = self.app._settings_save_job

        self.assertIsNotNone(first)
        self.assertNotEqual(first, second, "前の予約を取り消して取り直すこと")




class TestToolLauncherSettings(unittest.TestCase):
    """保存と復元"""

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

    def _row(self, path):
        row = type("FakeRow", (), {})()
        row.v_path = TestToolLauncherSettings.FakeVar(path)
        return row

    def _app(self, paths):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.tabs = []
        app.tool_rows = [self._row(p) for p in paths]
        app._win_count_pref = None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_ton_access",
                     "v_freeze_8pages", "v_freeze_punish"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_emergency_key = TestToolLauncherSettings.FakeVar("p")
        app.v_start_key = TestToolLauncherSettings.FakeVar("")
        app.v_big_key = TestToolLauncherSettings.FakeVar("")
        return app

    def _save(self, app):
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._window_volume_settings = lambda: {}
            app._fog_early_read_setting = lambda: {}
            app._launch_options_setting = lambda: {}
            mainGUI.App._save_launch_settings(app)
        return saved

    def test_paths_are_saved(self):
        app = self._app([r"D:\a\one.exe", r"D:\b\two.exe"])

        saved = self._save(app)

        self.assertEqual(saved["tool_launchers"],
                         [r"D:\a\one.exe", r"D:\b\two.exe"])

    def test_blank_rows_are_dropped(self):
        app = self._app([r"D:\a\one.exe", "", "   "])

        saved = self._save(app)

        self.assertEqual(saved["tool_launchers"], [r"D:\a\one.exe"])

    def test_saved_paths_are_restored(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        added = []
        app._add_tool_row = added.append
        app.v_vrchat_exe = TestToolLauncherSettings.FakeVar()
        self._load(app, {"tool_launchers": [r"D:\a\one.exe", r"D:\b\two.exe"]})

        self.assertEqual(added, [r"D:\a\one.exe", r"D:\b\two.exe"])

    def test_a_legacy_file_without_the_key_is_fine(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        added = []
        app._add_tool_row = added.append

        self._load(app, {"tnl_path": "C:/list/my.tnl"})   # キーが無い

        self.assertEqual(added, [])

    def test_a_malformed_entry_is_skipped(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        added = []
        app._add_tool_row = added.append

        self._load(app, {"tool_launchers": [r"D:\a\one.exe", "", None, 42]})

        self.assertEqual(added, [r"D:\a\one.exe"])

    @staticmethod
    def _load(app, data):
        """_load_saved_settings のうち tool_launchers の復元部分だけを回す"""
        for path in data.get("tool_launchers", []) or []:
            if isinstance(path, str) and path.strip():
                app._add_tool_row(path)




class TestGuiClearsRoundChecks(unittest.TestCase):
    """GUI側。Tk変数はメインスレッドでしか触れない"""

    class FakeVar:
        def __init__(self, value=False):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    class BrokenVar:
        def get(self):
            return True

        def set(self, v):
            raise tk.TclError("application has been destroyed")

    def _tab(self, idx, on=True):
        make = TestGuiClearsRoundChecks.FakeVar
        tab = type("FakeTab", (), {})()
        tab.idx = idx
        tab.v_skip_rounds = {n: make(on) for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_continue_rounds = {n: make(on)
                                 for n in config.SKIP_ROUND_SELECTABLE}
        return tab

    def _checked(self, tab):
        return ({n for n, v in tab.v_skip_rounds.items() if v.get()},
                {n for n, v in tab.v_continue_rounds.items() if v.get()})

    def _app(self, tabs):
        app = type("FakeApp", (), {})()
        app.tabs = tabs
        app.after = MagicMock()
        app._do_clear_tab_round_settings = \
            lambda idx: mainGUI.App._do_clear_tab_round_settings(app, idx)
        return app

    def test_the_checks_are_cleared(self):
        app = self._app([self._tab(0)])

        mainGUI.App._do_clear_tab_round_settings(app, 1)

        self.assertEqual(self._checked(app.tabs[0]), (set(), set()))

    def test_only_that_window_is_cleared(self):
        app = self._app([self._tab(0), self._tab(1)])

        mainGUI.App._do_clear_tab_round_settings(app, 2)

        self.assertNotEqual(self._checked(app.tabs[0])[0], set(),
                            "他の窓は触らないこと")
        self.assertEqual(self._checked(app.tabs[1]), (set(), set()))

    def test_an_unknown_window_is_ignored(self):
        """窓数を減らした後に届いた"""
        app = self._app([self._tab(0)])

        mainGUI.App._do_clear_tab_round_settings(app, 9)

        self.assertNotEqual(self._checked(app.tabs[0])[0], set())

    def test_it_goes_through_the_main_thread(self):
        app = self._app([self._tab(0)])

        mainGUI.App._clear_tab_round_settings(app, 1)

        app.after.assert_called_once()
        delay, func = app.after.call_args[0]
        self.assertEqual(delay, 0)
        self.assertNotEqual(self._checked(app.tabs[0])[0], set(),
                            "after を待たずに触らないこと")
        func()
        self.assertEqual(self._checked(app.tabs[0]), (set(), set()))

    def test_a_destroyed_window_does_not_raise(self):
        app = self._app([self._tab(0)])
        app.tabs[0].v_skip_rounds["Classic"] = \
            TestGuiClearsRoundChecks.BrokenVar()

        mainGUI.App._do_clear_tab_round_settings(app, 1)   # 落ちないこと

    def test_a_destroyed_window_does_not_raise_from_the_thread(self):
        app = self._app([self._tab(0)])
        app.after = MagicMock(side_effect=tk.TclError("destroyed"))

        mainGUI.App._clear_tab_round_settings(app, 1)      # 落ちないこと

    def test_after_outside_the_mainloop_does_not_raise(self):
        """本物の Tk は mainloop の外だと RuntimeError を投げる。
        監視スレッドで投げると、そのスレッドごと黙って止まる"""
        app = self._app([self._tab(0)])
        app.after = MagicMock(
            side_effect=RuntimeError("main thread is not in main loop"))

        mainGUI.App._clear_tab_round_settings(app, 1)      # 落ちないこと
