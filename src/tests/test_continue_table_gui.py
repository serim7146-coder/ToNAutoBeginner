"""GH: 続行表の画面（ContinueTableWindow とメイン画面のボタン・チェック・件数）。
窓は作った直後に隠れる（support）。本物の入力は使わず、ファイルを選ぶ・確認の画面は偽物にする。
保存は一時フォルダ（config.SETTINGS_PATH の差し替え）だけ"""
from tests.support import *  # noqa: F401,F403
import ContinueTable
import ContinueTableGUI
from ContinueTable import Table

KEY = "Classic/クラシック"
ALT = "Alternate/オルタネイト"


def _fresh_store(case):
    """今の表を空に・保存先（テスト用の一時フォルダ）の続行表のファイルを消す。終わったら同じに戻す"""
    ContinueTable.table_path().unlink(missing_ok=True)
    ContinueTable.set_current(Table())
    case.addCleanup(ContinueTable.table_path().unlink, missing_ok=True)
    case.addCleanup(ContinueTable.set_current, Table())


class _WindowCase(unittest.TestCase):
    def setUp(self):
        _fresh_store(self)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.changed = []
        self.win = ContinueTableGUI.ContinueTableWindow(self.root, on_change=lambda: self.changed.append(1))
        self.win.update()

    def _border(self, tid):
        return self.win._cells[tid].cget("highlightbackground")

    def _saved(self) -> Table:
        return ContinueTable.load()

    def _select_round(self, name):
        index = [ContinueTableGUI.round_display_name(k) for k, _ in self.win._rounds].index(name)
        self.win.list_rounds.selection_clear(0, "end")
        self.win.list_rounds.selection_set(index)
        self.win._show_round()


class TestWindow(_WindowCase):
    def test_it_opens_and_closes_without_being_shown(self):
        self.assertIn(self.win, _HIDDEN_WINDOWS)
        self.assertFalse(self.win.winfo_viewable())
        self.win.destroy()
        self.assertFalse(self.win.winfo_exists())

    def test_the_first_round_is_listed_with_one_cell_per_terror(self):
        self.assertEqual(self.win.list_rounds.size(), 19)
        self.assertEqual(self.win.list_rounds.get(0), "Classic")
        self.assertEqual(len(self.win._cells), len(ContinueTableGUI.load_rounds()[0][1]))
        self.assertIsNotNone(self.win._photo(0), "アイコンがある番号は絵")
        self.assertIsNone(self.win._photo(193), "絵が無い番号は名前の札")

    def test_switching_the_round_changes_the_grid(self):
        self._select_round("Alternate")
        self.assertEqual(sorted(self.win._cells), list(range(134, 170)))

    def test_clicking_a_cell_puts_it_in_the_table_and_saves_it(self):
        self.assertEqual(self._border(5), ContinueTableGUI.COLOR_IDLE)
        self.win._cells[5].event_generate("<Button-1>")           # 本物の入力ではなく、窓の中の合成イベント
        self.win.update()
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})})
        self.assertEqual(self._saved().disabled, {KEY: frozenset({5})}, "すぐ保存される")
        self.assertEqual(self._border(5), ContinueTableGUI.COLOR_DISABLED)
        self.assertEqual(len(self.changed), 1, "メイン画面の件数を更新する")

    def test_clicking_again_takes_it_out(self):
        self.win.toggle(5)
        self.win.toggle(5)
        self.assertEqual(ContinueTable.current().disabled, {})
        self.assertEqual(self._saved().disabled, {})
        self.assertEqual(self._border(5), ContinueTableGUI.COLOR_IDLE)

    def test_the_default_table_is_green_and_the_other_table_is_faint(self):
        self.win.toggle(5)                                     # 続行無効
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win._restyle_all()
        self.assertEqual(self._border(5), ContinueTableGUI.COLOR_DISABLED_FAINT)
        self.win.toggle(5)
        self.win.toggle(6)
        self.assertEqual(self._border(5), ContinueTableGUI.COLOR_DEFAULT)
        self.assertEqual(ContinueTable.current().default, {KEY: frozenset({5, 6})})
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})}, "もう片方は変えない")
        self.win.v_which.set(ContinueTable.DISABLED)
        self.win._restyle_all()
        self.assertEqual(self._border(5), ContinueTableGUI.COLOR_DISABLED)
        self.assertEqual(self._border(6), ContinueTableGUI.COLOR_DEFAULT_FAINT)

    def test_each_round_has_its_own_entries(self):
        self.win.toggle(5)
        self._select_round("Alternate")
        self.win.toggle(150)
        self.assertEqual(ContinueTable.current().disabled,
                         {KEY: frozenset({5}), ALT: frozenset({150})})

    def test_the_name_search_narrows_the_grid(self):
        self.win.v_search.set("huggy")
        self.win.update()
        self.assertEqual(sorted(self.win._cells), [0])
        self.win.v_search.set("")
        self.win.update()
        self.assertGreater(len(self.win._cells), 100)

    def test_clearing_the_round_takes_everything_out_of_the_current_table_only(self):
        self.win.toggle(5)
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.toggle(6)
        self.win.toggle(7)
        self.win._clear_round()
        self.assertEqual(ContinueTable.current().default, {})
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})})

    def test_the_notice_about_group_instances_is_shown(self):
        self.assertIn("干し芋・焼き芋", ContinueTableGUI.NOTE)
        texts = []
        stack = [self.win]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            try:
                texts.append(w.cget("text"))
            except tk.TclError:
                pass
        self.assertIn(ContinueTableGUI.NOTE, texts)


class TestImport(_WindowCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.asked = []
        self.errors = []
        self.answer = True
        self.path = ""
        self.win._ask_file = lambda title, pattern: (self.asked.append(pattern), self.path)[1]
        self.win._confirm = lambda text: (self.asked.append(text), self.answer)[1]
        self.win._error = self.errors.append

    def _file(self, name, data):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data), encoding="utf-8")
        self.path = str(path)

    def test_tnl_replaces_the_default_table(self):
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.toggle(1)
        self._file("a.tnl", {"data": {KEY: {"5": 1, "6": 1}}})
        self.win._import_tnl()
        self.assertEqual(ContinueTable.current().default, {KEY: frozenset({5, 6})}, "置き換え")
        self.assertEqual(self._saved().default, {KEY: frozenset({5, 6})})
        self.assertIn("*.tnl", self.asked)
        self.assertTrue(any("置き換え" in str(a) for a in self.asked), "確認を出す")
        self.assertEqual(self.changed, [1, 1])

    def test_tnnl_replaces_the_disabled_table_even_when_the_default_is_shown(self):
        self.win.v_which.set(ContinueTable.DEFAULT)
        self._file("a.tnnl", {"targets_dict": {KEY: [2, 3]}})
        self.win._import_tnnl()
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({2, 3})})
        self.assertEqual(ContinueTable.current().default, {})

    def test_declining_the_confirmation_changes_nothing(self):
        self.answer = False
        self._file("a.tnnl", {"targets_dict": {KEY: [2]}})
        self.win._import_tnnl()
        self.assertEqual(ContinueTable.current(), Table())
        self.assertEqual(self.changed, [])

    def test_cancelling_the_file_dialog_changes_nothing(self):
        self.path = ""
        self.win._import_tnl()
        self.assertEqual(ContinueTable.current(), Table())
        self.assertFalse([a for a in self.asked if "置き換え" in str(a)], "確認も出さない")

    def test_a_broken_file_is_reported_and_changes_nothing(self):
        self.win.toggle(5)
        path = Path(self.tmp.name) / "x.tnl"
        path.write_text("not json", encoding="utf-8")
        self.path = str(path)
        with patch.object(DebugLog, "exception"):
            self.win._import_tnl()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})})


class TestMainWindow(unittest.TestCase):
    def setUp(self):
        _fresh_store(self)
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            self.app = mainGUI.App()
        self.addCleanup(self.app.destroy)

    def test_the_count_label_and_the_two_checks(self):
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "無効 0件・デフォ 0件")
        self.assertTrue(self.app.v_ct_disabled.get())
        self.assertTrue(self.app.v_ct_default.get())

    def test_turning_a_check_off_is_saved_and_takes_effect(self):
        ContinueTable.set_current(ContinueTable.with_toggled(Table(), ContinueTable.DISABLED, KEY, 7))
        self.app.v_ct_disabled.set(False)
        self.app._on_continue_table_use()
        self.assertFalse(ContinueTable.current().use_disabled)
        self.assertFalse(ContinueTable.load().use_disabled, "保存される")
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "無効 1件・デフォ 0件")
        self.assertEqual(ContinueTable.apply({KEY: {7}}, ContinueTable.current()), {KEY: {7}})

    def test_the_button_opens_one_window_and_a_change_updates_the_count(self):
        self.app._open_continue_table()
        first = self.app._continue_table_window
        self.assertIsInstance(first, ContinueTableGUI.ContinueTableWindow)
        self.app._open_continue_table()
        self.assertIs(self.app._continue_table_window, first, "2 回目は同じ窓")
        first.toggle(5)
        first.v_which.set(ContinueTable.DEFAULT)
        first.toggle(6)
        first.toggle(7)
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "無効 1件・デフォ 2件")
        first.destroy()
        self.app._open_continue_table()
        self.assertIsNot(self.app._continue_table_window, first, "閉じたら作り直す")

    def test_the_saved_table_is_loaded_at_start(self):
        saved = ContinueTable.with_toggled(Table(use_default=False), ContinueTable.DEFAULT, ALT, 150)
        ContinueTable.save(saved)
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            app = mainGUI.App()
        self.addCleanup(app.destroy)
        self.assertEqual(ContinueTable.current(), saved)
        self.assertFalse(app.v_ct_default.get())
        self.assertEqual(app.lbl_ct_count.cget("text"), "無効 0件・デフォ 1件")
