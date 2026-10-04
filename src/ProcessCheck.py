"""プロセスが動いているかを見る。

`tasklist` などの外部プロセスを起こさずに済ませたいので、ctypes で
ToolHelp スナップショットを直に取る（実測で1回あたり約10ms）。
"""

import contextlib
import ctypes
from ctypes import wintypes

TH32CS_SNAPPROCESS = 0x00000002
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
except Exception:
    pass


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
