from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass(frozen=True)
class AdoRef:
    organization: str
    project: str
    team: str


class AdoClient:
    def __init__(self, ado: AdoRef, pat: str, timeout_s: int = 30):
        self.ado = ado
        self.base_url = "https://dev.azure.com"
        self.timeout_s = timeout_s

        token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{self.ado.organization}/{path.lstrip('/')}"

    def _request(
        self, method: str, path: str, params: Optional[Dict[str, Any]] = None, json_body: Any = None
    ) -> Dict[str, Any]:
        url = self._url(path)

        for attempt in range(5):
            r = self.session.request(method, url, params=params, json=json_body, timeout=self.timeout_s)
            if r.status_code == 429:
                time.sleep(1.0 + attempt)
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json()
                except Exception:
                    msg = r.text
                raise requests.HTTPError(f"{r.status_code} {r.reason}: {msg}", response=r)
            return r.json()

        raise requests.HTTPError("Too many retries (HTTP 429)")

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, params: Optional[Dict[str, Any]] = None, json_body: Any = None) -> Dict[str, Any]:
        return self._request("POST", path, params=params, json_body=json_body)

    # ---- Work API

    def get_team_settings(self) -> Dict[str, Any]:
        return self.get(
            f"{self.ado.project}/{self.ado.team}/_apis/work/teamsettings",
            params={"api-version": "7.1"},
        )

    def list_iterations(self) -> List[Dict[str, Any]]:
        payload = self.get(
            f"{self.ado.project}/{self.ado.team}/_apis/work/teamsettings/iterations",
            params={"api-version": "7.1"},
        )
        vals = payload.get("values", payload.get("value", []))
        return vals if isinstance(vals, list) else []

    def get_team_days_off(self, iteration_id: str) -> List[Dict[str, Any]]:
        payload = self.get(
            f"{self.ado.project}/{self.ado.team}/_apis/work/teamsettings/iterations/{iteration_id}/teamdaysoff",
            params={"api-version": "7.1"},
        )
        days = payload.get("daysOff", [])
        return days if isinstance(days, list) else []

    def get_capacities(self, iteration_id: str) -> List[Dict[str, Any]]:
        """Normalize the capacities response to a list of dict rows."""
        payload: Any = self.get(
            f"{self.ado.project}/{self.ado.team}/_apis/work/teamsettings/iterations/{iteration_id}/capacities",
            params={"api-version": "7.1"},
        )

        if isinstance(payload, list):
            return [p for p in payload if isinstance(p, dict)]

        if isinstance(payload, dict):
            for key in ("value", "values", "capacities"):
                v = payload.get(key)
                if isinstance(v, list):
                    return [p for p in v if isinstance(p, dict)]
            # Best-effort: find a list of dicts that looks like capacity rows
            for v in payload.values():
                if (
                    isinstance(v, list)
                    and v
                    and isinstance(v[0], dict)
                    and ("teamMember" in v[0] or "teamMemberIdentity" in v[0])
                ):
                    return [p for p in v if isinstance(p, dict)]

        return []

    # ---- WIT API

    def wiql_query_by_id(self, query_id: str) -> Dict[str, Any]:
        return self.get(
            f"{self.ado.project}/_apis/wit/wiql/{query_id}",
            params={"api-version": "7.1"},
        )

    def work_items_batch(self, ids: List[int], fields: List[str]) -> Dict[str, Any]:
        return self.post(
            f"{self.ado.project}/_apis/wit/workitemsbatch",
            params={"api-version": "7.1"},
            json_body={
                "ids": ids,
                "fields": fields,
                "errorPolicy": "Omit",
            },
        )

    def get_iteration_capacities(self, iteration_id: str) -> Dict[str, Any]:
        return self.get(
            f"{self.ado.project}/_apis/work/iterations/{iteration_id}/iterationcapacities",
            params={"api-version": "7.1"},
        )
