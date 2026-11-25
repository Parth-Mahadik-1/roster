from __future__ import annotations

from collections import Counter
from datetime import date

try:
    from ortools.sat.python import cp_model
except ModuleNotFoundError:  # Allows the sample dashboard to run while dependencies download.
    cp_model = None

from .models import Assignment, RosterInput, ScheduleResult, dates_in_month


class RosterScheduler:
    """Builds a monthly roster while keeping operational rules hard."""

    def generate(self, roster_input: RosterInput, year: int, month: int) -> ScheduleResult:
        days = list(dates_in_month(year, month))
        eligible = self._eligible_candidates(roster_input, days)
        diagnostics = self._coverage_precheck(roster_input, days, eligible)
        diagnostics.extend(self._combined_capacity_precheck(roster_input, days, eligible))
        if diagnostics:
            return ScheduleResult("infeasible", [], diagnostics)
        if cp_model is None:
            return self._generate_greedy(roster_input, days, eligible)

        model = cp_model.CpModel()
        variables: dict[tuple[str, date, str], cp_model.IntVar] = {}
        for employee in roster_input.employees:
            for workday in days:
                for role in roster_input.roles:
                    if employee.id in eligible[(workday, role.id)]:
                        variables[(employee.id, workday, role.id)] = model.NewBoolVar(
                            f"assign_{employee.id}_{workday.isoformat()}_{role.id}"
                        )

        for workday in days:
            for employee in roster_input.employees:
                day_assignments = [
                    variable
                    for (employee_id, assigned_day, _), variable in variables.items()
                    if employee_id == employee.id and assigned_day == workday
                ]
                if day_assignments:
                    model.Add(sum(day_assignments) <= 1)

            for role in roster_input.roles:
                required = role.minimum_staff if self._operates(role, workday, roster_input) else 0
                role_assignments = [
                    variables[(employee_id, workday, role.id)]
                    for employee_id in eligible[(workday, role.id)]
                ]
                model.Add(sum(role_assignments) == required)

        workloads = []
        for employee in roster_input.employees:
            total = sum(
                variable for (employee_id, _, _), variable in variables.items() if employee_id == employee.id
            )
            workloads.append(total)
        maximum = model.NewIntVar(0, len(days), "maximum_workload")
        minimum = model.NewIntVar(0, len(days), "minimum_workload")
        for workload in workloads:
            model.Add(workload <= maximum)
            model.Add(workload >= minimum)
        employee_by_id_for_objective = {employee.id: employee for employee in roster_input.employees}
        # A pod leader is the operational fallback. Prefer eligible non-leaders
        # whenever coverage is possible, then balance the remaining workload.
        leader_assignments = sum(
            variable
            for (employee_id, _, _), variable in variables.items()
            if employee_by_id_for_objective[employee_id].pod_role == "Pod Lead"
        )
        # Dedicated owners are a first-priority preference, not an absolute
        # lock: hard-locking them can make another specialist lane uncovered.
        # The optimiser therefore assigns the owner whenever it can do so
        # without breaking coverage, one-task-per-day, leave or weekly-off.
        dedicated_owner_misses = []
        for workday in days:
            for role in roster_input.roles:
                owner = role.primary_employee_id
                if owner and owner in eligible[(workday, role.id)] and self._operates(role, workday, roster_input):
                    dedicated_owner_misses.append(1 - variables[(owner, workday, role.id)])
        # For a multi-day leave/Comp Off only, spread the affected role-days
        # across backups where possible. This stays small and does not burden
        # a normal full-month roster solve.
        rotation_maxima = []
        for role_id, target_days in roster_input.rotation_targets.items():
            relevant_days = sorted(day for day in target_days if day in days)
            if not relevant_days:
                continue
            target_employees = {
                employee_id for workday in relevant_days for employee_id in eligible[(workday, role_id)]
            }
            if not target_employees:
                continue
            maximum_replacement_load = model.NewIntVar(0, len(relevant_days), f"replacement_max_{role_id}")
            for employee_id in target_employees:
                assigned_target_days = [
                    variables[(employee_id, workday, role_id)]
                    for workday in relevant_days
                    if (employee_id, workday, role_id) in variables
                ]
                if assigned_target_days:
                    model.Add(sum(assigned_target_days) <= maximum_replacement_load)
            rotation_maxima.append(maximum_replacement_load)
        rotation_weight = len(days) + 1
        rotation_upper_bound = sum(len(target_days) for target_days in roster_input.rotation_targets.values())
        leader_weight = (rotation_upper_bound + 1) * rotation_weight + len(days) + 1
        owner_weight = (len(days) + 1) * leader_weight
        model.Minimize(
            sum(dedicated_owner_misses) * owner_weight
            + leader_assignments * leader_weight
            + sum(rotation_maxima) * rotation_weight
            + (maximum - minimum)
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 10
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 1
        outcome = solver.Solve(model)
        if outcome not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return ScheduleResult("infeasible", [], self._matching_shortfall_messages(roster_input, days, eligible))

        employee_by_id = {employee.id: employee for employee in roster_input.employees}
        role_by_id = {role.id: role for role in roster_input.roles}
        assignments = []
        for (employee_id, workday, role_id), variable in variables.items():
            if solver.Value(variable):
                employee = employee_by_id[employee_id]
                role = role_by_id[role_id]
                assignments.append(
                    Assignment(
                        workday, employee.id, employee.name, employee.pod, role.id, role.brand, role.process,
                        role.shift, employee.designation, employee.pod_role,
                    )
                )
        assignments.sort(key=lambda assignment: (assignment.date, assignment.employee_id, assignment.role_id))
        return ScheduleResult("feasible", assignments, [])

    def _generate_greedy(
        self, roster_input: RosterInput, days: list[date], eligible: dict[tuple[date, str], list[str]]
    ) -> ScheduleResult:
        """A development fallback used only when OR-Tools is unavailable locally."""
        employee_by_id = {employee.id: employee for employee in roster_input.employees}
        workloads = Counter()
        assignments = []
        for workday in days:
            already_assigned = set()
            open_roles = [role for role in roster_input.roles if self._operates(role, workday, roster_input)]
            open_roles.sort(key=lambda role: len(eligible[(workday, role.id)]))
            for role in open_roles:
                selected = []
                candidates = sorted(
                    (employee_id for employee_id in eligible[(workday, role.id)] if employee_id not in already_assigned),
                    key=lambda employee_id: (employee_id != role.primary_employee_id, workloads[employee_id], employee_id),
                )
                if len(candidates) < role.minimum_staff:
                    return ScheduleResult(
                        "infeasible", [], [f"{workday.isoformat()} cannot allocate {role.brand} / {role.process} without overlap."]
                    )
                for employee_id in candidates[: role.minimum_staff]:
                    selected.append(employee_id)
                    already_assigned.add(employee_id)
                    workloads[employee_id] += 1
                    employee = employee_by_id[employee_id]
                    assignments.append(
                        Assignment(
                            workday, employee.id, employee.name, employee.pod, role.id, role.brand, role.process,
                            role.shift, employee.designation, employee.pod_role,
                        )
                    )
        assignments.sort(key=lambda assignment: (assignment.date, assignment.employee_id, assignment.role_id))
        return ScheduleResult("feasible", assignments, ["OR-Tools is not installed, so this local run uses the development fallback."])

    def _eligible_candidates(self, roster_input: RosterInput, days: list[date]) -> dict[tuple[date, str], list[str]]:
        candidates: dict[tuple[date, str], list[str]] = {}
        for workday in days:
            unavailable = roster_input.unavailable.get(workday, set())
            for role in roster_input.roles:
                candidates[(workday, role.id)] = [
                    employee.id
                    for employee in roster_input.employees
                    if role.id in employee.eligible_roles
                    and employee.id not in unavailable
                    and employee.weekly_off != workday.weekday()
                    and not (employee.pod_role == "Pod Lead" and role.shift.lower().startswith("night"))
                ]
        return candidates

    def _coverage_precheck(self, roster_input: RosterInput, days: list[date], eligible: dict[tuple[date, str], list[str]]) -> list[str]:
        diagnostics = []
        for workday in days:
            grouped: dict[tuple[str, str, str], list] = {}
            for role in roster_input.roles:
                if not self._operates(role, workday, roster_input):
                    continue
                grouped.setdefault((role.brand, role.process, role.shift), []).append(role)
            for (brand, process, shift), roles in grouped.items():
                required = sum(role.minimum_staff for role in roles)
                available = {
                    employee_id for role in roles for employee_id in eligible[(workday, role.id)]
                }
                shortfall = required - len(available)
                if shortfall > 0:
                    diagnostics.append(
                        f"Manager action — {workday.isoformat()} | {brand} / {process} / {shift}: "
                        f"staffing shortfall of {shortfall}. Required {required}; eligible and available {len(available)}. "
                        f"Add {shortfall} eligible employee(s) or enable approved backup eligibility."
                    )
        return diagnostics

    def _combined_capacity_precheck(
        self, roster_input: RosterInput, days: list[date], eligible: dict[tuple[date, str], list[str]]
    ) -> list[str]:
        diagnostics = []
        for workday in days:
            operating_roles = [role for role in roster_input.roles if self._operates(role, workday, roster_input)]
            required = sum(role.minimum_staff for role in operating_roles)
            unique_candidates = {employee_id for role in operating_roles for employee_id in eligible[(workday, role.id)]}
            if len(unique_candidates) < required:
                diagnostics.append(
                    f"Manager action — {workday.isoformat()}: total staffing shortfall of {required - len(unique_candidates)}. "
                    f"Active roles require {required} assignments but only {len(unique_candidates)} distinct eligible employees are available."
                )
        return diagnostics

    def _matching_shortfall_messages(
        self, roster_input: RosterInput, days: list[date], eligible: dict[tuple[date, str], list[str]]
    ) -> list[str]:
        """Explain a Hall-style conflict that aggregate headcount checks cannot see."""
        for workday in days:
            roles = [role for role in roster_input.roles if self._operates(role, workday, roster_input)]
            assigned_role_by_employee: dict[str, str] = {}

            def assign(role_id: str, visited: set[str]) -> bool:
                for employee_id in eligible[(workday, role_id)]:
                    if employee_id in visited:
                        continue
                    visited.add(employee_id)
                    previous_role = assigned_role_by_employee.get(employee_id)
                    if previous_role is None or assign(previous_role, visited):
                        assigned_role_by_employee[employee_id] = role_id
                        return True
                return False

            for role in sorted(roles, key=lambda item: len(eligible[(workday, item.id)])):
                if not assign(role.id, set()):
                    return [
                        f"Manager action — {workday.isoformat()} | {role.brand} / {role.process} / {role.shift}: "
                        "the eligible employees are already required by other active roles. "
                        "Add one cross-trained eligible employee or enable an approved backup for this role."
                    ]
        return [
            "Manager action — the roster constraints cannot be satisfied. Add eligible capacity or review weekly-off, leave, and backup eligibility settings."
        ]

    @staticmethod
    def _operates(role, workday: date, roster_input: RosterInput) -> bool:
        return (
            (role.effective_from is None or workday >= role.effective_from)
            and (role.effective_to is None or workday <= role.effective_to)
            and workday.weekday() in role.open_days and not (
            role.close_on_holidays and workday in roster_input.holidays
            )
        )


def validate_schedule(result: ScheduleResult, roster_input: RosterInput | None = None) -> list[str]:
    issues = []
    per_employee_day = Counter((assignment.employee_id, assignment.date) for assignment in result.assignments)
    for (employee_id, workday), count in per_employee_day.items():
        if count > 1:
            issues.append(f"{employee_id} has {count} overlapping assignments on {workday.isoformat()}.")
    if roster_input is None:
        return issues
    employees = {employee.id: employee for employee in roster_input.employees}
    roles = {role.id: role for role in roster_input.roles}
    coverage = Counter((assignment.date, assignment.role_id) for assignment in result.assignments)
    for assignment in result.assignments:
        employee = employees[assignment.employee_id]
        if assignment.role_id not in employee.eligible_roles:
            issues.append(f"{employee.id} is not eligible for {assignment.role_id}.")
        if employee.weekly_off == assignment.date.weekday() or employee.id in roster_input.unavailable.get(assignment.date, set()):
            issues.append(f"{employee.id} is unavailable on {assignment.date.isoformat()}.")
    for workday in {assignment.date for assignment in result.assignments}:
        for role in roles.values():
            required = role.minimum_staff if RosterScheduler._operates(role, workday, roster_input) else 0
            if coverage[(workday, role.id)] != required:
                issues.append(f"{workday.isoformat()} coverage for {role.id} is {coverage[(workday, role.id)]}, requires {required}.")
    return issues
