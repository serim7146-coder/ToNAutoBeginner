"""全種類の窓（焼き芋・干し芋・private 相当・public・その他のグループ・Emerald City）は、ToN ListTool のタブへ対応づけず、
在室者の名前で続行リストを組む（焼き芋・干し芋・private 相当は依頼者 2026-11-19、全種類は 2026-11-20）。

- 判定に使うリスト = 在室者の希望（全タブ・待機を含む全員ぶんから表示名で引く）＋ 自分以外がいるときの主催者のリスト
- 窓とタブの対応づけ（MatchTNL.tab_for_window）を、判定は呼ばない（別のインスタンスのタブへ誤って対応づけた誤爆の再現）
- 判定の行の後ろの根拠（`（在室N人・リストあり M人・続行希望: …）`。主催リストのときだけ・全種類の窓）
本物の入力・音・録画・窓・ネットワークは使わない。ファイルは一時フォルダだけ
"""
from tests.support import *  # noqa: F401,F403
import config
import ContinueTable
import GroupRound
import LogParser
import SharedState
import LogMonitor
import mainGUI
from State import WindowConfig

FOG = "Fog/霧"
ALT = "Alternate/オルタネイト"
STAR = GroupRound.SABOTAGE_STAR_KEY
MURDER = GroupRound.SABOTAGE_MURDER_KEY
T = own(154)            # Glaggle Gang（ListTool 154）。判定するテラー
OTHER = own(155)        # 別のテラー
THIRD = own(150)
HOST = "ぬし"
ME = "usr_0e01408a"
INSTANCES = (config.INSTANCE_YAKIIMO, config.INSTANCE_HOSHIIMO, config.INSTANCE_PRIVATE)
# 以前はタブで引いていた種類（依頼者 2026-11-20 から名前で引く）
TAB_ERA_INSTANCES = (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP, config.INSTANCE_EMERALD_CITY)
ALL_INSTANCES = INSTANCES + TAB_ERA_INSTANCES
# 種類 → (ラウンド, Killers 行のラウンド名, 続行リストのキー)
ROUNDS = {config.INSTANCE_YAKIIMO: ("Fog", "Fog (Alternate)", FOG),
          config.INSTANCE_HOSHIIMO: ("Alternate", "Alternate", ALT),
          config.INSTANCE_PRIVATE: ("Alternate", "Alternate", ALT),
          config.INSTANCE_PUBLIC: ("Alternate", "Alternate", ALT),
          config.INSTANCE_OTHER_GROUP: ("Alternate", "Alternate", ALT),
          config.INSTANCE_EMERALD_CITY: ("Alternate", "Alternate", ALT)}


class _Base(unittest.TestCase):
    def setUp(self):
        SharedState.set_instance_type(config.INSTANCE_PUBLIC)
        SharedState.continue_round_reset()
        SharedState.set_hands_free(False)
        SharedState.set_list_source("host")
        self.addCleanup(SharedState.set_list_source, None)
        self.addCleanup(SharedState.continue_round_reset)
        self.addCleanup(ContinueTable.set_current, ContinueTable.Store())
        ContinueTable.set_current(ContinueTable.Store())
        for p in (patch.object(ConnectDB, "register_round"),
                  patch.object(Recorder, "on_continue_start"),
                  patch.object(PlaySound, "play_sound"),
                  patch.object(LogMonitor.threading, "Thread")):
            p.start()
            self.addCleanup(p.stop)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db = Path(self._dir.name) / "host_state.sqlite3"
        self.user_save = Path(self._dir.name) / "user_save.json"

    # ── 道具 ──────────────────────────────
    def _load(self, tabs, host_ids=None):
        """tabs は [[(section, 名前, {キー: {ListTool の番号}}), ...], ...]。host_ids があれば
        user_save で last_active の主催者（HOST）に FOG の希望を持たせる。(続行リスト, meta, 希望)"""
        write_host_state(self.db, tabs)
        user_save = None
        if host_ids is not None:
            self.user_save.write_text(json.dumps({
                "last_active": HOST,
                "accounts": {HOST: {"data": {FOG: {str(i): 1 for i in host_ids}}}}}), encoding="utf-8")
            user_save = str(self.user_save)
        return MatchTNL.load_host_state(str(self.db), user_save)

    def _monitor(self, itype, wishes, present=("alpha",), me="serim01", host_self=None, known=True,
                 keep=None, tabs=None, sus=()):
        """present は自分以外の在室者の表示名。共有する続行リストは SharedLists で持つ"""
        lists = MatchTNL.SharedLists(keep if keep is not None else {}, wishes, None,
                                     tabs if tabs is not None else {"version": 0, "tabs": {}}, host_self)
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", voice_list_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, None, lambda _m: None, window_idx=1, lists=lists)
        st = monitor.st
        st.instance_type = itype
        st.in_round = True
        st.round_type = ROUNDS.get(itype, ("Fog",))[0]
        st.local_player_name = me
        st.local_user_id = ME
        st.players = {ME}
        st.player_names = {ME: me}
        for n, name in enumerate(present):
            uid = f"usr_{n:04x}c"
            st.players.add(uid)
            st.player_names[uid] = name
        st.players_known = known
        st.sus_players = set(sus)
        st.terror_ids = []
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._focus_for_freeze = MagicMock()
        return monitor

    @staticmethod
    def _judge(monitor, tid):
        """判定の結果（continue / skip / quiet …）。st.terror_ids も合わせる"""
        st = monitor.st
        st.terror_ids = [tid]
        killers_type = "Fog (Alternate)" if st.round_type == "Fog" else st.round_type
        return LogMonitor.LogMonitor._outcome(monitor._plan(killers_type, [tid], False))

    @staticmethod
    def _tabs_of(meta, version=1):
        return {"version": version, "tabs": meta["tabs_data"]}

    @staticmethod
    def _judgement_lines(monitor):
        return [m for m in monitor.logs if "判定:" in m]

    def _decide(self, monitor, tid, plan=None):
        """判定の行まで通す（_decide_with_keep_on_set）。判定の行を返す"""
        st = monitor.st
        st.terror_ids = [tid]
        monitor._decide_with_keep_on_set(st.round_type, plan)
        lines = self._judgement_lines(monitor)
        self.assertEqual(len(lines), 1, monitor.logs)
        return lines[0]


# ═══════════════════════════════════════════════
class TestListFromPresentNames(_Base):
    """1・4・13: 在室者の名前の希望 ∪ 主催者。タブの中身・不在の人の希望は使わない"""

    def test_the_list_is_the_present_players_wishes_and_the_host(self):
        for itype in INSTANCES:
            _round, _kr, key = ROUNDS[itype]
            wishes = {"alpha": {key: {T}}, "gone": {key: {OTHER}}, HOST: {key: {THIRD}}}
            # 別のタブの中身（OTHER・THIRD だけ）。使わない
            tabs = {"version": 1, "tabs": {0: {"participants": {"alpha"}, "waiting": set(),
                                                 "keepOn": {key: {OTHER}}, "wishes": {"alpha": {key: {OTHER}}}}}}
            monitor = self._monitor(itype, wishes, present=("alpha",), host_self=HOST, tabs=tabs)
            with self.subTest(itype=itype):
                self.assertEqual(monitor._keep_on_base(), {key: {T, THIRD}})
                self.assertEqual(self._judge(monitor, T), "continue", "在室者の希望")
                self.assertEqual(self._judge(monitor, THIRD), "continue", "不在の主催者の希望（他の人がいる）")
                self.assertEqual(self._judge(monitor, OTHER), "skip",
                                 "不在の人（タブの中身を含む）の希望は使わない")

    def test_a_present_host_is_not_doubled(self):
        monitor = self._monitor(config.INSTANCE_YAKIIMO, {"alpha": {FOG: {T}}, HOST: {FOG: {OTHER}}},
                                present=("alpha", HOST), host_self=HOST)

        self.assertEqual(set(monitor._effective_wishes()), {"alpha", HOST})
        self.assertEqual(monitor._keep_on_base(), {FOG: {T, OTHER}})

    def test_a_host_without_a_wish_entry_or_name_adds_nothing(self):
        for host_self in (None, "ghost"):
            monitor = self._monitor(config.INSTANCE_YAKIIMO, {"alpha": {FOG: {T}}, HOST: {FOG: {OTHER}}},
                                    host_self=host_self)
            with self.subTest(host_self=host_self):
                self.assertEqual(set(monitor._effective_wishes()), {"alpha"})
                self.assertEqual(self._judge(monitor, OTHER), "skip")

    def test_a_tab_that_would_be_chosen_is_ignored_in_a_private_window_too(self):
        """13: private の窓（他の人がいる）もタブではなく名前"""
        _k, meta, wishes = self._load([[(0, "x1", {FOG: {155}}), (1, "w1", {FOG: {154}})],
                                       [(0, "o1", {FOG: {155}}), (0, "o2", {FOG: {155}})]])
        monitor = self._monitor(config.INSTANCE_PRIVATE, wishes, present=("w1", "o1", "o2"),
                                tabs=self._tabs_of(meta))
        monitor.st.round_type = "Fog"

        self.assertEqual(self._judge(monitor, T), "continue")
        self.assertIs(monitor._tab_index, LogMonitor._UNSET, "タブへの対応づけを計算していない")
        self.assertEqual([m for m in monitor.logs if "[主催リスト]" in m], [])


class TestNeverUsesTabs(_Base):
    """2: どの種類の窓も tab_for_window・tab_match_detail を呼ばない（依頼者 2026-11-20）"""

    def test_no_tab_matching_in_a_judging_window(self):
        _k, meta, wishes = self._load([[(0, "alpha", {FOG: {155}})], [(0, "beta", {FOG: {154}})]])
        for itype in ALL_INSTANCES:
            monitor = self._monitor(itype, wishes, present=("alpha", "beta"), tabs=self._tabs_of(meta))
            with self.subTest(itype=itype), \
                    patch.object(MatchTNL, "tab_for_window", side_effect=AssertionError("tab_for_window")), \
                    patch.object(MatchTNL, "tab_match_detail", side_effect=AssertionError("tab_match_detail")):
                monitor._keep_on()
                monitor._keep_on_base()
                monitor._effective_wishes()
                monitor._group_wishes()
                monitor._plan(ROUNDS[itype][1], [T], False)
                self.assertEqual([m for m in monitor.logs if "[主催リスト] タブ" in m or "特定できません" in m], [],
                                 monitor.logs)

    def test_public_style_windows_use_present_names_not_the_tab(self):
        """public・その他のグループ・Emerald City も在室者の名前で引く。
        タブ 0 の keepOn は OTHER だけだが、在室者 alpha の希望（OTHER）と、待機の gamma の希望（T）で組む"""
        _k, meta, wishes = self._load([[(0, "alpha", {FOG: {155}}), (1, "gamma", {FOG: {154}})],
                                       [(0, "beta", {FOG: {150}})]])
        for itype in TAB_ERA_INSTANCES:
            monitor = self._monitor(itype, wishes, present=("alpha", "gamma"), tabs=self._tabs_of(meta))
            monitor.st.round_type = "Fog"
            with self.subTest(itype=itype):
                self.assertEqual(meta["tabs_data"][0]["keepOn"], {FOG: {OTHER}})
                self.assertEqual(monitor._keep_on_base(), {FOG: {T, OTHER}})
                self.assertIs(monitor._tab_index, LogMonitor._UNSET)


class TestMisboundTab(_Base):
    """3・4: タブの対応づけが別のインスタンスのタブを選ぶ並びでも、名前で引くので誤爆しない"""

    def _state(self):
        # タブ0 = この窓のインスタンス（参加者 x1。待機の w1 が 154 を希望）。タブ1 = 別のインスタンス（o1・o2）
        return self._load([[(0, "x1", {FOG: {155}}), (1, "w1", {FOG: {154}})],
                           [(0, "o1", {FOG: {155}}), (0, "o2", {FOG: {155}})]])

    def test_a_player_in_the_waiting_section_is_judged_by_name(self):
        _k, meta, wishes = self._state()
        present = {"serim01", "w1", "o1", "o2"}
        # 前提: 名前の重なりでは別のインスタンスのタブ 1 を選び、そのタブには T が無い
        self.assertEqual(MatchTNL.tab_for_window(meta["tabs_data"], present), 1)
        self.assertNotIn(T, meta["tabs_data"][1]["keepOn"][FOG])
        monitor = self._monitor(config.INSTANCE_YAKIIMO, wishes, present=("w1", "o1", "o2"),
                                tabs=self._tabs_of(meta))

        self.assertEqual(self._judge(monitor, T), "continue")

    def test_another_tabs_wish_does_not_leak(self):
        """別のタブの、いない人だけが希望するテラーは自爆"""
        _k, meta, wishes = self._load([[(0, "p1", {FOG: {155}})], [(0, "o1", {FOG: {154}}), (0, "o2", {FOG: {154}})]])
        monitor = self._monitor(config.INSTANCE_YAKIIMO, wishes, present=("p1",), tabs=self._tabs_of(meta))

        self.assertEqual(self._judge(monitor, T), "skip")
        self.assertEqual(self._judge(monitor, OTHER), "continue")


class TestHostList(_Base):
    """5・11・12: 不在の主催者のリスト"""

    def _state(self):
        # 主催者（HOST）だけが 154 を希望。周回の参加者 p1 は 155
        return self._load([[(0, "p1", {FOG: {155}})]], host_ids=(154,))

    def test_the_absent_host_list_applies_when_others_are_present(self):
        _k, meta, wishes = self._state()
        self.assertEqual(meta["host_self"], HOST)
        monitor = self._monitor(config.INSTANCE_YAKIIMO, wishes, present=("p1",), host_self=meta["host_self"])

        self.assertEqual(self._judge(monitor, T), "continue")
        monitor.host_self = None
        self.assertEqual(self._judge(monitor, T), "skip", "主催者の名前が分からなければ足さない")

    def test_a_solo_window_does_not_add_the_host(self):
        _k, meta, wishes = self._state()
        for itype in INSTANCES:
            monitor = self._monitor(itype, wishes, present=(), host_self=HOST)
            monitor.st.round_type = "Fog"
            with self.subTest(itype=itype):
                self.assertEqual(monitor._effective_wishes(), {})
                self.assertEqual(monitor._keep_on_base(), {})

    def test_nobody_present_has_a_list_but_the_absent_host_does(self):
        """在室者の誰もリストを持っていなければ止める。主催者のリストだけで動かさない"""
        _k, meta, wishes = self._state()
        monitor = self._monitor(config.INSTANCE_HOSHIIMO, wishes, present=("stranger",), host_self=HOST)
        monitor.st.round_type = "Alternate"

        self.assertEqual(monitor._effective_wishes(include_host=False), {})
        self.assertEqual(monitor._list_block_reason(), "wishes")
        monitor._on_killers([T], "Alternate", revealed=True)
        started = [c.kwargs["target"].__func__.__name__ for c in LogMonitor.threading.Thread.call_args_list
                   if "target" in c.kwargs]
        self.assertNotIn("do_skip", started)
        self.assertTrue(any("続行リストがありません" in m for m in monitor.logs), monitor.logs)


class TestUnknownRoster(_Base):
    """7: 誰がいるか分からない窓は共有リスト"""

    def test_an_unknown_roster_uses_the_shared_list(self):
        for itype in (config.INSTANCE_YAKIIMO, config.INSTANCE_HOSHIIMO):
            monitor = self._monitor(itype, {"alpha": {ROUNDS[itype][2]: {T}}}, known=False,
                                    keep={ROUNDS[itype][2]: {OTHER}}, host_self=HOST)
            with self.subTest(itype=itype):
                self.assertIs(monitor._keep_on(), monitor.keepOn_set)
                self.assertIsNone(monitor._effective_wishes())

    def test_an_unknown_private_window_uses_the_shared_list_too(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE, {"alpha": {ALT: {T}}}, known=False, keep={ALT: {OTHER}})

        self.assertIs(monitor._keep_on_base(), monitor.keepOn_set)
        self.assertIsNone(monitor._effective_wishes())


class TestRosterChanges(_Base):
    """8: 待機から入室した人の希望は、次の判定から効く"""

    def test_a_waiting_player_who_joins_is_counted_from_the_next_judgement(self):
        wishes = {"alpha": {FOG: {OTHER}}, "waiter": {FOG: {T}}}
        monitor = self._monitor(config.INSTANCE_YAKIIMO, wishes, present=("alpha",))
        self.assertEqual(self._judge(monitor, T), "skip")

        monitor._apply_player_event(LogParser.LogEvent(LogParser.EVENT_PLAYER_JOINED,
                                                       user_id="usr_9999w", player_name="waiter"))

        self.assertEqual(self._judge(monitor, T), "continue")
        monitor._apply_player_event(LogParser.LogEvent(LogParser.EVENT_PLAYER_LEFT,
                                                       user_id="usr_9999w", player_name="waiter"))
        self.assertEqual(self._judge(monitor, T), "skip")


class TestSwapHostWishes(_Base):
    """6・9: 全窓で共有するリストの差し替えとの連携"""

    def _fake_app(self, lists):
        app = type("FakeApp", (), {})()
        app.lists = lists
        app._shared_lists = lambda: app.lists
        for name in ("keepOn_set", "host_wishes", "host_participants", "host_tabs", "host_self"):
            setattr(type(app), name, getattr(mainGUI.App, name))
        app.logs = []
        app._log = app.logs.append
        app.lbl_tnl = MagicMock()
        app._host_save_stamp = None
        app._host_loss_since = None
        app._apply_own_list = lambda log=False: None
        for name in ("_apply_keep_on", "_apply_host_wishes", "_apply_host_tabs", "_host_loss_cleared",
                     "_fall_back_to_tnl", "_apply_host_source"):
            method = getattr(mainGUI.App, name)
            setattr(app, name, (lambda m: lambda *a, **kw: m(app, *a, **kw))(method))
        return app

    def test_the_wishes_swapped_every_poll_apply_to_the_next_judgement(self):
        monitor = self._monitor(config.INSTANCE_YAKIIMO, {}, present=("alpha",))
        app = self._fake_app(monitor.lists)
        tabs_before, roster_before = monitor.host_tabs, set(monitor.st.players)

        results = []
        for new in ({"alpha": {FOG: {OTHER}}}, {"alpha": {FOG: {T}}}, {"alpha": {FOG: {OTHER}}}):
            mainGUI.App._apply_host_wishes(app, new)
            results.append(self._judge(monitor, T))

        self.assertEqual(results, ["skip", "continue", "skip"])
        self.assertIs(monitor.host_tabs, tabs_before, "タブの版は変えない")
        self.assertEqual(monitor.st.players, roster_before)

    def test_apply_host_source_shares_host_self_and_falling_back_resets_it(self):
        keep, meta, wishes = self._load([[(0, "p1", {FOG: {155}})]], host_ids=(154,))
        monitor = self._monitor(config.INSTANCE_YAKIIMO, {}, present=("p1",))
        app = self._fake_app(monitor.lists)

        mainGUI.App._apply_host_source(app, ("loaded", (keep, meta, wishes, (1.0, 1))))

        self.assertEqual(app.host_self, HOST)
        self.assertEqual(monitor.host_self, HOST, "窓から見える（同じ SharedLists）")
        self.assertEqual(self._judge(monitor, T), "continue", "不在の主催者のリストが効く")

        mainGUI.App._fall_back_to_tnl(app, "テスト")

        self.assertIsNone(monitor.host_self)
        self.assertEqual(SharedState.get_list_source(), "tnl")

    def test_shared_lists_default_has_no_host_self(self):
        self.assertIsNone(MatchTNL.SharedLists().host_self)
        self.assertEqual(MatchTNL.SharedLists(host_self="a").host_self, "a")


class TestSabotageByNames(_Base):
    """10: 干し芋の Sabotage も同じ名前"""

    def _judge_sabotage(self, wishes, present, host_self=None):
        monitor = self._monitor(config.INSTANCE_HOSHIIMO, wishes, present=present, host_self=host_self,
                                sus=("murderer",))
        monitor.st.round_type = "Sabotage"
        monitor.st.terror_ids = [T]
        return LogMonitor.LogMonitor._outcome(monitor._plan("Sabotage", [T], False))

    def test_a_present_player_star_wish_counts(self):
        self.assertEqual(self._judge_sabotage({"alpha": {STAR: {T}}}, ("alpha", "murderer")), "continue")

    def test_an_absent_non_host_star_wish_does_not_count(self):
        self.assertEqual(self._judge_sabotage({"gone": {STAR: {T}}, "alpha": {STAR: {OTHER}}},
                                              ("alpha", "murderer")), "skip")

    def test_an_absent_host_star_wish_counts_when_others_are_present(self):
        self.assertEqual(self._judge_sabotage({HOST: {STAR: {T}}, "alpha": {STAR: {OTHER}}},
                                              ("alpha", "murderer"), host_self=HOST), "continue")


# ═══════════════════════════════════════════════
class TestBasisSuffix(_Base):
    """14〜18: 判定の行の後ろの根拠"""

    def _group(self, wishes, present=("alpha", "bravo", "charlie", "delta"), **kw):
        monitor = self._monitor(config.INSTANCE_YAKIIMO, wishes, present=present, **kw)
        return monitor

    def test_continue_shows_the_counts_and_only_the_wishers(self):
        wishes = {"alpha": {FOG: {T}}, "bravo": {FOG: {T}}, "charlie": {FOG: {OTHER}}}
        monitor = self._group(wishes)

        line = self._decide(monitor, T)

        self.assertTrue(line.endswith("【プレイ】（在室5人・リストあり3人・続行希望: alpha、bravo）"), line)
        self.assertNotIn("charlie", line, "希望していない在室者の名前は出さない")
        self.assertNotIn("delta", line)

    def test_skip_shows_the_counts_only(self):
        wishes = {"alpha": {FOG: {OTHER}}, "charlie": {FOG: {OTHER}}}
        monitor = self._group(wishes)

        line = self._decide(monitor, T)

        self.assertTrue(line.endswith("【スキップ】（在室5人・リストあり2人）"), line)
        for name in ("alpha", "charlie", "delta"):
            self.assertNotIn(name, line)

    def test_an_unknown_roster_is_shown_as_the_shared_list(self):
        monitor = self._group({"alpha": {FOG: {T}}}, known=False, keep={FOG: {T}})

        line = self._decide(monitor, T)

        self.assertTrue(line.endswith("【プレイ】（共有リスト）"), line)

    def test_the_host_marks(self):
        """いない主催者のリストを足したとき・主催者が希望者のとき"""
        monitor = self._group({"alpha": {FOG: {OTHER}}, HOST: {FOG: {T}}}, present=("alpha",), host_self=HOST)
        line = self._decide(monitor, T)
        self.assertTrue(line.endswith(f"【プレイ】（在室2人・リストあり1人・続行希望: {HOST}（主催者）・主催者を足す）"), line)

        monitor = self._group({"alpha": {FOG: {OTHER}}, HOST: {FOG: {OTHER}}}, present=("alpha",), host_self=HOST)
        line = self._decide(monitor, T)
        self.assertTrue(line.endswith("【スキップ】（在室2人・リストあり1人・主催者を足す）"), line)

        monitor = self._group({"alpha": {FOG: {T}}, HOST: {FOG: {T}}}, present=("alpha", HOST), host_self=HOST)
        line = self._decide(monitor, T)
        self.assertTrue(line.endswith(f"【プレイ】（在室3人・リストあり2人・続行希望: alpha、{HOST}（主催者））"), line)
        self.assertNotIn("主催者を足す", line, "いる主催者は足したことにしない")

    def test_a_private_window_gets_the_suffix_after_the_overlay_label(self):
        monitor = self._monitor(config.INSTANCE_PRIVATE, {"alpha": {ALT: {T}}}, present=("alpha",))

        line = self._decide(monitor, T)

        self.assertTrue(line.endswith("【プレイ】（在室2人・リストあり1人・続行希望: alpha）"), line)

    def test_tnl_mode_gets_nothing(self):
        SharedState.set_list_source("tnl")
        monitor = self._group({"alpha": {FOG: {T}}}, keep={FOG: {T}})
        line = self._decide(monitor, T)
        self.assertTrue(line.endswith("【プレイ】"), line)

        for itype in TAB_ERA_INSTANCES:
            window = self._monitor(itype, {"alpha": {FOG: {T}}}, keep={FOG: {T}})
            with self.subTest(itype=itype):
                self.assertEqual(window._basis_label([T]), "")

    def test_every_window_type_gets_the_basis_when_the_host_list_is_the_source(self):
        """依頼者 2026-11-20: public・その他のグループ・Emerald City にも根拠を付ける"""
        for itype in TAB_ERA_INSTANCES:
            window = self._monitor(itype, {"alpha": {ALT: {T}}}, present=("alpha",), keep={ALT: {T}})
            with self.subTest(itype=itype):
                self.assertEqual(window._basis_label([T]), "（在室2人・リストあり1人・続行希望: alpha）")

    def test_the_line_matches_the_decision_even_if_the_list_is_swapped_after_planning(self):
        """判定を作ったあと（Utils 待ちなど）に主催リストが差し替わっても、行は判定の時点の根拠"""
        monitor = self._group({"alpha": {FOG: {T}}})
        st = monitor.st
        st.terror_ids = [T]
        plan = monitor._plan("Fog (Alternate)", [T], False)
        self.assertEqual(LogMonitor.LogMonitor._outcome(plan), "continue")

        monitor.host_wishes = {"alpha": {FOG: {OTHER}}}         # 3 秒ごとの差し替え

        line = self._decide(monitor, T, plan)
        self.assertTrue(line.endswith("【プレイ】（在室5人・リストあり1人・続行希望: alpha）"), line)


if __name__ == "__main__":
    unittest.main()
