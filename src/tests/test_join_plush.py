"""入室の Begin の後に Guidance Plush を取りに行く（DZ）。本物の入力・OSC・窓は使わない"""
from tests.support import *  # noqa: F401,F403
import inspect

import numpy as np

PLUSH = 70
SCALE = 2.0
OFFSET = (100.0, 50.0)


def project(pt):
    """偽の位置合わせ: 見本の点 → 撮影の点（2 倍・ずらし）"""
    return pt[0] * SCALE + OFFSET[0], pt[1] * SCALE + OFFSET[1], SCALE, SCALE


def at(pt):
    return project(pt)[:2]


class _Shop:
    """偽の店。つまみの位置（最後のドラッグの先）で Have Plush の行に緑が出る。Equip で Equipping"""

    def __init__(self, test, row_shift=0, equips=(PLUSH,), no_row=False):
        self.test = test
        self.events = []
        self.locates = 0
        self.knob = at(ItemFetch.PLUSH_KNOB_TOP)
        self.row_shift = row_shift            # 緑が出るドラッグ先のずらし（見本の px）
        self.no_row = no_row
        self.equips = list(equips)            # Equip を押すたびに来る Equipping（None は来ない）
        self.seq = 0
        self.last_id = 0
        self.now = [0.0]
        self.logs = []

    # Fetcher に渡すもの
    def locate(self, img, point):
        self.locates += 1
        return (*project(point), 120, project)

    def capture(self):
        img = np.zeros((1080, 1920, 3), np.uint8)
        want = at((ItemFetch.PLUSH_KNOB_TARGET[0], ItemFetch.PLUSH_KNOB_TARGET[1] + self.row_shift))
        if not self.no_row and self.knob == want:
            x, y = (int(v) for v in at(ItemFetch.PLUSH_ROW))
            img[y - 3:y + 4, x - 20:x + 21] = (0, 200, 0)        # 緑の文字（BGR）
        ex, ey = (int(v) for v in at(ItemFetch.BUTTONS["Equip"]))
        img[ey - 4:ey + 5, ex - 10:ex + 11] = (0, 200, 0)
        return img, (1800.0, 100.0)

    def tab_down(self):
        self.events.append(("tab_down",))

    def tab_up(self):
        self.events.append(("tab_up",))

    def move_rel(self, dx, dy):
        self.events.append(("move", dx, dy))

    def click_at(self, x, y):
        self.events.append(("click", round(x, 1), round(y, 1)))
        ex, ey = at(ItemFetch.BUTTONS["Equip"])
        if abs(x - ex) < 15 and abs(y - ey) < 10 and self.equips:
            got = self.equips.pop(0)
            if got is not None:
                self.seq += 1
                self.last_id = got
        return True

    def drag(self, start, end, steps, step_sec):
        self.events.append(("drag", tuple(round(v, 1) for v in start), tuple(round(v, 1) for v in end),
                            steps, step_sec))
        self.knob = end
        return True

    def fetcher(self, stopped=lambda: None):
        f = ItemFetch.Fetcher(
            osc=MagicMock(), grounded=lambda: True, capture=self.capture, mouse=self,
            equip_seen=lambda: (self.seq, self.last_id), stopped=stopped, log=self.logs.append,
            locate_fn=self.locate, sleep=lambda s: self.now.__setitem__(0, self.now[0] + s),
            clock=lambda: self.now[0])
        f.gain = (1.0, 1.0)
        return f

    def kinds(self):
        return [e[0] for e in self.events]


def _r(pt):
    return tuple(round(v, 1) for v in pt)


class TestBuyPlush(unittest.TestCase):
    def test_event_drag_row_equip(self):
        shop = _Shop(self)
        self.assertTrue(shop.fetcher().buy_plush(PLUSH))
        top, target = _r(at(ItemFetch.PLUSH_KNOB_TOP)), _r(at(ItemFetch.PLUSH_KNOB_TARGET))
        self.assertEqual(shop.kinds(), ["tab_down", "click", "drag", "click", "click", "tab_up"])
        self.assertEqual(shop.events[1], ("click", *_r(at(ItemFetch.BUTTONS["Event"]))))
        self.assertEqual(shop.events[2], ("drag", top, target, 20, 0.03), "Tab＋左ボタンを押したまま 20 段")
        row = shop.events[3]
        self.assertAlmostEqual(row[1], at(ItemFetch.PLUSH_ROW)[0], delta=1.5, msg="行の緑の重心")
        self.assertAlmostEqual(row[2], at(ItemFetch.PLUSH_ROW)[1], delta=1.5)

    def test_points_come_from_the_first_screen_only(self):
        shop = _Shop(self)
        f = shop.fetcher()
        f.buy_plush(PLUSH)
        before_event = shop.locates
        self.assertGreater(before_event, 0)
        # Event を押した後は位置合わせしない（locate を呼ぶのは最初の画面だけ）
        shop2 = _Shop(self, row_shift=8)
        calls = []
        real = shop2.locate

        def locate(img, point):
            calls.append([e[0] for e in shop2.events])
            return real(img, point)
        shop2.locate = locate
        shop2.fetcher().buy_plush(PLUSH)
        self.assertTrue(all("click" not in c for c in calls), "Event の後に撮り直して位置合わせしない")

    def test_no_green_shifts_the_drag_after_going_back_to_the_top(self):
        shop = _Shop(self, row_shift=8)
        self.assertTrue(shop.fetcher().buy_plush(PLUSH))
        top = _r(at(ItemFetch.PLUSH_KNOB_TOP))
        t0 = _r(at(ItemFetch.PLUSH_KNOB_TARGET))
        t8 = _r(at((ItemFetch.PLUSH_KNOB_TARGET[0], ItemFetch.PLUSH_KNOB_TARGET[1] + 8)))
        drags = [(e[1], e[2]) for e in shop.events if e[0] == "drag"]
        self.assertEqual(drags, [(top, t0), (t0, top), (top, t8)])

    def test_three_tries_then_fail(self):
        shop = _Shop(self, no_row=True)
        self.assertFalse(shop.fetcher().buy_plush(PLUSH))
        drags = [e for e in shop.events if e[0] == "drag"]
        self.assertEqual(len(drags), 5, "上→0・戻す・上→+8・戻す・上→-8")
        self.assertEqual(shop.kinds()[-1], "tab_up")
        self.assertEqual(shop.kinds().count("click"), 1, "Event だけ。行も Equip も押さない")
        self.assertIn("アイテム取得: Have Plush の行が見つかりません", shop.logs)

    def test_a_wrong_or_missing_equipping_redoes_once(self):
        shop = _Shop(self, equips=(81, PLUSH))
        self.assertTrue(shop.fetcher().buy_plush(PLUSH))
        drags = [e for e in shop.events if e[0] == "drag"]
        self.assertEqual(len(drags), 3, "やり直しは一番上へ戻してから")
        self.assertEqual(shop.kinds().count("click"), 5, "Event・行・Equip・行・Equip")
        shop = _Shop(self, equips=(None, None))
        self.assertFalse(shop.fetcher().buy_plush(PLUSH))
        self.assertEqual(shop.kinds().count("click"), 5, "やり直しは 1 回だけ")

    def test_a_stop_still_releases_the_tab(self):
        shop = _Shop(self)
        n = [0]

        def stopped():
            n[0] += 1
            return "round" if any(e[0] == "drag" for e in shop.events) else None
        with self.assertRaises(ItemFetch.Stopped):
            shop.fetcher(stopped=stopped).buy_plush(PLUSH)
        self.assertEqual(shop.kinds()[-1], "tab_up")

    def test_the_constants(self):
        self.assertEqual((ItemFetch.PLUSH_KNOB_TOP, ItemFetch.PLUSH_KNOB_TARGET, ItemFetch.PLUSH_ROW,
                          ItemFetch.BUTTONS["Equip"]), ((114, 89), (114, 132), (73, 96), (180, 177)))
        self.assertEqual((ItemFetch.DRAG_STEPS, ItemFetch.DRAG_STEP_SEC, ItemFetch.PLUSH_SHIFTS,
                          ItemFetch.PLUSH_ROW_WINDOW), (20, 0.03, (0, 8, -8), (25, 5)))

    def test_the_lost_item_path_is_unchanged(self):
        src = inspect.getsource(ItemFetch.Fetcher.buy)
        self.assertIn("self.press_shop(shop, framed) and self.equip(target_id)", src)


# ── ドラッグ（_FrontOnlyMouse・偽の Windows）──────────────────
class TestTheDrag(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.front = 0x30
        for name, fake in (("window_origin", lambda h: (1000, 500)),
                           ("foreground_hwnd", lambda: self.front),
                           ("set_cursor_position", lambda p: self.events.append(("cursor", p)) or True),
                           ("mouse_down", lambda pause=False: self.events.append(("down",))),
                           ("mouse_up", lambda pause=False: self.events.append(("up",)))):
            p = patch.object(WindowOperator, name, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(ActionExecutor.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_press_twenty_steps_release(self):
        mouse = ActionExecutor._FrontOnlyMouse(0x30)
        self.assertTrue(mouse.drag((10, 20), (10, 60), 20, 0.03))
        self.assertEqual(self.events[0], ("cursor", (1010, 520)))
        self.assertEqual(self.events[1], ("down",))
        moves = [e for e in self.events[2:-1]]
        self.assertEqual(len(moves), 20)
        self.assertEqual(moves[-1], ("cursor", (1010, 560)))
        self.assertEqual(self.events[-1], ("up",))

    def test_losing_the_front_still_releases(self):
        mouse = ActionExecutor._FrontOnlyMouse(0x30)
        calls = [0]
        real = WindowOperator.set_cursor_position.side_effect

        def lose(p):
            calls[0] += 1
            if calls[0] == 5:
                self.front = 0x99
            return real(p)
        WindowOperator.set_cursor_position.side_effect = lose
        with self.assertRaises(ItemFetch.Stopped):
            mouse.drag((0, 0), (0, 40), 20, 0.03)
        self.assertEqual(self.events[-1], ("up",), "左ボタンは離す")


# ── ActionExecutor: 行くか・止める条件 ────────────────────
class TestFetchGuidancePlush(unittest.TestCase):
    def _ex(self, itype=config.INSTANCE_PRIVATE, receiver=True, osc=True):
        st = WindowState(instance_type=itype, window_idx=2)
        self.logs = []
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=0x30, osc_port=9000), st, lambda: True,
                                           self.logs.append)
        ex._osc = MagicMock() if osc else None
        ex._receiver = MagicMock() if receiver else None
        return ex

    def test_goes_with_the_plush_way(self):
        ex = self._ex()
        with patch.object(RoundDecision, "guidance_plush_id", return_value=PLUSH), \
             patch.object(ItemFetch, "available", return_value=True), \
             patch.object(ex, "_fetch_item", return_value="ok") as fetch:
            stop = lambda: False
            self.assertEqual(ex.fetch_guidance_plush(extra_stop=stop), "ok")
        fetch.assert_called_once_with(0, "Event", PLUSH, plush=True, extra_stop=stop)

    def test_skips(self):
        for kwargs, why in (({"receiver": False}, "受信が無い"), ({"osc": False}, "OSC が使えない窓"),
                            ({"itype": config.INSTANCE_PUBLIC}, "プライベートのインスタンスでない")):
            ex = self._ex(**kwargs)
            with patch.object(RoundDecision, "guidance_plush_id", return_value=PLUSH), \
                 patch.object(ItemFetch, "available", return_value=True), \
                 patch.object(ex, "_fetch_item") as fetch:
                self.assertEqual(ex.fetch_guidance_plush(), "skipped")
            fetch.assert_not_called()
            self.assertIn(f"Guidance Plush を取りに行きません（{why}）", self.logs)

    def test_another_item_does_not_stop_it_but_the_plush_does(self):
        ex = self._ex()
        st = ex._st
        st.item_id = 81
        self.assertEqual(ex._fetch_stopped(0, 1e18), "equipped", "今の取得は手で装備したら止まる")
        self.assertIsNone(ex._fetch_stopped(0, 1e18, want=PLUSH), "Guidance Plush はほかのアイテムがあっても")
        st.item_id = PLUSH
        self.assertEqual(ex._fetch_stopped(0, 1e18, want=PLUSH), "equipped")
        st.item_id = 0
        self.assertEqual(ex._fetch_stopped(0, 1e18, want=PLUSH, extra_stop=lambda: True), "stopped")
        st.in_round = True
        self.assertEqual(ex._fetch_stopped(0, 1e18, want=PLUSH), "round")

    def test_the_in_front_part_uses_buy_plush(self):
        # EV で、前面を借りて押すところは _fetch_in_front_locked に分けた（_fetch_in_front は装備待ちフリーズと鍵）
        src = inspect.getsource(ActionExecutor.ActionExecutor._fetch_in_front_locked)
        self.assertIn("fetcher.buy_plush(item_id) if plush else fetcher.buy(shop, item_id)", src)


# ── 押した後の Verified を待つ ───────────────────────────
class TestWaitVerified(unittest.TestCase):
    def _log(self, *lines):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "output_log.txt"
        path.write_text("2026.10.12 10:00:00 Debug      -  Verified\n", encoding="utf-8")   # 押す前
        offset = path.stat().st_size
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line)
        return str(path), offset

    def _wait(self, path, offset, running=lambda: True):
        now = [0.0]
        return ToNEntry.wait_verified(path, offset, 60.0, running,
                                      sleep=lambda s: now.__setitem__(0, now[0] + s),
                                      clock=lambda: now[0]), now[0]

    def test_the_first_verified_after_the_press(self):
        path, offset = self._log("2026.10.12 10:00:05 Debug      -  something\n",
                                 "2026.10.12 10:00:06 Debug      -  Verified\n")
        self.assertEqual(self._wait(path, offset)[0], "verified")

    def test_the_verified_before_the_press_does_not_count(self):
        path, offset = self._log()
        result, waited = self._wait(path, offset)
        self.assertEqual(result, "timeout")
        self.assertGreaterEqual(waited, 60.0, "60 秒で諦める")

    def test_a_round_start_stops_it(self):
        path, offset = self._log("2026.10.12 10:00:05 Debug      -  This round is taking place at "
                                 "Facility (12) and the round type is Classic\n",
                                 "2026.10.12 10:00:06 Debug      -  Verified\n")
        self.assertEqual(self._wait(path, offset)[0], "round")

    def test_a_half_written_line_waits_for_the_rest(self):
        path, offset = self._log("2026.10.12 10:00:06 Debug      -  Verif")
        now = [0.0]

        def sleep(s):
            now[0] += s
            with open(path, "a", encoding="utf-8") as f:
                f.write("ied\n")
        self.assertEqual(ToNEntry.wait_verified(path, offset, 60.0, sleep=sleep, clock=lambda: now[0]),
                         "verified")

    def test_a_stop(self):
        path, offset = self._log()
        self.assertEqual(self._wait(path, offset, running=lambda: False)[0], "stopped")


# ── 画面と入室の流れ ─────────────────────────────────
class _Entry:
    made = []

    def __init__(self, hwnd, window_index=0, log=None, is_running=None, can_operate=None):
        self.hwnd = hwnd
        self.calls = []
        _Entry.made.append(self)

    def run(self):
        return True

    def load_code(self, code, log_path):
        return "loaded"

    def press_begin(self):
        self.calls.append("begin")
        return True

    def close(self):
        pass


class _Thread:
    """入室の流れはその場で走らせ、Guidance Plush の待ちは走らせずに覚える（次の窓を待たせないこと）"""
    plush = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        if getattr(self.target, "__name__", "") == "_plush_after_entry":
            _Thread.plush.append(self.args)
        else:
            self.target(*self.args)


class TestTheEntryFlow(unittest.TestCase):
    def _app(self, begin=True, plush=True, n=2):
        app = type("FakeApp", (), {})()
        app.v_ton_entry = MagicMock(get=lambda: True)
        app.v_ton_begin = MagicMock(get=lambda: begin)
        app.v_ton_entry_plush = MagicMock(get=lambda: plush)
        app.v_emergency_key = MagicMock(get=lambda: "p")
        app._launched_tab_indices = None
        app.tabs = []
        for i in range(n):
            tab = MagicMock()
            tab.idx = i
            tab._get_selected_hwnd.return_value = 0x100 + i
            tab.v_log.get.return_value = f"C:/logs/output_log_{i}.txt"
            app.tabs.append(tab)
        app._entry_stop = threading.Event()
        app.btn_stop_entry = MagicMock()
        app.logs = []
        app._log = app.logs.append
        app.after = lambda _ms, fn, *a: fn(*a)

        def plush_after(*a):
            pass
        plush_after.__name__ = "_plush_after_entry"
        app._plush_after_entry = plush_after
        _Entry.made, _Thread.plush = [], []
        for target, name, value in ((ToNEntry, "ToNEntry", _Entry), (mainGUI.threading, "Thread", _Thread),
                                    (SaveLoad, "log_size", lambda p: 1234)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        return app

    def test_each_window_waits_on_its_own_thread(self):
        app = self._app()
        mainGUI.App._run_ton_entry(app)
        self.assertEqual([e.calls for e in _Entry.made], [["begin"], ["begin"]], "次の窓の入室は待たない")
        self.assertEqual(_Thread.plush, [(1, 0x100, "C:/logs/output_log_0.txt", 1234),
                                         (2, 0x101, "C:/logs/output_log_1.txt", 1234)])

    def test_off_or_begin_off_does_not_go(self):
        for kwargs in ({"plush": False}, {"begin": False}):
            app = self._app(**kwargs)
            mainGUI.App._run_ton_entry(app)
            self.assertEqual(_Thread.plush, [], kwargs)


class TestPlushAfterEntry(unittest.TestCase):
    def _app(self, running=True, receiver=True):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app._entry_stop = threading.Event()
        app._running = running
        monitor = MagicMock()
        monitor.cfg.hwnd = 0x30
        monitor._action._receiver = MagicMock() if receiver else None
        app.monitors = [monitor]
        self.action = monitor._action
        return app

    def _run(self, app, result):
        with patch.object(ToNEntry, "wait_verified", return_value=result) as wait:
            mainGUI.App._plush_after_entry(app, 3, 0x30, "C:/logs/x.txt", 99)
        self.assertEqual(wait.call_args.args[:3], ("C:/logs/x.txt", 99, config.PLUSH_VERIFIED_WAIT_SEC))

    def test_verified_and_the_macro_running_goes(self):
        app = self._app()
        self._run(app, "verified")
        self.action.fetch_guidance_plush.assert_called_once()

    def test_without_the_macro_or_its_receiver_skips(self):
        for kwargs in ({"running": False}, {"receiver": False}):
            app = self._app(**kwargs)
            self._run(app, "verified")
            self.action.fetch_guidance_plush.assert_not_called()
            self.assertIn("[窓3] マクロが動いていないため Guidance Plush を取りに行きません", app.logs)

    def test_no_verified_or_a_round_start(self):
        app = self._app()
        self._run(app, "timeout")
        self.assertIn("[窓3] ⚠ Begin の受理を確認できないため Guidance Plush を取りに行きません", app.logs)
        self._run(app, "round")
        self.assertIn("[窓3] ラウンドが始まったため Guidance Plush を取りに行きません", app.logs)
        self.action.fetch_guidance_plush.assert_not_called()


class TestTheCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_next_to_begin_and_only_with_begin(self):
        chk = self.app.chk_ton_entry_plush
        self.assertEqual(chk.cget("text"), "Guidance Plushを自動取得")
        row = chk.master.pack_slaves()
        texts = [w.cget("text") for w in row]
        self.assertEqual(texts[texts.index("自動でBeginを押す") + 1], "Guidance Plushを自動取得")
        self.app.v_ton_begin.set(False)
        self.app._refresh_plush_check()
        self.assertEqual(str(chk.cget("state")), "disabled")
        self.app.v_ton_begin.set(True)
        self.app._refresh_plush_check()
        self.assertEqual(str(chk.cget("state")), "normal")

    def test_saved_on_by_default(self):
        self.assertTrue(self.app.v_ton_entry_plush.get())
        self.app.v_ton_entry_plush.set(True)
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        self.assertIs(stored["ton_entry_plush"], True)
        for data, expected in (({"ton_entry_plush": True, "ton_begin": True}, True), ({}, True),
                               ({"ton_entry_plush": False}, False), ({"ton_entry_plush": "yes"}, True)):
            with patch.object(mainGUI, "load_settings", return_value=data):
                self.app._load_saved_settings()
            self.assertIs(self.app.v_ton_entry_plush.get(), expected, data)
