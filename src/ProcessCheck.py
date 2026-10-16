"""プロセスが動いているかを見る。

`tasklist` などの外部プロセスを起こさずに済ませたいので、ctypes で
ToolHelp スナップショットを直に取る（実測で1回あたり約10ms）。
"""

import contextlib
import ctypes
import os
import threading
import time
from ctypes import wintypes

import config

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
MAX_PATH = 260
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize",              wintypes.DWORD),
        ("cntUsage",            wintypes.DWORD),
        ("th32ProcessID",       wintypes.DWORD),
        ("th32DefaultHeapID",   ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID",        wintypes.DWORD),
        ("cntThreads",          wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase",      ctypes.c_long),
        ("dwFlags",             wintypes.DWORD),
        ("szExeFile",           wintypes.WCHAR * MAX_PATH),
    ]


# テストから差し替えられるようモジュール変数に持たせる
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    # 既定の c_int だと64bitでハンドルが切り詰められる
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
except Exception:
    pass


def _processes():
    """動いているプロセスの (PID, exe 名) を1つずつ返す。取れなければ OSError"""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise OSError("CreateToolhelp32Snapshot に失敗")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return
        while True:
            yield int(entry.th32ProcessID), entry.szExeFile
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return
    finally:
        try:
            kernel32.CloseHandle(snapshot)
        except Exception:
            pass


def process_name(pid: int) -> str | None:
    """PID のプロセスの exe 名（例 ToNUtils.exe）。分からなければ None"""
    if not pid:
        return None
    try:
        with contextlib.closing(_processes()) as processes:
            for found, name in processes:
                if found == pid:
                    return name
    except Exception:
        return None
    return None


def _process_names():
    """動いているプロセスの exe 名（小文字）を1つずつ返す。スナップショットは1回だけ取る。
    取れなければ OSError（呼び出し側が失敗として扱う）"""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise OSError("CreateToolhelp32Snapshot に失敗")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return
        while True:
            yield entry.szExeFile.lower()
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return
    finally:
        try:
            kernel32.CloseHandle(snapshot)
        except Exception:
            pass


def is_process_running(exe_name: str) -> bool:
    """指定名のプロセスが存在するか。比較は小文字化して完全一致。

    失敗時は False。ここで True に倒すと、呼び出し側は「動いている」と
    信じて古いファイルを読んでしまう。
    """
    if not exe_name:
        return False
    wanted = exe_name.lower()
    try:
        # 見つけた時点で抜けても、スナップショットはすぐ閉じる（closing）
        with contextlib.closing(_process_names()) as names:
            return any(name == wanted for name in names)
    except Exception:
        return False


def running_names() -> frozenset | None:
    """動いているプロセスの exe 名（小文字）すべて。いくつもの名前を見るときに、
    スナップショットを名前の数だけ取らずに済ませる。失敗時は None"""
    try:
        return frozenset(_process_names())
    except Exception:
        return None


def pid_of_name(exe_name: str) -> int | None:
    """その名前（小文字にして完全一致）のプロセスの PID を1つ。無ければ・取れなければ None（FO）"""
    if not exe_name:
        return None
    wanted = exe_name.lower()
    try:
        with contextlib.closing(_processes()) as processes:
            for pid, name in processes:
                if name.lower() == wanted:
                    return pid
    except Exception:
        return None
    return None


def _image_name(handle) -> str | None:
    """開いたプロセスの exe のファイル名（小文字）。取れなければ None"""
    size = wintypes.DWORD(1024)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return None
    return os.path.basename(buffer.value).lower() or None


def is_pid_alive(pid: int, exe_name: str = None) -> bool:
    """その PID のプロセスがまだ動いているか（FO）。PID を開いて終了コードを見るだけ（スナップショットは取らない。
    1回 数 µs）。exe_name を渡したら、いまその PID が同じ名前のプロセスかも見る（PID は終わった後に別の
    プロセスへ使い回されるので）。名前はそのプロセスの exe のパスから読み、読めないときだけ process_name で確かめる
    （一覧を1周するので重い。ふつうは通らない）。開けない（アクセス拒否）のは動いている扱い。失敗時は False"""
    if not pid:
        return False
    try:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED      # 管理者権限のプロセスなど。居る
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
                return False
            if not exe_name:
                return True
            image = _image_name(handle)
        finally:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
        if image is None:
            image = process_name(pid)
        return image is not None and image.lower() == exe_name.lower()
    except Exception:
        return False


class RunningNamesCache:
    """プロセスの一覧の取りまとめ（FO。依頼者 2026-10-16「CPU を減らす」）。

    3 秒ごとの 2 つの見張り（主催リストの元・外部ツールのボタン）が別々に一覧を取ると、1 窓の CPU の
    3〜5 割になっていた（一覧 1 回 約 11ms）。ここでは:
    - names(max_age): 前回取ってから PROCESS_LIST_MIN_SEC 以内なら前回の一覧を返す（取り直しは最大でもその間隔に 1 回）
    - watch(exe_name): 前回の一覧で見つけた PID が生きているかだけを見る（1回 数 µs）。消えた・まだ見つけていない
      ときだけ一覧を取り直す（それも PROCESS_LIST_MIN_SEC に 1 回まで。間隔の内なら「居ない」）
    一覧が取れないときは names は None・watch は False（is_process_running と同じ。取り直しは次の呼び出し）。
    どのスレッドから呼んでもよい（ロックの中で取る。2 つの見張りが同時に一覧を取らない）
    """

    def __init__(self, min_sec: float = None, list_fn=None, alive_fn=None, clock=None):
        self._min_sec = config.PROCESS_LIST_MIN_SEC if min_sec is None else min_sec
        self._list = list_fn or _name_pids
        self._alive = alive_fn or is_pid_alive
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._by_name: dict = {}        # 小文字の exe 名 → PID の集合（前回の一覧。消えた PID は見つけ次第外す）
        self._names = None              # 同じ名前の凍結集合（取れなかったら None）
        self._taken_at = None           # 前回の一覧を取った時刻

    def _age_within(self, limit: float) -> bool:
        return self._taken_at is not None and (self._clock() - self._taken_at) < limit

    def _refresh(self):
        """一覧を取り直す（ロックの中で）"""
        try:
            by_name = self._list()
        except Exception:
            by_name = None
        if by_name is None:
            self._by_name, self._names, self._taken_at = {}, None, None
            return
        self._by_name = {name: set(pids) for name, pids in by_name.items()}
        self._names = frozenset(self._by_name)
        self._taken_at = self._clock()

    def names(self, max_age: float = None) -> frozenset | None:
        """動いているプロセスの exe 名（小文字）すべて。前回から max_age 秒（既定・下限は PROCESS_LIST_MIN_SEC）以内なら
        前回の一覧。取れなければ None"""
        limit = self._min_sec if max_age is None else max(max_age, self._min_sec)
        with self._lock:
            if not self._age_within(limit):
                self._refresh()
            return self._names

    def watch(self, exe_name: str) -> bool:
        """その名前のプロセスが動いているか。比較は小文字化して完全一致。取れなければ False"""
        if not exe_name:
            return False
        wanted = exe_name.lower()
        with self._lock:
            pids = self._by_name.get(wanted)
            if pids:
                for pid in sorted(pids):
                    if self._alive(pid, wanted):
                        return True
                    pids.discard(pid)           # 終わった（または別のプロセスに使い回された）
                del self._by_name[wanted]
                self._names = frozenset(self._by_name)
            if self._age_within(self._min_sec):
                return False                    # 取り直したばかり。次の取り直しまでは「居ない」
            self._refresh()
            return wanted in self._by_name


def _name_pids() -> dict | None:
    """動いているプロセスの 小文字の exe 名 → PID の集合。一覧は1回だけ取る。取れなければ None"""
    try:
        by_name = {}
        with contextlib.closing(_processes()) as processes:
            for pid, name in processes:
                by_name.setdefault(name.lower(), set()).add(pid)
        return by_name
    except Exception:
        return None
