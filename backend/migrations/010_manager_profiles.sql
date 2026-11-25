BEGIN;

-- Backend-owned application profile linked to Supabase Auth.
-- Browser clients never read or write this table directly.
CREATE TABLE IF NOT EXISTS public.ui_user_profiles (
  auth_user_id UUID PRIMARY KEY,
  "Emp_id" BIGINT NOT NULL UNIQUE REFERENCES public."Employee"("Emp_id"),
  email VARCHAR(320) NOT NULL,
  role VARCHAR(30) NOT NULL DEFAULT 'Manager' CHECK (role IN ('Manager', 'Employee')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ui_user_profiles_employee_idx
  ON public.ui_user_profiles ("Emp_id", is_active);

CREATE OR REPLACE FUNCTION public.set_ui_user_profile_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS ui_user_profiles_updated_at ON public.ui_user_profiles;
CREATE TRIGGER ui_user_profiles_updated_at
BEFORE UPDATE ON public.ui_user_profiles
FOR EACH ROW EXECUTE FUNCTION public.set_ui_user_profile_updated_at();

ALTER TABLE public.ui_user_profiles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ui_user_profiles FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ui_user_profiles TO service_role;

COMMIT;
