"""テスト全体の共通の準備。各 tests/test_*.py が `from tests.support import *` で読む。

- Windows 専用のモジュール（win32gui・keyboard・pydirectinput）を偽物に差し替える
- テスト中に作る窓を画面に出さない・本物の OSC / 設定 / DB に触らない（setUpModule・tearDownModule）
- いくつものテストで使う偽物・道具
"""
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent      # src
REPO_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest
import hashlib
import base64
import struct
import socket
from unittest.mock import patch, MagicMock, call, ANY
import itertools
import threading
import time
import sys
import subprocess
import json
import copy
import urllib.request
import urllib.error
import urllib.parse
import tempfile
import gzip
import sqlite3
import ctypes
import io
import contextlib
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
from tkinter import ttk

# ── テスト中に作る窓は画面に出さない・フォーカスを取らない（テストの側だけ） ──
# Windows では新しい窓が作った瞬間に前に出る。流すたびに依頼者の画面の前面に
# 統計画面・メイン画面が出てきて邪魔だったので、Tk・Toplevel（App・StatisticsWindow・
# ReportDialog・Overlay などの派生も）を作った直後に withdraw する。製品のコードが呼ぶ
# deiconify・lift・focus_force・-topmost もテスト中は効かせない
_ORIGINAL_TK_INIT = tk.Tk.__init__
_ORIGINAL_TOPLEVEL_INIT = tk.Toplevel.__init__
_ORIGINAL_WM_ATTRIBUTES = tk.Wm.wm_attributes
_HIDDEN_WINDOWS: list = []          # 隠した窓（確かめるテストが使う）


def _hidden_tk_init(self, *args, **kwargs):
    _ORIGINAL_TK_INIT(self, *args, **kwargs)
    self.withdraw()
    _HIDDEN_WINDOWS.append(self)


def _hidden_toplevel_init(self, *args, **kwargs):
    _ORIGINAL_TOPLEVEL_INIT(self, *args, **kwargs)
    self.withdraw()
    _HIDDEN_WINDOWS.append(self)


def _attributes_without_topmost(self, *args, **kwargs):
    """-topmost だけ効かせない（ほかの属性は今のまま）"""
    if args and str(args[0]) == "-topmost" and len(args) > 1:
        return None
    kwargs.pop("topmost", None)
    return _ORIGINAL_WM_ATTRIBUTES(self, *args, **kwargs)


tk.Tk.__init__ = _hidden_tk_init
tk.Toplevel.__init__ = _hidden_toplevel_init
tk.Wm.deiconify = tk.Wm.wm_deiconify = lambda self: None
tk.Misc.lift = tk.Misc.tkraise = lambda self, aboveThis=None: None
tk.Misc.focus_force = lambda self: None
tk.Wm.attributes = tk.Wm.wm_attributes = _attributes_without_topmost


def _visible_windows_of_this_process() -> list:
    """このプロセスの、画面に見えているトップレベルの窓（EnumWindows＋IsWindowVisible）"""
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pid, found = os.getpid(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found
import ToolLauncher
import HotKey
import ItemFetch
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
import BeginDetect
import BeginMiss
import RoundStore
import VerifiedTracker
import DebugLog
import shutil
import random
import zipfile
import BugReport
import ItemCatalog
import WindowVolume
import ScreenCapture
import ToNEntry
import mainGUI
import UIFont
import AutoUpdate
import Migration
import config
from State import WindowConfig, WindowState


# ── テストから本物の OSC を送らない・本物の受け口を開かない ─────────
# 127.0.0.1:9000 は依頼者の窓1 の VRChat が待っている。テストが本物のソケットで送ると、窓1 が勝手に
# 動く・止まる（1回流すごとに約27万通送っていた）。OSC を使うモジュールの socket を、送ったもの・
# 開こうとした受け口を記録するだけの偽物に差し替える（読み込み時に。どのテストより前）。
# 記録が1つでもあれば tearDownModule で失敗にする（テストの並び順に左右されない）
def _module_time(sleep):
    """そのモジュールの time に差し替える偽物。patch.object(<モジュール>.time, "sleep") は time.sleep
    そのもの（全モジュール共通）を偽物にするので、ほかのモジュールの「時計を見ながら待つ」処理（OSC の
    送り直しなど）が時間の進まないまま空回りする。時計は本物のまま、sleep だけ偽物"""
    fake = type(sys)("time_for_test")
    fake.time = time.time
    fake.monotonic = time.monotonic
    fake.sleep = sleep
    return fake


class _OscSocketTrap:
    sends = []              # (送り先, 先頭のバイト)
    binds = []              # 開こうとした受け口

    class Socket:
        def __init__(self, *args, **kwargs):
            self._timeout = 0.2

        def sendto(self, data, addr):
            _OscSocketTrap.sends.append((addr, bytes(data[:32])))
            return len(data)

        def bind(self, addr):
            _OscSocketTrap.binds.append(addr)

        def settimeout(self, sec):
            self._timeout = sec

        def setsockopt(self, *args):
            pass

        def recvfrom(self, _size):
            # 何も届かない。time.sleep を偽物にしているテストでも空回りしないよう Event で待つ
            threading.Event().wait(self._timeout or 0.2)
            raise socket.timeout()

        def close(self):
            pass

    module = type(sys)("socket_trap")
    module.socket = Socket
    module.timeout = socket.timeout
    module.error = socket.error
    module.AF_INET = socket.AF_INET
    module.SOCK_DGRAM = socket.SOCK_DGRAM
    module.SOL_SOCKET = socket.SOL_SOCKET
    module.SO_REUSEADDR = socket.SO_REUSEADDR

    @classmethod
    def install(cls):
        for mod in (OSCClient, OSCReceiver):
            mod.socket = cls.module

    @classmethod
    def check(cls):
        """本物なら OSC を送っていた・受け口を開いていたテストがあれば失敗（tearDownModule から）"""
        if cls.sends or cls.binds:
            raise AssertionError(
                f"テストが OSC のソケットに届いた（本物なら送っていた）: 送信 {len(cls.sends)} 通"
                f"（先頭 {cls.sends[:3]}）・受け口 {cls.binds[:5]}。send を偽物にするか、移動を偽物にすること")


_OscSocketTrap.install()


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
                       debug=config.DEBUG_LOG_PATH,
                       rounds=config.ROUND_STORE_PATH,
                       trust=FogEarlyRead.trust,
                       items=config.ITEMS)
    # 本物の item.json（依頼者が作成中）の中身にテストを左右させない。表が要るテストは差し替える
    config.ITEMS = {}
    config.SETTINGS_PATH = root / "settings.json"
    config.DEBUG_LOG_PATH = root / "debug.log"
    config.ROUND_STORE_PATH = root / "rounds.sqlite"
    config.FOG_OBJECT_NAMES_PATH = root / "fog_object_names.json"
    # trust は import 時にパスを受け取っている。config を差し替えても効かないので作り直す
    FogEarlyRead.trust = FogEarlyRead.NameTrust(config.FOG_OBJECT_NAMES_PATH)
    # 本物の DB（.env があると Supabase につながる）へ送らない。差し替え忘れた送信は
    # オフラインと同じに失敗させる（urlopen を差し替えるテストは、その間それが勝つ）
    _real_paths["urlopen"] = urllib.request.urlopen
    urllib.request.urlopen = _refuse_network
    # 続行ラウンドの後の操作（DropRight・窓を戻す）は、既定 ON でも、ほかのテストの続行ラウンドで
    # 動かさない（OSC・窓の移動を送らない）。確かめるテストだけが ON にする
    SharedState.set_continue_drop_item(False)
    SharedState.set_continue_restore_window(False)


def _refuse_network(*_args, **_kwargs):
    raise OSError("テスト中は本物のネットワーク（DB）へ送らない")


def tearDownModule():
    urllib.request.urlopen = _real_paths["urlopen"]
    config.SETTINGS_PATH = _real_paths["settings"]
    config.FOG_OBJECT_NAMES_PATH = _real_paths["names"]
    config.DEBUG_LOG_PATH = _real_paths["debug"]
    config.ROUND_STORE_PATH = _real_paths["rounds"]
    FogEarlyRead.trust = _real_paths["trust"]
    config.ITEMS = _real_paths["items"]
    _sandbox.cleanup()
    _OscSocketTrap.check()

ConnectDB.SUPABASE_URL = "https://example.supabase.co"
ConnectDB.SUPABASE_KEY = "test-key"


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


# ── 霧の看破: 実ログ（--enable-sdk-log-levels 付き）から抜き出した固定データ ──
# 依頼者の実ログのパスには依存しない。公開の ID は「オフセット済み」
FOG_EARLY_READ_ROUNDS = [
    # (ログ・時刻, [NetworkProcessing] の行, 公開の行, 公開ID, objects で決まるID)
    ("16-30-13 16:38",
     ["2026.09.21 16:38:23 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [7] THE SUN because y_ui already owner"],
     "2026.09.21 16:39:14 Debug      -  Killers have been revealed - 6 0 0 // Round type is Fog",
     6, 6),
    ("16-30-13 17:20",
     ["2026.09.21 17:20:30 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [32] Paradise Bird (1) because tsuki__2 already owner"],
     "2026.09.21 17:21:20 Debug      -  Killers have been revealed - 12 0 0 // Round type is Fog (Alternate)",
     146, 146),
    ("16-30-13 17:31",
     ["2026.09.21 17:31:15 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [86] WALPURGISNACHT because tsuki__2 already owner",
      "2026.09.21 17:31:33 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [12] witchling (15) because tsuki__2 already owner",
      "2026.09.21 17:31:33 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [12] witchling because tsuki__2 already owner"],
     "2026.09.21 17:32:05 Debug      -  Killers have been revealed - 33 0 0 // Round type is Fog (Alternate)",
     167, 167),
    ("18-59-24 22:04",
     ["2026.09.21 22:04:41 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [20] Immortal Snail because meteor? already owner"],
     "2026.09.21 22:05:31 Debug      -  Killers have been revealed - 101 0 0 // Round type is Fog",
     101, 101),
    ("08-39-27 08:43",
     ["2026.09.21 08:43:40 Debug      -  [NetworkProcessing] serim01 would like to transfer [10] Kuro GuidingStar to すぅみ_suumi"],
     "2026.09.21 08:44:30 Debug      -  Killers have been revealed - 6 0 0 // Round type is Fog (Alternate)",
     140, 140),
    ("18-59-24 20:45",
     ["2026.09.21 20:45:59 Warning    -  [NetworkProcessing] Ignoring TrySetOwner attempt on [29b] SmileyWalker because meteor? already owner",
      "2026.09.21 20:46:01 Debug      -  [NetworkProcessing] Transferred ownership of [29b] SmileyWalker to 5",
      "2026.09.21 20:46:01 Debug      -  [NetworkProcessing] serim01 would like to transfer [29b] SmileyWalker to meteor?",
      "2026.09.21 20:46:01 Error      -  [NetworkProcessing] Non-owner attempted to request ownership of [29b] SmileyWalker for someone else."],
     "2026.09.21 20:46:49 Debug      -  Killers have been revealed - 8 0 0 // Round type is Fog (Alternate)",
     142, 142),
]


def line_after(line, sec, body="."):
    """line のログ時刻から sec 秒後の行"""
    at = datetime.fromtimestamp(LogParser.log_time(line) + sec)
    return at.strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  " + body


def fog_early_read_terrors():
    """terrors.json の該当部分だけ（値を写した）。本物の terrors.json には依存しない。
    "objects" は実ログの [NetworkProcessing] に出た名前"""
    def entry(tid, name, members, objects):
        return {"id": tid, "name": name, "terrors": members, "objects": objects}
    return ReadJson.normalize_terrors({
        "classic": [
            entry(6, "Black Sun", ["The Sun"], ["THE SUN"]),
            entry(12, "An Arbiter", ["An Arbiter"], ["Arbiter"]),
            entry(28, "Smileghost", ["Smileghost"], ["Innyume"]),
            entry(101, "Immortal Snail", ["Immortal Snail"], ["Immortal Snail"]),
            entry(133, "Malicious Twins", ["Malicious Twin"], ["Twin1"]),
        ],
        "alternate": [
            entry(139, "Chomper", ["Chomper"], []),
            entry(140, "The Knight of Toren", ["The Knight Of Toren"], ["Kuro GuidingStar"]),
            entry(142, "Smile Walker", ["Smile Walker"], ["SmileyWalker"]),
            entry(146, "Paradise Bird", ["Paradise Bird"], ["Paradise Bird"]),
            entry(167, "Walpurgisnacht", ["Walpurgisnacht", "Unknown Witch"],
                  ["WALPURGISNACHT", "witchling"]),
        ],
        "unbound": [
            entry(227, "Byte Horde", ["Byte Horde"], ["Duke"]),
        ],
    })


def wish_bits(ids):
    raw = bytearray(40)
    for tid in ids:
        raw[tid // 8] |= 1 << (tid % 8)
    return bytes(raw)


def write_host_state_like_json(path, raw):
    """以前の host_save.json.gz と同じ中身を、いまの SQLite 形式で作る。

    旧形式を読むのをやめた（仕様書 AM）ので、旧形式で書いていたテストを
    同じ意図のまま新形式で確かめるためのもの。読み方は以前の JSON 版と同じ:
    0 以外の整数が ON。ON が1つも無いラウンドはビット列0で書く（リストは
    持っている）。data が辞書でない人は wishes を書かない（持っていない）
    """
    tabs = []
    for tab in (raw.get("tabs") or []) if isinstance(raw, dict) else []:
        members = []
        for section, key in ((0, "participants"), (1, "waiting")):
            for member in tab.get(key) or []:
                data = member.get("data")
                rounds = None
                if isinstance(data, dict):
                    rounds = {}
                    for round_key, slots in data.items():
                        if not isinstance(slots, dict):
                            continue
                        ids = {int(k) for k, v in slots.items()
                               if isinstance(v, int) and v != 0}
                        rounds[round_key] = ids if ids else bytes(40)
                members.append((section, member.get("vrc_name"), rounds))
        tabs.append(members)
    write_host_state(path, tabs)


def write_old_host_save(path, participants):
    """ディスクに残っている古い host_save.json.gz（読まれないことを確かめる用）"""
    members = [{"vrc_name": f"むかしのひと{n}",
                "data": {"Classic/クラシック": {str(n + 50): 1}}}
               for n in range(participants)]
    with gzip.open(str(path), "wb") as f:
        f.write(json.dumps({"version": 5, "tabs": [{"participants": members}]},
                           ensure_ascii=False).encode("utf-8"))


def write_host_state(path, tabs):
    """ToN ListTool の新しい保存形式（SQLite）を作る。
    tabs は [[(section, 名前, {ラウンド: {ID, ...}}), ...], ...]"""
    Path(path).unlink(missing_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.executescript("""
            create table meta(key text primary key, value text);
            create table tabs(tab_index integer primary key, name text,
                              apply_nocontinue integer, nocontinue_targets text,
                              nocontinue_metadata text);
            create table participants(id integer primary key, tab_index integer,
                                      section integer, position integer, vrc_name text,
                                      original_name text, memo text, created_at text,
                                      is_visible integer);
            create table wishes(participant_id integer, round_name text, bits blob,
                                primary key(participant_id, round_name));
            create table wish_counts(tab_index integer, round_name text,
                                     terror_id integer, count integer);
            create table host_survived(tab_index integer, round_name text, terror_id integer);
            create table force_skips(tab_index integer, round_name text, terror_id integer);
        """)
        con.execute("insert into meta values ('version', '6')")
        pid = 0
        for tab_index, members in enumerate(tabs):
            con.execute("insert into tabs(tab_index, name) values (?, ?)",
                        (tab_index, f"タブ {tab_index + 1}"))
            for position, (section, name, rounds) in enumerate(members):
                pid += 1
                con.execute("insert into participants(id, tab_index, section, position,"
                            " vrc_name, is_visible) values (?, ?, ?, ?, ?, 1)",
                            (pid, tab_index, section, position, name))
                for round_name, ids in (rounds or {}).items():
                    bits = ids if isinstance(ids, (bytes, bytearray)) else wish_bits(ids)
                    con.execute("insert into wishes values (?, ?, ?)",
                                (pid, round_name, bits))
        con.commit()
    finally:
        con.close()


def _no_accept_wait(test):
    """差し込み後の受理待ちを0秒にする（受理待ちの長さを見ないテスト用）。

    受理が来ないテストで、試すたびに本物の時間（BEGIN_RETRY_WAIT_SEC）を
    待つとテストが遅くなる。受理待ちそのものは TestDipRetry で見る
    """
    patcher = patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0.0)
    patcher.start()
    test.addCleanup(patcher.stop)


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


def _cv2_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


class _FakeAudio:
    """音声の部分の偽物。volumes は「音声セッションがあるプロセスの音量」"""

    def __init__(self, pids, volumes):
        self.pids = dict(pids)              # hwnd → pid
        self.volumes = dict(volumes)        # pid → 0.0〜1.0
        self.calls = []
        self.error = None
        self.raise_on_set = None
        self.released = 0

    def pid_of(self, hwnd):
        self.calls.append(("pid", hwnd))
        return self.pids.get(hwnd, 0)

    def read_volumes(self, pids):
        self.calls.append(("read", tuple(pids)))
        return {p: self.volumes[p] for p in pids if p in self.volumes}

    def set_volume(self, pid, level):
        self.calls.append(("set", pid, round(level, 3)))
        if self.raise_on_set is not None:
            raise self.raise_on_set
        if pid not in self.volumes:
            return False
        self.volumes[pid] = level
        return True

    def take_error(self):
        error, self.error = self.error, None
        return error

    def release_com(self):
        self.released += 1

    def sets(self):
        return [c for c in self.calls if c[0] == "set"]


def _audio_device_available() -> bool:
    try:
        WindowVolume.read_volumes([])
        return WindowVolume.take_error() is None
    except Exception:
        return False
    finally:
        WindowVolume.release_com()


class _ChaseOsc:
    """OSC の送信を記録する偽物。_GLOBAL_ACTION_LOCK を持っていないことも見る"""

    def __init__(self, test):
        self.test = test
        self.sent = []

    def send(self, address, value):
        self.test.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked(), "前面を奪わないので鍵は取らない")
        self.sent.append((address, value))
        return True

    def stop_all(self, repeat=1):
        self.test.fail("stop_all() は使わない")


def _wait_until(check, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.005)
    return check()


def _item_table():
    return {29: ItemCatalog.Item("Emerald Coil", "Survival", False),
            36: ItemCatalog.Item("Hamburger", "Survival", True)}


def _load_build_script():
    import importlib.util
    path = REPO_ROOT / "build.py"
    spec = importlib.util.spec_from_file_location("build_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeUser32:
    """WindowOperator が使う user32 の代わり。カーソルの動きを記録する"""

    def __init__(self, cursor=(500, 500), screen=(0, 0, 2560, 1440), set_ok=True):
        self.cursor = cursor
        self.screen = screen
        self.set_ok = set_ok
        self.moves: list[tuple] = []

    def GetCursorPos(self, ref):
        point = ref._obj
        point.x, point.y = self.cursor
        return 1

    def SetCursorPos(self, x, y):
        if not self.set_ok:
            return 0
        self.moves.append((x, y))
        self.cursor = (x, y)
        return 1

    def GetSystemMetrics(self, index):
        return self.screen[{WindowOperator.SM_XVIRTUALSCREEN: 0,
                            WindowOperator.SM_YVIRTUALSCREEN: 1,
                            WindowOperator.SM_CXVIRTUALSCREEN: 2,
                            WindowOperator.SM_CYVIRTUALSCREEN: 3}[index]]


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


def eight_pages_terrors(pages):
    """8pages の表だけ差し替えた TERRORS。pages は {ログ番号: list_id | None}"""
    raw = {
        "classic": [{"id": 39, "name": "Rush", "terrors": ["Rush"]},
                    {"id": 85, "name": "Arrival", "terrors": ["Arrival"]}],
        "alternate": [{"id": 135, "name": "Whiteface", "terrors": ["Whiteface"]},
                      {"id": 172, "name": "Baldi", "terrors": ["Baldi"]}],
        "8pages": [{"id": page, "name": f"p{page}", "terrors": [],
                    **({} if list_id is _MISSING else {"list_id": list_id})}
                   for page, list_id in pages.items()],
    }
    return ReadJson.normalize_terrors(raw)


_MISSING = object()


def _freeze_other_continue():
    """別の窓が続行フリーズを張っている状態を作り、解除する関数を返す。

    続行フリーズは窓ごとの保持なので、他窓ぶんは別の WindowState で持つ
    """
    other = WindowState()
    SharedState.continue_round_start(other)
    return lambda: SharedState.continue_round_end(other)


def _decision_threads(mock_thread):
    """立ったデーモンのうち、判定に関わるものだけを返す。

    続行ラウンドの前面化（_focus_this_window_for）は判定と無関係な
    付随機能なので、判定を見るテストからは除く
    """
    return [c.kwargs["target"].__func__.__name__
            for c in mock_thread.call_args_list
            if "target" in c.kwargs
            and c.kwargs["target"].__func__.__name__
            != "_focus_this_window_for"]


def _real_keyboard():
    """本物の keyboard モジュール。support の冒頭で MagicMock に差し替えているので、
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


class _CancelKeyVar:
    def __init__(self, value):
        self._v = value

    def get(self):
        return self._v

    def set(self, value):
        self._v = value


def _with_cancel_key(app, key=config.SUICIDE_CANCEL_KEY):
    """偽の App に自爆キャンセルのキーの設定を足す（重なりの判定は本物）。
    アイテム自動取得のボタン・続行ラウンドの後のチェックの表示もここで足す"""
    app.v_suicide_cancel_key = _CancelKeyVar(key)
    app._refresh_item_fetch_button = lambda: None
    app._refresh_continue_after_checks = lambda: None
    app._refresh_suicide_cancel_key_label = lambda: None
    app._unhook_suicide_cancel_key = lambda: None
    app._suicide_cancel_key_conflict = lambda k: mainGUI.App._suicide_cancel_key_conflict(app, k)
    return app


def uids_list(text):
    return RoundStore.uids_list(text)


class RunNow:
    """threading.Thread の代わり。start() でその場で走らせる（送信などを待たずに確かめる）"""

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


# `from tests.support import *` で、_ で始まる道具・setUpModule・tearDownModule も渡す
__all__ = [name for name in dir() if not name.startswith("__")]
