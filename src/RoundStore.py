"""統計画面 v1 の手元の保存（%APPDATA%\\ToNAutoBeginner\\rounds.sqlite。標準の sqlite3）。

DB から取ったラウンドと、自分が送ったラウンドを貯める。統計画面は差分だけ DB から
取り、集計はここで行う（DB に集計を頼まない。アクセスが多いときに重くならないように）。

- 表 rounds: DB の "ToNRounds" と同じ列（time は 2026-01-01 UTC からの秒）＋ origin
  （'db' = DB から取った／'own' = 自分が送ったときに書いた）。other_uids はカンマ区切り
- 一意は (time, transformed_uid, round, map_id, terror1)。同じアカウントを複数の窓で
  動かしていると、同じ uid の別々のラウンドがほぼ同じ秒に始まりうるので、時刻と uid
  だけでは足りない。取り直した行は上書き（other_uids の更新を取り込む）
- 表 meta: my_uids（このPCから送ったことのある transformed_uid。複数ありうる）・
  initial_done（初回の全件取得が済んだか）・synced_to（DB から取った行の最大の time）

どのスレッドから呼んでもよい（鍵）。失敗しても例外を外へ出さない（統計が取れない
だけで、ツールの動きは止めない）。
"""
import sqlite3
import threading
from pathlib import Path

import config

ORIGIN_DB = "db"
ORIGIN_OWN = "own"

_SCHEMA = """
create table if not exists rounds (
    time integer not null,
    round integer not null,
    map_id integer,
    terror1 integer,
    terror2 integer,
    terror3 integer,
    transformed_uid integer,
    other_uids text,
    origin text not null
);
create unique index if not exists rounds_key
    on rounds(time, ifnull(transformed_uid, -99999), round, ifnull(map_id, -1), ifnull(terror1, -1));
create index if not exists rounds_time on rounds(time);
create table if not exists meta (key text primary key, value text);
"""


def uids_text(uids) -> str | None:
    """other_uids（リスト）→ カンマ区切り。空なら None"""
    values = [str(int(u)) for u in (uids or []) if u is not None]
    return ",".join(values) if values else None


def uids_list(text) -> list[int]:
    """カンマ区切り → リスト"""
    if not text:
        return []
    out = []
    for part in str(text).split(","):
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


class RoundStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(config.ROUND_STORE_PATH)
        self._lock = threading.Lock()
        self._ready = False

    # ── 接続 ─────────────────────────────────
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=10)
        if not self._ready:
            con.executescript(_SCHEMA)
            self._ready = True
        return con

    def _run(self, work, default=None):
        """鍵の中で接続して work(con) を行う。失敗したら default"""
        try:
            with self._lock:
                con = self._connect()
                try:
                    with con:
                        return work(con)
                finally:
                    con.close()
        except Exception:
            return default

    # ── meta ─────────────────────────────────
    def get_meta(self, key: str, default=None):
        def work(con):
            row = con.execute("select value from meta where key = ?", (key,)).fetchone()
            return row[0] if row else default
        return self._run(work, default)

    def set_meta(self, key: str, value) -> bool:
        def work(con):
            con.execute("insert or replace into meta(key, value) values (?, ?)",
                        (key, None if value is None else str(value)))
            return True
        return bool(self._run(work, False))

    def my_uids(self) -> set[int]:
        return set(uids_list(self.get_meta("my_uids")))

    # ── 自分の行 ───────────────────────────────
    def add_own(self, time: int, round_id: int, map_id, t1, t2, t3, uid) -> bool:
        """自分が送ったラウンドを1行（origin own）。uid が無ければ貯めない
        （DB でも誰の行か分からないので、自分の分として扱えない）"""
        if uid is None:
            return False

        def work(con):
            con.execute(
                "insert or ignore into rounds(time, round, map_id, terror1, terror2, terror3,"
                " transformed_uid, other_uids, origin) values (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (int(time), int(round_id), map_id, t1, t2, t3, int(uid), ORIGIN_OWN))
            mine = set(uids_list(self._meta(con, "my_uids")))
            if int(uid) not in mine:
                mine.add(int(uid))
                con.execute("insert or replace into meta(key, value) values ('my_uids', ?)",
                            (uids_text(sorted(mine)),))
            return True
        return bool(self._run(work, False))

    @staticmethod
    def _meta(con, key):
        row = con.execute("select value from meta where key = ?", (key,)).fetchone()
        return row[0] if row else None

    def count(self) -> int:
        return int(self._run(lambda con: con.execute("select count(*) from rounds").fetchone()[0], 0))

    def rows(self) -> list[tuple]:
        """全部の行（テスト・確認用）。(time, round, map_id, t1, t2, t3, uid, other_uids, origin)"""
        return self._run(lambda con: con.execute(
            "select time, round, map_id, terror1, terror2, terror3, transformed_uid,"
            " other_uids, origin from rounds order by time, rowid").fetchall(), [])


_default = None
_default_lock = threading.Lock()


def default_store() -> RoundStore:
    """config.ROUND_STORE_PATH の置き場所の store（パスが変わったら作り直す）"""
    global _default
    with _default_lock:
        path = Path(config.ROUND_STORE_PATH)
        if _default is None or _default.path != path:
            _default = RoundStore(path)
        return _default
