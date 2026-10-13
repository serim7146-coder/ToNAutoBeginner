"""
[ BEGIN ] の文字を VRChat の窓の画像から探す（OpenCV）。

使うのは、差し込み・クリックで Begin が押せなかったときの位置合わせだけ
（ActionExecutor._adjust_to_begin）。撮影は ScreenCapture.capture_window
（PrintWindow）なので、背面の窓でも撮れて前面を奪わない。

手順:
1. 画像を高さ H0 にそろえる（窓の大きさに依らず同じ尺度で探す）。視点の上下は
   固定なので、BEGIN の文字は画面の中ほどに来る。縦は BAND の帯だけを見る
2. 粗く: 見本1枚・縦横比1で大きさを WSTEP 刻みに振り、重ならない上位 K か所を拾う
3. 細かく: 候補の周りだけで、見本2枚・大きさ ±12%・縦横比 0.8〜1.25・横の傾き
   （横にずれて見ると文字が斜体のように傾く）を細かく振る
4. 受け入れるのは「近さ ≥ SCORE_MIN」かつ「周りが黒い（≥ DARK_MIN）」の候補か、
   「近さ ≥ SCORE_STRONG」の候補（黒さは見ない）。
   BEGIN は黒い天板の上にある。壁の模様は近さ 0.65 まで出ることがあるが、周りが
   黒くない（≤0.43）。INTERMISSION の画面は黒いが、文字が違うので近さが出ない

近さは赤さ（r - max(g, b)）の正規化相互相関。

実測（2026-09-30、撮影77枚。窓 2560x1440 と 868x704）:
  BEGIN が見えている33枚 → 33枚とも正しい位置で検出／BEGIN が無い20枚 → 誤検出0／
  真上から斜めに見た4枚 → 4枚とも検出。1枚あたり平均0.9〜1.9秒・最大1.7〜3.8秒（PC の負荷で変わる。同じ77枚を2回測った値）。
  見つけられないのは、利用者の置いた位置から左へ横移動で0.2〜0.25秒以上ずれたとき
  （BEGIN が小さく、斜めに傾いて見える。近さ0.54・黒さ0.57で基準に届かない）
  身長の低いアバター（2026-10-05 依頼者の撮影）では、BEGIN が天板の奥にほぼ真横から見え、
  縦が 1/3 ほどに潰れる（868x704 で高さ約 5px）。照準も天板の側面に当たる。対応しない
  （仕様で、自動 Begin の窓はふつうの身長のアバターにしてもらう。README）

実測（2026-10-03 実機。保存した「見つからなかった撮影」begin_miss 10枚にかけ直した）:
  BEGIN を近さ 0.79〜0.87 で正しく見つけているのに、周りの黒さ 0.31〜0.64 が DARK_MIN に
  届かず捨てていた（窓5 0.805/0.586・窓4 0.869/0.522・窓2 0.806/0.640・窓3 0.822/0.566・
  窓6 0.830/0.606 と 0.793/0.306・窓1 0.793/0.419）。黒さは「赤くない画素のうち明るさ 30 未満の
  割合」で、今のロビーの赤い光・舞う光では天板の周りが 30 を少し超える（9月30日の撮影より明るい）。
  9月30日の撮影 99枚で BEGIN でないものの近さは最高 0.640 → 近さ ≥ SCORE_STRONG（0.75）なら
  黒さを見ずに受け入れる

斜めから見た BEGIN（ET。2026-10-13 16:12 窓6: Run の後に台の左横の一段低い所に着き、BEGIN が画面の右寄りに
下から斜めに小さく見えた。上の照合では見つからない）: 上で見つからなかったときだけ、天板の見本
（PANEL_FILE。上下の点線を含む「[ BEGIN ]」）を特徴点（SIFT＋ホモグラフィ）で探す（_find_by_features）。
画像はグレーにして高さ PANEL_MAX_H までで 2 倍に拡大し、特徴点を 1 回取って、見本の大きさ 1.0・0.75 の 2 つに当てる。
インライアが少ない（6〜9）ので、受け入れは「写した天板の四隅が凸の四角形・文字の中心が画像の中・文字の所が
赤い・縦横比と幅がありえる範囲」を全部満たすときだけ。
実測（2026-10-13。手元の撮影に目で印を付けた。BEGIN が写っている 10 枚・端で切れた 2 枚・無い 51 枚）:
  上の照合だけ → 写っている 10 枚のうち 6 枚。特徴点を足す → 7 枚（依頼者のスクショ 31.webp を新たに拾う。
  中心 (552, 335)・本当の中心はおよそ (563, 333)）。特徴点だけで見ると 3 枚を拾い、無い 51 枚で誤検出 0
  （2 回流して同じ結果）。特徴点の 1 回は平均 0.35 秒・最大 0.73 秒
"""
import threading

import config

H0 = 1080                   # この高さにそろえて探す
BAND = (0.28, 0.78)         # 探す縦の範囲（高さに対する割合）
MIN_W, MAX_W, WSTEP = 55, 520, 1.12   # 粗い段の文字幅（高さ H0 換算の px）と刻み
K = 5                       # 粗い段で拾う候補の数
FINE_W = (0.88, 0.92, 0.96, 1.0, 1.04, 1.08, 1.12)
FINE_A = (0.8, 0.9, 1.0, 1.12, 1.25)
SHEARS = (-0.36, -0.18, 0.0, 0.18, 0.36)
FINE_MAX_W = 150            # 細かい段は、文字幅がこれを超える候補を縮めてから照合（速さのため）
# 受け入れる近さ。撮影 57枚（begin_data）で、周りが黒い候補の最大の近さは BEGIN の無い画面 16枚で
# 0.51〜0.54、見えている画面 26枚で 0.60〜0.97。遠く斜めの本物（2026-10-03 実機）が 0.59 だった
SCORE_MIN = 0.57
DARK_MIN = 0.65             # 受け入れる周りの黒さ（真っ黒に近い画素の割合）
SCORE_STRONG = 0.75         # これ以上の近さなら黒さを見ない（BEGIN でないものの最高 0.640）
# 見本（高さ H0 にそろえた画面から切り出した「[ BEGIN ]」の文字。元は利用者が
# Begin 前に置いた位置の撮影）。1枚目は粗い段でも使う
TEMPLATE_FILES = ("assets/templates/begin/begin_1.png", "assets/templates/begin/begin_2.png")
# 特徴点で探す見本（ET）: 2560×1440 の正面に近い撮影から切り出した天板（879×319）。文字「[ BEGIN ]」は
# その中の PANEL_TEXT（x0, y0, x1, y1）。返す中心と幅はこの四角を写したもの（上の照合と同じく文字の中心・幅）
PANEL_FILE = "assets/templates/begin/begin_panel.png"
PANEL_TEXT = (155, 161, 629, 232)
PANEL_SCALES = (1.0, 0.75)          # 見本の大きさ（2 つに当てて、受け入れたものでインライアの多い方）
PANEL_UP = 2.0                      # 画像の拡大（小さい窓で特徴点を取るため）
PANEL_MAX_H = 1500                  # 拡大した後の高さの上限（大きい窓は拡大しない・縮める）
PANEL_RATIO = 0.75                  # 対応の比の検定（Lowe）
PANEL_RANSAC_PX = 5.0
# ホモグラフィは USAC_MAGSAC で求める。インライアが 6〜10 と少ないので、RANSAC（既定）は同じ撮影でも回すたびに
# 結果が変わった（拾えたり拾えなかったり）。MAGSAC は種を変えても同じ結果で、拾える数も多かった
PANEL_MIN_INLIERS = 6
PANEL_RED_MIN = 0.15                # 写した文字の四角の中で赤い画素（赤さ > 60）の割合の下限
PANEL_ASPECT = (2.0, 12.0)          # 写した文字の幅 ÷ 高さ（正面で 6.7。斜めで潰れても）
PANEL_WIDTH = (0.03, 1.0)           # 写した文字の幅 ÷ 画像の高さ

_lock = threading.Lock()    # 見本の読み込みを1回にする
# 探すのは一度に1窓だけ（1回あたり平均0.9〜1.9秒・最大3.8秒ほど CPU を使い切る。6窓が同時に探すと、
# ほかの VRChat の窓の動きが重くなる）
_find_lock = threading.Lock()
_templates = None           # 読み込んだ見本（赤さ）。読めなかったら []
_panel = None               # 天板の見本の特徴点 [(倍率, keypoints, descriptors)]。読めなかったら []
_panel_size = (0, 0)        # 天板の見本の (高さ, 幅)


def _redness(bgr):
    import numpy as np
    b, g, r = (bgr[:, :, i].astype(np.int16) for i in range(3))
    return np.clip(r - np.maximum(g, b), 0, 255).astype(np.uint8)


def _load_templates() -> list:
    """見本を読む。cv2.imread は日本語を含むパスを読めないので、バイトで読んで decode する
    （onefile は利用者のフォルダ配下に展開される）"""
    global _templates
    with _lock:
        if _templates is not None:
            return _templates
        out = []
        try:
            import cv2
            import numpy as np
            for name in TEMPLATE_FILES:
                data = np.fromfile(str(config.resource_path(name)), dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                if img is None:
                    out = []
                    break
                out.append(_redness(img))
        except Exception:
            out = []
        _templates = out
        return out


def _load_panel() -> list:
    """天板の見本の特徴点を、PANEL_SCALES の大きさごとに 1 回だけ作る（ET）"""
    global _panel, _panel_size
    with _lock:
        if _panel is not None:
            return _panel
        out = []
        try:
            import cv2
            import numpy as np
            data = np.fromfile(str(config.resource_path(PANEL_FILE)), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is not None:
                _panel_size = img.shape[:2]
                sift = cv2.SIFT_create()
                for scale in PANEL_SCALES:
                    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    kp, des = sift.detectAndCompute(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), None)
                    if des is not None:
                        out.append((scale, kp, des))
        except Exception:
            out = []
        _panel = out
        return out


def available() -> bool:
    """検出器が使えるか（OpenCV・numpy が読めて、見本もそろっている）"""
    return bool(_load_templates())


def _shear(img, k):
    """横に傾ける（k>0 で上が右へ）。はみ出さないよう幅を広げる"""
    import cv2
    import numpy as np
    if not k:
        return img
    h, w = img.shape
    extra = int(abs(k) * h) + 1
    m = np.float32([[1, -k, extra if k > 0 else 0], [0, 1, 0]])
    return cv2.warpAffine(img, m, (w + extra, h), borderValue=0)


def _coarse(red, tmpl):
    import cv2
    t = tmpl[0]
    th0, tw0 = t.shape
    peaks = []
    w = MIN_W
    while w <= MAX_W:
        h = max(6, int(round(w * th0 / tw0)))
        if h < red.shape[0] and int(w) < red.shape[1]:
            res = cv2.matchTemplate(
                red, cv2.resize(t, (int(w), h), interpolation=cv2.INTER_AREA),
                cv2.TM_CCOEFF_NORMED)
            for _ in range(3):
                _mn, mx, _l, (x, y) = cv2.minMaxLoc(res)
                peaks.append((float(mx), x + w / 2, y + h / 2, w, h))
                res[max(0, y - h):y + h + 1,
                    max(0, x - int(w) // 2):x + int(w) // 2 + 1] = -1
        w *= WSTEP
    peaks.sort(reverse=True)
    picked = []
    for p in peaks:
        if all(abs(p[1] - q[1]) > max(p[3], q[3]) / 2
               or abs(p[2] - q[2]) > max(p[4], q[4]) for q in picked):
            picked.append(p)
        if len(picked) >= K:
            break
    return picked


def _fine(red, cand, tmpl):
    import cv2
    _s, cx, cy, w, h = cand
    f = min(1.0, FINE_MAX_W / w)
    if f < 1.0:
        red = cv2.resize(red, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        cx, cy, w, h = cx * f, cy * f, w * f, h * f
    hh, ww = red.shape
    x0 = max(0, int(cx - 1.2 * w)); x1 = min(ww, int(cx + 1.2 * w))
    y0 = max(0, int(cy - 2.5 * h)); y1 = min(hh, int(cy + 2.5 * h))
    roi = red[y0:y1, x0:x1]
    best = (-1.0, 0.0, 0.0, 0.0, 0.0)
    for t in tmpl:
        th0, tw0 = t.shape
        for fw in FINE_W:
            tw = int(round(w * fw))
            if tw < 1:
                continue
            for a in FINE_A:
                th = max(6, int(round(tw * th0 / tw0 * a)))
                base = cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA)
                for k in SHEARS:
                    tt = _shear(base, k)
                    if tt.shape[0] >= roi.shape[0] or tt.shape[1] >= roi.shape[1]:
                        continue
                    res = cv2.matchTemplate(roi, tt, cv2.TM_CCOEFF_NORMED)
                    _mn, mx, _l, (x, y) = cv2.minMaxLoc(res)
                    if mx > best[0]:
                        best = (float(mx), x0 + x + tt.shape[1] / 2, y0 + y + th / 2, tw, th)
    sc, bx, by, bw, bh = best
    return sc, bx / f, by / f, bw / f, bh / f


def _darkness(bgr, cx, cy, w, h) -> float:
    """候補の周り（文字と線を除く）のうち、真っ黒に近い画素の割合"""
    import numpy as np
    hh, ww = bgr.shape[:2]
    x0, x1 = max(0, int(cx - 0.65 * w)), min(ww, int(cx + 0.65 * w))
    y0, y1 = max(0, int(cy - 1.5 * h)), min(hh, int(cy + 1.5 * h))
    roi = bgr[y0:y1, x0:x1].astype(np.int16)
    if roi.size == 0:
        return 0.0
    red = np.clip(roi[:, :, 2] - np.maximum(roi[:, :, 0], roi[:, :, 1]), 0, 255)
    bg = red < 40
    return float((roi.max(axis=2)[bg] < 30).mean()) if bg.any() else 0.0


def find_in_bgr(bgr):
    """BGR の画像（numpy）から BEGIN を探す。

    見つかれば {"score", "dark", "cx", "cy", "w", "h"}（入力画像の座標。cx, cy は
    文字の中心、w, h は文字の幅と高さ）。見つからなければ None
    """
    tmpl = _load_templates()
    if not tmpl or bgr is None or bgr.ndim != 3 or bgr.shape[0] < 50:
        return None
    with _find_lock:
        hit = _find(bgr, tmpl)
        if hit is None:
            hit = _find_by_features(bgr)    # 見つからないときだけ、斜めから見た天板を特徴点で（ET）
        return hit


def _find(bgr, tmpl):
    import cv2
    s = H0 / bgr.shape[0]
    small = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    yb = int(H0 * BAND[0])
    red = _redness(small[yb:int(H0 * BAND[1])])
    best = None
    for c in _coarse(red, tmpl):
        sc, cx, cy, w, h = _fine(red, c, tmpl)
        box = (cx / s, (cy + yb) / s, w / s, h / s)
        dark = _darkness(bgr, *box)
        if sc >= SCORE_MIN and dark >= DARK_MIN:
            rule = "dark"
        elif sc >= SCORE_STRONG:
            rule = "strong"             # はっきり見えている（周りが明るいロビーでも）
        else:
            continue
        if best is None or sc > best["score"]:
            best = {"score": sc, "dark": dark, "cx": box[0], "cy": box[1],
                    "w": box[2], "h": box[3], "rule": rule}
    return best


def _find_by_features(bgr):
    """天板の見本を特徴点（SIFT＋ホモグラフィ）で探す（ET）。受け入れの条件を全部満たせば
    {"score": インライア/100, "dark": None, "cx", "cy", "w", "h", "rule": "features", "inliers"}、無ければ None"""
    try:
        import cv2
        import numpy as np
        panel = _load_panel()
        if not panel:
            return None
        height, width = bgr.shape[:2]
        up = min(PANEL_UP, PANEL_MAX_H / height)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if up != 1.0:
            gray = cv2.resize(gray, None, fx=up, fy=up,
                              interpolation=cv2.INTER_CUBIC if up > 1 else cv2.INTER_AREA)
        kp, des = cv2.SIFT_create().detectAndCompute(gray, None)
        if des is None or len(kp) < PANEL_MIN_INLIERS:
            return None
        matcher = cv2.BFMatcher()
        best = None
        for scale, pkp, pdes in panel:
            pairs = matcher.knnMatch(pdes, des, k=2)
            good = [m[0] for m in pairs if len(m) == 2 and m[0].distance < PANEL_RATIO * m[1].distance]
            if len(good) < PANEL_MIN_INLIERS:
                continue
            src = np.float32([pkp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2) / scale
            dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2) / up
            homo, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, PANEL_RANSAC_PX)
            if homo is None or mask is None:
                continue
            inliers = int(mask.sum())
            hit = _panel_hit(bgr, homo, inliers) if inliers >= PANEL_MIN_INLIERS else None
            if hit is not None and (best is None or inliers > best["inliers"]):
                best = hit
        return best
    except Exception:
        return None


def _panel_hit(bgr, homo, inliers):
    """写した天板と文字を確かめる。四隅が凸・文字の中心が画像の中・文字の所が赤い・縦横比と幅がありえる範囲"""
    import cv2
    import numpy as np
    height, width = bgr.shape[:2]
    ph, pw = _panel_size
    quad = cv2.perspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]).reshape(-1, 1, 2), homo)
    if not cv2.isContourConvex(quad):
        return None
    x0, y0, x1, y1 = PANEL_TEXT
    text = cv2.perspectiveTransform(np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]).reshape(-1, 1, 2),
                                    homo).reshape(-1, 2)
    cx, cy = (float(v) for v in text.mean(axis=0))
    if not (0 <= cx < width and 0 <= cy < height):
        return None
    tw = float((np.linalg.norm(text[1] - text[0]) + np.linalg.norm(text[2] - text[3])) / 2)
    th = float((np.linalg.norm(text[3] - text[0]) + np.linalg.norm(text[2] - text[1])) / 2)
    if th <= 0 or not (PANEL_ASPECT[0] <= tw / th <= PANEL_ASPECT[1]):
        return None
    if not (PANEL_WIDTH[0] <= tw / height <= PANEL_WIDTH[1]):
        return None
    lo = np.clip(text.min(axis=0), 0, [width - 1, height - 1]).astype(int)
    hi = np.clip(text.max(axis=0), 0, [width - 1, height - 1]).astype(int)
    roi = bgr[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1]
    if roi.size == 0 or float((_redness(roi) > 60).mean()) < PANEL_RED_MIN:
        return None
    return {"score": inliers / 100, "dark": None, "cx": cx, "cy": cy, "w": tw, "h": th,
            "rule": "features", "inliers": inliers}


def find(bits: bytes, w: int, h: int):
    """ScreenCapture.capture_window の戻り値（BGRA）から探す。返り値は find_in_bgr と同じ"""
    if not bits or w <= 0 or h <= 0 or len(bits) < w * h * 4:
        return None
    try:
        import numpy as np
        bgra = np.frombuffer(bits, dtype=np.uint8)[:w * h * 4].reshape(h, w, 4)
        return find_in_bgr(np.ascontiguousarray(bgra[:, :, :3]))
    except Exception:
        return None
