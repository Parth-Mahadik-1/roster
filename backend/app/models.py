from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class Employee:
    id: str
    name: str
    pod: str
    weekly_off: int
    eligible_roles: frozenset[str]
    backup_roles: frozenset[str] = frozenset()
    designation: str = ""
    pod_role: str = ""


@dataclass(frozen=True)
class Role:
    id: str
    brand: str
    process: str
    minimum_staff: int
    open_days: frozenset[int]
    shift: str = "09:00-18:00"
    close_on_holidays: bool = True
    effective_from: date | None = None
    effective_to: date | None = None
    primary_employee_id: str | None = None


@dataclass
class RosterInput:
    employees: list[Employee]
    roles: list[Role]
    unavailable: dict[date, set[str]] = field(default_factory=dict)
    holidays: set[date] = field(default_factory=set)
    # Used only for an approved leave/Comp Off trial. The optimizer spreads
    # these affected role-days across eligible colleagues where possible.
    rotation_targets: dict[str, set[date]] = field(default_factory=dict)


@dataclass(frozen=True)
class Assignment:
    date: date
    employee_id: str
    employee_name: str
    pod: str
    role_id: str
    brand: str
    process: str
    shift: str
    designation: str = ""
    pod_role: str = ""


@dataclass
class ScheduleResult:
    status: str
    assignments: list[Assignment]
    diagnostics: list[str]

    def rows(self) -> list[dict[str, str]]:
        return [
            {
                "date": assignment.date.isoformat(),
                "employee_id": assignment.employee_id,
                "employee": assignment.employee_name,
                "pod": assignment.pod,
                "designation": assignment.designation,
                "pod_role": assignment.pod_role,
                "role_id": assignment.role_id,
                "brand": assignment.brand,
                "process": assignment.process,
                "shift": assignment.shift,
                "status": "Working",
            }
            for assignment in self.assignments
        ]


def dates_in_month(year: int, month: int) -> Iterable[date]:
    from calendar import monthrange

    for day in range(1, monthrange(year, month)[1] + 1):
        yield date(year, month, day)
