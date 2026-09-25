"""画面のフォントを、その PC に在るものから選ぶ。

`"Segoe UI"` を名指しすると、配布した exe を別の PC で動かしたときに漢字が
出ないことがある（Segoe UI は日本語の字を持たず、Tk では Windows の代替
フォントが効かない環境がある）。日本語を持つフォントを在るものから選ぶ。

大きさと bold はそれぞれの呼び出し側のまま。ここでは family だけ決める。
参照は `UIFont.UI` のように属性で行う（`resolve()` の後に書き換わるため、
`from UIFont import UI` では古い値を掴む）。
"""
from tkinter import font as tkfont

# 今の Windows には必ず入っている。ログの等幅もこれ1本にする——桁をそろえて
# 出しているのはログの時刻だけで、Yu Gothic UI は数字がすべて同じ幅なので揃う
UI_CANDIDATES = ("Yu Gothic UI", "Yu Gothic")

UI = "TkDefaultFont"


def pick_font(candidates, families, fallback: str) -> str:
    """candidates のうち families に在る最初のもの。無ければ fallback"""
    available = set(families or ())
    for name in candidates:
        if name in available:
            return name
    return fallback


def named_family(name: str) -> str:
    """Tk の名前付きフォント（TkDefaultFont など）が実際に使う family"""
    try:
        return tkfont.nametofont(name).actual("family")
    except Exception:
        return name


def resolve(root=None) -> str:
    """root ができた後に呼ぶ。選んだ family を返し、UI に入れる"""
    global UI
    try:
        families = tkfont.families(root)
    except Exception:
        families = ()
    UI = pick_font(UI_CANDIDATES, families, named_family("TkDefaultFont"))
    return UI


def describe(root) -> str:
    """選んだフォントと画面の状態。別の PC で字が出ないときの切り分け用"""
    try:
        dpi = root.winfo_fpixels("1i")
        size = f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}"
    except Exception:
        return f"フォント: {UI}"
    # Windows の拡大率は 96dpi が100%（150% なら 144dpi）
    return (f"フォント: {UI} / 画面: {size} / "
            f"拡大率: {dpi / 96:.0%} ({dpi:.0f} dpi)")
