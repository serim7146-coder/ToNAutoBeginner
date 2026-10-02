import threading

import DebugLog

import config

# ═══════════════════════════════════════════════
#  グローバル操作ロック
#  キー入力・マウス操作は必ずこのロックを取ってから実行する
# ═══════════════════════════════════════════════
_GLOBAL_ACTION_LOCK = threading.Lock()

# ── 借りた前面の札 ─────────────────────────────
# フリーズの理由で VRChat の窓を前面にしたとき（続行・突入・速度検知・アイテムロスト）の
# 札（WindowOperator.FrontLoan）を窓ごとに1枚だけ持ち、その窓のフリーズが全部
# 解けたら元の窓へ返す。返すのは裏のスレッドで、_GLOBAL_ACTION_LOCK を取ってから
# （*_end はロックを持った場所からも呼ばれうる。このロックは再入できない）
_FRONT_LOAN_LOCK = threading.Lock()


def keep_front_loan(st, loan):
    """札を持つ。同じ窓で二重に借りたら最初の札だけを持つ"""
    if loan is None:
        return
    with _FRONT_LOAN_LOCK:
        if st.front_loan is None:
            st.front_loan = loan


def discard_front_loan(st):
    """札を捨てる（監視の停止・終了。返さない）"""
    with _FRONT_LOAN_LOCK:
        st.front_loan = None


def _holds_any_freeze(st) -> bool:
    return (st.equip_freeze_held or st.continue_freeze_held
            or st.speed_freeze_held or st.round_freeze_held)


def _return_front_when_free(st):
    """その窓のフリーズが全部解けていて札があれば、返しに行く。

    アイテム取得→Begin モードの装備待ちの窓が列の先頭にいれば、元の窓へは返さず
    札をその窓へ引き継ぐ（その窓の見張りが前面化＋音声を出すので、元の窓を一瞬
    挟まない）。その窓のフリーズが全部解けたら元の窓へ返る
    """
    with _FRONT_LOAN_LOCK:
        loan = st.front_loan
        if loan is None or _holds_any_freeze(st):
            return
        st.front_loan = None
        heir = _front_heir(st)
        if heir is not None:
            if heir.front_loan is None:
                loan.hwnd = heir.equip_front_hwnd   # 次に前に出るのはその窓
                heir.front_loan = loan
            return                                  # 札が既にあればそちらを使う
    _start_give_back(loan)


def _front_heir(st):
    """札を引き継ぐ窓（装備待ちの列の先頭で、前面を引き継ぐ装備待ち）。無ければ None"""
    with _EQUIP_FREEZE_LOCK:
        if not _EQUIP_QUEUE:
            return None
        head = _EQUIP_QUEUE[0]
    if head is st or not head.equip_front_hwnd:
        return None
    return head


def _start_give_back(loan):
    threading.Thread(target=_give_back, args=(loan,), daemon=True).start()


def _give_back(loan):
    with _GLOBAL_ACTION_LOCK:
        loan.give_back()

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
#  自爆キー（config の既定値。テストから差し替え可能）
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
#  フリーズ設定（全窓共通）
#  フリーズ自体が全窓を止める仕組みなので、窓ごとに分ける意味がない
# ═══════════════════════════════════════════════
_FREEZE_ON_8PAGES = False
_FREEZE_ON_PUNISH = False
_FREEZE_ROUNDS: set = set()
_FREEZE_LOCK = threading.Lock()

def get_freeze_on_8pages() -> bool:
    with _FREEZE_LOCK:
        return _FREEZE_ON_8PAGES

def set_freeze_on_8pages(val: bool):
    global _FREEZE_ON_8PAGES
    with _FREEZE_LOCK:
        _FREEZE_ON_8PAGES = bool(val)

def get_freeze_on_punish() -> bool:
    with _FREEZE_LOCK:
        return _FREEZE_ON_PUNISH

def set_freeze_on_punish(val: bool):
    global _FREEZE_ON_PUNISH
    with _FREEZE_LOCK:
        _FREEZE_ON_PUNISH = bool(val)

def get_freeze_rounds() -> set:
    """突入で全窓を止めるラウンド種別。コピーを返す（呼び出し側の書き換え防止）"""
    with _FREEZE_LOCK:
        return set(_FREEZE_ROUNDS)

def set_freeze_rounds(names):
    global _FREEZE_ROUNDS
    with _FREEZE_LOCK:
        _FREEZE_ROUNDS = set(names or ())

# ═══════════════════════════════════════════════
#  続行リストの供給元（全窓共通）
#  "host" = ToN ListTool の主催リスト / "tnl" = tnlファイル / None = 未決定
# ═══════════════════════════════════════════════
_LIST_SOURCE = None
_LIST_SOURCE_LOCK = threading.Lock()

def get_list_source():
    with _LIST_SOURCE_LOCK:
        return _LIST_SOURCE

def set_list_source(src):
    global _LIST_SOURCE
    with _LIST_SOURCE_LOCK:
        _LIST_SOURCE = src

# ═══════════════════════════════════════════════
#  アイテム取得→Beginモード
# ═══════════════════════════════════════════════
_ITEM_FETCH = False             # アイテム自動取得（CM。全窓共通・既定 OFF）
_ITEM_FETCH_LOCK = threading.Lock()


def get_item_fetch() -> bool:
    with _ITEM_FETCH_LOCK:
        return _ITEM_FETCH


def set_item_fetch(val: bool):
    global _ITEM_FETCH
    with _ITEM_FETCH_LOCK:
        _ITEM_FETCH = bool(val)


_ITEM_FETCH_GAIN = None         # 前にうまくいった視点の感度（横, 縦）。保存して次回の測りに使う


def get_item_fetch_gain():
    with _ITEM_FETCH_LOCK:
        return _ITEM_FETCH_GAIN


def set_item_fetch_gain(gain):
    """(横, 縦) か None。数でない・範囲（ItemFetch.GAIN_SANE）の外は None"""
    global _ITEM_FETCH_GAIN
    value = None
    try:
        x, y = (float(g) for g in gain)
        if all(0.05 <= g <= 20.0 for g in (x, y)):
            value = (x, y)
    except (TypeError, ValueError):
        value = None
    with _ITEM_FETCH_LOCK:
        _ITEM_FETCH_GAIN = value


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
# 装備待ちを張った順。アイテムロストの前面化＋音声は先頭の窓だけが出す
# （利用者が一度に操作できるのは1窓なので、1窓ずつ案内する）。
# 足す・外す・読むは _EQUIP_FREEZE_LOCK の中で行う。同じ瞬間に2窓が張っても
# 鍵の順に並ぶので、順番は必ず決まる
_EQUIP_QUEUE: list = []

def _note_freeze(st, kind: str, started: bool, count: int):
    """debug.log へ: どの窓がフリーズを張った／解いたか、いま何窓が張っているか"""
    DebugLog.write(f"[状態] 窓{getattr(st, 'window_idx', 0)} {kind}フリーズを"
                   f"{'張った' if started else '解いた'}（張っている窓: {count}）")


def equip_freeze_start(st):
    """窓stを装備待ちフリーズ保持者として登録（登録済みなら何もしない）"""
    global _EQUIP_FREEZE_COUNT
    with _EQUIP_FREEZE_LOCK:
        if st.equip_freeze_held:
            return
        st.equip_freeze_held = True
        _EQUIP_FREEZE_COUNT += 1
        _note_freeze(st, "装備待ち", True, _EQUIP_FREEZE_COUNT)
        _EQUIP_QUEUE.append(st)
        EQUIP_WAIT_EVENT.clear()

def equip_freeze_end(st):
    """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）"""
    global _EQUIP_FREEZE_COUNT
    with _EQUIP_FREEZE_LOCK:
        if not st.equip_freeze_held:
            return
        st.equip_freeze_held = False
        _note_freeze(st, "装備待ち", False, max(0, _EQUIP_FREEZE_COUNT - 1))
        st.equip_front_hwnd = 0
        _EQUIP_FREEZE_COUNT = max(0, _EQUIP_FREEZE_COUNT - 1)
        # 同一性で外す。WindowState は dataclass なので == は中身の比較
        # （いまは直前に落とした equip_freeze_held で区別がつくが、それに頼らない）
        _EQUIP_QUEUE[:] = [w for w in _EQUIP_QUEUE if w is not st]
        if _EQUIP_FREEZE_COUNT == 0:
            EQUIP_WAIT_EVENT.set()
    _return_front_when_free(st)

def equip_freeze_reset():
    """停止時など強制リセット（LogMonitor/WindowStateは起動ごとに作り直される前提）"""
    global _EQUIP_FREEZE_COUNT
    with _EQUIP_FREEZE_LOCK:
        _EQUIP_FREEZE_COUNT = 0
        _EQUIP_QUEUE.clear()
        EQUIP_WAIT_EVENT.set()

def is_first_in_equip_queue(st) -> bool:
    """窓stが装備待ちの列の先頭か（張っていなければ False）"""
    with _EQUIP_FREEZE_LOCK:
        return bool(_EQUIP_QUEUE) and _EQUIP_QUEUE[0] is st

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
        _note_freeze(st, "速度検知", True, _SPEED_FREEZE_COUNT)
        SPEED_FREEZE_EVENT.clear()

def speed_freeze_end(st):
    """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）"""
    global _SPEED_FREEZE_COUNT
    with _SPEED_FREEZE_LOCK:
        if not st.speed_freeze_held:
            return
        st.speed_freeze_held = False
        _note_freeze(st, "速度検知", False, max(0, _SPEED_FREEZE_COUNT - 1))
        _SPEED_FREEZE_COUNT = max(0, _SPEED_FREEZE_COUNT - 1)
        if _SPEED_FREEZE_COUNT == 0:
            SPEED_FREEZE_EVENT.set()
    _return_front_when_free(st)

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
        _note_freeze(st, "ラウンド突入", True, _ROUND_FREEZE_COUNT)
        ROUND_FREEZE_EVENT.clear()

def round_freeze_end(st):
    """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）"""
    global _ROUND_FREEZE_COUNT
    with _ROUND_FREEZE_LOCK:
        if not st.round_freeze_held:
            return
        st.round_freeze_held = False
        _note_freeze(st, "ラウンド突入", False, max(0, _ROUND_FREEZE_COUNT - 1))
        _ROUND_FREEZE_COUNT = max(0, _ROUND_FREEZE_COUNT - 1)
        if _ROUND_FREEZE_COUNT == 0:
            ROUND_FREEZE_EVENT.set()
    _return_front_when_free(st)

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
#  set() = 通常動作可能、clear() = 続行ラウンド中（他窓をブロック）
# ═══════════════════════════════════════════════
CONTINUE_ROUND_EVENT = threading.Event()
CONTINUE_ROUND_EVENT.set()  # 初期値は通常動作可能
_CONTINUE_ROUND_COUNT = 0
_CONTINUE_ROUND_LOCK = threading.Lock()

def continue_round_start(st):
    """窓stを続行フリーズの保持者として登録（登録済みなら何もしない）。

    保持を窓ごとに持つのは、足していない窓が引くのを防ぐため。DTM/Waldo の窓は
    is_continue_round=True でもここを呼ばない（他窓を止めない仕様）ので、
    終了側が無条件に引くと他窓の本物のフリーズを解除してしまう。
    """
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        if st.continue_freeze_held:
            return
        st.continue_freeze_held = True
        _CONTINUE_ROUND_COUNT += 1
        _note_freeze(st, "続行", True, _CONTINUE_ROUND_COUNT)
        CONTINUE_ROUND_EVENT.clear()

def continue_round_end(st):
    """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        if not st.continue_freeze_held:
            return
        st.continue_freeze_held = False
        _note_freeze(st, "続行", False, max(0, _CONTINUE_ROUND_COUNT - 1))
        _CONTINUE_ROUND_COUNT = max(0, _CONTINUE_ROUND_COUNT - 1)
        if _CONTINUE_ROUND_COUNT == 0:
            CONTINUE_ROUND_EVENT.set()
    _return_front_when_free(st)

def continue_round_reset():
    """停止時など強制リセット"""
    global _CONTINUE_ROUND_COUNT
    with _CONTINUE_ROUND_LOCK:
        _CONTINUE_ROUND_COUNT = 0
        CONTINUE_ROUND_EVENT.set()

def get_continue_round_count() -> int:
    with _CONTINUE_ROUND_LOCK:
        return _CONTINUE_ROUND_COUNT


# ── このツールが掴んでいる窓 ───────────────────
# 前面が VRChat の窓かを見るために使う。クラス名では判定しないので、当ツールが
# 把握していない VRChat の窓は拾えない（依頼者の判断で割り切る）
_WINDOW_HWNDS: set = set()
_WINDOW_HWND_LOCK = threading.Lock()


def register_window_hwnd(hwnd: int):
    if not hwnd:
        return
    with _WINDOW_HWND_LOCK:
        _WINDOW_HWNDS.add(int(hwnd))


def clear_window_hwnds():
    with _WINDOW_HWND_LOCK:
        _WINDOW_HWNDS.clear()


def managed_hwnds() -> frozenset:
    with _WINDOW_HWND_LOCK:
        return frozenset(_WINDOW_HWNDS)


# ── 当ツール自身の窓 ─────────────────────────
# 録画中だけキャプチャから外すために覚える。**VRChat の窓（_WINDOW_HWNDS）とは
# 別に持つこと。** 混ぜると _vrchat_is_in_front() が当ツールの窓を VRChat と
# 誤認して、Begin が毎回フォールバックする
_OWN_WINDOWS: set = set()
_OWN_WINDOW_LOCK = threading.Lock()


def register_own_window(hwnd: int):
    if not hwnd:
        return
    with _OWN_WINDOW_LOCK:
        _OWN_WINDOWS.add(int(hwnd))


def unregister_own_window(hwnd: int):
    with _OWN_WINDOW_LOCK:
        _OWN_WINDOWS.discard(int(hwnd or 0))


def own_windows() -> frozenset:
    with _OWN_WINDOW_LOCK:
        return frozenset(_OWN_WINDOWS)


def nothing_frozen() -> bool:
    """どのフリーズも張られていないか。

    前面化してよいかの判断に使う。フリーズが張られている間、前面はそれを
    張った窓のものなので、種別を問わず譲る。見るのは
    ActionExecutor._wait_other_windows() と同じ4つ（あちらは待ち続ける作りで
    形が違うので、共有しない）。
    """
    return (EQUIP_WAIT_EVENT.is_set() and CONTINUE_ROUND_EVENT.is_set()
            and SPEED_FREEZE_EVENT.is_set() and ROUND_FREEZE_EVENT.is_set())
