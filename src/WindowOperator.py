import time
import win32gui
import win32con
import win32api
import win32process
import keyboard
import pydirectinput

import config


def _attach_and_raise(hwnd: int) -> None:
    """対象ウィンドウのスレッドと入力状態を結び付けてから前面化を要求する。

    SetForegroundWindow は呼び出し元が前面権限を持たない場合 Windows に
    拒否される（マクロ起動直後などに起きる）。AttachThreadInput で
    フォアグラウンドスレッドと入力キューを共有すると要求が通る。
    """
    current = target = fg_thread = 0
    try:
        current = win32api.GetCurrentThreadId()
        target = win32process.GetWindowThreadProcessId(hwnd)[0]
        foreground = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    except Exception:
        pass  # 結び付けができなくても前面化自体は試みる

    attached = []
    for thread in {target, fg_thread}:
        if thread and current and thread != current:
            try:
                win32process.AttachThreadInput(current, thread, True)
                attached.append(thread)
            except Exception:
                pass
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        for thread in attached:
            try:
                win32process.AttachThreadInput(current, thread, False)
            except Exception:
                pass


def focus_window(hwnd: int) -> bool:
    """ウィンドウを前面化する。成功したら True。

    以前は SetForegroundWindow の失敗を握り潰していたため、フォーカスを
    取れないまま次のキー入力・クリックを別のウィンドウへ送っていた。
    戻り値で必ず成否を確認すること。
    """
    if hwnd == 0:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        for attempt in range(config.FOCUS_RETRY_MAX):
            if win32gui.GetForegroundWindow() == hwnd:
                time.sleep(config.OPERATOR_WAIT_SEC)
                return True
            _attach_and_raise(hwnd)
            time.sleep(config.FOCUS_RETRY_WAIT_SEC)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False

def hold_key(key: str, sec: float):
    if sec <= 0.0:
        return
    keyboard.press(key)
    time.sleep(sec)
    keyboard.release(key)
    time.sleep(config.OPERATOR_WAIT_SEC)

def click():
    pydirectinput.mouseDown()
    time.sleep(0.1)
    pydirectinput.mouseUp()
    time.sleep(config.OPERATOR_WAIT_SEC)