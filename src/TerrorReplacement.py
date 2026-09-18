"""置き換えテラーの表。

Killers 行のIDのまま確定しないテラーがある。別のログ行（合図）が来て初めて
正体が分かる。実測では合図は Killers 行の**後**に来る（Gigabytes 12/12件、
Atrached 3/3件）。ここに1か所でまとめ、待つか・どう差し替えるかを表で引く。
"""
from dataclasses import dataclass

import config
import ReadJson


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
    # 合図のログが LogParser に入っているか。分からない間は False
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
    # 入れて neo_pilot を立てるようにしてから wired=True にする
    Replacement("Neo Pilot", config.FUSION_PILOT_ID, None, None, "neo_pilot",
                wired=False, target_name="Neo Pilot"),
    Replacement("The Gigabytes", None, config.GIGABYTES_ID, CLASSIC,
                "gigabytes"),
)


def rows_for_flag(flag: str) -> list[Replacement]:
    return [r for r in TABLE if r.flag == flag and r.enabled]


def flags() -> set[str]:
    """ラウンドごとに落とす属性（無効な行のぶんも含める）"""
    return {r.flag for r in TABLE}
