"""監視を始め直したときの状態の取り戻し。
② すでに出た moon を、遡りで覚える（次に同じ moon が来たら 2 回目）
③ 遡りで所持アイテムが分かったら、判定に使う item_id も合わせる
④ ログが縮んだら、先頭から読み直さず末尾へ飛んで状態だけ取り戻す
偽のログを一時フォルダに作って読む。本物の DB・音・入力・窓には触れない（DB への送信・音・スレッドは偽物にする）"""
from tests.support import *  # noqa: F401,F403
import AccessCheck
import RoundSequence
import State

ME = "usr_0e01408a"
JOIN = "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"
PREFIX = "2026.09.20 11:55:{:02d} Debug      -  "


def _at(second):
    return PREFIX.format(second)


def _start(round_type="Fog"):
    return f"This round is taking place at Sewers (12) and the round type is {round_type}"


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        patch.object(ConnectDB, "register_round").start()
        patch.object(LogMonitor.threading, "Thread").start()
        patch.object(PlaySound, "play_sound").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.continue_round_reset)

    def _monitor(self, lines, login=True):
        """lines = [(秒, 行), ...] の入ったログを作り、その監視を（まだ遡らずに）返す"""
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        rows = ([(1, f"User Authenticated: serim01 ({ME})")] if login else []) + list(lines)
        tmp.write("\n".join(_at(sec) + line for sec, line in rows) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name), do_skip=True), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _started(self, lines, login=True):
        """lines の入ったログで、監視を始める（遡り）"""
        monitor = self._monitor(lines, login)
        with patch.object(ConnectDB, "send_Users", return_value=7), \
             patch.object(monitor, "_start_daemon"):
            monitor._detect_instance_from_log()
        return monitor

    @staticmethod
    def _feed(monitor, second, line):
        monitor._process(_at(second) + line)


class TestMoonsSeenBeforeTheRestart(_Case):
    """② 遡りで見つけた moon は、次に来たとき 2 回目として扱う"""

    def test_a_moon_in_the_look_back_makes_the_next_one_a_repeat(self):
        monitor = self._started([(2, JOIN), (10, _start("Mystic Moon")), (30, _start("Classic"))])
        self.assertTrue(monitor.sequence.moon_done["Mystic Moon"])
        self._feed(monitor, 40, _start("Mystic Moon"))
        self.assertTrue(monitor.st.moon_repeat)

    def test_only_the_moons_seen_are_marked(self):
        monitor = self._started([(2, JOIN), (10, _start("Mystic Moon"))])
        self._feed(monitor, 40, _start("Blood Moon"))
        self.assertFalse(monitor.st.moon_repeat, "出ていない moon は 1 回目のまま")
        self.assertEqual(monitor.sequence.moon_done,
                         {"Mystic Moon": True, "Blood Moon": True, "Twilight": False, "Solstice": False},
                         "Blood Moon は今来たので立つ。Twilight・Solstice は立たない")

    def test_without_a_moon_it_is_not_a_repeat(self):
        monitor = self._started([(2, JOIN), (10, _start("Fog")), (30, _start("Classic"))])
        self.assertFalse(any(monitor.sequence.moon_done.values()))
        self._feed(monitor, 40, _start("Mystic Moon"))
        self.assertFalse(monitor.st.moon_repeat, "今までどおり 1 回目")

    def test_a_moon_before_the_last_joining_does_not_count(self):
        """入り直すと moon の消化はやり直し（遡りは最後の入室より後だけを見る）"""
        monitor = self._started([(2, JOIN), (10, _start("Mystic Moon")), (20, JOIN), (30, _start("Fog"))])
        self.assertFalse(monitor.sequence.moon_done["Mystic Moon"])
        self._feed(monitor, 40, _start("Mystic Moon"))
        self.assertFalse(monitor.st.moon_repeat)

    def test_the_guess_about_the_order_is_not_touched(self):
        """並びの推定（仮説・保留）は遡りでは動かさない。moon の印だけ"""
        monitor = self._started([(2, JOIN), (10, _start("Mystic Moon")), (20, _start("Alternate")),
                                 (30, _start("Blood Moon"))])
        fresh = RoundSequence.RoundSequence()
        self.assertEqual(monitor.sequence._hyps, fresh._hyps)
        self.assertEqual(monitor.sequence._pending, [])
        self.assertEqual(monitor.sequence.rounds, [])
        self.assertTrue(monitor.sequence.moon_done["Mystic Moon"])
        self.assertTrue(monitor.sequence.moon_done["Blood Moon"])
        self.assertFalse(monitor.sequence.moons_unlocked())

    def test_mark_moons_seen_ignores_other_rounds_and_empty_input(self):
        sequence = RoundSequence.RoundSequence()
        sequence.mark_moons_seen(["Fog", "Classic", "Special", "Mystic Moon"])
        sequence.mark_moons_seen(None)
        sequence.mark_moons_seen([])
        self.assertEqual({name for name, done in sequence.moon_done.items() if done}, {"Mystic Moon"})


class TestItemAtTheRestart(_Case):
    """③ 遡りで所持が分かったら、item_id も合わせる"""

    def test_no_equipping_after_the_joining_means_no_item(self):
        monitor = self._started([(2, JOIN), (10, _start("Fog"))])
        self.assertEqual(monitor.st.held_item_id, 0)
        self.assertEqual(monitor.st.item_id, 0)
        self.assertEqual(monitor.st.item_id_at_round_start, 0)

    def test_an_equipping_line_sets_the_item(self):
        monitor = self._started([(2, JOIN), (5, "Equipping 36.")])
        self.assertEqual(monitor.st.held_item_id, 36)
        self.assertEqual(monitor.st.item_id, 36)
        self.assertEqual(monitor.st.item_id_at_round_start, 36)

    def test_an_equipping_before_the_last_joining_does_not_count(self):
        monitor = self._started([(2, "Equipping 36."), (3, JOIN)])
        self.assertEqual(monitor.st.item_id, 0)

    def test_without_a_joining_line_nothing_changes(self):
        fresh = State.WindowState()
        monitor = self._started([(5, "Equipping 36."), (10, _start("Fog"))])
        self.assertEqual(monitor.st.held_item_id, 0, "入室が見つからない遡りは所持も戻さない（今までどおり）")
        self.assertEqual(monitor.st.item_id, fresh.item_id)
        self.assertEqual(monitor.st.item_id_at_round_start, fresh.item_id_at_round_start)

    def test_other_fields_are_left_alone(self):
        fresh = State.WindowState()
        monitor = self._started([(2, JOIN), (5, "Equipping 36.")])
        self.assertEqual(monitor.st.item_lost_this_round, fresh.item_lost_this_round)
        self.assertEqual(monitor.st.last_lost_item_id, fresh.last_lost_item_id)


class TestLogShrinks(unittest.TestCase):
    """④ ログが縮んだら、先頭から読み直さず末尾へ飛ぶ（状態だけ遡って取り戻す）"""

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        patch.object(config, "LOG_POLL_INTERVAL", 0.01).start()
        patch.object(ConnectDB, "send_Users", return_value=7).start()
        patch.object(ConnectDB, "register_round").start()
        patch.object(PlaySound, "play_sound").start()
        patch.object(AccessCheck, "recheck_async").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.threads = []
        self.addCleanup(self._stop_all)

    def _stop_all(self):
        for monitor, thread in self.threads:
            monitor._running = False
            monitor._stop_event.set()
            thread.join(5)

    @staticmethod
    def _text(rows):
        return "".join(_at(sec) + line + "\n" for sec, line in rows)

    def _wait_for(self, condition, what, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail(f"待ったが起きなかった: {what}")

    def _running(self, rows):
        """rows のログで監視を始め、「監視開始」まで待つ。(path, monitor, 先頭の rows の長さ) を返す"""
        path = Path(self._dir.name) / "output_log.txt"
        path.write_bytes(self._text(rows).encode("utf-8"))
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path), {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._verify_access = lambda: True
        monitor._start_daemon = lambda f, *a: None
        monitor._process_lines = MagicMock()
        monitor._running = True
        monitor._stop_event.clear()
        thread = threading.Thread(target=monitor._run, daemon=True)
        self.threads.append((monitor, thread))
        thread.start()
        self._wait_for(lambda: any("監視開始" in m for m in monitor.logs), "監視開始")
        return path, monitor

    def _processed(self, monitor):
        return [line for call in monitor._process_lines.call_args_list for line in call.args[0]]

    def test_only_the_new_line_after_the_shrink_is_processed(self):
        head = [(1, f"User Authenticated: serim01 ({ME})"), (2, JOIN), (10, _start("Fog"))]
        more = [(20 + n, "Killers have been set - 5 0 0 // Round type is Fog") for n in range(5)]
        path, monitor = self._running(head + more)
        self.assertEqual(self._processed(monitor), [], "始めたときの中身は処理しない")
        before = path.stat().st_size

        with open(path, "r+b") as f:                    # 前の中身の先頭だけが残る（ログが短くなる）
            f.truncate(len(self._text(head).encode("utf-8")))
        self._wait_for(lambda: any("ログが短くなりました" in m for m in monitor.logs), "縮みの検出")
        self.assertLess(path.stat().st_size, before, "前提: 縮んでいる")
        new_line = _at(50) + "Equipping 12.\n"
        with open(path, "ab") as f:
            f.write(new_line.encode("utf-8"))

        self._wait_for(lambda: self._processed(monitor), "新しい 1 行の処理")
        time.sleep(0.1)
        self.assertEqual(self._processed(monitor), [new_line.rstrip("\n").encode("utf-8")],
                         "新しい 1 行だけ。縮む前の中身（ラウンド開始など）は処理しない")
        self.assertEqual(monitor.st.log_pos, path.stat().st_size)

    def test_the_state_is_taken_back_from_the_remaining_log(self):
        head = [(1, f"User Authenticated: serim01 ({ME})"), (2, JOIN), (3, "Equipping 36.")]
        path, monitor = self._running(head + [(20 + n, "Equipping 12.") for n in range(5)])
        self.assertEqual(monitor.st.held_item_id, 12)

        with open(path, "r+b") as f:
            f.truncate(len(self._text(head).encode("utf-8")))
        self._wait_for(lambda: any("ログが短くなりました" in m for m in monitor.logs), "縮みの検出")
        self._wait_for(lambda: monitor.st.held_item_id == 36, "残った中身から所持を取り戻す")
        self.assertEqual(monitor.st.item_id, 36)
        self.assertEqual(self._processed(monitor), [])

    def test_a_log_that_does_not_shrink_is_read_as_before(self):
        head = [(1, f"User Authenticated: serim01 ({ME})"), (2, JOIN)]
        path, monitor = self._running(head)
        lines = [_at(30) + "Equipping 12.\n", _at(31) + "Equipping 13.\n"]
        with open(path, "ab") as f:
            f.write("".join(lines).encode("utf-8"))

        self._wait_for(lambda: len(self._processed(monitor)) >= 2, "追記の処理")
        self.assertEqual(self._processed(monitor), [line.rstrip("\n").encode("utf-8") for line in lines])
        self.assertFalse(any("ログが短くなりました" in m for m in monitor.logs))
        self.assertEqual(monitor.st.log_pos, path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
