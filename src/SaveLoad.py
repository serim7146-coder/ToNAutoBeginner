"""入室後に Save/Load のコード欄へクリップボードのセーブコードを貼る（DY）。絵を探すところと
クリップボードだけ。前面・Tab・クリックの流れは ToNEntry.load_code。

**セーブコードは、画面のログ・debug.log・撮影・テストの出力のどこにも出さない（長さも出さない）。**
ここの関数はコードを返す・受け取るだけで、書き出さない。

- コード欄の見本: 全画面 2560×1440 の窓の x 1900〜2150・y 700〜840（コード欄の左寄りと、上下の説明の
  左寄り）。倍率は「窓のクライアントの高さ / 1440」を中心に ±0.05（0.01 刻み）。クリックの点は見本の
  左上から (100, 64)×倍率
- 入力欄の見本: 入力欄の右の送信の矢印（1280×720 に縮めた撮影から切り出した）。VRChat の入力欄は画面に
  貼り付いた UI で、大きさは min(幅 / 1280, 高さ / 720) で決まる（EA: 851×664 の窓で実測 0.65。高さだけで
  決めると 0.92 になり見つからなかった。ワールドの中の板のコード欄は高さで決まる）。
  窓の下寄り・右寄りの範囲だけを探す（入力欄が出ていない撮影では 0.48〜0.55・出ていれば 0.98〜1.0。
  2026-10-12 の実機の撮影）
- 板の見本（EX）: 2026-10-14 15:44〜15:45 に、Save/Load の板が画面の右端に斜めに見えてコード欄が見つからなかった
  （依頼者のスクショ 33。今の照合で一致 0.628。依頼者の見立てはアバターの身長＝目の高さの差）。見え方に左右され
  ないよう、最初に特徴点（SIFT＋ホモグラフィ。BeginDetect の ET と同じ）で板を探し、コード欄の左寄りの点を写す。
  見本はスクショ 33（コード欄は空）の板を、欄の四隅から正面に直したもの。受け入れられなければ今の照合を試す
"""
import threading

import config
import DebugLog

CODEBOX_FILE = "assets/templates/saveload/codebox_left.png"
PANEL_FILE = "assets/templates/saveload/codebox_panel.png"
PANEL_BOX = (40, 300, 520, 360)     # 板の見本の中のコード欄（左・上・右・下。右はスクショの画面の端で切れた所）
PANEL_CLICK = (100, 330)            # 板の見本の中のクリックの点（欄の左寄り。画面の外に出る右側は押さない）
PANEL_SCALES = (1.0, 0.6)           # 見本の大きさ（両方に当て、受け入れたものでインライアの多い方）
PANEL_UP = 2.0                      # 撮影の拡大（小さい窓で特徴点を取るため）
PANEL_MAX_H = 1500                  # 拡大した後の高さの上限（大きい窓は拡大しない・縮める）
PANEL_RATIO = 0.75                  # 対応の比の検定（Lowe）
PANEL_RANSAC_PX = 5.0
# 受け入れの条件。手元の撮影で決めた: 拾う側（スクショ 33 の 4 つの大きさ・上下の台形 ±0.06/±0.12・正面の
# 見本を貼った 2560/1280）10 枚は全部、インライアは最少 16。板の無い撮影（begin_miss・run_pos・fetch_miss・
# run_face・テストの資料）99 枚は、形の条件だけで全部はじいた（板の下半分だけ写った 2 枚も、点が画面の外）
PANEL_MIN_INLIERS = 10
PANEL_LEFT_H = (0.02, 0.08)         # 写した欄の左端の高さ ÷ 撮影の高さ（スクショ 33・正面とも 0.039）
ARROW_FILE = "assets/templates/saveload/send_arrow.png"
CODEBOX_BASE_HEIGHT = 1440          # コード欄の見本を作った窓の高さ
ARROW_BASE_WIDTH = 1280             # 矢印の見本を作った撮影の幅・高さ
ARROW_BASE_HEIGHT = 720
CLICK_OFFSET = (100, 64)            # コード欄の見本の左上からクリックの点まで（倍率 1）
MATCH_MIN = 0.8
SCALE_SPAN, SCALE_STEP = 0.05, 0.01
ARROW_REGION = (0.45, 0.55, 0.95, 0.95)     # 入力欄の矢印を探す範囲（窓の割合。左・上・右・下）
ARROW_SCALES = (0.95, 1.0, 1.05)
LOADED_LINE = "Loaded Data Successfully"

_lock = threading.Lock()
_templates = {}
_panel = None                       # [(大きさ, 特徴点, 記述子)]。読めなければ []


def _load(name: str):
    """灰色の見本。読めなければ ()"""
    with _lock:
        if name in _templates:
            return _templates[name]
        try:
            import cv2
            import numpy as np
            data = np.fromfile(str(config.resource_path(name)), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
            _templates[name] = img if img is not None else ()
        except Exception:
            DebugLog.exception("SaveLoad._load")
            _templates[name] = ()
        return _templates[name]


def available() -> bool:
    """今の照合の見本 2 枚。板の見本（特徴点）が読めなくても、今の照合だけで動く"""
    return all(len(_load(name)) > 0 for name in (CODEBOX_FILE, ARROW_FILE))


def codebox_scales(client_height: int) -> list:
    """試す倍率。高さ / 1440 を中心に ±0.05（0.01 刻み）。真ん中に近い順"""
    center = client_height / CODEBOX_BASE_HEIGHT
    n = int(round(SCALE_SPAN / SCALE_STEP))
    values = [round(center + i * SCALE_STEP, 3) for i in range(-n, n + 1)]
    return sorted((v for v in values if v > 0), key=lambda s: (abs(s - center), s))


def _best(gray, template, scales, offset=(0, 0)):
    """倍率を振っていちばん合う (左上 x, 左上 y, 一致, 倍率)。どの倍率でも置けなければ None"""
    import cv2
    best = None
    for s in scales:
        t = cv2.resize(template, None, fx=s, fy=s,
                       interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        th, tw = t.shape[:2]
        if th < 4 or tw < 4 or th > gray.shape[0] or tw > gray.shape[1]:
            continue
        _lo, score, _lo_at, at = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
        if best is None or score > best[2]:
            best = (at[0] + offset[0], at[1] + offset[1], float(score), s)
    return best


def _load_panel() -> list:
    """板の見本の特徴点を、PANEL_SCALES の大きさごとに 1 回だけ作る（EX）"""
    global _panel
    with _lock:
        if _panel is not None:
            return _panel
        out = []
        try:
            import cv2
            import numpy as np
            data = np.fromfile(str(config.resource_path(PANEL_FILE)), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                sift = cv2.SIFT_create()
                for scale in PANEL_SCALES:
                    small = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale,
                                                                interpolation=cv2.INTER_AREA)
                    kp, des = sift.detectAndCompute(small, None)
                    if des is not None:
                        out.append((scale, kp, des))
        except Exception:
            DebugLog.exception("SaveLoad._load_panel")
            out = []
        _panel = out
        return out


def _features(bgr):
    """板を特徴点で探す（EX）。(受け入れた (x, y, インライア, 倍率) か None, いちばん多かったインライア)。
    倍率は写した欄の左端の高さ ÷ 見本の欄の高さ"""
    try:
        import cv2
        import numpy as np
        panel = _load_panel()
        if not panel or not hasattr(bgr, "shape"):
            return None, 0
        height, width = bgr.shape[:2]
        up = min(PANEL_UP, PANEL_MAX_H / height)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if up != 1.0:
            gray = cv2.resize(gray, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC if up > 1 else cv2.INTER_AREA)
        kp, des = cv2.SIFT_create().detectAndCompute(gray, None)
        if des is None or len(kp) < PANEL_MIN_INLIERS:
            return None, 0
        matcher = cv2.BFMatcher()
        best, most = None, 0
        for scale, pkp, pdes in panel:
            pairs = matcher.knnMatch(pdes, des, k=2)
            good = [m[0] for m in pairs if len(m) == 2 and m[0].distance < PANEL_RATIO * m[1].distance]
            if len(good) < 4:
                continue
            src = np.float32([pkp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2) / scale
            dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2) / up
            homo, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, PANEL_RANSAC_PX)
            if homo is None or mask is None:
                continue
            inliers = int(mask.sum())
            most = max(most, inliers)
            hit = _panel_hit(homo, inliers, width, height) if inliers >= PANEL_MIN_INLIERS else None
            if hit is not None and (best is None or inliers > best[2]):
                best = hit
        return best, most
    except Exception:
        DebugLog.exception("SaveLoad._features")
        return None, 0


def _panel_hit(homo, inliers, width, height):
    """写した欄を確かめる。四隅が凸・向きがひっくり返っていない・左端がほぼ縦・左端の高さがありえる範囲・
    クリックの点と欄の左端が撮影の中"""
    import cv2
    import numpy as np
    x0, y0, x1, y1 = PANEL_BOX
    quad = cv2.perspectiveTransform(np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]).reshape(-1, 1, 2),
                                    homo).reshape(-1, 2)
    if not cv2.isContourConvex(quad.reshape(-1, 1, 2)):
        return None
    left_h = float(quad[3][1] - quad[0][1])
    if left_h <= 0 or quad[1][0] <= quad[0][0] or abs(float(quad[3][0] - quad[0][0])) >= 0.5 * left_h:
        return None
    if not (PANEL_LEFT_H[0] <= left_h / height <= PANEL_LEFT_H[1]):
        return None
    cx, cy = (float(v) for v in cv2.perspectiveTransform(np.float32([[PANEL_CLICK]]), homo).reshape(2))
    if not (0 <= cx < width and 0 <= cy < height and 0 <= quad[0][0] < width):
        return None
    return cx, cy, inliers, left_h / (y1 - y0)


def find_codebox(bgr, client_height: int, template=None):
    """撮影（BGR）の中のコード欄のクリックの点 (x, y, 一致, 倍率, 探し方)。見つからなければ None。
    最初に特徴点で板を探し（探し方 "features"。一致はインライアの数）、受け入れられなければ今の照合
    （"template"。MATCH_MIN 未満なら None）。一致の値は返すが、呼ぶ側は撮影もコードも書き出さない"""
    if not hasattr(bgr, "shape") or client_height <= 0:
        return None
    hit, _most = _features(bgr)
    if hit is not None:
        return (*hit, "features")
    hit = _template(bgr, client_height, template)
    if hit is None or hit[2] < MATCH_MIN:
        return None
    x, y, score, s = hit
    return x + CLICK_OFFSET[0] * s, y + CLICK_OFFSET[1] * s, score, s, "template"


def _template(bgr, client_height: int, template=None):
    """今の照合でいちばん合う (左上 x, 左上 y, 一致, 倍率)。見本が無い・置けなければ None"""
    import cv2
    template = _load(CODEBOX_FILE) if template is None else template
    if template is None or len(template) == 0:
        return None
    return _best(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), template, codebox_scales(client_height))


def miss_numbers(bgr, client_height: int) -> str:
    """見つからなかったときに debug.log に書く数字だけ（EX）: いちばん近かった照合の一致・倍率・位置、
    特徴点のいちばん多かったインライア、画面の明るさ。撮影もコードも書き出さない"""
    try:
        if not hasattr(bgr, "shape") or client_height <= 0:
            return "撮影なし"
        import cv2
        hit = _template(bgr, client_height)
        _hit, most = _features(bgr)
        light = float(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).mean())
        near = (f"照合 一致 {hit[2]:.3f}・倍率 {hit[3]:.2f}・位置 ({hit[0]}, {hit[1]})" if hit is not None
                else "照合 なし")
        return f"{near}／特徴点 インライア {most}／明るさ {light:.1f}"
    except Exception:
        DebugLog.exception("SaveLoad.miss_numbers")
        return "測れない"


def arrow_scale(client_width: int, client_height: int) -> float:
    """矢印の見本の倍率の基準。入力欄は min(幅 / 1280, 高さ / 720) で大きさが決まる（EA）"""
    return min(client_width / ARROW_BASE_WIDTH, client_height / ARROW_BASE_HEIGHT)


def input_box_shown(bgr, client_width: int, client_height: int, template=None) -> bool:
    """VRChat の入力欄（送信の矢印）が窓の下寄り・右寄りに出ているか。大きさは窓のクライアントの幅と高さ"""
    import cv2
    template = _load(ARROW_FILE) if template is None else template
    if (template is None or len(template) == 0 or not hasattr(bgr, "shape")
            or client_width <= 0 or client_height <= 0):
        return False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    left, top, right, bottom = ARROW_REGION
    x0, y0 = int(w * left), int(h * top)
    roi = gray[y0:int(h * bottom), x0:int(w * right)]
    base = arrow_scale(client_width, client_height)
    hit = _best(roi, template, [base * s for s in ARROW_SCALES], (x0, y0))
    return hit is not None and hit[2] >= MATCH_MIN


# ── クリップボード（中身はどこにも書き出さない）──────────────
def read_clipboard() -> str:
    """クリップボードのテキスト。無い・読めなければ空文字（理由だけ debug.log へ。中身は書かない）"""
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return ""
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return text.strip() if isinstance(text, str) else ""
    except Exception as e:
        DebugLog.write(f"[コードの読み込み] クリップボードを読めない（{type(e).__name__}）")
        return ""


def write_clipboard(text: str) -> bool:
    """クリップボードに text を入れ直す（窓ごとに、最初に読んだのと同じコードを貼るため）"""
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        DebugLog.write(f"[コードの読み込み] クリップボードに入れ直せない（{type(e).__name__}）")
        return False


def log_has_loaded_line(path: str, offset: int) -> bool:
    """VRChat のログの offset バイト目から後に LOADED_LINE があるか"""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return LOADED_LINE.encode("utf-8") in f.read()
    except OSError:
        return False


def log_size(path: str) -> int:
    try:
        import os
        return os.path.getsize(path)
    except OSError:
        return 0
