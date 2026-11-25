"""Manager registration through Supabase Auth and existing company tables."""

from __future__ import annotations

import json
import base64
import binascii
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from time import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import load_local_env
from .roster_persistence import insert_rows, update_rows
from .supabase_inspector import _normalise_project_url
from .supabase_repository import _fetch_table

SESSION_HEADER = "X-Roster-Session"
ROLE_COOKIES = {
    "manager": "roster_manager_session",
    "employee": "roster_employee_session",
}
LEGACY_COOKIE = "roster_session"
SESSION_SECONDS = 8 * 60 * 60
router = APIRouter(prefix="/api/auth")


@dataclass(frozen=True)
class Session:
    employee_id: int
    name: str
    role: str
    expires_at: float


sessions: dict[str, Session] = {}
revoked_sessions: set[str] = set()
_ephemeral_session_secret = secrets.token_bytes(32)


class ManagerRegistration(BaseModel):
    employee_id: int
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)


class ManagerLogin(ManagerRegistration):
    pass


class EmployeeLogin(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)


def _email(email: str) -> str:
    return email.strip().lower()


def _auth_request(path: str, payload: dict, *, service_role: bool = False) -> dict:
    """Call Supabase Auth while keeping its secret credential on the server."""
    load_local_env()
    base_url = _normalise_project_url(os.getenv("SUPABASE_URL", ""))
    key = os.getenv("SUPABASE_SECRET_KEY", "") if service_role else os.getenv("SUPABASE_ANON_KEY", "")
    key = key or os.getenv("SUPABASE_SECRET_KEY", "")
    if not base_url or not key:
        raise HTTPException(503, "Supabase authentication is not configured.")
    request = Request(
        f"{base_url}/auth/v1/{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        detail = json.loads(error.read().decode(errors="replace")).get("msg", "Supabase authentication request failed.")
        raise HTTPException(400 if error.code < 500 else 503, detail) from error
    except URLError as error:
        raise HTTPException(503, "Supabase authentication is unavailable.") from error


def _company_manager(employee_id: int, email: str) -> dict:
    try:
        employees = _fetch_table("Employee", "Emp_id,emp_name,email,designation,emp_status", "Emp_id.asc")
    except RuntimeError as error:
        raise HTTPException(503, "Company employee records are unavailable.") from error
    employee = next((row for row in employees if row["Emp_id"] == employee_id), None)
    if employee is None or str(employee.get("emp_status", "")).lower() != "active":
        raise HTTPException(401, "Employee ID or email is not an active company record.")
    if _email(str(employee.get("email", ""))) != _email(email):
        raise HTTPException(401, "Employee ID or email is not an active company record.")
    if "manager" not in str(employee.get("designation", "")).lower():
        raise HTTPException(403, "Only active employees with a Manager designation can register as managers.")
    return employee


def _company_employee(name: str, email: str) -> dict:
    try:
        employees = _fetch_table("Employee", "Emp_id,emp_name,email,designation,emp_status", "Emp_id.asc")
    except RuntimeError as error:
        raise HTTPException(503, "Company employee records are unavailable.") from error
    normalised_name = " ".join(name.split()).casefold()
    employee = next(
        (
            row for row in employees
            if " ".join(str(row.get("emp_name", "")).split()).casefold() == normalised_name
            and _email(str(row.get("email", ""))) == _email(email)
        ),
        None,
    )
    if employee is None or str(employee.get("emp_status", "")).lower() != "active":
        raise HTTPException(401, "Name or email is not an active company employee record.")
    return employee


def _profile_for_auth_user(auth_user_id: str) -> dict | None:
    try:
        profiles = _fetch_table(
            "ui_user_profiles",
            "auth_user_id,Emp_id,email,role,is_active,created_at,updated_at",
            "created_at.asc",
        )
    except RuntimeError as error:
        raise HTTPException(503, "Manager profile records are unavailable.") from error
    return next((profile for profile in profiles if profile["auth_user_id"] == auth_user_id), None)


def _profile_for_employee(employee_id: int) -> dict | None:
    try:
        profiles = _fetch_table(
            "ui_user_profiles",
            "auth_user_id,Emp_id,email,role,is_active,created_at,updated_at",
            "created_at.asc",
        )
    except RuntimeError as error:
        raise HTTPException(503, "Manager profile records are unavailable.") from error
    return next((profile for profile in profiles if profile["Emp_id"] == employee_id), None)


def _profile(employee: dict) -> dict:
    return {"employee_id": employee["Emp_id"], "name": employee["emp_name"], "role": "manager"}


def _session_secret() -> bytes:
    """Use one shared secret on Vercel, with a process-only fallback for local development."""
    load_local_env()
    configured = os.getenv("ROSTER_SESSION_SECRET", "") or os.getenv("SUPABASE_SECRET_KEY", "")
    return configured.encode() if configured else _ephemeral_session_secret


def _encode_session(employee: dict, role: str, expires_at: float) -> str:
    payload = json.dumps(
        {
            "employee_id": employee["Emp_id"],
            "name": employee["emp_name"],
            "role": role,
            "expires_at": expires_at,
            "nonce": secrets.token_urlsafe(12),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(_session_secret(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _decode_session(token: str) -> Session | None:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected = hmac.new(_session_secret(), encoded.encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64decode(supplied_signature + "=" * (-len(supplied_signature) % 4))
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        session = Session(
            employee_id=int(payload["employee_id"]),
            name=str(payload["name"]),
            role=str(payload["role"]),
            expires_at=float(payload["expires_at"]),
        )
        return session if session.expires_at > time() else None
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error):
        return None


def _create_session(employee: dict, role: str, request: FastAPIRequest, response: Response) -> dict:
    expires_at = time() + SESSION_SECONDS
    token = _encode_session(employee, role, expires_at)
    sessions[token] = Session(employee["Emp_id"], employee["emp_name"], role, expires_at)
    load_local_env()
    response.set_cookie(
        ROLE_COOKIES[role],
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https" or os.getenv("ROSTER_SECURE_COOKIES") == "1",
    )
    response.delete_cookie(LEGACY_COOKIE, httponly=True, samesite="lax")
    return {
        "employee_id": employee["Emp_id"],
        "name": employee["emp_name"],
        "role": role,
        "session_token": token,
    }


def _session_for_token(token: str) -> Session | None:
    if not token or token in revoked_sessions:
        return None
    session = sessions.get(token) or _decode_session(token)
    if session is None or session.expires_at <= time():
        sessions.pop(token, None)
        return None
    return session


def current_session(request: FastAPIRequest, required_role: str | None = None) -> Session | None:
    """Resolve the tab-specific token first, with role cookies as a fallback."""
    header_token = request.headers.get(SESSION_HEADER, "")
    if header_token:
        session = _session_for_token(header_token)
        if session is None or (required_role and session.role != required_role):
            return None
        return session

    roles = (required_role,) if required_role else ("manager", "employee")
    for role in roles:
        session = _session_for_token(request.cookies.get(ROLE_COOKIES[role], ""))
        if session is not None and session.role == role:
            return session
    return None


async def protect_roster(request: FastAPIRequest, call_next):
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/auth/"):
        employee_api = request.url.path.startswith("/api/v1/employee/")
        required_role = "employee" if employee_api else "manager"
        session = current_session(request, required_role)
        if session is None:
            return JSONResponse({"detail": f"Sign in as an {required_role} to continue."}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    return await call_next(request)


@router.post("/manager/register")
def register_manager(registration: ManagerRegistration) -> dict:
    """Create the Supabase Auth account only for a verified company manager."""
    employee = _company_manager(registration.employee_id, registration.email)
    if _profile_for_employee(employee["Emp_id"]) is not None:
        raise HTTPException(409, "This manager is already registered. Please sign in.")
    account = _auth_request(
        "signup",
        {
            "email": _email(registration.email),
            "password": registration.password,
            "data": {"employee_id": employee["Emp_id"], "role": "Manager"},
        },
    )
    # GoTrue can return the sign-up user directly, while token endpoints wrap it
    # in a `user` property. Accept both documented response shapes.
    auth_user = account.get("user") or account
    auth_user_id = auth_user.get("id")
    if not auth_user_id:
        raise HTTPException(503, "Supabase did not return a manager account.")
    # The existing Supabase trigger normally creates this profile. The fallback
    # keeps registration usable if that trigger is temporarily unavailable.
    manager_profile = _profile_for_auth_user(auth_user_id)
    if manager_profile is None:
        try:
            insert_rows(
                "ui_user_profiles",
                [{
                    "auth_user_id": auth_user_id,
                    "Emp_id": employee["Emp_id"],
                    "email": _email(registration.email),
                    "role": "Manager",
                    "is_active": True,
                }],
            )
        except RuntimeError as error:
            raise HTTPException(503, "Manager profile could not be created. Contact an administrator.") from error
    elif manager_profile["Emp_id"] != employee["Emp_id"]:
        raise HTTPException(409, "The Supabase account is linked to a different employee.")
    elif str(manager_profile.get("role", "")).lower() != "manager" or not manager_profile.get("is_active"):
        try:
            update_rows(
                "ui_user_profiles",
                f"auth_user_id=eq.{auth_user_id}",
                {"role": "Manager", "is_active": True, "email": _email(registration.email)},
            )
        except RuntimeError as error:
            raise HTTPException(503, "Manager access could not be assigned to the profile.") from error
    return {"message": "Verification email sent. Open the Supabase confirmation link before signing in."}


@router.post("/manager/login")
def manager_login(credentials: ManagerLogin, request: FastAPIRequest, response: Response) -> dict:
    employee = _company_manager(credentials.employee_id, credentials.email)
    account = _auth_request("token?grant_type=password", {"email": _email(credentials.email), "password": credentials.password})
    auth_user = account.get("user") or {}
    auth_user_id = auth_user.get("id")
    if not auth_user_id or not auth_user.get("email_confirmed_at"):
        raise HTTPException(401, "Verify the email from your registration message before signing in.")
    profile = _profile_for_auth_user(auth_user_id)
    if profile is None or profile["Emp_id"] != employee["Emp_id"] or not profile.get("is_active"):
        raise HTTPException(403, "This account is not an active manager profile.")
    if str(profile.get("role", "")).lower() != "manager":
        raise HTTPException(403, "This account does not have manager access.")
    return _create_session(employee, "manager", request, response)


@router.post("/employee/login")
def employee_login(credentials: EmployeeLogin, request: FastAPIRequest, response: Response) -> dict:
    employee = _company_employee(credentials.name, credentials.email)
    return _create_session(employee, "employee", request, response)


@router.get("/session")
def session(request: FastAPIRequest) -> dict:
    active_session = current_session(request)
    if active_session is None:
        raise HTTPException(401, "Sign in as a verified manager.")
    return {"employee_id": active_session.employee_id, "name": active_session.name, "role": active_session.role}


@router.post("/logout")
def logout(request: FastAPIRequest, response: Response) -> dict:
    token = request.headers.get(SESSION_HEADER, "")
    active_session = _session_for_token(token) if token else None
    if token:
        sessions.pop(token, None)
        revoked_sessions.add(token)
    if active_session is None:
        for role, cookie in ROLE_COOKIES.items():
            cookie_token = request.cookies.get(cookie, "")
            candidate = sessions.get(cookie_token)
            if candidate is not None and candidate.role == role:
                sessions.pop(cookie_token, None)
                active_session = candidate
                break
    if active_session is not None:
        response.delete_cookie(ROLE_COOKIES[active_session.role], httponly=True, samesite="lax")
    response.delete_cookie(LEGACY_COOKIE, httponly=True, samesite="lax")
    return {"signed_out": True}
