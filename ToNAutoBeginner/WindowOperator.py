import time
import win32gui
import keyboard
import pydirectinput

import config
import SharedState

def focus_window(hwnd: int):
    if hwnd == 0:
        return
    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(config.OPERATOR_WAIT_SEC)
    except Exception:
        pass

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