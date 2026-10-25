"""The stats tool: verified numbers come from cricbot's Pandit over HTTP (its SQL path and validators).

A service boundary keeps the two dependency sets apart; cricbot stays the source of truth for stats
until its query layer is ported (PLAN.md P1).
"""

from __future__ import annotations

from typing import Any

import httpx


class StatsClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.base = base_url.rstrip("/")
        self.http = client or httpx.Client(timeout=90)

    def online(self) -> bool:
        try:
            return self.http.get(f"{self.base}/api/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def ask(self, question: str) -> dict[str, Any] | None:
        try:
            r = self.http.post(f"{self.base}/api/agent/chat", json={"message": question, "history": []})
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        data = r.json()
        return {
            "reply": data.get("reply", ""),
            "tables": _compact(data.get("results") or []),
            "calls": data.get("tool_calls") or [],
            "validation": data.get("validation", ""),
        }


def _compact(results: list[Any], rows: int = 8) -> list[dict[str, Any]]:
    """Keep the first rows of each table so the LLM sees the numbers without the whole payload."""
    out = []
    for res in results[:4]:
        if isinstance(res, dict):
            table = res.get("rows") or res.get("data") or res.get("table")
            if isinstance(table, list):
                out.append({"title": res.get("title") or res.get("name") or "", "rows": table[:rows]})
            else:
                out.append({k: v for k, v in res.items() if not isinstance(v, (list, dict))})
    return out
