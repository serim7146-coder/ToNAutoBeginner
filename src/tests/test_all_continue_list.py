"""FE（依頼者 2026-10-15）: 全続行のラウンドでも続行リストを見て、続行になったら普通の続行ラウンドとすべて同じに
（アナウンス・他窓フリーズ・音量「続行」・録画・窓位置リセット）。続行にならなければ今どおり全続行。
本物の音・録画・窓・入力は使わない"""
from tests.support import *  # noqa: F401,F403
import Recorder

KEY = "Classic/クラシック"
LISTED, UNLISTED = 99, 12


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)
        SharedState.set_list_source("host")               # 続行リストは渡したもの（test_round_decisions と同じ）
        self.addCleanup(SharedState.set_list_source, None)
        p = patch.object(ConnectDB, "register_round")
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, keep_on=None, round_type="Classic"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", continue_rounds={round_type})
        logs = []
        monitor = LogMonitor.LogMonitor(cfg, keep_on if keep_on is not None else {KEY: {LISTED}},
                                        logs.append, window_idx=1)
        monitor.logs = logs
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor._running = True
        monitor.st.continue_hook = monitor._on_continue_freeze     # start() で付くもの（窓位置リセットの覚え）
        return monitor

    def _killers(self, monitor, ids, revealed=False):
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(PlaySound, "play_sound") as play, \
             patch.object(Recorder, "on_continue_start") as record, \
             patch.object(monitor._action, "remember_continue_window") as remember, \
             patch.object(WindowOperator, "focus_window", return_value=True):
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=revealed)
            started = [c.kwargs["target"].__name__ if hasattr(c.kwargs["target"], "__name__") else ""
                       for c in thread.call_args_list if "target" in c.kwargs]
            if "_delayed_decision" in started:
                # 置き換えの合図を待つ（Variant）。合図が来ないまま待ち明けたものとして進める（test_round_decisions と同じ）
                thread.reset_mock()
                monitor._delayed_decision(monitor.st.round_type, 0.0, monitor.st.round_seq)
                started += [c.kwargs["target"].__name__ for c in thread.call_args_list if "target" in c.kwargs]
        return started, play, record, remember


class TestAListedTerror(_Case):
    def test_everything_a_continue_round_does(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_SEER):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype)
            started, play, record, remember = self._killers(monitor, [LISTED])
            st = monitor.st
            self.assertTrue(st.is_continue_round, itype)
            self.assertTrue(st.continue_freeze_held, f"{itype}: 他窓フリーズ")
            play.assert_any_call("continue.mp3", kind="continue")
            record.assert_called_once_with(1)
            remember.assert_called_once()           # 続行の終わりの窓位置リセットのために覚える
            self.assertEqual(WindowVolume.category_of(st), WindowVolume.CONTINUE, "音量「続行」")
            self.assertNotIn("do_skip", started)
            self.assertTrue(any("【全続行 → 続行リストで続行】" in m for m in monitor.logs), monitor.logs)


class TestAnUnlistedTerror(_Case):
    def test_it_stays_all_continue(self):
        monitor = self._monitor()
        started, play, record, remember = self._killers(monitor, [UNLISTED])
        st = monitor.st
        self.assertFalse(st.is_continue_round)
        self.assertFalse(st.continue_freeze_held)
        self.assertTrue(st.all_continue_round)
        play.assert_not_called()
        record.assert_not_called()
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.ALL_CONTINUE)
        self.assertNotIn("do_skip", started, "自爆しない")
        self.assertIn("[窓1] ラウンド指定で続行: Classic", monitor.logs)

    def test_a_skip_setting_does_not_win(self):
        monitor = self._monitor()
        monitor.cfg.skip_rounds = {"Classic"}
        started, *_ = self._killers(monitor, [UNLISTED])
        self.assertNotIn("do_skip", started, "全続行は自爆するラウンドの指定より前")


class TestKnownLater(_Case):
    def test_quiet_then_listed_switches_to_continue(self):
        monitor = self._monitor(round_type="Fog")
        monitor.cfg.continue_rounds = {"Fog"}
        monitor = self._monitor(round_type="Fog", keep_on={"Fog/霧": {LISTED}})
        self._killers(monitor, [UNLISTED])
        self.assertFalse(monitor.st.is_continue_round)
        _s, play, record, _r = self._killers(monitor, [LISTED], revealed=True)
        self.assertTrue(monitor.st.is_continue_round, "公開されて続行に切り替わる")
        self.assertTrue(monitor.st.continue_freeze_held)
        play.assert_any_call("continue.mp3", kind="continue")

    def test_listed_then_unlisted_goes_back_like_any_continue(self):
        """判断がやり直されて（置き換えなどでテラーが変わって）続行でなくなった → 今の続行の決まりどおり外す"""
        monitor = self._monitor(round_type="Fog", keep_on={"Fog/霧": {LISTED}})
        self._killers(monitor, [LISTED])
        self.assertTrue(monitor.st.continue_freeze_held)
        monitor.st.terror_ids = [UNLISTED]
        with patch.object(LogMonitor.threading, "Thread") as thread, patch.object(PlaySound, "play_sound"):
            monitor._decide("Fog")
            started = [c.kwargs["target"].__name__ for c in thread.call_args_list if "target" in c.kwargs]
        self.assertFalse(monitor.st.is_continue_round)
        self.assertFalse(monitor.st.continue_freeze_held, "続行から外れたら他窓フリーズも外す（今の決まり）")
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(monitor.st.all_continue_round, "全続行に戻る")
        self.assertNotIn("do_skip", started)


class TestOtherKindsOfContinue(_Case):
    def test_dtm_waldo_follows_the_list(self):
        monitor = self._monitor(keep_on={})
        monitor.cfg.cancel_afk = True
        _s, play, _rec, _rem = self._killers(monitor, [config.DTM_ID] if hasattr(config, "DTM_ID")
                                             else [LogMonitor.DTM_TERROR_ID])
        self.assertTrue(monitor.st.is_continue_round)
        self.assertTrue(monitor.st.open_special_continue, "DTM/Waldo の続行（音量は今どおりその他）")

    def test_only_private_like_windows_have_all_continue(self):
        """全続行は private と Seer's Apostle（メンバーのみ）だけ。芋・public は設定が残っていても全続行ではない
        （グループ判定の「【全続行 → 続行リストで続行】」の言い方にも使う）"""
        for itype, expected in ((config.INSTANCE_PRIVATE, True), (config.INSTANCE_SEER, True),
                                (config.INSTANCE_HOSHIIMO, False), (config.INSTANCE_PUBLIC, False)):
            self.assertEqual(self._monitor(itype)._is_all_continue_round(), expected, itype)

    def test_a_public_window_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_PUBLIC)
        _s, play, _rec, _rem = self._killers(monitor, [LISTED])
        self.assertFalse(monitor.st.is_continue_round, "全続行は private だけ（public は操作しない）")
        play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
