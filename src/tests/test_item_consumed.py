"""消費系アイテムを手に持って利用者がクリックしたら、Maxwell を手に持ったまま死んだら、アイテムロストにする。

LogMonitor（_check_item_used・_on_you_died）を通す。自爆の長押しは WindowOperator.hold_key_background の
偽物で、本物のキー入力・OSC・マウス・窓は使わない。
"""
from tests.support import *  # noqa: F401,F403
import ItemUse

T = "2026.11.13 09:20:00 Debug      -  "
DIED = T + "You died."
REBORN = T + "LOL JK, REBORN!"

BURGER, HAUNTED_BURGER, GLOGNUT = 36, 74, 79
EMERALD_COIL = 29           # 消費系でない（item.json）
MAXWELL_ID = 41
MAXWELL_LOST = "Maxwell を持って死亡: アイテムロスト"


class _Case(unittest.TestCase):
    ROUND_SEQ = 5

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.set_continue_drop_item(False)
        SharedState.set_suicide_key("^")
        for reset in (SharedState.continue_round_reset, SharedState.round_freeze_reset,
                      SharedState.speed_freeze_reset, SharedState.equip_freeze_reset):
            reset()
            self.addCleanup(reset)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.set_continue_drop_item, False)
        self.addCleanup(SharedState.set_suicide_key, config.SELF_SUICIDE_KEY)
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.debug = []
        for p in (patch.object(config, "ITEMS", ItemCatalog.load_items(config.resource_path("assets/data/item.json"))),
                  patch.object(ConnectDB, "register_round"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_round_over"),
                  patch.object(DebugLog, "write", side_effect=self.debug.append),
                  patch.object(config, "SUICIDE_CONFIRM_SEC", 0.05),
                  patch.object(config, "SUICIDE_RETRY_MAX", 3)):
            p.start()
            self.addCleanup(p.stop)
        self.monitor = self._monitor()
        self.st = self.monitor.st
        self.tracker = self.st.item_use

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=9, auto_begin=False, do_skip=True), {},
                                        lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._start_daemon = lambda target, *a: None
        st = monitor.st
        st.instance_type = config.INSTANCE_PRIVATE
        st.in_round = True
        st.round_type = "Classic"
        st.round_seq = self.ROUND_SEQ
        return monitor

    def _equip(self, item_id):
        self.st.held_item_id = item_id
        self.st.item_id = item_id

    def _lost_lines(self):
        return [m.removeprefix("[窓1] ") for m in self.monitor.logs if "アイテムロスト" in m]

    def _screen(self):
        """画面のログ（窓の頭の印を除く）"""
        return [m.removeprefix("[窓1] ") for m in self.monitor.logs]

    def _suicide(self, during=None, before=None):
        """自爆の長押しを走らせる。during(n) は n 回目の長押しの最中（死亡の行を流す所）、
        before(n) は n 回目の長押しの直前。戻り値は長押しした回数"""
        sent = []

        def hold(hwnd, key, sec, stop=None, wake=None):
            if before:
                before(len(sent) + 1)
            sent.append(hwnd)
            if during:
                during(len(sent))
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            self.monitor._action.do_skip()
        return len(sent)


# ── A 消費系: 手に持っている間に利用者がクリックしたらアイテムロスト ──
class TestConsumableClick(_Case):
    def _click(self):
        self.assertTrue(self.tracker.use(ItemUse.SOURCE_HAND))

    def test_each_consumable_is_lost_by_a_user_click_while_held(self):
        for item_id, name in ((BURGER, "Burger"), (HAUNTED_BURGER, "Haunted Burger"),
                              (GLOGNUT, "Thorn Glognut")):
            with self.subTest(item_id=item_id):
                monitor = self._monitor()
                st, tracker = monitor.st, monitor.st.item_use
                st.held_item_id = st.item_id = item_id
                tracker.pickup("SomeHeldThing")
                tracker.use(ItemUse.SOURCE_HAND)
                monitor._check_item_used()
                self.assertEqual((st.item_id, st.held_item_id, st.last_lost_item_id), (0, 0, item_id))
                self.assertEqual(tracker.held_name(), "", "追跡も外れる")
                self.assertTrue(st.item_lost_this_round)
                self.assertTrue(any(f"{name} を使用: アイテムロスト" in m for m in monitor.logs), monitor.logs)
                self.assertTrue(any(f"{name} を 使用 でロスト" in m for m in monitor.logs), monitor.logs)

    def test_the_held_thing_may_have_any_name(self):
        self._equip(BURGER)
        self.monitor._process(T + "[Behaviour] Pickup object: 'NotABurgerAtAll' equipped = True, "
                                  "is AutoEquipType Pickup = True, last input method = Mouse")
        self._click()
        self.monitor._check_item_used()
        self.assertEqual(self.st.item_id, 0)

    def test_a_click_while_not_holding_changes_nothing(self):
        self._equip(BURGER)
        self.assertFalse(self.tracker.use(ItemUse.SOURCE_HAND))
        self.monitor._check_item_used()
        self.assertEqual((self.st.item_id, self.st.held_item_id, self.st.last_lost_item_id), (BURGER, BURGER, 0))
        self.assertEqual(self._lost_lines(), [])

    def test_a_click_with_a_non_consumable_equipped_is_dropped(self):
        self._equip(EMERALD_COIL)
        self.tracker.pickup("SomeHeldThing")
        self._click()
        self.monitor._check_item_used()
        self.assertEqual((self.st.item_id, self.st.held_item_id), (EMERALD_COIL, EMERALD_COIL))
        self.assertEqual(self._lost_lines(), [])
        # 捨てたクリックは、あとで消費系に持ち替えても生き返らない
        self._equip(BURGER)
        self.monitor._check_item_used()
        self.assertEqual(self.st.item_id, BURGER)
        self.assertEqual(self._lost_lines(), [])

    def test_nothing_equipped_with_a_click_changes_nothing(self):
        self.tracker.pickup("SomeHeldThing")
        self._click()
        self.monitor._check_item_used()
        self.assertEqual(self._lost_lines(), [])
        self.assertFalse(self.st.item_lost_this_round)

    def test_one_click_does_not_lose_twice(self):
        self._equip(BURGER)
        self.tracker.pickup("SomeHeldThing")
        self._click()
        self.monitor._check_item_used()
        self.monitor._check_item_used()
        self.assertEqual(len(self._lost_lines()), 1)
        # 取り直しても、クリックなしではロストにしない
        self._equip(BURGER)
        self.tracker.pickup("SomeHeldThing")
        self.monitor._check_item_used()
        self.assertEqual(self.st.item_id, BURGER)
        self.assertEqual(len(self._lost_lines()), 1)

    def test_several_clicks_before_the_check_count_once(self):
        self._equip(BURGER)
        self.tracker.pickup("SomeHeldThing")
        for _ in range(5):
            self.tracker.use(ItemUse.SOURCE_HAND)
        self.monitor._check_item_used()
        self.assertEqual(len(self._lost_lines()), 1)

    def test_a_drop_before_the_check_does_not_cancel_the_click(self):
        """使うと無くなる物は、クリックの直後に Drop の行が来る。見回りの前に離していてもロストにする"""
        self._equip(BURGER)
        self.tracker.pickup("SomeHeldThing")
        self._click()
        self.tracker.drop("SomeHeldThing")
        self.monitor._check_item_used()
        self.assertEqual((self.st.item_id, self.st.held_item_id), (0, 0))
        self.assertEqual(len(self._lost_lines()), 1)

    def test_the_debug_line_has_the_item_and_the_held_name_only(self):
        self._equip(GLOGNUT)
        self.tracker.pickup("SomeHeldThing")
        self._click()
        self.monitor._check_item_used()
        lines = [m for m in self.debug if "消費系" in m]
        self.assertEqual(len(lines), 1)
        self.assertIn("Thorn Glognut", lines[0])
        self.assertIn("SomeHeldThing", lines[0])
        self.assertTrue(lines[0].startswith("[窓1] "))

    def test_an_error_does_not_escape_and_is_written(self):
        with patch.object(self.tracker, "take_use", side_effect=RuntimeError("boom")), \
             patch.object(DebugLog, "exception") as written:
            self.monitor._check_item_used()
        written.assert_called_once_with("LogMonitor._check_item_used")

    def test_the_poll_loop_checks_after_the_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output_log.txt"
            path.write_text("", encoding="utf-8")
            monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x32, log_path=path), {}, lambda _m: None,
                                            window_idx=1)
            order = []
            monitor._flush_item_use_log = lambda: order.append("flush")
            monitor._check_item_used = lambda: order.append("check")
            monitor._running = True
            monitor._stop_event.set()                   # 1 周で抜ける
            with patch.object(monitor, "_verify_access", return_value=True):     # 確認は別のテストで見る
                monitor._run()
        self.assertEqual(order[:2], ["flush", "check"])

    def test_the_constants(self):
        self.assertEqual(config.CONSUMABLE_ITEM_IDS, frozenset({36, 74, 79}))
        self.assertEqual([ItemCatalog.item_name(i, config.ITEMS) for i in sorted(config.CONSUMABLE_ITEM_IDS)],
                         ["Burger", "Haunted Burger", "Thorn Glognut"])
        self.assertEqual(config.MAXWELL_LOG_NAME, "maxwell")
        self.assertEqual(ItemCatalog.item_name(MAXWELL_ID, config.ITEMS), "Maxwell")
        self.assertEqual((LogMonitor.HELD_LOST_USED, LogMonitor.HELD_LOST_DEATH), ("使用", "死亡"))


# ── B Maxwell を手に持ったまま死んだらアイテムロスト（死亡の行を読んだ時点で持っている物で決める） ──
class TestMaxwellDeath(_Case):
    def _die(self, n=None):
        self.monitor._process(DIED)

    def _fresh(self):
        """subTest ごとに窓を作り直す"""
        self.monitor = self._monitor()
        self.st = self.monitor.st
        self.tracker = self.st.item_use
        self._equip(MAXWELL_ID)

    def test_the_names_that_count_as_maxwell(self):
        for name in ("maxwell", "Maxwell", "max well", " MAXWELL "):
            with self.subTest(name=name):
                self._fresh()
                self.tracker.pickup(name)
                self._die()
                self.assertEqual(self._screen().count(MAXWELL_LOST), 1, self.monitor.logs)
                self.assertEqual((self.st.item_id, self.st.held_item_id, self.st.last_lost_item_id),
                                 (0, 0, MAXWELL_ID))
                self.assertEqual(self.tracker.held_name(), "")
                self.assertTrue(self.st.item_lost_this_round)
                self.assertTrue(any("Maxwell を 死亡 でロスト" in m for m in self.monitor.logs), self.monitor.logs)

    def test_a_death_with_maxwell_in_hand_is_lost_without_a_tool_suicide(self):
        """利用者が自分で自爆した死亡・テラーに殺された死亡でも、手に Maxwell なら拾う（ツールの自爆は押していない）"""
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.assertEqual(self.st._skip_time, 0.0)
        self._die()
        self.assertEqual(self._lost_lines(), [MAXWELL_LOST], self.monitor.logs)
        self.assertFalse(any("自爆成功" in m for m in self.monitor.logs))
        self.assertEqual((self.st.item_id, self.st.held_item_id, self.st.last_lost_item_id), (0, 0, MAXWELL_ID))

    def test_a_tool_suicide_with_maxwell_in_hand_is_lost_once(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.assertEqual(self._suicide(during=self._die), 1)
        self.assertEqual(self._screen().count(MAXWELL_LOST), 1, self.monitor.logs)
        self.assertTrue(any("✅ 自爆成功" in m for m in self.monitor.logs))
        self.assertEqual((self.st.item_id, self.st.held_item_id), (0, 0))

    def test_leaving_the_hand_before_the_death_line_is_not_lost(self):
        """自爆の最中に手放した（実ログの 11/18 の 3 件）。無くならない"""
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")

        def during(n):
            self.tracker.drop("Maxwell")             # 長押しの最中に手から外れた
            self._die()

        self.assertEqual(self._suicide(during=during), 1)
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))
        self.assertFalse(self.st.item_lost_this_round)
        self.assertTrue(any("✅ 自爆成功" in m for m in self.monitor.logs))

    def test_dropping_in_the_same_second_before_the_death_line_is_not_lost(self):
        """死亡と同じ秒で、死亡の行より前に離した（実ログの 11/15）。無くならない"""
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.tracker.drop("Maxwell")
        self._die()
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))

    def test_only_equipped_not_held_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self._suicide(during=self._die)
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))
        self.assertFalse(self.st.item_lost_this_round)
        self.assertTrue(any("✅ 自爆成功" in m for m in self.monitor.logs))

    def test_holding_something_else_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("BloodKatCharm")
        self._suicide(during=self._die)
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual(self.tracker.held_name(), "BloodKatCharm")
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))

    def test_a_suicide_that_does_not_kill_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.assertEqual(self._suicide(), 3, "死ななければ上限までやり直す")
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))

    def test_a_reborn_in_the_same_second_writes_the_lost_line_once(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self._suicide(during=lambda n: (self.monitor._process(DIED), self.monitor._process(REBORN)))
        self.assertEqual(self._lost_lines(), [MAXWELL_LOST], self.monitor.logs)
        self.assertEqual((self.st.item_id, self.st.held_item_id, self.st.last_lost_item_id), (0, 0, MAXWELL_ID))
        self.assertFalse(self.st.died_this_round, "復活の扱いは今までどおり（死亡の取り消し）")

    def test_killed_by_a_terror_then_reborn_writes_the_lost_line_once(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.monitor._process(DIED)
        self.monitor._process(REBORN)
        self.assertEqual(self._lost_lines(), [MAXWELL_LOST], self.monitor.logs)
        self.assertTrue(any("猫消費 → 死亡を取り消し" in m for m in self.debug), self.debug)

    def test_a_reborn_without_a_hand_hold_is_still_a_loss_as_before(self):
        self._equip(MAXWELL_ID)
        self.monitor._process(DIED)
        self.monitor._process(REBORN)
        self.assertEqual(self._lost_lines(), [f"{LogMonitor.HELD_LOST_REBORN}: アイテムロスト"])

    def test_a_death_outside_a_round_does_nothing(self):
        self.st.in_round = False
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self._die()
        self.assertEqual(self._lost_lines(), [])
        self.assertFalse(self.st.item_lost_this_round)
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))
        self.assertEqual(self.tracker.held_name(), "Maxwell")

    def test_the_death_line_then_the_lost_line_order_and_no_double_on_run(self):
        self.st.round_type = "Run"
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self._suicide(during=self._die)
        self.assertEqual(self._lost_lines(), [MAXWELL_LOST], "Run の死亡のロストが 2 行目に出ない")

    def test_a_run_death_without_maxwell_still_loses_the_item(self):
        self.st.round_type = "Run"
        self._equip(EMERALD_COIL)
        self._die()
        self.assertEqual(self._lost_lines(), ["Run死亡: アイテムロスト"])

    def test_without_a_tracker_a_death_is_safe(self):
        self.st.item_use = None
        with patch.object(self.monitor, "_lose_held_item") as lose:
            self._die()                                  # 例外にならない
        lose.assert_not_called()
        self.assertEqual(self._lost_lines(), [])
        self.assertTrue(self.st.died_this_round, "死亡の後始末は今までどおり")

    def test_the_state_has_no_tracker_until_the_monitor_makes_one(self):
        self.assertIsNone(WindowState().item_use)
