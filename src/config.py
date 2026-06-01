from pathlib import Path

import ReadJson

def resource_path(filename: str) -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here / filename,
        here.parent / filename,
        here.parent / "ToNAutoBeginner" / filename,
    ]
    compiled = globals().get("__compiled__")
    if compiled is not None:
        candidates.append(Path(compiled.containing_dir) / filename)

    for path in candidates:
        if path.exists():
            return path
    return candidates[0]

# ── テラーIDとテラー名の対応表.json ──
TERRORS = ReadJson.load_terrors(resource_path("terrors.json"))

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

LATE_ROUND = {
    "Punished",
}

# ── 自動Begin ──
BEGIN_WAIT_SEC        = 0.0    # ラウンド終了後Beginまでの待機
BEGIN_FORWARD_SEC     = 2.1    # Begin前の前進時間
BEGIN_LEFT_SEC        = 0.11   # Begin前の左移動時間
BEGIN_FORWARD_SEC_LATER = 3.2  # パニッシュや8 Pages後
BEGIN_LEFT_SEC_LATER  = 0.16  # パニッシュや8 Pages後


BEGIN_RETRY_LEFT_SEC  = 0.05   # Beginリトライ時の左移動時間
BEGIN_RETRY_RIGHT_SEC = 0.11   # Beginリトライ時の右移動時間
BEGIN_RETRY_WAIT_SEC  = 2.0    # リトライの間隔
BEGIN_RETRY_MAX       = 4      # リトライ回数（初回Beginは含まない）

# ── 自動自爆 ──
SELF_SUICIDE_KEY    = "^"  # デフォルト値（GUIで変更可能）
SUICIDE_HOLD_SEC    = 3.0     # 自爆ボタンを押す時間
SUICIDE_FOCUS_SETTLE_SEC = 0.25  # 自爆前にVRChatへフォーカスが移るのを待つ時間

# ── 3クラ続行設定 ──────────────────────────────
OPEN_SPECIAL_ROUND_TERROR_IDS: set[int] = {
    ReadJson.terror_id("Don't Touch Me", TERRORS),
    ReadJson.terror_id("Waldo", TERRORS)
}
OPEN_SPECIAL_ROUND_TARGET_WINS  = 3      # 何勝したらAFK回避を終わるか（窓ごと）
OPEN_SPECIAL_ROUND_INTERVAL_SEC = 60.0   # AFK回避の移動の間隔（秒）

# ── フリーズ解除待機 ──
EQUIP_RELEASE_DELAY_SEC = 2.0            # アイテム装備確認後のフリーズ解除までの待機時間

# ── インスタンスタイプ ──
INSTANCE_PUBLIC        = "public"
INSTANCE_PRIVATE       = "private"
INSTANCE_HOSHIIMO      = "hoshiimo"
INSTANCE_YAKIIMO       = "yakiimo"
INSTANCE_CBPS          = "cbps"
INSTANCE_OTHER_GROUP   = "other_group"

# ── インスタンスid ──
HOSHIIMO_GROUP_ID = "grp_0821983a-f7ab-4252-9895-0fe2712026a9"
YAKIIMO_GROUP_ID  = "grp_005eab93-0bee-4493-9973-252f9ed51461"
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
    "Bloodbath EX",
    "Randomizer",
    "Punished",
    "Sabotage",
}

# ── ログの更新頻度 ──
LOG_POLL_INTERVAL    = 0.3

# ── 音量 ──
DEFAULT_SOUND_VOLUME = 1.0

# ── 音声アナウンスファイルパス ──
VOICE_CONTINUE     = str(resource_path("voice/Continue.mp3"))
VOICE_FOG          = str(resource_path("voice/Fog.mp3"))
VOICE_ITEM_LOST    = str(resource_path("voice/ItemLost.mp3"))
VOICE_INTERMISSION = str(resource_path("voice/intermission.mp3"))
VOICE_FOXY         = str(resource_path("voice/SpawnFoxy.mp3"))

# 自動操作後の待ち時間
OPERATOR_WAIT_SEC = 0.05
