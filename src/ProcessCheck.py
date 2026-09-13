"""プロセスが動いているかを見る。

`tasklist` などの外部プロセスを起こさずに済ませたいので、ctypes で
ToolHelp スナップショットを直に取る（実測で1回あたり約10ms）。
"""

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


def is_process_running(exe_name: str) -> bool:
    """指定名のプロセスが存在するか。比較は小文字化して完全一致。

    失敗時は False。ここで True に倒すと、呼び出し側は「動いている」と
    信じて古いファイルを読んでしまう。
    """
    if not exe_name:
        return False
    wanted = exe_name.lower()
    snapshot = None
    try:
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            return False
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return False
        while True:
            if entry.szExeFile.lower() == wanted:
                return True
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return False
    except Exception:
        return False
    finally:
        if snapshot and snapshot != INVALID_HANDLE_VALUE:
            try:
                kernel32.CloseHandle(snapshot)
            except Exception:
                pass
