"""Tests for agile_mc.ado_sync.build_sprint_plan — forecast window → sprint chunks."""

from __future__ import annotations

import datetime as dt

import pytest

from agile_mc.ado_sync import Sprint, build_sprint_plan

_WEEKDAYS = {0, 1, 2, 3, 4}


def _sprint(name: str, start: dt.date, days: int = 14) -> Sprint:
    end_inclusive = start + dt.timedelta(days=days - 1)
    return Sprint(
        iteration_id=name,
        name=name,
        start_date=start,
        end_exclusive=end_inclusive + dt.timedelta(days=1),
        end_inclusive=end_inclusive,
    )


_SPRINT_22 = _sprint("Iteration 22 (2026)", dt.date(2026, 10, 15))


class TestBuildSprintPlan:
    def test_full_sprint_has_full_capacity(self):
        plan = build_sprint_plan([_SPRINT_22], dt.date(2026, 10, 15), dt.date(2026, 10, 28), _WEEKDAYS, {})

        assert plan[0].capacity_factor == pytest.approx(1.0)

    def test_full_sprint_includes_every_working_day(self):
        plan = build_sprint_plan([_SPRINT_22], dt.date(2026, 10, 15), dt.date(2026, 10, 28), _WEEKDAYS, {})

        assert len(plan[0].working_dates) == 10

    def test_forecast_starting_mid_sprint_scales_by_remaining_days(self):
        plan = build_sprint_plan([_SPRINT_22], dt.date(2026, 10, 22), dt.date(2026, 10, 28), _WEEKDAYS, {})

        assert plan[0].capacity_factor == pytest.approx(0.5)

    def test_per_date_ratio_reduces_capacity(self):
        ratios = {d: 0.5 for d in (dt.date(2026, 10, 15) + dt.timedelta(days=i) for i in range(14))}

        plan = build_sprint_plan([_SPRINT_22], dt.date(2026, 10, 15), dt.date(2026, 10, 28), _WEEKDAYS, ratios)

        assert plan[0].capacity_factor == pytest.approx(0.5)

    def test_team_days_off_are_excluded_from_working_dates(self):
        ratios = {dt.date(2026, 10, 15): 0.0}

        plan = build_sprint_plan([_SPRINT_22], dt.date(2026, 10, 15), dt.date(2026, 10, 28), _WEEKDAYS, ratios)

        assert dt.date(2026, 10, 15) not in plan[0].working_dates

    def test_past_sprints_are_skipped(self):
        earlier = _sprint("Iteration 21 (2026)", dt.date(2026, 10, 1))

        plan = build_sprint_plan([earlier, _SPRINT_22], dt.date(2026, 10, 15), dt.date(2026, 10, 28), _WEEKDAYS, {})

        assert [c.sprint_name for c in plan] == ["Iteration 22 (2026)"]

    def test_window_past_last_sprint_adds_future_sprints_of_same_length(self):
        plan = build_sprint_plan([_SPRINT_22], dt.date(2026, 10, 15), dt.date(2026, 11, 25), _WEEKDAYS, {})

        assert [c.sprint_name for c in plan] == ["Iteration 22 (2026)", "Future sprint 1", "Future sprint 2"]

    def test_future_sprints_start_after_last_sprint(self):
        plan = build_sprint_plan([_SPRINT_22], dt.date(2026, 10, 15), dt.date(2026, 11, 11), _WEEKDAYS, {})

        assert plan[1].working_dates[0] == dt.date(2026, 10, 29)

    def test_no_sprints_defaults_to_two_week_future_sprints(self):
        plan = build_sprint_plan([], dt.date(2026, 10, 19), dt.date(2026, 11, 1), _WEEKDAYS, {})

        assert len(plan[0].working_dates) == 10
