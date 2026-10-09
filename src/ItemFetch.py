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
import math
import threading
import time

import config

TEMPLATE_FILE = "shop_templates/shop_main.png"   # 店の最初の画面（1920x1080 の x760〜1140・y245〜505）
TEMPLATE_SIZE = (380, 260)  # 見本の幅・高さ（四隅を写すのに使う）
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
JUMP_AFTER_SEC = 0.30       # 左を押してから1回目のジャンプまで
JUMP_GAP_SEC = 0.14         # 1回目と2回目のジャンプの間
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
_template = None            # (keypoints, descriptors)。読めなければ ()


def _sift():
    import cv2
    return cv2.SIFT_create()


def _features(gray):
    return _sift().detectAndCompute(gray, None)


def _load_template():
    """見本を読む（1回だけ）。cv2.imread は日本語を含むパスを読めないのでバイトで読む"""
    global _template
    with _lock:
        if _template is not None:
            return _template
        try:
            import cv2
            import numpy as np
            data = np.fromfile(str(config.resource_path(TEMPLATE_FILE)), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
            keypoints, descriptors = _features(img) if img is not None else (None, None)
            _template = (keypoints, descriptors) if descriptors is not None else ()
        except Exception:
            _template = ()
        return _template


def available() -> bool:
    """使えるか（OpenCV の SIFT が使えて、見本が読める）"""
    return bool(_load_template())


def locate(bgr, point, template=None):
    """撮影（BGR）の中で、見本の point（見本の中の座標）が写っている位置と倍率と手がかりの数と写す関数
    (x, y, 横の倍率, 縦の倍率, インライアの数, project)。倍率は見本の 1px が画面で何 px か（点の
    ±SCALE_PROBE を写した距離から）。project(見本の点) は同じホモグラフィで別の点を (x, y, 横, 縦の倍率) に
    写す（店のボタンを押す回に Equip の位置も覚えるため）。見つからなければ None"""
    import cv2
    import numpy as np
    template = template or _load_template()
    if not template:
        return None
    k1, d1 = template
    k2, d2 = _features(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    if d2 is None or len(k2) < 2:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
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


def green_center(bgr, pos, scale):
    """pos（画面の点）の周りの窓の緑の画素の重心 (x, y)。少なければ・撮影が無ければ None"""
    try:
        import cv2
        import numpy as np
        if not hasattr(bgr, "shape"):
            return None
        sx, sy = scale
        h, w = bgr.shape[:2]
        x0 = max(0, int(round(pos[0] - GREEN_WINDOW[0] * sx)))
        x1 = min(w, int(round(pos[0] + GREEN_WINDOW[0] * sx)) + 1)
        y0 = max(0, int(round(pos[1] - GREEN_WINDOW[1] * sy)))
        y1 = min(h, int(round(pos[1] + GREEN_WINDOW[1] * sy)) + 1)
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


def panel_outside(corners, size) -> tuple:
    """四隅が撮影の端の余白（PANEL_MARGIN）の外へはみ出しているか (横, 縦)。size は撮影の (幅, 高さ)"""
    w, h = size
    mx, my = PANEL_MARGIN * w, PANEL_MARGIN * h
    return (any(x < mx or x > w - mx for x, _y in corners),
            any(y < my or y > h - my for _x, y in corners))


# ── 取りに行く ──────────────────────────────────

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


class Stopped(Exception):
    """ラウンド開始・停止・時間切れ（理由は args[0]）"""


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
                 locate_fn=locate, sleep=None, clock=None, saved_gain=None):
        self.osc = osc
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

    def move_to_shop(self) -> bool:
        """店の正面へ。着地2回が来なければ False。止まったら Stopped。どちらでも入力は離す"""
        started = self.clock()
        try:
            self._check()
            self.osc.send("/input/MoveLeft", 1)
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
                        break               # 柵の向こうに着いた。すぐ左を離す
                self.sleep(LAND_POLL_SEC)
            self.osc.send("/input/MoveLeft", 0)
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
        self.log(f"アイテム取得: 視点を戻した（縦 {dy}）")

    # ── 3. 店の画面の全体を映す・店のボタン ──
    def frame_panel(self, shop: str):
        """見本の四隅が撮影に入るまで視点を動かす（横が先。横が入って縦がはみ出すときだけ縦）。
        入っていれば動かさない（感度も測らない）。入った撮影の (撮影, 照準, locate の結果) か None"""
        point = BUTTONS[shop]
        misses = moves = 0
        while True:
            self._check()
            shot = self.capture()
            found = self.locate(shot[0], point) if shot is not None else None
            if found is None or len(found) < 6 or not callable(found[5]):
                misses += 1
                self.log(f"アイテム取得: 店の画面 {misses} 回目: 見つからない")
                if misses > MISS_TRIES:
                    self.log("アイテム取得: 店の画面が見つかりません")
                    return None
                self._wait(MISS_SEC)
                continue
            img, aim = shot
            project = found[5]
            out_x, out_y = panel_outside(panel_corners(project), (img.shape[1], img.shape[0]))
            cx, cy = project((TEMPLATE_SIZE[0] / 2, TEMPLATE_SIZE[1] / 2))[:2]
            dx, dy = cx - aim[0], cy - aim[1]
            where = ("入っている" if not (out_x or out_y)
                     else "はみ出す（" + "・".join(n for n, o in (("横", out_x), ("縦", out_y)) if o) + "）")
            self.log(f"アイテム取得: 店の画面: 手がかり {found[4]} 点・四隅 {where}・"
                     f"真ん中のずれ 横 {dx:.1f}・縦 {dy:.1f}（動かした {moves} 回）")
            if not (out_x or out_y):
                return img, aim, found
            if out_x and abs(dx) > PANEL_SETTLE_PX:
                want = (dx, 0.0)                # 横が先
            elif out_y and abs(dy) > PANEL_SETTLE_PX:
                want = (0.0, dy)
            else:
                self.log("アイテム取得: 店の画面が入りきりません（真ん中に寄せても はみ出す）")
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
            self.log(f"アイテム取得: 感度 保存した値を使う（横 {self.gain[0]:.3f}・縦 {self.gain[1]:.3f}）")
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

    def equip(self, target_id: int) -> bool:
        for attempt in range(1, EQUIP_TRIES + 1):
            seq_before = self.equip_seen()[0]
            (x, y), how = self._equip_target(first=attempt == 1)
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

    def buy(self, shop: str, target_id: int) -> bool:
        """店の前で、窓が前に出ている状態から。全体を映す → 店のボタン → Equip。
        保存した感度で視点を動かして映せなければ、測り直して1回やり直す"""
        framed = self.frame_panel(shop)
        if framed is None and self.gain_from_saved:
            self.log("アイテム取得: 感度 測り直し（保存した値で店の画面が入りきらない）")
            self.saved_gain = None      # 合わなかった値で測りの量を決めない・頼らない
            self.gain = None
            self.gain_from_saved = False
            framed = self.frame_panel(shop)
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
