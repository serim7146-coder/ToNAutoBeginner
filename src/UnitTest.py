import unittest
from unittest.mock import patch, MagicMock
import threading
import time
import sys
import json
import tempfile
import gzip
import ctypes
import io
from datetime import datetime
from pathlib import Path

# Windowsライブラリをモック化
sys.modules['win32gui'] = MagicMock()
sys.modules['keyboard'] = MagicMock()
sys.modules['pydirectinput'] = MagicMock()

import WindowOperator
import ConnectDB
import PlaySound
import LogParser
import MatchTNL
import ProcessCheck
import RoundSequence
import GroupRound
import RoundDecision
import Statistics
import StatisticsGUI
import LogMonitor
import ActionExecutor
import SharedState
import VRChatDiscovery
import VRChatLauncher
import OSCClient
import OSCReceiver
import ScreenCapture
import ToNEntry
import mainGUI
import AutoUpdate
import config
from State import WindowConfig, WindowState

ConnectDB.SUPABASE_URL = "https://example.supabase.co"
ConnectDB.SUPABASE_KEY = "test-key"


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


class TestConnectDbEnv(unittest.TestCase):
    def test_env_file_candidates_include_source_and_repo_locations(self):
        candidates = ConnectDB.env_file_candidates()
        repo_root = Path(__file__).resolve().parent.parent
        legacy_resource_dir = repo_root / "ToNAutoBeginner"

        self.assertTrue(any(path.parent.name == "src" and path.name == ".env" for path in candidates))
        self.assertTrue(any(path.parent == repo_root and path.name == ".env" for path in candidates))
        self.assertTrue(any(path.parent == legacy_resource_dir and path.name == ".env" for path in candidates))

    def test_env_file_candidates_prefers_nuitka_containing_dir(self):
        class Compiled:
            containing_dir = "C:/packed-app"

        ConnectDB.__compiled__ = Compiled()
        try:
            self.assertEqual(ConnectDB.env_file_candidates()[0], Path("C:/packed-app") / ".env")
        finally:
            del ConnectDB.__compiled__

# ═══════════════════════════════════════════════
#  WindowOperator.py
# ═══════════════════════════════════════════════
class TestFocusWindow(unittest.TestCase):
    """前面化は成否を返す。失敗を握り潰すと別の窓へ入力が飛ぶため。"""

    def test_hwnd_zero_returns_false(self):
        with patch('win32gui.SetForegroundWindow') as mock:
            self.assertFalse(WindowOperator.focus_window(0))
            mock.assert_not_called()

    def test_returns_true_without_raising_when_already_foreground(self):
        """既に前面ならSetForegroundWindowを呼ばずTrue"""
        with patch('win32gui.GetForegroundWindow', return_value=123), \
             patch('win32gui.IsIconic', return_value=False), \
             patch('win32gui.SetForegroundWindow') as mock:
            self.assertTrue(WindowOperator.focus_window(123))
            mock.assert_not_called()

    def test_returns_true_when_focus_is_taken(self):
        with patch('win32gui.IsIconic', return_value=False), \
             patch('win32gui.GetForegroundWindow', side_effect=[0, 0, 123, 123]), \
             patch('win32gui.BringWindowToTop'), \
             patch('win32gui.SetForegroundWindow'), \
             patch.object(WindowOperator.time, "sleep"):
            self.assertTrue(WindowOperator.focus_window(123))

    def test_retries_then_returns_false_when_focus_refused(self):
        """Windowsに前面化を拒否され続けたらFalse（呼び出し側が中止できる）"""
        with patch('win32gui.IsIconic', return_value=False), \
             patch('win32gui.GetForegroundWindow', return_value=999), \
             patch('win32gui.BringWindowToTop'), \
             patch('win32gui.SetForegroundWindow') as mock, \
             patch.object(WindowOperator.time, "sleep"):
            self.assertFalse(WindowOperator.focus_window(123))
            self.assertEqual(mock.call_count, config.FOCUS_RETRY_MAX)


class TestActionExecutorFocusFailure(unittest.TestCase):
    """フォーカスを取れない時は操作を送らない"""

    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()

    def test_do_skip_aborts_when_focus_fails(self):
        cfg = WindowConfig(hwnd=123)
        st = WindowState(in_round=True)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        with patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()
        mock_hold.assert_not_called()
        self.assertTrue(any("フォーカス取得失敗" in m for m in logs))

    def test_do_after_round_aborts_when_focus_fails(self):
        cfg = WindowConfig(hwnd=123)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(WindowOperator, "click") as mock_click, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_after_round()
        mock_hold.assert_not_called()
        mock_click.assert_not_called()

class TestOSCClient(unittest.TestCase):
    """OSC送信（多重起動で窓ごとにポートを分ける）"""

    def test_ports_are_distinct_per_window(self):
        seen = [OSCClient.ports_for_window(i) for i in range(4)]
        self.assertEqual(seen[0], (config.OSC_BASE_IN_PORT, config.OSC_BASE_IN_PORT + 1))
        flat = [p for pair in seen for p in pair]
        self.assertEqual(len(flat), len(set(flat)), "窓ごとにポートが重複してはいけない")

    def test_launch_arg_format(self):
        self.assertEqual(OSCClient.osc_launch_arg(0), "--osc=9000:127.0.0.1:9001")
        self.assertEqual(OSCClient.osc_launch_arg(2), "--osc=9020:127.0.0.1:9021")

    def test_message_is_valid_osc(self):
        """OSCは4バイト境界に揃っている必要がある"""
        for addr, val in [("/input/MoveForward", 1),
                          ("/input/LookHorizontal", 1.0),
                          ("/input/Jump", True)]:
            msg = OSCClient.OSCClient.build_message(addr, val)
            with self.subTest(addr=addr):
                self.assertEqual(len(msg) % 4, 0)
                self.assertTrue(msg.startswith(addr.encode()))

    def test_message_type_tags(self):
        self.assertIn(b",i", OSCClient.OSCClient.build_message("/x", 1))
        self.assertIn(b",f", OSCClient.OSCClient.build_message("/x", 1.0))

    def test_bool_is_sent_as_int_not_bool_tag(self):
        """ブール型タグ(,T/,F)は引数を持たない。VRChatへ送るとメモリ上の
        ゴミを読まれて巨大な値が入力に固定される事故が起きたため禁止。"""
        for value in (True, False):
            msg = OSCClient.OSCClient.build_message("/input/MoveForward", value)
            with self.subTest(value=value):
                self.assertIn(b",i", msg)
                self.assertNotIn(b",T", msg)
                self.assertNotIn(b",F", msg)
                self.assertEqual(len(msg) % 4, 0)

    def test_stop_all_includes_analog_axes(self):
        """Vertical/Horizontal を漏らすと移動が残り続ける"""
        client = OSCClient.OSCClient(9000)
        sent = []
        with patch.object(client, "send", side_effect=lambda a, v: sent.append(a) or True):
            client.stop_all()
        for address in ("/input/Vertical", "/input/Horizontal",
                        "/input/LookHorizontal", "/input/LookVertical",
                        "/input/MoveForward", "/input/MoveRight"):
            with self.subTest(address=address):
                self.assertIn(address, sent)

    def test_press_sends_reset_then_press_then_release(self):
        """0→1→0 で送る。0から1への変化で反応する入力があるため"""
        client = OSCClient.OSCClient(9000)
        sent = []
        with patch.object(client, "send", side_effect=lambda a, v: sent.append((a, v)) or True), \
             patch.object(OSCClient.time, "sleep"):
            client.press("/input/MoveForward", 0.5)
        self.assertEqual([v for _a, v in sent], [0, 1, 0])

    def test_launch_args_include_osc_when_index_given(self):
        args = VRChatLauncher.build_launch_args(
            Path("C:/launch.exe"), 0, True, None, osc_index=1)
        self.assertIn("--osc=9010:127.0.0.1:9011", args)

    def test_launch_args_omit_osc_when_index_none(self):
        args = VRChatLauncher.build_launch_args(Path("C:/launch.exe"), 0, True, None)
        self.assertFalse(any(a.startswith("--osc=") for a in args))


class TestPerWindowInstance(unittest.TestCase):
    """窓ごとに別のprivateインスタンスへ入る（同じインスタンスには入れないため）"""

    LINK = "vrchat://launch?ref=vrchat.com&id=wrld_abc-123:12345~private(usr_x)~region(jp)"

    def test_instance_number_differs_per_window(self):
        links = [VRChatLauncher.with_unique_instance(self.LINK, i) for i in range(4)]
        nums = [l.split(":")[-1].split("~")[0] for l in links]
        self.assertEqual(len(set(nums)), 4, "窓ごとに別インスタンスでなければならない")

    def test_only_instance_number_changes(self):
        out = VRChatLauncher.with_unique_instance(self.LINK, 1)
        self.assertIn("wrld_abc-123:", out)
        self.assertIn("~private(usr_x)~region(jp)", out)
        self.assertNotIn(":12345~", out)

    def test_empty_link_passthrough(self):
        self.assertEqual(VRChatLauncher.with_unique_instance("", 0), "")
        self.assertIsNone(VRChatLauncher.with_unique_instance(None, 0))

    def test_build_ton_link_uses_ton_world(self):
        link = VRChatLauncher.build_ton_link("usr_abc", 0)
        self.assertIn(config.TON_WORLD_ID, link)
        self.assertIn("~private(usr_abc)", link)
        self.assertTrue(link.startswith("vrchat://launch?"))

    def test_build_ton_link_requires_user_id(self):
        self.assertIsNone(VRChatLauncher.build_ton_link("", 0))

    def test_latest_user_id_reads_auth_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "output_log_2026-08-20_10-00-00.txt"
            line = ("2026.08.20 10:00:00 Log - User Authenticated: serim01 "
                    "(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)")
            p.write_text(line, encoding="utf-8")
            self.assertEqual(
                VRChatLauncher.latest_user_id(d),
                "usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3")


class TestToNEntry(unittest.TestCase):
    """入室時の自動操作（移動はOSC・クリックはマウス）"""

    def _entry(self, logs=None):
        return ToNEntry.ToNEntry(
            0x1234, osc_port=19990,
            log=(logs.append if logs is not None else None))

    def test_steps_have_expected_shape(self):
        """手順の構造を確認する。

        秒数は実機調整で頻繁に変わるので値そのものは検証しない
        （変えるたびにテストが壊れると調整の邪魔になる）。
        """
        steps = config.TON_ENTRY_STEPS
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0]["move"], "right")
        self.assertEqual(steps[1]["move"], "left")
        self.assertEqual(steps[2]["move"], "right")
        self.assertIsNone(steps[3]["move"], "最後は移動せず続けて押す")
        for step in steps[:3]:
            with self.subTest(label=step["label"]):
                self.assertGreater(step["sec"], 0, "移動する段は秒数が必要")
        self.assertEqual([s["label"] for s in steps],
                         ["警告同意", "難易度(Casual)", "BGM", "LET ME PLAY"])

    def test_move_uses_osc_and_releases(self):
        """移動はOSCで送り、必ず解除する（入力が残ると操作不能になる）"""
        entry = self._entry()
        with patch.object(entry._osc, "press", return_value=True) as mock_press, \
             patch.object(entry._osc, "stop_all") as mock_stop:
            self.assertTrue(entry.move("right", 0.4))
        mock_press.assert_called_once_with("/input/MoveRight", 0.4)
        mock_stop.assert_called()

    def test_move_rejects_unknown_direction(self):
        entry = self._entry()
        with patch.object(entry._osc, "press") as mock_press:
            self.assertFalse(entry.move("ななめ", 0.4))
        mock_press.assert_not_called()

    def test_click_requires_focus(self):
        """フォーカスを取れなければクリックしない（別の窓へ飛ぶため）"""
        logs = []
        entry = self._entry(logs)
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=False), \
             patch.object(ToNEntry.WindowOperator, "click") as mock_click:
            self.assertFalse(entry.click("テスト"))
        mock_click.assert_not_called()
        self.assertTrue(any("フォーカス取得失敗" in m for m in logs))

    def test_run_aborts_when_panel_never_appears(self):
        entry = self._entry()
        with patch.object(entry, "wait_for_panel", return_value=False), \
             patch.object(entry, "move") as mock_move, \
             patch.object(entry, "click") as mock_click:
            self.assertFalse(entry.run())
        mock_move.assert_not_called()
        mock_click.assert_not_called()

    def test_run_waits_before_first_action(self):
        """パネル検出直後は描画が整っていないので少し待つ"""
        entry = self._entry()
        slept = []
        with patch.object(entry, "wait_for_panel", return_value=True),              patch.object(entry, "move", return_value=True),              patch.object(entry, "click", return_value=True),              patch.object(entry._osc, "stop_all"),              patch.object(ToNEntry.time, "sleep", side_effect=slept.append):
            entry.run()
        self.assertIn(config.TON_ENTRY_START_DELAY_SEC, slept)

    def test_run_executes_moves_and_clicks_in_order(self):
        entry = self._entry()
        actions = []
        with patch.object(entry, "wait_for_panel", return_value=True), \
             patch.object(entry, "move",
                          side_effect=lambda d, s: actions.append(("move", d, s)) or True), \
             patch.object(entry, "click",
                          side_effect=lambda label: actions.append(("click", label)) or True), \
             patch.object(entry._osc, "stop_all"), \
             patch.object(ToNEntry.time, "sleep"):
            self.assertTrue(entry.run())
        # 設定から期待値を組み立てる（秒数は調整で変わるため直書きしない）
        expected = []
        for step in config.TON_ENTRY_STEPS:
            if step["move"]:
                expected.append(("move", step["move"], step["sec"]))
            expected.append(("click", step["label"]))
        self.assertEqual(actions, expected)

    def test_run_stops_when_cancelled(self):
        """中止フラグが立ったら操作を止め、入力も解除する"""
        entry = ToNEntry.ToNEntry(0x1234, osc_port=19990, is_running=lambda: False)
        with patch.object(entry, "wait_for_panel", return_value=True), \
             patch.object(entry, "move") as mock_move, \
             patch.object(entry._osc, "stop_all") as mock_stop:
            self.assertFalse(entry.run())
        mock_move.assert_not_called()
        mock_stop.assert_called()

    def test_run_aborts_when_click_fails(self):
        entry = self._entry()
        with patch.object(entry, "wait_for_panel", return_value=True), \
             patch.object(entry, "move", return_value=True), \
             patch.object(entry, "click", return_value=False), \
             patch.object(entry._osc, "stop_all") as mock_stop, \
             patch.object(ToNEntry.time, "sleep"):
            self.assertFalse(entry.run())
        mock_stop.assert_called()

    def test_loading_screen_is_excluded(self):
        """ロード画面にも赤いロゴが出るので背景色で除外する"""
        entry = self._entry()
        w = h = 200
        # 四隅が青緑 = ロード画面
        bits = bytearray(w * h * 4)
        for x, y in ((60, 60), (w - 60, 60), (60, h - 60), (w - 60, h - 60)):
            i = (y * w + x) * 4
            bits[i] = 200      # B
            bits[i + 1] = 100  # G
            bits[i + 2] = 20   # R
        self.assertTrue(entry._is_loading_screen(bytes(bits), w, h))

    def test_lobby_is_not_loading_screen(self):
        entry = self._entry()
        w = h = 200
        bits = bytes(w * h * 4)   # 全部黒
        self.assertFalse(entry._is_loading_screen(bits, w, h))

    def test_press_begin_moves_then_clicks(self):
        entry = self._entry()
        actions = []
        with patch.object(entry, "move",
                          side_effect=lambda d, s: actions.append((d, s)) or True), \
             patch.object(entry, "click", return_value=True), \
             patch.object(entry._osc, "stop_all"), \
             patch.object(ToNEntry.time, "sleep"):
            self.assertTrue(entry.press_begin())
        self.assertEqual(actions, [("forward", config.TON_ENTRY_BEGIN_FORWARD_SEC),
                                   ("left", config.TON_ENTRY_BEGIN_LEFT_SEC)])

    def test_entry_begin_distance_differs_from_round_end(self):
        """入室直後はラウンド終了後と位置が違うため別の値を使う"""
        self.assertNotEqual(config.TON_ENTRY_BEGIN_FORWARD_SEC, config.BEGIN_FORWARD_SEC)
        self.assertGreater(config.TON_ENTRY_BEGIN_FORWARD_SEC, 0)


class TestOscBranching(unittest.TestCase):
    """OSCが使えるかで移動手段と排他の粒度を変える"""

    def _executor(self, osc_port):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState()
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def test_uses_osc_only_when_port_assigned(self):
        self.assertTrue(self._executor(9000).uses_osc)
        self.assertFalse(self._executor(0).uses_osc)

    def test_move_uses_osc_when_available(self):
        """OSCが使える窓はキーを押さない（フォーカスを奪わない）"""
        ex = self._executor(9000)
        with patch.object(ex._osc, "press", return_value=True) as mock_press, \
             patch.object(ex._osc, "stop_all"), \
             patch.object(WindowOperator, "hold_key") as mock_key:
            ex.move("forward", 2.1)
        mock_press.assert_called_once_with("/input/MoveForward", 2.1)
        mock_key.assert_not_called()

    def test_move_falls_back_to_keyboard_without_osc(self):
        ex = self._executor(0)
        with patch.object(WindowOperator, "hold_key") as mock_key:
            ex.move("forward", 2.1)
        mock_key.assert_called_once_with("w", 2.1)

    def test_move_ignores_zero_duration(self):
        ex = self._executor(9000)
        with patch.object(ex._osc, "press") as mock_press:
            ex.move("forward", 0)
        mock_press.assert_not_called()

    def test_osc_move_does_not_hold_the_global_lock(self):
        """OSC移動中は他窓が操作できる（ロックを取らない）"""
        ex = self._executor(9000)
        held = []
        with patch.object(ex._osc, "press",
                          side_effect=lambda a, s: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked()) or True), \
             patch.object(ex._osc, "stop_all"):
            ex.move("forward", 1.0)
        self.assertEqual(held, [False], "OSC移動はロックを保持してはいけない")


class TestToNEntryLocking(unittest.TestCase):
    """入室操作はクリックだけ排他にする"""

    def _entry(self):
        return ToNEntry.ToNEntry(0x1234, osc_port=19990)

    def test_click_takes_the_global_lock(self):
        entry = self._entry()
        locked = []
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=True), \
             patch.object(ToNEntry.WindowOperator, "click",
                          side_effect=lambda: locked.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked())):
            entry.click("テスト")
        self.assertEqual(locked, [True], "クリックはロック内で行うこと")

    def test_move_does_not_take_the_lock(self):
        entry = self._entry()
        held = []
        with patch.object(entry._osc, "press",
                          side_effect=lambda a, s: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked()) or True), \
             patch.object(entry._osc, "stop_all"):
            entry.move("right", 0.38)
        self.assertEqual(held, [False], "移動はロック不要（OSCはフォーカスを奪わない）")

    def test_lock_is_released_after_click(self):
        entry = self._entry()
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=True), \
             patch.object(ToNEntry.WindowOperator, "click"):
            entry.click("テスト")
        self.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked())

    def test_click_aborts_without_focus_and_releases_lock(self):
        entry = self._entry()
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=False), \
             patch.object(ToNEntry.WindowOperator, "click") as mock_click:
            self.assertFalse(entry.click("テスト"))
        mock_click.assert_not_called()
        self.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked())


class TestOscAvailability(unittest.TestCase):
    """OSC可否の判定（起動時に1回だけ確定させる）"""

    def test_available_when_process_holds_expected_port(self):
        with patch.object(OSCClient, "udp_ports_of_process", return_value={9010, 5353}), \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertTrue(OSCClient.osc_available_for(0x1234, 1))

    def test_unavailable_when_port_missing(self):
        """手動起動の2窓目はポート競合でOSCが無効"""
        with patch.object(OSCClient, "udp_ports_of_process", return_value={5353}), \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertFalse(OSCClient.osc_available_for(0x1234, 1))

    def test_unavailable_when_pid_unknown(self):
        with patch("win32process.GetWindowThreadProcessId", return_value=(0, 0)):
            self.assertFalse(OSCClient.osc_available_for(0x1234, 0))


class TestUdpPortsByPid(unittest.TestCase):
    """UDPの待ち受け表は netstat 1回で全PIDぶん取る"""

    NETSTAT = (
        chr(10).join([
            "",
            "アクティブな接続",
            "",
            "  プロトコル  ローカル アドレス      外部アドレス            PID",
            "  UDP         0.0.0.0:9000           *:*                     4321",
            "  UDP         0.0.0.0:9010           *:*                     4321",
            "  UDP         127.0.0.1:5353         *:*                     999",
            "  UDP         [::]:9020              *:*                     555",
            "  TCP         0.0.0.0:80             0.0.0.0:0    LISTENING  111",
        ])
    )

    def _run_result(self, stdout):
        result = MagicMock()
        result.stdout = stdout
        return result

    def test_output_is_split_per_pid(self):
        with patch.object(OSCClient.subprocess, "run",
                          return_value=self._run_result(self.NETSTAT)) as mock_run:
            table = OSCClient.udp_ports_by_pid()

        mock_run.assert_called_once()
        self.assertEqual(table[4321], {9000, 9010})
        self.assertEqual(table[999], {5353})
        self.assertEqual(table[555], {9020}, "IPv6表記も拾うこと")
        self.assertNotIn(111, table, "TCPは含めないこと")

    def test_failure_returns_none(self):
        """「失敗」と「成功したがポート無し」を区別する"""
        with patch.object(OSCClient.subprocess, "run", side_effect=OSError("boom")):
            self.assertIsNone(OSCClient.udp_ports_by_pid())

    def test_of_process_delegates(self):
        with patch.object(OSCClient, "udp_ports_by_pid",
                          return_value={4321: {9000}}):
            self.assertEqual(OSCClient.udp_ports_of_process(4321), {9000})
            self.assertEqual(OSCClient.udp_ports_of_process(9999), set())

    def test_of_process_survives_a_failure(self):
        with patch.object(OSCClient, "udp_ports_by_pid", return_value=None):
            self.assertEqual(OSCClient.udp_ports_of_process(4321), set())

    def test_passing_the_table_does_not_run_netstat(self):
        table = {4321: {9010}}
        with patch.object(OSCClient.subprocess, "run") as mock_run, \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertTrue(OSCClient.osc_available_for(0x1234, 1, table))

        mock_run.assert_not_called()

    def test_missing_pid_in_the_table_is_unavailable_without_falling_back(self):
        """辞書にPIDが無いのは「ポートを持っていない」＝利用不可。撃ち直さない"""
        with patch.object(OSCClient.subprocess, "run") as mock_run, \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertFalse(OSCClient.osc_available_for(0x1234, 1, {999: {9010}}))

        mock_run.assert_not_called()

    def test_none_table_falls_back_per_window(self):
        """netstat失敗時は窓ごとの個別取得へ落ちる（全窓を巻き添えにしない）"""
        with patch.object(OSCClient, "udp_ports_of_process",
                          return_value={9010}) as mock_ports, \
             patch("win32process.GetWindowThreadProcessId", return_value=(0, 4321)):
            self.assertTrue(OSCClient.osc_available_for(0x1234, 1, None))

        mock_ports.assert_called_once_with(4321)


class TestVRChatLauncher(unittest.TestCase):
    """VRChat起動機構"""

    def test_build_launch_args_desktop(self):
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 2, desktop_mode=True)
        self.assertEqual(args, ["C:\\VRChat.exe", "--profile=2", "--no-vr"])

    def test_build_launch_args_vr_omits_no_vr(self):
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 0, desktop_mode=False)
        self.assertEqual(args, ["C:\\VRChat.exe", "--profile=0"])

    def test_build_launch_args_with_instance_link(self):
        link = "vrchat://launch?ref=vrchat.com&id=wrld_abc:1234"
        args = VRChatLauncher.build_launch_args(Path("C:/VRChat.exe"), 1, True, link)
        self.assertEqual(args[-1], link)

    def test_normalize_instance_link_accepts_raw_id(self):
        self.assertEqual(
            VRChatLauncher.normalize_instance_link("wrld_abc-123:4567~region(jp)"),
            "vrchat://launch?ref=vrchat.com&id=wrld_abc-123:4567~region(jp)",
        )

    def test_normalize_instance_link_accepts_vrchat_scheme(self):
        src = "vrchat://launch?ref=vrchat.com&id=wrld_abc:4567~private(usr_1)~region(jp)"
        self.assertEqual(VRChatLauncher.normalize_instance_link(src), src)

    def test_normalize_instance_link_accepts_web_url(self):
        src = "https://vrchat.com/home/launch?worldId=wrld_abc&instanceId=4567~region(jp)"
        self.assertEqual(
            VRChatLauncher.normalize_instance_link(src),
            "vrchat://launch?ref=vrchat.com&id=wrld_abc:4567~region(jp)",
        )

    def test_normalize_instance_link_rejects_garbage(self):
        for bad in ("", "   ", "https://example.com/", "just text"):
            with self.subTest(bad=bad):
                self.assertIsNone(VRChatLauncher.normalize_instance_link(bad))

    def test_instance_link_from_log_uses_latest_joining(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "output_log_test.txt"
            p.write_text(
                "2026.08.05 10:00:00 Log        -  [Behaviour] Joining wrld_old:1111~region(jp)\n"
                "2026.08.05 11:00:00 Log        -  [Behaviour] Joining wrld_new:2222~region(us)\n",
                encoding="utf-8")
            self.assertEqual(
                VRChatLauncher.instance_link_from_log(p),
                "vrchat://launch?ref=vrchat.com&id=wrld_new:2222~region(us)",
            )

    def test_instance_link_from_log_returns_none_without_joining(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "output_log_test.txt"
            p.write_text("no joining here\n", encoding="utf-8")
            self.assertIsNone(VRChatLauncher.instance_link_from_log(p))

    def test_resolve_vrchat_exe_prefers_manual_path(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "VRChat.exe"
            exe.write_text("x", encoding="utf-8")
            with patch.object(VRChatLauncher, "find_vrchat_exe") as mock_find:
                self.assertEqual(VRChatLauncher.resolve_vrchat_exe(str(exe)), exe)
            mock_find.assert_not_called()

    def test_resolve_vrchat_exe_falls_back_to_autodetect(self):
        with patch.object(VRChatLauncher, "find_vrchat_exe", return_value=Path("C:/auto/VRChat.exe")):
            self.assertEqual(VRChatLauncher.resolve_vrchat_exe(""), Path("C:/auto/VRChat.exe"))

    def test_resolve_vrchat_exe_returns_none_for_missing_manual(self):
        self.assertIsNone(VRChatLauncher.resolve_vrchat_exe("Z:/nope/VRChat.exe"))

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

    def test_resolve_rewrites_vrchat_exe_to_launcher(self):
        """手動でVRChat.exeを指定されてもlaunch.exeへ読み替える"""
        with tempfile.TemporaryDirectory() as d:
            install = Path(d)
            (install / "VRChat.exe").write_text("x", encoding="utf-8")
            (install / "launch.exe").write_text("x", encoding="utf-8")
            self.assertEqual(
                VRChatLauncher.resolve_vrchat_exe(str(install / "VRChat.exe")),
                install / "launch.exe")

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
    def test_find_latest_logs_returns_latest_in_oldest_to_newest_order(self):
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
                "output_log_2026-05-22_10-00-00.txt",
                "output_log_2026-05-23_10-00-00.txt",
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


class TestWindowTabHwndChoices(unittest.TestCase):
    def test_set_hwnd_choices_selects_requested_hwnd_without_discovery(self):
        class FakeVar:
            def __init__(self):
                self.value = "未選択"

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        class FakeCombo(dict):
            pass

        tab = type("FakeTab", (), {})()
        tab._hwnd_map = {}
        tab.cb_hwnd = FakeCombo()
        tab.v_hwnd_sel = FakeVar()

        with patch.object(VRChatDiscovery, "get_vrchat_windows") as mock_discover:
            mainGUI.WindowTab.set_hwnd_choices(tab, [0x1111, 0x2222], selected_hwnd=0x2222)

        mock_discover.assert_not_called()
        self.assertEqual(tab.cb_hwnd["values"], ["[1] HWND=0x00001111", "[2] HWND=0x00002222"])
        self.assertEqual(tab.v_hwnd_sel.get(), "[2] HWND=0x00002222")


class TestDetectInstanceTypeFromLog(unittest.TestCase):
    """ログ選択時のインスタンスタイプ検出（GUI用）"""

    PREFIX = "2026.07.13 10:00:00 Log        -  "

    def _write_log(self, tmpdir: str, lines: list[str]) -> Path:
        path = Path(tmpdir) / "output_log_test.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_detects_latest_joining_hoshiimo(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_log(d, [
                self.PREFIX + "[Behaviour] Joining wrld_aaa:11111~friends~region(jp)",
                self.PREFIX + "some other line",
                self.PREFIX + f"[Behaviour] Joining wrld_bbb:22222~group({config.HOSHIIMO_GROUP_ID})~groupAccessType(members)~region(jp)",
            ])
            self.assertEqual(
                LogMonitor.LogMonitor.detect_instance_type_from_log(path),
                config.INSTANCE_HOSHIIMO,
            )

    def test_detects_yakiimo(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_log(d, [
                self.PREFIX + f"[Behaviour] Joining wrld_bbb:22222~group({config.YAKIIMO_GROUP_ID})~groupAccessType(members)~region(jp)",
            ])
            self.assertEqual(
                LogMonitor.LogMonitor.detect_instance_type_from_log(path),
                config.INSTANCE_YAKIIMO,
            )

    def test_returns_none_without_joining_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_log(d, [self.PREFIX + "no joining here"])
            self.assertIsNone(LogMonitor.LogMonitor.detect_instance_type_from_log(path))

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(
            LogMonitor.LogMonitor.detect_instance_type_from_log(Path("Z:/no/such/log.txt"))
        )


class TestSettingsPersistence(unittest.TestCase):
    """前回tnlパスの保存・復元"""

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            settings_path = Path(d) / "sub" / "settings.json"
            with patch.object(config, "SETTINGS_PATH", settings_path):
                mainGUI.save_settings({"tnl_path": "C:/list/my.tnl"})
                self.assertEqual(mainGUI.load_settings().get("tnl_path"), "C:/list/my.tnl")

    def test_load_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(config, "SETTINGS_PATH", Path(d) / "none.json"):
                self.assertEqual(mainGUI.load_settings(), {})

    def test_load_broken_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            broken = Path(d) / "settings.json"
            broken.write_text("{not json", encoding="utf-8")
            with patch.object(config, "SETTINGS_PATH", broken):
                self.assertEqual(mainGUI.load_settings(), {})


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


class TestAppLogLimit(unittest.TestCase):
    def test_append_log_text_keeps_recent_lines_only(self):
        class FakeLogText:
            def __init__(self):
                self.lines = []
                self.state = None
                self.seen = None

            def config(self, **kwargs):
                self.state = kwargs.get("state", self.state)

            def insert(self, index, line):
                self.lines.append(line)

            def delete(self, start, end):
                end_line = int(end.split(".", 1)[0])
                del self.lines[:end_line - 1]

            def see(self, index):
                self.seen = index

        app = type("FakeApp", (), {})()
        app.log_text = FakeLogText()
        app._log_line_count = 0

        with patch.object(config, "GUI_LOG_MAX_LINES", 3):
            for i in range(5):
                mainGUI.App._append_log_text(app, f"line {i}\n")

        self.assertEqual(app.log_text.lines, ["line 2\n", "line 3\n", "line 4\n"])
        self.assertEqual(app._log_line_count, 3)
        self.assertEqual(app.log_text.seen, "end")
        self.assertEqual(app.log_text.state, "disabled")


class TestAppTabLifecycle(unittest.TestCase):
    def test_rebuild_tabs_destroys_old_tabs(self):
        class FakeNotebook:
            def __init__(self):
                self.forgot = []
                self.added = []

            def forget(self, tab):
                self.forgot.append(tab)

            def add(self, tab, text):
                self.added.append((tab, text))

        class OldTab:
            def __init__(self):
                self.destroyed = False

            def destroy(self):
                self.destroyed = True

        class NewTab:
            def __init__(self, parent, idx, on_log_selected=None):
                self.parent = parent
                self.idx = idx
                self.on_log_selected = on_log_selected
                self.destroyed = False

            def destroy(self):
                self.destroyed = True

        app = type("FakeApp", (), {})()
        app.nb = FakeNotebook()
        app._on_tab_log_selected = lambda tab: None
        app._apply_saved_window_settings = lambda: None
        app.tabs = [OldTab(), OldTab()]
        old_tabs = list(app.tabs)

        with patch.object(mainGUI, "WindowTab", NewTab):
            mainGUI.App._rebuild_tabs(app, 3)

        self.assertEqual(app.nb.forgot, old_tabs)
        self.assertTrue(all(tab.destroyed for tab in old_tabs))
        self.assertEqual(len(app.tabs), 3)
        self.assertEqual([tab.idx for tab in app.tabs], [0, 1, 2])
        self.assertEqual(len(app.nb.added), 3)

    def test_win_count_change_skips_rebuild_when_count_is_unchanged(self):
        class FakeVar:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        app = type("FakeApp", (), {})()
        app._running = False
        app.v_win_count = FakeVar(2)
        app.tabs = [object(), object()]
        app._rebuild_tabs = MagicMock()
        app._sync_launch_count = MagicMock()

        mainGUI.App._on_win_count_change(app)

        app._rebuild_tabs.assert_not_called()
        app._sync_launch_count.assert_called_once()


class TestGroupRoundTable(unittest.TestCase):
    """第1部: 干し芋/焼き芋のラウンド判定表"""

    HOSHIIMO = config.INSTANCE_HOSHIIMO
    YAKIIMO = config.INSTANCE_YAKIIMO
    SONIC = 40

    def _decide(self, instance_type, round_type, terror_ids=(1,), **kw):
        return GroupRound.decide(instance_type, round_type, list(terror_ids), **kw)

    def _both(self, round_type, terror_ids=(1,), **kw):
        return {self._decide(self.HOSHIIMO, round_type, terror_ids, **kw),
                self._decide(self.YAKIIMO, round_type, terror_ids, **kw)}

    def test_classic_without_a_variant_is_skipped(self):
        self.assertEqual(self._both("Classic", [self.SONIC]), {GroupRound.SKIP})

    def test_classic_with_each_variant_falls_through(self):
        for tid in (190, 191, 192, 314):
            self.assertEqual(self._both("Classic", [tid]), {GroupRound.NORMAL}, tid)

    def test_bloodbath_is_skipped(self):
        self.assertEqual(self._both("Bloodbath"), {GroupRound.SKIP})

    def test_classic_exe_and_randomizer_are_skipped_even_with_a_variant(self):
        """Variant例外なし"""
        for round_type in ("Classic.exe", "Randomizer"):
            self.assertEqual(self._both(round_type, [192]), {GroupRound.SKIP},
                             round_type)

    def test_double_trouble_and_bloodbath_ex_use_the_normal_judgement(self):
        for round_type in ("Double Trouble", "Bloodbath EX"):
            self.assertEqual(self._both(round_type), {GroupRound.NORMAL}, round_type)

    def test_eight_pages_and_run_always_continue(self):
        for round_type in ("8 Pages", "Run"):
            self.assertEqual(self._both(round_type), {GroupRound.CONTINUE}, round_type)

    def test_hoshiimo_fog_always_continues(self):
        self.assertEqual(self._decide(self.HOSHIIMO, "Fog", [7]),
                         GroupRound.CONTINUE)

    def test_yakiimo_fog_revealed_as_alternate_is_judged(self):
        alternate = MatchTNL.ALTERNATE_OFFSET + 5

        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog", [alternate],
                         killers_round_type="Fog (Alternate)"),
            GroupRound.NORMAL)

    def test_yakiimo_fog_revealed_as_plain_fog_is_skipped(self):
        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog", [7], killers_round_type="Fog"),
            GroupRound.SKIP)

    def test_an_unknown_killers_round_type_does_not_skip(self):
        """判定材料が無いときは自爆しない側へ倒す（通常の経路では来ない）"""
        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog", [7], killers_round_type=""),
            GroupRound.CONTINUE)

    def test_yakiimo_fog_updated_by_foxy_is_still_the_fog_row(self):
        """Foxy検出で st.round_type が Fog (Alternate) に更新されることがある"""
        self.assertEqual(
            self._decide(self.YAKIIMO, "Fog (Alternate)", [140],
                         killers_round_type="Fog (Alternate)"),
            GroupRound.NORMAL)
        self.assertEqual(
            self._decide(self.HOSHIIMO, "Fog (Alternate)", [140]),
            GroupRound.CONTINUE)

    def test_a_repeated_moon_is_skipped(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._both(moon, moon_repeat=True),
                             {GroupRound.SKIP}, moon)

    def test_yakiimo_skips_the_first_mystic_moon_and_solstice(self):
        for moon in ("Mystic Moon", "Solstice"):
            self.assertEqual(self._decide(self.YAKIIMO, moon), GroupRound.SKIP, moon)

    def test_yakiimo_plays_the_first_blood_moon_and_twilight(self):
        for moon in ("Blood Moon", "Twilight"):
            self.assertEqual(self._decide(self.YAKIIMO, moon),
                             GroupRound.CONTINUE, moon)

    def test_hoshiimo_plays_every_first_moon(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon),
                             GroupRound.CONTINUE, moon)

    def test_anything_else_uses_the_normal_judgement(self):
        for round_type in ("Midnight", "Punished", "Cracked", "Ghost", "Unbound"):
            self.assertEqual(self._both(round_type), {GroupRound.NORMAL}, round_type)

    def test_private_is_never_touched(self):
        for round_type in ("Classic", "Bloodbath", "8 Pages", "Fog", "Sabotage"):
            self.assertEqual(
                self._decide(config.INSTANCE_PRIVATE, round_type),
                GroupRound.NORMAL, round_type)

    def test_other_instances_are_never_touched(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_CBPS,
                      config.INSTANCE_OTHER_GROUP):
            self.assertEqual(self._decide(itype, "Classic"),
                             GroupRound.NORMAL, itype)


class TestGroupRoundSabotage(unittest.TestCase):
    """第2部: Sabotage の選出者判定"""

    STAR = GroupRound.SABOTAGE_STAR_KEY
    MURDER = GroupRound.SABOTAGE_MURDER_KEY

    def _decide(self, instance_type, terror_ids=(5,), sus=(), wishes=None,
                follow_host=True):
        return GroupRound.decide(
            instance_type, "Sabotage", list(terror_ids),
            sus_players=list(sus),
            host_wishes=wishes if wishes is not None else {},
            follow_host=follow_host,
        )

    def test_a_murderer_missing_from_the_list_has_no_wish(self):
        """実測では28人中7人が未掲載。珍しくない"""
        wishes = {"ほかのひと": {self.STAR: {99}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["のってないひと"],
                         wishes=wishes),
            GroupRound.SKIP)

    def test_yakiimo_skips_when_a_murderer_wants_the_murder_slot(self):
        wishes = {"ソノア7": {self.MURDER: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.SKIP)

    def test_one_of_two_murderers_is_enough(self):
        wishes = {"ソノア7": {self.MURDER: {99}},
                  "ユウナ2858": {self.MURDER: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO,
                         sus=["ソノア7", "ユウナ2858"], wishes=wishes),
            GroupRound.SKIP)

    def test_a_non_murderer_wanting_the_star_slot_continues(self):
        wishes = {"ソノア7": {self.MURDER: {99}},
                  "みているひと": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.CONTINUE)

    def test_the_murderers_own_star_wish_does_not_count(self):
        wishes = {"ソノア7": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.SKIP)

    def test_hoshiimo_ignores_the_murder_slot(self):
        """干し芋はマーダー側の判定をしない"""
        wishes = {"ソノア7": {self.MURDER: {5}},
                  "みているひと": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_HOSHIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.CONTINUE, "マーダー希望があっても star で続行になること")

    def test_hoshiimo_skips_without_a_star_wish(self):
        wishes = {"みているひと": {self.STAR: {99}}}

        self.assertEqual(
            self._decide(config.INSTANCE_HOSHIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.SKIP)

    def test_following_off_falls_back_to_the_normal_judgement(self):
        """追従OFFでは誰の希望か分からない。従来どおり keepOn_set で判定する"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            self.assertEqual(
                self._decide(itype, sus=["ソノア7"], wishes={}, follow_host=False),
                GroupRound.NORMAL, itype)

    def test_following_on_but_empty_list_also_falls_back(self):
        """0人のときは前のリストを保持しているので、希望が空なら判断できない"""
        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes={}),
            GroupRound.NORMAL)


class TestRoundSequence(unittest.TestCase):
    """ラウンド並び(N/S)の推定と moon の解放判定"""

    def _seq(self, *round_types):
        seq = RoundSequence.RoundSequence()
        for round_type in round_types:
            seq.on_round(round_type)
        return seq

    def _label(self, seq, index):
        return seq.labels()[index][1]

    def test_ghost_is_normal_when_followed_by_a_special(self):
        """Classic → Ghost → Midnight。Midnight(S)はN連が要るのでGhostはN"""
        seq = self._seq("Classic", "Ghost", "Midnight")

        self.assertEqual(self._label(seq, 1), "N")

    def test_ghost_is_special_when_followed_by_a_normal(self):
        """Classic → Ghost → Run。GhostもNだとN連3になるのでGhostはS"""
        seq = self._seq("Classic", "Ghost", "Run")

        self.assertEqual(self._label(seq, 1), "S")

    def test_two_overrides_in_a_row_are_resolved(self):
        seq = self._seq("Classic", "Unbound", "Ghost", "Bloodbath")

        self.assertEqual(self._label(seq, 1), "S", "Unbound")
        self.assertEqual(self._label(seq, 2), "N", "Ghost")

    def test_master_switch_allows_a_special_after_a_special(self):
        """通常なら S は連続しない。master切替の次だけ許す"""
        seq = self._seq("Classic", "Ghost", "Midnight")   # Midnight で run=0
        seq.on_master_switched()

        seq.on_round("Bloodbath")

        self.assertEqual(self._label(seq, 3), "S")
        self.assertTrue(seq._hyps, "矛盾で仮説が空になっていないこと")

    def test_master_switch_applies_only_to_the_next_round(self):
        seq = self._seq("Classic", "Ghost", "Midnight")
        seq.on_master_switched()
        seq.on_round("Bloodbath")

        self.assertFalse(seq.force_special, "1ラウンドで使い切ること")

    def test_classics_may_repeat_before_the_first_special(self):
        """3クラ未解放の間は Classic しか来ない。N連の上限を当てはめない"""
        seq = self._seq(*["Classic"] * 6)

        self.assertTrue(seq._hyps, "矛盾扱いにしないこと")
        self.assertFalse(seq.special_seen)
        self.assertEqual([lab for _t, lab in seq.labels()], ["N"] * 6)

    def test_first_moon_can_be_either_but_a_repeat_is_special(self):
        first = RoundSequence.RoundSequence()
        self.assertEqual(
            first._candidate_labels("Mystic Moon", False, True), ("N", "S"))

        repeat = RoundSequence.RoundSequence()
        repeat.moon_done["Mystic Moon"] = True
        self.assertEqual(
            repeat._candidate_labels("Mystic Moon", False, False), ("S",))

    def test_a_moon_flags_only_itself(self):
        seq = self._seq("Classic", "Mystic Moon")

        self.assertTrue(seq.is_moon_repeat("Mystic Moon"))
        for other in ("Blood Moon", "Twilight", "Solstice"):
            self.assertFalse(seq.is_moon_repeat(other), other)

    def test_the_flag_is_set_even_if_the_round_is_skipped(self):
        """焼き芋は1回目の Mystic Moon をスキップするが、出た事実は変わらない"""
        seq = RoundSequence.RoundSequence()
        seq.on_round("Classic")

        was_repeat = seq.is_moon_repeat("Mystic Moon")
        seq.on_round("Mystic Moon")     # スキップしても on_round は通す

        self.assertFalse(was_repeat, "判定時点では1回目")
        self.assertTrue(seq.is_moon_repeat("Mystic Moon"))

    def test_alternate_confirmed_normal_unlocks_all_four(self):
        seq = self._seq("Classic", "Alternate", "Midnight")

        self.assertEqual(self._label(seq, 1), "N")
        self.assertTrue(seq.moons_unlocked())

    def test_alternate_confirmed_special_unlocks_nothing(self):
        """直前にNが2つ続いていれば Alternate は S しか取れない"""
        seq = self._seq("Classic", "Ghost", "Bloodbath", "Classic", "Classic",
                        "Alternate")

        self.assertEqual(self._label(seq, 5), "S")
        self.assertFalse(seq.moons_unlocked())

    def test_an_undetermined_alternate_does_not_unlock(self):
        """未確定のうちは経路Bを発火させない（未解放扱い）"""
        seq = self._seq("Alternate", "Classic")

        self.assertEqual(self._label(seq, 0), "", "まだ決まらないこと")
        self.assertFalse(seq.moons_unlocked())

    def test_a_later_round_can_unlock_retroactively(self):
        seq = self._seq("Alternate")
        self.assertFalse(seq.moons_unlocked())

        seq.on_round("Midnight")

        self.assertEqual(self._label(seq, 0), "N")
        self.assertTrue(seq.moons_unlocked(), "確定した時点で4種立てること")

    def test_reset_clears_everything(self):
        seq = self._seq("Classic", "Mystic Moon", "Alternate", "Midnight")
        self.assertTrue(seq.moons_unlocked())

        seq.reset()

        self.assertFalse(any(seq.moon_done.values()))
        self.assertEqual(seq.labels(), [])
        self.assertFalse(seq.special_seen)
        self.assertEqual(seq._hyps, {(run, ()) for run in range(3)})

    def test_a_contradiction_falls_back_to_unknown(self):
        """前提が崩れても以後の判定を殺さない（仮説を空のままにしない）"""
        seq = RoundSequence.RoundSequence()
        seq.special_seen = True
        seq._hyps = {(2, ())}          # N連が上限。次にNは来られない

        seq.on_round("Classic")

        self.assertEqual(seq._hyps, {(run, ()) for run in range(3)})


class TestRoundSequenceWiring(unittest.TestCase):
    """LogMonitor 側の配線（ラウンド並びの前進とリセット）"""

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        return monitor

    def _round_start(self, monitor, round_type):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("This round is taking place at Facility (12) "
                             f"and the round type is {round_type}")

    def test_round_start_advances_the_sequence(self):
        monitor = self._monitor()

        self._round_start(monitor, "Classic")

        self.assertEqual(monitor.sequence.labels(), [("Classic", "N")])

    def test_moon_repeat_is_decided_before_the_flag_is_set(self):
        """1回目は moon_repeat=False、同じmoonの2回目で True"""
        monitor = self._monitor()

        self._round_start(monitor, "Classic")
        self._round_start(monitor, "Mystic Moon")
        first = monitor.st.moon_repeat
        self._round_start(monitor, "Mystic Moon")

        self.assertFalse(first, "出た本人のラウンドは1回目")
        self.assertTrue(monitor.st.moon_repeat)

    def test_master_switch_line_is_wired(self):
        monitor = self._monitor()

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("2026.09.11 23:50:50 Debug      -  "
                             "[Behaviour] OnMasterClientSwitched")

        self.assertTrue(monitor.sequence.force_special)

    def test_joining_resets_the_sequence(self):
        monitor = self._monitor()
        self._round_start(monitor, "Classic")
        self._round_start(monitor, "Mystic Moon")
        self.assertTrue(monitor.sequence.is_moon_repeat("Mystic Moon"))

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("2026.09.05 14:00:00 Debug      -  [Behaviour] "
                             "Joining wrld_1234:5678~group(grp_x)")

        self.assertFalse(monitor.sequence.is_moon_repeat("Mystic Moon"))
        self.assertEqual(monitor.sequence.labels(), [])


class TestSusPlayers(unittest.TestCase):
    """Sabotage の選出者を貯める（リセットは Verified Round End）"""

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        monitor.st.local_player_name = "わたし"
        return monitor

    def _feed(self, monitor, line):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process(line)

    def test_one_murderer_is_collected(self):
        monitor = self._monitor()

        self._feed(monitor, "Sus player = 3 ソノア7")

        self.assertEqual(monitor.st.sus_players, ["ソノア7"])

    def test_two_murderers_are_collected(self):
        monitor = self._monitor()

        self._feed(monitor, "Sus player = 3 ソノア7")
        self._feed(monitor, "Sus player 2 = 0 ユウナ2858")

        self.assertEqual(monitor.st.sus_players, ["ソノア7", "ユウナ2858"])

    def test_the_same_name_is_not_doubled(self):
        monitor = self._monitor()

        self._feed(monitor, "Sus player = 3 ソノア7")
        self._feed(monitor, "Sus player = 3 ソノア7")

        self.assertEqual(monitor.st.sus_players, ["ソノア7"])

    def test_round_start_does_not_clear_them(self):
        """ROUND_START は Sus player と同じ秒に来る。ここで消すと選出者が消える"""
        monitor = self._monitor()
        self._feed(monitor, "Sus player = 3 ソノア7")

        self._feed(monitor, "This round is taking place at Facility (12) "
                            "and the round type is Sabotage")

        self.assertEqual(monitor.st.sus_players, ["ソノア7"])

    def test_verified_round_end_clears_them(self):
        monitor = self._monitor()
        self._feed(monitor, "Sus player = 3 ソノア7")

        self._feed(monitor, "Verified Round End")

        self.assertEqual(monitor.st.sus_players, [])

    def test_the_existing_self_check_still_works(self):
        """自分がマーダーかの記録（アイテムロスト判定用）は残すこと"""
        monitor = self._monitor()
        monitor.st.in_round = True
        monitor.st.round_type = "Sabotage"

        self._feed(monitor, "Sus player = 3 わたし")

        self.assertTrue(monitor.st.sabotage_murder_this_round)
        self.assertEqual(monitor.st.sus_players, ["わたし"])


class TestHostSaveWishes(unittest.TestCase):
    """参加者別の続行希望（Sabotage のマーダー判定に使う）"""

    CLASSIC = "Classic/クラシック"
    MURDER = "Sabotage murder/サボタージュマーダー"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()

    def _write(self, raw):
        path = Path(self._dir.name) / "host_save.json.gz"
        with gzip.open(str(path), "wb") as f:
            f.write(json.dumps(raw).encode("utf-8"))
        return str(path)

    def _member(self, name, data):
        return {"vrc_name": name, "data": data}

    def test_wishes_are_kept_per_participant(self):
        path = self._write({"version": 5, "tabs": [{"participants": [
            self._member("ソノア7", {self.CLASSIC: {"1": 1}}),
            self._member("ユウナ2858", {self.MURDER: {"5": 1}})]}]})

        keep_on, _meta, wishes = MatchTNL.load_host_save(path)

        self.assertEqual(wishes["ソノア7"], {self.CLASSIC: {1}})
        self.assertEqual(wishes["ユウナ2858"], {self.MURDER: {5}})
        self.assertEqual(keep_on, {self.CLASSIC: {1}, self.MURDER: {5}},
                         "畳んだ方はこれまでどおり")

    def test_waiting_players_are_not_listed(self):
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member("ソノア7", {self.CLASSIC: {"1": 1}})],
            "waiting": [self._member("まちびと", {self.CLASSIC: {"9": 1}})]}]})

        _keep_on, _meta, wishes = MatchTNL.load_host_save(path)

        self.assertEqual(list(wishes), ["ソノア7"])

    def test_the_same_name_in_two_tabs_is_merged(self):
        path = self._write({"version": 5, "tabs": [
            {"participants": [self._member("ソノア7", {self.CLASSIC: {"1": 1}})]},
            {"participants": [self._member("ソノア7", {self.CLASSIC: {"2": 1}})]}]})

        _keep_on, _meta, wishes = MatchTNL.load_host_save(path)

        self.assertEqual(wishes["ソノア7"], {self.CLASSIC: {1, 2}})

    def test_names_with_unusual_characters_are_kept_as_is(self):
        """完全一致で引く。正規化しない（実測で一致することを確認済み）"""
        names = ["Dynamic_Naël", "Miyα", "えだまめ-Salt", "あんてな〜"]
        path = self._write({"version": 5, "tabs": [{"participants": [
            self._member(n, {self.CLASSIC: {"1": 1}}) for n in names]}]})

        _keep_on, _meta, wishes = MatchTNL.load_host_save(path)

        self.assertEqual(sorted(wishes), sorted(names))

    def test_apply_host_wishes_updates_in_place(self):
        app = type("FakeApp", (), {})()
        app.host_wishes = {"だれか": {self.CLASSIC: {1}}}
        before = app.host_wishes

        mainGUI.App._apply_host_wishes(app, {"べつのひと": {self.MURDER: {5}}})

        self.assertIs(app.host_wishes, before, "同じ dict のままにすること")
        self.assertEqual(app.host_wishes, {"べつのひと": {self.MURDER: {5}}})


class TestLoadHostSave(unittest.TestCase):
    """ToN ListTool の主催リストを keepOn_set の形で読む"""

    CLASSIC = "Classic/クラシック"
    FOG = "Fog/霧"
    FOG_ALT = "Fog (Alternate)/霧 (Alternate)"

    def _member(self, data):
        return {"vrc_name": "someone", "original_name": "someone",
                "data": data, "memo": "", "created_at": "", "is_visible": False}

    def _write(self, raw):
        path = Path(self._dir.name) / "host_save.json.gz"
        with gzip.open(str(path), "wb") as f:
            f.write(json.dumps(raw).encode("utf-8"))
        return str(path)

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()

    def test_two_participants_are_ored(self):
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 1, "2": 0}}),
                             self._member({self.CLASSIC: {"3": 1}})]}]})

        keep_on, meta, _wishes = MatchTNL.load_host_save(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1, 3}})
        self.assertEqual(meta["participants"], 2)

    def test_waiting_is_not_included(self):
        """待機列はその場にいない。続行判定に混ぜない"""
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 1}})],
            "waiting": [self._member({self.CLASSIC: {"99": 1}})]}]})

        keep_on, meta, _wishes = MatchTNL.load_host_save(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1}})
        self.assertEqual(meta["participants"], 1, "waiting は人数にも数えない")

    def test_all_tabs_are_folded_regardless_of_active_tab(self):
        path = self._write({"version": 5, "active_tab": 2, "tabs": [
            {"participants": [self._member({self.CLASSIC: {"1": 1}})]},
            {"participants": [self._member({self.CLASSIC: {"2": 1}})]},
            {"participants": [self._member({self.FOG: {"7": 1}})]}]})

        keep_on, meta, _wishes = MatchTNL.load_host_save(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1, 2}, self.FOG: {7}})
        self.assertEqual((meta["participants"], meta["tabs"]), (3, 3))

    def test_zero_slots_are_dropped(self):
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 0, "2": 0},
                                           self.FOG: {"5": 2}})]}]})

        keep_on, _meta, _wishes = MatchTNL.load_host_save(path)

        self.assertEqual(keep_on, {self.FOG: {5}}, "全部0のラウンドキーは残さない")

    def test_alternate_fog_key_is_ignored(self):
        """host_save 側にだけあるキー。既存は LOG_TO_TNL で通常Fogに寄せている"""
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.FOG_ALT: {"1": 1},
                                           self.CLASSIC: {"2": 1}})]}]})

        keep_on, _meta, _wishes = MatchTNL.load_host_save(path)

        self.assertEqual(keep_on, {self.CLASSIC: {2}})
        self.assertNotIn(1, keep_on.get(self.FOG, set()), "通常Fogへ畳まないこと")

    def test_broken_gzip_raises(self):
        """呼び出し側が握って前の値を保持する。ここでは握り潰さない"""
        path = Path(self._dir.name) / "broken.json.gz"
        path.write_bytes(b"not a gzip file at all")

        with self.assertRaises(Exception):
            MatchTNL.load_host_save(str(path))

    def test_missing_tabs_gives_an_empty_set(self):
        for raw in ({"version": 5},
                    {"version": 5, "tabs": []},
                    {"version": 5, "tabs": [{"participants": []}]}):
            keep_on, meta, _wishes = MatchTNL.load_host_save(self._write(raw))

            self.assertEqual(keep_on, {}, raw)
            self.assertEqual(meta["participants"], 0, raw)

    def test_unknown_version_is_still_read(self):
        """あちらのバージョンが上がっても、読める形なら読む"""
        path = self._write({"version": 99, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 1}})]}]})

        keep_on, _meta, _wishes = MatchTNL.load_host_save(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1}})


class TestApplyKeepOn(unittest.TestCase):
    """続行リストの差し替えは in-place（LogMonitor が同じ dict を掴んでいる）"""

    def _app(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = {"Classic/クラシック": {1}}
        return app

    def test_updates_in_place(self):
        app = self._app()
        before = app.keepOn_set

        mainGUI.App._apply_keep_on(app, {"Fog/霧": {7}})

        self.assertIs(app.keepOn_set, before, "同じ dict オブジェクトのままにすること")
        self.assertEqual(app.keepOn_set, {"Fog/霧": {7}})

    def test_running_monitor_sees_the_new_list(self):
        app = self._app()
        monitor = LogMonitor.LogMonitor(WindowConfig(), app.keepOn_set,
                                        lambda _m: None, window_idx=1)

        mainGUI.App._apply_keep_on(app, {"Fog/霧": {7}})

        self.assertEqual(monitor.keepOn_set, {"Fog/霧": {7}},
                         "走行中のモニタにも効くこと")


class TestProcessCheck(unittest.TestCase):
    """プロセスの生死。実プロセスには依存させない（kernel32 を差し替える）"""

    def _kernel32(self, names, snapshot=1234):
        """指定した名前のプロセスが並んでいる ToolHelp スナップショットを装う"""
        state = {"i": 0}
        k = MagicMock()
        k.CreateToolhelp32Snapshot.return_value = snapshot

        def fill(_snap, ref):
            entry = ref._obj
            if state["i"] >= len(names):
                return 0
            entry.szExeFile = names[state["i"]]
            state["i"] += 1
            return 1

        k.Process32FirstW.side_effect = fill
        k.Process32NextW.side_effect = fill
        return k

    def _running(self, k, name="ton_listtool.exe"):
        with patch.object(ProcessCheck, "kernel32", k):
            return ProcessCheck.is_process_running(name)

    def test_a_listed_process_is_found(self):
        k = self._kernel32(["explorer.exe", "ToN_ListTool.exe", "VRChat.exe"])

        self.assertTrue(self._running(k))

    def test_the_comparison_is_case_insensitive(self):
        """実物は ToN_ListTool.exe。小文字化して完全一致で見る"""
        k = self._kernel32(["TON_LISTTOOL.EXE"])

        self.assertTrue(self._running(k))

    def test_a_partial_name_does_not_match(self):
        k = self._kernel32(["ton_listtool_updater.exe", "ton-gui.exe"])

        self.assertFalse(self._running(k))

    def test_an_absent_process_is_not_found(self):
        k = self._kernel32(["explorer.exe", "VRChat.exe"])

        self.assertFalse(self._running(k))

    def test_a_failed_snapshot_is_false(self):
        """True に倒すと、呼び出し側が古いファイルを読む側へ倒れる"""
        k = self._kernel32([], snapshot=0)

        self.assertFalse(self._running(k))

    def test_an_invalid_handle_is_false(self):
        k = self._kernel32([], snapshot=ProcessCheck.INVALID_HANDLE_VALUE)

        self.assertFalse(self._running(k))

    def test_an_exception_is_false(self):
        k = MagicMock()
        k.CreateToolhelp32Snapshot.side_effect = OSError("boom")

        self.assertFalse(self._running(k))

    def test_an_empty_name_never_matches(self):
        k = self._kernel32(["explorer.exe"])

        self.assertFalse(self._running(k, name=""))
        k.CreateToolhelp32Snapshot.assert_not_called()

    def test_the_snapshot_is_closed(self):
        k = self._kernel32(["ToN_ListTool.exe"])

        self._running(k)

        k.CloseHandle.assert_called_once_with(1234)

    def test_the_snapshot_is_closed_even_on_error(self):
        k = self._kernel32(["ToN_ListTool.exe"])
        k.Process32FirstW.side_effect = OSError("boom")

        self.assertFalse(self._running(k))
        k.CloseHandle.assert_called_once_with(1234)


class TestHostListSource(unittest.TestCase):
    """続行リストの供給元を状況から決める（チェックボックスは無い）

    ToN ListTool が動いていて参加者がいれば主催リスト、それ以外は .tnl。
    ツールを閉じてもファイルは残るので、鮮度はプロセスの生死で見る。
    """

    CLASSIC = "Classic/クラシック"

    def setUp(self):
        SharedState.set_list_source(None)
        self._dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self._dir.name) / "host_save.json.gz")
        self.tnl = Path(self._dir.name) / "list.tnl"
        self.tnl.write_text(json.dumps(
            {"list_name": "L", "creator": "", "created_at": "",
             "data": {self.CLASSIC: {"1": 1}}}), encoding="utf-8")

    def tearDown(self):
        SharedState.set_list_source(None)
        self._dir.cleanup()

    class FakeVar:
        def __init__(self, value):
            self._v = value

        def get(self):
            return self._v

    def _app(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.host_wishes = {}
        app._host_save_stamp = None
        app._host_save_warned = False
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app.v_tnl = TestHostListSource.FakeVar(str(self.tnl))
        for name in ("_apply_keep_on", "_apply_host_wishes",
                     "_warn_host_save_once", "_fall_back_to_tnl", "_load_tnl"):
            setattr(app, name, self._bind(app, name))
        return app

    @staticmethod
    def _bind(app, name):
        method = getattr(mainGUI.App, name)
        return lambda *a, **kw: method(app, *a, **kw)

    def _write(self, participants):
        members = [{"vrc_name": f"ひと{n}", "data": {self.CLASSIC: {str(n + 5): 1}}}
                   for n in range(participants)]
        with gzip.open(self.path, "wb") as f:
            f.write(json.dumps({"version": 5,
                                "tabs": [{"participants": members}]}).encode("utf-8"))

    def _refresh(self, app, running=True):
        with patch.object(config, "HOST_SAVE_PATH", self.path), \
             patch.object(ProcessCheck, "is_process_running", return_value=running), \
             patch.object(mainGUI, "save_settings"), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._refresh_host_source(app)

    def _switch_logs(self, app):
        return [m for m in app.logs if "[続行リスト]" in m]

    # ── 供給元の選択 ──────────────────────────
    def test_a_closed_list_tool_uses_the_tnl(self):
        self._write(3)
        app = self._app()

        with patch.object(MatchTNL, "load_host_save") as mock_load:
            self._refresh(app, running=False)

        mock_load.assert_not_called()
        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {1}})

    def test_a_running_list_tool_with_participants_uses_the_host_list(self):
        self._write(3)
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6, 7}})
        self.assertEqual(len(app.host_wishes), 3)

    def test_zero_participants_falls_back_to_the_tnl(self):
        self._write(0)
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {1}})

    def test_closing_the_list_tool_returns_to_the_tnl(self):
        self._write(3)
        app = self._app()
        self._refresh(app)
        self.assertEqual(SharedState.get_list_source(), "host")

        self._refresh(app, running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {1}})

    def test_starting_a_lap_switches_to_the_host_list(self):
        self._write(0)
        app = self._app()
        self._refresh(app)
        self.assertEqual(SharedState.get_list_source(), "tnl")

        self._write(2)
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6}})

    def test_a_missing_file_falls_back_to_the_tnl(self):
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")

    # ── ログ ────────────────────────────────
    def test_the_same_source_logs_once(self):
        self._write(3)
        app = self._app()

        for _ in range(3):
            self._refresh(app)

        self.assertEqual(len(self._switch_logs(app)), 1, app.logs)

    def test_every_switch_is_logged(self):
        self._write(3)
        app = self._app()

        self._refresh(app)                 # → host
        self._refresh(app, running=False)  # → tnl
        self._refresh(app)                 # → host

        self.assertEqual(len(self._switch_logs(app)), 3, app.logs)

    def test_the_reason_is_in_the_message(self):
        self._write(0)
        app = self._app()
        self._refresh(app)
        self._write(3)
        self._refresh(app)
        self._refresh(app, running=False)

        joined = "\n".join(self._switch_logs(app))
        self.assertIn("周回の参加者がいません", joined)
        self.assertIn("主催リストへ切替（参加者3人）", joined)
        self.assertIn("ToN ListTool が起動していません", joined)

    def test_a_content_change_is_still_logged(self):
        self._write(2)
        app = self._app()
        self._refresh(app)

        self._write(4)
        self._refresh(app)

        hits = [m for m in app.logs if "続行対象" in m]
        self.assertEqual(len(hits), 2, app.logs)

    # ── 一時的な失敗は供給元を変えない ──────────
    def test_a_decode_failure_keeps_the_current_source(self):
        """書き込み中を掴みうる。ここで tnl へ倒すと3秒ごとに往復する"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        Path(self.path).write_bytes(b"half written garbage")
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host", "供給元を変えないこと")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6, 7}}, "前の値を保持")

    def test_a_repeated_failure_warns_once(self):
        self._write(3)
        app = self._app()
        self._refresh(app)
        Path(self.path).write_bytes(b"half written garbage")

        for _ in range(3):
            self._refresh(app)

        hits = [m for m in app.logs if "読み込み失敗" in m]
        self.assertEqual(len(hits), 1, app.logs)

    def test_an_unchanged_file_is_not_reread(self):
        self._write(3)
        app = self._app()
        self._refresh(app)

        with patch.object(MatchTNL, "load_host_save") as mock_load:
            self._refresh(app)

        mock_load.assert_not_called()

    def test_a_returning_source_rereads_even_if_unchanged(self):
        """tnl を経由して戻ってきたら、同じファイルでも読み直すこと"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        self._refresh(app, running=False)
        app.keepOn_set.clear()

        self._refresh(app)

        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6, 7}})

    def test_a_rewrite_of_the_same_size_is_picked_up(self):
        """(mtime, size) の組で見ている理由。片方だけ変わっても拾うこと"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        mtime, size = app._host_save_stamp

        # サイズは同じで mtime だけ違う（同じ秒内の書き換え相当）
        app._host_save_stamp = (mtime - 1, size)
        with patch.object(MatchTNL, "load_host_save",
                          return_value=({"x": {1}}, {"participants": 1, "tabs": 1},
                                        {})) as mock_load:
            self._refresh(app)
        mock_load.assert_called_once()

        # mtime は同じでサイズだけ違う
        app._host_save_stamp = (app._host_save_stamp[0], size - 1)
        with patch.object(MatchTNL, "load_host_save",
                          return_value=({"y": {2}}, {"participants": 1, "tabs": 1},
                                        {})) as mock_load:
            self._refresh(app)
        mock_load.assert_called_once()

    def test_recovery_after_a_failure_is_logged_again(self):
        """失敗中は1回だけ。成功で復帰して、また失敗したらまた1回出る"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        Path(self.path).write_bytes(b"half written garbage")
        self._refresh(app)
        self._refresh(app)
        self._write(4)
        self._refresh(app)                      # 復帰
        Path(self.path).write_bytes(b"broken again")
        self._refresh(app)

        hits = [m for m in app.logs if "読み込み失敗" in m]
        self.assertEqual(len(hits), 2, app.logs)

    def test_the_tick_survives_a_read_failure(self):
        """プロセス判定ではなく、読み込み側が投げても tick が止まらないこと"""
        self._write(3)
        app = self._app()
        app.after = MagicMock()
        app._poll_host_save = lambda: None
        app._refresh_host_source = lambda: mainGUI.App._refresh_host_source(app)

        with patch.object(config, "HOST_SAVE_PATH", self.path), \
             patch.object(ProcessCheck, "is_process_running", return_value=True), \
             patch.object(mainGUI.os, "stat", side_effect=RuntimeError("boom")):
            mainGUI.App._poll_host_save(app)

        app.after.assert_called_once()

    def test_a_stat_failure_falls_back_to_the_tnl(self):
        """OSError 以外で落ちても供給元だけは決まること"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        with patch.object(config, "HOST_SAVE_PATH", self.path), \
             patch.object(ProcessCheck, "is_process_running", return_value=True), \
             patch.object(mainGUI.os, "stat", side_effect=OSError("gone")), \
             patch.object(mainGUI, "save_settings"), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._refresh_host_source(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")

    # ── 参加者別の希望 ────────────────────────
    def test_switching_to_the_tnl_clears_the_wishes(self):
        """古い希望が残ると Sabotage の判定に効いてしまう"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        self.assertTrue(app.host_wishes)

        self._refresh(app, running=False)

        self.assertEqual(app.host_wishes, {})

    def test_switching_to_a_missing_tnl_clears_the_host_list(self):
        """tnl未設定のまま ListTool が落ちても、古い主催リストを残さない"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        app.v_tnl = TestHostListSource.FakeVar("")      # tnl 未設定

        self._refresh(app, running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {}, "続行0件として動く")
        self.assertEqual(app.host_wishes, {})

    # ── tick ────────────────────────────────
    def test_the_tick_survives_a_process_check_failure(self):
        """供給元の判定が投げても、次の tick が予約されること"""
        app = self._app()
        app.after = MagicMock()
        app._poll_host_save = lambda: None

        def boom():
            raise OSError("boom")
        app._refresh_host_source = boom

        mainGUI.App._poll_host_save(app)

        app.after.assert_called_once()

    def test_the_tick_needs_no_switch_to_run(self):
        """チェックボックスは無い。常に供給元を見に行く"""
        app = self._app()
        app.after = MagicMock()
        app._poll_host_save = lambda: None
        called = []
        app._refresh_host_source = lambda: called.append(1)

        mainGUI.App._poll_host_save(app)

        self.assertEqual(len(called), 1)
        app.after.assert_called_once()


class TestWindowTabAlwaysActive(unittest.TestCase):
    """「この窓を有効化」を廃止した。窓タブの窓はすべて対象になる"""

    def _source(self):
        return Path("mainGUI.py").read_text(encoding="utf-8")

    def test_the_checkbox_and_its_variable_are_gone(self):
        self.assertNotIn("v_active", self._source())

    def test_a_tab_without_a_log_reports_the_error(self):
        """以前は無効化して (None, None) で黙って飛ばせた。その経路が消えたこと"""
        tab = type("FakeTab", (), {})()
        tab.v_log = TestHostListSource.FakeVar("   ")

        cfg, err = mainGUI.WindowTab.get_config(tab)

        self.assertIsNone(cfg)
        self.assertEqual(err, "ログファイルが未設定です")

    def test_no_tab_is_filtered_out_any_more(self):
        """フィルタを外して list(self.tabs) になっていること"""
        source = self._source()

        self.assertNotIn("if tab.v_active", source)
        self.assertEqual(source.count("active_tabs = list(self.tabs)"), 2)


class TestLaunchWidgetsStillReachable(unittest.TestCase):
    """折りたたみの親を変えても、他のメソッドが触る属性が残っていること"""

    NAMES = ("btn_launch", "btn_stop_entry", "lbl_launch", "lbl_win_warn")

    def setUp(self):
        self.source = Path("mainGUI.py").read_text(encoding="utf-8")

    def test_every_widget_is_still_assigned(self):
        for name in self.NAMES:
            self.assertIn(f"self.{name} = ", self.source, name)

    def test_they_are_still_configured_elsewhere(self):
        """config(state=...) される側なので、参照が消えていないこと"""
        for name in self.NAMES:
            uses = self.source.count(f"self.{name}")
            self.assertGreater(uses, 1, f"{name} が代入だけになっている")

    def test_the_launch_button_is_outside_the_collapsible(self):
        """畳んでも「🚀 VRChatを起動」が見えていること"""
        self.assertIn("self.btn_launch = ttk.Button(lf1", self.source)
        self.assertIn("lf1 = ttk.Frame(f2)", self.source)
        self.assertIn('CollapsibleFrame(f2, text="VRChat起動の詳細設定"',
                      self.source)

    def test_the_details_are_inside_the_collapsible(self):
        for frame in ("lf2", "lf25", "lf3"):
            self.assertIn(f"{frame} = ttk.Frame(f2_launch)", self.source, frame)

    def test_the_window_count_warning_is_outside(self):
        """※ 窓数はマクロ起動前に… は窓数の話なので畳まない"""
        self.assertIn("self.lbl_win_warn = ttk.Label(\n            f2, ", self.source)


class TestSuicideKeyFixed(unittest.TestCase):
    """自爆キーは config 固定。GUIの入力欄は消えている"""

    def tearDown(self):
        SharedState.set_suicide_key(config.SELF_SUICIDE_KEY)

    def test_the_gui_has_no_input_for_it(self):
        self.assertNotIn("v_suicide_key",
                         Path("mainGUI.py").read_text(encoding="utf-8"),
            "入力欄と適用ボタンは削除されていること")

    def test_the_default_comes_from_config(self):
        self.assertEqual(SharedState.get_suicide_key(), config.SELF_SUICIDE_KEY)

    def test_the_setter_is_kept_for_tests(self):
        """GUIから呼ばれなくなるだけ。テストが4箇所で使っている"""
        SharedState.set_suicide_key("q")

        self.assertEqual(SharedState.get_suicide_key(), "q")


class TestFollowHostSettingRemoved(unittest.TestCase):
    """廃止した follow_host_save キーの後始末"""

    def test_a_legacy_settings_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"tnl_path": "C:/list/my.tnl",
                                        "follow_host_save": False}),
                            encoding="utf-8")

            with patch.object(config, "SETTINGS_PATH", path):
                data = mainGUI.load_settings()

            self.assertEqual(data.get("tnl_path"), "C:/list/my.tnl")

    def test_the_key_is_no_longer_saved(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app._apply_keep_on = lambda new: mainGUI.App._apply_keep_on(app, new)
        with tempfile.TemporaryDirectory() as tmp:
            tnl = Path(tmp) / "list.tnl"
            tnl.write_text(json.dumps({"list_name": "L", "creator": "",
                                       "created_at": "", "data": {}}),
                           encoding="utf-8")
            app.v_tnl = TestHostListSource.FakeVar(str(tnl))
            saved = {}
            with patch.object(mainGUI, "save_settings", saved.update), \
                 patch.object(mainGUI, "load_settings", return_value={}):
                mainGUI.App._load_tnl(app, show_error=False)

        self.assertNotIn("follow_host_save", saved)

    def test_the_app_has_no_follow_switch(self):
        self.assertFalse(hasattr(mainGUI.App, "_on_follow_host_toggled"))
        self.assertFalse(hasattr(mainGUI.App, "_refresh_host_save"))


class TestHoldKeyBackground(unittest.TestCase):
    """自爆キーをフォーカス無しで送る（送り切れたらTrue）"""

    VK = 0xDE       # ^ (JIS)
    SCAN = 0x0D

    def _user32(self, **over):
        """正常系の user32 モック。over で個別の戻り値を差し替える"""
        u = MagicMock()
        u.GetWindowThreadProcessId.return_value = 4321
        u.IsIconic.return_value = 0
        u.GetKeyboardLayout.return_value = 0x04110411
        u.VkKeyScanExW.return_value = self.VK          # シフト状態 0
        u.MapVirtualKeyExW.return_value = self.SCAN
        u.AttachThreadInput.return_value = 1
        u.GetKeyboardState.return_value = 1
        for name, value in over.items():
            getattr(u, name).return_value = value
        return u

    def _send(self, u, key="^", sec=0.0, hwnd=0x1234, kernel=None):
        k = kernel or MagicMock()
        if kernel is None:
            k.GetCurrentThreadId.return_value = 99
        with patch.object(WindowOperator, "user32", u), \
             patch.object(WindowOperator, "kernel32", k), \
             patch.object(WindowOperator.time, "sleep"):
            return WindowOperator.hold_key_background(hwnd, key, sec or 3.0)

    def test_unmappable_key_is_refused(self):
        u = self._user32(VkKeyScanExW=-1)

        self.assertFalse(self._send(u))
        u.PostMessageW.assert_not_called()

    def test_shifted_key_is_refused(self):
        """Shift併用キーは対象外。黙って別のキーを送らない"""
        u = self._user32(VkKeyScanExW=(1 << 8) | self.VK)

        self.assertFalse(self._send(u))
        u.PostMessageW.assert_not_called()

    def test_unmappable_scan_code_is_refused(self):
        u = self._user32(MapVirtualKeyExW=0)

        self.assertFalse(self._send(u))
        u.PostMessageW.assert_not_called()

    def test_minimized_window_is_refused(self):
        """最小化中は送れない。ここで復元すると背面化の意味が消える"""
        u = self._user32(IsIconic=1)

        self.assertFalse(self._send(u))
        u.AttachThreadInput.assert_not_called()
        u.PostMessageW.assert_not_called()

    def test_unknown_thread_is_refused(self):
        u = self._user32(GetWindowThreadProcessId=0)

        self.assertFalse(self._send(u))
        u.AttachThreadInput.assert_not_called()

    def test_multi_char_key_is_refused(self):
        """"f13" のような複数文字キーは VkKeyScanExW で解決できない"""
        u = self._user32()

        self.assertFalse(self._send(u, key="f13"))
        u.PostMessageW.assert_not_called()

    def test_happy_path_order(self):
        u = self._user32()
        order = []
        u.AttachThreadInput.side_effect = lambda a, b, f: order.append(
            "attach" if f else "detach") or 1
        u.SetKeyboardState.side_effect = lambda *_a: order.append("state") or 1
        u.PostMessageW.side_effect = lambda h, msg, *_a: order.append(
            "down" if msg == WindowOperator.WM_KEYDOWN else "up") or 1

        self.assertTrue(self._send(u))

        self.assertEqual(order[0], "attach")
        self.assertEqual(order[-1], "detach")
        self.assertEqual([o for o in order if o in ("down", "up")], ["down", "up"])
        self.assertIn("state", order[:3], "押下はキー状態にも書き込むこと")

    def test_lparam_values(self):
        u = self._user32()

        self.assertTrue(self._send(u))

        posts = [c.args for c in u.PostMessageW.call_args_list]
        self.assertEqual(len(posts), 2)
        (_h1, msg_down, vk_down, lp_down) = posts[0]
        (_h2, msg_up, vk_up, lp_up) = posts[1]
        self.assertEqual((msg_down, vk_down, lp_down),
                         (WindowOperator.WM_KEYDOWN, self.VK, 0x000D0001))
        self.assertEqual((msg_up, vk_up, lp_up),
                         (WindowOperator.WM_KEYUP, self.VK, 0xC00D0001))

    def test_detach_runs_even_on_error(self):
        """アタッチしたまま抜けるとユーザーの操作が対象窓へ流れ込む"""
        u = self._user32()
        u.PostMessageW.side_effect = OSError("boom")

        self.assertFalse(self._send(u))

        detaches = [c.args for c in u.AttachThreadInput.call_args_list
                    if not c.args[2]]
        self.assertEqual(len(detaches), 1, u.AttachThreadInput.call_args_list)

    def test_used_win32_apis_exist_on_real_dlls(self):
        """user32/kernel32 の取り違えはモックでは出ない。実物で名前だけ確かめる"""
        u = ctypes.WinDLL("user32")
        k = ctypes.WinDLL("kernel32")

        for name in ("GetWindowThreadProcessId", "IsIconic", "GetKeyboardLayout",
                     "VkKeyScanExW", "MapVirtualKeyExW", "AttachThreadInput",
                     "SendMessageTimeoutW", "SetFocus", "GetKeyboardState",
                     "SetKeyboardState", "PostMessageW"):
            self.assertTrue(hasattr(u, name), "user32." + name)
        self.assertTrue(hasattr(k, "GetCurrentThreadId"), "kernel32.GetCurrentThreadId")

    def test_thread_id_failure_does_not_escape(self):
        """スレッドID取得で落ちても False を返す（例外を投げるとフォールバックが走らない）"""
        u = self._user32()
        k = MagicMock()
        k.GetCurrentThreadId.side_effect = AttributeError("not found")

        self.assertFalse(self._send(u, kernel=k))
        u.PostMessageW.assert_not_called()
        u.AttachThreadInput.assert_not_called()

    def test_key_state_is_restored(self):
        u = self._user32()

        self.assertTrue(self._send(u))

        # 最後の SetKeyboardState では対象キーが離された状態に戻っている
        last = u.SetKeyboardState.call_args_list[-1].args[0]
        self.assertEqual(last._obj[self.VK], 0)


class TestSuicideBackgroundRouting(unittest.TestCase):
    """do_skip の送信経路（背面 → 失敗ならフォーカス方式）"""

    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.speed_freeze_reset()
        SharedState.round_freeze_reset()
        SharedState.set_suicide_key("^")

    tearDown = setUp

    def _executor(self):
        cfg = WindowConfig(hwnd=123, do_skip=True)
        st = WindowState(in_round=True)
        logs = []
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append), st, logs

    def test_background_success_does_not_take_focus(self):
        ex, st, _logs = self._executor()

        with patch.object(config, "SUICIDE_BACKGROUND", True), \
             patch.object(WindowOperator, "hold_key_background",
                          return_value=True) as mock_bg, \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()

        mock_bg.assert_called_once_with(123, "^", config.SUICIDE_HOLD_SEC)
        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertGreater(st._skip_time, 0, "死亡判定用の時刻は残すこと")

    def test_background_failure_falls_back_to_focus(self):
        ex, _st, logs = self._executor()

        with patch.object(config, "SUICIDE_BACKGROUND", True), \
             patch.object(WindowOperator, "hold_key_background",
                          return_value=False), \
             patch.object(WindowOperator, "focus_window", return_value=True) as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()

        mock_focus.assert_called_once_with(123)
        mock_hold.assert_called_once_with("^", config.SUICIDE_HOLD_SEC)
        self.assertTrue(any("フォーカス方式へ" in m for m in logs), logs)

    def test_disabled_uses_the_old_path_only(self):
        ex, _st, _logs = self._executor()

        with patch.object(config, "SUICIDE_BACKGROUND", False), \
             patch.object(WindowOperator, "hold_key_background") as mock_bg, \
             patch.object(WindowOperator, "focus_window", return_value=True) as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()

        mock_bg.assert_not_called()
        mock_focus.assert_called_once_with(123)
        mock_hold.assert_called_once_with("^", config.SUICIDE_HOLD_SEC)


class TestHoldKey(unittest.TestCase):
    def test_zero_sec_skips(self):
        """sec=0の時は何もしない"""
        with patch('keyboard.press') as mock:
            WindowOperator.hold_key('w', 0.0)
            mock.assert_not_called()

    def test_holds_key(self):
        """press→sleep→releaseの順で呼ばれる"""
        with patch('keyboard.press') as mock_press, \
             patch('keyboard.release') as mock_release:
            WindowOperator.hold_key('w', 0.1)
            mock_press.assert_called_once_with('w')
            mock_release.assert_called_once_with('w')

class TestClickAt(unittest.TestCase):
    def test_click(self):
        """mouseDown→mouseUpの順で呼ばれる"""
        with patch('pydirectinput.mouseDown') as mock_down, \
             patch('pydirectinput.mouseUp') as mock_up:
            WindowOperator.click()
            mock_down.assert_called_once()
            mock_up.assert_called_once()


class TestActionExecutorSkip(unittest.TestCase):
    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()
        SharedState.set_suicide_key(config.SELF_SUICIDE_KEY)

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.CONTINUE_ROUND_EVENT.set()
        SharedState.set_suicide_key(config.SELF_SUICIDE_KEY)
        SharedState.set_item_begin_mode(False)

    def test_do_skip_cancels_when_hwnd_missing(self):
        cfg = WindowConfig(hwnd=0)
        st = WindowState(in_round=True)
        logs: list[str] = []
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)

        with patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold:
            executor.do_skip()

        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertTrue(any("HWND" in msg for msg in logs))

    def test_do_skip_waits_after_focus_before_holding_key(self):
        cfg = WindowConfig(hwnd=123)
        st = WindowState(in_round=True)
        calls = []
        SharedState.set_suicide_key("x")
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _msg: None)

        def _focus(hwnd):
            calls.append(("focus", hwnd))
            return True

        with patch.object(WindowOperator, "focus_window", side_effect=_focus), \
             patch.object(ActionExecutor.time, "sleep", side_effect=lambda sec: calls.append(("sleep", sec))), \
             patch.object(WindowOperator, "hold_key", side_effect=lambda key, sec: calls.append(("hold", key, sec))):
            executor.do_skip()

        self.assertEqual(
            calls,
            [
                ("focus", 123),
                ("sleep", config.SUICIDE_FOCUS_SETTLE_SEC),
                ("hold", "x", config.SUICIDE_HOLD_SEC),
            ],
        )

    def test_do_after_round_uses_window_instance_not_global_instance(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        cfg = WindowConfig(hwnd=123)
        # Begin は Verified Round End の後にしか押さないので、実機同様に立てる
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True)
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _msg: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ActionExecutor.time, "sleep"), \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key"), \
             patch.object(WindowOperator, "click"):
            executor.do_after_round()

        mock_focus.assert_called()

    def test_do_after_round_item_begin_mode_no_deadlock_when_already_equipped(self):
        """アイテム取得→Beginモード: 自窓がクリアしたEQUIP_WAIT_EVENTを
        自分で待つデッドロックにならず、Beginクリックまで到達する"""
        SharedState.set_item_begin_mode(True)
        cfg = WindowConfig(hwnd=123)
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=True,
            item_id=5,  # フリーズ中に装備済み
            round_end_seen=True,
        )
        SharedState.equip_freeze_start(st)  # Verified End時に自窓がフリーズを張った状態
        calls = {"n": 0}

        def is_running():
            # デッドロック時（旧実装）はここがFalseになりclick未達でテスト失敗する
            calls["n"] += 1
            return calls["n"] < 30

        executor = ActionExecutor.ActionExecutor(cfg, st, is_running, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window"), \
             patch.object(WindowOperator, "hold_key"), \
             patch.object(WindowOperator, "click") as mock_click:
            executor.do_after_round()

        mock_click.assert_called()

    def test_do_after_round_item_begin_mode_waits_for_equip_then_begins(self):
        """アイテム取得→Beginモード: 未装備なら装備を待ち、装備後にBeginへ進む"""
        SharedState.set_item_begin_mode(True)
        cfg = WindowConfig(hwnd=123)
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=True,
            item_id=0,  # 未装備
            round_end_seen=True,
        )
        SharedState.equip_freeze_start(st)
        logs: list[str] = []
        sleep_count = {"n": 0}

        def fake_sleep(_sec):
            # 装備待ちループ数周後に装備完了をシミュレート
            sleep_count["n"] += 1
            if sleep_count["n"] >= 3:
                st.item_id = 5

        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)

        with patch.object(config, "BEGIN_WAIT_SEC", 0), \
             patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep), \
             patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window"), \
             patch.object(WindowOperator, "hold_key"), \
             patch.object(WindowOperator, "click") as mock_click:
            executor.do_after_round()

        mock_click.assert_called()
        self.assertTrue(any("装備確認" in msg for msg in logs))


    def _run_late_item_lost(self, osc_port: int):
        """Verified Round End で初めてロストが判明する実機どおりの順序を再現する。

        do_after_round は RoundOver+11秒で始まるため、開始時点では
        waiting_for_equip はまだ False。Round End 待ちの最中に立つ。
        """
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=False,   # RoundOver時点ではまだ判明していない
            round_end_seen=False,
            item_id=1,
        )
        order: list[str] = []

        def fake_sleep(_sec):
            # Round End 待ちの最中に Verified Round End が届く
            st.round_end_seen = True
            st.waiting_for_equip = True

        def fake_freeze_start(state):
            order.append("freeze")
            _real_freeze_start(state)

        _real_freeze_start = SharedState.equip_freeze_start
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep),              patch.object(ActionExecutor.SharedState, "equip_freeze_start",
                          side_effect=fake_freeze_start),              patch.object(ActionExecutor.PlaySound, "play_sound") as mock_sound,              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "hold_key"),              patch.object(WindowOperator, "click",
                          side_effect=lambda: order.append("click")):
            executor.do_after_round()

        return st, order, mock_sound

    def test_do_after_round_freezes_when_item_lost_found_at_round_end_osc(self):
        """OSC窓: RoundOver後に判明したアイテムロストでもフリーズが張られる"""
        with patch.object(ActionExecutor.OSCClient, "OSCClient", return_value=MagicMock()):
            st, order, mock_sound = self._run_late_item_lost(osc_port=9000)

        self.assertTrue(st.equip_freeze_held, "フリーズが張られていない")
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(order, ["freeze", "click"], "フリーズはBeginクリック前")
        mock_sound.assert_called()

    def test_do_after_round_freezes_when_item_lost_found_at_round_end_no_osc(self):
        """非OSC窓: 同上（キーボード操作経路でもフリーズが張られる）"""
        st, order, mock_sound = self._run_late_item_lost(osc_port=0)

        self.assertTrue(st.equip_freeze_held, "フリーズが張られていない")
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(order, ["freeze", "click"], "フリーズはBeginクリック前")
        mock_sound.assert_called()


class TestLaunchWindowCount(unittest.TestCase):
    """起動する窓数（既定は「窓数 − 起動済みの窓数」）"""

    class FakeVar:
        def __init__(self, value=0):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeTab:
        def __init__(self, idx, profile):
            self.idx = idx
            self.v_profile = TestLaunchWindowCount.FakeVar(profile)

    def test_count_is_win_count_minus_already_open(self):
        self.assertEqual(mainGUI.launch_window_count(4, 2), 2)
        self.assertEqual(mainGUI.launch_window_count(4, 0), 4)

    def test_count_never_goes_negative(self):
        """必要数より多く開いていても0で止める"""
        self.assertEqual(mainGUI.launch_window_count(2, 5), 0)
        self.assertEqual(mainGUI.launch_window_count(4, 4), 0)

    def test_launch_targets_are_taken_from_the_back(self):
        """既存の窓は先頭タブに割り当てられるので、起動するのは後ろのタブ"""
        tabs = [self.FakeTab(i, i + 1) for i in range(4)]

        picked = mainGUI.tabs_to_launch(tabs, 2)

        self.assertEqual([t.idx for t in picked], [2, 3])
        self.assertEqual(mainGUI.tabs_to_launch(tabs, 0), [])
        self.assertEqual([t.idx for t in mainGUI.tabs_to_launch(tabs, 9)], [0, 1, 2, 3])

    def test_plan_uses_tab_index_for_osc_and_profile(self):
        """OSC割当は監視開始時と同じタブ番号を使う（ポートがずれないように）"""
        tabs = [self.FakeTab(i, 10 + i) for i in range(4)]

        plan = mainGUI.build_launch_plan(mainGUI.tabs_to_launch(tabs, 2))

        self.assertEqual(plan, [(3, 12, 2), (4, 13, 3)])

    def test_sync_uses_detected_window_count(self):
        app = type("FakeApp", (), {})()
        app._running = False
        app.v_win_count = self.FakeVar(4)
        app.v_launch_count = self.FakeVar(0)

        with patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time",
                          return_value=[(1, None), (2, None)]):
            mainGUI.App._sync_launch_count(app)

        self.assertEqual(app.v_launch_count.get(), 2)

    def test_sync_is_skipped_while_running(self):
        """マクロ動作中は窓数を触らせないので既定値も更新しない"""
        app = type("FakeApp", (), {})()
        app._running = True
        app.v_win_count = self.FakeVar(4)
        app.v_launch_count = self.FakeVar(7)

        mainGUI.App._sync_launch_count(app)

        self.assertEqual(app.v_launch_count.get(), 7)

    def test_manual_value_is_clamped_to_the_tab_count(self):
        app = type("FakeApp", (), {})()
        app.v_launch_count = self.FakeVar(9)

        self.assertEqual(mainGUI.App._launch_count_value(app, 4), 4)
        self.assertEqual(app.v_launch_count.get(), 4, "丸めた値をGUIにも戻すこと")

        app.v_launch_count = self.FakeVar(-3)
        self.assertEqual(mainGUI.App._launch_count_value(app, 4), 0)


class TestItemLostAnnounceTiming(unittest.TestCase):
    """アイテムロストの通知はBeginクリックの直前に鳴らす"""

    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_item_begin_mode(False)

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_item_begin_mode(False)

    def _order_of_actions(self, osc_port: int) -> list[str]:
        """Verified Round End でロストが判明する流れの操作順を記録する"""
        cfg = WindowConfig(hwnd=123, osc_port=osc_port, voice_item_lost="lost.mp3")
        st = WindowState(
            instance_type=config.INSTANCE_PRIVATE,
            waiting_for_equip=False,   # RoundOver時点ではまだ判明していない
            round_end_seen=False,
            item_id=1,
        )
        order: list[str] = []

        def fake_sleep(_sec):
            # Round End 待ちの最中に Verified Round End が届く
            st.round_end_seen = True
            st.waiting_for_equip = True

        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep),              patch.object(ActionExecutor.SharedState, "equip_freeze_start",
                          side_effect=lambda state: order.append("freeze")),              patch.object(ActionExecutor.PlaySound, "play_sound",
                          side_effect=lambda _p: order.append("sound")),              patch.object(executor, "move",
                          side_effect=lambda d, sec: order.append("move")),              patch.object(executor, "move_forward_left",
                          side_effect=lambda f, l: order.append("move")),              patch.object(WindowOperator, "focus_window",
                          side_effect=lambda _h: order.append("focus") or True),              patch.object(WindowOperator, "click",
                          side_effect=lambda: order.append("click")):
            executor.do_after_round()
        return order

    def test_announce_comes_right_before_the_click_osc(self):
        """OSC窓: 移動・フリーズ・フォーカスの後、クリックの直前に鳴らす"""
        order = self._order_of_actions(osc_port=9000)

        self.assertEqual(order, ["move", "freeze", "focus", "sound", "click"])

    def test_announce_comes_right_before_the_click_no_osc(self):
        """非OSC窓: 移動もロック内なので、移動を終えてから鳴らす"""
        order = self._order_of_actions(osc_port=0)

        self.assertEqual(order, ["freeze", "focus", "move", "sound", "click"])

    def test_announce_is_skipped_when_item_is_kept(self):
        """アイテムを持ったまま終わったラウンドでは鳴らさない"""
        cfg = WindowConfig(hwnd=123, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=5)
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor.time, "sleep"),              patch.object(ActionExecutor.PlaySound, "play_sound") as mock_play,              patch.object(executor, "move"),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click") as mock_click:
            executor.do_after_round()

        mock_click.assert_called_once()
        mock_play.assert_not_called()

    def test_item_begin_mode_still_announces_before_waiting_for_equip(self):
        """アイテム取得→Beginモードだけは装備を待つ前に鳴らす

        この通知が「拾ってきて」の合図なので、クリック時まで遅らせると
        プレイヤーが装備すべきことに気付けない。
        """
        SharedState.set_item_begin_mode(True)
        cfg = WindowConfig(hwnd=123, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         waiting_for_equip=True, item_id=0, round_end_seen=True)
        SharedState.equip_freeze_start(st)
        order: list[str] = []
        sleeps = {"n": 0}

        def fake_sleep(_sec):
            sleeps["n"] += 1
            if sleeps["n"] >= 3:
                st.item_id = 5      # プレイヤーが装備した

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep),              patch.object(ActionExecutor.PlaySound, "play_sound",
                          side_effect=lambda _p: order.append("sound")),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click",
                          side_effect=lambda: order.append("click")):
            executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
            with patch.object(executor, "move"):
                executor.do_after_round()

        self.assertEqual(order, ["sound", "click"], "装備待ちより前に鳴らすこと")


class TestOscMoveDuringFreeze(unittest.TestCase):
    """全窓フリーズ中でもOSC移動は行い、フォーカスを要するクリックだけ待つ

    OSCはフォーカスを奪わないので他窓の操作を妨げない。フリーズ中に
    移動だけ済ませておけば、解除された瞬間にBeginを押せる。
    """

    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_item_begin_mode(False)

    def _executor(self, st, osc_port=9000):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def _state(self):
        # Beginは Verified Round End の後にしか押さないので実機同様に立てる
        return WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True)

    def _run_frozen_begin(self, release, osc_port=9000):
        """フリーズ中に do_after_round を走らせ、移動とクリックの前後を観測する。

        release() を呼ぶまでフリーズは解除されない。
        戻り値: (moves, moved, clicked, thread)
        """
        st = self._state()
        ex = self._executor(st, osc_port=osc_port)
        moves: list[str] = []
        moved = threading.Event()
        clicked = threading.Event()

        def on_move(direction, seconds):
            moves.append(direction)
            moved.set()

        def on_move_fl(forward_sec, left_sec):
            # 前進を通しで押し、頭だけ左を重ねる（斜め→直進）1回の移動
            moves.append("forward+left")
            moved.set()

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(ex, "move", side_effect=on_move), patch.object(ex, "move_forward_left", side_effect=on_move_fl),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click", side_effect=clicked.set):
            t = threading.Thread(target=ex.do_after_round, daemon=True)
            t.start()
            observed_move = moved.wait(3.0)
            clicked_while_frozen = clicked.wait(0.6)
            release()
            clicked_after_release = clicked.wait(3.0)
            t.join(timeout=3.0)
        return moves, observed_move, clicked_while_frozen, clicked_after_release

    def test_begin_move_runs_during_continue_freeze_and_click_waits(self):
        """他窓が続行ラウンド中でも、Begin前移動は進みクリックだけ待つ"""
        SharedState.continue_round_start()
        moves, observed_move, clicked_while_frozen, clicked_after_release =             self._run_frozen_begin(SharedState.continue_round_end)

        self.assertTrue(observed_move, "フリーズ中でもOSC移動は行うこと")
        self.assertEqual(moves, ["forward+left"])
        self.assertFalse(clicked_while_frozen, "フリーズ中にクリックしてはいけない")
        self.assertTrue(clicked_after_release, "解除後はクリックすること")

    def test_begin_move_runs_during_equip_freeze_and_click_waits(self):
        """他窓がアイテムロスト装備待ちでも、Begin前移動は進みクリックだけ待つ"""
        other = WindowState()
        SharedState.equip_freeze_start(other)
        moves, observed_move, clicked_while_frozen, clicked_after_release =             self._run_frozen_begin(lambda: SharedState.equip_freeze_end(other))

        self.assertTrue(observed_move, "フリーズ中でもOSC移動は行うこと")
        self.assertEqual(moves, ["forward+left"])
        self.assertFalse(clicked_while_frozen, "フリーズ中にクリックしてはいけない")
        self.assertTrue(clicked_after_release, "解除後はクリックすること")

    def test_keyboard_window_still_waits_before_moving(self):
        """キー入力で移動する窓は従来どおり、移動もフリーズ解除まで待つ

        キー移動はフォーカスを要するため、フリーズ中に動かすと他窓を妨げる。
        """
        SharedState.continue_round_start()
        moves, observed_move, clicked_while_frozen, clicked_after_release =             self._run_frozen_begin(SharedState.continue_round_end, osc_port=0)

        self.assertFalse(observed_move, "非OSC窓はフリーズ中に移動してはいけない")
        self.assertFalse(clicked_while_frozen)
        self.assertTrue(clicked_after_release, "解除後は移動してクリックすること")
        self.assertEqual(moves, ["forward+left"])


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
        link = "vrchat://launch?ref=vrchat.com&id=wrld_abc-123:12345~private(usr_x)~region(jp)"
        nums = [VRChatLauncher.with_unique_instance(link, i).split(":")[-1].split("~")[0]
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


class TestStartWithoutTnl(unittest.TestCase):
    """tnl未読み込みでも開始できる（続行リストは0件として扱う）"""

    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _app(self, keep_on):
        app = type("FakeApp", (), {})()
        app.keepOn_set = keep_on
        app.tabs = []
        app.monitors = []
        app.logs = []
        app._log = app.logs.append
        app._running = False
        app.btn_start = MagicMock()
        app.btn_stop = MagicMock()
        app.lbl_win_warn = MagicMock()
        for name in ("v_voice_continue", "v_voice_fog", "v_voice_item_lost",
                     "v_voice_intermission", "v_voice_foxy"):
            setattr(app, name, self.FakeVar(""))
        return app

    def test_start_is_not_blocked_without_tnl(self):
        """警告で止めず、そのまま窓の準備へ進む"""
        app = self._app({})

        with patch.object(mainGUI.messagebox, "showwarning") as mock_warn, \
             patch.object(mainGUI.messagebox, "showerror") as mock_error, \
             patch.object(VRChatDiscovery, "find_latest_logs", return_value=[]):
            mainGUI.App._start(app)

        mock_warn.assert_not_called()
        # 窓が無いので最後は「有効な窓/ログが見つかりません」で止まる＝tnlでは止まっていない
        mock_error.assert_called_once()
        self.assertTrue(any("tnl未読み込み" in m for m in app.logs))

    def test_empty_keep_on_set_never_continues(self):
        """tnlが空なら、どのテラーでも続行にならない"""
        for round_type in ("Classic", "Alternate", "Midnight", "Double Trouble"):
            decision = RoundDecision.decide_killers(
                {}, [42, 7, 3], round_type, wins=0, cancel_afk=False)
            self.assertFalse(decision.is_continue_round, round_type)

    def test_tnl_entries_still_continue(self):
        """読み込んだ場合は従来どおり続行する（止めすぎていないことの確認）"""
        decision = RoundDecision.decide_killers(
            {"Classic/クラシック": {42}}, [42], "Classic", wins=0, cancel_afk=False)

        self.assertTrue(decision.is_continue_round)


class _ImmediateThread:
    """テスト用: start() でその場で実行し、以後 is_alive() は一度だけTrue"""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._calls = 0

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def is_alive(self):
        self._calls += 1
        return self._calls <= 1

    def join(self, timeout=None):
        pass


class TestFreezeSettings(unittest.TestCase):
    """フリーズ設定は全窓共通（フリーズ自体が全窓を止めるため）"""

    def setUp(self):
        SharedState.set_freeze_on_8pages(False)
        SharedState.set_freeze_on_punish(False)
        SharedState.set_freeze_rounds(())

    tearDown = setUp

    def test_flags_round_trip(self):
        SharedState.set_freeze_on_8pages(True)
        SharedState.set_freeze_on_punish(True)

        self.assertTrue(SharedState.get_freeze_on_8pages())
        self.assertTrue(SharedState.get_freeze_on_punish())

    def test_rounds_round_trip(self):
        SharedState.set_freeze_rounds(["Alternate", "Ghost"])

        self.assertEqual(SharedState.get_freeze_rounds(), {"Alternate", "Ghost"})

    def test_rounds_accepts_any_iterable(self):
        SharedState.set_freeze_rounds(name for name in ("Midnight",))

        self.assertEqual(SharedState.get_freeze_rounds(), {"Midnight"})

    def test_getter_returns_a_copy(self):
        """返した set を書き換えても内部状態は変わらない"""
        SharedState.set_freeze_rounds(["Alternate"])

        got = SharedState.get_freeze_rounds()
        got.add("Unbound")
        got.discard("Alternate")

        self.assertEqual(SharedState.get_freeze_rounds(), {"Alternate"})

    def test_checkbox_order_follows_the_config_list(self):
        """ソートせず ROUND_FREEZE_SELECTABLE の順序で並べる"""
        made = mainGUI.freeze_round_vars(lambda: object())

        self.assertEqual(list(made), list(config.ROUND_FREEZE_SELECTABLE))


class TestFreezeSettingsMigration(unittest.TestCase):
    """旧形式（窓ごとの配列）の settings.json も読めること"""

    def test_flag_array_is_any(self):
        self.assertTrue(mainGUI._as_flag([False, True, False]))
        self.assertFalse(mainGUI._as_flag([False, False]))

    def test_flag_scalar_and_missing(self):
        self.assertTrue(mainGUI._as_flag(True))
        self.assertFalse(mainGUI._as_flag(None))
        self.assertFalse(mainGUI._as_flag([]))

    def test_round_arrays_are_unioned(self):
        value = [["Alternate"], [], ["Ghost", "Alternate"], ["Midnight"]]

        self.assertEqual(mainGUI._as_round_names(value),
                         {"Alternate", "Ghost", "Midnight"})

    def test_new_format_flat_list(self):
        self.assertEqual(mainGUI._as_round_names(["Alternate", "Punished"]),
                         {"Alternate", "Punished"})

    def test_missing_or_broken_falls_back_to_empty(self):
        self.assertEqual(mainGUI._as_round_names(None), set())
        self.assertEqual(mainGUI._as_round_names([]), set())
        self.assertEqual(mainGUI._as_round_names([None, 5]), set())


class TestSpeedFreeze(unittest.TestCase):
    """速度検知で 8 Pages / Punished を掴んだら全窓を止める"""

    def setUp(self):
        SharedState.speed_freeze_reset()
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_freeze_on_8pages(False)
        SharedState.set_freeze_on_punish(False)

    tearDown = setUp

    def _executor(self):
        cfg = WindowConfig(hwnd=123, osc_port=9000,
                           voice_8pages="8p.mp3", voice_punish="pn.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None), st

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _detect(self, ex, kind):
        with patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window", return_value=True):
            ex._announce_speed_kind(kind, 6.5)

    def test_eight_pages_freezes_when_enabled(self):
        SharedState.set_freeze_on_8pages(True)
        ex, st = self._executor()

        self._detect(ex, "8pages")

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertTrue(st.speed_freeze_held)
        self.assertEqual(st.speed_freeze_kind, "8pages")

    def test_eight_pages_does_not_freeze_when_disabled(self):
        ex, st = self._executor()

        self._detect(ex, "8pages")

        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertFalse(st.speed_freeze_held)

    def test_punish_freezes_when_enabled(self):
        SharedState.set_freeze_on_punish(True)
        ex, st = self._executor()

        self._detect(ex, "punish")

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertEqual(st.speed_freeze_kind, "punish")

    def test_normal_never_freezes(self):
        SharedState.set_freeze_on_8pages(True)
        SharedState.set_freeze_on_punish(True)
        ex, st = self._executor()

        self._detect(ex, "normal")

        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertFalse(st.speed_freeze_held)

    def test_first_window_is_brought_to_front(self):
        SharedState.set_freeze_on_8pages(True)
        ex, _st = self._executor()

        with patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window", return_value=True) as mock_focus:
            ex._announce_speed_kind("8pages", 6.5)

        mock_focus.assert_called_once_with(123)

    def test_second_window_does_not_steal_focus(self):
        SharedState.set_freeze_on_8pages(True)
        other = WindowState()
        SharedState.speed_freeze_start(other)
        ex, st = self._executor()

        with patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(WindowOperator, "focus_window") as mock_focus:
            ex._announce_speed_kind("8pages", 6.5)

        mock_focus.assert_not_called()
        self.assertTrue(st.speed_freeze_held, "フリーズ自体は張ること")

    def test_item_equip_releases_eight_pages_freeze(self):
        monitor = self._monitor()
        monitor.st.speed_freeze_kind = "8pages"
        SharedState.speed_freeze_start(monitor.st)

        monitor._process("Equipping 42.")

        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())

    def test_item_equip_does_not_release_punish_freeze(self):
        monitor = self._monitor()
        monitor.st.speed_freeze_kind = "punish"
        SharedState.speed_freeze_start(monitor.st)

        monitor._process("Equipping 42.")

        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set())

    def test_round_start_releases_any_freeze(self):
        """保険: アイテムを取らないままラウンドが始まっても必ず解除する"""
        for kind in ("8pages", "punish"):
            SharedState.speed_freeze_reset()
            monitor = self._monitor()
            monitor.st.speed_freeze_kind = kind
            SharedState.speed_freeze_start(monitor.st)

            with patch.object(ConnectDB, "send_ToNRoundStatistics"), \
             patch.object(LogMonitor.threading, "Thread"):
                monitor._process("This round is taking place at Facility (12) "
                                 "and the round type is Classic")

            self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set(), kind)
            self.assertFalse(monitor.st.speed_freeze_held, kind)

    def test_two_windows_hold_the_freeze_independently(self):
        a, b = WindowState(), WindowState()
        SharedState.speed_freeze_start(a)
        SharedState.speed_freeze_start(b)

        SharedState.speed_freeze_end(a)
        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set(), "まだ止まっていること")

        SharedState.speed_freeze_end(b)
        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())

    def test_the_window_that_froze_does_not_wait_for_itself(self):
        SharedState.set_freeze_on_8pages(True)
        ex, _st = self._executor()
        self._detect(ex, "8pages")

        self.assertTrue(ex._wait_other_windows(), "自分が張ったフリーズで詰まらない")


class TestRoundFreeze(unittest.TestCase):
    """指定ラウンドに突入したら全窓を止める（張った窓自身は自爆できる）"""

    ROUND_LINE = ("This round is taking place at Facility (12) "
                  "and the round type is %s")

    def setUp(self):
        SharedState.round_freeze_reset()
        SharedState.speed_freeze_reset()
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_freeze_rounds(())

    tearDown = setUp

    def _monitor(self, rounds=("Alternate",)):
        SharedState.set_freeze_rounds(rounds)
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _start_round(self, monitor, round_type):
        with patch.object(ConnectDB, "send_ToNRoundStatistics"), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process(self.ROUND_LINE % round_type)

    def test_selected_round_freezes_every_window(self):
        monitor = self._monitor(["Alternate"])

        self._start_round(monitor, "Alternate")

        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertTrue(monitor.st.round_freeze_held)

    def test_unselected_round_does_not_freeze(self):
        monitor = self._monitor(["Alternate"])

        self._start_round(monitor, "Classic")

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())

    def test_all_six_selectable_rounds_work(self):
        for name in config.ROUND_FREEZE_SELECTABLE:
            SharedState.round_freeze_reset()
            monitor = self._monitor([name])

            self._start_round(monitor, name)

            self.assertTrue(monitor.st.round_freeze_held, name)

    def test_freeze_happens_without_waiting_for_the_killers(self):
        monitor = self._monitor(["Midnight"])

        self._start_round(monitor, "Midnight")

        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertEqual(monitor.st.terror_ids, [], "テラーはまだ判明していない")

    def test_the_window_that_froze_can_still_suicide(self):
        """★自窓の自爆は止めない。止めるのは他窓だけ"""
        cfg = WindowConfig(hwnd=123, do_skip=True)
        st = WindowState(in_round=True, round_freeze_held=True)
        SharedState.ROUND_FREEZE_EVENT.clear()
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
        held = []

        with patch.object(WindowOperator, "focus_window", return_value=True), \
             patch.object(ActionExecutor.time, "sleep"), \
             patch.object(WindowOperator, "hold_key",
                          side_effect=lambda k, sec: held.append(k)):
            ex.do_skip()

        self.assertTrue(held, "自窓は待たずに自爆すること")

    def test_next_round_releases_even_without_dying(self):
        """保険: 死なずにラウンドが終わっても永久フリーズしない"""
        monitor = self._monitor(["Alternate"])
        self._start_round(monitor, "Alternate")

        self._start_round(monitor, "Classic")

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertFalse(monitor.st.round_freeze_held)

    def test_death_releases_after_the_delay(self):
        monitor = self._monitor(["Alternate"])
        self._start_round(monitor, "Alternate")
        monitor._running = True

        with patch.object(LogMonitor.time, "sleep") as mock_sleep:
            monitor._release_round_freeze_after_delay(monitor.st.round_seq)

        mock_sleep.assert_called_once_with(config.FOG_FREEZE_RELEASE_DELAY_SEC)
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())

    def test_two_windows_hold_it_independently(self):
        a, b = WindowState(), WindowState()
        SharedState.round_freeze_start(a)
        SharedState.round_freeze_start(b)

        SharedState.round_freeze_end(a)
        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())

        SharedState.round_freeze_end(b)
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())

    def test_reset_forces_release(self):
        st = WindowState()
        SharedState.round_freeze_start(st)

        SharedState.round_freeze_reset()

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertEqual(SharedState.get_round_freeze_count(), 0)

    def test_hands_free_does_not_freeze(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(["Alternate"])

        self._start_round(monitor, "Alternate")

        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set())


class TestTerrorNameAlwaysLogged(unittest.TestCase):
    """テラー名は判定より先に出す（早期returnの手前）

    _on_killers には設定・インスタンス種別による early return が5つあり、
    以前は判定まで到達しないと名前が一切残らなかった。
    """

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()

    def _monitor(self, instance_type=None, keep_on=None, **cfg_kw):
        cfg = WindowConfig(voice_continue="continue.mp3", **cfg_kw)
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type or config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _on_killers(self, monitor, ids=(99,), round_type="Classic", revealed=False):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type, revealed=revealed)
        return monitor.logs

    def test_logged_when_auto_skip_is_off(self):
        logs = self._on_killers(self._monitor(do_skip=False))

        self.assertTrue(any("テラーset:" in m for m in logs), logs)

    def test_logged_under_instance_restriction(self):
        """publicなど操作しないインスタンスでも名前は残す"""
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC)

        logs = self._on_killers(monitor)

        self.assertTrue(any("テラーset:" in m for m in logs), logs)
        self.assertTrue(any("インスタンス制限" in m for m in logs), logs)

    def test_logged_when_hoshiimo_skip_decides(self):
        monitor = self._monitor(instance_type=config.INSTANCE_HOSHIIMO)

        logs = self._on_killers(monitor)

        self.assertTrue(any("テラーset:" in m for m in logs), logs)

    def test_logged_in_hands_free(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(do_skip=True)

        logs = self._on_killers(monitor)

        self.assertTrue(any("テラーset:" in m for m in logs), logs)

    def test_unknown_id_shows_the_raw_id(self):
        """terrors.json に無いIDは ID:xxxxx の形で出る"""
        logs = self._on_killers(self._monitor(), ids=(99999,))

        self.assertTrue(any("ID:99999" in m for m in logs), logs)

    def test_decision_line_has_no_name(self):
        """判定行は名前を落としてタグだけ"""
        logs = self._on_killers(self._monitor())

        decision = [m for m in logs if "判定:" in m]
        self.assertEqual(len(decision), 1, logs)
        self.assertIn("【スキップ】", decision[0])
        self.assertNotIn("テラー", decision[0])

    def test_revealed_uses_the_other_verb(self):
        logs = self._on_killers(self._monitor(), revealed=True)

        self.assertTrue(any("テラーrevealed:" in m for m in logs), logs)

    def test_name_comes_before_the_decision(self):
        logs = self._on_killers(self._monitor())
        names = [i for i, m in enumerate(logs) if "テラーset:" in m]
        decisions = [i for i, m in enumerate(logs) if "判定:" in m]

        self.assertTrue(names and decisions)
        self.assertLess(names[0], decisions[0], "名前を先に出すこと")


class TestGigabytesDetect(unittest.TestCase):
    """The Gigabytes はテラーIDで判別できないのでログ行で拾う"""

    LINE = "2026.09.05 14:29:35 Debug      -  The Gigabytes have come."

    def test_line_is_parsed(self):
        event = LogParser.parse(self.LINE)

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, LogParser.EVENT_GIGABYTES)

    def test_similar_lines_do_not_match(self):
        """同じラウンドに出る紛らわしい行を拾わないこと"""
        for line in ("2026.09.05 14:29:35 Debug      -  The Gigabytes have come",
                     "2026.09.05 14:29:35 Debug      -  BLUE GIGABYTEtriggered "
                     "an Enrage State!",
                     "2026.09.05 14:29:35 Debug      -  The Gigabytes have come. now"):
            self.assertIsNone(LogParser.parse(line), line)

    def test_handler_logs_once(self):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process(self.LINE)

        hits = [m for m in logs if "The Gigabytes 出現" in m]
        self.assertEqual(len(hits), 1, logs)
        mock_thread.assert_not_called()
        mock_play.assert_not_called()

    def test_only_the_gigabytes_flag_is_set(self):
        """立てるのは gigabytes だけ。他の状態は動かさない"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        before = dict(vars(monitor.st))

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        after = dict(vars(monitor.st))
        self.assertTrue(after.pop("gigabytes"))
        before.pop("gigabytes")
        self.assertEqual(after, before, "gigabytes 以外は変えないこと")

    def test_terror_ids_are_replaced_wholesale(self):
        """元IDが毎回違うので「置換」ではなく差し替える"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [91]

        with patch.object(LogMonitor.threading, "Thread"),              patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process(self.LINE)

        self.assertEqual(monitor.st.terror_ids, [config.GIGABYTES_ID])

    def test_line_before_killers_leaves_ids_to_on_killers(self):
        """Killers 行より先に来ることがある。空のまま差し替えて統計を送らない"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread"),              patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._process(self.LINE)

        self.assertEqual(monitor.st.terror_ids, [])
        mock_send.assert_not_called()

        with patch.object(LogMonitor.threading, "Thread"),              patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._on_killers([91], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.GIGABYTES_ID])


class TestAtrachedDetect(unittest.TestCase):
    """Atrached は Sonic のVariant。IDでは判別できないのでログ行で拾う"""

    LINE = "2026.09.12 20:23:15 Debug      -  Lets play a game..."

    def test_line_is_parsed(self):
        event = LogParser.parse(self.LINE)

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, LogParser.EVENT_ATRACHED)

    def test_similar_lines_do_not_match(self):
        """アポストロフィ有り・ピリオドの数違いは拾わない"""
        prefix = "2026.09.12 20:23:15 Debug      -  "
        for body in ("Let's play a game...",
                     "Lets play a game.",
                     "Lets play a game",
                     "Lets play a game....",
                     "Lets play a game... now"):
            self.assertIsNone(LogParser.parse(prefix + body), body)

    def test_handler_logs_once(self):
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process(self.LINE)

        hits = [m for m in logs if "Atrached 出現" in m]
        self.assertEqual(len(hits), 1, logs)
        mock_thread.assert_not_called()
        mock_play.assert_not_called()

    def test_logged_even_when_auto_skip_is_off(self):
        """自爆オフでも出す（早期returnより前に置いてあること）"""
        logs = []
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=False), {},
                                        logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PUBLIC

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        self.assertTrue(any("Atrached 出現" in m for m in logs), logs)

    def test_sonic_is_replaced_with_atrached(self):
        """HHI(47->190)と同じ形。Sonic(40) を 191 に置換する"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.SONIC_ID]

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process(self.LINE)

        self.assertTrue(monitor.st.atrached_variant)
        self.assertEqual(monitor.st.terror_ids, [config.ATRACHED_ID])

    def test_line_before_killers_still_marks_the_variant(self):
        """Killers 行より先に来ても、後から来たIDに適用されること"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._process(self.LINE)
        mock_send.assert_not_called()

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._on_killers([config.SONIC_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.ATRACHED_ID])

    def test_other_round_types_are_ignored(self):
        """Classic 以外では置換しない（HHIと同じ扱い）"""
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Midnight"
        monitor.st.terror_ids = [config.SONIC_ID]

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.LINE)

        self.assertFalse(monitor.st.atrached_variant)
        self.assertEqual(monitor.st.terror_ids, [config.SONIC_ID])


class TestStringDownloadTrigger(unittest.TestCase):
    """速度検知の起点はラウンドデータの取得（誰がBeginを押しても出る）"""

    DL = ("[String Download] Attempting to load String from URL "
          "'https://pastebin.com/raw/E36sLedn'")
    DL_ALT = ("[String Download] Attempting to load String from URL "
              "'https://pastebin.com/raw/apPVH4KD'")

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _monitor(self, instance_type=None):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9000), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type or config.INSTANCE_PRIVATE
        monitor.st.round_end_seen = True
        return monitor

    def _started(self, monitor, line=None):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process(line or self.DL)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_download_line_is_parsed(self):
        event = LogParser.parse("2026.08.23 17:53:12 Debug      -  " + self.DL)

        self.assertEqual(event.kind, LogParser.EVENT_STRING_DOWNLOAD)
        self.assertEqual(event.url, "https://pastebin.com/raw/E36sLedn")

    def test_clearing_queue_line_does_not_match(self):
        """同じ [String Download] で始まる別の行を拾わないこと"""
        event = LogParser.parse("2026.08.23 17:53:12 Debug      -  "
                                "[String Download] Clearing string download queue")

        self.assertIsNone(event)

    def test_download_starts_both_modules_in_private(self):
        started = self._started(self._monitor(config.INSTANCE_PRIVATE))

        self.assertIn("do_speed_detect", started)
        self.assertIn("do_speed_strafe", started)

    def test_download_starts_detection_only_outside_private(self):
        started = self._started(self._monitor(config.INSTANCE_HOSHIIMO))

        self.assertIn("do_speed_detect", started)
        self.assertNotIn("do_speed_strafe", started)

    def test_download_before_round_end_starts_nothing(self):
        monitor = self._monitor()
        monitor.st.round_end_seen = False

        self.assertEqual(self._started(monitor), [])
        self.assertFalse(monitor.st.speed_probe_done)

    def test_three_downloads_in_one_round_start_it_once(self):
        """区間内にDLが複数来ても起動は1回だけ"""
        monitor = self._monitor()

        first = self._started(monitor)
        second = self._started(monitor)
        third = self._started(monitor)

        self.assertIn("do_speed_detect", first)
        self.assertEqual(second, [])
        self.assertEqual(third, [])

    def test_either_url_starts_it(self):
        """URLで絞っていないこと（ラウンドデータのURLは複数ある）"""
        for line in (self.DL, self.DL_ALT):
            started = self._started(self._monitor(), line)
            self.assertIn("do_speed_detect", started, line)

    def test_toggle_off_starts_nothing(self):
        SharedState.set_speed_detect(False)

        self.assertEqual(self._started(self._monitor()), [])

    def test_round_start_allows_the_next_round(self):
        monitor = self._monitor()
        self._started(monitor)
        self.assertTrue(monitor.st.speed_probe_done)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertFalse(monitor.st.speed_probe_done)

    def test_verified_no_longer_starts_the_probe(self):
        """Verified からは起動しない（起点の移設）"""
        monitor = self._monitor()

        started = self._started(monitor, "Verified")

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.speed_probe_done)

    def test_verified_still_marks_begin_done(self):
        """Verified の既存動作（自分がBeginを押せたかの記録）は残す"""
        monitor = self._monitor()

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("Verified")

        self.assertTrue(monitor.st.begin_done)

    def test_verified_periodic_guard_still_works(self):
        monitor = self._monitor()
        monitor.st.periodic_last = 1000.0
        monitor.st.periodic_period = 300.0

        with patch.object(LogMonitor.time, "time", return_value=1302.0), \
             patch.object(LogMonitor.threading, "Thread"):
            monitor._process("Verified")

        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(monitor.st.periodic_last, 1302.0)

    def test_verified_round_end_guard_still_works(self):
        monitor = self._monitor()
        monitor.st.round_end_seen = False

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("Verified")

        self.assertFalse(monitor.st.begin_done)

    def test_equip_wait_release_still_uses_begin_done(self):
        """装備待ちフリーズの解除は自分のBegin（Verified）を条件に残す"""
        monitor = self._monitor()
        monitor.st.waiting_for_equip = True
        monitor.st.begin_done = False

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Equipping 42.")
        targets = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]
        self.assertNotIn("_release_equip_wait_after_delay", targets,
                         "自分のBeginが無いうちは解除しないこと")

        monitor.st.begin_done = True
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Equipping 42.")
        targets = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]
        self.assertIn("_release_equip_wait_after_delay", targets)


class TestSpeedClassify(unittest.TestCase):
    """速度からラウンド種別を引く（値の照合だけ。張り付き判定は受信側）"""

    def test_normal_speed(self):
        self.assertEqual(ActionExecutor.classify_speed(6.6), "normal")

    def test_eight_pages_speed(self):
        self.assertEqual(ActionExecutor.classify_speed(6.5), "8pages")

    def test_punish_speed(self):
        self.assertEqual(ActionExecutor.classify_speed(4.0), "punish")

    def test_in_between_value_is_unknown(self):
        """帯で判定していないことの回帰: 6.52 はどれでもない"""
        self.assertEqual(ActionExecutor.classify_speed(6.52), "")

    def test_far_value_is_unknown(self):
        self.assertEqual(ActionExecutor.classify_speed(0.0), "")

    def test_small_jitter_still_matches(self):
        """一致の許容内なら拾う"""
        self.assertEqual(
            ActionExecutor.classify_speed(6.6 + config.SPEED_MATCH_TOL / 2), "normal")


class TestVelocityStability(unittest.TestCase):
    """張り付き（値が変化していない時間）は受信側が持つ"""

    def _receiver(self):
        return OSCReceiver.VelocityReceiver(19999)

    def _feed(self, r, value, at):
        """指定時刻にVelocityMagnitudeを受信したことにする"""
        with patch.object(OSCReceiver.time, "time", return_value=at):
            r._handle(OSCReceiver.VELOCITY_MAGNITUDE, value)

    def test_unreceived_is_none_and_zero(self):
        r = self._receiver()

        self.assertIsNone(r.stable_value)
        self.assertEqual(r.stable_for, 0.0)

    def test_stable_for_grows_while_the_value_holds(self):
        r = self._receiver()
        self._feed(r, 6.6, 1000.0)

        with patch.object(OSCReceiver.time, "time", return_value=1000.5):
            self.assertAlmostEqual(r.stable_for, 0.5)
            self.assertEqual(r.stable_value, 6.6)

    def test_jitter_within_tolerance_does_not_reset(self):
        """許容内の揺らぎで _stable_since を戻さない（戻すと永久に張り付かない）"""
        r = self._receiver()
        self._feed(r, 6.6, 1000.0)
        self._feed(r, 6.6 + config.SPEED_STICK_TOL / 2, 1000.2)

        with patch.object(OSCReceiver.time, "time", return_value=1000.4):
            self.assertAlmostEqual(r.stable_for, 0.4, places=6)
            self.assertEqual(r.stable_value, 6.6, "基準値は変えない")

    def test_change_beyond_tolerance_resets(self):
        r = self._receiver()
        self._feed(r, 6.6, 1000.0)
        self._feed(r, 4.0, 1000.3)

        with patch.object(OSCReceiver.time, "time", return_value=1000.31):
            self.assertEqual(r.stable_value, 4.0)
            self.assertAlmostEqual(r.stable_for, 0.01, places=6)

    def test_speed_and_ever_received_still_track_every_packet(self):
        r = self._receiver()
        self.assertFalse(r.ever_received)

        self._feed(r, 6.6, 1000.0)

        self.assertTrue(r.ever_received)
        self.assertEqual(r.speed, 6.6)


class TestSpeedDetect(unittest.TestCase):
    """判定モジュール: 受信するだけ。どのインスタンスでも動く"""

    def setUp(self):
        SharedState.set_speed_detect(True)
        SharedState.set_hands_free(False)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)
        SharedState.set_hands_free(False)

    class FakeReceiver:
        def __init__(self, value=None, stable_for=1.0, grounded=True,
                     ever_received=True, alive=True):
            self.stable_value = value
            self.stable_for = stable_for
            self.grounded = grounded
            self.ever_received = ever_received
            self.alive = alive
            self.stopped = False

        def stop(self):
            self.stopped = True

    def _executor(self, instance_type=None):
        cfg = WindowConfig(hwnd=123, osc_port=9000,
                           voice_8pages="8p.mp3", voice_punish="pn.mp3")
        st = WindowState(instance_type=instance_type or config.INSTANCE_PRIVATE)
        logs = []
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append), st, logs

    def _run(self, receiver, instance_type=None):
        ex, st, logs = self._executor(instance_type)
        ex._receiver = receiver
        calls = {"n": 0}

        def stop_after_one(_sec):
            calls["n"] += 1
            st.in_round = True      # 1周で打ち切る

        with patch.object(ActionExecutor.time, "sleep", side_effect=stop_after_one), \
             patch.object(ActionExecutor.PlaySound, "play_sound") as mock_play:
            ex.do_speed_detect()
        return st, logs, [c.args[0] for c in mock_play.call_args_list]

    def test_stable_eight_pages_is_announced(self):
        st, _logs, played = self._run(self.FakeReceiver(6.5))

        self.assertEqual(st.speed_round_kind, "8pages")
        self.assertEqual(played, ["8p.mp3"])

    def test_stable_punish_is_announced(self):
        st, _logs, played = self._run(self.FakeReceiver(4.0))

        self.assertEqual(st.speed_round_kind, "punish")
        self.assertEqual(played, ["pn.mp3"])

    def test_normal_is_not_announced(self):
        st, _logs, played = self._run(self.FakeReceiver(6.6))

        self.assertEqual(st.speed_round_kind, "normal")
        self.assertEqual(played, [])

    def test_not_stable_long_enough_is_ignored(self):
        """SPEED_STABLE_SEC に満たないうちは判定しない"""
        st, _logs, played = self._run(
            self.FakeReceiver(6.5, stable_for=config.SPEED_STABLE_SEC / 2))

        self.assertEqual(st.speed_round_kind, "")
        self.assertEqual(played, [])

    def test_stale_value_after_the_stream_dies_is_ignored(self):
        """途絶後は判定しない。

        stable_for は時間経過だけで伸びるので、送信が止まると凍結値が
        いつまでも「安定値」として通ってしまう。
        """
        st, _logs, played = self._run(
            self.FakeReceiver(4.0, stable_for=99.0, alive=False))

        self.assertEqual(st.speed_round_kind, "", "凍結値で判定してはいけない")
        self.assertEqual(played, [])

    def test_live_stream_still_decides(self):
        """受信が生きていれば従来どおり判定する（ガードで塞ぎすぎていない）"""
        st, _logs, played = self._run(
            self.FakeReceiver(4.0, stable_for=config.SPEED_STABLE_SEC, alive=True))

        self.assertEqual(st.speed_round_kind, "punish")
        self.assertEqual(played, ["pn.mp3"])

    def test_airborne_samples_are_ignored(self):
        st, _logs, played = self._run(self.FakeReceiver(6.5, grounded=False))

        self.assertEqual(st.speed_round_kind, "")
        self.assertEqual(played, [])

    def test_unreceived_is_ignored(self):
        st, _logs, played = self._run(self.FakeReceiver(None, ever_received=False))

        self.assertEqual(st.speed_round_kind, "")
        self.assertTrue(any("速度を受信できない" in m for m in _logs))

    def test_detect_runs_in_any_instance(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_PUBLIC):
            st, _logs, played = self._run(self.FakeReceiver(6.5), instance_type=itype)
            self.assertEqual(st.speed_round_kind, "8pages", itype)

    def test_hands_free_is_silent(self):
        SharedState.set_hands_free(True)
        st, _logs, played = self._run(self.FakeReceiver(6.5))

        self.assertEqual(st.speed_round_kind, "8pages", "判定自体はする")
        self.assertEqual(played, [])

    def test_toggle_off_does_nothing(self):
        SharedState.set_speed_detect(False)
        ex, st, _logs = self._executor()
        ex._receiver = self.FakeReceiver(6.5)

        with patch.object(ActionExecutor.time, "sleep"):
            ex.do_speed_detect()

        self.assertEqual(st.speed_round_kind, "")

    def test_without_a_receiver_it_does_nothing(self):
        ex, st, _logs = self._executor()
        ex._receiver = None

        with patch.object(ActionExecutor.time, "sleep"):
            ex.do_speed_detect()

        self.assertEqual(st.speed_round_kind, "")

    def test_detect_does_not_stop_the_shared_receiver(self):
        """常時監視なのでプローブ終了で止めない"""
        receiver = self.FakeReceiver(6.6)
        self._run(receiver)

        self.assertFalse(receiver.stopped)


class TestVelocityReceiverLifecycle(unittest.TestCase):
    """受信器は監視の開始から停止まで生かす"""

    def _executor(self, osc_port=9000):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(), lambda: True, logs.append)
        return ex, logs

    class FakeReceiver:
        def __init__(self, port, log=None):
            self.port = port
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True
            return True

        def stop(self):
            self.stopped = True

    def test_monitor_start_and_stop_control_the_receiver(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9010), {},
                                        lambda _m: None, window_idx=1)

        with patch.object(OSCReceiver, "VelocityReceiver", self.FakeReceiver), \
             patch.object(LogMonitor.threading, "Thread"):
            monitor.start()
            receiver = monitor._action._receiver
            self.assertIsNotNone(receiver, "監視開始で受信を始めること")
            self.assertTrue(receiver.started)
            self.assertEqual(receiver.port, 9011, "VRChatが送ってくる側のポート")

            monitor.stop()

        self.assertTrue(receiver.stopped, "監視停止で止めること")
        self.assertIsNone(monitor._action._receiver)

    def test_start_is_idempotent(self):
        ex, _logs = self._executor()

        with patch.object(OSCReceiver, "VelocityReceiver", self.FakeReceiver):
            self.assertTrue(ex.start_velocity_receiver())
            first = ex._receiver
            self.assertTrue(ex.start_velocity_receiver())

        self.assertIs(ex._receiver, first, "二重にbindしない")

    def test_no_osc_port_is_harmless(self):
        ex, logs = self._executor(osc_port=0)

        with patch.object(OSCReceiver, "VelocityReceiver") as mock_recv:
            self.assertFalse(ex.start_velocity_receiver())

        mock_recv.assert_not_called()
        self.assertIsNone(ex._receiver)
        self.assertTrue(ex._speed_ready.is_set(), "横移動を待たせないこと")

    def test_bind_failure_is_reported_once_and_harmless(self):
        ex, logs = self._executor()

        class DeadReceiver:
            def __init__(self, port, log=None):
                pass

            def start(self):
                return False

        with patch.object(OSCReceiver, "VelocityReceiver", DeadReceiver):
            self.assertFalse(ex.start_velocity_receiver())

        self.assertIsNone(ex._receiver)
        self.assertTrue(ex._speed_ready.is_set())
        self.assertEqual(len([m for m in logs if "速度受信を開始できない" in m]), 1)

    def test_stop_without_start_is_safe(self):
        ex, _logs = self._executor()

        ex.stop_velocity_receiver()

        self.assertIsNone(ex._receiver)


class TestSpeedStrafe(unittest.TestCase):
    """横移動モジュール: マクロなのでprivateのみ（起動側で判定）"""

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _executor(self, osc_port=9000, **st_kw):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, **st_kw)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        ex._speed_ready.set()   # 受信の準備は済んでいる前提
        return ex, st, logs

    def test_moves_right_once_then_left_once(self):
        """右 → 左 の1往復だけ。繰り返さない"""
        ex, _st, _logs = self._executor()
        moves = []

        with patch.object(ex, "move", side_effect=lambda d, sec: moves.append((d, sec))):
            ex.do_speed_strafe()

        self.assertEqual(moves, [("right", config.SPEED_PROBE_RIGHT_SEC),
                                 ("left", config.SPEED_PROBE_LEFT_SEC)])

    def test_item_lost_round_does_not_move(self):
        ex, _st, logs = self._executor(waiting_for_equip=True)

        with patch.object(ex, "move") as mock_move:
            ex.do_speed_strafe()

        mock_move.assert_not_called()
        self.assertTrue(any("横移動はしません" in m for m in logs))

    def test_toggle_off_stops_the_strafe(self):
        SharedState.set_speed_detect(False)
        ex, _st, _logs = self._executor()

        with patch.object(ex, "move") as mock_move:
            ex.do_speed_strafe()

        mock_move.assert_not_called()

    def test_no_osc_port_stops_the_strafe(self):
        ex, _st, _logs = self._executor(osc_port=0)

        with patch.object(ex, "move") as mock_move:
            ex.do_speed_strafe()

        mock_move.assert_not_called()


class TestSpeedProbeOrder(unittest.TestCase):
    """横移動は受信のbindが終わってから始める

    VRChatは値が変わったときしか送らないので、bind前に動き出すと立ち上がりの
    サンプルを永久に取りこぼす。
    """

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _executor(self, osc_port=9000):
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        return ex, st, logs

    def test_strafe_waits_until_the_receiver_is_ready(self):
        """準備できるまで move を呼ばない"""
        ex, _st, _logs = self._executor()
        moves = []
        done = threading.Event()

        def worker():
            with patch.object(ex, "move", side_effect=lambda d, sec: moves.append(d)):
                ex.do_speed_strafe()
            done.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.2)
        self.assertEqual(moves, [], "準備前に動いてはいけない")

        ex._speed_ready.set()

        self.assertTrue(done.wait(2.0))
        self.assertEqual(moves, ["right", "left"])

    def test_starting_the_receiver_makes_it_ready(self):
        """準備完了を伝えるのは受信の開始（監視開始時に1回）"""
        ex, _st, _logs = self._executor()

        class FakeReceiver:
            def __init__(self, port, log=None):
                pass

            def start(self):
                return True

            def stop(self):
                pass

        self.assertFalse(ex._speed_ready.is_set())
        with patch.object(OSCReceiver, "VelocityReceiver", FakeReceiver):
            ex.start_velocity_receiver()

        self.assertTrue(ex._speed_ready.is_set())

        ex.stop_velocity_receiver()
        self.assertFalse(ex._speed_ready.is_set(), "停止したら待ちに戻す")

    def test_strafe_moves_anyway_after_the_timeout(self):
        """待ち切れなくても移動する（受信が使えなくても横移動は他を壊さない）"""
        ex, _st, logs = self._executor()
        moves = []

        with patch.object(config, "SPEED_READY_TIMEOUT_SEC", 0.05), \
             patch.object(ex, "move", side_effect=lambda d, sec: moves.append(d)):
            ex.do_speed_strafe()

        self.assertEqual(moves, ["right", "left"], "止まってはいけない")
        self.assertTrue(any("準備を待てませんでした" in m for m in logs))


class TestOSCReceiverParse(unittest.TestCase):
    """VRChatからのOSCメッセージの読み取り"""

    def test_float_message(self):
        msg = OSCClient.OSCClient.build_message("/avatar/parameters/VelocityX", 1.5)
        self.assertEqual(OSCReceiver.parse_message(msg),
                         ("/avatar/parameters/VelocityX", 1.5))

    def test_broken_message_is_ignored(self):
        self.assertIsNone(OSCReceiver.parse_message(b"garbage"))

    def test_magnitude_message(self):
        msg = OSCClient.OSCClient.build_message(OSCReceiver.VELOCITY_MAGNITUDE, 6.6)
        address, value = OSCReceiver.parse_message(msg)

        self.assertEqual(address, OSCReceiver.VELOCITY_MAGNITUDE)
        self.assertAlmostEqual(value, 6.6, places=4)

    def test_grounded_defaults_to_true_when_never_received(self):
        """Groundedを持たないアバターでも機能を止めない"""
        self.assertTrue(OSCReceiver.VelocityReceiver(19999).grounded)

    def test_alive_is_true_right_after_start(self):
        """起動直後は受信0が正常。猶予内はTrueを返す"""
        r = OSCReceiver.VelocityReceiver(19999)
        r._started = time.time()

        self.assertTrue(r.alive)
        self.assertFalse(r.ever_received)

    def test_alive_is_false_after_the_grace_period(self):
        r = OSCReceiver.VelocityReceiver(19999)
        r._started = time.time() - config.SPEED_RECV_TIMEOUT_SEC - 1

        self.assertFalse(r.alive)

    def test_alive_follows_the_last_packet_once_received(self):
        r = OSCReceiver.VelocityReceiver(19999)
        r._started = time.time() - 999
        r._last_recv = time.time()

        self.assertTrue(r.alive, "受信が続いていれば生きている")

        r._last_recv = time.time() - config.SPEED_RECV_TIMEOUT_SEC - 1
        self.assertFalse(r.alive, "途絶したら死んでいる")

    def test_speed_is_none_before_receiving(self):
        self.assertIsNone(OSCReceiver.VelocityReceiver(19999).speed)


class TestHandsFreePerWindow(unittest.TestCase):
    """放置モードのトグルは全窓共通だが、効くのはprivate系の窓だけ

    干し芋の窓とプラベの窓を同時に監視する運用があるため、窓ごとに判定する。
    """

    def setUp(self):
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.equip_freeze_reset()

    def tearDown(self):
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.equip_freeze_reset()

    def _monitor(self, instance_type):
        cfg = WindowConfig(
            do_skip=True,
            voice_continue="continue.mp3",
            voice_fog="fog.mp3",
            voice_item_lost="lost.mp3",
            voice_foxy="foxy.mp3",
            voice_intermission="intermission.mp3",
            announce_intermission=True,
        )
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        return monitor

    def test_gate_is_per_window(self):
        SharedState.set_hands_free(True)
        self.assertTrue(self._monitor(config.INSTANCE_PRIVATE)._hands_free())
        self.assertFalse(self._monitor(config.INSTANCE_HOSHIIMO)._hands_free())
        self.assertFalse(self._monitor(config.INSTANCE_PUBLIC)._hands_free())

    def test_gate_is_off_when_the_toggle_is_off(self):
        self.assertFalse(self._monitor(config.INSTANCE_PRIVATE)._hands_free())

    def _announce_calls(self, instance_type):
        monitor = self._monitor(instance_type)
        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"), \
             patch.object(LogMonitor.threading, "Thread"):
            monitor._process("foxy the pirate turned evil!")
            monitor._process("Verified Round End")
        return [c.args[0] for c in mock_play.call_args_list]

    def test_private_window_is_silent_while_hands_free(self):
        SharedState.set_hands_free(True)
        self.assertEqual(self._announce_calls(config.INSTANCE_PRIVATE), [])

    def test_hoshiimo_window_still_announces_while_hands_free(self):
        """同時に監視している干し芋の窓は影響を受けない"""
        SharedState.set_hands_free(True)
        calls = self._announce_calls(config.INSTANCE_HOSHIIMO)

        self.assertIn("foxy.mp3", calls)
        self.assertIn("intermission.mp3", calls)

    def test_unknown_killers_suicide_only_in_private(self):
        """霧のテラー不明での即自爆もprivateの窓だけ"""
        SharedState.set_hands_free(True)
        line = "Killers is unknown - ??? // ??? // Round type is Fog"

        for itype, expected in ((config.INSTANCE_PRIVATE, True),
                                (config.INSTANCE_HOSHIIMO, False)):
            monitor = self._monitor(itype)
            with patch.object(LogMonitor.threading, "Thread") as mock_thread:
                monitor._process(line)
            self.assertEqual(mock_thread.called, expected, itype)

    def test_item_lost_announcement_follows_the_same_gate(self):
        """Beginクリック直前のロスト通知もprivateの窓だけ黙る"""
        SharedState.set_hands_free(True)
        played = []

        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO):
            cfg = WindowConfig(hwnd=123, voice_item_lost="lost.mp3")
            st = WindowState(instance_type=itype, waiting_for_equip=True, item_id=0)
            ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
            with patch.object(PlaySound, "play_sound") as mock_play:
                ex.announce_item_lost_if_needed()
            played.append(mock_play.called)

        self.assertEqual(played, [False, True], "privateだけ黙ること")

    def test_toggle_can_be_turned_on_regardless_of_instance(self):
        """GUIのトグル自体はインスタンスに関係なくONにできる"""
        app = type("FakeApp", (), {})()
        app.logs = []
        app._log = app.logs.append
        app.btn_hands_free = MagicMock()

        mainGUI.App._toggle_hands_free(app)

        self.assertTrue(SharedState.get_hands_free())


class TestEquipFreezeCounter(unittest.TestCase):
    """装備待ちフリーズのカウンタ管理（複数窓同時アイテムロスト対応）"""

    def setUp(self):
        SharedState.equip_freeze_reset()

    def tearDown(self):
        SharedState.equip_freeze_reset()

    def test_freeze_persists_until_all_windows_release(self):
        """2窓同時ロスト時、片方の解除だけでは全窓フリーズを解除しない"""
        st_a = WindowState()
        st_b = WindowState()
        SharedState.equip_freeze_start(st_a)
        SharedState.equip_freeze_start(st_b)
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(SharedState.get_equip_freeze_count(), 2)

        SharedState.equip_freeze_end(st_a)
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())  # ← Bの装備待ちが残っている

        SharedState.equip_freeze_end(st_b)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)

    def test_double_start_and_end_are_idempotent(self):
        """同一窓の多重登録・多重解除はカウントに影響しない"""
        st = WindowState()
        SharedState.equip_freeze_start(st)
        SharedState.equip_freeze_start(st)
        self.assertEqual(SharedState.get_equip_freeze_count(), 1)

        SharedState.equip_freeze_end(st)
        self.assertTrue(SharedState.EQUIP_WAIT_EVENT.is_set())
        SharedState.equip_freeze_end(st)
        self.assertEqual(SharedState.get_equip_freeze_count(), 0)

    def test_end_without_start_is_noop(self):
        """フリーズ未保持の窓の解除呼び出しは他窓のフリーズに影響しない"""
        holder = WindowState()
        bystander = WindowState()
        SharedState.equip_freeze_start(holder)

        SharedState.equip_freeze_end(bystander)
        self.assertFalse(SharedState.EQUIP_WAIT_EVENT.is_set())
        self.assertEqual(SharedState.get_equip_freeze_count(), 1)

# ═══════════════════════════════════════════════
#  LogParser.py
# ═══════════════════════════════════════════════
class TestLogParser(unittest.TestCase):
    def test_round_start_extracts_round_and_map_id(self):
        event = LogParser.parse("This round is taking place at Facility (12) and the round type is Fog")
        self.assertEqual(event.kind, LogParser.EVENT_ROUND_START)
        self.assertEqual(event.round_type, "Fog")
        self.assertEqual(event.map_id, 12)
        self.assertEqual(event.raw_map, "Facility (12)")

    def test_round_start_without_map_id_uses_zero(self):
        event = LogParser.parse("This round is taking place at Unknown Map and the round type is Run")
        self.assertEqual(event.kind, LogParser.EVENT_ROUND_START)
        self.assertEqual(event.map_id, 0)

    def test_killers_set_parses_terror_ids(self):
        event = LogParser.parse("Killers have been set - 1 2 3 // Round type is Double Trouble")
        self.assertEqual(event.kind, LogParser.EVENT_KILLERS_SET)
        self.assertEqual(event.round_type, "Double Trouble")
        self.assertEqual(event.terror_ids, [1, 2])

    def test_user_auth_and_joining_parse_after_prefix(self):
        prefix = "2026.05.24 10:00:00 Log - "
        user = LogParser.parse(prefix + "User Authenticated: tester (usr_12345678-1234-1234-1234-123456789abc)")
        joining = LogParser.parse(prefix + "[Behaviour] Joining wrld_abc:12345~friends~region(us)")
        self.assertEqual(user.kind, LogParser.EVENT_USER_AUTH)
        self.assertEqual(user.user_id, "usr_12345678-1234-1234-1234-123456789abc")
        self.assertEqual(user.player_name, "tester")
        self.assertEqual(joining.kind, LogParser.EVENT_JOINING)
        self.assertIn("~friends", joining.suffix)

    def test_sus_player_parses_name_and_second_slot(self):
        first = LogParser.parse("Sus player = 5 serim01")
        second = LogParser.parse("Sus player 2 = 13 urichata")

        self.assertEqual(first.kind, LogParser.EVENT_SUS_PLAYER)
        self.assertEqual(first.player_name, "serim01")

        self.assertEqual(second.kind, LogParser.EVENT_SUS_PLAYER)
        self.assertEqual(second.player_name, "urichata")

    def test_bloodthirsty_creature_log_parses(self):
        event = LogParser.parse(config.BLOODTHIRSTY_CREATURE_LOG)

        self.assertEqual(event.kind, LogParser.EVENT_CREATURE_BLOODTHIRSTY)

    def test_hungry_home_invader_log_parses(self):
        event = LogParser.parse(config.HUNGRY_HOME_INVADER_LOG)

        self.assertEqual(event.kind, LogParser.EVENT_HUNGRY_HOME_INVADER)

    def test_item_equip_parses_previous_item_id(self):
        event = LogParser.parse("Equipping 94. Was using 41")

        self.assertEqual(event.kind, LogParser.EVENT_ITEM_EQUIP)
        self.assertEqual(event.item_id, 94)
        self.assertEqual(event.previous_item_id, 41)

    def test_respawn_logs_parse(self):
        generic = LogParser.parse("Player respawned, opted out!")

        self.assertEqual(generic.kind, LogParser.EVENT_RESPAWN)
        self.assertEqual(generic.player_name, "")
        self.assertIsNone(LogParser.parse("[DEATH][serim01] serim01 was forcefully respawned."))

# ═══════════════════════════════════════════════
#  RoundDecision.py
# ═══════════════════════════════════════════════
class TestRoundDecision(unittest.TestCase):
    def test_normalize_killer_ids_applies_alternate_and_unbound_offsets(self):
        self.assertEqual(RoundDecision.normalize_killer_ids([1], "Alternate"), [135])
        self.assertEqual(RoundDecision.normalize_killer_ids([1], "Classic", "Unbound"), [201])

    def test_decide_killers_uses_tnl_and_open_special_target(self):
        keep_on = {"Classic/クラシック": {42}}
        decision = RoundDecision.decide_killers(keep_on, [42], "Classic", 0, False)
        self.assertTrue(decision.is_continue_round)
        self.assertFalse(decision.is_open_special_round_target)

        target_id = next(iter(config.OPEN_SPECIAL_ROUND_TERROR_IDS))
        special = RoundDecision.decide_killers({}, [target_id], "Classic", 0, True)
        self.assertTrue(special.is_continue_round)
        self.assertTrue(special.is_open_special_round_target)


class TestLogMonitorInstanceParsing(unittest.TestCase):
    def test_private_instance_suffixes(self):
        cases = [
            "~private",
            "~private(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)",
            "~private(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)~canRequestInvite",
            "~friends",
            "~friends(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)",
            "~hidden",
            "~hidden(usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3)",
            "~canRequestInvite",
        ]

        for suffix in cases:
            with self.subTest(suffix=suffix):
                self.assertEqual(LogMonitor.LogMonitor._parse_instance_type(suffix), config.INSTANCE_PRIVATE)

    def test_group_instance_suffixes(self):
        self.assertEqual(
            LogMonitor.LogMonitor._parse_instance_type(f"~group({config.HOSHIIMO_GROUP_ID})~groupAccessType(members)"),
            config.INSTANCE_HOSHIIMO,
        )
        self.assertEqual(
            LogMonitor.LogMonitor._parse_instance_type(f"~group({config.YAKIIMO_GROUP_ID})~groupAccessType(members)"),
            config.INSTANCE_YAKIIMO,
        )
        self.assertEqual(
            LogMonitor.LogMonitor._parse_instance_type("~group(grp_other)~groupAccessType(public)"),
            config.INSTANCE_OTHER_GROUP,
        )


class TestLogMonitorRuntimeHelpers(unittest.TestCase):
    def test_iter_log_lines_reversed_handles_chunk_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output_log.txt"
            path.write_text("first\nsecond\nthird\n", encoding="utf-8")

            lines = list(LogMonitor.LogMonitor._iter_log_lines_reversed(path, 5))

        self.assertEqual(lines, ["third", "second", "first"])

    def test_stop_sets_running_false_and_wakes_poll_wait(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _msg: None, window_idx=1)
        monitor._running = True
        monitor._stop_event.clear()

        monitor.stop()

        self.assertFalse(monitor._running)
        self.assertTrue(monitor._stop_event.is_set())

    def test_format_terror_ids_caches_name_lookup(self):
        LogMonitor._terror_name_cached.cache_clear()
        with patch.object(LogMonitor.ReadJson, "terror_name", return_value="Cached Terror") as mock_name:
            first = LogMonitor.format_terror_ids([9999])
            second = LogMonitor.format_terror_ids([9999])

        self.assertEqual(first, "Cached Terror")
        self.assertEqual(second, "Cached Terror")
        mock_name.assert_called_once_with(9999, config.TERRORS)


class TestLegacySettings(unittest.TestCase):
    """「干し芋自動自爆」チェックを消した後の古い settings.json"""

    LEGACY = {
        "tnl_path": "C:/list/my.tnl",
        "hoshiimo_skip": True,          # 消したキー
        "profiles": [1, 2],
        "freeze_8pages": True,
    }

    def test_a_legacy_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(self.LEGACY), encoding="utf-8")

            with patch.object(config, "SETTINGS_PATH", path):
                data = mainGUI.load_settings()

            self.assertEqual(data.get("tnl_path"), "C:/list/my.tnl")
            self.assertTrue(data.get("hoshiimo_skip"), "読めること自体は変わらない")

    def test_saving_drops_the_removed_key_without_failing(self):
        """保存時に書き直されるだけ。落ちない"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(self.LEGACY), encoding="utf-8")

            with patch.object(config, "SETTINGS_PATH", path):
                mainGUI.save_settings({**mainGUI.load_settings(),
                                       "tnl_path": "C:/list/other.tnl"})
                data = mainGUI.load_settings()

            self.assertEqual(data.get("tnl_path"), "C:/list/other.tnl")

    def test_window_config_has_no_hoshiimo_switch(self):
        self.assertFalse(hasattr(WindowConfig(), "hoshiimo_skip"))
        with self.assertRaises(TypeError):
            WindowConfig(hoshiimo_skip=True)


class TestSkipRoundsByType(unittest.TestCase):
    """privateのラウンド指定自爆（続行リストより優先、3クラ解放は例外）"""

    CLASSIC_KEY = "Classic/クラシック"
    DTM = LogMonitor.DTM_TERROR_ID

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, skip_rounds=("Classic",), exempt=False, do_skip=True,
                 cancel_afk=True, keep_on=None,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=do_skip, cancel_afk=cancel_afk,
                           voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds),
                           skip_variant_exempt=exempt)
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        return monitor

    def _killers(self, monitor, ids=(99,), round_type=None):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type or monitor.st.round_type,
                                revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 基本 ────────────────────────────────
    def test_an_unlisted_round_uses_the_normal_judgement(self):
        monitor = self._monitor(skip_rounds=("Bloodbath",),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_a_listed_round_is_skipped(self):
        monitor = self._monitor()

        started = self._killers(monitor)

        self.assertIn("do_skip", started)

    def test_the_skip_beats_the_keep_list(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)

    def test_auto_skip_off_does_not_skip(self):
        monitor = self._monitor(do_skip=False)

        started = self._killers(monitor)

        self.assertNotIn("do_skip", started)

    def test_no_announce_and_no_freeze(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})
        monitor.st.is_continue_round = True
        SharedState.continue_round_start()

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers([99], "Classic", revealed=False)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        mock_play.assert_not_called()

    def _run_delayed(self, monitor):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_round_skip("Classic", 0.0, monitor.st.round_seq)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── Variant例外 ────────────────────────
    def test_the_variant_exemption_falls_through(self):
        """例外ONのときは待ち明けに判断する。Variantなら通常判定へ"""
        monitor = self._monitor(exempt=True,
                                keep_on={self.CLASSIC_KEY: {config.ATRACHED_ID}})
        monitor._running = True
        monitor.st.terror_ids = [config.ATRACHED_ID]

        started = self._run_delayed(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_the_exemption_covers_all_four_variants(self):
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
                    config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID):
            monitor = self._monitor(exempt=True,
                                    keep_on={self.CLASSIC_KEY: {tid}})
            monitor._running = True
            monitor.st.terror_ids = [tid]

            started = self._run_delayed(monitor)

            self.assertNotIn("do_skip", started, tid)
            self.assertTrue(monitor.st.is_continue_round, tid)

    def test_the_exemption_still_skips_a_plain_terror(self):
        monitor = self._monitor(exempt=True)
        monitor.st.gigabytes = True     # 待ちを済ませた状態

        started = self._killers(monitor)

        self.assertIn("do_skip", started)

    def test_without_the_exemption_a_variant_is_skipped_too(self):
        monitor = self._monitor(exempt=False,
                                keep_on={self.CLASSIC_KEY: {config.ATRACHED_ID}})
        monitor.st.atrached_variant = True

        started = self._killers(monitor, [config.ATRACHED_ID])

        self.assertIn("do_skip", started)
        self.assertFalse(monitor.st.is_continue_round)

    # ── 3クラ解放が勝つ ───────────────────────
    def test_the_three_classic_unlock_wins(self):
        """DTMが出たラウンドは自爆指定を無視する（3クラ稼ぎを壊さない）"""
        monitor = self._monitor()

        started = self._killers(monitor, [self.DTM])

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_skip_applies_once_three_wins_are_done(self):
        monitor = self._monitor()
        monitor.st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS

        started = self._killers(monitor, [self.DTM])

        self.assertIn("do_skip", started)

    def test_the_skip_applies_when_cancel_afk_is_off(self):
        monitor = self._monitor(cancel_afk=False)

        started = self._killers(monitor, [self.DTM])

        self.assertIn("do_skip", started)

    # ── 他のインスタンス ──────────────────────
    def test_group_instances_are_untouched(self):
        """干し芋/焼き芋は GroupRound の結論が先に出る"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._monitor(skip_rounds=("8 Pages",),
                                    instance_type=itype)
            monitor.st.round_type = "8 Pages"

            started = self._killers(monitor, [1, 2])

            self.assertEqual(started, [], itype)   # 全続行のまま

    def test_public_is_untouched(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP):
            monitor = self._monitor(instance_type=itype)

            started = self._killers(monitor)

            self.assertEqual(started, [], itype)

    def test_hands_free_still_decides_first(self):
        """放置モードの3分岐はこの判定より前。そのまま"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(skip_rounds=())
        monitor.st.item_id = 0

        started = self._killers(monitor)

        self.assertIn("do_skip", started, "放置モードの即自爆が効いていること")

    # ── Variant待ち ────────────────────────
    def test_the_wait_happens_only_when_configured(self):
        monitor = self._monitor(exempt=True)

        started = self._killers(monitor)

        self.assertEqual(started, ["_delayed_round_skip"])

    def test_no_wait_without_the_exemption(self):
        monitor = self._monitor(exempt=False)

        self.assertIn("do_skip", self._killers(monitor))

    def test_no_wait_for_an_unlisted_round(self):
        """設定していない private の窓に待ちを増やさないこと"""
        monitor = self._monitor(skip_rounds=("Bloodbath",), exempt=True,
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertNotIn("_delayed_round_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_no_wait_when_nothing_is_selected(self):
        monitor = self._monitor(skip_rounds=(), exempt=True,
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertEqual(started, [])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_ends_in_a_skip_without_a_variant(self):
        monitor = self._monitor(exempt=True)
        monitor._running = True
        monitor.st.terror_ids = [99]

        self.assertIn("do_skip", self._run_delayed(monitor))

    def test_the_wait_falls_through_when_a_variant_arrives(self):
        monitor = self._monitor(exempt=True,
                                keep_on={self.CLASSIC_KEY: {config.GIGABYTES_ID}})
        monitor._running = True
        monitor.st.terror_ids = [config.GIGABYTES_ID]
        monitor.st.gigabytes = True

        started = self._run_delayed(monitor)

        self.assertEqual(started, [])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_aborts_when_the_round_changed(self):
        monitor = self._monitor(exempt=True)
        monitor._running = True
        monitor.st.terror_ids = [99]
        monitor.st.round_seq = 5

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_round_skip("Classic", 0.0, 4)

        mock_thread.assert_not_called()


class TestListSourceShared(unittest.TestCase):
    """続行リストの供給元は全窓共通の状態として持つ"""

    def setUp(self):
        SharedState.set_list_source(None)

    tearDown = setUp

    def test_it_starts_undecided(self):
        self.assertIsNone(SharedState.get_list_source())

    def test_it_round_trips(self):
        for src in ("host", "tnl", None):
            SharedState.set_list_source(src)
            self.assertEqual(SharedState.get_list_source(), src)

    def test_concurrent_writers_leave_a_valid_value(self):
        """ロックの有無を見る。壊れた値が残らないこと"""
        done = threading.Event()

        def spin(src):
            for _ in range(2000):
                SharedState.set_list_source(src)
                if SharedState.get_list_source() not in ("host", "tnl"):
                    done.set()
                    return

        SharedState.set_list_source("host")
        threads = [threading.Thread(target=spin, args=(s,), daemon=True)
                   for s in ("host", "tnl")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        self.assertFalse(done.is_set(), "想定外の値が観測された")
        self.assertIn(SharedState.get_list_source(), ("host", "tnl"))

    def test_the_gui_switch_updates_it(self):
        """mainGUI が供給元を切り替えたら SharedState 側も変わる"""
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.host_wishes = {}
        app._host_save_stamp = None
        app._host_save_warned = False
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app.v_tnl = TestHostListSource.FakeVar("")
        app._apply_keep_on = lambda new: mainGUI.App._apply_keep_on(app, new)
        app._apply_host_wishes = lambda new: mainGUI.App._apply_host_wishes(app, new)
        app._load_tnl = lambda **kw: None

        SharedState.set_list_source("host")
        mainGUI.App._fall_back_to_tnl(app, "テスト")

        self.assertEqual(SharedState.get_list_source(), "tnl")


class TestGroupNeedsHostList(unittest.TestCase):
    """干し芋/焼き芋は主催リストが無いと手を止める"""

    CLASSIC_KEY = "Classic/クラシック"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_HOSHIIMO, *,
                 voice="lost.mp3", keep_on=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost=voice)
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Bloodbath"
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids=(1, 2, 3)):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=False)
        self.played = mock_play
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 止まること ───────────────────────────
    def test_a_group_window_stops_without_the_host_list(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            for src in ("tnl", None):
                SharedState.set_list_source(src)
                monitor = self._monitor(itype)

                started = self._killers(monitor)

                self.assertEqual(started, [], f"{itype}/{src}")
                self.assertFalse(monitor.st.is_continue_round, f"{itype}/{src}")

    def test_the_normal_judgement_does_not_run_either(self):
        """tnlの内容で続行判定してアナウンスを出すのも誤り"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor(keep_on={"Bloodbath/ブラッドバス": {1}})

        self._killers(monitor, [1])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_the_host_list_lets_it_run(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._monitor(itype)

            started = self._killers(monitor)

            self.assertIn("do_skip", started, itype)   # Bloodbathは問答無用スキップ

    def test_private_is_untouched(self):
        for src in ("tnl", None):
            SharedState.set_list_source(src)
            monitor = self._monitor(config.INSTANCE_PRIVATE,
                                    keep_on={"Bloodbath/ブラッドバス": {1}})

            started = self._killers(monitor, [1])

            self.assertTrue(monitor.st.is_continue_round, src)
            self.assertNotIn("do_skip", started, src)

    def test_the_helper_is_false_for_private(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor(config.INSTANCE_PRIVATE)

        self.assertFalse(monitor._group_list_unavailable())

    # ── 通知 ────────────────────────────────
    def test_it_logs_and_plays_once(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._killers(monitor)

        self.assertEqual(
            len([m for m in monitor.logs if "主催リストが取れません" in m]), 1,
            monitor.logs)
        self.played.assert_called_once_with("lost.mp3")

    def test_three_rounds_still_notify_once(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        for _ in range(3):
            self._killers(monitor)

        self.assertEqual(
            len([m for m in monitor.logs if "主催リストが取れません" in m]), 1,
            monitor.logs)

    def test_the_terror_line_is_still_logged(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._killers(monitor)

        self.assertTrue(any("テラーset:" in m for m in monitor.logs), monitor.logs)

    def test_recovery_logs_once(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._killers(monitor)

        SharedState.set_list_source("host")
        self._killers(monitor)
        self._killers(monitor)

        back = [m for m in monitor.logs if "主催リストが戻りました" in m]
        self.assertEqual(len(back), 1, monitor.logs)
        self.assertFalse(monitor.st.list_lost_notified)

    def test_losing_it_again_notifies_again(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._killers(monitor)
        SharedState.set_list_source("host")
        self._killers(monitor)

        SharedState.set_list_source("tnl")
        self._killers(monitor)

        self.assertEqual(
            len([m for m in monitor.logs if "主催リストが取れません" in m]), 2,
            monitor.logs)

    def test_an_empty_voice_is_silent(self):
        """空文字は play_sound 側で弾かれる（他のアナウンスと同じ作り）"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor(voice="")

        self._killers(monitor)

        self.assertEqual(self.played.call_args.args, ("",))
        with patch.object(PlaySound, "threading") as mock_threading:
            PlaySound.play_sound("")
        mock_threading.Thread.assert_not_called()

    def test_hands_free_cannot_apply_to_a_group_window(self):
        """放置モードはprivateの窓だけ。グループの窓では抑制にならない"""
        SharedState.set_hands_free(True)
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._killers(monitor)

        self.assertFalse(monitor._hands_free())
        self.played.assert_called_once_with("lost.mp3")
        self.assertTrue(any("主催リストが取れません" in m for m in monitor.logs))

    def test_the_voice_defaults_to_empty(self):
        self.assertEqual(WindowConfig().voice_list_lost, "")
        self.assertEqual(config.VOICE_LIST_LOST, "")


class TestContinueRoundsByType(unittest.TestCase):
    """privateの「全続行するラウンド」。自爆もせず通常判定にも落とさない"""

    CLASSIC_KEY = "Classic/クラシック"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, continue_rounds=("Classic",), skip_rounds=(),
                 exempt=False, do_skip=True, keep_on=None,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds),
                           continue_rounds=set(continue_rounds),
                           skip_variant_exempt=exempt)
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        return monitor

    def _killers(self, monitor, ids=(99,)):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=False)
        self.played = mock_play
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_a_listed_round_does_not_self_destruct(self):
        started = self._killers(self._monitor())

        self.assertEqual(started, [])

    def test_nothing_else_happens_either(self):
        monitor = self._monitor()

        self._killers(monitor)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.played.assert_not_called()

    def test_it_does_not_fall_through_to_the_keep_list(self):
        """続行リストにテラーが無くても自爆しない"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {1}})

        started = self._killers(monitor, [99])

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.is_continue_round)

    def test_a_keep_listed_terror_still_announces_nothing(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {99}})

        self._killers(monitor, [99])

        self.assertFalse(monitor.st.is_continue_round)
        self.played.assert_not_called()

    def test_continue_beats_skip_when_both_are_set(self):
        """settings.json を手編集された場合の保険。自爆しない側に倒す"""
        monitor = self._monitor(continue_rounds=("Classic",),
                                skip_rounds=("Classic",))

        started = self._killers(monitor)

        self.assertEqual(started, [])

    def test_no_variant_wait_for_a_listed_round(self):
        """先に return するので Variant 待ちにも入らない"""
        monitor = self._monitor(continue_rounds=("Classic",),
                                skip_rounds=("Classic",), exempt=True)

        started = self._killers(monitor)

        self.assertNotIn("_delayed_round_skip", started)

    def test_an_unlisted_round_uses_the_normal_judgement(self):
        monitor = self._monitor(continue_rounds=("Bloodbath",),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_auto_skip_off_changes_nothing(self):
        monitor = self._monitor(do_skip=False)

        self.assertEqual(self._killers(monitor), [])

    def test_a_stale_continue_round_is_cleared(self):
        monitor = self._monitor()
        monitor.st.is_continue_round = True
        SharedState.continue_round_start()

        self._killers(monitor)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_group_instances_are_untouched(self):
        """干し芋/焼き芋は GroupRound の結論が先に出る"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._monitor(continue_rounds=("Bloodbath",),
                                    instance_type=itype)
            monitor.st.round_type = "Bloodbath"

            started = self._killers(monitor, [1, 2, 3])

            self.assertIn("do_skip", started, itype)   # 問答無用スキップのまま

    def test_public_is_untouched(self):
        for itype in (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP):
            monitor = self._monitor(instance_type=itype)

            self.assertEqual(self._killers(monitor), [], itype)

    def test_nothing_selected_keeps_the_old_behaviour(self):
        monitor = self._monitor(continue_rounds=(),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_the_window_config_defaults_to_nothing_selected(self):
        self.assertEqual(WindowConfig().continue_rounds, set())


class TestRoundListsAreExclusive(unittest.TestCase):
    """同じラウンドを両方に入れられないこと"""

    class Var:
        def __init__(self, value=False):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def test_checking_one_clears_the_other(self):
        skip, keep = self.Var(True), self.Var(True)

        mainGUI.exclusive_check(skip, keep)

        self.assertFalse(keep.get())
        self.assertTrue(skip.get())

    def test_unchecking_leaves_the_other_alone(self):
        skip, keep = self.Var(False), self.Var(True)

        mainGUI.exclusive_check(skip, keep)

        self.assertTrue(keep.get(), "外したときは相手を触らない")

    def test_it_works_in_both_directions(self):
        skip, keep = self.Var(True), self.Var(False)

        keep.set(True)
        mainGUI.exclusive_check(keep, skip)

        self.assertFalse(skip.get())

    def test_both_lists_use_the_same_round_order(self):
        self.assertEqual(list(mainGUI.skip_round_vars(lambda: object())),
                         config.SKIP_ROUND_SELECTABLE)


class TestSkipRoundsSettings(unittest.TestCase):
    """窓ごとの保存と復元"""

    class FakeVar:
        def __init__(self, value=False):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def _tab(self, names=(), exempt=False, keep=()):
        tab = type("FakeTab", (), {})()
        tab.v_profile = TestSkipRoundsSettings.FakeVar(0)
        tab.v_skip_rounds = {n: TestSkipRoundsSettings.FakeVar(n in names)
                             for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_continue_rounds = {n: TestSkipRoundsSettings.FakeVar(n in keep)
                                 for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_skip_variant_exempt = TestSkipRoundsSettings.FakeVar(exempt)
        return tab

    def test_the_selectable_list_drives_the_variables(self):
        made = mainGUI.skip_round_vars(lambda: object())

        self.assertEqual(list(made), config.SKIP_ROUND_SELECTABLE)

    def test_settings_are_saved_per_window(self):
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(("Classic", "Fog"), exempt=True), self._tab()]
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_instance_link",
                     "v_freeze_8pages", "v_freeze_punish"):
            setattr(app, name, TestSkipRoundsSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["skip_rounds"], [["Classic", "Fog"], []])
        self.assertEqual(saved["skip_variant_exempt"], [True, False])
        self.assertEqual(saved["continue_rounds"], [[], []])

    def test_saved_settings_are_restored_per_window(self):
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(), self._tab()]
        app._saved_profiles = []
        app._saved_skip_rounds = [["Classic", "Fog"], []]
        app._saved_skip_variant_exempt = [True, False]

        mainGUI.App._apply_saved_window_settings(app)

        first = {n for n, v in app.tabs[0].v_skip_rounds.items() if v.get()}
        second = {n for n, v in app.tabs[1].v_skip_rounds.items() if v.get()}
        self.assertEqual(first, {"Classic", "Fog"})
        self.assertEqual(second, set())
        self.assertTrue(app.tabs[0].v_skip_variant_exempt.get())
        self.assertFalse(app.tabs[1].v_skip_variant_exempt.get())

    def test_a_legacy_settings_file_without_the_keys_is_fine(self):
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(("Classic",), exempt=True)]
        app._saved_profiles = []

        mainGUI.App._apply_saved_window_settings(app)   # キーが無い状態

        self.assertTrue(app.tabs[0].v_skip_rounds["Classic"].get(),
                        "キーが無ければ触らないこと")

    def test_a_malformed_entry_is_ignored(self):
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(("Classic",))]
        app._saved_profiles = []
        app._saved_skip_rounds = ["Classic"]     # 文字列（配列ではない）
        app._saved_skip_variant_exempt = []

        mainGUI.App._apply_saved_window_settings(app)

        self.assertTrue(app.tabs[0].v_skip_rounds["Classic"].get())

    def test_the_window_config_defaults_to_nothing_selected(self):
        cfg = WindowConfig()

        self.assertEqual(cfg.skip_rounds, set())
        self.assertFalse(cfg.skip_variant_exempt)

    def test_continue_rounds_are_saved_and_restored(self):
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(keep=("8 Pages", "Run")), self._tab()]
        app._saved_profiles = []
        app._saved_continue_rounds = [["Fog"], []]

        mainGUI.App._apply_saved_window_settings(app)

        first = {n for n, v in app.tabs[0].v_continue_rounds.items() if v.get()}
        self.assertEqual(first, {"Fog"})
        self.assertEqual(
            {n for n, v in app.tabs[1].v_continue_rounds.items() if v.get()}, set())

    def test_a_round_in_both_lists_falls_to_continue(self):
        """settings.json を手編集された場合の保険"""
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(names=("Classic",))]
        app._saved_profiles = []
        app._saved_skip_rounds = [["Classic"]]
        app._saved_continue_rounds = [["Classic"]]

        mainGUI.App._apply_saved_window_settings(app)

        self.assertTrue(app.tabs[0].v_continue_rounds["Classic"].get())
        self.assertFalse(app.tabs[0].v_skip_rounds["Classic"].get(),
                         "自爆しない側に倒すこと")

    def test_a_legacy_file_without_continue_rounds_is_fine(self):
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(keep=("Run",))]
        app._saved_profiles = []

        mainGUI.App._apply_saved_window_settings(app)   # キーが無い状態

        self.assertTrue(app.tabs[0].v_continue_rounds["Run"].get(),
                        "キーが無ければ触らないこと")


class TestLogMonitorGroupRules(unittest.TestCase):
    """干し芋/焼き芋のラウンド判定を LogMonitor 越しに見る（第1部の統合側）

    「全続行」= 自爆しないだけ。続行アナウンスも他窓フリーズもしない。
    「通常判定」= 続行リストに無ければ自爆する。
    """

    DT_KEY = "Double Trouble/ダブルトラブル"
    FOG_KEY = "Fog/霧"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")   # グループ判定は主催リストが前提
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, do_skip=True, keep_on=None,
                 instance_type=config.INSTANCE_HOSHIIMO, host_wishes=None):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None,
                                        window_idx=1, host_wishes=host_wishes)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        return monitor

    def _killers(self, monitor, ids, killers_round_type=None, revealed=False):
        """_on_killers を回して、起動したスレッドの target 名を返す"""
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids),
                                killers_round_type or monitor.st.round_type,
                                revealed=revealed)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _round(self, monitor, round_type, ids=(99,), **kw):
        monitor.st.round_type = round_type
        return self._killers(monitor, ids, **kw)

    # ── 問答無用スキップ ──────────────────────
    def _classic_monitor(self, tid, keep_on=None,
                         instance_type=config.INSTANCE_HOSHIIMO):
        """テラーが確定した Classic ラウンド。

        Classicの1体構成は常に Gigabytes 待ちに入るので、判定は待ち明け
        （`_delayed_group_decision`）で出る。そこを直接動かして確かめる。
        """
        monitor = self._monitor(instance_type=instance_type, keep_on=keep_on)
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [tid]
        return monitor

    def test_classic_without_a_variant_is_skipped(self):
        """続行リストに載っていても問答無用でスキップする"""
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._classic_monitor(
                99, keep_on={"Classic/クラシック": {99}}, instance_type=itype)

            started = self._run_delayed(monitor)

            self.assertIn("do_skip", started, itype)
            self.assertFalse(monitor.st.is_continue_round, itype)

    def test_classic_with_each_variant_uses_the_keep_list(self):
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
                    config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID):
            monitor = self._classic_monitor(tid, keep_on={"Classic/クラシック": {tid}})

            started = self._run_delayed(monitor)

            self.assertTrue(monitor.st.is_continue_round, tid)
            self.assertNotIn("do_skip", started, tid)

    def test_classic_with_a_variant_still_skips_when_not_wanted(self):
        monitor = self._classic_monitor(config.ATRACHED_ID)

        started = self._run_delayed(monitor)

        self.assertIn("do_skip", started)

    def test_bloodbath_is_skipped(self):
        monitor = self._monitor()

        started = self._round(monitor, "Bloodbath", [1, 2, 3])

        self.assertIn("do_skip", started)

    def test_classic_exe_and_randomizer_are_skipped_even_with_a_variant(self):
        for round_type in ("Classic.exe", "Randomizer"):
            monitor = self._monitor()
            monitor.st.bloodthirsty_creature_variant = True

            started = self._round(monitor, round_type,
                                  [config.BLOODTHIRSTY_CREATURE_ID])

            self.assertIn("do_skip", started, round_type)

    # ── 全続行（自爆しないだけ） ───────────────
    def test_always_continue_rounds_do_nothing(self):
        for round_type in ("8 Pages", "Run"):
            monitor = self._monitor()

            with patch.object(PlaySound, "play_sound") as mock_play:
                started = self._round(monitor, round_type, [1, 2])

            self.assertEqual(started, [], round_type)
            self.assertFalse(monitor.st.is_continue_round, round_type)
            mock_play.assert_not_called()

    def test_always_continue_does_not_freeze_other_windows(self):
        monitor = self._monitor()

        self._round(monitor, "8 Pages", [1, 2])

        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_hoshiimo_fog_always_continues(self):
        monitor = self._monitor()

        started = self._round(monitor, "Fog", [7], killers_round_type="Fog",
                              revealed=True)

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.is_continue_round)

    def test_run_keeps_its_existing_round_start_behaviour(self):
        """「死亡待ち・アイテム購入予定」の既存挙動は変えない"""
        logs = []
        cfg = WindowConfig(do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, logs.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        monitor.st.is_continue_round = True

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Run")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertTrue(any("死亡待ち・アイテム購入予定" in m for m in logs), logs)

    # ── moon ────────────────────────────────
    def test_a_repeated_moon_is_skipped(self):
        monitor = self._monitor()
        monitor.st.moon_repeat = True

        started = self._round(monitor, "Blood Moon", [7])

        self.assertIn("do_skip", started)

    def test_hoshiimo_plays_every_first_moon(self):
        for moon in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice"):
            monitor = self._monitor()

            started = self._round(monitor, moon, [7])

            self.assertEqual(started, [], moon)

    def test_yakiimo_skips_the_first_mystic_moon_and_solstice(self):
        for moon in ("Mystic Moon", "Solstice"):
            monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)

            started = self._round(monitor, moon, [7])

            self.assertIn("do_skip", started, moon)

    def test_yakiimo_plays_the_first_blood_moon_and_twilight(self):
        for moon in ("Blood Moon", "Twilight"):
            monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)

            started = self._round(monitor, moon, [7])

            self.assertEqual(started, [], moon)

    # ── 焼き芋 Fog ──────────────────────────
    def test_yakiimo_fog_revealed_as_alternate_uses_the_keep_list(self):
        alternate = MatchTNL.ALTERNATE_OFFSET + 3
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO,
                                keep_on={self.FOG_KEY: {alternate}})
        monitor.st.round_type = "Fog"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers([3], "Fog (Alternate)", revealed=True)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual([c.kwargs["target"].__func__.__name__
                          for c in mock_thread.call_args_list
                          if "target" in c.kwargs], [])

    def test_yakiimo_fog_revealed_as_alternate_but_unwanted_is_skipped(self):
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)
        monitor.st.round_type = "Fog"

        started = self._killers(monitor, [3], "Fog (Alternate)", revealed=True)

        self.assertIn("do_skip", started)

    def test_yakiimo_fog_revealed_as_plain_fog_is_skipped(self):
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO,
                                keep_on={self.FOG_KEY: {7}})
        monitor.st.round_type = "Fog"

        started = self._killers(monitor, [7], "Fog", revealed=True)

        self.assertIn("do_skip", started)

    def test_foxy_in_fog_still_follows_the_fog_rule(self):
        """Foxy検出は st.round_type を Fog (Alternate) に書き換えてから
        _on_killers を呼ぶ。書き換え後も Fog の行から外れないこと"""
        monitor = self._monitor()      # 干し芋 → Fog は全続行
        monitor.st.round_type = "Fog"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("foxy the pirate turned evil!")

        self.assertEqual(monitor.st.round_type, "Fog (Alternate)")
        self.assertEqual([c.kwargs["target"].__func__.__name__
                          for c in mock_thread.call_args_list
                          if "target" in c.kwargs], [],
                         "全続行のまま。自爆も通常判定も走らせないこと")

    def test_killers_unknown_decides_nothing(self):
        """Fogは68ラウンド中63で revealed が来ない。その間は何もしない"""
        monitor = self._monitor(instance_type=config.INSTANCE_YAKIIMO)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Killers is unknown - ??? // x // Round type is Fog")

        mock_thread.assert_not_called()
        self.assertEqual(monitor.st.round_type, "Fog")

    # ── 通常判定（続行リスト照合） ──────────────
    def test_normal_judgement_continues_and_announces(self):
        monitor = self._monitor(keep_on={self.DT_KEY: {42}})

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor.st.round_type = "Double Trouble"
            monitor._on_killers([42], "Double Trouble", revealed=False)

        self.assertTrue(monitor.st.is_continue_round)
        mock_play.assert_called_once_with("continue.mp3")

    def test_normal_judgement_skips_when_not_wanted(self):
        """挙動が変わるところ: グループでも続行リストに無ければ自爆する"""
        monitor = self._monitor(keep_on={self.DT_KEY: {42}})

        started = self._round(monitor, "Double Trouble", [99])

        self.assertIn("do_skip", started)

    def test_auto_skip_off_never_skips(self):
        monitor = self._monitor(do_skip=False)

        started = self._round(monitor, "Double Trouble", [99])

        self.assertNotIn("do_skip", started)

    def test_auto_skip_off_also_blocks_the_group_skip(self):
        """cfg.do_skip は全体スイッチ。問答無用スキップもここで止まる"""
        monitor = self._monitor(do_skip=False)

        started = self._round(monitor, "Bloodbath", [1, 2, 3])

        self.assertNotIn("do_skip", started)

    def test_private_is_unaffected(self):
        """private では新ルールが一切効かない（Classicでも続行リスト次第）"""
        monitor = self._monitor(instance_type=config.INSTANCE_PRIVATE,
                                keep_on={"Classic/クラシック": {99}})

        started = self._round(monitor, "Classic", [99])

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_public_still_only_gets_the_voice(self):
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC)

        started = self._round(monitor, "Classic", [99])

        self.assertNotIn("do_skip", started)

    def test_hands_free_stays_private_only(self):
        """放置モードの自動操作は private 限定のまま"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(keep_on={self.DT_KEY: {42}})

        started = self._round(monitor, "Double Trouble", [42])

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round, "放置モードを通っていないこと")

    # ── Variant判定待ち ────────────────────
    def test_a_single_terror_classic_waits_for_gigabytes(self):
        """元IDが毎回違うのでIDから予測できない。1体構成は常に待つ"""
        monitor = self._monitor()

        started = self._round(monitor, "Classic", [99])

        self.assertEqual(started, ["_delayed_group_decision"])

    def test_every_round_waits_for_the_variant(self):
        """ラウンド種別で待ちを分けない。0.3秒で、確定した時点で打ち切る"""
        for round_type, ids in (("Bloodbath", [config.CURIOUS_CREATURE_ID, 2, 3]),
                                ("8 Pages", [config.CURIOUS_CREATURE_ID, 2])):
            monitor = self._monitor()

            started = self._round(monitor, round_type, ids)

            self.assertEqual(started, ["_delayed_group_decision"], round_type)

    def test_the_wait_still_reaches_the_same_answer(self):
        """待ち明けの結論は待たない場合と同じ（Bloodbathは問答無用スキップ）"""
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Bloodbath"
        monitor.st.terror_ids = [config.CURIOUS_CREATURE_ID, 2, 3]

        self.assertIn("do_skip", self._run_delayed(monitor, "Bloodbath"))

    def test_the_classic_wait_is_about_a_second(self):
        """実測ではVariantの出現ログは Killers行と同じ秒に出る"""
        monitor = self._monitor()
        monitor.st.round_type = "Classic"

        self.assertLessEqual(monitor._variant_wait_sec(), 1.0)

    def _run_delayed(self, monitor, killers_round_type="Classic", wait_sec=0.0):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_group_decision(killers_round_type, wait_sec,
                                            monitor.st.round_seq)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_the_wait_length_is_one_value(self):
        """待つのは Classic だけになったので、ラウンド種別で変えない"""
        monitor = self._monitor()
        waits = []
        for round_type in ("Classic", "Bloodbath", "未知のラウンド"):
            monitor.st.round_type = round_type
            waits.append(monitor._variant_wait_sec())

        self.assertEqual(set(waits), {config.TERROR_VARIANT_WAIT_SEC})
        self.assertLessEqual(config.TERROR_VARIANT_WAIT_SEC, 1.0,
                             "出現ログは Killers行と同じ秒に出る")

    def test_the_wait_ends_in_a_skip_when_no_marker_arrives(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]

        self.assertIn("do_skip", self._run_delayed(monitor))

    def test_the_wait_is_cancelled_by_the_gigabytes_line(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]
        monitor.keepOn_set["Classic/クラシック"] = {config.GIGABYTES_ID}
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("The Gigabytes have come.")

        self.assertEqual(self._run_delayed(monitor), [],
                         "問答無用スキップではなく通常判定に回ること")
        self.assertEqual(monitor.st.terror_ids, [config.GIGABYTES_ID])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_is_cancelled_by_the_atrached_line(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.SONIC_ID]
        monitor.keepOn_set["Classic/クラシック"] = {config.ATRACHED_ID}
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("Lets play a game...")
        monitor.st.gigabytes = True     # Gigabytes待ちは別。ここでは切り離す

        self.assertEqual(self._run_delayed(monitor), [],
                         "問答無用スキップではなく通常判定に回ること")
        self.assertEqual(monitor.st.terror_ids, [config.ATRACHED_ID])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_aborts_when_the_round_changed(self):
        monitor = self._monitor()
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]
        monitor.st.round_seq = 5

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_group_decision("Classic", 0.0, 4)

        mock_thread.assert_not_called()

    def test_the_wait_falls_through_to_the_normal_judgement(self):
        """Variantが確定したら通常判定へ回すこと（待ちの間に抜けている）"""
        monitor = self._monitor(keep_on={"Classic/クラシック": {config.ATRACHED_ID}})
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.ATRACHED_ID]
        monitor.st.atrached_variant = True
        monitor.st.gigabytes = True

        started = self._run_delayed(monitor)

        self.assertEqual(started, [])
        self.assertTrue(monitor.st.is_continue_round)

    # ── 既存の取りこぼし対策 ──────────────────
    def test_round_start_clears_stale_continue_state(self):
        """グループルールとは独立した既存挙動。ラウンド開始で続行状態を落とす"""
        monitor = self._monitor()
        monitor.st.is_continue_round = True

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(monitor.st.round_type, "Classic")

    def test_group_skip_clears_stale_continue_state(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"
        monitor.st.is_continue_round = True
        SharedState.continue_round_start()

        self._killers(monitor, [1, 2, 3])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)


class TestLogMonitorBeginDone(unittest.TestCase):
    """`Verified` はBegin受理以外に、約300秒周期の定期シグナルでも出る

    予測に使うのは「前回の“定期”からの間隔」。直前のVerifiedからの間隔で
    見ると、定期の直前に入るラウンド由来のVerified（24〜83秒間隔）に隠れて
    まったく検出できない。
    """

    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_end_seen = True
        return monitor

    def _verified_at(self, monitor, now: float):
        with patch.object(LogMonitor.time, "time", return_value=now):
            monitor._process("Verified")

    def test_periodic_signal_is_ignored(self):
        """前回の定期から周期ぶん経って来たVerifiedは棄却する"""
        monitor = self._monitor()
        monitor.st.periodic_last = 1000.0
        monitor.st.periodic_period = 300.0

        self._verified_at(monitor, 1302.0)   # 予測1300±8以内

        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(monitor.st.periodic_last, 1302.0, "位相を更新すること")

    def test_verified_61_seconds_after_periodic_is_accepted(self):
        """実障害と同じ条件: 直前のVerifiedから61秒でも本物として通す

        「直前のVerifiedから300秒」で判定すると、このケースを取り逃がす。
        """
        monitor = self._monitor()
        monitor.st.periodic_last = 1000.0
        monitor.st.periodic_period = 300.0

        self._verified_at(monitor, 1061.0)

        self.assertTrue(monitor.st.begin_done)
        self.assertEqual(monitor.st.pending_verified_time, 1061.0)

    def test_everything_received_clears_the_pending_check(self):
        """Everything recieved が続けば、その採用は正しかった"""
        monitor = self._monitor()
        self._verified_at(monitor, 1061.0)
        self.assertEqual(monitor.st.pending_verified_time, 1061.0)

        monitor._process("Everything recieved, looks good to meee~!")

        self.assertEqual(monitor.st.pending_verified_time, 0.0)

    def test_missing_everything_received_learns_the_phase(self):
        """Everything recieved が来なければ、あれは定期シグナルだった

        棄却したものだけで学習すると最初の1件を掴めず位相が永久に定まらない。
        この事後学習がブートストラップの唯一の手段。
        """
        monitor = self._monitor()
        self._verified_at(monitor, 1000.0)
        self.assertEqual(monitor.st.periodic_last, 0.0, "この時点ではまだ未学習")

        # 待ち時間ぎりぎりでは学習しない
        with patch.object(LogMonitor.time, "time",
                          return_value=1000.0 + config.VERIFIED_RECV_TIMEOUT_SEC):
            monitor._check_pending_verified()
        self.assertEqual(monitor.st.periodic_last, 0.0)

        with patch.object(LogMonitor.time, "time",
                          return_value=1000.0 + config.VERIFIED_RECV_TIMEOUT_SEC + 1):
            monitor._check_pending_verified()

        self.assertEqual(monitor.st.periodic_last, 1000.0, "Verifiedの時刻で位相を取る")
        self.assertEqual(monitor.st.pending_verified_time, 0.0)

    def test_out_of_range_intervals_do_not_update_the_period(self):
        """範囲外の間隔は周期学習に混ぜない（位相だけ更新する）"""
        monitor = self._monitor()
        monitor.st.periodic_last = 1000.0
        monitor.st.periodic_period = 300.0

        monitor._learn_periodic(1000.0 + config.VERIFIED_PERIODIC_MIN_SEC - 1)
        self.assertEqual(monitor.st.periodic_period, 300.0, "短すぎる間隔は無視")

        monitor.st.periodic_last = 2000.0
        monitor._learn_periodic(2000.0 + config.VERIFIED_PERIODIC_MAX_SEC + 1)
        self.assertEqual(monitor.st.periodic_period, 300.0, "長すぎる間隔も無視")

        # 範囲内なら指数平滑で追従する
        monitor.st.periodic_last = 3000.0
        monitor._learn_periodic(3310.0)
        self.assertAlmostEqual(monitor.st.periodic_period, 0.7 * 300.0 + 0.3 * 310.0)


class TestLogMonitorItemLostVoice(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")   # グループ判定は主催リストが前提
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, auto_begin: bool = False):
        cfg = WindowConfig(
            auto_begin=auto_begin,
            voice_item_lost="lost.mp3",
        )
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def test_hands_free_auto_begin_defers_voice_to_the_begin_click(self):
        """放置モード+自動Begin: Round Endでは鳴らさず、Beginクリック時に鳴らす"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(auto_begin=True)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play,              patch.object(ConnectDB, "send_ToNRoundStatistics"),              patch.object(LogMonitor.threading, "Thread"):
            monitor._process("You died.")
            monitor._process("RoundOver")
            monitor._process("Verified Round End")

        mock_play.assert_not_called()
        self.assertFalse(monitor.st.item_lost_announced)
        # Beginクリック時の判定材料は残っていること
        self.assertTrue(monitor.st.item_lost_this_round)
        self.assertEqual(monitor.st.item_id, 0)

    def test_item_lost_voice_does_not_play_on_verified_end_when_auto_begin_disabled(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("Verified Round End")

        mock_play.assert_not_called()
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)

    def test_item_lost_voice_plays_on_round_over_when_auto_begin_disabled(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("You died.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertTrue(monitor.st.item_lost_announced)

    def test_run_survival_does_not_play_item_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("Lived in round.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 7)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)
        self.assertTrue(monitor.st.lived_this_round)

    def test_run_death_marks_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        monitor._process("You died.")

        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)
        self.assertTrue(monitor.st.died_this_round)

    def test_run_without_death_does_not_play_item_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 7)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)

    def test_item_lost_voice_plays_on_round_over_without_verified_end(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("You died.")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.item_lost_announced)

    def test_death_does_not_mark_item_lost_by_itself(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Classic"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("You died.")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 7)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.died_this_round)
        self.assertFalse(monitor.st.item_equipped_after_death)

    def test_item_equip_after_death_prevents_round_over_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("You died.")
            monitor._process("Equipping 42.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 42)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.item_equipped_after_death)

    def test_sabotage_sus_player_self_marks_item_lost_on_round_start(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Sus player = 5 serim01")
            monitor._process("This round is taking place at Cheese Maze (59) and the round type is Sabotage")
            self.assertEqual(monitor.st.item_id, 0)
            self.assertTrue(monitor.st.sabotage_murder_this_round)
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertTrue(monitor.st.waiting_for_equip)

    def test_sabotage_sus_player_second_slot_can_mark_self(self):
        monitor = self._monitor(auto_begin=False)
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        monitor._process("Sus player = 5 other")
        monitor._process("Sus player 2 = 13 serim01")
        monitor._process("This round is taking place at Ancient (18) and the round type is Sabotage")

        self.assertTrue(monitor.st.sabotage_murder_this_round)
        self.assertEqual(monitor.st.item_id, 0)

    def test_sabotage_sus_player_other_does_not_mark_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Sus player = 5 other")
            monitor._process("This round is taking place at Cheese Maze (59) and the round type is Sabotage")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 10)
        self.assertFalse(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.sabotage_murder_this_round)

    def test_sabotage_murder_re_equip_prevents_round_over_lost_voice(self):
        monitor = self._monitor(auto_begin=False)
        monitor._process("User Authenticated: serim01 (usr_12345678-1234-1234-1234-123456789abc)")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Sus player = 5 serim01")
            monitor._process("This round is taking place at Cheese Maze (59) and the round type is Sabotage")
            monitor._process("Equipping 42.")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 42)
        self.assertFalse(monitor.st.waiting_for_equip)

    def test_punished_marks_item_lost_on_round_start(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10

        monitor._process("This round is taking place at Astral (13) and the round type is Punished")

        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)

    def test_eight_pages_kept_item_does_not_mark_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 10

        with patch.object(config, "EIGHT_PAGES_KEEP_ITEM_IDS", {10}):
            monitor._process("This round is taking place at Warehouse (0) and the round type is 8 Pages")

        self.assertEqual(monitor.st.item_id, 10)
        self.assertFalse(monitor.st.item_lost_this_round)

    def test_respawn_marks_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.in_round = True
        monitor.st.item_id = 10

        monitor._process("Player respawned, opted out!")
        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)

    def test_randomizer_item_change_warns_without_marking_item_lost(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 41
        monitor._process("This round is taking place at Secret (5) and the round type is Randomizer")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Equipping 94. Was using 41")
            monitor._process("Verified Round End")
            self.assertEqual(monitor.st.item_id, 94)
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertEqual(monitor.st.item_id, 94)
        self.assertTrue(monitor.st.randomizer_item_changed)
        self.assertFalse(monitor.st.item_lost_this_round)
        self.assertTrue(monitor.st.waiting_for_equip)

    def test_randomizer_restoring_original_item_clears_warning(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_id = 41
        monitor._process("This round is taking place at Secret (5) and the round type is Randomizer")

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Equipping 94. Was using 41")
            monitor._process("Equipping 41. Was using 94")
            monitor._process("RoundOver")

        mock_play.assert_not_called()
        self.assertEqual(monitor.st.item_id, 41)
        self.assertFalse(monitor.st.randomizer_item_changed)
        self.assertFalse(monitor.st.waiting_for_equip)

    def test_auto_begin_item_lost_voice_waits_until_begin_action(self):
        monitor = self._monitor(auto_begin=True)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"), \
             patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("You died.")
            monitor._process("RoundOver")          # Begin処理はここで起動する
            monitor._process("Verified Round End") # ロスト判定はここ

        mock_play.assert_not_called()
        mock_thread.assert_called_once()
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertFalse(monitor.st.item_lost_announced)

    def test_round_end_flag_gates_the_click(self):
        """クリックは Verified Round End を待つ。移動だけ先に進む。"""
        monitor = self._monitor(auto_begin=True)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"

        with patch.object(ConnectDB, "send_ToNRoundStatistics"),              patch.object(LogMonitor.threading, "Thread"):
            monitor._process("RoundOver")
            self.assertFalse(monitor.st.round_end_seen,
                             "RoundOver時点ではまだクリックできない")
            monitor._process("Verified Round End")
            self.assertTrue(monitor.st.round_end_seen,
                            "Round Endでクリック可になる")

    def test_round_over_time_is_recorded(self):
        """Begin待ちの起点として RoundOver の時刻を記録する。

        RoundOverから待機し、待ち終わる頃に Verified Round End が出て
        クリックできる状態になる、という組み立てのため。
        """
        monitor = self._monitor(auto_begin=True)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"

        with patch.object(ConnectDB, "send_ToNRoundStatistics"),              patch.object(LogMonitor.threading, "Thread"):
            monitor._process("RoundOver")
        self.assertGreater(monitor.st.round_over_time, 0,
                           "RoundOverの時刻を記録すること（Begin待ちの起点）")

    def test_yakiimo_plays_item_lost_voice_on_round_over_with_auto_begin_enabled(self):
        monitor = self._monitor(auto_begin=True)
        monitor.st.instance_type = config.INSTANCE_YAKIIMO
        monitor.st.in_round = True
        monitor.st.round_type = "Run"
        monitor.st.item_id = 7

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("You died.")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.item_lost_announced)

    def test_item_lost_voice_is_not_duplicated_by_round_over(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("You died.")
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")

    def test_round_start_resets_item_lost_voice_announcement(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_lost_announced = True
        monitor.st.item_lost_this_round = True
        monitor.st.randomizer_item_changed = True
        monitor.st.died_this_round = True
        monitor.st.lived_this_round = True
        monitor.st.item_equipped_after_death = True
        monitor.st.pending_sabotage_murder = True
        monitor.st.sabotage_murder_this_round = True

        monitor._process("This round is taking place at Facility (12) and the round type is Classic")

        self.assertFalse(monitor.st.item_lost_announced)
        self.assertFalse(monitor.st.item_lost_this_round)
        self.assertFalse(monitor.st.randomizer_item_changed)
        self.assertFalse(monitor.st.died_this_round)
        self.assertFalse(monitor.st.lived_this_round)
        self.assertFalse(monitor.st.item_equipped_after_death)
        self.assertFalse(monitor.st.pending_sabotage_murder)
        self.assertFalse(monitor.st.sabotage_murder_this_round)


class TestLogMonitorFogRound(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")   # グループ判定は主催リストが前提
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(self, *, keep_on: dict | None = None):
        cfg = WindowConfig(
            do_skip=True,
            voice_fog="fog.mp3",
            voice_continue="continue.mp3",
        )
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def test_fog_reveal_skip_releases_fog_freeze_before_skip(self):
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set())

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())
        mock_thread.assert_called_once()

    def _release_delay_for(self, monitor) -> float:
        """_release_continue_freeze_after_delay が実際に眠る秒数を取り出す"""
        monitor._running = True     # 監視スレッド起動後と同じ状態にする
        slept: list[float] = []
        with patch.object(LogMonitor.time, "sleep", side_effect=slept.append):
            monitor._release_continue_freeze_after_delay(monitor.st.round_seq)
        return slept[0]

    def test_fog_freeze_releases_sooner_than_continue_round(self):
        """霧ラウンド（テラー不明のまま）の解除猶予は続行ラウンドより短い"""
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")
            monitor._process("Killers is unknown - ??? // ??? // Round type is Fog")

        self.assertTrue(monitor.st.fog)
        self.assertEqual(self._release_delay_for(monitor),
                         config.FOG_FREEZE_RELEASE_DELAY_SEC)
        self.assertFalse(monitor.st.is_continue_round, "解除まで走ること")

    def test_continue_round_freeze_uses_the_longer_delay(self):
        """テラーが判明した続行ラウンドは長い方の猶予を使う"""
        monitor = self._monitor(keep_on={"Fog/霧": {44}})
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertFalse(monitor.st.fog, "テラー判明後は霧扱いしない")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(self._release_delay_for(monitor),
                         config.CONTINUE_FREEZE_RELEASE_DELAY_SEC)

    def test_fog_reveal_continue_does_not_double_count_freeze(self):
        monitor = self._monitor(keep_on={"Fog/霧": {44}})
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertEqual(SharedState.get_continue_round_count(), 1)

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1)
        mock_play.assert_not_called()


class TestLogMonitorPerWindowInstanceType(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")   # グループ判定は主催リストが前提
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        SharedState.set_list_source(None)
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def test_private_skip_is_not_blocked_by_hoshiimo_global_state(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        cfg = WindowConfig(do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Classic", revealed=False)

        mock_thread.assert_called_once()


class TestLogMonitorStatisticsRegistration(unittest.TestCase):
    def _monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _msg: None, window_idx=1)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.map_id = 12
        monitor.st.transformed_uid = 99
        return monitor

    def test_statistics_are_sent_when_killers_are_known(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)

        mock_send.assert_called_once_with("Classic", [1], 12, 99)
        self.assertTrue(monitor.st.statistics_sent)

    def test_statistics_are_sent_only_once_per_round(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)
            monitor._on_killers([2], "Classic", revealed=True)

        mock_send.assert_called_once_with("Classic", [1], 12, 99)
        self.assertEqual(monitor.st.terror_ids, [1, 2])

    def test_round_start_resets_statistics_sent_flag(self):
        monitor = self._monitor()
        monitor.st.statistics_sent = True
        monitor.st.bloodthirsty_creature_variant = True
        monitor.st.hungry_home_invader_variant = True

        monitor._process("This round is taking place at Facility (12) and the round type is Classic")

        self.assertFalse(monitor.st.statistics_sent)
        self.assertFalse(monitor.st.bloodthirsty_creature_variant)
        self.assertFalse(monitor.st.hungry_home_invader_variant)

    def test_bloodthirsty_log_before_killers_converts_curious_creature(self):
        monitor = self._monitor()
        monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99)

    def test_bloodthirsty_log_after_killers_updates_delayed_statistics(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            mock_send.assert_not_called()

            monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99)

    def test_bloodthirsty_variant_is_not_limited_to_classic(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Bloodbath", revealed=False)
            mock_send.assert_not_called()
            monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Bloodbath", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99)

    def test_hungry_home_invader_log_after_classic_slender_converts_id(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)
            mock_send.assert_not_called()
            monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_send.assert_called_once_with("Classic", [config.HUNGRY_HOME_INVADER_ID], 12, 99)

    def test_hungry_home_invader_log_before_classic_slender_converts_id(self):
        monitor = self._monitor()
        monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_send.assert_called_once_with("Classic", [config.HUNGRY_HOME_INVADER_ID], 12, 99)

    def test_hungry_home_invader_is_ignored_outside_classic(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Bloodbath", revealed=False)
            monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.SLENDER_ID])
        self.assertFalse(monitor.st.hungry_home_invader_variant)
        mock_send.assert_called_once_with("Bloodbath", [config.SLENDER_ID], 12, 99)

    def test_curious_creature_statistics_send_on_round_end_if_not_bloodthirsty(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            mock_send.assert_not_called()

            monitor._process("Verified Round End")

        self.assertEqual(monitor.st.terror_ids, [config.CURIOUS_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.CURIOUS_CREATURE_ID], 12, 99)

    def test_verified_end_does_not_send_statistics(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [1]

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._process("Verified Round End")

        mock_send.assert_not_called()

# ═══════════════════════════════════════════════
#  Statistics.py
# ═══════════════════════════════════════════════
class TestStatistics(unittest.TestCase):
    def test_binomial_upper_matches_direct_sum(self):
        n = 12
        k = 4
        p = 0.2
        direct = sum(Statistics.binomial_pmf(n, i, p) for i in range(k, n + 1))
        self.assertAlmostEqual(Statistics.binomial_pmf_upper(n, k, p), direct, places=12)

    def test_binomial_upper_uses_lower_tail_for_common_side(self):
        n = 20
        k = 8
        p = 0.5
        direct = sum(Statistics.binomial_pmf(n, i, p) for i in range(k, n + 1))
        self.assertAlmostEqual(Statistics.binomial_pmf_upper(n, k, p), direct, places=12)

    def test_round_filtering_and_summary_strip_round_names(self):
        rows = [
            {"date": 20260526, "time": 100000, "round": " Fog ", "terror_ids": [1, 2], "map_id": 12},
            {"date": 20260526, "time": 110000, "round": "Unbound", "terror_ids": [201], "map_id": 49},
            {"date": 20260527, "time": 100000, "round": "Fog", "terror_ids": [3], "map_id": 13},
        ]
        filtered = Statistics.filter_rows(
            rows,
            datetime(2026, 5, 26, 0, 0, 0),
            datetime(2026, 5, 26, 23, 59, 59),
            {"Fog"},
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(Statistics.available_rounds(rows), ["Fog", "Unbound"])
        self.assertEqual(Statistics.round_summary(filtered), [("Fog", 1, 2)])

    def test_map_name_for_id_uses_round_to_resolve_duplicate_ids(self):
        self.assertEqual(Statistics.map_name_for_id(1, "Run"), "Dring King's Citadel")
        self.assertEqual(Statistics.map_name_for_id(1, "Classic"), "Sewers")
        self.assertEqual(Statistics.map_name_for_id(999, "Classic"), "Map 999")

    def test_map_counts_for_terror_uses_map_names(self):
        rows = [
            {"round": "Run", "terror_ids": [1], "map_id": 1},
            {"round": "Classic", "terror_ids": [1, 1], "map_id": 1},
            {"round": "Classic", "terror_ids": [2], "map_id": 12},
        ]
        self.assertEqual(
            Statistics.map_counts_for_terror(rows, 1),
            [("Sewers", 2), ("Dring King's Citadel", 1)],
        )


class TestGuiRoundHelpers(unittest.TestCase):
    def test_ordered_round_entries_include_deferred_rounds_and_aliases(self):
        entries = StatisticsGUI._ordered_round_entries(["Unbound", "Fog", "Fog (Alternate)", "Ghost Alternate", "Mystic Moon"])

        self.assertIn(("Classic", "Classic"), entries)
        self.assertIn(("Run", "Run"), entries)
        self.assertLess(entries.index(("Fog", "Fog")), entries.index(("Fog(Alternate)", "Fog (Alternate)")))
        self.assertIn(("Ghost(Alternate)", "Ghost Alternate"), entries)

    def test_round_chart_colors_are_not_collapsed_to_one_color(self):
        self.assertGreater(len(set(StatisticsGUI.ROUND_CHART_COLORS)), 3)

    def test_round_count_sort_key_orders_by_count_desc_then_round_order(self):
        rows = [
            ("Unbound", 2, 2),
            ("Fog", 5, 5),
            ("Classic", 5, 5),
            ("Bloodbath", 1, 1),
        ]

        self.assertEqual(
            sorted(rows, key=StatisticsGUI._round_count_sort_key),
            [
                ("Classic", 5, 5),
                ("Fog", 5, 5),
                ("Unbound", 2, 2),
                ("Bloodbath", 1, 1),
            ],
        )

    def test_round_chart_draws_positive_extents_with_distinct_colors(self):
        class FakeCanvas:
            def __init__(self):
                self.polygons = []

            def delete(self, _target):
                pass

            def winfo_width(self):
                return 300

            def winfo_height(self):
                return 300

            def create_polygon(self, *args, **kwargs):
                self.polygons.append((args, kwargs))

            def create_oval(self, *args, **kwargs):
                pass

            def create_text(self, *args, **kwargs):
                pass

        window = type("FakeStatisticsWindow", (), {})()
        window.round_chart = FakeCanvas()
        window._round_chart_job = "job"
        window._round_chart_rows = [
            ("Bloodbath", 6, 6),
            ("Alternate", 4, 4),
            ("Randomizer", 2, 2),
        ]
        window._draw_round_slice = StatisticsGUI.StatisticsWindow._draw_round_slice.__get__(window)

        StatisticsGUI.StatisticsWindow._draw_round_chart(window)

        colors = [kwargs["fill"] for _args, kwargs in window.round_chart.polygons]
        self.assertEqual(colors, list(StatisticsGUI.ROUND_CHART_COLORS[:3]))
        self.assertTrue(all(len(args) >= 4 for args, _kwargs in window.round_chart.polygons))

    def test_filtered_rows_and_round_summary_reuses_cache_for_same_key(self):
        window = type("FakeStatisticsWindow", (), {})()
        window.rows = [{"round": "Unbound"}]
        window._filter_cache_key = None
        window._filter_cache_rows = []
        window._filter_cache_round_rows = []
        window._terror_stats_cache = {"unbound": (1, 1, [])}
        window._map_counts_cache = {1: [("Sewers", 1)]}
        start = datetime(2026, 5, 1, 0)
        end = datetime(2026, 5, 2, 0)
        key = (1, start, end, ("Unbound",))

        with patch.object(StatisticsGUI.Statistics, "filter_rows", return_value=window.rows) as mock_filter, \
             patch.object(StatisticsGUI.Statistics, "round_summary", return_value=[("Unbound", 1, 1)]) as mock_summary:
            first = StatisticsGUI.StatisticsWindow._filtered_rows_and_round_summary(
                window, key, start, end, {"Unbound"}
            )
            second = StatisticsGUI.StatisticsWindow._filtered_rows_and_round_summary(
                window, key, start, end, {"Unbound"}
            )

        self.assertEqual(first, second)
        mock_filter.assert_called_once()
        mock_summary.assert_called_once()
        self.assertEqual(window._terror_stats_cache, {})
        self.assertEqual(window._map_counts_cache, {})

    def test_terror_map_counts_are_cached_per_filtered_rows(self):
        class FakeTree:
            def __init__(self):
                self.inserted = []

            def selection(self):
                return ("1",)

            def insert(self, *args, **kwargs):
                self.inserted.append((args, kwargs))

        window = type("FakeStatisticsWindow", (), {})()
        window.terror_tree = FakeTree()
        window.map_tree = FakeTree()
        window.filtered_rows = [{"round": "Classic", "terror_ids": [1], "map_id": 1}]
        window._map_counts_cache = {}
        window._clear_tree = MagicMock()

        with patch.object(StatisticsGUI.Statistics, "map_counts_for_terror", return_value=[("Sewers", 1)]) as mock_counts:
            StatisticsGUI.StatisticsWindow._on_terror_selected(window)
            StatisticsGUI.StatisticsWindow._on_terror_selected(window)

        mock_counts.assert_called_once_with(window.filtered_rows, 1)
        self.assertEqual(window._clear_tree.call_count, 2)
        self.assertEqual(len(window.map_tree.inserted), 2)

    def test_load_rows_async_ignores_duplicate_request_while_loading(self):
        class FakeStatus:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        window = type("FakeStatisticsWindow", (), {})()
        window._rows_loading = True
        window.v_status = FakeStatus()

        with patch.object(StatisticsGUI.threading, "Thread") as mock_thread:
            StatisticsGUI.StatisticsWindow._load_rows_async(window)

        mock_thread.assert_not_called()
        self.assertIsNone(window.v_status.value)


# ═══════════════════════════════════════════════
#  ConnectDB.py
# ═══════════════════════════════════════════════
class TestGetTransformedUid(unittest.TestCase):
    def test_round_filters_url_encode_round_names(self):
        self.assertEqual(
            ConnectDB._in_filter("round", ("Fog (Alternate)",)),
            "&round=in.(Fog%20%28Alternate%29)",
        )

    def test_send_Users(self):
        """新規ユーザー登録"""
        # 1回目: 存在確認 → 空（新規）
        # 2回目: 既存transformed_uid一覧取得
        # 3回目: POST登録
        mock_res1 = MagicMock()
        mock_res1.__enter__ = MagicMock(return_value=mock_res1)
        mock_res1.__exit__ = MagicMock(return_value=False)
        mock_res1.read.return_value = json.dumps([]).encode()  # 存在しない

        mock_res2 = MagicMock()
        mock_res2.__enter__ = MagicMock(return_value=mock_res2)
        mock_res2.__exit__ = MagicMock(return_value=False)
        mock_res2.read.return_value = json.dumps([{"transformed_uid": 123}]).encode()

        mock_res3 = MagicMock()
        mock_res3.__enter__ = MagicMock(return_value=mock_res3)
        mock_res3.__exit__ = MagicMock(return_value=False)
        mock_res3.status = 201

        with patch('urllib.request.urlopen', side_effect=[mock_res1, mock_res2, mock_res3]):
            result = ConnectDB.send_Users("usr_new")
            self.assertNotEqual(result, 123)
            self.assertIsNotNone(result)

    def test_send_users_existing_user(self):
        """既存ユーザーの場合はtransformed_uidをそのまま返す"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        mock_res.read.return_value = json.dumps([{"VRChat_uid": "usr-existing", "transformed_uid": 123}]).encode()

        with patch('urllib.request.urlopen', return_value=mock_res):
            result = ConnectDB.send_Users("usr-existing")
            self.assertEqual(result, 123)

    def test_send_Users_full(self):
        """ユーザー登録限界"""
        existing = [{"VRChat_uid": "usr-existing", "transformed_uid": i} for i in range(-32768, 32767)]
        mock_res1 = MagicMock()
        mock_res1.__enter__ = MagicMock(return_value=mock_res1)
        mock_res1.__exit__ = MagicMock(return_value=False)
        mock_res1.read.return_value = json.dumps([]).encode()

        mock_res2 = MagicMock()
        mock_res2.__enter__ = MagicMock(return_value=mock_res2)
        mock_res2.__exit__ = MagicMock(return_value=False)
        mock_res2.read.return_value = json.dumps(existing).encode()

        with patch('urllib.request.urlopen', side_effect=[mock_res1, mock_res2]):
            result = ConnectDB.send_Users("usr_new")
            self.assertIsNone(result)

    def test_existing_user(self):
        """既存ユーザーのtransformed_uidを返す"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        mock_res.read.return_value = json.dumps([{"VRChat_uid": "usr-existing", "transformed_uid": 123}]).encode()

        with patch('urllib.request.urlopen', return_value=mock_res):
            result = ConnectDB.get_transformed_uid("usr-existing")
            self.assertEqual(result, 123)

    def test_new_user_calls_send_users(self):
        """存在しない場合はsend_Usersを呼ぶ"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        mock_res.read.return_value = json.dumps([]).encode()  # 空 = 未登録

        with patch('urllib.request.urlopen', return_value=mock_res), \
             patch.object(ConnectDB, 'send_Users', return_value=123) as mock_send:
            result = ConnectDB.get_transformed_uid("usr_new")
            mock_send.assert_called_once_with("usr_new")
            self.assertEqual(result, 123)

    def test_error_returns_none(self):
        """エラー時はNoneを返す"""
        with patch('urllib.request.urlopen', side_effect=Exception("network error")):
            result = ConnectDB.get_transformed_uid("usr_abc123")
            self.assertIsNone(result)

    def test_get_ToNRoundStatistics(self):
        """集計データを取得"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        expected = [{
            "created_at": "2026-05-24T12:34:56+00:00",
            "round": "Unbound",
            "terror_ids": [1],
            "map_id": 2,
            "transformed_uid": 123
        }]
        mock_res.read.return_value = json.dumps(expected).encode()
        with patch('urllib.request.urlopen', return_value=mock_res) as mock_urlopen:
            result = ConnectDB.get_ToNRoundStatistics()
            self.assertEqual(result, expected)
            requested_url = mock_urlopen.call_args.args[0].full_url
            self.assertIn("round=not.in.(Classic,Run)", requested_url)

        mock_res.read.return_value = json.dumps(expected).encode()
        with patch('urllib.request.urlopen', return_value=mock_res) as mock_urlopen:
            result = ConnectDB.get_ToNRoundStatistics(exclude_rounds=None, include_rounds=("Classic", "Run"))
            self.assertEqual(result, expected)
            requested_url = mock_urlopen.call_args.args[0].full_url
            self.assertIn("round=in.(Classic,Run)", requested_url)

    def test_get_ToNRoundStatistics_fetches_all_pages(self):
        first_page = [
            {"created_at": f"2026-05-24T12:{i % 60:02d}:00+00:00", "round": "Unbound", "terror_ids": [1]}
            for i in range(1000)
        ]
        second_page = [{"created_at": "2026-05-24T13:00:00+00:00", "round": "Unbound", "terror_ids": [2]}]

        mock_res1 = MagicMock()
        mock_res1.__enter__ = MagicMock(return_value=mock_res1)
        mock_res1.__exit__ = MagicMock(return_value=False)
        mock_res1.read.return_value = json.dumps(first_page).encode()

        mock_res2 = MagicMock()
        mock_res2.__enter__ = MagicMock(return_value=mock_res2)
        mock_res2.__exit__ = MagicMock(return_value=False)
        mock_res2.read.return_value = json.dumps(second_page).encode()

        with patch('urllib.request.urlopen', side_effect=[mock_res1, mock_res2]) as mock_urlopen:
            result = ConnectDB.get_ToNRoundStatistics()

        self.assertEqual(len(result), 1001)
        urls = [call.args[0].full_url for call in mock_urlopen.call_args_list]
        self.assertIn("offset=0", urls[0])
        self.assertIn("offset=1000", urls[1])

# ═══════════════════════════════════════════════
#  PlaySound.py
# ═══════════════════════════════════════════════
class TestPlaySound(unittest.TestCase):
    def test_get_sound_volume(self):
        result = PlaySound.get_sound_volume()
        self.assertEqual(result, 1.0)

    def test_set_sound_volume(self):
        global sound_volume
        PlaySound.set_sound_volume(0.3)
        result = PlaySound.get_sound_volume()
        self.assertEqual(result, 0.3)

    class _SyncThread:
        """再生スレッドをその場で実行して結果を確定させる"""

        def __init__(self, target=None, daemon=None, **_kw):
            self._target = target

        def start(self):
            self._target()

    def _play(self, path: str, exists: bool = True, fail_on: str = "",
              keep_warned: bool = False):
        """play_sound を同期実行し、送られたMCIコマンドと出力を返す"""
        commands: list[str] = []

        def fake_mci(command: str) -> int:
            commands.append(command)
            return 259 if fail_on and command.startswith(fail_on) else 0

        if not keep_warned:
            PlaySound._warned.clear()   # 「1回だけ出す」警告をテスト間で持ち越さない
        with patch.object(PlaySound, "_mci", side_effect=fake_mci), \
             patch.object(PlaySound, "_mci_error_text", return_value="MCIのエラー"), \
             patch.object(PlaySound.threading, "Thread", self._SyncThread), \
             patch("pathlib.Path.exists", return_value=exists), \
             patch("builtins.print") as mock_print:
            PlaySound.play_sound(path)
        return commands, [str(c.args[0]) for c in mock_print.call_args_list]

    def test_play_sound_opens_sets_volume_plays_and_closes(self):
        PlaySound.set_sound_volume(1.0)
        commands, _out = self._play("voice/continue.mp3")

        self.assertEqual(len(commands), 4)
        alias = commands[0].split("alias ")[1]
        self.assertTrue(commands[0].startswith('open "'), commands[0])
        self.assertIn(str(Path("voice/continue.mp3").absolute()), commands[0])
        self.assertIn("type mpegvideo", commands[0])
        self.assertEqual(commands[1], f"setaudio {alias} volume to 1000")
        self.assertEqual(commands[2], f"play {alias} wait")
        self.assertEqual(commands[3], f"close {alias}")

    def test_wav_uses_waveaudio_and_unknown_extension_omits_type(self):
        commands, _out = self._play("voice/se.wav")
        self.assertIn("type waveaudio", commands[0])

        commands, _out = self._play("voice/se.ogg")
        self.assertNotIn(" type ", commands[0])

    def test_each_playback_uses_a_unique_alias(self):
        """エイリアスを固定すると2回目の再生が1回目を止めてしまう"""
        first, _ = self._play("voice/continue.mp3")
        second, _ = self._play("voice/continue.mp3")

        self.assertNotEqual(first[0].split("alias ")[1],
                            second[0].split("alias ")[1])

    def test_volume_is_converted_to_0_1000(self):
        PlaySound.set_sound_volume(0.3)
        commands, _out = self._play("voice/continue.mp3")
        self.assertTrue(commands[1].endswith(" volume to 300"), commands[1])

        PlaySound.set_sound_volume(1.0)

    def test_close_runs_even_when_play_fails(self):
        """closeを飛ばすとデバイスが解放されず、いずれ再生できなくなる"""
        commands, out = self._play("voice/continue.mp3", fail_on="play")

        self.assertTrue(commands[-1].startswith("close "), commands)
        self.assertTrue(any("MCIのエラー" in line for line in out),
                        "失敗を標準出力に出すこと")

    def test_open_failure_skips_playback(self):
        commands, out = self._play("voice/continue.mp3", fail_on="open")

        self.assertEqual(len(commands), 1, "openに失敗したら以降は送らない")
        self.assertTrue(any("MCIのエラー" in line for line in out))

    def test_long_path_falls_back_to_the_short_path(self):
        """MCIは概ね128文字以上のパスを開けないので8.3形式で開き直す"""
        commands: list[str] = []
        short = r"C:\\DIR~1\\SND~1.MP3"

        def fake_mci(command: str) -> int:
            commands.append(command)
            return 304 if command.startswith("open") and short not in command else 0

        PlaySound._warned.clear()
        with patch.object(PlaySound, "_mci", side_effect=fake_mci), \
             patch.object(PlaySound, "_short_path", return_value=short), \
             patch.object(PlaySound.threading, "Thread", self._SyncThread), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.print") as mock_print:
            PlaySound.play_sound("voice/continue.mp3")

        self.assertEqual(len(commands), 5, commands)
        self.assertIn(short, commands[1])
        self.assertIn("type mpegvideo", commands[1], "種別は元の拡張子から決める")
        self.assertTrue(commands[3].startswith("play "), commands)
        mock_print.assert_not_called()

    def test_volume_failure_warns_once_and_keeps_playing(self):
        """waveaudioはsetaudio非対応。再生は続け、警告は拡張子ごとに1回だけ"""
        first, out1 = self._play("voice/se.wav", fail_on="setaudio")
        second, out2 = self._play("voice/se.wav", fail_on="setaudio",
                                  keep_warned=True)

        self.assertTrue(first[2].startswith("play "), first)
        self.assertTrue(first[3].startswith("close "), first)
        self.assertEqual(len(out1), 1, out1)
        self.assertEqual(out2, [], "2回目以降は黙ること")

    def test_play_sound_skips_if_not_exists(self):
        """ファイルが存在しない場合はMCIを呼ばない"""
        commands, _out = self._play("voice/notfound.mp3", exists=False)
        self.assertEqual(commands, [])

    def test_play_sound_skips_empty_path(self):
        """空文字の場合はMCIを呼ばない"""
        commands, _out = self._play("")
        self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
