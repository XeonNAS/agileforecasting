"""Tests for agile_mc.auth — app-level password gate helper."""

from __future__ import annotations

from agile_mc.auth import get_app_password


class TestGetAppPassword:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("MC_APP_PASSWORD", raising=False)
        assert get_app_password() is None

    def test_returns_none_when_empty_string(self, monkeypatch):
        monkeypatch.setenv("MC_APP_PASSWORD", "")
        assert get_app_password() is None

    def test_returns_none_when_whitespace_only(self, monkeypatch):
        monkeypatch.setenv("MC_APP_PASSWORD", "   ")
        assert get_app_password() is None

    def test_returns_password_when_set(self, monkeypatch):
        monkeypatch.setenv("MC_APP_PASSWORD", "hunter2")
        assert get_app_password() == "hunter2"

    def test_strips_surrounding_whitespace(self, monkeypatch):
        monkeypatch.setenv("MC_APP_PASSWORD", "  hunter2  ")
        assert get_app_password() == "hunter2"
