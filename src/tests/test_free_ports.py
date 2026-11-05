"""HK: 「VRChatを起動」は窓ごとの決め打ちではなく、空いている OSC のポートの組を使う。
netstat・起動・入室操作は偽物。本物の窓・ポート・netstat・VRChat の起動は使わない"""
from tests.support import *  # noqa: F401,F403


# ── free_port_pairs ──────────────────────────
class TestFreePortPairs(unittest.TestCase):
    def test_all_free_goes_from_9000_in_order(self):
        pairs, wrapped = OSCClient.free_port_pairs(3, set())
        self.assertEqual(pairs, [(9000, 9001), (9010, 9011), (9020, 9021)])
        self.assertFalse(wrapped)

    def test_a_pair_with_only_the_in_port_used_is_skipped(self):
        pairs, _ = OSCClient.free_port_pairs(2, {9000})
        self.assertEqual(pairs, [(9010, 9011), (9020, 9021)])

    def test_a_pair_with_only_the_out_port_used_is_skipped(self):
        pairs, _ = OSCClient.free_port_pairs(2, {9001})
        self.assertEqual(pairs, [(9010, 9011), (9020, 9021)])

    def test_it_searches_upward_from_start(self):
        pairs, wrapped = OSCClient.free_port_pairs(2, set(), start=9030)
        self.assertEqual(pairs, [(9030, 9031), (9040, 9041)])
        self.assertFalse(wrapped, "下は見ない")

    def test_it_wraps_to_9000_only_when_nothing_is_left_above(self):
        used = {9010, 9011, 9020, 9021, 9970, 9971}      # 9000 は空き。9970 以上で空いているのは 9980・9990
        pairs, wrapped = OSCClient.free_port_pairs(3, used, start=9970)
        # 上の 2 組を取って、3 組目だけ 9000 から探し直す
        self.assertEqual(pairs, [(9980, 9981), (9990, 9991), (9000, 9001)])
        self.assertTrue(wrapped)

    def test_the_last_candidate_is_9990(self):
        pairs, _ = OSCClient.free_port_pairs(1000, set())
        self.assertEqual(pairs[-1], (9990, 9991))
        self.assertEqual(len(pairs), (9990 - 9000) // 10 + 1)

    def test_it_returns_only_what_it_found(self):
        used = {p for b in range(9000, 9991, 10) for p in (b, b + 1)} - {9500, 9501}
        pairs, _ = OSCClient.free_port_pairs(3, used)
        self.assertEqual(pairs, [(9500, 9501)])

    def test_the_same_pair_is_never_returned_twice(self):
        pairs, wrapped = OSCClient.free_port_pairs(60, set(), start=9500)
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertTrue(wrapped)
        flat = [p for pair in pairs for p in pair]
        self.assertEqual(len(flat), len(set(flat)))

    def test_it_does_not_change_the_used_set_it_was_given(self):
        used = {9000}
        OSCClient.free_port_pairs(2, used)
        self.assertEqual(used, {9000})


class TestUsedUdpPorts(unittest.TestCase):
    def test_it_is_the_union_over_all_processes(self):
        with patch.object(OSCClient, "udp_ports_by_pid", return_value={1: {9000, 5353}, 2: {9000, 9011}}):
            self.assertEqual(OSCClient.used_udp_ports(), {9000, 5353, 9011})

    def test_netstat_failing_is_none(self):
        with patch.object(OSCClient, "udp_ports_by_pid", return_value=None):
            self.assertIsNone(OSCClient.used_udp_ports())

    def test_no_process_with_ports_is_an_empty_set(self):
        with patch.object(OSCClient, "udp_ports_by_pid", return_value={}):
            self.assertEqual(OSCClient.used_udp_ports(), set())


class TestOscArg(unittest.TestCase):
    def test_format(self):
        self.assertEqual(OSCClient.osc_arg((9030, 9031)), "--osc=9030:127.0.0.1:9031")
        self.assertEqual(OSCClient.osc_arg((9030, 9031), "10.0.0.1"), "--osc=9030:10.0.0.1:9031")

    def test_the_fixed_ports_stay_as_the_way_out(self):
        self.assertEqual(OSCClient.osc_launch_arg(2), OSCClient.osc_arg(OSCClient.ports_for_window(2)))


class TestBuildLaunchArgs(unittest.TestCase):
    def test_the_given_ports_come_out_in_osc(self):
        args = VRChatLauncher.build_launch_args(Path("C:/launch.exe"), 0, True, None, osc_ports=(9050, 9051))
        self.assertIn("--osc=9050:127.0.0.1:9051", args)

    def test_none_leaves_osc_out(self):
        args = VRChatLauncher.build_launch_args(Path("C:/launch.exe"), 0, True, None, osc_ports=None)
        self.assertFalse(any(a.startswith("--osc=") for a in args))


# ── plan_launch_ports ────────────────────────
FIXED = "[起動] 空いているポートを調べられないので、窓ごとの決まったポートで起動します"
NO_FREE = "[起動] 空いているポートが無いので、窓ごとの決まったポートで起動します"


class TestPlanLaunchPorts(unittest.TestCase):
    def test_a_new_window_goes_above_the_existing_one(self):
        pairs, note, wrapped = mainGUI.plan_launch_ports(1, [1], [(9000, 9001), (0, 0)], {9000, 9001})
        self.assertEqual(pairs, [(9010, 9011)])
        self.assertIsNone(note)
        self.assertFalse(wrapped)

    def test_it_does_not_take_a_lower_free_pair(self):
        """既存が 9020 なら 9030（9000 が空いていても下を取らない）"""
        pairs, _, wrapped = mainGUI.plan_launch_ports(1, [1], [(9020, 9021), (0, 0)], {9020, 9021})
        self.assertEqual(pairs, [(9030, 9031)])
        self.assertFalse(wrapped)

    def test_without_any_tab_port_it_starts_at_9000(self):
        pairs, _, _ = mainGUI.plan_launch_ports(2, [0, 1], [(0, 0), (0, 0)], set())
        self.assertEqual(pairs, [(9000, 9001), (9010, 9011)])

    def test_the_out_port_of_a_tab_is_in_use_too(self):
        pairs, _, _ = mainGUI.plan_launch_ports(1, [1], [(9000, 9001), (0, 9020)], set())
        self.assertEqual(pairs, [(9010, 9011)])
        pairs, _, _ = mainGUI.plan_launch_ports(1, [1], [(9000, 9001), (0, 9010)], set())
        self.assertEqual(pairs, [(9020, 9021)], "9010 は送信に使われている組")

    def test_a_tab_port_blocks_even_when_netstat_does_not_show_it(self):
        pairs, _, _ = mainGUI.plan_launch_ports(1, [1], [(9000, 9001), (9010, 9011)], set())
        self.assertEqual(pairs, [(9020, 9021)])

    def test_ports_used_by_other_apps_are_skipped(self):
        pairs, _, _ = mainGUI.plan_launch_ports(2, [0, 1], [(0, 0), (0, 0)], {9000, 9011})
        self.assertEqual(pairs, [(9020, 9021), (9030, 9031)])

    def test_every_window_gets_a_different_pair(self):
        pairs, _, _ = mainGUI.plan_launch_ports(4, [0, 1, 2, 3], [(0, 0)] * 4, set())
        self.assertEqual(len(set(pairs)), 4)

    def test_it_wraps_when_nothing_is_free_above(self):
        pairs, note, wrapped = mainGUI.plan_launch_ports(1, [1], [(9990, 9991), (0, 0)], {9990, 9991})
        self.assertEqual(pairs, [(9000, 9001)])
        self.assertIsNone(note)
        self.assertTrue(wrapped)

    def test_netstat_failing_goes_back_to_the_fixed_ports(self):
        pairs, note, wrapped = mainGUI.plan_launch_ports(2, [2, 3], [(0, 0)] * 4, None)
        self.assertEqual(pairs, [OSCClient.ports_for_window(2), OSCClient.ports_for_window(3)])
        self.assertEqual(note, FIXED)
        self.assertFalse(wrapped)

    def test_too_few_free_pairs_fix_only_the_missing_windows(self):
        used = {p for b in range(9000, 9991, 10) for p in (b, b + 1)} - {9500, 9501}
        pairs, note, _ = mainGUI.plan_launch_ports(3, [1, 2, 3], [(0, 0)] * 4, used)
        self.assertEqual(pairs, [(9500, 9501), OSCClient.ports_for_window(2), OSCClient.ports_for_window(3)])
        self.assertEqual(note, NO_FREE)


class TestTheOrderOfWindowsStays(unittest.TestCase):
    """assign_windows は受信ポートの昇順に並べる。新しい窓が上のポートなら既存の窓のタブは動かない"""

    def _assign(self, ports):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        logs, pids = [], {}
        for i, port in enumerate(ports):
            path = Path(tmp.name) / ("output_log_2026-08-05_12-00-0%d.txt" % i)
            path.write_text("2026.08.05 12:00:01 Debug      -  Arg: --osc=%d:127.0.0.1:%d\n" % (port, port + 1),
                            encoding="utf-8")
            logs.append(path)
            pids[0x10 + i] = 100 + i
        windows = [(0x10 + i, None) for i in range(len(ports))]
        table = {100 + i: {port} for i, port in enumerate(ports)}
        with patch.object(VRChatDiscovery.win32process, "GetWindowThreadProcessId",
                          side_effect=lambda h: (0, pids.get(h, 0))):
            return VRChatDiscovery.assign_windows(windows, logs, table, 120.0)

    def test_the_new_window_comes_after_the_existing_ones(self):
        existing = [(9000, 9001), (9010, 9011)]
        new = mainGUI.plan_launch_ports(1, [2], [(9000, 9001), (9010, 9011), (0, 0)], {9000, 9001, 9010, 9011})[0][0]
        before = self._assign([p[0] for p in existing])
        after = self._assign([p[0] for p in existing] + [new[0]])
        self.assertEqual([a.hwnd for a in after][:2], [a.hwnd for a in before], "既存の窓のタブは動かない")
        self.assertEqual([a.osc_in for a in after], [9000, 9010, 9020])


# ── 起動の worker ────────────────────────────
class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class TestLaunchWorker(unittest.TestCase):
    def _app(self, windows=2, use_osc=True, tab_ports=None):
        app = _with_cancel_key(type("FakeApp", (), {})())
        app._running = False
        app.v_join_world = _Var(True)
        app.v_desktop_mode = _Var(True)
        app.v_use_osc = _Var(use_osc)
        app.v_ton_access = _Var(config.TON_INSTANCE_ACCESS_INVITE_PLUS)
        app.v_launch_options = _Var("")
        app.v_launch_count = _Var(windows)
        app.tabs = []
        tab_ports = tab_ports or [(0, 0)] * windows
        for i in range(windows):
            tab = type("FakeTab", (), {})()
            tab.idx = i
            tab.v_profile = _Var(i)
            tab.osc_in, tab.osc_out = tab_ports[i]
            app.tabs.append(tab)
        app.btn_launch = MagicMock()
        app.lbl_launch = MagicMock()
        app._launch_count_value = lambda n: windows
        app._save_launch_settings = lambda: None
        app.after = lambda _ms, fn=None: None
        app.logs = []
        app._log = app.logs.append
        return app

    def _launch(self, app, used=frozenset()):
        launched = []

        def fake_thread(target=None, daemon=None):
            thread = MagicMock()
            thread.start = target
            return thread

        with patch.object(mainGUI, "messagebox"), \
             patch.object(VRChatLauncher, "find_vrchat_exe", return_value=Path("C:/VRChat/launch.exe")), \
             patch.object(VRChatLauncher, "latest_user_id", return_value="usr_me"), \
             patch.object(VRChatLauncher, "launch_one",
                          side_effect=lambda *a, **kw: launched.append((a, kw)) or ["exe"]), \
             patch.object(VRChatLauncher, "wait_for_windows", return_value=set()), \
             patch.object(OSCClient, "used_udp_ports",
                          return_value=None if used is None else set(used)) as netstat, \
             patch.object(OSCQuery, "ensure"), \
             patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time", return_value=[]), \
             patch.object(mainGUI.threading, "Thread", side_effect=fake_thread), \
             patch.object(mainGUI.time, "sleep"):
            mainGUI.App._launch_vrchat(app)
        return launched, netstat

    def test_netstat_runs_once_and_each_window_gets_its_own_pair(self):
        launched, netstat = self._launch(self._app(windows=3), used={9000})
        self.assertEqual(netstat.call_count, 1)
        self.assertEqual([kw["osc_ports"] for _a, kw in launched],
                         [(9010, 9011), (9020, 9021), (9030, 9031)])

    def test_the_new_windows_go_above_the_existing_tabs(self):
        app = self._app(windows=3, tab_ports=[(9000, 9001), (0, 0), (0, 0)])
        app._launch_count_value = lambda n: 2
        launched, _ = self._launch(app, used={9000, 9001})
        self.assertEqual([kw["osc_ports"] for _a, kw in launched], [(9010, 9011), (9020, 9021)])

    def test_osc_off_gives_no_ports_and_no_netstat(self):
        launched, netstat = self._launch(self._app(use_osc=False))
        self.assertEqual([kw["osc_ports"] for _a, kw in launched], [None, None])
        netstat.assert_not_called()

    def test_the_instance_numbers_stay_with_the_tab_numbers(self):
        app = self._app(windows=3, tab_ports=[(9000, 9001), (0, 0), (0, 0)])
        app._launch_count_value = lambda n: 2
        launched, _ = self._launch(app, used={9000, 9001})
        self.assertEqual([a[3] for a, _kw in launched],
                         [VRChatLauncher.build_ton_link("usr_me", index=i,
                                                        access=config.TON_INSTANCE_ACCESS_INVITE_PLUS)
                          for i in (1, 2)])
        self.assertEqual([a[1] for a, _kw in launched], [1, 2], "プロファイルもタブのまま")

    def test_netstat_failing_uses_the_fixed_ports_and_says_so(self):
        app = self._app(windows=2)
        launched, _ = self._launch(app, used=None)
        self.assertEqual([kw["osc_ports"] for _a, kw in launched],
                         [OSCClient.ports_for_window(0), OSCClient.ports_for_window(1)])
        self.assertIn(FIXED, app.logs)

    def test_no_free_pair_uses_the_fixed_ports_and_says_so(self):
        full = {p for b in range(9000, 9991, 10) for p in (b, b + 1)}
        app = self._app(windows=2)
        launched, _ = self._launch(app, used=full)
        self.assertEqual([kw["osc_ports"] for _a, kw in launched],
                         [OSCClient.ports_for_window(0), OSCClient.ports_for_window(1)])
        self.assertIn(NO_FREE, app.logs)


# ── 入室操作のポート ─────────────────────────
class TestEntryOscPort(unittest.TestCase):
    def test_the_tab_port_wins(self):
        with patch.object(VRChatDiscovery, "read_log_osc_ports") as read:
            self.assertEqual(mainGUI.entry_osc_port(9030, "C:/log.txt"), 9030)
        read.assert_not_called()

    def test_zero_reads_the_in_port_from_the_log(self):
        with patch.object(VRChatDiscovery, "read_log_osc_ports", return_value=(9050, 9051)) as read:
            self.assertEqual(mainGUI.entry_osc_port(0, "C:/log.txt"), 9050)
        read.assert_called_once_with("C:/log.txt")

    def test_unreadable_is_none(self):
        with patch.object(VRChatDiscovery, "read_log_osc_ports", return_value=None):
            self.assertIsNone(mainGUI.entry_osc_port(0, "C:/log.txt"))

    def test_no_log_is_none(self):
        with patch.object(VRChatDiscovery, "read_log_osc_ports") as read:
            self.assertIsNone(mainGUI.entry_osc_port(0, ""))
        read.assert_not_called()


class _Entry:
    made = []

    def __init__(self, hwnd, osc_port, log=None, is_running=None, can_operate=None):
        self.hwnd, self.osc_port, self.calls = hwnd, osc_port, []
        _Entry.made.append(self)

    def run(self):
        self.calls.append("run")
        return True

    def close(self):
        self.calls.append("close")


class _RunNow:
    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class TestTheEntryUsesTheRealPort(unittest.TestCase):
    def _app(self, ports, logs=None):
        app = type("FakeApp", (), {})()
        app.v_join_world = MagicMock(get=lambda: True)
        app.v_ton_entry = MagicMock(get=lambda: True)
        app.v_ton_begin = MagicMock(get=lambda: False)
        app.v_ton_entry_plush = MagicMock(get=lambda: False)
        app.v_emergency_key = MagicMock(get=lambda: "p")
        app._launched_tab_indices = None
        app.tabs = []
        for i, port in enumerate(ports):
            tab = MagicMock()
            tab.idx = i
            tab.osc_in = port
            tab._get_selected_hwnd.return_value = 0x100 + i
            tab.v_log.get.return_value = (logs or {}).get(i, f"C:/logs/output_log_{i}.txt")
            app.tabs.append(tab)
        app._entry_stop = threading.Event()
        app.btn_stop_entry = MagicMock()
        app.logs = []
        app._log = app.logs.append
        app.after = lambda _ms, fn, *a: fn(*a)
        _Entry.made = []
        for target, name, value in ((ToNEntry, "ToNEntry", _Entry), (mainGUI.threading, "Thread", _RunNow),
                                    (SaveLoad, "read_clipboard", lambda: "")):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        return app

    def test_each_window_gets_its_tab_port(self):
        app = self._app([9000, 9030])
        mainGUI.App._run_ton_entry(app)
        self.assertEqual([(e.hwnd, e.osc_port) for e in _Entry.made], [(0x100, 9000), (0x101, 9030)])

    def test_a_window_without_a_port_is_skipped_and_the_others_go_on(self):
        app = self._app([0, 9030])
        with patch.object(VRChatDiscovery, "read_log_osc_ports", return_value=None):
            mainGUI.App._run_ton_entry(app)
        self.assertEqual([(e.hwnd, e.osc_port) for e in _Entry.made], [(0x101, 9030)])
        self.assertIn("[窓1] 入室操作: OSC のポートが分からないのでスキップします", app.logs)
        self.assertIn("[入室操作] 完了", app.logs)

    def test_zero_falls_back_to_the_log_port(self):
        app = self._app([0])
        with patch.object(VRChatDiscovery, "read_log_osc_ports", return_value=(9070, 9071)):
            mainGUI.App._run_ton_entry(app)
        self.assertEqual([e.osc_port for e in _Entry.made], [9070])


class TestToNEntryTakesThePort(unittest.TestCase):
    def test_the_port_is_used_as_given(self):
        entry = ToNEntry.ToNEntry(0x10, osc_port=9123)
        self.addCleanup(entry._osc.close)       # close() は入力を 0 に戻す OSC を送るので、ソケットだけ閉じる
        self.assertEqual(entry._osc_port, 9123)

    def test_the_fixed_formula_is_gone(self):
        src = Path(ToNEntry.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ports_for_window", src)
        self.assertNotIn("window_index", src)
