"""
Build: リポジトリの直下で `python build.py`。
中身は下のコマンドと同じ（並びも同じ）＋展開先 --onefile-tempdir-spec="{CACHE_DIR}/ToNAutoBeginner/<APP_VERSION>-<ビルドの印>"
と --product-version/--file-version（APP_VERSION の数字）。版は build.py が src/config.py から読む。
参考（今までのコマンド。引数を変えるときは build.py の BASE_ARGS と一緒に直す）:
python -m nuitka src/main.py --onefile --windows-console-mode=disable --output-filename=ToNAutoBeginner.exe --include-module=win32gui --include-module=win32con --include-module=win32api --include-module=pydirectinput --include-module=keyboard --include-module=win32ui --include-module=win32process --include-module=win32crypt --include-package=cv2 --include-data-files=.env=.env --include-data-files=ToNAutoBeginnerIcon.ico=ToNAutoBeginnerIcon.ico --include-data-files=maps.json=maps.json --include-data-files=terrors.json=terrors.json --include-data-dir=voice=voice --include-data-dir=begin_templates=begin_templates --include-data-dir=shop_templates=shop_templates --enable-plugin=tk-inter --lto=yes --clang --follow-imports --windows-icon-from-ico=ToNAutoBeginnerIcon.ico
"""
import sys

if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "ToNAutoBeginner.ToNAutoBeginner"
    )

import mainGUI


def main():
    app = mainGUI.App()
    app.mainloop()


if __name__ == "__main__":
    main()
