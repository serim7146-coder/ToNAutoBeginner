"""FY・FZ: 「アイテム取得→Begin」モードでは、ロストした回はツールは何もしない（移動も連打も Begin も。利用者がする）。本物の入力・OSC・音は使わない"""
from tests.support import *  # noqa: F401,F403
from datetime import datetime
import tests.test_equip_freeze_timing as _timing


class _Case(_timing._Case):
    def _manual_executor(self):
        """アイテム取得→Begin モードの窓。ロストして装備待ち（waiting_for_equip）"""
        ex, st = self._executor(fetch=False)
        SharedState.set_item_begin_mode(True)
        st.waiting_for_equip = True
        st.item_id = 0
        ex._start_use_spam = MagicMock(return_value=None)
        ex._confirm_begin = MagicMock()
        ex._press_begin = MagicMock(return_value=True)
        return ex, st

    @staticmethod
    def _equip(st, at_call=1):
        """sleep が at_call 回目に呼ばれたとき装備した（st.item_id が立つ）ことにする"""
        calls = []

        def sleep(_sec):
            calls.append(1)
            if len(calls) >= at_call:
                st.item_id = 29
        return sleep


class TestHandleItemLostInTheItemBeginMode(_Case):
    def test_after_equipping_it_returns_manual_with_the_one_line(self):
        ex, st = self._manual_executor()
        with patch.object(ActionExecutor.time, "sleep", side_effect=self._equip(st)):
            self.assertEqual(ex._handle_item_lost(), "manual")
        self.assertIn("⚠ アイテムロスト → 全窓フリーズ（装備+Beginをしてください）", self.logs)
        self.assertFalse(any("装備確認" in m for m in self.logs), "装備の確認はログに出さない（1 行で足りる）")
        self.assertFalse(any("Beginへ向かいます" in m for m in self.logs), self.logs)
        self.assertTrue(st.equip_freeze_held, "装備待ちフリーズは Begin の受理まで解かない（ここでは解かない）")

    def test_a_round_start_before_equipping_returns_false(self):
        ex, st = self._manual_executor()

        def start_round(_sec):
            st.in_round = True                       # 誰かが Begin を押した
        with patch.object(ActionExecutor.time, "sleep", side_effect=start_round):
            self.assertIs(ex._handle_item_lost(), False)
        self.assertFalse(any("装備確認" in m for m in self.logs), self.logs)

    def test_stop_while_waiting_returns_false_and_ends_the_freeze(self):
        ex, st = self._manual_executor()
        with patch.object(ex, "_is_running", return_value=False), \
             patch.object(ActionExecutor.SharedState, "equip_freeze_end") as end:
            self.assertIs(ex._handle_item_lost(), False)
        end.assert_called_once_with(st)

    def test_the_auto_fetch_mode_still_returns_true(self):
        ex, st = self._manual_executor()
        SharedState.set_item_begin_mode(False)
        self.assertIs(ex._handle_item_lost(), True)
        self.assertIn("⚠ アイテムロスト → Beginへ向かいます（フリーズは Begin が通ってから）", self.logs)

    def test_no_loss_returns_true(self):
        ex, st = self._manual_executor()
        st.waiting_for_equip = False
        self.assertIs(ex._handle_item_lost(), True)


class TestDoAfterRoundInTheItemBeginMode(_Case):
    def _spy(self, ex):
        """ツールが何かを押す・動かすメソッドを全部見張る（FZ: ロストした回はどれも呼ばない）"""
        names = ("_begin_move", "_take_pos_ref", "_press_begin", "_confirm_begin", "_retry_after_giving_up",
                 "_handle_item_lost", "_start_use_spam")
        spies = {n: MagicMock(return_value=None) for n in names}
        spies["_press_begin"].return_value = True
        for n, m in spies.items():
            setattr(ex, n, m)
        return spies

    def test_a_lost_round_does_nothing_but_the_one_line(self):
        """FZ: 移動も、Run の位置の撮影も、連打も、Begin も、あきらめのやり直しも、BEGIN_WAIT_SEC の待ちもしない"""
        ex, st = self._manual_executor()
        spies = self._spy(ex)
        ex._pos_ref_plan = MagicMock(return_value=True)
        with patch.object(config, "BEGIN_WAIT_SEC", 9),              patch.object(ActionExecutor.time, "sleep") as sleep:
            ex.do_after_round()
        for name, spy in spies.items():
            spy.assert_not_called()
        sleep.assert_not_called()
        self.assertNotEqual(getattr(ex, "_begin_gave_up_round", None), st.round_seq)
        self.assertEqual(self.logs, ["⚠ アイテムロスト → 全窓フリーズ（装備+Beginをしてください）"])
        self.assertTrue(st.waiting_for_equip, "装備待ちは解かない（解除は装備＋利用者の Begin の受理かラウンド開始）")

    def test_it_waits_for_the_run_respawn_first_and_says_nothing_when_stopped(self):
        ex, st = self._manual_executor()
        spies = self._spy(ex)
        ex._wait_run_respawn = MagicMock(return_value=False)
        ex.do_after_round()
        self.assertEqual(self.logs, [])
        for spy in spies.values():
            spy.assert_not_called()

    def test_a_loss_found_only_at_verified_round_end_keeps_the_fy_path(self):
        """ロストが Verified Round End で初めて分かる回（RoundOver では立たない）: 移動はする・連打は止める・Begin は押さない"""
        ex, st = self._manual_executor()
        st.waiting_for_equip = False                 # 連打を始める時点ではまだ分からない
        stop = threading.Event()
        ex._start_use_spam = MagicMock(return_value=stop)
        ex._begin_move = MagicMock()
        ex._retry_after_giving_up = MagicMock()

        def found_at_round_end():
            st.waiting_for_equip = True
            return True
        ex._wait_round_end = found_at_round_end
        with patch.object(ActionExecutor.time, "sleep", side_effect=self._equip(st)):
            ex.do_after_round()
        ex._begin_move.assert_called_once()
        ex._start_use_spam.assert_called_once()
        self.assertTrue(stop.is_set(), "連打が残らない")
        ex._press_begin.assert_not_called()
        ex._confirm_begin.assert_not_called()
        ex._retry_after_giving_up.assert_not_called()
        self.assertEqual(self.logs.count("⚠ アイテムロスト → 全窓フリーズ（装備+Beginをしてください）"), 1, self.logs)

    def test_a_round_without_loss_is_as_before(self):
        ex, st = self._manual_executor()
        st.waiting_for_equip = False
        st.item_id = 29
        ex._begin_move = MagicMock()
        ex.do_after_round()
        ex._begin_move.assert_called_once()
        ex._start_use_spam.assert_called_once()
        ex._press_begin.assert_called_once()

    def test_the_auto_fetch_mode_is_as_before(self):
        ex, st = self._manual_executor()
        SharedState.set_item_begin_mode(False)

        def press(*_a, **_k):
            st.begin_done = True
            return True
        ex._press_begin = MagicMock(side_effect=press)
        ex._begin_move = MagicMock()
        with patch.object(ex, "_is_running", side_effect=[True] * 60 + [False] * 20):
            ex.do_after_round()
        ex._begin_move.assert_called_once()
        ex._start_use_spam.assert_called_once()
        ex._press_begin.assert_called_once()


class TestTheUsersBeginReleasesTheFreeze(unittest.TestCase):
    """ツールが押していない Begin も、位相が分かっていれば「受理（予定の外）」。装備済みなら遅れてフリーズ解除"""

    def test_an_accepted_begin_after_equipping_schedules_the_release(self):
        phase = datetime(2026, 10, 13, 9, 33, 17).timestamp()
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=True, hwnd=0x100, osc_port=9000), {},
                                        lambda _m: None, window_idx=1)
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        monitor._auto_begin_active = lambda: True
        monitor._verified.last_periodic = phase
        monitor._verified.on_round_end_verified(phase + 263)
        st.round_end_seen = True
        st.waiting_for_equip = True
        st.item_id = 29                                      # 装備済み
        st.last_begin_press_at = 0.0                         # ツールは押していない
        st.log_now = phase + 270
        started = []
        monitor._start_daemon = lambda target, *a: started.append(target.__func__.__name__)
        with patch.object(DebugLog, "write"), patch.object(SharedState, "get_speed_detect", return_value=False):
            monitor._on_verified(LogParser.LogEvent(LogParser.EVENT_VERIFIED))
        self.assertTrue(st.begin_done)
        self.assertFalse(st.waiting_for_equip)
        self.assertEqual(started, ["_release_equip_wait_after_delay"])


if __name__ == "__main__":
    unittest.main()
