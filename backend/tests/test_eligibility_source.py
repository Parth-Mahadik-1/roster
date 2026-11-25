import pytest

from app.live_roster import _ensure_eligibility_rows


def test_designation_is_not_used_when_explicit_eligibility_exists():
    tables = {
        "assignments": [
            {
                "Emp_id": 1,
                "brand_process_id": 9,
                "shift_id": 2,
                "status": "Active",
                "assignment_type": "Eligibility",
            }
        ],
    }

    # A generic title must not alter explicit roster skills.
    _ensure_eligibility_rows(tables, [])

    assert tables["assignments"][0]["brand_process_id"] == 9


def test_missing_eligibility_is_not_inferred_from_designation():
    with pytest.raises(RuntimeError, match="No active employee eligibility"):
        _ensure_eligibility_rows({"assignments": []}, [])
