"""Tests for pure utility functions in agile_mc.ado_sync."""

from __future__ import annotations

import datetime as dt

from agile_mc.ado_sync import (
    expand_ado_date_range,
    extract_sprint_number,
    iter_dates,
    parse_ado_dt,
    weekday_indexes_from_team_settings,
)


class TestParseAdoDt:
    def test_none_returns_none(self):
        assert parse_ado_dt(None) is None

    def test_empty_string_returns_none(self):
        assert parse_ado_dt("") is None

    def test_utc_z_suffix(self):
        result = parse_ado_dt("2026-03-15T00:00:00Z")
        assert result is not None
        assert result.date() == dt.date(2026, 3, 15)

    def test_with_offset(self):
        result = parse_ado_dt("2026-04-01T09:00:00+10:00")
        assert result is not None
        assert result.date() == dt.date(2026, 4, 1)


class TestExpandAdoDateRange:
    def test_single_day(self):
        d = dt.datetime(2026, 3, 10)
        result = expand_ado_date_range(d, d)
        assert result == [dt.date(2026, 3, 10)]

    def test_two_days_inclusive(self):
        start = dt.datetime(2026, 3, 10)
        end = dt.datetime(2026, 3, 11)
        result = expand_ado_date_range(start, end)
        assert result == [dt.date(2026, 3, 10), dt.date(2026, 3, 11)]

    def test_reversed_start_end_handled(self):
        # end < start should still produce correct range
        start = dt.datetime(2026, 3, 12)
        end = dt.datetime(2026, 3, 10)
        result = expand_ado_date_range(start, end)
        assert result == [dt.date(2026, 3, 10), dt.date(2026, 3, 11), dt.date(2026, 3, 12)]

    def test_multiday_range_length(self):
        start = dt.datetime(2026, 4, 1)
        end = dt.datetime(2026, 4, 7)
        result = expand_ado_date_range(start, end)
        assert len(result) == 7
        assert result[0] == dt.date(2026, 4, 1)
        assert result[-1] == dt.date(2026, 4, 7)


class TestWeekdayIndexes:
    def test_standard_workweek(self):
        days = ["monday", "tuesday", "wednesday", "thursday", "friday"]
        result = weekday_indexes_from_team_settings(days)
        assert result == {0, 1, 2, 3, 4}

    def test_case_insensitive(self):
        result = weekday_indexes_from_team_settings(["Monday", "FRIDAY"])
        assert result == {0, 4}

    def test_unknown_day_ignored(self):
        result = weekday_indexes_from_team_settings(["monday", "holiday"])
        assert result == {0}

    def test_empty_list(self):
        assert weekday_indexes_from_team_settings([]) == set()

    def test_full_week(self):
        all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        assert weekday_indexes_from_team_settings(all_days) == {0, 1, 2, 3, 4, 5, 6}


class TestIterDates:
    def test_single_day(self):
        d = dt.date(2026, 1, 1)
        result = list(iter_dates(d, d))
        assert result == [d]

    def test_three_days(self):
        start = dt.date(2026, 1, 1)
        end = dt.date(2026, 1, 3)
        result = list(iter_dates(start, end))
        assert result == [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)]

    def test_end_before_start_yields_nothing(self):
        result = list(iter_dates(dt.date(2026, 1, 5), dt.date(2026, 1, 1)))
        assert result == []


class TestExtractSprintNumber:
    def test_simple_number(self):
        assert extract_sprint_number("Sprint 42") == 42

    def test_leading_number(self):
        assert extract_sprint_number("42 - My Sprint") == 42

    def test_no_number_returns_none(self):
        assert extract_sprint_number("No digits here") is None

    def test_empty_string_returns_none(self):
        assert extract_sprint_number("") is None

    def test_none_returns_none(self):
        assert extract_sprint_number(None) is None  # type: ignore[arg-type]

    def test_multiple_numbers_picks_first(self):
        assert extract_sprint_number("Sprint 3 week 2") == 3
