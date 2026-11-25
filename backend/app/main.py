from __future__ import annotations

from datetime import date
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .manager_auth import protect_roster, router as auth_router
from .models import RosterInput
from .config import load_local_env
from .live_roster import build_live_roster, record_absence, request_time_off, restore_absence, roster_period
from .repository import load_roster_input
from .scheduler import RosterScheduler, validate_schedule
from .supabase_repository import catalog_overview, data_readiness, list_brands
from .dashboard_api import router as dashboard_router
from .employee_api import router as employee_router

load_local_env()
_default_origins = "http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:5174"
allowed_origins = [origin.strip().rstrip("/") for origin in os.getenv("CORS_ORIGINS", _default_origins).split(",") if origin.strip()]

app = FastAPI(title="DataStraw Smart Roster")
app.middleware("http")(protect_roster)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=os.getenv("CORS_ORIGIN_REGEX") or None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Roster-Request", "X-Roster-Session"],
)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(employee_router)
scheduler = RosterScheduler()
latest_result = None
latest_input: RosterInput | None = None


class GenerateRequest(BaseModel):
    year: int = Field(ge=2025, le=2035)
    month: int = Field(ge=1, le=12)


class AbsenceRequest(GenerateRequest):
    employee_id: str
    absence_date: date


class TimeOffRequest(BaseModel):
    employee_id: str
    unavailable_from: date
    unavailable_to: date
    availability_type: str = Field(pattern="^(Leave|Comp Off)$")


@app.get("/")
def service_status() -> dict:
    return {"service": "DataStraw Smart Roster API", "dashboard_endpoint": "/api/v1/dashboard"}


@app.get("/api/summary")
def summary() -> dict:
    load_local_env()
    if os.getenv("DATA_SOURCE", "sample").lower() == "supabase":
        catalog = catalog_overview()
        return {
            "employees": len(catalog["employees"]),
            "brands": len(catalog["brands"]),
            "processes": None,
            "pods": len(catalog["pods"]),
            "source": "supabase",
        }
    roster_input = load_roster_input()
    return {
        "employees": len(roster_input.employees),
        "brands": len({role.brand for role in roster_input.roles}),
        "processes": len({role.process for role in roster_input.roles}),
        "pods": len({employee.pod for employee in roster_input.employees}),
        "source": "sample",
    }


@app.get("/api/data-readiness")
def readiness() -> dict:
    return data_readiness()


@app.get("/api/catalog/brands")
def brands() -> dict:
    return {"brands": list_brands()}


@app.get("/api/catalog/employees")
def employees() -> dict:
    return {"employees": catalog_overview()["employees"]}


@app.get("/api/roster-period")
def live_roster_period() -> dict:
    return roster_period()


@app.post("/api/generate")
def generate(request: GenerateRequest) -> dict:
    global latest_result, latest_input
    load_local_env()
    if os.getenv("DATA_SOURCE", "sample").lower() == "supabase":
        return build_live_roster(request.year, request.month, persist=True)
    try:
        latest_input = load_roster_input()
    except RuntimeError as error:
        raise HTTPException(409, detail=str(error)) from error
    latest_result = scheduler.generate(latest_input, request.year, request.month)
    validation_issues = validate_schedule(latest_result, latest_input)
    if validation_issues:
        latest_result.status = "infeasible"
        latest_result.diagnostics.extend(validation_issues)
    return {"status": latest_result.status, "assignments": latest_result.rows(), "diagnostics": latest_result.diagnostics}


@app.post("/api/absence")
def simulate_absence(request: AbsenceRequest) -> dict:
    load_local_env()
    if os.getenv("DATA_SOURCE", "sample").lower() == "supabase":
        try:
            return record_absence(int(request.employee_id), request.absence_date)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, detail=str(error)) from error
    try:
        roster_input = load_roster_input()
    except RuntimeError as error:
        raise HTTPException(409, detail=str(error)) from error
    baseline = scheduler.generate(roster_input, request.year, request.month)
    if baseline.status != "feasible":
        raise HTTPException(409, detail=baseline.diagnostics)
    affected = [
        row for row in baseline.rows()
        if row["employee_id"] == request.employee_id and row["date"] == request.absence_date.isoformat()
    ]
    roster_input.unavailable.setdefault(request.absence_date, set()).add(request.employee_id)
    revised = scheduler.generate(roster_input, request.year, request.month)
    alternatives = [
        row for row in revised.rows()
        if row["date"] == request.absence_date.isoformat()
        and any(row["role_id"] == affected_row["role_id"] for affected_row in affected)
    ]
    return {
        "status": revised.status,
        "affected_assignments": affected,
        "alternative_assignments": alternatives,
        "coverage_gaps": revised.diagnostics,
        "explanation": "The revised roster is solved across the full month, so any replacement remains compatible with all other coverage and no-overlap constraints.",
    }


@app.post("/api/absence/restore")
def restore_absence_event(request: AbsenceRequest) -> dict:
    load_local_env()
    if os.getenv("DATA_SOURCE", "sample").lower() != "supabase":
        raise HTTPException(409, detail="Absence restoration is available only for the Supabase data source.")
    try:
        return restore_absence(int(request.employee_id), request.absence_date)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(409, detail=str(error)) from error


@app.post("/api/time-off")
def request_leave_or_comp_off(request: TimeOffRequest) -> dict:
    """Save only a leave/Comp Off request that a live re-roster can cover."""
    load_local_env()
    if os.getenv("DATA_SOURCE", "sample").lower() != "supabase":
        raise HTTPException(409, detail="Leave and Comp Off approval is available only for the Supabase data source.")
    try:
        return request_time_off(
            int(request.employee_id), request.unavailable_from, request.unavailable_to, request.availability_type
        )
    except (ValueError, RuntimeError) as error:
        raise HTTPException(409, detail=str(error)) from error
