"""消費系アイテムを手に持って手でクリックしたら、Maxwell を手に持ったまま自爆したら、アイテムロストにする。

LogMonitor（_check_item_used・_on_you_died）と ActionExecutor（自爆の長押しの始まり）を通す。長押しそのものは
WindowOperator.hold_key_background の偽物で、本物のキー入力・OSC・マウス・窓は使わない。
"""
from tests.support import *  # noqa: F401,F403
import ItemUse

T = "2026.11.13 09:20:00 Debug      -  "
DIED = T + "You died."
REBORN = T + "LOL JK, REBORN!"

BURGER, HAUNTED_BURGER, GLOGNUT = 36, 74, 79
EMERALD_COIL = 29           # 消費系でない（item.json）
MAXWELL_ID = 41
MAXWELL_LOST = "Maxwell を持って自爆: アイテムロスト"


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

        def hold(hwnd, key, sec, stop=None):
            if before:
                before(len(sent) + 1)
            sent.append(hwnd)
            if during:
                during(len(sent))
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            self.monitor._action.do_skip()
        return len(sent)


# ── A 消費系: 手に持っている間に手でクリックしたらアイテムロスト ──
class TestConsumableClick(_Case):
    def _click(self):
        self.assertTrue(self.tracker.use(ItemUse.SOURCE_HAND))

    def test_each_consumable_is_lost_by_a_hand_click_while_held(self):
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

    def test_a_drop_before_the_check_cancels_the_click(self):
        self._equip(BURGER)
        self.tracker.pickup("SomeHeldThing")
        self._click()
        self.tracker.drop("SomeHeldThing")
        self.monitor._check_item_used()
        self.assertEqual(self.st.item_id, BURGER)

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
            monitor._run()
        self.assertEqual(order[:2], ["flush", "check"])

    def test_the_constants(self):
        self.assertEqual(config.CONSUMABLE_ITEM_IDS, frozenset({36, 74, 79}))
        self.assertEqual([ItemCatalog.item_name(i, config.ITEMS) for i in sorted(config.CONSUMABLE_ITEM_IDS)],
                         ["Burger", "Haunted Burger", "Thorn Glognut"])
        self.assertEqual(config.MAXWELL_LOG_NAME, "maxwell")
        self.assertEqual(ItemCatalog.item_name(MAXWELL_ID, config.ITEMS), "Maxwell")
        self.assertEqual((LogMonitor.HELD_LOST_USED, LogMonitor.HELD_LOST_SUICIDE), ("使用", "自爆"))


# ── B Maxwell を手に持ったまま自爆したらアイテムロスト ──
class TestMaxwellSuicide(_Case):
    def _die(self, n):
        self.monitor._process(DIED)

    def test_the_names_that_count_as_maxwell(self):
        for name in ("maxwell", "Maxwell", "max well", " MAXWELL "):
            with self.subTest(name=name):
                self.setUp_again()
                self.tracker.pickup(name)
                seen = []
                self.assertEqual(self._suicide(during=lambda n: (seen.append(self.st.skip_maxwell_seq),
                                                                 self._die(n))), 1)
                self.assertEqual(seen, [self.ROUND_SEQ], "長押しの始まりの時点で印が立つ")
                self.assertEqual(self._screen().count(MAXWELL_LOST), 1, self.monitor.logs)
                self.assertEqual((self.st.item_id, self.st.held_item_id, self.st.last_lost_item_id),
                                 (0, 0, MAXWELL_ID))
                self.assertEqual(self.tracker.held_name(), "")
                self.assertEqual(self.st.skip_maxwell_seq, -1, "使ったら戻す")
                self.assertTrue(any("✅ 自爆成功" in m for m in self.monitor.logs))
                self.assertTrue(any("Maxwell を 自爆 でロスト" in m for m in self.monitor.logs), self.monitor.logs)

    def setUp_again(self):
        """subTest ごとに窓を作り直す"""
        self.monitor = self._monitor()
        self.st = self.monitor.st
        self.tracker = self.st.item_use
        self._equip(MAXWELL_ID)

    def test_only_equipped_not_held_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self._suicide(during=lambda n: (self.assertEqual(self.st.skip_maxwell_seq, -1), self._die(n)))
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))
        self.assertTrue(any("✅ 自爆成功" in m for m in self.monitor.logs))

    def test_holding_something_else_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("BloodKatCharm")
        self._suicide(during=self._die)
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual(self.tracker.held_name(), "BloodKatCharm")

    def test_a_death_that_is_not_a_suicide_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.monitor._process(DIED)
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual(self.st.skip_maxwell_seq, -1)
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))
        self.assertFalse(any("自爆成功" in m for m in self.monitor.logs))

    def test_a_death_that_is_not_a_suicide_then_reborn_behaves_as_before(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.monitor._process(DIED)
        self.monitor._process(REBORN)
        self.assertEqual(self._lost_lines(), [f"{LogMonitor.HELD_LOST_REBORN}: アイテムロスト"])

    def test_a_suicide_that_does_not_kill_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self.assertEqual(self._suicide(), 3, "死ななければ上限までやり直す")
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))

    def test_a_cancelled_suicide_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")

        def cancel(n):
            self.monitor._action.cancel_suicide()

        self.assertEqual(self._suicide(during=cancel), 1)
        self.assertEqual(self._lost_lines(), [])

    def test_a_death_in_the_next_round_is_not_lost_by_the_old_mark(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self._suicide()                                  # 死なずに終わる。印は前のラウンドのまま
        self.assertEqual(self.st.skip_maxwell_seq, self.ROUND_SEQ)
        self.st.round_seq = self.ROUND_SEQ + 1
        self.st.died_this_round = False
        # 長押しの終わり際と取られる時刻の死亡でも、ラウンドが違えばロストにしない
        self.st._skip_time = time.time()
        self.monitor._process(DIED)
        self.assertTrue(any("✅ 自爆成功" in m for m in self.monitor.logs))
        self.assertEqual(self._lost_lines(), [])
        self.assertEqual((self.st.item_id, self.st.held_item_id), (MAXWELL_ID, MAXWELL_ID))

    def test_picking_it_up_only_at_the_retry_is_lost(self):
        self._equip(MAXWELL_ID)
        seen = []

        def during(n):
            seen.append(self.st.skip_maxwell_seq)
            if n == 1:
                self.tracker.pickup("Maxwell")           # 1 回目の長押しの始まりのあとに拾った
            else:
                self._die(n)

        self.assertEqual(self._suicide(during=during), 2)
        self.assertEqual(seen, [-1, self.ROUND_SEQ], "2 回目の始まりで見直す")
        self.assertEqual(self._screen().count(MAXWELL_LOST), 1, self.monitor.logs)
        self.assertEqual((self.st.item_id, self.st.held_item_id), (0, 0))

    def test_dropping_it_before_the_retry_is_not_lost(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        seen = []

        def during(n):
            seen.append(self.st.skip_maxwell_seq)
            if n == 1:
                self.tracker.drop("Maxwell")             # 1 回目のあとに離した
            else:
                self._die(n)

        self.assertEqual(self._suicide(during=during), 2)
        self.assertEqual(seen, [self.ROUND_SEQ, -1], "2 回目の始まりで見直す")
        self.assertEqual(self._lost_lines(), [])

    def test_a_reborn_in_the_same_second_writes_the_lost_line_once(self):
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self._suicide(during=lambda n: (self.monitor._process(DIED), self.monitor._process(REBORN)))
        self.assertEqual(self._lost_lines(), [MAXWELL_LOST], self.monitor.logs)
        self.assertEqual((self.st.item_id, self.st.held_item_id, self.st.last_lost_item_id), (0, 0, MAXWELL_ID))
        self.assertFalse(self.st.died_this_round, "復活の扱いは今までどおり（死亡の取り消し）")

    def test_the_death_line_then_the_lost_line_order_and_no_double_on_run(self):
        self.st.round_type = "Run"
        self._equip(MAXWELL_ID)
        self.tracker.pickup("Maxwell")
        self._suicide(during=self._die)
        self.assertEqual(self._lost_lines(), [MAXWELL_LOST], "Run の死亡のロストが 2 行目に出ない")

    def test_without_a_tracker_the_start_of_the_hold_is_safe(self):
        st = WindowState(in_round=True, round_seq=self.ROUND_SEQ)
        self.assertIsNone(st.item_use)
        executor = ActionExecutor.ActionExecutor(WindowConfig(hwnd=9, do_skip=True), st, lambda: True,
                                                 lambda _m: None)
        self.assertEqual(executor._held_log_name(), "")
        with patch.object(WindowOperator, "hold_key_background", side_effect=lambda *a, **k: True):
            executor.do_skip()
        self.assertEqual(st.skip_maxwell_seq, -1)

    def test_the_state_default(self):
        self.assertEqual(WindowState().skip_maxwell_seq, -1)
