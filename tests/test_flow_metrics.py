"""Tests for agile_mc.flow_metrics — cycle time and before/after-Claude comparison."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from agile_mc.flow_metrics import (
    BEFORE_CLAUDE,
    SINCE_CLAUDE,
    claude_period,
    created_vs_finished,
    cycle_time_days,
    describe_backlog_change,
    improvement_summary,
    with_cycle_time,
)

_CLAUDE_START = dt.date(2026, 8, 1)


class TestCycleTimeDays:
    def test_same_day_counts_as_one_day(self):
        assert cycle_time_days(dt.date(2026, 8, 3), dt.date(2026, 8, 3)) == 1

    def test_counts_both_start_and_end_days(self):
        assert cycle_time_days(dt.date(2026, 8, 3), dt.date(2026, 8, 6)) == 4

    def test_missing_activated_date_has_no_cycle_time(self):
        assert cycle_time_days(None, dt.date(2026, 8, 6)) is None

    def test_activated_after_done_has_no_cycle_time(self):
        assert cycle_time_days(dt.date(2026, 8, 7), dt.date(2026, 8, 6)) is None


class TestWithCycleTime:
    def test_adds_cycle_time_column(self):
        items = pd.DataFrame(
            {"work_item_id": [1], "activated_date": [dt.date(2026, 8, 3)], "done_date": [dt.date(2026, 8, 6)]}
        )

        assert with_cycle_time(items)["cycle_time_days"].tolist() == [4]


class TestClaudePeriod:
    def test_dates_before_start_are_before_claude(self):
        result = claude_period(pd.Series([dt.date(2026, 7, 31)]), _CLAUDE_START)

        assert result.tolist() == [BEFORE_CLAUDE]

    def test_start_date_itself_is_since_claude(self):
        result = claude_period(pd.Series([_CLAUDE_START]), _CLAUDE_START)

        assert result.tolist() == [SINCE_CLAUDE]


def _sprints() -> pd.DataFrame:
    starts = [dt.date(2026, 6, 4), dt.date(2026, 6, 18), dt.date(2026, 7, 2), dt.date(2026, 8, 6), dt.date(2026, 8, 20)]
    return pd.DataFrame({"start_date": starts, "done_count": [4, 6, 20, 10, 12]})


def _items() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "done_date": [dt.date(2026, 7, 10)] * 3 + [dt.date(2026, 8, 25)] * 2,
            "cycle_time_days": [10.0, 20.0, 30.0, 4.0, 6.0],
        }
    )


class TestImprovementSummary:
    def test_median_sprint_throughput_per_period(self):
        summary = improvement_summary(_sprints(), _items(), _CLAUDE_START)

        assert summary["Median done per sprint"].tolist() == [6.0, 11.0]

    def test_counts_sprints_per_period(self):
        summary = improvement_summary(_sprints(), _items(), _CLAUDE_START)

        assert summary["Sprints"].tolist() == [3, 2]

    def test_median_cycle_time_per_period(self):
        summary = improvement_summary(_sprints(), _items(), _CLAUDE_START)

        assert summary["Median cycle time (days)"].tolist() == [20.0, 5.0]

    def test_85th_percentile_cycle_time_per_period(self):
        summary = improvement_summary(_sprints(), _items(), _CLAUDE_START)

        assert summary["85th percentile cycle time (days)"].tolist() == pytest.approx([27.0, 5.7])

    def test_items_without_cycle_time_are_excluded(self):
        items = pd.concat([_items(), pd.DataFrame({"done_date": [dt.date(2026, 8, 25)], "cycle_time_days": [None]})])

        summary = improvement_summary(_sprints(), items, _CLAUDE_START)

        assert summary["Items with cycle time"].tolist() == [3, 2]


def _two_sprints() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sprint_name": ["Sprint 1", "Sprint 2"],
            "start_date": [dt.date(2026, 9, 3), dt.date(2026, 9, 17)],
            "end_date": [dt.date(2026, 9, 16), dt.date(2026, 9, 30)],
            "done_count": [5, 8],
        }
    )


def _created(dates: list[dt.date]) -> pd.DataFrame:
    return pd.DataFrame({"work_item_id": range(len(dates)), "created_date": dates})


class TestCreatedVsFinished:
    def test_counts_items_created_in_each_sprint(self):
        created = _created([dt.date(2026, 9, 3), dt.date(2026, 9, 16), dt.date(2026, 9, 17)])

        result = created_vs_finished(_two_sprints(), created)

        assert result["created_count"].tolist() == [2, 1]

    def test_ignores_items_created_outside_the_sprints(self):
        created = _created([dt.date(2026, 9, 2), dt.date(2026, 10, 1)])

        result = created_vs_finished(_two_sprints(), created)

        assert result["created_count"].tolist() == [0, 0]

    def test_net_change_is_created_minus_finished(self):
        created = _created([dt.date(2026, 9, 4)] * 9 + [dt.date(2026, 9, 18)] * 3)

        result = created_vs_finished(_two_sprints(), created)

        assert result["net_change"].tolist() == [4, -5]

    def test_cumulative_net_change_running_total(self):
        created = _created([dt.date(2026, 9, 4)] * 9 + [dt.date(2026, 9, 18)] * 3)

        result = created_vs_finished(_two_sprints(), created)

        assert result["cumulative_net_change"].tolist() == [4, -1]


class TestDescribeBacklogChange:
    def _flow(self, created: list[int], done: list[int]) -> pd.DataFrame:
        return pd.DataFrame({"created_count": created, "done_count": done})

    def test_reports_backlog_growth(self):
        assert describe_backlog_change(self._flow([9, 6], [5, 4])).endswith("the backlog grew by 6.")

    def test_reports_backlog_shrinking(self):
        assert describe_backlog_change(self._flow([3, 2], [5, 4])).endswith("the backlog shrank by 4.")

    def test_reports_no_net_change(self):
        assert describe_backlog_change(self._flow([5], [5])).endswith("no net change.")
