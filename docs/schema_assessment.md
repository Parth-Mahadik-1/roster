# Supabase Schema Assessment

This maps the schema used by the current FastAPI implementation. Row counts are omitted because production data changes; run `python -m scripts.check_supabase` from `backend` for a read-only live inspection.

## Ownership model

The browser talks only to FastAPI. FastAPI reads and writes Supabase with backend environment variables. `SUPABASE_SECRET_KEY` is never a frontend variable.

| Area | Tables | Purpose |
| --- | --- | --- |
| Company masters | `Employee`, `Brand`, `process`, `Brand_processes`, `shifts`, `pods` | Employee and schedulable lane definitions. |
| Demand and eligibility | `staffing_requirements`, `employee_assignments` | Dated lane demand plus employee eligibility/coverage. |
| Calendars and absence | `operating_calendars`, `india_holidays`, `employee_weekly_offs`, `employee_availability` | Open work dates and employee availability. |
| Pod and ownership | `employee_pod_memberships`, `brand_process_shift_owners` | Dated pod membership and protected lane ownership. |
| Leave workflow | `employee_leave_requests` | Employee request, manager decision, review note, and approved availability link. |
| Persisted roster | `roster_runs`, `generated_roster_assignments` | Validated monthly baseline runs. |
| Authentication | `ui_user_profiles` plus Supabase `auth.users` | Confirmed manager account linked to one employee. |

## Required relationships

- `Brand_processes.Brand_id → Brand.Brand_id`
- `Brand_processes.Process_id → process.process_id`
- `staffing_requirements.brand_process_id → Brand_processes.Brand_process_id`
- `staffing_requirements.shift_id → shifts.shift_id`
- `employee_assignments.Emp_id → Employee.Emp_id`
- `employee_assignments.brand_process_id → Brand_processes.Brand_process_id`
- `employee_assignments.shift_id → shifts.shift_id`
- `pods.pod_lead_id → Employee.Emp_id`
- `employee_pod_memberships.Emp_id → Employee.Emp_id`
- `employee_pod_memberships.pod_id → pods.pod_id`
- `employee_availability.Emp_id → Employee.Emp_id`
- `employee_leave_requests.availability_id → employee_availability.availability_id`
- `generated_roster_assignments.roster_run_id → roster_runs.roster_run_id`
- `ui_user_profiles.Emp_id → Employee.Emp_id`

## Scheduling source of truth

`Employee.designation` is used for company role checks and display. Scheduling eligibility comes from active, dated `employee_assignments` rows with `assignment_type = 'Eligibility'`. Temporary replacement work uses `assignment_type = 'Coverage'`; it is not permanent skill mapping.

A monthly solve uses rows effective on each date from requirements, eligibility, shifts, calendars, holidays, weekly offs, availability, pod membership, and dedicated ownership. The scheduler validates employee/date overlap and exact demand before persisting a completed run.

The unique constraint on `generated_roster_assignments (roster_run_id, roster_date, Emp_id)` represents one baseline assignment per employee per day. Approved leave coverage is applied as a targeted overlay from `employee_assignments`, not inserted as a second baseline row. This prevents the earlier HTTP 409 duplicate-key failure while allowing a replacement to see normal and covered work.

## Leave and replacement transaction

1. Employee inserts a `Pending` request.
2. Manager simulation reads the current roster and returns eligible, available suggestions; it does not write.
3. Approval inserts approved `employee_availability`, dated `Coverage` rows, and updates the request to `Approved`.
4. Targeted regeneration validates the saved baseline plus the coverage overlay without reshuffling unaffected employees.
5. Employee views show baseline and coverage together with brand, process, date, and shift.
6. Cancellation deactivates related availability/coverage and marks the request `Cancelled`.

## Authentication flow

- Manager registration verifies employee ID, email, active status, and Manager designation in `Employee`.
- Supabase Auth owns passwords and email confirmation.
- `ui_user_profiles` links the Auth UUID to the active Manager employee.
- Employee login verifies name and email against `Employee`; employees do not create Auth accounts.
- FastAPI issues an HMAC-signed eight-hour token using `ROSTER_SESSION_SECRET`, allowing validation across Vercel function instances.

## Active migrations

Run in this order:

1. `backend/migrations/001_roster_operations.sql`
2. `backend/migrations/002_leave_and_pod_memberships.sql`
3. `backend/migrations/008_employee_leave_request_workflow.sql`
4. `backend/migrations/009_dedicated_brand_lane_owners.sql`
5. `backend/migrations/010_manager_profiles.sql`

Files under `backend/migrations/archive` are historical and must not be automatically applied. `006` removes current authentication objects. `007` rewrites designations and can remove Manager designation.

## Deployment validation

- All required tables are visible to the service role.
- Active employees have unique company emails and names.
- Every demanded lane has enough dated Eligibility rows for its shift.
- Dedicated owners are not placed in incompatible same-day lanes.
- The latest completed roster run has matching generated assignments.
- Frontend variables contain only `VITE_API_BASE_URL`.
