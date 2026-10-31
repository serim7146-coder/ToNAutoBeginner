"""HC: Ctrl を押しながら窓ごとの設定を変えると、全部の窓タブに同じ値が写る。
本物の App を 1 つ作って（窓は作った直後に隠れる。support）、窓のタブのチェック・ドロップダウンを手で変えたことにする。
Ctrl が押されているかは mainGUI.ctrl_held を差し替えて決める（本物のキー状態は見ない）。
保存は一時フォルダ（config.SETTINGS_PATH の差し替え）だけ。本物の入力・OSC・窓・ネットワークは使わない"""
from tests.support import *  # noqa: F401,F403
import ContinueTable
import config
import mainGUI
from ContinueTable import Store
from LogMonitor import WindowConfig

KEY = "Classic/クラシック"
PRESET = "プリセットP"
D, F = ContinueTable.DISABLED, ContinueTable.DEFAULT
ROUND = config.SKIP_ROUND_SELECTABLE[0]
CHECKS = (("auto_begin", "自動Begin"),
          ("do_skip", "自動自爆"),
          ("cancel_afk", "DTM/Waldo続行 (3クラまで)"),
          ("cancel_afk_after_unlock", "3クラ後もDTM/Waldo続行"),
          ("announce_intermission", "Intermissionアナウンス"))


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _checkbutton(tab, text, nth=0):
    """タブの中の、文字が text の Checkbutton（nth 番目。ラウンドのチェックは全自爆の格子が 0、全続行の格子が 1）"""
    found = [w for w in _descendants(tab) if isinstance(w, ttk.Checkbutton) and w.cget("text") == text]
    return found[nth]


def _var(tab, key):
    return getattr(tab, "v_" + key)


class TestCtrlBroadcast(unittest.TestCase):
    def setUp(self):
        ContinueTable.table_path().unlink(missing_ok=True)
        self.addCleanup(ContinueTable.table_path().unlink, missing_ok=True)
        self.addCleanup(ContinueTable.set_current, Store())
        store = ContinueTable.with_saved_preset(Store(), F, PRESET, {KEY: [7]})
        store = ContinueTable.with_saved_preset(store, D, PRESET, {KEY: [9]})
        ContinueTable.set_current(store)
        ContinueTable.save(store)                       # 保存内容は起動時に読まれる
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            self.app = mainGUI.App()
        self.addCleanup(self.app.destroy)
        self.assertGreaterEqual(len(self.app.tabs), 2)
        self.tabs = self.app.tabs
        self.log = patch.object(self.app, "_log").start()
        self.addCleanup(patch.stopall)
        self.ctrl = patch.object(mainGUI, "ctrl_held", return_value=False).start()

    def _store(self):
        return ContinueTable.current()

    # ── Ctrl なし: 今まで通り（その窓だけ） ─────────────────
    def test_without_ctrl_only_the_changed_window_changes(self):
        tab, others = self.tabs[0], self.tabs[1:]
        for key, text in CHECKS:
            before = [bool(_var(t, key).get()) for t in self.tabs]
            _checkbutton(tab, text).invoke()
            self.assertEqual(_var(tab, key).get(), not before[0], key)
            self.assertEqual([bool(_var(t, key).get()) for t in others], before[1:], key)
        _checkbutton(tab, ROUND, 0).invoke()
        self.assertTrue(tab.v_skip_rounds[ROUND].get())
        self.assertFalse(any(t.v_skip_rounds[ROUND].get() for t in others))
        tab.chk_ct_switch.invoke()
        self.assertTrue(ContinueTable.window_table(self._store(), 1).item_switch)
        self.assertFalse(any(ContinueTable.window_table(self._store(), t.idx + 1).item_switch for t in others))
        tab.v_ct_choice[F].set(PRESET)
        tab._on_continue_pick(F)
        self.assertEqual(ContinueTable.body_of(self._store(), 1, F), {KEY: frozenset({7})})
        self.assertTrue(all(ContinueTable.body_of(self._store(), t.idx + 1, F) == {} for t in others))
        self.log.assert_not_called()

    # ── Ctrl あり: 5 つのチェック ─────────────────────────
    def test_each_of_the_five_checks_reaches_every_window(self):
        self.ctrl.return_value = True
        for key, text in CHECKS:
            for start in (False, True):
                for t in self.tabs:
                    _var(t, key).set(start)
                _checkbutton(self.tabs[1], text).invoke()
                self.assertEqual([bool(_var(t, key).get()) for t in self.tabs], [not start] * len(self.tabs),
                                 f"{key} {start}")

    def test_the_other_checks_do_not_move(self):
        self.ctrl.return_value = True
        before = [{k: bool(_var(t, k).get()) for k, _ in CHECKS} for t in self.tabs]
        _checkbutton(self.tabs[0], "自動自爆").invoke()
        for t, was in zip(self.tabs, before):
            now = {k: bool(_var(t, k).get()) for k, _ in CHECKS}
            self.assertEqual({k: v for k, v in now.items() if k != "do_skip"},
                             {k: v for k, v in was.items() if k != "do_skip"})

    # ── Ctrl あり: ラウンドごとの全自爆・全続行 ─────────────
    def test_skip_round_reaches_every_window_and_clears_the_same_continue_round(self):
        self.ctrl.return_value = True
        for t in self.tabs:
            t.v_continue_rounds[ROUND].set(True)
        _checkbutton(self.tabs[0], ROUND, 0).invoke()
        for t in self.tabs:
            self.assertTrue(t.v_skip_rounds[ROUND].get())
            self.assertFalse(t.v_continue_rounds[ROUND].get(), "全続行の同じラウンドは外れる")

    def test_continue_round_reaches_every_window_and_clears_the_same_skip_round(self):
        self.ctrl.return_value = True
        for t in self.tabs:
            t.v_skip_rounds[ROUND].set(True)
        _checkbutton(self.tabs[1], ROUND, 1).invoke()
        for t in self.tabs:
            self.assertTrue(t.v_continue_rounds[ROUND].get())
            self.assertFalse(t.v_skip_rounds[ROUND].get())

    def test_turning_a_round_off_reaches_every_window(self):
        self.ctrl.return_value = True
        for t in self.tabs:
            t.v_skip_rounds[ROUND].set(True)
        _checkbutton(self.tabs[0], ROUND, 0).invoke()
        self.assertFalse(any(t.v_skip_rounds[ROUND].get() for t in self.tabs))

    def test_other_rounds_are_left_alone(self):
        self.ctrl.return_value = True
        other = config.SKIP_ROUND_SELECTABLE[1]
        self.tabs[1].v_skip_rounds[other].set(True)
        _checkbutton(self.tabs[0], ROUND, 0).invoke()
        self.assertTrue(self.tabs[1].v_skip_rounds[other].get())
        self.assertFalse(self.tabs[0].v_skip_rounds[other].get())

    # ── Ctrl あり: 続行表の行 ─────────────────────────────
    def test_item_switch_reaches_every_window_and_is_saved(self):
        self.ctrl.return_value = True
        self.tabs[1].chk_ct_switch.invoke()
        for t in self.tabs:
            self.assertTrue(t.v_ct_switch.get(), t.idx)
        for store in (self._store(), ContinueTable.load()):
            self.assertEqual([ContinueTable.window_table(store, t.idx + 1).item_switch for t in self.tabs],
                             [True] * len(self.tabs), "保存内容（windows）が全窓とも同じ")
        self.tabs[0].chk_ct_switch.invoke()
        self.assertEqual([ContinueTable.window_table(ContinueTable.load(), t.idx + 1).item_switch
                          for t in self.tabs], [False] * len(self.tabs))

    def test_default_continue_preset_reaches_every_window(self):
        self.ctrl.return_value = True
        self.tabs[1].v_ct_choice[F].set(PRESET)
        self.tabs[1]._on_continue_pick(F)
        for store in (self._store(), ContinueTable.load()):
            for t in self.tabs:
                self.assertEqual(ContinueTable.body_of(store, t.idx + 1, F), {KEY: frozenset({7})}, t.idx)
                self.assertEqual(ContinueTable.body_of(store, t.idx + 1, D), {}, "続行無効は変わらない")
        self.assertEqual([t.v_ct_choice[F].get() for t in self.tabs], [PRESET] * len(self.tabs), "行の表示も合う")

    def test_disabled_preset_and_none_reach_every_window(self):
        self.ctrl.return_value = True
        self.tabs[0].v_ct_choice[D].set(PRESET)
        self.tabs[0]._on_continue_pick(D)
        for t in self.tabs:
            self.assertEqual(ContinueTable.body_of(self._store(), t.idx + 1, D), {KEY: frozenset({9})})
        self.tabs[1].v_ct_choice[D].set(ContinueTable.NONE_LABEL)
        self.tabs[1]._on_continue_pick(D)
        for t in self.tabs:
            self.assertEqual(ContinueTable.body_of(self._store(), t.idx + 1, D), {}, "「なし」は全窓の表を空にする")
            self.assertEqual(t.v_ct_choice[D].get(), ContinueTable.NONE_LABEL)

    def test_the_dash_is_not_copied(self):
        self.ctrl.return_value = True
        tab = self.tabs[0]
        tab._on_broadcast = MagicMock()
        tab._on_continue_choice = MagicMock()           # 「-」は選択肢に無いので、今まで通りの受け口に渡るだけ
        tab.v_ct_choice[F].set(ContinueTable.DASH_LABEL)
        tab._on_continue_pick(F)
        tab._on_broadcast.assert_not_called()
        self.log.assert_not_called()

    def test_ctrl_is_read_at_the_moment_of_the_change(self):
        self.tabs[0].v_ct_choice[F].set(PRESET)
        self.tabs[0]._on_continue_pick(F)               # Ctrl なし: その窓だけ
        self.assertEqual(ContinueTable.body_of(self._store(), 2, F), {})
        self.ctrl.return_value = True
        self.tabs[0].v_ct_choice[D].set(PRESET)
        self.tabs[0]._on_continue_pick(D)               # Ctrl あり: 全窓
        self.assertEqual(ContinueTable.body_of(self._store(), 2, D), {KEY: frozenset({9})})
        self.assertEqual(ContinueTable.body_of(self._store(), 2, F), {}, "さっきの Ctrl なしの分は写らない")

    def test_the_window_assignment_is_not_copied(self):
        self.ctrl.return_value = True
        self.tabs[0].v_profile.set(5)
        self.tabs[0].v_log.set("a.txt")
        _checkbutton(self.tabs[0], "自動Begin").invoke()
        self.assertEqual([t.v_profile.get() for t in self.tabs[1:]], [0] * (len(self.tabs) - 1))
        self.assertEqual([t.v_log.get() for t in self.tabs[1:]], [""] * (len(self.tabs) - 1))

    # ── ログ ───────────────────────────────────────────────
    def test_one_log_line_per_broadcast(self):
        self.ctrl.return_value = True
        _checkbutton(self.tabs[0], "自動自爆").invoke()                  # 既定は ON なので OFF になる
        self.log.assert_called_once_with("[設定] Ctrl: 「自動自爆」を OFF にして全窓へ写しました")
        self.log.reset_mock()
        _checkbutton(self.tabs[0], "自動自爆").invoke()
        self.log.assert_called_once_with("[設定] Ctrl: 「自動自爆」を ON にして全窓へ写しました")
        self.log.reset_mock()
        self.tabs[0].chk_ct_switch.invoke()
        self.log.assert_called_once_with("[設定] Ctrl: 「アイテムで切り替える」を ON にして全窓へ写しました")
        self.log.reset_mock()
        _checkbutton(self.tabs[0], ROUND, 1).invoke()
        self.log.assert_called_once_with(f"[設定] Ctrl: 「全続行 {ROUND}」を ON にして全窓へ写しました")
        self.log.reset_mock()
        self.tabs[0].v_ct_choice[F].set(PRESET)
        self.tabs[0]._on_continue_pick(F)
        self.log.assert_called_once_with(f"[設定] Ctrl: 「デフォ続行」を「{PRESET}」に全窓へ写しました")
        self.log.reset_mock()
        self.tabs[0].v_ct_choice[D].set(ContinueTable.NONE_LABEL)
        self.tabs[0]._on_continue_pick(D)
        self.log.assert_called_once_with("[設定] Ctrl: 「続行無効」を「なし」に全窓へ写しました")

    # ── 動作中 ─────────────────────────────────────────────
    def test_while_running_every_windows_monitor_gets_the_value(self):
        self.ctrl.return_value = True
        self.app._running = True
        self.addCleanup(setattr, self.app, "_running", False)
        cfgs = [WindowConfig(auto_begin=True, do_skip=True, cancel_afk=True, announce_intermission=False)
                for _ in self.tabs]
        monitors = []
        for t, cfg in zip(self.tabs, cfgs):
            m = MagicMock()
            m.window_idx = t.idx + 1
            m.cfg = cfg
            monitors.append(m)
        self.app.monitors = monitors
        self.addCleanup(self.app.monitors.clear)
        _checkbutton(self.tabs[0], "自動自爆").invoke()
        _checkbutton(self.tabs[0], "Intermissionアナウンス").invoke()
        _checkbutton(self.tabs[0], ROUND, 0).invoke()
        for cfg in cfgs:
            self.assertFalse(cfg.do_skip)
            self.assertTrue(cfg.announce_intermission)
            self.assertEqual(cfg.skip_rounds, {ROUND})


class TestCtrlHeld(unittest.TestCase):
    """ctrl_held: GetKeyState の上位ビット。Windows 以外・失敗は False"""

    def test_reads_the_high_bit_of_get_key_state(self):
        with patch("ctypes.windll", create=True) as windll, patch.object(mainGUI.sys, "platform", "win32"):
            windll.user32.GetKeyState.return_value = -32768        # 0x8000 の符号付き
            self.assertTrue(mainGUI.ctrl_held())
            windll.user32.GetKeyState.assert_called_with(0x11)
            windll.user32.GetKeyState.return_value = 1             # トグルの下位ビットだけ
            self.assertFalse(mainGUI.ctrl_held())

    def test_failure_is_false(self):
        with patch("ctypes.windll", create=True) as windll, patch.object(mainGUI.sys, "platform", "win32"):
            windll.user32.GetKeyState.side_effect = OSError("no")
            self.assertFalse(mainGUI.ctrl_held())

    def test_not_windows_is_false(self):
        with patch("ctypes.windll", create=True) as windll, patch.object(mainGUI.sys, "platform", "linux"):
            windll.user32.GetKeyState.return_value = -32768
            self.assertFalse(mainGUI.ctrl_held())
            windll.user32.GetKeyState.assert_not_called()


if __name__ == "__main__":
    unittest.main()
