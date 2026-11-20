"""Begin を押さない回のアイテム自動取得（依頼者了承 2026-11-20。実機の測りで固めた値）。

ツールが Begin を押さない回（自動Begin OFF・「続行の後は自動Beginしない」の回）でも、private の窓で取りに行く:
  1. RoundOver の後（ActionExecutor.fetch_after_round_without_begin。RoundOver + BEGIN_WAIT_SEC から）
  2. ロビーのリスポーンでアイテムが外れた後の opted in の行（fetch_after_lobby_respawn。後ろへ → 左へ → 向きの直しをしてから）
移動は ItemFetch.Fetcher.move_to_shop_from_spawn（左 → 0.4 秒後に左前 → 空中 0.20 秒以上の後の接地で止まって左へ 90 度）。
時計・スレッド・窓・音・OSC・撮影は偽物で、本物の入力は送らない。
"""
from tests.support import *  # noqa: F401,F403
from tests.test_opted_in_item_notice import (  # noqa: F401
    _Case as _MonitorCase, RESPAWN, OPTED_IN, ROUND_OVER, VERIFIED_END, NOTICE, ROUND_OVER_LOST)

SPAWN_ROUND_OVER = "RoundOver 【⚠ アイテムロスト → 取りに行きます】"
SPAWN_LOBBY = "リスポーンでアイテムが外れました → 取りに行きます"
SPAWN_VERIFIED = "ラウンド終了 【⚠ アイテムロスト → 店へ取りに行きます（Begin は押しません）】"
SPAWN_VERIFIED_UNCOLLECTED = "ラウンド終了 【⚠ アイテム未回収 → 店へ取りに行きます（Begin は押しません）】"
OLD_VERIFIED = "ラウンド終了 【⚠ アイテムロスト → Begin が通ったらフリーズ】"
ITEMS = {29: ItemCatalog.Item("Emerald Coil", "Survival", False)}


# ── 移動 ──────────────────────────────────
class TestMoveToShopFromSpawn(unittest.TestCase):
    class FakeOsc:
        def __init__(self, test):
            self.test = test
            self.sent = []
            self.stopped = 0

        def send(self, address, value):
            self.sent.append((round(self.test.now[0], 2), address, value))

        def stop_all(self, repeat=2):
            self.stopped += 1

    def setUp(self):
        self.now = [0.0]

    def _fetcher(self, grounded=lambda: True, stopped=lambda: None):
        self.osc = self.FakeOsc(self)
        self.logs = []

        def sleep(sec):
            self.now[0] += sec
        return ItemFetch.Fetcher(
            osc=self.osc, grounded=grounded, capture=lambda: ("shot", (960.0, 540.0)), mouse=MagicMock(),
            equip_seen=lambda: (0, 0), stopped=stopped, log=self.logs.append,
            sleep=sleep, clock=lambda: self.now[0])

    def _air(self, *spans):
        """spans: (始まり, 終わり) の間だけ空中（接地 False）。それ以外は接地。時刻は偽の時計"""
        return lambda: not any(a <= self.now[0] < b for a, b in spans)

    def _addresses(self):
        return [(a, v) for _t, a, v in self.osc.sent]

    def test_the_values_are_the_measured_ones(self):
        self.assertEqual((ItemFetch.SPAWN_LEFT_FIRST_SEC, ItemFetch.SPAWN_MIN_AIR_SEC,
                          ItemFetch.SPAWN_LEAVE_LIMIT_SEC, ItemFetch.SPAWN_LAND_LIMIT_SEC, ItemFetch.TURN_SEC),
                         (0.40, 0.20, 5.0, 1.5, 0.50))

    def test_left_then_forward_then_stop_on_the_landing_and_turn_left(self):
        f = self._fetcher(grounded=self._air((2.0, 2.35)))
        self.assertTrue(f.move_to_shop_from_spawn())
        sent = self.osc.sent
        self.assertEqual(sent[0], (0.0, "/input/MoveLeft", 1))
        self.assertEqual(sent[1], (0.4, "/input/MoveForward", 1), "左だけで 0.40 秒歩いてから前も押す")
        landed = sent[2][0]
        self.assertTrue(2.30 <= landed <= 2.40, f"空中 0.35 秒の後の接地で止める（{landed}）")
        self.assertEqual([(a, v) for _t, a, v in sent[2:]],
                         [("/input/MoveForward", 0), ("/input/MoveLeft", 0),
                          ("/input/LookLeft", 1), ("/input/LookLeft", 0)])
        self.assertEqual({t for t, _a, _v in sent[2:5]}, {landed}, "着地したらすぐ離して向く")
        self.assertAlmostEqual(sent[5][0] - sent[4][0], ItemFetch.TURN_SEC, places=2)
        self.assertEqual(self.osc.stopped, 1, "最後に全部離す")
        self.assertTrue(any("降りて着地" in m for m in self.logs), self.logs)
        self.assertTrue(any("店の前に着いた" in m for m in self.logs), self.logs)

    def test_a_short_float_is_ignored_and_the_real_drop_is_counted(self):
        """歩いている途中の接地の浮き（実測 0.05〜0.13 秒）は着地に数えない"""
        f = self._fetcher(grounded=self._air((1.0, 1.10), (1.5, 1.69), (2.3, 2.7)))
        self.assertTrue(f.move_to_shop_from_spawn())
        released = [t for t, a, v in self.osc.sent if (a, v) == ("/input/MoveForward", 0)]
        self.assertEqual(len(released), 1)
        self.assertGreaterEqual(released[0], 2.65, "最初の 2 回の浮きでは止まらない")
        flickers = [m for m in self.logs if "接地の浮き" in m]
        self.assertEqual(len(flickers), 2, self.logs)

    def test_an_air_of_exactly_the_minimum_counts(self):
        f = self._fetcher(grounded=self._air((2.0, 2.205)))
        self.assertTrue(f.move_to_shop_from_spawn())
        self.assertFalse(any("接地の浮き" in m for m in self.logs), self.logs)

    def test_no_drop_within_the_limit_fails_and_releases(self):
        for received, expect in ((True, "受け取っています"), (None, "受け取れていません")):
            with self.subTest(received=received):
                self.now[0] = 0.0
                f = self._fetcher(grounded=(lambda: True) if received else (lambda: None))
                self.assertFalse(f.move_to_shop_from_spawn())
                self.assertTrue(5.0 <= self.now[0] <= 5.03, self.now[0])
                self.assertNotIn(("/input/LookLeft", 1), self._addresses())
                self.assertEqual(self.osc.stopped, 1)
                self.assertTrue(any("降りません" in m and expect in m for m in self.logs), self.logs)

    def test_an_air_before_any_ground_is_not_a_drop(self):
        """接地を 1 回も見ないうちの False は空中の始まりに数えない"""
        f = self._fetcher(grounded=lambda: False)
        self.assertFalse(f.move_to_shop_from_spawn())
        self.assertTrue(5.0 <= self.now[0] <= 5.03, self.now[0])
        self.assertNotIn(("/input/LookLeft", 1), self._addresses())

    def test_no_landing_after_the_air_fails(self):
        f = self._fetcher(grounded=self._air((2.0, 99.0)))
        self.assertFalse(f.move_to_shop_from_spawn())
        self.assertTrue(2.0 + 1.5 <= self.now[0] <= 2.0 + 1.5 + 0.05, self.now[0])
        self.assertNotIn(("/input/LookLeft", 1), self._addresses())
        self.assertEqual(self.osc.stopped, 1)
        self.assertTrue(any("着地しません" in m for m in self.logs), self.logs)

    def test_a_stop_while_walking_releases_the_keys(self):
        f = self._fetcher(grounded=self._air((2.0, 2.35)),
                          stopped=lambda: "round" if self.now[0] >= 1.0 else None)
        with self.assertRaises(ItemFetch.Stopped) as caught:
            f.move_to_shop_from_spawn()
        self.assertEqual(caught.exception.args[0], "round")
        self.assertEqual(self.osc.stopped, 1, "押したままにしない")
        self.assertNotIn(("/input/LookLeft", 1), self._addresses())

    def test_a_stop_while_turning_releases_the_keys(self):
        f = self._fetcher(grounded=self._air((2.0, 2.35)),
                          stopped=lambda: "stopped" if self.now[0] >= 2.5 else None)
        with self.assertRaises(ItemFetch.Stopped):
            f.move_to_shop_from_spawn()
        self.assertIn(("/input/LookLeft", 1), self._addresses())
        self.assertEqual(self.osc.stopped, 1)

    def test_move_to_shop_is_not_changed(self):
        """柵越えの道は今のまま（左 → 柵 → 後ろ・左 → 向く）。spawn の道を通らない"""
        f = self._fetcher(grounded=lambda: True)
        self.assertFalse(f.move_to_shop())
        self.assertNotIn(("/input/MoveForward", 1), self._addresses())


# ── アイテム自動取得の対象 ──────────────────
class _ExecutorCase(unittest.TestCase):
    def setUp(self):
        SharedState.set_item_fetch(True)
        SharedState.set_item_begin_mode(False)
        SharedState.set_hands_free(False)
        for undo in (SharedState.set_item_fetch, SharedState.set_item_begin_mode, SharedState.set_hands_free):
            self.addCleanup(undo, False)
        for reset in (SharedState.equip_freeze_reset, SharedState.continue_round_reset,
                      SharedState.speed_freeze_reset, SharedState.round_freeze_reset):
            reset()
            self.addCleanup(reset)
        self.addCleanup(patch.stopall)
        patch.object(config, "ITEMS", ITEMS).start()
        patch.object(ItemFetch, "available", return_value=True).start()
        patch.object(DebugLog, "write").start()
        self.running = {"on": True}
        self.logs = []

    def _executor(self, auto_begin=False, skipped=False, itype=config.INSTANCE_PRIVATE, receiver=True, osc=True):
        cfg = WindowConfig(hwnd=0x55, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=itype, round_seq=7, window_idx=2, waiting_for_equip=True,
                         item_id=0, last_lost_item_id=29, round_over_time=100.0)
        if skipped:
            st.begin_skipped_seq = 7
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: self.running["on"], self.logs.append,
                                           auto_begin_active=lambda: auto_begin
                                           and itype in config.PRIVATE_LIKE_INSTANCES)
        ex._osc = MagicMock() if osc else None
        ex._receiver = MagicMock() if receiver else None
        return ex, st


class TestTheTarget(_ExecutorCase):
    def test_a_window_where_the_tool_does_not_press_begin_is_a_target(self):
        for label, kwargs in (("自動Begin OFF", dict(auto_begin=False)),
                              ("続行の後は押さない回", dict(auto_begin=True, skipped=True))):
            with self.subTest(label):
                ex, _st = self._executor(**kwargs)
                self.assertEqual(ex.item_fetch_target("spawn"), ("Survival", 29))

    def test_the_begin_route_is_as_before(self):
        ex, _st = self._executor(auto_begin=False)
        self.assertIsNone(ex.item_fetch_target(), "Begin を押さない窓は今までどおり取りに行かない")
        self.assertIsNone(ex.item_fetch_target("begin"))
        ex, _st = self._executor(auto_begin=True)
        self.assertEqual(ex.item_fetch_target(), ("Survival", 29))
        self.assertEqual(ex.item_fetch_target("begin"), ("Survival", 29))
        ex, _st = self._executor(auto_begin=True, skipped=True)
        self.assertEqual(ex.item_fetch_target(), ("Survival", 29), "Begin の道は押さない回かを見ない（今まで）")

    def test_a_window_where_the_tool_presses_begin_is_not_a_spawn_target(self):
        ex, _st = self._executor(auto_begin=True)
        self.assertIsNone(ex.item_fetch_target("spawn"))

    def test_the_skip_mark_of_another_round_does_not_count(self):
        ex, st = self._executor(auto_begin=True, skipped=True)
        st.round_seq = 8
        self.assertIsNone(ex.item_fetch_target("spawn"))

    def test_only_private_like_windows(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO,
                      config.INSTANCE_OTHER_GROUP, config.INSTANCE_ACHIEVEMENT, ""):
            with self.subTest(itype=itype):
                ex, _st = self._executor(auto_begin=False, itype=itype)
                self.assertIsNone(ex.item_fetch_target("spawn"))
        ex, _st = self._executor(auto_begin=False, itype=config.INSTANCE_SEER)
        self.assertEqual(ex.item_fetch_target("spawn"), ("Survival", 29))
        ex, st = self._executor(auto_begin=False)
        st.in_ton_world = False
        self.assertIsNone(ex.item_fetch_target("spawn"))

    def test_the_conditions_that_stop_it(self):
        ex, _st = self._executor(osc=False)
        self.assertIsNone(ex.item_fetch_target("spawn"), "OSC が使えない")
        ex, _st = self._executor(receiver=False)
        self.assertIsNone(ex.item_fetch_target("spawn"), "接地の受信がない")
        ex, _st = self._executor()
        SharedState.set_item_fetch(False)
        self.assertIsNone(ex.item_fetch_target("spawn"), "設定 OFF")
        SharedState.set_item_fetch(True)
        SharedState.set_item_begin_mode(True)
        self.assertIsNone(ex.item_fetch_target("spawn"), "アイテム取得→Begin モード")
        SharedState.set_item_begin_mode(False)
        SharedState.set_hands_free(True)
        self.assertIsNone(ex.item_fetch_target("spawn"), "完全放置モード")
        SharedState.set_hands_free(False)
        self.assertEqual(ex.item_fetch_target("spawn"), ("Survival", 29))
        ex._st.last_lost_item_id = 0
        self.assertIsNone(ex.item_fetch_target("spawn"), "ロストしたアイテムが分からない")
        ex._st.last_lost_item_id = 999
        self.assertIsNone(ex.item_fetch_target("spawn"), "表に無いアイテム")


# ── どの移動を使うか ──────────────────────
class TestTheRoute(_ExecutorCase):
    def _run(self, route):
        ex, st = self._executor(auto_begin=False)
        fetcher = MagicMock()
        fetcher.move_to_shop.return_value = False
        fetcher.move_to_shop_from_spawn.return_value = False
        with patch.object(ActionExecutor.ItemFetch, "Fetcher", return_value=fetcher):
            kwargs = {} if route is None else {"route": route}
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29, **kwargs), "failed")
        return fetcher

    def test_spawn_uses_the_spawn_move(self):
        fetcher = self._run("spawn")
        fetcher.move_to_shop_from_spawn.assert_called_once_with()
        fetcher.move_to_shop.assert_not_called()

    def test_begin_and_the_default_use_the_fence_move(self):
        for route in (None, "begin"):
            fetcher = self._run(route)
            fetcher.move_to_shop.assert_called_once_with()
            fetcher.move_to_shop_from_spawn.assert_not_called()

    def test_after_the_move_the_shop_is_the_same(self):
        ex, st = self._executor(auto_begin=False)
        fetcher = MagicMock()
        fetcher.move_to_shop_from_spawn.return_value = True
        with patch.object(ActionExecutor.ItemFetch, "Fetcher", return_value=fetcher), \
             patch.object(ex, "_fetch_in_front", return_value="equipped") as front:
            self.assertEqual(ex._fetch_item(st.round_seq, "Survival", 29, route="spawn"), "ok")
        front.assert_called_once_with(fetcher, "Survival", 29, False)


# ── RoundOver の後 ──────────────────────
class _World:
    """偽の時計（ActionExecutor.time）。sleep で進む"""

    def __init__(self, test, start=100.0):
        self.now = [start]
        self.on_sleep = None
        w = self

        def sleep(sec):
            w.now[0] += sec
            if w.on_sleep:
                w.on_sleep()
        fake_time = type(sys)("time_for_test")
        fake_time.time = lambda: w.now[0]
        fake_time.monotonic = lambda: w.now[0]
        fake_time.sleep = sleep
        p = patch.object(ActionExecutor, "time", fake_time)
        p.start()
        test.addCleanup(p.stop)


class TestFetchAfterRoundOver(_ExecutorCase):
    def setUp(self):
        super().setUp()
        self.w = _World(self)
        self.ex, self.st = self._executor(auto_begin=False)
        self.events = []
        self.fetch_at = []
        self.ex._fetch_item = lambda *a, **k: (self.events.append(("fetch", a, k)),
                                               self.fetch_at.append(self.w.now[0]), "ok")[-1]
        self.ex.announce_item_lost_once = lambda: self.events.append("sound")
        self.ex._attend_to_item_loss = lambda: self.events.append("notice")

    def test_it_waits_for_round_over_plus_the_begin_wait_then_goes(self):
        self.w.now[0] = 100.0 + 2.0        # RoundOver の 2 秒後
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, ["sound", ("fetch", (7, "Survival", 29), {"route": "spawn"})])
        self.assertTrue(100.0 + config.BEGIN_WAIT_SEC <= self.fetch_at[0] <= 100.0 + config.BEGIN_WAIT_SEC + 0.01,
                        self.fetch_at)

    def test_it_goes_at_once_when_the_wait_is_already_over(self):
        self.w.now[0] = 100.0 + 30.0
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.fetch_at, [130.0])

    def test_it_quits_quietly_when_the_round_starts_while_waiting(self):
        self.w.on_sleep = lambda: setattr(self.st, "in_round", self.w.now[0] >= 105.0)
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, [])

    def test_it_quits_quietly_when_the_round_number_changes(self):
        self.w.on_sleep = lambda: setattr(self.st, "round_seq", 8) if self.w.now[0] >= 105.0 else None
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, [])

    def test_it_quits_quietly_when_stopped(self):
        self.w.on_sleep = lambda: self.running.update(on=self.w.now[0] < 105.0)
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, [])

    def test_it_quits_quietly_when_the_window_is_no_longer_private(self):
        self.w.on_sleep = lambda: setattr(self.st, "instance_type",
                                          config.INSTANCE_PUBLIC if self.w.now[0] >= 105.0 else config.INSTANCE_PRIVATE)
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, [])

    def test_it_does_nothing_when_the_item_was_equipped_meanwhile(self):
        def equip():
            if self.w.now[0] >= 105.0:
                self.st.item_id, self.st.waiting_for_equip = 29, False
        self.w.on_sleep = equip
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, [], "案内も取得もしない")

    def test_when_it_can_no_longer_go_the_notice_comes(self):
        def off():
            if self.w.now[0] >= 105.0:
                SharedState.set_item_fetch(False)
        self.w.on_sleep = off
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, ["notice"])

    def test_no_second_notice_when_the_respawn_notice_was_given(self):
        SharedState.set_item_fetch(False)
        self.st.respawn_notice_seq = 7
        self.w.now[0] = 130.0
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, [])

    def test_it_waits_for_a_run_respawn_to_finish_first(self):
        self.ex._run_respawn_idle.clear()
        calls = []
        self.ex._wait_run_respawn = lambda: calls.append(1) or False
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual((calls, self.events), ([1], []))

    def test_the_failure_of_the_fetch_gives_nothing(self):
        self.w.now[0] = 130.0
        self.ex._fetch_item = lambda *a, **k: self.events.append("fetch") or "failed"
        self.ex.fetch_after_round_without_begin(7)
        self.assertEqual(self.events, ["sound", "fetch"], "失敗しても案内を出さない（次のラウンドでまた）")

    def test_the_sound_is_the_only_announcement_while_going(self):
        """案内（前面化・音声・全窓フリーズ）の _attend_to_item_loss は呼ばず、音だけ（announce_item_lost_once）"""
        ex, st = self._executor(auto_begin=False)
        st.round_over_time = 0.0
        ex._fetch_item = MagicMock(return_value="ok")
        with patch.object(PlaySound, "play_sound") as play, \
             patch.object(ex, "_attend_to_item_loss") as attend:
            ex.fetch_after_round_without_begin(7)
        attend.assert_not_called()
        self.assertEqual([c.kwargs.get("kind") for c in play.call_args_list], ["item_lost"])
        self.assertEqual(SharedState.get_equip_freeze_count(), 0, "全窓フリーズは張らない")


# ── ロビーのリスポーンの後 ──────────────────
class TestFetchAfterLobbyRespawn(_ExecutorCase):
    def setUp(self):
        super().setUp()
        self.w = _World(self)
        self.ex, self.st = self._executor(auto_begin=False)
        self.st.in_round = False
        self.st.begin_done = False
        self.events = []
        ex = self.ex
        ex._respawn_when_free = MagicMock(side_effect=AssertionError("もうリスポーンしている。押さない"))
        ex._take_pos_run = MagicMock()
        ex._face_the_stairs = lambda seq: self.events.append("face") or 0
        ex._fix_depth = lambda seq: self.events.append("depth") and None
        self.stayed = []

        def counted(pairs, stop, start, extend, log):
            self.events.append(("back", pairs, self.w.now[0], ex._respawn_lobby, ex._run_respawn_idle.is_set()))
            return True, 0.0, "動き出し", 0
        ex._press_counted_from_start = counted
        ex._osc.press = lambda address, sec, stop=None: self.events.append(
            ("press", address, sec, stop() if stop else None))
        ex._osc.stop_all = lambda repeat=1: self.events.append("release")
        ex._fetch_item = lambda *a, **k: self.events.append(("fetch", a, k, ex._run_respawn_idle.is_set())) or "ok"
        ex.announce_item_lost_once = lambda: self.events.append("sound")
        ex._attend_to_item_loss = lambda: self.events.append("notice")
        patch.object(ActionExecutor, "MotionSampler", MagicMock()).start()
        self.start = self.w.now[0]

    def test_the_order_back_turn_face_then_the_fetch(self):
        self.ex.fetch_after_lobby_respawn(7)
        kinds = [e if isinstance(e, str) else e[0] for e in self.events]
        self.assertEqual(kinds, ["back", "release", "press", "release", "face", "depth", "sound", "fetch"])
        back = self.events[0]
        self.assertEqual(back[1], [("/input/MoveBackward", config.RUN_RESPAWN_BACK_SEC)])
        self.assertGreaterEqual(back[2] - self.start, config.RUN_BACK_DELAY_SEC, "「リスポーンの後」の待ちをしてから下がる")
        self.assertTrue(back[3], "ロビーの判定（_lobby_still）で続ける")
        self.assertFalse(back[4], "終わるまで _run_respawn_idle は下ろしたまま")
        self.assertEqual(self.events[2][1:3], ("/input/LookLeft", config.RUN_RESPAWN_TURN_SEC))
        self.assertEqual(self.events[2][3], False, "続けてよい（stop は偽）")
        fetch = self.events[-1]
        self.assertEqual(fetch[1:3], ((7, "Survival", 29), {"route": "spawn"}))
        self.assertTrue(fetch[3], "取りに行くときは _run_respawn_idle を戻してある")
        self.ex._respawn_when_free.assert_not_called()
        self.ex._take_pos_run.assert_not_called()
        self.assertFalse(self.ex._respawn_lobby)
        self.assertIn("正面を向きました", " ".join(self.logs))

    def test_it_does_not_press_the_respawn_or_borrow_the_front(self):
        with patch.object(WindowOperator, "borrow_front") as borrow, \
             patch.object(WindowOperator, "send_keys") as keys:
            self.ex.fetch_after_lobby_respawn(7)
        borrow.assert_not_called()
        keys.assert_not_called()
        self.ex._respawn_when_free.assert_not_called()

    def test_after_punished_it_backs_longer(self):
        self.st.round_type = config.LATE_ROUND[0] if hasattr(config.LATE_ROUND, "__getitem__") else next(iter(config.LATE_ROUND))
        self.ex.fetch_after_lobby_respawn(7)
        self.assertEqual(self.events[0][1], [("/input/MoveBackward", config.RUN_RESPAWN_BACK_SEC_LATER)])

    def test_a_moved_position_turns_once_more(self):
        self.ex._fix_depth = lambda seq: self.events.append("depth") or "遠い → 前へ 0.3 秒"
        self.ex.fetch_after_lobby_respawn(7)
        self.assertEqual([e for e in self.events if e == "face"], ["face", "face"])

    def test_the_stop_conditions_end_it_quietly(self):
        for label, change in (("停止", lambda: self.running.update(on=False)),
                              ("次のラウンド", lambda: setattr(self.st, "round_seq", 8)),
                              ("ラウンド開始", lambda: setattr(self.st, "in_round", True)),
                              ("private でない", lambda: setattr(self.st, "instance_type", config.INSTANCE_PUBLIC))):
            with self.subTest(label):
                self.setUp_again()
                change()
                self.ex.fetch_after_lobby_respawn(7)
                self.assertEqual([e for e in self.events if isinstance(e, tuple) and e[0] in ("back", "press", "fetch")], [])
                self.assertNotIn("notice", self.events)
                self.assertNotIn("sound", self.events)
                self.assertTrue(self.ex._run_respawn_idle.is_set())

    def setUp_again(self):
        self.tearDown_state()
        self.running["on"] = True
        self.st.round_seq, self.st.in_round, self.st.instance_type = 7, False, config.INSTANCE_PRIVATE
        self.events.clear()

    def tearDown_state(self):
        self.ex._respawn_lobby = False
        self.ex._run_respawn_idle.set()

    def test_a_stop_while_backing_does_not_go(self):
        def back(pairs, stop, start, extend, log):
            self.running["on"] = False
            return False, 0.0, "", 0
        self.ex._press_counted_from_start = back
        self.ex.fetch_after_lobby_respawn(7)
        self.assertNotIn("sound", self.events)
        self.assertFalse([e for e in self.events if isinstance(e, tuple) and e[0] == "fetch"])
        self.assertNotIn("notice", self.events, "止めたときは案内しない")

    def test_not_facing_gives_the_notice_and_does_not_walk(self):
        """向けなかった（Begin が通った）→ 歩かずに今の案内"""
        self.st.begin_done = True
        self.ex.fetch_after_lobby_respawn(7)
        self.assertEqual(self.events, ["notice"])
        self.assertTrue(any("歩きません" in m for m in self.logs), self.logs)
        self.assertTrue(self.ex._run_respawn_idle.is_set())

    def test_the_notice_is_the_callback_when_one_is_given(self):
        self.st.begin_done = True
        given = []
        self.ex.fetch_after_lobby_respawn(7, lambda: given.append(1))
        self.assertEqual((given, self.events), ([1], []))

    def test_when_it_can_no_longer_go_after_facing_the_notice_comes(self):
        self.ex._fix_depth = lambda seq: SharedState.set_item_fetch(False)
        self.ex.fetch_after_lobby_respawn(7)
        self.assertEqual(self.events[-1], "notice")
        self.assertNotIn("sound", self.events)

    def test_the_idle_flag_comes_back_even_if_it_breaks(self):
        self.ex._face_the_stairs = MagicMock(side_effect=RuntimeError("x"))
        with self.assertRaises(RuntimeError):
            self.ex.fetch_after_lobby_respawn(7)
        self.assertTrue(self.ex._run_respawn_idle.is_set())
        self.assertFalse(self.ex._respawn_lobby)


class TestRespawnAndFaceIsTheSame(_ExecutorCase):
    """_respawn_and_face は、リスポーンの後に _after_respawn_and_face へ渡すだけ（動きは変えていない）"""

    def test_the_respawn_comes_first_and_the_idle_flag_is_held_around_it(self):
        ex, st = self._executor(auto_begin=False)
        order = []
        ex._respawn_when_free = lambda seq: order.append(("respawn", ex._run_respawn_idle.is_set())) or None
        ex._after_respawn_and_face = lambda seq, label: order.append(("after", seq, label, ex._run_respawn_idle.is_set())) or True
        self.assertTrue(ex._respawn_and_face(7, "Run"))
        self.assertEqual(order, [("respawn", False), ("after", 7, "Run", False)])
        self.assertTrue(ex._run_respawn_idle.is_set())

    def test_a_failed_respawn_does_not_move(self):
        ex, st = self._executor(auto_begin=False)
        ex._respawn_when_free = lambda seq: "リスポーンの行が来ない"
        ex._after_respawn_and_face = MagicMock()
        self.assertFalse(ex._respawn_and_face(7, "Run"))
        ex._after_respawn_and_face.assert_not_called()
        self.assertIn("Run: リスポーンできませんでした（リスポーンの行が来ない）", self.logs)
        self.assertTrue(ex._run_respawn_idle.is_set())


# ── LogMonitor ──────────────────────────
class _SpawnMonitorCase(_MonitorCase):
    def setUp(self):
        super().setUp()
        SharedState.set_item_fetch(True)
        patch.object(ItemFetch, "available", return_value=True).start()

    def _monitor(self, auto_begin=False, in_round=False, item=29, skipped=False, itype=config.INSTANCE_PRIVATE):
        monitor = super()._monitor(auto_begin=auto_begin, in_round=in_round, item=item)
        monitor._action._osc = MagicMock()
        monitor._action._receiver = MagicMock()
        monitor.st.instance_type = itype
        if skipped:
            monitor.st.begin_skipped_seq = monitor.st.round_seq
        return monitor

    def _booked(self, monitor):
        return [n for n, _a in monitor.booked]


class TestOnRoundOver(_SpawnMonitorCase):
    def test_the_fetch_starts_instead_of_the_notice(self):
        for label, kwargs in (("自動Begin OFF", dict(auto_begin=False)),
                              ("続行の後は押さない回", dict(auto_begin=True, skipped=True))):
            with self.subTest(label):
                SharedState.equip_freeze_reset()
                self.play.reset_mock()
                self.borrow.reset_mock()
                monitor = self._monitor(in_round=True, **kwargs)
                monitor._process(RESPAWN)
                monitor._process(ROUND_OVER)
                st = monitor.st
                self.assertEqual(self._count(monitor, SPAWN_ROUND_OVER), 1, monitor.logs)
                self.assertEqual(self._count(monitor, ROUND_OVER_LOST), 0)
                self.assertEqual(self._announced(), (0, 0), "案内（前面化・音声）はしない")
                self.assertFalse(st.equip_freeze_held, "全窓フリーズは張らない")
                self.assertTrue(st.waiting_for_equip)
                fetches = [(n, a) for n, a in monitor.booked if n == "fetch_after_round_without_begin"]
                self.assertEqual(fetches, [("fetch_after_round_without_begin", (7,))])
                self.assertNotIn("do_after_round", self._booked(monitor))

    def test_the_notice_stays_when_it_cannot_go(self):
        cases = (("設定 OFF", lambda: SharedState.set_item_fetch(False), config.INSTANCE_PRIVATE),
                 ("public", lambda: None, config.INSTANCE_PUBLIC),
                 ("干し芋", lambda: None, config.INSTANCE_HOSHIIMO),
                 ("焼き芋", lambda: None, config.INSTANCE_YAKIIMO))
        for label, setup, itype in cases:
            with self.subTest(label):
                SharedState.equip_freeze_reset()
                SharedState.set_item_fetch(True)
                self.play.reset_mock()
                self.borrow.reset_mock()
                setup()
                monitor = self._monitor(in_round=True, itype=itype)
                monitor._process(RESPAWN)
                monitor._process(ROUND_OVER)
                self.assertEqual(self._count(monitor, ROUND_OVER_LOST), 1, monitor.logs)
                self.assertEqual(self._count(monitor, SPAWN_ROUND_OVER), 0)
                self.assertEqual(self._announced(), (1, 1), "今までの案内")
                self.assertNotIn("fetch_after_round_without_begin", self._booked(monitor))

    def test_item_begin_mode_hands_free_and_no_receiver_keep_the_notice(self):
        for label in ("モード", "放置", "受信なし"):
            with self.subTest(label):
                SharedState.equip_freeze_reset()
                SharedState.set_item_begin_mode(False)
                SharedState.set_hands_free(False)
                self.play.reset_mock()
                self.borrow.reset_mock()
                monitor = self._monitor(in_round=True, auto_begin=False)
                if label == "モード":
                    SharedState.set_item_begin_mode(True)
                elif label == "放置":
                    SharedState.set_hands_free(True)
                else:
                    monitor._action._receiver = None
                monitor._process(RESPAWN)
                monitor._process(ROUND_OVER)
                self.assertNotIn("fetch_after_round_without_begin", self._booked(monitor))
                self.assertEqual(self._count(monitor, SPAWN_ROUND_OVER), 0)

    def test_a_window_where_the_tool_presses_begin_is_as_before(self):
        monitor = self._monitor(in_round=True, auto_begin=True)
        monitor._process(RESPAWN)
        monitor._process(ROUND_OVER)
        self.assertIn("do_after_round", self._booked(monitor))
        self.assertNotIn("fetch_after_round_without_begin", self._booked(monitor))
        self.assertEqual(self._count(monitor, SPAWN_ROUND_OVER), 0)

    def test_the_respawn_notice_given_in_the_lobby_is_not_given_again_nor_fetched(self):
        monitor = self._monitor(in_round=True, auto_begin=False)
        monitor.st.respawn_notice_seq = monitor.st.round_seq
        monitor._process(RESPAWN)
        monitor._process(ROUND_OVER)
        self.assertNotIn("fetch_after_round_without_begin", self._booked(monitor))
        self.assertEqual(self._count(monitor, "リスポーンで外れたアイテムは案内済み"), 1, monitor.logs)


class TestOnVerifiedEnd(_SpawnMonitorCase):
    def test_the_lines_say_the_shop_not_begin(self):
        for label, kwargs in (("自動Begin OFF", dict(auto_begin=False)),
                              ("続行の後は押さない回", dict(auto_begin=True, skipped=True))):
            with self.subTest(label):
                monitor = self._monitor(in_round=True, **kwargs)
                monitor._process(RESPAWN)
                monitor._process(ROUND_OVER)
                monitor._process(VERIFIED_END)
                self.assertEqual(self._count(monitor, SPAWN_VERIFIED), 1, monitor.logs)
                self.assertEqual(self._count(monitor, "Begin が通ったら"), 0, monitor.logs)

    def test_the_uncollected_line(self):
        monitor = self._monitor(in_round=True, auto_begin=False, item=0)
        monitor.st.last_lost_item_id = 29
        monitor._process(ROUND_OVER)
        monitor._process(VERIFIED_END)
        self.assertEqual(self._count(monitor, SPAWN_VERIFIED_UNCOLLECTED), 1, monitor.logs)

    def test_the_old_lines_stay_when_it_cannot_go(self):
        SharedState.set_item_fetch(False)
        monitor = self._monitor(in_round=True, auto_begin=False)
        monitor._process(RESPAWN)
        monitor._process(ROUND_OVER)
        monitor._process(VERIFIED_END)
        self.assertEqual(self._count(monitor, OLD_VERIFIED), 1, monitor.logs)
        self.assertEqual(self._count(monitor, SPAWN_VERIFIED), 0)

    def test_a_window_where_the_tool_presses_begin_keeps_its_line(self):
        monitor = self._monitor(in_round=True, auto_begin=True)
        monitor._process(RESPAWN)
        monitor._process(ROUND_OVER)
        monitor._process(VERIFIED_END)
        self.assertEqual(self._count(monitor, "ラウンド終了 【⚠ アイテムロスト → Begin が通ったら取りに行く】"), 1, monitor.logs)
        self.assertEqual(self._count(monitor, SPAWN_VERIFIED), 0)


class TestOnOptedIn(_SpawnMonitorCase):
    def test_the_fetch_starts_instead_of_the_notice(self):
        for label, kwargs in (("自動Begin OFF", dict(auto_begin=False)),
                              ("続行の後は押さない回", dict(auto_begin=True, skipped=True))):
            with self.subTest(label):
                SharedState.equip_freeze_reset()
                self.play.reset_mock()
                self.borrow.reset_mock()
                monitor = self._monitor(**kwargs)
                monitor._process(RESPAWN)
                monitor._process(OPTED_IN)
                st = monitor.st
                self.assertEqual(self._announced(), (0, 0), "案内（前面化・音声）はしない")
                self.assertEqual(self._count(monitor, NOTICE), 0)
                self.assertEqual(self._count(monitor, SPAWN_LOBBY), 1, monitor.logs)
                self.assertTrue(st.waiting_for_equip)
                self.assertFalse(st.respawn_item_pending)
                self.assertFalse(st.equip_freeze_held)
                self.assertEqual(st.respawn_notice_seq, -1)
                self.assertEqual(st.last_lost_item_id, 29)
                started = [(n, a) for n, a in monitor.booked if n == "fetch_after_lobby_respawn"]
                self.assertEqual(len(started), 1, monitor.booked)
                self.assertEqual(started[0][1][0], 7)

    def test_the_callback_gives_todays_notice(self):
        monitor = self._monitor(auto_begin=False)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        callback = [a for n, a in monitor.booked if n == "fetch_after_lobby_respawn"][0][1]
        callback()
        st = monitor.st
        self.assertEqual(st.respawn_notice_seq, st.round_seq)
        self.assertTrue(st.equip_freeze_held)
        self.assertEqual(self._announced(), (1, 1))
        self.assertEqual(self._count(monitor, NOTICE), 1)

    def test_the_voice_flag_is_cleared_so_the_sound_can_play(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_lost_announced = True
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        self.assertFalse(monitor.st.item_lost_announced)

    def test_the_notice_stays_when_it_cannot_go(self):
        for label in ("設定 OFF", "public", "モード", "受信なし"):
            with self.subTest(label):
                SharedState.equip_freeze_reset()
                SharedState.set_item_fetch(True)
                SharedState.set_item_begin_mode(False)
                self.play.reset_mock()
                self.borrow.reset_mock()
                kwargs = {}
                if label == "設定 OFF":
                    SharedState.set_item_fetch(False)
                elif label == "public":
                    kwargs["itype"] = config.INSTANCE_PUBLIC
                elif label == "モード":
                    SharedState.set_item_begin_mode(True)
                monitor = self._monitor(auto_begin=False, **kwargs)
                if label == "受信なし":
                    monitor._action._receiver = None
                monitor._process(RESPAWN)
                monitor._process(OPTED_IN)
                self.assertEqual(self._count(monitor, NOTICE), 1, monitor.logs)
                self.assertEqual(self._announced(), (1, 1))
                self.assertNotIn("fetch_after_lobby_respawn", self._booked(monitor))

    def test_a_window_where_the_tool_presses_begin_is_as_before(self):
        monitor = self._monitor(auto_begin=True)
        monitor._action.item_fetch_target = lambda route="begin": ("Survival", 29) if route == "begin" else None
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (0, 0), "Begin の後に取りに行く窓は案内しない（今まで）")
        self.assertNotIn("fetch_after_lobby_respawn", self._booked(monitor))
        self.assertFalse(monitor.st.waiting_for_equip)

    def test_a_skipped_round_that_cannot_go_by_spawn_is_silent_as_before(self):
        monitor = self._monitor(auto_begin=True, skipped=True)
        monitor._action._receiver = None
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        self.assertEqual(self._announced(), (0, 0))
        self.assertNotIn("fetch_after_lobby_respawn", self._booked(monitor))

    def test_hands_free_is_as_before(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(auto_begin=False)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        self.assertNotIn("fetch_after_lobby_respawn", self._booked(monitor))
        self.assertEqual(self._announced(), (0, 0))

    def test_a_second_opted_in_does_not_start_it_twice(self):
        monitor = self._monitor(auto_begin=False)
        monitor._process(RESPAWN)
        monitor._process(OPTED_IN)
        monitor._process(OPTED_IN)
        self.assertEqual(self._booked(monitor).count("fetch_after_lobby_respawn"), 1)

    def test_an_opted_in_after_the_round_start_waits_for_the_round_over(self):
        monitor = self._monitor(auto_begin=False)
        monitor._process(RESPAWN)
        monitor._process("2026.11.19 12:00:00 Debug      -  This round is taking place at Sewers (12) and the round type is Classic")
        monitor._process(OPTED_IN)
        self.assertNotIn("fetch_after_lobby_respawn", self._booked(monitor))


if __name__ == "__main__":
    unittest.main()
