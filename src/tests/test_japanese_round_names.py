"""日本語表示の ToN: ラウンドの種類だけが訳されて出る。英語へ直して読む（MatchTNL.JP_ROUND_TYPES）"""
from tests.support import *  # noqa: F401,F403

import RoundDecision

# ListTool の言い方が変わったら判定が黙って変わる。組み立てた結果を丸ごと書き写してある
EXPECTED_JP_ROUND_TYPES = {
    "8ページ": "8 Pages",
    "Classic.exe": "Classic.exe",
    "Randomizer": "Randomizer",
    "アンバウンド": "Unbound",
    "オルタネイト": "Alternate",
    "クラシック": "Classic",
    "コールドナイト": "Cold Night",
    "ゴースト": "Ghost",
    "サボタージュ": "Sabotage",
    "サボタージュスター": "Sabotage star",
    "サボタージュマーダー": "Sabotage murder",
    "スペシャル": "Special",
    "ソルスティス": "Solstice",
    "ダブルトラブル": "Double Trouble",
    "トワイライト": "Twilight",
    "パニッシュ": "Punished",
    "ブラッドバス": "Bloodbath",
    "ブラッドバスEX": "Bloodbath EX",
    "ブラッドムーン": "Blood Moon",
    "ミスティックムーン": "Mystic Moon",
    "ミッドナイト": "Midnight",
    "ランダマイザー": "Randomizer",
    "狂気": "Cracked",
    "走れ！": "Run",
    "霧": "Fog",
}

START = "This round is taking place at Facility (12) and the round type is {}"
SET = "Killers have been set - 1 2 3 // Round type is {}"
UNKNOWN = "Killers is unknown - ??? // Facility // Round type is {}"
REVEALED = "Killers have been revealed - 1 2 3 // Round type is {}"
JOINING = "[Behaviour] Joining wrld_xxx:12345~region(jp)"


class JapaneseBase(unittest.TestCase):
    def setUp(self):
        # japanese_seen はプロセス全体の状態。立てたまま終わると後のテストへ漏れる
        self.addCleanup(LogParser.forget_japanese)
        LogParser.forget_japanese()


class TestBuiltTable(JapaneseBase):
    def test_the_whole_table_is_written_out(self):
        self.assertEqual(MatchTNL.JP_ROUND_TYPES, EXPECTED_JP_ROUND_TYPES)
        self.assertEqual(len(MatchTNL.JP_ROUND_TYPES), 25)

    def test_only_randomizer_is_reached_from_two_names(self):
        values = list(MatchTNL.JP_ROUND_TYPES.values())
        duplicated = sorted({v for v in values if values.count(v) > 1})
        self.assertEqual(duplicated, ["Randomizer"])

    def test_moon_and_alternate_keys_are_not_in_the_table(self):
        self.assertNotIn("Moon", MatchTNL.JP_ROUND_TYPES)
        for english in MatchTNL.JP_ROUND_TYPES.values():
            self.assertNotIn(MatchTNL.ALTERNATE_SUFFIX, english)

    def test_suffix_constant_is_shared_with_round_decision(self):
        self.assertEqual(RoundDecision.ALTERNATE_SUFFIX, MatchTNL.ALTERNATE_SUFFIX)
        self.assertEqual(MatchTNL.ALTERNATE_SUFFIX, " (Alternate)")


class TestParseJapanese(JapaneseBase):
    def test_all_names_come_back_in_english_at_the_four_entrances(self):
        for japanese, english in EXPECTED_JP_ROUND_TYPES.items():
            for template in (START, SET, UNKNOWN, REVEALED):
                with self.subTest(japanese=japanese, template=template[:12]):
                    event = LogParser.parse(template.format(japanese))
                    self.assertEqual(event.round_type, english)

    def test_alternate_suffix_is_kept(self):
        self.assertEqual(LogParser.parse(SET.format("ゴースト (Alternate)")).round_type, "Ghost (Alternate)")
        self.assertEqual(LogParser.parse(REVEALED.format("霧 (Alternate)")).round_type, "Fog (Alternate)")
        self.assertEqual(LogParser.parse(UNKNOWN.format("霧 (Alternate)")).round_type, "Fog (Alternate)")

    def test_english_names_do_not_change(self):
        for english in set(EXPECTED_JP_ROUND_TYPES.values()):
            with self.subTest(english=english):
                self.assertEqual(LogParser.parse(START.format(english)).round_type, english)
                self.assertEqual(LogParser.parse(SET.format(english + " (Alternate)")).round_type,
                                 english + " (Alternate)")

    def test_ex_alias_still_works(self):
        self.assertEqual(LogParser.parse(START.format("EX")).round_type, "Bloodbath EX")
        self.assertEqual(LogParser.parse(SET.format("EX")).round_type, "Bloodbath EX")

    def test_unknown_names_pass_through(self):
        for name in ("ほげ", "NewRound", "ほげ (Alternate)"):
            with self.subTest(name=name):
                self.assertEqual(LogParser.parse(START.format(name)).round_type, name)

    def test_bloodbath_with_three_same_numbers_is_ex(self):
        event = LogParser.parse("Killers have been set - 50 50 50 // Round type is ブラッドバス")
        self.assertEqual(event.round_type, "Bloodbath EX")
        normal = LogParser.parse("Killers have been set - 50 50 7 // Round type is ブラッドバス")
        self.assertEqual(normal.round_type, "Bloodbath")

    def test_japanese_pages_reach_the_all_continue_check(self):
        round_type = LogParser.parse(START.format("8ページ")).round_type
        self.assertEqual(RoundDecision.selectable_round_name(round_type), "8 Pages")
        alt = LogParser.parse(SET.format("ゴースト (Alternate)")).round_type
        self.assertEqual(RoundDecision.selectable_round_name(alt), "Ghost")

    def test_japanese_pages_are_an_all_continue_round_in_the_monitor(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(continue_rounds={"8 Pages"}), {},
                                        lambda _m: None, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_PRIVATE
        monitor.st.round_type = LogParser.parse(START.format("8ページ")).round_type
        self.assertTrue(monitor._is_all_continue_round())


class TestJapaneseSeen(JapaneseBase):
    def test_false_at_first(self):
        self.assertFalse(LogParser.japanese_seen())

    def test_japanese_name_raises_it(self):
        LogParser.parse(START.format("クラシック"))
        self.assertTrue(LogParser.japanese_seen())

    def test_japanese_in_each_entrance_raises_it(self):
        for template in (START, SET, UNKNOWN, REVEALED):
            with self.subTest(template=template[:12]):
                LogParser.forget_japanese()
                LogParser.parse(template.format("霧"))
                self.assertTrue(LogParser.japanese_seen())

    def test_english_names_do_not_raise_it(self):
        for english in sorted(set(EXPECTED_JP_ROUND_TYPES.values())) + ["EX", "ほげ"]:
            with self.subTest(english=english):
                LogParser.forget_japanese()
                LogParser.parse(START.format(english))
                LogParser.parse(SET.format(english))
                LogParser.parse(SET.format(english + " (Alternate)"))
                self.assertFalse(LogParser.japanese_seen())

    def test_run_is_never_evidence(self):
        LogParser.parse(START.format("Run"))
        self.assertFalse(LogParser.japanese_seen())

    def test_same_in_both_languages_is_never_evidence(self):
        for name in ("Classic.exe", "Randomizer"):
            with self.subTest(name=name):
                LogParser.parse(START.format(name))
                self.assertFalse(LogParser.japanese_seen())

    def test_katakana_randomizer_is_evidence(self):
        LogParser.parse(START.format("ランダマイザー"))
        self.assertTrue(LogParser.japanese_seen())

    def test_forget_lowers_it(self):
        LogParser.parse(START.format("霧"))
        LogParser.forget_japanese()
        self.assertFalse(LogParser.japanese_seen())

    def test_english_after_japanese_does_not_lower_it(self):
        LogParser.parse(START.format("霧"))
        LogParser.parse(START.format("Fog"))
        self.assertTrue(LogParser.japanese_seen())


class TestAnnounce(JapaneseBase):
    def _monitor(self):
        messages = []
        monitor = LogMonitor.LogMonitor(WindowConfig(), {}, messages.append, window_idx=1)
        monitor.st.instance_type = config.INSTANCE_HOSHIIMO
        return monitor, messages

    def _feed(self, monitor, line):
        with patch.object(LogMonitor.threading, "Thread"), \
             patch.object(PlaySound, "play_sound"), \
             patch.object(ConnectDB, "register_round"):
            monitor._process(line)

    def _said(self, messages):
        return [m for m in messages if "処理を日本語用に変更しました" in m]

    def test_said_once_per_window_over_two_rounds(self):
        monitor, messages = self._monitor()
        self._feed(monitor, START.format("クラシック"))
        self._feed(monitor, "RoundOver")
        self._feed(monitor, START.format("ブラッドバス"))
        self._feed(monitor, SET.format("ブラッドバス"))
        self.assertEqual(len(self._said(messages)), 1)
        self.assertIn("[窓1]", self._said(messages)[0])

    def test_each_window_says_it_once(self):
        first, first_messages = self._monitor()
        second, second_messages = self._monitor()
        self._feed(first, START.format("クラシック"))
        self._feed(second, START.format("クラシック"))
        self._feed(second, SET.format("クラシック"))
        self.assertEqual(len(self._said(first_messages)), 1)
        self.assertEqual(len(self._said(second_messages)), 1)

    def test_not_said_in_english(self):
        monitor, messages = self._monitor()
        self._feed(monitor, START.format("Classic"))
        self._feed(monitor, START.format("Run"))
        self.assertEqual(self._said(messages), [])

    def test_said_from_a_killers_line_too(self):
        for template in (SET, UNKNOWN, REVEALED):
            with self.subTest(template=template[:12]):
                LogParser.forget_japanese()
                monitor, messages = self._monitor()
                self._feed(monitor, template.format("霧"))
                self.assertEqual(len(self._said(messages)), 1)

    def test_joining_again_lowers_the_state_and_says_it_again(self):
        monitor, messages = self._monitor()
        self._feed(monitor, START.format("クラシック"))
        self.assertTrue(LogParser.japanese_seen())

        self._feed(monitor, JOINING)
        self.assertFalse(LogParser.japanese_seen())

        self._feed(monitor, START.format("クラシック"))
        self.assertEqual(len(self._said(messages)), 2)

    def test_english_after_rejoin_stays_silent(self):
        monitor, messages = self._monitor()
        self._feed(monitor, START.format("クラシック"))
        self._feed(monitor, JOINING)
        self._feed(monitor, START.format("Classic"))
        self.assertFalse(LogParser.japanese_seen())
        self.assertEqual(len(self._said(messages)), 1)


if __name__ == "__main__":
    unittest.main()
