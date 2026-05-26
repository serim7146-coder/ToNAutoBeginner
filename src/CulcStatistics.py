import math

# 二項定理における、ある点での確率の計算
# lgammaで、Lanczon法による階乗の対数の近似値が求まる。これにより、nCkのオーバーフローを回避する。
def binomial_pmf(n: int, k: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    if p == 0:
        return 1.0 if k == 0 else 0.0
    if p == 1:
        return 1.0 if k == n else 0.0

    log_prob = (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log(1-p)
    )
    return math.exp(log_prob)

# k回以上出る確率
# 漸化式により、計算量を削減
def binomial_pmf_upper(n: int, k: int, p: float) -> float:
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0

    q = 1 - p
    term = binomial_pmf(n, k, p)
    total = term

    for i in range(k, n):
        term *= ((n - i) / (i + 1)) * (p / q)
        total += term
        if term == 0.0:
            break

    return min(total, 1.0)

# 仮説検定
def hypothesis_testing(p, a) -> bool:
    return p <= a