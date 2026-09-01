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
#  速度によるラウンド種別の検知
#  判定（受信のみ）と横移動（マクロ）の両方をまとめて止められる
# ═══════════════════════════════════════════════
_SPEED_DETECT = config.SPEED_DETECT_ENABLED
_SPEED_DETECT_LOCK = threading.Lock()

def get_speed_detect() -> bool:
    with _SPEED_DETECT_LOCK:
        return _SPEED_DETECT

def set_speed_detect(val: bool):
    global _SPEED_DETECT
    with _SPEED_DETECT_LOCK:
        _SPEED_DETECT = val

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
#  複数窓が同時にアイテムロストしても全窓の解除が揃うまでフリーズを維持するため、
#  続行ラウンドと同じカウンタ方式で管理する。
#  clear/setは直接呼ばず equip_freeze_start / equip_freeze_end を使うこと。
#  窓ごとの多重登録・多重解除は WindowState.equip_freeze_held で防ぐ。
# ═══════════════════════════════════════════════
EQUIP_WAIT_EVENT = threading.Event()
EQUIP_WAIT_EVENT.set()  # 初期値は通常動作可能
_EQUIP_FREEZE_COUNT = 0
_EQUIP_FREEZE_LOCK = threading.Lock()

def equip_freeze_start(st):
    """窓stを装備待ちフリーズ保持者として登録（登録済みなら何もしない）"""
    global _EQUIP_FREEZE_COUNT
    with _EQUIP_FREEZE_LOCK:
        if st.equip_freeze_held:
            return
        st.equip_freeze_held = True
        _EQUIP_FREEZE_COUNT += 1
        EQUIP_WAIT_EVENT.clear()

def equip_freeze_end(st):
    """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）"""
    global _EQUIP_FREEZE_COUNT
    with _EQUIP_FREEZE_LOCK:
        if not st.equip_freeze_held:
            return
        st.equip_freeze_held = False
        _EQUIP_FREEZE_COUNT = max(0, _EQUIP_FREEZE_COUNT - 1)
        if _EQUIP_FREEZE_COUNT == 0:
            EQUIP_WAIT_EVENT.set()

def equip_freeze_reset():
    """停止時など強制リセット（LogMonitor/WindowStateは起動ごとに作り直される前提）"""
    global _EQUIP_FREEZE_COUNT
    with _EQUIP_FREEZE_LOCK:
        _EQUIP_FREEZE_COUNT = 0
        EQUIP_WAIT_EVENT.set()

def get_equip_freeze_count() -> int:
    with _EQUIP_FREEZE_LOCK:
        return _EQUIP_FREEZE_COUNT

# ═══════════════════════════════════════════════
#  速度検知フリーズ（8 Pages / Punished）
#  set() = 通常動作可能、clear() = フリーズ中（他窓のアクションをブロック）
#  適切なアイテム（スキャナー/ナッツ）を取りに行く時間を作るために止める。
#  窓ごとの多重登録・多重解除は WindowState.speed_freeze_held で防ぐ。
# ═══════════════════════════════════════════════
SPEED_FREEZE_EVENT = threading.Event()
SPEED_FREEZE_EVENT.set()  # 初期値は通常動作可能
_SPEED_FREEZE_COUNT = 0
_SPEED_FREEZE_LOCK = threading.Lock()

def speed_freeze_start(st):
    """窓stを速度検知フリーズの保持者として登録（登録済みなら何もしない）"""
    global _SPEED_FREEZE_COUNT
    with _SPEED_FREEZE_LOCK:
        if st.speed_freeze_held:
            return
        st.speed_freeze_held = True
        _SPEED_FREEZE_COUNT += 1
        SPEED_FREEZE_EVENT.clear()

def speed_freeze_end(st):
    """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）"""
    global _SPEED_FREEZE_COUNT
    with _SPEED_FREEZE_LOCK:
        if not st.speed_freeze_held:
            return
        st.speed_freeze_held = False
        _SPEED_FREEZE_COUNT = max(0, _SPEED_FREEZE_COUNT - 1)
        if _SPEED_FREEZE_COUNT == 0:
            SPEED_FREEZE_EVENT.set()

def speed_freeze_reset():
    """停止時など強制リセット"""
    global _SPEED_FREEZE_COUNT
    with _SPEED_FREEZE_LOCK:
        _SPEED_FREEZE_COUNT = 0
        SPEED_FREEZE_EVENT.set()

def get_speed_freeze_count() -> int:
    with _SPEED_FREEZE_LOCK:
        return _SPEED_FREEZE_COUNT

# ═══════════════════════════════════════════════
#  ラウンド突入フリーズ（Alternate/Unbound/Ghost 等）
#  set() = 通常動作可能、clear() = フリーズ中（他窓のアクションをブロック）
#  続行ラウンドと違い、張った窓自身の自爆は止めない。止めるのは他窓だけ。
#  窓ごとの多重登録・多重解除は WindowState.round_freeze_held で防ぐ。
# ═══════════════════════════════════════════════
ROUND_FREEZE_EVENT = threading.Event()
ROUND_FREEZE_EVENT.set()  # 初期値は通常動作可能
_ROUND_FREEZE_COUNT = 0
_ROUND_FREEZE_LOCK = threading.Lock()

def round_freeze_start(st):
    """窓stをラウンド突入フリーズの保持者として登録（登録済みなら何もしない）"""
    global _ROUND_FREEZE_COUNT
    with _ROUND_FREEZE_LOCK:
        if st.round_freeze_held:
            return
        st.round_freeze_held = True
        _ROUND_FREEZE_COUNT += 1
        ROUND_FREEZE_EVENT.clear()

def round_freeze_end(st):
    """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）"""
    global _ROUND_FREEZE_COUNT
    with _ROUND_FREEZE_LOCK:
        if not st.round_freeze_held:
            return
        st.round_freeze_held = False
        _ROUND_FREEZE_COUNT = max(0, _ROUND_FREEZE_COUNT - 1)
        if _ROUND_FREEZE_COUNT == 0:
            ROUND_FREEZE_EVENT.set()

def round_freeze_reset():
    """停止時など強制リセット"""
    global _ROUND_FREEZE_COUNT
    with _ROUND_FREEZE_LOCK:
        _ROUND_FREEZE_COUNT = 0
        ROUND_FREEZE_EVENT.set()

def get_round_freeze_count() -> int:
    with _ROUND_FREEZE_LOCK:
        return _ROUND_FREEZE_COUNT

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
