"""Select a non-conflicting 80% set of dedicated brand lane owners."""

from __future__ import annotations

from math import ceil

from .live_roster import _load_tables
from .roster_persistence import insert_rows


def seed_dedicated_owners(target_ratio: float = 0.8) -> dict:
    """Persist up to 80% dedicated owners without assigning one person two lanes.

    The most constrained lanes are allocated first. Existing active owners are
    retained; a lane is never seeded with a pod lead for a night shift.
    """
    if not 0 < target_ratio <= 1:
        raise ValueError("target_ratio must be between 0 and 1.")
    tables = _load_tables()
    active_requirements = [row for row in tables["requirements"] if row.get("required_employees", 0) > 0]
    lanes: dict[tuple[int, int], dict] = {}
    for row in active_requirements:
        key = (row["brand_process_id"], row["shift_id"])
        lane = lanes.setdefault(key, {
            "brand_process_id": key[0], "shift_id": key[1],
            "effective_from": row["effective_from"], "effective_to": row["effective_to"],
        })
        lane["effective_from"] = min(lane["effective_from"], row["effective_from"])
        lane["effective_to"] = max(lane["effective_to"], row["effective_to"])

    leaders = set(tables["pod_by_lead"])
    active_eligibility: dict[tuple[int, int], list[int]] = {key: [] for key in lanes}
    for row in tables["assignments"]:
        key = (row.get("brand_process_id"), row.get("shift_id"))
        if key not in lanes or str(row.get("assignment_type", "")).lower() != "eligibility" or str(row.get("status", "")).lower() != "active":
            continue
        shift_name = tables["shifts"][key[1]]["shift_name"].lower()
        if row["Emp_id"] in leaders and shift_name.startswith("night"):
            continue
        if row["Emp_id"] not in active_eligibility[key]:
            active_eligibility[key].append(row["Emp_id"])

    existing = {
        (row["brand_process_id"], row["shift_id"]): row["Emp_id"]
        for row in tables["dedicated_owners"]
        if str(row.get("status", "")).lower() == "active"
    }
    target = ceil(len(lanes) * target_ratio)
    used_employees = set(existing.values())
    selected = dict(existing)
    selected_by_pod: dict[str, int] = {}
    for employee_id in used_employees:
        pod = tables["pod_by_employee"].get(employee_id, tables["pod_by_lead"].get(employee_id, "Unassigned"))
        selected_by_pod[pod] = selected_by_pod.get(pod, 0) + 1
    rows = []
    for key in sorted(lanes, key=lambda lane: (len(active_eligibility[lane]), lane)):
        if len(selected) >= target:
            break
        if key in selected:
            continue
        candidates = [emp_id for emp_id in active_eligibility[key] if emp_id not in used_employees]
        if not candidates:
            continue
        candidate = min(
            candidates,
            key=lambda emp_id: (
                selected_by_pod.get(tables["pod_by_employee"].get(emp_id, tables["pod_by_lead"].get(emp_id, "Unassigned")), 0),
                emp_id,
            ),
        )
        lane = lanes[key]
        rows.append({**lane, "Emp_id": candidate, "status": "Active"})
        selected[key] = candidate
        used_employees.add(candidate)
        pod = tables["pod_by_employee"].get(candidate, tables["pod_by_lead"].get(candidate, "Unassigned"))
        selected_by_pod[pod] = selected_by_pod.get(pod, 0) + 1
    saved = insert_rows("brand_process_shift_owners", rows)
    return {
        "total_lanes": len(lanes),
        "target_lanes": target,
        "active_dedicated_lanes": len(selected),
        "coverage_percent": round(100 * len(selected) / len(lanes), 1) if lanes else 0,
        "created": len(saved),
        "unassigned_lanes": len(lanes) - len(selected),
    }
