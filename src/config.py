import os
from pathlib import Path

import ReadJson


VRCHAT_LOG_DIR = (
    Path(os.environ.get("APPDATA", "~")) / ".." / "LocalLow" / "VRChat" / "VRChat"
).resolve()
VRCHAT_WINDOW_CLASS = "UnityWndClass"

# ── アプリ情報・自動アップデート ──
# APP_VERSION はリリースごとに上げ、GitHubのリリースタグと一致させること
APP_VERSION       = "0.3.0"
GITHUB_REPO       = "serim7146-coder/ToNAutoBeginner"
UPDATE_ASSET_NAME = "ToNAutoBeginner.exe"

# ── 設定ファイル（前回のtnlパスなどを保存） ──
SETTINGS_PATH = Path(os.environ.get("APPDATA", ".")) / "ToNAutoBeginner" / "settings.json"

EMERGENCY_STOP_KEY = "p"
EMERGENCY_STOP_POLL_MS = 200

GUI_BG  = "#1e1e2e"
GUI_FG  = "#cdd6f4"
GUI_ACC = "#89b4fa"
GUI_RED = "#f38ba8"
GUI_GRN = "#a6e3a1"
GUI_SUB = "#313244"
GUI_YLW = "#f9e2af"
GUI_ORG = "#fab387"
GUI_LOG_MAX_LINES = 1000
GUI_OVERLAY_LOG_MAX_LINES = 20


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

ROUND_START_ITEM_LOSS_ROUNDS = {
    "Punished",
    "8 Pages",
}

# Wiki note: 8 Pages keeps items below 100 Eph. Fill this with item IDs once
# the app has an item-id to price table.
EIGHT_PAGES_KEEP_ITEM_IDS = set()

LATE_ROUND = {
    "Punished",
}

# ── 自動Begin ──
BEGIN_WAIT_SEC        = 11.0   # RoundOver から Begin移動を始めるまでの待機。
                               # 実測: RoundOver→Verified Round End が約13秒。
                               # 11秒待って約2.2秒移動すると、移動し終えた頃に
                               # Round End が出てクリックできる状態になる。

# 続行/霧ラウンドのフリーズ解除を死亡から遅らせる。
# 解除前に猶予を作り、手動での視点調整などを挟めるようにするため。
# 霧ラウンド（テラー不明のまま終わった場合）は短め、続行ラウンドは長めにする。
CONTINUE_FREEZE_RELEASE_DELAY_SEC = 5.0
FOG_FREEZE_RELEASE_DELAY_SEC      = 2.0
BEGIN_FORWARD_SEC     = 2.1    # Begin前の前進時間
BEGIN_LEFT_SEC        = 0.09   # Begin前の左移動時間（OSC移動での実測値）
BEGIN_FORWARD_SEC_LATER = 3.2  # パニッシュ後
BEGIN_LEFT_SEC_LATER  = 0.16  # パニッシュ後


BEGIN_RETRY_LEFT_SEC  = 0.05   # Beginリトライ時の左移動時間
BEGIN_RETRY_FIRST_LEFT_SEC = 0.10  # 1回目のリトライだけ左に大きく寄せる
BEGIN_RETRY_RIGHT_SEC = 0.11   # Beginリトライ時の右移動時間
BEGIN_RETRY_WAIT_SEC  = 2.0    # リトライの間隔
BEGIN_RETRY_MAX       = 4      # リトライ回数（初回Beginは含まない）

# ── フォーカス取得 ──
FOCUS_RETRY_MAX      = 3     # 前面化を試みる回数
FOCUS_RETRY_WAIT_SEC = 0.12  # 前面化要求後に反映を待つ時間

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

# ── テラーバリアント判定待ち ──
# Bloodthirsty Creature / Hungry Home Invader はテラー出現時にログ行が出る。
# 該当テラーがいるラウンドでは、出現を待ってから自動自爆の可否を判断する。
# 実測値(手元ログ): Classicは Killers設定からスポーンまで最大3秒(154件)、
# Bloodbathは枠ごとに遅れて出現し最大10秒(25件)。余裕を持たせた値にする。
TERROR_VARIANT_WAIT_SEC = {
    "Classic":      5.0,
    "Classic.exe":  5.0,
    "Randomizer":   5.0,
    "Bloodbath":   15.0,
}
TERROR_VARIANT_WAIT_DEFAULT_SEC = 10.0
TERROR_VARIANT_POLL_SEC = 0.2

# ── ToNワールド ──
TON_WORLD_ID = "wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd"  # Terrors of Nowhere
TON_DEFAULT_REGION = "jp"

# ── ToN入室時の自動操作 ──
# 的の位置は画面比率で持つ。スポーン地点と向きが固定なので毎回同じ位置に出る。
# 値は 2560x1440 での実測から算出（例: 警告同意 1327/2560, 739/1440）。
TON_ENTRY_ENABLED = True
TON_ENTRY_BEGIN   = True   # 選択画面突破後にBeginまで押すか

# 入室時の操作手順（実測で決めた移動量）
# アバターを横移動させると的が画面中央（クロスヘア）に来る。
# 画面座標は使わない（パネルの見かけの大きさが距離で変わるため）。
# 最後の2つは的がほぼ同じ位置なので移動せず続けて押す。
TON_ENTRY_STEPS = [
    {"move": "right", "sec": 0.35, "label": "警告同意"},
    {"move": "left",  "sec": 0.65, "label": "難易度(Casual)"},
    {"move": "right", "sec": 0.55, "label": "BGM"},
    {"move": None,    "sec": 0.0,  "label": "LET ME PLAY"},
]

# 選択画面を抜けた後、ロビーのBeginまで移動する量（実測）
# 入室直後は位置が違うため、ラウンド終了後の BEGIN_FORWARD_SEC とは別の値。
TON_ENTRY_BEGIN_FORWARD_SEC = 5.30
TON_ENTRY_BEGIN_LEFT_SEC    = 0.12

TON_ENTRY_PANEL_TIMEOUT  = 180.0  # パネル出現を待つ上限（読み込みが重い）
TON_ENTRY_POLL_SEC       = 2.0    # パネル確認の間隔
TON_ENTRY_START_DELAY_SEC = 3.0   # パネル検出後、操作を始めるまでの待ち
TON_ENTRY_SETTLE_SEC     = 1.0    # 移動後、クリックするまでの待ち
TON_ENTRY_STEP_WAIT_SEC  = 3.0    # クリック後、次の画面が出るまでの待ち
TON_ENTRY_MIN_RED_PIXELS = 300    # パネルが出ているとみなす赤画素の下限
TON_PANEL_STABLE_COUNT   = 2      # 連続で検出できたら確定

# ── OSC（窓ごとに別ポートを割り当てる） ──
# VRChatは既定でUDP 9000を掴む。多重起動では --osc= で分けないと競合する。
OSC_BASE_IN_PORT = 9000   # 窓1の受信ポート（送信は+1）
OSC_PORT_STRIDE  = 10     # 窓ごとのポート間隔
OSC_ENABLED      = True   # 起動時に --osc= を付けるか

# ── VRChat起動 ──
LAUNCH_STAGGER_SEC      = 6.0    # 窓を連続起動する際の間隔
LAUNCH_WINDOW_TIMEOUT   = 180.0  # ウィンドウ出現を待つ上限
LAUNCH_LOG_TIMEOUT      = 60.0   # 起動後にログ生成を待つ上限
LAUNCH_LOG_POLL_SEC     = 1.0    # ログ生成の確認間隔
LAUNCH_DESKTOP_MODE     = True   # 既定はデスクトップモード(--no-vr)

# ── ウィンドウ↔ログの対応付け ──
LOG_MATCH_TOLERANCE_SEC   = 120.0  # プロセス起動時刻とログ作成時刻の許容差
LOG_MATCH_CANDIDATE_COUNT = 20     # 突き合わせ対象にするログの本数

# ── ログの更新頻度 ──
LOG_POLL_INTERVAL    = 0.3
LOG_START_SCAN_CHUNK_BYTES = 256 * 1024

CURIOUS_CREATURE_ID = 106
BLOODTHIRSTY_CREATURE_ID = 192
BLOODTHIRSTY_CREATURE_LOG = "The creature is bloodthirsty today..."
SLENDER_ID = 47
HUNGRY_HOME_INVADER_ID = 190
HUNGRY_HOME_INVADER_LOG = "I hear strange sounds coming from the kitchen."

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
