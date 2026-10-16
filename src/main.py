"""
Build: リポジトリの直下で `python build.py`（exe の後にインストーラーも作る。Inno Setup 7 か 6 があれば）。
中身は下のコマンドと同じ（並びも同じ）＋展開先 --onefile-tempdir-spec="{CACHE_DIR}/ToNAutoBeginner/<APP_VERSION>-<ビルドの印>"
と --product-version/--file-version（APP_VERSION の数字）。版は build.py が src/config.py から読む。
参考（今までのコマンド。引数を変えるときは build.py の BASE_ARGS と一緒に直す）:
python -m nuitka src/main.py --onefile --windows-console-mode=disable --output-filename=ToNAutoBeginner.exe --include-module=win32gui --include-module=win32con --include-module=win32api --include-module=pydirectinput --include-module=keyboard --include-module=win32ui --include-module=win32process --include-module=win32crypt --include-module=win32clipboard --include-package=cv2 --include-package=zeroconf --include-package=ifaddr --recompile-extension-modules=zeroconf --recompile-extension-modules=zeroconf.* --include-data-files=.env=.env --include-data-files=assets/ToNAutoBeginnerIcon.ico=assets/ToNAutoBeginnerIcon.ico --include-data-files=assets/data/maps.json=assets/data/maps.json --include-data-files=assets/data/terrors.json=assets/data/terrors.json --include-data-files=assets/data/item.json=assets/data/item.json --include-data-dir=assets/voice=assets/voice --include-data-dir=assets/templates=assets/templates --enable-plugin=tk-inter --lto=yes --clang --follow-imports --windows-icon-from-ico=assets/ToNAutoBeginnerIcon.ico
"""
import ctypes
import os
import sys

# numpy の OpenBLAS は読み込んだ瞬間に全コアぶんの作業バッファを確保する（24 コアの PC で
# コミット 757 MB。1 スレッドなら 17 MB）。このツールの行列計算はホモグラフィ程度で 1 スレッドで
# 足りる。numpy を最初に読む前（どのモジュールより前）に決める
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

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
