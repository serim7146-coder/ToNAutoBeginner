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
# いちばん合った倍率の ±FINE_SPAN を FINE_STEP 刻みで探し直す（前後の位置を直す精度のため。範囲 SCALES の
# 中だけ。EE）。0.7〜1.3 に広げると、絵が小さいときに目印でない所とも 0.80〜0.82 で合ってしまうので広げない
# （端の撮影 face_fix2_093431 が 0.70 で 0.811。2026-10-12 技術選定の決定 B）
FINE_SPAN, FINE_STEP = 0.05, 0.01
WIDE_SCALES = tuple(round(0.7 + 0.05 * i, 2) for i in range(13))     # 見つからないときの記録だけ（動きには使わない）
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
    """目印の (中心 x, 一致, 倍率)。一致が RUN_FACE_MATCH_MIN 未満・撮れていなければ None。
    倍率は SCALES で探し、いちばん合った倍率の前後を範囲の中で細かく探し直す"""
    return _search(bgr, template, SCALES, fine=True, threshold=True)


def _search(bgr, template, scales, fine: bool, threshold: bool):
    import cv2
    template = _load() if template is None else template
    if template is None or len(template) == 0 or not hasattr(bgr, "shape"):
        return None
    h = bgr.shape[0]
    y0, y1 = int(h * BAND[0]), int(h * BAND[1])
    if y1 <= y0:
        return None
    gray = cv2.cvtColor(bgr[y0:y1], cv2.COLOR_BGR2GRAY)

    def match(f):
        s = h / BASE_HEIGHT * f
        t = cv2.resize(template, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        th, tw = t.shape[:2]
        if th < 4 or tw < 4 or th > gray.shape[0] or tw > gray.shape[1]:
            return None
        _lo, score, _lo_at, at = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
        return (at[0] + tw / 2, float(score), s), f

    best = _best_of(match, scales)
    if best is None:
        return None
    if fine:
        center = best[1]
        n = int(round(FINE_SPAN / FINE_STEP))
        for i in range(-n, n + 1):
            f = round(center + i * FINE_STEP, 3)
            if i == 0 or not (min(scales) <= f <= max(scales)):
                continue            # 範囲の外は探さない
            hit = match(f)
            if hit is not None and hit[0][1] > best[0][1]:
                best = hit
    if threshold and best[0][1] < config.RUN_FACE_MATCH_MIN:
        return None
    return best[0]


def _best_of(match, scales):
    best = None
    for f in scales:
        hit = match(f)
        if hit is not None and (best is None or hit[0][1] > best[0][1]):
            best = hit
    return best


def best_anywhere(bgr, template=None):
    """見つからなかったときの記録だけ: WIDE_SCALES（0.7〜1.3）で探した一番よい (中心 x, 一致, 倍率)。
    しきい値は見ない。動きには使わない（範囲の外は目印でない所とも合うため）"""
    return _search(bgr, template, WIDE_SCALES, fine=False, threshold=False)


def factor(mark, height: int) -> float:
    """目印の倍率 f（いつもの位置で 1.0）。find_mark の倍率は撮影の高さ / BASE_HEIGHT を掛けたもの"""
    return mark[2] / (height / BASE_HEIGHT)


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
