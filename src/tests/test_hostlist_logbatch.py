"""FT: 主催リストの読み直しを減らす・画面のログをまとめて差し込む。

① ToN ListTool は user_save.json の updated_at だけを数秒おきに書き直す。時刻・サイズで見ると毎回「変わった」になり、
   270〜340ms の load_host_state を 3 秒ごとに呼んでいた。読んでいる中身（last_active・accounts）のダイジェストで見る。
   ビット列 → ID の集合の変換（_wish_ids）は同じビット列なら覚えた結果を返す。
② 画面のログは 1 行ごとに受付箱へ入れず、ためて 1 回の差し込み・削除・スクロール・オーバーレイ更新で流す。
一時フォルダの偽のファイルだけを使う（ToN ListTool のフォルダは読まない・書かない）。偽の Tk（本物の窓は出さない）。
"""
from tests.support import *  # noqa: F401,F403
import types
import UiInbox


# ── ① user_save.json のダイジェスト ─────────────────
class TestUserSaveDigest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _write(self, name, raw):
        path = Path(self._dir.name) / name
        path.write_text(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return str(path)

    ACCOUNTS = {"alice_test": {"1": 1}}

    def _raw(self, updated_at="2026-10-17T10:00:00", last_active="alice_test", accounts=None):
        return {"updated_at": updated_at, "last_active": last_active,
                "accounts": self.ACCOUNTS if accounts is None else accounts}

    def test_only_updated_at_changing_gives_the_same_digest(self):
        a = self._write("a.json", self._raw(updated_at="2026-10-17T10:00:00"))
        b = self._write("b.json", self._raw(updated_at="2026-10-17T10:00:07"))
        self.assertEqual(mainGUI._user_save_digest(a), mainGUI._user_save_digest(b))
        self.assertEqual(mainGUI._user_save_digest(a)[0], "digest")

    def test_key_order_does_not_matter(self):
        a = self._write("a.json", {"accounts": {"x": {"1": 1, "2": 1}}, "last_active": "x"})
        b = self._write("b.json", {"last_active": "x", "accounts": {"x": {"2": 1, "1": 1}}})
        self.assertEqual(mainGUI._user_save_digest(a), mainGUI._user_save_digest(b))

    def test_accounts_changing_gives_a_different_digest(self):
        a = self._write("a.json", self._raw())
        b = self._write("b.json", self._raw(accounts={"alice_test": {"1": 1, "2": 1}}))
        self.assertNotEqual(mainGUI._user_save_digest(a), mainGUI._user_save_digest(b))

    def test_last_active_changing_gives_a_different_digest(self):
        a = self._write("a.json", self._raw(last_active="alice_test"))
        b = self._write("b.json", self._raw(last_active="bob_test"))
        self.assertNotEqual(mainGUI._user_save_digest(a), mainGUI._user_save_digest(b))

    def test_a_broken_json_falls_back_to_the_file_stamp(self):
        path = self._write("broken.json", '{"last_active": "alice_t')
        self.assertEqual(mainGUI._user_save_digest(path), mainGUI._file_stamp(path))
        self.assertIsNotNone(mainGUI._user_save_digest(path))

    def test_a_json_that_is_not_an_object_falls_back_to_the_file_stamp(self):
        path = self._write("list.json", "[1, 2, 3]")
        self.assertEqual(mainGUI._user_save_digest(path), mainGUI._file_stamp(path))

    def test_a_missing_file_is_none(self):
        self.assertIsNone(mainGUI._user_save_digest(str(Path(self._dir.name) / "none.json")))

    def test_the_source_reader_says_same_when_only_updated_at_changed(self):
        """本体の stamp が同じで user_save の updated_at だけ変わった → 読み直さない（"same"）"""
        user = Path(self._dir.name) / "user_save.json"
        user.write_text(json.dumps(self._raw(updated_at="t1")), encoding="utf-8")
        cache = types.SimpleNamespace(watch=lambda name: True)
        app = types.SimpleNamespace(_process_cache=cache, _host_save_stamp=None, _user_save_cache=None)
        fake_stat = types.SimpleNamespace(st_mtime=100.0, st_size=10)
        loaded = ({"Classic/クラシック": {1}}, {"listed": 1, "participants": 1}, {})
        with patch.object(config, "USER_SAVE_PATH", str(user)), \
             patch.object(config, "HOST_STATE_PATH", str(Path(self._dir.name) / "host_state.sqlite3")), \
             patch.object(mainGUI.os.path, "exists", return_value=True), \
             patch.object(mainGUI.os, "stat", return_value=fake_stat), \
             patch.object(mainGUI, "_file_stamp", return_value=None), \
             patch.object(SharedState, "get_list_source", return_value="host"), \
             patch.object(MatchTNL, "load_host_state", return_value=loaded) as load:
            kind, value = mainGUI.App._read_host_source(app)
            self.assertEqual(kind, "loaded")
            app._host_save_stamp = value[3]
            self.assertEqual(load.call_count, 1)

            user.write_text(json.dumps(self._raw(updated_at="t2")), encoding="utf-8")   # updated_at だけ
            self.assertEqual(mainGUI.App._read_host_source(app), ("same", None))
            self.assertEqual(load.call_count, 1, "読み直していない")

            user.write_text(json.dumps(self._raw(accounts={"alice_test": {"3": 1}})), encoding="utf-8")
            self.assertEqual(mainGUI.App._read_host_source(app)[0], "loaded", "中身が変われば読み直す")
            self.assertEqual(load.call_count, 2)


# ── GU: user_save.json は開く回数と時間を減らす ───────────
class TestUserSaveOpenOnce(unittest.TestCase):
    """ToN ListTool は user_save.json を「一時ファイルへ書く → os.replace」で保存する。こちらが同じ瞬間に
    開いていると置き換えが失敗する。開く前に stat・一気に読んですぐ閉じる・1 周期に最多 1 回開く。
    一時フォルダの偽のファイルだけを使う（ToN ListTool のフォルダは読まない・書かない）"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.user = Path(self._dir.name) / "user_save.json"
        self.db = Path(self._dir.name) / "host_state.sqlite3"

    def _raw(self, accounts=None):
        return {"updated_at": "t1", "last_active": "alice_test",
                "accounts": {"alice_test": {"data": {}}} if accounts is None else accounts}

    def _write(self, raw):
        self.user.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    def _count_opens(self, target):
        """target を開いた回数を数える（builtins.open を包む。中身は本物のまま）"""
        real_open = open
        calls = []

        def counting(file, *args, **kwargs):
            if str(file) == str(target):
                calls.append(file)
            return real_open(file, *args, **kwargs)
        return calls, patch("builtins.open", counting)

    def test_the_same_stamp_does_not_open_the_file(self):
        self._write(self._raw())
        first = mainGUI._read_user_save(str(self.user), None)
        calls, patcher = self._count_opens(self.user)
        with patcher:
            second = mainGUI._read_user_save(str(self.user), first)
        self.assertEqual(calls, [], "stamp が同じなら開かない")
        self.assertIs(second, first)
        self.assertEqual(second[1][0], "digest")

    def test_a_changed_stamp_opens_once_for_the_whole_cycle(self):
        """ダイジェストと自分のリストで 2 回開かない（読んだバイト列を load へ渡す）"""
        self._write(self._raw())
        cache = mainGUI._read_user_save(str(self.user), None)
        self._write(self._raw(accounts={"alice_test": {"data": {}}, "bob_test": {"data": {}}}))   # サイズも変わる
        app = types.SimpleNamespace(_process_cache=types.SimpleNamespace(watch=lambda name: True),
                                    _host_save_stamp=None, _user_save_cache=cache)
        fake_stat = types.SimpleNamespace(st_mtime=100.0, st_size=10)
        calls, patcher = self._count_opens(self.user)
        seen = {}

        def fake_load(path, user_save, user_save_raw=None):
            # 本物の _load_host_own_list を通す（raw が来ていれば開かない）
            keep_on, wishes = {}, {}
            seen["host_self"] = MatchTNL._load_host_own_list(user_save, keep_on, wishes, raw=user_save_raw)
            seen["wishes"] = wishes
            return keep_on, {"listed": 1, "participants": 1}, wishes

        with patch.object(config, "USER_SAVE_PATH", str(self.user)), \
             patch.object(config, "HOST_STATE_PATH", str(self.db)), \
             patch.object(mainGUI.os.path, "exists", return_value=True), \
             patch.object(mainGUI.os, "stat", return_value=fake_stat), \
             patch.object(SharedState, "get_list_source", return_value="host"), \
             patch.object(MatchTNL, "load_host_state", side_effect=fake_load), \
             patcher:
            kind, _value = mainGUI.App._read_host_source(app)
        self.assertEqual(kind, "loaded")
        self.assertEqual(len(calls), 1, "1 周期に開くのは 1 回だけ")
        self.assertEqual(seen["host_self"], "alice_test")
        self.assertIn("bob_test", seen["wishes"])

    def test_a_broken_json_falls_back_to_the_file_stamp_through_the_reader(self):
        self.user.write_text('{"last_active": "alice_t', encoding="utf-8")
        stamp, digest, raw = mainGUI._read_user_save(str(self.user), None)
        self.assertEqual(digest, mainGUI._file_stamp(str(self.user)))
        self.assertEqual(stamp, digest)
        self.assertIsNotNone(raw, "読めてはいる（解析だけ失敗）")

    def test_a_missing_file_is_none(self):
        self.assertEqual(mainGUI._read_user_save(str(self.user), None), (None, None, None))

    def test_a_given_raw_is_parsed_without_opening(self):
        self._write(self._raw())
        raw = self.user.read_bytes()
        calls, patcher = self._count_opens(self.user)
        with patcher:
            digest = mainGUI._user_save_digest(str(self.user), raw)
        self.assertEqual(calls, [])
        self.assertEqual(digest, mainGUI._user_save_digest(str(self.user)))

    def test_own_list_with_raw_does_not_open_and_matches_reading_from_the_path(self):
        data = {"alice_test": {"data": {"Classic/クラシック": {"1": True}}}, "bob_test": {"data": {}}}
        self._write(self._raw(accounts=data))
        keep_path, wishes_path = {}, {}
        host_path = MatchTNL._load_host_own_list(str(self.user), keep_path, wishes_path)
        raw = self.user.read_bytes()
        keep_raw, wishes_raw = {}, {}
        calls, patcher = self._count_opens(self.user)
        with patcher:
            host_raw = MatchTNL._load_host_own_list(str(self.user), keep_raw, wishes_raw, raw=raw)
        self.assertEqual(calls, [], "raw があれば開かない")
        self.assertEqual(host_raw, host_path)
        self.assertEqual(keep_raw, keep_path)
        self.assertEqual(wishes_raw, wishes_path)
        self.assertEqual(host_raw, "alice_test")

    def test_own_list_still_gives_up_quietly_on_bad_input(self):
        self.assertIsNone(MatchTNL._load_host_own_list(str(self.user), {}, {}))                       # 無い
        self.assertIsNone(MatchTNL._load_host_own_list(str(self.user), {}, {}, raw=b'{"accounts'))   # 壊れている
        self.assertIsNone(MatchTNL._load_host_own_list(str(self.user), {}, {}, raw=b"[1]"))          # オブジェクトでない


# ── ① _wish_ids の覚え書き ─────────────────────────
class TestWishIdsCache(unittest.TestCase):
    def test_values_are_as_before(self):
        self.assertEqual(MatchTNL._wish_ids(b"\x05"), {0, 2})
        self.assertEqual(MatchTNL._wish_ids(b"\x00\x80"), {15})
        self.assertEqual(MatchTNL._wish_ids(b""), set())
        self.assertIsInstance(MatchTNL._wish_ids(b"\x01"), frozenset)

    def test_the_same_bytes_give_the_same_object(self):
        bits = bytes([0x12, 0x34, 0x56, 0xF0])
        self.assertIs(MatchTNL._wish_ids(bits), MatchTNL._wish_ids(bytes(bits)))
        self.assertIs(MatchTNL._wish_ids(bits), MatchTNL._wish_ids(bytearray(bits)))

    def test_a_bytearray_gives_the_same_result_as_bytes(self):
        self.assertEqual(MatchTNL._wish_ids(bytearray(b"\x05\x01")), MatchTNL._wish_ids(b"\x05\x01"))
        self.assertEqual(MatchTNL._wish_ids(bytearray(b"\x05\x01")), {0, 2, 8})

    def test_not_bytes_is_empty(self):
        for bad in (None, "abc", 5, [1, 2], ()):
            self.assertEqual(MatchTNL._wish_ids(bad), frozenset(), repr(bad))

    def test_it_is_a_real_cache(self):
        MatchTNL._wish_ids_cached.cache_clear()
        MatchTNL._wish_ids(b"\xAA\xBB")
        MatchTNL._wish_ids(b"\xAA\xBB")
        info = MatchTNL._wish_ids_cached.cache_info()
        self.assertEqual((info.misses, info.hits), (1, 1))


# ── ② 画面のログをまとめて差し込む ─────────────────────
class _FakeLogText:
    def __init__(self):
        self.lines, self.state, self.inserts, self.deletes, self.sees = [], None, [], [], []

    def config(self, **kwargs):
        self.state = kwargs.get("state", self.state)

    def insert(self, index, text):
        self.inserts.append(text)
        for part in text.splitlines(keepends=True):
            self.lines.append(part)

    def delete(self, start, end):
        self.deletes.append((start, end))
        del self.lines[:int(end.split(".", 1)[0]) - 1]

    def see(self, index):
        self.sees.append(index)


class _FakeOverlay:
    def __init__(self):
        self._closed = False
        self.many, self.single = [], []

    def append_many(self, msgs):
        self.many.append(list(msgs))


class _FakeApp:
    _log = mainGUI.App._log
    _flush_log_lines = mainGUI.App._flush_log_lines
    _append_log_lines = mainGUI.App._append_log_lines
    _append_log_text = mainGUI.App._append_log_text

    def __init__(self):
        self.log_text = _FakeLogText()
        self._log_line_count = 0
        self._log_pending, self._log_pending_lock, self._log_flush_queued = [], threading.Lock(), False
        self._overlay = None
        self.afters = []                # after(0, func) の依頼

    def after(self, ms, func, *args):
        self.afters.append(func)


class TestLogBatching(unittest.TestCase):
    def setUp(self):
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)
        self.app = _FakeApp()

    def test_five_lines_make_one_request_and_one_insert(self):
        app = self.app
        for i in range(5):
            app._log(f"行{i}")
        self.assertEqual(len(app.afters), 1, "流す依頼は 1 件だけ")
        self.assertEqual(app.log_text.inserts, [], "流すまでは表示しない")
        app.afters[0]()
        self.assertEqual(len(app.log_text.inserts), 1)
        text = app.log_text.inserts[0]
        positions = [text.index(f"行{i}") for i in range(5)]
        self.assertEqual(positions, sorted(positions), "入れた順")
        self.assertEqual(text.count("\n"), 5)
        self.assertEqual(app.log_text.sees, ["end"])
        self.assertEqual(app.log_text.state, "disabled")
        self.assertEqual(app._log_line_count, 5)

    def test_after_a_flush_the_next_log_queues_again(self):
        app = self.app
        app._log("a")
        app._log("b")
        app.afters.pop()()
        self.assertFalse(app._log_flush_queued)
        app._log("c")
        self.assertEqual(len(app.afters), 1, "また 1 件")
        app.afters.pop()()
        self.assertEqual(len(app.log_text.inserts), 2)
        self.assertEqual(len(app.log_text.lines), 3)

    def test_the_inbox_gets_one_request_and_it_runs_on_the_tk_thread(self):
        """別のスレッドから 5 回 _log → 受付箱に 1 件。名前は _flush_log_lines（CPU の記録の名前）"""
        app = self.app
        inbox = UiInbox.UiInbox(types.SimpleNamespace(after=lambda *a, **k: None))
        app._inbox = inbox
        t = threading.Thread(target=lambda: [app._log(f"w{i}") for i in range(5)])
        t.start()
        t.join(5)
        self.assertEqual(inbox._queue.qsize(), 1)
        self.assertEqual(app.afters, [], "別のスレッドは Tk を呼ばない")
        func, _args = inbox._queue.get_nowait()
        self.assertEqual(func.__name__, "_flush_log_lines")
        func()
        self.assertEqual(len(app.log_text.inserts), 1)

    def test_a_tcl_error_on_queueing_resets_the_flag_and_does_not_raise(self):
        app = self.app
        with patch.object(UiInbox, "call_soon", side_effect=tk.TclError("gone")):
            app._log("閉じた後")
        self.assertFalse(app._log_flush_queued)
        app._log("次")
        self.assertEqual(len(app.afters), 1, "旗が戻っているのでまた依頼できる")

    def test_a_tcl_error_while_flushing_is_dropped(self):
        app = self.app
        app._log("x")
        with patch.object(app.log_text, "insert", side_effect=tk.TclError("gone")):
            app.afters.pop()()                       # 落ちない
        self.assertEqual(app._log_pending, [])
        self.assertFalse(app._log_flush_queued)

    def test_append_log_text_still_works_for_one_line(self):
        app = self.app
        app._append_log_text("one\n")
        self.assertEqual(app.log_text.inserts, ["one\n"])
        self.assertEqual(app._log_line_count, 1)

    def test_old_lines_beyond_the_limit_are_dropped_in_one_delete(self):
        app = self.app
        with patch.object(config, "GUI_LOG_MAX_LINES", 3):
            for i in range(5):
                app._log(f"L{i}")
            app.afters.pop()()
        self.assertEqual(len(app.log_text.deletes), 1)
        self.assertEqual(app.log_text.deletes[0], ("1.0", "3.0"))
        self.assertEqual(app._log_line_count, 3)
        self.assertEqual(len(app.log_text.lines), 3)
        self.assertIn("L2", app.log_text.lines[0])
        self.assertIn("L4", app.log_text.lines[2])

    def test_the_overlay_is_updated_once_with_every_line(self):
        app = self.app
        app._overlay = _FakeOverlay()
        for i in range(5):
            app._log(f"o{i}")
        app.afters.pop()()
        self.assertEqual(len(app._overlay.many), 1)
        rows = app._overlay.many[0]
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(not r.endswith("\n") for r in rows))
        self.assertTrue(rows[0].endswith("o0") and rows[4].endswith("o4"))

    def test_a_closed_overlay_is_skipped(self):
        app = self.app
        app._overlay = _FakeOverlay()
        app._overlay._closed = True
        app._log("z")
        app.afters.pop()()
        self.assertEqual(app._overlay.many, [])
        self.assertEqual(len(app.log_text.inserts), 1)

    def test_debug_log_is_written_at_log_time_not_at_flush(self):
        app = self.app
        app._log("すぐ書く")
        app._log("[画面] もう付いている")
        self.assertEqual(self.written, ["[画面] すぐ書く", "[画面] もう付いている"])
        self.assertEqual(app.log_text.inserts, [], "まだ流していない")

    def test_a_flush_with_nothing_pending_does_nothing(self):
        self.app._flush_log_lines()
        self.assertEqual(self.app.log_text.inserts, [])


class TestLogOverlayAppendMany(unittest.TestCase):
    class _Text:
        def __init__(self):
            self.calls = []

        def config(self, **kw):
            self.calls.append(("config", kw))

        def delete(self, a, b):
            self.calls.append(("delete", a, b))

        def insert(self, i, text):
            self.calls.append(("insert", text))

        def see(self, i):
            self.calls.append(("see", i))

    def _overlay(self):
        o = type("O", (), {})()
        o._closed = False
        o._lines = []
        o.MAX_LINES = 4
        o.text = self._Text()
        o.append_many = lambda msgs: mainGUI.LogOverlay.append_many(o, msgs)
        return o

    def test_many_lines_rewrite_the_text_once_and_keep_the_newest(self):
        o = self._overlay()
        mainGUI.LogOverlay.append_many(o, [f"m{i}" for i in range(7)])
        self.assertEqual(o._lines, ["m3", "m4", "m5", "m6"])
        inserts = [c for c in o.text.calls if c[0] == "insert"]
        self.assertEqual(inserts, [("insert", "m3\nm4\nm5\nm6")])
        self.assertEqual(len([c for c in o.text.calls if c[0] == "delete"]), 1)

    def test_append_is_append_many_of_one(self):
        o = self._overlay()
        mainGUI.LogOverlay.append(o, "only")
        self.assertEqual(o._lines, ["only"])
        self.assertEqual([c for c in o.text.calls if c[0] == "insert"], [("insert", "only")])

    def test_a_closed_overlay_does_nothing(self):
        o = self._overlay()
        o._closed = True
        mainGUI.LogOverlay.append_many(o, ["a"])
        self.assertEqual((o._lines, o.text.calls), ([], []))


if __name__ == "__main__":
    unittest.main()
