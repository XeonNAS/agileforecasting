"""Tests for the sprint capacity calculation fixes.

Covers:
- ADO finishDate is inclusive (date boundary)
- planned_working_days reflects only team-wide days off
- Individual member days off reduce capacity_factor but NOT planned_working_days
- Double-counting protection when an individual day off falls on a team day off
- capacity_factor and team_capacity_hours are correctly computed
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from agile_mc.ado_sync import (
    Sprint,
    build_capacity_schedule,
    fetch_sprints,
)

_WORKING = {0, 1, 2, 3, 4}  # Mon–Fri


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sprint(n: int, start: dt.date, end_inclusive: dt.date) -> Sprint:
    return Sprint(
        iteration_id=f"iter-{n:02d}",
        name=f"Sprint {n}",
        start_date=start,
        end_exclusive=end_inclusive + dt.timedelta(days=1),
        end_inclusive=end_inclusive,
    )


def _capacity_row(member_id: str, capacity_per_day: float, days_off: List[str]) -> Dict[str, Any]:
    return {
        "teamMember": {"id": member_id},
        "activities": [{"capacityPerDay": capacity_per_day}],
        "daysOff": [{"start": f"{d}T00:00:00Z", "end": f"{d}T00:00:00Z"} for d in days_off],
    }


def _team_day_off(date_str: str) -> Dict[str, str]:
    return {"start": f"{date_str}T00:00:00Z", "end": f"{date_str}T00:00:00Z"}


def _stub_ado(
    *,
    team_days_off: List[Dict] | None = None,
    capacities: List[Dict] | None = None,
    iteration_capacities: Dict | None = None,
) -> MagicMock:
    ado = MagicMock()
    ado.get_team_days_off.return_value = team_days_off or []
    ado.get_capacities.return_value = capacities or []
    ado.get_iteration_capacities.return_value = iteration_capacities or {}
    return ado


def _stub_ado_list_iterations(iterations: List[Dict]) -> MagicMock:
    ado = MagicMock()
    ado.list_iterations.return_value = iterations
    return ado


# ---------------------------------------------------------------------------
# Bug 1: ADO finishDate is inclusive — date boundary test
# ---------------------------------------------------------------------------


class TestFetchSprintsDateBoundary:
    def test_end_inclusive_equals_finish_date(self):
        """finishDate from ADO is the inclusive last day; end_inclusive must equal it."""
        ado = _stub_ado_list_iterations(
            [
                {
                    "id": "iter-11",
                    "name": "Iteration 11",
                    "attributes": {
                        "startDate": "2026-05-14T00:00:00Z",
                        "finishDate": "2026-05-27T00:00:00Z",
                    },
                }
            ]
        )
        sprints = fetch_sprints(ado)
        assert len(sprints) == 1
        assert sprints[0].end_inclusive == dt.date(2026, 5, 27)

    def test_end_exclusive_is_one_day_after_finish(self):
        ado = _stub_ado_list_iterations(
            [
                {
                    "id": "iter-11",
                    "name": "Iteration 11",
                    "attributes": {
                        "startDate": "2026-05-14T00:00:00Z",
                        "finishDate": "2026-05-27T00:00:00Z",
                    },
                }
            ]
        )
        sprints = fetch_sprints(ado)
        assert sprints[0].end_exclusive == dt.date(2026, 5, 28)

    def test_normal_working_days_includes_finish_date(self):
        """Iteration 11 May 14–27 has 10 Mon-Fri days, not 9."""
        # May 14(Thu) 15(Fri) 18(Mon) 19(Tue) 20(Wed) 21(Thu) 22(Fri)
        #     25(Mon) 26(Tue) 27(Wed) = 10 working days
        sprint = _make_sprint(11, dt.date(2026, 5, 14), dt.date(2026, 5, 27))
        ado = _stub_ado()
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        assert int(cap_df.iloc[0]["normal_working_days"]) == 10

    def test_finish_date_weekday_appears_in_per_date_ratio(self):
        """The finishDate (May 27, a Wednesday) must appear in per_date_ratio."""
        sprint = _make_sprint(11, dt.date(2026, 5, 14), dt.date(2026, 5, 27))
        ado = _stub_ado()
        _, _, per_date_ratio = build_capacity_schedule(ado, [sprint], _WORKING)
        assert dt.date(2026, 5, 27) in per_date_ratio


# ---------------------------------------------------------------------------
# Bug 2: planned_working_days must not be reduced by individual days off
# ---------------------------------------------------------------------------


class TestPlannedWorkingDaysScheduleOnly:
    def test_no_team_days_off_planned_equals_normal(self):
        """Iteration 12: 0 team days off → planned_working_days == normal_working_days."""
        # May 28–Jun 9: 9 Mon-Fri days
        sprint = _make_sprint(12, dt.date(2026, 5, 28), dt.date(2026, 6, 9))
        # 8 members, each with capacity 6.5; 7 of them have 1 day off each
        capacities = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, ["2026-05-28"]),
            _capacity_row("alvin", 6.5, ["2026-05-29"]),
            _capacity_row("eldric", 6.5, ["2026-06-01"]),
            _capacity_row("jason", 6.5, ["2026-06-02"]),
            _capacity_row("jose", 6.5, ["2026-06-03"]),
            _capacity_row("katie", 6.5, ["2026-06-04"]),
            _capacity_row("rence", 6.5, ["2026-06-05"]),
        ]
        ado = _stub_ado(capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        assert int(row["normal_working_days"]) == 9
        assert int(row["planned_working_days"]) == 9, "Individual days off must not reduce planned_working_days"

    def test_team_days_off_reduce_planned(self):
        """Iteration 11: 2 team days off → planned_working_days = 10 - 2 = 8."""
        sprint = _make_sprint(11, dt.date(2026, 5, 14), dt.date(2026, 5, 27))
        team_off = [_team_day_off("2026-05-21"), _team_day_off("2026-05-26")]
        capacities = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, ["2026-05-25"]),
            _capacity_row("eldric", 6.5, ["2026-05-19", "2026-05-20"]),
            _capacity_row("u3", 6.5, []),
            _capacity_row("u4", 6.5, []),
            _capacity_row("u5", 6.5, []),
            _capacity_row("u6", 6.5, []),
            _capacity_row("u7", 6.5, []),
        ]
        ado = _stub_ado(team_days_off=team_off, capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        assert int(row["normal_working_days"]) == 10
        assert int(row["planned_working_days"]) == 8

    def test_individual_days_off_not_subtracted_from_planned_with_team_off(self):
        """Iteration 11: Alexander and Eldric days off do not change planned_working_days."""
        sprint = _make_sprint(11, dt.date(2026, 5, 14), dt.date(2026, 5, 27))
        team_off = [_team_day_off("2026-05-21"), _team_day_off("2026-05-26")]
        capacities_with_individual = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, ["2026-05-25"]),
            _capacity_row("eldric", 6.5, ["2026-05-19", "2026-05-20"]),
            _capacity_row("u3", 6.5, []),
            _capacity_row("u4", 6.5, []),
            _capacity_row("u5", 6.5, []),
            _capacity_row("u6", 6.5, []),
            _capacity_row("u7", 6.5, []),
        ]
        capacities_no_individual = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, []),
            _capacity_row("eldric", 6.5, []),
            _capacity_row("u3", 6.5, []),
            _capacity_row("u4", 6.5, []),
            _capacity_row("u5", 6.5, []),
            _capacity_row("u6", 6.5, []),
            _capacity_row("u7", 6.5, []),
        ]
        ado_with = _stub_ado(team_days_off=team_off, capacities=capacities_with_individual)
        ado_without = _stub_ado(team_days_off=team_off, capacities=capacities_no_individual)
        _, cap_with, _ = build_capacity_schedule(ado_with, [sprint], _WORKING)
        _, cap_without, _ = build_capacity_schedule(ado_without, [sprint], _WORKING)
        assert int(cap_with.iloc[0]["planned_working_days"]) == int(cap_without.iloc[0]["planned_working_days"])


# ---------------------------------------------------------------------------
# capacity_factor and team_capacity_hours
# ---------------------------------------------------------------------------


class TestCapacityFactorCalculation:
    def test_iteration12_capacity_factor(self):
        """Iteration 12: 7 members each off 1 different day.

        baseline_per_day = 8 * 6.5 = 52
        baseline_capacity_sum = 52 * 9 = 468
        planned_capacity_sum = 7 * (7 * 6.5) + 2 * (8 * 6.5) = 318.5 + 104 = 422.5
        capacity_factor = 422.5 / 468 ≈ 0.9029
        """
        sprint = _make_sprint(12, dt.date(2026, 5, 28), dt.date(2026, 6, 9))
        capacities = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, ["2026-05-28"]),
            _capacity_row("alvin", 6.5, ["2026-05-29"]),
            _capacity_row("eldric", 6.5, ["2026-06-01"]),
            _capacity_row("jason", 6.5, ["2026-06-02"]),
            _capacity_row("jose", 6.5, ["2026-06-03"]),
            _capacity_row("katie", 6.5, ["2026-06-04"]),
            _capacity_row("rence", 6.5, ["2026-06-05"]),
        ]
        ado = _stub_ado(capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        assert float(row["team_capacity_hours"]) == pytest.approx(422.5)
        assert float(row["baseline_capacity_hours"]) == pytest.approx(468.0)
        assert float(row["capacity_factor"]) == pytest.approx(422.5 / 468.0, rel=1e-3)

    def test_iteration11_capacity_factor(self):
        """Iteration 11: 2 team days off, Alexander off 1 day, Eldric off 2 days.

        normal = 10, planned = 8 (10 - 2 team off)
        baseline_per_day = 8 * 6.5 = 52, baseline_capacity_sum = 52 * 10 = 520
        Team days off (May 21, May 26) contribute 0 each.
        Remaining 8 days:
          May 14, 15, 18, 22, 27: all 8 avail → 5 * 52 = 260
          May 19: Eldric off → 7 * 6.5 = 45.5
          May 20: Eldric off → 45.5
          May 25: Alexander off → 45.5
        planned_capacity_sum = 260 + 3 * 45.5 = 260 + 136.5 = 396.5
        capacity_factor = 396.5 / 520 ≈ 0.7625
        """
        sprint = _make_sprint(11, dt.date(2026, 5, 14), dt.date(2026, 5, 27))
        team_off = [_team_day_off("2026-05-21"), _team_day_off("2026-05-26")]
        capacities = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, ["2026-05-25"]),
            _capacity_row("eldric", 6.5, ["2026-05-19", "2026-05-20"]),
            _capacity_row("u3", 6.5, []),
            _capacity_row("u4", 6.5, []),
            _capacity_row("u5", 6.5, []),
            _capacity_row("u6", 6.5, []),
            _capacity_row("u7", 6.5, []),
        ]
        ado = _stub_ado(team_days_off=team_off, capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        assert float(row["team_capacity_hours"]) == pytest.approx(396.5)
        assert float(row["baseline_capacity_hours"]) == pytest.approx(520.0)
        assert float(row["capacity_factor"]) == pytest.approx(396.5 / 520.0, rel=1e-3)

    def test_no_days_off_capacity_factor_is_one(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        capacities = [_capacity_row(f"u{i}", 6.5, []) for i in range(4)]
        ado = _stub_ado(capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        assert float(cap_df.iloc[0]["capacity_factor"]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Double-counting: individual day off on a team day off
# ---------------------------------------------------------------------------


class TestDoubleCountingProtection:
    def test_individual_day_off_on_team_day_off_not_double_counted(self):
        """If a member's individual day off falls on a team day off, that day is
        only counted once in team_days_off_working (not also in inferred or member).
        planned_working_days should be normal - 1 (one unique team day off).
        """
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        # Jan 6 (Tue) is both a team day off AND member u1's personal day off
        team_off = [_team_day_off("2026-01-06")]
        capacities = [
            _capacity_row("u1", 6.5, ["2026-01-06"]),  # same day as team off
            _capacity_row("u2", 6.5, []),
        ]
        ado = _stub_ado(team_days_off=team_off, capacities=capacities)
        _, cap_df, per_date_ratio = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        # 10 working days in Jan 5-16, 1 team day off → 9
        assert int(row["planned_working_days"]) == 9
        assert int(row["normal_working_days"]) == 10
        # The team day off date must appear exactly once in team_days_off_dates
        assert row["team_days_off_dates"].count("2026-01-06") == 1
        assert row["inferred_zero_capacity_dates"] == ""
        # per_date_ratio for the team day off must be 0
        assert per_date_ratio[dt.date(2026, 1, 6)] == 0.0

    def test_individual_day_off_on_team_day_off_capacity_hours_not_double_subtracted(self):
        """Capacity hours for the overlapping day should not be subtracted twice."""
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        # Jan 6 team day off; u1 also has Jan 6 off
        team_off = [_team_day_off("2026-01-06")]
        capacities = [
            _capacity_row("u1", 6.5, ["2026-01-06"]),
            _capacity_row("u2", 6.5, []),
        ]
        ado = _stub_ado(team_days_off=team_off, capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        # 10 working days; 1 team off day (Jan 6) skipped entirely for both members
        # Remaining 9 days: u1 fully available, u2 fully available
        # team_capacity_hours = 9 * 2 * 6.5 = 117.0
        assert float(row["team_capacity_hours"]) == pytest.approx(117.0)


# ---------------------------------------------------------------------------
# schedule_availability is separate from capacity_factor
# ---------------------------------------------------------------------------


class TestScheduleAvailabilitySeparation:
    def test_schedule_availability_uses_only_team_days_off(self):
        """schedule_availability = planned / normal (team days off only)."""
        sprint = _make_sprint(12, dt.date(2026, 5, 28), dt.date(2026, 6, 9))
        capacities = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, ["2026-05-28"]),
            _capacity_row("alvin", 6.5, ["2026-05-29"]),
            _capacity_row("eldric", 6.5, ["2026-06-01"]),
        ]
        ado = _stub_ado(capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        # No team days off → schedule_availability = 1.0
        assert float(row["schedule_availability"]) == pytest.approx(1.0)
        # But capacity_factor < 1.0 because some members are off
        assert float(row["capacity_factor"]) < 1.0

    def test_schedule_availability_below_one_with_team_days_off(self):
        sprint = _make_sprint(11, dt.date(2026, 5, 14), dt.date(2026, 5, 27))
        team_off = [_team_day_off("2026-05-21"), _team_day_off("2026-05-26")]
        ado = _stub_ado(team_days_off=team_off)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        # 10 working days, 2 team off → 8/10 = 0.8
        assert float(row["schedule_availability"]) == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# per_user_capacity diagnostic field
# ---------------------------------------------------------------------------


class TestPerUserCapacityDiagnostic:
    def test_per_user_capacity_present(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        capacities = [_capacity_row("u1", 6.5, []), _capacity_row("u2", 4.0, [])]
        ado = _stub_ado(capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        per_user = cap_df.iloc[0]["per_user_capacity"]
        assert isinstance(per_user, list)
        assert len(per_user) == 2
        member_ids = {row["member_id"] for row in per_user}
        assert member_ids == {"u1", "u2"}

    def test_per_user_days_off_count_correct(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        # u1 has 2 days off (Jan 6 and Jan 7, both working days)
        capacities = [
            _capacity_row("u1", 6.5, ["2026-01-06", "2026-01-07"]),
            _capacity_row("u2", 6.5, []),
        ]
        ado = _stub_ado(capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        per_user = {row["member_id"]: row for row in cap_df.iloc[0]["per_user_capacity"]}
        assert per_user["u1"]["days_off_count"] == 2
        assert per_user["u2"]["days_off_count"] == 0

    def test_per_user_available_days_excludes_individual_and_team_days_off(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        # Jan 6 team off; u1 also has Jan 7 off
        team_off = [_team_day_off("2026-01-06")]
        capacities = [
            _capacity_row("u1", 6.5, ["2026-01-07"]),
            _capacity_row("u2", 6.5, []),
        ]
        ado = _stub_ado(team_days_off=team_off, capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        # 10 working days, 1 team off → 9 non-team working days
        # u1: 1 personal off → available = 9 - 1 = 8
        # u2: 0 personal off → available = 9
        per_user = {row["member_id"]: row for row in cap_df.iloc[0]["per_user_capacity"]}
        assert per_user["u1"]["available_days"] == 8
        assert per_user["u2"]["available_days"] == 9

    def test_per_user_available_capacity_hours(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        capacities = [
            _capacity_row("u1", 6.5, ["2026-01-06"]),  # 1 day off → 9 days * 6.5 = 58.5
            _capacity_row("u2", 4.0, []),  # 10 days * 4.0 = 40.0
        ]
        ado = _stub_ado(capacities=capacities)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        per_user = {row["member_id"]: row for row in cap_df.iloc[0]["per_user_capacity"]}
        assert per_user["u1"]["available_capacity_hours"] == pytest.approx(9 * 6.5)
        assert per_user["u2"]["available_capacity_hours"] == pytest.approx(10 * 4.0)


# ---------------------------------------------------------------------------
# summary_team_days_off_count does not pollute planned_working_days
# ---------------------------------------------------------------------------


class TestIterationSummaryDaysOffCountIsolated:
    def test_summary_api_days_off_count_does_not_reduce_planned(self):
        """iterationcapacities returning teamTotalDaysOff=7 (7 individual off days)
        must NOT reduce planned_working_days.
        """
        sprint = _make_sprint(12, dt.date(2026, 5, 28), dt.date(2026, 6, 9))
        capacities = [
            _capacity_row("adeza", 6.5, []),
            _capacity_row("alexander", 6.5, ["2026-05-28"]),
            _capacity_row("alvin", 6.5, ["2026-05-29"]),
            _capacity_row("eldric", 6.5, ["2026-06-01"]),
            _capacity_row("jason", 6.5, ["2026-06-02"]),
            _capacity_row("jose", 6.5, ["2026-06-03"]),
            _capacity_row("katie", 6.5, ["2026-06-04"]),
            _capacity_row("rence", 6.5, ["2026-06-05"]),
        ]
        # Simulate the broken API returning teamTotalDaysOff=7 (individual member off days)
        iteration_caps = {"teams": [{"teamCapacityPerDay": 52.0, "teamTotalDaysOff": 7}]}
        ado = _stub_ado(capacities=capacities, iteration_capacities=iteration_caps)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        row = cap_df.iloc[0]
        # Must be 9 (no team days off), not 9-7=2
        assert int(row["planned_working_days"]) == 9

    def test_summary_count_stored_for_diagnostics(self):
        """iteration_summary_team_days_off_count is preserved for auditing."""
        sprint = _make_sprint(12, dt.date(2026, 5, 28), dt.date(2026, 6, 9))
        iteration_caps = {"teams": [{"teamCapacityPerDay": 52.0, "teamTotalDaysOff": 7}]}
        ado = _stub_ado(iteration_capacities=iteration_caps)
        _, cap_df, _ = build_capacity_schedule(ado, [sprint], _WORKING)
        assert int(cap_df.iloc[0]["iteration_summary_team_days_off_count"]) == 7
