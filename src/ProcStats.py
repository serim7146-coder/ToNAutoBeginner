"""このプロセスのメモリ・ハンドル数・スレッド数（FC: Tcl の Event の漏れを見張る。debug.log に 10 分ごと）。

psutil は使わない（依存を増やさない）。Windows の API を ctypes で呼ぶ。取れない値は None。
"""
import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)

TH32CS_SNAPTHREAD = 0x00000004
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]


_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcessId.restype = wintypes.DWORD
_kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetProcessHandleCount.restype = wintypes.BOOL
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
_kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def memory() -> tuple:
    """(WS, Private) のバイト数。取れなければ (None, None)"""
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None, None
    return counters.WorkingSetSize, counters.PrivateUsage


def handle_count():
    count = wintypes.DWORD()
    if not _kernel32.GetProcessHandleCount(_kernel32.GetCurrentProcess(), ctypes.byref(count)):
        return None
    return count.value


def thread_count():
    """このプロセスの OS のスレッド数（Python 以外のスレッドも）"""
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return None
    try:
        pid = _kernel32.GetCurrentProcessId()
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        count = 0
        ok = _kernel32.Thread32First(snap, ctypes.byref(entry))
        while ok:
            if entry.th32OwnerProcessID == pid:
                count += 1
            entry.dwSize = ctypes.sizeof(entry)
            ok = _kernel32.Thread32Next(snap, ctypes.byref(entry))
        return count
    finally:
        _kernel32.CloseHandle(snap)


def line() -> str:
    """debug.log の 1 行（数字だけ）"""
    ws, private = memory()

    def mb(value):
        return "?" if value is None else f"{value / (1024 * 1024):.1f}MB"
    handles, threads = handle_count(), thread_count()
    return (f"[メモリ] WS {mb(ws)}・Private {mb(private)}・ハンドル {'?' if handles is None else handles}・"
            f"スレッド {'?' if threads is None else threads}")
