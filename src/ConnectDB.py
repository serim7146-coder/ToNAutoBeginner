import os
import sys
import json
import random
import threading
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_, **__):
        return False

base = Path(sys.executable).parent if getattr(sys, '__compiled__', False) else Path(__file__).parent
load_dotenv(base / ".env")
load_dotenv(base.parent / ".env")

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

def send_ToNRoundStatistics(round_name: str, terror_ids: list[int], map_id: int, transformed_uid: int | None):
    if not _configured():
        print("Supabase設定がないためラウンド統計送信をスキップします。")
        return
    def _send_ToNRoundStatistics():
        try:
            data = json.dumps({
                "round": round_name,
                "terror_ids": terror_ids,
                "map_id": map_id,
                "transformed_uid": transformed_uid
            }).encode()
            req = urllib.request.Request(
                _url("ToNRoundStatistics"),
                data=data,
                headers={
                    "Content-Type": "application/json",
                    **_headers(),
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
                print(f"Supabase登録: {res.status}")
        except urllib.error.HTTPError as e:
            print(f"HTTPエラー: {e.code} {e.read()}")
        except Exception as e:
            print(f"送信エラー: {e}")
            
    threading.Thread(target=_send_ToNRoundStatistics, daemon=True).start()

# どうせ全部取り出すことになるので、全部取り出す仕様
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
    round_filter = _in_filter("round", include_rounds) if include_rounds else _not_in_filter("round", exclude_rounds)
    while True:
        req = urllib.request.Request(
            _url(f"ToNRoundStatistics?select=*&order=created_at.desc{round_filter}&limit={page_size}&offset={offset}"),
            headers=_headers(accept=True)
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
            data = json.loads(res.read().decode("utf-8"))
        all_rows.extend(data)
        if len(data) < page_size:
            break
        offset += page_size
    return all_rows
