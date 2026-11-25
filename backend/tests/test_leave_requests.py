from app import leave_requests
from app.leave_requests import _coverage_review_note, cancel_leave_approval, visible_review_note


def test_coverage_review_note_names_assigned_employees():
    plan = {
        "replacements": [
            {"recommended": {"employee_id": 12, "employee": "Priya Patel"}},
            {"recommended": {"employee_id": 12, "employee": "Priya Patel"}},
            {"recommended": {"employee_id": 18, "employee": "Rohan Mehta"}},
        ],
    }

    note = _coverage_review_note(plan)

    assert "Priya Patel (#12)" in note
    assert "Rohan Mehta (#18)" in note
    assert "3 task(s)" in note


def test_coverage_review_note_explains_when_no_replacement_is_needed():
    assert _coverage_review_note({"replacements": []}) == (
        "No replacement assignment required; no scheduled task was affected."
    )


def test_cancel_approval_restores_linked_availability_and_coverage(monkeypatch):
    monkeypatch.setattr(leave_requests, "_fetch_table", lambda *_args: [{
        "leave_request_id": 9,
        "Emp_id": 16,
        "unavailable_from": "2026-12-01",
        "unavailable_to": "2026-12-01",
        "request_type": "Leave",
        "status": "Approved",
        "review_note": "Coverage assigned to Priya (#12). [coverage_ids:41,42]",
        "availability_id": 7,
    }])
    updates = []

    def update(table, query, payload):
        updates.append((table, query, payload))
        if table == "employee_leave_requests":
            return [{
                "leave_request_id": 9, "status": "Cancelled",
                "unavailable_from": "2026-12-01", "unavailable_to": "2026-12-01",
            }]
        return [{}]

    monkeypatch.setattr(leave_requests, "update_rows", update)

    result = cancel_leave_approval(9)

    assert result["restored_coverage_assignment_ids"] == [41, 42]
    assert ("employee_availability", "availability_id=eq.7", {"status": "Restored"}) in updates
    assert any(table == "employee_assignments" and "41,42" in query for table, query, _ in updates)
    assert updates[-1][2]["status"] == "Cancelled"
    assert visible_review_note("Assigned. [coverage_ids:41,42]") == "Assigned."
