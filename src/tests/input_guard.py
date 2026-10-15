"""テストが本物の入力を送らないように、テストの土台でいつも見張る（FF。依頼者 2026-10-15: テストを流している間に
窓1 の操作が乗っ取られた。見張りを付けて流すと、test_item_fetch が本物の SetCursorPos を呼んでいた）。

tests/support.py がいちばん最初に読む（どのモジュール・テストよりも前に）。本物に届きそうになったら、本物は呼ばずに
記録し、そのテストを失敗にする（呼ばれたテストの中で。別のスレッドからでも、そのとき動いていたテストに付ける）。
テストの外（テストとテストの間など）で呼ばれた分は、テストのモジュールの終わり（tearDownModule）で失敗にする。

見張るもの:
- UDP の送信先が 127.0.0.1／localhost の 9000〜9100（OSC）
- user32 の SendInput・keybd_event・mouse_event・SetCursorPos・ClipCursor・BlockInput、
  クリップボード（OpenClipboard・SetClipboardData・EmptyClipboard）、winmm の mciSendString・PlaySound、
  AttachThreadInput
- 本物の窓（IsWindow が真で、このプロセスの窓でないもの。テストが作った Tk の窓は通す）への PostMessage・
  SendMessage・SendMessageTimeout・SetForegroundWindow・ShowWindow・SetWindowPos・MoveWindow・BringWindowToTop・
  SetActiveWindow・SetFocus・SwitchToThisWindow
- ctypes のどの WinDLL／windll から引いても（CDLL.__getitem__ を包む）、pywin32 の同じ関数も

偽物にしてある所（support の MagicMock など）はそのまま。本物に届いたときだけ働く。本物を確かめる必要がある
テストは @allow_real_input("理由") で外す（今は無い）。
"""
import contextlib
import ctypes
import ctypes.wintypes as wintypes
import os
import socket
import sys
import threading
import traceback
import unittest

OSC_HOSTS = ("127.0.0.1", "localhost")
OSC_PORTS = range(9000, 9101)
ALWAYS = frozenset({"SendInput", "keybd_event", "mouse_event", "SetCursorPos", "ClipCursor", "BlockInput",
                    "OpenClipboard", "SetClipboardData", "EmptyClipboard", "mciSendStringW", "mciSendStringA",
                    "PlaySoundW", "PlaySoundA", "AttachThreadInput"})
TO_WINDOW = frozenset({"PostMessageW", "PostMessageA", "SendMessageW", "SendMessageA", "SendMessageTimeoutW",
                       "SendMessageTimeoutA", "SetForegroundWindow", "ShowWindow", "SetWindowPos", "MoveWindow",
                       "BringWindowToTop", "SetActiveWindow", "SetFocus", "SwitchToThisWindow"})

_lock = threading.Lock()
hits: list = []                 # [(テストの id か None, 種類, 中身, スレッドの名前, 呼び出し元)]
_state = {"test": None, "allowed": False}

_orig_getitem = ctypes.CDLL.__getitem__
_orig_sendto = socket.socket.sendto
_user32 = ctypes.WinDLL("user32")
_IsWindow = _orig_getitem(_user32, "IsWindow")
_IsWindow.argtypes = [wintypes.HWND]
_GetWindowThreadProcessId = _orig_getitem(_user32, "GetWindowThreadProcessId")
_GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def real_foreign_window(hwnd) -> bool:
    """本物の、ほかのプロセスの窓か（このプロセスの Tk の窓は False）"""
    try:
        handle = int(getattr(hwnd, "value", hwnd) or 0)
        if not handle or not _IsWindow(handle):
            return False
        pid = wintypes.DWORD()
        _GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return pid.value != os.getpid()
    except Exception:
        return False


def _hit(kind: str, detail: str):
    stack = "".join(traceback.format_stack(limit=10)[:-2])
    with _lock:
        hits.append((_state["test"], kind, detail, threading.current_thread().name, stack))


def _allowed() -> bool:
    return _state["allowed"]


def _fake_result(name, args):
    if name == "SendInput":
        return args[0] if args else 0
    if name.startswith("mciSendString"):
        return 0
    return 1


def guarded_call(name, func, args, kwargs):
    """見張りを通して呼ぶ。本物に届くなら記録して、本物は呼ばない"""
    if not _allowed():
        if name in ALWAYS:
            _hit(name, repr(args[:2])[:80])
            return _fake_result(name, args)
        if name in TO_WINDOW and args and real_foreign_window(args[0]):
            _hit(name, f"hwnd={int(getattr(args[0], 'value', args[0]) or 0):#x}（ほかのプロセスの窓）")
            return 1
    return func(*args, **kwargs)


class Guarded:
    """ctypes の関数を包む。argtypes・restype などの代入・読み出しは元の関数へ"""

    def __init__(self, name, func):
        self.__dict__["_name"] = name
        self.__dict__["_func"] = func

    def __call__(self, *args, **kwargs):
        return guarded_call(self._name, self._func, args, kwargs)

    def __getattr__(self, item):
        return getattr(self._func, item)

    def __setattr__(self, item, value):
        setattr(self._func, item, value)


def _getitem(self, name):
    func = _orig_getitem(self, name)
    if isinstance(name, str) and (name in ALWAYS or name in TO_WINDOW):
        return Guarded(name, func)
    return func


def _sendto(self, data, *args):
    if not _allowed():
        addr = args[-1] if args else None
        try:
            if addr and addr[0] in OSC_HOSTS and int(addr[1]) in OSC_PORTS:
                _hit("OSC", f"{addr[0]}:{addr[1]}")
                return len(data)
        except (TypeError, ValueError, IndexError):
            pass
    return _orig_sendto(self, data, *args)


def _wrap_module(mod, names):
    for name in names:
        func = getattr(mod, name, None)
        if func is None or isinstance(func, _PyFunc):
            continue
        key = name + "W" if name in ("PostMessage", "SendMessage", "SendMessageTimeout") else name
        setattr(mod, name, _PyFunc(key, func))


class _PyFunc:
    """pywin32 の関数を包む"""

    def __init__(self, name, func):
        self._name, self._func = name, func

    def __call__(self, *args, **kwargs):
        return guarded_call(self._name, self._func, args, kwargs)


_PYWIN32 = (("win32api", ("keybd_event", "mouse_event", "SetCursorPos", "ClipCursor", "PostMessage",
                          "SendMessage", "SendMessageTimeout")),
            ("win32gui", ("PostMessage", "SendMessage", "SendMessageTimeout", "SetForegroundWindow",
                          "ShowWindow", "SetWindowPos", "MoveWindow", "BringWindowToTop", "SetActiveWindow",
                          "SetFocus")),
            ("win32clipboard", ("OpenClipboard", "SetClipboardData", "EmptyClipboard")),
            ("win32process", ("AttachThreadInput",)))


def install():
    """どのモジュールよりも前に呼ぶ（tests/support.py の先頭）"""
    ctypes.CDLL.__getitem__ = _getitem
    for name in ("user32", "winmm", "kernel32"):       # windll に引いてあれば捨てる（包んだものを引き直す）
        ctypes.windll.__dict__.pop(name, None)
    socket.socket.sendto = _sendto
    for module_name, names in _PYWIN32:
        mod = sys.modules.get(module_name)
        if mod is None:
            try:
                mod = __import__(module_name)
            except ImportError:
                continue
        if isinstance(mod, type(sys)):                   # 偽物（MagicMock）は包まない
            _wrap_module(mod, names)
    unittest.TestCase.run = _run


_orig_run = unittest.TestCase.run


def _run(self, result=None):
    method = getattr(self, self._testMethodName, None)
    with _lock:
        start = len(hits)
    before = dict(_state)               # テストの中で別のテストを回す（見張り自体のテスト）ときのために戻す
    _state["test"], _state["allowed"] = self.id(), bool(getattr(method, "_allow_real_input", False))
    try:
        return _orig_run(self, result)
    finally:
        with _lock:
            mine = [h for h in hits[start:] if h[0] == self.id()]
        _state.update(before)
        if mine and result is not None:
            try:
                raise AssertionError(describe(mine))
            except AssertionError:
                result.addFailure(self, sys.exc_info())


def describe(found) -> str:
    lines = [f"テストが本物の入力に届いた（本物は呼んでいない）: {len(found)} 回"]
    for test, kind, detail, thread, stack in found[:5]:
        lines.append(f"- {kind} {detail}（テスト {test}・スレッド {thread}）\n{stack}")
    return "\n".join(lines)


def check_outside():
    """テストの外で届いた分（tearDownModule から）。あれば失敗"""
    with _lock:
        outside = [h for h in hits if h[0] is None]
        for h in outside:
            hits.remove(h)
    if outside:
        raise AssertionError("テストの外で" + describe(outside))


def allow_real_input(reason: str):
    """そのテストだけ見張りを外す（本物を確かめる必要があるとき。理由を書く）"""
    def mark(func):
        func._allow_real_input = reason
        return func
    return mark


@contextlib.contextmanager
def expect_hits():
    """見張り自体のテスト用: この中で届いた分を集めて、テストの失敗にはしない"""
    with _lock:
        start = len(hits)
    got: list = []
    try:
        yield got
    finally:
        with _lock:
            got.extend(hits[start:])
            del hits[start:]
