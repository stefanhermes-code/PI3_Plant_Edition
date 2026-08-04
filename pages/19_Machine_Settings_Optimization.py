"""Industrial Intelligence: Machine Settings Optimization

Ranks every process setting (mixer rpm, ratio/index, air pressure, ...) by
how clearly its low/medium/high ranges separate good outcomes from bad
ones for a foam grade, so the setting most worth reviewing surfaces first
- a starting point for technical review, not an automatic setpoint change.
"""

import pandas as pd
import streamlit as st

import ai_assistant
from access_control import can_use_page
from analytics import (
    BOOLEAN_SETTING_FIELDS,
    PHASE_SETTING_LABELS,
    format_setting_range,
    merged_run_property_dataframe,
    property_results_dataframe,
    rank_setting_optimization,
)
from auth import current_user, logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import (
    analysis_unit_picker,
    log_export_click,
    page_setup,
    render_ask_pi3_section,
    render_data_table,
    render_function_action_intro,
    render_pi3_docx_download,
    render_pi3_feedback_control,
    render_save_to_expert_notes_button,
    render_scatter_chart_no_zero,
    view_only_notice,
)
import reports
from tenant_scope import apply_scope, company_picker, grade_ids_for_company

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
        "Pick the foam grade (or a foam family to pool several grades together) and the property "
        "you want to optimize toward, then read the ranked table - the setting at the top "
        "separates good from bad outcomes most clearly and is the one most worth reviewing on the "
        "floor. Use the PI3 synthesis further down for a plain-language interpretation before "
        "proposing any setpoint change to your team. Download the Machine Settings Optimization "
        "Report further down for a shareable Word summary of the ranked table above."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("machine_settings_optimization", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice(action="using PI3")
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="mso_company_filter"
)
active_company_id = company.id if company else None
scoped_grade_ids = grade_ids_for_company(session, active_company_id)

# Only offer a grade here if it actually has quality test results to rank
# settings against - otherwise picking it just leads to a dead-end message
# (see Recipe Optimization's identical filter).
grades = [
    g for g in apply_scope(session.query(FoamGrade), FoamGrade.id, scoped_grade_ids).all()
    if not property_results_dataframe(session, foam_grade_id=g.id).empty
]
if not grades:
    st.warning(
        "No foam grade yet has quality test results recorded - add these first before using "
        "Machine Settings Optimization."
    )
    st.stop()

unit = analysis_unit_picker(grades, key_prefix="mso")
pooling_grades = unit["mode"] == "family"
if pooling_grades:
    st.caption(
        f"Pooling {len(unit['grade_ids'])} grade(s) in foam family **{unit['label']}**: "
        f"{', '.join(unit['member_grade_names'])}. Because grades in a family can have different "
        "target values for the same property, the ranking and drill-down below are computed "
        "against **% of each run's own target** instead of the property's raw unit - this keeps "
        "pooling grades together from reading a plain grade-to-grade target difference as a false "
        "pattern."
    )

grade_results_df = property_results_dataframe(session, foam_grade_id=unit["grade_ids"])
available_properties = sorted(grade_results_df["property_name"].dropna().unique())

property_name = st.selectbox("Property", available_properties)

ranked = rank_setting_optimization(session, unit["grade_ids"], property_name, normalize_pct_of_target=pooling_grades)
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

# ---------------------------------------------------------------------------
# Machine Settings Optimization Report (Context / Analysis / Conclusions) -
# the page's own ranked table, distinct from PI3's synthesis further down
# (which has its own separate Word download). Uses the exact `ranked`
# DataFrame already computed above - never re-derived.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Machine Settings Optimization Report")
st.caption(f"Context, analysis, and conclusions for {property_name} across {unit['label']}.")
mso_report_data = reports.build_machine_settings_report_data(
    session, unit, property_name, ranked, pooling_grades,
)
mso_rc1, mso_rc2 = st.columns(2)
mso_rc1.metric("Most actionable setting", top["label"])
mso_rc2.metric(
    "Settings with enough data", f"{len(ranked_with_data)} of {len(ranked)}",
)
st.download_button(
    "Download Word", data=reports.render_machine_settings_report_docx(mso_report_data),
    file_name="machine_settings_optimization_report.docx",
    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    key=f"mso_report_docx_{unit['state_key']}_{property_name}",
    on_click=log_export_click, args=("machine_settings_report_docx",),
    kwargs={"description": f"{property_name} · {unit['label']}"},
)

st.divider()
st.subheader("Drill into one setting")
setting_field = st.selectbox(
    "Process setting",
    ranked["field"].tolist(),
    format_func=lambda f: PHASE_SETTING_LABELS.get(f, f),
)

merged = merged_run_property_dataframe(
    session, unit["grade_ids"], property_name, normalize_pct_of_target=pooling_grades
)
merged = merged.dropna(subset=[setting_field, "actual_value"])

if len(merged) < 3:
    st.info("Need at least 3 runs with both this setting and this property recorded to compare ranges.")
    st.stop()

property_axis_label = property_name if not pooling_grades else f"{property_name} (% of target)"

merged = merged.copy()
merged["deviation_pct"] = ((merged["actual_value"] - merged["target_value"]) / merged["target_value"]).abs()
merged.loc[merged["target_value"].isna() | (merged["target_value"] == 0), "deviation_pct"] = float("nan")

merged["range"] = None
if setting_field in BOOLEAN_SETTING_FIELDS:
    # Group comparison, not a quantile split - see the matching comment in
    # analytics.rank_setting_optimization for why pd.qcut is the wrong tool
    # for a strictly 0/1 field (fails outright on skewed Yes/No splits).
    merged["range"] = merged[setting_field].map({1.0: "Yes", 0.0: "No"})
else:
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
    fallback_columns = ["run_id", "run_date", setting_field, "actual_value", "target_value"]
    if pooling_grades:
        fallback_columns.insert(2, "foam_grade")
    render_data_table(
        merged[fallback_columns],
        max_height="400px",
    )
else:
    summary = (
        merged.groupby("range", observed=True)
        .agg(
            setting_range=(setting_field, lambda s: format_setting_range(setting_field, s)),
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
        merged.rename(
            columns={
                setting_field: PHASE_SETTING_LABELS.get(setting_field, setting_field),
                "actual_value": property_axis_label,
            }
        ),
        x=PHASE_SETTING_LABELS.get(setting_field, setting_field),
        y=property_axis_label,
    )

plant_id = unit["plant_id"]
subject_desc = (
    f"foam grade {unit['label']}" if unit["mode"] == "grade"
    else f"foam family {unit['label']} (pooling grades: {', '.join(unit['member_grade_names'])})"
)
docx_grade_id = unit["entity_id"] if unit["mode"] == "grade" else None

if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.divider()
    st.subheader("Ask PI3 to interpret this ranking")
    if st.button(
        "Get PI3 interpretation",
        key=f"ask_pi3_optimization_{unit['state_key']}_{property_name}",
        disabled=not page_usable,
    ):
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
            f"identify which process settings are worth adjusting for {property_name} on "
            f"{subject_desc}. Below is a ranking of every recorded process setting by "
            "how clearly its low/medium/high ranges separate good outcomes from bad ones "
            "historically (bigger gap = more actionable).\n\n"
            f"{ranking_summary}\n\n"
            + (
                "Note: because this pools multiple foam grades, the property values are expressed "
                "as a percentage of each run's own target, not the raw unit.\n\n"
                if pooling_grades else ""
            )
            + "Using this ranking plus any relevant expert notes or historical cases in the "
            "connected knowledge base, explain in plain language which setting(s) are most worth "
            "reviewing and why. This is a starting point for the reviewer's own investigation, "
            "not a directive - phrase it as observations and hypotheses, never as an instruction "
            "to change a setting to a specific value."
        )
        with st.spinner("Using PI3..."):
            answer, interaction_log_id = ai_assistant.ask_assistant(prompt, company_id=active_company_id)
        if answer:
            st.session_state[f"optimization_ai_answer_{unit['state_key']}_{property_name}"] = answer
            st.session_state[f"optimization_ai_interaction_id_{unit['state_key']}_{property_name}"] = interaction_log_id
            st.session_state.pop(f"optimization_fixed_{unit['state_key']}_{property_name}_feedback_submitted", None)

    ai_answer = st.session_state.get(f"optimization_ai_answer_{unit['state_key']}_{property_name}")
    if ai_answer:
        st.subheader("🤖 PI3 interpretation")
        st.caption(
            "Generated by PI3 from the ranked settings pattern above plus expert notes and "
            "historical cases. Confirm through your own investigation before acting on it."
        )
        st.write(ai_answer)
        render_pi3_feedback_control(
            session, st.session_state.get(f"optimization_ai_interaction_id_{unit['state_key']}_{property_name}"),
            key_prefix=f"optimization_fixed_{unit['state_key']}_{property_name}",
        )
        optimization_question_label = f"PI3 interpretation of machine settings optimization for {property_name}, {unit['label']}"
        opt_dl_col, opt_save_col = st.columns([1, 1])
        with opt_dl_col:
            render_pi3_docx_download(
                session,
                plant_id,
                key_prefix=f"optimization_fixed_{unit['state_key']}_{property_name}",
                question_label=optimization_question_label,
                answer=ai_answer,
                foam_grade_id=docx_grade_id,
            )
        with opt_save_col:
            render_save_to_expert_notes_button(
                session,
                key_prefix=f"optimization_fixed_{unit['state_key']}_{property_name}",
                answer=ai_answer,
                question_label=optimization_question_label,
                link_type=unit["link_type"],
                entity_id=unit["entity_id"],
                disabled=not page_usable,
            )
elif user["is_platform_owner"]:
    # Only the platform owner sees why PI3 is unavailable here - a customer
    # whose subscription/plant simply doesn't have it enabled shouldn't be
    # shown a feature they don't know exists (see PI3 Gaps discussion,
    # 2026-08-01: "the customer does not know that they could have this
    # functionality").
    if ai_assistant.availability_status(session, plant_id) == "not_configured":
        st.caption(
            "PI3 isn't configured for this deployment yet (missing API credentials) - contact "
            "your administrator."
        )
    else:
        st.caption(
            "Enable PI3 connectivity for this plant (PI3 Connectivity, in Admin) to get PI3's "
            "interpretation here."
        )

st.divider()
render_ask_pi3_section(
    session,
    plant_id,
    default_foam_grade_id=docx_grade_id,
    page_context=(
        f"The reviewer is on the Machine Settings Optimization page, looking at '{property_name}' "
        f"for {subject_desc}."
    ),
    sample_questions=[
        f"Which process setting is most worth adjusting to improve {property_name} for {unit['label']}?",
        f"What's the best range for the top-ranked setting, and how confident should we be in it?",
        f"Have there been any quality issues reported for {unit['label']} recently?",
    ],
    note_link_type=unit["link_type"],
    note_entity_id=unit["entity_id"],
    key_prefix=f"ask_pi3_freeform_optimization_{unit['state_key']}_{property_name}",
    disabled=not page_usable,
)

