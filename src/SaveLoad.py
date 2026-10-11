"""入室後に Save/Load のコード欄へクリップボードのセーブコードを貼る（DY）。絵を探すところと
クリップボードだけ。前面・Tab・クリックの流れは ToNEntry.load_code。

**セーブコードは、画面のログ・debug.log・撮影・テストの出力のどこにも出さない（長さも出さない）。**
ここの関数はコードを返す・受け取るだけで、書き出さない。

- コード欄の見本: 全画面 2560×1440 の窓の x 1900〜2150・y 700〜840（コード欄の左寄りと、上下の説明の
  左寄り）。倍率は「窓のクライアントの高さ / 1440」を中心に ±0.05（0.01 刻み）。クリックの点は見本の
  左上から (100, 64)×倍率
- 入力欄の見本: 入力欄の右の送信の矢印（1280×720 に縮めた撮影から切り出した。倍率の基準は高さ 720）。
  窓の下寄り・右寄りの範囲だけを探す（入力欄が出ていない撮影では 0.48 前後・出ていれば 1.0。
  2026-10-12 の実機の撮影 9 枚）
"""
import threading

import config
import DebugLog

CODEBOX_FILE = "saveload_templates/codebox_left.png"
ARROW_FILE = "saveload_templates/send_arrow.png"
CODEBOX_BASE_HEIGHT = 1440          # コード欄の見本を作った窓の高さ
ARROW_BASE_HEIGHT = 720             # 矢印の見本を作った撮影の高さ
CLICK_OFFSET = (100, 64)            # コード欄の見本の左上からクリックの点まで（倍率 1）
MATCH_MIN = 0.8
SCALE_SPAN, SCALE_STEP = 0.05, 0.01
ARROW_REGION = (0.45, 0.55, 0.95, 0.95)     # 入力欄の矢印を探す範囲（窓の割合。左・上・右・下）
ARROW_SCALES = (0.95, 1.0, 1.05)
LOADED_LINE = "Loaded Data Successfully"

_lock = threading.Lock()
_templates = {}


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


def find_codebox(bgr, client_height: int, template=None):
    """撮影（BGR）の中のコード欄のクリックの点 (x, y, 一致, 倍率)。MATCH_MIN 未満・見本が無いなら None。
    一致の値は返すが、呼ぶ側は撮影もコードも書き出さない"""
    import cv2
    template = _load(CODEBOX_FILE) if template is None else template
    if template is None or len(template) == 0 or not hasattr(bgr, "shape") or client_height <= 0:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hit = _best(gray, template, codebox_scales(client_height))
    if hit is None or hit[2] < MATCH_MIN:
        return None
    x, y, score, s = hit
    return x + CLICK_OFFSET[0] * s, y + CLICK_OFFSET[1] * s, score, s


def input_box_shown(bgr, client_height: int, template=None) -> bool:
    """VRChat の入力欄（送信の矢印）が窓の下寄り・右寄りに出ているか"""
    import cv2
    template = _load(ARROW_FILE) if template is None else template
    if template is None or len(template) == 0 or not hasattr(bgr, "shape") or client_height <= 0:
        return False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    left, top, right, bottom = ARROW_REGION
    x0, y0 = int(w * left), int(h * top)
    roi = gray[y0:int(h * bottom), x0:int(w * right)]
    base = client_height / ARROW_BASE_HEIGHT
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
