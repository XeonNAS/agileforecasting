from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from .ado_client import AdoClient


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


def fetch_sprints(ado: AdoClient) -> List[Sprint]:
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


def _select_iteration_team_summary(summary_payload: Dict[str, Any], baseline_per_day: float) -> Optional[Dict[str, Any]]:
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

def build_capacity_schedule(
    ado: AdoClient,
    sprints: List[Sprint],
    working_weekdays: Set[int],
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[dt.date, float]]:
    sprint_rows: List[Dict[str, Any]] = []
    cap_rows: List[Dict[str, Any]] = []
    per_date_ratio: Dict[dt.date, float] = {}

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

        team_off = fetch_team_days_off_for_sprint(ado, sp)
        baseline_by_member, member_off = fetch_capacities_for_sprint(ado, sp)

        baseline_per_day = sum(baseline_by_member.values()) if baseline_by_member else 0.0
        if baseline_per_day <= 0.0:
            baseline_per_day = max(1.0, float(len(baseline_by_member) or 1))

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

        summary_team_days_off_count = fetch_iteration_summary_days_off_count(ado, sp, baseline_per_day)
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

    return pd.DataFrame(sprint_rows), pd.DataFrame(cap_rows), per_date_ratio


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

    wiql = ado.wiql_query_by_id(qid)
    work_items = wiql.get("workItems") or []
    ids = [int(wi.get("id")) for wi in work_items if isinstance(wi, dict) and wi.get("id") is not None]

    if not ids:
        return _zero_filled_daily(history_start, history_end, working_weekdays, team_days_off_all)

    candidates: List[str] = []
    if done_date_field and done_date_field != "AUTO":
        candidates.append(done_date_field)
    candidates.extend([
        "Microsoft.VSTS.Common.ClosedDate",
        "Microsoft.VSTS.Common.ResolvedDate",
        "Microsoft.VSTS.Common.StateChangeDate",
        "System.ChangedDate",
    ])
    fields = list(dict.fromkeys(candidates))

    done_dates: List[dt.date] = []
    chunk = 200
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        payload = ado.work_items_batch(batch, fields=fields)
        items = payload.get("value") or []
        for it in items:
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

    if not done_dates:
        return _zero_filled_daily(history_start, history_end, working_weekdays, team_days_off_all)

    ser = pd.Series(done_dates, name="date").value_counts().sort_index()
    df = ser.reset_index()
    df.columns = ["date", "done_count"]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= history_start) & (df["date"] <= history_end)].copy()

    filled = _zero_filled_daily(history_start, history_end, working_weekdays, team_days_off_all)

    # IMPORTANT: avoid done_count_x/done_count_y confusion
    merged = filled.merge(df, on="date", how="left", suffixes=("_base", ""))
    # df column keeps "done_count", base becomes "done_count_base"
    if "done_count" not in merged.columns:
        # fall back defensively
        dc = merged.filter(like="done_count").columns.tolist()
        raise KeyError(f"done_count not found after merge; columns={dc}")

    merged["done_count"] = merged["done_count"].fillna(0).astype(int)
    return merged[["date", "done_count", "is_working_day"]]


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
