"""説明の文の折り返し。Tk は半角スペースの位置を優先して改行し、スペースが無ければ幅いっぱいの
文字の途中で切るので、「スマホに ntfy」で改行して行の後ろが空いたり、文の途中で切れたりした。
半角スペースをなくし、文の区切りで改行する（1 行 35 文字以内。利用者の画面は 1 行 約 39 文字）。
本物の App を 1 つ作って（窓は隠す。support）ラベルの文字を調べる。保存は差し替える"""
from tests.support import *  # noqa: F401,F403
import ContinueTable
import mainGUI
from ContinueTable import Store

MAX_CHARS = 35

PHONE_LINES = [
    "続行ラウンド・全続行・待機列に入れるようになったときに、",
    "スマホへ通知します。スマホにntfyのアプリを入れて、",
    "下の宛先を購読してください。",
    "宛先を知っている人は、この通知を読めます（人に見せないでください）。",
]
VOLUME_LINES = [
    "続行の窓、全続行の窓、フリーズの窓、それ以外で音量を切り替えられます。",
    "マクロを停止すると元の音量に戻ります。",
]
REPORT_LINES = [
    "VRChatのログには、一緒にいた人の名前やユーザーID、",
    "あなたの表示名が含まれます。",
    "画面の撮影には一緒にいた人の名前が写ることがあります。",
    "送り先は開発者のDiscordです。",
]


def _labels(widget):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Label):
            yield child
        yield from _labels(child)


def _has_label(widget, text):
    return any(label.cget("text") == text for label in _labels(widget))


class TestNoteText(unittest.TestCase):
    NOTES = (
        ("PHONE_NOTIFY_NOTE", mainGUI.PHONE_NOTIFY_NOTE, PHONE_LINES),
        ("WINDOW_VOLUME_NOTE", mainGUI.WINDOW_VOLUME_NOTE, VOLUME_LINES),
        ("REPORT_NOTE", mainGUI.ReportDialog.REPORT_NOTE, REPORT_LINES),
    )

    def test_no_half_width_space(self):
        for name, note, _lines in self.NOTES:
            self.assertNotIn(" ", note, name)

    def test_every_line_fits(self):
        for name, note, _lines in self.NOTES:
            for line in note.split("\n"):
                self.assertLessEqual(len(line), MAX_CHARS, f"{name}: {line}")
                self.assertTrue(line, f"{name}: 空の行がある")

    def test_lines_are_as_specified(self):
        for name, note, lines in self.NOTES:
            self.assertEqual(note.split("\n"), lines, name)
            self.assertFalse(note.endswith("\n"), name)


class TestNoteLabels(unittest.TestCase):
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
        self.app.withdraw()
        self.app.update_idletasks()

    def test_the_phone_notify_label_uses_the_constant(self):
        self.assertTrue(_has_label(self.app, mainGUI.PHONE_NOTIFY_NOTE))

    def test_the_window_volume_label_uses_the_constant(self):
        self.assertTrue(_has_label(self.app, mainGUI.WINDOW_VOLUME_NOTE))

    def test_the_report_label_uses_the_constant(self):
        self.app._report_sent_at = None
        self.app._open_report()
        dialog = self.app._report_dialog
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        self.assertTrue(_has_label(dialog, mainGUI.ReportDialog.REPORT_NOTE))

    def test_wraplength_is_kept(self):
        """画面の拡大率が大きい PC の保険として、折り返しの幅は残す"""
        for note, width in ((mainGUI.PHONE_NOTIFY_NOTE, 420), (mainGUI.WINDOW_VOLUME_NOTE, 420)):
            labels = [lb for lb in _labels(self.app) if lb.cget("text") == note]
            self.assertEqual(len(labels), 1)
            self.assertEqual(int(str(labels[0].cget("wraplength"))), width)
