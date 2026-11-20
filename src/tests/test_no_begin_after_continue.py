"""続行ラウンドの後は自動 Begin を押さない設定（依頼者 2026-11-18。既定 OFF）。
対象は普通の続行（続行フリーズを張った続行）と全続行。DTM/Waldo 続行・完全放置モードは対象外。
押さないラウンドのアイテムロストは「自動Begin OFF の窓」と同じ経路（RoundOver で前面化・音声・全窓フリーズ、
解除は装備だけ）で案内する。
本物の音・前面・窓・入力・OSC・DB は使わない（予約は積むだけで、Begin は押さない）"""
from tests.support import *  # noqa: F401,F403
import inspect
import Recorder

P = "2026.11.18 12:00:00 Debug      -  "
ROUND_OVER = P + "RoundOver"
VERIFIED_END = P + "Verified Round End"
EQUIP = P + "Equipping 29."
ROUND_START = P + "This round is taking place at Sewers (12) and the round type is Classic"
SKIP_LOG = "続行ラウンドの後 → Begin は押しません（設定「続行の後は自動Beginしない」）"
LOST_LOG = "RoundOver 【⚠ アイテムロスト → 全窓フリーズ（装備かラウンド開始で解除）】"
MODE_LOG = "RoundOver 【⚠ アイテムロスト → 全窓フリーズ開始】"
KEY = "Classic/クラシック"
LISTED, UNLISTED = 99, 12


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        for undo in (SharedState.equip_freeze_reset, SharedState.continue_round_reset):
            self.addCleanup(undo)
        SharedState.set_no_begin_after_continue(True)
        SharedState.set_hands_free(False)
        SharedState.set_item_begin_mode(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_no_begin_after_continue, False)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_item_begin_mode, False)
        self.addCleanup(SharedState.set_list_source, None)
        self.play = patch.object(PlaySound, "play_sound").start()
        self.front = patch.object(WindowOperator, "borrow_front", return_value=(True, None)).start()
        patch.object(ConnectDB, "register_round").start()
        patch.object(Recorder, "on_round_over").start()
        patch.object(DebugLog, "write").start()
        self.thread = patch.object(LogMonitor.threading, "Thread").start()      # 見張りのスレッドは起こさない
        self.addCleanup(patch.stopall)

    def _monitor(self, auto_begin=True, itype=config.INSTANCE_PRIVATE, lost=False, window_idx=1,
                 how="freeze", continue_rounds=(), released=False):
        """how: 続行の種類。freeze = 普通の続行（続行フリーズを張る）/ all = 全続行の印 /
        dtm = DTM/Waldo 続行（is_continue_round だけでフリーズは張らない）/ both = 普通の続行 ＋ 全続行の印 /
        none = 続行でない。
        released: 続行フリーズを外してから RoundOver を迎える（死亡から 5 秒後にほかの窓のフリーズが解けた後。
        前面化・音声はほかの窓のフリーズが解けてから出るので、案内を見るテストはこれを立てる）"""
        cfg = WindowConfig(hwnd=0x10, auto_begin=auto_begin, voice_item_lost="lost.mp3",
                           continue_rounds=set(continue_rounds))
        logs = []
        monitor = LogMonitor.LogMonitor(cfg, {KEY: {LISTED}}, logs.append, window_idx=window_idx)
        monitor.logs = logs
        st = monitor.st
        st.instance_type = itype
        st.in_round = True
        st.round_seq = 7
        st.round_type = "Classic"
        st.item_id = 7
        st.continue_hook = monitor._on_continue_freeze
        monitor._running = True
        monitor._action.remember_continue_window = lambda: None      # 窓の位置は見ない
        self.daemons = []
        monitor._start_daemon = lambda target, *a: self.daemons.append((target.__name__, a))
        if lost:
            monitor._mark_item_lost("リスポーン: アイテムロスト")
        if how in ("freeze", "both", "dtm"):
            st.is_continue_round = True
        if how in ("freeze", "both"):
            SharedState.continue_round_start(st)
            self.assertEqual(st.continue_freeze_round, st.round_seq, "前提: 続行フリーズを張った")
        if how in ("all", "both"):
            st.all_continue_round = True
        if released:
            st.is_continue_round = False
            SharedState.continue_round_end(st)
            self.assertFalse(st.continue_freeze_held)
        return monitor

    def _line(self, monitor, body):
        with patch.object(LogMonitor.time, "sleep") as sleep:
            monitor._process(body)
        return sleep

    def _names(self):
        return [name for name, _a in self.daemons]

    def _count(self, monitor, text):
        return sum(1 for m in monitor.logs if text in m)


class TestBeginOrNot(_Case):
    def test_a_normal_continue_round_does_not_begin(self):
        monitor = self._monitor(how="freeze")
        self._line(monitor, ROUND_OVER)
        self.assertNotIn("do_after_round", self._names())
        self.assertEqual(self._count(monitor, SKIP_LOG), 1, monitor.logs)
        self.assertEqual(monitor.st.begin_skipped_seq, 7)

    def test_all_continue_does_not_begin(self):
        """全続行の印（all_continue_round）が立っていれば押さない。印を立てる経路は 4 つ（ラウンド指定・
        グループ判定・Run 全続行・Killers の行）。ラウンド指定は実際の判定の流れで、残りは印を直接立てて見る"""
        for route in ("ラウンド指定（Killers の判定）", "グループ判定（印を直接）", "Run 全続行（印を直接）"):
            with self.subTest(route=route):
                SharedState.continue_round_reset()
                if route.startswith("ラウンド指定"):
                    monitor = self._monitor(how="none", continue_rounds={"Classic"})
                    with patch.object(monitor._action, "remember_continue_window"), \
                            patch.object(WindowOperator, "focus_window", return_value=True):
                        monitor._on_killers([UNLISTED], "Classic", revealed=False)
                    self.assertTrue(monitor.st.all_continue_round, "前提: 全続行になった")
                    self.assertFalse(monitor.st.is_continue_round)
                else:
                    monitor = self._monitor(how="all")
                self._line(monitor, ROUND_OVER)
                self.assertNotIn("do_after_round", self._names(), route)
                self.assertEqual(self._count(monitor, SKIP_LOG), 1, (route, monitor.logs))
                self.assertEqual(monitor.st.begin_skipped_seq, 7, route)

    def test_all_continue_and_the_list_both_continue(self):
        monitor = self._monitor(how="both")
        self._line(monitor, ROUND_OVER)
        self.assertNotIn("do_after_round", self._names())
        self.assertEqual(self._count(monitor, SKIP_LOG), 1, "二重に数えない")
        self.assertEqual(monitor.st.begin_skipped_seq, 7)

    def test_a_dtm_waldo_continue_still_begins(self):
        monitor = self._monitor(how="dtm")
        self.assertFalse(monitor.st.continue_freeze_held, "前提: ほかの窓を止めない続行")
        self._line(monitor, ROUND_OVER)
        self.assertIn("do_after_round", self._names())
        self.assertEqual(monitor.st.begin_skipped_seq, -1)
        self.assertEqual(self._count(monitor, SKIP_LOG), 0)

    def test_a_skipped_round_still_begins(self):
        """続行でないラウンド（自爆したラウンドなど）は押す"""
        monitor = self._monitor(how="none")
        self._line(monitor, ROUND_OVER)
        self.assertIn("do_after_round", self._names())
        self.assertEqual(monitor.st.begin_skipped_seq, -1)
        self.assertEqual(self._count(monitor, SKIP_LOG), 0)

    def test_hands_free_still_begins(self):
        SharedState.set_hands_free(True)
        for how in ("freeze", "all"):
            with self.subTest(how=how):
                SharedState.continue_round_reset()
                self.daemons = []
                monitor = self._monitor(how=how)
                self.assertTrue(monitor._hands_free(), "前提: 完全放置モードの窓")
                self._line(monitor, ROUND_OVER)
                self.assertIn("do_after_round", self._names())
                self.assertEqual(monitor.st.begin_skipped_seq, -1)
                self.assertEqual(self._count(monitor, SKIP_LOG), 0)

    def test_only_that_round(self):
        monitor = self._monitor(how="freeze")
        self._line(monitor, ROUND_OVER)
        self.assertNotIn("do_after_round", self._names())
        # 利用者が BEGIN を押して次のラウンドが始まる（続行ではない）
        self._line(monitor, ROUND_START)
        self.assertEqual(monitor.st.round_seq, 8)
        self.assertEqual(monitor.st.begin_skipped_seq, 7, "前のラウンドの印は残るが、今のラウンドのものではない")
        self.assertFalse(monitor._begin_skipped())
        self.daemons.clear()
        self._line(monitor, ROUND_OVER)
        self.assertIn("do_after_round", self._names(), "次のラウンドの後は押す")
        self.assertEqual(self._count(monitor, SKIP_LOG), 1, "ログは押さなかった 1 回だけ")

    def test_off_begins_after_a_continue_round(self):
        SharedState.set_no_begin_after_continue(False)
        for how in ("freeze", "all", "both"):
            with self.subTest(how=how):
                SharedState.continue_round_reset()
                self.daemons = []
                monitor = self._monitor(how=how, lost=True)
                self._line(monitor, ROUND_OVER)
                self.assertIn("do_after_round", self._names())
                self.assertEqual(monitor.st.begin_skipped_seq, -1)
                self.assertEqual(self._count(monitor, SKIP_LOG), 0)
                self.assertFalse(monitor.st.equip_freeze_held, "アイテムロストの案内は今まで通り Begin の後")
                self.assertEqual(self._count(monitor, LOST_LOG), 0)
        self.play.assert_not_called()

    def test_auto_begin_off_is_unchanged(self):
        """「自動Begin」OFF の窓は、設定が ON でも今まで通り（印を立てず、ログも増やさない）"""
        for how in ("freeze", "all"):
            with self.subTest(how=how):
                SharedState.continue_round_reset()
                self.daemons = []
                monitor = self._monitor(auto_begin=False, how=how)
                self._line(monitor, ROUND_OVER)
                self.assertNotIn("do_after_round", self._names())
                self.assertEqual(monitor.st.begin_skipped_seq, -1)
                self.assertEqual(self._count(monitor, SKIP_LOG), 0)

    def test_windows_where_the_tool_does_not_begin_are_unchanged(self):
        """ツールが Begin を押さない窓（焼き芋・干し芋・public）は、設定が ON でも印を立てず、
        ログも出さない（依頼者 2026-11-19）。動きは設定 OFF のときと同じ（do_after_round を予約する）"""
        for itype in (config.INSTANCE_YAKIIMO, config.INSTANCE_HOSHIIMO, config.INSTANCE_PUBLIC):
            for how in ("freeze", "all"):
                with self.subTest(itype=itype, how=how):
                    SharedState.continue_round_reset()
                    self.daemons = []
                    monitor = self._monitor(itype=itype, how=how)
                    self._line(monitor, ROUND_OVER)
                    self.assertEqual(monitor.st.begin_skipped_seq, -1)
                    self.assertEqual(self._count(monitor, SKIP_LOG), 0, monitor.logs)
                    self.assertIn("do_after_round", self._names())

    def test_the_decision_is_made_before_the_all_continue_flag_is_cleared(self):
        monitor = self._monitor(how="all")
        self._line(monitor, ROUND_OVER)
        self.assertFalse(monitor.st.all_continue_round, "RoundOver で印は消える")
        self.assertEqual(monitor.st.begin_skipped_seq, 7, "消える前に決めてある")
        src = inspect.getsource(LogMonitor.LogMonitor._on_round_over)
        self.assertLess(src.index("self._skip_begin_after_continue()"),
                        src.index("st.all_continue_round = False"))

    def test_the_other_windows_are_unchanged(self):
        """この窓の続行でフリーズしていたほかの窓（続行ではない）は、今まで通り押す"""
        mine = self._monitor(how="freeze", window_idx=1)
        mine_daemons = self.daemons
        other = self._monitor(how="none", window_idx=2)
        self.assertTrue(mine.st.continue_freeze_held)
        self._line(other, ROUND_OVER)
        self.assertIn("do_after_round", self._names())
        self.assertEqual(other.st.begin_skipped_seq, -1)
        self._line(mine, ROUND_OVER)
        self.assertNotIn("do_after_round", [n for n, _a in mine_daemons])


class TestTheItemLoss(_Case):
    def _attend(self, monitor):
        return patch.object(monitor._action, "_attend_to_item_loss",
                            wraps=monitor._action._attend_to_item_loss)

    def test_the_item_loss_is_announced_at_round_over(self):
        monitor = self._monitor(how="freeze", lost=True, released=True)
        with self._attend(monitor) as attend:
            self._line(monitor, ROUND_OVER)
        attend.assert_called_once_with()
        st = monitor.st
        self.assertTrue(st.waiting_for_equip)
        self.assertTrue(st.equip_freeze_held, "全窓フリーズを張る")
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set(), "ほかの窓を止める")
        self.front.assert_called_once_with(0x10)
        self.play.assert_called_once_with("lost.mp3", kind="item_lost")
        self.assertEqual(self._count(monitor, LOST_LOG), 1, monitor.logs)
        self.assertNotIn("do_after_round", self._names())
        self.assertEqual(self._count(monitor, SKIP_LOG), 1)

    def test_equipping_releases_the_freeze_without_begin(self):
        monitor = self._monitor(how="freeze", lost=True, released=True)
        self._line(monitor, ROUND_OVER)
        self.assertFalse(monitor.st.begin_done, "Begin の受理は来ない")
        sleep = self._line(monitor, EQUIP)
        self.assertIn("_release_equip_freeze_after_equip", self._names())
        self.assertFalse(monitor.st.waiting_for_equip)
        with patch.object(LogMonitor.time, "sleep") as sleep:
            monitor._release_equip_freeze_after_equip()
        sleep.assert_called_once_with(config.EQUIP_RELEASE_DELAY_SEC)
        self.assertFalse(monitor.st.equip_freeze_held)
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(self._count(monitor, "✅ アイテム装備 → フリーズ解除"), 1, monitor.logs)

    def test_the_item_begin_mode_announces_once(self):
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor(how="freeze", lost=True, released=True)
        self.assertTrue(monitor._item_begin_mode_active(), "前提: モードが効く窓")
        with self._attend(monitor) as attend:
            self._line(monitor, ROUND_OVER)
        attend.assert_called_once_with()
        self.play.assert_called_once_with("lost.mp3", kind="item_lost")
        self.assertEqual(self._count(monitor, MODE_LOG), 1, "モード側の既存の枝が案内する")
        self.assertEqual(self._count(monitor, LOST_LOG), 0)
        self.assertNotIn("do_after_round", self._names())
        # 装備では、押さないラウンドとして Begin の受理を待たずに外す
        self._line(monitor, EQUIP)
        self.assertIn("_release_equip_freeze_after_equip", self._names())

    def test_the_item_begin_mode_is_unchanged_when_beginning(self):
        """続行でないラウンド（押す）では、モードの窓は今まで通り 1 回だけ案内する"""
        SharedState.set_item_begin_mode(True)
        monitor = self._monitor(how="none", lost=True)
        with self._attend(monitor) as attend:
            self._line(monitor, ROUND_OVER)
        attend.assert_called_once_with()
        self.assertEqual(self._count(monitor, MODE_LOG), 1)
        self.assertIn("do_after_round", self._names())

    def test_no_announce_without_an_item_loss(self):
        monitor = self._monitor(how="freeze", lost=False)
        with self._attend(monitor) as attend:
            self._line(monitor, ROUND_OVER)
        attend.assert_not_called()
        self.assertFalse(monitor.st.equip_freeze_held)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.play.assert_not_called()
        self.front.assert_not_called()
        self.assertEqual(self._count(monitor, SKIP_LOG), 1, "押さないログだけは出る")

    def test_the_verified_end_branches_are_the_same_as_auto_begin_off(self):
        """押さないラウンドの Verified Round End は、自動Begin OFF の窓と同じログ（_on_verified_end は変えていない）"""
        def item_logs(monitor):
            self._line(monitor, ROUND_OVER)
            self._line(monitor, VERIFIED_END)
            return [m for m in monitor.logs if "アイテム" in m or "全窓フリーズ" in m]
        skipped = item_logs(self._monitor(auto_begin=True, how="freeze", lost=True, released=True))
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        off = item_logs(self._monitor(auto_begin=False, how="none", lost=True))
        self.assertTrue(skipped)
        self.assertEqual(skipped, off)
        # アイテムを持っていないだけ（ロストではない）のラウンドも同じ
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        unheld_skipped = self._monitor(auto_begin=True, how="freeze", released=True)
        unheld_skipped.st.item_id = 0
        skipped = item_logs(unheld_skipped)
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        unheld_off = self._monitor(auto_begin=False, how="none")
        unheld_off.st.item_id = 0
        self.assertTrue(skipped)
        self.assertEqual(skipped, item_logs(unheld_off))

    def test_the_round_start_releases_at_once(self):
        monitor = self._monitor(how="freeze", lost=True, released=True)
        self._line(monitor, ROUND_OVER)
        self.assertTrue(monitor.st.equip_freeze_held)
        self._line(monitor, ROUND_START)
        self.assertFalse(monitor.st.equip_freeze_held)
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)


class TestTheSettingAndTheText(unittest.TestCase):
    def test_the_default_is_off(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_NO_BEGIN_AFTER_CONTINUE = _Setting(False, bool)", src)

    def test_the_setting_round_trips_in_shared_state(self):
        self.addCleanup(SharedState.set_no_begin_after_continue, False)
        SharedState.set_no_begin_after_continue(True)
        self.assertIs(SharedState.get_no_begin_after_continue(), True)
        SharedState.set_no_begin_after_continue(False)
        self.assertIs(SharedState.get_no_begin_after_continue(), False)

    def test_begin_skipped_seq_starts_at_minus_one(self):
        self.assertEqual(WindowState().begin_skipped_seq, -1)

    def test_the_log_line_is_the_one_agreed(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")
        self.assertIn(SKIP_LOG, src)

    def test_the_readme(self):
        readme = (Path(LogMonitor.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("**続行の後は自動Beginしない**（デフォルト OFF）", readme)
        # 2026-11-20: 押さない回もアイテムを取りに行く（README の文を直した）
        self.assertNotIn("アイテムの自動取得は動きません", readme)
        self.assertIn("音だけ鳴らして店へ取りに行きます", readme)


if __name__ == "__main__":
    unittest.main()
