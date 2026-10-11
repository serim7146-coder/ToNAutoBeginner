"""窓を整列する（DX）。本物の窓は動かさない（SetWindowPos・モニター・DWM は偽物）"""
from tests.support import *  # noqa: F401,F403
import WindowArrange

WORK = (0, 0, 2560, 1400)               # タスクバー（下 40px）を除いた作業領域
MARGINS = (7, 0, 7, 7)                  # 見えない縁（左・上・右・下）


# ── 升の割り方 ─────────────────────────────
class TestGrid(unittest.TestCase):
    def test_rows_and_columns(self):
        for n, text in ((1, "1×1"), (2, "2×1"), (3, "2×2"), (4, "2×2"), (5, "3×2"), (6, "3×2"),
                        (7, "4×2"), (8, "4×2"), (9, "3×3")):
            self.assertEqual(WindowArrange.size_text(n), text, n)

    def test_one_window_fills_the_area(self):
        self.assertEqual(WindowArrange.cells(WORK, 1), [WORK])

    def test_two_side_by_side(self):
        self.assertEqual(WindowArrange.cells(WORK, 2), [(0, 0, 1280, 1400), (1280, 0, 2560, 1400)])

    def test_eight_is_four_by_two(self):
        cells = WindowArrange.cells(WORK, 8)
        self.assertEqual(cells[:4], [(0, 0, 640, 700), (640, 0, 1280, 700),
                                     (1280, 0, 1920, 700), (1920, 0, 2560, 700)])
        self.assertEqual(cells[4:], [(0, 700, 640, 1400), (640, 700, 1280, 1400),
                                     (1280, 700, 1920, 1400), (1920, 700, 2560, 1400)])

    def test_four_and_six(self):
        self.assertEqual(WindowArrange.cells(WORK, 4),
                         [(0, 0, 1280, 700), (1280, 0, 2560, 700), (0, 700, 1280, 1400), (1280, 700, 2560, 1400)])
        self.assertEqual(WindowArrange.grid(6)[2],
                         [(0, 0, 1, 1), (1, 0, 1, 1), (2, 0, 1, 1), (0, 1, 1, 1), (1, 1, 1, 1), (2, 1, 1, 1)])

    def test_odd_counts_make_window_1_tall(self):
        self.assertEqual(WindowArrange.cells(WORK, 3),
                         [(0, 0, 1280, 1400), (1280, 0, 2560, 700), (1280, 700, 2560, 1400)])
        self.assertEqual(WindowArrange.grid(5)[2],
                         [(0, 0, 1, 2), (1, 0, 1, 1), (2, 0, 1, 1), (1, 1, 1, 1), (2, 1, 1, 1)])
        self.assertEqual(WindowArrange.grid(7)[2],
                         [(0, 0, 1, 2), (1, 0, 1, 1), (2, 0, 1, 1), (3, 0, 1, 1),
                          (1, 1, 1, 1), (2, 1, 1, 1), (3, 1, 1, 1)])

    def test_three_rows_and_their_remainders(self):
        """9 窓以上は 3 段。余りが 2 なら窓1 が左の列の 3 段ぶん、余りが 1 なら上の 2 段ぶん"""
        self.assertEqual(WindowArrange.grid(9)[:2], (3, 3))
        self.assertEqual(WindowArrange.grid(10)[2][0], (0, 0, 1, 3))
        places = WindowArrange.grid(11)[2]
        self.assertEqual(places[0], (0, 0, 1, 2))
        self.assertIn((0, 2, 1, 1), places, "左の列の下は次の窓")

    def test_cells_tile_without_gaps_or_overlaps(self):
        area = (-1920, 33, 1, 1073)                     # 割り切れない幅・高さ・負の座標
        for n in range(1, 12):
            cells = WindowArrange.cells(area, n)
            self.assertEqual(len(cells), n)
            total = sum((r - l) * (b - t) for l, t, r, b in cells)
            self.assertEqual(total, (area[2] - area[0]) * (area[3] - area[1]), n)
            for i, a in enumerate(cells):
                for b in cells[i + 1:]:
                    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
                    self.assertEqual(overlap, 0, (n, a, b))

    def test_the_invisible_border_is_added_outside(self):
        self.assertEqual(WindowArrange.outer_rect((0, 0, 1280, 700), MARGINS), (-7, 0, 1287, 707))


# ── 窓を動かす（偽物の Windows）─────────────────────
class _Windows:
    """偽の WindowOperator。起きたことは events に残す"""

    def __init__(self, test, states, rects=None):
        self.states = dict(states)
        self.rects = rects or {}
        self.events = []
        self.areas = []
        w = self
        for name, fake in (
                ("window_state", lambda h: w.states.get(h)),
                ("window_rect", lambda h: w.rects.get(h)),
                ("normal_rect", lambda h: w.rects.get(("normal", h))),
                ("work_area", lambda h: w.areas.append(h) or WORK),
                ("frame_margins", lambda h: MARGINS),
                ("rect_on_screen", lambda r: r[0] > -5000),
                ("restore_without_activating",
                 lambda h: w.events.append(("restore", h)) or w.states.__setitem__(h, "normal") or True),
                ("set_window_rect", lambda h, r: w.events.append(("rect", h, tuple(r))) or True),
                ("focus_window", lambda h: w.events.append(("focus", h)) or True)):
            p = patch.object(WindowOperator, name, side_effect=fake)
            p.start()
            test.addCleanup(p.stop)
        # 本物の SetWindowPos・ShowWindow へは届かないこと
        for name in ("SetWindowPos", "ShowWindow", "SetWindowPlacement", "MoveWindow"):
            p = patch.object(WindowOperator.win32gui, name,
                             side_effect=AssertionError(f"本物の {name} を呼んだ"))
            p.start()
            test.addCleanup(p.stop)

    def moved(self):
        return {h: r for kind, h, *r in self.events if kind == "rect" for r in r}


class _Tab:
    def __init__(self, idx, hwnd):
        self.idx, self.hwnd = idx, hwnd

    def _get_selected_hwnd(self):
        return self.hwnd


def _app(hwnds, layout=None):
    app = type("FakeApp", (), {})()
    app.tabs = [_Tab(i, h) for i, h in enumerate(hwnds)]
    app.logs = []
    app._log = app.logs.append
    app._window_layout = layout or {}
    app._schedule_settings_save = MagicMock()
    for name in ("_arrange_targets", "_arrange_windows", "_remember_window_layout",
                 "_forget_window_layout", "_refresh_forget_layout_button"):
        setattr(app, name, getattr(mainGUI.App, name).__get__(app))
    return app


HW = [0x100 + i for i in range(8)]


class TestArrange(unittest.TestCase):
    def test_six_windows_three_by_two_with_borders(self):
        windows = _Windows(self, {h: "normal" for h in HW[:6]})
        app = _app(HW[:6])
        app._arrange_windows()
        moved = windows.moved()
        self.assertEqual(moved[HW[0]], (-7, 0, 860, 707), "見えない縁を外に足す")
        self.assertEqual(moved[HW[5]], (1706 - 7, 700, 2560 + 7, 1400 + 7))
        self.assertEqual(len(moved), 6)
        self.assertEqual(app.logs[-1], "[整列] 窓を整列しました（6窓・3×2）")
        self.assertEqual(windows.areas, [HW[0]], "窓1 のモニターの作業領域")

    def test_three_windows_window_1_is_tall(self):
        windows = _Windows(self, {h: "normal" for h in HW[:3]})
        _app(HW[:3])._arrange_windows()
        self.assertEqual(windows.moved()[HW[0]], (-7, 0, 1287, 1407))

    def test_maximized_is_restored_first_and_nothing_takes_the_front(self):
        windows = _Windows(self, {HW[0]: "maximized", HW[1]: "normal"})
        _app(HW[:2])._arrange_windows()
        kinds = [(e[0], e[1]) for e in windows.events]
        self.assertLess(kinds.index(("restore", HW[0])), kinds.index(("rect", HW[0])))
        self.assertNotIn("focus", [e[0] for e in windows.events], "前面を奪わない")

    def test_fullscreen_minimized_and_missing_are_skipped(self):
        windows = _Windows(self, {HW[0]: "normal", HW[1]: "fullscreen", HW[2]: "minimized", HW[3]: "normal"})
        app = _app(HW[:5])                          # 窓5 は閉じた（状態が取れない）
        app._arrange_windows()
        self.assertEqual(set(windows.moved()), {HW[0], HW[3]})
        self.assertEqual(windows.moved()[HW[3]], (1280 - 7, 0, 2560 + 7, 1407), "残った 2 窓で並べる")
        for text in ("窓2: フルスクリーンのため整列しません", "窓3: 最小化のため整列しません",
                     "窓5: 窓が見つからないため整列しません"):
            self.assertIn(f"[整列] {text}", app.logs)

    def test_no_window(self):
        windows = _Windows(self, {})
        app = _app([0, 0])
        app._arrange_windows()
        self.assertEqual(windows.events, [])
        self.assertEqual(app.logs, ["[整列] 並べる窓がありません"])

    def test_window_1_missing_uses_the_primary_monitor(self):
        windows = _Windows(self, {HW[1]: "normal"})
        _app([0, HW[1]])._arrange_windows()
        self.assertEqual(windows.areas, [0], "窓1 が無ければ hwnd=0（主モニター）")


class TestRememberedLayout(unittest.TestCase):
    def test_remember_saves_normal_and_maximized_but_not_fullscreen_or_minimized(self):
        rects = {HW[0]: (10, 20, 110, 220), ("normal", HW[1]): (300, 0, 900, 500),
                 HW[1]: (0, 0, 2560, 1400),                 # 最大化の今の矩形は使わない
                 HW[2]: (0, 0, 2560, 1440), ("normal", HW[2]): (5, 5, 50, 50),   # 矩形は取れても覚えない
                 HW[3]: (-32000, -32000, -31840, -31972), ("normal", HW[3]): (6, 6, 60, 60)}
        windows = _Windows(self, {HW[0]: "normal", HW[1]: "maximized", HW[2]: "fullscreen",
                                  HW[3]: "minimized"}, rects)
        app = _app(HW[:4])
        app._remember_window_layout()
        self.assertEqual(app._window_layout, {"1": [10, 20, 110, 220], "2": [300, 0, 900, 500]})
        self.assertEqual(mainGUI.window_layout_setting(app._window_layout),
                         {"1": [10, 20, 100, 200], "2": [300, 0, 600, 500]}, "保存は [x, y, w, h]")
        app._schedule_settings_save.assert_called_once_with()
        self.assertIn("[整列] 配置を覚えました（窓1・窓2）", app.logs)
        self.assertEqual(windows.events, [], "覚えるときは動かさない")

    def test_nothing_to_remember_keeps_the_old_layout(self):
        _Windows(self, {HW[0]: "fullscreen"})
        app = _app(HW[:1], layout={"1": (0, 0, 10, 10)})
        app._remember_window_layout()
        self.assertEqual(app._window_layout, {"1": (0, 0, 10, 10)})
        app._schedule_settings_save.assert_not_called()

    def test_arrange_uses_the_remembered_rects_and_autos_the_rest(self):
        windows = _Windows(self, {h: "normal" for h in HW[:3]})
        app = _app(HW[:3], layout={"2": (100, 100, 900, 700)})
        app._arrange_windows()
        moved = windows.moved()
        self.assertEqual(moved[HW[1]], (100, 100, 900, 700), "覚えた矩形そのまま")
        self.assertEqual(moved[HW[0]], (-7, 0, 1287, 1407), "覚えていない 2 窓で自動（2×1）")
        self.assertEqual(moved[HW[2]], (1280 - 7, 0, 2567, 1407))
        self.assertEqual(app.logs[-1], "[整列] 窓を整列しました（2窓・2×1、覚えた配置: 窓2）")

    def test_an_offscreen_rect_is_arranged_automatically(self):
        windows = _Windows(self, {HW[0]: "normal"})
        app = _app(HW[:1], layout={"1": (-9000, 0, -8000, 500)})
        app._arrange_windows()
        self.assertEqual(windows.moved()[HW[0]], (-7, 0, 2567, 1407))

    def test_forget_goes_back_to_auto(self):
        windows = _Windows(self, {HW[0]: "normal"})
        app = _app(HW[:1], layout={"1": (100, 100, 900, 700)})
        app._forget_window_layout()
        self.assertEqual(app._window_layout, {})
        self.assertIn("[整列] 覚えた配置を消しました（自動で並べます）", app.logs)
        app._arrange_windows()
        self.assertEqual(windows.moved()[HW[0]], (-7, 0, 2567, 1407))

    def test_load_and_broken_values(self):
        self.assertEqual(mainGUI._as_window_layout({"1": [10, 20, 100, 200], "2": [0, 0, 0, 5],
                                                   "x": [0, 0, 5, 5], "3": [1, 2, 3], "4": "bad",
                                                   "5": [0, 0, True, 5]}),
                         {"1": (10, 20, 110, 220)})
        self.assertEqual(mainGUI._as_window_layout(None), {})
        self.assertEqual(mainGUI._as_window_layout([1, 2]), {})


class TestTheButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_on_the_launch_row(self):
        row = self.app.btn_launch.master
        texts = [w.cget("text") for w in row.pack_slaves()]
        self.assertIn("窓を整列", texts)
        self.assertEqual(texts[texts.index("窓を整列") + 1], "配置を覚える")
        self.assertIn(self.app.btn_forget_layout, row.pack_slaves())

    def test_the_forget_button_follows_the_layout(self):
        with patch.object(self.app, "_schedule_settings_save"), \
             patch.object(mainGUI.App, "_log", lambda *_a: None):
            self.app._window_layout = {}
            self.app._refresh_forget_layout_button()
            self.assertEqual(str(self.app.btn_forget_layout.cget("state")), "disabled")
            self.app._window_layout = {"1": (0, 0, 10, 10)}
            self.app._refresh_forget_layout_button()
            self.assertEqual(str(self.app.btn_forget_layout.cget("state")), "normal")
            self.app.btn_forget_layout.invoke()
            self.assertEqual(self.app._window_layout, {})
            self.assertEqual(str(self.app.btn_forget_layout.cget("state")), "disabled")

    def test_saved_and_loaded(self):
        self.app._window_layout = {"1": (10, 20, 110, 220)}
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        self.assertEqual(stored["window_layout"], {"1": [10, 20, 100, 200]})
        with patch.object(mainGUI, "load_settings", return_value={"window_layout": {"3": [0, 0, 50, 60]}}):
            self.app._load_saved_settings()
        self.assertEqual(self.app._window_layout, {"3": (0, 0, 50, 60)})
        self.assertEqual(str(self.app.btn_forget_layout.cget("state")), "normal")
        with patch.object(mainGUI, "load_settings", return_value={}):
            self.app._load_saved_settings()
        self.assertEqual(self.app._window_layout, {}, "無い設定ファイルは自動")


class TestTheRealCalls(unittest.TestCase):
    """WindowOperator の中身（win32 は偽物）: NOACTIVATE・作業領域・見えない縁"""

    def test_set_window_rect_does_not_activate(self):
        with patch.object(WindowOperator.win32gui, "SetWindowPos") as swp, \
             patch.object(DebugLog, "write"):
            self.assertTrue(WindowOperator.set_window_rect(0x30, (-7, 0, 860, 707)))
        swp.assert_called_once_with(0x30, 0, -7, 0, 867, 707,
                                    WindowOperator.win32con.SWP_NOZORDER | WindowOperator.win32con.SWP_NOACTIVATE)

    def test_work_area_is_rc_work_of_window_1s_monitor(self):
        with patch.object(WindowOperator.win32gui, "IsWindow", return_value=True), \
             patch.object(WindowOperator.win32api, "MonitorFromWindow", return_value="m2") as mfw, \
             patch.object(WindowOperator.win32api, "GetMonitorInfo",
                          return_value={"Monitor": (2560, 0, 5120, 1440), "Work": (2560, 0, 5120, 1400)}):
            self.assertEqual(WindowOperator.work_area(0x30), (2560, 0, 5120, 1400))
        self.assertEqual(mfw.call_args.args[0], 0x30)

    def test_work_area_without_window_1_is_the_primary_monitor(self):
        with patch.object(WindowOperator.win32api, "MonitorFromPoint", return_value="m1") as mfp, \
             patch.object(WindowOperator.win32api, "GetMonitorInfo",
                          return_value={"Monitor": (0, 0, 2560, 1440), "Work": (0, 0, 2560, 1400)}):
            self.assertEqual(WindowOperator.work_area(0), (0, 0, 2560, 1400))
        self.assertEqual(mfp.call_args.args[1], WindowOperator.win32con.MONITOR_DEFAULTTOPRIMARY)

    def test_frame_margins_from_dwm(self):
        def dwm(hwnd, attr, rect_ref, size):
            self.assertEqual(attr, WindowOperator.DWMWA_EXTENDED_FRAME_BOUNDS)
            rect = rect_ref._obj
            rect.left, rect.top, rect.right, rect.bottom = 107, 200, 993, 893
            return 0
        fake_dwm = type("Dwm", (), {"DwmGetWindowAttribute": staticmethod(dwm)})()
        fake_windll = type("WinDll", (), {"dwmapi": fake_dwm})()
        with patch.object(WindowOperator.win32gui, "GetWindowRect", return_value=(100, 200, 1000, 900)), \
             patch.object(WindowOperator.ctypes, "windll", fake_windll):
            self.assertEqual(WindowOperator.frame_margins(0x30), (7, 0, 7, 7))

    def test_rect_on_screen_uses_the_center(self):
        with patch.object(WindowOperator.win32api, "MonitorFromPoint", return_value=None) as mfp:
            self.assertFalse(WindowOperator.rect_on_screen((-9000, 0, -8000, 500)))
        self.assertEqual(mfp.call_args.args, ((-8500, 250), WindowOperator.win32con.MONITOR_DEFAULTTONULL))


class TestTheReadme(unittest.TestCase):
    def test_the_section(self):
        readme = (Path(mainGUI.__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        section = readme[readme.index("### 窓を整列"):]
        section = section[:section.index("\n### ", 1)]
        for word in ("配置を覚える", "✕", "VRAM", "8 窓は 4×2", "縦長", "[困ったとき](#困ったとき)"):
            self.assertIn(word, section, word)
