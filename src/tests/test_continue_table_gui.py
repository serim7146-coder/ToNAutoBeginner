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
        self.assertEqual(pitch, 99)

    def test_alternate_falls_back_to_twelve_columns_if_not_all_36_are_there(self):
        blocks = ContinueTableGUI.layout_for(ALT, list(range(134, 169)))
        self.assertEqual(len(blocks), 1)
        kind, cells, pitch = blocks[0]
        self.assertEqual((kind, pitch), ("alternate", 53))
        self.assertTrue(all(col == int(col) for _t, col, _r in cells), "逆三角ではない")
        self.assertEqual(_row_sizes(cells), [11, 12, 12])
        self.assertEqual(_tids(cells)[:3], [149, 150, 141], "順は alternate の順")

    def test_classic_round_has_the_bloodthirsty_three_in_the_variant_row(self):
        blocks, kinds = _blocks(KEY)
        self.assertEqual(kinds, ["classic", "variant"])
        self.assertEqual(blocks["variant"], [(190, 0, 0), (191, 1, 0), (192, 2, 0)], "左寄せの 1 行")

    def test_every_round_with_192_puts_the_three_in_the_variant_row(self):
        for name in ("Ghost", "Punished", "Bloodbath", "Double Trouble", "Bloodbath EX",
                     "Sabotage star", "Sabotage murder"):
            blocks, _kinds = _blocks(_round_key(name))
            self.assertEqual(_tids(blocks["variant"]), [190, 191, 192], name)
            self.assertNotIn(192, _tids(blocks["classic"]) + _tids(blocks.get("alternate", [])), name)

    def test_rounds_without_192_have_no_variant_row(self):
        for name in ("Classic.exe", "Randomizer", "Cracked", "Alternate"):
            self.assertNotIn("variant", _blocks(_round_key(name))[1], name)

    def test_fog_and_midnight_are_classic_then_alternate_then_variant(self):
        for name in ("Fog", "Midnight"):
            blocks, kinds = _blocks(_round_key(name))
            self.assertEqual(kinds, ["classic", "alternate", "variant"], name)
            self.assertEqual(_tids(blocks["variant"]), [190, 191, 192, 315, 316], name)
            self.assertEqual(_row_sizes(blocks["alternate"]), [11, 9, 7, 5, 3, 1], name)
            self.assertEqual(sorted(_tids(blocks["classic"])), list(range(134)), name)

    def test_unbound_is_a_twelve_column_block_in_id_order_with_192_alone_in_the_variant_row(self):
        blocks, kinds = _blocks(UNBOUND)
        self.assertEqual(kinds, ["unbound", "variant"])
        self.assertEqual(_tids(blocks["unbound"]), list(range(200, 284)))
        self.assertEqual(_row_sizes(blocks["unbound"]), [12] * 7)
        self.assertEqual(blocks["variant"], [(192, 0, 0)])

    def test_the_moons_put_all_thirteen_in_the_special_block_without_a_variant_row(self):
        for key in ("Mystic Moon/ミスティックムーン", "Blood Moon/ブラッドムーン"):
            blocks, kinds = _blocks(key)
            self.assertEqual(kinds, ["special"])
            self.assertEqual(_tids(blocks["special"]),
                             [190, 191, 192, 196, 197, 198, 199, 312, 313, 314, 315, 316, 317])

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
        self.assertEqual(len(keys), 18)
        self.assertEqual(len(set(ContinueTableGUI.round_display_name(k) for k in keys)), 18)

    def test_a_round_without_ids_makes_no_blocks(self):
        self.assertEqual(ContinueTableGUI.layout_for(KEY, []), [])


class TestIconLook(unittest.TestCase):
    def test_measured_geometry(self):
        g = ContinueTableGUI
        self.assertEqual((g.CELL_SIZE, g.ICON_SIZE, g.ICON_INSET, g.X_PITCH, g.Y_PITCH, g.ALT_Y_PITCH),
                         (40, 32, 4, 69, 53, 99))
        self.assertEqual(g.CANVAS_WIDTH, 827)
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
        self.assertIsNone(ContinueTableGUI.icon_png(317, 0.3))

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
        self.assertEqual((x1 - x0, y1 - y0), (69, 0), "0 の隣は 1 で横 69px")
        self.assertEqual(self._pos(12)[1] - y0, 53, "次の行は縦 53px")
        left = x0 - 69                                            # 上の行は左端を 1 つ空ける
        self.assertEqual(self._pos(122), (left, y0 + 10 * 53), "下の 2 行は左端から")
        self.assertEqual(self._pos(83), (left, y0 + 11 * 53))

    def test_the_variant_row_is_left_aligned_one_empty_row_below_the_grid(self):
        left, last_row = self._pos(122)[0], self._pos(83)[1]
        self.assertEqual(self._pos(190)[0], left, "バリアントの段は左寄せ")
        self.assertEqual(self._pos(191)[0] - self._pos(190)[0], 69)
        self.assertEqual(self._pos(190)[1] - last_row, 53 + 53, "最後の行の下に 1 行（53px）あけた下")

    def test_fog_blocks_are_classic_alternate_variant_with_a_53px_gap_between(self):
        self._select_round("Fog")
        classic_last = max(self._pos(t)[1] for t in range(134))
        alt = {t: self._pos(t) for t in range(134, 170)}
        alt_top, alt_last = min(y for _x, y in alt.values()), max(y for _x, y in alt.values())
        self.assertEqual(alt_top - classic_last, 53 + 53, "Classic の最後の行の下に 1 行あけて Alternate")
        self.assertEqual(alt_last - alt_top, 5 * 99, "Alternate の縦は 99px")
        variant_top = self._pos(190)[1]
        self.assertEqual(variant_top - alt_last, 53 + 53, "Alternate の最後の行の下に 1 行あけてバリアント")
        self.assertEqual(self._pos(315)[1], variant_top)
        self.assertEqual(self.win._content_height, variant_top + 53 + ContinueTableGUI.TOP_PAD,
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
        self.assertEqual([b - a for a, b in zip(ys, ys[1:])], [99] * 5)
        centre0 = (ordered[0][0][0] + ordered[0][-1][0]) / 2
        for row in ordered:
            xs = [x for x, _t in row]
            self.assertTrue(all(b - a == 69 for a, b in zip(xs, xs[1:])))
            self.assertAlmostEqual((xs[0] + xs[-1]) / 2, centre0, delta=1)
        self.assertEqual(ordered[0][5][1], 169, "1 段目の 6 番目")
        self.assertEqual(self.win._content_height, ContinueTableGUI.TOP_PAD * 2 + 5 * 99 + 53)

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
        self.assertEqual(ContinueTable.current().default, {KEY: frozenset({5, 6})})
        self.assertEqual(ContinueTable.current().disabled, {KEY: frozenset({5})}, "もう片方は変えない")

    def test_each_round_has_its_own_entries(self):
        self.win.toggle(5)
        self._select_round("Alternate")
        self.win.toggle(150)
        self.assertEqual(ContinueTable.current().disabled,
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
