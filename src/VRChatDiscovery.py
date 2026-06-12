import glob
import os
from pathlib import Path

import win32gui

import config


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
