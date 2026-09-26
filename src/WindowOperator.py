import contextlib
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

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def window_cursor_point(hwnd: int) -> tuple | None:
    """その窓の中の、カーソルを置く点。置けないなら None。

    置けないのは、最小化されている窓と、矩形が画面（全モニタ）の外にある窓。
    呼び出し側は None のとき従来の前面化＋クリックへ落とす。
    """
    if not hwnd:
        return None
    try:
        if win32gui.IsIconic(hwnd):
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right <= left or bottom <= top:
            return None
        dx, dy = config.BEGIN_CURSOR_OFFSET
        x = min(left + dx, right - 1)
        y = min(top + dy, bottom - 1)
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if not (vx <= x < vx + vw and vy <= y < vy + vh):
            return None        # 画面の外（別モニタを外した後など）
    except Exception:
        return None
    return (x, y)


def cursor_position() -> tuple | None:
    point = wintypes.POINT()
    try:
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
    except Exception:
        return None
    return (point.x, point.y)


@contextlib.contextmanager
def cursor_over_window(hwnd: int):
    """カーソルをその窓の中へ置き、抜けるときに必ず元の位置へ戻す。

    置けたかを yield する。置けなければ何も動かさずに False を返すので、
    呼び出し側は従来の前面化＋クリックへ落とせる。前面化は一切しない。
    """
    point = window_cursor_point(hwnd)
    if point is None:
        yield False
        return
    before = cursor_position()
    moved = False
    try:
        moved = bool(user32.SetCursorPos(*point))
        if moved:
            time.sleep(config.OPERATOR_WAIT_SEC)
        yield moved
    finally:
        if moved and before is not None:
            try:
                user32.SetCursorPos(*before)      # 例外が出ても必ず戻す
            except Exception:
                pass


def click():
    """前面の窓のクロスヘア位置をクリックする。必ずフォーカスを取ってから呼ぶ。

    OSCが使える窓の Begin は、これではなく「カーソルをその窓の矩形内へ置いて
    /input/UseRight を送る」で押せる（cursor_over_window()。前面化は不要）。
    実測（2026-09-25）: 裏のままカーソルをその窓の矩形の中へ置いて UseRight を
    パルス送信すると Begin が押される。別のウィンドウが上に重なっていても押せる。
    対照として矩形の外へ置くと押せない。つまり条件はフォーカスではなく
    「カーソルがその窓の矩形の中にあること」だった。

    以下は、その条件を満たさないときの話。背面クリックは実現できない。キーは
    hold_key_background() で背面に送れるのに、クリックだけフォーカスが要るのは、
    UnityがマウスボタンをRaw Inputで読むため。
    Raw Inputはカーネルの入力スタックからフォーカスのある窓へ届くもので、
    ユーザーモードから特定の窓へ差し込む口が無い。実機で確認済み:

        背面へ w を送る（PostMessage + AttachThreadInput + SetKeyboardState）
            → 前進する。キーはメッセージと GetKeyState でも読まれるため
        同じ経路で VK_LBUTTON + WM_LBUTTONDOWN/UP をクライアント座標へ送る
            → 反応しない（押しっぱなしの間を空けても同じ）

    以下は検討して否定済み。作り直さないこと:

        DirectInput          デバイスを「読む」APIで、他プロセスへ「送る」口が無い
                             （Send系はフォースフィードバック用）
        仮想マウスドライバ    作れても本物のマウスと同じ扱いになり、行き先は前面の
                             窓のまま。窓を選べないので複窓では意味が無い
        VRChatへのDLL注入     EAC対象。規約違反
        OSC                  OSCClient冒頭の通り、ワールドUIを押す入力が存在しない
        窓ごとに別デスクトップ CreateDesktopなら窓ごとに前面を持てるが、切り替えない
                             と画面が見えなくなるため採用しない（依頼者の判断）
    """
    pydirectinput.mouseDown()
    time.sleep(0.1)
    pydirectinput.mouseUp()
    time.sleep(config.OPERATOR_WAIT_SEC)