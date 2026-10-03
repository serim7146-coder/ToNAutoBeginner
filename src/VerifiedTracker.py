"""`Verified` の行が Begin の受理か、定期シグナルかを見分ける（純粋なクラス）。

`Verified` は Begin の受理のほかに、定期シグナルでも出る（中身は同じ）。
手元のログ全部で調べた規則（2026-09-30。確信度: 高）:
- 定期は前回の定期から 300秒後に出る
- その時点がラウンド中なら、そのラウンドの RoundOver の0〜1秒後まで遅れて出て、
  次はそこから300秒後（定期 4,294件のうち 300秒ちょうど 1,402件・RoundOver 直後 2,254件）
- 本物の受理は、ほぼ必ずラウンド開始の12〜13秒前（26,442件中26,436件）
- ラウンド中の Verified は全ログで1回だけ

時刻はすべてログの時刻（壁時計ではない。ログの追いつきや負荷で処理が遅れても
結果が変わらないように）。LogMonitor 以外に依存しない（技術選定側がログ全体に
当てて確かめられるように）。
"""
import config

BEGIN = "begin"
PERIODIC = "periodic"
IGNORE = "ignore"


class VerifiedTracker:
    def __init__(self):
        self.last_periodic = None       # 最後の定期の時刻（ログの時刻）
        self._in_round = False
        self._round_over_at = None      # 直近の RoundOver の時刻（Verified Round End まで）
        self._round_end_seen = False    # Verified Round End の後か（Begin を押せる）

    def on_round_start(self, t):
        self._in_round = True
        self._round_over_at = None
        self._round_end_seen = False

    def on_round_over(self, t):
        self._in_round = False
        self._round_over_at = t

    def on_round_end_verified(self, t):
        self._round_end_seen = True

    def on_begin_not_followed(self, t):
        """受理したのに15秒以内にラウンドが始まらなかった → その時刻は定期だった"""
        self.last_periodic = t

    def mark_periodic(self, t):
        """呼び出し側が定期と分かった（ツールが押していないのに来た Verified。CO）→ 位相の材料にする"""
        self.last_periodic = t

    def on_verified(self, t, pressed_recently: bool) -> str:
        """"begin" / "periodic" / "ignore" を返す。pressed_recently はツールが直前に
        Begin を押したか（予定と重なった1回を見分けるのに使う）"""
        if self._in_round:
            return IGNORE                   # 位相も動かさない
        due = (None if self.last_periodic is None
               else self.last_periodic + config.VERIFIED_PERIODIC_SEC)
        over = self._round_over_at
        if (not self._round_end_seen and over is not None and t is not None
                and 0 <= t - over <= config.VERIFIED_AFTER_OVER_SEC):
            # RoundOver 直後: ラウンド中に予定を迎えていたら、遅れて出た定期
            if due is None or due <= over:
                self.last_periodic = t
                return PERIODIC
            return IGNORE
        if due is not None and t is not None and abs(t - due) <= config.VERIFIED_PERIODIC_TOL_SEC:
            # 予定と重なった1回。押した直後なら、定期と受理が1行にまとまったとみなす
            self.last_periodic = t
            return BEGIN if pressed_recently else PERIODIC
        return BEGIN if self._round_end_seen else IGNORE
