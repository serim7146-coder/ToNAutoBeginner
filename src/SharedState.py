import threading

import config

# ── 排他制御 ──
# 全窓で共有するロック。キー入力・マウス操作は必ずこのロックを取ってから実行する。
_GLOBAL_ACTION_LOCK = threading.Lock()

# ── インスタンスタイプ(初期はパブリックを仮定) ──
_CURRENT_INSTANCE_TYPE = config.INSTANCE_PUBLIC
_INSTANCE_LOCK = threading.Lock()

def get_instance_type() -> str:
    with _INSTANCE_LOCK:
        return _CURRENT_INSTANCE_TYPE

def set_instance_type(t: str):
    global _CURRENT_INSTANCE_TYPE
    with _INSTANCE_LOCK:
        _CURRENT_INSTANCE_TYPE = t