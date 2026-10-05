"""遊ぶ窓を大きくする（ほぼフルスクリーン）と、続行ラウンドの後に窓の大きさ・位置を戻す。

VRChat の窓は、ほかの窓に完全に隠れると描画が間引かれ、CPU 使用率と FPS が落ちる
（依頼者の実測。優先度を上げても直らない）。なので本物のフルスクリーンにはせず:
  - 大きくする窓: その窓のモニターの作業領域（タスクバーを除く）いっぱいに広げ、下端だけ
    BIG_WINDOW_GAP_PX 空ける
  - その窓に完全に隠れてしまう VRChat の窓: 描画（クライアント領域）の上端が下端のすき間に
    来る位置へ、横に少しずつずらして置く。どの窓も少しは見えるので間引かれない。
    ほかのモニターの窓・はみ出していて隠れない窓は動かさない
同じキーをもう一度押すと元に戻す（mainGUI の「窓を大きくするキー」）。

続行ラウンド（依頼者）: 始まったときの窓の大きさと位置を覚え、終わったら戻す。キーで大きく
した・手で大きくした・VRChat のフルスクリーン（Alt+Enter）のどれでも戻す。フルスクリーンは
Alt+Enter で窓に戻してから位置を直す（実機未確認）。SharedState の続行フリーズの始まりと
終わりから呼ばれる（set_continue_hooks）。
"""
import threading
import time

import win32api
import win32gui

import config
import DebugLog
import SharedState
import VRChatDiscovery
import WindowOperator

# Win32 の定数（テストで pywin32 が差し替わっていても値が決まるよう、ここに持つ）
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
HWND_TOP = 0
SW_RESTORE = 9
MONITOR_DEFAULTTONEAREST = 2
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE = 0x0231, 0x0232
SMTO_ABORTIFHUNG = 0x0002

_lock = threading.Lock()
_big: tuple | None = None          # (大きくした窓, {窓: 元の矩形}, 大きくした矩形)
_saved: dict[int, tuple] = {}      # 続行ラウンドの窓 {窓: 始まったときの矩形}
_log = DebugLog.write


def set_logger(log):
    """画面のログへも出すとき（mainGUI）。既定は debug.log だけ"""
    global _log
    _log = log or DebugLog.write


# ── 計算（純粋） ─────────────────────────────────

def big_rect(work, gap: int, big_margins) -> tuple:
    """大きくする窓の矩形。左右の枠は外へ逃がして描画を端まで届かせ、下端は gap 空ける"""
    left, top, right, bottom = work
    return (left - big_margins[0], top, right + big_margins[2], bottom - gap)


def plan(work, gap: int, big_margins, others):
    """大きくする窓の矩形と、隠れる窓を置く左上を決める。

    work: 作業領域 (左, 上, 右, 下)。big_margins: 大きくする窓の (左, 上, 右, 下) の枠の太さ
    （窓の矩形とクライアント領域の差）。左右の枠は外へ逃がして、描画が端まで届くようにする。
    others: 隠れる窓 [(hwnd, 窓の矩形, クライアント上端までの高さ)]（Z 順の下から上）。
    戻り値: (大きくする窓の矩形, {hwnd: (x, y)})。隠れる窓は、上にある窓ほど右にずらすので、
    それぞれ左端の幅ぶんがすき間に見える
    """
    left, _top, right, bottom = work
    moves = {}
    count = len(others)
    for i, (hwnd, rect, client_top) in enumerate(others):
        width = rect[2] - rect[0]
        span = max(0, (right - left) - width)
        x = left + (span * i // (count - 1) if count > 1 else 0)
        moves[hwnd] = (x, bottom - gap - client_top)
    return big_rect(work, gap, big_margins), moves


def _inside(inner, outer) -> bool:
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


# ── Win32 ────────────────────────────────────────

def _window_rect(hwnd) -> tuple | None:
    try:
        return tuple(win32gui.GetWindowRect(hwnd))
    except Exception:
        return None


def _client_rect(hwnd) -> tuple | None:
    """クライアント領域（描画される所）の画面座標 (左, 上, 右, 下)"""
    try:
        _l, _t, width, height = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
        return (x, y, x + width, y + height)
    except Exception:
        return None


def _margins(hwnd) -> tuple:
    rect, client = _window_rect(hwnd), _client_rect(hwnd)
    if rect is None or client is None:
        return (0, 0, 0, 0)
    return (client[0] - rect[0], client[1] - rect[1], rect[2] - client[2], rect[3] - client[3])


def _monitor(hwnd) -> dict | None:
    try:
        return win32api.GetMonitorInfo(
            win32api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))
    except Exception:
        DebugLog.exception("WindowLayout._monitor")
        return None


def is_fullscreen(hwnd) -> bool:
    """VRChat のフルスクリーン（枠なしでモニターいっぱい）か"""
    try:
        if win32gui.GetWindowLong(hwnd, GWL_STYLE) & WS_CAPTION:
            return False
    except Exception:
        return False
    info = _monitor(hwnd)
    return bool(info) and _window_rect(hwnd) == tuple(info["Monitor"])


def _set_rect(hwnd, rect, flags=SWP_NOZORDER | SWP_NOACTIVATE):
    try:
        if win32gui.IsZoomed(hwnd):
            win32gui.ShowWindow(hwnd, SW_RESTORE)
        left, top, right, bottom = rect
        win32gui.SetWindowPos(hwnd, 0, left, top, right - left, bottom - top, flags)
        return True
    except Exception:
        DebugLog.exception("WindowLayout._set_rect")
        return False


def _notify(hwnd, msg):
    try:
        win32gui.SendMessageTimeout(hwnd, msg, 0, 0, SMTO_ABORTIFHUNG, 200)
    except Exception:
        DebugLog.exception("WindowLayout._notify")


def _resize_like_drag(hwnd, rect) -> bool:
    """手で窓の端をドラッグしたときと同じく、大きさ変更の始まり・終わりの合図で挟んで大きさを変える。
    合図なしで変えると VRChat が元の大きさに戻すことがあった（依頼者: 大きくならない）"""
    _notify(hwnd, WM_ENTERSIZEMOVE)
    ok = _set_rect(hwnd, rect, 0)
    _notify(hwnd, WM_EXITSIZEMOVE)
    return ok


def _near(a, b, tol: int = config.BIG_WINDOW_TOLERANCE_PX) -> bool:
    return a is not None and b is not None and all(abs(x - y) <= tol for x, y in zip(a, b))


def _put_back_others(big):
    """寄せたほかの窓を元の位置へ（大きくした窓は触らない）"""
    for other, rect in big[1].items():
        if other != big[0] and _window_exists(other):
            _set_rect(other, rect)


def _vrchat_windows() -> list[int]:
    """VRChat の窓を Z 順の下から上へ"""
    return VRChatDiscovery.get_vrchat_windows(config.BIG_WINDOW_SCAN_MAX)


# ── 大きくする・戻す（キー） ──────────────────────

def toggle_big() -> bool:
    """大きくしていれば戻す。していなければ、いちばん手前の VRChat を大きくする。
    大きくしたら True"""
    with _lock:
        big = _big
    if big is not None:
        restore_big()
        return False
    windows = _vrchat_windows()
    if not windows:
        _log("[窓] VRChat の窓が見つかりません")
        return False
    return enlarge(windows[-1])


def enlarge(hwnd: int) -> bool:
    global _big
    if is_fullscreen(hwnd):
        _log("[窓] その窓はフルスクリーン中です（Alt+Enter で窓に戻してから使ってください）")
        return False
    try:
        if win32gui.IsZoomed(hwnd):
            win32gui.ShowWindow(hwnd, SW_RESTORE)
    except Exception:
        DebugLog.exception("WindowLayout.enlarge")
    info = _monitor(hwnd)
    rect = _window_rect(hwnd)
    if not info or rect is None:
        _log("[窓] 窓の場所が分かりません")
        return False
    work = tuple(info["Work"])
    gap = config.BIG_WINDOW_GAP_PX
    margins = _margins(hwnd)
    covers = big_rect(work, gap, margins)
    others, originals = [], {hwnd: rect}
    for other in _vrchat_windows():
        if other == hwnd:
            continue
        try:
            if win32gui.IsIconic(other):
                continue
        except Exception:
            continue
        client, other_rect = _client_rect(other), _window_rect(other)
        if client is None or other_rect is None or not _inside(client, covers):
            continue                    # 大きくしても隠れない
        others.append((other, other_rect, client[1] - other_rect[1]))
        originals[other] = other_rect
    target, moves = plan(work, gap, margins, others)
    big = (hwnd, originals, target)
    with _lock:
        _big = big
    for other, (x, y) in moves.items():
        try:
            win32gui.SetWindowPos(other, HWND_TOP, x, y, 0, 0,
                                  SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            DebugLog.exception("WindowLayout.enlarge")
    _resize_like_drag(hwnd, target)
    with SharedState._GLOBAL_ACTION_LOCK:
        WindowOperator.focus_vrchat(hwnd)
    time.sleep(config.BIG_WINDOW_CHECK_SEC)
    actual = _window_rect(hwnd)
    DebugLog.write(f"[窓] 大きくする hwnd={int(hwnd):#x} 元={rect} 目標={target} 結果={actual}")
    if not _near(actual, target):
        with _lock:
            if _big is big:
                _big = None
        _put_back_others(big)
        _log("[窓] ⚠ 大きくできませんでした（VRChat が大きさを戻しました）。寄せた窓は戻しました")
        return False
    _log(f"[窓] 大きくしました（隠れる窓 {len(moves)} 個は下端に少し見えるように寄せました）")
    threading.Thread(target=_watch_big, args=(big,), daemon=True).start()
    return True


def _watch_big(big):
    """大きくした窓の大きさが変わったら（VRChat が戻した・手で変えた・閉じた）、寄せた窓を戻す"""
    global _big
    hwnd, _originals, target = big
    while True:
        time.sleep(config.BIG_WINDOW_WATCH_SEC)
        with _lock:
            if _big is not big:
                return              # キーで戻した・続行ラウンドの終わりで戻した
        if _window_exists(hwnd) and _near(_window_rect(hwnd), target):
            continue
        with _lock:
            if _big is not big:
                return
            _big = None
        _put_back_others(big)
        _log("[窓] 大きくした窓の大きさが変わったので、寄せた窓を元に戻しました")
        return


def restore_big():
    """キーで大きくした窓と、寄せた窓を元に戻す"""
    global _big
    with _lock:
        big, _big = _big, None
    if big is None:
        return
    for hwnd, rect in big[1].items():
        if _window_exists(hwnd):
            _set_rect(hwnd, rect)
    _log("[窓] 元の大きさと位置に戻しました")


def _window_exists(hwnd) -> bool:
    try:
        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        return False


# ── 続行ラウンド（SharedState の続行フリーズから） ─────────

def on_continue_start(hwnd: int):
    """続行ラウンドが始まった。その時の大きさと位置を覚える（続行のフリーズを張った時に1回）"""
    if not hwnd:
        return
    rect = _window_rect(hwnd)
    if rect is None:
        return
    with _lock:
        _saved[hwnd] = rect


def on_continue_end(hwnd: int):
    """続行ラウンドが終わった。裏で戻す（フルスクリーンを戻すのに待つので、呼び出し元を止めない）"""
    if not hwnd:
        return
    threading.Thread(target=restore_after_continue, args=(hwnd,), daemon=True).start()


def restore_after_continue(hwnd: int):
    global _big
    with _lock:
        rect = _saved.pop(hwnd, None)
        big = _big if _big is not None and _big[0] == hwnd else None
        if big is not None:
            _big = None
    if big is not None:
        _put_back_others(big)
        # キーで大きくする前の大きさを優先（続行より前に大きくしていた場合も元へ戻す）
        rect = big[1].get(hwnd, rect)
    if rect is None or not _window_exists(hwnd):
        return
    if _window_rect(hwnd) == rect:
        return
    if is_fullscreen(hwnd):
        _leave_fullscreen(hwnd)
    _set_rect(hwnd, rect)
    _log("[窓] 続行ラウンドが終わったので、元の大きさと位置に戻しました")


def _leave_fullscreen(hwnd: int):
    """Alt+Enter で窓に戻す（前面に出して押す。終わったら前面を返す）"""
    with SharedState._GLOBAL_ACTION_LOCK:
        ok, loan = WindowOperator.borrow_front(hwnd)
        if ok:
            WindowOperator.hold_key("alt+enter", config.CURSOR_LOCK_PRESS_SEC)
            time.sleep(config.FULLSCREEN_LEAVE_WAIT_SEC)
        if loan is not None:
            WindowOperator.return_front(loan)
