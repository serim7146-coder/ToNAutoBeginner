"""Run のリスポーンの後、真正面を向いているかを撮影で測る目印（DQ。依頼者 2026-10-11）。

目印は階段の上の構造の絵（assets/templates/run/stairs.png。140×90。窓1 867×702 で正面を向いた撮影の
x 368〜508・y 128〜218）。撮影の高さ 8〜45% の帯をグレーにして、絵を 撮影の高さ/702 ×
（0.85〜1.15）に縮めて照合し、いちばん合う所の中心を目印の x とする。
前の「赤い塊」（DO）は、画面右上の自分のアバターの小さな画像（赤い服）を拾って向きを振らせたので
やめた（2026-10-11 窓1・窓5）。大きくずれて目印が画面の端・外にあると一致は 0.5〜0.79 に落ちる
（RUN_FACE_MATCH_MIN 未満は見つからない扱い）。

FK: 目印の見かけの大きさは FOV で変わる（FOV 100 の絵に対して 75 なら 1.55 倍・60 なら 2.06 倍）ので、絵の照合
（倍率 0.85〜1.15）では多くの人が見つからない。そこで先に、玉座＋階段の切り出し（assets/templates/run/throne.png。
270×270）を特徴点（SIFT＋ホモグラフィ）で探して、玉座の軸の x を目印にする（倍率を決めずに見つかる）。
受け入れなかったときだけ、今までの絵の照合を保険に使う。閾値の根拠は config の RUN_FACE_FEATURE_*。

うまくいかなかったときの撮影は %APPDATA%\\ToNAutoBeginner\\run_face\\ に最新 SHOT_KEEP 枚まで残す
（1/2 に縮めた JPEG。ほかのプレイヤーの名前が写りうるので、不具合報告には Begin の撮影と同じく、
撮影を入れるチェックがあるときだけ添付する）。
"""
import threading
from datetime import datetime
from pathlib import Path

import config
import DebugLog

TEMPLATE_FILE = "assets/templates/run/stairs.png"
THRONE_FILE = "assets/templates/run/throne.png"     # FK: 特徴点で探す玉座＋階段の切り出し（270×270）
# 切り出しの中の玉座の軸の x（依頼者が拡大図で選んだ線。切り出し元の撮影の x 428 − 切り出しの左端 293）と、
# 切り出し元の撮影（851×664 の窓。題名バー込みで 867×703）の高さ。軸の y は切り出しの真ん中でよい
THRONE_AXIS_X = 135
THRONE_REF_HEIGHT = 703
FEATURE_MIN_H = 1000                # 撮影の高さがこれ未満なら、これに拡大して（INTER_CUBIC）特徴点を探す
FEATURE_REF_H = 600                 # 参照（切り出し）も同じ前処理でこの高さに拡大する
FEATURE_CLAHE = (3.0, (8, 8))       # コントラストの補正（clipLimit, tileGridSize）。暗い赤い画面で特徴点を増やす
FEATURE_CONTRAST = 0.02             # SIFT の contrastThreshold（既定 0.04 より低くして暗い所の点を拾う）
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
_throne = None                      # 玉座の ((高さ, 幅), 特徴点の座標, 記述子)。読めなければ ()


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
    """目印の (中心 x, 一致, 倍率, どちらで見つけたか)。先に特徴点で玉座を探し（FK。一致はインライア数・
    "features"）、受け入れなかったときだけ絵の照合（一致が RUN_FACE_MATCH_MIN 以上。"template"）を試す。
    どちらも無い・撮れていなければ None。絵の照合の倍率は SCALES で探し、いちばん合った倍率の前後を
    範囲の中で細かく探し直す"""
    hit = find_by_features(bgr)
    if hit is not None:
        return (*hit, "features")
    found = _search(bgr, template, SCALES, fine=True, threshold=True)
    return None if found is None else (*found, "template")


def how(mark) -> str:
    """find_mark の返り値をどちらで見つけたか（"features"・"template"）。4 つ目が無い形
    （best_anywhere の記録だけのものなど）は "template" """
    return mark[3] if len(mark) > 3 else "template"


def describe(mark) -> str:
    """debug.log の「一致」の書き分け: 特徴点なら「特徴点 インライア n」、絵の照合なら「一致 0.xxx」"""
    return f"特徴点 インライア {int(mark[1])}" if how(mark) == "features" else f"一致 {mark[1]:.3f}"


def _load_throne():
    """玉座の切り出しの特徴点を 1 回だけ作る（参照は変わらない）。読めなければ ()"""
    global _throne
    with _lock:
        if _throne is not None:
            return _throne
        try:
            import cv2
            import numpy as np
            data = np.fromfile(str(config.resource_path(THRONE_FILE)), dtype=np.uint8)
            # 撮影と同じ灰色への変え方にそろえる（IMREAD_GRAYSCALE とは丸めが違い、特徴点の数が変わる）
            bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
            img = None if bgr is None else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            _throne = ()
            if img is not None:
                gray, up = _feature_gray(img, FEATURE_REF_H)
                kp, des = cv2.SIFT_create(contrastThreshold=FEATURE_CONTRAST).detectAndCompute(gray, None)
                if des is not None and len(kp) >= 2:
                    _throne = (img.shape[:2], np.float32([k.pt for k in kp]) / up, des)
        except Exception:
            _throne = ()
        return _throne


def _feature_gray(gray, min_h: int):
    """特徴点を探す前処理: 高さ min_h 未満なら min_h に拡大（INTER_CUBIC）→ CLAHE。(処理後の灰色, 拡大の比)"""
    import cv2
    up = max(1.0, min_h / gray.shape[0])
    if up != 1.0:
        gray = cv2.resize(gray, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=FEATURE_CLAHE[0], tileGridSize=FEATURE_CLAHE[1])
    return clahe.apply(gray), up


def find_by_features(bgr):
    """玉座を特徴点（SIFT＋ホモグラフィ）で探し、(軸の x, インライア数, 倍率) を返す。受け入れなければ None。
    受け入れ: インライア ≥ RUN_FACE_FEATURE_MIN_INLIERS・写した四隅が凸・写した枠の幅が撮影の幅の
    RUN_FACE_FEATURE_WIDTH・軸の点が撮影の中（根拠は config）。倍率は絵の照合の mark[2] と同じ単位
    （factor() が、切り出し元のいつもの位置・FOV 100 で 1.0 になる）"""
    try:
        import cv2
        import numpy as np
        throne = _load_throne()
        if not throne or not hasattr(bgr, "shape") or bgr.ndim != 3:
            return None
        (ref_h, ref_w), ref_pts, ref_des = throne
        height, width = bgr.shape[:2]
        gray, up = _feature_gray(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), FEATURE_MIN_H)
        kp, des = cv2.SIFT_create(contrastThreshold=FEATURE_CONTRAST).detectAndCompute(gray, None)
        if des is None or len(kp) < 2:
            return None
        pairs = cv2.BFMatcher().knnMatch(ref_des, des, k=2)
        good = [m[0] for m in pairs
                if len(m) == 2 and m[0].distance < config.RUN_FACE_FEATURE_RATIO * m[1].distance]
        if len(good) < config.RUN_FACE_FEATURE_MIN_MATCHES:
            return None
        src = ref_pts[[m.queryIdx for m in good]].reshape(-1, 1, 2)
        dst = (np.float32([kp[m.trainIdx].pt for m in good]) / up).reshape(-1, 1, 2)
        homo, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, config.RUN_FACE_FEATURE_RANSAC_PX)
        if homo is None or mask is None:
            return None
        inliers = int(mask.sum())
        if inliers < config.RUN_FACE_FEATURE_MIN_INLIERS:
            return None
        corners = np.float32([[0, 0], [ref_w, 0], [ref_w, ref_h], [0, ref_h]]).reshape(-1, 1, 2)
        quad = cv2.perspectiveTransform(corners, homo)
        if not np.isfinite(quad).all() or not cv2.isContourConvex(quad):
            return None
        quad = quad.reshape(-1, 2)
        box_w = float((np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2)
        if not (config.RUN_FACE_FEATURE_WIDTH[0] <= box_w / width <= config.RUN_FACE_FEATURE_WIDTH[1]):
            return None
        axis = cv2.perspectiveTransform(np.float32([[[THRONE_AXIS_X, ref_h / 2]]]), homo)[0, 0]
        if not (np.isfinite(axis).all() and 0 <= axis[0] < width and 0 <= axis[1] < height):
            return None
        scale = float(np.sqrt(abs(np.linalg.det(homo[:2, :2] / homo[2, 2]))))     # 切り出しの 1px が撮影で何 px か
        return float(axis[0]), inliers, scale * THRONE_REF_HEIGHT / BASE_HEIGHT
    except Exception:
        DebugLog.exception("RunFace.find_by_features")
        return None


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
