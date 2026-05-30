import unittest
from unittest.mock import patch, MagicMock
import time
import sys
import json
from datetime import datetime
from pathlib import Path

# Windowsライブラリをモック化
sys.modules['win32gui'] = MagicMock()
sys.modules['keyboard'] = MagicMock()
sys.modules['pydirectinput'] = MagicMock()

import WindowOperator
import ConnectDB
import PlaySound
import LogParser
import RoundDecision
import Statistics
import GUI
import LogMonitor
import SharedState
import config
from State import WindowConfig

ConnectDB.SUPABASE_URL = "https://example.supabase.co"
ConnectDB.SUPABASE_KEY = "test-key"


class TestConfigResources(unittest.TestCase):
    def test_static_resources_resolve_from_repo_root(self):
        self.assertTrue(config.TERRORS["classic"])
        for path in (
            config.VOICE_CONTINUE,
            config.VOICE_FOG,
            config.VOICE_ITEM_LOST,
            config.VOICE_INTERMISSION,
            config.VOICE_FOXY,
        ):
            self.assertTrue(Path(path).exists(), path)


class TestConnectDbEnv(unittest.TestCase):
    def test_env_file_candidates_include_source_and_repo_locations(self):
        candidates = ConnectDB.env_file_candidates()
        repo_root = Path(__file__).resolve().parent.parent
        legacy_resource_dir = repo_root / "ToNAutoBeginner"

        self.assertTrue(any(path.parent.name == "src" and path.name == ".env" for path in candidates))
        self.assertTrue(any(path.parent == repo_root and path.name == ".env" for path in candidates))
        self.assertTrue(any(path.parent == legacy_resource_dir and path.name == ".env" for path in candidates))

    def test_env_file_candidates_prefers_nuitka_containing_dir(self):
        class Compiled:
            containing_dir = "C:/packed-app"

        ConnectDB.__compiled__ = Compiled()
        try:
            self.assertEqual(ConnectDB.env_file_candidates()[0], Path("C:/packed-app") / ".env")
        finally:
            del ConnectDB.__compiled__

# ═══════════════════════════════════════════════
#  WindowOperator.py
# ═══════════════════════════════════════════════
class TestFocusWindow(unittest.TestCase):
    def test_hwnd_zero_skips(self):
        """hwnd=0の時は何もしない"""
        with patch('win32gui.SetForegroundWindow') as mock:
            WindowOperator.focus_window(0)
            mock.assert_not_called()

    def test_focus_calls_setforeground(self):
        """hwnd!=0の時はSetForegroundWindowを呼ぶ"""
        with patch('win32gui.SetForegroundWindow') as mock:
            WindowOperator.focus_window(123)
            mock.assert_called_once_with(123)

class TestHoldKey(unittest.TestCase):
    def test_zero_sec_skips(self):
        """sec=0の時は何もしない"""
        with patch('keyboard.press') as mock:
            WindowOperator.hold_key('w', 0.0)
            mock.assert_not_called()

    def test_holds_key(self):
        """press→sleep→releaseの順で呼ばれる"""
        with patch('keyboard.press') as mock_press, \
             patch('keyboard.release') as mock_release:
            WindowOperator.hold_key('w', 0.1)
            mock_press.assert_called_once_with('w')
            mock_release.assert_called_once_with('w')

class TestClickAt(unittest.TestCase):
    def test_click(self):
        """mouseDown→mouseUpの順で呼ばれる"""
        with patch('pydirectinput.mouseDown') as mock_down, \
             patch('pydirectinput.mouseUp') as mock_up:
            WindowOperator.click()
            mock_down.assert_called_once()
            mock_up.assert_called_once()

# ═══════════════════════════════════════════════
#  LogParser.py
# ═══════════════════════════════════════════════
class TestLogParser(unittest.TestCase):
    def test_round_start_extracts_round_and_map_id(self):
        event = LogParser.parse("This round is taking place at Facility (12) and the round type is Fog")
        self.assertEqual(event.kind, LogParser.EVENT_ROUND_START)
        self.assertEqual(event.round_type, "Fog")
        self.assertEqual(event.map_id, 12)
        self.assertEqual(event.raw_map, "Facility (12)")

    def test_round_start_without_map_id_uses_zero(self):
        event = LogParser.parse("This round is taking place at Unknown Map and the round type is Run")
        self.assertEqual(event.kind, LogParser.EVENT_ROUND_START)
        self.assertEqual(event.map_id, 0)

    def test_killers_set_parses_terror_ids(self):
        event = LogParser.parse("Killers have been set - 1 2 3 // Round type is Double Trouble")
        self.assertEqual(event.kind, LogParser.EVENT_KILLERS_SET)
        self.assertEqual(event.round_type, "Double Trouble")
        self.assertEqual(event.terror_ids, [1, 2])

    def test_user_auth_and_joining_parse_after_prefix(self):
        prefix = "2026.05.24 10:00:00 Log - "
        user = LogParser.parse(prefix + "User Authenticated: tester (usr_12345678-1234-1234-1234-123456789abc)")
        joining = LogParser.parse(prefix + "[Behaviour] Joining wrld_abc:12345~friends~region(us)")
        self.assertEqual(user.kind, LogParser.EVENT_USER_AUTH)
        self.assertEqual(user.user_id, "usr_12345678-1234-1234-1234-123456789abc")
        self.assertEqual(joining.kind, LogParser.EVENT_JOINING)
        self.assertIn("~friends", joining.suffix)

# ═══════════════════════════════════════════════
#  RoundDecision.py
# ═══════════════════════════════════════════════
class TestRoundDecision(unittest.TestCase):
    def test_normalize_killer_ids_applies_alternate_and_unbound_offsets(self):
        self.assertEqual(RoundDecision.normalize_killer_ids([1], "Alternate"), [135])
        self.assertEqual(RoundDecision.normalize_killer_ids([1], "Classic", "Unbound"), [201])

    def test_decide_killers_uses_tnl_and_open_special_target(self):
        keep_on = {"Classic/クラシック": {42}}
        decision = RoundDecision.decide_killers(keep_on, [42], "Classic", 0, False)
        self.assertTrue(decision.is_continue_round)
        self.assertFalse(decision.is_open_special_round_target)

        target_id = next(iter(config.OpenSpecialRound_TERROR_IDS))
        special = RoundDecision.decide_killers({}, [target_id], "Classic", 0, True)
        self.assertTrue(special.is_continue_round)
        self.assertTrue(special.is_open_special_round_target)


class TestLogMonitorHoshiimo(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        LogMonitor._continue_round_reset()
        LogMonitor.set_hands_free(False)

    def tearDown(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        LogMonitor._continue_round_reset()
        LogMonitor.set_hands_free(False)

    def _monitor(self, *, hoshiimo_skip: bool = True, keep_on: dict | None = None):
        cfg = WindowConfig(
            hoshiimo_skip=hoshiimo_skip,
            do_skip=True,
            voice_continue="continue.mp3",
        )
        return LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None, window_idx=1)

    def test_hoshiimo_allows_continue_voice_outside_skip_rounds(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        monitor = self._monitor(
            hoshiimo_skip=True,
            keep_on={"Double Trouble/ダブルトラブル": {42}},
        )
        monitor.st.in_round = True
        monitor.st.round_type = "Double Trouble"

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers([42], "Double Trouble", revealed=False)

        mock_play.assert_called_once_with("continue.mp3")
        self.assertTrue(monitor.st.is_continue_round)

    def test_hoshiimo_does_not_run_normal_skip_for_non_skip_rounds(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Double Trouble"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Double Trouble", revealed=False)

        mock_thread.assert_not_called()

    def test_hoshiimo_hands_free_still_only_allows_voice(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        LogMonitor.set_hands_free(True)
        monitor = self._monitor(
            hoshiimo_skip=True,
            keep_on={"Double Trouble/ダブルトラブル": {42}},
        )
        monitor.st.in_round = True
        monitor.st.round_type = "Double Trouble"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([42], "Double Trouble", revealed=False)

        mock_play.assert_called_once_with("continue.mp3")
        mock_thread.assert_not_called()

    def test_hoshiimo_skip_round_still_uses_dedicated_skip(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Classic", revealed=False)

        mock_thread.assert_called_once()

    def test_hoshiimo_skip_clears_stale_continue_state(self):
        SharedState.set_instance_type(config.INSTANCE_HOSHIIMO)
        monitor = self._monitor(hoshiimo_skip=True)
        monitor.st.in_round = True
        monitor.st.round_type = "Classic"
        monitor.st.is_continue_round = True

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._on_killers([99], "Classic", revealed=False)

        self.assertFalse(monitor.st.is_continue_round)
        mock_thread.assert_called_once()

    def test_round_start_clears_stale_continue_state(self):
        monitor = self._monitor()
        monitor.st.is_continue_round = True

        monitor._process("This round is taking place at Facility (12) and the round type is Classic")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(monitor.st.round_type, "Classic")


class TestLogMonitorItemLostVoice(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        LogMonitor._continue_round_reset()
        LogMonitor.set_hands_free(False)

    def tearDown(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        LogMonitor._continue_round_reset()
        LogMonitor.set_hands_free(False)

    def _monitor(self, *, auto_begin: bool = False):
        cfg = WindowConfig(
            auto_begin=auto_begin,
            voice_item_lost="lost.mp3",
        )
        return LogMonitor.LogMonitor(cfg, {}, lambda _msg: None, window_idx=1)

    def test_item_lost_voice_plays_when_verified_end_detects_loss(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("Verified Round End")

        mock_play.assert_called_once_with("lost.mp3")
        self.assertTrue(monitor.st.waiting_for_equip)
        self.assertTrue(monitor.st.item_lost_announced)

    def test_item_lost_voice_is_not_duplicated_by_round_over(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.round_type = "Run"

        with patch.object(PlaySound, "play_sound") as mock_play, \
             patch.object(ConnectDB, "send_ToNRoundStatistics"):
            monitor._process("Verified Round End")
            monitor._process("RoundOver")

        mock_play.assert_called_once_with("lost.mp3")

    def test_round_start_resets_item_lost_voice_announcement(self):
        monitor = self._monitor(auto_begin=False)
        monitor.st.item_lost_announced = True

        monitor._process("This round is taking place at Facility (12) and the round type is Classic")

        self.assertFalse(monitor.st.item_lost_announced)


class TestLogMonitorFogRound(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PRIVATE)
        LogMonitor._continue_round_reset()
        LogMonitor.set_hands_free(False)

    def tearDown(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        LogMonitor._continue_round_reset()
        LogMonitor.set_hands_free(False)

    def _monitor(self, *, keep_on: dict | None = None):
        cfg = WindowConfig(
            do_skip=True,
            voice_fog="fog.mp3",
            voice_continue="continue.mp3",
        )
        return LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _msg: None, window_idx=1)

    def test_fog_reveal_skip_releases_fog_freeze_before_skip(self):
        monitor = self._monitor()
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertFalse(LogMonitor._CONTINUE_ROUND_EVENT.is_set())

        with patch.object(LogMonitor.threading, "Thread") as mock_thread:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertFalse(monitor.st.is_continue_round)
        self.assertTrue(LogMonitor._CONTINUE_ROUND_EVENT.is_set())
        mock_thread.assert_called_once()

    def test_fog_reveal_continue_does_not_double_count_freeze(self):
        monitor = self._monitor(keep_on={"Fog/霧": {44}})
        with patch.object(PlaySound, "play_sound"):
            monitor._process("This round is taking place at Facility (12) and the round type is Fog")

        self.assertEqual(LogMonitor._CONTINUE_ROUND_COUNT, 1)

        with patch.object(PlaySound, "play_sound") as mock_play:
            monitor._process("Killers have been revealed - 44 0 0 // Round type is Fog")

        self.assertTrue(monitor.st.is_continue_round)
        self.assertEqual(LogMonitor._CONTINUE_ROUND_COUNT, 1)
        mock_play.assert_not_called()

# ═══════════════════════════════════════════════
#  Statistics.py
# ═══════════════════════════════════════════════
class TestStatistics(unittest.TestCase):
    def test_binomial_upper_matches_direct_sum(self):
        n = 12
        k = 4
        p = 0.2
        direct = sum(Statistics.binomial_pmf(n, i, p) for i in range(k, n + 1))
        self.assertAlmostEqual(Statistics.binomial_pmf_upper(n, k, p), direct, places=12)

    def test_binomial_upper_uses_lower_tail_for_common_side(self):
        n = 20
        k = 8
        p = 0.5
        direct = sum(Statistics.binomial_pmf(n, i, p) for i in range(k, n + 1))
        self.assertAlmostEqual(Statistics.binomial_pmf_upper(n, k, p), direct, places=12)

    def test_round_filtering_and_summary_strip_round_names(self):
        rows = [
            {"date": 20260526, "time": 100000, "round": " Fog ", "terror_ids": [1, 2], "map_id": 12},
            {"date": 20260526, "time": 110000, "round": "Unbound", "terror_ids": [201], "map_id": 49},
            {"date": 20260527, "time": 100000, "round": "Fog", "terror_ids": [3], "map_id": 13},
        ]
        filtered = Statistics.filter_rows(
            rows,
            datetime(2026, 5, 26, 0, 0, 0),
            datetime(2026, 5, 26, 23, 59, 59),
            {"Fog"},
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(Statistics.available_rounds(rows), ["Fog", "Unbound"])
        self.assertEqual(Statistics.round_summary(filtered), [("Fog", 1, 2)])

    def test_map_name_for_id_uses_round_to_resolve_duplicate_ids(self):
        self.assertEqual(Statistics.map_name_for_id(1, "Run"), "Dring King's Citadel")
        self.assertEqual(Statistics.map_name_for_id(1, "Classic"), "Sewers")
        self.assertEqual(Statistics.map_name_for_id(999, "Classic"), "Map 999")

    def test_map_counts_for_terror_uses_map_names(self):
        rows = [
            {"round": "Run", "terror_ids": [1], "map_id": 1},
            {"round": "Classic", "terror_ids": [1, 1], "map_id": 1},
            {"round": "Classic", "terror_ids": [2], "map_id": 12},
        ]
        self.assertEqual(
            Statistics.map_counts_for_terror(rows, 1),
            [("Sewers", 2), ("Dring King's Citadel", 1)],
        )


class TestGuiRoundHelpers(unittest.TestCase):
    def test_ordered_round_entries_include_deferred_rounds_and_aliases(self):
        entries = GUI._ordered_round_entries(["Unbound", "Fog", "Fog (Alternate)", "Ghost Alternate", "Mystic Moon"])

        self.assertIn(("Classic", "Classic"), entries)
        self.assertIn(("Run", "Run"), entries)
        self.assertLess(entries.index(("Fog", "Fog")), entries.index(("Fog(Alternate)", "Fog (Alternate)")))
        self.assertIn(("Ghost(Alternate)", "Ghost Alternate"), entries)

    def test_round_chart_colors_are_not_collapsed_to_one_color(self):
        self.assertGreater(len(set(GUI.ROUND_CHART_COLORS)), 3)


# ═══════════════════════════════════════════════
#  ConnectDB.py
# ═══════════════════════════════════════════════
class TestGetTransformedUid(unittest.TestCase):
    def test_round_filters_url_encode_round_names(self):
        self.assertEqual(
            ConnectDB._in_filter("round", ("Fog (Alternate)",)),
            "&round=in.(Fog%20%28Alternate%29)",
        )

    def test_send_Users(self):
        """新規ユーザー登録"""
        # 1回目: 存在確認 → 空（新規）
        # 2回目: 既存transformed_uid一覧取得
        # 3回目: POST登録
        mock_res1 = MagicMock()
        mock_res1.__enter__ = MagicMock(return_value=mock_res1)
        mock_res1.__exit__ = MagicMock(return_value=False)
        mock_res1.read.return_value = json.dumps([]).encode()  # 存在しない

        mock_res2 = MagicMock()
        mock_res2.__enter__ = MagicMock(return_value=mock_res2)
        mock_res2.__exit__ = MagicMock(return_value=False)
        mock_res2.read.return_value = json.dumps([{"transformed_uid": 123}]).encode()

        mock_res3 = MagicMock()
        mock_res3.__enter__ = MagicMock(return_value=mock_res3)
        mock_res3.__exit__ = MagicMock(return_value=False)
        mock_res3.status = 201

        with patch('urllib.request.urlopen', side_effect=[mock_res1, mock_res2, mock_res3]):
            result = ConnectDB.send_Users("usr_new")
            self.assertNotEqual(result, 123)
            self.assertIsNotNone(result)

    def test_send_users_existing_user(self):
        """既存ユーザーの場合はtransformed_uidをそのまま返す"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        mock_res.read.return_value = json.dumps([{"VRChat_uid": "usr-existing", "transformed_uid": 123}]).encode()

        with patch('urllib.request.urlopen', return_value=mock_res):
            result = ConnectDB.send_Users("usr-existing")
            self.assertEqual(result, 123)

    def test_send_Users_full(self):
        """ユーザー登録限界"""
        existing = [{"VRChat_uid": "usr-existing", "transformed_uid": i} for i in range(-32768, 32767)]
        mock_res1 = MagicMock()
        mock_res1.__enter__ = MagicMock(return_value=mock_res1)
        mock_res1.__exit__ = MagicMock(return_value=False)
        mock_res1.read.return_value = json.dumps([]).encode()

        mock_res2 = MagicMock()
        mock_res2.__enter__ = MagicMock(return_value=mock_res2)
        mock_res2.__exit__ = MagicMock(return_value=False)
        mock_res2.read.return_value = json.dumps(existing).encode()

        with patch('urllib.request.urlopen', side_effect=[mock_res1, mock_res2]):
            result = ConnectDB.send_Users("usr_new")
            self.assertIsNone(result)

    def test_existing_user(self):
        """既存ユーザーのtransformed_uidを返す"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        mock_res.read.return_value = json.dumps([{"VRChat_uid": "usr-existing", "transformed_uid": 123}]).encode()

        with patch('urllib.request.urlopen', return_value=mock_res):
            result = ConnectDB.get_transformed_uid("usr-existing")
            self.assertEqual(result, 123)

    def test_new_user_calls_send_users(self):
        """存在しない場合はsend_Usersを呼ぶ"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        mock_res.read.return_value = json.dumps([]).encode()  # 空 = 未登録

        with patch('urllib.request.urlopen', return_value=mock_res), \
             patch.object(ConnectDB, 'send_Users', return_value=123) as mock_send:
            result = ConnectDB.get_transformed_uid("usr_new")
            mock_send.assert_called_once_with("usr_new")
            self.assertEqual(result, 123)

    def test_error_returns_none(self):
        """エラー時はNoneを返す"""
        with patch('urllib.request.urlopen', side_effect=Exception("network error")):
            result = ConnectDB.get_transformed_uid("usr_abc123")
            self.assertIsNone(result)

    def test_get_ToNRoundStatistics(self):
        """集計データを取得"""
        mock_res = MagicMock()
        mock_res.__enter__ = MagicMock(return_value=mock_res)
        mock_res.__exit__ = MagicMock(return_value=False)
        expected = [{
            "created_at": "2026-05-24T12:34:56+00:00",
            "round": "Unbound",
            "terror_ids": [1],
            "map_id": 2,
            "transformed_uid": 123
        }]
        mock_res.read.return_value = json.dumps(expected).encode()
        with patch('urllib.request.urlopen', return_value=mock_res) as mock_urlopen:
            result = ConnectDB.get_ToNRoundStatistics()
            self.assertEqual(result, expected)
            requested_url = mock_urlopen.call_args.args[0].full_url
            self.assertIn("round=not.in.(Classic,Run)", requested_url)

        mock_res.read.return_value = json.dumps(expected).encode()
        with patch('urllib.request.urlopen', return_value=mock_res) as mock_urlopen:
            result = ConnectDB.get_ToNRoundStatistics(exclude_rounds=None, include_rounds=("Classic", "Run"))
            self.assertEqual(result, expected)
            requested_url = mock_urlopen.call_args.args[0].full_url
            self.assertIn("round=in.(Classic,Run)", requested_url)

    def test_get_ToNRoundStatistics_fetches_all_pages(self):
        first_page = [
            {"created_at": f"2026-05-24T12:{i % 60:02d}:00+00:00", "round": "Unbound", "terror_ids": [1]}
            for i in range(1000)
        ]
        second_page = [{"created_at": "2026-05-24T13:00:00+00:00", "round": "Unbound", "terror_ids": [2]}]

        mock_res1 = MagicMock()
        mock_res1.__enter__ = MagicMock(return_value=mock_res1)
        mock_res1.__exit__ = MagicMock(return_value=False)
        mock_res1.read.return_value = json.dumps(first_page).encode()

        mock_res2 = MagicMock()
        mock_res2.__enter__ = MagicMock(return_value=mock_res2)
        mock_res2.__exit__ = MagicMock(return_value=False)
        mock_res2.read.return_value = json.dumps(second_page).encode()

        with patch('urllib.request.urlopen', side_effect=[mock_res1, mock_res2]) as mock_urlopen:
            result = ConnectDB.get_ToNRoundStatistics()

        self.assertEqual(len(result), 1001)
        urls = [call.args[0].full_url for call in mock_urlopen.call_args_list]
        self.assertIn("offset=0", urls[0])
        self.assertIn("offset=1000", urls[1])

# ═══════════════════════════════════════════════
#  PlaySound.py
# ═══════════════════════════════════════════════
class TestPlaySound(unittest.TestCase):
    def test_get_sound_volume(self):
        result = PlaySound.get_sound_volume()
        self.assertEqual(result, 1.0)

    def test_set_sound_volume(self):
        global sound_volume
        PlaySound.set_sound_volume(0.3)
        result = PlaySound.get_sound_volume()
        self.assertEqual(result, 0.3)

    def test_play_sound_calls_popen(self):
        """ファイルが存在する場合にPopenが呼ばれる"""
        with patch('subprocess.Popen') as mock_popen, \
            patch('pathlib.Path.exists', return_value=True):
            PlaySound.play_sound("voice/continue.mp3")
            import time; time.sleep(0.1)  # スレッド起動待ち
            mock_popen.assert_called_once()

    def test_play_sound_skips_if_not_exists(self):
        """ファイルが存在しない場合はPopenを呼ばない"""
        with patch('subprocess.Popen') as mock_popen, \
            patch('pathlib.Path.exists', return_value=False):
            PlaySound.play_sound("voice/notfound.mp3")
            time.sleep(0.1)
            mock_popen.assert_not_called()

    def test_play_sound_skips_empty_path(self):
        """空文字の場合はPopenを呼ばない"""
        with patch('subprocess.Popen') as mock_popen:
            PlaySound.play_sound("")
            mock_popen.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
