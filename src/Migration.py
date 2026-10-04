"""exe 単体で使っている人を、インストーラー版へ移す（依頼者: 全員を乗り換えてもらう）。

流れ（mainGUI._start_migration / _finish_migration / _cleanup_migrated_exe）:
  1. exe 単体（インストーラーで入れた場所の外）で動いていたら、最新リリースの
     ToNAutoBeginner-Setup.exe を落とす
  2. 古い exe の場所を settings.json に書き、Setup を画面なしで動かす係（PowerShell）を
     裏で起こして、ツールは終わる。係はツールの終了（起動中の目印が消えるの）を待ってから
     Setup を動かす。Setup は終わるとインストールしたツールを起動する（/launch=1）
  3. Setup が成功したら、係が古い exe（と .old）を消す（依頼者）。消せなかったときは、
     インストールしたツールが最初の起動で消す（settings.json の印を見る）
失敗したら（落とせない・リリースに Setup が無い・Setup が途中で止まる）、その回は今のまま
動き、次の起動でやり直す。設定・統計は %APPDATA% にあるので、どちらの exe でも同じものを使う。
"""
import base64
import os
import subprocess
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


def _quote(text) -> str:
    """PowerShell の '…'（中の ' は2つ重ねる）"""
    return "'" + str(text).replace("'", "''") + "'"


def setup_script(setup: Path, old_exe: Path | None = None, mutex: str = config.APP_MUTEX_NAME,
                 wait_sec: int = MUTEX_WAIT_SEC) -> str:
    """ツールの終了を待ってから Setup を画面なしで動かし、Setup を消す PowerShell。
    Setup が成功したら（終了コード 0）古い exe と .old も消す。exe はツールの終わり際まで
    開いていることがあるので、少し間を置いて何度か試す"""
    args = ",".join(_quote(a) for a in SETUP_ARGS)
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
        old = [_quote(old_exe), _quote(old_exe.with_name(old_exe.name + ".old"))]
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


def launch_setup(setup: Path, old_exe: Path | None = None) -> bool:
    """Setup を動かす係を裏で起こす。起こせたら True（呼び出し側はすぐツールを終える）"""
    try:
        subprocess.Popen(powershell_command(setup_script(setup, old_exe)),
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
