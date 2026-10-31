"""不具合の報告を Discord の Webhook へ送る。

送り先は .env の DISCORD_REPORT_WEBHOOK_URL（ConnectDB と同じ候補から読む）。
URL はどこにも出さない（画面・ログ・debug.log・失敗の文言）。
送るもの: 本文（版・窓・時刻・要件）と zip 1つ（report.txt・画面のログ・debug.log の末尾・
選んだ窓の VRChat のログの末尾・設定（settings.json・続行表・続行リスト））。Windows のユーザー名は %USERPROFILE% に伏せる。
画面（mainGUI）からは build_report() で中身を作り、send() で送る。どちらも別スレッドで呼ぶ。
"""
import io
import json
import os
import platform
import re
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import BeginAdjustShots
import BeginMiss
import FetchMiss
import RunFace
import config
import ContinueList
import ContinueTable
import ConnectDB            # .env を読み込む（同じ候補）。値は debug.log に書かないよう覚えている
import DebugLog

WEBHOOK_ENV = "DISCORD_REPORT_WEBHOOK_URL"
CONTENT_MAX = 2000          # Discord の本文の上限
CONTENT_MORE = "…（続きは report.txt）"

# 添付の名前（画面のチェックと合わせる）
GUI_LOG = "gui_log"
DEBUG_LOG = "debug_log"
VRCHAT_LOG = "vrchat_log"
SETTINGS = "settings"
BEGIN_MISS = "begin_miss"       # Begin の位置合わせで見つからなかった撮影
ATTACHMENTS = ((GUI_LOG, "画面のログ出力"), (DEBUG_LOG, "debug.log"),
               (VRCHAT_LOG, "選んだ窓の VRChat のログ"), (SETTINGS, "設定"),
               (BEGIN_MISS, "撮影（Begin・Run の向きで見つからなかったとき）"))


def webhook_url() -> str:
    url = (os.getenv(WEBHOOK_ENV) or "").strip()
    DebugLog.add_secret(url)
    return url


# ── 伏せる・切る ───────────────────────────────
def mask_user(text: str, home: str | None = None) -> str:
    """C:\\Users\\<名前>（~ と同じもの。大文字小文字を区別しない、\\ と / の両方）を伏せる"""
    home = home if home is not None else os.path.expanduser("~")
    text = str(text)
    if home and home != "~":
        for form in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(form), "%USERPROFILE%", text, flags=re.IGNORECASE)
    return text


def _clean(text: str) -> str:
    """ユーザー名と秘密（.env の値・OBS のパスワード）を伏せる"""
    return DebugLog.scrub(mask_user(text))


def _cut_to_line(data: bytes) -> bytes:
    """途中から始まる末尾は、最初の改行の後から（行の途中から始めない）"""
    cut = data.find(b"\n")
    return data[cut + 1:] if cut >= 0 else b""


def tail_bytes(path, limit: int) -> bytes | None:
    """ファイルの末尾 limit バイト（行の頭から）。無い・読めなければ None"""
    try:
        path = Path(path)
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size <= limit:
                return f.read()
            f.seek(size - limit)
            return _cut_to_line(f.read())
    except (OSError, TypeError, ValueError):
        return None


def read_file(path) -> bytes | None:
    """ファイルを全部。無い・読めなければ None（続行表・続行リストのような小さいファイル用）"""
    try:
        return Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None


def tail_text(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return _cut_to_line(data[-limit:]).decode("utf-8", errors="replace")


def debug_log_tail(path, limit: int) -> bytes | None:
    """debug.log の末尾 limit バイト。足りなければ .1 の後ろの分を前に足す"""
    newest = tail_bytes(path, limit)
    if newest is None:
        return None
    rest = limit - len(newest)
    older = tail_bytes(Path(str(path) + ".1"), rest) if rest > 0 and Path(str(path) + ".1").exists() else None
    if older and len(newest) == _size(path):     # 新しい方を全部入れたときだけ前を足す
        return older + newest
    return newest


def _size(path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return -1


def settings_for_report(data: dict) -> dict:
    return {k: v for k, v in (data or {}).items() if k not in config.REPORT_SETTINGS_EXCLUDE_KEYS}


# ── 本文と zip ─────────────────────────────────
def content(version: str, window, requirement: str, now: datetime, all_windows: int = 0) -> str:
    """Discord の本文（2000 文字に収める）"""
    head = (f"🐞 不具合報告 {version} / {window_label(window, all_windows)} / "
            f"{now.strftime('%Y-%m-%d %H:%M:%S')}\n")
    text = head + requirement
    if len(text) <= CONTENT_MAX:
        return text
    return head + requirement[:CONTENT_MAX - len(head) - len(CONTENT_MORE)] + CONTENT_MORE


def selected_windows(window) -> list[int]:
    """窓の選び方（窓の番号1つ・0＝窓なし・番号の並び）を、番号の並び（小さい順・重複なし）にする"""
    if isinstance(window, (list, tuple, set)):
        return sorted({int(w) for w in window if w})
    return [int(window)] if window else []


def window_label(window, all_windows: int = 0) -> str:
    """「窓2」「窓1・窓3・窓5」「全窓（6）」「窓なし」。all_windows は今の窓の数（2 以上で全部選べば全窓）"""
    windows = selected_windows(window)
    if not windows:
        return "窓なし"
    if all_windows >= 2 and windows == list(range(1, all_windows + 1)):
        return f"全窓（{all_windows}）"
    return "・".join(f"窓{n}" for n in windows)


def build_report(requirement: str, window, include: dict, gui_log: str, vrchat_log_path,
                 settings: dict, now: datetime, debug_log_path=None,
                 version: str | None = None, all_windows: int = 0) -> tuple[str, bytes]:
    """(zip のファイル名, zip の中身)。REPORT_MAX_BYTES を超えたら VRChat のログを減らす。

    window は窓の番号1つ（0 は窓なし）か番号の並び（DR: 複数・全部）。vrchat_log_path は1つの窓なら
    そのパス、複数なら {窓の番号: パス}。VRChat のログは全部の窓で同じ長さずつ（始めは
    REPORT_VRCHAT_LOG_START_BYTES ÷ 窓の数、収まらなければ全部を半分に）。1 窓・窓なしは今までと同じ中身"""
    version = version or config.APP_VERSION
    debug_log_path = debug_log_path or config.DEBUG_LOG_PATH
    windows = selected_windows(window)
    if isinstance(vrchat_log_path, dict):
        log_paths = vrchat_log_path
    else:
        log_paths = {windows[0]: vrchat_log_path} if windows else {}
    files: dict[str, bytes] = {}
    notes: list[str] = []

    def add(key, label, name, data, missing):
        if not include.get(key, False):
            notes.append(f"- {label}: 入れていません（未選択）")
        elif data is None:
            notes.append(f"- {label}: 入れていません（{missing}）")
        else:
            files[name] = _clean(data.decode("utf-8", errors="replace")
                                 if isinstance(data, bytes) else data).encode("utf-8")
            notes.append(f"- {label}: {name}")

    add(GUI_LOG, "画面のログ出力", "gui_log.txt", tail_text(gui_log or "", config.REPORT_GUI_LOG_MAX_BYTES),
        "ログが空です")
    add(DEBUG_LOG, "debug.log", "debug_log.txt",
        debug_log_tail(debug_log_path, config.REPORT_DEBUG_LOG_MAX_BYTES), "ファイルが無い")
    add(SETTINGS, "設定", "settings.json",
        json.dumps(settings_for_report(settings), ensure_ascii=False, indent=2, sort_keys=True), "")
    # 続行表・続行リストの保存ファイル（独自の番号とプリセットの名前だけ）も「設定」のチェックで入れる
    add(SETTINGS, "続行表（続行無効・デフォ続行・プリセット・アイテムの決まり）", "continue_table.json",
        read_file(ContinueTable.table_path()), "ファイルが無い")
    add(SETTINGS, "ツールの続行リスト", "continue_list.json", read_file(ContinueList.list_path()), "ファイルが無い")

    # 撮影（Begin は窓を選べばその窓の2枚、Run の向きはその窓の分。窓なしなら全部の窓の分）。そのまま入れる
    images: dict[str, bytes] = {}
    if not include.get(BEGIN_MISS, False):
        notes.append("- 撮影（Begin・Run の向きで見つからなかったとき）: 入れていません（未選択）")
    else:
        # Run の向きの撮影（DQ）も同じチェックで入れる（ほかのプレイヤーの名前が写りうる）
        # アイテム取得で見つからなかった撮影（ER）も同じ
        # 位置合わせの各段の撮影（EW）も同じ
        for files_for in (BeginMiss.files_for, BeginAdjustShots.files_for, RunFace.files_for, FetchMiss.files_for):
            for n in windows or [0]:
                images.update(files_for(n))
        if not images:
            notes.append("- 撮影（Begin・Run の向きで見つからなかったとき）: 無し")
    dropped: list[str] = []

    limit = config.REPORT_VRCHAT_LOG_START_BYTES // max(1, len(windows))
    while True:
        vr_notes = []
        vr_files = {}
        if not include.get(VRCHAT_LOG, False):
            vr_notes.append("- 選んだ窓の VRChat のログ: 入れていません（未選択）")
        elif not windows:
            vr_notes.append("- 選んだ窓の VRChat のログ: 入れていません（窓を選んでいない）")
        for n in (windows if include.get(VRCHAT_LOG, False) else []):
            label = "選んだ窓の VRChat のログ" if len(windows) == 1 else f"窓{n} の VRChat のログ"
            path = log_paths.get(n) or ""
            name = f"vrchat_log_window{n}.txt"
            if not path:
                vr_notes.append(f"- {label}: 入れていません（窓に未割り当て）")
                continue
            data = tail_bytes(path, limit)
            if data is None:
                vr_notes.append(f"- {label}: 入れていません（ファイルが無い）")
            elif limit < config.REPORT_VRCHAT_LOG_MIN_BYTES:
                vr_notes.append(f"- {label}: 入れていません（大きすぎて収まらない）")
            else:
                vr_files[name] = _clean(data.decode("utf-8", errors="replace")).encode("utf-8")
                cut = "（末尾）" if _size(path) > limit else ""
                vr_notes.append(f"- {label}: {name}{cut}")
        image_notes = ([f"- 撮影（Begin・Run の向きで見つからなかったとき）: {', '.join(images)}"] if images else [])
        image_notes += [f"- 撮影 {name}: 入れていません（大きすぎて収まらない）" for name in dropped]
        report = _report_txt(requirement, window, now, version, notes + image_notes + vr_notes, all_windows)
        data = _zip({"report.txt": report.encode("utf-8"), **files, **images, **vr_files})
        if len(data) <= config.REPORT_MAX_BYTES:
            return f"report_{now.strftime('%Y%m%d_%H%M%S')}.zip", data
        if vr_files:
            limit //= 2                 # 先に VRChat のログを削る（全部の窓で同じ長さずつ）
        elif images:
            name = list(images)[-1]     # それでも収まらなければ撮影を新しい方から外す
            images.pop(name)
            dropped.append(name)
        else:
            return f"report_{now.strftime('%Y%m%d_%H%M%S')}.zip", data


def _report_txt(requirement, window, now, version, notes, all_windows: int = 0) -> str:
    runtime = "exe" if "__compiled__" in globals() else "python"
    return _clean("\n".join([
        f"不具合報告 {version}",
        f"窓: {window_label(window, all_windows)}",
        f"時刻: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Windows: {platform.platform()}",
        f"実行: {runtime}（Python {sys.version.split()[0]}）",
        "",
        "添付:",
        *notes,
        "",
        "何が起きたか:",
        requirement,
    ]) + "\n")


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


# ── 送る ────────────────────────────────────
def multipart(text: str, zip_name: str, zip_data: bytes) -> tuple[bytes, str]:
    """(本文, Content-Type)。payload_json（content・allowed_mentions は空）＋ files[0]"""
    boundary = uuid.uuid4().hex
    payload = json.dumps({"content": text, "allowed_mentions": {"parse": []}}, ensure_ascii=False)
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
        f"Content-Type: application/json\r\n\r\n".encode("utf-8") + payload.encode("utf-8") + b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{zip_name}\"\r\n"
        f"Content-Type: application/zip\r\n\r\n".encode("utf-8") + zip_data + b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send(url: str, text: str, zip_name: str, zip_data: bytes, opener=None) -> tuple[bool, str]:
    """送る。(成功したか, 短い結果)。結果に URL は入れない"""
    if not url:
        return False, "送り先が設定されていません"
    body, ctype = multipart(text, zip_name, zip_data)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": ctype, "User-Agent": f"ToNAutoBeginner/{config.APP_VERSION}"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=config.REPORT_TIMEOUT_SEC) as res:
            status = getattr(res, "status", 200)
        if status in (200, 204):
            return True, "送信しました"
        return False, f"送れませんでした（HTTP {status}）"
    except urllib.error.HTTPError as e:
        return False, f"送れませんでした（HTTP {e.code}）"
    except urllib.error.URLError:
        return False, "送れませんでした（通信できません）"
    except Exception as e:
        return False, f"送れませんでした（{type(e).__name__}）"
