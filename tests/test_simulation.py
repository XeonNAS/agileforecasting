"""Tests for agile_mc.simulation — pure Monte Carlo functions."""

from __future__ import annotations

import datetime as dt

import numpy as np

from agile_mc.simulation import (
    at_least_threshold,
    completion_cdf_by_date,
    simulate_how_many_daily,
    simulate_when_daily,
    split_sample_counts,
    stochastic_round,
    threshold_breakdown,
)

# ---------------------------------------------------------------------------
# stochastic_round
# ---------------------------------------------------------------------------


class TestStochasticRound:
    def test_zero_or_negative_returns_zero(self):
        rng = np.random.default_rng(0)
        assert stochastic_round(0.0, rng) == 0
        assert stochastic_round(-1.5, rng) == 0

    def test_integer_input_returns_exact(self):
        rng = np.random.default_rng(0)
        assert stochastic_round(3.0, rng) == 3

    def test_fractional_rounds_to_floor_or_ceil(self):
        rng = np.random.default_rng(42)
        results = {stochastic_round(2.5, rng) for _ in range(200)}
        assert results <= {2, 3}, "result must be floor or ceil"
        assert 2 in results and 3 in results, "both outcomes should appear with 200 trials"

    def test_fraction_0_always_floor(self):
        rng = np.random.default_rng(0)
        # 4.0 has frac==0, so always returns 4
        for _ in range(20):
            assert stochastic_round(4.0, rng) == 4


# ---------------------------------------------------------------------------
# at_least_threshold
# ---------------------------------------------------------------------------


class TestAtLeastThreshold:
    def test_empty_returns_zero(self):
        assert at_least_threshold(np.array([]), 0.85) == 0

    def test_p0_returns_minimum(self):
        samples = np.array([1, 2, 3, 4, 5])
        # P(x >= N) = 0 means we need N above everything — clamps to max
        assert at_least_threshold(samples, 0.0) == 5

    def test_p1_returns_minimum(self):
        samples = np.array([1, 2, 3, 4, 5])
        assert at_least_threshold(samples, 1.0) == 1

    def test_p50_semantics(self):
        samples = np.array([10] * 50 + [20] * 50)
        result = at_least_threshold(samples, 0.50)
        # "at least" at 50%: P(x >= result) ~= 0.5
        # 50 out of 100 values are 20, so P(x >= 20) = 0.50 → threshold = 20
        assert result == 20

    def test_monotone_in_p(self):
        rng = np.random.default_rng(7)
        samples = rng.integers(0, 100, size=1000)
        t85 = at_least_threshold(samples, 0.85)
        t95 = at_least_threshold(samples, 0.95)
        # Higher confidence "at least" → lower or equal threshold
        assert t95 <= t85


# ---------------------------------------------------------------------------
# simulate_how_many_daily
# ---------------------------------------------------------------------------


class TestSimulateHowManyDaily:
    def _dates(self, n: int) -> list[dt.date]:
        base = dt.date(2026, 1, 5)
        return [base + dt.timedelta(days=i) for i in range(n)]

    def test_empty_history_returns_zeros(self):
        dates = self._dates(10)
        result = simulate_how_many_daily(np.array([]), dates, {}, 100, seed=1)
        assert (result == 0).all()

    def test_zero_history_returns_zeros(self):
        dates = self._dates(5)
        result = simulate_how_many_daily(np.array([0, 0, 0]), dates, {}, 100, seed=1)
        assert (result == 0).all()

    def test_deterministic_with_seed(self):
        dates = self._dates(5)
        history = np.array([3, 5, 7])
        r1 = simulate_how_many_daily(history, dates, {}, 50, seed=99)
        r2 = simulate_how_many_daily(history, dates, {}, 50, seed=99)
        np.testing.assert_array_equal(r1, r2)

    def test_capacity_ratio_zero_produces_zero(self):
        dates = self._dates(5)
        per_date = {d: 0.0 for d in dates}
        history = np.array([10, 20, 30])
        result = simulate_how_many_daily(history, dates, per_date, 200, seed=1)
        assert (result == 0).all()

    def test_positive_history_produces_positive_totals(self):
        dates = self._dates(10)
        history = np.array([5, 10, 15])
        result = simulate_how_many_daily(history, dates, {}, 500, seed=42)
        assert result.mean() > 0
        assert result.min() >= 0

    def test_output_shape(self):
        dates = self._dates(5)
        result = simulate_how_many_daily(np.array([3]), dates, {}, n_sims=200, seed=0)
        assert result.shape == (200,)


# ---------------------------------------------------------------------------
# simulate_when_daily
# ---------------------------------------------------------------------------


class TestSimulateWhenDaily:
    def _dates(self, start: dt.date, n: int) -> list[dt.date]:
        return [start + dt.timedelta(days=i) for i in range(n)]

    def test_zero_remaining_returns_first_date(self):
        start = dt.date(2026, 3, 1)
        dates = self._dates(start, 20)
        result = simulate_when_daily(np.array([5]), dates, {}, items_remaining=0, n_sims=10, seed=0)
        assert all(d == dates[0] for d in result)

    def test_output_length_matches_n_sims(self):
        start = dt.date(2026, 3, 1)
        dates = self._dates(start, 30)
        result = simulate_when_daily(np.array([3, 5, 7]), dates, {}, items_remaining=10, n_sims=50, seed=1)
        assert len(result) == 50

    def test_completion_date_within_bounds(self):
        start = dt.date(2026, 3, 1)
        dates = self._dates(start, 60)
        result = simulate_when_daily(np.array([5]), dates, {}, items_remaining=20, n_sims=100, seed=2)
        # All dates should be within the forecast window or equal to the last date (capped)
        assert all(dates[0] <= d <= dates[-1] for d in result)

    def test_more_remaining_means_later_completion(self):
        start = dt.date(2026, 3, 1)
        dates = self._dates(start, 200)
        history = np.array([3, 4, 5])
        r_small = simulate_when_daily(history, dates, {}, items_remaining=5, n_sims=300, seed=9)
        r_large = simulate_when_daily(history, dates, {}, items_remaining=50, n_sims=300, seed=9)
        mean_small = sum(d.toordinal() for d in r_small) / len(r_small)
        mean_large = sum(d.toordinal() for d in r_large) / len(r_large)
        assert mean_large > mean_small


# ---------------------------------------------------------------------------
# completion_cdf_by_date
# ---------------------------------------------------------------------------


class TestCompletionCdfByDate:
    def test_empty_returns_zeros(self):
        dates = [dt.date(2026, 1, d) for d in range(1, 6)]
        result = completion_cdf_by_date([], dates)
        assert result == [0.0] * 5

    def test_all_complete_before_first_date(self):
        early = dt.date(2026, 1, 1)
        dates = [dt.date(2026, 2, 1), dt.date(2026, 3, 1)]
        result = completion_cdf_by_date([early] * 100, dates)
        assert result == [1.0, 1.0]

    def test_all_complete_after_last_date(self):
        late = dt.date(2026, 12, 31)
        dates = [dt.date(2026, 1, 1), dt.date(2026, 6, 1)]
        result = completion_cdf_by_date([late] * 100, dates)
        assert result == [0.0, 0.0]

    def test_monotone_increasing(self):
        start = dt.date(2026, 3, 1)
        completion_dates = [start + dt.timedelta(days=i) for i in range(30)]
        query_dates = [start + dt.timedelta(days=i) for i in range(0, 30, 3)]
        result = completion_cdf_by_date(completion_dates, query_dates)
        for a, b in zip(result, result[1:]):
            assert b >= a

    def test_cdf_bounds(self):
        start = dt.date(2026, 1, 1)
        dates = [start + dt.timedelta(days=i) for i in range(10)]
        completion_dates = dates[:5]
        result = completion_cdf_by_date(completion_dates, dates)
        assert all(0.0 <= v <= 1.0 for v in result)


# ---------------------------------------------------------------------------
# split_sample_counts
# ---------------------------------------------------------------------------


class TestSplitSampleCounts:
    def test_totals_preserved(self):
        rng = np.random.default_rng(0)
        totals = rng.integers(0, 50, size=500)
        proj, bau = split_sample_counts(totals, 0.7, seed=1)
        np.testing.assert_array_equal(proj + bau, totals)

    def test_ratio_zero_all_bau(self):
        totals = np.array([10, 20, 30])
        proj, bau = split_sample_counts(totals, 0.0, seed=0)
        np.testing.assert_array_equal(proj, [0, 0, 0])
        np.testing.assert_array_equal(bau, totals)

    def test_ratio_one_all_project(self):
        totals = np.array([10, 20, 30])
        proj, bau = split_sample_counts(totals, 1.0, seed=0)
        np.testing.assert_array_equal(proj, totals)
        np.testing.assert_array_equal(bau, [0, 0, 0])

    def test_ratio_clamped(self):
        # ratio > 1 treated as 1; ratio < 0 treated as 0
        totals = np.array([10] * 200)
        proj_over, _ = split_sample_counts(totals, 1.5, seed=0)
        proj_under, _ = split_sample_counts(totals, -0.5, seed=0)
        assert (proj_over == totals).all()
        assert (proj_under == 0).all()

    def test_deterministic_with_seed(self):
        totals = np.arange(1, 101)
        r1 = split_sample_counts(totals, 0.6, seed=77)
        r2 = split_sample_counts(totals, 0.6, seed=77)
        np.testing.assert_array_equal(r1[0], r2[0])

    def test_expected_mean_split(self):
        # With ratio=0.8, mean project fraction should be close to 0.8
        rng = np.random.default_rng(5)
        totals = rng.integers(5, 20, size=2000)
        proj, _ = split_sample_counts(totals, 0.8, seed=42)
        actual_ratio = proj.sum() / totals.sum()
        assert abs(actual_ratio - 0.8) < 0.02


# ---------------------------------------------------------------------------
# threshold_breakdown
# ---------------------------------------------------------------------------


class TestThresholdBreakdown:
    def test_empty_returns_zeros(self):
        assert threshold_breakdown(np.array([]), np.array([]), np.array([]), 0.85) == (0, 0, 0)

    def test_breakdown_is_additive(self):
        rng = np.random.default_rng(3)
        totals = rng.integers(10, 100, size=500)
        proj = rng.integers(0, totals)
        bau = totals - proj
        total_t, proj_t, bau_t = threshold_breakdown(totals, proj, bau, 0.85)
        assert proj_t + bau_t == total_t

    def test_consistent_index(self):
        # All three values should come from the same simulation index
        totals = np.array([5, 10, 15, 20, 25])
        proj = np.array([1, 2, 3, 4, 5])
        bau = totals - proj
        total_t, proj_t, bau_t = threshold_breakdown(totals, proj, bau, 0.5)
        assert proj_t + bau_t == total_t

    def test_higher_p_gives_lower_or_equal_threshold(self):
        rng = np.random.default_rng(8)
        totals = rng.integers(0, 100, size=1000)
        proj, bau = split_sample_counts(totals, 0.7, seed=8)
        t85, _, _ = threshold_breakdown(totals, proj, bau, 0.85)
        t95, _, _ = threshold_breakdown(totals, proj, bau, 0.95)
        assert t95 <= t85
