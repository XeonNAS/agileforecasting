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
