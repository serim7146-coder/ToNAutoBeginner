"""
コンパイル
python -m nuitka src/main.py --onefile --windows-console-mode=disable --output-filename=ToNAutoBeginner.exe --include-module=win32gui --include-module=win32con --include-module=win32api --include-module=pydirectinput --include-module=keyboard --include-data-files=.env=.env --include-data-files=ToNAutoBeginnerIcon.ico=ToNAutoBeginnerIcon.ico --include-data-files=maps.json=maps.json --include-data-files=terrors.json=terrors.json --include-data-dir=voice=voice --enable-plugin=tk-inter --lto=yes --clang --follow-imports --windows-icon-from-ico=ToNAutoBeginnerIcon.ico
"""
import sys

if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "ToNAutoBeginner.ToNAutoBeginner"
    )

import keyboard

import GUI

# ═══════════════════════════════════════════════
if __name__ == "__main__":
    app = GUI.App()

    # 緊急停止キー: P（ポーリング方式で長時間動作を保証）
    # リストで囲むことでネスト関数内から書き換え可能にする
    _p_state = [False]

    def _poll_p_key():
        try:
            now = keyboard.is_pressed("p")
            if now and not _p_state[0]:
                print("[緊急停止] P キーが押されました")
                app.after(0, app._stop)
            _p_state[0] = now
        except Exception:
            pass
        app.after(200, _poll_p_key)   # 200msごとにチェック

    app.after(200, _poll_p_key)
    app.mainloop()
