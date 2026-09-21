"""設定ファイルに置く秘密（OBS のパスワード）を DPAPI で守る。

Windows の CryptProtectData は、いまのユーザー・この PC でしか復号できない形に
暗号化する。settings.json を持ち出されても平文は読めない。pywin32 の一部なので
依存は増えない。

失敗は例外にせず None を返す（起動や設定保存を止めないため）。
"""
import base64
from typing import Optional

import win32crypt

CRYPTPROTECT_UI_FORBIDDEN = 0x1     # 確認ダイアログを出さない（出せない場面で固まらないように）


def protect(text: str) -> Optional[str]:
    """暗号化して base64 の文字列にする。失敗したら None"""
    try:
        blob = win32crypt.CryptProtectData(
            text.encode("utf-8"), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN)
        return base64.b64encode(blob).decode("ascii")
    except Exception:
        return None


def unprotect(value: str) -> Optional[str]:
    """protect() の逆。別の PC・別のユーザーの値や壊れた値なら None"""
    try:
        blob = base64.b64decode(value, validate=True)
        _description, data = win32crypt.CryptUnprotectData(
            blob, None, None, None, CRYPTPROTECT_UI_FORBIDDEN)
        return data.decode("utf-8")
    except Exception:
        return None
