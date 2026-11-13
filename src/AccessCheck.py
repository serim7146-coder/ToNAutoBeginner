"""動かしてよいかをサーバー（Supabase の check_access。supabase/check_access.sql）に聞く。

締め出しの一覧と「使える最低の版」はサーバーにある。ツールは自分の UID と版だけを送って、
{"allowed", "min_version", "require_uid"} を受け取る。締め出しなら、何も出さずにプロセスを終える
（終わらせれば、マクロ・VRChat の起動・スマホ通知・OSC・声・窓の操作がすべて止まる）。
理由・UID はどこにも出さない（ログ・デバッグログ・画面・不具合の報告のどれにも）。

聞く先（URL と鍵）は、exe に同梱した .env だけ。exe の隣の .env・環境変数では変えられない。

結果:
  ALLOWED     動かしてよい
  BANNED      締め出し（enforce は終わらせる）
  UNREACHABLE サーバーに聞けず、最後の OK から ACCESS_GRACE_SEC を過ぎた（一度も OK が無い）
  NO_UID      UID が読めず、サーバーの設定 require_uid が真

サーバーに聞けないときのために、手元の記録（access.dat。DPAPI で包む）に、UID の SHA-256 ごとの
最後の OK の時刻と締め出しの印、サーバーの設定を残す。生の UID は書かない。
"""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import AutoUpdate
import DebugLog
import SecretStore
import TaskStats
import config

try:
    from dotenv import dotenv_values
except ModuleNotFoundError:
    def dotenv_values(*_, **__):
        return {}

ALLOWED = "allowed"
BANNED = "banned"
UNREACHABLE = "unreachable"
NO_UID = "no_uid"

# 終わらせる処理。本番は即座に・捕まえられずに終わる。テストはこれを差し替える
_TERMINATE = os._exit

RPC_NAME = "check_access"
_FUTURE_SLACK_SEC = 300.0       # 手元の記録の時刻が、今よりこれ以上未来ならその記録は無効

_lock = threading.Lock()
_memory: dict = {}              # UID の印 → (ALLOWED か BANNED, 聞いた時刻 monotonic)。サーバーの答えだけ
_asked: dict = {}               # UID の印 → 最後に聞いた時刻 monotonic（答えが来なくても）
_settings: dict = {}            # 最後の答えの設定 {"min_version", "require_uid"}
_rechecking = False


def _wall() -> float:
    return time.time()


def _mono() -> float:
    return time.monotonic()


def _key(uid: str) -> str:
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()


def _record_path() -> Path:
    return config.SETTINGS_PATH.parent / "access.dat"


# ── サーバー ──────────────────────────────
def _compiled() -> bool:
    return globals().get("__compiled__") is not None


def _server_config() -> tuple:
    """(URL, 鍵)。exe では同梱の .env だけを読む（os.environ には入れない・環境変数は見ない）。
    python で直接動かすときは ConnectDB が読んだ値"""
    if _compiled():
        values = dotenv_values(Path(__file__).resolve().parent / ".env")
        url, key = values.get("SUPABASE_URL"), values.get("SUPABASE_KEY")
    else:
        import ConnectDB
        url, key = ConnectDB.SUPABASE_URL, ConnectDB.SUPABASE_KEY
    DebugLog.add_secret(url)
    DebugLog.add_secret(key)
    return url, key


def _ask_server(uid: Optional[str], timeout: float) -> Optional[dict]:
    """check_access を呼ぶ。答えが {"allowed": bool, ...} の形ならそれ（min_version は文字列・
    require_uid は真偽に整える）。つながらない・HTTP エラー（404 を含む）・JSON でない・形が違う → None"""
    url, key = _server_config()
    if not url or not key:
        if _compiled():
            return None
        return {"allowed": True, "min_version": "", "require_uid": False}     # 開発実行は確認なしで動かす
    body = {"p_vrchat_uid": uid, "p_version": config.APP_VERSION}
    req = urllib.request.Request(
        f"{url}/rest/v1/rpc/{RPC_NAME}",
        data=json.dumps(body).encode("utf-8"),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            value = json.loads(res.read())
    except Exception as e:
        DebugLog.write(f"[確認] 通信に失敗: {type(e).__name__}")        # 型の名前だけ（URL・UID は入れない）
        return None
    if not isinstance(value, dict) or not isinstance(value.get("allowed"), bool):
        DebugLog.write("[確認] 答えの形が違います")
        return None
    min_version = value.get("min_version")
    return {"allowed": value["allowed"],
            "min_version": min_version if isinstance(min_version, str) else "",
            "require_uid": value.get("require_uid") is not False}


# ── 手元の記録 ────────────────────────────
def _load_record() -> dict:
    """読めない・壊れている・復号できない → 空"""
    empty = {"users": {}}
    try:
        text = _record_path().read_text(encoding="ascii").strip()
        plain = SecretStore.unprotect(text)
        if plain is None:
            return empty
        record = json.loads(plain)
    except Exception:
        return empty
    if not isinstance(record, dict) or not isinstance(record.get("users"), dict):
        return empty
    return record


def _save_record(record: dict) -> None:
    """一時ファイルに書いて置き換える。失敗しても黙って続ける（次の機会にまた書く）"""
    try:
        wrapped = SecretStore.protect(json.dumps(record))
        if wrapped is None:
            return
        path = _record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(wrapped, encoding="ascii")
        os.replace(tmp, path)
    except Exception:
        pass


def _entry(record: dict, key: str) -> tuple:
    """(最後の OK の時刻 epoch か None, 締め出しの印)。今より 300 秒を超えて未来の時刻は無効（None）"""
    entry = record["users"].get(key)
    if not isinstance(entry, dict):
        return None, False
    banned = entry.get("banned") is True
    ok_at = entry.get("ok_at")
    if isinstance(ok_at, bool) or not isinstance(ok_at, (int, float)):
        return None, banned
    if ok_at > _wall() + _FUTURE_SLACK_SEC:
        return None, banned
    return float(ok_at), banned


def _remember(answer: dict, key: Optional[str], allowed: Optional[bool]) -> None:
    """サーバーの答えを覚える（設定は常に。UID は key があるとき）。変わったときだけ書く"""
    with _lock:
        _settings["min_version"] = answer["min_version"]
        _settings["require_uid"] = answer["require_uid"]
        record = _load_record()
        before = json.dumps(record, sort_keys=True)
        record["min_version"] = answer["min_version"]
        record["require_uid"] = answer["require_uid"]
        if key is not None:
            old = record["users"].get(key)
            old_ok_at = old.get("ok_at") if isinstance(old, dict) else None
            if allowed:
                record["users"][key] = {"ok_at": _wall(), "banned": False}
            else:
                record["users"][key] = {"ok_at": old_ok_at if isinstance(old_ok_at, (int, float))
                                        and not isinstance(old_ok_at, bool) else 0, "banned": True}
        if key is not None or json.dumps(record, sort_keys=True) != before:
            _save_record(record)


# ── 確認 ─────────────────────────────────
def verify(uid: Optional[str], timeout: Optional[float] = None) -> str:
    """UID を確認して ALLOWED / BANNED / UNREACHABLE / NO_UID を返す（締め出しでも終わらせない）。
    サーバーに聞くので待つ（スレッド・起動時に呼ぶ。Tk の画面のスレッドでは呼ばない）"""
    if timeout is None:
        timeout = config.ACCESS_TIMEOUT_SEC
    uid = uid.strip() if isinstance(uid, str) else ""
    if not uid:
        return _verify_without_uid(timeout)
    key = _key(uid)
    with _lock:
        hit = _memory.get(key)
    if hit is not None:
        result, at = hit
        if result == BANNED or _mono() - at < config.ACCESS_MEMORY_SEC:
            return result
    answer = _ask_server(uid, timeout)
    with _lock:
        _asked[key] = _mono()
    if answer is not None:
        allowed = answer["allowed"]
        result = ALLOWED if allowed else BANNED
        with _lock:
            _memory[key] = (result, _mono())
        _remember(answer, key, allowed)
        return result
    with _lock:
        ok_at, banned = _entry(_load_record(), key)
    if banned:
        return BANNED
    if ok_at is not None and _wall() - ok_at < config.ACCESS_GRACE_SEC:
        return ALLOWED
    return UNREACHABLE


def _verify_without_uid(timeout: float) -> str:
    answer = _ask_server(None, timeout)
    if answer is not None:
        _remember(answer, None, None)
        require = answer["require_uid"]
    else:
        with _lock:
            saved = _load_record().get("require_uid")
        require = saved if isinstance(saved, bool) else True
    return NO_UID if require else ALLOWED


def enforce(uid: Optional[str], timeout: Optional[float] = None) -> str:
    """verify の結果が BANNED なら、何も出さずに終わる（ログも print も出さない）。ほかは結果を返す"""
    result = verify(uid, timeout)
    if result == BANNED:
        _TERMINATE(0)
    return result


def enforce_startup() -> None:
    """起動時（画面を出す前）: 直近のログの UID を確認する。UID が読めなくても起動は続ける"""
    try:
        import VRChatLauncher
        uid = VRChatLauncher.latest_user_id(config.VRCHAT_LOG_DIR)
    except Exception:
        return
    if not uid:
        return
    enforce(uid, timeout=config.ACCESS_STARTUP_TIMEOUT_SEC)


def too_old() -> str:
    """覚えている min_version が今の版（config.APP_VERSION）より大きければ、その min_version。
    そうでなければ ""。解釈できない値は ""（止めない）"""
    with _lock:
        minimum = _settings.get("min_version")
        if minimum is None:
            minimum = _load_record().get("min_version")
    if not isinstance(minimum, str):
        return ""
    wanted = AutoUpdate.parse_version(minimum)
    current = AutoUpdate.parse_version(config.APP_VERSION)
    if wanted and current and wanted > current:
        return minimum
    return ""


def recheck_async(uid: Optional[str]) -> None:
    """動作中の定期の確認。前に聞いてから ACCESS_MEMORY_SEC たっていたら、裏のスレッドで 1 回聞く
    （重ねて走らせない。締め出しなら終わる。ほかの結果では何もしない）"""
    global _rechecking
    if not isinstance(uid, str) or not uid.strip():
        return
    key = _key(uid.strip())
    with _lock:
        asked = _asked.get(key)
        if asked is None or _mono() - asked < config.ACCESS_MEMORY_SEC or _rechecking:
            return
        _rechecking = True

    def work():
        global _rechecking
        try:
            enforce(uid)
        finally:
            with _lock:
                _rechecking = False

    TaskStats.thread("AccessCheck.recheck", work, daemon=True).start()


def reset_for_tests() -> None:
    global _rechecking
    with _lock:
        _memory.clear()
        _asked.clear()
        _settings.clear()
        _rechecking = False
