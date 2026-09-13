"""ラウンドの並びから moon の解放状況を推定する。

ToNのラウンドは 通常(N) と 特殊(S) に分かれ、`N [N] S` の繰り返しで進む
（2つ目のNは1/2の確率で無い）。つまり連続Nは最大2、Sは連続しない。

この並びから `Alternate` が「通常オーバーライド(N)」だったかを判定する。
Solstice は他3moonの後にしか出ず、Solstice を終えると Alternate が通常
オーバーライド枠に加わるため、`Alternate` の N 確定は「4種moonが済んでいる」
ことを意味する。見ていないうちに他の窓や参加前に消化されていた場合を拾える。

ラベルはそのラウンドの時点では決まらないことが多く、**後続のラウンドで
遡って確定する**（`Classic → Ghost → Midnight` なら Midnight が来た時点で
Ghost が N と分かる）。そのため「ありうる状態」を1つに絞らず、
`(直近の連続N数, 保留中ラウンドのラベル列)` の仮説集合として持ち回る。

副作用なし。ログイベントを渡すと状態が進むだけ。
"""

# 4種moon。1回目はプレイ、2回目以降はスキップの対象になる
MOONS = ("Mystic Moon", "Blood Moon", "Twilight", "Solstice")

# 必ず通常(N)のラウンド
ALWAYS_NORMAL = frozenset({"Classic", "Run"})

# N と S のどちらもありうるラウンド（通常オーバーライド枠）
OVERRIDE_CANDIDATES = frozenset({"Ghost", "Unbound", "8 Pages", "Alternate"})

MAX_NORMAL_RUN = 2   # 連続してよい通常ラウンド数

# 遡って確定させる範囲。これを超えて割れ続けるラベルは未確定のまま捨てる
# （仮説の数が際限なく増えるのを防ぐ。実測では数ラウンドで収束する）
PENDING_WINDOW = 8


class Round:
    """1ラウンドぶんの記録。`label` は後から埋まることがある"""

    __slots__ = ("round_type", "label")

    def __init__(self, round_type: str):
        self.round_type = round_type
        self.label = ""

    def __repr__(self):
        return f"Round({self.round_type!r}, {self.label!r})"


class RoundSequence:
    """1インスタンスぶんのラウンド並びと moon 解放状況"""

    def __init__(self):
        self.reset()

    def reset(self):
        """インスタンスが変わったとき。moon の消化状況も分からなくなる"""
        # 仮説 = (直近の連続N数, 保留中ラウンドのラベル列)。途中参加では
        # 連続N数が分からないので {0, 1, 2} すべてから始める
        self._hyps: set[tuple[int, tuple[str, ...]]] = {
            (run, ()) for run in range(MAX_NORMAL_RUN + 1)
        }
        self._pending: list[Round] = []   # ラベル未確定で仮説に載っているもの
        self.rounds: list[Round] = []     # 観測した全ラウンド（テスト・確認用）
        self.moon_done: dict[str, bool] = {name: False for name in MOONS}
        self.force_special = False        # OnMasterClientSwitched の次は強制S
        self.special_seen = False         # 3クラ解放前は Classic しか来ない

    # ── 問い合わせ ────────────────────────────
    def is_moon_repeat(self, round_type: str) -> bool:
        """このmoonが2回目以降か。`on_round()` より前に呼ぶこと"""
        return bool(self.moon_done.get(round_type))

    def moons_unlocked(self) -> bool:
        return all(self.moon_done.values())

    def labels(self) -> list[tuple[str, str]]:
        """観測順の (ラウンド種別, ラベル)。ラベルは "N"/"S"/""（未確定）"""
        return [(r.round_type, r.label) for r in self.rounds]

    # ── 状態を進める ──────────────────────────
    def on_master_switched(self):
        """次のラウンドは連続N数の制約を無視して強制的にS"""
        self.force_special = True

    def on_round(self, round_type: str) -> str:
        """ラウンド1つぶん状態を進める。

        戻り値はこのラウンドのラベル（その場で決まらなければ ""）。
        後続で確定したぶんは `labels()` に反映される。
        moon のフラグはこの中で立てるので、判定に使う `is_moon_repeat()` は
        必ずこれより前に呼ぶこと。
        """
        forced = self.force_special
        self.force_special = False

        first_moon = round_type in MOONS and not self.moon_done[round_type]
        current = Round(round_type)
        self.rounds.append(current)

        # 3クラ未解放の間は Classic しか来ない。連続N数の制約が成り立たない
        # （Classicが5回続くこともある）ので、最初の Classic 以外が出るまでは
        # 推定を適用せず、「直前はNだった」ことだけを残す。その Classic が
        # N連の1つ目か2つ目かは分からないので {1, 2} の両方を持つ。
        if not self.special_seen:
            if round_type == "Classic":
                self._hyps = {(1, ()), (MAX_NORMAL_RUN, ())}
                self._pending = []
                current.label = "N"
                return "N"
            self.special_seen = True

        self._advance(current, forced, first_moon)
        self._resolve()

        if first_moon:
            # 経路A: 出たmoonだけ。スキップしても出現した事実は変わらない
            self.moon_done[round_type] = True
        return current.label

    def unlock_all_moons(self):
        for name in self.moon_done:
            self.moon_done[name] = True

    # ── 内部 ──────────────────────────────────
    def _candidate_labels(self, round_type: str, forced: bool,
                          first_moon: bool) -> tuple[str, ...]:
        if forced:
            return ("S",)
        if round_type in ALWAYS_NORMAL:
            return ("N",)
        if round_type in OVERRIDE_CANDIDATES or first_moon:
            return ("N", "S")
        return ("S",)

    def _advance(self, current: Round, forced: bool, first_moon: bool):
        labels = self._candidate_labels(current.round_type, forced, first_moon)
        new_hyps: set[tuple[int, tuple[str, ...]]] = set()
        for run, assigned in self._hyps:
            for label in labels:
                if label == "N" and run < MAX_NORMAL_RUN:
                    new_hyps.add((run + 1, assigned + ("N",)))
                elif label == "S" and (forced or run >= 1):
                    new_hyps.add((0, assigned + ("S",)))

        if new_hyps:
            self._hyps = new_hyps
            self._pending.append(current)
            return
        # 生き残る仮説が無いのは前提が崩れている（取りこぼし・想定外の並び）。
        # 空のまま進むと以後なにも判定できないので、不明に戻してやり直す。
        self._hyps = {(run, ()) for run in range(MAX_NORMAL_RUN + 1)}
        self._pending = []

    def _resolve(self):
        """全仮説が一致した先頭のラベルを確定させ、仮説から外す"""
        while self._pending:
            first = {assigned[0] for _run, assigned in self._hyps}
            if len(first) != 1:
                break
            label = first.pop()
            done = self._pending.pop(0)
            done.label = label
            self._hyps = {(run, assigned[1:]) for run, assigned in self._hyps}
            self._on_label_fixed(done)

        # 割れたまま溜まり続けるぶんは未確定として捨てる（仮説の爆発を防ぐ）
        while len(self._pending) > PENDING_WINDOW:
            self._pending.pop(0)
            self._hyps = {(run, assigned[1:]) for run, assigned in self._hyps}

    def _on_label_fixed(self, rnd: Round):
        if rnd.round_type == "Alternate" and rnd.label == "N":
            # 経路B: Alternate が通常オーバーライドなら4種すべて解放済み。
            # 遡って確定した場合も、確定したその時点で立てる。
            self.unlock_all_moons()
