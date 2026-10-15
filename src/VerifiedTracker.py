"""`Verified` の行が Begin の受理か、定期シグナルかを見分ける（純粋なクラス）。

`Verified` は Begin の受理のほかに、定期シグナルでも出る（中身は同じ）。
手元のログ全部で調べた規則（2026-09-30。確信度: 高）:
- 定期は前回の定期から 300秒後に出る
- その時点がラウンド中なら、そのラウンドの RoundOver の0〜1秒後まで遅れて出て、
  次はそこから300秒後（定期 4,294件のうち 300秒ちょうど 1,402件・RoundOver 直後 2,254件）
- ラウンド中に出た定期（位相を動かさない）の次も、最後の定期から 300 秒の倍数で出る。予定は「最後の定期 +
  300×k」のうちいちばん近いもの（EY: 09:23 窓3 は 09:13 から 600.14 秒で出たのに、300 秒後だけを見ていた）
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
        self.schedule_due = None        # 直前の on_verified が予定と重なったなら、その予定の時刻（理由の行用）

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
        """呼び出し側が定期と分かった（ツールが押していないのに来た Verified）→ 位相の材料にする"""
        self.last_periodic = t

    def nearest_due(self, t):
        """t にいちばん近い予定「最後の定期 + 300×k」（k ≥ 1。EY）。位相を知らない・時刻が無ければ None"""
        if self.last_periodic is None or t is None:
            return None
        k = max(1, round((t - self.last_periodic) / config.VERIFIED_PERIODIC_SEC))
        return self.last_periodic + config.VERIFIED_PERIODIC_SEC * k

    def on_verified(self, t, pressed_recently: bool) -> str:
        """"begin" / "periodic" / "ignore" を返す。予定と重なった 1 回は、ツールが直前に押していても
        定期（EP。pressed_recently は見ない）。本物の受理が重なっていたら、ラウンド開始が 12 秒ほど後に
        来るので、呼び出し側（LogMonitor._undo_ignored_verified）が後から本物だったと分かる"""
        self.schedule_due = None
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
        due = self.nearest_due(t)
        if due is not None and abs(t - due) <= config.VERIFIED_PERIODIC_TOL_SEC:
            # 予定と重なった1回は定期（EP: 09:38 窓5 は位相どおりの定期を、前面の連打の直後なので受理と取り違え、
            # 店へ行って戻れなくなった。前は「押した直後なら定期と受理が1行にまとまった」とみなしていた）
            self.last_periodic = t
            self.schedule_due = due
            return PERIODIC
        return BEGIN if self._round_end_seen else IGNORE
