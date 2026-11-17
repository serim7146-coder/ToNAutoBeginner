"""続行中の窓を OBS のシーンに並べるための計算。純粋（通信も窓の操作もしない）。

数・画布・モニタと窓の矩形を受け取って、OBS へ送る中身（シーンアイテムの変換など）を返すだけ。
送るのは Recorder.py、窓の矩形を取るのは WindowOperator.py / mainGUI.py。

座標はどれもデスクトップ全体の座標で、矩形は (左, 上, 右, 下)。サブモニタは負になる
（例: x が -1927〜7）。モニタの中での位置は「窓 − モニタ」で出す。
"""
import math
import re

import config

# obs-websocket の alignment（左=1 / 右=2 / 上=4 / 下=8 の足し算）。左上
ALIGN_TOP_LEFT = 5
BOUNDS_SCALE_INNER = "OBS_BOUNDS_SCALE_INNER"      # 縦横比を保って枠に収まる大きさにする


def grid_for(count: int) -> tuple:
    """並べる (列数, 行数)。N≤3 は 1 行、N≥4 は 列=ceil(sqrt(N))・行=ceil(N/列)。N<1 は 1×1"""
    n = max(1, int(count))
    if n <= 3:
        return n, 1
    cols = math.ceil(math.sqrt(n))
    return cols, math.ceil(n / cols)


def cells(count: int, canvas: tuple) -> list:
    """画布（幅, 高さ）を格子に割った枠 [(x, y, 幅, 高さ)]。count 個。左上から右へ、次の行へ。

    端数は隙間にも重なりにもならないよう、境界を整数に丸めて割る
    """
    n = max(1, int(count))
    width, height = int(canvas[0]), int(canvas[1])
    cols, rows = grid_for(n)
    out = []
    for i in range(n):
        col, row = i % cols, i // cols
        x0, x1 = col * width // cols, (col + 1) * width // cols
        y0, y1 = row * height // rows, (row + 1) * height // rows
        out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def crop_for(monitor: tuple, window: tuple):
    """モニタの中での窓の位置を、クロップ (左, 上, 右, 下) にする。

    モニタからはみ出した分は 0（負にしない）。モニタと重ならない窓は None
    """
    ml, mt, mr, mb = (int(v) for v in monitor)
    wl, wt, wr, wb = (int(v) for v in window)
    if min(wr, mr) - max(wl, ml) <= 0 or min(wb, mb) - max(wt, mt) <= 0:
        return None
    return (max(0, wl - ml), max(0, wt - mt), max(0, mr - wr), max(0, mb - wb))


def item_transform(cell: tuple, monitor: tuple, window: tuple):
    """1 つのソースの SetSceneItemTransform の sceneItemTransform。

    モニタの中での窓の位置をクロップにして、枠いっぱいに収める（縦横比は保つ）。
    窓がモニタと重ならないときは None（並べない）
    """
    crop = crop_for(monitor, window)
    if crop is None:
        return None
    x, y, width, height = cell
    left, top, right, bottom = crop
    return {
        "positionX": float(x),
        "positionY": float(y),
        "rotation": 0.0,
        "scaleX": 1.0,
        "scaleY": 1.0,
        "alignment": ALIGN_TOP_LEFT,
        "boundsType": BOUNDS_SCALE_INNER,
        "boundsAlignment": 0,                   # 枠の中央
        "boundsWidth": float(width),
        "boundsHeight": float(height),
        "cropLeft": left,
        "cropTop": top,
        "cropRight": right,
        "cropBottom": bottom,
    }


# ── ソース名 ─────────────────────────────
def source_name(window_idx: int) -> str:
    return config.OBS_SOURCE_NAME.format(idx=window_idx)


def is_tool_source(name: str) -> bool:
    """このツールが作るソースの名前か（利用者のソースを隠さないために）"""
    prefix, _sep, suffix = config.OBS_SOURCE_NAME.partition("{idx}")
    if not isinstance(name, str) or not name.startswith(prefix) or not name.endswith(suffix):
        return False
    end = len(name) - len(suffix)
    return end > len(prefix) and name[len(prefix):end].isdigit()


# ── モニタの選び方 ───────────────────────
_MONITOR_LABEL = re.compile(r"(\d+)\s*x\s*(\d+)\s*@\s*(-?\d+)\s*,\s*(-?\d+)")


def pick_monitor(items: list, monitor: tuple):
    """OBS が返した選択肢（GetInputPropertiesListPropertyItems の propertyItems）から、
    モニタの矩形に合うものの itemValue を返す。合うものが無ければ None。

    OBS のモニタキャプチャの選択肢の名前は「… 1: 1920x1080 @ -1927,357」の形
    （大きさ @ 左上）。位置と大きさが両方合うものを先に、無ければ位置だけで選ぶ。
    選べない（使えない）選択肢は飛ばす
    """
    ml, mt, mr, mb = (int(v) for v in monitor)
    size_and_pos = None
    pos_only = None
    for item in items or []:
        if not isinstance(item, dict) or item.get("itemEnabled") is False:
            continue
        m = _MONITOR_LABEL.search(str(item.get("itemName", "")))
        if not m or "itemValue" not in item:
            continue
        width, height, left, top = (int(v) for v in m.groups())
        if (left, top) != (ml, mt):
            continue
        if (width, height) == (mr - ml, mb - mt):
            if size_and_pos is None:
                size_and_pos = item["itemValue"]
        elif pos_only is None:
            pos_only = item["itemValue"]
    return size_and_pos if size_and_pos is not None else pos_only


# ── 映像ソースの種類の選び方 ─────────────
def pick_input_kind(kinds: list):
    """OBS が返した種類の一覧（GetInputKindList の inputKinds）から、画面（モニタ）を
    丸ごと取るものを選ぶ。名前は OBS の版や環境で変わるので、一覧にあるものから探す。
    見つからなければ None"""
    names = [k for k in (kinds or []) if isinstance(k, str)]
    for k in names:
        if k == "monitor_capture":
            return k
    for k in names:
        if "monitor_capture" in k:
            return k
    for k in names:
        if "display_capture" in k:
            return k
    return None
