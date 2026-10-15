"""FH（依頼者 2026-10-15「死亡か生存で音量が変化する → RoundOver で変化するように」）: 続行・全続行・突入フリーズの
窓の音量の分類は、そのラウンドの RoundOver まで保つ。フリーズの解除・続行の終わりのタイミングは変えない。
本物の音・窓は使わない"""
from tests.support import *  # noqa: F401,F403

cat = WindowVolume.category_of


class _Case(unittest.TestCase):
    def setUp(self):
        for reset in (SharedState.continue_round_reset, SharedState.round_freeze_reset):
            reset()
            self.addCleanup(reset)
        self.monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x10), {}, lambda m: None, window_idx=1)
        self.monitor._running = True
        self.st = self.monitor.st
        self.st.instance_type = config.INSTANCE_PRIVATE
        self.st.in_round = True
        self.st.round_seq = 5
        self.st.round_type = "Classic"

    def _line(self, line):
        with patch.object(LogMonitor.threading, "Thread"), patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"), patch.object(self.monitor, "_start_daemon"):
            self.monitor._process(line)

    def _start_continue(self):
        self.st.is_continue_round = True
        SharedState.continue_round_start(self.st)
        self.assertEqual(cat(self.st), WindowVolume.CONTINUE)


import Recorder  # noqa: E402


class TestContinue(_Case):
    def test_dying_keeps_the_continue_volume_until_round_over(self):
        self._start_continue()
        self.st.died_this_round = True
        with patch.object(LogMonitor.time, "sleep"):
            self.monitor._release_continue_freeze_after_delay(5)
        self.assertFalse(self.st.continue_freeze_held, "フリーズは今どおり猶予の後に解ける")
        self.assertFalse(self.st.is_continue_round)
        self.assertEqual(cat(self.st), WindowVolume.CONTINUE, "音量は RoundOver まで「続行」")
        self._line("RoundOver")
        self.assertEqual(cat(self.st), WindowVolume.OTHER, "RoundOver で戻る")

    def test_living_goes_back_at_round_over(self):
        self._start_continue()
        self._line("RoundOver")
        self.assertTrue(self.st.is_continue_round, "続行の終わりは今どおり（猶予の後）")
        self.assertEqual(cat(self.st), WindowVolume.OTHER, "音量は RoundOver で戻る（猶予を待たない）")

    def test_a_new_decision_drops_it_at_once(self):
        self._start_continue()
        self.st.all_continue_round = True
        self.monitor._clear_stale_continue_round()
        self.assertEqual(cat(self.st), WindowVolume.ALL_CONTINUE, "続行でなくなったらその場で（全続行へ）")
        self.st.all_continue_round = False
        self.st.volume_hold = None
        self._start_continue()
        self.monitor._clear_stale_continue_round()
        self.assertEqual(cat(self.st), WindowVolume.OTHER)

    def test_going_up_is_at_once(self):
        self.st.all_continue_round = True
        self.assertEqual(cat(self.st), WindowVolume.ALL_CONTINUE)
        self._start_continue()                      # 全続行 → 続行（FE）
        self.assertEqual(cat(self.st), WindowVolume.CONTINUE)
        with patch.object(LogMonitor.time, "sleep"):
            self.monitor._release_continue_freeze_after_delay(5)       # 死んで続行が外れる
        self.assertEqual(cat(self.st), WindowVolume.CONTINUE, "上がった後の「続行」を保つ（全続行へ下げない）")

    def test_the_next_round_uses_the_round_kinds_again(self):
        """RoundOver の後は続行・全続行・突入フリーズを見ないが、次のラウンド開始でまた見る"""
        self._line("RoundOver")
        self.assertTrue(self.st.after_round_over)
        self._line("This round is taking place at Facility (12) and the round type is Classic")
        SharedState.round_freeze_start(self.st)
        self.assertEqual(cat(self.st), WindowVolume.ALL_CONTINUE)


class TestAllContinueAndEntryFreeze(_Case):
    def test_all_continue_goes_back_at_round_over(self):
        self.st.all_continue_round = True
        self.assertEqual(cat(self.st), WindowVolume.ALL_CONTINUE)
        self._line("RoundOver")
        self.assertFalse(self.st.all_continue_round, "Verified Round End まで待たない")
        self.assertEqual(cat(self.st), WindowVolume.OTHER)

    def test_the_entry_freeze_keeps_all_continue_until_round_over(self):
        SharedState.round_freeze_start(self.st)
        self.assertEqual(cat(self.st), WindowVolume.ALL_CONTINUE)
        with patch.object(LogMonitor.time, "sleep"):
            self.monitor._release_round_freeze_after_delay(5)
        self.assertFalse(self.st.round_freeze_held, "フリーズは今どおり解ける")
        self.assertEqual(cat(self.st), WindowVolume.ALL_CONTINUE, "音量は RoundOver まで")
        self._line("RoundOver")
        self.assertEqual(cat(self.st), WindowVolume.OTHER)

    def test_a_lived_entry_freeze_also_goes_back_at_round_over(self):
        SharedState.round_freeze_start(self.st)
        self._line("RoundOver")
        self.assertTrue(self.st.round_freeze_held, "フリーズはラウンド開始まで（今どおり）")
        self.assertEqual(cat(self.st), WindowVolume.OTHER, "音量は RoundOver で戻る")


class TestOthers(_Case):
    def test_a_missed_round_over_is_dropped_at_the_next_round_start(self):
        self._start_continue()
        self.st.is_continue_round = False            # 猶予の後に外れた
        self.assertEqual(cat(self.st), WindowVolume.CONTINUE)
        self._line("This round is taking place at Facility (12) and the round type is Classic")
        self.assertEqual(self.st.round_seq, 6)
        self.assertEqual(cat(self.st), WindowVolume.OTHER, "RoundOver を取りこぼしても次のラウンドで外れる")
        self.assertFalse(self.st.after_round_over, "ラウンド開始で RoundOver の後ではなくなる")

    def test_freeze_windows_and_run_are_as_before(self):
        self.st.equip_freeze_held = True
        self._line("RoundOver")
        self.assertEqual(cat(self.st), WindowVolume.FREEZE, "装備待ちは今どおり（フリーズの解除で）")
        self.st.equip_freeze_held = False
        st = WindowState(round_type="Run", in_round=True, round_freeze_held=True)
        self.assertEqual(cat(st), WindowVolume.OTHER, "Run は今どおりその他")
        self.assertIsNone(st.volume_hold)


if __name__ == "__main__":
    unittest.main()
