from datetime import date

from app.sample_data import demo_data
from app.scheduler import RosterScheduler, validate_schedule


def test_monthly_roster_has_no_employee_overlap():
    roster_input = demo_data()
    result = RosterScheduler().generate(roster_input, 2026, 11)
    assert result.status == "feasible"
    assert validate_schedule(result, roster_input) == []


def test_absence_can_be_replanned_when_capacity_exists():
    roster_input = demo_data()
    roster_input.unavailable[date(2026, 11, 2)] = {"EMP001"}
    result = RosterScheduler().generate(roster_input, 2026, 11)
    assert result.status == "feasible"
    assert all(assignment.employee_id != "EMP001" or assignment.date != date(2026, 11, 2) for assignment in result.assignments)
    assert validate_schedule(result, roster_input) == []


def test_holiday_closes_configured_processes_only():
    roster_input = demo_data()
    roster_input.holidays.add(date(2026, 11, 2))
    result = RosterScheduler().generate(roster_input, 2026, 11)
    assert result.status == "feasible"
    assigned_roles = {assignment.role_id for assignment in result.assignments if assignment.date == date(2026, 11, 2)}
    assert assigned_roles == {"nova-support"}
