"""霧の看破（公開前に [NetworkProcessing] のオブジェクト名からテラーを特定する）の道具。

- 窓のログが看破できる起動方法か（先頭に --enable-sdk-log-levels があるか）
- 名前ごとの答え合わせの記録（一度でも公開と食い違った名前は以後使わない）

使い分け（判定に使うか・DBにだけ送るか）は LogMonitor._identify_fog_terror() の
入口で決める。ここには置かない。
"""
import json
import threading
from pathlib import Path

import config
import LogParser

LOG_HEAD_BYTES = 8192       # 起動引数はログの先頭付近に出る（VRChatDiscovery と同じ）

# 最初に当たった名前で、その場で看破する（保留しない）。
# 以前は参加直後などの全オブジェクトの同期（1秒に100種類以上の名前が出る）に備えて
# 最初の名前を2秒保留していたが、判明が毎回2〜3秒遅れていた。依頼者の直近8本の
# ログ（2026-09-29）では、霧368回・名前が見えた324回のうち、最初の名前から3秒以内に
# 別のテラーの名前が混ざった回は0。洪水（1秒に755〜792行）は参加直後だけで、
# 霧ラウンドの最中には一度も起きていない（最初の名前の秒は2〜7行、中央2）。
# 残るリスク（依頼者了承済み）: 洪水の最中に霧ラウンドが始まっていると、適当な名前で
# 決めてしまう。決めた後に別の名前が来ればそのラウンドの看破は void にするが、
# 判定（自爆・続行）はもう動いているので取り消せない。名前ごとの答え合わせ（trust）が
# 次のラウンドから外すのは、公開と食い違った名前だけ

# 看破してよい公開範囲（依頼者の決定 2026-10-01）。Friends・Invite+・Invite だけ。
# さらに、ツール全体のボタン（set_early_read_enabled）で許可したときだけ看破する。
# それ以外は NG と同じ（判定・表示には使わず、DB にだけ黙って送る）
EARLY_READ_ACCESS = frozenset({LogParser.ACCESS_FRIENDS, LogParser.ACCESS_INVITE_PLUS,
                               LogParser.ACCESS_INVITE})

_enabled_lock = threading.Lock()
_enabled = False        # 既定は切る（許可のボタン）。settings.json の fog_early_read_enabled


def set_early_read_enabled(enabled: bool):
    global _enabled
    with _enabled_lock:
        _enabled = bool(enabled)


def early_read_enabled() -> bool:
    with _enabled_lock:
        return _enabled


def early_read_allowed(access: str) -> bool:
    return early_read_enabled() and access in EARLY_READ_ACCESS


def launched_for_early_read(log_path) -> bool:
    """その窓のログの先頭 8KB に看破用の起動引数があるか。読めなければ False"""
    try:
        with open(log_path, "rb") as f:
            head = f.read(LOG_HEAD_BYTES)
    except Exception:
        return False
    return config.FOG_EARLY_READ_LAUNCH_FLAG.encode("ascii") in head


class NameTrust:
    """正規化した名前ごとの「一致 / 食い違い」の回数。全窓で1つ"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = None

    def _loaded(self) -> dict:
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}          # 無い・壊れている → 空として扱う
            self._data = {k: v for k, v in raw.items()
                          if isinstance(k, str) and isinstance(v, dict)} \
                if isinstance(raw, dict) else {}
        return self._data

    def usable(self, key: str) -> bool:
        """一度でも食い違った名前は使わない"""
        with self._lock:
            entry = self._loaded().get(key) or {}
        try:
            return int(entry.get("mismatch", 0)) == 0
        except (TypeError, ValueError):
            return False

    def counts(self, key: str) -> tuple[int, int]:
        with self._lock:
            entry = self._loaded().get(key) or {}
        return int(entry.get("match", 0) or 0), int(entry.get("mismatch", 0) or 0)

    def record(self, key: str, matched: bool):
        with self._lock:
            data = self._loaded()
            entry = data.setdefault(key, {})
            field = "match" if matched else "mismatch"
            try:
                entry[field] = int(entry.get(field, 0) or 0) + 1
            except (TypeError, ValueError):
                entry[field] = 1
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
            except Exception:
                pass              # 書けなくても判定は止めない（メモリ上には残る）


trust = NameTrust(config.FOG_OBJECT_NAMES_PATH)
