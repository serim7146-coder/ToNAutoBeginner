"""
VRChatの起動

- VRChat.exeの場所はSteamのライブラリ設定から自動検出する（手動指定も可能）
- 窓ごとに --profile=N を指定して起動する。同じIDなら同じアカウント設定を共有する
- ワールド参加を自動化する場合は vrchat:// のlaunchリンクを引数に渡す
"""
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import config


# VRChat.exe を直接起動すると「オフラインテストモード」になり、
# オンラインのワールドへ移動できない。通常起動には launch.exe を使う。
VRCHAT_INSTALL_RELATIVE = Path("steamapps") / "common" / "VRChat"
LAUNCHER_NAME = "launch.exe"
OFFLINE_EXE_NAME = "VRChat.exe"
RE_LIBRARY_PATH = re.compile(r'"path"\s+"([^"]+)"')
# ログの Joining 行から ワールドID:インスタンスID（region等の修飾込み）を取り出す
RE_JOINING_FULL = re.compile(r"\[Behaviour\] Joining (wrld_[\w-]+:\S+)")
# 各種URL形式からワールドID・インスタンスIDを取り出す
RE_LAUNCH_ID    = re.compile(r"[?&]id=([^&\s]+)")
RE_WEB_WORLD    = re.compile(r"[?&]worldId=([^&\s]+)")
RE_WEB_INSTANCE = re.compile(r"[?&]instanceId=([^&\s]+)")
RE_RAW_ID       = re.compile(r"^(wrld_[\w-]+:\S+)$")
RE_INSTANCE_NUM = re.compile(r"(id=wrld_[\w-]+:)(\d+)")
RE_USER_AUTH    = re.compile(r"User Authenticated: .*?\((usr_[0-9a-fA-F-]+)\)")


# ── VRChat.exeの検出 ──────────────────────────

def _steam_root() -> Optional[Path]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except Exception:
        return None


def steam_library_paths() -> list[Path]:
    """Steamのライブラリフォルダ一覧を返す"""
    roots: list[Path] = []
    root = _steam_root()
    if root is not None:
        roots.append(root)
    for fallback in (
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
    ):
        if fallback not in roots:
            roots.append(fallback)

    libraries: list[Path] = []
    for base in roots:
        if base not in libraries and base.exists():
            libraries.append(base)
        vdf = base / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for raw in RE_LIBRARY_PATH.findall(text):
            path = Path(raw.replace("\\\\", "\\"))
            if path not in libraries:
                libraries.append(path)
    return libraries


def prefer_launcher(exe: Path) -> Path:
    """VRChat.exe が指定された場合、同じフォルダの launch.exe を優先する。
    VRChat.exe 直接起動はオフラインテストモードになるため。"""
    exe = Path(exe)
    if exe.name.lower() == OFFLINE_EXE_NAME.lower():
        launcher = exe.with_name(LAUNCHER_NAME)
        if launcher.exists():
            return launcher
    return exe


def find_vrchat_exe() -> Optional[Path]:
    """VRChatの起動exe(launch.exe)を自動検出する。見つからなければNone。"""
    for library in steam_library_paths():
        install = library / VRCHAT_INSTALL_RELATIVE
        launcher = install / LAUNCHER_NAME
        if launcher.exists():
            return launcher
        fallback = install / OFFLINE_EXE_NAME
        if fallback.exists():
            return fallback
    return None


def resolve_vrchat_exe(manual_path: str = "") -> Optional[Path]:
    """手動指定を優先し、無ければ自動検出する。
    VRChat.exeが指定された場合は launch.exe へ読み替える。"""
    manual = (manual_path or "").strip()
    if manual:
        path = Path(manual)
        return prefer_launcher(path) if path.exists() else None
    return find_vrchat_exe()


# ── 参加リンク ────────────────────────────────

def normalize_instance_link(text: str) -> Optional[str]:
    """入力されたワールド情報を vrchat:// のlaunchリンクへ正規化する。
    受け付ける形式:
      - vrchat://launch?ref=vrchat.com&id=wrld_xxx:12345~region(jp)
      - https://vrchat.com/home/launch?worldId=wrld_xxx&instanceId=12345~region(jp)
      - wrld_xxx:12345~region(jp)
    """
    text = (text or "").strip()
    if not text:
        return None

    raw = RE_RAW_ID.match(text)
    if raw:
        return f"vrchat://launch?ref=vrchat.com&id={raw.group(1)}"

    world = RE_WEB_WORLD.search(text)
    instance = RE_WEB_INSTANCE.search(text)
    if world and instance:
        return f"vrchat://launch?ref=vrchat.com&id={world.group(1)}:{instance.group(1)}"

    launch_id = RE_LAUNCH_ID.search(text)
    if launch_id and launch_id.group(1).startswith("wrld_"):
        return f"vrchat://launch?ref=vrchat.com&id={launch_id.group(1)}"
    return None


def new_instance_number(index: int) -> int:
    """窓ごとに重複しないインスタンス番号を作る。

    同じ private インスタンスにはオーナー以外入れないため、窓ごとに
    別のインスタンスへ入れる必要がある。
    """
    base = int(time.time()) % 90000 + 10000
    return (base + index * 137) % 90000 + 10000


def with_unique_instance(link: str, index: int) -> str:
    """参加リンクのインスタンス番号だけを窓ごとに差し替える"""
    if not link:
        return link
    return RE_INSTANCE_NUM.sub(
        lambda m: m.group(1) + str(new_instance_number(index)), link, count=1)


def latest_user_id(log_dir) -> Optional[str]:
    """直近のログから自分のユーザーIDを取り出す（インスタンス生成に使う）"""
    try:
        import VRChatDiscovery
        logs = VRChatDiscovery.find_latest_logs(Path(log_dir), 3)
        for path in reversed(logs):
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            m = RE_USER_AUTH.search(text)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def build_ton_link(owner_user_id: str, index: int = 0,
                   region: str = None) -> Optional[str]:
    """ToNの private インスタンスへの参加リンクを組み立てる"""
    if not owner_user_id:
        return None
    region = region or config.TON_DEFAULT_REGION
    number = new_instance_number(index)
    return ("vrchat://launch?ref=vrchat.com&id=%s:%d~private(%s)~region(%s)"
            % (config.TON_WORLD_ID, number, owner_user_id, region))


def instance_link_from_log(log_path) -> Optional[str]:
    """ログの末尾から最新のJoining行を探し、参加リンクを組み立てる"""
    try:
        path = Path(log_path)
        if not path.exists():
            return None
        import LogMonitor
        lines = LogMonitor.LogMonitor._iter_log_lines_reversed(
            path, config.LOG_START_SCAN_CHUNK_BYTES)
        for line in lines:
            m = RE_JOINING_FULL.search(line)
            if m:
                return f"vrchat://launch?ref=vrchat.com&id={m.group(1)}"
    except Exception:
        return None
    return None


# ── 起動 ──────────────────────────────────────

def build_launch_args(
    exe: Path,
    profile_id: int,
    desktop_mode: bool = True,
    instance_link: Optional[str] = None,
    osc_index: Optional[int] = None,
) -> list[str]:
    """起動引数を組み立てる。

    osc_index を渡すと窓ごとに別のOSCポートを割り当てる。VRChatは既定で
    UDP 9000 を掴むため、多重起動では指定しないと2窓目以降が受信できない。
    """
    args = [str(exe), f"--profile={int(profile_id)}"]
    if desktop_mode:
        args.append("--no-vr")
    if osc_index is not None:
        import OSCClient
        args.append(OSCClient.osc_launch_arg(osc_index))
    if instance_link:
        args.append(instance_link)
    return args


def launch_one(
    exe: Path,
    profile_id: int,
    desktop_mode: bool = True,
    instance_link: Optional[str] = None,
    osc_index: Optional[int] = None,
):
    args = build_launch_args(exe, profile_id, desktop_mode, instance_link, osc_index)
    subprocess.Popen(args, cwd=str(Path(exe).parent))
    return args


def wait_for_windows(
    baseline_hwnds: set,
    expected_total: int,
    timeout_sec: float,
    poll_sec: float = 1.0,
    is_cancelled=None,
    discover=None,
) -> list[int]:
    """VRChatウィンドウが期待数に達するまで待つ。
    戻り値は新しく現れたHWND（起動が古い順）。"""
    if discover is None:
        import VRChatDiscovery
        discover = lambda: [h for h, _t in VRChatDiscovery.get_vrchat_windows_by_start_time(8)]
    deadline = time.time() + timeout_sec
    new_hwnds: list[int] = []
    while time.time() < deadline:
        if is_cancelled is not None and is_cancelled():
            break
        current = discover()
        new_hwnds = [h for h in current if h not in baseline_hwnds]
        if len(new_hwnds) >= expected_total:
            break
        time.sleep(poll_sec)
    return new_hwnds
