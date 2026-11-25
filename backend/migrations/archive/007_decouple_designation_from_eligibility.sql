BEGIN;

-- Safety gate: skills must already be stored explicitly before titles are
-- made process-neutral.  This migration never adds, removes, or edits an
-- employee's eligibility, brand/process assignment, shift, leave, or roster.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM public.employee_assignments
    WHERE LOWER(COALESCE(status, '')) = 'active'
      AND LOWER(COALESCE(assignment_type, '')) = 'eligibility'
  ) THEN
    RAISE EXCEPTION
      'No active Eligibility records found in employee_assignments. Add them before running this migration.';
  END IF;
END $$;

-- `designation` is an HR/display title only.  It must not identify the
-- employee's schedulable process; that many-to-many mapping lives in
-- employee_assignments (assignment_type = Eligibility).
COMMENT ON COLUMN public."Employee".designation IS
  'HR/display title only. Scheduling eligibility is defined by active employee_assignments Eligibility rows.';

UPDATE public."Employee" AS employee
SET designation = CASE
  WHEN EXISTS (
    SELECT 1
    FROM public.pods AS pod
    WHERE pod.pod_lead_id = employee."Emp_id"
  ) THEN 'Operations Lead'
  ELSE 'Operations Executive'
END
WHERE LOWER(COALESCE(employee.emp_status, '')) = 'active';

COMMIT;
