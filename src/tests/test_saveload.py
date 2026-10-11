"""入室後に Save/Load のコード欄へセーブコードを貼る（DY）。本物の入力・クリップボード・窓・OSC は使わない。
セーブコードは、どのログにも出ないこと"""
from tests.support import *  # noqa: F401,F403
import inspect

CODE = "SECRET-save-code-0123456789abcdef"
HWND = 0x55
ORIGIN = (100, 50)
FOUND = (500.0, 300.0, 0.99, 1.0)


class _World:
    """偽の窓・前面・カーソル・キー・時計・VRChat のログ。起きたことは events に順に残す"""

    def __init__(self, test, box_after=1, log_line=True, stop_at=None, front_lost_at=None,
                 loads_on_enter=1, closes_on_enter=1):
        self.test = test
        # EC: 何回目の Enter でログに「Loaded Data Successfully」が出るか・入力欄が閉じるか（None は出ない・閉じない）
        self.loads_on_enter = loads_on_enter
        self.closes_on_enter = closes_on_enter
        self.enters = 0
        self.events = []
        self.written = []
        self.front = 0x900
        self.cursor = (11, 22)
        self.now = [0.0]
        self.running = True
        self.box_after = box_after          # 入力欄が出るまでの撮影の回数（None は出ない）
        self.box_polls = 0
        self.log_line = log_line
        self.stop_at = stop_at              # この出来事のときに中止（入室操作を中止）
        self.front_lost_at = front_lost_at
        self.captures_after_paste = 0
        self.captures = 0
        self.box_sizes = []
        self.clipboard = []
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.log_path = str(Path(tmp.name) / "output_log.txt")
        Path(self.log_path).write_text("old line\\nLoaded Data Successfully (前の分)\\n", encoding="utf-8")
        w = self

        def event(e):
            w.events.append(e)
            kind = e[0]
            if kind == w.stop_at:
                w.running = False
            if kind == w.front_lost_at:
                w.front = 0x900

        def borrow(h):
            event(("borrow", h))
            w.front = h
            return True, "loan"

        def give_back(loan):
            event(("return",))
            w.front = 0x900
            return True

        def set_cursor(p):
            event(("cursor", p))
            w.cursor = p
            return True

        def keys(k):
            event(("keys", k))
            if k == "enter":
                w.enters += 1
            if k == "enter" and w.log_line and w.enters == w.loads_on_enter:
                with open(w.log_path, "a", encoding="utf-8") as f:
                    f.write("2026.10.12 10:00:01 Debug      -  Loaded Data Successfully!\\n")

        def box_shown(bgr, width, height):
            w.box_sizes.append((width, height))
            w.box_polls += 1
            if ("keys", "ctrl+v") in w.events:      # 貼った後: Enter で閉じたか（EC）
                return w.closes_on_enter is None or w.enters < w.closes_on_enter
            return w.box_after is not None and w.box_polls >= w.box_after

        def capture():
            w.captures += 1
            if any(e == ("keys", "ctrl+v") for e in w.events):
                w.captures_after_paste += 1
            return "shot"

        def sleep(sec):
            w.now[0] += sec
        fake_time = type(sys)("time_for_test")
        fake_time.time = fake_time.monotonic = lambda: w.now[0]
        fake_time.sleep = sleep
        for target, name, fake in (
                (WindowOperator, "borrow_front", borrow),
                (WindowOperator, "return_front", give_back),
                (WindowOperator, "cursor_position", lambda: w.cursor),
                (WindowOperator, "set_cursor_position", set_cursor),
                (WindowOperator, "foreground_hwnd", lambda: w.front),
                (WindowOperator, "window_origin", lambda h: ORIGIN),
                (WindowOperator, "client_height", lambda h: 1440),
                (WindowOperator, "client_size", lambda h: (2560, 1440)),
                (WindowOperator, "press_tab", lambda: event(("tab_down",))),
                (WindowOperator, "release_tab", lambda: event(("tab_up",))),
                (WindowOperator, "mouse_click", lambda sec, pause=False: event(("click", sec))),
                (WindowOperator, "send_keys", keys),
                (SaveLoad, "find_codebox", lambda bgr, h: FOUND),
                (SaveLoad, "input_box_shown", box_shown),
                (SaveLoad, "write_clipboard", lambda text: w.clipboard.append(text) or event(("clipboard",)) or True),
                (DebugLog, "write", w.written.append),
                (DebugLog, "exception", lambda *a: w.written.append("exception")),
        ):
            p = patch.object(target, name, side_effect=fake)
            p.start()
            test.addCleanup(p.stop)
        for target, name, value in ((ToNEntry, "time", fake_time),):
            p = patch.object(target, name, value)
            p.start()
            test.addCleanup(p.stop)
        self.capture = capture

    def entry(self, private=True):
        self.logs = []
        entry = ToNEntry.ToNEntry(HWND, osc_port=9000, log=self.logs.append,
                                  is_running=lambda: self.running, can_operate=lambda: private)
        entry._osc = MagicMock()                  # OSC は送らない（close の離す送信も）
        self.test.addCleanup(entry.close)
        entry._capture_bgr = self.capture
        return entry

    def kinds(self):
        return [e[0] for e in self.events]


class TestTheSequence(unittest.TestCase):
    def test_tab_click_release_paste_enter_then_give_back(self):
        world = _World(self)
        result = world.entry().load_code(CODE, world.log_path)
        self.assertEqual(result, "loaded")
        point = (int(FOUND[0] + ORIGIN[0]), int(FOUND[1] + ORIGIN[1]))
        self.assertEqual(world.events, [
            ("borrow", HWND), ("tab_down",), ("cursor", point), ("click", config.SAVELOAD_CLICK_SEC),
            ("tab_up",), ("clipboard",), ("keys", "ctrl+v"), ("keys", "enter"), ("return",),
            ("cursor", (11, 22))])
        self.assertEqual(world.clipboard, [CODE], "最初に読んだコードを入れ直してから貼る")
        self.assertIn("✅ コードを読み込みました", world.logs)

    def test_the_lock_is_held_while_in_front(self):
        world = _World(self)
        held = []
        borrow = WindowOperator.borrow_front.side_effect
        WindowOperator.borrow_front.side_effect = \
            lambda h: held.append(SharedState._GLOBAL_ACTION_LOCK.locked()) or borrow(h)
        world.entry().load_code(CODE, world.log_path)
        self.assertEqual(held, [True])

    def test_no_input_box_means_no_paste_and_no_enter(self):
        world = _World(self, box_after=None)
        entry = world.entry()
        self.assertEqual(entry.load_code(CODE, world.log_path), "skipped")
        self.assertNotIn(("keys", "ctrl+v"), world.events)
        self.assertNotIn(("keys", "enter"), world.events)
        self.assertEqual(world.clipboard, [])
        self.assertEqual(world.kinds()[-2:], ["return", "cursor"], "前面とカーソルは返す")
        self.assertGreaterEqual(world.now[0], config.SAVELOAD_BOX_WAIT_SEC, "最大まで待ってから")
        self.assertIn("⚠ コードを読み込めませんでした（入力欄が出ない）", world.logs)

    def test_the_input_box_is_polled(self):
        world = _World(self, box_after=3)
        self.assertEqual(world.entry().load_code(CODE, world.log_path), "loaded")
        self.assertEqual(world.box_polls, 3)
        self.assertEqual(set(world.box_sizes), {(2560, 1440)}, "クライアントの幅と高さを渡す（EA）")

    def test_no_capture_after_the_paste(self):
        world = _World(self)
        world.entry().load_code(CODE, world.log_path)
        self.assertEqual(world.captures_after_paste, 0, "Ctrl+V の後は撮らない")

    def test_an_unconfirmed_load_warns(self):
        world = _World(self, log_line=False)
        self.assertEqual(world.entry().load_code(CODE, world.log_path), "unconfirmed")
        self.assertIn("⚠ コードの読み込みを確認できませんでした", world.logs)
        self.assertGreaterEqual(world.now[0], config.SAVELOAD_LOG_WAIT_SEC)
        self.assertNotIn("⚠ 入力欄が開いたままです（手で Enter を押してください）", world.logs,
                         "Enter で閉じていれば言わない")

    def test_the_old_loaded_line_does_not_count(self):
        """前からログにある行では成功にしない（Enter の後に出た行だけ）"""
        world = _World(self, log_line=False)
        self.assertEqual(world.entry().load_code(CODE, world.log_path), "unconfirmed")

    def test_empty_clipboard_private_and_missing_codebox(self):
        world = _World(self)
        self.assertEqual(world.entry().load_code("", world.log_path), "skipped")
        self.assertIn("⚠ クリップボードが空のためコードを読み込みません", world.logs)
        self.assertEqual(world.entry(private=False).load_code(CODE, world.log_path), "skipped")
        self.assertEqual(world.captures, 0, "private でない窓は撮りもしない")
        with patch.object(SaveLoad, "find_codebox", return_value=None):
            entry = world.entry()
            self.assertEqual(entry.load_code(CODE, world.log_path), "skipped")
        self.assertIn("⚠ コード欄が見つかりません → コードを読み込みません", world.logs)
        self.assertEqual(world.events, [], "どれも何も押さない")

    def test_a_stop_releases_the_tab_and_gives_back(self):
        world = _World(self, stop_at="tab_down")
        self.assertEqual(world.entry().load_code(CODE, world.log_path), "skipped")
        self.assertNotIn("click", world.kinds())
        self.assertEqual(world.kinds(), ["borrow", "tab_down", "tab_up", "return", "cursor"])

    def test_a_stop_after_the_click_sends_nothing_more(self):
        world = _World(self, stop_at="click")
        world.entry().load_code(CODE, world.log_path)
        self.assertNotIn(("keys", "ctrl+v"), world.events)
        self.assertEqual(world.kinds()[-2:], ["return", "cursor"])

    def test_losing_the_front_stops_before_the_paste(self):
        world = _World(self, front_lost_at="tab_up")
        world.entry().load_code(CODE, world.log_path)
        self.assertNotIn(("keys", "ctrl+v"), world.events)

    def test_the_code_is_never_written(self):
        for kwargs in ({}, {"box_after": None}, {"log_line": False}, {"stop_at": "click"}):
            world = _World(self, **kwargs)
            world.entry().load_code(CODE, world.log_path)
            text = "\n".join(map(str, world.logs + world.written + world.events))
            self.assertNotIn(CODE, text, kwargs)
            self.assertNotIn(str(len(CODE)), text, ("長さも出さない", kwargs))


# ── 絵を探す（本物の見本・作った撮影）──────────────────────
class TestFinding(unittest.TestCase):
    def setUp(self):
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        self.codebox = SaveLoad._load(SaveLoad.CODEBOX_FILE)
        self.arrow = SaveLoad._load(SaveLoad.ARROW_FILE)
        self.assertTrue(SaveLoad.available(), "見本の 2 枚がある")

    def _shot(self, w, h, template, at, scale):
        cv2, np = self.cv2, self.np
        rng = np.random.default_rng(1)
        img = (rng.random((h, w)) * 40).astype(np.uint8)
        t = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        img[at[1]:at[1] + t.shape[0], at[0]:at[0] + t.shape[1]] = t
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    def test_the_scales_follow_the_window_height(self):
        self.assertEqual(SaveLoad.codebox_scales(1440)[0], 1.0)
        scales = SaveLoad.codebox_scales(706)
        self.assertEqual(scales[0], 0.49, "高さ / 1440 が真ん中")
        self.assertEqual((min(scales), max(scales), len(scales)), (0.44, 0.54, 11), "±0.05・0.01 刻み")

    def test_the_click_point_is_offset_by_the_scale(self):
        shot = self._shot(1280, 720, self.codebox, (900, 300), 0.5)
        x, y, score, s = SaveLoad.find_codebox(shot, 720)
        self.assertEqual(s, 0.5)
        self.assertGreater(score, 0.95)
        self.assertEqual((round(x), round(y)), (900 + 50, 300 + 32))

    def test_below_08_is_not_found(self):
        shot = self._shot(1280, 720, self.np.zeros((8, 8), self.np.uint8), (0, 0), 1.0)
        self.assertIsNone(SaveLoad.find_codebox(shot, 720))

    def test_the_input_box_arrow(self):
        h, w = 720, 1280
        self.assertTrue(SaveLoad.input_box_shown(self._shot(w, h, self.arrow, (908, 548), 1.0), w, h))
        self.assertFalse(SaveLoad.input_box_shown(self._shot(w, h, self.arrow, (20, 20), 1.0), w, h),
                         "下寄り・右寄りの範囲の外は見ない")
        self.assertFalse(SaveLoad.input_box_shown(
            self._shot(w, h, self.np.zeros((8, 8), self.np.uint8), (0, 0), 1.0), w, h))
        big = self._shot(2560, 1440, self.arrow, (1816, 1096), 2.0)
        self.assertTrue(SaveLoad.input_box_shown(big, 2560, 1440))

    # ── EA: 入力欄の大きさは min(幅 / 1280, 高さ / 720) ──
    def test_the_arrow_scale_is_the_smaller_of_width_and_height(self):
        self.assertAlmostEqual(SaveLoad.arrow_scale(851, 664), 0.665, places=3)
        self.assertEqual(SaveLoad.arrow_scale(2560, 1440), 2.0)
        self.assertEqual(SaveLoad.arrow_scale(3440, 1440), 2.0, "横長は高さで")
        self.assertEqual(SaveLoad.arrow_scale(1280, 1440), 1.0, "縦長は幅で")

    def _real(self, name, size, client):
        """実機の撮影の、探す範囲（左 45〜95%・上 55〜95%）だけを切り出した写し（灰色。右下の隅は消した）を、
        元の大きさの撮影に貼り戻す。窓の大きさは数字で渡す"""
        cv2, np = self.cv2, self.np
        crop = cv2.imdecode(np.fromfile(str(Path(__file__).parent / "data" / "saveload" / name), np.uint8),
                            cv2.IMREAD_GRAYSCALE)
        h, w = size
        img = np.zeros((h, w), np.uint8)
        x0, y0 = int(w * 0.45), int(h * 0.55)
        img[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]] = crop
        return SaveLoad.input_box_shown(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), *client)

    def test_the_real_small_window_with_the_input_box(self):
        """6 窓整列の 1 升（クライアント 851×664・撮影 867×703）で入力欄が開いている（中は空）"""
        self.assertTrue(self._real("small_popup.png", (703, 867), (851, 664)))

    def test_the_real_shots_without_the_input_box(self):
        self.assertFalse(self._real("small_no_popup.png", (703, 867), (851, 664)))
        self.assertFalse(self._real("full_half_no_popup.png", (720, 1280), (1280, 720)),
                         "全画面（半分に縮めた写し）")

    def test_the_codebox_still_uses_the_height(self):
        self.assertEqual(SaveLoad.codebox_scales(664)[0], round(664 / 1440, 3))


# ── クリップボード（偽の win32clipboard）────────────────────
class TestClipboard(unittest.TestCase):
    def _fake(self, text=None, fail=False):
        fake = MagicMock()
        fake.IsClipboardFormatAvailable.return_value = text is not None
        fake.GetClipboardData.return_value = text
        if fail:
            fake.OpenClipboard.side_effect = RuntimeError(CODE)
        p = patch.dict(sys.modules, {"win32clipboard": fake})
        p.start()
        self.addCleanup(p.stop)
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def test_read(self):
        fake = self._fake("  " + CODE + "\r\n")
        self.assertEqual(REAL_CLIPBOARD["read"](), CODE)
        fake.CloseClipboard.assert_called_once_with()

    def test_empty_and_broken(self):
        self._fake(None)
        self.assertEqual(REAL_CLIPBOARD["read"](), "")
        self._fake(fail=True)
        self.assertEqual(REAL_CLIPBOARD["read"](), "")
        self.assertFalse(any(CODE in m for m in self.written), "例外の中身も書かない")

    def test_write(self):
        fake = self._fake()
        self.assertTrue(REAL_CLIPBOARD["write"](CODE))
        fake.SetClipboardData.assert_called_once()
        self.assertEqual(fake.SetClipboardData.call_args.args[1], CODE)
        self.assertEqual(self.written, [])

    def test_tests_never_touch_the_real_clipboard(self):
        self.assertEqual(SaveLoad.read_clipboard(), "")
        self.assertFalse(SaveLoad.write_clipboard(CODE))


# ── 入室の流れ（mainGUI）─────────────────────────────
class _Entry:
    made = []

    def __init__(self, hwnd, window_index=0, log=None, is_running=None, can_operate=None):
        self.hwnd, self.calls = hwnd, []
        _Entry.made.append(self)

    def run(self):
        self.calls.append("run")
        return True

    def load_code(self, code, log_path):
        self.calls.append(("load", code, log_path))
        return "unconfirmed"

    def press_begin(self):
        self.calls.append("begin")
        return True

    def close(self):
        self.calls.append("close")


class TestTheEntryFlow(unittest.TestCase):
    def _app(self, begin=True, n=2):
        app = type("FakeApp", (), {})()
        app.v_ton_entry = MagicMock(get=lambda: True)
        app.v_ton_begin = MagicMock(get=lambda: begin)
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
        _Entry.made = []
        reads = []
        for target, name, value in ((ToNEntry, "ToNEntry", _Entry), (mainGUI.threading, "Thread", RunNow),
                                    (SaveLoad, "read_clipboard", lambda: reads.append(1) or CODE)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.reads = reads
        return app

    def test_run_then_load_then_begin_with_one_read_for_all_windows(self):
        app = self._app()
        mainGUI.App._run_ton_entry(app)
        self.assertEqual([e.calls for e in _Entry.made], [
            ["run", ("load", CODE, "C:/logs/output_log_0.txt"), "begin", "close"],
            ["run", ("load", CODE, "C:/logs/output_log_1.txt"), "begin", "close"]],
            "読み込めなくても Begin は押す")
        self.assertEqual(len(self.reads), 1, "クリップボードは最初に 1 回だけ読む")
        self.assertFalse(any(CODE in m for m in app.logs))

    def test_begin_off_does_not_load(self):
        app = self._app(begin=False)
        mainGUI.App._run_ton_entry(app)
        self.assertEqual([e.calls for e in _Entry.made], [["run", "close"], ["run", "close"]])
        self.assertEqual(self.reads, [], "読みもしない")

    def test_a_stop_during_the_load_skips_begin(self):
        app = self._app(n=1)
        load = _Entry.load_code

        def load_then_stop(entry, code, path):
            app._entry_stop.set()
            return load(entry, code, path)
        with patch.object(_Entry, "load_code", load_then_stop):
            mainGUI.App._run_ton_entry(app)
        self.assertNotIn("begin", _Entry.made[0].calls)


class TestTheBuild(unittest.TestCase):
    def test_the_templates_are_bundled(self):
        root = Path(SaveLoad.__file__).parent
        self.assertTrue((root / SaveLoad.CODEBOX_FILE).is_file())
        self.assertTrue((root / SaveLoad.ARROW_FILE).is_file())
        build = (root.parent / "build.py").read_text(encoding="utf-8")
        self.assertIn('"--include-data-dir=src/saveload_templates=saveload_templates"', build)
        self.assertIn('"--include-module=win32clipboard"', build)

    def test_only_paste_is_used_to_type_the_code(self):
        """コードを打ち込まない（打つと keyboard の記録に残りうる）。貼るだけ"""
        src = inspect.getsource(ToNEntry.ToNEntry._paste_code)
        for typing in ("keyboard.write", "typewrite", "send_keys(code", "press(code", "write(code"):
            self.assertNotIn(typing, src)
        self.assertIn('send_keys("ctrl+v")', src)
        self.assertEqual(len(re.findall(r"\bcode\b", src)), 2,
                         "コードを使うのはクリップボードへ入れ直すところだけ（＋引数）")


# ── EC: Enter が効かなかったら、入力欄が開いたままか見て押し直す ───────────────
class TestEnterAgain(unittest.TestCase):
    def setUp(self):
        import cv2
        import numpy as np
        from PIL import Image
        # 撮った画像を書き出す関数は呼ばれないこと
        for target, name in ((cv2, "imwrite"), (cv2, "imencode"), (Image.Image, "save"), (np, "save")):
            p = patch.object(target, name, side_effect=AssertionError(f"{name} を呼んだ"))
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _enters(world):
        return world.events.count(("keys", "enter"))

    def test_paste_then_one_second_then_enter(self):
        self.assertEqual(config.SAVELOAD_PASTE_ENTER_SEC, 1.0)
        world = _World(self)
        times = []
        keys = WindowOperator.send_keys.side_effect
        WindowOperator.send_keys.side_effect = lambda k: times.append((k, world.now[0])) or keys(k)
        world.entry().load_code(CODE, world.log_path)
        self.assertEqual([k for k, _t in times], ["ctrl+v", "enter"])
        self.assertAlmostEqual(times[1][1] - times[0][1], 1.0, places=6)
        self.assertEqual(self._enters(world), 1, "読み込めれば押し直さない")

    def test_open_box_gets_enter_again_and_loads(self):
        world = _World(self, loads_on_enter=2, closes_on_enter=2)
        self.assertEqual(world.entry().load_code(CODE, world.log_path), "loaded")
        self.assertEqual(self._enters(world), 2)
        self.assertIn("Enter を押し直しました（1 回目）", world.logs)
        self.assertIn("✅ コードを読み込みました", world.logs)

    def test_it_waits_three_seconds_before_looking(self):
        world = _World(self, loads_on_enter=2, closes_on_enter=2)
        looked, enters = [], []
        shown = SaveLoad.input_box_shown.side_effect
        keys = WindowOperator.send_keys.side_effect
        SaveLoad.input_box_shown.side_effect = \
            lambda *a: looked.append((("keys", "ctrl+v") in world.events, world.now[0])) or shown(*a)
        WindowOperator.send_keys.side_effect = \
            lambda k: (k == "enter" and enters.append(world.now[0])) or keys(k)
        world.entry().load_code(CODE, world.log_path)
        after_paste = [t for pasted, t in looked if pasted]
        self.assertTrue(after_paste)
        self.assertGreaterEqual(after_paste[0] - enters[0], config.SAVELOAD_RETRY_WAIT_SEC,
                                "Enter の後 3 秒はログだけを待つ")
        self.assertLess(after_paste[0] - enters[0], config.SAVELOAD_LOG_WAIT_SEC)

    def test_at_most_two_retries_then_left_open(self):
        world = _World(self, loads_on_enter=None, closes_on_enter=None)
        self.assertEqual(world.entry().load_code(CODE, world.log_path), "unconfirmed")
        self.assertEqual(self._enters(world), 3, "最初の 1 回と押し直し 2 回まで")
        self.assertIn("Enter を押し直しました（2 回目）", world.logs)
        self.assertIn("⚠ 入力欄が開いたままです（手で Enter を押してください）", world.logs)
        self.assertNotIn(("keys", "esc"), world.events, "Esc は押さない（貼ったコードが消える）")
        self.assertEqual(world.events.count(("keys", "ctrl+v")), 1, "貼り直さない")

    def test_a_closed_box_is_not_pressed_again(self):
        world = _World(self, loads_on_enter=None, closes_on_enter=1)
        self.assertEqual(world.entry().load_code(CODE, world.log_path), "unconfirmed")
        self.assertEqual(self._enters(world), 1)
        self.assertGreaterEqual(world.now[0], config.SAVELOAD_LOG_WAIT_SEC, "残りの時間（8 秒まで）ログを待つ")
        self.assertNotIn("⚠ 入力欄が開いたままです（手で Enter を押してください）", world.logs)

    def test_a_late_load_after_the_box_closed_counts(self):
        """入力欄は閉じた（Enter は効いた）が、ログが 3 秒より遅れて出た"""
        world = _World(self, loads_on_enter=None, closes_on_enter=1)
        entry = world.entry()
        mark = {}
        keys = WindowOperator.send_keys.side_effect

        def note(k):
            if k == "enter":
                mark["enter"] = world.now[0]
            return keys(k)
        WindowOperator.send_keys.side_effect = note
        with patch.object(SaveLoad, "log_has_loaded_line",
                          side_effect=lambda p, o: "enter" in mark and world.now[0] >= mark["enter"] + 5.0):
            self.assertEqual(entry.load_code(CODE, world.log_path), "loaded")
        self.assertEqual(self._enters(world), 1)

    def test_retry_borrows_the_front_inside_the_lock_and_gives_it_back(self):
        world = _World(self, loads_on_enter=2, closes_on_enter=2)
        held = []
        borrow = WindowOperator.borrow_front.side_effect
        WindowOperator.borrow_front.side_effect = \
            lambda h: held.append(SharedState._GLOBAL_ACTION_LOCK.locked()) or borrow(h)
        world.entry().load_code(CODE, world.log_path)
        self.assertEqual(held, [True, True], "貼るときと押し直すときの 2 回とも鍵の中")
        self.assertEqual(world.kinds().count("borrow"), world.kinds().count("return"))

    def test_lost_front_does_not_press(self):
        world = _World(self, loads_on_enter=None, closes_on_enter=None)
        entry = world.entry()
        borrow = WindowOperator.borrow_front.side_effect
        calls = [0]

        def second_fails(h):
            calls[0] += 1
            return (False, None) if calls[0] >= 2 else borrow(h)
        WindowOperator.borrow_front.side_effect = second_fails
        self.assertEqual(entry.load_code(CODE, world.log_path), "unconfirmed")
        self.assertEqual(self._enters(world), 1, "前面にできなければ押さない")
        self.assertIn("⚠ 入力欄が開いたままです（手で Enter を押してください）", world.logs)

    def test_another_window_in_front_does_not_get_enter(self):
        world = _World(self, loads_on_enter=None, closes_on_enter=None)
        entry = world.entry()
        borrow = WindowOperator.borrow_front.side_effect
        calls = [0]

        def borrowed_but_lost(h):
            calls[0] += 1
            ok, loan = borrow(h)
            if calls[0] >= 2:
                world.front = 0x900              # 借りたが、すぐ別の窓が前に出た
            return ok, loan
        WindowOperator.borrow_front.side_effect = borrowed_but_lost
        entry.load_code(CODE, world.log_path)
        self.assertEqual(self._enters(world), 1)

    def test_a_stop_while_waiting_does_not_press(self):
        world = _World(self, loads_on_enter=None, closes_on_enter=None)
        entry = world.entry()
        shown = SaveLoad.input_box_shown.side_effect

        def stop_then_look(*a):
            if ("keys", "ctrl+v") in world.events:
                world.running = False
            return shown(*a)
        SaveLoad.input_box_shown.side_effect = stop_then_look
        entry.load_code(CODE, world.log_path)
        self.assertEqual(self._enters(world), 1)

    def test_no_capture_is_written_and_the_code_is_not_logged(self):
        world = _World(self, loads_on_enter=None, closes_on_enter=None)
        world.entry().load_code(CODE, world.log_path)
        self.assertGreater(world.captures_after_paste, 0, "貼った後に撮るのは矢印を見るときだけ")
        text = "\n".join(map(str, world.logs + world.written + world.events))
        self.assertNotIn(CODE, text)

    def test_only_the_arrow_check_captures_after_the_paste(self):
        src = inspect.getsource(ToNEntry.ToNEntry._paste_code)
        after = src[src.index('send_keys("ctrl+v")'):]
        self.assertNotIn("_capture_bgr", after, "貼る流れの中では撮らない")
        src = inspect.getsource(ToNEntry.ToNEntry._input_box_open)
        self.assertIn("SaveLoad.input_box_shown", src)
        self.assertIn("del bgr", src)
