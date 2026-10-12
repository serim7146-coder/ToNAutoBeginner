"""鳴り止まない声を停止で止める（EM。依頼者 2026-10-13「Itemがないよを無限に繰り返す」）。
本物の MCI は使わない（`_mci`・`_mci_mode` を偽物に。再生のスレッドは本物）"""
from tests.support import *  # noqa: F401,F403
import inspect
import tests.test_gui as _gui_tests        # 停止の偽の画面を借りる（ここでは回さない）


class _Device:
    """偽の MCI。play の後は、stop・close が来るか ends_after 秒たつまで "playing"。送ったコマンドを持つ"""

    def __init__(self, test, ends_after=None):
        self.commands = []
        self.lock = threading.Lock()
        self.playing = {}           # エイリアス → 鳴り終わる時刻（None なら鳴り止まない）
        self.ends_after = ends_after
        self.threads = set()        # コマンドを送ったスレッド
        for name, value in (("_mci", self.mci), ("_mci_mode", self.mode)):
            p = patch.object(PlaySound, name, value)
            p.start()
            test.addCleanup(p.stop)
        p = patch.object(PlaySound, "POLL_SEC", 0.01)
        p.start()
        test.addCleanup(p.stop)
        PlaySound._active.clear()
        test.addCleanup(PlaySound._active.clear)

    def mci(self, command):
        with self.lock:
            self.commands.append(command)
            self.threads.add(threading.get_ident())
            verb, alias = command.split(" ")[:2]
            if verb == "play":
                self.playing[alias] = (time.monotonic() + self.ends_after) if self.ends_after else None
            elif verb in ("stop", "close"):
                self.playing.pop(alias, None)
        return 0

    def mode(self, alias):
        with self.lock:
            if alias not in self.playing:
                return "stopped"
            end = self.playing[alias]
            if end is not None and time.monotonic() >= end:
                del self.playing[alias]
                return "stopped"
            return "playing"

    def verbs(self, alias):
        return [c.split(" ")[0] for c in self.commands if c.split(" ")[1] == alias
                or (c.startswith("open") and c.endswith(f"alias {alias}"))]

    def aliases(self):
        return [c.split("alias ")[1] for c in self.commands if c.startswith("open")]


def _wait(cond, sec=3.0):
    deadline = time.monotonic() + sec
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class TestStopAll(unittest.TestCase):
    def setUp(self):
        p = patch("pathlib.Path.exists", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.written = []
        p = patch.object(DebugLog, "write", self.written.append)
        p.start()
        self.addCleanup(p.stop)

    def test_stop_all_stops_and_closes_every_playing_sound(self):
        dev = _Device(self)                 # 鳴り止まない（切れ端を繰り返していた）
        PlaySound.play_sound("voice/ItemLost.mp3")
        PlaySound.play_sound("voice/ItemLost.mp3")
        self.assertTrue(_wait(lambda: len(PlaySound._active) == 2), "同時に 2 つ鳴らせる")
        self.assertEqual(PlaySound.stop_all(), 2)
        self.assertTrue(_wait(lambda: not PlaySound._active), "止めたら覚えから外れる")
        _wait(lambda: all(dev.verbs(a)[-1:] == ["close"] for a in dev.aliases()))
        for alias in dev.aliases():
            self.assertEqual(dev.verbs(alias), ["open", "setaudio", "play", "stop", "close"], alias)
        self.assertIn("[音声] 鳴っている 2 個を止めました", self.written)

    def test_the_playing_thread_sends_stop_and_close_once(self):
        """MCI のエイリアスは開いたスレッドに縛られる（ほかのスレッドからは 263）。stop・close は再生のスレッドが
        1 回ずつ送る。stop_all を呼んだスレッドは MCI に何も送らない（二重に閉じない）"""
        dev = _Device(self)
        PlaySound.play_sound("voice/ItemLost.mp3")
        self.assertTrue(_wait(lambda: len(PlaySound._active) == 1))
        before = len(dev.commands)
        PlaySound.stop_all()
        self.assertEqual(len(dev.commands), before, "stop_all 自身は送らない")
        self.assertTrue(_wait(lambda: not PlaySound._active))
        _wait(lambda: dev.verbs(dev.aliases()[0])[-1:] == ["close"])
        alias = dev.aliases()[0]
        self.assertEqual(dev.verbs(alias).count("close"), 1)
        self.assertEqual(dev.verbs(alias).count("stop"), 1)
        self.assertNotIn(threading.get_ident(), dev.threads)

    def test_a_sound_that_ends_is_forgotten_and_not_stopped(self):
        dev = _Device(self, ends_after=0.05)
        PlaySound.play_sound("voice/Continue.mp3")
        self.assertTrue(_wait(lambda: len(dev.aliases()) == 1 and not PlaySound._active))
        _wait(lambda: dev.verbs(dev.aliases()[0])[-1:] == ["close"])
        self.assertEqual(dev.verbs(dev.aliases()[0]), ["open", "setaudio", "play", "close"])
        self.assertEqual(PlaySound.stop_all(), 0)
        self.assertFalse([m for m in self.written if "止めました" in m], "0 なら書かない")

    def test_play_has_no_wait(self):
        dev = _Device(self, ends_after=0.02)
        PlaySound.play_sound("voice/Continue.mp3")
        self.assertTrue(_wait(lambda: any(c.startswith("play") for c in dev.commands)))
        play = next(c for c in dev.commands if c.startswith("play"))
        self.assertNotIn("wait", play)

    def test_a_failed_play_is_closed_and_forgotten(self):
        dev = _Device(self)
        real = dev.mci
        dev_fail = lambda c: real(c) or (263 if c.startswith("play") else 0)  # noqa: E731
        with patch.object(PlaySound, "_mci", dev_fail), \
             patch.object(PlaySound, "_mci_error_text", return_value="err"), \
             patch("builtins.print"):
            PlaySound.play_sound("voice/Continue.mp3")
            self.assertTrue(_wait(lambda: dev.aliases() and dev.verbs(dev.aliases()[0])[-1:] == ["close"]))
        self.assertEqual(dev.verbs(dev.aliases()[0]), ["open", "setaudio", "play", "close"])
        self.assertTrue(_wait(lambda: not PlaySound._active))

    def test_a_stop_before_anything_plays_is_zero(self):
        PlaySound._active.clear()
        self.assertEqual(PlaySound.stop_all(), 0)


class TestTheAppStopsTheSounds(unittest.TestCase):
    def test_stop_calls_stop_all_after_the_monitors(self):
        order = []
        app = _gui_tests.TestSuicideKeysReleasedByTheApp._app(self)
        for m in app.monitors:
            m.stop.side_effect = lambda: order.append("monitor")
        app._stop_window_volume = lambda: order.append("volume")
        with patch.object(PlaySound, "stop_all", side_effect=lambda: order.append("sound") or 0), \
             patch.object(mainGUI.WindowOperator, "release_key_background", return_value=True):
            mainGUI.App._stop(app)
        self.assertEqual(order, ["monitor", "monitor", "sound", "volume"],
                         "監視を止めた後（新しい声が始まらない）に止める")

    def test_closing_goes_through_stop(self):
        src = inspect.getsource(mainGUI.App._on_close)
        self.assertIn("self._stop()", src)
        self.assertIn("PlaySound.stop_all()", inspect.getsource(mainGUI.App._stop))
