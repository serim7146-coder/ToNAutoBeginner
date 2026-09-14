"""登録された exe を起動する。

ToN ListTool / SaveManager / Overlay のような、周回のたびに手で立ち上げて
いるツールを1クリックで起動するためのもの。どの exe を並べるかは利用者が
決めるので、既定のパスは持たない。

GUI から切り離して単体テストできるようにしてある（`VRChatLauncher` と同じ
考え方）。起動した子プロセスは追いかけない——終了も再起動も監視もしない。
"""

import subprocess
from pathlib import Path

import ProcessCheck


def _clean(exe_path) -> str:
    """設定欄から来た値を扱える文字列にする。扱えなければ空文字"""
    if not isinstance(exe_path, str):
        return ""
    return exe_path.strip()


def button_label(exe_path: str) -> str:
    """ボタンに出す名前。exe のファイル名から拡張子を落としたもの。

    パスが空・取り出せない形なら空文字を返す（呼び出し側が既定の文字を出す）。
    """
    text = _clean(exe_path)
    if not text:
        return ""
    try:
        return Path(text).stem
    except Exception:
        return ""


def is_running(exe_path: str) -> bool:
    """その exe が既に動いているか。

    比較はファイル名だけ（`ProcessCheck.is_process_running` と同じ粒度）。
    同名の exe が別フォルダにあると区別できないが、ここではそれで足りる。
    """
    text = _clean(exe_path)
    if not text:
        return False
    try:
        name = Path(text).name
    except Exception:
        return False
    if not name:
        return False
    return ProcessCheck.is_process_running(name)


def launch(exe_path: str) -> None:
    """起動する。失敗したら例外を投げる（呼び出し側がログにする）。

    cwd は exe のあるフォルダ。GUIアプリなので CREATE_NO_WINDOW は付けない。
    待たない——GUIを止めないため。
    """
    text = _clean(exe_path)
    if not text:
        raise ValueError("起動するexeが指定されていません")
    path = Path(text)
    if not path.exists():
        raise FileNotFoundError(f"exeが見つかりません: {path}")
    subprocess.Popen([str(path)], cwd=str(path.parent))
