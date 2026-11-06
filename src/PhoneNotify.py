"""スマホ通知（ntfy）。通知の文面を作る関数と、送る send()。

送り先は https://ntfy.sh/ へ JSON を POST する（{"topic","title","message","priority"}）。宛先の topic を
URL に入れず本文に入れるので、送信の失敗の文（例外の文字）に topic は出ない。

守ること:
- 送信は裏のスレッド（daemon）で行う。判定を待たせない・止めない。失敗してもログに出すだけで、やり直さない
- プレイヤーの名前・インスタンスの ID・ワールドの ID・グループの ID は文面に入れない
- 宛先は秘密。debug.log・不具合の報告に書かない（register_topic で DebugLog に覚えさせる）
- User-Agent は誰が送っても同じ文字（利用者の情報を入れない）
"""
import json
import re
import secrets
import urllib.request

import config
import DebugLog
import TaskStats

KIND_CONTINUE = "continue"              # 続行ラウンド
KIND_ALL_CONTINUE = "all_continue"      # 全続行（Run を含む）

# ntfy の topic に使える文字。長さは 1〜PHONE_NOTIFY_TOPIC_MAX_LEN
_RE_TOPIC = re.compile(r"[-_A-Za-z0-9]{1,%d}" % config.PHONE_NOTIFY_TOPIC_MAX_LEN)
_RE_MAP_ID = re.compile(r"\s*\(\d+\)\s*$")
USER_AGENT = "ToNAutoBeginner"
# 通知の見出しのうち固定のもの（待機列・テスト送信は、本文に文を入れる）
APP_TITLE = "ToNAutoBeginner"
TEST_MESSAGE = "テスト送信です"

_KIND_TITLES = {KIND_CONTINUE: "続行ラウンド", KIND_ALL_CONTINUE: "全続行"}


# ── 宛先（topic）──────────────────────────
def new_topic() -> str:
    """推測しにくい宛先を作る（secrets。文字は [-_A-Za-z0-9]・64 文字以内）"""
    return config.PHONE_NOTIFY_TOPIC_PREFIX + secrets.token_urlsafe(config.PHONE_NOTIFY_TOPIC_BYTES)


def is_valid_topic(topic) -> bool:
    return isinstance(topic, str) and _RE_TOPIC.fullmatch(topic) is not None


def register_topic(topic):
    """宛先を debug.log・不具合の報告に書かない（どの行でも *** になる）"""
    if is_valid_topic(topic):
        DebugLog.add_secret(topic)


# ── 文面 ───────────────────────────────
def map_name_from_raw(raw_map: str) -> str:
    """ラウンド開始の行のマップ（`Sewers (12)`）から、番号を除いた名前（`Sewers`）"""
    return _RE_MAP_ID.sub("", raw_map or "").strip()


def round_text(kind: str, window_idx: int, round_type: str, map_name: str, terror_text: str) -> tuple:
    """続行・全続行の通知 (見出し, 本文)。本文は `ラウンド / マップ` と `テラー: 名前` の 2 行。
    terror_text が空ならテラー不明"""
    title = f"窓{window_idx} {_KIND_TITLES[kind]}"
    where = f"{round_type} / {map_name}" if map_name else f"{round_type}"
    return title, f"{where}\nテラー: {terror_text or '不明'}"


def queue_ready_text() -> tuple:
    """待機列に入れるようになった通知 (見出し, 本文)。待機列はアカウントのものなので窓の番号は入れない"""
    return APP_TITLE, "待機列に入れるようになりました"


# ── 送信 ───────────────────────────────
def build_body(topic: str, title: str, message: str) -> bytes:
    return json.dumps({"topic": topic, "title": title, "message": message,
                       "priority": config.PHONE_NOTIFY_PRIORITY},
                      ensure_ascii=False).encode("utf-8")


def send(topic: str, title: str, message: str, on_done=None):
    """裏のスレッド（daemon）で送る。送ったスレッドを返す（テストが待つため。呼び出し側は待たない）。
    on_done(ok: bool, reason: str) は終わったときにそのスレッドから 1 回呼ぶ。失敗の reason は短い文
    （宛先・URL を含まない）。呼び出し側が例外を出しても送信側は気にしない"""
    register_topic(topic)
    thread = TaskStats.thread("PhoneNotify.send", _send_now, topic, title, message, on_done, daemon=True)
    thread.start()
    return thread


def _send_now(topic, title, message, on_done):
    ok, reason = True, ""
    try:
        request = urllib.request.Request(
            config.PHONE_NOTIFY_URL, data=build_body(topic, title, message), method="POST",
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=config.PHONE_NOTIFY_TIMEOUT_SEC):
            pass
    except Exception as e:
        ok, reason = False, DebugLog.scrub(f"{type(e).__name__}: {e}")
    if on_done is not None:
        try:
            on_done(ok, reason)
        except Exception:
            DebugLog.exception("PhoneNotify.send")
