from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import load_local_env
from .models import Employee, Role, RosterInput
from .roster_persistence import insert_rows, save_roster
from .scheduler import RosterScheduler, validate_schedule
from .supabase_inspector import _normalise_project_url
from .supabase_repository import _fetch_table


def roster_period() -> dict[str, str | None]:
    requirements = _fetch_table("staffing_requirements", "effective_from,effective_to", "requirement_id.asc")
    starts = sorted(row["effective_from"] for row in requirements if row.get("effective_from"))
    ends = sorted(row["effective_to"] for row in requirements if row.get("effective_to"))
    return {"start": starts[0] if starts else None, "end": ends[-1] if ends else None}


def build_live_roster(
    year: int,
    month: int,
    persist: bool = False,
    extra_unavailable: dict[date, set[str]] | None = None,
    rotation_targets: dict[str, set[date]] | None = None,
) -> dict:
    tables = _load_tables()
    roster_input, weekly_offs = _build_roster_input(tables, year, month, extra_unavailable, rotation_targets)
    if not roster_input.roles:
        diagnostics = [f"No active staffing requirements exist for {year}-{month:02d}."]
        return {"status": "coverage_gap", "assignments": [], "diagnostics": diagnostics, "manager_messages": diagnostics, "weekly_offs": weekly_offs, "roster_run_id": None}
    result = RosterScheduler().generate(roster_input, year, month)
    diagnostics = list(result.diagnostics) + validate_schedule(result, roster_input)
    rows = result.rows() if result.status == "feasible" and not diagnostics else []
    for row in rows:
        row["status"] = "Scheduled"
    roster_run_id = save_roster(year, month, rows, diagnostics) if persist and rows else None
    return {
        "status": "feasible" if rows else "coverage_gap",
        "assignments": rows,
        "diagnostics": diagnostics,
        "manager_messages": [message for message in diagnostics if message.startswith("Manager action")],
        "weekly_offs": weekly_offs,
        "roster_run_id": roster_run_id,
    }


def record_absence(emp_id: int, absence_date: date) -> dict:
    """Backward-compatible one-day unexpected absence, stored as availability."""
    return request_time_off(emp_id, absence_date, absence_date, "Unavailable")


def request_time_off(emp_id: int, unavailable_from: date, unavailable_to: date, availability_type: str) -> dict:
    """Approve a leave/comp-off only when a re-solved live roster stays feasible."""
    if availability_type not in {"Leave", "Comp Off", "Unavailable"}:
        raise ValueError("availability_type must be Leave, Comp Off, or Unavailable.")
    if unavailable_to < unavailable_from:
        raise ValueError("The leave end date cannot be before the start date.")
    if (unavailable_to - unavailable_from).days + 1 > 3:
        raise ValueError("Leave or Comp Off is limited to three consecutive calendar days per request.")
    if unavailable_from.year != unavailable_to.year or unavailable_from.month != unavailable_to.month:
        raise ValueError("A leave request must remain within one roster month. Submit separate requests for each month.")
    tables = _load_tables()
    employee = tables["employees"].get(emp_id)
    if employee is None or str(employee.get("emp_status", "")).lower() != "active":
        raise ValueError(f"Employee ID {emp_id} is not an active Employee record.")
    if _has_overlapping_approved_availability(tables["availability"], emp_id, unavailable_from, unavailable_to):
        raise ValueError("This employee already has an approved leave, Comp Off, or unavailable record in the requested dates.")
    if not _within_active_task_assignment(tables["assignments"], emp_id, unavailable_from, unavailable_to):
        raise ValueError(
            "Leave can be requested only inside the employee's active allocated task period. "
            "Add or extend the employee eligibility assignment first."
        )

    baseline = build_live_roster(unavailable_from.year, unavailable_from.month)
    if not baseline["assignments"]:
        raise ValueError("The roster is already infeasible. Resolve the displayed coverage gaps before adding another absence.")
    request_dates = _date_range(unavailable_from, unavailable_to)
    affected = [
        row for row in baseline["assignments"]
        if row["employee_id"] == str(emp_id) and row["date"] in {day.isoformat() for day in request_dates}
    ]
    if not affected:
        raise ValueError(
            "The employee has no allocated company task in the requested period, so no leave approval is required for these dates."
        )

    trial_unavailable = {day: {str(emp_id)} for day in request_dates}
    rotation_targets: dict[str, set[date]] = defaultdict(set)
    for row in affected:
        rotation_targets[row["role_id"]].add(date.fromisoformat(row["date"]))
    trial = build_live_roster(
        unavailable_from.year, unavailable_from.month,
        extra_unavailable=trial_unavailable, rotation_targets=dict(rotation_targets),
    )
    if trial["status"] != "feasible":
        message = trial["diagnostics"][0] if trial["diagnostics"] else "No conflict-free eligible replacement is available."
        raise ValueError(f"{availability_type} not approved: {message}")

    created = insert_rows("employee_availability", [{
        "Emp_id": emp_id,
        "unavailable_from": unavailable_from.isoformat(),
        "unavailable_to": unavailable_to.isoformat(),
        "availability_type": availability_type,
        "status": "Approved",
        "reason": "Approved only after a feasible CP-SAT re-roster",
    }])[0]
    revised = build_live_roster(
        unavailable_from.year, unavailable_from.month, persist=True, rotation_targets=dict(rotation_targets)
    )
    affected_role_ids = {row["role_id"] for row in affected}
    alternatives = [
        row for row in revised["assignments"]
        if row["date"] in {day.isoformat() for day in request_dates} and row["role_id"] in affected_role_ids
    ]
    return {
        "status": revised["status"],
        "availability_id": created["availability_id"],
        "affected_assignments": affected,
        "alternative_assignments": alternatives,
        "coverage_gaps": revised["diagnostics"],
        "explanation": (
            f"{availability_type} was saved in employee_availability only after the complete month remained feasible. "
            "The optimizer re-distributed the affected tasks among eligible colleagues while preserving one task per employee per day, weekly offs, and leader restrictions."
        ),
    }


def restore_absence(emp_id: int, absence_date: date) -> dict:
    tables = _load_tables()
    availability_ids = [
        row["availability_id"] for row in tables["availability"]
        if row["Emp_id"] == emp_id
        and str(row.get("status", "")).lower() == "approved"
        and row["unavailable_from"] <= absence_date.isoformat() <= row["unavailable_to"]
    ]
    matching_ids = [row["assignment_id"] for row in tables["assignments"] if row["Emp_id"] == emp_id and _has_active_absence([row], emp_id, absence_date)]
    if not matching_ids and not availability_ids:
        raise ValueError(f"No active absence event exists for employee ID {emp_id} on {absence_date.isoformat()}.")
    if matching_ids:
        _update_absence_status(matching_ids, "Restored")
    if availability_ids:
        _write_table("employee_availability", "PATCH", "?availability_id=in.(" + ",".join(map(str, availability_ids)) + ")", {"status": "Restored"})
    revised = build_live_roster(absence_date.year, absence_date.month)
    return {"status": revised["status"], "restored_assignment_ids": matching_ids, "restored_availability_ids": availability_ids, "coverage_gaps": revised["diagnostics"], "explanation": "The event is retained for audit history with status Restored and no longer removes the employee from the roster."}


def _build_roster_input(
    tables: dict,
    year: int,
    month: int,
    extra_unavailable: dict[date, set[str]] | None = None,
    rotation_targets: dict[str, set[date]] | None = None,
) -> tuple[RosterInput, list[dict]]:
    unavailable = _unavailable_by_date(tables["assignments"], year, month)
    _add_availability_unavailability(unavailable, tables["availability"], year, month)
    for workday, employee_ids in (extra_unavailable or {}).items():
        if workday.year == year and workday.month == month:
            unavailable.setdefault(workday, set()).update(employee_ids)
    requirements = [row for row in tables["requirements"] if _overlaps_month(row, year, month)]
    roles = _roles_from_requirements(requirements, tables)
    _ensure_eligibility_rows(tables, requirements)
    leaders = set(tables["pod_by_lead"])
    weekly_off_by_id = _configured_weekly_offs(tables) or _weekly_offs(tables, roles, unavailable, leaders)
    if not _configured_weekly_offs(tables):
        _save_weekly_offs(weekly_off_by_id)
    employees = []
    for emp_id, row in tables["employees"].items():
        if str(row.get("emp_status", "")).lower() != "active":
            continue
        assigned_targets = {
            (assignment.get("brand_process_id"), assignment.get("shift_id"))
            for assignment in tables["assignments"]
            if assignment["Emp_id"] == emp_id and str(assignment.get("assignment_type", "")).lower() == "eligibility" and str(assignment.get("status", "")).lower() == "active"
        }
        eligible = {
            role.id for role in roles
            if tuple(map(int, role.id.split(":")[:2])) in assigned_targets
            and not (emp_id in leaders and role.shift.startswith("Night"))
        }
        if not eligible:
            continue
        pod = tables["pod_by_employee"].get(emp_id, tables["pod_by_lead"].get(emp_id, "Not assigned in Supabase"))
        employees.append(Employee(id=str(emp_id), name=row["emp_name"], pod=pod, weekly_off=weekly_off_by_id[emp_id], eligible_roles=frozenset(eligible), designation=row.get("designation") or "", pod_role="Pod Lead" if emp_id in leaders else "Employee"))
    off_rows = [{"employee_id": str(emp_id), "employee": tables["employees"][emp_id]["emp_name"], "day": _weekday_name(day), "pod_leader": emp_id in leaders} for emp_id, day in sorted(weekly_off_by_id.items())]
    holidays = {
        date.fromisoformat(row["holiday_date"])
        for row in tables["holidays"] if not row.get("is_working_override")
    }
    return RosterInput(
        employees=employees, roles=roles, unavailable=unavailable, holidays=holidays,
        rotation_targets=rotation_targets or {},
    ), off_rows


def _roles_from_requirements(requirements: list[dict], tables: dict) -> list[Role]:
    roles = []
    for requirement in sorted(requirements, key=lambda row: row["requirement_id"]):
        bridge_id, shift_id, count = requirement["brand_process_id"], requirement["shift_id"], requirement["required_employees"]
        bridge, shift = tables["bridges"][bridge_id], tables["shifts"][shift_id]
        brand = tables["brands"][bridge["Brand_id"]]["Brand_name"]
        process = tables["processes"][bridge["Process_id"]]["process_name"].strip().upper()
        label = f"{shift['shift_name']} ({shift['start_time']}-{shift['end_time']})"
        calendar = tables["calendars"].get(bridge_id, {})
        open_days = frozenset(range(7)) if calendar.get("operates_sundays", True) else frozenset(range(6))
        closes_for_holiday = bool(calendar.get("observes_public_holidays", False))
        owner = next((
            str(row["Emp_id"])
            for row in tables["dedicated_owners"]
            if row["brand_process_id"] == bridge_id
            and row["shift_id"] == shift_id
            and str(row.get("status", "")).lower() == "active"
            and row["effective_from"] <= requirement["effective_from"]
            and (row.get("effective_to") is None or row["effective_to"] >= requirement["effective_to"])
        ), None)
        for position in range(1, count + 1):
            roles.append(Role(id=f"{bridge_id}:{shift_id}:{requirement['requirement_id']}:{position}", brand=brand, process=process, minimum_staff=1, open_days=open_days, shift=label, close_on_holidays=closes_for_holiday, effective_from=date.fromisoformat(requirement["effective_from"]), effective_to=date.fromisoformat(requirement["effective_to"]), primary_employee_id=owner if position == 1 else None))
    return roles


def _ensure_eligibility_rows(tables: dict, requirements: list[dict]) -> None:
    # Skills are explicit, many-to-many records.  Never infer eligibility from
    # an HR designation: a person can be eligible for one or many processes.
    if not any(
        str(row.get("assignment_type", "")).lower() == "eligibility"
        and str(row.get("status", "")).lower() == "active"
        for row in tables["assignments"]
    ):
        raise RuntimeError(
            "No active employee eligibility is configured. Add active Eligibility rows to "
            "employee_assignments before generating a roster."
        )


def _weekly_offs(tables: dict, roles: list[Role], unavailable: dict[date, set[str]], leaders: set[int]) -> dict[int, int]:
    demand = Counter(role.process for role in roles)
    by_process: dict[str, list[int]] = defaultdict(list)
    bridge_process = {
        bridge_id: tables["processes"][bridge["Process_id"]]["process_name"].strip().upper()
        for bridge_id, bridge in tables["bridges"].items()
    }
    skills_by_employee: dict[int, set[str]] = defaultdict(set)
    for assignment in tables["assignments"]:
        if (
            str(assignment.get("assignment_type", "")).lower() == "eligibility"
            and str(assignment.get("status", "")).lower() == "active"
            and assignment.get("brand_process_id") in bridge_process
        ):
            skills_by_employee[assignment["Emp_id"]].add(bridge_process[assignment["brand_process_id"]])
    for emp_id, employee in tables["employees"].items():
        if str(employee.get("emp_status", "")).lower() == "active" and skills_by_employee.get(emp_id):
            # One weekly off applies to the employee, not separately to each
            # skill.  Use a stable primary skill only for the initial rotation;
            # the optimiser still validates every eligible assignment.
            by_process[sorted(skills_by_employee[emp_id])[0]].append(emp_id)
    offs = {leader: 6 for leader in leaders}
    for process, ids in by_process.items():
        absent_ids_by_weekday: dict[int, set[int]] = defaultdict(set)
        for day, absent_ids in unavailable.items():
            absent_ids_by_weekday[day.weekday()].update(int(emp_id) for emp_id in absent_ids if int(emp_id) in ids)
        for emp_id in sorted(ids):
            absence_days = [day for day in range(6) if emp_id in absent_ids_by_weekday[day]]
            if emp_id not in leaders and absence_days:
                offs[emp_id] = absence_days[0]
        for emp_id in sorted(ids):
            if emp_id in leaders or emp_id in offs:
                continue
            candidates = []
            for day in range(6):
                off_ids = {person for person, off_day in offs.items() if off_day == day and person in ids}
                if len(ids) - len(off_ids | absent_ids_by_weekday[day] | {emp_id}) >= demand[process]:
                    candidates.append(day)
            day = min(candidates or list(range(6)), key=lambda value: (sum(person in ids and off_day == value for person, off_day in offs.items()), value))
            offs[emp_id] = day
    return offs


def _configured_weekly_offs(tables: dict) -> dict[int, int]:
    return {
        row["Emp_id"]: row["weekday"]
        for row in tables["weekly_offs"]
        if str(row.get("status", "")).lower() == "active"
    }


def _save_weekly_offs(offs: dict[int, int]) -> None:
    insert_rows("employee_weekly_offs", [
        {"Emp_id": emp_id, "weekday": weekday, "effective_from": "2026-12-01", "effective_to": "2027-12-31", "status": "Active"}
        for emp_id, weekday in offs.items()
    ])


def _add_availability_unavailability(values: dict[date, set[str]], rows: list[dict], year: int, month: int) -> None:
    for row in rows:
        if str(row.get("status", "")).lower() != "approved":
            continue
        current, end = date.fromisoformat(row["unavailable_from"]), date.fromisoformat(row["unavailable_to"])
        while current <= end:
            if current.year == year and current.month == month:
                values.setdefault(current, set()).add(str(row["Emp_id"]))
            current = date.fromordinal(current.toordinal() + 1)


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _has_overlapping_approved_availability(rows: list[dict], emp_id: int, start: date, end: date) -> bool:
    return any(
        row["Emp_id"] == emp_id
        and str(row.get("status", "")).lower() == "approved"
        and date.fromisoformat(row["unavailable_from"]) <= end
        and date.fromisoformat(row["unavailable_to"]) >= start
        for row in rows
    )


def _within_active_task_assignment(assignments: list[dict], emp_id: int, start: date, end: date) -> bool:
    """The request has to be inside an active company task/eligibility period."""
    return any(
        row["Emp_id"] == emp_id
        and str(row.get("assignment_type", "")).lower() == "eligibility"
        and str(row.get("status", "")).lower() == "active"
        and row.get("assigned_from")
        and row.get("assigned_to")
        and date.fromisoformat(row["assigned_from"]) <= start
        and date.fromisoformat(row["assigned_to"]) >= end
        for row in assignments
    )


def _load_tables() -> dict:
    employees = _fetch_table("Employee", "Emp_id,emp_name,designation,emp_status", "Emp_id.asc")
    pods = _fetch_table("pods", "pod_id,pod_name,pod_lead_id", "pod_id.asc")
    brands = _fetch_table("Brand", "Brand_id,Brand_name,status", "Brand_id.asc")
    processes = _fetch_table("process", "process_id,process_name,status", "process_id.asc")
    bridges = _fetch_table("Brand_processes", "Brand_process_id,Brand_id,Process_id,status", "Brand_process_id.asc")
    shifts = _fetch_table("shifts", "shift_id,shift_name,start_time,end_time", "shift_id.asc")
    requirements = _fetch_table("staffing_requirements", "requirement_id,brand_process_id,shift_id,required_employees,effective_from,effective_to", "requirement_id.asc")
    assignments = _fetch_table("employee_assignments", "assignment_id,Emp_id,brand_process_id,shift_id,assigned_from,assigned_to,status,assignment_type", "assignment_id.asc")
    calendars = _fetch_table("operating_calendars", "brand_process_id,operates_sundays,observes_public_holidays,effective_from,effective_to,status", "calendar_id.asc")
    holidays = _fetch_table("india_holidays", "holiday_date,holiday_name,is_working_override", "holiday_id.asc")
    weekly_offs = _fetch_table("employee_weekly_offs", "Emp_id,weekday,effective_from,effective_to,status", "weekly_off_id.asc")
    availability = _fetch_table("employee_availability", "availability_id,Emp_id,unavailable_from,unavailable_to,availability_type,status", "availability_id.asc")
    dedicated_owners = _fetch_table("brand_process_shift_owners", "brand_process_id,shift_id,Emp_id,effective_from,effective_to,status", "dedicated_owner_id.asc")
    memberships = _fetch_table("employee_pod_memberships", "Emp_id,pod_id,effective_from,effective_to,status,is_primary", "employee_pod_membership_id.asc")
    pod_names = {row["pod_id"]: row["pod_name"] for row in pods}
    active_memberships = [
        row for row in memberships
        if str(row.get("status", "")).lower() == "active"
        and row.get("is_primary", True)
        and row["pod_id"] in pod_names
    ]
    return {"employees": {row["Emp_id"]: row for row in employees}, "pod_by_lead": {row["pod_lead_id"]: row["pod_name"] for row in pods if row.get("pod_lead_id") is not None}, "pod_by_employee": {row["Emp_id"]: pod_names[row["pod_id"]] for row in active_memberships}, "brands": {row["Brand_id"]: row for row in brands}, "processes": {row["process_id"]: row for row in processes}, "bridges": {row["Brand_process_id"]: row for row in bridges}, "shifts": {row["shift_id"]: row for row in shifts}, "requirements": requirements, "assignments": assignments, "calendars": {row["brand_process_id"]: row for row in calendars if str(row.get("status", "")).lower() == "active"}, "holidays": holidays, "weekly_offs": weekly_offs, "availability": availability, "dedicated_owners": dedicated_owners}


def _unavailable_by_date(assignments: list[dict], year: int, month: int) -> dict[date, set[str]]:
    values: dict[date, set[str]] = defaultdict(set)
    for row in assignments:
        if str(row.get("status", "")).lower() != "unavailable" or str(row.get("assignment_type", "")).lower() != "absence":
            continue
        current, end = date.fromisoformat(row["assigned_from"]), date.fromisoformat(row["assigned_to"])
        while current <= end:
            if current.year == year and current.month == month:
                values[current].add(str(row["Emp_id"]))
            current = date.fromordinal(current.toordinal() + 1)
    return values


def _has_active_absence(assignments: list[dict], emp_id: int, absence_date: date) -> bool:
    stamp = absence_date.isoformat()
    return any(row["Emp_id"] == emp_id and str(row.get("status", "")).lower() == "unavailable" and str(row.get("assignment_type", "")).lower() == "absence" and row.get("assigned_from") <= stamp <= row.get("assigned_to") for row in assignments)


def _overlaps_month(row: dict, year: int, month: int) -> bool:
    start, end = date(year, month, 1), date(year + (month == 12), month % 12 + 1, 1)
    return date.fromisoformat(row["effective_from"]) < end and date.fromisoformat(row["effective_to"]) >= start


def _weekday_name(day: int) -> str:
    return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[day]


def _update_absence_status(assignment_ids: list[int], status: str) -> None:
    _write_table("employee_assignments", "PATCH", "?assignment_id=in.(" + ",".join(map(str, assignment_ids)) + ")", {"status": status})


def _write_table(table: str, method: str, query: str, payload: dict) -> None:
    load_local_env()
    base_url = _normalise_project_url(os.getenv("SUPABASE_URL", ""))
    key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    request = Request(f"{base_url}/rest/v1/{table}{query}", data=json.dumps(payload).encode(), headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            response.read()
    except HTTPError as error:
        raise RuntimeError(f"Supabase rejected the roster update (HTTP {error.code}): {error.read().decode('utf-8', errors='replace')}") from error
