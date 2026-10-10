import contextlib
import ctypes
import os
from ctypes import wintypes
import time
import win32gui
import win32con
import win32api
import win32process
import keyboard
import pydirectinput

import config
import DebugLog
import SharedState


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
        DebugLog.exception("WindowOperator._attach_and_raise")
        pass  # 結び付けができなくても前面化自体は試みる

    attached = []
    for thread in {target, fg_thread}:
        if thread and current and thread != current:
            try:
                win32process.AttachThreadInput(current, thread, True)
                attached.append(thread)
            except Exception:
                DebugLog.exception("WindowOperator._attach_and_raise")
                pass
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        DebugLog.exception("WindowOperator._attach_and_raise")
        pass
    finally:
        for thread in attached:
            try:
                win32process.AttachThreadInput(current, thread, False)
            except Exception:
                DebugLog.exception("WindowOperator._attach_and_raise")
                pass


def focus_window(hwnd: int) -> bool:
    """ウィンドウを前面化する。成功したら True（debug.log に成否を書く）"""
    ok = _focus_window(hwnd)
    DebugLog.write(f"[操作] 前面化 hwnd={int(hwnd):#x} → {'成功' if ok else '失敗'}")
    return ok


def _focus_window(hwnd: int) -> bool:
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
        DebugLog.exception("WindowOperator.focus_window")
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
        DebugLog.exception("WindowOperator._background_key")
        return None
    return target_tid, vk, lparam_down, lparam_up


def hold_key_background(hwnd: int, key: str, sec: float, stop=None) -> bool:
    DebugLog.write(f"[操作] 背面キー {key} {sec:.2f}秒 hwnd={int(hwnd):#x}")
    if stop is None:
        return _hold_key_background(hwnd, key, sec)
    return _hold_key_background(hwnd, key, sec, stop)


def _wait_holding(sec: float, stop) -> None:
    """長押しの間待つ。stop（呼ぶと True で止める）があれば、待つ間に見て早めに抜ける"""
    if stop is None:
        time.sleep(sec)
        return
    deadline = time.time() + sec
    while not stop():
        left = deadline - time.time()
        if left <= 0:
            return
        time.sleep(min(HOLD_STOP_POLL_SEC, left))


HOLD_STOP_POLL_SEC = 0.05       # 長押しの途中で止める合図を見る間隔


def _hold_key_background(hwnd: int, key: str, sec: float, stop=None) -> bool:
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
        DebugLog.exception("WindowOperator.hold_key_background")
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
        _wait_holding(sec, stop)        # 止める合図が来たらその場で離す
        user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up)

        if saved is not None:
            restore = (ctypes.c_ubyte * 256)(*saved)
            restore[vk] = 0
            user32.SetKeyboardState(ctypes.byref(restore))
        return True
    except Exception:
        DebugLog.exception("WindowOperator.hold_key_background")
        return False
    finally:
        # アタッチしたまま抜けると、ユーザーの操作が対象窓へ流れ込む
        if attached:
            user32.AttachThreadInput(self_tid, target_tid, False)


def hold_keys_background(hwnd: int, keys, stop_event, resend_sec: float) -> bool:
    DebugLog.write(f"[操作] 背面キー長押しを開始 {'+'.join(keys)} hwnd={int(hwnd):#x}")
    try:
        return _hold_keys_background(hwnd, keys, stop_event, resend_sec)
    finally:
        DebugLog.write(f"[操作] 背面キー長押しを終了 {'+'.join(keys)}")


def _hold_keys_background(hwnd: int, keys, stop_event, resend_sec: float) -> bool:
    """複数のキーを、stop_event が立つまでフォーカスを奪わずに押し続ける。

    hold_key_background() と同じ手順（アタッチ・活性化の通知・SetFocus・
    SetKeyboardState・WM_KEYDOWN）を resend_sec ごとにやり直す。アタッチは
    送り直しのたびに付けて外す（数分つけっぱなしにしない。ほかの窓の自爆の
    キー送信と重なっても互いに影響しないように）。2回目以降の WM_KEYDOWN は
    押しっぱなしの繰り返し（lParam の bit30）。止めるときは全部に WM_KEYUP を
    送り、キー状態から押下を消す（例外でも必ず離し、必ずデタッチする）。
    最小化中・送れないキーなら何もせず False
    """
    params = [_background_key(hwnd, key) for key in keys]
    if not params or any(p is None for p in params):
        return False
    try:
        if user32.IsIconic(hwnd):
            return False
    except Exception:
        DebugLog.exception("WindowOperator.hold_keys_background")
        return False
    target_tid = params[0][0]
    vks = [p[1] for p in params]
    repeat = False
    try:
        while True:
            _with_attached(target_tid, lambda: _press_keys(hwnd, params, repeat))
            repeat = True
            if stop_event.wait(resend_sec):
                return True
    except Exception:
        DebugLog.exception("WindowOperator.hold_keys_background")
        return False
    finally:
        _with_attached(target_tid, lambda: _release_keys(hwnd, params, vks))


def _with_attached(target_tid: int, work):
    """呼んだスレッドを対象のスレッドにアタッチして work() を行い、必ず外す"""
    self_tid = 0
    attached = False
    try:
        self_tid = kernel32.GetCurrentThreadId()
        attached = bool(user32.AttachThreadInput(self_tid, target_tid, True))
        work()
    finally:
        if attached:
            user32.AttachThreadInput(self_tid, target_tid, False)


def _press_keys(hwnd: int, params, repeat: bool):
    result = wintypes.DWORD()
    for msg, wparam in ((WM_NCACTIVATE, 1), (WM_ACTIVATE, WA_ACTIVE)):
        user32.SendMessageTimeoutW(hwnd, msg, wparam, 0, SMTO_ABORTIFHUNG,
                                   ACTIVATE_TIMEOUT_MS, ctypes.byref(result))
    user32.SetFocus(hwnd)
    state = (ctypes.c_ubyte * 256)()
    if user32.GetKeyboardState(ctypes.byref(state)):
        for _tid, vk, _down, _up in params:
            state[vk] = 0x80
        user32.SetKeyboardState(ctypes.byref(state))
    for _tid, vk, lparam_down, _up in params:
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk,
                            lparam_down | (1 << 30) if repeat else lparam_down)


def _release_keys(hwnd: int, params, vks):
    for _tid, vk, _down, lparam_up in params:
        try:
            user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up)
        except Exception:
            DebugLog.exception("WindowOperator._release_keys")
            pass
    state = (ctypes.c_ubyte * 256)()
    if user32.GetKeyboardState(ctypes.byref(state)):
        for vk in vks:
            state[vk] = 0
        user32.SetKeyboardState(ctypes.byref(state))


def release_key_background(hwnd: int, key: str) -> bool:
    DebugLog.write(f"[操作] 背面キーを離す {key} hwnd={int(hwnd):#x}")
    return _release_key_background(hwnd, key)


def _release_key_background(hwnd: int, key: str) -> bool:
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
        DebugLog.exception("WindowOperator.release_key_background")
        return False
    finally:
        if attached:
            try:
                user32.AttachThreadInput(self_tid, target_tid, False)
            except Exception:
                DebugLog.exception("WindowOperator.release_key_background")
                pass


def hold_key(key: str, sec: float):
    DebugLog.write(f"[操作] 前面キー {key} {sec:.2f}秒")
    _hold_key(key, sec)


def _hold_key(key: str, sec: float):
    if sec <= 0.0:
        return
    keyboard.press(key)
    time.sleep(sec)
    keyboard.release(key)
    time.sleep(config.OPERATOR_WAIT_SEC)

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def cursor_target(hwnd: int) -> tuple:
    """(点, 置けない理由) を返す。置けるなら理由は空文字。

    点の決め方は window_cursor_point() の docstring を参照（クライアント領域の
    中央＝照準の位置）。理由も返すのは、見送りが無言だと原因を絞れないため。
    実機で「カーソルも動かさず前面化＋クリックしている」が起きたとき、候補
    （最小化・クライアント領域が0・画面外）を切り分けられなかった。
    """
    if not hwnd:
        return None, "カーソルを置けません（窓がありません）"
    try:
        if win32gui.IsIconic(hwnd):
            return None, "カーソルを置けません（最小化）"
        _cl, _ct, cw, ch = win32gui.GetClientRect(hwnd)
        if cw <= 0 or ch <= 0:
            return None, "カーソルを置けません（クライアント領域が0）"
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        right, bottom = left + cw, top + ch
        dx, dy = config.BEGIN_CURSOR_OFFSET
        x = min(max(left, (left + right) // 2 + dx), right - 1)
        y = min(max(top, (top + bottom) // 2 + dy), bottom - 1)
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if not (vx <= x < vx + vw and vy <= y < vy + vh):
            # 画面の外（別モニタを外した後など）
            return None, (f"カーソルを置けません（画面外: 点 ({x},{y}) "
                          f"画面 ({vx},{vy})-({vx + vw},{vy + vh})）")
    except Exception as e:
        DebugLog.exception("WindowOperator.cursor_target")
        return None, f"カーソルを置けません（窓の位置が取れません: {e}）"
    return (x, y), ""


def cursor_in_client(hwnd: int) -> bool:
    """利用者のカーソルが、その窓のクライアント領域の上にあるか（最小化・取れないときは False）。
    UseRight の連打で Begin が押される状態かを見る"""
    point = cursor_position()
    if not hwnd or point is None:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            return False
        _cl, _ct, cw, ch = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        DebugLog.exception("WindowOperator.cursor_in_client")
        return False
    return left <= point[0] < left + cw and top <= point[1] < top + ch


GA_ROOT = 2


def window_at_point(point: tuple) -> int:
    """その点にある窓（トップレベル）の hwnd。取れなければ 0。

    子ウィンドウが返るので GetAncestor(GA_ROOT) で親まで辿る。

    **差し込み先が覆われているかの判定には使わないこと**（2026-09-27 に撤回）。
    覆われていても Begin は押せる。VS Code が全画面で6窓すべてを覆っていても
    動いていた実例があり、2026-09-25 の実測（別の窓が上に重なっていても押せる）
    とも一致する。判定に使うと、当ツールの GUI 自身が差し込み点を覆っている窓
    （実測で窓1と窓4）が毎ラウンド前面化＋クリックへ落ちる。
    調査のために残してある。
    """
    try:
        child = user32.WindowFromPoint(wintypes.POINT(int(point[0]), int(point[1])))
        if not child:
            return 0
        return int(user32.GetAncestor(child, GA_ROOT) or child)
    except Exception:
        DebugLog.exception("WindowOperator.window_at_point")
        return 0


def window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        DebugLog.exception("WindowOperator.window_title")
        return ""


def window_rect(hwnd: int) -> tuple | None:
    """窓の (左, 上, 右, 下)。取れなければ None（debug.log の環境用）"""
    try:
        return tuple(win32gui.GetWindowRect(hwnd))
    except Exception:
        return None


def window_cursor_point(hwnd: int) -> tuple | None:
    """その窓の Begin ボタンの上に当たる点。置けないなら None。

    Begin は照準の位置にあり、照準はクライアント領域の中央にある。矩形の中
    ならどこでもよいわけではない（実測 2026-09-27。窓の隅では押せなかった）。
    タイトルバーや枠を含めないよう、窓矩形ではなくクライアント領域を使う。

    置けないのは、最小化されている窓と、矩形が画面（全モニタ）の外にある窓。
    呼び出し側は None のとき従来の前面化＋クリックへ落とす。理由まで要るときは
    cursor_target() を使う。
    """
    return cursor_target(hwnd)[0]


GA_ROOT = 2                    # GetAncestor: 親の鎖をたどった一番上（持ち主はたどらない）
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_capture_excluded(hwnd: int, excluded: bool) -> bool:
    """その窓を画面キャプチャから外す（戻すなら excluded=False）。

    WDA_EXCLUDEFROMCAPTURE は「物理モニタにだけ出す」指定。画面には普通に
    見えて操作もできるが、録画・スクリーンショット・画面共有からは、そこだけ
    無かったように抜ける（黒い四角も残らない）。
    Windows 10 2004（build 19041）以降。古い環境では False を返す
    """
    if not hwnd:
        return False
    try:
        affinity = WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE
        return bool(user32.SetWindowDisplayAffinity(int(hwnd), affinity))
    except Exception:
        DebugLog.exception("WindowOperator.set_capture_excluded")
        return False


def own_window_hwnd(widget) -> int:
    """Tk の窓の、トップレベルの hwnd。取れなければ 0。

    呼ぶのは update_idletasks() のあと（描画前だと id が確定しない）
    """
    try:
        widget.update_idletasks()
        return toplevel_hwnd(widget.winfo_id())
    except Exception:
        DebugLog.exception("WindowOperator.own_window_hwnd")
        return 0


def toplevel_hwnd(hwnd: int) -> int:
    """Tk の winfo_id() から、本当のトップレベル窓の hwnd を得る。

    winfo_id() が返すのは Tk の子ウィンドウなので、そのまま
    SetWindowDisplayAffinity に渡しても窓全体には効かない。親まで上げる
    """
    if not hwnd:
        return 0
    # GetAncestor(GA_ROOT) は親だけをたどる。GetParent は、ポップアップの窓（枠なしの
    # オーバーレイ）では親ではなく「持ち主」の窓を返す。それでたどると、オーバーレイの
    # 代わりにメイン画面を覚えてしまい、オーバーレイが録画から外れなかった
    try:
        root = user32.GetAncestor(int(hwnd), GA_ROOT)
        return int(root) if root else int(hwnd)
    except Exception:
        DebugLog.exception("WindowOperator.toplevel_hwnd")
        return int(hwnd)


def foreground_hwnd() -> int:
    """いま前面にある窓の hwnd。取れなければ 0。

    テストで差し替えられるように関数にしてある
    """
    try:
        return int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        DebugLog.exception("WindowOperator.foreground_hwnd")
        return 0


class FrontLoan:
    """ツールが VRChat の窓を前面にしたときに控える、返すための札"""

    def __init__(self, hwnd: int, previous: int, cursor):
        self.hwnd = hwnd            # 前に出した VRChat の窓
        self.previous = previous    # その直前に前面だった窓
        self.cursor = cursor        # その直前のカーソル位置（取れなければ None）

    def give_back(self) -> bool:
        return return_front(self)


def borrow_front(hwnd: int) -> tuple:
    """直前の前面の窓とカーソル位置を控えてから、hwnd を前面化する。

    (前面化できたか, 札) を返す。元の窓が無い・VRChat の窓（管理下の窓。クラス名
    では見ない）・同じ窓なら、返す必要がないので札は None。ツール自身の画面は
    「作業していた窓」なので札を出す
    """
    previous = foreground_hwnd()
    cursor = cursor_position()
    ok = focus_vrchat(hwnd)
    if (not ok or not previous or previous == hwnd
            or previous in SharedState.managed_hwnds()):
        return ok, None
    return ok, FrontLoan(hwnd, previous, cursor)


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20          # TOKEN_INFORMATION_CLASS.TokenElevation


def _process_elevated(pid: int | None) -> bool | None:
    """プロセスが昇格（管理者権限）しているか。pid が None ならこのプロセス。
    開けない・調べられないときは None。ハンドルは型を決めて渡す（int のままだと
    64ビットで上位が欠ける）"""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    a32 = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    a32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    a32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    process = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                              os.getpid() if pid is None else int(pid))
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not a32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevated = wintypes.DWORD()
            size = wintypes.DWORD()
            if not a32.GetTokenInformation(token, TOKEN_ELEVATION_CLASS, ctypes.byref(elevated),
                                           ctypes.sizeof(elevated), ctypes.byref(size)):
                return None
            return bool(elevated.value)
        finally:
            k32.CloseHandle(token)
    finally:
        k32.CloseHandle(process)


def can_bring_to_front(hwnd: int) -> bool:
    """その窓を前面にできるか。相手が昇格（管理者権限）していて、ツール自身は昇格して
    いなければ前面にできない（AttachThreadInput などが「アクセスが拒否されました」になる）。
    相手のプロセスを開けないときも、昇格しているとみなす。ツールが昇格していれば今のまま"""
    try:
        if _process_elevated(None):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        if not pid.value:
            return True                 # 分からない窓は今のまま（返してみる）
        return _process_elevated(pid.value) is False
    except Exception:
        DebugLog.exception("WindowOperator.can_bring_to_front")
        return True


def return_front(loan) -> bool:
    """札の窓へ前面を返す。先にカーソルを戻してから前面にする。返したら True。

    返さない: 札が無い・前面がもう「ツールが前に出した VRChat」ではない（利用者が
    自分で移っているので引き戻さない）・元の窓がもう無い。返さないときはカーソルも
    動かさない。ロック（SharedState._GLOBAL_ACTION_LOCK）は呼び出し側が取る
    """
    if loan is None:
        return False
    if foreground_hwnd() != loan.hwnd:
        return False
    try:
        if not win32gui.IsWindow(loan.previous):
            return False
    except Exception:
        DebugLog.exception("WindowOperator.return_front")
        return False
    if not can_bring_to_front(loan.previous):
        DebugLog.write("[操作] 前面を返す → 相手が管理者権限のため返しません")
        return False
    DebugLog.write(f"[操作] 前面を返す → hwnd={int(loan.previous):#x}")
    if loan.cursor is not None:
        try:
            user32.SetCursorPos(*loan.cursor)
        except Exception:
            DebugLog.exception("WindowOperator.return_front")
            pass
    return focus_window(loan.previous)


def aim_in_window_image(hwnd: int) -> tuple | None:
    """照準（クライアント領域の中央）を、窓の画像（GetWindowRect 基準。
    ScreenCapture.capture_window の座標）で返す。取れなければ None。

    窓表示ではタイトルバーと枠の分だけクライアント領域がずれるので、
    ClientToScreen(0,0) と窓の左上の差を足す
    """
    try:
        left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
        cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
        _l, _t, cw, ch = win32gui.GetClientRect(hwnd)
    except Exception:
        DebugLog.exception("WindowOperator.aim_in_window_image")
        return None
    if cw <= 0 or ch <= 0:
        return None
    return (cx - left + cw / 2, cy - top + ch / 2)


def lock_cursor(hwnd: int) -> bool:
    """前面の VRChat で CURSOR_LOCK_KEY を押して離し、カーソルを中央に固定する（依頼者の実測。
    固定済みでも押してよい）。浮いているかは見ない（Windows のカーソルの表示では判定が外れた）。
    キーは前面の窓へ届くので、その窓が前面のときだけ押す。押したら True"""
    if not config.CURSOR_LOCK_KEY or foreground_hwnd() != hwnd:
        return False
    DebugLog.write(f"[操作] カーソルを固定 → {config.CURSOR_LOCK_KEY} を押して離す "
                   f"hwnd={int(hwnd):#x}")
    try:
        _hold_key(config.CURSOR_LOCK_KEY, config.CURSOR_LOCK_PRESS_SEC)
    except Exception:
        DebugLog.exception("WindowOperator.lock_cursor")
        return False
    return True


def focus_vrchat(hwnd: int) -> bool:
    """VRChat の窓を前面化し、カーソルを中央に固定する"""
    ok = focus_window(hwnd)
    if ok:
        lock_cursor(hwnd)
    return ok


def client_height(hwnd: int) -> int | None:
    """窓のクライアント領域の高さ（GetClientRect）。取れなければ None"""
    try:
        _l, _t, _w, height = win32gui.GetClientRect(hwnd)
    except Exception:
        DebugLog.exception("WindowOperator.client_height")
        return None
    return int(height) if height and height > 0 else None


def window_state(hwnd: int) -> str | None:
    """窓の状態。"normal"・"maximized"・"minimized"・"fullscreen"（窓の矩形＝モニターの矩形で
    タイトルバーなし）。窓が無い・取れなければ None"""
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        if win32gui.IsIconic(hwnd):
            return "minimized"
        if win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
            return "maximized"
        if _is_fullscreen(hwnd):
            return "fullscreen"
        return "normal"
    except Exception:
        DebugLog.exception("WindowOperator.window_state")
        return None


def _is_fullscreen(hwnd: int) -> bool:
    if win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_CAPTION:
        return False                    # タイトルバーがある
    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    return tuple(win32api.GetMonitorInfo(monitor)["Monitor"]) == tuple(win32gui.GetWindowRect(hwnd))


def normal_rect(hwnd: int) -> tuple | None:
    """最大化・最小化の前の、元の矩形（GetWindowPlacement の rcNormalPosition）"""
    try:
        return tuple(int(v) for v in win32gui.GetWindowPlacement(hwnd)[4])
    except Exception:
        DebugLog.exception("WindowOperator.normal_rect")
        return None


def move_window(hwnd: int, left: int, top: int) -> bool:
    """窓の位置だけ動かす（大きさ・重なり順・前面は変えない）"""
    try:
        win32gui.SetWindowPos(hwnd, 0, int(left), int(top), 0, 0,
                              win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    except Exception:
        DebugLog.exception("WindowOperator.move_window")
        return False
    DebugLog.write(f"[操作] 窓の位置を動かす hwnd={int(hwnd):#x} → ({int(left)}, {int(top)})")
    return True


def restore_without_activating(hwnd: int) -> bool:
    """最大化を元の矩形に戻す。前面は奪わない（SetWindowPlacement に SW_SHOWNOACTIVATE。
    ShowWindow(SW_RESTORE) は前面にした: 2026-10-10 の実機）。それでも前面になったら、前の窓へ返す"""
    before = foreground_hwnd()
    try:
        flags, _show, pt_min, pt_max, normal = win32gui.GetWindowPlacement(hwnd)
        win32gui.SetWindowPlacement(hwnd, (flags, win32con.SW_SHOWNOACTIVATE, pt_min, pt_max, normal))
    except Exception:
        DebugLog.exception("WindowOperator.restore_without_activating")
        return False
    DebugLog.write(f"[操作] 最大化を解除（前面にしない） hwnd={int(hwnd):#x} → {tuple(normal)}")
    if before and before != hwnd and foreground_hwnd() == hwnd:
        DebugLog.write(f"[操作] 最大化を解除したら前面になった → hwnd={int(before):#x} へ返す")
        focus_window(before)
    return True


def send_keys(keys: str):
    """前面の窓へキーの組み合わせを押して離す（例 "alt+enter"）"""
    DebugLog.write(f"[操作] 前面キー {keys}")
    keyboard.send(keys)


def set_cursor_position(point) -> bool:
    """Windows のカーソルを画面の point へ置く。置けたら True"""
    try:
        return bool(user32.SetCursorPos(int(point[0]), int(point[1])))
    except Exception:
        DebugLog.exception("WindowOperator.set_cursor_position")
        return False


def window_origin(hwnd: int) -> tuple | None:
    """窓の左上（GetWindowRect。ScreenCapture.capture_window の撮影の (0, 0) が画面のどこか）"""
    try:
        left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        DebugLog.exception("WindowOperator.window_origin")
        return None
    return left, top


def cursor_position() -> tuple | None:
    point = wintypes.POINT()
    try:
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
    except Exception:
        DebugLog.exception("WindowOperator.cursor_position")
        return None
    return (point.x, point.y)


@contextlib.contextmanager
def cursor_over_window(hwnd: int, on_reason=None):
    """カーソルをその窓の Begin ボタンの上へ置き、抜けるときに必ず元へ戻す。

    置けたかを yield する。置けなければ何も動かさずに False を返すので、
    呼び出し側は従来の前面化＋クリックへ落とせる。前面化は一切しない。

    見送るときは on_reason(理由) を1回だけ呼ぶ。無言で落ちると、実機で
    「カーソルも動かさずに前面化＋クリックしている」が起きたときに原因を
    絞れない（点が出せない／SetCursorPos 失敗／読み返し不一致のどれか）。
    """
    point, reason = cursor_target(hwnd)
    if point is None:
        _say(on_reason, reason)
        yield False
        return
    before = cursor_position()
    moved = landed = False
    try:
        ctypes.set_last_error(0)
        moved = bool(user32.SetCursorPos(*point))
        if not moved:
            _say(on_reason, "カーソルを動かせません"
                            f"（SetCursorPos 失敗 err={ctypes.get_last_error()}）")
        else:
            # 読み返しは待つ前に行う。待ってから読むと、その 0.05 秒の間に
            # 利用者が自分の手でマウスを動かしたときに「置けなかった」と
            # 誤判定する。見たいのは SetCursorPos が効いたかどうかだけ。
            # 動かし**に行った**か（moved）とは別に持つ——ずれていても、
            # 動かしに行ったなら finally で必ず元へ戻す
            landed = _cursor_landed(point)
            if landed:
                time.sleep(config.OPERATOR_WAIT_SEC)
            else:
                _say(on_reason, "カーソルが置けていません"
                                f"（頼んだ点 {point} → 実際 {cursor_position()}）")
        yield landed
    finally:
        if moved and before is not None:
            try:
                user32.SetCursorPos(*before)      # 例外が出ても必ず戻す
            except Exception:
                DebugLog.exception("WindowOperator.cursor_over_window")
                pass


def _say(on_reason, reason: str):
    if on_reason is not None and reason:
        on_reason(reason)


CURSOR_LANDED_SLACK_PX = 2


def _cursor_landed(point: tuple) -> bool:
    """頼んだ点に本当に置けたか、読み返して確かめる。

    SetCursorPos が成功を返しても、ほかのアプリがマウスを掴んでいると実際には
    動かない（前面の VRChat がその代表）。置けていないまま UseRight を送っても
    Begin は押されないので、呼び出し側をフォールバックへ落とす。
    ±CURSOR_LANDED_SLACK_PX は端数の丸め（DPI スケーリングなど）の余裕
    """
    now = cursor_position()
    if now is None:
        return False
    return (abs(now[0] - point[0]) <= CURSOR_LANDED_SLACK_PX
            and abs(now[1] - point[1]) <= CURSOR_LANDED_SLACK_PX)


def click():
    _click()


def click_with_tab(hold_sec: float, still_front=None, pause: bool = True) -> bool:
    """Begin・ToN 入室のクリックの入口。前面の窓で Tab を押したまま左クリックする:
    Tab を押す → CLICK_TAB_LEAD_SEC 待つ → mouseDown → hold_sec → mouseUp → Tab を離す。
    Tab を押すとカーソルが照準（真ん中）へ戻る（依頼者）。Tab は何があっても離す。
    キーは lock_cursor() と同じ keyboard で送る（前面の VRChat に Tab が効いている経路）。
    アイテム取得は Tab を店の操作の間ずっと押したままにする（press_tab・release_tab・mouse_click）。

    still_front（呼ぶと前面がまだその窓か）を渡すと、待った後にもう一度見て、違えば
    クリックせずに Tab を離して False を返す。クリックしたら True。
    pause は pydirectinput の _pause"""
    key = config.CLICK_TAB_KEY
    keyboard.press(key)
    try:
        if config.CLICK_TAB_LEAD_SEC > 0:
            time.sleep(config.CLICK_TAB_LEAD_SEC)
        if still_front is not None and not still_front():
            DebugLog.write("[操作] クリックしない（Tab の後に前面でなくなった）")
            return False
        DebugLog.write("[操作] クリック（Tab の後）")
        pydirectinput.mouseDown(_pause=pause)
        time.sleep(hold_sec)
        pydirectinput.mouseUp(_pause=pause)
    finally:
        keyboard.release(key)
    return True


def press_tab():
    """Tab を押す（離すまで押したまま。アイテム取得の店の操作）。前面の窓へ届く"""
    DebugLog.write("[操作] Tab を押した（離すまで押したまま）")
    keyboard.press(config.CLICK_TAB_KEY)


def release_tab():
    DebugLog.write("[操作] Tab を離した")
    keyboard.release(config.CLICK_TAB_KEY)


def mouse_click(hold_sec: float, pause: bool = False):
    """前面の窓の今のカーソルの位置を左クリックする（Tab は呼び出し側が押している）"""
    pydirectinput.mouseDown(_pause=pause)
    time.sleep(hold_sec)
    pydirectinput.mouseUp(_pause=pause)


def _click():
    """前面の窓のクロスヘア位置をクリックする。必ずフォーカスを取ってから呼ぶ。

    OSCが使える窓の Begin は、これではなく「カーソルをその窓の Begin ボタンの
    上へ置いて /input/UseRight を送る」で押せる（cursor_over_window()。前面化は
    不要）。条件はフォーカスではなく「カーソルが Begin のボタンの上にあること」。
    デスクトップの照準はクライアント領域の中央にあるので、そこへ置く。

    矩形の中ならどこでもよい、ではない。ここには一度そう書いてあり、それを
    根拠に窓の左上（+20,60。実測の窓 1294x1399 では相対 1.5%, 4.3%）へ置く
    実装が入って、背面では一度も押せていなかった。実測は次のとおり:

        2026-09-25  裏のままカーソルを窓の中央へ置いて UseRight をパルス送信
                    すると Begin が押された。別の窓が上に重なっていても押せる。
                    対照として矩形の外へ置くと押せない
                    （このとき試したのは中央だけで、隅は試していない）
        2026-09-27  2窓へ UseRight を送りながら依頼者が手でカーソルを動かした。
                    Begin の画面の上に置いたときだけ押せた。隅では押せない

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
    click_with_tab(0.1)
    time.sleep(config.OPERATOR_WAIT_SEC)