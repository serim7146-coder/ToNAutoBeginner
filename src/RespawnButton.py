"""Esc のメニュー（Launch Pad）の「リスポーン」のボタンを、撮影から絵で探す（DL）。

絵は 1294×1399 の窓の画像から切り出したもの（respawn_templates/btn_respawn.png・128×50）。
窓の大きさでメニューの大きさも位置も変わるので、位置は決め打ちにせず、倍率を振って探す
（2026-10-10 の実機の窓2: 4 回とも一致 0.995 以上・メニューの無い画面は 0.46）。
"""
import threading

import config

TEMPLATE_FILE = "respawn_templates/btn_respawn.png"
MATCH_MIN = 0.8             # これ未満は「無い」
MATCH_GOOD = 0.95           # これ以上が出たら、残りの倍率は試さない（1 枚 1〜2 秒かかるため）
SCALE_MIN, SCALE_MAX, SCALE_STEP = 0.5, 2.0, 0.05
# 粗く探して一番よかった倍率の前後を細かく探し直す（DO: 867×702 の窓ではボタンが 0.51 倍で写り、
# 0.50 の一致は 0.796 で MATCH_MIN を下回って見つからなかった。0.51 は 0.89）
FINE_SPAN, FINE_STEP = 0.05, 0.01

_lock = threading.Lock()
_template = None            # 灰色の絵。読めなければ ()


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


def available() -> bool:
    return _load() is not None and len(_load()) > 0


def scales() -> list:
    """試す倍率。今の窓（1.0）に近い順"""
    n = int(round((SCALE_MAX - SCALE_MIN) / SCALE_STEP))
    values = [round(SCALE_MIN + i * SCALE_STEP, 2) for i in range(n + 1)]
    return sorted(values, key=lambda s: (abs(s - 1.0), s))


def find(bgr, template=None):
    """撮影（BGR）の中のボタンの (中心 x, 中心 y, 一致, 倍率)。MATCH_MIN 未満なら None"""
    import cv2
    template = _load() if template is None else template
    if template is None or len(template) == 0 or not hasattr(bgr, "shape"):
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    best = None
    for s in scales():
        hit = _match(gray, template, s)
        if hit is not None and (best is None or hit[2] > best[2]):
            best = hit
        if hit is not None and hit[2] >= MATCH_GOOD:
            break
    if best is not None and best[2] < MATCH_GOOD:
        # 粗い刻みの間に当たりがあるかもしれない。前後を細かく探し直す
        center = best[3]
        n = int(round(FINE_SPAN / FINE_STEP))
        for i in range(-n, n + 1):
            s = round(center + i * FINE_STEP, 2)
            if i == 0 or s <= 0:
                continue
            hit = _match(gray, template, s)
            if hit is not None and hit[2] > best[2]:
                best = hit
    if best is None or best[2] < MATCH_MIN:
        return None
    return best


def _match(gray, template, s):
    """倍率 s の絵でいちばん合う所 (中心 x, 中心 y, 一致, 倍率)。絵が小さすぎる・撮影より大きければ None"""
    import cv2
    t = cv2.resize(template, None, fx=s, fy=s,
                   interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    th, tw = t.shape[:2]
    if th < 4 or tw < 4 or th > gray.shape[0] or tw > gray.shape[1]:
        return None
    _lo, score, _lo_at, at = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
    return at[0] + tw / 2, at[1] + th / 2, float(score), s
