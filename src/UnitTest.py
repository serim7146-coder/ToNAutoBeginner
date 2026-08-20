import unittest
from unittest.mock import patch, MagicMock
import threading
import time
import sys
import json
import tempfile
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
import RoundDecision
import Statistics
import StatisticsGUI
import LogMonitor
import ActionExecutor
import SharedState
import VRChatDiscovery
import VRChatLauncher
import OSCClient
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
             patch.object(config, "BEGIN_RETRY_MAX", 0), \
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
        app._apply_saved_profiles = lambda: None
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
             patch.object(config, "BEGIN_RETRY_MAX", 0), \
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
             patch.object(config, "BEGIN_RETRY_MAX", 0), \
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
             patch.object(config, "BEGIN_RETRY_MAX", 0), \
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

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(config, "BEGIN_RETRY_MAX", 0),              patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep),              patch.object(ActionExecutor.SharedState, "equip_freeze_start",
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


class TestBeginRetryMove(unittest.TestCase):
    """Beginリトライの位置合わせ量"""

    def _moves(self, attempts):
        cfg = WindowConfig(hwnd=123, osc_port=9000)
        executor = ActionExecutor.ActionExecutor(cfg, WindowState(),
                                                 lambda: True, lambda _m: None)
        moves: list[tuple[str, float]] = []
        with patch.object(executor, "move",
                          side_effect=lambda d, sec: moves.append((d, sec))):
            for attempt in attempts:
                executor._retry_move(attempt)
        return moves

    def test_first_left_is_larger_than_later_lefts(self):
        """1回目の左だけ大きく寄せ、3回目以降は従来どおり"""
        self.assertEqual(
            self._moves([1, 2, 3, 4]),
            [("left", config.BEGIN_RETRY_FIRST_LEFT_SEC),
             ("right", config.BEGIN_RETRY_RIGHT_SEC),
             ("left", config.BEGIN_RETRY_LEFT_SEC),
             ("right", config.BEGIN_RETRY_RIGHT_SEC)],
        )
        self.assertGreater(config.BEGIN_RETRY_FIRST_LEFT_SEC,
                           config.BEGIN_RETRY_LEFT_SEC)


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

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(config, "BEGIN_RETRY_MAX", 0),              patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep),              patch.object(ActionExecutor.SharedState, "equip_freeze_start",
                          side_effect=lambda state: order.append("freeze")),              patch.object(ActionExecutor.PlaySound, "play_sound",
                          side_effect=lambda _p: order.append("sound")),              patch.object(executor, "move",
                          side_effect=lambda d, sec: order.append("move")),              patch.object(WindowOperator, "focus_window",
                          side_effect=lambda _h: order.append("focus") or True),              patch.object(WindowOperator, "click",
                          side_effect=lambda: order.append("click")):
            executor.do_after_round()
        return order

    def test_announce_comes_right_before_the_click_osc(self):
        """OSC窓: 移動・フリーズ・フォーカスの後、クリックの直前に鳴らす"""
        order = self._order_of_actions(osc_port=9000)

        self.assertEqual(order, ["move", "move", "freeze", "focus", "sound", "click"])

    def test_announce_comes_right_before_the_click_no_osc(self):
        """非OSC窓: 移動もロック内なので、移動を終えてから鳴らす"""
        order = self._order_of_actions(osc_port=0)

        self.assertEqual(order, ["freeze", "focus", "move", "move", "sound", "click"])

    def test_announce_is_skipped_when_item_is_kept(self):
        """アイテムを持ったまま終わったラウンドでは鳴らさない"""
        cfg = WindowConfig(hwnd=123, osc_port=9000, voice_item_lost="lost.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=5)
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(config, "BEGIN_RETRY_MAX", 0),              patch.object(ActionExecutor.time, "sleep"),              patch.object(ActionExecutor.PlaySound, "play_sound") as mock_play,              patch.object(executor, "move"),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click") as mock_click:
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

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(config, "BEGIN_RETRY_MAX", 0),              patch.object(ActionExecutor.time, "sleep", side_effect=fake_sleep),              patch.object(ActionExecutor.PlaySound, "play_sound",
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

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(config, "BEGIN_RETRY_MAX", 0),              patch.object(ex, "move", side_effect=on_move),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click", side_effect=clicked.set):
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
        self.assertEqual(moves, ["forward", "left"])
        self.assertFalse(clicked_while_frozen, "フリーズ中にクリックしてはいけない")
        self.assertTrue(clicked_after_release, "解除後はクリックすること")

    def test_begin_move_runs_during_equip_freeze_and_click_waits(self):
        """他窓がアイテムロスト装備待ちでも、Begin前移動は進みクリックだけ待つ"""
        other = WindowState()
        SharedState.equip_freeze_start(other)
        moves, observed_move, clicked_while_frozen, clicked_after_release =             self._run_frozen_begin(lambda: SharedState.equip_freeze_end(other))

        self.assertTrue(observed_move, "フリーズ中でもOSC移動は行うこと")
        self.assertEqual(moves, ["forward", "left"])
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
        self.assertEqual(moves, ["forward", "left"])

    def test_retry_move_runs_during_freeze_and_click_waits(self):
        """Beginリトライ中にフリーズが張られても、位置合わせの移動は進む"""
        st = self._state()
        ex = self._executor(st)
        clicks: list[int] = []
        retry_moved = threading.Event()
        second_click = threading.Event()

        def on_move(direction, seconds):
            if clicks:                      # 初回クリック後の移動＝リトライの位置合わせ
                retry_moved.set()

        def on_click():
            clicks.append(1)
            if len(clicks) == 1:
                # 初回Beginの直後に他窓が続行ラウンドを開始した状況
                SharedState.continue_round_start()
            else:
                second_click.set()

        with patch.object(config, "BEGIN_WAIT_SEC", 0),              patch.object(config, "BEGIN_RETRY_MAX", 2),              patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0.2),              patch.object(ex, "move", side_effect=on_move),              patch.object(WindowOperator, "focus_window", return_value=True),              patch.object(WindowOperator, "click", side_effect=on_click):
            t = threading.Thread(target=ex.do_after_round, daemon=True)
            t.start()
            observed_retry_move = retry_moved.wait(3.0)
            clicked_while_frozen = second_click.wait(0.6)
            SharedState.continue_round_end()
            clicked_after_release = second_click.wait(3.0)
            t.join(timeout=3.0)

        self.assertTrue(observed_retry_move, "フリーズ中でもリトライの移動は行うこと")
        self.assertFalse(clicked_while_frozen, "フリーズ中にリトライのクリックをしてはいけない")
        self.assertTrue(clicked_after_release, "解除後はリトライのクリックをすること")


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


class TestLogMonitorHoshiimo(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)

    def _monitor(
        self,
        *,
        hoshiimo_skip: bool = True,
        keep_on: dict | None = None,
        instance_type: str = config.INSTANCE_HOSHIIMO,
    ):
        cfg = WindowConfig(
            hoshiimo_skip=hoshiimo_skip,
            do_skip=True,
            voice_continue="continue.mp3",
        )
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None, window_idx=1)
        monitor.st.instance_type = instance_type
        return monitor

    def test_hoshiimo_allows_continue_voice_outside_skip_rounds(self):
        monitor = self._monitor(
            hoshiimo_skip=True,
            keep_on={"Double Trouble/ダブルトラブル": {42}},
        )
        monitor.st.in_round = True
        monitor.st.round_type = "Double Trouble"

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers([42], "Double Trouble", revealed=False)

        mock_play.assert_called_once_with("continue.mp3")
        self.assertTrue(monitor.st.is_continue_round)

    def test_hoshiimo_does_not_run_normal_skip_for_non_skip_rounds(self):
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Double Trouble"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Double Trouble", revealed=False)

        mock_thread.assert_not_called()

    def test_hoshiimo_hands_free_still_only_allows_voice(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(
            hoshiimo_skip=True,
            keep_on={"Double Trouble/ダブルトラブル": {42}},
        )
        monitor.st.in_round = True
        monitor.st.round_type = "Double Trouble"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([42], "Double Trouble", revealed=False)

        mock_play.assert_called_once_with("continue.mp3")
        mock_thread.assert_not_called()

    def test_hoshiimo_skip_round_still_uses_dedicated_skip(self):
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Classic", revealed=False)

        mock_thread.assert_called_once()

    def test_hoshiimo_classic_bloodthirsty_does_not_skip(self):
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_thread.assert_not_called()

    def test_hoshiimo_classic_curious_creature_defers_decision(self):
        """Curious Creatureがいる間は即自爆せず、出現ログ待ちに入る"""
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.CURIOUS_CREATURE_ID])
        # 即自爆ではなく、判定待ちスレッドが起動する
        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["target"].__func__,
                         LogMonitor.LogMonitor._delayed_group_skip)

    # ── バリアント判定待ちの挙動 ──────────────

    def _run_delayed(self, monitor, wait_sec=0.0):
        """_delayed_group_skip を待ち時間ゼロ相当で実行する"""
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_group_skip(wait_sec, monitor.st.round_seq)
        return mock_thread

    def test_delayed_skip_fires_when_marker_never_arrives(self):
        """通常のCurious Creatureなら待機後に自爆する（自動自爆を失わない）"""
        monitor = self._monitor(hoshiimo_skip=True)
        monitor._running = True
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.CURIOUS_CREATURE_ID]

        mock_thread = self._run_delayed(monitor)
        mock_thread.assert_called_once()

    def test_delayed_skip_cancelled_when_bloodthirsty_marker_arrives(self):
        monitor = self._monitor(hoshiimo_skip=True)
        monitor._running = True
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.CURIOUS_CREATURE_ID]
        monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)  # 出現時のログ

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_thread = self._run_delayed(monitor)
        mock_thread.assert_not_called()

    def test_delayed_skip_cancelled_when_hungry_home_invader_marker_arrives(self):
        monitor = self._monitor(hoshiimo_skip=True)
        monitor._running = True
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.SLENDER_ID]
        monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_thread = self._run_delayed(monitor)
        mock_thread.assert_not_called()

    def test_delayed_skip_aborts_when_round_changed(self):
        """待機中に次のラウンドが始まったら自爆しない"""
        monitor = self._monitor(hoshiimo_skip=True)
        monitor._running = True
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.CURIOUS_CREATURE_ID]

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_group_skip(0.0, monitor.st.round_seq + 1)  # 別ラウンドの予約
        mock_thread.assert_not_called()

    def test_slender_defers_in_hoshiimo(self):
        """Slenderも出現ログ待ちに入る（Hungry Home Invader対策）"""
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)

        self.assertEqual(mock_thread.call_args.kwargs["target"].__func__,
                         LogMonitor.LogMonitor._delayed_group_skip)

    def test_yakiimo_bloodthirsty_does_not_skip(self):
        """焼き芋でもバリアント例外が効く（従来は干し芋のみ有効だった）"""
        monitor = self._monitor(hoshiimo_skip=True, instance_type=config.INSTANCE_YAKIIMO)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_thread.assert_not_called()

    def test_yakiimo_hungry_home_invader_does_not_skip(self):
        monitor = self._monitor(hoshiimo_skip=True, instance_type=config.INSTANCE_YAKIIMO)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_thread.assert_not_called()

    def test_bloodthirsty_in_bloodbath_does_not_skip(self):
        """Classic以外の自爆対象ラウンドでもバリアント例外が効く"""
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Bloodbath"
        monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([config.CURIOUS_CREATURE_ID, 3, 4], "Bloodbath", revealed=False)

        self.assertIn(config.BLOODTHIRSTY_CREATURE_ID, monitor.st.terror_ids)
        mock_thread.assert_not_called()

    def test_bloodbath_uses_longer_variant_wait(self):
        """枠ごとに出現がずれるBloodbathはClassicより長く待つ"""
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.round_type = "Classic"
        classic_wait = monitor._variant_wait_sec()
        monitor.st.round_type = "Bloodbath"
        bloodbath_wait = monitor._variant_wait_sec()
        monitor.st.round_type = "未知のラウンド"
        default_wait = monitor._variant_wait_sec()

        self.assertGreater(bloodbath_wait, classic_wait)
        self.assertEqual(default_wait, config.TERROR_VARIANT_WAIT_DEFAULT_SEC)

    def test_yakiimo_skip_round_uses_hoshiimo_dedicated_skip(self):
        monitor = self._monitor(hoshiimo_skip=True, instance_type=config.INSTANCE_YAKIIMO)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Classic", revealed=False)

        mock_thread.assert_called_once()

    def test_hoshiimo_skip_clears_stale_continue_state(self):
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.is_continue_round = True

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Classic", revealed=False)

        self.assertFalse(monitor.st.is_continue_round)
        mock_thread.assert_called_once()

    def test_round_start_clears_stale_continue_state(self):
        monitor = self._monitor()
        monitor.st.is_continue_round = True

        monitor._process("This round is taking place at Facility (12) and the round type is Classic")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(monitor.st.round_type, "Classic")


class TestLogMonitorItemLostVoice(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
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
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
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
        self._stats_patcher = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats_patcher.start()

    def tearDown(self):
        self._stats_patcher.stop()
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
