/*
DataStraw roster workforce audit — READ-ONLY BY DEFAULT

Table map
---------
Employee                         HR identity and Active/Inactive status
employee_assignments             Eligibility, Primary, Backup, Coverage and Absence records
Brand_processes                  Brand-to-process bridge used by requirements and assignments
Brand / process / shifts         Operational labels and shift times
staffing_requirements            Required employees per brand/process/shift and effective dates
brand_process_shift_owners       Dedicated task owners — always protected
pods                             Pod leads — always protected
employee_pod_memberships         Employee-to-pod history
employee_weekly_offs             One configured weekday off per roster employee
employee_availability            Approved leave, Comp Off and same-day unavailability
employee_leave_requests          Pending/Approved/Rejected/Cancelled manager workflow
roster_runs                      Versioned monthly generation runs
generated_roster_assignments     Saved assignments belonging to one roster_run_id

Safety policy used by this report
---------------------------------
1. Never select a dedicated owner, pod lead or Manager for deactivation.
2. Never select a multi-skilled employee automatically.
3. Keep enough eligible employees for peak daily demand plus one weekly off.
4. Keep at least two non-protected backups per process where that capacity exists.
5. Select at most one employee from any single process in one review cycle.
6. Deactivate with Employee.emp_status = 'Inactive'; never DELETE HR records.
*/

-- Change only these dates when auditing another roster month.
WITH RECURSIVE
params AS (
  SELECT
    DATE '2026-12-01' AS period_start,
    DATE '2026-12-31' AS period_end,
    2::INTEGER AS desired_backups_per_process,
    1::INTEGER AS max_changes_per_process
),
calendar_days AS (
  SELECT day::DATE
  FROM params, generate_series(period_start, period_end, INTERVAL '1 day') AS day
),
active_employees AS (
  SELECT "Emp_id", emp_name, designation
  FROM public."Employee"
  WHERE lower(emp_status) = 'active'
),
protected_employees AS (
  SELECT p.pod_lead_id AS "Emp_id", 'Pod lead'::TEXT AS reason
  FROM public.pods p
  WHERE p.pod_lead_id IS NOT NULL
  UNION
  SELECT o."Emp_id", 'Dedicated owner'
  FROM public.brand_process_shift_owners o
  WHERE lower(o.status) = 'active'
  UNION
  SELECT e."Emp_id", 'Manager account'
  FROM active_employees e
  WHERE lower(coalesce(e.designation, '')) LIKE '%manager%'
),
employee_skills AS (
  SELECT DISTINCT
    ea."Emp_id",
    upper(trim(pr.process_name)) AS process_name
  FROM public.employee_assignments ea
  JOIN public."Brand_processes" bp
    ON bp."Brand_process_id" = ea.brand_process_id
  JOIN public.process pr
    ON pr.process_id = bp."Process_id"
  WHERE lower(ea.status) = 'active'
    AND lower(ea.assignment_type) = 'eligibility'
),
skill_counts AS (
  SELECT "Emp_id", count(*) AS skill_count
  FROM employee_skills
  GROUP BY "Emp_id"
),
operating_demand AS (
  SELECT
    d.day,
    upper(trim(pr.process_name)) AS process_name,
    sum(sr.required_employees)::INTEGER AS required_employees
  FROM calendar_days d
  JOIN public.staffing_requirements sr
    ON d.day BETWEEN sr.effective_from AND sr.effective_to
  JOIN public."Brand_processes" bp
    ON bp."Brand_process_id" = sr.brand_process_id
  JOIN public.process pr
    ON pr.process_id = bp."Process_id"
  LEFT JOIN public.operating_calendars oc
    ON oc.brand_process_id = sr.brand_process_id
   AND lower(oc.status) = 'active'
   AND d.day BETWEEN oc.effective_from AND oc.effective_to
  LEFT JOIN public.india_holidays h
    ON h.holiday_date = d.day
   AND NOT h.is_working_override
  WHERE NOT (
      extract(isodow FROM d.day) = 7
      AND coalesce(oc.operates_sundays, true) = false
    )
    AND NOT (
      h.holiday_date IS NOT NULL
      AND coalesce(oc.observes_public_holidays, false) = true
    )
  GROUP BY d.day, upper(trim(pr.process_name))
),
process_demand AS (
  SELECT process_name, max(required_employees)::INTEGER AS peak_daily_required
  FROM operating_demand
  GROUP BY process_name
),
eligible_counts AS (
  SELECT process_name, count(*)::INTEGER AS active_eligible
  FROM employee_skills es
  JOIN active_employees ae USING ("Emp_id")
  GROUP BY process_name
),
protected_counts AS (
  SELECT es.process_name, count(DISTINCT es."Emp_id")::INTEGER AS protected_eligible
  FROM employee_skills es
  JOIN protected_employees pe USING ("Emp_id")
  GROUP BY es.process_name
),
capacity AS (
  SELECT
    d.process_name,
    d.peak_daily_required,
    e.active_eligible,
    coalesce(pc.protected_eligible, 0) AS protected_eligible,
    ceil(d.peak_daily_required * 7.0 / 6.0)::INTEGER AS minimum_with_one_weekly_off,
    greatest(
      ceil(d.peak_daily_required * 7.0 / 6.0)::INTEGER,
      coalesce(pc.protected_eligible, 0) + p.desired_backups_per_process
    ) AS recommended_active_to_keep,
    least(
      p.max_changes_per_process,
      greatest(
        0,
        e.active_eligible - greatest(
          ceil(d.peak_daily_required * 7.0 / 6.0)::INTEGER,
          coalesce(pc.protected_eligible, 0) + p.desired_backups_per_process
        )
      )
    ) AS safe_change_limit
  FROM process_demand d
  JOIN eligible_counts e USING (process_name)
  LEFT JOIN protected_counts pc USING (process_name)
  CROSS JOIN params p
)
SELECT *
FROM capacity
ORDER BY process_name;

-- Protected employee audit. Every row returned here must remain Active.
WITH protected AS (
  SELECT p.pod_lead_id AS "Emp_id", 'Pod lead'::TEXT AS reason
  FROM public.pods p WHERE p.pod_lead_id IS NOT NULL
  UNION
  SELECT o."Emp_id", 'Dedicated owner'
  FROM public.brand_process_shift_owners o WHERE lower(o.status) = 'active'
  UNION
  SELECT e."Emp_id", 'Manager account'
  FROM public."Employee" e
  WHERE lower(e.emp_status) = 'active'
    AND lower(coalesce(e.designation, '')) LIKE '%manager%'
)
SELECT e."Emp_id", e.emp_name, e.designation,
       string_agg(DISTINCT p.reason, ', ' ORDER BY p.reason) AS protection_reason
FROM protected p
JOIN public."Employee" e USING ("Emp_id")
GROUP BY e."Emp_id", e.emp_name, e.designation
ORDER BY e."Emp_id";

/*
Ranked review candidates for December 2026.

This query intentionally returns candidates for human review; it does not update data.
It protects dedicated owners, pod leads, managers and multi-skilled employees. It also
limits the recommendation to one employee per process and only where the capacity
calculation retains two backups.
*/
WITH RECURSIVE
params AS (
  SELECT DATE '2026-12-01' AS period_start,
         DATE '2026-12-31' AS period_end,
         2::INTEGER AS desired_backups_per_process,
         1::INTEGER AS max_changes_per_process
),
calendar_days AS (
  SELECT day::DATE
  FROM params, generate_series(period_start, period_end, INTERVAL '1 day') AS day
),
latest_run AS (
  SELECT rr.roster_run_id
  FROM public.roster_runs rr, params p
  WHERE rr.roster_year = extract(year FROM p.period_start)
    AND rr.roster_month = extract(month FROM p.period_start)
    AND lower(rr.status) IN ('validated', 'published')
  ORDER BY rr.roster_run_id DESC
  LIMIT 1
),
workload AS (
  SELECT gra."Emp_id", count(*)::INTEGER AS monthly_assignments
  FROM public.generated_roster_assignments gra
  JOIN latest_run lr USING (roster_run_id)
  GROUP BY gra."Emp_id"
),
protected AS (
  SELECT p.pod_lead_id AS "Emp_id" FROM public.pods p WHERE p.pod_lead_id IS NOT NULL
  UNION
  SELECT o."Emp_id" FROM public.brand_process_shift_owners o WHERE lower(o.status) = 'active'
  UNION
  SELECT e."Emp_id" FROM public."Employee" e
  WHERE lower(e.emp_status) = 'active'
    AND lower(coalesce(e.designation, '')) LIKE '%manager%'
),
employee_skills AS (
  SELECT DISTINCT ea."Emp_id", upper(trim(pr.process_name)) AS process_name
  FROM public.employee_assignments ea
  JOIN public."Brand_processes" bp ON bp."Brand_process_id" = ea.brand_process_id
  JOIN public.process pr ON pr.process_id = bp."Process_id"
  WHERE lower(ea.status) = 'active' AND lower(ea.assignment_type) = 'eligibility'
),
skill_counts AS (
  SELECT "Emp_id", count(*) AS skill_count FROM employee_skills GROUP BY "Emp_id"
),
operating_demand AS (
  SELECT d.day, upper(trim(pr.process_name)) AS process_name,
         sum(sr.required_employees)::INTEGER AS required_employees
  FROM calendar_days d
  JOIN public.staffing_requirements sr ON d.day BETWEEN sr.effective_from AND sr.effective_to
  JOIN public."Brand_processes" bp ON bp."Brand_process_id" = sr.brand_process_id
  JOIN public.process pr ON pr.process_id = bp."Process_id"
  LEFT JOIN public.operating_calendars oc
    ON oc.brand_process_id = sr.brand_process_id
   AND lower(oc.status) = 'active'
   AND d.day BETWEEN oc.effective_from AND oc.effective_to
  LEFT JOIN public.india_holidays h ON h.holiday_date = d.day AND NOT h.is_working_override
  WHERE NOT (extract(isodow FROM d.day) = 7 AND coalesce(oc.operates_sundays, true) = false)
    AND NOT (h.holiday_date IS NOT NULL AND coalesce(oc.observes_public_holidays, false) = true)
  GROUP BY d.day, upper(trim(pr.process_name))
),
process_demand AS (
  SELECT process_name, max(required_employees)::INTEGER AS peak_daily_required
  FROM operating_demand GROUP BY process_name
),
eligible_counts AS (
  SELECT es.process_name, count(*)::INTEGER AS active_eligible
  FROM employee_skills es
  JOIN public."Employee" e USING ("Emp_id")
  WHERE lower(e.emp_status) = 'active'
  GROUP BY es.process_name
),
protected_counts AS (
  SELECT es.process_name, count(DISTINCT es."Emp_id")::INTEGER AS protected_eligible
  FROM employee_skills es JOIN protected p USING ("Emp_id")
  GROUP BY es.process_name
),
limits AS (
  SELECT d.process_name,
         least(
           p.max_changes_per_process,
           greatest(
             0,
             ec.active_eligible - greatest(
               ceil(d.peak_daily_required * 7.0 / 6.0)::INTEGER,
               coalesce(pc.protected_eligible, 0) + p.desired_backups_per_process
             )
           )
         ) AS safe_change_limit
  FROM process_demand d
  JOIN eligible_counts ec USING (process_name)
  LEFT JOIN protected_counts pc USING (process_name)
  CROSS JOIN params p
),
ranked AS (
  SELECT
    es.process_name,
    e."Emp_id",
    e.emp_name,
    e.designation,
    coalesce(w.monthly_assignments, 0) AS monthly_assignments,
    row_number() OVER (
      PARTITION BY es.process_name
      ORDER BY coalesce(w.monthly_assignments, 0), e."Emp_id"
    ) AS candidate_rank,
    l.safe_change_limit
  FROM employee_skills es
  JOIN public."Employee" e USING ("Emp_id")
  JOIN skill_counts sc USING ("Emp_id")
  JOIN limits l USING (process_name)
  LEFT JOIN workload w USING ("Emp_id")
  LEFT JOIN protected p USING ("Emp_id")
  WHERE lower(e.emp_status) = 'active'
    AND p."Emp_id" IS NULL
    AND sc.skill_count = 1
)
SELECT process_name, "Emp_id", emp_name, designation, monthly_assignments,
       candidate_rank, safe_change_limit,
       (candidate_rank <= safe_change_limit) AS within_safe_limit
FROM ranked
WHERE safe_change_limit > 0
ORDER BY process_name, candidate_rank, "Emp_id";

/*
APPROVAL-GATED DEACTIVATION TEMPLATE — DOES NOTHING AS WRITTEN
----------------------------------------------------------------
After management approval:
  1. Put only approved Emp_id values in approved_employee_ids.
  2. Run the audit queries again.
  3. Keep ROLLBACK for a dry run and inspect RETURNING output.
  4. Change ROLLBACK to COMMIT only after explicit final approval.

Never replace this with DELETE FROM public."Employee".
*/
BEGIN;

WITH approved_employee_ids AS (
  -- Empty by design. Example after approval: SELECT 23::BIGINT AS "Emp_id"
  SELECT NULL::BIGINT AS "Emp_id" WHERE false
),
protected AS (
  SELECT p.pod_lead_id AS "Emp_id" FROM public.pods p WHERE p.pod_lead_id IS NOT NULL
  UNION
  SELECT o."Emp_id" FROM public.brand_process_shift_owners o WHERE lower(o.status) = 'active'
  UNION
  SELECT e."Emp_id" FROM public."Employee" e
  WHERE lower(coalesce(e.designation, '')) LIKE '%manager%'
)
UPDATE public."Employee" e
SET emp_status = 'Inactive'
FROM approved_employee_ids a
LEFT JOIN protected p USING ("Emp_id")
WHERE e."Emp_id" = a."Emp_id"
  AND p."Emp_id" IS NULL
  AND lower(e.emp_status) = 'active'
RETURNING e."Emp_id", e.emp_name, e.designation, e.emp_status;

ROLLBACK;
