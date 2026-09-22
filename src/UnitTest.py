import unittest
import hashlib
import base64
import struct
import socket
from unittest.mock import patch, MagicMock
import threading
import time
import sys
import json
import tempfile
import gzip
import ctypes
import io
import os
import re
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
import ReadJson
import MatchTNL
import ProcessCheck
import tkinter as tk
import ToolLauncher
import HotKey
import RoundSequence
import TerrorReplacement
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
import OBSClient
import Recorder
import SecretStore
import FogEarlyRead
import ScreenCapture
import ToNEntry
import mainGUI
import AutoUpdate
import config
from State import WindowConfig, WindowState


# ── テストで本物の設定を書き換えない ─────────────────────
# %APPDATA%\ToNAutoBeginner の settings.json / fog_object_names.json は
# 依頼者の本物の設定。テスト全体を一時フォルダに向ける
REAL_SETTINGS_DIR = Path(os.environ.get("APPDATA", ".")) / "ToNAutoBeginner"
_sandbox = None
_real_paths = {}


def setUpModule():
    global _sandbox
    _sandbox = tempfile.TemporaryDirectory()
    root = Path(_sandbox.name) / "ToNAutoBeginner"
    _real_paths.update(settings=config.SETTINGS_PATH,
                       names=config.FOG_OBJECT_NAMES_PATH,
                       trust=FogEarlyRead.trust)
    config.SETTINGS_PATH = root / "settings.json"
    config.FOG_OBJECT_NAMES_PATH = root / "fog_object_names.json"
    # trust は import 時にパスを受け取っている。config を差し替えても効かないので作り直す
    FogEarlyRead.trust = FogEarlyRead.NameTrust(config.FOG_OBJECT_NAMES_PATH)


def tearDownModule():
    config.SETTINGS_PATH = _real_paths["settings"]
    config.FOG_OBJECT_NAMES_PATH = _real_paths["names"]
    FogEarlyRead.trust = _real_paths["trust"]
    _sandbox.cleanup()


class TestNoRealSettings(unittest.TestCase):
    """テスト中の設定の置き場所が、本物の %APPDATA%\\ToNAutoBeginner の下ではないこと"""

    def _assert_not_real(self, path):
        real = REAL_SETTINGS_DIR.resolve()
        self.assertNotEqual(Path(path).resolve().parent, real, path)
        self.assertNotIn(real, Path(path).resolve().parents, path)

    def test_the_settings_path_is_not_the_real_one(self):
        self._assert_not_real(config.SETTINGS_PATH)

    def test_the_fog_object_names_path_is_not_the_real_one(self):
        self._assert_not_real(config.FOG_OBJECT_NAMES_PATH)
        self._assert_not_real(FogEarlyRead.trust.path)

    def test_saving_settings_does_not_touch_the_real_one(self):
        mainGUI.save_settings({"probe": True})
        self.assertTrue(config.SETTINGS_PATH.exists())
        self._assert_not_real(config.SETTINGS_PATH)

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

    def test_do_skip_never_takes_focus(self):
        """自爆は背面送信だけ。送れなくても前面の窓へは押さない"""
        cfg = WindowConfig(hwnd=123)
        st = WindowState(in_round=True)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        with patch.object(WindowOperator, "hold_key_background",
                          return_value=False), \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()
        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertTrue(any("自爆できませんでした" in m for m in logs), logs)

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


class TestOSCPortsFromAssignment(unittest.TestCase):
    """GUI: 割り当てで得たポートを使う（9000 + idx*10 の決め打ちをやめる）"""

    class FakeVar:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeTab:
        def __init__(self, idx, hwnd=0x1000, log=""):
            self.idx = idx
            self.hwnd = hwnd
            self.osc_in = 0
            self.osc_out = 0
            self.v_log = TestOSCPortsFromAssignment.FakeVar(log)
            self.choices = None

        def set_hwnd_choices(self, hwnds, selected_hwnd=None):
            self.choices = list(hwnds)
            self.hwnd = selected_hwnd

        def _get_selected_hwnd(self):
            return self.hwnd

    def _app(self, tabs, assigned):
        app = type("FakeApp", (), {})()
        app.tabs = tabs
        app.logs = []
        app._log = app.logs.append
        app._on_tab_log_selected = lambda tab: None
        app._resolve_windows = lambda windows: assigned
        app._assign_source = mainGUI.App._assign_source
        return app

    @staticmethod
    def _assignment(hwnd, name, osc_in=0, osc_out=0, start_time=1.0):
        return VRChatDiscovery.WindowAssignment(
            hwnd, start_time, Path(f"C:/logs/{name}"), osc_in, osc_out)

    # ── 一括割り当て ───────────────────────────
    def test_the_tabs_take_the_ports_and_the_reason_is_logged(self):
        tabs = [self.FakeTab(0), self.FakeTab(1)]
        assigned = [self._assignment(0xA, "a.txt", 9003, 9004),
                    self._assignment(0xB, "b.txt", start_time=None)]
        app = self._app(tabs, assigned)

        mainGUI.App._assign_windows_and_logs(app, [(0xA, 1.0), (0xB, None)])

        self.assertEqual([(t.hwnd, t.osc_in, t.osc_out) for t in tabs],
                         [(0xA, 9003, 9004), (0xB, 0, 0)])
        self.assertEqual([t.v_log.get() for t in tabs],
                         [str(Path("C:/logs/a.txt")), str(Path("C:/logs/b.txt"))])
        self.assertTrue(any("OSC 9003" in m for m in app.logs), app.logs)
        self.assertTrue(any("起動時刻不明" in m for m in app.logs), app.logs)

    def test_the_hwnd_list_follows_the_new_order(self):
        """タブの[1][2]表示と中身がずれないこと"""
        tabs = [self.FakeTab(0), self.FakeTab(1)]
        assigned = [self._assignment(0xB, "b.txt", 9000, 9001),
                    self._assignment(0xA, "a.txt", 9010, 9011)]
        app = self._app(tabs, assigned)

        mainGUI.App._assign_windows_and_logs(app, [(0xA, 1.0), (0xB, 2.0)])

        self.assertEqual(tabs[0].choices, [0xB, 0xA])

    # ── 開始ボタンの経路 ─────────────────────────
    def _start_paths(self, tabs, assigned):
        app = self._app(tabs, assigned)
        with patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time",
                          return_value=[]):
            mainGUI.App._resolve_tab_ports(app)
        return app

    def test_start_fills_only_the_empty_tabs(self):
        """手で選んだログは上書きしない"""
        tabs = [self.FakeTab(0, hwnd=0xA, log=r"C:\mine\chosen.txt"),
                self.FakeTab(1, hwnd=0xB)]
        app = self._start_paths(tabs, [self._assignment(0xA, "a.txt", 9000, 9001),
                                       self._assignment(0xB, "b.txt", 9010, 9011)])

        self.assertEqual(tabs[0].v_log.get(), r"C:\mine\chosen.txt")
        self.assertEqual(tabs[1].v_log.get(), str(Path("C:/logs/b.txt")))
        self.assertTrue(any("b.txt" in m for m in app.logs), app.logs)

    def test_start_keeps_the_ports_of_the_hwnd_the_user_picked(self):
        """開始時は窓の並びを変えない。HWNDで引き当てる"""
        tabs = [self.FakeTab(0, hwnd=0xB), self.FakeTab(1, hwnd=0xA)]
        self._start_paths(tabs, [self._assignment(0xA, "a.txt", 9000, 9001),
                                 self._assignment(0xB, "b.txt", 9010, 9011)])

        self.assertEqual([(t.osc_in, t.osc_out) for t in tabs],
                         [(9010, 9011), (9000, 9001)])

    def test_a_window_that_is_not_in_the_assignment_has_no_osc(self):
        tabs = [self.FakeTab(0, hwnd=0xC)]
        tabs[0].osc_in, tabs[0].osc_out = 9000, 9001      # 前回の値が残っていても
        self._start_paths(tabs, [self._assignment(0xA, "a.txt", 9000, 9001)])

        self.assertEqual((tabs[0].osc_in, tabs[0].osc_out), (0, 0))
        self.assertEqual(tabs[0].v_log.get(), "")

    def test_a_netstat_failure_is_logged(self):
        app = self._app([], [])
        with patch.object(mainGUI.OSCClient, "udp_ports_by_pid", return_value=None), \
             patch.object(VRChatDiscovery, "find_latest_logs", return_value=[]):
            mainGUI.App._resolve_windows(app, [])

        self.assertTrue(any("netstat失敗" in m for m in app.logs), app.logs)

    # ── 速度受信のポート ─────────────────────────
    def test_the_receiver_uses_the_out_port_from_the_log(self):
        """送信ポートは受信+1とは限らない"""
        for out_port, expected in ((9004, 9004), (0, 9001)):
            cfg = WindowConfig(hwnd=123, osc_port=9000, osc_out_port=out_port)
            ex = ActionExecutor.ActionExecutor(cfg, WindowState(),
                                               lambda: True, lambda _m: None)

            with patch.object(ActionExecutor.OSCReceiver, "VelocityReceiver") as mock_recv:
                mock_recv.return_value.start.return_value = True
                ex.start_velocity_receiver()

            self.assertEqual(mock_recv.call_args.args[0], expected, out_port)


class FakeOBSServer:
    """obs-websocket v5 の偽サーバー（127.0.0.1 の空きポートで1接続だけ受ける）。

    クライアントが送ってきたフレームのマスクビットと中身を記録する。
    """

    def __init__(self, password=None, respond=True, record_active=False,
                 ping_first=False):
        self.password = password
        self.respond = respond
        self.record_active = record_active
        self.ping_first = ping_first
        self.masked = []            # 受けたフレームごとのマスクビット
        self.received = []          # 受けたJSON
        self.pong = None
        self.requests = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(5)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    SALT = "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI="
    CHALLENGE = "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="

    def close(self):
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(5)

    # ── 送受信 ───────────────────────────────
    @staticmethod
    def frame(payload: bytes, opcode=0x1) -> bytes:
        n = len(payload)
        if n <= 125:
            head = bytes([0x80 | opcode, n])
        elif n <= 0xFFFF:
            head = bytes([0x80 | opcode, 126]) + struct.pack("!H", n)
        else:
            head = bytes([0x80 | opcode, 127]) + struct.pack("!Q", n)
        return head + payload

    def _recv_exact(self, n):
        out = b""
        while len(out) < n:
            chunk = self._conn.recv(n - len(out))
            if not chunk:
                raise ConnectionError("closed")
            out += chunk
        return out

    def _read(self):
        head = self._recv_exact(2)
        self.masked.append(bool(head[1] & 0x80))
        pending = [head]

        def reader(n):
            # 先頭2バイトはマスクビットを見るために先に読んである
            return pending.pop() if pending else self._recv_exact(n)

        _fin, opcode, payload = OBSClient.read_frame(reader)
        return opcode, payload

    def _send_json(self, obj):
        self._conn.sendall(self.frame(json.dumps(obj).encode("utf-8")))

    def _read_json(self):
        while True:
            opcode, payload = self._read()
            if opcode == 0xA:
                self.pong = payload
                continue
            if opcode == 0x8:
                raise ConnectionError("client closed")
            msg = json.loads(payload.decode("utf-8"))
            self.received.append(msg)
            return msg

    def _serve(self):
        try:
            self._conn, _addr = self._srv.accept()
        except OSError:
            return
        self._conn.settimeout(5)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                data += self._conn.recv(4096)
            key = re.search(rb"Sec-WebSocket-Key: (\S+)", data).group(1).decode()
            if not self.respond:
                while self._conn.recv(4096):
                    pass
                return
            accept = base64.b64encode(hashlib.sha1(
                (key + OBSClient.WS_GUID).encode()).digest()).decode()
            self._conn.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                                f"Sec-WebSocket-Accept: {accept}\r\n"
                                "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n"
                                ).encode())
            hello = {"obsStudioVersion": "30.2.2", "obsWebSocketVersion": "5.5.2",
                     "rpcVersion": 1}
            if self.password is not None:
                hello["authentication"] = {"challenge": self.CHALLENGE,
                                           "salt": self.SALT}
            self._send_json({"op": 0, "d": hello})
            identify = self._read_json()
            if self.password is not None:
                expected = OBSClient.auth_string(self.password, self.SALT,
                                                 self.CHALLENGE)
                if identify["d"].get("authentication") != expected:
                    self._conn.sendall(self.frame(
                        struct.pack("!H", 4009) + b"Authentication failed.", 0x8))
                    return
            self._send_json({"op": 2, "d": {"negotiatedRpcVersion": 1}})
            while True:
                msg = self._read_json()
                self.requests.append(msg["d"]["requestType"])
                if self.ping_first:
                    self._conn.sendall(self.frame(b"are you there", 0x9))
                # 関係ないイベントを先に挟む（読み飛ばせること）
                self._send_json({"op": 5, "d": {"eventType": "Whatever"}})
                self._send_json({"op": 7, "d": {
                    "requestType": msg["d"]["requestType"],
                    "requestId": msg["d"]["requestId"],
                    "requestStatus": {"result": True, "code": 100},
                    "responseData": {"outputActive": self.record_active}}})
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            try:
                self._conn.close()
            except OSError:
                pass


class TestOBSClient(unittest.TestCase):
    """obs-websocket v5 の最小クライアント（自前の WebSocket）"""

    def _server(self, **kw):
        server = FakeOBSServer(**kw)
        self.addCleanup(server.close)
        return server

    # ── 1 認証文字列 ────────────────────────────
    def test_the_authentication_string_follows_the_protocol_document(self):
        """入力は obs-websocket 公式 docs/generated/protocol.md の
        「Creating an authentication string」の例の値をそのまま写した。

        同じ文書の Identify の例に出てくる "Dj6cLS+jrNA0HpCArRg0Z/Fc+YHdt2FQfAvgD1mip6Y="
        は、この入力から文書の手順どおりに計算した値と一致しない（Identify の
        例は別の値を載せているだけ）ので、期待値には使っていない。期待値は
        文書の4手順を Node.js の crypto で別に計算した値で、obs-websocket-js の
        実装（sha256(msg + salt) → sha256(hash + challenge)）とも同じ手順。
        """
        self.assertEqual(
            OBSClient.auth_string("supersecretpassword",
                                  "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=",
                                  "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="),
            "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4=")

    # ── 2 マスク ───────────────────────────────
    def test_a_client_frame_is_masked(self):
        payload = json.dumps({"op": 6, "d": {"requestType": "StartRecord"}}).encode()
        frame = OBSClient.encode_frame(payload, mask_key=b"\x01\x02\x03\x04")

        self.assertTrue(frame[1] & 0x80, "マスクビット")
        self.assertEqual(frame[2:6], b"\x01\x02\x03\x04")
        self.assertNotEqual(frame[6:], payload, "平文のまま送っていない")
        unmasked = bytes(b ^ frame[2 + i % 4] for i, b in enumerate(frame[6:]))
        self.assertEqual(unmasked, payload)

    def test_every_frame_on_the_wire_is_masked(self):
        server = self._server()
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=3)

        self.assertEqual(client.connect(), (True, ""))
        client.request("GetRecordStatus")
        client.close()
        server.close()

        self.assertTrue(server.masked)
        self.assertTrue(all(server.masked), server.masked)

    # ── 3 長さ ────────────────────────────────
    def test_every_length_class_round_trips(self):
        for n in (0, 125, 126, 65535, 65536, 70000):
            payload = bytes(i % 251 for i in range(n))
            frame = OBSClient.encode_frame(payload)
            buf = io.BytesIO(frame)

            fin, opcode, got = OBSClient.read_frame(buf.read)

            self.assertEqual((fin, opcode, got), (True, 0x1, payload), n)
            self.assertEqual(buf.read(), b"", f"{n}: 読み残しが無い")
            expected_len_code = 126 if 126 <= n <= 0xFFFF else (127 if n > 0xFFFF else n)
            self.assertEqual(frame[1] & 0x7F, expected_len_code, n)

    def test_an_unmasked_server_frame_is_read(self):
        for n in (5, 300, 70000):
            payload = b"x" * n
            buf = io.BytesIO(FakeOBSServer.frame(payload))

            self.assertEqual(OBSClient.read_frame(buf.read), (True, 0x1, payload), n)

    # ── 4 認証あり・なし ─────────────────────────
    def test_identify_without_authentication(self):
        server = self._server()
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=3)

        ok, reason = client.connect()
        got = client.request("GetRecordStatus")
        client.close()
        server.close()

        self.assertEqual((ok, reason), (True, ""))
        self.assertEqual(got, (True, {"outputActive": False}, ""))
        identify = server.received[0]
        self.assertEqual(identify["op"], 1)
        self.assertEqual(identify["d"]["rpcVersion"], 1)
        self.assertNotIn("authentication", identify["d"])

    def test_identify_with_authentication(self):
        server = self._server(password="hunter2", record_active=True)
        client = OBSClient.OBSClient("127.0.0.1", server.port, "hunter2", timeout=3)

        self.assertEqual(client.connect(), (True, ""))
        self.assertEqual(client.request("GetRecordStatus"),
                         (True, {"outputActive": True}, ""))
        client.close()

    def test_a_wrong_password_is_reported_without_the_password(self):
        server = self._server(password="hunter2")
        client = OBSClient.OBSClient("127.0.0.1", server.port, "wrong-pass", timeout=3)

        ok, reason = client.connect()

        self.assertFalse(ok)
        self.assertIn("認証に失敗", reason)
        self.assertNotIn("wrong-pass", reason)
        self.assertNotIn("wrong-pass", repr(client))

    def test_a_ping_is_answered_with_a_pong(self):
        server = self._server(ping_first=True)
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=3)
        client.connect()

        self.assertTrue(client.request("StartRecord")[0])
        client.request("GetRecordStatus")     # pong を受けたのを記録させる
        client.close()
        server.close()

        self.assertEqual(server.pong, b"are you there")

    # ── 5 タイムアウト ─────────────────────────
    def test_a_silent_server_times_out_without_raising(self):
        server = self._server(respond=False)
        client = OBSClient.OBSClient("127.0.0.1", server.port, timeout=0.3)

        t0 = time.monotonic()
        ok, reason = client.connect()
        elapsed = time.monotonic() - t0

        self.assertFalse(ok)
        self.assertIn("タイムアウト", reason)
        self.assertLess(elapsed, 2.0)

    def test_nobody_listening_is_a_plain_failure(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()                           # 誰も待ち受けていないポート

        ok, reason = OBSClient.OBSClient("127.0.0.1", port, timeout=1).connect()

        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_a_request_before_connecting_fails_quietly(self):
        self.assertEqual(OBSClient.OBSClient().request("StartRecord"),
                         (False, {}, "未接続です"))


class FakeOBS:
    """Recorder 用の偽クライアント。呼ばれたリクエストを記録する"""

    def __init__(self, recording=False, fail=""):
        self.recording = recording
        self.fail = fail
        self.calls = []

    def factory(self):
        obs = self

        class Client:
            def connect(self):
                return (False, obs.fail) if obs.fail else (True, "")

            def request(self, request_type, data=None):
                obs.calls.append(request_type)
                if request_type == "GetRecordStatus":
                    return True, {"outputActive": obs.recording}, ""
                if request_type == "StartRecord":
                    obs.recording = True
                if request_type == "StopRecord":
                    obs.recording = False
                return True, {}, ""

            def close(self):
                pass

        return Client()

    def count(self, request_type):
        return self.calls.count(request_type)


class TestRecordPlan(unittest.TestCase):
    """1本の録画を複数の窓で共有する（偽の OBS と偽の時計で）"""

    def setUp(self):
        self.now = 1000.0
        self.logs = []

    def _plan(self, obs, **kw):
        return Recorder.RecordPlan(obs.factory, self.logs.append,
                                   clock=lambda: self.now, tail_sec=3.0, **kw)

    def _advance(self, plan, sec):
        self.now += sec
        plan.tick()

    # ── 6 / 7 ─────────────────────────────────
    def test_one_window_starts_then_stops_three_seconds_after_round_over(self):
        obs = FakeOBS()
        plan = self._plan(obs)

        plan.continue_start(1)
        self.assertEqual(obs.count("StartRecord"), 1)
        self._advance(plan, 60)
        plan.round_over(1)
        self._advance(plan, 3.0)

        self.assertEqual(obs.count("StopRecord"), 1)
        self.assertFalse(plan.we_started)

    def test_it_does_not_stop_before_the_tail(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        plan.round_over(1)

        self._advance(plan, 2.9)

        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertEqual(plan.next_deadline(), 1003.0)

    def test_dying_early_keeps_recording_until_round_over(self):
        """死亡では止めない。止めるのは RoundOver+3秒だけ"""
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)

        self._advance(plan, 120)

        self.assertEqual(obs.count("StopRecord"), 0)

    # ── 8 / 9 複窓 ──────────────────────────────
    def test_two_windows_share_one_recording_until_the_last_round_over(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        self._advance(plan, 5)
        plan.continue_start(2)

        self.assertEqual(obs.count("StartRecord"), 1)
        plan.round_over(1)
        self._advance(plan, 10)
        self.assertEqual(obs.count("StopRecord"), 0, "窓2がまだ続行中")
        plan.round_over(2)
        self._advance(plan, 2.9)
        self.assertEqual(obs.count("StopRecord"), 0)
        self._advance(plan, 0.1)
        self.assertEqual(obs.count("StopRecord"), 1)

    def test_a_new_continue_during_the_tail_keeps_recording(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        plan.round_over(1)
        self._advance(plan, 1.0)

        plan.continue_start(2)
        self._advance(plan, 10)

        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertEqual(obs.count("StartRecord"), 1, "録り直さない")
        plan.round_over(2)
        self._advance(plan, 3)
        self.assertEqual(obs.count("StopRecord"), 1)

    def test_the_same_window_continuing_again_cancels_its_own_stop(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)
        plan.round_over(1)

        plan.continue_start(1)
        self._advance(plan, 10)

        self.assertEqual(obs.count("StopRecord"), 0)

    def test_a_round_over_without_a_continue_does_nothing(self):
        obs = FakeOBS()
        plan = self._plan(obs)

        plan.round_over(1)
        self._advance(plan, 10)

        self.assertEqual(obs.calls, [])

    # ── 10 手動の録画 ────────────────────────────
    def test_a_recording_started_by_hand_is_left_alone(self):
        obs = FakeOBS(recording=True)
        plan = self._plan(obs)

        plan.continue_start(1)
        plan.round_over(1)
        self._advance(plan, 5)
        plan.stop_all()

        self.assertEqual(obs.count("StartRecord"), 0)
        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertTrue(obs.recording)

    # ── 11 繋がらない ──────────────────────────
    def test_a_missing_obs_warns_once_and_tries_again_next_time(self):
        obs = FakeOBS(fail="OBSに接続できません")
        plan = self._plan(obs)

        for _ in range(3):
            plan.continue_start(1)
            plan.round_over(1)
            self._advance(plan, 5)

        warnings = [m for m in self.logs if "OBSに接続できません" in m]
        self.assertEqual(len(warnings), 1, self.logs)
        obs.fail = ""
        plan.continue_start(1)
        self.assertEqual(obs.count("StartRecord"), 1, "次の続行でまた試す")

    def test_a_failing_client_never_raises(self):
        def broken():
            raise OSError("boom")
        plan = Recorder.RecordPlan(broken, self.logs.append, clock=lambda: self.now)

        plan.continue_start(1)          # 例外が出ないこと
        plan.round_over(1)
        plan.tick()
        plan.stop_all()

        self.assertFalse(plan.we_started)

    # ── 12 停止 ─────────────────────────────────
    def test_stop_all_stops_only_what_we_started(self):
        obs = FakeOBS()
        plan = self._plan(obs)
        plan.continue_start(1)

        plan.stop_all()
        plan.stop_all()

        self.assertEqual(obs.count("StopRecord"), 1)
        self.assertEqual(plan.next_deadline(), None, "予約は捨てる")

    # ── 13 上限 ─────────────────────────────────
    def test_the_recording_stops_at_the_limit(self):
        obs = FakeOBS()
        plan = self._plan(obs, max_sec=900)
        plan.continue_start(1)

        self._advance(plan, 899)
        self.assertEqual(obs.count("StopRecord"), 0)
        self._advance(plan, 1)

        self.assertEqual(obs.count("StopRecord"), 1)
        self.assertTrue(any("上限" in m for m in self.logs), self.logs)

    def test_the_default_limit_is_configured(self):
        self.assertEqual(config.OBS_RECORD_MAX_SEC, 900)
        self.assertEqual(config.OBS_RECORD_TAIL_SEC, 3.0)


class TestRecorderThread(unittest.TestCase):
    """窓のスレッドからは投げるだけ。OBS がどうなっていても待たない"""

    def test_a_disabled_recorder_does_nothing(self):
        obs = FakeOBS()
        rec = Recorder.Recorder(client_factory=obs.factory)

        rec.on_continue_start(1)
        rec.on_round_over(1)
        rec.stop_all()

        self.assertIsNone(rec._thread, "スレッドも立てない")
        self.assertEqual(obs.calls, [])

    def test_the_default_is_disabled(self):
        self.assertFalse(Recorder.Recorder().enabled)

    def test_a_hanging_obs_does_not_block_the_caller(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class Hanging:
            def connect(self):
                release.wait(5)             # 応答しない OBS
                return False, "タイムアウト"

            def request(self, *_a):
                return False, {}, ""

            def close(self):
                pass

        rec = Recorder.Recorder(client_factory=Hanging)
        rec.configure(True, log=lambda _m: None)

        t0 = time.monotonic()
        for _ in range(5):
            rec.on_continue_start(1)
            rec.on_round_over(1)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.1)

    def test_the_worker_records_and_stops(self):
        obs = FakeOBS()
        logs = []
        rec = Recorder.Recorder(client_factory=obs.factory)
        rec.configure(True, log=logs.append)
        rec._plan.tail_sec = 0.05

        rec.on_continue_start(1)
        rec.on_round_over(1)
        deadline = time.monotonic() + 3
        while obs.count("StopRecord") == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(obs.calls, ["GetRecordStatus", "StartRecord", "StopRecord"])

    def test_stop_all_can_wait_for_the_stop_to_be_sent(self):
        obs = FakeOBS()
        rec = Recorder.Recorder(client_factory=obs.factory)
        rec.configure(True, log=lambda _m: None)
        rec.on_continue_start(1)

        rec.stop_all(wait_sec=3)

        self.assertEqual(obs.count("StopRecord"), 1)

    def test_the_password_is_not_logged(self):
        logs = []
        rec = Recorder.Recorder()
        rec.configure(True, "127.0.0.1", 1, "hunter2", log=logs.append)

        rec._plan.continue_start(1)       # 繋がらないポート。警告を出させる

        self.assertTrue(logs)
        self.assertFalse(any("hunter2" in m for m in logs), logs)


class TestRecordingHooks(unittest.TestCase):
    """LogMonitor: 続行と決まった瞬間に録画を始め、RoundOver で止める予約をする"""

    FOG_KEY = "Fog/霧"
    CLASSIC_KEY = "Classic/クラシック"
    PURSUER = 99

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        for p in (patch.object(ConnectDB, "send_ToNRoundStatistics"),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound")):
            p.start()
            self.addCleanup(p.stop)
        self.started = patch.object(Recorder, "on_continue_start").start()
        self.over = patch.object(Recorder, "on_round_over").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.set_list_source, None)

    def _monitor(self, keep_on=None, round_type="Classic", fog=False,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=3)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.st.fog = fog
        return monitor

    @staticmethod
    def _judge(monitor, ids, round_type="Classic"):
        """通常判定。Classic は _on_killers だと亜種待ち（別スレッド）を挟むので、
        待ちの後に走る判定そのものを呼ぶ"""
        monitor.st.terror_ids = list(ids)
        monitor._decide_with_keep_on_set(round_type)

    # ── 15 通常判定 ─────────────────────────────
    def test_a_continue_starts_the_recording_once(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})

        self._judge(monitor, [42])
        self._judge(monitor, [42])          # 同じラウンドで判定し直しても

        self.started.assert_called_once_with(3)

    def test_a_skip_does_not_record(self):
        monitor = self._monitor({self.CLASSIC_KEY: {42}})

        self._judge(monitor, [7])

        self.started.assert_not_called()

    def test_an_open_special_round_target_does_not_record(self):
        """DTM/Waldo の3クラ解放狙いはアナウンスが鳴らない"""
        monitor = self._monitor()

        self._judge(monitor, [LogMonitor.DTM_TERROR_ID])

        self.assertTrue(monitor.st.is_continue_round, "前提: 続行はする")
        self.started.assert_not_called()

    # ── 16 / 17 グループ ─────────────────────────
    def test_a_sabotage_star_wish_records(self):
        monitor = self._monitor(round_type="Sabotage",
                                instance_type=config.INSTANCE_HOSHIIMO)
        with patch.object(monitor, "_group_decision", return_value=GroupRound.WANTED):
            self.assertTrue(monitor._apply_group_decision("Sabotage"))

        self.started.assert_called_once_with(3)

    def test_an_everyone_continues_round_does_not_record(self):
        monitor = self._monitor(round_type="8 Pages",
                                instance_type=config.INSTANCE_HOSHIIMO)
        with patch.object(monitor, "_group_decision", return_value=GroupRound.CONTINUE):
            self.assertTrue(monitor._apply_group_decision("8 Pages"))

        self.started.assert_not_called()

    # ── 18 霧の前倒し判明 ─────────────────────────
    def test_a_fog_enrage_records_at_that_moment(self):
        monitor = self._monitor({self.FOG_KEY: {self.PURSUER}}, round_type="Fog",
                                fog=True)

        monitor._on_enrage("The Pursuer")

        self.started.assert_called_once_with(3)
        monitor._on_killers([self.PURSUER], "Fog", revealed=True)
        self.started.assert_called_once_with(3)

    # ── 19 RoundOver ─────────────────────────────
    def test_round_over_is_passed_on(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False

        monitor._process("2026.09.20 12:00:00 Debug      -  RoundOver")

        self.over.assert_called_once_with(3)

    # ── 20 放置モード ────────────────────────────
    def test_hands_free_does_not_record(self):
        """放置モード中は録らない（依頼者の判断。続行とフリーズはそのまま）"""
        SharedState.set_hands_free(True)
        monitor = self._monitor({self.CLASSIC_KEY: {42}})

        self._judge(monitor, [42])

        self.assertTrue(monitor.st.is_continue_round, "続行はする")
        self.started.assert_not_called()
        PlaySound.play_sound.assert_not_called()

    def test_hands_free_follows_the_announcement_per_window(self):
        """放置モードが効くのはprivateの窓だけ（_hands_free）。干し芋の窓では
        トグルがONでもアナウンスが鳴るので、録画もアナウンスと揃える"""
        SharedState.set_hands_free(True)
        monitor = self._monitor(round_type="Sabotage",
                                instance_type=config.INSTANCE_HOSHIIMO)
        with patch.object(monitor, "_group_decision", return_value=GroupRound.WANTED):
            monitor._apply_group_decision("Sabotage")

        PlaySound.play_sound.assert_called_once_with("continue.mp3")
        self.started.assert_called_once_with(3)

    def test_hands_free_sabotage_star_in_private_does_not_record(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(round_type="Sabotage")
        with patch.object(monitor, "_group_decision", return_value=GroupRound.WANTED):
            monitor._apply_group_decision("Sabotage")

        self.assertTrue(monitor.st.is_continue_round)
        self.started.assert_not_called()

    def test_entering_hands_free_mid_recording_still_passes_round_over(self):
        """録画の途中で放置に入っても、RoundOver+3秒で普通に止まるように"""
        monitor = self._monitor()
        monitor.cfg.auto_begin = False
        SharedState.set_hands_free(True)

        monitor._process("2026.09.20 12:00:00 Debug      -  RoundOver")

        self.over.assert_called_once_with(3)


class TestOBSSettingsInTheGui(unittest.TestCase):
    """GUI: 設定の受け渡しと接続テスト"""

    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _app(self, enabled=True, host="127.0.0.1", port="4455", password="hunter2"):
        app = type("FakeApp", (), {})()
        app.v_obs_enabled = self.FakeVar(enabled)
        app.v_obs_host = self.FakeVar(host)
        app.v_obs_port = self.FakeVar(port)
        app.v_obs_password = self.FakeVar(password)
        app.logs = []
        app._log = app.logs.append
        app._obs_endpoint = lambda: mainGUI.App._obs_endpoint(app)
        app._save_settings_now = lambda: None
        return app

    def test_the_settings_reach_the_recorder(self):
        app = self._app()
        with patch.object(Recorder, "configure") as mock_configure:
            mainGUI.App._apply_obs_settings(app)

        args = mock_configure.call_args
        self.assertEqual(args.args[:4], (True, "127.0.0.1", 4455, "hunter2"))

    # ── 録画中にOFF ───────────────────────────
    def test_turning_it_off_stops_without_waiting(self):
        app = self._app(enabled=False)
        with patch.object(Recorder, "configure"),              patch.object(Recorder, "stop_all") as mock_stop:
            mainGUI.App._apply_obs_settings(app)

        mock_stop.assert_called_once_with()          # wait_sec を渡さない＝待たない

    def test_turning_it_on_does_not_stop(self):
        app = self._app(enabled=True)
        with patch.object(Recorder, "configure"),              patch.object(Recorder, "stop_all") as mock_stop:
            mainGUI.App._apply_obs_settings(app)

        mock_stop.assert_not_called()

    def _recorder_after_turning_off(self, obs):
        rec = Recorder.Recorder(client_factory=obs.factory)
        rec.configure(True, log=lambda _m: None)
        rec.on_continue_start(1)
        rec.configure(False)
        rec.stop_all()                                # GUIのOFF操作（待たない）
        rec.stop_all(wait_sec=3)                      # 先の分を処理し終えるまで待つ（キューは順番どおり）
        return rec

    def test_turning_it_off_stops_our_recording_once(self):
        obs = FakeOBS()

        self._recorder_after_turning_off(obs)

        self.assertEqual(obs.count("StartRecord"), 1)
        self.assertEqual(obs.count("StopRecord"), 1)

    def test_turning_it_off_leaves_a_hand_started_recording_alone(self):
        obs = FakeOBS(recording=True)

        self._recorder_after_turning_off(obs)

        self.assertEqual(obs.count("StopRecord"), 0)
        self.assertTrue(obs.recording)

    def test_turning_it_off_does_not_block_on_a_hanging_obs(self):
        release = threading.Event()
        self.addCleanup(release.set)
        entered = threading.Event()

        class Hanging:
            def connect(self):
                entered.set()
                release.wait(5)
                return True, ""

            def request(self, *_a):
                return True, {"outputActive": False}, ""

            def close(self):
                pass

        rec = Recorder.Recorder(client_factory=Hanging)
        rec.configure(True, log=lambda _m: None)
        rec.on_continue_start(1)
        entered.wait(3)                               # ワーカーが OBS 待ちで固まっている

        rec.configure(False)
        t0 = time.monotonic()
        rec.stop_all()
        self.assertLess(time.monotonic() - t0, 0.1)

    def test_a_bad_port_falls_back_to_the_default(self):
        app = self._app(port="abc", host="")

        self.assertEqual(mainGUI.App._obs_endpoint(app),
                         (config.OBS_DEFAULT_HOST, config.OBS_DEFAULT_PORT))

    def test_the_connection_test_does_not_record_or_log_the_password(self):
        app = self._app()
        server = FakeOBSServer(password="hunter2", record_active=False)
        self.addCleanup(server.close)
        app.v_obs_port.set(str(server.port))

        mainGUI.App._test_obs_connection(app)
        deadline = time.monotonic() + 5
        while not any("✅" in m or "❌" in m for m in app.logs) \
                and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertTrue(any("✅" in m for m in app.logs), app.logs)
        self.assertEqual(server.requests, ["GetRecordStatus"], "録画はしない")
        self.assertFalse(any("hunter2" in m for m in app.logs), app.logs)

    def test_stopping_the_macro_stops_our_recording(self):
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        stop = source[source.index("    def _stop(self):"):
                      source.index("    def _log(self, msg: str):")]
        self.assertIn("Recorder.stop_all(", stop)

    def test_the_password_entry_hides_the_text(self):
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        self.assertRegex(source, r"textvariable=self\.v_obs_password[^)]*show=\"\*\"")

    def test_no_new_dependency(self):
        """標準ライブラリだけで書いている"""
        for name in ("OBSClient.py", "Recorder.py"):
            source = Path(name).read_text(encoding="utf-8")
            imports = set(re.findall(r"^(?:import|from) (\w+)", source, re.M))
            self.assertLessEqual(imports, {"base64", "hashlib", "json", "os",
                                           "socket", "struct", "time", "queue",
                                           "threading", "typing", "config",
                                           "OBSClient"}, name)


class TestOBSPasswordStorage(unittest.TestCase):
    """OBS のパスワードは DPAPI で暗号化して保存する。平文は書かない"""

    PASSWORD = "hunter2-ｐａｓｓ"

    class FakeVar:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    # ── 暗号化そのもの（本物の DPAPI） ─────────────────
    def test_it_round_trips(self):
        stored = SecretStore.protect(self.PASSWORD)

        self.assertIsNotNone(stored)
        self.assertNotIn(self.PASSWORD, stored)
        self.assertNotIn(self.PASSWORD,
                         base64.b64decode(stored).decode("utf-8", "replace"))
        self.assertEqual(SecretStore.unprotect(stored), self.PASSWORD)

    def test_a_foreign_or_broken_value_is_none(self):
        """別のPC・別のユーザーの値は復号できない。壊れた値と同じく None"""
        for value in ("", "not base64!!", base64.b64encode(b"x" * 200).decode()):
            self.assertIsNone(SecretStore.unprotect(value), value)

    # ── 保存 ─────────────────────────────────
    def _save(self, password, stored=None):
        app = type("FakeApp", (), {})()
        app.tabs = []
        app.tool_rows = []
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_instance_link",
                     "v_freeze_8pages", "v_freeze_punish", "v_emergency_key",
                     "v_obs_enabled", "v_obs_host", "v_obs_port"):
            setattr(app, name, self.FakeVar(""))
        app.v_obs_password = self.FakeVar(password)
        app.v_freeze_rounds = {}
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update),              patch.object(mainGUI, "load_settings", return_value=dict(stored or {})):
            mainGUI.App._save_launch_settings(app)
        return saved

    def test_the_saved_file_has_no_plaintext(self):
        saved = self._save(self.PASSWORD)

        self.assertNotIn(self.PASSWORD, json.dumps(saved, ensure_ascii=False))
        self.assertNotIn("obs_password", saved)
        self.assertEqual(SecretStore.unprotect(saved["obs_password_dpapi"]),
                         self.PASSWORD)

    def test_an_old_plaintext_key_is_dropped_on_save(self):
        saved = self._save(self.PASSWORD, stored={"obs_password": "old-plain"})

        self.assertNotIn("obs_password", saved)
        self.assertNotIn("old-plain", json.dumps(saved, ensure_ascii=False))

    def test_an_empty_password_is_stored_empty(self):
        self.assertEqual(self._save("")["obs_password_dpapi"], "")

    def test_a_failed_encryption_never_falls_back_to_plaintext(self):
        with patch.object(SecretStore, "protect", return_value=None):
            saved = self._save(self.PASSWORD)

        self.assertEqual(saved["obs_password_dpapi"], "")
        self.assertNotIn(self.PASSWORD, json.dumps(saved, ensure_ascii=False))

    # ── 読み込み ─────────────────────────────────
    def _load(self, data):
        """_load_saved_settings を回す（他の項目は既存テストと同じ偽物）"""
        app = type("FakeApp", (), {})()
        app.tabs = []
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc",
                     "v_ton_entry", "v_ton_begin", "v_join_world",
                     "v_instance_link", "v_emergency_key", "v_freeze_8pages",
                     "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, self.FakeVar(""))
        app.v_freeze_rounds = {}
        app._add_tool_row = lambda p, save=True: None
        app._refresh_emergency_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._apply_obs_settings = lambda: None
        app._apply_saved_window_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app.logs = []
        app._log = app.logs.append
        written = []
        with patch.object(mainGUI, "load_settings", return_value=dict(data)),              patch.object(mainGUI, "save_settings", written.append):
            mainGUI.App._load_saved_settings(app)
        return app, written

    def test_it_is_restored(self):
        app, written = self._load({"obs_password_dpapi": SecretStore.protect(self.PASSWORD)})

        self.assertEqual(app.v_obs_password.get(), self.PASSWORD)
        self.assertEqual(written, [], "書き直さない")
        self.assertEqual(app.logs, [])

    def test_an_old_plaintext_password_is_migrated(self):
        app, written = self._load({"obs_password": self.PASSWORD, "tnl_path": ""})

        self.assertEqual(app.v_obs_password.get(), self.PASSWORD)
        self.assertEqual(len(written), 1, "その場で書き直す")
        self.assertNotIn("obs_password", written[0])
        self.assertNotIn(self.PASSWORD, json.dumps(written[0], ensure_ascii=False))
        self.assertEqual(SecretStore.unprotect(written[0]["obs_password_dpapi"]),
                         self.PASSWORD)

    def test_an_undecryptable_password_is_empty_and_asked_for_once(self):
        foreign = base64.b64encode(b"from another PC" * 10).decode()

        app, written = self._load({"obs_password_dpapi": foreign})

        self.assertEqual(app.v_obs_password.get(), "")
        messages = [m for m in app.logs if "入れ直してください" in m]
        self.assertEqual(len(messages), 1, app.logs)
        self.assertEqual(written, [])

    def test_the_exe_build_includes_the_module(self):
        """Nuitka のビルド行は win32 系を明示している。漏れると exe でだけ落ちる"""
        self.assertIn("--include-module=win32crypt",
                      Path("main.py").read_text(encoding="utf-8"))


# ── 霧の看破: 実ログ（--enable-sdk-log-levels 付き）から抜き出した固定データ ──
# 依頼者の実ログのパスには依存しない。公開の ID は「オフセット済み」
FOG_EARLY_READ_ROUNDS = [
    # (ログ・時刻, [NetworkProcessing] の行, 公開の行, 公開ID, 別名なしで決まるID, 別名ありで決まるID)
    ("16-30-13 16:38",
     ["2026.09.21 16:38:23 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [7] THE SUN because y_ui already owner"],
     "2026.09.21 16:39:14 Debug      -  Killers have been revealed - 6 0 0 // Round type is Fog",
     6, 6, 6),
    ("16-30-13 17:20",
     ["2026.09.21 17:20:30 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [32] Paradise Bird (1) because tsuki__2 already owner"],
     "2026.09.21 17:21:20 Debug      -  Killers have been revealed - 12 0 0 // Round type is Fog (Alternate)",
     146, 146, 146),
    ("16-30-13 17:31",
     ["2026.09.21 17:31:15 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [86] WALPURGISNACHT because tsuki__2 already owner",
      "2026.09.21 17:31:33 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [12] witchling (15) because tsuki__2 already owner",
      "2026.09.21 17:31:33 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [12] witchling because tsuki__2 already owner"],
     "2026.09.21 17:32:05 Debug      -  Killers have been revealed - 33 0 0 // Round type is Fog (Alternate)",
     167, 167, 167),
    ("18-59-24 22:04",
     ["2026.09.21 22:04:41 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [20] Immortal Snail because meteor? already owner"],
     "2026.09.21 22:05:31 Debug      -  Killers have been revealed - 101 0 0 // Round type is Fog",
     101, 101, 101),
    ("08-39-27 08:43",
     ["2026.09.21 08:43:40 Debug      -  [NetworkProcessing] serim01 would like to transfer [10] Kuro GuidingStar to すぅみ_suumi"],
     "2026.09.21 08:44:30 Debug      -  Killers have been revealed - 6 0 0 // Round type is Fog (Alternate)",
     140, None, 140),
    ("18-59-24 20:45",
     ["2026.09.21 20:45:59 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [29b] SmileyWalker because meteor? already owner",
      "2026.09.21 20:46:01 Debug      -  [NetworkProcessing] Transferred ownership of [29b] SmileyWalker to 5",
      "2026.09.21 20:46:01 Debug      -  [NetworkProcessing] serim01 would like to transfer [29b] SmileyWalker to meteor?",
      "2026.09.21 20:46:01 Error      -  [NetworkProcessing] Non-owner attempted to request ownership of [29b] SmileyWalker for someone else."],
     "2026.09.21 20:46:49 Debug      -  Killers have been revealed - 8 0 0 // Round type is Fog (Alternate)",
     142, None, 142),
]


def fog_early_read_terrors(with_aliases=False):
    """terrors.json の該当部分だけ（コミット済みの値を写した）。依頼者の作業中の
    terrors.json には依存しない"""
    def entry(tid, name, members, aliases=None):
        e = {"id": tid, "name": name, "terrors": members}
        if with_aliases and aliases:
            e["fog_names"] = aliases
        return e
    return ReadJson.normalize_terrors({
        "classic": [
            entry(6, "Black Sun", ["The Sun"]),
            entry(12, "An Arbiter", ["An Arbiter"]),
            entry(101, "Immortal Snail", ["Immortal Snail"]),
        ],
        "alternate": [
            entry(140, "The Knight of Toren", ["The Knight of Toren"],
                  ["Kuro GuidingStar"]),
            entry(142, "Smile Walker", ["Smile Walker"], ["SmileyWalker"]),
            entry(146, "Paradise Bird", ["Paradise Bird"]),
            entry(167, "Walpurgisnacht", ["Walpurgisnacht", "Unknown Witch"]),
        ],
    })


class TestInstanceAccess(unittest.TestCase):
    """看破してよいインスタンスか（Joining 行の公開範囲）"""

    def _access(self, tail):
        event = LogParser.parse("2026.09.21 16:30:39 Debug      -  [Behaviour] Joining "
                                "wrld_a5e9ec13-36b1-4e63-ae0c-dab9023401f9:94781"
                                + tail + "~region(jp)")
        return LogParser.instance_access(event.suffix)

    def test_the_nine_kinds(self):
        cases = {
            "~private(usr_0e01408a)": ("invite", True),
            "~private(usr_0e01408a)~canRequestInvite": ("invite_plus", True),
            "~friends(usr_0e01408a)": ("friends", True),
            "~group(grp_8f8ace13)~groupAccessType(members)": ("group_members", True),
            "~hidden(usr_0e01408a)": ("friends_plus", False),
            "~group(grp_8f8ace13)~groupAccessType(plus)": ("group_plus", False),
            "~group(grp_8f8ace13)~groupAccessType(public)": ("group_public", False),
            "": ("public", False),
        }
        for tail, (kind, allowed) in cases.items():
            access = self._access(tail)
            self.assertEqual(access, kind, tail)
            self.assertEqual(FogEarlyRead.early_read_allowed(access), allowed, tail)

    def test_what_cannot_be_read_is_ng(self):
        self.assertEqual(LogParser.instance_access(None), "unknown")
        self.assertFalse(FogEarlyRead.early_read_allowed("unknown"))
        self.assertFalse(FogEarlyRead.early_read_allowed(""), "Joining 行を見ていない")
        self.assertEqual(self._access("~group(grp_8f8ace13)"), "unknown")
        self.assertEqual(WindowState().instance_access, "", "既定は判定できない＝NG")

    def test_a_joining_line_sets_it_on_the_window(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None)
        monitor._process("2026.09.21 17:04:52 Debug      -  [Behaviour] Joining "
                         "wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd:54453~group("
                         "grp_8f8ace13-018b-47e6-a0f3-885831fd9bc8)~groupAccessType"
                         "(members)~region(jp)")

        self.assertEqual(monitor.st.instance_access, "group_members")


class TestEarlyReadLaunchFlag(unittest.TestCase):
    """起動方法（窓のログの先頭 8KB に --enable-sdk-log-levels があるか）"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _log(self, text):
        path = Path(self._dir.name) / "output_log.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_flag_at_the_head(self):
        path = self._log("2026.09.21 16:30:15 Debug      -  Arg: --enable-sdk-log-levels\n")
        self.assertTrue(FogEarlyRead.launched_for_early_read(path))

    def test_no_flag(self):
        path = self._log("2026.09.21 16:30:15 Debug      -  Arg: --enable-debug-gui\n")
        self.assertFalse(FogEarlyRead.launched_for_early_read(path))

    def test_the_flag_after_8kb_does_not_count(self):
        path = self._log("x" * (FogEarlyRead.LOG_HEAD_BYTES + 10)
                         + " Arg: --enable-sdk-log-levels\n")
        self.assertFalse(FogEarlyRead.launched_for_early_read(path))

    def test_an_unreadable_log_cannot(self):
        self.assertFalse(FogEarlyRead.launched_for_early_read(
            Path(self._dir.name) / "nope.txt"))

    def test_start_reads_it_once(self):
        path = self._log("Arg: --enable-sdk-log-levels\n")
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path), {}, lambda _m: None)
        with patch.object(monitor, "_start_daemon"), \
             patch.object(monitor._action, "start_velocity_receiver"):
            monitor.start()
        monitor.stop()

        self.assertTrue(monitor.early_read_capable)


class TestEarlyReadNames(unittest.TestCase):
    """看破の名前の取り出しと照合"""

    def _names(self, lines):
        return [LogParser.parse(line).player_name for line in lines]

    def test_the_real_log_table(self):
        """別名なし: 4件が決まり、2件は決まらない。別名あり: 6件すべて決まる。
        決まったものはどれも公開と一致する（食い違い0件）"""
        for with_aliases, col in ((False, 4), (True, 5)):
            data = fog_early_read_terrors(with_aliases)
            for row in FOG_EARLY_READ_ROUNDS:
                where, lines, _reveal, public = row[0], row[1], row[2], row[3]
                decided = {ReadJson.fog_terror_id_by_object_name(n, data)
                           for n in self._names(lines)} - {None}
                expected = row[col]
                self.assertEqual(decided, {expected} if expected else set(),
                                 f"{where} 別名={with_aliases}")
                if expected is not None:
                    self.assertEqual(expected, public, f"{where}: 公開と一致")

    def test_the_bracket_is_not_the_id(self):
        """[7] THE SUN は Black Sun（6）。[29b] のように数字でないこともある"""
        names = self._names([FOG_EARLY_READ_ROUNDS[0][1][0],
                             FOG_EARLY_READ_ROUNDS[5][1][0]])
        self.assertEqual(names, ["THE SUN", "SmileyWalker"])

    def test_every_line_shape_gives_the_name(self):
        for line in FOG_EARLY_READ_ROUNDS[5][1]:
            self.assertEqual(LogParser.parse(line).player_name, "SmileyWalker", line)
        self.assertEqual(LogParser.network_object_name(
            "[NetworkProcessing] Setting [12] witchling (15) to request ownership"),
            "witchling (15)")
        self.assertEqual(LogParser.parse(
            "2026.09.21 20:46:00 Debug      -  [NetworkProcessing] Setting [29b] "
            "SmileyWalker to request ownership").player_name, "SmileyWalker")
        self.assertEqual(LogParser.network_object_name(
            "[NetworkProcessing] Transferred ownership of [3] Express Train to Hell to 20"),
            "Express Train to Hell", "名前の中の to で切らない")

    def test_lines_without_an_object_are_not_events(self):
        for body in ("[NetworkProcessing] Received ownership transfer of 12 from 3 to 4",
                     "[NetworkProcessing] Transferred ownership of monsterDetectionBox to 9"):
            self.assertIsNone(LogParser.parse("2026.09.21 16:38:23 Debug      -  " + body))

    def test_normalization(self):
        data = fog_early_read_terrors()
        for name in ("Paradise Bird", "PARADISE BIRD", "paradisebird",
                     "Paradise  Bird (12)", " Paradise Bird (1) "):
            self.assertEqual(ReadJson.fog_terror_id_by_object_name(name, data), 146, name)
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Paradise Bird (x)", data))
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("1", data))

    def test_a_name_for_two_ids_is_none(self):
        data = ReadJson.normalize_terrors({
            "classic": [{"id": 1, "name": "A", "terrors": ["Twin"]}],
            "alternate": [{"id": 140, "name": "B", "terrors": ["twin"]}]})
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Twin", data))

    def test_fog_names_are_for_early_read_only(self):
        data = fog_early_read_terrors(with_aliases=True)
        self.assertEqual(ReadJson.fog_terror_id_by_object_name("Kuro GuidingStar", data), 140)
        self.assertIsNone(ReadJson.fog_terror_id_by_name("Kuro GuidingStar", data), "Enrage には使わない")
        self.assertIsNone(ReadJson.terror_id_by_name("Kuro GuidingStar", data))

    def test_broken_fog_names_are_ignored(self):
        data = ReadJson.normalize_terrors({"alternate": [
            {"id": 140, "name": "K", "terrors": [], "fog_names": "Kuro"},
            {"id": 141, "name": "T", "terrors": [], "fog_names": [3, None, " ", "Deal2"]}]})
        self.assertIsNone(ReadJson.fog_terror_id_by_object_name("Kuro", data))
        self.assertEqual(ReadJson.fog_terror_id_by_object_name("Deal2", data), 141)


class TestFogEarlyReadUse(unittest.TestCase):
    """看破・Enrage 系の使い分け（インスタンス × 起動方法）。

    看破だけがインスタンスで分かれる。通常のログに出る Enrage / Joy / スタンは
    どのインスタンスでも表示して判定し、DB へも普通に送る。
    """

    FOG_KEY = "Fog/霧"
    SNAIL = 101
    SNAIL_LINE = FOG_EARLY_READ_ROUNDS[3][1][0]
    SNAIL_REVEAL = FOG_EARLY_READ_ROUNDS[3][2]
    KURO_LINE = FOG_EARLY_READ_ROUNDS[4][1][0]
    UNKNOWN = ("2026.09.21 22:04:41 Debug      -  Killers is unknown - ??? // "
               "Will be revealed after 50 seconds // Round type is Fog")

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.trust = FogEarlyRead.NameTrust(Path(self._dir.name) / "names.json")
        for p in (patch.object(config, "TERRORS", fog_early_read_terrors()),
                  patch.object(FogEarlyRead, "trust", self.trust),
                  patch.object(config, "FOG_EARLY_READ_ENABLED", True)):
            p.start()
            self.addCleanup(p.stop)
        self.send = self._start(patch.object(ConnectDB, "send_ToNRoundStatistics"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self.printed = self._start(patch("builtins.print"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, access="invite", capable=True, keep=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep if keep is not None else {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.early_read_capable = capable
        monitor.st.in_round = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._process(self.UNKNOWN)
        return monitor

    def _skipped(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs].count("do_skip")

    def _judged(self, monitor):
        """判定に使われたか（自爆・続行アナウンス・フリーズ・録画のどれか）"""
        return bool(self._skipped() or self.play.called or self.record.called
                    or monitor.st.is_continue_round
                    or SharedState.get_continue_round_count())

    # ── OK・看破できる ─────────────────────────
    def test_ok_capable_judges_by_early_read_and_skips(self):
        monitor = self._monitor()

        monitor._process(self.SNAIL_LINE)

        self.assertEqual(self._skipped(), 1)
        self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True)
        self.assertTrue(any("🔎 テラー判明(看破)" in m for m in monitor.logs), monitor.logs)

    def test_ok_capable_judges_by_early_read_and_continues(self):
        monitor = self._monitor(keep={self.FOG_KEY: {self.SNAIL}})

        monitor._process(self.SNAIL_LINE)

        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3")
        self.assertEqual(self._skipped(), 0)

    def test_ok_capable_falls_back_to_enrage(self):
        monitor = self._monitor()

        monitor._process(self.KURO_LINE)            # 別名が無いので決まらない
        self.assertFalse(self._judged(monitor))
        monitor._on_enrage("Immortal Snail")

        self.assertEqual(self._skipped(), 1)
        self.assertEqual(self.send.call_count, 1)

    def test_an_enrage_after_the_early_read_is_not_used_again(self):
        monitor = self._monitor()
        monitor._process(self.SNAIL_LINE)

        monitor._on_enrage("Black Sun")                 # Enrage 系が後から来ても
        monitor._process("2026.09.21 22:04:50 Debug      -  JOY WILL SOON AWAKEN...")
        monitor._process(self.SNAIL_REVEAL)

        self.assertEqual(self._skipped(), 1, "二重に自爆しない")
        self.assertEqual(self.send.call_count, 1)

    def test_an_early_read_after_the_enrage_is_not_used_again(self):
        """Enrage 系が先に決めたら、後から見えた看破は判定にも表示にも使わない"""
        monitor = self._monitor()
        monitor._on_enrage("Immortal Snail")

        monitor._process(self.SNAIL_LINE)

        self.assertEqual(self._skipped(), 1)
        self.assertEqual(self.send.call_count, 1)
        self.assertIsNone(monitor.st.early_read_tid)
        self.assertEqual(sum("🔎" in m for m in monitor.logs), 1, monitor.logs)

    # ── OK・看破できない ────────────────────────
    def test_ok_not_capable_ignores_the_lines_but_uses_the_enrage_family(self):
        for how in ("Enrage", "Joy", "Stunned"):
            self.thread.reset_mock()
            self.send.reset_mock()
            monitor = self._monitor(capable=False)

            monitor._process(self.SNAIL_LINE)
            self.assertEqual(self._skipped(), 0, f"{how}: 看破の行は読まない")
            self.send.assert_not_called()
            if how == "Enrage":
                monitor._on_enrage("Immortal Snail")
            elif how == "Joy":
                monitor._process("2026.09.21 22:04:50 Debug      -  JOY WILL SOON AWAKEN...")
            else:
                monitor._on_stunned("Immortal Snail")

            self.assertEqual(self._skipped(), 1, how)
            self.assertEqual(self.send.call_count, 1, how)
            self.assertFalse(self.send.call_args.kwargs["quiet"], f"{how}: 公開と同じく普通に送る")

    # ── NG・看破できる ──────────────────────────
    def test_ng_capable_sends_to_the_db_only(self):
        monitor = self._monitor(access="friends_plus",
                                keep={self.FOG_KEY: {self.SNAIL}})

        monitor._process(self.SNAIL_LINE)

        self.assertFalse(self._judged(monitor), "自爆・アナウンス・フリーズ・録画のどれもしない")
        self.send.assert_called_once_with("Fog", [self.SNAIL], 0, None, quiet=True)

    # ── NG・看破できない ────────────────────────
    JOY_LINE = "2026.09.21 22:04:50 Debug      -  JOY WILL SOON AWAKEN..."

    def _identify_by(self, monitor, how):
        if how == "Enrage":
            monitor._on_enrage("Immortal Snail")
        elif how == "Joy":
            monitor._process(self.JOY_LINE)
        else:
            monitor._process("2026.09.21 22:04:50 Debug      -  Immortal Snail was stunned.")

    def test_ng_not_capable_judges_by_the_enrage_family_and_skips(self):
        for how, tid in (("Enrage", self.SNAIL), ("Joy", config.JOY_ID),
                         ("Stunned", self.SNAIL)):
            self.thread.reset_mock()
            self.send.reset_mock()
            monitor = self._monitor(access="public", capable=False)

            self._identify_by(monitor, how)

            self.assertTrue(any(f"🔎 テラー判明({how})" in m for m in monitor.logs),
                            (how, monitor.logs))
            self.assertEqual(self._skipped(), 1, how)
            self.assertEqual(self.send.call_count, 1, how)
            self.assertEqual(self.send.call_args.args[1], [tid], how)
            self.assertFalse(self.send.call_args.kwargs["quiet"], how)

    def test_ng_not_capable_judges_by_the_enrage_family_and_continues(self):
        for how, tid in (("Enrage", self.SNAIL), ("Joy", config.JOY_ID),
                         ("Stunned", self.SNAIL)):
            self.thread.reset_mock()
            self.play.reset_mock()
            SharedState.continue_round_reset()
            monitor = self._monitor(access="public", capable=False,
                                    keep={self.FOG_KEY: {tid}})

            self._identify_by(monitor, how)

            self.assertTrue(monitor.st.is_continue_round, how)
            self.play.assert_called_once_with("continue.mp3")
            self.assertEqual(self._skipped(), 0, how)

    def test_ng_sends_only_once_per_round(self):
        monitor = self._monitor(access="group_plus")

        monitor._process(self.SNAIL_LINE)
        monitor._on_enrage("Immortal Snail")
        monitor._process(self.SNAIL_REVEAL)

        self.assertEqual(self.send.call_count, 1)

    def test_ng_the_enrage_after_a_silent_early_read_judges_once(self):
        """看破は黙って DB に送るだけ。後から来た Enrage でその場で判定し、公開で二重にしない"""
        monitor = self._monitor(access="unknown")
        monitor._process(self.SNAIL_LINE)
        self.assertEqual(self._skipped(), 0, "看破では判定しない")
        self.assertFalse(any("🔎" in m for m in monitor.logs), monitor.logs)

        monitor._on_enrage("Immortal Snail")
        self.assertEqual(self._skipped(), 1, "Enrage でその場で自爆する")
        self.assertTrue(any("🔎 テラー判明(Enrage)" in m for m in monitor.logs), monitor.logs)

        monitor._process(self.SNAIL_REVEAL)
        self.assertEqual(self._skipped(), 1, "公開で二重に自爆しない")

    # ── ログを流さない ───────────────────────────
    def test_nothing_about_the_early_read_is_shown_in_ng(self):
        monitor = self._monitor(access="public",
                                keep={self.FOG_KEY: {self.SNAIL}})
        before = list(monitor.logs)

        monitor._process(self.SNAIL_LINE)
        monitor._process(FOG_EARLY_READ_ROUNDS[0][1][0])  # 別の名前（取り違え）も出さない

        self.assertEqual(monitor.logs, before, "画面のログ・オーバーレイに何も足されない")
        self.printed.assert_not_called()
        self.play.assert_not_called()

    def test_an_unknown_enrage_name_is_logged_in_ng(self):
        monitor = self._monitor(access="public")

        monitor._on_enrage("存在しないテラー名XYZ")

        self.assertTrue(any("Enrage: 存在しないテラー名XYZ" in m for m in monitor.logs),
                        monitor.logs)

    def test_the_early_read_db_send_is_quiet_even_in_ok(self):
        monitor = self._monitor()

        monitor._process(self.SNAIL_LINE)

        self.assertTrue(self.send.call_args.kwargs["quiet"])
        self.assertFalse(any("Supabase" in m or "送信" in m for m in monitor.logs))

    def test_a_normal_round_is_still_sent_loudly(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"
        monitor.st.fog = False

        monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        self.assertFalse(self.send.call_args.kwargs["quiet"])

    # ── 同じラウンドで2種類 ─────────────────────────
    def test_two_different_ids_void_the_round(self):
        """先に来た名前で決めた後に別のIDが見えたら、以後このラウンドの看破は使わない"""
        monitor = self._monitor(access="public")         # DB だけの窓で見る
        monitor._process(FOG_EARLY_READ_ROUNDS[0][1][0])  # THE SUN → 6

        monitor._process(self.SNAIL_LINE)                 # 101

        self.assertTrue(monitor.st.early_read_void)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[1], [6])

    def test_two_different_ids_before_use_are_not_used(self):
        monitor = self._monitor()
        monitor.st.early_read_hits = {"thesun": (6, "THE SUN"), "x": (101, "x")}
        monitor.st.early_read_void = True

        monitor._process(FOG_EARLY_READ_ROUNDS[3][1][0])

        self.assertFalse(self._judged(monitor))
        self.send.assert_not_called()

    # ── スイッチ ──────────────────────────────
    def test_the_switch_turns_the_lines_off_but_not_the_enrage_rules(self):
        with patch.object(config, "FOG_EARLY_READ_ENABLED", False):
            ok = self._monitor()
            ok._process(self.SNAIL_LINE)
            self.assertEqual(self._skipped(), 0, "看破の行は読まない")
            ok._on_enrage("Immortal Snail")
            self.assertEqual(self._skipped(), 1, "OK なら Enrage で判定")

            self.send.reset_mock()
            ng = self._monitor(access="public")
            ng._process(self.SNAIL_LINE)
            self.assertEqual(self._skipped(), 1, "看破の行は読まない")
            ng._on_enrage("Immortal Snail")
            self.assertEqual(self._skipped(), 2, "NG でも Enrage で判定")
            self.assertEqual(self.send.call_count, 1)


class TestFogEarlyReadAnswerCheck(unittest.TestCase):
    """答え合わせ（一度でも公開と食い違った名前は以後使わない）"""

    UNKNOWN = TestFogEarlyReadUse.UNKNOWN

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "fog_object_names.json"
        self.trust = FogEarlyRead.NameTrust(self.path)
        for p in (patch.object(config, "TERRORS", fog_early_read_terrors(True)),
                  patch.object(FogEarlyRead, "trust", self.trust),
                  patch.object(ConnectDB, "send_ToNRoundStatistics"),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(Recorder, "on_continue_start")):
            p.start()
            self.addCleanup(p.stop)
        self.send = ConnectDB.send_ToNRoundStatistics

    def _monitor(self, access="invite"):
        monitor = LogMonitor.LogMonitor(WindowConfig(do_skip=True), {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.early_read_capable = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _round(self, monitor, lines, reveal):
        monitor._process("2026.09.21 22:04:00 Debug      -  This round is taking place "
                         "at Facility (12) and the round type is Fog")
        monitor._process(self.UNKNOWN)
        for line in lines:
            monitor._process(line)
        logs_before_reveal = list(monitor.logs)
        if reveal:
            monitor._process(reveal)
        return logs_before_reveal

    def test_the_real_rounds_all_match(self):
        """実ログの6件を流す。食い違いは0件（別名ありの固定データ）"""
        monitor = self._monitor()
        for _where, lines, reveal, *_rest in FOG_EARLY_READ_ROUNDS:
            self._round(monitor, lines, reveal)

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual({k: v.get("mismatch", 0) for k, v in data.items()},
                         {k: 0 for k in data})
        self.assertEqual(sum(v["match"] for v in data.values()), 6)

    def test_an_alternate_reveal_is_offset_before_comparing(self):
        """33 は Fog (Alternate) なら 167 Walpurgisnacht。オフセット前で比べると Luigi 扱いになる"""
        monitor = self._monitor()
        row = FOG_EARLY_READ_ROUNDS[2]

        self._round(monitor, row[1], row[2])

        self.assertEqual(self.trust.counts("walpurgisnacht"), (1, 0))

    def test_a_match_is_counted(self):
        monitor = self._monitor()
        row = FOG_EARLY_READ_ROUNDS[3]

        self._round(monitor, row[1], row[2])
        self._round(monitor, row[1], row[2])

        self.assertEqual(self.trust.counts("immortalsnail"), (2, 0))

    def test_a_mismatch_retires_the_name_and_warns_after_the_reveal_only(self):
        monitor = self._monitor(access="public")
        snail = FOG_EARLY_READ_ROUNDS[3][1]
        wrong = ("2026.09.21 22:05:31 Debug      -  Killers have been revealed - "
                 "12 0 0 // Round type is Fog")

        before = self._round(monitor, snail, wrong)

        self.assertFalse(any("食い違い" in m for m in before), "公開の前には出さない")
        warnings = [m for m in monitor.logs if "看破の名前「Immortal Snail」" in m]
        self.assertEqual(len(warnings), 1, monitor.logs)
        self.assertFalse(self.trust.usable("immortalsnail"))

        self.send.reset_mock()
        ok = self._monitor()
        self._round(ok, snail, None)
        self.send.assert_not_called()                  # DB にも使わない
        self.assertIsNone(ok.st.early_read_tid, "判定にも使わない")

    def test_it_survives_on_disk(self):
        self.trust.record("immortalsnail", False)

        again = FogEarlyRead.NameTrust(self.path)

        self.assertFalse(again.usable("immortalsnail"))
        self.assertTrue(again.usable("thesun"))

    def test_a_broken_file_is_empty(self):
        for text in ("{not json", "[1, 2]", '{"x": 3, "y": {"mismatch": "a"}}'):
            self.path.write_text(text, encoding="utf-8")
            trust = FogEarlyRead.NameTrust(self.path)

            self.assertTrue(trust.usable("thesun"), text)
            trust.record("thesun", True)                 # 落ちない
            self.assertEqual(trust.counts("thesun")[0], 1, text)

    def test_a_round_that_ends_before_the_reveal_is_not_checked(self):
        monitor = self._monitor()
        self._round(monitor, FOG_EARLY_READ_ROUNDS[3][1], None)

        monitor._process("2026.09.21 22:05:00 Debug      -  RoundOver")

        self.assertEqual(self.trust.counts("immortalsnail"), (0, 0))
        self.assertFalse(monitor.st.fog_reading)


class TestFogVariantsInNg(unittest.TestCase):
    """Variant の合図（Foxy など）は通常のログ。看破 NG の霧でもその場で出して判定する"""

    PREFIX = "2026.09.21 22:04:45 Debug      -  "
    SIGNALS = {
        "Foxy": "foxy the pirate turned evil!",
        "Bloodthirsty": "The creature is bloodthirsty today...",
        "Hungry Home Invader": "I hear strange sounds coming from the kitchen.",
        "Gigabytes": "The Gigabytes have come.",
        "Atrached": "Lets play a game...",
    }

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        self.send = self._start(patch.object(ConnectDB, "send_ToNRoundStatistics"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))
        self.record = self._start(patch.object(Recorder, "on_continue_start"))
        self.printed = self._start(patch("builtins.print"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, access, round_type="Fog", keep=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_foxy="foxy.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.logs = []
        monitor.logger = monitor.logs.append
        if round_type == "Fog":
            monitor._process(self.PREFIX + "Killers is unknown - ??? // Will be "
                             "revealed after 50 seconds // Round type is Fog")
        return monitor

    def _started(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs]

    # ── NG の霧・公開前 ─────────────────────────
    def test_ng_fog_signals_are_shown_on_the_spot(self):
        expected = {"Foxy": "🦊 Foxyが出た！", "Bloodthirsty": "の合図"}   # 霧が対象の合図
        for source, mark in expected.items():
            monitor = self._monitor("public")

            monitor._process(self.PREFIX + self.SIGNALS[source])

            self.assertTrue(any(mark in m for m in monitor.logs), (source, monitor.logs))

    def test_ng_fog_foxy_is_shown_and_judged_on_the_spot(self):
        monitor = self._monitor("group_plus")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_any_call("foxy.mp3")
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID], "Foxy で確定")
        self.assertEqual(self._started().count("do_skip"), 1)
        self.assertFalse(self.send.call_args.kwargs["quiet"])

        sanic_in_log = config.SANIC_ID - MatchTNL.ALTERNATE_OFFSET
        monitor._process(self.PREFIX + f"Killers have been revealed - {sanic_in_log} 0 0 "
                         "// Round type is Fog (Alternate)")
        self.assertEqual(self._started().count("do_skip"), 1, "公開で二重に自爆しない")

    def test_after_the_reveal_it_is_shown_as_before(self):
        monitor = self._monitor("public")
        monitor._process(self.PREFIX + "Killers have been revealed - 101 0 0 "
                         "// Round type is Fog")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_called_with("foxy.mp3")

    # ── OK の霧は今までどおり ─────────────────────
    def test_ok_fog_foxy_is_as_before(self):
        monitor = self._monitor("invite")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_any_call("foxy.mp3")
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID], "Foxy で確定")
        self.assertIn("do_skip", self._started())

    def test_ok_fog_bloodthirsty_signal_is_logged(self):
        monitor = self._monitor("friends")

        monitor._process(self.PREFIX + self.SIGNALS["Bloodthirsty"])

        self.assertTrue(any("の合図" in m for m in monitor.logs), monitor.logs)

    # ── 霧以外は今までどおり ──────────────────────
    def test_other_rounds_are_as_before_even_in_ng(self):
        expected = {"Gigabytes": "👾", "Atrached": "🎮",
                    "Hungry Home Invader": "の合図", "Bloodthirsty": "の合図"}
        for source, mark in expected.items():
            monitor = self._monitor("public", round_type="Classic")

            monitor._process(self.PREFIX + self.SIGNALS[source])

            self.assertTrue(any(mark in m for m in monitor.logs), (source, monitor.logs))

    def test_foxy_outside_fog_is_as_before_even_in_ng(self):
        monitor = self._monitor("public", round_type="Alternate")

        monitor._process(self.PREFIX + self.SIGNALS["Foxy"])

        self.assertTrue(any("🦊" in m for m in monitor.logs), monitor.logs)
        self.play.assert_called_once_with("foxy.mp3")


class TestFogStunned(unittest.TestCase):
    """スタンによる霧のテラー判明。Enrage 系として使い分けに乗る"""

    FOG_KEY = "Fog/霧"
    WITCH = 167            # Walpurgisnacht（個体名 Unknown Witch）
    UNKNOWN = ("2026.09.21 17:31:15 Debug      -  Killers is unknown - ??? // "
               "Will be revealed after 50 seconds // Round type is Fog")
    STUN = "2026.09.21 17:31:40 Debug      -  Unknown Witch was stunned."

    def setUp(self):
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(SharedState.set_list_source, None)
        for p in (patch.object(config, "TERRORS", fog_early_read_terrors()),
                  patch.object(Recorder, "on_continue_start")):
            p.start()
            self.addCleanup(p.stop)
        self.send = self._start(patch.object(ConnectDB, "send_ToNRoundStatistics"))
        self.thread = self._start(patch.object(LogMonitor.threading, "Thread"))
        self.play = self._start(patch.object(PlaySound, "play_sound"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _monitor(self, access, keep=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep or {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = access
        monitor.st.in_round = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._process(self.UNKNOWN)
        return monitor

    def _skipped(self):
        return [c.kwargs["target"].__func__.__name__
                for c in self.thread.call_args_list if "target" in c.kwargs].count("do_skip")

    def test_ok_instance_decides_by_the_stun(self):
        monitor = self._monitor("invite", keep={self.FOG_KEY: {self.WITCH}})

        monitor._process(self.STUN)

        self.assertEqual(monitor.st.enrage_identified, self.WITCH)
        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3")
        self.assertTrue(any("テラー判明(Stunned)" in m for m in monitor.logs), monitor.logs)
        self.assertFalse(self.send.call_args.kwargs["quiet"])

    def test_ng_instance_decides_by_the_stun_too(self):
        """スタンは通常のログ。看破 NG のインスタンスでも表示して判定する"""
        monitor = self._monitor("public", keep={self.FOG_KEY: {self.WITCH}})

        monitor._process(self.STUN)

        self.assertEqual(monitor.st.enrage_identified, self.WITCH)
        self.assertTrue(monitor.st.is_continue_round)
        self.play.assert_called_once_with("continue.mp3")
        self.assertTrue(any("テラー判明(Stunned)" in m for m in monitor.logs), monitor.logs)
        self.send.assert_called_once_with("Fog", [self.WITCH], 0, None, quiet=False)

    def test_an_unknown_name_is_logged_in_any_instance(self):
        for access in ("invite", "public"):
            monitor = self._monitor(access)

            monitor._process("2026.09.21 17:31:40 Debug      -  存在しない名前XYZ was stunned.")

            self.assertTrue(any("Stunned: 存在しない名前XYZ" in m for m in monitor.logs),
                            (access, monitor.logs))

    def test_only_while_the_fog_terror_is_unknown(self):
        monitor = self._monitor("invite")
        monitor._process("2026.09.21 17:32:05 Debug      -  Killers have been revealed - "
                         "33 0 0 // Round type is Fog (Alternate)")
        self.thread.reset_mock()

        monitor._process(self.STUN)

        self.assertEqual(self._skipped(), 0, "公開の後のスタンで判定し直さない")

    def test_the_switch(self):
        with patch.object(config, "STUNNED_IDENTIFY_ENABLED", False):
            monitor = self._monitor("invite")
            monitor._process(self.STUN)

        self.assertIsNone(monitor.st.enrage_identified)
        self.send.assert_not_called()


class TestQuietStatistics(unittest.TestCase):
    """看破・Enrage 系の DB 送信は print も出さない"""

    class RunNow:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            self.target()

    def _send(self, quiet, configured=True, fail=True):
        with patch.object(ConnectDB, "_configured", return_value=configured), \
             patch.object(ConnectDB, "_url", return_value="https://example.invalid/x"), \
             patch.object(ConnectDB, "_headers", return_value={}), \
             patch.object(ConnectDB.threading, "Thread", self.RunNow), \
             patch.object(ConnectDB.urllib.request, "urlopen",
                          side_effect=OSError("offline") if fail else None), \
             patch("builtins.print") as printed:
            ConnectDB.send_ToNRoundStatistics("Fog", [101], 12, 99, quiet=quiet)
        return printed

    def test_quiet_prints_nothing(self):
        self._send(True).assert_not_called()
        self._send(True, configured=False).assert_not_called()

    def test_loud_still_prints(self):
        self.assertTrue(self._send(False).called)
        self.assertTrue(self._send(False, configured=False).called)


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


class TestTerrorIdByName(unittest.TestCase):
    """名前 → ID の逆引き。完全一致・一意のときだけ"""

    def _data(self, classic):
        return {"classic": classic, "alternate": {}, "unbound": {}}

    def test_an_exact_name_resolves(self):
        self.assertEqual(
            ReadJson.terror_id_by_name("The Pursuer", config.TERRORS), 99)

    def test_the_fixed_spelling_resolves(self):
        """依頼者が terrors.json を直した箇所。SOS → S.O.S"""
        self.assertEqual(ReadJson.terror_id_by_name("S.O.S", config.TERRORS), 97)

    def test_a_partial_name_does_not_resolve(self):
        """Mona が Mona & Mona & Mona & Mona に当たらないこと"""
        self.assertIsNone(ReadJson.terror_id_by_name("Mona", config.TERRORS))
        self.assertEqual(
            ReadJson.terror_id_by_name("Mona & Mona & Mona & Mona",
                                       config.TERRORS), 233)

    def test_an_individual_name_does_not_resolve(self):
        """terrors.json だけを見た場合。別名表は渡していない"""
        for name in ("Furnace", "BooBooBaby", "Deal", "Stringman",
                     "GlaggleLand Disruptor", "[REDACTED]"):
            self.assertIsNone(ReadJson.terror_id_by_name(name, config.TERRORS),
                              name)

    def test_a_duplicate_name_resolves_to_nothing(self):
        data = self._data({"1": "Twin", "2": "Twin", "3": "Solo"})

        self.assertIsNone(ReadJson.terror_id_by_name("Twin", data))
        self.assertEqual(ReadJson.terror_id_by_name("Solo", data), 3)

    def test_junk_resolves_to_nothing(self):
        for name in ("", "   ", None, 123):
            self.assertIsNone(ReadJson.terror_id_by_name(name, config.TERRORS),
                              repr(name))

    def test_the_case_is_not_absorbed(self):
        self.assertIsNone(ReadJson.terror_id_by_name("the pursuer",
                                                     config.TERRORS))

    def test_the_index_follows_a_new_table(self):
        first = self._data({"1": "A"})
        second = self._data({"5": "B"})

        self.assertEqual(ReadJson.terror_id_by_name("A", first), 1)
        self.assertEqual(ReadJson.terror_id_by_name("B", second), 5)
        self.assertIsNone(ReadJson.terror_id_by_name("A", second))


class TestTerrorsJsonFormat(unittest.TestCase):
    """terrors.json の新しい形（カテゴリごとの配列）と旧い形（辞書）"""

    NEW = {
        "classic": [
            {"id": 0, "name": "Huggy", "terrors": ["Huggy"]},
            {"id": 1, "name": "Corrupted Toys", "terrors": ["Corrupted Woody"]},
            {"id": 22, "name": "Starved", "terrors": ["Starved", "Furnace"]},
            {"id": 24, "name": "The Guidance", "terrors": ["The Guidance"]},
            {"id": 26, "name": "Nextbots", "terrors": ["Bear5", "Obunga"]},
            {"id": 49, "name": "Mona", "terrors": ["Mona"]},
            {"id": 191, "name": "Atrached", "terrors": ["Atrached"]},
        ],
        "alternate": [
            {"id": 149, "name": "Feddys", "terrors": ["Bear5", "Freddy"]},
            {"id": 151, "name": "The Observation",
             "terrors": ["The Observation", "The Guidance"]},
            {"id": 167, "name": "Walpurgisnacht", "terrors": ["Walpurgisnacht"]},
        ],
        "unbound": [
            {"id": 200, "name": "Guidance & The Booboo's",
             "terrors": ["The Guidance", "BooBooBabies"]},
            {"id": 281, "name": "Atrached Pack", "terrors": ["Atrached"]},
        ],
        "8pages": [
            {"id": "49 23 0", "name": "SM64.Z64", "terrors": ["SM64.Z64"]},
        ],
    }

    def _data(self):
        return ReadJson.normalize_terrors(
            {k: [dict(e) for e in v] for k, v in self.NEW.items()})

    def _fog(self, name):
        return ReadJson.fog_terror_id_by_name(name, self._data())

    # ── 読める ─────────────────────────────
    def test_the_real_file_loads(self):
        """中身は固定しない——依頼者が随時書き換えるため"""
        for category in ReadJson.MAIN_CATEGORIES:
            self.assertIsInstance(config.TERRORS.get(category), dict, category)
            for id_, name in config.TERRORS[category].items():
                self.assertTrue(id_.isdigit(), (category, id_))
                self.assertIsInstance(name, str, (category, id_))

    def test_ids_and_names_resolve_in_the_new_shape(self):
        data = self._data()

        self.assertEqual(ReadJson.terror_name(1, data), "Corrupted Toys")
        self.assertEqual(ReadJson.terror_name(167, data), "Walpurgisnacht")
        self.assertEqual(ReadJson.terror_id("Starved", data), 22)
        self.assertIsNone(ReadJson.terror_name(9999, data))

    def test_the_old_shape_still_works(self):
        old = {"classic": {"1": "Corrupted Toys"}, "alternate": {"167": "Walpurgisnacht"},
               "unbound": {}}

        data = ReadJson.normalize_terrors(old)

        self.assertEqual(ReadJson.terror_name(1, data), "Corrupted Toys")
        self.assertEqual(ReadJson.terror_id("Walpurgisnacht", data), 167)
        self.assertEqual(ReadJson.fog_terror_id_by_name("Walpurgisnacht", data), 167)

    def test_statistics_see_the_same_shape(self):
        """統計画面は {カテゴリ: {"ID": 名前}} を前提にしている"""
        data = self._data()

        self.assertEqual(Statistics.candidate_ids_for_category("Alternate", data),
                         {149, 151, 167})
        self.assertEqual(Statistics.terror_name(24, data), "The Guidance")

    def test_string_ids_stay_out_of_the_id_lookup(self):
        """8pages の "49 23 0" が classic 49 の引きを壊さない"""
        data = self._data()

        self.assertEqual(ReadJson.terror_name(49, data), "Mona")
        self.assertEqual(data["8pages"][0]["id"], "49 23 0", "読めるようにだけしておく")

    def test_broken_entries_are_skipped(self):
        data = ReadJson.normalize_terrors({"classic": [
            {"id": 1, "name": "Ok"}, "junk", {"id": True, "name": "Bool"},
            {"id": "x", "name": "NotANumber"}, {"id": "7", "name": "Digits"},
            {"id": 8}, {"id": 9, "name": "NoIndividuals", "terrors": None},
        ]})

        self.assertEqual(data["classic"], {"1": "Ok", "7": "Digits",
                                           "9": "NoIndividuals"})

    # ── 霧の個体名 ───────────────────────────
    def test_an_individual_name_resolves(self):
        self.assertEqual(self._fog("Corrupted Woody"), 1)
        self.assertEqual(self._fog("Furnace"), 22)

    def test_a_terror_name_still_resolves(self):
        self.assertEqual(self._fog("Walpurgisnacht"), 167)

    def test_unbound_is_not_consulted(self):
        """unbound まで引くと Atrached が 191/281 で決まらなくなる"""
        self.assertEqual(self._fog("Atrached"), 191)
        self.assertIsNone(self._fog("BooBooBabies"))

    def test_the_terror_name_wins_a_tie(self):
        """The Observation の The Guidance は65秒後。判明前なら classic 24"""
        self.assertEqual(self._fog("The Guidance"), 24)

    def test_two_individuals_do_not_resolve(self):
        """Bear5 はどちらも個体名。決めずに revealed を待つ"""
        self.assertIsNone(self._fog("Bear5"))

    def test_no_partial_match(self):
        self.assertIsNone(self._fog("Bear"))
        self.assertIsNone(self._fog("corrupted woody"))

    def test_the_alias_file_is_no_longer_read(self):
        """terror_aliases.json は廃止した。依頼者が後で消す"""
        self.assertFalse(hasattr(config, "TERROR_ALIASES"))
        self.assertFalse(hasattr(config, "TERROR_ALIASES_PATH"))
        self.assertFalse(hasattr(ReadJson, "load_terror_aliases"))

    def test_the_build_no_longer_bundles_it(self):
        build = Path("main.py").read_text(encoding="utf-8")

        self.assertNotIn("terror_aliases.json", build)
        self.assertIn("--include-data-files=terrors.json=terrors.json", build)

    def test_junk_resolves_to_nothing(self):
        for name in ("", None, 123):
            self.assertIsNone(self._fog(name), repr(name))

    def test_the_enrage_uses_the_individual_names(self):
        cfg = WindowConfig(do_skip=True)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = "Fog"

        with patch.object(config, "TERRORS", self._data()), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._on_enrage("Corrupted Woody")

        self.assertEqual(monitor.st.terror_ids, [1])


class TestIsAlternateTerror(unittest.TestCase):
    """terrors.json のカテゴリでオルタネイト枠を判定する"""

    WALPURGISNACHT = 167     # alternate
    STARVED = 22             # classic
    SELF_INSERTS = 283       # unbound

    def test_the_categories_do_not_overlap(self):
        """重複があると、この判定そのものが成り立たない"""
        seen = {}
        for category in ("classic", "alternate", "unbound"):
            for id_ in (config.TERRORS.get(category) or {}):
                self.assertNotIn(int(id_), seen,
                                 f"{id_} が {seen.get(int(id_))} と重複")
                seen[int(id_)] = category

    def test_an_alternate_terror(self):
        self.assertTrue(ReadJson.is_alternate_terror(self.WALPURGISNACHT,
                                                     config.TERRORS))

    def test_a_classic_terror(self):
        self.assertFalse(ReadJson.is_alternate_terror(self.STARVED,
                                                      config.TERRORS))

    def test_an_unbound_terror(self):
        self.assertFalse(ReadJson.is_alternate_terror(self.SELF_INSERTS,
                                                      config.TERRORS))

    def test_junk_does_not_raise(self):
        for tid in (None, "167", 999999, -1, True, 1.5):
            self.assertFalse(ReadJson.is_alternate_terror(tid, config.TERRORS),
                             repr(tid))

    def test_a_table_without_the_category_does_not_raise(self):
        for data in ({}, {"classic": {"1": "A"}}, {"alternate": None}):
            self.assertFalse(ReadJson.is_alternate_terror(167, data), data)

    def test_every_alternate_id_is_above_the_offset_range(self):
        """135以下だと apply_alternate_offset が二重に足してしまう"""
        for id_ in (config.TERRORS.get("alternate") or {}):
            self.assertGreater(int(id_), MatchTNL.ALTERNATE_LOG_MAX, id_)


class TestEnrageFogAlternate(unittest.TestCase):
    """Enrage の前倒しでもオルタネイト枠を伝える。

    `Killers is unknown` の行には `Fog (Alternate)` が出ない。焼き芋は
    オルタネイト枠のFogだけを続行リスト判定に回すので、枠を伝えないと
    「リストを見ずに自爆」になる。
    """

    WALPURGISNACHT = 167     # alternate
    HELL_BELL = 65           # classic。実ログでFogに74回出る
    FOG_ALT = GroupRound.FOG_ALTERNATE_ROUND_TYPE

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
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_YAKIIMO, keep_on=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = "Fog"
        monitor.st.fog = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _enrage(self, monitor, name):
        """グループのルールに渡った round_type を返す（引かれなければ None）"""
        seen = []
        real = monitor._group_decision
        with patch.object(monitor, "_group_decision",
                          side_effect=lambda rt, ids=None: (
                              seen.append(rt), real(rt, ids))[1]), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._on_enrage(name)
        return seen[0] if seen else None

    # ── 焼き芋（実害が出ていたところ） ─────────────
    def test_an_alternate_terror_reaches_the_group_rule_as_alternate(self):
        monitor = self._monitor()

        passed = self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(passed, self.FOG_ALT)

    def test_it_does_not_force_a_skip(self):
        """これが直したかった動き。枠を伝えないと問答無用の自爆になる"""
        monitor = self._monitor()
        self.assertEqual(monitor._group_decision("Fog"), GroupRound.SKIP,
                         "前提: 焼き芋は枠を伝えないとFogを自爆する")

        with patch.object(monitor, "_start_group_skip") as forced:
            self._enrage(monitor, "Walpurgisnacht")

        forced.assert_not_called()

    def test_a_classic_terror_still_forces_a_skip(self):
        """焼き芋の従来どおりの動き。巻き込んで弱めていないこと"""
        monitor = self._monitor()

        with patch.object(monitor, "_start_group_skip") as forced:
            self._enrage(monitor, "Hell Bell")

        forced.assert_called_once()

    def test_an_alternate_terror_in_the_list_continues(self):
        monitor = self._monitor(keep_on={"Fog/霧": {self.WALPURGISNACHT}})

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(monitor.st.is_continue_round)

    def test_a_classic_terror_is_unchanged(self):
        monitor = self._monitor()

        passed = self._enrage(monitor, "Hell Bell")

        self.assertEqual(passed, "Fog", "従来どおり")

    def test_the_hoshiimo_rule_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_HOSHIIMO)

        self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(monitor._group_decision("Fog"), GroupRound.CONTINUE)
        self.assertEqual(monitor._group_decision(self.FOG_ALT),
                         GroupRound.CONTINUE)

    # ── st.round_type は触らない ───────────────
    def test_the_round_type_is_not_rewritten(self):
        """書き換えると cfg.skip_rounds の「Fog」指定が黙って効かなくなる"""
        monitor = self._monitor()

        self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(monitor.st.round_type, "Fog")

    def test_the_round_skip_setting_still_works(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE)
        monitor.cfg.skip_rounds = {"Fog"}

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(monitor._should_skip_by_round(),
                        "Fog を自爆指定していたら、オルタでも自爆する")

    def test_it_is_logged(self):
        monitor = self._monitor()

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(any("オルタネイト確定" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_classic_terror_logs_nothing_about_alternate(self):
        monitor = self._monitor()

        self._enrage(monitor, "Hell Bell")

        self.assertFalse(any("オルタネイト確定" in m for m in monitor.logs),
                         monitor.logs)

    # ── IDを壊さない ──────────────────────────
    def test_the_offset_is_not_applied_twice(self):
        """167 が 301 にならないこと"""
        monitor = self._monitor()

        self._enrage(monitor, "Walpurgisnacht")

        self.assertEqual(monitor.st.terror_ids, [self.WALPURGISNACHT])
        self.assertEqual(monitor.st.enrage_identified, self.WALPURGISNACHT)

    def test_the_offset_helper_leaves_alternate_ids_alone(self):
        self.assertEqual(
            MatchTNL.apply_alternate_offset([self.WALPURGISNACHT], self.FOG_ALT),
            [self.WALPURGISNACHT])

    # ── private は変わらない ───────────────────
    def test_private_decisions_are_unchanged(self):
        """実ログに出た Fog の alternate テラーを、両方の枠で突き合わせる。
        LOG_TO_TNL がどちらも `Fog/霧` へ寄せているので結論は同じになる"""
        keep_on = {"Fog/霧": {137, 154, 163, 164, 168, 192, 315, 316}}
        for tid in (167, 157, 151, 159, 137, 161, 153, 142, 162, 156, 160):
            plain = RoundDecision.decide_killers(keep_on, [tid], "Fog", 0, True)
            alt = RoundDecision.decide_killers(keep_on, [tid], self.FOG_ALT,
                                               0, True)

            self.assertEqual(plain.is_continue_round, alt.is_continue_round, tid)

    def test_the_tnl_lookup_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE,
                                keep_on={"Fog/霧": {self.WALPURGISNACHT}})

        self._enrage(monitor, "Walpurgisnacht")

        self.assertTrue(monitor.st.is_continue_round,
                        "private は st.round_type で引くので従来どおり")

    # ── 対象を広げない ───────────────────────
    def test_other_rounds_still_do_nothing(self):
        monitor = self._monitor()
        monitor.st.round_type = "8 Pages"

        passed = self._enrage(monitor, "Walpurgisnacht")

        self.assertIsNone(passed)
        self.assertEqual(monitor.st.terror_ids, [])

    def test_the_switch_still_turns_it_off(self):
        monitor = self._monitor()

        with patch.object(config, "ENRAGE_IDENTIFY_ENABLED", False):
            passed = self._enrage(monitor, "Walpurgisnacht")

        self.assertIsNone(passed)
        self.assertEqual(monitor.logs, [])


class TestEnrageLine(unittest.TestCase):
    """Enrage 行の読み取り"""

    PREFIX = "2026.09.15 10:00:00 Debug      -  "

    def _parse(self, body):
        return LogParser.parse(self.PREFIX + body)

    def test_a_name_is_taken(self):
        event = self._parse("Teuthidatriggered an Enrage State!")

        self.assertEqual(event.kind, LogParser.EVENT_ENRAGE)
        self.assertEqual(event.player_name, "Teuthida")

    def test_the_later_stages_look_the_same(self):
        for stage in ("Enrage2", "Enrage3"):
            event = self._parse(f"Teuthidatriggered an {stage} State!")

            self.assertEqual(event.kind, LogParser.EVENT_ENRAGE, stage)
            self.assertEqual(event.player_name, "Teuthida", stage)

    def test_an_empty_name_is_not_an_event(self):
        """実データに46回ある"""
        self.assertIsNone(self._parse("triggered an Enrage State!"))

    def test_a_stun_line_is_an_event(self):
        """スタンされた名前でも霧のテラーを判明させる（実ログの形そのまま）"""
        for body, name in (("Teuthida was stunned.", "Teuthida"),
                           ("Unknown Witch was stunned.", "Unknown Witch"),
                           ("Mountain Of Smiling Bodies was stunned.",
                            "Mountain Of Smiling Bodies")):
            event = self._parse(body)
            self.assertEqual(event.kind, LogParser.EVENT_STUNNED, body)
            self.assertEqual(event.player_name, name, body)

    def test_a_stun_line_without_a_name_is_not_an_event(self):
        self.assertIsNone(self._parse("was stunned."))

    def test_a_name_with_spaces_survives(self):
        event = self._parse("GlaggleLand Disruptortriggered an Enrage State!")

        self.assertEqual(event.player_name, "GlaggleLand Disruptor")


class TestEnrageIdentify(unittest.TestCase):
    """Fog のテラー不明中に、Enrage から判定を前倒しする"""

    FOG_KEY = "Fog/霧"
    PURSUER = 99

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

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, keep_on=None,
                 round_type="Fog"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.st.fog = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _enrage(self, monitor, name="The Pursuer"):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_enrage(name)
        self.played = mock_play
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _revealed(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=True)
        self.played = mock_play
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 前倒しされる ────────────────────────
    def test_a_known_name_decides_early(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})

        self._enrage(monitor)

        self.assertEqual(monitor.st.terror_ids, [self.PURSUER])
        self.assertEqual(monitor.st.enrage_identified, self.PURSUER)
        self.assertTrue(monitor.st.is_continue_round)
        self.assertTrue(any("テラー判明(Enrage)" in m for m in monitor.logs),
                        monitor.logs)

    def test_it_works_through_the_log_line(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("2026.09.15 10:00:00 Debug      -  "
                             "The Pursuertriggered an Enrage2 State!")

        self.assertEqual(monitor.st.terror_ids, [self.PURSUER])

    def test_an_unknown_name_waits(self):
        """どちらの表にも無い名前。現物の別名表は随時書き換わるので、
        実在しない名前を使う（Furnace などは表に載りうる）"""
        monitor = self._monitor()

        started = self._enrage(monitor, "存在しないテラー名XYZ")

        self.assertEqual(monitor.st.terror_ids, [])
        self.assertIsNone(monitor.st.enrage_identified)
        self.assertEqual(started, [])
        self.assertTrue(any("テラー表に無し" in m for m in monitor.logs),
                        monitor.logs)

    def test_only_the_first_enrage_counts(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})
        self._enrage(monitor)

        started = self._enrage(monitor, "Teuthida")

        self.assertEqual(started, [])
        self.assertEqual(monitor.st.terror_ids, [self.PURSUER])

    # ── 二重実行しない ──────────────────────
    def test_the_real_reveal_does_not_skip_twice(self):
        monitor = self._monitor()          # 続行リストは空 → 自爆する
        first = self._enrage(monitor)
        self.assertIn("do_skip", first)

        second = self._revealed(monitor, [self.PURSUER])

        self.assertNotIn("do_skip", second, "自爆は1回だけ")

    def test_the_real_reveal_does_not_announce_twice(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})
        self._enrage(monitor)
        self.assertTrue(monitor.st.is_continue_round)

        self._revealed(monitor, [self.PURSUER])

        self.played.assert_not_called()
        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_a_mismatch_is_logged_but_not_redecided(self):
        monitor = self._monitor()
        self._enrage(monitor)

        started = self._revealed(monitor, [42])

        self.assertTrue(any("食い違いました" in m for m in monitor.logs),
                        monitor.logs)
        self.assertEqual(started, [], "やり直さないこと")

    def test_a_matching_reveal_logs_nothing(self):
        monitor = self._monitor()
        self._enrage(monitor)
        monitor.logs.clear()

        self._revealed(monitor, [self.PURSUER])

        self.assertFalse(any("食い違いました" in m for m in monitor.logs),
                         monitor.logs)

    # ── 対象を広げない ───────────────────────
    def test_other_rounds_do_nothing(self):
        for round_type in ("Classic", "8 Pages", "Bloodbath"):
            monitor = self._monitor(round_type=round_type)

            started = self._enrage(monitor)

            self.assertEqual(monitor.st.terror_ids, [], round_type)
            self.assertEqual(started, [], round_type)
            self.assertEqual(monitor.logs, [], round_type)

    def test_a_known_terror_is_left_alone(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [42]

        self._enrage(monitor)

        self.assertEqual(monitor.st.terror_ids, [42])
        self.assertIsNone(monitor.st.enrage_identified)

    def test_the_switch_turns_it_off(self):
        monitor = self._monitor(keep_on={self.FOG_KEY: {self.PURSUER}})

        with patch.object(config, "ENRAGE_IDENTIFY_ENABLED", False):
            started = self._enrage(monitor)

        self.assertEqual(monitor.st.terror_ids, [])
        self.assertEqual(started, [])
        self.assertEqual(monitor.logs, [])

    # ── 状態のクリア ────────────────────────
    def test_a_new_round_clears_it(self):
        monitor = self._monitor()
        self._enrage(monitor)
        self.assertIsNotNone(monitor.st.enrage_identified)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertIsNone(monitor.st.enrage_identified)

    def test_a_new_instance_clears_it(self):
        monitor = self._monitor()
        self._enrage(monitor)

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("2026.09.15 10:00:00 Debug      -  [Behaviour] "
                             "Joining wrld_1234:5678~private(usr_x)")

        self.assertIsNone(monitor.st.enrage_identified)

    # ── インスタンス種別 ──────────────────────
    def test_it_works_in_private_and_in_groups(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype, keep_on={self.FOG_KEY: {self.PURSUER}})

            self._enrage(monitor)

            self.assertEqual(monitor.st.terror_ids, [self.PURSUER], itype)
            self.assertEqual(monitor.st.enrage_identified, self.PURSUER, itype)


class TestEmeraldCityInstance(unittest.TestCase):
    """Emerald City は識別だけ。判定にも自爆にも入れない"""

    REAL_LINE = ("2026.09.15 14:32:17 Debug      -  [Behaviour] Joining "
                 "wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd:95351"
                 "~group(grp_8f8ace13-018b-47e6-a0f3-885831fd9bc8)"
                 "~groupAccessType(members)~region(jp)")

    def _parse(self, suffix):
        return LogMonitor.LogMonitor._parse_instance_type(suffix)

    def test_the_group_is_recognised(self):
        suffix = f"~group({config.EMERALD_CITY_GROUP_ID})~groupAccessType(members)"

        self.assertEqual(self._parse(suffix), config.INSTANCE_EMERALD_CITY)

    def test_the_real_log_line_is_recognised(self):
        event = LogParser.parse(self.REAL_LINE)

        self.assertEqual(event.kind, LogParser.EVENT_JOINING)
        self.assertEqual(self._parse(event.suffix), config.INSTANCE_EMERALD_CITY)

    def test_it_is_not_a_group_round_instance(self):
        """ここに入れると自爆が走る。依頼と逆になる"""
        self.assertNotIn(config.INSTANCE_EMERALD_CITY, GroupRound.GROUP_INSTANCES)

    def test_the_group_rules_do_not_apply(self):
        for round_type in ("Classic", "Bloodbath", "Fog", "Mystic Moon",
                           "Sabotage", "8 Pages"):
            self.assertEqual(
                GroupRound.decide(config.INSTANCE_EMERALD_CITY, round_type, [99]),
                GroupRound.NORMAL, round_type)

    def test_the_window_does_not_self_destruct(self):
        """other_group と同じく、判定も自爆も走らない"""
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_EMERALD_CITY
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._on_killers([99], "Classic", revealed=False)
        started = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]

        self.assertEqual(started, [])
        self.assertFalse(monitor.st.is_continue_round)

    def test_an_unknown_group_is_still_other_group(self):
        suffix = "~group(grp_0000ffff-0000-0000-0000-000000000000)"

        self.assertEqual(self._parse(suffix), config.INSTANCE_OTHER_GROUP)

    def test_the_imo_groups_are_unchanged(self):
        self.assertEqual(self._parse(f"~group({config.HOSHIIMO_GROUP_ID})"),
                         config.INSTANCE_HOSHIIMO)
        self.assertEqual(self._parse(f"~group({config.YAKIIMO_GROUP_ID})"),
                         config.INSTANCE_YAKIIMO)

    def test_private_and_public_are_unchanged(self):
        for marker in ("~private", "~friends", "~hidden", "~canRequestInvite"):
            self.assertEqual(self._parse(marker), config.INSTANCE_PRIVATE, marker)
        self.assertEqual(self._parse(""), config.INSTANCE_PUBLIC)
        self.assertEqual(self._parse("~region(jp)"), config.INSTANCE_PUBLIC)

    def test_the_group_id_is_not_empty(self):
        """空だと group() がどのグループにも一致してしまう（CBPS_GROUP_ID の轍）"""
        self.assertTrue(config.EMERALD_CITY_GROUP_ID.strip())
        self.assertTrue(config.EMERALD_CITY_GROUP_ID.startswith("grp_"))


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


class TestSelfInsertsBloodthirsty(unittest.TestCase):
    """Unbound の Self Inserts に Bloodthirsty が出たら最優先で続行する。

    ToN ListTool に「Bloodthirsty 入りの Self Inserts」を指定する手段が無いので、
    リストにも自爆指定にも頼れない。
    """

    UNBOUND_KEY = "Unbound/アンバウンド"
    SELF_INSERTS = config.SELF_INSERTS_ID          # 283
    PACK = 265                                     # Pack of Wild Yet Curious

    def _decide(self, round_type, terror_ids, keep_on=None,
                bloodthirsty=False, wins=99, cancel_afk=False):
        return RoundDecision.decide_killers(
            keep_on or {}, list(terror_ids), round_type, wins, cancel_afk,
            bloodthirsty_variant=bloodthirsty)

    # ── 強制続行 ────────────────────────────
    def test_self_inserts_with_bloodthirsty_continues(self):
        decided = self._decide("Unbound", [self.SELF_INSERTS], bloodthirsty=True)

        self.assertTrue(decided.is_continue_round)

    def test_without_bloodthirsty_it_follows_the_list(self):
        self.assertFalse(
            self._decide("Unbound", [self.SELF_INSERTS]).is_continue_round)
        self.assertTrue(
            self._decide("Unbound", [self.SELF_INSERTS],
                         {self.UNBOUND_KEY: {self.SELF_INSERTS}}).is_continue_round)

    def test_the_listed_case_is_unchanged(self):
        """リストに283があれば Bloodthirsty でなくても続行（従来どおり）"""
        decided = self._decide("Unbound", [self.SELF_INSERTS],
                               {self.UNBOUND_KEY: {self.SELF_INSERTS}})

        self.assertTrue(decided.is_continue_round)

    # ── 対象を広げない ───────────────────────
    def test_the_pack_is_not_covered(self):
        """Pack of Wild Yet Curious(265) はリストで指定できるので対象外"""
        self.assertFalse(
            self._decide("Unbound", [self.PACK], bloodthirsty=True).is_continue_round)
        self.assertTrue(
            self._decide("Unbound", [self.PACK], {self.UNBOUND_KEY: {self.PACK}},
                         bloodthirsty=True).is_continue_round)

    def test_other_rounds_are_not_covered(self):
        for round_type, ids in (("Classic", [config.BLOODTHIRSTY_CREATURE_ID]),
                                ("Fog", [config.BLOODTHIRSTY_CREATURE_ID]),
                                ("Midnight", [self.SELF_INSERTS])):
            self.assertFalse(
                self._decide(round_type, ids, bloodthirsty=True).is_continue_round,
                round_type)

    def test_another_unbound_group_is_not_covered(self):
        self.assertFalse(
            self._decide("Unbound", [200], bloodthirsty=True).is_continue_round)

    def test_omitting_the_flag_keeps_the_old_behaviour(self):
        decided = RoundDecision.decide_killers(
            {}, [self.SELF_INSERTS], "Unbound", 99, False)

        self.assertFalse(decided.is_continue_round)

    # ── 3クラ解放と混ざらない ──────────────────
    def test_it_does_not_set_the_open_special_flag(self):
        """混ぜると3クラ解放の AFK 解除が誤って走る"""
        decided = self._decide("Unbound", [self.SELF_INSERTS], bloodthirsty=True)

        self.assertTrue(decided.is_continue_round)
        self.assertFalse(decided.is_open_special_round_target)

    def test_the_helper_is_precise(self):
        ok = RoundDecision.is_self_inserts_bloodthirsty
        self.assertTrue(ok([self.SELF_INSERTS], "Unbound", True))
        self.assertFalse(ok([self.SELF_INSERTS], "Unbound", False))
        self.assertFalse(ok([self.PACK], "Unbound", True))
        self.assertFalse(ok([self.SELF_INSERTS], "Classic", True))
        self.assertFalse(ok([], "Unbound", True))


class TestSelfInsertsBloodthirstyWiring(unittest.TestCase):
    """LogMonitor 側。自爆指定より優先されること"""

    SELF_INSERTS = config.SELF_INSERTS_ID

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
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, skip_rounds=(),
                 bloodthirsty=True):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Unbound"
        monitor.st.terror_ids = [self.SELF_INSERTS]
        monitor.st.bloodthirsty_creature_variant = bloodthirsty
        return monitor

    def test_the_skip_list_does_not_win(self):
        """private で Unbound を自爆指定していても続行する"""
        monitor = self._monitor(skip_rounds=("Unbound",))

        self.assertFalse(monitor._should_skip_by_round())

    def test_the_skip_list_still_wins_without_bloodthirsty(self):
        monitor = self._monitor(skip_rounds=("Unbound",), bloodthirsty=False)

        self.assertTrue(monitor._should_skip_by_round())

    def test_it_continues_in_every_instance_type(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype)

            with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
                 patch.object(PlaySound, "play_sound"):
                monitor._on_killers([self.SELF_INSERTS], "Unbound", revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]

            self.assertTrue(monitor.st.is_continue_round, itype)
            self.assertNotIn("do_skip", started, itype)

    def test_the_flag_reaches_decide_killers(self):
        monitor = self._monitor()

        with patch.object(RoundDecision, "decide_killers",
                          return_value=RoundDecision.KillerDecision(False, True)
                          ) as mock_decide, \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._decide_with_keep_on_set("Unbound")

        self.assertIs(mock_decide.call_args.kwargs["bloodthirsty_variant"], True)

    def test_the_106_row_does_not_cover_unbound(self):
        """Unbound の terror_ids は [283]。待つのは 106 の行ではなく Self Inserts の行"""
        monitor = self._monitor(bloodthirsty=False)

        names = [r.name for r in monitor._pending_replacements()]
        self.assertNotIn("Wild Yet Bloodthirsty Creature", names)
        self.assertIn("Self Inserts (Bloodthirsty)", names)

        monitor.st.terror_ids = [config.CURIOUS_CREATURE_ID]
        self.assertIn("Wild Yet Bloodthirsty Creature",
                      [r.name for r in monitor._pending_replacements()],
                      "106 がいるラウンドでは従来どおり待つこと")


class TestTerrorReplacementTable(unittest.TestCase):
    """置き換えテラーの表。待ち方と差し替えはすべてここを引く"""

    def _row(self, name):
        rows = [r for r in TerrorReplacement.TABLE if r.name == name]
        self.assertEqual(len(rows), 1, name)
        return rows[0]

    def test_the_rows_from_the_requester(self):
        classic = frozenset({"Classic"})
        cases = {
            "Atrached": (config.SONIC_ID, config.ATRACHED_ID, classic),
            "Hungry Home Invader": (config.SLENDER_ID,
                                    config.HUNGRY_HOME_INVADER_ID, classic),
            "Wild Yet Bloodthirsty Creature": (config.CURIOUS_CREATURE_ID,
                                               config.BLOODTHIRSTY_CREATURE_ID,
                                               None),
            "The Gigabytes": (None, config.GIGABYTES_ID, classic),
            "Foxy": (config.SANIC_ID, config.FOXY_ID, None),
        }
        for name, (source, target, rounds) in cases.items():
            row = self._row(name)
            self.assertEqual((row.source, row.target_id(), row.rounds),
                             (source, target, rounds), name)
            self.assertTrue(row.enabled, name)

    def test_the_ids_match_terrors_json(self):
        """IDを書き間違えると、別のテラーで待つ・差し替える"""
        names = {
            config.SONIC_ID: "Sonic", config.ATRACHED_ID: "Atrached",
            config.SLENDER_ID: "Slenderwheels",
            config.HUNGRY_HOME_INVADER_ID: "Hungry Home Invader",
            config.CURIOUS_CREATURE_ID: "Wild Yet Curious Creature",
            config.BLOODTHIRSTY_CREATURE_ID: "Wild Yet Bloodthirsty Creature",
            config.GIGABYTES_ID: "The Gigabytes", config.SANIC_ID: "Sanic",
            config.FOXY_ID: "Foxy", config.FUSION_PILOT_ID: "Fusion Pilot",
            config.SELF_INSERTS_ID: "Self Inserts",
        }
        for tid, name in names.items():
            self.assertEqual(ReadJson.terror_name(tid, config.TERRORS), name, tid)

    def test_the_foxy_row_is_the_alternate_sanic(self):
        """いまのコードは霧の Foxy を 2+134=136 として判定していた（Sanic）"""
        self.assertTrue(ReadJson.is_alternate_terror(config.SANIC_ID, config.TERRORS))
        self.assertTrue(ReadJson.is_alternate_terror(config.FOXY_ID, config.TERRORS))

    def test_self_inserts_only_raises_a_flag(self):
        row = self._row("Self Inserts (Bloodthirsty)")

        self.assertFalse(row.changes_id)
        self.assertEqual(row.apply([config.SELF_INSERTS_ID]),
                         [config.SELF_INSERTS_ID])
        self.assertEqual(row.flag, "bloodthirsty_creature_variant")

    def test_gigabytes_replaces_the_whole_line_up(self):
        row = self._row("The Gigabytes")

        self.assertEqual(row.apply([99]), [config.GIGABYTES_ID])
        self.assertTrue(row.could_apply([99], "Classic"))
        self.assertFalse(row.could_apply([1, 2], "Classic"), "1体構成だけ")
        self.assertFalse(row.could_apply([99], "Bloodbath"))

    def test_every_flag_exists_on_the_state(self):
        st = WindowState()
        for flag in TerrorReplacement.flags():
            self.assertIs(getattr(st, flag), False, flag)

    def test_every_target_counts_as_a_variant_except_foxy(self):
        """Variant例外（自爆指定より優先）の対象は従来の4つのまま"""
        self.assertEqual(config.VARIANT_TERROR_IDS, frozenset({
            config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
            config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID}))


class TestNeoPilotSlot(unittest.TestCase):
    """Neo Pilot は枠だけ。IDと合図が分かるまで無効"""

    def _row(self):
        return [r for r in TerrorReplacement.TABLE if r.name == "Neo Pilot"][0]

    def _with_neo_pilot(self):
        data = {k: dict(v) if isinstance(v, dict) else v
                for k, v in config.TERRORS.items()}
        data["alternate"]["999"] = "Neo Pilot"
        return patch.object(config, "TERRORS", data)

    def test_it_is_disabled_now(self):
        row = self._row()

        self.assertEqual(row.source, config.FUSION_PILOT_ID)
        self.assertIsNone(row.target_id(), "terrors.json にまだ無い")
        self.assertFalse(row.enabled)

    def test_it_never_waits_while_disabled(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.round_type = "Alternate"
        monitor.st.terror_ids = [config.FUSION_PILOT_ID]

        self.assertEqual(monitor._pending_replacements(), [])

    def test_the_id_alone_does_not_enable_it(self):
        """合図のログが分からないまま待つと、毎回0.3秒むだに待つだけ"""
        with self._with_neo_pilot():
            row = self._row()
            self.assertEqual(row.target_id(), 999)
            self.assertFalse(row.enabled)

    def test_the_id_and_the_signal_enable_it(self):
        import dataclasses
        with self._with_neo_pilot():
            row = dataclasses.replace(self._row(), wired=True)

            self.assertTrue(row.enabled)
            self.assertEqual(row.apply([config.FUSION_PILOT_ID]), [999])

    def test_the_signal_alone_does_not_enable_it(self):
        import dataclasses
        row = dataclasses.replace(self._row(), wired=True)

        self.assertFalse(row.enabled, "IDが無い間は表から外れる")


class TestReplacementWait(unittest.TestCase):
    """判定のための待ちは「置き換えが起きると結論が変わるときだけ」"""

    DT_KEY = "Double Trouble/ダブルトラブル"
    CRACKED_KEY = "Cracked/狂気"
    CURIOUS = config.CURIOUS_CREATURE_ID
    BLOODTHIRSTY = config.BLOODTHIRSTY_CREATURE_ID

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self.sent = self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source(None)

    def _monitor(self, round_type, keep_on=None, *, skip_rounds=(),
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor._running = True
        return monitor

    def _killers(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _delayed(self, monitor, wait_sec=0.0):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_decision(monitor.st.round_type, wait_sec,
                                      monitor.st.round_seq)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 待つ・待たない ───────────────────────
    def test_it_waits_when_the_replacement_is_wanted(self):
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.BLOODTHIRSTY}})

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]),
                         ["_delayed_decision"])

    def test_it_does_not_wait_when_neither_is_wanted(self):
        monitor = self._monitor("Double Trouble")

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]), ["do_skip"])

    def test_it_waits_when_only_the_original_is_wanted(self):
        """続行→自爆に変わりうる。依頼者の言葉の裏返しも拾う"""
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.CURIOUS}})

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]),
                         ["_delayed_decision"])

    def test_it_does_not_wait_when_both_are_wanted(self):
        monitor = self._monitor("Double Trouble",
                                {self.DT_KEY: {self.CURIOUS, self.BLOODTHIRSTY}})

        started = self._killers(monitor, [self.CURIOUS, 5])

        self.assertNotIn("_delayed_decision", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_private_waits_without_a_designation(self):
        """依頼者の報告: Cracked の自爆指定は無かったのに、待たずに自爆した"""
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})

        started = self._killers(monitor, [self.CURIOUS])

        self.assertEqual(started, ["_delayed_decision"])
        self.assertNotIn("do_skip", started)

    def test_it_works_in_all_three_instance_types(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            monitor = self._monitor("Double Trouble",
                                    {self.DT_KEY: {self.BLOODTHIRSTY}},
                                    instance_type=itype)

            self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]),
                             ["_delayed_decision"], itype)

    def test_hands_free_skips_at_once(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        monitor.st.item_id = 0

        started = self._killers(monitor, [self.CURIOUS])

        self.assertEqual(started, ["do_skip"], "放置モードは待たない")

    def test_a_public_window_never_waits(self):
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.BLOODTHIRSTY}},
                                instance_type=config.INSTANCE_PUBLIC)

        self.assertEqual(self._killers(monitor, [self.CURIOUS, 5]), [])

    # ── 待ち明け ────────────────────────────
    def test_the_signal_during_the_wait_continues(self):
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        self._killers(monitor, [self.CURIOUS])
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("The creature is bloodthirsty today...")

        started = self._delayed(monitor)

        self.assertEqual(monitor.st.terror_ids, [self.BLOODTHIRSTY])
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_no_signal_ends_in_the_original_judgement(self):
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        self._killers(monitor, [self.CURIOUS])

        self.assertIn("do_skip", self._delayed(monitor))

    def test_the_signal_ends_the_wait_early(self):
        """残り時間を待たずに判定する"""
        monitor = self._monitor("Cracked", {self.CRACKED_KEY: {self.BLOODTHIRSTY}})
        self._killers(monitor, [self.CURIOUS])
        naps = []

        def nap(_sec):
            naps.append(_sec)
            monitor._process("The creature is bloodthirsty today...")

        with patch.object(LogMonitor.time, "sleep", side_effect=nap):
            started = self._delayed(monitor, wait_sec=60.0)

        self.assertEqual(len(naps), 1, "合図の直後に抜けること")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    # ── 統計の待ち ──────────────────────────
    def test_statistics_wait_even_when_the_decision_does_not(self):
        monitor = self._monitor("Double Trouble")

        started = self._killers(monitor, [self.CURIOUS, 5])

        self.assertEqual(started, ["do_skip"], "判定は待たない")
        self.sent.assert_not_called()

        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process("The creature is bloodthirsty today...")

        self.sent.assert_called_once()
        self.assertEqual(self.sent.call_args.args[1], [self.BLOODTHIRSTY, 5])

    def test_statistics_do_not_wait_for_self_inserts(self):
        """IDが変わらないので待つ意味が無い"""
        monitor = self._monitor("Unbound")

        self._killers(monitor, [config.SELF_INSERTS_ID])

        self.sent.assert_called_once()

    def test_statistics_do_not_wait_without_a_candidate(self):
        monitor = self._monitor("Double Trouble")

        self._killers(monitor, [1, 2])

        self.sent.assert_called_once()

    # ── 判定の比較は本番と同じ関数 ──────────────────
    def test_the_comparison_uses_the_real_decision(self):
        """待つかの判断が本番の判定とずれないこと"""
        monitor = self._monitor("Double Trouble", {self.DT_KEY: {self.BLOODTHIRSTY}})
        monitor.st.terror_ids = [self.CURIOUS, 5]
        calls = []
        real = monitor._plan

        def spy(*args):
            calls.append(args)
            return real(*args)

        with patch.object(monitor, "_plan", side_effect=spy):
            self.assertTrue(monitor._replacement_changes_decision("Double Trouble"))
            with patch.object(LogMonitor.threading, "Thread"), \
                 patch.object(PlaySound, "play_sound"):
                monitor._decide("Double Trouble")

        self.assertIn(("Double Trouble", [self.CURIOUS, 5], False), calls)
        self.assertIn(("Double Trouble", [self.BLOODTHIRSTY, 5], True), calls)

    def test_every_plan_kind_has_an_outcome(self):
        for plan in (("group", GroupRound.SKIP), ("group", GroupRound.CONTINUE),
                     ("group", GroupRound.WANTED), ("restricted",),
                     ("hands_free", "x"), ("continue_rounds",), ("round_skip",),
                     ("list", True, False), ("list", True, True),
                     ("list", False, False)):
            self.assertIn(LogMonitor.LogMonitor._outcome(plan),
                          {"skip", "quiet", "continue", "open_special"}, plan)


class TestFogFoxy(unittest.TestCase):
    """霧で Foxy が出たら Foxy(316) として判定する"""

    FOG_KEY = "Fog/霧"
    FOXY_LINE = "2026.09.18 10:00:00 Debug      -  foxy the pirate turned evil!"

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
        SharedState.set_list_source(None)

    def _monitor(self, keep_on=None, *, instance_type=config.INSTANCE_PRIVATE,
                 skip_rounds=()):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = "Fog"
        monitor.st.fog = True
        monitor._running = True
        return monitor

    def _lines(self, monitor, *lines):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            for line in lines:
                monitor._process(line)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_it_is_judged_as_foxy(self):
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}})

        started = self._lines(monitor, self.FOXY_LINE)

        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_sanic_in_the_list_does_not_count(self):
        """以前は Sanic(136) として判定していた"""
        monitor = self._monitor({self.FOG_KEY: {config.SANIC_ID}})

        started = self._lines(monitor, self.FOXY_LINE)

        self.assertIn("do_skip", started)

    def test_the_round_type_stays_fog(self):
        monitor = self._monitor()

        self._lines(monitor, self.FOXY_LINE)

        self.assertEqual(monitor.st.round_type, "Fog")

    def test_the_fog_skip_designation_still_applies(self):
        """st.round_type を書き換えていた頃は、ここで自爆指定が外れていた"""
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}},
                                skip_rounds=("Fog",))

        self.assertTrue(monitor._should_skip_by_round([config.FOXY_ID], False))

    def test_yakiimo_judges_it_by_the_list(self):
        """焼き芋はオルタ枠の Fog だけをリスト判定に回す"""
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}},
                                instance_type=config.INSTANCE_YAKIIMO)

        started = self._lines(monitor, self.FOXY_LINE)

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_a_later_reveal_does_not_decide_again(self):
        """二重に自爆しないこと"""
        monitor = self._monitor()

        first = self._lines(monitor, self.FOXY_LINE)
        second = self._lines(
            monitor,
            "Killers have been revealed - 2 0 0 // Round type is Fog (Alternate)")

        self.assertEqual(first.count("do_skip"), 1)
        self.assertNotIn("do_skip", second)
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])

    def test_a_revealed_sanic_turns_into_foxy(self):
        """Foxy の行が revealed より後に来た場合"""
        monitor = self._monitor()
        self._lines(monitor,
                    "Killers have been revealed - 2 0 0 // Round type is Fog (Alternate)")
        self.assertEqual(monitor.st.terror_ids, [config.SANIC_ID])

        self._lines(monitor, self.FOXY_LINE)

        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])

    def test_a_revealed_sanic_waits_for_foxy_when_it_matters(self):
        monitor = self._monitor({self.FOG_KEY: {config.FOXY_ID}})

        started = self._lines(
            monitor,
            "Killers have been revealed - 2 0 0 // Round type is Fog (Alternate)")

        self.assertEqual(started, ["_delayed_decision"])


class TestSelfInsertsBloodthirstyWait(unittest.TestCase):
    """Self Inserts は Bloodthirsty 行を待ってから判定する。

    実測: Variant の出現ログは Killers 行の**後**に出る（手元ログの
    Gigabytes 12件・Atrached 3件がすべて後）。待たないと、Self Inserts の
    強制続行がまさにその場面で発火しない。
    """

    SELF_INSERTS = config.SELF_INSERTS_ID
    UNBOUND_KEY = "Unbound/アンバウンド"

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
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE, skip_rounds=(),
                 keep_on=None, terror_ids=(config.SELF_INSERTS_ID,)):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Unbound"
        monitor.st.terror_ids = list(terror_ids)
        monitor._running = True
        return monitor

    def _started(self, monitor):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(monitor.st.terror_ids), "Unbound",
                                revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    @staticmethod
    def _pending(monitor):
        return any(r.name == "Self Inserts (Bloodthirsty)"
                   for r in monitor._pending_replacements())

    def _run_wait(self, monitor):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._delayed_decision("Unbound", 0.0, monitor.st.round_seq)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 待つかどうか ─────────────────────────
    def test_it_waits_for_self_inserts(self):
        monitor = self._monitor()

        self.assertTrue(self._pending(monitor))

    def test_it_stops_waiting_once_the_line_arrives(self):
        monitor = self._monitor()
        monitor.st.bloodthirsty_creature_variant = True

        self.assertFalse(self._pending(monitor))

    def test_it_does_not_wait_for_other_groups(self):
        for ids in ([265], [200], [config.CURIOUS_CREATURE_ID]):
            monitor = self._monitor(terror_ids=ids)

            self.assertFalse(self._pending(monitor), ids)

    def test_it_does_not_wait_in_other_rounds(self):
        for round_type in ("Classic", "Fog", "Midnight"):
            monitor = self._monitor()
            monitor.st.round_type = round_type

            self.assertFalse(self._pending(monitor),
                             round_type)

    # ── 待ちに入ること ───────────────────────
    def test_the_round_enters_the_wait(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            monitor = self._monitor(itype)

            self.assertEqual(self._started(monitor),
                             ["_delayed_decision"], itype)

    def test_a_round_that_already_saw_the_line_does_not_wait(self):
        monitor = self._monitor(keep_on={self.UNBOUND_KEY: {self.SELF_INSERTS}})
        monitor.st.bloodthirsty_creature_variant = True

        started = self._started(monitor)

        self.assertNotIn("_delayed_decision", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_other_rounds_gain_no_latency(self):
        monitor = self._monitor(terror_ids=[265])

        started = self._started(monitor)

        self.assertNotIn("_delayed_decision", started)

    # ── 待ち明けの判断 ───────────────────────
    def test_the_line_arriving_during_the_wait_continues(self):
        monitor = self._monitor()
        monitor.st.bloodthirsty_creature_variant = True   # 待っている間に来た

        started = self._run_wait(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_without_the_line_it_follows_the_list(self):
        monitor = self._monitor()

        started = self._run_wait(monitor)

        self.assertFalse(monitor.st.is_continue_round)
        self.assertIn("do_skip", started, "private なのでリストに無ければ自爆")

    def test_the_skip_list_is_still_honoured_after_the_wait(self):
        """Bloodthirsty が来なければ、自爆指定はそのまま効く"""
        monitor = self._monitor(skip_rounds=("Unbound",))

        started = self._run_wait(monitor)

        self.assertIn("do_skip", started)

    def test_the_line_beats_the_skip_list_after_the_wait(self):
        monitor = self._monitor(skip_rounds=("Unbound",))
        monitor.st.bloodthirsty_creature_variant = True

        started = self._run_wait(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_the_skip_list_does_not_decide_before_the_wait(self):
        """待たずに自爆指定を見ると、フラグが立つ前に自爆が決まってしまう"""
        monitor = self._monitor(skip_rounds=("Unbound",))

        started = self._started(monitor)

        self.assertEqual(started, ["_delayed_decision"])

    def test_the_wait_aborts_when_the_round_changed(self):
        monitor = self._monitor()
        monitor.st.round_seq = 5

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision("Unbound", 0.0, 4)

        mock_thread.assert_not_called()

    def test_the_wait_uses_the_shared_length(self):
        monitor = self._monitor()

        self.assertEqual(monitor._variant_wait_sec(),
                         config.TERROR_VARIANT_WAIT_SEC)


class TestSpecialMoonKey(unittest.TestCase):
    """ToN ListTool は Variant と Moon を Special/Moon の13枠にまとめて記録する。

    ラウンド別のキーは空のままなので、そちらだけ見ていると永久に空振りする。
    ただし置き換えてはいけない——ID192 はラウンド別のキーにも入っている。
    """

    SPECIAL = MatchTNL.SPECIAL_MOON_KEY
    CLASSIC = "Classic/クラシック"
    FOG = "Fog/霧"

    def _continue(self, round_type, terror_ids, keep_on, wins=99, cancel_afk=False):
        return RoundDecision.decide_killers(
            keep_on, list(terror_ids), round_type, wins, cancel_afk
        ).is_continue_round

    # ── Variant ─────────────────────────────
    def test_each_variant_is_found_in_the_special_slot(self):
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
                    config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID):
            self.assertTrue(
                self._continue("Classic", [tid], {self.SPECIAL: {tid}}), tid)

    def test_a_variant_missing_from_the_slot_does_not_continue(self):
        self.assertFalse(
            self._continue("Classic", [config.ATRACHED_ID],
                           {self.SPECIAL: {config.GIGABYTES_ID}}))

    def test_an_unmutated_terror_is_not_covered(self):
        """Sonic のまま（Variant確定前）なら Special/Moon に居ない"""
        self.assertFalse(
            self._continue("Classic", [config.SONIC_ID],
                           {self.SPECIAL: {config.ATRACHED_ID}}))

    def test_an_ordinary_terror_passes_straight_through(self):
        self.assertFalse(self._continue("Classic", [42], {self.SPECIAL: {191}}))

    # ── ラウンド別キーを取りこぼさない ────────────
    def test_classic_sees_bloodthirsty_in_the_special_slot(self):
        self.assertTrue(
            self._continue("Classic", [config.BLOODTHIRSTY_CREATURE_ID],
                           {self.SPECIAL: {config.BLOODTHIRSTY_CREATURE_ID}}))

    def test_a_round_key_still_wins_on_its_own(self):
        """ID192 は Fog などラウンド別のキーにも入っている。寄せると落ちる"""
        self.assertTrue(
            self._continue("Fog", [config.BLOODTHIRSTY_CREATURE_ID],
                           {self.FOG: {config.BLOODTHIRSTY_CREATURE_ID}}))

    def test_other_rounds_do_not_look_at_the_special_slot(self):
        """Special/Moon にだけ192を入れている人が、Fog などで誤続行しないこと"""
        for round_type in ("Fog", "Ghost", "Midnight", "Punished"):
            self.assertFalse(
                self._continue(round_type, [config.BLOODTHIRSTY_CREATURE_ID],
                               {self.SPECIAL: {config.BLOODTHIRSTY_CREATURE_ID}}),
                round_type)

    def test_being_in_both_is_fine(self):
        keep_on = {"Classic/クラシック": {config.BLOODTHIRSTY_CREATURE_ID},
                   self.SPECIAL: {config.BLOODTHIRSTY_CREATURE_ID}}

        self.assertTrue(
            self._continue("Classic", [config.BLOODTHIRSTY_CREATURE_ID], keep_on))

    def test_the_round_list_is_classic_and_the_four_moons(self):
        self.assertEqual(
            RoundDecision.SPECIAL_MOON_ROUNDS,
            {"Classic", "Mystic Moon", "Blood Moon", "Twilight", "Solstice"})

    def test_a_classic_round_key_still_works(self):
        self.assertTrue(self._continue("Classic", [42],
                                       {"Classic/クラシック": {42}}))

    def test_an_unrelated_round_key_is_untouched(self):
        self.assertTrue(self._continue("Fog", [7], {self.FOG: {7}}))
        self.assertFalse(self._continue("Fog", [7], {self.FOG: {8}}))

    # ── Moon ────────────────────────────────
    def test_moons_are_found_in_the_special_slot(self):
        for round_type, tid in (("Mystic Moon", 313), ("Blood Moon", 315),
                                ("Twilight", 316), ("Solstice", 317)):
            self.assertTrue(
                self._continue(round_type, [tid], {self.SPECIAL: {tid}}),
                round_type)

    def test_a_special_round_type_still_works(self):
        for round_type in ("Special", "Moon"):
            self.assertTrue(
                self._continue(round_type, [313], {self.SPECIAL: {313}}),
                round_type)
            self.assertFalse(
                self._continue(round_type, [313], {self.SPECIAL: {315}}),
                round_type)

    # ── 壊れない ────────────────────────────
    def test_a_missing_special_key_is_fine(self):
        self.assertFalse(self._continue("Classic", [191], {self.CLASSIC: {1}}))

    def test_an_empty_keep_list_is_fine(self):
        self.assertFalse(self._continue("Classic", [191], {}))

    def test_an_empty_terror_list_is_fine(self):
        self.assertFalse(self._continue("Classic", [], {self.SPECIAL: {191}}))

    def test_the_open_special_round_path_is_unchanged(self):
        dtm = LogMonitor.DTM_TERROR_ID
        decision = RoundDecision.decide_killers({}, [dtm], "Classic", 0, True)

        self.assertTrue(decision.is_open_special_round_target)
        self.assertTrue(decision.is_continue_round)

    def test_the_real_list_now_covers_atrached(self):
        """現物で再現していた不具合。Classic/クラシックは空、191はSpecial/Moon"""
        if not Path(config.HOST_SAVE_PATH).exists():
            self.skipTest("host_save.json.gz が無い")
        keep_on, _meta, _wishes = MatchTNL.load_host_save(
            config.HOST_SAVE_PATH, config.USER_SAVE_PATH)
        if config.ATRACHED_ID not in keep_on.get(self.SPECIAL, set()):
            self.skipTest("現物の Special/Moon に191が無い")

        self.assertNotIn(config.ATRACHED_ID, keep_on.get(self.CLASSIC, set()),
                         "ラウンド別キーには入っていない")
        self.assertTrue(
            self._continue("Classic", [config.ATRACHED_ID], keep_on))


class TestSpecialMoonKeyWiring(unittest.TestCase):
    """インスタンス種別によらず同じに効くこと"""

    SPECIAL = MatchTNL.SPECIAL_MOON_KEY

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
        SharedState.set_list_source(None)

    def _monitor(self, instance_type):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(
            cfg, {self.SPECIAL: {config.ATRACHED_ID}}, lambda _m: None,
            window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.atrached_variant = True   # Variant は確定済み
        return monitor

    def test_a_classic_variant_continues_everywhere(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype)

            # 待ちを止めるのに st.gigabytes を立ててはいけない。_on_killers が
            # ids を [314] に差し替えるので、判定したいIDごと変わってしまう
            with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
                 patch.object(LogMonitor.LogMonitor,
                              "_replacement_changes_decision",
                              return_value=False), \
                 patch.object(PlaySound, "play_sound"):
                monitor._on_killers([config.ATRACHED_ID], "Classic", revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]

            self.assertTrue(monitor.st.is_continue_round, itype)
            self.assertNotIn("do_skip", started, itype)


class TestGroupMoonSkip(unittest.TestCase):
    """自爆リストの moon は干し芋/焼き芋でも効く（1回目でも自爆）"""

    HOSHIIMO = config.INSTANCE_HOSHIIMO
    YAKIIMO = config.INSTANCE_YAKIIMO

    def _decide(self, instance_type, moon, skip_moons=(), moon_repeat=False):
        return GroupRound.decide(instance_type, moon, [7],
                                 skip_moons=skip_moons, moon_repeat=moon_repeat)

    # ── 指定すれば自爆 ────────────────────────
    def test_hoshiimo_skips_every_checked_moon(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon, {moon}),
                             GroupRound.SKIP, moon)

    def test_a_checked_moon_skips_on_its_first_appearance(self):
        """消化済み扱いにはしない。指定されているから自爆する"""
        self.assertEqual(
            self._decide(self.HOSHIIMO, "Mystic Moon", {"Mystic Moon"},
                         moon_repeat=False),
            GroupRound.SKIP)

    def test_yakiimo_skips_a_checked_blood_moon(self):
        """焼き芋で実際に挙動が変わるのは Blood Moon / Twilight"""
        for moon in ("Blood Moon", "Twilight"):
            self.assertEqual(self._decide(self.YAKIIMO, moon, {moon}),
                             GroupRound.SKIP, moon)

    def test_only_the_named_moon_is_affected(self):
        decided = self._decide(self.HOSHIIMO, "Twilight", {"Blood Moon"})

        self.assertEqual(decided, GroupRound.CONTINUE)

    # ── 指定しなければ従来どおり ─────────────────
    def test_an_unchecked_first_moon_still_plays(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon),
                             GroupRound.CONTINUE, moon)

    def test_an_unchecked_repeat_still_skips(self):
        for moon in RoundSequence.MOONS:
            self.assertEqual(self._decide(self.HOSHIIMO, moon, moon_repeat=True),
                             GroupRound.SKIP, moon)

    def test_yakiimo_first_moons_are_unchanged(self):
        for moon in ("Mystic Moon", "Solstice"):
            self.assertEqual(self._decide(self.YAKIIMO, moon),
                             GroupRound.SKIP, moon)

    def test_omitting_the_argument_keeps_the_old_behaviour(self):
        self.assertEqual(
            GroupRound.decide(self.HOSHIIMO, "Blood Moon", [7]),
            GroupRound.CONTINUE)

    def test_a_non_moon_in_the_set_changes_nothing(self):
        decided = self._decide(self.HOSHIIMO, "Blood Moon",
                               {"Classic", "Bloodbath"})

        self.assertEqual(decided, GroupRound.CONTINUE)

    def test_the_earlier_branches_are_untouched(self):
        """moon の節より前の分岐に影響していないこと"""
        self.assertEqual(
            GroupRound.decide(self.HOSHIIMO, "Bloodbath", [1],
                              skip_moons={"Bloodbath", "Mystic Moon"}),
            GroupRound.SKIP)
        self.assertEqual(
            GroupRound.decide(self.HOSHIIMO, "8 Pages", [1],
                              skip_moons={"8 Pages", "Mystic Moon"}),
            GroupRound.CONTINUE)


class TestGroupMoonSkipWiring(unittest.TestCase):
    """LogMonitor から渡すのは moon だけ"""

    def _monitor(self, skip_rounds=()):
        cfg = WindowConfig(skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        monitor.st.round_type = "Twilight"
        monitor.st.terror_ids = [7]
        return monitor

    def _passed(self, monitor):
        with patch.object(GroupRound, "decide",
                          return_value=GroupRound.SKIP) as mock_decide:
            monitor._group_decision("Twilight")
        return mock_decide.call_args.kwargs["skip_moons"]

    def test_only_moons_are_passed(self):
        monitor = self._monitor({"Classic", "Twilight", "Bloodbath"})

        self.assertEqual(self._passed(monitor), {"Twilight"})

    def test_an_empty_list_passes_an_empty_set(self):
        monitor = self._monitor()

        self.assertEqual(self._passed(monitor), set())

    def test_every_moon_is_passed_through(self):
        monitor = self._monitor(set(RoundSequence.MOONS) | {"Classic"})

        self.assertEqual(self._passed(monitor), set(RoundSequence.MOONS))

    def test_a_checked_moon_skips_end_to_end(self):
        """干し芋の窓で、1回目の Twilight が自爆になること"""
        SharedState.set_list_source("host")
        try:
            monitor = self._monitor({"Twilight"})
            monitor.st.in_round = True
            with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
                 patch.object(PlaySound, "play_sound"), \
                 patch.object(ConnectDB, "send_ToNRoundStatistics"):
                monitor._on_killers([7], "Twilight", revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]
        finally:
            SharedState.set_list_source(None)

        self.assertIn("do_skip", started)

    def test_the_round_sequence_is_not_touched(self):
        """消化済み扱いにしない。moon_done も is_moon_repeat も動かさない"""
        seq = RoundSequence.RoundSequence()
        monitor = self._monitor({"Twilight"})
        monitor.sequence = seq

        with patch.object(GroupRound, "decide", return_value=GroupRound.SKIP):
            monitor._group_decision("Twilight")

        self.assertFalse(any(seq.moon_done.values()))
        self.assertFalse(seq.is_moon_repeat("Twilight"))


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

    def test_a_non_murderer_wanting_the_star_slot_is_wanted(self):
        """誰かが欲しがっている続行。全続行とは別枠（アナウンスが要る）"""
        wishes = {"ソノア7": {self.MURDER: {99}},
                  "みているひと": {self.STAR: {5}}}

        self.assertEqual(
            self._decide(config.INSTANCE_YAKIIMO, sus=["ソノア7"], wishes=wishes),
            GroupRound.WANTED)

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
            GroupRound.WANTED, "マーダー希望があっても star で続行になること")

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

    def test_waiting_players_are_in_the_wishes(self):
        """待機も名前ごとの希望に入れる。誰がいるかは窓ごとにこちらで見る
        （ToN ListTool は複窓だと全員を待機へ移すことがある）"""
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member("ソノア7", {self.CLASSIC: {"1": 1}})],
            "waiting": [self._member("まちびと", {self.CLASSIC: {"9": 1}})]}]})

        keep_on, meta, wishes = MatchTNL.load_host_save(path)

        self.assertEqual(set(wishes), {"ソノア7", "まちびと"})
        self.assertEqual(wishes["まちびと"], {self.CLASSIC: {9}})
        self.assertEqual(keep_on, {self.CLASSIC: {1}}, "共有リストは参加者だけ")
        self.assertEqual((meta["participants"], meta["listed"]), (1, 2))

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
        """待機は共有リストに混ぜない（名前ごとの希望には入る。TestWaitingList）"""
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


class TestWishesPerWindow(unittest.TestCase):
    """窓ごとに「そのインスタンスにいる人」の希望だけで判定する。

    主催リストは全窓で共有しているので、そのままだと焼き芋の窓の参加者の
    希望でソロの窓まで判定してしまう（依頼者: 焼き芋1＋ソロ3で、ソロなのに続行）。
    """

    DT = "Double Trouble/ダブルトラブル"
    ME = "usr_0e01408a"
    PREFIX = "2026.09.19 10:00:00 Debug      -  "

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
        SharedState.set_list_source(None)

    WISHES = {
        "serim01": {DT: {1}},
        "さぶりむ": {DT: {2}},
        "offall": {},                  # リストはあるが全部 OFF
        "roundmate": {DT: {5}},        # 焼き芋の周回の参加者
        "elsewhere": {DT: {9}},        # 周回の参加者だが、この窓にはいない
    }

    def _monitor(self, me="serim01", others=(), itype=config.INSTANCE_PRIVATE,
                 wishes=None, known=True):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost="lost.mp3")
        union = {self.DT: {1, 5, 9}}
        monitor = LogMonitor.LogMonitor(
            cfg, union, lambda _m: None, window_idx=1,
            host_wishes=dict(self.WISHES if wishes is None else wishes))
        st = monitor.st
        st.instance_type = itype
        st.in_round = True
        st.round_type = "Double Trouble"
        st.local_player_name = me
        st.local_user_id = self.ME
        st.players = {self.ME}
        st.player_names = {self.ME: me}
        for n, name in enumerate(others):
            uid = f"usr_{n:04x}a"
            st.players.add(uid)
            st.player_names[uid] = name
        st.players_known = known
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), "Double Trouble", revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _line(self, monitor, body):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.PREFIX + body)

    # ── 誰の希望を使うか ──────────────────────
    def test_a_group_window_uses_those_present(self):
        monitor = self._monitor(others=("roundmate",), itype=config.INSTANCE_YAKIIMO)

        self.assertEqual(monitor._keep_on(), {self.DT: {1, 5}})

    def test_a_solo_window_is_not_pulled_by_the_lap(self):
        """依頼者の報告そのもの"""
        monitor = self._monitor()

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})
        started = self._killers(monitor, [5, 3])
        self.assertIn("do_skip", started, "焼き芋の参加者の希望で続行しない")

    def test_a_sub_account_uses_its_own_list(self):
        monitor = self._monitor(me="さぶりむ")

        self.assertEqual(monitor._keep_on(), {self.DT: {2}})
        self.assertNotIn("do_skip", self._killers(monitor, [2, 3]))

    def test_my_own_list_counts_without_my_join_line(self):
        """自分の入室行を取りこぼしても、アカウント名（User Authenticated）で引く"""
        monitor = self._monitor()
        monitor.st.player_names.pop(self.ME)

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    def test_a_lap_member_who_is_elsewhere_does_not_count(self):
        monitor = self._monitor(others=("roundmate",), itype=config.INSTANCE_YAKIIMO)

        self.assertNotIn(9, monitor._keep_on()[self.DT])

    def test_nobody_else_with_wishes_falls_to_my_own_list(self):
        monitor = self._monitor(others=("stranger",))

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    def test_no_list_at_all_stops(self):
        """誰もリストを持っていない。続行が無いのか分からないので止める"""
        monitor = self._monitor(me="nobody", others=("stranger",))

        started = self._killers(monitor, [5, 3])

        self.assertEqual(started, [])
        self.assertTrue(any("続行リストがありません" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_solo_list_with_everything_off_skips_everything(self):
        """依頼者: 続行なかったら全部死ぬだけ"""
        monitor = self._monitor(me="offall")

        self.assertEqual(monitor._keep_on(), {})
        started = self._killers(monitor, [1, 5])

        self.assertIn("do_skip", started)
        self.assertFalse(any("続行リストがありません" in m for m in monitor.logs),
                         "止めずに判定すること")

    def test_an_everything_off_player_is_not_unmatched(self):
        monitor = self._monitor(others=("offall",), itype=config.INSTANCE_YAKIIMO)

        self._killers(monitor, [1, 3])

        self.assertFalse(any("希望が見つからない入室者" in m for m in monitor.logs),
                         monitor.logs)

    def test_the_periodic_check_agrees(self):
        monitor = self._monitor(me="nobody")

        with patch.object(PlaySound, "play_sound") as played:
            monitor._check_group_list_state()

        self.assertTrue(monitor.st.list_lost_notified)
        played.assert_called_once_with("lost.mp3")

    def test_finding_wishes_again_resumes(self):
        monitor = self._monitor(me="nobody")
        with patch.object(PlaySound, "play_sound"):
            monitor._check_group_list_state()

        self._line(monitor, "[Behaviour] OnPlayerJoined roundmate (usr_bbbb)")
        monitor._check_group_list_state()

        self.assertFalse(monitor.st.list_lost_notified)
        self.assertTrue(any("続行リストが見つかりました" in m for m in monitor.logs),
                        monitor.logs)

    # ── 従来どおりのところ ─────────────────────
    def test_the_tnl_is_used_as_before(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self.assertIs(monitor._keep_on(), monitor.keepOn_set)

    def test_unknown_presence_keeps_the_shared_list(self):
        """誰がいるか分からないときは絞り込まない（A の安全側と同じ）"""
        monitor = self._monitor(known=False)

        self.assertIs(monitor._keep_on(), monitor.keepOn_set)

    def test_no_per_person_wishes_keeps_the_shared_list(self):
        monitor = self._monitor(wishes={})

        self.assertIs(monitor._keep_on(), monitor.keepOn_set)

    # ── Sabotage ───────────────────────────
    def test_sabotage_only_sees_those_present(self):
        monitor = self._monitor(others=("roundmate",), itype=config.INSTANCE_YAKIIMO)

        self.assertEqual(set(monitor._group_wishes()), {"serim01", "roundmate"})

    def test_sabotage_ignores_someone_who_is_elsewhere(self):
        star = GroupRound.SABOTAGE_STAR_KEY
        wishes = {"serim01": {}, "elsewhere": {star: {7}}}
        monitor = self._monitor(itype=config.INSTANCE_YAKIIMO, wishes=wishes)
        monitor.st.round_type = "Sabotage"
        monitor.st.terror_ids = [7]

        self.assertNotEqual(monitor._group_decision("Sabotage"), GroupRound.WANTED,
                            "その場にいない人の希望で続行しない")

    # ── 入退室 ────────────────────────────
    def test_joining_and_leaving_move_the_list(self):
        monitor = self._monitor()
        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

        self._line(monitor, "[Behaviour] OnPlayerJoined roundmate (usr_bbbb)")
        self.assertEqual(monitor._keep_on(), {self.DT: {1, 5}})

        self._line(monitor, "[Behaviour] OnPlayerLeft roundmate (usr_bbbb)")
        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    def test_a_new_instance_forgets_the_names(self):
        monitor = self._monitor(others=("roundmate",))

        self._line(monitor, "[Behaviour] Joining wrld_1:2~private(usr_x)")

        self.assertEqual(monitor.st.player_names, {})

    def test_an_unmatched_player_is_logged_once(self):
        monitor = self._monitor(others=("stranger",), itype=config.INSTANCE_YAKIIMO)

        self._killers(monitor, [1, 3])
        self._killers(monitor, [1, 3])

        hits = [m for m in monitor.logs if "希望が見つからない入室者" in m]
        self.assertEqual(len(hits), 1, monitor.logs)
        self.assertIn("stranger", hits[0])

    def test_restoring_brings_the_names_back(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        tmp.write("\n".join(self.PREFIX + line for line in (
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_x)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[Behaviour] OnPlayerJoined roundmate (usr_bbbb)",
        )) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        cfg = WindowConfig(log_path=Path(tmp.name))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1,
                                        host_wishes=dict(self.WISHES))
        monitor.logger = lambda _m: None

        with patch.object(ConnectDB, "send_Users", return_value=1):
            monitor._detect_instance_from_log()

        self.assertEqual(monitor._present_names(), {"serim01", "roundmate"})
        self.assertEqual(monitor._keep_on(), {self.DT: {1, 5}})


class TestWaitingList(unittest.TestCase):
    """ToN ListTool の「参加者」判定に頼らない。

    ListTool は VRChat のログを読んで、その場にいる人を参加者・いない人を待機に
    振り分ける。複窓だとソロの窓のログを読んで、干し芋の全員を待機へ移す
    （実測: 参加者18・待機251 → 参加者0・待機269）。待機の人も続行リストを
    持ったまま残っているので、誰がいるかは窓ごとにこちらで見る。
    """

    DT = "Double Trouble/ダブルトラブル"
    ME = "usr_0e01408a"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        self._stats = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.host = str(Path(self._dir.name) / "host_save.json.gz")

    def _write(self, participants=(), waiting=()):
        def member(name, wanted):
            return {"vrc_name": name, "data": {self.DT: {str(t): 1 for t in wanted}}}
        with gzip.open(self.host, "wb") as f:
            f.write(json.dumps({"version": 5, "tabs": [{
                "participants": [member(n, w) for n, w in participants],
                "waiting": [member(n, w) for n, w in waiting]}]},
                ensure_ascii=False).encode("utf-8"))

    def _everyone_moved_to_waiting(self):
        """干し芋の2人と、別の周回の人。ListTool が全員を待機へ移した後"""
        self._write(participants=(), waiting=(("hoshi_a", {5}), ("hoshi_b", {6}),
                                              ("someone_else", {9})))
        return MatchTNL.load_host_save(self.host)

    def _monitor(self, keep, wishes, *, present=("hoshi_a", "hoshi_b"),
                 me="serim01", itype=config.INSTANCE_HOSHIIMO, known=True,
                 participants=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep, lambda _m: None, window_idx=1,
                                        host_wishes=wishes,
                                        host_participants=participants)
        st = monitor.st
        st.instance_type = itype
        st.in_round = True
        st.round_type = "Double Trouble"
        st.local_player_name = me
        st.local_user_id = self.ME
        st.players = {self.ME}
        st.player_names = {self.ME: me}
        for n, name in enumerate(present):
            uid = f"usr_{n:04x}c"
            st.players.add(uid)
            st.player_names[uid] = name
        st.players_known = known
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), "Double Trouble", revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 読み込み ─────────────────────────────
    def test_the_waiting_list_is_read_into_the_wishes(self):
        keep, meta, wishes = self._everyone_moved_to_waiting()

        self.assertEqual(wishes["hoshi_a"], {self.DT: {5}})
        self.assertEqual(meta["participants"], 0)
        self.assertEqual(meta["listed"], 3)
        self.assertEqual(keep, {}, "共有リストは参加者だけ（ここでは空）")

    # ── 窓の判定 ─────────────────────────────
    def test_a_group_window_keeps_judging_from_the_waiting_list(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes)

        self.assertEqual(monitor._list_block_reason(), "")
        self.assertEqual(monitor._keep_on(), {self.DT: {5, 6}})
        started = self._killers(monitor, [5, 1])
        self.assertNotIn("do_skip", started, "hoshi_a の希望で続行")
        self.assertTrue(monitor.st.is_continue_round)

    def test_someone_waiting_elsewhere_is_not_used(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes)

        self.assertNotIn(9, monitor._keep_on()[self.DT])

    def test_a_solo_window_still_uses_its_own_list(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        wishes["serim01"] = {self.DT: {1}}
        monitor = self._monitor(keep, wishes, present=(),
                                itype=config.INSTANCE_PRIVATE)

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    # ── 誰がいるか分からない窓 ────────────────────
    def test_an_unknown_window_with_an_empty_shared_list_stops(self):
        """共有リストが空のまま判定すると全ラウンド自爆する"""
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes, known=False)

        started = self._killers(monitor, [5, 1])

        self.assertEqual(started, [])
        self.assertTrue(any("続行リストがありません" in m for m in monitor.logs),
                        monitor.logs)

    def test_an_unknown_window_with_a_shared_list_still_judges(self):
        keep = {self.DT: {5}}
        monitor = self._monitor(keep, {"hoshi_a": {self.DT: {5}}}, known=False)

        self.assertEqual(monitor._list_block_reason(), "")

    def test_the_periodic_check_agrees(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes, known=False)

        with patch.object(PlaySound, "play_sound"):
            monitor._check_group_list_state()

        self.assertTrue(monitor.st.list_lost_notified)

    def test_sabotage_in_an_unknown_window_ignores_the_waiting_list(self):
        """誰がいるか分からないときは、参加者（＋自分）の希望だけ"""
        wishes = {"hoshi_a": {"x": {1}}, "waiting_person": {"x": {2}}}
        monitor = self._monitor({}, wishes, known=False,
                                participants={"hoshi_a"})

        self.assertEqual(set(monitor._group_wishes()), {"hoshi_a"})

    def test_the_participant_names_include_the_host(self):
        user = str(Path(self._dir.name) / "user_save.json")
        Path(user).write_text(json.dumps({"last_active": "serim01", "accounts": {
            "serim01": {"data": {self.DT: {"1": 1}}}}}), encoding="utf-8")
        self._write(participants=(("hoshi_a", {5}),), waiting=(("w", {9}),))

        _keep, meta, _wishes = MatchTNL.load_host_save(self.host, user)

        self.assertEqual(meta["participant_names"], {"hoshi_a", "serim01"})


class TestHostSaveAllAccounts(unittest.TestCase):
    """user_save.json の全アカウントを、名前ごとの希望として読む"""

    PAGES = "8 Pages/8ページ"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.host = str(Path(self._dir.name) / "host_save.json.gz")
        self.user = str(Path(self._dir.name) / "user_save.json")
        with gzip.open(self.host, "wb") as f:
            f.write(json.dumps({"version": 5, "tabs": [{"participants": [
                {"vrc_name": "roundmate", "data": {self.PAGES: {"70": 1}}}]}]}
            ).encode("utf-8"))
        Path(self.user).write_text(json.dumps({
            "last_active": "serim01",
            "accounts": {
                "serim01": {"data": {self.PAGES: {"10": 1}}},
                "ruri9752 fff9": {"data": {self.PAGES: {"20": 1}}},
                "さぶりむ": {"data": {self.PAGES: {"30": 0}}},   # 何もONでない
                "壊れ": {"data": "辞書ではない"},
            }}, ensure_ascii=False), encoding="utf-8")

    def _load(self):
        return MatchTNL.load_host_save(self.host, self.user)

    def test_every_account_is_in_the_wishes(self):
        _keep, _meta, wishes = self._load()

        self.assertEqual(wishes["serim01"], {self.PAGES: {10}})
        self.assertEqual(wishes["ruri9752 fff9"], {self.PAGES: {20}})
        self.assertEqual(wishes["roundmate"], {self.PAGES: {70}})

    def test_an_account_with_everything_off_is_kept_empty(self):
        """さぶりむ（全部 OFF）はリストを持っている。壊れたものは持っていない"""
        _keep, _meta, wishes = self._load()

        self.assertEqual(wishes["さぶりむ"], {})
        self.assertNotIn("壊れ", wishes)

    def test_a_participant_with_everything_off_is_kept_empty(self):
        """周回の参加者も同じ扱い"""
        with gzip.open(self.host, "wb") as f:
            f.write(json.dumps({"version": 5, "tabs": [{"participants": [
                {"vrc_name": "roundmate", "data": {self.PAGES: {"70": 0}}}]}]}
            ).encode("utf-8"))

        _keep, meta, wishes = self._load()

        self.assertEqual(wishes["roundmate"], {})
        self.assertEqual(meta["participants"], 1)

    def test_the_shared_list_still_adds_only_the_active_account(self):
        """GUI の件数表示は従来どおり"""
        keep, meta, _wishes = self._load()

        self.assertEqual(keep, {self.PAGES: {10, 70}})
        self.assertEqual(meta["host_self"], "serim01")
        self.assertEqual(meta["participants"], 1)


class TestHostOwnList(unittest.TestCase):
    """主催者自身の続行リスト（host_save の participants には入らない）"""

    CLASSIC = "Classic/クラシック"
    PAGES = "8 Pages/8ページ"
    FOG_ALT = "Fog (Alternate)/霧 (Alternate)"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.host = str(Path(self._dir.name) / "host_save.json.gz")
        self.user = str(Path(self._dir.name) / "user_save.json")

    def tearDown(self):
        self._dir.cleanup()

    def _write_host(self, participants=1, name="ひと1"):
        members = [{"vrc_name": f"{name}{n}", "data": {self.CLASSIC: {str(n + 5): 1}}}
                   for n in range(participants)]
        with gzip.open(self.host, "wb") as f:
            f.write(json.dumps({"version": 5,
                                "tabs": [{"participants": members}]}).encode("utf-8"))

    def _write_user(self, raw):
        Path(self.user).write_text(json.dumps(raw), encoding="utf-8")

    def _account(self, data, name="serim01"):
        return {"version": 200, "last_active": name,
                "accounts": {name: {"list_name": f"{name}のリスト", "data": data}}}

    def _load(self, with_user=True):
        return MatchTNL.load_host_save(self.host, self.user if with_user else None)

    # ── 足される ────────────────────────────
    def test_the_host_wishes_are_merged(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1, "135": 1}}))

        keep_on, _meta, _wishes = self._load()

        self.assertEqual(keep_on[self.PAGES], {70, 135})
        self.assertEqual(keep_on[self.CLASSIC], {5}, "participants も残ること")

    def test_the_host_appears_in_the_wishes(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        _keep_on, _meta, wishes = self._load()

        self.assertEqual(wishes["serim01"], {self.PAGES: {70}})

    def test_without_the_path_nothing_changes(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        keep_on, meta, wishes = self._load(with_user=False)

        self.assertNotIn(self.PAGES, keep_on)
        self.assertNotIn("serim01", wishes)
        self.assertIsNone(meta["host_self"])

    def test_the_name_is_reported(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1}}, name="さぶりむ"))

        _keep_on, meta, _wishes = self._load()

        self.assertEqual(meta["host_self"], "さぶりむ")

    # ── 人数に数えない（ここが事故のもと） ──────
    def test_the_host_is_not_counted(self):
        self._write_host(3)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        _keep_on, meta, _wishes = self._load()

        self.assertEqual(meta["participants"], 3, "自分を足して4にしないこと")

    def test_an_empty_lap_stays_empty(self):
        """0人のままでないと .tnl へのフォールバックと「手を止める」が効かない"""
        self._write_host(0)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        keep_on, meta, _wishes = self._load()

        self.assertEqual(meta["participants"], 0)
        self.assertEqual(keep_on[self.PAGES], {70}, "リスト自体は読めている")

    # ── 諦める側 ────────────────────────────
    def test_a_missing_file_is_ignored(self):
        self._write_host(1)

        keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])
        self.assertEqual(keep_on, {self.CLASSIC: {5}})

    def test_broken_json_is_ignored(self):
        self._write_host(1)
        Path(self.user).write_text("{ not json", encoding="utf-8")

        _keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])

    def test_an_unknown_last_active_is_ignored(self):
        self._write_host(1)
        raw = self._account({self.PAGES: {"70": 1}})
        raw["last_active"] = "だれか"

        self._write_user(raw)
        _keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])

    def test_a_non_dict_data_is_ignored(self):
        self._write_host(1)
        self._write_user(self._account("これは辞書ではない"))

        _keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])

    def test_a_broken_host_save_still_raises(self):
        """host_save 側のデコード失敗は従来どおり例外（呼び出し側が握る）"""
        Path(self.host).write_bytes(b"half written garbage")
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        with self.assertRaises(Exception):
            self._load()

    # ── participants と同じ扱い ────────────────
    def test_zero_slots_are_dropped(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 0, "135": 0},
                                        self.CLASSIC: {"9": 1}}))

        keep_on, _meta, wishes = self._load()

        self.assertNotIn(self.PAGES, keep_on)
        self.assertEqual(wishes["serim01"], {self.CLASSIC: {9}})

    def test_the_alternate_keys_are_ignored(self):
        self._write_host(1)
        self._write_user(self._account({self.FOG_ALT: {"1": 1},
                                        self.PAGES: {"70": 1}}))

        keep_on, _meta, wishes = self._load()

        self.assertNotIn(self.FOG_ALT, keep_on)
        self.assertEqual(wishes["serim01"], {self.PAGES: {70}})

    def test_a_host_with_everything_off_is_kept_empty(self):
        """全部 OFF は「続行したいものが無い」。リストが無いのとは違う"""
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 0}}))

        _keep_on, meta, wishes = self._load()

        self.assertEqual(meta["host_self"], "serim01", "読めてはいる")
        self.assertEqual(wishes["serim01"], {}, "空でも {} で残すこと")

    def test_a_duplicate_name_is_merged(self):
        """participants に同名がいても OR されるだけ"""
        with gzip.open(self.host, "wb") as f:
            f.write(json.dumps({"version": 5, "tabs": [{"participants": [
                {"vrc_name": "serim01",
                 "data": {self.CLASSIC: {"5": 1}}}]}]}).encode("utf-8"))
        self._write_user(self._account({self.CLASSIC: {"9": 1}}))

        keep_on, _meta, wishes = self._load()

        self.assertEqual(keep_on[self.CLASSIC], {5, 9})
        self.assertEqual(wishes["serim01"][self.CLASSIC], {5, 9})

    def test_the_real_file_is_readable(self):
        """現物で読めること（形が変わっていたら気づけるように）"""
        if not Path(config.USER_SAVE_PATH).exists():
            self.skipTest("user_save.json が無い")
        raw = json.loads(Path(config.USER_SAVE_PATH).read_text(encoding="utf-8"))

        name = raw.get("last_active")
        self.assertIsInstance(name, str)
        self.assertIn(name, raw.get("accounts", {}))
        self.assertIsInstance(raw["accounts"][name].get("data"), dict)


class TestHostListGrace(unittest.TestCase):
    """主催リストが一瞬取れなくても、すぐには tnl へ切り替えない。

    プロセスが見えない・host_save を stat できない・参加者0人は、どれも一瞬
    だけ起きうる。1回で切り替えると他の人がいる窓でだけ警告が鳴り、
    「複窓で頻繁に落ちる」ように見えていた。
    """

    CLASSIC = "Classic/クラシック"
    GRACE = 10.0

    def setUp(self):
        grace = patch.object(config, "HOST_LIST_LOSS_GRACE_SEC", self.GRACE)
        grace.start()
        self.addCleanup(grace.stop)
        SharedState.set_list_source(None)
        self.addCleanup(SharedState.set_list_source, None)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = str(Path(self._dir.name) / "host_save.json.gz")
        self.user_save = str(Path(self._dir.name) / "user_save.json")
        self.tnl = Path(self._dir.name) / "list.tnl"
        self.tnl.write_text(json.dumps(
            {"list_name": "L", "creator": "", "created_at": "",
             "data": {self.CLASSIC: {"1": 1}}}), encoding="utf-8")
        self.now = 1000.0
        self.app = self._app()

    def _app(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.host_wishes = {}
        app._host_save_stamp = None
        app._host_save_warned = False
        app._host_loss_since = None
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app.v_tnl = TestHostListSource.FakeVar(str(self.tnl))
        for name in ("_apply_keep_on", "_apply_host_wishes", "_host_list_lost",
                     "_warn_host_save_once", "_fall_back_to_tnl", "_load_tnl"):
            method = getattr(mainGUI.App, name)
            setattr(app, name, (lambda m: lambda *a, **kw: m(app, *a, **kw))(method))
        return app

    def _write(self, participants, wanted=7):
        members = [{"vrc_name": f"ひと{n}", "data": {self.CLASSIC: {str(wanted): 1}}}
                   for n in range(participants)]
        with gzip.open(self.path, "wb") as f:
            f.write(json.dumps({"version": 5,
                                "tabs": [{"participants": members}]}).encode("utf-8"))

    def _tick(self, after=0.0, running=True, stat_fails=False):
        """3秒ごとの確認を1回回す。after 秒たってから"""
        self.now += after
        stat = patch.object(mainGUI.os, "stat", side_effect=OSError("swapping")) \
            if stat_fails else patch.object(mainGUI.os, "stat", wraps=os.stat)
        with patch.object(config, "HOST_SAVE_PATH", self.path), \
             patch.object(config, "USER_SAVE_PATH", self.user_save), \
             patch.object(ProcessCheck, "is_process_running", return_value=running), \
             patch.object(mainGUI.time, "monotonic", side_effect=lambda: self.now), \
             patch.object(mainGUI, "save_settings"), \
             patch.object(mainGUI, "load_settings", return_value={}), \
             stat:
            mainGUI.App._refresh_host_source(self.app)

    def _on_host_list(self):
        self._write(2)
        self._tick()
        self.assertEqual(SharedState.get_list_source(), "host", "前提")
        self.host_list = dict(self.app.keepOn_set)

    def _grace_logs(self):
        return [m for m in self.app.logs if "猶予中" in m]

    # ── 一瞬では切り替えない ─────────────────────
    def test_a_moment_without_the_file_keeps_the_host_list(self):
        self._on_host_list()

        self._tick(3, stat_fails=True)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(self.app.keepOn_set, self.host_list, "前の主催リストのまま")

    def test_a_moment_without_participants_keeps_the_host_list(self):
        self._on_host_list()
        self._write(0)

        self._tick(3)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(self.app.keepOn_set, self.host_list)

    def test_a_moment_without_the_process_keeps_the_host_list(self):
        self._on_host_list()

        self._tick(3, running=False)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(self.app.keepOn_set, self.host_list)

    # ── 続いたら切り替える ───────────────────────
    def test_a_lasting_loss_switches_to_the_tnl(self):
        self._on_host_list()

        self._tick(3, running=False)
        self._tick(3, running=False)
        self.assertEqual(SharedState.get_list_source(), "host", "まだ6秒")
        self._tick(self.GRACE, running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(self.app.keepOn_set, {self.CLASSIC: {1}})

    def test_a_good_read_resets_the_grace(self):
        self._on_host_list()
        self._tick(3, running=False)
        self._tick(self.GRACE - 4, running=True)      # 途中で読めた

        self._tick(3, running=False)                  # また一瞬
        self._tick(self.GRACE - 4, running=False)

        self.assertEqual(SharedState.get_list_source(), "host",
                         "読めた時点で数え直していること")

    def test_the_grace_is_logged_once(self):
        self._on_host_list()

        for _ in range(3):
            self._tick(2, running=False)

        self.assertEqual(len(self._grace_logs()), 1, self.app.logs)

    def test_a_new_loss_logs_again(self):
        self._on_host_list()
        self._tick(3, running=False)
        self._tick(3)                                 # 戻った
        self._tick(3, stat_fails=True)                # また取れない

        self.assertEqual(len(self._grace_logs()), 2, self.app.logs)

    # ── 戻るのはすぐ／守るものが無ければすぐ ─────────────
    def test_returning_from_the_tnl_is_immediate(self):
        self._tick(running=False)
        self.assertEqual(SharedState.get_list_source(), "tnl", "前提")
        self._write(2)

        self._tick(0.1)

        self.assertEqual(SharedState.get_list_source(), "host")

    def test_no_host_list_yet_goes_to_the_tnl_at_once(self):
        """まだ主催リストを使っていなければ、守るものが無い"""
        self._tick(running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(self._grace_logs(), [])


class TestHostListSource(unittest.TestCase):
    """続行リストの供給元を状況から決める（チェックボックスは無い）

    ToN ListTool が動いていて参加者がいれば主催リスト、それ以外は .tnl。
    ツールを閉じてもファイルは残るので、鮮度はプロセスの生死で見る。
    """

    CLASSIC = "Classic/クラシック"

    def setUp(self):
        # この組は「どの条件で切り替わるか」を見る。猶予は TestHostListGrace で見る
        grace = patch.object(config, "HOST_LIST_LOSS_GRACE_SEC", 0.0)
        grace.start()
        self.addCleanup(grace.stop)
        SharedState.set_list_source(None)
        self._dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self._dir.name) / "host_save.json.gz")
        self.user_save = str(Path(self._dir.name) / "user_save.json")   # 作らない
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
        app._host_loss_since = None
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app.v_tnl = TestHostListSource.FakeVar(str(self.tnl))
        for name in ("_apply_keep_on", "_apply_host_wishes", "_host_list_lost",
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
        # 主催者自身のリストは既定では使わない（実ファイルに引きずられないため）
        with patch.object(config, "HOST_SAVE_PATH", self.path), \
             patch.object(config, "USER_SAVE_PATH", self.user_save), \
             patch.object(ProcessCheck, "is_process_running", return_value=running), \
             patch.object(mainGUI, "save_settings"), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._refresh_host_source(app)

    def _switch_logs(self, app):
        return [m for m in app.logs if "[続行リスト]" in m]

    # ── 待機（ToN ListTool が全員を待機へ移したとき） ─────────
    def _write_lists(self, participants, waiting):
        members = lambda n: [{"vrc_name": f"ひと{i}", "data": {self.CLASSIC: {"5": 1}}}
                             for i in range(n)]
        with gzip.open(self.path, "wb") as f:
            f.write(json.dumps({"version": 5, "tabs": [{
                "participants": members(participants),
                "waiting": members(waiting)}]}).encode("utf-8"))

    def test_only_waiting_keeps_the_host_list(self):
        app = self._app()
        self._write_lists(2, 0)
        self._refresh(app)
        self.assertEqual(SharedState.get_list_source(), "host", "前提")

        self._write_lists(0, 3)            # ListTool が全員を待機へ移した
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")

    def test_nobody_anywhere_falls_back(self):
        app = self._app()
        self._write_lists(2, 0)
        self._refresh(app)

        self._write_lists(0, 0)
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertTrue(any("続行リストを持つ人がいません" in m for m in app.logs),
                        app.logs)

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
        self.assertIn("続行リストを持つ人がいません", joined)
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

    def test_a_user_save_change_is_picked_up(self):
        """自分のリストだけ更新したときも読み直すこと"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        Path(self.user_save).write_text(
            json.dumps({"last_active": "serim01",
                        "accounts": {"serim01": {"data": {}}}}), encoding="utf-8")
        with patch.object(MatchTNL, "load_host_save",
                          return_value=({"x": {1}}, {"participants": 1, "listed": 1, "tabs": 1,
                                                     "host_self": None},
                                        {})) as mock_load:
            self._refresh(app)

        mock_load.assert_called_once()

    def test_the_log_marks_the_host_being_included(self):
        self._write(3)
        app = self._app()

        with patch.object(MatchTNL, "load_host_save",
                          return_value=({"x": {1}},
                                        {"participants": 3, "listed": 3, "tabs": 1,
                                         "host_self": "serim01"}, {})):
            self._refresh(app)

        self.assertTrue(any("(+自分)" in m for m in app.logs), app.logs)

    def test_the_log_omits_it_when_the_host_is_missing(self):
        self._write(3)
        app = self._app()

        self._refresh(app)

        hits = [m for m in app.logs if "続行対象" in m]
        self.assertTrue(hits)
        self.assertNotIn("(+自分)", hits[0])

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
        mtime, size, user = app._host_save_stamp

        # サイズは同じで mtime だけ違う（同じ秒内の書き換え相当）
        app._host_save_stamp = (mtime - 1, size, user)
        with patch.object(MatchTNL, "load_host_save",
                          return_value=({"x": {1}}, {"participants": 1, "listed": 1, "tabs": 1},
                                        {})) as mock_load:
            self._refresh(app)
        mock_load.assert_called_once()

        # mtime は同じでサイズだけ違う
        app._host_save_stamp = (app._host_save_stamp[0], size - 1, user)
        with patch.object(MatchTNL, "load_host_save",
                          return_value=({"y": {2}}, {"participants": 1, "listed": 1, "tabs": 1},
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


def _single_attempt(test):
    """1回目の自爆・Beginクリックだけを見るテスト用。やり直しは止める。

    やり直しは死亡や受理を待つので、ここを止めないと本物の時間を待ち、
    呼び出し回数も変わる。やり直し自体は TestSuicideRetry / TestBeginRetry で見る。
    """
    for name, value in (("SUICIDE_RETRY_MAX", 1), ("SUICIDE_CONFIRM_SEC", 0.0),
                        ("BEGIN_RETRY_MAX", 1), ("BEGIN_RETRY_WAIT_SEC", 0.0)):
        patcher = patch.object(config, name, value)
        patcher.start()
        test.addCleanup(patcher.stop)


class TestSuicideRetry(unittest.TestCase):
    """死ななければ自爆をやり直す（背面送信のまま。最大 SUICIDE_RETRY_MAX 回）"""

    def setUp(self):
        SharedState.set_suicide_key("^")
        for name, value in (("SUICIDE_CONFIRM_SEC", 0.05), ("SUICIDE_RETRY_MAX", 3)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _executor(self, **state):
        cfg = WindowConfig(hwnd=123, do_skip=True)
        st = WindowState(in_round=True, round_seq=7, **state)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        return ex, st, logs

    def _skip(self, ex, st, die_on=None, before=None):
        """die_on 回目の長押しで死ぬ。before(n) は n 回目の長押しの直前に呼ぶ"""
        sent = []

        def hold(hwnd, key, sec):
            sent.append(hwnd)
            if before:
                before(len(sent))
            if die_on is not None and len(sent) >= die_on:
                st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold), \
             patch.object(WindowOperator, "focus_window") as focus, \
             patch.object(WindowOperator, "hold_key") as foreground:
            ex.do_skip()
        focus.assert_not_called()
        foreground.assert_not_called()
        return len(sent)

    def test_it_tries_again_when_still_alive(self):
        ex, st, logs = self._executor()

        self.assertEqual(self._skip(ex, st, die_on=2), 2)
        self.assertTrue(any("やり直し（2/3回目）" in m for m in logs), logs)

    def test_a_death_stops_it(self):
        ex, st, _logs = self._executor()

        self.assertEqual(self._skip(ex, st, die_on=1), 1)

    def test_it_gives_up_after_three(self):
        ex, st, logs = self._executor()

        self.assertEqual(self._skip(ex, st), 3)
        self.assertTrue(any("⚠ 自爆を3回試しましたが死にませんでした" in m
                            for m in logs), logs)

    def test_a_continue_round_stops_the_retry(self):
        ex, st, logs = self._executor()

        def turn_into_continue(n):
            if n == 1:
                st.is_continue_round = True     # 1回目の最中に続行と分かった

        self.assertEqual(self._skip(ex, st, before=turn_into_continue), 1)
        self.assertFalse(any("試しましたが" in m for m in logs), "警告は出さない")

    def test_a_new_round_stops_the_old_retry(self):
        ex, st, logs = self._executor()

        def next_round(n):
            if n == 1:
                st.round_seq += 1

        self.assertEqual(self._skip(ex, st, before=next_round), 1)
        self.assertFalse(any("試しましたが" in m for m in logs))

    def test_the_round_ending_stops_it(self):
        ex, st, _logs = self._executor()

        def round_over(n):
            if n == 1:
                st.in_round = False

        self.assertEqual(self._skip(ex, st, before=round_over), 1)

    def test_its_own_item_wait_stops_it(self):
        ex, st, _logs = self._executor()

        def lost(n):
            if n == 1:
                st.waiting_for_equip = True

        self.assertEqual(self._skip(ex, st, before=lost), 1)

    def test_a_death_just_after_the_hold_counts(self):
        """長押しが終わった直後の死亡も成功として拾う（やり直さない）"""
        ex, st, _logs = self._executor()
        sent = []
        naps = []

        def nap(_sec):
            naps.append(_sec)
            st.died_this_round = True     # 確認の待ちの間に死亡が届いた

        with patch.object(config, "SUICIDE_CONFIRM_SEC", 5.0), \
             patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda *a: sent.append(1) or True), \
             patch.object(ActionExecutor.time, "sleep", side_effect=nap):
            ex.do_skip()

        self.assertEqual(len(sent), 1)
        self.assertEqual(len(naps), 1, "死亡を見たらすぐ抜けること")

    def test_an_unsendable_key_is_not_retried(self):
        """最小化などで送れないものは、やり直しても送れない"""
        ex, st, logs = self._executor()

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=False) as bg:
            ex.do_skip()

        bg.assert_called_once()
        self.assertTrue(any("自爆できませんでした" in m for m in logs), logs)

    def test_one_flow_per_round(self):
        """同じラウンドで2本目が来ても、やり直しの流れは1本"""
        ex, st, _logs = self._executor()
        sent = []

        def hold(hwnd, key, sec):
            sent.append(1)
            if len(sent) == 1:
                ex.do_skip()             # 1本目の最中に2本目が来た
            st.died_this_round = True
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            ex.do_skip()

        self.assertEqual(len(sent), 1)
        self.assertEqual(st.suicide_seq, -1, "終わったら次を受け付ける")

    def test_the_success_log_covers_the_end_of_the_hold(self):
        """_skip_time は長押しの開始時。3.0秒ちょうどで切ると終わり際を落とす"""
        for elapsed, expected in ((2.0, True), (4.0, True), (5.0, False)):
            cfg = WindowConfig(do_skip=True)
            monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
            logs = []
            monitor.logger = logs.append
            monitor.st._skip_time = 1000.0

            # この組の setUp は確認の待ちを縮めている。ここは本来の値で見る
            with patch.object(config, "SUICIDE_CONFIRM_SEC", 1.5),                  patch.object(LogMonitor.time, "time", return_value=1000.0 + elapsed):
                monitor._process("You died.")

            self.assertEqual(any("自爆成功" in m for m in logs), expected, elapsed)


class TestBeginRetry(unittest.TestCase):
    """押した Begin が受理（本物の Verified）されなければ、クリックだけ押し直す"""

    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        for name, value in (("BEGIN_RETRY_WAIT_SEC", 0.05), ("BEGIN_RETRY_MAX", 3),
                            ("BEGIN_WAIT_SEC", 0)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()

    def _run(self, osc_port=9000, accept_on=None, after_click=None):
        """accept_on 回目のクリックで受理される。after_click(n) はクリックの後に呼ぶ"""
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=5, round_seq=4)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        clicks = []
        moves = []

        def click():
            clicks.append(1)
            if accept_on is not None and len(clicks) >= accept_on:
                st.begin_done = True
            if after_click:
                after_click(len(clicks), st)

        with patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(ex, "move", side_effect=lambda *a: moves.append(1)), \
             patch.object(ex, "move_forward_left",
                          side_effect=lambda *a: moves.append(1)), \
             patch.object(WindowOperator, "focus_window", return_value=True), \
             patch.object(WindowOperator, "click", side_effect=click):
            ex.do_after_round()
        self.logs = logs
        return len(clicks), len(moves)

    def test_it_clicks_again_without_moving(self):
        for osc_port in (9000, 0):
            clicks, moves = self._run(osc_port, accept_on=2)

            self.assertEqual(clicks, 2, osc_port)
            self.assertEqual(moves, 1, f"{osc_port}: 移動は最初の1回だけ")
            self.assertTrue(any("押し直し（2/3回目）" in m for m in self.logs),
                            self.logs)

    def test_an_accepted_begin_is_not_clicked_again(self):
        for osc_port in (9000, 0):
            clicks, _moves = self._run(osc_port, accept_on=1)

            self.assertEqual(clicks, 1, osc_port)

    def test_it_gives_up_after_three(self):
        clicks, _moves = self._run()

        self.assertEqual(clicks, 3)
        self.assertTrue(any("⚠ Begin を3回押しましたが受理されませんでした" in m
                            for m in self.logs), self.logs)

    def test_a_started_round_stops_the_retry(self):
        def round_started(n, st):
            if n == 1:
                st.in_round = True

        clicks, _moves = self._run(after_click=round_started)

        self.assertEqual(clicks, 1)
        self.assertFalse(any("受理されませんでした" in m for m in self.logs))

    def test_a_new_round_stops_the_old_retry(self):
        def next_round(n, st):
            if n == 1:
                st.round_seq += 1

        clicks, _moves = self._run(after_click=next_round)

        self.assertEqual(clicks, 1)

    def test_it_only_retries_what_it_clicked(self):
        """クリックしていなければ（フォーカス失敗など）押し直しの流れに入らない"""
        cfg = WindowConfig(hwnd=123, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True,
                         item_id=5)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ex, "move_forward_left"), \
             patch.object(ex, "_confirm_begin") as confirm, \
             patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(WindowOperator, "click"):
            ex.do_after_round()

        confirm.assert_not_called()


class TestSuicideBackgroundRouting(unittest.TestCase):
    """do_skip の送信経路（背面だけ。フォーカス方式への落とし先は廃止）"""

    def setUp(self):
        _single_attempt(self)
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

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=True) as mock_bg, \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()

        mock_bg.assert_called_once_with(123, "^", config.SUICIDE_HOLD_SEC)
        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertGreater(st._skip_time, 0, "死亡判定用の時刻は残すこと")

    def test_background_failure_does_not_fall_back(self):
        """落とすとロック無しでプレイ中の窓に自爆キーが押されうる"""
        ex, st, logs = self._executor()

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=False), \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(WindowOperator, "hold_key") as mock_hold, \
             patch.object(ActionExecutor.time, "sleep"):
            ex.do_skip()

        mock_focus.assert_not_called()
        mock_hold.assert_not_called()
        self.assertTrue(any("自爆できませんでした" in m for m in logs), logs)
        self.assertEqual(st._skip_time, 0.0, "自爆成功と誤判定しないこと")

    def test_the_old_switches_are_gone(self):
        self.assertFalse(hasattr(config, "SUICIDE_BACKGROUND"))
        self.assertFalse(hasattr(config, "SUICIDE_FOCUS_SETTLE_SEC"))


class TestSuicideIsolation(unittest.TestCase):
    """自爆はロックもフリーズも見ない。前面の窓を切り替えないので要らない"""

    def setUp(self):
        _single_attempt(self)
        self._reset()
        SharedState.set_suicide_key("^")

    def tearDown(self):
        self._reset()

    @staticmethod
    def _reset():
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.speed_freeze_reset()
        SharedState.round_freeze_reset()

    def _executor(self, hwnd=123, **state):
        cfg = WindowConfig(hwnd=hwnd, do_skip=True)
        st = WindowState(in_round=True, **state)
        logs = []
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append), st

    def _skip(self, ex, timeout=2.0):
        """別スレッドで回す。止まってしまったら False"""
        sent = []
        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda h, k, sec: sent.append(h) or True):
            t = threading.Thread(target=ex.do_skip, daemon=True)
            t.start()
            t.join(timeout)
        return (not t.is_alive()), sent

    # ── ロック ──────────────────────────────
    def test_it_does_not_wait_for_the_lock(self):
        """Begin やクリックがロックを握っている間でも自爆する"""
        ex, _st = self._executor()
        with SharedState._GLOBAL_ACTION_LOCK:
            finished, sent = self._skip(ex)

        self.assertTrue(finished, "ロックを待って止まった")
        self.assertEqual(sent, [123])

    def test_it_never_holds_the_lock(self):
        ex, _st = self._executor()
        held = []
        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda *a: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked()) or True):
            ex.do_skip()

        self.assertEqual(held, [False])

    # ── フリーズ ─────────────────────────────
    def test_another_windows_continue_freeze_does_not_stop_it(self):
        SharedState.continue_round_start()
        ex, _st = self._executor()

        finished, sent = self._skip(ex)

        self.assertTrue(finished)
        self.assertEqual(sent, [123])

    def test_another_windows_item_wait_does_not_stop_it(self):
        SharedState.EQUIP_WAIT_EVENT.clear()
        ex, _st = self._executor()

        finished, sent = self._skip(ex)

        self.assertTrue(finished)
        self.assertEqual(sent, [123])

    def test_speed_and_entry_freezes_do_not_stop_it(self):
        SharedState.SPEED_FREEZE_EVENT.clear()
        SharedState.ROUND_FREEZE_EVENT.clear()
        ex, _st = self._executor()

        finished, sent = self._skip(ex)

        self.assertTrue(finished)
        self.assertEqual(sent, [123])

    # ── 自窓の状態では止まる ─────────────────────
    def test_its_own_continue_round_stops_it(self):
        ex, _st = self._executor(is_continue_round=True)

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    def test_its_own_item_wait_stops_it(self):
        ex, _st = self._executor(waiting_for_equip=True)

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    def test_outside_a_round_it_does_nothing(self):
        ex, st = self._executor()
        st.in_round = False

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    def test_a_stopped_monitor_does_nothing(self):
        cfg = WindowConfig(hwnd=123, do_skip=True)
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(in_round=True),
                                           lambda: False, lambda _m: None)

        _finished, sent = self._skip(ex)

        self.assertEqual(sent, [])

    # ── 同時に ──────────────────────────────
    def test_two_windows_skip_at_the_same_time(self):
        a, _ = self._executor(hwnd=0xA)
        b, _ = self._executor(hwnd=0xB)
        inside = []
        peak = []
        gate = threading.Barrier(2, timeout=2.0)
        lock = threading.Lock()

        def hold(hwnd, key, sec):
            with lock:
                inside.append(hwnd)
                peak.append(len(inside))
            gate.wait()                 # 2つとも中に入るまで待つ
            with lock:
                inside.remove(hwnd)
            return True

        with patch.object(WindowOperator, "hold_key_background", side_effect=hold):
            threads = [threading.Thread(target=ex.do_skip, daemon=True)
                       for ex in (a, b)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(3.0)

        self.assertFalse(any(t.is_alive() for t in threads), "片方が待たされた")
        self.assertEqual(max(peak), 2, "2窓が同時に送っていること")


class TestHoldKeyBackgroundInParallel(unittest.TestCase):
    """並行に呼んでも、各呼び出しは自分の対象にだけアタッチし、必ず外す"""

    def test_each_call_attaches_only_to_its_own_window(self):
        targets = {0xA: 1001, 0xB: 1002}
        calls = []
        record = threading.Lock()
        gate = threading.Barrier(2, timeout=2.0)

        u = MagicMock()
        u.GetWindowThreadProcessId.side_effect = lambda h, _p: targets[h]
        u.IsIconic.return_value = 0
        u.GetKeyboardLayout.return_value = 0x04110411
        u.VkKeyScanExW.return_value = 0xDE
        u.MapVirtualKeyExW.return_value = 0x0D
        u.GetKeyboardState.return_value = 1

        def attach(me, target, on):
            with record:
                calls.append((me, target, on))
            return 1

        u.AttachThreadInput.side_effect = attach
        k = MagicMock()
        k.GetCurrentThreadId.side_effect = threading.get_ident

        results = {}

        def run(hwnd):
            results[hwnd] = WindowOperator.hold_key_background(hwnd, "^", 3.0)

        with patch.object(WindowOperator, "user32", u), \
             patch.object(WindowOperator, "kernel32", k), \
             patch.object(WindowOperator.time, "sleep",
                          side_effect=lambda _s: gate.wait()):
            threads = [threading.Thread(target=run, args=(h,)) for h in targets]
            for t in threads:
                t.start()
            for t in threads:
                t.join(3.0)

        self.assertEqual(results, {0xA: True, 0xB: True})
        attached = [(me, t) for me, t, on in calls if on]
        detached = [(me, t) for me, t, on in calls if not on]
        self.assertEqual(sorted(attached), sorted(detached), "必ず外すこと")
        self.assertEqual(sorted(t for _me, t in attached), [1001, 1002])
        self.assertEqual(len({me for me, _t in attached}), 2,
                         "呼び出したスレッドごとに別々にアタッチしていること")


class TestReleaseKeyBackground(unittest.TestCase):
    """押されたままかもしれない自爆キーを離す"""

    VK = 0xDE
    SCAN = 0x0D

    def _user32(self, pressed=True, **over):
        u = MagicMock()
        u.GetWindowThreadProcessId.return_value = 4321
        u.GetKeyboardLayout.return_value = 0x04110411
        u.VkKeyScanExW.return_value = self.VK
        u.MapVirtualKeyExW.return_value = self.SCAN
        u.AttachThreadInput.return_value = 1

        def get_state(ref):
            if pressed:
                ref._obj[self.VK] = 0x80
            return 1

        u.GetKeyboardState.side_effect = get_state
        for name, value in over.items():
            getattr(u, name).return_value = value
        return u

    def _release(self, u, key="^", hwnd=0x1234):
        k = MagicMock()
        k.GetCurrentThreadId.return_value = 99
        with patch.object(WindowOperator, "user32", u), \
             patch.object(WindowOperator, "kernel32", k):
            return WindowOperator.release_key_background(hwnd, key)

    def test_the_key_up_is_posted(self):
        u = self._user32()

        self.assertTrue(self._release(u))

        msg = u.PostMessageW.call_args.args
        self.assertEqual(msg[:3], (0x1234, WindowOperator.WM_KEYUP, self.VK))
        self.assertTrue(msg[3] & (1 << 31), "離上の lParam")

    def test_a_held_key_state_is_cleared(self):
        u = self._user32(pressed=True)
        written = []
        u.SetKeyboardState.side_effect = lambda ref: written.append(
            ref._obj[self.VK]) or 1

        self._release(u)

        self.assertEqual(written, [0])

    def test_an_unpressed_key_leaves_the_state_alone(self):
        """押していなくても無害"""
        u = self._user32(pressed=False)

        self.assertTrue(self._release(u))

        u.SetKeyboardState.assert_not_called()

    def test_it_detaches(self):
        u = self._user32()

        self._release(u)

        self.assertEqual([c.args[2] for c in u.AttachThreadInput.call_args_list],
                         [True, False])

    def test_a_minimized_window_still_gets_it(self):
        """離すだけなら窓を出す必要は無い"""
        u = self._user32(IsIconic=1)

        self.assertTrue(self._release(u))
        u.PostMessageW.assert_called_once()

    def test_an_unsendable_key_does_nothing(self):
        for over in (dict(VkKeyScanExW=-1), dict(VkKeyScanExW=(1 << 8) | self.VK),
                     dict(GetWindowThreadProcessId=0)):
            u = self._user32(**over)

            self.assertFalse(self._release(u), over)
            u.PostMessageW.assert_not_called()

    def test_a_failure_does_not_raise(self):
        u = self._user32()
        u.PostMessageW.side_effect = OSError("gone")

        self.assertFalse(self._release(u))
        self.assertEqual(u.AttachThreadInput.call_args.args[2], False,
                         "失敗してもデタッチすること")

    def test_the_real_dll_has_what_it_uses(self):
        """名前を間違えると AttributeError で黙って止まる（以前の実例あり）"""
        import ctypes as real_ctypes
        u32 = real_ctypes.WinDLL("user32")
        k32 = real_ctypes.WinDLL("kernel32")
        for name in ("GetWindowThreadProcessId", "GetKeyboardLayout",
                     "VkKeyScanExW", "MapVirtualKeyExW", "AttachThreadInput",
                     "GetKeyboardState", "SetKeyboardState", "PostMessageW"):
            self.assertTrue(hasattr(u32, name), name)
        self.assertTrue(hasattr(k32, "GetCurrentThreadId"))


class TestSuicideKeysReleasedByTheApp(unittest.TestCase):
    """停止・終了・起動のときに自爆キーを離す"""

    def setUp(self):
        SharedState.set_suicide_key("^")

    def _app(self, hwnds=(0xA, 0xB)):
        app = type("FakeApp", (), {})()
        app.order = []
        app.monitors = []
        for h in hwnds:
            m = MagicMock()
            m.cfg.hwnd = h
            m.stop.side_effect = lambda h=h: app.order.append(("stop", h))
            app.monitors.append(m)
        app._entry_stop = threading.Event()
        app.btn_start = MagicMock()
        app.btn_stop = MagicMock()
        app.lbl_win_warn = MagicMock()
        app.logs = []
        app._log = app.logs.append
        app._release_suicide_keys = \
            lambda hs: mainGUI.App._release_suicide_keys(app, hs)
        return app

    def _released(self, app):
        return patch.object(
            mainGUI.WindowOperator, "release_key_background",
            side_effect=lambda h, k: app.order.append(("release", h, k)) or True)

    def test_stopping_releases_every_watched_window(self):
        app = self._app()

        with self._released(app):
            mainGUI.App._stop(app)

        self.assertIn(("release", 0xA, "^"), app.order)
        self.assertIn(("release", 0xB, "^"), app.order)

    def test_it_releases_after_the_monitors_stop(self):
        """止める前に離すと、止まるまでの間にまた押されうる"""
        app = self._app()

        with self._released(app):
            mainGUI.App._stop(app)

        kinds = [e[0] for e in app.order]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "stop"),
                        min(i for i, k in enumerate(kinds) if k == "release"))

    def test_closing_releases_before_destroying(self):
        app = self._app()
        app._save_settings_now = lambda: None
        app._stop = lambda: mainGUI.App._stop(app)
        app.destroy = lambda: app.order.append(("destroy",))

        with self._released(app):
            mainGUI.App._on_close(app)

        kinds = [e[0] for e in app.order]
        self.assertIn("release", kinds)
        self.assertLess(kinds.index("release"), kinds.index("destroy"))

    def test_startup_releases_every_vrchat_window(self):
        """前回このツールが長押しの最中に落ちていた場合の回収"""
        app = self._app(hwnds=())
        app.v_win_count = MagicMock()
        app.tabs = [object(), object()]
        app._rebuild_tabs = MagicMock()
        app._assign_windows_and_logs = MagicMock()

        with self._released(app), \
             patch.object(mainGUI.VRChatDiscovery,
                          "get_vrchat_windows_by_start_time",
                          return_value=[(0x11, 1.0), (0x22, 2.0)]):
            mainGUI.App._auto_detect_windows(app)

        self.assertEqual([e[1] for e in app.order if e[0] == "release"],
                         [0x11, 0x22])

    def test_a_failure_does_not_stop_the_stop(self):
        app = self._app()

        with patch.object(mainGUI.WindowOperator, "release_key_background",
                          side_effect=OSError("gone")):
            mainGUI.App._stop(app)          # 落ちないこと

        self.assertFalse(app._running)
        self.assertTrue(any("離せませんでした" in m for m in app.logs), app.logs)

    def test_an_unset_window_is_skipped(self):
        app = self._app(hwnds=(0, 0xA))

        with self._released(app):
            mainGUI.App._stop(app)

        self.assertEqual([e[1] for e in app.order if e[0] == "release"], [0xA])


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
        _single_attempt(self)
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

    def test_do_skip_sends_the_current_key_in_the_background(self):
        cfg = WindowConfig(hwnd=123)
        st = WindowState(in_round=True)
        SharedState.set_suicide_key("x")
        executor = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _msg: None)

        with patch.object(WindowOperator, "hold_key_background",
                          return_value=True) as mock_bg, \
             patch.object(WindowOperator, "focus_window") as mock_focus, \
             patch.object(ActionExecutor.time, "sleep") as mock_sleep:
            executor.do_skip()

        mock_bg.assert_called_once_with(123, "x", config.SUICIDE_HOLD_SEC)
        mock_focus.assert_not_called()
        mock_sleep.assert_not_called()

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
        _single_attempt(self)
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
        _single_attempt(self)
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
        for name in ("_resolve_tab_ports", "_resolve_windows"):
            setattr(app, name, getattr(mainGUI.App, name).__get__(app))
        app._apply_obs_settings = lambda: None
        app._assign_source = mainGUI.App._assign_source
        return app

    def test_start_is_not_blocked_without_tnl(self):
        """警告で止めず、そのまま窓の準備へ進む"""
        app = self._app({})

        with patch.object(mainGUI.messagebox, "showwarning") as mock_warn, \
             patch.object(mainGUI.messagebox, "showerror") as mock_error, \
             patch.object(VRChatDiscovery, "find_latest_logs", return_value=[]), \
             patch.object(VRChatDiscovery, "get_vrchat_windows_by_start_time",
                          return_value=[]), \
             patch.object(mainGUI.OSCClient, "udp_ports_by_pid", return_value=None):
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


class TestRoundFreezeVoice(unittest.TestCase):
    """突入で全窓停止を選んだラウンドに入ったら、そのラウンドの音声を鳴らす"""

    ROUND_LINE = ("This round is taking place at Facility (12) "
                  "and the round type is %s")
    VOICES = {"Fog": "fog.mp3", "Unbound": "unbound.mp3", "Midnight": "midnight.mp3",
              "Alternate": "alternate.mp3", "Ghost": "ghost.mp3"}

    def setUp(self):
        self._reset()
        self.addCleanup(self._reset)

    @staticmethod
    def _reset():
        SharedState.round_freeze_reset()
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_freeze_rounds(())

    def _monitor(self, rounds, **voices):
        SharedState.set_freeze_rounds(rounds)
        cfg = WindowConfig(voice_fog="fog.mp3", voice_unbound="unbound.mp3",
                           voice_midnight="midnight.mp3",
                           voice_alternate="alternate.mp3", voice_ghost="ghost.mp3",
                           voice_punish="punish.mp3", voice_8pages="8pages.mp3")
        for name, value in voices.items():
            setattr(cfg, name, value)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        return monitor

    def _enter(self, monitor, round_type):
        with patch.object(ConnectDB, "send_ToNRoundStatistics"), \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as played:
            monitor._process(self.ROUND_LINE % round_type)
        return [c.args[0] for c in played.call_args_list]

    def test_each_chosen_round_plays_its_own_voice(self):
        for round_type, voice in self.VOICES.items():
            self._reset()
            monitor = self._monitor([round_type])

            self.assertEqual(self._enter(monitor, round_type), [voice], round_type)

    def test_an_unchosen_round_is_silent(self):
        monitor = self._monitor(["Unbound"])

        self.assertEqual(self._enter(monitor, "Ghost"), [])

    def test_punished_and_eight_pages_are_not_announced(self):
        """依頼の5つに入っていない（その音声は速度検知用）"""
        for round_type in ("Punished", "8 Pages"):
            self._reset()
            monitor = self._monitor([round_type])

            self.assertEqual(self._enter(monitor, round_type), [], round_type)
            self.assertTrue(monitor.st.round_freeze_held, "フリーズ自体は張る")

    def test_hands_free_is_silent(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(["Unbound"])

        self.assertEqual(self._enter(monitor, "Unbound"), [])

    def test_an_empty_path_is_silent(self):
        monitor = self._monitor(["Midnight"], voice_midnight="")

        self.assertEqual(self._enter(monitor, "Midnight"), [])

    # ── 霧は二重に鳴らさない ───────────────────
    def test_fog_plays_once_even_with_the_entry_announcement_on(self):
        monitor = self._monitor(["Fog"])

        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True):
            played = self._enter(monitor, "Fog")

        self.assertEqual(played, ["fog.mp3"])

    def test_fog_unchosen_stays_silent_by_default(self):
        monitor = self._monitor([])

        self.assertEqual(self._enter(monitor, "Fog"), [])

    def test_fog_unchosen_still_uses_the_entry_announcement(self):
        monitor = self._monitor([])

        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True):
            self.assertEqual(self._enter(monitor, "Fog"), ["fog.mp3"])

    # ── 設定 ─────────────────────────────
    def test_the_voice_files_are_bundled(self):
        for path in (config.VOICE_UNBOUND, config.VOICE_MIDNIGHT,
                     config.VOICE_ALTERNATE, config.VOICE_GHOST, config.VOICE_FOG):
            self.assertTrue(Path(path).exists(), path)

    def test_the_gui_has_a_row_and_hands_it_to_the_monitor(self):
        gui = Path("mainGUI.py").read_text(encoding="utf-8")
        for name in ("unbound", "midnight", "alternate", "ghost"):
            self.assertIn(f"self.v_voice_{name}", gui, name)
            self.assertIn(f"cfg.voice_{name}", gui, name)
            self.assertIn(f"config.VOICE_{name.upper()}", gui, name)

    def test_the_window_config_defaults_to_silence(self):
        cfg = WindowConfig()
        for name in ("unbound", "midnight", "alternate", "ghost"):
            self.assertEqual(getattr(cfg, f"voice_{name}"), "", name)


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
        _single_attempt(self)
        cfg = WindowConfig(hwnd=123, do_skip=True)
        st = WindowState(in_round=True, round_freeze_held=True)
        SharedState.ROUND_FREEZE_EVENT.clear()
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
        held = []

        with patch.object(WindowOperator, "hold_key_background",
                          side_effect=lambda h, k, sec: held.append(k) or True):
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

        self.assertTrue(any("Terror set:" in m for m in logs), logs)

    def test_logged_under_instance_restriction(self):
        """publicなど操作しないインスタンスでも名前は残す"""
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC)

        logs = self._on_killers(monitor)

        self.assertTrue(any("Terror set:" in m for m in logs), logs)
        self.assertTrue(any("インスタンス制限" in m for m in logs), logs)

    def test_logged_when_hoshiimo_skip_decides(self):
        monitor = self._monitor(instance_type=config.INSTANCE_HOSHIIMO)

        logs = self._on_killers(monitor)

        self.assertTrue(any("Terror set:" in m for m in logs), logs)

    def test_logged_in_hands_free(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(do_skip=True)

        logs = self._on_killers(monitor)

        self.assertTrue(any("Terror set:" in m for m in logs), logs)

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

        self.assertTrue(any("Terror revealed:" in m for m in logs), logs)

    def test_name_comes_before_the_decision(self):
        logs = self._on_killers(self._monitor())
        names = [i for i, m in enumerate(logs) if "Terror set:" in m]
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
                     "2026.09.05 14:29:35 Debug      -  The Gigabytes have come. now"):
            self.assertIsNone(LogParser.parse(line), line)

        # Enrage 行は Enrage として拾う。Gigabytes の出現行ではない
        enrage = LogParser.parse("2026.09.05 14:29:35 Debug      -  "
                                 "BLUE GIGABYTEtriggered an Enrage State!")
        self.assertEqual(enrage.kind, LogParser.EVENT_ENRAGE)
        self.assertEqual(enrage.player_name, "BLUE GIGABYTE")

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
        monitor.st.round_type = "Classic"     # Gigabytes は Classic でしか起きない
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


class TestVerifiedStrafe(unittest.TestCase):
    """横移動は本物の Verified（自分の Begin が通った）で、private のときだけ。

    Verified はインマスでなければ来ないので、これで自動的に「インマスのときだけ」
    になる。STRING_DOWNLOAD で動くと Begin を押す前に移動してしまう。
    """

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9000), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.round_end_seen = True
        return monitor

    def _started(self, monitor, line="Verified"):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process(line)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_a_real_verified_strafes_in_private(self):
        monitor = self._monitor()

        started = self._started(monitor)

        self.assertIn("do_speed_strafe", started)
        self.assertTrue(monitor.st.speed_strafe_done)

    def test_other_instances_do_not_strafe(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO,
                      config.INSTANCE_PUBLIC):
            monitor = self._monitor(itype)

            started = self._started(monitor)

            self.assertNotIn("do_speed_strafe", started, itype)
            self.assertTrue(monitor.st.begin_done, f"{itype}: Verified 自体は採用")

    def test_a_periodic_verified_does_not_strafe(self):
        monitor = self._monitor()
        monitor.st.periodic_last = 1000.0
        monitor.st.periodic_period = 300.0

        with patch.object(LogMonitor.time, "time", return_value=1302.0):
            started = self._started(monitor)

        self.assertNotIn("do_speed_strafe", started)
        self.assertFalse(monitor.st.speed_strafe_done)

    def test_a_verified_before_the_round_end_does_not_strafe(self):
        monitor = self._monitor()
        monitor.st.round_end_seen = False

        started = self._started(monitor)

        self.assertNotIn("do_speed_strafe", started)

    def test_it_strafes_once_per_round(self):
        monitor = self._monitor()

        first = self._started(monitor)
        second = self._started(monitor)

        self.assertIn("do_speed_strafe", first)
        self.assertNotIn("do_speed_strafe", second)

    def test_the_next_round_may_strafe_again(self):
        monitor = self._monitor()
        self._started(monitor)

        with patch.object(ConnectDB, "send_ToNRoundStatistics"):
            self._started(monitor, "This round is taking place at Facility (12) "
                                   "and the round type is Classic")

        self.assertFalse(monitor.st.speed_strafe_done)

    def test_the_toggle_off_does_not_strafe(self):
        SharedState.set_speed_detect(False)
        monitor = self._monitor()

        self.assertNotIn("do_speed_strafe", self._started(monitor))

    def test_the_strafe_comes_after_detection_has_started(self):
        """Verified は Round End の後に来るので、横移動は必ず検知の最中に入る"""
        monitor = self._monitor()
        monitor.st.round_end_seen = False

        end = self._started(monitor, "Verified Round End")
        verified = self._started(monitor)

        self.assertEqual(end, ["do_speed_detect"])
        self.assertEqual(verified, ["do_speed_strafe"])

    def test_joining_advances_the_instance_counter(self):
        monitor = self._monitor()
        before = monitor.st.instance_seq

        self._started(monitor, "[Behaviour] Joining wrld_1234:5678~private(usr_me)")

        self.assertEqual(monitor.st.instance_seq, before + 1)


class TestSpeedDetectNeedsOsc(unittest.TestCase):
    """速度を受け取れるのは OSC の窓だけ。

    キー操作の窓は Verified Round End を待ってから Begin 前に歩くので、検知の
    最中に歩きが入るのではと心配された。そもそも受信を開かないので検知しない。
    """

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def test_a_key_window_opens_no_receiver(self):
        cfg = WindowConfig(hwnd=123, osc_port=0)
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(), lambda: True,
                                           lambda _m: None)

        with patch.object(ActionExecutor.OSCReceiver, "VelocityReceiver") as made:
            self.assertFalse(ex.start_velocity_receiver())

        made.assert_not_called()
        self.assertIsNone(ex._receiver)

    def test_a_key_window_does_not_sample_at_all(self):
        cfg = WindowConfig(hwnd=123, osc_port=0)
        ex = ActionExecutor.ActionExecutor(cfg, WindowState(), lambda: True,
                                           lambda _m: None)
        ex.start_velocity_receiver()

        with patch.object(ex, "_sample_speed") as sample, \
             patch.object(ActionExecutor.time, "sleep") as nap:
            ex.do_speed_detect()

        sample.assert_not_called()
        nap.assert_not_called()

    def test_ordinary_walking_values_are_not_a_round_kind(self):
        """判定は定数との一致（±0.01）だけ。途中の値では決めない"""
        for speed in (0.0, 2.0, 3.99 - 0.02, 5.0, 6.45, 6.55, 7.0):
            self.assertEqual(ActionExecutor.classify_speed(speed), "", speed)


class TestEightPagesFirstSlot(unittest.TestCase):
    """8 Pages の続行判定は1枠目（A）だけ。terror_ids は [A, B] のまま"""

    PAGES = "8 Pages/8ページ"

    def _decide(self, keep, ids, round_type="8 Pages"):
        return RoundDecision.decide_killers(keep, list(ids), round_type, 0, True)

    def test_only_the_second_slot_listed_does_not_continue(self):
        self.assertFalse(self._decide({self.PAGES: {23}}, [49, 23]).is_continue_round)

    def test_the_first_slot_listed_continues(self):
        self.assertTrue(self._decide({self.PAGES: {49}}, [49, 23]).is_continue_round)

    def test_other_two_slot_rounds_still_use_both(self):
        dt = "Double Trouble/ダブルトラブル"
        self.assertTrue(self._decide({dt: {23}}, [49, 23],
                                     "Double Trouble").is_continue_round)

    def test_the_monitor_keeps_both_ids(self):
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, {self.PAGES: {23}}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = "8 Pages"
        logs = []
        monitor.logger = logs.append

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics") as sent:
            monitor._process("Killers have been set - 49 23 0 // Round type is 8 Pages")

        started = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]
        self.assertEqual(monitor.st.terror_ids, [49, 23], "ログ・統計には両方")
        self.assertEqual(sent.call_args.args[1], [49, 23])
        self.assertIn("do_skip", started, "B だけがリストにあっても続行しない")
        both = LogMonitor.format_terror_ids([49, 23])
        self.assertTrue(any(both in m for m in logs), logs)

    def test_the_alternate_eight_pages_is_already_one_slot(self):
        self.assertEqual(MatchTNL.parse_terror_ids("5", "0", "0", "8 Pages (Alternate)"),
                         [5])


class TestSpeedDetectRunsUntilTheRound(unittest.TestCase):
    """速度検知は時間ではなくラウンド突入で止める"""

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _run(self, on_tick, advance=1.0):
        cfg = WindowConfig(hwnd=123, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)
        # 値を返さない受信（判定では止まらない）
        ex._receiver = type("R", (), dict(stable_value=None, stable_for=0.0,
                                          grounded=True, ever_received=True,
                                          alive=True))()
        clock = {"t": 1000.0}

        def sleep(_sec):
            clock["t"] += advance
            on_tick(st, clock["t"] - 1000.0)

        with patch.object(ActionExecutor.time, "time",
                          side_effect=lambda: clock["t"]), \
             patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
            ex.do_speed_detect()
        return clock["t"] - 1000.0

    def test_it_stops_when_the_round_starts(self):
        def tick(st, elapsed):
            if elapsed >= 12:
                st.in_round = True

        self.assertEqual(self._run(tick), 12)

    def test_it_keeps_watching_past_twenty_seconds(self):
        """Begin から開始まで20秒を超えることがある（実測1.5%）"""
        def tick(st, elapsed):
            if elapsed >= 45:
                st.in_round = True

        self.assertEqual(self._run(tick), 45)

    def test_the_safety_limit_stops_it(self):
        """ラウンドが来ないまま回り続けない"""
        elapsed = self._run(lambda st, e: None)

        self.assertGreaterEqual(elapsed, config.SPEED_PROBE_MAX_SEC)
        self.assertLessEqual(elapsed, config.SPEED_PROBE_MAX_SEC + 1)

    def test_the_limit_is_longer_than_the_longest_wait_seen(self):
        """実ログの最大は 119 秒"""
        self.assertGreater(config.SPEED_PROBE_MAX_SEC, 119)

    def test_changing_instance_stops_it(self):
        def tick(st, elapsed):
            if elapsed >= 5:
                st.instance_seq += 1

        self.assertEqual(self._run(tick), 5)

    def test_the_old_timeout_is_gone(self):
        self.assertFalse(hasattr(config, "SPEED_PROBE_TIMEOUT_SEC"))


class TestStringDownloadTrigger(unittest.TestCase):
    """速度検知の起点は Verified Round End。ラウンドデータの取得は使わない

    String Download は Begin 以外でも定期的に出るので、区間の最初の1件という
    前提が崩れる。Verified Round End はラウンドごとに1回で、誰が Begin しても出る。
    """

    END = "Verified Round End"

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

    def test_a_download_starts_nothing(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO):
            monitor = self._monitor(itype)

            self.assertEqual(self._started(monitor), [], itype)
            self.assertFalse(monitor.st.speed_probe_done, itype)

    def test_the_round_end_starts_detection_in_every_instance(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_HOSHIIMO,
                      config.INSTANCE_YAKIIMO, config.INSTANCE_PUBLIC):
            monitor = self._monitor(itype)
            monitor.st.round_end_seen = False

            started = self._started(monitor, self.END)

            self.assertIn("do_speed_detect", started, itype)
            self.assertNotIn("do_speed_strafe", started,
                             f"{itype}: 横移動は Verified で")
            self.assertTrue(monitor.st.speed_probe_done, itype)

    def test_download_before_round_end_starts_nothing(self):
        monitor = self._monitor()
        monitor.st.round_end_seen = False

        self.assertEqual(self._started(monitor), [])
        self.assertFalse(monitor.st.speed_probe_done)

    def test_it_starts_once_per_round(self):
        monitor = self._monitor()

        first = self._started(monitor, self.END)
        second = self._started(monitor, self.END)
        downloads = self._started(monitor)

        self.assertIn("do_speed_detect", first)
        self.assertNotIn("do_speed_detect", second)
        self.assertEqual(downloads, [])

    def test_neither_url_starts_it(self):
        for line in (self.DL, self.DL_ALT):
            self.assertEqual(self._started(self._monitor(), line), [], line)

    def test_toggle_off_starts_nothing(self):
        SharedState.set_speed_detect(False)

        self.assertNotIn("do_speed_detect", self._started(self._monitor(), self.END))

    def test_round_start_allows_the_next_round(self):
        monitor = self._monitor()
        self._started(monitor, self.END)
        self.assertTrue(monitor.st.speed_probe_done)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is Classic")

        self.assertFalse(monitor.st.speed_probe_done)

    def test_verified_does_not_start_the_detection(self):
        """判定の起点はラウンドデータの取得。Verified は横移動だけ"""
        monitor = self._monitor()

        started = self._started(monitor, "Verified")

        self.assertNotIn("do_speed_detect", started)
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

    def _monitor(self, *, skip_rounds=("Classic",), do_skip=True,
                 cancel_afk=True, keep_on=None,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=do_skip, cancel_afk=cancel_afk,
                           voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._running = True
        return monitor

    def _killers(self, monitor, ids=(99,), round_type=None, settle=True):
        """settle: 置き換え待ちに入ったら、合図が来ないまま0.3秒経ったものとして
        その場で判定まで進める（Classic の1体構成は Gigabytes 待ちに入りうる）"""
        round_type = round_type or monitor.st.round_type
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type, revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]
            if settle and "_delayed_decision" in started:
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += [c.kwargs["target"].__func__.__name__
                            for c in mock_thread.call_args_list
                            if "target" in c.kwargs]
        return started

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
            monitor._delayed_decision("Classic", 0.0, monitor.st.round_seq)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── Variant例外（常に効く。切り替えは廃止） ───────────
    def test_the_variant_exemption_falls_through(self):
        """Variantなら自爆指定より優先して通常判定へ"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.ATRACHED_ID}})
        monitor.st.terror_ids = [config.ATRACHED_ID]

        started = self._run_delayed(monitor)

        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)

    def test_the_exemption_covers_all_four_variants(self):
        for tid in (config.HUNGRY_HOME_INVADER_ID, config.ATRACHED_ID,
                    config.BLOODTHIRSTY_CREATURE_ID, config.GIGABYTES_ID):
            monitor = self._monitor(keep_on={self.CLASSIC_KEY: {tid}})
            monitor.st.terror_ids = [tid]

            started = self._run_delayed(monitor)

            self.assertNotIn("do_skip", started, tid)
            self.assertTrue(monitor.st.is_continue_round, tid)

    def test_the_exemption_still_skips_a_plain_terror(self):
        # 待ちを止めるのに st.gigabytes を立ててはいけない。_on_killers が
        # ids を [314] に差し替えるので、テラーIDごと変わってしまう
        monitor = self._monitor()

        with patch.object(LogMonitor.LogMonitor,
                          "_replacement_changes_decision", return_value=False):
            started = self._killers(monitor)

        self.assertIn("do_skip", started)
        self.assertEqual(monitor.st.terror_ids, [99], "IDが差し替わっていないこと")

    def test_a_variant_is_never_skipped_by_the_designation(self):
        """以前は切り替え次第で自爆していた。いまは常にリストで判定する"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.ATRACHED_ID}})
        monitor.st.atrached_variant = True

        started = self._killers(monitor, [config.ATRACHED_ID])

        self.assertNotIn("do_skip", started)
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_config_has_no_switch_any_more(self):
        self.assertFalse(hasattr(WindowConfig(), "skip_variant_exempt"))

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

    # ── 置き換え待ち ───────────────────────
    def test_a_designated_round_waits_when_a_variant_would_change_it(self):
        """Gigabytes が来ればリスト判定で続行になる。来るまで待つ"""
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.GIGABYTES_ID}})

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["_delayed_decision"])

    def test_no_wait_when_neither_side_continues(self):
        """置き換わってもリストに無ければ、どちらにしても自爆"""
        monitor = self._monitor()

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["do_skip"])

    def test_an_undesignated_round_waits_too(self):
        """自爆指定が無くても待つ（以前は指定があるときしか待たなかった）"""
        monitor = self._monitor(skip_rounds=("Bloodbath",),
                                keep_on={self.CLASSIC_KEY: {99}})

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["_delayed_decision"])

    def test_nothing_selected_and_nothing_listed_does_not_wait(self):
        monitor = self._monitor(skip_rounds=())

        started = self._killers(monitor, settle=False)

        self.assertEqual(started, ["do_skip"])

    def test_the_wait_ends_in_a_skip_without_a_variant(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [99]

        self.assertIn("do_skip", self._run_delayed(monitor))

    def test_the_wait_falls_through_when_a_variant_arrives(self):
        monitor = self._monitor(keep_on={self.CLASSIC_KEY: {config.GIGABYTES_ID}})
        monitor.st.terror_ids = [config.GIGABYTES_ID]
        monitor.st.gigabytes = True     # _run_delayed は _on_killers を通らないので
                                        # ここでは ids の差し替えは起きない

        started = self._run_delayed(monitor)

        self.assertEqual(started, [])
        self.assertTrue(monitor.st.is_continue_round)

    def test_the_wait_aborts_when_the_round_changed(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [99]
        monitor.st.round_seq = 5

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._delayed_decision("Classic", 0.0, 4)

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


def _real_keyboard():
    """本物の keyboard モジュール。UnitTest 冒頭で MagicMock に差し替えているので、
    キー名の検証だけは実物に確かめさせる（モックでは何でも通ってしまう）"""
    mocked = sys.modules.get("keyboard")
    try:
        del sys.modules["keyboard"]
        import keyboard as real
        return real
    except Exception:
        return None
    finally:
        sys.modules["keyboard"] = mocked


class TestHotKey(unittest.TestCase):
    """緊急停止キーの検証・表記・捕捉"""

    @classmethod
    def setUpClass(cls):
        cls.real = _real_keyboard()

    def setUp(self):
        if self.real is None:
            self.skipTest("keyboard が入っていない")
        self._kb = patch.object(HotKey, "keyboard", self.real)
        self._kb.start()

    def tearDown(self):
        self._kb.stop()

    def test_real_key_names_are_accepted(self):
        for name in ("p", "f9", "esc", "ctrl+p", "scroll lock", "ctrl+shift+f12"):
            self.assertTrue(HotKey.is_valid(name), name)

    def test_junk_is_rejected(self):
        for name in ("zzz", "", "   ", None, 123, object()):
            self.assertFalse(HotKey.is_valid(name), repr(name))

    def test_without_keyboard_nothing_is_valid(self):
        with patch.object(HotKey, "keyboard", None):
            self.assertFalse(HotKey.is_valid("p"))       # 例外を投げないこと

    def test_the_display_capitalises_each_part(self):
        self.assertEqual(HotKey.display("p"), "P")
        self.assertEqual(HotKey.display("ctrl+p"), "Ctrl+P")
        self.assertEqual(HotKey.display("ctrl+shift+f12"), "Ctrl+Shift+F12")
        self.assertEqual(HotKey.display("scroll lock"), "Scroll Lock")

    def test_the_display_falls_back_for_junk(self):
        for name in ("", "   ", None, 123):
            self.assertEqual(HotKey.display(name),
                             HotKey.display(config.EMERGENCY_STOP_KEY), repr(name))

    def test_capture_returns_what_was_pressed(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        with patch.object(HotKey.keyboard, "read_hotkey", return_value="f9"):
            self.assertEqual(HotKey.capture(2.0), "f9")

    def test_capture_never_suppresses(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        """押したキーがVRChatや他のアプリに届かなくなる"""
        with patch.object(HotKey.keyboard, "read_hotkey",
                          return_value="p") as mock_read:
            HotKey.capture(2.0)

        self.assertEqual(mock_read.call_args.kwargs.get("suppress"), False)

    def test_capture_gives_up_on_a_timeout(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        started = threading.Event()

        def never(**_kw):
            started.set()
            time.sleep(5)

        with patch.object(HotKey.keyboard, "read_hotkey", never):
            self.assertIsNone(HotKey.capture(0.2))
        self.assertTrue(started.wait(2), "読み取りは始まっていること")

    def test_capture_swallows_errors(self):
        self._kb.stop()
        self.addCleanup(self._kb.start)
        with patch.object(HotKey.keyboard, "read_hotkey",
                          side_effect=RuntimeError("boom")):
            self.assertIsNone(HotKey.capture(2.0))

    def test_capture_without_keyboard_is_none(self):
        with patch.object(HotKey, "keyboard", None):
            self.assertIsNone(HotKey.capture(2.0))


class TestEmergencyKeyGui(unittest.TestCase):
    """GUI 側。App を1つ立てて確かめる"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        self._kb = patch.object(HotKey, "keyboard", real)
        self._kb.start()
        self.addCleanup(self._kb.stop)
        self.app.v_emergency_key.set(config.EMERGENCY_STOP_KEY)
        self.app._capturing_key = False
        self.app._emergency_stop_key_pressed = False
        self.app.logs = []
        self._log = patch.object(mainGUI.App, "_log",
                                 lambda _s, m: self.app.logs.append(m))
        self._log.start()

    def tearDown(self):
        self._log.stop()
        self.app._capturing_key = False
        self.app.v_emergency_key.set(config.EMERGENCY_STOP_KEY)

    def test_the_default_is_p(self):
        self.assertEqual(self.app.v_emergency_key.get(), "p")

    def test_a_valid_key_is_adopted(self):
        self.app._finish_capture_key("f9")

        self.assertEqual(self.app.v_emergency_key.get(), "f9")
        self.assertIn("F9", self.app.lbl_emergency.cget("text"))

    def test_an_invalid_key_falls_back_to_the_default(self):
        """打ち間違いを抱えたままだと緊急停止が黙って効かなくなる"""
        self.app.v_emergency_key.set("f9")

        self.app._finish_capture_key("zzz")

        self.assertEqual(self.app.v_emergency_key.get(), "p")
        self.assertTrue(any("使えないキー" in m for m in self.app.logs),
                        self.app.logs)

    def test_a_timeout_leaves_the_key_alone(self):
        self.app.v_emergency_key.set("f9")

        self.app._finish_capture_key(None)

        self.assertEqual(self.app.v_emergency_key.get(), "f9")
        self.assertTrue(any("取れませんでした" in m for m in self.app.logs))

    def test_the_poll_is_paused_while_capturing(self):
        """設定しようとしたキーで停止がかかると困る"""
        self.app._capturing_key = True

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mainGUI.App._poll_emergency_stop_key(self.app)

        mock_keyboard.is_pressed.assert_not_called()

    def test_the_poll_resumes_after_capturing(self):
        self.app._capturing_key = True
        self.app._finish_capture_key("f9")

        self.assertFalse(self.app._capturing_key)
        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mock_keyboard.is_pressed.return_value = False
            mainGUI.App._poll_emergency_stop_key(self.app)

        mock_keyboard.is_pressed.assert_called_once_with("f9")

    def test_capturing_runs_off_the_gui_thread(self):
        """read_hotkey は待つので、GUIスレッドで呼ぶと固まる"""
        seen = {}

        def slow(_timeout):
            seen["thread"] = threading.current_thread()
            return "f9"

        with patch.object(HotKey, "capture", slow), \
             patch.object(self.app, "after") as mock_after:
            self.app._begin_capture_key()
            for _ in range(50):
                if "thread" in seen:
                    break
                time.sleep(0.02)

        self.assertIn("thread", seen, "捕捉が始まっていること")
        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertTrue(mock_after.called, "結果はGUIスレッドへ戻すこと")
        self.app._capturing_key = False

    def test_the_configured_key_stops_the_macro(self):
        self.app.v_emergency_key.set("f9")
        self.app._emergency_stop_key_pressed = False

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after") as mock_after:
            mock_keyboard.is_pressed.return_value = True
            mainGUI.App._poll_emergency_stop_key(self.app)

        mock_keyboard.is_pressed.assert_called_once_with("f9")
        self.assertIn(self.app._stop, [c.args[1] for c in mock_after.call_args_list
                                       if len(c.args) > 1])

    def test_the_default_key_does_not_stop_once_changed(self):
        self.app.v_emergency_key.set("f9")

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mock_keyboard.is_pressed.return_value = False
            mainGUI.App._poll_emergency_stop_key(self.app)

        mock_keyboard.is_pressed.assert_called_once_with("f9")
        self.assertNotIn("p", [c.args[0] for c in
                               mock_keyboard.is_pressed.call_args_list])

    def test_a_broken_key_at_poll_time_falls_back(self):
        """is_pressed が投げたら握り潰さずに既定値へ倒す"""
        self.app.v_emergency_key.set("zzz")

        with patch.object(mainGUI, "keyboard") as mock_keyboard, \
             patch.object(self.app, "after"):
            mock_keyboard.is_pressed.side_effect = ValueError("bad")
            mainGUI.App._poll_emergency_stop_key(self.app)

        self.assertEqual(self.app.v_emergency_key.get(), "p")
        self.assertTrue(any("使えないキー" in m for m in self.app.logs),
                        self.app.logs)

    def test_the_label_follows_the_key(self):
        self.app.v_emergency_key.set("ctrl+p")

        self.app._refresh_emergency_key_label()

        self.assertIn("Ctrl+P", self.app.lbl_emergency.cget("text"))

    def test_no_hardcoded_p_key_remains(self):
        source = Path("mainGUI.py").read_text(encoding="utf-8")

        self.assertNotIn("Pキー", source)
        self.assertNotIn("EMERGENCY_STOP_KEY.upper()", source)

    def test_a_missing_keyboard_module_does_not_break_startup(self):
        with patch.object(mainGUI, "keyboard", None):
            mainGUI.App._start_emergency_stop_polling(self.app)   # 落ちないこと


class TestEmergencyKeySettings(unittest.TestCase):
    """保存と復元。壊れた値で緊急停止を失わないこと"""

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def _app(self, key="p"):
        app = type("FakeApp", (), {})()
        app.v_emergency_key = TestEmergencyKeySettings.FakeVar(key)
        app._refresh_emergency_key_label = lambda: None
        return app

    def _load(self, app, data):
        """_load_saved_settings のうち緊急停止キーの復元部分だけを回す"""
        key = data.get("emergency_stop_key", config.EMERGENCY_STOP_KEY)
        if not HotKey.is_valid(key):
            key = config.EMERGENCY_STOP_KEY
        app.v_emergency_key.set(key)
        app._refresh_emergency_key_label()

    def test_the_key_is_saved(self):
        app = self._app("f9")
        app.tabs = []
        app.tool_rows = []
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_instance_link",
                     "v_freeze_8pages", "v_freeze_punish"):
            setattr(app, name, TestEmergencyKeySettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestEmergencyKeySettings.FakeVar(""))
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["emergency_stop_key"], "f9")

    def test_a_saved_key_is_restored(self):
        app = self._app()

        self._load(app, {"emergency_stop_key": "ctrl+p"})

        self.assertEqual(app.v_emergency_key.get(), "ctrl+p")

    def test_a_broken_saved_key_falls_back(self):
        real = _real_keyboard()
        if real is None:
            self.skipTest("keyboard が入っていない")
        self._kb = patch.object(HotKey, "keyboard", real)
        self._kb.start()
        self.addCleanup(self._kb.stop)
        app = self._app()

        self._load(app, {"emergency_stop_key": "zzz"})

        self.assertEqual(app.v_emergency_key.get(), "p")

    def test_a_legacy_file_without_the_key_is_fine(self):
        app = self._app()

        self._load(app, {"tnl_path": "C:/list/my.tnl"})

        self.assertEqual(app.v_emergency_key.get(), "p")


class TestToolLauncher(unittest.TestCase):
    """登録した exe を起動する（GUI 不要の部分）"""

    EXE = r"D:\tools\ToN_ListTool.exe"

    def test_the_label_is_the_file_stem(self):
        self.assertEqual(ToolLauncher.button_label(self.EXE), "ToN_ListTool")

    def test_an_empty_path_has_no_label(self):
        for value in ("", "   ", None):
            self.assertEqual(ToolLauncher.button_label(value), "", repr(value))

    def test_a_odd_path_does_not_raise(self):
        for value in (123, object(), "::::"):
            ToolLauncher.button_label(value)   # 例外を投げないこと

    def test_is_running_asks_by_file_name(self):
        with patch.object(ProcessCheck, "is_process_running",
                          return_value=True) as mock_running:
            self.assertTrue(ToolLauncher.is_running(self.EXE))

        mock_running.assert_called_once_with("ToN_ListTool.exe")

    def test_an_empty_path_is_not_running(self):
        with patch.object(ProcessCheck, "is_process_running") as mock_running:
            self.assertFalse(ToolLauncher.is_running(""))

        mock_running.assert_not_called()

    def test_launch_starts_it_in_its_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "ToN_ListTool.exe"
            exe.write_bytes(b"")

            with patch.object(ToolLauncher.subprocess, "Popen") as mock_popen:
                ToolLauncher.launch(str(exe))

            mock_popen.assert_called_once_with([str(exe)], cwd=str(exe.parent))

    def test_launch_does_not_hide_the_window(self):
        """GUIアプリなので CREATE_NO_WINDOW は付けない"""
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "app.exe"
            exe.write_bytes(b"")

            with patch.object(ToolLauncher.subprocess, "Popen") as mock_popen:
                ToolLauncher.launch(str(exe))

            self.assertNotIn("creationflags", mock_popen.call_args.kwargs)

    def test_an_empty_path_raises(self):
        with self.assertRaises(ValueError):
            ToolLauncher.launch("   ")

    def test_a_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            ToolLauncher.launch(r"D:\nope\missing.exe")


class TestToolLauncherRows(unittest.TestCase):
    """GUI 側の行の扱い。App を1つ立てて確かめる"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        for row in list(self.app.tool_rows):
            mainGUI.App._remove_tool_row(self.app, row)
        self.app.logs = []
        self._log = patch.object(mainGUI.App, "_log",
                                 lambda _s, m: self.app.logs.append(m))
        self._log.start()

    def tearDown(self):
        self._log.stop()
        for row in list(self.app.tool_rows):
            mainGUI.App._remove_tool_row(self.app, row)

    def _add(self, path=""):
        row = self.app._add_tool_row(path)
        self.app.update_idletasks()
        return row

    def test_adding_a_row(self):
        self._add()

        self.assertEqual(len(self.app.tool_rows), 1)

    def test_removing_a_row(self):
        row = self._add()

        self.app._remove_tool_row(row)

        self.assertEqual(self.app.tool_rows, [])

    def test_the_label_follows_the_path(self):
        row = self._add()

        with patch.object(ToolLauncher, "is_running", return_value=False):
            row.v_path.set(r"D:\tools\ToNSaveManager.exe")
            self.app.update_idletasks()

        self.assertIn("ToNSaveManager", row.btn.cget("text"))

    def test_an_empty_path_shows_the_default_label(self):
        row = self._add()

        self.assertEqual(row.btn.cget("text"), "▶ 起動")

    def test_the_button_launches(self):
        row = self._add(r"D:\tools\ToN_ListTool.exe")

        with patch.object(ToolLauncher, "is_running", return_value=False), \
             patch.object(ToolLauncher, "launch") as mock_launch:
            self.app._launch_tool(row)

        mock_launch.assert_called_once_with(r"D:\tools\ToN_ListTool.exe")
        self.assertTrue(any("を起動しました" in m for m in self.app.logs))

    def test_a_running_tool_is_not_launched_again(self):
        """止めているのはここ。ボタンの無効化は見た目でしかない"""
        row = self._add(r"D:\tools\ToN_ListTool.exe")

        with patch.object(ToolLauncher, "is_running", return_value=True), \
             patch.object(ToolLauncher, "launch") as mock_launch:
            self.app._launch_tool(row)

        mock_launch.assert_not_called()
        self.assertTrue(any("すでに起動しています" in m for m in self.app.logs),
                        self.app.logs)

    def test_a_failing_launch_is_logged(self):
        row = self._add(r"D:\nope\missing.exe")

        with patch.object(ToolLauncher, "is_running", return_value=False), \
             patch.object(ToolLauncher, "launch",
                          side_effect=FileNotFoundError("ない")):
            self.app._launch_tool(row)      # 落ちないこと

        self.assertTrue(any("起動に失敗" in m for m in self.app.logs), self.app.logs)

    def test_a_running_tool_disables_the_button(self):
        row = self._add(r"D:\tools\ToN_ListTool.exe")

        with patch.object(ToolLauncher, "is_running", return_value=True):
            self.app._refresh_tool_row(row)

        self.assertEqual(str(row.btn.cget("state")), "disabled")

    def test_the_poll_survives_a_failure(self):
        self._add(r"D:\tools\ToN_ListTool.exe")
        calls = []
        with patch.object(mainGUI.App, "_refresh_tool_row",
                          side_effect=RuntimeError("boom")), \
             patch.object(self.app, "after", lambda *a: calls.append(a)):
            mainGUI.App._poll_tool_buttons(self.app)

        self.assertEqual(len(calls), 1, "次のtickが予約されること")

    def test_the_poll_is_not_the_host_save_one(self):
        """続行リストの供給元判定とボタンの見た目は無関係"""
        source = Path("mainGUI.py").read_text(encoding="utf-8")

        self.assertIn("_poll_tool_buttons", source)
        start = source.index("def _poll_host_save")
        end = source.index("def ", start + 10)
        self.assertNotIn("_refresh_tool_row", source[start:end])

    def test_the_section_is_not_on_the_window_tab(self):
        """窓タブを何枚開いてもセクションは1つだけ"""
        self.assertFalse(hasattr(mainGUI.WindowTab, "_add_tool_row"))
        source = Path("mainGUI.py").read_text(encoding="utf-8")
        tab = source[source.index("class WindowTab"):source.index("class App")]
        self.assertNotIn("tool_rows", tab)
        self.assertNotIn("ToolLauncher", tab)


class TestSettingsArePersisted(unittest.TestCase):
    """設定は VRChat を起動しなくても保存されること。

    保存の仕組み自体は前からあったが、呼ばれるのが「VRChatを起動」ボタンの
    中だけだった。このツールから起動しない人には何も残らなかった。
    """

    def _app(self, tabs=1, save=None):
        app = type("FakeApp", (), {})()
        app.tabs = [object()] * tabs
        app.logs = []
        app._log = app.logs.append
        app._save_launch_settings = save or MagicMock()
        app._save_settings_now = lambda: mainGUI.App._save_settings_now(app)
        return app

    # ── 終了時 ──────────────────────────────
    def test_closing_saves(self):
        app = self._app()
        app._stop = MagicMock()
        app.destroy = MagicMock()

        mainGUI.App._on_close(app)

        app._save_launch_settings.assert_called_once()

    def test_it_saves_before_destroying(self):
        """destroy() の後は Tk 変数を読めない"""
        order = []
        app = self._app(save=lambda: order.append("save"))
        app._stop = lambda: order.append("stop")
        app.destroy = lambda: order.append("destroy")

        mainGUI.App._on_close(app)

        self.assertLess(order.index("save"), order.index("destroy"))

    def test_it_saves_before_stopping(self):
        """停止が長引いても保存は済ませる"""
        order = []
        app = self._app(save=lambda: order.append("save"))
        app._stop = lambda: order.append("stop")
        app.destroy = lambda: order.append("destroy")

        mainGUI.App._on_close(app)

        self.assertLess(order.index("save"), order.index("stop"))

    def test_a_failing_save_still_closes_the_window(self):
        """閉じられなくなるほうが困る"""
        app = self._app(save=MagicMock(side_effect=OSError("disk full")))
        app._stop = MagicMock()
        app.destroy = MagicMock()

        mainGUI.App._on_close(app)

        app.destroy.assert_called_once()
        self.assertTrue(any("保存に失敗" in m for m in app.logs), app.logs)

    def test_no_tabs_means_no_save(self):
        """profiles などが空配列で上書きされる事故を避ける"""
        app = self._app(tabs=0)

        mainGUI.App._save_settings_now(app)

        app._save_launch_settings.assert_not_called()

    # ── 保存する項目は増減していない ────────────────
    def test_the_saved_keys_are_unchanged(self):
        app = type("FakeApp", (), {})()
        app.tabs = []
        app.tool_rows = []
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_instance_link",
                     "v_freeze_8pages", "v_freeze_punish", "v_emergency_key"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(set(saved), {
            "vrchat_exe", "desktop_mode", "use_osc", "ton_entry", "ton_begin",
            "join_world", "instance_link", "profiles", "freeze_8pages",
            "freeze_punish", "freeze_rounds", "emergency_stop_key",
            "tool_launchers", "obs_record", "obs_host", "obs_port", "obs_password_dpapi",
        }, "ラウンド指定3種は保存しない")

    def test_other_keys_in_the_file_survive(self):
        app = type("FakeApp", (), {})()
        app.tabs = []
        app.tool_rows = []
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_instance_link",
                     "v_freeze_8pages", "v_freeze_punish", "v_emergency_key"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings",
                          return_value={"tnl_path": "C:/list/my.tnl"}):
            mainGUI.App._save_launch_settings(app)

        self.assertEqual(saved["tnl_path"], "C:/list/my.tnl")


class TestToolRowsSaveOnChange(unittest.TestCase):
    """外部ツールの行は、追加・削除・パス変更で保存される"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        for row in list(self.app.tool_rows):
            mainGUI.App._remove_tool_row(self.app, row)
        job = getattr(self.app, "_settings_save_job", None)
        if job is not None:
            self.app.after_cancel(job)
            self.app._settings_save_job = None
        self._save = patch.object(mainGUI.App, "_save_launch_settings")
        self.saved = self._save.start()

    def tearDown(self):
        self._save.stop()
        for row in list(self.app.tool_rows):
            self.app.tool_rows.remove(row)
            row.destroy()

    def _settle(self):
        """デバウンスのタイマーを起こす"""
        for _ in range(60):
            self.app.update()
            if self.saved.called:
                return
            time.sleep(0.02)
        self.app.update()

    def test_adding_a_row_saves(self):
        self.app._add_tool_row()

        self.saved.assert_called_once()

    def test_restoring_does_not_save(self):
        """読み込んだ端から書き戻さない"""
        self.app._add_tool_row(r"D:\a\one.exe", save=False)

        self.saved.assert_not_called()

    def test_removing_a_row_saves(self):
        row = self.app._add_tool_row(save=False)

        self.app._remove_tool_row(row)

        self.saved.assert_called_once()

    def test_changing_the_path_saves_after_the_debounce(self):
        row = self.app._add_tool_row(save=False)

        row.v_path.set(r"D:\a\one.exe")
        self.assertFalse(self.saved.called, "すぐには書かないこと")
        self._settle()

        self.saved.assert_called_once()

    def test_typing_saves_only_once(self):
        row = self.app._add_tool_row(save=False)

        for text in ("D", "D:", r"D:\a", r"D:\a\one.exe"):
            row.v_path.set(text)
            self.app.update()
        self._settle()

        self.assertEqual(self.saved.call_count, 1, "まとめて1回だけ")

    def test_a_late_timer_after_destroy_does_not_raise(self):
        """ウィンドウを閉じた後にタイマーが発火しても落ちないこと"""
        app = type("FakeApp", (), {})()
        app._settings_save_job = "予約済み"
        app._save_settings_now = MagicMock(
            side_effect=tk.TclError("application has been destroyed"))

        mainGUI.App._run_scheduled_save(app)      # 例外を投げないこと

        self.assertIsNone(app._settings_save_job, "予約は消しておくこと")

    def test_scheduling_after_destroy_does_not_raise(self):
        app = type("FakeApp", (), {})()
        app._settings_save_job = None
        app._run_scheduled_save = lambda: None
        app.after = MagicMock(side_effect=tk.TclError("destroyed"))
        app.after_cancel = MagicMock()

        mainGUI.App._schedule_settings_save(app)

        self.assertIsNone(app._settings_save_job)

    def test_the_debounce_is_rescheduled_not_stacked(self):
        row = self.app._add_tool_row(save=False)

        row.v_path.set("a")
        first = self.app._settings_save_job
        row.v_path.set("ab")
        second = self.app._settings_save_job

        self.assertIsNotNone(first)
        self.assertNotEqual(first, second, "前の予約を取り消して取り直すこと")


class TestToolLauncherSettings(unittest.TestCase):
    """保存と復元"""

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

    def _row(self, path):
        row = type("FakeRow", (), {})()
        row.v_path = TestToolLauncherSettings.FakeVar(path)
        return row

    def _app(self, paths):
        app = type("FakeApp", (), {})()
        app.tabs = []
        app.tool_rows = [self._row(p) for p in paths]
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_instance_link",
                     "v_freeze_8pages", "v_freeze_punish"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestToolLauncherSettings.FakeVar(""))
        app.v_emergency_key = TestToolLauncherSettings.FakeVar("p")
        return app

    def _save(self, app):
        saved = {}
        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._save_launch_settings(app)
        return saved

    def test_paths_are_saved(self):
        app = self._app([r"D:\a\one.exe", r"D:\b\two.exe"])

        saved = self._save(app)

        self.assertEqual(saved["tool_launchers"],
                         [r"D:\a\one.exe", r"D:\b\two.exe"])

    def test_blank_rows_are_dropped(self):
        app = self._app([r"D:\a\one.exe", "", "   "])

        saved = self._save(app)

        self.assertEqual(saved["tool_launchers"], [r"D:\a\one.exe"])

    def test_saved_paths_are_restored(self):
        app = type("FakeApp", (), {})()
        added = []
        app._add_tool_row = added.append
        app.v_vrchat_exe = TestToolLauncherSettings.FakeVar()
        self._load(app, {"tool_launchers": [r"D:\a\one.exe", r"D:\b\two.exe"]})

        self.assertEqual(added, [r"D:\a\one.exe", r"D:\b\two.exe"])

    def test_a_legacy_file_without_the_key_is_fine(self):
        app = type("FakeApp", (), {})()
        added = []
        app._add_tool_row = added.append

        self._load(app, {"tnl_path": "C:/list/my.tnl"})   # キーが無い

        self.assertEqual(added, [])

    def test_a_malformed_entry_is_skipped(self):
        app = type("FakeApp", (), {})()
        added = []
        app._add_tool_row = added.append

        self._load(app, {"tool_launchers": [r"D:\a\one.exe", "", None, 42]})

        self.assertEqual(added, [r"D:\a\one.exe"])

    @staticmethod
    def _load(app, data):
        """_load_saved_settings のうち tool_launchers の復元部分だけを回す"""
        for path in data.get("tool_launchers", []) or []:
            if isinstance(path, str) and path.strip():
                app._add_tool_row(path)


class TestGroupListStatePolled(unittest.TestCase):
    """主催リストの喪失/復帰は、ラウンドを待たずに監視ループで拾う"""

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

    def _monitor(self, instance_type=config.INSTANCE_HOSHIIMO, voice="lost.mp3"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost=voice)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _tick(self, monitor):
        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._check_group_list_state()
        self.played = mock_play
        return mock_play

    def _lost(self, monitor):
        return [m for m in monitor.logs if "主催リストが取れません" in m]

    def _back(self, monitor):
        return [m for m in monitor.logs if "主催リストが戻りました" in m]

    def test_a_lost_list_is_announced_without_a_round(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)
        self.played.assert_called_once_with("lost.mp3")

    def test_ticking_again_stays_quiet(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        for _ in range(5):
            self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)
        self.played.assert_not_called()

    def test_recovery_is_announced(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        SharedState.set_list_source("host")
        self._tick(monitor)

        self.assertEqual(len(self._back(monitor)), 1, monitor.logs)
        self.assertFalse(monitor.st.list_lost_notified)

    def test_ticking_after_recovery_stays_quiet(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)
        SharedState.set_list_source("host")
        self._tick(monitor)

        for _ in range(5):
            self._tick(monitor)

        self.assertEqual(len(self._back(monitor)), 1, monitor.logs)

    def test_losing_it_twice_announces_twice(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)
        SharedState.set_list_source("host")
        self._tick(monitor)
        SharedState.set_list_source("tnl")

        self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 2, monitor.logs)
        self.played.assert_called_once_with("lost.mp3")

    def test_a_solo_private_window_is_untouched(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor(config.INSTANCE_PRIVATE)
        monitor.st.local_user_id = "usr_me"
        monitor.st.players = {"usr_me"}
        monitor.st.players_known = True

        for _ in range(3):
            self._tick(monitor)

        self.assertEqual(monitor.logs, [])
        self.played.assert_not_called()
        self.assertFalse(monitor.st.list_lost_notified)

    def test_it_fires_mid_round_too(self):
        """ラウンド進行中に落ちてもその場で鳴らす"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [99]

        self._tick(monitor)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)

    def test_hands_free_is_silent_but_still_logs(self):
        SharedState.set_hands_free(True)
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        with patch.object(LogMonitor.LogMonitor, "_hands_free", return_value=True):
            self._tick(monitor)

        self.played.assert_not_called()
        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)

    # ── _on_killers 側との関係 ───────────────
    def test_on_killers_does_not_announce_twice(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor.st.in_round = True
            monitor.st.round_type = "Bloodbath"
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        self.assertEqual(len(self._lost(monitor)), 1, monitor.logs)
        mock_play.assert_not_called()

    def test_the_on_killers_guard_still_stops_the_round(self):
        """通知を早めても、手を止めているのは判定時点のガードのまま"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._tick(monitor)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor.st.in_round = True
            monitor.st.round_type = "Bloodbath"
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        self.assertEqual([c.kwargs["target"].__func__.__name__
                          for c in mock_thread.call_args_list
                          if "target" in c.kwargs], [])
        self.assertFalse(monitor.st.is_continue_round)

    # ── ループが止まらないこと ─────────────────
    def test_the_loop_survives_a_failing_check(self):
        monitor = self._monitor()
        monitor._running = True
        monitor._stop_event = threading.Event()
        ticks = {"n": 0}

        def boom():
            ticks["n"] += 1
            if ticks["n"] >= 3:
                monitor._running = False
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "output_log.txt"
            log.write_text("", encoding="utf-8")
            monitor.cfg.log_path = log
            with patch.object(config, "LOG_POLL_INTERVAL", 0), \
                 patch.object(monitor, "_check_group_list_state", boom), \
                 patch.object(monitor, "_detect_instance_from_log"):
                monitor._run()

        self.assertGreaterEqual(ticks["n"], 3, "例外のたびにループが回り続けること")
        self.assertTrue(any("主催リストの確認に失敗" in m for m in monitor.logs),
                        monitor.logs)

    def test_the_loop_calls_it_every_tick(self):
        monitor = self._monitor()
        monitor._running = True
        monitor._stop_event = threading.Event()
        calls = {"n": 0}

        def count():
            calls["n"] += 1
            if calls["n"] >= 3:
                monitor._running = False

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "output_log.txt"
            log.write_text("", encoding="utf-8")
            monitor.cfg.log_path = log
            with patch.object(config, "LOG_POLL_INTERVAL", 0), \
                 patch.object(monitor, "_check_group_list_state", count), \
                 patch.object(monitor, "_detect_instance_from_log"):
                monitor._run()

        self.assertEqual(calls["n"], 3)


class TestPlayerLines(unittest.TestCase):
    """入退室の行。[Behaviour] の方だけを拾う"""

    PREFIX = "2026.09.18 16:56:57 Debug      -  "
    ME = "usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3"

    def _parse(self, body):
        return LogParser.parse(self.PREFIX + body)

    def test_a_join_is_read(self):
        event = self._parse(f"[Behaviour] OnPlayerJoined serim01 ({self.ME})")

        self.assertEqual(event.kind, LogParser.EVENT_PLAYER_JOINED)
        self.assertEqual(event.user_id, self.ME)
        self.assertEqual(event.player_name, "serim01")

    def test_a_leave_is_read(self):
        event = self._parse(f"[Behaviour] OnPlayerLeft serim01 ({self.ME})")

        self.assertEqual(event.kind, LogParser.EVENT_PLAYER_LEFT)
        self.assertEqual(event.user_id, self.ME)

    def test_a_japanese_name_is_read(self):
        event = self._parse("[Behaviour] OnPlayerJoined しゅんかしゅうとう "
                            "(usr_f651fb3b-9ea3-4136-aa53-2d3e11aebff4)")

        self.assertEqual(event.player_name, "しゅんかしゅうとう")

    def test_a_name_with_brackets_is_read(self):
        event = self._parse("[Behaviour] OnPlayerJoined a (b) c "
                            "(usr_f651fb3b-9ea3-4136-aa53-2d3e11aebff4)")

        self.assertEqual(event.player_name, "a (b) c")
        self.assertEqual(event.user_id, "usr_f651fb3b-9ea3-4136-aa53-2d3e11aebff4")

    def test_the_playerlog_form_is_not_counted(self):
        """同じ入室が2行出る。両方拾うと二重に数える"""
        self.assertIsNone(self._parse("[PlayerLog] OnPlayerJoined: serim01 (VR=False)"))

    def test_similar_lines_are_not_counted(self):
        for body in ("[Behaviour] OnPlayerLeftRoom",
                     f"[Behaviour] OnPlayerJoinComplete serim01",
                     "[Behaviour] OnPlayerLeft VRCPlayer[Remote] 63939541 21 ()"):
            event = self._parse(body)
            self.assertFalse(
                event is not None and event.kind in (
                    LogParser.EVENT_PLAYER_JOINED, LogParser.EVENT_PLAYER_LEFT),
                body)


class TestHostListNeedsOthers(unittest.TestCase):
    """主催リストが必要なのは「他の人がいて、自動自爆ON」のときだけ"""

    ME = "usr_0e01408a"
    PREFIX = "2026.09.18 16:56:57 Debug      -  "
    JOIN = (PREFIX + "[Behaviour] Joining wrld_1234:5678~private(usr_me)")

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("tnl")
        self._stats = patch.object(ConnectDB, "send_ToNRoundStatistics")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    def _monitor(self, *, others=(), do_skip=True, known=True,
                 instance_type=config.INSTANCE_PRIVATE, keep_on=None):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3",
                           voice_list_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Bloodbath"
        monitor.st.local_user_id = self.ME
        monitor.st.players = {self.ME, *others}
        monitor.st.players_known = known
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids=(1, 2, 3)):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _stopped(self, monitor):
        return any("主催リストが取れません" in m for m in monitor.logs)

    def _line(self, monitor, body):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.PREFIX + body)

    # ── 依頼者の表 ──────────────────────────
    def test_solo_with_the_tnl_decides(self):
        monitor = self._monitor()

        started = self._killers(monitor)

        self.assertFalse(self._stopped(monitor), monitor.logs)
        self.assertIn("do_skip", started)

    def test_others_with_skip_on_and_the_tnl_stops(self):
        monitor = self._monitor(others={"usr_f00d"})

        started = self._killers(monitor)

        self.assertTrue(self._stopped(monitor), monitor.logs)
        self.assertEqual(started, [])

    def test_others_with_skip_on_and_the_host_list_decides(self):
        SharedState.set_list_source("host")
        monitor = self._monitor(others={"usr_f00d"})

        started = self._killers(monitor)

        self.assertFalse(self._stopped(monitor), monitor.logs)
        self.assertIn("do_skip", started)

    def test_others_with_skip_off_and_the_tnl_does_not_stop(self):
        monitor = self._monitor(others={"usr_f00d"}, do_skip=False,
                                keep_on={"Bloodbath/ブラッドバス": {1}})

        self._killers(monitor, [1])

        self.assertFalse(self._stopped(monitor), monitor.logs)
        self.assertTrue(monitor.st.is_continue_round, "tnl で判定している")

    def test_it_applies_to_group_windows_the_same_way(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            solo = self._monitor(instance_type=itype)
            crowd = self._monitor(instance_type=itype, others={"usr_f00d"})

            self.assertFalse(solo._host_list_missing(), itype)
            self.assertTrue(crowd._host_list_missing(), itype)

    def test_a_public_window_never_asks_for_it(self):
        """public は判定そのものをしない。鳴らしても意味が無い"""
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC,
                                others={"usr_a", "usr_b"})

        self.assertFalse(monitor._host_list_missing())

    # ── 人数の数え方 ─────────────────────────
    def test_i_am_not_one_of_the_others(self):
        monitor = self._monitor()

        self._line(monitor, f"[Behaviour] OnPlayerJoined serim01 ({self.ME})")

        self.assertFalse(monitor._others_present())

    def test_a_join_and_a_leave_move_the_count(self):
        monitor = self._monitor()

        self._line(monitor, "[Behaviour] OnPlayerJoined friend (usr_f00d)")
        self.assertTrue(monitor._others_present())

        self._line(monitor, "[Behaviour] OnPlayerLeft friend (usr_f00d)")
        self.assertFalse(monitor._others_present())

    def test_the_playerlog_line_does_not_double_count(self):
        monitor = self._monitor()
        self._line(monitor, "[Behaviour] OnPlayerJoined friend (usr_f00d)")
        self._line(monitor, "[PlayerLog] OnPlayerJoined: friend (VR=False)")

        self._line(monitor, "[Behaviour] OnPlayerLeft friend (usr_f00d)")

        self.assertFalse(monitor._others_present(), "1回の退室で0人に戻る")

    def test_joining_an_instance_clears_the_count(self):
        monitor = self._monitor(others={"usr_f00d"})

        self._line(monitor, "[Behaviour] Joining wrld_1234:5678~private(usr_me)")

        self.assertEqual(monitor.st.players, set())
        self.assertTrue(monitor.st.players_known,
                        "入室の瞬間から見ているので信用してよい")

    def test_an_unknown_count_means_others_are_present(self):
        """復元できないときは安全側"""
        monitor = self._monitor(known=False)

        self.assertTrue(monitor._others_present())
        self.assertTrue(monitor._host_list_missing())

    def test_a_fresh_window_starts_unknown(self):
        self.assertFalse(WindowState().players_known)

    def test_my_own_id_comes_from_the_auth_line(self):
        monitor = self._monitor()
        monitor.st.local_user_id = ""

        self._line(monitor, "User Authenticated: serim01 (usr_0e01408a-ac26)")

        self.assertEqual(monitor.st.local_user_id, "usr_0e01408a-ac26")

    # ── 周期チェックと一致していること ──────────────
    def test_the_periodic_check_uses_the_same_condition(self):
        cases = [
            dict(),                                         # ソロ
            dict(others={"usr_f00d"}),                     # 他の人・自爆ON
            dict(others={"usr_f00d"}, do_skip=False),      # 他の人・自爆OFF
            dict(known=False),                               # 不明
            dict(instance_type=config.INSTANCE_PUBLIC, others={"usr_a"}),
        ]
        for case in cases:
            for src in ("tnl", "host", None):
                SharedState.set_list_source(src)
                polled = self._monitor(**case)
                decided = self._monitor(**case)

                with patch.object(PlaySound, "play_sound"):
                    polled._check_group_list_state()
                self._killers(decided)

                self.assertEqual(self._stopped(polled), self._stopped(decided),
                                 f"{case} / {src}")


class TestPlayersRestoredOnStart(unittest.TestCase):
    """マクロを途中で始めても、いまのインスタンスの人数が分かること"""

    ME = "usr_0e01408a"

    def _log_file(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        prefix = "2026.09.18 16:56:57 Debug      -  "
        tmp.write("\n".join(prefix + line for line in lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def _restore(self, lines):
        cfg = WindowConfig(log_path=self._log_file(lines))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        with patch.object(ConnectDB, "send_Users", return_value=1):
            monitor._detect_instance_from_log()
        return monitor

    def test_the_players_since_the_last_join_are_restored(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_old:1~private(usr_me)",
            "[Behaviour] OnPlayerJoined ghost (usr_9057)",   # 前のインスタンス
            f"[Behaviour] OnPlayerLeft serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~group(grp_x)~groupAccessType(plus)",
            "[Behaviour] OnPlayerJoined a (usr_a)",
            "[Behaviour] OnPlayerJoined b (usr_b)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[Behaviour] OnPlayerLeft b (usr_b)",
        ])

        self.assertTrue(monitor.st.players_known)
        self.assertEqual(monitor.st.players, {"usr_a", self.ME})
        self.assertEqual(monitor._other_players(), {"usr_a"})
        self.assertTrue(any("自分以外 1人" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_solo_instance_is_restored_as_solo(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_me)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[PlayerLog] OnPlayerJoined: serim01 (VR=False)",
        ])

        self.assertTrue(monitor.st.players_known)
        self.assertFalse(monitor._others_present())

    def test_everyone_left_is_solo(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_me)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[Behaviour] OnPlayerJoined a (usr_a)",
            "[Behaviour] OnPlayerLeft a (usr_a)",
        ])

        self.assertFalse(monitor._others_present())

    def test_no_join_line_means_others_are_present(self):
        """復元できない。安全側に倒す"""
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] OnPlayerJoined a (usr_a)",
        ])

        self.assertFalse(monitor.st.players_known)
        self.assertTrue(monitor._others_present())
        self.assertTrue(any("復元できません" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_read_error_means_others_are_present(self):
        cfg = WindowConfig(log_path=self._log_file(["x"]))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logger = lambda _m: None

        with patch.object(LogMonitor.LogMonitor, "_iter_log_lines_reversed",
                          side_effect=OSError("locked")):
            monitor._detect_instance_from_log()

        self.assertFalse(monitor.st.players_known)
        self.assertTrue(monitor._others_present())

    def test_my_id_is_restored_too(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_me)",
        ])

        self.assertEqual(monitor.st.local_user_id, self.ME)


class TestReadmeRelease(unittest.TestCase):
    def _readme(self):
        return (Path(__file__).resolve().parent.parent / "README.md"
                ).read_text(encoding="utf-8")

    def test_the_download_url_follows_the_latest_release(self):
        """版を書くと、リリース前は404・リリース後は古くなる。latest なら直さなくてよい"""
        # markdown のリンク [文字](URL) の閉じ括弧を URL に含めない
        urls = re.findall(r"https://github\.com/[^\s)]+/releases/[^\s)]+", self._readme())

        self.assertTrue(urls, "URL が見つからない")
        for url in urls:
            self.assertIn("/releases/latest/download/", url)

    def test_the_url_points_at_the_update_asset(self):
        urls = re.findall(r"releases/latest/download/([^\s)]+)", self._readme())

        self.assertEqual(urls, [config.UPDATE_ASSET_NAME])

    def test_the_url_points_at_this_repository(self):
        self.assertIn(f"github.com/{config.GITHUB_REPO}/releases/",
                      self._readme())


class TestSabotageStarAnnounces(unittest.TestCase):
    """Star側の続行希望は「誰かが欲しがっている」。アナウンスとフリーズを出す"""

    STAR = GroupRound.SABOTAGE_STAR_KEY
    MURDER = GroupRound.SABOTAGE_MURDER_KEY

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

    def _monitor(self, instance_type=config.INSTANCE_HOSHIIMO, wishes=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(
            cfg, {}, lambda _m: None, window_idx=1,
            host_wishes=wishes if wishes is not None
            else {"みているひと": {self.STAR: {5}}})
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Sabotage"
        monitor.st.sus_players = ["ソノア7"]
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _apply(self, monitor, round_type=None):
        monitor.st.terror_ids = [5]
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound") as mock_play:
            handled = monitor._apply_group_decision(
                round_type or monitor.st.round_type)
        self.played = mock_play
        return handled

    # ── WANTED の処理 ─────────────────────────
    def test_it_announces_once(self):
        monitor = self._monitor()

        self.assertTrue(self._apply(monitor))

        self.played.assert_called_once_with("continue.mp3")
        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_it_logs_as_play(self):
        monitor = self._monitor()

        self._apply(monitor)

        self.assertTrue(any("【プレイ】" in m for m in monitor.logs), monitor.logs)
        self.assertTrue(any("続行アナウンス再生" in m for m in monitor.logs))

    def test_twice_in_a_round_freezes_once(self):
        """同じラウンドで _on_killers が複数回来ても二重にフリーズしない"""
        monitor = self._monitor()

        self._apply(monitor)
        first = self.played.call_count
        self._apply(monitor)

        self.assertEqual(first, 1)
        self.played.assert_not_called()
        self.assertEqual(SharedState.get_continue_round_count(), 1)

    def test_hands_free_is_silent(self):
        SharedState.set_hands_free(True)
        monitor = self._monitor(config.INSTANCE_PRIVATE)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        self.assertTrue(monitor._hands_free())
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO

        # 放置モードが効く窓を装って、鳴らさない側の分岐を通す
        with patch.object(LogMonitor.LogMonitor, "_hands_free",
                          return_value=True):
            self._apply(monitor)

        self.played.assert_not_called()
        self.assertTrue(monitor.st.is_continue_round, "続行状態自体は立てる")

    def test_both_group_types_behave_the_same(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            SharedState.continue_round_reset()
            monitor = self._monitor(itype)

            self._apply(monitor)

            self.assertTrue(monitor.st.is_continue_round, itype)
            self.played.assert_called_once_with("continue.mp3")

    # ── CONTINUE は従来どおり無音 ───────────────
    def test_plain_continue_stays_silent(self):
        monitor = self._monitor()
        monitor.st.round_type = "8 Pages"

        self.assertTrue(self._apply(monitor, "8 Pages"))

        self.played.assert_not_called()
        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_hoshiimo_fog_stays_silent(self):
        monitor = self._monitor()
        monitor.st.round_type = "Fog"

        self._apply(monitor, "Fog")

        self.played.assert_not_called()
        self.assertFalse(monitor.st.is_continue_round)

    def test_yakiimo_fog_still_goes_to_the_normal_judgement(self):
        monitor = self._monitor(config.INSTANCE_YAKIIMO)
        monitor.st.round_type = "Fog"

        handled = self._apply(monitor, "Fog (Alternate)")

        self.assertFalse(handled, "NORMAL のまま（通常判定で鳴る）")

    # ── 後始末 ───────────────────────────────
    def _next_round(self, monitor, round_type, ids, wishes=None):
        monitor.st.round_type = round_type
        monitor.st.terror_ids = list(ids)
        if wishes is not None:
            monitor.host_wishes = wishes
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            return monitor._apply_group_decision(round_type)

    def test_a_following_skip_releases_the_freeze(self):
        monitor = self._monitor()
        self._apply(monitor)

        self._next_round(monitor, "Bloodbath", [1, 2, 3])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_a_following_normal_round_releases_the_freeze(self):
        monitor = self._monitor()
        self._apply(monitor)

        monitor.st.round_type = "Midnight"
        monitor.st.terror_ids = [99]
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"):
            monitor._decide_with_keep_on_set("Midnight")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_a_following_plain_continue_releases_the_freeze(self):
        """張りっぱなしになると全窓が止まる。ここがいちばん危ない"""
        monitor = self._monitor()
        self._apply(monitor)
        self.assertEqual(SharedState.get_continue_round_count(), 1)

        self._next_round(monitor, "8 Pages", [1, 2])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_round_start_also_releases_it(self):
        """通常はこちらで落ちる（ラウンドの切れ目）"""
        monitor = self._monitor()
        self._apply(monitor)

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("This round is taking place at Facility (12) "
                             "and the round type is 8 Pages")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)


class TestGroupNeedsHostList(unittest.TestCase):
    """他の人がいて自動自爆ONなら、主催リストが無いと手を止める。

    ここの窓は人数を復元していない（players_known=False）ので「他の人が
    いる」扱いになる。ソロの扱いは TestHostListNeedsOthers で見る。
    """

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

    def test_a_solo_private_window_uses_the_tnl(self):
        for src in ("tnl", None):
            SharedState.set_list_source(src)
            monitor = self._monitor(config.INSTANCE_PRIVATE,
                                    keep_on={"Bloodbath/ブラッドバス": {1}})
            monitor.st.local_user_id = "usr_me"
            monitor.st.players = {"usr_me"}
            monitor.st.players_known = True

            started = self._killers(monitor, [1])

            self.assertTrue(monitor.st.is_continue_round, src)
            self.assertNotIn("do_skip", started, src)

    def test_a_private_window_with_others_stops_too(self):
        """private でも他の人がいれば他人の周回。自分の tnl では裁かない"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor(config.INSTANCE_PRIVATE)

        self.assertTrue(monitor._host_list_missing())

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

        self.assertTrue(any("Terror set:" in m for m in monitor.logs), monitor.logs)

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

    def test_the_voice_defaults_to_the_bundled_file(self):
        self.assertTrue(config.VOICE_LIST_LOST.endswith("StopAutoSuicide.mp3"),
                        config.VOICE_LIST_LOST)
        self.assertTrue(Path(config.VOICE_LIST_LOST).exists(),
                        "voice フォルダに実ファイルがあること")

    def test_the_window_config_still_defaults_to_silence(self):
        """GUIから注入されるまでは鳴らさない（他のvoiceと同じ）"""
        self.assertEqual(WindowConfig().voice_list_lost, "")


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
                 do_skip=True, keep_on=None,
                 instance_type=config.INSTANCE_PRIVATE):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3",
                           skip_rounds=set(skip_rounds),
                           continue_rounds=set(continue_rounds))
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor._running = True
        return monitor

    def _killers(self, monitor, ids=(99,)):
        """置き換え待ちに入ったら、合図が来ないまま待ち明けたものとして進める"""
        round_type = monitor.st.round_type
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers(list(ids), round_type, revealed=False)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]
            if "_delayed_decision" in started:
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += [c.kwargs["target"].__func__.__name__
                            for c in mock_thread.call_args_list
                            if "target" in c.kwargs]
        self.played = mock_play
        return started

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
                                skip_rounds=("Classic",),
                                keep_on={self.CLASSIC_KEY: {config.GIGABYTES_ID}})

        started = self._killers(monitor)

        self.assertNotIn("_delayed_decision", started)

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
    """窓ごとのラウンド指定。**保存も復元もしない**——自爆設定の持ち越しは危ない"""

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
        return tab

    def test_the_selectable_list_drives_the_variables(self):
        made = mainGUI.skip_round_vars(lambda: object())

        self.assertEqual(list(made), config.SKIP_ROUND_SELECTABLE)

    def _save(self, tabs, stored=None):
        app = type("FakeApp", (), {})()
        app.tabs = tabs
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc", "v_ton_entry",
                     "v_ton_begin", "v_join_world", "v_instance_link",
                     "v_freeze_8pages", "v_freeze_punish"):
            setattr(app, name, TestSkipRoundsSettings.FakeVar(""))
        app.v_freeze_rounds = {}
        for name in ("v_obs_enabled", "v_obs_host", "v_obs_port", "v_obs_password"):
            setattr(app, name, TestSkipRoundsSettings.FakeVar(""))
        app.tool_rows = []
        app.v_emergency_key = TestSkipRoundsSettings.FakeVar("p")
        saved = {}

        with patch.object(mainGUI, "save_settings", saved.update), \
             patch.object(mainGUI, "load_settings",
                          return_value=dict(stored or {})):
            mainGUI.App._save_launch_settings(app)
        return saved

    # ── 保存しない ───────────────────────────
    def test_the_round_lists_are_not_saved(self):
        saved = self._save([self._tab(("Classic", "Fog"), exempt=True,
                                      keep=("Run",)), self._tab()])

        for key in ("skip_rounds", "skip_variant_exempt", "continue_rounds"):
            self.assertNotIn(key, saved, key)

    def test_the_other_window_settings_are_still_saved(self):
        """巻き込んでいないこと"""
        saved = self._save([self._tab(), self._tab()])

        self.assertEqual(saved["profiles"], [0, 0])
        self.assertEqual(saved["tool_launchers"], [])
        self.assertEqual(saved["emergency_stop_key"], "p")

    def test_old_values_are_removed_from_the_file(self):
        """書かないだけでは足りない——マージするので前回の値が残り続ける"""
        saved = self._save([self._tab()], stored={
            "skip_rounds": [["Classic"]],
            "skip_variant_exempt": [True],
            "continue_rounds": [["Fog"]],
            "tnl_path": "C:/list/my.tnl",
        })

        for key in ("skip_rounds", "skip_variant_exempt", "continue_rounds"):
            self.assertNotIn(key, saved, key)
        self.assertEqual(saved["tnl_path"], "C:/list/my.tnl", "他は消さないこと")

    # ── 復元しない ───────────────────────────
    def test_the_round_lists_are_not_restored(self):
        """古い settings.json に値が残っていても、チェックは付かない"""
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(), self._tab()]
        app._saved_profiles = []
        app._saved_skip_rounds = [["Classic", "Fog"], []]
        app._saved_skip_variant_exempt = [True, False]
        app._saved_continue_rounds = [["Run"], []]

        mainGUI.App._apply_saved_window_settings(app)

        for tab in app.tabs:
            self.assertEqual(
                {n for n, v in tab.v_skip_rounds.items() if v.get()}, set())
            self.assertEqual(
                {n for n, v in tab.v_continue_rounds.items() if v.get()}, set())

    def test_the_profiles_are_still_restored(self):
        """巻き込んでいないこと"""
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(), self._tab()]
        app._saved_profiles = [3, 5]

        mainGUI.App._apply_saved_window_settings(app)

        self.assertEqual([tab.v_profile.get() for tab in app.tabs], [3, 5])

    def test_the_window_config_defaults_to_nothing_selected(self):
        cfg = WindowConfig()

        self.assertEqual(cfg.skip_rounds, set())

    def test_a_started_window_begins_with_nothing_selected(self):
        """起動直後のタブから作った設定にも何も入らない"""
        cfg = mainGUI.LogMonitor.WindowConfig(
            skip_rounds={n for n, v in self._tab().v_skip_rounds.items()
                         if v.get()},
            continue_rounds={n for n, v in self._tab().v_continue_rounds.items()
                             if v.get()})

        self.assertEqual(cfg.skip_rounds, set())
        self.assertEqual(cfg.continue_rounds, set())


class TestRoundSettingsAreNotLoaded(unittest.TestCase):
    """起動時、ラウンド指定3種が未チェックで始まること。

    _load_saved_settings を丸ごと回す——「復元しない」ことの検証なので、
    途中を差し替えると意味が無い。
    """

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    def _tab(self):
        tab = type("FakeTab", (), {})()
        tab.v_profile = TestRoundSettingsAreNotLoaded.FakeVar(0)
        tab.v_skip_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                             for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_continue_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                                 for n in config.SKIP_ROUND_SELECTABLE}
        return tab

    def _load(self, data):
        app = type("FakeApp", (), {})()
        app.tabs = [self._tab(), self._tab()]
        for name in ("v_vrchat_exe", "v_desktop_mode", "v_use_osc",
                     "v_ton_entry", "v_ton_begin", "v_join_world",
                     "v_instance_link", "v_emergency_key", "v_freeze_8pages",
                     "v_freeze_punish", "v_tnl", "v_obs_enabled", "v_obs_host",
                     "v_obs_port", "v_obs_password"):
            setattr(app, name, TestRoundSettingsAreNotLoaded.FakeVar(""))
        app._apply_obs_settings = lambda: None
        app.v_freeze_rounds = {n: TestRoundSettingsAreNotLoaded.FakeVar(False)
                               for n in config.SKIP_ROUND_SELECTABLE}
        app.added_tools = []
        app._add_tool_row = lambda p, save=True: app.added_tools.append(p)
        app._refresh_emergency_key_label = lambda: None
        app._apply_freeze_settings = lambda: None
        app._load_tnl = lambda show_error=True: None
        app._apply_saved_window_settings = \
            lambda: mainGUI.App._apply_saved_window_settings(app)

        with patch.object(mainGUI, "load_settings", return_value=data), \
             patch.object(HotKey, "is_valid", return_value=True):
            mainGUI.App._load_saved_settings(app)
        return app

    FULL = {
        "skip_rounds": [["Classic", "Fog"], ["Run"]],
        "skip_variant_exempt": [True, True],
        "continue_rounds": [["8 Pages"], ["Midnight"]],
        "profiles": [2, 7],
        "emergency_stop_key": "f9",
        "tool_launchers": ["D:/tools/one.exe"],
        "freeze_8pages": True,
        "freeze_punish": True,
        "freeze_rounds": ["Classic"],
    }

    def test_skip_rounds_start_unchecked(self):
        app = self._load(dict(self.FULL))

        for tab in app.tabs:
            self.assertEqual(
                {n for n, v in tab.v_skip_rounds.items() if v.get()}, set())

    def test_an_old_variant_switch_in_the_file_is_ignored(self):
        """チェックボックスは廃止した。古いファイルに残っていても読まない"""
        app = self._load(dict(self.FULL))

        for tab in app.tabs:
            self.assertFalse(hasattr(tab, "v_skip_variant_exempt"))

    def test_continue_rounds_start_unchecked(self):
        app = self._load(dict(self.FULL))

        for tab in app.tabs:
            self.assertEqual(
                {n for n, v in tab.v_continue_rounds.items() if v.get()}, set())

    def test_the_profiles_are_restored(self):
        app = self._load(dict(self.FULL))

        self.assertEqual([tab.v_profile.get() for tab in app.tabs], [2, 7])

    def test_the_other_settings_are_restored(self):
        """freeze_* / emergency_stop_key / tool_launchers は従来どおり"""
        app = self._load(dict(self.FULL))

        self.assertEqual(app.v_emergency_key.get(), "f9")
        self.assertEqual(app.added_tools, ["D:/tools/one.exe"])
        self.assertTrue(app.v_freeze_8pages.get())
        self.assertTrue(app.v_freeze_punish.get())
        self.assertTrue(app.v_freeze_rounds["Classic"].get())


class TestRoundSettingsClearedOnInstanceChange(unittest.TestCase):
    """インスタンスが変わったらラウンド指定を解除する。

    監視が見る側（WindowConfig）と、利用者が見る側（GUIのチェック）の
    **両方**。片方だけだと表示と動きが食い違う。
    """

    JOIN = ("2026.09.15 10:00:00 Debug      -  [Behaviour] "
            "Joining wrld_1234:5678~private(usr_x)")

    def _monitor(self, skip=("Classic",), keep=(), callback="none"):
        cfg = WindowConfig(do_skip=True, skip_rounds=set(skip),
                           continue_rounds=set(keep))
        kwargs = {} if callback == "none" else \
            {"on_round_settings_cleared": callback}
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None,
                                        window_idx=1, **kwargs)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _join(self, monitor):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.JOIN)

    # ── 監視が見る側 ──────────────────────────
    def test_the_config_is_cleared(self):
        monitor = self._monitor(skip=("Classic", "Fog"), keep=("Run",))

        self._join(monitor)

        self.assertEqual(monitor.cfg.skip_rounds, set())
        self.assertEqual(monitor.cfg.continue_rounds, set())

    def test_it_stops_skipping_that_round(self):
        monitor = self._monitor(skip=("Classic",))
        monitor.st.round_type = "Classic"
        self.assertTrue(monitor._should_skip_by_round(), "前提")

        self._join(monitor)

        self.assertFalse(monitor._should_skip_by_round())

    def test_the_master_switch_is_left_alone(self):
        """「自動自爆」は従来どおり"""
        monitor = self._monitor()

        self._join(monitor)

        self.assertTrue(monitor.cfg.do_skip)

    def test_it_is_logged(self):
        monitor = self._monitor()

        self._join(monitor)

        self.assertTrue(any("ラウンド指定を解除" in m for m in monitor.logs),
                        monitor.logs)

    def test_nothing_selected_logs_nothing(self):
        """毎回の入室で流れると邪魔になる"""
        monitor = self._monitor(skip=())

        self._join(monitor)

        self.assertFalse(any("ラウンド指定を解除" in m for m in monitor.logs),
                         monitor.logs)

    # ── 利用者が見る側 ─────────────────────────
    def test_the_callback_gets_the_window_number(self):
        got = []
        monitor = self._monitor(callback=got.append)

        self._join(monitor)

        self.assertEqual(got, [1])

    def test_the_callback_is_called_even_with_nothing_selected(self):
        """監視開始の後でチェックを付けた分は cfg に入っていない"""
        got = []
        monitor = self._monitor(skip=(), callback=got.append)

        self._join(monitor)

        self.assertEqual(got, [1])

    def test_no_callback_still_works(self):
        monitor = self._monitor()

        self._join(monitor)      # 既定は None。落ちないこと

        self.assertEqual(monitor.cfg.skip_rounds, set())


class TestGuiClearsRoundChecks(unittest.TestCase):
    """GUI側。Tk変数はメインスレッドでしか触れない"""

    class FakeVar:
        def __init__(self, value=False):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    class BrokenVar:
        def get(self):
            return True

        def set(self, v):
            raise tk.TclError("application has been destroyed")

    def _tab(self, idx, on=True):
        make = TestGuiClearsRoundChecks.FakeVar
        tab = type("FakeTab", (), {})()
        tab.idx = idx
        tab.v_skip_rounds = {n: make(on) for n in config.SKIP_ROUND_SELECTABLE}
        tab.v_continue_rounds = {n: make(on)
                                 for n in config.SKIP_ROUND_SELECTABLE}
        return tab

    def _checked(self, tab):
        return ({n for n, v in tab.v_skip_rounds.items() if v.get()},
                {n for n, v in tab.v_continue_rounds.items() if v.get()})

    def _app(self, tabs):
        app = type("FakeApp", (), {})()
        app.tabs = tabs
        app.after = MagicMock()
        app._do_clear_tab_round_settings = \
            lambda idx: mainGUI.App._do_clear_tab_round_settings(app, idx)
        return app

    def test_the_checks_are_cleared(self):
        app = self._app([self._tab(0)])

        mainGUI.App._do_clear_tab_round_settings(app, 1)

        self.assertEqual(self._checked(app.tabs[0]), (set(), set()))

    def test_only_that_window_is_cleared(self):
        app = self._app([self._tab(0), self._tab(1)])

        mainGUI.App._do_clear_tab_round_settings(app, 2)

        self.assertNotEqual(self._checked(app.tabs[0])[0], set(),
                            "他の窓は触らないこと")
        self.assertEqual(self._checked(app.tabs[1]), (set(), set()))

    def test_an_unknown_window_is_ignored(self):
        """窓数を減らした後に届いた"""
        app = self._app([self._tab(0)])

        mainGUI.App._do_clear_tab_round_settings(app, 9)

        self.assertNotEqual(self._checked(app.tabs[0])[0], set())

    def test_it_goes_through_the_main_thread(self):
        app = self._app([self._tab(0)])

        mainGUI.App._clear_tab_round_settings(app, 1)

        app.after.assert_called_once()
        delay, func = app.after.call_args[0]
        self.assertEqual(delay, 0)
        self.assertNotEqual(self._checked(app.tabs[0])[0], set(),
                            "after を待たずに触らないこと")
        func()
        self.assertEqual(self._checked(app.tabs[0]), (set(), set()))

    def test_a_destroyed_window_does_not_raise(self):
        app = self._app([self._tab(0)])
        app.tabs[0].v_skip_rounds["Classic"] = \
            TestGuiClearsRoundChecks.BrokenVar()

        mainGUI.App._do_clear_tab_round_settings(app, 1)   # 落ちないこと

    def test_a_destroyed_window_does_not_raise_from_the_thread(self):
        app = self._app([self._tab(0)])
        app.after = MagicMock(side_effect=tk.TclError("destroyed"))

        mainGUI.App._clear_tab_round_settings(app, 1)      # 落ちないこと

    def test_after_outside_the_mainloop_does_not_raise(self):
        """本物の Tk は mainloop の外だと RuntimeError を投げる。
        監視スレッドで投げると、そのスレッドごと黙って止まる"""
        app = self._app([self._tab(0)])
        app.after = MagicMock(
            side_effect=RuntimeError("main thread is not in main loop"))

        mainGUI.App._clear_tab_round_settings(app, 1)      # 落ちないこと


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

    def _killers(self, monitor, ids, killers_round_type=None, revealed=False,
                 settle=False):
        """_on_killers を回して、起動したスレッドの target 名を返す。

        settle: 置き換え待ちに入ったら、合図が来ないまま待ち明けたものとして進める
        """
        round_type = killers_round_type or monitor.st.round_type
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type, revealed=revealed)
            started = [c.kwargs["target"].__func__.__name__
                       for c in mock_thread.call_args_list if "target" in c.kwargs]
            if settle and "_delayed_decision" in started:
                monitor._running = True
                mock_thread.reset_mock()
                monitor._delayed_decision(round_type, 0.0, monitor.st.round_seq)
                started += [c.kwargs["target"].__func__.__name__
                            for c in mock_thread.call_args_list
                            if "target" in c.kwargs]
        return started

    def _round(self, monitor, round_type, ids=(99,), **kw):
        monitor.st.round_type = round_type
        return self._killers(monitor, ids, **kw)

    # ── 問答無用スキップ ──────────────────────
    def _classic_monitor(self, tid, keep_on=None,
                         instance_type=config.INSTANCE_HOSHIIMO):
        """テラーが確定した Classic ラウンド。

        Classicの1体構成は Gigabytes 待ちに入りうるので、判定は待ち明け
        （`_delayed_decision`）で出る。そこを直接動かして確かめる。
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
        """Foxy検出はオルタ枠を引数で伝える。st.round_type は Fog のまま"""
        monitor = self._monitor()      # 干し芋 → Fog は全続行
        monitor.st.round_type = "Fog"
        monitor.st.instance_access = "group_members"   # 看破 OK（干し芋は Group Only）

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("foxy the pirate turned evil!")

        self.assertEqual(monitor.st.round_type, "Fog",
                         "書き換えると「Fog」の自爆指定が効かなくなる")
        self.assertEqual(monitor.st.terror_ids, [config.FOXY_ID])
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

        # Classic の1体構成は Gigabytes が来ると結論が変わるので待つ
        started = self._round(monitor, "Classic", [99], settle=True)

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
    def test_a_single_terror_classic_waits_when_gigabytes_is_wanted(self):
        """元IDが毎回違うので、1体構成はどれも Gigabytes の候補"""
        monitor = self._monitor(keep_on={"Classic/クラシック": {config.GIGABYTES_ID}})

        started = self._round(monitor, "Classic", [99])

        self.assertEqual(started, ["_delayed_decision"])

    def test_a_single_terror_classic_skips_at_once_when_nothing_is_wanted(self):
        """置き換わってもリストに無ければ、どちらにしても自爆。待たない"""
        monitor = self._monitor()

        started = self._round(monitor, "Classic", [99])

        self.assertEqual(started, ["do_skip"])

    def test_rounds_whose_rule_ignores_the_terror_do_not_wait(self):
        """Bloodbath は問答無用スキップ、8 Pages は全続行。置き換わっても同じ"""
        for round_type, ids, expected in (
                ("Bloodbath", [config.CURIOUS_CREATURE_ID, 2, 3], ["do_skip"]),
                ("8 Pages", [config.CURIOUS_CREATURE_ID, 2], [])):
            monitor = self._monitor(
                keep_on={"Bloodbath/ブラッドバス": {config.BLOODTHIRSTY_CREATURE_ID}})

            started = self._round(monitor, round_type, ids)

            self.assertEqual(started, expected, round_type)

    def test_a_normal_round_waits_when_the_variant_is_wanted(self):
        monitor = self._monitor(
            keep_on={self.DT_KEY: {config.BLOODTHIRSTY_CREATURE_ID}})

        started = self._round(monitor, "Double Trouble",
                              [config.CURIOUS_CREATURE_ID, 5])

        self.assertEqual(started, ["_delayed_decision"])

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
            monitor._delayed_decision(killers_round_type, wait_sec,
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
            monitor._delayed_decision("Classic", 0.0, 4)

        mock_thread.assert_not_called()

    def test_the_wait_falls_through_to_the_normal_judgement(self):
        """Variantが確定したら通常判定へ回すこと（待ちの間に抜けている）"""
        monitor = self._monitor(keep_on={"Classic/クラシック": {config.ATRACHED_ID}})
        monitor._running = True
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [config.ATRACHED_ID]
        monitor.st.atrached_variant = True
        monitor.st.gigabytes = True     # _run_delayed は _on_killers を通らないので
                                        # ここでは ids の差し替えは起きない

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
        started = [c.kwargs["target"].__func__.__name__
                   for c in mock_thread.call_args_list if "target" in c.kwargs]
        # Begin 処理と、Verified Round End からの速度検知だけ。音声は出さない
        self.assertEqual(sorted(started), ["do_after_round", "do_speed_detect"])
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

    def test_fog_entry_does_not_freeze_the_other_windows(self):
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)
        self.assertTrue(SharedState.CONTINUE_ROUND_EVENT.is_set())

    def test_a_fog_skip_leaves_another_windows_freeze_alone(self):
        """入場で足していないので、判明時に引いてもいけない"""
        SharedState.continue_round_start()          # 別の窓の本物の続行
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1,
                         "別の窓のフリーズが残っていること")
        self.assertFalse(SharedState.CONTINUE_ROUND_EVENT.is_set())
        mock_thread.assert_called_once()        # 自爆

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

    def test_fog_reveal_continue_counts_the_freeze_once(self):
        """入場では張らず、続行と分かった時点で1回だけ張る"""
        monitor = self._monitor(keep_on={"Fog/霧": {44}})
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertEqual(SharedState.get_continue_round_count(), 0)

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 1)
        mock_play.assert_called_once_with("continue.mp3")

    def test_the_fog_voice_is_off_by_default(self):
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertFalse(config.ANNOUNCE_FOG_ON_ENTRY)
        mock_play.assert_not_called()

    def test_the_fog_voice_can_be_turned_back_on(self):
        """仕組みは残してある"""
        monitor = self._monitor()
        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True), \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        mock_play.assert_called_once_with("fog.mp3")
        self.assertEqual(SharedState.get_continue_round_count(), 0,
                         "音声を戻してもフリーズは戻らない")

    def test_the_fog_voice_setting_is_still_there(self):
        self.assertTrue(config.VOICE_FOG.endswith("Fog.mp3"))
        self.assertEqual(WindowConfig().voice_fog, "")

    def test_fog_can_be_chosen_for_the_entry_freeze(self):
        self.assertIn("Fog", config.ROUND_FREEZE_SELECTABLE)

    def test_choosing_fog_freezes_every_window(self):
        SharedState.round_freeze_reset()
        SharedState.set_freeze_rounds({"Fog"})
        self.addCleanup(SharedState.set_freeze_rounds, set())
        self.addCleanup(SharedState.round_freeze_reset)
        monitor = self._monitor()

        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertTrue(monitor.st.round_freeze_held)
        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set())
        self.assertEqual(SharedState.get_continue_round_count(), 0,
                         "続行フリーズとは別物")

    def test_fog_is_not_frozen_unless_chosen(self):
        SharedState.round_freeze_reset()
        SharedState.set_freeze_rounds(set())
        monitor = self._monitor()

        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertFalse(monitor.st.round_freeze_held)


class TestFogJoy(unittest.TestCase):
    """霧で「JOY WILL SOON AWAKEN...」が出たらテラーは Joy（alternate 164）"""

    FOG_KEY = "Fog/霧"
    LINE = "2026.09.13 19:51:40 Debug      -  JOY WILL SOON AWAKEN..."

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
        SharedState.set_list_source(None)

    def _monitor(self, keep_on=None, *, instance_type=config.INSTANCE_PRIVATE,
                 round_type="Fog"):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.instance_access = "invite"      # 公開前の霧の情報を使ってよいインスタンス
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _joy(self, monitor):
        seen = []
        real = monitor._on_killers

        def spy(ids, round_type, revealed):
            seen.append(round_type)
            return real(ids, round_type, revealed)

        with patch.object(monitor, "_on_killers", side_effect=spy), \
             patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._process(self.LINE)
        self.passed = seen
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_the_line_is_read(self):
        self.assertEqual(LogParser.parse(self.LINE).kind, LogParser.EVENT_JOY)

    def test_the_id_is_joy(self):
        self.assertEqual(ReadJson.terror_name(config.JOY_ID, config.TERRORS), "Joy")
        self.assertTrue(ReadJson.is_alternate_terror(config.JOY_ID, config.TERRORS))

    def test_fog_is_judged_as_joy(self):
        monitor = self._monitor({self.FOG_KEY: {config.JOY_ID}})

        started = self._joy(monitor)

        self.assertEqual(monitor.st.terror_ids, [config.JOY_ID])
        self.assertTrue(monitor.st.is_continue_round)
        self.assertNotIn("do_skip", started)
        self.assertTrue(any("テラー判明(Joy)" in m for m in monitor.logs),
                        monitor.logs)

    def test_an_unwanted_joy_is_skipped(self):
        monitor = self._monitor()

        self.assertIn("do_skip", self._joy(monitor))

    def test_the_alternate_slot_goes_through_the_argument(self):
        monitor = self._monitor()

        self._joy(monitor)

        self.assertEqual(self.passed, [GroupRound.FOG_ALTERNATE_ROUND_TYPE])
        self.assertEqual(monitor.st.round_type, "Fog")

    def test_yakiimo_falls_to_the_list(self):
        monitor = self._monitor({self.FOG_KEY: {config.JOY_ID}},
                                instance_type=config.INSTANCE_YAKIIMO)

        started = self._joy(monitor)

        self.assertNotIn("do_skip", started, "問答無用スキップにならないこと")
        self.assertTrue(monitor.st.is_continue_round)

    def test_other_rounds_do_nothing(self):
        """実ログ19回のうち18回は霧以外（Alternate/Midnight/Unbound）"""
        for round_type in ("Alternate", "Midnight", "Unbound", "8 Pages"):
            monitor = self._monitor(round_type=round_type)

            started = self._joy(monitor)

            self.assertEqual(started, [], round_type)
            self.assertEqual(self.passed, [], round_type)

    def test_a_known_terror_is_left_alone(self):
        monitor = self._monitor()
        monitor.st.terror_ids = [42]

        self._joy(monitor)

        self.assertEqual(self.passed, [])
        self.assertEqual(monitor.st.terror_ids, [42])

    def test_only_once_per_round(self):
        monitor = self._monitor()
        self._joy(monitor)

        again = self._joy(monitor)

        self.assertEqual(again, [])
        self.assertEqual(self.passed, [])

    def test_a_later_reveal_does_not_decide_again(self):
        monitor = self._monitor()
        first = self._joy(monitor)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._process("Killers have been revealed - 30 0 0 // "
                             "Round type is Fog (Alternate)")

        self.assertEqual(first.count("do_skip"), 1)
        mock_thread.assert_not_called()

    def test_an_enrage_after_joy_does_nothing(self):
        monitor = self._monitor()
        self._joy(monitor)

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_enrage("The Pursuer")

        mock_thread.assert_not_called()
        self.assertEqual(monitor.st.terror_ids, [config.JOY_ID])


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
        """Gigabytes の候補にならない構成（Bloodbath の3体）はすぐ送る"""
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)

        mock_send.assert_called_once_with("Bloodbath", [1, 2, 3], 12, 99, quiet=False)
        self.assertTrue(monitor.st.statistics_sent)

    def test_a_single_classic_waits_for_gigabytes(self):
        """元IDが不定なので、Classic の1体構成はどれも Gigabytes の候補"""
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)

        mock_send.assert_not_called()
        self.assertFalse(monitor.st.statistics_sent)

    def test_a_single_classic_is_sent_at_the_round_end(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)
            monitor._process("Verified Round End")

        mock_send.assert_called_once_with("Classic", [1], 12, 99, quiet=False)

    def test_gigabytes_sends_the_replaced_id(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([1], "Classic", revealed=False)
            monitor._process("The Gigabytes have come.")

        mock_send.assert_called_once_with("Classic", [config.GIGABYTES_ID], 12, 99, quiet=False)

    def test_statistics_are_sent_only_once_per_round(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([1, 2, 3], "Bloodbath", revealed=False)
            monitor._on_killers([4], "Bloodbath", revealed=True)

        mock_send.assert_called_once_with("Bloodbath", [1, 2, 3], 12, 99, quiet=False)
        self.assertEqual(monitor.st.terror_ids, [1, 2, 3, 4])

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
        monitor.cfg.auto_begin = False
        monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            # Classic の1体なので Gigabytes を待つ。送るのは終了時
            mock_send.assert_not_called()
            monitor._process("Verified Round End")

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99, quiet=False)

    def test_bloodthirsty_log_after_killers_updates_delayed_statistics(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            mock_send.assert_not_called()

            monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99, quiet=False)

    def test_bloodthirsty_variant_is_not_limited_to_classic(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Bloodbath", revealed=False)
            mock_send.assert_not_called()
            monitor._process(config.BLOODTHIRSTY_CREATURE_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.BLOODTHIRSTY_CREATURE_ID])
        mock_send.assert_called_once_with("Bloodbath", [config.BLOODTHIRSTY_CREATURE_ID], 12, 99, quiet=False)

    def test_hungry_home_invader_log_after_classic_slender_converts_id(self):
        monitor = self._monitor()

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)
            mock_send.assert_not_called()
            monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_send.assert_called_once_with("Classic", [config.HUNGRY_HOME_INVADER_ID], 12, 99, quiet=False)

    def test_hungry_home_invader_log_before_classic_slender_converts_id(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False
        monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Classic", revealed=False)
            mock_send.assert_not_called()       # Gigabytes 待ち
            monitor._process("Verified Round End")

        self.assertEqual(monitor.st.terror_ids, [config.HUNGRY_HOME_INVADER_ID])
        mock_send.assert_called_once_with("Classic", [config.HUNGRY_HOME_INVADER_ID], 12, 99, quiet=False)

    def test_hungry_home_invader_is_ignored_outside_classic(self):
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.SLENDER_ID], "Bloodbath", revealed=False)
            monitor._process(config.HUNGRY_HOME_INVADER_LOG)

        self.assertEqual(monitor.st.terror_ids, [config.SLENDER_ID])
        self.assertFalse(monitor.st.hungry_home_invader_variant)
        mock_send.assert_called_once_with("Bloodbath", [config.SLENDER_ID], 12, 99, quiet=False)

    def test_curious_creature_statistics_send_on_round_end_if_not_bloodthirsty(self):
        monitor = self._monitor()
        monitor.cfg.auto_begin = False

        with patch.object(ConnectDB, "send_ToNRoundStatistics") as mock_send:
            monitor._on_killers([config.CURIOUS_CREATURE_ID], "Classic", revealed=False)
            mock_send.assert_not_called()

            monitor._process("Verified Round End")

        self.assertEqual(monitor.st.terror_ids, [config.CURIOUS_CREATURE_ID])
        mock_send.assert_called_once_with("Classic", [config.CURIOUS_CREATURE_ID], 12, 99, quiet=False)

    def test_verified_end_does_not_send_statistics(self):
        """待つものが無い構成なら、終了時に改めて送らない"""
        monitor = self._monitor()
        monitor.st.round_type = "Bloodbath"
        monitor.st.terror_ids = [1, 2, 3]

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
