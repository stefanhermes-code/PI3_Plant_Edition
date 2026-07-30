"""Industrial Intelligence: Machine Settings Optimization

Ranks every process setting (mixer rpm, ratio/index, air pressure, ...) by
how clearly its low/medium/high ranges separate good outcomes from bad
ones for a foam grade, so the setting most worth reviewing surfaces first
- a starting point for technical review, not an automatic setpoint change.
"""

import pandas as pd
import streamlit as st

import ai_assistant
from analytics import (
    PHASE_SETTING_LABELS,
    merged_run_property_dataframe,
    property_results_dataframe,
    rank_setting_optimization,
)
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup, render_data_table, render_function_action_intro, render_scatter_chart_no_zero

page_setup("Machine Settings Optimization")
init_db()
require_login()
logout_button()

st.title("Machine Settings Optimization")
render_function_action_intro(
    function_text=(
        "Ranks every Finalized-phase process setting (mixer rpm, ratio/index, air pressure, "
        "conveyor speed, and so on) by how clearly its low/medium/high ranges separate outcomes "
        "closest to target from outcomes furthest from it, across a foam grade's production runs "
        "- a starting point for your team to review, not an automatic setpoint change. PI3 can "
        "then turn the ranked pattern into a plain-language read."
    ),
    action_text=(
        "Pick the foam grade and property you want to optimize toward, then read the ranked "
        "table - the setting at the top separates good from bad outcomes most clearly and is the "
        "one most worth reviewing on the floor. Use the PI3 synthesis further down for a "
        "plain-language interpretation before proposing any setpoint change to your team."
    ),
)
session = get_session()

# Only offer a grade here if it actually has quality test results to rank
# settings against - otherwise picking it just leads to a dead-end message
# (see Recipe Optimization's identical filter).
grades = [
    g for g in session.query(FoamGrade).all()
    if not property_results_dataframe(session, foam_grade_id=g.id).empty
]
if not grades:
    st.warning(
        "No foam grade yet has quality test results recorded - add these first before using "
        "Machine Settings Optimization."
    )
    st.stop()

c1, c2 = st.columns(2)
grade = c1.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)

grade_results_df = property_results_dataframe(session, foam_grade_id=grade.id)
available_properties = sorted(grade_results_df["property_name"].dropna().unique())

property_name = c2.selectbox("Property", available_properties)

ranked = rank_setting_optimization(session, grade.id, property_name)
ranked_with_data = ranked.dropna(subset=["spread_pct"])

if ranked_with_data.empty:
    st.info(
        "No process setting has enough runs (need at least 3, with enough variation to split "
        "into ranges) with both a recorded Finalized-phase value and this property yet. Add "
        "Finalized-phase settings for more of this grade's production runs to unlock this "
        "analysis."
    )
    st.stop()

st.subheader("All settings, ranked by how clearly they separate outcomes")
display_ranked = ranked_with_data.rename(
    columns={
        "label": "Process setting",
        "n": "Runs compared",
        "best_range": "Best range",
        "best_range_setting": "Best range (values)",
        "best_range_avg_dev_pct": "Best range avg deviation %",
        "spread_pct": "Gap vs worst range (pts)",
    }
)[
    [
        "Process setting",
        "Runs compared",
        "Best range",
        "Best range (values)",
        "Best range avg deviation %",
        "Gap vs worst range (pts)",
    ]
]
render_data_table(display_ranked)

top = ranked_with_data.iloc[0]
st.caption(
    f"Most actionable: **{top['label']}**, {top['best_range']} range "
    f"({top['best_range_setting']}) averages {top['best_range_avg_dev_pct']:.1f}% deviation from "
    f"target - a {top['spread_pct']:.1f} point gap versus this setting's worst-performing range, "
    f"across {int(top['n'])} runs. Review applicability against current raw materials and process "
    "conditions before adjusting settings."
)

st.divider()
st.subheader("Drill into one setting")
setting_field = st.selectbox(
    "Process setting",
    ranked["field"].tolist(),
    format_func=lambda f: PHASE_SETTING_LABELS.get(f, f),
)

merged = merged_run_property_dataframe(session, grade.id, property_name)
merged = merged.dropna(subset=[setting_field, "actual_value"])

if len(merged) < 3:
    st.info("Need at least 3 runs with both this setting and this property recorded to compare ranges.")
    st.stop()

merged = merged.copy()
merged["deviation_pct"] = ((merged["actual_value"] - merged["target_value"]) / merged["target_value"]).abs()
merged.loc[merged["target_value"].isna() | (merged["target_value"] == 0), "deviation_pct"] = float("nan")

merged["range"] = None
for q, labels in ((3, ["Low", "Medium", "High"]), (2, ["Low", "High"])):
    try:
        merged["range"] = pd.qcut(merged[setting_field], q=q, labels=labels, duplicates="drop")
        break
    except ValueError:
        continue

if merged["range"].isna().all() or merged["range"].nunique(dropna=True) < 2:
    st.info(
        f"Not enough variation in {PHASE_SETTING_LABELS.get(setting_field, setting_field)} across these "
        "runs yet to split into ranges — showing the raw data instead."
    )
    render_data_table(
        merged[["run_id", "run_date", setting_field, "actual_value", "target_value"]],
        max_height="400px",
    )
else:
    summary = (
        merged.groupby("range", observed=True)
        .agg(
            setting_range=(setting_field, lambda s: f"{s.min():g}–{s.max():g}"),
            avg_actual=("actual_value", "mean"),
            avg_target=("target_value", "mean"),
            avg_abs_deviation_pct=("deviation_pct", "mean"),
            runs=("run_id", "count"),
        )
        .reset_index()
    )
    summary["avg_actual"] = summary["avg_actual"].round(2)
    summary["avg_target"] = summary["avg_target"].round(2)
    summary["avg_abs_deviation_pct"] = (summary["avg_abs_deviation_pct"] * 100).round(1)

    render_data_table(summary)

    with_deviation = summary.dropna(subset=["avg_abs_deviation_pct"])
    if not with_deviation.empty:
        best = with_deviation.sort_values("avg_abs_deviation_pct").iloc[0]
        st.caption(
            f"Closest to target historically: **{best['range']}** range "
            f"({PHASE_SETTING_LABELS.get(setting_field, setting_field)} {best['setting_range']}), "
            f"averaging {best['avg_abs_deviation_pct']:.1f}% deviation from target across "
            f"{int(best['runs'])} run(s). Review applicability against current raw materials and "
            "process conditions before adjusting settings."
        )

    render_scatter_chart_no_zero(
        merged.rename(columns={setting_field: PHASE_SETTING_LABELS.get(setting_field, setting_field)}),
        x=PHASE_SETTING_LABELS.get(setting_field, setting_field),
        y="actual_value",
    )

if ai_assistant.is_enabled_for_plant(session, grade.product_family.plant_id if grade.product_family else None):
    st.divider()
    st.subheader("Ask PI3 to interpret this ranking")
    if st.button("Get PI3 interpretation", key=f"ask_pi3_optimization_{grade.id}_{property_name}"):
        ranking_summary = "\n".join(
            (
                f"- {r['label']}: best range {r['best_range']} ({r['best_range_setting']}), "
                f"{r['best_range_avg_dev_pct']:.1f}% avg deviation, {r['spread_pct']:.1f} point "
                f"gap vs its worst range, across {int(r['n'])} runs"
            )
            if pd.notna(r["spread_pct"])
            else f"- {r['label']}: not enough data ({int(r['n'])} runs)"
            for _, r in ranked.iterrows()
        )
        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer "
            f"identify which process settings are worth adjusting for {property_name} on foam "
            f"grade {grade.grade_name}. Below is a ranking of every recorded process setting by "
            "how clearly its low/medium/high ranges separate good outcomes from bad ones "
            "historically (bigger gap = more actionable).\n\n"
            f"{ranking_summary}\n\n"
            "Using this ranking plus any relevant expert notes or historical cases in the "
            "connected knowledge base, explain in plain language which setting(s) are most worth "
            "reviewing and why. This is a starting point for the reviewer's own investigation, "
            "not a directive - phrase it as observations and hypotheses, never as an instruction "
            "to change a setting to a specific value."
        )
        with st.spinner("Using PI3..."):
            answer = ai_assistant.ask_assistant(prompt)
        if answer:
            st.session_state[f"optimization_ai_answer_{grade.id}_{property_name}"] = answer

    ai_answer = st.session_state.get(f"optimization_ai_answer_{grade.id}_{property_name}")
    if ai_answer:
        st.subheader("🤖 PI3 interpretation")
        st.caption(
            "Generated by PI3 from the ranked settings pattern above plus expert notes and "
            "historical cases. Confirm through your own investigation before acting on it."
        )
        st.write(ai_answer)

