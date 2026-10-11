"""不具合報告で窓を複数・全部選べる（DR）。本物の Discord へは送らない"""
from tests.support import *  # noqa: F401,F403
import RunFace
import string


class TestTheLabels(unittest.TestCase):
    def test_labels(self):
        L = BugReport.window_label
        self.assertEqual(L(0), "窓なし")
        self.assertEqual(L([]), "窓なし")
        self.assertEqual(L(2), "窓2")
        self.assertEqual(L([2]), "窓2")
        self.assertEqual(L([3, 1]), "窓1・窓3")
        self.assertEqual(L([1, 3, 5], 6), "窓1・窓3・窓5")
        self.assertEqual(L([1, 2, 3, 4, 5, 6], 6), "全窓（6）")
        self.assertEqual(L([1], 1), "窓1", "1 窓しかないときは今どおり")
        self.assertEqual(L([1, 2], 3), "窓1・窓2")

    def test_the_content(self):
        now = datetime(2026, 10, 11, 12, 0, 0)
        self.assertIn("/ 窓1・窓3 /", BugReport.content("v", [1, 3], "x", now, 6))
        self.assertIn("/ 全窓（6） /", BugReport.content("v", [1, 2, 3, 4, 5, 6], "x", now, 6))
        self.assertIn("/ 窓なし /", BugReport.content("v", [], "x", now, 6))


class TestTheZip(unittest.TestCase):
    NOW = datetime(2026, 10, 11, 12, 0, 0)

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        for p in (patch.object(BeginMiss, "folder", return_value=self.dir / "begin_miss"),
                  patch.object(RunFace, "folder", return_value=self.dir / "run_face"),
                  patch.object(DebugLog, "write")):
            p.start()
            self.addCleanup(p.stop)
        (self.dir / "begin_miss").mkdir()
        (self.dir / "run_face").mkdir()
        for n in (1, 2, 3):
            (self.dir / "begin_miss" / f"窓{n}_1.png").write_bytes(b"png%d" % n)
            (self.dir / "run_face" / f"20261011_0900{n:02d}_000000_窓{n}_tries.jpg").write_bytes(b"jpg%d" % n)
        self.debug = self.dir / "debug.log"
        self.debug.write_text("d\n", encoding="utf-8")

    def _log(self, n, text=None):
        path = self.dir / f"output_log_{n}.txt"
        path.write_text(text or f"window {n} line\n", encoding="utf-8")
        return str(path)

    def _zip(self, windows, paths, include=None, all_windows=6):
        include = include if include is not None else {k: True for k, _l in BugReport.ATTACHMENTS}
        name, data = BugReport.build_report("止まった", windows, include, "gui\n", paths, {}, self.NOW,
                                            debug_log_path=self.debug, version="v", all_windows=all_windows)
        z = zipfile.ZipFile(io.BytesIO(data))
        return name, data, {n: z.read(n) for n in z.namelist()}

    def test_two_windows(self):
        _n, _d, files = self._zip([1, 3], {1: self._log(1), 3: self._log(3)})
        self.assertIn("vrchat_log_window1.txt", files)
        self.assertIn("vrchat_log_window3.txt", files)
        self.assertNotIn("vrchat_log_window2.txt", files)
        self.assertEqual(sorted(f for f in files if f.startswith(("begin_miss/", "run_face/"))),
                         ["begin_miss/窓1_1.png", "begin_miss/窓3_1.png",
                          "run_face/20261011_090001_000000_窓1_tries.jpg",
                          "run_face/20261011_090003_000000_窓3_tries.jpg"], "選んだ窓の撮影だけ")
        report = files["report.txt"].decode("utf-8")
        self.assertIn("窓: 窓1・窓3", report)
        self.assertIn("- 窓1 の VRChat のログ: vrchat_log_window1.txt", report)
        self.assertIn("- 窓3 の VRChat のログ: vrchat_log_window3.txt", report)

    def test_all_windows_and_an_unassigned_one(self):
        paths = {n: self._log(n) for n in (1, 2, 3, 4, 6)}
        paths[5] = ""
        _n, _d, files = self._zip([1, 2, 3, 4, 5, 6], paths)
        report = files["report.txt"].decode("utf-8")
        self.assertIn("窓: 全窓（6）", report)
        self.assertIn("- 窓5 の VRChat のログ: 入れていません（窓に未割り当て）", report)
        self.assertEqual(sorted(f for f in files if f.startswith("vrchat_log_window")),
                         [f"vrchat_log_window{n}.txt" for n in (1, 2, 3, 4, 6)])

    def test_a_missing_file_is_explained(self):
        _n, _d, files = self._zip([1, 2], {1: self._log(1), 2: str(self.dir / "none.txt")})
        self.assertIn("- 窓2 の VRChat のログ: 入れていません（ファイルが無い）",
                      files["report.txt"].decode("utf-8"))

    def test_no_window_is_as_before(self):
        _n, _d, files = self._zip([], {})
        report = files["report.txt"].decode("utf-8")
        self.assertIn("窓: 窓なし", report)
        self.assertIn("- 選んだ窓の VRChat のログ: 入れていません（窓を選んでいない）", report)
        self.assertEqual(len([f for f in files if f.startswith(("begin_miss/", "run_face/"))]), 6,
                         "窓なしなら全部の窓の撮影（今どおり）")

    def test_one_window_is_the_same_as_before(self):
        path = self._log(2)
        old = self._zip(2, path, all_windows=0)[2]
        new = self._zip([2], {2: path}, all_windows=6)[2]
        self.assertEqual(old, new)
        report = new["report.txt"].decode("utf-8")
        self.assertIn("窓: 窓2", report)
        self.assertIn("- 選んだ窓の VRChat のログ: vrchat_log_window2.txt", report)

    def _big_logs(self, count, size):
        rng = random.Random(1)
        letters = string.ascii_letters + string.digits
        paths = {}
        for n in range(1, count + 1):
            lines = ["".join(rng.choices(letters, k=99)) for _ in range(size // 100)]
            paths[n] = self._log(n, "\n".join(lines) + "\n")
        return paths

    def test_six_big_logs_fit_with_the_same_length_each(self):
        paths = self._big_logs(6, 3 * 1024 * 1024)
        name, data, files = self._zip([1, 2, 3, 4, 5, 6], paths)
        self.assertLessEqual(len(data), config.REPORT_MAX_BYTES)
        logs = {f: len(v) for f, v in files.items() if f.startswith("vrchat_log_window")}
        self.assertEqual(len(logs), 6, "全部の窓のログが入る")
        self.assertLessEqual(max(logs.values()) - min(logs.values()), 200, "同じ長さずつ（行の切れ目の差だけ）")
        self.assertLessEqual(max(logs.values()), config.REPORT_VRCHAT_LOG_START_BYTES // 6)
        report = files["report.txt"].decode("utf-8")
        self.assertEqual(report.count("（末尾）"), 6)

    def test_logs_too_small_to_be_useful_are_left_out(self):
        paths = self._big_logs(6, 1024 * 1024)
        with patch.object(config, "REPORT_MAX_BYTES", 600 * 1024):
            _n, data, files = self._zip([1, 2, 3, 4, 5, 6], paths)
        self.assertFalse([f for f in files if f.startswith("vrchat_log_window")])
        report = files["report.txt"].decode("utf-8")
        self.assertEqual(report.count("入れていません（大きすぎて収まらない）"), 6)

    def test_the_start_is_divided_by_the_window_count(self):
        paths = self._big_logs(2, 5 * 1024 * 1024)
        _n, _d, files = self._zip([1, 2], paths)
        logs = {f: len(v) for f, v in files.items() if f.startswith("vrchat_log_window")}
        self.assertTrue(all(v <= config.REPORT_VRCHAT_LOG_START_BYTES // 2 for v in logs.values()), logs)
        self.assertTrue(all(v > config.REPORT_VRCHAT_LOG_START_BYTES // 4 for v in logs.values()),
                        "収まるなら半分にはしない")


class TestTheDialog(unittest.TestCase):
    """画面（本物の Tk の App。送信は差し替え）"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app._report_sent_at = None
        p = patch.object(mainGUI.App, "_log", lambda _s, m: None)
        p.start()
        self.addCleanup(p.stop)
        count = len(self.app.tabs)
        self.app._rebuild_tabs(3)
        self.addCleanup(self.app._rebuild_tabs, count)

    def _dialog(self, tab=1):
        self.app.nb.select(self.app.tabs[tab])
        self.app._open_report()
        dialog = self.app._report_dialog
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        return dialog

    def test_opens_with_the_current_window_only(self):
        dialog = self._dialog(tab=2)
        self.assertEqual(sorted(dialog.v_windows), [1, 2, 3])
        self.assertEqual(dialog.selected_windows(), [3])
        self.assertFalse(dialog.v_all_windows.get())

    def test_all_checks_and_unchecks_every_window(self):
        dialog = self._dialog()
        dialog.v_all_windows.set(True)
        dialog._on_all_windows()
        self.assertEqual(dialog.selected_windows(), [1, 2, 3])
        dialog.v_all_windows.set(False)
        dialog._on_all_windows()
        self.assertEqual(dialog.selected_windows(), [], "何も無ければ窓なし")

    def test_checking_every_window_checks_all(self):
        dialog = self._dialog()
        for var in dialog.v_windows.values():
            var.set(True)
            dialog._on_window_check()
        self.assertTrue(dialog.v_all_windows.get())
        dialog.v_windows[2].set(False)
        dialog._on_window_check()
        self.assertFalse(dialog.v_all_windows.get())

    def _send(self, dialog):
        built, contents = [], []

        def build(*args, **kwargs):
            built.append((args, kwargs))
            return "r.zip", b"zip"
        real_content = BugReport.content
        with patch.object(BugReport, "webhook_url", return_value="https://x.example/h"), \
             patch.object(BugReport, "build_report", side_effect=build), \
             patch.object(BugReport, "content", side_effect=lambda *a, **k: contents.append(real_content(*a, **k))
                          or contents[-1]), \
             patch.object(BugReport, "send", return_value=(True, "送信しました")), \
             patch.object(mainGUI.threading, "Thread", RunNow):
            dialog._send()
            self.app.update()
        return built, contents

    def test_sending_two_windows(self):
        for n in (1, 2, 3):
            self.app.tabs[n - 1].v_log.set(rf"C:\logs\output_log_{n}.txt")
        dialog = self._dialog()
        dialog.text.insert("1.0", "止まった")
        dialog.v_windows[1].set(True)
        dialog.v_windows[2].set(False)
        dialog.v_windows[3].set(True)
        built, contents = self._send(dialog)
        args, kwargs = built[0]
        self.assertEqual(args[1], [1, 3])
        self.assertEqual(args[4], {1: r"C:\logs\output_log_1.txt", 3: r"C:\logs\output_log_3.txt"})
        self.assertEqual(kwargs["all_windows"], 3)
        self.assertIn("/ 窓1・窓3 /", contents[0])

    def test_sending_all_and_none(self):
        dialog = self._dialog()
        dialog.text.insert("1.0", "止まった")
        dialog.v_all_windows.set(True)
        dialog._on_all_windows()
        built, contents = self._send(dialog)
        self.assertEqual(built[0][0][1], [1, 2, 3])
        self.assertIn("/ 全窓（3） /", contents[0])

        self.app._report_sent_at = None
        dialog = self._dialog()
        dialog.text.insert("1.0", "止まった")
        dialog.v_windows[2].set(False)
        built, contents = self._send(dialog)
        self.assertEqual(built[0][0][1], [])
        self.assertEqual(built[0][0][4], {})
        self.assertIn("/ 窓なし /", contents[0])
