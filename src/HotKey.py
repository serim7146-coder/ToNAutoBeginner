"""緊急停止キーの検証・表記・捕捉。

`keyboard.is_pressed()` は不正なキー名で `ValueError` を投げる。緊急停止の
ポーリングはそれを握り潰すので、**不正なキーが設定されると緊急停止が黙って
死ぬ**——200msごとに例外が出るだけで、押しても何も起きず、ログにも残らない。
効かないことに緊急時まで気づけない。

なので、設定するときも設定ファイルから読むときも、ここを通して検証する。

GUI から切り離して単体テストできるようにしてある。
"""

import threading

import config
import DebugLog
import MouseButtons

try:
    import keyboard
except ImportError:
    keyboard = None


def is_valid(name) -> bool:
    """`keyboard` が受け付けるキー名か。

    文字列以外・空文字・不正な名前はすべて False。`keyboard` が無い環境でも
    False を返す（呼び出し側が既定値へ倒す）。マウスのサイドボタン（mouse:x1・mouse:x2。GW）は
    `keyboard` が無い環境でも True。
    """
    if MouseButtons.is_mouse(name):
        return True
    if keyboard is None or not isinstance(name, str) or not name.strip():
        return False
    try:
        keyboard.parse_hotkey(name)
        return True
    except Exception:
        DebugLog.exception("HotKey.is_valid")
        return False


def display(name) -> str:
    """GUI とログに出す表記。`p` → `P`、`ctrl+p` → `Ctrl+P`。

    `.upper()` で済ませると `ctrl+p` が `CTRL+P` になるので、`+` で分けて
    各語の頭だけ大文字にする。不正・空なら既定値の表記を返す。
    マウスのサイドボタンは「マウス4」「マウス5」。
    """
    if MouseButtons.is_mouse(name):
        return MouseButtons.display(name)
    if not isinstance(name, str) or not name.strip():
        name = config.EMERGENCY_STOP_KEY
    return "+".join(part.strip().title() for part in name.split("+") if part.strip())


# 捕捉の前から「押されている」と記録されたまま残るキー。読み込んだ設定から外す（EL: 日本語キーボードの
# 「半角/全角」は離した（KEY_UP）が届かないことがあり、keyboard の記録に残り続けて、どのキーを設定しても
# 「+半角/全角」が付いていた）
STALE_KEYS = ("半角/全角",)


def without_stale(name):
    """設定のキーから STALE_KEYS を外す。(外した後, 外したか)。外して何も残らなければ (None, True)
    （None は呼び出し側の検証で不正 → その設定の既定・未設定の扱いになる）。文字列でなければそのまま"""
    if not isinstance(name, str):
        return name, False
    parts = [part.strip() for part in name.split("+")]
    kept = [part for part in parts if part.lower() not in STALE_KEYS]
    if len(kept) == len(parts):
        return name, False
    kept = [part for part in kept if part]
    return ("+".join(kept) if kept else None), True


def hook_key(name, callback, suppress=False, kb=None):
    """そのキーの押した・離したの購読。マウスのサイドボタンなら MouseButtons、それ以外は keyboard（GW）。
    返した目印は unhook へ渡す。kb は keyboard の代わりに使うモジュール（呼び出し元が持っている物。省略は HotKey の物）"""
    if MouseButtons.is_mouse(name):
        return MouseButtons.hook_key(name, callback, suppress=suppress)
    return (keyboard if kb is None else kb).hook_key(name, callback, suppress=suppress)


def unhook(handle, kb=None):
    """hook_key の購読を外す。MouseButtons の目印なら MouseButtons、それ以外は keyboard
    （keyboard の KeyError はそのまま外へ出す。呼び元が握る）"""
    if isinstance(handle, MouseButtons._Hook):
        MouseButtons.unhook(handle)
        return
    (keyboard if kb is None else kb).unhook(handle)


def is_pressed(name, kb=None) -> bool:
    """そのキー（マウスのサイドボタンも）がいま押されているか"""
    if MouseButtons.is_mouse(name):
        return MouseButtons.is_pressed(name)
    return (keyboard if kb is None else kb).is_pressed(name)


def capture(timeout_sec: float = 5.0):
    """押されたキー（組み合わせ）を1つ返す。押されなければ（または失敗すれば）None。

    `suppress=True` にしないこと。設定のために押したキーが VRChat や他の
    アプリに届かなくなる。

    `keyboard.read_hotkey()` は使わない。あれは最初に離したときに、ライブラリが「押されている」と
    記録しているキーを全部入れるので、捕捉の前から離した記録が届かずに残っているキー（「半角/全角」）が
    毎回付いてきた（EL）。自前のフックで、捕捉を始めた後に押したキーだけを覚え、最初に離したときに
    「捕捉中に押して、まだ離していないキー＋今離したキー」を返す。捕捉の前から押されていたキーの
    離した通知は数えない。名前の並べ方は `keyboard.get_hotkey_name`（read_hotkey と同じ）。
    時間切れなら None。フックはどの終わり方でも外す（待つスレッドも立てないので、時間切れの後に
    何も残らない。前は read_hotkey のスレッドが次の打鍵まで残っていた）。
    マウスのサイドボタン（GW）も同じ捕捉で待つ。押して離したら、そのボタンの名前（mouse:x1）を返す。
    組み合わせは作らない（キーを押したままサイドボタンを離せば、サイドボタンだけ）。先に終わった方が勝つ。
    """
    if keyboard is None:
        return None
    pressed = {}            # スキャンコード（無ければ名前）→ 名前。捕捉中に押して、まだ離していない（押した順）
    result = {}
    done = threading.Event()
    finish_lock = threading.Lock()      # キーとマウスが同時に終わっても、先の 1 つだけを結果にする
    mouse_pressed = set()               # 捕捉中に押して、まだ離していないサイドボタン
    down = getattr(keyboard, "KEY_DOWN", "down")
    up = getattr(keyboard, "KEY_UP", "up")

    def on_event(event):
        if done.is_set():
            return
        try:
            code = getattr(event, "scan_code", None)
            code = code if code is not None else event.name
            if event.event_type == down:
                pressed.setdefault(code, event.name)
            elif event.event_type == up and code in pressed:
                name = keyboard.get_hotkey_name(list(pressed.values()))
                with finish_lock:
                    if not done.is_set():
                        result["key"] = name
                        done.set()
        except Exception:
            DebugLog.exception("HotKey.capture")
            done.set()

    def on_mouse(event):
        if done.is_set():
            return
        try:
            if event.event_type == "down":
                mouse_pressed.add(event.name)
            elif event.event_type == "up" and event.name in mouse_pressed:
                with finish_lock:
                    if not done.is_set():
                        result["key"] = event.name
                        done.set()
        except Exception:
            DebugLog.exception("HotKey.capture.mouse")
            done.set()

    try:
        hook = keyboard.hook(on_event, suppress=False)
    except Exception:
        DebugLog.exception("HotKey.hook")
        return None
    mouse_hook = None
    try:
        mouse_hook = MouseButtons.hook(on_mouse)
        done.wait(max(0.0, timeout_sec))
    finally:
        try:
            keyboard.unhook(hook)
        except Exception:
            DebugLog.exception("HotKey.unhook")
        if mouse_hook is not None:
            MouseButtons.unhook(mouse_hook)
    return result.get("key")
