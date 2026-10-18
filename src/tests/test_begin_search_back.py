"""Begin を探す動きの「戻し」（FM）。前へ動いた量を速さの受信から測り、戻すのは動いた分だけ。
壁（台座など）で動けなければ前へ探すのをやめ、代わりに後ろへ BEGIN_SEARCH_BLOCKED_BACK_SEC 秒動いて探す。

撮影・移動・速さの受信・時計はすべて偽物（本物の OSC・キー・マウスは送らない。本物の時計では待たない）"""
from tests.support import *  # noqa: F401,F403

_RealSampler = ActionExecutor.MotionSampler     # 偽物に差し替える前の本物（distance_so_far の確認用）

BACK = "/input/MoveBackward"


class _World:
    """偽の世界。時計と、動いた距離（速さの積分の代わり）。後ろへ押している間は back_speed で距離が増える"""

    def __init__(self):
        self.t = 0.0
        self.dist = 0.0
        self.heard = True           # 速さを受け取れているか（False なら distance_so_far は None）
        self.pressing = False       # OSC で後ろへ押している最中か
        self.back_speed = 5.0       # 後ろへ押している間の速さ（毎秒の距離）

    def clock(self):
        return self.t

    def sleep(self, sec):
        self.t += sec
        if self.pressing:
            self.dist += self.back_speed * sec


class _FakeSampler:
    """MotionSampler の偽物（スレッドを立てない）。つくられた数・離したかを残す"""
    world = None
    made = []

    def __init__(self, receiver, write, *a, **k):
        self.receiver = receiver
        self.released = False
        _FakeSampler.made.append(self)

    def start(self):
        pass

    def release(self):
        self.released = True

    def distance_so_far(self):
        return _FakeSampler.world.dist if _FakeSampler.world.heard else None


class _FakeOsc:
    def __init__(self, world):
        self.world = world
        self.sent = []
        self.stop_all_calls = 0

    def send(self, address, value):
        self.sent.append((address, value))
        self.world.pressing = (address == BACK and value == 1)
        return True

    def stop_all(self, repeat=2):
        self.stop_all_calls += 1
        self.world.pressing = False


class TestBeginSearchBack(unittest.TestCase):
    """_search_for_begin（撮影は BeginDetect の結果を順に返す偽物。窓の高さ 1000px）"""

    AIM = (500.0, 400.0)
    H = 1000

    def setUp(self):
        self.moves = []
        self.logs = []
        self.hits = []
        self.pushes = []            # 前へ 0.3 秒押すたびに増える距離（足りなければ最後の値）
        self.gain = {}              # (向き, 秒) → その移動で増える距離（なければ 0.25）
        self.world = _World()
        _FakeSampler.world = self.world
        _FakeSampler.made = []
        self.osc = _FakeOsc(self.world)
        cfg = WindowConfig(hwnd=0x100, osc_port=9000)
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=5, round_seq=1)
        self.ex = ActionExecutor.ActionExecutor(cfg, self.st, lambda: True, self.logs.append)
        self.ex._osc = self.osc
        self.ex._receiver = object()            # 速さを受け取れる窓（読むのは偽の MotionSampler）
        self.pressed = []                       # _press_back_distance の結果
        original = ActionExecutor.ActionExecutor._press_back_distance

        def press_back(target, cap, distance):
            result = original(self.ex, target, cap, distance, clock=self.world.clock, sleep=self.world.sleep)
            self.pressed.append(result)
            return result

        for p in (patch.object(BeginDetect, "available", return_value=True),
                  patch.object(BeginDetect, "find", side_effect=self._find),
                  patch.object(WindowOperator, "aim_in_window_image", return_value=self.AIM),
                  patch.object(ScreenCapture, "capture_window", return_value=(b"\0" * 16, 2, self.H)),
                  patch.object(ActionExecutor, "MotionSampler", _FakeSampler),
                  patch.object(ActionExecutor.time, "sleep"),
                  patch.object(self.ex, "move", side_effect=self._move),
                  patch.object(self.ex, "_press_back_distance", side_effect=press_back)):
            p.start()
            self.addCleanup(p.stop)

    def _find(self, _bits, _w, _h):
        """hits は (横ずれ（文字幅の何倍）, w/H) か None"""
        if not self.hits:
            return None
        item = self.hits.pop(0)
        if item is None:
            return None
        dx_w, ratio = item
        w = ratio * self.H
        return {"score": 0.9, "dark": 0.9, "cx": self.AIM[0] + dx_w * w, "cy": 480.0, "w": w, "h": 20}

    def _move(self, direction, sec):
        self.moves.append((direction, round(sec, 3)))
        if direction == "forward" and sec == config.BEGIN_SEARCH_FORWARD_SEC and self.pushes:
            self.world.dist += self.pushes.pop(0) if len(self.pushes) > 1 else self.pushes[0]
        else:
            self.world.dist += self.gain.get((direction, round(sec, 3)), 0.25)

    def _press_direct(self, target, cap, distance, clock=None, sleep=None):
        """差し替える前の _press_back_distance を直接呼ぶ"""
        return ActionExecutor.ActionExecutor._press_back_distance(
            self.ex, target, cap, distance, clock=clock, sleep=sleep)

    def _search(self):
        return self.ex._search_for_begin(1)

    def _measure_log(self):
        lines = [m for m in self.logs if "探す動きの実測" in m]
        self.assertEqual(len(lines), 1, self.logs)
        return lines[0]

    def _accept_on_move(self, nth):
        count = {"n": 0}
        inner = self.ex.move.side_effect

        def move(direction, sec):
            inner(direction, sec)
            count["n"] += 1
            if count["n"] == nth:
                self.st.begin_done = True
        self.ex.move.side_effect = move

    # ── 動いたなら、動いた距離だけ戻す ───────────────────────────
    def test_it_goes_back_by_the_measured_distance_not_the_seconds(self):
        """前へ 4 回とも 1.0 動いた（合計 4.0）。後ろへは 5/秒で 4.0 動くまで＝0.8 秒で離す（押した秒数 1.2 ではない）"""
        self.pushes = [1.0]
        self.assertIsNone(self._search())
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15)] + [("forward", 0.3)] * 4,
                         "後ろへの戻しは move ではなく距離で止める押し方")
        self.assertEqual(len(self.pressed), 1)
        seconds, moved = self.pressed[0]
        self.assertAlmostEqual(seconds, 0.8, delta=0.06)
        self.assertGreaterEqual(moved, 4.0)
        self.assertLess(moved, 4.0 + 5.0 * 0.06)
        self.assertTrue(any("4.00 だけ後ろへ戻します" in m for m in self.logs), self.logs)

    def test_the_return_presses_back_and_releases(self):
        self.pushes = [1.0]
        self._search()
        self.assertEqual(self.osc.sent[0], (BACK, 0), "0→1 の変化で反応する入力があるため、先に 0")
        self.assertEqual(self.osc.sent[1], (BACK, 1))
        self.assertEqual(self.osc.sent[-1], (BACK, 0), "必ず離す")
        self.assertGreaterEqual(self.osc.stop_all_calls, 1)
        self.assertFalse(self.world.pressing)

    def test_the_return_is_capped_by_the_pushed_seconds(self):
        """後ろへ押しても距離が増えない（遅い）ときは、前へ押した秒数 1.2 で離す"""
        self.pushes = [1.0]
        self.world.back_speed = 0.1
        self._search()
        seconds, moved = self.pressed[0]
        self.assertAlmostEqual(seconds, 1.2, delta=0.06)
        self.assertLess(moved, 4.0)

    def test_the_return_resends_the_press(self):
        """押している間は 1 を送り直す（途中で入力が消えても戻る）"""
        self.pushes = [1.0]
        self.world.back_speed = 0.1
        self._search()
        self.assertGreater(self.osc.sent.count((BACK, 1)), 2)

    def test_it_logs_the_measured_numbers(self):
        self.pushes = [1.0]
        self._search()
        line = self._measure_log()
        for part in ("前へ 4 回", "押した 1.20秒", "動いた 4.00", "動けなかった 0 回", "後ろへ戻した"):
            self.assertIn(part, line)

    def test_the_sampler_is_made_and_released(self):
        self.pushes = [1.0]
        self._search()
        self.assertEqual(len(_FakeSampler.made), 1)
        self.assertTrue(_FakeSampler.made[0].released)

    def test_found_while_pushing_forward_does_not_go_back(self):
        self.pushes = [1.0]
        self.hits = [None, None, None, (0.0, 0.07)]     # 後ろ 0.15 の後・前へ 1 回目・2 回目・3 回目
        hit, _aim = self._search()
        self.assertAlmostEqual(hit["w"], 0.07 * self.H)
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15)] + [("forward", 0.3)] * 3)
        self.assertEqual(self.pressed, [])
        self.assertTrue(_FakeSampler.made[0].released)
        self.assertIn("前へ 3 回", self._measure_log())

    # ── 前へ動けない（壁）→ 後ろへ 0.3 秒 ───────────────────────────
    def test_a_wall_on_the_first_push_stops_forward_and_goes_back_for_a_fixed_time(self):
        """報告者 05:41: 前へ押しても 0。残りの前を押さず、後ろへ 0.3 秒動いて見る。無ければあきらめる（戻さない）"""
        self.pushes = [0.0]
        self.gain[("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC)] = 3.5
        self.assertIsNone(self._search())
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15), ("forward", 0.3),
                                      ("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC)])
        self.assertEqual(self.pressed, [], "さらに戻さない")
        self.assertEqual(self.osc.sent, [])
        self.assertTrue(any("前へ動けない" in m and "後ろへ 0.30秒動いて探します" in m for m in self.logs), self.logs)
        self.assertTrue(any("あきらめます（さらに戻しません）" in m for m in self.logs), self.logs)
        line = self._measure_log()
        self.assertIn("前へ 1 回", line)
        self.assertIn("動けなかった 1 回", line)
        self.assertIn("壁で後ろへ 0.30秒動いた 3.50", line)

    def test_after_the_wall_it_looks_once_more_and_returns_what_it_sees(self):
        self.pushes = [0.0]
        self.hits = [None, None, (0.0, 0.12)]       # 後ろ 0.15・前へ 1 回目（壁）・後ろへ 0.3 秒の後に見つかる
        hit, aim = self._search()
        self.assertAlmostEqual(hit["w"], 0.12 * self.H)
        self.assertEqual(aim, self.AIM)
        self.assertEqual(self.moves[-1], ("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC))
        self.assertEqual(self.pressed, [])
        self.assertFalse(any("あきらめ" in m for m in self.logs), self.logs)

    def test_a_wall_on_the_third_push_does_not_return_by_the_distance(self):
        """2 回動いて 3 回目が壁 → 3 回目で前をやめ、後ろへ 0.3 秒。動いた 3.0 ぶんの戻しはしない（壁の後ろへで足りる）"""
        self.pushes = [1.5, 1.5, 0.0]
        self.assertIsNone(self._search())
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15)] + [("forward", 0.3)] * 3
                         + [("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC)])
        self.assertEqual(self.pressed, [])
        line = self._measure_log()
        self.assertIn("前へ 3 回", line)
        self.assertIn("動いた 3.00", line)
        self.assertIn("動けなかった 1 回", line)

    def test_slightly_below_the_wall_distance_is_a_wall_and_exactly_it_is_not(self):
        with patch.object(config, "BEGIN_SEARCH_BLOCKED_DIST", 0.5):
            self.pushes = [0.25]
            self._search()
            self.assertEqual(self.moves[-1], ("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC))
            self.assertEqual(self.moves.count(("forward", 0.3)), 1)
            self.moves.clear()
            self.logs.clear()
            self.pushes = [0.5]
            self._search()
            self.assertEqual(self.moves.count(("forward", 0.3)), config.BEGIN_SEARCH_FORWARD_TRIES,
                             "ちょうどなら壁ではない（前を押し切る）")
            self.assertNotIn(("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC), self.moves)

    def test_when_the_speed_stops_arriving_midway_it_is_treated_as_a_wall(self):
        """途中で速さが届かなくなった（距離が増えない）→ 壁と区別がつかない。押しても同じなので同じに扱い、ログで分かる"""
        self.pushes = [1.5, 0.0]
        self._search()
        self.assertEqual(self.moves[-1], ("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC))
        self.assertTrue(any("壁か、速さが届いていない" in m for m in self.logs), self.logs)

    # ── 戻さない下限 ───────────────────────────────────────────
    def test_it_does_not_return_when_it_barely_moved_forward(self):
        with patch.object(config, "BEGIN_SEARCH_FORWARD_TRIES", 1), \
                patch.object(config, "BEGIN_SEARCH_BLOCKED_DIST", 0.25):
            self.pushes = [0.25]
            self.assertIsNone(self._search())
        self.assertEqual(self.pressed, [])
        self.assertEqual(self.osc.sent, [])
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15), ("forward", 0.3)])
        self.assertTrue(any("戻しません" in m and "0.25" in m for m in self.logs), self.logs)

    def test_it_returns_when_the_forward_total_is_exactly_the_minimum(self):
        with patch.object(config, "BEGIN_SEARCH_FORWARD_TRIES", 1), \
                patch.object(config, "BEGIN_SEARCH_BLOCKED_DIST", 0.25), \
                patch.object(config, "BEGIN_SEARCH_RETURN_MIN", 0.5):
            self.pushes = [0.5]
            self._search()
        self.assertEqual(len(self.pressed), 1, "下限ちょうどは戻す")

    # ── 速さを受け取れない窓は今までどおり ───────────────────────────
    def test_without_a_receiver_it_goes_back_by_the_pushed_seconds_as_before(self):
        self.ex._receiver = None
        self.assertIsNone(self._search())
        self.assertEqual(self.moves[-1], ("back", 1.2))
        net = sum(s if d == "forward" else -s for d, s in self.moves)
        self.assertAlmostEqual(net, 0.0, msg="元の位置へ戻る")
        self.assertEqual(_FakeSampler.made, [], "受信が無ければ読まない")
        self.assertEqual(self.osc.sent, [])
        self.assertEqual(self.moves.count(("forward", 0.3)), 4, "壁の判断はしない（4 回とも押す）")
        self.assertIn("測れず", self._measure_log())

    def test_a_receiver_that_never_heard_anything_goes_back_by_seconds_too(self):
        """受信器はあるが 1 件も届いていない → 動いた量は測れない。壁とは見ず、秒数で戻す"""
        self.world.heard = False
        self.assertIsNone(self._search())
        self.assertEqual(self.moves[-1], ("back", 1.2))
        self.assertEqual(self.moves.count(("forward", 0.3)), 4)
        self.assertEqual(self.pressed, [])
        self.assertTrue(_FakeSampler.made[0].released)

    def test_without_the_osc_client_it_does_not_read_the_speed(self):
        self.ex._osc = None
        self.assertIsNone(self._search())
        self.assertEqual(_FakeSampler.made, [])
        self.assertEqual(self.moves[-1], ("back", 1.2))

    # ── 受理・開始・停止 ───────────────────────────────────────
    def test_an_acceptance_while_pushing_forward_stops_without_going_back(self):
        self.pushes = [1.0]
        self._accept_on_move(4)             # back, forward(戻し), forward, forward ← ここで受理
        self.assertEqual(self._search(), "skip")
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15), ("forward", 0.3), ("forward", 0.3)])
        self.assertEqual(self.pressed, [])
        self.assertTrue(_FakeSampler.made[0].released)

    def test_an_acceptance_after_the_last_push_does_not_go_back(self):
        self.pushes = [1.0]
        self._accept_on_move(6)
        self.assertEqual(self._search(), "skip")
        self.assertEqual(self.pressed, [])
        self.assertEqual(self.osc.sent, [])

    def test_an_acceptance_on_the_wall_push_does_not_go_back_for_the_fixed_time(self):
        self.pushes = [0.0]
        self._accept_on_move(3)             # back, forward(戻し), forward ← 壁の押しで受理
        self.assertEqual(self._search(), "skip")
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15), ("forward", 0.3)])

    def test_an_acceptance_while_going_back_for_the_fixed_time_is_a_skip(self):
        self.pushes = [0.0]
        self._accept_on_move(4)             # 壁の後ろへ 0.3 秒の間に受理
        self.assertEqual(self._search(), "skip")
        self.assertEqual(self.moves[-1], ("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC))
        self.assertFalse(any("あきらめ" in m for m in self.logs), self.logs)

    def test_an_acceptance_just_before_the_fixed_back_move_does_not_move(self):
        """壁と見た行を書く間に受理が来た → 移動の直前にもう一度見て、後ろへ動かない"""
        self.pushes = [0.0]
        write = self.ex._log

        def log(message):
            write(message)
            if "前へ動けない" in message:
                self.st.begin_done = True
        self.ex._log = log
        self.assertEqual(self._search(), "skip")
        self.assertNotIn(("back", config.BEGIN_SEARCH_BLOCKED_BACK_SEC), self.moves)

    def test_the_sampler_is_released_even_if_the_move_raises(self):
        def boom(direction, sec):
            if direction == "forward" and sec == 0.3:
                raise RuntimeError("x")
        self.ex.move.side_effect = boom
        with self.assertRaises(RuntimeError):
            self._search()
        self.assertTrue(_FakeSampler.made[0].released)

    # ── 距離で戻す押し方 ───────────────────────────────────────
    def test_press_back_stops_at_once_when_the_macro_is_stopped(self):
        with patch.object(self.ex, "_move_stopped", return_value=True):
            seconds, _moved = self._press_direct(
                4.0, 1.2, lambda: self.world.dist, clock=self.world.clock, sleep=self.world.sleep)
        self.assertLess(seconds, 0.1)
        self.assertEqual(self.osc.sent[-1], (BACK, 0))

    def test_press_back_sends_nothing_when_not_private(self):
        self.st.instance_type = config.INSTANCE_PUBLIC
        result = self._press_direct(
            4.0, 1.2, lambda: self.world.dist, clock=self.world.clock, sleep=self.world.sleep)
        self.assertEqual(result, (0.0, 0.0))
        self.assertEqual(self.osc.sent, [])

    def test_press_back_releases_even_if_reading_the_distance_raises(self):
        calls = {"n": 0}

        def distance():
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("x")
            return 0.0
        with self.assertRaises(RuntimeError):
            self._press_direct(4.0, 1.2, distance, clock=self.world.clock, sleep=self.world.sleep)
        self.assertEqual(self.osc.sent[-1], (BACK, 0))

    def test_the_config_values_are_used_and_ordered(self):
        self.assertLess(config.BEGIN_SEARCH_BLOCKED_DIST, config.BEGIN_SEARCH_RETURN_MIN)
        self.assertGreater(config.BEGIN_SEARCH_BLOCKED_BACK_SEC, 0)
        self.assertEqual(config.BEGIN_SEARCH_BLOCKED_BACK_SEC, 0.3)


class TestMotionSamplerDistance(unittest.TestCase):
    """MotionSampler.distance_so_far: Begin 前の移動の「実測 距離」と同じ測り方で、今までの距離を返す"""

    def _sampler(self, samples, now):
        sampler = _RealSampler(None, lambda m: None, clock=lambda: now, interval=0.05)
        sampler._started = 0.0
        sampler.samples = samples
        return sampler

    def test_none_before_any_speed_is_heard(self):
        self.assertIsNone(self._sampler([], 1.0).distance_so_far())
        self.assertIsNone(self._sampler([(0.0, None, None), (0.05, None, None)], 1.0).distance_so_far())

    def test_it_is_the_same_integral_as_the_measured_line(self):
        samples = [(0.0, 0.0, None), (0.05, 5.0, None), (0.10, 6.0, None), (0.15, 4.0, None), (0.20, 0.0, None)]
        distance = self._sampler(samples, 0.20).distance_so_far()
        line = ActionExecutor.summarize_motion(samples, 0.20)
        self.assertIn(f"距離 {distance:.2f}", line)
        self.assertAlmostEqual(distance, 5.0 * 0.05 + 6.0 * 0.05 + 4.0 * 0.05)

    def test_it_counts_the_time_since_the_last_read_at_the_last_speed(self):
        samples = [(0.0, 0.0, None), (0.05, 5.0, None), (0.10, 5.0, None)]
        self.assertAlmostEqual(self._sampler(samples, 0.12).distance_so_far(), 0.25 + 5.0 * 0.02)

    def test_it_does_not_keep_growing_if_the_reading_stopped(self):
        samples = [(0.0, 0.0, None), (0.05, 5.0, None), (0.10, 5.0, None)]
        self.assertAlmostEqual(self._sampler(samples, 30.0).distance_so_far(), 0.25 + 5.0 * 0.10)

    def test_a_standing_still_gives_zero(self):
        samples = [(0.0, 0.0, None), (0.05, 0.0, None)]
        self.assertEqual(self._sampler(samples, 0.07).distance_so_far(), 0.0)

    def test_it_skips_the_reads_without_a_speed(self):
        samples = [(0.0, 5.0, None), (0.05, None, None), (0.10, 5.0, None)]
        self.assertAlmostEqual(self._sampler(samples, 0.10).distance_so_far(), 5.0 * 0.10)
