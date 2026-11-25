from __future__ import annotations

from .models import Employee, Role, RosterInput


def demo_data() -> RosterInput:
    pods = ["Alpha", "Bravo", "Charlie", "Delta"]
    role_ids = ["nova-support", "nova-acc", "nova-ndr", "orbit-support", "orbit-verify", "apex-support", "apex-returns", "lumen-quality"]
    roles = [
        Role("nova-support", "Nova Retail", "Customer Support", 1, frozenset(range(7)), close_on_holidays=False),
        Role("nova-acc", "Nova Retail", "ACC", 1, frozenset(range(6))),
        Role("nova-ndr", "Nova Retail", "NDR", 1, frozenset(range(6))),
        Role("orbit-support", "Orbit Commerce", "Customer Support", 1, frozenset(range(6))),
        Role("orbit-verify", "Orbit Commerce", "Verification", 1, frozenset(range(6))),
        Role("apex-support", "Apex Services", "Customer Support", 1, frozenset(range(5))),
        Role("apex-returns", "Apex Services", "Returns", 1, frozenset(range(5))),
        Role("lumen-quality", "Lumen Media", "Quality", 1, frozenset(range(6))),
    ]
    employees = []
    for number in range(1, 33):
        primary = role_ids[(number - 1) % len(role_ids)]
        secondary = role_ids[number % len(role_ids)]
        backup = role_ids[(number + 2) % len(role_ids)]
        employees.append(
            Employee(
                id=f"EMP{number:03d}",
                name=f"Employee {number:02d}",
                pod=pods[(number - 1) % len(pods)],
                weekly_off=(number - 1) % 7,
                eligible_roles=frozenset({primary, secondary, backup}),
                backup_roles=frozenset({backup}),
            )
        )
    return RosterInput(employees=employees, roles=roles)
