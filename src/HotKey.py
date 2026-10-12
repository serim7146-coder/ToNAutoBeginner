"""緊急停止キーの検証・表記・捕捉。

`keyboard.is_pressed()` は不正なキー名で `ValueError` を投げる。緊急停止の
ポーリングはそれを握り潰すので、**不正なキーが設定されると緊急停止が黙って
死ぬ**——200msごとに例外が出るだけで、押しても何も起きず、ログにも残らない。
効かないことに緊急時まで気づけない。

なので、設定するときも設定ファイルから読むときも、ここを通して検証する。

GUI から切り離して単体テストできるようにしてある。
"""

import queue

import config
import DebugLog

try:
    import keyboard
except ImportError:
    keyboard = None


def is_valid(name) -> bool:
    """`keyboard` が受け付けるキー名か。

    文字列以外・空文字・不正な名前はすべて False。`keyboard` が無い環境でも
    False を返す（呼び出し側が既定値へ倒す）。
    """
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
    """
    if not isinstance(name, str) or not name.strip():
        name = config.EMERGENCY_STOP_KEY
    return "+".join(part.strip().title() for part in name.split("+") if part.strip())


def capture(timeout_sec: float = 5.0):
    """押されたキーを1つ返す（`keyboard.read_hotkey` と同じ形。離した瞬間に、押されている組み合わせの名前）。
    押されなければ（または失敗すれば）None。

    `suppress=True` にしないこと。設定のために押したキーが VRChat や他の
    アプリに届かなくなる。

    `keyboard.read_hotkey()` はキーが押されるまで戻らず、時間切れで置き去りにしたスレッドが
    次の打鍵まで残っていた。ここは自分でフックを掛け、時間切れなら外す（残らない）。
    組み合わせの名前は `read_hotkey` と同じやり方（離したキー＋そのとき押されているキー）
    """
    if keyboard is None:
        return None
    found = queue.Queue()
    up = getattr(keyboard, "KEY_UP", "up")

    def on_event(event):
        if getattr(event, "event_type", None) != up:
            return
        try:
            pressed = getattr(keyboard, "_pressed_events", {})
            names = [e.name for e in list(pressed.values())] + [event.name]
            found.put(keyboard.get_hotkey_name(names))
        except Exception:
            DebugLog.exception("HotKey.capture.on_event")
            found.put(None)

    try:
        hooked = keyboard.hook(on_event, suppress=False)
    except Exception:
        DebugLog.exception("HotKey.capture")
        return None
    try:
        return found.get(timeout=max(0.0, timeout_sec))
    except queue.Empty:
        return None
    finally:
        try:
            keyboard.unhook(hooked)
        except Exception:
            DebugLog.exception("HotKey.capture.unhook")
