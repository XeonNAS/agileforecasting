import datetime as dt

import numpy as np
import pandas as pd

from agile_mc.plots import (
    cycle_time_figure,
    how_many_figures,
    sprint_moving_average,
    sprint_throughput_figure,
    sprint_trendline,
)


def _sprint_df(done_counts):
    start = dt.date(2026, 1, 5)
    rows = []
    for i, done in enumerate(done_counts):
        sprint_start = start + dt.timedelta(days=14 * i)
        rows.append(
            {
                "iteration_id": f"it-{i}",
                "sprint_name": f"Sprint {i + 1}",
                "sprint_num": i + 1,
                "start_date": sprint_start,
                "end_date": sprint_start + dt.timedelta(days=13),
                "done_count": done,
            }
        )
    return pd.DataFrame(rows)


def test_moving_average_is_blank_until_three_sprints_exist():
    result = sprint_moving_average(pd.Series([5, 30, 10]))

    assert result.iloc[:2].isna().all()


def test_moving_average_uses_trailing_three_sprints():
    result = sprint_moving_average(pd.Series([5, 30, 10, 11, 7, 15]))

    np.testing.assert_allclose(result.iloc[2:].to_numpy(), [15.0, 17.0, 28 / 3, 11.0])


def test_trendline_fits_a_straight_line_through_linear_data():
    result = sprint_trendline(pd.Series([2, 4, 6]))

    np.testing.assert_allclose(result.to_numpy(), [2.0, 4.0, 6.0])


def test_trendline_slope_follows_least_squares_fit():
    result = sprint_trendline(pd.Series([5, 30, 10, 11, 7, 15]))

    np.testing.assert_allclose(result.iloc[1] - result.iloc[0], -9 / 17.5)


def test_trendline_is_blank_for_a_single_sprint():
    result = sprint_trendline(pd.Series([5]))

    assert result.isna().all()


def test_figure_includes_trend_line():
    fig = sprint_throughput_figure(_sprint_df([5, 30, 10]))

    assert fig.data[2].name == "Trend"


def test_figure_plots_every_sprint_as_a_bar():
    fig = sprint_throughput_figure(_sprint_df([5, 30, 10, 11, 7, 15]))

    assert list(fig.data[0].y) == [5, 30, 10, 11, 7, 15]


def test_figure_orders_sprints_by_start_date():
    df = _sprint_df([5, 30, 10]).iloc[::-1]

    fig = sprint_throughput_figure(df)

    assert list(fig.data[0].x) == ["Sprint 1", "Sprint 2", "Sprint 3"]


def test_figure_names_the_moving_average_line():
    fig = sprint_throughput_figure(_sprint_df([5, 30, 10]))

    assert fig.data[1].name == "3-sprint moving average"


def test_how_many_figures_omit_at_most_chart():
    figs = how_many_figures(np.array([3, 5, 8]))

    assert list(figs.keys()) == ["How Many - At Least Chance", "How Many - Exact Probability"]


def _trace_names(fig):
    return [t.name for t in fig.data]


def test_sprint_figure_without_claude_start_has_no_period_medians():
    fig = sprint_throughput_figure(_sprint_df([5, 30, 10, 11]))

    assert "Median since Claude" not in _trace_names(fig)


def test_sprint_figure_shows_median_since_claude_start():
    df = _sprint_df([5, 30, 10, 11])

    fig = sprint_throughput_figure(df, claude_start=df["start_date"].iloc[2])

    assert list(fig.data[_trace_names(fig).index("Median since Claude")].y) == [10.5, 10.5]


def test_sprint_figure_marks_claude_start_between_sprints():
    df = _sprint_df([5, 30, 10, 11])

    fig = sprint_throughput_figure(df, claude_start=df["start_date"].iloc[2])

    assert fig.layout.shapes[0].x0 == 1.5


def _cycle_items():
    return pd.DataFrame(
        {
            "work_item_id": [1, 2, 3, 4],
            "done_date": [dt.date(2026, 7, 10), dt.date(2026, 7, 20), dt.date(2026, 8, 10), dt.date(2026, 8, 20)],
            "cycle_time_days": [10.0, 20.0, 4.0, 6.0],
        }
    )


def test_cycle_time_figure_plots_each_item():
    fig = cycle_time_figure(_cycle_items())

    assert list(fig.data[0].y) == [10.0, 20.0, 4.0, 6.0]


def test_cycle_time_figure_skips_items_without_cycle_time():
    items = pd.concat(
        [
            _cycle_items(),
            pd.DataFrame({"work_item_id": [5], "done_date": [dt.date(2026, 8, 21)], "cycle_time_days": [None]}),
        ]
    )

    fig = cycle_time_figure(items)

    assert len(fig.data[0].y) == 4


def test_cycle_time_figure_splits_85th_percentile_by_claude_start():
    fig = cycle_time_figure(_cycle_items(), claude_start=dt.date(2026, 8, 1))

    assert "85th percentile since Claude" in _trace_names(fig)
