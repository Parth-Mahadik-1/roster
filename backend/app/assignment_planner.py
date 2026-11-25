from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .supabase_repository import _fetch_table, _normalise_project_url
from .config import load_local_env
import os


PROCESS_KEYWORDS = {
    "CS": "customer support",
    "OM": "order management",
    "RET": "retention",
    "NDR": "ndr",
    "ACC": "acc",
}


@dataclass(frozen=True)
class PlannedAssignment:
    emp_id: int
    brand_process_id: int
    shift_id: int
    assigned_from: str
    assigned_to: str
    assignment_type: str

    def payload(self) -> dict:
        return {
            "Emp_id": self.emp_id,
            "brand_process_id": self.brand_process_id,
            "shift_id": self.shift_id,
            "assigned_from": self.assigned_from,
            "assigned_to": self.assigned_to,
            "status": "Active",
            "assignment_type": self.assignment_type,
        }


def build_initial_plan() -> list[PlannedAssignment]:
    employees = _fetch_table("Employee", "Emp_id,designation,emp_status", "Emp_id.asc")
    processes = _fetch_table("process", "process_id,process_name,status", "process_id.asc")
    bridges = _fetch_table("Brand_processes", "Brand_process_id,Process_id,status", "Brand_process_id.asc")
    requirements = _fetch_table(
        "staffing_requirements",
        "requirement_id,brand_process_id,shift_id,required_employees,effective_from,effective_to",
        "requirement_id.asc",
    )
    active_employees = [employee for employee in employees if employee["emp_status"].lower() == "active"]
    active_processes = {process["process_id"]: process["process_name"].strip() for process in processes if process["status"].lower() == "active"}
    active_bridges = {
        bridge["Brand_process_id"]: bridge["Process_id"]
        for bridge in bridges
        if bridge["status"].lower() == "active" and bridge["Process_id"] in active_processes
    }
    usable_requirements = [
        requirement for requirement in requirements if requirement["brand_process_id"] in active_bridges and requirement["required_employees"] > 0
    ]
    required_positions = sum(requirement["required_employees"] for requirement in usable_requirements)
    if len(active_employees) < required_positions:
        raise RuntimeError(f"Need {required_positions} active employees but only {len(active_employees)} are available.")

    employees_by_process: dict[str, list[dict]] = defaultdict(list)
    for employee in active_employees:
        designation = employee["designation"].lower()
        for process_code, keyword in PROCESS_KEYWORDS.items():
            if keyword in designation:
                employees_by_process[process_code].append(employee)
    unused = {employee["Emp_id"]: employee for employee in active_employees}
    primary: list[PlannedAssignment] = []
    primary_by_employee: dict[int, PlannedAssignment] = {}
    for requirement in usable_requirements:
        process_name = active_processes[active_bridges[requirement["brand_process_id"]]]
        process_code = process_name.upper()
        preferred = [employee for employee in employees_by_process.get(process_code, []) if employee["Emp_id"] in unused]
        candidates = preferred or [unused[employee_id] for employee_id in sorted(unused)]
        for employee in candidates[: requirement["required_employees"]]:
            assignment = PlannedAssignment(
                emp_id=employee["Emp_id"],
                brand_process_id=requirement["brand_process_id"],
                shift_id=requirement["shift_id"],
                assigned_from=requirement["effective_from"],
                assigned_to=requirement["effective_to"],
                assignment_type="Primary",
            )
            primary.append(assignment)
            primary_by_employee[assignment.emp_id] = assignment
            unused.pop(assignment.emp_id)

    backups: list[PlannedAssignment] = []
    used_backup_keys: set[tuple[int, int, int]] = set()
    for requirement in usable_requirements:
        target_key = (requirement["brand_process_id"], requirement["shift_id"])
        primary_employees_for_target = {
            assignment.emp_id for assignment in primary if (assignment.brand_process_id, assignment.shift_id) == target_key
        }
        candidates = sorted(
            primary_by_employee.values(),
            key=lambda assignment: (assignment.shift_id == requirement["shift_id"], assignment.emp_id),
        )
        backup_source = next(
            (
                assignment
                for assignment in candidates
                if assignment.emp_id not in primary_employees_for_target
                and (assignment.emp_id, requirement["brand_process_id"], requirement["shift_id"]) not in used_backup_keys
            ),
            None,
        )
        if backup_source is None:
            continue
        backup = PlannedAssignment(
            emp_id=backup_source.emp_id,
            brand_process_id=requirement["brand_process_id"],
            shift_id=requirement["shift_id"],
            assigned_from=requirement["effective_from"],
            assigned_to=requirement["effective_to"],
            assignment_type="Backup",
        )
        backups.append(backup)
        used_backup_keys.add((backup.emp_id, backup.brand_process_id, backup.shift_id))

    plan = primary + backups
    _validate_plan(plan, required_positions, primary_by_employee)
    return plan


def apply_initial_plan(plan: list[PlannedAssignment]) -> list[dict]:
    existing = _fetch_table("employee_assignments", "assignment_id", "assignment_id.asc")
    if existing:
        raise RuntimeError("employee_assignments already has records. The initial plan will not overwrite them.")
    return _insert_plan(plan)


def build_support_backup_plan() -> list[PlannedAssignment]:
    """Allocate unassigned, active employees to backup only within their recorded process designation."""
    employees = _fetch_table("Employee", "Emp_id,designation,emp_status", "Emp_id.asc")
    processes = _fetch_table("process", "process_id,process_name,status", "process_id.asc")
    bridges = _fetch_table("Brand_processes", "Brand_process_id,Process_id,status", "Brand_process_id.asc")
    requirements = _fetch_table(
        "staffing_requirements",
        "brand_process_id,shift_id,required_employees,effective_from,effective_to",
        "brand_process_id.asc",
    )
    assignments = _fetch_table(
        "employee_assignments",
        "Emp_id,brand_process_id,shift_id,assigned_from,assigned_to,assignment_type,status",
        "assignment_id.asc",
    )
    process_names = {row["process_id"]: row["process_name"].strip().upper() for row in processes if str(row.get("status", "")).lower() == "active"}
    bridge_processes = {
        row["Brand_process_id"]: process_names[row["Process_id"]]
        for row in bridges
        if str(row.get("status", "")).lower() == "active" and row["Process_id"] in process_names
    }
    primary_employee_ids = {row["Emp_id"] for row in assignments if str(row.get("assignment_type", "")).lower() == "primary" and str(row.get("status", "")).lower() == "active"}
    existing_backup_keys = {
        (row["Emp_id"], row.get("brand_process_id"), row.get("shift_id"))
        for row in assignments
        if str(row.get("assignment_type", "")).lower() == "backup" and str(row.get("status", "")).lower() == "active"
    }
    plan: list[PlannedAssignment] = []
    for employee in employees:
        if str(employee.get("emp_status", "")).lower() != "active" or employee["Emp_id"] in primary_employee_ids:
            continue
        designation = str(employee.get("designation", "")).lower()
        employee_processes = [code for code, keyword in PROCESS_KEYWORDS.items() if keyword in designation]
        for requirement in requirements:
            process_code = bridge_processes.get(requirement["brand_process_id"])
            key = (employee["Emp_id"], requirement["brand_process_id"], requirement["shift_id"])
            if process_code not in employee_processes or key in existing_backup_keys:
                continue
            plan.append(
                PlannedAssignment(
                    emp_id=employee["Emp_id"],
                    brand_process_id=requirement["brand_process_id"],
                    shift_id=requirement["shift_id"],
                    assigned_from=requirement["effective_from"],
                    assigned_to=requirement["effective_to"],
                    assignment_type="Backup",
                )
            )
            existing_backup_keys.add(key)
    return plan


def apply_support_backup_plan(plan: list[PlannedAssignment]) -> list[dict]:
    if not plan:
        return []
    return _insert_plan(plan)


def _insert_plan(plan: list[PlannedAssignment]) -> list[dict]:
    load_local_env()
    base_url = _normalise_project_url(os.getenv("SUPABASE_URL", ""))
    key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    request = Request(
        f"{base_url}/rest/v1/employee_assignments",
        data=json.dumps([assignment.payload() for assignment in plan]).encode(),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            created = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase rejected the initial assignment insert (HTTP {error.code}): {detail}") from error
    if len(created) != len(plan):
        raise RuntimeError(f"Expected {len(plan)} new assignments but Supabase returned {len(created)}.")
    return created


def plan_summary(plan: list[PlannedAssignment]) -> dict:
    primary = [assignment for assignment in plan if assignment.assignment_type == "Primary"]
    backup = [assignment for assignment in plan if assignment.assignment_type == "Backup"]
    return {
        "total": len(plan),
        "primary": len(primary),
        "backup": len(backup),
        "regular_backup_employees": len({assignment.emp_id for assignment in backup} & {assignment.emp_id for assignment in primary}),
    }


def _validate_plan(plan: list[PlannedAssignment], required_positions: int, primary_by_employee: dict[int, PlannedAssignment]) -> None:
    primary = [assignment for assignment in plan if assignment.assignment_type == "Primary"]
    if len(primary) != required_positions:
        raise RuntimeError("The plan does not cover every required primary position.")
    if len(primary_by_employee) != len(primary):
        raise RuntimeError("An employee was assigned as primary more than once.")
    for assignment in plan:
        if assignment.assigned_from > assignment.assigned_to:
            raise RuntimeError("An assignment has an invalid date range.")
    for assignment in plan:
        if assignment.assignment_type == "Backup" and assignment.emp_id not in primary_by_employee:
            raise RuntimeError("A backup employee must also hold a primary assignment.")
