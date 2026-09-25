"""画面のフォントを、その PC に在るものから選ぶ。

`"Segoe UI"` を名指しすると、配布した exe を別の PC で動かしたときに漢字が
出ないことがある（Segoe UI は日本語の字を持たず、Tk では Windows の代替
フォントが効かない環境がある）。日本語を持つフォントを在るものから選ぶ。

大きさと bold はそれぞれの呼び出し側のまま。ここでは family だけ決める。
参照は `UIFont.UI` / `UIFont.MONO` のように属性で行う（`resolve()` の後に
書き換わるため、`from UIFont import UI` では古い値を掴む）。
"""
from tkinter import font as tkfont

# 画面の文字。前から順に、在るものを使う
UI_CANDIDATES = ("Yu Gothic UI", "Meiryo UI", "MS UI Gothic")
# ログ・オーバーレイの等幅。Consolas は日本語を持たないので使わない。
# ＭＳ ゴシックは等幅で日本語も持つ。無ければ画面の文字と同じものに落とす
MONO_CANDIDATES = ("ＭＳ ゴシック", "MS Gothic") + UI_CANDIDATES

UI = "TkDefaultFont"
MONO = "TkFixedFont"


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


def resolve(root=None) -> tuple[str, str]:
    """root ができた後に呼ぶ。選んだ (画面, 等幅) を返し、UI / MONO に入れる"""
    global UI, MONO
    try:
        families = tkfont.families(root)
    except Exception:
        families = ()
    UI = pick_font(UI_CANDIDATES, families, named_family("TkDefaultFont"))
    MONO = pick_font(MONO_CANDIDATES, families, named_family("TkFixedFont"))
    return UI, MONO


def describe(root) -> str:
    """選んだフォントと画面の状態。別の PC で字が出ないときの切り分け用"""
    try:
        dpi = root.winfo_fpixels("1i")
        size = f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}"
    except Exception:
        return f"フォント: {UI} / 等幅: {MONO}"
    # Windows の拡大率は 96dpi が100%（150% なら 144dpi）
    return (f"フォント: {UI} / 等幅: {MONO} / 画面: {size} / "
            f"拡大率: {dpi / 96:.0%} ({dpi:.0f} dpi)")
