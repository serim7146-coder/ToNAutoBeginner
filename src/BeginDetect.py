"""
画面から ToN の [ BEGIN ] 台を探す（純Python・追加依存なし）

判定手順（numpy/cv2 のプロトタイプと同じ）:
  1. 明るい赤いセルを拾う        r>150 かつ g<r*0.55 かつ b<r*0.55
  2. 固定HUD領域を除外           右下のアバター / ミュートアイコン
  3. 周囲が真っ黒な赤だけ残す    窓内の「赤でないセル」の平均輝度 < 25
  4. 膨張して8近傍で連結
  5. 面積（膨張前のセル数）とbbox平均輝度で候補を絞る
  6. 候補を画面中心（横方向）に近い順に並べて全部返す

壁の INTERMISSION は BEGIN と画像特徴で区別できないため、1件に絞らない。
中心に近い順に撃ち、効かなければ呼び出し側が次の候補へ移る。

配布物に載せるため numpy/OpenCV/PIL は使わない。重い部分（窓内の合計）は
2次元累積和で O(セル数) に落としてある。
"""
from itertools import accumulate

import config


def _grid(bits: bytes, w: int, h: int, sub: int):
    """間引きグリッドの輝度と赤フラグを作る。(gw, gh, lum, red)"""
    stride = w * 4
    step = sub * 4
    gw = w // sub
    gh = h // sub
    red_min = config.BEGIN_DETECT_RED_MIN
    ratio = config.BEGIN_DETECT_RED_RATIO
    lum: list[int] = []
    red: list[int] = []
    for gy in range(gh):
        off = gy * sub * stride
        row = bits[off:off + stride]
        # チャンネル分解はスライスに任せる（1画素ずつ触るとPythonでは重すぎる）
        bs = row[0::step][:gw]
        gs = row[1::step][:gw]
        rs = row[2::step][:gw]
        lum.extend([(r * 299 + g * 587 + b * 114) // 1000
                    for b, g, r in zip(bs, gs, rs)])
        red.extend([1 if (r > red_min and g < r * ratio and b < r * ratio) else 0
                    for b, g, r in zip(bs, gs, rs)])
    return gw, gh, lum, red


def _clear_region(red: list[int], gw: int, gh: int, region) -> None:
    """画面比率で指定した矩形の赤フラグを落とす（固定HUDの除外）"""
    y0, y1, x0, x1 = region
    for gy in range(int(gh * y0), min(gh, int(gh * y1) + 1)):
        base = gy * gw
        for gx in range(int(gw * x0), min(gw, int(gw * x1) + 1)):
            red[base + gx] = 0


def _prefix(vals: list[int], gw: int, gh: int) -> list[list[int]]:
    """2次元累積和。P[y][x] = 左上から (x-1, y-1) までの合計"""
    rows = [[0] * (gw + 1)]
    prev = rows[0]
    for gy in range(gh):
        acc = list(accumulate(vals[gy * gw:(gy + 1) * gw], initial=0))
        prev = [a + b for a, b in zip(prev, acc)]
        rows.append(prev)
    return rows


def _area_sum(p: list[list[int]], x0: int, y0: int, x1: int, y1: int) -> int:
    """[x0, x1) x [y0, y1) の合計"""
    return p[y1][x1] - p[y0][x1] - p[y1][x0] + p[y0][x0]


def detect_all(bits: bytes, w: int, h: int) -> list:
    """候補を |dx| の昇順で返す。空リストなら見つからなかった。

    画像特徴だけでは BEGIN と壁の INTERMISSION を見分けられない（実データで
    7つの特徴量を比べたが、どれも重複か逆転する）。そこで1件に絞らず全候補を
    返し、撃って効かなければ次を試す総当たりに委ねる。

    各要素:
      {"dx", "dy": 画面中心からのズレ（元画像px, 右が正）,
       "cx", "cy": 画面上の絶対座標（元画像px）,
       "area": 膨張前のセル数, "bbox": (x, y, w, h)}
    """
    sub = config.BEGIN_DETECT_SUBSAMPLE
    if not bits or w <= 0 or h <= 0 or w // sub < 3 or h // sub < 3:
        return []
    if len(bits) < w * h * 4:
        return []

    gw, gh, lum, red = _grid(bits, w, h, sub)
    _clear_region(red, gw, gh, config.BEGIN_DETECT_EXCLUDE_AVATAR)
    _clear_region(red, gw, gh, config.BEGIN_DETECT_EXCLUDE_MUTE)

    p_lum = _prefix(lum, gw, gh)
    p_red = _prefix(red, gw, gh)
    p_red_lum = _prefix([l * r for l, r in zip(lum, red)], gw, gh)

    # ── 周囲が真っ黒な赤だけ残す ──
    half = config.BEGIN_DETECT_AROUND_WIN // 2
    max_lum = config.BEGIN_DETECT_AROUND_MAX_LUM
    keep = bytearray(gw * gh)
    for gy in range(gh):
        base = gy * gw
        y0 = max(0, gy - half)
        y1 = min(gh, gy + half + 1)
        for gx in range(gw):
            if not red[base + gx]:
                continue
            x0 = max(0, gx - half)
            x1 = min(gw, gx + half + 1)
            cells = (x1 - x0) * (y1 - y0)
            n_red = _area_sum(p_red, x0, y0, x1, y1)
            n_notred = cells - n_red
            if n_notred <= 0:
                continue
            s = _area_sum(p_lum, x0, y0, x1, y1) - _area_sum(p_red_lum, x0, y0, x1, y1)
            if s / n_notred < max_lum:
                keep[base + gx] = 1

    # ── 膨張してから連結（面積は膨張前で数える） ──
    d = config.BEGIN_DETECT_DILATE_CELLS
    grown = bytearray(gw * gh)
    for gy in range(gh):
        base = gy * gw
        for gx in range(gw):
            if not keep[base + gx]:
                continue
            for ny in range(max(0, gy - d), min(gh, gy + d + 1)):
                nbase = ny * gw
                for nx in range(max(0, gx - d), min(gw, gx + d + 1)):
                    grown[nbase + nx] = 1

    cands: list = []
    seen = bytearray(gw * gh)
    min_cells = config.BEGIN_DETECT_MIN_CELLS
    max_bbox_lum = config.BEGIN_DETECT_MAX_BBOX_LUM
    for start in range(gw * gh):
        if not grown[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        cells = []
        while stack:
            i = stack.pop()
            cells.append(i)
            cy, cx = divmod(i, gw)
            for ny in range(max(0, cy - 1), min(gh, cy + 2)):
                for nx in range(max(0, cx - 1), min(gw, cx + 2)):
                    j = ny * gw + nx
                    if grown[j] and not seen[j]:
                        seen[j] = 1
                        stack.append(j)

        area = sum(keep[i] for i in cells)
        if area < min_cells:
            continue
        xs = [i % gw for i in cells]
        ys = [i // gw for i in cells]
        x0, x1 = min(xs), max(xs) + 1
        y0, y1 = min(ys), max(ys) + 1
        bbox_cells = (x1 - x0) * (y1 - y0)
        if _area_sum(p_lum, x0, y0, x1, y1) / bbox_cells > max_bbox_lum:
            continue
        cx = (sum(xs) / len(xs) + 0.5) * sub
        cy = (sum(ys) / len(ys) + 0.5) * sub
        cands.append({"dx": cx - w / 2, "dy": cy - h / 2, "cx": cx, "cy": cy,
                      "area": area,
                      "bbox": (x0 * sub, y0 * sub,
                               (x1 - x0) * sub, (y1 - y0) * sub)})

    # 画面中心に近い順。最大の塊から試すと壁のINTERMISSIONを掴んだまま抜けられない
    cands.sort(key=lambda c: abs(c["dx"]))
    return cands


def detect(bits: bytes, w: int, h: int):
    """中心に最も近い候補を (dx, dy, info) で返す。無ければ None。"""
    cands = detect_all(bits, w, h)
    if not cands:
        return None
    c = cands[0]
    return c["dx"], c["dy"], {"area": c["area"], "bbox": c["bbox"]}
