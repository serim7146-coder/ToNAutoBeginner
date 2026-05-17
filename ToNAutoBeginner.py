"""
Compile
python -m nuitka ToNAutoBeginner.py --onefile --windows-console-mode=disable --output-filename=ToNAutoBeginner.exe --include-module=win32gui --include-module=win32con --include-module=win32api --include-module=pydirectinput --include-module=keyboard --enable-plugin=tk-inter --lto=yes --clang --follow-imports
"""
"""
github実行手順
git add .
git config --global user.email "serim7146@gmail.com"
git config --global user.name "serim7146-coder"
git commit -m "変更内容"
git push origin main

git pull origin main
"""

import json
import os
import re
import time
import glob
import threading
import tkinter as tk
import keyboard
import pydirectinput
import win32gui, win32con
import urllib.request
from tkinter import ttk, scrolledtext, filedialog, messagebox
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ═══════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════
pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.05

VRCHAT_LOG_DIR = (
    Path(os.environ.get("APPDATA", "~")) / ".." / "LocalLow" / "VRChat" / "VRChat"
).resolve()

VRCHAT_WINDOW_CLASS  = "UnityWndClass"
LOG_POLL_INTERVAL    = 0.3
SELF_DESTRUCT_KEY    = "^"  # デフォルト値（GUIで変更可能）

# ── 自動Begin ──

BEGIN_WAIT_SEC        = 0.0    # ラウンド終了後Beginまでの待機
BEGIN_FORWARD_SEC     = 2.1    # Begin前の前進時間
BEGIN_LEFT_SEC        = 0.11   # Begin前の左移動時間

BEGIN_RETRY_LEFT_SEC  = 0.05   # Beginリトライ時の左移動時間
BEGIN_RETRY_RIGHT_SEC = 0.11   # Beginリトライ時の右移動時間
BEGIN_RETRY_WAIT_SEC  = 2.0    # リトライの間隔
BEGIN_RETRY_MAX       = 4      # リトライ回数（初回Beginは含まない）

# ── 自動自爆 ──

DESTRUCT_HOLD_SEC    = 3.0     # 自爆ボタンを押す時間

# ── アイテムロストが発生するラウンド ──
ITEM_LOSS_ROUNDS = {
    "Randomizer",
    "Punished",
    "8 Pages",
    "Run",
}

INSTANT_ROUND_TYPES = {"Run"}

# ── インスタンス管理 ──
HOSHIIMO_GROUP_ID = "grp_0821983a-f7ab-4252-9895-0fe2712026a9"
CBPS_GROUP_ID     = "" # 後ほど埋めます。

# ── グループインスタンスで、自動自爆するラウンド ──
HOSHIIMO_SKIP_ROUNDS = {
    "Classic",
    "Classic.exe",
    "Bloodbath",
    "Randomizer",
}
CBPS_SKIP_ROUNDS = {
    "Classic",
    "Bloodbath",
    "Double Trouble",
    "Bloodbath EX"
    "Randomizer",
    "Punish",
    "Sabotage",
}

# ── インスタンスタイプ定数 ──
INSTANCE_PUBLIC        = "public"
INSTANCE_PRIVATE       = "private"
INSTANCE_HOSHIIMO      = "hoshiimo"
INSTANCE_CBPS          = "cbps"
INSTANCE_OTHER_GROUP   = "other_group"

# ── インスタンスタイプ(初期はパブリックを仮定) ──
_CURRENT_INSTANCE_TYPE = INSTANCE_PUBLIC
_INSTANCE_LOCK = threading.Lock()

def get_instance_type() -> str:
    with _INSTANCE_LOCK:
        return _CURRENT_INSTANCE_TYPE

def set_instance_type(t: str):
    global _CURRENT_INSTANCE_TYPE
    with _INSTANCE_LOCK:
        _CURRENT_INSTANCE_TYPE = t

# ラウンド開始時点で即続行確定・他窓フリーズ開始するラウンド
# （Killers行を待たずにtaking place行で確定）
INSTANT_CONTINUE_TYPES = {
    "Fog",             # 霧（テラー不問で続行）
    "Fog (Alternate)", # 霧Alternate（テラー不問で続行）
}

# Alternate枠テラーのオフセット設定
# ログID 0〜35 のテラーは Alternate枠 → +134 してtnlスロットIDに変換する
# tnlのAlternateスロットは134〜169として登録されているため
ALTERNATE_OFFSET  = 134
ALTERNATE_LOG_MAX = 35   # ログIDが0〜35 = Alternate枠テラー

# ラウンドタイプごとの「何番目の枠がAlternate枠か（0始まり）」
# None = 全枠がAlternate（Alternateラウンド単体）
# キー = ログの "Round type is XXX" の XXX 部分
ALTERNATE_SLOT_POSITIONS: dict[str, list[int] | None] = {
    "Alternate":           None,  # 全枠Alternate → +134
    "Midnight":            [2],   # 3枠目のみAlternate → +134
    "Fog (Alternate)":     None,  # 全枠Alternate → +134、tnl照合はFog
    "Ghost (Alternate)":   None,  # 全枠Alternate → +134、tnl照合はGhost
    # 通常の Fog/Ghost/8Pages はClassicテラーのみ → オフセット不要
}

# ── 特殊ラウンド ──
SPECIAL_ROUND_TNL_KEYS = {
    "Classic.exe",
    "Randomizer",
    "8 Pages",
    "Fog",
    "Ghost",
    "Punished",
    "Sabotage",
    "Bloodbath",
    "Double Trouble",
    "Bloodbath EX",
    "Cracked",
    "Alternate",
    "Midnight",
    "Unbound",
    "Mystic Moon",
    "Blood Moon",
    "Twilight",
    "Solstice",
    "Fog (Alternate)",
    "Ghost (Alternate)",
    "Sabotage star",
    "Sabotage murder",
    "Special",
}

REPLACEMENT_ROUND_TNL_KEYS = {
    "Classic.exe/Classic.exe",
    "Randomizer/Randomizer",
    "Fog (Alternate)/霧 (Alternate)",
    "Ghost (Alternate)/ゴースト (Alternate)",
    "Bloodbath EX/ブラッドバスEX",
    "Special/Moon",
}

# ── 音声アナウンスファイルパス ──
VOICE_CONTINUE     = "voice/Continue.mp3"    # 続行ラウンド用
VOICE_FOG          = "voice/Fog.mp3"   # 霧ラウンド用
VOICE_ITEM_LOST    = "voice/ItemLost.mp3"  # アイテムロスト時
VOICE_INTERMISSION = "voice/intermission.mp3" #intermission突入時
VOICE_FOXY         = "voice/foxy.mp3" # Foxyが出現したとき

# ── 3クラ続行設定 ──────────────────────────────
# DTM / Waldo のテラーID（GUIから設定可能、不明な場合は0のまま）
# ログに "Killers have been set - X 0 0 // Round type is ..." が出たときのXがID
DontTouchMe = 50
Waldo = 131
OpenSpecialRound_TERROR_IDS: set[int] = {DontTouchMe, Waldo}

OpenSpecialRound_TARGET_WINS  = 3       # 何勝したらジャンプ停止するか（窓ごと）
OpenSpecialRound_INTERVAL_SEC = 60.0    # AFK回避の移動の間隔（秒）

# 生存/死亡ログ
RE_LIVED = re.compile(r"^Lived in round[.]$")
RE_YOU_DIED = re.compile(r"^You died[.]$")  # 自爆成功検出

# ── 多窓排他制御 ──────────────────────────────
# 全窓で共有するロック。キー入力・マウス操作は必ずこのロックを取ってから実行する。
# これにより「窓Aが自爆中に窓Bが割り込む」ことを防ぐ。
_GLOBAL_ACTION_LOCK = threading.Lock()

# 自爆キー（GUIから変更可能）
_DESTRUCT_KEY = SELF_DESTRUCT_KEY
_DESTRUCT_KEY_LOCK = threading.Lock()

def get_destruct_key() -> str:
    with _DESTRUCT_KEY_LOCK:
        return _DESTRUCT_KEY

def set_destruct_key(key: str):
    global _DESTRUCT_KEY
    with _DESTRUCT_KEY_LOCK:
        _DESTRUCT_KEY = key

# 装備待ちイベント（アイテムロストラウンド後のBegin押下から装備まで全窓フリーズ）
# set() 状態 = 通常動作可能、clear() 状態 = 装備待ち中（他窓のアクションをブロック）
_EQUIP_WAIT_EVENT = threading.Event()
_EQUIP_WAIT_EVENT.set()   # 初期値は通常動作可能

_ITEM_GET_BEGIN_MODE = False
_ITEM_GET_BEGIN_LOCK = threading.Lock()

def get_item_get_begin_mode() -> bool:
    with _ITEM_GET_BEGIN_LOCK:
        return _ITEM_GET_BEGIN_MODE

def set_item_get_begin_mode(val: bool):
    global _ITEM_GET_BEGIN_MODE
    with _ITEM_GET_BEGIN_LOCK:
        _ITEM_GET_BEGIN_MODE = val

_HANDS_FREE = False
_HANDS_FREE_LOCK = threading.Lock()

def get_hands_free() -> bool:
    with _HANDS_FREE_LOCK:
        return _HANDS_FREE

def set_hands_free(val: bool):
    global _HANDS_FREE
    with _HANDS_FREE_LOCK:
        _HANDS_FREE = val

# 続行・霧ラウンド中フリーズイベント
# set() = 通常動作可能、clear() = 続行/霧ラウンド中（他窓をブロック）
_CONTINUE_ROUND_EVENT = threading.Event()
_CONTINUE_ROUND_EVENT.set()   # 初期値は通常動作可能
_CONTINUE_ROUND_COUNT = 0     # 続行ラウンド中の窓数カウンター
_CONTINUE_ROUND_LOCK  = threading.Lock()  # カウンター操作用ロック

def _continue_round_start():
    """続行ラウンド開始：カウンターを増やしてフリーズ"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT += 1 # 続行が二つ来た時、片方が死んだらフリーズ解除される現象があり、それを回避するため
        _CONTINUE_ROUND_EVENT.clear()

def _continue_round_end():
    """続行ラウンド終了：カウンターを減らし、0になったらフリーズ解除"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT = max(0, _CONTINUE_ROUND_COUNT - 1)
        if _CONTINUE_ROUND_COUNT == 0:
            _CONTINUE_ROUND_EVENT.set()

def _continue_round_reset():
    """停止時など強制リセット"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT = 0
        _CONTINUE_ROUND_EVENT.set()

# ウィンドウフォーカス切り替え後の安定待機
FOCUS_WAIT_SEC = 0.3


# ═══════════════════════════════════════════════
#  ログ正規表現
# ═══════════════════════════════════════════════
RE_ROUND_START      = re.compile(r"This round is taking place at (.+) and the round type is (.+)") # ラウンド突入(180s段階)
RE_MAP_ID           = re.compile(r"\((\d+)\)$")   # マップ名からID抽出
RE_KILLERS_SET      = re.compile(r"Killers have been set - (\d+) (\d+) (\d+) // Round type is (.+)") # テラー判明ログ
RE_KILLERS_UNKNOWN  = re.compile(r"Killers is unknown - \?\?\? // .+ // Round type is (.+)") # 霧ラウンド時に最初のログ
RE_KILLERS_REVEALED = re.compile(r"Killers have been revealed - (\d+) (\d+) (\d+) // Round type is (.+)") # 霧ラウンド時のテラー判明時のログ
RE_FOXY             = re.compile(r"foxy the pirate turned evil!", re.IGNORECASE)  # Foxyの出現ログ
RE_JOINING          = re.compile(r"\[Behaviour\] Joining (wrld_[^:]+):\d+(.*?)(?:~region\(|$)") # インスタンスタイプを取得するためのJoinログ
RE_ROUND_OVER       = re.compile(r"^RoundOver$") # ラウンド終了
RE_VERIFIED_END     = re.compile(r"^Verified Round End$") # intermission突入
RE_BEGIN_DONE       = re.compile(r"^Verified$") # connecting突入
RE_ITEM_EQUIP       = re.compile(r"^Equipping (\d+)[.]")   # アイテム装備検出

RE_LOG_PREFIX       = re.compile(r"^\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+\s+-\s+")


# ═══════════════════════════════════════════════
#  ラウンドタイプ → TNLキー
# ═══════════════════════════════════════════════
LOG_TO_TNL = {
    "Classic":           "Classic/クラシック",
    "Classic.exe":       "Classic.exe/Classic.exe",
    "Randomizer":        "Randomizer/Randomizer",
    "8 Pages":           "8 Pages/8ページ",
    "Fog":               "Fog/霧",
    "Ghost":             "Ghost/ゴースト",
    "Punished":          "Punished/パニッシュ",
    "Sabotage":          "Sabotage/サボタージュ",
    "Bloodbath":         "Bloodbath/ブラッドバス",
    "Double Trouble":    "Double Trouble/ダブルトラブル",
    "Bloodbath EX":      "Bloodbath EX/ブラッドバスEX",
    "Cracked":           "Cracked/狂気",
    "Alternate":         "Alternate/オルタネイト",
    "Midnight":          "Midnight/ミッドナイト",
    "Unbound":           "Unbound/アンバウンド",
    "Run":               "Run/走れ！",
    "Mystic Moon":       "Mystic Moon/ミスティックムーン",
    "Blood Moon":        "Blood Moon/ブラッドムーン",
    "Twilight":          "Twilight/トワイライト",
    "Solstice":          "Solstice/ソルスティス",
    "Fog (Alternate)":   "Fog/霧",             # tnl照合は通常Fogスロット
    "Ghost (Alternate)": "Ghost/ゴースト",     # tnl照合は通常Ghostスロット
    "Sabotage star":     "Sabotage star/サボタージュスター",
    "Sabotage murder":   "Sabotage murder/サボタージュマーダー",
    "Special":           "Special/Moon",
    "Moon":              "Special/Moon",
}


# ═══════════════════════════════════════════════
#  TNLデータ
# ═══════════════════════════════════════════════
def load_tnl(path: str) -> tuple[dict[str, set[int]], dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data", {})
    keepOn_set: dict[str, set[int]] = {}
    for round_key, slots in data.items():
        if not isinstance(slots, dict):
            continue
        ids = {int(k) for k, v in slots.items() if isinstance(v, int) and v != 0}
        if ids:
            keepOn_set[round_key] = ids
    meta = {k: raw.get(k, "") for k in ("list_name", "creator", "created_at")}
    return keepOn_set, meta


def should_continue(keepOn_set: dict, tnl_key: str, terror_ids: list[int]) -> bool:
    if tnl_key not in keepOn_set:
        return False
    return any(t in keepOn_set[tnl_key] for t in terror_ids)


def parse_terror_ids(a: str, b: str, c: str, round_type: str = "") -> list[int]:
    ids = [int(x) for x in (a, b, c)]
    if round_type in ("Midnight", "Bloodbath"):
        return ids[:3]
    elif round_type in ("Double Trouble", "8 Pages"):
        return ids[:2]
    else:
        return ids[:1]


# April Fool期間中の特例: Alternate ID→tnlID の特別マッピング
# キー=ログに出るID(オフセット適用後), 値=tnlでの実際のID
ALTERNATE_ID_OVERRIDE: dict[int, int] = {
    136: 316,   # April Fool: Alternate ログID2(+134=136) → tnl316
}

# Unboundラウンドのオフセット: ログID + 200 = tnlスロットID
UNBOUND_OFFSET = 200


def apply_alternate_offset(ids: list[int], round_type: str) -> list[int]:
    """
    Alternate枠テラーのログID（0〜35）を +134 してtnlスロットIDに変換する。
    round_type = ログの "Round type is XXX" の XXX 部分
    - Midnight: 3枠目（index=2）のみAlternate
    - Alternate/Fog(Alternate)/Ghost(Alternate)/8Pages(Alternate): 全枠Alternate
    - その他（通常8Pages/Fog/Ghost等）: オフセットなし
    """
    if round_type not in ALTERNATE_SLOT_POSITIONS:
        return ids
    positions = ALTERNATE_SLOT_POSITIONS[round_type]  # None=全枠, list=指定枠のみ
    result = []
    for i, tid in enumerate(ids):
        is_alt_slot = (positions is None) or (i in positions)
        if is_alt_slot and 0 <= tid <= ALTERNATE_LOG_MAX:
            converted = tid + ALTERNATE_OFFSET
            # April Fool等の特例マッピング
            result.append(ALTERNATE_ID_OVERRIDE.get(converted, converted))
        else:
            result.append(tid)
    return result


# ═══════════════════════════════════════════════
#  ウィンドウ操作
# ═══════════════════════════════════════════════
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
        if win32gui.GetClassName(h) == VRCHAT_WINDOW_CLASS:
            hwnds.insert(0, h)
            found_hwnd += 1
    if found_hwnd >= hwnd_count:
        return hwnds
    win32gui.EnumWindows(cb, None)
    return hwnds


def focus_window(hwnd: int):
    """指定ウィンドウをフォアグラウンドに持ってくる（サイズは変えない）"""
    if hwnd == 0:
        return
    try:
        # SW_RESTORE はサイズを変えてしまうので、最小化時だけ使う
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(FOCUS_WAIT_SEC)
    except Exception:
        pass


# 音量（0.0〜1.0）
_VOICE_VOLUME = 1.0
_VOICE_VOLUME_LOCK = threading.Lock()

def get_voice_volume() -> float:
    with _VOICE_VOLUME_LOCK:
        return _VOICE_VOLUME

def set_voice_volume(v: float):
    global _VOICE_VOLUME
    with _VOICE_VOLUME_LOCK:
        _VOICE_VOLUME = max(0.0, min(1.0, v))


def play_voice(path: str):
    """音声ファイルを非同期再生する（.wav/.mp3対応）"""
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    vol = int(get_voice_volume() * 100)
    def _play():
        try:
            import subprocess
            # PowerShellでSoundPlayerを使って再生（音量はWScriptで制御）
            script = (
                f'$vol = {vol};'
                f'$obj = New-Object -ComObject WScript.Shell;'
                f'Add-Type -AssemblyName System.Windows.Forms;'
                f'[System.Windows.Forms.SendKeys]::SendWait([char]0xAD) | Out-Null;'  # mute trick
                f'$p = New-Object Media.SoundPlayer \"{p}\";'
                f'$p.PlaySync();'
            )
            # シンプルにSoundPlayerで再生（音量はOS側のミキサーに依存）
            subprocess.Popen(
                ["powershell", "-c",
                 f'(New-Object Media.SoundPlayer "{p}").PlaySync()'],
                creationflags=0x08000000
            )
        except Exception:
            pass
    threading.Thread(target=_play, daemon=True).start()


CSV_LOG_PATH = Path("ToNAutoBeginner_rounds.csv")
CSV_LOCK = threading.Lock()
# 重複排除用: (ts_int, round, terror_str, map_id) の最近の記録
_CSV_RECENT: list[tuple] = []
_CSV_DEDUP_SEC = 2  # 同一インスタンスとみなす誤差秒数

def post_to_supabase(round_name: str, terror_ids: list[int], map_id: int):
    """ラウンド結果をSupabaseに送信する（非同期・重複排除付き）"""
    now = datetime.now()
    date = int(now.strftime("%Y%m%d"))
    time = int(now.strftime("%H%M%S"))
    key = (round_name, tuple(sorted(terror_ids)), map_id)
    now_ts = date * 1000000 + time

    with CSV_LOCK:
        for rec in _CSV_RECENT:
            if rec["key"] == key and abs(now_ts - rec["ts"]) <= 2:
                return
        if len(_CSV_RECENT) > 20:
            _CSV_RECENT.clear()
        _CSV_RECENT.append({"ts": now_ts, "key": key})

    def _send():
    try:
        data = json.dumps({...}).encode()
        req = urllib.request.Request(...)
        with urllib.request.urlopen(req) as res:
            print(f"Supabase応答: {res.status} {res.read()}")
    except urllib.error.HTTPError as e:
        print(f"HTTPエラー: {e.code} {e.read()}")
    except Exception as e:
        print(f"送信エラー: {e}")

    threading.Thread(target=_send, daemon=True).start()


def find_latest_logs(base_dir: Path, count: int = 4) -> list[Path]:
    """ファイル名から時刻を読み取り、最新count件を古い順で返す。
    ファイル名形式: output_log_YYYY-MM-DD_HH-MM-SS.txt
    窓1=最も古いログ、窓N=最も新しいログ の順に割り当てる。"""
    pattern = str(base_dir / "output_log_*.txt")
    files = glob.glob(pattern)

    def log_datetime(path: str) -> str:
        # ファイル名から日時部分を抽出してソートキーにする
        name = os.path.basename(path)
        # output_log_2026-05-09_00-35-09.txt → "2026-05-09_00-35-09"
        try:
            return name.replace("output_log_", "").replace(".txt", "")
        except Exception:
            return ""

    # ファイル名の日時で降順ソート（新しい順）→ 最新count件取得 → 昇順（古い順）に戻す
    sorted_desc = sorted(files, key=log_datetime, reverse=True)
    latest = sorted_desc[:count]
    return [Path(f) for f in sorted(latest, key=log_datetime, reverse=False)]


# ═══════════════════════════════════════════════
#  キー / マウス操作
# ═══════════════════════════════════════════════
def hold_key(key: str, sec: float):
    """キーを指定秒長押しする。"""
    if sec <= 0:
        return
    keyboard.press(key)
    time.sleep(sec)
    keyboard.release(key)
    time.sleep(0.05)

def click_at():
    """現在のマウス位置でクリック（pydirectinput使用）"""
    pydirectinput.mouseDown()
    time.sleep(0.1)
    pydirectinput.mouseUp()
    time.sleep(0.1)


# ═══════════════════════════════════════════════
#  窓ごとの設定
# ═══════════════════════════════════════════════
@dataclass
class WindowConfig:
    hwnd: int = 0
    log_path: Path = None
    active: bool = True
    auto_begin: bool = True
    do_skip: bool = True
    cancel_afk: bool = True      # DTM/Waldo続行（3クラまで）
    hoshiimo_skip: bool = False  # 干し芋自動自爆
    voice_intermission: str = "" # Intermission音声
    announce_intermission: bool = False  # Intermissionアナウンス
    respawn_btn_x: int = 960   # リスポーンボタン X（Runラウンド用）
    respawn_btn_y: int = 400   # リスポーンボタン Y（Runラウンド用）
    voice_continue: str = ""   # 続行ラウンド音声ファイルパス
    voice_fog: str = ""        # 霧ラウンド音声ファイルパス
    voice_item_lost: str = ""  # アイテムロスト音声ファイルパス


# ═══════════════════════════════════════════════
#  1窓の実行状態
# ═══════════════════════════════════════════════
@dataclass
class WindowState:
    log_pos: int = 0
    # Round情報
    in_round: bool = False
    round_type: str = ""
    terror_ids: list = field(default_factory=list)
    map_id: int = 0                    # マップID（括弧内の数字）
    # 自爆関係
    fog: bool = False
    is_continue_round: bool = False  # 続行/霧ラウンド中（他窓フリーズ中）
    _skip_time: float = 0.0        # 自爆実行時刻（RoundOver判定用）
    begin_done: bool = False   # Beginが正常に押されたか(Connecting)
    # 3クラ開け
    is_OpenSpecialRound_round: bool = False     # 現在のラウンドが特殊ラウンドを開けるラウンドかどうか
    OpenSpecialRound_wins: int = 0              # 窓ごとの勝利数
    # アイテム関係
    item_id: int = 1             # 何のアイテムを所持しているか(未所持は0となる)
    waiting_for_equip: bool = False  # アイテム装備待ち（操作権限を譲渡中）


# ═══════════════════════════════════════════════
#  ログ監視ワーカー
# ═══════════════════════════════════════════════
class LogMonitor:
    def __init__(self, cfg: WindowConfig, keepOn_set: dict, logger, window_idx: int = 0):
        self.cfg = cfg
        self.keepOn_set = keepOn_set
        self.logger = logger
        self.window_idx = window_idx
        self.st = WindowState()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        # 過去ログからインスタンスタイプを検出（ワールド入室後の起動に対応）
        self._detect_instance_from_log()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _detect_instance_from_log(self):
        """ログファイルを末尾から遡り、最後のJoining行からインスタンスタイプを取得する"""
        if not self.cfg.log_path or not self.cfg.log_path.exists():
            return
        try:
            with open(self.cfg.log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            # 末尾から遡って最初に見つかったJoining行を使用
            for line in reversed(lines):
                line = RE_LOG_PREFIX.sub("", line).strip()
                m = RE_JOINING.search(line)
                if m:
                    suffix = m.group(2)
                    if f"group({HOSHIIMO_GROUP_ID})" in suffix:
                        itype = INSTANCE_HOSHIIMO
                    elif "~group(" in suffix:
                        itype = INSTANCE_OTHER_GROUP
                    elif "~friends" in suffix or "~hidden" in suffix or "~private" in suffix or "~private" in suffix:
                        itype = INSTANCE_PRIVATE
                    else:
                        itype = INSTANCE_PUBLIC
                    set_instance_type(itype)
                    self._log(f"インスタンスタイプ検出: {itype}")
                    return
        except Exception as e:
            self._log(f"インスタンス検出エラー: {e}")

    def stop(self):
        self._running = False

    def _log(self, msg: str):
        self.logger(f"[窓{self.window_idx}] {msg}")

    # ── メインループ ──────────────────────────
    def _run(self):
        cfg = self.cfg
        if not cfg.log_path or not cfg.log_path.exists():
            self._log(f"ログが見つかりません: {cfg.log_path}")
            return
        self.st.log_pos = cfg.log_path.stat().st_size
        self._log("監視開始")
        while self._running:
            try:
                with open(cfg.log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self.st.log_pos)
                    chunk = f.read()
                    self.st.log_pos = f.tell()
                if chunk:
                    for line in chunk.splitlines():
                        line = RE_LOG_PREFIX.sub("", line).strip()
                        self._process(line)
            except Exception as e:
                self._log(f"読み取りエラー: {e}")
            time.sleep(LOG_POLL_INTERVAL)

    # ── ログ行処理 ────────────────────────────
    def _process(self, line: str):
        st = self.st

        # アイテム装備検出（操作権限譲渡中の再開トリガー）
        m = RE_ITEM_EQUIP.match(line)
        if m:
            st.item_id = int(m.group(1))
            self._log(f"✅ アイテム装備 (id={st.item_id})")
            if st.waiting_for_equip:
                # 両条件（装備＋Begin）が揃ったら2秒後に解除
                if st.begin_done:
                    st.waiting_for_equip = False
                    def _delayed_release(self=self):
                        time.sleep(2.0)
                        _EQUIP_WAIT_EVENT.set()
                        self._log("✅ 全窓フリーズ解除")
                    threading.Thread(target=_delayed_release, daemon=True).start()
            return

        m = RE_ROUND_START.match(line)
        if m:
            st.in_round        = True
            st.round_type      = m.group(2).strip()
            st.terror_ids      = []
            st.map_id          = int(RE_MAP_ID.search(m.group(1).strip()).group(1))
            st.fog             = False
            st.begin_done      = False
            st.is_OpenSpecialRound_round   = False
            # アイテムロスト中にラウンドが始まったらフリーズ解除
            # （has_item=Falseのまま → 次のVerified Round Endで再フリーズ）
            if st.waiting_for_equip:
                st.waiting_for_equip = False
                _EQUIP_WAIT_EVENT.set()
                self._log("一時的にアイテムロストフリーズを解除")

            if st.round_type in INSTANT_ROUND_TYPES:
                # Runは死亡してアイテムロスト対応へ
                st.is_continue_round = False
                self._log(f"Round: {st.round_type} 【死亡待ち・アイテム購入予定】")
                return

            if st.round_type in INSTANT_CONTINUE_TYPES:
                if not get_hands_free():
                    # 霧系：taking place時点で即続行確定・他窓フリーズ開始
                    st.is_continue_round = True
                    _continue_round_start()
                    play_voice(self.cfg.voice_fog)
                    self._log(f"開始: {st.round_type} 【他窓フリーズ開始】")
                return
            
            else:
                self._log(f"開始: {st.round_type}")
            return

        m = RE_KILLERS_SET.match(line)
        if m:
            # 特殊ラウンドを経験したら3勝扱い（OpenSpecialRound_completed=True）
            if st.round_type in SPECIAL_ROUND_TNL_KEYS:
                st.OpenSpecialRound_wins = OpenSpecialRound_TARGET_WINS
            if not (st.round_type == "Alternate" and m.group(4).strip() == "Classic"): # AF期間中は極まれに偽Classicがある
                st.round_type = m.group(4).strip()
            self._on_killers(
                parse_terror_ids(m.group(1), m.group(2), m.group(3), st.round_type)
                , st.round_type, revealed=False
            )
            return

        # Fogラウンド突入
        if RE_KILLERS_UNKNOWN.match(line):
            st.fog  = True
            st.round_type = "fog"
            self._log(f"テラー不明 → revealed待ち")
            if get_hands_free():
                self._log(f"開始: {st.round_type} 【放置モード→即自爆】")
                threading.Thread(target=self._do_skip, daemon=True).start()
            return

        if RE_FOXY.search(line):
            # 「foxy the pirate turned evil!」→ Alternate ID2（+134=136）確定
            self._log("🦊 Foxyが出た！")
            # ここにフォクシーの出現の音声を追加する
            
            # もし霧なら自爆するか判定する(他はRE_KILLERS_SETから行う)
            if st.round_type == "fog":
                self._on_killers([2], st.round_type, revealed=True)
            return

        m = RE_KILLERS_REVEALED.match(line)
        if m:
            self._on_killers(
                parse_terror_ids(m.group(1), m.group(2), m.group(3), m.group(4).strip()),
                m.group(4).strip(), revealed=True)
            return

        m = RE_JOINING.search(line)
        if m:
            suffix = m.group(2)
            if f"group({HOSHIIMO_GROUP_ID})" in suffix:
                itype = INSTANCE_HOSHIIMO
            elif "~group(" in suffix:
                itype = INSTANCE_OTHER_GROUP
            elif "~friends(hidden)~" in suffix or "~hidden(" in suffix or "~friends~" in suffix or "~private(" in suffix:
                itype = INSTANCE_PRIVATE
            else:
                itype = INSTANCE_PUBLIC
            set_instance_type(itype)
            self._log(f"インスタンスタイプ: {itype}")
            return

        if RE_ROUND_OVER.match(line):
            st.in_round = False
            # アイテムロスト音声: auto_beginなしの場合はRoundOverで流す
            if st.waiting_for_equip and not self.cfg.auto_begin:
                play_voice(self.cfg.voice_item_lost)
            return

        if RE_LIVED.match(line):
            if st.is_OpenSpecialRound_round:
                st.OpenSpecialRound_wins += 1
                self._log(f"生存数: {st.OpenSpecialRound_wins}/{OpenSpecialRound_TARGET_WINS}")
                if st.OpenSpecialRound_wins >= OpenSpecialRound_TARGET_WINS:
                    self._log("🎉 3勝達成！以降のDTM/Waldoラウンドはスキップします")
            st.is_OpenSpecialRound_round = False
            return

        if RE_YOU_DIED.match(line):
            if st._skip_time > 0 and (time.time() - st._skip_time) <= 3.0:
                self._log("✅ 自爆成功")
                st._skip_time = 0.0
            st.is_OpenSpecialRound_round = False
            return

        if RE_BEGIN_DONE.match(line):
            st.begin_done = True
            self._log("✅ Connecting")
            # アイテムロスト中のBegin確認
            if st.waiting_for_equip:
                if st.item_id:
                    st.waiting_for_equip = False
                    def _delayed_release(self=self):
                        time.sleep(2.0)
                        _EQUIP_WAIT_EVENT.set()
                        self._log("✅ 全窓フリーズ解除")
                    threading.Thread(target=_delayed_release, daemon=True).start()
            return

        if RE_VERIFIED_END.match(line):
            # 続行/霧ラウンドのフリーズ解除
            if st.is_continue_round:
                st.is_continue_round = False
                _continue_round_end()
                self._log("▶ 続行/霧ラウンド終了 → 他窓フリーズ解除")
            # アイテムロスト判定（放置モード中はフリーズしない）
            if not get_hands_free():
                if st.round_type in ITEM_LOSS_ROUNDS:
                    st.item_id = 0
                    if get_item_get_begin_mode():
                        # アイテム取得→Beginモード: ラウンド終了時点でフォーカス・全窓フリーズ
                        st.waiting_for_equip = True
                        _EQUIP_WAIT_EVENT.clear()
                        self._log("ラウンド終了 【⚠ アイテムロスト → フォーカス・全窓フリーズ開始】")
                        threading.Thread(target=lambda: focus_window(self.cfg.hwnd), daemon=True).start()
                    else:
                        st.waiting_for_equip = True
                        self._log("ラウンド終了 【⚠ アイテムロスト → Begin時にフリーズ開始】")
                elif not st.item_id:
                    st.waiting_for_equip = True
                    self._log("ラウンド終了 【⚠ アイテム未回収 → Begin時に再フリーズ】")
                else:
                    self._log("ラウンド終了")
            else:
                if st.round_type in ITEM_LOSS_ROUNDS:
                    st.item_id = 0
                self._log("ラウンド終了")
            # CSVにラウンド結果を記録（重複排除・ユーザーID付き）
            post_to_supabase(st.round_type, st.terror_ids, st.map_id)
            # Intermissionアナウンス
            if self.cfg.announce_intermission:
                play_voice(self.cfg.voice_intermission)
            # アイテムロスト音声はRoundOverで流す（auto_begin=Falseの場合）
            if self.cfg.auto_begin:
                threading.Thread(target=self._do_after_round, daemon=True).start()
            return

    # ── テラー確定処理 ────────────────────────
    def _on_killers(self, ids: list[int], round_type: str, revealed: bool):
        st = self.st
        st.fog = False

        # Alternate枠のオフセット補正（round_type で判定）
        ids = apply_alternate_offset(ids, round_type)

        # Unboundラウンドのオフセット補正: ログID + 200 = tnlID
        if st.round_type == "Unbound":
            ids = [tid + UNBOUND_OFFSET for tid in ids]

        # テラーIDを累積（複数回Killers行が来るラウンド対応）
        for tid in ids:
            if tid not in st.terror_ids:
                st.terror_ids.append(tid)
        # インスタンス制限チェック
        itype = get_instance_type()
        is_allowed = itype in INSTANCE_PRIVATE
        is_hoshiimo = itype == INSTANCE_HOSHIIMO

        # 干し芋グループ専用自動自爆
        if is_hoshiimo and self.cfg.hoshiimo_skip:
            if st.round_type in HOSHIIMO_SKIP_ROUNDS:
                self._log(f"干し芋自動自爆: {st.round_type}")
                if not st.is_continue_round:
                    threading.Thread(target=self._do_skip, daemon=True).start()
                return
            else:
                # 干し芋グループだがスキップ対象外→何もしない
                return

        # 通常機能はフレ/フレ+/招待/招待+のみ
        # ただし完全放置モードはインスタンス問わず動作
        if not is_allowed:
            self._log(f"インスタンス制限: 操作スキップ ({itype})")
            return

        # 放置モードの処理
        if get_hands_free():
            # 特殊ラウンド経験済み → 全ラウンド即自爆
            if st.OpenSpecialRound_wins >= OpenSpecialRound_TARGET_WINS:
                self._log(f"放置モード(3クラ済み): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    threading.Thread(target=self._do_skip, daemon=True).start()
                return
            # アイテムなし → DTMのみ続行、Waldo含むそれ以外は自爆
            if not st.item_id:
                # DTM(50)はアイテム不要なので続行可、Waldo(131)はアイテム必要なので自爆
                DTM_ONLY_IDS = {50}
                has_dtm = bool(
                    self.cfg.cancel_afk and
                    any(t in DTM_ONLY_IDS for t in st.terror_ids)
                )
                if not has_dtm:
                    self._log(f"放置モード(アイテムなし・DTMなし): 即自爆 {st.terror_ids} / {st.round_type}")
                    if not st.is_continue_round and self.cfg.do_skip:
                        threading.Thread(target=self._do_skip, daemon=True).start()
                    return
                # DTMありなので通常判定へ fall through
            # アイテムあり・特殊ラウンド未経験 → DTM/Waldoのみ続行、他は即自爆
            has_cancel_afk = bool(
                OpenSpecialRound_TERROR_IDS and
                any(t in OpenSpecialRound_TERROR_IDS for t in st.terror_ids) and
                self.cfg.cancel_afk
            )
            if not has_cancel_afk:
                self._log(f"放置モード(DTM/Waldo以外): 即自爆 {st.terror_ids} / {st.round_type}")
                if not st.is_continue_round and self.cfg.do_skip:
                    threading.Thread(target=self._do_skip, daemon=True).start()
                return
            # DTM/Waldobなので通常判定へ（is_OpenSpecialRound_target で続行）

        # 累積テラーIDで判定（複数体ラウンド対応）
        all_ids = st.terror_ids  # すでに累積済み
        is_special_round = st.round_type in SPECIAL_ROUND_TNL_KEYS

        is_OpenSpecialRound_target = (
            bool(all_ids and OpenSpecialRound_TERROR_IDS and
                 any(t in OpenSpecialRound_TERROR_IDS for t in all_ids))
            and not is_special_round
            and st.OpenSpecialRound_wins < OpenSpecialRound_TARGET_WINS
            and self.cfg.cancel_afk   # 窓ごとの設定
        )

        # 続行判定: 1体でも続行希望があれば続行
        # 特殊ラウンド中・3勝後のDTM/WaldoはtnlのみでkeepOn判定
        st.is_continue_round = should_continue(self.keepOn_set, LOG_TO_TNL.get(round_type, round_type), all_ids) or is_OpenSpecialRound_target

        verb = "revealed" if revealed else "set"
        if is_OpenSpecialRound_target:
            tag = "【プレイ(DTM/Waldo)】"
        else:
            tag = "【プレイ】" if st.is_continue_round else "【スキップ】"
        self._log(f"テラー{verb}: {all_ids} / {round_type} {tag}")

        if st.is_continue_round:
            # 続行ラウンドの音声アナウンス＋他窓フリーズ（DTM/Waldo以外）
            if not is_OpenSpecialRound_target:
                play_voice(self.cfg.voice_continue)
                self._log("🎙 続行アナウンス再生")
                _continue_round_start()
                self._log("⏸ 続行/霧ラウンド中 → 他窓フリーズ開始")
            # 3クラ開け続行開始
            if is_OpenSpecialRound_target and st.OpenSpecialRound_wins < OpenSpecialRound_TARGET_WINS:
                st.is_OpenSpecialRound_round = True
                self._log(f"3クラ解放ラウンド開始（勝利数: {st.OpenSpecialRound_wins}/{OpenSpecialRound_TARGET_WINS}）")
                t = threading.Thread(target=self._do_OpenSpecialRound_loop, daemon=True)
                t.start()
            elif is_OpenSpecialRound_target:
                self._log("DTM/Waldoラウンドだが3勝達成済み→AFK解除なし")
        else:
            # 全テラーがスキップ対象 → 自爆（まだ自爆していなければ）
            if self.cfg.do_skip and not st.is_continue_round:
                threading.Thread(target=self._do_skip, daemon=True).start()

    # ═════════════════════════════════════════
    #  アクション（全てグローバルロックで排他）
    # ═════════════════════════════════════════

    def _focus(self):
        """この窓にフォーカスを当てる"""
        hwnd = self.cfg.hwnd
        focus_window(hwnd)
        self._log(f"フォーカス切替 → HWND={hwnd:#010x}")

    def _do_skip(self):
        """通常スキップ: ^ キー長押しで自爆"""
        # 自分の窓がアイテムロスト待ち中は自爆しない
        if self.st.waiting_for_equip:
            self._log("自爆キャンセル（アイテムロスト待ち中）")
            return
        # 他窓が装備待ち・続行ラウンド中はフリーズ（自分が続行ラウンド中は除く）
        if not self.st.is_continue_round:
            while self._running and self.st.in_round:
                eq_ok  = _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                con_ok = _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                if eq_ok and con_ok:
                    break
        with _GLOBAL_ACTION_LOCK:
            if not self._running or self.st.is_continue_round or not self.st.in_round:
                return
            # ロック取得後も再チェック
            if self.st.waiting_for_equip:
                self._log("自爆キャンセル（ロック取得後にアイテムロスト待ち検出）")
                return
            self.st._skip_time = time.time()   # 自爆実行時刻を記録
            self._log(f"自爆実行中 ({DESTRUCT_HOLD_SEC}秒)…")
            self._focus()
            hold_key(get_destruct_key(), DESTRUCT_HOLD_SEC)

    def _do_after_round(self):
        """
        ラウンド終了後: 待機 → [購入+Begin前移動] → Beginクリック＆リトライ

        ロック戦略:
          - 購入・移動・クリック: ロックを取って実行（他窓と干渉しない）
          - クリック後の「ラウンド開始待ち」: ロックを解放して待機
            → 待機中に他窓が操作できる
        """
        time.sleep(BEGIN_WAIT_SEC)
        # Beginはフレ/フレ+/招待/招待+のみ
        if get_instance_type() not in INSTANCE_PRIVATE:
            return
        if not self._running or self.st.in_round:
            self._log("Begin キャンセル（停止 or 次のラウンドが開始）")
            return
        # 他窓が装備待ち・続行ラウンド中はフリーズ（自分が続行ラウンド中は除く）
        # 両イベントが同時にセット状態になるまで待つ（複合ケース対応）
        cfg = self.cfg
        st  = self.st

        # 他窓のフリーズ解除を待つ（自分がアイテムロスト中の場合は除く）
        if not st.is_continue_round and not st.waiting_for_equip:
            while self._running:
                eq_ok  = _EQUIP_WAIT_EVENT.is_set()
                con_ok = _CONTINUE_ROUND_EVENT.is_set()
                if eq_ok and con_ok:
                    break
                if not eq_ok:
                    self._log("他窓の装備待ち中 → フリーズ")
                if not con_ok:
                    self._log("他窓の続行/霧ラウンド中 → フリーズ")
                _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
            if not self._running:
                return

        # アイテムロスト状態なら他窓の解除を待ってから即フリーズ
        if st.waiting_for_equip:
            if not st.is_continue_round:
                while self._running:
                    if _EQUIP_WAIT_EVENT.is_set() and _CONTINUE_ROUND_EVENT.is_set():
                        break
                    _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
                if not self._running:
                    return
            _EQUIP_WAIT_EVENT.clear()
            self._log("⚠ アイテムロスト → 全窓フリーズ（Beginへ向かいます）")

        # ── フェーズ1: Begin前移動 + 初回クリック（ロック内）──
        with _GLOBAL_ACTION_LOCK:
            if not self._running or st.in_round:
                self._log("Begin キャンセル（ロック待ち中に停止 or 次のラウンドが開始）")
                # アイテムロスト中でかつラウンドに入った → フリーズ解除してフェーズ2aへ
                if st.waiting_for_equip and st.in_round:
                    _EQUIP_WAIT_EVENT.set()
                    self._log("ラウンド開始によりフリーズ解除 → 装備待ちへ")
                elif not st.waiting_for_equip:
                    return
            if not st.waiting_for_equip:
                # 通常のフリーズチェック（アイテムロスト中は自分がフリーズ主体なのでスキップ）
                if not st.is_continue_round:
                    if not _CONTINUE_ROUND_EVENT.is_set():
                        self._log("Begin キャンセル（ロック取得後にフリーズ検出）")
                        return
            if not st.in_round:
                self._focus()
                # アイテムロスト時はBeginへ向かう直前に音声（フォーカス後）
                if st.waiting_for_equip:
                    play_voice(cfg.voice_item_lost)
                hold_key("w", BEGIN_FORWARD_SEC)
                hold_key("a", BEGIN_LEFT_SEC)
                time.sleep(0.1)
                if not st.in_round:
                    self._log("Beginクリック")
                    click_at()

        # ── フェーズ2a: アイテムロスト装備待ち（ロック外）──
        if st.waiting_for_equip:
            self._log("アイテム装備を待っています… （装備すると自動再開）")
            while st.waiting_for_equip and self._running:
                time.sleep(0.3)
            if not self._running:
                _EQUIP_WAIT_EVENT.set()
                return
            self._log("✅ アイテム装備確認 → 続行")
            # 装備完了後はラウンドが始まっていれば終了（Beginは不要）
            if st.in_round:
                return

        # ── フェーズ2: Begin確認待ち＆リトライ（ロック外）──
        # "Verified" ログ = Begin正常押下確認。即座に検出してリトライ。
        if st.begin_done:
            return
        for attempt in range(1, BEGIN_RETRY_MAX+1):
            self._log(f"Begin確認待ち… [{attempt-1}/{BEGIN_RETRY_MAX}]")
            waited = 0.0
            while waited < BEGIN_RETRY_WAIT_SEC:
                time.sleep(0.2)
                waited += 0.2
                if not self._running:
                    return
                if st.begin_done:
                    return
                # フリーズチェック
                if not st.is_continue_round and not st.waiting_for_equip:
                    if not _EQUIP_WAIT_EVENT.is_set():
                        self._log("Begin待機中に他窓がアイテムロスト → 一時フリーズ")
                        while self._running and not _EQUIP_WAIT_EVENT.is_set():
                            _EQUIP_WAIT_EVENT.wait(timeout=1.0)
                    if not _CONTINUE_ROUND_EVENT.is_set():
                        self._log("Begin待機中に他窓が続行ラウンド開始 → 一時フリーズ")
                        while self._running and not _CONTINUE_ROUND_EVENT.is_set():
                            _CONTINUE_ROUND_EVENT.wait(timeout=1.0)
            if not self._running:
                return
            # Verified未確認 → リトライクリック
            if attempt < BEGIN_RETRY_MAX:
                self._log(f"Verified未確認 → リトライ {attempt}/{BEGIN_RETRY_MAX}")
                with _GLOBAL_ACTION_LOCK:
                    if not self._running or st.in_round or st.begin_done:
                        return
                    self._focus()
                    if attempt % 2 == 1:
                        hold_key("a", BEGIN_RETRY_LEFT_SEC)
                    else:
                        hold_key("d", BEGIN_RETRY_RIGHT_SEC)
                    time.sleep(0.1)
                    if st.in_round or st.begin_done:
                        return
                    click_at()

        self._log(f"Begin {BEGIN_RETRY_MAX}回試行しましたがVerified未確認")

    def _do_OpenSpecialRound_loop(self):
        """
        ラウンド中60秒ごとに移動キーをわずかに押す（ジャンプ代替）。
        - フォーカス切り替えは _GLOBAL_ACTION_LOCK 内でのみ行う
          → 自爆・Begin操作中にフォーカスを奪わない
          → ロック待ちになることで自爆完了後に実行される
        - 停止条件: _running=False / in_round=False /
                    is_OpenSpecialRound_round=False / OpenSpecialRound_completed=True
        """
        st = self.st
        self._log(f"AFK解除ループ開始（{OpenSpecialRound_INTERVAL_SEC}秒ごと）")
        elapsed = 0.0
        CHECK_INTERVAL = 1.0
        while True:
            if not self._running or not st.in_round or not st.is_OpenSpecialRound_round or st.OpenSpecialRound_wins >= OpenSpecialRound_TARGET_WINS:
                break
            time.sleep(CHECK_INTERVAL)
            elapsed += CHECK_INTERVAL
            if elapsed >= OpenSpecialRound_INTERVAL_SEC:
                elapsed = 0.0
                if not self._running or not st.in_round or not st.is_OpenSpecialRound_round or st.OpenSpecialRound_wins >= OpenSpecialRound_TARGET_WINS:
                    break
                # ロックを取ってフォーカス＆キー送信
                # 自爆・Begin中はロック待ちになるので操作が重ならない
                with _GLOBAL_ACTION_LOCK:
                    if not self._running or not st.in_round or not st.is_OpenSpecialRound_round or st.OpenSpecialRound_wins >= OpenSpecialRound_TARGET_WINS:
                        break
                    focus_window(self.cfg.hwnd)
                    keyboard.press("w")
                    time.sleep(0.05)
                    keyboard.release("w")
                self._log("移動キー送信（ジャンプ代替）")
        self._log("AFK解除ループ終了")


# ═══════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════
BG  = "#1e1e2e"
FG  = "#cdd6f4"
ACC = "#89b4fa"
RED = "#f38ba8"
GRN = "#a6e3a1"
SUB = "#313244"
YLW = "#f9e2af"
ORG = "#fab387"


class WindowTab(ttk.Frame):
    def __init__(self, parent, idx: int):
        super().__init__(parent)
        self.idx = idx
        self._hwnd_map: dict[str, int] = {}
        self._build()

    def _build(self):
        p = self

        def section(text):
            ttk.Label(p, text=text, background=BG, foreground=ACC,
                      font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))

        # ── VRChatウィンドウ ──
        section("■ VRChatウィンドウ")
        self.v_hwnd_sel = tk.StringVar(value="未選択")
        hf = ttk.Frame(p)
        hf.pack(fill="x", padx=10, pady=2)
        self.cb_hwnd = ttk.Combobox(hf, textvariable=self.v_hwnd_sel,
                                     state="readonly", width=36)
        self.cb_hwnd.pack(side="left", padx=(0, 4))
        ttk.Button(hf, text="🔄 更新", command=self._refresh_hwnds(1)).pack(side="left") # refresh_hwndsに1を入力することで、最新でアクティブになった窓を選択する

        # ── ログファイル ──
        section("■ ログファイル")
        self.v_log = tk.StringVar()
        lf = ttk.Frame(p)
        lf.pack(fill="x", padx=10, pady=2)
        ttk.Entry(lf, textvariable=self.v_log, width=80).pack(side="left", padx=(0, 4))
        ttk.Button(lf, text="…", width=3, command=self._browse_log).pack(side="left")

        # ── ON/OFF ──
        ttk.Separator(p, orient="horizontal").pack(fill="x", padx=10, pady=8)
        cf = ttk.Frame(p)
        cf.pack(fill="x", padx=10)
        self.v_active      = tk.BooleanVar(value=True)
        self.v_auto_begin  = tk.BooleanVar(value=True)
        self.v_do_skip     = tk.BooleanVar(value=True)
        self.v_cancel_afk  = tk.BooleanVar(value=True)
        self.v_hoshiimo    = tk.BooleanVar(value=False)
        self.v_announce_intermission = tk.BooleanVar(value=False)
        ttk.Checkbutton(cf, text="この窓を有効化",            variable=self.v_active).pack(side="left")
        ttk.Checkbutton(cf, text="自動Begin",                variable=self.v_auto_begin).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="自動自爆",                 variable=self.v_do_skip).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(cf, text="DTM/Waldo続行 (3クラまで)", variable=self.v_cancel_afk).pack(side="left", padx=(12, 0))
        cf2 = ttk.Frame(p)
        cf2.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Checkbutton(cf2, text="干し芋自動自爆",           variable=self.v_hoshiimo).pack(side="left")
        ttk.Checkbutton(cf2, text="Intermissionアナウンス",    variable=self.v_announce_intermission).pack(side="left", padx=(12, 0))


    # 最新のアクティブになったデータから取得し、VRChatウィンドウを古い順にhwndが入った配列で返す
    def _refresh_hwnds(self, hwnd_count: int):
        hwnds = get_vrchat_windows(hwnd_count)
        self._hwnd_map = {}
        choices = []
        for i, h in enumerate(hwnds):
            try:
                import win32gui as _wg
                rect = _wg.GetWindowRect(h)
            except Exception:
                rect = (0, 0, 0, 0)
            label = f"[{i+1}] HWND={h:#010x}"
            self._hwnd_map[label] = h
            choices.append(label)
        self.cb_hwnd["values"] = choices
        if choices and self.v_hwnd_sel.get() == "未選択":
            self.v_hwnd_sel.set(choices[0])

    def _get_selected_hwnd(self) -> int:
        return self._hwnd_map.get(self.v_hwnd_sel.get(), 0)

    def _browse_log(self):
        p = filedialog.askopenfilename(
            title="VRChatログを選択",
            initialdir=str(VRCHAT_LOG_DIR),
            filetypes=[("Log", "*.txt"), ("All", "*.*")]
        )
        if p:
            self.v_log.set(p)

    def get_config(self) -> "tuple[Optional[WindowConfig], Optional[str]]":
        if not self.v_active.get():
            return None, None
        log_str = self.v_log.get().strip()
        if not log_str:
            return None, "ログファイルが未設定です"
        log_path = Path(log_str)
        if not log_path.exists():
            return None, f"ログファイルが存在しません: {log_path.name}"
        return WindowConfig(
            hwnd=self._get_selected_hwnd(),
            log_path=log_path,
            active=True,
            auto_begin=self.v_auto_begin.get(),
            do_skip=self.v_do_skip.get(),
            cancel_afk=self.v_cancel_afk.get(),
            hoshiimo_skip=self.v_hoshiimo.get(),
            announce_intermission=self.v_announce_intermission.get(),
        ), None


# ── ログオーバーレイ ──────────────────────────────
class LogOverlay(tk.Toplevel):
    """半透明の最前面ログオーバーレイウィンドウ"""
    MAX_LINES = 20

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Log Overlay")
        self.overrideredirect(True)      # タイトルバーなし
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.75)
        self.configure(bg="#000000")
        self.geometry("520x320+10+10")
        self._lines: list[str] = []
        self._drag_x = 0
        self._drag_y = 0

        # ヘッダ（ドラッグ用）
        hf = tk.Frame(self, bg="#1e1e2e", cursor="fleur")
        hf.pack(fill="x")
        tk.Label(hf, text="ToNAutoBeginner Log", bg="#1e1e2e", fg="#89b4fa",
                 font=("Consolas", 9, "bold")).pack(side="left", padx=6)
        tk.Button(hf, text="✕", bg="#1e1e2e", fg="#f38ba8",
                  font=("Consolas", 9), relief="flat", bd=0,
                  command=self.close).pack(side="right", padx=4)
        # 透明度スライダー
        tk.Label(hf, text="α:", bg="#1e1e2e", fg="#cdd6f4",
                 font=("Consolas", 8)).pack(side="right")
        self._alpha = tk.DoubleVar(value=0.75)
        tk.Scale(hf, from_=0.2, to=1.0, resolution=0.05,
                 variable=self._alpha, orient="horizontal", length=80,
                 bg="#1e1e2e", fg="#cdd6f4", troughcolor="#313244",
                 highlightthickness=0, bd=0,
                 command=lambda v: self.attributes("-alpha", float(v))
                 ).pack(side="right")
        hf.bind("<ButtonPress-1>",   self._drag_start)
        hf.bind("<B1-Motion>",       self._drag_move)

        # ログテキスト
        self.text = tk.Text(
            self, bg="#000000", fg="#a6e3a1",
            font=("Consolas", 9), state="disabled",
            relief="flat", bd=0, wrap="word",
            insertbackground="#cdd6f4"
        )
        self.text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # リサイズグリップ
        grip = tk.Label(self, text="⠿", bg="#000000", fg="#444444",
                        cursor="size_nw_se", font=("Consolas", 10))
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>",  self._resize_start)
        grip.bind("<B1-Motion>",      self._resize_move)
        self._rw = self._rh = 0

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._closed = False

    def append(self, msg: str):
        if self._closed:
            return
        self._lines.append(msg)
        if len(self._lines) > self.MAX_LINES:
            self._lines.pop(0)
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n".join(self._lines))
        self.text.see("end")
        self.text.config(state="disabled")

    def close(self):
        self._closed = True
        self.destroy()

    def _drag_start(self, e):
        self._drag_x = e.x_root - self.winfo_x()
        self._drag_y = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    def _resize_start(self, e):
        self._rw = self.winfo_width()
        self._rh = self.winfo_height()
        self._drag_x = e.x_root
        self._drag_y = e.y_root

    def _resize_move(self, e):
        nw = max(300, self._rw + e.x_root - self._drag_x)
        nh = max(120, self._rh + e.y_root - self._drag_y)
        self.geometry(f"{nw}x{nh}")


# ── 折りたたみ可能フレーム ──────────────────────
class CollapsibleFrame(tk.Frame):
    """クリックで折りたたみ可能なLabelFrame風ウィジェット"""
    def __init__(self, parent, text: str, collapsed: bool = False, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self._collapsed = collapsed
        # ヘッダ行
        hf = tk.Frame(self, bg=BG)
        hf.pack(fill="x")
        self._toggle_btn = tk.Button(
            hf, text="▶" if collapsed else "▼",
            bg=BG, fg=ACC, font=("Segoe UI", 9),
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle)
        self._toggle_btn.pack(side="left")
        tk.Label(hf, text=text, bg=BG, fg=ACC,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=4)
        # 区切り線
        tk.Frame(hf, bg=SUB, height=1).pack(side="left", fill="x", expand=True, pady=6)
        # コンテンツ領域
        self.content = tk.Frame(self, bg=BG)
        if not collapsed:
            self.content.pack(fill="x", padx=8, pady=(0, 4))

    def _toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.content.pack_forget()
            self._toggle_btn.config(text="▶")
        else:
            self.content.pack(fill="x", padx=8, pady=(0, 4))
            self._toggle_btn.config(text="▼")


# ── メインウィンドウ ──────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ToNAutoBeginner")
        self.geometry("780x700")
        self.configure(bg=BG)
        self.v_tnl       = tk.StringVar()
        self.v_win_count = tk.IntVar(value=4)
        self.keepOn_set: dict = {}
        self.monitors: list[LogMonitor] = []
        self._running = False
        self._overlay: LogOverlay | None = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",            background=BG)
        s.configure("TLabel",            background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("TButton",           font=("Segoe UI", 10, "bold"), padding=4)
        s.configure("TCheckbutton",      background=BG, foreground=FG, font=("Segoe UI", 10))
        s.map("TCheckbutton", background=[("active", BG)])
        s.configure("TLabelframe",       background=BG, foreground=ACC)
        s.configure("TLabelframe.Label", background=BG, foreground=ACC,
                    font=("Segoe UI", 10, "bold"))
        s.configure("TEntry",            fieldbackground=SUB, foreground=FG)
        s.configure("TSpinbox",          fieldbackground=SUB, foreground=FG)
        s.configure("TNotebook",         background=BG, tabmargins=[2, 2, 2, 0])
        s.configure("TNotebook.Tab",     background=SUB, foreground=FG, padding=[10, 4])
        s.map("TNotebook.Tab",
              background=[("selected", ACC)], foreground=[("selected", BG)])
        s.configure("TSeparator",        background=SUB)

        # ① TNL
        f1 = ttk.LabelFrame(self, text="① 続行リスト(.tnl)", padding=8)
        f1.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Entry(f1, textvariable=self.v_tnl, width=58).pack(side="left", padx=(0, 6))
        ttk.Button(f1, text="参照…",    command=self._browse_tnl).pack(side="left")
        ttk.Button(f1, text="再読み込み", command=self._load_tnl).pack(side="left", padx=(4, 0))
        self.lbl_tnl = ttk.Label(f1, text="未読み込み", foreground=RED)
        self.lbl_tnl.pack(side="left", padx=(10, 0))

        # ② 自爆キー設定
        fk = ttk.Frame(self)
        fk.pack(fill="x", padx=12, pady=(0, 4))
        ttk.Label(fk, text="自爆キー:").pack(side="left")
        self.v_destruct_key = tk.StringVar(value=SELF_DESTRUCT_KEY)
        ek = ttk.Entry(fk, textvariable=self.v_destruct_key, width=6)
        ek.pack(side="left", padx=(6, 4))
        ttk.Button(fk, text="適用",
                   command=lambda: set_destruct_key(self.v_destruct_key.get().strip())
                   ).pack(side="left")

        # ③ 窓数・ログ
        f2 = CollapsibleFrame(self, text="② 窓数・ログ設定")
        f2.pack(fill="x", padx=12, pady=4)
        f2 = f2.content  # 以降はcontent内に追加
        wf = ttk.Frame(f2)
        wf.pack(fill="x")
        ttk.Label(wf, text="窓数:").pack(side="left")
        sb = ttk.Spinbox(wf, from_=1, to=8, textvariable=self.v_win_count, width=4,
                         command=self._on_win_count_change)
        sb.pack(side="left", padx=(4, 16))
        sb.bind("<FocusOut>", lambda e: self._on_win_count_change())
        ttk.Button(wf, text="📄 最新ログを自動割り当て",
                   command=self._assign_logs).pack(side="left")


        self.lbl_win_warn = ttk.Label(
            f2, text="※ 窓数はマクロ起動前に設定してください", foreground=YLW)
        self.lbl_win_warn.pack(anchor="w")

        # ③ 窓タブ
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="x", expand=False, padx=12, pady=4)
        self.tabs: list[WindowTab] = []
        self._rebuild_tabs(self.v_win_count.get())

        # 音声ファイル設定
        fv_wrap = CollapsibleFrame(self, text="③ 音声アナウンス設定", collapsed=True)
        fv_wrap.pack(fill="x", padx=12, pady=4)
        fv = fv_wrap.content

        def voice_row(parent, label, var):
            f = ttk.Frame(parent)
            f.pack(fill="x", pady=2)
            ttk.Label(f, text=label, width=24, anchor="w").pack(side="left")
            ttk.Entry(f, textvariable=var, width=36).pack(side="left", padx=(0, 4))
            ttk.Button(f, text="…", width=3,
                command=lambda v=var: v.set(
                    filedialog.askopenfilename(
                        title="音声ファイルを選択",
                        filetypes=[("音声", "*.wav *.mp3"), ("All", "*.*")]
                    ) or v.get()
                )).pack(side="left")

        self.v_voice_continue    = tk.StringVar(value=VOICE_CONTINUE)
        self.v_voice_fog         = tk.StringVar(value=VOICE_FOG)
        self.v_voice_item_lost   = tk.StringVar(value=VOICE_ITEM_LOST)
        self.v_voice_intermission = tk.StringVar(value=VOICE_INTERMISSION)
        voice_row(fv, "続行ラウンド:", self.v_voice_continue)
        voice_row(fv, "霧ラウンド:", self.v_voice_fog)
        voice_row(fv, "アイテムロスト:", self.v_voice_item_lost)
        voice_row(fv, "Intermission:", self.v_voice_intermission)

        # 音量スライダー
        volf = ttk.Frame(fv)
        volf.pack(fill="x", pady=(6, 0))
        ttk.Label(volf, text="音量:").pack(side="left")
        self.v_volume = tk.DoubleVar(value=1.0)
        ttk.Scale(volf, from_=0.0, to=1.0, variable=self.v_volume,
                  orient="horizontal", length=160,
                  command=lambda v: set_voice_volume(float(v))).pack(side="left", padx=(6, 4))
        self.lbl_volume = ttk.Label(volf, text="100%")
        self.lbl_volume.pack(side="left")
        self.v_volume.trace_add("write", lambda *_: self.lbl_volume.config(
            text=f"{int(self.v_volume.get()*100)}%"))

        # AFK解除設定（DTM / Waldo）
        # コントロール
        fc = ttk.Frame(self)
        fc.pack(pady=6)
        self.btn_start = ttk.Button(fc, text="▶ マクロ開始",
                                    command=self._start, width=16)
        self.btn_start.pack(side="left", padx=6)
        self.btn_stop  = ttk.Button(fc, text="■ 停止",
                                    command=self._stop, state="disabled", width=12)
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(fc, text="📋 オーバーレイ",
                   command=self._toggle_overlay, width=14).pack(side="left", padx=6)
        ttk.Label(fc, text="緊急停止: Pキー長押し",
                  foreground=ORG).pack(side="left", padx=(10, 0))

        # 完全放置モード（全窓共通）
        fhf = ttk.Frame(self)
        fhf.pack(pady=(0, 4))
        self.btn_hands_free = tk.Button(
            fhf, text="🤖 完全放置モード: OFF（全窓共通）",
            bg=SUB, fg=FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_hands_free)
        self.btn_hands_free.pack(side="left")
        ttk.Label(fhf, text="← ONにするとアイテムロストを無視・全ラウンド即自爆",
                  foreground=YLW).pack(side="left", padx=(10, 0))

        # アイテム取得→Begin
        fif = ttk.Frame(self)
        fif.pack(pady=(0, 4))
        self.btn_item_get_begin = tk.Button(
            fif, text="🎯 アイテム取得→Begin: OFF（全窓共通）",
            bg=SUB, fg=FG, font=("Segoe UI", 11, "bold"),
            relief="raised", padx=12, pady=5,
            command=self._toggle_item_get_begin)
        self.btn_item_get_begin.pack(side="left")
        ttk.Label(fif, text="← ONにするとアイテムロストラウンド開始時にフォーカス＆フリーズ",
                  foreground=YLW).pack(side="left", padx=(10, 0))

        # ログ
        fl = ttk.LabelFrame(self, text="ログ出力", padding=4)
        fl.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(
            fl, height=16, bg="#181825", fg=FG, width=80,
            font=("Consolas", 9), state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(fl, text="クリア", command=self._clear_log).pack(anchor="e", pady=(2, 0))

        # クレジット表示
        tk.Label(self, text="Credit: VOICEVOX冥鳴ひまり",
                 bg=BG, fg=SUB, font=("Segoe UI", 8)).pack(anchor="e", padx=12)

    def _rebuild_tabs(self, count: int):
        for tab in self.tabs:
            self.nb.forget(tab)
        self.tabs.clear()
        for i in range(count):
            tab = WindowTab(self.nb, i)
            self.nb.add(tab, text=f"窓{i + 1}")
            self.tabs.append(tab)

    def _on_win_count_change(self):
        if self._running:
            return
        try:
            n = max(1, min(8, int(self.v_win_count.get())))
        except (ValueError, tk.TclError):
            n = 1
        self.v_win_count.set(n)
        self._rebuild_tabs(n)

    def _toggle_item_get_begin(self):
        val = not get_item_get_begin_mode()
        set_item_get_begin_mode(val)
        if val:
            self.btn_item_get_begin.config(
                text="🎯 アイテム取得→Begin: ON（全窓共通）",
                bg="#1a3a5a", fg="#89b4fa", relief="sunken")
            self._log("[アイテム取得→Begin] ON: ラウンド開始時にフォーカス＆フリーズ")
        else:
            self.btn_item_get_begin.config(
                text="🎯 アイテム取得→Begin: OFF（全窓共通）",
                bg=SUB, fg=FG, relief="raised")
            self._log("[アイテム取得→Begin] OFF")

    def _toggle_hands_free(self):
        val = not get_hands_free()
        set_hands_free(val)
        if val:
            self.btn_hands_free.config(
                text="🤖 完全放置モード: ON（全窓共通）",
                bg="#3a1a1a", fg=RED, relief="sunken")
            self._log("[放置モード] ON: アイテムロスト無視・全ラウンド即自爆")
        else:
            self.btn_hands_free.config(
                text="🤖 完全放置モード: OFF（全窓共通）",
                bg=SUB, fg=FG, relief="raised")
            self._log("[放置モード] OFF")

    def _browse_tnl(self):
        p = filedialog.askopenfilename(
            title="tnlファイルを選択",
            filetypes=[("TNL files", "*.tnl"), ("All", "*.*")]
        )
        if p:
            self.v_tnl.set(p)
            self._load_tnl()

    def _load_tnl(self):
        p = self.v_tnl.get().strip()
        if not p or not Path(p).exists():
            messagebox.showerror("エラー", "tnlファイルが見つかりません")
            return
        try:
            self.keepOn_set, meta = load_tnl(p)
            total = sum(len(v) for v in self.keepOn_set.values())
            msg = f"[{meta['list_name']}] {len(self.keepOn_set)}ラウンド / {total}件 スキップ対象"
            self.lbl_tnl.config(text=msg, foreground=GRN)
            self._log(f"[TNL] {msg}")
        except Exception as e:                                  
            messagebox.showerror("TNL読み込みエラー", str(e))

    def _assign_logs(self):
        """
        VRChatウィンドウとログファイルを起動順（古い順）に自動割り当てる。
        有効な窓タブのi番目 → HWND[i] + ログ[i] を一括設定。
        """
        hwnds = get_vrchat_windows(self.v_win_count.get()) # 選ばれたのが古い順で配列に格納される。
        active_tabs = [tab for tab in self.tabs if tab.v_active.get()]
        count = max(len(hwnds), len(active_tabs))
        logs = find_latest_logs(VRCHAT_LOG_DIR, count)

        if not hwnds and not logs:
            self._log("[自動割り当て] VRChatウィンドウもログも見つかりません")
            return

        for i, tab in enumerate(active_tabs):
            # HWND割り当て
            if i < len(hwnds):
                hwnd = hwnds[i]
                # ComboboxにHWNDを追加して選択
                tab._refresh_hwnds(self.v_win_count.get())
                label = next(
                    (k for k, v in tab._hwnd_map.items() if v == hwnd), None)
                if label:
                    tab.v_hwnd_sel.set(label)

            # ログ割り当て
            if i < len(logs):
                tab.v_log.set(str(logs[i]))

            hwnd_str = f"{hwnds[i]:#010x}" if i < len(hwnds) else "未検出"
            log_str  = logs[i].name if i < len(logs) else "未検出"
            self._log(f"[窓{tab.idx+1}] HWND={hwnd_str} → {log_str}")
        self._log(f"[自動割り当て] {len(active_tabs)}窓に割り当てました")

    def _start(self):
        if not self.keepOn_set:
            messagebox.showwarning("警告", "先にtnlを読み込んでください")
            return

        # 有効な窓のログが空なら自動割り当て
        active_tabs = [tab for tab in self.tabs if tab.v_active.get()]
        logs = find_latest_logs(VRCHAT_LOG_DIR, len(active_tabs))
        log_idx = 0
        for tab in active_tabs:
            if not tab.v_log.get().strip() and log_idx < len(logs):
                tab.v_log.set(str(logs[log_idx]))
                self._log(f"[窓{tab.idx+1}] ログを自動割り当て: {logs[log_idx].name}")
            log_idx += 1

        self.monitors.clear()
        for tab in self.tabs:
            cfg, err = tab.get_config()
            if cfg is None:
                if err:
                    self._log(f"[窓{tab.idx+1}] スキップ: {err}")
                continue
            if cfg.hwnd == 0:
                self._log(f"[窓{tab.idx+1}] ⚠ HWNDが未選択です。窓タブで「🔄 更新」してVRChatウィンドウを選択してください")
            # 音声ファイルパスをAppのGUI設定から注入
            cfg.voice_continue     = self.v_voice_continue.get().strip()
            cfg.voice_fog          = self.v_voice_fog.get().strip()
            cfg.voice_item_lost    = self.v_voice_item_lost.get().strip()
            cfg.voice_intermission = self.v_voice_intermission.get().strip()
            self._log(f"[窓{tab.idx+1}] HWND={cfg.hwnd:#010x}  ログ={cfg.log_path.name}")
            mon = LogMonitor(cfg, self.keepOn_set, self._log, window_idx=tab.idx + 1)
            self.monitors.append(mon)
            mon.start()

        if not self.monitors:
            messagebox.showerror("エラー", "有効な窓/ログが見つかりません")
            return

        self._running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_win_warn.config(
            text="⚠ 動作中です。窓数はマクロ停止後に変更できます", foreground=RED)
        self._log(f"[起動] {len(self.monitors)}窓の監視を開始")

    def _stop(self):
        _EQUIP_WAIT_EVENT.set()          # フリーズ中でも確実に解除
        _continue_round_reset()        # 続行ラウンドフリーズも解除
        for m in self.monitors:
            m.stop()
        self.monitors.clear()
        self._running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_win_warn.config(
            text="※ 窓数はマクロ起動前に設定してください", foreground=YLW)
        self._log("[停止] マクロを停止しました")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}\n"
        def _a():
            self.log_text.config(state="normal")
            self.log_text.insert("end", line)
            self.log_text.see("end")
            self.log_text.config(state="disabled")
            if self._overlay and not self._overlay._closed:
                self._overlay.append(f"[{ts}] {msg}")
        self.after(0, _a)

    def _toggle_overlay(self):
        if self._overlay and not self._overlay._closed:
            self._overlay.close()
            self._overlay = None
        else:
            self._overlay = LogOverlay(self)

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _on_close(self):
        self._stop()
        self.destroy()


# ═══════════════════════════════════════════════
if __name__ == "__main__":
    app = App()

    # 緊急停止キー: P（ポーリング方式で長時間動作を保証）
    # リストで囲むことでネスト関数内から書き換え可能にする
    _p_state = [False]

    def _poll_p_key():
        try:
            now = keyboard.is_pressed("p")
            if now and not _p_state[0]:
                print("[緊急停止] P キーが押されました")
                app.after(0, app._stop)
            _p_state[0] = now
        except Exception:
            pass
        app.after(200, _poll_p_key)   # 200msごとにチェック

    app.after(200, _poll_p_key)
    app.mainloop()
