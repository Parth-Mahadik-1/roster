from __future__ import annotations

from app.assignment_planner import apply_initial_plan, build_initial_plan, plan_summary


if __name__ == "__main__":
    plan = build_initial_plan()
    print("Planned assignment summary:", plan_summary(plan))
    created = apply_initial_plan(plan)
    print(f"Created {len(created)} employee_assignments records.")
