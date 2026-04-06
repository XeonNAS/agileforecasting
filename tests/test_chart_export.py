"""Tests for chart_export.export_plotly_figure.

These tests require Chrome/kaleido to be available (same requirement as the
production export path).  They are intentionally integration-level: mocking
kaleido internals would mask the exact failure we experienced in production.
"""

from __future__ import annotations

import pytest
import plotly.graph_objects as go

from agile_mc.chart_export import ChartExportResult, export_plotly_figure


def _simple_figure(title: str = "Test chart") -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=[1, 2, 3], y=[4, 5, 6], name="series")
    fig.update_layout(title_text=title)
    return fig


def test_export_png_returns_bytes():
    fig = _simple_figure()
    result = export_plotly_figure(fig, fmt="png", base_name="test_chart")
    assert isinstance(result, ChartExportResult)
    assert result.filename == "test_chart.png"
    assert result.mime == "image/png"
    assert len(result.data) > 1000, "PNG should have substantial byte content"
    # PNG files start with the PNG magic bytes
    assert result.data[:4] == b"\x89PNG", "Exported data should be a valid PNG"


def test_export_svg_returns_bytes():
    fig = _simple_figure()
    result = export_plotly_figure(fig, fmt="svg", base_name="test_chart")
    assert isinstance(result, ChartExportResult)
    assert result.filename == "test_chart.svg"
    assert result.mime == "image/svg+xml"
    assert len(result.data) > 100
    assert b"<svg" in result.data, "Exported data should contain SVG markup"


def test_export_unknown_fmt_falls_back_to_png():
    fig = _simple_figure()
    result = export_plotly_figure(fig, fmt="pdf", base_name="test")
    assert result.filename == "test.png"
    assert result.mime == "image/png"


def test_export_preserves_no_secrets_in_filename():
    """Sanity check: filename is derived from base_name, not from figure data."""
    fig = _simple_figure(title="chart with spaces")
    result = export_plotly_figure(fig, fmt="png", base_name="safe_name")
    assert result.filename == "safe_name.png"
