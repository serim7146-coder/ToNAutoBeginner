"""画面の整理（DV）: 開始・停止を1つのボタン／キーの設定を「⌨ キーバインド」の画面へ／フリーズを1つの枠へ。
本物の Tk で App を1つ立てて見る（本物のキー・フック・窓には触らない）"""
from tests.support import *  # noqa: F401,F403


def _all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _all_widgets(child)


class _AppCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app._running = False
        self.app._start_stop_busy = False
        self.app._capturing_key = False
        self.app._refresh_start_stop_button()
        self.app.v_emergency_key.set(config.EMERGENCY_STOP_KEY)
        self.app.v_start_key.set("")
        self.app.v_suicide_cancel_key.set(config.SUICIDE_CANCEL_KEY)
        self.app.logs = []
        p = patch.object(mainGUI.App, "_log", lambda _s, m: self.app.logs.append(m))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.app, "_schedule_settings_save")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._close_dialog)
        self.addCleanup(SharedState.set_chase_enabled, True)

    def _close_dialog(self):
        dialog = getattr(self.app, "_keybind_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()

    def _dialog(self):
        self.app._open_keybind_dialog()
        self.app._keybind_dialog.withdraw()
        return self.app._keybind_dialog


class TestStartStopButton(_AppCase):
    """1つのボタンで開始・停止。途中は押せない"""

    def setUp(self):
        super().setUp()
        self.rearm = []
        p = patch.object(self.app, "after",
                         lambda ms, fn, *a: self.rearm.append((ms, fn, a)))
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

        def start():
            self.calls.append("start")
            self.app._running = True
            self.app._refresh_start_stop_button()

        def stop():
            self.calls.append("stop")
            self.app._running = False
            self.app._refresh_start_stop_button()
        for name, fn in (("_start", start), ("_stop", stop)):
            p = patch.object(self.app, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def _button(self):
        b = self.app.btn_start_stop
        return b.cget("text"), b.cget("fg"), str(b.cget("state"))

    def _rearm(self):
        rearm, self.rearm = self.rearm, []
        self.assertEqual([ms for ms, _f, _a in rearm], [config.START_STOP_REARM_MS])
        for _ms, fn, args in rearm:
            fn(*args)

    def test_only_one_button_and_no_separate_stop_button(self):
        texts = [w.cget("text") for w in _all_widgets(self.app)
                 if isinstance(w, (tk.Button, ttk.Button))]
        self.assertEqual(texts.count("▶ マクロ開始"), 1, texts)
        self.assertNotIn("■ 停止", texts, "止まっているときは停止の表示は無い")
        self.assertFalse(hasattr(self.app, "btn_start") or hasattr(self.app, "btn_stop"))

    def test_start_then_stop_with_the_same_button(self):
        self.assertEqual(self._button(), ("▶ マクロ開始", config.GUI_GRN, "normal"))

        self.app.btn_start_stop.invoke()
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(self._button(), ("■ 停止", config.GUI_RED, "disabled"), "途中は押せない")
        self._rearm()
        self.assertEqual(self._button(), ("■ 停止", config.GUI_RED, "normal"))

        self.app.btn_start_stop.invoke()
        self.assertEqual(self.calls, ["start", "stop"])
        self._rearm()
        self.assertEqual(self._button(), ("▶ マクロ開始", config.GUI_GRN, "normal"))

    def test_the_colour_changes(self):
        start_bg = self.app.btn_start_stop.cget("bg")
        self.app.btn_start_stop.invoke()
        self.assertNotEqual(self.app.btn_start_stop.cget("bg"), start_bg)

    def test_pressing_again_in_the_middle_does_nothing(self):
        """二度押しで開始・停止が続けて走らない（積まれたクリックも、直に呼ばれても）"""
        self.app.btn_start_stop.invoke()
        self.app.btn_start_stop.invoke()            # disabled の間
        self.app._on_start_stop_button()             # 直に呼ばれても
        self.assertEqual(self.calls, ["start"])
        self._rearm()
        self.app.btn_start_stop.invoke()
        self.assertEqual(self.calls, ["start", "stop"])

    def test_a_start_that_fails_comes_back_to_start(self):
        """窓が無くて始まらなかった・例外でも、押せないままにならない"""
        with patch.object(self.app, "_start", lambda: None):
            self.app.btn_start_stop.invoke()
        self._rearm()
        self.assertEqual(self._button(), ("▶ マクロ開始", config.GUI_GRN, "normal"))
        with patch.object(self.app, "_start", MagicMock(side_effect=RuntimeError("x"))):
            with self.assertRaises(RuntimeError):
                self.app._on_start_stop_button()
        self._rearm()
        self.assertEqual(self._button()[2], "normal")

    def test_the_real_stop_puts_the_button_back(self):
        """本物の _stop（重いところは止める）の最後でボタンが戻る"""
        stop = mainGUI.App._stop
        self.app._running = True
        self.app._refresh_start_stop_button()
        with patch.object(self.app, "_stop_window_volume"), \
             patch.object(self.app, "_release_suicide_keys"), \
             patch.object(self.app, "_show_own_windows_again"), \
             patch.object(mainGUI.Recorder, "stop_all"), \
             patch.object(self.app, "monitors", []):
            stop(self.app)
        self.assertEqual(self._button(), ("▶ マクロ開始", config.GUI_GRN, "normal"))

    def test_the_real_start_shows_stop(self):
        """本物の _start（窓1つ・監視は偽物）の最後で「■ 停止」になる"""
        start = mainGUI.App._start
        tab = MagicMock(idx=0, osc_in=0, osc_out=0)
        cfg = WindowConfig()
        cfg.hwnd = 0x1234
        cfg.log_path = Path("output_log.txt")
        tab.get_config.return_value = (cfg, None)
        with patch.object(self.app, "tabs", [tab]), \
             patch.object(self.app, "monitors", []), \
             patch.object(self.app, "_resolve_tab_ports"), \
             patch.object(self.app, "_apply_obs_settings"), \
             patch.object(self.app, "_start_window_volume"), \
             patch.object(self.app, "_debug_start"), \
             patch.object(mainGUI.LogMonitor, "LogMonitor"), \
             patch.object(mainGUI.SharedState, "register_window_hwnd"):
            start(self.app)
        self.addCleanup(setattr, self.app, "_running", False)
        self.assertTrue(self.app._running)
        self.assertEqual(self._button()[:2], ("■ 停止", config.GUI_RED))


class TestStartKeyOnlyStarts(_AppCase):
    """マクロ開始のキーは開始だけ（依頼者: 開始の暴発が大変まずい）"""

    def setUp(self):
        super().setUp()
        self.started = MagicMock()
        self.stopped = MagicMock()
        for name, mock in (("_start", self.started), ("_stop", self.stopped)):
            p = patch.object(self.app, name, mock)
            p.start()
            self.addCleanup(p.stop)

    def test_while_running_it_does_nothing(self):
        self.app._running = True
        self.app._on_start_key("f9")
        self.started.assert_not_called()
        self.stopped.assert_not_called()
        self.assertIn("[マクロ開始] いま押せません（動作中か起動中）", self.app.logs)

    def test_while_starting_or_stopping_it_does_nothing(self):
        self.app._start_stop_busy = True
        self.app._on_start_key("f9")
        self.started.assert_not_called()

    def test_when_stopped_it_starts(self):
        self.app._on_start_key("f9")
        self.started.assert_called_once_with()
        self.stopped.assert_not_called()


class TestKeybindDialog(_AppCase):
    def setUp(self):
        super().setUp()
        p = patch.object(mainGUI.threading, "Thread", RunNow)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.app, "after", lambda _ms, fn, *a: fn(*a))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.app, "_hook_suicide_cancel_key")
        p.start()
        self.addCleanup(p.stop)

    def _capture(self, target, key):
        with patch.object(HotKey, "capture", return_value=key):
            self.app._begin_capture_key(target)

    def test_the_main_screen_has_no_key_buttons(self):
        texts = [w.cget("text") for w in _all_widgets(self.app)
                 if isinstance(w, (tk.Button, ttk.Button))]
        for text in ("キーを押して設定", "解除", "変更"):
            self.assertNotIn(text, texts)
        self.assertEqual(texts.count("⌨ キーバインド"), 1)
        row = self.app.btn_start_stop.master
        self.assertIn("⌨ キーバインド", [w.cget("text") for w in row.pack_slaves()])
        self.assertEqual(row.pack_slaves()[-1].cget("text"), "報告", "報告は行の最後のまま")

    def test_the_main_screen_shows_the_keys_in_one_line(self):
        self.app.v_emergency_key.set("ctrl+p")
        self.app._refresh_emergency_key_label()
        self.assertEqual(self.app.lbl_keys.cget("text"),
                         "緊急停止: Ctrl+Pキー・自爆キャンセル: ^キー・マクロ開始: 未設定")

    def test_the_button_opens_the_dialog_once(self):
        button = next(w for w in self.app.btn_start_stop.master.pack_slaves()
                      if w.cget("text") == "⌨ キーバインド")
        button.invoke()
        dialog = self.app._keybind_dialog
        dialog.withdraw()
        self.assertEqual(dialog.title(), "キーバインド")
        button.invoke()
        self.assertIs(self.app._keybind_dialog, dialog, "開いていれば同じ画面")

    def test_the_rows(self):
        self._dialog()
        self.assertEqual(self.app.lbl_start_key.cget("text"), "マクロ開始: 未設定")
        self.assertEqual(self.app.lbl_emergency.cget("text"),
                         f"緊急停止: {HotKey.display(config.EMERGENCY_STOP_KEY)}キー")
        self.assertEqual(self.app.lbl_suicide_cancel_key.cget("text"), "自爆キャンセル: ^キー")
        for name in ("btn_capture_start_key", "btn_capture_key", "btn_capture_cancel_key"):
            self.assertEqual(getattr(self.app, name).cget("text"), "変更", name)
        self.assertEqual(self.app.btn_clear_start_key.cget("text"), "解除")
        clears = [w for w in _all_widgets(self.app._keybind_dialog)
                  if isinstance(w, ttk.Button) and w.cget("text") == "解除"]
        self.assertEqual(clears, [self.app.btn_clear_start_key], "解除はマクロ開始だけ")

    def test_change_and_clear_the_start_key(self):
        self._dialog()
        with patch.object(HotKey, "capture", return_value="f9"):
            self.app.btn_capture_start_key.invoke()          # 画面の「変更」
        self.assertEqual(self.app.v_start_key.get(), "f9")
        self.assertEqual(self.app.lbl_start_key.cget("text"), "マクロ開始: F9キー")
        self.assertIn("マクロ開始: F9キー", self.app.lbl_keys.cget("text"))
        self.assertEqual(self.app.btn_capture_start_key.cget("text"), "変更")
        self.assertEqual(str(self.app.btn_capture_start_key.cget("state")), "normal")

        self.app.btn_clear_start_key.invoke()
        self.assertEqual(self.app.v_start_key.get(), "")
        self.assertIn("マクロ開始: 未設定", self.app.lbl_keys.cget("text"))

    def test_change_the_stop_and_cancel_keys(self):
        self._dialog()
        self._capture("stop", "f8")
        self._capture("cancel", "f7")
        self.assertEqual(self.app.v_emergency_key.get(), "f8")
        self.assertEqual(self.app.v_suicide_cancel_key.get(), "f7")
        self.assertEqual(self.app.lbl_keys.cget("text"),
                         "緊急停止: F8キー・自爆キャンセル: F7キー・マクロ開始: 未設定")

    def test_the_overlap_check_is_the_same(self):
        self._dialog()
        self._capture("start", config.EMERGENCY_STOP_KEY)
        self.assertEqual(self.app.v_start_key.get(), "")
        self.assertTrue(any("緊急停止キーと同じ" in m for m in self.app.logs), self.app.logs)

    def test_the_keys_are_saved_under_the_same_names(self):
        self._dialog()
        self._capture("start", "f9")
        self._capture("stop", "f8")
        self._capture("cancel", "f7")
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update):
            self.app._save_launch_settings()
        self.assertEqual((stored["start_key"], stored["emergency_stop_key"],
                          stored["suicide_cancel_key"]), ("f9", "f8", "f7"))

    def test_a_closed_dialog_still_updates_the_main_line(self):
        """変更の途中で画面を閉じても落ちない。メインの1行は合う"""
        self._dialog()
        self.app._capturing_key = True
        self._close_dialog()
        self.app._finish_capture_key("f9", "start")
        self.assertEqual(self.app.v_start_key.get(), "f9")
        self.assertIn("マクロ開始: F9キー", self.app.lbl_keys.cget("text"))

    def test_chase_is_shown_and_its_check_moved_here(self):
        self._dialog()
        self.assertIn("F1", self.app.lbl_chase_keys.cget("text"))
        self.assertIn("F2", self.app.lbl_chase_keys.cget("text"))
        checks = [w for w in _all_widgets(self.app._keybind_dialog)
                  if isinstance(w, ttk.Checkbutton) and w.cget("text") == "チェイス（F1・F2）を使う"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].winfo_manager(), "pack", "画面に置かれている")
        main =[w for w in _all_widgets(self.app) if isinstance(w, ttk.Checkbutton)
                and w.winfo_toplevel() is self.app and w.cget("text") == "チェイス（F1・F2）を使う"]
        self.assertEqual(main, [], "メインの画面からはなくす")
        changes = [w for w in _all_widgets(self.app._keybind_dialog) if isinstance(w, ttk.Button)
                   and w.cget("text") == "変更" and w.grid_info().get("row") not in (0, 1, 2)]
        self.assertEqual(changes, [], "チェイスのキーは変えられない")
        with patch.object(self.app, "_hook_chase_keys"), patch.object(self.app, "_unhook_chase_keys"):
            checks[0].invoke()                       # 切る
            self.assertFalse(SharedState.get_chase_enabled())
            checks[0].invoke()                       # 戻す
            self.assertTrue(SharedState.get_chase_enabled())
        SharedState.set_chase_enabled(False)
        self.app._refresh_chase_check()              # 読み込みで変わったとき
        self.assertEqual(str(checks[0].instate(["selected"])), "False")


class TestFreezeFrame(_AppCase):
    def setUp(self):
        super().setUp()
        for var in (self.app.v_freeze_8pages, self.app.v_freeze_punish,
                    *self.app.v_freeze_rounds.values()):
            var.set(False)
        self.app._apply_freeze_settings()
        self.addCleanup(SharedState.set_freeze_rounds, [])
        self.addCleanup(SharedState.set_freeze_on_8pages, False)
        self.addCleanup(SharedState.set_freeze_on_punish, False)

    def _rows(self):
        frame = self.app.freeze_frame
        self.assertEqual(frame.cget("text"), "フリーズ")
        rows = []
        for row in frame.pack_slaves():
            items = row.pack_slaves()
            rows.append((items[0].cget("text"), [w for w in items[1:]]))
        return rows

    def test_two_rows(self):
        rows = self._rows()
        self.assertEqual([title for title, _ in rows], ["先読み（速度検知）:", "ラウンド突入:"])
        pre, entry = rows[0][1], rows[1][1]
        self.assertEqual([w.cget("text") for w in pre], ["8 Pages", "Punished"])
        self.assertEqual([str(w.cget("variable")) for w in pre],
                         [str(self.app.v_freeze_8pages), str(self.app.v_freeze_punish)])
        self.assertEqual([w.cget("text") for w in entry], list(self.app.v_freeze_rounds))
        self.assertEqual(list(self.app.v_freeze_rounds),
                         ["Alternate", "Midnight", "Unbound", "Fog", "Ghost", "8 Pages", "Punished"])
        self.assertEqual([str(w.cget("variable")) for w in entry],
                         [str(v) for v in self.app.v_freeze_rounds.values()])

    def test_right_below_the_speed_detect_button(self):
        order = self.app.pack_slaves()
        self.assertEqual(order.index(self.app.freeze_frame),
                         order.index(self.app.btn_speed_detect.master) + 1)

    def test_no_freeze_checks_left_outside(self):
        outside = [w.cget("text") for w in _all_widgets(self.app) if isinstance(w, ttk.Checkbutton)
                   and "フリーズ" in w.cget("text")]
        self.assertEqual(outside, [])

    def test_the_checks_work_as_before(self):
        pre = self._rows()[0][1]
        pre[1].invoke()                                       # Punished
        self.assertTrue(SharedState.get_freeze_on_punish())
        self.assertTrue(self.app.v_freeze_rounds["Punished"].get(), "突入側も入る（今までどおり）")
        entry = self._rows()[1][1]
        entry[3].invoke()                                     # Fog
        self.assertIn("Fog", SharedState.get_freeze_rounds())

    def test_the_saved_settings_load_and_save_under_the_same_names(self):
        data = {"freeze_8pages": True, "freeze_punish": False, "freeze_rounds": ["Fog", "Ghost"],
                "start_key": "f9", "chase_enabled": False}
        with patch.object(mainGUI, "load_settings", return_value=data):
            self.app._load_saved_settings()
        self.addCleanup(self.app.v_start_key.set, "")
        self.assertTrue(self.app.v_freeze_8pages.get())
        self.assertEqual({n for n, v in self.app.v_freeze_rounds.items() if v.get()}, {"Fog", "Ghost"})
        self.assertEqual(self.app.v_start_key.get(), "f9")
        self.assertFalse(self.app.v_chase_enabled.get())
        self.assertIn("マクロ開始: F9キー", self.app.lbl_keys.cget("text"))
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        self.assertEqual((stored["freeze_8pages"], stored["freeze_punish"], stored["freeze_rounds"],
                          stored["start_key"], stored["chase_enabled"]),
                         (True, False, ["Fog", "Ghost"], "f9", False))
