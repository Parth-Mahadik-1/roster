"""Read persisted rosters and apply targeted absence coverage overlays."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from .supabase_repository import _fetch_table


def latest_persisted_roster(
    year: int,
    month: int,
    *,
    run_offset: int = 0,
    apply_coverage: bool = True,
    apply_availability: bool = True,
) -> dict | None:
    runs = _fetch_table(
        "roster_runs",
        "roster_run_id,roster_year,roster_month,status,diagnostics,created_at",
        "roster_run_id.desc",
    )
    matching = [
        row for row in runs
        if row["roster_year"] == year and row["roster_month"] == month
        and str(row.get("status", "")).lower() in {"validated", "published"}
    ]
    expected_dates = _expected_operating_dates(year, month)
    complete_runs: list[tuple[dict, list[dict]]] = []
    for candidate in matching:
        candidate_rows = _fetch_table(
            "generated_roster_assignments",
            "generated_assignment_id,roster_run_id,roster_date,Emp_id,brand_process_id,shift_id,assignment_status,assignment_source",
            "roster_date.asc",
            {"roster_run_id": f"eq.{candidate['roster_run_id']}"},
        )
        if _complete_runs([candidate], candidate_rows, expected_dates):
            complete_runs.append((candidate, candidate_rows))
        if len(complete_runs) > run_offset:
            break
    if len(complete_runs) <= run_offset:
        return None
    run, generated = complete_runs[run_offset]
    employees = {
        row["Emp_id"]: row
        for row in _fetch_table("Employee", "Emp_id,emp_name,designation,emp_status", "Emp_id.asc")
    }
    brands = {
        row["Brand_id"]: row["Brand_name"]
        for row in _fetch_table("Brand", "Brand_id,Brand_name,status", "Brand_id.asc")
    }
    processes = {
        row["process_id"]: str(row["process_name"]).strip().upper()
        for row in _fetch_table("process", "process_id,process_name,status", "process_id.asc")
    }
    bridges = {
        row["Brand_process_id"]: row
        for row in _fetch_table("Brand_processes", "Brand_process_id,Brand_id,Process_id,status", "Brand_process_id.asc")
    }
    shifts = {
        row["shift_id"]: row
        for row in _fetch_table("shifts", "shift_id,shift_name,start_time,end_time,duration", "shift_id.asc")
    }
    pods = _employee_pods()
    availability = _fetch_table(
        "employee_availability",
        "availability_id,Emp_id,unavailable_from,unavailable_to,availability_type,status,created_at",
        "availability_id.asc",
    )
    unavailable = {
        (row["Emp_id"], day.isoformat())
        for row in availability
        if str(row.get("status", "")).lower() == "approved"
        for day in _dates(date.fromisoformat(row["unavailable_from"]), date.fromisoformat(row["unavailable_to"]))
    } if apply_availability else set()

    rows = [
        _display_assignment(
            row, employees, pods, brands, processes, bridges, shifts,
            row.get("assignment_source") or "Generated",
        )
        for row in generated
        if (row["Emp_id"], row["roster_date"]) not in unavailable
    ]
    if apply_coverage:
        assignments = _fetch_table(
            "employee_assignments",
            "assignment_id,Emp_id,brand_process_id,shift_id,assigned_from,assigned_to,status,assignment_type",
            "assignment_id.asc",
        )
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        coverage = [
            row for row in assignments
            if str(row.get("assignment_type", "")).lower() == "coverage"
            and str(row.get("status", "")).lower() == "active"
            and date.fromisoformat(row["assigned_from"]) <= end
            and date.fromisoformat(row["assigned_to"]) >= start
        ]
        for item in coverage:
            for workday in _dates(date.fromisoformat(item["assigned_from"]), date.fromisoformat(item["assigned_to"])):
                if workday.year != year or workday.month != month:
                    continue
                if (item["Emp_id"], workday.isoformat()) in unavailable:
                    continue
                raw = {
                    "generated_assignment_id": f"coverage-{item['assignment_id']}-{workday.isoformat()}",
                    "roster_date": workday.isoformat(),
                    "Emp_id": item["Emp_id"],
                    "brand_process_id": item["brand_process_id"],
                    "shift_id": item["shift_id"],
                    "assignment_status": "Coverage",
                }
                rows.append(_display_assignment(raw, employees, pods, brands, processes, bridges, shifts, "Coverage"))
    rows.sort(key=lambda row: (row["date"], int(row["employee_id"]), row["brand_process_id"], row["shift_id"]))
    weekly_offs = _fetch_table(
        "employee_weekly_offs",
        "Emp_id,weekday,effective_from,effective_to,status",
        "weekly_off_id.asc",
    )
    return {
        "status": "feasible",
        "assignments": rows,
        "diagnostics": run.get("diagnostics") or [],
        "manager_messages": [],
        "weekly_offs": [
            {"employee_id": str(row["Emp_id"]), "day": _weekday_name(row["weekday"])}
            for row in weekly_offs if str(row.get("status", "")).lower() == "active"
        ],
        "roster_run_id": run["roster_run_id"],
        "created_at": run["created_at"],
    }


def _display_assignment(raw: dict, employees: dict, pods: dict, brands: dict, processes: dict, bridges: dict, shifts: dict, source: str) -> dict:
    employee = employees.get(raw["Emp_id"], {})
    bridge = bridges.get(raw["brand_process_id"], {})
    shift = shifts.get(raw["shift_id"], {})
    return {
        "date": raw["roster_date"],
        "employee_id": str(raw["Emp_id"]),
        "employee": employee.get("emp_name", f"Employee {raw['Emp_id']}"),
        "designation": employee.get("designation") or "",
        "pod": pods.get(raw["Emp_id"], "Not assigned"),
        "role_id": f"{raw['brand_process_id']}:{raw['shift_id']}:{source.lower()}:{raw['generated_assignment_id']}",
        "brand_process_id": raw["brand_process_id"],
        "shift_id": raw["shift_id"],
        "brand": brands.get(bridge.get("Brand_id"), "Unassigned"),
        "process": processes.get(bridge.get("Process_id"), "Unassigned"),
        "shift": f"{shift.get('shift_name', 'Unassigned')} ({shift.get('start_time', '')}-{shift.get('end_time', '')})",
        "start_time": str(shift.get("start_time", "")),
        "end_time": str(shift.get("end_time", "")),
        "hours": float(shift.get("duration") or 0),
        "status": raw.get("assignment_status", "Scheduled"),
        "assignment_source": source,
    }


def _employee_pods() -> dict[int, str]:
    pods = _fetch_table("pods", "pod_id,pod_name,pod_lead_id", "pod_id.asc")
    memberships = _fetch_table(
        "employee_pod_memberships",
        "Emp_id,pod_id,effective_from,effective_to,status,is_primary",
        "employee_pod_membership_id.asc",
    )
    names = {row["pod_id"]: row["pod_name"] for row in pods}
    result = {row["pod_lead_id"]: row["pod_name"] for row in pods if row.get("pod_lead_id") is not None}
    result.update({
        row["Emp_id"]: names[row["pod_id"]]
        for row in memberships
        if str(row.get("status", "")).lower() == "active" and row.get("is_primary", True) and row["pod_id"] in names
    })
    return result


def _dates(start: date, end: date) -> list[date]:
    return [date.fromordinal(day) for day in range(start.toordinal(), end.toordinal() + 1)]


def _weekday_name(day: int) -> str:
    return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[day]


def _complete_runs(runs: list[dict], assignments: list[dict], expected_dates: set[str]) -> list[dict]:
    """Ignore old/partial weekly runs when a monthly roster is requested."""
    if not expected_dates:
        return runs
    dates_by_run: dict[int, set[str]] = {}
    for row in assignments:
        dates_by_run.setdefault(row["roster_run_id"], set()).add(row["roster_date"])
    return [run for run in runs if expected_dates <= dates_by_run.get(run["roster_run_id"], set())]


def _expected_operating_dates(year: int, month: int) -> set[str]:
    """Return dates on which at least one active staffing lane must operate."""
    try:
        requirements = _fetch_table(
            "staffing_requirements",
            "requirement_id,brand_process_id,effective_from,effective_to",
            "requirement_id.asc",
        )
        calendars = _fetch_table(
            "operating_calendars",
            "brand_process_id,operates_sundays,observes_public_holidays,effective_from,effective_to,status",
            "calendar_id.asc",
        )
        holidays = _fetch_table(
            "india_holidays", "holiday_date,is_working_override", "holiday_id.asc",
        )
    except RuntimeError:
        # Do not hide a valid saved roster if the validation metadata is
        # temporarily unavailable. Generation still performs its own checks.
        return set()

    active_calendars = {
        row["brand_process_id"]: row
        for row in calendars if str(row.get("status", "")).lower() == "active"
    }
    closed_holidays = {
        row["holiday_date"] for row in holidays if not row.get("is_working_override")
    }
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    expected = set()
    current = start
    while current <= end:
        stamp = current.isoformat()
        for requirement in requirements:
            if not (requirement["effective_from"] <= stamp <= requirement["effective_to"]):
                continue
            calendar = active_calendars.get(requirement["brand_process_id"], {})
            if current.weekday() == 6 and not calendar.get("operates_sundays", True):
                continue
            if stamp in closed_holidays and calendar.get("observes_public_holidays", False):
                continue
            expected.add(stamp)
            break
        current += timedelta(days=1)
    return expected
