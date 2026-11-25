from datetime import date

from app.models import Employee, Role, RosterInput
from app import live_roster
from app.scheduler import RosterScheduler, validate_schedule


def test_requirement_runs_only_inside_its_effective_dates():
    role = Role("role", "Brand", "CS", 1, frozenset(range(7)), effective_from=date(2027, 1, 2), effective_to=date(2027, 1, 2))
    employee = Employee("1", "Employee", "Pod", 6, frozenset({"role"}))
    result = RosterScheduler().generate(RosterInput([employee], [role]), 2027, 1)
    assert result.status == "feasible"
    assert [assignment.date for assignment in result.assignments] == [date(2027, 1, 2)]


def test_night_assignment_prefers_non_leader_when_both_are_eligible():
    role = Role("night", "Brand", "CS", 1, frozenset(range(7)), shift="Night (18:00-04:00)")
    leader = Employee("1", "Leader", "Pod", 7, frozenset({"night"}), pod_role="Pod Lead")
    worker = Employee("2", "Worker", "Pod", 7, frozenset({"night"}))
    result = RosterScheduler().generate(RosterInput([leader, worker], [role]), 2027, 1)
    assert result.status == "feasible"
    assert all(assignment.employee_id == "2" for assignment in result.assignments)
    assert validate_schedule(result) == []


def test_pod_leader_is_never_used_for_night_shift_even_as_only_candidate():
    role = Role("night", "Brand", "CS", 1, frozenset(range(7)), shift="Night (18:00-04:00)")
    leader = Employee("1", "Leader", "Pod", 6, frozenset({"night"}), pod_role="Pod Lead")
    result = RosterScheduler().generate(RosterInput([leader], [role]), 2027, 1)
    assert result.status == "infeasible"
    assert "Manager action" in result.diagnostics[0]


def test_dedicated_owner_handles_their_lane_when_present():
    role = Role("owned", "Brand", "CS", 1, frozenset(range(7)), primary_employee_id="1")
    owner = Employee("1", "Owner", "Pod", 7, frozenset({"owned"}))
    backup = Employee("2", "Backup", "Pod", 7, frozenset({"owned"}))
    result = RosterScheduler().generate(RosterInput([owner, backup], [role]), 2027, 1)
    assert result.status == "feasible"
    assert all(assignment.employee_id == "1" for assignment in result.assignments)


def test_dedicated_owner_uses_eligible_backup_when_unavailable():
    role = Role("owned", "Brand", "CS", 1, frozenset({0}), primary_employee_id="1")
    owner = Employee("1", "Owner", "Pod", 7, frozenset({"owned"}))
    backup = Employee("2", "Backup", "Pod", 7, frozenset({"owned"}))
    unavailable_day = date(2027, 1, 4)
    result = RosterScheduler().generate(RosterInput([owner, backup], [role], unavailable={unavailable_day: {"1"}}), 2027, 1)
    assert result.status == "feasible"
    assert result.assignments[0].employee_id == "2"


def test_insufficient_eligible_workers_is_explained():
    role = Role("role", "Brand", "CS", 2, frozenset(range(7)))
    employee = Employee("1", "Employee", "Pod", 6, frozenset({"role"}))
    result = RosterScheduler().generate(RosterInput([employee], [role]), 2027, 1)
    assert result.status == "infeasible"
    assert "Manager action" in result.diagnostics[0]
    assert "staffing shortfall of 1" in result.diagnostics[0]


def test_multi_day_leave_rebalances_when_other_eligible_employees_exist():
    role = Role("role", "Brand", "CS", 1, frozenset(range(7)))
    employees = [
        Employee("1", "Absent", "Pod", 6, frozenset({"role"})),
        Employee("2", "Backup A", "Pod", 5, frozenset({"role"})),
        Employee("3", "Backup B", "Pod", 4, frozenset({"role"})),
    ]
    roster_input = RosterInput(
        employees,
        [role],
        unavailable={date(2027, 1, 4): {"1"}, date(2027, 1, 5): {"1"}, date(2027, 1, 6): {"1"}},
        rotation_targets={"role": {date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6)}},
    )
    result = RosterScheduler().generate(roster_input, 2027, 1)
    assert result.status == "feasible"
    leave_dates = {date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6)}
    assert not any(assignment.employee_id == "1" and assignment.date in leave_dates for assignment in result.assignments)
    replacements = {assignment.employee_id for assignment in result.assignments if assignment.date in leave_dates}
    assert replacements == {"2", "3"}


def test_only_approved_availability_is_applied_to_roster_generation():
    unavailable = {}
    live_roster._add_availability_unavailability(unavailable, [
        {"Emp_id": 7, "unavailable_from": "2026-12-03", "unavailable_to": "2026-12-04", "status": "Approved"},
        {"Emp_id": 8, "unavailable_from": "2026-12-03", "unavailable_to": "2026-12-03", "status": "Cancelled"},
    ], 2026, 12)

    assert unavailable == {
        date(2026, 12, 3): {"7"},
        date(2026, 12, 4): {"7"},
    }
