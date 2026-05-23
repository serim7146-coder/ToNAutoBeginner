import os
import sys
import json
import random
import threading
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

base = Path(sys.executable).parent if getattr(sys, '__compiled__', False) else Path(__file__).parent
load_dotenv(base / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 追加できない時はNoneを返す
def send_Users(VRChat_uid: str) -> int | None:
    # VRChat_uidが既に存在するか確認
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/Users?VRChat_uid=eq.{VRChat_uid}&select=transformed_uid",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req) as res:
        existing_user = json.loads(res.read())
    if existing_user:
        print("既に登録されています。")
        return existing_user[0]["transformed_uid"]
    
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/Users?select=transformed_uid",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req) as res:
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
        f"{SUPABASE_URL}/rest/v1/Users",
        data=Users_data,
        headers={
            "Content-Type": "application/json",
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as res:
        print(f"ユーザー登録: {res.status}")
        return transformed_uid

def get_transformed_uid(VRChat_uid: str) -> int:
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/Users?VRChat_uid=eq.{VRChat_uid}&select=transformed_uid",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read())
        if data:
            return data[0]["transformed_uid"]
        # なければ新規登録
        return send_Users(VRChat_uid)
    except Exception as e:
        print(f"transformed_uid取得エラー: {e}")
        return None

def send_ToNRoundStatistics(round_name: str, terror_ids: list[int], map_id: int, transformed_uid: int):
    def _send_ToNRoundStatistics():
        try:
            data = json.dumps({
                "round": round_name,
                "terror_ids": terror_ids,
                "map_id": map_id,
                "transformed_uid": transformed_uid
            }).encode()
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/ToNRoundStatistics",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                },
                method="POST"
            )
            with urllib.request.urlopen(req) as res:
                print(f"Supabase登録: {res.status}")
        except urllib.error.HTTPError as e:
            print(f"HTTPエラー: {e.code} {e.read()}")
        except Exception as e:
            print(f"送信エラー: {e}")
            
    threading.Thread(target=_send_ToNRoundStatistics, daemon=True).start()

# どうせ全部取り出すことになるので、全部取り出す仕様
def get_ToNRoundStatistics():
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/ToNRoundStatistics?select=*",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data
