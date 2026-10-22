"""supabase/migrate_terror_ids_v2.sql の対応表が、ツールの TerrorIds と全部一致する（SQL は流さない）"""
from tests.support import *  # noqa: F401,F403

SQL = REPO_ROOT / "supabase" / "migrate_terror_ids_v2.sql"


def _text():
    return SQL.read_text(encoding="utf-8")


def _map_rows():
    text = _text()
    body = text.split("-- BEGIN terror_id_map rows", 1)[1].split("-- END terror_id_map rows", 1)[0]
    return [(int(a), b == "true", int(c))
            for a, b, c in re.findall(r"^\s*\((\d+), (true|false), (\d+)\)", body, re.M)]


class TestTerrorIdMap(unittest.TestCase):
    def test_the_rows_are_exactly_the_tools_conversion(self):
        rows = _map_rows()
        self.assertEqual(len(rows), len(set((legacy, eight) for legacy, eight, _own in rows)), "鍵が重ならない")
        normal = {legacy: own for legacy, eight, own in rows if not eight}
        eight = {legacy: own for legacy, eight, own in rows if eight}
        self.assertEqual(normal, TerrorIds.TABLE.by_legacy)
        self.assertEqual(eight, TerrorIds.TABLE.by_legacy_8p)

    def test_every_value_in_the_map_gives_the_same_as_the_tools_function(self):
        rows = _map_rows()
        by_key = {(legacy, eight): own for legacy, eight, own in rows}
        for legacy in range(0, 1300):
            for round_id in (1, 51, 105):
                eight = round_id == 105
                sql = (by_key.get((legacy, True)) if eight and (legacy, True) in by_key else None)
                if sql is None:
                    sql = by_key.get((legacy, False))
                self.assertEqual(sql, TerrorIds.own_from_legacy_round(legacy, round_id), (legacy, round_id))

    def test_the_own_numbers_fit_in_a_smallint(self):
        self.assertLessEqual(max(own for _l, _e, own in _map_rows()), 32767)
        self.assertLessEqual(max(TerrorIds.ids()), 32767)

    def test_the_parts_that_need_the_unknown_current_definitions_are_marked_and_not_invented(self):
        text = _text()
        self.assertGreaterEqual(text.count("［依存"), 3)
        self.assertIn("TODO", text)
        executable = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
        self.assertNotIn("create or replace function", executable, "register_round_v2 の本体は書かない")
        self.assertNotIn("create table public.\"ToNRounds_v2\"", executable)
        self.assertNotIn("drop table", executable, "表は消さない（名前を変えて残す手順はコメント）")
        self.assertIn("create table if not exists public.terror_id_map", executable)

    def test_the_sql_tells_the_order_and_the_old_table_stays_until_the_later_step(self):
        text = _text()
        self.assertIn("A（リリース前）", text)
        self.assertIn("B（リリースの 3 日ほど後", text)
        self.assertIn("ToNRounds_legacy_bak", text)
