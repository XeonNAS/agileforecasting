"""Tests for pure utility functions in agile_mc.ado_sync."""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from agile_mc.ado_sync import (
    SavedQueryParseError,
    describe_ado_sync_error,
    expand_ado_date_range,
    extract_sprint_number,
    handle_ado_sync_exception,
    iter_dates,
    parse_ado_dt,
    parse_query_id_from_url_or_guid,
    validate_saved_query,
    weekday_indexes_from_team_settings,
)

# A canonical Azure DevOps query GUID used across the parsing tests.
_GUID = "12345678-9abc-def0-1234-56789abcdef0"


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


class TestParseQueryIdFromUrlOrGuid:
    def test_raw_guid(self):
        assert parse_query_id_from_url_or_guid(_GUID) == _GUID

    def test_raw_guid_uppercase_is_lowercased(self):
        assert parse_query_id_from_url_or_guid(_GUID.upper()) == _GUID

    def test_raw_guid_with_surrounding_whitespace(self):
        assert parse_query_id_from_url_or_guid(f"  {_GUID}  ") == _GUID

    def test_queries_query_path(self):
        url = f"https://dev.azure.com/org/proj/_queries/query/{_GUID}/"
        assert parse_query_id_from_url_or_guid(url) == _GUID

    def test_queries_query_edit_path(self):
        url = f"https://dev.azure.com/org/proj/_queries/query-edit/{_GUID}"
        assert parse_query_id_from_url_or_guid(url) == _GUID

    def test_query_id_query_string(self):
        url = f"https://dev.azure.com/org/proj/_queries/?queryId={_GUID}"
        assert parse_query_id_from_url_or_guid(url) == _GUID

    def test_id_query_string(self):
        url = f"https://dev.azure.com/org/proj/_queries/query/?id={_GUID}"
        assert parse_query_id_from_url_or_guid(url) == _GUID

    def test_guid_with_extra_path_and_query_params(self):
        url = f"https://dev.azure.com/org/proj/_queries/query/Shared%20Queries/{_GUID}/?fullScreen=true&tempQueryId=abc"
        assert parse_query_id_from_url_or_guid(url) == _GUID

    def test_url_encoded_query_id(self):
        # %2D is an encoded hyphen — the GUID is only recognisable after decoding.
        encoded = _GUID.replace("-", "%2D")
        url = f"https://dev.azure.com/org/proj/_queries/?queryId={encoded}"
        assert parse_query_id_from_url_or_guid(url) == _GUID

    def test_visualstudio_host_variant(self):
        url = f"https://org.visualstudio.com/proj/_queries/query/{_GUID}/"
        assert parse_query_id_from_url_or_guid(url) == _GUID

    def test_invalid_text_returns_none(self):
        assert parse_query_id_from_url_or_guid("not a query at all") is None

    def test_backlog_url_without_guid_returns_none(self):
        assert parse_query_id_from_url_or_guid("https://dev.azure.com/org/proj/_backlogs/board") is None

    def test_empty_and_none_return_none(self):
        assert parse_query_id_from_url_or_guid("") is None
        assert parse_query_id_from_url_or_guid(None) is None  # type: ignore[arg-type]


class TestValidateSavedQuery:
    def test_valid_returns_guid(self):
        url = f"https://dev.azure.com/org/proj/_queries/query/{_GUID}/"
        assert validate_saved_query(url) == _GUID

    def test_invalid_raises_saved_query_parse_error(self):
        with pytest.raises(SavedQueryParseError) as ei:
            validate_saved_query("https://dev.azure.com/org/proj/_backlogs/board")
        # Message points the user at the query URL, not connection settings.
        msg = str(ei.value)
        assert "Saved query URL could not be recognised" in msg
        assert "connection" not in msg.lower()

    def test_saved_query_parse_error_is_value_error(self):
        # Existing ``except ValueError`` handlers must keep catching it.
        assert issubclass(SavedQueryParseError, ValueError)


class TestAdoSyncErrorClassification:
    def test_saved_query_parse_error_message_is_precise(self):
        msg = describe_ado_sync_error(SavedQueryParseError("Saved query URL could not be recognised."))
        assert "Saved query URL could not be recognised" in msg
        assert "connection" not in msg.lower()

    def test_value_error_not_labelled_connection_failure(self):
        msg = describe_ado_sync_error(ValueError("boom"), log_path="/tmp/app.log")
        assert "connection settings" not in msg.lower()
        assert "data-processing error" in msg

    def test_handle_logs_traceback_and_returns_message(self, caplog):
        log = logging.getLogger("test.ado.sync")
        try:
            raise ValueError("downstream parse boom")
        except ValueError as e:
            with caplog.at_level(logging.ERROR, logger="test.ado.sync"):
                msg = handle_ado_sync_exception(e, log, log_path="/tmp/app.log")

        # The full traceback is logged (exc_info present) at ERROR level...
        records = [r for r in caplog.records if r.name == "test.ado.sync"]
        assert records, "expected an ERROR record to be logged"
        assert any(r.levelno == logging.ERROR and r.exc_info is not None for r in records)
        # ...and the user message does not mislabel it as a connection failure.
        assert "connection settings" not in msg.lower()

    def test_handle_saved_query_error_does_not_blame_connection(self, caplog):
        log = logging.getLogger("test.ado.sync.sq")
        try:
            validate_saved_query("no guid here")
        except SavedQueryParseError as e:
            with caplog.at_level(logging.ERROR, logger="test.ado.sync.sq"):
                msg = handle_ado_sync_exception(e, log, log_path="/tmp/app.log")

        assert "Saved query URL could not be recognised" in msg
        assert "connection" not in msg.lower()
        assert any(r.exc_info is not None for r in caplog.records if r.name == "test.ado.sync.sq")
