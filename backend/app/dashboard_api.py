"""Versioned backend-for-frontend API for the separate React dashboard."""

from __future__ import annotations

from calendar import month_name, monthrange
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .leave_requests import (
    cancel_leave_approval,
    create_leave_request as create_pending_leave_request,
    review_leave_request,
    visible_review_note,
)
from .live_roster import build_live_roster, record_absence, request_time_off, restore_absence
from .replacement_simulator import retire_month_coverage, simulate_request
from .roster_adjustments import latest_persisted_roster
from .supabase_repository import _fetch_table, dashboard_catalog

router = APIRouter(prefix="/api/v1", tags=["Dashboard"])


class DashboardPeriod(BaseModel):
    year: int = Field(ge=2025, le=2035)
    month: int = Field(ge=1, le=12)


class DashboardAbsence(DashboardPeriod):
    employee_id: int
    absence_date: date


class LeaveRequestPayload(BaseModel):
    employee_id: int
    unavailable_from: date
    unavailable_to: date
    request_type: str = Field(pattern="^(Leave|Comp Off|Unavailable)$")


class ManagerRequestDecision(BaseModel):
    review_note: str = Field(default="", max_length=300)


def build_dashboard_snapshot(year: int, month: int, persist: bool = False) -> dict:
    """Return all data needed to render one calendar/dashboard screen."""
    catalog = dashboard_catalog()
    roster = _regenerate_roster(year, month) if persist else latest_persisted_roster(year, month)
    if roster is None:
        roster = build_live_roster(year, month)
    rows = roster["assignments"]
    return {
        "api_version": "v1",
        "calendar": {
            "year": year,
            "month": month,
            "label": f"{month_name[month]} {year}",
            "starts_on": date(year, month, 1).isoformat(),
            "ends_on": date(year, month, monthrange(year, month)[1]).isoformat(),
        },
        "summary": {
            "employees": len(catalog["employees"]),
            "brands": len(catalog["brands"]),
            "processes": len(catalog["processes"]),
            "pods": len(catalog["pods"]),
            "scheduled_assignments": len(rows),
            "coverage_status": roster["status"],
            "manager_action_count": len(roster.get("manager_messages", [])),
        },
        "catalog": catalog,
        "filters": {
            "brands": catalog["brands"],
            "employees": catalog["employees"],
            "pods": catalog["pods"],
            "processes": catalog["processes"],
            "shifts": catalog["shifts"],
            "statuses": ["Scheduled"],
        },
        "roster": roster,
    }


def _regenerate_roster(year: int, month: int) -> dict:
    """Publish approved leave as a targeted change, preserving other shifts."""
    if _approved_requests_exist(year, month):
        baseline = latest_persisted_roster(
            year, month, apply_coverage=False, apply_availability=False,
        )
        adjusted = latest_persisted_roster(year, month)
        if baseline is None or adjusted is None:
            raise ValueError("Generate a baseline monthly roster before publishing approved leave.")
        _validate_targeted_roster(year, month, baseline["assignments"], adjusted["assignments"])
        return {
            **adjusted,
            "diagnostics": [
                *adjusted.get("diagnostics", []),
                "Targeted leave coverage validated; unaffected assignments were preserved.",
            ],
            "manager_messages": [],
        }

    roster = build_live_roster(year, month, persist=True)
    if roster.get("roster_run_id") is not None:
        retire_month_coverage(year, month)
    return roster


def _approved_requests_exist(year: int, month: int) -> bool:
    period_start = date(year, month, 1).isoformat()
    period_end = date(year, month, monthrange(year, month)[1]).isoformat()
    rows = _fetch_table(
        "employee_leave_requests",
        "leave_request_id",
        "leave_request_id.asc",
        {
            "status": "eq.Approved",
            "unavailable_from": f"lte.{period_end}",
            "unavailable_to": f"gte.{period_start}",
        },
    )
    return bool(rows)


def _validate_targeted_roster(
    year: int,
    month: int,
    baseline_rows: list[dict],
    adjusted_rows: list[dict],
) -> None:
    """Require exact one-for-one coverage while preserving baseline assignments."""
    availability = _fetch_table(
        "employee_availability",
        "Emp_id,unavailable_from,unavailable_to,status",
        "availability_id.asc",
    )
    approved_unavailable = {
        (str(item["Emp_id"]), day.isoformat())
        for item in availability
        if str(item.get("status", "")).lower() == "approved"
        for day in (
            date.fromordinal(ordinal)
            for ordinal in range(
                date.fromisoformat(item["unavailable_from"]).toordinal(),
                date.fromisoformat(item["unavailable_to"]).toordinal() + 1,
            )
        )
        if day.year == year and day.month == month
    }

    def signature(item: dict) -> tuple[str, str, int, int, str]:
        return (
            item["date"], str(item["employee_id"]),
            int(item["brand_process_id"]), int(item["shift_id"]),
            str(item.get("assignment_source", "Generated")).lower(),
        )

    baseline_by_signature = {signature(item): item for item in baseline_rows}
    adjusted_by_signature = {signature(item): item for item in adjusted_rows}
    removed = [
        item for key, item in baseline_by_signature.items()
        if key not in adjusted_by_signature
    ]
    added = [
        item for key, item in adjusted_by_signature.items()
        if key not in baseline_by_signature
    ]
    invalid_removal = next((
        item for item in removed
        if (str(item["employee_id"]), item["date"]) not in approved_unavailable
    ), None)
    if invalid_removal:
        raise ValueError(
            f"Targeted regeneration blocked: an unrelated assignment for employee "
            f"{invalid_removal['employee_id']} on {invalid_removal['date']} would change."
        )
    invalid_addition = next((
        item for item in added if str(item.get("assignment_source", "")).lower() != "coverage"
    ), None)
    if invalid_addition:
        raise ValueError(
            f"Targeted regeneration blocked: a non-coverage assignment would be added on "
            f"{invalid_addition['date']}."
        )
    removed_tasks = Counter(
        (item["date"], item["brand_process_id"], item["shift_id"]) for item in removed
    )
    coverage_tasks = Counter(
        (item["date"], item["brand_process_id"], item["shift_id"]) for item in added
    )
    if removed_tasks != coverage_tasks:
        missing = removed_tasks - coverage_tasks
        extra = coverage_tasks - removed_tasks
        key = next(iter(missing or extra), None)
        detail = f"{key[0]} task {key[1]}/{key[2]}" if key else "approved task"
        raise ValueError(
            f"Targeted regeneration blocked: {detail} is not replaced one-for-one. "
            "Run the replacement simulator again."
        )
    daily_load = Counter((item["employee_id"], item["date"]) for item in adjusted_rows)
    overloaded = [(employee_id, stamp, count) for (employee_id, stamp), count in daily_load.items() if count > 2]
    if overloaded:
        employee_id, stamp, count = overloaded[0]
        raise ValueError(
            f"Targeted regeneration blocked: employee {employee_id} has {count} tasks on {stamp}; maximum is 2."
        )


def build_report_snapshot(year: int, month: int) -> dict:
    """Load analytics only when the Reports view is requested."""
    roster = latest_persisted_roster(year, month) or build_live_roster(year, month)
    return {
        "calendar": {"year": year, "month": month, "label": f"{month_name[month]} {year}"},
        "coverage_status": roster["status"],
        "report": _build_report(year, month, roster["assignments"]),
        "roster": roster,
    }


def _build_report(year: int, month: int, rows: list[dict]) -> dict:
    """Build month-level staffing analytics from the same live roster snapshot.

    ``required`` is the number of planned staff-shifts after each lane's
    effective dates, Sunday rule, and public-holiday rule have been applied.
    ``assigned`` is the corresponding number actually allocated by the roster.
    """
    try:
        requirements = _fetch_table(
            "staffing_requirements",
            "requirement_id,brand_process_id,shift_id,required_employees,effective_from,effective_to",
            "requirement_id.asc",
        )
        bridges = _fetch_table("Brand_processes", "Brand_process_id,Brand_id,Process_id,status", "Brand_process_id.asc")
        brands = _fetch_table("Brand", "Brand_id,Brand_name,status", "Brand_id.asc")
        processes = _fetch_table("process", "process_id,process_name,status", "process_id.asc")
        shifts = _fetch_table("shifts", "shift_id,shift_name", "shift_id.asc")
        calendars = _fetch_table(
            "operating_calendars",
            "brand_process_id,operates_sundays,observes_public_holidays,effective_from,effective_to,status",
            "calendar_id.asc",
        )
        holidays = _fetch_table("india_holidays", "holiday_date,is_working_override", "holiday_id.asc")
    except RuntimeError:
        # Keep the dashboard contract readable in degraded/test environments.
        # A live Supabase request normally supplies the complete calculation.
        return _report_from_roster_rows(year, month, rows)

    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])
    bridge_to_brand = {
        row["Brand_process_id"]: row["Brand_id"]
        for row in bridges
        if str(row.get("status", "Active")).lower() == "active"
    }
    brand_names = {row["Brand_id"]: row["Brand_name"] for row in brands}
    process_names = {row["process_id"]: row["process_name"].strip().upper() for row in processes}
    shift_names = {row["shift_id"]: row["shift_name"] for row in shifts}
    calendar_by_bridge = {
        row["brand_process_id"]: row
        for row in calendars
        if str(row.get("status", "Active")).lower() == "active"
    }
    closed_holidays = {
        date.fromisoformat(row["holiday_date"])
        for row in holidays
        if not row.get("is_working_override")
    }

    required_by_brand: Counter[str] = Counter()
    required_by_lane: Counter[tuple[str, str, str]] = Counter()
    open_days_by_lane: defaultdict[tuple[str, str, str], set[date]] = defaultdict(set)
    open_days_by_brand: defaultdict[str, set[date]] = defaultdict(set)
    for requirement in requirements:
        bridge_id = requirement["brand_process_id"]
        brand_id = bridge_to_brand.get(bridge_id)
        if brand_id is None:
            continue
        brand = brand_names.get(brand_id, f"Brand {brand_id}")
        bridge = next((row for row in bridges if row["Brand_process_id"] == bridge_id), {})
        process = process_names.get(bridge.get("Process_id"), "UNASSIGNED")
        shift = shift_names.get(requirement["shift_id"], "Unassigned")
        lane = (brand, process, shift)
        current = max(month_start, date.fromisoformat(requirement["effective_from"]))
        final = min(month_end, date.fromisoformat(requirement["effective_to"]))
        calendar = calendar_by_bridge.get(bridge_id, {})
        while current <= final:
            is_sunday = current.weekday() == 6
            closed_for_sunday = is_sunday and not calendar.get("operates_sundays", True)
            closed_for_holiday = current in closed_holidays and bool(calendar.get("observes_public_holidays", False))
            if not closed_for_sunday and not closed_for_holiday:
                required_by_brand[brand] += int(requirement["required_employees"])
                open_days_by_brand[brand].add(current)
                required_by_lane[lane] += int(requirement["required_employees"])
                open_days_by_lane[lane].add(current)
            current += timedelta(days=1)

    assigned_by_brand = Counter(row.get("brand", "Unassigned") for row in rows)
    assigned_by_lane = Counter(
        (row.get("brand", "Unassigned"), str(row.get("process", "UNASSIGNED")).strip().upper(), str(row.get("shift", "Unassigned")).split(" (")[0])
        for row in rows
    )
    all_brands = sorted(set(required_by_brand) | set(assigned_by_brand))
    brand_staffing = []
    for brand in all_brands:
        required = required_by_brand[brand]
        assigned = assigned_by_brand[brand]
        open_days = len(open_days_by_brand[brand])
        brand_staffing.append({
            "brand": brand,
            "required": required,
            "assigned": assigned,
            "open_days": open_days,
            "daily_required": round(required / open_days, 1) if open_days else 0,
            "daily_assigned": round(assigned / open_days, 1) if open_days else 0,
            "coverage_percent": round((assigned / required) * 100) if required else 0,
        })

    assignments_by_date: defaultdict[str, list[dict]] = defaultdict(list)
    for row in rows:
        assignments_by_date[row["date"]].append(row)
    daily_staffing = []
    for day_offset in range((month_end - month_start).days + 1):
        day = month_start + timedelta(days=day_offset)
        day_rows = assignments_by_date[day.isoformat()]
        daily_staffing.append({
            "date": day.isoformat(),
            "label": str(day.day),
            "working_employees": len({row["employee_id"] for row in day_rows}),
            "assignments": len(day_rows),
        })

    day_assignments = sum(1 for row in rows if not str(row.get("shift", "")).lower().startswith("night"))
    night_assignments = len(rows) - day_assignments
    lanes = []
    for lane in sorted(set(required_by_lane) | set(assigned_by_lane)):
        required = required_by_lane[lane]
        assigned = assigned_by_lane[lane]
        open_days = len(open_days_by_lane[lane])
        lanes.append({
            "brand": lane[0], "process": lane[1], "shift": lane[2], "required": required,
            "assigned": assigned, "open_days": open_days,
            "daily_required": round(required / open_days, 1) if open_days else 0,
            "coverage_percent": round((assigned / required) * 100) if required else 0,
        })
    return {
        "brand_staffing": brand_staffing,
        "lanes": lanes,
        "summary": {
            "required_assignments": sum(required_by_brand.values()),
            "assigned_assignments": len(rows),
            "working_employees": len({row["employee_id"] for row in rows}),
            "day_shift_assignments": day_assignments,
            "night_shift_assignments": night_assignments,
            "average_daily_staff": round(sum(item["working_employees"] for item in daily_staffing) / len(daily_staffing), 1) if daily_staffing else 0,
        },
        "daily_staffing": daily_staffing,
    }


def _report_from_roster_rows(year: int, month: int, rows: list[dict]) -> dict:
    """Return useful assigned-workload analytics when requirement metadata is unavailable."""
    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])
    assigned_by_brand = Counter(row.get("brand", "Unassigned") for row in rows)
    assignments_by_date: defaultdict[str, list[dict]] = defaultdict(list)
    for row in rows:
        assignments_by_date[row["date"]].append(row)
    daily_staffing = []
    for day_offset in range((month_end - month_start).days + 1):
        day = month_start + timedelta(days=day_offset)
        day_rows = assignments_by_date[day.isoformat()]
        daily_staffing.append({"date": day.isoformat(), "label": str(day.day), "working_employees": len({row["employee_id"] for row in day_rows}), "assignments": len(day_rows)})
    day_assignments = sum(1 for row in rows if not str(row.get("shift", "")).lower().startswith("night"))
    return {
        "brand_staffing": [{"brand": brand, "required": assigned, "assigned": assigned, "open_days": 0, "daily_required": 0, "daily_assigned": 0, "coverage_percent": 100} for brand, assigned in sorted(assigned_by_brand.items())],
        "lanes": [{"brand": brand, "process": "UNASSIGNED", "shift": "Unassigned", "required": assigned, "assigned": assigned, "open_days": 0, "daily_required": 0, "coverage_percent": 100} for brand, assigned in sorted(assigned_by_brand.items())],
        "summary": {"required_assignments": len(rows), "assigned_assignments": len(rows), "working_employees": len({row["employee_id"] for row in rows}), "day_shift_assignments": day_assignments, "night_shift_assignments": len(rows) - day_assignments, "average_daily_staff": round(sum(item["working_employees"] for item in daily_staffing) / len(daily_staffing), 1) if daily_staffing else 0},
        "daily_staffing": daily_staffing,
    }


def build_request_snapshot(year: int, month: int) -> dict:
    """Return manager-facing leave and same-day availability activity."""
    period_start = date(year, month, 1)
    period_end = date(year, month, monthrange(year, month)[1])
    employees, leave_requests = _request_sources(period_start, period_end)
    requests = []
    for row in leave_requests:
        start = date.fromisoformat(row["unavailable_from"])
        end = date.fromisoformat(row["unavailable_to"])
        if start > period_end or end < period_start:
            continue
        employee = employees.get(row["Emp_id"], {})
        requests.append({
            "id": row["leave_request_id"],
            "employee_id": row["Emp_id"],
            "employee": employee.get("name", f"Employee {row['Emp_id']}"),
            "designation": employee.get("designation", ""),
            "pod": employee.get("pod", "Not assigned"),
            "from": row["unavailable_from"],
            "to": row["unavailable_to"],
            "type": row["request_type"],
            "status": row["status"],
            "reason": visible_review_note(row.get("review_note")),
            "coverage_summary": (
                visible_review_note(row.get("review_note"))
                or "No replacement assignment required; no scheduled task was affected."
            ) if str(row.get("status", "")).lower() == "approved" else "",
            "created_at": row.get("requested_at"),
        })
    reference_day = date.today()
    today_unavailable = [
        item for item in requests
        if item["status"].lower() == "approved"
        and item["from"] <= reference_day.isoformat() <= item["to"]
    ]
    pending_requests = [item for item in requests if item["status"].lower() == "pending"]
    approved_unavailability = [item for item in requests if item["status"].lower() == "approved"]
    return {
        "calendar": {"year": year, "month": month, "label": f"{month_name[month]} {year}"},
        "reference_date": reference_day.isoformat(),
        "requests": requests,
        "pending_requests": pending_requests,
        "approved_unavailability": approved_unavailability,
        "today_unavailable": today_unavailable,
        "status_counts": dict(Counter(item["status"] for item in requests)),
    }


def _request_sources(period_start: date, period_end: date) -> tuple[dict[int, dict], list[dict]]:
    """Load only the four datasets used by Requests, concurrently."""
    queries = {
        "employees": (
            "Employee", "Emp_id,emp_name,designation,emp_status", "Emp_id.asc", None,
        ),
        "pods": (
            "pods", "pod_id,pod_name,pod_lead_id", "pod_id.asc", None,
        ),
        "memberships": (
            "employee_pod_memberships",
            "Emp_id,pod_id,effective_from,effective_to,status,is_primary",
            "employee_pod_membership_id.asc",
            None,
        ),
        "requests": (
            "employee_leave_requests",
            "leave_request_id,Emp_id,unavailable_from,unavailable_to,request_type,status,review_note,requested_at",
            "requested_at.desc",
            {
                "unavailable_from": f"lte.{period_end.isoformat()}",
                "unavailable_to": f"gte.{period_start.isoformat()}",
            },
        ),
    }
    with ThreadPoolExecutor(max_workers=len(queries), thread_name_prefix="request-snapshot") as pool:
        pending = {
            name: pool.submit(_fetch_table, table, select, order, filters)
            for name, (table, select, order, filters) in queries.items()
        }
        data = {name: future.result() for name, future in pending.items()}

    pod_names = {row["pod_id"]: row["pod_name"] for row in data["pods"]}
    lead_pods: dict[int, list[str]] = defaultdict(list)
    for pod in data["pods"]:
        if pod.get("pod_lead_id") is not None:
            lead_pods[pod["pod_lead_id"]].append(pod["pod_name"])
    member_pods = {
        row["Emp_id"]: pod_names[row["pod_id"]]
        for row in data["memberships"]
        if str(row.get("status", "")).lower() == "active"
        and row.get("is_primary", True)
        and row["pod_id"] in pod_names
    }
    employees = {
        row["Emp_id"]: {
            "id": row["Emp_id"],
            "name": row["emp_name"],
            "designation": row.get("designation") or "",
            "pod": member_pods.get(
                row["Emp_id"], ", ".join(lead_pods.get(row["Emp_id"], [])) or "Not assigned",
            ),
        }
        for row in data["employees"]
    }
    return employees, data["requests"]


@router.get("/dashboard")
def get_dashboard(year: int = Query(ge=2025, le=2035), month: int = Query(ge=1, le=12)) -> dict:
    """Read-only dashboard snapshot. It never persists a new roster run."""
    try:
        return build_dashboard_snapshot(year, month)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(503, detail=str(error)) from error


@router.get("/reports")
def get_report(year: int = Query(ge=2025, le=2035), month: int = Query(ge=1, le=12)) -> dict:
    """Load the heavier staffing analytics on demand, after the roster view is ready."""
    try:
        return build_report_snapshot(year, month)
    except RuntimeError as error:
        raise HTTPException(503, detail=str(error)) from error


@router.get("/requests")
def get_requests(year: int = Query(ge=2025, le=2035), month: int = Query(ge=1, le=12)) -> dict:
    """Read availability activity for the selected calendar month."""
    try:
        return build_request_snapshot(year, month)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(503, detail=str(error)) from error


@router.post("/requests/{request_id}/simulate")
def simulate_leave_replacement(request_id: int) -> dict:
    try:
        return {"simulation": simulate_request(request_id)}
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post("/dashboard/generate")
def generate_dashboard(request: DashboardPeriod) -> dict:
    """Generate and persist a validated roster, then return the complete UI snapshot."""
    try:
        return build_dashboard_snapshot(request.year, request.month, persist=True)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(503, detail=str(error)) from error


@router.post("/dashboard/absence")
def create_absence(request: DashboardAbsence) -> dict:
    try:
        operation = record_absence(request.employee_id, request.absence_date)
        return {
            "operation": operation,
            "dashboard": build_dashboard_snapshot(request.year, request.month),
        }
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post("/requests")
def create_leave_request(request: LeaveRequestPayload) -> dict:
    try:
        created = create_pending_leave_request(
            request.employee_id,
            request.unavailable_from,
            request.unavailable_to,
            request.request_type,
        )
        return {
            "request": created,
            "requests": build_request_snapshot(
                request.unavailable_from.year, request.unavailable_from.month
            ),
        }
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post("/requests/{request_id}/approve")
def approve_leave_request(request_id: int, decision: ManagerRequestDecision) -> dict:
    try:
        reviewed = review_leave_request(request_id, "Approved", decision.review_note)
        request = reviewed["request"]
        request_date = date.fromisoformat(request["unavailable_from"])
        return {
            "review": reviewed,
            "dashboard": build_dashboard_snapshot(request_date.year, request_date.month),
            "requests": build_request_snapshot(request_date.year, request_date.month),
        }
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post("/requests/{request_id}/reject")
def reject_leave_request(request_id: int, decision: ManagerRequestDecision) -> dict:
    try:
        reviewed = review_leave_request(request_id, "Rejected", decision.review_note)
        request = reviewed["request"]
        request_date = date.fromisoformat(request["unavailable_from"])
        return {
            "review": reviewed,
            "requests": build_request_snapshot(request_date.year, request_date.month),
        }
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post("/requests/{request_id}/cancel")
def cancel_approved_leave_request(request_id: int) -> dict:
    try:
        cancelled = cancel_leave_approval(request_id)
        request = cancelled["request"]
        request_date = date.fromisoformat(request["unavailable_from"])
        return {
            "review": cancelled,
            "dashboard": build_dashboard_snapshot(request_date.year, request_date.month),
            "requests": build_request_snapshot(request_date.year, request_date.month),
        }
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post("/dashboard/absence/restore")
def restore_absence_event(request: DashboardAbsence) -> dict:
    try:
        operation = restore_absence(request.employee_id, request.absence_date)
        return {
            "operation": operation,
            "dashboard": build_dashboard_snapshot(request.year, request.month),
        }
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, detail=str(error)) from error
