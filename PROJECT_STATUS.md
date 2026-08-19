# PI3 Plant Edition App — Project Status

Last updated: 2026-08-01

## Where things stand

The app is live and under active development well beyond the original
v0.1 prototype scope (see `version.py` for the current version — 1.49.0
as of this update). It has grown to include multi-tenant company/role/
subscription support, a plant-scoped PI3 AI query agent, recipe cost and
correlation analytics, control-chart trend analysis, CSV import across
most data-entry pages, and a full audit-and-remediation pass against the
findings in `PI3_Gaps_and_Ambiguities.docx`.

- App code: `app.py`, `views/`, `db.py`, `auth.py`, `helpers.py`,
  `ai_assistant.py`, `analytics.py`, `access_control.py`,
  `tenant_scope.py`, `demo_data.py`
- `README.md`: deployment guide (Supabase + Streamlit Community Cloud),
  kept up to date as features land
- Database: Supabase Postgres (project id `aazkdsqpytjciiqtvnfj`)
- Git: repo pushed to
  `https://github.com/stefanhermes-code/PI3_Plant_Edition.git` — pushes
  happen routinely as part of the normal development workflow (see
  `git log` for the full commit history)

## What's NOT done yet

Nothing structural is blocked. Any remaining open items are tracked as
specific findings (not a general "push hasn't happened" blocker — that
was resolved long ago). Check `PI3_Gaps_and_Ambiguities.docx` for the
current list of deferred, business-decision-dependent items (e.g. the
PI3 vector-search cross-plant filter, which is documented but
intentionally not yet implemented — see the docstring on
`push_document_to_vector_store()` in `ai_assistant.py`).

## Next step

Continue working through outstanding items in
`PI3_Gaps_and_Ambiguities.docx`, or pick up whatever feature work is
next in the backlog. Deployment steps for Streamlit Community Cloud are
in `README.md`.
