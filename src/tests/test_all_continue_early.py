"""全続行のラウンドは、テラーが分かる前（Killers is unknown）から音量を「全続行」にする。
干し芋の Fog・8 Pages・1回目の moon、private の「ラウンドごとの全続行」。
テラーが分かったら今までどおり（続行リストにあれば普通の続行、なければ全続行のまま・行は二度出さない）。
本物の音・録画・窓・入力は使わない"""
from tests.support import *  # noqa: F401,F403
import Recorder

HOSHI, YAKI = config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO
FOG_KEY, PAGES_KEY = "Fog/霧", "8 Pages/8ページ"
LISTED, UNLISTED = 99, 12
GROUP_LINE = "[窓1] グループ判定: {} 【全続行】"
PRIVATE_LINE = "[窓1] ラウンド指定で続行: {}"


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.continue_round_reset()
        self.addCleanup(SharedState.continue_round_reset)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        SharedState.set_hands_free(False)
        self.addCleanup(SharedState.set_hands_free, False)
        p = patch.object(ConnectDB, "register_round")
        p.start()
        self.addCleanup(p.stop)

    def _monitor(self, instance_type, round_type, keep_on=None, continue_rounds=(), skip_rounds=()):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           continue_rounds=set(continue_rounds), skip_rounds=set(skip_rounds))
        logs = []
        monitor = LogMonitor.LogMonitor(cfg, keep_on if keep_on is not None else {}, logs.append, window_idx=1)
        monitor.logs = logs
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor._running = True
        monitor.st.continue_hook = monitor._on_continue_freeze
        return monitor

    def _unknown(self, monitor, round_type):
        with patch.object(LogMonitor.threading, "Thread") as thread:
            monitor._process(f"Killers is unknown - ??? // x // Round type is {round_type}")
            return [c.kwargs["target"].__name__ for c in thread.call_args_list if "target" in c.kwargs]

    def _killers(self, monitor, ids, revealed=False):
        with patch.object(LogMonitor.threading, "Thread") as thread, \
             patch.object(PlaySound, "play_sound") as play, \
             patch.object(Recorder, "on_continue_start") as record, \
             patch.object(monitor._action, "remember_continue_window"), \
             patch.object(WindowOperator, "focus_window", return_value=True):
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=revealed)
            started = [c.kwargs["target"].__name__ for c in thread.call_args_list if "target" in c.kwargs]
        return started, play, record


class TestGroupWhileUnknown(_Case):
    def _check_unknown(self, round_type, keep_key):
        monitor = self._monitor(HOSHI, round_type, keep_on={keep_key: {LISTED}})
        started = self._unknown(monitor, round_type)
        st = monitor.st
        self.assertTrue(st.all_continue_round)
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.ALL_CONTINUE)
        self.assertEqual(monitor.logs.count(GROUP_LINE.format(round_type)), 1, monitor.logs)
        self.assertNotIn("do_skip", started)
        self.assertFalse(st.is_continue_round)
        return monitor

    def test_hoshiimo_fog_and_8_pages_are_all_continue_at_once(self):
        self._check_unknown("Fog", FOG_KEY)
        self._check_unknown("8 Pages", PAGES_KEY)

    def test_listed_terror_then_becomes_a_continue_round(self):
        for round_type, key, revealed in (("Fog", FOG_KEY, True), ("8 Pages", PAGES_KEY, False)):
            SharedState.continue_round_reset()
            monitor = self._check_unknown(round_type, key)
            started, play, record = self._killers(monitor, [LISTED], revealed=revealed)
            st = monitor.st
            self.assertTrue(st.is_continue_round, round_type)
            self.assertTrue(st.continue_freeze_held, round_type)
            play.assert_any_call("continue.mp3", kind="continue")
            record.assert_called_once_with(1)
            self.assertEqual(WindowVolume.category_of(st), WindowVolume.CONTINUE, round_type)
            self.assertNotIn("do_skip", started)
            self.assertEqual(monitor.logs.count(GROUP_LINE.format(round_type)), 1,
                             "全続行の行は二度出さない（続行の行は別）")
            self.assertTrue(any("【全続行 → 続行リストで続行】" in m for m in monitor.logs), monitor.logs)

    def test_unlisted_terror_stays_all_continue_without_a_second_line(self):
        for round_type, key, revealed in (("Fog", FOG_KEY, True), ("8 Pages", PAGES_KEY, False)):
            SharedState.continue_round_reset()
            monitor = self._check_unknown(round_type, key)
            started, play, record = self._killers(monitor, [UNLISTED], revealed=revealed)
            st = monitor.st
            self.assertFalse(st.is_continue_round, round_type)
            self.assertTrue(st.all_continue_round, round_type)
            self.assertEqual(WindowVolume.category_of(st), WindowVolume.ALL_CONTINUE, round_type)
            self.assertEqual(monitor.logs.count(GROUP_LINE.format(round_type)), 1, monitor.logs)
            play.assert_not_called()
            self.assertNotIn("do_skip", started)

    def test_without_the_unknown_line_it_is_still_logged_when_the_terror_is_known(self):
        """テラー不明の行が来ないラウンド（set だけ）は今までどおり、分かった時点で 1 回出る"""
        monitor = self._monitor(HOSHI, "Fog", keep_on={FOG_KEY: {LISTED}})
        self._killers(monitor, [UNLISTED], revealed=True)
        self.assertEqual(monitor.logs.count(GROUP_LINE.format("Fog")), 1, monitor.logs)
        self.assertTrue(monitor.st.all_continue_round)

    def test_a_new_round_logs_again(self):
        monitor = self._check_unknown("Fog", FOG_KEY)
        monitor.st.round_seq += 1
        monitor.st.all_continue_round = False
        self._unknown(monitor, "Fog")
        self.assertEqual(monitor.logs.count(GROUP_LINE.format("Fog")), 2, monitor.logs)

    def test_yakiimo_fog_is_not_set_while_unknown(self):
        monitor = self._monitor(YAKI, "Fog")
        started = self._unknown(monitor, "Fog")
        self.assertFalse(monitor.st.all_continue_round)
        self.assertEqual(WindowVolume.category_of(monitor.st), WindowVolume.OTHER)
        self.assertEqual(started, [])
        self.assertFalse(any("【全続行】" in m for m in monitor.logs))

    def test_a_skip_moon_is_not_set_while_unknown(self):
        monitor = self._monitor(HOSHI, "Twilight", skip_rounds={"Twilight"})
        self._unknown(monitor, "Twilight")
        self.assertFalse(monitor.st.all_continue_round)

    def test_public_window_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_PUBLIC, "Fog", continue_rounds={"Fog"})
        self._unknown(monitor, "Fog")
        self.assertFalse(monitor.st.all_continue_round)


class TestPureFunction(unittest.TestCase):
    def f(self, itype, round_type, **kw):
        return GroupRound.is_all_continue_regardless_of_terror(itype, round_type, **kw)

    def test_first_moon_is_yes(self):
        for moon in ("Blood Moon", "Twilight"):
            self.assertTrue(self.f(HOSHI, moon), moon)
            self.assertTrue(self.f(YAKI, moon), moon)
        self.assertTrue(self.f(HOSHI, "Mystic Moon"))
        self.assertTrue(self.f(HOSHI, "Solstice"))

    def test_moon_exceptions_are_no(self):
        self.assertFalse(self.f(HOSHI, "Twilight", skip_moons={"Twilight"}))
        self.assertTrue(self.f(HOSHI, "Twilight", skip_moons={"Blood Moon"}))
        self.assertFalse(self.f(HOSHI, "Blood Moon", moon_repeat=True))
        self.assertFalse(self.f(YAKI, "Mystic Moon"))
        self.assertFalse(self.f(YAKI, "Solstice"))

    def test_fog_and_the_always_continue_rounds(self):
        self.assertTrue(self.f(HOSHI, "Fog"))
        self.assertTrue(self.f(HOSHI, "Fog (Alternate)"))
        self.assertFalse(self.f(YAKI, "Fog"))
        for itype in (HOSHI, YAKI):
            for name in GroupRound.ALWAYS_CONTINUE_ROUNDS:
                self.assertTrue(self.f(itype, name), (itype, name))
            for name in ("Classic", "Bloodbath", "Sabotage", "Alternate"):
                self.assertFalse(self.f(itype, name), (itype, name))

    def test_not_a_group_instance_is_no(self):
        for itype in (config.INSTANCE_PRIVATE, config.INSTANCE_PUBLIC):
            self.assertFalse(self.f(itype, "Fog"))
            self.assertFalse(self.f(itype, "8 Pages"))

    def test_it_agrees_with_decide(self):
        """decide が（テラーを問わず）CONTINUE を返すものだけ yes。焼き芋 Fog は Killers 行しだいなので外す"""
        names = (*GroupRound.ALWAYS_CONTINUE_ROUNDS, *GroupRound.MOONS, "Fog", "Classic", "Bloodbath", "Sabotage")
        for itype in (HOSHI, YAKI):
            for name in names:
                for repeat in (False, True):
                    for skip in (set(), {name}):
                        want = GroupRound.decide(itype, name, [7], killers_round_type="Fog",
                                                 moon_repeat=repeat, skip_moons=skip) == GroupRound.CONTINUE
                        if itype == YAKI and name == "Fog":
                            want = False        # Killers 行が Fog だと SKIP（含めない）
                        self.assertEqual(self.f(itype, name, moon_repeat=repeat, skip_moons=skip), want,
                                         (itype, name, repeat, skip))


class TestPrivate(_Case):
    def _fog(self, **kw):
        return self._monitor(config.INSTANCE_PRIVATE, "Fog", keep_on={FOG_KEY: {LISTED}},
                             continue_rounds={"Fog"}, **kw)

    def test_all_continue_round_is_set_while_unknown(self):
        monitor = self._fog()
        started = self._unknown(monitor, "Fog")
        st = monitor.st
        self.assertTrue(st.all_continue_round)
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.ALL_CONTINUE)
        self.assertEqual(monitor.logs.count(PRIVATE_LINE.format("Fog")), 1, monitor.logs)
        self.assertNotIn("do_skip", started)

    def test_unlisted_later_keeps_one_line(self):
        monitor = self._fog()
        self._unknown(monitor, "Fog")
        self._killers(monitor, [UNLISTED], revealed=True)
        self.assertFalse(monitor.st.is_continue_round)
        self.assertTrue(monitor.st.all_continue_round)
        self.assertEqual(monitor.logs.count(PRIVATE_LINE.format("Fog")), 1, monitor.logs)

    def test_listed_later_is_a_continue_round(self):
        monitor = self._fog()
        self._unknown(monitor, "Fog")
        started, play, record = self._killers(monitor, [LISTED], revealed=True)
        st = monitor.st
        self.assertTrue(st.is_continue_round)
        self.assertTrue(st.continue_freeze_held)
        play.assert_any_call("continue.mp3", kind="continue")
        self.assertEqual(WindowVolume.category_of(st), WindowVolume.CONTINUE)
        self.assertNotIn("do_skip", started)
        self.assertEqual(monitor.logs.count(PRIVATE_LINE.format("Fog")), 1, monitor.logs)

    def test_a_round_not_in_the_setting_is_unchanged(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE, "Fog", continue_rounds={"Classic"})
        self._unknown(monitor, "Fog")
        self.assertFalse(monitor.st.all_continue_round)

    def test_hands_free_is_unchanged(self):
        SharedState.set_hands_free(True)
        monitor = self._fog()
        started = self._unknown(monitor, "Fog")
        self.assertFalse(monitor.st.all_continue_round)
        self.assertIn("do_skip", started, "放置モードは今までどおり即自爆")
        self.assertFalse(any("ラウンド指定で続行" in m for m in monitor.logs))
