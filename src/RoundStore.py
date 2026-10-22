"""統計画面 v1 の手元の保存（%APPDATA%\\ToNAutoBeginner\\rounds.sqlite。標準の sqlite3）。

DB から取ったラウンドと、自分が送ったラウンドを貯める。統計画面は差分だけ DB から
取り、集計はここで行う（DB に集計を頼まない。アクセスが多いときに重くならないように）。

- 表 rounds: DB の "ToNRounds_v2" と同じ列（time は 2026-01-01 UTC からの秒）＋ origin
  （'db' = DB から取った／'own' = 自分が送ったときに書いた）。other_uids はカンマ区切り。
  テラーの番号はツールの独自の番号（TerrorIds）。前の版の番号で貯めてあった店は、最初に開いたとき 1 回だけ
  独自の番号に移す（移す前の店は rounds.sqlite.listtool.bak に残す。meta の terror_ids が own の印）
- 一意は (time, transformed_uid, round, map_id, terror1)。同じアカウントを複数の窓で
  動かしていると、同じ uid の別々のラウンドがほぼ同じ秒に始まりうるので、時刻と uid
  だけでは足りない。取り直した行は上書き（other_uids の更新を取り込む）
- 表 meta: my_uids（このPCから送ったことのある transformed_uid。複数ありうる）・
  initial_done（初回の全件取得が済んだか）・synced_to（DB から取った行の最大の time）・synced_uids。
  この 3 つは取り先（v2・legacy＝無印）ごとに別々に持つ（initial_done:v2 など）。一方の取り先が失敗しても
  もう一方は進む
- 列 players: ラウンド開始の瞬間のインスタンスの人数（自分を含む）。分からない行・今までの行は NULL

どのスレッドから呼んでもよい（鍵）。失敗しても例外を外へ出さない（統計が取れない
だけで、ツールの動きは止めない）。
"""
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import config
import DebugLog
import TerrorIds

ORIGIN_DB = "db"
ORIGIN_OWN = "own"
BAK_SUFFIX = ".listtool.bak"        # 番号を移す前の店（rounds.sqlite.listtool.bak）
TERROR_IDS_OWN = "own"              # meta の terror_ids。独自の番号に移し終えた印
V2_SOURCE = "v2"                    # 取り先の名前（DB v2）
LEGACY_SOURCE = "legacy"            # 無印 ToNRounds（LegacyRoundsTable）

_CREATE_ROUNDS_NEW = """create table rounds_new (
    time integer not null,
    round integer not null,
    map_id integer,
    terror1 integer,
    terror2 integer,
    terror3 integer,
    transformed_uid integer,
    other_uids text,
    origin text not null,
    players integer
)"""

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
    origin text not null,
    players integer
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
class Source:
    """DB の取り先 1 つ。fetch(since, my_uids) が行を返し、convert(行) が v2 と同じ形に直す（v2 は None）"""
    name: str
    fetch: object
    convert: object = None


def sources() -> tuple:
    """DB から取る表の一覧（v2 と、一時的に無印）。取り先ごとに別々に進める。
    無印を消すときは LegacyRoundsTable の行を消す。ConnectDB が RoundStore を読むので、ここで読む"""
    import ConnectDB
    import LegacyRoundsTable
    return (Source(V2_SOURCE, ConnectDB.fetch_rounds),
            Source(LegacyRoundsTable.NAME, LegacyRoundsTable.fetch, LegacyRoundsTable.convert))


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
# ローカル時刻（time は 2026-01-01 UTC からの秒）
_LOCAL = "datetime(time + {epoch}, 'unixepoch', 'localtime')"


class RoundStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(config.ROUND_STORE_PATH)
        self._lock = threading.Lock()
        self._ready = False
        self._source_failed: dict = {}      # 取り先ごとの「失敗を debug.log に書いた」（1 回だけ書く）

    # ── 接続 ─────────────────────────────────
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=10)
        if not self._ready:
            try:
                con.executescript(_SCHEMA)
                # 人数の列が無い店（前の版が作ったもの）へ足す。今までの行は NULL
                if "players" not in {row[1] for row in con.execute("pragma table_info(rounds)")}:
                    con.execute("alter table rounds add column players integer")
                    con.commit()
                self._migrate_terror_ids(con)
            except Exception:
                con.close()
                raise                   # 移せなかった店は使わない（_ready にしない。次に開くときまた試す）
            self._ready = True
        return con

    def backup_path(self) -> Path:
        return self.path.with_name(self.path.name + BAK_SUFFIX)

    def _backup(self, con):
        """移す前の店を rounds.sqlite.listtool.bak に残す（すでにあれば作らない・上書きしない）。
        一時ファイルに書いてから置き換える。失敗したら例外（呼び出し側は移さない）"""
        bak = self.backup_path()
        if bak.exists():
            return
        tmp = bak.with_name(bak.name + ".tmp")
        dest = None
        try:
            dest = sqlite3.connect(str(tmp))
            con.backup(dest)
            dest.close()
            dest = None
            os.replace(tmp, bak)
        finally:
            if dest is not None:
                dest.close()
            try:
                tmp.unlink()
            except OSError:
                pass

    def _migrate_terror_ids(self, con):
        """前のツールの番号で貯めてあった店を、独自の番号に 1 回だけ移す（meta の terror_ids が own の印）。

        - 行が無い店は印だけ付ける（.bak は作らない）
        - 行があれば、先に .bak（作れなければ例外＝移さない）。変換は (番号, ラウンド) で引く
          （8 Pages のラウンドは list_id → 3000＋ページ。8 Pages の索引に無い番号は通常の索引へ。どちらにも無ければ NULL）
        - 変換した結果が同じ行になるもの（一意の鍵が重なる行）は 1 行にまとまる
        - 取り込みの記録（initial_done・synced_to・synced_uids）は無印のものとして …:legacy に移す。v2 の記録は
          まだ無いので、v2 は最初に全部取る
        - 全部 1 つの取引。途中で失敗したら元のまま"""
        if self._meta(con, "terror_ids") == TERROR_IDS_OWN:
            return
        total = con.execute("select count(*) from rounds").fetchone()[0]
        if total:
            self._backup(con)
        try:
            con.execute("begin")
            if total:
                self._convert_rows(con, total)
                for key in ("initial_done", "synced_to", "synced_uids"):
                    value = self._meta(con, key)
                    if value is not None:
                        con.execute("insert or replace into meta(key, value) values (?, ?)",
                                    (f"{key}:{LEGACY_SOURCE}", value))
                        con.execute("delete from meta where key = ?", (key,))
            con.execute("insert or replace into meta(key, value) values ('terror_ids', ?)",
                        (TERROR_IDS_OWN,))
            con.commit()
        except Exception:
            con.rollback()
            raise

    @staticmethod
    def _convert_rows(con, total: int):
        table = TerrorIds.TABLE
        con.execute("create temp table m_normal(legacy integer primary key, own integer not null)")
        con.executemany("insert into m_normal values (?, ?)", sorted(table.by_legacy.items()))
        con.execute("create temp table m_eight(legacy integer primary key, own integer not null)")
        con.executemany("insert into m_eight values (?, ?)", sorted(table.by_legacy_8p.items()))
        eight = TerrorIds.EIGHT_PAGES_ROUND_ID
        columns = ", ".join(
            f"case when r.round = {eight} then coalesce(e{i}.own, n{i}.own) else n{i}.own end as t{i}"
            for i in (1, 2, 3))
        joins = " ".join(f"left join m_eight e{i} on e{i}.legacy = r.terror{i} "
                         f"left join m_normal n{i} on n{i}.legacy = r.terror{i}" for i in (1, 2, 3))
        con.execute(f"create temp table converted as select r.rowid as rid, {columns} from rounds r {joins}")
        nulled = con.execute(
            "select ifnull(sum(r.terror1 is not null and c.t1 is null), 0)"
            " + ifnull(sum(r.terror2 is not null and c.t2 is null), 0)"
            " + ifnull(sum(r.terror3 is not null and c.t3 is null), 0)"
            " from rounds r join converted c on c.rid = r.rowid").fetchone()[0]
        con.execute(_CREATE_ROUNDS_NEW)
        con.execute("create unique index rounds_new_key on rounds_new(time, ifnull(transformed_uid, -99999),"
                    " round, ifnull(map_id, -1), ifnull(terror1, -1))")
        con.execute(
            "insert or ignore into rounds_new(time, round, map_id, terror1, terror2, terror3,"
            " transformed_uid, other_uids, origin, players)"
            " select r.time, r.round, r.map_id, c.t1, c.t2, c.t3, r.transformed_uid, r.other_uids,"
            " r.origin, r.players from rounds r join converted c on c.rid = r.rowid order by r.rowid")
        kept = con.execute("select count(*) from rounds_new").fetchone()[0]
        con.execute("drop table rounds")
        con.execute("alter table rounds_new rename to rounds")
        con.execute("drop index rounds_new_key")
        con.execute("create unique index rounds_key on rounds(time, ifnull(transformed_uid, -99999), round,"
                    " ifnull(map_id, -1), ifnull(terror1, -1))")
        con.execute("create index if not exists rounds_time on rounds(time)")
        con.execute("drop table converted")
        con.execute("drop table m_normal")
        con.execute("drop table m_eight")
        DebugLog.write(f"[統計] 手元の店の番号を独自の番号に移しました: {total} 行"
                       f"（変換できず空にした番号 {nulled} 件・重なって 1 行になった {total - kept} 行）")

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
    def add_own(self, time: int, round_id: int, map_id, t1, t2, t3, uid, players=None) -> bool:
        """自分が送ったラウンドを1行（origin own）。uid が無ければ貯めない
        （DB でも誰の行か分からないので、自分の分として扱えない）"""
        if uid is None:
            return False

        def work(con):
            con.execute(
                "insert or ignore into rounds(time, round, map_id, terror1, terror2, terror3,"
                " transformed_uid, other_uids, origin, players) values (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (int(time), int(round_id), map_id, t1, t2, t3, int(uid), ORIGIN_OWN, players))
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

    def sync_all(self, source_list=None) -> bool:
        """DB の取り先（v2・無印）をそれぞれ別々に取り込む。戻り値は v2 の成否。
        無印の失敗（消えて 404 になった・通信）は v2 に響かせない（無印の失敗は debug.log に 1 回だけ書く）"""
        ok = False
        for source in (source_list if source_list is not None else sources()):
            done = self.sync(source.fetch, source.name, source.convert)
            if source.name == V2_SOURCE:
                ok = done
            elif not done and not self._source_failed.get(source.name):
                self._source_failed[source.name] = True
                DebugLog.write(f"[統計] {source.name} の取り込みに失敗しました（v2 の分だけで続けます）")
            elif done:
                self._source_failed.pop(source.name, None)
        return ok

    def sync(self, fetch, source: str = V2_SOURCE, convert=None) -> bool:
        """DB から差分を取って手元に入れる。fetch(since, my_uids) は ConnectDB.fetch_rounds。
        source は取り先の名前（記録の meta の鍵に添える）。convert があれば行ごとに通す（無印を v2 の形に直す）。

        初回（または my_uids に新しい uid が増えた）は全件（自分の行も含む）。以降は
        time >= synced_to − 15分 の、1人目が自分でない行か other_uids のある行だけ。
        取った行は上書きで入れ、other_uids に自分がいる行と重なる own の行は消す
        （DB の行が正。自分は2人目以降だった）。通信の失敗は False（手元はそのまま・この取り先の記録も変えない）。
        fetch が返すのはイテレータでもよい（1 ページずつ読みながら書く。読み進めている途中で
        失敗したら、その回に入れた行は 1 つの取引なので全部戻る）
        """
        def meta_key(name):
            return f"{name}:{source}"

        mine = self.my_uids()
        synced_uids = set(uids_list(self.get_meta(meta_key("synced_uids"))))
        full = self.get_meta(meta_key("initial_done")) != "1" or not mine <= synced_uids
        synced_to = self.get_meta(meta_key("synced_to"))
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
                if convert is not None:
                    row = convert(row)
                others = [u for u in (row.get("other_uids") or []) if u is not None]
                values = (int(row["time"]), int(row["round"]), row.get("map_id"),
                          row.get("terror1"), row.get("terror2"), row.get("terror3"),
                          row.get("transformed_uid"), uids_text(others), ORIGIN_DB)
                con.execute(
                    "insert or replace into rounds(time, round, map_id, terror1, terror2,"
                    " terror3, transformed_uid, other_uids, origin, players)"
                    " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, row.get("players")))
                for uid in set(others) & mine:
                    con.execute(
                        "delete from rounds where origin = ? and transformed_uid = ?"
                        " and abs(time - ?) <= ? and round = ? and map_id is ? and terror1 is ?",
                        (ORIGIN_OWN, uid, values[0], self.OWN_MATCH_SEC, values[1],
                         values[2], values[3]))
                newest = values[0] if newest is None else max(newest, values[0])
            con.execute("insert or replace into meta(key, value) values (?, '1')", (meta_key("initial_done"),))
            con.execute("insert or replace into meta(key, value) values (?, ?)",
                        (meta_key("synced_uids"), uids_text(sorted(mine))))
            if newest is not None:
                con.execute("insert or replace into meta(key, value) values (?, ?)",
                            (meta_key("synced_to"), str(newest)))
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

    # 1 テラーの内訳を引く関数の terror_id は、番号 1 つか番号の集合（統計は 8 Pages に出た同じテラーをまとめて
    # 数えるので、{通常の番号} ∪ {まとめる 8 Pages の番号} を渡す。TerrorIds.statistics_ids）
    @staticmethod
    def _id_list(terror_id) -> list[int]:
        if isinstance(terror_id, int):
            return [int(terror_id)]
        return sorted(int(t) for t in terror_id)

    @classmethod
    def _in(cls, terror_id) -> tuple[str, list[int]]:
        ids = cls._id_list(terror_id)
        return f"({','.join('?' * len(ids))})", ids

    def map_counts_for_terror(self, flt: Filter, terror_id) -> list[tuple]:
        """[(map_id, ラウンドの番号, そのテラーの枠数)]"""
        marks, ids = self._in(terror_id)
        return self._query(flt, f"select map_id, round, count(*) from ({_TERRORS}) where tid in {marks} "
                                "group by map_id, round", ids)

    def round_counts_for_terror(self, flt: Filter, terror_id) -> list[tuple[int, int]]:
        """[(ラウンドの番号, そのテラーの枠数)]。多い順"""
        marks, ids = self._in(terror_id)
        return self._query(flt, f"select round, count(*) as n from ({_TERRORS}) where tid in {marks} "
                                "group by round order by n desc, round", ids)

    def rounds_for_terror(self, flt: Filter, terror_id, limit: int) -> tuple[int, list[tuple]]:
        """そのテラーが出たラウンド。(全件数, 新しい順に limit 件の
        [(time, round, map_id, t1, t2, t3, transformed_uid, other_uids, origin)])"""
        marks, ids = self._in(terror_id)
        any_slot = f"(terror1 in {marks} or terror2 in {marks} or terror3 in {marks})"
        total = self._query(flt, f"select count(*) from f where {any_slot}", ids * 3)
        rows = self._query(flt, "select time, round, map_id, terror1, terror2, terror3,"
                                " transformed_uid, other_uids, origin from f"
                                f" where {any_slot} order by time desc limit ?", (*ids * 3, int(limit)))
        return (int(total[0][0]) if total else 0), rows

    def time_series(self, flt: Filter, unit: str, terror_id=None) -> list[tuple]:
        """日（unit='day'、'YYYY-MM-DD'）か時間帯（unit='hour'、0〜23）ごとの
        [(区切り, ラウンド数, そのテラーの枠数)]。ローカル時刻で区切る"""
        from config import DB_TIME_EPOCH
        local = _LOCAL.format(epoch=int(DB_TIME_EPOCH))
        key = f"date({local})" if unit == "day" else f"cast(strftime('%H', {local}) as integer)"
        marks, ids = self._in(-1 if terror_id is None else terror_id)
        hit = " + ".join(f"ifnull(terror{n} in {marks}, 0)" for n in (1, 2, 3))
        return self._query(flt, f"select {key} as k, count(*), sum({hit}) from f group by k order by k",
                           ids * 3)

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
