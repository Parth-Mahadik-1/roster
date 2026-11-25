from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import load_local_env


def inspect_supabase_schema() -> dict:
    """Read the public REST schema and one sample row from each readable table.

    This is read-only. It uses the anonymous key provided by the project owner and
    respects existing Supabase Row Level Security policies.
    """
    load_local_env()
    base_url = _normalise_project_url(os.getenv("SUPABASE_URL", ""))
    inspection_key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    if not base_url or not inspection_key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env before inspection.")

    headers = {"apikey": inspection_key, "Authorization": f"Bearer {inspection_key}"}
    openapi = _request_json(f"{base_url}/rest/v1/", {**headers, "Accept": "application/openapi+json"})
    table_schemas: list[tuple[str, dict]] = []
    for path, methods in openapi.get("paths", {}).items():
        if not path.startswith("/") or path.count("/") != 1 or not isinstance(methods, dict):
            continue
        table_name = path.removeprefix("/")
        if not table_name or table_name in {"rpc", ""}:
            continue
        get_operation = methods.get("get", {})
        schema = get_operation.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
        table_schemas.append((table_name, schema))
    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = dict(executor.map(lambda item: (item, _table_snapshot(base_url, item, headers)), (name for name, _ in table_schemas)))
    tables = [
        {"table": table_name, "response_schema": schema, "sample": _public_snapshot(snapshots[table_name])}
        for table_name, schema in table_schemas
    ]
    return {
        "project_url": base_url,
        "table_count": len(tables),
        "tables": tables,
        "relationship_checks": _relationship_checks(snapshots),
    }


def _normalise_project_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value or value.startswith("http://") or value.startswith("https://"):
        return value
    if "." not in value:
        return f"https://{value}.supabase.co"
    return f"https://{value}"


def _table_snapshot(base_url: str, table_name: str, headers: dict[str, str]) -> dict:
    try:
        data = _request_json(f"{base_url}/rest/v1/{table_name}?limit=100", headers)
        columns = sorted({column for row in data for column in row})
        start_dates = sorted(str(row["effective_from"]) for row in data if row.get("effective_from"))
        end_dates = sorted(str(row["effective_to"]) for row in data if row.get("effective_to"))
        effective_date_range = None
        if start_dates or end_dates:
            effective_date_range = {
                "earliest_start": start_dates[0] if start_dates else None,
                "latest_start": start_dates[-1] if start_dates else None,
                "earliest_end": end_dates[0] if end_dates else None,
                "latest_end": end_dates[-1] if end_dates else None,
            }
        return {
            "readable": True,
            "rows_observed": len(data),
            "columns_observed": columns,
            "row": data[0] if data else None,
            "effective_date_range": effective_date_range,
            "_records": data,
        }
    except RuntimeError as error:
        return {"readable": False, "error": str(error)}


def _public_snapshot(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key != "_records"}


def _relationship_checks(snapshots: dict[str, dict]) -> list[dict]:
    def records(table_name: str) -> list[dict]:
        return snapshots.get(table_name, {}).get("_records", [])

    def check(source: str, source_column: str, target: str, target_column: str) -> dict:
        target_values = {row[target_column] for row in records(target) if target_column in row}
        missing = sorted(
            {row[source_column] for row in records(source) if row.get(source_column) is not None} - target_values
        )
        return {
            "relationship": f"{source}.{source_column} -> {target}.{target_column}",
            "valid": not missing,
            "missing_reference_values": missing,
        }

    return [
        check("Brand_processes", "Brand_id", "Brand", "Brand_id"),
        check("Brand_processes", "Process_id", "process", "process_id"),
        check("staffing_requirements", "brand_process_id", "Brand_processes", "Brand_process_id"),
        check("staffing_requirements", "shift_id", "shifts", "shift_id"),
        check("pods", "pod_lead_id", "Employee", "Emp_id"),
    ]


def _request_json(url: str, headers: dict[str, str]) -> dict | list:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Network error: {error.reason}") from error
