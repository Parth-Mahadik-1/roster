from app.roster_adjustments import _complete_runs


def test_partial_week_run_is_not_used_as_monthly_roster():
    runs = [{"roster_run_id": 5}, {"roster_run_id": 4}]
    expected = {f"2026-12-{day:02d}" for day in range(1, 32)}
    assignments = [
        {"roster_run_id": 5, "roster_date": f"2026-12-{day:02d}"}
        for day in range(1, 8)
    ] + [
        {"roster_run_id": 4, "roster_date": f"2026-12-{day:02d}"}
        for day in range(1, 32)
    ]

    assert _complete_runs(runs, assignments, expected) == [{"roster_run_id": 4}]


def test_no_saved_run_is_returned_when_all_are_partial():
    runs = [{"roster_run_id": 5}]
    expected = {f"2026-12-{day:02d}" for day in range(1, 32)}
    assignments = [
        {"roster_run_id": 5, "roster_date": f"2026-12-{day:02d}"}
        for day in range(1, 8)
    ]

    assert _complete_runs(runs, assignments, expected) == []
