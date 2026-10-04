"""置き換えテラーの表と、その合図のログ行。

Killers 行のIDのまま確定しないテラーがある。別のログ行（合図）が来て初めて
正体が分かる。実測では合図は Killers 行の**後**に来る（Gigabytes 12/12件、
Atrached 3/3件）。ここに1か所でまとめ、待つか・どう差し替えるかを表で引く。

- SIGNALS: 合図のログ行 → WindowState の属性（flag）。LogParser がこれで行を見分ける
- TABLE: flag が立ったときに、どのIDをどう差し替えるか

新しい置き換えを足すときは、config に番号、SIGNALS に合図の行、TABLE に差し替えを足す。
WindowState に flag の属性も要る（ラウンドごとに落とす）。
"""
import re
from dataclasses import dataclass

import config
import ReadJson


@dataclass(frozen=True)
class Signal:
    """置き換えの合図のログ行"""
    flag: str                   # 立てる WindowState の属性（TABLE の flag と同じ）
    pattern: re.Pattern         # 行頭の時刻などを外した行に当てる
    sample: str                 # 実物の行（テストで流す見本）
    announce: str               # 公開ログに出す1行


SIGNALS: tuple[Signal, ...] = (
    Signal("atrached_variant", re.compile(r"^Lets play a game[.][.][.]$"),
           "Lets play a game...", "🎮 Atrached 出現（Sonic の Variant）"),
    Signal("hungry_home_invader_variant",
           re.compile(r"^I hear strange sounds coming from the kitchen[.]$"),
           "I hear strange sounds coming from the kitchen.",
           "🏠 Hungry Home Invader 出現（Slender の Variant）"),
    # Curious の Bloodthirsty 化。Unbound の Self Inserts の中の Curious も同じ行
    Signal("bloodthirsty_creature_variant",
           re.compile(r"^The creature is bloodthirsty today[.][.][.]$"),
           "The creature is bloodthirsty today...",
           "🩸 Bloodthirsty 出現（Curious の Variant）"),
    # 行のどこかに出る。大文字小文字も問わない
    Signal("foxy", re.compile(r"foxy the pirate turned evil!", re.IGNORECASE),
           "foxy the pirate turned evil!", "🦊 Foxyが出た！"),
    # この行はまだ実ログで観測できていない（低確率で、手元のログ22本には0件）。
    # 正確な大文字小文字と句点が分からないので緩く受ける。実物が取れたら締める
    Signal("glorbo", re.compile(r"^the real g has appeared[.]?$", re.IGNORECASE),
           "the real g has appeared", "🫠 Glorbo 出現（Arkus の Variant）"),
    # テラーIDでは判別できない（実測でIDが毎回異なる）。この行だけが手がかり。
    # Killers have been set と同じ秒に出る
    Signal("gigabytes", re.compile(r"^The Gigabytes have come[.]$"),
           "The Gigabytes have come.", "👾 The Gigabytes 出現"),
)


def match_signal(line: str) -> Signal | None:
    """合図の行なら、その Signal（行頭の時刻などは外してから渡す）"""
    for row in SIGNALS:
        if row.pattern.search(line):        # ^ で始まるものは行頭からの一致と同じ
            return row
    return None


def signal(flag: str) -> Signal:
    """flag の合図（テスト・呼び出し側の見本用）。無ければ KeyError"""
    for row in SIGNALS:
        if row.flag == flag:
            return row
    raise KeyError(flag)


@dataclass(frozen=True)
class Replacement:
    name: str
    # 元のID。None は「元IDを問わない」（Gigabytes は元IDが毎回違う）
    source: int | None
    # 置き換え後のID。source と同じなら差し替えず、フラグだけが意味を持つ
    target: int | None
    # 起きるラウンド。None なら全ラウンド
    rounds: frozenset | None
    # 合図のログを見たら立てる WindowState の属性
    flag: str
    # 合図の行が SIGNALS に入っているか。分からない間は False
    wired: bool = True
    # target が分からないとき、terrors.json から名前で引く
    target_name: str = ""

    def target_id(self) -> int | None:
        if self.target is not None:
            return self.target
        if not self.target_name:
            return None
        return ReadJson.terror_id_by_name(self.target_name, config.TERRORS)

    @property
    def enabled(self) -> bool:
        """IDと合図の両方が分かっているときだけ使う"""
        return self.wired and self.target_id() is not None

    @property
    def changes_id(self) -> bool:
        return self.source is None or self.source != self.target_id()

    def matches_round(self, round_type: str) -> bool:
        return self.rounds is None or round_type in self.rounds

    def could_apply(self, ids, round_type: str) -> bool:
        """このテラー構成・ラウンドで起こりうるか（合図の有無は見ない）"""
        if not self.enabled or not self.matches_round(round_type):
            return False
        if self.source is None:
            return len(ids) == 1
        return self.source in ids

    def apply(self, ids: list[int]) -> list[int]:
        target = self.target_id()
        if self.source is None:
            return [target] if ids else list(ids)
        return [target if tid == self.source else tid for tid in ids]


CLASSIC = frozenset({"Classic"})

# 並び順に意味がある。Gigabytes はテラー構成ごと差し替えるので最後
TABLE: tuple[Replacement, ...] = (
    Replacement("Atrached", config.SONIC_ID, config.ATRACHED_ID, CLASSIC,
                "atrached_variant"),
    Replacement("Hungry Home Invader", config.SLENDER_ID,
                config.HUNGRY_HOME_INVADER_ID, CLASSIC,
                "hungry_home_invader_variant"),
    # 全ラウンドで起きる（Bloodbath なども）
    Replacement("Wild Yet Bloodthirsty Creature", config.CURIOUS_CREATURE_ID,
                config.BLOODTHIRSTY_CREATURE_ID, None,
                "bloodthirsty_creature_variant"),
    # IDは変わらない。中の Curious が Bloodthirsty 化するとリストを見ずに
    # 続行になる（RoundDecision.is_self_inserts_bloodthirsty）ので、合図を
    # 待つ理由は上と同じ。合図のログも同じ行
    Replacement("Self Inserts (Bloodthirsty)", config.SELF_INSERTS_ID,
                config.SELF_INSERTS_ID, frozenset({"Unbound"}),
                "bloodthirsty_creature_variant"),
    # alternate のIDはほかのカテゴリと重ならないので、ラウンドを絞らなくても
    # オルタ枠でしか起きない
    Replacement("Foxy", config.SANIC_ID, config.FOXY_ID, None, "foxy"),
    # 枠だけ。ID（terrors.json）と合図のログ（LogParser）が分かるまで無効。
    # 有効にするには terrors.json に "Neo Pilot" を足し、合図の行を LogParser に
    # SIGNALS に足して neo_pilot を立てるようにしてから wired=True にする
    Replacement("Neo Pilot", config.FUSION_PILOT_ID, None, None, "neo_pilot",
                wired=False, target_name="Neo Pilot"),
    # Punished の Sewers で、Arkus が低確率で Glorbo になる。マップは条件に
    # 入れない（表に map の欄が無く、合図が来た時点で確定する）。ほかのマップの
    # Punished + Arkus では 0.3 秒の変種待ちが入るだけ
    Replacement("Glorbo", config.ARKUS_ID, config.GLORBO_ID,
                frozenset({"Punished"}), "glorbo"),
    Replacement("The Gigabytes", None, config.GIGABYTES_ID, CLASSIC,
                "gigabytes"),
)


def rows_for_flag(flag: str) -> list[Replacement]:
    return [r for r in TABLE if r.flag == flag and r.enabled]


def flags() -> set[str]:
    """ラウンドごとに落とす属性（無効な行のぶんも含める）"""
    return {r.flag for r in TABLE}
