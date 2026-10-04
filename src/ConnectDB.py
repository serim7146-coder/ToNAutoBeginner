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


def _register_env_secrets():
    """.env の値（Supabase の鍵・Discord の Webhook の URL など）を debug.log に書かない"""
    for env_path in env_file_candidates():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            DebugLog.add_secret(line.split("=", 1)[1].strip().strip("'\""))


_register_env_secrets()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DebugLog.add_secret(SUPABASE_URL)
DebugLog.add_secret(SUPABASE_KEY)
REQUEST_TIMEOUT = 10
DEFAULT_EXCLUDED_STAT_ROUNDS = ("Classic", "Run")

def _say_user(message: str):
    """ユーザー登録・transformed_uid の取得の成否（送ったラウンドの中身は含まない）。
    debug.log にも書く。register_round の送信まわりは使わない（中身・結果は書かない）"""
    print(message)
    DebugLog.write(f"[DB] {message}")


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

# DB の関数（supabase/get_transformed_uid.sql）。uid → transformed_uid を返し、無ければ割り当てる
TRANSFORMED_UID_RPC = "rpc/get_transformed_uid"


def send_Users(VRChat_uid: str) -> int | None:
    """VRChat の uid → transformed_uid（int2。DB を軽くするための番号）。無ければ DB 側で割り当てる。

    DB の関数 get_transformed_uid を呼ぶ。Users の表は直接読まない——読めると、鍵（exe から
    取り出せる）を持つ誰でも全員の uid と番号の対応を引けてしまう。関数がまだ DB に無い
    （404）ときだけ前のやり方へ落ちる。割り当てられないときは None
    """
    if not _configured():
        _say_user("Supabase設定がないためユーザー登録をスキップします。")
        return None
    req = urllib.request.Request(
        _url(TRANSFORMED_UID_RPC),
        data=json.dumps({"p_vrchat_uid": VRChat_uid}).encode(),
        headers={"Content-Type": "application/json", **_headers(accept=True)},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
            value = json.loads(res.read())
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        _say_user("DB に get_transformed_uid がありません → 前のやり方で登録します")
        return _send_Users_direct(VRChat_uid)
    if value is None:
        _say_user("transformed_uid を割り当てられませんでした")
        return None
    return int(value)


def get_transformed_uid(VRChat_uid: str) -> int | None:
    """send_Users() と同じ（無ければ割り当てる）。失敗は None"""
    if not _configured():
        _say_user("Supabase設定がないためtransformed_uid取得をスキップします。")
        return None
    try:
        return send_Users(VRChat_uid)
    except Exception as e:
        _say_user(f"transformed_uid取得エラー: {e}")
        return None


def _send_Users_direct(VRChat_uid: str) -> int | None:
    """前のやり方（Users を直接読んで、空いている番号を選んで書く）。DB に関数
    get_transformed_uid がまだ無いときだけ使う。関数を入れて Users を閉じたら消す"""
    uid = urllib.parse.quote(VRChat_uid, safe="")
    # VRChat_uidが既に存在するか確認
    req = urllib.request.Request(
        _url(f"Users?VRChat_uid=eq.{uid}&select=transformed_uid"),
        headers=_headers(accept=True)
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
        existing_user = json.loads(res.read())
    if existing_user:
        _say_user("既に登録されています。")
        return existing_user[0]["transformed_uid"]
    
    req = urllib.request.Request(
        _url("Users?select=transformed_uid"),
        headers=_headers(accept=True)
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
        existing = {row["transformed_uid"] for row in json.loads(res.read())} # 既存のtransformed_uidを取得
    if len(existing) >= 65534:
        _say_user("これ以上transformed_uidを追加できません。")
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
        _say_user(f"ユーザー登録: {res.status}")
        return transformed_uid


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


def fetch_rounds(since: int | None, my_uids, page_size: int = 1000) -> list[dict]:
    """"ToNRounds" の行を time の昇順で取る（統計画面 v1 の差分取り）。

    since があれば time >= since。my_uids があれば「1人目が自分でない行」か「1人目が
    自分でも other_uids がある行」だけ（自分が送った行は手元にあるので取らない）。
    ページ送りは time の昇順＋件数（offset は途中で行が増えると取りこぼすので使わない）。
    同じ time の行はページの境目で取り直すことがあるが、手元の一意で上書きするので
    二重にならない。送らない設定・通信の失敗は例外（呼び出し側が手元の分で表示する）
    """
    if not _configured():
        raise RuntimeError("Supabase設定がありません")
    uid_filter = ""
    if my_uids:
        listed = ",".join(str(int(u)) for u in sorted(my_uids))
        uid_filter = f"&or=(transformed_uid.not.in.({listed}),other_uids.not.is.null)"
    rows: list[dict] = []
    cursor = since
    while True:
        time_filter = f"&time=gte.{int(cursor)}" if cursor is not None else ""
        req = urllib.request.Request(
            _url(f"ToNRounds?select=*&order=time.asc{time_filter}{uid_filter}&limit={page_size}"),
            headers=_headers(accept=True))
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
            data = json.loads(res.read().decode("utf-8"))
        rows.extend(data)
        if len(data) < page_size:
            return rows
        last = int(data[-1]["time"])
        # 1ページ全部が同じ time なら先へ進める（同じ所を取り続けない）
        cursor = last + 1 if cursor is not None and last == int(cursor) else last


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
