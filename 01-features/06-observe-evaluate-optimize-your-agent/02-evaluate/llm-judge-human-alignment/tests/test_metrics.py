import math

import pytest
from metrics import (
    exact_agreement,
    mean_absolute_error,
    mean_pairwise_kappa,
    quadratic_weighted_kappa,
    spearman,
    wilson_upper_bound,
    within_one,
)


def test_kappa_perfect_agreement():
    assert quadratic_weighted_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0)


def test_kappa_penalizes_large_gaps_more_than_small_gaps():
    human = [1, 1, 3, 5, 5]
    near = quadratic_weighted_kappa(human, [2, 1, 3, 5, 5])
    far = quadratic_weighted_kappa(human, [4, 1, 3, 5, 5])
    assert near > far


def test_kappa_matches_reference_value():
    # Reference value from sklearn.metrics.cohen_kappa_score(weights="quadratic").
    assert quadratic_weighted_kappa([1, 2, 3, 4, 5, 3], [1, 3, 3, 5, 4, 2]) == pytest.approx(0.8, abs=1e-9)


def test_kappa_undefined_without_variance():
    assert quadratic_weighted_kappa([5, 5, 5], [5, 5, 5]) is None


def test_mean_pairwise_kappa_averages_pairs():
    ratings = {"a": [1, 2, 3, 4], "b": [1, 2, 3, 4], "c": [1, 2, 3, 4]}
    assert mean_pairwise_kappa(ratings) == pytest.approx(1.0)


def test_spearman_handles_ties_and_inversion():
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 5, 5], [1, 2, 4, 5]) == pytest.approx(math.sqrt(0.8))
    assert spearman([3, 3, 3], [1, 2, 3]) is None


def test_distance_measures():
    assert mean_absolute_error([1, 5], [4, 5]) == 1.5
    assert exact_agreement([1, 2, 3], [1, 2, 4]) == pytest.approx(2 / 3)
    assert within_one([1, 2, 3], [3, 2, 4]) == pytest.approx(2 / 3)


def test_wilson_upper_bound_is_wide_for_small_samples():
    assert wilson_upper_bound(0, 5) == pytest.approx(0.4345, abs=1e-3)
    assert wilson_upper_bound(0, 0) is None
    assert wilson_upper_bound(5, 5) == 1.0
