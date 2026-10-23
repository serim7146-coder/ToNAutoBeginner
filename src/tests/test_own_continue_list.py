"""GN: 続行リストをツールの中で持つ（ContinueList）・画面で直す・生存したら自動で外す。

保存は一時フォルダ（support が config.SETTINGS_PATH を差し替える）だけ。ListTool のフォルダや本物の %APPDATA% には
書かない。ファイルを選ぶ・確認の画面は偽物。窓は作った直後に隠れる（support）。本物の入力は使わない"""
from tests.support import *  # noqa: F401,F403
import ContinueList
import ContinueTable
import ContinueTableGUI

CLASSIC = "Classic/クラシック"
FOG = "Fog/霧"
DOUBLE = "Double Trouble/ダブルトラブル"
MYSTIC = "Mystic Moon/ミスティックムーン"
BLOOD = "Blood Moon/ブラッドムーン"
SPECIAL = "Special/Moon"
MOON_ROUNDS = ("Mystic Moon", "Blood Moon", "Twilight", "Solstice", "Special", "Cold Night")   # Special/Moon の 6 ラウンド
MOON_A, MOON_B = TerrorIds.MYSTIC_MOON, TerrorIds.BLOOD_MOON        # 4003・4004
BLOODTHIRSTY = TerrorIds.BLOODTHIRSTY_CREATURE
LIST = ContinueTableGUI.LIST


def _sets(data):
    """{キー: frozenset} → 比べやすい {キー: set}"""
    return {key: set(ids) for key, ids in data.items()}


def _press(widget, text):
    """窓の中の、文字が text のボタンを押す（呼ばれるのは偽物の関数だけ）"""
    next(b for b in _widgets_of(widget, ttk.Button) if b.cget("text") == text).invoke()


def _reset_list(case):
    """今のリストを空に・保存先（テスト用の一時フォルダ）のファイルを消す。終わったら同じに戻す"""
    def clean():
        ContinueList.list_path().unlink(missing_ok=True)
        ContinueList.set_current({}, {"list_name": ""})
    clean()
    case.addCleanup(clean)


class TestRemoveSurvived(unittest.TestCase):
    """生存したテラーを、そのラウンドの種類のキーから外す（純粋な関数）"""

    def _remove(self, data, round_type, ids):
        before = {key: set(ids) for key, ids in data.items()}
        new, removed = ContinueList.remove_survived(data, round_type, ids)
        self.assertEqual(_sets(data), before, "元の dict は書き換えない")
        return new, removed

    def test_one_terror_of_classic_is_removed_from_classic_only(self):
        data = {CLASSIC: frozenset({3, 7}), FOG: frozenset({3})}
        new, removed = self._remove(data, "Classic", [3])
        self.assertEqual(_sets(new), {CLASSIC: {7}, FOG: {3}})
        self.assertEqual(removed, [(CLASSIC, 3)])

    def test_only_the_listed_ones_of_several_terrors_are_removed(self):
        data = {DOUBLE: frozenset({3, 9}), CLASSIC: frozenset({3})}
        new, removed = self._remove(data, "Double Trouble", [3, 5])
        self.assertEqual(_sets(new), {DOUBLE: {9}, CLASSIC: {3}})
        self.assertEqual(removed, [(DOUBLE, 3)])

    def test_every_listed_terror_of_a_multi_terror_round_is_removed(self):
        data = {DOUBLE: frozenset({3, 5, 9})}
        new, removed = self._remove(data, "Double Trouble", [3, 5])
        self.assertEqual(_sets(new), {DOUBLE: {9}})
        self.assertEqual(removed, [(DOUBLE, 3), (DOUBLE, 5)])

    def test_the_same_terror_twice_is_removed_once(self):
        new, removed = self._remove({"Midnight/ミッドナイト": frozenset({3})}, "Midnight", [3, 3, 3])
        self.assertEqual(new, {})
        self.assertEqual(removed, [("Midnight/ミッドナイト", 3)])

    def test_a_key_that_becomes_empty_is_dropped(self):
        new, _removed = self._remove({CLASSIC: frozenset({3}), FOG: frozenset({1})}, "Classic", [3])
        self.assertEqual(_sets(new), {FOG: {1}})

    def test_the_six_special_moon_rounds_remove_from_the_one_special_moon_key(self):
        """Mystic Moon・Blood Moon・Twilight・Solstice・Special・Cold Night は Special/Moon の 1 つのキー"""
        data = {SPECIAL: frozenset({MOON_A, MOON_B}), CLASSIC: frozenset({MOON_A})}
        for round_type in MOON_ROUNDS:
            new, removed = self._remove(data, round_type, [MOON_A])
            self.assertEqual(_sets(new), {SPECIAL: {MOON_B}, CLASSIC: {MOON_A}}, round_type)
            self.assertEqual(removed, [(SPECIAL, MOON_A)], round_type)

    def test_a_special_moon_terror_is_not_removed_from_the_other_rounds_keys(self):
        for round_type in ("Classic", "Fog"):
            data = {SPECIAL: frozenset({BLOODTHIRSTY}), FOG: frozenset({BLOODTHIRSTY})}
            new, removed = self._remove(data, round_type, [BLOODTHIRSTY])
            if round_type == "Classic":
                self.assertIs(new, data, "Classic のキーには無く、Special/Moon を予備に見ることはもう無い")
                self.assertEqual(removed, [])
            else:
                self.assertEqual(_sets(new), {SPECIAL: {BLOODTHIRSTY}})
                self.assertEqual(removed, [(FOG, BLOODTHIRSTY)])

    def test_when_the_round_key_has_it_special_moon_is_left_alone(self):
        data = {CLASSIC: frozenset({BLOODTHIRSTY}), SPECIAL: frozenset({BLOODTHIRSTY})}
        new, removed = self._remove(data, "Classic", [BLOODTHIRSTY])
        self.assertEqual(_sets(new), {SPECIAL: {BLOODTHIRSTY}})
        self.assertEqual(removed, [(CLASSIC, BLOODTHIRSTY)])

    def test_nothing_listed_changes_nothing(self):
        data = {CLASSIC: frozenset({7})}
        for round_type, ids in (("Classic", [3]), ("Fog", [7]), ("Classic", []), ("No Such Round", [7])):
            new, removed = self._remove(data, round_type, ids)
            self.assertIs(new, data, round_type)
            self.assertEqual(removed, [])
        new, removed = ContinueList.remove_survived({}, "Classic", [1])
        self.assertEqual((new, removed), ({}, []))

    def test_the_round_keys_follow_the_judgement(self):
        self.assertEqual(ContinueList.round_keys("Classic"), [CLASSIC])
        self.assertEqual(ContinueList.round_keys("Fog (Alternate)"), [FOG], "判定と同じく通常の Fog")
        for round_type in MOON_ROUNDS:
            self.assertEqual(ContinueList.round_keys(round_type), [SPECIAL], round_type)
        self.assertFalse(hasattr(ContinueList, "MOON_ROUNDS"))

    def test_after_removal_the_judgement_no_longer_continues(self):
        data = {CLASSIC: frozenset({3})}
        self.assertTrue(RoundDecision.list_continues(data, [3], "Classic"))
        new, _removed = ContinueList.remove_survived(data, "Classic", [3])
        self.assertFalse(RoundDecision.list_continues(new, [3], "Classic"))


class TestEditHelpers(unittest.TestCase):
    def test_added_removed_and_toggled_return_new_dicts(self):
        data = {CLASSIC: frozenset({1})}
        added = ContinueList.with_ids_added(data, [CLASSIC, FOG], [2, 3])
        self.assertEqual(_sets(added), {CLASSIC: {1, 2, 3}, FOG: {2, 3}})
        self.assertEqual(_sets(data), {CLASSIC: {1}})
        removed = ContinueList.with_ids_removed(added, [CLASSIC], [1, 2, 3])
        self.assertEqual(_sets(removed), {FOG: {2, 3}}, "空になったキーは持たない")
        self.assertEqual(_sets(ContinueList.toggled(data, [CLASSIC], 1)), {})
        self.assertEqual(_sets(ContinueList.toggled(data, [CLASSIC], 2)), {CLASSIC: {1, 2}})

    def test_toggling_an_id_present_in_only_one_of_several_keys_removes_it_everywhere(self):
        keys = [MYSTIC, BLOOD, SPECIAL]
        data = {MYSTIC: frozenset({196})}
        self.assertEqual(ContinueList.toggled(data, keys, 196), {})
        self.assertEqual(_sets(ContinueList.toggled({}, keys, 196)), {key: {196} for key in keys})

    def test_counts(self):
        self.assertEqual(ContinueList.counts({CLASSIC: frozenset({1, 2}), FOG: frozenset({3})}), (2, 3))
        self.assertEqual(ContinueList.counts({}), (0, 0))


class TestFiles(unittest.TestCase):
    def setUp(self):
        _reset_list(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = patch.object(DebugLog, "write")
        self.written = patcher.start()
        self.addCleanup(patcher.stop)

    def _tnl(self, name="a.tnl", data=None, list_name="リストA"):
        path = self.dir / name
        path.write_text(json.dumps({"list_name": list_name, "creator": "x", "created_at": "",
                                    "data": data if data is not None else {CLASSIC: {"3": 1, "4": 0, "7": 1}}}),
                        encoding="utf-8")
        return path

    def test_the_path_is_next_to_settings_json(self):
        self.assertEqual(ContinueList.list_path(), config.SETTINGS_PATH.parent / "continue_list.json")

    def test_a_missing_file_is_none(self):
        self.assertIsNone(ContinueList.load())
        self.assertIsNone(ContinueList.load(self.dir / "nothing.json"))

    def test_save_then_load_round_trips(self):
        data = {CLASSIC: frozenset({7, 3}), FOG: frozenset({1})}
        self.assertTrue(ContinueList.save(data, {"list_name": "リスト"}))
        raw = json.loads(ContinueList.list_path().read_text(encoding="utf-8"))
        self.assertEqual(raw, {"version": 2, "list_name": "リスト", "data": {CLASSIC: [3, 7], FOG: [1]}})
        loaded, meta = ContinueList.load()
        self.assertEqual(loaded, data)
        self.assertEqual(meta, {"list_name": "リスト"})
        self.assertEqual(sorted(p.name for p in ContinueList.list_path().parent.iterdir()
                                if p.name.startswith("continue_list")), ["continue_list.json"], "一時ファイルは残らない")

    def test_a_broken_file_starts_empty_and_is_noted(self):
        for text in ("not json", "[1, 2]", '{"data": 5}'):
            ContinueList.list_path().parent.mkdir(parents=True, exist_ok=True)
            ContinueList.list_path().write_text(text, encoding="utf-8")
            data, meta = ContinueList.load()
            self.assertEqual(data, {}, text)
            self.assertEqual(meta, {"list_name": ""}, text)
        self.assertTrue(self.written.called)

    def test_unusable_values_are_dropped_when_reading(self):
        ContinueList.list_path().parent.mkdir(parents=True, exist_ok=True)
        ContinueList.list_path().write_text(json.dumps(
            {"version": 2, "data": {CLASSIC: [1, "x", True, 2.5, 4], FOG: [], MYSTIC: "no"}}),
            encoding="utf-8")
        data, _meta = ContinueList.load()
        self.assertEqual(_sets(data), {CLASSIC: {1, 4}})

    def test_a_failed_save_returns_false_and_leaves_no_temp_file(self):
        target = self.dir / "dir.json"
        target.mkdir()                          # 置き換え先がフォルダ → os.replace が失敗する
        self.assertFalse(ContinueList.save({CLASSIC: frozenset({1})}, {"list_name": ""}, target))
        self.assertEqual([p.name for p in self.dir.iterdir()], ["dir.json"])

    def test_from_tnl_reads_like_the_judgement_does(self):
        path = self._tnl(data={CLASSIC: {"3": 1, "4": 0, "7": 1}, "Fog (Alternate)/霧 (Alternate)": {"1": 1},
                              FOG: {"2": 0}})
        data, meta = ContinueList.from_tnl(path)
        self.assertEqual(data, {CLASSIC: frozenset({3, 7})})
        self.assertEqual(data, {key: frozenset(ids) for key, ids in MatchTNL.load_tnl(str(path))[0].items()})
        self.assertEqual(meta, {"list_name": "リストA"})

    def test_from_tnl_raises_for_a_broken_file(self):
        path = self.dir / "bad.tnl"
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            ContinueList.from_tnl(path)

    # ── .tnl への書き出し ──
    def test_write_tnl_can_be_read_back_by_the_tnl_reader(self):
        data = {CLASSIC: frozenset({3, 7, BLOODTHIRSTY}), FOG: frozenset({own(150)}),
                SPECIAL: frozenset({MOON_A, TerrorIds.RIFT_MONSTERS})}
        path = self.dir / "out.tnl"
        self.assertTrue(ContinueList.write_tnl(data, {"list_name": "出力"}, path))
        keep_on, meta = MatchTNL.load_tnl(str(path))
        self.assertEqual(keep_on, _sets(data))
        self.assertEqual(meta["list_name"], "出力")
        self.assertEqual(ContinueList.from_tnl(path)[0], data)

    def test_write_tnl_lists_every_slot_as_0_or_1_in_the_tnl_shape(self):
        data = {CLASSIC: frozenset({3})}
        path = self.dir / "out.tnl"
        ContinueList.write_tnl(data, {"list_name": "出力"}, path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual({k: raw[k] for k in ("format", "version", "creator", "list_name")},
                         {"format": "tnl_json", "version": 105, "creator": "ToNAutoBeginner", "list_name": "出力"})
        self.assertRegex(raw["created_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        slots = json.loads((REPO_ROOT / "assets" / "data" / "continue_slots.json").read_text(encoding="utf-8"))
        tool_keys = [key for key, _ids in slots["rounds"] if key != SPECIAL]
        moon_keys = [key for key, _ids in slots["listtool"]["moon_keys"]]
        self.assertEqual(list(raw["data"]), tool_keys + moon_keys, "ラウンドの枠を全部・同じ順で。ListTool にだけあるキーが最後")
        self.assertEqual(moon_keys, [SPECIAL, MYSTIC, BLOOD])
        for key, ids in slots["rounds"]:
            if key == SPECIAL:
                continue
            visible = [i for i in ids if i < 4000 or str(i) in slots["listtool"]["variants"]
                       and key in slots["listtool"]["variants"][str(i)]]
            expected = [str(TerrorIds.to_listtool(i)) for i in visible]
            self.assertEqual(list(raw["data"][key]), expected, key)
        for key, ids in slots["listtool"]["moon_keys"]:
            self.assertEqual(list(raw["data"][key]), [str(TerrorIds.to_listtool(i)) for i in ids], key)
        for key, values in raw["data"].items():
            self.assertTrue(all(v in (0, 1) and isinstance(v, int) for v in values.values()), key)
        self.assertEqual(raw["data"][CLASSIC]["3"], 1)
        self.assertEqual(raw["data"][CLASSIC]["4"], 0)
        self.assertEqual(sum(sum(v.values()) for v in raw["data"].values()), 1)
        self.assertNotIn("190", raw["data"][CLASSIC], "HHI は ListTool のラウンドのキーに枠が無い")
        self.assertIn("192", raw["data"][CLASSIC], "Bloodthirsty は ListTool の Classic にも枠がある")
        self.assertNotIn("1283", raw["data"]["Unbound/アンバウンド"], "Self Inserts (Bloodthirsty) は ListTool に無い")

    def test_write_tnl_keeps_ids_and_keys_that_are_not_in_the_slot_table(self):
        data = {CLASSIC: frozenset({9999}), "Future Round/未来": frozenset({5})}
        path = self.dir / "out.tnl"
        ContinueList.write_tnl(data, {}, path)
        self.assertEqual(MatchTNL.load_tnl(str(path))[0], {"Future Round/未来": {5}},
                         "表に無い番号（ListTool の番号が無い）は書かない。枠の表に無いキーは書く")

    def test_write_tnl_does_not_touch_the_own_list_file(self):
        ContinueList.save({CLASSIC: frozenset({1})}, {"list_name": "x"})
        before = ContinueList.list_path().read_bytes()
        ContinueList.write_tnl({CLASSIC: frozenset({2})}, {"list_name": "y"}, self.dir / "out.tnl")
        self.assertEqual(ContinueList.list_path().read_bytes(), before)

    def test_a_failed_write_tnl_returns_false(self):
        target = self.dir / "dir.tnl"
        target.mkdir()
        self.assertFalse(ContinueList.write_tnl({}, {}, target))


class TestCurrent(unittest.TestCase):
    def setUp(self):
        _reset_list(self)

    def test_set_update_and_save_current(self):
        ContinueList.set_current({CLASSIC: frozenset({1})}, {"list_name": "n"})
        self.assertEqual(ContinueList.current(), {CLASSIC: frozenset({1})})
        self.assertEqual(ContinueList.current_meta(), {"list_name": "n"})
        new = ContinueList.update(lambda data: ContinueList.with_ids_added(data, [FOG], [2]))
        self.assertIs(new, ContinueList.current())
        self.assertEqual(ContinueList.current_meta(), {"list_name": "n"}, "名前はそのまま")
        ContinueList.set_current({})
        self.assertEqual(ContinueList.current_meta(), {"list_name": "n"})
        ContinueList.set_current({CLASSIC: frozenset({5})})
        self.assertTrue(ContinueList.save_current())
        self.assertEqual(ContinueList.load(), ({CLASSIC: frozenset({5})}, {"list_name": "n"}))

    def test_removing_while_editing_loses_neither_change(self):
        """窓ごとのスレッドの自動削除と画面の編集が同時に来ても、どちらも残る"""
        ContinueList.set_current({CLASSIC: frozenset(range(300))})
        stop = threading.Event()

        def remover():
            for tid in range(150):
                ContinueList.remove_survived_current("Classic", [tid])
            stop.set()

        thread = threading.Thread(target=remover, daemon=True)
        thread.start()
        for tid in range(1000, 1150):
            ContinueList.update(lambda data, t=tid: ContinueList.with_ids_added(data, [FOG], [t]))
        thread.join(10)
        self.assertEqual(ContinueList.current()[CLASSIC], frozenset(range(150, 300)))
        self.assertEqual(ContinueList.current()[FOG], frozenset(range(1000, 1150)))


# ── LogMonitor ───────────────────────────────
LIVED = "Lived in round."


class TestLogMonitorRemoval(unittest.TestCase):
    def setUp(self):
        _reset_list(self)
        SharedState.set_list_source(None)
        self.addCleanup(SharedState.set_list_source, None)
        self.table_before = ContinueTable.current()
        self.changed = []

    def _monitor(self, round_type="Classic", terror_ids=(7,), itype=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(
            WindowConfig(hwnd=0x30, osc_port=0), {}, lambda _m: None, window_idx=2,
            on_list_changed=lambda: self.changed.append(1))
        monitor._running = True
        monitor.st.instance_type = itype
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.st.terror_ids = list(terror_ids)
        monitor._start_daemon = lambda target, *a: target(*a)
        monitor.logs = []
        monitor._log = monitor.logs.append
        return monitor

    def _lines(self, monitor):
        return [m for m in monitor.logs if m.startswith("[続行リスト]")]

    def test_surviving_removes_the_terror_logs_it_and_tells_the_gui(self):
        ContinueList.set_current({CLASSIC: frozenset({7, 8}), FOG: frozenset({7})})
        monitor = self._monitor()
        monitor._process(LIVED)
        self.assertEqual(_sets(ContinueList.current()), {CLASSIC: {8}, FOG: {7}})
        self.assertEqual(self._lines(monitor),
                         [f"[続行リスト] 生存したので外しました: {LogMonitor.format_terror_ids([7])} / Classic"])
        self.assertEqual(self.changed, [1])
        self.assertTrue(monitor.st.lived_this_round)

    def test_a_second_lived_line_in_the_same_round_removes_nothing_more(self):
        ContinueList.set_current({CLASSIC: frozenset({7, 8})})
        monitor = self._monitor()
        monitor._process(LIVED)
        monitor._process(LIVED)
        self.assertEqual(len(self._lines(monitor)), 1)
        self.assertEqual(self.changed, [1])

    def test_several_terrors_are_removed_and_named_together(self):
        ContinueList.set_current({DOUBLE: frozenset({3, 5, 9})})
        monitor = self._monitor("Double Trouble", (3, 5))
        monitor._process(LIVED)
        self.assertEqual(_sets(ContinueList.current()), {DOUBLE: {9}})
        self.assertEqual(self._lines(monitor),
                         [f"[続行リスト] 生存したので外しました: {LogMonitor.format_terror_ids([3, 5])} / Double Trouble"])

    def test_a_moon_round_removes_from_the_special_moon_key(self):
        for round_type in MOON_ROUNDS:
            ContinueList.set_current({SPECIAL: frozenset({MOON_A, MOON_B}), CLASSIC: frozenset({MOON_A})})
            monitor = self._monitor(round_type, (flow_id(196),))
            monitor._process(LIVED)
            self.assertEqual(_sets(ContinueList.current()), {SPECIAL: {MOON_B}, CLASSIC: {MOON_A}}, round_type)
            self.assertEqual(len(self._lines(monitor)), 1, "ログは 1 行")
            self.assertIn("Mystic Moon", self._lines(monitor)[0], "名前で出す")

    def test_a_terror_that_is_not_listed_logs_nothing_and_tells_no_one(self):
        ContinueList.set_current({CLASSIC: frozenset({8})})
        monitor = self._monitor()
        monitor._process(LIVED)
        self.assertEqual(_sets(ContinueList.current()), {CLASSIC: {8}})
        self.assertEqual(self._lines(monitor), [])
        self.assertEqual(self.changed, [])

    def test_the_host_list_is_never_edited(self):
        ContinueList.set_current({CLASSIC: frozenset({7})})
        SharedState.set_list_source("host")
        monitor = self._monitor()
        monitor._process(LIVED)
        self.assertEqual(_sets(ContinueList.current()), {CLASSIC: {7}})
        self.assertEqual((self._lines(monitor), self.changed), ([], []))

    def test_after_going_back_to_the_own_list_it_is_edited_again(self):
        ContinueList.set_current({CLASSIC: frozenset({7})})
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        monitor._process(LIVED)
        self.assertEqual(ContinueList.current(), {})

    def test_group_instances_do_not_edit_the_list(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            ContinueList.set_current({CLASSIC: frozenset({7})})
            monitor = self._monitor(itype=itype)
            monitor._process(LIVED)
            self.assertEqual(_sets(ContinueList.current()), {CLASSIC: {7}}, itype)
            self.assertEqual((self._lines(monitor), self.changed), ([], []), itype)

    def test_unknown_terrors_do_not_edit_the_list(self):
        """Fog でテラーがまだ分からないまま生存した"""
        ContinueList.set_current({FOG: frozenset({7})})
        monitor = self._monitor("Fog", ())
        monitor._process(LIVED)
        self.assertEqual(_sets(ContinueList.current()), {FOG: {7}})
        self.assertEqual((self._lines(monitor), self.changed), ([], []))

    def test_the_overlay_tables_are_not_touched(self):
        store = ContinueTable.with_preset_body(self.table_before, ContinueTable.DEFAULT, "既定", {CLASSIC: [7]})
        ContinueTable.set_current(store)
        self.addCleanup(ContinueTable.set_current, self.table_before)
        ContinueList.set_current({CLASSIC: frozenset({7})})
        monitor = self._monitor()
        monitor._process(LIVED)
        self.assertEqual(ContinueList.current(), {})
        self.assertIs(ContinueTable.current(), store)
        self.assertEqual(ContinueTable.table_for(store, 2).default, {CLASSIC: frozenset({7})},
                         "デフォ続行に入っていれば、外した後も続行する")

    def test_without_a_callback_it_still_edits_and_does_not_fail(self):
        ContinueList.set_current({CLASSIC: frozenset({7})})
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=0x30, osc_port=0), {}, lambda _m: None, window_idx=1)
        monitor._running = True
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = "Classic"
        monitor.st.terror_ids = [7]
        monitor._log = lambda _m: None
        monitor._process(LIVED)
        self.assertEqual(ContinueList.current(), {})

    def test_the_ids_that_the_judgement_used_are_the_ones_removed(self):
        """st.terror_ids は判定に使う番号。それと同じテラーを外す（Alternate の 136 は独自の番号の 1002）"""
        ContinueList.set_current({"Alternate/オルタネイト": frozenset({own(136), own(140)})})
        monitor = self._monitor("Alternate", (flow_id(136),))
        monitor._process(LIVED)
        self.assertEqual(_sets(ContinueList.current()), {"Alternate/オルタネイト": {own(140)}})
        self.assertEqual(self._lines(monitor), ["[続行リスト] 生存したので外しました: Sanic / Alternate"])


# ── 続行表の窓（続行リスト）───────────────────────
class _ListWindowCase(unittest.TestCase):
    def setUp(self):
        _reset_list(self)
        ContinueTable.table_path().unlink(missing_ok=True)
        ContinueTable.set_current(ContinueTable.Store())
        self.addCleanup(ContinueTable.table_path().unlink, missing_ok=True)
        self.addCleanup(ContinueTable.set_current, ContinueTable.Store())
        patcher = patch.object(ContinueTableGUI, "SAVE_DELAY_MS", 30)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.changed, self.list_changed = [], []
        self.win = ContinueTableGUI.ContinueTableWindow(
            self.root, on_change=lambda: self.changed.append(1),
            on_list_change=lambda: self.list_changed.append(ContinueList.current()))
        self.win.update()

    def _style(self, tid):
        cell = self.win._cells[tid]
        canvas = self.win.canvas
        return canvas.itemcget(cell.frame, "outline"), float(canvas.itemcget(cell.frame, "width"))

    def _select_round(self, name):
        names = list(self.win.list_rounds.get(0, "end"))
        self.win.select_round(names.index(name))


class TestListInTheTableWindow(_ListWindowCase):
    def test_the_list_is_a_third_kind_with_a_blue_frame(self):
        self.assertEqual(ContinueTableGUI.WHICH_TEXT[LIST], "続行リスト")
        self.assertEqual(ContinueTableGUI.COLOR_LIST, "#4FA3FF")
        radios = [w.cget("text") for w in _widgets_of(self.win, ttk.Radiobutton)]
        self.assertEqual(radios, ["続行リスト", "続行無効", "デフォ続行"], "続行リストが先頭")
        self.assertEqual(self.win.v_which.get(), LIST, "最初に選ばれているのは続行リスト")
        self.assertEqual(self.win._which(), LIST)
        self.win.toggle(5)
        self.assertEqual(self._style(5), ("#4FA3FF", 2.0))
        self.assertEqual(self._style(6)[1], 1.0, "入っていないものは細い灰色の枠")

    def test_the_bottom_buttons_are_in_order_with_the_list_pair_first(self):
        win = ContinueTableGUI.ContinueTableWindow(
            self.root, on_import_tnl=lambda **k: None, on_export_tnl=lambda **k: None)
        self.addCleanup(win.destroy)
        bottom = next(b for b in _widgets_of(win, ttk.Button) if b.cget("text") == ".tnnl を続行無効へ読み込む").master
        self.assertEqual([b.cget("text") for b in bottom.pack_slaves()],
                         [".tnl を続行リストへ読み込む", "続行リストを .tnl に書き出す",
                          ".tnnl を続行無効へ読み込む", ".tnl をデフォ続行へ読み込む"])

    def test_the_list_buttons_call_the_callbacks_with_the_window_as_parent(self):
        calls = []
        win = ContinueTableGUI.ContinueTableWindow(
            self.root, on_import_tnl=lambda **k: calls.append(("import", k)),
            on_export_tnl=lambda **k: calls.append(("export", k)))
        self.addCleanup(win.destroy)
        _press(win, ".tnl を続行リストへ読み込む")
        _press(win, "続行リストを .tnl に書き出す")
        self.assertEqual(calls, [("import", {"parent": win}), ("export", {"parent": win})])

    def test_without_callbacks_the_list_buttons_are_not_made(self):
        texts = [b.cget("text") for b in _widgets_of(self.win, ttk.Button)]
        self.assertNotIn(".tnl を続行リストへ読み込む", texts)
        self.assertNotIn("続行リストを .tnl に書き出す", texts)
        self.assertIn(".tnnl を続行無効へ読み込む", texts, "ほかのボタンは残る")
        self.assertIn(".tnl をデフォ続行へ読み込む", texts)

    def test_the_preset_controls_are_hidden_for_the_list_and_back_for_the_others(self):
        widgets = [w for w, _pack in self.win._preset_widgets]
        self.assertEqual(len(widgets), 6, "ラベル・ドロップダウン・4 つのボタン")
        self.assertEqual([w.winfo_manager() for w in widgets], [""] * 6, "隠れている")
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.assertEqual([w.winfo_manager() for w in widgets], ["pack"] * 6)
        order = [w for w in self.win.cb_preset.master.pack_slaves() if w in widgets]
        self.assertEqual(order, widgets, "元の並びで戻る")
        self.win.v_which.set(LIST)
        self.assertEqual([w.winfo_manager() for w in widgets], [""] * 6)
        self.win.v_which.set(ContinueTable.DISABLED)
        self.assertEqual([w.winfo_manager() for w in widgets], ["pack"] * 6)
        self.assertEqual(self.win.v_preset.get(), "既定")

    def test_clicking_toggles_the_own_list_and_applies_at_once(self):
        self.win.toggle(5)
        self.assertEqual(_sets(ContinueList.current()), {CLASSIC: {5}})
        self.assertEqual(self.list_changed, [ContinueList.current()], "押したその場で全窓へ")
        self.win.toggle(5)
        self.assertEqual(ContinueList.current(), {})
        self.assertEqual(len(self.list_changed), 2)

    def test_clicking_does_not_touch_the_overlay_tables_or_their_file(self):
        before = ContinueTable.current()
        self.win.toggle(5)
        self.win._flush_save()
        self.assertIs(ContinueTable.current(), before)
        self.assertFalse(ContinueTable.table_path().exists(), "続行表のファイルは書かない")
        self.assertEqual(self.changed, [], "プリセットの件数の更新は要らない")

    def test_the_overlay_tables_do_not_touch_the_list(self):
        self.win.v_which.set(ContinueTable.DISABLED)
        self.win.toggle(5)
        self.assertEqual(ContinueList.current(), {})
        self.assertEqual(self.list_changed, [])

    def test_the_other_two_tables_still_show_their_thin_frames(self):
        store = ContinueTable.with_preset_body(ContinueTable.current(), ContinueTable.DISABLED, "既定", {CLASSIC: [1, 3]})
        store = ContinueTable.with_preset_body(store, ContinueTable.DEFAULT, "既定", {CLASSIC: [2, 3]})
        ContinueTable.set_current(store)
        self.win._restyle_all()
        self.assertEqual(self._style(1), (ContinueTableGUI.COLOR_DISABLED, 1.0))
        self.assertEqual(self._style(2), (ContinueTableGUI.COLOR_DEFAULT, 1.0))
        self.assertEqual(self._style(3), (ContinueTableGUI.COLOR_DEFAULT, 1.0), "両方なら重ねで勝つデフォ続行の色")
        self.win.toggle(1)
        self.assertEqual(self._style(1), ("#4FA3FF", 2.0), "続行リストに入れたらそちらが強い")

    def test_the_list_is_not_shown_while_editing_the_overlay_tables(self):
        ContinueList.set_current({CLASSIC: frozenset({5})})
        self.win.v_which.set(ContinueTable.DISABLED)
        self.win._restyle_all()
        self.assertEqual(self._style(5), (ContinueTableGUI.COLOR_IDLE, 1.0), "今までどおりの見た目")

    def test_select_all_and_clear_all_work_on_the_visible_cells(self):
        self.win.select_all()
        self.assertEqual(set(ContinueList.current()[CLASSIC]), set(self.win._cells))
        self.win.clear_all()
        self.assertEqual(ContinueList.current(), {})

    def test_select_all_respects_the_search(self):
        needle = ContinueTableGUI.terror_name(0, CLASSIC)
        self.win.v_search.set(needle)
        self.win.select_all()
        listed = ContinueList.current()[CLASSIC]
        self.assertIn(0, listed)
        self.assertEqual(set(listed), {t for t in self.win._cells if self.win._matches(t)})
        self.assertLess(len(listed), len(self.win._cells))

    def test_special_moon_writes_its_one_key_and_clears_it(self):
        self._select_round("Special/Moon")
        self.win.toggle(MOON_A)
        self.assertEqual(_sets(ContinueList.current()), {SPECIAL: {MOON_A}})
        self.win.toggle(MOON_A)
        self.assertEqual(ContinueList.current(), {})
        self.win.select_all()
        self.assertEqual(set(ContinueList.current()), {SPECIAL})
        self.assertEqual(set(ContinueList.current()[SPECIAL]), set(MatchTNL.slots().by_key[SPECIAL]))
        self.win.clear_all()
        self.assertEqual(ContinueList.current(), {})

    def test_special_moon_shows_a_terror_that_is_in_the_key(self):
        ContinueList.set_current({SPECIAL: frozenset({MOON_A})})
        self._select_round("Special/Moon")
        self.assertEqual(self._style(MOON_A), ("#4FA3FF", 2.0))
        self.win.toggle(MOON_A)
        self.assertEqual(ContinueList.current(), {})

    def test_saving_is_put_off_and_writes_only_the_list_file(self):
        with patch.object(ContinueList, "save_current", wraps=ContinueList.save_current) as save:
            for tid in (5, 6, 7):
                self.win.toggle(tid)
            self.assertEqual(save.call_count, 0, "押した時点では書かない")
            self.assertFalse(ContinueList.list_path().exists())
            end = time.time() + 3
            while save.call_count == 0 and time.time() < end:
                self.win.update()
                time.sleep(0.01)
            time.sleep(0.1)
            self.win.update()
            self.assertEqual(save.call_count, 1, "まとめて 1 回")
        self.assertEqual(_sets(ContinueList.load()[0]), {CLASSIC: {5, 6, 7}})
        self.assertFalse(ContinueTable.table_path().exists())

    def test_closing_the_window_writes_what_is_still_pending(self):
        self.win.toggle(5)
        self.assertFalse(ContinueList.list_path().exists())
        self.win.destroy()
        self.assertEqual(_sets(ContinueList.load()[0]), {CLASSIC: {5}})

    def test_refresh_list_redraws_after_a_change_from_outside(self):
        ContinueList.set_current({CLASSIC: frozenset({5})})
        self.win.refresh_list()
        self.assertEqual(self._style(5), ("#4FA3FF", 2.0))
        ContinueList.set_current({})
        self.win.refresh_list()
        self.assertEqual(self._style(5)[1], 1.0)

    def test_the_item_rules_and_imports_are_still_there(self):
        texts = [b.cget("text") for b in _widgets_of(self.win, ttk.Button)]
        for text in (".tnl をデフォ続行へ読み込む", ".tnnl を続行無効へ読み込む", "決まりを足す", "全選択", "全解除"):
            self.assertIn(text, texts)


def _widgets_of(widget, cls):
    found = [widget] if isinstance(widget, cls) else []
    for child in widget.winfo_children():
        found.extend(_widgets_of(child, cls))
    return found


# ── メイン画面 ────────────────────────────────
class _AppCase(unittest.TestCase):
    """本物の App（隠れた窓）。保存は一時フォルダ。.tnl は一時フォルダのもの"""

    def setUp(self):
        _reset_list(self)
        config.SETTINGS_PATH.unlink(missing_ok=True)
        self.addCleanup(config.SETTINGS_PATH.unlink, missing_ok=True)
        ContinueTable.table_path().unlink(missing_ok=True)
        ContinueTable.set_current(ContinueTable.Store())
        self.addCleanup(ContinueTable.table_path().unlink, missing_ok=True)
        SharedState.set_list_source(None)
        self.addCleanup(SharedState.set_list_source, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.logs = []

    def _tnl(self, name="a.tnl", data=None, list_name="リストA"):
        path = self.dir / name
        path.write_text(json.dumps({"format": "tnl_json", "version": 105, "list_name": list_name,
                                    "data": data if data is not None else {CLASSIC: {"3": 1, "4": 0, "7": 1}}}),
                        encoding="utf-8")
        return path

    def _app(self, settings=None):
        if settings is not None:
            mainGUI.save_settings(settings)
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            app = mainGUI.App()
        self.addCleanup(app.destroy)
        app._log = self.logs.append
        return app


class TestStartup(_AppCase):
    def test_first_run_builds_the_own_list_from_the_saved_tnl_and_saves_it(self):
        tnl = self._tnl()
        before = tnl.read_bytes()
        app = self._app({"tnl_path": str(tnl)})
        self.assertEqual(_sets(app.keepOn_set), {CLASSIC: {3, 7}})
        self.assertEqual(_sets(ContinueList.current()), {CLASSIC: {3, 7}})
        self.assertEqual(_sets(ContinueList.load()[0]), {CLASSIC: {3, 7}}, "保存した")
        self.assertEqual(app.lbl_tnl.cget("text"), "1ラウンド / 2件 続行対象")
        self.assertEqual(tnl.read_bytes(), before, ".tnl は書き換えない")

    def test_after_that_the_tnl_is_never_read_again(self):
        tnl = self._tnl()
        self._app({"tnl_path": str(tnl)})
        tnl.write_text(json.dumps({"list_name": "変えた", "data": {FOG: {"1": 1}}}), encoding="utf-8")
        with patch.object(MatchTNL, "load_tnl", side_effect=AssertionError("tnl を読んだ")):
            app = self._app()
        self.assertEqual(_sets(app.keepOn_set), {CLASSIC: {3, 7}})
        self.assertEqual(app.lbl_tnl.cget("text"), "1ラウンド / 2件 続行対象")

    def test_edits_survive_a_restart(self):
        app = self._app({"tnl_path": str(self._tnl())})
        ContinueList.update(lambda data: ContinueList.with_ids_removed(data, [CLASSIC], [3]))
        ContinueList.save_current()
        again = self._app()
        self.assertEqual(_sets(again.keepOn_set), {CLASSIC: {7}})

    def test_without_a_saved_tnl_it_starts_empty_and_does_not_save(self):
        app = self._app({})
        self.assertEqual(app.keepOn_set, {})
        self.assertFalse(ContinueList.list_path().exists(), "次の起動でまた作れるように、空は保存しない")
        self.assertEqual(app.lbl_tnl.cget("text"), "0ラウンド / 0件 続行対象")

    def test_a_missing_tnl_is_reported_and_leaves_the_list_empty(self):
        app = self._app({"tnl_path": str(self.dir / "gone.tnl")})
        self.assertEqual(app.keepOn_set, {})
        self.assertFalse(ContinueList.list_path().exists())

    def test_a_broken_tnl_is_reported_and_leaves_the_list_empty(self):
        bad = self.dir / "bad.tnl"
        bad.write_text("not json", encoding="utf-8")
        with patch.object(DebugLog, "exception"):
            app = self._app({"tnl_path": str(bad)})
        self.assertEqual(app.keepOn_set, {})
        self.assertFalse(ContinueList.list_path().exists())

    def test_a_broken_own_file_starts_empty_and_does_not_fall_back_to_the_tnl(self):
        ContinueList.list_path().parent.mkdir(parents=True, exist_ok=True)
        ContinueList.list_path().write_text("not json", encoding="utf-8")
        with patch.object(DebugLog, "write"):
            app = self._app({"tnl_path": str(self._tnl())})
        self.assertEqual(app.keepOn_set, {})

    def test_the_main_window_has_no_tnl_buttons_and_no_list_frame(self):
        app = self._app({})
        texts = [b.cget("text") for b in _widgets_of(app, ttk.Button)]
        self.assertNotIn(".tnl から読み込む", texts)
        self.assertNotIn(".tnl に書き出す", texts)
        self.assertNotIn("再読み込み", texts)
        self.assertNotIn("参照…", texts)
        frames = [w.cget("text") for w in _widgets_of(app, ttk.LabelFrame)]
        self.assertNotIn("① 続行リスト", frames)
        self.assertNotIn("① 続行リスト(.tnl)", frames)
        self.assertEqual(frames.count("続行表"), 0, "古い「続行表」の枠は残っていない")

    def test_the_continue_table_frame_is_the_first_section_and_holds_the_list_status(self):
        app = self._app({})
        first = next(w for w in app.pack_slaves() if isinstance(w, ttk.LabelFrame))
        self.assertEqual(first.cget("text"), "① 続行表", "最初の枠")
        self.assertIs(app.lbl_tnl.master, first, "リストの状態の文字は続行表の枠の中")
        row = first.pack_slaves()
        self.assertIsInstance(row[0], ttk.Button)
        self.assertEqual(row[0].cget("text"), "続行表")
        self.assertEqual(row[1:], [app.lbl_tnl], "続行表のボタン → リストの状態の文字（プリセットの件数は出さない）")
        self.assertFalse(hasattr(app, "lbl_ct_count"))


class TestImportAndExport(_AppCase):
    def setUp(self):
        super().setUp()
        self.app = self._app({"tnl_path": str(self._tnl())})
        self.asked = []

    def _import(self, path, answer=True):
        with patch.object(mainGUI.filedialog, "askopenfilename", return_value=str(path)) as ask, \
             patch.object(mainGUI.messagebox, "askyesno",
                          side_effect=lambda *a, **k: (self.asked.append(a), answer)[1]) as yes, \
             patch.object(mainGUI.messagebox, "showerror") as error:
            self.app._import_tnl()
        return ask, yes, error

    def test_import_replaces_the_own_list_after_confirming(self):
        other = self._tnl("b.tnl", {FOG: {"150": 1}}, "リストB")
        before = other.read_bytes()
        _ask, yes, error = self._import(other)
        self.assertEqual(yes.call_count, 1, "確認を出す")
        self.assertIn("置き換え", self.asked[0][1])
        self.assertEqual(error.call_count, 0)
        self.assertEqual(_sets(self.app.keepOn_set), {FOG: {own(150)}}, "ListTool の 150 は独自の番号に直る")
        self.assertEqual(_sets(ContinueList.load()[0]), {FOG: {own(150)}}, "保存した")
        self.assertEqual(self.app.lbl_tnl.cget("text"), "1ラウンド / 1件 続行対象")
        self.assertEqual(mainGUI.load_settings().get("tnl_path"), str(other), "選んだ .tnl を覚える")
        self.assertEqual(other.read_bytes(), before, ".tnl は書き換えない")

    def test_the_status_and_log_do_not_show_the_list_name_but_the_meta_keeps_it(self):
        other = self._tnl("b.tnl", {FOG: {"150": 1}}, "リストB")
        self._import(other)
        self.assertEqual(self.app.lbl_tnl.cget("text"), "1ラウンド / 1件 続行対象")
        self.assertIn("[続行リスト] 1ラウンド / 1件 続行対象", self.logs)
        self.assertNotIn("リストB", self.app.lbl_tnl.cget("text"))
        self.assertEqual(ContinueList.current_meta().get("list_name"), "リストB", "名前は覚えておく（書き出しに使う）")

    def test_import_passes_the_parent_to_every_dialog(self):
        other = self._tnl("b.tnl", {FOG: {"150": 1}}, "リストB")
        win = object()
        with patch.object(mainGUI.filedialog, "askopenfilename", return_value=str(other)) as ask, \
             patch.object(mainGUI.messagebox, "askyesno", return_value=True) as yes:
            self.app._import_tnl(parent=win)
        self.assertIs(ask.call_args.kwargs.get("parent"), win)
        self.assertIs(yes.call_args.kwargs.get("parent"), win)
        self.assertEqual(_sets(self.app.keepOn_set), {FOG: {own(150)}}, "中身は今まで通り置き換わる")

    def test_import_error_dialog_gets_the_parent(self):
        bad = self.dir / "bad.tnl"
        bad.write_text("not json", encoding="utf-8")
        win = object()
        with patch.object(DebugLog, "exception"), \
             patch.object(mainGUI.filedialog, "askopenfilename", return_value=str(bad)), \
             patch.object(mainGUI.messagebox, "askyesno", return_value=True), \
             patch.object(mainGUI.messagebox, "showerror") as error:
            self.app._import_tnl(parent=win)
        self.assertIs(error.call_args.kwargs.get("parent"), win)

    def test_without_a_parent_the_dialogs_get_none(self):
        with patch.object(mainGUI.filedialog, "askopenfilename", return_value="") as ask:
            self.app._import_tnl()
        self.assertIsNone(ask.call_args.kwargs.get("parent"), "今まで通り（親を渡さない）")
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value="") as save:
            self.app._export_tnl()
        self.assertIsNone(save.call_args.kwargs.get("parent"))

    def test_export_passes_the_parent_to_the_dialogs(self):
        win = object()
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value=str(self.dir / "p.tnl")) as ask:
            self.app._export_tnl(parent=win)
        self.assertIs(ask.call_args.kwargs.get("parent"), win)
        self.assertNotIn("initialdir", ask.call_args.kwargs)
        bad = self.dir / "dir2.tnl"
        bad.mkdir()
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value=str(bad)), \
             patch.object(mainGUI.messagebox, "showerror") as error:
            self.app._export_tnl(parent=win)
        self.assertIs(error.call_args.kwargs.get("parent"), win)

    def test_the_table_window_buttons_run_the_app_functions_over_the_window(self):
        self.app._open_continue_table()
        window = self.app._continue_table_window
        self.addCleanup(window.destroy)
        other = self._tnl("c.tnl", {FOG: {"150": 1}}, "リストC")
        with patch.object(mainGUI.filedialog, "askopenfilename", return_value=str(other)) as ask, \
             patch.object(mainGUI.messagebox, "askyesno", return_value=True):
            _press(window, ".tnl を続行リストへ読み込む")
        self.assertIs(ask.call_args.kwargs.get("parent"), window, "ダイアログは続行表の窓の上")
        self.assertEqual(_sets(self.app.keepOn_set), {FOG: {own(150)}})
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value="") as save:
            _press(window, "続行リストを .tnl に書き出す")
        self.assertIs(save.call_args.kwargs.get("parent"), window)

    def test_declining_changes_nothing(self):
        other = self._tnl("b.tnl", {FOG: {"150": 1}})
        self._import(other, answer=False)
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {3, 7}})
        self.assertEqual(_sets(ContinueList.load()[0]), {CLASSIC: {3, 7}})

    def test_cancelling_the_file_dialog_asks_nothing(self):
        _ask, yes, error = self._import("")
        self.assertEqual((yes.call_count, error.call_count), (0, 0))
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {3, 7}})

    def test_a_broken_tnl_is_reported_and_changes_nothing(self):
        bad = self.dir / "bad.tnl"
        bad.write_text("not json", encoding="utf-8")
        with patch.object(DebugLog, "exception"):
            _ask, _yes, error = self._import(bad)
        self.assertEqual(error.call_count, 1)
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {3, 7}})
        self.assertEqual(mainGUI.load_settings().get("tnl_path"), str(self.dir / "a.tnl"))

    def test_export_writes_a_tnl_that_reads_back_the_same(self):
        out = self.dir / "mine.tnl"
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value=str(out)) as ask, \
             patch.object(mainGUI.messagebox, "showerror") as error:
            self.app._export_tnl()
        self.assertEqual(error.call_count, 0)
        self.assertEqual(MatchTNL.load_tnl(str(out))[0], {CLASSIC: {3, 7}})
        self.assertEqual(MatchTNL.load_tnl(str(out))[1]["list_name"], "リストA")
        self.assertNotIn("initialdir", ask.call_args.kwargs, "ListTool のフォルダを既定にしない")
        self.assertTrue(any("書き出しました" in m for m in self.logs))

    def test_export_never_defaults_to_the_listtool_folder(self):
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value="") as ask:
            self.app._export_tnl()
        for value in list(ask.call_args.kwargs.values()):
            self.assertNotIn("ToN ListTool", str(value))
            self.assertNotIn("ToN_ListTool", str(value))

    def test_cancelling_the_save_dialog_writes_nothing(self):
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value=""), \
             patch.object(ContinueList, "write_tnl") as write:
            self.app._export_tnl()
        self.assertEqual(write.call_count, 0)

    def test_a_failed_export_is_reported(self):
        bad = self.dir / "dir.tnl"
        bad.mkdir()
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value=str(bad)), \
             patch.object(mainGUI.messagebox, "showerror") as error:
            self.app._export_tnl()
        self.assertEqual(error.call_count, 1)

    def test_exporting_does_not_change_the_own_list(self):
        before = ContinueList.list_path().read_bytes()
        with patch.object(mainGUI.filedialog, "asksaveasfilename", return_value=str(self.dir / "o.tnl")):
            self.app._export_tnl()
        self.assertEqual(ContinueList.list_path().read_bytes(), before)


class TestWholeFlow(_AppCase):
    def setUp(self):
        super().setUp()
        self.app = self._app({"tnl_path": str(self._tnl(data={CLASSIC: {"3": 1, "7": 1}}))})

    def _monitor(self, round_type="Classic", ids=(7,)):
        monitor = LogMonitor.LogMonitor(
            WindowConfig(hwnd=0x30, osc_port=0), None, lambda _m: None, window_idx=1,
            lists=self.app.lists, on_list_changed=self.app._on_own_list_changed)
        monitor._running = True
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = round_type
        monitor.st.terror_ids = list(ids)
        monitor._start_daemon = lambda target, *a: target(*a)
        monitor.logs = []
        monitor._log = monitor.logs.append
        return monitor

    def test_surviving_removes_it_from_every_window_saves_and_updates_the_count(self):
        first, second = self._monitor(), self._monitor()
        before = self.app.keepOn_set
        first._process(LIVED)
        self.app.update()                                       # 画面のスレッドでの反映
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {3}})
        self.assertIsNot(self.app.keepOn_set, before, "新しい dict を代入する")
        self.assertIs(second.keepOn_set, self.app.keepOn_set, "全窓が同じものを見る")
        self.assertEqual(_sets(ContinueList.load()[0]), {CLASSIC: {3}}, "保存した")
        self.assertEqual(self.app.lbl_tnl.cget("text"), "1ラウンド / 1件 続行対象")
        self.assertFalse(RoundDecision.list_continues(second.keepOn_set, [7], "Classic"))
        self.assertTrue(RoundDecision.list_continues(second.keepOn_set, [3], "Classic"))

    def test_while_the_host_list_is_active_the_base_list_stays_the_host_list(self):
        SharedState.set_list_source("host")
        self.app._apply_keep_on({CLASSIC: {99}})
        self.app._apply_own_list()
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {99}})
        self.app._finish_own_list_change()
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {99}})

    def test_going_back_from_the_host_list_uses_the_own_list(self):
        SharedState.set_list_source("host")
        self.app._apply_keep_on({CLASSIC: {99}})
        self.app._fall_back_to_tnl("テスト")
        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {3, 7}})

    def test_edits_made_while_the_host_list_is_active_show_after_going_back(self):
        SharedState.set_list_source("host")
        self.app._apply_keep_on({CLASSIC: {99}})
        self.app._open_continue_table()
        self.app._continue_table_window.v_which.set(LIST)
        self.app._continue_table_window.toggle(3)               # 3 を外す
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {99}}, "主催リストのまま")
        self.app._fall_back_to_tnl("テスト")
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {7}})

    def test_the_table_window_edit_reaches_every_window_at_once(self):
        monitor = self._monitor()
        self.app._open_continue_table()
        window = self.app._continue_table_window
        window.v_which.set(LIST)
        before = self.app.keepOn_set
        window.toggle(8)
        self.assertEqual(_sets(self.app.keepOn_set), {CLASSIC: {3, 7, 8}})
        self.assertIsNot(self.app.keepOn_set, before)
        self.assertIs(monitor.keepOn_set, self.app.keepOn_set)
        self.assertEqual(self.app.lbl_tnl.cget("text"), "1ラウンド / 3件 続行対象")
        window._flush_save()
        self.assertEqual(_sets(ContinueList.load()[0]), {CLASSIC: {3, 7, 8}})
        window.destroy()

    def test_an_open_table_window_is_redrawn_when_a_survive_removes_a_terror(self):
        self.app._open_continue_table()
        window = self.app._continue_table_window
        window.v_which.set(LIST)
        window._restyle_all()
        cell = window._cells[7]
        self.assertEqual(window.canvas.itemcget(cell.frame, "outline"), "#4FA3FF")
        self._monitor()._process(LIVED)
        self.app.update()
        self.assertNotEqual(window.canvas.itemcget(cell.frame, "outline"), "#4FA3FF")
        window.destroy()
