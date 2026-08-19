"""One-time backfill: tag existing PI3 vector store files so the new
company-scoped file_search filter (ai_assistant._file_search_filters(),
fixed 2026-08-02 for Gate 3 Item 21 of the Duroflex pilot readiness list)
doesn't silently hide anything that was pushed before the fix shipped.

Why this is needed at all: ask_assistant() and ask_plant_question() now
pass a `filters` parameter that only returns documents tagged
company_id=<the asking company> OR shared=True (see
ai_assistant.push_document_to_vector_store's docstring for the full
history). Two kinds of existing vector store file predate that tagging
scheme and would otherwise vanish from every future search, not leak -
this app's own code change fails closed, which is the safe direction for
a data-isolation gap to fail in, but "safe" still means "missing", so this
script needs to run once, promptly, after this fix is deployed:

1. This app's own previously-pushed Expert Notes - tagged with plant_id
   only (added 2026-08-01, before company_id was ever set - see git
   history around helpers.render_save_to_expert_notes_button and
   views/20_Expert_Notes.py). This script backfills company_id onto each
   of these by looking up Plant.company_id in this app's own database -
   the exact same lookup company_id_for_plant() in helpers.py now does
   for every new push, applied retroactively here.

2. The pre-existing general reference library - customer questions, TDS
   documents, formulation science uploaded directly via the OpenAI
   dashboard/API before this app ever pushed anything, so these files
   have NO attributes at all. This script tags each of them shared=True
   so they keep appearing in every company's search results, which is
   the deliberately-intended behavior for general expertise (see the
   comment above PLANT_QUERY_SYSTEM_PROMPT in ai_assistant.py).

Idempotent and safe to re-run: a file that already has company_id or
shared=True set is left untouched (skipped, logged as "already tagged").
A file with a plant_id that no longer resolves to a Plant row is left
untouched too, but printed as NEEDS ATTENTION, so a human decides what to
do with it rather than the script guessing (defaulting a truly orphaned
file to shared=True would be a real leak, not a safe fallback, if that
plant's data was actually private).

Requires OPENAI_API_KEY and PI3_VECTOR_STORE_ID - reads them the same way
ai_assistant._get_secret() does (Streamlit secrets first, environment
variable fallback), so run this either with a real .streamlit/secrets.toml
in the current directory or with both exported as environment variables.
Also requires DATABASE_URL (same as running the app itself) to look up
Plant.company_id for step 1.

Usage: python backfill_vector_store_tenant_tags.py [--apply]
Without --apply, does a dry run: prints exactly what it would change and
touches nothing. Pass --apply to actually call the OpenAI API and write
the changes.
"""

import argparse
import os
import sys

try:
    import streamlit as st
except Exception:  # pragma: no cover - streamlit is a hard dependency of this app, but be defensive
    st = None


def _get_secret(name):
    """Same Streamlit-secrets-then-env-var fallback as ai_assistant._get_secret()."""
    if st is not None:
        try:
            if name in st.secrets:
                return st.secrets[name]
        except Exception:
            pass
    return os.environ.get(name)


def _vector_stores_api(client):
    """Same beta-namespace-then-top-level fallback as ai_assistant._vector_stores_api()."""
    vs = getattr(client.beta, "vector_stores", None)
    if vs is not None:
        return vs
    return client.vector_stores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes. Without this flag, only prints what would change.",
    )
    args = parser.parse_args()

    api_key = _get_secret("OPENAI_API_KEY")
    vector_store_id = _get_secret("PI3_VECTOR_STORE_ID")
    if not api_key or not vector_store_id:
        print("OPENAI_API_KEY and/or PI3_VECTOR_STORE_ID are not set - nothing to do.")
        sys.exit(1)

    from openai import OpenAI

    from db import ENGINE, Plant
    from sqlalchemy.orm import Session

    client = OpenAI(api_key=api_key)
    vs_files_api = _vector_stores_api(client)

    plant_company = {}
    with Session(ENGINE) as session:
        for plant in session.query(Plant).all():
            plant_company[plant.id] = plant.company_id

    print(f"Loaded {len(plant_company)} plants from the app database for company_id lookup.")
    print(f"{'APPLYING CHANGES' if args.apply else 'DRY RUN (pass --apply to write changes)'} "
          f"against vector store {vector_store_id}\n")

    tagged_shared = 0
    tagged_company = 0
    already_tagged = 0
    needs_attention = []

    files_page = vs_files_api.list(vector_store_id=vector_store_id)
    all_files = list(files_page)
    print(f"Found {len(all_files)} files in the vector store.\n")

    for vs_file in all_files:
        file_id = vs_file.id
        attributes = dict(getattr(vs_file, "attributes", None) or {})

        if "company_id" in attributes or attributes.get("shared") is True:
            already_tagged += 1
            continue

        if "plant_id" in attributes:
            plant_id = attributes["plant_id"]
            company_id = plant_company.get(plant_id)
            if company_id is None:
                needs_attention.append((file_id, attributes))
                print(f"  NEEDS ATTENTION: {file_id} has plant_id={plant_id}, which no longer "
                      f"resolves to a Plant row - left untouched. Attributes: {attributes}")
                continue
            new_attributes = {**attributes, "company_id": company_id}
            print(f"  {file_id}: plant_id={plant_id} -> tagging company_id={company_id}")
            if args.apply:
                vs_files_api.update(vector_store_id=vector_store_id, file_id=file_id, attributes=new_attributes)
            tagged_company += 1
        else:
            print(f"  {file_id}: no attributes at all -> tagging shared=True (general reference library)")
            if args.apply:
                vs_files_api.update(
                    vector_store_id=vector_store_id, file_id=file_id, attributes={"shared": True}
                )
            tagged_shared += 1

    print(
        f"\nSummary: {tagged_company} tagged with company_id, {tagged_shared} tagged shared=True, "
        f"{already_tagged} already tagged (skipped), {len(needs_attention)} need manual attention."
    )
    if not args.apply:
        print("\nThis was a dry run - nothing was changed. Re-run with --apply to write these changes.")
    if needs_attention:
        print(
            "\nFiles needing manual attention were left completely untouched (not tagged shared=True "
            "by default, since that could leak a plant's data that was meant to stay private) - "
            "decide by hand whether each one should be tagged shared=True, given a real company_id, "
            "or deleted from the vector store."
        )


if __name__ == "__main__":
    main()
