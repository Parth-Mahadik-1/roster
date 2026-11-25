"""Targeted replacement planning for pending leave and sudden absence requests."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date

from .roster_adjustments import latest_persisted_roster
from .roster_persistence import insert_rows, update_rows
from .supabase_repository import _fetch_table

MAX_TASKS_PER_DAY = 2


def simulate_request(request_id: int) -> dict:
    requests = _fetch_table(
        "employee_leave_requests",
        "leave_request_id,Emp_id,unavailable_from,unavailable_to,request_type,status",
        "leave_request_id.asc",
    )
    request = next((row for row in requests if row["leave_request_id"] == request_id), None)
    if request is None:
        raise ValueError("Leave request was not found.")
    if str(request.get("status", "")).lower() != "pending":
        raise ValueError("Only a pending request can be simulated.")
    start = date.fromisoformat(request["unavailable_from"])
    end = date.fromisoformat(request["unavailable_to"])
    if start.year != end.year or start.month != end.month:
        raise ValueError("Run separate replacement simulations for each roster month.")
    roster = latest_persisted_roster(start.year, start.month)
    if roster is None:
        raise ValueError("Generate and save the monthly roster before simulating a replacement.")
    request_dates = {day.isoformat() for day in _dates(start, end)}
    affected = [
        row for row in roster["assignments"]
        if int(row["employee_id"]) == request["Emp_id"] and row["date"] in request_dates
    ]

    employees = {
        row["Emp_id"]: row
        for row in _fetch_table("Employee", "Emp_id,emp_name,designation,emp_status", "Emp_id.asc")
    }
    eligibility = _fetch_table(
        "employee_assignments",
        "assignment_id,Emp_id,brand_process_id,shift_id,assigned_from,assigned_to,status,assignment_type",
        "assignment_id.asc",
    )
    weekly_offs = {
        row["Emp_id"]: row["weekday"]
        for row in _fetch_table(
            "employee_weekly_offs",
            "Emp_id,weekday,effective_from,effective_to,status",
            "weekly_off_id.asc",
        )
        if str(row.get("status", "")).lower() == "active"
    }
    unavailable = _fetch_table(
        "employee_availability",
        "Emp_id,unavailable_from,unavailable_to,status",
        "availability_id.asc",
    )
    pod_leaders = {
        row["pod_lead_id"]
        for row in _fetch_table("pods", "pod_id,pod_name,pod_lead_id", "pod_id.asc")
        if row.get("pod_lead_id") is not None
    }
    tasks_by_employee_day: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for assignment in roster["assignments"]:
        tasks_by_employee_day[(int(assignment["employee_id"]), assignment["date"])].append(assignment)
    planned_extra: dict[tuple[int, str], int] = defaultdict(int)
    replacements = []
    gaps = []

    for assignment in affected:
        workday = date.fromisoformat(assignment["date"])
        candidates = []
        for employee_id, employee in employees.items():
            if employee_id == request["Emp_id"] or str(employee.get("emp_status", "")).lower() != "active":
                continue
            if weekly_offs.get(employee_id) == workday.weekday():
                continue
            if _is_unavailable(unavailable, employee_id, workday):
                continue
            if employee_id in pod_leaders and str(assignment["shift"]).lower().startswith("night"):
                continue
            if not _is_eligible(eligibility, employee_id, assignment["brand_process_id"], assignment["shift_id"], workday):
                continue
            current_tasks = tasks_by_employee_day[(employee_id, assignment["date"])]
            current_load = len(current_tasks) + planned_extra[(employee_id, assignment["date"])]
            if current_load >= MAX_TASKS_PER_DAY:
                continue
            same_shift = any(task["shift_id"] == assignment["shift_id"] for task in current_tasks)
            candidates.append({
                "employee_id": employee_id,
                "employee": employee["emp_name"],
                "designation": employee.get("designation") or "",
                "already_working": bool(current_tasks),
                "same_shift": same_shift,
                "current_task_count": len(current_tasks),
                "current_tasks": [
                    {"brand": task["brand"], "process": task["process"], "shift": task["shift"]}
                    for task in current_tasks
                ],
            })
        candidates.sort(key=lambda item: (
            not item["same_shift"],
            not item["already_working"],
            item["current_task_count"],
            item["employee"].casefold(),
        ))
        if not candidates:
            gaps.append({
                "date": assignment["date"],
                "brand": assignment["brand"],
                "process": assignment["process"],
                "shift": assignment["shift"],
                "message": "No present eligible employee has capacity for this task.",
            })
            continue
        recommended = candidates[0]
        planned_extra[(recommended["employee_id"], assignment["date"])] += 1
        replacements.append({
            "date": assignment["date"],
            "absent_employee_id": request["Emp_id"],
            "brand_process_id": assignment["brand_process_id"],
            "shift_id": assignment["shift_id"],
            "brand": assignment["brand"],
            "process": assignment["process"],
            "shift": assignment["shift"],
            "recommended": recommended,
            "alternatives": candidates[1:5],
        })
    employee = employees.get(request["Emp_id"], {})
    return {
        "request_id": request_id,
        "employee_id": request["Emp_id"],
        "employee": employee.get("emp_name", f"Employee {request['Emp_id']}"),
        "from": request["unavailable_from"],
        "to": request["unavailable_to"],
        "type": request["request_type"],
        "roster_run_id": roster["roster_run_id"],
        "affected_task_count": len(affected),
        "unaffected_task_count": max(len(roster["assignments"]) - len(affected), 0),
        "feasible": not gaps,
        "replacements": replacements,
        "gaps": gaps,
        "explanation": (
            "Only the absent employee's affected tasks are considered. Every other roster assignment remains unchanged. "
            "Present eligible employees may keep their own task and cover one additional task."
        ),
    }


def save_simulated_approval(plan: dict) -> dict:
    availability = insert_rows("employee_availability", [{
        "Emp_id": plan["employee_id"],
        "unavailable_from": plan["from"],
        "unavailable_to": plan["to"],
        "availability_type": plan["type"],
        "status": "Approved",
        "reason": "Approved after targeted replacement simulation",
    }])[0]
    coverage_rows = [
        {
            "Emp_id": replacement["recommended"]["employee_id"],
            "brand_process_id": replacement["brand_process_id"],
            "shift_id": replacement["shift_id"],
            "assigned_from": replacement["date"],
            "assigned_to": replacement["date"],
            "status": "Active",
            "assignment_type": "Coverage",
        }
        for replacement in plan["replacements"]
    ]
    coverage = insert_rows("employee_assignments", coverage_rows) if coverage_rows else []
    return {"availability": availability, "coverage_assignments": coverage, "simulation": plan}


def retire_month_coverage(year: int, month: int) -> list[int]:
    """Retire temporary coverage after a full optimized month is saved."""
    start = date(year, month, 1).isoformat()
    end = date(year, month, monthrange(year, month)[1]).isoformat()
    coverage = _fetch_table(
        "employee_assignments",
        "assignment_id,assigned_from,assigned_to,status,assignment_type",
        "assignment_id.asc",
        {
            "assignment_type": "eq.Coverage",
            "status": "eq.Active",
            "assigned_from": f"lte.{end}",
            "assigned_to": f"gte.{start}",
        },
    )
    assignment_ids = [row["assignment_id"] for row in coverage]
    if assignment_ids:
        update_rows(
            "employee_assignments",
            "assignment_id=in.(" + ",".join(map(str, assignment_ids)) + ")",
            {"status": "Restored"},
        )
    return assignment_ids


def _is_eligible(rows: list[dict], employee_id: int, brand_process_id: int, shift_id: int, workday: date) -> bool:
    return any(
        row["Emp_id"] == employee_id
        and row["brand_process_id"] == brand_process_id
        and row["shift_id"] == shift_id
        and str(row.get("assignment_type", "")).lower() == "eligibility"
        and str(row.get("status", "")).lower() == "active"
        and row.get("assigned_from")
        and row.get("assigned_to")
        and row["assigned_from"] <= workday.isoformat() <= row["assigned_to"]
        for row in rows
    )


def _is_unavailable(rows: list[dict], employee_id: int, workday: date) -> bool:
    stamp = workday.isoformat()
    return any(
        row["Emp_id"] == employee_id
        and str(row.get("status", "")).lower() == "approved"
        and row["unavailable_from"] <= stamp <= row["unavailable_to"]
        for row in rows
    )


def _dates(start: date, end: date) -> list[date]:
    return [date.fromordinal(day) for day in range(start.toordinal(), end.toordinal() + 1)]
