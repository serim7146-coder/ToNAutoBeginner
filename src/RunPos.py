"""Run の位置合わせのためのデータ集め（EQ。依頼者 2026-10-13）。撮って残すだけで、位置は直さない。

- 基準（ref）: 窓ごとに、マクロを開始してから普通に終わったラウンドが RUN_POS_AFTER_ROUNDS 回たった後の、
  RoundOver から Begin 前の移動までの間に、止まっている（速さ 0.1 未満が 1.0 秒・0.5 秒あけた 2 枚の差が小さい）
  ときに 1 回。窓の大きさが変わったら撮り直す
- Run の後（run）: Run のリスポーン → 後ろへ → 左へ → 向きの直し、の後に 1 枚

同じ窓・同じアバター・同じ窓の大きさの撮影どうしで比べるので、視点の高さ・FOV・窓の大きさに左右されない。
足元に近い床の赤い円は、少し動くだけで画面の位置が大きく変わる（遠い目印の大きさより見分けやすい）。

%APPDATA%\\ToNAutoBeginner\\run_pos\\ に等倍の PNG。名前に窓の番号・時刻・種類・クライアントの大きさ。
基準は窓ごとに新しい RUN_POS_REF_KEEP 枚、Run の後は全部で新しい RUN_POS_RUN_KEEP 枚まで。ほかのプレイヤーの
名前が写りうるので、不具合の報告には入れない。撮影は背面のまま（PrintWindow）で、前面も入力も使わない。
"""
import threading
from datetime import datetime
from pathlib import Path

import config
import DebugLog

DIR_NAME = "run_pos"

_lock = threading.Lock()


def folder() -> Path:
    """置き場所（設定と同じ %APPDATA%\\ToNAutoBeginner の下。テストでは差し替わる）"""
    return Path(config.SETTINGS_PATH).parent / DIR_NAME


def diff(a, b):
    """2 枚の差（グレーにした画素の差の絶対値の平均。0〜255）。比べられなければ None"""
    try:
        if not hasattr(a, "shape") or not hasattr(b, "shape") or a.shape != b.shape:
            return None
        import cv2
        import numpy as np
        ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.int16)
        gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.int16)
        return float(np.abs(ga - gb).mean())
    except Exception:
        DebugLog.exception("RunPos.diff")
        return None


def save(bgr, window: int, kind: str, size) -> Path | None:
    """等倍の PNG で残す（kind は "ref" か "run"）。古いものは残す数を超えた分を消す"""
    try:
        if not hasattr(bgr, "shape"):
            return None
        import cv2
        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            return None
        w, h = size if size else (bgr.shape[1], bgr.shape[0])
        with _lock:
            folder().mkdir(parents=True, exist_ok=True)
            path = folder() / f"{datetime.now():%Y%m%d_%H%M%S_%f}_窓{window}_{kind}_{w}x{h}.png"
            path.write_bytes(buf.tobytes())     # cv2.imwrite は日本語のパスに書けないのでバイトで書く
            if kind == "ref":
                pattern, keep = f"*_窓{window}_ref_*.png", config.RUN_POS_REF_KEEP
            else:
                pattern, keep = "*_run_*.png", config.RUN_POS_RUN_KEEP
            shots = sorted(p for p in folder().glob(pattern) if p.is_file())
            for old in shots[:-keep]:
                old.unlink()
        return path
    except Exception:
        DebugLog.exception("RunPos.save")
        return None
