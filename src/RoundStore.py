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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Filter:
    """集計の条件。時刻は DB と同じ（2026-01-01 UTC からの秒）。rounds は番号の集合
    （None なら絞らない）。mine は「自分の分」（1人目が自分か、other_uids に自分がいる行）"""
    start: int | None = None
    end: int | None = None
    rounds: frozenset | None = None
    mine: bool = False


# テラーの3列をまとめて1列として数えるための部分問い合わせ
_TERRORS = ("select terror1 as tid, map_id, round, time from f where terror1 is not null "
            "union all select terror2, map_id, round, time from f where terror2 is not null "
            "union all select terror3, map_id, round, time from f where terror3 is not null")
# 見た人数（1人目＋other_uids の数）
_PLAYERS = ("case when other_uids is null or other_uids = '' then 1 "
            "else 2 + length(other_uids) - length(replace(other_uids, ',', '')) end")
# ローカル時刻（time は 2026-01-01 UTC からの秒）
_LOCAL = "datetime(time + {epoch}, 'unixepoch', 'localtime')"


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

    # ── DB との同期 ─────────────────────────────
    # 直近15分は取り直す（マルチで後から uid が足されるため）
    RESYNC_BACK_SEC = 900
    # 自分が2人目以降だった DB の行と、手元の own の行を同じラウンドとみなす開始の差
    OWN_MATCH_SEC = 15

    def sync(self, fetch) -> bool:
        """DB から差分を取って手元に入れる。fetch(since, my_uids) は ConnectDB.fetch_rounds。

        初回（または my_uids に新しい uid が増えた）は全件（自分の行も含む）。以降は
        time >= synced_to − 15分 の、1人目が自分でない行か other_uids のある行だけ。
        取った行は上書きで入れ、other_uids に自分がいる行と重なる own の行は消す
        （DB の行が正。自分は2人目以降だった）。通信の失敗は False（手元はそのまま）
        """
        mine = self.my_uids()
        synced_uids = set(uids_list(self.get_meta("synced_uids")))
        full = self.get_meta("initial_done") != "1" or not mine <= synced_uids
        synced_to = self.get_meta("synced_to")
        try:
            if full or synced_to is None:
                rows = fetch(None, set())
            else:
                rows = fetch(int(synced_to) - self.RESYNC_BACK_SEC, mine)
        except Exception:
            return False

        def work(con):
            newest = None if synced_to is None else int(synced_to)
            for row in rows:
                others = [u for u in (row.get("other_uids") or []) if u is not None]
                values = (int(row["time"]), int(row["round"]), row.get("map_id"),
                          row.get("terror1"), row.get("terror2"), row.get("terror3"),
                          row.get("transformed_uid"), uids_text(others), ORIGIN_DB)
                con.execute(
                    "insert or replace into rounds(time, round, map_id, terror1, terror2,"
                    " terror3, transformed_uid, other_uids, origin)"
                    " values (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
                for uid in set(others) & mine:
                    con.execute(
                        "delete from rounds where origin = ? and transformed_uid = ?"
                        " and abs(time - ?) <= ? and round = ? and map_id is ? and terror1 is ?",
                        (ORIGIN_OWN, uid, values[0], self.OWN_MATCH_SEC, values[1],
                         values[2], values[3]))
                newest = values[0] if newest is None else max(newest, values[0])
            con.execute("insert or replace into meta(key, value) values ('initial_done', '1')")
            con.execute("insert or replace into meta(key, value) values ('synced_uids', ?)",
                        (uids_text(sorted(mine)),))
            if newest is not None:
                con.execute("insert or replace into meta(key, value) values ('synced_to', ?)",
                            (str(newest),))
            return True
        return bool(self._run(work, False))

    # ── 集計（SQLite の GROUP BY。行を Python の辞書にしない）──────
    def _where(self, con, flt: Filter):
        clauses, params = [], []
        if flt.start is not None:
            clauses.append("time >= ?")
            params.append(int(flt.start))
        if flt.end is not None:
            clauses.append("time <= ?")
            params.append(int(flt.end))
        if flt.rounds is not None:
            ids = sorted(int(r) for r in flt.rounds)
            clauses.append(f"round in ({','.join('?' * len(ids))})" if ids else "0")
            params.extend(ids)
        if flt.mine:
            mine = sorted(uids_list(self._meta(con, "my_uids")))
            if not mine:
                clauses.append("0")
            else:
                ors = [f"transformed_uid in ({','.join('?' * len(mine))})"]
                params.extend(mine)
                for uid in mine:
                    ors.append("(',' || ifnull(other_uids, '') || ',') like ?")
                    params.append(f"%,{uid},%")
                clauses.append("(" + " or ".join(ors) + ")")
        return (" where " + " and ".join(clauses)) if clauses else "", params

    def _query(self, flt: Filter, sql: str, extra=(), default=None):
        """f（条件で絞った rounds）を使う問い合わせ"""
        def work(con):
            where, params = self._where(con, flt)
            return con.execute(f"with f as (select * from rounds{where}) " + sql,
                               (*params, *extra)).fetchall()
        return self._run(work, [] if default is None else default)

    def available_rounds(self) -> list[int]:
        return [r[0] for r in self._run(
            lambda con: con.execute("select distinct round from rounds order by round").fetchall(), [])]

    def time_range(self, flt: Filter = Filter()):
        """(最初, 最後) の time。無ければ (None, None)"""
        rows = self._query(flt, "select min(time), max(time) from f")
        return tuple(rows[0]) if rows else (None, None)

    def round_summary(self, flt: Filter) -> list[tuple[int, int, int]]:
        """[(ラウンドの番号, ラウンド数, テラーの枠数)]"""
        return self._query(flt, "select round, count(*), sum((terror1 is not null) + "
                                "(terror2 is not null) + (terror3 is not null)) from f group by round")

    def terror_counts(self, flt: Filter) -> dict[int, int]:
        """{テラー: 出た枠数}（3列をまとめて数える）"""
        return dict(self._query(flt, f"select tid, count(*) from ({_TERRORS}) group by tid"))

    def map_counts_for_terror(self, flt: Filter, terror_id: int) -> list[tuple]:
        """[(map_id, ラウンドの番号, そのテラーの枠数)]"""
        return self._query(flt, f"select map_id, round, count(*) from ({_TERRORS}) where tid = ? "
                                "group by map_id, round", (int(terror_id),))

    def round_counts_for_terror(self, flt: Filter, terror_id: int) -> list[tuple[int, int]]:
        """[(ラウンドの番号, そのテラーの枠数)]。多い順"""
        return self._query(flt, f"select round, count(*) as n from ({_TERRORS}) where tid = ? "
                                "group by round order by n desc, round", (int(terror_id),))

    def rounds_for_terror(self, flt: Filter, terror_id: int, limit: int) -> tuple[int, list[tuple]]:
        """そのテラーが出たラウンド。(全件数, 新しい順に limit 件の
        [(time, round, map_id, t1, t2, t3, transformed_uid, other_uids, origin)])"""
        tid = int(terror_id)
        total = self._query(flt, "select count(*) from f where ? in (terror1, terror2, terror3)",
                            (tid,))
        rows = self._query(flt, "select time, round, map_id, terror1, terror2, terror3,"
                                " transformed_uid, other_uids, origin from f"
                                " where ? in (terror1, terror2, terror3)"
                                " order by time desc limit ?", (tid, int(limit)))
        return (int(total[0][0]) if total else 0), rows

    def time_series(self, flt: Filter, unit: str, terror_id: int | None = None) -> list[tuple]:
        """日（unit='day'、'YYYY-MM-DD'）か時間帯（unit='hour'、0〜23）ごとの
        [(区切り, ラウンド数, そのテラーの枠数)]。ローカル時刻で区切る"""
        from config import DB_TIME_EPOCH
        local = _LOCAL.format(epoch=int(DB_TIME_EPOCH))
        key = f"date({local})" if unit == "day" else f"cast(strftime('%H', {local}) as integer)"
        tid = -1 if terror_id is None else int(terror_id)
        return self._query(flt, f"select {key} as k, count(*), sum((terror1 is ?) + (terror2 is ?)"
                                " + (terror3 is ?)) from f group by k order by k", (tid, tid, tid))

    def terror_pairs(self, flt: Filter) -> list[tuple[int, int, int]]:
        """一緒に出たテラーの組 [(小さい方, 大きい方, 回数)]。多い順"""
        pairs = " union all ".join(
            f"select min({a}, {b}) as a, max({a}, {b}) as b from f "
            f"where {a} is not null and {b} is not null"
            for a, b in (("terror1", "terror2"), ("terror1", "terror3"), ("terror2", "terror3")))
        return self._query(flt, f"select a, b, count(*) as n from ({pairs}) group by a, b "
                                "order by n desc, a, b")

    def map_terror_counts(self, flt: Filter) -> list[tuple]:
        """[(map_id, ラウンドの番号, テラー, 枠数)]"""
        return self._query(flt, f"select map_id, round, tid, count(*) from ({_TERRORS}) "
                                "group by map_id, round, tid")

    def player_counts(self, flt: Filter) -> dict[int, int]:
        """{見た人数（1人目＋other_uids）: ラウンド数}"""
        return dict(self._query(flt, f"select {_PLAYERS} as n, count(*) from f group by n"))

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
