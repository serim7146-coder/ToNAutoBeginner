"""GS: 焼き芋の霧の自爆は `Killers have been revealed` の行で（依頼者 2026-10-25）。

焼き芋の Fog で判定が SKIP（クラシックの霧）のとき、テラーが Enrage・スタン・看破・DTM 推定などの前倒しで
分かっただけなら自爆しない。公開ログに 1 回だけ「霧: 公開の行で自爆します」と出し、公開の行で自爆する。
続行になる判定（オルタネイトで続行リストにある等）は、これまでどおり分かった時点で行う。
"""
from tests.support import *  # noqa: F401,F403


class TestYakiimoFogSkipWaitsForTheRevealedLine(unittest.TestCase):
    FOG_KEY = "Fog/霧"
    SNAIL = 101            # classic
    WITCH = 1033           # alternate（Walpurgisnacht。個体名 Unknown Witch）
    UNKNOWN = ("2026.09.21 17:31:15 Debug      -  Killers is unknown - ??? // "
               "Will be revealed after 50 seconds // Round type is Fog")
    STUN_SNAIL = "2026.09.21 17:31:40 Debug      -  Immortal Snail was stunned."
    STUN_WITCH = "2026.09.21 17:31:40 Debug      -  Unknown Witch was stunned."
    REVEAL_SNAIL = ("2026.09.21 17:32:05 Debug      -  Killers have been revealed - "
                    "101 0 0 // Round type is Fog")
    REVEAL_WITCH = ("2026.09.21 17:32:05 Debug      -  Killers have been revealed - "
                    "33 0 0 // Round type is Fog (Alternate)")
    SET_SNAIL = ("2026.09.21 17:31:15 Debug      -  Killers have been set - "
                 "101 0 0 // Round type is Fog")
    WAIT_LOG = "[窓1] 霧: 公開の行で自爆します"

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        FogEarlyRead.set_early_read_enabled(True)          # 霧看破のボタン
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        for p in (patch.object(config, "TERRORS", fog_early_read_terrors()),
                  patch.object(Recorder, "on_continue_start")):
            p.start()
            self.addCleanup(p.stop)
        self.send = self._start(patch.object(ConnectDB, "register_round"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, instance_type=config.INSTANCE_YAKIIMO, keep=None, unknown=True):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        if unknown:
            monitor._process(self.UNKNOWN)
        return monitor

    def _skipped(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs].count("do_skip")

    # ── 前倒しで分かっても、自爆は公開の行まで待つ ──────────
    def test_a_stun_does_not_skip_yet_and_says_so_once(self):
        monitor = self._monitor()

        monitor._process(self.STUN_SNAIL)

        self.assertEqual(self._skipped(), 0)
        self.assertEqual(monitor.logs.count(self.WAIT_LOG), 1, monitor.logs)
        self.assertEqual(monitor.st.enrage_identified, self.SNAIL)
        self.assertTrue(any("テラー判明(Stunned)" in m for m in monitor.logs), monitor.logs)
        self.assertFalse(monitor.st.is_continue_round)

    def test_the_revealed_line_then_skips_once(self):
        monitor = self._monitor()
        monitor._process(self.STUN_SNAIL)

        monitor._process(self.REVEAL_SNAIL)

        self.assertEqual(self._skipped(), 1)
        self.assertEqual(monitor.logs.count(self.WAIT_LOG), 1, "公開の行で言い直さない")
        self.assertIn("[窓1] グループ自動自爆: Fog", monitor.logs)

    def test_enrage_waits_too(self):
        monitor = self._monitor()

        monitor._on_enrage("Immortal Snail")
        self.assertEqual(self._skipped(), 0)
        self.assertEqual(monitor.logs.count(self.WAIT_LOG), 1, monitor.logs)

        monitor._process(self.REVEAL_SNAIL)
        self.assertEqual(self._skipped(), 1)

    def test_the_early_read_waits_too(self):
        monitor = self._monitor()

        monitor._identify_fog_terror(self.SNAIL, "看破", "Immortal Snail", early_read=True)
        self.assertEqual(self._skipped(), 0)
        self.assertEqual(monitor.logs.count(self.WAIT_LOG), 1, monitor.logs)

        monitor._process(self.REVEAL_SNAIL)
        self.assertEqual(self._skipped(), 1)

    def test_the_no_object_dtm_waits_too(self):
        monitor = self._monitor()

        monitor._identify_fog_terror(config.DTM_ID, "看破（オブジェクトなし）", "", early_read=True)
        self.assertEqual(self._skipped(), 0)
        self.assertEqual(monitor.logs.count(self.WAIT_LOG), 1, monitor.logs)

        monitor._process("2026.09.21 17:32:05 Debug      -  Killers have been revealed - "
                         "50 0 0 // Round type is Fog")
        self.assertEqual(self._skipped(), 1)

    def test_nothing_happens_if_the_revealed_line_never_comes(self):
        """公開の行が来ないままラウンドが終わったら何もしない（今と同じ）"""
        monitor = self._monitor()
        monitor._process(self.STUN_SNAIL)

        monitor._on_round_over(None)

        self.assertEqual(self._skipped(), 0)

    def test_a_mismatch_is_still_warned_and_skips_once(self):
        monitor = self._monitor()
        monitor._process(self.STUN_SNAIL)

        monitor._process("2026.09.21 17:32:05 Debug      -  Killers have been revealed - "
                         "12 0 0 // Round type is Fog")

        self.assertEqual(self._skipped(), 1)
        self.assertTrue(any("食い違いました" in m for m in monitor.logs), monitor.logs)

    def test_a_new_round_waits_again(self):
        """待つ印はラウンドごと（前のラウンドの印が残って、次の霧が即自爆にならない／待ちっぱなしにならない）"""
        monitor = self._monitor()
        monitor._process(self.STUN_SNAIL)
        monitor._process(self.REVEAL_SNAIL)
        self.assertEqual(self._skipped(), 1)
        monitor.st.round_seq += 1               # 次のラウンド
        monitor.st.enrage_identified = None
        monitor.st.terror_ids = []
        monitor.st.statistics_sent = False
        monitor._process(self.UNKNOWN)

        monitor._process(self.STUN_SNAIL)

        self.assertEqual(self._skipped(), 1, "2 回目の前倒しでは自爆しない")
        self.assertEqual(monitor.logs.count(self.WAIT_LOG), 2, monitor.logs)

        monitor._process(self.REVEAL_SNAIL)
        self.assertEqual(self._skipped(), 2)

    # ── 続行になる判定は今までどおり早い ─────────────────
    def test_an_alternate_in_the_list_continues_at_once(self):
        monitor = self._monitor(keep={self.FOG_KEY: {self.WITCH}})

        monitor._process(self.STUN_WITCH)

        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3", kind="continue")
        self.assertEqual(self._skipped(), 0)
        self.assertNotIn(self.WAIT_LOG, monitor.logs)

        monitor._process(self.REVEAL_WITCH)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(self._skipped(), 0)
        self.play.assert_called_once()          # 公開の行でやり直さない

    def test_a_wrong_classic_guess_then_an_alternate_in_the_list_continues_at_the_reveal(self):
        """前倒しはクラシックだったが、公開はオルタネイトでリストにある → 公開の行で続行（自爆しない）"""
        monitor = self._monitor(keep={self.FOG_KEY: {self.WITCH}})
        monitor._process(self.STUN_SNAIL)
        self.assertEqual(self._skipped(), 0)

        monitor._process(self.REVEAL_WITCH)

        self.assertEqual(self._skipped(), 0)
        self.assertTrue(monitor.st.is_continue_round)

    # ── 公開の行から直接分かったときは今までどおり ────────
    def test_a_direct_reveal_skips_at_once_without_the_note(self):
        monitor = self._monitor()

        monitor._process(self.REVEAL_SNAIL)

        self.assertEqual(self._skipped(), 1)
        self.assertNotIn(self.WAIT_LOG, monitor.logs)

    def test_a_killers_set_line_skips_at_once(self):
        monitor = self._monitor(unknown=False)
        monitor.st.round_type = "Fog"

        monitor._process(self.SET_SNAIL)

        self.assertEqual(self._skipped(), 1)
        self.assertNotIn(self.WAIT_LOG, monitor.logs)

    # ── ほかは変わらない ─────────────────────────
    def test_hoshiimo_fog_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_HOSHIIMO)

        monitor._process(self.STUN_SNAIL)
        monitor._process(self.REVEAL_SNAIL)

        self.assertEqual(self._skipped(), 0, "干し芋の Fog は全続行")
        self.assertNotIn(self.WAIT_LOG, monitor.logs)

    def test_private_fog_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE)

        monitor._process(self.STUN_SNAIL)

        self.assertEqual(self._skipped(), 1, "private はリストで判定して、その場で自爆")
        self.assertNotIn(self.WAIT_LOG, monitor.logs)

    def test_the_terror_is_shown_and_sent_early_as_before(self):
        monitor = self._monitor()

        monitor._process(self.STUN_SNAIL)

        self.assertTrue(any("Terror revealed:" in m for m in monitor.logs), monitor.logs)
        self.send.assert_called_once()
