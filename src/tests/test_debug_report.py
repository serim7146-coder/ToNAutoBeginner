"""debug.log と不具合の報告"""
from tests.support import *  # noqa: F401,F403




class TestDebugLogRich(unittest.TestCase):
    """debug.log を充実させる（守ること・例外・世代・速さ）"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "debug.log"
        p = patch.object(config, "DEBUG_LOG_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)
        DebugLog._seen.clear()
        self.addCleanup(DebugLog._seen.clear)

    def _text(self):
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    # ── 伏せる ───────────────────────────────
    def test_the_user_name_is_masked(self):
        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\alice"}):
            DebugLog.write(r"ログ=C:\Users\Alice\AppData\LocalLow\x.txt 別=c:/users/alice/y")
        text = self._text()
        self.assertNotIn("alice", text.lower())
        self.assertIn(r"%USERPROFILE%\AppData\LocalLow\x.txt", text)
        self.assertIn("%USERPROFILE%/y", text)

    def test_secrets_are_masked(self):
        self.addCleanup(DebugLog._secrets.discard, "s3cr3t-value-xyz")
        DebugLog.add_secret("s3cr3t-value-xyz")
        DebugLog.add_secret("ab")                       # 短すぎるものは覚えない
        DebugLog.write("鍵=s3cr3t-value-xyz ab")
        self.assertNotIn("s3cr3t", self._text())
        self.assertIn("鍵=*** ab", self._text())

    def test_the_obs_password_is_never_written(self):
        password = "obs-pass-9876"
        self.addCleanup(DebugLog._secrets.discard, password)
        mainGUI.with_obs_password({}, password)
        DebugLog.write(f"OBS 接続 {password}")
        self.assertNotIn(password, self._text())

    def test_env_values_are_never_written(self):
        env = Path(self._dir.name) / ".env"
        env.write_text("# comment\nSUPABASE_KEY=env-key-12345\nDISCORD_REPORT_WEBHOOK_URL='https://hook.example/abc'\n",
                       encoding="utf-8")
        for value in ("env-key-12345", "https://hook.example/abc"):
            self.addCleanup(DebugLog._secrets.discard, value)
        with patch.object(ConnectDB, "env_file_candidates", return_value=[env]):
            ConnectDB._register_env_secrets()
        DebugLog.write("送り先 https://hook.example/abc 鍵 env-key-12345")
        self.assertNotIn("env-key", self._text())
        self.assertNotIn("hook.example", self._text())

    def test_the_settings_lose_the_obs_items(self):
        self.assertEqual(config.REPORT_SETTINGS_EXCLUDE_KEYS, ("obs_password_dpapi", "obs_password"))
        app = type("FakeApp", (), {})()
        app.winfo_screenwidth = lambda: 2560
        app.winfo_screenheight = lambda: 1440
        data = {"obs_password_dpapi": "QUFBQUFBQQ==", "obs_password": "plain-pw", "win_count": 6}
        with patch.object(mainGUI, "load_settings", return_value=data):
            mainGUI.App._debug_environment(app)
        text = self._text()
        self.assertIn("[環境] 起動 版=", text)
        self.assertIn("画面=2560x1440", text)
        self.assertIn('"win_count": 6', text)
        for word in ("obs_password", "QUFBQUFBQQ", "plain-pw"):
            self.assertNotIn(word, text)

    # ── 画面のログ ───────────────────────────
    def test_every_screen_line_goes_to_the_debug_log(self):
        app = type("FakeApp", (), {})()
        app.after = lambda _ms, _fn=None: None
        mainGUI.App._log(app, "[窓2] ✅ Connecting")
        self.assertIn("] [画面] [窓2] ✅ Connecting", self._text())

    # ── 例外 ─────────────────────────────────
    def _boom(self, message="boom"):
        try:
            raise ValueError(message)
        except ValueError:
            return sys.exc_info()

    def test_exception_writes_the_traceback(self):
        try:
            raise KeyError("missing")
        except KeyError:
            DebugLog.exception("Test.where")
        text = self._text()
        self.assertIn("[例外] Test.where\nTraceback (most recent call last):", text)
        self.assertIn("KeyError: 'missing'", text)
        self.assertIn("test_exception_writes_the_traceback", text)

    def test_the_same_one_is_written_once_a_minute_with_the_count(self):
        clock = [1000.0]
        with patch.object(DebugLog.time, "monotonic", side_effect=lambda: clock[0]):
            for _ in range(3):
                DebugLog.report("Test.same", *self._boom())
            DebugLog.report("Test.other", *self._boom())         # 場所が違えば別
            DebugLog.report("Test.same", *self._boom("other"))   # メッセージが違えば別
            clock[0] += DebugLog.EXCEPTION_THROTTLE_SEC - 0.1
            DebugLog.report("Test.same", *self._boom())
            clock[0] += 0.2
            DebugLog.report("Test.same", *self._boom())
        text = self._text()
        self.assertEqual(text.count("[例外] Test.same"), 3)
        self.assertEqual(text.count("[例外] Test.same（同じものを 3 回省略）"), 1)
        self.assertEqual(text.count("[例外] Test.other"), 1)

    def _restore_hooks(self):
        sys_hook, thread_hook = sys.excepthook, threading.excepthook
        self.addCleanup(setattr, sys, "excepthook", sys_hook)
        self.addCleanup(setattr, threading, "excepthook", thread_hook)

    def test_the_sys_hook(self):
        self._restore_hooks()
        previous = MagicMock()
        sys.excepthook = previous
        DebugLog.install_hooks()
        info = self._boom("sys hook")
        sys.excepthook(*info)
        previous.assert_called_once_with(*info)
        self.assertIn("[例外] sys.excepthook\nTraceback", self._text())
        self.assertIn("ValueError: sys hook", self._text())
        DebugLog.install_hooks()
        self.assertIs(sys.excepthook.__closure__ is not None, True)
        sys.excepthook(*self._boom("again"))
        self.assertEqual(previous.call_count, 2, "二重に差し替えない")

    def test_the_threading_hook(self):
        self._restore_hooks()
        previous = MagicMock()
        threading.excepthook = previous
        DebugLog.install_hooks()

        def fail():
            raise RuntimeError("in a thread")

        th = threading.Thread(target=fail, name="worker-x")
        th.start()
        th.join()
        previous.assert_called_once()
        self.assertIn("[例外] threading.excepthook（worker-x）\nTraceback", self._text())
        self.assertIn("RuntimeError: in a thread", self._text())

    def test_the_tk_hook(self):
        self._restore_hooks()
        root = type("Root", (), {})()
        previous = MagicMock()
        root.report_callback_exception = previous
        DebugLog.install_hooks(root)
        info = self._boom("tk")
        root.report_callback_exception(*info)
        previous.assert_called_once_with(*info)
        self.assertIn("[例外] Tk report_callback_exception\nTraceback", self._text())

    def test_a_swallowed_exception_is_now_written(self):
        """黙って捨てていた所の例: 前面化の失敗"""
        with patch.object(WindowOperator.win32gui, "IsIconic", side_effect=OSError("gone")):
            self.assertFalse(WindowOperator.focus_window(0x123))
        text = self._text()
        self.assertIn("[例外] WindowOperator.focus_window", text)
        self.assertIn("OSError: gone", text)
        self.assertIn("[操作] 前面化 hwnd=0x123 → 失敗", text)

    def test_a_swallowed_osc_failure_is_written(self):
        client = OSCClient.OSCClient(9)
        client._sock = MagicMock()
        client._sock.sendto.side_effect = OSError("no route")
        self.assertFalse(client.send("/input/Jump", 1))
        self.assertIn("[例外] OSCClient.send", self._text())

    # ── 世代と速さ ───────────────────────────
    def test_three_generations_are_kept(self):
        with patch.object(config, "DEBUG_LOG_MAX_BYTES", 60):
            for i in range(6):
                DebugLog.write(f"line{i} " + "x" * 60)
        names = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(names, ["debug.log", "debug.log.1", "debug.log.2", "debug.log.3"])
        newest_first = [self.path] + [self.path.with_name(f"debug.log.{n}") for n in (1, 2, 3)]
        self.assertEqual([p.read_text(encoding="utf-8").split("] ", 1)[1][:5] for p in newest_first],
                         ["line5", "line4", "line3", "line2"], "古いものは消える")

    def test_the_limit_is_20_mb(self):
        self.assertEqual(config.DEBUG_LOG_MAX_BYTES, 20 * 1024 * 1024)
        self.assertEqual(DebugLog.GENERATIONS, 3)

    def test_ten_thousand_lines_are_fast_enough(self):
        started = time.perf_counter()
        for i in range(10000):
            DebugLog.write(f"[画面] [窓1] line {i}")
        took = time.perf_counter() - started
        self.assertLess(took, 15.0, f"{took:.1f}秒（画面のスレッドから書くので遅すぎないこと）")
        self.assertEqual(len(self._text().splitlines()), 10000)




class TestDebugLogTraces(unittest.TestCase):
    """読んだイベント・状態の変化・操作の跡"""

    def setUp(self):
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=3)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def test_events(self):
        monitor = self._monitor()
        with patch.object(LogMonitor.threading, "Thread"), patch.object(ConnectDB, "register_round"):
            for line in ("This round is taking place at Facility (12) and the round type is Classic",
                         "Killers have been set - 5 0 0 // Round type is Classic",
                         "Equipping 29.", "You died.", "RoundOver",
                         "[Behaviour] Joining wrld_x:1~friends(usr_me)~region(jp)",
                         "[Behaviour] OnPlayerJoined someone (usr_abc)"):
                monitor._process("2026.10.01 12:00:00 Debug      -  " + line)
        events = [m for m in self.written if "[事象]" in m]
        self.assertIn("[窓3] [事象] round_start 種類=Classic マップ=12", events)
        self.assertIn("[窓3] [事象] killers_set 種類=Classic テラー=[5]", events)
        self.assertIn("[窓3] [事象] item_equip アイテム=29", events)
        self.assertIn("[窓3] [事象] you_died", events)
        self.assertIn("[窓3] [事象] round_over", events)
        self.assertIn("[窓3] [事象] joining 公開範囲=friends", events)
        self.assertIn("[窓3] [事象] player_joined", events, "入ってきた人の名前は書かない")
        self.assertFalse(any("someone" in m or "usr_abc" in m for m in self.written))

    def test_network_object_lines_are_not_events(self):
        monitor = self._monitor()
        monitor._process("2026.10.01 12:00:00 Debug      -  [NetworkProcessing] Ignoring TrySetOwner "
                         "attempt on [20] Immortal Snail because x already owner")
        self.assertFalse(any("[事象]" in m for m in self.written), self.written)

    def test_freezes(self):
        # 前のテストが張ったまま残したものを数えない（張っている窓の数を見るため）
        for reset in (SharedState.equip_freeze_reset, SharedState.continue_round_reset,
                      SharedState.speed_freeze_reset, SharedState.round_freeze_reset):
            reset()
        st = WindowState(window_idx=2)
        other = WindowState(window_idx=4)
        self.addCleanup(SharedState.equip_freeze_reset)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.speed_freeze_reset)
        self.addCleanup(SharedState.round_freeze_reset)
        SharedState.equip_freeze_start(st)
        SharedState.equip_freeze_start(other)
        with patch.object(SharedState, "_return_front_when_free"):
            SharedState.equip_freeze_end(st)
        SharedState.continue_round_start(st)
        SharedState.speed_freeze_start(st)
        SharedState.round_freeze_start(st)
        with patch.object(SharedState, "_return_front_when_free"):
            SharedState.continue_round_end(st)
            SharedState.speed_freeze_end(st)
            SharedState.round_freeze_end(st)
        states = [m for m in self.written if m.startswith("[状態]")]
        self.assertEqual(states, [
            "[状態] 窓2 装備待ちフリーズを張った（張っている窓: 1）",
            "[状態] 窓4 装備待ちフリーズを張った（張っている窓: 2）",
            "[状態] 窓2 装備待ちフリーズを解いた（張っている窓: 1）",
            "[状態] 窓2 続行フリーズを張った（張っている窓: 1）",
            "[状態] 窓2 速度検知フリーズを張った（張っている窓: 1）",
            "[状態] 窓2 ラウンド突入フリーズを張った（張っている窓: 1）",
            "[状態] 窓2 続行フリーズを解いた（張っている窓: 0）",
            "[状態] 窓2 速度検知フリーズを解いた（張っている窓: 0）",
            "[状態] 窓2 ラウンド突入フリーズを解いた（張っている窓: 0）",
        ])

    def test_the_monitor_gives_its_number_to_the_state(self):
        self.assertEqual(self._monitor().st.window_idx, 3)

    def test_operations(self):
        with patch.object(WindowOperator, "_focus_window", return_value=True), \
             patch.object(WindowOperator, "_click"), \
             patch.object(WindowOperator, "_hold_key_background", return_value=True):
            WindowOperator.focus_window(0x10)
            WindowOperator.click()
            WindowOperator.hold_key_background(0x10, "w", 0.5)
        self.assertIn("[操作] 前面化 hwnd=0x10 → 成功", self.written)
        self.assertIn("[操作] クリック", self.written)
        self.assertIn("[操作] 背面キー w 0.50秒 hwnd=0x10", self.written)

    def test_osc_moves_and_the_chase(self):
        st = WindowState(window_idx=5)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1, osc_port=9000), st,
                                           lambda: True, lambda _m: None)
        ex._osc = MagicMock()
        ex.move("forward", 1.0)
        ex.move_forward_left(2.0, 0.5)
        with patch.object(ActionExecutor.threading, "Thread"):
            ex._start_chase_locked("forward")
            ex._stop_chase_locked()
        self.assertIn("[窓5] [操作] 移動 forward 1.00秒（OSC）", self.written)
        self.assertIn("[窓5] [操作] 前進 2.00秒＋左 0.50秒（OSC）", self.written)
        self.assertIn("[窓5] [操作] チェイス開始 forward", self.written)
        self.assertIn("[窓5] [操作] チェイス終了", self.written)

    def test_the_begin_move_says_which_one_and_how_it_ended(self):
        for round_type, kind, fwd, left in (("Punished", "Punished後", config.BEGIN_FORWARD_SEC_LATER,
                                             config.BEGIN_LEFT_SEC_LATER),
                                            ("Classic", "通常", config.BEGIN_FORWARD_SEC, config.BEGIN_LEFT_SEC)):
            self.written.clear()
            st = WindowState(window_idx=1, round_type=round_type)
            ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1, osc_port=9000), st,
                                               lambda: True, lambda _m: None)
            ex._osc = MagicMock()
            ex._osc.press_multi.return_value = True
            ex._begin_move()
            ex._osc.press_multi.assert_called_once_with([("/input/MoveForward", fwd),
                                                     ("/input/MoveLeft", left)], stop=ex._stopped)
            self.assertIn(f"[操作] [窓1] Begin前の移動: {kind} 前進{fwd}秒・左{left}秒"
                          f"（round_type={round_type}）", self.written)
            self.assertIn("[操作] [窓1] Begin前の移動: 最後までやった", self.written)
            self.assertTrue(st.begin_move_done)

    def test_a_begin_move_that_could_not_send(self):
        st = WindowState(window_idx=2, round_type="Classic")
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=1, osc_port=9000), st,
                                           lambda: True, lambda _m: None)
        ex._osc = MagicMock()
        ex._osc.press_multi.return_value = False
        ex._begin_move()
        self.assertIn("[操作] [窓2] Begin前の移動: 途中で止めた（OSC を送れない）", self.written)
        ex._osc = None
        with patch.object(WindowOperator, "hold_key_background", return_value=False):
            ex._begin_move()
        self.assertIn("[操作] [窓2] Begin前の移動: 途中で止めた（移動キーを送れない）", self.written)
        ex._osc = MagicMock()
        ex._osc.press_multi.side_effect = OSError("x")
        with self.assertRaises(OSError):
            ex._begin_move()
        self.assertIn("[操作] [窓2] Begin前の移動: 途中で止めた（OSError）", self.written)

    def test_a_sound(self):
        with tempfile.TemporaryDirectory() as d:
            voice = Path(d) / "continue.mp3"
            voice.write_bytes(b"x")
            with patch.object(PlaySound.threading, "Thread"):
                PlaySound.play_sound(str(voice))
        self.assertTrue(any(m == "[音声] 再生 continue.mp3" for m in self.written), self.written)

    def test_sound_errors_go_to_the_debug_log(self):
        with patch("builtins.print"):
            PlaySound._say("音声再生エラー: test")
        self.assertIn("[音声] 音声再生エラー: test", self.written)

    def test_the_stop_reason(self):
        for reason, expected in ((None, "ボタン"), ("緊急停止キー", "緊急停止キー")):
            self.written.clear()
            app = MagicMock()
            app._stop_reason = reason
            app.monitors = []
            with patch.object(Recorder, "stop_all"):
                mainGUI.App._stop(app)
            self.assertIn(f"[環境] 停止（理由: {expected}）", self.written)
            self.assertIsNone(app._stop_reason, "次の停止に持ち越さない")
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('self._stop_reason = "緊急停止キー"', src)
        self.assertIn('self._stop_reason = "ウィンドウを閉じた"', src)

    def test_the_start_environment(self):
        app = type("FakeApp", (), {})()
        mon = MagicMock()
        mon.window_idx = 2
        mon.cfg = WindowConfig(hwnd=0x20, osc_port=9010, osc_out_port=9011, auto_begin=True,
                               do_skip=False, skip_rounds={"Fog"})
        app.monitors = [mon]
        app._window_volume_settings = lambda: {"window_volume_enabled": False}
        with patch.object(WindowOperator, "window_rect", return_value=(0, 0, 800, 600)):
            mainGUI.App._debug_start(app)
        first = next(m for m in self.written if m.startswith("[環境] 開始 窓2"))
        for part in ("hwnd=0x20", "位置=(0, 0, 800, 600)", "OSC=9010/9011", "自動Begin=True",
                     "自爆=False", "自爆ラウンド=['Fog']"):
            self.assertIn(part, first)
        self.assertTrue(any(m.startswith("[環境] 開始 共通 放置=") and "霧の即時判定=" in m
                            for m in self.written))

    def test_db_sends_write_nothing_about_the_round(self):
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
             patch.object(ConnectDB.threading, "Thread", RunNow), \
             patch.object(ConnectDB.urllib.request, "urlopen", side_effect=urlopen), \
             patch.object(RoundStore, "default_store", return_value=MagicMock()), \
             patch("builtins.print"):
            ConnectDB.register_round("Fog", [101, 7], 12, -13, round_time=1767225610)
        self.assertEqual(len(sent), 1)
        self.assertFalse(any("Supabase" in m or "101" in m or "register_round" in m or "[DB]" in m
                             for m in self.written), self.written)




class TestBugReportContent(unittest.TestCase):
    """送る中身（本文・zip・伏せる・大きさ）。本物の Discord へは送らない"""

    NOW = datetime(2026, 10, 1, 12, 34, 56)
    HOME = r"C:\Users\Alice"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        p = patch.object(BugReport.os.path, "expanduser", return_value=self.HOME)
        p.start()
        self.addCleanup(p.stop)
        self.debug = self.dir / "debug.log"
        self.debug.write_text("debug line\n", encoding="utf-8")
        self.vrchat = self.dir / "output_log.txt"
        self.vrchat.write_text("vrchat line C:/users/alice/AppData\n", encoding="utf-8")
        p = patch.object(BeginMiss, "folder", return_value=self.dir / "begin_miss")   # 撮影はこのテストの場所
        p.start()
        self.addCleanup(p.stop)

    def _zip(self, include=None, window=2, vrchat=None, settings=None, gui="画面 C:\\USERS\\ALICE\\x\n"):
        include = include if include is not None else {k: True for k, _l in BugReport.ATTACHMENTS}
        name, data = BugReport.build_report(
            "動かない C:\\Users\\Alice\\y", window, include, gui,
            str(self.vrchat) if vrchat is None else vrchat,
            settings if settings is not None else {"win_count": 6, "obs_password_dpapi": "QUFB",
                                                   "obs_password": "plain"},
            self.NOW, debug_log_path=self.debug, version="v9.9.9")
        z = zipfile.ZipFile(io.BytesIO(data))
        return name, data, {n: z.read(n).decode("utf-8") for n in z.namelist()}

    def test_the_content(self):
        text = BugReport.content("v9.9.9", 2, "止まった", self.NOW)
        self.assertEqual(text, "🐞 不具合報告 v9.9.9 / 窓2 / 2026-10-01 12:34:56\n止まった")
        self.assertIn("/ 窓なし /", BugReport.content("v9.9.9", 0, "x", self.NOW))
        long = BugReport.content("v9.9.9", 1, "あ" * 3000, self.NOW)
        self.assertEqual(len(long), BugReport.CONTENT_MAX)
        self.assertTrue(long.endswith("…（続きは report.txt）"))

    def test_everything_selected(self):
        name, _data, files = self._zip()
        self.assertEqual(name, "report_20261001_123456.zip")
        self.assertEqual(set(files), {"report.txt", "gui_log.txt", "debug_log.txt",
                                      "vrchat_log_window2.txt", "settings.json"})
        settings = json.loads(files["settings.json"])
        self.assertEqual(settings, {"win_count": 6})
        report = files["report.txt"]
        for part in ("不具合報告 v9.9.9", "窓: 窓2", "時刻: 2026-10-01 12:34:56", "Windows:",
                     "実行:", "- 画面のログ出力: gui_log.txt", "- 設定: settings.json", "動かない"):
            self.assertIn(part, report)

    def test_only_the_selected_ones_with_reasons(self):
        include = {BugReport.GUI_LOG: False, BugReport.DEBUG_LOG: True,
                   BugReport.VRCHAT_LOG: False, BugReport.SETTINGS: False, BugReport.BEGIN_MISS: False}
        _n, _d, files = self._zip(include=include)
        self.assertEqual(set(files), {"report.txt", "debug_log.txt"})
        self.assertEqual(files["report.txt"].count("（未選択）"), 4)

    def test_vrchat_log_reasons(self):
        for window, path, reason in ((0, None, "窓を選んでいない"), (3, "", "窓に未割り当て"),
                                     (3, str(self.dir / "none.txt"), "ファイルが無い")):
            _n, _d, files = self._zip(window=window, vrchat=path if path is not None else str(self.vrchat))
            self.assertNotIn("vrchat_log_window3.txt", files)
            self.assertIn(f"選んだ窓の VRChat のログ: 入れていません（{reason}）", files["report.txt"])
        self.debug.unlink()
        _n, _d, files = self._zip()
        self.assertIn("debug.log: 入れていません（ファイルが無い）", files["report.txt"])

    def test_the_user_name_is_masked_everywhere(self):
        _n, _d, files = self._zip()
        for name, text in files.items():
            self.assertNotIn("alice", text.lower(), name)
        self.assertIn("%USERPROFILE%/AppData", files["vrchat_log_window2.txt"])
        self.assertIn("%USERPROFILE%\\x", files["gui_log.txt"])
        self.assertIn("%USERPROFILE%\\y", files["report.txt"])

    def test_secrets_are_masked(self):
        self.addCleanup(DebugLog._secrets.discard, "hook-secret-777")
        DebugLog.add_secret("hook-secret-777")
        _n, _d, files = self._zip(gui="送り先 hook-secret-777\n")
        self.assertNotIn("hook-secret-777", files["gui_log.txt"])

    def _big_log(self, lines=40000):
        rnd = random.Random(7)
        with open(self.vrchat, "w", encoding="utf-8") as f:
            for i in range(lines):
                f.write(f"LINE-{i:06d} " + "".join(rnd.choice("0123456789abcdef") for _ in range(60)) + "\n")

    def test_a_big_log_is_cut_to_fit_from_a_line_start(self):
        self._big_log()
        with patch.object(config, "REPORT_MAX_BYTES", 600 * 1024), \
             patch.object(config, "REPORT_VRCHAT_LOG_START_BYTES", 2 * 1024 * 1024), \
             patch.object(config, "REPORT_VRCHAT_LOG_MIN_BYTES", 64 * 1024):
            _n, data, files = self._zip()
        self.assertLessEqual(len(data), 600 * 1024)
        text = files["vrchat_log_window2.txt"]
        self.assertTrue(text.startswith("LINE-"), text[:20])
        self.assertTrue(text.endswith("\n"))
        self.assertIn("LINE-039999", text, "末尾を入れる")
        self.assertIn("（末尾）", files["report.txt"])

    def test_too_big_drops_the_vrchat_log(self):
        self._big_log()
        with patch.object(config, "REPORT_MAX_BYTES", 40 * 1024), \
             patch.object(config, "REPORT_VRCHAT_LOG_START_BYTES", 2 * 1024 * 1024), \
             patch.object(config, "REPORT_VRCHAT_LOG_MIN_BYTES", 512 * 1024):
            _n, data, files = self._zip()
        self.assertNotIn("vrchat_log_window2.txt", files)
        self.assertIn("入れていません（大きすぎて収まらない）", files["report.txt"])

    def test_the_real_limits(self):
        self.assertEqual(config.REPORT_MAX_BYTES, int(9.5 * 1024 * 1024))
        self.assertEqual(config.REPORT_VRCHAT_LOG_START_BYTES, 8 * 1024 * 1024)
        self.assertEqual(config.REPORT_VRCHAT_LOG_MIN_BYTES, 256 * 1024)
        self.assertEqual(config.REPORT_DEBUG_LOG_MAX_BYTES, 5 * 1024 * 1024)
        self.assertEqual(config.REPORT_COOLDOWN_SEC, 60)

    def test_tails_start_at_a_line(self):
        path = self.dir / "t.txt"
        path.write_bytes(b"first line\nsecond line\nthird\n")
        self.assertEqual(BugReport.tail_bytes(path, 15), b"third\n")
        self.assertEqual(BugReport.tail_bytes(path, 1000), b"first line\nsecond line\nthird\n")
        self.assertEqual(BugReport.tail_text("aaa\nbbb\nccc\n", 6), "ccc\n")

    def test_the_debug_log_adds_the_older_one(self):
        self.debug.write_bytes(b"new1\nnew2\n")
        self.debug.with_name("debug.log.1").write_bytes(b"old1\nold2\nold3\n")
        self.assertEqual(BugReport.debug_log_tail(self.debug, 18), b"old3\nnew1\nnew2\n")
        self.assertEqual(BugReport.debug_log_tail(self.debug, 8), b"new2\n", "新しい方で足りれば足さない")




class TestBugReportSend(unittest.TestCase):
    """送り方（multipart・UA・失敗の文言に URL を出さない）"""

    URL = "https://discord.example/api/webhooks/123/SECRET-TOKEN"

    def _parts(self, body: bytes, ctype: str):
        boundary = ctype.split("boundary=")[1].encode()
        return [p for p in body.split(b"--" + boundary) if p.strip() not in (b"", b"--")]

    def test_the_multipart(self):
        body, ctype = BugReport.multipart("本文 @everyone", "report_x.zip", b"PK\x03\x04zip")
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        parts = self._parts(body, ctype)
        self.assertEqual(len(parts), 2)
        head, payload = parts[0].split(b"\r\n\r\n", 1)
        self.assertIn(b'name="payload_json"', head)
        data = json.loads(payload.rstrip(b"\r\n").decode("utf-8"))
        self.assertEqual(data, {"content": "本文 @everyone", "allowed_mentions": {"parse": []}})
        head, file_data = parts[1].split(b"\r\n\r\n", 1)
        self.assertIn(b'name="files[0]"; filename="report_x.zip"', head)
        self.assertEqual(file_data.rstrip(b"\r\n"), b"PK\x03\x04zip")

    def test_a_good_send(self):
        seen = []

        def opener(req, timeout=None):
            seen.append((req, timeout))
            res = MagicMock()
            res.__enter__ = MagicMock(return_value=res)
            res.__exit__ = MagicMock(return_value=False)
            res.status = 200
            return res

        ok, message = BugReport.send(self.URL, "本文", "r.zip", b"zip", opener=opener)
        self.assertEqual((ok, message), (True, "送信しました"))
        req, timeout = seen[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(req.get_header("User-agent"), f"ToNAutoBeginner/{config.APP_VERSION}")
        self.assertEqual(req.get_method(), "POST")

    def test_failures_never_show_the_url(self):
        errors = (urllib.error.HTTPError(self.URL, 429, f"Too Many {self.URL}", {}, None),
                  urllib.error.URLError(f"cannot reach {self.URL}"),
                  OSError(f"boom {self.URL}"))
        for error in errors:
            ok, message = BugReport.send(self.URL, "x", "r.zip", b"z",
                                         opener=MagicMock(side_effect=error))
            self.assertFalse(ok)
            self.assertNotIn("discord", message)
            self.assertNotIn("SECRET", message)
        self.assertEqual(BugReport.send(self.URL, "x", "r.zip", b"z",
                                        opener=MagicMock(side_effect=errors[0]))[1],
                         "送れませんでした（HTTP 429）")

    def test_no_url_does_not_send(self):
        opener = MagicMock()
        self.assertEqual(BugReport.send("", "x", "r.zip", b"z", opener=opener),
                         (False, "送り先が設定されていません"))
        opener.assert_not_called()

    def test_the_url_comes_from_the_env_and_is_kept_secret(self):
        self.addCleanup(DebugLog._secrets.discard, self.URL)
        with patch.dict(os.environ, {"DISCORD_REPORT_WEBHOOK_URL": self.URL}):
            self.assertEqual(BugReport.webhook_url(), self.URL)
        self.assertEqual(DebugLog.scrub(f"post {self.URL}"), "post ***")
        with patch.dict(os.environ, {"DISCORD_REPORT_WEBHOOK_URL": ""}):
            self.assertEqual(BugReport.webhook_url(), "")




class TestBugReportDialog(unittest.TestCase):
    """画面（本物の Tk の App。送信は差し替え）"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app._report_sent_at = None
        self.logs = []
        p = patch.object(mainGUI.App, "_log", lambda _s, m: self.logs.append(m))
        p.start()
        self.addCleanup(p.stop)
        if len(self.app.tabs) < 3:
            count = len(self.app.tabs)
            self.app._rebuild_tabs(3)
            self.addCleanup(self.app._rebuild_tabs, count)

    def _dialog(self):
        self.app._open_report()
        dialog = self.app._report_dialog
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        return dialog

    def test_the_button_is_last_on_the_row(self):
        last = self.app.btn_start.master.pack_slaves()[-1]
        self.assertEqual(last.cget("text"), "報告")

    def test_the_window_defaults_to_the_selected_tab(self):
        self.app.nb.select(self.app.tabs[1])
        self.assertEqual(self._dialog().v_window.get(), "窓2")

    def test_all_attachments_are_on_and_the_note_is_shown(self):
        dialog = self._dialog()
        self.assertTrue(all(v.get() for v in dialog.v_include.values()))
        self.assertEqual(set(dialog.v_include), {k for k, _l in BugReport.ATTACHMENTS})
        self.assertIn("一緒にいた人の名前", dialog.REPORT_NOTE)

    def test_an_empty_requirement_is_not_sent(self):
        dialog = self._dialog()
        with patch.object(BugReport, "send") as send, \
             patch.object(BugReport, "webhook_url", return_value="https://x.example/h"):
            dialog._send()
        send.assert_not_called()
        self.assertEqual(dialog.v_status.get(), "何が起きたかを書いてください")

    def test_no_url_is_explained_and_not_sent(self):
        dialog = self._dialog()
        dialog.text.insert("1.0", "止まった")
        with patch.object(BugReport, "send") as send, \
             patch.object(BugReport, "webhook_url", return_value=""):
            dialog._send()
        send.assert_not_called()
        self.assertEqual(dialog.v_status.get(), "送り先が設定されていません")
        self.assertIn("[報告] 送り先が設定されていません", self.logs)

    def _send_ok(self, dialog, result=(True, "送信しました")):
        built = []

        def build(*args, **kwargs):
            built.append(args)
            return "r.zip", b"zip"

        with patch.object(BugReport, "webhook_url", return_value="https://x.example/h"), \
             patch.object(BugReport, "build_report", side_effect=build), \
             patch.object(BugReport, "send", return_value=result) as send, \
             patch.object(mainGUI.threading, "Thread", RunNow):
            dialog._send()
            self.app.update()
        return built, send

    def test_a_good_send_closes_and_starts_the_cooldown(self):
        self.app.tabs[2].v_log.set(r"C:\logs\output_log_3.txt")
        dialog = self._dialog()
        dialog.text.insert("1.0", "止まった")
        dialog.v_window.set("窓3")
        built, send = self._send_ok(dialog)
        send.assert_called_once()
        self.assertEqual(built[0][0], "止まった")
        self.assertEqual(built[0][1], 3)
        self.assertEqual(built[0][4], r"C:\logs\output_log_3.txt", "選んだ窓のログ")
        self.assertIn("[報告] 送信しました", self.logs)
        self.assertFalse(dialog.winfo_exists(), "成功したら閉じる")
        self.assertGreater(self.app._report_cooldown_remaining(), 59)

        again = self._dialog()
        again.text.insert("1.0", "もう一度")
        self.assertEqual(str(again.btn_send.cget("state")), "disabled")
        with patch.object(BugReport, "send") as send2, \
             patch.object(BugReport, "webhook_url", return_value="https://x.example/h"):
            again._send()
        send2.assert_not_called()
        self.assertIn("続けては送れません", again.v_status.get())

    def test_the_cooldown_ends_after_60_seconds(self):
        self.app._report_sent_at = time.monotonic() - config.REPORT_COOLDOWN_SEC - 1
        dialog = self._dialog()
        self.assertEqual(str(dialog.btn_send.cget("state")), "normal")

    def test_a_failure_stays_open(self):
        dialog = self._dialog()
        dialog.text.insert("1.0", "止まった")
        self._send_ok(dialog, result=(False, "送れませんでした（HTTP 429）"))
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(dialog.v_status.get(), "送れませんでした（HTTP 429）")
        self.assertIn("[報告] 送れませんでした（HTTP 429）", self.logs)
        self.assertEqual(self.app._report_cooldown_remaining(), 0.0)




class TestDebugLog(unittest.TestCase):
    """公開ログとは別のファイルへ、時刻つきで1行ずつ追記する"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "sub" / "debug.log"
        p = patch.object(config, "DEBUG_LOG_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def _lines(self, path=None):
        return (path or self.path).read_text(encoding="utf-8").splitlines()

    def test_each_line_starts_with_the_time_and_is_appended(self):
        DebugLog.write("one")
        DebugLog.write("two")

        lines = self._lines()
        self.assertEqual(len(lines), 2)
        for line, text in zip(lines, ("one", "two")):
            self.assertRegex(line, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] " + text + "$")

    def test_a_full_file_moves_to_dot_one(self):
        with patch.object(config, "DEBUG_LOG_MAX_BYTES", 60):
            DebugLog.write("old " + "x" * 60)
            DebugLog.write("older1")                     # ここで回る
            DebugLog.write("y" * 60)
            DebugLog.write("newest")                     # もう一度回る（前の .1 は消える）

        backup = self.path.with_name("debug.log.1")
        self.assertEqual([l.split("] ", 1)[1] for l in self._lines(backup)], ["older1", "y" * 60])
        self.assertEqual([l.split("] ", 1)[1] for l in self._lines()], ["newest"])

    def test_an_unwritable_path_does_not_raise(self):
        blocker = Path(self._dir.name) / "file"
        blocker.write_text("x", encoding="utf-8")
        with patch.object(config, "DEBUG_LOG_PATH", blocker / "debug.log"):
            DebugLog.write("nowhere")                    # 親がファイル。例外を出さない

    def test_threads_do_not_mix_lines(self):
        def worker(n):
            for i in range(100):
                DebugLog.write(f"t{n}-{i}-" + "z" * 50)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        lines = self._lines()
        self.assertEqual(len(lines), 600)
        for line in lines:
            self.assertRegex(line, r"^\[[^\]]+\] t\d-\d+-z{50}$")

    def test_the_real_path_is_under_appdata_next_to_settings(self):
        self.assertEqual(_real_paths["debug"].name, "debug.log")
        self.assertEqual(_real_paths["debug"].parent, _real_paths["settings"].parent)
        self.assertEqual(config.DEBUG_LOG_MAX_BYTES, 20 * 1024 * 1024)
