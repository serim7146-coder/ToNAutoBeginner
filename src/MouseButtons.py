"""マウスのサイドボタン（XButton1・XButton2）をキーバインドに使う（GW）。

`keyboard` はマウスを拾えないので、Windows の低レベルマウスフック（WH_MOUSE_LL）を ctypes で張る。
追加の依存は無い。アプリ起動時に専用の daemon スレッドを 1 本立て（GetMessage のループ）、終了時に
WM_QUIT を送って外す。Windows 以外・張れなかったときは何もしない（キーは今まで通り）。

- 対象はサイドボタンの 2 つだけ。保存する名前は `mouse:x1` / `mouse:x2`、画面とログの表記は「マウス4」「マウス5」。
  左・右・中・ホイールは扱わない。キーとの組み合わせ（Ctrl+マウス4）も作らない
- suppress しない。フックの中では必ず CallNextHookEx を返す（VRChat や他のアプリにもそのまま届く）
- フックの中は記録と通知だけ（Windows は遅いフックを外す）。重い処理は呼び先が Tk のスレッドへ回す
- 購読は keyboard と同じ形（hook・hook_key・unhook・is_pressed）。通知の Event は keyboard の event と同じ属性名
  （name・event_type）だけを持つ。scan_code は持たない（キーの判定に混ざらない）
- 張れていなくても購読は受ける（通知が来ないだけ）
"""

import sys
import threading
from dataclasses import dataclass

import DebugLog
import TaskStats

X1 = "mouse:x1"
X2 = "mouse:x2"
NAMES = {X1: "マウス4", X2: "マウス5"}

WH_MOUSE_LL = 14
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_QUIT = 0x0012
XBUTTON1 = 1
XBUTTON2 = 2
_BUTTON_NAMES = {XBUTTON1: X1, XBUTTON2: X2}

START_WAIT_SEC = 1.0        # start() がフックを張れたかを待つ上限
STOP_WAIT_SEC = 1.0         # stop() がスレッドの終わりを待つ上限


def is_mouse(name) -> bool:
    """保存される名前（mouse:x1・mouse:x2）か。文字列以外は False"""
    return isinstance(name, str) and name in NAMES


def display(name) -> str:
    """画面とログの表記（「マウス4」「マウス5」）"""
    return NAMES[name]


@dataclass
class Event:
    """keyboard の event と同じ属性名。event_type は "down" / "up"。scan_code は持たない"""
    name: str
    event_type: str


class _Hook:
    """購読の目印。name が None なら全部のボタンの通知を受ける"""

    def __init__(self, name, callback):
        self.name = name
        self.callback = callback


_lock = threading.Lock()
_hooks: list = []               # _Hook。購読の一覧（通知は list の写しを回す）
_pressed: set = set()           # いま押されているボタンの名前
_start_lock = threading.Lock()  # start・stop を 1 つずつにする
_thread = None
_thread_id = None
_available = False              # フックが張れているか


# ── 購読（keyboard と同じ形）──────────────────────
def hook(callback) -> _Hook:
    """全部のボタンの押した・離したを受ける"""
    handle = _Hook(None, callback)
    with _lock:
        _hooks.append(handle)
    return handle


def hook_key(name, callback, suppress=False) -> _Hook:
    """そのボタンの押した・離したを受ける。suppress は受けるだけで、いつも通す（keyboard と呼び方を揃えるため）"""
    handle = _Hook(name, callback)
    with _lock:
        _hooks.append(handle)
    return handle


def unhook(handle):
    """購読を外す。無ければ何もしない"""
    with _lock:
        try:
            _hooks.remove(handle)
        except ValueError:
            pass


def is_pressed(name) -> bool:
    with _lock:
        return name in _pressed


def available() -> bool:
    return _available


def _clear():
    """購読と押下の記録を空にする（フックそのものは触らない）"""
    with _lock:
        _hooks.clear()
        _pressed.clear()


def reset_for_tests():
    """テストの後始末用。購読と押下の記録を空に戻す"""
    _clear()


# ── 通知（フックの中から呼ばれる）──────────────────
def _on_hook_event(wParam, mouse_data):
    """サイドボタンの押した・離したを記録して配る。ほかのボタン・ほかのメッセージは何もしない"""
    if wParam == WM_XBUTTONDOWN:
        kind = "down"
    elif wParam == WM_XBUTTONUP:
        kind = "up"
    else:
        return
    name = _BUTTON_NAMES.get((mouse_data >> 16) & 0xFFFF)
    if name is None:
        return
    with _lock:
        if kind == "down":
            _pressed.add(name)
        else:
            _pressed.discard(name)
    _dispatch(Event(name, kind))


def _dispatch(event):
    with _lock:
        handles = list(_hooks)
    for handle in handles:
        if handle.name is not None and handle.name != event.name:
            continue
        try:
            handle.callback(event)
        except Exception:
            DebugLog.exception("MouseButtons._dispatch")


# ── フック本体（Windows だけ）────────────────────
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # WindowOperator とは別に持つ（argtypes をぶつけない）
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _LRESULT = ctypes.c_ssize_t
    _ULONG_PTR = ctypes.c_size_t
    _HOOKPROC = ctypes.CFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", wintypes.POINT),
                    ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", _ULONG_PTR)]

    _user32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
    _user32.SetWindowsHookExW.restype = ctypes.c_void_p
    _user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    _user32.CallNextHookEx.restype = _LRESULT
    _user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                     wintypes.UINT, wintypes.UINT)
    _user32.PeekMessageW.restype = wintypes.BOOL
    _user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.PostThreadMessageW.restype = wintypes.BOOL

    def _proc(nCode, wParam, lParam):
        """フックの本体。必ず CallNextHookEx を返す（suppress しない）"""
        try:
            if nCode >= 0 and wParam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                _on_hook_event(wParam, data.mouseData)
        except Exception:
            DebugLog.exception("MouseButtons._proc")
        return _user32.CallNextHookEx(None, nCode, wParam, lParam)

    # GC で消えると、Windows が消えた関数を呼んで落ちる。モジュールで持ち続ける
    _proc_ref = _HOOKPROC(_proc)

    def _run(ready: threading.Event):
        """専用スレッド。フックを張って GetMessage のループを回し、WM_QUIT で外す"""
        global _thread_id, _available
        handle = None
        try:
            handle = _user32.SetWindowsHookExW(WH_MOUSE_LL, _proc_ref, None, 0)
            if not handle:
                DebugLog.write(f"[MouseButtons] フックを張れませんでした（エラー {ctypes.get_last_error()}）")
                return
            msg = wintypes.MSG()
            _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)     # メッセージの待ち行列を先に作る（WM_QUIT を受けられるように）
            _thread_id = threading.get_native_id()
            _available = True
            ready.set()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass                    # 0（WM_QUIT）か -1（失敗）で抜ける
        except Exception:
            DebugLog.exception("MouseButtons._run")
        finally:
            _available = False
            _thread_id = None
            if handle:
                try:
                    _user32.UnhookWindowsHookEx(handle)
                except Exception:
                    DebugLog.exception("MouseButtons._run.unhook")
            ready.set()                 # 張れなかったときも start() を待たせない

    def start() -> bool:
        """アプリ起動時に 1 回。専用スレッドでフックを張り、張れたかを最大 1 秒待って返す。
        起動済みなら何もしない（今の状態を返す）"""
        global _thread
        with _start_lock:
            if _thread is not None and _thread.is_alive():
                return _available
            ready = threading.Event()
            _thread = TaskStats.thread("MouseButtons", _run, ready, daemon=True)
            _thread.start()
            ready.wait(START_WAIT_SEC)
            return _available

    def stop():
        """アプリ終了時に 1 回。フックを外してスレッドの終わりを待ち、購読と押下の記録を空にする"""
        global _thread
        with _start_lock:
            thread, tid = _thread, _thread_id
            if thread is not None and thread.is_alive() and tid:
                _user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
                thread.join(STOP_WAIT_SEC)
            _thread = None
        _clear()

else:
    def start() -> bool:
        return False

    def stop():
        _clear()
