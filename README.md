# DataStraw Smart Roster

Production-ready roster management application with a React/Vite frontend, FastAPI backend, Supabase persistence and authentication, and an OR-Tools CP-SAT scheduler. The repository is split into two independent Vercel projects.

## Project layout

```text
datastraw-roster-assignment/
├── backend/                 FastAPI API and Vercel Python Function
│   ├── index.py             Vercel entry point
│   ├── app/                 API, authentication, scheduler and roster logic
│   ├── migrations/          Active Supabase SQL migrations
│   ├── scripts/             Database inspection/seed helpers
│   ├── tests/               Backend test suite
│   ├── .env.example
│   ├── requirements-dev.txt
│   └── requirements.txt
├── front/                   React/Vite manager and employee UI
│   ├── src/
│   ├── .env.example
│   ├── package.json
│   └── vercel.json
└── docs/schema_assessment.md
```

## Current behavior

- Managers register only when employee ID, company email, active status, and Manager designation match the Supabase `Employee` row. Supabase Auth sends the confirmation email.
- Employees sign in with the exact employee name and email stored in `Employee`; no employee registration is required.
- Manager and employee browser tabs keep separate signed sessions. The token works across Vercel function instances when `ROSTER_SESSION_SECRET` is consistent.
- The monthly roster respects eligibility, staffing requirements, shifts, operating calendars, holidays, weekly offs, approved unavailability, pod rules, and dedicated lane owners.
- Leave simulation is advisory. Approval records unavailability and targeted coverage. Regeneration validates and overlays only approved replacement work; unaffected assignments do not move.
- Replacement rows show the covered brand and process while the replacement employee keeps their own assignment.

## Prerequisites

- Python 3.11 or 3.12
- Node.js 20 or newer
- A Supabase project with the company master tables
- A Vercel account

## Supabase setup

The application expects these existing master tables: `Employee`, `Brand`, `process`, `Brand_processes`, `shifts`, `staffing_requirements`, `pods`, and `employee_assignments`.

Run the active SQL files from `backend/migrations` in filename order in the Supabase SQL Editor:

1. `001_roster_operations.sql`
2. `002_leave_and_pod_memberships.sql`
3. `008_employee_leave_request_workflow.sql`
4. `009_dedicated_brand_lane_owners.sql`
5. `010_manager_profiles.sql`

Do not run files under `backend/migrations/archive` during a fresh setup. They are retained only for history: `006` removes the profile table required by current authentication, and `007` rewrites designations used by manager verification.

Confirm that active employees have dated `employee_assignments` rows with `assignment_type = 'Eligibility'`. The scheduler cannot cover a lane without enough eligible employees.

## Local setup

### Backend

```powershell
cd backend
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8010
```

Fill `backend/.env` with real Supabase values. Generate the session secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

API status: `http://127.0.0.1:8010/`  
OpenAPI docs: `http://127.0.0.1:8010/docs`

### Frontend

In a second terminal:

```powershell
cd front
Copy-Item .env.example .env
npm ci
npm run dev
```

For local development, keep `VITE_API_BASE_URL=http://127.0.0.1:8010`. Open the Vite URL shown in the terminal, normally `http://localhost:5173`.

## Environment variables

### Backend only

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATA_SOURCE` | Yes | Set to `supabase` outside isolated sample tests. |
| `SUPABASE_URL` | Yes | Supabase project URL. |
| `SUPABASE_ANON_KEY` | Yes | Supabase Auth sign-up/sign-in calls. |
| `SUPABASE_SECRET_KEY` | Yes | Server-only service-role/secret key for table access. |
| `ROSTER_SESSION_SECRET` | Yes | Shared signing secret for stateless eight-hour sessions. |
| `ROSTER_SECURE_COOKIES` | Production | Set to `1` on HTTPS. |
| `CORS_ORIGINS` | Yes | Exact frontend URLs, comma-separated, without trailing slashes. |
| `CORS_ORIGIN_REGEX` | Optional | Anchored regex for trusted Vercel preview origins. |

### Frontend only

| Variable | Required | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | Yes | Public deployed backend URL without a trailing slash. |

Never add `SUPABASE_SECRET_KEY` or `ROSTER_SESSION_SECRET` to the frontend. Vite variables are public and embedded in the browser bundle.

## Verification

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q

cd ..\front
npm ci
npm run build
```

The database inspection helper is read-only:

```powershell
cd backend
.\.venv\Scripts\python -m scripts.check_supabase
```

## Deploy to Vercel

Use one Git repository and create two Vercel projects from it.

### Backend project

1. Import the repository in Vercel.
2. Set **Root Directory** to `backend`.
3. Vercel detects the FastAPI `app` exported by `backend/index.py`; no route rewrite is required.
4. Add every backend variable above to Production, Preview, and Development as appropriate.
5. Deploy and verify `https://YOUR-BACKEND.vercel.app/` returns API status JSON.

### Frontend project

1. Import the same repository as another Vercel project.
2. Set **Root Directory** to `front`.
3. Add `VITE_API_BASE_URL=https://YOUR-BACKEND.vercel.app`.
4. Deploy.

### Connect deployed origins

1. Set backend `CORS_ORIGINS=https://YOUR-FRONTEND.vercel.app`, then redeploy the backend.
2. In Supabase Dashboard, open **Authentication → URL Configuration**.
3. Set **Site URL** to the frontend production URL.
4. Add the production frontend URL and explicitly trusted preview callback URLs to **Redirect URLs**.
5. Test manager registration/email confirmation, manager login, employee login, request simulation, approval, targeted regeneration, and cancellation.

Vercel variables belong to each Vercel project. Backend secrets stay only in the backend project; the frontend project needs only the public backend URL. Redeploy the frontend after changing `VITE_API_BASE_URL` because Vite reads it at build time.

## Operational notes

- Production must use `DATA_SOURCE=supabase`; process memory is not a database on serverless infrastructure.
- Signed application sessions expire after eight hours.
- A completed roster is persisted in `roster_runs` and `generated_roster_assignments`; readers ignore incomplete runs.
- Approved leave coverage is an overlay and is not inserted as a duplicate baseline employee/date row.
- Vercel duration and bundle limits apply to OR-Tools. Check build/function logs after the first deployment.
- See `docs/schema_assessment.md` for the table map and ownership rules.

## Security checklist

- `.env`, `.vercel`, build output, dependencies, logs, and caches are ignored by Git.
- Only `.env.example` templates are committed.
- Rotate any credential that has ever been committed or shared publicly.
- Keep Supabase Row Level Security enabled; browser code does not receive the service-role key.
- Restrict CORS to known frontend domains.
