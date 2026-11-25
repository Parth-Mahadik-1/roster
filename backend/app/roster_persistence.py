from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import load_local_env
from .supabase_inspector import _normalise_project_url


def insert_rows(table: str, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    load_local_env()
    base_url = _normalise_project_url(os.getenv("SUPABASE_URL", ""))
    key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    request = Request(
        f"{base_url}/rest/v1/{table}", data=json.dumps(rows).encode(), method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        raise RuntimeError(f"Supabase rejected {table} insert (HTTP {error.code}): {error.read().decode(errors='replace')}") from error


def update_rows(table: str, query: str, payload: dict) -> list[dict]:
    """Update explicitly selected rows through the backend service role only."""
    load_local_env()
    base_url = _normalise_project_url(os.getenv("SUPABASE_URL", ""))
    key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    request = Request(
        f"{base_url}/rest/v1/{table}?{query}", data=json.dumps(payload).encode(), method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        raise RuntimeError(f"Supabase rejected {table} update (HTTP {error.code}): {error.read().decode(errors='replace')}") from error


def save_roster(year: int, month: int, rows: list[dict], diagnostics: list[str]) -> int:
    run = insert_rows("roster_runs", [{"roster_year": year, "roster_month": month, "status": "Validated", "diagnostics": diagnostics}])[0]
    assignments = []
    for row in rows:
        bridge_id, shift_id, *_ = row["role_id"].split(":")
        assignments.append({
            "roster_run_id": run["roster_run_id"],
            "roster_date": row["date"],
            "Emp_id": int(row["employee_id"]),
            "brand_process_id": int(bridge_id),
            "shift_id": int(shift_id),
            "assignment_status": row.get("status", "Scheduled"),
            "assignment_source": row.get("assignment_source", "Optimized"),
        })
    insert_rows("generated_roster_assignments", assignments)
    return run["roster_run_id"]
