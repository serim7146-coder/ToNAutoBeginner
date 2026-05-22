from pathlib import Path

import ReadJson

# ── テラーIDとテラー名の対応表.json ──
_BASE = Path(__file__).parent
_TERRORS_PATH = _BASE / "terrors.json"
# ↓これが固定量
TERRORS = ReadJson.load_terrors(_TERRORS_PATH)

SPECIAL_ROUND = {
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

ITEM_LOSS_ROUNDS = {
    "Randomizer",
    "Punished",
    "8 Pages",
    "Run",
}

# ── 自動Begin ──
BEGIN_WAIT_SEC        = 0.0    # ラウンド終了後Beginまでの待機
BEGIN_FORWARD_SEC     = 2.1    # Begin前の前進時間
BEGIN_LEFT_SEC        = 0.11   # Begin前の左移動時間

BEGIN_RETRY_LEFT_SEC  = 0.05   # Beginリトライ時の左移動時間
BEGIN_RETRY_RIGHT_SEC = 0.11   # Beginリトライ時の右移動時間
BEGIN_RETRY_WAIT_SEC  = 2.0    # リトライの間隔
BEGIN_RETRY_MAX       = 4      # リトライ回数（初回Beginは含まない）

# ── 自動自爆 ──
SELF_SUSIDE_KEY    = "^"  # デフォルト値（GUIで変更可能）
SUSIDE_HOLD_SEC    = 3.0     # 自爆ボタンを押す時間

# ── 3クラ続行設定 ──────────────────────────────
OpenSpecialRound_TERROR_IDS: set[int] = {ReadJson.terror_id("Don't Touch Me"), ReadJson.terror_id("Waldo")}
OpenSpecialRound_TARGET_WINS  = 3       # 何勝したらジャンプ停止するか（窓ごと）
OpenSpecialRound_INTERVAL_SEC = 60.0    # AFK回避の移動の間隔（秒）

# ── インスタンスタイプ ──
INSTANCE_PUBLIC        = "public"
INSTANCE_PRIVATE       = "private"
INSTANCE_HOSHIIMO      = "hoshiimo"
INSTANCE_CBPS          = "cbps"
INSTANCE_OTHER_GROUP   = "other_group"

# ── インスタンスid ──
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

# ── ログの更新頻度 ──
LOG_POLL_INTERVAL    = 0.3

# ── 音量 ──
DEFAULT_SOUND_VOLUME = 1.0

# ── 音声アナウンスファイルパス ──
VOICE_CONTINUE     = "../voice/Continue.mp3"
VOICE_FOG          = "../voice/Fog.mp3"
VOICE_ITEM_LOST    = "../voice/ItemLost.mp3"
VOICE_INTERMISSION = "../voice/intermission.mp3"
VOICE_FOXY         = "../voice/foxy.mp3"