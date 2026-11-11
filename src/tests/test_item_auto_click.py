"""アイテムの自動クリック（段階 2 B）: 手に持った Bloody Kat Charm（表で間隔が決まっているアイテム）を、窓ごとの設定が ON のとき
ラウンド中だけ OSC の /input/UseRight で押す。private 相当か、公開範囲がグループのメンバーのみのインスタンスで送る。
偽の時計・偽のソケット・偽の _start_daemon だけを使う（本物の OSC・入力・窓・スレッドは使わない）"""
import types

from tests.support import *  # noqa: F401,F403
import tests.test_gui as _gui                       # 設定を読む偽の App の土台を借りる
import tests.test_ui_tidy as _ui_tidy               # 本物の App を立てる土台を借りる

CHARM = "BloodKatCharm"
PRIVATE, PUBLIC = config.INSTANCE_PRIVATE, config.INSTANCE_PUBLIC
OTHER_GROUP = config.INSTANCE_OTHER_GROUP
ACCESS = LogParser
ROUND_LINE = "2026.11.11 09:20:00 Debug      -  This round is taking place at Facility (12) and the round type is Classic"
ROUND_OVER = "2026.11.11 09:25:00 Debug      -  RoundOver"
PICK = ("2026.11.11 09:20:38 Debug      -  [Behaviour] Pickup object: 'BloodKatCharm' equipped = True, "
        "is AutoEquipType Pickup = True, last input method = Mouse, is AutoHold is enabled for this controller type")
DROP = ("2026.11.11 09:20:38 Debug      -  [Behaviour] Drop object: 'BloodKatCharm, was equipped = True' "
        "Throw on release, last input method = Mouse")
JOIN_GROUP = ("2026.11.11 09:21:00 Debug      -  [Behaviour] Joining wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd:12345"
              "~group(grp_11111111-1111-1111-1111-111111111111)~groupAccessType({})~region(jp)")
JOIN_PRIVATE = ("2026.11.11 09:21:00 Debug      -  [Behaviour] Joining wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd:12345"
                "~private(usr_00000000-0000-0000-0000-000000000000)~region(jp)")
DIED = "2026.11.11 09:22:00 Debug      -  You died."
START_LINE = "[アイテム] 自動クリック"


class _FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(bytes(data))
        return len(data)

    def close(self):
        pass


class _Clock:
    """偽の時計。monotonic・time が同じ値を返し、sleep は進める"""

    def __init__(self):
        self.now = 0.0

    def module(self):
        fake = type(sys)("clock_for_test")
        fake.monotonic = fake.time = lambda: self.now
        fake.sleep = self.advance
        return fake

    def advance(self, sec):
        self.now += max(0.0, sec)


class _Wake:
    """_auto_click_wake の偽物。待つ = 偽の時計を進めてから、決めた時刻の出来事（state の書き換え）を起こす"""

    def __init__(self, clock, events=()):
        self.clock = clock
        self.waits = []
        self.events = sorted(events, key=lambda e: e[0])

    def wait(self, sec):
        self.waits.append((self.clock.now, sec))
        self.clock.advance(sec)
        while self.events and self.events[0][0] <= self.clock.now:
            self.events.pop(0)[1]()
        return False

    def clear(self):
        pass

    def set(self):
        pass


class _Case(unittest.TestCase):
    """動作中の 1 窓の ActionExecutor（偽の時計・偽のソケット）"""

    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        self.clock = _Clock()
        self.running = [True]

    def _executor(self, instance=PRIVATE, access=ACCESS.ACCESS_INVITE, auto=True, osc=True, held=CHARM,
                  in_round=True):
        st = WindowState()
        st.instance_type, st.instance_access = instance, access
        st.in_round, st.round_seq, st.instance_seq = in_round, 1, 1
        st.window_idx = 1
        st.item_use = ItemUse.UseTracker(1, clock=lambda: self.clock.now)
        if held:
            st.item_use.pickup(held)
        cfg = WindowConfig(hwnd=0x10, osc_port=9000 if osc else 0, item_auto_click=auto)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: self.running[0], lambda _m: None)
        if ex._osc is not None:
            ex._osc._sock = _FakeSocket()               # 本物のソケットには送らない
            self.addCleanup(ex._osc.close)
        self.st, self.cfg, self.ex = st, cfg, ex
        return ex

    def _use_lines(self):
        return [line for line in self.st.item_use.take_debug_lines() if line.startswith("使用")]


def _debug_lines(written):
    return [c.args[0] for c in written.call_args_list if START_LINE in c.args[0]]


# ── 送ってよいインスタンスの表 ──────────────────────────
class TestAllowedInTable(unittest.TestCase):
    def test_send(self):
        a = ACCESS
        cases = [
            (PRIVATE, a.ACCESS_INVITE), (PRIVATE, a.ACCESS_INVITE_PLUS), (PRIVATE, a.ACCESS_FRIENDS),
            (PRIVATE, a.ACCESS_FRIENDS_PLUS),
            (config.INSTANCE_SEER, a.ACCESS_GROUP_MEMBERS),
            (config.INSTANCE_HOSHIIMO, a.ACCESS_GROUP_MEMBERS), (config.INSTANCE_YAKIIMO, a.ACCESS_GROUP_MEMBERS),
            (config.INSTANCE_ACHIEVEMENT, a.ACCESS_GROUP_MEMBERS),
            (config.INSTANCE_EMERALD_CITY, a.ACCESS_GROUP_MEMBERS),
            (OTHER_GROUP, a.ACCESS_GROUP_MEMBERS),
        ]
        for instance, access in cases:
            with self.subTest(instance=instance, access=access):
                self.assertTrue(ActionExecutor.item_auto_click_allowed_in(instance, access))

    def test_do_not_send(self):
        a = ACCESS
        cases = [
            (OTHER_GROUP, a.ACCESS_GROUP_PLUS), (OTHER_GROUP, a.ACCESS_GROUP_PUBLIC),
            (config.INSTANCE_HOSHIIMO, a.ACCESS_GROUP_PLUS), (config.INSTANCE_YAKIIMO, a.ACCESS_GROUP_PUBLIC),
            (config.INSTANCE_ACHIEVEMENT, a.ACCESS_GROUP_PLUS), (config.INSTANCE_EMERALD_CITY, a.ACCESS_GROUP_PLUS),
            (PUBLIC, a.ACCESS_PUBLIC), (OTHER_GROUP, a.ACCESS_UNKNOWN), (PUBLIC, a.ACCESS_UNKNOWN),
            ("", ""), ("", a.ACCESS_GROUP_PLUS),
        ]
        for instance, access in cases:
            with self.subTest(instance=instance, access=access):
                self.assertFalse(ActionExecutor.item_auto_click_allowed_in(instance, access))

    def test_a_narrower_list_of_group_types(self):
        with patch.object(ActionExecutor, "ITEM_AUTO_CLICK_GROUP_TYPES", frozenset({config.INSTANCE_HOSHIIMO})):
            self.assertTrue(ActionExecutor.item_auto_click_allowed_in(config.INSTANCE_HOSHIIMO,
                                                                      ACCESS.ACCESS_GROUP_MEMBERS))
            self.assertFalse(ActionExecutor.item_auto_click_allowed_in(config.INSTANCE_EMERALD_CITY,
                                                                       ACCESS.ACCESS_GROUP_MEMBERS))
            self.assertFalse(ActionExecutor.item_auto_click_allowed_in(OTHER_GROUP, ACCESS.ACCESS_GROUP_MEMBERS))
            self.assertTrue(ActionExecutor.item_auto_click_allowed_in(PRIVATE, ACCESS.ACCESS_INVITE),
                            "private 相当は、グループの絞り込みに関係なく送る")
        self.assertIsNone(ActionExecutor.ITEM_AUTO_CLICK_GROUP_TYPES, "既定はどのグループでも")

    def test_the_executor_reads_the_state_of_its_window(self):
        st = WindowState()
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x10), st, lambda: True, lambda _m: None)
        for instance, access, want in ((OTHER_GROUP, ACCESS.ACCESS_GROUP_MEMBERS, True),
                                       (OTHER_GROUP, ACCESS.ACCESS_GROUP_PLUS, False),
                                       (PRIVATE, "", True), (PUBLIC, ACCESS.ACCESS_PUBLIC, False)):
            st.instance_type, st.instance_access = instance, access
            self.assertEqual(ex.item_auto_click_allowed(), want, (instance, access))


class TestOtherOperationsStayPrivateOnly(_Case):
    def test_can_operate_is_unchanged_for_group_members(self):
        for instance in (OTHER_GROUP, config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO,
                         config.INSTANCE_EMERALD_CITY, config.INSTANCE_ACHIEVEMENT):
            self.assertFalse(ActionExecutor.can_operate_in(instance), instance)
        for instance in (PRIVATE, config.INSTANCE_SEER):
            self.assertTrue(ActionExecutor.can_operate_in(instance), instance)

    def test_move_sends_nothing_in_a_group_members_instance(self):
        ex = self._executor(instance=OTHER_GROUP, access=ACCESS.ACCESS_GROUP_MEMBERS)
        self.assertTrue(ex.item_auto_click_allowed())
        self.assertFalse(ex.can_operate())
        self.assertFalse(ex.move("forward", 0.1))
        self.assertEqual(ex._osc._sock.sent, [])


# ── 次の一手（時計も待ちも持たない判定）──────────────────
class TestStep(_Case):
    def _step(self, last=None, now=0.0, round_seq=1, instance_seq=1):
        return self.ex._auto_click_step(round_seq, instance_seq, last, now)

    def test_press_when_on_holding_the_target_in_a_round(self):
        self._executor()
        self.assertEqual(self._step(), ("press", 0.0))

    def test_waits_for_the_interval_after_a_press(self):
        self._executor()
        self.assertEqual(self._step(last=0.0, now=0.5), ("wait", 1.5))
        self.assertEqual(self._step(last=0.0, now=2.0), ("press", 0.0))

    def test_waits_for_the_ct_after_a_hand_click(self):
        self._executor()
        self.st.item_use.use(ItemUse.SOURCE_HAND)               # 時刻 0 に手で使った（CT 1.0）
        kind, sec = self._step(last=None, now=0.3)
        self.assertEqual(kind, "wait")
        self.assertAlmostEqual(sec, 0.7)
        self.assertEqual(self._step(last=None, now=1.0), ("press", 0.0))

    def test_waits_for_whichever_is_later_the_interval_or_the_ct(self):
        row = ItemUse.UseItem(ItemUse.KIND_CLICK_CD, "AutoTest", "Auto Test", 3.0, 0.0, 0.0, auto_click_sec=0.5)
        with patch.object(ItemUse, "TABLE", (row,)):
            self._executor(held="AutoTest")
            self.st.item_use.use(ItemUse.SOURCE_HAND)
            kind, sec = self._step(last=0.0, now=0.6)
            self.assertEqual(kind, "wait")
            self.assertAlmostEqual(sec, 2.4)                    # 間隔はもう過ぎているが CT が残っている
            kind, sec = self._step(last=2.9, now=3.0)
            self.assertEqual(kind, "wait")
            self.assertAlmostEqual(sec, 0.4)                    # CT は明けたが、間隔が残っている

    def test_nothing_to_do_when_off(self):
        self._executor(auto=False)
        self.assertEqual(self._step(last=0.0), ("idle", config.ITEM_AUTO_CLICK_IDLE_SEC))

    def test_nothing_to_do_when_not_holding(self):
        self._executor(held=None)
        self.assertEqual(self._step(), ("idle", config.ITEM_AUTO_CLICK_IDLE_SEC))

    def test_nothing_to_do_when_holding_a_weapon(self):
        self._executor(held="Taser")
        self.assertEqual(self._step(), ("idle", config.ITEM_AUTO_CLICK_IDLE_SEC))
        self.assertIsNone(self.ex._auto_click_target())

    def test_nothing_to_do_without_osc(self):
        self._executor(osc=False)
        self.assertFalse(self.ex.uses_osc)
        self.assertEqual(self._step(), ("idle", config.ITEM_AUTO_CLICK_IDLE_SEC))

    def test_nothing_to_do_when_the_instance_is_not_allowed(self):
        for instance, access in ((PUBLIC, ACCESS.ACCESS_PUBLIC), (OTHER_GROUP, ACCESS.ACCESS_GROUP_PLUS),
                                 (OTHER_GROUP, ACCESS.ACCESS_GROUP_PUBLIC), ("", "")):
            self._executor(instance=instance, access=access)
            self.assertEqual(self._step(), ("idle", config.ITEM_AUTO_CLICK_IDLE_SEC), (instance, access))

    def test_it_presses_in_a_group_members_instance(self):
        self._executor(instance=OTHER_GROUP, access=ACCESS.ACCESS_GROUP_MEMBERS)
        self.assertEqual(self._step(), ("press", 0.0))

    def test_it_ends_when_the_round_is_over(self):
        self._executor(in_round=False)
        self.assertEqual(self._step(), ("end", 0.0))

    def test_it_ends_on_death_and_respawn(self):
        self._executor()
        self.st.died_this_round = True                          # 死亡・ラウンド中のリスポーンはどちらもこの印
        self.assertEqual(self._step(), ("end", 0.0))

    def test_it_ends_on_stop(self):
        self._executor()
        self.running[0] = False
        self.assertEqual(self._step(), ("end", 0.0))

    def test_it_ends_when_the_instance_changes(self):
        self._executor()
        self.st.instance_seq += 1
        self.assertEqual(self._step(), ("end", 0.0))

    def test_it_ends_when_another_round_has_started(self):
        self._executor()
        self.st.round_seq += 1
        self.assertEqual(self._step(), ("end", 0.0))

    def test_the_blocked_note_is_for_a_target_in_a_disallowed_instance_only(self):
        self._executor(instance=PUBLIC, access=ACCESS.ACCESS_PUBLIC)
        self.assertTrue(self.ex._auto_click_blocked_by_instance())
        self._executor(instance=PUBLIC, access=ACCESS.ACCESS_PUBLIC, held="Taser")
        self.assertFalse(self.ex._auto_click_blocked_by_instance())
        self._executor(instance=PUBLIC, access=ACCESS.ACCESS_PUBLIC, held=None)
        self.assertFalse(self.ex._auto_click_blocked_by_instance())
        self._executor(instance=PUBLIC, access=ACCESS.ACCESS_PUBLIC, auto=False)
        self.assertFalse(self.ex._auto_click_blocked_by_instance())
        self._executor()
        self.assertFalse(self.ex._auto_click_blocked_by_instance())


# ── ラウンドごとの見張りの本体（偽の時計で回す）────────────
class TestLoop(_Case):
    def _run(self, events=(), **kwargs):
        """do_item_auto_click を偽の時計で回す。押した時刻と、押すときに渡された stop を控える。戻りは (押した時刻, 書いた行)"""
        ex = self._executor(**kwargs)
        ex._auto_click_wake = _Wake(self.clock, events)
        self.presses, self.stops = [], []

        def press(address, hold, stop=None):
            self.presses.append((self.clock.now, address, hold))
            self.stops.append(stop)
            self.clock.advance(hold)
            return True
        ex._osc and setattr(ex._osc, "press", press)
        with patch.object(ActionExecutor, "time", self.clock.module()), \
             patch.object(DebugLog, "write") as written:
            REAL_ITEM_AUTO_CLICK(ex, 1)
        return [p[0] for p in self.presses], _debug_lines(written)

    def _end_at(self, when):
        return (when, lambda: setattr(self.st, "in_round", False))

    def test_it_presses_every_interval_during_the_round(self):
        times, lines = self._run(events=[self._end_at(5.5)])
        self.assertEqual(len(times), 3)
        for got, want in zip(times, (0.0, 2.0, 4.0)):
            self.assertAlmostEqual(got, want, delta=0.01)
        self.assertEqual([p[1:] for p in self.presses], [(ItemUse.USE_ADDRESS, config.ITEM_AUTO_CLICK_PULSE_SEC)] * 3)

    def test_it_writes_only_a_start_line_and_an_end_line(self):
        _times, lines = self._run(events=[self._end_at(5.5)])
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("[窓1] [アイテム] 自動クリック", lines[0])
        self.assertIn("終わりました", lines[1])
        self.assertNotIn("usr_", "".join(lines))

    def test_the_first_press_waits_for_the_ct_of_a_hand_click(self):
        ex = self._executor()
        self.st.item_use.use(ItemUse.SOURCE_HAND)
        ex._auto_click_wake = _Wake(self.clock, [self._end_at(3.5)])
        self.presses = []

        def press(address, hold, stop=None):
            self.presses.append(self.clock.now)
            self.clock.advance(hold)
            return True
        ex._osc.press = press
        with patch.object(ActionExecutor, "time", self.clock.module()), patch.object(DebugLog, "write"):
            REAL_ITEM_AUTO_CLICK(ex, 1)
        self.assertAlmostEqual(self.presses[0], 1.0, delta=0.01)        # 手で使った CT（1.0 秒）が明けてから

    def test_turning_it_off_waits_and_turning_it_on_again_resumes(self):
        off = (2.5, lambda: setattr(self.cfg, "item_auto_click", False))
        on = (5.5, lambda: setattr(self.cfg, "item_auto_click", True))
        times, lines = self._run(events=[off, on, self._end_at(9.0)])
        self.assertEqual(len(times), 4, times)
        self.assertAlmostEqual(times[0], 0.0, delta=0.01)
        self.assertAlmostEqual(times[1], 2.0, delta=0.01)
        self.assertTrue(all(t >= 5.5 for t in times[2:]), "OFF の間は押さない")
        self.assertEqual(len(lines), 2, "OFF にしてもスレッドは残る（開始と終わりの 2 行だけ）")

    def test_dropping_the_item_waits_and_picking_it_up_again_resumes(self):
        drop = (2.5, lambda: self.st.item_use.drop(CHARM))
        pick = (5.5, lambda: self.st.item_use.pickup(CHARM))
        times, _lines = self._run(events=[drop, pick, self._end_at(8.0)])
        self.assertEqual(len(times), 3, times)
        self.assertTrue(times[2] >= 5.5)

    def test_it_ends_on_each_of_the_round_ending_events(self):
        for name, end in (("round over", lambda: setattr(self.st, "in_round", False)),
                          ("death or respawn", lambda: setattr(self.st, "died_this_round", True)),
                          ("stop", lambda: self.running.__setitem__(0, False)),
                          ("instance change", lambda: setattr(self.st, "instance_seq", self.st.instance_seq + 1))):
            with self.subTest(end=name):
                self.running[0] = True
                self.clock.now = 0.0
                times, lines = self._run(events=[(0.5, end)])
                self.assertEqual(len(times), 1, "最初の 1 回だけ押して、そこで終わる")
                self.assertEqual(len(lines), 2, lines)

    def test_a_press_is_stopped_at_once_when_the_round_ends_or_the_item_is_gone(self):
        self._run(events=[self._end_at(1.0)])
        stop = self.stops[0]
        self.assertTrue(callable(stop))
        # 押している最中を想定して、その時点の状態を作り直して見る
        self.st.in_round = True
        self.assertFalse(stop())
        self.st.died_this_round = True
        self.assertTrue(stop())
        self.st.died_this_round = False
        self.cfg.item_auto_click = False
        self.assertTrue(stop())
        self.cfg.item_auto_click = True
        self.assertFalse(stop())
        self.st.item_use.drop(CHARM)
        self.assertTrue(stop())
        self.st.item_use.pickup(CHARM)
        self.running[0] = False
        self.assertTrue(stop())

    def test_no_press_in_a_disallowed_instance_and_one_note_per_round(self):
        times, lines = self._run(instance=PUBLIC, access=ACCESS.ACCESS_PUBLIC, events=[self._end_at(4.5)])
        self.assertEqual(times, [])
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("このインスタンスでは送りません（種類・公開範囲）", lines[1])
        self.assertEqual(sum("このインスタンスでは" in line for line in lines), 1)

    def test_a_group_members_instance_presses_and_group_plus_does_not(self):
        times, _ = self._run(instance=OTHER_GROUP, access=ACCESS.ACCESS_GROUP_MEMBERS, events=[self._end_at(3.5)])
        self.assertEqual(len(times), 2)
        self.clock.now = 0.0
        times, _ = self._run(instance=OTHER_GROUP, access=ACCESS.ACCESS_GROUP_PLUS, events=[self._end_at(3.5)])
        self.assertEqual(times, [])

    def test_no_note_when_not_holding_a_target_or_off(self):
        for kwargs in ({"held": "Taser"}, {"held": None}, {"auto": False}):
            with self.subTest(kwargs=kwargs):
                self.clock.now = 0.0
                times, lines = self._run(instance=PUBLIC, access=ACCESS.ACCESS_PUBLIC,
                                         events=[self._end_at(2.5)], **kwargs)
                self.assertEqual(times, [])
                self.assertEqual(len(lines), 2, lines)

    def test_a_weapon_is_never_pressed_and_a_window_without_osc_does_not_fail(self):
        times, _ = self._run(held="Taser", events=[self._end_at(3.5)])
        self.assertEqual(times, [])
        self.clock.now = 0.0
        ex = self._executor(osc=False)
        ex._auto_click_wake = _Wake(self.clock, [self._end_at(3.5)])
        with patch.object(ActionExecutor, "time", self.clock.module()), patch.object(DebugLog, "write"):
            REAL_ITEM_AUTO_CLICK(ex, 1)

    def test_it_does_not_look_at_the_freeze_or_the_idle_mode(self):
        with patch.object(SharedState, "get_hands_free", return_value=True):
            times, _ = self._run(events=[self._end_at(1.5)])
        self.assertEqual(len(times), 1)

    def test_the_wait_is_never_shorter_than_a_millisecond(self):
        self._run(events=[self._end_at(3.0)])
        self.assertTrue(all(sec >= 0.001 for _t, sec in self.ex._auto_click_wake.waits))


# ── 本物の OSCClient（偽のソケット）で、押す・離す ─────────────
class TestRealOscClient(_Case):
    def _decoded(self, ex):
        return [bytes(m) for m in ex._osc._sock.sent]

    def _msg(self, value):
        return OSCClient.OSCClient.build_message(ItemUse.USE_ADDRESS, value)

    def test_a_press_counts_as_one_use_through_on_send(self):
        ex = self._executor()
        with patch.object(OSCClient, "time", self.clock.module()):
            ex._osc.press(ItemUse.USE_ADDRESS, config.ITEM_AUTO_CLICK_PULSE_SEC)
        lines = self._use_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("（OSC・", lines[0])
        msgs = self._decoded(ex)
        self.assertEqual(msgs[0], self._msg(0))
        self.assertEqual(msgs[1], self._msg(1))
        self.assertEqual(msgs[-1], self._msg(0))

    def test_a_press_is_released_at_once_when_stopped(self):
        ex = self._executor()
        with patch.object(OSCClient, "time", self.clock.module()):
            ex._osc.press(ItemUse.USE_ADDRESS, 5.0, stop=lambda: True)
        self.assertEqual(self._decoded(ex), [self._msg(0), self._msg(1), self._msg(0)], "0・1・すぐ 0")
        self.assertEqual(self.clock.now, 0.0, "待たずに離す")
        self.assertEqual(len(self._use_lines()), 1)

    def test_the_loop_sends_real_messages_once_per_interval(self):
        ex = self._executor()
        ex._auto_click_wake = _Wake(self.clock, [(2.5, lambda: setattr(self.st, "in_round", False))])
        with patch.object(ActionExecutor, "time", self.clock.module()), \
             patch.object(OSCClient, "time", self.clock.module()), patch.object(DebugLog, "write"):
            REAL_ITEM_AUTO_CLICK(ex, 1)
        msgs = self._decoded(ex)
        self.assertEqual(msgs.count(self._msg(0)), 4, "押す前の 0 と離す 0 を 2 回分")
        self.assertEqual(len(self._use_lines()), 2, "0 秒と 2 秒に 1 回ずつ使用に数える")
        self.assertEqual(msgs[-1], self._msg(0), "押したままにしない")


# ── 連打（Begin）との重なり ───────────────────────────
class TestNeverTogetherWithTheBeginSpam(_Case):
    def test_the_spam_does_not_run_in_a_round_and_the_auto_click_does_not_run_outside_one(self):
        ex = self._executor(in_round=True)
        stop = threading.Event()
        with patch.object(ActionExecutor, "time", self.clock.module()):
            ex._spam_use_right_loop(stop, 1)                    # ラウンド中は、すぐ返る（押さない）
        self.assertEqual(ex._osc._sock.sent, [])
        self.assertEqual(ex._auto_click_step(1, 1, None, 0.0), ("press", 0.0))
        self.st.in_round = False
        self.assertEqual(ex._auto_click_step(1, 1, None, 0.0), ("end", 0.0))


# ── LogMonitor（偽の _start_daemon）──────────────────────
class _MonitorCase(unittest.TestCase):
    def setUp(self):
        ItemUse.reset_for_tests()
        self.addCleanup(ItemUse.reset_for_tests)
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)

    def _monitor(self, auto=True, osc=True, instance=PRIVATE, running=True):
        logs = []
        cfg = WindowConfig(hwnd=0x41, osc_port=9000 if osc else 0, item_auto_click=auto)
        monitor = LogMonitor.LogMonitor(cfg, {}, logs.append, window_idx=1)
        monitor.st.instance_type = instance
        monitor.st.instance_access = ACCESS.ACCESS_INVITE
        monitor._running = running
        monitor.targets = []
        monitor._start_daemon = lambda target, *a: monitor.targets.append((getattr(target, "__name__", ""), a, target))
        self.addCleanup(monitor._action._osc.close if monitor._action._osc else (lambda: None))
        return monitor

    def _process(self, monitor, line):
        with patch.object(ConnectDB, "register_round"), patch.object(PlaySound, "play_sound"), \
             patch.object(SharedState, "continue_round_start"), patch.object(DebugLog, "write"):
            monitor._process(line)

    @staticmethod
    def _loops(monitor):
        """立てた自動クリックの見張り [("do_item_auto_click", (round_seq,))]（support が本体を差し替えているので、束縛メソッドで見分ける）"""
        return [("do_item_auto_click", t[1]) for t in monitor.targets if t[2] == monitor._action.do_item_auto_click]


class TestLogMonitorStartsTheLoop(_MonitorCase):
    def test_a_round_start_with_it_on_starts_one_loop(self):
        monitor = self._monitor()
        self._process(monitor, ROUND_LINE)
        self.assertEqual(self._loops(monitor), [("do_item_auto_click", (monitor.st.round_seq,))])
        monitor._ensure_item_auto_click()
        monitor.on_item_auto_click_changed()
        self.assertEqual(len(self._loops(monitor)), 1, "同じラウンドで二重に立てない")

    def test_it_is_off_by_default_and_starts_nothing(self):
        off = self._monitor(auto=False)
        self._process(off, ROUND_LINE)
        self.assertEqual(self._loops(off), [])
        self.assertFalse(WindowConfig().item_auto_click)
        on = self._monitor()
        self._process(on, ROUND_LINE)
        names_on = [t[0] for t in on.targets if t[2] != on._action.do_item_auto_click]
        names_off = [t[0] for t in off.targets]
        self.assertEqual(names_on, names_off, "ほかの _start_daemon の呼び出しは今までのまま")

    def test_turning_it_on_in_the_middle_of_a_round_starts_it_once(self):
        monitor = self._monitor(auto=False)
        self._process(monitor, ROUND_LINE)
        monitor.cfg.item_auto_click = True
        monitor.on_item_auto_click_changed()
        monitor.on_item_auto_click_changed()
        self.assertEqual(self._loops(monitor), [("do_item_auto_click", (monitor.st.round_seq,))])

    def test_each_round_gets_its_own_loop(self):
        monitor = self._monitor()
        self._process(monitor, ROUND_LINE)
        self._process(monitor, ROUND_OVER)
        self._process(monitor, ROUND_LINE)
        self.assertEqual([a for _n, a in self._loops(monitor)], [(1,), (2,)])

    def test_not_started_after_death(self):
        monitor = self._monitor(auto=False)
        self._process(monitor, ROUND_LINE)
        self._process(monitor, DIED)
        monitor.cfg.item_auto_click = True
        monitor.on_item_auto_click_changed()
        self.assertEqual(self._loops(monitor), [])

    def test_not_started_without_osc_or_outside_a_round_or_when_stopped(self):
        for kwargs in ({"osc": False}, {"running": False}):
            with self.subTest(kwargs=kwargs):
                monitor = self._monitor(**kwargs)
                self._process(monitor, ROUND_LINE)
                monitor.on_item_auto_click_changed()
                self.assertEqual(self._loops(monitor), [])
        monitor = self._monitor()
        monitor.on_item_auto_click_changed()                    # ラウンドの外
        self.assertEqual(self._loops(monitor), [])
        self._process(monitor, ROUND_LINE)
        self._process(monitor, ROUND_OVER)
        monitor.on_item_auto_click_changed()
        self.assertEqual(len(self._loops(monitor)), 1, "終わったラウンドでは立て直さない")

    def test_the_instance_access_is_read_after_joining(self):
        monitor = self._monitor(instance=PUBLIC)
        self._process(monitor, JOIN_GROUP.format("members"))
        self.assertEqual(monitor.st.instance_access, ACCESS.ACCESS_GROUP_MEMBERS)
        self.assertEqual(monitor.st.instance_type, OTHER_GROUP)
        monitor.st.item_use.pickup(CHARM)
        self.assertTrue(monitor._action.item_auto_click_allowed())
        self._process(monitor, JOIN_GROUP.format("plus"))
        monitor.st.item_use.pickup(CHARM)
        self.assertEqual(monitor.st.instance_access, ACCESS.ACCESS_GROUP_PLUS)
        self.assertFalse(monitor._action.item_auto_click_allowed())
        self._process(monitor, JOIN_PRIVATE)
        self.assertTrue(monitor._action.item_auto_click_allowed())


class TestLogMonitorKicks(_MonitorCase):
    def _kicked(self, monitor, act):
        wake = monitor._action._auto_click_wake
        wake.clear()
        act()
        return wake.is_set()

    def test_kicks_on_each_event_that_changes_the_state(self):
        monitor = self._monitor()
        self._process(monitor, ROUND_LINE)
        for name, act in (
                ("pickup", lambda: self._process(monitor, PICK)),
                ("drop", lambda: self._process(monitor, DROP)),
                ("lose held item", lambda: monitor._lose_held_item("テスト")),
                ("mark item lost", lambda: monitor._mark_item_lost("テスト: アイテムロスト")),
                ("death", lambda: self._process(monitor, DIED)),
                ("round over", lambda: self._process(monitor, ROUND_OVER)),
                ("joining", lambda: self._process(monitor, JOIN_PRIVATE)),
                ("mark out of round", lambda: monitor._mark_out_of_round("リスポーン"))):
            with self.subTest(event=name):
                self.assertTrue(self._kicked(monitor, act))

    def test_kicks_on_stop(self):
        monitor = self._monitor()
        patches = (patch.object(monitor._action, "start_velocity_receiver"),
                   patch.object(monitor._action, "stop_velocity_receiver"))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        monitor.start()
        self.assertTrue(self._kicked(monitor, monitor.stop))

    def test_changing_the_setting_kicks(self):
        monitor = self._monitor()
        self.assertTrue(self._kicked(monitor, monitor.on_item_auto_click_changed))

    def test_the_executor_kick_sets_the_event(self):
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x10), WindowState(), lambda: True, lambda _m: None)
        self.assertFalse(ex._auto_click_wake.is_set())
        ex.kick_item_auto_click()
        self.assertTrue(ex._auto_click_wake.is_set())


# ── 画面（本物の App。設定の保存・読み込み・Ctrl で全窓へ）───────────
def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


class TestGui(_ui_tidy._AppCase):
    def setUp(self):
        super().setUp()
        p = patch.object(config, "GUI_OVERLAY_ITEM_REFRESH_MS", 600000)    # 窓の上の表示が勝手に回らないように
        p.start()
        self.addCleanup(p.stop)
        self._tabs(4)
        self.addCleanup(self._clean)
        self.ctrl = patch.object(mainGUI, "ctrl_held", return_value=False).start()
        self.addCleanup(patch.stopall)

    def _tabs(self, count):
        if len(self.app.tabs) != count:
            self.app.v_win_count.set(count)
            self.app._rebuild_tabs(count)
        self.app._window_item_auto_click = {}
        for tab in self.app.tabs:
            tab.set_item_auto_click(False)

    def _clean(self):
        self.app._running = False
        self.app.monitors = []
        self.app._window_item_auto_click = {}

    def _save(self):
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        return stored

    def _check(self, tab):
        found = [w for w in _descendants(tab) if isinstance(w, ttk.Checkbutton)
                 and w.cget("text") == "アイテム自動クリック"]
        self.assertEqual(len(found), 1)
        return found[0]

    def test_the_check_is_off_by_default_and_in_the_live_settings(self):
        for tab in self.app.tabs:
            self.assertFalse(tab.v_item_auto_click.get())
            self.assertIs(tab.live_settings()["item_auto_click"], False)
            self._check(tab)
        tab = self.app.tabs[1]
        tab.v_item_auto_click.set(True)
        self.assertIs(tab.live_settings()["item_auto_click"], True)
        self.assertTrue(WindowConfig(**tab.live_settings()).item_auto_click)

    def test_the_check_sits_in_its_own_row_right_under_the_item_switch_row(self):
        tab = self.app.tabs[0]
        slaves = tab.pack_slaves()
        switch_row = slaves[[i for i, w in enumerate(slaves) if tab.chk_ct_switch in w.winfo_children()][0]]
        after = slaves[slaves.index(switch_row) + 1]
        self.assertEqual([w.cget("text") for w in after.winfo_children()], ["アイテム自動クリック"])

    def test_ctrl_click_sets_every_window(self):
        self.ctrl.return_value = True
        self._check(self.app.tabs[0]).invoke()
        self.assertTrue(all(t.v_item_auto_click.get() for t in self.app.tabs))
        self.assertTrue(any("「アイテム自動クリック」を ON にして全窓に反映しました" in m for m in self.app.logs))
        self._check(self.app.tabs[2]).invoke()
        self.assertFalse(any(t.v_item_auto_click.get() for t in self.app.tabs))

    def test_without_ctrl_only_that_window_changes(self):
        self._check(self.app.tabs[1]).invoke()
        self.assertEqual([t.v_item_auto_click.get() for t in self.app.tabs], [False, True, False, False])

    def test_the_label_for_the_broadcast_log(self):
        self.assertEqual(mainGUI.BROADCAST_CHECK_LABELS["item_auto_click"], "アイテム自動クリック")

    def test_it_is_saved_per_window_and_a_hand_change_schedules_a_save(self):
        tab = self.app.tabs[2]
        self.app._schedule_settings_save.reset_mock()
        tab.v_item_auto_click.set(True)
        self.app._schedule_settings_save.assert_called()
        stored = self._save()
        self.assertEqual(stored["item_auto_click"], {"1": False, "2": False, "3": True, "4": False})

    def test_loading_into_the_tabs_does_not_schedule_a_save(self):
        self.app._schedule_settings_save.reset_mock()
        self.app.tabs[0].set_item_auto_click(True)
        self.assertTrue(self.app.tabs[0].v_item_auto_click.get())
        self.app._schedule_settings_save.assert_not_called()

    def test_it_is_restored_after_the_tabs_are_rebuilt_and_kept_for_hidden_windows(self):
        self.app.tabs[0].v_item_auto_click.set(True)
        self.app.tabs[2].v_item_auto_click.set(True)
        self.app._rebuild_tabs(2)
        self.assertEqual([t.v_item_auto_click.get() for t in self.app.tabs], [True, False])
        self.assertEqual(self._save()["item_auto_click"]["3"], True, "消した窓の分も残る")
        self.app._rebuild_tabs(4)
        self.assertEqual([t.v_item_auto_click.get() for t in self.app.tabs], [True, False, True, False])

    def test_a_change_while_running_reaches_the_monitor_and_kicks(self):
        cfg = WindowConfig()
        monitor = MagicMock()
        monitor.window_idx = 2
        monitor.cfg = cfg
        self.app.monitors = [monitor]
        self.app._running = True
        self.app.tabs[1].v_item_auto_click.set(True)
        self.assertTrue(cfg.item_auto_click)
        monitor.on_item_auto_click_changed.assert_called()
        self.app.tabs[1].v_item_auto_click.set(False)
        self.assertFalse(cfg.item_auto_click)

    def test_it_does_not_reach_a_monitor_while_stopped(self):
        monitor = MagicMock()
        monitor.window_idx = 1
        monitor.cfg = WindowConfig()
        self.app.monitors = [monitor]
        self.app._running = False
        self.app.tabs[0].v_item_auto_click.set(True)
        self.assertFalse(monitor.cfg.item_auto_click)
        monitor.on_item_auto_click_changed.assert_not_called()

    def _start_with(self, osc_in):
        tab = self.app.tabs[0]
        tab.osc_in, tab.osc_out = osc_in, osc_in + 1 if osc_in else 0
        tab.set_item_auto_click(True)
        cfg = WindowConfig(hwnd=0x1234, log_path=Path("output_log_test.txt"), item_auto_click=True)
        patches = [patch.object(tab, "get_config", return_value=(cfg, None)),
                   patch.object(LogMonitor, "LogMonitor"),
                   patch.object(SharedState, "register_window_hwnd"),
                   patch.object(self.app, "_resolve_tab_ports"), patch.object(self.app, "_apply_obs_settings"),
                   patch.object(self.app, "_start_window_volume"), patch.object(self.app, "_debug_start"),
                   patch.object(self.app, "_refresh_continue_choices"), patch.object(self.app, "_start_item_overlays")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for other in self.app.tabs[1:]:
            p = patch.object(other, "get_config", return_value=(None, "ログファイルが未設定です"))
            p.start()
            self.addCleanup(p.stop)
        self.app.monitors = []
        self.app._start()
        return [m for m in self.app.logs if "アイテム自動クリック" in m]

    def test_start_logs_once_when_it_is_on_without_osc(self):
        lines = self._start_with(0)
        self.assertEqual(lines, ["[窓1] アイテム自動クリック: OSC利用不可のため送りません"])

    def test_start_does_not_log_when_osc_is_available(self):
        self.assertEqual(self._start_with(9000), [])


class TestSavedFlags(unittest.TestCase):
    def test_only_a_true_value_of_a_valid_window_is_on(self):
        got = mainGUI._as_window_flags({"1": True, "2": "true", "3": 1, "4": None, "5": False, "x": True,
                                        "0": True, str(config.MAX_WINDOWS + 1): True, 6: True, "-1": True})
        self.assertEqual(got, {1: True, 2: False, 3: False, 4: False, 5: False})

    def test_a_broken_whole_value_is_empty(self):
        for value in (None, [], "1", 5, True, [("1", True)]):
            self.assertEqual(mainGUI._as_window_flags(value), {}, repr(value))

    def test_loading_from_settings_keeps_only_valid_flags(self):
        inst = _gui.TestWindowCountIsRemembered("test_a_valid_value_is_read")
        app = inst._load({"item_auto_click": {"1": True, "2": "yes", "3": 1, "9999": True}})
        self.assertEqual(app._window_item_auto_click, {1: True, 2: False, 3: False})
        app = inst._load({"item_auto_click": "broken"})
        self.assertEqual(app._window_item_auto_click, {})
        app = inst._load({})
        self.assertEqual(app._window_item_auto_click, {})

    def test_the_default_of_the_config_field_is_off(self):
        self.assertIs(WindowConfig().item_auto_click, False)
