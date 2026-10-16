"""FO（依頼者 2026-10-16「CPU を減らす 3 件、全部いいと思います」）:
① プロセスの一覧を取る回数を減らす（ProcessCheck.RunningNamesCache・主催リストの元と外部ツールのボタンで共有）
② OSC の受信（固定ポート）で速さと接地以外は解析しない
③ 画面（Tk）のスレッドの内訳を [CPU] の行に出す（UiInbox・TaskStats.tk_timed）
本物のプロセスの一覧・OSC・入力は使わない（一覧・生死・時計・ソケットは偽物）"""
from tests.support import *  # noqa: F401,F403
import types
import ProcStats
import TaskStats
import UiInbox


# ── 道具 ─────────────────────────────
class _Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class _World:
    """偽のプロセスの世界。by_name は 小文字の exe 名 → PID の集合。list_calls は一覧を取った回数"""

    def __init__(self, by_name=None):
        self.by_name = {k: set(v) for k, v in (by_name or {}).items()}
        self.list_calls = 0
        self.alive_calls = []
        self.renamed = {}                   # PID → いまその PID が名乗る名前（使い回された）
        self.fail_list = False

    def list_fn(self):
        self.list_calls += 1
        if self.fail_list:
            return None
        return {k: set(v) for k, v in self.by_name.items()}

    def alive_fn(self, pid, exe_name=None):
        self.alive_calls.append((pid, exe_name))
        for name, pids in self.by_name.items():
            if pid in pids:
                return self.renamed.get(pid, name) == exe_name if exe_name else True
        return False

    def kill(self, name, pid):
        self.by_name[name].discard(pid)
        if not self.by_name[name]:
            del self.by_name[name]

    def start(self, name, pid):
        self.by_name.setdefault(name, set()).add(pid)


def _cache(world=None, clock=None, min_sec=10.0):
    world = world or _World()
    clock = clock or _Clock()
    return ProcessCheck.RunningNamesCache(min_sec=min_sec, list_fn=world.list_fn, alive_fn=world.alive_fn,
                                          clock=clock), world, clock


LIST_TOOL = "ton_listtool.exe"


# ── ① 取りまとめ ─────────────────────────────
class TestNamesCache(unittest.TestCase):
    def test_the_default_interval_is_the_config_one(self):
        self.assertEqual(config.PROCESS_LIST_MIN_SEC, 10.0)
        self.assertEqual(ProcessCheck.RunningNamesCache()._min_sec, 10.0)

    def test_names_within_the_interval_is_the_last_list(self):
        cache, world, clock = _cache(_World({"a.exe": {1}, "b.exe": {2}}))
        first = cache.names()
        clock.now += 9.9
        second = cache.names()
        self.assertEqual(world.list_calls, 1, "10 秒以内は取り直さない")
        self.assertEqual(first, frozenset({"a.exe", "b.exe"}))
        self.assertIs(first, second)

    def test_names_after_the_interval_takes_the_list_again(self):
        cache, world, clock = _cache(_World({"a.exe": {1}}))
        cache.names()
        world.start("c.exe", 3)
        clock.now += 10.0
        self.assertEqual(cache.names(), frozenset({"a.exe", "c.exe"}))
        self.assertEqual(world.list_calls, 2)

    def test_max_age_can_only_lengthen_the_interval(self):
        cache, world, clock = _cache(_World({"a.exe": {1}}))
        cache.names()
        clock.now += 5.0
        cache.names(max_age=1.0)
        self.assertEqual(world.list_calls, 1, "短くはできない（一覧は最大でも 10 秒に 1 回）")
        clock.now += 20.0
        cache.names(max_age=60.0)
        self.assertEqual(world.list_calls, 1, "60 秒までは前回の一覧")
        clock.now += 50.0
        cache.names(max_age=60.0)
        self.assertEqual(world.list_calls, 2)

    def test_a_failed_list_is_none_and_is_retried_at_the_next_call(self):
        cache, world, clock = _cache(_World({"a.exe": {1}}))
        world.fail_list = True
        self.assertIsNone(cache.names())
        self.assertFalse(cache.watch("a.exe"))
        self.assertEqual(world.list_calls, 2, "失敗は覚えない（今までどおり、呼ぶたびに取りに行く）")
        world.fail_list = False
        self.assertEqual(cache.names(), frozenset({"a.exe"}))

    def test_a_list_that_raises_is_a_failure(self):
        def boom():
            raise OSError("snapshot")
        cache = ProcessCheck.RunningNamesCache(list_fn=boom, alive_fn=lambda *a: True, clock=_Clock())
        self.assertIsNone(cache.names())
        self.assertFalse(cache.watch("a.exe"))

    def test_the_first_watch_takes_the_list_and_finds_it(self):
        cache, world, _ = _cache(_World({LIST_TOOL: {10}}))
        self.assertTrue(cache.watch("ToN_ListTool.exe"), "比較は小文字")
        self.assertEqual(world.list_calls, 1)

    def test_a_living_process_takes_no_list(self):
        cache, world, clock = _cache(_World({LIST_TOOL: {10}}))
        cache.watch(LIST_TOOL)
        for _ in range(100):
            clock.now += 3.0                    # 3 秒ごと。10 秒を何度またいでも
            self.assertTrue(cache.watch(LIST_TOOL))
        self.assertEqual(world.list_calls, 1, "生きていれば一覧は取り直さない")
        self.assertEqual(world.alive_calls[-1], (10, LIST_TOOL), "PID と名前で生死を見る")

    def test_a_missing_process_is_looked_for_once_per_interval(self):
        cache, world, clock = _cache(_World({"a.exe": {1}}))
        self.assertFalse(cache.watch(LIST_TOOL))
        for _ in range(3):
            clock.now += 3.0
            self.assertFalse(cache.watch(LIST_TOOL))
        self.assertEqual(world.list_calls, 1, "10 秒以内は取り直さない")
        world.start(LIST_TOOL, 10)
        clock.now += 3.0                        # 前回から 12 秒
        self.assertTrue(cache.watch(LIST_TOOL), "起動したのは取り直しで分かる")
        self.assertEqual(world.list_calls, 2)

    def test_a_process_that_disappears_is_seen_at_once(self):
        cache, world, clock = _cache(_World({LIST_TOOL: {10}}))
        cache.watch(LIST_TOOL)
        world.kill(LIST_TOOL, 10)
        clock.now += 3.0
        self.assertFalse(cache.watch(LIST_TOOL), "落ちた検知は遅れない（一覧の間隔の内でも）")
        clock.now += 3.0
        self.assertFalse(cache.watch(LIST_TOOL), "前回の一覧に残っていた名前で「居る」と答えない")
        self.assertEqual(world.list_calls, 1)

    def test_a_vanished_process_takes_the_list_again_after_the_interval(self):
        cache, world, clock = _cache(_World({LIST_TOOL: {10}}))
        cache.watch(LIST_TOOL)
        world.kill(LIST_TOOL, 10)
        clock.now += 10.0
        self.assertFalse(cache.watch(LIST_TOOL))
        self.assertEqual(world.list_calls, 2, "消えたときは取り直す")
        world.start(LIST_TOOL, 11)              # 立て直した（PID が変わった）
        clock.now += 10.0
        self.assertTrue(cache.watch(LIST_TOOL))
        self.assertEqual(world.list_calls, 3)
        clock.now += 3.0
        self.assertTrue(cache.watch(LIST_TOOL))
        self.assertEqual(world.list_calls, 3, "新しい PID を覚えて、また生死だけを見る")
        self.assertEqual(world.alive_calls[-1][0], 11)

    def test_a_reused_pid_is_not_the_process(self):
        cache, world, clock = _cache(_World({LIST_TOOL: {10}}))
        cache.watch(LIST_TOOL)
        world.renamed[10] = "other.exe"          # 同じ PID が別のプロセスに使い回された
        clock.now += 3.0
        self.assertFalse(cache.watch(LIST_TOOL))
        world.by_name = {"other.exe": {10}}
        clock.now += 10.0
        self.assertFalse(cache.watch(LIST_TOOL))

    def test_names_does_not_report_a_dead_one_forever_after_watch_saw_it_die(self):
        cache, world, clock = _cache(_World({LIST_TOOL: {10}, "a.exe": {1}}))
        self.assertIn(LIST_TOOL, cache.names())
        cache.watch(LIST_TOOL)
        world.kill(LIST_TOOL, 10)
        clock.now += 3.0
        cache.watch(LIST_TOOL)
        self.assertEqual(cache.names(), frozenset({"a.exe"}), "死んだと分かった名前は一覧からも外す")

    def test_another_instance_of_the_same_name_keeps_it_alive(self):
        cache, world, clock = _cache(_World({LIST_TOOL: {10, 11}}))
        cache.watch(LIST_TOOL)
        world.kill(LIST_TOOL, 10)
        clock.now += 3.0
        self.assertTrue(cache.watch(LIST_TOOL))
        self.assertEqual(world.list_calls, 1)
        world.alive_calls.clear()
        clock.now += 3.0
        self.assertTrue(cache.watch(LIST_TOOL))
        self.assertEqual([pid for pid, _ in world.alive_calls], [11], "終わった PID はもう見に行かない")

    def test_an_empty_name_is_never_running_and_takes_no_list(self):
        cache, world, _ = _cache()
        self.assertFalse(cache.watch(""))
        self.assertFalse(cache.watch(None))
        self.assertEqual(world.list_calls, 0)

    def test_the_two_watchers_share_one_list(self):
        """主催リストの元（watch）と外部ツールのボタン（names）が同じ秒に来ても、一覧は 1 回"""
        cache, world, clock = _cache(_World({LIST_TOOL: {10}}))
        names = cache.names(max_age=config.PROCESS_LIST_MIN_SEC)
        found = cache.watch(LIST_TOOL)
        clock.now += 3.0
        names2 = cache.names(max_age=config.PROCESS_LIST_MIN_SEC)
        self.assertTrue(found)
        self.assertEqual((names, names2), (frozenset({LIST_TOOL}),) * 2)
        self.assertEqual(world.list_calls, 1)

    def test_two_threads_at_once_take_one_list(self):
        world = _World({"a.exe": {1}})
        slow_calls = []

        def slow_list():
            slow_calls.append(1)
            time.sleep(0.1)
            return world.list_fn()
        cache = ProcessCheck.RunningNamesCache(list_fn=slow_list, alive_fn=world.alive_fn, clock=time.monotonic)
        results = []
        start = threading.Event()

        def call(fn):
            start.wait()
            results.append(fn())
        threads = [threading.Thread(target=call, args=(lambda: cache.names(),)),
                   threading.Thread(target=call, args=(lambda: cache.watch("a.exe"),))]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(5)
        self.assertEqual(len(slow_calls), 1, "同時に来ても一覧は 1 回（ロックの中で取る）")
        self.assertEqual(sorted(map(str, results)), sorted([str(frozenset({"a.exe"})), "True"]))


class TestPidHelpers(unittest.TestCase):
    """ctypes の呼び出しは偽の kernel32 で（本物のプロセスは開かない）"""

    def _kernel32(self, exit_code=ProcessCheck.STILL_ACTIVE, image=r"C:\Tools\ToN_ListTool.exe", handle=77,
                  image_ok=True):
        k = MagicMock()
        k.opened = []
        k.closed = []

        def open_process(access, inherit, pid):
            k.opened.append((access, inherit, pid))
            return handle

        def get_exit_code(h, ref):
            ref._obj.value = exit_code
            return 1

        def query_name(h, flags, buffer, ref):
            if not image_ok:
                return 0
            buffer.value = image
            ref._obj.value = len(image)
            return 1
        k.OpenProcess.side_effect = open_process
        k.GetExitCodeProcess.side_effect = get_exit_code
        k.QueryFullProcessImageNameW.side_effect = query_name
        k.CloseHandle.side_effect = k.closed.append
        return k

    def _alive(self, k, pid=10, name=None):
        with patch.object(ProcessCheck, "kernel32", k):
            return ProcessCheck.is_pid_alive(pid, name)

    def test_a_running_process(self):
        k = self._kernel32()
        self.assertTrue(self._alive(k))
        self.assertEqual(k.opened, [(ProcessCheck.PROCESS_QUERY_LIMITED_INFORMATION, False, 10)])
        self.assertEqual(k.closed, [77], "開いたら閉じる")
        k.CreateToolhelp32Snapshot.assert_not_called()

    def test_a_finished_process(self):
        k = self._kernel32(exit_code=0)
        self.assertFalse(self._alive(k))
        self.assertEqual(k.closed, [77])

    def test_the_name_is_compared_with_the_image_file_name(self):
        k = self._kernel32()
        self.assertTrue(self._alive(k, name="ton_listtool.exe"))
        self.assertTrue(self._alive(k, name="TON_LISTTOOL.EXE"))
        self.assertFalse(self._alive(k, name="other.exe"), "同じ PID の別のプロセス")
        k.CreateToolhelp32Snapshot.assert_not_called()      # 一覧は 1 周しない

    def test_an_unreadable_image_name_is_checked_with_process_name(self):
        k = self._kernel32(image_ok=False)
        with patch.object(ProcessCheck, "_processes",
                          side_effect=lambda: (r for r in [(4, "System"), (10, "ToN_ListTool.exe")])):
            self.assertTrue(self._alive(k, name="ton_listtool.exe"))
        with patch.object(ProcessCheck, "_processes", side_effect=lambda: (r for r in [(4, "System"), (10, "other.exe")])):
            self.assertFalse(self._alive(k, name="ton_listtool.exe"))
        with patch.object(ProcessCheck, "_processes", side_effect=OSError):
            self.assertFalse(self._alive(k, name="ton_listtool.exe"))

    def test_a_process_that_cannot_be_opened(self):
        k = self._kernel32(handle=0)
        with patch.object(ProcessCheck.ctypes, "get_last_error", return_value=87):
            self.assertFalse(self._alive(k), "その PID は無い")
        with patch.object(ProcessCheck.ctypes, "get_last_error", return_value=ProcessCheck.ERROR_ACCESS_DENIED):
            self.assertTrue(self._alive(k, name="x.exe"), "アクセス拒否は居る（名前は確かめられない）")
        k.GetExitCodeProcess.assert_not_called()

    def test_no_pid_and_failures(self):
        k = self._kernel32()
        self.assertFalse(self._alive(k, pid=0))
        k.OpenProcess.side_effect = OSError("boom")
        self.assertFalse(self._alive(k))
        k2 = self._kernel32()
        k2.GetExitCodeProcess.side_effect = lambda h, ref: 0
        self.assertFalse(self._alive(k2))
        self.assertEqual(k2.closed, [77])

    def test_pid_of_name(self):
        rows = [(4, "System"), (10, "ToN_ListTool.exe"), (11, "other.exe")]
        with patch.object(ProcessCheck, "_processes", side_effect=lambda: (r for r in rows)):
            self.assertEqual(ProcessCheck.pid_of_name("ton_listtool.EXE"), 10)
        with patch.object(ProcessCheck, "_processes", side_effect=lambda: (r for r in rows)):
            self.assertIsNone(ProcessCheck.pid_of_name("none.exe"))
        self.assertIsNone(ProcessCheck.pid_of_name(""))
        with patch.object(ProcessCheck, "_processes", side_effect=OSError):
            self.assertIsNone(ProcessCheck.pid_of_name("a.exe"))

    def test_the_default_list_groups_pids_by_lower_name(self):
        rows = [(4, "System"), (10, "Chrome.exe"), (11, "chrome.exe")]
        with patch.object(ProcessCheck, "_processes", side_effect=lambda: (r for r in rows)):
            self.assertEqual(ProcessCheck._name_pids(), {"system": {4}, "chrome.exe": {10, 11}})
        with patch.object(ProcessCheck, "_processes", side_effect=OSError):
            self.assertIsNone(ProcessCheck._name_pids())


# ── ① 画面 ─────────────────────────────
class TestTheGuiUsesTheSharedCache(unittest.TestCase):
    def test_the_host_source_asks_the_shared_cache_and_not_the_list(self):
        fake = FakeProcessCache(running=False)
        app = types.SimpleNamespace(_process_cache=fake)
        with patch.object(ProcessCheck, "is_process_running", side_effect=AssertionError("一覧を直に取った")), \
             patch.object(ProcessCheck, "running_names", side_effect=AssertionError("一覧を直に取った")):
            result = mainGUI.App._read_host_source(app)
        self.assertEqual(result, ("lost", "ToN ListTool が起動していません"))
        self.assertEqual(fake.watched, [config.TON_LISTTOOL_PROCESS])

    def test_a_cache_that_cannot_list_is_a_lost_list_tool(self):
        """一覧が取れない（watch が False）なら、今までの is_process_running と同じ「起動していません」"""
        cache, world, _ = _cache(_World())
        world.fail_list = True
        app = types.SimpleNamespace(_process_cache=cache)
        self.assertEqual(mainGUI.App._read_host_source(app)[0], "lost")

    def test_the_app_has_one_cache(self):
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("ProcessCheck.RunningNamesCache()"), 1)
        self.assertIn("self._process_cache.watch(config.TON_LISTTOOL_PROCESS)", source)
        self.assertIn("self._process_cache.names(max_age=config.PROCESS_LIST_MIN_SEC)", source)

    def _poll_buttons(self, app):
        got = []
        app._off_gui = lambda work, name=None: work()
        app._after_from_worker = lambda func, *args: got.append((func, args))
        app._finish_tool_poll = "finish"
        mainGUI.App._poll_tool_buttons(app)
        return got

    def _row(self, path):
        row = type("Row", (), {})()
        row.v_path = types.SimpleNamespace(get=lambda: path)
        return row

    def test_the_buttons_take_the_names_from_the_shared_cache(self):
        fake = FakeProcessCache(names_value=frozenset({"ton_listtool.exe"}))
        first, second = self._row(r"D:\tools\ToN_ListTool.exe"), self._row(r"D:\tools\SaveManager.exe")
        app = types.SimpleNamespace(_process_cache=fake, tool_rows=[first, second])
        got = self._poll_buttons(app)
        self.assertEqual(fake.names_asked, [config.PROCESS_LIST_MIN_SEC])
        self.assertEqual(got, [("finish", ({first: True, second: False},))])

    def test_one_list_for_the_host_poll_and_the_buttons(self):
        """3 秒ごとの 2 つの見張りを 10 分（200 回ずつ）回しても、一覧は 60 回以内（10 秒に 1 回）"""
        cache, world, clock = _cache(_World({LIST_TOOL: {10}, "savemanager.exe": {20}}))
        app = types.SimpleNamespace(_process_cache=cache, tool_rows=[self._row(r"D:\tools\ToN_ListTool.exe")])
        with tempfile.TemporaryDirectory() as tmp,              patch.object(config, "HOST_STATE_PATH", str(Path(tmp) / "none.sqlite3")):   # 本物の ListTool のファイルは読まない
            for _ in range(200):
                result = mainGUI.App._read_host_source(types.SimpleNamespace(_process_cache=cache))
                self._poll_buttons(app)
                clock.now += 3.0
        self.assertEqual(result[0], "lost")
        self.assertIn("がありません", result[1], "プロセスは居る。ファイルが無いだけ")
        self.assertLessEqual(world.list_calls, 60)
        self.assertEqual(world.list_calls, 50, "3 秒ごとの確認だと 10 秒を超える最初の確認（12 秒おき）で取り直す。"
                                               "主催リストの元は生死だけ")


# ── ② OSC ─────────────────────────────
def _pad(raw: bytes) -> bytes:
    return raw + b"\0" * (4 - len(raw) % 4)


def _osc(address: str, value) -> bytes:
    if isinstance(value, bool):
        return _pad(address.encode()) + _pad(b",T" if value else b",F")
    if isinstance(value, int):
        return _pad(address.encode()) + _pad(b",i") + struct.pack(">i", value)
    return _pad(address.encode()) + _pad(b",f") + struct.pack(">f", value)


class _Sock:
    """recvfrom が決めた順にデータを返し、尽きたら OSError（受信ループが終わる）"""

    def __init__(self, datagrams):
        self._data = list(datagrams)

    def recvfrom(self, _size):
        if not self._data:
            raise OSError("closed")
        return self._data.pop(0), ("127.0.0.1", 9001)


def _receiver(datagrams):
    receiver = OSCReceiver.VelocityReceiver(0, port=9001)
    receiver._running = True
    receiver._sock = _Sock(datagrams)
    return receiver


class TestOnlySpeedAndGroundedAreParsed(unittest.TestCase):
    def test_the_prefixes_include_the_nul(self):
        self.assertEqual(OSCReceiver._VEL_PREFIX, b"/avatar/parameters/VelocityMagnitude\0")
        self.assertEqual(OSCReceiver._GND_PREFIX, b"/avatar/parameters/Grounded\0")

    def test_other_parameters_are_not_parsed(self):
        others = [_osc("/avatar/parameters/Voice", 0.5), _osc("/avatar/parameters/VelocityX", 1.0),
                  _osc("/avatar/parameters/Upright", 1.0), _osc("/avatar/change", 0),
                  _osc("/avatar/parameters/IsLocal", True), b"#bundle\0" + b"\0" * 8, b"", b"\0\0\0\0"]
        receiver = _receiver(others)
        with patch.object(OSCReceiver, "parse_message", wraps=OSCReceiver.parse_message) as parse:
            receiver._run()
        parse.assert_not_called()
        self.assertIsNone(receiver.speed)
        self.assertFalse(receiver.ever_received)

    def test_a_longer_name_that_starts_the_same_is_not_taken(self):
        """前方一致は NUL 終端まで（別の名前を速さ・接地として扱わない）"""
        lookalikes = [_osc("/avatar/parameters/VelocityMagnitudeX", 9.0),
                      _osc("/avatar/parameters/VelocityMagnitude2", 9.0),
                      _osc("/avatar/parameters/GroundedX", 0.0), _osc("/avatar/parameters/Grounded_", True),
                      _osc("/avatar/parameters/VelocityMagnitud", 9.0)]
        receiver = _receiver(lookalikes)
        with patch.object(OSCReceiver, "parse_message", wraps=OSCReceiver.parse_message) as parse:
            receiver._run()
        parse.assert_not_called()
        self.assertIsNone(receiver.speed)
        self.assertTrue(receiver.grounded)

    def test_speed_and_grounded_are_handled_as_before(self):
        receiver = _receiver([_osc("/avatar/parameters/Voice", 0.2),
                              _osc("/avatar/parameters/VelocityMagnitude", 6.5),
                              _osc("/avatar/parameters/Grounded", True),
                              _osc("/avatar/parameters/Voice", 0.3),
                              _osc("/avatar/parameters/Grounded", False)])
        with patch.object(OSCReceiver, "parse_message", wraps=OSCReceiver.parse_message) as parse:
            receiver._run()
        self.assertEqual(parse.call_count, 3, "速さと接地の 3 件だけ")
        self.assertAlmostEqual(receiver.speed, 6.5, places=5)
        self.assertFalse(receiver.grounded)
        self.assertTrue(receiver.ever_received)
        self.assertTrue(receiver.alive)

    def test_an_int_velocity_and_a_broken_speed_message(self):
        receiver = _receiver([_osc("/avatar/parameters/VelocityMagnitude", 3),
                              _pad(b"/avatar/parameters/VelocityMagnitude")])       # 値の無い壊れたもの
        receiver._run()
        self.assertEqual(receiver.speed, 3.0, "壊れたものは今までどおり捨てる")

    def test_the_other_address_user_is_not_touched(self):
        """OSCQuery のサービスは今までどおり全部を parse_message で読む（振り分けのため。FO では触らない）"""
        source = Path("OSCQuery.py").read_text(encoding="utf-8")
        self.assertIn("parsed = OSCReceiver.parse_message(data)", source)
        self.assertNotIn("_VEL_PREFIX", source)
        self.assertEqual(OSCReceiver.parse_message(_osc("/avatar/parameters/Voice", 0.5))[0],
                         "/avatar/parameters/Voice")


# ── ③ 画面の内訳 ─────────────────────────────
def _fresh_tk(test):
    p = patch.multiple(TaskStats, _finished={}, _live={}, _tk={}, _last_wall=None, _last_process_cpu=None,
                       _last_tk_cpu=None, _last_peak_ws=None)
    p.start()
    test.addCleanup(p.stop)


def _clock(thread_time_values):
    values = iter(thread_time_values)
    return types.SimpleNamespace(thread_time=lambda: next(values), monotonic=time.monotonic)


class TestTkAccount(unittest.TestCase):
    def setUp(self):
        _fresh_tk(self)

    def test_it_adds_count_and_cpu_by_name(self):
        TaskStats.tk_account("_log", 0.5)
        TaskStats.tk_account("_log", 0.25)
        TaskStats.tk_account("_finish_host_poll", 1.0)
        self.assertEqual(TaskStats._tk, {"_log": [2, 0.75], "_finish_host_poll": [1, 1.0]})

    def test_a_negative_difference_is_zero(self):
        TaskStats.tk_account("x", -0.1)
        self.assertEqual(TaskStats._tk, {"x": [1, 0.0]})

    def test_tk_timed_counts_the_difference_of_the_calling_threads_clock(self):
        @TaskStats.tk_timed
        def _poll_x(a, b=2):
            return a + b
        with patch.object(TaskStats, "time", _clock([10.0, 10.25])):
            self.assertEqual(_poll_x(1, b=5), 6, "戻り値はそのまま")
        self.assertEqual(TaskStats._tk, {"_poll_x": [1, 0.25]})
        self.assertEqual(_poll_x.__name__, "_poll_x", "名前は変わらない")

    def test_tk_timed_counts_a_function_that_raises_and_lets_it_out(self):
        @TaskStats.tk_timed
        def _boom():
            raise ValueError("x")
        with patch.object(TaskStats, "time", _clock([1.0, 1.5])):
            with self.assertRaises(ValueError):
                _boom()
        self.assertEqual(TaskStats._tk, {"_boom": [1, 0.5]})

    def test_tk_timed_works_on_a_method(self):
        class Thing:
            @TaskStats.tk_timed
            def _tick(self):
                return self
        thing = Thing()
        self.assertIs(thing._tick(), thing)
        self.assertEqual(TaskStats._tk["_tick"][0], 1)


class TestTkSnapshotAndLine(unittest.TestCase):
    def setUp(self):
        _fresh_tk(self)

    def test_the_first_one_has_no_tk_top_and_drops_the_old(self):
        TaskStats.tk_account("old", 1.0)
        snap = TaskStats.snapshot(1.0, 10.0, 100, wall_now=50.0)
        self.assertEqual(snap["tk_top"], [])
        self.assertEqual(TaskStats._tk, {}, "始まる前の分は捨てる")

    def test_tk_top_is_in_order_of_cpu_and_is_reset(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        TaskStats.tk_account("a", 1.0)
        TaskStats.tk_account("b", 9.0)
        TaskStats.tk_account("c", 3.5)
        TaskStats.tk_account("d", 3.5)
        TaskStats.tk_account("c", 0.0)
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        self.assertEqual(snap["tk_top"], [("b", 1, 9.0), ("c", 2, 3.5), ("d", 1, 3.5), ("a", 1, 1.0)])
        self.assertEqual(TaskStats.snapshot(0.0, 0.0, 1, wall_now=1200.0)["tk_top"], [])

    def test_the_snapshot_copy_is_not_the_live_dict(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        TaskStats.tk_account("a", 1.0)
        snap = TaskStats.snapshot(0.0, 0.0, 1, wall_now=600.0)
        TaskStats.tk_account("a", 1.0)
        self.assertEqual(snap["tk_top"], [("a", 1, 1.0)], "リセットした後の書き込みは入らない")

    SNAP = {"first": False, "elapsed": 600.0, "process_delta": 25.2, "tk_delta": 8.8,
            "finished": [("do_after_round", 72, 18.4)], "live": [("LogMonitor._run", 1, 1.4)],
            "tk_top": [("_log", 1200, 3.1), ("_finish_host_poll", 195, 1.2), ("_poll_host_save", 195, 0.0)],
            "peak_ws": 0, "peak_delta": 0}

    def test_the_format(self):
        self.assertEqual(
            TaskStats.line(self.SNAP),
            "[CPU] 10分で 25.2秒（4.2%）・画面 8.8秒（上位: _log ×1200 3.1秒 / _finish_host_poll ×195 1.2秒 / "
            "_poll_host_save ×195 0.0秒）・終わった: do_after_round ×72 18.4秒・生きている: LogMonitor._run 1.4秒")

    def test_a_single_call_still_shows_its_count(self):
        snap = dict(self.SNAP, tk_top=[("_log_memory", 1, 0.1)])
        self.assertIn("画面 8.8秒（上位: _log_memory ×1 0.1秒）・終わった", TaskStats.line(snap))

    def test_only_the_top_few_with_a_mark_for_the_rest(self):
        rows = [(f"f{i}", i + 1, 10.0 - i) for i in range(10)]
        text = TaskStats.line(dict(self.SNAP, tk_top=rows))
        self.assertIn("f7 ×8 3.0秒 …）・終わった", text)
        self.assertNotIn("f8", text)
        with patch.object(config, "MEMORY_LOG_TOP", 2):
            text = TaskStats.line(dict(self.SNAP, tk_top=rows))
        self.assertIn("画面 8.8秒（上位: f0 ×1 10.0秒 / f1 ×2 9.0秒 …）・", text)

    def test_nothing_ran_has_no_breakdown(self):
        self.assertIn("・画面 8.8秒・終わった", TaskStats.line(dict(self.SNAP, tk_top=[])))
        snap = dict(self.SNAP)
        del snap["tk_top"]
        self.assertIn("・画面 8.8秒・終わった", TaskStats.line(snap), "古い形の辞書でも書ける")

    def test_an_unknown_screen_cpu_still_gets_the_breakdown(self):
        text = TaskStats.line(dict(self.SNAP, tk_delta=None))
        self.assertIn("・画面 ?（上位: _log ×1200 3.1秒", text)


class TestTheInboxAccounts(unittest.TestCase):
    def setUp(self):
        _fresh_tk(self)
        self.inbox = UiInbox.UiInbox(MagicMock())

    def test_each_function_is_counted_by_its_name(self):
        def _finish_host_poll(result):
            pass

        class Holder:
            def _log(self, text):
                pass
        holder = Holder()
        self.inbox.put(_finish_host_poll, 1)
        self.inbox.put(holder._log, "a")
        self.inbox.put(holder._log, "b")
        self.inbox.put(lambda: None)
        with patch.object(UiInbox, "time", _clock([0, 1.0, 1, 1.5, 2, 2.5, 3, 3.25])):
            self.inbox.drain()
        self.assertEqual(TaskStats._tk, {"_finish_host_poll": [1, 1.0], "_log": [2, 1.0], "<lambda>": [1, 0.25]})

    def test_a_function_that_raises_is_counted_and_the_next_one_still_runs(self):
        ran = []

        def _bad():
            raise RuntimeError("x")

        def _good():
            ran.append(1)
        self.inbox.put(_bad)
        self.inbox.put(_good)
        with patch.object(DebugLog, "exception"), patch.object(UiInbox, "time", _clock([0, 2.0, 2, 2.5])):
            self.inbox.drain()
        self.assertEqual(ran, [1])
        self.assertEqual(TaskStats._tk, {"_bad": [1, 2.0], "_good": [1, 0.5]})

    def test_a_callable_without_a_name_is_counted_by_its_type(self):
        import functools
        self.inbox.put(functools.partial(lambda x: None, 1))
        self.inbox.drain()
        self.assertEqual(list(TaskStats._tk), ["partial"])

    def test_the_real_cpu_is_nonnegative_and_the_order_is_kept(self):
        order = []
        self.inbox.put(lambda: order.append(1))
        self.inbox.put(lambda: order.append(2))
        self.inbox.drain()
        self.assertEqual(order, [1, 2])
        self.assertEqual(TaskStats._tk["<lambda>"][0], 2)
        self.assertGreaterEqual(TaskStats._tk["<lambda>"][1], 0.0)

    def test_the_inbox_work_comes_out_in_the_cpu_line(self):
        TaskStats.snapshot(0.0, 0.0, 1, wall_now=0.0)
        self.inbox.put(lambda: None)
        with patch.object(UiInbox, "time", _clock([0, 3.0])):
            self.inbox.drain()
        text = TaskStats.line(TaskStats.snapshot(3.0, 4.0, 1, wall_now=600.0))
        self.assertIn("・画面 3.0秒（上位: <lambda> ×1 3.0秒）・", text)


class TestTheTimedPeriodicFunctions(unittest.TestCase):
    """after で予約される定期の関数（Tk のスレッドで走る）が計測される。中身は動かさず、呼ばれて数えられることだけ"""

    def setUp(self):
        _fresh_tk(self)

    def _counted(self, name):
        return TaskStats._tk.get(name, [0, 0.0])[0]

    def test_the_two_polls_and_the_log(self):
        app = MagicMock()
        app.tool_rows = []
        with patch.object(DebugLog, "write"):
            mainGUI.App._log_memory(app)        # 先に（snapshot が集計を空にするので）
        mainGUI.App._poll_host_save(app)
        mainGUI.App._poll_tool_buttons(app)
        self.assertEqual([self._counted(n) for n in ("_poll_host_save", "_poll_tool_buttons", "_log_memory")],
                         [1, 1, 1])

    def test_the_report_dialog_cooldown(self):
        dialog = MagicMock()
        dialog.app._report_cooldown_remaining.return_value = 0
        dialog._sending = True
        mainGUI.ReportDialog._refresh_cooldown(dialog)
        self.assertEqual(self._counted("_refresh_cooldown"), 1)

    def test_the_round_chart(self):
        window = MagicMock()
        window._round_chart_rows = []
        StatisticsGUI.StatisticsWindow._draw_round_chart(window)
        self.assertEqual(self._counted("_draw_round_chart"), 1)

    def test_they_keep_their_names_for_the_task_stats_and_logs(self):
        for func in (mainGUI.App._poll_host_save, mainGUI.App._poll_tool_buttons, mainGUI.App._log_memory,
                     mainGUI.ReportDialog._refresh_cooldown, StatisticsGUI.StatisticsWindow._draw_round_chart):
            self.assertTrue(hasattr(func, "__wrapped__"), func.__name__)

    def test_only_the_tk_thread_functions_are_decorated(self):
        """裏のスレッドで走る work（_off_gui に渡すもの）には付けない（別のスレッドの CPU を画面に足さない）"""
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("@TaskStats.tk_timed"), 4)
        self.assertEqual(Path("StatisticsGUI.py").read_text(encoding="utf-8").count("@TaskStats.tk_timed"), 1)
