"""続行ラウンドと突入フリーズのラウンドを OBS で録画する。1本の録画を全窓で共有する。

- 録画開始（続行）: 続行と決まった瞬間（LogMonitor が continue_round_start(st) を呼ぶ直後）
- 録画開始（突入フリーズ）: 窓が突入フリーズに入った瞬間（round_freeze_start(st) の直後）
- 録画終了: 最後の窓の RoundOver から OBS_RECORD_TAIL_SEC 秒後（どちらで始めても同じ。
  フリーズの解除では止めない）
- このツールが始めた録画だけ止める。利用者が手で始めた録画は絶対に止めない
- 録画の間だけ、ツール専用のシーン（config.OBS_SCENE_NAME）へ切り替え、続行中の窓だけを
  並べる（1 窓は画面いっぱい・2 窓は左右・3 窓は横 3 分割・4 窓は 2×2）。窓の指定は
  ディスプレイキャプチャを窓の矩形で切り出す（クロップはシーンアイテムの変換）。
  録画の前のシーンは覚えておき、録画が終わったら必ず戻す。利用者が手で録画しているときは
  シーンの命令を 1 つも送らない。シーンやソースの操作が失敗しても録画は止めない

OBS との通信はすべて専用のワーカースレッドで行う。LogMonitor のスレッドからは
キューに投げるだけで待たない（OBS が落ちていても自爆・Begin・フリーズを遅らせない）。
"""
import queue
import threading
import time
from typing import Callable, Optional

import config
import DebugLog
import OBSClient
import RecordLayout
import TaskStats


class _Conn:
    """1 回の接続で複数のリクエストを送る。request は例外を投げない（失敗は戻り値で返す）"""

    def __init__(self, client_factory: Callable):
        self._factory = client_factory
        self._client = None
        self.reason = ""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def open(self) -> bool:
        try:
            self._client = self._factory()
        except Exception as e:
            DebugLog.exception("Recorder._Conn.open")
            self.reason = f"OBSクライアントを作れません（{e}）"
            return False
        try:
            ok, reason = self._client.connect()
        except Exception as e:
            DebugLog.exception("Recorder._Conn.open")
            ok, reason = False, f"OBSとの通信に失敗しました（{e}）"
        if not ok:
            self.reason = reason
        return ok

    def request(self, request_type: str, data: dict = None):
        try:
            if data is None:
                return self._client.request(request_type)
            return self._client.request(request_type, data)
        except Exception as e:
            DebugLog.exception("Recorder._Conn.request")
            return False, {}, f"OBSとの通信に失敗しました（{e}）"

    def close(self):
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            DebugLog.exception("Recorder._Conn.close")


class RecordPlan:
    """録画の状態と判断。スレッドを持たない（ワーカーから呼ばれる）。

    client_factory() は connect() / request() / close() を持つものを返す。
    """

    def __init__(self, client_factory: Callable, log: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic,
                 tail_sec: float = config.OBS_RECORD_TAIL_SEC,
                 max_sec: float = config.OBS_RECORD_MAX_SEC,
                 hide_windows: Callable[[bool], None] = None,
                 window_source: Callable[[int], tuple] = None):
        self._client_factory = client_factory
        self._log = log
        self._clock = clock
        self.tail_sec = tail_sec
        self.max_sec = max_sec
        self.active: set = set()                 # 続行中・突入フリーズ中で、まだ RoundOver+tail を迎えていない窓
        self.release_at: dict = {}               # 窓 → active から外す時刻
        self.we_started = False                  # このツールが録画を始めたか
        self.started_at: Optional[float] = None
        self._warned = ""                        # 最後に出した警告（同じものを連打しない）
        # 録画中に当ツールの窓を隠す関数（窓の操作はこのファイルでは行わない）
        self._hide_windows = hide_windows
        # 窓の番号 → (モニタの矩形, 窓の矩形) か None（窓の操作はこのファイルでは行わない）。
        # 渡されていなければシーンには一切触らない
        self._window_source = window_source
        self._previous_scene: Optional[str] = None   # 録画の前のシーン。録画が終わったら戻す
        self._in_scene = False                       # ツール専用のシーンへ切り替えているか
        self._scene_warned = False                   # この録画でシーンの警告を出したか（1 回だけ）
        self._input_kind: Optional[str] = None       # OBS に聞いた画面キャプチャの種類
        self._monitor_prop: Optional[str] = None     # モニタを選ぶ設定の鍵（OBS に聞いたもの）
        self._shown: tuple = ()                      # 今の並び（窓の番号）。変わったときだけログに出す

    # ── 窓からの知らせ ─────────────────────────
    def continue_start(self, window: int, label: str = "続行ラウンド"):
        """録画の開始。label は開始ログの（）の中身（続行ラウンド／突入フリーズ）"""
        self.active.add(window)
        self.release_at.pop(window, None)        # 停止の予約があれば取り消す
        if self.we_started:
            self._apply_layout()                 # 窓が増えた
            return
        ok, data, reason = self._call("GetRecordStatus")
        if not ok:
            self._warn(window, reason)
            return
        if data.get("outputActive"):
            # 利用者が手で録っている。触らない（止めもしない）
            self._note(f"[窓{window}] OBSはすでに録画中です → 手動の録画とみなし、開始も停止もしません")
            return
        self._scene_warned = False
        self._enter_scene()
        ok, _data, reason = self._call("StartRecord")
        if not ok:
            self._leave_scene()                  # 録画できなかったのに別のシーンに残さない
            self._warn(window, reason)
            return
        self.we_started = True
        self.started_at = self._clock()
        self._warned = ""
        self._hide_own_windows(True)
        self._log(f"[窓{window}] 🎥 OBS録画を開始しました（{label}）")
        self._apply_layout()

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
                else:
                    self._apply_layout()         # 残った窓で組み直す
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
        self._hide_own_windows(False)
        try:
            ok, _data, reason = self._call("StopRecord")
            if ok:
                self._log(f"🎥 OBS録画を停止しました（{why}）")
            else:
                self._log(f"⚠ OBS録画を停止できませんでした: {reason}")
        finally:
            self._leave_scene()         # 何があっても元のシーンへ戻す

    def _hide_own_windows(self, hidden: bool):
        """当ツールの窓を、録画の間だけ画面キャプチャから外す。

        実際に隠すのは外から渡された関数（このファイルは標準ライブラリと
        OBSClient だけで書く決まりなので、窓の操作は持ち込まない）。
        渡されていなければ何もしない
        """
        if not config.HIDE_OWN_WINDOWS_WHILE_RECORDING:
            return
        if self._hide_windows is None:
            return
        try:
            self._hide_windows(hidden)
        except Exception:
            DebugLog.exception("Recorder._hide_own_windows")
            pass                # 付随機能なので、失敗しても録画は続ける

    # ── 録画用のシーン ─────────────────────────
    def _collect_windows(self) -> list:
        """続行中の窓の [(窓の番号, モニタの矩形, 窓の矩形)]。窓の番号の小さい順。
        矩形が取れない窓（最小化・消えた）は入れない"""
        if self._window_source is None:
            return []
        out = []
        for idx in sorted(self.active):
            try:
                info = self._window_source(idx)
                if not info:
                    continue
                monitor, window = info
                if RecordLayout.crop_for(monitor, window) is None:
                    continue
            except Exception:
                DebugLog.exception("Recorder._collect_windows")
                continue
            out.append((idx, tuple(monitor), tuple(window)))
        return out

    def _warn_scene(self, reason: str):
        """シーンやソースの失敗は、この録画で 1 回だけ知らせる（録画は止めない）"""
        DebugLog.write(f"[OBS] シーン操作の失敗: {reason}")
        if self._scene_warned:
            return
        self._scene_warned = True
        self._log(f"⚠ OBSのシーンを組めません: {reason}（録画は今のシーンのまま続けます）")

    def _enter_scene(self) -> bool:
        """ツール専用のシーンへ切り替える。元のシーンは覚えておく。
        作れない・切り替えられないときは False（録画そのものは今までどおり続ける）"""
        self._in_scene = False
        self._previous_scene = None
        self._shown = ()
        try:
            if not self._collect_windows():
                return False                    # 並べる窓が無いなら、利用者のシーンのまま
            scene = config.OBS_SCENE_NAME
            with _Conn(self._client_factory) as conn:
                if not conn.open():
                    self._warn_scene(conn.reason)
                    return False
                ok, data, reason = conn.request("GetSceneList")
                if not ok:
                    self._warn_scene(reason)
                    return False
                names = {sc.get("sceneName") for sc in (data.get("scenes") or [])
                         if isinstance(sc, dict)}
                if scene not in names:
                    ok, _data, reason = conn.request("CreateScene", {"sceneName": scene})
                    if not ok:
                        self._warn_scene(reason)
                        return False
                ok, data, reason = conn.request("GetCurrentProgramScene")
                if not ok:
                    self._warn_scene(reason)
                    return False
                current = data.get("currentProgramSceneName") or data.get("sceneName")
                if not current:
                    self._warn_scene("今のシーンの名前が分かりません")
                    return False                # 戻し先が分からないまま切り替えない
                if current != scene:
                    ok, _data, reason = conn.request("SetCurrentProgramScene",
                                                     {"sceneName": scene})
                    if not ok:
                        self._warn_scene(reason)
                        return False
                    self._previous_scene = current
                # current == scene: 前回の取り残し。戻し先は分からないのでそのまま（戻さない）
                self._in_scene = True
                return True
        except Exception as e:
            DebugLog.exception("Recorder._enter_scene")
            self._warn_scene(f"想定外のエラー（{e}）")
            return False

    def _leave_scene(self):
        """録画の前のシーンへ戻す。切り替えていなければ何もしない。例外は外へ出さない"""
        previous, self._previous_scene = self._previous_scene, None
        self._in_scene = False
        self._shown = ()
        if not previous:
            return
        try:
            ok, _data, reason = self._call("SetCurrentProgramScene", {"sceneName": previous})
            if not ok:
                self._log(f"⚠ OBSのシーンを元に戻せませんでした: {reason}")
        except Exception:
            DebugLog.exception("Recorder._leave_scene")

    def _apply_layout(self):
        """続行中の窓のぶんだけソースを出し、格子に並べる。
        失敗しても録画は止めない（警告を 1 回出すだけ）"""
        if not self._in_scene or not self.active:
            return                              # 空なら並べ直さない（すぐ録画が止まる）
        try:
            self._apply_layout_inner()
        except Exception as e:
            DebugLog.exception("Recorder._apply_layout")
            self._warn_scene(f"想定外のエラー（{e}）")

    def _apply_layout_inner(self):
        windows = self._collect_windows()
        scene = config.OBS_SCENE_NAME
        with _Conn(self._client_factory) as conn:
            if not conn.open():
                self._warn_scene(conn.reason)
                return
            ok, data, _reason = conn.request("GetVideoSettings")
            canvas = config.OBS_CANVAS_FALLBACK
            if ok:
                w, h = data.get("baseWidth"), data.get("baseHeight")
                if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                    canvas = (w, h)
            ok, data, reason = conn.request("GetSceneItemList", {"sceneName": scene})
            if not ok:
                self._warn_scene(reason)
                return
            items = {it.get("sourceName"): it for it in (data.get("sceneItems") or [])
                     if isinstance(it, dict)}
            state = {"input_names": None, "monitors": None}
            shown = []
            frames = RecordLayout.cells(len(windows), canvas) if windows else []
            for (idx, monitor, window), cell in zip(windows, frames):
                name = RecordLayout.source_name(idx)
                transform = RecordLayout.item_transform(cell, monitor, window)
                item_id = self._ensure_scene_item(conn, scene, name, items, state)
                if transform is None or item_id is None:
                    continue
                if not self._select_monitor(conn, name, monitor, state):
                    continue
                ok, _data, reason = conn.request("SetSceneItemTransform", {
                    "sceneName": scene, "sceneItemId": item_id,
                    "sceneItemTransform": transform})
                if not ok:
                    self._warn_scene(reason)
                    continue
                ok, _data, reason = conn.request("SetSceneItemEnabled", {
                    "sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": True})
                if not ok:
                    self._warn_scene(reason)
                    continue
                shown.append(idx)
            # 並べなかった窓のソースは隠す（消さない。次に使う）
            shown_names = {RecordLayout.source_name(i) for i in shown}
            for name, item in items.items():
                if (not RecordLayout.is_tool_source(name) or name in shown_names
                        or item.get("sceneItemEnabled") is False
                        or item.get("sceneItemId") is None):
                    continue
                ok, _data, reason = conn.request("SetSceneItemEnabled", {
                    "sceneName": scene, "sceneItemId": item["sceneItemId"],
                    "sceneItemEnabled": False})
                if not ok:
                    self._warn_scene(reason)
        key = tuple(shown)
        if key and key != self._shown:
            cols, rows = RecordLayout.grid_for(len(key))
            self._log("🎥 録画の画面: " + "・".join(f"窓{i}" for i in key)
                      + f"（{cols}×{rows}）")
        self._shown = key

    def _ensure_scene_item(self, conn: "_Conn", scene: str, name: str, items: dict,
                           state: dict):
        """窓のソースがシーンに無ければ作る（入力ごと無ければ CreateInput）。
        sceneItemId を返す。作れなければ None（警告を出す）。作ったものは最初は隠しておく"""
        if name in items and items[name].get("sceneItemId") is not None:
            return items[name]["sceneItemId"]
        if state["input_names"] is None:
            ok, data, reason = conn.request("GetInputList")
            if not ok:
                self._warn_scene(reason)
                return None
            state["input_names"] = {i.get("inputName") for i in (data.get("inputs") or [])
                                    if isinstance(i, dict)}
        if name in state["input_names"]:
            ok, data, reason = conn.request("CreateSceneItem", {
                "sceneName": scene, "sourceName": name, "sceneItemEnabled": False})
        else:
            if self._input_kind is None:
                ok, data, reason = conn.request("GetInputKindList", {"unversioned": True})
                if not ok:
                    self._warn_scene(reason)
                    return None
                self._input_kind = RecordLayout.pick_input_kind(data.get("inputKinds"))
                if self._input_kind is None:
                    self._warn_scene("OBSに画面キャプチャ（monitor_capture）の種類がありません")
                    return None
            ok, data, reason = conn.request("CreateInput", {
                "sceneName": scene, "inputName": name, "inputKind": self._input_kind,
                "sceneItemEnabled": False})
            if ok:
                state["input_names"].add(name)
        if not ok:
            self._warn_scene(reason)
            return None
        item_id = data.get("sceneItemId")
        if item_id is None:
            self._warn_scene("sceneItemId が返りません")
            return None
        items[name] = {"sourceName": name, "sceneItemId": item_id, "sceneItemEnabled": False}
        return item_id

    def _select_monitor(self, conn: "_Conn", name: str, monitor: tuple, state: dict) -> bool:
        """ソースのモニタを、窓が載っているモニタにする。選択肢は OBS に聞く"""
        if state["monitors"] is None:
            found = None
            props = (self._monitor_prop,) if self._monitor_prop else ("monitor_id", "monitor")
            for prop in props:
                ok, data, _reason = conn.request("GetInputPropertiesListPropertyItems",
                                                 {"inputName": name, "propertyName": prop})
                if ok and data.get("propertyItems"):
                    found = (prop, data["propertyItems"])
                    break
            if found is None:
                self._warn_scene("OBSからモニタの選択肢が取れません（monitor_id）")
                return False
            self._monitor_prop = found[0]
            state["monitors"] = found
        prop, options = state["monitors"]
        value = RecordLayout.pick_monitor(options, monitor)
        if value is None:
            self._warn_scene(f"窓が載っているモニタをOBSの選択肢から選べません（{tuple(monitor)}）")
            return False
        ok, _data, reason = conn.request("SetInputSettings", {
            "inputName": name, "inputSettings": {prop: value}, "overlay": True})
        if not ok:
            self._warn_scene(reason)
            return False
        return True

    def _call(self, request_type: str, data: dict = None):
        try:
            client = self._client_factory()
        except Exception as e:
            DebugLog.exception("Recorder._call")
            return False, {}, f"OBSクライアントを作れません（{e}）"
        try:
            ok, reason = client.connect()
            if not ok:
                return False, {}, reason
            if data is None:
                return client.request(request_type)
            return client.request(request_type, data)
        except Exception as e:           # 偽物や将来の変更で投げても外へ出さない
            DebugLog.exception("Recorder._call")
            return False, {}, f"OBSとの通信に失敗しました（{e}）"
        finally:
            try:
                client.close()
            except Exception:
                DebugLog.exception("Recorder._call")
                pass

    def _warn(self, window: int, reason: str):
        """同じ理由は1回だけ出す。次の録画でまた試す"""
        if reason == self._warned:
            return
        self._warned = reason
        self._log(f"[窓{window}] ⚠ OBS録画を開始できません: {reason}（次の録画でまた試します）")

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
        self._record_continue = False       # 続行ラウンドを録画する
        self._record_freeze = False         # 突入フリーズのラウンドを録画する
        self._log: Callable[[str], None] = lambda _m: None
        self._hide_windows: Optional[Callable[[bool], None]] = None
        self._window_source: Optional[Callable[[int], tuple]] = None
        self._plan = RecordPlan(self._make_client, self._emit, clock,
                                hide_windows=self._call_hide_windows,
                                window_source=self._call_window_source)
        self._host = config.OBS_DEFAULT_HOST
        self._port = config.OBS_DEFAULT_PORT
        self._password = ""

    def set_window_hider(self, hide_windows: Callable[[bool], None]):
        """録画中に当ツールの窓を隠す関数を差し込む（GUI 側が用意する）"""
        with self._lock:
            self._hide_windows = hide_windows

    def set_window_source(self, window_source: Callable[[int], tuple]):
        """窓の番号から (モニタの矩形, 窓の矩形) か None を返す関数を差し込む（GUI 側が用意する）。
        差し込まれていなければ、録画中にシーンへは触らない"""
        with self._lock:
            self._window_source = window_source

    def _call_window_source(self, window: int):
        with self._lock:
            source = self._window_source
        return source(window) if source is not None else None

    def _call_hide_windows(self, hidden: bool):
        with self._lock:
            hider = self._hide_windows
        if hider is not None:
            hider(hidden)

    def configure(self, enabled: bool, host: str = config.OBS_DEFAULT_HOST,
                  port: int = config.OBS_DEFAULT_PORT, password: str = "",
                  log: Callable[[str], None] = None, record_freeze: bool = False):
        """enabled は続行ラウンドの録画、record_freeze は突入フリーズの録画（別々のスイッチ）"""
        with self._lock:
            self._record_continue = bool(enabled)
            self._record_freeze = bool(record_freeze)
            self._host = host or config.OBS_DEFAULT_HOST
            self._port = int(port or config.OBS_DEFAULT_PORT)
            self._password = password or ""
            if log is not None:
                self._log = log

    @property
    def enabled(self) -> bool:
        """どちらかの録画がONか（RoundOver を受け付けるか）"""
        return self._record_continue or self._record_freeze

    # ── 窓のスレッドから（待たない） ──────────────
    def on_continue_start(self, window: int):
        if self._record_continue:
            self._post(("start", window, "続行ラウンド"))

    def on_round_freeze_start(self, window: int):
        if self._record_freeze:
            self._post(("start", window, "突入フリーズ"))

    def on_round_over(self, window: int):
        if self.enabled:
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
                self._thread = TaskStats.thread("Recorder._run", self._run, daemon=True,
                                                thread_name="OBSRecorder")
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
                DebugLog.exception("Recorder._run")
                self._emit(f"⚠ OBS録画の処理でエラー: {e}")

    def _handle(self, item):
        kind, arg, *rest = item
        if kind == "start":
            self._plan.continue_start(arg, *rest)
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
            DebugLog.exception("Recorder._emit")
            pass


# プロセスで1つ。LogMonitor と mainGUI はこの関数を呼ぶ
_default = Recorder()
configure = _default.configure
on_continue_start = _default.on_continue_start
on_round_freeze_start = _default.on_round_freeze_start
on_round_over = _default.on_round_over
stop_all = _default.stop_all
set_window_hider = _default.set_window_hider
set_window_source = _default.set_window_source


def is_enabled() -> bool:
    return _default.enabled
