"""GI: 続行表の画面（ContinueTableWindow とメイン画面のボタン・チェック・件数）。
窓は作った直後に隠れる（support）。本物の入力は使わず、ファイルを選ぶ・確認の画面は偽物にする。
保存は一時フォルダ（config.SETTINGS_PATH の差し替え）だけ。隠れた窓にはマウスの合成イベントが届かないので、
押す・乗せるは受け口（_on_click・_show_tip）を直接呼ぶ"""
from tests.support import *  # noqa: F401,F403
import cv2
import numpy as np
import ContinueTable
import ContinueTableGUI
from ContinueTable import Table

KEY = "Classic/クラシック"
ALT = "Alternate/オルタネイト"
UNBOUND = "Unbound/アンバウンド"
EIGHT = "8 Pages/8ページ"


def _fresh_store(case):
    """今の表を空に・保存先（テスト用の一時フォルダ）の続行表のファイルを消す。終わったら同じに戻す"""
    ContinueTable.table_path().unlink(missing_ok=True)
    ContinueTable.set_current(Table())
    case.addCleanup(ContinueTable.table_path().unlink, missing_ok=True)
    case.addCleanup(ContinueTable.set_current, Table())


def _slot_ids(key):
    return dict(ContinueTableGUI.load_rounds())[key]


def _flat(rows):
    return [i for row in rows for i in row]


class TestLayout(unittest.TestCase):
    """layout_for: 画面を作らずに、本体の格子とバリアントの段の並びを確かめる"""

    def _layout(self, key):
        return ContinueTableGUI.layout_for(key, _slot_ids(key))

    def test_alternate_is_an_inverted_pyramid(self):
        rows, variant = self._layout(ALT)
        self.assertEqual([len(r) for r in rows], [11, 9, 7, 5, 3, 1])
        self.assertEqual(_flat(rows), list(range(134, 170)))
        self.assertEqual(variant, [])

    def test_classic_has_twelve_per_row_and_the_bloodthirsty_three_in_the_variant_row(self):
        rows, variant = self._layout(KEY)
        self.assertEqual(_flat(rows), list(range(134)), "192 は本体に無い")
        self.assertTrue(all(len(r) == 12 for r in rows[:-1]))
        self.assertEqual(len(rows[-1]), 134 % 12)
        self.assertEqual(variant, [190, 191, 192])

    def test_every_round_with_192_puts_the_three_in_the_variant_row(self):
        for name in ("Ghost", "Punished", "Bloodbath", "Double Trouble", "Bloodbath EX",
                     "Sabotage star", "Sabotage murder"):
            key = next(k for k, _ in ContinueTableGUI.load_rounds() if k.startswith(name + "/"))
            rows, variant = self._layout(key)
            self.assertEqual(variant, [190, 191, 192], name)
            self.assertNotIn(192, _flat(rows), name)

    def test_rounds_without_192_have_no_variant_row(self):
        for name in ("Classic.exe", "Randomizer", "Cracked", "Alternate"):
            key = next(k for k, _ in ContinueTableGUI.load_rounds() if k.startswith(name + "/"))
            self.assertEqual(self._layout(key)[1], [], name)

    def test_fog_and_midnight_add_neo_pilot_and_foxy(self):
        for name in ("Fog", "Midnight"):
            key = next(k for k, _ in ContinueTableGUI.load_rounds() if k.startswith(name + "/"))
            rows, variant = self._layout(key)
            self.assertEqual(variant, [190, 191, 192, 315, 316], name)
            self.assertFalse({190, 191, 192, 315, 316} & set(_flat(rows)), name)

    def test_unbound_variant_row_is_only_192(self):
        rows, variant = self._layout(UNBOUND)
        self.assertEqual(variant, [192])
        self.assertEqual(_flat(rows), list(range(200, 284)))

    def test_the_moons_keep_all_thirteen_in_the_main_grid(self):
        for key in ("Mystic Moon/ミスティックムーン", "Blood Moon/ブラッドムーン"):
            rows, variant = self._layout(key)
            self.assertEqual(variant, [])
            self.assertEqual(_flat(rows), [190, 191, 192, 196, 197, 198, 199, 312, 313, 314, 315, 316, 317])
            self.assertEqual([len(r) for r in rows], [12, 1])

    def test_eight_pages_shows_only_ids_that_eight_pages_has(self):
        rows, variant = self._layout(EIGHT)
        shown = set(_flat(rows))
        allowed = ContinueTableGUI.eight_pages_ids()
        self.assertEqual(variant, [])
        self.assertTrue(shown <= allowed)
        self.assertEqual(shown, allowed & set(_slot_ids(EIGHT)))
        self.assertLess(len(shown), len(_slot_ids(EIGHT)), "出ないテラーは絞られる")
        self.assertTrue({170, 171, 172, 173, 174, 193, 194} <= shown)
        self.assertFalse({3, 5, 6, 7, 10} & shown, "list_id に無い番号")
        self.assertEqual(_flat(ContinueTableGUI.layout_for(EIGHT, [0, 3, 177])[0]), [0, 177])

    def test_eight_pages_ids_come_from_the_list_ids_of_terrors_json(self):
        raw = json.loads((REPO_ROOT / "assets" / "data" / "terrors.json").read_text(encoding="utf-8"))
        self.assertEqual(ContinueTableGUI.eight_pages_ids(),
                         frozenset(e["list_id"] for e in raw["8pages"]))

    def test_there_is_no_special_round_in_the_screen(self):
        keys = [k for k, _ in ContinueTableGUI.load_rounds()]
        self.assertNotIn("Special/Moon", keys)
        self.assertEqual(len(keys), 18)
        self.assertEqual(len(set(ContinueTableGUI.round_display_name(k) for k in keys)), 18)

    def test_a_round_without_ids_makes_empty_rows(self):
        self.assertEqual(ContinueTableGUI.layout_for(KEY, []), ([], []))


class TestIconLook(unittest.TestCase):
    def test_brightness_levels_are_35_60_and_100_percent(self):
        self.assertEqual(ContinueTableGUI.LEVELS, {"dim": 0.35, "other": 0.6, "full": 1.0})

    def test_the_png_is_darker_by_the_factor_and_keeps_its_size(self):
        decode = lambda b: cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_UNCHANGED)
        full = decode(ContinueTableGUI.icon_png(0))
        for factor in (0.35, 0.6):
            dim = decode(ContinueTableGUI.icon_png(0, factor))
            self.assertEqual(dim.shape, full.shape)
            expected = (full[..., :3].astype(float) * factor).round()
            self.assertLessEqual(np.abs(dim[..., :3].astype(float) - expected).max(), 1)
            if full.shape[2] == 4:
                self.assertTrue((dim[..., 3] == full[..., 3]).all(), "透明はそのまま")
        self.assertIsNone(ContinueTableGUI.icon_png(317, 0.35))

    def test_scale_color(self):
        self.assertEqual(ContinueTableGUI.scale_color("#ffffff", 0.35), "#595959")
        self.assertEqual(ContinueTableGUI.scale_color("#102030", 1.0), "#102030")


def _wait(win, condition, seconds=3.0):
    """条件が整うまで窓のイベント（after）を回す"""
    end = time.time() + seconds
    while not condition() and time.time() < end:
        win.update()
        time.sleep(0.01)
    return condition()


class _WindowCase(unittest.TestCase):
    def setUp(self):
        _fresh_store(self)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.changed = []
        self.win = ContinueTableGUI.ContinueTableWindow(self.root, on_change=lambda: self.changed.append(1))
        self.win.update()

    def _style(self, tid):
        cell = self.win._cells[tid]
        canvas = self.win.canvas
        return (canvas.itemcget(cell.frame, "outline"), float(canvas.itemcget(cell.frame, "width")),
                canvas.itemcget(cell.face, "image"))

    def _photo_name(self, tid, level):
        return str(self.win._photo(tid, level))

    def _saved(self) -> Table:
        return ContinueTable.load()

    def _select_round(self, name):
        names = list(self.win.combo_round.cget("values"))
        self.win.combo_round.current(names.index(name))
        self.win._show_round()


class TestWindow(_WindowCase):
    def test_it_opens_and_closes_without_being_shown(self):
        self.assertIn(self.win, _HIDDEN_WINDOWS)
        self.assertFalse(self.win.winfo_viewable())
        self.win.destroy()
        self.assertFalse(self.win.winfo_exists())

    def test_the_round_is_chosen_from_a_small_readonly_dropdown(self):
        combo = self.win.combo_round
        self.assertEqual(str(combo.cget("state")), "readonly")
        values = list(combo.cget("values"))
        self.assertEqual(len(values), 18)
        self.assertEqual(values[0], "Classic")
        self.assertNotIn("Special", values)
        self.assertEqual(self.win.v_round.get(), "Classic")
        self.assertFalse(hasattr(self.win, "list_rounds"), "左のラウンドの一覧は無い")

    def test_the_first_round_has_one_cell_per_terror_including_the_variant_row(self):
        self.assertEqual(sorted(self.win._cells), list(range(134)) + [190, 191, 192])
        self.assertIsNotNone(self.win._photo(0, "full"), "アイコンがある番号は絵")
        self.assertIsNone(self.win._photo(317, "full"), "絵が無い番号は名前の札")

    def test_the_grid_is_centered_in_rows_and_the_variant_row_is_left_aligned_below(self):
        def left(tid):
            return self.win.canvas.coords(self.win._cells[tid].frame)[0]

        def top(tid):
            return self.win.canvas.coords(self.win._cells[tid].frame)[1]
        cell = ContinueTableGUI.CELL
        self.assertEqual(left(1) - left(0), cell)
        self.assertEqual(top(12) - top(0), cell, "13 個目は次の段")
        self.assertEqual(left(132) - left(0), (12 - 2) * cell // 2, "最後の段（2 個）は真ん中寄せ")
        self.assertEqual(left(190), left(0), "バリアントの段は左寄せ")
        self.assertEqual(top(190) - top(132), 2 * cell, "本体の最後の段から 1 段あけた下")

    def test_alternate_rows_are_an_inverted_pyramid_on_screen(self):
        self._select_round("Alternate")
        self.assertEqual(sorted(self.win._cells), list(range(134, 170)))
        cell = ContinueTableGUI.CELL
        tops = {}
        for tid, c in self.win._cells.items():
            tops.setdefault(self.win.canvas.coords(c.frame)[1], []).append(tid)
        self.assertEqual([len(v) for _, v in sorted(tops.items())], [11, 9, 7, 5, 3, 1])
        first, last = sorted(tops.items())[0][1], sorted(tops.items())[-1][1]
        centre = lambda tid: self.win.canvas.coords(self.win._cells[tid].frame)[0] + 21.5
        self.assertAlmostEqual(centre(last[0]), (centre(first[0]) + centre(first[-1])) / 2, delta=1)
        self.assertEqual(self.win._content_height, ContinueTableGUI.TOP_PAD * 2 + 6 * cell)

    def test_switching_the_round_changes_the_grid(self):
        self._select_round("Unbound")
        self.assertEqual(sorted(self.win._cells), [192] + list(range(200, 284)))
        self._select_round("Mystic Moon")
        self.assertEqual(sorted(self.win._cells), [190, 191, 192, 196, 197, 198, 199, 312, 313, 314, 315, 316, 317])
        self._select_round("8 Pages")
        self.assertEqual(set(self.win._cells), ContinueTableGUI.eight_pages_ids() & set(_slot_ids(EIGHT)))

    def test_the_tooltip_shows_the_name_from_icons_json_and_unbound_192_is_the_pack(self):
        def shown(tid):
            self.win._show_tip(tid)
            return self.win.canvas.itemcget(self.win._tip[0], "text")
        self.assertEqual(shown(0), "Huggy")
        self.assertEqual(shown(190), "Hungry Home Invader")
        self.assertEqual(shown(192), "Wild Yet Bloodthirsty Creature")
        self._select_round("Unbound")
        self.assertEqual(shown(192), "Pack of Wild Yet Bloodthirsty Creatures")
        self._select_round("8 Pages")
        self.assertEqual(shown(193), "The Navigator")
        self._select_round("Mystic Moon")
        self.assertEqual(shown(312), "Riftmonster")
        self.assertEqual(shown(317), "Glorbo", "絵が無い番号の札にも名前")
        self.win._hide_tip()
        self.assertEqual(self.win.canvas.find_withtag("tip"), ())

    def test_a_cell_with_no_icon_is_a_name_tile_and_dims_with_its_state(self):
        self._select_round("Mystic Moon")
        cell = self.win._cells[317]
        self.assertIsNotNone(cell.label)
        canvas = self.win.canvas
        dim_fill = canvas.itemcget(cell.face, "fill")
        self.win.toggle(317)
        self.assertNotEqual(canvas.itemcget(cell.face, "fill"), dim_fill)
        self.assertEqual(canvas.itemcget(cell.face, "fill"), ContinueTableGUI.tile_color(317))
        self.assertEqual(canvas.itemcget(cell.label, "text"), "Glorbo")

    def test_clicking_a_cell_puts_it_in_the_table(self):
        with patch.object(self.win, "_current_tid", return_value=5):
            self.win._on_click()                              # マウスの下のセルを押した
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})}, "判定にはその場で効く")
        self.assertEqual(len(self.changed), 1, "メイン画面の件数を更新する")
        self.assertEqual(self._style(5)[0], ContinueTableGUI.COLOR_DISABLED)

    def test_the_item_under_the_mouse_is_found_by_its_tag(self):
        cell = self.win._cells[7]
        for item in (cell.face, cell.frame):
            self.assertEqual(self.win._tid_of(item), 7)
        self.assertIsNone(self.win._tid_of(self.win.canvas.create_rectangle(0, 0, 1, 1)))

    def test_clicking_again_takes_it_out(self):
        self.win.toggle(5)
        self.win.toggle(5)
        self.assertEqual(ContinueTable.current().disabled, {})
        self.assertEqual(self._style(5)[:2], (ContinueTableGUI.COLOR_IDLE, 1.0))

    def test_a_variant_cell_goes_into_the_rounds_key(self):
        self.win.toggle(190)
        self.win.toggle(192)
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({190, 192})})
        self._select_round("Unbound")
        self.win.toggle(192)
        self.assertEqual(ContinueTable.current().disabled[UNBOUND], frozenset({192}))

    def test_a_click_changes_only_that_cell_and_does_not_rebuild_the_grid(self):
        canvas = self.win.canvas
        before_items = canvas.find_all()
        before = {tid: self._style(tid) for tid in self.win._cells}
        configured = []
        real = canvas.itemconfigure
        canvas.itemconfigure = lambda item, *a, **kw: (configured.append(item), real(item, *a, **kw))[1]
        with patch.object(self.win, "_show_round") as show, patch.object(self.win, "_draw_cell") as draw, \
                patch.object(canvas, "delete") as delete:
            self.win.toggle(5)
        self.assertEqual(show.call_count + draw.call_count + delete.call_count, 0, "作り直さない")
        cell = self.win._cells[5]
        self.assertEqual(set(configured), {cell.face, cell.frame}, "そのセルの item だけ")
        self.assertEqual(canvas.find_all(), before_items, "item は増えも減りもしない")
        after = {tid: self._style(tid) for tid in self.win._cells}
        self.assertEqual([t for t in after if after[t] != before[t]], [5])

    def test_three_brightness_variants_are_cached_per_icon(self):
        photos = {lv: self.win._photo(5, lv) for lv in ContinueTableGUI.LEVELS}
        self.assertEqual(len({str(p) for p in photos.values()}), 3, "3 種は別の絵")
        for level, photo in photos.items():
            self.assertIs(self.win._photo(5, level), photo, "作り直さず持っておく")

    def test_the_frame_color_and_width_and_brightness_for_each_state(self):
        dis, dfl = ContinueTableGUI.COLOR_DISABLED, ContinueTableGUI.COLOR_DEFAULT
        self.assertEqual(self._style(5), (ContinueTableGUI.COLOR_IDLE, 1.0, self._photo_name(5, "dim")))
        self.win.toggle(5)                                           # 続行無効に入れる
        self.assertEqual(self._style(5), (dis, 3.0, self._photo_name(5, "full")))
        self.win.v_which.set(ContinueTable.DEFAULT)                   # デフォ続行の編集へ
        self.win._restyle_all()
        self.assertEqual(self._style(5), (dis, 1.0, self._photo_name(5, "other")), "もう片方: 60%＋細い枠")
        self.assertEqual(self._style(6), (ContinueTableGUI.COLOR_IDLE, 1.0, self._photo_name(6, "dim")))
        self.win.toggle(6)
        self.assertEqual(self._style(6), (dfl, 3.0, self._photo_name(6, "full")))
        self.win.v_which.set(ContinueTable.DISABLED)
        self.win._restyle_all()
        self.assertEqual(self._style(5), (dis, 3.0, self._photo_name(5, "full")))
        self.assertEqual(self._style(6), (dfl, 1.0, self._photo_name(6, "other")))

    def test_the_default_table_is_green_and_the_other_table_is_not_changed(self):
        self.win.toggle(5)
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.toggle(5)
        self.win.toggle(6)
        self.assertEqual(ContinueTable.current().default, {KEY: frozenset({5, 6})})
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})}, "もう片方は変えない")

    def test_each_round_has_its_own_entries(self):
        self.win.toggle(5)
        self._select_round("Alternate")
        self.win.toggle(150)
        self.assertEqual(ContinueTable.current().disabled,
                         {KEY: frozenset({5}), ALT: frozenset({150})})
        self._select_round("Classic")
        self.assertEqual(self._style(5)[1], 3.0, "戻ると選んだ状態で描かれる")

    def test_the_name_search_keeps_the_grid_and_lights_up_the_matches(self):
        items = self.win.canvas.find_all()
        self.win.toggle(1)
        self.win.v_search.set("huggy")
        self.win.update()
        self.assertEqual(self.win.canvas.find_all(), items, "格子は作り直さない")
        self.assertEqual(self._style(0), (ContinueTableGUI.COLOR_MATCH, 3.0, self._photo_name(0, "full")))
        self.assertEqual(self._style(1), (ContinueTableGUI.COLOR_FAINT, 1.0, self._photo_name(1, "dim")),
                         "合わないものは暗いまま枠も薄く")
        self.win.v_search.set("")
        self.assertEqual(self._style(0)[:2], (ContinueTableGUI.COLOR_IDLE, 1.0))
        self.assertEqual(self._style(1)[:2], (ContinueTableGUI.COLOR_DISABLED, 3.0))

    def test_the_search_uses_the_names_of_the_round(self):
        self._select_round("Unbound")
        self.win.v_search.set("pack of wild yet bloodthirsty")
        self.assertEqual(self._style(192)[0], ContinueTableGUI.COLOR_MATCH)

    def test_clearing_the_round_takes_everything_out_of_the_current_table_only(self):
        self.win.toggle(5)
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.toggle(6)
        self.win.toggle(7)
        self.win._clear_round()
        self.assertEqual(ContinueTable.current().default, {})
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})})

    def test_clearing_the_round_also_clears_the_variant_row(self):
        for tid in (5, 190, 191, 192):
            self.win.toggle(tid)
        self.win._clear_round()
        self.assertEqual(ContinueTable.current().disabled, {})
        self.assertEqual(self._style(192)[:2], (ContinueTableGUI.COLOR_IDLE, 1.0))

    def test_the_notice_about_group_instances_is_shown(self):
        self.assertIn("干し芋・焼き芋", ContinueTableGUI.NOTE)
        texts = []
        stack = [self.win]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            try:
                texts.append(w.cget("text"))
            except tk.TclError:
                pass
        self.assertIn(ContinueTableGUI.NOTE, texts)


class TestSaving(_WindowCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(ContinueTableGUI, "SAVE_DELAY_MS", 30)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_saving_is_put_off_and_done_once_after_the_changes_stop(self):
        with patch.object(ContinueTable, "save", wraps=ContinueTable.save) as save:
            for tid in (5, 6, 7):
                self.win.toggle(tid)
            self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5, 6, 7})})
            self.assertEqual(save.call_count, 0, "押した時点では書かない")
            self.assertFalse(ContinueTable.table_path().exists())
            self.assertTrue(_wait(self.win, lambda: save.call_count > 0))
            self.win.update()
            time.sleep(0.1)
            self.win.update()
            self.assertEqual(save.call_count, 1, "まとめて 1 回")
        self.assertEqual(self._saved().disabled, {KEY: frozenset({5, 6, 7})})

    def test_each_click_pushes_the_save_back(self):
        self.win.toggle(5)
        first = self.win._save_job
        self.win.toggle(6)
        self.assertNotEqual(self.win._save_job, first, "予約を取り直す")

    def test_saving_nothing_pending_writes_nothing(self):
        with patch.object(ContinueTable, "save") as save:
            self.win._flush_save()
        self.assertEqual(save.call_count, 0)

    def test_closing_the_window_writes_what_is_still_pending(self):
        self.win.toggle(5)
        self.assertFalse(ContinueTable.table_path().exists())
        self.win.destroy()
        self.assertEqual(self._saved().disabled, {KEY: frozenset({5})})
        self.assertIsNone(self.win._save_job)

    def test_closing_with_nothing_pending_does_not_save(self):
        with patch.object(ContinueTable, "save") as save:
            self.win.destroy()
        self.assertEqual(save.call_count, 0)

    def test_clearing_the_round_is_saved_too(self):
        self.win.toggle(5)
        self.win._flush_save()
        self.win._clear_round()
        self.win._flush_save()
        self.assertEqual(self._saved().disabled, {})


class TestImport(_WindowCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.asked = []
        self.errors = []
        self.answer = True
        self.path = ""
        self.win._ask_file = lambda title, pattern: (self.asked.append(pattern), self.path)[1]
        self.win._confirm = lambda text: (self.asked.append(text), self.answer)[1]
        self.win._error = self.errors.append

    def _file(self, name, data):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data), encoding="utf-8")
        self.path = str(path)

    def test_tnl_replaces_the_default_table(self):
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.toggle(1)
        self._file("a.tnl", {"data": {KEY: {"5": 1, "6": 1}}})
        self.win._import_tnl()
        self.assertEqual(ContinueTable.current().default, {KEY: frozenset({5, 6})}, "置き換え")
        self.win._flush_save()
        self.assertEqual(self._saved().default, {KEY: frozenset({5, 6})})
        self.assertIn("*.tnl", self.asked)
        self.assertTrue(any("置き換え" in str(a) for a in self.asked), "確認を出す")
        self.assertEqual(self.changed, [1, 1])

    def test_tnnl_replaces_the_disabled_table_even_when_the_default_is_shown(self):
        self.win.v_which.set(ContinueTable.DEFAULT)
        self._file("a.tnnl", {"targets_dict": {KEY: [2, 3]}})
        self.win._import_tnnl()
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({2, 3})})
        self.assertEqual(ContinueTable.current().default, {})

    def test_declining_the_confirmation_changes_nothing(self):
        self.answer = False
        self._file("a.tnnl", {"targets_dict": {KEY: [2]}})
        self.win._import_tnnl()
        self.assertEqual(ContinueTable.current(), Table())
        self.assertEqual(self.changed, [])

    def test_cancelling_the_file_dialog_changes_nothing(self):
        self.path = ""
        self.win._import_tnl()
        self.assertEqual(ContinueTable.current(), Table())
        self.assertFalse([a for a in self.asked if "置き換え" in str(a)], "確認も出さない")

    def test_a_broken_file_is_reported_and_changes_nothing(self):
        self.win.toggle(5)
        path = Path(self.tmp.name) / "x.tnl"
        path.write_text("not json", encoding="utf-8")
        self.path = str(path)
        with patch.object(DebugLog, "exception"):
            self.win._import_tnl()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})})


class TestMainWindow(unittest.TestCase):
    def setUp(self):
        _fresh_store(self)
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            self.app = mainGUI.App()
        self.addCleanup(self.app.destroy)

    def test_the_count_label_and_the_two_checks(self):
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "無効 0件・デフォ 0件")
        self.assertTrue(self.app.v_ct_disabled.get())
        self.assertTrue(self.app.v_ct_default.get())

    def test_turning_a_check_off_is_saved_and_takes_effect(self):
        ContinueTable.set_current(ContinueTable.with_toggled(Table(), ContinueTable.DISABLED, KEY, 7))
        self.app.v_ct_disabled.set(False)
        self.app._on_continue_table_use()
        self.assertFalse(ContinueTable.current().use_disabled)
        self.assertFalse(ContinueTable.load().use_disabled, "保存される")
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "無効 1件・デフォ 0件")
        self.assertEqual(ContinueTable.apply({KEY: {7}}, ContinueTable.current()), {KEY: {7}})

    def test_the_button_opens_one_window_and_a_change_updates_the_count(self):
        self.app._open_continue_table()
        first = self.app._continue_table_window
        self.assertIsInstance(first, ContinueTableGUI.ContinueTableWindow)
        self.app._open_continue_table()
        self.assertIs(self.app._continue_table_window, first, "2 回目は同じ窓")
        first.toggle(5)
        first.v_which.set(ContinueTable.DEFAULT)
        first.toggle(6)
        first.toggle(7)
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "無効 1件・デフォ 2件")
        first.destroy()
        self.app._open_continue_table()
        self.assertIsNot(self.app._continue_table_window, first, "閉じたら作り直す")

    def test_the_saved_table_is_loaded_at_start(self):
        saved = ContinueTable.with_toggled(Table(use_default=False), ContinueTable.DEFAULT, ALT, 150)
        ContinueTable.save(saved)
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            app = mainGUI.App()
        self.addCleanup(app.destroy)
        self.assertEqual(ContinueTable.current(), saved)
        self.assertFalse(app.v_ct_default.get())
        self.assertEqual(app.lbl_ct_count.cget("text"), "無効 0件・デフォ 1件")
