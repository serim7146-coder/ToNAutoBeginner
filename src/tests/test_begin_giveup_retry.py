"""FL: BEGIN が見つからずあきらめた窓も、やり直し続ける（依頼者 2026-10-16。不具合報告 report_20261016_174808）。
1 窓で Run の後に BEGIN が画面に無くなり、05:41:57 にあきらめてから 12 時間止まった（6 窓ならほかの窓が次のラウンドを
始めるが、1 窓だと誰も始めない）。あきらめた窓は 30 秒後に「リスポーン → Begin 前の移動 → 押す」をやり直し、通らなければ
3 分おきに繰り返す（上限なし。ラウンドが始まる・止める・Begin が通るまで）。
本物の OSC・入力・撮影・前面は使わない。時計は偽物（本物の時間は待たない）"""
from tests.support import *  # noqa: F401,F403
import inspect
from tests.test_equip_freeze_timing import _Case

T0 = 1000.0         # _module_clock の始まり


class _Retry(_Case):
    def _ex(self, how="given_up"):
        """あきらめた窓。how: given_up（BEGIN が見つからない・FL）/ gave_up（3 回押して受理されない・EV）。
        リスポーン・Begin 前の移動・押し直し（do_begin_again）は記録するだけの偽物。self.order に (名前, 時刻) の順"""
        ex, st = self._executor(fetch=False)
        st.begin_done = False
        st.waiting_for_equip = False
        del ex._retry_after_giving_up                 # 本物を使う
        if how == "given_up":
            ex._begin_given_up_round = 7
        else:
            ex._begin_gave_up_round = 7
        self.order = []
        self.faced = True
        self.accept_on = None                         # n 回目の押し直しで通る

        def respawn(seq, label):
            self.order.append(("respawn", self.now[0], label, ex._respawn_lobby))
            return self.faced
        ex._respawn_and_face = respawn
        ex._begin_move = lambda: self.order.append(("move", self.now[0]))

        def again(seq):
            n = len([o for o in self.order if o[0] == "again"]) + 1
            self.order.append(("again", self.now[0], ex._should_retry_begin(seq),
                               ex._begin_given_up_round, ex._begin_gave_up_round))
            if self.accept_on is not None and n >= self.accept_on:
                st.begin_done = True
        ex.do_begin_again = again
        return ex, st

    def _times(self, name):
        return [round(o[1] - T0, 1) for o in self.order if o[0] == name]


class TestFirstRetryAfter30Seconds(_Retry):
    def test_it_respawns_moves_and_presses_in_that_order_30_seconds_later(self):
        ex, st = self._ex()
        self.accept_on = 1
        ex._retry_after_giving_up(7)
        self.assertEqual([o[0] for o in self.order], ["respawn", "move", "again"])
        for o in self.order:
            self.assertAlmostEqual(o[1] - T0, config.BEGIN_GIVE_UP_RETRY_SEC, delta=0.6)
        self.assertEqual(config.BEGIN_GIVE_UP_RETRY_SEC, 30.0)
        self.assertEqual(self.order[0][2:], ("Begin のやり直し", True), "Run リスポーンの設定に関わらず・ラウンドの外の見張りで")
        self.assertFalse(ex._respawn_lobby)
        self.assertIn("Begin: あきらめてから 30 秒 → リスポーンして Begin 前からやり直します", self.logs)

    def test_the_marks_are_put_back_so_the_press_can_run(self):
        """あきらめた印のままだと _should_retry_begin が偽で、押し直しが 1 回しか押さない"""
        ex, st = self._ex()
        self.assertFalse(ex._should_retry_begin(7), "あきらめた直後は偽（今どおり）")
        self.accept_on = 1
        ex._retry_after_giving_up(7)
        again = [o for o in self.order if o[0] == "again"][0]
        self.assertEqual(again[2:], (True, -1, -1))

    def test_a_failed_respawn_goes_on_from_there_without_moving(self):
        ex, st = self._ex()
        self.faced = False
        self.accept_on = 1
        ex._retry_after_giving_up(7)
        self.assertEqual([o[0] for o in self.order], ["respawn", "again"], "店の前（ここ）から動くと別の所へ行く。EY と同じ")
        self.assertIn("Begin: リスポーンできなかった → その場から押します", self.logs)

    def test_a_window_without_osc_cannot_respawn_and_presses_from_there(self):
        ex, st = self._ex()
        ex._osc = None
        self.accept_on = 1
        ex._retry_after_giving_up(7)
        self.assertEqual([o[0] for o in self.order], ["again"])
        self.assertIn("Begin: OSC が使えない窓なのでリスポーンしません → その場から押します", self.logs)

    def test_the_shop_mark_is_cleared_so_the_press_does_not_respawn_twice(self):
        ex, st = self._ex()
        ex._fetched_round = 7
        self.accept_on = 1
        ex._retry_after_giving_up(7)
        self.assertEqual(ex._fetched_round, -1)


class TestRepeatEvery3Minutes(_Retry):
    def test_it_repeats_every_180_seconds_until_it_passes(self):
        ex, st = self._ex()
        self.accept_on = 4
        ex._retry_after_giving_up(7)
        self.assertEqual(self._times("respawn"), [30.0, 210.0, 390.0, 570.0])
        self.assertEqual(self._times("move"), [30.0, 210.0, 390.0, 570.0])
        self.assertEqual(self._times("again"), [30.0, 210.0, 390.0, 570.0])
        self.assertEqual(config.BEGIN_GIVE_UP_REPEAT_SEC, 180.0)
        self.assertEqual([m for m in self.logs if "分おき" in m],
                         ["Begin: 3 分おきにやり直します（2 回目）", "Begin: 3 分おきにやり直します（3 回目）",
                          "Begin: 3 分おきにやり直します（4 回目）"])
        self.assertEqual(len([m for m in self.logs if "あきらめてから 30 秒" in m]), 1)
        self.assertTrue(st.begin_done)

    def test_it_stops_when_it_passes_and_does_not_press_again(self):
        ex, st = self._ex()
        self.accept_on = 2
        ex._retry_after_giving_up(7)
        self.now[0] += 100000
        ex._retry_after_giving_up(7)                  # 通った後に呼ばれても何もしない
        self.assertEqual(self._times("again"), [30.0, 210.0])

    def test_every_retry_gives_up_again_in_the_press_and_the_loop_goes_on(self):
        """押し直しの中でまたあきらめても（印が立つ）、入れ子にならずに 180 秒後のやり直しへ"""
        ex, st = self._ex()
        calls = []

        def again(seq):
            calls.append(self.now[0] - T0)
            ex._begin_given_up_round = seq            # また「見つからない」
            ex._retry_after_giving_up(seq)            # 本物の do_begin_again の終わりと同じ呼び方（入れ子にしない）
            if len(calls) >= 3:
                st.begin_done = True
        ex.do_begin_again = again
        ex._retry_after_giving_up(7)
        self.assertEqual(calls, [30.0, 210.0, 390.0])
        self.assertEqual(ex._give_up_loop_round, -1, "回し終えたら印を戻す")

    def test_the_first_30_seconds_is_used_once_per_round(self):
        """受理と取り違えて抜けた後にまたあきらめたら、30 秒ではなく 3 分から数える"""
        ex, st = self._ex()
        self.accept_on = 1
        ex._retry_after_giving_up(7)
        st.begin_done = False                         # 定期の取り違え（LogMonitor が戻す）
        ex._begin_given_up_round = 7
        self.accept_on = 2
        before = self.now[0]
        ex._retry_after_giving_up(7)
        self.assertAlmostEqual(self.order[-1][1] - before, config.BEGIN_GIVE_UP_REPEAT_SEC, delta=0.6)

    def test_a_new_round_starts_the_count_again(self):
        ex, st = self._ex()
        self.accept_on = 2
        ex._retry_after_giving_up(7)
        st.begin_done = False
        st.round_seq = 8
        ex._begin_given_up_round = 8
        self.accept_on = 99
        ex.do_begin_again = lambda seq: setattr(st, "begin_done", True)
        ex._retry_after_giving_up(8)
        self.assertEqual(ex._give_up_retries, 1, "ラウンドが変わったら数え直す")

    def test_a_long_idle_single_window_keeps_trying_for_12_hours(self):
        """1 窓でラウンドが始まらないまま 12 時間: 回数の上限は無い（10/16 は 12 時間止まったまま）"""
        ex, st = self._ex()
        hours = 12 * 3600
        ex._is_running = lambda: self.now[0] - T0 < hours
        ex._retry_after_giving_up(7)
        self.assertEqual(len(self._times("again")), 1 + int((hours - 30) // 180), "30 秒後 + 3 分おき")
        self.assertGreaterEqual(len(self._times("again")), 240)
        self.assertEqual(len(self._times("respawn")), len(self._times("again")))
        self.assertEqual(self._times("again")[:3], [30.0, 210.0, 390.0])
        self.assertEqual(self._times("again")[-1] % 180, 30.0)


class TestAfterThreePresses(_Retry):
    """EV: 3 回押して受理されなかった窓の 30 秒後の 1 回はリスポーンしない。通らなければ 3 分おきに合流する"""

    def test_the_first_try_is_ev_as_before_then_3_minutes_with_respawn(self):
        ex, st = self._ex("gave_up")
        self.accept_on = 3
        ex._retry_after_giving_up(7)
        self.assertEqual([o[0] for o in self.order],
                         ["again", "respawn", "move", "again", "respawn", "move", "again"],
                         "30 秒後の 1 回は今のまま（押し直しだけ）。それから先は リスポーン → 移動 → 押す")
        self.assertEqual(self._times("again"), [30.0, 210.0, 390.0])
        self.assertIn("Begin をもう一度押します（あきらめてから 30 秒）", self.logs)
        self.assertEqual([m for m in self.logs if "分おき" in m],
                         ["Begin: 3 分おきにやり直します（2 回目）", "Begin: 3 分おきにやり直します（3 回目）"])
        self.assertEqual([m for m in self.logs if "リスポーンして Begin 前から" in m], [])

    def test_passing_on_the_ev_try_ends_it(self):
        ex, st = self._ex("gave_up")
        self.accept_on = 1
        ex._retry_after_giving_up(7)
        self.assertEqual([o[0] for o in self.order], ["again"])
        self.assertEqual([m for m in self.logs if "分おき" in m], [])


class TestWhenToStop(_Retry):
    def _spoil_at(self, ex, st, after, spoil):
        real = ActionExecutor.time.sleep

        def sleep(sec):
            real(sec)
            if self.now[0] - T0 > after:
                spoil(st)
        return patch.object(ActionExecutor.time, "sleep", side_effect=sleep)

    def test_a_round_start_acceptance_new_round_or_a_stop_ends_it(self):
        for name, spoil in (("ラウンド開始", lambda st: setattr(st, "in_round", True)),
                            ("受理", lambda st: setattr(st, "begin_done", True)),
                            ("次のラウンド", lambda st: setattr(st, "round_seq", 8))):
            for after in (10, 100, 500):                 # 最初の待ち・2 回目の待ち・その先
                ex, st = self._ex()
                with self._spoil_at(ex, st, after, spoil):
                    ex._retry_after_giving_up(7)
                done = len(self._times("again"))
                self.assertLessEqual(done, 1 + (after - 30) // 180 + 1, f"{name} {after} 秒")
                self.assertTrue(all(t <= after + 1 for t in self._times("again")), f"{name} {after} 秒: 以後は押さない")

    def test_a_stop_ends_it(self):
        for after in (10, 100, 500):
            ex, st = self._ex()
            ex._is_running = lambda after=after: self.now[0] - T0 <= after
            ex._retry_after_giving_up(7)
            self.assertTrue(all(t <= after for t in self._times("again")), after)

    def test_a_stop_at_the_moment_the_wait_ends_does_not_press(self):
        """待ち終えたちょうどその時に止めた・ラウンドが始まった: 3 回押した窓の 30 秒後の 1 回も押さない"""
        for how in ("gave_up", "given_up"):
            ex, st = self._ex(how)
            ex._is_running = lambda: self.now[0] - T0 < config.BEGIN_GIVE_UP_RETRY_SEC - 0.2
            ex._retry_after_giving_up(7)
            self.assertEqual(self.order, [], how)
            ex, st = self._ex(how)
            real = ActionExecutor.time.sleep

            def sleep(sec, st=st):
                real(sec)
                if self.now[0] - T0 >= config.BEGIN_GIVE_UP_RETRY_SEC:
                    st.in_round = True
            with patch.object(ActionExecutor.time, "sleep", side_effect=sleep):
                ex._retry_after_giving_up(7)
            self.assertEqual(self.order, [], how)

    def test_a_stop_before_the_first_try_does_not_respawn(self):
        ex, st = self._ex()
        ex._is_running = lambda: self.now[0] - T0 < 20
        ex._retry_after_giving_up(7)
        self.assertEqual(self.order, [])

    def test_a_round_that_starts_during_the_respawn_does_not_press(self):
        ex, st = self._ex()
        def respawn(seq, label):
            st.in_round = True
            return True
        ex._respawn_and_face = respawn
        ex._retry_after_giving_up(7)
        self.assertEqual(self._times("again"), [])
        self.assertEqual(self._times("move"), [])

    def test_not_a_private_instance_does_not_retry(self):
        ex, st = self._ex()
        st.instance_type = config.INSTANCE_PUBLIC
        ex._retry_after_giving_up(7)
        self.assertEqual(self.order, [], "操作してはいけない窓ではリスポーンもしない")


class TestWaitsForTheOtherWindows(_Retry):
    def test_it_waits_for_the_other_windows_before_respawning(self):
        ex, st = self._ex()
        self.accept_on = 1

        def wait():
            self.order.append(("wait", self.now[0]))
            self.now[0] += 100                        # ほかの窓のフリーズが解けるまで 100 秒
            return True
        ex._wait_other_windows = wait
        ex._retry_after_giving_up(7)
        self.assertEqual([o[0] for o in self.order], ["wait", "respawn", "move", "again"])
        self.assertAlmostEqual(self.order[0][1] - T0, 30, delta=0.6)
        self.assertAlmostEqual(self.order[1][1] - T0, 130, delta=0.6)

    def test_a_stop_while_waiting_ends_it(self):
        ex, st = self._ex()
        ex._wait_other_windows = lambda: False
        ex._retry_after_giving_up(7)
        self.assertEqual(self.order, [])


class TestTheFlowAsAWhole(_Retry):
    """本物の do_after_round → _confirm_begin → あきらめ → やり直し。BEGIN が見つからない窓が 2 回目で通る"""

    def _flow(self, accept_on):
        ex, st = self._executor(fetch=False, accept_on=accept_on)
        st.waiting_for_equip = False
        del ex._retry_after_giving_up
        self.order = []
        ex._adjust_to_begin = lambda seq: "not_found"
        ex._respawn_and_face = lambda seq, label: self.order.append(("respawn", self.now[0])) or True
        ex._begin_move = lambda: (self.order.append(("move", self.now[0])), setattr(st, "begin_move_done", True))
        ex._osc = MagicMock()
        real_press = ex._press_begin

        def press(again=False, click_only=False):
            self.order.append(("press", self.now[0]))
            return real_press(again=again, click_only=click_only)
        ex._press_begin = press
        ex.item_fetch_target = lambda: None
        return ex, st

    def test_a_window_that_cannot_find_begin_tries_again_30_seconds_and_3_minutes_later(self):
        ex, st = self._flow(accept_on=3)
        with patch.object(WindowOperator, "focus_vrchat") as focus, \
                patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0.0):
            ex.do_after_round()
        focus.assert_called()                            # あきらめるときは窓を前に出すだけ（今どおり）
        presses = [round(o[1] - T0, 1) for o in self.order if o[0] == "press"]
        self.assertEqual(len(presses), 3)
        self.assertAlmostEqual(presses[1] - presses[0], config.BEGIN_GIVE_UP_RETRY_SEC, delta=1.0)
        self.assertAlmostEqual(presses[2] - presses[1], config.BEGIN_GIVE_UP_REPEAT_SEC, delta=1.0)
        self.assertEqual([o[0] for o in self.order if o[0] != "press"],
                         ["move", "respawn", "move", "respawn", "move"], "最初の Begin 前の移動 → 2 回とも リスポーン → 移動")
        self.assertTrue(st.begin_done)
        self.assertEqual(len([m for m in self.logs if "画面に BEGIN が見つかりません" in m]), 2, "最初と 30 秒後で 2 回あきらめ、3 回目で通る")
        self.assertIn("Begin: あきらめてから 30 秒 → リスポーンして Begin 前からやり直します", self.logs)
        self.assertIn("Begin: 3 分おきにやり直します（2 回目）", self.logs)

    def test_the_use_spam_is_stopped_before_the_long_wait(self):
        ex, st = self._flow(accept_on=2)
        spam = threading.Event()
        ex._start_use_spam = lambda seq: spam
        seen = []
        real = ex._retry_after_giving_up
        ex._retry_after_giving_up = lambda seq: seen.append(spam.is_set()) or real(seq)
        with patch.object(WindowOperator, "focus_vrchat"), patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0.0):
            ex.do_after_round()
        self.assertEqual(seen, [True], "何時間も続きうるので、その間 UseRight を送り続けない")

    def test_the_call_sites(self):
        src = inspect.getsource(ActionExecutor.ActionExecutor.do_after_round)
        self.assertIn("if self._gave_up_begin(round_seq):", src)
        src = inspect.getsource(ActionExecutor.ActionExecutor.do_begin_again)
        self.assertIn("if self._gave_up_begin(round_seq):", src)
        src = inspect.getsource(ActionExecutor.ActionExecutor._show_window_without_begin)
        self.assertIn("self._begin_given_up_round = round_seq", src)
        self.assertIn("画面に BEGIN が見つかりません", src)

    def test_the_giving_up_marks_make_should_retry_false_until_the_retry(self):
        ex, st = self._ex()
        self.assertFalse(ex._should_retry_begin(7))
        self.assertTrue(ex._gave_up_begin(7))
        ex2, _st2 = self._ex("gave_up")
        self.assertTrue(ex2._gave_up_begin(7))
        self.assertFalse(ex2._gave_up_begin(8))
        ex3, _st3 = self._executor(fetch=False)
        self.assertFalse(ex3._gave_up_begin(7))


class TestSingleAttemptStopsTheLoop(unittest.TestCase):
    def test_single_attempt_stubs_the_retry(self):
        from tests import support
        src = inspect.getsource(support._single_attempt)
        self.assertIn("_retry_after_giving_up", src, "このループは入口を止めれば動かない")
