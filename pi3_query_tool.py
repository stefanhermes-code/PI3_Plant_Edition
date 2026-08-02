"""Read-only, plant-scoped SQL tool backing PI3's free-form question
feature (see ai_assistant.ask_plant_question()).

This is the "safety net" half of the design agreed for that feature: PI3
is allowed to write its own SQL rather than being limited to a fixed menu
of pre-built questions, but every query it writes is constrained on four
independent levels before it ever touches real data:

1. Curated views, not raw tables. PI3 only ever sees v_pi3_production_runs,
   v_pi3_property_results, v_pi3_recipe_composition, v_pi3_stream_readings,
   and v_pi3_quality_issues (created directly in Supabase - see the
   project's SQL history, there is no migrations-file convention in this
   repo). These mirror the same joins analytics.py already gets right
   (Setup-vs-Finalized phases, recipe version chains, replicate handling)
   so PI3 is filtering/aggregating over pre-tested ground, not re-deriving
   multi-table joins itself on every question.

2. A dedicated, restricted Postgres role (pi3_readonly). This role has
   SELECT granted on exactly those 5 views and nothing else - no raw
   tables, no other schema, no write privileges of any kind, a 5-second
   statement_timeout set at the role level, and a connection limit of 5.
   THIS is the real enforcement boundary: even if every check in this
   file had a bug, a query that somehow referenced a raw table would
   still fail with a Postgres permission error, because this role simply
   has no grant on it. The validation below is a fast, friendly first
   pass on top of that - not a substitute for it.

3. The plant filter is injected here, in Python, wrapping whatever SELECT
   PI3 wrote - never left to the generated SQL to remember. A model
   forgetting a WHERE clause, or a malformed/adversarial question trying
   to talk PI3 into ignoring its scope, cannot produce a query that
   returns another plant's rows: the wrapper's WHERE plant_id = :plant_id
   is applied unconditionally, after the model's SQL has already been
   fixed as a subquery.

4. Single-statement, SELECT-only, allow-listed-view validation, plus a
   hard row cap - belt-and-suspenders alongside the role's own timeout/
   connection limit, so a malformed or overly broad query fails fast with
   a clear message PI3 can act on, rather than hanging or returning
   an unbounded result set.

Required secret: PI3_READONLY_DATABASE_URL - a connection string for the
pi3_readonly role (Session Pooler recommended, matching the app's main
DATABASE_URL convention - see db.py). This is a SEPARATE secret from
DATABASE_URL; the app's normal read/write connection is never used here.

5. pass_fail freshness. v_pi3_property_results (the view backing quality
   test result questions) carries a pass_fail column that mirrors
   PhysicalPropertyResult.pass_fail - a value stored once, in this app's
   own database, at the moment each result was written (see the "recompute
   live, don't trust a stored verdict" note in quality_standards.py). That
   staleness problem can't be fixed by editing this view from here, since
   the view itself lives directly in Supabase, not in this repo. Instead,
   _recompute_live_pass_fail() below overwrites pass_fail in Python, after
   the SQL has already run, using this app's own
   quality_standards.compute_pass_fail() - the exact same tolerance rules
   every other pass rate in the app uses, with no LLM arithmetic involved
   and no Supabase migration ever required when a tolerance changes.
"""

import os
import re

import streamlit as st
from sqlalchemy import create_engine, text

from quality_standards import compute_pass_fail

ALLOWED_VIEWS = (
    "v_pi3_production_runs",
    "v_pi3_property_results",
    "v_pi3_recipe_composition",
    "v_pi3_stream_readings",
    "v_pi3_quality_issues",
)

MAX_ROWS = 500

_FORBIDDEN_KEYWORDS = re.compile(
    r"(?i)\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|"
    r"call|copy|vacuum|do|set|reset|begin|commit|rollback|listen|notify)\b"
)
_TABLE_REF = re.compile(r"(?i)\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)")

_engine = None


class QueryRejected(Exception):
    """Raised when a PI3-generated query fails validation, or fails to
    execute. Callers should catch this and feed str(exc) back to the model
    as the tool's result (not as a Python exception) - it's phrased so the
    model can understand what to fix and retry, rather than being a fatal
    error for the whole conversation turn."""


def _get_secret(name):
    """Same Streamlit-secrets-then-env-var fallback used throughout this
    app (see db.py._database_url, ai_assistant.py._get_secret) - duplicated
    here rather than imported, so this module has no import dependency on
    ai_assistant.py (which imports this module the other way, to register
    the tool - avoids a circular import for a 6-line helper)."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


def is_configured():
    """True once PI3_READONLY_DATABASE_URL is set. Callers should treat a
    False here as "the free-form data query tool isn't available yet" -
    distinct from ai_assistant.is_configured() (OpenAI secrets) and
    is_enabled_for_plant() (per-plant opt-in), all three of which gate
    different things."""
    return bool(_get_secret("PI3_READONLY_DATABASE_URL"))


def _engine_instance():
    global _engine
    if _engine is None:
        url = _get_secret("PI3_READONLY_DATABASE_URL")
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def _validate_select(sql):
    """Fast, friendly validation before a query ever reaches Postgres -
    see the module docstring for why the pi3_readonly role's own grants
    are the actual security boundary, not this function. Returns the
    cleaned statement (semicolon stripped) or raises QueryRejected."""
    if not sql or not sql.strip():
        raise QueryRejected("No SQL was provided.")

    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        raise QueryRejected(
            "Only a single SQL statement is allowed. Remove any additional statements "
            "separated by semicolons and ask one question at a time."
        )
    if not re.match(r"(?is)^\s*select\b", stripped):
        raise QueryRejected("Only SELECT statements are allowed - this tool is read-only.")
    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise QueryRejected(
            "This query contains a keyword that isn't permitted here (only a plain read-only "
            "SELECT against the listed views is allowed)."
        )

    referenced = {m.lower().split(".")[-1] for m in _TABLE_REF.findall(stripped)}
    disallowed = referenced - set(ALLOWED_VIEWS)
    if disallowed:
        raise QueryRejected(
            f"This query references {', '.join(sorted(disallowed))}, which isn't one of the "
            f"views available to query. Available views: {', '.join(ALLOWED_VIEWS)}."
        )

    select_clause_match = re.search(r"(?is)^\s*select\s+(.*?)\s+from\b", stripped)
    select_clause = select_clause_match.group(1) if select_clause_match else ""
    if "*" not in select_clause and "plant_id" not in select_clause.lower():
        raise QueryRejected(
            "The SELECT list must include plant_id (or use SELECT *) - it's required to scope "
            "results to the current plant."
        )

    return stripped


def _recompute_live_pass_fail(rows):
    """Overwrites each row's pass_fail with a live verdict from
    quality_standards.compute_pass_fail(), instead of the value the
    Supabase view returned - see point 5 in the module docstring for why.
    Only touches rows that came from v_pi3_property_results with
    property_name/target_value/actual_value all selected (typically via
    SELECT * or an explicit column list that includes them); rows missing
    any of the three, or without a pass_fail column at all, are left
    exactly as returned. Mutates and returns the same list."""
    for row in rows:
        if "pass_fail" in row and {"property_name", "target_value", "actual_value"} <= row.keys():
            row["pass_fail"] = compute_pass_fail(
                row["property_name"], row["target_value"], row["actual_value"]
            )
    return rows


def run_plant_query(sql, plant_id, max_rows=MAX_ROWS):
    """Validate and execute a PI3-generated SELECT against the curated
    views, hard-scoped to exactly one plant regardless of what the query
    itself does or doesn't filter on.

    Returns (rows, executed_sql): rows is a list of plain dicts (JSON-
    serializable, safe to hand back to the model as the tool result),
    executed_sql is the exact final SQL that ran (including the injected
    plant filter and row limit) - this is what "show your work" surfaces
    to the reviewer alongside PI3's answer.

    Raises QueryRejected on any validation or execution failure. This is
    a normal, expected control-flow path (a model's first attempt at a
    query is not guaranteed to be valid) - callers feed the message back
    to the model as the tool's result so it can correct and retry, rather
    than treating this as a fatal error."""
    if plant_id is None:
        raise QueryRejected("No plant is selected - a plant-scoped query cannot run without one.")
    if not is_configured():
        raise QueryRejected("The plant-data query tool isn't configured for this deployment yet.")

    validated = _validate_select(sql)
    wrapped = (
        f"SELECT * FROM ({validated}) AS pi3_subquery "
        f"WHERE pi3_subquery.plant_id = :pi3_plant_id LIMIT :pi3_max_rows"
    )

    try:
        engine = _engine_instance()
        with engine.connect() as conn:
            result = conn.execute(
                text(wrapped), {"pi3_plant_id": plant_id, "pi3_max_rows": max_rows}
            )
            rows = [dict(row._mapping) for row in result]
            rows = _recompute_live_pass_fail(rows)
    except QueryRejected:
        raise
    except Exception as exc:
        raise QueryRejected(
            f"This query could not be run against the available views: {exc}"
        ) from exc

    return rows, wrapped
