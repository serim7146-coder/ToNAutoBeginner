"""このプロセスのメモリ・ハンドル数・スレッド数（FC: Tcl の Event の漏れを見張る。debug.log に 10 分ごと）と、
CPU の時間（FI: プロセス全体・スレッドごと。TaskStats が仕事ごとの内訳に使う）。

psutil は使わない（依存を増やさない）。Windows の API を ctypes で呼ぶ。取れない値は None。
"""
import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)

TH32CS_SNAPTHREAD = 0x00000004
THREAD_QUERY_LIMITED_INFORMATION = 0x0800
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
_kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenThread.restype = wintypes.HANDLE
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.GetThreadTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.GetThreadTimes.restype = wintypes.BOOL
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


def peak_working_set():
    """WS の最大値（バイト）。取れなければ None"""
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.PeakWorkingSetSize


def _cpu_seconds(times) -> float:
    """GetProcessTimes / GetThreadTimes のカーネル＋ユーザー（FILETIME は 100ns 単位）の秒"""
    kernel, user = times
    return (((kernel.dwHighDateTime << 32) | kernel.dwLowDateTime)
            + ((user.dwHighDateTime << 32) | user.dwLowDateTime)) / 1e7


def process_cpu():
    """このプロセスの CPU の時間（カーネル＋ユーザー。秒）。取れなければ None"""
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not _kernel32.GetProcessTimes(_kernel32.GetCurrentProcess(), ctypes.byref(created),
                                     ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return _cpu_seconds((kernel, user))


def thread_cpu(native_id: int):
    """OS のスレッド番号（threading.get_native_id()）のスレッドの CPU の時間（カーネル＋ユーザー。秒）。
    開けなければ None（終わった直後など）"""
    handle = _kernel32.OpenThread(THREAD_QUERY_LIMITED_INFORMATION, False, native_id)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _kernel32.GetThreadTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return _cpu_seconds((kernel, user))
    finally:
        _kernel32.CloseHandle(handle)


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


def line(peak_ws=None, peak_delta=None) -> str:
    """debug.log の 1 行（数字だけ）。末尾は WS の最大値と、前回の行からの増え（TaskStats が前回の値を持つ。
    初回など差が無いときは peak_delta=None で「初回」）"""
    ws, private = memory()
    if peak_ws is None:
        peak_ws = peak_working_set()

    def mb(value):
        return "?" if value is None else f"{value / (1024 * 1024):.1f}MB"
    handles, threads = handle_count(), thread_count()
    peak = "?" if peak_ws is None else f"{peak_ws / (1024 * 1024):.0f}MB"
    rise = "初回" if peak_delta is None else f"前回から {peak_delta / (1024 * 1024):+.0f}"
    return (f"[メモリ] WS {mb(ws)}・Private {mb(private)}・ハンドル {'?' if handles is None else handles}・"
            f"スレッド {'?' if threads is None else threads}・最大 WS {peak}（{rise}）")
