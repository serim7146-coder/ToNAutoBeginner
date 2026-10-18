"""Begin の位置合わせで、VRChat が前面なら視点の高さ（上下）もマウスで合わせる（GA）。
本物の窓・撮影・マウスは使わない: _look_for_begin は偽の「世界」（マウスの合計から BEGIN の縦の位置を出す）、
_FetchMouse.move_rel・WindowOperator.foreground_hwnd・time.sleep は差し替え"""
from tests.support import *  # noqa: F401,F403
import inspect

import ActionExecutor
import BeginAdjustShots
import ItemFetch
import SharedState

AIM = (1000.0, 500.0)
FRONT = 0x100           # この窓の hwnd（前面）
OTHER = 0x999           # ほかの窓が前面
W = 100                 # 文字の幅（許容 = 100 * 0.25 = 25px）
H = 800                 # 窓の高さ（w/H = 0.125 は押せる範囲の中なので前後は動かない）


class _World:
    """マウスの縦の合計 pos から BEGIN の縦のずれを出す偽の視点。
    dy = dy0 - k * pos（k > 0: マウスを下へ動かすと物が上へ動く＝ふつうの向き。k < 0: 逆向き）"""

    def __init__(self, dy0, k):
        self.dy0, self.k, self.pos = dy0, k, 0.0

    def dy(self):
        return self.dy0 - self.k * self.pos


class TestBeginPitch(unittest.TestCase):

    def setUp(self):
        self.logs = []
        self.sent = []              # move_rel に渡した (dx, dy)
        self.steps = []             # 撮り直し（高さ合わせの後）ごとの、それまでに送った分の区切り
        self.stages = []            # _look_for_begin を呼んだ段
        self.moves = []             # self.move（横・前後）
        self.script = []            # 空でなければ _look_for_begin が順に返す（None＝見失った）
        self.world = _World(90, 0.62)
        self.front = FRONT
        self.fg_calls = 0
        cfg = WindowConfig(hwnd=FRONT, osc_port=9000)
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=5, round_seq=1)
        self.ex = ActionExecutor.ActionExecutor(cfg, self.st, lambda: True, self.logs.append)
        self.gain = (0.59, 0.59)
        self.stopped = lambda: False
        self.sleeps = []
        self.set_gain = MagicMock()
        for p in (patch.object(BeginDetect, "available", return_value=True),
                  patch.object(self.ex, "_look_for_begin", side_effect=self._look),
                  patch.object(self.ex, "_adjust_stopped", side_effect=lambda _seq: self.stopped()),
                  patch.object(self.ex, "move", side_effect=lambda d, s: self.moves.append((d, round(s, 3)))),
                  patch.object(ActionExecutor.ActionExecutor, "_saved_fetch_gain",
                               side_effect=lambda: self.gain),
                  patch.object(SharedState, "set_item_fetch_gain", self.set_gain),
                  patch.object(ActionExecutor._FetchMouse, "move_rel", side_effect=self._move_rel),
                  patch.object(WindowOperator, "foreground_hwnd", side_effect=self._foreground),
                  patch.object(ActionExecutor.time, "sleep", side_effect=self.sleeps.append)):
            p.start()
            self.addCleanup(p.stop)

    # ── 偽物 ──
    def _foreground(self):
        self.fg_calls += 1
        return self.front

    def _move_rel(self, dx, dy):
        self.sent.append((dx, dy))
        self.world.pos += dy

    def _hit(self, dy, dx=0.0):
        return {"score": 0.9, "dark": 0.9, "cx": AIM[0] + dx, "cy": AIM[1] + dy, "w": W, "h": 20, "H": H}

    def _look(self, stage=""):
        self.stages.append(stage)
        if stage == "高さ合わせ":
            self.steps.append(len(self.sent))
        if self.script:
            item = self.script.pop(0)
            return (None, AIM) if item is None else (item, AIM)
        return self._hit(self.world.dy()), AIM

    def _pitch_logs(self):
        return [m for m in self.logs if "高さ合わせ" in m]

    # ── 前面・保存した感度 ──
    def test_front_with_a_saved_gain_levels_the_view(self):
        """縦 +90px・保存の感度 0.59 → マウス +153（STEP ずつに分けて送る）→ 撮り直しで -5px → 済み。横は今どおり"""
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(sum(dy for _dx, dy in self.sent), 153)
        self.assertTrue(all(dx == 0 for dx, _dy in self.sent), "横のマウスは動かさない")
        self.assertTrue(all(abs(dy) <= ItemFetch.STEP for _dx, dy in self.sent), "STEP ずつに分ける")
        self.assertEqual(len(self.sent), 26)           # 153 / 6 = 25.5
        self.assertEqual(self.sleeps.count(ItemFetch.STEP_SEC), 26, "1 回送るごとに STEP_SEC 待つ")
        self.assertIn(config.BEGIN_ADJUST_SETTLE_SEC, self.sleeps, "動かしたら待ってから撮り直す")
        self.assertEqual(self.stages, ["最初", "高さ合わせ"])
        self.assertIn("Begin: 高さ合わせ（照準から縦 +90px・感度 保存値 0.59）→ マウス +153", self.logs)
        self.assertIn("Begin: 高さ合わせ済み（縦 -5px）", self.logs)
        self.assertEqual(self.moves, [], "横・前後は動かさない")
        self.assertFalse(any("前面のときだけ" in m for m in self.logs))

    def test_it_is_not_saved_and_not_put_back(self):
        self.ex._adjust_to_begin(1)
        self.set_gain.assert_not_called()
        self.assertEqual(self.ex._pending_view_dy, 0, "戻さない・戻す分にも足さない")
        self.assertEqual(self.world.pos, 153, "視点は動かしたまま")

    def test_it_checks_the_front_before_every_move(self):
        """最初の判定のほかに、動かす直前にも毎回確かめる（2 歩で収まる世界: 判定は 1 + 2 回）"""
        self.world = _World(80, 0.3)        # 136 × 0.3 = 41 → 39px 残り → 2 歩目で収まる
        self.ex._adjust_to_begin(1)
        self.assertEqual(self.fg_calls, 1 + 2)
        self.assertEqual(len(self._pitch_logs()), 3, self._pitch_logs())     # 2 回動かす + 済み

    def test_losing_the_front_between_steps_stops(self):
        self.world = _World(80, 0.3)
        seq = iter([FRONT, FRONT, OTHER])
        with patch.object(WindowOperator, "foreground_hwnd", side_effect=lambda: next(seq)):
            self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(sum(dy for _dx, dy in self.sent), 136, "1 歩目だけ動いた")
        self.assertIn("Begin: 高さ合わせ: 前面でなくなった → やめる", self.logs)

    # ── 前面でない・許容内 ──
    def test_not_in_front_does_not_move(self):
        self.front = OTHER
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.stages, ["最初"])
        self.assertIn("Begin: 高さ合わせは前面のときだけ（縦 +90px のまま）", self.logs)

    def test_not_in_front_but_within_the_tolerance_says_nothing(self):
        self.front = OTHER
        self.world = _World(20, 0.62)
        self.ex._adjust_to_begin(1)
        self.assertEqual(self.sent, [])
        self.assertEqual(self._pitch_logs(), [])

    def test_within_the_tolerance_does_nothing(self):
        """|dy| <= 0.25 × 幅（25px）: 動かさない・ログなし・前面も見ない"""
        for dy in (25, -25, 0):
            self.logs.clear()
            self.world = _World(dy, 0.62)
            self.assertEqual(self.ex._adjust_to_begin(1), "aimed", dy)
            self.assertEqual(self.sent, [], dy)
            self.assertEqual(self._pitch_logs(), [], dy)
        self.assertEqual(self.fg_calls, 0)

    def test_just_over_the_tolerance_moves(self):
        self.world = _World(26, 0.62)
        self.ex._adjust_to_begin(1)
        self.assertEqual(sum(dy for _dx, dy in self.sent), 44)      # 26 / 0.59 = 44.07

    def test_it_moves_up_when_begin_is_above(self):
        self.world = _World(-90, 0.62)
        self.ex._adjust_to_begin(1)
        self.assertEqual(sum(dy for _dx, dy in self.sent), -153)
        self.assertIn("Begin: 高さ合わせ（照準から縦 -90px・感度 保存値 0.59）→ マウス -153", self.logs)

    # ── 感度が無い ──
    def test_without_a_gain_the_first_step_measures_it(self):
        """1 歩目は 24 単位 → 24 × 0.5 = 12px 動いた → 感度 0.5 → 2 歩目で残りの 48px（96 単位）→ 合う"""
        self.gain = None
        self.world = _World(60, 0.5)
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual([sum(d for _x, d in self.sent[:4]), sum(d for _x, d in self.sent)], [24, 120])
        self.assertIn("Begin: 高さ合わせ（照準から縦 +60px・感度 測る）→ マウス +24", self.logs)
        self.assertIn("Begin: 高さ合わせ（照準から縦 +48px・感度 測定値 0.50）→ マウス +96", self.logs)
        self.assertIn("Begin: 高さ合わせ済み（縦 +0px）", self.logs)
        self.set_gain.assert_not_called()

    def test_without_a_gain_the_direction_is_measured_too(self):
        """マウスを下へ動かすと物が下へ動く世界（k < 0）: 1 歩目で分かり、2 歩目は逆（上）へ動かす"""
        self.gain = None
        self.world = _World(60, -0.5)
        self.ex._adjust_to_begin(1)
        self.assertEqual(sum(d for _x, d in self.sent[:4]), 24)
        self.assertEqual(sum(d for _x, d in self.sent), 24 - 144, "2 歩目は -144（72 / 0.5 を逆向き）")
        self.assertIn("Begin: 高さ合わせ済み（縦 +0px）", self.logs)

    def test_a_measured_gain_outside_the_sane_range_gives_up(self):
        self.gain = None
        self.script = [self._hit(60), self._hit(60 - 24 * 50)]       # 24 単位で 1200px 動いた（感度 50）
        self.ex._adjust_to_begin(1)
        self.assertEqual(len(self.steps), 1)
        self.assertEqual(sum(d for _x, d in self.sent), 24, "やめる")
        self.assertTrue(any("感度が測れない" in m for m in self.logs), self.logs)

    def test_a_saved_gain_with_the_wrong_direction_flips_once(self):
        """保存の感度 0.5 で向きが逆（k = -0.5）: 1 歩目で |dy| が増える → 2 歩目から符号を反転して合う"""
        self.gain = (0.5, 0.5)
        self.world = _World(60, -0.5)
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        per_step = []
        last = 0
        for end in self.steps:
            per_step.append(sum(d for _x, d in self.sent[last:end]))
            last = end
        self.assertEqual(per_step, [120, -160, -80])
        self.assertTrue(any("向きが逆 → 反転" in m for m in self.logs), self.logs)
        self.assertIn("Begin: 高さ合わせ済み（縦 +0px）", self.logs)

    def test_still_the_wrong_direction_after_the_flip_gives_up(self):
        self.gain = (0.5, 0.5)
        self.script = [self._hit(60), self._hit(90), self._hit(120)]     # 動かすたびに遠ざかる
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(len(self.steps), 2, "2 歩で諦める")
        self.assertTrue(any("向きが合わない → やめる" in m for m in self.logs), self.logs)
        self.assertEqual(self.script, [], "撮ったのは 3 枚（最初・1 歩目・2 歩目）")

    # ── 動いていない・見失った ──
    def test_not_moving_gives_up(self):
        self.world = _World(120, 0.0)
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(len(self.steps), 1)
        self.assertIn("Begin: 高さ合わせ: 動いていない → やめる", self.logs)

    def test_losing_it_goes_to_the_existing_retake(self):
        """高さ合わせの後に見失った → 撮り直し（見つかれば続ける）。高さ合わせはもう一度できる"""
        self.script = [self._hit(120), None, self._hit(10)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.stages, ["最初", "高さ合わせ", "撮り直し"])
        self.assertFalse(any("探し直します" in m for m in self.logs))

    def test_losing_it_twice_searches_again(self):
        self.script = [self._hit(120), None, None]
        found = (self._hit(5), AIM)
        with patch.object(self.ex, "_search_for_begin", return_value=found) as search:
            self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        search.assert_called_once()
        self.assertIn("Begin: BEGIN を見失いました → 探し直します", self.logs)

    def test_losing_it_and_not_finding_it_again_is_not_found(self):
        self.script = [self._hit(120), None, None]
        with patch.object(self.ex, "_search_for_begin", return_value=None):
            self.assertEqual(self.ex._adjust_to_begin(1), "not_found")

    def test_losing_it_after_every_pitch_ends(self):
        """高さ合わせのたびに見失い、撮り直しでは見つかる、を繰り返しても終わる（高さ合わせは 2 回まで）"""
        self.script = [self._hit(120), None, self._hit(120), None, self._hit(120), None, self._hit(120)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertLessEqual(self.stages.count("高さ合わせ"), 2)

    # ── 上限 ──
    def test_one_move_is_capped_at_move_cap(self):
        self.world = _World(1000, 0.62)
        self.ex._adjust_to_begin(1)
        first = sum(d for _x, d in self.sent[:self.steps[0]])
        self.assertEqual(first, ItemFetch.MOVE_CAP)
        self.assertTrue(any("→ マウス +160" in m for m in self.logs), self.logs)

    def test_at_most_three_steps(self):
        self.world = _World(1000, 0.1)          # 160 × 0.1 = 16px ずつしか寄らない
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(len(self.steps), config.BEGIN_PITCH_MAX_STEPS)
        self.assertEqual(sum(d for _x, d in self.sent), 3 * ItemFetch.MOVE_CAP)
        self.assertTrue(any("3歩で収まらない" in m for m in self.logs), self.logs)

    # ── 止める ──
    def test_stopped_before_the_move_does_not_move(self):
        """最初の撮影の後（動かす前）に止まった（受理・ラウンド開始・停止）→ 動かさない"""
        self.stopped = lambda: "最初" in self.stages
        self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.assertEqual(self.sent, [])

    def test_stopped_after_the_look_leaves_at_once(self):
        self.stopped = lambda: "高さ合わせ" in self.stages
        self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.assertEqual(len(self.steps), 1)
        self.assertEqual(self.moves, [])

    # ── 横・前後との組み合わせ ──
    def test_x_off_after_the_pitch_is_adjusted_once_more(self):
        """高さ合わせの後に横がずれていたら、横を 1 歩寄せる。高さ合わせはやり直さない"""
        self.script = [self._hit(120), self._hit(5, dx=80), self._hit(5, dx=2)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("right", 0.1)])
        self.assertEqual(self.stages, ["最初", "高さ合わせ", "動いた後"])
        self.assertEqual(self.steps, [self.steps[0]], "高さ合わせは 1 回だけ")

    def test_pitch_only_after_x_and_depth_are_done(self):
        """横がずれているうちは高さを見ない（横の後に見る）"""
        self.script = [self._hit(90, dx=80), self._hit(90, dx=2)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("right", 0.1)])
        self.assertEqual(self.stages[:2], ["最初", "動いた後"])
        self.assertEqual(sum(d for _x, d in self.sent), 153)

    # ── 戻り値・設定・文書 ──
    def test_the_look_returns_the_aim_point(self):
        """_look_for_begin の戻り値は (文字 or None, 照準の (x, y))"""
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=FRONT, osc_port=9000),
                                           WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=3),
                                           lambda: True, lambda _m: None)
        found = {"score": 0.9, "dark": 0.9, "cx": 10.0, "cy": 20.0, "w": 5, "h": 2}
        with patch.object(WindowOperator, "aim_in_window_image", return_value=(426.0, 330.0)), \
             patch.object(ScreenCapture, "capture_window", return_value=(b"\0" * 16, 2, 800)), \
             patch.object(BeginDetect, "find", return_value=found), \
             patch.object(BeginAdjustShots, "save"):
            hit, aim = ex._look_for_begin("最初")
        self.assertEqual(aim, (426.0, 330.0))
        self.assertEqual(hit["H"], 800)

    def test_the_values(self):
        self.assertEqual((config.BEGIN_PITCH_TOL, config.BEGIN_PITCH_PROBE_UNITS, config.BEGIN_PITCH_MAX_STEPS),
                         (0.25, 24, 3))

    def test_the_pitch_is_not_added_to_the_pending_view(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor._pitch_to_begin)
        self.assertNotIn("_pending_view_dy +=", src)
        self.assertNotIn("set_item_fetch_gain", src)


if __name__ == "__main__":
    unittest.main()
