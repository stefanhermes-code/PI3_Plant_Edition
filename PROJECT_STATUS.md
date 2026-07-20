# PI3 Plant Edition App — Project Status

Last updated: 2026-07-20

## Where things stand

The v0.1 Streamlit prototype is fully built in this folder, per the
`CharlieC_Build_Prompt_Pack_PI3_Plant_Edition` spec (all 12 screens, 16
data entities, the trial closure rule, similar case retrieval with the
advisory-language boundary, PI3/AI connectivity placeholder, and demo
data matching the hardness-drift/shrinkage demonstration case).

- App code: complete (`app.py`, `pages/`, `db.py`, `auth.py`,
  `helpers.py`, `demo_data.py`)
- `README.md`: deployment guide (Supabase + Streamlit Community Cloud)
- `.gitignore`: excludes secrets and local db/cache files
- Database: Supabase Postgres chosen as the backing store (not SQLite —
  Streamlit Cloud's filesystem isn't guaranteed persistent)
- Git: repo initialized locally in this folder, one commit made
  (`PI3 Plant Edition v0.1 internal prototype - Streamlit app`),
  `origin` set to `https://github.com/stefanhermes-code/PI3_Plant_Edition.git`

## What's NOT done yet

**The push to GitHub hasn't happened.** Two blockers were hit:

1. The Cowork sandbox's shell has no network route to github.com (same
   restriction that blocks arbitrary `pip install`), so `git push` fails
   with a proxy 403 regardless of credentials.
2. A "GitHub Integration" connector was added in Settings > Connectors
   (shows Connected there), but it hasn't surfaced as usable tools in any
   Cowork session tried so far — connectors added mid-session may need a
   brand new conversation to attach, or this particular connector may not
   extend into Cowork.

## Next step

Either:
- Run `git push -u origin main` from a terminal on your own machine
  (fastest — your own GitHub credentials already work there), or
- Start a fresh Cowork conversation and ask Claude to check whether the
  GitHub connector's tools are available yet; if so, it can push directly
  from there.

Once pushed, follow the deployment steps in `README.md` to get it running
on Streamlit Community Cloud with your Supabase connection string in
Secrets.
