"""
Build:
python -m nuitka src/main.py --onefile --windows-console-mode=disable --output-filename=ToNAutoBeginner.exe --include-module=win32gui --include-module=win32con --include-module=win32api --include-module=pydirectinput --include-module=keyboard --include-data-files=.env=.env --include-data-files=ToNAutoBeginnerIcon.ico=ToNAutoBeginnerIcon.ico --include-data-files=maps.json=maps.json --include-data-files=terrors.json=terrors.json --include-data-dir=voice=voice --enable-plugin=tk-inter --lto=yes --clang --follow-imports --windows-icon-from-ico=ToNAutoBeginnerIcon.ico
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
