"""ToN Utils の続行の判断に従う（EB）。本物の Utils のログ・sqlite・入力は使わない（偽物の行・一時ファイル）"""
from tests.support import *  # noqa: F401,F403
import datetime
import UtilsWatch

SOURCE = "0123456789abcdef0123456789abcdef"


class _FakeWatch:
    """偽の UtilsWatch。actions は待つ間に返すもの（press・hold・None）"""

    def __init__(self, reason=None, action=None):
        self.reason = reason
        self.action = action
        self.refreshed = 0

    def window_reason(self, log_path):
        return (None if self.reason == UtilsWatch.REASON_NOT_RUNNING else SOURCE), self.reason

    def refresh(self):
        self.refreshed += 1

    def action_since(self, source, since):
        assert source == SOURCE
        return (self.action, since + 2.6) if self.action else (None, None)


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.set_list_source("host")
        SharedState.set_follow_utils(True)
        self.addCleanup(SharedState.set_follow_utils, False)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.continue_round_reset)
        for target, name, value in ((ConnectDB, "register_round", MagicMock()),
                                    (PlaySound, "play_sound", MagicMock()),
                                    (Recorder, "on_continue_start", MagicMock()),
                                    (config, "UTILS_WAIT_SEC", 0.05),
                                    (config, "UTILS_POLL_SEC", 0.01)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.watch = _FakeWatch()
        p = patch.object(UtilsWatch, "shared", return_value=self.watch)
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self, keep_on=None, **cfg_kw):
        cfg = WindowConfig(voice_continue="continue.mp3", do_skip=True,
                           log_path=Path("C:/logs/output_log_2026-10-12_10-00-00.txt"), **cfg_kw)
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None, window_idx=2)
        monitor._running = True
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.round_seq = 5
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._focus_for_freeze = lambda *_a: None
        self.started = []
        monitor._start_daemon = lambda target, *a: self.started.append((target.__name__, a))
        return monitor

    def _set(self, monitor, ids=(99,)):
        """Killers have been set（起点）"""
        monitor.st.utils_anchor_seq = monitor.st.round_seq
        monitor._on_killers(list(ids), "Classic", revealed=False)

    def _wait(self, monitor):
        """待つ処理をその場で走らせる"""
        name, args = next(s for s in self.started if s[0] == "_wait_utils")
        monitor._wait_utils(*args)

    def names(self):
        return [n for n, _a in self.started]


class TestTheDecision(_Case):
    def test_off_is_unchanged(self):
        SharedState.set_follow_utils(False)
        monitor = self._monitor()
        self._set(monitor)
        self.assertEqual(self.names(), ["do_skip"])
        self.assertTrue(any("【スキップ】" in m for m in monitor.logs))

    def test_it_waits_instead_of_skipping(self):
        monitor = self._monitor()
        self._set(monitor)
        self.assertEqual(self.names(), ["_wait_utils"], "起点では自爆しない")
        self.assertTrue(any("Utils の判断を待ちます" in m and "skip" in m for m in monitor.logs),
                        "このツールの判定も残す")

    def test_press_skips_without_this_tool_pressing(self):
        self.watch.action = "press"
        monitor = self._monitor()
        self._set(monitor)
        self._wait(monitor)
        self.assertEqual(self.names(), ["_wait_utils"], "do_skip を走らせない")
        self.assertIn("[窓2] Utils: 自爆（スキップ）", monitor.logs)
        self.assertFalse(monitor.st.continue_freeze_held)

    def test_hold_lets_this_tool_decide_and_skip(self):
        self.watch.action = "hold"
        monitor = self._monitor()
        self._set(monitor)
        self._wait(monitor)
        self.assertEqual(self.names(), ["_wait_utils", "do_skip"])
        self.assertIn("[窓2] Utils: 許可されていないため、このツールで判定", monitor.logs)

    def test_nothing_continues_like_play(self):
        monitor = self._monitor()
        self._set(monitor)
        self._wait(monitor)
        self.assertNotIn("do_skip", self.names())
        self.assertIn("[窓2] Utils: 続行", monitor.logs)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertTrue(monitor.st.continue_freeze_held, "続行フリーズを張る")
        self.assertTrue(any("【プレイ】" in m for m in monitor.logs))
        PlaySound.play_sound.assert_called_once_with("continue.mp3", kind="continue")

    def test_a_round_that_ended_does_nothing(self):
        monitor = self._monitor()
        self._set(monitor)
        monitor.st.in_round = False
        self._wait(monitor)
        self.assertEqual(self.names(), ["_wait_utils"])
        self.assertFalse(any(m.startswith("[窓2] Utils:") for m in monitor.logs))
        self.assertFalse(monitor.st.continue_freeze_held)

    def test_a_round_that_ends_as_utils_answers_does_nothing(self):
        """Utils の行を読んだその時にラウンドが終わっていたら、このツールは何もしない"""
        monitor = self._monitor()
        self._set(monitor)

        def answer(source, since):
            monitor.st.in_round = False
            return "hold", since
        self.watch.action_since = answer
        self._wait(monitor)
        self.assertEqual(self.names(), ["_wait_utils"], "hold でも自爆しない")
        self.assertFalse(any(m.startswith("[窓2] Utils:") for m in monitor.logs))

    def test_twice_in_a_round_waits_once(self):
        monitor = self._monitor()
        self._set(monitor)
        monitor._decide("Classic")
        self.assertEqual(self.names(), ["_wait_utils"])

    def test_hands_free_also_follows(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor()
        self._set(monitor)
        self.assertEqual(self.names(), ["_wait_utils"], "放置モードの即自爆もしない")
        self._wait(monitor)
        self.assertTrue(monitor.st.continue_freeze_held, "何も来なければ続行")


class TestTheAnchor(_Case):
    def _event(self, line):
        return LogParser.parse(LogParser.strip_prefix(line))

    def test_killers_set_is_the_anchor(self):
        monitor = self._monitor()
        monitor._on_killers_set(self._event(
            "2026.10.12 10:00:00 Debug      -  Killers have been set - 99 0 0 // Round type is Classic"))
        self.assertEqual(monitor.st.utils_anchor_seq, 5)
        self.assertEqual(self.names(), ["_wait_utils"])

    def test_an_early_read_waits_for_the_reveal(self):
        """Fog: 看破・Enrage などで先に分かっても待ち始めない。本当に明かされてから"""
        monitor = self._monitor()
        monitor.st.round_type = "Fog"
        monitor._identify_fog_terror(99, "Enrage", "x")
        self.assertEqual(self.started, [], "自爆も待ちもしない")
        self.assertIn("[窓2] Utils: 明かされるまで判断を待ちます", monitor.logs)
        self.assertFalse(monitor.st.continue_freeze_held)
        monitor._on_killers_revealed(self._event(
            "2026.10.12 10:00:30 Debug      -  Killers have been revealed - 99 0 0 // Round type is Fog"))
        self.assertEqual(self.names(), ["_wait_utils"], "明かされたら待ち始める")

    def test_without_the_mode_the_early_read_still_decides(self):
        SharedState.set_follow_utils(False)
        monitor = self._monitor()
        monitor.st.round_type = "Fog"
        monitor._identify_fog_terror(99, "Enrage", "x")
        self.assertEqual(self.names(), ["do_skip"])
        monitor._on_killers_revealed(self._event(
            "2026.10.12 10:00:30 Debug      -  Killers have been revealed - 99 0 0 // Round type is Fog"))
        self.assertEqual(self.names(), ["do_skip"], "今までどおり二重にしない")


class TestWhenUtilsCannotDecide(_Case):
    def test_each_reason_falls_back_and_logs_once(self):
        for reason in (UtilsWatch.REASON_NOT_RUNNING, UtilsWatch.REASON_NO_SOURCE, UtilsWatch.REASON_NOT_SEEN):
            self.watch.reason = reason
            monitor = self._monitor()
            self._set(monitor)
            monitor.st.round_seq += 1
            self._set(monitor)
            self.assertEqual(self.names(), ["do_skip", "do_skip"], reason)
            text = f"[窓2] Utils の判断が読めないため、このツールで判定します（{reason}）"
            self.assertEqual(monitor.logs.count(text), 1, "理由が変わらなければ 1 回")

    def test_a_new_reason_is_logged_again(self):
        monitor = self._monitor()
        self.watch.reason = UtilsWatch.REASON_NOT_RUNNING
        self._set(monitor)
        self.watch.reason = UtilsWatch.REASON_NO_SOURCE
        monitor.st.round_seq += 1
        self._set(monitor)
        self.assertEqual(len([m for m in monitor.logs if "Utils の判断が読めない" in m]), 2)


# ── UtilsWatch（一時ファイル・偽のプロセス）──────────────────────
def _utc(epoch: float) -> str:
    """epoch 秒 → Utils のログの行の頭の時刻（UTC）"""
    d = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _eval_line(action, at, source=SOURCE):
    return (f"{_utc(at)}  INFO ton_gui_core::room_action: auto self-destruct evaluated "
            f"source={source} diag=x action=\"{action}\"")


class TestTheLog(unittest.TestCase):
    T0 = datetime.datetime(2026, 10, 1, 9, 0, 0, tzinfo=datetime.timezone.utc).timestamp()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.log = self.dir / "ton-utils.log"
        self.watch = UtilsWatch.UtilsWatch(self.log, self.dir / "none.sqlite", running_names=lambda: set())

    def _write(self, *lines, mode="a"):
        with open(self.log, mode, encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    @staticmethod
    def _eval(action, source=SOURCE):
        return _eval_line(action, TestTheLog.T0, source)

    def test_lines_already_in_the_file_count_by_their_time(self):
        """最初に読んだ行も、Utils の時刻で数える（EB2: 読むのが遅れても取りこぼさない）"""
        self._write(_eval_line("press", self.T0 + 0.6))
        self.watch.refresh()
        self.assertIn(SOURCE, self.watch.seen)
        self.assertEqual(self.watch.action_since(SOURCE, self.T0 - 2), ("press", self.T0 + 0.6))
        self.assertEqual(self.watch.action_since(SOURCE, self.T0 + 1), (None, None), "それより前の行は数えない")
        self.assertEqual(self.watch.action_since("ffff", 0.0), (None, None), "ほかの窓の行は数えない")
        self._write(_eval_line("hold", self.T0 + 30))
        self.watch.refresh()
        self.assertEqual(self.watch.action_since(SOURCE, self.T0 + 28), ("hold", self.T0 + 30))

    def test_a_utils_start_forgets_what_was_seen(self):
        self._write(self._eval("press"), "2026-10-12T02:00:00Z  INFO ton_gui: started log=x version=\"0.1.0\"")
        self.watch.refresh()
        self.assertNotIn(SOURCE, self.watch.seen)
        self._write(f"2026-10-12T02:00:01Z  INFO x: self-destruct permission changed source={SOURCE} allowed=true")
        self.watch.refresh()
        self.assertIn(SOURCE, self.watch.seen, "allowed=true も見たうちに入る")

    def test_allowed_false_is_not_enough(self):
        self._write(f"2026-10-12T02:00:01Z  INFO x: self-destruct permission changed source={SOURCE} allowed=false")
        self.watch.refresh()
        self.assertNotIn(SOURCE, self.watch.seen)

    def test_a_shrunk_file_is_read_from_the_start(self):
        self._write(*[self._eval("hold", "aaaa")] * 20)
        self.watch.refresh()
        self._write(_eval_line("press", self.T0 + 100), mode="w")          # 小さくなった
        self.watch.refresh()
        self.assertEqual(self.watch.action_since(SOURCE, self.T0 + 99)[0], "press")
        self.assertNotIn("aaaa", self.watch.seen, "前の分は忘れる")

    def test_a_half_written_line_waits(self):
        self.watch.refresh()                                  # ファイルが無い → 何もしない
        self._write()
        self.watch.refresh()
        line = self._eval("press")
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(line[:60])
        self.watch.refresh()
        self.assertEqual(self.watch.action_since(SOURCE, 0.0), (None, None))
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(line[60:] + "\n")
        self.watch.refresh()
        self.assertEqual(self.watch.action_since(SOURCE, 0.0)[0], "press")

    def test_utc_to_epoch_across_the_date_in_jst(self):
        """Utils は UTC、VRChat のログはローカル時刻。JST の 0 時すぎ ＝ UTC の前の日の 15 時すぎ"""
        jst = datetime.timezone(datetime.timedelta(hours=9))
        want = datetime.datetime(2026, 10, 12, 0, 0, 1, 500000, tzinfo=jst).timestamp()
        self.assertEqual(UtilsWatch.utc_epoch("2026-10-11T15:00:01.5Z  INFO x"), want)
        self.assertEqual(UtilsWatch.utc_epoch("2026-10-11T15:00:01.500000Z  INFO x"), want)
        self.assertEqual(UtilsWatch.utc_epoch("2026-10-11T15:00:01Z  INFO x"), want - 0.5)
        self.assertIsNone(UtilsWatch.utc_epoch("no time here"))
        # VRChat のログの行（ローカル時刻）と同じ時点になる
        local = datetime.datetime.fromtimestamp(want - 0.5).strftime("%Y.%m.%d %H:%M:%S")
        self.assertEqual(LogParser.log_time(local + " Debug      -  x"), want - 0.5)


# ── EB2: 起点の行の時刻と Utils の時刻で数える（本物の UtilsWatch・一時ファイル）─────
class TestTheTiming(_Case):
    ANCHOR_LOCAL = "2026.10.01 18:30:00"            # 起点の Killers have been set（ローカル時刻・秒まで）

    def setUp(self):
        super().setUp()
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.log = Path(d.name) / "ton-utils.log"
        self.real = UtilsWatch.UtilsWatch(self.log, Path(d.name) / "none.sqlite",
                                          running_names=lambda: {"ton-utils.exe"})
        self.real.source_for = lambda _p: SOURCE
        UtilsWatch.shared.return_value = self.real
        self.anchor = LogParser.log_time(self.ANCHOR_LOCAL + " Debug      -  x")

    def _write(self, *lines):
        with open(self.log, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def _round(self, *utils_lines):
        """Utils が前に窓を見ている → 起点の行を処理（その前に utils_lines がもうファイルにある）→ 待つ"""
        self._write(_eval_line("hold", self.anchor - 3600))            # 前に見た（この起動の後）
        self._write(*utils_lines)
        monitor = self._monitor()
        line = (f"{self.ANCHOR_LOCAL} Debug      -  Killers have been set - 99 0 0 // Round type is Classic")
        monitor.st.log_now = LogParser.log_time(line)
        monitor._on_killers_set(LogParser.parse(LogParser.strip_prefix(line)))
        self._wait(monitor)
        return monitor

    def test_a_press_already_in_the_file_skips(self):
        """読むのが遅れて、起点を処理した時にはもう press がある → スキップ（続行にしない）"""
        monitor = self._round(_eval_line("press", self.anchor + 0.6))
        self.assertIn("[窓2] Utils: 自爆（スキップ）", monitor.logs)
        self.assertFalse(monitor.st.continue_freeze_held)

    def test_a_press_just_before_the_second_counts(self):
        """VRChat のログは秒で切り捨て。起点の行の時刻より少し前の Utils の時刻も数える（−2 秒まで）"""
        monitor = self._round(_eval_line("press", self.anchor - 1.5))
        self.assertIn("[窓2] Utils: 自爆（スキップ）", monitor.logs)

    def test_a_press_of_the_previous_round_does_not_count(self):
        monitor = self._round(_eval_line("press", self.anchor - 2.5), _eval_line("press", self.anchor - 40))
        self.assertIn("[窓2] Utils: 続行", monitor.logs, "前のラウンドの press は数えない")
        self.assertTrue(monitor.st.continue_freeze_held)

    def test_a_hold_after_the_anchor(self):
        monitor = self._round(_eval_line("hold", self.anchor + 0.4))
        self.assertIn("[窓2] Utils: 許可されていないため、このツールで判定", monitor.logs)
        self.assertIn("do_skip", self.names())

    def test_nothing_continues_after_the_wall_clock_wait(self):
        started = time.monotonic()
        monitor = self._round()
        self.assertIn("[窓2] Utils: 続行", monitor.logs)
        self.assertGreaterEqual(time.monotonic() - started, config.UTILS_WAIT_SEC, "上限は壁時計で待つ")

    def test_the_debug_line_is_the_gap_from_the_anchor(self):
        with patch.object(DebugLog, "write") as write:
            self._round(_eval_line("press", self.anchor + 0.6))
        self.assertIn("[Utils] 窓2 Utils: press 0.6 秒", [c.args[0] for c in write.call_args_list])

    def test_a_press_written_while_waiting(self):
        self._write(_eval_line("hold", self.anchor - 3600))
        monitor = self._monitor()
        monitor.st.log_now = self.anchor
        monitor._on_killers_set(LogParser.parse(LogParser.strip_prefix(
            f"{self.ANCHOR_LOCAL} Debug      -  Killers have been set - 99 0 0 // Round type is Classic")))
        real_refresh = self.real.refresh
        calls = [0]

        def refresh():
            calls[0] += 1
            if calls[0] == 2:
                self._write(_eval_line("press", self.anchor + 1.2))   # 待っている間に書かれた
            real_refresh()
        self.real.refresh = refresh
        self._wait(monitor)
        self.assertIn("[窓2] Utils: 自爆（スキップ）", monitor.logs)


class TestTheProcess(unittest.TestCase):
    def test_the_real_exe_name_and_others(self):
        for names, expected in (({"ton-utils.exe"}, True), ({"tonutils.exe"}, True), ({"ToN Utils.exe"}, True),
                                ({"tonsavemanager.exe", "vrchat.exe"}, False), (None, False), (set(), False)):
            watch = UtilsWatch.UtilsWatch(running_names=lambda n=names: n)
            self.assertIs(watch.running(), expected, names)

    def test_it_is_cached_briefly(self):
        calls = []
        now = [0.0]
        watch = UtilsWatch.UtilsWatch(running_names=lambda: calls.append(1) or {"ton-utils.exe"},
                                      clock=lambda: now[0])
        watch.running()
        watch.running()
        self.assertEqual(len(calls), 1)
        now[0] = UtilsWatch.PROCESS_CACHE_SEC + 0.1
        watch.running()
        self.assertEqual(len(calls), 2)


class TestTheDatabase(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.db = Path(d.name) / "save-history.sqlite"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE logs (source TEXT, started TEXT, other TEXT)")
        con.execute("CREATE TABLE saves (player TEXT, code TEXT)")
        con.execute("INSERT INTO logs VALUES (?, ?, ?)", (SOURCE, "2026-10-10T06:16:40", "x"))
        con.execute("INSERT INTO logs VALUES (?, ?, ?)", ("ffff", "2026-10-10T06:16:55", "x"))
        con.execute("INSERT INTO saves VALUES ('someone', 'SECRET')")
        con.commit()
        con.close()
        self.now = [0.0]
        self.watch = UtilsWatch.UtilsWatch(Path(d.name) / "ton-utils.log", self.db,
                                           running_names=lambda: {"ton-utils.exe"}, clock=lambda: self.now[0])

    def test_source_by_the_log_file_name(self):
        self.assertEqual(self.watch.source_for("C:/x/output_log_2026-10-10_06-16-40.txt"), SOURCE)
        self.assertEqual(self.watch.source_for("C:/x/output_log_2026-10-10_06-16-55.txt"), "ffff")
        self.assertIsNone(self.watch.source_for("C:/x/output_log_2026-10-10_07-00-00.txt"))
        self.assertIsNone(self.watch.source_for("C:/x/other.txt"))
        self.assertEqual(UtilsWatch.started_key("C:/x/output_log_2026-10-10_06-16-40.txt"), "2026-10-10 06-16-40")

    def test_read_only_and_only_the_logs_table(self):
        real = sqlite3.connect
        seen = {"uri": [], "sql": []}

        def connect(target, *a, **kw):
            seen["uri"].append((target, kw.get("uri")))
            con = real(target, *a, **kw)
            con.set_trace_callback(seen["sql"].append)
            return con
        with patch.object(UtilsWatch.sqlite3, "connect", side_effect=connect):
            self.watch.source_for("C:/x/output_log_2026-10-10_06-16-40.txt")
        self.assertEqual(len(seen["uri"]), 1)
        target, uri = seen["uri"][0]
        self.assertTrue(target.startswith("file:") and target.endswith("?mode=ro") and uri is True, target)
        self.assertEqual(seen["sql"], ["SELECT source, started FROM logs"], "logs の source・started だけ")
        with self.assertRaises(sqlite3.OperationalError):
            con = real(target, uri=True)
            try:
                con.execute("INSERT INTO logs VALUES ('a', 'b', 'c')")
            finally:
                con.close()
        self.assertEqual(list(Path(self.db).parent.glob("*.sqlite*")), [self.db], "写しを作らない")

    def test_a_locked_or_broken_database_is_tried_again_later(self):
        calls = []

        def broken(*a, **kw):
            calls.append(1)
            raise sqlite3.OperationalError("database is locked")
        with patch.object(UtilsWatch.sqlite3, "connect", side_effect=broken), \
             patch.object(DebugLog, "write") as write:
            self.assertIsNone(self.watch.source_for("C:/x/output_log_2026-10-10_06-16-40.txt"))
            self.assertIsNone(self.watch.source_for("C:/x/output_log_2026-10-10_06-16-40.txt"))
            self.assertEqual(len(calls), 1, "すぐには読み直さない")
            self.now[0] = UtilsWatch.DB_RETRY_SEC + 0.1
            self.watch.source_for("C:/x/output_log_2026-10-10_06-16-40.txt")
            self.assertEqual(len(calls), 2)
        self.assertTrue(all("SECRET" not in str(c) for c in write.call_args_list))

    def test_window_reasons(self):
        log = self.watch.log_path
        name = "C:/x/output_log_2026-10-10_06-16-40.txt"
        self.assertEqual(UtilsWatch.UtilsWatch(log, self.db, running_names=lambda: {"vrchat.exe"})
                         .window_reason(name), (None, UtilsWatch.REASON_NOT_RUNNING))
        self.assertEqual(self.watch.window_reason("C:/x/output_log_2026-10-10_09-00-00.txt"),
                         (None, UtilsWatch.REASON_NO_SOURCE))
        self.assertEqual(self.watch.window_reason(name), (SOURCE, UtilsWatch.REASON_NOT_SEEN))
        log.write_text(TestTheLog._eval("hold") + "\n", encoding="utf-8")
        self.assertEqual(self.watch.window_reason(name), (SOURCE, None))


class TestTheCheckAndSettings(unittest.TestCase):
    def setUp(self):
        self.addCleanup(SharedState.set_follow_utils, False)

    def test_off_by_default_saved_and_loaded(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_FOLLOW_UTILS = _Setting(False, bool)", src)
        app = mainGUI.App()
        self.addCleanup(app.destroy)
        row = next(w for w in app.pack_slaves()
                   if any(isinstance(c, ttk.Checkbutton) and c.cget("text") == "Utilsの判断に従う"
                          for c in w.winfo_children()))
        self.assertIn("霧即時判定", [c.cget("text") for c in row.winfo_children()], "チェックの行")
        self.assertFalse(app.v_follow_utils.get())
        app.v_follow_utils.set(True)
        with patch.object(app, "_schedule_settings_save"), patch.object(mainGUI.App, "_log", lambda *_a: None):
            app._on_follow_utils_changed()
        self.assertTrue(SharedState.get_follow_utils())
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            app._save_launch_settings()
        self.assertIs(stored["follow_utils"], True)
        for data, expected in (({"follow_utils": True}, True), ({}, False), ({"follow_utils": "yes"}, False)):
            with patch.object(mainGUI, "load_settings", return_value=data):
                app._load_saved_settings()
            self.assertIs(SharedState.get_follow_utils(), expected, data)
            self.assertIs(app.v_follow_utils.get(), expected)

    def test_the_readme(self):
        readme = (Path(mainGUI.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        section = readme[readme.index("### Utilsの判断に従う"):]
        section = section[:section.index("\n### ", 1)]
        for word in ("自爆を許可した窓だけ", "このツールが判定", "明かされてから", "読み取り専用"):
            self.assertIn(word, section, word)
