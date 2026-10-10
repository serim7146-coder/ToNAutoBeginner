"""
Build: リポジトリの直下で `python build.py`（exe の後にインストーラーも作る。Inno Setup 7 か 6 があれば）。
中身は下のコマンドと同じ（並びも同じ）＋展開先 --onefile-tempdir-spec="{CACHE_DIR}/ToNAutoBeginner/<APP_VERSION>-<ビルドの印>"
と --product-version/--file-version（APP_VERSION の数字）。版は build.py が src/config.py から読む。
参考（今までのコマンド。引数を変えるときは build.py の BASE_ARGS と一緒に直す）:
python -m nuitka src/main.py --onefile --windows-console-mode=disable --output-filename=ToNAutoBeginner.exe --include-module=win32gui --include-module=win32con --include-module=win32api --include-module=pydirectinput --include-module=keyboard --include-module=win32ui --include-module=win32process --include-module=win32crypt --include-package=cv2 --include-data-files=.env=.env --include-data-files=ToNAutoBeginnerIcon.ico=ToNAutoBeginnerIcon.ico --include-data-files=maps.json=maps.json --include-data-files=terrors.json=terrors.json --include-data-files=item.json=item.json --include-data-dir=voice=voice --include-data-dir=begin_templates=begin_templates --include-data-dir=shop_templates=shop_templates --include-data-dir=src/respawn_templates=respawn_templates --enable-plugin=tk-inter --lto=yes --clang --follow-imports --windows-icon-from-ico=ToNAutoBeginnerIcon.ico
"""
import ctypes
import sys

if sys.platform == "win32":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "ToNAutoBeginner.ToNAutoBeginner"
    )

import config
import mainGUI

# 起動中の目印のハンドル。閉じずに持っておく（プロセスが終われば Windows が消す）
_running_mutex = None


def hold_running_mutex():
    """インストーラー・アンインストーラーに起動中だと分かるようにする（Inno Setup の AppMutex）。
    二重起動を止めるものではない。失敗しても起動は続ける"""
    global _running_mutex
    if sys.platform != "win32" or _running_mutex:
        return
    try:
        _running_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, config.APP_MUTEX_NAME)
    except Exception:
        _running_mutex = None


def main():
    hold_running_mutex()
    app = mainGUI.App()
    app.mainloop()


if __name__ == "__main__":
    main()
