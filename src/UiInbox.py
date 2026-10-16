"""別のスレッドから Tk のスレッドへ「やってほしい関数」を渡す受付箱（FC。依頼者 2026-10-15「メモリが 50MB → 200MB」）。

Tk のスレッドでないスレッドが _tkinter を呼ぶ（after(0) を含む）と、そのスレッド 1 本ごとに Tcl の Event 1 個と
スレッドごとのデータ（約 17KB）が残る（Tcl の DllMain が DisableThreadLibraryCalls なので、Python のスレッドの終わりで
閉じられない）。v1.2.0 の 12 時間で Event 46,920 個・Private 980MB。

別のスレッドは put() で Python の queue に入れるだけ（Tk は呼ばない）。Tk のスレッドが after(INTERVAL_MS) ごとに
取り出して、入れた順に実行する（遅れは最大 INTERVAL_MS）。1 件ごとに例外を受け止めて debug.log に書き、次へ進む。
close() は残りを実行してから止める（停止の直前のログが画面に出るように）。
"""
import queue
import threading
import time
import tkinter as tk

import DebugLog
import TaskStats

INTERVAL_MS = 50


def call_soon(widget, func, *args):
    """Tk のスレッドで func を実行してもらう（どのスレッドからでも）。Tk のスレッド（受付箱を作ったスレッド）から
    呼んだら今までどおり after(0)、ほかのスレッドからは受付箱へ入れるだけ（Tk を呼ばない）。受付箱が無い
    （テストの偽の窓）なら after(0)。閉じた後の after は TclError（呼ぶ側が受け止める）"""
    inbox = getattr(widget, "_inbox", None)
    if inbox is None or threading.current_thread() is inbox.owner:
        widget.after(0, func, *args)
    else:
        inbox.put(func, *args)


class UiInbox:
    def __init__(self, widget, interval_ms: int = INTERVAL_MS):
        self._widget = widget
        self._interval = interval_ms
        self._queue = queue.SimpleQueue()
        self._closed = False
        self._after_id = None
        self.owner = threading.current_thread()     # Tk のスレッド（窓を作ったスレッド）

    def start(self):
        """Tk のスレッドから呼ぶ"""
        self._schedule()

    def put(self, func, *args):
        """どのスレッドからでも。閉じた後は捨てる"""
        if not self._closed:
            self._queue.put((func, args))

    def _schedule(self):
        try:
            self._after_id = self._widget.after(self._interval, self._pump)
        except (tk.TclError, RuntimeError):
            self._closed = True             # 窓が無くなった

    def _pump(self):
        self._after_id = None
        self.drain()
        if not self._closed:
            self._schedule()

    def drain(self):
        """Tk のスレッドで、今入っている分を入れた順に実行する（実行中に増えた分は次の回）"""
        for _ in range(self._queue.qsize()):
            try:
                func, args = self._queue.get_nowait()
            except queue.Empty:
                return
            start = time.thread_time()
            try:
                func(*args)
            except Exception:
                DebugLog.exception(f"UiInbox: {getattr(func, '__qualname__', func)}")
            # 画面のスレッドで何が CPU を使ったか（FO）。名前は関数の名前（lambda は「<lambda>」）
            TaskStats.tk_account(getattr(func, "__name__", None) or type(func).__name__, time.thread_time() - start)

    def close(self):
        """Tk のスレッドから。残りを実行してから止める"""
        self.drain()
        self._closed = True
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except (tk.TclError, RuntimeError):
                pass
            self._after_id = None
