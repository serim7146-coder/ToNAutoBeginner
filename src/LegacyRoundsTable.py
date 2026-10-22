"""無印 ToNRounds の一時的な取り込み。

無印を消したら、このファイルと RoundStore.sources() の 1 行を消す（そのとき、無印の取り込みが 404 などで失敗する
間は v2 の分だけで動くようにしてあるので、消す前に止めても統計は壊れない）。

新しい版は DB v2（ToNRounds_v2。独自の番号・人数つき）へ送る。古い版の人は 3 日ほど無印（ToNRounds。前の番号・
人数なし）へ送り続けるので、統計の取り込みは一時的に両方を見る。無印の行は、ここでツールの独自の番号に直し
（8 Pages のラウンドは list_id → 3000＋ページ）、人数は NULL にして、v2 の行と同じ形にしてから手元へ入れる。
"""
import ConnectDB
import TerrorIds

NAME = "legacy"             # 手元の meta の鍵の添え字（initial_done:legacy など）
TABLE = "ToNRounds"


def fetch(since, my_uids):
    return ConnectDB.fetch_rounds(since, my_uids, table=TABLE)


def convert(row: dict) -> dict:
    """無印の 1 行 → v2 と同じ形（独自の番号・人数 NULL）。変換できない番号は NULL"""
    round_id = row.get("round")
    converted = {f"terror{i}": TerrorIds.own_from_legacy_round(row.get(f"terror{i}"), round_id)
                 for i in (1, 2, 3)}
    return {**row, **converted, "players": None}
