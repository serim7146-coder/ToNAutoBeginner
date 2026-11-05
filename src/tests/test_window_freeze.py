"""HJ: 「全窓フリーズ」の枠（速度検知の 8 Pages・Punished／ラウンド突入）は窓ごと。
その窓が条件に当たったときに、その窓の設定で全窓を止める（止める範囲は今までどおり全窓）。

画面は本物の App を 1 つ作って（窓は作った直後に隠れる。support）見る。Ctrl が押されているかは mainGUI.ctrl_held を
差し替えて決める。保存は一時フォルダ（config.SETTINGS_PATH の差し替え）だけ。本物の入力・OSC・窓・ネットワークは使わない"""
from tests.support import *  # noqa: F401,F403
import config
import mainGUI
from LogMonitor import WindowConfig

ROUND_LINE = "This round is taking place at Facility (12) and the round type is %s"
EVENTS = ("EQUIP_WAIT_EVENT", "CONTINUE_ROUND_EVENT", "SPEED_FREEZE_EVENT", "ROUND_FREEZE_EVENT")


def _reset_freezes():
    SharedState.round_freeze_reset()
    SharedState.speed_freeze_reset()
    SharedState.equip_freeze_reset()
    SharedState.continue_round_reset()
    SharedState.set_hands_free(False)


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _checkbutton(tab, text, nth=0):
    """タブの中の、文字が text の Checkbutton。全窓フリーズの枠のチェックは freeze_box の中だけから探す"""
    found = [w for w in _descendants(tab.freeze_box) if isinstance(w, ttk.Checkbutton) and w.cget("text") == text]
    return found[nth]


def _values(**kw):
    return {"freeze_8pages": False, "freeze_punish": False, "freeze_rounds": [], **kw}


class TestPerWindowRoundEntry(unittest.TestCase):
    """ラウンド突入: 窓1 だけ Fog を選ぶ → 窓1 の突入では全窓を止め、窓2 の突入では止めない"""

    def setUp(self):
        _reset_freezes()
        self.addCleanup(_reset_freezes)
        for p in (patch.object(ConnectDB, "register_round"),
                  patch.object(LogMonitor.threading, "Thread"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(WindowOperator, "focus_window", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def _monitor(self, idx, rounds):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=100 + idx, freeze_rounds=set(rounds)), {},
                                        lambda _m: None, window_idx=idx)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor._running = True
        return monitor

    def test_only_the_window_that_chose_fog_freezes_everyone(self):
        w1, w2 = self._monitor(1, {"Fog"}), self._monitor(2, set())

        w2._process(ROUND_LINE % "Fog")
        self.assertFalse(w2.st.round_freeze_held, "窓2 は選んでいない")
        self.assertTrue(SharedState.ROUND_FREEZE_EVENT.is_set(), "全窓は止まらない")

        w1._process(ROUND_LINE % "Fog")
        self.assertTrue(w1.st.round_freeze_held)
        self.assertFalse(SharedState.ROUND_FREEZE_EVENT.is_set(), "窓1 の突入で全窓が止まる")

    def test_each_window_reads_its_own_rounds(self):
        w1, w2 = self._monitor(1, {"Midnight"}), self._monitor(2, {"Ghost"})

        w1._process(ROUND_LINE % "Ghost")
        w2._process(ROUND_LINE % "Midnight")
        self.assertFalse(w1.st.round_freeze_held)
        self.assertFalse(w2.st.round_freeze_held)

        w1._process(ROUND_LINE % "Midnight")
        w2._process(ROUND_LINE % "Ghost")
        self.assertTrue(w1.st.round_freeze_held)
        self.assertTrue(w2.st.round_freeze_held)

    def test_the_fog_announcement_skips_only_the_window_that_froze(self):
        """Fog のアナウンス（入場時）は、その窓が突入で Fog を選んでいれば二重に鳴らさない"""
        chosen, other = self._monitor(1, {"Fog"}), self._monitor(2, set())
        for monitor in (chosen, other):
            monitor.cfg.voice_fog = "fog.mp3"
        with patch.object(config, "ANNOUNCE_FOG_ON_ENTRY", True):
            chosen._process(ROUND_LINE % "Fog")
            from_chosen = [c.kwargs.get("kind") for c in PlaySound.play_sound.call_args_list]
            PlaySound.play_sound.reset_mock()
            other._process(ROUND_LINE % "Fog")
            from_other = [c.kwargs.get("kind") for c in PlaySound.play_sound.call_args_list]
        self.assertEqual(from_chosen.count("fog"), 1, "突入フリーズの音声だけ（アナウンスと二重にしない）")
        self.assertEqual(from_other.count("fog"), 1, "選んでいない窓はアナウンスが鳴る")


class TestPerWindowSpeedDetect(unittest.TestCase):
    """速度検知: 窓1 だけ 8 Pages を選ぶ → 窓2 の検知では張らず、音声の分岐も窓2 の設定で動く"""

    def setUp(self):
        _reset_freezes()
        self.addCleanup(_reset_freezes)

    def _executor(self, **freeze):
        cfg = WindowConfig(hwnd=123, osc_port=9000, voice_8pages="8p.mp3", voice_punish="pn.mp3", **freeze)
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        return ActionExecutor.ActionExecutor(cfg, st, lambda: True, lambda _m: None,
                                             auto_begin_active=lambda: True), st

    def _detect(self, ex, kind):
        with patch.object(ActionExecutor.PlaySound, "play_sound") as play, \
             patch.object(WindowOperator, "focus_window", return_value=True):
            ex._announce_speed_kind(kind, 6.5)
        return [c.args[0] for c in play.call_args_list]

    def test_the_window_that_chose_freezes_everyone_and_speaks(self):
        ex1, st1 = self._executor(freeze_8pages=True)

        played = self._detect(ex1, "8pages")

        self.assertTrue(st1.speed_freeze_held)
        self.assertFalse(SharedState.SPEED_FREEZE_EVENT.is_set(), "全窓が止まる")
        self.assertEqual(played, ["8p.mp3"])

    def test_a_window_that_did_not_choose_neither_freezes_nor_speaks(self):
        ex2, st2 = self._executor()          # 窓2: 自動 Begin が回している窓で、フリーズしない種別は知らせない

        played = self._detect(ex2, "8pages")

        self.assertFalse(st2.speed_freeze_held)
        self.assertTrue(SharedState.SPEED_FREEZE_EVENT.is_set())
        self.assertEqual(played, [])

    def test_the_two_windows_side_by_side(self):
        ex1, st1 = self._executor(freeze_8pages=True)
        ex2, st2 = self._executor(freeze_punish=True)

        self.assertEqual(self._detect(ex2, "8pages"), [], "窓2 は 8 Pages を選んでいない")
        self.assertFalse(st2.speed_freeze_held)
        self.assertEqual(self._detect(ex1, "8pages"), ["8p.mp3"])
        self.assertTrue(st1.speed_freeze_held)
        self.assertEqual(self._detect(ex2, "punish"), ["pn.mp3"], "窓2 は Punished を選んでいる")
        self.assertTrue(st2.speed_freeze_held)
        self.assertEqual(self._detect(ex1, "punish"), [], "窓1 は Punished を選んでいない")

    def test_the_setting_is_read_when_it_is_needed(self):
        """動作中に変えると次の判定から効く（cfg を読み直す）"""
        ex, st = self._executor()
        self.assertEqual(self._detect(ex, "8pages"), [])

        ex._cfg.freeze_8pages = True

        self.assertEqual(self._detect(ex, "8pages"), ["8p.mp3"])
        self.assertTrue(st.speed_freeze_held)

    def test_the_executor_reads_the_window_config_not_a_shared_value(self):
        ex, _st = self._executor(freeze_8pages=True, freeze_punish=False)

        self.assertTrue(ex._freeze_enabled_for("8pages"))
        self.assertFalse(ex._freeze_enabled_for("punish"))
        self.assertFalse(ex._freeze_enabled_for("normal"))


class _AppCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(mainGUI.App, "_start_host_save_polling"):
            cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.logged = []
        p = patch.object(mainGUI.App, "_log", lambda _s, m: self.logged.append(m))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.app, "_schedule_settings_save")
        self.saved = p.start()
        self.addCleanup(p.stop)
        p = patch.object(mainGUI, "ctrl_held", return_value=False)
        self.ctrl = p.start()
        self.addCleanup(p.stop)
        if len(self.app.tabs) != 4:
            self.app.v_win_count.set(4)
            self.app._rebuild_tabs(4)
        self.tabs = self.app.tabs
        for tab in self.tabs:
            tab.set_freeze_values({})
        self.app._window_freezes = {}
        self.app._running = False
        self.app.monitors = []

    def _live(self):
        """動作中のふりをして、各窓の監視（の cfg）を作る"""
        cfgs = [WindowConfig() for _ in self.tabs]
        for t, cfg in zip(self.tabs, cfgs):
            m = MagicMock()
            m.window_idx = t.idx + 1
            m.cfg = cfg
            self.app.monitors.append(m)
        self.app._running = True
        self.addCleanup(setattr, self.app, "_running", False)
        self.addCleanup(self.app.monitors.clear)
        return cfgs

    def _save(self, stored_before=None):
        stored = {}
        with patch.object(mainGUI, "save_settings", stored.update), \
             patch.object(mainGUI, "load_settings", return_value=dict(stored_before or {})):
            self.app._save_launch_settings()
        return stored

    def _load(self, data):
        with patch.object(mainGUI, "load_settings", return_value=dict(data)), \
             patch.object(self.app, "_load_own_list", lambda: None), \
             patch.object(self.app, "_add_tool_row", lambda p, save=True: None):
            self.app._load_saved_settings()


class TestTabSettings(_AppCase):
    """窓のタブ: 変数・live_settings・動作中の反映・連動"""

    def test_live_settings_carries_the_three_settings(self):
        tab = self.tabs[0]
        tab.v_freeze_8pages.set(True)
        tab.v_freeze_rounds["Fog"].set(True)

        got = tab.live_settings()

        self.assertIs(got["freeze_8pages"], True)
        self.assertIs(got["freeze_punish"], False)
        self.assertEqual(got["freeze_rounds"], {"Fog"})
        self.assertIsInstance(got["freeze_rounds"], set)

    def test_get_config_and_snapshot_include_them(self):
        tab = self.tabs[0]
        tab.set_freeze_values(_values(freeze_punish=True, freeze_rounds=["Ghost"]))
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "output_log.txt"
            log.write_text("", encoding="utf-8")
            tab.v_log.set(str(log))
            cfg, err = tab.get_config()
        self.assertIsNone(err)
        self.assertTrue(cfg.freeze_punish)
        self.assertEqual(cfg.freeze_rounds, {"Ghost"})
        snap = tab.snapshot()
        self.assertEqual((snap["freeze_8pages"], snap["freeze_punish"], snap["freeze_rounds"]),
                         (False, True, {"Ghost"}))

    def test_changing_while_running_reaches_only_that_windows_monitor(self):
        """動作中に変えたら、その窓の監視に次の判定から効く"""
        cfgs = self._live()
        tab = self.tabs[1]

        tab.v_freeze_8pages.set(True)
        tab.v_freeze_punish.set(True)
        tab.v_freeze_rounds["Fog"].set(True)

        self.assertTrue(cfgs[1].freeze_8pages)
        self.assertTrue(cfgs[1].freeze_punish)
        self.assertEqual(cfgs[1].freeze_rounds, {"Fog"})
        for i in (0, 2, 3):
            self.assertFalse(cfgs[i].freeze_8pages)
            self.assertEqual(cfgs[i].freeze_rounds, set())

        tab.v_freeze_rounds["Fog"].set(False)
        self.assertEqual(cfgs[1].freeze_rounds, set(), "外しても届く")

    def test_nothing_reaches_the_monitor_while_stopped(self):
        self.app.monitors = []
        cfg = WindowConfig()
        m = MagicMock()
        m.window_idx = 1
        m.cfg = cfg
        self.app.monitors.append(m)
        self.addCleanup(self.app.monitors.clear)

        self.tabs[0].v_freeze_8pages.set(True)

        self.assertFalse(cfg.freeze_8pages, "次の開始で写る")

    def test_a_change_schedules_a_save(self):
        self.saved.reset_mock()
        self.tabs[2].v_freeze_rounds["Midnight"].set(True)
        self.assertTrue(self.saved.called)
        self.saved.reset_mock()
        self.tabs[2].v_freeze_8pages.set(True)
        self.assertTrue(self.saved.called)
        self.saved.reset_mock()
        self.tabs[2].v_auto_begin.set(not self.tabs[2].v_auto_begin.get())
        self.assertFalse(self.saved.called, "これを保存するのは全窓フリーズの 3 種だけ（ほかは今までどおり）")

    # ── 連動 ───────────────────────────────────────────
    def test_checking_a_speed_freeze_checks_only_this_windows_same_round_entry(self):
        for text, name, other in (("8 Pages", "8 Pages", "Punished"), ("Punished", "Punished", "8 Pages")):
            for t in self.tabs:
                t.set_freeze_values({})
            _checkbutton(self.tabs[1], text, 0).invoke()

            self.assertTrue(self.tabs[1].v_freeze_rounds[name].get(), name)
            self.assertFalse(self.tabs[1].v_freeze_rounds[other].get(), "もう一方は変えない")
            for i in (0, 2, 3):
                self.assertFalse(self.tabs[i].v_freeze_rounds[name].get(), "ほかの窓の突入は変わらない")

    def test_unchecking_the_speed_freeze_leaves_the_round_entry(self):
        tab = self.tabs[0]
        _checkbutton(tab, "8 Pages", 0).invoke()             # 入れる
        self.assertTrue(tab.v_freeze_rounds["8 Pages"].get())
        _checkbutton(tab, "8 Pages", 0).invoke()             # 外す
        self.assertFalse(tab.v_freeze_8pages.get())
        self.assertTrue(tab.v_freeze_rounds["8 Pages"].get(), "外しても突入側はそのまま")

    def test_the_round_entry_does_not_touch_the_speed_side(self):
        tab = self.tabs[0]
        tab.v_freeze_rounds["Punished"].set(True)
        self.assertFalse(tab.v_freeze_punish.get())

    def test_the_link_reaches_the_monitor_too(self):
        cfgs = self._live()
        _checkbutton(self.tabs[0], "Punished", 0).invoke()

        self.assertTrue(cfgs[0].freeze_punish)
        self.assertIn("Punished", cfgs[0].freeze_rounds)

    def test_loading_values_does_not_link(self):
        tab = self.tabs[0]
        tab.set_freeze_values(_values(freeze_8pages=True, freeze_punish=True))
        self.assertEqual(tab.freeze_values(), _values(freeze_8pages=True, freeze_punish=True))

    def test_restore_brings_the_settings_back(self):
        tab = self.tabs[0]
        tab.set_freeze_values(_values(freeze_8pages=True, freeze_rounds=["Fog", "Ghost"]))
        snap = tab.snapshot()
        tab.set_freeze_values({})
        tab.restore(snap)
        self.assertEqual(tab.freeze_values(), _values(freeze_8pages=True, freeze_rounds=["Fog", "Ghost"]))

    def test_the_section_is_closed_at_first_and_opens_in_every_tab(self):
        self.assertEqual(mainGUI.WindowTab.SECTIONS, ("assign", "rounds", "freeze"))
        self.assertTrue(all(t.sections["freeze"].collapsed for t in self.tabs))
        self.app._on_tab_section_toggled("freeze", False)
        self.addCleanup(self.app._on_tab_section_toggled, "freeze", True)
        self.assertTrue(all(not t.sections["freeze"].collapsed for t in self.tabs))


class TestCtrlBroadcastFreeze(_AppCase):
    """Ctrl を押しながら変えると全窓に同じ値が入る（連動も各窓で行う）"""

    def test_each_of_the_three_reaches_every_window(self):
        self.ctrl.return_value = True
        _checkbutton(self.tabs[1], "Punished", 0).invoke()                       # 速度検知
        self.assertEqual([t.v_freeze_punish.get() for t in self.tabs], [True] * 4)
        _checkbutton(self.tabs[2], "8 Pages", 0).invoke()
        self.assertEqual([t.v_freeze_8pages.get() for t in self.tabs], [True] * 4)
        _checkbutton(self.tabs[3], "Ghost", 0).invoke()                          # ラウンド突入
        self.assertEqual([t.v_freeze_rounds["Ghost"].get() for t in self.tabs], [True] * 4)
        _checkbutton(self.tabs[0], "Ghost", 0).invoke()                          # 外す
        self.assertEqual([t.v_freeze_rounds["Ghost"].get() for t in self.tabs], [False] * 4)

    def test_the_link_happens_in_every_window(self):
        self.ctrl.return_value = True
        _checkbutton(self.tabs[0], "8 Pages", 0).invoke()
        self.assertEqual([t.v_freeze_rounds["8 Pages"].get() for t in self.tabs], [True] * 4)
        self.assertEqual([t.v_freeze_rounds["Punished"].get() for t in self.tabs], [False] * 4)

    def test_turning_off_does_not_clear_the_round_entries(self):
        self.ctrl.return_value = True
        _checkbutton(self.tabs[0], "Punished", 0).invoke()                       # ON（突入も入る）
        _checkbutton(self.tabs[0], "Punished", 0).invoke()                       # OFF
        self.assertEqual([t.v_freeze_punish.get() for t in self.tabs], [False] * 4)
        self.assertEqual([t.v_freeze_rounds["Punished"].get() for t in self.tabs], [True] * 4)

    def test_other_settings_stay_put(self):
        self.ctrl.return_value = True
        self.tabs[1].v_freeze_rounds["Midnight"].set(True)
        self.tabs[1].set_freeze_values(_values(freeze_rounds=["Midnight"]))
        _checkbutton(self.tabs[0], "Ghost", 0).invoke()
        self.assertTrue(self.tabs[1].v_freeze_rounds["Midnight"].get())
        self.assertFalse(self.tabs[0].v_freeze_rounds["Midnight"].get())
        self.assertEqual([t.v_freeze_8pages.get() for t in self.tabs], [False] * 4)

    def test_without_ctrl_only_that_window_changes(self):
        _checkbutton(self.tabs[1], "Punished", 0).invoke()
        _checkbutton(self.tabs[1], "Fog", 0).invoke()
        _checkbutton(self.tabs[1], "8 Pages", 0).invoke()
        self.assertTrue(self.tabs[1].v_freeze_punish.get())
        self.assertTrue(self.tabs[1].v_freeze_rounds["Fog"].get())
        for i in (0, 2, 3):
            self.assertEqual(self.tabs[i].freeze_values(), _values())
        self.assertEqual(self.logged, [])

    def test_ctrl_is_read_at_the_moment_of_the_change(self):
        _checkbutton(self.tabs[0], "Fog", 0).invoke()                            # Ctrl なし: その窓だけ
        self.assertFalse(self.tabs[1].v_freeze_rounds["Fog"].get())
        self.ctrl.return_value = True
        _checkbutton(self.tabs[0], "Midnight", 0).invoke()                       # Ctrl あり: 全窓
        self.assertEqual([t.v_freeze_rounds["Midnight"].get() for t in self.tabs], [True] * 4)
        self.assertFalse(self.tabs[1].v_freeze_rounds["Fog"].get(), "さっきの Ctrl なしの分は写らない")

    def test_the_log_lines(self):
        self.ctrl.return_value = True
        _checkbutton(self.tabs[0], "8 Pages", 0).invoke()
        _checkbutton(self.tabs[0], "Punished", 0).invoke()
        _checkbutton(self.tabs[0], "Fog", 0).invoke()
        _checkbutton(self.tabs[0], "Fog", 0).invoke()
        self.assertEqual(self.logged, [
            "[設定] Ctrl: 「速度検知 8 Pages」を ON にして全窓へ写しました",
            "[設定] Ctrl: 「速度検知 Punished」を ON にして全窓へ写しました",
            "[設定] Ctrl: 「ラウンド突入 Fog」を ON にして全窓へ写しました",
            "[設定] Ctrl: 「ラウンド突入 Fog」を OFF にして全窓へ写しました",
        ])

    def test_while_running_every_monitor_gets_it(self):
        self.ctrl.return_value = True
        cfgs = self._live()
        _checkbutton(self.tabs[0], "Punished", 0).invoke()
        _checkbutton(self.tabs[0], "Alternate", 0).invoke()
        for cfg in cfgs:
            self.assertTrue(cfg.freeze_punish)
            self.assertEqual(cfg.freeze_rounds, {"Punished", "Alternate"})

    def test_a_save_is_scheduled(self):
        self.ctrl.return_value = True
        self.saved.reset_mock()
        _checkbutton(self.tabs[0], "Cracked", 0).invoke()
        self.assertTrue(self.saved.called)


class TestSaveAndLoad(_AppCase):
    """保存（window_freezes）と読み込み"""

    def test_round_trip(self):
        self.tabs[0].set_freeze_values(_values(freeze_8pages=True, freeze_rounds=["Fog", "8 Pages"]))
        self.tabs[2].set_freeze_values(_values(freeze_punish=True, freeze_rounds=["Ghost"]))

        stored = self._save()

        self.assertEqual(stored["window_freezes"]["1"], _values(freeze_8pages=True, freeze_rounds=["8 Pages", "Fog"]))
        self.assertEqual(stored["window_freezes"]["2"], _values())
        self.assertEqual(stored["window_freezes"]["3"], _values(freeze_punish=True, freeze_rounds=["Ghost"]))
        self.assertEqual(set(stored["window_freezes"]), {"1", "2", "3", "4"})
        for old in ("freeze_8pages", "freeze_punish", "freeze_rounds"):
            self.assertNotIn(old, stored)

        for t in self.tabs:
            t.set_freeze_values({})
        self._load(json.loads(json.dumps(stored)))               # JSON を通した形
        self.assertEqual(self.tabs[0].freeze_values(), _values(freeze_8pages=True, freeze_rounds=["8 Pages", "Fog"]))
        self.assertEqual(self.tabs[1].freeze_values(), _values())
        self.assertEqual(self.tabs[2].freeze_values(), _values(freeze_punish=True, freeze_rounds=["Ghost"]))

    def test_loading_does_not_link(self):
        self._load({"window_freezes": {"1": _values(freeze_8pages=True, freeze_punish=True)}})
        self.assertTrue(self.tabs[0].v_freeze_8pages.get())
        self.assertFalse(self.tabs[0].v_freeze_rounds["8 Pages"].get())
        self.assertFalse(self.tabs[0].v_freeze_rounds["Punished"].get())

    def test_a_missing_window_is_off(self):
        self._load({"window_freezes": {"2": _values(freeze_8pages=True)}})
        self.assertEqual(self.tabs[0].freeze_values(), _values())
        self.assertTrue(self.tabs[1].v_freeze_8pages.get())

    def test_reducing_the_window_count_and_coming_back_keeps_the_settings(self):
        self.tabs[3].set_freeze_values(_values(freeze_punish=True, freeze_rounds=["Cracked"]))
        self.tabs[1].set_freeze_values(_values(freeze_8pages=True))
        self.addCleanup(self.app._rebuild_tabs, 4)

        self.app._rebuild_tabs(2)                                # 窓 3・4 のタブは消える
        self.assertEqual(len(self.app.tabs), 2)
        stored = self._save()
        self.assertEqual(stored["window_freezes"]["4"], _values(freeze_punish=True, freeze_rounds=["Cracked"]),
                         "タブが無い窓の分も保存に残る")
        self.assertTrue(self.app.tabs[1].v_freeze_8pages.get())

        self.app._rebuild_tabs(4)
        self.assertEqual(self.app.tabs[3].freeze_values(), _values(freeze_punish=True, freeze_rounds=["Cracked"]))
        self.assertEqual(self.app.tabs[2].freeze_values(), _values())

    def test_a_change_made_just_before_reducing_is_kept(self):
        """消えるタブの最後の変更も残す（消す前に控える）"""
        self.addCleanup(self.app._rebuild_tabs, 4)
        self.tabs[3].set_freeze_values(_values(freeze_rounds=["Fog"]))
        self.app._rebuild_tabs(3)
        self.tabs = self.app.tabs
        self.app._rebuild_tabs(4)
        self.assertEqual(self.app.tabs[3].freeze_values(), _values(freeze_rounds=["Fog"]))

    def test_broken_values_are_off(self):
        data = {"window_freezes": {
            "1": "oops",                                                  # 窓の値が dict でない
            "2": {"freeze_8pages": True, "freeze_punish": [False, True], "freeze_rounds": 5},
            "3": {"freeze_rounds": ["Fog", None, 3]},
            "x": _values(freeze_8pages=True),                             # 番号でない
            "0": _values(freeze_8pages=True),                             # 範囲の外
            str(config.MAX_WINDOWS + 1): _values(freeze_8pages=True),
        }}

        got = mainGUI._as_window_freezes(data)

        self.assertEqual(sorted(got), [1, 2, 3])
        self.assertEqual(got[1], _values())
        self.assertEqual(got[2], _values(freeze_8pages=True, freeze_punish=True), "壊れた突入だけ OFF。配列は畳む")
        self.assertEqual(got[3], _values(freeze_rounds=["Fog"]))
        for bad in (None, 5, [], "x"):
            self.assertEqual(mainGUI._as_window_freezes({"window_freezes": bad}), {}, bad)
        self.assertEqual(mainGUI._as_window_freezes(None)[1], _values(), "壊れた全体でも落ちない")

    def test_the_new_key_wins_over_the_old_ones(self):
        data = {"window_freezes": {"1": _values()}, "freeze_8pages": True, "freeze_rounds": ["Fog"]}
        got = mainGUI._as_window_freezes(data)
        self.assertEqual(got, {1: _values()}, "新しいキーがあれば古いキーは見ない")


class TestLoadingDoesNotSave(_AppCase):
    """読み込み・戻す書き込みでは保存を予約しない（起動しただけで settings.json を書き換えない）。手で変えた・Ctrl で写したときは予約する"""

    def test_set_freeze_values_and_restore_do_not_call_back(self):
        tab = self.tabs[0]
        calls = []
        tab._on_freeze_changed = lambda: calls.append(1)

        tab.set_freeze_values(_values(freeze_8pages=True, freeze_rounds=["Fog"]))
        snap = tab.snapshot()
        tab.set_freeze_values({})
        tab.restore(snap)

        self.assertEqual(calls, [])
        self.assertEqual(tab.freeze_values(), _values(freeze_8pages=True, freeze_rounds=["Fog"]))
        self.assertFalse(tab._freeze_loading, "書き終えたら guard は戻る")
        tab.v_freeze_rounds["Ghost"].set(True)
        self.assertEqual(calls, [1], "あとの手の変更は頼む")

    def test_the_guard_is_cleared_even_if_a_write_fails(self):
        tab = self.tabs[0]
        with self.assertRaises(KeyError):
            tab.restore({})                               # 値が足りず途中で落ちる
        self.assertFalse(tab._freeze_loading)

    def test_the_settings_changed_trace_still_fires_when_loading(self):
        cfgs = self._live()
        self.tabs[1].set_freeze_values(_values(freeze_punish=True, freeze_rounds=["Fog"]))
        self.assertTrue(cfgs[1].freeze_punish, "動作中の監視へは今までどおり届く")
        self.assertEqual(cfgs[1].freeze_rounds, {"Fog"})

    def test_loading_old_keys_does_not_schedule_a_save_and_the_file_keeps_them_until_a_real_save(self):
        old = {"freeze_8pages": True, "freeze_punish": False, "freeze_rounds": ["Fog"]}
        mainGUI.save_settings(dict(old))
        self.addCleanup(config.SETTINGS_PATH.unlink, missing_ok=True)
        self.saved.reset_mock()

        self.app._load_saved_settings()

        self.saved.assert_not_called()
        self.assertEqual(json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8")), old, "起動では書き換えない")
        for tab in self.tabs:
            self.assertEqual(tab.freeze_values(), _values(freeze_8pages=True, freeze_rounds=["Fog"]))

        self.app._save_settings_now()                     # 本物の保存

        stored = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
        for key in old:
            self.assertNotIn(key, stored)
        self.assertEqual(stored["window_freezes"]["1"], _values(freeze_8pages=True, freeze_rounds=["Fog"]))

    def test_rebuilding_the_tabs_does_not_schedule_a_save_for_the_freeze_values(self):
        self.tabs[0].set_freeze_values(_values(freeze_rounds=["Fog"]))
        self.addCleanup(self.app._rebuild_tabs, 4)
        self.saved.reset_mock()
        self.app._rebuild_tabs(3)
        self.assertFalse(self.saved.called)

    def test_a_hand_change_and_a_ctrl_broadcast_do_schedule_a_save(self):
        self.saved.reset_mock()
        _checkbutton(self.tabs[0], "Fog", 0).invoke()
        self.assertTrue(self.saved.called, "手の変更")
        self.saved.reset_mock()
        _checkbutton(self.tabs[0], "8 Pages", 0).invoke()
        self.assertTrue(self.saved.called, "速度検知（連動つき）")
        self.saved.reset_mock()
        self.ctrl.return_value = True
        _checkbutton(self.tabs[1], "Ghost", 0).invoke()
        self.assertTrue(self.saved.called, "Ctrl で全窓へ")
        self.saved.reset_mock()
        self.tabs[2].set_check("freeze_punish", True)
        self.assertTrue(self.saved.called, "set_check")
        self.saved.reset_mock()
        self.tabs[2].set_freeze_round("Cracked", True)
        self.assertTrue(self.saved.called, "set_freeze_round")


class TestMigration(_AppCase):
    """古い共通の値（freeze_8pages・freeze_punish・freeze_rounds）を窓 1〜MAX_WINDOWS の全部へ写す"""

    OLD = {"freeze_8pages": True, "freeze_punish": False, "freeze_rounds": ["Fog", "Ghost"]}

    def test_the_old_keys_go_to_every_window(self):
        got = mainGUI._as_window_freezes(dict(self.OLD))

        self.assertEqual(sorted(got), list(range(1, config.MAX_WINDOWS + 1)))
        for number in got:
            self.assertEqual(got[number], _values(freeze_8pages=True, freeze_rounds=["Fog", "Ghost"]), number)
        got[1]["freeze_rounds"].append("x")
        self.assertEqual(got[2]["freeze_rounds"], ["Fog", "Ghost"], "窓ごとに別のリスト")

    def test_no_keys_at_all_is_everything_off(self):
        got = mainGUI._as_window_freezes({})
        self.assertEqual(sorted(got), list(range(1, config.MAX_WINDOWS + 1)))
        self.assertTrue(all(v == _values() for v in got.values()))

    def test_the_old_array_shapes_are_read(self):
        """窓ごとの配列だった頃の形: フラグはいずれかが ON なら ON、突入は全窓の和集合"""
        got = mainGUI._as_window_freezes({"freeze_8pages": [False, True], "freeze_punish": [False, False],
                                          "freeze_rounds": [["Fog"], ["Ghost", "Fog"]]})
        self.assertEqual(got[1], _values(freeze_8pages=True, freeze_rounds=["Fog", "Ghost"]))
        self.assertEqual(got[config.MAX_WINDOWS], got[1])

    def test_loading_an_old_file_reaches_every_tab_and_the_next_save_drops_the_old_keys(self):
        self._load(dict(self.OLD))
        for tab in self.tabs:
            self.assertEqual(tab.freeze_values(), _values(freeze_8pages=True, freeze_rounds=["Fog", "Ghost"]))

        stored = self._save(stored_before=self.OLD)

        for old in self.OLD:
            self.assertNotIn(old, stored, "古いキーは消える")
        self.assertEqual(sorted(stored["window_freezes"], key=int), [str(n) for n in range(1, config.MAX_WINDOWS + 1)])
        for number in range(1, config.MAX_WINDOWS + 1):
            self.assertEqual(stored["window_freezes"][str(number)],
                             _values(freeze_8pages=True, freeze_rounds=["Fog", "Ghost"]), number)

    def test_the_same_behavior_as_before_for_a_migrated_file(self):
        """移した後の窓の設定が、以前の共通の値と同じ判定になる"""
        self._load(dict(self.OLD))
        cfgs = []
        for tab in self.tabs:
            cfgs.append(WindowConfig(**{k: v for k, v in tab.live_settings().items() if k.startswith("freeze_")}))
        for cfg in cfgs:
            self.assertTrue(cfg.freeze_8pages)
            self.assertFalse(cfg.freeze_punish)
            self.assertEqual(cfg.freeze_rounds, {"Fog", "Ghost"})


class TestDebugStart(_AppCase):
    """[環境] 開始: 窓ごとの行に 3 項目、共通の行からは外れる"""

    def test_the_lines(self):
        monitors = []
        for i in (1, 2):
            m = MagicMock()
            m.window_idx = i
            m.cfg = WindowConfig(hwnd=0x10 * i, freeze_8pages=(i == 1), freeze_rounds={"Fog"} if i == 2 else set())
            monitors.append(m)
        self.app.monitors = monitors
        self.addCleanup(self.app.monitors.clear)
        lines = []

        with patch.object(DebugLog, "write", side_effect=lines.append), \
             patch.object(WindowOperator, "window_rect", return_value=None):
            self.app._debug_start()

        per_window = [l for l in lines if "開始 窓" in l]
        common = [l for l in lines if "開始 共通" in l]
        self.assertEqual(len(per_window), 2)
        self.assertIn(" 8Pagesフリーズ=True Punishedフリーズ=False 突入フリーズ=[]", per_window[0])
        self.assertIn(" 8Pagesフリーズ=False Punishedフリーズ=False 突入フリーズ=['Fog']", per_window[1])
        self.assertEqual(len(common), 1)
        for word in ("8Pagesフリーズ", "Punishedフリーズ", "突入フリーズ"):
            self.assertNotIn(word, common[0])


if __name__ == "__main__":
    unittest.main()
