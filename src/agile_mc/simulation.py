from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional

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
    working_dates: List[dt.date]  # working dates in this chunk (team days off already removed)
    capacity_factor: float  # planned / baseline capacity for the sprint


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
            # capacity_factor already covers days off and any partial sprint at the window edges
            total += stochastic_round(samp * float(chunk.capacity_factor), rng)
        out[i] = total
    return out


@dataclass
class WhenSimulationResult:
    """Result of simulate_when_daily.

    unfinished_count is the number of simulations that did not complete
    within max_days steps. Their completion_dates entry is forecast_dates[-1]
    — a placeholder, not a genuine completion date — so callers must check
    unfinished_count before treating the result as a trustworthy forecast.
    """

    completion_dates: List[dt.date]
    unfinished_count: int


def simulate_when_daily(
    history_counts: np.ndarray,
    forecast_dates: List[dt.date],
    per_date_ratio: Dict[dt.date, float],
    items_remaining: int,
    n_sims: int,
    seed: Optional[int] = None,
    max_days: int = 800,
    project_ratio: float = 1.0,
) -> WhenSimulationResult:
    rng = np.random.default_rng(seed)
    history = history_counts.astype(float)
    if history.size == 0 or items_remaining <= 0:
        # Return "today" as degenerate
        degenerate = forecast_dates[0] if forecast_dates else dt.date.today()
        return WhenSimulationResult([degenerate] * n_sims, unfinished_count=0)

    out: List[dt.date] = []
    unfinished_count = 0
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
            # Scale before rounding: rounding small daily counts by the ratio first distorts it
            done = stochastic_round(samp * ratio * project_ratio, rng)
            remaining -= done
            if remaining <= 0:
                out.append(d)
                break
            day_idx += 1
            steps += 1

        if remaining > 0:
            # Did not finish within max_days working days — forecast_dates[-1] is
            # a placeholder, not a genuine completion date. Callers must surface
            # unfinished_count rather than silently plotting this as real.
            out.append(forecast_dates[-1])
            unfinished_count += 1
    return WhenSimulationResult(out, unfinished_count)


def simulate_when_sprint(
    history_sprint_counts: np.ndarray,
    plan: List[SprintPlanChunk],
    items_remaining: int,
    n_sims: int,
    seed: Optional[int] = None,
    project_ratio: float = 1.0,
) -> WhenSimulationResult:
    """Sprint-at-a-time When forecast; a completion is reported as the last working date of its sprint.

    Simulations still unfinished after the final plan chunk are counted in
    unfinished_count and given that chunk's last date as a placeholder.
    """
    rng = np.random.default_rng(seed)
    hist = history_sprint_counts.astype(float)
    plan = [chunk for chunk in plan if chunk.working_dates]
    if hist.size == 0 or not plan or items_remaining <= 0:
        degenerate = plan[0].working_dates[0] if plan else dt.date.today()
        return WhenSimulationResult([degenerate] * n_sims, unfinished_count=0)

    placeholder = plan[-1].working_dates[-1]
    out: List[dt.date] = []
    unfinished_count = 0
    for _ in range(n_sims):
        remaining = int(items_remaining)
        for chunk in plan:
            samp = float(rng.choice(hist))
            remaining -= stochastic_round(samp * float(chunk.capacity_factor) * project_ratio, rng)
            if remaining <= 0:
                out.append(chunk.working_dates[-1])
                break
        else:
            out.append(placeholder)
            unfinished_count += 1
    return WhenSimulationResult(out, unfinished_count)


def completion_cdf_by_date(completion_dates: List[dt.date], dates: List[dt.date]) -> List[float]:
    if not completion_dates:
        return [0.0] * len(dates)
    comp = np.array([d.toordinal() for d in completion_dates], dtype=int)
    out = []
    for d in dates:
        out.append(float(np.mean(comp <= d.toordinal())))
    return out


def split_sample_counts(
    total_samples: np.ndarray,
    project_ratio: float,
    seed: Optional[int] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Split total completed items into project vs BAU using binomial sampling,
    preserving the total for each simulation.
    """
    totals = np.asarray(total_samples, dtype=int)
    ratio = min(max(float(project_ratio), 0.0), 1.0)
    rng = np.random.default_rng(seed)
    project = rng.binomial(totals, ratio)
    bau = totals - project
    return project.astype(int), bau.astype(int)


def threshold_breakdown(
    total_samples: np.ndarray,
    project_samples: np.ndarray,
    bau_samples: np.ndarray,
    p: float,
) -> tuple[int, int, int]:
    """Return a consistent total/project/BAU triple at the 'at least p' threshold.
    This keeps the breakdown additive for each confidence line.
    """
    if len(total_samples) == 0:
        return 0, 0, 0
    order = np.argsort(np.asarray(total_samples, dtype=int))
    idx = int((1.0 - float(p)) * len(order))
    idx = max(0, min(idx, len(order) - 1))
    sel = order[idx]
    return (
        int(total_samples[sel]),
        int(project_samples[sel]),
        int(bau_samples[sel]),
    )
