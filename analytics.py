"""Shared data-assembly helpers for the Industrial Intelligence pages.

The real value of PI3 Plant Edition is the join that already exists in the
schema: a production run carries a recipe (version + components), a
machine, its Finalized-phase process settings, and the physical property
results / quality observations it produced - all keyed to the same
production_run_id. Every Industrial Intelligence function (Recipe
Optimization, Trend Analysis, Machine Settings vs Physical Properties
Correlation, Root-Cause Assistant, Machine Settings Optimization) starts
from that same join, so it is built once here rather than five
slightly-different copies of the same query living in each page.

Every `foam_grade_id` parameter below (see _grade_id_list) accepts either a
single foam grade's id (the original, most common case) or a list of ids -
the latter is how "analyze by foam family" works (Trend Analysis,
Machine Settings vs Physical Properties Correlation, and Machine Settings
Optimization): a product family's member grades are resolved to their ids
by helpers.analysis_unit_picker and passed through here as a list, pooling
every one of those grades' runs into the same analysis. Recipe Optimization
and Root-Cause Assistant stay single-grade-only - their sections (current
formulation/cost, version diff, run-vs-prior-run diff) are inherently about
one specific grade's recipe, not something that pools sensibly.

Note: ProductionRun deliberately has no back-populated .phases/.results
collections (see the comment on ProductionRun in db.py - it avoids a
Streamlit/SQLAlchemy deepcopy crash). Every function below queries
ProductionPhase/PhysicalPropertyResult directly by production_run_id
instead.
"""

import datetime as dt
import random
import time

import numpy as np
import pandas as pd
import streamlit as st
from scipy import stats as scipy_stats
from sqlalchemy.orm import joinedload

from db import (
    ComponentStreamReading,
    CustomerTrial,
    OptimizationTrial,
    PerformanceLog,
    PhysicalPropertyResult,
    ProductionPhase,
    ProductionRun,
    RawMaterial,
    RecipeVersion,
)
from quality_standards import compute_pass_fail


def compute_runtime_output(phase, foam_grade):
    """Derives the physical output of a production run (added 2026-08-05
    per user request; moved here from pages/4_Production_Run_Trial_Record.py
    on 2026-08-05 so the Overview page's meters/kg-produced KPIs can share
    the exact same math as the Runtime Data tab's own calculated-output
    display, instead of drifting apart as two copies) from whichever of
    meters-produced / conveyor-speed / recorded start-end time is actually
    known. Length and runtime are two ways of knowing the same thing
    (length = speed x time), so this fills in whichever one is missing
    rather than requiring both:
    - meters_produced entered on the phase -> that IS the length; runtime is
      only shown (by the caller) as an implied cross-check against the
      recorded start/end times, never written back over them.
    - meters_produced left blank -> length is calculated instead from
      conveyor speed x the recorded start/end duration.
    Tunnel width (phase.sidewall_width_mm - renamed "Tunnel width" in the UI
    2026-08-05, since "sidewall width" wasn't a term anyone recognized;
    the column itself is unchanged) x foam height x length gives the
    produced volume (m3); volume x the foam grade's target density gives
    the produced weight (kg). Returns a dict of None-safe display values;
    never raises."""
    result = {
        "length_m": None, "length_source": None,
        "actual_duration_min": None, "implied_duration_min": None,
        "volume_m3": None, "weight_kg": None,
    }
    if phase is None:
        return result

    if phase.phase_start and phase.phase_end and phase.phase_end > phase.phase_start:
        result["actual_duration_min"] = (phase.phase_end - phase.phase_start).total_seconds() / 60.0

    speed = phase.conveyor_speed or None  # m/min

    if phase.meters_produced:
        result["length_m"] = phase.meters_produced
        result["length_source"] = "entered"
        if speed:
            result["implied_duration_min"] = phase.meters_produced / speed
    elif speed and result["actual_duration_min"] is not None:
        result["length_m"] = speed * result["actual_duration_min"]
        result["length_source"] = "calculated"

    if result["length_m"] and phase.sidewall_width_mm and phase.foam_height_mm:
        width_m = phase.sidewall_width_mm / 1000.0
        height_m = phase.foam_height_mm / 1000.0
        result["volume_m3"] = width_m * height_m * result["length_m"]
        density = foam_grade.target_density if foam_grade else None
        if density and result["volume_m3"] is not None:
            result["weight_kg"] = result["volume_m3"] * density

    return result


def _log_performance(_session, function_name, foam_grade_id, property_name, duration_ms, row_count):
    """Records one PerformanceLog row (see db.py) for a cache-MISS call to
    one of the three functions below - added 2026-08-02 in response to a
    reported "app feels slow in general", so there's a real, persistent
    record of how expensive each actual database fetch was, visible on the
    Performance admin page, instead of only ever being able to guess.

    Best-effort only and must never be able to break a page: any failure
    (including this table not existing yet on some environment that hasn't
    picked up this migration) is swallowed silently rather than surfaced -
    a performance-monitoring feature must never be the thing that takes an
    Intelligence page down. Also does cheap, infrequent housekeeping (a ~2%
    chance per call of trimming rows older than 30 days) so this table
    doesn't grow unbounded on a deployment nobody manually tidies."""
    try:
        grade_ids = _grade_id_list(foam_grade_id)
        _session.add(
            PerformanceLog(
                function_name=function_name,
                grade_ids=",".join(str(g) for g in grade_ids) if grade_ids else None,
                property_name=property_name,
                row_count=row_count,
                duration_ms=round(duration_ms, 2),
            )
        )
        _session.commit()
        if random.random() < 0.02:
            cutoff = dt.datetime.utcnow() - dt.timedelta(days=30)
            _session.query(PerformanceLog).filter(PerformanceLog.created_at < cutoff).delete()
            _session.commit()
    except Exception:
        _session.rollback()

# Cache TTL for the three DB-loading functions below (run_settings_dataframe,
# property_results_dataframe, actual_usage_dataframe). Fixed 2026-08-02: these
# were being re-queried from scratch on every Streamlit rerun - which happens
# on every widget click on a page, not just on navigation - and each one is
# used by every Industrial Intelligence page, several of them multiple times
# per rerun (once to check which grades have data for a dropdown, again for
# the selected grade, again inside ranking/correlation helpers). Caching them
# turns most of those repeat calls within a single page visit into a
# dict-lookup instead of a round trip to Supabase. 30s is short enough that
# entering new data and immediately switching to an analysis page won't look
# stale for long, but long enough to absorb the burst of reruns a reviewer
# generates while adjusting selectboxes on one page. Keyed only on
# foam_grade_id/property_name (the session argument is prefixed with `_` so
# Streamlit doesn't try to hash the SQLAlchemy Session object itself) - safe
# across users because a foam_grade_id already scopes to one company, so
# there's no cross-tenant leak risk in sharing this cache.
_DATA_CACHE_TTL = 30


def _grade_id_list(foam_grade_id):
    """Every function below that takes `foam_grade_id` accepts either a
    single id (the original "analyze one foam grade" case) or a list/tuple
    of ids (analyzing a whole foam family - a product family's grades
    pooled together, see analysis_unit_picker in helpers.py). Normalizes
    both shapes to a list (or None) so the caller can always filter with
    `.in_()`. None/empty means "no grade filter" (every grade)."""
    if foam_grade_id is None:
        return None
    if isinstance(foam_grade_id, (list, tuple, set)):
        return list(foam_grade_id) or None
    return [foam_grade_id]

# Machine/process settings captured per phase (see ProductionPhase in
# db.py) - deliberately CONTROLLABLE inputs only, not measured outcomes.
# These are the fields every process-vs-quality analysis works from: the
# "Machine Settings vs Physical Properties Correlation" and "Machine
# Settings Optimization" pages both present this list as "the settings you
# can act on", so a field that isn't actually something an operator sets
# has no business appearing in either ranking - it would read as if
# adjusting an outcome were the lever, which it never is.
#
# foam_height_mm, ambient_temperature_c, ambient_humidity_pct, and
# rise_time were removed from this list on 2026-08-05 (a user correctly
# spotted foam_height_mm still showing up in the correlation/optimization
# rankings as if it were a "process setting"): all 4 are measured outcomes
# of the foaming process, not configured inputs - see db.py's
# ProductionPhase docstrings, and the 2026-08-03 decision that already
# removed these same 4 fields from the Setup tab for exactly this reason
# (foam_height_mm/ambient_* "RETIRED FROM THE SETUP TAB... a measured
# outcome, not something planned"). That Setup-tab fix never propagated
# back to this list, which is why the correlation/optimization pages kept
# ranking them as settings for two more days. They're still readable
# directly off ProductionPhase wherever a page genuinely wants the
# observed outcome (e.g. Trend Analysis, or the Runtime Data tab's own
# display) - just no longer treated as a lever to "optimize".
PHASE_SETTING_FIELDS = [
    "mixer_rpm",
    "conveyor_speed",
    "air_injection_rate",
    "air_pressure_bar",
    "ratio_index",
    "sidewall_width_mm",
    "top_flat_system_used",
]

PHASE_SETTING_LABELS = {
    "mixer_rpm": "Mixer rpm",
    "conveyor_speed": "Conveyor speed (m/min)",
    "air_injection_rate": "Air injection rate",
    "air_pressure_bar": "Air pressure (bar)",
    "ratio_index": "Ratio / index",
    "sidewall_width_mm": "Tunnel width (mm)",
    "top_flat_system_used": "Top-flat system in use",
}

# Yes/No fields in the list above - added 2026-08-03 (top_flat_system_used
# has a direct impact on block geometry, per user feedback, so it belongs
# in the same correlation/optimization pipeline as the continuous settings).
# Every consumer of PHASE_SETTING_FIELDS still treats these as numeric
# (0.0/1.0/NaN - see run_settings_dataframe below), so correlation (a valid
# point-biserial correlation) and quantile bucketing both work unmodified;
# this set exists only so the bucket-range label can read "Yes"/"No"
# instead of the literal "0-0"/"1-1" a raw min/max format would produce.
BOOLEAN_SETTING_FIELDS = {"top_flat_system_used"}


def format_setting_range(field, series):
    """Human-readable label for one qcut bucket's range of a given setting
    field - "Yes"/"No" for boolean fields (see BOOLEAN_SETTING_FIELDS),
    "{min}-{max}" for every continuous field. Shared by
    rank_setting_optimization below and the Machine Settings Optimization
    page's own drill-down bucketing, so the two never disagree on how a
    bucket is labeled."""
    if field in BOOLEAN_SETTING_FIELDS:
        return "Yes" if series.max() >= 0.5 else "No"
    return f"{series.min():g}–{series.max():g}"


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def run_settings_dataframe(_session, foam_grade_id=None):
    """One row per production run: identifying info (grade, recipe version,
    machine) plus its Finalized-phase process settings (falls back to the
    Setup phase if no Finalized phase has been recorded yet for that run).
    `foam_grade_id` accepts a single id or a list of ids (a foam family's
    grades pooled together) - see _grade_id_list above.

    Cached (see _DATA_CACHE_TTL) and eager-loads foam_grade/recipe_version/
    machine plus all of this batch of runs' ProductionPhase rows in one
    query each - fixed 2026-08-02, previously issued one query per run just
    for its phases (N+1), which got slower as more production runs were
    recorded. The `_session` parameter name (leading underscore) tells
    Streamlit's cache not to try to hash the SQLAlchemy Session object -
    the cache key is just foam_grade_id. Logs its own duration to
    PerformanceLog on every call (see _log_performance) - this function
    only runs at all on a cache miss, so every logged row here is real
    database work, not a cache hit.
    """
    _t0 = time.perf_counter()
    q = _session.query(ProductionRun).options(
        joinedload(ProductionRun.foam_grade),
        joinedload(ProductionRun.recipe_version),
        joinedload(ProductionRun.machine),
    )
    grade_ids = _grade_id_list(foam_grade_id)
    if grade_ids:
        q = q.filter(ProductionRun.foam_grade_id.in_(grade_ids))
    runs = q.order_by(ProductionRun.run_date).all()

    run_ids = [run.id for run in runs]
    phases_by_run = {}
    if run_ids:
        all_phases = (
            _session.query(ProductionPhase)
            .filter(ProductionPhase.production_run_id.in_(run_ids))
            .all()
        )
        for p in all_phases:
            phases_by_run.setdefault(p.production_run_id, {})[p.phase_name] = p

    rows = []
    for run in runs:
        by_name = phases_by_run.get(run.id, {})
        phase = by_name.get("Finalized") or by_name.get("Setup")

        row = {
            "run_id": run.id,
            "run_date": run.run_date,
            "foam_grade_id": run.foam_grade_id,
            "foam_grade": run.foam_grade.grade_name if run.foam_grade else None,
            "recipe_version_id": run.recipe_version_id,
            "recipe_version": run.recipe_version.version_label if run.recipe_version else None,
            "machine_id": run.machine_id,
            "machine": run.machine.name if run.machine else None,
        }
        for field in PHASE_SETTING_FIELDS:
            if field == "ratio_index":
                # Recipe-level formulation constant since 2026-08-03 (see
                # RecipeVersion.ratio_index in db.py), not a per-phase
                # setting - sourced from the run's recipe version instead of
                # getattr(phase, ...) like every other field here.
                row[field] = run.recipe_version.ratio_index if run.recipe_version else None
            elif field in BOOLEAN_SETTING_FIELDS:
                # Coerced to 0.0/1.0/NaN (not left as True/False/None) right
                # here - the single point every downstream consumer (.corr(),
                # pd.qcut, reports.py's getattr loop, Root-Cause Assistant's
                # pct-change comparison) reads from, so none of them need
                # their own True/False handling.
                raw = getattr(phase, field) if phase else None
                row[field] = (1.0 if raw else 0.0) if raw is not None else None
            else:
                row[field] = getattr(phase, field) if phase else None
        rows.append(row)

    df = pd.DataFrame(rows)
    _log_performance(_session, "run_settings_dataframe", foam_grade_id, None, (time.perf_counter() - _t0) * 1000, len(df))
    return df


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def property_results_dataframe(_session, foam_grade_id=None, property_name=None, include_trials=False):
    """One row per physical property result, joined with the run's grade,
    recipe version, and machine - the base table for trend/correlation
    work. `foam_grade_id` accepts a single id or a list of ids (a foam
    family's grades pooled together).

    pass_fail is recomputed live via quality_standards.compute_pass_fail()
    rather than read from the result's stored pass_fail column - a stored
    verdict only reflects whatever tolerance rule was in effect at the
    moment it was written, so every consumer of this dataframe (Recipe
    Optimization, Trend Analysis, Process-Property Correlation, Root-Cause
    Assistant, Machine Settings Optimization) needs the current rule, not
    a historical snapshot of it.

    `include_trials` (default False - production-run-only, matching every
    caller's behavior before 2026-08-03) additionally unions in results
    sourced from CustomerTrial/OptimizationTrial (see
    db.SAMPLE_SOURCE_TYPES) - the two independent lab-trial flows that have
    no ProductionPhase/machine behind them. Only Trend Analysis and Recipe
    Optimization ever pass True for this - Process-Property Correlation,
    Root-Cause Assistant, and Machine Settings Optimization are keyed on
    PHASE_SETTING_FIELDS (machine/process settings) that simply don't
    exist for a lab trial, so they stay production-run-only unconditionally
    rather than accepting this parameter. Trial-sourced rows carry
    run_id=None, run_date=None, machine=None, and a "source" column
    ("Production Run" / "Customer Trial" / "Optimization Trial") so callers
    can tell the two apart when it matters (e.g. excluding trial rows from
    anything that assumes a real run_date for time-ordering).

    Cached (see _DATA_CACHE_TTL) and eager-loads each result's production
    run plus that run's foam_grade/recipe_version/machine in the same
    query - fixed 2026-08-02, previously touched each of those 3
    relationships as a separate lazy-loaded query PER RESULT ROW (roughly
    1 + 4x the number of results), which is the main reason this got
    slower as more quality data was recorded. The `_session` parameter
    name (leading underscore) tells Streamlit's cache not to try to hash
    the SQLAlchemy Session object - the cache key is just foam_grade_id/
    property_name/include_trials. Logs its own duration to PerformanceLog
    on every call (see _log_performance) - only runs at all on a cache
    miss."""
    _t0 = time.perf_counter()
    run_load = joinedload(PhysicalPropertyResult.production_run)
    q = (
        _session.query(PhysicalPropertyResult)
        .join(ProductionRun)
        .options(
            run_load.joinedload(ProductionRun.foam_grade),
            run_load.joinedload(ProductionRun.recipe_version),
            run_load.joinedload(ProductionRun.machine),
        )
    )
    grade_ids = _grade_id_list(foam_grade_id)
    if grade_ids:
        q = q.filter(ProductionRun.foam_grade_id.in_(grade_ids))
    if property_name:
        q = q.filter(PhysicalPropertyResult.property_name == property_name)
    results = q.all()

    rows = []
    for r in results:
        run = r.production_run
        if run is None:
            continue
        rows.append(
            {
                "result_id": r.id,
                "source": "Production Run",
                "run_id": run.id,
                "trial_id": None,
                "run_date": run.run_date,
                "foam_grade_id": run.foam_grade_id,
                "foam_grade": run.foam_grade.grade_name if run.foam_grade else None,
                "recipe_version_id": run.recipe_version_id,
                "recipe_version": run.recipe_version.version_label if run.recipe_version else None,
                "machine_id": run.machine_id,
                "machine": run.machine.name if run.machine else None,
                "property_name": r.property_name,
                "target_value": r.target_value,
                "actual_value": r.actual_value,
                "unit": r.unit,
                "pass_fail": compute_pass_fail(r.property_name, r.target_value, r.actual_value),
                "tested_at": r.tested_at,
            }
        )

    if include_trials:
        for source_label, trial_model, fk_col in (
            ("Customer Trial", CustomerTrial, PhysicalPropertyResult.customer_trial_id),
            ("Optimization Trial", OptimizationTrial, PhysicalPropertyResult.optimization_trial_id),
        ):
            trial_load = joinedload(getattr(PhysicalPropertyResult, "customer_trial" if trial_model is CustomerTrial else "optimization_trial"))
            tq = (
                _session.query(PhysicalPropertyResult)
                .join(trial_model, fk_col == trial_model.id)
                .options(
                    trial_load.joinedload(trial_model.foam_grade),
                    trial_load.joinedload(trial_model.recipe_version),
                )
            )
            if grade_ids:
                tq = tq.filter(trial_model.foam_grade_id.in_(grade_ids))
            if property_name:
                tq = tq.filter(PhysicalPropertyResult.property_name == property_name)
            for r in tq.all():
                trial = r.customer_trial if trial_model is CustomerTrial else r.optimization_trial
                if trial is None:
                    continue
                rows.append(
                    {
                        "result_id": r.id,
                        "source": source_label,
                        "run_id": None,
                        "trial_id": trial.id,
                        "run_date": trial.trial_date,
                        "foam_grade_id": trial.foam_grade_id,
                        "foam_grade": trial.foam_grade.grade_name if trial.foam_grade else None,
                        "recipe_version_id": trial.recipe_version_id,
                        "recipe_version": trial.recipe_version.version_label if trial.recipe_version else None,
                        "machine_id": None,
                        "machine": None,
                        "property_name": r.property_name,
                        "target_value": r.target_value,
                        "actual_value": r.actual_value,
                        "unit": r.unit,
                        "pass_fail": compute_pass_fail(r.property_name, r.target_value, r.actual_value),
                        "tested_at": r.tested_at,
                    }
                )

    df = pd.DataFrame(rows)
    _log_performance(
        _session, "property_results_dataframe", foam_grade_id, property_name,
        (time.perf_counter() - _t0) * 1000, len(df),
    )
    return df


def pass_rate(series) -> float | None:
    """Share of non-null Pass/Fail values that are 'Pass', or None if there
    is nothing to compute from."""
    known = series.dropna()
    if known.empty:
        return None
    return round((known == "Pass").sum() / len(known), 3)


def _safe_int(value):
    """int(value), or None if value is missing/NaN - a lab-trial-sourced row
    in an SPC series (see property_run_series's include_trials) has no
    run_id, and int(nan) raises rather than producing something usable, so
    every SPC function below that surfaces a flagged point's run id goes
    through this instead of a bare int() cast."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return int(value)


def normalize_to_pct_of_target(df):
    """Re-expresses a dataframe's actual_value/target_value columns on a
    "percent of target" basis - target becomes 100 for every row,
    actual_value becomes (actual/target)*100 - instead of the property's
    own raw unit. This is what makes pooling multiple foam grades into one
    analysis (see helpers.analysis_unit_picker's "foam family" mode)
    statistically sound rather than misleading: two grades of the same
    property (e.g. two different density grades) can have very different
    target values, so a control chart, capability index, or raw
    setting-vs-actual_value correlation built from RAW values would read a
    plain grade-to-grade target difference as a false shift/drift/
    correlation. Building on % of target instead makes every run's target
    identical, so pooling grades together no longer confounds "which grade
    is this run" with the thing actually being measured.

    Only ever applied in foam-family mode - single-grade analyses keep
    using each property's own raw unit/scale exactly as before (this
    function is never called for foam_grade_id being a single id), so
    existing single-grade numbers are completely unaffected by this.

    Rows with a missing or zero target_value are dropped (nothing to
    normalize against). The original raw values are kept under
    _raw_actual_value/_raw_target_value in case a caller wants to still
    display/export the real unit alongside the normalized one. Returns a
    copy; safe to call on an empty dataframe (returns it unchanged)."""
    if df.empty:
        return df
    out = df.dropna(subset=["target_value"])
    out = out[out["target_value"] != 0].copy()
    if out.empty:
        return out
    out["_raw_actual_value"] = out["actual_value"]
    out["_raw_target_value"] = out["target_value"]
    out["actual_value"] = (out["actual_value"] / out["target_value"]) * 100
    out["target_value"] = 100.0
    return out.reset_index(drop=True)


def merged_run_property_dataframe(session, foam_grade_id, property_name, normalize_pct_of_target=False):
    """One row per production run for a given grade/property: process
    settings joined to that run's mean result for the chosen property.
    Used by Machine Settings vs Physical Properties Correlation and
    Machine Settings Optimization, which both need "one settings snapshot"
    per "one quality outcome". `normalize_pct_of_target=True` re-expresses
    actual_value/target_value as percent-of-target before returning (see
    normalize_to_pct_of_target) - pass this when foam_grade_id is a foam
    family's list of grade ids, since those grades can have different
    target values for the same property."""
    settings_df = run_settings_dataframe(session, foam_grade_id=foam_grade_id)
    results_df = property_results_dataframe(session, foam_grade_id=foam_grade_id, property_name=property_name)
    if settings_df.empty or results_df.empty:
        return pd.DataFrame()

    per_run_result = (
        results_df.groupby("run_id")
        .agg(actual_value=("actual_value", "mean"), target_value=("target_value", "mean"))
        .reset_index()
    )
    merged = settings_df.merge(per_run_result, on="run_id", how="inner")
    if normalize_pct_of_target:
        merged = normalize_to_pct_of_target(merged)
    return merged


def rank_setting_correlations(session, foam_grade_id, property_name, normalize_pct_of_target=False):
    """For EVERY process setting at once, compute its correlation with the
    chosen property's actual value across this grade's runs, ranked by
    |correlation| descending. This is the difference between "intelligence"
    and "a graph you have to already know where to point": instead of
    picking one setting and hoping it's the relevant one, the reviewer sees
    immediately which of the 7 settings actually moves this property, and
    by how much, before drilling into any single scatter plot.

    `normalize_pct_of_target` - see merged_run_property_dataframe - pass
    True when pooling a foam family's grades together."""
    merged = merged_run_property_dataframe(
        session, foam_grade_id, property_name, normalize_pct_of_target=normalize_pct_of_target
    )
    rows = []
    for field in PHASE_SETTING_FIELDS:
        if merged.empty:
            sub = merged
        else:
            sub = merged.dropna(subset=[field, "actual_value"])
        n = len(sub)
        corr = round(sub[field].corr(sub["actual_value"]), 3) if n >= 3 else None
        rows.append({"field": field, "label": PHASE_SETTING_LABELS.get(field, field), "n": n, "correlation": corr})
    ranked = pd.DataFrame(rows)
    # Every field is appended above regardless of whether a correlation could
    # be computed (unlike rank_component_actual_correlations etc., which
    # skip uncomputable rows entirely) - so if every setting has too few points,
    # "correlation" ends up an all-None object-dtype column, and pandas'
    # .abs() raises TypeError on that (bad operand type for abs(): 'NoneType')
    # rather than treating it as NaN. pd.to_numeric coerces None/object-None
    # to a proper float NaN first, which .abs() (and na_position="last"
    # below) handle correctly regardless of how many rows have no value yet.
    ranked["_abs"] = pd.to_numeric(ranked["correlation"], errors="coerce").abs()
    ranked = ranked.sort_values("_abs", ascending=False, na_position="last").drop(columns=["_abs"]).reset_index(drop=True)
    return ranked


def rank_setting_optimization(session, foam_grade_id, property_name, normalize_pct_of_target=False):
    """For EVERY process setting, bucket its values into Low/Medium/High (or
    Low/High) ranges and measure the gap between the best- and
    worst-performing range's average absolute deviation from target. A
    bigger gap means that setting more clearly separates good outcomes from
    bad ones for this grade/property - ranked so the most actionable
    setting surfaces first, instead of the reviewer checking each of the 7
    settings one at a time to find out which one matters.

    `normalize_pct_of_target` - see merged_run_property_dataframe. Note this
    function's own deviation_pct math below is already scale-invariant
    under that normalization ((actual-target)/target is unchanged whether
    actual/target are raw or already expressed as percent-of-target), so
    passing True here only matters for keeping this function's INPUT
    consistent with rank_setting_correlations' when both are shown
    side-by-side for the same foam-family selection - it does not change
    this function's own ranking numbers."""
    merged = merged_run_property_dataframe(
        session, foam_grade_id, property_name, normalize_pct_of_target=normalize_pct_of_target
    )
    rows = []
    for field in PHASE_SETTING_FIELDS:
        label = PHASE_SETTING_LABELS.get(field, field)
        empty_row = {
            "field": field, "label": label, "n": 0,
            "best_range": None, "best_range_setting": None,
            "best_range_avg_dev_pct": None, "spread_pct": None,
        }
        if merged.empty:
            rows.append(empty_row)
            continue
        sub = merged.dropna(subset=[field, "actual_value"]).copy()
        if len(sub) < 3:
            empty_row["n"] = len(sub)
            rows.append(empty_row)
            continue

        sub["deviation_pct"] = ((sub["actual_value"] - sub["target_value"]) / sub["target_value"]).abs()
        sub.loc[sub["target_value"].isna() | (sub["target_value"] == 0), "deviation_pct"] = float("nan")

        range_col = None
        if field in BOOLEAN_SETTING_FIELDS:
            # A strictly 0/1 field is a group comparison, not a quantile
            # split - pd.qcut is the wrong tool here regardless, and
            # actively fails (raises ValueError under duplicates="drop")
            # for the skewed splits a Yes/No setting produces in practice
            # (e.g. 5 Yes vs 2 No), which would otherwise fall through to
            # the "not enough variation" empty row even though a clean
            # two-group comparison exists. Map directly to Yes/No instead.
            range_col = sub[field].map({1.0: "Yes", 0.0: "No"})
        else:
            for q, labels in ((3, ["Low", "Medium", "High"]), (2, ["Low", "High"])):
                try:
                    range_col = pd.qcut(sub[field], q=q, labels=labels, duplicates="drop")
                    break
                except ValueError:
                    continue
        if range_col is None or range_col.nunique(dropna=True) < 2:
            empty_row["n"] = len(sub)
            rows.append(empty_row)
            continue

        sub["range"] = range_col
        summary = (
            sub.groupby("range", observed=True)
            .agg(avg_dev=("deviation_pct", "mean"), setting_range=(field, lambda s: format_setting_range(field, s)))
            .dropna(subset=["avg_dev"])
        )
        if summary.empty:
            empty_row["n"] = len(sub)
            rows.append(empty_row)
            continue

        summary = summary.sort_values("avg_dev")
        best, worst = summary.iloc[0], summary.iloc[-1]
        rows.append(
            {
                "field": field,
                "label": label,
                "n": len(sub),
                "best_range": summary.index[0],
                "best_range_setting": best["setting_range"],
                "best_range_avg_dev_pct": round(best["avg_dev"] * 100, 1),
                "spread_pct": round((worst["avg_dev"] - best["avg_dev"]) * 100, 1),
            }
        )
    ranked = pd.DataFrame(rows)
    ranked = ranked.sort_values("spread_pct", ascending=False, na_position="last").reset_index(drop=True)
    return ranked


# ---------------------------------------------------------------------------
# Recipe Optimization: cost, version diff, component-level correlation
# ---------------------------------------------------------------------------
# These three functions are what turn "a table of ingredients" and "a table
# of quality outcomes" into an actual optimization view: what does this
# formulation cost, what specifically changed between two versions, and
# which raw material's dosage is actually associated with which property -
# instead of leaving the reviewer to eyeball two ingredient lists and a
# results table side by side.


def _resolve_component_cost_per_kg(session, component, raw_material_cache):
    """Cost/kg for a recipe component: prefer the linked RawMaterial (by
    raw_material_id), fall back to a case-insensitive name match against
    the Raw Materials master list (covers components entered as free text
    before a matching master record existed). Returns None if no cost is
    recorded anywhere for this material - callers must treat that as
    "unknown", never as zero."""
    if component.raw_material_id and component.raw_material_id in raw_material_cache:
        rm = raw_material_cache[component.raw_material_id]
        if rm and rm.cost_per_kg is not None:
            return rm.cost_per_kg
    name_key = (component.raw_material_name or "").strip().lower()
    for rm in raw_material_cache.values():
        if rm and rm.name.strip().lower() == name_key and rm.cost_per_kg is not None:
            return rm.cost_per_kg
    return None


def recipe_version_cost(session, recipe_version):
    """Formulation cost for one recipe version, in cost per 100 parts (the
    standard php-based costing convention: sum of each component's php x
    its cost/kg, since php already expresses each material as parts per
    hundred of the base polyol). Returns a dict:
    - total_cost: float, or None if NO component has cost data at all
    - priced_php / total_php: how much of the formulation (by php) is
      actually covered by known costs, so a partial total can be flagged
    - missing: list of raw material names with no cost recorded
    Never fabricates a cost for an unpriced material - a formulation with
    missing prices gets an honest partial total, not a silently wrong one.
    """
    raw_material_cache = {rm.id: rm for rm in session.query(RawMaterial).all()}
    total_cost = 0.0
    priced_php = 0.0
    total_php = 0.0
    missing = []
    any_priced = False

    for c in recipe_version.components:
        php = c.php or 0.0
        total_php += php
        cost_per_kg = _resolve_component_cost_per_kg(session, c, raw_material_cache)
        if cost_per_kg is None:
            missing.append(c.raw_material_name)
            continue
        any_priced = True
        total_cost += php * cost_per_kg
        priced_php += php

    return {
        "total_cost": round(total_cost, 4) if any_priced else None,
        "priced_php": round(priced_php, 2),
        "total_php": round(total_php, 2),
        "missing": missing,
        "complete": not missing,
    }


def recipe_version_diff(version_a, version_b):
    """Component-by-component diff between two recipe versions of the same
    foam grade: for every raw material appearing in either version, its php
    in each, the change, and whether it's new/removed/unchanged. This is
    the same "what actually changed" question Root-Cause Assistant answers
    for process settings between two production runs - applied to
    formulation instead, since today the only way to compare two versions
    is to read both ingredient lists by eye."""
    a_by_name = {c.raw_material_name.strip().lower(): c for c in version_a.components}
    b_by_name = {c.raw_material_name.strip().lower(): c for c in version_b.components}
    all_keys = sorted(set(a_by_name) | set(b_by_name))

    rows = []
    for key in all_keys:
        ca, cb = a_by_name.get(key), b_by_name.get(key)
        php_a = ca.php if ca else None
        php_b = cb.php if cb else None
        name = (ca or cb).raw_material_name
        role = (cb or ca).role_in_formulation
        if php_a is None:
            status, delta, delta_pct = "Added", php_b, None
        elif php_b is None:
            status, delta, delta_pct = "Removed", -php_a, None
        else:
            delta = round(php_b - php_a, 3)
            delta_pct = round((delta / php_a) * 100, 1) if php_a else None
            status = "Unchanged" if abs(delta) < 1e-9 else "Changed"
        rows.append(
            {
                "raw_material_name": name,
                "role": role,
                "php_a": php_a,
                "php_b": php_b,
                "delta": delta,
                "delta_pct": delta_pct,
                "status": status,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        status_order = {"Added": 0, "Removed": 1, "Changed": 2, "Unchanged": 3}
        df["_order"] = df["status"].map(status_order)
        df = df.sort_values(["_order", "raw_material_name"]).drop(columns=["_order"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Actual (metered) usage vs. outcome.
# ---------------------------------------------------------------------------
# A recipe version's php is a target, not a measurement: the same recipe
# version, run a hundred times, does not meter out the exact same dosage of
# every material every time - that is what the flow meters on
# ComponentStreamReading exist to capture. Correlating a raw material's
# PLANNED php across recipe versions against outcomes was tried here and
# deliberately removed - the reviewer for this app judged it not a
# meaningful analysis (recipe versions replace each other rather than
# forming a comparable series, so "does changing the planned formulation
# matter" isn't a question worth ranking materials by). The functions below
# ask the question a plant running one settled recipe actually needs
# answered instead: "does this run's ACTUAL metered dosage of each material
# line up with this run's actual outcome" - with n = number of production
# runs, not number of recipe versions, so it works even for a grade with a
# single recipe version that has simply been run (and metered, and tested)
# many times.


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def actual_usage_dataframe(_session, foam_grade_id=None):
    """One row per (production run, raw-material stream): that stream's
    actual delivered quantity for the run's Finalized phase, re-expressed as
    an actual-php-equivalent using the run's own Base-polyol stream reading
    as the 100-parts basis - the same convention every planned recipe uses,
    computed here from what the flow meters actually measured for that one
    batch instead of from the recipe. Runs with no Finalized-phase stream
    readings, or with no identifiable Base-polyol reading to normalize
    against, are skipped rather than guessed at. `foam_grade_id` accepts a
    single id or a list of ids (a foam family's grades pooled together).

    Cached (see _DATA_CACHE_TTL) and batch-loads this grade's Finalized
    phases and their stream readings in two queries total instead of one
    query per run plus one query per phase - fixed 2026-08-02, same N+1
    pattern as run_settings_dataframe/property_results_dataframe above.
    Logs its own duration to PerformanceLog on every call (see
    _log_performance) - only runs at all on a cache miss."""
    _t0 = time.perf_counter()
    q = _session.query(ProductionRun).options(
        joinedload(ProductionRun.recipe_version).joinedload(RecipeVersion.components)
    )
    grade_ids = _grade_id_list(foam_grade_id)
    if grade_ids:
        q = q.filter(ProductionRun.foam_grade_id.in_(grade_ids))
    runs = q.all()

    run_ids = [run.id for run in runs]
    phase_by_run = {}
    if run_ids:
        phases = (
            _session.query(ProductionPhase)
            .filter(
                ProductionPhase.production_run_id.in_(run_ids),
                ProductionPhase.phase_name == "Finalized",
            )
            .all()
        )
        phase_by_run = {p.production_run_id: p for p in phases}

    readings_by_phase = {}
    phase_ids = [p.id for p in phase_by_run.values()]
    if phase_ids:
        all_readings = (
            _session.query(ComponentStreamReading)
            .filter(ComponentStreamReading.production_phase_id.in_(phase_ids))
            .all()
        )
        for r in all_readings:
            readings_by_phase.setdefault(r.production_phase_id, []).append(r)

    rows = []
    for run in runs:
        phase = phase_by_run.get(run.id)
        if phase is None:
            continue
        readings = readings_by_phase.get(phase.id, [])
        if not readings:
            continue

        recipe_version = run.recipe_version
        polyol_name = None
        if recipe_version:
            for c in recipe_version.components:
                if c.role_in_formulation and "base polyol" in c.role_in_formulation.strip().lower():
                    polyol_name = c.raw_material_name.strip().lower()
                    break
        if polyol_name is None:
            continue

        polyol_reading = next(
            (r for r in readings if r.stream_name and r.stream_name.strip().lower() == polyol_name), None
        )
        if polyol_reading is None or not polyol_reading.flow_total_qty:
            continue
        polyol_qty = polyol_reading.flow_total_qty

        for r in readings:
            if r.flow_total_qty is None:
                continue
            rows.append(
                {
                    "run_id": run.id,
                    "foam_grade_id": run.foam_grade_id,
                    "recipe_version_id": run.recipe_version_id,
                    "stream_name": r.stream_name,
                    "flow_total_qty": r.flow_total_qty,
                    "actual_php_equivalent": round((r.flow_total_qty / polyol_qty) * 100, 4),
                }
            )
    df = pd.DataFrame(rows)
    _log_performance(_session, "actual_usage_dataframe", foam_grade_id, None, (time.perf_counter() - _t0) * 1000, len(df))
    return df


def rank_component_actual_correlations(session, foam_grade_id, property_name, min_runs=3):
    """For every raw-material stream with metered readings for this grade,
    correlate its ACTUAL per-run dosage (see actual_usage_dataframe) against
    that same run's actual outcome for the chosen property, ranked by
    |correlation| descending.

    Needs real per-run variation to say anything: a material must have
    metered readings paired with a quality result for at least `min_runs`
    production runs, or it's excluded rather than shown as a misleading
    correlation. Returns an empty DataFrame if nothing qualifies -
    callers should treat that as "not enough metered/tested runs yet", not
    as "no relationship found"."""
    usage_df = actual_usage_dataframe(session, foam_grade_id=foam_grade_id)
    if usage_df.empty:
        return pd.DataFrame()

    results_df = property_results_dataframe(session, foam_grade_id=foam_grade_id, property_name=property_name)
    if results_df.empty:
        return pd.DataFrame()
    per_run_result = results_df.groupby("run_id")["actual_value"].mean()

    rows = []
    for material, sub in usage_df.groupby("stream_name"):
        php_series = sub.set_index("run_id")["actual_php_equivalent"]
        outcome_series = per_run_result.reindex(php_series.index)
        paired = pd.DataFrame({"php": php_series, "outcome": outcome_series}).dropna()
        n = len(paired)
        if n < min_runs:
            continue
        corr = paired["php"].corr(paired["outcome"])
        if pd.isna(corr):
            continue
        rows.append({"raw_material_name": material, "n_runs": n, "correlation": round(corr, 3)})

    ranked = pd.DataFrame(rows)
    if not ranked.empty:
        ranked["_abs"] = ranked["correlation"].abs()
        ranked = ranked.sort_values("_abs", ascending=False).drop(columns=["_abs"]).reset_index(drop=True)
    return ranked


# ---------------------------------------------------------------------------
# Trend Analysis: statistical process control, not just a line chart
# ---------------------------------------------------------------------------
# A plot of actual-vs-target over time cannot, by itself, tell a reviewer
# whether a wobble is real or just noise, or when a real shift actually
# started. That is what statistical process control (SPC) exists for. The
# four functions below are the standard SPC toolkit for exactly this kind of
# data (one measurement per production run, tracked over time against a
# target): an individuals control chart with real control limits and the
# classic Western Electric/Nelson run rules (catches sudden shifts and
# short-run drift), a process capability index against the property's
# tolerance band (catches "technically stable but too close to spec" even
# with no rule violations), a CUSUM chart (catches slow sustained drift a
# Shewhart chart is notoriously bad at catching early - pump wear, catalyst
# degradation, a slightly-off raw material lot), and a formal trend test
# (replaces "average of the first half vs. the second half" with an actual
# significance test). All four are deterministic - no ingredient list, no
# assumption, no interpretation. Interpretation is PI3's job, downstream of
# these numbers, same as everywhere else in the app.

_D2_MOVING_RANGE = 1.128  # control-chart constant for a 2-point moving range (individuals chart)


def property_run_series(session, foam_grade_id, property_name, normalize_pct_of_target=False, include_trials=False):
    """One row per production run (mean of any replicate results) for a
    foam grade/property, sorted chronologically by test date. This is the
    base series every SPC function below works from - a control chart,
    capability index, or trend test needs one point per run, not one point
    per replicate.

    `normalize_pct_of_target=True` re-expresses actual_value/target_value
    as percent-of-target (see normalize_to_pct_of_target) before returning
    - pass this when foam_grade_id is a foam family's list of grade ids, so
    the control chart/capability/CUSUM/trend-test functions downstream
    don't mistake a grade-to-grade target difference for a real shift or
    drift. Every SPC function reads only actual_value/target_value, so
    normalizing here is sufficient - nothing downstream needs to know
    whether it's looking at a raw unit or percent-of-target.

    `include_trials` (default False) is passed straight through to
    property_results_dataframe() - see that function's docstring. Only
    Trend Analysis passes True here. Trial-sourced rows have no run_id
    (lab trials have no ProductionRun behind them), so grouping can't use
    plain run_id alone once trials are included - "source"/"run_id"/
    "trial_id" together identify one event (one production run, or one
    lab trial) regardless of source; dropna=False keeps trial rows (which
    have a null run_id) and production-run rows (which have a null
    trial_id) from being silently dropped by groupby's default NaN
    handling."""
    df = property_results_dataframe(
        session, foam_grade_id=foam_grade_id, property_name=property_name, include_trials=include_trials
    )
    if df.empty:
        return df
    group_cols = ["source", "run_id", "trial_id"] if "trial_id" in df.columns else ["run_id"]
    per_run = (
        df.groupby(group_cols, dropna=False)
        .agg(
            actual_value=("actual_value", "mean"),
            target_value=("target_value", "mean"),
            tested_at=("tested_at", "max"),
            recipe_version=("recipe_version", "first"),
            machine=("machine", "first"),
            foam_grade=("foam_grade", "first"),
            n_replicates=("result_id", "count"),
        )
        .reset_index()
    )
    per_run = per_run.dropna(subset=["tested_at", "actual_value"]).sort_values("tested_at").reset_index(drop=True)
    if normalize_pct_of_target:
        per_run = normalize_to_pct_of_target(per_run)
    return per_run


def _moving_range_sigma(values):
    """Short-term sigma estimated from the mean moving range between
    consecutive points (values / d2), rather than the naive sample stdev -
    the standard control-chart estimator, since it isn't inflated by a real
    shift the way the plain stdev of the whole series would be. Falls back
    to the sample stdev if there aren't enough points for a moving range."""
    n = len(values)
    if n < 2:
        return 0.0
    mr = np.abs(np.diff(values))
    mr_bar = mr.mean()
    if mr_bar > 0:
        return mr_bar / _D2_MOVING_RANGE
    return float(np.std(values, ddof=1)) if n >= 2 else 0.0


def control_chart_analysis(series_df, min_points=5):
    """Individuals (I-MR) control chart: center line at the process mean,
    control limits at +/-3 sigma (moving-range sigma, see
    _moving_range_sigma), plus the 4 most commonly used Western
    Electric/Nelson run rules so a real signal is flagged explicitly instead
    of left for the reviewer to eyeball:
    - beyond the 3-sigma control limits (a genuine special-cause point)
    - 8+ consecutive points on one side of the center line (a sustained shift)
    - 6+ consecutive points steadily rising or falling (a sustained drift)
    - 2 of any 3 consecutive points beyond the 2-sigma warning line
    Returns a dict with the annotated per-point DataFrame, the control
    limits, and a flags list (each flag reduced to its first occurrence -
    "the earliest point where this pattern is already true" - plus how many
    points match it). Returns {"ready": False, "n": n} if there aren't at
    least `min_points` results yet - a control chart from a handful of
    points is noise dressed up as insight."""
    n = len(series_df)
    if n < min_points:
        return {"ready": False, "n": n}

    values = series_df["actual_value"].to_numpy(dtype=float)
    mean = float(values.mean())
    sigma = _moving_range_sigma(values)
    ucl = mean + 3 * sigma
    lcl = mean - 3 * sigma
    warn_hi = mean + 2 * sigma
    warn_lo = mean - 2 * sigma

    chart_df = series_df.copy().reset_index(drop=True)
    chart_df["center_line"] = mean
    chart_df["ucl"] = ucl
    chart_df["lcl"] = lcl

    flag_hits = {}  # rule -> list of point indices

    def _record(rule, idx):
        flag_hits.setdefault(rule, []).append(idx)

    if sigma > 0:
        for i, v in enumerate(values):
            if v > ucl or v < lcl:
                _record("Beyond 3-sigma control limit", i)

        side = np.sign(values - mean)
        run_len = 1
        for i in range(1, n):
            run_len = run_len + 1 if side[i] == side[i - 1] and side[i] != 0 else 1
            if run_len >= 8:
                _record("Sustained shift (8+ consecutive points on one side)", i)

        trend_len = 1
        direction = 0
        for i in range(1, n):
            d = np.sign(values[i] - values[i - 1])
            trend_len = trend_len + 1 if d == direction and d != 0 else 1
            direction = d if d != 0 else direction
            if trend_len >= 6:
                _record("Sustained drift (6+ consecutive points trending)", i)

        for i in range(2, n):
            window = values[i - 2 : i + 1]
            if (window > warn_hi).sum() >= 2 or (window < warn_lo).sum() >= 2:
                _record("2-of-3 beyond 2-sigma warning line", i)

    flags = []
    for rule, indices in flag_hits.items():
        first_i = min(indices)
        flags.append(
            {
                "rule": rule,
                "first_index": first_i,
                "first_run_id": _safe_int(chart_df.iloc[first_i]["run_id"]),
                "first_source": chart_df.iloc[first_i]["source"] if "source" in chart_df.columns else "Production Run",
                "first_tested_at": chart_df.iloc[first_i]["tested_at"],
                "points_matching": len(indices),
            }
        )
    flags.sort(key=lambda f: f["first_index"])

    return {
        "ready": True,
        "n": n,
        "mean": mean,
        "sigma": sigma,
        "ucl": ucl,
        "lcl": lcl,
        "chart_df": chart_df,
        "flags": flags,
        "in_control": len(flags) == 0,
    }


def capability_analysis(series_df, tolerance_pct=0.10, min_points=5):
    """Process capability (Cpk) against the property's tolerance band - the
    app's own +/-10% pass/fail convention, reused here as the spec limits
    rather than inventing a separate one. Cpk answers a different question
    than the control chart: a process can be perfectly "in control" (no rule
    violations, stable mean) and still be a Cpk of 0.6 - too close to its
    own spec limits to have any real margin. Returns None if there isn't a
    usable, consistent target value or not enough points yet.

    Rule of thumb this function's callers should use for the number:
    Cpk >= 1.33 is generally considered capable, 1.0-1.33 marginal, <1.0 not
    capable (the process routinely produces some results outside the
    tolerance band even when "in control")."""
    n = len(series_df)
    if n < min_points:
        return None
    target = series_df["target_value"].dropna().median()
    if pd.isna(target) or target == 0:
        return None

    values = series_df["actual_value"].to_numpy(dtype=float)
    mean = float(values.mean())
    sigma = _moving_range_sigma(values)
    if sigma <= 0:
        return None

    usl = target * (1 + tolerance_pct)
    lsl = target * (1 - tolerance_pct)
    cpu = (usl - mean) / (3 * sigma)
    cpl = (mean - lsl) / (3 * sigma)
    cpk = min(cpu, cpl)

    return {
        "n": n,
        "target": target,
        "mean": mean,
        "sigma": sigma,
        "usl": usl,
        "lsl": lsl,
        "cpu": round(cpu, 3),
        "cpl": round(cpl, 3),
        "cpk": round(cpk, 3),
    }


def cusum_analysis(series_df, k=0.5, h=5.0, min_points=8):
    """Two-sided tabular CUSUM: standardizes each point to z = (x -
    reference) / sigma, then accumulates C+ (upward drift) and C- (downward
    drift), resetting toward zero each step by a slack k (in sigma units -
    default 0.5 sigma, tuned to detect a sustained ~1-sigma shift) and
    flagging a breach once the cumulative sum exceeds the decision interval
    h (default 5 sigma). This exists because a Shewhart control chart is
    bad at catching exactly this failure mode: a small, sustained shift (a
    pump slowly wearing, a catalyst slowly losing activity) that never puts
    any single point outside the 3-sigma control limits but is still a
    real, accumulating drift.

    The reference point is the property's own recorded target value, not
    the sample mean of the series - deliberately. CUSUM is meant to detect
    drift away from where the process is supposed to be; if the series
    already contains an unaddressed shift, the sample mean is itself
    contaminated by that shift; centering on it would dilute the very drift
    this function exists to catch (and can even flag the earlier, correct
    period as the anomaly instead of the later, shifted one). Falls back to
    the sample mean only if no target value is recorded for this property.
    Returns None if there aren't enough points yet."""
    n = len(series_df)
    if n < min_points:
        return None

    values = series_df["actual_value"].to_numpy(dtype=float)
    target = series_df["target_value"].dropna().median()
    reference = float(target) if pd.notna(target) else float(values.mean())
    sigma = _moving_range_sigma(values)
    if sigma <= 0:
        return None

    z = (values - reference) / sigma
    c_pos = np.zeros(n)
    c_neg = np.zeros(n)
    for i in range(n):
        prev_pos = c_pos[i - 1] if i > 0 else 0.0
        prev_neg = c_neg[i - 1] if i > 0 else 0.0
        c_pos[i] = max(0.0, prev_pos + z[i] - k)
        c_neg[i] = min(0.0, prev_neg + z[i] + k)

    breach_index = None
    breach_direction = None
    for i in range(n):
        if c_pos[i] > h:
            breach_index, breach_direction = i, "upward"
            break
        if c_neg[i] < -h:
            breach_index, breach_direction = i, "downward"
            break

    chart_df = series_df.copy().reset_index(drop=True)
    chart_df["cusum_positive"] = c_pos
    chart_df["cusum_negative"] = c_neg

    result = {
        "n": n,
        "k": k,
        "h": h,
        "reference": reference,
        "chart_df": chart_df,
        "breach_index": breach_index,
        "breach_direction": breach_direction,
    }
    if breach_index is not None:
        result["breach_run_id"] = _safe_int(chart_df.iloc[breach_index]["run_id"])
        result["breach_source"] = (
            chart_df.iloc[breach_index]["source"] if "source" in chart_df.columns else "Production Run"
        )
        result["breach_tested_at"] = chart_df.iloc[breach_index]["tested_at"]
    return result


def trend_test(series_df, min_points=5, alpha=0.05):
    """Formal test for a monotonic trend over the run sequence - two
    complementary checks, not one, so the verdict doesn't rest on a single
    assumption about the trend's shape (fixed 2026-08-05: a user correctly
    pointed out that a linear-only test silently assumes the drift is a
    straight line, and never says so):

    1. Linear regression (scipy.stats.linregress) of actual_value against
       run order - the primary test. Assumes the drift proceeds at a
       roughly CONSTANT rate (a straight line) - the shape a steady
       mechanical/chemical cause (pump wear, catalyst degradation, a
       drifting raw-material lot) typically produces. slope/intercept
       together define the fitted line the page draws directly on the
       results chart; r_squared is how much of the run-to-run variation
       that line explains; p_value is the chance a slope this steep is
       just random noise.
    2. Mann-Kendall (Kendall's tau via scipy.stats.kendalltau, of
       actual_value against run order) - a secondary, non-parametric
       cross-check that only assumes the DIRECTION is consistent, not
       that the rate is constant. This is what catches a real trend that
       curves or changes pace, which the linear test's R² would otherwise
       under-credit and risk being read as noise. mk_significant usually
       agrees with `significant`; when it doesn't, that disagreement
       itself is the useful signal (see pages/16_Trend_Analysis.py's
       cross-check message) - it means the drift likely isn't a straight
       line.

    p < 0.05 (alpha) is the conventional threshold used for both. Returns
    None if there aren't enough points yet."""
    n = len(series_df)
    if n < min_points:
        return None

    values = series_df["actual_value"].to_numpy(dtype=float)
    x = np.arange(n, dtype=float)
    result = scipy_stats.linregress(x, values)

    direction = "increasing" if result.slope > 0 else ("decreasing" if result.slope < 0 else "flat")
    significant = result.pvalue < alpha

    mk = scipy_stats.kendalltau(x, values)
    mk_tau = float(mk.correlation)
    mk_p_value = float(mk.pvalue)
    if np.isnan(mk_tau):
        mk_tau = 0.0
    if np.isnan(mk_p_value):
        mk_p_value = 1.0
    mk_significant = mk_p_value < alpha
    mk_direction = "increasing" if mk_tau > 0 else ("decreasing" if mk_tau < 0 else "flat")

    return {
        "n": n,
        "slope_per_run": round(float(result.slope), 5),
        "intercept": round(float(result.intercept), 5),
        "r_squared": round(float(result.rvalue) ** 2, 3),
        "p_value": round(float(result.pvalue), 4),
        "significant": significant,
        "direction": direction,
        "alpha": alpha,
        "mk_tau": round(mk_tau, 3),
        "mk_p_value": round(mk_p_value, 4),
        "mk_significant": mk_significant,
        "mk_direction": mk_direction,
    }
