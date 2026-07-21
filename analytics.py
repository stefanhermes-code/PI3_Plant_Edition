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

import pandas as pd

from db import PhysicalPropertyResult, ProductionPhase, ProductionRun

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
