import os
import sys
import json
import hashlib
import time
from datetime import datetime, timezone
import random
import threading
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

import config
import DebugLog
import RoundStore

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_, **__):
        return False

def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            normalized = path.resolve()
        except OSError:
            normalized = path
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(path)
    return deduped


def env_file_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = []
    compiled = globals().get("__compiled__")
    if compiled is not None:
        candidates.append(Path(compiled.containing_dir) / ".env")

    candidates.extend((
        Path(sys.executable).resolve().parent / ".env",
        here / ".env",
        here.parent / ".env",
        here.parent / "ToNAutoBeginner" / ".env",
    ))
    return _dedupe_paths(candidates)


for env_path in env_file_candidates():
    load_dotenv(env_path)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
REQUEST_TIMEOUT = 10
DEFAULT_EXCLUDED_STAT_ROUNDS = ("Classic", "Run")

def _configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)

def _headers(accept: bool = False) -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    if accept:
        headers["Accept"] = "application/json"
    return headers

def _url(path: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{path}"

def _not_in_filter(column: str, values: tuple[str, ...] | list[str] | None) -> str:
    if not values:
        return ""
    encoded_values = ",".join(urllib.parse.quote(str(value), safe="") for value in values)
    return f"&{column}=not.in.({encoded_values})"

def _in_filter(column: str, values: tuple[str, ...] | list[str] | None) -> str:
    if not values:
        return ""
    encoded_values = ",".join(urllib.parse.quote(str(value), safe="") for value in values)
    return f"&{column}=in.({encoded_values})"

# 追加できない時はNoneを返す
def send_Users(VRChat_uid: str) -> int | None:
    if not _configured():
        print("Supabase設定がないためユーザー登録をスキップします。")
        return None
    uid = urllib.parse.quote(VRChat_uid, safe="")
    # VRChat_uidが既に存在するか確認
    req = urllib.request.Request(
        _url(f"Users?VRChat_uid=eq.{uid}&select=transformed_uid"),
        headers=_headers(accept=True)
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
        existing_user = json.loads(res.read())
    if existing_user:
        print("既に登録されています。")
        return existing_user[0]["transformed_uid"]
    
    req = urllib.request.Request(
        _url("Users?select=transformed_uid"),
        headers=_headers(accept=True)
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
        existing = {row["transformed_uid"] for row in json.loads(res.read())} # 既存のtransformed_uidを取得
    if len(existing) >= 65534:
        print("これ以上transformed_uidを追加できません。")
        return
    while True:
        transformed_uid = random.randint(-32768, 32767)
        if transformed_uid not in existing:
            break
        
    Users_data = json.dumps({
        "VRChat_uid": VRChat_uid, 
        "transformed_uid": transformed_uid, 
    }).encode()
    req = urllib.request.Request(
        _url("Users"),
        data=Users_data,
        headers={
            "Content-Type": "application/json",
            **_headers(),
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
        print(f"ユーザー登録: {res.status}")
        return transformed_uid

def get_transformed_uid(VRChat_uid: str) -> int | None:
    if not _configured():
        print("Supabase設定がないためtransformed_uid取得をスキップします。")
        return None
    try:
        uid = urllib.parse.quote(VRChat_uid, safe="")
        req = urllib.request.Request(
            _url(f"Users?VRChat_uid=eq.{uid}&select=transformed_uid"),
            headers=_headers(accept=True)
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
            data = json.loads(res.read())
        if data:
            return data[0]["transformed_uid"]
        # なければ新規登録
        return send_Users(VRChat_uid)
    except Exception as e:
        print(f"transformed_uid取得エラー: {e}")
        return None

def round_type_id(round_name: str) -> int:
    """ログのラウンド名 → 番号（ToN Save Manager の ToNRoundType）。表に無ければ 999"""
    return config.ROUND_TYPE_IDS.get(str(round_name or "").strip(),
                                     config.ROUND_TYPE_UNKNOWN_ID)


def instance_key(instance_id: str) -> int | None:
    """インスタンスの ID の SHA-256 の先頭8バイトを、符号つき64bit整数にする。空なら None"""
    if not instance_id:
        return None
    digest = hashlib.sha256(instance_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def db_time(epoch: float | None) -> int:
    """epoch 秒 → 2026-01-01 00:00:00 UTC からの秒。無ければ今"""
    return int(time.time() if epoch is None else epoch) - config.DB_TIME_EPOCH


def register_round(round_name: str, terror_ids: list[int], map_id: int,
                   transformed_uid: int | None, quiet: bool = False,
                   instance_key: int | None = None, round_time: float | None = None):
    """ラウンドを DB v1（"ToNRounds"）へ送る（関数 register_round を通す）。

    instance_key が同じで開始の差が15秒以内なら、DB 側で1行にまとめる（ソロなら None）。
    round_time はそのラウンドの開始の行の時刻（epoch）。テラーは先頭3つ。
    quiet なら送信について何も出さない（霧の看破・Enrage 系）
    """
    say = (lambda _m: None) if quiet else print
    round_id = round_type_id(round_name)
    if round_id == config.ROUND_TYPE_UNKNOWN_ID:
        DebugLog.write(f"未知のラウンド名: {round_name!r}")
    if not _configured():
        say("Supabase設定がないためラウンド統計送信をスキップします。")
        return
    ids = (list(terror_ids or []) + [None, None, None])[:3]
    payload = {
        "p_instance": instance_key,
        "p_time": db_time(round_time),
        "p_round": round_id,
        "p_map": map_id,
        "p_t1": ids[0], "p_t2": ids[1], "p_t3": ids[2],
        "p_uid": transformed_uid,
    }

    def _send():
        # 手元にも「自分の行」として貯める（統計画面は自分の行を DB から取りに行かない）
        RoundStore.default_store().add_own(
            payload["p_time"], round_id, map_id, ids[0], ids[1], ids[2], transformed_uid)
        try:
            req = urllib.request.Request(
                _url("rpc/register_round"),
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **_headers()},
                method="POST")
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
                say(f"Supabase登録: {res.status}")
        except urllib.error.HTTPError as e:
            say(f"HTTPエラー: {e.code} {e.read()}")
        except Exception as e:
            say(f"送信エラー: {e}")

    threading.Thread(target=_send, daemon=True).start()


def round_row(row: dict) -> dict:
    """"ToNRounds" の1行を、統計画面が使う形（round 名・terror_ids・created_at など）に直す。
    other_uids（同じラウンドを見たほかの人）があっても1件"""
    number = row.get("round")
    ts = row.get("time")
    created = (datetime.fromtimestamp(int(ts) + config.DB_TIME_EPOCH, timezone.utc)
               .isoformat().replace("+00:00", "Z") if ts is not None else None)
    return {
        "created_at": created,
        "round": config.ROUND_TYPE_NAMES.get(number, str(number)),
        "terror_ids": [t for t in (row.get("terror1"), row.get("terror2"), row.get("terror3"))
                       if t is not None],
        "map_id": row.get("map_id"),
        "transformed_uid": row.get("transformed_uid"),
    }


def get_ToNRoundStatistics(
    exclude_rounds: tuple[str, ...] | list[str] | None = DEFAULT_EXCLUDED_STAT_ROUNDS,
    include_rounds: tuple[str, ...] | list[str] | None = None,
):
    if not _configured():
        print("Supabase設定がないため集計データ取得をスキップします。")
        return []
    all_rows = []
    offset = 0
    page_size = 1000
    # 絞り込みはラウンドの番号で（名前 → 番号）
    include_ids = sorted({round_type_id(n) for n in include_rounds or ()})
    exclude_ids = sorted({round_type_id(n) for n in exclude_rounds or ()})
    round_filter = (_in_filter("round", include_ids) if include_rounds
                    else _not_in_filter("round", exclude_ids))
    while True:
        req = urllib.request.Request(
            _url(f"ToNRounds?select=*&order=time.desc{round_filter}&limit={page_size}&offset={offset}"),
            headers=_headers(accept=True)
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
            data = json.loads(res.read().decode("utf-8"))
        all_rows.extend(round_row(row) for row in data)
        if len(data) < page_size:
            break
        offset += page_size
    return all_rows
