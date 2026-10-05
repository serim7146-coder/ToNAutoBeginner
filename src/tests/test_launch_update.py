"""VRChat の起動・窓とログの対応・自動アップデート・ビルド"""
from tests.support import *  # noqa: F401,F403




class TestConfigResources(unittest.TestCase):
    def test_static_resources_resolve_from_repo_root(self):
        self.assertTrue(config.TERRORS["classic"])
        for path in (
            config.VOICE_CONTINUE,
            config.VOICE_FOG,
            config.VOICE_ITEM_LOST,
            config.VOICE_INTERMISSION,
            config.VOICE_FOXY,
        ):
            self.assertTrue(Path(path).exists(), path)




class TestPerWindowInstance(unittest.TestCase):
    """窓ごとに別のprivateインスタンスへ入る（同じインスタンスには入れないため）"""

    def test_instance_number_differs_per_window(self):
        links = [VRChatLauncher.build_ton_link("usr_x", i) for i in range(4)]
        nums = [l.split(":")[-1].split("~")[0] for l in links]
        self.assertEqual(len(set(nums)), 4, "窓ごとに別インスタンスでなければならない")

    def test_only_the_instance_number_differs(self):
        first, second = (VRChatLauncher.build_ton_link("usr_x", i) for i in (0, 1))
        self.assertNotEqual(first, second)
        self.assertEqual(first.split(":")[:-1], second.split(":")[:-1])
        for link in (first, second):
            self.assertTrue(link.endswith("~private(usr_x)~region(jp)"), link)

    def test_build_ton_link_uses_ton_world(self):
        link = VRChatLauncher.build_ton_link("usr_abc", 0)
        self.assertIn(config.TON_WORLD_ID, link)
        self.assertIn("~private(usr_abc)", link)
        self.assertTrue(link.startswith("vrchat://launch?"))

    def test_build_ton_link_requires_user_id(self):
        self.assertIsNone(VRChatLauncher.build_ton_link("", 0))

    def test_build_ton_link_takes_the_instance_access(self):
        """インバイト+ は ~canRequestInvite 付き。省いたときはインバイト"""
        plain = VRChatLauncher.build_ton_link("usr_abc", 0)
        invite = VRChatLauncher.build_ton_link(
            "usr_abc", 0, access=config.TON_INSTANCE_ACCESS_INVITE)
        plus = VRChatLauncher.build_ton_link(
            "usr_abc", 0, access=config.TON_INSTANCE_ACCESS_INVITE_PLUS)

        for link in (plain, invite):
            self.assertNotIn("canRequestInvite", link)
            self.assertRegex(link, r"~private\(usr_abc\)~region\(jp\)$")
        self.assertRegex(plus, r"~private\(usr_abc\)~canRequestInvite~region\(jp\)$")

    def test_both_invite_kinds_can_be_read_early_with_the_button(self):
        """インバイトもインバイト+ も、霧看破のボタンが ON なら看破する"""
        FogEarlyRead.set_early_read_enabled(True)
        self.addCleanup(FogEarlyRead.set_early_read_enabled, False)
        self.assertTrue(FogEarlyRead.early_read_allowed(config.TON_INSTANCE_ACCESS_INVITE))
        self.assertTrue(FogEarlyRead.early_read_allowed(config.TON_INSTANCE_ACCESS_INVITE_PLUS))
        self.assertEqual(config.TON_INSTANCE_ACCESS_DEFAULT,
                         config.TON_INSTANCE_ACCESS_INVITE_PLUS, "既定はインバイト+")

    def test_latest_user_id_reads_auth_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "output_log_2026-08-20_10-00-00.txt"
            line = ("2026.08.20 10:00:00 Log - User Authenticated: serim01 "
                    "(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)")
            p.write_text(line, encoding="utf-8")
            self.assertEqual(
                VRChatLauncher.latest_user_id(d),
                "usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3")

    def test_latest_user_id_reads_the_newest_log_first(self):
        """別アカウントで入り直したら、新しいログのユーザーIDを使う"""
        with tempfile.TemporaryDirectory() as d:
            for stamp, user in (("2026-08-20_09-00-00", "usr_00000000-0000-0000-0000-00000000000a"),
                                ("2026-08-20_11-00-00", "usr_00000000-0000-0000-0000-00000000000c"),
                                ("2026-08-20_10-00-00", "usr_00000000-0000-0000-0000-00000000000b")):
                (Path(d) / f"output_log_{stamp}.txt").write_text(
                    f"2026.08.20 10:00:00 Log - User Authenticated: a ({user})",
                    encoding="utf-8")
            self.assertEqual(VRChatLauncher.latest_user_id(d),
                             "usr_00000000-0000-0000-0000-00000000000c")




class TestVRChatLauncher(unittest.TestCase):
    """VRChat起動機構"""

    def test_build_launch_args_desktop(self):
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 2, desktop_mode=True)
        self.assertEqual(args, ["C:\\VRChat.exe", "--profile=2", "--no-vr",
                                *config.LAUNCH_OPTION])

    def test_build_launch_args_vr_omits_no_vr(self):
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 0, desktop_mode=False)
        self.assertEqual(args, ["C:\\VRChat.exe", "--profile=0", *config.LAUNCH_OPTION])

    def test_build_launch_args_with_instance_link(self):
        link = "vrchat://launch?ref=vrchat.com&id=wrld_abc:1234"
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 1, True, link)
        self.assertIn(link, args)
        self.assertEqual(args[len(args) - len(config.LAUNCH_OPTION):],
                         list(config.LAUNCH_OPTION), "起動オプションは最後に付く")

    def test_the_launch_options_enable_the_early_read(self):
        """看破は --enable-sdk-log-levels 付きのログにしか出ない行を読む"""
        self.assertIn(config.FOG_EARLY_READ_LAUNCH_FLAG, config.LAUNCH_OPTION)
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 0, osc_index=1)
        self.assertIn(config.FOG_EARLY_READ_LAUNCH_FLAG, args)

    def test_find_vrchat_exe_prefers_launch_exe(self):
        """VRChat.exe直接起動はオフラインテストモードになるためlaunch.exeを選ぶ"""
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / "SteamLibrary"
            install = lib / "steamapps" / "common" / "VRChat"
            install.mkdir(parents=True)
            (install / "VRChat.exe").write_text("x", encoding="utf-8")
            (install / "launch.exe").write_text("x", encoding="utf-8")
            with patch.object(VRChatLauncher, "steam_library_paths", return_value=[lib]):
                self.assertEqual(VRChatLauncher.find_vrchat_exe(), install / "launch.exe")

    def test_find_vrchat_exe_falls_back_when_no_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / "SteamLibrary"
            install = lib / "steamapps" / "common" / "VRChat"
            install.mkdir(parents=True)
            (install / "VRChat.exe").write_text("x", encoding="utf-8")
            with patch.object(VRChatLauncher, "steam_library_paths", return_value=[lib]):
                self.assertEqual(VRChatLauncher.find_vrchat_exe(), install / "VRChat.exe")

    def test_wait_for_windows_returns_new_hwnds_only(self):
        states = [[1, 2], [1, 2], [1, 2, 5], [1, 2, 5, 6]]
        calls = {"n": 0}

        def discover():
            i = min(calls["n"], len(states) - 1)
            calls["n"] += 1
            return states[i]

        with patch.object(VRChatLauncher.time, "sleep"):
            found = VRChatLauncher.wait_for_windows(
                {1, 2}, expected_total=2, timeout_sec=30.0, discover=discover)
        self.assertEqual(found, [5, 6])

    def test_wait_for_windows_stops_when_cancelled(self):
        with patch.object(VRChatLauncher.time, "sleep"):
            found = VRChatLauncher.wait_for_windows(
                set(), expected_total=4, timeout_sec=30.0,
                is_cancelled=lambda: True, discover=lambda: [9])
        self.assertEqual(found, [])




class TestVRChatDiscovery(unittest.TestCase):
    def test_find_latest_logs_returns_latest_in_newest_to_oldest_order(self):
        """ログファイルは新しいものから見る。本数の上限も効く"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in [
                "output_log_2026-05-21_10-00-00.txt",
                "output_log_2026-05-22_10-00-00.txt",
                "output_log_2026-05-23_10-00-00.txt",
            ]:
                (base / name).write_text("", encoding="utf-8")

            logs = VRChatDiscovery.find_latest_logs(base, 2)

        self.assertEqual(
            [path.name for path in logs],
            [
                "output_log_2026-05-23_10-00-00.txt",
                "output_log_2026-05-22_10-00-00.txt",
            ],
        )

    def test_get_vrchat_windows_filters_by_title_and_class(self):
        def enum_windows(callback, arg):
            for hwnd in (1, 2, 3):
                callback(hwnd, arg)

        with patch.object(VRChatDiscovery.win32gui, "EnumWindows", side_effect=enum_windows), \
             patch.object(VRChatDiscovery.win32gui, "IsWindowVisible", side_effect=lambda hwnd: hwnd != 1), \
             patch.object(VRChatDiscovery.win32gui, "GetWindowText", side_effect=lambda hwnd: "VRChat" if hwnd != 2 else "Other"), \
             patch.object(VRChatDiscovery.win32gui, "GetClassName", side_effect=lambda hwnd: config.VRCHAT_WINDOW_CLASS):
            hwnds = VRChatDiscovery.get_vrchat_windows(4)

        self.assertEqual(hwnds, [3])




class TestWindowLogMatching(unittest.TestCase):
    """起動時刻によるウィンドウ↔ログ対応付け"""

    @staticmethod
    def _log(stamp: str) -> Path:
        return Path(f"C:/logs/output_log_{stamp}.txt")

    @staticmethod
    def _epoch(stamp: str) -> float:
        return datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").timestamp()

    def test_parse_log_start_time(self):
        self.assertEqual(
            VRChatDiscovery.parse_log_start_time(self._log("2026-08-05_12-30-41")),
            self._epoch("2026-08-05_12-30-41"),
        )
        self.assertIsNone(VRChatDiscovery.parse_log_start_time(Path("C:/logs/other.txt")))
        self.assertIsNone(VRChatDiscovery.parse_log_start_time(Path("C:/logs/output_log_bad.txt")))

    def test_matches_by_launch_time_regardless_of_order(self):
        """Zオーダーが入れ替わっていても起動時刻で正しく対応する"""
        logs = [self._log("2026-08-05_09-00-00"), self._log("2026-08-05_12-00-00")]
        # 窓リストの並びと関係なく、時刻が近い方へ割り当てられること
        windows = [
            (0xAAA, self._epoch("2026-08-05_12-00-03")),  # 12時の窓が先頭
            (0xBBB, self._epoch("2026-08-05_09-00-02")),
        ]
        matched = VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0)
        self.assertEqual(matched[0].name, "output_log_2026-08-05_12-00-00.txt")
        self.assertEqual(matched[1].name, "output_log_2026-08-05_09-00-00.txt")

    def test_nearest_log_wins_when_launches_are_close(self):
        """近接した起動でも、差が小さい組から確定するので取り違えない"""
        logs = [self._log("2026-08-05_12-00-00"), self._log("2026-08-05_12-00-30")]
        windows = [
            (0xAAA, self._epoch("2026-08-05_12-00-28")),
            (0xBBB, self._epoch("2026-08-05_12-00-01")),
        ]
        matched = VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0)
        self.assertEqual(matched[0].name, "output_log_2026-08-05_12-00-30.txt")
        self.assertEqual(matched[1].name, "output_log_2026-08-05_12-00-00.txt")

    def test_one_log_is_never_assigned_twice(self):
        logs = [self._log("2026-08-05_12-00-00")]
        windows = [
            (0xAAA, self._epoch("2026-08-05_12-00-01")),
            (0xBBB, self._epoch("2026-08-05_12-00-02")),
        ]
        matched = VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0)
        self.assertEqual(matched[0].name, "output_log_2026-08-05_12-00-00.txt")
        self.assertIsNone(matched[1])

    def test_falls_back_to_order_when_start_time_unknown(self):
        """起動時刻を取得できない窓には未使用ログを順に割り当てる"""
        logs = [self._log("2026-08-05_09-00-00"), self._log("2026-08-05_12-00-00")]
        windows = [
            (0xAAA, self._epoch("2026-08-05_12-00-02")),
            (0xBBB, None),  # 取得失敗（管理者権限のVRChatなど）
        ]
        matched = VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0)
        self.assertEqual(matched[0].name, "output_log_2026-08-05_12-00-00.txt")
        self.assertEqual(matched[1].name, "output_log_2026-08-05_09-00-00.txt")

    def test_the_fallback_takes_the_newest_logs(self):
        """候補は新しい順で来る。起動時刻で決まらない窓には新しい方から必要数を取る"""
        logs = [self._log("2026-08-05_12-00-00"), self._log("2026-08-05_11-00-00"),
                self._log("2026-08-05_10-00-00")]              # 新しい順
        windows = [(0xAAA, None), (0xBBB, None)]
        matched = VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0)
        self.assertEqual({p.name for p in matched},
                         {"output_log_2026-08-05_12-00-00.txt",
                          "output_log_2026-08-05_11-00-00.txt"},
                         "いちばん古いログは使わない")

    def test_the_fallback_pairs_as_before(self):
        """組み合わせはこれまでと同じ: 起動の古い窓 ↔ 選んだ中で古いログ"""
        logs = [self._log("2026-08-05_12-00-00"), self._log("2026-08-05_11-00-00"),
                self._log("2026-08-05_10-00-00")]              # 新しい順
        windows = [(0xAAA, None), (0xBBB, None)]               # 起動の古い順
        matched = VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0)
        self.assertEqual([p.name for p in matched],
                         ["output_log_2026-08-05_11-00-00.txt",
                          "output_log_2026-08-05_12-00-00.txt"])

    def test_stale_log_outside_tolerance_is_not_matched_directly(self):
        """古すぎるログは時刻一致では選ばれない（フォールバックでのみ使われる）"""
        logs = [self._log("2026-08-01_09-00-00")]
        windows = [(0xAAA, self._epoch("2026-08-05_12-00-00"))]
        matched = VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0)
        self.assertEqual(matched[0].name, "output_log_2026-08-01_09-00-00.txt")  # 他に候補が無ければ使う

        # 時刻の合うログがあればそちらが優先される
        logs2 = [self._log("2026-08-01_09-00-00"), self._log("2026-08-05_12-00-05")]
        matched2 = VRChatDiscovery.match_windows_to_logs(windows, logs2, 120.0)
        self.assertEqual(matched2[0].name, "output_log_2026-08-05_12-00-05.txt")

    def test_count_time_matched_logs_only_counts_strict_matches(self):
        """ログ生成待ちの判定: 時刻が合うログだけを数える（古いログで誤検知しない）"""
        logs = [self._log("2026-08-05_09-00-00")]
        windows = [(0xAAA, self._epoch("2026-08-05_12-00-01"))]
        # 起動直後: 対応するログがまだ無い → 0
        self.assertEqual(
            VRChatDiscovery.count_time_matched_logs(windows, logs, 120.0), 0)
        # ログが出来た → 1
        logs.append(self._log("2026-08-05_12-00-03"))
        self.assertEqual(
            VRChatDiscovery.count_time_matched_logs(windows, logs, 120.0), 1)

    def test_count_time_matched_logs_is_one_to_one(self):
        logs = [self._log("2026-08-05_12-00-00")]
        windows = [
            (0xAAA, self._epoch("2026-08-05_12-00-01")),
            (0xBBB, self._epoch("2026-08-05_12-00-02")),
        ]
        self.assertEqual(
            VRChatDiscovery.count_time_matched_logs(windows, logs, 120.0), 1)

    def test_windows_sorted_by_start_time_with_unknown_last(self):
        starts = {0x1: 300.0, 0x2: 100.0, 0x3: None}

        def fake_enum(cb, _):
            for h in (0x1, 0x2, 0x3):  # Zオーダー順（起動順とは無関係）
                cb(h, None)

        with patch.object(VRChatDiscovery.win32gui, "EnumWindows", side_effect=fake_enum), \
             patch.object(VRChatDiscovery.win32gui, "IsWindowVisible", return_value=True), \
             patch.object(VRChatDiscovery.win32gui, "GetWindowText", return_value="VRChat"), \
             patch.object(VRChatDiscovery.win32gui, "GetClassName", return_value=config.VRCHAT_WINDOW_CLASS), \
             patch.object(VRChatDiscovery, "get_process_start_time", side_effect=lambda h: starts[h]):
            result = VRChatDiscovery.get_vrchat_windows_by_start_time(8)

        self.assertEqual([h for h, _t in result], [0x2, 0x1, 0x3])

    def test_get_process_start_time_returns_none_on_failure(self):
        with patch.object(VRChatDiscovery.win32process, "GetWindowThreadProcessId",
                          side_effect=OSError("denied")):
            self.assertIsNone(VRChatDiscovery.get_process_start_time(0x1234))




class TestOSCLogBinding(unittest.TestCase):
    """窓↔ログをOSCポートで確定する。

    起動時刻は「近い順」でしかなく、同時刻に立てた窓では取り違えうる。
    誤ったログを掴んだ窓は他人のラウンドを見て自爆するので、VRChatが実際に
    掴んでいるUDPポートと、ログ先頭の --osc= を突き合わせて一意に決める。
    """

    HEAD = ("2026.08.05 12:00:01 Debug      -  Launching with args: 5\n"
            "2026.08.05 12:00:01 Debug      -  Arg: C:\\VRChat\\VRChat.exe\n"
            "%s"
            "2026.08.05 12:00:01 Debug      -  Arg: --enable-debug-gui\n")

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.pids: dict[int, int] = {}

    # ── 道具 ────────────────────────────────
    def _log(self, stamp: str, osc_in: int = None, filler: int = 0) -> Path:
        """ログを1本作る。osc_in=None は手動起動（--osc が入らない）"""
        arg = ("2026.08.05 12:00:01 Debug      -  "
               f"Arg: --osc={osc_in}:127.0.0.1:{osc_in + 1}\n") if osc_in else ""
        path = Path(self._dir.name) / f"output_log_{stamp}.txt"
        path.write_text(self.HEAD % arg + "x" * filler, encoding="utf-8")
        return path

    def _window(self, hwnd: int, stamp: str = None, pid: int = None):
        self.pids[hwnd] = pid if pid is not None else hwnd
        return hwnd, (self._epoch(stamp) if stamp else None)

    @staticmethod
    def _epoch(stamp: str) -> float:
        return datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").timestamp()

    def _assign(self, windows, logs, ports_by_pid, tolerance=120.0):
        with patch.object(VRChatDiscovery.win32process, "GetWindowThreadProcessId",
                          side_effect=lambda h: (0, self.pids.get(h, 0))):
            return VRChatDiscovery.assign_windows(
                windows, logs, ports_by_pid, tolerance)

    @staticmethod
    def _names(assigned):
        return [(a.log.name if a.log else None, a.osc_in) for a in assigned]

    # ── ① 並び順はポートで決まる ─────────────────
    def test_the_windows_are_ordered_by_their_osc_in_port(self):
        """Zオーダーも起動順もバラバラでも、9000/9010/9020/9030 の順に入る"""
        logs = [self._log("2026-08-05_12-00-0%d" % i, 9000 + i * 10)
                for i in range(4)]
        windows = [self._window(0xD, pid=40), self._window(0xB, pid=20),
                   self._window(0xA, pid=10), self._window(0xC, pid=30)]
        ports = {10: {9000}, 20: {9010}, 30: {9020}, 40: {9030}}

        assigned = self._assign(windows, logs, ports)

        self.assertEqual([a.hwnd for a in assigned], [0xA, 0xB, 0xC, 0xD])
        self.assertEqual([a.osc_in for a in assigned], [9000, 9010, 9020, 9030])
        self.assertEqual([a.log.name for a in assigned],
                         [p.name for p in logs])

    # ── ② 刻み10を前提にしない ──────────────────
    def test_a_window_on_9003_sits_between_9000_and_9010(self):
        """ToNUtils が立てた窓は 9003 だった（9000 + idx*10 に乗らない）"""
        logs = [self._log("2026-08-05_12-00-00", 9000),
                self._log("2026-08-05_12-00-10", 9010),
                self._log("2026-08-05_12-00-20", 9003)]
        windows = [self._window(0xA, pid=10), self._window(0xB, pid=20),
                   self._window(0xE, pid=30)]

        assigned = self._assign(windows, logs,
                                {10: {9000}, 20: {9010}, 30: {9003}})

        self.assertEqual([a.osc_in for a in assigned], [9000, 9003, 9010])
        self.assertEqual([a.hwnd for a in assigned], [0xA, 0xE, 0xB])

    # ── ③ 手動起動（--osc 無し） ─────────────────
    def test_a_log_without_the_osc_arg_counts_as_the_default_port(self):
        """手動起動のログに --osc は入らないが、VRChatは既定で9000を掴む"""
        manual = self._log("2026-08-05_12-00-00")
        ours = self._log("2026-08-05_12-00-10", 9010)
        windows = [self._window(0xB, pid=20), self._window(0xA, pid=10)]

        assigned = self._assign(windows, [manual, ours],
                                {10: {9000}, 20: {9010}})

        self.assertEqual(self._names(assigned),
                         [(manual.name, 9000), (ours.name, 9010)])

    # ── ④ netstat が失敗したとき ────────────────
    def test_a_netstat_failure_falls_back_to_the_launch_times(self):
        logs = [self._log("2026-08-05_09-00-00", 9010),
                self._log("2026-08-05_12-00-00", 9000)]
        windows = [self._window(0xA, "2026-08-05_12-00-02", pid=10),
                   self._window(0xB, "2026-08-05_09-00-01", pid=20)]

        assigned = self._assign(windows, logs, None)

        self.assertEqual([a.hwnd for a in assigned], [0xA, 0xB], "並びは変えない")
        self.assertEqual(self._names(assigned),
                         [(logs[1].name, 0), (logs[0].name, 0)])
        # 従来の割り当てと同じ結果になること
        self.assertEqual(
            [a.log for a in assigned],
            VRChatDiscovery.match_windows_to_logs(windows, logs, 120.0))

    # ── ⑤⑥ 9000 を名乗るログが並ぶとき ────────────
    def test_the_nearest_launch_time_wins_among_logs_on_the_same_port(self):
        """手動起動を繰り返すと9000のログが並ぶ。ここを外すといちばん危ない"""
        old = self._log("2026-08-05_09-00-00")
        new = self._log("2026-08-05_12-00-00")
        windows = [self._window(0xA, "2026-08-05_09-00-03", pid=10)]

        assigned = self._assign(windows, [old, new], {10: {9000}})

        self.assertEqual(self._names(assigned), [(old.name, 9000)])

    def test_the_newer_log_wins_a_tie_on_the_same_port(self):
        """起動時刻の差が同じ2本が同じ受信ポートを名乗るなら、新しいログを採る"""
        old = self._log("2026-08-05_11-59-50")
        new = self._log("2026-08-05_12-00-10")
        windows = [self._window(0xA, "2026-08-05_12-00-00", pid=10)]
        logs = VRChatDiscovery.find_latest_logs(Path(self._dir.name), 20)

        assigned = self._assign(windows, logs, {10: {9000}})

        self.assertEqual(self._names(assigned), [(new.name, 9000)], old.name)

    def test_the_tab_order_does_not_follow_the_log_order(self):
        """ログを新しい順に見ても、窓1・窓2…の並びは受信ポート昇順 → 起動の古い順のまま"""
        self._log("2026-08-05_12-00-00", 9010)
        self._log("2026-08-05_12-00-10", 9000)
        self._log("2026-08-05_09-00-00")
        self._log("2026-08-05_10-00-00")
        windows = [self._window(0xC, "2026-08-05_09-00-01", pid=30),
                   self._window(0xD, pid=40),
                   self._window(0xA, "2026-08-05_12-00-01", pid=10),
                   self._window(0xB, "2026-08-05_12-00-11", pid=20)]
        logs = VRChatDiscovery.find_latest_logs(Path(self._dir.name), 20)

        assigned = self._assign(windows, logs, {10: {9010}, 20: {9000}})

        self.assertEqual([a.hwnd for a in assigned], [0xB, 0xA, 0xC, 0xD])
        self.assertEqual(self._names(assigned),
                         [("output_log_2026-08-05_12-00-10.txt", 9000),
                          ("output_log_2026-08-05_12-00-00.txt", 9010),
                          ("output_log_2026-08-05_09-00-00.txt", 0),
                          ("output_log_2026-08-05_10-00-00.txt", 0)])

    def test_the_newest_log_wins_when_the_launch_time_is_unknown(self):
        """管理者権限のVRChatなどで起動時刻が取れない窓"""
        old = self._log("2026-08-05_09-00-00")
        new = self._log("2026-08-05_12-00-00")
        windows = [self._window(0xA, pid=10)]

        assigned = self._assign(windows, [old, new], {10: {9000}})

        self.assertEqual(self._names(assigned), [(new.name, 9000)])

    # ── ⑦ 掴んでいるポートには雑多なものが混ざる ──────
    def test_only_the_port_written_in_the_log_is_matched(self):
        """pidは 5353(mDNS) や一時ポートも掴んでいる。若い順に取ってはいけない"""
        ours = self._log("2026-08-05_12-00-10", 9010)
        windows = [self._window(0xA, pid=10)]
        held = {10: {5353, 9010, 51852, 61324}}

        assigned = self._assign(windows, [ours], held)

        self.assertEqual(self._names(assigned), [(ours.name, 9010)])

    def test_a_process_without_the_logged_port_is_not_decided_by_osc(self):
        manual = self._log("2026-08-05_12-00-00")
        windows = [self._window(0xA, "2026-08-05_12-00-01", pid=10)]

        assigned = self._assign(windows, [manual], {10: {5353, 51852}})

        self.assertEqual(assigned[0].osc_in, 0, "OSCでは決まらない")
        self.assertEqual(assigned[0].log.name, manual.name, "起動時刻で決まる")

    # ── ⑧ 混在しても1つのログを2つの窓へ渡さない ──────
    def test_a_log_is_never_shared_between_an_osc_window_and_another(self):
        ours = self._log("2026-08-05_12-00-00", 9010)
        manual = self._log("2026-08-05_12-00-01")
        windows = [self._window(0xA, "2026-08-05_12-00-02", pid=10),
                   self._window(0xB, "2026-08-05_12-00-01", pid=20)]

        assigned = self._assign(windows, [ours, manual], {10: {9010}})

        self.assertEqual(self._names(assigned),
                         [(ours.name, 9010), (manual.name, 0)])
        self.assertEqual([a.hwnd for a in assigned], [0xA, 0xB])

    def test_two_windows_of_one_process_never_share_a_log(self):
        """掴んでいるポートはpid単位。同じpidの窓が2つ見つかると同じログに見える"""
        near = self._log("2026-08-05_12-00-00")
        far = self._log("2026-08-05_09-00-00")
        windows = [self._window(0xA, "2026-08-05_12-00-01", pid=10),
                   self._window(0xB, "2026-08-05_12-00-02", pid=10)]

        assigned = self._assign(windows, [near, far], {10: {9000}})

        self.assertEqual({a.log.name for a in assigned}, {near.name, far.name})

    def test_one_window_never_gets_two_logs(self):
        first = self._log("2026-08-05_12-00-00", 9000)
        second = self._log("2026-08-05_12-00-30", 9000)
        windows = [self._window(0xA, "2026-08-05_12-00-01", pid=10)]

        assigned = self._assign(windows, [first, second], {10: {9000}})

        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0].log.name, first.name)

    # ── ⑨ ログの読み取り ───────────────────────
    def test_read_log_osc_ports(self):
        self.assertEqual(
            VRChatDiscovery.read_log_osc_ports(self._log("2026-08-05_12-00-00", 9003)),
            (9003, 9004))
        self.assertEqual(
            VRChatDiscovery.read_log_osc_ports(self._log("2026-08-05_12-00-01")),
            VRChatDiscovery.VRCHAT_DEFAULT_OSC_PORTS, "--osc無し＝手動起動")
        self.assertIsNone(
            VRChatDiscovery.read_log_osc_ports(Path(self._dir.name) / "nope.txt"),
            "読めないログは既定ポート扱いにしない")

    def test_only_the_head_of_the_log_is_read(self):
        """ログは数MBある。全部読むと窓数ぶん遅くなる"""
        path = Path(self._dir.name) / "output_log_2026-08-05_12-00-00.txt"
        path.write_text("x" * (VRChatDiscovery.LOG_HEAD_BYTES + 100)
                        + " Arg: --osc=9020:127.0.0.1:9021\n", encoding="utf-8")

        self.assertEqual(VRChatDiscovery.read_log_osc_ports(path),
                         VRChatDiscovery.VRCHAT_DEFAULT_OSC_PORTS)

    def test_an_unreadable_log_is_not_bound_to_a_window(self):
        """読めないログを既定ポートの窓へ結びつけない"""
        windows = [self._window(0xA, "2026-08-05_12-00-01", pid=10)]
        missing = Path(self._dir.name) / "output_log_2026-08-05_12-00-00.txt"

        with patch.object(VRChatDiscovery, "read_log_osc_ports", return_value=None):
            assigned = self._assign(windows, [missing], {10: {9000}})

        self.assertEqual(assigned[0].osc_in, 0)




class TestAutoUpdate(unittest.TestCase):
    """GitHub Releases自動アップデート"""

    def test_parse_version(self):
        self.assertEqual(AutoUpdate.parse_version("0.3.0"), (0, 3, 0))
        self.assertEqual(AutoUpdate.parse_version("v1.2.10"), (1, 2, 10))
        self.assertEqual(AutoUpdate.parse_version("1.2-beta"), (1,))
        self.assertEqual(AutoUpdate.parse_version(""), ())
        self.assertEqual(AutoUpdate.parse_version("garbage"), ())

    def test_is_newer(self):
        self.assertTrue(AutoUpdate.is_newer("0.3.1", "0.3.0"))
        self.assertTrue(AutoUpdate.is_newer("v0.10.0", "0.9.9"))
        self.assertFalse(AutoUpdate.is_newer("0.3.0", "0.3.0"))
        self.assertFalse(AutoUpdate.is_newer("0.2.9", "0.3.0"))
        self.assertFalse(AutoUpdate.is_newer("garbage", "0.3.0"))

    def test_find_exe_asset(self):
        release = {"assets": [
            {"name": "ToNAutoBeginner.7z", "browser_download_url": "https://x/7z", "size": 1},
            {"name": config.UPDATE_ASSET_NAME, "browser_download_url": "https://x/exe", "size": 123},
        ]}
        self.assertEqual(AutoUpdate.find_exe_asset(release), ("https://x/exe", 123))
        self.assertIsNone(AutoUpdate.find_exe_asset({"assets": []}))
        self.assertIsNone(AutoUpdate.find_exe_asset({}))

    def test_fetch_latest_release_returns_none_on_network_error(self):
        with patch.object(AutoUpdate.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertIsNone(AutoUpdate.fetch_latest_release())

    def test_apply_update_swaps_files_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "app.exe"
            exe.write_text("OLD", encoding="utf-8")
            new = Path(d) / "new.exe.download"
            new.write_text("NEW", encoding="utf-8")

            self.assertTrue(AutoUpdate.apply_update(new, exe))
            self.assertEqual(exe.read_text(encoding="utf-8"), "NEW")
            old = Path(d) / "app.exe.old"
            self.assertEqual(old.read_text(encoding="utf-8"), "OLD")
            self.assertFalse(new.exists())

    def test_apply_update_restores_on_move_failure(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "app.exe"
            exe.write_text("OLD", encoding="utf-8")
            missing_new = Path(d) / "no_such_file"

            self.assertFalse(AutoUpdate.apply_update(missing_new, exe))
            self.assertEqual(exe.read_text(encoding="utf-8"), "OLD")  # 退避から復元される

    def test_cleanup_old_exe_removes_backup(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "app.exe"
            exe.write_text("X", encoding="utf-8")
            old = Path(d) / "app.exe.old"
            old.write_text("Y", encoding="utf-8")
            with patch.object(AutoUpdate, "current_exe_path", return_value=exe):
                AutoUpdate.cleanup_old_exe()
            self.assertFalse(old.exists())
            self.assertTrue(exe.exists())




class TestLaunchOptions(unittest.TestCase):
    """インスタンスタイプ4つのリンクと、起動オプションの欄"""

    def test_the_four_links(self):
        for access, kind in ((config.TON_INSTANCE_ACCESS_INVITE, "~private(u)~region"),
                             (config.TON_INSTANCE_ACCESS_INVITE_PLUS, "~private(u)~canRequestInvite~region"),
                             (config.TON_INSTANCE_ACCESS_FRIENDS, "~friends(u)~region"),
                             (config.TON_INSTANCE_ACCESS_FRIENDS_PLUS, "~hidden(u)~region"),
                             ("unknown", "~private(u)~region"), (None, "~private(u)~region")):
            link = VRChatLauncher.build_ton_link("u", 0, region="jp", access=access)
            self.assertIn(kind, link, access)
            self.assertTrue(link.endswith("~region(jp)"), link)
            self.assertIn(config.TON_WORLD_ID + ":", link)

    def test_the_log_side_reads_them_as_private(self):
        for access in config.TON_INSTANCE_ACCESS_CHOICES:
            link = VRChatLauncher.build_ton_link("usr_x", 0, access=access)
            suffix = link.split(":", 2)[-1]
            self.assertEqual(LogMonitor.LogMonitor._parse_instance_type(suffix),
                             config.INSTANCE_PRIVATE, access)

    BASE = [str(Path("C:/VRChat.exe")), "--profile=0", "--no-vr"]

    def test_no_extra_options_is_the_same_as_before(self):
        for extra in ("", "   ", None):
            self.assertEqual(VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 0,
                                                              extra_options=extra),
                             self.BASE + list(config.LAUNCH_OPTION), repr(extra))

    def test_extra_options_go_after_the_launch_option(self):
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 0,
                                                extra_options=" --foo   --bar ")
        self.assertEqual(args, self.BASE + list(config.LAUNCH_OPTION) + ["--foo", "--bar"])

    def test_the_same_option_is_not_added_twice(self):
        args = VRChatLauncher.build_launch_args(
            Path("C:/VRChat.exe"), 0,
            extra_options=f"{config.LAUNCH_OPTION[0]} --no-vr --foo --foo")
        self.assertEqual(args, self.BASE + list(config.LAUNCH_OPTION) + ["--foo"])

    def test_values_are_always_added(self):
        """重複を見るのは - で始まる語だけ。値の語（1080）は同じでも付ける"""
        args = VRChatLauncher.build_launch_args(
            Path("C:/VRChat.exe"), 0,
            extra_options="-screen-width 1080 -screen-height 1080")
        self.assertEqual(args, self.BASE + list(config.LAUNCH_OPTION)
                         + ["-screen-width", "1080", "-screen-height", "1080"])

    def test_an_option_already_in_launch_option_is_not_added(self):
        args = VRChatLauncher.build_launch_args(
            Path("C:/VRChat.exe"), 0, extra_options="--enable-sdk-log-levels --foo --foo 0 0")
        self.assertEqual(args.count("--enable-sdk-log-levels"), 1)
        self.assertEqual(args, self.BASE + list(config.LAUNCH_OPTION) + ["--foo", "0", "0"])

    def test_launch_one_passes_them(self):
        with patch.object(VRChatLauncher.subprocess, "Popen") as popen:
            VRChatLauncher.launch_one(Path("C:/VRChat.exe"), 0, extra_options="--foo")
        self.assertEqual(popen.call_args.args[0][-1], "--foo")




class TestBuildScript(unittest.TestCase):
    """build.py が組み立てるコマンド（Nuitka は呼ばない）"""

    def setUp(self):
        self.build = _load_build_script()

    def _main_py_command(self) -> list[str]:
        src = SRC_DIR.joinpath("main.py").read_text(encoding="utf-8")
        line = next(l for l in src.splitlines() if l.startswith("python -m nuitka src/main.py"))
        return line.split()[4:]

    def test_every_current_argument_is_kept_in_order(self):
        old = self._main_py_command()
        self.assertEqual(self.build.BASE_ARGS, old, "1つも落とさない・変えない・並びも同じ")
        cmd = self.build.build_command("v1.2.3")
        self.assertEqual(cmd[1:4], ["-m", "nuitka", "src/main.py"])
        self.assertEqual(cmd[4:4 + len(old)], old)

    def test_the_extract_dir_and_version_come_from_app_version(self):
        cmd = self.build.build_command("v1.2.3", stamp="20261002123456000001")
        self.assertEqual(cmd[-3:], ["--onefile-tempdir-spec={CACHE_DIR}/ToNAutoBeginner/v1.2.3-20261002123456000001",
                                    "--product-version=1.2.3", "--file-version=1.2.3"])
        self.assertEqual(len(cmd), 4 + len(self.build.BASE_ARGS) + 3, "足すのは3つだけ")

    def test_two_builds_of_the_same_version_get_different_folders(self):
        first = self.build.build_command("v1.0.0")[-3]
        time.sleep(0.002)
        second = self.build.build_command("v1.0.0")[-3]
        self.assertNotEqual(first, second, "同じ版でもビルドのたびに別のフォルダ")
        self.assertRegex(first, r"/ToNAutoBeginner/v1\.0\.0-\d{20}$")
        folder = first.rsplit("/", 1)[1]
        self.assertTrue(AutoUpdate.RE_EXTRACT_DIR.fullmatch(folder), "掃除の形と合う")
        self.assertEqual(self.build.build_stamp(datetime(2026, 10, 2, 12, 34, 56, 7)), "20261002123456000007")

    def test_the_version_is_read_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.py"
            path.write_text('# x\nAPP_VERSION       = "v2.5.0"\nOTHER = 1\n', encoding="utf-8")
            self.assertEqual(self.build.read_app_version(path), "v2.5.0")
            path.write_text("OTHER = 1\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                self.build.read_app_version(path)
        self.assertEqual(self.build.read_app_version(), config.APP_VERSION, "本物の config.py と同じ")

    def test_a_bad_version_stops(self):
        for bad in ("dev", "v1.2.3.4.5", "v1.x"):
            with self.assertRaises(SystemExit, msg=bad):
                self.build.build_command(bad)

    def test_main_runs_nuitka_in_the_repo_root(self):
        with patch.object(self.build.subprocess, "run") as run, patch("builtins.print"), \
             patch.object(self.build, "find_iscc", return_value=None):
            run.return_value.returncode = 0
            self.assertEqual(self.build.main(), 0, "Inno Setup が無くても exe ができれば成功")
        self.assertEqual(run.call_count, 1)
        cmd = run.call_args.args[0]
        self.assertTrue(any(a.startswith(f"--onefile-tempdir-spec={{CACHE_DIR}}/ToNAutoBeginner/{config.APP_VERSION}-")
                            for a in cmd), cmd)
        self.assertEqual(Path(run.call_args.kwargs["cwd"]), REPO_ROOT)

    def test_the_installer_is_built_after_the_exe(self):
        iscc = Path("C:/Inno/ISCC.exe")
        with patch.object(self.build.subprocess, "run") as run, patch("builtins.print"), \
             patch.object(self.build, "find_iscc", return_value=iscc):
            run.return_value.returncode = 0
            self.assertEqual(self.build.main(), 0)
        nuitka, setup = (c.args[0] for c in run.call_args_list)
        self.assertEqual(nuitka[1:3], ["-m", "nuitka"])
        numbers = config.APP_VERSION.lstrip("v")
        self.assertEqual(setup, [str(iscc), f"/DAppVersion={numbers}",
                                 str(REPO_ROOT / "installer" / "ToNAutoBeginner.iss")])

    def test_no_installer_when_the_exe_failed(self):
        with patch.object(self.build.subprocess, "run") as run, patch("builtins.print"), \
             patch.object(self.build, "find_iscc", return_value=Path("C:/Inno/ISCC.exe")):
            run.return_value.returncode = 3
            self.assertEqual(self.build.main(), 3)
        self.assertEqual(run.call_count, 1)

    def test_iscc_is_found_by_env_then_path_then_the_usual_places(self):
        with patch.object(self.build.shutil, "which", return_value="/bin/ISCC"):
            self.assertEqual(self.build.find_iscc({"ISCC": "D:/x/ISCC.exe"}), Path("D:/x/ISCC.exe"))
            self.assertEqual(self.build.find_iscc({}), Path("/bin/ISCC"))
        with tempfile.TemporaryDirectory() as d, \
             patch.object(self.build.shutil, "which", return_value=None):
            self.assertIsNone(self.build.find_iscc({"LOCALAPPDATA": d}))
            exe = Path(d) / "Programs" / "Inno Setup 6" / "ISCC.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            self.assertEqual(self.build.find_iscc({"LOCALAPPDATA": d,
                                                   "ProgramFiles": d, "ProgramFiles(x86)": d}), exe)


class TestInstallerScript(unittest.TestCase):
    """installer/ToNAutoBeginner.iss（Inno Setup）の約束ごと。ISCC は呼ばない"""

    def setUp(self):
        self.text = (REPO_ROOT / "installer" / "ToNAutoBeginner.iss").read_text(encoding="utf-8-sig")

    def _setting(self, key):
        m = re.search(rf"^{re.escape(key)}=(.*)$", self.text, re.M)
        self.assertIsNotNone(m, key)
        return m.group(1).strip()

    def test_it_installs_per_user_where_the_auto_update_can_write(self):
        self.assertEqual(self._setting("PrivilegesRequired"), "lowest")
        self.assertEqual(self._setting("DefaultDirName"), r"{autopf}\{#AppName}")

    def test_the_running_check_uses_the_apps_mutex(self):
        self.assertEqual(self._setting("AppMutex"), config.APP_MUTEX_NAME)

    def test_the_app_id_is_fixed(self):
        self.assertEqual(self._setting("AppId"), "{{F3825561-B8B1-4328-8C50-51C1EFCF0BF5}",
                         "変えると上書きもアンインストールも効かなくなる")

    def test_uninstall_removes_the_data_and_the_extract_dirs(self):
        section = self.text.split("[UninstallDelete]", 1)[1]
        for name in (r"{userappdata}\{#AppName}", r"{localappdata}\{#AppName}",
                     r"{app}\{#AppExe}.old"):
            self.assertIn(f'Name: "{name}"', section, name)
        self.assertIn('#define AppName "ToNAutoBeginner"', self.text)
        self.assertEqual(config.SETTINGS_PATH.parent.name, "ToNAutoBeginner", "%APPDATA% の同じフォルダ")

    def test_it_packs_the_exe_that_nuitka_builds(self):
        self.assertIn('#define AppExe "ToNAutoBeginner.exe"', self.text)
        self.assertIn('Source: "..\\{#AppExe}"', self.text)
        self.assertEqual(config.UPDATE_ASSET_NAME, "ToNAutoBeginner.exe")
        self.assertEqual(self._setting("OutputBaseFilename"), "ToNAutoBeginner-Setup")


class TestRunningMutex(unittest.TestCase):
    """インストーラーが起動中と分かる目印（main.hold_running_mutex）"""

    def setUp(self):
        import main
        self.main = main
        self.addCleanup(setattr, main, "_running_mutex", None)
        main._running_mutex = None

    def test_it_creates_the_named_mutex_once(self):
        fake = MagicMock()
        fake.windll.kernel32.CreateMutexW.return_value = 77
        with patch.object(self.main, "ctypes", fake), patch.object(self.main.sys, "platform", "win32"):
            self.main.hold_running_mutex()
            self.main.hold_running_mutex()
        fake.windll.kernel32.CreateMutexW.assert_called_once_with(None, False, config.APP_MUTEX_NAME)
        self.assertEqual(self.main._running_mutex, 77)

    def test_a_failure_does_not_stop_the_start(self):
        fake = MagicMock()
        fake.windll.kernel32.CreateMutexW.side_effect = OSError("x")
        with patch.object(self.main, "ctypes", fake), patch.object(self.main.sys, "platform", "win32"):
            self.main.hold_running_mutex()
        self.assertIsNone(self.main._running_mutex)




class TestOldExtractDirs(unittest.TestCase):
    """古い onefile の展開先を消す（一時フォルダで。本物の LOCALAPPDATA・APPDATA には触らない）。
    自分の展開先は版ではなく、動いている自分の場所で決める"""

    OWN = "v1.0.0-20261002120000000001"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name) / "ToNAutoBeginner"
        for name in (self.OWN, "v1.0.0-20261001090000000000", "v0.9.0-20260901000000000000",
                     "v1.0.0", "notes", "cache", "v1.x-abc"):
            (self.base / name).mkdir(parents=True)
            (self.base / name / "a.txt").write_text("x", encoding="utf-8")
        (self.base / "v0.8.0-20260801000000000000.txt").write_text("a file", encoding="utf-8")
        self.own = self.base / self.OWN
        self.written = []
        p = patch.object(DebugLog, "write", side_effect=self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def _left(self):
        return sorted(p.name for p in self.base.iterdir())

    def _clean(self, **kw):
        kw.setdefault("own", self.own)
        kw.setdefault("onefile", True)
        return AutoUpdate.cleanup_old_extract_dirs(self.base, **kw)

    def test_other_builds_are_removed_and_mine_stays(self):
        removed = self._clean()
        self.assertEqual(removed, ["v0.9.0-20260901000000000000", "v1.0.0-20261001090000000000"],
                         "ほかの版も、同じ版の古い印も消す")
        self.assertEqual(self._left(), sorted([self.OWN, "cache", "notes", "v1.0.0", "v1.x-abc",
                                               "v0.8.0-20260801000000000000.txt"]),
                         "自分の展開先・形の違うもの・ファイルは残す")
        self.assertTrue(any("古い版の展開先を消しました" in m for m in self.written))

    def test_my_place_comes_from_the_running_module(self):
        # onefile では、動いているモジュールのファイルは展開先にある
        with patch.object(AutoUpdate, "__file__", str(self.own / "AutoUpdate.py")):
            self.assertEqual(AutoUpdate.own_extract_dir(), self.own.resolve())
            self.assertEqual(len(AutoUpdate.cleanup_old_extract_dirs(self.base, onefile=True)), 2)
        self.assertIn(self.OWN, self._left())

    def test_an_unknown_place_removes_nothing(self):
        for own in (Path(self._dir.name) / "elsewhere", self.base, self.base / self.OWN / "deeper"):
            self.assertEqual(self._clean(own=own), [], own)
        self.assertEqual(len([n for n in self._left() if n.startswith("v")]), 6)
        self.assertTrue(any("自分の展開先が分からない" in m for m in self.written))

    def test_python_runs_do_nothing(self):
        self.assertEqual(self._clean(onefile=False), [])
        self.assertFalse(AutoUpdate.is_onefile_exe(), "python で動かしている")
        self.assertEqual(AutoUpdate.cleanup_old_extract_dirs(self.base, own=self.own), [])
        self.assertIn("v0.9.0-20260901000000000000", self._left())

    def test_the_onefile_flag_comes_from_nuitka(self):
        flag = type("Compiled", (), {"onefile": True})()
        with patch.object(AutoUpdate, "__compiled__", flag, create=True):
            self.assertTrue(AutoUpdate.is_onefile_exe())
        flag.onefile = False
        with patch.object(AutoUpdate, "__compiled__", flag, create=True):
            self.assertFalse(AutoUpdate.is_onefile_exe(), "standalone は対象外")

    def test_folders_in_use_are_skipped(self):
        real = shutil.rmtree

        def rmtree(path, *a, **kw):
            if Path(path).name.startswith("v0.9.0"):
                raise PermissionError("in use")
            return real(path, *a, **kw)

        with patch.object(AutoUpdate.shutil, "rmtree", side_effect=rmtree):
            removed = self._clean()
        self.assertEqual(removed, ["v1.0.0-20261001090000000000"])
        self.assertIn("v0.9.0-20260901000000000000", self._left())
        self.assertTrue(any("消せません" in m and "v0.9.0" in m for m in self.written), self.written)

    def test_the_settings_folder_is_never_touched(self):
        with patch.object(config, "SETTINGS_PATH", self.base / "settings.json"):
            self.assertEqual(self._clean(), [])
        self.assertIn("v0.9.0-20260901000000000000", self._left())

    def test_the_default_place_is_local_appdata(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": self._dir.name}):
            self.assertEqual(AutoUpdate.extract_base_dir(), self.base)
            self.assertEqual(len(AutoUpdate.cleanup_old_extract_dirs(own=self.own, onefile=True)), 2)
        with patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            self.assertIsNone(AutoUpdate.extract_base_dir())
            self.assertEqual(AutoUpdate.cleanup_old_extract_dirs(own=self.own, onefile=True), [])

    def test_a_missing_base_is_fine(self):
        self.assertEqual(AutoUpdate.cleanup_old_extract_dirs(self.base / "none", own=self.own,
                                                             onefile=True), [])

    def test_startup_runs_it_in_the_background(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        self.assertIn("threading.Thread(target=AutoUpdate.cleanup_old_extract_dirs, daemon=True).start()", src)





class TestMultiWindowLaunch(unittest.TestCase):
    """1〜8窓の起動と自動Join"""

    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def test_eight_windows_get_distinct_osc_ports(self):
        pairs = [OSCClient.ports_for_window(i) for i in range(config.MAX_WINDOWS)]
        flat = [p for pair in pairs for p in pair]

        self.assertEqual(len(set(flat)), len(flat), "受信・送信ポートが衝突している")

    def test_eight_windows_get_distinct_instances(self):
        """同じprivateインスタンスには入れないので、窓ごとに別インスタンスが要る"""
        nums = [VRChatLauncher.build_ton_link("usr_x", i).split(":")[-1].split("~")[0]
                for i in range(config.MAX_WINDOWS)]

        self.assertEqual(len(set(nums)), config.MAX_WINDOWS)

    def test_launch_plan_covers_eight_windows(self):
        tabs = []
        for i in range(config.MAX_WINDOWS):
            tab = type("FakeTab", (), {})()
            tab.idx = i
            tab.v_profile = self.FakeVar(i)
            tabs.append(tab)

        plan = mainGUI.build_launch_plan(mainGUI.tabs_to_launch(tabs, config.MAX_WINDOWS))

        self.assertEqual(len(plan), config.MAX_WINDOWS)
        self.assertEqual([p[0] for p in plan], list(range(1, config.MAX_WINDOWS + 1)))
        self.assertEqual([p[2] for p in plan], list(range(config.MAX_WINDOWS)))

    def test_wait_for_windows_counts_new_ones_only(self):
        """逐次起動では「既存＋i個目」が出たかで次へ進む"""
        baseline = {1, 2}
        states = [[1, 2], [1, 2, 3], [1, 2, 3, 4]]

        def discover():
            return states.pop(0) if len(states) > 1 else states[0]

        found = VRChatLauncher.wait_for_windows(baseline, 2, 5.0, poll_sec=0.01,
                                                discover=discover)

        self.assertEqual(found, [3, 4])




class TestJoinStatus(unittest.TestCase):
    """ToNに入れたかをログで確認する"""

    def _log_with(self, tmpdir, line):
        path = Path(tmpdir) / "output_log_2026-01-01_00-00-00.txt"
        path.write_text(line + chr(10), encoding="utf-8")
        return path

    def test_ton_join_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._log_with(
                d, "2026.01.01 00:00:00 Debug      -  [Behaviour] Joining "
                   + config.TON_WORLD_ID + ":12345~private(usr_x)~region(jp)")

            self.assertEqual(VRChatLauncher.joined_world_id(path), config.TON_WORLD_ID)
            self.assertTrue(VRChatLauncher.joined_ton(path))

    def test_other_world_is_not_ton(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._log_with(
                d, "2026.01.01 00:00:00 Debug      -  [Behaviour] Joining "
                   "wrld_other-0000:1~region(jp)")

            self.assertFalse(VRChatLauncher.joined_ton(path))

    def test_missing_log_is_not_ton(self):
        self.assertFalse(VRChatLauncher.joined_ton("does_not_exist.txt"))

    def test_report_names_the_windows_that_are_not_in_ton(self):
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        tabs = []
        for i, in_ton in enumerate((True, False, True)):
            tab = type("FakeTab", (), {})()
            tab.idx = i
            tab.v_log = TestMultiWindowLaunch.FakeVar(f"log{i}.txt")
            tab._in_ton = in_ton
            tabs.append(tab)
        app.tabs = tabs
        joined = {f"log{i}.txt": t._in_ton for i, t in enumerate(tabs)}

        with patch.object(VRChatLauncher, "joined_ton", side_effect=lambda p: joined[p]):
            missing = mainGUI.App._report_join_status(app)

        self.assertEqual(missing, [2])
        self.assertTrue(any("ToNに入れていない窓" in m for m in app.logs))




class TestReadmeRelease(unittest.TestCase):
    def _readme(self):
        return (REPO_ROOT / "README.md"
                ).read_text(encoding="utf-8")

    def test_the_download_url_follows_the_latest_release(self):
        """版を書くと、リリース前は404・リリース後は古くなる。latest なら直さなくてよい"""
        # markdown のリンク [文字](URL) の閉じ括弧を URL に含めない
        urls = re.findall(r"https://github\.com/[^\s)]+/releases/[^\s)]+", self._readme())

        self.assertTrue(urls, "URL が見つからない")
        for url in urls:
            self.assertIn("/releases/latest/download/", url)

    def test_the_url_points_at_the_installer_and_the_update_asset(self):
        urls = re.findall(r"releases/latest/download/([^\s)]+)", self._readme())

        self.assertEqual(urls, ["ToNAutoBeginner-Setup.exe", config.UPDATE_ASSET_NAME])

    def test_the_url_points_at_this_repository(self):
        self.assertIn(f"github.com/{config.GITHUB_REPO}/releases/",
                      self._readme())


class TestMigrationRules(unittest.TestCase):
    """exe 単体 → インストーラー版への移行（Migration。Windows の処理は差し替え）"""

    def test_who_is_moved(self):
        installed = Path("C:/Users/a/AppData/Local/Programs/ToNAutoBeginner")
        self.assertFalse(Migration.needs_migration(None, installed), "開発実行は移さない")
        self.assertTrue(Migration.needs_migration(Path("D:/tools/ToNAutoBeginner.exe"), None),
                        "インストールしていない")
        self.assertTrue(Migration.needs_migration(Path("D:/tools/ToNAutoBeginner.exe"), installed),
                        "インストールしてあっても、外の exe なら移す")
        self.assertFalse(Migration.needs_migration(installed / "ToNAutoBeginner.exe", installed))

    def test_the_install_dir_comes_from_inno_setups_uninstall_key(self):
        fake = MagicMock()
        fake.QueryValueEx.return_value = ("C:\\Apps\\ToNAutoBeginner\\", 1)
        with patch.dict(sys.modules, {"winreg": fake}):
            self.assertEqual(Migration.installed_dir(), Path("C:\\Apps\\ToNAutoBeginner"))
        self.assertEqual(fake.OpenKey.call_args.args[1], Migration.UNINSTALL_KEY)
        iss = (REPO_ROOT / "installer" / "ToNAutoBeginner.iss").read_text(encoding="utf-8-sig")
        app_id = re.search(r"^AppId=\{(\{[0-9A-F-]+\})$", iss, re.M).group(1)
        self.assertTrue(Migration.UNINSTALL_KEY.endswith("\\" + app_id + "_is1"), "AppId と同じ")
        fake.OpenKey.side_effect = OSError("no key")
        with patch.dict(sys.modules, {"winreg": fake}):
            self.assertIsNone(Migration.installed_dir())

    def test_the_script_waits_for_the_app_then_runs_setup_silently(self):
        setup = Path("C:/Users/O'Neil/AppData/Local/Temp/ToNAutoBeginner-Setup.exe")
        script = Migration.setup_script(setup)
        self.assertIn(f"TryOpenExisting('{config.APP_MUTEX_NAME}'", script)
        self.assertIn("'C:/Users/O''Neil/AppData/Local/Temp/ToNAutoBeginner-Setup.exe'", script,
                      "' は2つ重ねる")
        self.assertIn("'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/launch=1'", script)
        self.assertLess(script.index("TryOpenExisting"), script.index("Start-Process"))
        self.assertNotIn("ExitCode", script, "古い exe を渡さなければ消さない")
        cmd = Migration.powershell_command(script)
        self.assertEqual(cmd[-2], "-EncodedCommand")
        self.assertEqual(base64.b64decode(cmd[-1]).decode("utf-16-le"), script)

    def test_the_old_exe_is_removed_only_after_setup_succeeds(self):
        setup = Path("C:/Temp/ToNAutoBeginner-Setup.exe")
        script = Migration.setup_script(setup, Path("D:/tools/ToNAutoBeginner.exe"))
        removal = script.index("if ($p.ExitCode -eq 0)")
        self.assertLess(script.index("Start-Process"), removal, "Setup が終わってから")
        self.assertIn("-Wait -PassThru", script)
        self.assertIn("'D:/tools/ToNAutoBeginner.exe', 'D:/tools/ToNAutoBeginner.exe.old'",
                      script[removal:])

    def test_the_installer_relaunches_after_a_silent_migration(self):
        iss = (REPO_ROOT / "installer" / "ToNAutoBeginner.iss").read_text(encoding="utf-8-sig")
        self.assertIn("Check: LaunchAfterSilentInstall", iss)
        self.assertIn("{param:launch|0}", iss)
        self.assertIn("DisableDirPage=auto", iss, "新しく入れる人は場所を選ぶ")
        self.assertIn("/launch=1", Migration.SETUP_ARGS)

    def test_the_old_exe_is_removed_but_never_the_running_one(self):
        with tempfile.TemporaryDirectory() as d:
            old = Path(d) / "ToNAutoBeginner.exe"
            old.write_text("x", encoding="utf-8")
            (Path(d) / "ToNAutoBeginner.exe.old").write_text("x", encoding="utf-8")
            self.assertTrue(Migration.remove_old_exe(old, old), "今動いているもの → 触らない")
            self.assertTrue(old.exists())
            self.assertTrue(Migration.remove_old_exe(old, Path("C:/x/ToNAutoBeginner.exe")))
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertTrue(Migration.remove_old_exe(old, None), "もう無い")


class TestMigrationInTheApp(unittest.TestCase):
    """mainGUI 側の手順（画面・スレッド・Setup の起動は差し替え）"""

    def _app(self, running=False):
        app = MagicMock()
        app._running = running
        app.MIGRATION_RETRY_MS = mainGUI.App.MIGRATION_RETRY_MS
        return app

    def test_nothing_happens_when_not_needed(self):
        app = self._app()
        with patch.object(AutoUpdate, "current_exe_path", return_value=None), \
             patch.object(mainGUI.threading, "Thread") as thread:
            self.assertFalse(mainGUI.App._start_migration(app))
        thread.assert_not_called()

    def test_a_standalone_exe_starts_the_download(self):
        app = self._app()
        exe = Path("D:/tools/ToNAutoBeginner.exe")
        with patch.object(AutoUpdate, "current_exe_path", return_value=exe), \
             patch.object(Migration, "installed_dir", return_value=None), \
             patch.object(mainGUI.threading, "Thread") as thread:
            self.assertTrue(mainGUI.App._start_migration(app))
        worker = thread.call_args.kwargs["target"]
        release = {"assets": [{"name": config.SETUP_ASSET_NAME, "browser_download_url": "u", "size": 9}]}
        with patch.object(AutoUpdate, "fetch_latest_release", return_value=release), \
             patch.object(AutoUpdate, "download_to_temp", return_value=Path("t")) as dl:
            worker()
        dl.assert_called_once_with("u", 9)
        app._after_from_worker.assert_called_once_with(app._finish_migration, exe, Path("t"), True)

    def test_finishing_writes_the_old_path_runs_setup_and_closes(self):
        app = self._app()
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "tmpabc.exe.download"
            tmp.write_text("setup", encoding="utf-8")
            mainGUI.save_settings({"win_count": 3})
            with patch.object(mainGUI.messagebox, "showinfo"), \
                 patch.object(Migration, "launch_setup", return_value=True) as launch:
                mainGUI.App._finish_migration(app, Path("D:/tools/ToNAutoBeginner.exe"), tmp)
            setup = Path(d) / config.SETUP_ASSET_NAME
            launch.assert_called_once_with(setup, Path("D:/tools/ToNAutoBeginner.exe"))
            self.assertTrue(setup.exists())
        data = mainGUI.load_settings()
        self.assertEqual(data[Migration.SETTINGS_KEY], str(Path("D:/tools/ToNAutoBeginner.exe")))
        self.assertEqual(data["win_count"], 3, "ほかの設定はそのまま")
        app._on_close.assert_called_once()

    def test_a_failed_download_keeps_using_the_app(self):
        app = self._app()
        with patch.object(Migration, "launch_setup") as launch:
            mainGUI.App._finish_migration(app, Path("D:/x.exe"), None)
        launch.assert_not_called()
        app._on_close.assert_not_called()
        self.assertIn("次の起動でやり直します", app._log.call_args.args[0])

    def test_it_waits_while_the_macro_runs(self):
        app = self._app(running=True)
        with patch.object(Migration, "launch_setup") as launch:
            mainGUI.App._finish_migration(app, Path("D:/x.exe"), Path("t"))
        launch.assert_not_called()
        self.assertEqual(app.after.call_args.args[0], mainGUI.App.MIGRATION_RETRY_MS)

    def test_the_installed_app_removes_the_old_exe_once(self):
        app = self._app()
        installed = Path("C:/Apps/ToNAutoBeginner")
        mainGUI.save_settings({Migration.SETTINGS_KEY: "D:/tools/ToNAutoBeginner.exe", "win_count": 2})
        with patch.object(AutoUpdate, "current_exe_path", return_value=installed / "ToNAutoBeginner.exe"), \
             patch.object(Migration, "installed_dir", return_value=installed), \
             patch.object(Migration, "remove_old_exe", return_value=True) as remove:
            mainGUI.App._cleanup_migrated_exe(app)
            mainGUI.App._cleanup_migrated_exe(app)
        remove.assert_called_once()
        self.assertEqual(mainGUI.load_settings(), {"win_count": 2})

    def test_not_yet_installed_keeps_the_mark(self):
        app = self._app()
        mainGUI.save_settings({Migration.SETTINGS_KEY: "D:/tools/ToNAutoBeginner.exe"})
        with patch.object(AutoUpdate, "current_exe_path", return_value=Path("D:/tools/ToNAutoBeginner.exe")), \
             patch.object(Migration, "installed_dir", return_value=None), \
             patch.object(Migration, "remove_old_exe") as remove:
            mainGUI.App._cleanup_migrated_exe(app)
        remove.assert_not_called()
        self.assertIn(Migration.SETTINGS_KEY, mainGUI.load_settings())
