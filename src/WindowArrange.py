"""窓を整列する（DX）: 升の割り方と矩形の計算だけ。Windows の API は呼ばない（WindowOperator が動かす）

升の割り方（n ＝ 並べる窓の数）:
- 1 窓は作業領域いっぱい、2 窓は横に 2 つ（1 段）
- 3〜8 窓は 2 段で、列の数は ceil(n / 2)（8 窓 → 4×2）
- 9 窓以上は 3 段で、列の数は ceil(n / 3)
- 升が余るとき（端数）は、窓1 が左の列を上から縦に伸ばして、余りの数＋1 段ぶんを使う
  （2 段の 3・5・7 窓は左の列の上下。3 段で余りが 2 なら左の列の 3 段ぶん、余りが 1 なら
  上の 2 段ぶん）。残りの窓は、窓2 から左上 → 右 → 下の順に、空いている升へ
"""
import math


def grid(n: int) -> tuple[int, int, list]:
    """(段の数, 列の数, 窓ごとの (列, 段, 列の幅, 段の高さ))。窓の並びは渡した順（窓の番号の順）"""
    if n <= 0:
        return 0, 0, []
    if n == 1:
        rows, cols = 1, 1
    elif n == 2:
        rows, cols = 1, 2
    elif n <= 8:
        rows, cols = 2, math.ceil(n / 2)
    else:
        rows, cols = 3, math.ceil(n / 3)
    extra = rows * cols - n
    first_span = extra + 1          # 端数のとき窓1 が縦に伸びる段の数（余りが無ければ 1）
    places = [(0, 0, 1, first_span)]
    taken = {(0, r) for r in range(first_span)}
    free = [(c, r) for r in range(rows) for c in range(cols) if (c, r) not in taken]
    places += [(c, r, 1, 1) for c, r in free[:n - 1]]
    return rows, cols, places


def cells(area: tuple, n: int) -> list[tuple]:
    """作業領域 area（左, 上, 右, 下）を等分した、窓ごとの見えている枠の矩形（左, 上, 右, 下）。
    端の 1px のずれは右端・下端で吸収する（境目は整数で切り、隣とすき間も重なりも作らない）"""
    rows, cols, places = grid(n)
    left, top, right, bottom = area
    xs = [left + (right - left) * k // cols for k in range(cols)] + [right] if cols else []
    ys = [top + (bottom - top) * k // rows for k in range(rows)] + [bottom] if rows else []
    return [(xs[c], ys[r], xs[c + w], ys[r + h]) for c, r, w, h in places]


def outer_rect(visible: tuple, margins: tuple) -> tuple:
    """見えている枠を visible にするための、SetWindowPos に渡す矩形。margins は見えない縁
    （GetWindowRect と DWMWA_EXTENDED_FRAME_BOUNDS の差。左・上・右・下）"""
    left, top, right, bottom = visible
    ml, mt, mr, mb = margins
    return (left - ml, top - mt, right + mr, bottom + mb)


def size_text(n: int) -> str:
    """ログに出す並び（例 "3×2"）。列×段"""
    rows, cols, _places = grid(n)
    return f"{cols}×{rows}"
