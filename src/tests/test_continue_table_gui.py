"""GI: 続行表の画面（ContinueTableWindow とメイン画面のボタン・チェック・件数）。
窓は作った直後に隠れる（support）。本物の入力は使わず、ファイルを選ぶ・確認の画面は偽物にする。
保存は一時フォルダ（config.SETTINGS_PATH の差し替え）だけ。隠れた窓にはマウスの合成イベントが届かないので、
押す・乗せるは受け口（_on_click・_show_tip）を直接呼ぶ"""
from tests.support import *  # noqa: F401,F403
import cv2
import numpy as np
import ContinueTable
import ContinueTableGUI
from ContinueTable import Table, Store

KEY = "Classic/クラシック"
ALT = "Alternate/オルタネイト"
UNBOUND = "Unbound/アンバウンド"
EIGHT = "8 Pages/8ページ"


def _fresh_store(case):
    """今の表を空に・保存先（テスト用の一時フォルダ）の続行表のファイルを消す。終わったら同じに戻す"""
    ContinueTable.table_path().unlink(missing_ok=True)
    ContinueTable.set_current(Store())
    case.addCleanup(ContinueTable.table_path().unlink, missing_ok=True)
    case.addCleanup(ContinueTable.set_current, Store())


def _cur() -> Table:
    """今の保存内容の、窓 1 の表。編集の窓は「既定」のプリセットを編集し、窓 1 は何も選んでいない（＝既定）ので同じもの"""
    return ContinueTable.table_for(ContinueTable.current(), 1)


def _slot_ids(key):
    return dict(ContinueTableGUI.load_rounds())[key]


def _blocks(key, ids=None):
    """layout_for の結果を {種類: [(番号, 列, 行), ...]} と種類の並びにする"""
    blocks = ContinueTableGUI.layout_for(key, _slot_ids(key) if ids is None else ids)
    return {kind: cells for kind, cells, _pitch in blocks}, [kind for kind, _c, _p in blocks]


def _tids(cells):
    return [tid for tid, _col, _row in cells]


def _row_sizes(cells):
    sizes = {}
    for _tid, _col, row in cells:
        sizes[row] = sizes.get(row, 0) + 1
    return [sizes[r] for r in sorted(sizes)]


def _round_key(name):
    return next(k for k, _ in ContinueTableGUI.load_rounds() if k.startswith(name + "/"))


class TestGrid12(unittest.TestCase):
    """grid12 / pyramid: 空きを上の行の左端に置く規則（ListTool の寸法）"""

    def test_134_has_twelve_rows_ten_with_a_gap_at_the_left_and_two_full(self):
        cells = ContinueTableGUI.grid12(list(range(134)))
        self.assertEqual(_row_sizes(cells), [11] * 10 + [12] * 2)
        by_row = {}
        for _tid, col, row in cells:
            by_row.setdefault(row, []).append(col)
        for row in range(10):
            self.assertEqual(by_row[row], list(range(1, 12)), row)
        for row in (10, 11):
            self.assertEqual(by_row[row], list(range(12)), row)
        self.assertEqual(_tids(cells), list(range(134)), "渡した順のまま")

    def test_a_full_grid_has_no_gap(self):
        cells = ContinueTableGUI.grid12(list(range(24)))
        self.assertEqual(_row_sizes(cells), [12, 12])
        self.assertEqual(min(col for _t, col, _r in cells), 0)

    def test_one_gap_per_row_from_the_top(self):
        cells = ContinueTableGUI.grid12(list(range(23)))     # 2 行・空き 1
        self.assertEqual(_row_sizes(cells), [11, 12])
        self.assertEqual(min(col for _t, col, r in cells if r == 0), 1)
        self.assertEqual(min(col for _t, col, r in cells if r == 1), 0)

    def test_when_gaps_outnumber_rows_it_fills_from_the_left(self):
        cells = ContinueTableGUI.grid12(list(range(13)))     # 2 行・空き 11 は 1 行に 1 つでは置けない
        self.assertEqual(_row_sizes(cells), [12, 1])
        self.assertEqual(min(col for _t, col, _r in cells), 0)
        self.assertEqual(ContinueTableGUI.grid12([]), [])

    def test_pyramid_is_centred_11_9_7_5_3_1(self):
        cells = ContinueTableGUI.pyramid(list(range(36)))
        self.assertEqual(_row_sizes(cells), [11, 9, 7, 5, 3, 1])
        for row, size in enumerate(ContinueTableGUI.ALTERNATE_ROWS):
            cols = [col for _t, col, r in cells if r == row]
            self.assertEqual(cols[0] + cols[-1], ContinueTableGUI.COLUMNS - 1, "12 列の真ん中に寄る")
            self.assertEqual(len(cols), size)


class TestLayout(unittest.TestCase):
    """layout_for: 画面を作らずに、ブロックの並びと各マスの位置を確かめる"""

    def test_classic_follows_the_listtool_order_not_the_id_order(self):
        blocks, kinds = _blocks(KEY)
        classic = blocks["classic"]
        self.assertEqual(sorted(_tids(classic)), list(range(134)), "192 は本体に無い")
        self.assertEqual(classic[0], (0, 1, 0), "1 マス目は 0（行 0 の列 1）")
        at = {(col, row): tid for tid, col, row in classic}
        self.assertEqual(at[(0, 10)], 122)
        self.assertEqual(at[(0, 11)], 83)
        self.assertEqual(_tids(classic), ContinueTableGUI.listtool_order()["classic"])
        self.assertEqual(_row_sizes(classic), [11] * 10 + [12] * 2)

    def test_alternate_is_an_inverted_pyramid_in_the_listtool_order(self):
        blocks, kinds = _blocks(ALT)
        self.assertEqual(kinds, ["alternate"])
        cells = blocks["alternate"]
        self.assertEqual(_row_sizes(cells), [11, 9, 7, 5, 3, 1])
        self.assertEqual(sorted(_tids(cells)), list(range(134, 170)))
        row0 = [tid for tid, _col, row in sorted(cells, key=lambda c: c[1]) if row == 0]
        self.assertEqual(row0[5], 169, "1 段目の 6 番目（紫のハート）")
        self.assertEqual(row0[:3], [149, 150, 141])
        pitch = next(p for k, _c, p in ContinueTableGUI.layout_for(ALT, _slot_ids(ALT)) if k == "alternate")
        self.assertEqual(pitch, 46, "逆三角の段の間も 46px")

    def test_alternate_falls_back_to_twelve_columns_if_not_all_36_are_there(self):
        blocks = ContinueTableGUI.layout_for(ALT, list(range(134, 169)))
        self.assertEqual(len(blocks), 1)
        kind, cells, pitch = blocks[0]
        self.assertEqual((kind, pitch), ("alternate", 46))
        self.assertTrue(all(col == int(col) for _t, col, _r in cells), "逆三角ではない")
        self.assertEqual(_row_sizes(cells), [11, 12, 12])
        self.assertEqual(_tids(cells)[:3], [149, 150, 141], "順は alternate の順")

    def test_classic_round_has_the_bloodthirsty_three_in_the_variant_row(self):
        blocks, kinds = _blocks(KEY)
        self.assertEqual(kinds, ["classic", "variant"])
        self.assertEqual(blocks["variant"], [(190, 0, 0), (191, 1, 0), (192, 2, 0)], "左寄せの 1 行")

    def test_every_other_round_with_192_has_only_192_in_the_variant_row(self):
        """HHI（190）と Atrached（191）は Classic だけ（GK）。Classic.exe・Randomizer にも 192 が付いた"""
        for name in ("Classic.exe", "Randomizer", "Ghost", "Punished", "Bloodbath", "Double Trouble",
                     "Bloodbath EX", "Sabotage star", "Sabotage murder"):
            blocks, _kinds = _blocks(_round_key(name))
            self.assertEqual(_tids(blocks["variant"]), [192], name)
            self.assertNotIn(192, _tids(blocks["classic"]) + _tids(blocks.get("alternate", [])), name)

    def test_190_and_191_are_in_no_slot_but_classics_variant_row(self):
        for key, ids in ContinueTableGUI.load_rounds():
            self.assertFalse({190, 191} & set(ids), key)
        raw = json.loads((REPO_ROOT / "assets" / "data" / "continue_slots.json").read_text(encoding="utf-8"))
        for key, ids in raw["rounds"]:
            self.assertFalse({190, 191} & set(ids), key)
        for key, ids in ContinueTableGUI.load_rounds():
            variant = [t for kind, cells, _p in ContinueTableGUI.layout_for(key, ids) if kind == "variant"
                       for t in _tids(cells)]
            self.assertEqual({190, 191} <= set(variant), key == KEY, key)

    def test_rounds_without_192_have_no_variant_row(self):
        for name in ("Cracked", "Alternate"):
            self.assertNotIn("variant", _blocks(_round_key(name))[1], name)

    def test_fog_and_midnight_are_classic_then_alternate_then_variant(self):
        for name in ("Fog", "Midnight"):
            blocks, kinds = _blocks(_round_key(name))
            self.assertEqual(kinds, ["classic", "alternate", "variant"], name)
            self.assertEqual(_tids(blocks["variant"]), [192, 315, 316], name)
            self.assertEqual(_row_sizes(blocks["alternate"]), [11, 9, 7, 5, 3, 1], name)
            self.assertEqual(sorted(_tids(blocks["classic"])), list(range(134)), name)

    def test_unbound_is_a_twelve_column_block_in_id_order_with_192_and_self_inserts_in_the_variant_row(self):
        blocks, kinds = _blocks(UNBOUND)
        self.assertEqual(kinds, ["unbound", "variant"])
        self.assertEqual(_tids(blocks["unbound"]), list(range(200, 284)))
        self.assertEqual(_row_sizes(blocks["unbound"]), [12] * 7)
        self.assertEqual(blocks["variant"], [(192, 0, 0), (config.SELF_INSERTS_BLOODTHIRSTY_SLOT, 1, 0)])

    def test_the_moon_tab_is_the_union_of_the_two_moon_slots_without_190_and_191(self):
        blocks, kinds = _blocks(ContinueTableGUI.MOON_TAB)
        self.assertEqual(kinds, ["special"], "バリアントの段は作らない")
        self.assertEqual(_tids(blocks["special"]), [192, 196, 197, 198, 199, 312, 313, 314, 315, 316, 317])

    def test_the_moon_tab_keeps_the_union_even_if_the_two_slots_differ(self):
        slots = [("Mystic Moon/x", [190, 192, 196]), ("Classic/c", [1]), ("Blood Moon/y", [191, 192, 317])]
        self.assertEqual(ContinueTableGUI._moon_tab(slots), ("Moon", [192, 196, 317]))

    def test_eight_pages_shows_only_ids_that_eight_pages_has(self):
        blocks, kinds = _blocks(EIGHT)
        shown = {t for cells in blocks.values() for t in _tids(cells)}
        allowed = ContinueTableGUI.eight_pages_ids()
        self.assertNotIn("variant", kinds)
        self.assertTrue(shown <= allowed)
        self.assertEqual(shown, allowed & set(_slot_ids(EIGHT)))
        self.assertLess(len(shown), len(_slot_ids(EIGHT)), "出ないテラーは絞られる")
        self.assertTrue({170, 171, 172, 173, 174, 193, 194} <= shown)
        self.assertEqual(_tids(blocks["special"])[:3], [170, 171, 172], "特別のブロックは番号順")
        self.assertFalse({3, 5, 6, 7, 10} & shown, "list_id に無い番号")
        small, _k = _blocks(EIGHT, [0, 3, 177])
        self.assertEqual({t for cells in small.values() for t in _tids(cells)}, {0, 177})

    def test_ids_outside_every_range_are_not_lost(self):
        blocks, _kinds = _blocks(KEY, [0, 195, 300])
        self.assertEqual(_tids(blocks["special"]), [195, 300])

    def test_eight_pages_ids_come_from_the_list_ids_of_terrors_json(self):
        raw = json.loads((REPO_ROOT / "assets" / "data" / "terrors.json").read_text(encoding="utf-8"))
        self.assertEqual(ContinueTableGUI.eight_pages_ids(),
                         frozenset(e["list_id"] for e in raw["8pages"]))

    def test_layout_json_has_every_classic_and_alternate_id_once(self):
        order = ContinueTableGUI.listtool_order()
        self.assertEqual(sorted(order["classic"]), list(range(134)))
        self.assertEqual(sorted(order["alternate"]), list(range(134, 170)))

    def test_there_is_no_special_round_in_the_screen(self):
        keys = [k for k, _ in ContinueTableGUI.load_rounds()]
        self.assertNotIn("Special/Moon", keys)
        self.assertEqual(len(keys), 17)
        self.assertEqual(len(set(ContinueTableGUI.round_display_name(k) for k in keys)), 17)

    def test_the_four_moon_rounds_are_one_moon_in_the_place_of_mystic_moon(self):
        names = [ContinueTableGUI.round_display_name(k) for k, _ in ContinueTableGUI.load_rounds()]
        self.assertEqual(names.count("Moon"), 1)
        for gone in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice"):
            self.assertNotIn(gone, names)
        self.assertEqual(names[names.index("Moon") - 1], "Unbound", "Mystic Moon の位置")
        self.assertEqual(ContinueTableGUI.ROUND_KEYS_FOR_TAB["Moon"],
                         ["Mystic Moon/ミスティックムーン", "Blood Moon/ブラッドムーン",
                          "Twilight/トワイライト", "Solstice/ソルスティス"])
        self.assertEqual(ContinueTableGUI.table_keys("Moon"), ContinueTableGUI.ROUND_KEYS_FOR_TAB["Moon"])
        self.assertEqual(ContinueTableGUI.table_keys(KEY), [KEY])

    def test_a_round_without_ids_makes_no_blocks(self):
        self.assertEqual(ContinueTableGUI.layout_for(KEY, []), [])


class TestIconLook(unittest.TestCase):
    def test_measured_geometry(self):
        g = ContinueTableGUI
        self.assertEqual((g.CELL_SIZE, g.ICON_SIZE, g.ICON_INSET, g.X_PITCH, g.Y_PITCH),
                         (40, 32, 4, 46, 46), "間隔は縦横とも 46px（40px のマス＋6px）")
        self.assertEqual(g.CANVAS_WIDTH, 574)
        self.assertEqual(g.COLOR_GRID_BG, "#2d2d2d")

    def test_opacity_levels_are_30_60_100_and_15_percent(self):
        self.assertEqual(ContinueTableGUI.LEVELS, {"dim": 0.3, "other": 0.6, "full": 1.0, "faint": 0.15})

    def test_blending_over_the_background_is_icon_times_alpha_plus_bg_times_rest(self):
        red = np.array([[[0, 0, 200, 255]]], dtype=np.uint8)      # BGRA の赤
        out = ContinueTableGUI.blend_over_bg(red, 0.3)
        self.assertLessEqual(np.abs(out[0, 0].astype(int) - np.array([32, 32, 92])).max(), 1)
        clear = np.array([[[0, 0, 200, 0]]], dtype=np.uint8)      # 透明は背景そのまま
        self.assertEqual(tuple(ContinueTableGUI.blend_over_bg(clear, 1.0)[0, 0]), (45, 45, 45))
        self.assertEqual(tuple(ContinueTableGUI.blend_over_bg(red[..., :3], 1.0)[0, 0]), (0, 0, 200))

    def test_the_png_is_32px_and_blended_by_the_alpha(self):
        decode = lambda b: cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_UNCHANGED)
        full = decode(ContinueTableGUI.icon_png(0))
        self.assertEqual(full.shape[:2], (32, 32))
        for alpha in (0.3, 0.6, 0.15):
            part = decode(ContinueTableGUI.icon_png(0, alpha))
            self.assertEqual(part.shape, full.shape)
            expected = full[..., :3].astype(float) * alpha + 45 * (1 - alpha)
            self.assertLessEqual(np.abs(part[..., :3].astype(float) - expected).max(), 2)
        self.assertIsNone(ContinueTableGUI.icon_png(99999, 0.3))

    def test_blend_color(self):
        self.assertEqual(ContinueTableGUI.blend_color("#ffffff", 0.3), "#6c6c6c")
        self.assertEqual(ContinueTableGUI.blend_color("#102030", 1.0), "#102030")

    def test_frame_points_stay_inside_the_cell(self):
        for width in (1, 2):
            pts = ContinueTableGUI.frame_points(100, 200, width)
            xs, ys = pts[0::2], pts[1::2]
            self.assertGreaterEqual(min(xs), 100)
            self.assertLessEqual(max(xs), 139)
            self.assertGreaterEqual(min(ys), 200)
            self.assertLessEqual(max(ys), 239)
            self.assertEqual(len(pts), 16, "角を落とした 8 角形")


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
        """保存したファイルの窓 1 の表（編集の窓は「既定」を編集する）"""
        return ContinueTable.table_for(ContinueTable.load(), 1)

    def _select_round(self, name):
        names = list(self.win.list_rounds.get(0, "end"))
        self.win.select_round(names.index(name))


class TestWindow(_WindowCase):
    def test_it_opens_and_closes_without_being_shown(self):
        self.assertIn(self.win, _HIDDEN_WINDOWS)
        self.assertFalse(self.win.winfo_viewable())
        self.win.destroy()
        self.assertFalse(self.win.winfo_exists())

    def test_the_round_is_chosen_from_a_narrow_list_on_the_left_not_a_dropdown(self):
        self.assertFalse(hasattr(self.win, "combo_round"), "上のドロップダウンは無い")
        lst = self.win.list_rounds
        self.assertIsInstance(lst, tk.Listbox)
        self.assertEqual(lst.pack_info()["side"], "left")
        self.assertEqual(int(lst.cget("width")), 0, "幅は名前が入る最小")
        self.assertEqual(lst.cget("bg"), ContinueTableGUI.COLOR_GRID_BG, "暗い色")
        values = list(lst.get(0, "end"))
        self.assertEqual(len(values), 17)
        self.assertEqual(values[0], "Classic")
        self.assertNotIn("Special", values)
        self.assertEqual(lst.curselection(), (0,))

    def test_moon_is_one_entry_and_the_four_moon_rounds_are_not_in_the_list(self):
        values = list(self.win.list_rounds.get(0, "end"))
        self.assertEqual(values.count("Moon"), 1)
        for gone in ("Mystic Moon", "Blood Moon", "Twilight", "Solstice"):
            self.assertNotIn(gone, values)

    def test_choosing_in_the_list_shows_that_round(self):
        names = list(self.win.list_rounds.get(0, "end"))
        self.win.list_rounds.selection_clear(0, "end")
        self.win.list_rounds.selection_set(names.index("Unbound"))
        self.assertTrue(self.win.list_rounds.bind("<<ListboxSelect>>"), "選んだら呼ぶ束縛がある")
        self.win._on_round_select()               # 隠れた窓にはイベントが届かないので受け口を直接呼ぶ
        self.assertEqual(self.win._round_key(), UNBOUND)
        self.assertEqual(sorted(self.win._cells),
                         [192] + list(range(200, 284)) + [config.SELF_INSERTS_BLOODTHIRSTY_SLOT])

    def test_choosing_the_same_round_again_does_not_redraw(self):
        with patch.object(self.win, "_show_round") as show:
            self.win._on_round_select()
        self.assertEqual(show.call_count, 0)

    def test_the_select_all_and_clear_all_buttons_replace_clear_the_round(self):
        texts = []
        stack = [self.win]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if isinstance(w, ttk.Button):
                texts.append(w.cget("text"))
        self.assertIn("全選択", texts)
        self.assertIn("全解除", texts)
        self.assertNotIn("このラウンドを全部外す", texts)

    def test_the_first_round_has_one_cell_per_terror_including_the_variant_row(self):
        self.assertEqual(sorted(self.win._cells), list(range(134)) + [190, 191, 192])
        self.assertIsNotNone(self.win._photo(0, "full"), "アイコンがある番号は絵")
        self.assertIsNotNone(self.win._photo(317, "full"), "Glorbo にも絵がある")

    def _pos(self, tid):
        """マスの左上 (x, y)（キャンバス上）"""
        return self.win._origin(self.win._cells[tid])

    def test_cells_are_40px_with_the_icon_32px_inset_4px(self):
        x, y = self._pos(0)
        cell = self.win._cells[0]
        self.assertEqual(self.win.canvas.coords(cell.face), [x + 4, y + 4])
        xs = self.win.canvas.coords(cell.frame)[0::2]
        self.assertLessEqual(max(xs) - min(xs), 39)
        self.assertEqual(self.win._photo(0, "full").width(), 32)
        self.assertEqual(self.win.canvas.cget("bg"), "#2d2d2d")

    def test_classic_is_laid_out_with_the_listtool_pitches_and_gaps(self):
        (x0, y0), (x1, y1) = self._pos(0), self._pos(1)
        self.assertEqual((x1 - x0, y1 - y0), (46, 0), "0 の隣は 1 で横 46px")
        self.assertEqual(self._pos(12)[1] - y0, 46, "次の行は縦 46px")
        left = x0 - 46                                            # 上の行は左端を 1 つ空ける
        self.assertEqual(self._pos(122), (left, y0 + 10 * 46), "下の 2 行は左端から")
        self.assertEqual(self._pos(83), (left, y0 + 11 * 46))

    def test_the_variant_row_is_left_aligned_one_empty_row_below_the_grid(self):
        left, last_row = self._pos(122)[0], self._pos(83)[1]
        self.assertEqual(self._pos(190)[0], left, "バリアントの段は左寄せ")
        self.assertEqual(self._pos(191)[0] - self._pos(190)[0], 46)
        self.assertEqual(self._pos(190)[1] - last_row, 46 + 46, "最後の行の下に 1 行（46px）あけた下")

    def test_fog_blocks_are_classic_alternate_variant_with_a_46px_gap_between(self):
        self._select_round("Fog")
        classic_last = max(self._pos(t)[1] for t in range(134))
        alt = {t: self._pos(t) for t in range(134, 170)}
        alt_top, alt_last = min(y for _x, y in alt.values()), max(y for _x, y in alt.values())
        self.assertEqual(alt_top - classic_last, 46 + 46, "Classic の最後の行の下に 1 行あけて Alternate")
        self.assertEqual(alt_last - alt_top, 5 * 46, "Alternate の縦も 46px")
        variant_top = self._pos(192)[1]
        self.assertEqual(variant_top - alt_last, 46 + 46, "Alternate の最後の行の下に 1 行あけてバリアント")
        self.assertEqual(self._pos(315)[1], variant_top)
        self.assertEqual(self.win._content_height, variant_top + 46 + ContinueTableGUI.TOP_PAD,
                         "最後のブロックの下に余白")

    def test_alternate_rows_are_an_inverted_pyramid_on_screen(self):
        self._select_round("Alternate")
        self.assertEqual(sorted(self.win._cells), list(range(134, 170)))
        rows = {}
        for tid in self.win._cells:
            x, y = self._pos(tid)
            rows.setdefault(y, []).append((x, tid))
        ys = sorted(rows)
        ordered = [sorted(rows[y]) for y in ys]
        self.assertEqual([len(r) for r in ordered], [11, 9, 7, 5, 3, 1])
        self.assertEqual([b - a for a, b in zip(ys, ys[1:])], [46] * 5)
        centre0 = (ordered[0][0][0] + ordered[0][-1][0]) / 2
        for row in ordered:
            xs = [x for x, _t in row]
            self.assertTrue(all(b - a == 46 for a, b in zip(xs, xs[1:])))
            self.assertAlmostEqual((xs[0] + xs[-1]) / 2, centre0, delta=1)
        self.assertEqual(ordered[0][5][1], 169, "1 段目の 6 番目")
        self.assertEqual(self.win._content_height, ContinueTableGUI.TOP_PAD * 2 + 5 * 46 + 46)

    def test_switching_the_round_changes_the_grid(self):
        self._select_round("Unbound")
        self.assertEqual(sorted(self.win._cells),
                         [192] + list(range(200, 284)) + [config.SELF_INSERTS_BLOODTHIRSTY_SLOT])
        self._select_round("Moon")
        self.assertEqual(sorted(self.win._cells), [192, 196, 197, 198, 199, 312, 313, 314, 315, 316, 317])
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
        self.assertEqual(shown(config.SELF_INSERTS_BLOODTHIRSTY_SLOT), "Self Inserts (Bloodthirsty)")
        self._select_round("8 Pages")
        self.assertEqual(shown(193), "The Navigator")
        self._select_round("Moon")
        self.assertEqual(shown(312), "Riftmonster")
        self.assertEqual(shown(317), "Glorbo")
        self.win._hide_tip()
        self.assertEqual(self.win.canvas.find_withtag("tip"), ())

    def test_glorbo_shows_the_question_mark_icon(self):
        self._select_round("Moon")
        self.assertIsNone(self.win._cells[317].label, "名前の札ではなく絵")
        sheet = ContinueTableGUI._sheet("terrors")
        crop = sheet[0:32, 0:32]                             # terrors.png の列 0・段 0
        expected = ContinueTableGUI.blend_over_bg(crop, 1.0)
        decoded = cv2.imdecode(np.frombuffer(ContinueTableGUI.icon_png(317), np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(int(np.abs(decoded[..., :3].astype(int) - expected.astype(int)).max()), 0)

    def test_a_cell_with_no_icon_is_a_name_tile_and_dims_with_its_state(self):
        self.addCleanup(setattr, ContinueTableGUI, "icon_png", ContinueTableGUI.icon_png)
        ContinueTableGUI.icon_png = lambda tid, alpha=1.0: None      # 絵が無い番号の扱いを確かめる
        self._select_round("Moon")
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
        self.assertEqual(_cur().disabled, {KEY: frozenset({5})}, "判定にはその場で効く")
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
        self.assertEqual(_cur().disabled, {})
        self.assertEqual(self._style(5)[:2], (ContinueTableGUI.COLOR_IDLE, 1.0))

    def test_a_variant_cell_goes_into_the_rounds_key(self):
        self.win.toggle(190)
        self.win.toggle(192)
        self.assertEqual(_cur().disabled, {KEY: frozenset({190, 192})})
        self._select_round("Unbound")
        self.win.toggle(192)
        self.win.toggle(config.SELF_INSERTS_BLOODTHIRSTY_SLOT)
        self.assertEqual(_cur().disabled[UNBOUND],
                         frozenset({192, config.SELF_INSERTS_BLOODTHIRSTY_SLOT}))

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

    def test_four_opacity_variants_are_cached_per_icon(self):
        photos = {lv: self.win._photo(5, lv) for lv in ContinueTableGUI.LEVELS}
        self.assertEqual(len({str(p) for p in photos.values()}), 4, "4 種は別の絵")
        for level, photo in photos.items():
            self.assertIs(self.win._photo(5, level), photo, "作り直さず持っておく")

    def test_the_frame_color_and_width_and_brightness_for_each_state(self):
        dis, dfl = ContinueTableGUI.COLOR_DISABLED, ContinueTableGUI.COLOR_DEFAULT
        self.assertEqual(self._style(5), (ContinueTableGUI.COLOR_IDLE, 1.0, self._photo_name(5, "dim")))
        self.win.toggle(5)                                           # 続行無効に入れる
        self.assertEqual(self._style(5), (dis, 2.0, self._photo_name(5, "full")))
        self.win.v_which.set(ContinueTable.DEFAULT)                   # デフォ続行の編集へ
        self.win._restyle_all()
        self.assertEqual(self._style(5), (dis, 1.0, self._photo_name(5, "other")), "もう片方: 60%＋細い枠")
        self.assertEqual(self._style(6), (ContinueTableGUI.COLOR_IDLE, 1.0, self._photo_name(6, "dim")))
        self.win.toggle(6)
        self.assertEqual(self._style(6), (dfl, 2.0, self._photo_name(6, "full")))
        self.win.v_which.set(ContinueTable.DISABLED)
        self.win._restyle_all()
        self.assertEqual(self._style(5), (dis, 2.0, self._photo_name(5, "full")))
        self.assertEqual(self._style(6), (dfl, 1.0, self._photo_name(6, "other")))

    def test_the_default_table_is_green_and_the_other_table_is_not_changed(self):
        self.win.toggle(5)
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.toggle(5)
        self.win.toggle(6)
        self.assertEqual(_cur().default, {KEY: frozenset({5, 6})})
        self.assertEqual(_cur().disabled, {KEY: frozenset({5})}, "もう片方は変えない")

    def test_each_round_has_its_own_entries(self):
        self.win.toggle(5)
        self._select_round("Alternate")
        self.win.toggle(150)
        self.assertEqual(_cur().disabled,
                         {KEY: frozenset({5}), ALT: frozenset({150})})
        self._select_round("Classic")
        self.assertEqual(self._style(5)[1], 2.0, "戻ると選んだ状態で描かれる")

    def test_the_name_search_keeps_the_grid_and_lights_up_the_matches(self):
        items = self.win.canvas.find_all()
        self.win.toggle(1)
        self.win.v_search.set("huggy")
        self.win.update()
        self.assertEqual(self.win.canvas.find_all(), items, "格子は作り直さない")
        self.assertEqual(self._style(0), (ContinueTableGUI.COLOR_MATCH, 2.0, self._photo_name(0, "full")))
        self.assertEqual(self._style(1), (ContinueTableGUI.COLOR_FAINT, 1.0, self._photo_name(1, "faint")),
                         "合わないものは暗いまま枠も薄く")
        self.win.v_search.set("")
        self.assertEqual(self._style(0)[:2], (ContinueTableGUI.COLOR_IDLE, 1.0))
        self.assertEqual(self._style(1)[:2], (ContinueTableGUI.COLOR_DISABLED, 2.0))

    def test_the_search_uses_the_names_of_the_round(self):
        self._select_round("Unbound")
        self.win.v_search.set("pack of wild yet bloodthirsty")
        self.assertEqual(self._style(192)[0], ContinueTableGUI.COLOR_MATCH)

    def test_clear_all_takes_everything_out_of_the_current_table_only(self):
        self.win.toggle(5)
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.toggle(6)
        self.win.toggle(7)
        self.win.clear_all()
        self.assertEqual(_cur().default, {})
        self.assertEqual(_cur().disabled, {KEY: frozenset({5})})

    def test_clear_all_also_clears_the_variant_row(self):
        for tid in (5, 190, 191, 192):
            self.win.toggle(tid)
        self.win.clear_all()
        self.assertEqual(_cur().disabled, {})
        self.assertEqual(self._style(192)[:2], (ContinueTableGUI.COLOR_IDLE, 1.0))

    def test_select_all_puts_every_visible_cell_including_the_variant_row_into_the_current_table(self):
        self.win.select_all()
        self.assertEqual(_cur().disabled,
                         {KEY: frozenset(list(range(134)) + [190, 191, 192])})
        self.assertEqual(_cur().default, {}, "もう片方は変えない")
        self.assertEqual(self._style(190)[:2], (ContinueTableGUI.COLOR_DISABLED, 2.0))
        self.assertEqual(len(self.changed), 1)
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.win.select_all()
        self.assertEqual(_cur().default, _cur().disabled)

    def test_select_all_leaves_other_rounds_alone(self):
        self._select_round("8 Pages")
        self.win.select_all()
        self.assertEqual(_cur().disabled[EIGHT], frozenset(self.win._cells))
        self.assertNotIn(KEY, _cur().disabled)

    def test_select_all_and_clear_all_with_a_search_touch_only_the_matches(self):
        self.win.toggle(1)
        self.win.v_search.set("huggy")
        self.win.select_all()
        self.assertEqual(_cur().disabled[KEY], frozenset({0, 1}), "合う 0 だけ足す")
        self.win.clear_all()
        self.assertEqual(_cur().disabled[KEY], frozenset({1}), "合う 0 だけ外す")

    def test_select_all_reaches_the_decision_at_once_and_is_saved_later(self):
        self.win.select_all()
        effective = ContinueTable.apply({KEY: {7}}, _cur())
        self.assertNotIn(5, ContinueTable.apply({KEY: {5}}, _cur())[KEY], "続行無効が判定に効く")
        self.assertEqual(effective[KEY], set(), "判定の表にその場で入る")
        self.assertFalse(ContinueTable.table_path().exists(), "保存はまとめて")
        self.win._flush_save()
        self.assertEqual(self._saved().disabled[KEY], frozenset(list(range(134)) + [190, 191, 192]))
        self.win.clear_all()
        self.win._flush_save()
        self.assertEqual(self._saved().disabled, {})

    def test_the_moon_writes_to_all_four_keys_and_clears_them(self):
        keys = ContinueTableGUI.ROUND_KEYS_FOR_TAB["Moon"]
        self._select_round("Moon")
        self.win.toggle(317)
        self.assertEqual(_cur().disabled, {k: frozenset({317}) for k in keys})
        self.win.toggle(317)
        self.assertEqual(_cur().disabled, {})
        self.win.select_all()
        every = frozenset({192, 196, 197, 198, 199, 312, 313, 314, 315, 316, 317})
        self.assertEqual(_cur().disabled, {k: every for k in keys})
        self.win.clear_all()
        self.assertEqual(_cur().disabled, {})

    def test_the_moon_shows_a_cell_as_in_if_any_of_the_four_has_it(self):
        keys = ContinueTableGUI.ROUND_KEYS_FOR_TAB["Moon"]
        ContinueTable.set_current(Store(disabled_presets={"既定": {keys[2]: frozenset({312})}}))     # Twilight だけ
        self._select_round("Moon")
        self.assertEqual(self._style(312)[:2], (ContinueTableGUI.COLOR_DISABLED, 2.0))
        self.assertEqual(self._style(313)[:2], (ContinueTableGUI.COLOR_IDLE, 1.0))
        self.win.toggle(312)                      # 入っているので外す（4 つすべてから）
        self.assertEqual(_cur().disabled, {})

    def test_a_moon_click_does_not_touch_other_rounds(self):
        self.win.toggle(5)
        self._select_round("Moon")
        self.win.toggle(317)
        self.assertEqual(_cur().disabled[KEY], frozenset({5}))

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
            self.assertEqual(_cur().disabled, {KEY: frozenset({5, 6, 7})})
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

    def test_clearing_all_is_saved_too(self):
        self.win.toggle(5)
        self.win._flush_save()
        self.win.clear_all()
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
        self.assertEqual(_cur().default, {KEY: frozenset({5, 6})}, "置き換え")
        self.win._flush_save()
        self.assertEqual(self._saved().default, {KEY: frozenset({5, 6})})
        self.assertIn("*.tnl", self.asked)
        self.assertTrue(any("置き換え" in str(a) for a in self.asked), "確認を出す")
        self.assertEqual(self.changed, [1, 1])

    def test_tnnl_replaces_the_disabled_table_even_when_the_default_is_shown(self):
        self.win.v_which.set(ContinueTable.DEFAULT)
        self._file("a.tnnl", {"targets_dict": {KEY: [2, 3]}})
        self.win._import_tnnl()
        self.assertEqual(_cur().disabled, {KEY: frozenset({2, 3})})
        self.assertEqual(_cur().default, {})

    def test_declining_the_confirmation_changes_nothing(self):
        self.answer = False
        self._file("a.tnnl", {"targets_dict": {KEY: [2]}})
        self.win._import_tnnl()
        self.assertEqual(ContinueTable.current(), Store())
        self.assertEqual(self.changed, [])

    def test_cancelling_the_file_dialog_changes_nothing(self):
        self.path = ""
        self.win._import_tnl()
        self.assertEqual(ContinueTable.current(), Store())
        self.assertFalse([a for a in self.asked if "置き換え" in str(a)], "確認も出さない")

    def test_a_broken_file_is_reported_and_changes_nothing(self):
        self.win.toggle(5)
        path = Path(self.tmp.name) / "x.tnl"
        path.write_text("not json", encoding="utf-8")
        self.path = str(path)
        with patch.object(DebugLog, "exception"):
            self.win._import_tnl()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(_cur().disabled, {KEY: frozenset({5})})


def _widgets(widget, cls):
    """widget の下にある cls の部品すべて"""
    found = [widget] if isinstance(widget, cls) else []
    for child in widget.winfo_children():
        found.extend(_widgets(child, cls))
    return found


class TestPresetControls(_WindowCase):
    """GL: 編集の窓のプリセット（ドロップダウンと 新規・複製・名前変更・削除）。名前を聞く・確認は偽物"""

    def setUp(self):
        super().setUp()
        self.patch_save = patch.object(ContinueTableGUI, "SAVE_DELAY_MS", 30)
        self.patch_save.start()
        self.addCleanup(self.patch_save.stop)
        self.names = []                    # 名前を聞かれたときの答え（None は取り消し）
        self.asked = []                    # 聞かれた (題, 初期値)
        self.confirms = []
        self.errors = []
        self.answer = True
        self.win._ask_name = lambda title, initial="": (self.asked.append((title, initial)), self.names.pop(0))[1]
        self.win._confirm = lambda text: (self.confirms.append(text), self.answer)[1]
        self.win._error = self.errors.append

    def _store(self):
        return ContinueTable.current()

    def _names(self, which=ContinueTable.DISABLED):
        return ContinueTable.preset_names(self._store(), which)

    def _press(self, text):
        button = next(b for b in _widgets(self.win, ttk.Button) if b.cget("text") == text)
        button.invoke()

    def test_it_shows_the_base_preset_with_a_dropdown_and_four_buttons_next_to_the_switch(self):
        self.assertEqual(self.win.v_preset.get(), "既定")
        self.assertEqual(list(self.win.cb_preset["values"]), ["既定"])
        self.assertEqual(str(self.win.cb_preset.cget("state")), "readonly")
        texts = {b.cget("text") for b in _widgets(self.win, ttk.Button)}
        self.assertTrue({"新規", "複製", "名前変更", "削除"} <= texts)
        radios = _widgets(self.win, ttk.Radiobutton)
        self.assertEqual(radios[0].master, self.win.cb_preset.master, "切り替えと同じ段にある")

    def test_new_makes_an_empty_preset_selects_it_and_saves(self):
        self.win.toggle(5)                                  # 既定に 5
        self.names = ["Boss用"]
        self._press("新規")
        self.assertEqual(self._names(), ["既定", "Boss用"])
        self.assertEqual(self.win.v_preset.get(), "Boss用")
        self.assertEqual(list(self.win.cb_preset["values"]), ["既定", "Boss用"])
        self.assertEqual(self._store().disabled_presets["Boss用"], {})
        self.win.toggle(6)                                  # 選んだプリセットに入る
        self.assertEqual(self._store().disabled_presets["Boss用"], {KEY: frozenset({6})})
        self.assertEqual(self._store().disabled_presets["既定"], {KEY: frozenset({5})}, "既定は変わらない")
        self.assertEqual(self._style(5)[0], ContinueTableGUI.COLOR_IDLE, "格子は選んだプリセットの中身")
        self.win._flush_save()
        self.assertEqual(ContinueTable.load().disabled_presets["Boss用"], {KEY: frozenset({6})})
        self.assertEqual(self.errors, [])
        self.assertTrue(self.changed, "メイン画面へ知らせる")

    def test_new_with_a_taken_name_or_empty_name_is_refused_and_cancel_does_nothing(self):
        before = self._store()
        self.names = ["既定", "  ", "なし", None]
        for _ in self.names[:]:
            self._press("新規")
        self.assertEqual(len(self.errors), 3, "同名・空・「なし」はそれぞれ理由を出す")
        self.assertEqual(self._store(), before)
        self.assertEqual(self.win.v_preset.get(), "既定")

    def test_copy_asks_with_a_default_name_and_copies_the_content(self):
        self.win.toggle(5)
        self.names = ["既定 のコピー"]
        self._press("複製")
        self.assertEqual(self.asked[-1][1], "既定 のコピー", "名前の既定は「〇〇 のコピー」")
        self.assertEqual(self._names(), ["既定", "既定 のコピー"])
        self.assertEqual(self.win.v_preset.get(), "既定 のコピー")
        self.assertEqual(self._store().disabled_presets["既定 のコピー"], {KEY: frozenset({5})})
        self.win.toggle(5)                                  # コピーの方だけ外す
        self.assertEqual(self._store().disabled_presets["既定 のコピー"], {})
        self.assertEqual(self._store().disabled_presets["既定"], {KEY: frozenset({5})})

    def test_copy_with_a_taken_name_is_refused(self):
        self.names = ["既定"]
        self._press("複製")
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self._names(), ["既定"])

    def test_rename_changes_the_name_and_windows_that_used_it_follow(self):
        self.names = ["B"]
        self._press("新規")
        ContinueTable.set_current(ContinueTable.with_choice(self._store(), 2, ContinueTable.DISABLED, "B"))
        self.names = ["B改"]
        self._press("名前変更")
        self.assertEqual(self.asked[-1][1], "B", "今の名前が初期値")
        self.assertEqual(self._names(), ["既定", "B改"])
        self.assertEqual(self.win.v_preset.get(), "B改")
        self.assertEqual(ContinueTable.choice_for(self._store(), 2, ContinueTable.DISABLED), "B改")

    def test_rename_to_a_name_in_use_is_refused_but_keeping_the_name_is_fine(self):
        self.names = ["B"]
        self._press("新規")
        self.names = ["既定"]
        self._press("名前変更")
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self._names(), ["既定", "B"])
        self.names = ["B"]
        self._press("名前変更")
        self.assertEqual(len(self.errors), 1, "同じ名前のままは何もしない（エラーにしない）")

    def test_delete_asks_first_and_windows_that_used_it_go_back_to_the_base(self):
        self.names = ["B"]
        self._press("新規")
        ContinueTable.set_current(ContinueTable.with_choice(self._store(), 3, ContinueTable.DISABLED, "B"))
        self.answer = False
        self._press("削除")
        self.assertEqual(len(self.confirms), 1)
        self.assertIn("B", self.confirms[0])
        self.assertEqual(self._names(), ["既定", "B"], "いいえなら消さない")
        self.answer = True
        self._press("削除")
        self.assertEqual(self._names(), ["既定"])
        self.assertEqual(self.win.v_preset.get(), "既定", "消えた名前を選んだままにしない")
        self.assertEqual(ContinueTable.choice_for(self._store(), 3, ContinueTable.DISABLED), "既定")
        self.win.toggle(5)
        self.assertEqual(self._store().disabled_presets["既定"], {KEY: frozenset({5})}, "以後は既定を編集する")

    def test_the_last_preset_cannot_be_deleted(self):
        self._press("削除")
        self.assertEqual(self.confirms, [], "確認も出さない")
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self._names(), ["既定"])

    def test_deleting_the_base_preset_sends_windows_to_the_first_one(self):
        self.names = ["B"]
        self._press("新規")
        self.win.v_preset.set("既定")
        self.win._on_preset_picked()
        self._press("削除")
        self.assertEqual(self._names(), ["B"])
        self.assertEqual(self.win.v_preset.get(), "B")
        self.assertEqual(ContinueTable.choice_for(self._store(), 1, ContinueTable.DISABLED), "B")

    def test_picking_in_the_dropdown_switches_what_the_grid_edits_and_shows(self):
        self.win.toggle(5)
        self.names = ["B"]
        self._press("新規")
        self.assertEqual(self._style(5)[0], ContinueTableGUI.COLOR_IDLE, "B は空")
        self.win.v_preset.set("既定")
        self.win._on_preset_picked()
        self.assertEqual(self._style(5)[0], ContinueTableGUI.COLOR_DISABLED, "既定の 5 が出る")
        self.win.select_all()
        self.assertEqual(self._store().disabled_presets["B"], {}, "全選択は選んでいるプリセットだけ")
        self.assertTrue(self._store().disabled_presets["既定"][KEY] > {5})
        self.win.clear_all()
        self.assertEqual(self._store().disabled_presets["既定"], {})

    def test_the_two_kinds_have_their_own_presets_and_selections(self):
        self.names = ["無効B"]
        self._press("新規")
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.assertEqual(list(self.win.cb_preset["values"]), ["既定"], "デフォ続行のプリセットは別")
        self.assertEqual(self.win.v_preset.get(), "既定")
        self.names = ["デフォB"]
        self._press("新規")
        self.assertEqual(self._names(ContinueTable.DEFAULT), ["既定", "デフォB"])
        self.assertEqual(self._names(ContinueTable.DISABLED), ["既定", "無効B"])
        self.win.toggle(7)
        self.assertEqual(self._store().default_presets["デフォB"], {KEY: frozenset({7})})
        self.win.v_which.set(ContinueTable.DISABLED)
        self.assertEqual(self.win.v_preset.get(), "無効B", "戻すと、その種類で選んでいたもの")
        self.assertEqual(self._style(7)[0], ContinueTableGUI.COLOR_DEFAULT, "もう片方の表（デフォB）の 7 は細い枠で出る")

    def test_imports_go_into_the_selected_presets(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tnl = Path(tmp.name) / "a.tnl"
        tnl.write_text(json.dumps({"data": {KEY: {"5": 1}}}), encoding="utf-8")
        tnnl = Path(tmp.name) / "a.tnnl"
        tnnl.write_text(json.dumps({"targets_dict": {KEY: [2, 3]}}), encoding="utf-8")
        self.win._ask_file = lambda title, pattern: str(tnl if pattern == "*.tnl" else tnnl)
        self.names = ["無効B"]
        self._press("新規")                                 # 続行無効の無効B を選ぶ
        self.win.v_which.set(ContinueTable.DEFAULT)
        self.names = ["デフォB"]
        self._press("新規")
        self.win._import_tnl()
        self.win._import_tnnl()
        store = self._store()
        self.assertEqual(store.default_presets["デフォB"], {KEY: frozenset({5})}, ".tnl → デフォ続行の選んでいるもの")
        self.assertEqual(store.disabled_presets["無効B"], {KEY: frozenset({2, 3})}, ".tnnl → 続行無効の選んでいるもの")
        self.assertEqual(store.default_presets["既定"], {})
        self.assertEqual(store.disabled_presets["既定"], {})
        self.assertIn("デフォB", self.confirms[0], "確認の文にプリセット名が出る")

    def test_closing_writes_the_pending_preset_changes(self):
        self.names = ["B"]
        self._press("新規")
        self.win.destroy()
        self.assertIn("B", ContinueTable.load().disabled_presets)


class TestMainWindow(unittest.TestCase):
    def setUp(self):
        _fresh_store(self)
        self.app = self._app()

    def _app(self):
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            app = mainGUI.App()
        self.addCleanup(app.destroy)
        return app

    def _pick(self, tab, which, label):
        """窓のドロップダウンで label を選んだことにする（hidden な窓には合成イベントが届かないので受け口を呼ぶ）"""
        tab.v_ct_choice[which].set(label)
        tab._on_continue_pick(which)

    def test_the_count_label_counts_presets(self):
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "プリセット 無効 1 個・デフォ 1 個")

    def test_the_global_checkboxes_are_gone(self):
        self.assertFalse(hasattr(self.app, "v_ct_disabled"))
        self.assertFalse(hasattr(self.app, "v_ct_default"))
        frame = next(f for f in _widgets(self.app, ttk.LabelFrame) if f.cget("text") == "続行表")
        self.assertEqual([b.cget("text") for b in _widgets(frame, ttk.Checkbutton)], [])

    def test_every_window_row_has_the_two_dropdowns_listing_presets_and_none(self):
        self.assertGreaterEqual(len(self.app.tabs), 2)
        for tab in self.app.tabs:
            for which in (ContinueTable.DISABLED, ContinueTable.DEFAULT):
                box = tab.cb_ct_choice[which]
                self.assertEqual(list(box["values"]), ["既定", "なし"], tab.idx)
                self.assertEqual(tab.v_ct_choice[which].get(), "既定", "まだ選んでいない窓は既定")
                self.assertEqual(str(box.cget("state")), "readonly")
        labels = [w.cget("text") for w in _widgets(self.app.tabs[0], ttk.Label)]
        self.assertIn("続行無効:", labels)
        self.assertIn("デフォ続行:", labels)

    def test_choosing_none_in_one_window_saves_and_applies_to_that_window_only(self):
        ContinueTable.set_current(ContinueTable.with_preset_body(
            ContinueTable.current(), ContinueTable.DISABLED, "既定", {KEY: [7]}))
        first, second = self.app.tabs[0], self.app.tabs[1]
        self._pick(first, ContinueTable.DISABLED, "なし")
        store = ContinueTable.current()
        self.assertIsNone(ContinueTable.choice_for(store, 1, ContinueTable.DISABLED))
        self.assertEqual(ContinueTable.table_for(store, 1).disabled, {}, "窓 1 は続行無効を重ねない")
        self.assertEqual(ContinueTable.table_for(store, 2).disabled, {KEY: frozenset({7})}, "窓 2 はそのまま")
        self.assertEqual(ContinueTable.apply({KEY: {7}}, ContinueTable.table_for(store, 1)), {KEY: {7}})
        self.assertEqual(ContinueTable.apply({KEY: {7}}, ContinueTable.table_for(store, 2)), {KEY: set()})
        self.assertEqual(second.v_ct_choice[ContinueTable.DISABLED].get(), "既定")
        saved = ContinueTable.load()
        self.assertIsNone(ContinueTable.choice_for(saved, 1, ContinueTable.DISABLED), "保存される")
        self.assertEqual(ContinueTable.choice_for(saved, 2, ContinueTable.DISABLED), "既定")

    def test_choosing_a_preset_per_window(self):
        store = ContinueTable.with_new_preset(ContinueTable.current(), ContinueTable.DEFAULT, "デフォB")
        ContinueTable.set_current(store)
        self.app._refresh_continue_table_count()
        second = self.app.tabs[1]
        self.assertEqual(list(second.cb_ct_choice[ContinueTable.DEFAULT]["values"]), ["既定", "デフォB", "なし"])
        self._pick(second, ContinueTable.DEFAULT, "デフォB")
        self.assertEqual(ContinueTable.choice_for(ContinueTable.current(), 2, ContinueTable.DEFAULT), "デフォB")
        self.assertEqual(ContinueTable.choice_for(ContinueTable.current(), 1, ContinueTable.DEFAULT), "既定")
        self.assertEqual(ContinueTable.choice_for(ContinueTable.load(), 2, ContinueTable.DEFAULT), "デフォB")
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "プリセット 無効 1 個・デフォ 2 個")

    def test_the_choice_survives_a_change_of_the_window_count(self):
        self._pick(self.app.tabs[1], ContinueTable.DISABLED, "なし")
        self.app._rebuild_tabs(3)
        self.assertEqual(self.app.tabs[1].v_ct_choice[ContinueTable.DISABLED].get(), "なし")
        self.assertEqual(self.app.tabs[2].v_ct_choice[ContinueTable.DISABLED].get(), "既定")

    def test_the_editor_window_changes_refresh_the_dropdowns_of_every_window(self):
        self.app._open_continue_table()
        editor = self.app._continue_table_window
        editor._ask_name = lambda title, initial="": "B"
        editor._error = lambda text: None
        editor.new_preset()
        for tab in self.app.tabs:
            self.assertEqual(list(tab.cb_ct_choice[ContinueTable.DISABLED]["values"]), ["既定", "B", "なし"])
        self._pick(self.app.tabs[0], ContinueTable.DISABLED, "B")
        editor._confirm = lambda text: True
        editor.delete_preset()                              # 選んでいる B を消す
        self.assertEqual(self.app.tabs[0].v_ct_choice[ContinueTable.DISABLED].get(), "既定",
                         "消したプリセットを使っていた窓は既定へ")
        self.assertEqual(list(self.app.tabs[0].cb_ct_choice[ContinueTable.DISABLED]["values"]), ["既定", "なし"])
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "プリセット 無効 1 個・デフォ 1 個")
        editor.destroy()

    def test_the_button_opens_one_window_and_a_change_updates_the_count(self):
        self.app._open_continue_table()
        first = self.app._continue_table_window
        self.assertIsInstance(first, ContinueTableGUI.ContinueTableWindow)
        self.app._open_continue_table()
        self.assertIs(self.app._continue_table_window, first, "2 回目は同じ窓")
        first._ask_name = lambda title, initial="": "B"
        first.new_preset()
        first.v_which.set(ContinueTable.DEFAULT)
        first._ask_name = lambda title, initial="": "C"
        first.new_preset()
        first._ask_name = lambda title, initial="": "D"
        first.new_preset()
        self.assertEqual(self.app.lbl_ct_count.cget("text"), "プリセット 無効 2 個・デフォ 3 個")
        first.destroy()
        self.app._open_continue_table()
        self.assertIsNot(self.app._continue_table_window, first, "閉じたら作り直す")

    def test_the_saved_store_is_loaded_at_start_and_shown_per_window(self):
        saved = Store(disabled_presets={"既定": {}, "A": {KEY: frozenset({7})}},
                      default_presets={"既定": {ALT: frozenset({150})}},
                      windows={2: {ContinueTable.DISABLED: "A", ContinueTable.DEFAULT: None}})
        ContinueTable.save(saved)
        app = self._app()
        self.assertEqual(ContinueTable.current(), saved)
        self.assertEqual(app.tabs[0].v_ct_choice[ContinueTable.DISABLED].get(), "既定")
        self.assertEqual(app.tabs[1].v_ct_choice[ContinueTable.DISABLED].get(), "A")
        self.assertEqual(app.tabs[1].v_ct_choice[ContinueTable.DEFAULT].get(), "なし")
        self.assertEqual(app.lbl_ct_count.cget("text"), "プリセット 無効 2 個・デフォ 1 個")

    def test_a_v1_file_with_a_check_off_shows_none_in_every_window(self):
        ContinueTable.table_path().write_text(json.dumps(
            {"version": 1, "use_disabled": False, "use_default": True,
             "disabled": {KEY: [7]}, "default": {}}), encoding="utf-8")
        app = self._app()
        for tab in app.tabs:
            self.assertEqual(tab.v_ct_choice[ContinueTable.DISABLED].get(), "なし", tab.idx)
            self.assertEqual(tab.v_ct_choice[ContinueTable.DEFAULT].get(), "既定", tab.idx)


# ── GM: 「アイテムで切り替え」の欄 ─────────────────────
class TestItemRuleData(unittest.TestCase):
    """item_choices などの画面を作らない部分（本物の item.json を読む）"""

    @classmethod
    def setUpClass(cls):
        cls.items = ItemCatalog.load_items(config.resource_path("assets/data/item.json"))

    def test_the_choices_start_with_not_holding_then_every_item_grouped_by_shop(self):
        choices = ContinueTableGUI.item_choices(self.items)
        self.assertEqual(choices[0], (0, "持っていない"))
        self.assertEqual(sorted(tid for tid, _t in choices[1:]), sorted(self.items))
        texts = [text for _tid, text in choices]
        self.assertEqual(len(texts), len(set(texts)), "文字が重ならない")
        shops = [item.category for item in (self.items[tid] for tid, _t in choices[1:])]
        runs = [shop for n, shop in enumerate(shops) if n == 0 or shops[n - 1] != shop]
        self.assertEqual(len(runs), len(set(runs)), "店ごとにまとまっている")
        by_id = dict(choices)
        self.assertEqual(by_id[70], "Event / Guidance Plush")

    def test_duplicate_texts_get_the_id_added(self):
        items = {1: ItemCatalog.Item("A", "S", True), 2: ItemCatalog.Item("A", "S", False)}
        self.assertEqual(ContinueTableGUI.item_choices(items)[1:], [(1, "S / A"), (2, "S / A (id=2)")])

    def test_an_empty_table_still_offers_not_holding(self):
        self.assertEqual(ContinueTableGUI.item_choices({}), [(0, "持っていない")])

    def test_pick_text_round_trip(self):
        keep, none = ContinueTable.KEEP, None
        self.assertEqual(ContinueTableGUI.pick_text(keep), "窓のまま")
        self.assertEqual(ContinueTableGUI.pick_text(none), "なし")
        self.assertEqual(ContinueTableGUI.pick_text("既定"), "既定")
        for pick in (keep, none, "既定"):
            self.assertIs(ContinueTableGUI.pick_from_text(ContinueTableGUI.pick_text(pick)), pick)


class TestItemRules(_WindowCase):
    """GM: 窓の下の「アイテムで切り替え」。押す代わりに部品の値を変えて受け口を呼ぶ（隠れた窓にはイベントが届かない）"""

    ITEMS = {5: ItemCatalog.Item("Radar", "Enkephalin", True), 70: ItemCatalog.Item("Guidance Plush", "Event", True),
             11: ItemCatalog.Item("Nora Plush", "Survival", True)}

    def setUp(self):
        patcher = patch.object(config, "ITEMS", self.ITEMS)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()
        patch_save = patch.object(ContinueTableGUI, "SAVE_DELAY_MS", 30)
        patch_save.start()
        self.addCleanup(patch_save.stop)
        self.names = []
        self.win._ask_name = lambda title, initial="": self.names.pop(0)
        self.win._confirm = lambda text: True
        self.win._error = lambda text: None

    def _rules(self):
        return ContinueTable.current().item_rules

    def _rows(self):
        return self.win._rule_rows

    def _pick(self, index, item=None, disabled=None, default=None):
        """行 index のドロップダウンを選び直して、選んだときの受け口を呼ぶ"""
        row = self._rows()[index]
        if item is not None:
            row["v_item"].set(item)
        if disabled is not None:
            row[ContinueTable.DISABLED].set(disabled)
        if default is not None:
            row[ContinueTable.DEFAULT].set(default)
        self.win._on_rule_picked(index)

    def _add_preset(self, which, name):
        self.win.v_which.set(which)
        self.names = [name]
        self.win.new_preset()
        self.win.v_which.set(ContinueTable.DISABLED)

    def _saved_rules(self):
        self.win._flush_save()
        return ContinueTable.load().item_rules

    def test_the_section_is_under_the_buttons_with_an_add_button(self):
        frames = [f for f in _widgets(self.win, ttk.LabelFrame) if f.cget("text") == "アイテムで切り替え"]
        self.assertEqual(len(frames), 1)
        texts = {b.cget("text") for b in _widgets(frames[0], ttk.Button)}
        self.assertIn("決まりを足す", texts)
        self.assertIs(frames[0].master, self.win, "窓の直下の段（ボタンの段と並ぶ）")
        packed = [w for w in self.win.pack_slaves() if w.winfo_manager() == "pack"]
        buttons_row = next(w for w in packed if any(b.cget("text").startswith(".tnl")
                                                    for b in _widgets(w, ttk.Button)))
        self.assertGreater(packed.index(buttons_row), packed.index(frames[0]),
                           "side=bottom は先に置いたほうが下: 欄は読み込みのボタンの段の下にある")
        self.assertEqual(self._rows(), [], "初めは決まりが無い")

    def test_adding_makes_a_row_for_not_holding_with_window_as_is_and_saves(self):
        self.win.add_rule()
        self.assertEqual(self._rules(), (ContinueTable.ItemRule(0),))
        row = self._rows()[0]
        self.assertEqual(row["v_item"].get(), "持っていない")
        self.assertEqual(row[ContinueTable.DISABLED].get(), "窓のまま")
        self.assertEqual(row[ContinueTable.DEFAULT].get(), "窓のまま")
        self.assertTrue(self.changed, "メイン画面へ知らせる")
        self.assertEqual(self._saved_rules(), (ContinueTable.ItemRule(0),))

    def test_item_choices_list_not_holding_then_the_items_by_shop(self):
        self.win.add_rule()
        values = list(self._rows()[0]["cb_item"]["values"])
        self.assertEqual(values, ["持っていない", "Enkephalin / Radar", "Event / Guidance Plush", "Survival / Nora Plush"])

    def test_the_preset_dropdowns_offer_keep_none_and_the_preset_names(self):
        self.win.add_rule()
        self._add_preset(ContinueTable.DEFAULT, "足す")
        row = self._rows()[0]
        self.assertEqual(list(row["cb_disabled"]["values"]), ["窓のまま", "なし", "既定"])
        self.assertEqual(list(row["cb_default"]["values"]), ["窓のまま", "なし", "既定", "足す"],
                         "プリセットを作ったら選択肢を作り直す")

    def test_changing_a_row_changes_the_rule_at_once_and_saves_later(self):
        self.win.add_rule()
        self._add_preset(ContinueTable.DEFAULT, "足す")
        self._pick(0, item="Event / Guidance Plush", disabled="なし", default="足す")
        expected = (ContinueTable.ItemRule(70, None, "足す"),)
        self.assertEqual(self._rules(), expected, "判定には押したその場で効く")
        self.assertEqual(ContinueTable.table_for(ContinueTable.current(), 1, 70).default, {})
        self.assertTrue(_wait(self.win, lambda: ContinueTable.table_path().exists()
                              and ContinueTable.load().item_rules == expected), "保存はまとめて")

    def test_picking_keep_again_returns_to_the_window_choice(self):
        self.win.add_rule()
        self._pick(0, disabled="なし")
        self.assertEqual(self._rules()[0].disabled, None)
        self._pick(0, disabled="窓のまま")
        self.assertIs(self._rules()[0].disabled, ContinueTable.KEEP)

    def test_move_up_and_down_reorder_and_the_ends_do_nothing(self):
        for item in ("Enkephalin / Radar", "Event / Guidance Plush", "Survival / Nora Plush"):
            self.win.add_rule()
            self._pick(len(self._rules()) - 1, item=item)
        self.assertEqual([r.item for r in self._rules()], [5, 70, 11])
        self._rows()[2]["↑"].invoke()
        self.assertEqual([r.item for r in self._rules()], [5, 11, 70])
        self._rows()[0]["↓"].invoke()
        self.assertEqual([r.item for r in self._rules()], [11, 5, 70])
        self.changed.clear()
        self._rows()[0]["↑"].invoke()
        self._rows()[2]["↓"].invoke()
        self.assertEqual([r.item for r in self._rules()], [11, 5, 70], "端では動かない")
        self.assertEqual(self.changed, [], "何も変わらないので知らせない")
        self.assertEqual([r.item for r in self._saved_rules()], [11, 5, 70])
        self.assertEqual(self._rows()[0]["v_item"].get(), "Survival / Nora Plush", "行も並べ直す")

    def test_the_cross_deletes_the_row_and_saves(self):
        self.win.add_rule()
        self.win.add_rule()
        self._pick(1, item="Event / Guidance Plush")
        self._rows()[0]["×"].invoke()
        self.assertEqual([r.item for r in self._rules()], [70])
        self.assertEqual(len(self._rows()), 1)
        self.assertEqual([r.item for r in self._saved_rules()], [70])
        self._rows()[0]["×"].invoke()
        self.assertEqual(self._rules(), ())
        self.assertEqual(self._saved_rules(), ())

    def test_a_row_under_the_same_item_is_dimmed(self):
        for _ in range(3):
            self.win.add_rule()
        self._pick(1, item="Event / Guidance Plush")
        dim = ContinueTableGUI.STYLE_RULE_DIM
        styles = [[str(row[k].cget("style")) for k in ("cb_item", "cb_disabled", "cb_default")]
                  for row in self._rows()]
        self.assertEqual(styles[0], ["TCombobox"] * 3)
        self.assertEqual(styles[1], ["TCombobox"] * 3)
        self.assertEqual(styles[2], [dim] * 3, "上に「持っていない」の決まりがある")
        self._pick(2, item="Event / Guidance Plush")
        self.assertEqual(str(self._rows()[2]["cb_item"].cget("style")), dim)
        self._pick(0, item="Enkephalin / Radar")          # 一番上のアイテムを変えても、Plush は 2 行目が先
        self.assertEqual(str(self._rows()[2]["cb_item"].cget("style")), dim, "Plush は 2 行目が先")
        self.assertEqual(str(self._rows()[1]["cb_item"].cget("style")), "TCombobox")
        self._rows()[1]["×"].invoke()
        self.assertEqual(str(self._rows()[1]["cb_item"].cget("style")), "TCombobox", "上が消えれば薄くない")

    def test_a_saved_item_missing_from_the_table_is_shown_by_id(self):
        ContinueTable.set_current(ContinueTable.with_rule_added(ContinueTable.current(), 4321))
        self.win._show_rules()
        row = self._rows()[0]
        self.assertEqual(row["v_item"].get(), "id=4321（表に無い）")
        self.assertIn("id=4321（表に無い）", row["cb_item"]["values"])
        self._pick(0, disabled="なし")
        self.assertEqual(self._rules(), (ContinueTable.ItemRule(4321, None, ContinueTable.KEEP),),
                         "表に無い id のまま残る")

    def test_renaming_a_preset_changes_the_rule_and_the_row(self):
        self._add_preset(ContinueTable.DISABLED, "P")
        self.win.add_rule()
        self._pick(0, disabled="P")
        self.names = ["Q"]
        self.win.rename_preset()
        self.assertEqual(self._rules()[0].disabled, "Q")
        row = self._rows()[0]
        self.assertEqual(row["cb_disabled"].get(), "Q")
        self.assertEqual(list(row["cb_disabled"]["values"]), ["窓のまま", "なし", "既定", "Q"])
        self.assertEqual(self._saved_rules()[0].disabled, "Q")

    def test_deleting_a_preset_makes_the_rule_keep_the_window_choice(self):
        self._add_preset(ContinueTable.DISABLED, "P")
        self.win.add_rule()
        self._pick(0, disabled="P")
        self.win.delete_preset()
        self.assertIs(self._rules()[0].disabled, ContinueTable.KEEP)
        self.assertEqual(self._rows()[0]["cb_disabled"].get(), "窓のまま")
        self.assertNotIn("P", self._rows()[0]["cb_disabled"]["values"])

    def test_a_window_opened_with_saved_rules_shows_them(self):
        store = ContinueTable.with_rule_added(ContinueTable.current(), 70, None, ContinueTable.KEEP)
        ContinueTable.set_current(store)
        win = ContinueTableGUI.ContinueTableWindow(self.root)
        self.addCleanup(win.destroy)
        row = win._rule_rows[0]
        self.assertEqual((row["v_item"].get(), row["disabled"].get(), row["default"].get()),
                         ("Event / Guidance Plush", "なし", "窓のまま"))

    def test_the_rules_affect_the_decision_without_any_real_input(self):
        self._add_preset(ContinueTable.DISABLED, "外す")
        ContinueTable.set_current(ContinueTable.with_preset_body(ContinueTable.current(), ContinueTable.DISABLED,
                                                                 "外す", {KEY: [7]}))
        self.win.add_rule()
        self._pick(0, item="Event / Guidance Plush", disabled="外す")
        store = ContinueTable.current()
        self.assertEqual(ContinueTable.table_for(store, 1, 70).disabled, {KEY: frozenset({7})})
        self.assertEqual(ContinueTable.table_for(store, 1, 0).disabled, {})
