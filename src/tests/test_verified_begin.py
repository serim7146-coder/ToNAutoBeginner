"""Begin（移動・押し方・受理の見分け・位置合わせ）"""
from tests.support import *  # noqa: F401,F403




class TestToNEntry(unittest.TestCase):
    """入室時の自動操作（移動はOSC・クリックはマウス）"""

    def _entry(self, logs=None):
        return ToNEntry.ToNEntry(
            0x1234, osc_port=19990,
            log=(logs.append if logs is not None else None), can_operate=lambda: True)

    def test_steps_have_expected_shape(self):
        """手順の構造を確認する。

        秒数は実機調整で頻繁に変わるので値そのものは検証しない
        （変えるたびにテストが壊れると調整の邪魔になる）。
        """
        steps = config.TON_ENTRY_STEPS
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0]["move"], "right")
        self.assertEqual(steps[1]["move"], "left")
        self.assertEqual(steps[2]["move"], "right")
        self.assertIsNone(steps[3]["move"], "最後は移動せず続けて押す")
        for step in steps[:3]:
            with self.subTest(label=step["label"]):
                self.assertGreater(step["sec"], 0, "移動する段は秒数が必要")
        self.assertEqual([s["label"] for s in steps],
                         ["警告同意", "難易度(Casual)", "BGM", "LET ME PLAY"])

    def test_move_uses_osc_and_releases(self):
        """移動はOSCで送り、必ず解除する（入力が残ると操作不能になる）"""
        entry = self._entry()
        with patch.object(entry._osc, "press", return_value=True) as mock_press, \
             patch.object(entry._osc, "stop_all") as mock_stop:
            self.assertTrue(entry.move("right", 0.4))
        mock_press.assert_called_once_with("/input/MoveRight", 0.4, stop=ANY)
        mock_stop.assert_called()

    def test_move_rejects_unknown_direction(self):
        entry = self._entry()
        with patch.object(entry._osc, "press") as mock_press:
            self.assertFalse(entry.move("ななめ", 0.4))
        mock_press.assert_not_called()

    def test_click_requires_focus(self):
        """フォーカスを取れなければクリックしない（別の窓へ飛ぶため）"""
        logs = []
        entry = self._entry(logs)
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=False), \
             patch.object(ToNEntry.WindowOperator, "click") as mock_click:
            self.assertFalse(entry.click("テスト"))
        mock_click.assert_not_called()
        self.assertTrue(any("フォーカス取得失敗" in m for m in logs))

    def test_run_aborts_when_panel_never_appears(self):
        entry = self._entry()
        with patch.object(entry, "wait_for_panel", return_value=False), \
             patch.object(entry, "move") as mock_move, \
             patch.object(entry, "click") as mock_click:
            self.assertFalse(entry.run())
        mock_move.assert_not_called()
        mock_click.assert_not_called()

    def test_run_waits_before_first_action(self):
        """パネル検出直後は描画が整っていないので少し待つ"""
        entry = self._entry()
        slept = []
        with patch.object(entry, "wait_for_panel", return_value=True),              patch.object(entry, "move", return_value=True),              patch.object(entry, "click", return_value=True),              patch.object(entry._osc, "stop_all"),              patch.object(ToNEntry.time, "sleep", side_effect=slept.append):
            entry.run()
        self.assertIn(config.TON_ENTRY_START_DELAY_SEC, slept)

    def test_run_executes_moves_and_clicks_in_order(self):
        entry = self._entry()
        actions = []
        with patch.object(entry, "wait_for_panel", return_value=True), \
             patch.object(entry, "move",
                          side_effect=lambda d, s: actions.append(("move", d, s)) or True), \
             patch.object(entry, "click",
                          side_effect=lambda label: actions.append(("click", label)) or True), \
             patch.object(entry._osc, "stop_all"), \
             patch.object(ToNEntry.time, "sleep"):
            self.assertTrue(entry.run())
        # 設定から期待値を組み立てる（秒数は調整で変わるため直書きしない）
        expected = []
        for step in config.TON_ENTRY_STEPS:
            if step["move"]:
                expected.append(("move", step["move"], step["sec"]))
            expected.append(("click", step["label"]))
        self.assertEqual(actions, expected)

    def test_run_stops_when_cancelled(self):
        """中止フラグが立ったら操作を止め、入力も解除する"""
        entry = ToNEntry.ToNEntry(0x1234, osc_port=19990, is_running=lambda: False,
                                  can_operate=lambda: True)
        with patch.object(entry, "wait_for_panel", return_value=True), \
             patch.object(entry, "move") as mock_move, \
             patch.object(entry._osc, "stop_all") as mock_stop:
            self.assertFalse(entry.run())
        mock_move.assert_not_called()
        mock_stop.assert_called()

    def test_run_aborts_when_click_fails(self):
        entry = self._entry()
        with patch.object(entry, "wait_for_panel", return_value=True), \
             patch.object(entry, "move", return_value=True), \
             patch.object(entry, "click", return_value=False), \
             patch.object(entry._osc, "stop_all") as mock_stop, \
             patch.object(ToNEntry.time, "sleep"):
            self.assertFalse(entry.run())
        mock_stop.assert_called()

    def test_loading_screen_is_excluded(self):
        """ロード画面にも赤いロゴが出るので背景色で除外する"""
        entry = self._entry()
        w = h = 200
        # 四隅が青緑 = ロード画面
        bits = bytearray(w * h * 4)
        for x, y in ((60, 60), (w - 60, 60), (60, h - 60), (w - 60, h - 60)):
            i = (y * w + x) * 4
            bits[i] = 200      # B
            bits[i + 1] = 100  # G
            bits[i + 2] = 20   # R
        self.assertTrue(entry._is_loading_screen(bytes(bits), w, h))

    def test_lobby_is_not_loading_screen(self):
        entry = self._entry()
        w = h = 200
        bits = bytes(w * h * 4)   # 全部黒
        self.assertFalse(entry._is_loading_screen(bits, w, h))

    def test_press_begin_moves_then_clicks(self):
        entry = self._entry()
        actions = []
        with patch.object(entry, "move",
                          side_effect=lambda d, s: actions.append((d, s)) or True), \
             patch.object(entry, "click", return_value=True), \
             patch.object(entry._osc, "stop_all"), \
             patch.object(ToNEntry.time, "sleep"):
            self.assertTrue(entry.press_begin())
        self.assertEqual(actions, [("forward", config.TON_ENTRY_BEGIN_FORWARD_SEC),
                                   ("left", config.TON_ENTRY_BEGIN_LEFT_SEC)])

    def test_entry_begin_distance_differs_from_round_end(self):
        """入室直後はラウンド終了後と位置が違うため別の値を使う"""
        self.assertNotEqual(config.TON_ENTRY_BEGIN_FORWARD_SEC, config.BEGIN_FORWARD_SEC)
        self.assertGreater(config.TON_ENTRY_BEGIN_FORWARD_SEC, 0)




class TestToNEntryLocking(unittest.TestCase):
    """入室操作はクリックだけ排他にする"""

    def _entry(self):
        return ToNEntry.ToNEntry(0x1234, osc_port=19990, can_operate=lambda: True)

    def test_click_takes_the_global_lock(self):
        entry = self._entry()
        locked = []
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=True), \
             patch.object(ToNEntry.WindowOperator, "click",
                          side_effect=lambda: locked.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked())):
            entry.click("テスト")
        self.assertEqual(locked, [True], "クリックはロック内で行うこと")

    def test_move_does_not_take_the_lock(self):
        entry = self._entry()
        held = []
        with patch.object(entry._osc, "press",
                          side_effect=lambda a, s, stop=None: held.append(
                              SharedState._GLOBAL_ACTION_LOCK.locked()) or True), \
             patch.object(entry._osc, "stop_all"):
            entry.move("right", 0.38)
        self.assertEqual(held, [False], "移動はロック不要（OSCはフォーカスを奪わない）")

    def test_lock_is_released_after_click(self):
        entry = self._entry()
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=True), \
             patch.object(ToNEntry.WindowOperator, "click"):
            entry.click("テスト")
        self.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked())

    def test_click_aborts_without_focus_and_releases_lock(self):
        entry = self._entry()
        with patch.object(ToNEntry.WindowOperator, "focus_window", return_value=False), \
             patch.object(ToNEntry.WindowOperator, "click") as mock_click:
            self.assertFalse(entry.click("テスト"))
        mock_click.assert_not_called()
        self.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked())




class TestBeginRetry(unittest.TestCase):
    """押した Begin が受理（本物の Verified）されなければ、クリックだけ押し直す"""

    def setUp(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()
        for name, value in (("BEGIN_RETRY_WAIT_SEC", 0.05), ("BEGIN_RETRY_MAX", 3),
                            ("BEGIN_WAIT_SEC", 0)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        SharedState.equip_freeze_reset()
        SharedState.continue_round_reset()

    def _run(self, osc_port=9000, accept_on=None, after_click=None):
        """accept_on 回目のクリックで受理される。after_click(n) はクリックの後に呼ぶ"""
        cfg = WindowConfig(hwnd=123, osc_port=osc_port)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE,
                         round_end_seen=True, item_id=5, round_seq=4)
        logs = []
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append)
        clicks = []
        moves = []

        def click():
            clicks.append(1)
            if accept_on is not None and len(clicks) >= accept_on:
                st.begin_done = True
            if after_click:
                after_click(len(clicks), st)

        with patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ActionExecutor.PlaySound, "play_sound"), \
             patch.object(ex, "move", side_effect=lambda *a: moves.append(1)), \
             patch.object(ex, "move_forward_left",
                          side_effect=lambda *a: moves.append(1)), \
             patch.object(WindowOperator, "focus_window", return_value=True), \
             patch.object(WindowOperator, "click", side_effect=click):
            ex.do_after_round()
        self.logs = logs
        return len(clicks), len(moves)

    def test_it_clicks_again_without_moving(self):
        for osc_port in (9000, 0):
            clicks, moves = self._run(osc_port, accept_on=2)

            self.assertEqual(clicks, 2, osc_port)
            self.assertEqual(moves, 1, f"{osc_port}: 移動は最初の1回だけ")
            self.assertTrue(any("押し直し（2/3回目）" in m for m in self.logs),
                            self.logs)

    def test_an_accepted_begin_is_not_clicked_again(self):
        for osc_port in (9000, 0):
            clicks, _moves = self._run(osc_port, accept_on=1)

            self.assertEqual(clicks, 1, osc_port)

    def test_it_gives_up_after_three(self):
        clicks, _moves = self._run()

        self.assertEqual(clicks, 3)
        self.assertTrue(any("⚠ Begin を3回押しましたが受理されませんでした" in m
                            for m in self.logs), self.logs)

    def test_a_started_round_stops_the_retry(self):
        def round_started(n, st):
            if n == 1:
                st.in_round = True

        clicks, _moves = self._run(after_click=round_started)

        self.assertEqual(clicks, 1)
        self.assertFalse(any("受理されませんでした" in m for m in self.logs))

    def test_a_new_round_stops_the_old_retry(self):
        def next_round(n, st):
            if n == 1:
                st.round_seq += 1

        clicks, _moves = self._run(after_click=next_round)

        self.assertEqual(clicks, 1)

    def test_it_only_retries_what_it_clicked(self):
        """クリックしていなければ（フォーカス失敗など）押し直しの流れに入らない"""
        cfg = WindowConfig(hwnd=123, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_end_seen=True,
                         item_id=5)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ex, "move_forward_left"), \
             patch.object(ex, "_confirm_begin") as confirm, \
             patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(WindowOperator, "click"):
            ex.do_after_round()

        confirm.assert_not_called()




class TestSpeedVoiceFollowsAutoBegin(unittest.TestCase):
    """8 Pages / Punished の音声は、自動 Begin が機能しているかで出し分ける。

    ツールが回している窓（自動 Begin が ON で private）では、フリーズしない種別は
    知らせない。人が遊んでいる窓では、フリーズ設定に関わらず鳴らす。
    """

    def setUp(self):
        self._saved = (SharedState.get_freeze_on_8pages(),
                       SharedState.get_freeze_on_punish())
        SharedState.set_hands_free(False)
        self.addCleanup(self._restore)

    def _restore(self):
        SharedState.set_freeze_on_8pages(self._saved[0])
        SharedState.set_freeze_on_punish(self._saved[1])
        SharedState.set_hands_free(False)

    def _announce(self, kind, active, freeze_8pages=False, freeze_punish=False):
        """鳴ったかを返す。フリーズそのものは別に見るので止めておく"""
        SharedState.set_freeze_on_8pages(freeze_8pages)
        SharedState.set_freeze_on_punish(freeze_punish)
        cfg = WindowConfig(voice_8pages="8pages.mp3", voice_punish="punish.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                           auto_begin_active=lambda: active)
        with patch.object(ex, "_freeze_for_speed_kind"), \
             patch.object(PlaySound, "play_sound") as play:
            ex._announce_speed_kind(kind, 3.0)
        return [c.args[0] for c in play.call_args_list]

    # ── 1〜4. 4マス ─────────────────────────
    def test_a_played_window_with_the_freeze_on_plays(self):
        self.assertEqual(self._announce("8pages", active=False, freeze_8pages=True),
                         ["8pages.mp3"])

    def test_a_played_window_with_the_freeze_off_still_plays(self):
        """人が遊んでいる窓は、フリーズ設定に関わらず鳴らす"""
        self.assertEqual(self._announce("8pages", active=False, freeze_8pages=False),
                         ["8pages.mp3"])

    def test_a_tool_run_window_with_the_freeze_on_plays(self):
        self.assertEqual(self._announce("8pages", active=True, freeze_8pages=True),
                         ["8pages.mp3"])

    def test_a_tool_run_window_with_the_freeze_off_is_silent(self):
        """変わったのはこの1マスだけ"""
        self.assertEqual(self._announce("8pages", active=True, freeze_8pages=False), [])

    def test_the_same_four_cases_for_punished(self):
        for active, freeze, expected in ((False, True, ["punish.mp3"]),
                                         (False, False, ["punish.mp3"]),
                                         (True, True, ["punish.mp3"]),
                                         (True, False, [])):
            self.assertEqual(self._announce("punish", active=active,
                                            freeze_punish=freeze),
                             expected, (active, freeze))

    # ── 5. 自分の種別の設定を見る ─────────────────────
    def test_eight_pages_does_not_look_at_the_punish_setting(self):
        """種別の取り違え。8 Pages が OFF・Punished が ON なら 8 Pages は鳴らない"""
        self.assertEqual(self._announce("8pages", active=True,
                                        freeze_8pages=False, freeze_punish=True), [])
        self.assertEqual(self._announce("8pages", active=True,
                                        freeze_8pages=True, freeze_punish=False),
                         ["8pages.mp3"])

    def test_punished_does_not_look_at_the_eight_pages_setting(self):
        self.assertEqual(self._announce("punish", active=True,
                                        freeze_8pages=True, freeze_punish=False), [])
        self.assertEqual(self._announce("punish", active=True,
                                        freeze_8pages=False, freeze_punish=True),
                         ["punish.mp3"])

    def test_the_voice_and_the_freeze_read_the_same_setting(self):
        """音声とフリーズが別々に設定を読むと、いつか食い違う"""
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        for name in ("_announce_speed_kind", "_freeze_for_speed_kind"):
            body = src[src.index(f"    def {name}("):]
            body = body[:body.index("\n    def ", 10)]
            self.assertIn("self._freeze_enabled_for(kind)", body, name)
            self.assertNotIn("get_freeze_on_", body, name)

    # ── 6. 両方の条件が要る ───────────────────────
    def _monitor(self, auto_begin, instance_type):
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=auto_begin), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        return monitor

    def test_auto_begin_is_active_only_when_on_and_private(self):
        cases = ((True, config.INSTANCE_PRIVATE, True),
                 (False, config.INSTANCE_PRIVATE, False),
                 (True, config.INSTANCE_PUBLIC, False),
                 (True, config.INSTANCE_YAKIIMO, False),
                 (True, config.INSTANCE_HOSHIIMO, False))
        for auto_begin, itype, expected in cases:
            self.assertEqual(self._monitor(auto_begin, itype)._auto_begin_active(),
                             expected, (auto_begin, itype))

    def test_the_executor_asks_the_monitor(self):
        """判定は LogMonitor の1か所。ActionExecutor には関数で渡す"""
        monitor = self._monitor(True, config.INSTANCE_PRIVATE)

        self.assertTrue(monitor._action._auto_begin_active())
        monitor.st.instance_type = config.INSTANCE_PUBLIC
        self.assertFalse(monitor._action._auto_begin_active(), "その時々の値を見る")

    def test_without_a_callable_it_counts_as_not_active(self):
        """渡されなければ「機能していない」＝鳴らす側"""
        SharedState.set_freeze_on_8pages(False)
        cfg = WindowConfig(voice_8pages="8pages.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

        with patch.object(ex, "_freeze_for_speed_kind"), \
             patch.object(PlaySound, "play_sound") as play:
            ex._announce_speed_kind("8pages", 3.0)

        play.assert_called_once_with("8pages.mp3")

    def test_the_round_over_split_uses_the_same_rule(self):
        src = Path(LogMonitor.__file__).read_text(encoding="utf-8")

        self.assertIn("announce_on_round_over = not self._auto_begin_active()", src)

    # ── 7. 放置モードと平常 ───────────────────────
    def test_hands_free_plays_nothing(self):
        SharedState.set_hands_free(True)
        for active in (False, True):
            for kind in ("8pages", "punish"):
                self.assertEqual(self._announce(kind, active=active,
                                                freeze_8pages=True, freeze_punish=True),
                                 [], (active, kind))

    def test_normal_plays_nothing(self):
        for active in (False, True):
            self.assertEqual(self._announce("normal", active=active,
                                            freeze_8pages=True, freeze_punish=True),
                             [], active)

    def test_silence_adds_no_log(self):
        """鳴らさなかったことはログに足さない（依頼者はログが増えるのを嫌う）"""
        SharedState.set_freeze_on_8pages(False)
        logs = []
        cfg = WindowConfig(voice_8pages="8pages.mp3")
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(cfg, st, lambda: True, logs.append,
                                           auto_begin_active=lambda: True)

        with patch.object(ex, "_freeze_for_speed_kind"), \
             patch.object(PlaySound, "play_sound"):
            ex._announce_speed_kind("8pages", 3.0)

        self.assertEqual(len(logs), 1, "速度の1行だけ")




@unittest.skipUnless(_cv2_available(), "OpenCV / numpy が無い")
class TestBeginDetect(unittest.TestCase):
    """[ BEGIN ] の文字を窓の画像から探す検出器（届けられたもの。中身は変えない）"""

    def setUp(self):
        import numpy as np
        self.np = np
        self.addCleanup(setattr, BeginDetect, "_templates", BeginDetect._templates)

    def _template(self):
        import cv2
        path = config.resource_path(BeginDetect.TEMPLATE_FILES[0])
        data = self.np.fromfile(str(path), dtype=self.np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)

    def _pasted(self, x, y):
        """黒い 1920x1080 に begin_1.png を2倍にして貼る。貼った中心を返す"""
        import cv2
        t = cv2.resize(self._template(), None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        img = self.np.zeros((1080, 1920, 3), dtype=self.np.uint8)
        h, w = t.shape[:2]
        img[y:y + h, x:x + w] = t
        return img, (x + w / 2, y + h / 2)

    def test_the_templates_load(self):
        self.assertTrue(BeginDetect.available())

    def test_a_pasted_begin_is_found_at_its_centre(self):
        img, (cx, cy) = self._pasted(700, 480)

        hit = BeginDetect.find_in_bgr(img)

        self.assertIsNotNone(hit)
        self.assertLessEqual(abs(hit["cx"] - cx), 5, hit)
        self.assertLessEqual(abs(hit["cy"] - cy), 5, hit)

    def test_nothing_is_found_in_black_or_noise(self):
        black = self.np.zeros((1080, 1920, 3), dtype=self.np.uint8)
        noise = self.np.random.default_rng(1).integers(0, 256, (1080, 1920, 3),
                                                       dtype=self.np.uint8)
        self.assertIsNone(BeginDetect.find_in_bgr(black))
        self.assertIsNone(BeginDetect.find_in_bgr(noise))

    def test_missing_templates_make_it_unavailable(self):
        img, _c = self._pasted(700, 480)             # 貼る絵は差し替える前に作る
        BeginDetect._templates = None
        with patch.object(BeginDetect, "TEMPLATE_FILES", ("begin_templates/nope.png",)):
            self.assertFalse(BeginDetect.available())
            self.assertIsNone(BeginDetect.find_in_bgr(img))
        BeginDetect._templates = None

    def test_the_capture_format_is_accepted(self):
        """ScreenCapture.capture_window の形（BGRA のバイト列）から探せる"""
        img, (cx, _cy) = self._pasted(900, 500)
        alpha = self.np.full((1080, 1920, 1), 255, dtype=self.np.uint8)
        bits = self.np.concatenate([img, alpha], axis=2).tobytes()

        hit = BeginDetect.find(bits, 1920, 1080)

        self.assertIsNotNone(hit)
        self.assertLessEqual(abs(hit["cx"] - cx), 5)
        self.assertIsNone(BeginDetect.find(b"", 1920, 1080))




class TestBeginThreePresses(unittest.TestCase):
    """1ラウンドで押すのは3回（BEGIN_RETRY_MAX）で終わり。位置合わせは1回目と
    2回目の間に1回だけ。差し込み → 位置合わせ → 差し込み → 前面化＋クリック、
    最初から前面化＋クリックの窓は クリック → 位置合わせ → クリック → クリック"""

    def setUp(self):
        self.events = []
        self.logs = []
        cfg = WindowConfig(hwnd=0x100, osc_port=9000)
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=5, round_seq=1)
        self.ex = ActionExecutor.ActionExecutor(cfg, self.st, lambda: True, self.logs.append)
        self.accept_after = None          # この出来事の数に達したら受理
        self.dips = []                    # 差し込みごとの結果（"miss" / "unplaceable" / "ok"）
        self.adjust = "aimed"
        self.front = [False]              # _vrchat_is_in_front の答え（尽きたら最後の値）
        for p in (patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0),
                  patch.object(ActionExecutor.time, "sleep"),
                  patch.object(self.ex, "_wait_other_windows", return_value=True),
                  patch.object(self.ex, "_begin_precheck", return_value=True),
                  patch.object(self.ex, "_start_use_spam", return_value=None),
                  patch.object(self.ex, "_begin_by_cursor", return_value=True),
                  patch.object(self.ex, "_vrchat_is_in_front", side_effect=self._front),
                  patch.object(self.ex, "_dip_cursor_for_begin", side_effect=self._dip),
                  patch.object(self.ex, "_adjust_to_begin", side_effect=self._adjust),
                  patch.object(WindowOperator, "borrow_front", side_effect=self._borrow),
                  patch.object(WindowOperator, "return_front"),
                  patch.object(WindowOperator, "click", side_effect=lambda: self._event("click")),
                  patch.object(WindowOperator, "focus_window",
                               side_effect=lambda h: self._event("focus") or True)):
            p.start()
            self.addCleanup(p.stop)

    def _event(self, name):
        self.events.append(name)
        if self.accept_after is not None and len(self.events) >= self.accept_after:
            self.st.begin_done = True

    def _front(self):
        return self.front.pop(0) if len(self.front) > 1 else self.front[0]

    def _dip(self, _tail):
        result = self.dips.pop(0) if self.dips else "miss"
        self.ex._dip_landed = result != "unplaceable"
        self._event("dip")
        return self.st.begin_done

    def _adjust(self, _round_seq):
        self._event("adjust")
        return self.adjust

    def _borrow(self, _hwnd):
        self.borrowed = getattr(self, "borrowed", 0) + 1
        return True, None

    def _round(self):
        """do_after_round の押すところと同じ: 1回目を押し、押せたら確かめる"""
        if self.ex._press_begin():
            self.ex._confirm_begin(self.st.round_seq)

    def test_dip_adjust_dip_is_enough(self):
        self.dips = ["miss", "ok"]
        self.accept_after = 3

        self._round()

        self.assertEqual(self.events, ["dip", "adjust", "dip"])

    def test_dip_adjust_dip_click_and_no_more(self):
        with patch.object(self.ex, "_begin_accepted",
                          wraps=self.ex._begin_accepted) as waited:
            self._round()

        self.assertEqual(self.events, ["dip", "adjust", "dip", "click"])
        # 差し込みは中で受理を待ち終えている。待ち直すのはクリックの後だけ
        self.assertEqual(waited.call_count, 1, "クリックの後の1回だけ")
        self.assertTrue(any("Begin を3回押しましたが受理されませんでした" in m
                            for m in self.logs), self.logs)

    def test_a_click_window_clicks_three_times(self):
        self.ex._begin_by_cursor.return_value = False

        self._round()

        self.assertEqual(self.events, ["click", "adjust", "click", "click"])

    def test_vrchat_in_front_before_the_second_turns_it_into_a_click(self):
        self.front = [False, True]

        self._round()

        self.assertEqual(self.events, ["dip", "adjust", "click", "click"])

    def test_an_unplaceable_dip_clicks_within_the_same_turn(self):
        self.dips = ["unplaceable", "unplaceable"]

        self._round()

        self.assertEqual(self.events, ["dip", "click", "adjust", "dip", "click", "click"])
        turns = [m for m in self.logs if "押し直し（" in m]
        self.assertEqual(len(turns), 2, "回は3回（1回目＋押し直し2回）")

    def test_an_acceptance_after_any_turn_ends_it(self):
        for after, expected in ((1, ["dip"]), (3, ["dip", "adjust", "dip"]),
                                (4, ["dip", "adjust", "dip", "click"])):
            self.events.clear()
            self.st.begin_done = False
            self.accept_after = after

            self._round()

            self.assertEqual(self.events, expected, after)
        self.assertFalse(any("受理されませんでした" in m for m in self.logs))

    def test_a_missing_begin_only_shows_the_window(self):
        self.adjust = "not_found"

        self._round()

        self.assertEqual(self.events, ["dip", "adjust", "focus"], "クリックしない")
        self.assertEqual(getattr(self, "borrowed", 0), 0, "札を作らない（元の窓へ返さない）")
        self.assertFalse(self.ex._should_retry_begin(self.st.round_seq))
        self.assertTrue(any("BEGIN が見つかりません" in m for m in self.logs), self.logs)
        self.assertFalse(any("受理されませんでした" in m for m in self.logs))

    def test_a_missing_begin_in_a_click_window_also_stops_clicking(self):
        self.ex._begin_by_cursor.return_value = False
        self.adjust = "not_found"

        self._round()

        self.assertEqual(self.events, ["click", "adjust", "focus"])

    def test_the_give_up_is_only_for_that_round(self):
        self.adjust = "not_found"
        self._round()
        self.st.round_seq += 1

        self.assertTrue(self.ex._should_retry_begin(self.st.round_seq))

    def test_a_skipped_adjustment_keeps_the_order(self):
        self.adjust = "skip"

        self._round()

        self.assertEqual(self.events, ["dip", "adjust", "dip", "click"])

    def test_an_acceptance_during_the_adjustment_stops_there(self):
        def accept(_round_seq):
            self._event("adjust")
            self.st.begin_done = True
            return "skip"

        self.ex._adjust_to_begin.side_effect = accept
        self._round()

        self.assertEqual(self.events, ["dip", "adjust"])
        self.assertFalse(any("押し直し" in m for m in self.logs), self.logs)

    def test_a_dip_that_landed_counts_as_the_turn(self):
        """差し込めた回（受理なし）は、その回のうちにクリックへ落ちない"""
        self.assertTrue(self.ex._press_begin())
        self.assertEqual(self.events, ["dip"])
        self.assertEqual(self.ex._last_press, "dip")




class TestBeginAdjust(unittest.TestCase):
    """位置合わせ: 撮影 → BEGIN を探す → 照準との横のずれ → 横移動 → 撮り直し"""

    AIM = (1000.0, 500.0)

    def setUp(self):
        self.moves = []
        self.logs = []
        self.hits = []
        cfg = WindowConfig(hwnd=0x100, osc_port=9000)
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=5, round_seq=1)
        self.ex = ActionExecutor.ActionExecutor(cfg, self.st, lambda: True, self.logs.append)
        self.addCleanup(setattr, ActionExecutor.ActionExecutor, "_detector_unavailable_logged",
                        ActionExecutor.ActionExecutor._detector_unavailable_logged)
        # 高さ 971px で幅 100px の文字は w/H 0.103（目標）。横のテストで前後は動かない
        self.capture = patch.object(ScreenCapture, "capture_window",
                                    return_value=(b"\0" * 16, 2, 971))
        for p in (patch.object(BeginDetect, "available", return_value=True),
                  patch.object(BeginDetect, "find", side_effect=self._find),
                  patch.object(WindowOperator, "aim_in_window_image", return_value=self.AIM),
                  self.capture,
                  patch.object(ActionExecutor.time, "sleep"),
                  patch.object(self.ex, "move", side_effect=self._move)):
            p.start()
            self.addCleanup(p.stop)

    def _hit(self, dx, w=100):
        return {"score": 0.9, "dark": 0.9, "cx": self.AIM[0] + dx, "cy": 480.0,
                "w": w, "h": 20}

    def _find(self, _bits, _w, _h):
        self.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked(), "撮影・検出でロックを持たない")
        if not self.hits:
            return None
        dx = self.hits.pop(0)
        return None if dx is None else self._hit(dx)

    def _move(self, direction, sec):
        self.assertFalse(SharedState._GLOBAL_ACTION_LOCK.locked(), "横移動でロックを持たない")
        self.moves.append((direction, round(sec, 3)))

    def test_it_moves_right_then_uses_the_measured_speed(self):
        """1回目 0.1秒で 150px 動いた → 1500px/秒 → 残り 60px は 0.04秒 → 下限の 0.05秒"""
        self.hits = [210, 60, 10]

        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")

        self.assertEqual(self.moves, [("right", 0.1), ("right", 0.05)])
        self.assertTrue(any("位置合わせ済み（横 +10px）" in m for m in self.logs), self.logs)
        self.assertTrue(any("位置合わせ（照準から横 +210px）→ 右へ 0.10秒" in m
                            for m in self.logs), self.logs)

    def test_it_moves_left_when_begin_is_left(self):
        self.hits = [-120, -10]

        self.ex._adjust_to_begin(1)

        self.assertEqual(self.moves, [("left", 0.1)])

    def test_the_second_step_uses_the_measured_speed(self):
        """1回目 0.1秒で 100px → 1000px/秒 → 残り 200px は 0.2秒"""
        self.hits = [300, 200, 0]

        self.ex._adjust_to_begin(1)

        self.assertEqual(self.moves, [("right", 0.1), ("right", 0.2)])

    def test_within_the_tolerance_it_does_not_move(self):
        self.hits = [30]          # 許容 = 100 * 0.3 = 30px

        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")

        self.assertEqual(self.moves, [])
        self.assertTrue(any("照準の上にあります" in m for m in self.logs), self.logs)

    def test_the_step_count_is_capped(self):
        self.hits = [1000, 900, 800, 700, 600, 500, 400]
        with patch.object(config, "BEGIN_ADJUST_MAX_TOTAL_SEC", 100):
            self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(len(self.moves), config.BEGIN_ADJUST_MAX_STEPS)
        self.assertTrue(any("打ち切り" in m for m in self.logs))

    def test_the_total_time_is_capped(self):
        """0.1秒で 10px（100px/秒）→ 以後は上限 0.4秒ずつ → 合計 1.2秒で止まる"""
        self.hits = [5000, 4990, 4950, 4910, 4870, 4830]
        with patch.object(config, "BEGIN_ADJUST_MAX_STEPS", 100):
            self.ex._adjust_to_begin(1)
        self.assertEqual(self.moves, [("right", 0.1), ("right", 0.4), ("right", 0.4),
                                      ("right", 0.3)])
        self.assertAlmostEqual(sum(s for _d, s in self.moves),
                               config.BEGIN_ADJUST_MAX_TOTAL_SEC)

    def test_it_stops_when_nothing_moved(self):
        self.hits = [200, 196]            # 4px しか変わらない（OSC が届いていない等）

        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")

        self.assertEqual(self.moves, [("right", 0.1)])
        self.assertTrue(any("動いていない" in m for m in self.logs), self.logs)

    def test_losing_begin_after_a_move_searches_again(self):
        """動いた後に見失ったら撮り直し → 無ければ探し直す。それでも無ければ not_found"""
        self.hits = [200, None]

        self.assertEqual(self.ex._adjust_to_begin(1), "not_found")
        self.assertEqual(self.moves[:3], [("right", 0.1), ("back", 0.15), ("forward", 0.15)])

    def test_an_acceptance_stops_it(self):
        self.hits = [400, 300, 200, 100]
        self.ex.move.side_effect = lambda d, s: (self._move(d, s),
                                                 setattr(self.st, "begin_done", True))

        self.assertEqual(self.ex._adjust_to_begin(1), "skip")

        self.assertEqual(len(self.moves), 1, "それ以上動かない")

    def test_a_new_round_or_a_stop_stops_it(self):
        self.hits = [400, 300]
        self.st.in_round = True
        self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.st.in_round = False
        self.assertEqual(self.ex._adjust_to_begin(2), "skip", "別のラウンド")
        self.assertEqual(self.moves, [])

    def test_no_begin_anywhere_is_not_found(self):
        """見つからなければ探す（後ろ→戻して前へ×4→戻す）。それでも無ければ not_found"""
        self.hits = [None] * 10

        self.assertEqual(self.ex._adjust_to_begin(1), "not_found")
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15)] + [("forward", 0.3)] * 4
                         + [("back", 1.2)])

    def test_no_capture_is_a_skip(self):
        self.capture.stop()
        with patch.object(ScreenCapture, "capture_window", return_value=(b"", 0, 0)):
            self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.capture.start()
        with patch.object(WindowOperator, "aim_in_window_image", return_value=None):
            self.assertEqual(self.ex._adjust_to_begin(1), "skip")

    def test_an_unavailable_detector_is_a_skip_told_once(self):
        ActionExecutor.ActionExecutor._detector_unavailable_logged = False
        with patch.object(BeginDetect, "available", return_value=False):
            self.assertEqual(self.ex._adjust_to_begin(1), "skip")
            self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.assertEqual(sum("使えません" in m for m in self.logs), 1, self.logs)




class TestBeginAfterFalseVerified(unittest.TestCase):
    """定期の Verified を受理と取り違えたら、15秒後に begin_done を戻して Begin を押し直す"""

    BASE = datetime(2026, 10, 1, 14, 23, 9).timestamp()

    def _stamp(self, at):
        return datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  "

    def _monitor(self, auto_begin=True, instance_type=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=auto_begin), {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.last_begin_press_at = time.time()   # ツールが押した直後（受理されるのはこのときだけ）
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        self.started = []
        return monitor

    def _feed(self, monitor, at, body=None, clock=None):
        """1行読んで、読み取りのループと同じく _check_pending_verified を呼ぶ"""
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(SharedState, "get_speed_detect", return_value=False), \
             patch.object(LogMonitor.time, "time", return_value=clock or at + 5000), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            monitor._process(self._stamp(at) + (body or "[Behaviour] tick"))
            monitor._check_pending_verified()
        for c in thread.call_args_list:
            target = c.kwargs.get("target")
            if target is not None and hasattr(target, "__func__"):
                self.started.append((target.__func__.__name__, c.kwargs.get("args", ())))

    def _again(self):
        return [args for name, args in self.started if name == "do_begin_again"]

    def _false_begin(self, monitor):
        """位相を知らない起動直後: Verified Round End と同じ秒の定期の Verified を受理と取り違える"""
        self._feed(monitor, self.BASE, "RoundOver")
        self._feed(monitor, self.BASE + 13, "Verified Round End")
        self._feed(monitor, self.BASE + 13, "Verified")
        self.assertTrue(monitor.st.begin_done, "前提: 受理と判断")

    def test_no_round_start_resets_and_begins_again_once(self):
        monitor = self._monitor()
        self._false_begin(monitor)
        seq = monitor.st.round_seq
        self._feed(monitor, self.BASE + 13 + config.VERIFIED_ROUND_START_WAIT_SEC)
        self.assertTrue(monitor.st.begin_done, "15秒ちょうどではまだ待つ")
        self.assertEqual(self._again(), [])

        self._feed(monitor, self.BASE + 14 + config.VERIFIED_ROUND_START_WAIT_SEC)

        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(self._again(), [(seq,)])
        self.assertTrue(any("Begin が通っていませんでした（定期の Verified でした）→ 押し直します" in m
                            for m in monitor.logs), monitor.logs)
        self._feed(monitor, self.BASE + 40)
        self.assertEqual(len(self._again()), 1, "1回だけ")

    def test_a_scheduled_one_right_after_a_press(self):
        monitor = self._monitor()
        monitor._verified.last_periodic = self.BASE - 287          # 次の予定は BASE+13
        self._feed(monitor, self.BASE, "RoundOver")
        self._feed(monitor, self.BASE + 12, "Verified Round End")
        monitor.st.last_begin_press_at = 50_000.0
        self._feed(monitor, self.BASE + 13, "Verified", clock=50_000.5)
        self.assertTrue(monitor.st.begin_done, "前提: 重なった1回は受理")
        self._feed(monitor, self.BASE + 30)
        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(len(self._again()), 1)

    def test_a_round_start_within_15_seconds_does_nothing(self):
        monitor = self._monitor()
        self._false_begin(monitor)
        self._feed(monitor, self.BASE + 20,
                   "This round is taking place at Facility (12) and the round type is Classic")
        self._feed(monitor, self.BASE + 60)
        self.assertEqual(self._again(), [])
        self.assertFalse(any("押し直します" in m for m in monitor.logs))

    def test_windows_the_tool_does_not_begin_only_reset(self):
        for auto_begin, itype in ((False, config.INSTANCE_PRIVATE), (True, config.INSTANCE_PUBLIC)):
            monitor = self._monitor(auto_begin=auto_begin, instance_type=itype)
            self._false_begin(monitor)
            self._feed(monitor, self.BASE + 30)
            self.assertFalse(monitor.st.begin_done, (auto_begin, itype))
            self.assertEqual(self._again(), [], (auto_begin, itype))

    def test_stopped_or_in_a_round_does_nothing(self):
        for spoil in ("stopped", "in_round"):
            monitor = self._monitor()
            self._false_begin(monitor)
            if spoil == "stopped":
                monitor._running = False
            else:
                monitor.st.in_round = True
            self._feed(monitor, self.BASE + 30)
            self.assertTrue(monitor.st.begin_done, spoil)
            self.assertEqual(self._again(), [], spoil)

    def test_nothing_to_redo_when_begin_is_not_marked(self):
        """受理の印がもう無い（ほかの経路で戻った）なら、押し直さない"""
        monitor = self._monitor()
        self._false_begin(monitor)
        monitor.st.begin_done = False
        self._feed(monitor, self.BASE + 30)
        self.assertEqual(self._again(), [])
        self.assertFalse(any("押し直します" in m for m in monitor.logs))

    def test_window_6(self):
        """実機の窓6: 14:23:22 Verified Round End と同じ秒の Verified、その後は5分おきの定期だけ"""
        monitor = self._monitor()
        t = datetime(2026, 10, 1, 14, 23, 22).timestamp()
        self._feed(monitor, t - 13, "RoundOver")
        self._feed(monitor, t, "Verified Round End")
        self._feed(monitor, t, "Verified")
        monitor.st.last_begin_press_at = 0.0          # 以後はツールが押していない（定期）
        # 実機ではログの行が絶えず来るので、受理から15秒過ぎた最初の行で「始まらなかった」が決まる
        # （次の Verified の300秒後より前）。その行が無いと、後で決まった位相が先に覚えた位相を上書きする
        self._feed(monitor, t + 20)
        for k in range(1, 5):
            self._feed(monitor, t + 300 * k + 1, "Verified")
            self._feed(monitor, t + 300 * k + 20)
        self.assertFalse(monitor.st.begin_done, "以後の定期は受理にしない")
        self.assertEqual(len(self._again()), 1, "押し直しが始まる（1回）")

    def test_the_move_mark_is_reset_at_round_over_and_round_start(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.begin_move_done = True
        self._feed(monitor, self.BASE, "RoundOver")
        self.assertFalse(monitor.st.begin_move_done)
        monitor.st.begin_move_done = True
        self._feed(monitor, self.BASE + 30,
                   "This round is taking place at Facility (12) and the round type is Classic")
        self.assertFalse(monitor.st.begin_move_done)




class TestDoBeginAgain(unittest.TestCase):
    """ActionExecutor.do_begin_again（移動がまだなら移動から、済んでいれば押すところから）"""

    def _executor(self, **st_kw):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=3, **st_kw)
        self.running = True
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=123, osc_port=9000), st,
                                           lambda: self.running, lambda _m: None)
        self.order = []
        patches = (
            patch.object(ex, "move_forward_left", side_effect=lambda *_a: self.order.append("move")),
            patch.object(ex, "_wait_other_windows", side_effect=lambda: self.order.append("wait") or True),
            patch.object(ex, "_begin_precheck", side_effect=lambda check_freeze=True: True),
            patch.object(ex, "_press_begin", side_effect=lambda again=False, click_only=False:
                         self.order.append(("press", again)) or True),
            patch.object(ex, "_confirm_begin", side_effect=lambda seq: self.order.append(("confirm", seq))),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return ex, st

    def test_not_moved_yet_moves_once_then_presses(self):
        ex, st = self._executor()
        ex.do_begin_again(3)
        self.assertEqual(self.order, ["move", "wait", ("press", True), ("confirm", 3)])
        self.assertTrue(st.begin_move_done)

    def test_already_moved_only_presses(self):
        ex, st = self._executor(begin_move_done=True)
        ex.do_begin_again(3)
        self.assertEqual(self.order, ["wait", ("press", True), ("confirm", 3)])

    def test_nothing_when_it_no_longer_makes_sense(self):
        for name, kw, seq in (("受理済み", {"begin_done": True}, 3), ("ラウンド中", {"in_round": True}, 3),
                              ("次のラウンド", {}, 2)):
            ex, _st = self._executor(**kw)
            ex.do_begin_again(seq)
            self.assertEqual(self.order, [], name)
        ex, st = self._executor()
        st.instance_type = config.INSTANCE_PUBLIC
        ex.do_begin_again(3)
        self.assertEqual(self.order, [], "public")
        ex, _st = self._executor()
        self.running = False
        ex.do_begin_again(3)
        self.assertEqual(self.order, [], "停止中")

    def test_accepted_while_moving_does_not_press(self):
        ex, st = self._executor()
        ex.move_forward_left.side_effect = lambda *_a: setattr(st, "begin_done", True)
        ex.do_begin_again(3)
        self.assertNotIn(("press", True), self.order)

    def test_the_normal_begin_move_sets_the_mark(self):
        ex, st = self._executor()
        ex._begin_move()
        self.assertTrue(st.begin_move_done)




class TestBeginAdjustDepth(unittest.TestCase):
    """Begin の位置合わせの前後（文字の幅÷窓の高さ）と、見つからないときの探し方。
    撮影は差し替え（BeginDetect の結果を順に返す偽物。窓の高さ 1000px）"""

    AIM = (500.0, 400.0)
    H = 1000

    def setUp(self):
        self.moves = []
        self.logs = []
        self.hits = []
        cfg = WindowConfig(hwnd=0x100, osc_port=9000)
        self.st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=5, round_seq=1)
        self.ex = ActionExecutor.ActionExecutor(cfg, self.st, lambda: True, self.logs.append)
        for p in (patch.object(BeginDetect, "available", return_value=True),
                  patch.object(BeginDetect, "find", side_effect=self._find),
                  patch.object(WindowOperator, "aim_in_window_image", return_value=self.AIM),
                  patch.object(ScreenCapture, "capture_window", return_value=(b"\0" * 16, 2, self.H)),
                  patch.object(ActionExecutor.time, "sleep"),
                  patch.object(self.ex, "move", side_effect=self._move)):
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

    def test_the_direction_from_the_width(self):
        """押せる範囲（実測 0.066〜0.404）の内側 0.08〜0.33 は動かない。外なら前・後ろへ"""
        D = ActionExecutor.depth_direction
        for ratio in (0.07, 0.066, 0.065, 0.044, 0.0799):
            self.assertEqual(D(ratio), "forward", ratio)
        for ratio in (0.08, 0.103, 0.12, 0.2, 0.3, 0.33):
            self.assertIsNone(D(ratio), ratio)
        for ratio in (0.3301, 0.40, 0.404):
            self.assertEqual(D(ratio), "back", ratio)
        self.assertIsNone(D(None), "窓の高さが分からなければ前後は見ない")
        self.assertEqual(ActionExecutor.depth_target("forward"), 0.12)
        self.assertEqual(ActionExecutor.depth_target("back"), 0.25)

    def test_far_moves_forward_toward_0_12_and_stops_in_range(self):
        """0.040 → 前へ0.1秒で 0.060（0.2/秒）→ 0.12 へ向けて 0.30秒 → 範囲に入って止まる"""
        self.hits = [(0.0, 0.040), (0.0, 0.060), (0.0, 0.100)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("forward", 0.1), ("forward", 0.3)])
        self.assertTrue(any("位置合わせ（前後: 幅 0.040 → 目標 0.120）→ 前へ 0.10秒" in m for m in self.logs),
                        self.logs)

    def test_near_moves_back_toward_0_25(self):
        """0.40 → 後ろへ0.1秒で 0.36（0.4/秒）→ 0.25 へ向けて 0.275秒 → 0.26 で止まる"""
        self.hits = [(0.0, 0.40), (0.0, 0.36), (0.0, 0.26)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("back", 0.1), ("back", 0.275)])
        self.assertTrue(any("目標 0.250）→ 後ろへ" in m for m in self.logs), self.logs)

    def test_a_pressable_place_does_not_move(self):
        """依頼者「Begin が目の前で押せる状態でも後ろに下がる」: 0.2〜0.3 でも動かない"""
        for ratio in (0.30, 0.20, 0.12, 0.085):
            self.moves.clear()
            self.hits = [(0.1, ratio)]
            self.assertEqual(self.ex._adjust_to_begin(1), "aimed", ratio)
            self.assertEqual(self.moves, [], ratio)

    def test_one_step_into_the_range_stops(self):
        """合わせる先に届かなくても、範囲に入れば止まる"""
        self.hits = [(0.0, 0.070), (0.0, 0.086)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("forward", 0.1)])

    def test_a_different_walking_speed_still_reaches_the_range(self):
        """足が速い（Punished の後など）: 0.1秒で 0.04 変わる → 2回目はそれに合わせて短く"""
        self.hits = [(0.0, 0.030), (0.0, 0.070), (0.0, 0.110)]
        self.ex._adjust_to_begin(1)
        self.assertEqual(self.moves, [("forward", 0.1), ("forward", 0.125)])

    def test_horizontal_first_then_depth_then_horizontal_again(self):
        self.hits = [(2.58, 0.404), (1.54, 0.404), (0.09, 0.404), (0.09, 0.36), (0.5, 0.30), (0.05, 0.30)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual([d for d, _s in self.moves], ["right", "right", "back", "back", "right"])

    def test_the_horizontal_rate_is_in_text_widths(self):
        """横の速さは文字幅/秒: 1回目 0.1秒で 1.04 減 → 残り 1.54 は 0.148秒"""
        self.hits = [(2.58, 0.103), (1.54, 0.103), (0.05, 0.103)]
        self.ex._adjust_to_begin(1)
        self.assertEqual(self.moves, [("right", 0.1), ("right", 0.148)])

    def test_the_depth_step_count_and_total_are_capped(self):
        self.hits = [(0.0, 0.010 + 0.004 * i) for i in range(20)]
        with patch.object(config, "BEGIN_ADJUST_DEPTH_MAX_TOTAL_SEC", 100):
            self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(len(self.moves), config.BEGIN_ADJUST_DEPTH_MAX_STEPS)
        self.assertTrue(any("打ち切り" in m for m in self.logs))
        self.moves.clear()
        self.hits = [(0.0, 0.010 + 0.004 * i) for i in range(20)]
        with patch.object(config, "BEGIN_ADJUST_DEPTH_MAX_STEPS", 100):
            self.ex._adjust_to_begin(1)
        self.assertAlmostEqual(sum(s for _d, s in self.moves), config.BEGIN_ADJUST_DEPTH_MAX_TOTAL_SEC)
        self.assertTrue(all(s <= config.BEGIN_ADJUST_DEPTH_MAX_SEC for _d, s in self.moves))

    def test_no_depth_change_stops_it(self):
        self.hits = [(0.0, 0.070), (0.0, 0.071)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("forward", 0.1)])
        self.assertTrue(any("前後に動いていない" in m for m in self.logs), self.logs)

    # ── 見つからないとき ─────────────────────────────
    def test_too_near_is_found_by_stepping_back(self):
        self.hits = [None, (0.0, 0.104)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("back", 0.15)])

    def test_too_far_is_found_by_stepping_forward(self):
        """実機1: 前へ 0.3秒×3 で見つかった（幅 0.070）→ 範囲まで前へ"""
        self.hits = [None, None, None, None, (0.0, 0.070), (0.0, 0.086)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15), ("forward", 0.3), ("forward", 0.3),
                                      ("forward", 0.3), ("forward", 0.1)])

    def test_nothing_found_goes_back_and_gives_up_as_before(self):
        self.hits = [None] * 10
        self.assertEqual(self.ex._adjust_to_begin(1), "not_found")
        self.assertEqual(self.moves[-1], ("back", 1.2), "前へ動いた分を戻す")
        net = sum(s if d == "forward" else -s for d, s in self.moves)
        self.assertAlmostEqual(net, 0.0, msg="元の位置へ戻る")

    def test_the_window_height_goes_with_the_hit(self):
        self.hits = [(0.0, 0.103)]
        hit, _aim = self.ex._look_for_begin()
        self.assertEqual(hit["H"], self.H)

    # ── 見失ったら撮り直す・探し直す（1度だけ） ───────────────
    def test_losing_it_once_retakes_and_continues(self):
        self.hits = [(2.0, 0.10), None, (0.1, 0.10)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("right", 0.1)], "撮り直しで見つかれば探さない")

    def test_losing_it_twice_searches_again(self):
        """23:53 の回: 右へ 0.10秒の後に見失った → 撮り直しても無い → 探し直して見つける"""
        self.hits = [(2.0, 0.10), None, None, (0.1, 0.10)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("right", 0.1), ("back", 0.15)])
        self.assertTrue(any("見失いました → 探し直します" in m for m in self.logs), self.logs)

    def test_searching_again_only_once(self):
        self.hits = [(2.0, 0.10), None, None, (1.5, 0.10), None, None, (0.1, 0.10)]
        self.assertEqual(self.ex._adjust_to_begin(1), "aimed")
        self.assertEqual(self.moves, [("right", 0.1), ("back", 0.15), ("right", 0.1)],
                         "2回目に見失ったら探さずに打ち切る")
        self.assertTrue(any("BEGIN を見失った" in m for m in self.logs), self.logs)

    def test_searching_again_and_finding_nothing_is_not_found(self):
        self.hits = [(2.0, 0.10)] + [None] * 10
        self.assertEqual(self.ex._adjust_to_begin(1), "not_found")
        self.assertEqual(self.moves[-1], ("back", 1.2))

    # ── 受理・開始・停止が来たらその場で終わる ──────────────────
    def _accept_on_move(self, nth):
        count = {"n": 0}

        def move(direction, sec):
            self._move(direction, sec)
            count["n"] += 1
            if count["n"] == nth:
                self.st.begin_done = True
        self.ex.move.side_effect = move

    def test_an_acceptance_while_searching_forward_stops_without_going_back(self):
        """23:52 の回: 探して前へ動く間に通っていたのに、戻して前面化までしていた"""
        self.hits = [None] * 10
        self._accept_on_move(4)                     # back, forward(戻し), forward 0.3, forward 0.3 ← ここで受理
        self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.assertEqual(self.moves, [("back", 0.15), ("forward", 0.15), ("forward", 0.3), ("forward", 0.3)])

    def test_an_acceptance_just_before_going_back_does_not_go_back(self):
        self.hits = [None] * 10
        self._accept_on_move(6)                     # 最後の前へ 0.3秒の後に受理
        self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.assertNotIn(("back", 1.2), self.moves)

    def test_an_acceptance_while_aligning_stops(self):
        self.hits = [(2.0, 0.04), (1.0, 0.04), (0.1, 0.04)]
        self._accept_on_move(1)
        self.assertEqual(self.ex._adjust_to_begin(1), "skip")
        self.assertEqual(len(self.moves), 1)

    def test_an_acceptance_after_a_capture_stops(self):
        found = []

        def find(*a):
            found.append(1)
            self.st.begin_done = True               # 撮っている間に通った
            return None
        with patch.object(BeginDetect, "find", side_effect=find):
            self.st.begin_done = False
            self.hits = []
            self.assertEqual(self.ex._search_for_begin(1), "skip")
        self.assertEqual(self.moves, [("back", 0.15)], "戻さない")

    def test_an_acceptance_while_a_capture_finds_it_stops(self):
        """探して見つけた撮影の間に通っていたら、見つけた位置で合わせに入らず終わる"""
        def find(*a):
            self.st.begin_done = True
            return self._find(*a)
        with patch.object(BeginDetect, "find", side_effect=find):
            self.st.begin_done = False
            self.hits = [(2.0, 0.10)]
            self.assertEqual(self.ex._search_for_begin(1), "skip")
        self.assertEqual(self.moves, [("back", 0.15)])

    def test_a_skip_does_not_show_the_window(self):
        """探す途中で受理されたら、窓を前に出すだけ（_show_window_without_begin）もしない"""
        with patch.object(self.ex, "_adjust_to_begin", return_value="skip"), \
             patch.object(self.ex, "_show_window_without_begin") as show, \
             patch.object(self.ex, "_accepted_after_press", side_effect=[False, True]), \
             patch.object(self.ex, "_should_retry_begin", return_value=True), \
             patch.object(self.ex, "_click_begin_again", return_value=True):
            self.ex._confirm_begin(1)
        show.assert_not_called()




class TestBeginScoreMin(unittest.TestCase):
    """SCORE_MIN 0.57（BEGIN の無い画面の最大 0.54 より上、遠く斜めの本物 0.59 より下）"""

    def _find_with(self, score, dark=0.9):
        import numpy as np
        bgr = np.zeros((1080, 1920, 3), dtype=np.uint8)
        with patch.object(BeginDetect, "_coarse", return_value=[object()]), \
             patch.object(BeginDetect, "_fine", return_value=(score, 960.0, 300.0, 120.0, 30.0)), \
             patch.object(BeginDetect, "_darkness", return_value=dark), \
             patch.object(BeginDetect, "_redness", side_effect=lambda img: img[:, :, 0]):
            return BeginDetect._find(bgr, {"x": 1})

    def test_the_threshold(self):
        self.assertEqual(BeginDetect.SCORE_MIN, 0.57)
        self.assertIsNotNone(self._find_with(0.59), "遠く斜めの本物（2026-10-03）")
        self.assertIsNotNone(self._find_with(0.57))
        self.assertIsNone(self._find_with(0.54), "BEGIN の無い画面の最大")
        self.assertIsNone(self._find_with(0.569))
        self.assertIsNone(self._find_with(0.59, dark=0.5), "周りが黒くなければ拾わない（今のまま）")




class TestBeginStrongScore(unittest.TestCase):
    """近さ ≥ SCORE_STRONG（0.75）なら周りの黒さを見ない（実機で BEGIN を 0.79〜0.87 で見つけているのに
    黒さ 0.31〜0.64 で捨てていた）。今の「近さ ≥ 0.57 かつ 黒さ ≥ 0.65」も残す"""

    def _find(self, *candidates):
        """candidates は (近さ, 黒さ)。候補ごとに位置をずらす"""
        import numpy as np
        bgr = np.zeros((1080, 1920, 3), dtype=np.uint8)
        fine = [(sc, 100.0 + 100 * i, 300.0, 120.0, 30.0) for i, (sc, _d) in enumerate(candidates)]
        dark = [d for _sc, d in candidates]
        with patch.object(BeginDetect, "_coarse", return_value=[object()] * len(candidates)), \
             patch.object(BeginDetect, "_fine", side_effect=fine), \
             patch.object(BeginDetect, "_darkness", side_effect=dark), \
             patch.object(BeginDetect, "_redness", side_effect=lambda img: img[:, :, 0]):
            return BeginDetect._find(bgr, {"x": 1})

    def test_the_threshold(self):
        self.assertEqual(BeginDetect.SCORE_STRONG, 0.75)

    def test_clear_begins_in_a_bright_lobby_are_taken(self):
        for score, dark, name in ((0.805, 0.586, "窓5"), (0.793, 0.306, "窓6"), (0.869, 0.522, "窓4"),
                                  (0.75, 0.0, "ちょうど")):
            hit = self._find((score, dark))
            self.assertIsNotNone(hit, name)
            self.assertEqual((hit["score"], hit["dark"], hit["rule"]), (score, dark, "strong"), name)

    def test_walls_and_weak_ones_are_not(self):
        for score, dark, name in ((0.64, 0.43, "壁"), (0.74, 0.50, "強くない・黒くない"),
                                  (0.749, 0.649, "どちらもわずかに足りない")):
            self.assertIsNone(self._find((score, dark)), name)

    def test_the_old_rule_stays(self):
        hit = self._find((0.58, 0.70))
        self.assertIsNotNone(hit)
        self.assertEqual(hit["rule"], "dark")
        self.assertEqual(self._find((0.80, 0.90))["rule"], "dark", "両方満たすなら今の規則の名前")
        self.assertIsNone(self._find((0.56, 0.90)), "近さが足りなければ黒くても拾わない")

    def test_the_closest_acceptable_one_wins(self):
        hit = self._find((0.64, 0.43), (0.79, 0.30), (0.60, 0.80), (0.70, 0.40))
        self.assertEqual(hit["score"], 0.79, "受け入れられるものの中で近さが最大")
        self.assertEqual(hit["cx"], 200.0)
        hit = self._find((0.62, 0.90), (0.74, 0.60))
        self.assertEqual(hit["score"], 0.62, "受け入れられない 0.74 より、受け入れられる 0.62")
        hit = self._find((0.60, 0.80), (0.86, 0.50), (0.81, 0.30))
        self.assertEqual((hit["score"], hit["cx"]), (0.86, 200.0), "先に来た弱いものより、後の強いもの")




class TestVerifiedPhase(unittest.TestCase):
    """ツールが Begin を押す窓で、押した記録の無い Verified が Verified Round End の後に来たとき、定期の
    位相を知っていて予定（±TOL）に重ならなければ受理する。位相を知らない・予定に重なるなら今どおり無視。
    無視した Verified の後 15 秒以内にラウンドが始まったら、覚えた位相を元に戻す"""

    BASE = datetime(2026, 10, 3, 20, 8, 30).timestamp()
    NOW = 1_000_000.0
    START = "This round is taking place at Sewers (12) and the round type is Classic"

    def _monitor(self, auto_begin=True, phase=None):
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=auto_begin, osc_port=9000), {},
                                        lambda _m: None, window_idx=5)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._verified.last_periodic = phase
        self.debug = []
        monitor._debug = self.debug.append
        return monitor

    def _feed(self, monitor, at, body):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(SharedState, "get_speed_detect", return_value=False), \
             patch.object(LogMonitor.time, "time", return_value=self.NOW), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            monitor._process(datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S")
                             + " Debug      -  " + body)

    def _verified_after_round_end(self, monitor, at=None):
        """窓5 20:08:43: Verified Round End の 0.3 秒後に Verified（ツールが押した記録なし）"""
        self._feed(monitor, self.BASE, "RoundOver")
        self._feed(monitor, self.BASE + 13, "Verified Round End")
        self._feed(monitor, at or self.BASE + 13, "Verified")

    def test_window_5_off_schedule_is_accepted(self):
        monitor = self._monitor(phase=self.BASE - 100)      # 予定は BASE+200
        self._verified_after_round_end(monitor)
        self.assertTrue(monitor.st.begin_done)
        self.assertIn("[事象] verified → 受理（予定の外）", self.debug)
        self.assertEqual(monitor._verified.last_periodic, self.BASE - 100, "位相は動かさない")
        ex = monitor._action
        with patch.object(ex, "_adjust_to_begin") as adjust, \
             patch.object(BeginMiss, "save") as save:
            ex._confirm_begin(monitor.st.round_seq)
        adjust.assert_not_called()
        save.assert_not_called()

    def test_on_schedule_is_periodic(self):
        monitor = self._monitor(phase=self.BASE + 13 - 300)  # ちょうど予定
        self._verified_after_round_end(monitor)
        self.assertFalse(monitor.st.begin_done)
        self.assertIn("[事象] verified → 無視（定期）", self.debug)
        self.assertEqual(monitor._verified.last_periodic, self.BASE + 13)

    def test_within_the_tolerance_is_periodic(self):
        monitor = self._monitor(phase=self.BASE + 13 - 300 - config.VERIFIED_PERIODIC_TOL_SEC)
        self._verified_after_round_end(monitor)
        self.assertFalse(monitor.st.begin_done)

    def test_an_unknown_phase_is_ignored_as_in_co(self):
        monitor = self._monitor(phase=None)
        self._verified_after_round_end(monitor)
        self.assertFalse(monitor.st.begin_done)
        self.assertIn("[事象] verified → 無視（ツールがまだ押していない）", self.debug)
        self.assertEqual(monitor._verified.last_periodic, self.BASE + 13)

    def test_a_round_start_12s_later_undoes_the_learned_phase(self):
        for phase, name in ((None, "位相を知らなかった"), (self.BASE + 13 - 300, "予定と重なった")):
            monitor = self._monitor(phase=phase)
            self._verified_after_round_end(monitor)
            self._feed(monitor, self.BASE + 25, self.START)
            self.assertEqual(monitor._verified.last_periodic, phase, name)
            self.assertTrue(any("無視した Verified の 12.0秒後にラウンド開始" in m for m in self.debug), name)

    def test_no_round_start_keeps_it(self):
        monitor = self._monitor(phase=None)
        self._verified_after_round_end(monitor)
        self._feed(monitor, self.BASE + 40, "[Behaviour] tick")
        self.assertEqual(monitor._verified.last_periodic, self.BASE + 13)

    def test_a_late_round_start_keeps_it(self):
        monitor = self._monitor(phase=None)
        self._verified_after_round_end(monitor)
        self._feed(monitor, self.BASE + 13 + config.VERIFIED_ROUND_START_WAIT_SEC + 1, self.START)
        self.assertEqual(monitor._verified.last_periodic, self.BASE + 13, "15 秒より後は本物とは言えない")

    def test_it_is_undone_only_once(self):
        monitor = self._monitor(phase=None)
        self._verified_after_round_end(monitor)
        self._feed(monitor, self.BASE + 25, self.START)
        monitor._verified.last_periodic = 123.0
        self._feed(monitor, self.BASE + 26, self.START)
        self.assertEqual(monitor._verified.last_periodic, 123.0)

    def test_a_pressed_one_is_accepted_as_before(self):
        monitor = self._monitor(phase=None)
        monitor.st.last_begin_press_at = self.NOW - 1
        self._verified_after_round_end(monitor)
        self.assertTrue(monitor.st.begin_done)
        self.assertIn("[事象] verified → 受理", self.debug)

    def test_windows_the_tool_does_not_press_are_as_before(self):
        for phase in (None, self.BASE - 100):
            monitor = self._monitor(auto_begin=False, phase=phase)
            self._verified_after_round_end(monitor)
            self.assertTrue(monitor.st.begin_done, phase)
            self.assertIn("[事象] verified → 受理", self.debug, phase)




class TestCursorOverWindow(unittest.TestCase):
    """裏の窓のワールドUIは「カーソルが Begin のボタンの上にある」と押せる。

    ボタンはデスクトップの照準の位置＝クライアント領域の中央にある。矩形の中
    ならどこでもよい、ではない（実測 2026-09-27。窓の隅では押せなかった）。
    前面化は要らないので、この仕組みは SetForegroundWindow を一切呼ばない。
    """

    RECT = (100, 200, 1000, 800)        # クライアント領域（スクリーン座標）

    @staticmethod
    def center(rect):
        return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)

    def _window(self, user32=None, rect=None, iconic=False):
        rect = rect or self.RECT
        return (patch.object(WindowOperator, "user32", user32 or FakeUser32()),
                patch.object(WindowOperator.win32gui, "GetClientRect",
                             return_value=(0, 0, rect[2] - rect[0], rect[3] - rect[1])),
                patch.object(WindowOperator.win32gui, "ClientToScreen",
                             return_value=(rect[0], rect[1])),
                patch.object(WindowOperator.win32gui, "IsIconic", return_value=iconic))

    @contextlib.contextmanager
    def _patched(self, **kwargs):
        patches = self._window(**kwargs)
        for p in patches:
            p.start()
        try:
            yield
        finally:
            for p in patches:
                p.stop()

    def test_the_point_is_the_centre_of_the_client_area(self):
        """窓の左上ではなく中央。左上（相対 1.5%, 4.3%）では押せなかった"""
        with self._patched():
            self.assertEqual(WindowOperator.window_cursor_point(123),
                             self.center(self.RECT))

    def test_the_title_bar_is_not_counted(self):
        """クライアント領域で測る。窓枠を含めると照準から縦にずれる"""
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(WindowOperator, "user32", FakeUser32()))
            stack.enter_context(patch.object(WindowOperator.win32gui, "IsIconic",
                                             return_value=False))
            stack.enter_context(patch.object(WindowOperator.win32gui, "GetClientRect",
                                             return_value=(0, 0, 900, 570)))
            stack.enter_context(patch.object(WindowOperator.win32gui, "ClientToScreen",
                                             return_value=(108, 230)))
            # 窓矩形が (100,200)-(1000,800) でも、枠とタイトルバーの分だけ違う
            self.assertEqual(WindowOperator.window_cursor_point(123), (558, 515))

    def test_the_offset_shifts_from_the_centre(self):
        with self._patched(), patch.object(config, "BEGIN_CURSOR_OFFSET", (30, -40)):
            cx, cy = self.center(self.RECT)
            self.assertEqual(WindowOperator.window_cursor_point(123), (cx + 30, cy - 40))

    def test_the_offset_cannot_leave_the_window(self):
        with self._patched(), patch.object(config, "BEGIN_CURSOR_OFFSET", (9999, -9999)):
            x, y = WindowOperator.window_cursor_point(123)
        self.assertEqual((x, y), (self.RECT[2] - 1, self.RECT[1]))

    def test_a_small_window_still_gets_a_point_inside(self):
        with self._patched(rect=(0, 0, 10, 10)):
            self.assertEqual(WindowOperator.window_cursor_point(123), (5, 5))

    def test_a_minimized_window_has_no_point(self):
        with self._patched(iconic=True):
            self.assertIsNone(WindowOperator.window_cursor_point(123))

    def test_a_window_off_screen_has_no_point(self):
        for rect in ((5000, 200, 5900, 800), (100, -3000, 1000, -2400),
                     (100, 200, 100, 800)):
            with self._patched(rect=rect):
                self.assertIsNone(WindowOperator.window_cursor_point(123), rect)

    def test_no_hwnd_has_no_point(self):
        with self._patched():
            self.assertIsNone(WindowOperator.window_cursor_point(0))

    def test_the_cursor_moves_in_and_comes_back(self):
        user32 = FakeUser32(cursor=(42, 43))
        with self._patched(user32=user32):
            with WindowOperator.cursor_over_window(123) as over:
                self.assertTrue(over)
                self.assertEqual(user32.cursor, self.center(self.RECT))

        self.assertEqual(user32.cursor, (42, 43), "元の位置へ戻す")
        self.assertEqual(len(user32.moves), 2)

    def test_the_cursor_comes_back_after_an_exception(self):
        user32 = FakeUser32(cursor=(42, 43))
        with self._patched(user32=user32):
            with self.assertRaises(RuntimeError):
                with WindowOperator.cursor_over_window(123):
                    raise RuntimeError("押している途中で落ちた")

        self.assertEqual(user32.cursor, (42, 43))

    def test_a_window_without_a_point_moves_nothing(self):
        user32 = FakeUser32(cursor=(42, 43))
        with self._patched(user32=user32, iconic=True):
            with WindowOperator.cursor_over_window(123) as over:
                self.assertFalse(over)

        self.assertEqual(user32.moves, [], "動かさない")

    def test_it_never_brings_the_window_to_the_front(self):
        user32 = FakeUser32()
        with self._patched(user32=user32), \
             patch.object(WindowOperator, "focus_window") as focus, \
             patch.object(WindowOperator, "_attach_and_raise") as raise_:
            with WindowOperator.cursor_over_window(123):
                pass

        focus.assert_not_called()
        raise_.assert_not_called()




class TestWaitForTheAcceptance(unittest.TestCase):
    """押せているのに前面化＋クリックするのをやめる。

    実機のログ: 「カーソルを一瞬合わせる」→「✅ Connecting」→ なのに
    「フォーカス切替」「Beginクリック」。差し込みは往復1回＝0.05秒で終わるのに
    受理はその後に来るので、待たずに判定すると押せていても必ず
    「押せなかった」ことになっていた。
    """

    RECT = (100, 200, 1000, 800)
    HWND = 246

    def setUp(self):
        SharedState.clear_window_hwnds()
        for name in ("EQUIP_WAIT_EVENT", "CONTINUE_ROUND_EVENT",
                     "SPEED_FREEZE_EVENT", "ROUND_FREEZE_EVENT"):
            getattr(SharedState, name).set()
        self.addCleanup(SharedState.clear_window_hwnds)

    def _executor(self, logs=None):
        cfg = WindowConfig(hwnd=self.HWND, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=0,
                         round_seq=7)
        return ActionExecutor.ActionExecutor(
            cfg, st, lambda: True,
            logs.append if logs is not None else (lambda _m: None)), st

    def _press(self, executor, on_sleep=None, lock_hook=None, press_wait=None):
        """実際の _press_begin を流す。戻り値と focus/click の呼ばれ方を見る"""
        user32 = FakeUser32()
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=False))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=(0, 0, 900, 600)))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=(100, 200)))
            enter(patch.object(WindowOperator, "foreground_hwnd", return_value=0))
            enter(patch.object(OSCClient.OSCClient, "press", return_value=True))
            if press_wait is not None:
                enter(patch.object(config, "BEGIN_RETRY_WAIT_SEC", press_wait))
            # time モジュールは ActionExecutor と WindowOperator で同じものなので、
            # patch は1つだけにする（2つ当てると後の方が side_effect を潰す）
            enter(patch.object(ActionExecutor.time, "sleep",
                               side_effect=on_sleep))
            focus = enter(patch.object(WindowOperator, "focus_window",
                                       return_value=True))
            click = enter(patch.object(WindowOperator, "click"))
            if lock_hook is not None:
                enter(patch.object(SharedState, "_GLOBAL_ACTION_LOCK", lock_hook))
            ok = executor._press_begin()
        return ok, focus, click, user32

    # ── 1・6. 待てば押せている ────────────────────
    def test_an_acceptance_soon_after_the_dip_takes_no_focus(self):
        logs = []
        executor, st = self._executor(logs)
        slept = []

        def accept(sec):
            slept.append(sec)
            if len(slept) >= 4:            # 0.05 × 4 = 0.2 秒ほどで届く
                st.begin_done = True

        ok, focus, click, _u = self._press(executor, on_sleep=accept)

        self.assertTrue(ok, "受理扱い")
        focus.assert_not_called()
        click.assert_not_called()
        self.assertFalse(any("Beginクリック" in m for m in logs), logs)

    def test_an_immediate_acceptance_takes_no_focus(self):
        executor, st = self._executor()

        def accept(_sec):
            st.begin_done = True

        ok, focus, click, _u = self._press(executor, on_sleep=accept)

        self.assertTrue(ok)
        focus.assert_not_called()
        click.assert_not_called()

    # ── 2. 来なければフォールバック ──────────────────
    def test_no_acceptance_ends_the_turn_without_a_click(self):
        """受理が来なくても、差し込めた回はその回のうちにクリックへ落ちない（次の回は位置合わせの後。受理待ちは上限で抜ける）"""
        executor, _st = self._executor()
        started = time.time()

        ok, focus, click, user32 = self._press(executor, on_sleep=None,
                                              press_wait=0.2)

        self.assertTrue(ok)
        focus.assert_not_called()
        click.assert_not_called()
        self.assertTrue(user32.moves, "差し込みはしている")
        self.assertLess(time.time() - started, 5.0)

    # ── 3. 途中で抜ける ──────────────────────
    def test_a_started_round_stops_the_wait(self):
        executor, st = self._executor()
        slept = []

        def start_round(sec):
            slept.append(sec)
            st.in_round = True

        ok, focus, click, _u = self._press(executor, on_sleep=start_round)

        self.assertFalse(ok, "ラウンドが始まったら押さない")
        focus.assert_not_called()
        click.assert_not_called()
        self.assertLessEqual(len(slept), 3, "すぐ抜けること")

    def test_a_new_round_seq_stops_the_wait(self):
        """別のラウンドになったら、上限を待たずに抜けること"""
        executor, st = self._executor()
        started = time.time()

        def bump(_sec):
            st.round_seq += 1

        with patch.object(config, "BEGIN_RETRY_WAIT_SEC", 1.0), \
             patch.object(ActionExecutor.time, "sleep", side_effect=bump):
            self.assertFalse(executor._wait_begin_accepted(st.round_seq))

        self.assertLess(time.time() - started, 0.5, "上限まで待たないこと")

    def test_a_started_round_stops_the_wait_promptly(self):
        executor, st = self._executor()
        started = time.time()

        def start_round(_sec):
            st.in_round = True

        with patch.object(config, "BEGIN_RETRY_WAIT_SEC", 1.0), \
             patch.object(ActionExecutor.time, "sleep", side_effect=start_round):
            self.assertFalse(executor._wait_begin_accepted(st.round_seq))

        self.assertLess(time.time() - started, 0.5)

    def test_a_stop_ends_the_wait(self):
        executor, st = self._executor()
        running = [True, True, True]
        executor._is_running = lambda: bool(running) and running.pop()

        ok, focus, click, _u = self._press(executor)

        self.assertFalse(ok)
        focus.assert_not_called()
        click.assert_not_called()

    # ── 4. ロック待ちの間に受理された ──────────────────
    def _fallback(self, executor, lock, logs=None):
        """差し込みを見送らせて（最小化）、フォールバックの入口だけを通す"""
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=True))
            enter(patch.object(WindowOperator, "user32", FakeUser32()))
            enter(patch.object(WindowOperator, "foreground_hwnd", return_value=0))
            focus = enter(patch.object(WindowOperator, "focus_window",
                                       return_value=True))
            click = enter(patch.object(WindowOperator, "click"))
            enter(patch.object(SharedState, "_GLOBAL_ACTION_LOCK", lock))
            enter(patch.object(ActionExecutor.time, "sleep"))
            if logs is not None:
                executor._log = logs.append
            ok = executor._press_begin()
        return ok, focus, click

    def test_an_acceptance_while_waiting_for_the_lock_cancels_the_click(self):
        """6窓では他窓のクリックを待つ間に受理が届く。そこで気づくこと。

        差し込みもロックを取るので、受理が届くのは2回目に入ったとき＝
        フォールバックがロックを取ったときにする
        """
        logs = []
        executor, st = self._executor()
        entered = []

        class LockThatAcceptsOnTheSecondEntry:
            def __enter__(self_inner):
                entered.append(1)
                if len(entered) >= 2:
                    st.begin_done = True
                return self_inner

            def __exit__(self_inner, *_a):
                return False

        ok, focus, click = self._fallback(
            executor, LockThatAcceptsOnTheSecondEntry(), logs)

        self.assertTrue(ok)
        self.assertEqual(len(entered), 2, "差し込みとフォールバックで1回ずつ")
        focus.assert_not_called()
        click.assert_not_called()
        self.assertTrue(any("受理されたので前面化しません" in m for m in logs), logs)

    def test_an_acceptance_before_the_lock_skips_it_entirely(self):
        """差し込みを見送った直後に受理が届いたら、ロック待ちに入らない。

        入ると、6窓では他窓のクリックを待つ間ずっと止まる
        """
        logs = []
        executor, st = self._executor()
        entered = []

        class WatchedLock:
            def __enter__(self_inner):
                entered.append(1)
                return self_inner

            def __exit__(self_inner, *_a):
                return False

        def log(message):
            logs.append(message)
            if "最小化" in message:
                st.begin_done = True     # 見送った直後に受理が届いた

        executor._log = log
        ok, focus, click = self._fallback(executor, WatchedLock())

        self.assertTrue(ok)
        self.assertEqual(len(entered), 1, "差し込みの1回だけ。もう取らない")
        focus.assert_not_called()
        click.assert_not_called()

    # ── 5. 待ちは上限で止まる ─────────────────────
    def test_the_wait_does_not_exceed_the_limit(self):
        executor, st = self._executor()
        started = time.time()

        with patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0.3):
            self.assertFalse(executor._wait_begin_accepted(st.round_seq))

        elapsed = time.time() - started
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 1.5, "上限を大きく超えないこと")

    def test_the_limit_leaves_room_for_the_answer(self):
        """押してから Verified までは実測 0.2 秒ほど。余裕を見た長さであること。

        値そのものは調整の余地があるので固定しない（短くしすぎると、押せて
        いるのに前面化へ落ちる元の不具合に戻る）
        """
        self.assertGreaterEqual(config.BEGIN_RETRY_WAIT_SEC, 0.5)

    def test_an_already_accepted_begin_returns_true_at_once(self):
        executor, st = self._executor()
        st.begin_done = True
        started = time.time()

        self.assertTrue(executor._wait_begin_accepted(st.round_seq))

        self.assertLess(time.time() - started, 0.2, "待たない")




class TestCursorGiveUpReasons(unittest.TestCase):
    """カーソル方式を見送ったら、必ず理由を1行出す。

    無言で落ちると原因を絞れない。実機で「カーソルも動かさず前面化＋クリック
    している」が起きたとき、候補3つ（点が出せない／SetCursorPos 失敗／
    読み返し不一致）のどれかを切り分けられなかった。
    """

    HWND = 123
    RECT = (100, 200, 1000, 800)
    CENTRE = (550, 500)

    def _over(self, user32=None, iconic=False, client=None, screen=None,
              at_point=None, title=""):
        """cursor_over_window を回して (置けたか, 出た理由) を返す"""
        user32 = user32 or FakeUser32()
        reasons = []
        client = client if client is not None else (
            0, 0, self.RECT[2] - self.RECT[0], self.RECT[3] - self.RECT[1])
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=iconic))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=client))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=screen or (self.RECT[0], self.RECT[1])))
            enter(patch.object(WindowOperator, "window_at_point",
                               return_value=at_point if at_point is not None
                               else self.HWND))
            enter(patch.object(WindowOperator, "window_title", return_value=title))
            enter(patch.object(WindowOperator.time, "sleep"))
            with WindowOperator.cursor_over_window(self.HWND,
                                                   reasons.append) as over:
                pass
        return over, reasons

    # ── 4. 3つの見送りに固有のログ ───────────────────
    def test_a_minimized_window_says_so(self):
        over, reasons = self._over(iconic=True)

        self.assertFalse(over)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("最小化", reasons[0])

    def test_a_zero_client_area_says_so(self):
        over, reasons = self._over(client=(0, 0, 0, 0))

        self.assertFalse(over)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("クライアント領域が0", reasons[0])

    def test_an_off_screen_window_says_where(self):
        over, reasons = self._over(screen=(9000, 9000))

        self.assertFalse(over)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("画面外", reasons[0])
        self.assertIn("9450", reasons[0], "頼んだ点を添える")
        self.assertIn("2560", reasons[0], "画面の範囲も添える")

    def test_a_failed_setcursorpos_says_so_with_the_error(self):
        over, reasons = self._over(user32=FakeUser32(set_ok=False))

        self.assertFalse(over)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("SetCursorPos 失敗", reasons[0])
        self.assertIn("err=", reasons[0])

    def test_a_cursor_that_did_not_land_says_both_points(self):
        user32 = FakeUser32()
        real = user32.SetCursorPos

        def stuck(x, y):
            real(x, y)
            user32.cursor = (7, 9)
            return 1

        user32.SetCursorPos = stuck

        over, reasons = self._over(user32=user32)

        self.assertFalse(over)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("置けていません", reasons[0])
        self.assertIn("(550, 500)", reasons[0], "頼んだ点")
        self.assertIn("(7, 9)", reasons[0], "実際の位置")

    def test_the_reasons_are_all_different(self):
        """どの経路で落ちたか、文面で見分けられること"""
        seen = set()
        for kwargs in ({"iconic": True}, {"client": (0, 0, 0, 0)},
                       {"screen": (9000, 9000)},
                       {"user32": FakeUser32(set_ok=False)}):
            _over, reasons = self._over(**kwargs)
            seen.add(reasons[0])

        self.assertEqual(len(seen), 4, seen)

    def test_a_good_placement_says_nothing(self):
        over, reasons = self._over()

        self.assertTrue(over)
        self.assertEqual(reasons, [])

    # ── 6〜7. 覆われていても差し込む（2026-09-27 に判定を撤回）─────────
    def test_a_covered_point_is_still_used(self):
        """覆われていても Begin は押せる。判定を入れてはいけない。

        VS Code が全画面で6窓すべてを覆っていても動いていた実例があり、
        2026-09-25 の実測（別の窓が上に重なっていても押せる）とも一致する。
        判定を入れると、当ツールの GUI 自身が差し込み点を覆っている窓
        （実測で窓1と窓4）が毎ラウンド前面化＋クリックへ落ちる
        """
        user32 = FakeUser32()

        over, reasons = self._over(user32=user32, at_point=999,
                                   title="notes.txt - メモ帳")

        self.assertTrue(over, "覆われていても差し込む")
        self.assertEqual(user32.moves, [(550, 500), (500, 500)],
                         "置いて戻す")
        self.assertEqual(reasons, [], "覆いを理由に見送らない")

    def test_nothing_says_the_point_is_covered(self):
        for at_point in (999, 0, self.HWND):
            _over, reasons = self._over(at_point=at_point, title="何かの窓")

            self.assertFalse(any("覆われ" in r for r in reasons),
                             (at_point, reasons))

    def test_the_source_does_not_test_for_a_covering_window(self):
        """将来また同じ判定を入れないための見張り"""
        src = Path(WindowOperator.__file__).read_text(encoding="utf-8")
        body = src[src.index("def cursor_over_window"):]
        body = body[:body.index("\ndef ", 10)]

        self.assertNotIn("window_at_point", body)
        self.assertNotIn("window_title", body)

    def test_an_uncovered_point_still_dips(self):
        over, reasons = self._over(at_point=self.HWND)

        self.assertTrue(over)
        self.assertEqual(reasons, [])

    def test_an_unknown_window_at_the_point_does_not_block_it(self):
        over, _reasons = self._over(at_point=0)

        self.assertTrue(over)

    # ── 1〜2. 読み返しの順番 ─────────────────────
    def test_the_readback_comes_before_the_dwell(self):
        """待ってから読むと、その間に手でマウスを動かされて誤判定する"""
        order = []
        user32 = FakeUser32()
        real_set, real_get = user32.SetCursorPos, user32.GetCursorPos
        user32.SetCursorPos = lambda x, y: (order.append("set"),
                                            real_set(x, y))[1]
        user32.GetCursorPos = lambda ref: (order.append("get"),
                                           real_get(ref))[1]

        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=False))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=(0, 0, 900, 600)))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=(100, 200)))
            enter(patch.object(WindowOperator, "window_at_point",
                               return_value=self.HWND))
            enter(patch.object(WindowOperator.time, "sleep",
                               side_effect=lambda _s: order.append("sleep")))
            with WindowOperator.cursor_over_window(self.HWND):
                pass

        # before の読み取り → 置く → 読み返す → 滞在 → 戻す
        self.assertEqual(order, ["get", "set", "get", "sleep", "set"], order)

    def test_a_missed_placement_does_not_dwell(self):
        order = []
        user32 = FakeUser32()
        real = user32.SetCursorPos

        def stuck(x, y):
            real(x, y)
            user32.cursor = (7, 9)
            order.append("set")
            return 1

        user32.SetCursorPos = stuck

        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=False))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=(0, 0, 900, 600)))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=(100, 200)))
            enter(patch.object(WindowOperator, "window_at_point",
                               return_value=self.HWND))
            enter(patch.object(WindowOperator.time, "sleep",
                               side_effect=lambda _s: order.append("sleep")))
            with WindowOperator.cursor_over_window(self.HWND) as over:
                self.assertFalse(over)

        self.assertNotIn("sleep", order, "置けていないなら滞在しない")

    # ── 5. 同じ理由を繰り返さない ───────────────────
    def _executor(self, logs):
        cfg = WindowConfig(hwnd=self.HWND, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=0)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True,
                                             logs.append), st

    def test_the_same_reason_is_logged_once_a_round(self):
        logs = []
        ex, st = self._executor(logs)

        for _ in range(5):
            ex._log_cursor_reason("カーソルを置けません（最小化）")

        self.assertEqual(len([m for m in logs if "最小化" in m]), 1, logs)

    def test_a_different_reason_is_still_logged(self):
        logs = []
        ex, _st = self._executor(logs)

        ex._log_cursor_reason("カーソルを置けません（最小化）")
        ex._log_cursor_reason("カーソルを動かせません（SetCursorPos 失敗 err=5）")

        self.assertEqual(len(logs), 2, logs)

    def test_the_next_round_says_it_again(self):
        logs = []
        ex, st = self._executor(logs)

        ex._log_cursor_reason("カーソルを置けません（最小化）")
        st.round_seq += 1
        ex._log_cursor_reason("カーソルを置けません（最小化）")

        self.assertEqual(len(logs), 2, logs)

    def test_the_reason_is_prefixed(self):
        logs = []
        ex, _st = self._executor(logs)

        ex._log_cursor_reason("カーソルを置けません（最小化）")

        self.assertTrue(logs[0].startswith("Begin: "), logs)

    def test_the_dip_logs_the_reason_before_falling_back(self):
        """無言で前面化＋クリックへ落ちないこと"""
        logs = []
        ex, _st = self._executor(logs)

        with patch.object(WindowOperator.win32gui, "IsIconic",
                          return_value=True), \
             patch.object(WindowOperator, "user32", FakeUser32()), \
             patch.object(ActionExecutor.time, "sleep"):
            self.assertFalse(ex._dip_cursor_for_begin(""))

        self.assertTrue(any("最小化" in m for m in logs), logs)




class TestDipRetry(unittest.TestCase):
    """カーソルの差し込みを1回だけやり直す（1回目＋やり直し1回）。

    やり直すのは一時的な失敗だけ: 受理が来ない・SetCursorPos が効かない・読み返しが
    ずれる。最小化・画面外は何度やっても同じなのでやり直さない。2回目の前に VRChat が
    前面になっていたら、カーソルに触らない（前面の窓のカメラを回さないため）。
    """

    HWND = 321
    CENTRE = (550, 500)
    HOME = (500, 500)

    def setUp(self):
        SharedState.clear_window_hwnds()
        self.addCleanup(SharedState.clear_window_hwnds)

    def _executor(self, logs=None):
        cfg = WindowConfig(hwnd=self.HWND, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=0,
                         round_seq=3)
        ex = ActionExecutor.ActionExecutor(
            cfg, st, lambda: True,
            logs.append if logs is not None else (lambda _m: None))
        return ex, st

    def _dip(self, ex, user32=None, accept_on=None, iconic=False, front=0,
             each_wait=None, wait_sec=0.0):
        """_dip_cursor_for_begin を回す。accept_on 回目の差し込みで受理が来る。

        each_wait(n): n 回目の受理待ちが始まったときに呼ぶ（間に何かを起こす用）
        """
        user32 = user32 or FakeUser32()
        dips = {"n": 0}
        waits = {"n": 0}
        real_wait = ex._wait_begin_accepted
        st = ex._st
        real_set = user32.SetCursorPos

        def on_press(_addr, _sec):
            return True

        def set_cursor(x, y):
            # 差し込みは「中央へ置いた回数」で数える。待ちの秒数で数えると、
            # WindowOperator 側の待ちも同じ 0.05 秒なので1回を2回に数える
            ok = real_set(x, y)
            if (x, y) == self.CENTRE and ok:
                dips["n"] += 1
                if accept_on is not None and dips["n"] >= accept_on:
                    st.begin_done = True
            return ok

        user32.SetCursorPos = set_cursor

        def wait(round_seq):
            waits["n"] += 1
            if each_wait is not None:
                each_wait(waits["n"])
            return real_wait(round_seq)

        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=iconic))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=(0, 0, 900, 600)))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=(100, 200)))
            enter(patch.object(WindowOperator, "foreground_hwnd",
                               side_effect=front if callable(front) else
                               (lambda: front)))
            enter(patch.object(OSCClient.OSCClient, "press", side_effect=on_press))
            enter(patch.object(config, "BEGIN_RETRY_WAIT_SEC", wait_sec))
            slept = []
            enter(patch.object(ActionExecutor.time, "sleep", side_effect=slept.append))
            enter(patch.object(ex, "_wait_begin_accepted", side_effect=wait))
            ok = ex._dip_cursor_for_begin("")
        return ok, user32, slept, waits["n"]

    # ── 1〜3. 受理 ───────────────────────────
    def test_accepted_at_once_dips_only_once(self):
        logs = []
        ex, _st = self._executor(logs)

        ok, user32, _slept, waits = self._dip(ex, accept_on=1)

        self.assertTrue(ok)
        self.assertEqual(user32.moves, [self.CENTRE, self.HOME], "1往復だけ")
        self.assertEqual(waits, 1)
        self.assertFalse(any("もう一度" in m for m in logs), logs)

    def test_no_acceptance_tries_once_more(self):
        logs = []
        ex, _st = self._executor(logs)

        ok, user32, _slept, waits = self._dip(ex, accept_on=2)

        self.assertTrue(ok)
        self.assertEqual(user32.moves, [self.CENTRE, self.HOME] * 2, "2往復")
        self.assertEqual(waits, 2)
        self.assertEqual([m for m in logs if "もう一度" in m],
                         ["Begin: カーソルをもう一度合わせる"], "やり直しのログは1行")

    def test_two_misses_fall_back_after_exactly_two_dips(self):
        ex, _st = self._executor()

        ok, user32, _slept, waits = self._dip(ex, accept_on=None)

        self.assertFalse(ok, "フォールバックへ")
        self.assertEqual(user32.moves.count(self.CENTRE), 2, "差し込みはちょうど2回")
        self.assertEqual(waits, 2)

    # ── 4. 置けなかった（一時的） ────────────────────
    def test_a_failed_setcursorpos_waits_the_gap_and_retries(self):
        ex, _st = self._executor()
        user32 = FakeUser32()
        real = user32.SetCursorPos
        calls = {"n": 0}

        def flaky(x, y):
            calls["n"] += 1
            if calls["n"] == 1:
                return 0                      # 1回目だけ効かない
            return real(x, y)

        user32.SetCursorPos = flaky

        ok, _u, slept, _waits = self._dip(ex, user32=user32, accept_on=1)

        self.assertTrue(ok)
        self.assertIn(config.BEGIN_CURSOR_GAP_SEC, slept, "少しおいてから")

    def test_a_readback_mismatch_waits_the_gap_and_retries(self):
        ex, _st = self._executor()
        user32 = FakeUser32()
        real = user32.SetCursorPos
        calls = {"n": 0}

        def moved_by_hand(x, y):
            calls["n"] += 1
            real(x, y)
            if calls["n"] == 1:
                user32.cursor = (7, 9)        # 置いた瞬間に手でマウスを動かされた
            return 1

        user32.SetCursorPos = moved_by_hand

        ok, _u, slept, _waits = self._dip(ex, user32=user32, accept_on=1)

        self.assertTrue(ok)
        self.assertIn(config.BEGIN_CURSOR_GAP_SEC, slept)

    # ── 5. やり直さないもの ───────────────────────
    def test_a_minimized_window_does_not_retry(self):
        ex, _st = self._executor()

        ok, user32, slept, waits = self._dip(ex, iconic=True)

        self.assertFalse(ok)
        self.assertEqual(user32.moves, [], "カーソルに触らない")
        self.assertNotIn(config.BEGIN_CURSOR_GAP_SEC, slept, "やり直さない")
        self.assertEqual(waits, 0)

    def test_a_window_off_every_screen_does_not_retry(self):
        ex, _st = self._executor()
        user32 = FakeUser32(screen=(0, 0, 100, 100))      # 点 (550,500) は画面外

        ok, _u, slept, waits = self._dip(ex, user32=user32)

        self.assertFalse(ok)
        self.assertEqual(user32.moves, [])
        self.assertNotIn(config.BEGIN_CURSOR_GAP_SEC, slept)
        self.assertEqual(waits, 0)

    # ── 6. 2回目の前に VRChat が前面になっていたら ───────────
    def test_vrchat_in_front_before_the_retry_leaves_the_cursor_alone(self):
        ex, _st = self._executor()
        SharedState.register_window_hwnd(999)             # 管理下の VRChat
        # _dip_cursor_for_begin の中で前面を見るのは2回目の前だけ（1回目の前は
        # _press_begin が見る）。そこで VRChat が前面になっている
        ok, user32, _slept, _waits = self._dip(ex, front=999)

        self.assertFalse(ok)
        self.assertEqual(user32.moves, [self.CENTRE, self.HOME],
                         "2回目は SetCursorPos を呼ばない")

    # ── 7. 間に受理されていたら ───────────────────────
    def test_an_acceptance_between_the_tries_skips_the_second(self):
        ex, st = self._executor()

        # 1回目の受理待ちでは来ず、その直後（2回目の前）に届く
        real = ex._wait_begin_accepted

        def wait_then_accept(round_seq):
            result = real(round_seq)
            st.begin_done = True
            return result

        with patch.object(ex, "_wait_begin_accepted", side_effect=wait_then_accept):
            user32 = FakeUser32()
            with contextlib.ExitStack() as stack:
                enter = stack.enter_context
                enter(patch.object(WindowOperator, "user32", user32))
                enter(patch.object(WindowOperator.win32gui, "IsIconic",
                                   return_value=False))
                enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                                   return_value=(0, 0, 900, 600)))
                enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                                   return_value=(100, 200)))
                enter(patch.object(WindowOperator, "foreground_hwnd", return_value=0))
                enter(patch.object(OSCClient.OSCClient, "press", return_value=True))
                enter(patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0.0))
                enter(patch.object(ActionExecutor.time, "sleep"))
                ok = ex._dip_cursor_for_begin("")

        self.assertTrue(ok)
        self.assertEqual(user32.moves, [self.CENTRE, self.HOME], "2回目は試さない")

    def test_a_started_round_between_the_tries_stops(self):
        ex, st = self._executor()

        def start_round(n):
            if n == 1:
                st.in_round = True

        ok, user32, _slept, _waits = self._dip(ex, each_wait=start_round)

        self.assertFalse(ok)
        self.assertEqual(user32.moves.count(self.CENTRE), 1)

    def test_a_new_round_seq_between_the_tries_stops(self):
        ex, st = self._executor()

        def bump(n):
            if n == 1:
                st.round_seq += 1

        ok, user32, _slept, _waits = self._dip(ex, each_wait=bump)

        self.assertFalse(ok)
        self.assertEqual(user32.moves.count(self.CENTRE), 1)

    # ── 8. 受理待ちは BEGIN_RETRY_WAIT_SEC ─────────────────
    def test_the_wait_follows_the_shared_setting(self):
        ex, st = self._executor()
        started = time.time()

        with patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0.3):
            self.assertFalse(ex._wait_begin_accepted(st.round_seq))

        elapsed = time.time() - started
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 1.5)

    def test_there_is_no_separate_press_wait(self):
        """受理待ちは押し直し前の待ちと共用する（依頼者の判断）"""
        here = Path(ActionExecutor.__file__).parent
        for name in ("config.py", "ActionExecutor.py"):
            self.assertNotIn("BEGIN_PRESS_WAIT_SEC",
                             (here / name).read_text(encoding="utf-8"), name)

    # ── 9. 2回目が上限で切り捨てられない ───────────────────
    def test_a_long_first_wait_does_not_cut_off_the_second_try(self):
        """以前は BEGIN_CURSOR_LIMIT_SEC（4.0）がループ全体の締め切りで、
        1回目の受理待ちが5秒あると2回目を切り捨てていた"""
        ex, _st = self._executor()
        clock = {"t": 1000.0}

        def slow_wait(round_seq):
            clock["t"] += 60.0                # 1回目の受理待ちがとても長かった
            return False

        with patch.object(ex, "_wait_begin_accepted", side_effect=slow_wait):
            user32 = FakeUser32()
            with contextlib.ExitStack() as stack:
                enter = stack.enter_context
                enter(patch.object(WindowOperator, "user32", user32))
                enter(patch.object(WindowOperator.win32gui, "IsIconic",
                                   return_value=False))
                enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                                   return_value=(0, 0, 900, 600)))
                enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                                   return_value=(100, 200)))
                enter(patch.object(WindowOperator, "foreground_hwnd", return_value=0))
                enter(patch.object(OSCClient.OSCClient, "press", return_value=True))
                enter(patch.object(ActionExecutor.time, "sleep"))
                enter(patch.object(ActionExecutor.time, "time",
                                   side_effect=lambda: clock["t"]))
                ex._dip_cursor_for_begin("")

        self.assertEqual(user32.moves.count(self.CENTRE), 2, "2回目も試す")

    def test_the_try_count_is_two(self):
        self.assertEqual(config.BEGIN_CURSOR_DIPS, 2)

    def test_the_old_overall_limit_is_gone(self):
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _dip_cursor_for_begin("):]
        body = body[:body.index("\n    def ", 10)]

        self.assertNotIn("BEGIN_CURSOR_LIMIT_SEC", body)

    def test_the_return_move_is_still_there(self):
        """試すたびに元の位置へ戻す作りは消さない（依頼者の指示）"""
        src = Path(WindowOperator.__file__).read_text(encoding="utf-8")
        body = src[src.index("def cursor_over_window"):]
        body = body[:body.index("\ndef ", 10)]

        self.assertIn("user32.SetCursorPos(*before)", body)
        self.assertIn("finally:", body)




class TestVRChatInFrontFallback(unittest.TestCase):
    """VRChat の窓が前面なら、カーソルを触らずフォールバックする。

    前面の VRChat はマウスを掴んでいるので、こちらの SetCursorPos がその窓から
    見て「マウスを動かされた」＝カメラが回ることがある。操作中の窓の照準が
    Begin から外れかねないので、触る前に判定して避ける。
    """

    RECT = (100, 200, 1000, 800)
    HWND = 123
    OTHER = 456

    def setUp(self):
        _no_accept_wait(self)
        SharedState.clear_window_hwnds()
        self.addCleanup(SharedState.clear_window_hwnds)

    def _executor(self, hwnd=None):
        cfg = WindowConfig(hwnd=hwnd or self.HWND, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=0)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True,
                                             lambda _m: None), st

    def _press(self, executor, front=0, user32=None, logs=None):
        user32 = user32 or FakeUser32()
        if logs is not None:
            executor._log = logs.append
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=(0, 0, self.RECT[2] - self.RECT[0],
                                             self.RECT[3] - self.RECT[1])))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=(self.RECT[0], self.RECT[1])))
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=False))
            enter(patch.object(WindowOperator, "foreground_hwnd",
                               return_value=front))
            focus = enter(patch.object(WindowOperator, "focus_window",
                                       return_value=True))
            click = enter(patch.object(WindowOperator, "click"))
            enter(patch.object(OSCClient.OSCClient, "press", return_value=True))
            enter(patch.object(ActionExecutor.time, "sleep"))
            ok = executor._press_begin()
        return ok, user32, focus, click

    # ── 1〜3. 前面が管理下の窓 ─────────────────────
    def test_a_managed_window_in_front_never_touches_the_cursor(self):
        SharedState.register_window_hwnd(self.OTHER)
        executor, _st = self._executor()

        _ok, user32, _focus, _click = self._press(executor, front=self.OTHER)

        self.assertEqual(user32.moves, [], "カーソルを1度も動かさない")

    def test_a_managed_window_in_front_falls_back_to_the_click(self):
        SharedState.register_window_hwnd(self.OTHER)
        executor, _st = self._executor()
        logs = []

        ok, _u, focus, click = self._press(executor, front=self.OTHER, logs=logs)

        self.assertTrue(ok)
        focus.assert_called_once()
        click.assert_called_once()
        self.assertTrue(any("VRChatが前面" in m for m in logs), logs)

    def test_this_window_in_front_is_treated_the_same(self):
        """「前面なら連打だけで押せるはず」は確かめられていないので前提にしない"""
        SharedState.register_window_hwnd(self.HWND)
        executor, _st = self._executor()

        ok, user32, focus, click = self._press(executor, front=self.HWND)

        self.assertTrue(ok)
        self.assertEqual(user32.moves, [])
        focus.assert_called_once()
        click.assert_called_once()

    # ── 4. 前面が管理外 ───────────────────────
    def test_an_unmanaged_window_in_front_still_dips(self):
        SharedState.register_window_hwnd(self.HWND)
        executor, _st = self._executor()

        _ok, user32, _focus, _click = self._press(executor, front=9999)

        self.assertTrue(user32.moves, "メモ帳などが前面なら、これまでどおり差す")

    def test_no_foreground_still_dips(self):
        SharedState.register_window_hwnd(self.HWND)
        executor, _st = self._executor()

        _ok, user32, _focus, _click = self._press(executor, front=0)

        self.assertTrue(user32.moves)

    def test_nothing_registered_still_dips(self):
        """マクロを止めたあと（記録が空）は判定が効かない"""
        executor, _st = self._executor()

        _ok, user32, _focus, _click = self._press(executor, front=self.HWND)

        self.assertTrue(user32.moves)

    # ── 5〜6. 置けたかの読み返し ──────────────────
    def test_a_cursor_that_did_not_move_falls_back(self):
        """SetCursorPos が成功を返しても実際には動かないことがある"""
        executor, _st = self._executor()
        user32 = FakeUser32()
        real_set = user32.SetCursorPos

        def stuck(x, y):
            real_set(x, y)
            user32.cursor = (500, 500)      # 実際には動いていない
            return 1

        user32.SetCursorPos = stuck

        ok, _u, focus, click = self._press(executor, front=9999, user32=user32)

        self.assertTrue(ok)
        focus.assert_called_once()
        click.assert_called_once()

    def test_a_cursor_that_landed_keeps_dipping(self):
        executor, _st = self._executor()

        _ok, user32, focus, click = self._press(executor, front=9999)

        self.assertEqual(user32.moves.count((550, 500)),
                         config.BEGIN_CURSOR_DIPS, "差し続ける")
        focus.assert_not_called()    # 差し込めた回はクリックへ落ちない
        click.assert_not_called()

    def test_a_small_rounding_error_is_allowed(self):
        """DPI スケーリングなどの端数は許す"""
        executor, _st = self._executor()
        user32 = FakeUser32()
        real_set = user32.SetCursorPos

        def off_by_one(x, y):
            real_set(x, y)
            user32.cursor = (x + 1, y - 1)
            return 1

        user32.SetCursorPos = off_by_one

        with patch.object(WindowOperator, "user32", user32), \
             patch.object(WindowOperator.win32gui, "GetClientRect",
                          return_value=(0, 0, 900, 600)), \
             patch.object(WindowOperator.win32gui, "ClientToScreen",
                          return_value=(100, 200)), \
             patch.object(WindowOperator.win32gui, "IsIconic", return_value=False), \
             patch.object(WindowOperator.time, "sleep"):
            with WindowOperator.cursor_over_window(self.HWND) as over:
                self.assertTrue(over, "±2px までは置けたものとして扱う")

    def test_a_big_miss_is_not_allowed(self):
        user32 = FakeUser32()
        real_set = user32.SetCursorPos

        def way_off(x, y):
            real_set(x, y)
            user32.cursor = (x + config.BEGIN_CURSOR_DIPS + 20, y)
            return 1

        user32.SetCursorPos = way_off

        with patch.object(WindowOperator, "user32", user32), \
             patch.object(WindowOperator.win32gui, "GetClientRect",
                          return_value=(0, 0, 900, 600)), \
             patch.object(WindowOperator.win32gui, "ClientToScreen",
                          return_value=(100, 200)), \
             patch.object(WindowOperator.win32gui, "IsIconic", return_value=False), \
             patch.object(WindowOperator.time, "sleep"):
            with WindowOperator.cursor_over_window(self.HWND) as over:
                self.assertFalse(over)

    def test_the_cursor_still_comes_back_after_a_miss(self):
        user32 = FakeUser32(cursor=(42, 43))

        def stuck(x, y):
            user32.moves.append((x, y))
            return 1                        # cursor は動かないまま

        user32.SetCursorPos = stuck

        with patch.object(WindowOperator, "user32", user32), \
             patch.object(WindowOperator.win32gui, "GetClientRect",
                          return_value=(0, 0, 900, 600)), \
             patch.object(WindowOperator.win32gui, "ClientToScreen",
                          return_value=(100, 200)), \
             patch.object(WindowOperator.win32gui, "IsIconic", return_value=False), \
             patch.object(WindowOperator.time, "sleep"):
            with WindowOperator.cursor_over_window(self.HWND) as over:
                self.assertFalse(over)

        self.assertEqual(user32.moves[-1], (42, 43), "元の位置へ戻す")

    def test_an_unreadable_cursor_is_not_a_success(self):
        """GetCursorPos が失敗したら、置けたか分からない。置けた扱いにしない"""
        class Blind(FakeUser32):
            def GetCursorPos(self, ref):
                return 0

        user32 = Blind()
        with patch.object(WindowOperator, "user32", user32),              patch.object(WindowOperator.win32gui, "GetClientRect",
                          return_value=(0, 0, 900, 600)),              patch.object(WindowOperator.win32gui, "ClientToScreen",
                          return_value=(100, 200)),              patch.object(WindowOperator.win32gui, "IsIconic", return_value=False),              patch.object(WindowOperator.time, "sleep"):
            with WindowOperator.cursor_over_window(self.HWND) as over:
                self.assertFalse(over)

    # ── 7. 記録の出入り ───────────────────────
    def test_registering_and_clearing(self):
        self.assertEqual(SharedState.managed_hwnds(), frozenset())

        SharedState.register_window_hwnd(11)
        SharedState.register_window_hwnd(22)
        SharedState.register_window_hwnd(11)        # 重複は増えない

        self.assertEqual(SharedState.managed_hwnds(), frozenset({11, 22}))

        SharedState.clear_window_hwnds()
        self.assertEqual(SharedState.managed_hwnds(), frozenset())

    def test_a_zero_hwnd_is_not_registered(self):
        SharedState.register_window_hwnd(0)

        self.assertEqual(SharedState.managed_hwnds(), frozenset())

    def test_the_result_is_a_snapshot(self):
        SharedState.register_window_hwnd(11)
        snapshot = SharedState.managed_hwnds()

        SharedState.register_window_hwnd(22)

        self.assertEqual(snapshot, frozenset({11}), "後から増えない")

    def test_start_registers_and_stop_clears(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        start = src[src.index("    def _start(self)"):]
        start = start[:start.index("\n    def ", 10)]
        stop = src[src.index("    def _stop(self)"):]
        stop = stop[:stop.index("\n    def ", 10)]

        self.assertIn("SharedState.register_window_hwnd(cfg.hwnd)", start)
        self.assertIn("SharedState.clear_window_hwnds()", stop)

    # ── 触る前に見ていること ─────────────────────
    def test_the_front_is_checked_before_the_cursor_moves(self):
        """判定が後だと、見る前にカメラを回してしまう"""
        SharedState.register_window_hwnd(self.OTHER)
        executor, _st = self._executor()
        order = []
        user32 = FakeUser32()
        real_set = user32.SetCursorPos
        user32.SetCursorPos = lambda x, y: (order.append("cursor"),
                                            real_set(x, y))[1]

        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=(0, 0, 900, 600)))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=(100, 200)))
            enter(patch.object(WindowOperator.win32gui, "IsIconic",
                               return_value=False))
            enter(patch.object(WindowOperator, "foreground_hwnd",
                               side_effect=lambda: (order.append("front"),
                                                    self.OTHER)[1]))
            enter(patch.object(WindowOperator, "focus_window", return_value=True))
            enter(patch.object(WindowOperator, "click"))
            enter(patch.object(OSCClient.OSCClient, "press", return_value=True))
            enter(patch.object(ActionExecutor.time, "sleep"))
            executor._press_begin()

        # 2回目の "front" は前面化の直前に元の窓を控える分（WindowOperator.borrow_front）
        self.assertEqual(order[0], "front", "カーソルより先に前面を見る")
        self.assertNotIn("cursor", order, "見るだけで、カーソルは触らない")




class TestBeginByCursor(unittest.TestCase):
    """OSCが使える窓の Begin は、カーソルを置いて UseRight を送る（前面化しない）"""

    def setUp(self):
        _no_accept_wait(self)

    RECT = (100, 200, 1000, 800)        # クライアント領域（スクリーン座標）

    @staticmethod
    def center(rect):
        return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)

    class CountingStop:
        """N 周で必ず止まるストップ。止まらない実装でもテストが終わるように"""

        def __init__(self, limit=3, waits=None):
            self.limit = limit
            self.calls = 0
            self.waits = waits if waits is not None else []

        def is_set(self):
            self.calls += 1
            return self.calls > self.limit

        def wait(self, sec):
            self.waits.append(sec)

    def _executor(self, osc_port=9000, hwnd=123, item_id=0):
        cfg = WindowConfig(hwnd=hwnd, osc_port=osc_port)
        # item_id=0 が手ぶら。持っていると UseRight でその持ち物を使ってしまう
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, item_id=item_id)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None), st

    def _press(self, executor, user32=None, iconic=False, rect=None, again=False,
               sleep=True):
        user32 = user32 or FakeUser32()
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            rect = rect or self.RECT
            enter(patch.object(WindowOperator, "user32", user32))
            enter(patch.object(WindowOperator.win32gui, "GetClientRect",
                               return_value=(0, 0, rect[2] - rect[0], rect[3] - rect[1])))
            enter(patch.object(WindowOperator.win32gui, "ClientToScreen",
                               return_value=(rect[0], rect[1])))
            enter(patch.object(WindowOperator.win32gui, "IsIconic", return_value=iconic))
            focus = enter(patch.object(WindowOperator, "focus_window", return_value=True))
            click = enter(patch.object(WindowOperator, "click"))
            press = enter(patch.object(OSCClient.OSCClient, "press", return_value=True))
            if sleep:
                enter(patch.object(ActionExecutor.time, "sleep"))
            ok = executor._press_begin(again=again)
        return ok, user32, focus, click, press

    def test_it_dips_the_cursor_into_the_window(self):
        """押すのは連打側。ここはカーソルを一瞬だけ置く"""
        executor, st = self._executor()

        def accept(_sec):
            st.begin_done = True        # ひと差しで押せた

        with patch.object(ActionExecutor.time, "sleep", side_effect=accept):
            ok, user32, focus, click, press = self._press(executor, sleep=False)

        self.assertTrue(ok)
        focus.assert_not_called()
        click.assert_not_called()
        self.assertEqual(user32.moves, [self.center(self.RECT), (500, 500)],
                         "Begin の上へ入って、すぐ戻る")

    def test_it_dips_again_until_it_is_accepted(self):
        executor, _st = self._executor()
        slept = []

        with patch.object(ActionExecutor.time, "sleep", side_effect=slept.append):
            ok, user32, focus, click, _press = self._press(executor, sleep=False)

        self.assertTrue(ok)
        # 差し込めた回は、受理が来なくてもその回のうちにクリックへ落ちない（# 1ラウンドで押すのは3回。次の回は位置合わせの後）
        focus.assert_not_called()
        click.assert_not_called()
        self.assertEqual(user32.moves.count((500, 500)), config.BEGIN_CURSOR_DIPS,
                         "試すたびにカーソルを戻す")
        # DWELL は WindowOperator 側の待ちと同じ秒数なので、回数ではなく有無で見る
        self.assertIn(config.BEGIN_CURSOR_DWELL_SEC, slept)
        self.assertNotIn(config.BEGIN_CURSOR_GAP_SEC, slept,
                         "置けたときは間を空けない（受理待ちが間になる）")

    def test_it_gives_up_after_the_tries(self):
        """ループ全体の時間の上限は無くした。試す回数で終わる"""
        executor, _st = self._executor()

        ok, user32, focus, click, _press = self._press(executor)

        self.assertTrue(ok)
        self.assertEqual(user32.moves.count((500, 500)), config.BEGIN_CURSOR_DIPS)
        focus.assert_not_called()       # 試し終えたらこの回はここまで
        click.assert_not_called()
        self.assertEqual(executor._last_press, "dip")

    def test_a_shop_item_still_uses_the_cursor(self):
        """st.item_id はショップの装備で、手に持っているかではない。

        手に持つ／離すは [Behaviour] Pickup object: / Drop object: で出るが、
        こちらは読んでいない。ここで分岐していたため、アイテムを買っている
        窓では UseRight が1発も送られていなかった
        """
        executor, st = self._executor(item_id=5)

        def accept(_sec):
            st.begin_done = True        # ひと差しで押せた

        with patch.object(ActionExecutor.time, "sleep", side_effect=accept):
            ok, user32, focus, click, press = self._press(executor, sleep=False)

        self.assertTrue(ok)
        focus.assert_not_called()
        click.assert_not_called()
        self.assertTrue(user32.moves, "カーソルを差す")
        self.assertEqual([c.args[0] for c in press.call_args_list],
                         [], "差し込みの側からは送らない（連打が送る）")

    def test_an_empty_hand_uses_the_cursor(self):
        executor, st = self._executor()

        def accept(_sec):
            st.begin_done = True        # ひと差しで押せた

        with patch.object(ActionExecutor.time, "sleep", side_effect=accept):
            ok, user32, focus, click, _press = self._press(executor, sleep=False)

        self.assertTrue(ok)
        focus.assert_not_called()
        click.assert_not_called()
        self.assertTrue(user32.moves, "カーソルを差す")

    def test_the_begin_path_never_drops(self):
        """手ぶらでも持っていても、Begin の経路から DropRight は出さない"""
        for item_id in (0, 5):
            executor, _st = self._executor(item_id=item_id)

            _ok, _u, _focus, _click, press = self._press(executor)

            self.assertFalse(any(c.args[0] == "/input/DropRight"
                                 for c in press.call_args_list),
                             (item_id, press.call_args_list))

    # ── 連打（カーソルは動かさない） ─────────────────
    def test_the_spam_waits_for_the_start_and_then_pulses(self):
        executor, st = self._executor()
        st.round_over_time = 1000.0
        user32 = FakeUser32()
        waits = []
        clock = itertools.chain([1000.0], itertools.repeat(1000.0 + config.BEGIN_USE_SPAM_START_SEC))

        with patch.object(WindowOperator, "user32", user32), \
             patch.object(ActionExecutor.time, "time", side_effect=lambda: next(clock)), \
             patch.object(executor, "_use_right_reaches_begin", return_value=False), \
             patch.object(OSCClient.OSCClient, "press", return_value=True) as press:
            executor._spam_use_right(self.CountingStop(limit=2, waits=waits), st.round_seq)

        self.assertEqual(waits[0], 0.1, "始まる時刻まで待つ")
        press.assert_called_once_with("/input/UseRight", config.BEGIN_USE_PULSE_SEC)
        self.assertEqual(user32.moves, [], "連打の間はカーソルを動かさない")

    def test_starting_the_spam_never_drops(self):
        """連打を立てるときに何も落とさない"""
        executor, st = self._executor()

        with patch.object(ActionExecutor.threading, "Thread") as thread, \
             patch.object(OSCClient.OSCClient, "press", return_value=True) as press:
            self.assertIsNotNone(executor._start_use_spam(st.round_seq))

        press.assert_not_called()
        thread.assert_called_once()

    def test_the_spam_pauses_while_another_window_is_frozen(self):
        """押す見込みが無い間は送らない。飛んでいる UseRight は、利用者の
        カーソルがその窓へ来た瞬間に Begin を押してしまう"""
        executor, st = self._executor()
        st.round_over_time = time.time() - 60      # 送り始める時刻は過ぎている
        other = WindowState(instance_type=config.INSTANCE_PRIVATE)
        SharedState.continue_round_start(other)
        self.addCleanup(SharedState.continue_round_reset)
        waits = []

        with patch.object(OSCClient.OSCClient, "press") as press:
            executor._spam_use_right(self.CountingStop(limit=3, waits=waits),
                                     st.round_seq)

        press.assert_not_called()
        self.assertTrue(waits, "休みながら待つ（スレッドは生きている）")

    def test_the_spam_resumes_once_the_freeze_lifts(self):
        executor, st = self._executor()
        st.round_over_time = time.time() - 60
        other = WindowState(instance_type=config.INSTANCE_PRIVATE)
        SharedState.continue_round_start(other)
        self.addCleanup(SharedState.continue_round_reset)
        stop = self.CountingStop(limit=4)
        lifted = []

        def wait(_sec):
            if not lifted:
                lifted.append(True)
                SharedState.continue_round_end(other)   # 解ける

        stop.wait = wait

        with patch.object(OSCClient.OSCClient, "press", return_value=True) as press:
            executor._spam_use_right(stop, st.round_seq)

        self.assertTrue(press.called, "解けたら送り始める")

    def test_the_spam_does_not_pause_for_its_own_equip_wait(self):
        """自分のフリーズで休むと、アイテムロスト窓が押せなくなる"""
        executor, st = self._executor()
        st.round_over_time = time.time() - 60
        st.waiting_for_equip = True
        SharedState.equip_freeze_start(st)
        self.addCleanup(SharedState.equip_freeze_reset)

        with patch.object(OSCClient.OSCClient, "press", return_value=True) as press:
            executor._spam_use_right(self.CountingStop(limit=1), st.round_seq)

        press.assert_called_once_with("/input/UseRight", config.BEGIN_USE_PULSE_SEC)

    def test_the_pause_is_logged_once_a_round(self):
        executor, st = self._executor()
        logs = []
        executor._log = logs.append
        st.round_over_time = time.time() - 60
        other = WindowState(instance_type=config.INSTANCE_PRIVATE)
        SharedState.continue_round_start(other)
        self.addCleanup(SharedState.continue_round_reset)

        with patch.object(OSCClient.OSCClient, "press"):
            executor._spam_use_right(self.CountingStop(limit=5), st.round_seq)

        self.assertEqual(len([m for m in logs if "連打を止めています" in m]), 1, logs)

    def test_the_next_round_says_it_again(self):
        executor, st = self._executor()
        logs = []
        executor._log = logs.append
        other = WindowState(instance_type=config.INSTANCE_PRIVATE)
        SharedState.continue_round_start(other)
        self.addCleanup(SharedState.continue_round_reset)

        executor._log_spam_paused()
        st.round_seq += 1
        executor._log_spam_paused()

        self.assertEqual(len(logs), 2, logs)

    def test_the_spam_stops_when_begin_is_accepted(self):
        executor, st = self._executor()
        st.round_over_time = time.time() - 60      # 連打を始める時刻は過ぎている
        st.begin_done = True

        with patch.object(OSCClient.OSCClient, "press") as press:
            executor._spam_use_right(self.CountingStop(), st.round_seq)

        press.assert_not_called()

    def test_the_spam_keeps_going_when_the_item_changes(self):
        """途中で装備が変わっても連打は止めない（ショップの装備は無関係）"""
        executor, st = self._executor()
        st.round_over_time = time.time() - 60      # 連打を始める時刻は過ぎている

        def equip(_address, _sec):
            st.item_id = 4              # 1回送ったところで装備が変わった
            return True

        with patch.object(OSCClient.OSCClient, "press", side_effect=equip) as press:
            executor._spam_use_right(self.CountingStop(limit=3), st.round_seq)

        self.assertEqual([c.args[0] for c in press.call_args_list],
                         ["/input/UseRight"] * 3, "止まらずに送り続ける")

    def test_one_spam_cycle_is_twice_the_pulse(self):
        """押している時間と離している時間が同じ＝1周は押下時間の2倍。

        押し下がる瞬間がこの周期で来る。ひと差しの滞在
        （BEGIN_CURSOR_DWELL_SEC）より短くないと、差し込みが空振りする
        """
        executor, st = self._executor()
        st.round_over_time = time.time() - 60      # 連打を始める時刻は過ぎている
        waits = []

        with patch.object(OSCClient.OSCClient, "press", return_value=True) as press:
            executor._spam_use_right(self.CountingStop(limit=3, waits=waits),
                                     st.round_seq)

        holds = [c.args[1] for c in press.call_args_list]
        self.assertEqual(holds, [config.BEGIN_USE_PULSE_SEC] * 3, "押している時間")
        self.assertEqual(waits, [config.BEGIN_USE_PULSE_SEC] * 3, "離している時間")
        self.assertLessEqual(holds[0] + waits[0], config.BEGIN_CURSOR_DWELL_SEC,
                             "1周がひと差しの滞在に収まること")

    def test_the_spam_sends_with_a_shop_item_equipped(self):
        executor, st = self._executor(item_id=3)
        st.round_over_time = time.time() - 60

        with patch.object(OSCClient.OSCClient, "press", return_value=True) as press:
            executor._spam_use_right(self.CountingStop(limit=1), st.round_seq)

        press.assert_called_once_with("/input/UseRight", config.BEGIN_USE_PULSE_SEC)

    def test_the_spam_is_not_started_without_osc_or_with_the_switch_off(self):
        executor, st = self._executor(osc_port=0)
        self.assertIsNone(executor._start_use_spam(st.round_seq))

        with patch.object(config, "BEGIN_BY_CURSOR", False):
            executor, st = self._executor()
            self.assertIsNone(executor._start_use_spam(st.round_seq))

        executor, st = self._executor(item_id=5)
        # 本物の連打のスレッドを残さない（後のテストの time.sleep の差し替えに混ざる）
        with patch.object(ActionExecutor.threading, "Thread"):
            self.assertIsNotNone(executor._start_use_spam(st.round_seq),
                                 "ショップの装備は関係ない")

    def test_the_round_flow_starts_the_spam_before_waiting(self):
        """Verified Round End を待つ前から連打を回しておく"""
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        flow = src[src.index("    def do_after_round"):]
        self.assertLess(flow.index("self._start_use_spam(round_seq)"),
                        flow.index("self._wait_round_end()"))
        self.assertIn("spam.set()", flow)

    def test_a_minimized_window_falls_back_to_the_click(self):
        executor, _st = self._executor()

        ok, user32, focus, click, press = self._press(executor, iconic=True)

        self.assertTrue(ok)
        focus.assert_called_once()
        click.assert_called_once()
        press.assert_not_called()
        self.assertEqual(user32.moves, [])

    def test_a_window_off_screen_falls_back_to_the_click(self):
        executor, _st = self._executor()

        ok, _u, focus, click, press = self._press(executor, rect=(9000, 9000, 9900, 9600))

        self.assertTrue(ok)
        focus.assert_called_once()
        click.assert_called_once()
        press.assert_not_called()

    def test_a_window_without_osc_uses_the_click(self):
        executor, _st = self._executor(osc_port=0)

        ok, user32, focus, click, _press = self._press(executor)

        self.assertTrue(ok)
        focus.assert_called_once()
        click.assert_called_once()
        self.assertEqual(user32.moves, [], "カーソルは動かさない")

    def test_the_switch_goes_back_to_the_old_way(self):
        executor, _st = self._executor()

        with patch.object(config, "BEGIN_BY_CURSOR", False):
            ok, user32, focus, click, press = self._press(executor)

        self.assertTrue(ok)
        focus.assert_called_once()
        click.assert_called_once()
        press.assert_not_called()
        self.assertEqual(user32.moves, [])

    def test_a_failed_focus_does_not_press(self):
        executor, _st = self._executor(osc_port=0)
        with patch.object(WindowOperator, "focus_window", return_value=False), \
             patch.object(WindowOperator, "click") as click:
            self.assertFalse(executor._press_begin())
        click.assert_not_called()

    def test_the_retry_starts_its_own_spam(self):
        """押し直しのときは連打が止まっているので、その場で立て直す"""
        executor, st = self._executor()

        with patch.object(executor, "_start_use_spam") as spam, \
             patch.object(executor, "_dip_cursor_for_begin", return_value=True):
            self.assertTrue(executor._press_begin(again=True))

        spam.assert_called_once_with(st.round_seq)
        spam.return_value.set.assert_called_once()

    def test_the_retry_presses_the_same_way(self):
        executor, st = self._executor()
        st.round_seq = 4
        logs = []
        executor._log = logs.append

        with patch.object(executor, "_wait_other_windows", return_value=True), \
             patch.object(executor, "_begin_precheck", return_value=True), \
             patch.object(executor, "_should_retry_begin", return_value=True), \
             patch.object(executor, "_press_begin", return_value=True) as press:
            self.assertTrue(executor._click_begin_again(4))

        press.assert_called_once_with(again=True, click_only=False)

    def test_the_retry_gives_up_after_the_limit(self):
        executor, st = self._executor()
        presses = []

        with patch.object(config, "BEGIN_RETRY_MAX", 3), \
             patch.object(config, "BEGIN_RETRY_WAIT_SEC", 0), \
             patch.object(executor, "_should_retry_begin", return_value=True), \
             patch.object(executor, "_wait_other_windows", return_value=True), \
             patch.object(executor, "_begin_precheck", return_value=True), \
             patch.object(executor, "_adjust_to_begin", return_value="skip"), \
             patch.object(executor, "_press_begin",
                          side_effect=lambda again=False, click_only=False:
                          presses.append((again, click_only)) or True), \
             patch.object(ActionExecutor.time, "sleep"):
            executor._confirm_begin(st.round_seq)

        self.assertEqual(presses, [(True, False), (True, True)],
                         "2回目と3回目（前面化＋クリック）を押して諦める")




class TestBeginNeverDropsTheItem(unittest.TestCase):
    """Begin のために持ち物を落とす処理は外した（依頼者の判断）。

    落とす必要のある局面が無かったため。持っている窓は前面化＋クリックで
    押す。ロストの扱い（音声・他窓フリーズ・装備待ち）は周回の要なので、
    Begin の前後で失っても見逃さないことを確かめる。
    """

    def setUp(self):
        # RoundOver でも装備待ちフリーズを張るので、前のテストの分を残さない
        SharedState.equip_freeze_reset()
        self.addCleanup(SharedState.equip_freeze_reset)

    def _monitor(self, item_id=5):
        cfg = WindowConfig(hwnd=1, voice_item_lost="lost.mp3", auto_begin=False)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.item_id = item_id
        monitor.st.in_round = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def test_a_loss_around_begin_is_a_real_loss(self):
        """intermission（ラウンド外）で失っても、ロストとして扱う。

        以前は Begin のために自分で落とした分を見逃す抜け道があり、
        そこへ本当のロストが紛れ込むと音声も装備待ちも走らなかった
        """
        monitor = self._monitor()
        monitor.st.in_round = False

        monitor._mark_item_lost("リスポーン: アイテムロスト")

        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)
        self.assertTrue(monitor._round_lost_item())
        self.assertTrue(monitor._round_item_warning())
        self.assertTrue(any("アイテムロスト" in m for m in monitor.logs), monitor.logs)

    def test_a_real_loss_is_still_a_loss(self):
        monitor = self._monitor()

        monitor._mark_item_lost("リスポーン: アイテムロスト")

        self.assertEqual(monitor.st.item_id, 0)
        self.assertTrue(monitor.st.item_lost_this_round)
        self.assertTrue(monitor._round_lost_item())
        self.assertTrue(monitor._round_item_warning())
        self.assertTrue(any("アイテムロスト" in m for m in monitor.logs), monitor.logs)

    def test_a_real_loss_still_announces_and_freezes(self):
        """本当のロストは、今までどおり RoundOver で通知して装備待ちに入る"""
        monitor = self._monitor()
        monitor._mark_item_lost("Run死亡: アイテムロスト")

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(monitor._action, "announce_item_lost_once") as announce:
            monitor._process("2026.09.25 00:00:00 Debug      -  RoundOver")

        announce.assert_called_once()
        self.assertTrue(monitor.st.waiting_for_equip)

    def test_a_loss_just_before_begin_still_freezes(self):
        """Begin の直前に失った場合も、装備待ちに入る（見逃さない）"""
        monitor = self._monitor()
        monitor.st.in_round = False
        monitor._mark_item_lost("リスポーン: アイテムロスト")

        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(monitor._action, "announce_item_lost_once"):
            monitor._process("2026.09.25 00:00:00 Debug      -  RoundOver")

        self.assertTrue(monitor.st.waiting_for_equip)

    def test_the_source_sends_no_drop_for_begin(self):
        """Begin のために落とす処理がコードから無くなっていること。DropRight を送るのは
        続行ラウンドの後（依頼者 2026-10-10・_drop_item_after_continue）の1か所だけ"""
        here = Path(ActionExecutor.__file__).parent
        for name in ("ActionExecutor.py", "LogMonitor.py", "WindowOperator.py",
                     "config.py", "State.py", "SharedState.py"):
            src = (here / name).read_text(encoding="utf-8")
            self.assertNotIn("item_dropped_for_begin", src, name)
            if name != "ActionExecutor.py":
                self.assertNotIn("DropRight", src, name)
        src = (here / "ActionExecutor.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"/input/DropRight"'), 1)
        body = src[src.index("    def _drop_item_after_continue("):]
        body = body[:body.index("\n    def ", 1)]
        self.assertIn('"/input/DropRight"', body)

    def test_the_next_round_needs_no_flag_to_reset(self):
        """ラウンド開始でロスト関連が戻ること（落とした印はもう無い）"""
        monitor = self._monitor()
        monitor.st.item_lost_this_round = True

        monitor._process("This round is taking place at Facility (12) "
                         "and the round type is Classic")

        self.assertFalse(monitor.st.item_lost_this_round)
        monitor.st.item_id = 7
        monitor._mark_item_lost("リスポーン: アイテムロスト")
        self.assertTrue(monitor.st.item_lost_this_round, "次のラウンドは普通にロスト")




class TestVerifiedStrafe(unittest.TestCase):
    """横移動は本物の Verified（自分の Begin が通った）で、private のときだけ。

    Verified はインマスでなければ来ないので、これで自動的に「インマスのときだけ」
    になる。STRING_DOWNLOAD で動くと Begin を押す前に移動してしまう。
    """

    def setUp(self):
        SharedState.set_speed_detect(True)

    def tearDown(self):
        SharedState.set_speed_detect(config.SPEED_DETECT_ENABLED)

    def _monitor(self, instance_type=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(WindowConfig(osc_port=9000), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.round_end_seen = True
        monitor._verified.on_round_end_verified(0)   # Begin を押せる（トラッカーにも）
        monitor.st.last_begin_press_at = time.time()   # ツールが押した直後（受理されるのはこのときだけ）
        return monitor

    def _started(self, monitor, line="Verified"):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process(line)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def test_a_real_verified_strafes_in_private(self):
        monitor = self._monitor()

        started = self._started(monitor)

        self.assertIn("do_speed_strafe", started)
        self.assertTrue(monitor.st.speed_strafe_done)

    def test_other_instances_do_not_strafe(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO,
                      config.INSTANCE_PUBLIC):
            monitor = self._monitor(itype)

            started = self._started(monitor)

            self.assertNotIn("do_speed_strafe", started, itype)
            self.assertTrue(monitor.st.begin_done, f"{itype}: Verified 自体は採用")

    def test_a_periodic_verified_does_not_strafe(self):
        """不具合の再現: intermission 中の定期で横移動を始めない"""
        monitor = self._monitor()
        monitor.st.last_begin_press_at = 0.0          # 押していない（定期）
        base = datetime(2026, 9, 26, 12, 0, 0).timestamp()
        monitor._verified.last_periodic = base
        stamp = datetime.fromtimestamp(base + config.VERIFIED_PERIODIC_SEC)

        started = self._started(
            monitor, stamp.strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  Verified")

        self.assertNotIn("do_speed_strafe", started)
        self.assertFalse(monitor.st.speed_strafe_done)


    def test_a_verified_before_the_round_end_does_not_strafe(self):
        monitor = self._monitor()
        monitor.st.round_end_seen = False
        monitor._verified.on_round_start(0)          # RoundOver の後・Verified Round End の前
        monitor._verified.on_round_over(0)

        started = self._started(monitor)

        self.assertNotIn("do_speed_strafe", started)

    def test_it_strafes_once_per_round(self):
        monitor = self._monitor()

        first = self._started(monitor)
        second = self._started(monitor)

        self.assertIn("do_speed_strafe", first)
        self.assertNotIn("do_speed_strafe", second)

    def test_the_next_round_may_strafe_again(self):
        monitor = self._monitor()
        self._started(monitor)

        with patch.object(ConnectDB, "register_round"):
            self._started(monitor, "This round is taking place at Facility (12) "
                                   "and the round type is Classic")

        self.assertFalse(monitor.st.speed_strafe_done)

    def test_the_toggle_off_does_not_strafe(self):
        SharedState.set_speed_detect(False)
        monitor = self._monitor()

        self.assertNotIn("do_speed_strafe", self._started(monitor))

    def test_the_strafe_comes_after_detection_has_started(self):
        """Verified は Round End の後に来るので、横移動は必ず検知の最中に入る"""
        monitor = self._monitor()
        monitor.st.round_end_seen = False
        monitor._verified.on_round_start(0)          # RoundOver の後・Verified Round End の前
        monitor._verified.on_round_over(0)

        end = self._started(monitor, "Verified Round End")
        verified = self._started(monitor)

        self.assertEqual(end, ["do_speed_detect"])
        self.assertEqual(verified, ["do_speed_strafe"])

    def test_joining_advances_the_instance_counter(self):
        monitor = self._monitor()
        before = monitor.st.instance_seq

        self._started(monitor, "[Behaviour] Joining wrld_1234:5678~private(usr_me)")

        self.assertEqual(monitor.st.instance_seq, before + 1)




class TestNoVerifiedBeforePress(unittest.TestCase):
    """ツールが Begin を押す窓では、ツールが直前（BEGIN_PRESS_RECENT_SEC 以内）に押したときの
    Verified だけを受理にする。押していないのに来たものは定期（VerifiedTracker に覚えさせる・begin_done を
    立てない・横移動しない）。ツールが押さない窓は今どおり"""

    BASE = datetime(2026, 10, 3, 5, 45, 25).timestamp()
    NOW = 1_000_000.0           # 壁時計（押した時刻と比べる）

    def _monitor(self, auto_begin=True, instance_type=config.INSTANCE_PRIVATE):
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=auto_begin, osc_port=9000), {},
                                        lambda _m: None, window_idx=4)
        monitor.st.instance_type = instance_type
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        self.started = []
        return monitor

    def _feed(self, monitor, at, body):
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(SharedState, "get_speed_detect", return_value=True), \
             patch.object(LogMonitor.time, "time", return_value=self.NOW), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            monitor._process(datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S")
                             + " Debug      -  " + body)
        for c in thread.call_args_list:
            target = c.kwargs.get("target")
            if target is not None and hasattr(target, "__func__"):
                self.started.append(target.__func__.__name__)

    def _window_4(self, monitor):
        """起動直後（定期の位相を知らない）: RoundOver → Verified Round End → 押す前の Verified"""
        self._feed(monitor, self.BASE, "RoundOver")
        self._feed(monitor, self.BASE + 12, "Verified Round End")
        self._feed(monitor, self.BASE + 12, "Verified")

    def test_window_4_is_not_accepted_but_learned_as_periodic(self):
        monitor = self._monitor()
        self._window_4(monitor)
        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(monitor._verified.last_periodic, self.BASE + 12, "定期として覚える")
        self.assertNotIn("do_speed_strafe", self.started, "横移動を始めない")
        self.assertIn("[窓4] Verified を無視（ツールがまだ押していない → 定期）", monitor.logs)

    def test_after_the_tool_presses_the_verified_is_accepted(self):
        monitor = self._monitor()
        self._window_4(monitor)
        monitor.st.last_begin_press_at = self.NOW - 0.5        # カーソルを差し込んで押した
        self._feed(monitor, self.BASE + 14, "Verified")
        self.assertTrue(monitor.st.begin_done)
        self.assertIn("do_speed_strafe", self.started)

    def test_a_press_within_3_seconds_counts(self):
        for ago, accepted in ((0.0, True), (config.BEGIN_PRESS_RECENT_SEC, True),
                              (config.BEGIN_PRESS_RECENT_SEC + 0.5, False)):
            monitor = self._monitor()
            self._feed(monitor, self.BASE, "RoundOver")
            self._feed(monitor, self.BASE + 12, "Verified Round End")
            monitor.st.last_begin_press_at = self.NOW - ago
            self._feed(monitor, self.BASE + 13, "Verified")
            self.assertEqual(monitor.st.begin_done, accepted, ago)

    def test_windows_the_tool_does_not_press_are_as_before(self):
        for auto_begin, itype in ((False, config.INSTANCE_PRIVATE), (True, config.INSTANCE_PUBLIC),
                                  (True, config.INSTANCE_YAKIIMO)):
            monitor = self._monitor(auto_begin=auto_begin, instance_type=itype)
            self._window_4(monitor)
            self.assertTrue(monitor.st.begin_done, (auto_begin, itype))
            self.assertIsNone(monitor._verified.last_periodic)

    def test_the_tracker_learns_the_phase(self):
        tracker = VerifiedTracker.VerifiedTracker()
        tracker.mark_periodic(100.0)
        self.assertEqual(tracker.last_periodic, 100.0)
        tracker.on_round_over(390.0)
        self.assertEqual(tracker.on_verified(400.5, False), VerifiedTracker.PERIODIC,
                         "覚えた位相から 300秒後の予定の1回は定期")




class TestBeginMoveMeasure(unittest.TestCase):
    """Begin 前の移動（と押し直しの移動）で、実際に動いた量を debug.log に1行残す（記録だけ）"""

    S = ActionExecutor.summarize_motion

    @staticmethod
    def _series(speeds, grounded=None, step=0.05):
        grounded = grounded or [None] * len(speeds)
        return [(round(i * step, 4), s, g) for i, (s, g) in enumerate(zip(speeds, grounded))]

    def test_distance_top_start_and_series(self):
        speeds = [0.0, 0.0, 3.3] + [6.6] * 37        # 2.0 秒
        line = ActionExecutor.summarize_motion(self._series(speeds), released_at=1.4)
        distance = float(line.split("距離 ")[1].split("・")[0])
        self.assertAlmostEqual(distance, (3.3 + 6.6 * 36) * 0.05, delta=0.006)   # 2 桁に丸めた値
        self.assertIn("最高 6.60", line)
        self.assertIn("動き出し 0.10秒", line)
        self.assertIn("止まり なし", line)
        self.assertIn("接地 なし", line)
        self.assertIn("速さ [0.0, 3.3, 6.6, 6.6,", line)
        self.assertEqual(line.count(", ") + 1, 20, "0.1 秒ごと")

    def test_a_reset_like_drop_is_a_stall(self):
        """移動が途中でリセットされたような列（速さが 0 に落ちて戻る）→ 止まりに出る"""
        speeds = [0.0, 6.6, 6.6, 6.6, 0.0, 0.0, 0.0, 6.6, 6.6, 6.6, 0.0, 0.0]
        line = ActionExecutor.summarize_motion(self._series(speeds), released_at=0.45)
        self.assertIn("止まり 0.20〜0.35秒", line)
        self.assertNotIn("0.50", line.split("止まり")[1].split("・")[0], "離した後の減速は止まりではない")

    def test_stopped_until_release(self):
        speeds = [6.6, 6.6, 0.1, 0.1, 0.1]
        line = ActionExecutor.summarize_motion(self._series(speeds), released_at=0.2)
        self.assertIn("止まり 0.10〜0.20秒", line)

    def test_never_moving(self):
        line = ActionExecutor.summarize_motion(self._series([0.0] * 10), released_at=0.3)
        self.assertIn("距離 0.00・最高 0.00・動き出し なし・止まり なし", line)

    def test_grounded_changes(self):
        line = ActionExecutor.summarize_motion(
            self._series([6.6] * 5, [True, True, False, False, True]), released_at=0.2)
        self.assertIn("接地 0.10秒→0、0.20秒→1", line)

    def test_the_series_is_capped_at_40(self):
        line = ActionExecutor.summarize_motion(self._series([6.6] * 200), released_at=9.0)
        self.assertEqual(line.split("速さ ")[1].count("6.6"), 40)

    def test_no_speed_received(self):
        self.assertEqual(ActionExecutor.summarize_motion([], 0.0), "実測なし（速さを受信していません）")
        self.assertEqual(ActionExecutor.summarize_motion(self._series([None] * 5), 0.1),
                         "実測なし（速さを受信していません）")

    # ── 読み方 ──
    def test_it_reads_every_0_05s_until_0_6s_after_release(self):
        now = [0.0]
        lines = []
        receiver = type("R", (), {"speed": 6.6, "grounded_or_none": True})()
        sampler = ActionExecutor.MotionSampler(receiver, lines.append, clock=lambda: now[0],
                                               sleep=lambda s: now.__setitem__(0, now[0] + s))
        sampler._started = 0.0                      # start は呼ばず（スレッドを使わず）、その場で回す
        sampler._released = 1.0
        sampler._run()
        times = [t for t, _s, _g in sampler.samples]
        self.assertAlmostEqual(times[1] - times[0], 0.05)
        self.assertGreaterEqual(times[-1], 1.6 - 1e-9)
        self.assertLess(times[-1], 1.6 + 0.05)
        self.assertTrue(lines[-1].startswith("実測 距離 "), lines)

    def test_no_receiver_writes_no_measure_at_release(self):
        lines = []
        sampler = ActionExecutor.MotionSampler(None, lines.append)
        sampler.start()
        self.assertIsNone(sampler.thread)
        sampler.release()
        self.assertEqual(lines, ["実測なし（速さを受信していません）"])

    def _executor(self, receiver):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=3, window_idx=1)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=123, osc_port=9000), st,
                                           lambda: True, lambda _m: None)
        ex._receiver = receiver
        return ex, st

    def _wait_for(self, write, text, sec=3.0):
        deadline = time.time() + sec
        while time.time() < deadline:
            lines = [c.args[0] for c in write.call_args_list]
            hit = [l for l in lines if text in l]
            if hit:
                return hit
            time.sleep(0.02)
        return []

    def test_the_begin_move_logs_the_measure_and_keeps_its_length(self):
        receiver = type("R", (), {"speed": 6.6, "grounded_or_none": True})()
        ex, st = self._executor(receiver)
        with patch.object(ex, "move_forward_left", return_value=True) as move, \
             patch.object(ActionExecutor, "MOTION_TAIL_SEC", 0.1), \
             patch.object(DebugLog, "write") as write:
            ex._begin_move()
            move.assert_called_once_with(config.BEGIN_FORWARD_SEC, config.BEGIN_LEFT_SEC)
            hit = self._wait_for(write, "実測")
        self.assertTrue(hit)
        self.assertTrue(hit[0].startswith("[操作] [窓1] Begin前の移動: 実測 距離 "), hit)
        self.assertIn("最高 6.60", hit[0])

    def test_without_a_receiver_it_says_so(self):
        ex, st = self._executor(None)
        with patch.object(ex, "move_forward_left", return_value=True), \
             patch.object(DebugLog, "write") as write:
            ex._begin_move()
        lines = [c.args[0] for c in write.call_args_list]
        self.assertIn("[操作] [窓1] Begin前の移動: 実測なし（速さを受信していません）", lines)

    def test_a_failing_move_still_releases(self):
        ex, st = self._executor(None)
        with patch.object(ex, "move_forward_left", side_effect=RuntimeError("x")), \
             patch.object(DebugLog, "write") as write:
            with self.assertRaises(RuntimeError):
                ex._begin_move()
        self.assertIn("[操作] [窓1] Begin前の移動: 実測なし（速さを受信していません）",
                      [c.args[0] for c in write.call_args_list])

    def test_the_move_of_the_second_press_logs_too(self):
        receiver = type("R", (), {"speed": 6.6, "grounded_or_none": None})()
        ex, st = self._executor(receiver)
        with patch.object(ex, "move_forward_left", return_value=True), \
             patch.object(ex, "_wait_other_windows", return_value=False), \
             patch.object(ex, "_begin_precheck", return_value=True), \
             patch.object(ActionExecutor, "MOTION_TAIL_SEC", 0.1), \
             patch.object(DebugLog, "write") as write:
            ex.do_begin_again(3)
            self.assertTrue(self._wait_for(write, "Begin前の移動: 実測"))




class TestVerifiedDebugLine(unittest.TestCase):
    """debug.log の `Verified` の行の記録は `[事象] verified → 受理／無視（理由）`（判定の後に1行）。
    `begin_done` という名前で出さない（読んだだけで Begin が通ったように見えた）"""

    BASE = datetime(2026, 10, 3, 5, 45, 25).timestamp()

    def _monitor(self, auto_begin=True):
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=auto_begin), {}, lambda _m: None,
                                        window_idx=3)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _debug_lines(self, monitor, lines):
        with patch.object(DebugLog, "write") as write, \
             patch.object(LogMonitor.threading, "Thread"), \
             patch.object(LogMonitor.time, "time", return_value=1_000_000.0), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            for at, body in lines:
                monitor._process(datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S")
                                 + " Debug      -  " + body)
        return [c.args[0] for c in write.call_args_list if "[事象]" in c.args[0]]

    def test_the_event_is_named_verified(self):
        self.assertEqual(LogParser.parse("2026.10.03 05:45:37 Debug      -  Verified").kind, "verified")
        self.assertEqual(LogParser.EVENT_VERIFIED, "verified")
        self.assertFalse(hasattr(LogParser, "EVENT_BEGIN_DONE"))

    def test_accepted(self):
        monitor = self._monitor()
        monitor.st.last_begin_press_at = 1_000_000.0 - 1.0
        lines = self._debug_lines(monitor, [(self.BASE, "RoundOver"),
                                            (self.BASE + 12, "Verified Round End"),
                                            (self.BASE + 13, "Verified")])
        self.assertIn("[窓3] [事象] verified → 受理", lines)
        self.assertFalse(any("begin_done" in l for l in lines), lines)
        self.assertEqual(sum(bool(re.search(r"\[事象\] verified( |$)", l)) for l in lines), 1,
                         "1行だけ（判定の後。verified_end は別の事象）")

    def test_not_pressed_yet(self):
        lines = self._debug_lines(self._monitor(), [(self.BASE, "RoundOver"),
                                                    (self.BASE + 12, "Verified Round End"),
                                                    (self.BASE + 12, "Verified")])
        self.assertIn("[窓3] [事象] verified → 無視（ツールがまだ押していない）", lines)

    def test_periodic(self):
        monitor = self._monitor(auto_begin=False)
        monitor._verified.last_periodic = self.BASE
        lines = self._debug_lines(monitor, [(self.BASE + 290, "RoundOver"),
                                            (self.BASE + 300.5, "Verified Round End"),
                                            (self.BASE + 300, "Verified")])
        self.assertIn("[窓3] [事象] verified → 無視（定期）", lines)

    def test_before_the_round_end(self):
        monitor = self._monitor(auto_begin=False)
        monitor._verified.last_periodic = self.BASE - 1000
        lines = self._debug_lines(monitor, [(self.BASE, "This round is taking place at Sewers (12) "
                                                         "and the round type is Classic"),
                                            (self.BASE + 60, "RoundOver"),
                                            (self.BASE + 64, "Verified")])
        self.assertIn("[窓3] [事象] verified → 無視（Verified Round End より前）", lines)

    def test_the_screen_log_is_as_before(self):
        monitor = self._monitor()
        monitor.st.last_begin_press_at = 1_000_000.0
        self._debug_lines(monitor, [(self.BASE + 12, "Verified Round End"), (self.BASE + 13, "Verified")])
        self.assertIn("[窓3] ✅ Connecting", monitor.logs)




class TestBeginMiss(unittest.TestCase):
    """位置合わせで BEGIN が見つからなかった撮影を、窓ごとに最新2枚 begin_miss\窓N_1.png・窓N_2.png に
    残し（古い方から上書き）、不具合報告の zip の begin_miss/ に入れる（選んだ窓の分・窓なしなら全部）"""

    NOW = datetime(2026, 10, 3, 7, 0, 1)

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        p = patch.object(BeginMiss, "folder", return_value=self.dir / "begin_miss")
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def _shot(value, w=4, h=3):
        return bytes([value, value, value, 255]) * (w * h), w, h

    def _save(self, window, value, stage="探す"):
        bits, w, h = self._shot(value)
        path = BeginMiss.save(window, bits, w, h, stage)
        time.sleep(0.02)                    # 古い・新しいは更新時刻で見る
        return path

    @staticmethod
    def _value(path):
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        return int(img[0, 0, 0])

    def test_two_per_window_and_the_third_overwrites_the_oldest(self):
        first = self._save(3, 10)
        second = self._save(3, 20)
        self.assertEqual((first.name, second.name), ("窓3_1.png", "窓3_2.png"))
        third = self._save(3, 30)
        self.assertEqual(third.name, "窓3_1.png", "古い方から上書き")
        fourth = self._save(3, 40)
        self.assertEqual(fourth.name, "窓3_2.png")
        files = sorted(p.name for p in (self.dir / "begin_miss").iterdir())
        self.assertEqual(files, ["窓3_1.png", "窓3_2.png"])
        self.assertEqual(self._value(self.dir / "begin_miss" / "窓3_1.png"), 30)
        self.assertEqual(self._value(self.dir / "begin_miss" / "窓3_2.png"), 40)

    def test_other_windows_are_not_touched(self):
        self._save(1, 11)
        for v in (20, 21, 22):
            self._save(2, v)
        self.assertEqual(self._value(self.dir / "begin_miss" / "窓1_1.png"), 11)
        self.assertFalse((self.dir / "begin_miss" / "窓1_2.png").exists())

    def test_the_debug_line(self):
        with patch.object(DebugLog, "write") as write:
            self._save(4, 50, stage="動いた後")
        write.assert_called_once_with("[操作] [窓4] Begin: 見つからなかった撮影を保存 窓4_1.png（動いた後・4x3）")

    def test_a_failing_save_does_not_raise(self):
        (self.dir / "begin_miss").write_text("ファイルがあってフォルダを作れない")
        with patch.object(DebugLog, "exception") as exc:
            self.assertIsNone(BeginMiss.save(1, *self._shot(1), "探す"))
        exc.assert_called_once_with("BeginMiss.save")
        (self.dir / "begin_miss").unlink()
        with patch.object(DebugLog, "exception") as exc:
            self.assertIsNone(BeginMiss.save(1, b"\0" * 8, 4, 3, "探す"), "大きさが合わない撮影は残さない")
        exc.assert_not_called()                     # 例外にせず、黙って残さない

    # ── 位置合わせから ──
    def _executor(self, window_idx=2):
        cfg = WindowConfig(hwnd=0x100, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, window_idx=window_idx)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None)

    def _look(self, ex, hit, stage="探す"):
        bits, w, h = self._shot(60)
        with patch.object(WindowOperator, "aim_in_window_image", return_value=(2.0, 1.5)), \
             patch.object(ScreenCapture, "capture_window", return_value=(bits, w, h)), \
             patch.object(BeginDetect, "find", return_value=hit), \
             patch.object(BeginMiss, "save", wraps=BeginMiss.save) as save:
            seen = ex._look_for_begin(stage)
        return seen, save

    def test_a_miss_is_saved_with_its_stage(self):
        seen, save = self._look(self._executor(), None, "撮り直し")
        self.assertEqual(seen, (None, 2.0))
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0], 2)
        self.assertEqual(save.call_args.args[4], "撮り直し")
        self.assertTrue((self.dir / "begin_miss" / "窓2_1.png").is_file())

    def test_a_hit_is_not_saved(self):
        seen, save = self._look(self._executor(), {"score": 0.9, "cx": 1, "w": 1})
        save.assert_not_called()
        self.assertIsNotNone(seen[0])

    def test_a_failing_save_does_not_stop_the_look(self):
        (self.dir / "begin_miss").write_text("x")
        with patch.object(DebugLog, "exception"):
            seen, _save = self._look(self._executor(), None)
        self.assertEqual(seen, (None, 2.0))

    def test_every_look_names_its_stage(self):
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        for stage in ("最初", "動いた後", "撮り直し", "探す"):
            self.assertIn(f'self._look_for_begin("{stage}")', src)
        self.assertNotIn("self._look_for_begin()", src)

    # ── 不具合報告 ──
    def _report(self, window, include=None, limit=None):
        include = include if include is not None else {k: True for k, _l in BugReport.ATTACHMENTS}
        debug = self.dir / "debug.log"
        debug.write_text("d\n", encoding="utf-8")
        with patch.object(config, "REPORT_MAX_BYTES", limit or config.REPORT_MAX_BYTES):
            _n, data = BugReport.build_report("止まった", window, include, "", "", {}, self.NOW,
                                              debug_log_path=debug, version="v9.9.9")
        z = zipfile.ZipFile(io.BytesIO(data))
        return {n: z.read(n) for n in z.namelist()}

    def _three_windows(self):
        for window, values in ((1, (1, 2)), (2, (3, 4)), (3, (5,))):
            for v in values:
                self._save(window, v)

    def test_the_selected_window_only(self):
        self._three_windows()
        files = self._report(2)
        self.assertEqual(sorted(n for n in files if n.startswith("begin_miss/")),
                         ["begin_miss/窓2_1.png", "begin_miss/窓2_2.png"])
        self.assertEqual(files["begin_miss/窓2_1.png"],
                         (self.dir / "begin_miss" / "窓2_1.png").read_bytes(), "そのまま入れる")
        report = files["report.txt"].decode("utf-8")
        self.assertIn("- Begin の撮影（見つからなかったとき）: begin_miss/窓2_1.png, begin_miss/窓2_2.png", report)

    def test_no_window_means_every_window(self):
        self._three_windows()
        files = self._report(0)
        self.assertEqual(len([n for n in files if n.startswith("begin_miss/")]), 5)

    def test_unchecked_is_not_included(self):
        self._three_windows()
        include = {k: True for k, _l in BugReport.ATTACHMENTS}
        include[BugReport.BEGIN_MISS] = False
        files = self._report(2, include)
        self.assertFalse([n for n in files if n.startswith("begin_miss/")])
        self.assertIn("- Begin の撮影（見つからなかったとき）: 入れていません（未選択）",
                      files["report.txt"].decode("utf-8"))

    def test_none_says_none(self):
        self._save(1, 1)
        files = self._report(4)
        self.assertIn("- Begin の撮影（見つからなかったとき）: 無し", files["report.txt"].decode("utf-8"))

    def test_too_big_drops_pictures_with_a_note(self):
        import numpy as np
        rng = np.random.default_rng(0)
        for window in (1, 2):
            noise = rng.integers(0, 255, 200 * 200 * 4, dtype=np.uint8).tobytes()
            BeginMiss.save(window, noise, 200, 200, "探す")
            time.sleep(0.02)
        one = len((self.dir / "begin_miss" / "窓1_1.png").read_bytes())
        files = self._report(0, limit=one + 3000)
        pictures = [n for n in files if n.startswith("begin_miss/")]
        self.assertEqual(pictures, ["begin_miss/窓1_1.png"], "新しい方から外す")
        self.assertIn("- Begin の撮影 begin_miss/窓2_1.png: 入れていません（大きすぎて収まらない）",
                      files["report.txt"].decode("utf-8"))

    def test_the_checkbox_and_the_note(self):
        self.assertIn((BugReport.BEGIN_MISS, "Begin の撮影（見つからなかったとき）"), BugReport.ATTACHMENTS)
        self.assertIn("画面の撮影には一緒にいた人の名前が写ることがあります", mainGUI.ReportDialog.REPORT_NOTE)




class TestVerifiedTracker(unittest.TestCase):
    """定期の Verified: 前回から300秒後。ラウンド中なら RoundOver の0〜1秒後へ遅れ、
    次はそこから300秒後"""

    P = 300.0

    def setUp(self):
        self.tr = VerifiedTracker.VerifiedTracker()

    def _round(self, start, over, end=None):
        self.tr.on_round_start(start)
        self.tr.on_round_over(over)
        if end is not None:
            self.tr.on_round_end_verified(end)

    def test_exactly_300_seconds_is_periodic(self):
        self.tr.last_periodic = 1000.0
        self.tr.on_round_end_verified(1100.0)
        self.assertEqual(self.tr.on_verified(1300.0, False), VerifiedTracker.PERIODIC)
        self.assertEqual(self.tr.last_periodic, 1300.0)

    def test_a_due_during_a_round_comes_just_after_round_over(self):
        self.tr.last_periodic = 1000.0
        self._round(1200.0, 1400.0)                          # 予定 1300 はラウンド中
        self.assertEqual(self.tr.on_verified(1401.0, False), VerifiedTracker.PERIODIC)
        self.assertEqual(self.tr.last_periodic, 1401.0)
        self.tr.on_round_end_verified(1413.0)
        self.assertEqual(self.tr.on_verified(1701.0, False), VerifiedTracker.PERIODIC,
                         "次はそこから300秒")

    def test_without_a_phase_the_one_after_round_over_is_periodic(self):
        self._round(100.0, 400.0)
        self.assertEqual(self.tr.on_verified(400.0, False), VerifiedTracker.PERIODIC)
        self.assertEqual(self.tr.last_periodic, 400.0, "位相を掴む")

    def test_after_round_over_but_not_due_is_ignored(self):
        self.tr.last_periodic = 1000.0
        self._round(1100.0, 1250.0)                          # 予定 1300 はまだ
        self.assertEqual(self.tr.on_verified(1251.0, False), VerifiedTracker.IGNORE)
        self.assertEqual(self.tr.last_periodic, 1000.0, "位相は動かさない")

    def test_after_round_over_is_only_two_seconds(self):
        self._round(100.0, 400.0)
        self.assertEqual(self.tr.on_verified(403.0, False), VerifiedTracker.IGNORE,
                         "Verified Round End より前（RoundOver 直後以外）")

    def test_an_off_schedule_verified_while_waiting_for_begin_is_begin(self):
        self.tr.last_periodic = 1000.0
        self._round(1050.0, 1150.0, 1163.0)
        self.assertEqual(self.tr.on_verified(1190.0, False), VerifiedTracker.BEGIN)
        self.assertEqual(self.tr.last_periodic, 1000.0)

    def test_one_on_schedule_is_begin_only_if_just_pressed(self):
        for pressed, expected in ((True, VerifiedTracker.BEGIN),
                                  (False, VerifiedTracker.PERIODIC)):
            tr = VerifiedTracker.VerifiedTracker()
            tr.last_periodic = 1000.0
            tr.on_round_end_verified(1200.0)
            self.assertEqual(tr.on_verified(1300.0, pressed), expected, pressed)
            self.assertEqual(tr.last_periodic, 1300.0, "どちらでも位相は更新")

    def test_two_in_a_row_are_periodic_and_begin(self):
        self.tr.last_periodic = 1000.0
        self.tr.on_round_end_verified(1250.0)
        self.assertEqual(self.tr.on_verified(1300.0, False), VerifiedTracker.PERIODIC)
        self.assertEqual(self.tr.on_verified(1301.5, False), VerifiedTracker.BEGIN)

    def test_the_tolerance_is_one_second(self):
        for offset, expected in ((-1.5, VerifiedTracker.BEGIN), (-1.0, VerifiedTracker.PERIODIC),
                                 (1.0, VerifiedTracker.PERIODIC), (1.5, VerifiedTracker.BEGIN)):
            tr = VerifiedTracker.VerifiedTracker()
            tr.last_periodic = 1000.0
            tr.on_round_end_verified(1250.0)
            self.assertEqual(tr.on_verified(1300.0 + offset, False), expected, offset)

    def test_during_a_round_is_ignored(self):
        self.tr.last_periodic = 1000.0
        self.tr.on_round_start(1250.0)
        self.assertEqual(self.tr.on_verified(1300.0, True), VerifiedTracker.IGNORE)
        self.assertEqual(self.tr.last_periodic, 1000.0, "位相も動かさない")

    def test_before_the_round_end_verified_is_ignored(self):
        self._round(100.0, 200.0)
        self.assertEqual(self.tr.on_verified(250.0, True), VerifiedTracker.IGNORE)

    def test_a_begin_not_followed_becomes_the_phase(self):
        self.tr.on_begin_not_followed(777.0)
        self.assertEqual(self.tr.last_periodic, 777.0)




class TestVerifiedInTheMonitor(unittest.TestCase):
    """LogMonitor: 本物の行の時刻でトラッカーを進め、begin のときだけ受理（横移動）する"""

    def _stamp(self, at):
        return datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S") + " Debug      -  "

    def _monitor(self, path=None):
        # 規則そのものを見るので、ツールが押さない窓
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=path, auto_begin=False), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _feed(self, monitor, at, body, clock=None):
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(SharedState, "get_speed_detect", return_value=True), \
             patch.object(LogMonitor.time, "time", return_value=clock or at + 5000), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(Recorder, "on_round_over"):
            monitor._process(self._stamp(at) + body)
        return [c.kwargs["target"].__func__.__name__ for c in thread.call_args_list
                if "target" in c.kwargs and hasattr(c.kwargs["target"], "__func__")]

    def test_only_begin_strafes(self):
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        monitor = self._monitor()
        self._feed(monitor, base, "This round is taking place at Facility (12) and the round type is Classic")
        self._feed(monitor, base + 200, "RoundOver")
        started = self._feed(monitor, base + 201, "Verified")      # 位相なし → RoundOver 直後の定期
        self.assertNotIn("do_speed_strafe", started)
        self.assertFalse(monitor.st.begin_done)
        self.assertTrue(any("定期シグナル" in m for m in monitor.logs), monitor.logs)

        self._feed(monitor, base + 213, "Verified Round End")
        started = self._feed(monitor, base + 230, "Verified")      # 予定と合わない → 受理
        self.assertIn("do_speed_strafe", started)
        self.assertTrue(monitor.st.begin_done)

    def test_the_next_periodic_after_a_continue_round_is_not_taken_for_begin(self):
        """不具合の再現: 長い続行ラウンドの間に予定を迎え、RoundOver 直後に遅れて出た定期で
        位相を直さないと、その300秒後の定期を Begin 待ちの受理と取り違えた"""
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        monitor = self._monitor()
        monitor._verified.last_periodic = base
        self._feed(monitor, base + 100, "This round is taking place at Facility (12) and the round type is Classic")
        self._feed(monitor, base + 420, "RoundOver")                # 予定 base+300 はラウンド中
        self._feed(monitor, base + 421, "Verified")                 # 遅れて出た定期
        self._feed(monitor, base + 433, "Verified Round End")
        self._feed(monitor, base + 500, "This round is taking place at Facility (12) and the round type is Classic")
        self._feed(monitor, base + 690, "RoundOver")
        self._feed(monitor, base + 703, "Verified Round End")

        started = self._feed(monitor, base + 721, "Verified")       # base+421+300

        self.assertNotIn("do_speed_strafe", started)
        self.assertFalse(monitor.st.begin_done)

    def test_a_press_just_before_makes_the_scheduled_one_a_begin(self):
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        monitor = self._monitor()
        monitor._verified.last_periodic = base
        self._feed(monitor, base + 213, "Verified Round End")
        monitor.st.last_begin_press_at = 50_000.0
        started = self._feed(monitor, base + 300, "Verified", clock=50_000.0 + config.BEGIN_PRESS_RECENT_SEC)
        self.assertIn("do_speed_strafe", started)

    def test_a_press_too_long_ago_does_not_count(self):
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        monitor = self._monitor()
        monitor._verified.last_periodic = base
        self._feed(monitor, base + 213, "Verified Round End")
        monitor.st.last_begin_press_at = 50_000.0
        started = self._feed(monitor, base + 300, "Verified",
                             clock=50_000.0 + config.BEGIN_PRESS_RECENT_SEC + 0.5)
        self.assertNotIn("do_speed_strafe", started)

    def test_the_fallback_still_learns_the_phase(self):
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        monitor = self._monitor()
        self._feed(monitor, base + 13, "Verified Round End")
        self._feed(monitor, base + 50, "Verified")
        monitor.st.log_now = base + 50 + config.VERIFIED_ROUND_START_WAIT_SEC + 1
        monitor._check_pending_verified()
        self.assertEqual(monitor._verified.last_periodic, base + 50)

    # ── 起動時に位相を取り戻す ───────────────────────
    def _write_log(self, entries):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("\n".join(self._stamp(at) + body for at, body in entries) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def test_the_phase_is_learned_from_the_last_30_minutes(self):
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        start = "This round is taking place at Facility (12) and the round type is Classic"
        entries = [(base, "User Authenticated: a (usr_0e01408a)"),
                   (base + 1, "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"),
                   (base + 100, start), (base + 400, "RoundOver"),
                   (base + 401, "Verified"),                            # 遅れて出た定期
                   (base + 413, "Verified Round End"),
                   (base + 430, "Verified"), (base + 443, start),        # 本物の受理
                   (base + 600, "RoundOver"), (base + 613, "Verified Round End")]
        written = []
        monitor = self._monitor(self._write_log(entries))
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None), \
             patch.object(DebugLog, "write", side_effect=written.append):
            monitor._detect_instance_from_log()

        self.assertEqual(monitor._verified.last_periodic, base + 401)
        self.assertTrue(any("定期 Verified の位相を復元" in m for m in written), written)
        self.assertFalse(any("位相" in m for m in monitor.logs), "公開ログには出さない")
        started = self._feed(monitor, base + 701, "Verified")      # その300秒後
        self.assertNotIn("do_speed_strafe", started)
        self.assertFalse(monitor.st.begin_done)

    def test_a_begin_not_followed_in_the_past_is_the_phase(self):
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        entries = [(base, "User Authenticated: a (usr_0e01408a)"),
                   (base + 1, "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"),
                   (base + 10, "This round is taking place at Facility (12) and the round type is Classic"),
                   (base + 100, "RoundOver"), (base + 113, "Verified Round End"),
                   (base + 150, "Verified"), (base + 400, "Verified Round End")]
        monitor = self._monitor(self._write_log(entries))
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None), \
             patch.object(DebugLog, "write"):
            monitor._detect_instance_from_log()
        self.assertEqual(monitor._verified.last_periodic, base + 150)

    def test_older_than_30_minutes_is_not_used(self):
        base = datetime(2026, 9, 30, 21, 0, 0).timestamp()
        entries = [(base, "User Authenticated: a (usr_0e01408a)"),
                   (base + 1, "[Behaviour] Joining wrld_now:2~private(usr_me)~region(jp)"),
                   (base + 10, "This round is taking place at Facility (12) and the round type is Classic"),
                   (base + 100, "RoundOver"), (base + 101, "Verified"),
                   (base + 101 + config.VERIFIED_LEARN_BACK_SEC + 10, "Verified Round End")]
        monitor = self._monitor(self._write_log(entries))
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: None), \
             patch.object(DebugLog, "write"):
            monitor._detect_instance_from_log()
        self.assertIsNone(monitor._verified.last_periodic)




class TestBeginPressTime(unittest.TestCase):
    """押した時刻を残すのは、カーソルを置けた差し込みと前面化＋クリック（連打そのものは入れない）"""

    def _executor(self):
        cfg = WindowConfig(hwnd=0x100, osc_port=9000)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE, round_seq=1)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None), st

    def test_a_click_records_the_time(self):
        ex, st = self._executor()
        with patch.object(ex, "_begin_by_cursor", return_value=False), \
             patch.object(WindowOperator, "borrow_front", return_value=(True, None)), \
             patch.object(WindowOperator, "return_front"), \
             patch.object(WindowOperator, "click"), \
             patch.object(ActionExecutor.time, "time", return_value=4242.0):
            ex._press_begin()
        self.assertEqual(st.last_begin_press_at, 4242.0)

    def test_a_dip_that_landed_records_the_time(self):
        ex, st = self._executor()

        @contextlib.contextmanager
        def landed(_hwnd, _reason=None):
            yield True

        with patch.object(WindowOperator, "cursor_over_window", side_effect=landed), \
             patch.object(ex, "_wait_begin_accepted", return_value=True), \
             patch.object(ActionExecutor.time, "sleep"), \
             patch.object(ActionExecutor.time, "time", return_value=5151.0):
            ex._dip_cursor_for_begin("")
        self.assertEqual(st.last_begin_press_at, 5151.0)

    def test_a_dip_that_could_not_land_records_nothing(self):
        ex, st = self._executor()

        @contextlib.contextmanager
        def missed(_hwnd, _reason=None):
            yield False

        with patch.object(WindowOperator, "cursor_over_window", side_effect=missed), \
             patch.object(WindowOperator, "cursor_target", return_value=(None, "")), \
             patch.object(ActionExecutor.time, "sleep"):
            ex._dip_cursor_for_begin("")
        self.assertEqual(st.last_begin_press_at, 0.0)




class TestLogMonitorBeginDone(unittest.TestCase):
    """`Verified` は Begin 受理以外に、300秒ちょうどの定期シグナルでも出る。

    見分けるのは位相（前に定期だと分かった時刻）から300秒の倍数かどうか。
    時刻はログの時刻で見る——壁時計だと、ログの追いつきや負荷で処理が遅れた
    ときに定期を Begin 受理と取り違える（intermission 中に横移動が走った）。
    """

    PREFIX = "2026.09.26 "

    def _monitor(self, phase=0.0):
        # 規則そのものを見るので、ツールが押さない窓（押した記録なしでも今どおり受理）
        monitor = LogMonitor.LogMonitor(WindowConfig(auto_begin=False), {}, lambda _msg: None,
                                        window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_end_seen = True
        monitor._verified.on_round_end_verified(0)   # Begin を押せる（トラッカーにも）
        monitor._verified.last_periodic = phase or None
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    @staticmethod
    def _stamp(at: float) -> str:
        return datetime.fromtimestamp(at).strftime("%Y.%m.%d %H:%M:%S")

    def _at(self, monitor, at: float, body="Verified"):
        """ログの時刻 at の行として流す（壁時計はわざとずらしておく）"""
        with patch.object(LogMonitor.time, "time", return_value=at + 12345.0):
            monitor._process(f"{self._stamp(at)} Debug      -  {body}")

    def _base(self) -> float:
        return datetime(2026, 9, 26, 12, 0, 0).timestamp()

    # ── 定期を無視する ──────────────────────────
    def test_a_periodic_verified_is_ignored(self):
        """不具合の再現: intermission 中の定期で begin_done を立てない"""
        base = self._base()
        monitor = self._monitor(phase=base)

        self._at(monitor, base + config.VERIFIED_PERIODIC_SEC)

        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(monitor._verified.last_periodic, base + config.VERIFIED_PERIODIC_SEC,
                         "位相を更新すること")
        self.assertTrue(any("定期シグナル" in m for m in monitor.logs), monitor.logs)

    def test_a_missed_periodic_is_picked_up_after_the_next_round_over(self):
        """倍数では追わない（定期はラウンド中なら RoundOver 直後へ遅れて位相がずれる）。
        取りこぼしても、次に RoundOver 直後に来た定期で位相を掴み直す"""
        base = self._base()
        monitor = self._monitor(phase=base)
        self._at(monitor, base + 2 * config.VERIFIED_PERIODIC_SEC)
        self.assertTrue(monitor.st.begin_done, "予定から外れた Verified は受理")

        over = base + 2 * config.VERIFIED_PERIODIC_SEC + 200
        self._at(monitor, over - 150, "This round is taking place at Facility (12) "
                                      "and the round type is Classic")
        with patch.object(LogMonitor.threading, "Thread"):   # 本物の Begin のスレッドを残さない
            self._at(monitor, over, "RoundOver")
        monitor.st.begin_done = False
        self._at(monitor, over + 1)

        self.assertFalse(monitor.st.begin_done)
        self.assertEqual(monitor._verified.last_periodic, over + 1)

    def test_too_far_is_not_periodic(self):
        base = self._base()
        monitor = self._monitor(phase=base)

        self._at(monitor, base + 11 * config.VERIFIED_PERIODIC_SEC)

        self.assertTrue(monitor.st.begin_done, "予定（前回＋300秒）以外は当てにしない")

    def test_the_boundary_is_one_second(self):
        base = self._base()
        for offset, periodic in ((-2, False), (-1, True), (0, True), (1, True), (2, False)):
            monitor = self._monitor(phase=base)

            self._at(monitor, base + config.VERIFIED_PERIODIC_SEC + offset)

            self.assertEqual(not monitor.st.begin_done, periodic, offset)

    def test_a_round_verified_between_periodics_is_accepted(self):
        """定期の合間に来るラウンド由来の Verified は今までどおり通す"""
        base = self._base()
        monitor = self._monitor(phase=base)

        self._at(monitor, base + 61)

        self.assertTrue(monitor.st.begin_done)
        self.assertEqual(monitor.st.pending_verified_time, base + 61)

    def test_without_a_phase_it_is_accepted(self):
        """位相を掴む前は Begin 受理として扱う（取りこぼさない側に倒す）"""
        base = self._base()
        monitor = self._monitor()

        self._at(monitor, base + config.VERIFIED_PERIODIC_SEC)

        self.assertTrue(monitor.st.begin_done)

    def test_it_judges_by_the_log_time_not_the_clock(self):
        """行の処理が遅れても結果が変わらないこと"""
        base = self._base()
        monitor = self._monitor(phase=base)

        with patch.object(LogMonitor.time, "time", return_value=base + 99999.0):
            monitor._process(f"{self._stamp(base + config.VERIFIED_PERIODIC_SEC)} "
                             "Debug      -  Verified")

        self.assertFalse(monitor.st.begin_done, "壁時計ではなくログの時刻で見る")

    # ── 位相を掴む ───────────────────────────
    def test_a_round_start_confirms_the_verified(self):
        base = self._base()
        monitor = self._monitor()
        self._at(monitor, base)
        self.assertEqual(monitor.st.pending_verified_time, base)

        self._at(monitor, base + 12, "This round is taking place at Facility (12) "
                                     "and the round type is Classic")

        self.assertEqual(monitor.st.pending_verified_time, 0.0)
        self.assertIsNone(monitor._verified.last_periodic, "位相は書き換えない")

    def test_no_round_start_learns_the_phase(self):
        base = self._base()
        monitor = self._monitor()
        self._at(monitor, base)

        # 待ち時間ちょうどではまだ待つ
        monitor.st.log_now = base + config.VERIFIED_ROUND_START_WAIT_SEC
        monitor._check_pending_verified()
        self.assertIsNone(monitor._verified.last_periodic)

        monitor.st.log_now = base + config.VERIFIED_ROUND_START_WAIT_SEC + 1
        monitor._check_pending_verified()

        self.assertEqual(monitor._verified.last_periodic, base, "Verified の時刻で位相を取る")
        self.assertEqual(monitor.st.pending_verified_time, 0.0)

    def test_the_phase_it_learned_is_used_next_time(self):
        """掴んだ位相で、次の定期を無視できること（通しの流れ）"""
        base = self._base()
        monitor = self._monitor()
        self._at(monitor, base)                         # 位相を掴む前なので採用
        monitor.st.log_now = base + 60
        monitor._check_pending_verified()               # ラウンド開始が来ない → 定期だった
        monitor.st.begin_done = False

        self._at(monitor, base + config.VERIFIED_PERIODIC_SEC)

        self.assertFalse(monitor.st.begin_done)

    # ── 既存の守り ──────────────────────────
    def test_a_verified_before_the_round_end_is_ignored(self):
        base = self._base()
        monitor = self._monitor()
        monitor.st.round_end_seen = False
        monitor._verified.on_round_start(0)          # RoundOver の後・Verified Round End の前
        monitor._verified.on_round_over(0)

        self._at(monitor, base)

        self.assertFalse(monitor.st.begin_done)
        self.assertTrue(any("Verified Round End より前" in m for m in monitor.logs),
                        monitor.logs)
