import pytest

from app import dashboard_api


def test_dashboard_snapshot_is_complete_frontend_contract(monkeypatch):
    monkeypatch.setattr(dashboard_api, "dashboard_catalog", lambda: {
        "employees": [{"id": 1, "name": "Employee", "pod": "Alpha"}],
        "brands": [{"id": 1, "name": "Brand"}],
        "pods": [{"pod_id": 1, "pod_name": "Alpha"}],
        "processes": [{"id": 1, "name": "CS"}],
        "shifts": [{"id": 1, "name": "Morning", "start_time": "08:00", "end_time": "18:00"}],
    })
    monkeypatch.setattr(dashboard_api, "build_live_roster", lambda year, month, persist=False: {
        "status": "feasible",
        "assignments": [{"date": "2026-12-01", "employee_id": "1", "status": "Scheduled"}],
        "diagnostics": [],
        "manager_messages": [],
        "weekly_offs": [],
        "roster_run_id": 7 if persist else None,
    })
    monkeypatch.setattr(dashboard_api, "latest_persisted_roster", lambda year, month: {
        "status": "feasible",
        "assignments": [{"date": "2026-12-01", "employee_id": "1", "status": "Scheduled"}],
        "diagnostics": [],
        "manager_messages": [],
        "weekly_offs": [],
        "roster_run_id": 7,
    })

    snapshot = dashboard_api.build_dashboard_snapshot(2026, 12)

    assert snapshot["api_version"] == "v1"
    assert snapshot["calendar"]["label"] == "December 2026"
    assert snapshot["summary"]["scheduled_assignments"] == 1
    assert snapshot["filters"]["employees"] == snapshot["catalog"]["employees"]
    assert snapshot["roster"]["status"] == "feasible"


def test_request_snapshot_returns_manager_activity(monkeypatch):
    tables = {
        "Employee": [{"Emp_id": 7, "emp_name": "Employee", "designation": "Operations Executive", "emp_status": "Active"}],
        "pods": [{"pod_id": 1, "pod_name": "Alpha", "pod_lead_id": None}],
        "employee_pod_memberships": [{"Emp_id": 7, "pod_id": 1, "status": "Active", "is_primary": True}],
        "employee_leave_requests": [{
            "leave_request_id": 5,
            "Emp_id": 7,
            "unavailable_from": "2026-12-03",
            "unavailable_to": "2026-12-03",
            "request_type": "Unavailable",
            "status": "Approved",
            "review_note": "Coverage validated",
            "requested_at": "2026-12-01T09:00:00Z",
        }],
    }
    monkeypatch.setattr(dashboard_api, "_fetch_table", lambda table, *_args: tables[table])

    snapshot = dashboard_api.build_request_snapshot(2026, 12)

    assert snapshot["calendar"]["label"] == "December 2026"
    assert snapshot["requests"][0]["employee"] == "Employee"
    assert snapshot["requests"][0]["type"] == "Unavailable"


def test_generate_snapshot_retires_targeted_coverage_after_new_run(monkeypatch):
    monkeypatch.setattr(dashboard_api, "dashboard_catalog", lambda: {
        "employees": [], "brands": [], "pods": [], "processes": [], "shifts": [],
    })
    monkeypatch.setattr(dashboard_api, "build_live_roster", lambda *_args, **_kwargs: {
        "status": "feasible", "assignments": [], "diagnostics": [],
        "manager_messages": [], "weekly_offs": [], "roster_run_id": 12,
    })
    monkeypatch.setattr(dashboard_api, "_approved_requests_exist", lambda *_args: False)
    retired = []
    monkeypatch.setattr(
        dashboard_api, "retire_month_coverage",
        lambda year, month: retired.append((year, month)) or [51],
    )

    dashboard_api.build_dashboard_snapshot(2026, 12, persist=True)

    assert retired == [(2026, 12)]


def test_approved_leave_regeneration_preserves_unaffected_assignments(monkeypatch):
    adjusted_rows = [
        {
            "date": "2026-12-03", "employee_id": "8", "role_id": "3:1:generated:1",
            "brand_process_id": 3, "shift_id": 1, "status": "Scheduled",
            "assignment_source": "Optimized",
        },
        {
            "date": "2026-12-03", "employee_id": "9", "role_id": "4:1:generated:2",
            "brand_process_id": 4, "shift_id": 1, "status": "Scheduled",
            "assignment_source": "Optimized",
        },
        {
            "date": "2026-12-03", "employee_id": "8", "role_id": "2:1:coverage:51",
            "brand_process_id": 2, "shift_id": 1, "status": "Coverage",
            "assignment_source": "Coverage",
        },
    ]
    roster = {
        "status": "feasible", "assignments": adjusted_rows, "diagnostics": [],
        "manager_messages": [], "weekly_offs": [], "roster_run_id": 12,
    }
    monkeypatch.setattr(dashboard_api, "_approved_requests_exist", lambda *_args: True)
    monkeypatch.setattr(dashboard_api, "latest_persisted_roster", lambda *_args, **_kwargs: roster)
    monkeypatch.setattr(dashboard_api, "_validate_targeted_roster", lambda *_args: None)
    published = dashboard_api._regenerate_roster(2026, 12)

    assert published["assignments"] == adjusted_rows
    assert published["roster_run_id"] == 12
    assert published["diagnostics"] == [
        "Targeted leave coverage validated; unaffected assignments were preserved.",
    ]


def test_targeted_validator_allows_only_one_for_one_coverage(monkeypatch):
    baseline = [
        {"date": "2026-12-03", "employee_id": "7", "brand_process_id": 2, "shift_id": 1, "assignment_source": "Optimized"},
        {"date": "2026-12-03", "employee_id": "8", "brand_process_id": 3, "shift_id": 1, "assignment_source": "Optimized"},
    ]
    adjusted = [
        baseline[1],
        {"date": "2026-12-03", "employee_id": "9", "brand_process_id": 2, "shift_id": 1, "assignment_source": "Coverage"},
    ]
    monkeypatch.setattr(dashboard_api, "_fetch_table", lambda *_args: [{
        "Emp_id": 7, "unavailable_from": "2026-12-03", "unavailable_to": "2026-12-03", "status": "Approved",
    }])

    dashboard_api._validate_targeted_roster(2026, 12, baseline, adjusted)

    changed_unrelated = [
        adjusted[1],
        {"date": "2026-12-03", "employee_id": "10", "brand_process_id": 3, "shift_id": 1, "assignment_source": "Optimized"},
    ]
    with pytest.raises(ValueError, match="unrelated assignment"):
        dashboard_api._validate_targeted_roster(2026, 12, baseline, changed_unrelated)
