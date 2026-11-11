"""窓のタブの右の空きに「Ctrl＋クリックで全窓まとめて変更」を出す案内（表示だけ）。
本物の App を 1 つ作って（窓は作った直後に隠れる。support）、ラベルの数・出る／隠れるを調べる。
タブの右端は _tabs_right_edge を差し替えて決める。保存は一時フォルダだけ"""
from tests.support import *  # noqa: F401,F403
import ContinueTable
import mainGUI
from ContinueTable import Store

TEXT = "Ctrl＋クリックで全窓まとめて変更"


def _hints(widget):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Label) and child.cget("text") == TEXT:
            yield child
        yield from _hints(child)


class TestCtrlHintFits(unittest.TestCase):
    def test_text(self):
        self.assertEqual(mainGUI.CTRL_HINT_TEXT, TEXT)

    def test_fits_exact_and_overflow(self):
        # 収まる（右端 100＋余白 10＋文字 200 = 310 < 400）
        self.assertTrue(mainGUI.ctrl_hint_fits(100, 200, 400, 10))
        # ぴったり（310 == 310）は収まる
        self.assertTrue(mainGUI.ctrl_hint_fits(100, 200, 310, 10))
        # 1px はみ出す（310 > 309）
        self.assertFalse(mainGUI.ctrl_hint_fits(100, 200, 309, 10))
        # 大きくはみ出す・タブが枠いっぱい
        self.assertFalse(mainGUI.ctrl_hint_fits(400, 200, 400, 10))
        # タブが無い（0）なら、文字と余白だけで判定する
        self.assertTrue(mainGUI.ctrl_hint_fits(0, 200, 210, 10))
        self.assertFalse(mainGUI.ctrl_hint_fits(0, 200, 209, 10))


class TestCtrlHintLabel(unittest.TestCase):
    def setUp(self):
        ContinueTable.table_path().unlink(missing_ok=True)
        self.addCleanup(ContinueTable.table_path().unlink, missing_ok=True)
        self.addCleanup(ContinueTable.set_current, Store())
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            self.app = mainGUI.App()
        self.addCleanup(self.app.destroy)
        self.addCleanup(patch.stopall)
        patch.object(self.app, "_log").start()
        patch.object(self.app, "_schedule_settings_save").start()
        self.app.update_idletasks()

    def _wide(self):
        """窓は隠れていて Tk が枠を並べないので、枠の幅を差し替える"""
        return patch.object(self.app.nb, "winfo_width", return_value=1000)

    def test_label_text_and_single(self):
        labels = list(_hints(self.app))
        self.assertEqual(len(labels), 1)
        self.assertIs(labels[0], self.app.lbl_ctrl_hint)
        self.assertEqual(labels[0].cget("text"), TEXT)

    def test_rebuild_tabs_keeps_one_label(self):
        label = self.app.lbl_ctrl_hint
        for n in (2, 5, 1, 3):
            self.app.v_win_count.set(n)
            self.app._on_win_count_change()
            self.assertEqual(len(self.app.tabs), n)
            self.app.update()
            labels = list(_hints(self.app))
            self.assertEqual(len(labels), 1, n)
            self.assertIs(labels[0], label, n)
            self.assertTrue(label.winfo_exists(), n)
        with self._wide(), patch.object(self.app, "_tabs_right_edge", return_value=100):
            self.app.v_win_count.set(4)
            self.app._on_win_count_change()
            self.app.update()                              # 作り直しの後の判定（after_idle）
            self.assertEqual(label.winfo_manager(), "place", "作り直しの後も残って出ている")

    def test_shown_or_hidden_by_fit(self):
        label = self.app.lbl_ctrl_hint
        need = label.winfo_reqwidth() + mainGUI.CTRL_HINT_MARGIN_X + mainGUI.CTRL_HINT_GAP
        with self._wide():
            with patch.object(self.app, "_tabs_right_edge", return_value=1000 - need):
                self.app._update_ctrl_hint()
                self.assertEqual(label.winfo_manager(), "place", "ぴったりは出る")
            with patch.object(self.app, "_tabs_right_edge", return_value=1000 - need + 1):
                self.app._update_ctrl_hint()
                self.assertEqual(label.winfo_manager(), "", "1px はみ出すと隠れる")
            with patch.object(self.app, "_tabs_right_edge", return_value=1000):
                self.app._update_ctrl_hint()
                self.assertEqual(label.winfo_manager(), "")
            with patch.object(self.app, "_tabs_right_edge", return_value=0):
                self.app._update_ctrl_hint()                # 幅が戻ったら、また出る
                self.assertEqual(label.winfo_manager(), "place")
                self.app._update_ctrl_hint()                # 続けて呼んでも 1 つのまま
                self.assertEqual(label.winfo_manager(), "place")
        self.assertEqual(len(list(_hints(self.app))), 1)

    def test_configure_runs_the_judgement(self):
        label = self.app.lbl_ctrl_hint
        with self._wide(), patch.object(self.app, "_tabs_right_edge", return_value=0):
            self.app.nb.event_generate("<Configure>")
            self.app.update()
            self.assertEqual(label.winfo_manager(), "place")
        with self._wide(), patch.object(self.app, "_tabs_right_edge", return_value=10 ** 6):
            self.app.nb.event_generate("<Configure>")
            self.app.update()
            self.assertEqual(label.winfo_manager(), "")

    def test_rebuild_runs_the_judgement(self):
        label = self.app.lbl_ctrl_hint
        with self._wide(), patch.object(self.app, "_tabs_right_edge", return_value=0):
            self.app._update_ctrl_hint()
            self.assertEqual(label.winfo_manager(), "place")
        with self._wide(), patch.object(self.app, "_tabs_right_edge", return_value=10 ** 6):
            self.app.v_win_count.set(2 if len(self.app.tabs) != 2 else 3)
            self.app._on_win_count_change()
            self.app.update()
            self.assertEqual(label.winfo_manager(), "", "作り直しの後も判定する")

    def test_tabs_right_edge_scans_from_the_right(self):
        def index(spec):
            x = int(spec[1:].split(",")[0])
            if x < 300:
                return 0
            raise tk.TclError("expected integer but got \"\"")      # タブに当たらない（本物の Tk と同じ）
        with self._wide(), patch.object(self.app.nb, "index", side_effect=index) as idx:
            edge = self.app._tabs_right_edge()
            self.assertGreaterEqual(edge, 300)
            self.assertLessEqual(edge, 300 + mainGUI.CTRL_HINT_PROBE_STEP)
            self.assertLess(idx.call_count, 1000 // mainGUI.CTRL_HINT_PROBE_STEP + 1)
        with self._wide(), patch.object(self.app.nb, "index", side_effect=tk.TclError("x")):
            self.assertEqual(self.app._tabs_right_edge(), 0, "タブが無い・調べられないときは 0")

    def test_text_is_gray_and_not_clickable(self):
        self.assertEqual(str(self.app.lbl_ctrl_hint.cget("foreground")), mainGUI.COLOR_CT_DIM)
        self.assertEqual(self.app.lbl_ctrl_hint.bind(), ())
