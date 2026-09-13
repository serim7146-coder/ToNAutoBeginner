"""干し芋/焼き芋インスタンスのラウンド判定。

private（私用）インスタンスの判定には一切関わらない。呼び出し側で
インスタンス種別を確かめてから使うこと。

副作用なし。入力（インスタンス種別・ラウンド種別・テラーID・選出者名・
参加者別の続行希望・moonの解放状況）だけで決まる純粋関数。
"""

import config
import MatchTNL

# 判定結果
CONTINUE = "continue"   # 全続行 = 自爆しないだけ。続行アナウンスもフリーズもしない
SKIP     = "skip"       # 問答無用スキップ（グループ自動自爆）
NORMAL   = "normal"     # 通常判定（RoundDecision.decide_killers）へ委譲

# 問答無用スキップ（バリアント例外なし）
ALWAYS_SKIP_ROUNDS = frozenset({"Bloodbath", "Classic.exe", "Randomizer"})

# 全続行（自爆しないだけ）
ALWAYS_CONTINUE_ROUNDS = frozenset({"8 Pages", "Run"})

# 焼き芋では1回目でもスキップするmoon
YAKIIMO_SKIP_FIRST_MOONS = frozenset({"Mystic Moon", "Solstice"})

MOONS = frozenset({"Mystic Moon", "Blood Moon", "Twilight", "Solstice"})

SABOTAGE_STAR_KEY   = MatchTNL.LOG_TO_TNL["Sabotage star"]
SABOTAGE_MURDER_KEY = MatchTNL.LOG_TO_TNL["Sabotage murder"]

# 焼き芋 Fog でオルタネイト枠とみなす、Killers 行の round_type
FOG_ALTERNATE_ROUND_TYPE = "Fog (Alternate)"

# Foxy が出ると LogMonitor の EVENT_FOXY ハンドラが st.round_type を
# "Fog (Alternate)" に書き換えてから _on_killers を呼ぶ。両方を Fog 行として
# 扱わないと、その場合だけ Fog のルールから外れる
FOG_ROUND_TYPES = frozenset({"Fog", FOG_ALTERNATE_ROUND_TYPE})

GROUP_INSTANCES = frozenset({config.INSTANCE_HOSHIIMO, config.INSTANCE_YAKIIMO})


def is_variant(terror_ids) -> bool:
    """Classicで通常判定に回すバリアントテラーがいるか"""
    return any(tid in config.VARIANT_TERROR_IDS for tid in terror_ids)


def is_fog_alternate(killers_round_type: str) -> bool:
    """オルタネイト枠のFogか。

    `apply_alternate_offset()` が round_type を見て +134 しているので、
    ID範囲(134〜169)で見ても同じ情報になる。直接的なほうを使う。
    """
    return killers_round_type == FOG_ALTERNATE_ROUND_TYPE


def decide(
    instance_type: str,
    round_type: str,
    terror_ids,
    *,
    killers_round_type: str = "",
    moon_repeat: bool = False,
    sus_players=(),
    host_wishes: dict | None = None,
    follow_host: bool = False,
) -> str:
    """CONTINUE / SKIP / NORMAL のいずれかを返す。

    `killers_round_type` は `Killers have been set/revealed` 行に出るラウンド種別。
    焼き芋 Fog のオルタネイト判定に使う。
    `moon_repeat` はこのmoonが2回目以降か（`RoundSequence.is_moon_repeat()`）。
    `sus_players` は Sabotage で選出されたマーダーの表示名。
    `host_wishes` は `{vrc_name: {round_key: set(ids)}}`。
    `follow_host` が False のときは参加者別の情報が無いので Sabotage は
    従来どおり通常判定へ落とす。
    """
    if instance_type not in GROUP_INSTANCES:
        return NORMAL

    if round_type == "Classic":
        # バリアント確定を待たずに呼ぶと取り逃がす。呼び出し側で待つこと
        return NORMAL if is_variant(terror_ids) else SKIP

    if round_type in ALWAYS_SKIP_ROUNDS:
        return SKIP

    if round_type in ALWAYS_CONTINUE_ROUNDS:
        return CONTINUE

    if round_type in MOONS:
        if moon_repeat:
            return SKIP
        if (instance_type == config.INSTANCE_YAKIIMO
                and round_type in YAKIIMO_SKIP_FIRST_MOONS):
            return SKIP
        return CONTINUE

    if round_type in FOG_ROUND_TYPES:
        if instance_type == config.INSTANCE_HOSHIIMO:
            return CONTINUE
        if not killers_round_type:
            # Killers行を見ていない。判定材料が無いときは自爆しない側へ倒す
            # （通常の経路では `Killers is unknown` のまま _on_killers を
            # 通らないので、ここには来ない）
            return CONTINUE
        # 焼き芋: オルタネイト枠のFogだけが対象。あとは続行リスト次第
        return NORMAL if is_fog_alternate(killers_round_type) else SKIP

    if round_type == "Sabotage":
        return _decide_sabotage(instance_type, terror_ids, sus_players,
                                host_wishes, follow_host)

    return NORMAL


def _decide_sabotage(instance_type, terror_ids, sus_players, host_wishes,
                     follow_host) -> str:
    if not follow_host or not host_wishes:
        # 参加者別の希望が無い。誰の希望かを分けられないので通常判定に落とす
        return NORMAL

    murderers = set(sus_players)
    ids = set(terror_ids)

    if instance_type == config.INSTANCE_YAKIIMO:
        # 選出されたマーダーがマーダー枠で続行を希望している → 自爆
        # （主催リストに載っていないマーダーは「希望なし」扱い）
        for name in murderers:
            if ids & host_wishes.get(name, {}).get(SABOTAGE_MURDER_KEY, set()):
                return SKIP

    # マーダー以外の参加者が star 枠で続行を希望している → 続行。
    # マーダー本人が star を持っていてもここでは数えない
    for name, wishes in host_wishes.items():
        if name in murderers:
            continue
        if ids & wishes.get(SABOTAGE_STAR_KEY, set()):
            return CONTINUE

    return SKIP
