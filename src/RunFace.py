"""Run のリスポーンの後、階段（Begin）の上の赤い光を撮影から探す（DO。向きを真正面に直す目印）。

2026-10-11 の実機（窓1 867×702・窓2 1294×1399）: 撮影の高さ 15〜35% の帯で、HSV の赤
（H < 10 か > 165・S > 80・V > 180）を 9×9 で膨らませて塊にすると、いちばん大きい塊の横の中心が
階段の上の光になる（窓2 で左へ 0.8 秒 → x 466・0.9 秒 → 673・1.0 秒 → 884。真ん中は 647）。
ほかの小さな赤（遠くの灯りなど）は 1294×1399 で 80〜90 画素、光は 950〜1050 画素だった。
"""
BAND = (0.15, 0.35)                 # 探す帯（撮影の高さの割合）
HUE_LOW, HUE_HIGH = 10, 165         # 赤: H < 10 か H > 165（OpenCV の H は 0〜179）
SAT_MIN, VAL_MIN = 80, 180
DILATE = 9                          # 膨らませる大きさ
MIN_AREA = 0.0002                   # 塊の大きさの下限（撮影の面積に対して。867×702 で 122・1294×1399 で 362）


def find_light(bgr):
    """光の (横の中心 x, 塊の大きさ)。見つからなければ None"""
    import cv2
    import numpy as np
    if not hasattr(bgr, "shape"):
        return None
    h, w = bgr.shape[:2]
    y0, y1 = int(h * BAND[0]), int(h * BAND[1])
    if y1 <= y0 or w <= 0:
        return None
    hsv = cv2.cvtColor(np.ascontiguousarray(bgr[y0:y1]), cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = (((hue < HUE_LOW) | (hue > HUE_HIGH)) & (sat > SAT_MIN) & (val > VAL_MIN)).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((DILATE, DILATE), np.uint8))
    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = int(stats[i, cv2.CC_STAT_AREA])
    if area < MIN_AREA * w * h:
        return None
    return float(centroids[i][0]), area
