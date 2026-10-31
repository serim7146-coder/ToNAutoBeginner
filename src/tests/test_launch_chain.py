"""起動の 2 行目の 4 つのチェック（自動Join → 自動突破 → 自動でBegin → Guidance Plush）は連鎖（GZ）。
左が OFF なら右も効かない。本物の入力・OSC・窓・クリップボードは使わない"""
from tests.support import *  # noqa: F401,F403
from tests.test_join_plush import _Entry, _Thread


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _chain_app(join=True, entry=True, begin=True, plush=True, running=False, n=2):
    """偽の App。連鎖を読む関数だけ本物を結ぶ"""
    app = type("FakeApp", (), {})()
    app.v_join_world = _Var(join)
    app.v_ton_entry = _Var(entry)
    app.v_ton_begin = _Var(begin)
    app.v_ton_entry_plush = _Var(plush)
    app.v_emergency_key = _Var("p")
    app._running = running
    app._start_stop_busy = False
    app.logs = []
    app._log = app.logs.append
    app.calls = []
    app.after = lambda _ms, fn, *a: None
    app._refresh_start_stop_button = lambda: app.calls.append("refresh")
    app._rearm_start_stop_button = lambda: None
    app._schedule_settings_save = lambda: app.calls.append("save")
    for name in ("_refresh_hands_free_button", "_refresh_item_fetch_button", "_refresh_speed_detect_button"):
        setattr(app, name, lambda: None)
    app._launched_tab_indices = None
    app.tabs = []
    for i in range(n):
        tab = MagicMock()
        tab.idx = i
        tab._get_selected_hwnd.return_value = 0x100 + i
        tab.v_log.get.return_value = f"C:/logs/output_log_{i}.txt"
        app.tabs.append(tab)
    app._entry_stop = threading.Event()
    app.btn_stop_entry = MagicMock()

    def plush_after(*a):
        pass
    plush_after.__name__ = "_plush_after_entry"
    app._plush_after_entry = plush_after

    def start():
        app.calls.append("start")
        app._running = True
    app._start = start
    for name in ("_launch_chain", "_all_auto_checks_on", "_apply_full_auto_settings",
                 "_auto_start_before_entry", "_run_ton_entry"):
        setattr(app, name, getattr(mainGUI.App, name).__get__(app))
    return app


class TestTheChainValues(unittest.TestCase):
    def test_each_stage_needs_everything_to_its_left(self):
        cases = (
            (dict(join=False), (False, False, False, False)),
            (dict(join=False, entry=True, begin=True, plush=True), (False, False, False, False)),
            (dict(entry=False), (True, False, False, False)),
            (dict(begin=False), (True, True, False, False)),
            (dict(plush=False), (True, True, True, False)),
            (dict(), (True, True, True, True)),
        )
        for kwargs, expected in cases:
            self.assertEqual(mainGUI.App._launch_chain(_chain_app(**kwargs)), expected, kwargs)

    def test_all_auto_checks_on_needs_all_four(self):
        for kwargs in (dict(join=False), dict(entry=False), dict(begin=False), dict(plush=False)):
            self.assertFalse(_chain_app(**kwargs)._all_auto_checks_on(), kwargs)
        self.assertTrue(_chain_app()._all_auto_checks_on())


class TestTheChainActs(unittest.TestCase):
    def setUp(self):
        for getter, setter in ((SharedState.get_hands_free, SharedState.set_hands_free),
                               (SharedState.get_item_fetch, SharedState.set_item_fetch),
                               (SharedState.get_speed_detect, SharedState.set_speed_detect)):
            self.addCleanup(setter, getter())
        SharedState.set_hands_free(False)
        SharedState.set_item_fetch(False)
        SharedState.set_speed_detect(True)
        _Entry.made, _Thread.plush = [], []
        for target, name, value in ((ToNEntry, "ToNEntry", _Entry), (mainGUI.threading, "Thread", _Thread),
                                    (SaveLoad, "log_size", lambda p: 1234),
                                    (SaveLoad, "read_clipboard", lambda: "")):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_join_off_skips_the_entry_and_does_not_start_the_macro(self):
        app = _chain_app(join=False)
        app._auto_start_before_entry()
        self.assertEqual(app.calls, [], "マクロの自動開始なし")
        self.assertEqual(app.logs, [], "ログも出さない")
        app._run_ton_entry()
        self.assertEqual(app.logs, ["[入室操作] 自動Join が OFF のためスキップします"])
        self.assertEqual(_Entry.made, [])
        self.assertFalse(app._all_auto_checks_on())
        self.assertEqual(SharedState.get_hands_free(), False, "全自動の設定にもならない")

    def test_entry_off_with_join_on_skips_as_before(self):
        app = _chain_app(entry=False)
        app._auto_start_before_entry()
        app._run_ton_entry()
        self.assertEqual(app.calls, [])
        self.assertEqual(app.logs, ["[入室操作] 設定がOFFのためスキップします"])
        self.assertEqual(_Entry.made, [])

    def test_begin_off_enters_without_begin_and_plush(self):
        app = _chain_app(begin=False, plush=True)
        app._auto_start_before_entry()
        self.assertEqual(app.calls, [], "Begin が効いていないのでマクロの自動開始なし")
        app._run_ton_entry()
        self.assertEqual(len(_Entry.made), 2)
        self.assertEqual([e.calls for e in _Entry.made], [[], []], "Begin は押さない")
        self.assertEqual(_Thread.plush, [], "Plush も取りに行かない")

    def test_plush_off_presses_begin_without_plush(self):
        app = _chain_app(plush=False)
        app._run_ton_entry()
        self.assertEqual([e.calls for e in _Entry.made], [["begin"], ["begin"]])
        self.assertEqual(_Thread.plush, [])

    def test_all_on_works_as_before(self):
        app = _chain_app()
        app._auto_start_before_entry()
        self.assertEqual(app.calls[-1], "start")
        self.assertTrue(app._running)
        self.assertTrue(SharedState.get_hands_free(), "4 つ全部 ON なら全自動の設定")
        self.assertIn("[起動] 入室操作の前にマクロを自動で開始しました", app.logs)
        app._run_ton_entry()
        self.assertEqual([e.calls for e in _Entry.made], [["begin"], ["begin"]])
        self.assertEqual(len(_Thread.plush), 2)
        self.assertFalse([m for m in app.logs if "スキップ" in m])


class TestTheChainOnScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _states(self):
        app = self.app
        return tuple(str(chk.cget("state")) for chk in
                     (app.chk_ton_entry, app.chk_ton_begin, app.chk_ton_entry_plush))

    def _set(self, join, entry, begin, plush):
        app = self.app
        app.v_join_world.set(join)
        app.v_ton_entry.set(entry)
        app.v_ton_begin.set(begin)
        app.v_ton_entry_plush.set(plush)
        app._refresh_launch_chain()

    def test_the_right_checks_turn_grey_stage_by_stage_and_keep_their_values(self):
        n, d = "normal", "disabled"
        for values, expected in (((True, True, True, True), (n, n, n)),
                                 ((False, True, True, True), (d, d, d)),
                                 ((True, False, True, True), (n, d, d)),
                                 ((True, True, False, True), (n, n, d)),
                                 ((False, False, False, False), (d, d, d))):
            self._set(*values)
            self.assertEqual(self._states(), expected, values)
            self.assertEqual((self.app.v_join_world.get(), self.app.v_ton_entry.get(),
                              self.app.v_ton_begin.get(), self.app.v_ton_entry_plush.get()),
                             values, "灰色でも値は消さない")

    def test_the_checks_call_the_refresh(self):
        for chk in (self.app.chk_ton_entry, self.app.chk_ton_begin, self.app.chk_ton_entry_plush):
            self.assertTrue(str(chk.cget("command")), chk)
        self._set(False, True, True, True)
        join_chk = self.app.chk_ton_entry.master.pack_slaves()[0]
        self.assertEqual(join_chk.cget("text"), "ToNへ自動Join")
        join_chk.invoke()                                   # 押して ON にした
        self.assertEqual(self._states(), ("normal", "normal", "normal"))
        join_chk.invoke()                                   # もう一度（ON → OFF）
        self.assertEqual(self._states(), ("disabled", "disabled", "disabled"))

    def test_the_four_values_are_saved_as_they_are_and_restored(self):
        for values in ((False, True, True, True), (True, False, True, False), (True, True, False, True),
                       (True, True, True, False), (True, True, True, True)):
            self._set(*values)
            stored = {}
            with patch.object(mainGUI, "save_settings", stored.update), \
                 patch.object(mainGUI, "load_settings", return_value={}):
                self.app._save_launch_settings()
            self.assertEqual((stored["join_world"], stored["ton_entry"], stored["ton_begin"],
                              stored["ton_entry_plush"]), values, "灰色でも 4 つそのまま保存")
            self._set(True, True, True, True)
            with patch.object(mainGUI, "load_settings", return_value=dict(stored)):
                self.app._load_saved_settings()
            self.assertEqual((self.app.v_join_world.get(), self.app.v_ton_entry.get(),
                              self.app.v_ton_begin.get(), self.app.v_ton_entry_plush.get()), values)
            join, entry, begin, _plush = values
            self.assertEqual(self._states(), (
                "normal" if join else "disabled",
                "normal" if join and entry else "disabled",
                "normal" if join and entry and begin else "disabled"), "読み込み後の灰色")
