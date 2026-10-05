"""音声・窓の音量・OBS 録画"""
from tests.support import *  # noqa: F401,F403




class TestOBSClient(unittest.TestCase):
    """obs-websocket v5 の最小クライアント（自前の WebSocket）"""

    def _server(self, **kw):
        server = FakeOBSServer(**kw)
        self.addCleanup(server.close)
        return server

    # ── 1 認証文字列 ────────────────────────────
    def test_the_authentication_string_follows_the_protocol_document(self):
        """入力は obs-websocket 公式 docs/generated/protocol.md の
        「Creating an authentication string」の例の値をそのまま写した。

        同じ文書の Identify の例に出てくる "Dj6cLS+jrNA0HpCArRg0Z/Fc+YHdt2FQfAvgD1mip6Y="
        は、この入力から文書の手順どおりに計算した値と一致しない（Identify の
        例は別の値を載せているだけ）ので、期待値には使っていない。期待値は
        文書の4手順を Node.js の crypto で別に計算した値で、obs-websocket-js の
        実装（sha256(msg + salt) → sha256(hash + challenge)）とも同じ手順。
        """
        self.assertEqual(
            OBSClient.auth_string("supersecretpassword",
                                  "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=",
                                  "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="),
            "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4=")

    # ── 2 マスク ───────────────────────────────
    def test_a_client_frame_is_masked(self):
        payload = json.dumps({"op": 6, "d": {"requestType": "StartRecord"}}).encode()
        frame = OBSClient.encode_frame(payload, mask_key=b"\x01\x02\x03\x04")

        self.assertTrue(frame[1] & 0x80, "マスクビット")
        self.assertEqual(frame[2:6], b"\x01\x02\x03\x04")
        self.assertNotEqual(frame[6:], payload, "平文のまま送っていない")
        unmasked = bytes(b ^ frame[2 + i % 4] for i, b in enumerate(frame[6:]))
        self.assertEqual(unmasked, payload)

    def test_every_frame_on_the_wire_is_masked(self):
        server = self._server()
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=3)

        self.assertEqual(client.connect(), (True, ""))
        client.request("GetRecordStatus")
        client.close()
        server.close()

        self.assertTrue(server.masked)
        self.assertTrue(all(server.masked), server.masked)

    # ── 3 長さ ────────────────────────────────
    def test_every_length_class_round_trips(self):
        for n in (0, 125, 126, 65535, 65536, 70000):
            payload = bytes(i % 251 for i in range(n))
            frame = OBSClient.encode_frame(payload)
            buf = io.BytesIO(frame)

            fin, opcode, got = OBSClient.read_frame(buf.read)

            self.assertEqual((fin, opcode, got), (True, 0x1, payload), n)
            self.assertEqual(buf.read(), b"", f"{n}: 読み残しが無い")
            expected_len_code = 126 if 126 <= n <= 0xFFFF else (127 if n > 0xFFFF else n)
            self.assertEqual(frame[1] & 0x7F, expected_len_code, n)

    def test_an_unmasked_server_frame_is_read(self):
        for n in (5, 300, 70000):
            payload = b"x" * n
            buf = io.BytesIO(FakeOBSServer.frame(payload))

            self.assertEqual(OBSClient.read_frame(buf.read), (True, 0x1, payload), n)

    # ── 4 認証あり・なし ─────────────────────────
    def test_identify_without_authentication(self):
        server = self._server()
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=3)

        ok, reason = client.connect()
        got = client.request("GetRecordStatus")
        client.close()
        server.close()

        self.assertEqual((ok, reason), (True, ""))
        self.assertEqual(got, (True, {"outputActive": False}, ""))
        identify = server.received[0]
        self.assertEqual(identify["op"], 1)
        self.assertEqual(identify["d"]["rpcVersion"], 1)
        self.assertNotIn("authentication", identify["d"])

    def test_identify_with_authentication(self):
        server = self._server(password="hunter2", record_active=True)
        client = OBSClient.OBSClient("127.0.0.1", server.port, "hunter2", timeout=3)

        self.assertEqual(client.connect(), (True, ""))
        self.assertEqual(client.request("GetRecordStatus"),
                         (True, {"outputActive": True}, ""))
        client.close()

    def test_a_wrong_password_is_reported_without_the_password(self):
        server = self._server(password="hunter2")
        client = OBSClient.OBSClient("127.0.0.1", server.port, "wrong-pass", timeout=3)

        ok, reason = client.connect()

        self.assertFalse(ok)
        self.assertIn("認証に失敗", reason)
        self.assertNotIn("wrong-pass", reason)
        self.assertNotIn("wrong-pass", repr(client))

    def test_a_ping_is_answered_with_a_pong(self):
        server = self._server(ping_first=True)
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=3)
        client.connect()

        self.assertTrue(client.request("StartRecord")[0])
        client.request("GetRecordStatus")     # pong を受けたのを記録させる
        client.close()
        server.close()

        self.assertEqual(server.pong, b"are you there")

    # ── 5 タイムアウト ─────────────────────────
    def test_a_silent_server_times_out_without_raising(self):
        server = self._server(respond=False)
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=0.3)

        t0 = time.monotonic()
        ok, reason = client.connect()
        elapsed = time.monotonic() - t0

        self.assertFalse(ok)
        self.assertIn("タイムアウト", reason)
        self.assertLess(elapsed, 2.0)

    def test_nobody_listening_is_a_plain_failure(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()                           # 誰も待ち受けていないポート

        ok, reason = OBSClient.OBSClient("127.0.0.1", port, timeout=1).connect()

        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_a_request_before_connecting_fails_quietly(self):
        self.assertEqual(OBSClient.OBSClient().request("StartRecord"),
                         (False, {}, "未接続です"))




class TestRecordPlan(unittest.TestCase):
    """1本の録画を複数の窓で共有する（偽の OBS と偽の時計で）"""

    def setUp(self):
        self.now = 1000.0
        self.logs = []

    def _plan(self, obs, **kw):
        return Recorder.RecordPlan(obs.factory, self.logs.append,
                                   clock=lambda: self.now, tail_sec=3.0, **kw)

    def _advance(self, plan, sec):
        self.now += sec
        plan.tick()

    # ── 6 / 7 ─────────────────────────────────
    def test_one_window_starts_then_stops_three_seconds_after_round_over(self):
        obs = FakeOBS()
        plan = self._plan(obs)

        plan.continue_start(1)
        self.assertEqual(obs.count("StartRecord"), 1)
        self._advance(plan, 60)
        plan.round_over(1)
        self._advance(plan, 3.0)

        self.assertEqual(obs.count("StopRecord"), 1)
        self.assertFalse(plan.we_started)

    def test_it_does_not_stop_before_the_tail(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        plan.round_over(1)

        self._advance(plan, 2.9)

        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertEqual(plan.next_deadline(), 1003.0)

    def test_dying_early_keeps_recording_until_round_over(self):
        """死亡では止めない。止めるのは RoundOver+3秒だけ"""
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)

        self._advance(plan, 120)

        self.assertEqual(obs.count("StopRecord"), 0)

    # ── 8 / 9 複窓 ──────────────────────────────
    def test_two_windows_share_one_recording_until_the_last_round_over(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        self._advance(plan, 5)
        plan.continue_start(2)

        self.assertEqual(obs.count("StartRecord"), 1)
        plan.round_over(1)
        self._advance(plan, 10)
        self.assertEqual(obs.count("StopRecord"), 0, "窓2がまだ続行中")
        plan.round_over(2)
        self._advance(plan, 2.9)
        self.assertEqual(obs.count("StopRecord"), 0)
        self._advance(plan, 0.1)
        self.assertEqual(obs.count("StopRecord"), 1)

    def test_a_new_continue_during_the_tail_keeps_recording(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        plan.round_over(1)
        self._advance(plan, 1.0)

        plan.continue_start(2)
        self._advance(plan, 10)

        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertEqual(obs.count("StartRecord"), 1, "録り直さない")
        plan.round_over(2)
        self._advance(plan, 3)
        self.assertEqual(obs.count("StopRecord"), 1)

    def test_the_same_window_continuing_again_cancels_its_own_stop(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        plan.round_over(1)

        plan.continue_start(1)
        self._advance(plan, 10)

        self.assertEqual(obs.count("StopRecord"), 0)

    def test_a_round_over_without_a_continue_does_nothing(self):
        obs = FakeOBS()
        plan = self._plan(obs)

        plan.round_over(1)
        self._advance(plan, 10)

        self.assertEqual(obs.calls, [])

    # ── 10 手動の録画 ────────────────────────────
    def test_a_recording_started_by_hand_is_left_alone(self):
        obs = FakeOBS(recording=True)
        plan = self._plan(obs)

        plan.continue_start(1)
        plan.round_over(1)
        self._advance(plan, 5)
        plan.stop_all()

        self.assertEqual(obs.count("StartRecord"), 0)
        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertTrue(obs.recording)

    # ── 11 繋がらない ──────────────────────────
    def test_a_missing_obs_warns_once_and_tries_again_next_time(self):
        obs = FakeOBS(fail="OBSに接続できません")
        plan = self._plan(obs)

        for _ in range(3):
            plan.continue_start(1)
            plan.round_over(1)
            self._advance(plan, 5)

        warnings = [m for m in self.logs if "OBSに接続できません" in m]
        self.assertEqual(len(warnings), 1, self.logs)
        obs.fail = ""
        plan.continue_start(1)
        self.assertEqual(obs.count("StartRecord"), 1, "次の続行でまた試す")

    def test_a_failing_client_never_raises(self):
        def broken():
            raise OSError("boom")
        plan = Recorder.RecordPlan(broken, self.logs.append, clock=lambda: self.now)

        plan.continue_start(1)          # 例外が出ないこと
        plan.round_over(1)
        plan.tick()
        plan.stop_all()

        self.assertFalse(plan.we_started)

    # ── 12 停止 ─────────────────────────────────
    def test_stop_all_stops_only_what_we_started(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)

        plan.stop_all()
        plan.stop_all()

        self.assertEqual(obs.count("StopRecord"), 1)
        self.assertEqual(plan.next_deadline(), None, "予約は捨てる")

    # ── 13 上限 ─────────────────────────────────
    def test_the_recording_stops_at_the_limit(self):
        obs = FakeOBS()
        plan = self._plan(obs, max_sec=900)
        plan.continue_start(1)

        self._advance(plan, 899)
        self.assertEqual(obs.count("StopRecord"), 0)
        self._advance(plan, 1)

        self.assertEqual(obs.count("StopRecord"), 1)
        self.assertTrue(any("上限" in m for m in self.logs), self.logs)

    def test_the_default_limit_is_configured(self):
        self.assertEqual(config.OBS_RECORD_MAX_SEC, 900)
        self.assertEqual(config.OBS_RECORD_TAIL_SEC, 3.0)




class TestRecorderThread(unittest.TestCase):
    """窓のスレッドからは投げるだけ。OBS がどうなっていても待たない"""

    def test_a_disabled_recorder_does_nothing(self):
        obs = FakeOBS()
        rec = Recorder.Recorder(client_factory=obs.factory)

        rec.on_continue_start(1)
        rec.on_round_over(1)
        rec.stop_all()

        self.assertIsNone(rec._thread, "スレッドも立てない")
        self.assertEqual(obs.calls, [])

    def test_the_default_is_disabled(self):
        self.assertFalse(Recorder.Recorder().enabled)

    def test_a_hanging_obs_does_not_block_the_caller(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class Hanging:
            def connect(self):
                release.wait(5)             # 応答しない OBS
                return False, "タイムアウト"

            def request(self, *_a):
                return False, {}, ""

            def close(self):
                pass

        rec = Recorder.Recorder(client_factory=Hanging)
        rec.configure(True, log=lambda _m: None)

        t0 = time.monotonic()
        for _ in range(5):
            rec.on_continue_start(1)
            rec.on_round_over(1)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.1)

    def test_the_worker_records_and_stops(self):
        obs = FakeOBS()
        logs = []
        rec = Recorder.Recorder(client_factory=obs.factory)
        rec.configure(True, log=logs.append)
        rec._plan.tail_sec = 0.05

        rec.on_continue_start(1)
        rec.on_round_over(1)
        deadline = time.monotonic() + 3
        while obs.count("StopRecord") == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(obs.calls, ["GetRecordStatus", "StartRecord", "StopRecord"])

    def test_stop_all_can_wait_for_the_stop_to_be_sent(self):
        obs = FakeOBS()
        rec = Recorder.Recorder(client_factory=obs.factory)
        rec.configure(True, log=lambda _m: None)
        rec.on_continue_start(1)

        rec.stop_all(wait_sec=3)

        self.assertEqual(obs.count("StopRecord"), 1)

    def test_the_password_is_not_logged(self):
        logs = []
        rec = Recorder.Recorder()
        rec.configure(True, "127.0.0.1", 1, "hunter2", log=logs.append)

        rec._plan.continue_start(1)       # 繋がらないポート。警告を出させる

        self.assertTrue(logs)
        self.assertFalse(any("hunter2" in m for m in logs), logs)




class TestRecordingHooks(unittest.TestCase):
    """LogMonitor: 続行と決まった瞬間に録画を始め、RoundOver で止める予約をする"""

    FOG_KEY = "Fog/霧"
    CLASSIC_KEY = "Classic/クラシック"
    PURSUER = 99

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        for p in (patch.object(ConnectDB, "register_round"),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound")):
            p.start()
            self.addCleanup(p.stop)
        self.started = patch.object(Recorder, "on_continue_start").start()
        self.over = patch.object(Recorder, "on_round_over").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_list_source, None)

    def _monitor(self, keep_on=None, round_type="Classic", fog=False,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=3)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.st.fog = fog
        return monitor

    @staticmethod
    def _judge(monitor, ids, round_type="Classic"):
        """通常判定。Classic は _on_killers だと亜種待ち（別スレッド）を挟むので、
        待ちの後に走る判定そのものを呼ぶ"""
        monitor.st.terror_ids = list(ids)
        monitor._decide_with_keep_on_set(round_type)

    # ── 15 通常判定 ─────────────────────────────
    def test_a_continue_starts_the_recording_once(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})

        self._judge(monitor, [42])
        self._judge(monitor, [42])          # 同じラウンドで判定し直しても

        self.started.assert_called_once_with(3)

    def test_a_skip_does_not_record(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})

        self._judge(monitor, [7])

        self.started.assert_not_called()

    def test_an_open_special_round_target_does_not_record(self):
        """DTM/Waldo の3クラ解放狙いはアナウンスが鳴らない"""
        monitor = self._monitor()

        self._judge(monitor, [LogMonitor.DTM_TERROR_ID])

        self.assertTrue(monitor.st.is_continue_round, "前提: 続行はする")
        self.started.assert_not_called()

    # ── 16 / 17 グループ ─────────────────────────
    def test_a_sabotage_star_wish_records(self):
        monitor = self._monitor(round_type="Sabotage",
                                instance_type=config.INSTANCE_HOSHIIMO)
        with patch.object(monitor, "_group_decision", return_value=GroupRound.WANTED):
            self.assertTrue(monitor._apply_group_decision("Sabotage"))

        self.started.assert_called_once_with(3)

    def test_an_everyone_continues_round_does_not_record(self):
        monitor = self._monitor(round_type="8 Pages",
                                instance_type=config.INSTANCE_HOSHIIMO)
        with patch.object(monitor, "_group_decision", return_value=GroupRound.CONTINUE):
            self.assertTrue(monitor._apply_group_decision("8 Pages"))

        self.started.assert_not_called()

    # ── 18 霧の前倒し判明 ─────────────────────────
    def test_a_fog_enrage_records_at_that_moment(self):
        monitor = self._monitor({self.FOG_KEY: {self.PURSUER}}, round_type="Fog",
                                fog=True)

        monitor._on_enrage("The Pursuer")

        self.started.assert_called_once_with(3)
        monitor._on_killers([self.PURSUER], "Fog", revealed=True)
        self.started.assert_called_once_with(3)

    # ── 19 RoundOver ─────────────────────────────
    def test_round_over_is_passed_on(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False

        monitor._process("2026.09.20 12:00:00 Debug      -  RoundOver")

        self.over.assert_called_once_with(3)

    # ── 20 放置モード ────────────────────────────
    def test_hands_free_does_not_record(self):
        """放置モード中は録らない（依頼者の判断。続行とフリーズはそのまま）"""
        SharedState.set_hands_free(True)
        monitor = self._monitor({self.CLASSIC_KEY: {42}})

        self._judge(monitor, [42])

        self.assertTrue(monitor.st.is_continue_round, "続行はする")
        self.started.assert_not_called()
        PlaySound.play_sound.assert_not_called()

    def test_hands_free_follows_the_announcement_per_window(self):
        """放置モードが効くのはprivateの窓だけ（_hands_free）。干し芋の窓では
        トグルがONでもアナウンスが鳴るので、録画もアナウンスと揃える"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(round_type="Sabotage",
                                instance_type=config.INSTANCE_HOSHIIMO)
        with patch.object(monitor, "_group_decision", return_value=GroupRound.WANTED):
            monitor._apply_group_decision("Sabotage")

        PlaySound.play_sound.assert_called_once_with("continue.mp3")
        self.started.assert_called_once_with(3)

    def test_hands_free_sabotage_star_in_private_does_not_record(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(round_type="Sabotage")
        with patch.object(monitor, "_group_decision", return_value=GroupRound.WANTED):
            monitor._apply_group_decision("Sabotage")

        self.assertTrue(monitor.st.is_continue_round)
        self.started.assert_not_called()

    def test_entering_hands_free_mid_recording_still_passes_round_over(self):
        """録画の途中で放置に入っても、RoundOver+3秒で普通に止まるように"""
        monitor = self._monitor()
        monitor.cfg.auto_begin = False
        SharedState.set_hands_free(True)

        monitor._process("2026.09.20 12:00:00 Debug      -  RoundOver")

        self.over.assert_called_once_with(3)




class TestWindowVolumeCategory(unittest.TestCase):
    """分類: 続行 ＞ フリーズ窓（フリーズを張った窓）＞ その他"""

    def test_continue(self):
        self.assertEqual(WindowVolume.category_of(WindowState(is_continue_round=True)),
                         WindowVolume.CONTINUE)

    def test_each_freeze_this_window_holds(self):
        for held in ("equip_freeze_held", "speed_freeze_held", "round_freeze_held"):
            st = WindowState()
            setattr(st, held, True)
            self.assertEqual(WindowVolume.category_of(st), WindowVolume.FREEZE, held)

    def test_continue_wins_over_a_freeze(self):
        st = WindowState(is_continue_round=True)
        st.equip_freeze_held = True
        st.continue_freeze_held = True
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.CONTINUE)

    def test_dtm_waldo_continue_without_the_freeze_is_continue(self):
        st = WindowState(is_continue_round=True)
        st.continue_freeze_held = False
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.CONTINUE)

    def test_run_is_other_even_with_the_round_freeze(self):
        for held in (False, True):
            st = WindowState(round_type="Run")
            st.round_freeze_held = held
            self.assertEqual(WindowVolume.category_of(st), WindowVolume.OTHER, held)

    def test_a_dtm_waldo_continue_is_other(self):
        st = WindowState(is_continue_round=True)
        st.open_special_continue = True
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.OTHER)

    def test_a_dtm_waldo_continue_with_an_equip_wait_is_a_freeze_window(self):
        st = WindowState(is_continue_round=True)
        st.open_special_continue = True
        st.equip_freeze_held = True
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.FREEZE)

    def test_a_normal_continue_is_continue(self):
        st = WindowState(is_continue_round=True, round_type="Classic")
        st.open_special_continue = False
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.CONTINUE)

    def test_nothing_is_other(self):
        self.assertEqual(WindowVolume.category_of(WindowState()), WindowVolume.OTHER)
        st = WindowState()
        st.continue_freeze_held = True      # 続行の印が無ければ続行ではない
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.OTHER)




class TestWindowVolumeController(unittest.TestCase):
    """0.5秒ごとに窓の分類を見て、変わったときだけ音量を設定する。停止で元に戻す"""

    def setUp(self):
        self.a = WindowState()                       # その他
        self.b = WindowState(is_continue_round=True)  # 続行
        self.c = WindowState()                       # まだ音を出していない窓
        self.audio = _FakeAudio({0xA: 11, 0xB: 22, 0xC: 33}, {11: 0.8, 22: 0.6})
        self.logs = []
        self.ctl = WindowVolume.VolumeController(
            lambda: [(1, 0xA, self.a), (2, 0xB, self.b), (3, 0xC, self.c)],
            self.logs.append, audio=self.audio)
        self.ctl.set_levels(100, 90, 0)
        self.ctl.set_enabled(True)

    def test_off_touches_nothing(self):
        self.ctl.set_enabled(False)

        for _ in range(3):
            self.ctl._tick_safely()
        self.ctl.restore()

        self.assertEqual(self.audio.calls, [], "読みも書きもしない")
        self.assertEqual(self.logs, [])

    def test_the_default_is_off(self):
        ctl = WindowVolume.VolumeController(lambda: [(1, 0xA, self.a)], self.logs.append,
                                            audio=self.audio)
        ctl.tick()
        self.assertEqual(self.audio.calls, [])
        self.assertFalse(config.DEFAULT_WINDOW_VOLUME_ENABLED)
        self.assertEqual((config.DEFAULT_WINDOW_VOLUME_CONTINUE,
                          config.DEFAULT_WINDOW_VOLUME_FREEZE,
                          config.DEFAULT_WINDOW_VOLUME_OTHER), (100, 100, 0))

    def test_each_window_gets_its_category_once(self):
        self.ctl.tick()
        self.ctl.tick()

        self.assertEqual(self.audio.sets(), [("set", 11, 0.0), ("set", 22, 1.0)],
                         "同じ分類が続くあいだは2回目以降設定しない")
        self.assertEqual(self.logs, ["[窓1] VRChat の音量 → その他 0%",
                                     "[窓2] VRChat の音量 → 続行 100%"])

    def test_a_freeze_window_gets_the_freeze_level(self):
        self.a.round_freeze_held = True
        self.ctl.tick()
        self.assertIn(("set", 11, 0.9), self.audio.sets())

    def test_a_manual_change_is_left_alone_until_the_category_changes(self):
        self.ctl.tick()
        self.audio.volumes[11] = 0.7                # 利用者が音量ミキサーで変えた
        self.audio.calls.clear()

        self.ctl.tick()
        self.assertEqual(self.audio.sets(), [], "読んだ値が違うだけでは設定し直さない")

        self.a.equip_freeze_held = True
        self.ctl.tick()
        self.assertEqual(self.audio.sets(), [("set", 11, 0.9)])

    def test_the_original_is_remembered_once_and_restored(self):
        self.ctl.tick()
        self.a.equip_freeze_held = True
        self.ctl.tick()                             # 分類が変わっても元の音量は最初のまま
        self.audio.calls.clear()

        self.ctl.restore()

        self.assertEqual(sorted(self.audio.sets()), [("set", 11, 0.8), ("set", 22, 0.6)])
        self.assertEqual(self.logs[-1], "[停止] VRChat の音量を元に戻しました（2窓）")
        self.audio.calls.clear()
        self.ctl.restore()
        self.assertEqual(self.audio.calls, [], "戻し終えたら覚えを消す")

    def test_only_windows_it_set_and_still_alive_are_restored(self):
        self.ctl.tick()
        del self.audio.volumes[22]                  # 窓2を閉じた
        self.audio.calls.clear()

        self.ctl.restore()

        self.assertEqual(self.audio.sets(), [("set", 11, 0.8)], "窓3（設定していない）・窓2（閉じた）は戻さない")
        self.assertEqual(self.logs[-1], "[停止] VRChat の音量を元に戻しました（1窓）")

    def test_turning_it_off_restores_at_once_and_on_again_remembers_anew(self):
        self.ctl.tick()
        self.ctl.set_enabled(False)
        self.audio.calls.clear()

        self.ctl.tick()
        self.assertEqual(sorted(self.audio.sets()), [("set", 11, 0.8), ("set", 22, 0.6)])
        self.audio.calls.clear()
        self.ctl.tick()
        self.assertEqual(self.audio.calls, [], "OFF のあいだは触らない")

        self.audio.volumes[11] = 0.5                # OFF のあいだに手で変えた
        self.ctl.set_enabled(True)
        self.ctl.tick()
        self.ctl.set_enabled(False)
        self.audio.calls.clear()
        self.ctl.tick()
        self.assertIn(("set", 11, 0.5), self.audio.sets(), "ON に戻したら覚え直す")

    def test_a_slider_change_takes_effect_on_the_next_tick(self):
        self.ctl.tick()
        self.audio.calls.clear()

        self.ctl.set_levels(80, 90, 30)
        self.ctl.tick()

        self.assertEqual(self.audio.sets(), [("set", 11, 0.3), ("set", 22, 0.8)])

    def test_a_window_without_a_session_is_tried_again_later(self):
        self.ctl.tick()
        self.assertNotIn(33, [c[1] for c in self.audio.sets()], "音を出していない窓には設定しない")

        self.audio.volumes[33] = 0.4                # 音を出し始めた
        self.ctl.tick()

        self.assertIn(("set", 33, 0.0), self.audio.sets())
        self.audio.calls.clear()
        self.ctl.restore()
        self.assertIn(("set", 33, 0.4), self.audio.sets())

    def test_a_failed_set_is_not_remembered(self):
        self.audio.pids[0xD] = 44
        d = WindowState()
        ctl = WindowVolume.VolumeController(lambda: [(4, 0xD, d)], self.logs.append,
                                            audio=self.audio)
        ctl.set_enabled(True)
        self.audio.volumes[44] = 0.5
        real = self.audio.set_volume
        self.audio.set_volume = lambda pid, level: False
        ctl.tick()
        self.audio.set_volume = real
        ctl.restore()
        self.assertEqual(self.audio.sets(), [], "設定できなかった窓は戻す対象にしない")

    def test_a_failure_is_logged_once_and_the_watch_goes_on(self):
        self.audio.raise_on_set = OSError("boom")

        for _ in range(3):
            self.ctl._tick_safely()
        self.audio.error = "COM を初期化できません"
        self.ctl._tick_safely()

        warnings = [m for m in self.logs if m.startswith("⚠ VRChat の音量を変えられません")]
        self.assertEqual(len(warnings), 1, self.logs)
        self.assertEqual(len([c for c in self.audio.sets() if c[1] == 11]), 4, "毎回試している")

    def test_an_audio_error_alone_is_logged_once(self):
        self.audio.error = "音量を読めません（x）"
        self.ctl._tick_safely()
        self.audio.error = "音量を読めません（y）"
        self.ctl._tick_safely()
        self.assertEqual(self.logs.count("⚠ VRChat の音量を変えられません（音量を読めません（x））"), 1)
        self.assertEqual(sum(m.startswith("⚠") for m in self.logs), 1)

    def test_the_thread_sets_then_restores_on_stop(self):
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01):
            self.ctl.start()
            for _ in range(200):
                if len(self.audio.sets()) >= 2:
                    break
                time.sleep(0.01)
            self.ctl.stop()

        self.assertEqual(self.audio.volumes[11], 0.8)
        self.assertEqual(self.audio.volumes[22], 0.6)
        self.assertEqual(self.audio.released, 1, "COM はそのスレッドで片付ける")
        self.assertTrue(any("元に戻しました（2窓）" in m for m in self.logs), self.logs)

    def test_the_thread_survives_an_exception(self):
        self.audio.raise_on_set = OSError("boom")
        with patch.object(config, "WINDOW_VOLUME_POLL_SEC", 0.01):
            self.ctl.start()
            for _ in range(200):
                if len(self.audio.sets()) >= 6:
                    break
                time.sleep(0.01)
            alive = self.ctl._thread.is_alive()
            self.ctl.stop()
        self.assertTrue(alive)
        self.assertGreaterEqual(len(self.audio.sets()), 6)




class TestWindowVolumeSettings(unittest.TestCase):
    """settings.json: 古いファイル（キーが無い）でも既定値。壊れた値も既定値"""

    def test_an_old_file_gives_the_defaults(self):
        enabled, levels = WindowVolume.levels_from_settings({})
        self.assertFalse(enabled)
        self.assertEqual(levels, {WindowVolume.CONTINUE: 100, WindowVolume.FREEZE: 100,
                                  WindowVolume.OTHER: 0})

    def test_saved_values_are_read_and_clamped(self):
        enabled, levels = WindowVolume.levels_from_settings({
            "window_volume_enabled": True, "window_volume_continue": 75,
            "window_volume_freeze": 150, "window_volume_other": -5})
        self.assertTrue(enabled)
        self.assertEqual(levels, {WindowVolume.CONTINUE: 75, WindowVolume.FREEZE: 100,
                                  WindowVolume.OTHER: 0})

    def test_broken_values_fall_back(self):
        enabled, levels = WindowVolume.levels_from_settings({
            "window_volume_enabled": "yes", "window_volume_continue": "80",
            "window_volume_freeze": True, "window_volume_other": None})
        self.assertFalse(enabled)
        self.assertEqual(levels, {WindowVolume.CONTINUE: 100, WindowVolume.FREEZE: 100,
                                  WindowVolume.OTHER: 0})

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _app(self):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app.v_wvol_enabled = self.Var(False)
        app.v_wvol = {WindowVolume.CONTINUE: self.Var(100), WindowVolume.FREEZE: self.Var(100),
                      WindowVolume.OTHER: self.Var(0)}
        app._window_volume_values = lambda: mainGUI.App._window_volume_values(app)
        return app

    def test_they_are_saved_and_loaded_back(self):
        app = self._app()
        mainGUI.App._load_window_volume_settings(app, {
            "window_volume_enabled": True, "window_volume_continue": 70,
            "window_volume_freeze": 40, "window_volume_other": 10})

        saved = mainGUI.App._window_volume_settings(app)

        self.assertEqual(saved, {"window_volume_enabled": True, "window_volume_continue": 70,
                                 "window_volume_freeze": 40, "window_volume_other": 10})

    def test_the_save_includes_the_keys(self):
        app = self._app()
        stored = {}
        fake = _with_cancel_key(type("FakeApp", (), {})())
        fake.tabs, fake.tool_rows, fake._win_count_pref = [], [], None
        for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin",
                     "v_join_world", "v_ton_access", "v_freeze_8pages", "v_freeze_punish",
                     "v_emergency_key", "v_start_key", "v_big_key", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(fake, name, self.Var(""))
        fake.v_freeze_rounds = {}
        fake._window_volume_settings = lambda: mainGUI.App._window_volume_settings(app)
        fake._fog_early_read_setting = lambda: {}
        fake._launch_options_setting = lambda: {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(fake)
        for key in ("window_volume_enabled", "window_volume_continue",
                    "window_volume_freeze", "window_volume_other"):
            self.assertIn(key, stored)

    def test_the_gui_hands_the_values_to_the_running_controller(self):
        app = self._app()
        app.v_wvol_enabled.set(True)
        app.v_wvol[WindowVolume.OTHER].set(25)
        app._window_volume = MagicMock()

        mainGUI.App._apply_window_volume_settings(app)

        app._window_volume.set_levels.assert_called_once_with(100, 100, 25)
        app._window_volume.set_enabled.assert_called_once_with(True)

    def test_nothing_happens_when_not_running(self):
        app = self._app()
        app._window_volume = None
        mainGUI.App._apply_window_volume_settings(app)       # 落ちない




@unittest.skipUnless(sys.platform == "win32" and _audio_device_available(), "音声デバイスが無い")
class TestWindowVolumeCom(unittest.TestCase):
    """本物の Core Audio を読むだけ（音量は変えない）"""

    def tearDown(self):
        WindowVolume.release_com()

    def test_reading_does_not_raise(self):
        volumes = WindowVolume.read_volumes([os.getpid(), 0])
        self.assertIsInstance(volumes, dict)
        for level in volumes.values():
            self.assertTrue(0.0 <= level <= 1.0)
        self.assertIsNone(WindowVolume.take_error())

    def test_the_iid_is_isimpleaudiovolume(self):
        g = WindowVolume.IID_ISimpleAudioVolume
        self.assertEqual((g.d1, g.d2, g.d3), (0x87CE5498, 0x68D6, 0x44E5))

    def test_a_missing_window_has_no_pid(self):
        self.assertEqual(WindowVolume.pid_of(0), 0)



# ═══════════════════════════════════════════════
#  PlaySound.py
# ═══════════════════════════════════════════════
class TestPlaySound(unittest.TestCase):
    def test_get_sound_volume(self):
        result = PlaySound.get_sound_volume()
        self.assertEqual(result, 1.0)

    def test_set_sound_volume(self):
        global sound_volume
        PlaySound.set_sound_volume(0.3)
        result = PlaySound.get_sound_volume()
        self.assertEqual(result, 0.3)

    class _SyncThread:
        """再生スレッドをその場で実行して結果を確定させる"""

        def __init__(self, target=None, daemon=None, **_kw):
            self._target = target

        def start(self):
            self._target()

    def _play(self, path: str, exists: bool = True, fail_on: str = "",
              keep_warned: bool = False):
        """play_sound を同期実行し、送られたMCIコマンドと出力を返す"""
        commands: list[str] = []

        def fake_mci(command: str) -> int:
            commands.append(command)
            return 259 if fail_on and command.startswith(fail_on) else 0

        if not keep_warned:
            PlaySound._warned.clear()   # 「1回だけ出す」警告をテスト間で持ち越さない
        with patch.object(PlaySound, "_mci", side_effect=fake_mci), \
             patch.object(PlaySound, "_mci_error_text", return_value="MCIのエラー"), \
             patch.object(PlaySound.threading, "Thread", self._SyncThread), \
             patch("pathlib.Path.exists", return_value=exists), \
             patch("builtins.print") as mock_print:
            PlaySound.play_sound(path)
        return commands, [str(c.args[0]) for c in mock_print.call_args_list]

    def test_play_sound_opens_sets_volume_plays_and_closes(self):
        PlaySound.set_sound_volume(1.0)
        commands, _out = self._play("voice/continue.mp3")

        self.assertEqual(len(commands), 4)
        alias = commands[0].split("alias ")[1]
        self.assertTrue(commands[0].startswith('open "'), commands[0])
        self.assertIn(str(Path("voice/continue.mp3").absolute()), commands[0])
        self.assertIn("type mpegvideo", commands[0])
        self.assertEqual(commands[1], f"setaudio {alias} volume to 1000")
        self.assertEqual(commands[2], f"play {alias} wait")
        self.assertEqual(commands[3], f"close {alias}")

    def test_wav_uses_waveaudio_and_unknown_extension_omits_type(self):
        commands, _out = self._play("voice/se.wav")
        self.assertIn("type waveaudio", commands[0])

        commands, _out = self._play("voice/se.ogg")
        self.assertNotIn(" type ", commands[0])

    def test_each_playback_uses_a_unique_alias(self):
        """エイリアスを固定すると2回目の再生が1回目を止めてしまう"""
        first, _ = self._play("voice/continue.mp3")
        second, _ = self._play("voice/continue.mp3")

        self.assertNotEqual(first[0].split("alias ")[1],
                            second[0].split("alias ")[1])

    def test_volume_is_converted_to_0_1000(self):
        PlaySound.set_sound_volume(0.3)
        commands, _out = self._play("voice/continue.mp3")
        self.assertTrue(commands[1].endswith(" volume to 300"), commands[1])

        PlaySound.set_sound_volume(1.0)

    def test_close_runs_even_when_play_fails(self):
        """closeを飛ばすとデバイスが解放されず、いずれ再生できなくなる"""
        commands, out = self._play("voice/continue.mp3", fail_on="play")

        self.assertTrue(commands[-1].startswith("close "), commands)
        self.assertTrue(any("MCIのエラー" in line for line in out),
                        "失敗を標準出力に出すこと")

    def test_open_failure_skips_playback(self):
        commands, out = self._play("voice/continue.mp3", fail_on="open")

        self.assertEqual(len(commands), 1, "openに失敗したら以降は送らない")
        self.assertTrue(any("MCIのエラー" in line for line in out))

    def test_long_path_falls_back_to_the_short_path(self):
        """MCIは概ね128文字以上のパスを開けないので8.3形式で開き直す"""
        commands: list[str] = []
        short = r"C:\\DIR~1\\SND~1.MP3"

        def fake_mci(command: str) -> int:
            commands.append(command)
            return 304 if command.startswith("open") and short not in command else 0

        PlaySound._warned.clear()
        with patch.object(PlaySound, "_mci", side_effect=fake_mci), \
             patch.object(PlaySound, "_short_path", return_value=short), \
             patch.object(PlaySound.threading, "Thread", self._SyncThread), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.print") as mock_print:
            PlaySound.play_sound("voice/continue.mp3")

        self.assertEqual(len(commands), 5, commands)
        self.assertIn(short, commands[1])
        self.assertIn("type mpegvideo", commands[1], "種別は元の拡張子から決める")
        self.assertTrue(commands[3].startswith("play "), commands)
        mock_print.assert_not_called()

    def test_volume_failure_warns_once_and_keeps_playing(self):
        """waveaudioはsetaudio非対応。再生は続け、警告は拡張子ごとに1回だけ"""
        first, out1 = self._play("voice/se.wav", fail_on="setaudio")
        second, out2 = self._play("voice/se.wav", fail_on="setaudio",
                                  keep_warned=True)

        self.assertTrue(first[2].startswith("play "), first)
        self.assertTrue(first[3].startswith("close "), first)
        self.assertEqual(len(out1), 1, out1)
        self.assertEqual(out2, [], "2回目以降は黙ること")

    def test_play_sound_skips_if_not_exists(self):
        """ファイルが存在しない場合はMCIを呼ばない"""
        commands, _out = self._play("voice/notfound.mp3", exists=False)
        self.assertEqual(commands, [])

    def test_play_sound_skips_empty_path(self):
        """空文字の場合はMCIを呼ばない"""
        commands, _out = self._play("")
        self.assertEqual(commands, [])
