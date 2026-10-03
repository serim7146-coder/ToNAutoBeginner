"""Begin の位置合わせで BEGIN が見つからなかった撮影を残す（CR）。

窓ごとに最新の2枚だけ: %APPDATA%\\ToNAutoBeginner\\begin_miss\\窓N_1.png・窓N_2.png（古い方から上書き）。
撮影は位置合わせで使ったものをそのまま保存する（撮り直さない）。保存に失敗しても例外を外へ出さない
（位置合わせを止めない）。不具合報告（BugReport）の添付に入れる。
"""
import threading
from pathlib import Path

import config
import DebugLog

KEEP = 2                    # 窓ごとに残す枚数
DIR_NAME = "begin_miss"

_lock = threading.Lock()


def folder() -> Path:
    """置き場所（設定と同じ %APPDATA%\\ToNAutoBeginner の下。テストでは差し替わる）"""
    return Path(config.SETTINGS_PATH).parent / DIR_NAME


def slots(window: int) -> list[Path]:
    return [folder() / f"窓{window}_{n}.png" for n in range(1, KEEP + 1)]


def _next_slot(window: int) -> Path:
    """空きがあればそこ、無ければ一番古いもの"""
    paths = slots(window)
    for path in paths:
        if not path.exists():
            return path
    return min(paths, key=lambda p: p.stat().st_mtime_ns)


def _png(bits: bytes, w: int, h: int):
    if not bits or w <= 0 or h <= 0 or len(bits) < w * h * 4:
        return None
    import cv2
    import numpy as np
    bgra = np.frombuffer(bits, dtype=np.uint8)[:w * h * 4].reshape(h, w, 4)
    ok, buf = cv2.imencode(".png", np.ascontiguousarray(bgra[:, :, :3]))
    return buf.tobytes() if ok else None


def save(window: int, bits: bytes, w: int, h: int, stage: str) -> Path | None:
    """撮影（ScreenCapture.capture_window の BGRA）を PNG で残す。保存先を返す（失敗は None）"""
    try:
        data = _png(bits, w, h)
        if data is None:
            return None
        with _lock:
            folder().mkdir(parents=True, exist_ok=True)
            path = _next_slot(window)
            path.write_bytes(data)      # cv2.imwrite は日本語のパスに書けないのでバイトで書く
        DebugLog.write(f"[操作] [窓{window}] Begin: 見つからなかった撮影を保存 {path.name}"
                       f"（{stage}・{w}x{h}）")
        return path
    except Exception:
        DebugLog.exception("BeginMiss.save")
        return None


def files_for(window) -> dict[str, bytes]:
    """不具合報告に入れる {zip の中の名前: PNG}。window が 0（窓なし）なら全部の窓の分。古い順"""
    try:
        if window:
            paths = [p for p in slots(window) if p.is_file()]
        else:
            paths = [p for p in folder().glob("窓*_*.png") if p.is_file()] if folder().is_dir() else []
        paths.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))
        return {f"{DIR_NAME}/{p.name}": p.read_bytes() for p in paths}
    except Exception:
        DebugLog.exception("BeginMiss.files_for")
        return {}
