from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd

BEFORE_CLAUDE = "Before Claude"
SINCE_CLAUDE = "Since Claude"
CYCLE_TIME_PERCENTILE = 85


def cycle_time_days(activated: Optional[dt.date], done: Optional[dt.date]) -> Optional[int]:
    """Calendar days from activation to done, counting both ends (same-day = 1)."""
    if activated is None or done is None or pd.isna(activated) or activated > done:
        return None
    return (done - activated).days + 1


def with_cycle_time(done_items: pd.DataFrame) -> pd.DataFrame:
    out = done_items.copy()
    out["cycle_time_days"] = pd.array(
        [cycle_time_days(a, d) for a, d in zip(out["activated_date"], out["done_date"])],
        dtype="Int64",
    )
    return out


def claude_period(dates: pd.Series, claude_start: dt.date) -> pd.Series:
    return pd.Series(
        np.where(pd.to_datetime(dates).dt.date >= claude_start, SINCE_CLAUDE, BEFORE_CLAUDE),
        index=dates.index,
    )


def created_vs_finished(sprint_df: pd.DataFrame, created_items: pd.DataFrame) -> pd.DataFrame:
    """Per sprint: items created (by created date, inclusive of both sprint ends) against done_count.

    net_change > 0 means the backlog grew that sprint; cumulative_net_change is the running total.
    """
    created_dates = pd.to_datetime(created_items["created_date"]).dt.date
    out = sprint_df.reset_index(drop=True).copy()
    out["created_count"] = [
        int(created_dates.between(start, end).sum()) for start, end in zip(out["start_date"], out["end_date"])
    ]
    out["net_change"] = out["created_count"] - out["done_count"].astype(int)
    out["cumulative_net_change"] = out["net_change"].cumsum()
    return out


def describe_backlog_change(flow_df: pd.DataFrame) -> str:
    created = int(flow_df["created_count"].sum())
    finished = int(flow_df["done_count"].sum())
    growth = created - finished
    if growth > 0:
        outcome = f"the backlog grew by {growth}"
    elif growth < 0:
        outcome = f"the backlog shrank by {-growth}"
    else:
        outcome = "no net change"
    return f"Over these {len(flow_df)} sprints: {created} items created, {finished} finished — {outcome}."


def improvement_summary(sprint_df: pd.DataFrame, items_df: pd.DataFrame, claude_start: dt.date) -> pd.DataFrame:
    """Before/since comparison: sprints are assigned by start date, items by done date."""
    sprint_period = claude_period(sprint_df["start_date"], claude_start)
    timed = items_df.dropna(subset=["cycle_time_days"])
    item_period = claude_period(timed["done_date"], claude_start)

    rows = []
    for period in (BEFORE_CLAUDE, SINCE_CLAUDE):
        done = sprint_df.loc[sprint_period == period, "done_count"].astype(float)
        cycle = timed.loc[item_period == period, "cycle_time_days"].astype(float)
        rows.append(
            {
                "Period": period,
                "Sprints": int(done.size),
                "Median done per sprint": float(done.median()) if done.size else np.nan,
                "Items with cycle time": int(cycle.size),
                "Median cycle time (days)": float(cycle.median()) if cycle.size else np.nan,
                f"{CYCLE_TIME_PERCENTILE}th percentile cycle time (days)": (
                    float(np.percentile(cycle, CYCLE_TIME_PERCENTILE)) if cycle.size else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)
