"""FG（依頼者 2026-10-15）: グループの全続行も音量「全続行」・AFK を鳴らすときの速さの記録・声ごとのオン・オフ・
アナウンス欄の並べ方。本物の音・入力・窓は使わない（App は隠したまま）"""
from tests.support import *  # noqa: F401,F403
import tests.support as support
import inspect


def _clock():
    now = [1000.0]
    fake = type(sys)("time_for_test")
    fake.time = fake.monotonic = lambda: now[0]
    fake.sleep = lambda sec: now.__setitem__(0, now[0] + sec)
    return fake, now


class TestGroupAllContinue(unittest.TestCase):
    def test_the_group_continue_is_all_continue(self):
        """15:17 窓1（干し芋）: 「グループ判定: Mystic Moon 【全続行】」なのに音量が「全続行」にならなかった"""
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10), {}, lambda m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        monitor.st.round_type = "Mystic Moon"
        with patch.object(monitor, "_group_decision", return_value=GroupRound.CONTINUE):
            self.assertTrue(monitor._apply_group_decision("Mystic Moon"))
        self.assertTrue(monitor.st.all_continue_round)
        self.assertEqual(WindowVolume.category_of(monitor.st), WindowVolume.ALL_CONTINUE)

    def test_it_is_dropped_as_before(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10), {}, lambda m: None, window_idx=1)
        monitor.st.all_continue_round = True
        with patch.object(PlaySound, "play_sound"), patch.object(monitor, "_start_daemon"):
            monitor._process("This round is taking place at Facility (12) and the round type is Classic")
        self.assertFalse(monitor.st.all_continue_round, "ラウンド開始で落とす")


class TestTheAfkNote(unittest.TestCase):
    def _watch(self, receiver, speeds, moved=None, end=71):
        st = WindowState(instance_type=config.INSTANCE_HOSHIIMO, window_idx=1, round_seq=3, in_round=True)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x10, voice_afk="afk.mp3"), st, lambda: True,
                                           lambda m: None)
        ex._receiver = receiver
        clock, now = _clock()
        start = now[0]
        seq = list(speeds)
        if receiver is not None:
            type(receiver).speed = property(lambda _s: seq[min(int(now[0] - start), len(seq) - 1)])
            # 動いたと見た時刻: moved（開始からの秒の一覧）のうち、今までのいちばん新しいもの（x.5 秒にして、
            # 確認と確認の間に起きたことにする）
            marks = [start + t for t in (moved or ())]
            type(receiver).last_moved_at = property(lambda _s: max([m for m in marks if m <= now[0]], default=0.0))
        self.sounds = []
        real_sleep = clock.sleep

        def sleep(sec):
            real_sleep(sec)
            if now[0] - start >= end:
                st.in_round = False
        clock.sleep = sleep
        written = []
        with patch.object(ActionExecutor, "time", clock), \
             patch.object(PlaySound, "play_sound", side_effect=lambda *a, **k: self.sounds.append(now[0] - start)), \
             patch.object(DebugLog, "write", side_effect=written.append):
            support.REAL_AFK_WATCH(ex, 3)
        return [m for m in written if "AFK 回避:" in m], start

    def test_the_line_has_the_last_speed_the_age_and_the_peak(self):
        receiver = type("R", (), {})()
        receiver.ever_received = True
        receiver.last_received_at = 1000.0 - 12.5
        lines, _ = self._watch(receiver, [0.05, 0.0, 0.08, 0.0])
        self.assertEqual(lines, ["[操作] [窓1] AFK 回避: 最後に受けた速さ 0.00・最後に受けてから 82.5 秒・"
                                 "受けたことがある・この 70 秒の最大 0.08・最後に動いたと見てから なし"])

    def test_never_received_and_no_receiver(self):
        receiver = type("R", (), {})()
        receiver.ever_received = False
        receiver.last_received_at = 0.0
        lines, _ = self._watch(receiver, [None])
        self.assertEqual(lines, ["[操作] [窓1] AFK 回避: 最後に受けた速さ None・最後に受けてから なし・"
                                 "受けたことがない・この 70 秒の最大 なし・最後に動いたと見てから なし",
                                 "[操作] [窓1] AFK 回避: 速さを受けていない → 鳴らさない・動かさない"])
        lines, _ = self._watch(None, [None])
        self.assertEqual(lines, ["[操作] [窓1] AFK 回避: 速さの受信器なし",
                                 "[操作] [窓1] AFK 回避: 速さを受けていない → 鳴らさない・動かさない"])

    def test_settings_load_the_voice_switches(self):
        self.assertIn("App._load_voice_enabled(self, data)", inspect.getsource(mainGUI.App._load_saved_settings))

    def _still_receiver(self):
        receiver = type("R", (), {})()
        receiver.ever_received = True
        receiver.last_received_at = 1.0
        return receiver

    def test_a_jump_between_checks_counts_as_moving(self):
        """FG（依頼者「ジャンプで動いた判定にできていない可能性」）: 確認の間に 0.5 が届いてすぐ 0 になっても数え直す。
        ここでは 30.5 秒に跳んだ（確認の瞬間の値はずっと 0）→ 31 秒の確認から数え直して 101 秒まで鳴らない"""
        self._watch(self._still_receiver(), [0.0], moved=[30.5], end=95)
        self.assertEqual(self.sounds, [], "跳んだので数え直した")
        self._watch(self._still_receiver(), [0.0], moved=[30.5], end=110)
        self.assertEqual([round(t) for t in self.sounds], [101])

    def test_nothing_comes_and_it_warns_at_70(self):
        lines, _ = self._watch(self._still_receiver(), [0.0], end=71)
        self.assertEqual([round(t) for t in self.sounds], [70])
        self.assertTrue(lines[0].endswith("最後に動いたと見てから なし"), lines)

    def test_the_line_says_how_long_since_it_last_moved(self):
        lines, _ = self._watch(self._still_receiver(), [0.0], moved=[0.5], end=72)
        self.assertTrue(lines[0].endswith("最後に動いたと見てから 70.5 秒"), lines)

    def test_the_receiver_marks_speed_and_grounded_changes(self):
        """速さが AFK_MOVING_SPEED 以上で届いた・Grounded が変わった（跳んだ・着地した）時刻を覚える"""
        import OSCReceiver
        receiver = OSCReceiver.VelocityReceiver(0x10, 9001)
        self.assertEqual(receiver.last_moved_at, 0.0)
        receiver._handle(OSCReceiver.VELOCITY_MAGNITUDE, 0.05)
        self.assertEqual(receiver.last_moved_at, 0.0, "0.1 未満は動いたに入れない")
        receiver._handle(OSCReceiver.GROUNDED, True)
        self.assertEqual(receiver.last_moved_at, 0.0, "最初の Grounded は変化ではない")
        receiver._handle(OSCReceiver.GROUNDED, False)
        jumped = receiver.last_moved_at
        self.assertGreater(jumped, 0.0, "Grounded が変わっただけでも動いた")
        receiver._handle(OSCReceiver.GROUNDED, False)
        self.assertEqual(receiver.last_moved_at, jumped, "同じ値なら変わっていない")
        with patch.object(OSCReceiver.time, "time", return_value=jumped + 5):
            receiver._handle(OSCReceiver.VELOCITY_MAGNITUDE, 0.5)
        self.assertEqual(receiver.last_moved_at, jumped + 5)

    def test_the_receiver_knows_when_it_last_got_a_speed(self):
        import OSCReceiver
        receiver = OSCReceiver.VelocityReceiver(0x10, 9001)
        self.assertEqual(receiver.last_received_at, 0.0)
        receiver._handle(OSCReceiver.VELOCITY_MAGNITUDE, 4.0)
        self.assertGreater(receiver.last_received_at, 0.0)


class TestVoiceOnOff(unittest.TestCase):
    def setUp(self):
        for kind in PlaySound.VOICE_KINDS:
            self.addCleanup(PlaySound.set_voice_enabled, kind, True)
        p = patch("pathlib.Path.exists", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def _played(self, **kw):
        with patch.object(PlaySound.threading, "Thread") as thread, \
             patch.object(DebugLog, "write") as write:
            PlaySound.play_sound("voice/AFK.mp3", **kw)
        return thread.called, [c.args[0] for c in write.call_args_list]

    def test_off_does_not_play_but_try_does(self):
        PlaySound.set_voice_enabled("afk", False)
        played, written = self._played(kind="afk")
        self.assertFalse(played)
        self.assertIn("[音声] afk はオフ → 鳴らさない", written)
        self.assertTrue(self._played(kind="afk", force=True)[0], "▶ はオフでも鳴る")
        self.assertTrue(self._played(kind="fog")[0], "ほかの声は鳴る")
        self.assertTrue(self._played()[0], "種類なしは今どおり")

    def test_the_setting(self):
        got = PlaySound.load_voice_enabled({"afk": False, "fog": "no", "continue": None})
        self.assertFalse(got["afk"])
        self.assertTrue(got["fog"], "壊れた値はオン")
        self.assertTrue(got["continue"])
        self.assertEqual(PlaySound.voice_enabled_setting()["afk"], False)
        self.assertEqual(set(PlaySound.voice_enabled_setting()), set(PlaySound.VOICE_KINDS))
        self.assertTrue(all(PlaySound.load_voice_enabled(None).values()), "無ければ全部オン")


class TestTheAnnounceRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()
        cls.app._announce_frame._toggle()
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        for kind in PlaySound.VOICE_KINDS:
            self.addCleanup(PlaySound.set_voice_enabled, kind, True)

    def _rows(self):
        return [r.winfo_children() for r in self.app._announce_frame.content.pack_slaves()]

    def test_a_check_turns_the_voice_off_and_is_saved(self):
        kids = self._rows()[16]                                 # AFK
        self.assertEqual(kids[1].cget("text"), "AFK")
        with patch.object(self.app, "_schedule_settings_save") as save:
            kids[0].invoke()
        self.assertFalse(PlaySound.get_voice_enabled("afk"))
        save.assert_called_once()
        self.app._load_voice_enabled({"voice_enabled": {"afk": True}})
        self.assertTrue(self.app.v_voice_enabled["afk"].get())
        self.assertTrue(PlaySound.get_voice_enabled("afk"))

    def test_the_file_name_only_and_the_full_path_in_the_tooltip(self):
        kids = self._rows()[0]
        entry = kids[2]
        self.assertEqual(entry.get(), "Continue.mp3")
        self.assertEqual(entry.tooltip.text(), self.app.v_voice_continue.get())
        old = self.app.v_voice_continue.get()
        self.addCleanup(self.app.v_voice_continue.set, old)
        self.app.v_voice_continue.set(r"C:\somewhere\else\Other.wav")
        self.assertEqual(entry.get(), "Other.wav", "選び直したら名前も変わる")
        self.assertEqual(self.app.v_voice_continue.get(), r"C:\somewhere\else\Other.wav", "中はフルパス")

    def test_the_title_width_fits_the_longest(self):
        widths = {str(k[1].cget("width")) for k in self._rows()}
        self.assertEqual(widths, {str(mainGUI.display_width("8 Pages(速度検知)"))})
        self.assertEqual(mainGUI.display_width("主催リスト喪失"), 14)
        self.assertEqual(mainGUI.display_width("8 Pages(速度検知)"), 17)

    def test_the_rows_are_tight_and_fit_the_window(self):
        for kids in self._rows():
            heights = [k.winfo_reqheight() for k in kids if k.winfo_class() in ("TEntry", "TButton", "TCheckbutton")]
            self.assertLessEqual(max(heights) - min(heights), 2, "ボタンとファイル欄の高さをそろえる")
        self.assertLessEqual(self.app._announce_frame.content.winfo_reqwidth(), 880, "窓の最小幅に収まる")
        self.assertEqual({str(r.pack_info()["pady"]) for r in self.app._announce_frame.content.pack_slaves()}, {"0"})


if __name__ == "__main__":
    unittest.main()
