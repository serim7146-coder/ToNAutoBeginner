"""exe 単体で使っている人を、インストーラー版へ移す（依頼者: 全員を乗り換えてもらう）。

流れ（mainGUI._start_migration / _finish_migration / _cleanup_migrated_exe）:
  1. exe 単体（インストーラーで入れた場所の外）で動いていたら、最新リリースの
     ToNAutoBeginner-Setup.exe を落とす
  2. 古い exe の場所を settings.json に書き、Setup を画面なしで動かす係（PowerShell）を
     裏で起こして、ツールは終わる。係はツールの終了（起動中の目印が消えるの）を待ってから
     Setup を動かす。Setup は終わるとインストールしたツールを起動する（/launch=1）
     入れる場所は、古い exe があったフォルダ（依頼者。/DIR で渡す）。まだインストールして
     いない人だけ。Program Files などの管理者権限が要る場所・書き込めない場所・ドライブの
     直下なら渡さず、Setup の既定（%LOCALAPPDATA% の Programs）に入れる（target_dir）
  3. Setup が成功したら、係が古い exe（と .old）を消す（依頼者）。同じフォルダに同じ名前で
     入れたときは Setup が上書きしたので、exe は消さず .old だけ消す。消せなかったときは、
     インストールしたツールが最初の起動で消す（settings.json の印を見る）
失敗したら（落とせない・リリースに Setup が無い・Setup が途中で止まる）、その回は今のまま
動き、次の起動でやり直す。設定・統計は %APPDATA% にあるので、どちらの exe でも同じものを使う。
"""
import base64
import os
import subprocess
import tempfile
from pathlib import Path

import config

# Inno Setup が書くアンインストール情報（installer/ToNAutoBeginner.iss の AppId + "_is1"）
UNINSTALL_KEY = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                 r"\{F3825561-B8B1-4328-8C50-51C1EFCF0BF5}_is1")
SETTINGS_KEY = "migrated_from"      # settings.json に書く、移す前の exe の場所
MUTEX_WAIT_SEC = 60                 # ツールが終わるのを待つ上限（過ぎたら Setup を動かしてみる）
SETUP_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/launch=1")
CREATE_NO_WINDOW = 0x08000000


def installed_dir() -> Path | None:
    """インストーラーで入れた場所。入れていなければ（読めなければ）None"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            location = winreg.QueryValueEx(key, "InstallLocation")[0]
    except Exception:
        return None
    location = str(location or "").strip().rstrip("\\/")
    return Path(location) if location else None


def _same_dir(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def needs_migration(exe: Path | None, installed: Path | None) -> bool:
    """移すか。exe で動いていて（開発実行は None）、インストールした場所の外なら移す"""
    if exe is None:
        return False
    return installed is None or not _same_dir(exe.parent, installed)


# 管理者権限が要るので入れない場所（installer/ToNAutoBeginner.iss の NextButtonClick と同じ）
PROTECTED_DIR_ENVS = ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "SystemRoot")


def _is_under(path: Path, parent: str) -> bool:
    if not parent:
        return False
    a = os.path.normcase(os.path.abspath(path)).rstrip("\\/") + os.sep
    b = os.path.normcase(os.path.abspath(parent)).rstrip("\\/") + os.sep
    return a.startswith(b)


def _writable(folder: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=folder):
            pass
        return True
    except Exception:
        return False


def target_dir(old_exe: Path, installed: Path | None) -> Path | None:
    """Setup に渡すインストール先（古い exe のフォルダ）。渡さないときは None。
    もうインストールしてある人は、その場所のまま（上書き）"""
    if installed is not None:
        return None
    folder = Path(os.path.abspath(old_exe.parent))
    if folder == Path(folder.anchor):
        return None                             # ドライブの直下
    if any(_is_under(folder, os.environ.get(env, "")) for env in PROTECTED_DIR_ENVS):
        return None
    return folder if _writable(folder) else None


def _overwritten_by_setup(old_exe: Path, install_dir: Path | None) -> bool:
    """Setup が古い exe をそのまま上書きするか（同じフォルダに同じ名前）"""
    return (install_dir is not None and _same_dir(old_exe.parent, install_dir)
            and os.path.normcase(old_exe.name) == os.path.normcase(config.UPDATE_ASSET_NAME))


def _quote(text) -> str:
    """PowerShell の '…'（中の ' は2つ重ねる）"""
    return "'" + str(text).replace("'", "''") + "'"


def setup_script(setup: Path, old_exe: Path | None = None, install_dir: Path | None = None,
                 mutex: str = config.APP_MUTEX_NAME, wait_sec: int = MUTEX_WAIT_SEC) -> str:
    """ツールの終了を待ってから Setup を画面なしで動かし、Setup を消す PowerShell。
    install_dir を渡すとそこへ入れる（/DIR）。Setup が成功したら（終了コード 0）古い exe と
    .old も消す（Setup が上書きした exe は消さない）。exe はツールの終わり際まで開いている
    ことがあるので、少し間を置いて何度か試す"""
    setup_args = list(SETUP_ARGS)
    if install_dir is not None:
        # Start-Process は空白を含む引数を "" で囲まないので、自分で囲む
        setup_args.append(f'/DIR="{install_dir}"')
    args = ",".join(_quote(a) for a in setup_args)
    lines = [
        f"$deadline = (Get-Date).AddSeconds({int(wait_sec)})",
        "while ((Get-Date) -lt $deadline) {",
        "  $m = $null",
        f"  if (-not [System.Threading.Mutex]::TryOpenExisting({_quote(mutex)}, [ref]$m)) {{ break }}",
        "  $m.Dispose()",
        "  Start-Sleep -Milliseconds 300",
        "}",
        f"$p = Start-Process -FilePath {_quote(setup)} -ArgumentList {args} -Wait -PassThru",
        f"Remove-Item -LiteralPath {_quote(setup)} -Force -ErrorAction SilentlyContinue",
    ]
    if old_exe is not None:
        old = [_quote(old_exe.with_name(old_exe.name + ".old"))]
        if not _overwritten_by_setup(old_exe, install_dir):
            old.insert(0, _quote(old_exe))
        lines += [
            "if ($p.ExitCode -eq 0) {",
            "  for ($i = 0; $i -lt 20; $i++) {",
            f"    Remove-Item -LiteralPath {', '.join(old)} -Force -ErrorAction SilentlyContinue",
            f"    if (-not (Test-Path -LiteralPath {old[0]})) {{ break }}",
            "    Start-Sleep -Milliseconds 500",
            "  }",
            "}",
        ]
    return "\n".join(lines)


def powershell_command(script: str) -> list[str]:
    """パスに日本語や記号があっても崩れないよう、-EncodedCommand（UTF-16LE の base64）で渡す"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-EncodedCommand", encoded]


def launch_setup(setup: Path, old_exe: Path | None = None, install_dir: Path | None = None) -> bool:
    """Setup を動かす係を裏で起こす。起こせたら True（呼び出し側はすぐツールを終える）"""
    try:
        subprocess.Popen(powershell_command(setup_script(setup, old_exe, install_dir)),
                         creationflags=CREATE_NO_WINDOW,
                         close_fds=True)
        return True
    except Exception:
        return False


def remove_old_exe(old: Path, current: Path | None) -> bool:
    """移す前の exe（と自動更新の残りの .old）を消す。今動いている exe は消さない。
    もう無い・消せたら True（settings.json の印を外してよい）"""
    if current is not None and _same_dir(old, current):
        return True
    for path in (old, old.with_name(old.name + ".old")):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass                # 開いている・権限が無い。.old は残っても印は外す
    return not old.exists()
