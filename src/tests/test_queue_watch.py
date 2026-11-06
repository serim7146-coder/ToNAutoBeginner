"""待機列に入れるようになった合図（QueueWatch）。VRChat のログの Get / POST / Delete / Joining から読む。

ID は伏せた形（wrld_X・grp_X・番号は 5 桁）。本物の窓の並びを写したものを使い、本物のログは読まない。
スマホへは送らない（PhoneNotify.send を偽物にする）。時計は差し替える"""
from tests.support import *  # noqa: F401,F403

import QueueWatch
import PhoneNotify

WORLD = "wrld_X"
TAIL = "~group(grp_X)~groupAccessType(plus)~region(jp)"
A, B = 1, 2             # 窓の番号
DATE = "2026.11.06"


def inst(number) -> str:
    """インスタンスの印（`wrld_…:番号~…` の全部）"""
    return f"{WORLD}:{number}{TAIL}"


def line(hms: str, text: str) -> str:
    return f"{DATE} {hms} Debug      -  {text}"


def post(hms, number):
    return line(hms, f"[API] Requesting Post instances/{inst(number)}/queue {{{{}}}} retryCount: 2")


def get(hms, number):
    return line(hms, f"[API] Requesting Get instances/{inst(number)} {{{{}}}} retryCount: 2")


def delete(hms, number):
    return line(hms, f"[API] Requesting Delete instances/{inst(number)}/queue {{{{}}}} retryCount: 2")


def joining(hms, number):
    return line(hms, f"[Behaviour] Joining {inst(number)}")


def other(hms, text="[Behaviour] OnPlayerJoined someone"):
    return line(hms, text)


class Clock:
    """実時間の代わり（tick を決まった通りに動かす）"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, sec):
        self.now += sec


class _Base(unittest.TestCase):
    def setUp(self):
        QueueWatch.reset()
        self.addCleanup(QueueWatch.reset)
        self.clock = Clock()
        self.fired = []                 # (読んだ窓, 待機列に入れた窓の記録)

    def window(self, idx, hands_free=False):
        win = QueueWatch.Candidates(idx, lambda p, i=idx: self.fired.append((i, p)),
                                    hands_free=lambda: hands_free, clock=self.clock)
        return _Window(win)

    def windows_that_fired(self):
        return [p.window_idx for _i, p in self.fired]


class _Window:
    def __init__(self, win):
        self.win = win

    def read(self, *lines):
        for text in lines:
            self.win.feed(LogParser.log_time(text), text)

    def tick(self):
        self.win.tick()


class TestRealSequence(_Base):
    """本物の 2 窓の並び（ID を伏せたもの）。窓 A の合図が 1 回だけ出る"""

    def test_it_fires_once_and_names_the_window_that_queued(self):
        a, b = self.window(A), self.window(B)
        b.read(post("16:20:13", 6515))
        a.read(delete("16:20:37", 6515), post("16:20:37", 39837))
        # 窓 B が、たくさんの別のインスタンスの Get をまとめて読む（06515 と 39837 も入る）。合図にしない
        b.read(get("16:23:36", 11111), get("16:23:37", 6515), get("16:23:38", 22222),
               get("16:23:39", 39837), get("16:23:40", 33333), get("16:23:41", 44444))
        self.clock.advance(10)
        a.tick()
        b.tick()
        self.assertEqual(self.fired, [])
        # 窓 A の Get が 1 つだけ。窓 B はその 2 秒後
        a.read(get("16:25:57", 39837))
        b.read(get("16:25:59", 39837))
        self.clock.advance(3)
        a.tick()
        b.tick()
        self.assertEqual(self.windows_that_fired(), [A])
        self.assertEqual(self.fired[0][0], A)

    def test_the_fired_record_is_forgotten_so_a_later_get_does_not_fire_again(self):
        a, b = self.window(A), self.window(B)
        a.read(post("16:20:37", 39837))
        a.read(get("16:25:57", 39837))
        self.clock.advance(3)
        a.tick()
        b.read(get("16:26:30", 39837))
        self.clock.advance(3)
        b.tick()
        self.assertEqual(self.windows_that_fired(), [A])


class TestRule(_Base):
    def setUp(self):
        super().setUp()
        self.a = self.window(A)
        self.a.read(post("10:00:00", 39837))

    def test_a_lone_get_fires_from_the_tick_without_another_line(self):
        self.a.read(get("10:01:00", 39837))
        self.clock.advance(2.9)
        self.a.tick()
        self.assertEqual(self.fired, [])
        self.clock.advance(0.1)
        self.a.tick()
        self.assertEqual(self.windows_that_fired(), [A])

    def test_a_line_three_seconds_later_in_the_log_closes_it(self):
        self.a.read(get("10:01:00", 39837))
        self.a.read(other("10:01:02"))
        self.assertEqual(self.fired, [])
        self.a.read(other("10:01:03"))
        self.assertEqual(self.windows_that_fired(), [A])

    def test_the_tick_does_not_close_it_before_the_batch_is_read(self):
        """まとめて読んだ後ろの行（3 秒以内の別の Get）が先。tick はその後"""
        self.a.read(get("10:01:00", 39837))
        self.clock.advance(5)                       # 実時間は過ぎていても、行を読むあいだは閉じない
        self.a.read(get("10:01:02", 55555))
        self.a.tick()
        self.assertEqual(self.fired, [])

    def test_a_different_instance_get_within_three_seconds_before_cancels(self):
        for gap, expect in ((1, False), (3, False), (4, True)):
            with self.subTest(gap=gap):
                QueueWatch.reset()
                self.fired.clear()
                win = self.window(A)
                win.read(post("10:00:00", 39837))
                win.read(get(f"10:01:{10 - gap:02d}", 55555))
                win.read(get("10:01:10", 39837))
                self.clock.advance(3)
                win.tick()
                self.assertEqual(bool(self.fired), expect)

    def test_a_different_instance_get_within_three_seconds_after_cancels(self):
        for gap, expect in ((1, False), (3, False), (4, True)):
            with self.subTest(gap=gap):
                QueueWatch.reset()
                self.fired.clear()
                win = self.window(A)
                win.read(post("10:00:00", 39837))
                win.read(get("10:01:10", 39837))
                win.read(get(f"10:01:{10 + gap:02d}", 55555))
                self.clock.advance(3)
                win.tick()
                self.assertEqual(bool(self.fired), expect)

    def test_a_get_of_the_same_instance_nearby_does_not_count(self):
        self.a.read(get("10:01:00", 39837), get("10:01:01", 39837), get("10:01:02", 39837))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.windows_that_fired(), [A])

    def test_menu_lines_are_not_part_of_the_rule(self):
        self.a.read(other("10:00:59", "[Menu] opened"), get("10:01:00", 39837),
                    other("10:01:01", "[Menu] closed"))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.windows_that_fired(), [A])

    def test_a_get_of_a_different_instance_in_another_window_does_not_count(self):
        b = self.window(B)
        b.read(get("10:01:01", 55555))
        self.a.read(get("10:01:00", 39837))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.windows_that_fired(), [A])

    def test_the_post_less_than_ten_seconds_earlier_does_not_fire(self):
        win = self.window(B)
        win.read(post("11:00:00", 77777), get("11:00:09", 77777))
        self.clock.advance(3)
        win.tick()
        self.assertEqual(self.fired, [])

    def test_the_post_exactly_ten_seconds_earlier_fires(self):
        win = self.window(B)
        win.read(post("11:00:00", 77777), get("11:00:10", 77777))
        self.clock.advance(3)
        win.tick()
        self.assertEqual(self.windows_that_fired(), [B])

    def test_an_instance_that_was_not_queued_does_not_fire(self):
        self.a.read(get("10:01:00", 12345))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.fired, [])

    def test_a_delete_forgets_it(self):
        self.a.read(delete("10:00:30", 39837), get("10:01:00", 39837))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.fired, [])

    def test_a_delete_between_the_get_and_the_close_forgets_it(self):
        self.a.read(get("10:01:00", 39837), delete("10:01:01", 39837))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.fired, [])

    def test_joining_forgets_it(self):
        self.a.read(joining("10:00:30", 39837), get("10:01:00", 39837))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.fired, [])

    def test_joining_another_instance_does_not_forget_it(self):
        self.a.read(joining("10:00:30", 11111), get("10:01:00", 39837))
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.windows_that_fired(), [A])

    def test_three_hours_forget_it(self):
        for sec, expect in ((3 * 3600 - 1, True), (3 * 3600, False)):
            with self.subTest(sec=sec):
                QueueWatch.reset()
                self.fired.clear()
                win = self.window(A)
                win.read(post("10:00:00", 39837))
                end = 10 * 3600 + sec
                win.read(get("%02d:%02d:%02d" % (end // 3600, end % 3600 // 60, end % 60), 39837))
                self.clock.advance(3)
                win.tick()
                self.assertEqual(bool(self.fired), expect)

    def test_a_window_that_did_not_post_can_fire(self):
        b = self.window(B)
        b.read(get("10:01:00", 39837))
        self.clock.advance(3)
        b.tick()
        self.assertEqual(self.fired[0][0], B)
        self.assertEqual(self.fired[0][1].window_idx, A)

    def test_only_one_window_fires_per_queue(self):
        b = self.window(B)
        self.a.read(get("10:01:00", 39837))
        b.read(get("10:01:00", 39837))
        self.clock.advance(3)
        self.a.tick()
        b.tick()
        self.assertEqual(len(self.fired), 1)

    def test_only_posts_read_after_the_start_count(self):
        """監視を始める前の POST は読まない（さかのぼって探さない）"""
        QueueWatch.reset()
        win = self.window(A)
        win.read(get("10:01:00", 39837))
        self.clock.advance(3)
        win.tick()
        self.assertEqual(self.fired, [])

    def test_hands_free_is_taken_when_the_post_line_is_read(self):
        flag = {"on": True}
        win = QueueWatch.Candidates(A, lambda p: self.fired.append(p), hands_free=lambda: flag["on"],
                                    clock=self.clock)
        text = post("12:00:00", 88888)
        win.feed(LogParser.log_time(text), text)
        flag["on"] = False                       # 読んだあとに変わっても、記録は読んだ時点のまま
        text = get("12:01:00", 88888)
        win.feed(LogParser.log_time(text), text)
        self.clock.advance(3)
        win.tick()
        self.assertEqual(len(self.fired), 1)
        self.assertTrue(self.fired[0].hands_free)

    def test_lines_joined_by_vrchat_are_read(self):
        joined = "2026.11.06 10:01:00 Debug      -  " + get("10:01:00", 39837)
        self.a.read(joined)
        self.clock.advance(3)
        self.a.tick()
        self.assertEqual(self.windows_that_fired(), [A])


class TestMonitor(_Base):
    """LogMonitor を通した合図。ツールのログ・debug.log・スマホ"""

    TOPIC = "tonab-unit_test-topic-0123456789abcdef"

    def setUp(self):
        super().setUp()
        self.logs = []
        cfg = WindowConfig(hwnd=0x40)
        self.monitor = LogMonitor.LogMonitor(cfg, {}, self.logs.append, window_idx=A)
        self.monitor._queue_watch._clock = self.clock
        self.monitor._running = True
        self.sent = []
        patcher = patch.object(PhoneNotify, "send",
                               side_effect=lambda topic, title, message, on_done=None: self.sent.append(
                                   (topic, title, message)))
        patcher.start()
        self.addCleanup(patcher.stop)
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(self.TOPIC)
        self.addCleanup(SharedState.set_phone_notify, False)
        self.addCleanup(SharedState.set_phone_topic, "")
        SharedState.set_hands_free(False)
        self.addCleanup(SharedState.set_hands_free, False)
        self.debug_path = config.DEBUG_LOG_PATH

    def run_sequence(self):
        m = self.monitor
        m._process(post("16:20:37", 39837))
        m._process(get("16:25:57", 39837))
        self.clock.advance(3)
        m._queue_watch.tick()

    def test_it_logs_and_sends_the_window_that_queued(self):
        self.run_sequence()
        self.assertEqual(self.sent, [(self.TOPIC, "ToNAutoBeginner", "窓1 待機列に入れるようになりました")])
        self.assertIn("[窓1] 待機列に入れるようになりました", self.logs)

    def test_a_window_that_did_not_queue_names_the_window_that_did(self):
        QueueWatch.note_post(inst(39837), LogParser.log_time(post("16:20:37", 39837)), 3, False)
        self.monitor._process(get("16:25:57", 39837))
        self.clock.advance(3)
        self.monitor._queue_watch.tick()
        self.assertEqual(self.sent[0][2], "窓3 待機列に入れるようになりました")

    def test_a_hands_free_window_that_queued_logs_but_sends_nothing(self):
        SharedState.set_hands_free(True)
        self.monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.run_sequence()
        self.assertEqual(self.sent, [])
        self.assertIn("[窓1] 待機列に入れるようになりました", self.logs)

    def test_the_hands_free_of_the_window_that_queued_decides_not_the_one_that_saw_it(self):
        QueueWatch.note_post(inst(39837), LogParser.log_time(post("16:20:37", 39837)), 3, True)
        SharedState.set_hands_free(False)
        self.monitor._process(get("16:25:57", 39837))
        self.clock.advance(3)
        self.monitor._queue_watch.tick()
        self.assertEqual(self.sent, [])
        self.assertTrue(any("待機列に入れるようになりました" in m for m in self.logs))

    def test_off_sends_nothing_but_the_tool_still_logs(self):
        SharedState.set_phone_notify(False)
        self.run_sequence()
        self.assertEqual(self.sent, [])
        self.assertIn("[窓1] 待機列に入れるようになりました", self.logs)

    def test_no_instance_world_or_group_id_reaches_any_log(self):
        m = self.monitor
        m._process(post("16:20:13", 6515))
        m._process(delete("16:20:37", 6515))
        m._process(post("16:20:37", 39837))
        m._process(get("16:23:36", 11111))
        m._process(get("16:23:37", 6515))
        m._process(joining("16:24:00", 11111))
        m._process(get("16:25:57", 39837))
        self.clock.advance(3)
        m._queue_watch.tick()
        self.assertEqual(len(self.sent), 1)
        debug = self.debug_path.read_text(encoding="utf-8") if self.debug_path.exists() else ""
        text = "\n".join(self.logs) + "\n" + debug + "\n" + repr(self.sent)
        for word in ("wrld_", "grp_", "39837", "06515", "6515", "11111", "~group", "region("):
            self.assertNotIn(word, text)

    def test_an_error_in_the_watch_does_not_stop_the_line_from_being_read(self):
        self.monitor._queue_watch.feed = MagicMock(side_effect=RuntimeError("boom"))
        self.monitor._process(other("16:00:00", "something happened"))        # 例外が外へ出ない
        self.monitor._process(other("16:00:01", "something else happened"))
        self.assertEqual(self.monitor.st.log_now, LogParser.log_time(other("16:00:01")))


if __name__ == "__main__":
    unittest.main()
