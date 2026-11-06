"""スマホ通知（ntfy。PhoneNotify・LogMonitor._notify_phone・設定の画面）。

本物のネットワークへは送らない（tests/support.py が urlopen を断る。送る側は偽物に差し替える）。
本物の入力・クリップボードにも触らない"""
from tests.support import *  # noqa: F401,F403

import PhoneNotify
import QueueWatch

TOPIC = "tonab-unit_test-topic-0123456789abcdef"


class _Sender:
    """urllib.request.urlopen の偽物。送ろうとした Request と timeout を貯める"""

    def __init__(self, error=None, gate=None):
        self.requests, self.timeouts, self.error, self.gate = [], [], error, gate

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return contextlib.nullcontext()

    def body(self, index=-1):
        return json.loads(self.requests[index].data.decode("utf-8"))


class TestText(unittest.TestCase):
    def test_continue_and_all_continue_text(self):
        self.assertEqual(PhoneNotify.round_text(PhoneNotify.KIND_CONTINUE, 3, "Classic", "Sewers", "Arkus"),
                         ("窓3 続行ラウンド", "Classic / Sewers\nテラー: Arkus"))
        self.assertEqual(PhoneNotify.round_text(PhoneNotify.KIND_ALL_CONTINUE, 1, "Run", "Facility", "A, B"),
                         ("窓1 全続行", "Run / Facility\nテラー: A, B"))

    def test_unknown_terrors_and_missing_map(self):
        self.assertEqual(PhoneNotify.round_text(PhoneNotify.KIND_ALL_CONTINUE, 2, "Fog", "Sewers", ""),
                         ("窓2 全続行", "Fog / Sewers\nテラー: 不明"))
        self.assertEqual(PhoneNotify.round_text(PhoneNotify.KIND_CONTINUE, 2, "Fog", "", "X")[1],
                         "Fog\nテラー: X")

    def test_queue_text(self):
        self.assertEqual(PhoneNotify.queue_ready_text(), (PhoneNotify.APP_TITLE, "待機列に入れるようになりました"))

    def test_the_map_name_loses_its_number(self):
        self.assertEqual(PhoneNotify.map_name_from_raw("Cheese Maze (59)"), "Cheese Maze")
        self.assertEqual(PhoneNotify.map_name_from_raw("Sewers"), "Sewers")
        self.assertEqual(PhoneNotify.map_name_from_raw(""), "")

    def test_a_generated_topic_is_valid_and_hard_to_guess(self):
        topics = {PhoneNotify.new_topic() for _ in range(20)}
        self.assertEqual(len(topics), 20)
        for topic in topics:
            self.assertRegex(topic, r"^[-_A-Za-z0-9]{1,64}$")
            self.assertTrue(PhoneNotify.is_valid_topic(topic))
            self.assertGreaterEqual(len(topic), 30)

    def test_what_is_not_a_topic(self):
        for bad in ("", None, 5, "a b", "日本語", "a/b", "x" * 65, "a.b"):
            self.assertFalse(PhoneNotify.is_valid_topic(bad), bad)
        self.assertTrue(PhoneNotify.is_valid_topic("x" * 64))


class TestSend(unittest.TestCase):
    def setUp(self):
        self.addCleanup(DebugLog._secrets.discard, TOPIC)

    def send(self, sender, results=None):
        done = None if results is None else (lambda ok, why: results.append((ok, why)))
        with patch.object(urllib.request, "urlopen", sender):
            thread = PhoneNotify.send(TOPIC, "見出し", "本文", on_done=done)
            thread.join(5)
        return thread

    def test_the_body_is_json_with_topic_title_message_and_priority_4(self):
        sender = _Sender()
        self.send(sender)
        request = sender.requests[0]
        self.assertEqual(request.full_url, config.PHONE_NOTIFY_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(sender.body(), {"topic": TOPIC, "title": "見出し", "message": "本文", "priority": 4})
        self.assertEqual(sender.timeouts, [config.PHONE_NOTIFY_TIMEOUT_SEC])

    def test_the_url_has_no_topic_and_the_user_agent_is_generic(self):
        sender = _Sender()
        self.send(sender)
        request = sender.requests[0]
        self.assertNotIn(TOPIC, request.full_url)
        self.assertEqual(request.get_header("User-agent"), "ToNAutoBeginner")
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")

    def test_it_reports_success_to_on_done(self):
        sender, results = _Sender(), []
        self.send(sender, results)
        self.assertEqual(results, [(True, "")])

    def test_a_failure_only_reports_it_and_does_not_raise(self):
        sender, results = _Sender(error=OSError("接続できません")), []
        thread = self.send(sender, results)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertIn("OSError", results[0][1])
        self.assertEqual(len(sender.requests), 1, "やり直さない")

    def test_a_failure_text_never_carries_the_topic(self):
        sender, results = _Sender(error=OSError(f"bad {TOPIC} here")), []
        self.send(sender, results)
        self.assertNotIn(TOPIC, results[0][1])

    def test_it_runs_on_a_daemon_thread_and_does_not_block_the_caller(self):
        gate = threading.Event()
        sender = _Sender(gate=gate)
        with patch.object(urllib.request, "urlopen", sender):
            started = time.monotonic()
            thread = PhoneNotify.send(TOPIC, "t", "m")
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertTrue(thread.daemon)
            gate.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_an_error_in_on_done_does_not_escape(self):
        def boom(_ok, _why):
            raise RuntimeError("boom")
        with patch.object(urllib.request, "urlopen", _Sender()):
            PhoneNotify.send(TOPIC, "t", "m", on_done=boom).join(5)

    def test_the_default_test_environment_refuses_the_network_and_only_fails(self):
        results = []
        PhoneNotify.send(TOPIC, "t", "m", on_done=lambda ok, why: results.append(ok)).join(5)
        self.assertEqual(results, [False])


class TestSecret(unittest.TestCase):
    def setUp(self):
        self.addCleanup(DebugLog._secrets.discard, TOPIC)

    def _text(self):
        return config.DEBUG_LOG_PATH.read_text(encoding="utf-8") if config.DEBUG_LOG_PATH.exists() else ""

    def test_the_topic_is_scrubbed_from_debug_log(self):
        PhoneNotify.register_topic(TOPIC)
        DebugLog.write(f"宛先 {TOPIC} です")
        self.assertNotIn(TOPIC, self._text())
        self.assertIn("宛先 *** です", self._text())

    def test_a_loaded_topic_is_registered_as_a_secret(self):
        app = type("FakeApp", (), {})()
        mainGUI.App._load_phone_notify(app, {"phone_notify_topic": TOPIC})
        DebugLog.write(f"x {TOPIC}")
        self.assertNotIn(TOPIC, self._text())

    def test_the_settings_for_a_report_and_debug_log_lose_the_topic(self):
        self.assertIn("phone_notify_topic", config.REPORT_SETTINGS_EXCLUDE_KEYS)
        self.assertEqual(BugReport.settings_for_report({"phone_notify_topic": TOPIC, "win_count": 3}),
                         {"win_count": 3})
        app = type("FakeApp", (), {})()
        app.winfo_screenwidth = lambda: 2560
        app.winfo_screenheight = lambda: 1440
        with patch.object(mainGUI, "load_settings",
                          return_value={"phone_notify_topic": TOPIC, "phone_notify": True, "win_count": 6}):
            mainGUI.App._debug_environment(app)
        self.assertNotIn(TOPIC, self._text())
        self.assertIn('"win_count": 6', self._text())


class _FakeApp:
    """設定の画面の偽物（Tk を開かない）。mainGUI.App の関数を呼ぶ"""

    def __init__(self):
        self.logs, self.saves = [], 0
        self.clipboard = []
        self.v_phone_notify = type("V", (), {"v": False, "get": lambda s: s.v, "set": lambda s, x: setattr(s, "v", x)})()
        self.v_phone_topic = type("V", (), {"v": "", "get": lambda s: s.v, "set": lambda s, x: setattr(s, "v", x)})()

    def _log(self, msg):
        self.logs.append(msg)

    def _schedule_settings_save(self):
        self.saves += 1

    def clipboard_clear(self):
        self.clipboard.clear()

    def clipboard_append(self, text):
        self.clipboard.append(text)

    def _phone_test_done(self, ok, reason):
        mainGUI.App._phone_test_done(self, ok, reason)

    def _sync_queue_watcher(self):
        # 本物は VRChat のログを読む見張りを始める。偽の App では始めない（呼ばれたことだけ覚える）
        self.syncs = getattr(self, "syncs", 0) + 1


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.addCleanup(SharedState.set_phone_notify, False)
        self.addCleanup(SharedState.set_phone_topic, "")
        self.addCleanup(DebugLog._secrets.discard, TOPIC)
        SharedState.set_phone_notify(False)
        SharedState.set_phone_topic("")

    def test_the_default_is_off(self):
        self.assertFalse(SharedState.get_phone_notify())
        app = _FakeApp()
        mainGUI.App._load_phone_notify(app, {})
        self.assertFalse(SharedState.get_phone_notify())
        self.assertFalse(app.v_phone_notify.get())

    def test_a_broken_value_is_off_and_only_true_turns_it_on(self):
        for value, expect in ((True, True), (False, False), ("true", False), (1, False), (None, False)):
            mainGUI.App._load_phone_notify(_FakeApp(), {"phone_notify": value, "phone_notify_topic": TOPIC})
            self.assertEqual(SharedState.get_phone_notify(), expect, value)

    def test_a_missing_or_broken_topic_is_generated_and_loading_does_not_save(self):
        for saved in ({}, {"phone_notify_topic": "bad topic!"}, {"phone_notify_topic": 5}):
            app = _FakeApp()
            mainGUI.App._load_phone_notify(app, saved)
            topic = SharedState.get_phone_topic()
            self.assertTrue(PhoneNotify.is_valid_topic(topic), saved)
            self.assertEqual(app.v_phone_topic.get(), topic)
            self.assertEqual(app.saves, 0, "読み込みでは保存しない")
            self.addCleanup(DebugLog._secrets.discard, topic)

    def test_a_good_topic_is_kept_and_not_saved_again(self):
        app = _FakeApp()
        mainGUI.App._load_phone_notify(app, {"phone_notify_topic": TOPIC, "phone_notify": True})
        self.assertEqual(SharedState.get_phone_topic(), TOPIC)
        self.assertEqual(app.v_phone_topic.get(), TOPIC)
        self.assertEqual(app.saves, 0)
        self.assertTrue(SharedState.get_phone_notify())

    def test_the_checkbox_sets_the_flag_and_saves(self):
        app = _FakeApp()
        app.v_phone_notify.set(True)
        mainGUI.App._on_phone_notify_changed(app)
        self.assertTrue(SharedState.get_phone_notify())
        self.assertEqual(app.saves, 1)
        self.assertEqual(app.syncs, 1, "見張りも「通知する」に合わせる")
        app.v_phone_notify.set(False)
        mainGUI.App._on_phone_notify_changed(app)
        self.assertFalse(SharedState.get_phone_notify())

    def test_renewing_makes_a_new_valid_topic(self):
        app = _FakeApp()
        SharedState.set_phone_topic(TOPIC)
        mainGUI.App._renew_phone_topic(app)
        new = SharedState.get_phone_topic()
        self.assertNotEqual(new, TOPIC)
        self.assertTrue(PhoneNotify.is_valid_topic(new))
        self.assertEqual(app.v_phone_topic.get(), new)
        self.assertEqual(app.saves, 1)
        self.addCleanup(DebugLog._secrets.discard, new)
        self.assertFalse(any(TOPIC in m or new in m for m in app.logs), "ログに宛先を出さない")

    def test_copy_puts_the_topic_on_the_clipboard_without_logging_it(self):
        app = _FakeApp()
        SharedState.set_phone_topic(TOPIC)
        mainGUI.App._copy_phone_topic(app)
        self.assertEqual(app.clipboard, [TOPIC])
        self.assertEqual(app.saves, 1, "作ったばかりの宛先を残す")
        self.assertFalse(any(TOPIC in m for m in app.logs))

    def test_the_test_send_works_while_off_and_reports_in_the_tool_log(self):
        app = _FakeApp()
        SharedState.set_phone_topic(TOPIC)
        SharedState.set_phone_notify(False)
        sender, threads = _Sender(), []
        real_send = PhoneNotify.send

        def keep(*args, **kwargs):
            threads.append(real_send(*args, **kwargs))
            return threads[-1]

        with patch.object(urllib.request, "urlopen", sender), patch.object(PhoneNotify, "send", keep):
            mainGUI.App._test_phone_notify(app)
            for thread in threads:
                thread.join(5)
        self.assertEqual(sender.body()["topic"], TOPIC)
        self.assertEqual(sender.body()["priority"], 4)
        self.assertEqual(app.saves, 1, "作ったばかりの宛先を残す")
        self.assertTrue(any("テスト送信しました" in m for m in app.logs), app.logs)
        self.assertFalse(any(TOPIC in m for m in app.logs))

    def test_a_failed_test_send_only_logs(self):
        app = _FakeApp()
        SharedState.set_phone_topic(TOPIC)
        threads = []
        real_send = PhoneNotify.send

        def keep(*args, **kwargs):
            threads.append(real_send(*args, **kwargs))
            return threads[-1]

        with patch.object(urllib.request, "urlopen", _Sender(error=OSError("down"))), \
                patch.object(PhoneNotify, "send", keep):
            mainGUI.App._test_phone_notify(app)
            for thread in threads:
                thread.join(5)
        self.assertTrue(any("テスト送信に失敗しました" in m and "OSError" in m for m in app.logs), app.logs)
        self.assertFalse(any(TOPIC in m for m in app.logs))

    def test_the_test_send_without_a_topic_says_so(self):
        app = _FakeApp()
        with patch.object(PhoneNotify, "send") as send:
            mainGUI.App._test_phone_notify(app)
        send.assert_not_called()
        self.assertTrue(any("宛先がありません" in m for m in app.logs))


class _Rounds(unittest.TestCase):
    """LogMonitor の 7 か所。偽の送信（PhoneNotify.send）に届いた中身で確かめる"""

    PREFIX = "2026.11.06 13:00:00 Debug      -  "
    CLASSIC_KEY = "Classic/クラシック"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(TOPIC)
        self.sent, self.logs = [], []
        for p in (patch.object(ConnectDB, "register_round"),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_continue_start"),
                  patch.object(PhoneNotify, "send", side_effect=self._record)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_phone_notify, False)
        self.addCleanup(SharedState.set_phone_topic, "")

    def _record(self, topic, title, message, on_done=None):
        self.sent.append((topic, title, message))

    def monitor(self, keep_on=None, continue_rounds=(), instance=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=True, cancel_afk=True, voice_continue="continue.mp3",
                           continue_rounds=set(continue_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, self.logs.append, window_idx=2)
        monitor.st.instance_type = instance
        monitor._running = True
        monitor._start_daemon = lambda *a, **k: None
        return monitor

    def start_round(self, monitor, round_type="Classic", map_text="Facility (12)"):
        monitor._process(self.PREFIX + f"This round is taking place at {map_text} and the round type is {round_type}")

    def killers(self, monitor, ids, round_type="Classic"):
        monitor._process(self.PREFIX + "Killers have been set - "
                         + " ".join(str(i) for i in (list(ids) + [0, 0])[:3]) + f" // Round type is {round_type}")
        if not monitor.st.is_continue_round and monitor.st.terror_ids:
            monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)


class TestSites(_Rounds):
    def test_a_normal_continue(self):
        monitor = self.monitor(keep_on={self.CLASSIC_KEY: {99}})
        self.start_round(monitor)
        self.killers(monitor, [99])
        self.assertEqual(self.sent, [(TOPIC, "窓2 続行ラウンド",
                                      f"Classic / Facility\nテラー: {LogMonitor.format_terror_ids([99])}")])

    def test_a_glorbo_continue(self):
        monitor = self.monitor()
        self.start_round(monitor)
        monitor.st.terror_ids = [99]
        monitor._start_glorbo_continue("Classic")
        self.assertEqual([m[1] for m in self.sent], ["窓2 続行ラウンド"])

    def test_a_group_wanted_continue(self):
        for decision in (GroupRound.WANTED, GroupRound.CONTINUE_LISTED):
            with self.subTest(decision=decision):
                self.sent.clear()
                monitor = self.monitor(instance=config.INSTANCE_HOSHIIMO)
                self.start_round(monitor)
                monitor.st.terror_ids = [99]
                with patch.object(monitor, "_group_decision", return_value=decision):
                    self.assertTrue(monitor._apply_group_decision("Classic"))
                self.assertEqual([m[1] for m in self.sent], ["窓2 続行ラウンド"])

    def test_a_group_all_continue(self):
        monitor = self.monitor(instance=config.INSTANCE_YAKIIMO)
        self.start_round(monitor)
        monitor.st.terror_ids = [99]
        with patch.object(monitor, "_group_decision", return_value=GroupRound.CONTINUE):
            self.assertTrue(monitor._apply_group_decision("Classic"))
        self.assertEqual([m[1] for m in self.sent], ["窓2 全続行"])

    def test_a_round_setting_all_continue(self):
        monitor = self.monitor(continue_rounds=("Classic",))
        self.start_round(monitor)
        self.killers(monitor, [99])
        self.assertEqual(self.sent, [(TOPIC, "窓2 全続行",
                                      f"Classic / Facility\nテラー: {LogMonitor.format_terror_ids([99])}")])

    def test_all_continue_while_the_terrors_are_unknown(self):
        monitor = self.monitor(continue_rounds=("Fog",))
        self.start_round(monitor, "Fog")
        monitor._on_killers_unknown(type("E", (), {"round_type": "Fog"})())
        self.assertEqual(self.sent, [(TOPIC, "窓2 全続行", "Fog / Facility\nテラー: 不明")])

    def test_run_all_continue(self):
        monitor = self.monitor(continue_rounds=("Run",))
        self.start_round(monitor, "Run", "Cheese Maze (59)")
        self.assertEqual(self.sent, [(TOPIC, "窓2 全続行", "Run / Cheese Maze\nテラー: 不明")])

    def test_run_without_all_continue_sends_nothing(self):
        monitor = self.monitor()
        self.start_round(monitor, "Run")
        self.assertEqual(self.sent, [])

    def test_a_skip_sends_nothing(self):
        monitor = self.monitor()
        self.start_round(monitor)
        self.killers(monitor, [99])
        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(self.sent, [])

    def test_the_map_is_remembered_at_round_start(self):
        monitor = self.monitor()
        self.start_round(monitor, "Classic", "Cheese Maze (59)")
        self.assertEqual(monitor.st.map_name, "Cheese Maze")


class TestOnceAPerRound(_Rounds):
    def test_the_terror_reveal_does_not_send_again(self):
        monitor = self.monitor(continue_rounds=("Fog",))
        self.start_round(monitor, "Fog")
        monitor._on_killers_unknown(type("E", (), {"round_type": "Fog"})())
        self.assertEqual(len(self.sent), 1)
        monitor._run_plan("Fog", ("continue_rounds",))          # テラーが分かったときの判定
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], "窓2 全続行")

    def test_all_continue_turning_into_continue_does_not_send_again(self):
        monitor = self.monitor(keep_on={self.CLASSIC_KEY: {99}}, continue_rounds=("Classic",))
        self.start_round(monitor)
        monitor._run_plan("Classic", ("continue_rounds",))
        monitor.st.terror_ids = [99]
        monitor._decide_with_keep_on_set("Classic", ("list", True, False))
        self.assertEqual([m[1] for m in self.sent], ["窓2 全続行"])

    def test_continue_turning_into_all_continue_does_not_send_again(self):
        monitor = self.monitor(keep_on={self.CLASSIC_KEY: {99}}, continue_rounds=("Classic",))
        self.start_round(monitor)
        monitor.st.terror_ids = [99]
        monitor._decide_with_keep_on_set("Classic", ("list", True, False))
        monitor._run_plan("Classic", ("continue_rounds",))
        self.assertEqual([m[1] for m in self.sent], ["窓2 続行ラウンド"])

    def test_every_site_twice_in_one_round_sends_once(self):
        monitor = self.monitor(continue_rounds=("Classic",))
        self.start_round(monitor)
        monitor.st.terror_ids = [99]
        with patch.object(monitor, "_group_decision", return_value=GroupRound.CONTINUE):
            monitor._apply_group_decision("Classic")
        monitor._run_plan("Classic", ("continue_rounds",))
        monitor._apply_all_continue_while_unknown()
        monitor._start_glorbo_continue("Classic")
        self.assertEqual(len(self.sent), 1)

    def test_the_next_round_sends_again(self):
        monitor = self.monitor(continue_rounds=("Classic",))
        self.start_round(monitor)
        monitor._run_plan("Classic", ("continue_rounds",))
        self.start_round(monitor)
        monitor._run_plan("Classic", ("continue_rounds",))
        self.assertEqual(len(self.sent), 2)

    def test_each_window_sends_for_itself(self):
        first, second = self.monitor(continue_rounds=("Classic",)), self.monitor(continue_rounds=("Classic",))
        second.window_idx = 5
        for monitor in (first, second):
            self.start_round(monitor)
            monitor._run_plan("Classic", ("continue_rounds",))
        self.assertEqual([m[1] for m in self.sent], ["窓2 全続行", "窓5 全続行"])


class TestWhenNotToSend(_Rounds):
    def test_off_sends_nothing(self):
        SharedState.set_phone_notify(False)
        monitor = self.monitor(continue_rounds=("Run",))
        self.start_round(monitor, "Run")
        self.assertEqual(self.sent, [])

    def test_a_broken_topic_sends_nothing(self):
        SharedState.set_phone_topic("not a topic!")
        monitor = self.monitor(continue_rounds=("Run",))
        self.start_round(monitor, "Run")
        self.assertEqual(self.sent, [])

    def test_hands_free_sends_nothing(self):
        SharedState.set_hands_free(True)
        monitor = self.monitor(continue_rounds=("Classic",))
        self.assertTrue(monitor._hands_free())
        self.start_round(monitor)
        monitor._run_plan("Classic", ("continue_rounds",))
        monitor._start_glorbo_continue("Classic")
        self.assertEqual(self.sent, [])

    def test_a_window_that_is_not_private_like_is_not_hands_free_and_sends(self):
        SharedState.set_hands_free(True)
        monitor = self.monitor(instance=config.INSTANCE_YAKIIMO)
        self.assertFalse(monitor._hands_free())
        self.start_round(monitor)
        monitor.st.terror_ids = [99]
        with patch.object(monitor, "_group_decision", return_value=GroupRound.CONTINUE):
            monitor._apply_group_decision("Classic")
        self.assertEqual(len(self.sent), 1)

    def test_an_error_while_sending_does_not_touch_the_decision(self):
        monitor = self.monitor(keep_on={self.CLASSIC_KEY: {99}})
        self.start_round(monitor)
        with patch.object(PhoneNotify, "send", side_effect=RuntimeError("boom")):
            self.killers(monitor, [99])
        self.assertTrue(monitor.st.is_continue_round)


class TestNetworkFailureFromTheMonitor(unittest.TestCase):
    PREFIX = "2026.11.06 13:00:00 Debug      -  "

    def test_a_network_failure_only_logs_and_the_round_goes_on(self):
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(TOPIC)
        SharedState.set_hands_free(False)
        self.addCleanup(SharedState.set_phone_notify, False)
        self.addCleanup(SharedState.set_phone_topic, "")
        self.addCleanup(DebugLog._secrets.discard, TOPIC)
        logs, threads = [], []
        real_send = PhoneNotify.send

        def keep(*args, **kwargs):
            threads.append(real_send(*args, **kwargs))
            return threads[-1]

        monitor = LogMonitor.LogMonitor(WindowConfig(continue_rounds={"Run"}), {}, logs.append, window_idx=2)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        monitor._start_daemon = lambda *a, **k: None
        with patch.object(urllib.request, "urlopen", _Sender(error=OSError("down"))), \
                patch.object(PhoneNotify, "send", keep), \
                patch.object(ConnectDB, "register_round"):
            monitor._process(self.PREFIX + "This round is taking place at Facility (12) and the round type is Run")
            for thread in threads:
                thread.join(5)
        self.assertEqual(len(threads), 1)
        self.assertTrue(any("[窓2] スマホ通知: 送信に失敗しました" in m and "OSError" in m for m in logs), logs)
        self.assertFalse(any(TOPIC in m for m in logs))
        self.assertTrue(monitor.st.all_continue_round)


if __name__ == "__main__":
    unittest.main()
