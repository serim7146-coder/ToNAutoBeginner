"""Run のリスポーンの後、真正面を向いているかを撮影で測る目印（DQ。依頼者 2026-10-11）。

目印は階段の上の構造の絵（run_templates/stairs.png。140×90。窓1 867×702 で正面を向いた撮影の
x 368〜508・y 128〜218）。撮影の高さ 8〜45% の帯をグレーにして、絵を 撮影の高さ/702 ×
（0.85〜1.15）に縮めて照合し、いちばん合う所の中心を目印の x とする。
前の「赤い塊」（DO）は、画面右上の自分のアバターの小さな画像（赤い服）を拾って向きを振らせたので
やめた（2026-10-11 窓1・窓5）。大きくずれて目印が画面の端・外にあると一致は 0.5〜0.79 に落ちる
（RUN_FACE_MATCH_MIN 未満は見つからない扱い）。

うまくいかなかったときの撮影は %APPDATA%\\ToNAutoBeginner\\run_face\\ に最新 SHOT_KEEP 枚まで残す
（1/2 に縮めた JPEG。ほかのプレイヤーの名前が写りうるので、不具合報告には Begin の撮影と同じく、
撮影を入れるチェックがあるときだけ添付する）。
"""
import threading
from datetime import datetime
from pathlib import Path

import config
import DebugLog

TEMPLATE_FILE = "run_templates/stairs.png"
BAND = (0.08, 0.45)                 # 探す帯（撮影の高さの割合）
BASE_HEIGHT = 702                   # 絵を切り出した撮影の高さ
SCALES = (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15)
DIR_NAME = "run_face"
SHOT_KEEP = 20

_lock = threading.Lock()
_template = None                    # 灰色の絵。読めなければ ()


def _load():
    global _template
    with _lock:
        if _template is not None:
            return _template
        try:
            import cv2
            import numpy as np
            data = np.fromfile(str(config.resource_path(TEMPLATE_FILE)), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
            _template = img if img is not None else ()
        except Exception:
            _template = ()
        return _template


def find_mark(bgr, template=None):
    """目印の (中心 x, 一致, 倍率)。一致が RUN_FACE_MATCH_MIN 未満・撮れていなければ None"""
    import cv2
    template = _load() if template is None else template
    if template is None or len(template) == 0 or not hasattr(bgr, "shape"):
        return None
    h = bgr.shape[0]
    y0, y1 = int(h * BAND[0]), int(h * BAND[1])
    if y1 <= y0:
        return None
    gray = cv2.cvtColor(bgr[y0:y1], cv2.COLOR_BGR2GRAY)
    best = None
    for f in SCALES:
        s = h / BASE_HEIGHT * f
        t = cv2.resize(template, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        th, tw = t.shape[:2]
        if th < 4 or tw < 4 or th > gray.shape[0] or tw > gray.shape[1]:
            continue
        _lo, score, _lo_at, at = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
        if best is None or score > best[1]:
            best = (at[0] + tw / 2, float(score), s)
    if best is None or best[1] < config.RUN_FACE_MATCH_MIN:
        return None
    return best


def folder() -> Path:
    """置き場所（設定と同じ %APPDATA%\\ToNAutoBeginner の下。テストでは差し替わる）"""
    return Path(config.SETTINGS_PATH).parent / DIR_NAME


def save_shot(bgr, window: int, why: str) -> Path | None:
    """うまくいかなかったときの撮影を、1/2 に縮めた JPEG で残す。古いものは SHOT_KEEP 枚を超えた分を消す"""
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
        DebugLog.write(f"[操作] [窓{window}] Run: 向きの撮影を残した {path.name}")
        return path
    except Exception:
        DebugLog.exception("RunFace.save_shot")
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
        DebugLog.exception("RunFace.files_for")
        return {}
