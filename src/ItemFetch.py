"""アイテム自動取得: Red Merchant Store で、ロストしたアイテムを Equip する。

手順（2026-10-03 実機。窓 1920x1080・OSC。3店とも手が空から 5.5〜6.4 秒で装備できた）:
    1. 移動（OSC）: 左を押したまま跳んで柵を越え（着地2回）、後ろ→後ろ＋左、左へ90度回る
    2. 窓を前に出す（店のボタンはマウスの左クリックでしか押せない。UseRight は効かない）
    3. 店の画面を SIFT の特徴点＋ホモグラフィで見つけ、見本の四隅が撮影に入るまで視点を回す
       （横が先。縦は要るときだけ。入っていれば動かさない）
    4. Tab を押したまま SetCursorPos でカーソルを店のボタンの点（見本の上のボタンの位置を写した点）へ
       置いてクリック。Equip も同じホモグラフィで写した点の周りの緑の文字（無ければその点）へ同じく
       （Tab を押している間はカーソルが視点を回さずに動き、照準ではなくカーソルの位置で押せる。
       依頼者 2026-10-10 の実機）
    5. ログの Equipping <id> で装備できたかを確かめる（来ない・0・違う番号なら Equip を押し直す）

店を開くと説明欄には直前に持っていたアイテムが出ているので、一覧から選ぶ操作はしない。
OSC・接地・撮影・マウス・ログは差し替えられる（Fetcher の引数）。
"""
import inspect
import math
import threading
import time

import config
import LogParser

# 店の最初の画面の見本（レイアウトは同じ。文字だけが違う）。日本語の見本は英語とまったく
# 同じ枠に揃えて作ってあるので、BUTTONS も窓の大きさの定数も共通。
# ログで日本語と確定したら日本語だけを試す（_templates_to_try）
TEMPLATE_EN = "assets/templates/shop/shop_main.png"     # 英語（1920x1080 の x760〜1140・y245〜505）
TEMPLATE_JA = "assets/templates/shop/shop_main_ja.png"  # 日本語（2560x1440 の実画面から同じ枠へ揃えた）
TEMPLATE_FILES = (TEMPLATE_EN, TEMPLATE_JA)             # 言語が分からないうちに試す順
TEMPLATE_SIZE = (380, 260)  # 見本の幅・高さ（四隅を写すのに使う。2 枚とも同じ）
# 見本の中のボタンの位置（切り出しの左上が 0,0）。Equip は店を開いた後の画面にあるが、
# 見本のタイトル・枠・色見本の帯で位置が決まる（実機で3店とも1回で合った）
BUTTONS = {
    "Enkephalin": (106, 129),
    "Survival": (266, 95),
    "Event": (266, 189),
    "Equip": (180, 177),        # 緑の文字の中心（撮影4枚の平均。前の (174, 172) は左上にずれていた）
}
# item.json のカテゴリ → 店。Others・表に無いアイテムは取らない
SHOP_BY_CATEGORY = {"Enkephalin": "Enkephalin", "Survival": "Survival", "Event": "Event"}

# 特徴点の照合
RATIO = 0.75                # BFMatcher の比
MIN_GOOD = 12               # 良い対応の数
MIN_INLIERS = 10            # ホモグラフィのインライア
RANSAC_PX = 5.0

# 店の画面を映す
# 見本の四隅が撮影の端からこの割合（撮影の幅・高さに対して）より内側にあれば、全体が映っている
PANEL_MARGIN = 0.02
PANEL_TRIES = 6             # 全体を映すのに視点を動かす回数の上限
PANEL_SETTLE_PX = 2.0       # 見本の真ん中が照準からこれ以内なら、その向きに動かしても入らない
# 照準の左下の HUD（Intermission／Time の枠。頭に固定）。押す文字（横は押す点・縦は文字の箱。button_box）が
# ここに掛かったら店の画面を真ん中へ寄せる。
# 照準からの位置をクライアントの高さで割った割合（VRChat は縦の画角が決まっている）。
# 2026-10-10 の6窓の撮影（hud_w1〜6.png、851×663）の明るい画素は 横 -0.210〜-0.094・縦 +0.066〜+0.132。
# 全画面（16:9。2026-10-12 窓1 の 1920×1080）では HUD が上と右に広がり 横 -0.25〜-0.04・縦 +0.03〜+0.19
# （EI）。両方を包むように、上下左右に 0.02 ほどの余白を足した
HUD_X = (-0.27, -0.03)
HUD_Y = (0.02, 0.21)
# 店のボタン・Equip が HUD に掛かるとき、下を見るのではなく横にずらして帯の外へ出す（2026-11-17）。
# 寄せ先は Survival・Event のボタンが実際に居る位置（見本で (266-190)/1080）。
# 「帯の右端 −0.03 のすぐ外」ではなくここにするのは、HUD_X が撮影からの推定で余白込みの値だから。
# +0.070 は Survival 117 回・Event 434 回の実クリックが成功している位置（2026-11-17 の debug.log）
BUTTON_SAFE_X = 0.070
SCALE_PROBE = 10            # 倍率を測るのに、点の ±この px を写す
# gain（画面px ／ マウス1）は視点の感度で人によって違う（依頼者の PC で 横0.81・縦0.59）。決め打ちに
# しない: 視点を動かすのが要るときだけ、横・縦それぞれ小さく動かして測る（calibrate）
CALIB_UNITS = 24            # 測りで送る量（保存した gain が無いとき）
CALIB_PX = 20.0             # 保存した gain があれば、画面でこれくらい動く量を送る
CALIB_UNITS_RANGE = (6, 160)
GAIN_SANE = (0.05, 20.0)    # 測った gain がこの外なら1回測り直し、だめなら失敗
MOVE_CAP = 160              # 1回に送る量の上限（マウスの単位）
STEP = 6                    # 一度に送る量（大きく送ると Windows のマウスの加速で行き過ぎる）
# 送る間隔（測り・全体を映す動き・縦の戻しで共通。変えたら保存した感度は使わず測り直す）
STEP_SEC = 0.005
AFTER_MOVE_SEC = 0.06
MISS_TRIES = 6              # 店の画面が見つからないときの撮り直し
MISS_SEC = 0.08
CLICK_SEC = 0.04

# 移動（OSC。Begin を押した位置から。開始から約 2.5 秒で店の正面）
JUMP_AFTER_SEC = 0.30       # 左を押してから1回目のジャンプまで（速さを受け取れない窓だけ）
JUMP_GAP_SEC = 0.14         # 1回目と2回目のジャンプの間（速さを受け取れない窓だけ）
# 柵越えを速さで（ER。2026-10-13 11:33 の実測: 6 窓とも柵に当たると速さが 6 前後から 0.0〜0.21 に落ち、跳ぶと
# 接地 0 → 約 0.25 秒で 1（柵の上）→ 0 → 約 0.6 秒後に 1（柵の向こう）。前の「0.30 秒で決め打ち」は出発点が柵から
# 遠いと手前で跳び、歩いている途中の接地の浮きも着地に数えて「柵を越えた」が早すぎた）
FENCE_MOVE_SPEED = 1.0      # 速さがいったんこれ以上出たら歩き出した
FENCE_HIT_SPEED = 0.5       # 歩き出した後にこれ未満へ落ちたら柵に当たった → 跳ぶ
FENCE_LIMIT_SEC = 3.0       # 左を押してから越えるまでの上限
FENCE_JUMPS = 3             # 越えられずに張り付いたら跳び直す（合わせてこの回数まで）
FENCE_REJUMP_SEC = 0.4      # 跳んでからこの秒数たっても地面にいて止まっていれば、跳べていない → 跳び直す
FENCE_START_WAIT_SEC = 0.6  # 速さが一度も出ないまま、この秒数で押し直す（EH と同じ）
FENCE_REPRESS_MAX = 2
FENCE_REPRESS_GAP_SEC = 0.1
JUMP_HOLD_SEC = 0.06
LAND_LIMIT_SEC = 2.5        # 着地2回（柵の上・柵の向こう）を待つ上限
LAND_POLL_SEC = 0.01
BACK_SEC = 0.25             # 後ろだけ
BACK_LEFT_SEC = 0.40        # 後ろ＋左（斜め）
TURN_SEC = 0.50             # 左へ90度

# Equip
EQUIP_TRIES = 3
EQUIP_WAIT_SEC = 0.8        # Equipping <id> を待つ
EQUIP_POLL_SEC = 0.02
SHOP_OPEN_WAIT_SEC = 0.5    # 店のボタンを押してから、Equip の緑の文字が出るのを待つ上限（1回目だけ）

# ── Guidance Plush（DZ。入室の Begin の後）──────────────
# 点はすべて、Event を押す前の最初の画面の位置合わせ（ホモグラフィ）から写す（Event Shop の画面では
# 小さい窓で位置合わせが効かない。2026-10-12 の実機）。座標は見本（shop_main）の px
PLUSH_KNOB_TOP = (114, 89)          # 一覧のスクロールバーのつまみの真ん中（一番上）
PLUSH_KNOB_TARGET = (114, 132)      # Have Plush が見えるつまみの真ん中
PLUSH_ROW = (73, 96)                # Have Plush の行（緑の文字。Paradise Lost は赤・Joyous Orb は黄）
PLUSH_ROW_WINDOW = (25, 5)          # 行の緑を探す範囲（横 ±25・縦 ±5）
PLUSH_SHIFTS = (0, 8, -8)           # 行に緑が無ければドラッグ先をずらす（見本の px。最大 3 回）
PLUSH_ROUNDS = 2                    # Equipping が来ない・違う番号なら、行を選ぶところから 1 回やり直す
# Have Plush の行の点は、画面に固定の HUD（Intermission の枠の右の端）にクリックを止められることがある。
# 2026-10-19〜21 の 31 回: 照準から横 −0.090 より左は反応なし・−0.089 より右は反応あり（余白 0.01）。
# 一覧の道（buy_plush）だけ、行の点が HUD に掛かるなら視点を横に動かして、その点を右へ寄せる
PLUSH_ROW_HUD_X_MAX = -0.080        # 行の点がこれより左（照準から・クライアントの高さで割る）で縦が HUD_Y の中なら止められる
PLUSH_ROW_SAFE_X = -0.070           # 寄せる先（照準から・高さで割る）
# 行の取り違え（Jailbird など）を避けるため、緑の上下の行の色の並びで Have Plush を確かめる。
# 一覧は行の間隔が約 16（見本の px。撮影から測った）。Have Plush の上は Paradise Lost（赤）・下は Joyous Orb（黄）
PLUSH_ROW_PITCH = 16
RED_HSV_RANGES = (((0, 80, 90), (10, 255, 255)), ((165, 80, 90), (179, 255, 255)))   # Paradise Lost（H 175〜178）
YELLOW_HSV = ((15, 80, 90), (35, 255, 255))                                           # Joyous Orb（H 19〜26）
PLUSH_OPEN_WAIT_SEC = 0.5           # Event を押してから一覧が出るまで
DRAG_STEPS = 20                     # つまみのドラッグ（Tab＋左ボタンを押したまま 20 段）
DRAG_STEP_SEC = 0.03
DRAG_SETTLE_SEC = 0.2               # ドラッグを離してから撮るまで
# つまみのドラッグの記録（FQ。引く点・長さは変えない。debug.log に前・途中・後を残して次の失敗で決める）
PLUSH_KNOB_WINDOW = (20, 60)        # つまみを探す範囲（見本の px。横 ±20・縦 ±60 で溝全体）
KNOB_GRAY_MIN = 30                  # つまみとみなす明るさ（溝は 0〜10・つまみは 50〜80 だった。報告の撮影で測った値）
KNOB_SAT_MAX = 40                   # 灰色（max-min がこれ以下）
KNOB_MOVED_PX = 5                   # これ以上 上端が動けば「動いた」（見本の px）


def shop_for(item_id, items) -> str | None:
    """取りに行く店。Others・表に無い・番号が分からない → None（取らない）"""
    if not item_id:
        return None
    item = items.get(item_id)
    if item is None:
        return None
    return SHOP_BY_CATEGORY.get(item.category)


# ── 見本と特徴点 ────────────────────────────────

_lock = threading.Lock()
_templates = None           # {見本のファイル名: (keypoints, descriptors)}。読めなければ {}


def _sift():
    import cv2
    return cv2.SIFT_create()


def _features(gray):
    return _sift().detectAndCompute(gray, None)


def _load_templates():
    """見本を読む（1回だけ）。cv2.imread は日本語を含むパスを読めないのでバイトで読む。
    読めない見本は黙って飛ばす（英語だけ・日本語だけでも動く）"""
    global _templates
    with _lock:
        if _templates is not None:
            return _templates
        loaded = {}
        try:
            import cv2
            import numpy as np
            for name in TEMPLATE_FILES:
                try:
                    data = np.fromfile(str(config.resource_path(name)), dtype=np.uint8)
                    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
                    keypoints, descriptors = _features(img) if img is not None else (None, None)
                    if descriptors is not None:
                        loaded[name] = (keypoints, descriptors)
                except Exception:
                    pass
        except Exception:
            loaded = {}
        _templates = loaded
        return _templates


def _templates_to_try() -> list:
    """試す見本。ログで日本語と確定していれば日本語だけ（英語は当たりようがないので試さない。
    依頼者 2026-11-17「日本語の見本が失敗したら英語も失敗します」）。
    日本語の見本が読めていないときだけ、確定していても残りを試す"""
    loaded = _load_templates()
    if LogParser.japanese_seen() and TEMPLATE_JA in loaded:
        return [loaded[TEMPLATE_JA]]
    return [loaded[name] for name in TEMPLATE_FILES if name in loaded]


def available() -> bool:
    """使えるか（OpenCV の SIFT が使えて、見本が 1 枚でも読める）"""
    return bool(_load_templates())


def locate(bgr, point, template=None):
    """撮影（BGR）の中で、見本の point（見本の中の座標）が写っている位置と倍率と手がかりの数と写す関数
    (x, y, 横の倍率, 縦の倍率, インライアの数, project)。倍率は見本の 1px が画面で何 px か（点の
    ±SCALE_PROBE を写した距離から）。project(見本の点) は同じホモグラフィで別の点を (x, y, 横, 縦の倍率) に
    写す（店のボタンを押す回に Equip の位置も覚えるため）。見つからなければ None。
    見本は _templates_to_try() の順に試し、先に当たった方を使う
    （撮影の特徴点は見本の枚数に関係なく 1 回だけ計算する）。template を渡したときはそれだけを使う"""
    import cv2
    templates = [template] if template else _templates_to_try()
    if not templates:
        return None
    k2, d2 = _features(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    if d2 is None or len(k2) < 2:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    for k1, d1 in templates:
        found = _match_one(k1, d1, k2, d2, matcher, point)
        if found is not None:
            return found
    return None


def _match_one(k1, d1, k2, d2, matcher, point):
    """見本 1 枚ぶんの照合。見つからなければ None"""
    import cv2
    import numpy as np
    pairs = matcher.knnMatch(d1, d2, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
    if len(good) < MIN_GOOD:
        return None
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    h, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX)
    if h is None or mask is None or int(mask.sum()) < MIN_INLIERS:
        return None
    def project(pt):
        px, py = pt
        d = SCALE_PROBE
        pts = cv2.perspectiveTransform(np.float32([[(px, py), (px - d, py), (px + d, py),
                                                    (px, py - d), (px, py + d)]]), h)[0]
        sx = float(np.hypot(*(pts[2] - pts[1]))) / (2 * d)
        sy = float(np.hypot(*(pts[4] - pts[3]))) / (2 * d)
        return float(pts[0][0]), float(pts[0][1]), sx, sy

    x, y, sx, sy = project(point)
    return x, y, sx, sy, int(mask.sum()), project


# Equip は緑の文字のど真ん中を押す。押す点の近くの緑の画素の重心。窓は見本の px で持ち、倍率で写す。
# 窓は Equip の近くだけ（左の一覧・上のアイテム名の緑の文字を入れない）
GREEN_HSV_LOW = (40, 80, 90)        # OpenCV の HSV（H は 0〜179）
GREEN_HSV_HIGH = (90, 255, 255)
GREEN_WINDOW = (30, 15)             # 押す点から 横 ±30・縦 ±15（見本の px）
GREEN_MIN_AREA = 3.0                # 緑の画素がこれ未満（見本の px² に直して）なら見つからない


def green_center(bgr, pos, scale, window=GREEN_WINDOW):
    """pos（画面の点）の周りの窓（見本の px で 横 ±window[0]・縦 ±window[1]）の緑の画素の重心 (x, y)。
    少なければ・撮影が無ければ None"""
    try:
        import cv2
        import numpy as np
        if not hasattr(bgr, "shape"):
            return None
        sx, sy = scale
        h, w = bgr.shape[:2]
        x0 = max(0, int(round(pos[0] - window[0] * sx)))
        x1 = min(w, int(round(pos[0] + window[0] * sx)) + 1)
        y0 = max(0, int(round(pos[1] - window[1] * sy)))
        y1 = min(h, int(round(pos[1] + window[1] * sy)) + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        hsv = cv2.cvtColor(np.ascontiguousarray(bgr[y0:y1, x0:x1]), cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(GREEN_HSV_LOW, np.uint8), np.array(GREEN_HSV_HIGH, np.uint8))
        ys, xs = np.nonzero(mask)
        if len(xs) < max(1.0, GREEN_MIN_AREA * sx * sy):
            return None
        return x0 + float(xs.mean()), y0 + float(ys.mean())
    except Exception:
        return None


def color_area(bgr, pos, scale, ranges, window=GREEN_WINDOW) -> int:
    """green_center と同じ窓（pos の周りの 見本の px で 横 ±window[0]・縦 ±window[1]）で、ranges
    （(low, high) の並び）のどれかに入る画素の数。撮影が無い・例外は 0"""
    try:
        import cv2
        import numpy as np
        if not hasattr(bgr, "shape"):
            return 0
        sx, sy = scale
        h, w = bgr.shape[:2]
        x0 = max(0, int(round(pos[0] - window[0] * sx)))
        x1 = min(w, int(round(pos[0] + window[0] * sx)) + 1)
        y0 = max(0, int(round(pos[1] - window[1] * sy)))
        y1 = min(h, int(round(pos[1] + window[1] * sy)) + 1)
        if x1 <= x0 or y1 <= y0:
            return 0
        hsv = cv2.cvtColor(np.ascontiguousarray(bgr[y0:y1, x0:x1]), cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for low, high in ranges:
            mask |= cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
        return int(np.count_nonzero(mask))
    except Exception:
        return 0


# ── 進化アイテム（依頼者 2026-11-17。実機の全画面 16 枚から測った） ──
# 失くしたのが進化アイテムなら、Equip（装備する）ではなく、その右に並ぶ小さな色文字を押す。
# 番号 → (色相, 彩度, 同じページで一番明るい文字を 1.0 としたときの明るさ)。
# 位置も個数も持たない——アップデートで色文字が増減しても壊れないため。
# 表に無い番号は今までどおり「装備する」を押す（知らないものが来ても害が無い）。
# 明るさの比は、同じ元アイテムの色文字の中で一番明るい V を 1.0 にして出した
EVOLUTIONS = {
    93: (0, 155, 1.00),     # Scanner（レーダー）
    46: (0, 173, 1.00),     # Red Medkit（メッドキット）
    87: (25, 117, 1.00),    # Slots（Haveの酒）
    45: (119, 148, 1.00),   # Psycho Coil（グローコイル）
    33: (0, 154, 1.00),     # Bloody Coil（スピードコイル）
    76: (90, 76, 1.00),     # Pale Regen（リジェネコイル・蒼白）
    80: (67, 136, 0.96),    # Life Coil（リジェネコイル・緑）
    31: (0, 148, 1.00),     # Bloody Bat（金属バット）★ Ghost Bat と色相が 2 しか違わない
    43: (135, 169, 0.85),   # Color Bat（金属バット）
    42: (93, 104, 1.00),    # Justitia（金属バット）
    49: (109, 25, 0.80),    # Metal Pipe（金属バット・白い文字）
    82: (178, 172, 0.22),   # Ghost Bat（金属バット）★ 明るさの比で見分ける
    18: (0, 154, 1.00),     # Bloody Kat Charm（シルバーキャットチャーム）
    72: (126, 98, 0.96),    # Luna Charm（シルバーキャットチャーム）
    44: (24, 61, 1.00),     # Twilight Coil（カオスコイル。彩度 61 なので白い文字の側）
    85: (0, 184, 1.00),     # Destroyer Coil（コルクスクリュー）
    66: (0, 155, 0.89),     # Bloody Pistol（アンティークリボルバー）
    38: (90, 80, 1.00),     # Pale Pistol（アンティークリボルバー）
    69: (90, 74, 1.00),     # Pale Teleporter（テレポーター）
    32: (0, 155, 0.91),     # Bloody Teleporter（テレポーター）
    91: (140, 163, 0.96),   # Broken Teleporter（テレポーター）
    39: (75, 143, 0.83),    # Thorn Hacker（テレポーター）
    86: (25, 117, 0.82),    # Soul Visitor（テレポーター）
    74: (109, 25, 1.00),    # Haunted Burger（ハンバーガー・白い文字）
    11: (143, 140, 1.00),   # Nora Plush（Beyond人形）
    73: (109, 25, 1.00),    # Roblander Plush（DarkGrey人形・白い文字）
    75: (20, 146, 1.00),    # Observation Plush（Have人形）
}
HUE_TOLERANCE = 14          # 色相の許容（同じページの最小の差は 15）
SAT_WHITE = 70              # これ未満は「白い文字」。白どうしは同じページに無い
VREL_TOLERANCE = 0.35       # 相対の明るさの許容（1.00 と 0.22 を分けるのに十分）
# 色文字を探す範囲（見本の px。Equip の中心から）。実測: +56.9〜+139.0・上下 ±7.3
EVOLUTION_BAND = (40, 160, -14, 14)     # 左, 右, 上, 下
EVOLUTION_MIN_PIXELS = 25               # 文字と認める画素数（見本の px²。倍率で掛ける）
EVOLUTION_TOP_PERCENT = 12              # 帯の明るい方から数えて、この割合の値を閾値にする
EVOLUTION_V_FLOOR = 30                  # 閾値の下限（帯がほぼ真っ暗でも背景を拾わない）
EVOLUTION_ROUNDS = 6                    # 明るい方から剥がす回数の上限（色文字は最大 5 つ）
EVOLUTION_GAP_PX = 3                    # 同じ文字の字と字の隙間を繋ぐ幅（見本の px。横だけ）
EVOLUTION_HEIGHT_PX = (3, 12)           # 1 つの色文字の高さの範囲（見本の px）
EVOLUTION_WIDTH_PX = (4, 60)            # 1 つの色文字の幅の範囲（見本の px）


def evolution_labels(bgr, equip_pos, scale) -> list:
    """Equip（装備する）の右に並ぶ色文字を返す [(x, y, 色相, 彩度, 明るさ)]。画面の px。
    見つからない・撮影が無い・例外は空。

    明るさの閾値は**固定で持たない**。帯の中の明るい方から数えて決める
    （ゴーストは 56・ほかは 200 超で、固定だとゴーストが落ちる）。
    彩度では絞らない（白い文字が落ちる）。色は閾値を超えた画素だけで測る
    （文字の箱ごと測ると、暗い内側を数えて暗く出る）"""
    try:
        import cv2
        import numpy as np
        if not hasattr(bgr, "shape"):
            return []
        sx, sy = scale
        h, w = bgr.shape[:2]
        left, right, top, bottom = EVOLUTION_BAND
        x0 = max(0, int(round(equip_pos[0] + left * sx)))
        x1 = min(w, int(round(equip_pos[0] + right * sx)) + 1)
        y0 = max(0, int(round(equip_pos[1] + top * sy)))
        y1 = min(h, int(round(equip_pos[1] + bottom * sy)) + 1)
        if x1 <= x0 or y1 <= y0:
            return []
        hsv = cv2.cvtColor(np.ascontiguousarray(bgr[y0:y1, x0:x1]), cv2.COLOR_BGR2HSV)
        value = hsv[:, :, 2]
        # 1 つの文字は字ごとに分かれているので、横に少し太らせて 1 つに繋ぐ（縦は太らせない。2 段が混ざる）
        half = max(1, int(round(EVOLUTION_GAP_PX * sx / 2)))
        kernel = np.ones((1, 2 * half + 1), np.uint8)
        least = max(1.0, EVOLUTION_MIN_PIXELS * sx * sy)
        pad_x, pad_y = int(math.ceil(2 * sx)), int(math.ceil(sy))
        claimed = np.zeros(value.shape, bool)       # もう数えた所（縁の薄い画素を次の回で拾い直さない）
        found = []
        # 明るい方から剥がしていく。1 回で全部拾おうとすると、明るい文字が帯の 12% を超えたとき、
        # 閾値が明るい文字の中に入ってしまい、暗い文字（ゴースト）が落ちる
        for _ in range(EVOLUTION_ROUNDS):
            free = ~claimed
            if not free.any():
                break
            threshold = max(EVOLUTION_V_FLOOR, int(np.percentile(value[free], 100 - EVOLUTION_TOP_PERCENT)))
            ink = (value >= threshold) & free
            if not ink.any():
                break
            count, labeled = cv2.connectedComponents(cv2.dilate(ink.astype(np.uint8), kernel), connectivity=8)
            for i in range(1, count):
                ys, xs = np.nonzero((labeled == i) & ink)
                if len(xs) == 0:
                    continue
                if len(xs) < least:
                    # 暗めの文字の、閾値を超えた散らばりだけ。その画素だけ外して、残りを次の回で拾う
                    # （箱ごと外すと、文字が丸ごと落ちる）
                    claimed[ys, xs] = True
                    continue
                claimed[max(0, ys.min() - pad_y):ys.max() + pad_y + 1,
                        max(0, xs.min() - pad_x):xs.max() + pad_x + 1] = True
                width = int(xs.max() - xs.min()) + 1
                height = int(ys.max() - ys.min()) + 1
                if not (EVOLUTION_WIDTH_PX[0] * sx <= width <= EVOLUTION_WIDTH_PX[1] * sx):
                    continue
                if not (EVOLUTION_HEIGHT_PX[0] * sy <= height <= EVOLUTION_HEIGHT_PX[1] * sy):
                    continue
                pixels = hsv[ys, xs]            # 閾値を超えた画素だけで色を測る
                hues = pixels[:, 0].astype(float)
                # 赤は 0 と 179 の両側に割れる。円の平均を中心にずらしてから中央値を取る
                angle = hues * (2 * math.pi / 180.0)
                center = (math.atan2(np.sin(angle).mean(), np.cos(angle).mean()) * 180.0 / (2 * math.pi)) % 180.0
                hue = (center + float(np.median(((hues - center + 90.0) % 180.0) - 90.0))) % 180.0
                found.append((x0 + (int(xs.min()) + int(xs.max())) / 2.0,
                              y0 + (int(ys.min()) + int(ys.max())) / 2.0,
                              hue, float(np.median(pixels[:, 1])), float(np.median(pixels[:, 2]))))
        return sorted(found, key=lambda t: (t[0], t[1]))
    except Exception:
        return []


def _hue_gap(a, b) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def pick_evolution(labels, want):
    """色が合う文字を 1 つ返す（evolution_labels の 1 件）。want は EVOLUTIONS の値
    (色相, 彩度, 相対の明るさ)。合うものが無い・2 つ以上に割れたときは None
    （呼ぶ側が Equip に落ちる。間違えて押すより押さない）"""
    if not labels:
        return None
    want_hue, want_sat, want_rel = want
    brightest = max(label[4] for label in labels)
    if brightest <= 0:
        return None
    white = want_sat < SAT_WHITE
    fits = []
    for label in labels:
        _x, _y, hue, sat, val = label
        if white:
            if sat >= SAT_WHITE:
                continue
        elif sat < SAT_WHITE or _hue_gap(hue, want_hue) > HUE_TOLERANCE:
            continue
        # 色相が近い 2 つ（金属バットの 血濡れ・ゴースト）は、ここの明るさの比だけで分かれる
        if abs(val / brightest - want_rel) > VREL_TOLERANCE:
            continue
        fits.append(label)
    # 2 つ以上残る（同じ色が 2 つ）なら決められない。押さない
    return fits[0] if len(fits) == 1 else None


def row_order_ok(bgr, center, scale) -> tuple:
    """緑の重心 center の上 PLUSH_ROW_PITCH に赤・下に黄があるか。(合ったか, ログの文字) を返す。
    合えば '赤／黄'、違えば '上 黄・下 黄' のように上下の色の名前（赤・黄・緑・なし のうち画素が一番多いもの）。
    窓は PLUSH_ROW_WINDOW。最小の量は GREEN_MIN_AREA × 倍率²（green_center と同じ）"""
    sx, sy = scale
    least = max(1.0, GREEN_MIN_AREA * sx * sy)
    colors = (("赤", RED_HSV_RANGES), ("黄", (YELLOW_HSV,)), ("緑", ((GREEN_HSV_LOW, GREEN_HSV_HIGH),)))

    def name_at(dy_px):
        pos = (center[0], center[1] + dy_px * sy)
        counts = [(color_area(bgr, pos, scale, rng, PLUSH_ROW_WINDOW), nm) for nm, rng in colors]
        best, nm = max(counts, key=lambda c: c[0])
        return nm if best >= least else "なし", counts

    up, up_counts = name_at(-PLUSH_ROW_PITCH)
    down, down_counts = name_at(PLUSH_ROW_PITCH)
    up_red = dict((nm, n) for n, nm in up_counts)["赤"] >= least
    down_yellow = dict((nm, n) for n, nm in down_counts)["黄"] >= least
    if up_red and down_yellow:
        return True, "赤／黄"
    return False, f"上 {up}・下 {down}"


def knob_band(bgr, pos, scale, window=PLUSH_KNOB_WINDOW):
    """pos（画面の点。見積もりのつまみ）の周りの窓（見本の px × scale）で、明るい灰色の縦の帯の
    (中心 x, 上端 y, 下端 y) を画面の座標で返す。無ければ None。
    列ごとに「灰色（max-min <= KNOB_SAT_MAX）で明るい（gray >= KNOB_GRAY_MIN）画素」を数え、最大の 6 割以上の列を帯とする
    （最大が 0 なら None）。帯の上端・下端は、帯の列で明るい画素のある行の最小・最大。green_center と同じ流儀（例外は None）"""
    try:
        import numpy as np
        if not hasattr(bgr, "shape"):
            return None
        sx, sy = scale
        h, w = bgr.shape[:2]
        x0 = max(0, int(round(pos[0] - window[0] * sx)))
        x1 = min(w, int(round(pos[0] + window[0] * sx)) + 1)
        y0 = max(0, int(round(pos[1] - window[1] * sy)))
        y1 = min(h, int(round(pos[1] + window[1] * sy)) + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        area = np.ascontiguousarray(bgr[y0:y1, x0:x1]).astype(np.int32)
        gray = area.mean(axis=2)
        sat = area.max(axis=2) - area.min(axis=2)
        mask = (sat <= KNOB_SAT_MAX) & (gray >= KNOB_GRAY_MIN)
        counts = mask.sum(axis=0)
        peak = int(counts.max())
        if peak <= 0:
            return None
        cols = np.nonzero(counts >= 0.6 * peak)[0]
        rows = np.nonzero(mask[:, cols].any(axis=1))[0]
        if len(cols) == 0 or len(rows) == 0:
            return None
        return (x0 + float(cols.mean()), y0 + int(rows.min()), y0 + int(rows.max()))
    except Exception:
        return None


# ── 視点を動かす量 ──────────────────────────────

def split_move(mx: float, my: float, step: int = STEP) -> list:
    """(mx, my) を1回 step 以下の整数の刻みに分ける（合計は四捨五入した mx, my）"""
    n = max(1, math.ceil(max(abs(mx), abs(my)) / step))   # 24 → 6×4
    out = []
    for i in range(n):
        dx = int(round(mx * (i + 1) / n)) - int(round(mx * i / n))
        dy = int(round(my * (i + 1) / n)) - int(round(my * i / n))
        out.append((dx, dy))
    return out


def panel_corners(project) -> list:
    """見本の四隅を、撮影の上へ写した点 [(x, y)]×4（左上・右上・左下・右下）"""
    w, h = TEMPLATE_SIZE
    return [tuple(project(c)[:2]) for c in ((0, 0), (w, 0), (0, h), (w, h))]


def button_box(project, point, half_y=None) -> list:
    """重なりを見る 2 点（EI・技術選定の決定 A）: 横は押す点、縦は文字の箱（見本の px で 押す点の上下
    ± half_y。既定は GREEN_WINDOW の縦）。撮影の上へ写した [(x, y)]×2（上・下）。
    横も箱にすると、店の画面を真ん中へ寄せた後も Equip の箱の左の端（照準から −0.037）が HUD_X に入り、
    押せなくなる"""
    hy = GREEN_WINDOW[1] if half_y is None else half_y
    px, py = point
    return [tuple(project((px, py + sy * hy))[:2]) for sy in (-1, 1)]


def box_in_hud(box, aim, height) -> bool:
    """2 点のどちらかが HUD の範囲に入っているか（EI: 12:11 窓1 は押す点だけ見て、文字が掛かったまま押していた）"""
    return any(in_hud(c, aim, height) for c in box)


def box_ratio(box, aim, height) -> str:
    """箱の範囲を照準からの割合で（debug.log 用）"""
    xs = [(x - aim[0]) / height for x, _y in box]
    ys = [(y - aim[1]) / height for _x, y in box]
    return f"横 {min(xs):+.3f}〜{max(xs):+.3f}・縦 {min(ys):+.3f}〜{max(ys):+.3f}"


def in_hud(point, aim, height) -> bool:
    """撮影の点が HUD の範囲（照準 ＋ HUD_X・HUD_Y × クライアントの高さ）に入っているか"""
    rx, ry = (point[0] - aim[0]) / height, (point[1] - aim[1]) / height
    return HUD_X[0] <= rx <= HUD_X[1] and HUD_Y[0] <= ry <= HUD_Y[1]


def hud_shift_x(boxes, names, aim, height) -> float:
    """HUD に掛かっている押す点（boxes の中の names）を、まとめて BUTTON_SAFE_X まで右へ出すのに要る量（画面 px）。

    返すのは frame_panel の want と同じ向き（負 ＝ 視点を左へ ＝ 板が右へ）。
    掛かっている点が無ければ 0.0。いちばん左の点を基準にする（全部が帯の外に出る）。
    boxes は [(名前, button_box の 2 点)]、names は HUD に掛かっている名前、aim は照準、height はクライアントの高さ"""
    xs = [x for name, box in boxes if name in names for x, _y in box]
    if not xs:
        return 0.0
    return min(0.0, (min(xs) - aim[0]) - BUTTON_SAFE_X * height)


def shifted_outside(corners, shift, size) -> tuple:
    """四隅を横に shift（hud_shift_x と同じ向き。負 ＝ 板が右へ）だけ動かしたら、撮影からはみ出すか (横, 縦)。
    panel_outside と同じ形。視点を横に回すのは画面上のほぼ平行移動なので、動かす前に当たりを付けるのに使う"""
    return panel_outside([(x - shift, y) for x, y in corners], size)


def row_blocked(point, aim, height) -> bool:
    """Have Plush の行の点（撮影）が、HUD にクリックを止められる位置か（照準から横 PLUSH_ROW_HUD_X_MAX より左・
    縦が HUD_Y の中。一覧の道だけ見る）"""
    rx, ry = (point[0] - aim[0]) / height, (point[1] - aim[1]) / height
    return rx < PLUSH_ROW_HUD_X_MAX and HUD_Y[0] <= ry <= HUD_Y[1]


def panel_outside(corners, size) -> tuple:
    """四隅が撮影の端の余白（PANEL_MARGIN）の外へはみ出しているか (横, 縦)。size は撮影の (幅, 高さ)"""
    w, h = size
    mx, my = PANEL_MARGIN * w, PANEL_MARGIN * h
    return (any(x < mx or x > w - mx for x, _y in corners),
            any(y < my or y > h - my for _x, y in corners))


# ── 取りに行く ──────────────────────────────────

def _accepts_on_mid(fn) -> bool:
    try:
        return "on_mid" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


class CountingMouse:
    """送ったマウスの相対移動の合計（横・縦）を数える（終わったら逆向きに送って視点を戻すため）"""

    def __init__(self, inner):
        self.inner = inner
        self.total = [0, 0]

    def move_rel(self, dx: int, dy: int):
        self.inner.move_rel(dx, dy)
        self.total[0] += dx
        self.total[1] += dy

    def tab_down(self):
        """Tab を押す（店の操作の間ずっと押したまま）"""
        self.inner.tab_down()

    def tab_up(self):
        self.inner.tab_up()

    def click_at(self, x: float, y: float) -> bool:
        """（Tab を押したまま）撮影の (x, y) へカーソルを置いてクリックする。押したら True。
        視点は回らない（数えない）"""
        return self.inner.click_at(x, y)

    def drag(self, start, end, steps: int, step_sec: float, on_mid=None) -> bool:
        """（Tab を押したまま）撮影の start で左ボタンを押し、steps 段で end まで動かして離す。
        カーソルで動かすので視点は回らない（数えない）。on_mid は真ん中の段で呼ぶ（中の drag が受けるときだけ渡す）"""
        if on_mid is not None and _accepts_on_mid(self.inner.drag):
            return self.inner.drag(start, end, steps, step_sec, on_mid=on_mid)
        return self.inner.drag(start, end, steps, step_sec)


class Stopped(Exception):
    """ラウンド開始・停止・時間切れ（理由は args[0]）"""


def _knob_text(band, est) -> str:
    """ログ「つまみ 実測（引く前）」の中身。band は knob_band の結果、est は見積もりの点"""
    where = f"（見積もり ({est[0]:.1f}, {est[1]:.1f})）"
    if band is None:
        return f"見つかりません{where}"
    return f"x {band[0]:.1f}・y {band[1]}〜{band[2]}{where}"


class Fetcher:
    """1回の取得。

    osc:        send(address, value)・stop_all(repeat)
    grounded:   () → 接地しているか（True/False。分からなければ None）
    capture:    () → (BGR の撮影, 照準の位置 (x, y)) か None
    mouse:      move_rel(dx, dy)・tab_down()・tab_up()・click_at(撮影の x, y)（Tab を押したまま
                カーソルを置いて読み直し、置けていればクリック。押したら True）
    equip_seen: () → (Equipping を受けた回数, 最後の id)
    stopped:    () → やめる理由（str）か None
    log:        debug.log へ（窓の番号は呼び出し側が付ける）
    saved_gain: 前に測った gain（横, 縦）か None。視点を動かすときはまずこれを使い、全体を映せなければ
                測り直して1回やり直す
    """

    def __init__(self, osc, grounded, capture, mouse, equip_seen, stopped, log,
                 locate_fn=locate, sleep=None, clock=None, saved_gain=None, client_height=None,
                 speed=None, on_miss=None):
        self.osc = osc
        # () → 速さ（VelocityMagnitude）か None。無ければ柵越えは決め打ち（ER）
        self.speed = speed
        # (撮影, 理由) → 見つからなかった撮影を残す（ER。FetchMiss）。last_shot は直前に撮ったもの
        self.on_miss = on_miss
        self.last_shot = None
        # () → 窓のクライアント領域の高さ（HUD の範囲の大きさ）。無い・取れなければ撮影の高さ
        self.client_height = client_height
        self.grounded = grounded
        self.capture = capture
        self.mouse = CountingMouse(mouse)   # 測りと全体を映す動きを全部数える
        self.equip_seen = equip_seen
        self.stopped = stopped
        self.log = log
        self.locate = locate_fn
        self.sleep = sleep or time.sleep
        self.clock = clock or time.time
        self.saved_gain = saved_gain
        self.gain = None            # 視点を動かすのに使う感度（保存した値か calibrate で測った値）
        self.gain_from_saved = False
        self.measured_gain = None   # calibrate で測った感度（保存するのはこれだけ）
        # 店のボタンを押した撮影の同じホモグラフィで写した Equip: ((x, y), (横, 縦の倍率))。
        # 視点は動かさないので、店の中でもそのまま使える
        self.equip_point = None
        # つまみのドラッグの記録（FQ）: 最後のドラッグの引く前・途中の撮影（失敗したときだけ残す）
        self.before_shot = None
        self.mid_shot = None

    def _check(self):
        reason = self.stopped()
        if reason:
            raise Stopped(reason)

    def _wait(self, sec: float):
        self._check()
        self.sleep(sec)
        self._check()

    # ── 1. 移動 ──
    def _jump(self):
        self.osc.send("/input/Jump", 1)
        self.sleep(JUMP_HOLD_SEC)
        self.osc.send("/input/Jump", 0)

    def _speed_now(self):
        try:
            return self.speed() if self.speed is not None else None
        except Exception:
            return None

    def _cross_fence_fixed(self) -> bool:
        """速さを受け取れない窓: 左を押して JUMP_AFTER_SEC で決め打ちに 2 回跳び、着地 2 回で越えた（前のまま）"""
        self._wait(JUMP_AFTER_SEC)
        self._jump()
        self._wait(JUMP_GAP_SEC)
        self._jump()
        lands, air = 0, False
        deadline = self.clock() + LAND_LIMIT_SEC
        while True:
            if self.clock() >= deadline:
                self.log(f"アイテム取得: 柵を越えられません（着地 {lands} 回）")
                return False
            self._check()
            g = self.grounded()
            if g is False:
                air = True
            elif g is True and air:
                lands += 1
                air = False
                if lands == 2:
                    return True             # 柵の向こうに着いた
            self.sleep(LAND_POLL_SEC)

    def _cross_fence_by_speed(self, started: float) -> bool:
        """左を押したまま、速さがいったん FENCE_MOVE_SPEED 以上出た後に FENCE_HIT_SPEED 未満へ落ちたら（柵に
        当たった）跳ぶ。着地は跳んだ後から数え、接地の 0→1 が 2 回で越えた（歩いている途中の浮きは数えない）。
        2 回目の着地の前に、地面にいて速さが FENCE_HIT_SPEED 未満（越えられずに張り付いた・跳べていない）なら
        跳び直す（合わせて FENCE_JUMPS 回まで。数え直す）。速さが一度も出なければ跳ばずに押し直す
        （FENCE_REPRESS_MAX 回まで）。FENCE_LIMIT_SEC で越えられなければ False"""
        deadline = started + FENCE_LIMIT_SEC
        moved = False
        pressed_at = started
        represses = 0
        jumps = 0
        jumped_at = None
        lands, air = 0, False
        land_times = []
        while True:
            now = self.clock()
            if now >= deadline:
                self.log(f"アイテム取得: 柵を越えられません（{FENCE_LIMIT_SEC:.1f}秒・跳んだ {jumps} 回・"
                         f"着地 {lands} 回・" + ("歩き出した" if moved else "速度が出ない") + "）")
                return False
            self._check()
            s = self._speed_now()
            g = self.grounded()
            if jumps == 0:
                if s is not None and s >= FENCE_MOVE_SPEED:
                    moved = True
                if moved and s is not None and s < FENCE_HIT_SPEED:
                    self.log(f"アイテム取得: 柵に当たった {now - started:.2f}秒（速度 {s:.2f}）→ 跳びます")
                    jumps, jumped_at, lands, air = 1, self.clock(), 0, False     # 着地は跳び始めから数える
                    self._jump()
                elif not moved and now - pressed_at >= FENCE_START_WAIT_SEC and represses < FENCE_REPRESS_MAX:
                    represses += 1
                    self.log(f"アイテム取得: 速度が出ません（{FENCE_START_WAIT_SEC:.1f}秒）→ 左を押し直します（{represses} 回目）")
                    self.osc.send("/input/MoveLeft", 0)
                    self.sleep(FENCE_REPRESS_GAP_SEC)
                    self.osc.send("/input/MoveLeft", 1)
                    pressed_at = self.clock()
                    continue
            else:
                if g is False:
                    air = True
                elif g is True and air:
                    lands += 1
                    air = False
                    land_times.append(now - jumped_at)
                    if lands == 2:
                        self.log("アイテム取得: 着地 " + "・".join(f"{t:.2f}" for t in land_times)
                                 + f" 秒（跳んでから。跳んだ {jumps} 回）")
                        return True
                stuck = (g is True and s is not None and s < FENCE_HIT_SPEED
                         and (lands >= 1 or now - jumped_at >= FENCE_REJUMP_SEC))
                if stuck and jumps < FENCE_JUMPS:
                    jumps += 1
                    self.log(f"アイテム取得: 越えられずに張り付いた {now - started:.2f}秒（速度 {s:.2f}・着地 {lands} 回）"
                             f"→ 跳び直します（{jumps} 回目）")
                    jumped_at, lands, air, land_times = self.clock(), 0, False, []
                    self._jump()
            self.sleep(LAND_POLL_SEC)

    def move_to_shop(self) -> bool:
        """店の正面へ。柵を越えられなければ False。止まったら Stopped。どちらでも入力は離す"""
        started = self.clock()
        try:
            self._check()
            self.osc.send("/input/MoveLeft", 1)
            if self.speed is None or self._speed_now() is None:
                crossed = self._cross_fence_fixed()     # 速さを受け取れない窓は今の決め打ち
            else:
                crossed = self._cross_fence_by_speed(started)
            if not crossed:
                return False
            self.osc.send("/input/MoveLeft", 0)     # 柵の向こうに着いた。すぐ左を離す
            self.log(f"アイテム取得: 柵を越えた {self.clock() - started:.2f}秒")
            self.osc.send("/input/MoveBackward", 1)
            self._wait(BACK_SEC)
            self.osc.send("/input/MoveLeft", 1)
            self._wait(BACK_LEFT_SEC)
            self.osc.send("/input/MoveBackward", 0)
            self.osc.send("/input/MoveLeft", 0)
            self.osc.send("/input/LookLeft", 1)
            self._wait(TURN_SEC)
            self.osc.send("/input/LookLeft", 0)
            self.log(f"アイテム取得: 店の前に着いた {self.clock() - started:.2f}秒")
            return True
        finally:
            self.osc.stop_all(repeat=1)         # 途中でやめても押したままにしない

    # ── 視点を戻す（どの終わり方でも。縦だけ）──
    def restore_view(self):
        """送ったマウスの相対移動の縦の合計を逆向きに、同じ刻み（6px・STEP_SEC）で送って戻す。止めない。
        横は戻さない（ラウンド突入などマップが変わると向きは強制的にそろう。依頼者）"""
        dy = -self.mouse.total[1]
        if not dy:
            return
        for sx, sy in split_move(0, dy):
            self.mouse.move_rel(sx, sy)
            self.sleep(STEP_SEC)
        self.log(f"アイテム取得: 視点を戻しました（縦 {dy}）")

    # ── 3. 店の画面の全体を映す・店のボタン ──
    def frame_panel(self, shop: str, plush_row: bool = False):
        """見本の四隅が撮影に入るまで視点を動かす（横が先。横が入って縦がはみ出すときだけ縦）。
        入っていれば動かさない（感度も測らない）。入った撮影の (撮影, 照準, locate の結果) か None。
        plush_row（一覧の道）なら、Have Plush の行の点が HUD に止められる位置のとき（row_blocked）も、
        ボタンの扱いのあとで、視点を横に動かしてその点を PLUSH_ROW_SAFE_X へ寄せる（縦は動かさない）"""
        point = BUTTONS[shop]
        misses = moves = 0
        while True:
            self._check()
            shot = self.capture()
            if shot is not None:
                self.last_shot = shot[0]
            found = self.locate(shot[0], point) if shot is not None else None
            if found is None or len(found) < 6 or not callable(found[5]):
                misses += 1
                self.log(f"アイテム取得: 店の画面 {misses} 回目: 見つかりません")
                if misses > MISS_TRIES:
                    self.log("アイテム取得: 店の画面が見つかりません")
                    self._miss("店の画面")
                    return None
                self._wait(MISS_SEC)
                continue
            img, aim = shot
            project = found[5]
            out_x, out_y = panel_outside(panel_corners(project), (img.shape[1], img.shape[0]))
            cx, cy = project((TEMPLATE_SIZE[0] / 2, TEMPLATE_SIZE[1] / 2))[:2]
            dx, dy = cx - aim[0], cy - aim[1]
            height = (self.client_height() if self.client_height else None) or img.shape[0]
            boxes = [(name, button_box(project, pt)) for name, pt in (("店のボタン", BUTTONS[shop]),
                                                                       ("Equip", BUTTONS["Equip"]))]
            hud = [name for name, box in boxes if box_in_hud(box, aim, height)]
            row_pt = project(PLUSH_ROW)[:2] if plush_row else None
            blocked = row_blocked(row_pt, aim, height) if plush_row else False
            where = ("入っている" if not (out_x or out_y)
                     else "はみ出す（" + "・".join(n for n, o in (("横", out_x), ("縦", out_y)) if o) + "）")
            self.log(f"アイテム取得: 店の画面: 手がかり {found[4]} 点・四隅 {where}・"
                     f"HUD に重なる点 {'・'.join(hud + ['Plush の行'] if blocked else hud) or 'なし'}・"
                     f"真ん中のずれ 横 {dx:.1f}・縦 {dy:.1f}（動かした {moves} 回）・見た箱 "
                     + "／".join(f"{name} {box_ratio(box, aim, height)}" for name, box in boxes)
                     + (f"／Plush の行 横 {(row_pt[0] - aim[0]) / height:+.3f}・縦 {(row_pt[1] - aim[1]) / height:+.3f}"
                        if plush_row else ""))
            if not (out_x or out_y or hud or blocked):
                return img, aim, found
            shift = 0.0
            if hud and not (out_x or out_y):
                # 板は全部見えていて HUD に当たるだけ → 下を見ず、横にずらして帯の外へ（2026-11-17）。
                # ずらすと板がはみ出す・ずらす量が PANEL_SETTLE_PX 以下なら、今までの枝へ落とす
                want_x = hud_shift_x(boxes, hud, aim, height)
                if abs(want_x) > PANEL_SETTLE_PX and not any(
                        shifted_outside(panel_corners(project), want_x, (img.shape[1], img.shape[0]))):
                    shift = want_x
            # はみ出している・押す点が HUD に重なる → 店の画面の真ん中を照準へ寄せる（横が先。縦は要るときだけ）
            if not (out_x or out_y or hud):
                # 行の点だけが HUD に重なる（ボタンの扱いが先）→ 視点を横だけ動かして、行の点を SAFE へ寄せる（板が右へ動く）
                want = ((row_pt[0] - aim[0]) - PLUSH_ROW_SAFE_X * height, 0.0)
                if abs(want[0]) <= PANEL_SETTLE_PX:
                    self.log("アイテム取得: 押す点が HUD に重なります（Plush の行）")
                    return None
            elif shift:
                want = (shift, 0.0)             # 店のボタン・Equip だけが HUD → 横に 1 回ずらす（縦は動かさない）
            elif (out_x or (hud and not out_y)) and abs(dx) > PANEL_SETTLE_PX:
                want = (dx, 0.0)                # 横が先
            elif (out_y or hud) and abs(dy) > PANEL_SETTLE_PX:
                want = (0.0, dy)
            elif out_x or out_y:
                self.log("アイテム取得: 店の画面が入りきりません（真ん中に寄せても はみ出す）")
                return None
            else:
                self.log(f"アイテム取得: 押す点が HUD に重なります（真ん中に寄せても {'・'.join(hud)}）")
                return None
            if moves >= PANEL_TRIES:
                self.log(f"アイテム取得: 店の画面が入りきりません（{moves} 回動かした）")
                return None
            if self.gain is None:
                if not self._use_gain(shop):
                    return None
                if self.measured_gain is not None:
                    continue                    # 測りで視点が動いた。撮り直してから決める
            units = tuple(max(-MOVE_CAP, min(MOVE_CAP, d / g)) for d, g in zip(want, self.gain))
            self.log(f"アイテム取得: 店の画面: 視点を動かす 横 {units[0]:.1f}・縦 {units[1]:.1f}")
            for sx, sy in split_move(*units):
                self.mouse.move_rel(sx, sy)
                self.sleep(STEP_SEC)
            moves += 1
            self._wait(AFTER_MOVE_SEC)

    def _use_gain(self, shop: str) -> bool:
        """視点を動かす感度を決める。保存した値があればそれ、無ければ測る"""
        if self.saved_gain:
            self.gain = tuple(self.saved_gain)
            self.gain_from_saved = True
            self.log(f"アイテム取得: 感度 保存した値を使います（横 {self.gain[0]:.3f}・縦 {self.gain[1]:.3f}）")
            return True
        return self.calibrate(shop)

    def press_shop(self, shop: str, framed) -> bool:
        """全体が映った撮影で、店のボタンの点へ Tab ＋カーソルで押す。同じ写し方で Equip の点も覚える"""
        _img, _aim, found = framed
        x, y = found[:2]
        ex, ey, esx, esy = found[5](BUTTONS["Equip"])
        self.equip_point = ((ex, ey), (esx, esy))
        self._check()
        ok = self.mouse.click_at(x, y)
        self.log(f"アイテム取得: {shop} のボタン: 点 ({x:.1f}, {y:.1f})（撮影）・Tab＋カーソルで"
                 f"{'押した' if ok else '押せません'}（Equip の点 ({ex:.1f}, {ey:.1f})）")
        return ok

    def _equip_target(self, first: bool) -> tuple:
        """Equip を押す点と、どう決めたか。覚えた点の周りの緑の文字の重心、無ければ覚えた点。
        1回目は店が開くのを SHOP_OPEN_WAIT_SEC まで待つ（緑が出るまで撮り直す）"""
        pos, scale = self.equip_point
        deadline = self.clock() + (SHOP_OPEN_WAIT_SEC if first else 0.0)
        while True:
            self._check()
            shot = self.capture()
            center = green_center(shot[0], pos, scale) if shot is not None else None
            if center is not None:
                return center, "緑の文字"
            if self.clock() >= deadline:
                return pos, "覚えた点"
            self._wait(MISS_SEC)

    # ── 4. Equip とログ ──
    def _wait_equip(self, seq_before: int):
        """次の Equipping の id。来なければ None"""
        deadline = self.clock() + EQUIP_WAIT_SEC
        while self.clock() < deadline:
            # 先に結果を見る（装備できると、ログの流れが装備待ちを解き「装備した」で止まるため）
            seq, item_id = self.equip_seen()
            if seq != seq_before:
                return item_id
            self._check()
            self.sleep(EQUIP_POLL_SEC)
        return None

    def _evolution_spot(self, target_id: int, equip_pos):
        """失くしたのが進化アイテム（EVOLUTIONS）なら、Equip の右に並ぶ色文字のうち、その色の 1 つの点
        （画面の px）。表に無い番号は撮らずに None（今までどおり Equip）。色文字が見つからない・色が合わない・
        割れたときも None（間違ったものを押すより、Equip に落ちて失敗で終わる方がまし）"""
        want = EVOLUTIONS.get(target_id)
        if want is None:
            return None
        self._check()
        shot = self.capture()
        if shot is None:
            return None
        labels = evolution_labels(shot[0], equip_pos, self.equip_point[1])
        picked = pick_evolution(labels, want)
        if picked is None:
            self.log(f"アイテム取得: 進化 {target_id}: 色文字 {len(labels)} 個から色が合う 1 つを選べません"
                     "（Equip を押します）")
            return None
        self.log(f"アイテム取得: 進化 {target_id}: 色文字 {len(labels)} 個から選んだ（色相 {picked[2]:.0f}・"
                 f"彩度 {picked[3]:.0f}・明るさ {picked[4]:.0f}）")
        return picked[0], picked[1]

    def equip(self, target_id: int) -> bool:
        for attempt in range(1, EQUIP_TRIES + 1):
            seq_before = self.equip_seen()[0]
            (x, y), how = self._equip_target(first=attempt == 1)
            spot = self._evolution_spot(target_id, (x, y))
            if spot is not None:
                x, y = spot
                how += "・進化"
            self._check()
            ok = self.mouse.click_at(x, y)
            self.log(f"アイテム取得: Equip: 点 ({x:.1f}, {y:.1f})（撮影・{how}）・Tab＋カーソルで"
                     f"{'押した' if ok else '押せません'}（{attempt}/{EQUIP_TRIES}回目）")
            if not ok:
                return False
            got = self._wait_equip(seq_before)
            if got == target_id:
                self.log(f"アイテム取得: Equip → Equipping {got}")
                return True
            self.log(f"アイテム取得: Equip → {'来ない' if got is None else f'Equipping {got}'}"
                     f"（{attempt}/{EQUIP_TRIES}回目）")
        return False

    # ── 視点の感度（gain）を測る ──
    def _find(self, point):
        """見本の point が写っている位置。見つからなければ撮り直し（MISS_TRIES 回まで）、だめなら None"""
        for misses in range(MISS_TRIES + 1):
            self._check()
            shot = self.capture()
            found = self.locate(shot[0], point) if shot is not None else None
            if found is not None:
                return found[:2]
            if misses < MISS_TRIES:
                self._wait(MISS_SEC)
        return None

    def _calib_units(self, axis: int) -> int:
        """測りで送る量。前にうまくいった gain があれば、画面で約 CALIB_PX 動く量"""
        if not self.saved_gain:
            return CALIB_UNITS
        low, high = CALIB_UNITS_RANGE
        return int(max(low, min(high, round(CALIB_PX / self.saved_gain[axis]))))

    def calibrate(self, name: str) -> bool:
        """視点を動かす前に、横・縦それぞれ小さく動かして撮り直し、実際の動きから gain を
        測る。GAIN_SANE の外なら1回測り直し、だめなら False。店の画面が見つからなければ False"""
        point = BUTTONS[name]
        gain = []
        for axis, label in ((0, "横"), (1, "縦")):
            units = self._calib_units(axis)
            for attempt in (1, 2):
                before = self._find(point)
                if before is None:
                    self.log("アイテム取得: 感度を測れません（店の画面が見つかりません）")
                    return False
                for dx, dy in split_move(units if axis == 0 else 0, units if axis == 1 else 0):
                    self.mouse.move_rel(dx, dy)
                    self.sleep(STEP_SEC)
                self._wait(AFTER_MOVE_SEC)
                after = self._find(point)
                if after is None:
                    self.log("アイテム取得: 感度を測れません（店の画面が見つかりません）")
                    return False
                g = (before[axis] - after[axis]) / units
                if GAIN_SANE[0] <= g <= GAIN_SANE[1]:
                    gain.append(g)
                    break
                self.log(f"アイテム取得: {label}の感度 {g:.3f} は範囲外（{attempt}/2回目）")
            else:
                if not self.saved_gain:
                    return False
                # 2回とも範囲外: 前にうまくいった値があればそれで続ける
                gain.append(float(self.saved_gain[axis]))
                self.log(f"アイテム取得: {label}の感度は保存した値 {gain[-1]:.3f} で続けます")
        self.gain = tuple(gain)
        self.gain_from_saved = False
        self.measured_gain = tuple(gain)    # 保存してよいのは測った値だけ
        self.log(f"アイテム取得: 感度 横 {gain[0]:.3f}・縦 {gain[1]:.3f}")
        return True

    def _frame(self, shop: str, plush_row: bool = False):
        """全体を映す。保存した感度で映せなければ、測り直して1回やり直す。plush_row は frame_panel へ素通し"""
        framed = self.frame_panel(shop, plush_row)
        if framed is None and self.gain_from_saved:
            self.log("アイテム取得: 感度 測り直し（保存した値で店の画面が入りきらない）")
            self.saved_gain = None      # 合わなかった値で測りの量を決めない・頼らない
            self.gain = None
            self.gain_from_saved = False
            framed = self.frame_panel(shop, plush_row)
        return framed

    def buy(self, shop: str, target_id: int) -> bool:
        """店の前で、窓が前に出ている状態から。全体を映す → 店のボタン → Equip。
        保存した感度で視点を動かして映せなければ、測り直して1回やり直す"""
        framed = self._frame(shop)
        if framed is None:
            return False
        # Tab は店の操作の間に1回だけ押したまま（依頼者「タブ→クリック→クリック→タブ離す」）。
        # 視点はもう動かさない。どの終わり方でも、前面を返す・視点を戻すより前（ここ）で離す
        self.mouse.tab_down()
        try:
            self._wait(config.FETCH_TAB_SETTLE_SEC)     # VRChat が Tab を読んでカーソルを真ん中へ戻し終える
            return self.press_shop(shop, framed) and self.equip(target_id)
        finally:
            self.mouse.tab_up()

    # ── Guidance Plush（DZ）─────────────────────
    def buy_plush(self, target_id: int) -> bool:
        """店の前で、窓が前に出ている状態から。全体を映す → Event → つまみをドラッグして Have Plush の
        行を出す → 行（緑の文字）を押す → Equip。点はすべて最初の画面の位置合わせから写す（Event の
        後に撮り直して位置合わせはしない）。Equipping target_id が来ない・違う番号なら、行を選ぶ
        ところから 1 回やり直す"""
        framed = self._frame("Event", plush_row=True)
        if framed is None:
            return False
        project = framed[2][5]
        self.knob_at = None             # 店を開くと、つまみは一番上（全画面・小さい窓とも）
        self.mouse.tab_down()
        try:
            self._wait(config.FETCH_TAB_SETTLE_SEC)
            if not self.press_shop("Event", framed):
                return False
            self._wait(PLUSH_OPEN_WAIT_SEC)
            for attempt in range(1, PLUSH_ROUNDS + 1):
                row = self._show_plush_row(project)
                if row is None:
                    self.log("アイテム取得: Have Plush の行が見つかりません")
                    self._miss("Have Plush の行")
                    self._keep_shot(self.before_shot, "Plush_引く前")
                    self._keep_shot(self.mid_shot, "Plush_引いている途中")
                    return False
                self._check()
                before = self.last_shot         # 行を押す直前の撮影（失敗したときだけ残す）
                if not self.mouse.click_at(*row):
                    return False
                self.log(f"アイテム取得: Have Plush の行を押した ({row[0]:.1f}, {row[1]:.1f})")
                if self._equip_once(target_id, first=attempt == 1):
                    return True
                self.log(f"アイテム取得: Have Plush の装備を確かめられません（{attempt}/{PLUSH_ROUNDS}回目）")
                self._keep_shot(before, "Plush_押す前")
                self._keep_shot(self._capture_bgr(), "Plush_押した後")
            return False
        finally:
            self.mouse.tab_up()

    def _show_plush_row(self, project):
        """つまみを一番上から PLUSH_KNOB_TARGET（＋ずらし）へドラッグして、Have Plush の行の緑の重心を返す。
        緑が無ければ、一番上へ戻してから、ずらした先へもう一度（最大 len(PLUSH_SHIFTS) 回）。無ければ None"""
        top = project(PLUSH_KNOB_TOP)[:2]
        for shift in PLUSH_SHIFTS:
            target = project((PLUSH_KNOB_TARGET[0], PLUSH_KNOB_TARGET[1] + shift))[:2]
            if self.knob_at is not None:
                self._drag(self.knob_at, top, "一番上へ戻す")
                self.knob_at = None
            # 引く前のつまみを測る（記録だけ。振る舞いは変えない）
            first = self.capture()
            self.before_shot = first[0] if first else None
            self.mid_shot = None
            _tx, _ty, ksx, ksy = project(PLUSH_KNOB_TOP)
            before = knob_band(first[0], top, (ksx, ksy)) if first else None
            self.log(f"アイテム取得: つまみ 実測（引く前）: " + _knob_text(before, top))
            self._drag(top, target, f"ずらし {shift:+d}",
                       on_mid=lambda: setattr(self, "mid_shot", self._capture_bgr()))
            self.knob_at = target
            self._wait(DRAG_SETTLE_SEC)
            shot = self.capture()
            if shot is not None:
                self.last_shot = shot[0]
            after = knob_band(shot[0], top, (ksx, ksy)) if shot is not None else None
            if before is None or after is None:
                self.log("アイテム取得: つまみ 実測（引いた後）: 比べられません")
            else:
                moved = abs(after[1] - before[1])
                self.log(f"アイテム取得: つまみ 実測（引いた後）: y {after[1]}〜{after[2]}（"
                         + (f"動いた {moved} px" if moved >= KNOB_MOVED_PX * ksy else "動いていない") + "）")
            rx, ry, sx, sy = project(PLUSH_ROW)
            green = (green_center(shot[0], (rx, ry), (sx, sy), PLUSH_ROW_WINDOW)
                     if shot is not None else None)
            center, text = green, "緑なし"
            if green is not None:
                ok, how = row_order_ok(shot[0], green, (sx, sy))
                text = f"緑 ({green[0]:.1f}, {green[1]:.1f})" + (f"・並び {how}" if ok else f"・並びが違う（{how}）")
                if not ok:
                    center = None               # 上下の並びが Have Plush でない（Jailbird など）→ 緑なしと同じ扱い
            self.log(f"アイテム取得: Have Plush の行 ({rx:.1f}, {ry:.1f}): " + text)
            if center is not None:
                return center
        return None

    def _miss(self, why: str):
        """見つからなかった。直前の撮影を残す（ER。残すのは呼び出し側の on_miss）"""
        if self.on_miss is not None and self.last_shot is not None:
            try:
                self.on_miss(self.last_shot, why)
            except Exception:
                pass

    def _keep_shot(self, bgr, why: str):
        """装備を確かめられなかったときの撮影を残す（FN。店の画面なので名前は写らない。残すのは on_miss）。
        保存に失敗しても取得は止めない"""
        if self.on_miss is not None and bgr is not None:
            try:
                self.on_miss(bgr, why)
            except Exception:
                pass

    def _capture_bgr(self):
        """今の画面を撮る。撮れなければ None（残すだけの撮影なので、失敗しても取得は止めない）"""
        if self.on_miss is None:
            return None
        try:
            shot = self.capture()
            return shot[0] if shot is not None else None
        except Exception:
            return None

    def _drag(self, start, end, what: str, on_mid=None):
        self._check()
        if not self.mouse.drag(start, end, DRAG_STEPS, DRAG_STEP_SEC, on_mid=on_mid):
            raise Stopped("front_lost")
        self.log(f"アイテム取得: つまみをドラッグ（{what}） ({start[0]:.1f}, {start[1]:.1f}) → "
                 f"({end[0]:.1f}, {end[1]:.1f})")

    def _equip_once(self, target_id: int, first: bool) -> bool:
        """Equip を 1 回押して、Equipping target_id を確かめる"""
        seq_before = self.equip_seen()[0]
        (x, y), how = self._equip_target(first=first)
        self._check()
        if not self.mouse.click_at(x, y):
            return False
        got = self._wait_equip(seq_before)
        self.log(f"アイテム取得: Equip: 点 ({x:.1f}, {y:.1f})（{how}）→ "
                 + ("来ない" if got is None else f"Equipping {got}"))
        return got == target_id
