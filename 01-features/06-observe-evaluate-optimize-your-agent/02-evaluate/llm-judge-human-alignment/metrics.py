"""Agreement measures for ordinal 1-5 ratings, implemented without third-party dependencies."""

import math
from itertools import combinations
from statistics import mean

SCALE = (1, 2, 3, 4, 5)


def quadratic_weighted_kappa(a: list[int], b: list[int]) -> float | None:
    """Cohen's kappa with quadratic weights: penalizes a 1-vs-4 gap more than 1-vs-2.

    Returns None when kappa is undefined, for example when both raters give every
    case the same score (no variance to agree on).
    """
    if len(a) != len(b) or not a:
        raise ValueError("ratings must be non-empty and the same length")
    k = len(SCALE)
    index = {score: i for i, score in enumerate(SCALE)}
    observed = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        observed[index[x]][index[y]] += 1
    n = len(a)
    row = [sum(observed[i]) for i in range(k)]
    col = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    weight = [[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)]
    disagreement = sum(weight[i][j] * observed[i][j] for i in range(k) for j in range(k)) / n
    expected = sum(weight[i][j] * row[i] * col[j] for i in range(k) for j in range(k)) / (n * n)
    if expected == 0:
        return None
    return 1 - disagreement / expected


def mean_pairwise_kappa(ratings_by_reviewer: dict[str, list[int]]) -> float | None:
    """Average quadratic weighted kappa across every pair of reviewers."""
    values = [
        kappa
        for a, b in combinations(ratings_by_reviewer.values(), 2)
        if (kappa := quadratic_weighted_kappa(a, b)) is not None
    ]
    return mean(values) if values else None


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for position in range(i, j + 1):
            ranks[order[position]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    """Spearman rank correlation with average ranks for ties. None if either side is constant."""
    ra, rb = _average_ranks(a), _average_ranks(b)
    ma, mb = mean(ra), mean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    var = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return cov / var if var else None


def mean_absolute_error(a: list[float], b: list[float]) -> float:
    return mean(abs(x - y) for x, y in zip(a, b))


def exact_agreement(a: list[int], b: list[int]) -> float:
    return mean(1.0 if x == y else 0.0 for x, y in zip(a, b))


def within_one(a: list[int], b: list[int]) -> float:
    return mean(1.0 if abs(x - y) <= 1 else 0.0 for x, y in zip(a, b))


def wilson_upper_bound(successes: int, trials: int, z: float = 1.96) -> float | None:
    """Upper bound of the Wilson score interval, used for the severe false-pass rate.

    With small calibration sets the bound stays wide even when no false pass is observed,
    which is the honest answer: 0 of 5 does not prove the rate is 0.
    """
    if trials == 0:
        return None
    p = successes / trials
    centre = p + z * z / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return min(1.0, (centre + margin) / (1 + z * z / trials))
