"""ログのテラーは番号でなく名前で出す（名前が無ければ #番号）。debug.log の [事象] の行は独自の番号"""
from tests.support import *  # noqa: F401,F403


class _Case(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        SharedState.set_hands_free(False)
        SharedState.continue_round_reset()
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.set_instance_type, config.INSTANCE_PUBLIC)
        self.addCleanup(SharedState.set_hands_free, False)
        self.addCleanup(SharedState.continue_round_reset)

    def _monitor(self, round_type="Classic", **cfg_kw):
        cfg = WindowConfig(voice_continue="continue.mp3", **cfg_kw)
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.in_round = True
        monitor.st.round_type = round_type
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids, round_type=None, revealed=False):
        with patch.object(LogMonitor.threading, "Thread"), patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), round_type or monitor.st.round_type, revealed=revealed)
        return monitor.logs


class TestPublicLog(_Case):
    def test_set_shows_the_name(self):
        logs = self._killers(self._monitor(), [50])
        self.assertIn("[窓1] Terror set: Don't Touch Me / Classic", logs)

    def test_revealed_shows_the_name(self):
        logs = self._killers(self._monitor("Fog"), [5], revealed=True)
        self.assertTrue(any(m.startswith("[窓1] Terror revealed: ") and m.endswith(" / Fog") for m in logs), logs)
        self.assertFalse(any("ID:" in m or "[5]" in m for m in logs), logs)

    def test_an_alternate_terror_is_shown_by_name(self):
        logs = self._killers(self._monitor("Alternate"), [1])
        self.assertIn("[窓1] Terror set: Whiteface / Alternate", logs)

    def test_eight_pages_are_shown_by_name(self):
        """今 Huggy と出ているものは Huggy のまま。今 ID:172 と出ていた Baldi は Baldi"""
        self.assertIn("[窓1] Terror set: Huggy / 8 Pages", self._killers(self._monitor("8 Pages"), [14]))
        self.assertIn("[窓1] Terror set: Baldi / 8 Pages", self._killers(self._monitor("8 Pages"), [46]))

    def test_a_number_without_a_name_is_shown_with_a_hash(self):
        logs = self._killers(self._monitor(), [99999])
        self.assertIn("[窓1] Terror set: #99999 / Classic", logs)

    def test_several_terrors_are_joined_with_commas(self):
        logs = self._killers(self._monitor("Bloodbath"), [50, 7, 3])
        self.assertTrue(any("Terror set: Don't Touch Me, " in m for m in logs), logs)

    def test_the_decision_line_shows_the_names(self):
        monitor = self._monitor(do_skip=True)
        monitor.st.terror_ids = [50]
        SharedState.set_list_source(None)
        monitor.keepOn_set = {"Classic/クラシック": {50}}
        with patch.object(LogMonitor.threading, "Thread"), patch.object(PlaySound, "play_sound"):
            monitor._decide_with_keep_on_set("Classic")
        self.assertTrue(any(m.startswith("[窓1] 判定: Don't Touch Me / Classic ") for m in monitor.logs),
                        monitor.logs)


class TestHandsFreeReasons(_Case):
    def test_the_reasons_show_names_not_numbers(self):
        monitor = self._monitor()
        monitor.st.open_special_round_wins = config.OPEN_SPECIAL_ROUND_TARGET_WINS
        reason = monitor._hands_free_skip_reason([7])
        self.assertIn("即自爆", reason)
        self.assertNotIn("[7]", reason)
        self.assertIn(TerrorIds.name(7), reason)
        monitor.st.open_special_round_wins = 0
        monitor.st.round_type = "Double Trouble"
        reason = monitor._hands_free_skip_reason([7, 3])
        self.assertIn(f"{TerrorIds.name(7)}, {TerrorIds.name(3)}", reason)
        monitor.st.round_type = "Classic"
        monitor.st.item_id = 0
        monitor.cfg.cancel_afk = True
        reason = monitor._hands_free_skip_reason([7])
        self.assertIn(TerrorIds.name(7), reason)
        self.assertNotIn("[7]", reason)
        monitor.st.item_id = 5
        reason = monitor._hands_free_skip_reason([7])
        self.assertIn(TerrorIds.name(7), reason)
        self.assertNotIn("[7]", reason)


class TestDebugEventLine(_Case):
    """debug.log の [事象] の行のテラーは、独自の番号（ログの番号そのままではない）"""

    def _line(self, monitor, event):
        with patch.object(DebugLog, "write") as write:
            monitor._debug_event(event)
        self.assertEqual(write.call_count, 1)
        return write.call_args.args[0]

    def test_alternate_is_1000_plus(self):
        event = LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_SET, round_type="Alternate", terror_ids=[1])
        self.assertIn("テラー=[1001]", self._line(self._monitor("Alternate"), event))

    def test_eight_pages_are_3000_plus_the_first_number(self):
        event = LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_SET, round_type="8 Pages", terror_ids=[14, 3])
        self.assertIn("テラー=[3014]", self._line(self._monitor("8 Pages"), event))

    def test_unbound_follows_the_round_of_the_window(self):
        event = LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_SET, round_type="Unbound", terror_ids=[5])
        self.assertIn("テラー=[2005]", self._line(self._monitor("Unbound"), event))

    def test_classic_is_the_same_number(self):
        event = LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_SET, round_type="Classic", terror_ids=[5])
        self.assertIn("テラー=[5]", self._line(self._monitor(), event))

    def test_the_input_is_not_changed(self):
        ids = [1]
        event = LogParser.LogEvent(kind=LogParser.EVENT_KILLERS_SET, round_type="Alternate", terror_ids=ids)
        self._line(self._monitor("Alternate"), event)
        self.assertEqual(event.terror_ids, [1])


class TestStatisticsNames(unittest.TestCase):
    def test_a_terror_not_in_the_table_is_shown_with_a_hash(self):
        self.assertEqual(Statistics.terror_name(999999, config.TERRORS), '#999999')
        self.assertEqual(Statistics.terror_name(0, config.TERRORS), 'Huggy')
