"""続行ラウンドを OBS で録画する。1本の録画を全窓で共有する。

- 録画開始: 続行と決まった瞬間（LogMonitor が continue_round_start() を呼ぶ直後）
- 録画終了: 最後の窓の RoundOver から OBS_RECORD_TAIL_SEC 秒後
- このツールが始めた録画だけ止める。利用者が手で始めた録画は絶対に止めない

OBS との通信はすべて専用のワーカースレッドで行う。LogMonitor のスレッドからは
キューに投げるだけで待たない（OBS が落ちていても自爆・Begin・フリーズを遅らせない）。
"""
import queue
import threading
import time
from typing import Callable, Optional

import config
import OBSClient


class RecordPlan:
    """録画の状態と判断。スレッドを持たない（ワーカーから呼ばれる）。

    client_factory() は connect() / request() / close() を持つものを返す。
    """

    def __init__(self, client_factory: Callable, log: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic,
                 tail_sec: float = config.OBS_RECORD_TAIL_SEC,
                 max_sec: float = config.OBS_RECORD_MAX_SEC):
        self._client_factory = client_factory
        self._log = log
        self._clock = clock
        self.tail_sec = tail_sec
        self.max_sec = max_sec
        self.active: set = set()                 # 続行中で、まだ RoundOver+tail を迎えていない窓
        self.release_at: dict = {}               # 窓 → active から外す時刻
        self.we_started = False                  # このツールが録画を始めたか
        self.started_at: Optional[float] = None
        self._warned = ""                        # 最後に出した警告（同じものを連打しない）

    # ── 窓からの知らせ ─────────────────────────
    def continue_start(self, window: int):
        self.active.add(window)
        self.release_at.pop(window, None)        # 停止の予約があれば取り消す
        if self.we_started:
            return
        ok, data, reason = self._call("GetRecordStatus")
        if not ok:
            self._warn(window, reason)
            return
        if data.get("outputActive"):
            # 利用者が手で録っている。触らない（止めもしない）
            self._note(f"[窓{window}] OBSはすでに録画中です → 手動の録画とみなし、開始も停止もしません")
            return
        ok, _data, reason = self._call("StartRecord")
        if not ok:
            self._warn(window, reason)
            return
        self.we_started = True
        self.started_at = self._clock()
        self._warned = ""
        self._log(f"[窓{window}] 🎥 OBS録画を開始しました（続行ラウンド）")

    def round_over(self, window: int):
        if window in self.active:
            self.release_at[window] = self._clock() + self.tail_sec

    def tick(self):
        """時間で決まる処理（RoundOver+tail の到来・上限）"""
        now = self._clock()
        for window, at in list(self.release_at.items()):
            if now >= at:
                del self.release_at[window]
                self.active.discard(window)
                if not self.active:
                    self._stop("最後の続行ラウンドが終わりました")
        if (self.we_started and self.started_at is not None
                and now - self.started_at >= self.max_sec):
            self._stop(f"上限の{int(self.max_sec)}秒に達しました（RoundOverが来ていません）")
            self.active.clear()
            self.release_at.clear()

    def stop_all(self):
        """ツールの停止時。予約を捨て、自分が始めた録画だけ止める"""
        self.release_at.clear()
        self.active.clear()
        self._stop("マクロ停止")

    def next_deadline(self) -> Optional[float]:
        times = list(self.release_at.values())
        if self.we_started and self.started_at is not None:
            times.append(self.started_at + self.max_sec)
        return min(times) if times else None

    # ── 内部 ──────────────────────────────
    def _stop(self, why: str):
        if not self.we_started:
            return                  # 手動の録画・始められなかった録画は止めない
        self.we_started = False
        self.started_at = None
        ok, _data, reason = self._call("StopRecord")
        if ok:
            self._log(f"🎥 OBS録画を停止しました（{why}）")
        else:
            self._log(f"⚠ OBS録画を停止できませんでした: {reason}")

    def _call(self, request_type: str):
        try:
            client = self._client_factory()
        except Exception as e:
            return False, {}, f"OBSクライアントを作れません（{e}）"
        try:
            ok, reason = client.connect()
            if not ok:
                return False, {}, reason
            return client.request(request_type)
        except Exception as e:           # 偽物や将来の変更で投げても外へ出さない
            return False, {}, f"OBSとの通信に失敗しました（{e}）"
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _warn(self, window: int, reason: str):
        """同じ理由は1回だけ出す。次の続行でまた試す"""
        if reason == self._warned:
            return
        self._warned = reason
        self._log(f"[窓{window}] ⚠ OBS録画を開始できません: {reason}（次の続行ラウンドでまた試します）")

    def _note(self, msg: str):
        if msg != self._warned:
            self._warned = msg
            self._log(msg)


class Recorder:
    """プロセスで1つ。窓のスレッドからは投げるだけで待たない"""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 client_factory: Callable = None):
        self._clock = clock
        self._client_factory_override = client_factory
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._enabled = False
        self._log: Callable[[str], None] = lambda _m: None
        self._plan = RecordPlan(self._make_client, self._emit, clock)
        self._host = config.OBS_DEFAULT_HOST
        self._port = config.OBS_DEFAULT_PORT
        self._password = ""

    def configure(self, enabled: bool, host: str = config.OBS_DEFAULT_HOST,
                  port: int = config.OBS_DEFAULT_PORT, password: str = "",
                  log: Callable[[str], None] = None):
        with self._lock:
            self._enabled = bool(enabled)
            self._host = host or config.OBS_DEFAULT_HOST
            self._port = int(port or config.OBS_DEFAULT_PORT)
            self._password = password or ""
            if log is not None:
                self._log = log

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 窓のスレッドから（待たない） ──────────────
    def on_continue_start(self, window: int):
        if self._enabled:
            self._post(("start", window))

    def on_round_over(self, window: int):
        if self._enabled:
            self._post(("round_over", window))

    def stop_all(self, wait_sec: float = 0.0):
        """ツールの停止時。無効にした後でも、自分が始めた録画は止める。

        wait_sec > 0 なら StopRecord を送り終えるまで最大その秒数だけ待つ
        （アプリ終了でワーカーごと消える前に止めるため）。
        """
        if self._thread is None:
            return              # 一度も録画に関わっていない
        done = threading.Event()
        self._post(("stop_all", done))
        if wait_sec > 0:
            done.wait(wait_sec)

    # ── ワーカー ──────────────────────────────
    def _post(self, item):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="OBSRecorder",
                                                daemon=True)
                self._thread.start()
        self._queue.put(item)

    def _run(self):
        while True:
            deadline = self._plan.next_deadline()
            timeout = None if deadline is None else max(0.0, deadline - self._clock())
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = None
            try:
                if item is not None:
                    self._handle(item)
                self._plan.tick()
            except Exception as e:      # ワーカーが死ぬと以後録画できなくなる
                self._emit(f"⚠ OBS録画の処理でエラー: {e}")

    def _handle(self, item):
        kind, arg = item
        if kind == "start":
            self._plan.continue_start(arg)
        elif kind == "round_over":
            self._plan.round_over(arg)
        elif kind == "stop_all":
            try:
                self._plan.stop_all()
            finally:
                arg.set()

    def _make_client(self):
        if self._client_factory_override is not None:
            return self._client_factory_override()
        with self._lock:
            host, port, password = self._host, self._port, self._password
        return OBSClient.OBSClient(host, port, password)

    def _emit(self, msg: str):
        try:
            self._log(msg)
        except Exception:
            pass


# プロセスで1つ。LogMonitor と mainGUI はこの関数を呼ぶ
_default = Recorder()
configure = _default.configure
on_continue_start = _default.on_continue_start
on_round_over = _default.on_round_over
stop_all = _default.stop_all


def is_enabled() -> bool:
    return _default.enabled
