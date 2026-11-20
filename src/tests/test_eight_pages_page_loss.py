"""8 Pages でページを取ったとき、持ち込めないアイテムは「所持」だけでなくロストの案内の記録も失う。

開始で死亡 → ロビーで装備し直し → 他の人がページを取った、という流れ（実機 2026-11-20 13:32）。
表示は「所持アイテム: なし」になったのに、ロストの案内の記録（st.item_id）が残り、
ラウンド終了のアイテムロストの案内が出なかった。時計・スレッド・音は偽物で、本物の入力は送らない。
"""
from tests.support import *  # noqa: F401,F403

T = "2026.11.20 13:32:00 Debug      -  "
START = T + "This round is taking place at Sewers (12) and the round type is 8 Pages"
DIED = T + "You died."
PAGE = T + "Page Collected - 6/8"
ROUND_OVER = T + "RoundOver"


def _item_ids():
    """テスト用の表（config.ITEMS を差し替えたもの）から、8 Pages に持ち込めない id と持ち込める id を
    1 つずつ引く（本物の item.json は依頼者が作成中なので、中身にテストを左右させない）"""
    banned = next(i for i in sorted(config.ITEMS)
                  if ItemCatalog.eight_pages_allowed(i, config.ITEMS) is False)
    allowed = next(i for i in sorted(config.ITEMS)
                   if ItemCatalog.eight_pages_allowed(i, config.ITEMS) is True)
    return banned, allowed


class TestEightPagesPageLoss(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        for reset in (SharedState.continue_round_reset, SharedState.round_freeze_reset,
                      SharedState.speed_freeze_reset, SharedState.equip_freeze_reset):
            reset()
            self.addCleanup(reset)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        for p in (patch.object(config, "ITEMS", _item_table()),
                  patch.object(ConnectDB, "register_round"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_round_over")):
            p.start()
            self.addCleanup(p.stop)
        self.banned, self.allowed = _item_ids()

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=9, auto_begin=False), {},
                                        lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.local_player_name = "serim01"
        monitor._start_daemon = lambda target, *a: None
        return monitor

    def _equip(self, item_id):
        return T + f"Equipping {item_id}."

    def _feed(self, monitor, *lines):
        for line in lines:
            monitor._process(line)

    def _round_over(self, monitor):
        with patch.object(monitor._action, "_attend_to_item_loss") as attend:
            monitor._process(ROUND_OVER)
        return attend

    def test_the_table_has_both_kinds_of_item(self):
        self.assertIs(ItemCatalog.eight_pages_allowed(self.banned, config.ITEMS), False)
        self.assertIs(ItemCatalog.eight_pages_allowed(self.allowed, config.ITEMS), True)

    def test_a_banned_item_equipped_in_the_lobby_is_lost_at_the_page_and_announced(self):
        monitor = self._monitor()
        self._feed(monitor, START, DIED, self._equip(self.banned))
        self.assertEqual(monitor.st.item_id, self.banned, "ロビーで装備し直した")

        self._feed(monitor, PAGE)

        st = monitor.st
        self.assertEqual(st.held_item_id, 0)
        self.assertEqual(st.item_id, 0, "ロストの案内の記録も失う")
        self.assertTrue(st.item_lost_this_round)
        self.assertTrue(monitor._round_lost_item())
        self.assertTrue(monitor._round_item_warning())
        self.assertEqual(st.last_lost_item_id, self.banned, "自動取得が取りに行くアイテム")
        self.assertTrue(any(m.endswith("8 Pages: ページ取得でアイテムロスト") for m in monitor.logs),
                        monitor.logs)

        attend = self._round_over(monitor)

        attend.assert_called_once()
        self.assertTrue(st.waiting_for_equip)
        self.assertTrue(any("アイテムロスト" in m and "RoundOver" in m for m in monitor.logs),
                        monitor.logs)

    def test_an_allowed_item_is_kept_and_not_announced(self):
        monitor = self._monitor()
        self._feed(monitor, START, DIED, self._equip(self.allowed))

        self._feed(monitor, PAGE)

        st = monitor.st
        self.assertEqual(st.held_item_id, self.allowed)
        self.assertEqual(st.item_id, self.allowed)
        self.assertFalse(monitor._round_lost_item())
        self.assertFalse(monitor._round_item_warning())
        self.assertFalse(any("ページ取得" in m for m in monitor.logs), monitor.logs)

        attend = self._round_over(monitor)

        attend.assert_not_called()
        self.assertFalse(monitor.st.waiting_for_equip)

    def test_an_item_that_is_not_in_the_table_is_kept(self):
        unknown = max(config.ITEMS) + 1000
        monitor = self._monitor()
        self._feed(monitor, START, DIED, self._equip(unknown))

        self._feed(monitor, PAGE)

        self.assertEqual(monitor.st.held_item_id, unknown)
        self.assertEqual(monitor.st.item_id, unknown)
        self.assertFalse(monitor._round_lost_item())

    def test_not_equipped_again_adds_no_log_line_for_the_record(self):
        """開始でロスト扱いにしたまま装備し直していないなら、ページを取っても新しい行を足さない"""
        monitor = self._monitor()
        self._feed(monitor, self._equip(self.banned), START)
        self.assertEqual(monitor.st.item_id, 0, "8 Pages の開始でロスト扱い")
        self.assertEqual(monitor.st.held_item_id, self.banned, "所持の表示はまだ失わない")
        before = [m for m in monitor.logs if "アイテムロスト" in m or "アイテムなし" in m]

        self._feed(monitor, PAGE)

        after = [m for m in monitor.logs if "アイテムロスト" in m or "アイテムなし" in m]
        self.assertEqual(after, before, "「アイテムロスト」「アイテムなし」の行は増えない")
        self.assertEqual(monitor.st.held_item_id, 0, "所持の表示はこれまで通りここで失う")
        self.assertTrue(any("ページ取得 でロスト" in m for m in monitor.logs), monitor.logs)
        self.assertTrue(monitor._round_lost_item())
        self.assertEqual(monitor.st.last_lost_item_id, self.banned)

    def test_a_page_outside_eight_pages_does_nothing(self):
        monitor = self._monitor()
        start = START.replace("8 Pages", "Classic")
        self._feed(monitor, start, self._equip(self.banned), PAGE)

        self.assertEqual(monitor.st.item_id, self.banned)
        self.assertEqual(monitor.st.held_item_id, self.banned)
        self.assertFalse(monitor._round_lost_item())


if __name__ == "__main__":
    unittest.main()
