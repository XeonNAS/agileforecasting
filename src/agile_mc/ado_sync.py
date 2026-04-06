from __future__ import annotations

import datetime as dt
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from .ado_client import AdoClient

logger = logging.getLogger(__name__)

# Conservative parallelism limit: enough to overlap round-trips without
# risking ADO rate-limiting (HTTP 429).  The bottleneck is network latency
# per sprint (3 calls × ~200–500 ms each), so even 4 workers gives a large
# speedup on typical team sizes of 10–30 sprints.
_ADO_MAX_WORKERS = 4


# ---------------------------------------------------------------------------
# Pure utility functions
# ---------------------------------------------------------------------------


def parse_ado_dt(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def expand_ado_date_range(start: dt.datetime, end: dt.datetime) -> List[dt.date]:
    """Expand an Azure DevOps days-off range to calendar dates.

    For sprint/team/member days off, Azure DevOps UI lets users pick a start day
    and an end day, and the range is expected to include both of those days.
    Treating the end date as exclusive drops the final day for multi-day entries
    such as 2026-04-02 through 2026-04-03.
    """
    sd = start.date()
    ed = end.date()
    if ed < sd:
        sd, ed = ed, sd
    out: List[dt.date] = []
    cur = sd
    while cur <= ed:
        out.append(cur)
        cur = cur + dt.timedelta(days=1)
    return out


def weekday_indexes_from_team_settings(working_days: List[str]) -> Set[int]:
    m = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    return {m[d.lower()] for d in working_days if d and d.lower() in m}


def iter_dates(start: dt.date, end_inclusive: dt.date) -> Iterable[dt.date]:
    cur = start
    while cur <= end_inclusive:
        yield cur
        cur = cur + dt.timedelta(days=1)


def extract_sprint_number(name: str) -> Optional[int]:
    m = re.search(r"(\d+)", name or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


@dataclass(frozen=True)
class Sprint:
    iteration_id: str
    name: str
    start_date: dt.date
    end_exclusive: dt.date
    end_inclusive: dt.date


# ---------------------------------------------------------------------------
# Sprint metadata: fetching
# ---------------------------------------------------------------------------


@dataclass
class _SprintMetadata:
    """All three capacity-related API responses for one sprint, pre-fetched."""
    team_days_off: Set[dt.date] = field(default_factory=set)
    baseline_by_member: Dict[str, float] = field(default_factory=dict)
    member_days_off: Dict[str, Set[dt.date]] = field(default_factory=dict)
    baseline_per_day: float = 0.0
    summary_days_off_count: Optional[int] = None


def fetch_sprints(ado: AdoClient) -> List[Sprint]:
    _t = time.perf_counter()
    iterations = ado.list_iterations()
    sprints: List[Sprint] = []
    for it in iterations:
        if not isinstance(it, dict):
            continue
        it_id = it.get("id")
        name = it.get("name") or ""
        attrs = it.get("attributes") or {}
        if not isinstance(attrs, dict):
            continue
        sd = parse_ado_dt(attrs.get("startDate"))
        fd = parse_ado_dt(attrs.get("finishDate"))
        if not it_id or not sd or not fd:
            continue
        start = sd.date()
        end_excl = fd.date()
        end_incl = end_excl - dt.timedelta(days=1)
        sprints.append(Sprint(str(it_id), str(name), start, end_excl, end_incl))
    sprints.sort(key=lambda s: (s.start_date, s.name))
    logger.info("fetch_sprints: %d sprints in %.0fms", len(sprints), (time.perf_counter() - _t) * 1000)
    return sprints


def fetch_team_days_off_for_sprint(ado: AdoClient, sprint: Sprint) -> Set[dt.date]:
    out: Set[dt.date] = set()
    for dr in ado.get_team_days_off(sprint.iteration_id) or []:
        if not isinstance(dr, dict):
            continue
        s = parse_ado_dt(dr.get("start"))
        e = parse_ado_dt(dr.get("end"))
        if not s or not e:
            continue
        for d in expand_ado_date_range(s, e):
            out.add(d)
    return out


def _parse_days_off_ranges(value: Any) -> Set[dt.date]:
    out: Set[dt.date] = set()
    for dr in value or []:
        if not isinstance(dr, dict):
            continue
        s = parse_ado_dt(dr.get("start"))
        e = parse_ado_dt(dr.get("end"))
        if not s or not e:
            continue
        for d in expand_ado_date_range(s, e):
            out.add(d)
    return out


def fetch_capacities_for_sprint(ado: AdoClient, sprint: Sprint) -> Tuple[Dict[str, float], Dict[str, Set[dt.date]]]:
    baseline: Dict[str, float] = {}
    member_days_off: Dict[str, Set[dt.date]] = {}

    rows = ado.get_capacities(sprint.iteration_id) or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        member = row.get("teamMember") or row.get("teamMemberIdentity") or {}
        if not isinstance(member, dict):
            member = {}
        member_id = member.get("id") or member.get("uniqueName") or member.get("displayName") or "unknown"

        activities = row.get("activities") or []
        cap = 0.0
        if isinstance(activities, list):
            for a in activities:
                if not isinstance(a, dict):
                    continue
                try:
                    cap += float(a.get("capacityPerDay") or 0.0)
                except Exception:
                    pass
        # If capacity isn't configured, fallback to 1.0 per member so days-off still matter
        if cap <= 0.0:
            cap = 1.0
        baseline[str(member_id)] = cap

        member_days_off[str(member_id)] = _parse_days_off_ranges(row.get("daysOff") or [])

    return baseline, member_days_off


def _select_iteration_team_summary(
    summary_payload: Dict[str, Any], baseline_per_day: float
) -> Optional[Dict[str, Any]]:
    teams = summary_payload.get("teams")
    if not isinstance(teams, list):
        return None
    team_rows = [row for row in teams if isinstance(row, dict)]
    if not team_rows:
        return None
    if len(team_rows) == 1:
        return team_rows[0]

    best_row: Optional[Dict[str, Any]] = None
    best_diff: Optional[float] = None
    for row in team_rows:
        try:
            cap = float(row.get("teamCapacityPerDay") or 0.0)
        except Exception:
            continue
        diff = abs(cap - float(baseline_per_day))
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_row = row

    # Only trust the match if it is effectively the same total team capacity.
    if best_row is not None and best_diff is not None:
        tolerance = max(0.01, abs(float(baseline_per_day)) * 0.01)
        if best_diff <= tolerance:
            return best_row

    return None


def fetch_iteration_summary_days_off_count(ado: AdoClient, sprint: Sprint, baseline_per_day: float) -> Optional[int]:
    try:
        payload = ado.get_iteration_capacities(sprint.iteration_id) or {}
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    selected = _select_iteration_team_summary(payload, baseline_per_day)
    if isinstance(selected, dict):
        try:
            return int(selected.get("teamTotalDaysOff"))
        except Exception:
            pass

    teams = payload.get("teams")
    if isinstance(teams, list) and len([t for t in teams if isinstance(t, dict)]) == 1:
        try:
            only = next(t for t in teams if isinstance(t, dict))
            return int(only.get("teamTotalDaysOff"))
        except Exception:
            pass

    # Last resort only when the iteration clearly appears to have a single aggregate number.
    try:
        total = payload.get("totalIterationDaysOff")
        if total is not None and (not isinstance(teams, list) or len([t for t in teams if isinstance(t, dict)]) <= 1):
            return int(total)
    except Exception:
        pass
    return None


def _fetch_sprint_metadata(ado: AdoClient, sprint: Sprint) -> _SprintMetadata:
    """Fetch all three capacity-related endpoints for one sprint.

    Designed to run in a thread pool — the AdoClient's requests.Session is
    thread-safe for concurrent reads once the headers are initialised.
    Call order: team_days_off → capacities → (derive baseline_per_day) →
    iteration_summary.  Steps 1 and 2 are independent but kept serial here
    because the cross-sprint parallelism in build_capacity_schedule already
    provides the dominant speedup.
    """
    team_days_off = fetch_team_days_off_for_sprint(ado, sprint)
    baseline_by_member, member_days_off = fetch_capacities_for_sprint(ado, sprint)

    baseline_per_day = sum(baseline_by_member.values()) if baseline_by_member else 0.0
    if baseline_per_day <= 0.0:
        baseline_per_day = max(1.0, float(len(baseline_by_member) or 1))

    summary_days_off_count = fetch_iteration_summary_days_off_count(ado, sprint, baseline_per_day)

    return _SprintMetadata(
        team_days_off=team_days_off,
        baseline_by_member=baseline_by_member,
        member_days_off=member_days_off,
        baseline_per_day=baseline_per_day,
        summary_days_off_count=summary_days_off_count,
    )


# ---------------------------------------------------------------------------
# Capacity schedule builder
# ---------------------------------------------------------------------------


def build_capacity_schedule(
    ado: AdoClient,
    sprints: List[Sprint],
    working_weekdays: Set[int],
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[dt.date, float]]:
    sprint_rows: List[Dict[str, Any]] = []
    cap_rows: List[Dict[str, Any]] = []
    per_date_ratio: Dict[dt.date, float] = {}

    if not sprints:
        return pd.DataFrame(sprint_rows), pd.DataFrame(cap_rows), per_date_ratio

    # ------------------------------------------------------------------
    # Phase 1: Fetch all sprint metadata in parallel.
    # Each sprint requires 3 independent-ish API calls; doing them in a
    # thread pool reduces N×3 serial round-trips to ceil(N/workers)×3.
    # Example: 20 sprints, 4 workers → 5 "rounds" instead of 20 rounds,
    # saving ~75% of metadata fetch time.
    # ------------------------------------------------------------------
    _t_meta = time.perf_counter()
    n_workers = min(len(sprints), _ADO_MAX_WORKERS)
    api_calls_expected = len(sprints) * 3  # team_days_off + capacities + iteration_summary

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        meta_list: List[_SprintMetadata] = list(
            pool.map(lambda sp: _fetch_sprint_metadata(ado, sp), sprints)
        )

    sprint_meta: Dict[str, _SprintMetadata] = {
        sp.iteration_id: meta for sp, meta in zip(sprints, meta_list)
    }
    _t_meta_elapsed = time.perf_counter() - _t_meta
    logger.info(
        "build_capacity_schedule: fetched metadata for %d sprints "
        "(%d API calls) in %.1fs [%d workers, ~%.0f ms/sprint serial-equivalent]",
        len(sprints),
        api_calls_expected,
        _t_meta_elapsed,
        n_workers,
        (_t_meta_elapsed / len(sprints)) * 1000,
    )

    # ------------------------------------------------------------------
    # Phase 2: Compute capacity schedule (pure computation, no API calls).
    # ------------------------------------------------------------------
    _t_compute = time.perf_counter()
    for sp in sprints:
        sprint_rows.append(
            {
                "iteration_id": sp.iteration_id,
                "sprint_name": sp.name,
                "sprint_num": extract_sprint_number(sp.name),
                "start_date": sp.start_date.isoformat(),
                "end_date": sp.end_inclusive.isoformat(),
            }
        )

        meta = sprint_meta[sp.iteration_id]
        team_off = meta.team_days_off
        baseline_by_member = meta.baseline_by_member
        member_off = meta.member_days_off
        baseline_per_day = meta.baseline_per_day
        summary_team_days_off_count = meta.summary_days_off_count

        working_dates = [d for d in iter_dates(sp.start_date, sp.end_inclusive) if d.weekday() in working_weekdays]
        working_dates_set = set(working_dates)
        normal_working_days = len(working_dates)

        team_days_off_working = sorted([d for d in team_off if d in working_dates_set])

        inferred_zero_capacity_dates: List[dt.date] = []
        planned_capacity_sum = 0.0
        for d in working_dates:
            if d in team_off:
                per_date_ratio[d] = 0.0
                continue
            available = 0.0
            for mid, cap in baseline_by_member.items():
                if d in member_off.get(mid, set()):
                    continue
                available += cap
            ratio = available / baseline_per_day if baseline_per_day > 0 else 1.0
            per_date_ratio[d] = ratio
            if baseline_by_member and available <= 0.0:
                inferred_zero_capacity_dates.append(d)
            planned_capacity_sum += available

        inferred_zero_capacity_dates = sorted(set(inferred_zero_capacity_dates) - set(team_days_off_working))

        explicit_or_inferred_days_off = set(team_days_off_working) | set(inferred_zero_capacity_dates)

        summary_fallback_count = 0
        if summary_team_days_off_count is not None:
            summary_fallback_count = max(0, int(summary_team_days_off_count) - len(explicit_or_inferred_days_off))
        planned_working_days = max(0, normal_working_days - len(explicit_or_inferred_days_off) - summary_fallback_count)

        baseline_capacity_sum = baseline_per_day * float(normal_working_days)

        if summary_fallback_count > 0 and baseline_per_day > 0:
            planned_capacity_sum = max(0.0, planned_capacity_sum - (baseline_per_day * float(summary_fallback_count)))

        if baseline_capacity_sum <= 0:
            capacity_factor = (planned_working_days / normal_working_days) if normal_working_days else 1.0
        else:
            capacity_factor = planned_capacity_sum / baseline_capacity_sum

        cap_rows.append(
            {
                "iteration_id": sp.iteration_id,
                "sprint_name": sp.name,
                "sprint_num": extract_sprint_number(sp.name),
                "start_date": sp.start_date.isoformat(),
                "end_date": sp.end_inclusive.isoformat(),
                "normal_working_days": normal_working_days,
                "planned_working_days": planned_working_days,
                "capacity_factor": round(float(capacity_factor), 4),
                "team_days_off_dates": ", ".join([d.isoformat() for d in team_days_off_working]),
                "explicit_team_days_off_dates": ", ".join([d.isoformat() for d in team_days_off_working]),
                "inferred_zero_capacity_dates": ", ".join([d.isoformat() for d in inferred_zero_capacity_dates]),
                "iteration_summary_team_days_off_count": (
                    int(summary_team_days_off_count) if summary_team_days_off_count is not None else None
                ),
                "summary_fallback_days_off_count": int(summary_fallback_count),
            }
        )

    logger.debug(
        "build_capacity_schedule: schedule computation for %d sprints in %.0fms",
        len(sprints),
        (time.perf_counter() - _t_compute) * 1000,
    )
    return pd.DataFrame(sprint_rows), pd.DataFrame(cap_rows), per_date_ratio


# ---------------------------------------------------------------------------
# Throughput from saved query
# ---------------------------------------------------------------------------


def parse_query_id_from_url_or_guid(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"[0-9a-fA-F\-]{36}", s):
        return s.lower()
    m = re.search(r"/_queries/query/([0-9a-fA-F\-]{36})", s)
    if m:
        return m.group(1).lower()
    return None


def fetch_daily_throughput_from_saved_query(
    ado: AdoClient,
    saved_query_url_or_guid: str,
    history_start: dt.date,
    history_end: dt.date,
    working_weekdays: Set[int],
    team_days_off_all: Set[dt.date],
    done_date_field: str = "AUTO",
) -> pd.DataFrame:
    qid = parse_query_id_from_url_or_guid(saved_query_url_or_guid)
    if not qid:
        raise ValueError("Could not parse saved query GUID from the provided value")

    # -- WIQL query: retrieve matching work item IDs ----------------------
    _t_wiql = time.perf_counter()
    wiql = ado.wiql_query_by_id(qid)
    work_items = wiql.get("workItems") or []
    ids = [int(wi.get("id")) for wi in work_items if isinstance(wi, dict) and wi.get("id") is not None]
    logger.info(
        "fetch_throughput: WIQL query returned %d work item IDs in %.0fms",
        len(ids),
        (time.perf_counter() - _t_wiql) * 1000,
    )

    if not ids:
        return _zero_filled_daily(history_start, history_end, working_weekdays, team_days_off_all)

    candidates: List[str] = []
    if done_date_field and done_date_field != "AUTO":
        candidates.append(done_date_field)
    candidates.extend(
        [
            "Microsoft.VSTS.Common.ClosedDate",
            "Microsoft.VSTS.Common.ResolvedDate",
            "Microsoft.VSTS.Common.StateChangeDate",
            "System.ChangedDate",
        ]
    )
    fields = list(dict.fromkeys(candidates))

    # -- Work item batch fetch (parallelised) -----------------------------
    chunk = 200
    batches = [ids[i : i + chunk] for i in range(0, len(ids), chunk)]
    n_workers = min(len(batches), _ADO_MAX_WORKERS)
    logger.info(
        "fetch_throughput: fetching %d work items in %d batch(es) [chunk=%d, workers=%d]",
        len(ids),
        len(batches),
        chunk,
        n_workers,
    )

    _t_batch = time.perf_counter()

    def _fetch_batch(batch: List[int]) -> List[Dict[str, Any]]:
        return ado.work_items_batch(batch, fields=fields).get("value") or []

    if len(batches) == 1:
        all_items: List[Dict[str, Any]] = _fetch_batch(batches[0])
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            all_items = []
            for chunk_items in pool.map(_fetch_batch, batches):
                all_items.extend(chunk_items)

    logger.info(
        "fetch_throughput: batch fetch complete — %d items in %.0fms",
        len(all_items),
        (time.perf_counter() - _t_batch) * 1000,
    )

    # -- Extract done dates -----------------------------------------------
    _t_tx = time.perf_counter()
    done_dates: List[dt.date] = []
    for it in all_items:
        if not isinstance(it, dict):
            continue
        f = it.get("fields") or {}
        if not isinstance(f, dict):
            continue
        picked = None
        for k in fields:
            if f.get(k):
                picked = f.get(k)
                break
        if not picked:
            continue
        dtd = parse_ado_dt(picked)
        if not dtd:
            continue
        done_dates.append(dtd.date())

    logger.info(
        "fetch_throughput: extracted %d done dates from %d items in %.0fms",
        len(done_dates),
        len(all_items),
        (time.perf_counter() - _t_tx) * 1000,
    )

    if not done_dates:
        return _zero_filled_daily(history_start, history_end, working_weekdays, team_days_off_all)

    # -- Build daily throughput dataframe ---------------------------------
    _t_df = time.perf_counter()
    ser = pd.Series(done_dates, name="date").value_counts().sort_index()
    df = ser.reset_index()
    df.columns = ["date", "done_count"]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= history_start) & (df["date"] <= history_end)].copy()

    # Map done counts onto the zero-filled working-day grid.
    # Uses a dict lookup instead of a full merge to avoid the column-suffix
    # confusion and unnecessary DataFrame allocation.
    filled = _zero_filled_daily(history_start, history_end, working_weekdays, team_days_off_all)
    done_by_date: Dict[dt.date, int] = dict(zip(df["date"], df["done_count"]))
    filled["done_count"] = filled["date"].map(done_by_date).fillna(0).astype(int)

    logger.debug(
        "fetch_throughput: dataframe built in %.0fms (%d rows, history %s–%s)",
        (time.perf_counter() - _t_df) * 1000,
        len(filled),
        history_start,
        history_end,
    )
    return filled[["date", "done_count", "is_working_day"]]


def _zero_filled_daily(
    start: dt.date,
    end: dt.date,
    working_weekdays: Set[int],
    team_days_off_all: Set[dt.date],
) -> pd.DataFrame:
    rows = []
    cur = start
    while cur <= end:
        is_working = (cur.weekday() in working_weekdays) and (cur not in team_days_off_all)
        rows.append({"date": cur, "done_count": 0, "is_working_day": bool(is_working)})
        cur = cur + dt.timedelta(days=1)
    return pd.DataFrame(rows)
