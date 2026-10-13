"""Run のラウンドに入ったら、リスポーン → 後ろへ → 正面を向く（DL）"""
from tests.support import *  # noqa: F401,F403
import RespawnButton


HWND = 0x40
BUTTON = (578.0, 1033.0)            # 撮影の中のボタン（実機の窓2）
ORIGIN = (100, 50)                  # 窓の左上


class _World:
    """偽の前面・キー・マウス・カーソル・時計・撮影。起きたことは events に順に残す"""

    def __init__(self, test, ex, st):
        self.test, self.ex, self.st = test, ex, st
        self.events = []
        self.front = 0x900
        self.now = [0.0]
        self.found = (BUTTON[0], BUTTON[1], 0.99, 1.0)
        self.respawns = True            # 押したら「Player respawned」が来る
        self.reads = []                 # カーソルの読み直しで返す位置（空なら置いた点）
        self.placed = None
        self.on_sleep = None
        self.on_find = None
        self.locked = []                # 前面の操作のときに鍵を持っていたか
        w = self

        def borrow(hwnd):
            w.events.append("borrow")
            w.locked.append(SharedState._GLOBAL_ACTION_LOCK.locked())
            w.front = hwnd
            return True, "loan"

        def give_back(loan):
            w.events.append("return")
            w.front = 0x900
            return True

        def keys(k):
            w.events.append(("keys", k))
            w.locked.append(SharedState._GLOBAL_ACTION_LOCK.locked())

        def put(point):
            w.events.append(("cursor", tuple(point)))
            w.placed = tuple(point)
            return True

        def read():
            if w.front != HWND:
                return (11, 22)             # 前面を借りる前（元の位置）
            return w.reads.pop(0) if w.reads else (w.placed or (11, 22))

        def click(sec, pause=False):
            w.events.append(("click", sec))
            w.locked.append(SharedState._GLOBAL_ACTION_LOCK.locked())
            if w.respawns:
                w.st.respawn_seen_seq += 1          # LogMonitor._on_respawn と同じ

        def find(shot):
            w.events.append("find")
            if w.on_find:
                w.on_find()
            return w.found

        def sleep(sec):
            w.now[0] += sec
            if w.on_sleep:
                w.on_sleep()
        fake_time = type(sys)("time_for_test")
        fake_time.time = lambda: w.now[0]
        fake_time.monotonic = lambda: w.now[0]
        fake_time.sleep = sleep
        self.cursor_before = (11, 22)
        for p in (patch.object(WindowOperator, "borrow_front", side_effect=borrow),
                  patch.object(WindowOperator, "return_front", side_effect=give_back),
                  patch.object(WindowOperator, "send_keys", side_effect=keys),
                  patch.object(WindowOperator, "set_cursor_position", side_effect=put),
                  patch.object(WindowOperator, "cursor_position", side_effect=read),
                  patch.object(WindowOperator, "mouse_click", side_effect=click),
                  patch.object(WindowOperator, "window_origin", return_value=ORIGIN),
                  patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: w.front),
                  patch.object(RespawnButton, "find", side_effect=find),
                  patch.object(ex, "_capture_bgr", return_value="shot"),
                  patch.object(ActionExecutor, "time", fake_time),
                  patch.object(DebugLog, "write")):
            p.start()
            test.addCleanup(p.stop)


class _Osc:
    def __init__(self, world):
        self.world = world
        self.on_press = None

    def press(self, address, sec, stop=None):
        self.world.events.append(("osc", address, sec))
        if self.on_press:
            self.on_press(address, stop)
        return True

    def stop_all(self, repeat=1):
        self.world.events.append("release")


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_run_respawn(True)
        self.addCleanup(SharedState.set_run_respawn, False)
        for reset in (SharedState.equip_freeze_reset, SharedState.continue_round_reset,
                      SharedState.speed_freeze_reset, SharedState.round_freeze_reset):
            reset()
            self.addCleanup(reset)
        self.running = {"on": True}
        self.logs = []
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, in_round=True, round_seq=5,
                              round_type="Run", window_idx=2)
        self.ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=HWND, osc_port=9000), self.st,
                                                lambda: self.running["on"], self.logs.append)
        self.w = _World(self, self.ex, self.st)
        self.osc = _Osc(self.w)
        self.ex._osc = self.osc

    def _run(self):
        self.ex.do_run_respawn(self.st.round_seq)

    POINT = (int(BUTTON[0] + ORIGIN[0]), int(BUTTON[1] + ORIGIN[1]))
    WHOLE = ["borrow", ("keys", "esc"), "find", ("cursor", POINT), ("click", 0.15), "return",
             ("cursor", (11, 22)),
             ("osc", "/input/MoveBackward", 2.85), "release", ("osc", "/input/LookLeft", 0.9), "release"]


class TestTheWholeRun(_Case):
    def test_the_order(self):
        self._run()
        self.assertEqual(self.w.events, self.WHOLE)
        self.assertEqual(self.logs, ["Run: リスポーンして正面を向きました"])
        self.assertTrue(all(self.w.locked), "前面の操作は鍵の中")
        self.assertGreaterEqual(self.w.now[0], config.RESPAWN_MENU_WAIT_SEC, "メニューが開くのを待った")

    def test_the_values(self):
        self.assertEqual((config.RUN_RESPAWN_BACK_SEC, config.RUN_RESPAWN_TURN_SEC, config.RESPAWN_MENU_WAIT_SEC,
                          config.RESPAWN_CLICK_SEC, config.RESPAWN_LOG_WAIT_SEC, RespawnButton.MATCH_MIN),
                         (2.85, 0.9, 1.0, 0.15, 2.0, 0.8))

    def test_the_cursor_is_placed_again_when_it_moved(self):
        self.w.reads = [(1280, 719)]
        self._run()
        self.assertEqual(self.w.events.count(("cursor", self.POINT)), 2)
        self.assertIn(("click", 0.15), self.w.events)

    def test_a_cursor_that_cannot_be_placed_does_not_click(self):
        self.w.reads = [(1280, 719)] * 3
        self._run()
        self.assertNotIn(("click", 0.15), self.w.events)
        self.assertEqual(self.w.events[-3:], [("keys", "esc"), "return", ("cursor", (11, 22))])
        self.assertFalse([e for e in self.w.events if e[0] == "osc"])

    def test_no_button_closes_the_menu_and_does_not_move(self):
        self.w.found = None
        self._run()
        self.assertEqual(self.w.events, ["borrow", ("keys", "esc"), "find", "find", "find", ("keys", "esc"),
                                         "return", ("cursor", (11, 22))])
        self.assertEqual(self.logs, ["Run: リスポーンできませんでした（リスポーンのボタンが見つからない）"])

    def test_no_respawn_line_presses_once_more_then_closes_the_menu(self):
        """行が来ない → ボタンがまだ写っているのを確かめて、もう1回だけ押す → それでも来なければ Esc（DQ）。
        行を待つ間は前面を返して鍵を離す（ほかの窓の Begin を止めない）。押し直しとメニューを閉じるのは
        前面を借り直して（メニューは開いたままなので Esc で開き直さない）"""
        self.w.respawns = False
        self._run()
        back = ("cursor", (11, 22))
        self.assertEqual(self.w.events, ["borrow", ("keys", "esc"), "find", ("cursor", self.POINT), ("click", 0.15),
                                         "return", back,
                                         "borrow", "find", ("cursor", self.POINT), ("click", 0.15), "return", back,
                                         "borrow", ("keys", "esc"), "return", back])
        self.assertEqual(self.logs, ["Run: リスポーンできませんでした（リスポーンの行が来ない）"])
        self.assertLess(self.w.now[0], config.RESPAWN_MENU_WAIT_SEC + 2 * config.RESPAWN_LOG_WAIT_SEC + 0.5)
        self.assertTrue(all(self.w.locked), "前面の操作は鍵の中")

    def test_the_line_is_waited_for_without_the_lock(self):
        """押した後の「Player respawned」待ちは鍵の外（ほかの窓が前面の操作をできる）"""
        self.w.respawns = False
        locked_while_waiting = []
        self.w.on_sleep = lambda: (locked_while_waiting.append(SharedState._GLOBAL_ACTION_LOCK.locked())
                                   if ("click", 0.15) in self.w.events and "return" in self.w.events
                                   and self.w.events[-1] == ("cursor", (11, 22)) else None)
        self._run()
        self.assertTrue(locked_while_waiting, "待っている間に眠った")
        self.assertEqual(set(locked_while_waiting), {False})

    def test_the_second_press_goes_through(self):
        """1回目を取りこぼした（重い窓）→ 2回目で行が来る → 続ける"""
        clicks = {"n": 0}
        real = WindowOperator.mouse_click.side_effect

        def click(sec, pause=False):
            clicks["n"] += 1
            self.w.respawns = clicks["n"] >= 2
            real(sec, pause)
        with patch.object(WindowOperator, "mouse_click", side_effect=click):
            self._run()
        self.assertEqual(clicks["n"], 2)
        self.assertEqual(self.w.events.count(("click", 0.15)), 2)
        self.assertIn(("osc", "/input/MoveBackward", 2.85), self.w.events)
        self.assertEqual(self.logs, ["Run: リスポーンして正面を向きました"])

    def test_no_second_press_when_the_button_is_gone(self):
        self.w.respawns = False
        found = iter([(BUTTON[0], BUTTON[1], 0.99, 1.0), None])
        self.w.found = None
        with patch.object(RespawnButton, "find", side_effect=lambda _s: self.w.events.append("find") or next(found)):
            self._run()
        self.assertEqual(self.w.events.count(("click", 0.15)), 1, "押し直さない")
        self.assertEqual(self.logs, ["Run: リスポーンできませんでした（リスポーンの行が来ない）"])

    def test_not_private_no_osc_or_off_does_nothing(self):
        for setup in (lambda: setattr(self.st, "instance_type", config.INSTANCE_PUBLIC),
                      lambda: setattr(self.st, "instance_type", config.INSTANCE_HOSHIIMO),
                      lambda: setattr(self.ex, "_osc", None),
                      lambda: SharedState.set_run_respawn(False)):
            self.st.instance_type = config.INSTANCE_PRIVATE
            self.ex._osc = self.osc
            SharedState.set_run_respawn(True)
            setup()
            self.w.events.clear()
            self.logs.clear()
            self._run()
            self.assertEqual(self.w.events, [])
            self.assertEqual(self.logs, [], "公開ログにも出さない（その窓では使わない機能）")

    def test_a_window_not_in_front_gets_no_esc(self):
        with patch.object(WindowOperator, "borrow_front", side_effect=lambda h: self.w.events.append("borrow")
                          or (True, None)):
            self._run()
        self.assertNotIn(("keys", "esc"), self.w.events)
        self.assertFalse([e for e in self.w.events if e[0] == "osc"])

    def test_stopped_while_backing_lets_go(self):
        def press(address, stop):
            if address == "/input/MoveBackward":
                self.running["on"] = False
                self.assertTrue(stop(), "押している最中に止まる")
        self.osc.on_press = press
        self._run()
        self.assertEqual(self.w.events[-2:], [("osc", "/input/MoveBackward", 2.85), "release"])
        self.assertNotIn(("osc", "/input/LookLeft", 0.9), self.w.events)

    def test_the_next_round_while_backing_lets_go(self):
        def press(address, stop):
            if address == "/input/MoveBackward":
                self.st.round_seq += 1
                self.assertTrue(stop())
        self.osc.on_press = press
        self._run()
        self.assertNotIn(("osc", "/input/LookLeft", 0.9), self.w.events)
        self.assertEqual(self.w.events[-1], "release")
        self.assertEqual(self.logs, [])

    def test_stopped_while_the_menu_is_open_closes_it(self):
        self.w.on_find = lambda: self.running.update(on=False)
        self._run()
        self.assertNotIn(("click", 0.15), self.w.events)
        self.assertEqual(self.w.events[-3:], [("keys", "esc"), "return", ("cursor", (11, 22))])


class TestTheFreezes(_Case):
    """前面を出すので、ほかの窓のフリーズの間は借りない（依頼者「しっかりフリーズ管理」）"""

    def test_it_waits_for_another_windows_freeze_then_does_it(self):
        other = WindowState()
        SharedState.continue_round_start(other)
        waits = []

        def sleep():
            waits.append(list(self.w.events))
            if len(waits) == 5:
                SharedState.continue_round_end(other)
        self.w.on_sleep = sleep
        self._run()
        self.assertTrue(all(w == [] for w in waits[:5]), "フリーズの間は前面を借りない")
        self.assertEqual(self.w.events, self.WHOLE)

    def test_every_freeze_kind_is_waited_for(self):
        for start, end in ((SharedState.equip_freeze_start, SharedState.equip_freeze_end),
                           (SharedState.speed_freeze_start, SharedState.speed_freeze_end),
                           (SharedState.round_freeze_start, SharedState.round_freeze_end)):
            other = WindowState()
            start(other)
            self.w.events.clear()
            self.w.on_sleep = lambda: end(other) if self.w.now[0] > 1 else None
            self._run()
            self.assertEqual(self.w.events[0], "borrow")
            self.assertGreater(self.w.now[0], 1)

    def test_if_run_ends_while_frozen_it_does_nothing(self):
        SharedState.continue_round_start(WindowState())
        self.w.on_sleep = lambda: setattr(self.st, "in_round", False) if self.w.now[0] > 3 else None
        self._run()
        self.assertEqual(self.w.events, [])
        self.assertEqual(self.logs, ["Run: リスポーンできませんでした（ほかの窓のフリーズ中に Run が終わった）"])

    def test_its_own_round_freeze_is_not_waited_for(self):
        SharedState.round_freeze_start(self.st)         # Run の突入フリーズ（自分）
        self._run()
        self.assertEqual(self.w.events, self.WHOLE)
        self.assertLess(self.w.now[0], config.RESPAWN_MENU_WAIT_SEC + config.RUN_BACK_DELAY_SEC + 0.5)

    def test_a_continue_round_while_in_front_gives_it_back_then_does_it_again(self):
        other = WindowState()
        state = {"found": 0}

        def on_find():
            state["found"] += 1
            if state["found"] == 1:
                SharedState.continue_round_start(other)     # 別の窓が続行ラウンドに入った
        self.w.on_find = on_find
        self.w.on_sleep = lambda: (SharedState.continue_round_end(other)
                                   if "return" in self.w.events and self.w.now[0] > 5 else None)
        self._run()
        first = self.w.events.index("return")
        self.assertEqual(self.w.events[:first + 2], ["borrow", ("keys", "esc"), "find", ("cursor", self.POINT),
                                                     ("keys", "esc"), "return", ("cursor", (11, 22))])
        self.assertNotIn(("click", 0.15), self.w.events[:first])
        self.assertEqual(self.w.events[first + 2:], self.WHOLE, "解けたらやり直す")
        self.assertTrue(all(self.w.locked))

    def test_a_freeze_while_waiting_for_the_line(self):
        other = WindowState()
        self.w.respawns = False
        clicked = {"n": 0}

        def on_sleep():
            if ("click", 0.15) in self.w.events and not clicked["n"]:
                clicked["n"] = 1
                SharedState.continue_round_start(other)
            elif clicked["n"] and "return" in self.w.events:
                SharedState.continue_round_end(other)
                self.w.respawns = True
        self.w.on_sleep = on_sleep
        self._run()
        first = self.w.events.index("return")
        self.assertEqual(self.w.events[first - 1], ("click", 0.15), "押したらすぐ前面を返す（行は鍵の外で待つ）")
        self.assertEqual(self.w.events.count(("keys", "esc")), 1,
                         "フリーズが解けてから押し直すとき、開いたままのメニューに Esc を送らない")
        self.assertEqual(self.w.events[first + 1:first + 7],
                         [("cursor", (11, 22)), "borrow", "find", ("cursor", self.POINT), ("click", 0.15), "return"],
                         "解けたら前面を借り直して押し直す")
        self.assertEqual(self.w.events[-4:], [("osc", "/input/MoveBackward", 2.85), "release",
                                              ("osc", "/input/LookLeft", 0.9), "release"])

    def test_a_freeze_while_waiting_for_the_lock_does_not_borrow(self):
        """鍵を待つ間にほかの窓がフリーズを張った → 鍵を取れても前面を借りずに、解けるのを待つ"""
        other = WindowState()
        SharedState._GLOBAL_ACTION_LOCK.acquire()
        done = threading.Event()
        t = threading.Thread(target=lambda: (self._run(), done.set()), daemon=True)
        t.start()
        time.sleep(0.05)                                # 鍵を待っている
        SharedState.continue_round_start(other)
        self.w.on_sleep = lambda: SharedState.continue_round_end(other) if self.w.now[0] > 2 else None
        SharedState._GLOBAL_ACTION_LOCK.release()
        self.assertTrue(done.wait(2.0))
        self.assertEqual(self.w.events, self.WHOLE, "一度も前面を借りずに待ってから")

    def test_the_front_work_waits_for_the_lock(self):
        """ほかの窓が前面の操作（Begin のクリックなど）をしている間は、鍵が空くまで待つ"""
        SharedState._GLOBAL_ACTION_LOCK.acquire()
        done = threading.Event()
        t = threading.Thread(target=lambda: (self._run(), done.set()), daemon=True)
        t.start()
        time.sleep(0.05)
        self.assertEqual(self.w.events, [], "鍵が空くまで前面を借りない")
        SharedState._GLOBAL_ACTION_LOCK.release()
        self.assertTrue(done.wait(2.0))
        self.assertEqual(self.w.events, self.WHOLE)


class TestNoClashWithTheItemLoss(_Case):
    """リスポーンで付くアイテムロストから動き出すこの窓の操作（Begin 前の移動・アイテム取得・
    押し直し）は、後ろへ・正面への移動が終わるまで待つ"""

    def test_after_round_waits_until_the_respawn_is_done(self):
        started = threading.Event()
        release = threading.Event()

        def press(address, stop):
            if address == "/input/MoveBackward":
                started.set()
                release.wait(2.0)
        self.osc.on_press = press
        runner = threading.Thread(target=self._run, daemon=True)
        runner.start()
        self.assertTrue(started.wait(2.0))
        after = []
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(self.ex, "_begin_precheck", side_effect=lambda **k: after.append("begin") and False):
            self.st.in_round = False
            t = threading.Thread(target=self.ex.do_after_round, daemon=True)
            t.start()
            time.sleep(0.1)
            self.assertEqual(after, [], "移動の間は Begin へ進まない")
            release.set()
            runner.join(2.0)
            t.join(2.0)
        self.assertEqual(after, ["begin"])

    def test_begin_again_waits_too(self):
        self.ex._run_respawn_idle.clear()
        calls = []
        with patch.object(self.ex, "_begin_precheck", side_effect=lambda **k: calls.append(1) and False):
            t = threading.Thread(target=self.ex.do_begin_again, args=(self.st.round_seq,), daemon=True)
            self.st.in_round = False
            t.start()
            time.sleep(0.1)
            self.assertEqual(calls, [])
            self.ex._run_respawn_idle.set()
            t.join(2.0)
        self.assertEqual(calls, [1])

    def test_a_stop_while_waiting_ends_it(self):
        self.ex._run_respawn_idle.clear()
        self.running["on"] = False
        self.assertFalse(self.ex._wait_run_respawn())

    def test_the_item_loss_itself_is_unchanged(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=1), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.item_id = 29
        monitor._process("2026.10.10 13:03:24 Debug      -  Player respawned, opted out!")
        self.assertEqual(monitor.st.respawn_seen_seq, 1)
        self.assertEqual(monitor.st.item_id, 0, "リスポーン: アイテムロスト（今どおり）")
        self.assertTrue(monitor.st.item_lost_this_round)


class TestTheTrigger(unittest.TestCase):
    LINE = "2026.10.10 13:03:20 Debug      -  This round is taking place at Facility (12) and the round type is {}"

    def setUp(self):
        SharedState.set_run_respawn(True)
        self.addCleanup(SharedState.set_run_respawn, False)

    def _started(self, round_type):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=HWND, osc_port=9000), {}, lambda _m: None,
                                        window_idx=2)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        targets = []
        monitor._start_daemon = lambda target, *a: targets.append((getattr(target, "__name__", ""), a))
        with patch.object(ConnectDB, "register_round"):
            monitor._process(self.LINE.format(round_type))
        return [t for t in targets if t[0] == "do_run_respawn"], monitor

    def test_run_starts_it_once(self):
        started, monitor = self._started("Run")
        self.assertEqual(started, [("do_run_respawn", (monitor.st.round_seq,))])

    def test_other_rounds_do_not(self):
        for round_type in ("Classic", "Punished", "8 Pages", "Midnight"):
            self.assertEqual(self._started(round_type)[0], [], round_type)

    def test_off_does_not(self):
        SharedState.set_run_respawn(False)
        self.assertEqual(self._started("Run")[0], [])


class TestTheButtonPicture(unittest.TestCase):
    """倍率を振って絵で探す（窓の大きさが違っても見つかる）"""

    def _scene(self, scale, at=(600, 1000), size=(1294, 1399), seed=1):
        import cv2
        import numpy as np
        rng = np.random.default_rng(seed)
        img = (rng.random((size[1], size[0], 3)) * 60).astype(np.uint8)
        t = cv2.imdecode(np.fromfile(str(config.resource_path(RespawnButton.TEMPLATE_FILE)), np.uint8),
                         cv2.IMREAD_COLOR)
        t = cv2.resize(t, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        th, tw = t.shape[:2]
        x0, y0 = int(at[0] - tw / 2), int(at[1] - th / 2)
        img[y0:y0 + th, x0:x0 + tw] = t
        return img, (x0 + tw / 2, y0 + th / 2)

    def test_the_picture_is_in_assets_and_built_in(self):
        self.assertTrue(Path(config.resource_path(RespawnButton.TEMPLATE_FILE)).is_file())
        self.assertTrue((Path(RespawnButton.__file__).parent.parent / RespawnButton.TEMPLATE_FILE).is_file(),
                        "assets の中（EK）")
        self.assertTrue(RespawnButton.available())
        build = _load_build_script()
        self.assertIn("--include-data-dir=assets/templates=assets/templates", build.BASE_ARGS)

    def test_found_at_other_window_sizes(self):
        for scale in (1.0, 0.6, 1.5):
            img, center = self._scene(scale)
            found = RespawnButton.find(img)
            self.assertIsNotNone(found, scale)
            self.assertAlmostEqual(found[0], center[0], delta=3, msg=scale)
            self.assertAlmostEqual(found[1], center[1], delta=3, msg=scale)
            self.assertAlmostEqual(found[3], scale, delta=0.06, msg=scale)
            self.assertGreaterEqual(found[2], RespawnButton.MATCH_MIN)

    def test_a_full_screen_is_searched_at_a_smaller_size_but_answers_in_its_own(self):
        """全画面（2560×1440）は高さ 720 に縮めて探す（1 回 7.8 秒 → 1.9 秒）。返す座標・倍率は元の撮影のもの"""
        img, center = self._scene(1440 / 1399, at=(1800, 1200), size=(2560, 1440))
        found = RespawnButton.find(img)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found[0], center[0], delta=4)
        self.assertAlmostEqual(found[1], center[1], delta=4)
        self.assertAlmostEqual(found[3], 1440 / 1399, delta=0.06, msg="倍率は元の撮影に対するもの")
        self.assertGreaterEqual(found[2], RespawnButton.MATCH_GOOD)
        self.assertEqual(RespawnButton.WORK_HEIGHT, 720)

    def test_no_button_no_match(self):
        import numpy as np
        img = (np.random.default_rng(2).random((1399, 1294, 3)) * 60).astype(np.uint8)
        self.assertIsNone(RespawnButton.find(img))
        self.assertIsNone(RespawnButton.find(None))

    def test_the_scales_start_at_the_window_it_was_cut_from(self):
        s = RespawnButton.scales()
        self.assertEqual(s[0], 1.0)
        self.assertEqual((min(s), max(s)), (0.5, 2.0))


class TestTheSetting(unittest.TestCase):
    def setUp(self):
        self.addCleanup(SharedState.set_run_respawn, False)

    def test_on_by_default_saved_and_restored(self):
        src = Path(SharedState.__file__).read_text(encoding="utf-8")
        self.assertIn("_RUN_RESPAWN = _Setting(True, bool)", src)
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn("self.v_run_respawn = tk.BooleanVar(value=True)", gui)
        self.assertIn('text="Runリスポーン"', gui)          # DV3: 見出しを無くして短い名前
        readme = (Path(mainGUI.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("Run のラウンドに入ったら、リスポーンして", readme)

    def test_load(self):
        for data, expected in (({}, True), ({"run_respawn": "x"}, True), ({"run_respawn": False}, False)):
            SharedState.set_run_respawn(not expected)
            app = _with_cancel_key(type("FakeApp", (), {})())
            app.tabs = []
            for name in ("v_desktop_mode", "v_use_osc", "v_ton_entry", "v_ton_begin", "v_join_world",
                         "v_ton_access", "v_instance_link", "v_emergency_key", "v_start_key",
                         "v_freeze_8pages", "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                         "v_obs_port", "v_obs_password"):
                setattr(app, name, MagicMock())
            app.v_freeze_rounds = {}
            for name in ("_add_tool_row", "_refresh_emergency_key_label", "_refresh_start_key_label",
                         "_apply_freeze_settings", "_apply_obs_settings", "_apply_saved_window_settings",
                         "_load_tnl", "_log", "_load_window_volume_settings", "_load_fog_early_read_setting",
                         "_load_launch_options_setting"):
                setattr(app, name, lambda *a, **k: None)
            with patch.object(mainGUI, "load_settings", return_value=dict(data)), \
                 patch.object(mainGUI, "save_settings"):
                mainGUI.App._load_saved_settings(app)
            self.assertEqual(SharedState.get_run_respawn(), expected, data)

    def test_the_check_changes_it(self):
        app = type("FakeApp", (), {})()
        app.v_run_respawn = MagicMock(get=lambda: False)
        app._schedule_settings_save = MagicMock()
        SharedState.set_run_respawn(True)
        mainGUI.App._on_run_respawn_changed(app)
        self.assertFalse(SharedState.get_run_respawn())
        app._schedule_settings_save.assert_called_once()


class TestRoundOverRightAfterTheRespawn(_Case):
    """ToN はリスポーン（opt out）でその人のラウンドを終わらせる（同じ秒に RoundOver）。
    リスポーンした後は in_round を見ずに、後ろへ・左へ・向きの直しを最後まで続ける（DP。01:35 の窓4）"""

    def _round_over_while_backing(self):
        seen = []

        def press(address, stop):
            if address == "/input/MoveBackward":
                self.st.in_round = False                    # 04.988 round_over
                seen.append(stop())
        self.osc.on_press = press
        return seen

    def test_window_4_at_0135_goes_on_to_the_end(self):
        seen = self._round_over_while_backing()
        with patch.object(DebugLog, "write") as write:
            self._run()
        self.assertEqual(seen, [False], "RoundOver では止めない")
        self.assertEqual(self.w.events[-4:], [("osc", "/input/MoveBackward", 2.85), "release",
                                              ("osc", "/input/LookLeft", 0.9), "release"])
        self.assertEqual(self.logs, ["Run: リスポーンして正面を向きました"])
        notes = [c.args[0] for c in write.call_args_list if "RoundOver が来た" in c.args[0]]
        self.assertEqual(notes, ["[操作] [窓2] Run: リスポーンの後に RoundOver が来た → 後ろへ・正面へは続ける"])

    def test_the_facing_fix_goes_on_after_the_round_over(self):
        self._round_over_while_backing()
        lights = iter([(433.5 - 120, 0.9, 1.0), (433.5, 0.9, 1.0)])
        frame = __import__("numpy").zeros((702, 867, 3), "uint8")
        with patch.object(self.ex, "_capture_bgr", return_value=frame), \
             patch.object(ActionExecutor.RunFace, "find_mark", side_effect=lambda _s: next(lights)):
            self._run()
        self.assertEqual([e for e in self.w.events if e[0] == "osc"][-1][1], "/input/LookLeft")
        self.assertEqual(len([e for e in self.w.events if e[0] == "osc"]), 3, "後ろ・左 0.9・直し 1 回")
        self.assertEqual(self.logs, ["Run: リスポーンして正面を向きました（向きを 1 回直しました）"])

    def test_the_begin_move_comes_after_all_of_it(self):
        begun = []
        later = []

        def press(address, stop):
            if address == "/input/MoveBackward":
                self.st.in_round = False                    # RoundOver → Begin 待ちが始まる
                t = threading.Thread(target=self.ex.do_after_round, daemon=True)
                later.append(t)
                t.start()
                time.sleep(0.1)
        self.osc.on_press = press
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(self.ex, "_begin_precheck",
                          side_effect=lambda **k: begun.append(list(self.w.events)) and False):
            self._run()
            later[0].join(2.0)
        self.assertEqual(len(begun), 1)
        self.assertEqual(begun[0][-2:], [("osc", "/input/LookLeft", 0.9), "release"],
                         "後ろへ・左へが終わってから Begin へ")

    def test_the_next_round_after_the_round_over_stops_it(self):
        def press(address, stop):
            if address == "/input/MoveBackward":
                self.st.in_round = False
                self.assertFalse(stop())
                self.st.round_seq += 1                      # 次のラウンドが始まった
                self.st.in_round = True
                self.assertTrue(stop())
        self.osc.on_press = press
        self._run()
        self.assertNotIn(("osc", "/input/LookLeft", 0.9), self.w.events)
        self.assertEqual(self.logs, [])

    def test_stopping_after_the_round_over_stops_it(self):
        def press(address, stop):
            if address == "/input/MoveBackward":
                self.st.in_round = False
                self.running["on"] = False
                self.assertTrue(stop())
        self.osc.on_press = press
        self._run()
        self.assertNotIn(("osc", "/input/LookLeft", 0.9), self.w.events)

    def test_a_run_already_over_before_the_respawn_does_not_respawn(self):
        self.st.in_round = False
        self._run()
        self.assertEqual(self.w.events, [])
        self.assertEqual(self.logs, ["Run: リスポーンできませんでした（止めた・ラウンドが変わった）"])

    def test_a_round_over_while_the_menu_is_open_stops_before_the_respawn(self):
        """リスポーンする前（メニュー・ボタン）は今どおり in_round も見る"""
        self.w.on_find = lambda: setattr(self.st, "in_round", False)
        self._run()
        self.assertNotIn(("click", 0.15), self.w.events)
        self.assertFalse([e for e in self.w.events if e[0] == "osc"])


class TestHoldTheFrontAfterThePress(_Case):
    """EN: 押した後、最大 RESPAWN_HOLD_FRONT_SEC は鍵・前面・カーソルをそのままにして行を待つ（ceed392 で押した
    直後に返したら、VRChat がクリックを処理する前に前面が移って押せなくなった。前の成功 51 回は 0.26〜0.58 秒）"""

    def setUp(self):
        super().setUp()
        self.w.respawns = False
        self.line_at = None             # 押し終えてから行が来るまで（None なら来ない）
        self.line_click = 1             # 何回目の押しの後に来るか
        self.clicked_at = []
        self.returned_at = []
        self.free_while_waiting = []
        w = self.w
        real_click = WindowOperator.mouse_click.side_effect
        real_return = WindowOperator.return_front.side_effect

        def click(sec, pause=False):
            real_click(sec, pause)
            self.clicked_at.append(w.now[0])

        def give_back(loan):
            self.returned_at.append(w.now[0])
            return real_return(loan)

        def on_sleep():
            if self.line_at is not None and len(self.clicked_at) >= self.line_click \
                    and self.st.respawn_seen_seq == 0 and w.now[0] - self.clicked_at[-1] >= self.line_at - 1e-9:
                self.st.respawn_seen_seq += 1
                w.events.append("line")
            if self.clicked_at and len(self.returned_at) == len(self.clicked_at):
                # 返した後（鍵の外）: ほかの窓が鍵を取れるか
                got = SharedState._GLOBAL_ACTION_LOCK.acquire(blocking=False)
                if got:
                    SharedState._GLOBAL_ACTION_LOCK.release()
                self.free_while_waiting.append(got)
        w.on_sleep = on_sleep
        for p in (patch.object(WindowOperator, "mouse_click", side_effect=click),
                  patch.object(WindowOperator, "return_front", side_effect=give_back)):
            p.start()
            self.addCleanup(p.stop)

    def _written(self):
        return [c.args[0] for c in DebugLog.write.call_args_list]

    def test_a_line_after_0_4_is_waited_for_in_front(self):
        self.line_at = 0.4
        locked = []
        on_sleep = self.w.on_sleep

        def watch():
            if self.clicked_at and not self.returned_at:
                locked.append((SharedState._GLOBAL_ACTION_LOCK.locked(), self.w.front == HWND))
            on_sleep()
        self.w.on_sleep = watch
        self._run()
        i = self.w.events.index(("click", 0.15))
        self.assertEqual(self.w.events[i:i + 4], [("click", 0.15), "line", "return", ("cursor", (11, 22))],
                         "前面とカーソルを返すのは行が来た後")
        self.assertTrue(locked and all(l == (True, True) for l in locked), "待つ間は鍵と前面を持ったまま")
        self.assertAlmostEqual(self.returned_at[0] - self.clicked_at[0], 0.4, delta=0.11)
        self.assertEqual(self.w.events.count(("click", 0.15)), 1)
        self.assertIn("[操作] [窓2] Run: 「Player respawned」を受けた（押してから 0.40 秒・前面のまま）",
                      self._written())
        self.assertEqual(self.logs, ["Run: リスポーンして正面を向きました"])

    def test_no_line_in_1_0_returns_the_front_and_waits_outside_the_lock(self):
        self.line_at = 1.5
        self._run()
        self.assertAlmostEqual(self.returned_at[0] - self.clicked_at[0], config.RESPAWN_HOLD_FRONT_SEC, delta=0.11)
        self.assertTrue(self.free_while_waiting and all(self.free_while_waiting), "ほかの窓が鍵を取れる")
        self.assertEqual(self.w.events.count(("click", 0.15)), 1, "2 秒の中で来たので押し直さない")
        self.assertIn("[操作] [窓2] Run: 「Player respawned」を受けた（押してから 1.50 秒・前面を返した後）",
                      self._written())

    def test_the_whole_wait_is_still_2_seconds_from_the_press(self):
        """鍵の外の残りは、押してから RESPAWN_LOG_WAIT_SEC まで（前面で待った 1.0 秒を足さない）"""
        self._run()
        self.assertEqual(len(self.clicked_at), 2)
        self.assertAlmostEqual(self.clicked_at[1] - self.clicked_at[0], config.RESPAWN_LOG_WAIT_SEC,
                               delta=0.6, msg="押し直しまで（押し直しの前の見直しの分だけ足される）")
        self.assertLess(self.clicked_at[1] - self.clicked_at[0], config.RESPAWN_LOG_WAIT_SEC + 0.6)

    def test_the_second_press_also_holds_the_front(self):
        self._run()
        self.assertEqual(len(self.clicked_at), 2)
        self.assertAlmostEqual(self.returned_at[1] - self.clicked_at[1], config.RESPAWN_HOLD_FRONT_SEC, delta=0.11)
        self.assertEqual(self.logs, ["Run: リスポーンできませんでした（リスポーンの行が来ない）"])

    def test_the_second_press_line_in_front(self):
        self.line_at, self.line_click = 0.3, 2
        self._run()
        self.assertIn("[操作] [窓2] Run: 「Player respawned」を受けた（押してから 0.30 秒・前面のまま）",
                      self._written())
        self.assertEqual(len(self.clicked_at), 2)
        self.assertAlmostEqual(self.returned_at[1] - self.clicked_at[1], 0.3, delta=0.11)

    def test_stopped_while_holding_gives_back_at_once(self):
        def stop_soon():
            if self.clicked_at and self.w.now[0] - self.clicked_at[0] >= 0.2:
                self.running["on"] = False
        on_sleep = self.w.on_sleep
        self.w.on_sleep = lambda: (stop_soon(), on_sleep())
        self._run()
        self.assertLess(self.returned_at[0] - self.clicked_at[0], 0.35, "1.0 秒待たずに返す")
        self.assertEqual(self.w.events.count(("click", 0.15)), 1)

    def test_the_next_round_while_holding_gives_back_at_once(self):
        def next_round():
            if self.clicked_at and self.w.now[0] - self.clicked_at[0] >= 0.2:
                self.st.round_seq += 1
        on_sleep = self.w.on_sleep
        self.w.on_sleep = lambda: (next_round(), on_sleep())
        self._run()
        self.assertLess(self.returned_at[0] - self.clicked_at[0], 0.35)

    def test_the_value(self):
        self.assertEqual(config.RESPAWN_HOLD_FRONT_SEC, 1.0)
        self.assertEqual(config.RESPAWN_CLICK_SEC, 0.15, "押す長さは変えない")
