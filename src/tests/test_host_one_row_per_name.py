"""ToN ListTool の主催リスト（host_local_state.bin）は、名前ごとに 1 行だけ使う。全種類の窓が在室者の名前で引く。

ListTool は登録した人を各タブの待機に残し続け、行はその人のリストの登録時点の写し。同じ名前の行が複数あって
中身が違う。全部を和集合にすると、ListTool が判定に使う行に無い続行まで拾う（2026-11-20 の実例）。
- 参加者の行があればそれ（複数なら created_at が最新）。無ければ待機の行のうち created_at が最新（依頼者 2026-11-20）
- created_at が無い・読めない行は最も古い。同点はファイルで先に出てくる行
- 全部 OFF の行を選んだ人は {}（リストはあるが続行希望なし）
- 中身の違う行を持つ名前の人数を debug.log に 1 回
- public・その他のグループ・Emerald City の窓も、タブではなく在室者の名前で判定し、根拠の文も付く

偽の host_local_state.bin（一時フォルダ）だけを使う。DPAPI は本物を呼ばない。本物の入力・音・ネットワークは使わない
"""
from tests.support import *  # noqa: F401,F403
import config
import ContinueTable
import DebugLog
import GroupRound
import LogMonitor
import SharedState
import TerrorIds
from State import WindowConfig
from tests.test_host_local_state import LocalStateTestCase, person

MIDNIGHT = "Midnight/ミッドナイト"
ALT = "Alternate/オルタネイト"
SIGNUS = 42             # ListTool の番号 42 = 独自の番号 42（Classic の Signus）。Midnight の枠に出る
BAD_BATTER = 41         # 同 41（Bad Batter）。Midnight の枠に出る
T = own(154)            # Fog の枠で判定するテラー（ListTool 154）
OTHER = own(155)
THIRD = own(150)
ME = "usr_0e01408a"


def row(name, rounds, created_at, **extra):
    return person(name, rounds, created_at=created_at, **extra)


class TestOneRowPerName(LocalStateTestCase):
    def setUp(self):
        super().setUp()
        TerrorIds._reported.clear()
        self.addCleanup(TerrorIds._reported.clear)

    def test_alus_shape_the_participant_row_wins_over_an_old_waiting_row_with_signus(self):
        """1: 2026-11-20 の実例の形。参加者の行（2026-11-18・Midnight に Signus 無し）と待機の 3 行（古い行にだけ Signus）。
        Signus は続行に入らない"""
        got = self.load([{"participants": [row("alu", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18 17:59:04")],
                          "waiting": [row("alu", {MIDNIGHT: {BAD_BATTER}}, "2026-11-16 10:00:00"),
                                      row("alu", {MIDNIGHT: {BAD_BATTER, SIGNUS}}, "2026-10-18 09:00:00"),
                                      row("alu", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18 20:00:00")]}])
        wishes = got[2]

        self.assertEqual(wishes["alu"], {MIDNIGHT: {BAD_BATTER}})
        self.assertNotIn(SIGNUS, wishes["alu"][MIDNIGHT])
        # 従来どおり: 畳んだ続行リストは参加者の行だけ、タブの内訳は全行の和集合
        self.assertEqual(got[0], {MIDNIGHT: {BAD_BATTER}})
        self.assertEqual(got[1]["tabs_data"][0]["wishes"]["alu"], {MIDNIGHT: {BAD_BATTER, SIGNUS}})

    def test_a_participant_row_wins_even_when_a_waiting_row_is_newer(self):
        """2"""
        got = self.load([{"participants": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-01-01 00:00:00")],
                          "waiting": [row("a", {MIDNIGHT: {SIGNUS}}, "2026-12-31 23:59:59")]}])

        self.assertEqual(got[2]["a"], {MIDNIGHT: {BAD_BATTER}})

    def test_the_newest_participant_row_wins_among_several_tabs(self):
        got = self.load([{"participants": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-11-01 00:00:00")]},
                         {"participants": [row("a", {MIDNIGHT: {SIGNUS}}, "2026-11-02 00:00:00")]}])

        self.assertEqual(got[2]["a"], {MIDNIGHT: {SIGNUS}})

    def test_only_waiting_rows_the_newest_wins(self):
        """3"""
        got = self.load([{"waiting": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-11-10 00:00:00"),
                                      row("a", {MIDNIGHT: {SIGNUS}}, "2026-11-12 00:00:00"),
                                      row("a", {MIDNIGHT: {BAD_BATTER, SIGNUS}}, "2026-11-11 00:00:00")]}])

        self.assertEqual(got[2]["a"], {MIDNIGHT: {SIGNUS}})
        self.assertEqual(got[1]["participants"], 0)

    def test_the_newest_waiting_row_can_be_in_another_tab(self):
        got = self.load([{"waiting": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-11-10 00:00:00")]},
                         {"waiting": [row("a", {MIDNIGHT: {SIGNUS}}, "2026-11-12 00:00:00")]}])

        self.assertEqual(got[2]["a"], {MIDNIGHT: {SIGNUS}})

    def test_an_all_off_row_chosen_means_a_list_with_no_wish(self):
        """4: いちばん新しい待機の行が全部 OFF なら {}。古い行の希望は使わず、リストを持つ人として数える"""
        got = self.load([{"waiting": [row("a", {MIDNIGHT: {SIGNUS}}, "2026-11-10 00:00:00"),
                                      row("a", {MIDNIGHT: set()}, "2026-11-12 00:00:00")]}])

        self.assertEqual(got[2]["a"], {})
        self.assertEqual(got[1]["listed"], 2, "行ごとに数える（従来どおり）")
        self.assertIn("a", got[2])

    def test_a_row_without_readable_bits_is_not_a_candidate(self):
        """読めた wish_bits の行が 1 つも無い行は、新しくても候補にしない（従来どおり）"""
        broken = row("a", {MIDNIGHT: "zz"}, "2026-12-01 00:00:00")
        got = self.load([{"waiting": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-11-10 00:00:00"), broken]}])

        self.assertEqual(got[2]["a"], {MIDNIGHT: {BAD_BATTER}})

    def test_a_missing_or_unreadable_created_at_is_the_oldest(self):
        """5: created_at が無い・読めない行は最も古い扱い"""
        for bad in (None, "", "きのう", 12345):
            with self.subTest(created_at=bad):
                got = self.load([{"waiting": [row("a", {MIDNIGHT: {SIGNUS}}, bad),
                                              row("a", {MIDNIGHT: {BAD_BATTER}}, "2000-01-01 00:00:00")]}])
                self.assertEqual(got[2]["a"], {MIDNIGHT: {BAD_BATTER}})
        entry = row("a", {MIDNIGHT: {SIGNUS}}, "x")
        del entry["created_at"]                       # キーごと無い
        got = self.load([{"waiting": [entry, row("a", {MIDNIGHT: {BAD_BATTER}}, "2000-01-01 00:00:00")]}])
        self.assertEqual(got[2]["a"], {MIDNIGHT: {BAD_BATTER}})

    def test_a_tie_takes_the_row_that_comes_first_in_the_file(self):
        """5: 同点（同じ created_at・どちらも無い）はファイルで先の行（タブ順・並び順）"""
        same = "2026-11-10 00:00:00"
        got = self.load([{"waiting": [row("a", {MIDNIGHT: {BAD_BATTER}}, same)]},
                         {"waiting": [row("a", {MIDNIGHT: {SIGNUS}}, same)]}])
        self.assertEqual(got[2]["a"], {MIDNIGHT: {BAD_BATTER}})

        got = self.load([{"waiting": [row("a", {MIDNIGHT: {SIGNUS}}, None),
                                      row("a", {MIDNIGHT: {BAD_BATTER}}, None)]}])
        self.assertEqual(got[2]["a"], {MIDNIGHT: {SIGNUS}})

    def test_the_time_is_compared_as_a_time_not_as_text(self):
        """日時として比べる（"T" 区切りでも）。文字列比べだと "2026-11-18T..." が "2026-11-18 ..." より新しく出る"""
        got = self.load([{"waiting": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18T08:00:00"),
                                      row("a", {MIDNIGHT: {SIGNUS}}, "2026-11-18 09:00:00")]}])

        self.assertEqual(got[2]["a"], {MIDNIGHT: {SIGNUS}})

    def test_other_names_are_chosen_independently(self):
        got = self.load([{"participants": [row("p", {MIDNIGHT: {BAD_BATTER}}, "2026-11-01 00:00:00")],
                          "waiting": [row("w", {MIDNIGHT: {SIGNUS}}, "2026-11-01 00:00:00"),
                                      row("w", {MIDNIGHT: {BAD_BATTER}}, "2026-11-02 00:00:00")]}])

        self.assertEqual(got[2], {"p": {MIDNIGHT: {BAD_BATTER}}, "w": {MIDNIGHT: {BAD_BATTER}}})

    def test_the_count_of_names_with_different_rows_is_reported_once(self):
        """6: 中身の違う行を持つ名前の人数を debug.log に 1 回。同じ中身の行だけの名前・1 行だけの名前は数えない"""
        tabs = [{"participants": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18 00:00:00"),
                                  row("same", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18 00:00:00"),
                                  row("alone", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18 00:00:00")],
                 "waiting": [row("a", {MIDNIGHT: {SIGNUS}}, "2026-10-18 00:00:00"),
                             row("b", {MIDNIGHT: {BAD_BATTER}}, "2026-10-18 00:00:00"),
                             row("b", {MIDNIGHT: set()}, "2026-10-19 00:00:00"),
                             row("same", {MIDNIGHT: {BAD_BATTER}}, "2026-10-18 00:00:00")]}]
        written = []

        with patch.object(DebugLog, "write", side_effect=written.append):
            self.load(tabs)
            self.load(tabs)             # 数秒ごとに読み直す。同じ人数は 2 回目から書かない

        lines = [m for m in written if "中身の違うリスト" in m]
        self.assertEqual(len(lines), 1, written)
        self.assertIn("2 人", lines[0])
        self.assertIn("参加者の行（無ければいちばん新しい待機の行）を使います", lines[0])
        for name in ("same", "alone"):
            self.assertNotIn(name, lines[0], "名前は書かない（人数だけ）")

    def test_nothing_is_reported_when_no_name_has_different_rows(self):
        written = []
        with patch.object(DebugLog, "write", side_effect=written.append):
            self.load([{"participants": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18 00:00:00")],
                        "waiting": [row("a", {MIDNIGHT: {BAD_BATTER}}, "2026-10-18 00:00:00")]}])

        self.assertEqual([m for m in written if "中身の違うリスト" in m], [])

    def test_the_host_own_list_is_unchanged(self):
        """4(仕様): user_save.json の主催者のリストは今までどおり wishes に入る"""
        user = self.dir / "user_save.json"
        user.write_text(json.dumps({"last_active": "host_test", "accounts": {
            "host_test": {"data": {MIDNIGHT: {str(BAD_BATTER): 1}}}}}), encoding="utf-8")
        self.write([{"participants": [row("a", {MIDNIGHT: {SIGNUS}}, "2026-11-18 00:00:00")]}])

        keep_on, meta, wishes = MatchTNL.load_host_local_state(self.bin, str(user))

        self.assertEqual(wishes["host_test"], {MIDNIGHT: {BAD_BATTER}})
        self.assertEqual(wishes["a"], {MIDNIGHT: {SIGNUS}})
        self.assertEqual(meta["host_self"], "host_test")


class TestEveryWindowJudgesByPresentNames(LocalStateTestCase):
    """7: public・その他のグループ・Emerald City も、タブではなく在室者の名前で判定する（依頼者 2026-11-20）"""

    TYPES = (config.INSTANCE_PUBLIC, config.INSTANCE_OTHER_GROUP, config.INSTANCE_EMERALD_CITY)

    def setUp(self):
        super().setUp()
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

    def _state(self):
        # タブ 0 = 参加者 x1（OTHER を希望）・待機 w1（T を希望）・待機 gone（THIRD を希望。その場にいない）。
        # タブ 1 = 参加者 o1・o2（OTHER を希望）。在室者 w1・o1・o2 は、名前の重なりではタブ 1 に対応づく
        # （そのタブには T が無い）。タブの対応づけを使うと T を自爆にしてしまう
        return self.load([
            {"participants": [row("x1", {ALT: {155}}, "2026-11-18 00:00:00")],
             "waiting": [row("w1", {ALT: {154}}, "2026-11-18 00:00:00"),
                         row("gone", {ALT: {150}}, "2026-11-18 00:00:00")]},
            {"participants": [row("o1", {ALT: {155}}, "2026-11-18 00:00:00"),
                              row("o2", {ALT: {155}}, "2026-11-18 00:00:00")]}])

    def _monitor(self, itype, state, present=("w1", "o1", "o2")):
        keep_on, meta, wishes = state
        tabs = {"version": 1, "tabs": meta["tabs_data"]}
        lists = MatchTNL.SharedLists(keep_on, wishes, None, tabs, None)
        cfg = WindowConfig(do_skip=True, voice_continue="continue.mp3", voice_list_lost="lost.mp3")
        monitor = LogMonitor.LogMonitor(cfg, None, lambda _m: None, window_idx=1, lists=lists)
        st = monitor.st
        st.instance_type = itype
        st.in_round = True
        st.round_type = "Alternate"
        st.local_player_name = "serim01"
        st.local_user_id = ME
        st.players = {ME}
        st.player_names = {ME: "serim01"}
        for n, name in enumerate(present):
            uid = f"usr_{n:04x}c"
            st.players.add(uid)
            st.player_names[uid] = name
        st.players_known = True
        st.terror_ids = []
        monitor._running = True
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._focus_for_freeze = MagicMock()
        return monitor

    @staticmethod
    def _judge(monitor, tid):
        """続行リストでの判定（_list_plan）。public 系の窓は _plan が「操作しない（restricted）」を返すので、
        リストの判定そのものを見る"""
        monitor.st.terror_ids = [tid]
        return LogMonitor.LogMonitor._outcome(monitor._list_plan([tid], False))

    def test_the_premise_the_tab_match_would_pick_the_wrong_tab(self):
        """前提: タブの対応づけ（今は判定が使わない）は別のインスタンスのタブ 1 を選び、そこには T が無い"""
        state = self._state()
        tabs_data = state[1]["tabs_data"]
        self.assertEqual(MatchTNL.tab_for_window(tabs_data, {"w1", "o1", "o2"}), 1)
        self.assertNotIn(T, tabs_data[1]["keepOn"][ALT])

    def test_a_present_name_in_the_waiting_section_counts_and_an_absent_tab_player_does_not(self):
        state = self._state()
        for itype in self.TYPES:
            monitor = self._monitor(itype, state)
            with self.subTest(itype=itype), \
                    patch.object(MatchTNL, "tab_for_window", side_effect=AssertionError("tab_for_window")), \
                    patch.object(MatchTNL, "tab_match_detail", side_effect=AssertionError("tab_match_detail")):
                self.assertEqual(self._judge(monitor, T), "continue", "在室者 w1（待機）の希望")
                self.assertEqual(self._judge(monitor, OTHER), "continue", "在室者 o1・o2 の希望")
                self.assertEqual(self._judge(monitor, THIRD), "skip", "その場にいない gone の希望は使わない")
                self.assertEqual(monitor._keep_on_base(), {ALT: {T, OTHER}})
                self.assertEqual(set(monitor._effective_wishes()), {"w1", "o1", "o2"})
                self.assertIs(monitor._tab_index, LogMonitor._UNSET, "タブへの対応づけを計算していない")
                self.assertEqual([m for m in monitor.logs if "[主催リスト]" in m], [], monitor.logs)

    def test_the_decision_basis_is_shown_in_every_window_type(self):
        state = self._state()
        for itype in self.TYPES:
            monitor = self._monitor(itype, state)
            with self.subTest(itype=itype):
                self.assertEqual(monitor._basis_label([T]), "（在室4人・リストあり3人・続行希望: w1）")
                self.assertEqual(monitor._basis_label([THIRD]), "（在室4人・リストあり3人）")

    def test_the_basis_is_appended_to_the_judgement_line_of_a_public_window(self):
        state = self._state()
        monitor = self._monitor(config.INSTANCE_PUBLIC, state)
        st = monitor.st
        st.terror_ids = [T]

        monitor._decide_with_keep_on_set(st.round_type, None)

        lines = [m for m in monitor.logs if "判定:" in m]
        self.assertEqual(len(lines), 1, monitor.logs)
        self.assertTrue(lines[0].endswith("【プレイ】（在室4人・リストあり3人・続行希望: w1）"), lines[0])

    def test_the_basis_is_not_shown_when_the_tnl_is_the_source(self):
        state = self._state()
        SharedState.set_list_source("tnl")
        monitor = self._monitor(config.INSTANCE_PUBLIC, state)

        self.assertEqual(monitor._basis_label([T]), "")

    def test_the_row_chosen_per_name_is_what_a_public_window_judges_with(self):
        """Alu's の形を窓まで通す: 参加者の行に Signus が無ければ、古い待機の行に Signus があっても自爆"""
        state = self.load([{"participants": [row("alu", {MIDNIGHT: {BAD_BATTER}}, "2026-11-18 17:59:04")],
                            "waiting": [row("alu", {MIDNIGHT: {BAD_BATTER, SIGNUS}}, "2026-10-18 09:00:00")]}])
        monitor = self._monitor(config.INSTANCE_PUBLIC, state, present=("alu",))
        monitor.st.round_type = "Midnight"

        self.assertNotIn(SIGNUS, monitor._keep_on_base().get(MIDNIGHT, ()))
        self.assertIn(BAD_BATTER, monitor._keep_on_base().get(MIDNIGHT, ()))


if __name__ == "__main__":
    unittest.main()
