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
    with _EQUIP.lock:
        if not _EQUIP.queue:
            return None
        head = _EQUIP.queue[0]
    if head is st or not head.equip_front_hwnd:
        return None
    return head


def _start_give_back(loan):
    threading.Thread(target=_give_back, args=(loan,), daemon=True).start()


def _give_back(loan):
    with _GLOBAL_ACTION_LOCK:
        loan.give_back()

# ═══════════════════════════════════════════════
#  全窓共通の設定（鍵つきの値1つ）
# ═══════════════════════════════════════════════
class _Setting:
    """どのスレッドから読み書きしてもよい値1つ。convert は書くときに通す（型をそろえる・検証する）"""

    def __init__(self, value, convert=None):
        self._lock = threading.Lock()
        self._convert = convert or (lambda v: v)
        self._value = self._convert(value)

    def get(self):
        with self._lock:
            return self._value

    def set(self, value):
        value = self._convert(value)
        with self._lock:
            self._value = value


# インスタンスタイプ（初期はパブリックを仮定）
_INSTANCE_TYPE = _Setting(config.INSTANCE_PUBLIC)
get_instance_type, set_instance_type = _INSTANCE_TYPE.get, _INSTANCE_TYPE.set

# 自爆キー（config の既定値。テストから差し替え可能）
_SUICIDE_KEY = _Setting(config.SELF_SUICIDE_KEY)
get_suicide_key, set_suicide_key = _SUICIDE_KEY.get, _SUICIDE_KEY.set

# 放置モード
_HANDS_FREE = _Setting(False)
get_hands_free, set_hands_free = _HANDS_FREE.get, _HANDS_FREE.set

# 速度によるラウンド種別の検知。判定（受信のみ）と横移動（マクロ）の両方をまとめて止められる
_SPEED_DETECT = _Setting(config.SPEED_DETECT_ENABLED)
get_speed_detect, set_speed_detect = _SPEED_DETECT.get, _SPEED_DETECT.set

# フリーズ設定（全窓共通）。フリーズ自体が全窓を止める仕組みなので、窓ごとに分ける意味がない
_FREEZE_ON_8PAGES = _Setting(False, bool)
get_freeze_on_8pages, set_freeze_on_8pages = _FREEZE_ON_8PAGES.get, _FREEZE_ON_8PAGES.set
_FREEZE_ON_PUNISH = _Setting(False, bool)
get_freeze_on_punish, set_freeze_on_punish = _FREEZE_ON_PUNISH.get, _FREEZE_ON_PUNISH.set
# 突入で全窓を止めるラウンド種別。読むときはコピーを返す（呼び出し側の書き換え防止）
_FREEZE_ROUNDS = _Setting(set(), lambda names: set(names or ()))


def get_freeze_rounds() -> set:
    return set(_FREEZE_ROUNDS.get())


set_freeze_rounds = _FREEZE_ROUNDS.set

# 続行リストの供給元（全窓共通）。"host" = ToN ListTool の主催リスト / "tnl" = tnlファイル / None = 未決定
_LIST_SOURCE = _Setting(None)
get_list_source, set_list_source = _LIST_SOURCE.get, _LIST_SOURCE.set

# アイテム自動取得（全窓共通・既定 OFF）
_ITEM_FETCH = _Setting(False, bool)
get_item_fetch, set_item_fetch = _ITEM_FETCH.get, _ITEM_FETCH.set

ITEM_FETCH_GAIN_MARK = "calib"  # calibrate で測った値の印（印の無い前の保存は読み捨てる）


def _checked_gain(gain):
    """(横, 縦, 送った間隔の秒, "calib") か None。数でない・範囲（ItemFetch.GAIN_SANE）の外・間隔が無い・
    測った値の印が無い（前の保存の形。合わせで直した値の可能性がある）は None（読み捨てる）"""
    try:
        *numbers, mark = gain
        x, y, step = (float(g) for g in numbers)
        if (mark == ITEM_FETCH_GAIN_MARK and all(0.05 <= g <= 20.0 for g in (x, y))
                and step > 0):
            return (x, y, step, ITEM_FETCH_GAIN_MARK)
    except (TypeError, ValueError):
        pass
    return None


# 測った視点の感度（横, 縦, 送った間隔, "calib"）。保存して次回に使う
_ITEM_FETCH_GAIN = _Setting(None, _checked_gain)
get_item_fetch_gain, set_item_fetch_gain = _ITEM_FETCH_GAIN.get, _ITEM_FETCH_GAIN.set

# アイテム取得→Beginモード
_ITEM_BEGIN_MODE = _Setting(False)
get_item_begin_mode, set_item_begin_mode = _ITEM_BEGIN_MODE.get, _ITEM_BEGIN_MODE.set

# 続行ラウンドの後にアイテムを落とす・窓を元の位置に戻す（全窓共通・既定 ON）
_CONTINUE_DROP_ITEM = _Setting(True, bool)
get_continue_drop_item, set_continue_drop_item = _CONTINUE_DROP_ITEM.get, _CONTINUE_DROP_ITEM.set
_CONTINUE_RESTORE_WINDOW = _Setting(True, bool)
get_continue_restore_window, set_continue_restore_window = (_CONTINUE_RESTORE_WINDOW.get,
                                                            _CONTINUE_RESTORE_WINDOW.set)


# ═══════════════════════════════════════════════
#  全窓フリーズ（装備待ち・速度検知・ラウンド突入・続行）
#  event: set() = 通常動作可能、clear() = どれかの窓が張っている（他窓のアクションをブロック）。
#  複数窓が同時に張っても全窓の解除が揃うまで維持するため、張っている窓の数で管理する。
#  event の clear/set は直接呼ばず start / end を使うこと。
#  窓ごとの多重登録・多重解除は WindowState の held 属性で防ぐ（足していない窓が引くと、
#  別の窓の本物のフリーズを解いてしまう）
# ═══════════════════════════════════════════════
def _note_freeze(st, kind: str, started: bool, count: int):
    """debug.log へ: どの窓がフリーズを張った／解いたか、いま何窓が張っているか"""
    DebugLog.write(f"[状態] 窓{getattr(st, 'window_idx', 0)} {kind}フリーズを"
                   f"{'張った' if started else '解いた'}（張っている窓: {count}）")


# マクロの回（開始・停止のたびに進む）。止めた後もしばらく動いている前の回のスレッドが、
# 新しい回のフリーズを張ったり解いたりしないように、窓（WindowState.run_id）と照らす
_RUN = _Setting(0)


def begin_run() -> int:
    """マクロの開始・停止のたびに（Tk のスレッドから）呼ぶ。全窓フリーズを全部解き、回を進める。
    以後、前の回の窓はフリーズを張れない・解けない（張っても数に入らない）"""
    for freeze in (_EQUIP, _SPEED, _ROUND, _CONTINUE):
        freeze.reset()
    run = _RUN.get() + 1
    _RUN.set(run)
    return run


def current_run() -> int:
    return _RUN.get()


def _stale(st) -> bool:
    """前の回の窓か（run_id を持たない窓＝テストなどは今の回とみなす）"""
    run = getattr(st, "run_id", None)
    return run is not None and run != _RUN.get()


class _Freeze:
    def __init__(self, kind: str, held_attr: str, keep_order: bool = False):
        self.kind = kind                    # debug.log に出す名前
        self.held_attr = held_attr          # WindowState の「この窓が張っているか」
        self.event = threading.Event()
        self.event.set()                    # 初期値は通常動作可能
        self.count = 0
        self.lock = threading.Lock()
        # 張った順（keep_order のときだけ）。足す・外す・読むは lock の中で行う。
        # 同じ瞬間に2窓が張っても鍵の順に並ぶので、順番は必ず決まる
        self.queue: list | None = [] if keep_order else None

    def start(self, st):
        """窓stを保持者として登録（登録済みなら何もしない）。前の回の窓は登録しない"""
        with self.lock:
            if _stale(st):
                return
            if getattr(st, self.held_attr):
                return
            setattr(st, self.held_attr, True)
            self.count += 1
            _note_freeze(st, self.kind, True, self.count)
            if self.queue is not None:
                self.queue.append(st)
            self.event.clear()

    def end(self, st):
        """窓stの保持を解除し、保持窓が0になったらフリーズ解除（未保持なら何もしない）。
        前の回の窓は印を落とすだけ（その回の数は begin_run で0にしてある。引くと今の回の数が狂う）"""
        with self.lock:
            if not getattr(st, self.held_attr):
                return
            if _stale(st):
                setattr(st, self.held_attr, False)
                return
            setattr(st, self.held_attr, False)
            _note_freeze(st, self.kind, False, max(0, self.count - 1))
            self._on_end(st)
            self.count = max(0, self.count - 1)
            if self.queue is not None:
                # 同一性で外す。WindowState は dataclass なので == は中身の比較
                self.queue[:] = [w for w in self.queue if w is not st]
            if self.count == 0:
                self.event.set()
        _return_front_when_free(st)

    def _on_end(self, st):
        """解くときに一緒に落とすもの（鍵の中で呼ぶ）"""

    def reset(self):
        """停止時など強制リセット（LogMonitor/WindowStateは起動ごとに作り直される前提）"""
        with self.lock:
            self.count = 0
            if self.queue is not None:
                self.queue.clear()
            self.event.set()

    def get_count(self) -> int:
        with self.lock:
            return self.count


class _EquipFreeze(_Freeze):
    def _on_end(self, st):
        st.equip_front_hwnd = 0             # 前面の引き継ぎも終わり


# 装備待ち。アイテムロストの前面化＋音声は張った順の先頭の窓だけが出す
# （利用者が一度に操作できるのは1窓なので、1窓ずつ案内する）
_EQUIP = _EquipFreeze("装備待ち", "equip_freeze_held", keep_order=True)
EQUIP_WAIT_EVENT = _EQUIP.event
_EQUIP_QUEUE = _EQUIP.queue
equip_freeze_start, equip_freeze_end = _EQUIP.start, _EQUIP.end
equip_freeze_reset, get_equip_freeze_count = _EQUIP.reset, _EQUIP.get_count


def is_first_in_equip_queue(st) -> bool:
    """窓stが装備待ちの列の先頭か（張っていなければ False）"""
    with _EQUIP.lock:
        return bool(_EQUIP.queue) and _EQUIP.queue[0] is st


# 速度検知（8 Pages / Punished）。適切なアイテム（スキャナー/ナッツ）を取りに行く時間を作る
_SPEED = _Freeze("速度検知", "speed_freeze_held")
SPEED_FREEZE_EVENT = _SPEED.event
speed_freeze_start, speed_freeze_end = _SPEED.start, _SPEED.end
speed_freeze_reset, get_speed_freeze_count = _SPEED.reset, _SPEED.get_count

# ラウンド突入（Alternate/Unbound/Ghost 等）。続行ラウンドと違い、張った窓自身の自爆は止めない
_ROUND = _Freeze("ラウンド突入", "round_freeze_held")
ROUND_FREEZE_EVENT = _ROUND.event
round_freeze_start, round_freeze_end = _ROUND.start, _ROUND.end
round_freeze_reset, get_round_freeze_count = _ROUND.reset, _ROUND.get_count

# 続行・霧ラウンド中。DTM/Waldo の窓は is_continue_round=True でも張らない（他窓を止めない仕様）
class _ContinueFreeze(_Freeze):
    """続行ラウンドのフリーズ。この窓が張った・外した瞬間に st.continue_hook(True / False) を呼ぶ
    （続行ラウンドの始まりに窓の位置を覚え、終わりにアイテムを落として窓を戻す。LogMonitor が入れる）。
    前の回の窓・止めたとき（reset）は呼ばない"""

    def start(self, st):
        was = st.continue_freeze_held
        super().start(st)
        if not was and st.continue_freeze_held:
            _call_continue_hook(st, True)

    def end(self, st):
        was = st.continue_freeze_held
        stale = _stale(st)
        super().end(st)
        if was and not st.continue_freeze_held and not stale:
            _call_continue_hook(st, False)


def _call_continue_hook(st, started: bool):
    hook = getattr(st, "continue_hook", None)
    if hook is None:
        return
    try:
        hook(started)
    except Exception:
        DebugLog.exception("SharedState._call_continue_hook")


_CONTINUE = _ContinueFreeze("続行", "continue_freeze_held")
CONTINUE_ROUND_EVENT = _CONTINUE.event
continue_round_start, continue_round_end = _CONTINUE.start, _CONTINUE.end
continue_round_reset, get_continue_round_count = _CONTINUE.reset, _CONTINUE.get_count


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


# 当ツールの窓をいま録画から外しているか。録画の途中で開いた窓（オーバーレイ・統計画面）も
# 開いたその場で外すため（外すのは録画の開始時に開いていた窓だけだった）
_OWN_WINDOWS_HIDDEN = _Setting(False, bool)
own_windows_hidden, set_own_windows_hidden = _OWN_WINDOWS_HIDDEN.get, _OWN_WINDOWS_HIDDEN.set


def nothing_frozen() -> bool:
    """どのフリーズも張られていないか。

    前面化してよいかの判断に使う。フリーズが張られている間、前面はそれを
    張った窓のものなので、種別を問わず譲る。見るのは
    ActionExecutor._wait_other_windows() と同じ4つ（あちらは待ち続ける作りで
    形が違うので、共有しない）。
    """
    return (EQUIP_WAIT_EVENT.is_set() and CONTINUE_ROUND_EVENT.is_set()
            and SPEED_FREEZE_EVENT.is_set() and ROUND_FREEZE_EVENT.is_set())
