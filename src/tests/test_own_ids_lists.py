"""続行リスト・続行表を独自のテラー番号にする: 今までと同じ判定になること・ファイルの移行・.tnl/.tnnl/主催リストの行き来・
Special/Moon の配り方・出ないテラー。

比べる元は、テストの中に凍結した今のやり方（ToN ListTool の番号・Special/Moon を予備に見る判定）と、番号を変える前の
ファイル（src/tests/data/terror_ids/）。ToN ListTool のフォルダは使わない。保存は一時フォルダだけ
"""
from tests.support import *  # noqa: F401,F403
import ContinueList
import ContinueTable

FROZEN = SRC_DIR / "tests" / "data" / "terror_ids"

CLASSIC = "Classic/クラシック"
FOG = "Fog/霧"
GHOST = "Ghost/ゴースト"
PUNISHED = "Punished/パニッシュ"
ALT = "Alternate/オルタネイト"
UNBOUND = "Unbound/アンバウンド"
MIDNIGHT = "Midnight/ミッドナイト"
EIGHT = "8 Pages/8ページ"
SPECIAL = "Special/Moon"
MYSTIC = "Mystic Moon/ミスティックムーン"
BLOOD = "Blood Moon/ブラッドムーン"
TWILIGHT = "Twilight/トワイライト"
SOLSTICE = "Solstice/ソルスティス"
BLOODBATH = "Bloodbath/ブラッドバス"


def _frozen(name):
    return json.loads((FROZEN / name).read_text(encoding="utf-8"))


# ── 凍結した今のやり方（ToN ListTool の番号。Classic と Moon は Special/Moon を予備に見る）──
OLD_LOG_TO_TNL = {
    "Classic": CLASSIC, "Classic.exe": "Classic.exe/Classic.exe", "Randomizer": "Randomizer/Randomizer",
    "8 Pages": EIGHT, "Fog": FOG, "Ghost": GHOST, "Punished": PUNISHED, "Sabotage": "Sabotage/サボタージュ",
    "Bloodbath": BLOODBATH, "Double Trouble": "Double Trouble/ダブルトラブル",
    "Bloodbath EX": "Bloodbath EX/ブラッドバスEX", "Cracked": "Cracked/狂気", "Alternate": ALT,
    "Midnight": MIDNIGHT, "Unbound": UNBOUND, "Run": "Run/走れ！", "Mystic Moon": MYSTIC, "Blood Moon": BLOOD,
    "Twilight": TWILIGHT, "Solstice": SOLSTICE, "Fog (Alternate)": FOG, "Ghost (Alternate)": GHOST,
    "Sabotage star": "Sabotage star/サボタージュスター", "Sabotage murder": "Sabotage murder/サボタージュマーダー",
    "Special": SPECIAL, "Moon": SPECIAL}
OLD_SPECIAL_MOON_ROUNDS = {"Classic", "Mystic Moon", "Blood Moon", "Twilight", "Solstice"}
MOON_ROUNDS = ("Mystic Moon", "Blood Moon", "Twilight", "Solstice")


def old_list_continues(keep, ids, round_type):
    key = OLD_LOG_TO_TNL.get(round_type, round_type)
    hit = key in keep and any(t in keep[key] for t in ids)
    if not hit and round_type in OLD_SPECIAL_MOON_ROUNDS:
        hit = SPECIAL in keep and any(t in keep[SPECIAL] for t in ids)
    return hit


# 出ないテラー（前の番号）: タブごと
DISABLED_TABS = {"ghost": ("Ghost", "Ghost (Alternate)"), "fog": ("Fog", "Fog (Alternate)"),
                 "punished": ("Punished",), "midnight": ("Midnight",),
                 "bloodbath": ("Bloodbath", "Double Trouble", "Bloodbath EX")}


def _disabled_legacy_by_round():
    by_round = {}
    for legacy, (_name, rounds) in _frozen("disabled_rounds.json").items():
        for round_name in rounds:
            for round_type in DISABLED_TABS.get(round_name, ()):
                by_round.setdefault(round_type, set()).add(int(legacy))
    return by_round


def _old_slots():
    return {key: list(ids) for key, ids in _frozen("continue_slots.json")["rounds"]}


def _candidates(round_type):
    """そのラウンドに出うるテラー（前の番号）: 前のラウンドの枠に、置き換えで出るもの（Variant・Glorbo・Foxy・Neo Pilot）を
    足して、出ないテラーを引いたもの。Moon・Special のラウンドは Special/Moon の枠にある Moon・Rift Monsters・
    Meatball Man・Gigabytes"""
    old = _old_slots()
    key = OLD_LOG_TO_TNL.get(round_type, round_type)
    ids = set(old.get(key, ()))
    if round_type == "Classic":
        ids |= {190, 191, 314}
    elif round_type in MOON_ROUNDS:
        ids = {196, 197, 198, 199, 312, 313, 314}
    elif round_type in ("Special", "Moon"):
        ids = {312, 313, 314}
    elif round_type == "Punished":
        ids |= {317}
    elif round_type in ("Alternate", "Fog", "Midnight", "Fog (Alternate)"):
        ids |= {315, 316}
    elif round_type == "8 Pages":
        ids = {e["list_id"] for e in _frozen("terrors.json")["8pages"]}
    ids -= _disabled_legacy_by_round().get(round_type, set())
    ids.discard(1283)           # Self Inserts (Bloodthirsty) の仮の番号。流れには出ない（番号は Self Inserts のまま）
    return sorted(ids)


def _random_old_list(rng, density=0.3, moons_equal=True, listtool=True):
    """前の形のリスト。listtool なら ListTool のファイルの形（1283 は ListTool に無いので入れない）"""
    old = _old_slots()
    data = {}
    for key, ids in old.items():
        chosen = {i for i in ids if rng.random() < density and not (listtool and i == 1283)}
        if chosen:
            data[key] = chosen
    if moons_equal and (MYSTIC in data or BLOOD in data):
        both = data.get(MYSTIC, set()) | data.get(BLOOD, set())      # ListTool の Mystic Moon と Blood Moon は同じ中身
        data[MYSTIC], data[BLOOD] = set(both), set(both)
    return data


def _skip(round_type, t, old):
    """わざと変わるところ（詳細設計 §6）は同じ結果のテストから外す"""
    moonish = old.get(SPECIAL, set()) | old.get(MYSTIC, set()) | old.get(BLOOD, set())
    if t in (315, 316, 317) and t in moonish:
        return True             # Neo Pilot・Foxy・Glorbo: Special/Moon にあれば Alternate・Fog・Midnight・Punished でも続行
    only_moon_key = (old.get(MYSTIC, set()) | old.get(BLOOD, set())) - old.get(SPECIAL, set())
    if round_type in ("Twilight", "Solstice", "Special") and t in only_moon_key:
        return True             # Mystic Moon・Blood Moon にだけ入れていたものが、ほかの Moon・Special でも効く
    return False


class TestSameJudgement(unittest.TestCase):
    """完了条件 2: 同じリストなら、どのラウンド・どのテラーでも今までと同じ結果"""

    ROUNDS = [r for r in OLD_LOG_TO_TNL if r != "Moon"]       # Moon はログに出ない（ツールからも無くした）

    def _compare(self, old, new, rounds=None):
        checked = 0
        for round_type in rounds or self.ROUNDS:
            for t in _candidates(round_type):
                if _skip(round_type, t, old):
                    continue
                self.assertEqual(
                    RoundDecision.list_continues(new, [flow_id(t, round_type == "8 Pages")], round_type),
                    old_list_continues(old, [t], round_type), (round_type, t))
                checked += 1
        return checked

    def test_random_lists_from_the_listtool_files(self):
        rng = random.Random(20261023)
        checked = 0
        for _ in range(40):
            old = _random_old_list(rng)
            new = MatchTNL.own_list_from_listtool(old)
            checked += self._compare(old, new)
        self.assertGreater(checked, 50000)

    def test_an_empty_list_and_a_full_list(self):
        self.assertGreater(self._compare({}, MatchTNL.own_list_from_listtool({})), 1000)
        full = {key: {i for i in ids if i != 1283} for key, ids in _old_slots().items()}
        self.assertGreater(self._compare(full, MatchTNL.own_list_from_listtool(full)), 1000)

    def test_a_list_with_only_special_moon(self):
        for ids in (_old_slots()[SPECIAL], [192], [190, 191], [314], [196], [312, 313]):
            old = {SPECIAL: set(ids)}
            self._compare(old, MatchTNL.own_list_from_listtool(old))

    def test_one_terror_at_a_time_in_every_key(self):
        for key, ids in _old_slots().items():
            for tid in ids:
                if tid == 1283:
                    continue
                old = {key: {tid}}
                self._compare(old, MatchTNL.own_list_from_listtool(old),
                              rounds=[r for r in self.ROUNDS if OLD_LOG_TO_TNL.get(r) in (key, SPECIAL)])

    def test_several_terrors_at_once(self):
        rng = random.Random(7)
        for _ in range(500):
            round_type = rng.choice(["Midnight", "Bloodbath", "Double Trouble", "Bloodbath EX", "Classic", "Fog"])
            pool = _candidates(round_type)
            ids = rng.sample(pool, rng.choice([2, 3]))
            old = _random_old_list(rng, density=0.15)
            if any(_skip(round_type, t, old) for t in ids):
                continue
            new = MatchTNL.own_list_from_listtool(old)
            self.assertEqual(RoundDecision.list_continues(new, [flow_id(t) for t in ids], round_type),
                             old_list_continues(old, ids, round_type), (round_type, ids))

    def test_the_tool_own_list_of_the_previous_version_moves_to_the_same_judgement(self):
        """前の版のツールの続行リスト（v1。Moon のタブは 4 つのキーに同じ中身を書く）"""
        rng = random.Random(99)
        for _ in range(20):
            old = _random_old_list(rng, listtool=False)
            for key in (TWILIGHT, SOLSTICE):
                if MYSTIC in old:
                    old[key] = set(old[MYSTIC])
            new, _dropped = ContinueList.migrate_legacy(old)
            self._compare(old, new)

    def test_a_terror_removed_after_surviving_no_longer_continues(self):
        """生存で外す: 外した番号は、そのラウンドの判定で続行でなくなる"""
        rng = random.Random(5)
        for _ in range(30):
            old = _random_old_list(rng, density=0.6)
            new = MatchTNL.own_list_from_listtool(old)
            round_type = rng.choice(["Classic", "Fog", "Alternate", "Unbound", "Punished"])
            t = rng.choice(_candidates(round_type))
            after, _removed = ContinueList.remove_survived(new, round_type, [own(t)])
            self.assertFalse(RoundDecision.list_continues(after, [flow_id(t)], round_type), (round_type, t))

    def test_the_sabotage_wishes_are_the_same(self):
        for ids in ([5], [40], [5, 40]):
            wishes = {"a": {GroupRound.SABOTAGE_STAR_KEY: {5}}, "b": {GroupRound.SABOTAGE_MURDER_KEY: {40}}}
            new = {name: {k: frozenset(own(i) for i in v) for k, v in w.items()} for name, w in wishes.items()}
            self.assertEqual(
                GroupRound.decide(config.INSTANCE_YAKIIMO, "Sabotage", [flow_id(i) for i in ids],
                                  sus_players=["b"], host_wishes=new, follow_host=True),
                GroupRound.decide(config.INSTANCE_YAKIIMO, "Sabotage", ids,
                                  sus_players=["b"], host_wishes=wishes, follow_host=True))


class TestWhatChangesOnPurpose(unittest.TestCase):
    """詳細設計 §6: 判定がわざと変わるところ"""

    def _loaded(self, data):
        return MatchTNL.own_list_from_listtool(data)

    def test_cold_night_continues_for_rift_monsters(self):
        keep = self._loaded({SPECIAL: {312}})
        self.assertTrue(RoundDecision.list_continues(keep, [flow_id(312)], "Cold Night"))
        self.assertFalse(old_list_continues({SPECIAL: {312}}, [312], "Cold Night"), "今まではキーが無く常に自爆")

    def test_glorbo_in_special_moon_continues_in_punished(self):
        keep = self._loaded({SPECIAL: {317}})
        self.assertEqual(keep, {PUNISHED: frozenset({TerrorIds.GLORBO})})
        self.assertTrue(RoundDecision.list_continues(keep, [flow_id(317)], "Punished"))
        self.assertFalse(old_list_continues({SPECIAL: {317}}, [317], "Punished"))

    def test_foxy_and_neo_pilot_in_special_moon_continue_in_the_alternate_fog_and_midnight_rounds(self):
        keep = self._loaded({SPECIAL: {315, 316}})
        for round_type in ("Alternate", "Fog", "Midnight"):
            self.assertTrue(RoundDecision.list_continues(keep, [flow_id(315)], round_type), round_type)
            self.assertTrue(RoundDecision.list_continues(keep, [flow_id(316)], round_type), round_type)
            self.assertFalse(old_list_continues({SPECIAL: {315, 316}}, [315], round_type), round_type)
        self.assertFalse(RoundDecision.list_continues(keep, [flow_id(315)], "Ghost"), "Ghost には出ない")

    def test_a_moon_item_in_one_key_only_works_in_the_other_moon_rounds(self):
        keep = self._loaded({MYSTIC: {196}})
        for round_type in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice", "Special", "Cold Night"):
            self.assertTrue(RoundDecision.list_continues(keep, [flow_id(196)], round_type), round_type)
        self.assertFalse(old_list_continues({MYSTIC: {196}}, [196], "Twilight"))


class TestWhereSpecialMoonGoes(unittest.TestCase):
    """ListTool の Special/Moon・Mystic Moon・Blood Moon を、ツールの置き場所へ配る（詳細設計 §5 の表）"""

    PLACES = {
        # ListTool の番号: 行き先のキー
        190: [CLASSIC], 191: [CLASSIC],
        192: [CLASSIC],                       # Bloodthirsty: 前の版が Special/Moon を見たのは Classic と Moon だけ
        196: [SPECIAL], 197: [SPECIAL], 198: [SPECIAL], 199: [SPECIAL],
        312: [SPECIAL], 313: [SPECIAL],
        314: [CLASSIC, SPECIAL],              # Gigabytes
        315: [ALT, FOG, MIDNIGHT], 316: [ALT, FOG, MIDNIGHT],
        317: [PUNISHED],
    }

    def test_each_terror_goes_to_its_places(self):
        for lt, keys in self.PLACES.items():
            got = MatchTNL.own_list_from_listtool({SPECIAL: {lt}})
            self.assertEqual(sorted(got), sorted(keys), lt)
            for ids in got.values():
                self.assertEqual(ids, frozenset({TerrorIds.TABLE.by_listtool[lt]}), lt)

    def test_mystic_and_blood_moon_go_to_special_moon_only(self):
        for key in (MYSTIC, BLOOD):
            self.assertEqual(MatchTNL.own_list_from_listtool({key: {196, 312, 314}}),
                             {SPECIAL: frozenset({4003, 4007, 4009})})
            self.assertEqual(MatchTNL.own_list_from_listtool({key: {192, 315}}), {},
                             "Special/Moon の枠に無いものは捨てる")

    def test_other_keys_are_not_touched_and_the_three_are_merged(self):
        got = MatchTNL.own_list_from_listtool({CLASSIC: {5, 192}, SPECIAL: {192, 196}, MYSTIC: {197}})
        self.assertEqual(got, {CLASSIC: frozenset({5, 4002}), SPECIAL: frozenset({4003, 4004})})

    def test_the_slots_say_the_same_as_the_table_in_the_design(self):
        slots = MatchTNL.slots().by_key
        self.assertEqual(slots[SPECIAL], frozenset(range(4003, 4010)))
        self.assertEqual({4000, 4001, 4009} - slots[CLASSIC], set())
        self.assertEqual(TerrorIds.GLORBO in slots[PUNISHED], True)
        self.assertEqual(TerrorIds.SELF_INSERTS_BLOODTHIRSTY in slots[UNBOUND], True)
        for key in (ALT, FOG, MIDNIGHT):
            self.assertIn(TerrorIds.NEO_PILOT, slots[key])
            self.assertIn(TerrorIds.FOXY, slots[key])
        self.assertNotIn(TerrorIds.NEO_PILOT, slots[GHOST])
        self.assertNotIn(TerrorIds.FOXY, slots[GHOST])
        self.assertNotIn(TerrorIds.BLOODTHIRSTY_CREATURE, slots[SPECIAL], "Bloodthirsty は各ラウンド。Special/Moon には置かない")
        self.assertFalse({4003, 4004, 4005, 4006, 4007, 4008} & slots[CLASSIC])

    def test_bloodthirsty_is_in_every_round_the_listtool_has_192(self):
        for key, ids in _old_slots().items():
            if key in (SPECIAL, MYSTIC, BLOOD):
                continue
            self.assertEqual(TerrorIds.BLOODTHIRSTY_CREATURE in MatchTNL.slots().by_key.get(key, ()),
                             192 in ids, key)


class TestNotAppearing(unittest.TestCase):
    """出ないテラー: 枠から外してある・読み込みで外れる（debug.log に件数）"""

    def test_the_slots_have_no_terror_that_the_round_does_not_have(self):
        by_key = MatchTNL.slots().by_key
        counts = {}
        for round_type, legacy_ids in _disabled_legacy_by_round().items():
            key = MatchTNL.LOG_TO_TNL[round_type]
            gone = {own(i) for i in legacy_ids}
            self.assertFalse(gone & by_key[key], round_type)
            counts[round_type] = len(gone)
        self.assertEqual(counts, {"Ghost": 22, "Ghost (Alternate)": 22, "Fog": 1, "Fog (Alternate)": 1,
                                  "Punished": 2, "Midnight": 4, "Bloodbath": 4, "Double Trouble": 4,
                                  "Bloodbath EX": 4})

    def test_their_replacements_are_not_in_those_rounds_either(self):
        by_key = MatchTNL.slots().by_key
        legacy = _disabled_legacy_by_round()
        for round_type, source, replaced in (("Ghost", 136, TerrorIds.FOXY), ("Ghost", 163, TerrorIds.NEO_PILOT)):
            self.assertIn(source, legacy[round_type])
            self.assertNotIn(replaced, by_key[MatchTNL.LOG_TO_TNL[round_type]])

    def test_the_eight_pages_key_has_only_the_62_rows(self):
        self.assertEqual(MatchTNL.slots().by_key[EIGHT], TerrorIds.eight_pages_ids())
        self.assertEqual(len(MatchTNL.slots().by_key[EIGHT]), 62)

    def test_loading_drops_them_and_writes_one_line(self):
        data = {GHOST: {87, 86, 5}, EIGHT: {0, 5, 135}, CLASSIC: {87}}      # 87 は Ghost に出ない BFF。5 は 8 Pages に出ない
        with patch.object(DebugLog, "write") as write:
            got = MatchTNL.own_list_from_listtool(data, "テスト")
        self.assertEqual(got, {GHOST: frozenset({86, 5}), EIGHT: frozenset({3014, 3050}),
                               CLASSIC: frozenset({87})})
        lines = [c.args[0] for c in write.call_args_list]
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(any("8 Pages に出ないテラー 1 件" in line for line in lines), lines)
        self.assertTrue(any("そのラウンドに出ないテラー 1 件" in line for line in lines), lines)

    def test_nothing_is_written_when_nothing_is_dropped(self):
        with patch.object(DebugLog, "write") as write:
            MatchTNL.own_list_from_listtool({CLASSIC: {5}, FOG: {7}}, "テスト")
        write.assert_not_called()

    def test_the_host_list_does_not_write_the_same_line_every_time(self):
        data = {GHOST: {87}}
        TerrorIds._reported.discard(("dropped", ("主催リスト", 1)))
        with patch.object(DebugLog, "write") as write:
            for _ in range(5):
                MatchTNL.own_list_from_listtool(data, "主催リスト", once=True)
        self.assertEqual(write.call_count, 1)

    def test_a_key_that_ends_up_empty_is_not_kept(self):
        self.assertEqual(MatchTNL.own_list_from_listtool({GHOST: {87}}), {})


class TestListMigration(unittest.TestCase):
    """continue_list.json（v1 → v2）: 1 回だけ独自の番号へ。.listtool.bak が残る"""

    V1 = {"version": 1, "list_name": "リスト",
          "data": {CLASSIC: [3, 7, 190, 192], ALT: [135, 150], UNBOUND: [283, 1283],
                   MYSTIC: [196, 312], BLOOD: [196, 312], TWILIGHT: [196, 312], SOLSTICE: [196, 312],
                   SPECIAL: [192, 314, 317, 315], EIGHT: [0, 135, 172, 5], GHOST: [87, 86]}}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "continue_list.json"
        self.bak = self.dir / "continue_list.json.listtool.bak"

    def _write(self, raw=None):
        self.path.write_text(json.dumps(raw or self.V1, ensure_ascii=False), encoding="utf-8")

    def test_the_numbers_move_and_the_special_moon_content_is_distributed(self):
        self._write()
        with patch.object(DebugLog, "write") as write:
            data, meta = ContinueList.load(self.path)
        self.assertEqual(meta, {"list_name": "リスト"})
        self.assertEqual({k: set(v) for k, v in data.items()}, {
            CLASSIC: {3, 7, 4000, 4002, 4009},                    # 190・192 + Special/Moon から 192・314
            ALT: {1001, 1016, 4010},                              # Special/Moon の 315
            UNBOUND: {2083, 4013},
            SPECIAL: {4003, 4007, 4009},
            EIGHT: {3014, 3050, 3046},                            # 5 は 8 Pages に出ない
            GHOST: {86},                                          # BFF は Ghost に出ない
            PUNISHED: {4012},                                     # Special/Moon の 317
            FOG: {4010}, MIDNIGHT: {4010}})
        lines = " ".join(c.args[0] for c in write.call_args_list)
        self.assertIn("独自の番号に移しました", lines)
        self.assertIn("ラウンド", lines)

    def test_the_file_is_rewritten_as_v2_and_the_original_is_kept(self):
        self._write()
        original = self.path.read_bytes()
        ContinueList.load(self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 2)
        self.assertEqual(raw["data"][CLASSIC], [3, 7, 4000, 4002, 4009])
        self.assertEqual(self.bak.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["continue_list.json", "continue_list.json.listtool.bak"], "一時ファイルは残らない")

    def test_the_second_time_changes_nothing(self):
        self._write()
        first, _meta = ContinueList.load(self.path)
        body = self.path.read_bytes()
        bak = self.bak.read_bytes()
        with patch.object(DebugLog, "write") as write:
            second, _meta = ContinueList.load(self.path)
        self.assertEqual(first, second)
        self.assertEqual(self.path.read_bytes(), body)
        self.assertEqual(self.bak.read_bytes(), bak)
        write.assert_not_called()

    def test_an_existing_backup_is_never_overwritten(self):
        self._write()
        self.bak.write_bytes(b"old")
        ContinueList.load(self.path)
        self.assertEqual(self.bak.read_bytes(), b"old")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["version"], 2)

    def test_when_the_backup_fails_the_file_is_not_rewritten_but_the_list_is_used(self):
        self._write()
        original = self.path.read_bytes()
        with patch.object(ContinueList.shutil, "copy2", side_effect=OSError("disk")), \
             patch.object(DebugLog, "write"):
            data, _meta = ContinueList.load(self.path)
        self.assertEqual(self.path.read_bytes(), original, "書き換えない")
        self.assertFalse(self.bak.exists())
        self.assertIn(3, data[CLASSIC], "メモリの上では独自の番号で使う")

    def test_numbers_that_are_not_in_the_table_are_dropped_with_one_line(self):
        self._write({"version": 1, "data": {CLASSIC: [3, 999], FOG: [175]}})
        with patch.object(DebugLog, "write") as write:
            data, _meta = ContinueList.load(self.path)
        self.assertEqual({k: set(v) for k, v in data.items()}, {CLASSIC: {3}})
        lines = [c.args[0] for c in write.call_args_list]
        self.assertEqual(len([l for l in lines if "表に無い番号を落としました: 2 件" in l]), 1, lines)

    def test_the_number_of_eight_pages_rows_is_the_62_that_appear(self):
        every = [e["list_id"] for e in _frozen("terrors.json")["8pages"]]
        self._write({"version": 1, "data": {EIGHT: list(range(0, 170)) + [170, 171, 172, 173, 174, 177, 193, 194]}})
        data, _meta = ContinueList.load(self.path)
        self.assertEqual(len(data[EIGHT]), 62)
        self.assertEqual(sorted(data[EIGHT]), sorted(3000 + e["id"] for e in _frozen("terrors.json")["8pages"]))
        self.assertEqual(len(every), 62)

    def test_an_empty_or_new_file_is_not_rewritten(self):
        self._write({"version": 2, "list_name": "x", "data": {CLASSIC: [3]}})
        body = self.path.read_bytes()
        ContinueList.load(self.path)
        self.assertEqual(self.path.read_bytes(), body)
        self.assertFalse(self.bak.exists())
        self._write({"version": 1, "list_name": "x", "data": {}})
        ContinueList.load(self.path)
        self.assertFalse(self.bak.exists(), "直す番号が無い")


class TestTableMigration(unittest.TestCase):
    """continue_table.json（v1・v2 → v3）"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "continue_table.json"
        self.bak = self.dir / "continue_table.json.listtool.bak"

    V2 = {"version": 2,
          "disabled_presets": {"既定": {CLASSIC: [5, 190], ALT: [135], SPECIAL: [196, 317], UNBOUND: [1283]},
                               "別": {EIGHT: [0, 5]}},
          "default_presets": {"既定": {FOG: [150, 13], MYSTIC: [197]}},
          "windows": {"1": {"disabled": "別", "default": None}},
          "item_rules": [{"item": 70, "disabled": "別"}]}

    def test_v2_moves_to_v3_keeping_windows_and_item_rules(self):
        self.path.write_text(json.dumps(self.V2, ensure_ascii=False), encoding="utf-8")
        original = self.path.read_bytes()
        store = ContinueTable.load(self.path)
        self.assertEqual({k: set(v) for k, v in store.disabled_presets["既定"].items()},
                         {CLASSIC: {5, 4000}, ALT: {1001}, SPECIAL: {4003}, PUNISHED: {4012}, UNBOUND: {4013}})
        self.assertEqual({k: set(v) for k, v in store.disabled_presets["別"].items()}, {EIGHT: {3014}})
        self.assertEqual({k: set(v) for k, v in store.default_presets["既定"].items()},
                         {FOG: {1016}, SPECIAL: {4004}}, "Fog の 13 は出ない・Blood Moon は Special/Moon へ")
        self.assertEqual(store.windows, {1: {"disabled": "別", "default": None}})
        self.assertEqual(store.item_rules, (ContinueTable.ItemRule(70, "別", ContinueTable.KEEP),))
        self.assertEqual(self.bak.read_bytes(), original)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["version"], 3)

    def test_v1_moves_to_v3_and_the_second_time_changes_nothing(self):
        self.path.write_text(json.dumps({"version": 1, "use_disabled": True, "use_default": False,
                                         "disabled": {CLASSIC: [5], ALT: [164]}, "default": {UNBOUND: [283]}}),
                             encoding="utf-8")
        store = ContinueTable.load(self.path)
        self.assertEqual({k: set(v) for k, v in store.disabled_presets["既定"].items()},
                         {CLASSIC: {5}, ALT: {1030}})
        self.assertEqual({k: set(v) for k, v in store.default_presets["既定"].items()}, {UNBOUND: {2083}})
        self.assertEqual(store.windows[1]["default"], None, "use_default が false だった種類は全窓「なし」")
        body = self.path.read_bytes()
        with patch.object(DebugLog, "write") as write:
            again = ContinueTable.load(self.path)
        self.assertEqual(again, store)
        self.assertEqual(self.path.read_bytes(), body)
        write.assert_not_called()

    def test_a_failed_backup_does_not_rewrite(self):
        self.path.write_text(json.dumps(self.V2, ensure_ascii=False), encoding="utf-8")
        original = self.path.read_bytes()
        with patch.object(ContinueList.shutil, "copy2", side_effect=OSError("disk")), patch.object(DebugLog, "write"):
            store = ContinueTable.load(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIn(5, store.disabled_presets["既定"][CLASSIC])
        self.assertFalse(self.bak.exists())

    def test_an_existing_backup_is_not_overwritten(self):
        self.path.write_text(json.dumps(self.V2, ensure_ascii=False), encoding="utf-8")
        self.bak.write_bytes(b"older")
        ContinueTable.load(self.path)
        self.assertEqual(self.bak.read_bytes(), b"older")

    def test_numbers_not_in_the_table_are_dropped_with_one_line(self):
        self.path.write_text(json.dumps({"version": 2, "disabled_presets": {"既定": {CLASSIC: [5, 999]}},
                                         "default_presets": {"既定": {}}, "windows": {}}), encoding="utf-8")
        with patch.object(DebugLog, "write") as write:
            store = ContinueTable.load(self.path)
        self.assertEqual(store.disabled_presets["既定"], {CLASSIC: frozenset({5})})
        self.assertEqual(len([c for c in write.call_args_list if "表に無い番号を落としました" in c.args[0]]), 1)


class TestTnlRoundTrip(unittest.TestCase):
    """完了条件 4: .tnl を読んで書き出すと、ListTool の番号と値は元と同じ（出ないテラー・ツールに無いものを除く）"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _tnl(self, values):
        """frozen の枠の全部を並べた ListTool の .tnl。values は {キー: {番号}} で ON にするもの"""
        data = {key: {str(i): int(i in values.get(key, ())) for i in ids} for key, ids in _old_slots().items()}
        path = self.dir / "in.tnl"
        path.write_text(json.dumps({"format": "tnl_json", "version": 105, "creator": "ListTool", "list_name": "元",
                                    "data": data}), encoding="utf-8")
        return path, data

    def _roundtrip(self, values):
        path, data = self._tnl(values)
        loaded, _meta = ContinueList.from_tnl(path)
        out = self.dir / "out.tnl"
        self.assertTrue(ContinueList.write_tnl(loaded, {"list_name": "元"}, out))
        return data, json.loads(out.read_text(encoding="utf-8"))["data"], loaded

    def test_a_list_without_overlaps_comes_back_the_same(self):
        values = {CLASSIC: {3, 7, 100}, FOG: {150, 88}, ALT: {140, 160}, UNBOUND: {205, 283}, EIGHT: {0, 135, 172},
                  PUNISHED: {61, 192}, SPECIAL: {196, 312, 314}}
        before, after, _loaded = self._roundtrip(values)
        disabled = _disabled_legacy_by_round()
        appearing = {legacy for e in _frozen("terrors.json")["8pages"] for legacy in [e["list_id"]]}
        for key, slots in before.items():
            if key in (SPECIAL, MYSTIC, BLOOD):
                continue
            for slot, value in slots.items():
                legacy = int(slot)
                round_type = next((r for r, k in OLD_LOG_TO_TNL.items() if k == key), None)
                if key == EIGHT and legacy not in appearing:
                    self.assertNotIn(slot, after[key], "8 Pages に出ない番号は書き出さない")
                    continue
                if legacy == 1283:
                    self.assertNotIn(slot, after[key], "Self Inserts (Bloodthirsty) は ListTool に無い")
                    continue
                if legacy in disabled.get(round_type, ()):
                    self.assertNotIn(slot, after[key], f"{key}: 出ないテラーは書き出さない")
                    continue
                self.assertEqual(after[key].get(slot), value, (key, slot))
        self.assertEqual(set(after) - set(before), set())

    def test_the_three_moon_keys_are_rebuilt_with_the_same_set(self):
        _before, after, loaded = self._roundtrip({SPECIAL: {196, 312, 314}, CLASSIC: {192}})
        on = {k: {int(s) for s, v in after[k].items() if v} for k in (SPECIAL, MYSTIC, BLOOD)}
        self.assertEqual(on[SPECIAL], on[MYSTIC])
        self.assertEqual(on[SPECIAL], on[BLOOD])
        self.assertEqual(on[SPECIAL], {196, 312, 314, 192}, "Classic に置いた Bloodthirsty も Special/Moon に書く")
        self.assertEqual(loaded[SPECIAL], frozenset({4003, 4007, 4009}))

    def test_reading_it_again_gives_the_same_tool_list(self):
        values = {CLASSIC: {3, 192, 100}, SPECIAL: {196, 314, 317, 315}, PUNISHED: {50}, FOG: {88}, EIGHT: {0}}
        path, _data = self._tnl(values)
        first, _meta = ContinueList.from_tnl(path)
        out = self.dir / "out.tnl"
        ContinueList.write_tnl(first, {"list_name": "元"}, out)
        second, _meta = ContinueList.from_tnl(out)
        self.assertEqual(second, first)

    def test_a_terror_listtool_has_no_number_for_is_not_written(self):
        data = {UNBOUND: frozenset({2083, TerrorIds.SELF_INSERTS_BLOODTHIRSTY}), CLASSIC: frozenset({4000, 4001, 4009})}
        out = self.dir / "out.tnl"
        ContinueList.write_tnl(data, {}, out)
        raw = json.loads(out.read_text(encoding="utf-8"))["data"]
        self.assertEqual(raw[UNBOUND]["283"], 1)
        self.assertNotIn("1283", raw[UNBOUND])
        for slot in ("190", "191", "314"):
            self.assertNotIn(slot, raw[CLASSIC], "ListTool のラウンドのキーに枠が無い")
        self.assertEqual({k for k, v in raw[SPECIAL].items() if v}, {"314"}, "Gigabytes は Special/Moon に書く")
        self.assertNotIn("190", raw[SPECIAL])

    def test_the_eight_pages_key_is_written_with_the_list_ids(self):
        out = self.dir / "out.tnl"
        ContinueList.write_tnl({EIGHT: frozenset({3014, 3046, 3050})}, {}, out)
        raw = json.loads(out.read_text(encoding="utf-8"))["data"][EIGHT]
        self.assertEqual({int(k) for k, v in raw.items() if v}, {0, 172, 135})
        self.assertEqual(len(raw), 62)


class TestTnnl(unittest.TestCase):
    def test_a_forced_skip_file_is_converted_and_distributed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tnnl"
            path.write_text(json.dumps({"targets_dict": {
                CLASSIC: [5, 192], ALT: [135], EIGHT: [0, 5], SPECIAL: [314, 317], GHOST: [87, 86],
                "Fog (Alternate)/霧 (Alternate)": [3]}}), encoding="utf-8-sig")
            got = ContinueTable.from_tnnl(path)
        self.assertEqual({k: set(v) for k, v in got.items()},
                         {CLASSIC: {5, 4002, 4009}, ALT: {1001}, EIGHT: {3014}, SPECIAL: {4009},
                          PUNISHED: {4012}, GHOST: {86}})

    def test_the_tnl_importer_for_the_table_is_converted_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tnl"
            path.write_text(json.dumps({"data": {SPECIAL: {"196": 1, "314": 1}, ALT: {"135": 1}}}), encoding="utf-8")
            got = ContinueTable.from_tnl(path)
        self.assertEqual({k: set(v) for k, v in got.items()}, {SPECIAL: {4003, 4009}, ALT: {1001}, CLASSIC: {4009}})


class TestHostListBits(unittest.TestCase):
    def test_bits_of_135_283_317_175(self):
        """主催リストのビット列 {135, 283, 317, 175} → 独自の番号。317 は Special/Moon から Punished へ。
        175 は表に無いので 1 回だけ書いて捨てる"""
        TerrorIds._reported.discard(("listtool", 175))
        with tempfile.TemporaryDirectory() as tmp:
            host = str(Path(tmp) / "host_state.sqlite3")
            bits = wish_bits({135, 175, 283, 317})
            write_host_state(host, [[(0, "ひと", {ALT: bits, UNBOUND: bits, SPECIAL: bits})]])
            with patch.object(DebugLog, "write") as write:
                keep_on, _meta, wishes = MatchTNL.load_host_state(host)
                MatchTNL.load_host_state(host)          # 数秒ごとに読む
        self.assertEqual(keep_on[ALT], {1001})
        self.assertEqual(keep_on[UNBOUND], {2083})
        self.assertEqual(keep_on[PUNISHED], {4012})
        self.assertNotIn(SPECIAL, keep_on, "Special/Moon に入るのは Moon の枠のものだけ")
        self.assertEqual(wishes["ひと"][PUNISHED], {4012})
        self.assertEqual(len([c for c in write.call_args_list if "175" in c.args[0]]), 1, "175 は 1 回だけ書く")


class TestFlowStillWorksWithTheSharedListsInTheWindow(unittest.TestCase):
    def test_a_window_judges_a_loaded_listtool_file_like_before(self):
        """ListTool の .tnl（Classic の 5・Alternate の 135・Special/Moon の 314）を読んだ窓の判定"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tnl"
            path.write_text(json.dumps({"data": {CLASSIC: {"5": 1}, ALT: {"135": 1}, SPECIAL: {"314": 1}}}),
                            encoding="utf-8")
            keep_on, _meta = MatchTNL.load_tnl(str(path))
        self.assertTrue(RoundDecision.list_continues(keep_on, [flow_id(5)], "Classic"))
        self.assertTrue(RoundDecision.list_continues(keep_on, [flow_id(135)], "Alternate"))
        self.assertTrue(RoundDecision.list_continues(keep_on, [flow_id(314)], "Classic"))
        self.assertFalse(RoundDecision.list_continues(keep_on, [flow_id(6)], "Classic"))
