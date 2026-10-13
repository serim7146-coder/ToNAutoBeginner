"""Begin の位置合わせの各段の撮影を、見つかった・見つからなかったに関係なく残す（EW。依頼者 2026-10-13）。

23:12 窓1 は位置合わせで「照準の右 +49px」→ 右へ 0.10 秒 →「+197px」とかえって遠くなった。見つかった段の撮影は
残していなかった（begin_miss は見つからなかったときだけ）ので、何を BEGIN と見たのか確かめられなかった。

1 回の位置合わせごとに %APPDATA%\\ToNAutoBeginner\\begin_adjust\\<時刻>_窓N\\ に、段ごとの撮影を
「NN_段.jpg」（1/2 の JPEG）で残す。見つかった段は、見つけた四角（今の照合・特徴点のどちらか）と照準を描き込む。
新しい KEEP 組まで。ほかのプレイヤーの名前が写りうるので、不具合の報告には撮影を入れるチェックがあるときだけ
（begin_miss と同じ扱い）。保存に失敗しても例外を外へ出さない（位置合わせを止めない）。
"""
import threading
from datetime import datetime
from pathlib import Path

import config
import DebugLog

DIR_NAME = "begin_adjust"
KEEP = 10                   # 残す組（1 回の位置合わせ）の数
SCALE = 0.5

_lock = threading.Lock()


def folder() -> Path:
    """置き場所（設定と同じ %APPDATA%\\ToNAutoBeginner の下。テストでは差し替わる）"""
    return Path(config.SETTINGS_PATH).parent / DIR_NAME


def new_group(window: int) -> str:
    """1 回の位置合わせの組の名前（時刻_窓N）"""
    return f"{datetime.now():%Y%m%d_%H%M%S_%f}_窓{window}"


def _bgr(bits: bytes, w: int, h: int):
    if not bits or w <= 0 or h <= 0 or len(bits) < w * h * 4:
        return None
    import numpy as np
    bgra = np.frombuffer(bits, dtype=np.uint8)[:w * h * 4].reshape(h, w, 4)
    return np.ascontiguousarray(bgra[:, :, :3])


def draw(bgr, hit, aim):
    """見つけた四角（緑）と照準（黄の十字）を描いた写しを返す"""
    import cv2
    img = bgr.copy()
    if hit is not None:
        x0, y0 = int(round(hit["cx"] - hit["w"] / 2)), int(round(hit["cy"] - hit["h"] / 2))
        x1, y1 = int(round(hit["cx"] + hit["w"] / 2)), int(round(hit["cy"] + hit["h"] / 2))
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 255, 0), 2)
        cv2.putText(img, str(hit.get("rule", "")), (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0), 1)
    if aim is not None:
        ax, ay = int(round(aim[0])), int(round(aim[1]))
        cv2.drawMarker(img, (ax, ay), (0, 255, 255), cv2.MARKER_CROSS, 24, 2)
    return img


def save(group: str, index: int, stage: str, bits: bytes, w: int, h: int, hit, aim) -> Path | None:
    """1 段の撮影を残す。見つかった段（hit）は四角と照準を描く。古い組は KEEP を超えた分を消す"""
    try:
        bgr = _bgr(bits, w, h)
        if bgr is None:
            return None
        import cv2
        img = draw(bgr, hit, aim) if hit is not None else bgr   # 描くのは見つかった段だけ
        small = cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        with _lock:
            d = folder() / group
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{index:02d}_{stage}{'' if hit is not None else '_なし'}.jpg"
            path.write_bytes(buf.tobytes())     # cv2.imwrite は日本語のパスに書けないのでバイトで書く
            groups = sorted(p for p in folder().iterdir() if p.is_dir())
            for old in groups[:-KEEP]:
                for f in old.iterdir():
                    f.unlink()
                old.rmdir()
        return path
    except Exception:
        DebugLog.exception("BeginAdjustShots.save")
        return None


def files_for(window) -> dict[str, bytes]:
    """不具合報告に入れる {zip の中の名前: JPEG}。window が 0（窓なし）なら全部の窓の分。古い順"""
    try:
        if not folder().is_dir():
            return {}
        out = {}
        for d in sorted(p for p in folder().iterdir() if p.is_dir()):
            if window and not d.name.endswith(f"_窓{window}"):
                continue
            for f in sorted(d.glob("*.jpg")):
                out[f"{DIR_NAME}/{d.name}/{f.name}"] = f.read_bytes()
        return out
    except Exception:
        DebugLog.exception("BeginAdjustShots.files_for")
        return {}
