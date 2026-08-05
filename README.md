# PI3 Plant Edition — v0.1 internal prototype

Flexible slabstock foam expert system for HTC Global Co. Ltd. Captures and
connects recipe versions, production runs, runtime data, quality test
results, and quality issues, across the three parents a run/trial can
belong to (Production Run, Customer Trial, Optimization Trial) — with a
controlled advisory boundary and optional PI3/AI connectivity add-on. Built
per `CharlieC_Build_Prompt_Pack_PI3_Plant_Edition`.

This is a controlled prototype: flexible slabstock foam only, manual-entry
first with CSV/Excel import, no ERP or live machine integration, no
autonomous formulation optimization.

## Structure

- `app.py` — Dashboard (Screen 1, entry point)
- `pages/` — the remaining 11 screens (see below)
- `db.py` — SQLAlchemy models for the 16 v0.1 entities, plus the multi-
  tenant layer (`Company`, `SubscriptionType`, `Role`, `RolePagePermission`,
  `User`)
- `auth.py` — database-backed login (hashed passwords, per-user validity
  window), falling back to `secrets.toml` only on a fresh/unmigrated
  deployment with no `users` rows yet
- `access_control.py` — shared page-visibility rules: which pages a role
  can see, and which a company's subscription gates
- `helpers.py` — shared UI helpers, advisory disclaimer text
- `demo_data.py` — seeds the internal demonstration case (no real client
  data); not wired into the UI anymore (the Demo Data Admin page was
  removed 2026-07-31) - call `seed_demo_data(session)` directly for a
  throwaway local/dev database

## Screens

1. Dashboard (`app.py`)
2. Plant & Foam Equipment Overview
3. Product Family & Foam Grade Profile
4. Recipes
5. Production Run (also handles runtime data entry + CSV import)
6. Quality Test Result
7. Quality Issue
8. PI3 Connectivity (platform-owner only: per-plant PI3/AI on-off switch)
9. User Accounts (a company's own admin manages their own users; platform owner manages every company's)
10. User Roles (a company's own admin narrows their own built-in-role clones + custom roles; platform owner manages every company's)
11. Default User Roles (platform-owner only: the template new companies' built-in roles are seeded from)
12. Companies (platform-owner only: the tenant boundary)
13. Subscription Types (platform-owner only: commercial tiers, limits/features, list prices)

This list predates several pages added since (Raw Materials, Production
Samples, Customer Trials & Samples, Optimization Trials & Samples, the
five Industrial Intelligence screens, Report, Expert Notes) and postdates
several removals: Similar Case Retrieval (dropped 2026-08-01), the Trial /
Experiment + Adjustment & Conclusion + Approval & Review trio (dropped
2026-08-04, once Customer Trial/Optimization Trial made that separate
trial-closeout workflow redundant — see `access_control.py`'s docstring
and `PI3_Gaps_and_Ambiguities.docx`), and conditioning tracking (dropped
2026-08-04 per user direction — samples now live directly on the page for
their source: Production Samples / Customer Trials & Samples /
Optimization Trials & Samples, all under the Samples & Trials nav
section, with no conditioning history behind them). Treat `app.py`'s own
nav-section lists as the actual source of truth for what screens exist,
not this list's exact count.

## Trial data model

A quality test result or quality issue always belongs to exactly one of
three parents: a Production Run, a Customer Trial, or an Optimization
Trial (`db.SAMPLE_SOURCE_TYPES`). Customer Trials and Optimization Trials
are their own independent lab-trial flows (`pages/11_Customer_Trials.py`,
`pages/12_Optimization_Trials.py`) — they don't hang off a Production Run.

## The one rule that can't be bypassed

A Customer Trial cannot be closed unless `outcome`, `reviewed_by`, and
`date_closed` are all present; an Optimization Trial additionally requires
`conclusion`, `reuse_recommendation`, and `approved_by`. This is enforced
in `db.py` (`CustomerTrial.can_close()` / `OptimizationTrial.can_close()`)
and checked again in `pages/11_Customer_Trials.py` /
`pages/12_Optimization_Trials.py` before the "Close trial" button is
enabled.

## Deploying to Streamlit Community Cloud

### 1. Database — Supabase Postgres

Streamlit Community Cloud's filesystem is not guaranteed to persist across
app reboots or redeploys, so this app is built to use a hosted Postgres
database rather than a local SQLite file.

1. Create a free project at supabase.com.
2. Go to **Project Settings > Database > Connection string > URI**, and copy
   the **Session pooler** connection string (works better than the direct
   connection from serverless/app-hosting environments).
3. It will look like:
   `postgresql://postgres.xxxxx:[PASSWORD]@aws-0-xxxx.pooler.supabase.com:5432/postgres`
4. Rewrite the scheme to use the psycopg2 driver explicitly:
   `postgresql+psycopg2://postgres.xxxxx:[PASSWORD]@aws-0-xxxx.pooler.supabase.com:5432/postgres`

### 2. Push this folder to a GitHub repo

```
git init
git add .
git commit -m "PI3 Plant Edition v0.1 prototype"
git remote add origin <your-repo-url>
git push -u origin main
```

(`.streamlit/secrets.toml.example` is safe to commit. Never commit a real
`secrets.toml`.)

### 3. Deploy on Streamlit Community Cloud

1. Go to share.streamlit.io and create a new app from your repo, branch
   `main`, main file `app.py`.
2. In the app's **Settings > Secrets**, paste the contents of
   `.streamlit/secrets.toml.example`, filled in with your real Supabase
   connection string and real user accounts (see below).
3. Deploy. The app will create all tables automatically on first load
   (`init_db()` runs on every page).

### 4. Users, companies, and subscriptions

User accounts are database-backed (hashed passwords, optional validity
window), not config-file entries. On a fresh deployment with no rows yet in
the `users` table, the `[users.<name>]` blocks in Secrets still work as a
bootstrap fallback so you can log in once and create real accounts.

To set up a new customer company: log in as the platform owner (HTC) and
use **Companies** to add the tenant, **Subscription Types** to assign it a
commercial tier (user/plant limits, feature flags), and **User Accounts**
to create its first admin user. That company's own admin can then manage
their own users and any custom roles (**User Roles**) without seeing other
companies' data — plants, raw materials, and suppliers are scoped by
`company_id`; recipe/production/quality data inherits that scoping through
the plant it's keyed to. This is still not full SSO/identity management —
adequate for direct commercial deployment to a handful of customers, not
for enterprise identity federation.

### 5. Load demo data (optional, local/dev only)

The Demo Data Admin page was removed from the UI (not needed once real
customer data exists). To seed the hardness-drift/shrinkage demonstration
case from `04_PI3_Plant_Edition_Demonstration_Case.docx` into a local/dev
database, run:

```
python -c "from db import get_session, init_db; from demo_data import seed_demo_data; init_db(); print(seed_demo_data(get_session()))"
```

## Local development

```
pip install -r requirements.txt
streamlit run app.py
```

Without a `DATABASE_URL` secret or environment variable, the app falls back
to a local SQLite file (`pi3_local.db`) for convenience — do not rely on
this for the deployed app.

## Required one-time step: backfill PI3 vector store tenant tags (2026-08-02)

PI3's semantic search (file_search) is now scoped so a company only sees
its own pushed documents (Expert Notes) plus a shared general reference
library — see `ai_assistant._file_search_filters()` and
`ai_assistant.push_document_to_vector_store()`'s docstring (fixes Gate 3,
Item 21 of the Duroflex pilot readiness list). This only works for vector
store files that carry the right tags, and files pushed before this fix
don't yet carry them. Run this once, right after deploying this change,
against the real production vector store:

```
python backfill_vector_store_tenant_tags.py          # dry run - prints what would change
python backfill_vector_store_tenant_tags.py --apply   # writes the changes
```

Safe to re-run (already-tagged files are skipped). Needs `OPENAI_API_KEY`,
`PI3_VECTOR_STORE_ID`, and `DATABASE_URL` available the same way the app
itself reads them (Streamlit secrets or environment variables). Anything
the script flags as "NEEDS ATTENTION" was left untouched on purpose — see
the script's own docstring for why guessing on those would be worse than
leaving them for a human to check.

## Troubleshooting: sidebar reverts to a plain page list

Symptom: the sidebar shows a flat, alphabetical/numeric list of page names
straight from the filenames (e.g. "Plant Installation Overview", "Production
Run Trial Record") with no logo, no version number, no section headers, and
no icons — as if `app.py`'s custom navigation code doesn't exist.

This is not a code regression (check `app.py` still has `st.navigation(...,
position="hidden")` and the custom `with st.sidebar:` block first if in
doubt) — it's Streamlit Community Cloud serving a stale cached build. Fix:
open the app on share.streamlit.io, click the **⋮** menu (top right) →
**Clear cache**. A plain reboot does not always clear this; Clear cache did
(confirmed 2026-07-22, v1.6.1). Repository/branch/main-file settings are not
usually the cause if this has worked before.

## Troubleshooting: ImportError "cannot import name 'X' from 'helpers'" (or any module) after a push

Symptom: right after pushing a commit that adds a new function/name to a
shared module (`helpers.py`, `db.py`, `cascades.py`, ...), the deployed app
throws `ImportError: cannot import name 'X' from 'Y'` even though the file
on GitHub's `main` branch clearly contains that name.

This is a different failure mode from the sidebar issue above: it's not a
stale build, it's a stale **Python process**. Streamlit Community Cloud's
"pull code changes from GitHub" step (visible in the deploy log) doesn't
always restart the underlying Python process — it can just re-run the
script against modules already sitting in `sys.modules` from before the
push. **Clear cache does not fix this** (it only clears
`@st.cache_data`/`@st.cache_resource`, not Python's module cache). The fix
is a full **Reboot app** (a separate action from Clear cache, restarts the
container/process so every module is freshly imported) — confirmed
2026-07-24, v1.10.0/v1.10.1. If no distinct "Reboot app" option is visible,
delete and redeploy the app from the same repo/branch as a fallback.

Check the deploy log (Manage app → logs) for the real traceback first —
Streamlit's on-screen error message is redacted, but the log shows the
exact `ImportError` and whether a "Pulling code changes from Github" line
appears right before the failure without a following full dependency
install / "Uvicorn server started" sequence, which is the tell for this
stale-process scenario versus an actual code bug.

## Troubleshooting: sidebar reverts to a plain page list, and Clear cache / Reboot app don't fix it

Symptom looks identical to the stale-build case above, but neither Clear
cache nor a full Reboot app resolves it. Check the deploy log's dependency
install section (right after "Pulling code changes from Github") for the
actual `streamlit`/`pandas`/`pyarrow` versions installed and the Python
version the container is using.

`requirements.txt` originally left `streamlit`/`pandas`/`numpy` unbounded
(`>=` only), so Streamlit Community Cloud's `uv pip install` was free to
resolve to whatever the newest release was at deploy time. On 2026-07-31 a
routine reboot triggered a fresh dependency resolution that picked up
streamlit 1.60.0, pandas 3.0.5, and pyarrow's latest on Python 3.14. Two
real, separate things came out of that:

1. The Subscription Types page threw a hard `pyarrow.lib.ArrowTypeError`
   from a column that mixed `int` and `str` values, which pandas 3.0's
   stricter Arrow interop no longer tolerates - a genuine code bug, fixed
   regardless of dependency version.
2. The **first** fix attempt pinned `streamlit<1.60` to roll back to the
   last version this app had been tested against - which made the sidebar
   problem worse, not better. Streamlit Community Cloud forces the Python
   version (no `runtime.txt` support, and by this date its only offered
   version was 3.14 - check Advanced settings → Python version), and
   Streamlit itself only gained real Python 3.14 support in 1.60.0 (a PEP
   649 deferred-annotation-evaluation fix). Running streamlit<1.60 on a
   Python-3.14-only host silently breaks `st.navigation()`'s custom-sidebar
   routing - no traceback, it just falls back to Streamlit's default flat
   page list and default centered layout. The fix is the opposite of the
   instinct to roll back: pin streamlit **>=1.60**, not below it, and keep
   pandas <3.0 separately (verified `streamlit>=1.60,<1.61` + `pandas<3.0`
   + `pyarrow<25` resolves cleanly to streamlit 1.60.0 / pandas 2.3.3 /
   pyarrow 24.0.0 - see the comment in `requirements.txt`).

If this recurs, compare the deploy log's installed versions against these
pins - and specifically check what Python version Cloud is forcing
(Advanced settings → Python version) before assuming a lower dependency
version is the safe choice. "Newer Python forces a newer Streamlit
minimum" is the opposite of the usual "pin everything down" instinct and
is easy to get backwards under pressure, as happened here.

## Troubleshooting: app shows "Oops" on every load, deploy log full of "GZipResponder.__init__() missing 1 required keyword-only argument: 'thread_minimum_size'"

Different failure mode from the two sidebar cases above - this one crashes
every single request (the deploy log shows the Streamlit server responding
500 to its own health checks right after boot), so the app never renders
at all, not even a broken sidebar.

Cause: `streamlit==1.60.0` declares its own dependency on `starlette` as a
wide, unpinned range (`starlette<2,>=0.40.0`) - nothing in this app's own
`requirements.txt` constrained it further before 2026-08-05. On a routine
reboot, `uv pip install` resolved that range to the newest release
satisfying it, `starlette==1.4.0`. That starlette release added a new
required keyword-only argument (`thread_minimum_size`) to its internal
`GZipResponder.__init__` - and streamlit 1.60.0's own vendored subclass
(`streamlit/web/server/starlette/starlette_gzip_middleware.py`) doesn't
pass it, so every request crashes with the `TypeError` above before it
ever reaches this app's code. Confirmed by installing streamlit 1.60.0
with no other constraints in a clean venv: it resolves starlette to 1.4.0
and fails the same way, so this is squarely an upstream
streamlit/starlette incompatibility, not a bug introduced by this app.

Fix (already applied, 2026-08-05): pin `starlette==1.3.1` explicitly in
`requirements.txt` - the last release before that breaking change.
Confirmed by running streamlit 1.60.0 against starlette 1.3.1 directly:
the server boots and responds 200 to its own health check with no GZip
error. If this recurs after a future streamlit upgrade, re-check what
starlette version the new streamlit release actually needs before
re-pinning - don't just bump the pin blindly.

## What v0.1 deliberately does not do

No ERP integration, no live machine connection, no autonomous formulation
optimization, no complex billing engine (subscription tiers enforce
user/plant limits and feature flags, but there's no payment processing),
no customer complaint platform. Root-Cause Assistant and every other
PI3-touching screen never issues formulation instructions — PI3 only
surfaces historical records and hypotheses for human review.

Multi-tenancy is "shared database, `company_id` column," not a database
per customer. Plants, raw materials, and suppliers are scoped directly by
their own `company_id` column; every other operational page (production
runs, quality results, recipes, trials, approvals, similar case
retrieval, expert notes, the industrial intelligence pages, the report
page) is scoped via `tenant_scope.apply_scope()` / `run_ids_for_company()`
through the plant/recipe hierarchy it's keyed to. This retrofit pass
completed 2026-08-01 (confirmed by direct reading of `tenant_scope.py`
and every operational page) - this paragraph previously described it as
future work, which was stale by the time it was caught.
