"""サーバーへの確認（AccessCheck）と、LogMonitor がそれを使って窓を始める流れ。
本物のネットワーク・DPAPI・%APPDATA% には触れない（tests/support.py が _ask_server を偽物にし、手元の記録は
このテストの一時フォルダに作る）。終わらせる処理（_TERMINATE）は必ず偽物にする。UID は作り物"""
from tests.support import *  # noqa: F401,F403

import AccessCheck
import TaskStats

UID = "usr_00000000-aaaa-bbbb-cccc-000000000001"
OTHER = "usr_00000000-aaaa-bbbb-cccc-000000000002"
PREFIX = "2026.10.31 12:40:19 Debug      -  "
DAY = 86400.0
ALLOW = {"allowed": True, "min_version": "", "require_uid": True}
DENY = {"allowed": False, "min_version": "", "require_uid": True}


class _Terminated(BaseException):
    """_TERMINATE の代わりに投げる目印。本物の os._exit は except Exception で捕まえられないので BaseException"""


class _Case(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        AccessCheck.reset_for_tests()
        self.addCleanup(AccessCheck.reset_for_tests)
        self.now = [1_800_000_000.0]            # 壁の時計（epoch 秒）
        self.mono = [1000.0]                    # 単調時計
        self.terminate = MagicMock()
        self.ask = MagicMock(return_value=dict(ALLOW))
        for target, name, value in ((config, "SETTINGS_PATH", self.dir / "settings.json"),
                                    (AccessCheck, "_TERMINATE", self.terminate),
                                    (AccessCheck, "_ask_server", self.ask),
                                    (AccessCheck, "_wall", lambda: self.now[0]),
                                    (AccessCheck, "_mono", lambda: self.mono[0])):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def forget_memory(self):
        """プロセスを入れ直した状態（メモリの覚えは消え、手元の記録のファイルは残る）"""
        AccessCheck.reset_for_tests()

    def record(self):
        return AccessCheck._load_record()

    def write_record(self, uid, ok_at=None, banned=False, **extra):
        users = {AccessCheck._key(uid): {"ok_at": ok_at, "banned": banned}}
        AccessCheck._save_record({"users": users, **extra})


class TestVerify(_Case):
    def test_an_allowed_answer(self):
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)
        self.ask.assert_called_once_with(UID, config.ACCESS_TIMEOUT_SEC)
        self.terminate.assert_not_called()

    def test_a_banned_answer(self):
        self.ask.return_value = dict(DENY)
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.BANNED)
        self.terminate.assert_not_called()          # verify は終わらせない（enforce が終わらせる）

    def test_the_timeout_can_be_given(self):
        AccessCheck.verify(UID, timeout=1.5)
        self.ask.assert_called_once_with(UID, 1.5)

    def test_no_answer_within_three_days_of_the_last_ok_is_allowed(self):
        AccessCheck.verify(UID)
        self.forget_memory()
        self.ask.return_value = None
        self.now[0] += config.ACCESS_GRACE_SEC - 1
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)

    def test_no_answer_at_or_after_three_days_is_unreachable(self):
        AccessCheck.verify(UID)
        self.forget_memory()
        self.ask.return_value = None
        for extra in (0, 1, DAY):
            self.now[0] = 1_800_000_000.0 + config.ACCESS_GRACE_SEC + extra
            self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE, extra)

    def test_no_answer_and_no_record_is_unreachable(self):
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE)

    def test_a_record_for_another_uid_does_not_help(self):
        AccessCheck.verify(OTHER)
        self.forget_memory()
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE)

    def test_a_broken_record_is_empty(self):
        path = AccessCheck._record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ask.return_value = None
        wrapped_list = AccessCheck.SecretStore.protect("[1, 2]")
        wrapped_bad_users = AccessCheck.SecretStore.protect('{"users": 5}')
        for text in ("", "not a record", "FAKE:@@@@", wrapped_list, wrapped_bad_users):
            path.write_text(text, encoding="ascii")
            self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE, text)
        path.write_bytes(b"\xff\xfe\x00")                       # 文字として読めない
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE)
        path.unlink()
        path.mkdir()                                            # ファイルではなくフォルダ
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE)

    def test_a_record_that_cannot_be_unprotected_is_empty(self):
        AccessCheck.verify(UID)
        self.forget_memory()
        self.ask.return_value = None
        with patch.object(AccessCheck.SecretStore, "unprotect", return_value=None):
            self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE)

    def test_a_time_in_the_future_invalidates_the_record(self):
        self.ask.return_value = None
        now = self.now[0]
        self.write_record(UID, ok_at=now + 301)
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE)
        self.write_record(UID, ok_at=now + 299)                  # 300 秒までの時計のずれは許す
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)

    def test_a_non_numeric_time_is_not_an_ok(self):
        self.ask.return_value = None
        for bad in (None, "now", True, [1]):
            self.write_record(UID, ok_at=bad)
            self.assertEqual(AccessCheck.verify(UID), AccessCheck.UNREACHABLE, bad)

    def test_a_banned_record_stays_banned_without_an_answer(self):
        self.ask.return_value = dict(DENY)
        AccessCheck.verify(UID)
        self.forget_memory()
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.BANNED)

    def test_a_banned_record_is_kept_even_if_its_time_is_in_the_future(self):
        self.ask.return_value = None
        self.write_record(UID, ok_at=self.now[0] + DAY, banned=True)
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.BANNED)

    def test_an_allowed_answer_clears_an_old_banned_record(self):
        self.ask.return_value = dict(DENY)
        AccessCheck.verify(UID)
        self.forget_memory()
        self.ask.return_value = dict(ALLOW)
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)
        self.forget_memory()
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)      # もう締め出しの印は無い

    def test_the_same_uid_is_not_asked_again_within_the_memory_time(self):
        AccessCheck.verify(UID)
        self.mono[0] += config.ACCESS_MEMORY_SEC - 1
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)
        self.assertEqual(self.ask.call_count, 1)
        self.mono[0] += 1
        AccessCheck.verify(UID)
        self.assertEqual(self.ask.call_count, 2, "時間がたてば聞き直す")
        AccessCheck.verify(OTHER)
        self.assertEqual(self.ask.call_count, 3, "別の UID は別")

    def test_a_banned_uid_is_never_asked_again(self):
        self.ask.return_value = dict(DENY)
        AccessCheck.verify(UID)
        self.mono[0] += 10 * config.ACCESS_MEMORY_SEC
        self.ask.return_value = dict(ALLOW)
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.BANNED)
        self.assertEqual(self.ask.call_count, 1)

    def test_an_unreachable_result_is_asked_again_next_time(self):
        self.ask.return_value = None
        AccessCheck.verify(UID)
        AccessCheck.verify(UID)
        self.assertEqual(self.ask.call_count, 2)
        self.ask.return_value = dict(ALLOW)
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)

    def test_a_grace_ok_is_not_remembered_as_an_answer(self):
        AccessCheck.verify(UID)
        self.forget_memory()
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)
        self.ask.return_value = dict(DENY)
        self.assertEqual(AccessCheck.verify(UID), AccessCheck.BANNED, "次はサーバーに聞き直す")

    def test_the_uid_is_trimmed(self):
        AccessCheck.verify(f"  {UID}\n")
        self.ask.assert_called_once_with(UID, config.ACCESS_TIMEOUT_SEC)

    def test_without_a_uid_the_server_setting_decides(self):
        for empty in (None, "", "   "):
            self.ask.reset_mock()
            self.ask.return_value = {"allowed": True, "min_version": "", "require_uid": True}
            self.assertEqual(AccessCheck.verify(empty), AccessCheck.NO_UID, empty)
            self.ask.assert_called_once_with(None, config.ACCESS_TIMEOUT_SEC)
            self.ask.return_value = {"allowed": True, "min_version": "", "require_uid": False}
            self.assertEqual(AccessCheck.verify(empty), AccessCheck.ALLOWED, empty)

    def test_without_a_uid_and_without_an_answer_the_saved_setting_decides(self):
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(None), AccessCheck.NO_UID, "記録が無ければ UID 必須")
        self.ask.return_value = {"allowed": True, "min_version": "", "require_uid": False}
        AccessCheck.verify(None)                                # 設定を覚える
        self.forget_memory()
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(None), AccessCheck.ALLOWED)
        self.ask.return_value = {"allowed": True, "min_version": "", "require_uid": True}
        AccessCheck.verify(None)
        self.ask.return_value = None
        self.assertEqual(AccessCheck.verify(None), AccessCheck.NO_UID)

    def test_a_non_string_uid_is_no_uid(self):
        self.assertEqual(AccessCheck.verify(12345), AccessCheck.NO_UID)


class TestEnforce(_Case):
    def test_a_banned_uid_terminates_once(self):
        self.ask.return_value = dict(DENY)
        AccessCheck.enforce(UID)
        self.terminate.assert_called_once_with(0)

    def test_others_do_not_terminate_and_return_the_result(self):
        self.assertEqual(AccessCheck.enforce(UID), AccessCheck.ALLOWED)
        self.ask.return_value = None
        self.assertEqual(AccessCheck.enforce(OTHER), AccessCheck.UNREACHABLE)
        self.assertEqual(AccessCheck.enforce(None), AccessCheck.NO_UID)
        self.terminate.assert_not_called()

    def test_enforce_writes_nothing_anywhere_when_banned(self):
        for banned_by in ("answer", "record"):
            self.forget_memory()
            self.terminate.reset_mock()
            self.ask.return_value = dict(DENY) if banned_by == "answer" else None
            written = []
            with patch.object(DebugLog, "write", side_effect=written.append), \
                 patch.object(DebugLog, "exception", side_effect=written.append), \
                 patch("builtins.print") as printed, \
                 contextlib.redirect_stdout(io.StringIO()) as out, \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                AccessCheck.enforce(UID)
            self.terminate.assert_called_once_with(0)
            self.assertEqual((written, out.getvalue(), err.getvalue()), ([], "", ""), banned_by)
            printed.assert_not_called()

    def test_the_production_hook_is_os_exit(self):
        self.assertIs(REAL_TERMINATE, os._exit)


class TestEnforceStartup(_Case):
    def test_a_banned_latest_user_terminates(self):
        self.ask.return_value = dict(DENY)
        with patch.object(VRChatLauncher, "latest_user_id", return_value=UID) as latest:
            AccessCheck.enforce_startup()
        latest.assert_called_once_with(config.VRCHAT_LOG_DIR)
        self.ask.assert_called_once_with(UID, config.ACCESS_STARTUP_TIMEOUT_SEC)
        self.terminate.assert_called_once_with(0)

    def test_others_do_not_terminate(self):
        self.ask.return_value = None
        with patch.object(VRChatLauncher, "latest_user_id", return_value=UID):
            AccessCheck.enforce_startup()           # 聞けなくても起動は続く
        self.terminate.assert_not_called()

    def test_no_uid_asks_nothing(self):
        with patch.object(VRChatLauncher, "latest_user_id", return_value=None):
            AccessCheck.enforce_startup()
        self.ask.assert_not_called()
        self.terminate.assert_not_called()

    def test_a_read_failure_neither_crashes_nor_terminates(self):
        with patch.object(VRChatLauncher, "latest_user_id", side_effect=OSError("x")):
            AccessCheck.enforce_startup()
        self.ask.assert_not_called()
        self.terminate.assert_not_called()


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestAskServer(unittest.TestCase):
    """本物の _ask_server（REAL_ASK_SERVER）。urllib.request.urlopen を偽物にして呼ぶ（本物のネットワークへは出ない）"""

    def _ask(self, body=None, uid=UID, timeout=2.5, raises=None):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req, timeout))
            if raises is not None:
                raise raises
            return _FakeResponse(body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))

        with patch.object(urllib.request, "urlopen", fake_urlopen):
            result = REAL_ASK_SERVER(uid, timeout)
        self.calls = calls
        return result

    GOOD = {"allowed": True, "min_version": "v1.2.3", "require_uid": True}

    def test_a_good_answer(self):
        self.assertEqual(self._ask(self.GOOD), self.GOOD)
        self.assertEqual(self._ask({"allowed": False, "min_version": "v0.0.0", "require_uid": False}),
                         {"allowed": False, "min_version": "v0.0.0", "require_uid": False})

    def test_the_request_carries_only_the_uid_and_the_version(self):
        self._ask(self.GOOD, timeout=2.5)
        (req, timeout), = self.calls
        self.assertEqual(req.full_url, f"{ConnectDB.SUPABASE_URL}/rest/v1/rpc/check_access")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"p_vrchat_uid": UID, "p_version": config.APP_VERSION})
        self.assertEqual(req.get_header("Apikey"), ConnectDB.SUPABASE_KEY)
        self.assertEqual(req.get_header("Authorization"), f"Bearer {ConnectDB.SUPABASE_KEY}")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(timeout, 2.5)

    def test_a_missing_uid_is_sent_as_null(self):
        self._ask(self.GOOD, uid=None)
        self.assertEqual(json.loads(self.calls[0][0].data), {"p_vrchat_uid": None, "p_version": config.APP_VERSION})

    def test_a_failure_is_no_answer(self):
        cases = [
            urllib.error.HTTPError("u", 404, "not found", {}, None),
            urllib.error.HTTPError("u", 500, "boom", {}, None),
            urllib.error.URLError("offline"),
            socket.timeout("slow"),
            TimeoutError("slow"),
            OSError("refused"),
            ConnectionResetError("reset"),
        ]
        for exc in cases:
            self.assertIsNone(self._ask(raises=exc), repr(exc))

    def test_a_bad_answer_is_no_answer(self):
        for body in (b"not json", b"", b"[]", b"null", b'"x"', b"123",
                     {"allowed": "true"}, {"allowed": 1}, {"allowed": None}, {"min_version": "v1.0.0"}, []):
            self.assertIsNone(self._ask(body), body)

    def test_the_other_fields_are_made_safe(self):
        self.assertEqual(self._ask({"allowed": True}),
                         {"allowed": True, "min_version": "", "require_uid": True})
        self.assertEqual(self._ask({"allowed": True, "min_version": 5, "require_uid": "no"}),
                         {"allowed": True, "min_version": "", "require_uid": True})
        self.assertEqual(self._ask({"allowed": True, "min_version": None, "require_uid": False}),
                         {"allowed": True, "min_version": "", "require_uid": False})

    def test_a_failure_writes_only_the_kind_of_error(self):
        for exc in (urllib.error.URLError(f"{UID} {ConnectDB.SUPABASE_URL}"), OSError(UID)):
            written = []
            with patch.object(DebugLog, "write", side_effect=written.append), \
                 patch.object(DebugLog, "exception", side_effect=written.append), \
                 patch("builtins.print") as printed:
                self._ask(raises=exc)
            self.assertEqual(len(written), 1, written)
            self.assertIn(type(exc).__name__, written[0])
            for text in written:
                self.assertNotIn(UID, text)
                self.assertNotIn(ConnectDB.SUPABASE_URL, text)
                self.assertNotIn("example.supabase", text)
            printed.assert_not_called()

    def test_a_compiled_exe_uses_only_the_bundled_env(self):
        """exe では、同梱の .env だけを見る（環境変数・ConnectDB が読んだ値は見ない。os.environ にも入れない）"""
        bundled = {"SUPABASE_URL": "https://bundled.example", "SUPABASE_KEY": "bundled-key"}
        env = {"SUPABASE_URL": "https://from-env.example", "SUPABASE_KEY": "env-key"}
        with patch.object(AccessCheck, "__compiled__", object(), create=True), \
             patch.object(AccessCheck, "dotenv_values", return_value=bundled) as values, \
             patch.dict(os.environ, env):
            result = self._ask(self.GOOD)
            self.assertEqual(os.environ["SUPABASE_URL"], env["SUPABASE_URL"], "os.environ を書き換えない")
        self.assertEqual(result, self.GOOD)
        values.assert_called_once_with(Path(AccessCheck.__file__).resolve().parent / ".env")
        (req, _timeout), = self.calls
        self.assertEqual(req.full_url, "https://bundled.example/rest/v1/rpc/check_access")
        self.assertEqual(req.get_header("Apikey"), "bundled-key")
        self.assertNotIn("from-env", req.full_url)

    def test_a_compiled_exe_without_a_bundled_setting_has_no_answer(self):
        with patch.object(AccessCheck, "__compiled__", object(), create=True), \
             patch.object(AccessCheck, "dotenv_values", return_value={}):
            self.assertIsNone(self._ask(self.GOOD))
        self.assertEqual(self.calls, [], "通信しない")

    def test_a_development_run_without_a_setting_runs_without_asking(self):
        with patch.object(ConnectDB, "SUPABASE_URL", None), patch.object(ConnectDB, "SUPABASE_KEY", None):
            result = self._ask(self.GOOD)
        self.assertEqual(result, {"allowed": True, "min_version": "", "require_uid": False})
        self.assertEqual(self.calls, [], "通信しない")

    def test_the_url_and_the_key_are_hidden_from_the_debug_log(self):
        with patch.object(DebugLog, "add_secret") as add:
            self._ask(self.GOOD)
        added = [c.args[0] for c in add.call_args_list]
        self.assertIn(ConnectDB.SUPABASE_URL, added)
        self.assertIn(ConnectDB.SUPABASE_KEY, added)


class TestRecord(_Case):
    def test_the_file_has_no_raw_uid(self):
        self.ask.return_value = dict(DENY)
        AccessCheck.verify(UID)
        AccessCheck.verify(OTHER)           # BANNED のメモリがあっても別の UID は聞く
        raw = AccessCheck._record_path().read_text(encoding="ascii")
        plain = AccessCheck.SecretStore.unprotect(raw)
        self.assertNotIn(UID, raw)
        self.assertNotIn(UID, plain)
        self.assertNotIn(OTHER, plain)
        self.assertIn(AccessCheck._key(UID), plain)
        self.assertEqual(len(AccessCheck._key(UID)), 64)
        self.assertEqual(AccessCheck._key(UID), hashlib.sha256(UID.encode("utf-8")).hexdigest())

    def test_the_record_holds_the_time_and_the_settings(self):
        self.ask.return_value = {"allowed": True, "min_version": "v1.5.0", "require_uid": False}
        AccessCheck.verify(UID)
        record = self.record()
        self.assertEqual(record["users"][AccessCheck._key(UID)], {"ok_at": self.now[0], "banned": False})
        self.assertEqual((record["min_version"], record["require_uid"]), ("v1.5.0", False))

    def test_the_file_is_written_by_replacing_a_temp_file(self):
        with patch.object(AccessCheck.os, "replace", wraps=os.replace) as replace:
            AccessCheck.verify(UID)
        replace.assert_called()
        src, dst = replace.call_args.args
        self.assertEqual(Path(dst), AccessCheck._record_path())
        self.assertNotEqual(Path(src), Path(dst))
        self.assertEqual(sorted(p.name for p in AccessCheck._record_path().parent.iterdir()), ["access.dat"])

    def test_the_location_is_next_to_the_settings(self):
        self.assertEqual(AccessCheck._record_path(), self.dir / "access.dat")

    def test_a_write_failure_is_silent(self):
        with patch.object(AccessCheck.os, "replace", side_effect=OSError("denied")):
            self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)
        with patch.object(AccessCheck.SecretStore, "protect", return_value=None):
            self.forget_memory()
            self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)

    def test_the_uid_is_not_in_the_bug_report(self):
        self.ask.return_value = dict(DENY)
        AccessCheck.verify(UID)
        home = r"C:\Users\Alice"
        debug = self.dir / "debug.log"
        debug.write_text("debug line\n", encoding="utf-8")
        vrchat = self.dir / "output_log.txt"
        vrchat.write_text("vrchat line\n", encoding="utf-8")
        with patch.object(BugReport.os.path, "expanduser", return_value=home), \
             patch.object(BeginMiss, "folder", return_value=self.dir / "begin_miss"):
            _name, data = BugReport.build_report(
                "動かない", 2, {k: True for k, _l in BugReport.ATTACHMENTS}, "画面\n",
                str(vrchat), {"win_count": 6}, datetime(2026, 10, 1, 12, 34, 56),
                debug_log_path=debug, version="v9.9.9")
        self.assertNotIn(UID.encode(), data)
        z = zipfile.ZipFile(io.BytesIO(data))
        for name in z.namelist():
            self.assertNotIn(UID, z.read(name).decode("utf-8", "replace"), name)
            self.assertNotIn("access", name)


class TestTooOld(_Case):
    def _learn(self, minimum):
        self.forget_memory()                        # 1 時間以内は聞き直さないので、入れ直してから聞く
        self.ask.return_value = {"allowed": True, "min_version": minimum, "require_uid": True}
        AccessCheck.verify(UID)

    def test_a_larger_minimum_is_too_old(self):
        with patch.object(config, "APP_VERSION", "v1.4.1"):
            self._learn("v1.5.0")
            self.assertEqual(AccessCheck.too_old(), "v1.5.0")
            self._learn("v2.0")
            self.assertEqual(AccessCheck.too_old(), "v2.0")
            self._learn("1.4.2")
            self.assertEqual(AccessCheck.too_old(), "1.4.2")

    def test_the_same_or_a_smaller_minimum_is_fine(self):
        with patch.object(config, "APP_VERSION", "v1.4.1"):
            for minimum in ("v1.4.1", "v1.4.0", "v1.3.9", "v0.0.0", "v1.4"):
                self._learn(minimum)
                self.assertEqual(AccessCheck.too_old(), "", minimum)

    def test_a_value_that_cannot_be_read_is_fine(self):
        with patch.object(config, "APP_VERSION", "v1.4.1"):
            for minimum in ("", "latest", "あ", "v", "..."):
                self._learn(minimum)
                self.assertEqual(AccessCheck.too_old(), "", minimum)

    def test_nothing_known_is_fine(self):
        self.assertEqual(AccessCheck.too_old(), "")

    def test_the_minimum_survives_a_restart(self):
        with patch.object(config, "APP_VERSION", "v1.4.1"):
            self._learn("v1.5.0")
            self.forget_memory()
            self.assertEqual(AccessCheck.too_old(), "v1.5.0")

    def test_a_no_uid_answer_also_teaches_the_minimum(self):
        with patch.object(config, "APP_VERSION", "v1.4.1"):
            self.ask.return_value = {"allowed": True, "min_version": "v9.0.0", "require_uid": True}
            AccessCheck.verify(None)
            self.assertEqual(AccessCheck.too_old(), "v9.0.0")

    def test_an_old_version_is_not_stopped_by_verify_itself(self):
        with patch.object(config, "APP_VERSION", "v1.4.1"):
            self._learn("v9.0.0")
            self.assertEqual(AccessCheck.verify(UID), AccessCheck.ALLOWED)


class TestRecheck(_Case):
    def setUp(self):
        super().setUp()
        self.started = []

        class _Thread:
            def __init__(inner, target):
                inner.target = target

            def start(inner):
                self.started.append(inner)

        p = patch.object(TaskStats, "thread", lambda name, target, *a, **k: _Thread(target))
        p.start()
        self.addCleanup(p.stop)

    def test_it_does_nothing_before_the_memory_time(self):
        AccessCheck.verify(UID)
        self.mono[0] += config.ACCESS_MEMORY_SEC - 1
        AccessCheck.recheck_async(UID)
        self.assertEqual(self.started, [])

    def test_it_asks_once_in_the_background_after_the_memory_time(self):
        AccessCheck.verify(UID)
        self.mono[0] += config.ACCESS_MEMORY_SEC
        AccessCheck.recheck_async(UID)
        AccessCheck.recheck_async(UID)              # 走っている間は重ねない
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.ask.call_count, 1, "スレッドが走るまでは聞かない")
        self.started[0].target()
        self.assertEqual(self.ask.call_count, 2)
        AccessCheck.recheck_async(UID)              # 聞いたので、次の 1 時間はしない
        self.assertEqual(len(self.started), 1)

    def test_it_can_run_again_after_the_hour(self):
        AccessCheck.verify(UID)
        for n in (1, 2):
            self.mono[0] += config.ACCESS_MEMORY_SEC
            AccessCheck.recheck_async(UID)
            self.started[-1].target()
            self.assertEqual(len(self.started), n)

    def test_it_asks_again_after_an_unreachable_attempt_only_after_an_hour(self):
        self.ask.return_value = None
        AccessCheck.verify(UID)
        self.mono[0] += config.ACCESS_MEMORY_SEC
        AccessCheck.recheck_async(UID)
        self.started[0].target()
        AccessCheck.recheck_async(UID)
        self.assertEqual(len(self.started), 1, "つながらなくても、1 時間に 1 回")
        self.terminate.assert_not_called()

    def test_a_uid_never_asked_or_empty_is_ignored(self):
        for uid in (UID, None, "", "  ", 5):
            AccessCheck.recheck_async(uid)
        self.assertEqual(self.started, [])

    def test_a_banned_answer_terminates(self):
        AccessCheck.verify(UID)
        self.mono[0] += config.ACCESS_MEMORY_SEC
        self.ask.return_value = dict(DENY)
        AccessCheck.recheck_async(UID)
        self.started[0].target()
        self.terminate.assert_called_once_with(0)

    def test_a_failure_lets_the_next_one_run(self):
        AccessCheck.verify(UID)
        self.mono[0] += config.ACCESS_MEMORY_SEC
        self.ask.side_effect = RuntimeError("x")
        AccessCheck.recheck_async(UID)
        with self.assertRaises(RuntimeError):
            self.started[0].target()
        self.assertFalse(AccessCheck._rechecking)


# ── LogMonitor ───────────────────────────────
def _login(uid=UID, name="tester"):
    return f"User Authenticated: {name} ({uid})"


class _MonitorCase(_Case):
    """窓の監視（LogMonitor._run）を裏のスレッドで回して、確認の前後の動きを見る"""

    def setUp(self):
        super().setUp()
        for target, name, value in ((config, "LOG_POLL_INTERVAL", 0.01),
                                    (config, "ACCESS_RETRY_SEC", 0.05)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.send = MagicMock(return_value=7)
        p = patch.object(ConnectDB, "send_Users", self.send)
        p.start()
        self.addCleanup(p.stop)
        self.threads = []
        self.addCleanup(self._stop_all)

    def _stop_all(self):
        for monitor, thread in self.threads:
            monitor._running = False
            monitor._stop_event.set()
            thread.join(5)

    def log_file(self, lines):
        path = self.dir / "output_log.txt"
        path.write_text("".join(PREFIX + line + "\n" for line in lines), encoding="utf-8")
        return path

    def append(self, path, *lines):
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(PREFIX + line + "\n" for line in lines))

    def monitor(self, path):
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path), {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._start_daemon = lambda f, *a: f(*a)         # 統計用の取得もその場で（待たずに確かめるため）
        monitor._process = MagicMock()
        return monitor

    def run_in_thread(self, monitor):
        monitor._running = True
        monitor._stop_event.clear()
        thread = threading.Thread(target=monitor._run, daemon=True)
        self.threads.append((monitor, thread))
        thread.start()
        return thread

    def wait_for(self, condition, what, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail(f"待ったが起きなかった: {what}")

    def started(self, monitor):
        return any("監視開始" in m for m in monitor.logs)

    def notices(self, monitor, text):
        return sum(text in m for m in monitor.logs)


UNREACHABLE_TEXT = "サーバーに確認できないため、この窓は操作しません"
NO_UID_TEXT = "ログインの行が見つからないため、この窓は操作しません"


class TestMonitorWaitsForTheAnswer(_MonitorCase):
    def test_an_ok_starts_the_window_and_then_fetches_the_statistics_id(self):
        monitor = self.monitor(self.log_file([_login()]))
        order = []
        self.ask.side_effect = lambda uid, timeout: (order.append("ask"), dict(ALLOW))[1]
        self.send.side_effect = lambda uid, name=None: (order.append("send"), 7)[1]
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.started(monitor), "監視開始")
        self.wait_for(lambda: self.send.call_count == 1, "send_Users")
        self.assertEqual(order, ["ask", "send"], "確認が先・統計用の番号は後")
        self.send.assert_called_once_with(UID, "tester")
        self.assertEqual(monitor.st.transformed_uid, 7)
        self.assertNotIn(UNREACHABLE_TEXT, "".join(monitor.logs))

    def test_the_statistics_id_is_not_fetched_before_the_ok(self):
        monitor = self.monitor(self.log_file([_login()]))
        self.ask.return_value = None
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.notices(monitor, UNREACHABLE_TEXT), "文言")
        time.sleep(0.2)
        self.send.assert_not_called()
        self.assertFalse(self.started(monitor))
        self.ask.return_value = dict(ALLOW)
        self.wait_for(lambda: self.started(monitor), "監視開始")
        self.wait_for(lambda: self.send.call_count == 1, "send_Users")
        self.send.assert_called_once_with(UID, "tester")

    def test_while_unreachable_no_line_is_processed_and_the_notice_is_once(self):
        path = self.log_file([_login()])
        monitor = self.monitor(path)
        self.ask.return_value = None
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.notices(monitor, UNREACHABLE_TEXT), "文言")
        self.append(path, "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
                    "This round is taking place at Sewers (12) and the round type is Classic",
                    "Verified Round End")
        calls_before = self.ask.call_count
        self.wait_for(lambda: self.ask.call_count >= calls_before + 3, "聞き直し（30 秒ごと。ここでは短くしてある）")
        monitor._process.assert_not_called()
        self.send.assert_not_called()
        self.assertEqual(self.notices(monitor, UNREACHABLE_TEXT), 1, monitor.logs)
        self.assertEqual(self.notices(monitor, NO_UID_TEXT), 0)

    def test_when_it_turns_ok_the_window_reads_the_log_again_and_starts(self):
        path = self.log_file([_login()])
        monitor = self.monitor(path)
        self.ask.return_value = None
        spy = MagicMock(wraps=monitor._detect_instance_from_log)
        monitor._detect_instance_from_log = spy
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.notices(monitor, UNREACHABLE_TEXT), "文言")
        self.assertEqual(spy.call_count, 1)
        self.append(path, "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)",
                    "[Behaviour] OnPlayerJoined other (usr_00000000-aaaa-bbbb-cccc-0000000000ff)")
        self.ask.return_value = dict(ALLOW)
        self.wait_for(lambda: self.started(monitor), "監視開始")
        self.assertEqual(spy.call_count, 2, "待った場合は遡りをやり直す")
        self.assertIn("usr_00000000-aaaa-bbbb-cccc-0000000000ff", monitor.st.players,
                      "待つ間に起きた入室を取り込んでいる")
        self.assertEqual(monitor.st.log_pos, path.stat().st_size, "待つ間の行を読み直すのではなく、末尾から監視する")
        monitor._process.assert_not_called()

    def test_without_a_wait_the_log_is_read_once(self):
        monitor = self.monitor(self.log_file([_login()]))
        spy = MagicMock(wraps=monitor._detect_instance_from_log)
        monitor._detect_instance_from_log = spy
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.started(monitor), "監視開始")
        self.assertEqual(spy.call_count, 1)

    def test_a_missing_uid_picks_up_only_the_login_line_and_starts_when_it_comes(self):
        path = self.log_file(["[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"])
        monitor = self.monitor(path)
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.notices(monitor, NO_UID_TEXT), "文言")
        self.append(path, "Verified Round End", "[Behaviour] OnPlayerJoined a (usr_00000000-aaaa-bbbb-cccc-0000000000aa)")
        time.sleep(0.2)
        monitor._process.assert_not_called()
        self.assertFalse(self.started(monitor))
        self.ask.assert_called()                    # UID なしで聞いている
        self.assertTrue(all(c.args[0] is None for c in self.ask.call_args_list))
        self.append(path, "[Authentication - x] Preforming post login checks for tester (%s)" % UID)
        self.wait_for(lambda: self.started(monitor), "監視開始")
        self.assertEqual(monitor.st.local_user_id, UID)
        self.assertEqual(self.ask.call_args.args[0], UID)
        self.assertEqual(self.notices(monitor, NO_UID_TEXT), 1, "同じ理由では 1 回だけ")
        self.wait_for(lambda: self.send.call_count == 1, "send_Users")
        self.send.assert_called_once_with(UID, "tester")

    def test_a_login_line_checks_at_once_not_after_the_retry_time(self):
        path = self.log_file([])
        monitor = self.monitor(path)
        with patch.object(config, "ACCESS_RETRY_SEC", 3600.0):
            self.run_in_thread(monitor)
            self.wait_for(lambda: self.notices(monitor, NO_UID_TEXT), "文言")
            self.append(path, _login())
            self.wait_for(lambda: self.started(monitor), "監視開始")

    def test_without_a_uid_the_server_can_say_it_is_not_needed(self):
        monitor = self.monitor(self.log_file(["[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"]))
        self.ask.return_value = {"allowed": True, "min_version": "", "require_uid": False}
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.started(monitor), "監視開始")
        self.assertEqual(monitor.st.local_user_id, "")
        time.sleep(0.1)
        self.send.assert_not_called()               # UID が無いので統計用の番号は取りに行かない
        self.assertEqual(self.notices(monitor, NO_UID_TEXT), 0)

    def test_a_missing_uid_and_an_unreachable_server_says_the_server_text_once_per_reason(self):
        path = self.log_file([])
        monitor = self.monitor(path)
        self.ask.return_value = None
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.notices(monitor, NO_UID_TEXT), "文言")
        self.append(path, _login())
        self.wait_for(lambda: self.notices(monitor, UNREACHABLE_TEXT), "文言")
        time.sleep(0.2)
        self.assertEqual(self.notices(monitor, NO_UID_TEXT), 1)
        self.assertEqual(self.notices(monitor, UNREACHABLE_TEXT), 1)

    def test_stopping_leaves_the_wait_at_once(self):
        monitor = self.monitor(self.log_file([_login()]))
        self.ask.return_value = None
        with patch.object(config, "LOG_POLL_INTERVAL", 30.0), patch.object(config, "ACCESS_RETRY_SEC", 3600.0):
            thread = self.run_in_thread(monitor)
            self.wait_for(lambda: self.notices(monitor, UNREACHABLE_TEXT), "文言")
            started = time.monotonic()
            monitor._running = False
            monitor._stop_event.set()
            thread.join(2.0)
            self.assertFalse(thread.is_alive(), "待ちから抜けていない")
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(self.started(monitor))
        self.send.assert_not_called()

    def test_the_log_shrinking_while_waiting_does_not_break_the_wait(self):
        path = self.log_file([_login()])
        monitor = self.monitor(path)
        self.ask.return_value = None
        self.run_in_thread(monitor)
        self.wait_for(lambda: self.notices(monitor, UNREACHABLE_TEXT), "文言")
        path.write_text("", encoding="utf-8")       # ログが切り替わった（前より短くなる）
        self.append(path, _login(OTHER, name="t"))
        self.wait_for(lambda: monitor.st.local_user_id == OTHER, "新しいログインの行")

    def test_a_banned_answer_ends_the_process_without_a_word(self):
        monitor = self.monitor(self.log_file([_login()]))
        self.ask.return_value = dict(DENY)
        self.terminate.side_effect = SystemExit         # 裏のスレッドを黙って終わらせる（本物の os._exit の代わり）
        written = []
        with patch.object(DebugLog, "write", side_effect=written.append):
            thread = self.run_in_thread(monitor)
            self.wait_for(lambda: self.terminate.call_count >= 1, "終わらせる処理")
        self.terminate.assert_called_once_with(0)
        self.send.assert_not_called()
        self.assertFalse(self.started(monitor))
        for text in monitor.logs:
            self.assertNotIn(UNREACHABLE_TEXT, text)
            self.assertNotIn(NO_UID_TEXT, text)
        self.assertNotIn("締め出", "".join(monitor.logs) + "".join(map(str, written)))


class TestMonitorVerifyAccess(_MonitorCase):
    def test_a_banned_result_that_comes_back_logs_no_reason(self):
        monitor = self.monitor(Path("unused.txt"))
        self.ask.return_value = dict(DENY)
        monitor.st.local_user_id = UID
        self.assertFalse(monitor._verify_access())
        self.terminate.assert_called_once_with(0)
        self.assertEqual(monitor.logs, [])

    def test_apply_user_auth_only_stores_the_name_and_the_uid(self):
        monitor = self.monitor(Path("unused.txt"))
        with patch.object(monitor, "_start_daemon") as daemon:
            monitor._apply_user_auth(LogParser.parse(PREFIX + _login(name="タロウ")))
        daemon.assert_not_called()
        self.ask.assert_not_called()
        self.assertEqual((monitor.st.local_user_id, monitor.st.local_player_name), (UID, "タロウ"))

    def test_the_poll_loop_asks_for_the_hourly_recheck_with_the_uid(self):
        monitor = self.monitor(self.log_file([_login()]))
        monitor.st.local_user_id = UID
        monitor._running = True
        monitor._stop_event.set()                   # 1 周で抜ける
        with patch.object(AccessCheck, "recheck_async") as recheck, \
             patch.object(monitor, "_verify_access", return_value=True):
            monitor._run()
        recheck.assert_called_with(UID)


class TestLoginLineWhileRunning(_MonitorCase):
    LOGIN = "User Authenticated: tester ({})"
    POST_LOGIN = "[Authentication - abc] Preforming post login checks for tester ({})"

    def _real_monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path("unused.txt")), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def test_a_banned_login_line_terminates_before_anything_is_stored_or_logged(self):
        self.ask.return_value = dict(DENY)
        self.terminate.side_effect = _Terminated
        for fmt in (self.LOGIN, self.POST_LOGIN):
            self.terminate.reset_mock()
            self.forget_memory()
            monitor = self._real_monitor()
            with patch.object(DebugLog, "write") as written, self.assertRaises(_Terminated):
                monitor._process(PREFIX + fmt.format(UID))
            self.terminate.assert_called_once_with(0)
            self.assertEqual(monitor.st.local_user_id, "", fmt)
            self.assertEqual(monitor.st.local_player_name, "", fmt)
            self.assertFalse(any(UID in m for m in monitor.logs), fmt)
            self.assertFalse(any(UID in str(c) for c in written.call_args_list), fmt)

    def test_a_normal_login_line_goes_on(self):
        for fmt in (self.LOGIN, self.POST_LOGIN):
            monitor = self._real_monitor()
            monitor._process(PREFIX + fmt.format(UID))
            self.terminate.assert_not_called()
            self.assertEqual(monitor.st.local_user_id, UID, fmt)

    def test_an_unreachable_server_does_not_stop_a_running_window(self):
        self.ask.return_value = None
        monitor = self._real_monitor()
        monitor._process(PREFIX + self.LOGIN.format(UID))
        self.terminate.assert_not_called()
        self.assertEqual(monitor.st.local_user_id, UID)
        self.assertEqual(monitor.logs, [], "動いている窓には何も出さない")

    def test_the_login_line_while_running_does_not_start_the_statistics_fetch(self):
        monitor = self._real_monitor()
        with patch.object(monitor, "_start_daemon") as daemon:
            monitor._process(PREFIX + self.LOGIN.format(UID))
        daemon.assert_not_called()


# ── 画面（mainGUI）──────────────────────────
class _Stopped(Exception):
    """窓の準備まで進んだことを知らせる目印"""


class TestTooOldInTheGui(_Case):
    def _app(self, minimum):
        app = mainGUI.App.__new__(mainGUI.App)
        app.logs = []
        app._log = app.logs.append
        app._start_quiet = False
        app.keepOn_set = True
        patcher = patch.object(AccessCheck, "too_old", return_value=minimum)
        patcher.start()
        self.addCleanup(patcher.stop)
        return app

    def test_start_refuses_and_says_why(self):
        app = self._app("v9.0.0")
        with patch.object(mainGUI.messagebox, "showwarning") as warned, \
             patch.object(app, "_resolve_tab_ports", create=True) as resolved:
            mainGUI.App._start(app)
        warned.assert_called_once()
        resolved.assert_not_called()                # 始めずに戻った
        self.assertEqual(app.logs, ["[更新] この版は使えません（v9.0.0 以上が必要です）。更新してください"])

    def test_the_quiet_start_does_not_show_a_dialog(self):
        app = self._app("v9.0.0")
        app._start_quiet = True
        with patch.object(mainGUI.messagebox, "showwarning") as warned:
            mainGUI.App._start(app)
        warned.assert_not_called()
        self.assertEqual(len(app.logs), 1)

    def test_a_fine_version_starts_as_before(self):
        app = self._app("")
        with patch.object(mainGUI.messagebox, "showwarning") as warned, \
             patch.object(app, "_resolve_tab_ports", side_effect=_Stopped, create=True):
            with self.assertRaises(_Stopped):               # 断らずに先（窓の準備）へ進んだ
                mainGUI.App._start(app)
        warned.assert_not_called()
        self.assertFalse(any("この版は使えません" in m for m in app.logs))

    def test_the_text(self):
        self.assertEqual(mainGUI._too_old_text("v1.5.0"),
                         "[更新] この版は使えません（v1.5.0 以上が必要です）。更新してください")


if __name__ == "__main__":
    unittest.main()
