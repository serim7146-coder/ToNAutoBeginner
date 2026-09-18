import ctypes
from ctypes import wintypes
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

# 背面キー送信で使う定数。pywin32にラッパが無いので ctypes で user32 を直に叩く
# （win32ui を落としたときと同じ方針。新しい依存は足さない）。
# テストから差し替えられるようモジュール変数に持たせる。
user32 = ctypes.WinDLL("user32", use_last_error=True)
# GetCurrentThreadId は kernel32 側のエクスポート。user32 には無い。
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_ACTIVATE        = 0x0006
WM_NCACTIVATE      = 0x0086
WM_KEYDOWN         = 0x0100
WM_KEYUP           = 0x0101
WA_ACTIVE          = 1
SMTO_ABORTIFHUNG   = 0x0002
MAPVK_VK_TO_VSC_EX = 4
ACTIVATE_TIMEOUT_MS = 200

# VkKeyScanExW は SHORT を返す。既定の c_int のままだと上位バイト（シフト状態）に
# ゴミが混じりうるので、戻り値の型だけ明示しておく。
try:
    user32.VkKeyScanExW.restype = ctypes.c_short
except Exception:
    pass


def _background_key(hwnd: int, key: str):
    """背面送信に要る値 (対象スレッド, VK, 押下の lParam, 離上の lParam)。

    送れない窓・キーなら None。例外は投げない。
    """
    if not hwnd or len(key) != 1:
        return None
    try:
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        if not target_tid:
            return None
        hkl = user32.GetKeyboardLayout(target_tid)
        scan_state = user32.VkKeyScanExW(ctypes.c_wchar(key), hkl)
        if scan_state in (-1, 0xFFFF):
            return None
        vk = scan_state & 0xFF
        if (scan_state >> 8) & 0xFF:
            return None     # Shift併用キーは対象外。黙って別のキーを送らない

        scan = user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC_EX, hkl)
        if not scan:
            return None
        ext = 1 if (scan >> 8) & 0xFF in (0xE0, 0xE1) else 0
        scan &= 0xFF
        lparam_down = 1 | (scan << 16) | (ext << 24)
        lparam_up = lparam_down | (1 << 30) | (1 << 31)
    except Exception:
        return None
    return target_tid, vk, lparam_down, lparam_up


def hold_key_background(hwnd: int, key: str, sec: float) -> bool:
    """フォーカスを奪わずにキーを押しっぱなしにする。送り切れたら True。

    PostMessage だけでは足りない。Unityは GetKeyState / GetKeyboardState でも
    キーを読むため、AttachThreadInput で入力状態を共有したうえで
    SetKeyboardState で対象スレッドのキー状態にも押下を書き込む。

    戻り値は「背面送信を最後まで実行できたか」。ゲーム側が反応したかは見ない
    （それはログ側の既存の仕組みで判定する）。前面の窓は切り替えないので、
    複数の窓から同時に呼んでよい。各呼び出しは自分のスレッドを自分の対象の
    スレッドにだけアタッチし、必ずデタッチして抜ける。
    """
    if sec <= 0.0:
        return False
    params = _background_key(hwnd, key)
    if params is None:
        return False
    target_tid, vk, lparam_down, lparam_up = params
    try:
        if user32.IsIconic(hwnd):
            # 最小化中は送れない。ここで復元すると窓が出てきて背面化の意味が消える
            return False
    except Exception:
        return False

    self_tid = 0            # finally から参照するので先に置く
    attached = False
    try:
        self_tid = kernel32.GetCurrentThreadId()
        attached = bool(user32.AttachThreadInput(self_tid, target_tid, True))
        result = wintypes.DWORD()
        for msg, wparam in ((WM_NCACTIVATE, 1), (WM_ACTIVATE, WA_ACTIVE)):
            user32.SendMessageTimeoutW(hwnd, msg, wparam, 0, SMTO_ABORTIFHUNG,
                                       ACTIVATE_TIMEOUT_MS, ctypes.byref(result))
        user32.SetFocus(hwnd)

        state = (ctypes.c_ubyte * 256)()
        saved = None
        if user32.GetKeyboardState(ctypes.byref(state)):
            saved = bytes(state)
            state[vk] = 0x80
            user32.SetKeyboardState(ctypes.byref(state))

        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam_down)
        time.sleep(sec)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up)

        if saved is not None:
            restore = (ctypes.c_ubyte * 256)(*saved)
            restore[vk] = 0
            user32.SetKeyboardState(ctypes.byref(restore))
        return True
    except Exception:
        return False
    finally:
        # アタッチしたまま抜けると、ユーザーの操作が対象窓へ流れ込む
        if attached:
            user32.AttachThreadInput(self_tid, target_tid, False)


def release_key_background(hwnd: int, key: str) -> bool:
    """押されたままかもしれないキーを、フォーカスを奪わずに離す。

    自爆スレッドは daemon なので、長押しの最中にこのツールが終わると
    WM_KEYUP もキー状態の戻しも走らず、VRChat 側では押されたままになる。
    押していなくても無害（押下が残っていなければキー状態は触らない）。
    最小化中でも送る（離すだけなら窓を出す必要は無い）。例外は投げない。
    """
    params = _background_key(hwnd, key)
    if params is None:
        return False
    target_tid, vk, _lparam_down, lparam_up = params
    self_tid = 0
    attached = False
    try:
        self_tid = kernel32.GetCurrentThreadId()
        attached = bool(user32.AttachThreadInput(self_tid, target_tid, True))
        state = (ctypes.c_ubyte * 256)()
        if user32.GetKeyboardState(ctypes.byref(state)) and state[vk] & 0x80:
            state[vk] = 0
            user32.SetKeyboardState(ctypes.byref(state))
        user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up)
        return True
    except Exception:
        return False
    finally:
        if attached:
            try:
                user32.AttachThreadInput(self_tid, target_tid, False)
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