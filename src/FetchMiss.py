"""アイテム取得で見つからなかったときの撮影を残す（ER。依頼者 2026-10-13）。

「店の画面が見つかりません」「Have Plush の行が見つかりません」のとき、その時の撮影を
%APPDATA%\\ToNAutoBeginner\\fetch_miss\\ に、窓の番号・時刻・理由を名前にして、1/2 に縮めた JPEG で残す
（新しい SHOT_KEEP 枚まで）。ほかのプレイヤーの名前が写りうるので、不具合の報告には撮影を入れるチェックが
あるときだけ入れる（Begin・Run の向きの撮影と同じ扱い）。
"""
import threading
from datetime import datetime
from pathlib import Path

import config
import DebugLog

DIR_NAME = "fetch_miss"
SHOT_KEEP = 20

_lock = threading.Lock()


def folder() -> Path:
    """置き場所（設定と同じ %APPDATA%\\ToNAutoBeginner の下。テストでは差し替わる）"""
    return Path(config.SETTINGS_PATH).parent / DIR_NAME


def save(bgr, window: int, why: str) -> Path | None:
    """1/2 に縮めた JPEG で残す。古いものは SHOT_KEEP 枚を超えた分を消す"""
    try:
        if not hasattr(bgr, "shape"):
            return None
        import cv2
        small = cv2.resize(bgr, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        with _lock:
            folder().mkdir(parents=True, exist_ok=True)
            path = folder() / f"{datetime.now():%Y%m%d_%H%M%S_%f}_窓{window}_{why}.jpg"
            path.write_bytes(buf.tobytes())     # cv2.imwrite は日本語のパスに書けないのでバイトで書く
            shots = sorted(p for p in folder().glob("*.jpg") if p.is_file())
            for old in shots[:-SHOT_KEEP]:
                old.unlink()
        DebugLog.write(f"[操作] [窓{window}] アイテム取得: 見つからなかった撮影を残した {path.name}")
        return path
    except Exception:
        DebugLog.exception("FetchMiss.save")
        return None


def files_for(window) -> dict[str, bytes]:
    """不具合報告に入れる {zip の中の名前: JPEG}。window が 0（窓なし）なら全部の窓の分。古い順"""
    try:
        if not folder().is_dir():
            return {}
        paths = sorted(p for p in folder().glob("*.jpg") if p.is_file()
                       and (not window or f"_窓{window}_" in p.name))
        return {f"{DIR_NAME}/{p.name}": p.read_bytes() for p in paths}
    except Exception:
        DebugLog.exception("FetchMiss.files_for")
        return {}
