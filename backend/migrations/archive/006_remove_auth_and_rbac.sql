BEGIN;

-- Removes only the public authentication/RBAC objects created for this app.
-- It intentionally does NOT remove Supabase's auth.users records or any
-- employee, roster, staffing, brand, process, pod, or calendar tables.
DROP FUNCTION IF EXISTS public.get_my_rbac_access();
DROP FUNCTION IF EXISTS public.has_rbac_permission(VARCHAR);
DROP FUNCTION IF EXISTS public.is_valid_employee_id(BIGINT);
DROP FUNCTION IF EXISTS public.is_roster_manager();

DROP TRIGGER IF EXISTS on_ui_auth_user_created ON auth.users;
DROP FUNCTION IF EXISTS public.create_ui_profile_for_auth_user();

DROP TABLE IF EXISTS public.rbac_role_permissions;
DROP TABLE IF EXISTS public.rbac_permissions;
DROP TABLE IF EXISTS public.rbac_roles;
DROP TABLE IF EXISTS public.ui_user_profiles;

DROP TYPE IF EXISTS public.ui_app_role;

COMMIT;
