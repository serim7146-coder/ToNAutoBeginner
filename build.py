"""ToNAutoBeginner.exe をビルドする（Nuitka の onefile）。

    python build.py

src/config.py の APP_VERSION を読んで、展開先（--onefile-tempdir-spec）と exe の版を
その版にする。展開先は「版-ビルドの印」（印はビルドした時刻。ビルドのたびに違う）に
固定するので、2回目以降の起動は展開を省いて速くなり（Nuitka の cached モード）、
同じ版でビルドし直しても古い中身を使わない。版は手で書かない。
引数は src/main.py の頭のビルドコマンドと同じ（並びも同じ）で、足すのは展開先と版だけ。

exe ができたら、続けてインストーラー（dist/ToNAutoBeginner-Setup.exe）を作る。
Inno Setup 6 の ISCC.exe が要る。見つからなければ exe だけ作って終わる（失敗にはしない）。
場所が決まった所に無いときは、環境変数 ISCC に ISCC.exe のパスを入れる。
"""
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "src" / "config.py"
INSTALLER_SCRIPT = ROOT / "installer" / "ToNAutoBeginner.iss"

# src/main.py の頭のコマンドと同じ引数（1つも落とさない・変えない）
BASE_ARGS = [
    "--onefile",
    "--windows-console-mode=disable",
    "--output-filename=ToNAutoBeginner.exe",
    "--include-module=win32gui",
    "--include-module=win32con",
    "--include-module=win32api",
    "--include-module=pydirectinput",
    "--include-module=keyboard",
    "--include-module=win32ui",
    "--include-module=win32process",
    "--include-module=win32crypt",
    "--include-package=cv2",
    "--include-data-files=.env=.env",
    "--include-data-files=ToNAutoBeginnerIcon.ico=ToNAutoBeginnerIcon.ico",
    "--include-data-files=maps.json=maps.json",
    "--include-data-files=terrors.json=terrors.json",
    "--include-data-files=item.json=item.json",
    "--include-data-dir=voice=voice",
    "--include-data-dir=begin_templates=begin_templates",
    "--include-data-dir=shop_templates=shop_templates",
    "--enable-plugin=tk-inter",
    "--lto=yes",
    "--clang",
    "--follow-imports",
    "--windows-icon-from-ico=ToNAutoBeginnerIcon.ico",
]


def read_app_version(path=CONFIG) -> str:
    """config.py の APP_VERSION（例 "v1.0.0"）。import はしない（読み込み時の副作用を避ける）"""
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit(f"APP_VERSION が見つかりません: {path}")
    return m.group(1)


def version_numbers(version: str) -> str:
    """"v1.0.0" → "1.0.0"（exe の版。数字は4つまで）"""
    numbers = version.lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+){0,3}", numbers):
        raise SystemExit(f"APP_VERSION の形が版の数字ではありません: {version!r}")
    return numbers


def build_stamp(now: datetime | None = None) -> str:
    """ビルドの印（ビルドした時刻。マイクロ秒まで。ビルドのたびに違う）"""
    return (now or datetime.now()).strftime("%Y%m%d%H%M%S%f")


def build_command(version: str, stamp: str | None = None) -> list[str]:
    numbers = version_numbers(version)
    stamp = stamp or build_stamp()
    return [sys.executable, "-m", "nuitka", "src/main.py", *BASE_ARGS,
            f"--onefile-tempdir-spec={{CACHE_DIR}}/ToNAutoBeginner/{version}-{stamp}",
            f"--product-version={numbers}",
            f"--file-version={numbers}"]


def find_iscc(environ=None) -> Path | None:
    """Inno Setup のコンパイラ（ISCC.exe）。環境変数 ISCC → PATH → 既定の入れ場所の順"""
    environ = os.environ if environ is None else environ
    if environ.get("ISCC"):
        return Path(environ["ISCC"])
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    candidates = [
        Path(environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    if environ.get("LOCALAPPDATA"):
        candidates.append(Path(environ["LOCALAPPDATA"]) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    return next((p for p in candidates if p.is_file()), None)


def installer_command(iscc: Path, version: str) -> list[str]:
    return [str(iscc), f"/DAppVersion={version_numbers(version)}", str(INSTALLER_SCRIPT)]


def main() -> int:
    version = read_app_version()
    command = build_command(version)
    print(" ".join(command))
    code = subprocess.run(command, cwd=str(ROOT)).returncode
    if code != 0:
        return code
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup 6（ISCC.exe）が見つからないので、インストーラーは作りません。"
              "exe だけできています。作るなら Inno Setup 6 を入れるか、環境変数 ISCC に"
              " ISCC.exe のパスを入れてからもう一度実行してください")
        return 0
    command = installer_command(iscc, version)
    print(" ".join(command))
    return subprocess.run(command, cwd=str(ROOT)).returncode


if __name__ == "__main__":
    sys.exit(main())
