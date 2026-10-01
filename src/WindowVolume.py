"""VRChat の窓ごとの音量を、窓の状態（続行・フリーズ窓・その他）で切り替える。

使うのは Windows の「アプリごとの音量」（音量ミキサーと同じ。Core Audio の音声
セッション）。VRChat 本体には触らない。依存を足さないよう ctypes で直接呼ぶ。
VRChat の窓はプロセスごとに別々の音声セッションを持つので、窓（＝プロセス）ごとに
変えられる（2026-09-30、6窓で確認）。

注意: ツールが落ちて元の音量に戻せなかったとき、Windows がアプリごとの音量を
覚えていて、次に起動した VRChat が小さい音量で始まる可能性がある（未確認）。
"""
import ctypes
import sys
import threading
from ctypes import POINTER, byref, c_float, c_int, c_long, c_uint, c_ulong, c_void_p, wintypes

import config
import DebugLog

# ── 分類 ───────────────────────────────────
CONTINUE = "continue"
FREEZE = "freeze"
OTHER = "other"
CATEGORY_LABELS = {CONTINUE: "続行", FREEZE: "フリーズ窓", OTHER: "その他"}


def category_of(st) -> str:
    """窓の分類。重なったら上が勝つ: 続行 ＞ フリーズ窓（フリーズを張った窓）＞ その他。

    続行の窓は続行フリーズも張っているが続行として扱う。Run と DTM/Waldo の続行は
    通常（その他）として扱う（依頼者 2026-09-30）。Run は突入フリーズを張っていても
    その他。DTM/Waldo の窓がアイテムロストなどでフリーズを張ったらフリーズ窓
    """
    if st.round_type == "Run":
        return OTHER
    if st.is_continue_round and not st.open_special_continue:
        return CONTINUE
    if st.equip_freeze_held or st.speed_freeze_held or st.round_freeze_held:
        return FREEZE
    return OTHER


def levels_from_settings(data: dict) -> tuple:
    """settings.json から (有効か, {分類: %}) を読む。無い・壊れた値は既定値"""
    def percent(key, default):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return int(min(100, max(0, round(value))))

    enabled = data.get("window_volume_enabled", config.DEFAULT_WINDOW_VOLUME_ENABLED)
    return (enabled if isinstance(enabled, bool) else config.DEFAULT_WINDOW_VOLUME_ENABLED,
            {CONTINUE: percent("window_volume_continue", config.DEFAULT_WINDOW_VOLUME_CONTINUE),
             FREEZE: percent("window_volume_freeze", config.DEFAULT_WINDOW_VOLUME_FREEZE),
             OTHER: percent("window_volume_other", config.DEFAULT_WINDOW_VOLUME_OTHER)})


# ── 音声の部分（Core Audio を ctypes で） ────────────────
# vtable の番号は IUnknown が 0〜2。
# IMMDeviceEnumerator.EnumAudioEndpoints(3) → IMMDeviceCollection.GetCount(3)/Item(4) →
# IMMDevice.Activate(3) → IAudioSessionManager2.GetSessionEnumerator(5) →
# IAudioSessionEnumerator.GetCount(3)/GetSession(4) → QueryInterface で
# IAudioSessionControl2.GetProcessId(14) と ISimpleAudioVolume.SetMasterVolume(3)/GetMasterVolume(4)
_ole32 = ctypes.WinDLL("ole32")          # OleDLL だと負の HRESULT で例外になるので WinDLL
_user32 = ctypes.WinDLL("user32")

COINIT_MULTITHREADED = 0x0
RPC_E_CHANGED_MODE = -2147417850         # 0x80010106: 別の方式で初期化済み（そのまま使える）
CLSCTX_ALL = 23
E_RENDER = 0
DEVICE_STATE_ACTIVE = 1


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16),
                ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str):
        g = cls()
        _ole32.CLSIDFromString(ctypes.c_wchar_p("{" + text + "}"), byref(g))
        return g


CLSID_MMDeviceEnumerator = _GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = _GUID.parse("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = _GUID.parse("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
# 別の値を書きがちなので注意（ISimpleAudioVolume）
IID_ISimpleAudioVolume = _GUID.parse("87CE5498-68D6-44E5-9215-6DA47EF883D8")

_local = threading.local()               # このスレッドで COM を初期化したか
last_error = None                        # 最後に起きた失敗（見張りが1回だけログに出す）


def _method(obj, index, *argtypes, restype=c_long):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])


def _release(obj):
    if obj:
        try:
            _method(obj, 2, restype=c_ulong)(obj)
        except Exception:
            DebugLog.exception("WindowVolume._release")
            pass


def _ensure_com() -> bool:
    """呼び出したスレッドで COM を初期化する（1回だけ）。使えなければ False"""
    if getattr(_local, "ready", False):
        return True
    hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    if hr < 0 and hr != RPC_E_CHANGED_MODE:
        _fail(f"COM を初期化できません（{hr & 0xFFFFFFFF:#010x}）")
        return False
    _local.ready = True
    _local.owned = hr >= 0               # S_OK / S_FALSE なら後で CoUninitialize する
    return True


def release_com():
    """このスレッドで初期化した COM を片付ける（見張りのスレッドの終わりに呼ぶ）"""
    if getattr(_local, "ready", False) and getattr(_local, "owned", False):
        _ole32.CoUninitialize()
    _local.ready = False
    _local.owned = False


def _fail(message: str):
    global last_error
    last_error = message


def _each_volume(visit):
    """全ての有効な出力デバイスの音声セッションについて visit(pid, simple_volume) を呼ぶ。

    VRChat が既定でない出力に出していることがあるので、デバイスは全部見る。
    取った参照は必ず Release する。HRESULT が負なら、その1件を飛ばして続ける
    """
    if not _ensure_com():
        return
    enum = c_void_p()
    hr = _ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                 byref(IID_IMMDeviceEnumerator), byref(enum))
    if hr < 0:
        _fail(f"音声デバイスを列挙できません（{hr & 0xFFFFFFFF:#010x}）")
        return
    coll = c_void_p()
    try:
        if _method(enum, 3, c_int, c_uint, c_void_p)(
                enum, E_RENDER, DEVICE_STATE_ACTIVE, byref(coll)) < 0:
            return
        count = c_uint()
        if _method(coll, 3, c_void_p)(coll, byref(count)) < 0:
            return
        for d in range(count.value):
            _each_device_volume(coll, d, visit)
    finally:
        _release(coll)
        _release(enum)


def _each_device_volume(coll, index, visit):
    dev, mgr, sessions = c_void_p(), c_void_p(), c_void_p()
    try:
        if _method(coll, 4, c_uint, c_void_p)(coll, index, byref(dev)) < 0:
            return
        if _method(dev, 3, c_void_p, c_uint, c_void_p, c_void_p)(
                dev, byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, byref(mgr)) < 0:
            return
        if _method(mgr, 5, c_void_p)(mgr, byref(sessions)) < 0:
            return
        count = c_int()
        if _method(sessions, 3, c_void_p)(sessions, byref(count)) < 0:
            return
        for i in range(count.value):
            _each_session_volume(sessions, i, visit)
    finally:
        _release(sessions)
        _release(mgr)
        _release(dev)


def _each_session_volume(sessions, index, visit):
    ctl, ctl2, vol = c_void_p(), c_void_p(), c_void_p()
    try:
        if _method(sessions, 4, c_int, c_void_p)(sessions, index, byref(ctl)) < 0:
            return
        if _method(ctl, 0, c_void_p, c_void_p)(
                ctl, byref(IID_IAudioSessionControl2), byref(ctl2)) < 0:
            return
        pid = wintypes.DWORD()
        if _method(ctl2, 14, c_void_p)(ctl2, byref(pid)) < 0:
            return
        if _method(ctl, 0, c_void_p, c_void_p)(
                ctl, byref(IID_ISimpleAudioVolume), byref(vol)) < 0:
            return
        visit(pid.value, vol)
    finally:
        _release(vol)
        _release(ctl2)
        _release(ctl)


def read_volumes(pids) -> dict:
    """{pid: 音量(0.0〜1.0)}。セッションが無いプロセスは入れない。例外は外へ出さない"""
    wanted = set(pids)
    out = {}

    def visit(pid, vol):
        if pid in wanted and pid not in out:
            level = c_float()
            if _method(vol, 4, c_void_p)(vol, byref(level)) >= 0:
                out[pid] = float(level.value)

    try:
        _each_volume(visit)
    except Exception as e:
        DebugLog.exception("WindowVolume.visit")
        _fail(f"音量を読めません（{e}）")
    return out


def set_volume(pid: int, level: float) -> bool:
    """そのプロセスの全セッションに音量を設定する。1つでも設定できたら True"""
    level = min(1.0, max(0.0, float(level)))
    done = []

    def visit(p, vol):
        if p == pid and _method(vol, 3, c_float, c_void_p)(vol, level, None) >= 0:
            done.append(p)

    try:
        _each_volume(visit)
    except Exception as e:
        DebugLog.exception("WindowVolume.visit")
        _fail(f"音量を変えられません（{e}）")
    return bool(done)


def pid_of(hwnd: int) -> int:
    """窓のプロセス ID。取れなければ 0"""
    pid = wintypes.DWORD()
    try:
        _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), byref(pid))
    except Exception:
        DebugLog.exception("WindowVolume.pid_of")
        return 0
    return int(pid.value)


def take_error():
    """最後の失敗を取り出して消す"""
    global last_error
    error, last_error = last_error, None
    return error


# ── 見張り ─────────────────────────────────
class VolumeController:
    """WINDOW_VOLUME_POLL_SEC ごとに、各窓の分類に合わせて音量を設定する。

    windows() は [(窓番号, hwnd, WindowState)] を返す関数。設定値は GUI から
    set_enabled() / set_levels() で渡す（Tk 変数を裏のスレッドから読まない）。
    audio は read_volumes / set_volume / pid_of / take_error を持つもの（テストで差し替える）
    """

    def __init__(self, windows, log, audio=None):
        self._windows = windows
        self._log = log
        self._audio = audio or sys.modules[__name__]
        self._lock = threading.Lock()
        self._enabled = config.DEFAULT_WINDOW_VOLUME_ENABLED
        self._levels = {CONTINUE: config.DEFAULT_WINDOW_VOLUME_CONTINUE,
                        FREEZE: config.DEFAULT_WINDOW_VOLUME_FREEZE,
                        OTHER: config.DEFAULT_WINDOW_VOLUME_OTHER}
        self._applied: dict = {}     # pid → 最後に設定した %（同じなら触らない）
        self._original: dict = {}    # pid → 初めて設定する直前の音量（0.0〜1.0）
        self._warned = False         # 使えないことを告げたか
        self._stop_event = threading.Event()
        self._thread = None

    # ── GUI から ──
    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = bool(enabled)

    def set_levels(self, continue_=None, freeze=None, other=None):
        with self._lock:
            for key, value in ((CONTINUE, continue_), (FREEZE, freeze), (OTHER, other)):
                if value is not None:
                    self._levels[key] = int(min(100, max(0, round(value))))

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        """見張りを止め、元の音量に戻す（戻すのは見張りのスレッド。COM を同じ所で使う）"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    # ── 裏のスレッド ──
    def _run(self):
        try:
            while not self._stop_event.is_set():
                self._tick_safely()
                self._stop_event.wait(config.WINDOW_VOLUME_POLL_SEC)
            self._restore_safely("[停止] ")
        finally:
            release = getattr(self._audio, "release_com", None)
            if release is not None:
                release()

    def _tick_safely(self):
        try:
            self.tick()
        except Exception as e:
            DebugLog.exception("WindowVolume._tick_safely")
            self._warn(str(e))
        self._warn(self._audio.take_error())

    def _restore_safely(self, prefix: str):
        try:
            self.restore(prefix)
        except Exception as e:
            DebugLog.exception("WindowVolume._restore_safely")
            self._warn(str(e))

    def _warn(self, error):
        if error and not self._warned:
            self._warned = True
            self._log(f"⚠ VRChat の音量を変えられません（{error}）")

    def tick(self):
        """1回分。OFF なら（覚えている音量があれば戻して）何もしない"""
        with self._lock:
            enabled = self._enabled
            levels = dict(self._levels)
        if not enabled:
            if self._original:
                self.restore("")         # 動作中に OFF にした → その場で戻す
            return
        for idx, hwnd, st in self._windows():
            pid = self._audio.pid_of(hwnd)
            if not pid:
                continue
            category = category_of(st)
            target = levels[category]
            if self._applied.get(pid) == target:
                continue                  # 変わっていない（手で変えた音量も上書きしない）
            original = self._original.get(pid)
            if original is None:
                current = self._audio.read_volumes([pid])
                if pid not in current:
                    continue              # まだ音を出していない。次の回にまた試す
                original = current[pid]
            if not self._audio.set_volume(pid, target / 100):
                continue
            self._original.setdefault(pid, original)
            self._applied[pid] = target
            self._log(f"[窓{idx}] VRChat の音量 → {CATEGORY_LABELS[category]} {target}%")

    def restore(self, prefix: str = "[停止] "):
        """覚えている元の音量に戻す。戻すのは設定した窓だけ・まだ音声セッションが
        ある（生きている）プロセスだけ。戻し終えたら覚えを消す"""
        original, self._original, self._applied = self._original, {}, {}
        if not original:
            return
        alive = self._audio.read_volumes(list(original))
        restored = sum(1 for pid, level in original.items()
                       if pid in alive and self._audio.set_volume(pid, level))
        self._log(f"{prefix}VRChat の音量を元に戻しました（{restored}窓）")
