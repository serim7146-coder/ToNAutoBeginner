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


def hold_keys_background(hwnd: int, keys, stop_event, resend_sec: float) -> bool:
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
            pass
    state = (ctypes.c_ubyte * 256)()
    if user32.GetKeyboardState(ctypes.byref(state)):
        for vk in vks:
            state[vk] = 0
        user32.SetKeyboardState(ctypes.byref(state))


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
        return None, f"カーソルを置けません（窓の位置が取れません: {e}）"
    return (x, y), ""


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
        return 0


def window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


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
        return False


def own_window_hwnd(widget) -> int:
    """Tk の窓の、トップレベルの hwnd。取れなければ 0。

    呼ぶのは update_idletasks() のあと（描画前だと id が確定しない）
    """
    try:
        widget.update_idletasks()
        return toplevel_hwnd(widget.winfo_id())
    except Exception:
        return 0


def toplevel_hwnd(hwnd: int) -> int:
    """Tk の winfo_id() から、本当のトップレベル窓の hwnd を得る。

    winfo_id() が返すのは Tk の子ウィンドウなので、そのまま
    SetWindowDisplayAffinity に渡しても窓全体には効かない。親まで上げる
    """
    if not hwnd:
        return 0
    current = int(hwnd)
    try:
        for _ in range(16):         # 念のため上限を置く（輪を作らない）
            parent = user32.GetParent(current)
            if not parent:
                break
            current = int(parent)
    except Exception:
        return int(hwnd)
    return current


def foreground_hwnd() -> int:
    """いま前面にある窓の hwnd。取れなければ 0。

    テストで差し替えられるように関数にしてある
    """
    try:
        return int(win32gui.GetForegroundWindow() or 0)
    except Exception:
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
    ok = focus_window(hwnd)
    if (not ok or not previous or previous == hwnd
            or previous in SharedState.managed_hwnds()):
        return ok, None
    return ok, FrontLoan(hwnd, previous, cursor)


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
        return False
    if loan.cursor is not None:
        try:
            user32.SetCursorPos(*loan.cursor)
        except Exception:
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
        return None
    if cw <= 0 or ch <= 0:
        return None
    return (cx - left + cw / 2, cy - top + ch / 2)


def cursor_position() -> tuple | None:
    point = wintypes.POINT()
    try:
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
    except Exception:
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
    pydirectinput.mouseDown()
    time.sleep(0.1)
    pydirectinput.mouseUp()
    time.sleep(config.OPERATOR_WAIT_SEC)