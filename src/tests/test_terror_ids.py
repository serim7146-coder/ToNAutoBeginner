"""独自のテラー番号の表（TerrorIds・assets/data/terror_ids.json）。

見本は src/tests/data/terror_ids/ に置いた、番号を変える前の terrors.json・continue_slots.json・icons.json・
layout.json（凍結）。本物の ToN ListTool のフォルダは使わない
"""
from tests.support import *  # noqa: F401,F403

import TerrorIds

FROZEN = SRC_DIR / "tests" / "data" / "terror_ids"


def _frozen(name):
    return json.loads((FROZEN / name).read_text(encoding="utf-8"))


# 4000 番台の (独自の番号, 前の番号)。後ろへ足すだけ。並びを変えたらこのテストが落ちる
FIXED_PAIRS = [(4000, 190), (4001, 191), (4002, 192), (4003, 196), (4004, 197), (4005, 198), (4006, 199),
               (4007, 312), (4008, 313), (4009, 314), (4010, 315), (4011, 316), (4012, 317), (4013, 1283)]


class TestTable(unittest.TestCase):
    def test_the_file_loads_without_dropping_a_row(self):
        raw = json.loads(config.resource_path(TerrorIds.FILE).read_text(encoding="utf-8"))
        with patch.object(DebugLog, "write") as write:
            table = TerrorIds.parse(raw)
        write.assert_not_called()
        self.assertEqual(len(table.rows), len(raw["terrors"]))
        self.assertEqual(len(table.rows), 330)

    def test_every_row_is_in_the_range_of_its_kind(self):
        for own, found in TerrorIds.TABLE.rows.items():
            self.assertTrue(TerrorIds._kind_matches(own, found.kind), (own, found.kind))
        counts = {}
        for found in TerrorIds.TABLE.rows.values():
            counts[found.kind] = counts.get(found.kind, 0) + 1
        self.assertEqual(counts, {"classic": 134, "alternate": 36, "unbound": 84, "eight_pages": 62,
                                  "variant": 7, "moon": 4, "special": 3})

    def test_the_indexes_do_not_overlap(self):
        table = TerrorIds.TABLE
        self.assertEqual(len(table.by_listtool), 267)       # Self Inserts (Bloodthirsty) は ListTool の番号が無い
        self.assertEqual(len(table.by_listtool_8p), 62)
        self.assertEqual(len(table.by_legacy), 268)
        self.assertEqual(len(table.by_legacy_8p), 62)
        for own in list(table.by_listtool.values()) + list(table.by_legacy.values()):
            self.assertNotEqual(table.rows[own].kind, "eight_pages")
        for own in list(table.by_listtool_8p.values()) + list(table.by_legacy_8p.values()):
            self.assertEqual(table.rows[own].kind, "eight_pages")

    def test_the_fixed_numbers_are_the_agreed_ones(self):
        for own, legacy in FIXED_PAIRS:
            self.assertEqual(TerrorIds.row(own).legacy, legacy, own)
        self.assertEqual([own for own in TerrorIds.ids() if own >= 4000], [own for own, _ in FIXED_PAIRS])
        names = {own: TerrorIds.name(own) for own, _ in FIXED_PAIRS}
        self.assertEqual(names[4007], "Rift Monsters")
        self.assertEqual(names[4011], "Evil Purple Foxy")
        self.assertEqual(names[4013], "Self Inserts (Bloodthirsty)")
        self.assertEqual([names[n] for n in (4003, 4004, 4005, 4006)],
                         ["Mystic Moon", "Blood Moon", "Twilight", "Solstice"])
        self.assertIsNone(TerrorIds.row(4013).listtool)
        self.assertIsNone(TerrorIds.to_listtool(4013))
        for own, legacy in FIXED_PAIRS[:-1]:
            self.assertEqual(TerrorIds.row(own).listtool, legacy)

    def test_the_named_constants_match_the_table(self):
        pairs = {"HUNGRY_HOME_INVADER": 190, "ATRACHED": 191, "BLOODTHIRSTY_CREATURE": 192,
                 "MYSTIC_MOON": 196, "BLOOD_MOON": 197, "TWILIGHT": 198, "SOLSTICE": 199,
                 "RIFT_MONSTERS": 312, "MEATBALL_MAN": 313, "GIGABYTES": 314, "NEO_PILOT": 315,
                 "FOXY": 316, "GLORBO": 317, "SELF_INSERTS_BLOODTHIRSTY": 1283}
        for constant, legacy in pairs.items():
            self.assertEqual(TerrorIds.row(getattr(TerrorIds, constant)).legacy, legacy, constant)

    def test_the_categories_for_statistics(self):
        self.assertEqual({o for o in TerrorIds.ids() if TerrorIds.category(o) == "classic" and o >= 1000},
                         {4000, 4001, 4002, 4009, 4012})
        self.assertEqual({o for o in TerrorIds.ids() if TerrorIds.category(o) == "alternate" and o >= 2000},
                         {4011})
        self.assertEqual([o for o in TerrorIds.ids() if o >= 4000 and TerrorIds.category(o) is None],
                         [4003, 4004, 4005, 4006, 4007, 4008, 4010, 4013])
        self.assertTrue(all(TerrorIds.category(o) is None for o in TerrorIds.eight_pages_ids()))

    def test_the_key_and_the_round_number_are_the_ones_the_tool_uses(self):
        self.assertEqual(TerrorIds.EIGHT_PAGES_KEY, MatchTNL.LOG_TO_TNL["8 Pages"])
        self.assertEqual(TerrorIds.EIGHT_PAGES_ROUND_ID, config.ROUND_TYPE_IDS["8 Pages"])
        self.assertEqual(TerrorIds.ALTERNATE_SLOT_POSITIONS,
                         {"Alternate": None, "Midnight": [2], "Fog (Alternate)": None, "Ghost (Alternate)": None})

    def test_the_table_file_is_in_the_build_in_the_same_order_as_main(self):
        build = _load_build_script()
        want = "--include-data-files=assets/data/terror_ids.json=assets/data/terror_ids.json"
        self.assertIn(want, build.BASE_ARGS)
        main = SRC_DIR.joinpath("main.py").read_text(encoding="utf-8")
        line = next(l for l in main.splitlines() if l.startswith("python -m nuitka src/main.py"))
        self.assertIn(want, line.split())

    def test_a_broken_row_is_dropped_and_written_once(self):
        raw = {"terrors": [
            {"id": 1, "kind": "classic", "category": "classic", "name": "A", "log": 1, "listtool": 1, "legacy": 1},
            {"id": 1001, "kind": "classic", "name": "kind and range disagree"},
            {"id": 5, "kind": "classic", "name": ""},
            {"id": 7, "kind": "classic", "name": "dup"}, {"id": 7, "kind": "classic", "name": "dup again"},
            "not a dict"]}
        with patch.object(DebugLog, "write") as write:
            table = TerrorIds.parse(raw)
        self.assertEqual(sorted(table.rows), [1, 7])
        self.assertEqual(table.rows[7].name, "dup")
        self.assertEqual(write.call_count, 1)
        with self.assertRaises(ValueError):
            TerrorIds.parse({"nothing": []})

    def test_a_number_in_two_rows_is_not_indexed(self):
        raw = {"terrors": [
            {"id": 1, "kind": "classic", "name": "A", "listtool": 9, "legacy": 9},
            {"id": 2, "kind": "classic", "name": "B", "listtool": 9, "legacy": 2}]}
        with patch.object(DebugLog, "write") as write:
            table = TerrorIds.parse(raw)
        self.assertNotIn(9, table.by_listtool)
        self.assertEqual(table.by_legacy, {9: 1, 2: 2})
        self.assertEqual(write.call_count, 1)


class TestEightPagesRows(unittest.TestCase):
    def test_the_62_pages_are_3000_plus_the_page_number(self):
        raw = _frozen("terrors.json")["8pages"]
        self.assertEqual(len(raw), 62)
        for entry in raw:
            row = TerrorIds.row(3000 + entry["id"])
            self.assertEqual(row.kind, "eight_pages")
            self.assertEqual(row.log, entry["id"])
            self.assertEqual(row.listtool, entry["list_id"])
            self.assertEqual(row.legacy, entry["list_id"])
        self.assertEqual(TerrorIds.eight_pages_ids(), frozenset(3000 + e["id"] for e in raw))

    def test_names_come_from_the_normal_row_then_the_icon_names_then_the_page_name(self):
        pages = {e["id"]: e for e in _frozen("terrors.json")["8pages"]}
        self.assertEqual(TerrorIds.name(3014), "Huggy")             # list_id 0 → Classic の Huggy
        self.assertEqual(TerrorIds.name(3050), "Whiteface")          # list_id 135 → Alternate の Whiteface
        self.assertEqual(TerrorIds.name(3046), "Baldi")              # list_id 172 → 通常の索引に無い。8 Pages の名前
        self.assertEqual(pages[46]["name"], "Baldi")
        self.assertEqual(TerrorIds.name(3056), "The Navigator")      # 193 → icons.json の names
        self.assertEqual(TerrorIds.name(3032), "Alternates")
        self.assertEqual(TerrorIds.name(3000), "Slendy")

    def test_the_icons_follow_the_list_id(self):
        icons = {int(k): tuple(v) for k, v in _frozen("icons.json")["icons"].items()}
        for entry in _frozen("terrors.json")["8pages"]:
            self.assertEqual(TerrorIds.icon(3000 + entry["id"]), icons[entry["list_id"]])

    def test_the_eight_pages_ids_are_looked_up_in_their_own_index(self):
        self.assertEqual(TerrorIds.from_listtool(0, TerrorIds.EIGHT_PAGES_KEY), 3014)
        self.assertEqual(TerrorIds.from_listtool(0, "Classic/クラシック"), 0)
        self.assertEqual(TerrorIds.own_from_legacy(172, eight_pages=True), 3046)
        self.assertIsNone(TerrorIds.own_from_legacy(172))
        self.assertEqual(TerrorIds.own_from_legacy(135, eight_pages=True), 3050)
        self.assertEqual(TerrorIds.own_from_legacy(135), 1001)
        # 8 Pages の索引に無い番号は通常の索引へ落ちる（DB の行）。どちらにも無ければ None
        self.assertEqual(TerrorIds.own_from_legacy(5, eight_pages=True), 5)
        self.assertIsNone(TerrorIds.own_from_legacy(175, eight_pages=True))

    def test_the_rule_numbers_do_not_overlap_the_pages_except_arkus(self):
        """どの決まりも 8 Pages の番号を見ない。重なるのは Arkus（61。ページ 4）だけで、Arkus を使う
        決まり（Glorbo への置き換え）は Punished でしか働かない。新しい決まりが 8 Pages の番号と
        重なったら、ここで落ちる（意図したものか確かめるきっかけ）"""
        rule_ids = {50, 131, 40, 47, 106, 190, 191, 192, 283, 136, 316, 163, 315, 61, 317, 314, 164}
        list_ids = {e["list_id"] for e in _frozen("terrors.json")["8pages"]}
        self.assertEqual(list_ids & rule_ids, {61})
        self.assertEqual({e["id"] for e in _frozen("terrors.json")["8pages"] if e["list_id"] == 61}, {4})
        # 8 Pages の場面の独自の番号は 3000 番台。決まりの独自の番号（0〜2999 と 4000〜）とは重ならない
        for own in TerrorIds.eight_pages_ids():
            self.assertTrue(3000 <= own < 4000)


class TestFrozenData(unittest.TestCase):
    """凍結した今のファイルの番号が、全部表に当たる"""

    def test_every_terror_in_terrors_json_resolves(self):
        raw = _frozen("terrors.json")
        for category in ("classic", "alternate", "unbound"):
            for entry in raw[category]:
                own = TerrorIds.own_from_legacy(entry["id"])
                self.assertIsNotNone(own, entry)
                self.assertEqual(TerrorIds.category(own), category)
                expect = _frozen("icons.json")["names"].get(str(entry["id"]), entry["name"])
                if entry["id"] == 316:
                    expect = "Evil Purple Foxy"            # 正式名が長いので、表示名はこれ
                self.assertEqual(TerrorIds.name(own), expect)

    def test_every_icon_resolves_to_a_row_with_the_same_picture(self):
        icons = _frozen("icons.json")["icons"]
        eight = {e["list_id"] for e in _frozen("terrors.json")["8pages"]}
        for number, spot in icons.items():
            number = int(number)
            own = TerrorIds.own_from_legacy(number)
            if own is None:
                self.assertIn(number, eight, number)         # 8 Pages にだけ出る番号（170 など）
                continue
            self.assertEqual(TerrorIds.icon(own), tuple(spot), number)

    def test_every_slot_resolves(self):
        for key, numbers in _frozen("continue_slots.json")["rounds"]:
            for number in numbers:
                if key == TerrorIds.EIGHT_PAGES_KEY:
                    ok = number in TerrorIds.TABLE.by_listtool_8p or number in TerrorIds.TABLE.by_listtool
                else:
                    ok = number in TerrorIds.TABLE.by_listtool or number == 1283
                self.assertTrue(ok, (key, number))

    def test_the_eight_pages_key_has_62_appearing_and_116_that_do_not(self):
        numbers = next(ids for key, ids in _frozen("continue_slots.json")["rounds"]
                       if key == TerrorIds.EIGHT_PAGES_KEY)
        self.assertEqual(len(numbers), 178)
        self.assertEqual(sum(1 for n in numbers if n in TerrorIds.TABLE.by_listtool_8p), 62)

    def test_the_layout_numbers_resolve(self):
        layout = _frozen("layout.json")
        for number in layout["classic"] + layout["alternate"]:
            self.assertIsNotNone(TerrorIds.own_from_legacy(number), number)


class TestFromLog(unittest.TestCase):
    def test_classic_numbers_pass_through(self):
        self.assertEqual(TerrorIds.from_log([5, 7], "Classic"), [5, 7])
        self.assertEqual(TerrorIds.from_log([5], "Fog"), [5])
        self.assertEqual(TerrorIds.from_log([], "Classic"), [])

    def test_alternate_slots_are_1000_plus(self):
        self.assertEqual(TerrorIds.from_log([1], "Alternate"), [1001])
        self.assertEqual(TerrorIds.from_log([40], "Alternate"), [1040], "36 以上も Alternate")
        self.assertEqual(TerrorIds.from_log([3, 4], "Fog (Alternate)"), [1003, 1004])
        self.assertEqual(TerrorIds.from_log([3], "Ghost (Alternate)"), [1003])

    def test_midnight_only_the_third_slot_is_alternate(self):
        self.assertEqual(TerrorIds.from_log([5, 6, 7], "Midnight"), [5, 6, 1007])
        self.assertEqual(TerrorIds.from_log([5, 6, 40], "Midnight"), [5, 6, 1040])

    def test_unbound_is_2000_plus(self):
        self.assertEqual(TerrorIds.from_log([5], "Unbound", "Unbound"), [2005])
        self.assertEqual(TerrorIds.from_log([5], "Classic", "Unbound"), [2005])

    def test_eight_pages_use_the_first_number_only(self):
        self.assertEqual(TerrorIds.from_log([14, 3], "8 Pages"), [3014])
        self.assertEqual(TerrorIds.from_log([70], "8 Pages"), [3070], "表に無いページも落とさない")
        self.assertEqual(TerrorIds.from_log([], "8 Pages"), [])
        self.assertEqual(TerrorIds.from_log([14], "8 Pages (Alternate)"), [14], "今どおり Classic として扱う")

    def test_the_log_numbers_do_not_change_the_input(self):
        ids = [1, 2]
        TerrorIds.from_log(ids, "Alternate")
        self.assertEqual(ids, [1, 2])

    def test_unknown_numbers_flow_and_are_named_by_number(self):
        self.assertEqual(TerrorIds.from_log([500], "Classic"), [500])
        self.assertEqual(TerrorIds.name(500), "#500")
        self.assertEqual(TerrorIds.name(0), "Huggy")
        self.assertEqual(TerrorIds.name(1001), "Whiteface")
        self.assertEqual(TerrorIds.name(1040), "#1040")
        self.assertEqual(TerrorIds.name(3070), "#3070")

    def test_game_id_is_the_log_number_where_there_is_one(self):
        self.assertEqual(TerrorIds.game_id(1001), 1)
        self.assertEqual(TerrorIds.game_id(2005), 5)
        self.assertEqual(TerrorIds.game_id(3014), 14)
        self.assertEqual(TerrorIds.game_id(7), 7)
        self.assertEqual(TerrorIds.game_id(4000), 4000)
        self.assertEqual(TerrorIds.game_id(99999), 99999)

    def test_an_unknown_own_number_is_reported_once(self):
        TerrorIds._reported.discard(("own", 123456))
        with patch.object(DebugLog, "write") as write:
            TerrorIds.report_unknown(123456)
            TerrorIds.report_unknown(123456)
        self.assertEqual(write.call_count, 1)
        self.assertIn("123456", write.call_args.args[0])


class TestListTool(unittest.TestCase):
    def test_numbers_go_in_and_out(self):
        self.assertEqual(TerrorIds.from_listtool(135, "Alternate/オルタネイト"), 1001)
        self.assertEqual(TerrorIds.from_listtool(283, "Unbound/アンバウンド"), 2083)
        self.assertEqual(TerrorIds.from_listtool(192, "Classic/クラシック"), 4002)
        self.assertEqual(TerrorIds.from_listtool(317, "Special/Moon"), 4012)
        for own in (0, 1001, 2083, 4002, 4012):
            self.assertEqual(TerrorIds.from_listtool(TerrorIds.to_listtool(own), "Classic/クラシック"), own)
        self.assertEqual(TerrorIds.to_listtool(3014), 0, "8 Pages の行は list_id")
        self.assertEqual(TerrorIds.to_listtool(3046), 172)
        self.assertIsNone(TerrorIds.to_listtool(4013))
        self.assertIsNone(TerrorIds.to_listtool(99999))

    def test_a_number_not_in_the_table_is_written_once_and_dropped(self):
        TerrorIds._reported.discard(("listtool", 777))
        with patch.object(DebugLog, "write") as write:
            self.assertIsNone(TerrorIds.from_listtool(777, "Classic/クラシック", "主催リスト"))
            self.assertIsNone(TerrorIds.from_listtool(777, "Fog/霧", "主催リスト"))
        self.assertEqual(write.call_count, 1)
        self.assertIn("777", write.call_args.args[0])

    def test_a_list_goes_in_with_the_8_pages_index_and_drops_what_does_not_appear(self):
        data = {"Classic/クラシック": {0, 192, 5},
                TerrorIds.EIGHT_PAGES_KEY: {0, 172, 5, 135, 175},      # 0・172・135 は出る。5 は出ない。175 は表に無い
                "Alternate/オルタネイト": {135}}
        with patch.object(DebugLog, "write") as write:
            result = TerrorIds.list_from_listtool(data, "テスト")
        self.assertEqual(result, {"Classic/クラシック": frozenset({0, 4002, 5}),
                                  TerrorIds.EIGHT_PAGES_KEY: frozenset({3014, 3046, 3050}),
                                  "Alternate/オルタネイト": frozenset({1001})})
        written = "\n".join(c.args[0] for c in write.call_args_list)
        self.assertIn("1 件", written.replace("件を", "件"), "8 Pages に出ないテラーの件数（5 の 1 件）")

    def test_a_key_that_ends_up_empty_is_not_kept(self):
        self.assertEqual(TerrorIds.list_from_listtool({"Classic/クラシック": {9999}}), {})

    def test_strict_refuses_a_number_not_in_the_table(self):
        with self.assertRaises(ValueError):
            TerrorIds.list_from_listtool({"Classic/クラシック": {9999}}, strict=True)
        TerrorIds.list_from_listtool({TerrorIds.EIGHT_PAGES_KEY: {5}}, strict=True)    # 8 Pages に出ないだけ。例外にしない

    def test_a_list_goes_out_without_the_numbers_listtool_does_not_have(self):
        out = TerrorIds.list_to_listtool({"Classic/クラシック": {0, 4002, 4013}, "Alternate/オルタネイト": {1001},
                                          TerrorIds.EIGHT_PAGES_KEY: {3014, 3046}})
        self.assertEqual(out, {"Classic/クラシック": [0, 192], "Alternate/オルタネイト": [135],
                               TerrorIds.EIGHT_PAGES_KEY: [0, 172]})


class TestMigration(unittest.TestCase):
    def test_legacy_numbers_move_to_the_new_ranges(self):
        self.assertEqual(TerrorIds.own_from_legacy(0), 0)
        self.assertEqual(TerrorIds.own_from_legacy(133), 133)
        self.assertEqual(TerrorIds.own_from_legacy(134), 1000)
        self.assertEqual(TerrorIds.own_from_legacy(169), 1035)
        self.assertEqual(TerrorIds.own_from_legacy(200), 2000)
        self.assertEqual(TerrorIds.own_from_legacy(283), 2083)
        for own, legacy in FIXED_PAIRS:
            self.assertEqual(TerrorIds.own_from_legacy(legacy), own)
        for legacy in (170, 171, 172, 173, 174, 177, 193, 194, 195, 175, 284, 311, 318, 999):
            self.assertIsNone(TerrorIds.own_from_legacy(legacy), legacy)

    def test_the_round_number_decides_whether_it_is_a_8_pages_row(self):
        self.assertEqual(TerrorIds.own_from_legacy_round(0, 105), 3014)
        self.assertEqual(TerrorIds.own_from_legacy_round(0, 1), 0)
        self.assertEqual(TerrorIds.own_from_legacy_round(135, 105), 3050)
        self.assertEqual(TerrorIds.own_from_legacy_round(135, 51), 1001)
        self.assertEqual(TerrorIds.own_from_legacy_round(172, 105), 3046)
        self.assertIsNone(TerrorIds.own_from_legacy_round(175, 105))
        self.assertIsNone(TerrorIds.own_from_legacy_round(None, 105))
        self.assertEqual(TerrorIds.own_from_legacy_round(1283, 10), 4013)

    def test_a_list_moves_and_drops_what_the_table_does_not_have(self):
        data = {"Classic/クラシック": {0, 192, 999}, "Unbound/アンバウンド": {283, 1283},
                TerrorIds.EIGHT_PAGES_KEY: {0, 172, 5}}
        moved, dropped = TerrorIds.migrate_legacy(data)
        self.assertEqual(moved, {"Classic/クラシック": frozenset({0, 4002}),
                                 "Unbound/アンバウンド": frozenset({2083, 4013}),
                                 TerrorIds.EIGHT_PAGES_KEY: frozenset({3014, 3046})})
        self.assertEqual(sorted(dropped), [("8 Pages/8ページ", 5), ("Classic/クラシック", 999)])
