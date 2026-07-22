# PI3 Plant Edition — v0.1 internal prototype

Flexible slabstock foam expert system for HTC Global Co. Ltd. Captures and
connects recipe versions, production runs, runtime data, quality test
results, quality issues, adjustment/conclusion history, and
approvals — with a controlled advisory boundary and optional PI3/AI
connectivity add-on. Built per `CharlieC_Build_Prompt_Pack_PI3_Plant_Edition`.

This is a controlled prototype: flexible slabstock foam only, manual-entry
first with CSV/Excel import, no ERP or live machine integration, no
autonomous formulation optimization.

## Structure

- `app.py` — Dashboard (Screen 1, entry point)
- `pages/` — the remaining 11 screens (see below)
- `db.py` — SQLAlchemy models for the 16 v0.1 entities
- `auth.py` — simple role-gated login (admin / technical / viewer)
- `helpers.py` — shared UI helpers, advisory disclaimer text
- `demo_data.py` — seeds the internal demonstration case (no real client data)

## Screens

1. Dashboard (`app.py`)
2. Plant / Installation Overview
3. Product Family & Foam Grade Profile
4. Recipe Version Record
5. Production Run / Trial Record (also handles runtime data entry + CSV import)
6. Quality Test Result
7. Quality Issue
8. Adjustment & Conclusion
9. Approval & Review — the only screen that can close a trial
10. Similar Case Retrieval ("Ask PI3")
11. PI3/AI Connectivity (placeholder, disabled by default)
12. Maintenance & License Admin
13. Demo Data Admin (utility, not one of the 12 core screens)

## The one rule that can't be bypassed

A trial cannot be closed unless `conclusion`, `reuse_recommendation`,
`reviewed_by`, `approved_by`, and `date_closed` are all present. This is
enforced in `db.py` (`TrialRecord.can_close()`) and checked again in
`pages/8_Approval_Review.py` before the "Close trial" button is enabled.

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

### 4. Users and roles

Edit the `[users.<name>]` blocks in Secrets to set real usernames,
passwords, and roles (`admin`, `technical`, or `viewer`). This is a simple
internal login, not full SSO/identity management — adequate for a small
technical team prototype, not for a public-facing deployment.

### 5. Load demo data

Log in as an `admin` user and open **Demo Data Admin** in the sidebar, then
click "Load demo data". This seeds the hardness-drift/shrinkage
demonstration case from `04_PI3_Plant_Edition_Demonstration_Case.docx`.

## Local development

```
pip install -r requirements.txt
streamlit run app.py
```

Without a `DATABASE_URL` secret or environment variable, the app falls back
to a local SQLite file (`pi3_local.db`) for convenience — do not rely on
this for the deployed app.

## What v0.1 deliberately does not do

No ERP integration, no live machine connection, no autonomous formulation
optimization, no full multi-tenant SaaS, no complex billing engine, no
customer complaint platform. "Similar Case Retrieval" never issues
formulation instructions — it surfaces historical records for human
review only.
