"""不具合の報告を Discord の Webhook へ送る。

送り先は .env の DISCORD_REPORT_WEBHOOK_URL（ConnectDB と同じ候補から読む）。
URL はどこにも出さない（画面・ログ・debug.log・失敗の文言）。
送るもの: 本文（版・窓・時刻・要件）と zip 1つ（report.txt・画面のログ・debug.log の末尾・
選んだ窓の VRChat のログの末尾・設定）。Windows のユーザー名は %USERPROFILE% に伏せる。
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

import BeginMiss
import config
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
               (BEGIN_MISS, "Begin の撮影（見つからなかったとき）"))


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
def content(version: str, window, requirement: str, now: datetime) -> str:
    """Discord の本文（2000 文字に収める）"""
    head = f"🐞 不具合報告 {version} / {window_label(window)} / {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
    text = head + requirement
    if len(text) <= CONTENT_MAX:
        return text
    return head + requirement[:CONTENT_MAX - len(head) - len(CONTENT_MORE)] + CONTENT_MORE


def window_label(window) -> str:
    return f"窓{window}" if window else "窓なし"


def build_report(requirement: str, window, include: dict, gui_log: str, vrchat_log_path,
                 settings: dict, now: datetime, debug_log_path=None,
                 version: str | None = None) -> tuple[str, bytes]:
    """(zip のファイル名, zip の中身)。REPORT_MAX_BYTES を超えたら VRChat のログを減らす"""
    version = version or config.APP_VERSION
    debug_log_path = debug_log_path or config.DEBUG_LOG_PATH
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

    # Begin の撮影（窓を選べばその窓の2枚、窓なしなら全部の窓の分）。PNG なのでそのまま入れる
    images: dict[str, bytes] = {}
    if not include.get(BEGIN_MISS, False):
        notes.append("- Begin の撮影（見つからなかったとき）: 入れていません（未選択）")
    else:
        images = BeginMiss.files_for(window)
        if not images:
            notes.append("- Begin の撮影（見つからなかったとき）: 無し")
    dropped: list[str] = []

    vrchat_name = f"vrchat_log_window{window}.txt" if window else ""
    limit = config.REPORT_VRCHAT_LOG_START_BYTES
    while True:
        vr_notes = []
        vr_files = {}
        if not include.get(VRCHAT_LOG, False):
            vr_notes.append("- 選んだ窓の VRChat のログ: 入れていません（未選択）")
        elif not window:
            vr_notes.append("- 選んだ窓の VRChat のログ: 入れていません（窓を選んでいない）")
        elif not vrchat_log_path:
            vr_notes.append("- 選んだ窓の VRChat のログ: 入れていません（窓に未割り当て）")
        else:
            data = tail_bytes(vrchat_log_path, limit)
            if data is None:
                vr_notes.append("- 選んだ窓の VRChat のログ: 入れていません（ファイルが無い）")
            elif limit < config.REPORT_VRCHAT_LOG_MIN_BYTES:
                vr_notes.append("- 選んだ窓の VRChat のログ: 入れていません（大きすぎて収まらない）")
            else:
                vr_files[vrchat_name] = _clean(data.decode("utf-8", errors="replace")).encode("utf-8")
                cut = "（末尾）" if _size(vrchat_log_path) > limit else ""
                vr_notes.append(f"- 選んだ窓の VRChat のログ: {vrchat_name}{cut}")
        image_notes = ([f"- Begin の撮影（見つからなかったとき）: {', '.join(images)}"] if images else [])
        image_notes += [f"- Begin の撮影 {name}: 入れていません（大きすぎて収まらない）" for name in dropped]
        report = _report_txt(requirement, window, now, version, notes + image_notes + vr_notes)
        data = _zip({"report.txt": report.encode("utf-8"), **files, **images, **vr_files})
        if len(data) <= config.REPORT_MAX_BYTES:
            return f"report_{now.strftime('%Y%m%d_%H%M%S')}.zip", data
        if vr_files:
            limit //= 2                 # 先に VRChat のログを削る（今の順番のまま）
        elif images:
            name = list(images)[-1]     # それでも収まらなければ撮影を新しい方から外す
            images.pop(name)
            dropped.append(name)
        else:
            return f"report_{now.strftime('%Y%m%d_%H%M%S')}.zip", data


def _report_txt(requirement, window, now, version, notes) -> str:
    runtime = "exe" if "__compiled__" in globals() else "python"
    return _clean("\n".join([
        f"不具合報告 {version}",
        f"窓: {window_label(window)}",
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
