"""Shared data-assembly helpers for the Industrial Intelligence pages.

The real value of PI3 Plant Edition is the join that already exists in the
schema: a production run carries a recipe (version + components), a
machine, its Finalized-phase process settings, and the physical property
results / quality observations it produced - all keyed to the same
production_run_id. Every Industrial Intelligence function (Recipe
Optimization, Trend Analysis, Process-Property Correlation, Root-Cause
Assistant, Machine Settings Optimization) starts from that same join, so
it is built once here rather than five slightly-different copies of the
same query living in each page.

Note: ProductionRun deliberately has no back-populated .phases/.results
collections (see the comment on ProductionRun in db.py - it avoids a
Streamlit/SQLAlchemy deepcopy crash). Every function below queries
ProductionPhase/PhysicalPropertyResult directly by production_run_id
instead.
"""

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from db import (
    ComponentStreamReading,
    PhysicalPropertyResult,
    ProductionPhase,
    ProductionRun,
    RawMaterial,
    RecipeVersion,
)

# Machine/process settings captured per phase (see ProductionPhase in
# db.py). These are the fields every process-vs-quality analysis works
# from.
PHASE_SETTING_FIELDS = [
    "mixer_rpm",
    "conveyor_speed",
    "air_injection_rate",
    "air_pressure_bar",
    "ratio_index",
    "foam_height_mm",
    "sidewall_width_mm",
]

PHASE_SETTING_LABELS = {
    "mixer_rpm": "Mixer rpm",
    "conveyor_speed": "Conveyor speed (m/min)",
    "air_injection_rate": "Air injection rate",
    "air_pressure_bar": "Air pressure (bar)",
    "ratio_index": "Ratio / index",
    "foam_height_mm": "Foam height (mm)",
    "sidewall_width_mm": "Sidewall width (mm)",
}


def run_settings_dataframe(session, foam_grade_id=None):
    """One row per production run: identifying info (grade, recipe version,
    machine) plus its Finalized-phase process settings (falls back to the
    Setup phase if no Finalized phase has been recorded yet for that run).
    """
    q = session.query(ProductionRun)
    if foam_grade_id:
        q = q.filter(ProductionRun.foam_grade_id == foam_grade_id)
    runs = q.order_by(ProductionRun.run_date).all()

    rows = []
    for run in runs:
        phase_rows = (
            session.query(ProductionPhase).filter(ProductionPhase.production_run_id == run.id).all()
        )
        by_name = {p.phase_name: p for p in phase_rows}
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
            row[field] = getattr(phase, field) if phase else None
        rows.append(row)

    return pd.DataFrame(rows)


def property_results_dataframe(session, foam_grade_id=None, property_name=None):
    """One row per physical property result, joined with the run's grade,
    recipe version, and machine - the base table for trend/correlation
    work."""
    q = session.query(PhysicalPropertyResult).join(ProductionRun)
    if foam_grade_id:
        q = q.filter(ProductionRun.foam_grade_id == foam_grade_id)
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
                "run_id": run.id,
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
                "pass_fail": r.pass_fail,
                "tested_at": r.tested_at,
            }
        )
    return pd.DataFrame(rows)


def pass_rate(series) -> float | None:
    """Share of non-null Pass/Fail values that are 'Pass', or None if there
    is nothing to compute from."""
    known = series.dropna()
    if known.empty:
        return None
    return round((known == "Pass").sum() / len(known), 3)


def merged_run_property_dataframe(session, foam_grade_id, property_name):
    """One row per production run for a given grade/property: process
    settings joined to that run's mean result for the chosen property.
    Used by Process-Property Correlation and Machine Settings Optimization,
    which both need "one settings snapshot" per "one quality outcome"."""
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
    return merged


def rank_setting_correlations(session, foam_grade_id, property_name):
    """For EVERY process setting at once, compute its correlation with the
    chosen property's actual value across this grade's runs, ranked by
    |correlation| descending. This is the difference between "intelligence"
    and "a graph you have to already know where to point": instead of
    picking one setting and hoping it's the relevant one, the reviewer sees
    immediately which of the 7 settings actually moves this property, and
    by how much, before drilling into any single scatter plot."""
    merged = merged_run_property_dataframe(session, foam_grade_id, property_name)
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
    # be computed (unlike rank_component_correlations etc., which skip
    # uncomputable rows entirely) - so if every setting has too few points,
    # "correlation" ends up an all-None object-dtype column, and pandas'
    # .abs() raises TypeError on that (bad operand type for abs(): 'NoneType')
    # rather than treating it as NaN. pd.to_numeric coerces None/object-None
    # to a proper float NaN first, which .abs() (and na_position="last"
    # below) handle correctly regardless of how many rows have no value yet.
    ranked["_abs"] = pd.to_numeric(ranked["correlation"], errors="coerce").abs()
    ranked = ranked.sort_values("_abs", ascending=False, na_position="last").drop(columns=["_abs"]).reset_index(drop=True)
    return ranked


def rank_setting_optimization(session, foam_grade_id, property_name):
    """For EVERY process setting, bucket its values into Low/Medium/High (or
    Low/High) ranges and measure the gap between the best- and
    worst-performing range's average absolute deviation from target. A
    bigger gap means that setting more clearly separates good outcomes from
    bad ones for this grade/property - ranked so the most actionable
    setting surfaces first, instead of the reviewer checking each of the 7
    settings one at a time to find out which one matters."""
    merged = merged_run_property_dataframe(session, foam_grade_id, property_name)
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
            .agg(avg_dev=("deviation_pct", "mean"), setting_range=(field, lambda s: f"{s.min():g}–{s.max():g}"))
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


def rank_component_correlations(session, foam_grade_id, property_name, min_versions=3):
    """For every raw material used anywhere in this grade's recipe
    versions, correlate its php against that version's mean outcome for
    the chosen property, across all of the grade's recipe versions. Ranked
    by |correlation| descending.

    Needs real variation to say anything: a raw material must appear (with
    a recorded php) in at least `min_versions` versions, and those versions
    must have quality results, or it's excluded rather than shown as a
    misleading single-point "correlation". Returns an empty DataFrame if
    nothing qualifies - callers should treat that as "not enough recipe
    version history yet", not as "no relationship found"."""
    versions = session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == foam_grade_id).all()
    if len(versions) < min_versions:
        return pd.DataFrame()

    results_df = property_results_dataframe(session, foam_grade_id=foam_grade_id, property_name=property_name)
    if results_df.empty:
        return pd.DataFrame()
    per_version_result = results_df.groupby("recipe_version_id")["actual_value"].mean()

    php_by_material = {}  # name -> {version_id: php}
    for v in versions:
        outcome = per_version_result.get(v.id)
        if outcome is None or pd.isna(outcome):
            continue
        for c in v.components:
            if c.php is None:
                continue
            php_by_material.setdefault(c.raw_material_name, {})[v.id] = c.php

    rows = []
    for material, php_map in php_by_material.items():
        if len(php_map) < min_versions:
            continue
        php_series = pd.Series(php_map)
        outcome_series = per_version_result.reindex(php_series.index)
        corr = php_series.corr(outcome_series)
        if pd.isna(corr):
            continue
        rows.append({"raw_material_name": material, "n_versions": len(php_map), "correlation": round(corr, 3)})

    ranked = pd.DataFrame(rows)
    if not ranked.empty:
        ranked["_abs"] = ranked["correlation"].abs()
        ranked = ranked.sort_values("_abs", ascending=False).drop(columns=["_abs"]).reset_index(drop=True)
    return ranked


# ---------------------------------------------------------------------------
# Actual (metered) usage vs. outcome - the per-run counterpart to
# rank_component_correlations above.
# ---------------------------------------------------------------------------
# A recipe version's php is a target, not a measurement: the same recipe
# version, run a hundred times, does not meter out the exact same dosage of
# every material every time - that is what the flow meters on
# ComponentStreamReading exist to capture. rank_component_correlations only
# asks "does changing the PLANNED formulation matter" and needs several
# recipe versions to say anything at all. The functions below ask the
# question a plant running one settled recipe actually needs answered:
# "does this run's ACTUAL metered dosage of each material line up with this
# run's actual outcome" - with n = number of production runs, not number of
# recipe versions, so it works even for a grade with a single recipe version
# that has simply been run (and metered, and tested) many times.


def actual_usage_dataframe(session, foam_grade_id=None):
    """One row per (production run, raw-material stream): that stream's
    actual delivered quantity for the run's Finalized phase, re-expressed as
    an actual-php-equivalent using the run's own Base-polyol stream reading
    as the 100-parts basis - the same convention every planned recipe uses,
    computed here from what the flow meters actually measured for that one
    batch instead of from the recipe. Runs with no Finalized-phase stream
    readings, or with no identifiable Base-polyol reading to normalize
    against, are skipped rather than guessed at."""
    q = session.query(ProductionRun)
    if foam_grade_id:
        q = q.filter(ProductionRun.foam_grade_id == foam_grade_id)
    runs = q.all()

    rows = []
    for run in runs:
        phase = (
            session.query(ProductionPhase)
            .filter(
                ProductionPhase.production_run_id == run.id,
                ProductionPhase.phase_name == "Finalized",
            )
            .first()
        )
        if phase is None:
            continue
        readings = (
            session.query(ComponentStreamReading)
            .filter(ComponentStreamReading.production_phase_id == phase.id)
            .all()
        )
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
    return pd.DataFrame(rows)


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


def property_run_series(session, foam_grade_id, property_name):
    """One row per production run (mean of any replicate results) for a
    foam grade/property, sorted chronologically by test date. This is the
    base series every SPC function below works from - a control chart,
    capability index, or trend test needs one point per run, not one point
    per replicate."""
    df = property_results_dataframe(session, foam_grade_id=foam_grade_id, property_name=property_name)
    if df.empty:
        return df
    per_run = (
        df.groupby("run_id")
        .agg(
            actual_value=("actual_value", "mean"),
            target_value=("target_value", "mean"),
            tested_at=("tested_at", "max"),
            recipe_version=("recipe_version", "first"),
            machine=("machine", "first"),
            n_replicates=("result_id", "count"),
        )
        .reset_index()
    )
    per_run = per_run.dropna(subset=["tested_at", "actual_value"]).sort_values("tested_at").reset_index(drop=True)
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
                "first_run_id": int(chart_df.iloc[first_i]["run_id"]),
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
        result["breach_run_id"] = int(chart_df.iloc[breach_index]["run_id"])
        result["breach_tested_at"] = chart_df.iloc[breach_index]["tested_at"]
    return result


def trend_test(series_df, min_points=5, alpha=0.05):
    """Formal test for a monotonic trend over the run sequence: linear
    regression of actual_value against run order, with a significance test
    on the slope (via scipy.stats.linregress). This replaces an
    eyeballed/first-half-vs-second-half average with an actual answer to
    "is this a statistically real trend or is it noise" - p < 0.05 is the
    conventional threshold used here. Returns None if there aren't enough
    points yet."""
    n = len(series_df)
    if n < min_points:
        return None

    values = series_df["actual_value"].to_numpy(dtype=float)
    x = np.arange(n, dtype=float)
    result = scipy_stats.linregress(x, values)

    direction = "increasing" if result.slope > 0 else ("decreasing" if result.slope < 0 else "flat")
    significant = result.pvalue < alpha

    return {
        "n": n,
        "slope_per_run": round(float(result.slope), 5),
        "r_squared": round(float(result.rvalue) ** 2, 3),
        "p_value": round(float(result.pvalue), 4),
        "significant": significant,
        "direction": direction,
        "alpha": alpha,
    }
