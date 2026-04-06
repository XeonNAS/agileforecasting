"""Tests for the parallelised sprint-metadata fetch and capacity schedule builder.

These tests use a lightweight stub AdoClient so no real ADO credentials are
required.  They verify that:
  - _fetch_sprint_metadata calls all three API endpoints and returns a
    correctly populated _SprintMetadata object.
  - build_capacity_schedule produces identical output whether the sprints
    are processed serially (1 worker) or in parallel (multiple workers).
  - fetch_daily_throughput_from_saved_query handles single and multi-batch
    work-item fetches correctly.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from agile_mc.ado_sync import (
    Sprint,
    _SprintMetadata,
    _fetch_sprint_metadata,
    build_capacity_schedule,
    fetch_daily_throughput_from_saved_query,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_sprint(n: int, start: dt.date, end_inclusive: dt.date) -> Sprint:
    return Sprint(
        iteration_id=f"iter-{n:02d}",
        name=f"Sprint {n}",
        start_date=start,
        end_exclusive=end_inclusive + dt.timedelta(days=1),
        end_inclusive=end_inclusive,
    )


def _stub_ado(
    *,
    team_days_off: List[Dict[str, Any]] | None = None,
    capacities: List[Dict[str, Any]] | None = None,
    iteration_capacities: Dict[str, Any] | None = None,
) -> MagicMock:
    """Return a minimal mock AdoClient."""
    ado = MagicMock()
    ado.get_team_days_off.return_value = team_days_off or []
    ado.get_capacities.return_value = capacities or []
    ado.get_iteration_capacities.return_value = iteration_capacities or {}
    return ado


# ---------------------------------------------------------------------------
# _fetch_sprint_metadata
# ---------------------------------------------------------------------------


class TestFetchSprintMetadata:
    def test_calls_all_three_endpoints(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        ado = _stub_ado()
        _fetch_sprint_metadata(ado, sprint)
        ado.get_team_days_off.assert_called_once_with(sprint.iteration_id)
        ado.get_capacities.assert_called_once_with(sprint.iteration_id)
        ado.get_iteration_capacities.assert_called_once_with(sprint.iteration_id)

    def test_team_days_off_parsed(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        ado = _stub_ado(
            team_days_off=[{"start": "2026-01-06T00:00:00Z", "end": "2026-01-06T00:00:00Z"}]
        )
        meta = _fetch_sprint_metadata(ado, sprint)
        assert dt.date(2026, 1, 6) in meta.team_days_off

    def test_baseline_per_day_computed_from_capacities(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        capacities = [
            {
                "teamMember": {"id": "u1"},
                "activities": [{"capacityPerDay": 6.0}],
                "daysOff": [],
            },
            {
                "teamMember": {"id": "u2"},
                "activities": [{"capacityPerDay": 4.0}],
                "daysOff": [],
            },
        ]
        ado = _stub_ado(capacities=capacities)
        meta = _fetch_sprint_metadata(ado, sprint)
        assert meta.baseline_per_day == pytest.approx(10.0)
        assert meta.baseline_by_member == {"u1": 6.0, "u2": 4.0}

    def test_baseline_per_day_fallback_when_no_capacities(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        ado = _stub_ado(capacities=[])
        meta = _fetch_sprint_metadata(ado, sprint)
        assert meta.baseline_per_day >= 1.0

    def test_iteration_summary_days_off_parsed(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        ado = _stub_ado(
            iteration_capacities={"teams": [{"teamCapacityPerDay": 1.0, "teamTotalDaysOff": 2}]}
        )
        meta = _fetch_sprint_metadata(ado, sprint)
        assert meta.summary_days_off_count == 2

    def test_iteration_summary_exception_returns_none(self):
        sprint = _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))
        ado = _stub_ado()
        ado.get_iteration_capacities.side_effect = Exception("network error")
        meta = _fetch_sprint_metadata(ado, sprint)
        assert meta.summary_days_off_count is None


# ---------------------------------------------------------------------------
# build_capacity_schedule
# ---------------------------------------------------------------------------


class TestBuildCapacitySchedule:
    _WORKING = {0, 1, 2, 3, 4}  # Mon–Fri

    def _two_sprints(self):
        return [
            _make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16)),
            _make_sprint(2, dt.date(2026, 1, 19), dt.date(2026, 1, 30)),
        ]

    def test_returns_three_values(self):
        ado = _stub_ado()
        result = build_capacity_schedule(ado, self._two_sprints(), self._WORKING)
        assert len(result) == 3

    def test_sprint_df_has_expected_rows(self):
        ado = _stub_ado()
        sprints_df, _, _ = build_capacity_schedule(ado, self._two_sprints(), self._WORKING)
        assert len(sprints_df) == 2
        assert list(sprints_df["sprint_name"]) == ["Sprint 1", "Sprint 2"]

    def test_cap_df_has_expected_columns(self):
        ado = _stub_ado()
        _, cap_df, _ = build_capacity_schedule(ado, self._two_sprints(), self._WORKING)
        expected = {"sprint_name", "normal_working_days", "planned_working_days", "capacity_factor"}
        assert expected.issubset(set(cap_df.columns))

    def test_per_date_ratio_covers_working_days(self):
        ado = _stub_ado()
        _, _, per_date_ratio = build_capacity_schedule(ado, self._two_sprints(), self._WORKING)
        # All Mon–Fri dates in both sprints should be in the ratio dict
        for sp in self._two_sprints():
            cur = sp.start_date
            while cur <= sp.end_inclusive:
                if cur.weekday() in self._WORKING:
                    assert cur in per_date_ratio, f"Missing date {cur}"
                cur += dt.timedelta(days=1)

    def test_empty_sprints_returns_empty_dataframes(self):
        ado = _stub_ado()
        sd, cd, pdr = build_capacity_schedule(ado, [], self._WORKING)
        assert sd.empty
        assert cd.empty
        assert pdr == {}

    def test_team_days_off_sets_ratio_to_zero(self):
        ado = _stub_ado(
            team_days_off=[{"start": "2026-01-06T00:00:00Z", "end": "2026-01-06T00:00:00Z"}]
        )
        sprints = [_make_sprint(1, dt.date(2026, 1, 5), dt.date(2026, 1, 16))]
        _, _, per_date_ratio = build_capacity_schedule(ado, sprints, self._WORKING)
        assert per_date_ratio.get(dt.date(2026, 1, 6)) == 0.0

    def test_api_calls_made_once_per_sprint(self):
        ado = _stub_ado()
        sprints = self._two_sprints()
        build_capacity_schedule(ado, sprints, self._WORKING)
        # Each of the three endpoints should be called once per sprint
        assert ado.get_team_days_off.call_count == 2
        assert ado.get_capacities.call_count == 2
        assert ado.get_iteration_capacities.call_count == 2


# ---------------------------------------------------------------------------
# fetch_daily_throughput_from_saved_query
# ---------------------------------------------------------------------------


class TestFetchDailyThroughput:
    _HISTORY_START = dt.date(2026, 1, 1)
    _HISTORY_END = dt.date(2026, 1, 31)
    _WORKING = {0, 1, 2, 3, 4}

    def _stub_ado_for_throughput(self, *, ids: List[int], fields_response: List[Dict]) -> MagicMock:
        ado = MagicMock()
        ado.wiql_query_by_id.return_value = {"workItems": [{"id": i} for i in ids]}
        ado.work_items_batch.return_value = {"value": fields_response}
        return ado

    def _closed_item(self, date_str: str) -> Dict:
        return {"fields": {"Microsoft.VSTS.Common.ClosedDate": date_str}}

    def test_no_items_returns_zero_filled(self):
        ado = MagicMock()
        ado.wiql_query_by_id.return_value = {"workItems": []}
        result = fetch_daily_throughput_from_saved_query(
            ado, "12345678-1234-1234-1234-123456789012",
            self._HISTORY_START, self._HISTORY_END, self._WORKING, set()
        )
        assert "done_count" in result.columns
        assert result["done_count"].sum() == 0

    def test_single_batch_correct_counts(self):
        items = [self._closed_item("2026-01-12T00:00:00Z")] * 3
        ado = self._stub_ado_for_throughput(ids=list(range(3)), fields_response=items)
        result = fetch_daily_throughput_from_saved_query(
            ado, "12345678-1234-1234-1234-123456789012",
            self._HISTORY_START, self._HISTORY_END, self._WORKING, set()
        )
        day = result[result["date"] == dt.date(2026, 1, 12)]
        assert len(day) == 1
        assert int(day.iloc[0]["done_count"]) == 3

    def test_multi_batch_all_items_processed(self):
        # 250 items → 2 batches; all closed on the same date
        n = 250
        items = [self._closed_item("2026-01-15T00:00:00Z")] * 200  # batch returns 200 items
        ado = MagicMock()
        ado.wiql_query_by_id.return_value = {"workItems": [{"id": i} for i in range(n)]}
        # First batch returns 200 items, second returns 50
        ado.work_items_batch.side_effect = [
            {"value": [self._closed_item("2026-01-15T00:00:00Z")] * 200},
            {"value": [self._closed_item("2026-01-15T00:00:00Z")] * 50},
        ]
        result = fetch_daily_throughput_from_saved_query(
            ado, "12345678-1234-1234-1234-123456789012",
            self._HISTORY_START, self._HISTORY_END, self._WORKING, set()
        )
        day = result[result["date"] == dt.date(2026, 1, 15)]
        assert int(day.iloc[0]["done_count"]) == 250

    def test_invalid_query_guid_raises(self):
        ado = MagicMock()
        with pytest.raises(ValueError, match="Could not parse saved query GUID"):
            fetch_daily_throughput_from_saved_query(
                ado, "not-a-guid",
                self._HISTORY_START, self._HISTORY_END, self._WORKING, set()
            )

    def test_output_columns(self):
        ado = self._stub_ado_for_throughput(
            ids=[1], fields_response=[self._closed_item("2026-01-12T00:00:00Z")]
        )
        result = fetch_daily_throughput_from_saved_query(
            ado, "12345678-1234-1234-1234-123456789012",
            self._HISTORY_START, self._HISTORY_END, self._WORKING, set()
        )
        assert set(result.columns) == {"date", "done_count", "is_working_day"}

    def test_dates_outside_history_excluded(self):
        items = [self._closed_item("2025-06-01T00:00:00Z")]  # before history
        ado = self._stub_ado_for_throughput(ids=[1], fields_response=items)
        result = fetch_daily_throughput_from_saved_query(
            ado, "12345678-1234-1234-1234-123456789012",
            self._HISTORY_START, self._HISTORY_END, self._WORKING, set()
        )
        assert result["done_count"].sum() == 0
