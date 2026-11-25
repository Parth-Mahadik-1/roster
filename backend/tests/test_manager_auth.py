from fastapi.testclient import TestClient

from app import manager_auth
from app.main import app


def test_manager_registration_uses_employee_auth_and_profile_tables(monkeypatch):
    employees = [
        {"Emp_id": 111, "emp_name": "Loki", "email": "loki@example.com", "designation": "Manager", "emp_status": "Active"},
        {"Emp_id": 112, "emp_name": "Employee", "email": "employee@example.com", "designation": "Operations Executive", "emp_status": "Active"},
        {"Emp_id": 113, "emp_name": "Second Employee", "email": "second@example.com", "designation": "Operations Executive", "emp_status": "Active"},
    ]
    profiles = []

    def fetch_table(table, *_args):
        if table == "Employee":
            return employees
        if table == "ui_user_profiles":
            return profiles
        raise AssertionError(f"Unexpected table: {table}")

    def insert_rows(table, rows):
        assert table == "ui_user_profiles"
        profiles.extend(rows)
        return rows

    def auth_request(path, _payload, **_kwargs):
        if path == "signup":
            return {"id": "auth-loki", "email": "loki@example.com"}
        if path == "token?grant_type=password":
            return {"user": {"id": "auth-loki", "email_confirmed_at": "2026-11-25T00:00:00Z"}}
        raise AssertionError(f"Unexpected Auth request: {path}")

    monkeypatch.setattr(manager_auth, "_fetch_table", fetch_table)
    monkeypatch.setattr(manager_auth, "insert_rows", insert_rows)
    monkeypatch.setattr(manager_auth, "_auth_request", auth_request)
    monkeypatch.setattr(manager_auth, "sessions", {})

    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/summary").status_code == 401
        invalid = client.post("/api/auth/manager/register", json={
            "employee_id": 112, "email": "employee@example.com", "password": "test-only-password",
        })
        assert invalid.status_code == 403

        registration = client.post("/api/auth/manager/register", json={
            "employee_id": 111, "email": "LOKI@example.com", "password": "test-only-password",
        })
        assert registration.status_code == 200
        assert profiles == [{
            "auth_user_id": "auth-loki", "Emp_id": 111, "email": "loki@example.com", "role": "Manager", "is_active": True,
        }]

        login = client.post("/api/auth/manager/login", json={
            "employee_id": 111, "email": "loki@example.com", "password": "test-only-password",
        })
        assert login.status_code == 200
        manager_login = login.json()
        assert {key: manager_login[key] for key in ("employee_id", "name", "role")} == {
            "employee_id": 111, "name": "Loki", "role": "manager",
        }
        assert manager_login["session_token"]
        assert "HttpOnly" in login.headers["set-cookie"]
        # Vercel may route the next request to another function instance.
        # A signed token must remain valid without process-local session memory.
        manager_auth.sessions.clear()
        assert client.get(
            "/api/auth/session", headers={manager_auth.SESSION_HEADER: manager_login["session_token"]},
        ).json() == {"employee_id": 111, "name": "Loki", "role": "manager"}

        assert client.post(
            "/api/auth/logout", headers={manager_auth.SESSION_HEADER: manager_login["session_token"]},
        ).status_code == 200
        assert client.get(
            "/api/auth/session", headers={manager_auth.SESSION_HEADER: manager_login["session_token"]},
        ).status_code == 401

        employee_login = client.post("/api/auth/employee/login", json={
            "name": "  Employee  ", "email": "EMPLOYEE@example.com",
        })
        assert employee_login.status_code == 200
        employee_profile = employee_login.json()
        assert {key: employee_profile[key] for key in ("employee_id", "name", "role")} == {
            "employee_id": 112, "name": "Employee", "role": "employee",
        }
        assert employee_profile["session_token"]

        # A second manager sign-in must not replace the employee tab's session.
        second_manager_login = client.post("/api/auth/manager/login", json={
            "employee_id": 111, "email": "loki@example.com", "password": "test-only-password",
        }).json()
        assert client.get(
            "/api/auth/session", headers={manager_auth.SESSION_HEADER: employee_profile["session_token"]},
        ).json()["role"] == "employee"
        assert client.get(
            "/api/auth/session", headers={manager_auth.SESSION_HEADER: second_manager_login["session_token"]},
        ).json()["role"] == "manager"

        second_employee = client.post("/api/auth/employee/login", json={
            "name": "Second Employee", "email": "second@example.com",
        }).json()
        assert client.get(
            "/api/auth/session", headers={manager_auth.SESSION_HEADER: employee_profile["session_token"]},
        ).json()["employee_id"] == 112
        assert client.get(
            "/api/auth/session", headers={manager_auth.SESSION_HEADER: second_employee["session_token"]},
        ).json()["employee_id"] == 113
