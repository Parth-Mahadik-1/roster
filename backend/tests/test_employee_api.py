import json

from app import employee_api


def test_employee_updates_only_show_targeted_coverage_assignments(monkeypatch):
    employee = {"Emp_id": 7, "emp_name": "Employee Seven", "email": "seven@example.com", "designation": "Operations Executive", "emp_status": "Active"}
    roster = {
        "roster_run_id": 4,
        "created_at": "2026-11-26T08:00:00Z",
        "assignments": [
            {"role_id": "own", "date": "2026-12-01", "employee_id": "7", "brand": "Own Brand", "process": "CS", "shift": "Morning (08:00-18:00)", "start_time": "08:00", "end_time": "18:00", "hours": 10, "status": "Scheduled", "assignment_source": "Generated"},
            {"role_id": "coverage", "date": "2026-12-01", "employee_id": "7", "brand": "Covered Brand", "process": "NDR", "shift": "Morning (08:00-18:00)", "start_time": "08:00", "end_time": "18:00", "hours": 10, "status": "Coverage", "assignment_source": "Coverage"},
            {"role_id": "other", "date": "2026-12-01", "employee_id": "8", "brand": "Other Brand", "process": "CS", "shift": "Morning (08:00-18:00)", "start_time": "08:00", "end_time": "18:00", "hours": 10, "status": "Scheduled", "assignment_source": "Generated"},
        ],
    }
    monkeypatch.setattr(employee_api, "latest_persisted_roster", lambda *_args, **_kwargs: roster)
    monkeypatch.setattr(employee_api, "_fetch_table", lambda table, *_args: [employee] if table == "Employee" else [])

    dashboard = employee_api.build_employee_dashboard(7, 2026, 12)

    assert [row["id"] for row in dashboard["schedule"]] == ["own", "coverage"]
    assert "change" not in dashboard["schedule"][0]
    assert dashboard["schedule"][1]["change"]["type"] == "Coverage"
    assert len(dashboard["updates"]) == 1
    assert dashboard["summary"] == {"scheduled_days": 1, "scheduled_hours": 20, "pending_requests": 0}
    assert json.loads(json.dumps(dashboard))["roster"]["run_id"] == 4
