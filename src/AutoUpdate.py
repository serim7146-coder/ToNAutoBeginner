"""
GitHub Releases を使った自動アップデート

流れ:
  1. 起動時に releases/latest を取得し、APP_VERSION とタグを比較
  2. 新しければ EXE アセットを一時ファイルへダウンロード
  3. 実行中の EXE を「.old」へリネーム（実行中でもリネームは可能）し、
     新 EXE を元のパスへ移動 → 新 EXE を起動して自分は終了
  4. 次回起動時に cleanup_old_exe() が .old を削除

開発実行時（未コンパイル）は current_exe_path() が None を返し、全体が無効化される。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import config


_API_URL = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
_FETCH_TIMEOUT_SEC = 5.0
_DOWNLOAD_TIMEOUT_SEC = 60.0
_USER_AGENT = f"ToNAutoBeginner/{config.APP_VERSION}"


def parse_version(tag: str) -> tuple[int, ...]:
    """'v0.3.0' / '0.3.0' → (0, 3, 0)。解釈できない場合は空タプル。"""
    tag = (tag or "").strip().lstrip("vV")
    parts: list[int] = []
    for p in tag.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts)


def is_newer(remote_tag: str, current: str) -> bool:
    remote = parse_version(remote_tag)
    return bool(remote) and remote > parse_version(current)


def fetch_latest_release() -> dict | None:
    """最新リリース情報を取得する。失敗時はNone（オフラインでも起動を妨げない）"""
    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception:
        return None


def find_exe_asset(release: dict) -> tuple[str, int] | None:
    """リリースアセットから配布EXEの (URL, サイズ) を返す。無ければNone。"""
    for asset in release.get("assets", []):
        if asset.get("name") == config.UPDATE_ASSET_NAME and asset.get("browser_download_url"):
            return asset["browser_download_url"], int(asset.get("size", 0))
    return None


def current_exe_path() -> Path | None:
    """コンパイル済みEXEとして動作している場合のみ自分のEXEパスを返す"""
    if globals().get("__compiled__") is None:
        return None
    try:
        p = Path(sys.argv[0]).resolve()
    except Exception:
        return None
    if p.suffix.lower() == ".exe" and p.exists():
        return p
    return None


def download_to_temp(url: str, expected_size: int) -> Path | None:
    """アセットを一時ファイルへダウンロードする。サイズ不一致・失敗はNone。"""
    tmp = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        fd, tmp_name = tempfile.mkstemp(suffix=".exe.download")
        tmp = Path(tmp_name)
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SEC) as res, open(fd, "wb") as f:
            shutil.copyfileobj(res, f)
        if expected_size and tmp.stat().st_size != expected_size:
            tmp.unlink(missing_ok=True)
            return None
        return tmp
    except Exception:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        return None


def apply_update(new_file: Path, exe_path: Path) -> bool:
    """実行中EXEを .old へ退避し、新EXEを本来のパスへ移動する"""
    old_path = exe_path.with_name(exe_path.name + ".old")
    try:
        if old_path.exists():
            old_path.unlink()
        exe_path.rename(old_path)
    except Exception:
        return False
    try:
        shutil.move(str(new_file), str(exe_path))
        return True
    except Exception:
        # 置き換え失敗時は退避したEXEを元へ戻す
        try:
            old_path.rename(exe_path)
        except Exception:
            pass
        return False


def cleanup_old_exe():
    """前回アップデートの残骸（.old）を削除する。起動時に呼ぶ。"""
    exe = current_exe_path()
    if exe is None:
        return
    old = exe.with_name(exe.name + ".old")
    try:
        if old.exists():
            old.unlink()
    except Exception:
        pass  # 旧EXEがまだ終了しきっていない場合など。次回起動時に再試行される


def restart_to_new_exe(exe_path: Path):
    """新しいEXEを起動する（呼び出し側でアプリを終了すること）"""
    subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
