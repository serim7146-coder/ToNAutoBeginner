"""テスト中に作った窓が画面に出ないこと（名前で最後に流れる）"""
from tests.support import *  # noqa: F401,F403




class TestZzNoWindowIsShown(unittest.TestCase):
    """テスト中に作った窓は画面に出ない（名前で最後に流れる。それまでの分も見る）"""

    def test_no_visible_window_after_all_the_tests(self):
        self.assertEqual(_visible_windows_of_this_process(), [])

    def test_the_real_windows_stay_hidden_even_when_they_ask(self):
        self.assertEqual(_visible_windows_of_this_process(), [], "前提: いまは0件")
        app = mainGUI.App()
        self.addCleanup(app.destroy)
        with patch.object(ConnectDB, "fetch_rounds", side_effect=lambda s, m: []),              patch.object(StatisticsGUI.threading, "Thread", RunNow):
            stats = StatisticsGUI.StatisticsWindow(app, store=RoundStore.RoundStore(
                Path(tempfile.mkdtemp()) / "r.sqlite"))
        app._open_report()
        app._open_report()                       # 2回目は lift する経路
        overlay = tk.Toplevel(app)
        overlay.attributes("-topmost", True)     # メイン画面のオーバーレイと同じ呼び方
        for w in (app, stats, app._report_dialog, overlay):
            w.deiconify()
            w.lift()
            w.focus_force()
        app.update()
        self.assertEqual(_visible_windows_of_this_process(), [])
        for w in (app, stats, app._report_dialog, overlay):
            self.assertIn(w, _HIDDEN_WINDOWS)
            self.assertFalse(w.winfo_viewable(), w)
