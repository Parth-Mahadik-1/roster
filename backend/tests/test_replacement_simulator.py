from app import replacement_simulator


def test_simulator_prefers_present_eligible_employee_and_preserves_other_tasks(monkeypatch):
    roster = {
        "roster_run_id": 9,
        "assignments": [
            {"date": "2026-12-03", "employee_id": "7", "employee": "Absent", "brand_process_id": 2, "shift_id": 1, "brand": "Brand A", "process": "CS", "shift": "Morning (08:00-18:00)"},
            {"date": "2026-12-03", "employee_id": "8", "employee": "Present", "brand_process_id": 3, "shift_id": 1, "brand": "Brand B", "process": "NDR", "shift": "Morning (08:00-18:00)"},
            {"date": "2026-12-04", "employee_id": "9", "employee": "Unchanged", "brand_process_id": 4, "shift_id": 1, "brand": "Brand C", "process": "ACC", "shift": "Morning (08:00-18:00)"},
        ],
    }
    tables = {
        "employee_leave_requests": [{"leave_request_id": 4, "Emp_id": 7, "unavailable_from": "2026-12-03", "unavailable_to": "2026-12-03", "request_type": "Unavailable", "status": "Pending"}],
        "Employee": [
            {"Emp_id": 7, "emp_name": "Absent", "designation": "Operations Executive", "emp_status": "Active"},
            {"Emp_id": 8, "emp_name": "Present", "designation": "Operations Executive", "emp_status": "Active"},
            {"Emp_id": 10, "emp_name": "Free", "designation": "Operations Executive", "emp_status": "Active"},
        ],
        "employee_assignments": [
            {"assignment_id": 1, "Emp_id": 8, "brand_process_id": 2, "shift_id": 1, "assigned_from": "2026-12-01", "assigned_to": "2026-12-31", "status": "Active", "assignment_type": "Eligibility"},
            {"assignment_id": 2, "Emp_id": 10, "brand_process_id": 2, "shift_id": 1, "assigned_from": "2026-12-01", "assigned_to": "2026-12-31", "status": "Active", "assignment_type": "Eligibility"},
        ],
        "employee_weekly_offs": [],
        "employee_availability": [],
        "pods": [],
    }
    monkeypatch.setattr(replacement_simulator, "latest_persisted_roster", lambda *_args: roster)
    monkeypatch.setattr(replacement_simulator, "_fetch_table", lambda table, *_args: tables[table])

    simulation = replacement_simulator.simulate_request(4)

    assert simulation["feasible"] is True
    assert simulation["affected_task_count"] == 1
    assert simulation["unaffected_task_count"] == 2
    assert simulation["replacements"][0]["recommended"]["employee_id"] == 8
    assert simulation["replacements"][0]["recommended"]["already_working"] is True
    assert simulation["replacements"][0]["recommended"]["current_task_count"] == 1


def test_full_regeneration_retires_temporary_coverage(monkeypatch):
    monkeypatch.setattr(replacement_simulator, "_fetch_table", lambda *_args: [
        {
            "assignment_id": 51,
            "assigned_from": "2026-12-03",
            "assigned_to": "2026-12-03",
            "status": "Active",
            "assignment_type": "Coverage",
        },
    ])
    updates = []
    monkeypatch.setattr(
        replacement_simulator,
        "update_rows",
        lambda table, query, payload: updates.append((table, query, payload)) or [{}],
    )

    retired = replacement_simulator.retire_month_coverage(2026, 12)

    assert retired == [51]
    assert updates == [(
        "employee_assignments", "assignment_id=in.(51)", {"status": "Restored"},
    )]
