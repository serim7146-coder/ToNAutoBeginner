"""入室直後の選択画面の「四角（ボタンの枠）」を絵から探す

ToNEntry が、押すボタンの位置を知るために使う。純関数だけ（I/O もスレッドも持たない）。
cv2・numpy は関数の中で読む（読めない環境でも他の機能は動く）。

- 季節イベントでロゴが水色になるので、画面中央の赤い画素の数は当てにしない。枠そのものの形を見る
- 枠は赤い細線。赤さ r - max(g, b) で 2 値化し、**穴**（親のある輪郭）だけを四角の候補にする。
  連結成分は使わない（1 画面目の格子は枠どうしが繋がって 1 個の塊になる）
- 手元の撮影の実測: 枠 BGR ≈ (116, 117, 194)（赤さ 77〜98）・地 ≈ (0, 0, 16)（赤さ 16）。
  1920x1080 で四角 14 個・誤検出 0（18 ms）、高さ 466 に縮めても 13 個・誤検出 0（2 ms）。
  長方形らしさは 0.75〜0.91（高さ 1080）・0.75〜0.82（高さ 466）
- 名前や座標の意味は出さない（miss_numbers は数字だけ）
"""
import config


def available() -> bool:
    """cv2 と numpy が読めるか"""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def red_mask(bgr, th=None):
    """赤さ r - max(g, b) が th 以上の画素を 255、それ以外を 0 にした uint8 の 2 値画像"""
    import numpy as np
    th = config.TON_ENTRY_RED_MIN if th is None else th
    img = bgr.astype(np.int16)
    redness = img[:, :, 2] - np.maximum(img[:, :, 1], img[:, :, 0])
    return np.where(redness >= th, 255, 0).astype(np.uint8)


def _scan(bgr, client_height):
    """(輪郭の穴の総数, 条件を満たした四角のリスト)。四角は dict(x, y, w, h, cx, cy)"""
    import cv2
    mask = red_mask(bgr)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return 0, []
    min_side = max(4, client_height * config.TON_ENTRY_MIN_SIDE_RATIO)
    holes = 0
    rects = []
    for i, contour in enumerate(contours):
        if hierarchy[0][i][3] < 0:          # 親の無い輪郭（外側）は見ない。穴だけ
            continue
        holes += 1
        x, y, w, h = cv2.boundingRect(contour)
        if w < min_side or h < min_side:
            continue
        if cv2.contourArea(contour) / float(w * h) < config.TON_ENTRY_RECTNESS_MIN:
            continue
        approx = cv2.approxPolyDP(contour, 0.05 * cv2.arcLength(contour, True), True)
        if len(approx) != 4:
            continue
        rects.append({"x": x, "y": y, "w": w, "h": h, "cx": x + w / 2.0, "cy": y + h / 2.0})
    return holes, rects


def find_rects(bgr, client_height) -> list:
    """条件（大きさ・長方形らしさ・4 頂点）を満たす四角。dict(x, y, w, h, cx, cy)"""
    return _scan(bgr, client_height)[1]


def group_rows(rects, row_tol=None, size_tol=None) -> list:
    """四角を、同じ行（y 中心の差が高さ × row_tol 以内）の中で、幅と高さが互いに ±size_tol 以内の組にする。
    組は x の昇順。1 個だけの組も含む。起点ごとに作り、同じ中身の組は 1 つにする"""
    row_tol = config.TON_ENTRY_ROW_TOL if row_tol is None else row_tol
    size_tol = config.TON_ENTRY_SIZE_TOL if size_tol is None else size_tol
    rows = []
    for rect in sorted(rects, key=lambda r: (r["cy"], r["cx"])):
        for row in rows:
            if abs(rect["cy"] - row[0]["cy"]) <= row_tol * min(rect["h"], row[0]["h"]):
                row.append(rect)
                break
        else:
            rows.append([rect])
    groups = []
    for row in rows:
        seen = set()
        for seed in row:
            members = [r for r in row
                       if abs(r["w"] - seed["w"]) <= size_tol * seed["w"]
                       and abs(r["h"] - seed["h"]) <= size_tol * seed["h"]]
            key = tuple(id(r) for r in members)
            if key in seen:
                continue
            seen.add(key)
            groups.append(sorted(members, key=lambda r: r["cx"]))
    return groups


def _best(groups, image_width):
    """いちばん多い組。同数なら横の中心が画像の中央に近い方（まだ同じなら先に出たもの）"""
    best = []
    best_gap = None
    for group in groups:
        center = (group[0]["cx"] + group[-1]["cx"]) / 2.0
        gap = abs(center - image_width / 2.0)
        if (not best or len(group) > len(best)
                or (len(group) == len(best) and gap < best_gap)):
            best, best_gap = group, gap
    return best


def best_group(bgr, client_height) -> list:
    """いちばん要素数の多い組（x 昇順）。無ければ []"""
    return _best(group_rows(find_rects(bgr, client_height)), bgr.shape[1])


def pick(bgr, client_height, expect, index):
    """いちばん多い組の x 昇順で index 番目の (cx, cy)。expect が整数で個数が違えば None。
    expect が None なら個数は見ない。index が範囲外・組が空でも None"""
    group = best_group(bgr, client_height)
    if not group:
        return None
    if expect is not None and len(group) != expect:
        return None
    if not 0 <= index < len(group):
        return None
    rect = group[index]
    return (rect["cx"], rect["cy"])


def miss_numbers(bgr, client_height, expect=None) -> str:
    """見つからなかったときに debug.log に書く数字だけ。名前も座標の意味も出さない"""
    holes, rects = _scan(bgr, client_height)
    group = _best(group_rows(rects), bgr.shape[1])
    size = f"{group[0]['w']}x{group[0]['h']}" if group else "0x0"
    text = f"四角 {holes} 個 / 条件を満たす {len(rects)} 個 / いちばん多い組 {len(group)} 個 / 大きさ {size}"
    if expect is not None:
        text += f" / 期待 {expect}"
    return text
