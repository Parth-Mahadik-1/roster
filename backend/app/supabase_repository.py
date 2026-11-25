from __future__ import annotations

from datetime import date
import os
from urllib.parse import urlencode

from .config import load_local_env
from .models import RosterInput
from .supabase_inspector import _normalise_project_url, _request_json, inspect_supabase_schema


ASSIGNMENT_FIELDS = {
    "assignment_id",
    "Emp_id",
    "brand_process_id",
    "shift_id",
    "assigned_from",
    "assigned_to",
    "status",
    "assignment_type",
}


def list_brands() -> list[dict]:
    """Read the existing Brand table without changing any Supabase data."""
    records = _fetch_table("Brand", "Brand_id,Brand_name,status", "Brand_id.asc")
    return [
        {"id": row["Brand_id"], "name": row["Brand_name"], "status": row.get("status")}
        for row in records
    ]


def list_employees() -> list[dict]:
    """Read employees and pod-lead relationships exactly as recorded in Supabase."""
    employees = _fetch_table("Employee", "Emp_id,emp_name,designation,emp_status", "Emp_id.asc")
    pods = _fetch_table("pods", "pod_id,pod_name,pod_lead_id", "pod_id.asc")
    memberships = _fetch_table(
        "employee_pod_memberships", "Emp_id,pod_id,effective_from,effective_to,status,is_primary",
        "employee_pod_membership_id.asc",
    )
    lead_pods: dict[int, list[str]] = {}
    for pod in pods:
        if pod.get("pod_lead_id") is not None:
            lead_pods.setdefault(pod["pod_lead_id"], []).append(pod["pod_name"])
    pod_names = {pod["pod_id"]: pod["pod_name"] for pod in pods}
    member_pods = {
        row["Emp_id"]: pod_names[row["pod_id"]]
        for row in memberships
        if str(row.get("status", "")).lower() == "active"
        and row.get("is_primary", True)
        and row["pod_id"] in pod_names
    }
    return [
        {
            "id": employee["Emp_id"],
            "name": employee["emp_name"],
            "designation": employee.get("designation") or "",
            "status": employee.get("emp_status") or "",
            "pod": member_pods.get(employee["Emp_id"], ", ".join(lead_pods.get(employee["Emp_id"], [])) or "Not assigned in Supabase"),
            "pod_role": "Pod Lead" if employee["Emp_id"] in lead_pods else "Employee",
        }
        for employee in employees
    ]


def catalog_overview() -> dict:
    brands = list_brands()
    employees = list_employees()
    return {"employees": employees, "brands": brands, "pods": _fetch_table("pods", "pod_id,pod_name,pod_lead_id", "pod_id.asc")}


def dashboard_catalog() -> dict:
    """Read the complete frontend catalog from Supabase in backend-owned form."""
    overview = catalog_overview()
    processes = _fetch_table("process", "process_id,process_name,status", "process_id.asc")
    shifts = _fetch_table("shifts", "shift_id,shift_name,start_time,end_time", "shift_id.asc")
    bridges = _fetch_table("Brand_processes", "Brand_process_id,Process_id,status", "Brand_process_id.asc")
    eligibility = _fetch_table(
        "employee_assignments",
        "Emp_id,brand_process_id,status,assignment_type",
        "assignment_id.asc",
    )
    process_by_id = {
        row["process_id"]: row["process_name"].strip().upper()
        for row in processes
        if str(row.get("status", "Active")).lower() == "active"
    }
    process_by_bridge = {
        row["Brand_process_id"]: process_by_id.get(row["Process_id"])
        for row in bridges
        if str(row.get("status", "Active")).lower() == "active"
    }
    eligible_processes: dict[int, set[str]] = {}
    for row in eligibility:
        if str(row.get("status", "")).lower() != "active":
            continue
        if str(row.get("assignment_type", "")).lower() != "eligibility":
            continue
        process = process_by_bridge.get(row.get("brand_process_id"))
        if process:
            eligible_processes.setdefault(row["Emp_id"], set()).add(process)
    employees = [
        {
            **employee,
            # Eligibility is the source of truth for multi-skilled employees;
            # designation remains only a descriptive HR field.
            "eligible_processes": sorted(eligible_processes.get(employee["id"], set())),
        }
        for employee in overview["employees"]
    ]
    return {
        **overview,
        "employees": employees,
        "processes": [
            {"id": row["process_id"], "name": row["process_name"], "status": row.get("status")}
            for row in processes
        ],
        "shifts": [
            {
                "id": row["shift_id"],
                "name": row["shift_name"],
                "start_time": str(row["start_time"]),
                "end_time": str(row["end_time"]),
            }
            for row in shifts
        ],
    }


def _fetch_table(
    table: str,
    select: str,
    order: str,
    filters: dict[str, str] | None = None,
) -> list[dict]:
    load_local_env()
    base_url = _normalise_project_url(os.getenv("SUPABASE_URL", ""))
    key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    if not base_url or not key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env before loading Supabase data.")
    query = urlencode({"select": select, "order": order, **(filters or {})})
    return _request_json(
        f"{base_url}/rest/v1/{table}?{query}",
        {"apikey": key, "Authorization": f"Bearer {key}"},
    )


def data_readiness() -> dict:
    """Summarise whether the confirmed database can generate a valid roster."""
    report = inspect_supabase_schema()
    snapshots = {table["table"]: table["sample"] for table in report["tables"]}
    row_counts = {name: snapshot.get("rows_observed", 0) for name, snapshot in snapshots.items()}
    blockers = []
    if row_counts.get("employee_assignments", 0) == 0:
        blockers.append("employee_assignments has no rows, so employee-to-brand/process eligibility is unavailable.")
    expected = {"employee_availability", "weekly_offs", "leave_records", "operating_calendars"}
    missing = sorted(expected - set(snapshots))
    if missing:
        blockers.append("No scheduling-rule data was found for " + ", ".join(missing) + ".")
    staffing_dates = _staffing_date_range(report)
    if staffing_dates["latest_start"] and staffing_dates["latest_start"] > date.today().isoformat():
        blockers.append(
            "All observed staffing requirements begin in the future, so the current month has no confirmed demand."
        )
    return {
        "connected": True,
        "table_counts": row_counts,
        "relationships": report["relationship_checks"],
        "employee_assignment_fields": sorted(ASSIGNMENT_FIELDS),
        "staffing_requirement_dates": staffing_dates,
        "scheduling_ready": not blockers,
        "blockers": blockers,
    }


def _staffing_date_range(report: dict) -> dict:
    requirements = next(table["sample"] for table in report["tables"] if table["table"] == "staffing_requirements")
    return requirements.get("effective_date_range") or {
        "earliest_start": None,
        "latest_start": None,
        "earliest_end": None,
        "latest_end": None,
    }


def build_roster_input_from_catalog(catalog: dict[str, list[dict]]) -> RosterInput:
    """Map the confirmed table structure after eligibility rows have been supplied.

    Callers must validate readiness first. This function intentionally avoids making up
    employee-to-pod, weekly-off, leave, or availability data that is not present.
    """
    assignments = catalog["employee_assignments"]
    if not assignments:
        raise RuntimeError("employee_assignments is empty.")
    unexpected = ASSIGNMENT_FIELDS - set(assignments[0])
    if unexpected:
        raise RuntimeError("employee_assignments is missing expected columns: " + ", ".join(sorted(unexpected)))
    raise RuntimeError(
        "The live schema has no employee-to-pod and weekly-off mapping yet. "
        "Keep DATA_SOURCE=sample until these confirmed sources are available."
    )
