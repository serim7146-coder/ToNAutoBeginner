import datetime
import glob
import os
from pathlib import Path
from typing import Optional

import win32gui
import win32api
import win32con
import win32process

import config


LOG_NAME_PREFIX = "output_log_"
LOG_NAME_SUFFIX = ".txt"
LOG_NAME_TIME_FORMAT = "%Y-%m-%d_%H-%M-%S"


def get_vrchat_windows(hwnd_count: int) -> list[int]:
    hwnds = []
    found_hwnd = 0

    def cb(h, _):
        nonlocal found_hwnd
        if found_hwnd >= hwnd_count:
            return
        if not win32gui.IsWindowVisible(h):
            return
        if "VRChat" not in win32gui.GetWindowText(h):
            return
        if win32gui.GetClassName(h) == config.VRCHAT_WINDOW_CLASS:
            hwnds.insert(0, h)
            found_hwnd += 1

    if found_hwnd >= hwnd_count:
        return hwnds
    win32gui.EnumWindows(cb, None)
    return hwnds


# ═══════════════════════════════════════════════
#  起動時刻によるウィンドウ↔ログ対応付け
#  EnumWindowsの順序はZオーダー（前面にある順）で、窓を切り替えるだけで
#  入れ替わる。起動時刻で突き合わせることで順序に依存せず対応付ける。
# ═══════════════════════════════════════════════
def parse_log_start_time(path) -> Optional[float]:
    """ログファイル名(output_log_YYYY-MM-DD_HH-MM-SS.txt)からVRChat起動時刻を取り出す"""
    name = Path(path).name
    if not name.startswith(LOG_NAME_PREFIX) or not name.endswith(LOG_NAME_SUFFIX):
        return None
    stamp = name[len(LOG_NAME_PREFIX):-len(LOG_NAME_SUFFIX)]
    try:
        return datetime.datetime.strptime(stamp, LOG_NAME_TIME_FORMAT).timestamp()
    except ValueError:
        return None


def get_process_start_time(hwnd: int) -> Optional[float]:
    """ウィンドウを持つプロセスの起動時刻(UNIX秒)。取得できなければNone。
    VRChatが管理者権限で起動している場合などは取得に失敗しうる。"""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return None
    try:
        return win32process.GetProcessTimes(handle)["CreationTime"].timestamp()
    except Exception:
        return None
    finally:
        try:
            win32api.CloseHandle(handle)
        except Exception:
            pass


def get_vrchat_windows_by_start_time(max_count: int = config.MAX_WINDOWS) -> list[tuple[int, Optional[float]]]:
    """VRChatウィンドウを (HWND, 起動時刻) の形で起動が古い順に返す。
    起動時刻を取得できなかった窓はNoneを付けて末尾に置く（従来のZオーダー逆順）。"""
    zorder: list[int] = []

    def cb(h, _):
        if len(zorder) >= max_count:
            return
        if not win32gui.IsWindowVisible(h):
            return
        if "VRChat" not in win32gui.GetWindowText(h):
            return
        if win32gui.GetClassName(h) == config.VRCHAT_WINDOW_CLASS:
            zorder.append(h)

    win32gui.EnumWindows(cb, None)

    timed = [(h, get_process_start_time(h)) for h in reversed(zorder)]
    known   = sorted((t for t in timed if t[1] is not None), key=lambda t: t[1])
    unknown = [t for t in timed if t[1] is None]
    return known + unknown


def time_matched_pairs(
    windows: list[tuple[int, Optional[float]]],
    log_times: list[tuple[Path, Optional[float]]],
    tolerance_sec: float,
) -> dict[int, int]:
    """起動時刻が一致する窓とログを1対1で対応付ける。
    戻り値は {窓index: ログindex}。差が小さい組から確定する。"""
    pairs: list[tuple[float, int, int]] = []
    for wi, (_hwnd, wt) in enumerate(windows):
        if wt is None:
            continue
        for li, (_path, lt) in enumerate(log_times):
            if lt is None:
                continue
            diff = abs(lt - wt)
            if diff <= tolerance_sec:
                pairs.append((diff, wi, li))
    pairs.sort()

    result: dict[int, int] = {}
    used_logs: set[int] = set()
    for _diff, wi, li in pairs:
        if wi in result or li in used_logs:
            continue
        result[wi] = li
        used_logs.add(li)
    return result


def count_time_matched_logs(
    windows: list[tuple[int, Optional[float]]],
    candidate_logs: list[Path],
    tolerance_sec: float,
) -> int:
    """起動時刻でログが確定した窓の数。起動直後のログ生成待ちに使う。"""
    log_times = [(p, parse_log_start_time(p)) for p in candidate_logs]
    return len(time_matched_pairs(windows, log_times, tolerance_sec))


def match_windows_to_logs(
    windows: list[tuple[int, Optional[float]]],
    candidate_logs: list[Path],
    tolerance_sec: float,
) -> list[Optional[Path]]:
    """窓とログを起動時刻で1対1に対応付ける。
    差が小さい組から貪欲に確定し、対応が付かなかった窓には
    未使用ログを新しい順に取って古い順で割り当てる（従来方式のフォールバック）。"""
    log_times = [(p, parse_log_start_time(p)) for p in candidate_logs]
    matched = time_matched_pairs(windows, log_times, tolerance_sec)

    result: list[Optional[Path]] = [None] * len(windows)
    used_logs: set[int] = set()
    for wi, li in matched.items():
        result[wi] = log_times[li][0]
        used_logs.add(li)

    unmatched = [wi for wi in range(len(windows)) if result[wi] is None]
    if unmatched:
        leftover = [p for i, (p, _t) in enumerate(log_times) if i not in used_logs]
        leftover = leftover[-len(unmatched):]  # 新しい方から必要数だけ取り、古い順のまま使う
        for wi, path in zip(unmatched, leftover):
            result[wi] = path
    return result


def find_latest_logs(base_dir: Path, count: int = 4) -> list[Path]:
    pattern = str(base_dir / "output_log_*.txt")
    files = glob.glob(pattern)

    def log_datetime(path: str) -> str:
        name = os.path.basename(path)
        try:
            return name.replace("output_log_", "").replace(".txt", "")
        except Exception:
            return ""

    sorted_desc = sorted(files, key=log_datetime, reverse=True)
    latest = sorted_desc[:count]
    return [Path(f) for f in sorted(latest, key=log_datetime, reverse=False)]
