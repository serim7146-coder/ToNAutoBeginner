"""ToN Utils の続行の判断を読む（EB）。

- Utils のログ（%APPDATA%\\ton-syncskip\\ton-utils.log）の後ろに増えた分を読む（ファイルが小さくなったら最初から）
  - `auto self-destruct evaluated source=<32桁> … action="press"` … Utils が自爆する
  - `… action="hold"` … 押さない（その窓で自爆を許可されていない）
  - 続行するラウンドでは何も書かない
  - `self-destruct permission changed source=… allowed=true|false`
  - `ton_gui: started log=…` … Utils の起動。ここで「この起動の後に見た窓」を忘れる
- source → 窓: save-history.sqlite の `logs` 表の source・started。started は VRChat のログのファイル名の日時
  （`output_log_<started>.txt`）。**sqlite は読み取り専用（`file:…?mode=ro`）で開き、`logs` の source・started
  だけを読む**（ほかの表にほかのプレイヤーの名前・セーブコードがある。写しも取らない）
- Utils が動いているか: プロセス名の英数字だけを見て `tonutils` を含むか（実機は `ToN-Utils.exe`。2026-10-12）

press・hold は、Utils のログの行の頭の時刻（UTC）で持つ。起点（VRChat のログの killers_set・killers_revealed
の行の時刻）と比べる（EB2）。このツールが行を読んだ時刻では比べない——読むのが遅れると、起点を処理した
時にはもう press がファイルにあり、それを「前の行」として捨ててスキップを続行と取り違えたため
"""
import datetime
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

import DebugLog
import ProcessCheck

UTILS_DIR = Path(os.environ.get("APPDATA", ".")) / "ton-syncskip"
LOG_PATH = UTILS_DIR / "ton-utils.log"
DB_PATH = UTILS_DIR / "save-history.sqlite"
PROCESS_KEY = "tonutils"
PROCESS_CACHE_SEC = 2.0
DB_RETRY_SEC = 5.0

RE_EVALUATED = re.compile(r"auto self-destruct evaluated source=([0-9a-f]+).*?action=\"(\w+)\"")
RE_PERMISSION = re.compile(r"self-destruct permission changed source=([0-9a-f]+).*?allowed=(\w+)")
RE_STARTED = re.compile(r"\bton_gui: started\b")
RE_UTC = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?Z")
RE_LOG_NAME = re.compile(r"output_log_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")

REASON_NOT_RUNNING = "ToN Utils が動いていない"
REASON_NO_SOURCE = "この窓のセーブの記録が見つからない"
REASON_NOT_SEEN = "この Utils の起動の後に、この窓の行を見ていない"


def utc_epoch(line: str) -> float | None:
    """Utils のログの行の頭の時刻（UTC。`2026-10-10T00:00:27.900141Z`）を epoch 秒で。無ければ None"""
    m = RE_UTC.match(line or "")
    if not m:
        return None
    try:
        whole = datetime.datetime(*map(int, m.groups()[:6]), tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None
    fraction = m.group(7)
    return whole + (int(fraction) / 10 ** len(fraction) if fraction else 0.0)


def normalize_process(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def started_key(log_path) -> str | None:
    """VRChat のログのファイル名から started を比べる形（"2026-10-10 06-16-40"）に"""
    m = RE_LOG_NAME.search(Path(str(log_path)).name) if log_path else None
    return f"{m.group(1)} {m.group(2)}" if m else None


def normalize_started(value) -> str:
    """logs.started（"2026-10-10T06:16:40" など）を比べる形に"""
    return str(value).replace("T", " ").replace(":", "-")


class UtilsWatch:
    def __init__(self, log_path=LOG_PATH, db_path=DB_PATH, running_names=None, clock=time.monotonic):
        self.log_path = Path(log_path)
        self.db_path = Path(db_path)
        self._running_names = running_names or ProcessCheck.running_names
        self._clock = clock
        self._lock = threading.Lock()
        self._offset = None             # None: まだ読んでいない（最初の読み込みは昔の行。動きとしては数えない）
        self._rest = b""
        self.seen = set()               # この Utils の起動の後に press・hold・allowed=true を見た source
        self.events = []                # (Utils の時刻 epoch, source, action)。最初の読み込みの分も入れる
        self._sources = {}              # started の形 → source
        self._db_tried = None
        self._proc = (None, False)      # (見た時刻, 動いているか)

    # ── ログ ──
    def refresh(self):
        """後ろに増えた分を読む。ファイルが小さくなったら最初から（Utils の入れ直しなど）"""
        with self._lock:
            try:
                size = self.log_path.stat().st_size
            except OSError:
                return
            first = self._offset is None
            if first or size < self._offset:
                self._offset = 0
                self._rest = b""
                self.seen.clear()
            if size == self._offset:
                return
            try:
                with open(self.log_path, "rb") as f:
                    f.seek(self._offset)
                    data = f.read(size - self._offset)
            except OSError:
                return
            self._offset += len(data)
            lines = (self._rest + data).split(b"\n")
            self._rest = lines.pop()
            for raw in lines:
                self._take(raw.decode("utf-8", "replace"))

    def _take(self, line: str):
        if RE_STARTED.search(line):
            self.seen.clear()           # Utils の起動。前の起動で見た窓は数えない
            return
        m = RE_EVALUATED.search(line)
        if m:
            source, action = m.group(1), m.group(2)
            self.seen.add(source)
            at = utc_epoch(line)
            if at is not None:
                self.events.append((at, source, action))
                del self.events[:-500]
            return
        m = RE_PERMISSION.search(line)
        if m and m.group(2) == "true":
            self.seen.add(m.group(1))

    def action_since(self, source: str, since: float) -> tuple:
        """Utils の時刻が since（epoch 秒）以後の、その source の最初の press・hold を (action, 時刻) で。
        無ければ (None, None)。すでにファイルにあった行も数える"""
        with self._lock:
            for at, src, action in self.events:
                if at >= since and src == source and action in ("press", "hold"):
                    return action, at
        return None, None

    # ── プロセス ──
    def running(self) -> bool:
        checked, value = self._proc
        now = self._clock()
        if checked is not None and now - checked < PROCESS_CACHE_SEC:
            return value
        names = self._running_names()
        value = bool(names) and any(PROCESS_KEY in normalize_process(n) for n in names)
        self._proc = (now, value)
        return value

    # ── source ↔ 窓 ──
    def source_for(self, log_path) -> str | None:
        key = started_key(log_path)
        if key is None:
            return None
        if key not in self._sources:
            now = self._clock()
            if self._db_tried is None or now - self._db_tried >= DB_RETRY_SEC:
                self._db_tried = now
                self._sources.update(self._read_sources())
        for started, source in self._sources.items():
            if started.startswith(key):
                return source
        return None

    def _read_sources(self) -> dict:
        """logs 表の source・started だけ（読み取り専用。読めなければ空。写しは取らない）"""
        if not self.db_path.exists():
            return {}
        uri = "file:" + self.db_path.as_posix() + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=1.0)
            try:
                rows = con.execute("SELECT source, started FROM logs").fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            DebugLog.write(f"[Utils] save-history を読めない（{type(e).__name__}）。少し後にもう一度")
            return {}
        return {normalize_started(started): str(source) for source, started in rows if source and started}

    def window_reason(self, log_path):
        """(source, 理由)。Utils がこの窓を判断するなら (source, None)"""
        if not self.running():
            return None, REASON_NOT_RUNNING
        source = self.source_for(log_path)
        if source is None:
            return None, REASON_NO_SOURCE
        self.refresh()
        if source not in self.seen:
            return source, REASON_NOT_SEEN
        return source, None


_watch = None
_watch_lock = threading.Lock()


def shared() -> UtilsWatch:
    global _watch
    with _watch_lock:
        if _watch is None:
            _watch = UtilsWatch()
        return _watch
