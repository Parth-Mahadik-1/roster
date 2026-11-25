"""Private employee dashboard API backed by the existing Supabase tables."""

from __future__ import annotations

from calendar import month_name, monthrange
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .leave_requests import create_leave_request
from .manager_auth import current_session
from .roster_adjustments import latest_persisted_roster
from .supabase_repository import _fetch_table

router = APIRouter(prefix="/api/v1/employee", tags=["Employee"])


class EmployeeLeaveRequest(BaseModel):
    unavailable_from: date
    unavailable_to: date
    request_type: str = Field(pattern="^(Leave|Comp Off|Unavailable)$")


def _employee_id(request: Request) -> int:
    session = current_session(request, "employee")
    if session is None or session.role != "employee":
        raise HTTPException(403, "Employee access is required.")
    return session.employee_id


def build_employee_dashboard(employee_id: int, year: int, month: int) -> dict:
    employees = _fetch_table("Employee", "Emp_id,emp_name,email,designation,emp_status", "Emp_id.asc")
    employee = next((row for row in employees if row["Emp_id"] == employee_id), None)
    if employee is None or str(employee.get("emp_status", "")).lower() != "active":
        raise HTTPException(403, "The employee account is no longer active.")

    roster = latest_persisted_roster(year, month)
    assignments = []
    updates = []
    if roster:
        for row in roster["assignments"]:
            if int(row["employee_id"]) != employee_id:
                continue
            displayed = {
                "id": row["role_id"],
                "date": row["date"],
                "brand": row["brand"],
                "process": row["process"],
                "shift": row["shift"].split(" (", 1)[0],
                "starts_at": row["start_time"],
                "ends_at": row["end_time"],
                "hours": row["hours"],
                "status": row["status"],
                "assignment_source": row["assignment_source"],
            }
            if row["assignment_source"] == "Coverage":
                change = {"type": "Coverage", "updated_at": None, "previous": None, "current": {**displayed}}
                displayed["change"] = change
                updates.append(change)
            assignments.append(displayed)

    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    leave_rows = _fetch_table(
        "employee_leave_requests",
        "leave_request_id,Emp_id,unavailable_from,unavailable_to,request_type,status,review_note,requested_at,reviewed_at",
        "leave_request_id.asc",
    )
    requests = [
        {
            "id": row["leave_request_id"],
            "from": row["unavailable_from"],
            "to": row["unavailable_to"],
            "type": row["request_type"],
            "status": row["status"],
            "review_note": row.get("review_note"),
            "requested_at": row["requested_at"],
        }
        for row in leave_rows
        if row["Emp_id"] == employee_id
        and row["unavailable_from"] <= end.isoformat()
        and row["unavailable_to"] >= start.isoformat()
    ]
    return {
        "profile": {
            "employee_id": employee["Emp_id"],
            "name": employee["emp_name"],
            "email": employee["email"],
            "designation": employee["designation"],
        },
        "calendar": {
            "year": year,
            "month": month,
            "label": f"{month_name[month]} {year}",
            "starts_on": start.isoformat(),
            "ends_on": end.isoformat(),
        },
        "summary": {
            "scheduled_days": len({item["date"] for item in assignments}),
            "scheduled_hours": sum(item["hours"] for item in assignments),
            "pending_requests": sum(item["status"] == "Pending" for item in requests),
        },
        "roster": {
            "run_id": roster["roster_run_id"] if roster else None,
            "updated_at": roster["created_at"] if roster else None,
            "has_previous_version": False,
        },
        "schedule": assignments,
        "updates": sorted(updates, key=lambda item: (item["current"] or item["previous"])["date"]),
        "requests": requests,
    }


@router.get("/dashboard")
def employee_dashboard(
    request: Request,
    year: int = Query(ge=2025, le=2035),
    month: int = Query(ge=1, le=12),
) -> dict:
    return build_employee_dashboard(_employee_id(request), year, month)


@router.post("/requests")
def submit_employee_request(request: Request, leave: EmployeeLeaveRequest) -> dict:
    employee_id = _employee_id(request)
    try:
        created = create_leave_request(
            employee_id,
            leave.unavailable_from,
            leave.unavailable_to,
            leave.request_type,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, str(error)) from error
    return {"request": created}
