from app import roster_persistence


def test_save_roster_preserves_coverage_source(monkeypatch):
    inserted = []

    def insert(table, rows):
        inserted.append((table, rows))
        if table == "roster_runs":
            return [{"roster_run_id": 13}]
        return rows

    monkeypatch.setattr(roster_persistence, "insert_rows", insert)

    roster_persistence.save_roster(2026, 12, [{
        "date": "2026-12-03",
        "employee_id": "8",
        "role_id": "2:1:coverage:51",
        "status": "Coverage",
        "assignment_source": "Coverage",
    }], [])

    saved = inserted[1][1][0]
    assert saved["assignment_status"] == "Coverage"
    assert saved["assignment_source"] == "Coverage"
