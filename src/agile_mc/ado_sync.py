from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import urllib.parse
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
# Capacity source constants
# ---------------------------------------------------------------------------

#: Azure returned capacity rows with at least one member having non-zero capacity.
CAPACITY_SOURCE_CONFIGURED = "azure_configured"
#: Azure returned no capacity rows for this sprint (common for future sprints).
CAPACITY_SOURCE_MISSING = "missing_capacity"
#: Azure returned capacity rows but every member has 0 capacityPerDay.
CAPACITY_SOURCE_ZERO = "zero_capacity"
#: No Azure data available; capacity was inherited from the last configured sprint.
CAPACITY_SOURCE_CARRIED = "carried_forward"

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
    capacity_source: str = CAPACITY_SOURCE_MISSING


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
        # ADO finishDate is the inclusive last day of the iteration (the UI
        # shows that date as the sprint end).  end_exclusive is derived for
        # range operations only.
        end_incl = fd.date()
        end_excl = end_incl + dt.timedelta(days=1)
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


def fetch_capacities_for_sprint(
    ado: AdoClient, sprint: Sprint
) -> Tuple[Dict[str, float], Dict[str, Set[dt.date]], str]:
    """Fetch per-member capacity for a sprint.

    Returns (baseline_by_member, member_days_off, capacity_source).

    capacity_source is one of:
    - CAPACITY_SOURCE_CONFIGURED  real rows with at least one non-zero capacityPerDay
    - CAPACITY_SOURCE_MISSING     Azure returned no rows (e.g. unconfigured future sprint)
    - CAPACITY_SOURCE_ZERO        rows returned but every member has 0 capacityPerDay
    """
    baseline: Dict[str, float] = {}
    member_days_off: Dict[str, Set[dt.date]] = {}

    rows = ado.get_capacities(sprint.iteration_id) or []
    if not rows:
        logger.debug("fetch_capacities_for_sprint: no capacity rows returned for sprint %s", sprint.name)
        return baseline, member_days_off, CAPACITY_SOURCE_MISSING

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
        baseline[str(member_id)] = cap
        member_days_off[str(member_id)] = _parse_days_off_ranges(row.get("daysOff") or [])

    logger.debug(
        "fetch_capacities_for_sprint: %d members for sprint %s; capacities=%s",
        len(baseline),
        sprint.name,
        {mid: cap for mid, cap in baseline.items()},
    )

    # Distinguish ZERO (rows exist, all 0) from CONFIGURED (at least one non-zero)
    if baseline and all(v == 0.0 for v in baseline.values()):
        logger.debug(
            "fetch_capacities_for_sprint: all %d members have 0 capacityPerDay for sprint %s",
            len(baseline),
            sprint.name,
        )
        return baseline, member_days_off, CAPACITY_SOURCE_ZERO

    return baseline, member_days_off, CAPACITY_SOURCE_CONFIGURED


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
    """
    team_days_off = fetch_team_days_off_for_sprint(ado, sprint)
    baseline_by_member, member_days_off, capacity_source = fetch_capacities_for_sprint(ado, sprint)

    baseline_per_day = sum(baseline_by_member.values()) if baseline_by_member else 0.0

    # Always fetch the iteration summary for diagnostic purposes; the result is
    # stored as-is in ado_team_total_days_off_count (a raw Azure field that may
    # include individual member days off, not only team-wide days off).
    summary_days_off_count = fetch_iteration_summary_days_off_count(ado, sprint, baseline_per_day)

    return _SprintMetadata(
        team_days_off=team_days_off,
        baseline_by_member=baseline_by_member,
        member_days_off=member_days_off,
        baseline_per_day=baseline_per_day,
        summary_days_off_count=summary_days_off_count,
        capacity_source=capacity_source,
    )


# ---------------------------------------------------------------------------
# Pure sprint capacity calculation
# ---------------------------------------------------------------------------


def calculate_sprint_capacity(
    sprint: Sprint,
    working_weekdays: Set[int],
    team_days_off: Set[dt.date],
    baseline_by_member: Dict[str, float],
    member_days_off: Dict[str, Set[dt.date]],
    capacity_source: str,
    summary_days_off_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Compute all sprint capacity fields from parsed ADO inputs.

    Pure function — no I/O.  Accepts the data returned by the three ADO
    capacity endpoints and returns every field needed for the cap_df row plus
    a ``per_date_ratios`` dict (keyed by date) for merging into the global
    capacity map used by the simulation.

    Definitions
    -----------
    normal_working_days
        Weekdays in the sprint range (before any days off).
    team_days_off_count
        Working days off for the whole team, from the team days-off endpoint only.
        Individual member days off do NOT reduce this value.
    planned_working_days
        normal_working_days - team_days_off_count (team-wide only).
    schedule_availability
        planned_working_days / normal_working_days.
    per_user available_days
        planned_working_days - user's individual days off (excluding team days off).
    team_capacity_hours
        Sum of (capacity_per_day × available_days) for every member.
    baseline_capacity_hours
        Sum of (capacity_per_day × normal_working_days) for every member.
    capacity_factor
        team_capacity_hours / baseline_capacity_hours.
    per_date_ratios
        team available capacity / baseline_per_day for each working day.
        0.0 on team days off.  1.0 when capacity source is MISSING (no data).
    """
    working_dates = [d for d in iter_dates(sprint.start_date, sprint.end_inclusive) if d.weekday() in working_weekdays]
    working_dates_set = set(working_dates)
    normal_working_days = len(working_dates)

    team_days_off_working = sorted([d for d in team_days_off if d in working_dates_set])
    team_days_off_count = len(team_days_off_working)

    per_date_ratios: Dict[dt.date, float] = {}
    warnings: List[str] = []

    # ------------------------------------------------------------------
    # MISSING: Azure returned no capacity rows; carry-forward not applied.
    # Treat all non-team-off working days as full capacity for simulation.
    # Report numeric capacity fields as None — we have no data.
    # ------------------------------------------------------------------
    if capacity_source == CAPACITY_SOURCE_MISSING:
        for d in working_dates:
            per_date_ratios[d] = 0.0 if d in team_days_off else 1.0
        planned_working_days = max(0, normal_working_days - team_days_off_count)
        schedule_availability = (planned_working_days / normal_working_days) if normal_working_days else 1.0
        warnings.append(
            f"No capacity configured in Azure DevOps for sprint '{sprint.name}'. "
            "Simulation uses full capacity (1.0) for this sprint's working days."
        )
        return {
            "normal_working_days": normal_working_days,
            "planned_working_days": planned_working_days,
            "schedule_availability": round(schedule_availability, 4),
            "capacity_factor": None,
            "team_capacity_hours": None,
            "baseline_capacity_hours": None,
            "team_days_off_dates": ", ".join(d.isoformat() for d in team_days_off_working),
            "team_days_off_count": team_days_off_count,
            "inferred_zero_capacity_dates": "",
            "ado_team_total_days_off_count": summary_days_off_count,
            "capacity_source": capacity_source,
            "per_user_capacity": json.dumps([]),
            "per_date_ratios": per_date_ratios,
            "warnings": json.dumps(warnings),
        }

    # ------------------------------------------------------------------
    # ZERO: Azure returned rows but every member has 0 capacityPerDay.
    # Treat all working days as zero capacity; report explicit zeros.
    # ------------------------------------------------------------------
    if capacity_source == CAPACITY_SOURCE_ZERO:
        for d in working_dates:
            per_date_ratios[d] = 0.0
        planned_working_days = max(0, normal_working_days - team_days_off_count)
        schedule_availability = (planned_working_days / normal_working_days) if normal_working_days else 1.0
        per_user_capacity = [
            {
                "member_id": mid,
                "capacity_per_day": 0.0,
                "days_off_count": len(
                    [d for d in member_days_off.get(mid, set()) if d in working_dates_set and d not in team_days_off]
                ),
                "available_days": planned_working_days,
                "available_capacity_hours": 0.0,
            }
            for mid in baseline_by_member
        ]
        warnings.append(
            f"Azure DevOps returned capacity rows for sprint '{sprint.name}' but all members have 0 capacity."
        )
        return {
            "normal_working_days": normal_working_days,
            "planned_working_days": planned_working_days,
            "schedule_availability": round(schedule_availability, 4),
            "capacity_factor": 0.0,
            "team_capacity_hours": 0.0,
            "baseline_capacity_hours": 0.0,
            "team_days_off_dates": ", ".join(d.isoformat() for d in team_days_off_working),
            "team_days_off_count": team_days_off_count,
            "inferred_zero_capacity_dates": "",
            "ado_team_total_days_off_count": summary_days_off_count,
            "capacity_source": capacity_source,
            "per_user_capacity": json.dumps(per_user_capacity),
            "per_date_ratios": per_date_ratios,
            "warnings": json.dumps(warnings),
        }

    # ------------------------------------------------------------------
    # CONFIGURED or CARRIED: full per-day per-member calculation.
    # ------------------------------------------------------------------
    baseline_per_day = sum(baseline_by_member.values())

    inferred_zero_capacity_dates: List[dt.date] = []
    planned_capacity_sum = 0.0

    for d in working_dates:
        if d in team_days_off:
            per_date_ratios[d] = 0.0
            continue
        available = 0.0
        for mid, cap in baseline_by_member.items():
            if d not in member_days_off.get(mid, set()):
                available += cap
        ratio = available / baseline_per_day if baseline_per_day > 0 else 1.0
        per_date_ratios[d] = ratio
        # A non-team-off working day with 0 available capacity means every member
        # has an individual day off on that date — treat it as an inferred team day off
        # for the purpose of planned_working_days.
        if available <= 0.0:
            inferred_zero_capacity_dates.append(d)
        planned_capacity_sum += available

    inferred_zero_capacity_dates = sorted(set(inferred_zero_capacity_dates) - set(team_days_off_working))

    # planned_working_days reflects the sprint *schedule*: only team-wide days off
    # (explicit or inferred all-absent) reduce it.  Individual member days off must
    # NOT reduce this value — they affect that person's available capacity only.
    explicit_or_inferred_days_off = set(team_days_off_working) | set(inferred_zero_capacity_dates)
    planned_working_days = max(0, normal_working_days - len(explicit_or_inferred_days_off))

    baseline_capacity_sum = baseline_per_day * float(normal_working_days)
    schedule_availability = (planned_working_days / normal_working_days) if normal_working_days else 1.0

    if baseline_capacity_sum > 0:
        capacity_factor = planned_capacity_sum / baseline_capacity_sum
    else:
        capacity_factor = schedule_availability

    per_user_capacity = []
    for mid, cap in baseline_by_member.items():
        user_days_off_excl_team = [
            d for d in member_days_off.get(mid, set()) if d in working_dates_set and d not in team_days_off
        ]
        available_days = normal_working_days - team_days_off_count - len(user_days_off_excl_team)
        available_cap_hours = sum(
            cap for d in working_dates if d not in team_days_off and d not in member_days_off.get(mid, set())
        )
        per_user_capacity.append(
            {
                "member_id": mid,
                "capacity_per_day": cap,
                "days_off_count": len(user_days_off_excl_team),
                "available_days": available_days,
                "available_capacity_hours": available_cap_hours,
            }
        )

    return {
        "normal_working_days": normal_working_days,
        "planned_working_days": planned_working_days,
        "schedule_availability": round(schedule_availability, 4),
        "capacity_factor": round(capacity_factor, 4),
        "team_capacity_hours": round(planned_capacity_sum, 4),
        "baseline_capacity_hours": round(baseline_capacity_sum, 4),
        "team_days_off_dates": ", ".join(d.isoformat() for d in team_days_off_working),
        "team_days_off_count": team_days_off_count,
        "inferred_zero_capacity_dates": ", ".join(d.isoformat() for d in inferred_zero_capacity_dates),
        "ado_team_total_days_off_count": summary_days_off_count,
        "capacity_source": capacity_source,
        "per_user_capacity": json.dumps(per_user_capacity),
        "per_date_ratios": per_date_ratios,
        "warnings": json.dumps(warnings),
    }


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
    # ------------------------------------------------------------------
    _t_meta = time.perf_counter()
    n_workers = min(len(sprints), _ADO_MAX_WORKERS)
    api_calls_expected = len(sprints) * 3  # team_days_off + capacities + iteration_summary

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        meta_list: List[_SprintMetadata] = list(pool.map(lambda sp: _fetch_sprint_metadata(ado, sp), sprints))

    sprint_meta: Dict[str, _SprintMetadata] = {sp.iteration_id: meta for sp, meta in zip(sprints, meta_list)}
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
    # Phase 1.5: Apply carry-forward for MISSING capacity sprints.
    #
    # Future sprints often have no capacity configured in Azure DevOps yet.
    # Rather than reporting zero capacity (which breaks forecasting), we carry
    # the last CONFIGURED sprint's per-member baseline forward.  The target
    # sprint's own team_days_off and member_days_off (which Azure does return)
    # are still applied on top of the carried baseline.
    #
    # ZERO capacity is NOT carried forward — if Azure explicitly says a member
    # has 0 hours, that is a deliberate configuration.
    # ------------------------------------------------------------------
    last_valid_baseline: Dict[str, float] = {}
    for sp in sprints:
        meta = sprint_meta[sp.iteration_id]
        if meta.capacity_source == CAPACITY_SOURCE_CONFIGURED:
            last_valid_baseline = dict(meta.baseline_by_member)
        elif meta.capacity_source == CAPACITY_SOURCE_MISSING and last_valid_baseline:
            carried_per_day = sum(last_valid_baseline.values())
            sprint_meta[sp.iteration_id] = _SprintMetadata(
                team_days_off=meta.team_days_off,
                baseline_by_member=dict(last_valid_baseline),
                member_days_off=meta.member_days_off,
                baseline_per_day=carried_per_day,
                summary_days_off_count=meta.summary_days_off_count,
                capacity_source=CAPACITY_SOURCE_CARRIED,
            )
            logger.debug(
                "build_capacity_schedule: sprint %s has no capacity; carried forward from last "
                "configured sprint (%d members, %.1f h/day)",
                sp.name,
                len(last_valid_baseline),
                carried_per_day,
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
        result = calculate_sprint_capacity(
            sprint=sp,
            working_weekdays=working_weekdays,
            team_days_off=meta.team_days_off,
            baseline_by_member=meta.baseline_by_member,
            member_days_off=meta.member_days_off,
            capacity_source=meta.capacity_source,
            summary_days_off_count=meta.summary_days_off_count,
        )

        # Merge this sprint's per-date ratios into the global map.
        per_date_ratio.update(result.pop("per_date_ratios"))

        cap_rows.append(
            {
                "iteration_id": sp.iteration_id,
                "sprint_name": sp.name,
                "sprint_num": extract_sprint_number(sp.name),
                "start_date": sp.start_date.isoformat(),
                "end_date": sp.end_inclusive.isoformat(),
                **result,
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


# Canonical Azure DevOps query GUID: 8-4-4-4-12 hex groups.
_GUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

#: User-facing message shown when a saved query URL/GUID cannot be recognised.
SAVED_QUERY_PARSE_MESSAGE = (
    "Saved query URL could not be recognised. "
    "Open the saved Azure DevOps query and copy its URL, or paste the query GUID."
)


class SavedQueryParseError(ValueError):
    """Raised when a saved-query URL or GUID cannot be parsed into a query GUID.

    Subclasses ValueError so existing ``except ValueError`` handlers keep
    working, while callers that want the precise validation message can catch
    this type specifically.
    """


def parse_query_id_from_url_or_guid(s: str) -> Optional[str]:
    """Extract an Azure DevOps saved-query GUID from a raw GUID or any query URL.

    Accepts the common Azure DevOps query URL variants, including:
      * a bare GUID (``a1b2c3d4-....``)
      * ``.../_queries/query/<guid>/`` and ``.../_queries/query-edit/<guid>``
      * URLs with ``?queryId=<guid>`` or ``?id=<guid>`` in the query string
      * URLs where the GUID appears elsewhere in the path or query string
        (extra path segments, folders, trailing parameters)
      * URL-encoded values (``%2D`` for ``-`` etc.)

    Returns the lowercased GUID, or ``None`` if no GUID can be found.  Parsing
    is intentionally permissive about *where* the GUID sits, but still strict
    about the GUID *shape*, so non-query text (e.g. a backlog URL with no GUID)
    returns ``None`` rather than a false positive.
    """
    s = (s or "").strip()
    if not s:
        return None

    # Fast path: the whole value is a canonical GUID.
    if re.fullmatch(_GUID_RE, s):
        return s.lower()

    # Decode percent-encoding so encoded GUIDs / separators are matched.
    decoded = urllib.parse.unquote(s)
    parsed = urllib.parse.urlparse(decoded)

    # 1) Prefer an explicit query-string parameter (queryId / id / wiql).
    qs = {k.lower(): v for k, v in urllib.parse.parse_qs(parsed.query).items()}
    for key in ("queryid", "id", "wiql"):
        for val in qs.get(key, []):
            m = re.search(_GUID_RE, val)
            if m:
                return m.group(0).lower()

    # 2) Prefer a GUID that directly follows a query path segment.
    m = re.search(r"/_queries/(?:query|query-edit|edit|folder)/(" + _GUID_RE + r")", decoded)
    if m:
        return m.group(1).lower()

    # 3) Last resort: any canonical GUID anywhere in the (decoded) value.
    m = re.search(_GUID_RE, decoded)
    if m:
        return m.group(0).lower()

    return None


def validate_saved_query(saved_query_url_or_guid: str) -> str:
    """Validate a saved-query URL/GUID and return the normalised query GUID.

    Raises :class:`SavedQueryParseError` (with a precise, PAT-free message) when
    the value cannot be parsed.  Call this *before* the expensive sprint/capacity
    API calls so an unrecognised query is reported immediately and is not
    mistaken for a connection problem.
    """
    qid = parse_query_id_from_url_or_guid(saved_query_url_or_guid)
    if not qid:
        raise SavedQueryParseError(SAVED_QUERY_PARSE_MESSAGE)
    return qid


def describe_ado_sync_error(exc: Exception, log_path: Optional[str] = None) -> str:
    """Map an ADO sync exception to a precise, PAT-free user-facing message.

    Crucially, this never blames "connection settings" for a saved-query parse
    error or a downstream data-processing ``ValueError`` — those are validation
    or computation problems, not connectivity problems.  HTTP/connection errors
    are handled separately by the caller.
    """
    if isinstance(exc, SavedQueryParseError):
        return str(exc)
    if isinstance(exc, ValueError):
        msg = f"ADO sync failed while processing data ({type(exc).__name__}: {exc})."
        if log_path:
            msg += (
                " This is a data-processing error, not a connection problem — "
                f"see the app log for the full traceback: {log_path}"
            )
        return msg
    msg = f"ADO sync failed ({type(exc).__name__}: {exc})."
    if log_path:
        msg += f" Check the app log for the full traceback: {log_path}"
    return msg


def handle_ado_sync_exception(exc: Exception, log: logging.Logger, log_path: Optional[str] = None) -> str:
    """Log the full traceback for an ADO sync failure and return a user message.

    Centralises catch-block behaviour so the Streamlit UI and the tests share
    one implementation: always logs at ERROR *with* the traceback, and returns
    a precise message that never mislabels a ValueError as a connection failure.
    """
    log.exception("ADO sync failed: %s: %s", type(exc).__name__, exc)
    return describe_ado_sync_error(exc, log_path)


def fetch_daily_throughput_from_saved_query(
    ado: AdoClient,
    saved_query_url_or_guid: str,
    history_start: dt.date,
    history_end: dt.date,
    working_weekdays: Set[int],
    team_days_off_all: Set[dt.date],
    done_date_field: str = "AUTO",
) -> pd.DataFrame:
    qid = validate_saved_query(saved_query_url_or_guid)

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
