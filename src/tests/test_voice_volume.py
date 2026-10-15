"""声ごとの音量（EO。依頼者 2026-10-13「各音声の音量を変更できるようにしてください」）。
全体の音量 × 声の音量。本物の MCI は使わない（`_mci`・`_mci_mode` を偽物に）"""
from tests.support import *  # noqa: F401,F403
import inspect
import re
import tempfile


class _SyncThread:
    def __init__(self, target=None, daemon=None, **_kw):
        self._target = target

    def start(self):
        self._target()


def _reset_volumes(test):
    before = PlaySound.get_sound_volume()
    saved = dict(PlaySound._voice_volumes)
    test.addCleanup(PlaySound.set_sound_volume, before)
    test.addCleanup(lambda: (PlaySound._voice_volumes.clear(), PlaySound._voice_volumes.update(saved)))
    PlaySound._voice_volumes.clear()


class TestTheVolume(unittest.TestCase):
    def setUp(self):
        _reset_volumes(self)

    def _setaudio(self, kind=None):
        commands = []
        with patch.object(PlaySound, "_mci", side_effect=lambda c: commands.append(c) or 0), \
             patch.object(PlaySound, "_mci_mode", return_value="stopped"), \
             patch.object(PlaySound.threading, "Thread", _SyncThread), \
             patch("pathlib.Path.exists", return_value=True):
            PlaySound.play_sound("voice/ItemLost.mp3", kind=kind)
        return next(c for c in commands if c.startswith("setaudio"))

    def test_the_whole_times_the_voice(self):
        PlaySound.set_sound_volume(0.8)
        PlaySound.set_voice_volume("item_lost", 0.5)
        self.assertTrue(self._setaudio("item_lost").endswith(" volume to 400"))

    def test_no_kind_is_the_whole_only(self):
        PlaySound.set_sound_volume(0.8)
        PlaySound.set_voice_volume("item_lost", 0.5)
        self.assertTrue(self._setaudio(None).endswith(" volume to 800"))

    def test_another_kind_is_not_affected(self):
        PlaySound.set_sound_volume(1.0)
        PlaySound.set_voice_volume("item_lost", 0.5)
        self.assertTrue(self._setaudio("continue").endswith(" volume to 1000"))

    def test_the_default_is_100_and_values_are_clamped(self):
        self.assertEqual(PlaySound.get_voice_volume("fog"), 1.0)
        PlaySound.set_voice_volume("fog", 1.7)
        self.assertEqual(PlaySound.get_voice_volume("fog"), 1.0)
        PlaySound.set_voice_volume("fog", -1)
        self.assertEqual(PlaySound.get_voice_volume("fog"), 0.0)

    def test_twelve_kinds_named_after_the_window_settings(self):
        self.assertEqual(len(PlaySound.VOICE_KINDS), 18, "EZ で 6 つ（突入フリーズの 5 つと AFK）")
        for kind in PlaySound.VOICE_KINDS:
            self.assertTrue(hasattr(WindowConfig(), f"voice_{kind}"), kind)


class TestEveryCallerPassesItsKind(unittest.TestCase):
    def test_no_call_without_a_kind(self):
        for name in ("ActionExecutor.py", "LogMonitor.py"):
            src = Path(name).read_text(encoding="utf-8")
            calls = re.findall(r"PlaySound\.play_sound\(([^\n]*)\)", src)
            self.assertTrue(calls, name)
            for call in calls:
                self.assertIn("kind=", call, f"{name}: {call}")

    def test_each_voice_goes_with_its_own_kind(self):
        """設定の voice_<種類> を鳴らす所は、その種類を渡す"""
        for name in ("ActionExecutor.py", "LogMonitor.py"):
            src = Path(name).read_text(encoding="utf-8")
            for attr, kind in re.findall(r'play_sound\(self\._?cfg\.voice_(\w+), kind="(\w+)"\)', src):
                self.assertEqual(attr, kind, name)
        lm = Path("LogMonitor.py").read_text(encoding="utf-8")
        used = set(re.findall(r'play_sound\(self\.cfg\.voice_\w+, kind="(\w+)"\)', lm))
        self.assertEqual(used, {"continue", "list_lost", "foxy", "fog", "intermission"})
        self.assertIn('play_sound(self._cfg.voice_item_lost, kind="item_lost")',
                      Path("ActionExecutor.py").read_text(encoding="utf-8"))

    def test_the_round_entry_voices(self):
        kinds = LogMonitor.LogMonitor.ROUND_ENTRY_VOICE_KINDS
        self.assertEqual(kinds, {"Fog": "fog", "Unbound": "unbound", "Midnight": "midnight",
                                 "Alternate": "alternate", "Ghost": "ghost",
                                 "Sabotage murder": "murder", "Sabotage star": "sabotage", "Cracked": "cracked",
                                 "Double Trouble": "double_trouble", "Bloodbath EX": "bloodbath_ex"})
        cfg = WindowConfig(voice_fog="f", voice_unbound="u", voice_midnight="m", voice_alternate="a",
                           voice_ghost="g", voice_murder="mu", voice_sabotage="sa", voice_cracked="c",
                           voice_double_trouble="d", voice_bloodbath_ex="e")
        monitor = type("M", (), {"cfg": cfg})()
        for round_type, kind in kinds.items():
            self.assertEqual(LogMonitor.LogMonitor._round_entry_voice(monitor, round_type),
                             getattr(cfg, f"voice_{kind}"), round_type)

    def test_the_speed_voices(self):
        """8 Pages・Punish（速度検知）は、種類の名前がそのまま渡る"""
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(WindowConfig(voice_8pages="8.mp3", voice_punish="p.mp3"), st,
                                           lambda: True, lambda _m: None)
        for kind in ("8pages", "punish"):
            self.assertIn(kind, PlaySound.VOICE_KINDS)
            self.assertEqual(ex._speed_voice(kind), getattr(ex._cfg, f"voice_{kind}"))
        self.assertIn("PlaySound.play_sound(self._speed_voice(kind), kind=kind)",
                      Path("ActionExecutor.py").read_text(encoding="utf-8"))


class TestTheScreen(unittest.TestCase):
    class Var:
        def __init__(self, value=None):
            self.v = value

        def get(self):
            return self.v

        def set(self, value):
            self.v = value

    def setUp(self):
        _reset_volumes(self)
        self.app = type("FakeApp", (), {})()
        self.app.logs = []
        self.app._log = self.app.logs.append
        self.app.saves = []
        self.app._schedule_settings_save = lambda: self.app.saves.append(1)
        self.app.v_voice_volumes = {k: self.Var(1.0) for k in PlaySound.VOICE_KINDS}

    def test_the_slider_takes_effect_and_is_saved(self):
        mainGUI.App._set_voice_volume(self.app, "item_lost", 0.35)
        self.assertEqual(PlaySound.get_voice_volume("item_lost"), 0.35)
        self.assertEqual(self.app.saves, [1])
        self.assertEqual(PlaySound.voice_volumes_setting()["item_lost"], 0.35)
        self.assertEqual(set(PlaySound.voice_volumes_setting()), set(PlaySound.VOICE_KINDS))

    def test_it_is_saved_in_the_settings(self):
        src = inspect.getsource(mainGUI.App._save_launch_settings)
        self.assertIn('"voice_volumes":           PlaySound.voice_volumes_setting(),', src)

    def test_loading_sets_the_sound_and_the_sliders(self):
        mainGUI.App._load_voice_volumes(self.app, {"voice_volumes": {"item_lost": 0.5, "fog": 0}})
        self.assertEqual(PlaySound.get_voice_volume("item_lost"), 0.5)
        self.assertEqual(PlaySound.get_voice_volume("fog"), 0.0)
        self.assertEqual(self.app.v_voice_volumes["item_lost"].get(), 0.5)
        self.assertEqual(self.app.v_voice_volumes["continue"].get(), 1.0)

    def test_broken_values_are_100(self):
        for broken in (None, [], "x", {"item_lost": "0.5"}, {"item_lost": 1.5}, {"item_lost": -0.1},
                       {"item_lost": True}, {"item_lost": None}):
            PlaySound.set_voice_volume("item_lost", 0.2)
            mainGUI.App._load_voice_volumes(self.app, {"voice_volumes": broken})
            self.assertEqual(PlaySound.get_voice_volume("item_lost"), 1.0, broken)
            self.assertEqual(self.app.v_voice_volumes["item_lost"].get(), 1.0, broken)
        mainGUI.App._load_voice_volumes(self.app, {})
        self.assertEqual(PlaySound.get_voice_volume("continue"), 1.0, "無い")

    def test_load_is_called_when_the_settings_are_read(self):
        src = inspect.getsource(mainGUI.App._load_saved_settings)
        self.assertIn("App._load_voice_volumes(self, data)", src)

    def test_the_play_button_plays_once_with_the_kind_even_when_stopped(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        f = Path(d.name) / "lost.mp3"
        f.write_bytes(b"x")
        self.app._running = False
        with patch.object(PlaySound, "play_sound") as play:
            mainGUI.App._try_voice(self.app, "item_lost", f"  {f}  ", "アイテムロスト")
        play.assert_called_once_with(str(f), kind="item_lost")
        self.assertEqual(self.app.logs, [])

    def test_the_play_button_says_when_the_file_is_missing(self):
        with patch.object(PlaySound, "play_sound") as play:
            mainGUI.App._try_voice(self.app, "fog", "C:/no/such/fog.mp3", "Fog")
            mainGUI.App._try_voice(self.app, "fog", "", "Fog")
        play.assert_not_called()
        self.assertEqual(self.app.logs, ["[音声] Fog のファイルがありません"] * 2)

    def test_every_row_has_its_kind(self):
        src = inspect.getsource(mainGUI.App)
        rows = re.findall(r'voice_row\(fva, "[^"]+", self\.v_voice_(\w+), "(\w+)"\)', src)
        self.assertEqual(len(rows), 18)
        for attr, kind in rows:
            self.assertEqual(attr, kind)
        self.assertEqual({k for _a, k in rows}, set(PlaySound.VOICE_KINDS))


class TestTheRealRows(unittest.TestCase):
    """本物の App の「アナウンス」の 18 行に、スライダー・%・▶ が付いている"""

    @classmethod
    def setUpClass(cls):
        cls.app = mainGUI.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        _reset_volumes(self)

    def _rows(self):
        rows = []
        for row in self.app._announce_frame.content.winfo_children():
            kids = row.winfo_children()
            rows.append(([k.winfo_class() for k in kids], kids))
        return rows

    def test_each_row_has_a_slider_a_percent_and_a_play_button(self):
        rows = self._rows()
        self.assertEqual(len(rows), 18)
        for classes, kids in rows:
            self.assertEqual(classes, ["TLabel", "TEntry", "TButton", "TScale", "TLabel", "TButton"])
            self.assertEqual(kids[-1].cget("text"), "▶")
            self.assertEqual(kids[4].cget("text"), "100%")
        self.assertEqual(set(self.app.v_voice_volumes), set(PlaySound.VOICE_KINDS))

    def test_the_percent_follows_the_slider(self):
        with patch.object(self.app, "_schedule_settings_save"):
            self.app.v_voice_volumes["item_lost"].set(0.42)
            self.app.update_idletasks()
        _classes, kids = self._rows()[8]
        self.assertEqual(kids[0].cget("text"), "アイテムロスト:")
        self.assertEqual(kids[4].cget("text"), "42%")
        self.app.v_voice_volumes["item_lost"].set(1.0)

    def test_the_play_button_plays_that_row(self):
        _classes, kids = self._rows()[8]
        with patch.object(self.app, "_try_voice") as try_voice:
            kids[-1].invoke()
        try_voice.assert_called_once_with("item_lost", self.app.v_voice_item_lost.get(), "アイテムロスト")
