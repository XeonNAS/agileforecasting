from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def stochastic_round(x: float, rng: np.random.Generator) -> int:
    if x <= 0:
        return 0
    f = np.floor(x)
    frac = x - f
    return int(f + (1 if rng.random() < frac else 0))


def at_least_threshold(samples: np.ndarray, p: float) -> int:
    """Return N such that P(samples >= N) ~= p ("at least" semantics).
    Implemented as sorted[int((1-p)*n)].
    """
    if samples.size == 0:
        return 0
    p = float(p)
    p = min(max(p, 0.0), 1.0)
    s = np.sort(samples.astype(int))
    idx = int((1.0 - p) * len(s))
    idx = min(max(idx, 0), len(s) - 1)
    return int(s[idx])


def simulate_how_many_daily(
    history_counts: np.ndarray,
    forecast_dates: List[dt.date],
    per_date_ratio: Dict[dt.date, float],
    n_sims: int,
    seed: Optional[int] = None,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    history = history_counts.astype(float)
    if history.size == 0:
        return np.zeros(n_sims, dtype=int)

    out = np.zeros(n_sims, dtype=int)
    for i in range(n_sims):
        total = 0
        for d in forecast_dates:
            samp = float(rng.choice(history))
            ratio = float(per_date_ratio.get(d, 1.0))
            total += stochastic_round(samp * ratio, rng)
        out[i] = total
    return out


@dataclass(frozen=True)
class SprintPlanChunk:
    sprint_name: str
    sprint_num: Optional[int]
    working_dates: List[dt.date]          # working dates in this chunk (team days off already removed)
    capacity_factor: float                # planned / baseline capacity for the sprint


def simulate_how_many_sprint(
    history_sprint_counts: np.ndarray,
    plan: List[SprintPlanChunk],
    n_sims: int,
    seed: Optional[int] = None,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    hist = history_sprint_counts.astype(float)
    if hist.size == 0 or not plan:
        return np.zeros(n_sims, dtype=int)

    out = np.zeros(n_sims, dtype=int)
    for i in range(n_sims):
        total = 0
        for chunk in plan:
            samp = float(rng.choice(hist))
            # Apply sprint capacity factor (team+individual days off)
            scaled = samp * float(chunk.capacity_factor)
            # If forecast window covers only part of a sprint, scale by fraction of working days included
            # compared to that sprint's *planned working days* (chunk.working_dates is already those in window)
            # The caller should supply chunk.working_dates for that window; we use len / max(len_total,1) outside.
            # Here we assume caller pre-scaled capacity_factor to the window if needed; keep as-is.
            total += stochastic_round(scaled, rng)
        out[i] = total
    return out


def simulate_when_daily(
    history_counts: np.ndarray,
    forecast_dates: List[dt.date],
    per_date_ratio: Dict[dt.date, float],
    items_remaining: int,
    n_sims: int,
    seed: Optional[int] = None,
    max_days: int = 800,
) -> List[dt.date]:
    rng = np.random.default_rng(seed)
    history = history_counts.astype(float)
    if history.size == 0 or items_remaining <= 0:
        # Return "today" as degenerate
        return [forecast_dates[0] if forecast_dates else dt.date.today()] * n_sims

    out: List[dt.date] = []
    # Ensure we have some dates to walk; if forecast_dates is only a window, extend by repeating pattern.
    if not forecast_dates:
        forecast_dates = [dt.date.today()]

    for _ in range(n_sims):
        remaining = int(items_remaining)
        day_idx = 0
        steps = 0
        while remaining > 0 and steps < max_days:
            d = forecast_dates[min(day_idx, len(forecast_dates) - 1)]
            samp = float(rng.choice(history))
            ratio = float(per_date_ratio.get(d, 1.0))
            done = stochastic_round(samp * ratio, rng)
            remaining -= done
            if remaining <= 0:
                out.append(d)
                break
            day_idx += 1
            steps += 1

        if remaining > 0:
            # didn't finish within max_days; set to last date
            out.append(forecast_dates[-1])
    return out


def completion_cdf_by_date(completion_dates: List[dt.date], dates: List[dt.date]) -> List[float]:
    if not completion_dates:
        return [0.0] * len(dates)
    comp = np.array([d.toordinal() for d in completion_dates], dtype=int)
    out = []
    for d in dates:
        out.append(float(np.mean(comp <= d.toordinal())))
    return out
