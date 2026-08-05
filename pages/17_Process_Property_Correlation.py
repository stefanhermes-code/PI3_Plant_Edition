"""Industrial Intelligence: Machine Settings vs Physical Properties Correlation

Cross-references every machine/process setting (Finalized-phase mixer rpm,
ratio/index, air pressure, ...) against a physical property outcome for
the same production runs at once, ranked by strength, so the reviewer sees
which settings actually move the needle on quality without checking each
one individually. PI3 can then synthesize the ranked pattern into a plain-
language read for the technical team.
"""

import pandas as pd
import streamlit as st

import ai_assistant
from access_control import can_use_page
from analytics import (
    PHASE_SETTING_LABELS,
    merged_run_property_dataframe,
    property_results_dataframe,
    rank_setting_correlations,
)
from auth import current_user, logout_button, require_login
from db import FoamGrade, get_session, init_db
import reports
from tenant_scope import apply_scope, company_picker, grade_ids_for_company
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

page_setup("Machine Settings vs Physical Properties Correlation")
init_db()
require_login()
logout_button()

st.title("Machine Settings vs Physical Properties Correlation")
render_function_action_intro(
    function_text=(
        "Cross-references every Finalized-phase machine/process setting (mixer rpm, ratio/index, "
        "air pressure, conveyor speed, and so on) against a chosen physical property outcome, "
        "across the same production runs at once, ranked by correlation strength - so you see "
        "which settings actually move that property's outcome without checking each one "
        "individually against a scatter plot. PI3 can then synthesize the ranked pattern into a "
        "plain-language read for the technical team."
    ),
    action_text=(
        "Choose whether to analyze one foam grade or a whole foam family (its grades pooled "
        "together) and the property you want to explain, then read down the ranked table - the "
        "setting at the top has the strongest statistical association with that outcome across "
        "the recorded runs. Treat it as a lead to investigate, not a cause on its own: review it "
        "against current raw materials and process conditions before treating it as causal. Use "
        "'Ask PI3' if you want the ranked pattern turned into a plain-language interpretation. "
        "Download the Process-Property Correlation Report further down for a shareable Word "
        "summary of the ranked table above."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("machine_settings_correlation", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice(action="using PI3 and saving to Expert Notes")
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="ppc_company_filter"
)
active_company_id = company.id if company else None
scoped_grade_ids = grade_ids_for_company(session, active_company_id)

# Only offer a grade here if it actually has quality test results to
# correlate against - otherwise picking it just leads to a dead-end
# message (see Recipe Optimization's identical filter).
grades = [
    g for g in apply_scope(session.query(FoamGrade), FoamGrade.id, scoped_grade_ids).all()
    if not property_results_dataframe(session, foam_grade_id=g.id).empty
]
if not grades:
    st.warning(
        "No foam grade yet has quality test results recorded - add these first before using "
        "Machine Settings vs Physical Properties Correlation."
    )
    st.stop()

unit = analysis_unit_picker(grades, key_prefix="ppc")
pooling_grades = unit["mode"] == "family"
if pooling_grades:
    st.caption(
        f"Pooling {len(unit['grade_ids'])} grade(s) in foam family **{unit['label']}**: "
        f"{', '.join(unit['member_grade_names'])}. Because grades in a family can have different "
        "target values for the same property, the correlation below is computed against **% of "
        "each run's own target** instead of the property's raw unit - this is what keeps pooling "
        "grades together from reading a plain grade-to-grade target difference as a false "
        "correlation."
    )

grade_results_df = property_results_dataframe(session, foam_grade_id=unit["grade_ids"])
available_properties = sorted(grade_results_df["property_name"].dropna().unique())

property_name = st.selectbox("Property", available_properties)

ranked = rank_setting_correlations(session, unit["grade_ids"], property_name, normalize_pct_of_target=pooling_grades)
ranked_with_data = ranked.dropna(subset=["correlation"])

if ranked_with_data.empty:
    st.info(
        "No process setting has enough runs (need at least 3) with both a recorded Finalized-"
        "phase value and this property yet. Add Finalized-phase settings for more of this "
        "grade's production runs to unlock this analysis."
    )
    st.stop()

st.subheader("All settings, ranked by correlation strength")
# Ranked (in rank_setting_correlations, analytics.py) by |correlation|
# descending - a strong negative correlation is just as significant an
# association as a strong positive one, just in the opposite direction.
# Shown here as its own "Strength" column (always positive, so it reads
# as visibly highest-to-lowest top to bottom) alongside the signed
# "Correlation" column for direction - a signed column alone made the
# ranking look inconsistent at a glance (e.g. a strong negative value
# appearing above a smaller positive one).
display_ranked = ranked.copy()
display_ranked["strength"] = display_ranked["correlation"].abs().round(3)
display_ranked = display_ranked.rename(
    columns={"label": "Process setting", "n": "Runs compared", "strength": "Strength (|r|)", "correlation": "Correlation"}
)[["Process setting", "Runs compared", "Strength (|r|)", "Correlation"]]
render_data_table(display_ranked)
st.caption(
    "Ranked by strength (|r|) - the size of the association regardless of direction - so the "
    "setting most worth investigating is always at the top, whether it moves this property up "
    "or down. Correlation keeps the sign to show that direction."
)

top = ranked_with_data.iloc[0]
direction = "positive" if top["correlation"] > 0 else "negative"
st.caption(
    f"Strongest association: **{top['label']}** ({direction}, r={top['correlation']:.2f}) across "
    f"{int(top['n'])} runs. Historical pattern for technical review - confirm against current raw "
    "materials and process conditions before treating it as causal."
)

# ---------------------------------------------------------------------------
# Process-Property Correlation Report (Context / Analysis / Conclusions) -
# the page's own ranked correlation table, distinct from PI3's synthesis
# further down (which has its own separate Word download). Uses the exact
# `ranked` DataFrame already computed above - never re-derived.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Process-Property Correlation Report")
st.caption(f"Context, analysis, and conclusions for {property_name} across {unit['label']}.")
correlation_report_data = reports.build_correlation_report_data(
    session, unit, property_name, ranked, pooling_grades,
)
pc_rc1, pc_rc2 = st.columns(2)
pc_rc1.metric("Strongest association", top["label"] if not ranked_with_data.empty else "—")
pc_rc2.metric(
    "Settings with enough data", f"{len(ranked_with_data)} of {len(ranked)}",
)
st.download_button(
    "Download Word", data=reports.render_correlation_report_docx(correlation_report_data),
    file_name="process_property_correlation_report.docx",
    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    key=f"correlation_report_docx_{unit['state_key']}_{property_name}",
    on_click=log_export_click, args=("correlation_report_docx",),
    kwargs={"description": f"{property_name} · {unit['label']}"},
)

st.divider()
st.subheader("Detailed correlation graph")
setting_field = st.selectbox(
    "Process setting",
    ranked["field"].tolist(),
    format_func=lambda f: PHASE_SETTING_LABELS.get(f, f),
)

merged = merged_run_property_dataframe(session, unit["grade_ids"], property_name, normalize_pct_of_target=pooling_grades)
merged = merged.dropna(subset=[setting_field, "actual_value"])

property_axis_label = property_name if not pooling_grades else f"{property_name} (% of target)"
if len(merged) < 2:
    st.info(
        "Not enough runs with both this process setting and this property recorded yet to "
        "compare (need at least 2)."
    )
else:
    chart_df = merged[[setting_field, "actual_value"]].rename(
        columns={setting_field: PHASE_SETTING_LABELS.get(setting_field, setting_field), "actual_value": property_axis_label}
    )
    render_scatter_chart_no_zero(
        chart_df, x=PHASE_SETTING_LABELS.get(setting_field, setting_field), y=property_axis_label
    )
    drill_columns = ["run_id", "run_date", "machine", setting_field, "actual_value", "target_value"]
    if pooling_grades:
        drill_columns.insert(2, "foam_grade")
    render_data_table(merged[drill_columns], max_height="400px")

plant_id = unit["plant_id"]
subject_desc = (
    f"foam grade {unit['label']}" if unit["mode"] == "grade"
    else f"foam family {unit['label']} (pooling grades: {', '.join(unit['member_grade_names'])})"
)
docx_grade_id = unit["entity_id"] if unit["mode"] == "grade" else None
if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.divider()
    st.subheader("Ask PI3 to interpret this pattern")
    if st.button(
        "Get PI3 interpretation",
        key=f"ask_pi3_correlation_{unit['state_key']}_{property_name}",
        disabled=not page_usable,
    ):
        ranking_summary = "\n".join(
            f"- {r['label']}: r={r['correlation']:.2f} across {int(r['n'])} runs"
            if pd.notna(r["correlation"])
            else f"- {r['label']}: not enough data ({int(r['n'])} runs)"
            for _, r in ranked.iterrows()
        )
        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer "
            f"understand which process settings are associated with {property_name} for "
            f"{subject_desc}. Below is a ranked list of every recorded process setting's "
            "correlation with this property across this production history.\n\n"
            f"{ranking_summary}\n\n"
            + (
                "Note: because this pools multiple foam grades, the property values shown are "
                "expressed as a percentage of each run's own target, not the raw unit.\n\n"
                if pooling_grades else ""
            )
            + "Using this ranking plus any relevant expert notes or historical cases in the connected "
            "knowledge base, explain in plain language which setting(s) most likely matter and why, "
            "and what this means practically. This is a historical pattern for the reviewer's own "
            "investigation, not a directive - phrase it as observations and hypotheses, not "
            "instructions to change a setting."
        )
        with st.spinner("Using PI3..."):
            answer, interaction_log_id = ai_assistant.ask_assistant(
                prompt, company_id=active_company_id, call_site="process_property_correlation"
            )
        if answer:
            st.session_state[f"correlation_ai_answer_{unit['state_key']}_{property_name}"] = answer
            st.session_state[f"correlation_ai_interaction_id_{unit['state_key']}_{property_name}"] = interaction_log_id
            st.session_state.pop(f"correlation_fixed_{unit['state_key']}_{property_name}_saved_note_id", None)
            st.session_state.pop(f"correlation_fixed_{unit['state_key']}_{property_name}_feedback_submitted", None)

    ai_answer = st.session_state.get(f"correlation_ai_answer_{unit['state_key']}_{property_name}")
    if ai_answer:
        st.subheader("🤖 PI3 interpretation")
        st.caption(
            "Generated by PI3 from the ranked correlation pattern above plus expert notes and "
            "historical cases. Confirm through your own investigation before acting on it."
        )
        st.write(ai_answer)
        render_pi3_feedback_control(
            session, st.session_state.get(f"correlation_ai_interaction_id_{unit['state_key']}_{property_name}"),
            key_prefix=f"correlation_fixed_{unit['state_key']}_{property_name}",
        )
        corr_question_label = f"PI3 interpretation of process-setting correlation for {property_name}, {unit['label']}"
        corr_dl_col, corr_save_col = st.columns([1, 1])
        with corr_dl_col:
            render_pi3_docx_download(
                session,
                plant_id,
                key_prefix=f"correlation_fixed_{unit['state_key']}_{property_name}",
                question_label=corr_question_label,
                answer=ai_answer,
                foam_grade_id=docx_grade_id,
            )
        with corr_save_col:
            render_save_to_expert_notes_button(
                session,
                key_prefix=f"correlation_fixed_{unit['state_key']}_{property_name}",
                answer=ai_answer,
                question_label=corr_question_label,
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
        f"The reviewer is on the Machine Settings vs Physical Properties Correlation page, looking "
        f"at '{property_name}' "
        f"for {subject_desc}."
    ),
    sample_questions=[
        f"Which process setting correlates most strongly with {property_name} for {unit['label']}?",
        f"Which ingredient's dosage correlates most with {property_name} for {unit['label']}?",
        f"Have there been any quality issues reported for {unit['label']} recently?",
    ],
    note_link_type=unit["link_type"],
    note_entity_id=unit["entity_id"],
    key_prefix=f"ask_pi3_freeform_correlation_{unit['state_key']}_{property_name}",
    disabled=not page_usable,
)

