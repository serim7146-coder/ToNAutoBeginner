from dataclasses import dataclass

import config
import ItemCatalog
import MatchTNL
import TerrorIds


@dataclass(frozen=True)
class KillerDecision:
    is_open_special_round_target: bool
    is_continue_round: bool


ALTERNATE_SUFFIX = " (Alternate)"


def selectable_round_name(round_type: str) -> str:
    """ラウンドの種類 → 選べる名前（全続行・自爆するラウンドのチェックボックスの名前）。
    Killers の行の種類は「Ghost (Alternate)」のように (Alternate) が付く。付ける前の名前が
    config.SKIP_ROUND_SELECTABLE にあるもの（Fog・Ghost・8 Pages）だけ外す（依頼者 2026-10-10:
    Ghost を選んだら Ghost (Alternate) にも効かせる）。ほか（単独の Alternate など）はそのまま"""
    if round_type and round_type.endswith(ALTERNATE_SUFFIX):
        base = round_type[:-len(ALTERNATE_SUFFIX)]
        if base in config.SKIP_ROUND_SELECTABLE:
            return base
    return round_type


def normalize_killer_ids(ids: list[int], round_type: str, state_round_type: str = "") -> list[int]:
    normalized = MatchTNL.apply_alternate_offset(ids, round_type)
    if state_round_type == "Unbound":
        normalized = [tid + MatchTNL.UNBOUND_OFFSET for tid in normalized]
    return normalized


def open_special_active(wins: int, after_unlock: bool = False) -> bool:
    """DTM/Waldo を続行する時期か（3勝前。窓の設定で3勝の後も）"""
    return after_unlock or wins < config.OPEN_SPECIAL_ROUND_TARGET_WINS


def open_special_round_type(round_type: str) -> bool:
    """DTM/Waldo を続行してよいラウンドか。テラーが複数体出るラウンドだけは続行しない
    （Double Trouble・Bloodbath・Midnight。Bloodbath EX は続行）"""
    return round_type not in config.OPEN_SPECIAL_ROUND_EXCLUDED_ROUNDS


def guidance_plush_id() -> int | None:
    """Guidance Plush の番号（item.json から名前で。無ければ config の番号。どちらも無ければ None）。
    Waldo の続行と、完全放置モードのアイテム自動取得の条件"""
    return (ItemCatalog.item_id_by_name(config.GUIDANCE_PLUSH_NAME, config.ITEMS)
            or config.GUIDANCE_PLUSH_ITEM_ID)


def open_special_ids(terror_ids, holds_plush: bool | None = None) -> list[int]:
    """続行してよい DTM/Waldo。Waldo は Guidance Plush を持っているときだけ。
    holds_plush が None（持っているか分からない）なら前と同じく続行する"""
    return [tid for tid in terror_ids or ()
            if tid in config.OPEN_SPECIAL_ROUND_TERROR_IDS
            and not (tid == config.WALDO_ID and holds_plush is False)]


def is_open_special_round_target(
    terror_ids: list[int],
    round_type: str,
    wins: int,
    cancel_afk: bool,
    after_unlock: bool = False,
    holds_plush: bool | None = None,
) -> bool:
    return (
        bool(open_special_ids(terror_ids, holds_plush))
        and open_special_round_type(round_type)
        and open_special_active(wins, after_unlock)
        and cancel_afk
    )


def is_self_inserts_bloodthirsty(
    terror_ids: list[int],
    round_type: str,
    bloodthirsty_variant: bool,
    table_disabled: bool = False,
) -> bool:
    """Unbound の Self Inserts に Bloodthirsty が出たか。

    ToN ListTool は「Bloodthirsty 入りの Self Inserts」を表現できない——
    リストに出せるのは `Self Inserts` の1枠だけ——ので、リストに頼らず
    必ず続行する。`Pack of Wild Yet Curious(265)` は対象外（あちらは
    リストで指定できる）。

    table_disabled: 続行表の続行無効に Self Inserts (Bloodthirsty) の仮の番号があるとき True
    （呼ぶ側が決める）。このときは必ず続行せず、普通のリストの判定に任せる。
    """
    return (
        bool(bloodthirsty_variant)
        and not table_disabled
        and round_type == "Unbound"
        and config.SELF_INSERTS_ID in terror_ids
    )


def list_continues(keep_on_set: dict, terror_ids: list[int], round_type: str) -> bool:
    """続行リストだけで見て、このテラー構成を続行するか（3クラ解放・Self Inserts は含めない）。
    ラウンドのキーだけを見る（Special/Moon のラウンドは Special/Moon のキー。Classic などが Special/Moon を
    予備に見ることはもう無い——ListTool の Special/Moon は読み込むときに置き場所のキーへ配ってある）"""
    tnl_key = MatchTNL.LOG_TO_TNL.get(round_type, round_type)
    # TEMP: 流れの中の番号がまだ今の番号の間（独自の番号になるまで）は、リストが独自の番号なので直して比べる
    eight_pages = round_type == TerrorIds.EIGHT_PAGES_ROUND
    own = [t for t in (TerrorIds.own_from_legacy(x, eight_pages) for x in terror_ids) if t is not None]
    return MatchTNL.should_continue(keep_on_set, tnl_key, own)


def decide_killers(
    keep_on_set: dict,
    terror_ids: list[int],
    round_type: str,
    wins: int,
    cancel_afk: bool,
    bloodthirsty_variant: bool = False,
    after_unlock: bool = False,
    holds_plush: bool | None = None,
    self_inserts_table_disabled: bool = False,
) -> KillerDecision:
    open_special = is_open_special_round_target(terror_ids, round_type, wins, cancel_afk,
                                                after_unlock, holds_plush)
    # リストで表現できない組み合わせ。3クラ解放とは別物なので、
    # KillerDecision.open_special には混ぜない（AFK解除が誤って走る）
    forced = is_self_inserts_bloodthirsty(terror_ids, round_type,
                                          bloodthirsty_variant,
                                          self_inserts_table_disabled)
    should_continue = (list_continues(keep_on_set, terror_ids, round_type)
                       or open_special or forced)
    return KillerDecision(open_special, should_continue)
