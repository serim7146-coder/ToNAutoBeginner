import threading

import config

# ═══════════════════════════════════════════════
#  グローバル操作ロック
#  キー入力・マウス操作は必ずこのロックを取ってから実行する
# ═══════════════════════════════════════════════
_GLOBAL_ACTION_LOCK = threading.Lock()

# ═══════════════════════════════════════════════
#  インスタンスタイプ（初期はパブリックを仮定）
# ═══════════════════════════════════════════════
_CURRENT_INSTANCE_TYPE = config.INSTANCE_PUBLIC
_INSTANCE_LOCK = threading.Lock()

def get_instance_type() -> str:
    with _INSTANCE_LOCK:
        return _CURRENT_INSTANCE_TYPE

def set_instance_type(t: str):
    global _CURRENT_INSTANCE_TYPE
    with _INSTANCE_LOCK:
        _CURRENT_INSTANCE_TYPE = t

# ═══════════════════════════════════════════════
#  自爆キー（GUIから変更可能）
# ═══════════════════════════════════════════════
_SUICIDE_KEY = config.SELF_SUICIDE_KEY
_SUICIDE_KEY_LOCK = threading.Lock()

def get_suicide_key() -> str:
    with _SUICIDE_KEY_LOCK:
        return _SUICIDE_KEY

def set_suicide_key(key: str):
    global _SUICIDE_KEY
    with _SUICIDE_KEY_LOCK:
        _SUICIDE_KEY = key

# ═══════════════════════════════════════════════
#  放置モード
# ═══════════════════════════════════════════════
_HANDS_FREE = False
_HANDS_FREE_LOCK = threading.Lock()

def get_hands_free() -> bool:
    with _HANDS_FREE_LOCK:
        return _HANDS_FREE

def set_hands_free(val: bool):
    global _HANDS_FREE
    with _HANDS_FREE_LOCK:
        _HANDS_FREE = val

# ═══════════════════════════════════════════════
#  アイテム取得→Beginモード
# ═══════════════════════════════════════════════
_ITEM_BEGIN_MODE = False
_ITEM_BEGIN_MODE_LOCK = threading.Lock()

def get_item_begin_mode() -> bool:
    with _ITEM_BEGIN_MODE_LOCK:
        return _ITEM_BEGIN_MODE

def set_item_begin_mode(val: bool):
    global _ITEM_BEGIN_MODE
    with _ITEM_BEGIN_MODE_LOCK:
        _ITEM_BEGIN_MODE = val

# ═══════════════════════════════════════════════
#  装備待ちイベント
#  set() = 通常動作可能、clear() = 装備待ち中（他窓のアクションをブロック）
# ═══════════════════════════════════════════════
EQUIP_WAIT_EVENT = threading.Event()
EQUIP_WAIT_EVENT.set()  # 初期値は通常動作可能

# ═══════════════════════════════════════════════
#  続行・霧ラウンド中フリーズイベント
#  set() = 通常動作可能、clear() = 続行/霧ラウンド中（他窓をブロック）
# ═══════════════════════════════════════════════
CONTINUE_ROUND_EVENT = threading.Event()
CONTINUE_ROUND_EVENT.set()  # 初期値は通常動作可能
_CONTINUE_ROUND_COUNT = 0
_CONTINUE_ROUND_LOCK = threading.Lock()

def continue_round_start():
    """続行ラウンド開始：カウンターを増やしてフリーズ"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT += 1
        CONTINUE_ROUND_EVENT.clear()

def continue_round_end():
    """続行ラウンド終了：カウンターを減らし、0になったらフリーズ解除"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT = max(0, _CONTINUE_ROUND_COUNT - 1)
        if _CONTINUE_ROUND_COUNT == 0:
            CONTINUE_ROUND_EVENT.set()

def continue_round_reset():
    """停止時など強制リセット"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT = 0
        CONTINUE_ROUND_EVENT.set()

def get_continue_round_count() -> int:
    with _CONTINUE_ROUND_LOCK:
        return _CONTINUE_ROUND_COUNT
