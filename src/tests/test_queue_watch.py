"""待機列に入れるようになった合図（QueueWatch）。VRChat のログの Get / POST / Delete / Joining から読む。

マクロを開始していなくても、通知する が ON のあいだツールが自分でログを読む（Watcher）。窓の番号は使わない。

ID は伏せた形（wrld_X・grp_X・番号は 5 桁）。本物の窓の並びを写したものを使い、本物のログは読まない
（一時フォルダに偽の output_log_*.txt を作る）。スマホへは送らない（PhoneNotify.send を偽物にする）。
時計は差し替える"""
from tests.support import *  # noqa: F401,F403

import QueueWatch
import PhoneNotify
import PlaySound

WORLD = "wrld_X"
TAIL = "~group(grp_X)~groupAccessType(plus)~region(jp)"
DATE = "2026.11.06"
TOPIC = "tonab-unit_test-topic-0123456789abcdef"
LOG_A = "output_log_2026-11-06_19-00-00.txt"
LOG_B = "output_log_2026-11-06_19-30-00.txt"
VOICE = "C:/voices/Queue.mp3"          # 偽のパス（鳴らさない）
_REAL_PLAY_SOUND = PlaySound.play_sound     # _Env が偽物に差し替える前の本物


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


def at(hms) -> float:
    """ログの時刻（epoch 秒）"""
    return LogParser.log_time(line(hms, "x"))


class Clock:
    """実時間（monotonic）の代わり"""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, sec):
        self.now += sec


class _Env(unittest.TestCase):
    """一時フォルダの偽ログ・差し替えた時計・偽の送信。Watcher は notify_ready につなぐ（本番と同じ）"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tonab_queue_"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        QueueWatch.reset()
        self.addCleanup(QueueWatch.reset)
        self.clock = Clock()
        self.wall = Clock(at("19:50:34"))
        self.logs, self.sent = [], []
        patcher = patch.object(PhoneNotify, "send",
                               side_effect=lambda topic, title, message, on_done=None: self.sent.append(
                                   (topic, title, message)))
        patcher.start()
        self.addCleanup(patcher.stop)
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(TOPIC)
        SharedState.set_hands_free(False)
        SharedState.set_queue_voice(VOICE)
        self.addCleanup(SharedState.set_queue_voice, "")
        self.played = []                    # 声は鳴らさない（PlaySound.play_sound を偽物にする）
        sound = patch.object(PlaySound, "play_sound",
                             side_effect=lambda path, kind=None, force=False: self.played.append((path, kind)))
        sound.start()
        self.addCleanup(sound.stop)
        self.addCleanup(SharedState.set_phone_notify, False)
        self.addCleanup(SharedState.set_phone_topic, "")
        self.addCleanup(SharedState.set_hands_free, False)
        self.watcher = QueueWatch.Watcher(self.dir, lambda: QueueWatch.notify_ready(self.logs.append),
                                          clock=self.clock, wall=self.wall)

    # ── 偽のログ ──
    def path(self, name=LOG_A) -> Path:
        return self.dir / name

    def write(self, name, *lines, newline=True):
        """ログに行を足す。更新時刻は今（wall）にする"""
        data = "".join(text + ("\n" if newline else "") for text in lines).encode("utf-8")
        self.append_bytes(name, data)

    def append_bytes(self, name, data: bytes):
        with open(self.path(name), "ab") as f:
            f.write(data)
        self.touch(name)

    def touch(self, name, age=0.0):
        stamp = self.wall.now - age
        os.utime(self.path(name), (stamp, stamp))

    # ── 時間 ──
    def pass_time(self, sec):
        self.clock.advance(sec)
        self.wall.advance(sec)

    def poll(self):
        self.watcher.poll()

    def settle(self):
        """後ろの 3 秒を過ぎさせて読む（候補が閉じる）"""
        self.pass_time(3)
        self.poll()

    def texts(self):
        return [m for _t, _ti, m in self.sent]


class TestCompletion(_Env):
    def test_a_lone_get_after_a_post_sends_once_without_any_macro_or_window(self):
        """今日の実例: POST 19:47:50・Get 19:50:32。マクロは動いていない・窓の割り当てもない"""
        self.write(LOG_A, other("19:47:00"), post("19:47:50", 39837), other("19:48:10"),
                   get("19:50:32", 39837))
        self.touch(LOG_A)
        self.poll()
        self.assertEqual(self.sent, [], "後ろの 3 秒が閉じるまで送らない")
        self.settle()
        self.assertEqual(self.sent, [(TOPIC, PhoneNotify.APP_TITLE, "待機列に入れるようになりました")])
        self.assertEqual(self.logs, ["[スマホ通知] 待機列に入れるようになりました"])
        self.pass_time(10)
        self.poll()
        self.assertEqual(len(self.sent), 1, "使い切った記録で出直さない")

    def test_the_text_has_no_window_number(self):
        self.assertEqual(PhoneNotify.queue_ready_text(), (PhoneNotify.APP_TITLE, "待機列に入れるようになりました"))

    def test_hands_free_still_sends(self):
        SharedState.set_hands_free(True)
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.poll()
        self.settle()
        self.assertEqual(self.texts(), ["待機列に入れるようになりました"])


class TestQueueVoice(_Env):
    """合図で声「待機列」を 1 回鳴らす。「通知する」が ON のときだけ。声と送信は互いに影響しない"""

    def fire(self):
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.poll()
        self.settle()

    def test_on_plays_once_with_the_configured_path_and_sends_once(self):
        self.fire()
        self.assertEqual(self.played, [(VOICE, "queue")])
        self.assertEqual(len(self.sent), 1)
        self.pass_time(10)
        self.poll()
        self.assertEqual(len(self.played), 1)

    def test_phone_notify_off_plays_nothing_and_sends_nothing(self):
        SharedState.set_phone_notify(False)
        self.fire()
        self.assertEqual(self.played, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.logs, ["[スマホ通知] 待機列に入れるようになりました"], "ツールのログは書く（今までどおり）")

    def test_an_invalid_topic_still_plays_and_does_not_send(self):
        SharedState.set_phone_topic("bad topic!")
        self.fire()
        self.assertEqual(self.played, [(VOICE, "queue")])
        self.assertEqual(self.sent, [])

    def test_a_failing_sound_still_sends(self):
        PlaySound.play_sound.side_effect = RuntimeError("boom")
        self.fire()
        self.assertEqual(len(self.sent), 1)

    def test_a_failing_send_does_not_escape_and_the_sound_already_played(self):
        PhoneNotify.send.side_effect = RuntimeError("boom")
        self.fire()
        self.assertEqual(self.played, [(VOICE, "queue")])

    def test_a_signal_whose_get_is_60_seconds_old_makes_no_sound(self):
        """VRChat の通知が消えている古い合図は、黙って使い切る（声も送信も無い）"""
        self.wall.now = at("19:50:32") + 60
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.touch(LOG_A)
        self.poll()
        self.settle()
        self.assertEqual(self.played, [])
        self.assertEqual(self.sent, [])

    def test_the_same_get_in_two_logs_plays_once(self):
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.write(LOG_B, get("19:50:32", 39837))
        self.poll()
        self.settle()
        self.assertEqual(len(self.played), 1)
        self.assertEqual(len(self.sent), 1)

    def test_hands_free_still_plays(self):
        SharedState.set_hands_free(True)
        self.fire()
        self.assertEqual(self.played, [(VOICE, "queue")])

    def test_the_real_play_sound_honours_the_voice_checkbox(self):
        """本物の PlaySound で、声のチェックが OFF なら再生のスレッドを起こさない（音は鳴らさない）"""
        self.addCleanup(PlaySound.set_voice_enabled, "queue", True)
        PlaySound.set_voice_enabled("queue", False)
        with patch.object(PlaySound, "play_sound", _REAL_PLAY_SOUND),              patch("pathlib.Path.exists", return_value=True),              patch.object(PlaySound.TaskStats, "thread") as thread:
            self.fire()
            thread.assert_not_called()
            PlaySound.set_voice_enabled("queue", True)
            _REAL_PLAY_SOUND(VOICE, kind="queue")
            thread.assert_called_once()     # ON なら再生に進む（スレッドは偽物なので鳴らない）
        self.assertEqual(len(self.sent), 1, "声が OFF でもスマホへは送る")


class TestAcrossLogs(_Env):
    def test_a_post_in_one_log_and_the_get_in_another_fires_once(self):
        self.write(LOG_A, post("19:47:50", 39837))
        self.write(LOG_B, other("19:48:00"))
        self.poll()
        self.write(LOG_B, get("19:50:32", 39837))
        self.pass_time(1)
        self.poll()
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_the_same_get_in_two_logs_a_couple_of_seconds_apart_sends_once(self):
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.write(LOG_B, get("19:50:34", 39837))
        self.poll()
        self.settle()
        self.pass_time(5)
        self.poll()
        self.assertEqual(len(self.sent), 1)

    def test_the_first_read_judges_lines_of_all_logs_in_time_order(self):
        """ログ B の POST がログ A の Get より前。ログ A を先に読んでも、時刻の順に判定する"""
        self.write(LOG_A, get("19:50:32", 39837))
        self.write(LOG_B, post("19:47:50", 39837))
        self.poll()
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_a_delete_in_one_log_after_the_post_in_another_log_forgets_it(self):
        """新しい名前のログ（B）は先に読む。それでも時刻は A の POST（19:48）→ B の Delete（19:49）の順"""
        self.write(LOG_B, delete("19:49:00", 39837), get("19:50:32", 39837))
        self.write(LOG_A, post("19:48:00", 39837))
        self.poll()
        self.settle()
        self.assertEqual(self.sent, [])

    def test_the_post_in_a_later_log_after_the_get_does_not_fire(self):
        self.write(LOG_A, get("19:47:50", 39837))
        self.write(LOG_B, post("19:50:32", 39837))
        self.poll()
        self.settle()
        self.assertEqual(self.sent, [])


class TestCatchUp(_Env):
    def test_a_signal_older_than_sixty_seconds_in_the_first_read_is_silent(self):
        self.write(LOG_A, post("19:40:00", 39837), get("19:41:00", 39837))
        self.poll()
        self.settle()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.logs, [])

    def test_a_consumed_record_does_not_fire_on_a_later_lone_get(self):
        self.write(LOG_A, post("19:40:00", 39837), get("19:41:00", 39837))
        self.poll()
        self.settle()
        self.pass_time(10)
        self.write(LOG_A, get(datetime.fromtimestamp(self.wall.now).strftime("%H:%M:%S"), 39837))
        self.poll()
        self.settle()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.logs, [])

    def _closing_at(self, seconds_after_get):
        """Get のログの時刻から seconds_after_get 秒後に、候補が閉じるようにする"""
        self.wall.now = at("19:50:32") + seconds_after_get - 3
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.poll()
        self.settle()

    def test_a_signal_closed_fifty_nine_seconds_after_the_get_is_sent(self):
        self._closing_at(59)
        self.assertEqual(len(self.sent), 1)

    def test_a_signal_closed_sixty_seconds_after_the_get_is_silent(self):
        self._closing_at(60)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.logs, [])

    def test_a_post_before_the_start_and_a_get_afterwards_fires_once(self):
        self.write(LOG_A, post("19:47:50", 39837))
        self.poll()                 # 見張りが始まる。POST を読む
        self.pass_time(10)
        self.write(LOG_A, get("19:50:32", 39837))
        self.wall.now = at("19:50:34")
        self.poll()
        self.settle()
        self.assertEqual(len(self.sent), 1)


class TestDelete(_Env):
    def test_a_new_post_with_a_delete_of_the_earlier_queue_does_not_fire_for_the_earlier(self):
        """本物の並び: 前の待機列を Delete して、新しい待機列に POST する"""
        self.write(LOG_A, post("19:45:00", 6515), delete("19:47:50", 6515), post("19:47:50", 39837),
                   get("19:50:32", 6515))
        self.poll()
        self.settle()
        self.assertEqual(self.sent, [])

    def test_the_new_queue_still_fires(self):
        self.write(LOG_A, post("19:45:00", 6515), delete("19:47:50", 6515), post("19:47:50", 39837),
                   get("19:50:32", 6515))
        self.poll()
        self.settle()
        self.write(LOG_A, get("19:51:30", 39837))
        self.pass_time(56)
        self.poll()
        self.settle()
        self.assertEqual(len(self.sent), 1)


class TestHalfWrittenLine(_Env):
    def test_a_line_cut_in_the_marker_or_in_a_multibyte_char_is_judged_when_it_completes(self):
        full_get = (get("19:50:32", 39837) + " 日本語\n").encode("utf-8")
        cuts = {"マーカーの途中": full_get.index(b"instances/") + 4,
                "日本語の文字の途中": full_get.index("日".encode("utf-8")) + 1,
                "時刻の途中": 8}
        for label, cut in cuts.items():
            with self.subTest(label=label):
                QueueWatch.reset()
                self.sent.clear()
                self.path(LOG_A).unlink(missing_ok=True)
                self.watcher = QueueWatch.Watcher(self.dir, lambda: QueueWatch.notify_ready(self.logs.append),
                                                  clock=self.clock, wall=self.wall)
                self.wall.now = at("19:50:34")
                self.append_bytes(LOG_A, (post("19:47:50", 39837) + "\n").encode("utf-8") + full_get[:cut])
                self.poll()
                self.settle()
                self.assertEqual(self.sent, [], "書きかけの行は判定に使わない")
                self.append_bytes(LOG_A, full_get[cut:])
                self.poll()
                self.settle()
                self.assertEqual(len(self.sent), 1)

    def test_a_trailing_carriage_return_is_stripped(self):
        self.append_bytes(LOG_A, (post("19:47:50", 39837) + "\r\n" + get("19:50:32", 39837) + "\r\n").encode("utf-8"))
        self.poll()
        self.settle()
        self.assertEqual(len(self.sent), 1)


class TestReading(_Env):
    def test_a_shrunk_log_is_read_again_from_the_start(self):
        self.write(LOG_A, *[other("19:00:00", "x" * 200) for _ in range(5)], post("19:00:10", 11111))
        self.poll()
        size = self.path(LOG_A).stat().st_size
        self.path(LOG_A).write_text("", encoding="utf-8")
        self.pass_time(10)
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.assertLess(self.path(LOG_A).stat().st_size, size)
        self.wall.now = at("19:50:34")
        self.poll()
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_a_log_that_did_not_grow_is_not_opened(self):
        self.write(LOG_A, other("19:47:00"))
        self.poll()
        opened = []
        real_open = open

        def spy(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", spy):
            self.pass_time(1)
            self.poll()
            self.pass_time(1)
            self.poll()
        self.assertEqual([p for p in opened if p == str(self.path(LOG_A))], [])
        self.write(LOG_A, other("19:47:02"))
        with patch("builtins.open", spy):
            self.pass_time(1)
            self.poll()
        self.assertEqual([p for p in opened if p == str(self.path(LOG_A))], [str(self.path(LOG_A))])

    def test_lines_that_do_not_matter_are_not_kept(self):
        self.write(LOG_A, other("19:47:00"))
        self.poll()
        log = self.watcher._logs[self.path(LOG_A)]
        self.write(LOG_A, *[other("19:47:00", "x" * 100) for _ in range(50)], post("19:47:50", 39837),
                   "no time, instances/ here", joining("19:47:51", 11111))
        kept = self.watcher._read_new(log, self.clock())
        self.assertEqual([at_ for at_, _text in kept], [at("19:47:50"), at("19:47:51")])

    def test_a_log_older_than_the_grace_is_not_followed_until_it_is_updated(self):
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        self.touch(LOG_A, age=config.LOG_LIVE_GRACE_SEC + 10)
        self.poll()
        self.settle()
        self.assertEqual(self.watcher._logs, {})
        self.assertEqual(self.sent, [])
        self.touch(LOG_A)
        self.pass_time(config.QUEUE_WATCH_SCAN_SEC)
        self.poll()
        self.assertEqual(list(self.watcher._logs), [self.path(LOG_A)])
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_a_new_log_is_found_at_the_next_scan(self):
        self.poll()
        self.write(LOG_B, post("19:47:50", 39837), get("19:50:32", 39837))
        self.pass_time(1)
        self.poll()
        self.assertEqual(self.watcher._logs, {})
        self.pass_time(config.QUEUE_WATCH_SCAN_SEC)
        self.poll()
        self.assertEqual(len(self.watcher._logs), 1)

    def test_a_log_quiet_for_three_hours_is_dropped(self):
        self.write(LOG_A, other("19:47:00"))
        self.poll()
        self.assertEqual(len(self.watcher._logs), 1)
        self.pass_time(config.QUEUE_FORGET_SEC)
        self.poll()
        self.assertEqual(self.watcher._logs, {})

    def test_a_missing_directory_or_file_is_skipped(self):
        watcher = QueueWatch.Watcher(self.dir / "nothing", lambda: None, clock=self.clock, wall=self.wall)
        watcher.poll()
        self.write(LOG_A, other("19:47:00"))
        self.poll()
        self.path(LOG_A).unlink()
        self.pass_time(1)
        self.write(LOG_B, other("19:47:00"))
        self.poll()                 # 消えたログで例外を出さない


class TestRule(_Env):
    def setUp(self):
        super().setUp()
        self.wall.now = at("10:01:02")
        self.write(LOG_A, post("10:00:00", 39837))

    def feed(self, *lines):
        self.write(LOG_A, *lines)
        self.poll()

    def test_a_lone_get_fires_from_the_tick_without_another_line(self):
        self.feed(get("10:01:00", 39837))
        self.pass_time(2.9)
        self.poll()
        self.assertEqual(self.sent, [])
        self.pass_time(0.1)
        self.poll()
        self.assertEqual(len(self.sent), 1)

    def test_a_line_three_seconds_later_in_the_log_closes_it(self):
        self.feed(get("10:01:00", 39837), joining("10:01:02", 11111))
        self.assertEqual(self.sent, [])
        self.feed(joining("10:01:03", 22222))
        self.assertEqual(len(self.sent), 1)

    def test_a_different_instance_get_within_three_seconds_cancels(self):
        for where in ("before", "after"):
            for gap, expect in ((1, 0), (3, 0), (4, 1)):
                with self.subTest(where=where, gap=gap):
                    QueueWatch.reset()
                    self.sent.clear()
                    self.logs.clear()
                    self.path(LOG_A).unlink(missing_ok=True)
                    self.watcher = QueueWatch.Watcher(self.dir, lambda: QueueWatch.notify_ready(self.logs.append),
                                                      clock=self.clock, wall=self.wall)
                    sign = -1 if where == "before" else 1
                    other_sec = 10 + sign * gap
                    self.wall.now = at("10:01:14")
                    self.write(LOG_A, post("10:00:00", 39837), get("10:01:10", 39837),
                               get(f"10:01:{other_sec:02d}", 55555))
                    self.poll()
                    self.settle()
                    self.assertEqual(len(self.sent), expect)

    def test_a_get_of_the_same_instance_nearby_does_not_count(self):
        self.feed(get("10:01:00", 39837), get("10:01:01", 39837), get("10:01:02", 39837))
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_menu_lines_are_not_part_of_the_rule(self):
        self.feed(other("10:00:59", "[Menu] opened"), get("10:01:00", 39837), other("10:01:01", "[Menu] closed"))
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_the_post_less_than_ten_seconds_earlier_does_not_fire(self):
        QueueWatch.note_post(inst(77777), at("11:00:00"))
        self.wall.now = at("11:00:12")
        self.feed(get("11:00:09", 77777))
        self.settle()
        self.assertEqual(self.sent, [])

    def test_the_post_exactly_ten_seconds_earlier_fires(self):
        self.wall.now = at("11:00:12")
        self.feed(post("11:00:00", 77777), get("11:00:10", 77777))
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_a_re_post_replaces_the_earlier_one(self):
        """入れ直したら 10 秒は新しい POST から数える"""
        self.wall.now = at("10:01:02")
        self.feed(post("10:00:55", 39837), get("10:01:00", 39837))
        self.settle()
        self.assertEqual(self.sent, [])

    def test_an_instance_that_was_not_queued_does_not_fire(self):
        self.feed(get("10:01:00", 12345))
        self.settle()
        self.assertEqual(self.sent, [])

    def test_a_delete_forgets_it(self):
        self.feed(delete("10:00:30", 39837), get("10:01:00", 39837))
        self.settle()
        self.assertEqual(self.sent, [])

    def test_a_delete_between_the_get_and_the_close_forgets_it(self):
        self.feed(get("10:01:00", 39837), delete("10:01:01", 39837))
        self.settle()
        self.assertEqual(self.sent, [])

    def test_joining_forgets_it(self):
        self.feed(joining("10:00:30", 39837), get("10:01:00", 39837))
        self.settle()
        self.assertEqual(self.sent, [])

    def test_joining_another_instance_does_not_forget_it(self):
        self.feed(joining("10:00:30", 11111), get("10:01:00", 39837))
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_three_hours_forget_it(self):
        for sec, expect in ((3 * 3600 - 1, 1), (3 * 3600, 0)):
            with self.subTest(sec=sec):
                QueueWatch.reset()
                self.sent.clear()
                self.path(LOG_A).unlink(missing_ok=True)
                self.watcher = QueueWatch.Watcher(self.dir, lambda: QueueWatch.notify_ready(self.logs.append),
                                                  clock=self.clock, wall=self.wall)
                end = 10 * 3600 + sec
                hms = "%02d:%02d:%02d" % (end // 3600, end % 3600 // 60, end % 60)
                self.wall.now = at(hms) + 2
                self.write(LOG_A, post("10:00:00", 39837), get(hms, 39837))
                self.poll()
                self.settle()
                self.assertEqual(len(self.sent), expect)

    def test_lines_joined_by_vrchat_are_read(self):
        joined = "2026.11.06 10:01:00 Debug      -  " + get("10:01:00", 39837)
        self.feed(joined)
        self.settle()
        self.assertEqual(len(self.sent), 1)


class TestNotifyReady(unittest.TestCase):
    def setUp(self):
        self.logs, self.sent, self.done = [], [], []
        patcher = patch.object(PhoneNotify, "send",
                               side_effect=lambda topic, title, message, on_done=None: (
                                   self.sent.append((topic, title, message)), self.done.append(on_done)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.played = []
        sound = patch.object(PlaySound, "play_sound",
                             side_effect=lambda path, kind=None, force=False: self.played.append((path, kind)))
        sound.start()
        self.addCleanup(sound.stop)
        SharedState.set_queue_voice(VOICE)
        for setter, value in ((SharedState.set_phone_notify, False), (SharedState.set_phone_topic, ""),
                              (SharedState.set_queue_voice, "")):
            self.addCleanup(setter, value)

    def test_the_sound_is_off_when_notify_is_off(self):
        SharedState.set_phone_notify(False)
        SharedState.set_phone_topic(TOPIC)
        QueueWatch.notify_ready(self.logs.append)
        self.assertEqual(self.played, [])

    def test_the_sound_is_checked_after_the_logger_and_a_sound_error_still_sends(self):
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(TOPIC)
        with patch.object(PlaySound, "play_sound", side_effect=RuntimeError("boom")):
            QueueWatch.notify_ready(self.logs.append)
        self.assertEqual(len(self.sent), 1)

    def test_it_logs_and_sends_when_on_with_a_valid_topic(self):
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(TOPIC)
        QueueWatch.notify_ready(self.logs.append)
        self.assertEqual(self.logs, ["[スマホ通知] 待機列に入れるようになりました"])
        self.assertEqual(self.sent, [(TOPIC, "ToNAutoBeginner", "待機列に入れるようになりました")])

    def test_off_does_not_send(self):
        SharedState.set_phone_notify(False)
        SharedState.set_phone_topic(TOPIC)
        QueueWatch.notify_ready(self.logs.append)
        self.assertEqual(self.sent, [])

    def test_an_invalid_topic_does_not_send(self):
        SharedState.set_phone_notify(True)
        for bad in ("", "bad topic!", None):
            SharedState.set_phone_topic(bad)
            QueueWatch.notify_ready(self.logs.append)
        self.assertEqual(self.sent, [])

    def test_a_failed_send_logs_the_failure_and_a_good_one_does_not(self):
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(TOPIC)
        QueueWatch.notify_ready(self.logs.append)
        self.done[0](True, "")
        self.assertEqual(len(self.logs), 1)
        self.done[0](False, "OSError: down")
        self.assertEqual(self.logs[-1], "スマホ通知: 送信に失敗しました（OSError: down）")

    def test_an_error_while_sending_does_not_escape(self):
        SharedState.set_phone_notify(True)
        SharedState.set_phone_topic(TOPIC)
        with patch.object(PhoneNotify, "send", side_effect=RuntimeError("boom")):
            QueueWatch.notify_ready(self.logs.append)
        self.assertEqual(len(self.logs), 1)


class TestQueuedNotice(_Env):
    """待機列に入れた行（POST）を読んだとき、ツールのログに 1 行出す。声・スマホへの送信はしない"""

    QUEUED = "[スマホ通知] 待機列に入りました"
    READY = "[スマホ通知] 待機列に入れるようになりました"

    def setUp(self):
        super().setUp()
        self.queued_calls = []
        self.watcher = QueueWatch.Watcher(
            self.dir, lambda: QueueWatch.notify_ready(self.logs.append), clock=self.clock, wall=self.wall,
            on_queued=lambda: (self.queued_calls.append(1), QueueWatch.notify_queued(self.logs.append)))

    def test_a_new_post_logs_once_and_sends_nothing(self):
        self.write(LOG_A, post("19:50:20", 39837))
        self.poll()
        self.assertEqual(self.logs, [self.QUEUED])
        self.assertEqual(self.sent, [], "スマホへは送らない")
        self.assertEqual(self.played, [], "声は鳴らさない")
        self.poll()
        self.pass_time(10)
        self.poll()
        self.assertEqual(self.logs, [self.QUEUED], "読み直さない・使い回さない")

    def test_a_post_sixty_seconds_old_or_older_is_silent_and_fifty_nine_is_not(self):
        self.write(LOG_A, post("19:49:34", 39837))          # wall 19:50:34 の 60 秒前
        self.poll()
        self.assertEqual(self.queued_calls, [])
        self.write(LOG_A, post("19:49:35", 39838))          # 59 秒前
        self.poll()
        self.assertEqual(self.queued_calls, [1])

    def test_an_old_post_in_the_first_read_is_silent_but_still_remembered(self):
        self.write(LOG_A, post("19:40:00", 39837), get("19:50:32", 39837))
        self.poll()
        self.settle()
        self.assertEqual(self.queued_calls, [])
        self.assertEqual(self.logs, [self.READY], "合図の動きは今までどおり")

    def test_get_delete_and_joining_do_not_call_it(self):
        self.write(LOG_A, get("19:50:30", 39837), delete("19:50:31", 39837), joining("19:50:32", 39837),
                   other("19:50:33"))
        self.poll()
        self.settle()
        self.assertEqual(self.queued_calls, [])
        self.assertEqual(self.logs, [])

    def test_the_ready_signal_still_works_after_the_notice(self):
        self.write(LOG_A, post("19:50:00", 39837), get("19:50:32", 39837))
        self.poll()
        self.settle()
        self.assertEqual(self.logs, [self.QUEUED, self.READY])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.played), 1)

    def test_no_instance_id_reaches_the_log(self):
        self.write(LOG_A, post("19:50:20", 39837))
        self.poll()
        for text in self.logs:
            for word in ("wrld_", "grp_", "39837"):
                self.assertNotIn(word, text)

    def test_a_watcher_without_on_queued_works_as_before(self):
        watcher = QueueWatch.Watcher(self.dir, lambda: QueueWatch.notify_ready(self.logs.append),
                                     clock=self.clock, wall=self.wall)
        self.write(LOG_A, post("19:50:00", 39837), get("19:50:32", 39837))
        watcher.poll()
        self.pass_time(3)
        watcher.poll()
        self.assertEqual(self.logs, [self.READY])

    def test_a_failing_callback_does_not_stop_the_reading(self):
        def boom():
            raise RuntimeError("boom")

        self.watcher = QueueWatch.Watcher(self.dir, lambda: QueueWatch.notify_ready(self.logs.append),
                                          clock=self.clock, wall=self.wall, on_queued=boom)
        self.write(LOG_A, post("19:50:00", 39837), get("19:50:32", 39837))
        with patch.object(DebugLog, "exception") as recorded:
            self.poll()
            self.settle()
        self.assertTrue(recorded.called)
        self.assertEqual(self.logs, [self.READY], "合図は出る")

    def test_candidates_calls_on_queued_with_the_log_time_only(self):
        calls = []
        cands = QueueWatch.Candidates(lambda _at: None, on_queued=calls.append)
        cands.feed(at("19:50:20"), post("19:50:20", 39837))
        cands.feed(at("19:50:21"), get("19:50:21", 39837))
        cands.feed(None, post("19:50:22", 39838))           # 時刻が読めない行は呼ばない
        self.assertEqual(calls, [at("19:50:20")])

    def test_candidates_swallows_an_error_from_on_queued(self):
        def boom(_at):
            raise RuntimeError("boom")

        cands = QueueWatch.Candidates(lambda _at: None, on_queued=boom)
        with patch.object(DebugLog, "exception") as recorded:
            cands.feed(at("19:50:20"), post("19:50:20", 39837))
        self.assertTrue(recorded.called)

    def test_notify_queued_only_logs_one_line(self):
        QueueWatch.notify_queued(self.logs.append)
        self.assertEqual(self.logs, [self.QUEUED])
        self.assertEqual((self.sent, self.played), ([], []))


class TestSyncQueueWatcherWiring(unittest.TestCase):
    """mainGUI の _sync_queue_watcher は、on_queued にツールのログへ 1 行出す関数をつなぐ（見張りは本物を始めない）"""

    def test_the_watcher_gets_both_callbacks(self):
        logs = []
        app = type("FakeApp", (), {})()
        app._log = logs.append
        self.addCleanup(SharedState.set_phone_notify, False)
        SharedState.set_phone_notify(True)
        with patch.object(QueueWatch, "Watcher") as watcher_class,              patch.object(QueueWatch, "notify_ready") as ready:
            mainGUI.App._sync_queue_watcher(app)
            args, kwargs = watcher_class.call_args
            self.assertEqual(args[0], config.VRCHAT_LOG_DIR)
            args[1]()
            ready.assert_called_once_with(logs.append)
            kwargs["on_queued"]()
        self.assertEqual(logs, ["[スマホ通知] 待機列に入りました"])
        watcher_class.return_value.start.assert_called_once()


class TestNoIdsInLogs(_Env):
    def test_no_topic_instance_world_or_group_id_reaches_any_log(self):
        self.write(LOG_A, post("19:45:00", 6515), delete("19:47:50", 6515), post("19:47:50", 39837),
                   get("19:48:36", 11111), get("19:48:37", 6515), joining("19:49:00", 11111),
                   get("19:50:32", 39837))
        self.poll()
        self.settle()
        self.assertEqual(len(self.sent), 1)
        debug = config.DEBUG_LOG_PATH.read_text(encoding="utf-8") if config.DEBUG_LOG_PATH.exists() else ""
        text = "\n".join(self.logs) + "\n" + debug + "\n" + repr(self.sent[0][1:])
        for word in ("wrld_", "grp_", "39837", "6515", "11111", "~group", "region(", TOPIC):
            self.assertNotIn(word, text)

    def test_an_error_from_the_callback_is_swallowed_and_logged_without_ids(self):
        def boom():
            raise RuntimeError("boom")
        watcher = QueueWatch.Watcher(self.dir, boom, clock=self.clock, wall=self.wall)
        self.write(LOG_A, post("19:47:50", 39837), get("19:50:32", 39837))
        watcher.poll()
        self.pass_time(3)
        watcher.poll()              # 例外が外へ出ない


class TestThread(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tonab_queue_"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        QueueWatch.reset()
        self.addCleanup(QueueWatch.reset)

    def test_start_and_stop(self):
        watcher = QueueWatch.Watcher(self.dir, lambda: None)
        watcher.stop()                          # 始める前でも安全
        watcher.start()
        thread = watcher._thread
        self.assertTrue(thread.is_alive())
        self.assertTrue(thread.daemon)
        self.assertEqual(thread.name, "QueueWatcher")
        watcher.stop()
        self.assertFalse(thread.is_alive())
        watcher.stop()                          # 2 回呼んでも安全

    def test_start_forgets_the_old_records(self):
        QueueWatch.note_post(inst(39837), at("10:00:00"))
        watcher = QueueWatch.Watcher(self.dir, lambda: None)
        watcher.start()
        self.addCleanup(watcher.stop)
        self.assertIsNone(QueueWatch.take_ready(inst(39837), at("10:01:00")))

    def test_the_thread_reads_a_log_and_signals_once(self):
        now = time.time()

        def stamp(offset):
            return datetime.fromtimestamp(now + offset).strftime("%Y.%m.%d %H:%M:%S")

        text = (f"{stamp(-30)} Debug      -  [API] Requesting Post instances/{inst(39837)}/queue {{{{}}}} retryCount: 2\n"
                f"{stamp(-5)} Debug      -  [API] Requesting Get instances/{inst(39837)} {{{{}}}} retryCount: 2\n")
        (self.dir / LOG_A).write_text(text, encoding="utf-8")
        signals = []
        with patch.object(config, "QUEUE_READY_QUIET_SEC", 0.0), \
                patch.object(config, "QUEUE_WATCH_INTERVAL_SEC", 0.05):
            watcher = QueueWatch.Watcher(self.dir, lambda: signals.append(1))
            watcher.start()
            self.addCleanup(watcher.stop)
            deadline = time.monotonic() + 5
            while not signals and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.2)
            watcher.stop()
        self.assertEqual(signals, [1])

    def test_an_error_in_a_cycle_does_not_stop_the_loop(self):
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("boom")
        with patch.object(config, "QUEUE_WATCH_INTERVAL_SEC", 0.02):
            watcher = QueueWatch.Watcher(self.dir, lambda: None)
            watcher.poll = boom
            watcher.start()
            self.addCleanup(watcher.stop)
            deadline = time.monotonic() + 5
            while len(calls) < 3 and time.monotonic() < deadline:
                time.sleep(0.02)
            watcher.stop()
        self.assertGreaterEqual(len(calls), 3)


class _AppForSync:
    """_sync_queue_watcher・_on_close を呼ぶための偽の App（Tk を開かない）"""

    def __init__(self):
        self._queue_watcher = None
        self.logs = []

    def _log(self, msg):
        self.logs.append(msg)


class TestAppWiring(unittest.TestCase):
    def setUp(self):
        self.addCleanup(SharedState.set_phone_notify, False)
        SharedState.set_phone_notify(False)

    def test_sync_starts_and_stops_with_the_setting(self):
        app = _AppForSync()
        with patch.object(QueueWatch, "Watcher") as watcher_class:
            mainGUI.App._sync_queue_watcher(app)
            watcher_class.assert_not_called()               # OFF のあいだは作らない
            SharedState.set_phone_notify(True)
            mainGUI.App._sync_queue_watcher(app)
            mainGUI.App._sync_queue_watcher(app)            # ON のまま呼んでも 1 つだけ
            watcher_class.assert_called_once()
            self.assertEqual(watcher_class.call_args.args[0], config.VRCHAT_LOG_DIR)
            watcher = watcher_class.return_value
            watcher.start.assert_called_once_with()
            self.assertIs(app._queue_watcher, watcher)
            SharedState.set_phone_notify(False)
            mainGUI.App._sync_queue_watcher(app)
            watcher.stop.assert_called_once_with()
            self.assertIsNone(app._queue_watcher)
            mainGUI.App._sync_queue_watcher(app)            # 止めたあとに呼んでも何もしない
            watcher.stop.assert_called_once_with()

    def test_the_signal_goes_to_notify_ready_with_the_app_log(self):
        app = _AppForSync()
        SharedState.set_phone_notify(True)
        with patch.object(QueueWatch, "Watcher") as watcher_class, \
                patch.object(QueueWatch, "notify_ready") as notify:
            mainGUI.App._sync_queue_watcher(app)
            watcher_class.call_args.args[1]()
        notify.assert_called_once_with(app._log)

    def test_loading_the_settings_alone_does_not_start_a_watcher(self):
        app = type("FakeApp", (), {})()
        app.v_phone_notify = MagicMock()
        app.v_phone_topic = MagicMock()
        self.addCleanup(SharedState.set_phone_topic, "")
        self.addCleanup(DebugLog._secrets.discard, TOPIC)
        with patch.object(QueueWatch, "Watcher") as watcher_class:
            mainGUI.App._load_phone_notify(app, {"phone_notify": True, "phone_notify_topic": TOPIC})
        watcher_class.assert_not_called()
        self.assertFalse(hasattr(app, "_queue_watcher"))

    def test_the_app_syncs_after_loading_the_settings(self):
        import inspect
        src = inspect.getsource(mainGUI.App.__init__)
        self.assertLess(src.index("self._load_saved_settings()"), src.index("self._sync_queue_watcher()"))

    def test_the_checkbox_syncs(self):
        import inspect
        self.assertIn("self._sync_queue_watcher()", inspect.getsource(mainGUI.App._on_phone_notify_changed))

    def test_closing_stops_the_watcher_before_destroying(self):
        order = []
        app = MagicMock()
        app._queue_watcher = MagicMock()
        app._queue_watcher.stop = lambda *a, **k: order.append("watcher")
        app._save_settings_now = lambda: None
        app._stop = lambda: order.append("stop")
        app.destroy = lambda: order.append("destroy")
        mainGUI.App._on_close(app)
        self.assertLess(order.index("watcher"), order.index("destroy"))
        self.assertIsNone(app._queue_watcher)

    def test_closing_without_a_watcher_is_fine(self):
        app = MagicMock()
        app._queue_watcher = None
        app._save_settings_now = lambda: None
        app.destroy = MagicMock()
        mainGUI.App._on_close(app)
        app.destroy.assert_called_once()
        bare = MagicMock(spec=["_save_settings_now", "_stop", "destroy", "_unhook_chase_keys", "_unhook_move_keys",
                               "_unhook_suicide_cancel_key", "_unhook_stop_start_keys", "_show_own_windows_again"])
        mainGUI.App._on_close(bare)         # 属性そのものが無くても落ちない


if __name__ == "__main__":
    unittest.main()
