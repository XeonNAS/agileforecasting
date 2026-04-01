from __future__ import annotations

"""Runtime patch for Azure DevOps sprint capacity and Streamlit cache.

What this patch does
--------------------
1) Disables Streamlit caching decorators at runtime so old capacity data cannot
   survive code changes or patch changes.
2) Intercepts Azure DevOps capacity-related HTTP GET responses and folds
   iteration-level team days off into:
      - team capacities payloads
      - single-member capacity payloads
      - iteration capacity summary payloads
3) Writes a simple debug log file in the current working directory so it is
   obvious whether the patch loaded and whether a capacity API call was seen.

Safe fallback: if anything here fails, the app continues to run.
"""

import builtins
import json
import os
import re
import sys
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

PATCH_NAME = "mc-teamdaysoff-v2"
LOG_FILE = Path.cwd() / "mc_teamdaysoff_patch.log"
_DEBUG = os.environ.get("MC_DEBUG_TEAMDAYSOFF_PATCH", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
    "",
}

_CAPACITIES_RE = re.compile(
    r"/_apis/work/teamsettings/iterations/(?P<iteration>[^/]+)/capacities(?:/(?P<member>[^/?]+))?(?=[/?]|$)",
    re.IGNORECASE,
)
_ITERATION_CAP_SUMMARY_RE = re.compile(
    r"/_apis/work/iterations/(?P<iteration>[^/]+)/iterationcapacities(?=[/?]|$)",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    line = f"[{PATCH_NAME}] {msg}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    if _DEBUG:
        try:
            print(line)
        except Exception:
            pass


def _startup_banner() -> None:
    _log(f"loaded python={sys.version.split()[0]} cwd={Path.cwd()}")


def _identity_cache_decorator(func: Any = None, **kwargs: Any) -> Any:
    if func is None:
        def decorator(inner: Any) -> Any:
            return inner
        return decorator
    return func


def _patch_streamlit_cache() -> None:
    try:
        import streamlit as st  # type: ignore
    except Exception as exc:
        _log(f"streamlit cache patch skipped: import failed: {exc!r}")
        return

    if getattr(st, "_mc_teamdaysoff_cache_patched", False):
        return

    try:
        st.cache_data = _identity_cache_decorator  # type: ignore[assignment]
        st.cache_resource = _identity_cache_decorator  # type: ignore[assignment]
        setattr(st, "_mc_teamdaysoff_cache_patched", True)
        _log("streamlit cache decorators disabled")
    except Exception as exc:
        _log(f"streamlit cache patch failed safely: {exc!r}")


# Ensure streamlit gets patched even when it is imported later.
_original_import = builtins.__import__


def _patched_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    mod = _original_import(name, globals, locals, fromlist, level)
    try:
        if name == "streamlit" or name.startswith("streamlit.") or (fromlist and name == "streamlit"):
            _patch_streamlit_cache()
    except Exception:
        pass
    return mod


builtins.__import__ = _patched_import
if "streamlit" in sys.modules:
    _patch_streamlit_cache()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(s[:10]), time.min, tzinfo=timezone.utc)
    except Exception:
        return None


def _iter_weekdays_in_range(start_value: Any, end_value: Any) -> Iterable[date]:
    start_dt = _parse_dt(start_value)
    end_dt = _parse_dt(end_value)
    if start_dt is None or end_dt is None:
        return []

    start_d = start_dt.date()
    end_d = end_dt.date()
    if end_d < start_d:
        start_d, end_d = end_d, start_d

    out: list[date] = []
    current = start_d
    while current <= end_d:
        if current.weekday() < 5:
            out.append(current)
        current += timedelta(days=1)
    return out


def _normalize_range(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    start = item.get("start")
    end = item.get("end")
    if start is None or end is None:
        return None
    return {"start": start, "end": end}


# Azure DevOps teamdaysoff URL exists on the teamsettings/iterations path.
def _teamdaysoff_url_from_capacities_url(url: str) -> str | None:
    parts = urlsplit(url)
    m = _CAPACITIES_RE.search(parts.path)
    if not m:
        return None
    new_path = parts.path[: m.start()] + f"/_apis/work/teamsettings/iterations/{m.group('iteration')}/teamdaysoff" + parts.path[m.end() :]
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


# Iteration capacity summary URL has no team in the path, so we derive the linked teamdaysoff
# using the returned team ids or skip if we cannot obtain a team-scoped URL.
def _teamdaysoff_urls_from_iteration_summary_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    teams = payload.get("teams")
    if not isinstance(teams, list):
        return []
    urls: list[str] = []
    for team in teams:
        if not isinstance(team, dict):
            continue
        links = team.get("_links") or {}
        team_iteration = None
        if isinstance(links, dict):
            team_iteration = (links.get("teamIteration") or {}).get("href")
        if not team_iteration:
            continue
        parts = urlsplit(str(team_iteration))
        path = parts.path.rstrip("/") + "/teamdaysoff"
        urls.append(urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment)))
    return urls


def _extract_member_payloads(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict) and isinstance(payload.get("teamMembers"), list):
        return [x for x in payload["teamMembers"] if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return None


def _merge_team_ranges_into_member_payload(member_payload: dict[str, Any], team_ranges: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    merged = deepcopy(member_payload)
    existing = merged.get("daysOff")
    if not isinstance(existing, list):
        existing = []
    seen = {
        (str(item.get("start")), str(item.get("end")))
        for item in existing
        if isinstance(item, dict) and item.get("start") is not None and item.get("end") is not None
    }
    added = 0
    for rng in team_ranges:
        key = (str(rng["start"]), str(rng["end"]))
        if key not in seen:
            existing.append({"start": rng["start"], "end": rng["end"]})
            seen.add(key)
            added += 1
    merged["daysOff"] = existing
    return merged, added


def _merge_team_ranges_into_capacities_payload(payload: Any, team_ranges: list[dict[str, Any]]) -> tuple[Any, int]:
    members = _extract_member_payloads(payload)
    if not members:
        return payload, 0

    merged_payload = deepcopy(payload)
    if isinstance(merged_payload, dict) and isinstance(merged_payload.get("teamMembers"), list):
        target_members = merged_payload["teamMembers"]
    elif isinstance(merged_payload, list):
        target_members = merged_payload
    else:
        return payload, 0

    unique_weekdays: set[date] = set()
    for rng in team_ranges:
        unique_weekdays.update(_iter_weekdays_in_range(rng.get("start"), rng.get("end")))

    total_added_ranges = 0
    for idx, member in enumerate(target_members):
        if not isinstance(member, dict):
            continue
        merged_member, added = _merge_team_ranges_into_member_payload(member, team_ranges)
        target_members[idx] = merged_member
        total_added_ranges += added

    if isinstance(merged_payload, dict) and "totalDaysOff" in merged_payload:
        try:
            current_total = int(merged_payload.get("totalDaysOff") or 0)
        except Exception:
            current_total = 0
        merged_payload["totalDaysOff"] = current_total + len(unique_weekdays)

    return merged_payload, total_added_ranges


def _apply_teamdaysoff_to_iteration_summary(payload: Any, team_days_off_payloads: list[Any]) -> tuple[Any, int]:
    if not isinstance(payload, dict):
        return payload, 0
    teams = payload.get("teams")
    if not isinstance(teams, list) or not teams:
        return payload, 0

    total_unique_weekdays: set[date] = set()
    per_team_weekday_counts: list[int] = []
    for item in team_days_off_payloads:
        team_ranges = []
        if isinstance(item, dict) and isinstance(item.get("daysOff"), list):
            team_ranges = [rng for rng in (_normalize_range(x) for x in item.get("daysOff", [])) if rng is not None]
        weekdays: set[date] = set()
        for rng in team_ranges:
            weekdays.update(_iter_weekdays_in_range(rng.get("start"), rng.get("end")))
        per_team_weekday_counts.append(len(weekdays))
        total_unique_weekdays.update(weekdays)

    if not total_unique_weekdays and not any(per_team_weekday_counts):
        return payload, 0

    merged = deepcopy(payload)
    merged_teams = merged.get("teams") or []
    patched = 0
    for idx, team in enumerate(merged_teams):
        if not isinstance(team, dict):
            continue
        add_days = per_team_weekday_counts[idx] if idx < len(per_team_weekday_counts) else 0
        if add_days <= 0:
            continue
        try:
            current = int(team.get("teamTotalDaysOff") or 0)
        except Exception:
            current = 0
        team["teamTotalDaysOff"] = current + add_days
        patched += add_days

    if "totalIterationDaysOff" in merged:
        try:
            current_total = int(merged.get("totalIterationDaysOff") or 0)
        except Exception:
            current_total = 0
        merged["totalIterationDaysOff"] = current_total + len(total_unique_weekdays)

    return merged, patched


def _rewrite_response_bytes(response: Any, payload: Any) -> Any:
    encoding = getattr(response, "encoding", None) or "utf-8"
    body = json.dumps(payload).encode(encoding)
    try:
        response._content = body
    except Exception:
        pass
    try:
        response.headers["Content-Length"] = str(len(body))
    except Exception:
        pass
    return response


def _augment_ado_payload_for_url(url: str, payload: Any, fetch_json: Callable[[str], Any]) -> tuple[Any, str | None]:
    cap_match = _CAPACITIES_RE.search(urlsplit(url).path)
    if cap_match:
        team_url = _teamdaysoff_url_from_capacities_url(url)
        if not team_url:
            return payload, None
        team_payload = fetch_json(team_url)
        team_ranges = []
        if isinstance(team_payload, dict) and isinstance(team_payload.get("daysOff"), list):
            team_ranges = [rng for rng in (_normalize_range(x) for x in team_payload.get("daysOff", [])) if rng is not None]
        if not team_ranges:
            return payload, f"no team daysoff ranges returned for {team_url}"

        if isinstance(payload, dict) and "teamMembers" in payload:
            merged, added = _merge_team_ranges_into_capacities_payload(payload, team_ranges)
            return merged, f"capacities payload merged ranges={len(team_ranges)} member_additions={added} via {team_url}"

        if isinstance(payload, dict) and "teamMember" in payload:
            merged, added = _merge_team_ranges_into_member_payload(payload, team_ranges)
            return merged, f"single-member payload merged ranges={len(team_ranges)} additions={added} via {team_url}"

        if isinstance(payload, list):
            merged, added = _merge_team_ranges_into_capacities_payload(payload, team_ranges)
            return merged, f"list payload merged ranges={len(team_ranges)} member_additions={added} via {team_url}"

    if _ITERATION_CAP_SUMMARY_RE.search(urlsplit(url).path):
        team_urls = _teamdaysoff_urls_from_iteration_summary_payload(payload)
        if not team_urls:
            return payload, "iteration summary had no resolvable teamIteration links"
        team_payloads = []
        for team_url in team_urls:
            try:
                team_payloads.append(fetch_json(team_url))
            except Exception as exc:
                _log(f"iteration summary teamdaysoff fetch failed for {team_url}: {exc!r}")
                team_payloads.append({})
        merged, added = _apply_teamdaysoff_to_iteration_summary(payload, team_payloads)
        return merged, f"iteration summary merged team urls={len(team_urls)} added_days={added}"

    return payload, None


def _install_requests_patch() -> None:
    try:
        import requests  # type: ignore
    except Exception as exc:
        _log(f"requests patch skipped: {exc!r}")
        return

    original_request = requests.sessions.Session.request
    if getattr(original_request, "_mc_teamdaysoff_patched", False):
        return

    def patched_request(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        response = original_request(self, method, url, *args, **kwargs)
        try:
            if str(method).upper() != "GET":
                return response

            path = urlsplit(str(url)).path
            if not (_CAPACITIES_RE.search(path) or _ITERATION_CAP_SUMMARY_RE.search(path)):
                return response

            original_payload = response.json()

            def fetch_json(team_url: str) -> Any:
                team_response = original_request(self, "GET", team_url, *args, **kwargs)
                status = getattr(team_response, "status_code", None)
                if status != 200:
                    raise RuntimeError(f"status {status} for {team_url}")
                return team_response.json()

            new_payload, info = _augment_ado_payload_for_url(str(url), original_payload, fetch_json)
            if info:
                _log(info)
            if new_payload is not original_payload:
                response = _rewrite_response_bytes(response, new_payload)
        except Exception as exc:
            _log(f"requests patch failed safely for {url}: {exc!r}")
        return response

    patched_request._mc_teamdaysoff_patched = True  # type: ignore[attr-defined]
    requests.sessions.Session.request = patched_request  # type: ignore[assignment]
    _log("requests patch installed")


_install_requests_patch()
_startup_banner()
