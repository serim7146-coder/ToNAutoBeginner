"""ToN ListTool の主催リスト・参加者・タブ"""
from tests.support import *  # noqa: F401,F403




class TestWindowTabHwndChoices(unittest.TestCase):
    def test_set_hwnd_choices_selects_requested_hwnd_without_discovery(self):
        class FakeVar:
            def __init__(self):
                self.value = "未選択"

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        class FakeCombo(dict):
            pass

        tab = type("FakeTab", (), {})()
        tab._hwnd_map = {}
        tab.cb_hwnd = FakeCombo()
        tab.v_hwnd_sel = FakeVar()

        with patch.object(VRChatDiscovery, "get_vrchat_windows") as mock_discover:
            mainGUI.WindowTab.set_hwnd_choices(tab, [0x1111, 0x2222], selected_hwnd=0x2222)

        mock_discover.assert_not_called()
        self.assertEqual(tab.cb_hwnd["values"], ["[1] HWND=0x00001111", "[2] HWND=0x00002222"])
        self.assertEqual(tab.v_hwnd_sel.get(), "[2] HWND=0x00002222")




class TestAppTabLifecycle(unittest.TestCase):
    def test_rebuild_tabs_destroys_old_tabs(self):
        class FakeNotebook:
            def __init__(self):
                self.forgot = []
                self.added = []

            def forget(self, tab):
                self.forgot.append(tab)

            def add(self, tab, text):
                self.added.append((tab, text))

        class OldTab:
            def __init__(self, idx):
                self.idx = idx
                self.destroyed = False

            def snapshot(self):
                return {"idx": self.idx}

            def destroy(self):
                self.destroyed = True

        class NewTab:
            def __init__(self, parent, idx, on_log_selected=None, on_settings_changed=None,
                         section_collapsed=None, on_section_toggled=None):
                self.parent = parent
                self.idx = idx
                self.on_log_selected = on_log_selected
                self.destroyed = False
                self.restored = None

            def restore(self, saved):
                self.restored = saved

            def destroy(self):
                self.destroyed = True

        app = type("FakeApp", (), {})()
        app.nb = FakeNotebook()
        app._on_tab_log_selected = lambda tab: None
        app._apply_tab_settings_live = lambda tab: None
        app._apply_saved_window_settings = lambda: None
        app._tab_sections = {}
        app._on_tab_section_toggled = lambda _key, _collapsed: None
        app.tabs = [OldTab(0), OldTab(1)]
        old_tabs = list(app.tabs)

        with patch.object(mainGUI, "WindowTab", NewTab):
            mainGUI.App._rebuild_tabs(app, 3)

        self.assertEqual(app.nb.forgot, old_tabs)
        self.assertTrue(all(tab.destroyed for tab in old_tabs))
        self.assertEqual(len(app.tabs), 3)
        self.assertEqual([tab.idx for tab in app.tabs], [0, 1, 2])
        self.assertEqual(len(app.nb.added), 3)
        self.assertEqual([tab.restored for tab in app.tabs], [{"idx": 0}, {"idx": 1}, None],
                         "残った窓は前の設定のまま。増えた窓は既定")

    def test_win_count_change_skips_rebuild_when_count_is_unchanged(self):
        class FakeVar:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        app = type("FakeApp", (), {})()
        app._running = False
        app.v_win_count = FakeVar(2)
        app.tabs = [object(), object()]
        app._rebuild_tabs = MagicMock()
        app._sync_launch_count = MagicMock()

        mainGUI.App._on_win_count_change(app)

        app._rebuild_tabs.assert_not_called()
        app._sync_launch_count.assert_called_once()




class TestHostSaveWishes(unittest.TestCase):
    """参加者別の続行希望（Sabotage のマーダー判定に使う）"""

    CLASSIC = "Classic/クラシック"
    MURDER = "Sabotage murder/サボタージュマーダー"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()

    def _write(self, raw):
        """以前の JSON 版と同じ中身を、いまの SQLite 版で作る"""
        path = Path(self._dir.name) / "host_state.sqlite3"
        write_host_state_like_json(path, raw)
        return str(path)

    def _member(self, name, data):
        return {"vrc_name": name, "data": data}

    def test_wishes_are_kept_per_participant(self):
        path = self._write({"version": 5, "tabs": [{"participants": [
            self._member("ソノア7", {self.CLASSIC: {"1": 1}}),
            self._member("ユウナ2858", {self.MURDER: {"5": 1}})]}]})

        keep_on, _meta, wishes = MatchTNL.load_host_state(path)

        self.assertEqual(wishes["ソノア7"], {self.CLASSIC: {1}})
        self.assertEqual(wishes["ユウナ2858"], {self.MURDER: {5}})
        self.assertEqual(keep_on, {self.CLASSIC: {1}, self.MURDER: {5}},
                         "畳んだ方はこれまでどおり")

    def test_waiting_players_are_in_the_wishes(self):
        """待機も名前ごとの希望に入れる。誰がいるかは窓ごとにこちらで見る
        （ToN ListTool は複窓だと全員を待機へ移すことがある）"""
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member("ソノア7", {self.CLASSIC: {"1": 1}})],
            "waiting": [self._member("まちびと", {self.CLASSIC: {"9": 1}})]}]})

        keep_on, meta, wishes = MatchTNL.load_host_state(path)

        self.assertEqual(set(wishes), {"ソノア7", "まちびと"})
        self.assertEqual(wishes["まちびと"], {self.CLASSIC: {9}})
        self.assertEqual(keep_on, {self.CLASSIC: {1}}, "共有リストは参加者だけ")
        self.assertEqual((meta["participants"], meta["listed"]), (1, 2))

    def test_the_same_name_in_two_tabs_is_merged(self):
        path = self._write({"version": 5, "tabs": [
            {"participants": [self._member("ソノア7", {self.CLASSIC: {"1": 1}})]},
            {"participants": [self._member("ソノア7", {self.CLASSIC: {"2": 1}})]}]})

        _keep_on, _meta, wishes = MatchTNL.load_host_state(path)

        self.assertEqual(wishes["ソノア7"], {self.CLASSIC: {1, 2}})

    def test_names_with_unusual_characters_are_kept_as_is(self):
        """完全一致で引く。正規化しない（実測で一致することを確認済み）"""
        names = ["Dynamic_Naël", "Miyα", "えだまめ-Salt", "あんてな〜"]
        path = self._write({"version": 5, "tabs": [{"participants": [
            self._member(n, {self.CLASSIC: {"1": 1}}) for n in names]}]})

        _keep_on, _meta, wishes = MatchTNL.load_host_state(path)

        self.assertEqual(sorted(wishes), sorted(names))

    def test_apply_host_wishes_replaces_the_dict(self):
        """中身の入れ替えではなく差し替え（読む窓が空の途中を見ない）。前の dict は触らない"""
        app = type("FakeApp", (), {})()
        app.host_wishes = {"だれか": {self.CLASSIC: {1}}}
        before = app.host_wishes

        mainGUI.App._apply_host_wishes(app, {"べつのひと": {self.MURDER: {5}}})

        self.assertIsNot(app.host_wishes, before, "新しい dict を代入すること")
        self.assertEqual(before, {"だれか": {self.CLASSIC: {1}}}, "前の dict は書き換えない")
        self.assertEqual(app.host_wishes, {"べつのひと": {self.MURDER: {5}}})




class TestLoadHostSave(unittest.TestCase):
    """ToN ListTool の主催リストを keepOn_set の形で読む"""

    CLASSIC = "Classic/クラシック"
    FOG = "Fog/霧"
    FOG_ALT = "Fog (Alternate)/霧 (Alternate)"

    def _member(self, data):
        return {"vrc_name": "someone", "original_name": "someone",
                "data": data, "memo": "", "created_at": "", "is_visible": False}

    def _write(self, raw):
        """以前の JSON 版と同じ中身を、いまの SQLite 版で作る"""
        path = Path(self._dir.name) / "host_state.sqlite3"
        write_host_state_like_json(path, raw)
        return str(path)

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()

    def test_two_participants_are_ored(self):
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 1, "2": 0}}),
                             self._member({self.CLASSIC: {"3": 1}})]}]})

        keep_on, meta, _wishes = MatchTNL.load_host_state(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1, 3}})
        self.assertEqual(meta["participants"], 2)

    def test_waiting_is_not_included(self):
        """待機は共有リストに混ぜない（名前ごとの希望には入る。TestWaitingList）"""
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 1}})],
            "waiting": [self._member({self.CLASSIC: {"99": 1}})]}]})

        keep_on, meta, _wishes = MatchTNL.load_host_state(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1}})
        self.assertEqual(meta["participants"], 1, "waiting は人数にも数えない")

    def test_all_tabs_are_folded_regardless_of_active_tab(self):
        path = self._write({"version": 5, "active_tab": 2, "tabs": [
            {"participants": [self._member({self.CLASSIC: {"1": 1}})]},
            {"participants": [self._member({self.CLASSIC: {"2": 1}})]},
            {"participants": [self._member({self.FOG: {"7": 1}})]}]})

        keep_on, meta, _wishes = MatchTNL.load_host_state(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1, 2}, self.FOG: {7}})
        self.assertEqual((meta["participants"], meta["tabs"]), (3, 3))

    def test_zero_slots_are_dropped(self):
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 0, "2": 0},
                                           self.FOG: {"5": 2}})]}]})

        keep_on, _meta, _wishes = MatchTNL.load_host_state(path)

        self.assertEqual(keep_on, {self.FOG: {5}}, "全部0のラウンドキーは残さない")

    def test_alternate_fog_key_is_ignored(self):
        """host_save 側にだけあるキー。既存は LOG_TO_TNL で通常Fogに寄せている"""
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.FOG_ALT: {"1": 1},
                                           self.CLASSIC: {"2": 1}})]}]})

        keep_on, _meta, _wishes = MatchTNL.load_host_state(path)

        self.assertEqual(keep_on, {self.CLASSIC: {2}})
        self.assertNotIn(1, keep_on.get(self.FOG, set()), "通常Fogへ畳まないこと")

    def test_a_broken_file_raises(self):
        """呼び出し側が握って前の値を保持する。ここでは握り潰さない"""
        path = Path(self._dir.name) / "host_state.sqlite3"
        path.write_bytes(b"not a sqlite file at all")

        with self.assertRaises(Exception):
            MatchTNL.load_host_state(str(path))

    def test_missing_tabs_gives_an_empty_set(self):
        for raw in ({"version": 5},
                    {"version": 5, "tabs": []},
                    {"version": 5, "tabs": [{"participants": []}]}):
            keep_on, meta, _wishes = MatchTNL.load_host_state(self._write(raw))

            self.assertEqual(keep_on, {}, raw)
            self.assertEqual(meta["participants"], 0, raw)

    def test_unknown_version_is_still_read(self):
        """あちらのバージョンが上がっても、読める形なら読む"""
        path = self._write({"version": 5, "tabs": [{
            "participants": [self._member({self.CLASSIC: {"1": 1}})]}]})
        con = sqlite3.connect(path)
        try:
            con.execute("update meta set value = '99' where key = 'version'")
            con.commit()
        finally:
            con.close()

        keep_on, _meta, _wishes = MatchTNL.load_host_state(path)

        self.assertEqual(keep_on, {self.CLASSIC: {1}})




class TestWishesPerWindow(unittest.TestCase):
    """窓ごとに「そのインスタンスにいる人」の希望だけで判定する。

    主催リストは全窓で共有しているので、そのままだと焼き芋の窓の参加者の
    希望でソロの窓まで判定してしまう（依頼者: 焼き芋1＋ソロ3で、ソロなのに続行）。
    """

    DT = "Double Trouble/ダブルトラブル"
    ME = "usr_0e01408a"
    PREFIX = "2026.09.19 10:00:00 Debug      -  "

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    WISHES = {
        "serim01": {DT: {1}},
        "さぶりむ": {DT: {2}},
        "offall": {},                  # リストはあるが全部 OFF
        "roundmate": {DT: {5}},        # 焼き芋の周回の参加者
        "elsewhere": {DT: {9}},        # 周回の参加者だが、この窓にはいない
    }

    def _monitor(self, me="serim01", others=(), itype=config.INSTANCE_PRIVATE,
                 wishes=None, known=True):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost="lost.mp3")
        union = {self.DT: {1, 5, 9}}
        monitor = LogMonitor.LogMonitor(
            cfg, union, lambda _m: None, window_idx=1,
            host_wishes=dict(self.WISHES if wishes is None else wishes))
        st = monitor.st
        st.instance_type = itype
        st.in_round = True
        st.round_type = "Double Trouble"
        st.local_player_name = me
        st.local_user_id = self.ME
        st.players = {self.ME}
        st.player_names = {self.ME: me}
        for n, name in enumerate(others):
            uid = f"usr_{n:04x}a"
            st.players.add(uid)
            st.player_names[uid] = name
        st.players_known = known
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), "Double Trouble", revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _line(self, monitor, body):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.PREFIX + body)

    # ── 誰の希望を使うか ──────────────────────
    def test_a_group_window_uses_those_present(self):
        monitor = self._monitor(others=("roundmate",), itype=config.INSTANCE_YAKIIMO)

        self.assertEqual(monitor._keep_on(), {self.DT: {1, 5}})

    def test_a_solo_window_is_not_pulled_by_the_lap(self):
        """依頼者の報告そのもの"""
        monitor = self._monitor()

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})
        started = self._killers(monitor, [5, 3])
        self.assertIn("do_skip", started, "焼き芋の参加者の希望で続行しない")

    def test_a_sub_account_uses_its_own_list(self):
        monitor = self._monitor(me="さぶりむ")

        self.assertEqual(monitor._keep_on(), {self.DT: {2}})
        self.assertNotIn("do_skip", self._killers(monitor, [2, 3]))

    def test_my_own_list_counts_without_my_join_line(self):
        """自分の入室行を取りこぼしても、アカウント名（User Authenticated）で引く"""
        monitor = self._monitor()
        monitor.st.player_names.pop(self.ME)

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    def test_a_lap_member_who_is_elsewhere_does_not_count(self):
        monitor = self._monitor(others=("roundmate",), itype=config.INSTANCE_YAKIIMO)

        self.assertNotIn(9, monitor._keep_on()[self.DT])

    def test_nobody_else_with_wishes_falls_to_my_own_list(self):
        monitor = self._monitor(others=("stranger",))

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    def test_no_list_at_all_stops(self):
        """誰もリストを持っていない。続行が無いのか分からないので止める"""
        monitor = self._monitor(me="nobody", others=("stranger",))

        started = self._killers(monitor, [5, 3])

        self.assertEqual(started, [])
        self.assertTrue(any("続行リストがありません" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_solo_list_with_everything_off_skips_everything(self):
        """依頼者: 続行なかったら全部死ぬだけ"""
        monitor = self._monitor(me="offall")

        self.assertEqual(monitor._keep_on(), {})
        started = self._killers(monitor, [1, 5])

        self.assertIn("do_skip", started)
        self.assertFalse(any("続行リストがありません" in m for m in monitor.logs),
                         "止めずに判定すること")

    def test_an_everything_off_player_is_not_unmatched(self):
        monitor = self._monitor(others=("offall",), itype=config.INSTANCE_YAKIIMO)

        self._killers(monitor, [1, 3])

        self.assertFalse(any("希望が見つからない入室者" in m for m in monitor.logs),
                         monitor.logs)

    def test_the_periodic_check_agrees(self):
        monitor = self._monitor(me="nobody")

        with patch.object(PlaySound, "play_sound") as played:
            monitor._check_group_list_state()

        self.assertTrue(monitor.st.list_lost_notified)
        played.assert_called_once_with("lost.mp3")

    def test_finding_wishes_again_resumes(self):
        monitor = self._monitor(me="nobody")
        with patch.object(PlaySound, "play_sound"):
            monitor._check_group_list_state()

        self._line(monitor, "[Behaviour] OnPlayerJoined roundmate (usr_bbbb)")
        monitor._check_group_list_state()

        self.assertFalse(monitor.st.list_lost_notified)
        self.assertTrue(any("続行リストが見つかりました" in m for m in monitor.logs),
                        monitor.logs)

    # ── 従来どおりのところ ─────────────────────
    def test_the_tnl_is_used_as_before(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self.assertIs(monitor._keep_on(), monitor.keepOn_set)

    def test_unknown_presence_keeps_the_shared_list(self):
        """誰がいるか分からないときは絞り込まない（A の安全側と同じ）"""
        monitor = self._monitor(known=False)

        self.assertIs(monitor._keep_on(), monitor.keepOn_set)

    def test_no_per_person_wishes_keeps_the_shared_list(self):
        monitor = self._monitor(wishes={})

        self.assertIs(monitor._keep_on(), monitor.keepOn_set)

    # ── Sabotage ───────────────────────────
    def test_sabotage_only_sees_those_present(self):
        monitor = self._monitor(others=("roundmate",), itype=config.INSTANCE_YAKIIMO)

        self.assertEqual(set(monitor._group_wishes()), {"serim01", "roundmate"})

    def test_sabotage_ignores_someone_who_is_elsewhere(self):
        star = GroupRound.SABOTAGE_STAR_KEY
        wishes = {"serim01": {}, "elsewhere": {star: {7}}}
        monitor = self._monitor(itype=config.INSTANCE_YAKIIMO, wishes=wishes)
        monitor.st.round_type = "Sabotage"
        monitor.st.terror_ids = [7]

        self.assertNotEqual(monitor._group_decision("Sabotage"), GroupRound.WANTED,
                            "その場にいない人の希望で続行しない")

    # ── 入退室 ────────────────────────────
    def test_joining_and_leaving_move_the_list(self):
        monitor = self._monitor()
        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

        self._line(monitor, "[Behaviour] OnPlayerJoined roundmate (usr_bbbb)")
        self.assertEqual(monitor._keep_on(), {self.DT: {1, 5}})

        self._line(monitor, "[Behaviour] OnPlayerLeft roundmate (usr_bbbb)")
        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    def test_a_new_instance_forgets_the_names(self):
        monitor = self._monitor(others=("roundmate",))

        self._line(monitor, "[Behaviour] Joining wrld_1:2~private(usr_x)")

        self.assertEqual(monitor.st.player_names, {})

    def test_an_unmatched_player_is_logged_once(self):
        monitor = self._monitor(others=("stranger",), itype=config.INSTANCE_YAKIIMO)

        self._killers(monitor, [1, 3])
        self._killers(monitor, [1, 3])

        hits = [m for m in monitor.logs if "希望が見つからない入室者" in m]
        self.assertEqual(len(hits), 1, monitor.logs)
        self.assertIn("stranger", hits[0])

    def test_restoring_brings_the_names_back(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        tmp.write("\n".join(self.PREFIX + line for line in (
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_x)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[Behaviour] OnPlayerJoined roundmate (usr_bbbb)",
        )) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        cfg = WindowConfig(log_path=Path(tmp.name))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1,
                                        host_wishes=dict(self.WISHES))
        monitor.logger = lambda _m: None

        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: f(*a)):
            monitor._detect_instance_from_log()

        self.assertEqual(monitor._present_names(), {"serim01", "roundmate"})
        self.assertEqual(monitor._keep_on(), {self.DT: {1, 5}})




class TestWaitingList(unittest.TestCase):
    """ToN ListTool の「参加者」判定に頼らない。

    ListTool は VRChat のログを読んで、その場にいる人を参加者・いない人を待機に
    振り分ける。複窓だとソロの窓のログを読んで、干し芋の全員を待機へ移す
    （実測: 参加者18・待機251 → 参加者0・待機269）。待機の人も続行リストを
    持ったまま残っているので、誰がいるかは窓ごとにこちらで見る。
    """

    DT = "Double Trouble/ダブルトラブル"
    ME = "usr_0e01408a"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()
        self.addCleanup(self._stats.stop)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.host = str(Path(self._dir.name) / "host_state.sqlite3")

    def _write(self, participants=(), waiting=()):
        def member(name, wanted):
            return {"vrc_name": name, "data": {self.DT: {str(t): 1 for t in wanted}}}
        write_host_state_like_json(self.host, {"version": 5, "tabs": [{
            "participants": [member(n, w) for n, w in participants],
            "waiting": [member(n, w) for n, w in waiting]}]})

    def _everyone_moved_to_waiting(self):
        """干し芋の2人と、別の周回の人。ListTool が全員を待機へ移した後"""
        self._write(participants=(), waiting=(("hoshi_a", {5}), ("hoshi_b", {6}),
                                              ("someone_else", {9})))
        return MatchTNL.load_host_state(self.host)

    def _monitor(self, keep, wishes, *, present=("hoshi_a", "hoshi_b"),
                 me="serim01", itype=config.INSTANCE_HOSHIIMO, known=True,
                 participants=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep, lambda _m: None, window_idx=1,
                                        host_wishes=wishes,
                                        host_participants=participants)
        st = monitor.st
        st.instance_type = itype
        st.in_round = True
        st.round_type = "Double Trouble"
        st.local_player_name = me
        st.local_user_id = self.ME
        st.players = {self.ME}
        st.player_names = {self.ME: me}
        for n, name in enumerate(present):
            uid = f"usr_{n:04x}c"
            st.players.add(uid)
            st.player_names[uid] = name
        st.players_known = known
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), "Double Trouble", revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 読み込み ─────────────────────────────
    def test_the_waiting_list_is_read_into_the_wishes(self):
        keep, meta, wishes = self._everyone_moved_to_waiting()

        self.assertEqual(wishes["hoshi_a"], {self.DT: {5}})
        self.assertEqual(meta["participants"], 0)
        self.assertEqual(meta["listed"], 3)
        self.assertEqual(keep, {}, "共有リストは参加者だけ（ここでは空）")

    # ── 窓の判定 ─────────────────────────────
    def test_a_group_window_keeps_judging_from_the_waiting_list(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes)

        self.assertEqual(monitor._list_block_reason(), "")
        self.assertEqual(monitor._keep_on(), {self.DT: {5, 6}})
        started = self._killers(monitor, [5, 1])
        self.assertNotIn("do_skip", started, "hoshi_a の希望で続行")
        self.assertTrue(monitor.st.is_continue_round)

    def test_someone_waiting_elsewhere_is_not_used(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes)

        self.assertNotIn(9, monitor._keep_on()[self.DT])

    def test_a_solo_window_still_uses_its_own_list(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        wishes["serim01"] = {self.DT: {1}}
        monitor = self._monitor(keep, wishes, present=(),
                                itype=config.INSTANCE_PRIVATE)

        self.assertEqual(monitor._keep_on(), {self.DT: {1}})

    # ── 誰がいるか分からない窓 ────────────────────
    def test_an_unknown_window_with_an_empty_shared_list_stops(self):
        """共有リストが空のまま判定すると全ラウンド自爆する"""
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes, known=False)

        started = self._killers(monitor, [5, 1])

        self.assertEqual(started, [])
        self.assertTrue(any("続行リストがありません" in m for m in monitor.logs),
                        monitor.logs)

    def test_an_unknown_window_with_a_shared_list_still_judges(self):
        keep = {self.DT: {5}}
        monitor = self._monitor(keep, {"hoshi_a": {self.DT: {5}}}, known=False)

        self.assertEqual(monitor._list_block_reason(), "")

    def test_the_periodic_check_agrees(self):
        keep, _meta, wishes = self._everyone_moved_to_waiting()
        monitor = self._monitor(keep, wishes, known=False)

        with patch.object(PlaySound, "play_sound"):
            monitor._check_group_list_state()

        self.assertTrue(monitor.st.list_lost_notified)

    def test_sabotage_in_an_unknown_window_ignores_the_waiting_list(self):
        """誰がいるか分からないときは、参加者（＋自分）の希望だけ"""
        wishes = {"hoshi_a": {"x": {1}}, "waiting_person": {"x": {2}}}
        monitor = self._monitor({}, wishes, known=False,
                                participants={"hoshi_a"})

        self.assertEqual(set(monitor._group_wishes()), {"hoshi_a"})

    def test_the_participant_names_include_the_host(self):
        user = str(Path(self._dir.name) / "user_save.json")
        Path(user).write_text(json.dumps({"last_active": "serim01", "accounts": {
            "serim01": {"data": {self.DT: {"1": 1}}}}}), encoding="utf-8")
        self._write(participants=(("hoshi_a", {5}),), waiting=(("w", {9}),))

        _keep, meta, _wishes = MatchTNL.load_host_state(self.host, user)

        self.assertEqual(meta["participant_names"], {"hoshi_a", "serim01"})




class TestHostSaveAllAccounts(unittest.TestCase):
    """user_save.json の全アカウントを、名前ごとの希望として読む"""

    PAGES = "8 Pages/8ページ"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.host = str(Path(self._dir.name) / "host_state.sqlite3")
        self.user = str(Path(self._dir.name) / "user_save.json")
        write_host_state_like_json(self.host, {"version": 5, "tabs": [{"participants": [
            {"vrc_name": "roundmate", "data": {self.PAGES: {"70": 1}}}]}]})
        Path(self.user).write_text(json.dumps({
            "last_active": "serim01",
            "accounts": {
                "serim01": {"data": {self.PAGES: {"10": 1}}},
                "ruri9752 fff9": {"data": {self.PAGES: {"20": 1}}},
                "さぶりむ": {"data": {self.PAGES: {"30": 0}}},   # 何もONでない
                "壊れ": {"data": "辞書ではない"},
            }}, ensure_ascii=False), encoding="utf-8")

    def _load(self):
        return MatchTNL.load_host_state(self.host, self.user)

    def test_every_account_is_in_the_wishes(self):
        _keep, _meta, wishes = self._load()

        self.assertEqual(wishes["serim01"], {self.PAGES: {10}})
        self.assertEqual(wishes["ruri9752 fff9"], {self.PAGES: {20}})
        self.assertEqual(wishes["roundmate"], {self.PAGES: {70}})

    def test_an_account_with_everything_off_is_kept_empty(self):
        """さぶりむ（全部 OFF）はリストを持っている。壊れたものは持っていない"""
        _keep, _meta, wishes = self._load()

        self.assertEqual(wishes["さぶりむ"], {})
        self.assertNotIn("壊れ", wishes)

    def test_a_participant_with_everything_off_is_kept_empty(self):
        """周回の参加者も同じ扱い"""
        write_host_state_like_json(self.host, {"version": 5, "tabs": [{"participants": [
            {"vrc_name": "roundmate", "data": {self.PAGES: {"70": 0}}}]}]})

        _keep, meta, wishes = self._load()

        self.assertEqual(wishes["roundmate"], {})
        self.assertEqual(meta["participants"], 1)

    def test_the_shared_list_still_adds_only_the_active_account(self):
        """GUI の件数表示は従来どおり"""
        keep, meta, _wishes = self._load()

        self.assertEqual(keep, {self.PAGES: {10, 70}})
        self.assertEqual(meta["host_self"], "serim01")
        self.assertEqual(meta["participants"], 1)




class TestHostOwnList(unittest.TestCase):
    """主催者自身の続行リスト（host_save の participants には入らない）"""

    CLASSIC = "Classic/クラシック"
    PAGES = "8 Pages/8ページ"
    FOG_ALT = "Fog (Alternate)/霧 (Alternate)"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.host = str(Path(self._dir.name) / "host_state.sqlite3")
        self.user = str(Path(self._dir.name) / "user_save.json")

    def tearDown(self):
        self._dir.cleanup()

    def _write_host(self, participants=1, name="ひと1"):
        members = [{"vrc_name": f"{name}{n}", "data": {self.CLASSIC: {str(n + 5): 1}}}
                   for n in range(participants)]
        write_host_state_like_json(self.host, {"version": 5,
                                               "tabs": [{"participants": members}]})

    def _write_user(self, raw):
        Path(self.user).write_text(json.dumps(raw), encoding="utf-8")

    def _account(self, data, name="serim01"):
        return {"version": 200, "last_active": name,
                "accounts": {name: {"list_name": f"{name}のリスト", "data": data}}}

    def _load(self, with_user=True):
        return MatchTNL.load_host_state(self.host, self.user if with_user else None)

    # ── 足される ────────────────────────────
    def test_the_host_wishes_are_merged(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1, "135": 1}}))

        keep_on, _meta, _wishes = self._load()

        self.assertEqual(keep_on[self.PAGES], {70, 135})
        self.assertEqual(keep_on[self.CLASSIC], {5}, "participants も残ること")

    def test_the_host_appears_in_the_wishes(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        _keep_on, _meta, wishes = self._load()

        self.assertEqual(wishes["serim01"], {self.PAGES: {70}})

    def test_without_the_path_nothing_changes(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        keep_on, meta, wishes = self._load(with_user=False)

        self.assertNotIn(self.PAGES, keep_on)
        self.assertNotIn("serim01", wishes)
        self.assertIsNone(meta["host_self"])

    def test_the_name_is_reported(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 1}}, name="さぶりむ"))

        _keep_on, meta, _wishes = self._load()

        self.assertEqual(meta["host_self"], "さぶりむ")

    # ── 人数に数えない（ここが事故のもと） ──────
    def test_the_host_is_not_counted(self):
        self._write_host(3)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        _keep_on, meta, _wishes = self._load()

        self.assertEqual(meta["participants"], 3, "自分を足して4にしないこと")

    def test_an_empty_lap_stays_empty(self):
        """0人のままでないと .tnl へのフォールバックと「手を止める」が効かない"""
        self._write_host(0)
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        keep_on, meta, _wishes = self._load()

        self.assertEqual(meta["participants"], 0)
        self.assertEqual(keep_on[self.PAGES], {70}, "リスト自体は読めている")

    # ── 諦める側 ────────────────────────────
    def test_a_missing_file_is_ignored(self):
        self._write_host(1)

        keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])
        self.assertEqual(keep_on, {self.CLASSIC: {5}})

    def test_broken_json_is_ignored(self):
        self._write_host(1)
        Path(self.user).write_text("{ not json", encoding="utf-8")

        _keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])

    def test_an_unknown_last_active_is_ignored(self):
        self._write_host(1)
        raw = self._account({self.PAGES: {"70": 1}})
        raw["last_active"] = "だれか"

        self._write_user(raw)
        _keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])

    def test_a_non_dict_data_is_ignored(self):
        self._write_host(1)
        self._write_user(self._account("これは辞書ではない"))

        _keep_on, meta, _wishes = self._load()

        self.assertIsNone(meta["host_self"])

    def test_a_broken_host_save_still_raises(self):
        """host_save 側のデコード失敗は従来どおり例外（呼び出し側が握る）"""
        Path(self.host).write_bytes(b"half written garbage")
        self._write_user(self._account({self.PAGES: {"70": 1}}))

        with self.assertRaises(Exception):
            self._load()

    # ── participants と同じ扱い ────────────────
    def test_zero_slots_are_dropped(self):
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 0, "135": 0},
                                        self.CLASSIC: {"9": 1}}))

        keep_on, _meta, wishes = self._load()

        self.assertNotIn(self.PAGES, keep_on)
        self.assertEqual(wishes["serim01"], {self.CLASSIC: {9}})

    def test_the_alternate_keys_are_ignored(self):
        self._write_host(1)
        self._write_user(self._account({self.FOG_ALT: {"1": 1},
                                        self.PAGES: {"70": 1}}))

        keep_on, _meta, wishes = self._load()

        self.assertNotIn(self.FOG_ALT, keep_on)
        self.assertEqual(wishes["serim01"], {self.PAGES: {70}})

    def test_a_host_with_everything_off_is_kept_empty(self):
        """全部 OFF は「続行したいものが無い」。リストが無いのとは違う"""
        self._write_host(1)
        self._write_user(self._account({self.PAGES: {"70": 0}}))

        _keep_on, meta, wishes = self._load()

        self.assertEqual(meta["host_self"], "serim01", "読めてはいる")
        self.assertEqual(wishes["serim01"], {}, "空でも {} で残すこと")

    def test_a_duplicate_name_is_merged(self):
        """participants に同名がいても OR されるだけ"""
        write_host_state_like_json(self.host, {"version": 5, "tabs": [{"participants": [
            {"vrc_name": "serim01",
             "data": {self.CLASSIC: {"5": 1}}}]}]})
        self._write_user(self._account({self.CLASSIC: {"9": 1}}))

        keep_on, _meta, wishes = self._load()

        self.assertEqual(keep_on[self.CLASSIC], {5, 9})
        self.assertEqual(wishes["serim01"][self.CLASSIC], {5, 9})

    def test_the_real_file_is_readable(self):
        """現物で読めること（形が変わっていたら気づけるように）"""
        if not Path(config.USER_SAVE_PATH).exists():
            self.skipTest("user_save.json が無い")
        raw = json.loads(Path(config.USER_SAVE_PATH).read_text(encoding="utf-8"))

        name = raw.get("last_active")
        self.assertIsInstance(name, str)
        self.assertIn(name, raw.get("accounts", {}))
        self.assertIsInstance(raw["accounts"][name].get("data"), dict)




class TestOldListToolIsNotRead(unittest.TestCase):
    """2.13 より前の ToN ListTool（host_save.json.gz）は読まない。

    ディスクに何日も前のものが残っていることがあり（依頼者の PC では新 9/27・
    旧 9/21）、SQLite が一瞬無いだけで古い参加者と古い続行リストで判定して
    しまう。将来また旧ファイルへ戻す分岐を足さないための見張り
    """

    FILES = ("config.py", "mainGUI.py", "MatchTNL.py", "LogMonitor.py",
             "ActionExecutor.py", "SharedState.py")

    def test_the_old_path_and_reader_are_gone(self):
        here = Path(MatchTNL.__file__).parent
        for name in self.FILES:
            src = (here / name).read_text(encoding="utf-8")
            self.assertNotIn("HOST_SAVE_PATH", src, name)
            self.assertNotIn("load_host_save", src, name)
            self.assertNotIn("host_save.json.gz\")", src, name)

    def test_the_module_no_longer_offers_the_reader(self):
        self.assertFalse(hasattr(MatchTNL, "load_host_save"))
        self.assertFalse(hasattr(config, "HOST_SAVE_PATH"))

    def test_the_shared_parts_are_still_there(self):
        """新旧で共用していた部品は残す（主催者自身のリストが使う）"""
        for name in ("_fold_wishes", "_wish_ids", "_load_host_own_list",
                     "HOST_SAVE_IGNORED_KEYS", "load_host_state"):
            self.assertTrue(hasattr(MatchTNL, name), name)
        self.assertTrue(hasattr(config, "USER_SAVE_PATH"))

    def test_the_missing_sqlite_file_is_reported_as_lost(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _read_host_source(self) -> tuple:"):]
        body = body[:body.index("\n    def ", 10)]

        self.assertIn('return "lost", f"{name} がありません"', body)




class TestHostListGrace(unittest.TestCase):
    """主催リストが一瞬取れなくても、すぐには tnl へ切り替えない。

    プロセスが見えない・host_save を stat できない・参加者0人は、どれも一瞬
    だけ起きうる。1回で切り替えると他の人がいる窓でだけ警告が鳴り、
    「複窓で頻繁に落ちる」ように見えていた。
    """

    CLASSIC = "Classic/クラシック"
    GRACE = 10.0

    def setUp(self):
        grace = patch.object(config, "HOST_LIST_LOSS_GRACE_SEC", self.GRACE)
        grace.start()
        self.addCleanup(grace.stop)
        SharedState.set_list_source(None)
        self.addCleanup(SharedState.set_list_source, None)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = str(Path(self._dir.name) / "host_save.json.gz")
        self.db = str(Path(self._dir.name) / "host_state.sqlite3")      # 既定では作らない
        self.user_save = str(Path(self._dir.name) / "user_save.json")
        self.tnl = Path(self._dir.name) / "list.tnl"
        self.tnl.write_text(json.dumps(
            {"list_name": "L", "creator": "", "created_at": "",
             "data": {self.CLASSIC: {"1": 1}}}), encoding="utf-8")
        self.now = 1000.0
        self.app = self._app()

    def _app(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.host_wishes = {}
        app.host_tabs = {"version": 0, "tabs": {}}
        app._host_save_stamp = None
        app._host_save_warned = False
        app._host_loss_since = None
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app.v_tnl = TestHostListSource.FakeVar(str(self.tnl))
        for name in ("_apply_keep_on", "_apply_host_wishes", "_apply_host_tabs", "_host_list_lost",
                     "_warn_host_save_once", "_fall_back_to_tnl", "_load_tnl",
                     "_read_host_source", "_apply_host_source"):
            method = getattr(mainGUI.App, name)
            setattr(app, name, (lambda m: lambda *a, **kw: m(app, *a, **kw))(method))
        return app

    def _write(self, participants, wanted=7):
        members = [{"vrc_name": f"ひと{n}", "data": {self.CLASSIC: {str(wanted): 1}}}
                   for n in range(participants)]
        write_host_state_like_json(self.db, {"version": 5,
                                             "tabs": [{"participants": members}]})

    def _tick(self, after=0.0, running=True, stat_fails=False):
        """3秒ごとの確認を1回回す。after 秒たってから"""
        self.now += after
        stat = patch.object(mainGUI.os, "stat", side_effect=OSError("swapping")) \
            if stat_fails else patch.object(mainGUI.os, "stat", wraps=os.stat)
        with patch.object(config, "HOST_STATE_PATH", self.db), \
             patch.object(config, "USER_SAVE_PATH", self.user_save), \
             patch.object(ProcessCheck, "is_process_running", return_value=running), \
             patch.object(mainGUI.time, "monotonic", side_effect=lambda: self.now), \
             patch.object(mainGUI, "save_settings"), \
             patch.object(mainGUI, "load_settings", return_value={}), \
             stat:
            mainGUI.App._refresh_host_source(self.app)

    def _on_host_list(self):
        self._write(2)
        self._tick()
        self.assertEqual(SharedState.get_list_source(), "host", "前提")
        self.host_list = dict(self.app.keepOn_set)

    def _grace_logs(self):
        return [m for m in self.app.logs if "猶予中" in m]

    # ── 一瞬では切り替えない ─────────────────────
    def test_a_briefly_missing_sqlite_file_is_forgiven(self):
        """ListTool が作り直している最中などで一瞬無いだけなら、すぐには
        切り替えない。古い host_save.json.gz にも逃げない"""
        write_old_host_save(self.path, 3)
        self._on_host_list()
        Path(self.db).unlink()

        self._tick(after=self.GRACE / 2)

        self.assertEqual(SharedState.get_list_source(), "host", "猶予の内")
        self.assertEqual(self.app.keepOn_set, self.host_list, "古い JSON を読まない")

    def test_a_sqlite_file_missing_for_long_goes_to_the_tnl(self):
        write_old_host_save(self.path, 3)
        self._on_host_list()
        Path(self.db).unlink()

        self._tick(after=0.0)
        self._tick(after=self.GRACE + 1)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(self.app.keepOn_set, {self.CLASSIC: {1}}, ".tnl の中身")

    def test_a_moment_without_the_file_keeps_the_host_list(self):
        self._on_host_list()

        self._tick(3, stat_fails=True)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(self.app.keepOn_set, self.host_list, "前の主催リストのまま")

    def test_a_moment_without_participants_keeps_the_host_list(self):
        self._on_host_list()
        self._write(0)

        self._tick(3)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(self.app.keepOn_set, self.host_list)

    def test_a_moment_without_the_process_keeps_the_host_list(self):
        self._on_host_list()

        self._tick(3, running=False)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(self.app.keepOn_set, self.host_list)

    # ── 続いたら切り替える ───────────────────────
    def test_a_lasting_loss_switches_to_the_tnl(self):
        self._on_host_list()

        self._tick(3, running=False)
        self._tick(3, running=False)
        self.assertEqual(SharedState.get_list_source(), "host", "まだ6秒")
        self._tick(self.GRACE, running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(self.app.keepOn_set, {self.CLASSIC: {1}})

    def test_a_good_read_resets_the_grace(self):
        self._on_host_list()
        self._tick(3, running=False)
        self._tick(self.GRACE - 4, running=True)      # 途中で読めた

        self._tick(3, running=False)                  # また一瞬
        self._tick(self.GRACE - 4, running=False)

        self.assertEqual(SharedState.get_list_source(), "host",
                         "読めた時点で数え直していること")

    def test_the_grace_is_logged_once(self):
        self._on_host_list()

        for _ in range(3):
            self._tick(2, running=False)

        self.assertEqual(len(self._grace_logs()), 1, self.app.logs)

    def test_a_new_loss_logs_again(self):
        self._on_host_list()
        self._tick(3, running=False)
        self._tick(3)                                 # 戻った
        self._tick(3, stat_fails=True)                # また取れない

        self.assertEqual(len(self._grace_logs()), 2, self.app.logs)

    # ── 戻るのはすぐ／守るものが無ければすぐ ─────────────
    def test_returning_from_the_tnl_is_immediate(self):
        self._tick(running=False)
        self.assertEqual(SharedState.get_list_source(), "tnl", "前提")
        self._write(2)

        self._tick(0.1)

        self.assertEqual(SharedState.get_list_source(), "host")

    def test_no_host_list_yet_goes_to_the_tnl_at_once(self):
        """まだ主催リストを使っていなければ、守るものが無い"""
        self._tick(running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(self._grace_logs(), [])




class TestHostStateSqlite(unittest.TestCase):
    """ToN ListTool の新しい保存形式（host_state.sqlite3）を読む"""

    CLASSIC = "Classic/クラシック"
    FOG = "Fog (Alternate)/霧 (Alternate)"

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db = Path(self._dir.name) / "host_state.sqlite3"

    def _load(self, tabs, user_save=None):
        write_host_state(self.db, tabs)
        return MatchTNL.load_host_state(str(self.db), user_save)

    # ── 参加者と待機 ──────────────────────────
    def test_only_participants_are_folded_into_the_shared_list(self):
        keep_on, meta, wishes = self._load([[
            (0, "さんかしゃ", {self.CLASSIC: {5}}),
            (1, "たいき", {self.CLASSIC: {7}}),
        ]])

        self.assertEqual(keep_on, {self.CLASSIC: {5}}, "待機は畳まない")
        self.assertEqual(wishes["たいき"], {self.CLASSIC: {7}}, "待機も名前では引ける")
        self.assertEqual(meta["participants"], 1)
        self.assertEqual(meta["participant_names"], {"さんかしゃ"})
        self.assertEqual(meta["listed"], 2, "参加者＋待機のうちリストを持つ人")
        self.assertEqual(meta["tabs"], 1)
        self.assertIsNone(meta["host_self"])

    def test_the_same_name_in_two_tabs_is_folded(self):
        keep_on, meta, _w = self._load([
            [(0, "ふたり", {self.CLASSIC: {1}})],
            [(0, "ふたり", {self.CLASSIC: {2}})],
        ])

        self.assertEqual(keep_on, {self.CLASSIC: {1, 2}})
        self.assertEqual(meta["tabs"], 2)
        self.assertEqual(meta["participants"], 2)

    # ── ビット列 ────────────────────────────
    def test_the_bits_are_read_from_the_lowest_bit(self):
        keep_on, _meta, _w = self._load([[(0, "ひと", {self.CLASSIC: {0, 1, 7, 8, 9, 134, 319}})]])

        self.assertEqual(keep_on[self.CLASSIC], {0, 1, 7, 8, 9, 134, 319})

    def test_a_single_byte_is_not_reversed(self):
        """下位ビットから: 0x01 は ID 0、0x80 は ID 7"""
        for raw, expected in ((b"\x01", {0}), (b"\x80", {7}), (b"\x00\x02", {9})):
            keep_on, _meta, _w = self._load([[(0, "ひと", {self.CLASSIC: raw})]])
            self.assertEqual(keep_on.get(self.CLASSIC, set()), expected, raw)

    def test_empty_or_odd_length_bits(self):
        for raw, expected in ((b"", set()), (bytes(40), set()),
                              (b"\x00" * 60 + b"\x01", {480})):
            keep_on, meta, _w = self._load([[(0, "ひと", {self.CLASSIC: raw})]])
            self.assertEqual(keep_on.get(self.CLASSIC, set()), expected, raw)
            self.assertEqual(meta["listed"], 1, "行があればリストは持っている")

    # ── 古い JSON と同じ結果になること ──────────────
    def test_it_reads_the_same_thing_the_json_reader_did(self):
        """以前は JSON 版と結果を突き合わせていた。JSON 版を消したので、同じ入力に
        対して JSON 版が返していた値を直接書いて確かめる"""
        people = [(0, "さんかしゃA", {self.CLASSIC: {1, 300}, self.FOG: {9}}),
                  (0, "さんかしゃB", {self.CLASSIC: {2}}),
                  (1, "たいきC", {self.FOG: {4, 5}})]

        keep_on, meta, wishes = self._load([people])

        self.assertEqual(keep_on, {self.CLASSIC: {1, 2, 300}},
                         "参加者だけを畳む。Fog (Alternate) は無視する")
        self.assertEqual(wishes, {"さんかしゃA": {self.CLASSIC: {1, 300}},
                                  "さんかしゃB": {self.CLASSIC: {2}},
                                  "たいきC": {}},
                         "待機も名前では引ける。無視するキーは名前ごとにも入らない")
        self.assertEqual(meta["participants"], 2)
        self.assertEqual(meta["participant_names"], {"さんかしゃA", "さんかしゃB"})
        self.assertEqual(meta["listed"], 3)
        self.assertEqual(meta["tabs"], 1)
        self.assertIsNone(meta["host_self"])
        self.assertEqual(set(meta["tabs_data"]), {0}, "1タブぶん")
        self.assertEqual(meta["tabs_data"][0]["participants"],
                         {"さんかしゃA", "さんかしゃB"})
        self.assertEqual(meta["tabs_data"][0]["waiting"], {"たいきC"})

    # ── 主催者自身 ──────────────────────────
    def test_the_host_own_list_is_added(self):
        user_save = Path(self._dir.name) / "user_save.json"
        user_save.write_text(json.dumps({
            "last_active": "ぬし",
            "accounts": {"ぬし": {"data": {self.CLASSIC: {"42": 1}}}}}),
            encoding="utf-8")

        keep_on, meta, wishes = self._load(
            [[(0, "さんかしゃ", {self.CLASSIC: {5}})]], user_save=str(user_save))

        self.assertEqual(keep_on[self.CLASSIC], {5, 42})
        self.assertEqual(meta["host_self"], "ぬし")
        self.assertEqual(meta["participants"], 1, "主催者は数えない")
        self.assertIn("ぬし", meta["participant_names"])
        self.assertEqual(wishes["ぬし"], {self.CLASSIC: {42}})

    # ── 読み方 ─────────────────────────────
    def test_a_wal_file_is_copied_too(self):
        """ListTool が開いている間、新しい行は -wal にだけある（本体はまだ古い）"""
        write_host_state(self.db, [[(0, "ひと", {self.CLASSIC: {3}})]])
        con = sqlite3.connect(str(self.db))
        self.addCleanup(con.close)
        con.execute("pragma journal_mode=wal")
        con.execute("insert into participants(id, tab_index, section, position,"
                    " vrc_name, is_visible) values (99, 0, 0, 1, 'あとから', 1)")
        con.execute("insert into wishes values (99, ?, ?)",
                    (self.CLASSIC, wish_bits({11})))
        con.commit()
        self.assertTrue(Path(str(self.db) + "-wal").exists(), "前提: -wal がある")

        keep_on, meta, _w = MatchTNL.load_host_state(str(self.db))

        self.assertEqual(keep_on[self.CLASSIC], {3, 11}, "-wal のぶんも読む")
        self.assertEqual(meta["participants"], 2)

    def test_the_list_tool_folder_is_not_written_to(self):
        """読むだけ。ListTool のフォルダに何も足さない・触らない"""
        write_host_state(self.db, [[(0, "ひと", {self.CLASSIC: {3}})]])
        before = {p.name: p.stat().st_mtime_ns for p in Path(self._dir.name).iterdir()}

        MatchTNL.load_host_state(str(self.db))

        after = {p.name: p.stat().st_mtime_ns for p in Path(self._dir.name).iterdir()}
        self.assertEqual(after, before)

    def test_a_broken_file_raises(self):
        self.db.write_bytes(b"not a database at all")

        with self.assertRaises(Exception):
            MatchTNL.load_host_state(str(self.db))

    def test_a_missing_file_raises(self):
        with self.assertRaises(Exception):
            MatchTNL.load_host_state(str(self.db) + ".nope")




class TestWindowTabMatching(unittest.TestCase):
    """ToN ListTool の複窓対応: 窓ごとに、対応するタブの続行リストだけを使う。

    窓とタブの対応はどこにも記録されていないので、その窓にいる人の名前と、
    タブの参加者の名前の重なりで決める。
    """

    CLASSIC = "Classic/クラシック"
    FOG = "Fog/霧"

    def _tabs(self, *tabs):
        """(参加者名の集合, keepOn, 待機名の集合) の並びからタブの内訳を作る"""
        data = {}
        for index, tab in enumerate(tabs):
            names, keep_on, waiting = tab
            data[index] = {
                "participants": set(names),
                "waiting": set(waiting or ()),
                "keepOn": {k: set(v) for k, v in (keep_on or {}).items()},
                "wishes": {name: {k: set(v) for k, v in (keep_on or {}).items()}
                           for name in set(names) | set(waiting or ())},
            }
        return {"version": 1, "tabs": data}

    def _monitor(self, host_tabs, present=(), me="ぬし", known=True):
        monitor = LogMonitor.LogMonitor(
            WindowConfig(), {"共有": {999}}, lambda _m: None, window_idx=1,
            host_wishes={name: {} for name in present},
            host_tabs=host_tabs)
        monitor.st.local_player_name = me
        monitor.st.players_known = known
        monitor.st.players = set(range(len(present)))
        monitor.st.player_names = dict(zip(monitor.st.players, present))
        monitor.logs = []
        monitor.logger = monitor.logs.append
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        return monitor

    # ── 対応づけ ────────────────────────────
    def test_each_window_uses_its_own_tab(self):
        tabs = self._tabs((["あ", "い"], {self.CLASSIC: {1}}, []),
                          (["う", "え"], {self.CLASSIC: {2}}, []))

        first = self._monitor(tabs, present=["あ", "い"])
        second = self._monitor(tabs, present=["う", "え"])

        self.assertEqual(first._keep_on(), {self.CLASSIC: {1}})
        self.assertEqual(second._keep_on(), {self.CLASSIC: {2}})
        self.assertNotIn(2, first._keep_on().get(self.CLASSIC, set()),
                         "別のタブの希望は混ぜない")

    def test_the_tab_with_the_most_names_wins(self):
        tabs = self._tabs((["あ"], {self.CLASSIC: {1}}, []),
                          (["あ", "い", "う"], {self.CLASSIC: {2}}, []))

        monitor = self._monitor(tabs, present=["あ", "い", "う"])

        self.assertEqual(monitor._keep_on(), {self.CLASSIC: {2}})
        self.assertTrue(any("タブ2 に対応" in m for m in monitor.logs), monitor.logs)

    def test_no_overlap_falls_back_to_the_shared_list(self):
        tabs = self._tabs((["あ"], {self.CLASSIC: {1}}, []))

        monitor = self._monitor(tabs, present=["か", "き"])

        self.assertEqual(monitor._keep_on(), {}, "共有リスト＋名前の絞り込みのまま")
        self.assertTrue(any("見つかりません" in m for m in monitor.logs), monitor.logs)

    def test_a_tie_is_not_matched(self):
        tabs = self._tabs((["あ", "か"], {self.CLASSIC: {1}}, []),
                          (["い", "き"], {self.CLASSIC: {2}}, []))

        monitor = self._monitor(tabs, present=["あ", "い"])

        self.assertIsNone(MatchTNL.tab_for_window(tabs["tabs"], {"あ", "い"}))
        self.assertEqual(monitor._keep_on(), {})

    def test_unknown_players_are_not_matched(self):
        tabs = self._tabs((["あ", "い"], {self.CLASSIC: {1}}, []))

        monitor = self._monitor(tabs, present=["あ", "い"], known=False)

        self.assertEqual(monitor._keep_on(), {"共有": {999}}, "共有リストのまま")

    def test_a_solo_window_is_not_matched(self):
        """自分しかいない窓は、参加者0人のタブと区別できない"""
        tabs = self._tabs((["ぬし"], {self.CLASSIC: {1}}, []))

        monitor = self._monitor(tabs, present=["ぬし"], me="ぬし")

        self.assertEqual(monitor._keep_on(), {})

    def test_waiting_names_do_not_match(self):
        """待機はその場にいない人。待機で対応づけない"""
        tabs = self._tabs(([], {self.CLASSIC: {1}}, ["あ", "い"]),
                          (["う"], {self.CLASSIC: {2}}, []))

        self.assertIsNone(MatchTNL.tab_for_window(tabs["tabs"], {"あ", "い"}))

    def test_the_wishes_come_from_the_tab(self):
        tabs = self._tabs((["あ"], {self.FOG: {7}}, ["たいき"]),
                          (["う"], {self.FOG: {8}}, []))

        monitor = self._monitor(tabs, present=["あ"])

        self.assertEqual(monitor._effective_wishes(), {"あ": {self.FOG: {7}}})

    # ── 計算のやり直し ───────────────────────
    def test_it_recomputes_when_the_players_change(self):
        tabs = self._tabs((["あ", "い"], {self.CLASSIC: {1}}, []),
                          (["う", "え"], {self.CLASSIC: {2}}, []))
        monitor = self._monitor(tabs, present=["あ", "い"])
        self.assertEqual(monitor._keep_on(), {self.CLASSIC: {1}})

        monitor.st.players = {0, 1}
        monitor.st.player_names = {0: "う", 1: "え"}

        self.assertEqual(monitor._keep_on(), {self.CLASSIC: {2}}, "在室者が変われば選び直す")

    def test_it_does_not_recompute_while_nothing_changes(self):
        tabs = self._tabs((["あ", "い"], {self.CLASSIC: {1}}, []))
        monitor = self._monitor(tabs, present=["あ", "い"])

        with patch.object(MatchTNL, "tab_for_window",
                          side_effect=MatchTNL.tab_for_window) as match:
            for _ in range(5):
                monitor._keep_on()

        self.assertEqual(match.call_count, 1, "在室者も主催リストも変わっていない")

    def test_a_new_host_list_recomputes(self):
        tabs = self._tabs((["あ", "い"], {self.CLASSIC: {1}}, []))
        monitor = self._monitor(tabs, present=["あ", "い"])
        monitor._keep_on()

        tabs["tabs"][0]["keepOn"] = {self.CLASSIC: {5}}
        tabs["version"] += 1

        with patch.object(MatchTNL, "tab_for_window",
                          side_effect=MatchTNL.tab_for_window) as match:
            self.assertEqual(monitor._keep_on(), {self.CLASSIC: {5}})

        match.assert_called_once()

    # ── 複窓オフ ────────────────────────────
    def test_a_single_tab_still_works(self):
        tabs = self._tabs((["あ", "い"], {self.CLASSIC: {1}}, ["たいき"]))

        monitor = self._monitor(tabs, present=["あ", "い"])

        self.assertEqual(monitor._keep_on(), {self.CLASSIC: {1}})

    def test_without_tabs_nothing_changes(self):
        monitor = self._monitor({"version": 0, "tabs": {}}, present=["あ"])

        self.assertIsNone(monitor._window_tab())
        self.assertEqual(monitor._keep_on(), {}, "名前で絞った共有リスト")




class TestRealHostStateTabs(unittest.TestCase):
    """依頼者の実ファイルで、タブごとの内訳が読めること（無ければスキップ）"""

    def test_the_real_file_splits_into_tabs(self):
        path = Path(config.HOST_STATE_PATH)
        if not path.exists():
            self.skipTest("host_state.sqlite3 が無い")

        _keep, meta, _wishes = MatchTNL.load_host_state(str(path))

        tabs = meta["tabs_data"]
        self.assertTrue(tabs, "タブの内訳が空")
        counts = {tab: len(data["participants"]) for tab, data in sorted(tabs.items())}
        self.assertEqual(sum(counts.values()), meta["participants"],
                         "タブごとの人数の合計が全体と合う")
        # 参加者がいるタブは、その名前で引ける
        for tab, data in tabs.items():
            if data["participants"]:
                self.assertEqual(
                    MatchTNL.tab_for_window(tabs, data["participants"]), tab)




class TestHostListSource(unittest.TestCase):
    """続行リストの供給元を状況から決める（チェックボックスは無い）

    ToN ListTool が動いていて参加者がいれば主催リスト、それ以外は .tnl。
    ツールを閉じてもファイルは残るので、鮮度はプロセスの生死で見る。
    """

    CLASSIC = "Classic/クラシック"

    def setUp(self):
        # この組は「どの条件で切り替わるか」を見る。猶予は TestHostListGrace で見る
        grace = patch.object(config, "HOST_LIST_LOSS_GRACE_SEC", 0.0)
        grace.start()
        self.addCleanup(grace.stop)
        SharedState.set_list_source(None)
        self._dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self._dir.name) / "host_save.json.gz")
        self.db = str(Path(self._dir.name) / "host_state.sqlite3")      # 既定では作らない
        self.user_save = str(Path(self._dir.name) / "user_save.json")   # 作らない
        self.tnl = Path(self._dir.name) / "list.tnl"
        self.tnl.write_text(json.dumps(
            {"list_name": "L", "creator": "", "created_at": "",
             "data": {self.CLASSIC: {"1": 1}}}), encoding="utf-8")

    def tearDown(self):
        SharedState.set_list_source(None)
        self._dir.cleanup()

    class FakeVar:
        def __init__(self, value):
            self._v = value

        def get(self):
            return self._v

    def _app(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.host_wishes = {}
        app.host_tabs = {"version": 0, "tabs": {}}
        app._host_save_stamp = None
        app._host_save_warned = False
        app._host_loss_since = None
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app.v_tnl = TestHostListSource.FakeVar(str(self.tnl))
        for name in ("_apply_keep_on", "_apply_host_wishes", "_apply_host_tabs", "_host_list_lost",
                     "_warn_host_save_once", "_fall_back_to_tnl", "_load_tnl",
                     "_read_host_source", "_apply_host_source"):
            setattr(app, name, self._bind(app, name))
        return app

    @staticmethod
    def _bind(app, name):
        method = getattr(mainGUI.App, name)
        return lambda *a, **kw: method(app, *a, **kw)

    def _write(self, participants):
        members = [{"vrc_name": f"ひと{n}", "data": {self.CLASSIC: {str(n + 5): 1}}}
                   for n in range(participants)]
        write_host_state_like_json(self.db, {"version": 5,
                                             "tabs": [{"participants": members}]})

    def _refresh(self, app, running=True):
        # 主催者自身のリストは既定では使わない（実ファイルに引きずられないため）
        with patch.object(config, "HOST_STATE_PATH", self.db), \
             patch.object(config, "USER_SAVE_PATH", self.user_save), \
             patch.object(ProcessCheck, "is_process_running", return_value=running), \
             patch.object(mainGUI, "save_settings"), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._refresh_host_source(app)

    def _switch_logs(self, app):
        return [m for m in app.logs if "[続行リスト]" in m]

    # ── 待機（ToN ListTool が全員を待機へ移したとき） ─────────
    def _write_lists(self, participants, waiting):
        members = lambda n: [{"vrc_name": f"ひと{i}", "data": {self.CLASSIC: {"5": 1}}}
                             for i in range(n)]
        write_host_state_like_json(self.db, {"version": 5, "tabs": [{
            "participants": members(participants),
            "waiting": members(waiting)}]})

    def test_only_waiting_keeps_the_host_list(self):
        app = self._app()
        self._write_lists(2, 0)
        self._refresh(app)
        self.assertEqual(SharedState.get_list_source(), "host", "前提")

        self._write_lists(0, 3)            # ListTool が全員を待機へ移した
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")

    def test_nobody_anywhere_falls_back(self):
        app = self._app()
        self._write_lists(2, 0)
        self._refresh(app)

        self._write_lists(0, 0)
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertTrue(any("続行リストを持つ人がいません" in m for m in app.logs),
                        app.logs)

    # ── 新しい保存先（SQLite）を優先する ─────────────
    def test_the_sqlite_file_wins_over_the_json(self):
        write_old_host_save(self.path, 3)    # 古い JSON が残っていても
        write_host_state(self.db, [[(0, "いまのひと", {self.CLASSIC: {9}})]])
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {9}}, "sqlite の中身を使う")

    def test_the_host_own_list_is_still_read(self):
        """user_save.json は 2.13 でも現役。主催者自身の希望はここにしか無い"""
        write_host_state(self.db, [[(0, "さんかしゃ", {self.CLASSIC: {5}})]])
        Path(self.user_save).write_text(json.dumps({
            "last_active": "ぬし",
            "accounts": {"ぬし": {"data": {self.CLASSIC: {"42": 1}}}}}),
            encoding="utf-8")
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 42}}, "自分の希望も入る")
        self.assertEqual(app.host_wishes["ぬし"], {self.CLASSIC: {42}})

    def test_without_the_sqlite_file_the_old_json_is_not_read(self):
        """SQLite が一瞬無いだけで、何日も前の古いリストを黙って読まないこと。

        依頼者の PC には 9/21 の host_save.json.gz が残っていた（新は 9/27）。
        古い参加者と古い続行リストで判定すると、他人の周回を誤って自爆させる
        """
        write_old_host_save(self.path, 3)    # 古い JSON だけがある
        app = self._app()

        with patch.object(MatchTNL, "load_host_state") as load:
            self._refresh(app)

        load.assert_not_called()
        self.assertEqual(SharedState.get_list_source(), "tnl", "猶予0なので .tnl へ")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {1}}, ".tnl の中身")
        self.assertNotIn(53, app.keepOn_set.get(self.CLASSIC, set()),
                         "古い JSON の中身は入らない")
        self.assertTrue(any("host_state.sqlite3 がありません" in m for m in app.logs),
                        app.logs)

    def test_a_wal_only_change_is_picked_up(self):
        """SQLite は本体を触らずに -wal だけ伸びることがある"""
        write_host_state(self.db, [[(0, "ひと", {self.CLASSIC: {9}})]])
        app = self._app()
        self._refresh(app)
        stamp = app._host_save_stamp

        Path(self.db + "-wal").write_bytes(b"x" * 16)
        self._refresh(app)

        self.assertNotEqual(app._host_save_stamp, stamp, "読み直している")

    # ── 供給元の選択 ──────────────────────────
    def test_a_closed_list_tool_uses_the_tnl(self):
        self._write(3)
        app = self._app()

        with patch.object(MatchTNL, "load_host_state") as mock_load:
            self._refresh(app, running=False)

        mock_load.assert_not_called()
        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {1}})

    def test_a_running_list_tool_with_participants_uses_the_host_list(self):
        self._write(3)
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6, 7}})
        self.assertEqual(len(app.host_wishes), 3)

    def test_zero_participants_falls_back_to_the_tnl(self):
        self._write(0)
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {1}})

    def test_closing_the_list_tool_returns_to_the_tnl(self):
        self._write(3)
        app = self._app()
        self._refresh(app)
        self.assertEqual(SharedState.get_list_source(), "host")

        self._refresh(app, running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {1}})

    def test_starting_a_lap_switches_to_the_host_list(self):
        self._write(0)
        app = self._app()
        self._refresh(app)
        self.assertEqual(SharedState.get_list_source(), "tnl")

        self._write(2)
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6}})

    def test_a_missing_file_falls_back_to_the_tnl(self):
        app = self._app()

        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")

    # ── ログ ────────────────────────────────
    def test_the_same_source_logs_once(self):
        self._write(3)
        app = self._app()

        for _ in range(3):
            self._refresh(app)

        self.assertEqual(len(self._switch_logs(app)), 1, app.logs)

    def test_every_switch_is_logged(self):
        self._write(3)
        app = self._app()

        self._refresh(app)                 # → host
        self._refresh(app, running=False)  # → tnl
        self._refresh(app)                 # → host

        self.assertEqual(len(self._switch_logs(app)), 3, app.logs)

    def test_the_reason_is_in_the_message(self):
        self._write(0)
        app = self._app()
        self._refresh(app)
        self._write(3)
        self._refresh(app)
        self._refresh(app, running=False)

        joined = "\n".join(self._switch_logs(app))
        self.assertIn("続行リストを持つ人がいません", joined)
        self.assertIn("主催リストへ切替（参加者3人）", joined)
        self.assertIn("ToN ListTool が起動していません", joined)

    def test_a_content_change_is_still_logged(self):
        self._write(2)
        app = self._app()
        self._refresh(app)

        self._write(4)
        self._refresh(app)

        hits = [m for m in app.logs if "続行対象" in m]
        self.assertEqual(len(hits), 2, app.logs)

    # ── 一時的な失敗は供給元を変えない ──────────
    def test_a_decode_failure_keeps_the_current_source(self):
        """書き込み中を掴みうる。ここで tnl へ倒すと3秒ごとに往復する"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        Path(self.db).write_bytes(b"half written garbage")
        self._refresh(app)

        self.assertEqual(SharedState.get_list_source(), "host", "供給元を変えないこと")
        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6, 7}}, "前の値を保持")

    def test_a_repeated_failure_warns_once(self):
        self._write(3)
        app = self._app()
        self._refresh(app)
        Path(self.db).write_bytes(b"half written garbage")

        for _ in range(3):
            self._refresh(app)

        hits = [m for m in app.logs if "読み込み失敗" in m]
        self.assertEqual(len(hits), 1, app.logs)

    def test_a_user_save_change_is_picked_up(self):
        """自分のリストだけ更新したときも読み直すこと"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        Path(self.user_save).write_text(
            json.dumps({"last_active": "serim01",
                        "accounts": {"serim01": {"data": {}}}}), encoding="utf-8")
        with patch.object(MatchTNL, "load_host_state",
                          return_value=({"x": {1}}, {"participants": 1, "listed": 1, "tabs": 1,
                                                     "host_self": None},
                                        {})) as mock_load:
            self._refresh(app)

        mock_load.assert_called_once()

    def test_the_log_marks_the_host_being_included(self):
        self._write(3)
        app = self._app()

        with patch.object(MatchTNL, "load_host_state",
                          return_value=({"x": {1}},
                                        {"participants": 3, "listed": 3, "tabs": 1,
                                         "host_self": "serim01"}, {})):
            self._refresh(app)

        self.assertTrue(any("(+自分)" in m for m in app.logs), app.logs)

    def test_the_log_omits_it_when_the_host_is_missing(self):
        self._write(3)
        app = self._app()

        self._refresh(app)

        hits = [m for m in app.logs if "続行対象" in m]
        self.assertTrue(hits)
        self.assertNotIn("(+自分)", hits[0])

    def test_an_unchanged_file_is_not_reread(self):
        self._write(3)
        app = self._app()
        self._refresh(app)

        with patch.object(MatchTNL, "load_host_state") as mock_load:
            self._refresh(app)

        mock_load.assert_not_called()

    def test_a_returning_source_rereads_even_if_unchanged(self):
        """tnl を経由して戻ってきたら、同じファイルでも読み直すこと"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        self._refresh(app, running=False)
        app.keepOn_set.clear()

        self._refresh(app)

        self.assertEqual(app.keepOn_set, {self.CLASSIC: {5, 6, 7}})

    def test_a_rewrite_of_the_same_size_is_picked_up(self):
        """(mtime, size) の組で見ている理由。片方だけ変わっても拾うこと"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        mtime, size, user, wal = app._host_save_stamp

        # サイズは同じで mtime だけ違う（同じ秒内の書き換え相当）
        app._host_save_stamp = (mtime - 1, size, user, wal)
        with patch.object(MatchTNL, "load_host_state",
                          return_value=({"x": {1}}, {"participants": 1, "listed": 1, "tabs": 1},
                                        {})) as mock_load:
            self._refresh(app)
        mock_load.assert_called_once()

        # mtime は同じでサイズだけ違う
        app._host_save_stamp = (app._host_save_stamp[0], size - 1, user)
        with patch.object(MatchTNL, "load_host_state",
                          return_value=({"y": {2}}, {"participants": 1, "listed": 1, "tabs": 1},
                                        {})) as mock_load:
            self._refresh(app)
        mock_load.assert_called_once()

    def test_recovery_after_a_failure_is_logged_again(self):
        """失敗中は1回だけ。成功で復帰して、また失敗したらまた1回出る"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        Path(self.db).write_bytes(b"half written garbage")
        self._refresh(app)
        self._refresh(app)
        self._write(4)
        self._refresh(app)                      # 復帰
        Path(self.db).write_bytes(b"broken again")
        self._refresh(app)

        hits = [m for m in app.logs if "読み込み失敗" in m]
        self.assertEqual(len(hits), 2, app.logs)

    @staticmethod
    def _tick(app):
        """_poll_host_save を1回まわす。裏のスレッドの代わりにその場で、Tk へ渡す分もその場で"""
        app._off_gui = lambda work: work()
        app._after_from_worker = lambda func, *args: func(*args)
        app._finish_host_poll = lambda result: mainGUI.App._finish_host_poll(app, result)
        mainGUI.App._poll_host_save(app)

    def test_the_tick_survives_a_read_failure(self):
        """プロセス判定ではなく、読み込み側が投げても tick が止まらないこと"""
        self._write(3)
        app = self._app()
        app.after = MagicMock()
        app._poll_host_save = lambda: None

        with patch.object(config, "HOST_STATE_PATH", self.db), \
             patch.object(ProcessCheck, "is_process_running", return_value=True), \
             patch.object(mainGUI.os, "stat", side_effect=RuntimeError("boom")):
            self._tick(app)

        app.after.assert_called_once()

    def test_the_tick_reads_off_the_gui_thread(self):
        """プロセスとファイルを読むのは裏のスレッド（Tk のスレッドを止めない）"""
        app = self._app()
        seen = {}
        app._read_host_source = lambda: seen.setdefault("thread", threading.current_thread()) and ("same", None)
        app._after_from_worker = lambda func, *args: seen.setdefault("handed", func)
        app._finish_host_poll = lambda result: None
        app._off_gui = mainGUI.App._off_gui         # 本物（裏のスレッドを立てる）

        mainGUI.App._poll_host_save(app)
        for _ in range(100):
            if "handed" in seen:
                break
            time.sleep(0.01)

        self.assertIsNot(seen.get("thread"), threading.main_thread())
        self.assertIs(seen.get("handed"), app._finish_host_poll, "結果は Tk のスレッドへ渡す")

    def test_a_stat_failure_falls_back_to_the_tnl(self):
        """OSError 以外で落ちても供給元だけは決まること"""
        self._write(3)
        app = self._app()
        self._refresh(app)

        with patch.object(config, "HOST_STATE_PATH", self.db), \
             patch.object(ProcessCheck, "is_process_running", return_value=True), \
             patch.object(mainGUI.os, "stat", side_effect=OSError("gone")), \
             patch.object(mainGUI, "save_settings"), \
             patch.object(mainGUI, "load_settings", return_value={}):
            mainGUI.App._refresh_host_source(app)

        self.assertEqual(SharedState.get_list_source(), "tnl")

    # ── 参加者別の希望 ────────────────────────
    def test_switching_to_the_tnl_clears_the_wishes(self):
        """古い希望が残ると Sabotage の判定に効いてしまう"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        self.assertTrue(app.host_wishes)

        self._refresh(app, running=False)

        self.assertEqual(app.host_wishes, {})

    def test_switching_to_a_missing_tnl_clears_the_host_list(self):
        """tnl未設定のまま ListTool が落ちても、古い主催リストを残さない"""
        self._write(3)
        app = self._app()
        self._refresh(app)
        app.v_tnl = TestHostListSource.FakeVar("")      # tnl 未設定

        self._refresh(app, running=False)

        self.assertEqual(SharedState.get_list_source(), "tnl")
        self.assertEqual(app.keepOn_set, {}, "続行0件として動く")
        self.assertEqual(app.host_wishes, {})

    # ── tick ────────────────────────────────
    def test_the_tick_survives_a_process_check_failure(self):
        """供給元の判定が投げても、次の tick が予約されること"""
        app = self._app()
        app.after = MagicMock()
        app._poll_host_save = lambda: None

        def boom():
            raise OSError("boom")
        app._read_host_source = boom

        self._tick(app)

        app.after.assert_called_once()

    def test_the_tick_needs_no_switch_to_run(self):
        """チェックボックスは無い。常に供給元を見に行く"""
        app = self._app()
        app.after = MagicMock()
        app._poll_host_save = lambda: None
        called = []
        app._read_host_source = lambda: called.append(1) or ("same", None)

        self._tick(app)

        self.assertEqual(len(called), 1)
        app.after.assert_called_once()




class TestWindowTabAlwaysActive(unittest.TestCase):
    """「この窓を有効化」を廃止した。窓タブの窓はすべて対象になる"""

    def _source(self):
        return Path("mainGUI.py").read_text(encoding="utf-8")

    def test_the_checkbox_and_its_variable_are_gone(self):
        self.assertNotIn("v_active", self._source())

    def test_a_tab_without_a_log_reports_the_error(self):
        """以前は無効化して (None, None) で黙って飛ばせた。その経路が消えたこと"""
        tab = type("FakeTab", (), {})()
        tab.v_log = TestHostListSource.FakeVar("   ")

        cfg, err = mainGUI.WindowTab.get_config(tab)

        self.assertIsNone(cfg)
        self.assertEqual(err, "ログファイルが未設定です")

    def test_no_tab_is_filtered_out_any_more(self):
        """フィルタを外して list(self.tabs) になっていること"""
        source = self._source()

        self.assertNotIn("if tab.v_active", source)
        self.assertEqual(source.count("active_tabs = list(self.tabs)"), 2)




class TestFollowHostSettingRemoved(unittest.TestCase):
    """廃止した follow_host_save キーの後始末"""

    def test_a_legacy_settings_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"tnl_path": "C:/list/my.tnl",
                                        "follow_host_save": False}),
                            encoding="utf-8")

            with patch.object(config, "SETTINGS_PATH", path):
                data = mainGUI.load_settings()

            self.assertEqual(data.get("tnl_path"), "C:/list/my.tnl")

    def test_the_key_is_no_longer_saved(self):
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app._apply_keep_on = lambda new: mainGUI.App._apply_keep_on(app, new)
        with tempfile.TemporaryDirectory() as tmp:
            tnl = Path(tmp) / "list.tnl"
            tnl.write_text(json.dumps({"list_name": "L", "creator": "",
                                       "created_at": "", "data": {}}),
                           encoding="utf-8")
            app.v_tnl = TestHostListSource.FakeVar(str(tnl))
            saved = {}
            with patch.object(mainGUI, "save_settings", saved.update), \
                 patch.object(mainGUI, "load_settings", return_value={}):
                mainGUI.App._load_tnl(app, show_error=False)

        self.assertNotIn("follow_host_save", saved)

    def test_the_app_has_no_follow_switch(self):
        self.assertFalse(hasattr(mainGUI.App, "_on_follow_host_toggled"))
        self.assertFalse(hasattr(mainGUI.App, "_refresh_host_save"))




class TestListSourceShared(unittest.TestCase):
    """続行リストの供給元は全窓共通の状態として持つ"""

    def setUp(self):
        SharedState.set_list_source(None)

    tearDown = setUp

    def test_it_starts_undecided(self):
        self.assertIsNone(SharedState.get_list_source())

    def test_it_round_trips(self):
        for src in ("host", "tnl", None):
            SharedState.set_list_source(src)
            self.assertEqual(SharedState.get_list_source(), src)

    def test_concurrent_writers_leave_a_valid_value(self):
        """ロックの有無を見る。壊れた値が残らないこと"""
        done = threading.Event()

        def spin(src):
            for _ in range(2000):
                SharedState.set_list_source(src)
                if SharedState.get_list_source() not in ("host", "tnl"):
                    done.set()
                    return

        SharedState.set_list_source("host")
        threads = [threading.Thread(target=spin, args=(s,), daemon=True)
                   for s in ("host", "tnl")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        self.assertFalse(done.is_set(), "想定外の値が観測された")
        self.assertIn(SharedState.get_list_source(), ("host", "tnl"))

    def test_the_gui_switch_updates_it(self):
        """mainGUI が供給元を切り替えたら SharedState 側も変わる"""
        app = type("FakeApp", (), {})()
        app.keepOn_set = {}
        app.host_wishes = {}
        app.host_tabs = {"version": 0, "tabs": {}}
        app._host_save_stamp = None
        app._host_save_warned = False
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app.v_tnl = TestHostListSource.FakeVar("")
        app._apply_keep_on = lambda new: mainGUI.App._apply_keep_on(app, new)
        app._apply_host_wishes = lambda new: mainGUI.App._apply_host_wishes(app, new)
        app._apply_host_tabs = lambda new: mainGUI.App._apply_host_tabs(app, new)
        app._load_tnl = lambda **kw: None

        SharedState.set_list_source("host")
        mainGUI.App._fall_back_to_tnl(app, "テスト")

        self.assertEqual(SharedState.get_list_source(), "tnl")




class TestPlayerLines(unittest.TestCase):
    """入退室の行。[Behaviour] の方だけを拾う"""

    PREFIX = "2026.09.18 16:56:57 Debug      -  "
    ME = "usr_0e01408a-ac26-4b08-be43-4ee6db08c6c3"

    def _parse(self, body):
        return LogParser.parse(self.PREFIX + body)

    def test_a_join_is_read(self):
        event = self._parse(f"[Behaviour] OnPlayerJoined serim01 ({self.ME})")

        self.assertEqual(event.kind, LogParser.EVENT_PLAYER_JOINED)
        self.assertEqual(event.user_id, self.ME)
        self.assertEqual(event.player_name, "serim01")

    def test_a_leave_is_read(self):
        event = self._parse(f"[Behaviour] OnPlayerLeft serim01 ({self.ME})")

        self.assertEqual(event.kind, LogParser.EVENT_PLAYER_LEFT)
        self.assertEqual(event.user_id, self.ME)

    def test_a_japanese_name_is_read(self):
        event = self._parse("[Behaviour] OnPlayerJoined しゅんかしゅうとう "
                            "(usr_f651fb3b-9ea3-4136-aa53-2d3e11aebff4)")

        self.assertEqual(event.player_name, "しゅんかしゅうとう")

    def test_a_name_with_brackets_is_read(self):
        event = self._parse("[Behaviour] OnPlayerJoined a (b) c "
                            "(usr_f651fb3b-9ea3-4136-aa53-2d3e11aebff4)")

        self.assertEqual(event.player_name, "a (b) c")
        self.assertEqual(event.user_id, "usr_f651fb3b-9ea3-4136-aa53-2d3e11aebff4")

    def test_the_playerlog_form_is_not_counted(self):
        """同じ入室が2行出る。両方拾うと二重に数える"""
        self.assertIsNone(self._parse("[PlayerLog] OnPlayerJoined: serim01 (VR=False)"))

    def test_similar_lines_are_not_counted(self):
        for body in ("[Behaviour] OnPlayerLeftRoom",
                     f"[Behaviour] OnPlayerJoinComplete serim01",
                     "[Behaviour] OnPlayerLeft VRCPlayer[Remote] 63939541 21 ()"):
            event = self._parse(body)
            self.assertFalse(
                event is not None and event.kind in (
                    LogParser.EVENT_PLAYER_JOINED, LogParser.EVENT_PLAYER_LEFT),
                body)




class TestHostListNeedsOthers(unittest.TestCase):
    """主催リストが必要なのは「他の人がいて、自動自爆ON」のときだけ"""

    ME = "usr_0e01408a"
    PREFIX = "2026.09.18 16:56:57 Debug      -  "
    JOIN = (PREFIX + "[Behaviour] Joining wrld_1234:5678~private(usr_me)")

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("tnl")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_list_source(None)

    def _monitor(self, *, others=(), do_skip=True, known=True,
                 instance_type=config.INSTANCE_PRIVATE, keep_on=None):
        cfg = WindowConfig(do_skip=do_skip, voice_continue="continue.mp3",
                           voice_list_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Bloodbath"
        monitor.st.local_user_id = self.ME
        monitor.st.players = {self.ME, *others}
        monitor.st.players_known = known
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids=(1, 2, 3)):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound"):
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=False)
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    def _stopped(self, monitor):
        return any("主催リストが取れません" in m for m in monitor.logs)

    def _line(self, monitor, body):
        with patch.object(LogMonitor.threading, "Thread"):
            monitor._process(self.PREFIX + body)

    # ── 依頼者の表 ──────────────────────────
    def test_solo_with_the_tnl_decides(self):
        monitor = self._monitor()

        started = self._killers(monitor)

        self.assertFalse(self._stopped(monitor), monitor.logs)
        self.assertIn("do_skip", started)

    def test_others_with_skip_on_and_the_tnl_stops(self):
        monitor = self._monitor(others={"usr_f00d"})

        started = self._killers(monitor)

        self.assertTrue(self._stopped(monitor), monitor.logs)
        self.assertEqual(started, [])

    def test_others_with_skip_on_and_the_host_list_decides(self):
        SharedState.set_list_source("host")
        monitor = self._monitor(others={"usr_f00d"})

        started = self._killers(monitor)

        self.assertFalse(self._stopped(monitor), monitor.logs)
        self.assertIn("do_skip", started)

    def test_others_with_skip_off_and_the_tnl_does_not_stop(self):
        monitor = self._monitor(others={"usr_f00d"}, do_skip=False,
                                keep_on={"Bloodbath/ブラッドバス": {1}})

        self._killers(monitor, [1])

        self.assertFalse(self._stopped(monitor), monitor.logs)
        self.assertTrue(monitor.st.is_continue_round, "tnl で判定している")

    def test_it_applies_to_group_windows_the_same_way(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            solo = self._monitor(instance_type=itype)
            crowd = self._monitor(instance_type=itype, others={"usr_f00d"})

            self.assertFalse(solo._host_list_missing(), itype)
            self.assertTrue(crowd._host_list_missing(), itype)

    def test_a_public_window_never_asks_for_it(self):
        """public は判定そのものをしない。鳴らしても意味が無い"""
        monitor = self._monitor(instance_type=config.INSTANCE_PUBLIC,
                                others={"usr_a", "usr_b"})

        self.assertFalse(monitor._host_list_missing())

    # ── 人数の数え方 ─────────────────────────
    def test_i_am_not_one_of_the_others(self):
        monitor = self._monitor()

        self._line(monitor, f"[Behaviour] OnPlayerJoined serim01 ({self.ME})")

        self.assertFalse(monitor._others_present())

    def test_a_join_and_a_leave_move_the_count(self):
        monitor = self._monitor()

        self._line(monitor, "[Behaviour] OnPlayerJoined friend (usr_f00d)")
        self.assertTrue(monitor._others_present())

        self._line(monitor, "[Behaviour] OnPlayerLeft friend (usr_f00d)")
        self.assertFalse(monitor._others_present())

    def test_the_playerlog_line_does_not_double_count(self):
        monitor = self._monitor()
        self._line(monitor, "[Behaviour] OnPlayerJoined friend (usr_f00d)")
        self._line(monitor, "[PlayerLog] OnPlayerJoined: friend (VR=False)")

        self._line(monitor, "[Behaviour] OnPlayerLeft friend (usr_f00d)")

        self.assertFalse(monitor._others_present(), "1回の退室で0人に戻る")

    def test_joining_an_instance_clears_the_count(self):
        monitor = self._monitor(others={"usr_f00d"})

        self._line(monitor, "[Behaviour] Joining wrld_1234:5678~private(usr_me)")

        self.assertEqual(monitor.st.players, set())
        self.assertTrue(monitor.st.players_known,
                        "入室の瞬間から見ているので信用してよい")

    def test_an_unknown_count_means_others_are_present(self):
        """復元できないときは安全側"""
        monitor = self._monitor(known=False)

        self.assertTrue(monitor._others_present())
        self.assertTrue(monitor._host_list_missing())

    def test_a_fresh_window_starts_unknown(self):
        self.assertFalse(WindowState().players_known)

    def test_my_own_id_comes_from_the_auth_line(self):
        monitor = self._monitor()
        monitor.st.local_user_id = ""

        self._line(monitor, "User Authenticated: serim01 (usr_0e01408a-ac26)")

        self.assertEqual(monitor.st.local_user_id, "usr_0e01408a-ac26")

    # ── 周期チェックと一致していること ──────────────
    def test_the_periodic_check_uses_the_same_condition(self):
        cases = [
            dict(),                                         # ソロ
            dict(others={"usr_f00d"}),                     # 他の人・自爆ON
            dict(others={"usr_f00d"}, do_skip=False),      # 他の人・自爆OFF
            dict(known=False),                               # 不明
            dict(instance_type=config.INSTANCE_PUBLIC, others={"usr_a"}),
        ]
        for case in cases:
            for src in ("tnl", "host", None):
                SharedState.set_list_source(src)
                polled = self._monitor(**case)
                decided = self._monitor(**case)

                with patch.object(PlaySound, "play_sound"):
                    polled._check_group_list_state()
                self._killers(decided)

                self.assertEqual(self._stopped(polled), self._stopped(decided),
                                 f"{case} / {src}")




class TestPlayersRestoredOnStart(unittest.TestCase):
    """マクロを途中で始めても、いまのインスタンスの人数が分かること"""

    ME = "usr_0e01408a"

    def _log_file(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        prefix = "2026.09.18 16:56:57 Debug      -  "
        tmp.write("\n".join(prefix + line for line in lines) + "\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def _restore(self, lines):
        cfg = WindowConfig(log_path=self._log_file(lines))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        with patch.object(ConnectDB, "send_Users", return_value=1), \
             patch.object(monitor, "_start_daemon", side_effect=lambda f, *a: f(*a)):
            monitor._detect_instance_from_log()
        return monitor

    def test_the_players_since_the_last_join_are_restored(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_old:1~private(usr_me)",
            "[Behaviour] OnPlayerJoined ghost (usr_9057)",   # 前のインスタンス
            f"[Behaviour] OnPlayerLeft serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~group(grp_x)~groupAccessType(plus)",
            "[Behaviour] OnPlayerJoined a (usr_a)",
            "[Behaviour] OnPlayerJoined b (usr_b)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[Behaviour] OnPlayerLeft b (usr_b)",
        ])

        self.assertTrue(monitor.st.players_known)
        self.assertEqual(monitor.st.players, {"usr_a", self.ME})
        self.assertEqual(monitor._other_players(), {"usr_a"})
        self.assertTrue(any("自分以外 1人" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_solo_instance_is_restored_as_solo(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_me)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[PlayerLog] OnPlayerJoined: serim01 (VR=False)",
        ])

        self.assertTrue(monitor.st.players_known)
        self.assertFalse(monitor._others_present())

    def test_everyone_left_is_solo(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_me)",
            f"[Behaviour] OnPlayerJoined serim01 ({self.ME})",
            "[Behaviour] OnPlayerJoined a (usr_a)",
            "[Behaviour] OnPlayerLeft a (usr_a)",
        ])

        self.assertFalse(monitor._others_present())

    def test_no_join_line_means_others_are_present(self):
        """復元できない。安全側に倒す"""
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] OnPlayerJoined a (usr_a)",
        ])

        self.assertFalse(monitor.st.players_known)
        self.assertTrue(monitor._others_present())
        self.assertTrue(any("復元できません" in m for m in monitor.logs),
                        monitor.logs)

    def test_a_read_error_means_others_are_present(self):
        cfg = WindowConfig(log_path=self._log_file(["x"]))
        monitor = LogMonitor.LogMonitor(cfg, {}, lambda _m: None, window_idx=1)
        monitor.logger = lambda _m: None

        with patch.object(LogMonitor.LogMonitor, "_iter_log_lines_reversed",
                          side_effect=OSError("locked")):
            monitor._detect_instance_from_log()

        self.assertFalse(monitor.st.players_known)
        self.assertTrue(monitor._others_present())

    def test_my_id_is_restored_too(self):
        monitor = self._restore([
            f"User Authenticated: serim01 ({self.ME})",
            "[Behaviour] Joining wrld_now:2~private(usr_me)",
        ])

        self.assertEqual(monitor.st.local_user_id, self.ME)




class TestGroupNeedsHostList(unittest.TestCase):
    """他の人がいて自動自爆ONなら、主催リストが無いと手を止める。

    ここの窓は人数を復元していない（players_known=False）ので「他の人が
    いる」扱いになる。ソロの扱いは TestHostListNeedsOthers で見る。
    """

    CLASSIC_KEY = "Classic/クラシック"

    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self._stats = patch.object(ConnectDB, "register_round")
        self._stats.start()

    def tearDown(self):
        self._stats.stop()
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source(None)

    def _monitor(self, instance_type=config.INSTANCE_HOSHIIMO, *,
                 voice="lost.mp3", keep_on=None):
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3",
                           voice_list_lost=voice)
        monitor = LogMonitor.LogMonitor(cfg, keep_on or {}, lambda _m: None,
                                        window_idx=1)
        monitor.st.instance_type = instance_type
        monitor.st.in_round = True
        monitor.st.round_type = "Bloodbath"
        monitor.logs = []
        monitor.logger = monitor.logs.append
        return monitor

    def _killers(self, monitor, ids=(1, 2, 3)):
        with patch.object(LogMonitor.threading, "Thread") as mock_thread, \
             patch.object(PlaySound, "play_sound") as mock_play:
            monitor._on_killers(list(ids), monitor.st.round_type, revealed=False)
        self.played = mock_play
        return [c.kwargs["target"].__func__.__name__
                for c in mock_thread.call_args_list if "target" in c.kwargs]

    # ── 止まること ───────────────────────────
    def test_a_group_window_stops_without_the_host_list(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            for src in ("tnl", None):
                SharedState.set_list_source(src)
                monitor = self._monitor(itype)

                started = self._killers(monitor)

                self.assertEqual(started, [], f"{itype}/{src}")
                self.assertFalse(monitor.st.is_continue_round, f"{itype}/{src}")

    def test_the_normal_judgement_does_not_run_either(self):
        """tnlの内容で続行判定してアナウンスを出すのも誤り"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor(keep_on={"Bloodbath/ブラッドバス": {1}})

        self._killers(monitor, [1])

        self.assertFalse(monitor.st.is_continue_round)
        self.assertEqual(SharedState.get_continue_round_count(), 0)

    def test_the_host_list_lets_it_run(self):
        for itype in (config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO):
            monitor = self._monitor(itype)

            started = self._killers(monitor)

            self.assertIn("do_skip", started, itype)   # Bloodbathは問答無用スキップ

    def test_a_solo_private_window_uses_the_tnl(self):
        for src in ("tnl", None):
            SharedState.set_list_source(src)
            monitor = self._monitor(config.INSTANCE_PRIVATE,
                                    keep_on={"Bloodbath/ブラッドバス": {1}})
            monitor.st.local_user_id = "usr_me"
            monitor.st.players = {"usr_me"}
            monitor.st.players_known = True

            started = self._killers(monitor, [1])

            self.assertTrue(monitor.st.is_continue_round, src)
            self.assertNotIn("do_skip", started, src)

    def test_a_private_window_with_others_stops_too(self):
        """private でも他の人がいれば他人の周回。自分の tnl では裁かない"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor(config.INSTANCE_PRIVATE)

        self.assertTrue(monitor._host_list_missing())

    # ── 通知 ────────────────────────────────
    def test_it_logs_and_plays_once(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._killers(monitor)

        self.assertEqual(
            len([m for m in monitor.logs if "主催リストが取れません" in m]), 1,
            monitor.logs)
        self.played.assert_called_once_with("lost.mp3")

    def test_three_rounds_still_notify_once(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        for _ in range(3):
            self._killers(monitor)

        self.assertEqual(
            len([m for m in monitor.logs if "主催リストが取れません" in m]), 1,
            monitor.logs)

    def test_the_terror_line_is_still_logged(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._killers(monitor)

        self.assertTrue(any("Terror set:" in m for m in monitor.logs), monitor.logs)

    def test_recovery_logs_once(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._killers(monitor)

        SharedState.set_list_source("host")
        self._killers(monitor)
        self._killers(monitor)

        back = [m for m in monitor.logs if "主催リストが戻りました" in m]
        self.assertEqual(len(back), 1, monitor.logs)
        self.assertFalse(monitor.st.list_lost_notified)

    def test_losing_it_again_notifies_again(self):
        SharedState.set_list_source("tnl")
        monitor = self._monitor()
        self._killers(monitor)
        SharedState.set_list_source("host")
        self._killers(monitor)

        SharedState.set_list_source("tnl")
        self._killers(monitor)

        self.assertEqual(
            len([m for m in monitor.logs if "主催リストが取れません" in m]), 2,
            monitor.logs)

    def test_an_empty_voice_is_silent(self):
        """空文字は play_sound 側で弾かれる（他のアナウンスと同じ作り）"""
        SharedState.set_list_source("tnl")
        monitor = self._monitor(voice="")

        self._killers(monitor)

        self.assertEqual(self.played.call_args.args, ("",))
        with patch.object(PlaySound, "threading") as mock_threading:
            PlaySound.play_sound("")
        mock_threading.Thread.assert_not_called()

    def test_hands_free_cannot_apply_to_a_group_window(self):
        """放置モードはprivateの窓だけ。グループの窓では抑制にならない"""
        SharedState.set_hands_free(True)
        SharedState.set_list_source("tnl")
        monitor = self._monitor()

        self._killers(monitor)

        self.assertFalse(monitor._hands_free())
        self.played.assert_called_once_with("lost.mp3")
        self.assertTrue(any("主催リストが取れません" in m for m in monitor.logs))

    def test_the_voice_defaults_to_the_bundled_file(self):
        self.assertTrue(config.VOICE_LIST_LOST.endswith("StopAutoSuicide.mp3"),
                        config.VOICE_LIST_LOST)
        self.assertTrue(Path(config.VOICE_LIST_LOST).exists(),
                        "voice フォルダに実ファイルがあること")

    def test_the_window_config_still_defaults_to_silence(self):
        """GUIから注入されるまでは鳴らさない（他のvoiceと同じ）"""
        self.assertEqual(WindowConfig().voice_list_lost, "")
