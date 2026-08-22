"""
ウィンドウの画素取得（PrintWindow）

第3引数の 2 (PW_RENDERFULLCONTENT) を必ず維持すること。これにより
背景に回っている窓でもキャプチャできる（多重起動での運用に必須）。
Easy Anti-Cheat 下でも取得できる経路。
"""
import win32gui
import win32ui
from ctypes import windll


def capture_window(hwnd: int) -> tuple[bytes, int, int]:
    """PrintWindowでウィンドウの画素を取得する。(bits, 幅, 高さ)。

    bits は BGRA 32bit（bits[i]=B, bits[i+1]=G, bits[i+2]=R）。
    失敗時は (b"", 0, 0)。
    """
    dc = mfc = save = bmp = None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return b"", 0, 0
        dc = win32gui.GetWindowDC(hwnd)
        mfc = win32ui.CreateDCFromHandle(dc)
        save = mfc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc, w, h)
        save.SelectObject(bmp)
        windll.user32.PrintWindow(hwnd, save.GetSafeHdc(), 2)
        bits = bmp.GetBitmapBits(True)
        return bits, w, h
    except Exception:
        return b"", 0, 0
    finally:
        try:
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
            if save is not None:
                save.DeleteDC()
            if mfc is not None:
                mfc.DeleteDC()
            if dc is not None:
                win32gui.ReleaseDC(hwnd, dc)
        except Exception:
            pass
