from dataclasses import dataclass

import config
import MatchTNL


# ToN ListTool は Variant と Moon を Special/Moon の13枠にまとめて記録する。
# そこを見るのは Classic と Moon だけ。全ラウンドで見ると、Special/Moon にだけ
# 192(Bloodthirsty Creature) を入れている人の Fog などが誤って続行になる
# （192 は Classic 以外にも出る唯一の Variant）。
MOON_ROUND_TYPES = frozenset({
    "Mystic Moon", "Blood Moon", "Twilight", "Solstice",
})
SPECIAL_MOON_ROUNDS = frozenset({"Classic"}) | MOON_ROUND_TYPES


@dataclass(frozen=True)
class KillerDecision:
    is_open_special_round_target: bool
    is_continue_round: bool


def normalize_killer_ids(ids: list[int], round_type: str, state_round_type: str = "") -> list[int]:
    normalized = MatchTNL.apply_alternate_offset(ids, round_type)
    if state_round_type == "Unbound":
        normalized = [tid + MatchTNL.UNBOUND_OFFSET for tid in normalized]
    return normalized


def is_open_special_round_target(
    terror_ids: list[int],
    round_type: str,
    wins: int,
    cancel_afk: bool,
) -> bool:
    return (
        bool(terror_ids and config.OPEN_SPECIAL_ROUND_TERROR_IDS)
        and any(tid in config.OPEN_SPECIAL_ROUND_TERROR_IDS for tid in terror_ids)
        and round_type not in config.SPECIAL_ROUND
        and wins < config.OPEN_SPECIAL_ROUND_TARGET_WINS
        and cancel_afk
    )


def is_self_inserts_bloodthirsty(
    terror_ids: list[int],
    round_type: str,
    bloodthirsty_variant: bool,
) -> bool:
    """Unbound の Self Inserts に Bloodthirsty が出たか。

    ToN ListTool は「Bloodthirsty 入りの Self Inserts」を表現できない——
    リストに出せるのは `Self Inserts` の1枠だけ——ので、リストに頼らず
    必ず続行する。`Pack of Wild Yet Curious(265)` は対象外（あちらは
    リストで指定できる）。
    """
    return (
        bool(bloodthirsty_variant)
        and round_type == "Unbound"
        and config.SELF_INSERTS_ID in terror_ids
    )


def decide_killers(
    keep_on_set: dict,
    terror_ids: list[int],
    round_type: str,
    wins: int,
    cancel_afk: bool,
    bloodthirsty_variant: bool = False,
) -> KillerDecision:
    open_special = is_open_special_round_target(terror_ids, round_type, wins, cancel_afk)
    # リストで表現できない組み合わせ。3クラ解放とは別物なので、
    # KillerDecision.open_special には混ぜない（AFK解除が誤って走る）
    forced = is_self_inserts_bloodthirsty(terror_ids, round_type,
                                          bloodthirsty_variant)
    tnl_key = MatchTNL.LOG_TO_TNL.get(round_type, round_type)
    should_continue = MatchTNL.should_continue(keep_on_set, tnl_key, terror_ids)
    if not should_continue and round_type in SPECIAL_MOON_ROUNDS:
        # ラウンド別のキーに加えて Special/Moon 枠も見る。置き換えではない——
        # ID192(Bloodthirsty Creature) は Fog / Ghost / Midnight などラウンド別の
        # キーにも入っていて、寄せるとそちらの指定を取りこぼす
        should_continue = MatchTNL.should_continue(
            keep_on_set, MatchTNL.SPECIAL_MOON_KEY, terror_ids)
    should_continue = should_continue or open_special or forced
    return KillerDecision(open_special, should_continue)
