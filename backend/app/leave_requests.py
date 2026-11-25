"""Pending leave requests and manager decision workflow."""

from __future__ import annotations

from datetime import date
import re

from .replacement_simulator import save_simulated_approval, simulate_request
from .roster_persistence import insert_rows, update_rows
from .supabase_repository import _fetch_table


REQUEST_TYPES = {"Leave", "Comp Off", "Unavailable"}
COVERAGE_IDS = re.compile(r"\s*\[coverage_ids:([0-9,]+)\]\s*$")


def create_leave_request(emp_id: int, unavailable_from: date, unavailable_to: date, request_type: str) -> dict:
    if request_type not in REQUEST_TYPES:
        raise ValueError("Request type must be Leave, Comp Off, or Unavailable.")
    if unavailable_to < unavailable_from:
        raise ValueError("The request end date cannot be before the start date.")
    if (unavailable_to - unavailable_from).days + 1 > 31:
        raise ValueError("A request cannot exceed 31 consecutive calendar days.")
    if unavailable_from.year != unavailable_to.year or unavailable_from.month != unavailable_to.month:
        raise ValueError("Submit separate requests for separate roster months.")
    if request_type == "Unavailable" and unavailable_from != unavailable_to:
        raise ValueError("Unavailable is a one-day request.")
    employees = _fetch_table("Employee", "Emp_id,emp_status", "Emp_id.asc")
    if not any(row["Emp_id"] == emp_id and str(row.get("emp_status", "")).lower() == "active" for row in employees):
        raise ValueError(f"Employee ID {emp_id} is not an active Employee record.")
    existing = _fetch_table(
        "employee_leave_requests",
        "leave_request_id,Emp_id,unavailable_from,unavailable_to,status",
        "leave_request_id.asc",
    )
    if any(
        row["Emp_id"] == emp_id and str(row.get("status", "")).lower() == "pending"
        and row["unavailable_from"] <= unavailable_to.isoformat()
        and row["unavailable_to"] >= unavailable_from.isoformat()
        for row in existing
    ):
        raise ValueError("This employee already has a pending request that overlaps those dates.")
    return insert_rows("employee_leave_requests", [{
        "Emp_id": emp_id,
        "unavailable_from": unavailable_from.isoformat(),
        "unavailable_to": unavailable_to.isoformat(),
        "request_type": request_type,
        "status": "Pending",
    }])[0]


def review_leave_request(request_id: int, decision: str, review_note: str = "") -> dict:
    if decision not in {"Approved", "Rejected"}:
        raise ValueError("Manager decision must be Approved or Rejected.")
    requests = _fetch_table(
        "employee_leave_requests",
        "leave_request_id,Emp_id,unavailable_from,unavailable_to,request_type,status",
        "leave_request_id.asc",
    )
    request = next((row for row in requests if row["leave_request_id"] == request_id), None)
    if request is None:
        raise ValueError("Leave request was not found.")
    if str(request.get("status", "")).lower() != "pending":
        raise ValueError("Only a pending request can be reviewed.")
    payload = {"status": decision, "review_note": review_note or None}
    operation = None
    if decision == "Approved":
        plan = simulate_request(request_id)
        if not plan["feasible"]:
            raise ValueError("The replacement simulation has uncovered tasks. Review the gaps before approving.")
        operation = save_simulated_approval(plan)
        payload["availability_id"] = operation["availability"]["availability_id"]
        coverage_ids = [row["assignment_id"] for row in operation["coverage_assignments"]]
        summary = _coverage_review_note(plan)
        if review_note:
            summary = f"{summary} Manager note: {review_note}"
        payload["review_note"] = _note_with_coverage_ids(summary, coverage_ids)
    updated = update_rows("employee_leave_requests", f"leave_request_id=eq.{request_id}", payload)
    return {"request": updated[0], "operation": operation}


def _coverage_review_note(plan: dict) -> str:
    """Persist the replacement outcome so it remains visible after reload."""
    if not plan["replacements"]:
        return "No replacement assignment required; no scheduled task was affected."
    replacements = []
    seen = set()
    for item in plan["replacements"]:
        employee = item["recommended"]
        key = employee["employee_id"]
        if key in seen:
            continue
        seen.add(key)
        replacements.append(f"{employee['employee']} (#{key})")
    visible = replacements[:4]
    extra = len(replacements) - len(visible)
    assignees = ", ".join(visible) + (f" and {extra} more" if extra else "")
    return f"Coverage assigned to {assignees} for {len(plan['replacements'])} task(s)."[:300]


def visible_review_note(note: str | None) -> str:
    return COVERAGE_IDS.sub("", note or "").strip()


def _note_with_coverage_ids(note: str, assignment_ids: list[int]) -> str:
    marker = f" [coverage_ids:{','.join(map(str, assignment_ids))}]" if assignment_ids else ""
    return note[:300 - len(marker)].rstrip() + marker


def cancel_leave_approval(request_id: int) -> dict:
    """Cancel one approval and reverse only records created for that request."""
    requests = _fetch_table(
        "employee_leave_requests",
        "leave_request_id,Emp_id,unavailable_from,unavailable_to,request_type,status,review_note,availability_id",
        "leave_request_id.asc",
    )
    request = next((row for row in requests if row["leave_request_id"] == request_id), None)
    if request is None:
        raise ValueError("Leave request was not found.")
    if str(request.get("status", "")).lower() != "approved":
        raise ValueError("Only an approved request can be cancelled.")

    note = request.get("review_note") or ""
    marker = COVERAGE_IDS.search(note)
    coverage_ids = [int(value) for value in marker.group(1).split(",")] if marker else []
    if coverage_ids:
        update_rows(
            "employee_assignments",
            "assignment_id=in.(" + ",".join(map(str, coverage_ids)) + ")",
            {"status": "Restored"},
        )
    if request.get("availability_id") is not None:
        update_rows(
            "employee_availability",
            f"availability_id=eq.{request['availability_id']}",
            {"status": "Restored"},
        )
    updated = update_rows(
        "employee_leave_requests",
        f"leave_request_id=eq.{request_id}",
        {"status": "Cancelled", "review_note": "Approval cancelled by manager."},
    )
    return {
        "request": updated[0],
        "restored_availability_id": request.get("availability_id"),
        "restored_coverage_assignment_ids": coverage_ids,
    }
