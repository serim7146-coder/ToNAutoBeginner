"""画面の整理（DV・DV2）: 開始・停止を1つのボタン／キーの設定を「⌨ キーバインド」の画面へ／フリーズを1つの枠へ。
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
        self.app.v_chase_cw_key.set(config.CHASE_CW_KEY)
        self.app.v_chase_ccw_key.set(config.CHASE_CCW_KEY)
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
                         "マクロ開始: 未設定・停止: Ctrl+Pキー・自爆キャンセル: ^キー・チェイス: F1/F2キー")

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
        """5つのキー。どれも「変更」と「解除」がある（DV2）"""
        self._dialog()
        self.assertEqual(self.app.lbl_start_key.cget("text"), "マクロ開始: 未設定")
        self.assertEqual(self.app.lbl_emergency.cget("text"),
                         f"停止: {HotKey.display(config.EMERGENCY_STOP_KEY)}キー")
        self.assertEqual(self.app.lbl_suicide_cancel_key.cget("text"), "自爆キャンセル: ^キー")
        self.assertEqual(self.app.lbl_chase_cw_key.cget("text"), "チェイス（時計回り）: F1キー")
        self.assertEqual(self.app.lbl_chase_ccw_key.cget("text"), "チェイス（反時計回り）: F2キー")
        for row, target in enumerate(("start", "stop", "cancel", "cw", "ccw")):
            names = mainGUI.App.KEY_BUTTONS[target]
            capture, clear = getattr(self.app, names["capture"]), getattr(self.app, names["clear"])
            self.assertEqual((capture.cget("text"), clear.cget("text")), ("変更", "解除"), target)
            self.assertEqual((int(capture.grid_info()["row"]), int(clear.grid_info()["row"])),
                             (row, row), target)

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
                         "マクロ開始: 未設定・停止: F8キー・自爆キャンセル: F7キー・チェイス: F1/F2キー")

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

    def test_the_chase_check_is_here(self):
        """「チェイスを使う」のチェックはキーバインドの画面だけ（DV2 で名前を短く）"""
        self._dialog()
        checks = [w for w in _all_widgets(self.app._keybind_dialog)
                  if isinstance(w, ttk.Checkbutton) and w.cget("text") == "チェイスを使う"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].winfo_manager(), "pack", "画面に置かれている")
        main = [w for w in _all_widgets(self.app) if isinstance(w, ttk.Checkbutton)
                and w.winfo_toplevel() is self.app and "チェイス" in w.cget("text")]
        self.assertEqual(main, [], "メインの画面には無い")
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
        """速度検知の行と、ラウンド突入の行（EZ: 12 個になったので 7 個で折り返す。2 行目は見出しを空ける）"""
        rows = self._rows()
        self.assertEqual([title for title, _ in rows], ["速度検知:", "ラウンド突入:", ""])
        pre, entry = rows[0][1], rows[1][1] + rows[2][1]
        self.assertEqual([len(rows[1][1]), len(rows[2][1])], [7, 5])
        self.assertEqual([w.cget("text") for w in pre], ["8 Pages", "Punished"])
        self.assertEqual([str(w.cget("variable")) for w in pre],
                         [str(self.app.v_freeze_8pages), str(self.app.v_freeze_punish)])
        self.assertEqual([w.cget("text") for w in entry], list(self.app.v_freeze_rounds))
        self.assertEqual(list(self.app.v_freeze_rounds),
                         ["Alternate", "Midnight", "Unbound", "Fog", "Ghost", "8 Pages", "Punished",
                          "Sabotage murder", "Sabotage star", "Cracked", "Double Trouble", "Bloodbath EX"])
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


# ── DV2（依頼者が DV の画面を見て頼んだ手直し）──────────────────────
class TestDv2MainScreen(_AppCase):
    def _row(self):
        return self.app.btn_start_stop.master.pack_slaves()

    def test_the_button_order(self):
        self.assertEqual([w.cget("text") for w in self._row()],
                         ["▶ マクロ開始", "📋 オーバーレイ", "統計", "⌨ キーバインド", "報告"])

    def test_the_start_button_is_the_biggest_and_keeps_its_size(self):
        self.app.update_idletasks()
        start = self.app.btn_start_stop
        size = (start.winfo_reqwidth(), start.winfo_reqheight())
        for other in self._row()[1:]:
            self.assertGreater(size[0], other.winfo_reqwidth(), other.cget("text"))
            self.assertGreater(size[1], other.winfo_reqheight(), other.cget("text"))
        self.app._running = True
        self.app._refresh_start_stop_button()
        self.app.update_idletasks()
        self.assertEqual((start.winfo_reqwidth(), start.winfo_reqheight()), size,
                         "止まっている／動いているで大きさが変わらない")

    def test_the_short_check_names(self):
        """DV3: 見出しを無くして短い名前だけ（詳しくは README）"""
        row = next(w for w in self.app.pack_slaves()
                   if any(isinstance(c, ttk.Checkbutton) and c.cget("text") == "アイテムを落とす"
                          for c in w.winfo_children()))
        items = [(type(w).__name__, w.cget("text")) for w in row.pack_slaves()]
        self.assertEqual(items, [("Checkbutton", "アイテムを落とす"), ("Checkbutton", "窓位置リセット"),
                                 ("Checkbutton", "Runリスポーン"), ("Checkbutton", "霧即時判定"),
                                 ("Checkbutton", "Utilsの判断に従う")])        # EB
        variables = [str(w.cget("variable")) for w in row.pack_slaves()
                     if isinstance(w, ttk.Checkbutton)]
        self.assertEqual(variables, [str(v) for v in (self.app.v_continue_drop_item,
                                                      self.app.v_continue_restore_window,
                                                      self.app.v_run_respawn,
                                                      self.app.v_fog_early_read,
                                                      self.app.v_follow_utils)])

    def test_the_freeze_row_is_called_speed_detect(self):
        row = self.app.freeze_frame.pack_slaves()[0]
        self.assertEqual(row.pack_slaves()[0].cget("text"), "速度検知:")


class TestDv2Keys(_AppCase):
    def setUp(self):
        super().setUp()
        for target, kwargs in ((mainGUI.threading, {"Thread": RunNow}),
                               (self.app, {"after": lambda _ms, fn, *a: fn(*a)})):
            for name, value in kwargs.items():
                p = patch.object(target, name, value)
                p.start()
                self.addCleanup(p.stop)
        p = patch.object(self.app, "_hook_suicide_cancel_key")
        self.hook_cancel = p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.app, "_hook_chase_keys")
        self.hook_chase = p.start()
        self.addCleanup(p.stop)
        self._dialog()

    def _capture(self, target, key):
        with patch.object(HotKey, "capture", return_value=key):
            getattr(self.app, mainGUI.App.KEY_BUTTONS[target]["capture"]).invoke()

    def _clear(self, target):
        getattr(self.app, mainGUI.App.KEY_BUTTONS[target]["clear"]).invoke()

    def _hooked_chase_keys(self):
        """本物の _hook_chase_keys が登録するキー（keyboard は偽物）"""
        keyboard = MagicMock()
        with patch.object(mainGUI, "keyboard", keyboard), \
             patch.object(self.app, "_chase_hooks", []), \
             patch.object(self.app, "_unhook_chase_keys"):
            mainGUI.App._hook_chase_keys(self.app)
        return [c.args[0] for c in keyboard.hook_key.call_args_list]

    # ── チェイスのキーを変える ────────────────────
    def test_the_chase_keys_can_be_changed_and_are_rehooked(self):
        self._capture("cw", "f6")
        self._capture("ccw", "f5")
        self.assertEqual((self.app.v_chase_cw_key.get(), self.app.v_chase_ccw_key.get()),
                         ("f6", "f5"))
        self.assertEqual(self.hook_chase.call_count, 2, "変えるたびにフックし直す")
        self.assertEqual(self.app.lbl_chase_cw_key.cget("text"), "チェイス（時計回り）: F6キー")
        self.assertTrue(self.app.lbl_keys.cget("text").endswith("・チェイス: F6/F5キー"),
                        self.app.lbl_keys.cget("text"))
        self.assertEqual(self._hooked_chase_keys(), ["f6", "f5"])

    def test_the_chase_key_overlap_is_checked(self):
        for key, other in ((config.EMERGENCY_STOP_KEY, "緊急停止"),
                           (config.SUICIDE_CANCEL_KEY, "自爆キャンセル"),
                           (config.CHASE_CCW_KEY, "チェイスのもう一方")):
            self.app.logs.clear()
            self._capture("cw", key)
            self.assertEqual(self.app.v_chase_cw_key.get(), config.CHASE_CW_KEY, key)
            self.assertTrue(any(f"{other}のキーと同じ" in m for m in self.app.logs), self.app.logs)
        self.app.v_start_key.set("f9")
        self._capture("ccw", "f9")
        self.assertEqual(self.app.v_chase_ccw_key.get(), config.CHASE_CCW_KEY, "マクロ開始とも")
        self.hook_chase.assert_not_called()

    def test_other_keys_cannot_take_a_chase_key_while_chase_is_used(self):
        self._capture("stop", config.CHASE_CW_KEY)
        self._capture("start", config.CHASE_CCW_KEY)
        self._capture("cancel", config.CHASE_CW_KEY)
        self.assertEqual((self.app.v_emergency_key.get(), self.app.v_start_key.get(),
                          self.app.v_suicide_cancel_key.get()),
                         (config.EMERGENCY_STOP_KEY, "", config.SUICIDE_CANCEL_KEY))
        SharedState.set_chase_enabled(False)              # 使わないなら空いている（DT）
        self._capture("start", config.CHASE_CCW_KEY)
        self.assertEqual(self.app.v_start_key.get(), config.CHASE_CCW_KEY)

    # ── 解除 ──────────────────────────────
    def test_every_key_can_be_cleared(self):
        self.app.v_start_key.set("f9")
        with patch.object(mainGUI.messagebox, "askyesno", return_value=True):
            for target in ("start", "stop", "cancel", "cw", "ccw"):
                self._clear(target)
        self.assertEqual([v.get() for v in (self.app.v_start_key, self.app.v_emergency_key,
                                            self.app.v_suicide_cancel_key, self.app.v_chase_cw_key,
                                            self.app.v_chase_ccw_key)], [""] * 5)
        self.assertEqual(self.app.lbl_keys.cget("text"),
                         "マクロ開始: 未設定・停止: 未設定・自爆キャンセル: 未設定・チェイス: 未設定")
        self.assertEqual(self.app.lbl_emergency.cget("text"), "停止: 未設定")
        self.hook_cancel.assert_called_once_with()
        self.assertEqual(self.hook_chase.call_count, 2, "チェイスは解除のたびにフックし直す")
        self.assertEqual(self._hooked_chase_keys(), [], "両方解除したら何も拾わない")

    def test_clearing_the_emergency_key_asks_first(self):
        with patch.object(mainGUI.messagebox, "askyesno", return_value=False) as ask:
            self._clear("stop")
        ask.assert_called_once()
        self.assertEqual(ask.call_args.args[0], "停止のキーの解除")          # DV3:「停止」
        self.assertIn("停止のキーが無くなります", ask.call_args.args[1])
        self.assertNotIn("緊急", ask.call_args.args[1])
        self.assertEqual(self.app.v_emergency_key.get(), config.EMERGENCY_STOP_KEY,
                         "いいえなら解除しない")
        with patch.object(mainGUI.messagebox, "askyesno", return_value=True):
            self._clear("stop")
        self.assertEqual(self.app.v_emergency_key.get(), "")

    def test_the_other_keys_are_cleared_without_asking(self):
        with patch.object(mainGUI.messagebox, "askyesno") as ask:
            for target in ("cancel", "cw"):
                self._clear(target)
        ask.assert_not_called()

    def test_a_cleared_emergency_key_does_nothing_and_is_not_defaulted(self):
        self.app.v_emergency_key.set("")
        with patch.object(mainGUI, "keyboard") as keyboard, \
             patch.object(self.app, "_stop") as stop:
            keyboard.is_pressed.return_value = True
            mainGUI.App._on_stop_start_key_event(self.app)
        self.assertNotIn("", [c.args[0] for c in keyboard.is_pressed.call_args_list])
        stop.assert_not_called()
        self.assertEqual(self.app.v_emergency_key.get(), "", "既定値へ戻さない")

    def test_a_cleared_chase_key_is_not_hooked(self):
        self._clear("cw")
        self.assertEqual(self._hooked_chase_keys(), [config.CHASE_CCW_KEY])

    # ── 保存・読み込み ────────────────────────
    def _load(self, data):
        """壊れたキーを見分けるのは本物の keyboard（キーを送らない・フックしない）"""
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        with patch.object(HotKey, "keyboard", real),              patch.object(mainGUI, "load_settings", return_value=data):
            self.app._load_saved_settings()

    def _saved(self):
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            self.app._save_launch_settings()
        return stored

    def test_the_chase_keys_are_saved_and_loaded(self):
        self._capture("cw", "f6")
        stored = self._saved()
        self.assertEqual((stored["chase_cw_key"], stored["chase_ccw_key"]),
                         ("f6", config.CHASE_CCW_KEY))
        self._load({"chase_cw_key": "f6", "chase_ccw_key": "f5"})
        self.assertEqual((self.app.v_chase_cw_key.get(), self.app.v_chase_ccw_key.get()),
                         ("f6", "f5"))

    def test_an_old_file_gives_f1_and_f2(self):
        self.app.v_chase_cw_key.set("f6")
        self._load({})
        self.assertEqual((self.app.v_chase_cw_key.get(), self.app.v_chase_ccw_key.get()),
                         ("f1", "f2"))
        self.assertEqual(self.app.lbl_chase_cw_key.cget("text"), "チェイス（時計回り）: F1キー")

    def test_broken_cleared_and_overlapping_values(self):
        self._load({"chase_cw_key": "zzz", "chase_ccw_key": ""})
        self.assertEqual((self.app.v_chase_cw_key.get(), self.app.v_chase_ccw_key.get()),
                         ("f1", ""), "壊れた値は既定・空は解除のまま")
        self._load({"emergency_stop_key": "f1"})
        self.assertEqual((self.app.v_emergency_key.get(), self.app.v_chase_cw_key.get()),
                         ("f1", ""), "緊急停止と重なるチェイスのキーは未設定（緊急停止はそのまま）")
        self._load({"chase_cw_key": "f2"})
        self.assertEqual((self.app.v_chase_cw_key.get(), self.app.v_chase_ccw_key.get()),
                         ("f2", ""), "2つが重なれば後の方を未設定")

    def test_cleared_keys_stay_cleared_after_loading(self):
        self._load({"emergency_stop_key": "", "suicide_cancel_key": "", "chase_cw_key": "",
                    "chase_ccw_key": ""})
        self.assertEqual([v.get() for v in (self.app.v_emergency_key, self.app.v_suicide_cancel_key,
                                            self.app.v_chase_cw_key, self.app.v_chase_ccw_key)],
                         [""] * 4)
        self._load({"emergency_stop_key": "zzz", "suicide_cancel_key": "zzz"})
        self.assertEqual((self.app.v_emergency_key.get(), self.app.v_suicide_cancel_key.get()),
                         (config.EMERGENCY_STOP_KEY, config.SUICIDE_CANCEL_KEY),
                         "壊れた値は今までどおり既定")


# ── DV3（言葉を短く・キーの並びと名前）──────────────────────
class TestDv3(_AppCase):
    def test_the_dialog_rows_have_no_notes(self):
        """キーバインドの画面: マクロ開始 → 停止 → 自爆キャンセル → チェイス2つ。右の説明文は無い"""
        self._dialog()
        grid = self.app.lbl_start_key.master
        labels = sorted(((int(w.grid_info()["row"]), int(w.grid_info()["column"])), w.cget("text"))
                        for w in grid.grid_slaves() if isinstance(w, ttk.Label))
        self.assertEqual(labels, [((0, 0), "マクロ開始: 未設定"),
                                  ((1, 0), f"停止: {HotKey.display(config.EMERGENCY_STOP_KEY)}キー"),
                                  ((2, 0), "自爆キャンセル: ^キー"),
                                  ((3, 0), "チェイス（時計回り）: F1キー"),
                                  ((4, 0), "チェイス（反時計回り）: F2キー")])
        texts = [w.cget("text") for w in _all_widgets(self.app._keybind_dialog)
                 if isinstance(w, ttk.Label)]
        self.assertFalse(any("緊急" in t for t in texts), texts)

    def test_the_main_line_order_and_keys(self):
        self.app.v_start_key.set("f9")
        self.app._refresh_start_key_label()
        self.assertEqual(self.app.lbl_keys.cget("text"),
                         f"マクロ開始: F9キー・停止: {HotKey.display(config.EMERGENCY_STOP_KEY)}キー"
                         "・自爆キャンセル: ^キー・チェイス: F1/F2キー")
        self.app.v_chase_ccw_key.set("")
        self.app._refresh_chase_key_labels()
        self.assertTrue(self.app.lbl_keys.cget("text").endswith("・チェイス: F1キー/未設定"),
                        self.app.lbl_keys.cget("text"))

    def test_no_emergency_word_on_the_screens(self):
        self._dialog()
        for top in (self.app, self.app._keybind_dialog):
            texts = [w.cget("text") for w in _all_widgets(top)
                     if isinstance(w, (ttk.Label, ttk.Checkbutton, ttk.Button, tk.Button))]
            self.assertFalse(any("緊急停止" in t for t in texts), texts)

    def test_the_log_words_and_saved_names_stay(self):
        """ログの [緊急停止] と保存の名前は変えない"""
        gui = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn('self._log(f"[緊急停止] {HotKey.display(key)}キーが押されました")', gui)
        for name in ("emergency_stop_key", "continue_drop_item", "continue_restore_window",
                     "run_respawn", "fog_early_read_enabled"):
            self.assertIn(f'"{name}"', gui, name)
