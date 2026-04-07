"""Regression tests for the When-forecast calendar layout.

These tests verify that the context-block annotation (the ADO/details summary
box) never overlaps the first calendar row, for a range of month counts.

The overlap bug was caused by _prepared_export_figure() in chart_export.py
overriding the calendar figure's computed width/height/margins with generic
fixed values.  That changed paper_h (plot-area height in pixels) from the value
used to compute annotation y-coordinates, pushing the annotation bottom below
y=1.0 (the top of the plot area) and into the first row of tiles.

The fix:
  1. export_plotly_figure(..., preserve_layout=True) skips the dimension override
     for the calendar figure.
  2. The annotation height estimate in calendar_export.py was corrected to
     include the full borderpad overhead (2×borderpad + 2×borderwidth = 22 px),
     plus a safety buffer of 12 px.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from agile_mc.calendar_export import build_when_calendar_figure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dummy_completion_dates(n: int = 200, seed: int = 42) -> list[dt.date]:
    """Generate *n* completion dates spread over ~18 months from a fixed start."""
    rng = random.Random(seed)
    start = dt.date(2026, 4, 7)
    return sorted(start + dt.timedelta(days=rng.randint(1, 550)) for _ in range(n))


_CONTEXT_LINES = [
    "ADO: myorg/myproject/myteam",
    "Start date: 2026-04-07",
    "Items remaining: 42",
    "Forecast basis: throughput",
    "Simulations: 10000",
    "Generated: 2026-04-07 12:00",
    "Seed: 42",  # worst-case: 7 lines (most text, tallest annotation)
]


# ---------------------------------------------------------------------------
# Layout invariant tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("months", [3, 6, 9, 12])
def test_context_annotation_stays_in_top_margin(months: int) -> None:
    """Context annotation top must be above y=1.0 and fit within the top margin.

    With preserve_layout=True (which is how the app exports the calendar) the
    figure's own paper_h is used.  The annotation y-coordinates were computed
    for exactly that paper_h, so the bottom should land well above y=1.0.
    """
    completion_dates = _dummy_completion_dates()
    start = dt.date(2026, 4, 7)

    fig = build_when_calendar_figure(
        completion_dates=completion_dates,
        sprint_label_by_date={},
        months_to_show=months,
        start_date=start,
        cols=4,
        title="When forecast calendar",
        context_lines=_CONTEXT_LINES,
    )

    layout = fig.layout
    T: int = int(layout.margin.t)
    B: int = int(layout.margin.b)
    fig_height: int = int(layout.height)
    # paper_h = height of the actual plot area (inside margins)
    paper_h: int = fig_height - T - B
    assert paper_h > 0, f"months={months}: paper_h must be positive, got {paper_h}"

    # Find the context annotation: xanchor="left", x=0.0
    ctx_ann = next(
        (a for a in layout.annotations if getattr(a, "xanchor", None) == "left"),
        None,
    )
    assert ctx_ann is not None, f"months={months}: context annotation not found in figure"

    # --- Top of annotation must be in the top margin (y > 1.0) ---
    top_y: float = float(ctx_ann.y)
    assert top_y > 1.0, (
        f"months={months}: context annotation top y={top_y:.4f} is not above "
        f"the plot area (y=1.0)"
    )

    # Convert paper-coord top to pixels from figure top:
    #   pixel_from_top = T - (y - 1.0) * paper_h
    top_px_from_top = T - (top_y - 1.0) * paper_h
    assert top_px_from_top >= 0, (
        f"months={months}: annotation top ({top_px_from_top:.1f} px) clips outside the figure"
    )

    # --- Estimated bottom of annotation must still be within the top margin ---
    # Estimate annotation height the same way calendar_export does, plus borderpad overhead.
    text: str = getattr(ctx_ann, "text", "") or ""
    line_count = text.count("<br>") + 1
    borderpad = 10  # must match calendar_export.py
    est_height_px = line_count * 16 + 2 * borderpad + 2

    bottom_px_from_top = top_px_from_top + est_height_px
    assert bottom_px_from_top <= T, (
        f"months={months}: annotation bottom (~{bottom_px_from_top:.0f} px from top) "
        f"exceeds the top margin ({T} px) — it would overlap the first calendar row. "
        f"est_height_px={est_height_px}, top_px_from_top={top_px_from_top:.1f}"
    )


@pytest.mark.parametrize("months", [3, 6, 9, 12])
def test_title_annotation_stays_in_top_margin(months: int) -> None:
    """Title annotation must also fit within the top margin."""
    completion_dates = _dummy_completion_dates()
    start = dt.date(2026, 4, 7)

    fig = build_when_calendar_figure(
        completion_dates=completion_dates,
        sprint_label_by_date={},
        months_to_show=months,
        start_date=start,
        cols=4,
        title="When forecast calendar",
        context_lines=_CONTEXT_LINES,
    )

    layout = fig.layout
    T = int(layout.margin.t)
    B = int(layout.margin.b)
    paper_h = int(layout.height) - T - B

    # Title annotation: xanchor="center", x=0.5
    title_ann = next(
        (a for a in layout.annotations if getattr(a, "xanchor", None) == "center" and
         getattr(a, "x", None) == 0.5 and float(getattr(a, "y", 0)) > 1.0),
        None,
    )
    assert title_ann is not None, f"months={months}: title annotation not found"

    top_px_from_top = T - (float(title_ann.y) - 1.0) * paper_h
    assert 0 <= top_px_from_top <= T, (
        f"months={months}: title annotation top ({top_px_from_top:.1f} px) "
        f"is outside the top margin ({T} px)"
    )


@pytest.mark.parametrize("months", [3, 6, 9, 12])
def test_screen_mode_has_no_header_annotation(months: int) -> None:
    """Screen rendering (no context_lines) must produce no header annotation above y=1.0."""
    completion_dates = _dummy_completion_dates()
    start = dt.date(2026, 4, 7)

    fig = build_when_calendar_figure(
        completion_dates=completion_dates,
        sprint_label_by_date={},
        months_to_show=months,
        start_date=start,
        cols=3,
        # No context_lines → screen mode
    )

    # Export-header annotations use yanchor="top".  Month-title annotations use
    # yanchor="bottom" and may also sit slightly above y=1.0 (in the inter-row
    # gap) — those are fine and expected.
    header_anns = [
        a for a in fig.layout.annotations
        if float(getattr(a, "y", 0)) > 1.0 and getattr(a, "yanchor", None) == "top"
    ]
    assert not header_anns, (
        f"months={months}: screen mode should have no export-header annotations "
        f"(yanchor=top, y>1.0), got {header_anns}"
    )


@pytest.mark.parametrize("months", [3, 6, 9, 12])
def test_figure_height_accommodates_tiles(months: int) -> None:
    """Paper height must be at least 6 × _TILE_H_PX (one row of week tiles)."""
    _TILE_H_PX = 44  # must match calendar_export.py
    completion_dates = _dummy_completion_dates()
    start = dt.date(2026, 4, 7)

    for context in [None, _CONTEXT_LINES]:
        fig = build_when_calendar_figure(
            completion_dates=completion_dates,
            sprint_label_by_date={},
            months_to_show=months,
            start_date=start,
            cols=4,
            title="When forecast calendar",
            context_lines=context,
        )
        layout = fig.layout
        T = int(layout.margin.t)
        B = int(layout.margin.b)
        paper_h = int(layout.height) - T - B
        assert paper_h >= 6 * _TILE_H_PX, (
            f"months={months} context={'yes' if context else 'no'}: "
            f"paper_h={paper_h} < min {6*_TILE_H_PX} px"
        )
