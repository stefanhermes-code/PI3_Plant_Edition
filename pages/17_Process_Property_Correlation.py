"""Industrial Intelligence: Process-Property Correlation

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
from analytics import (
    PHASE_SETTING_LABELS,
    merged_run_property_dataframe,
    property_results_dataframe,
    rank_setting_correlations,
)
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup, render_ask_pi3_section

page_setup("Process-Property Correlation")
init_db()
require_login()
logout_button()

st.title("Process-Property Correlation")
st.caption(
    "Ranks every recorded machine/process setting by how strongly it's associated with a "
    "physical property outcome across this grade's production runs, so the settings worth "
    "investigating surface first."
)
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade first.")
    st.stop()

c1, c2 = st.columns(2)
grade = c1.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)

grade_results_df = property_results_dataframe(session, foam_grade_id=grade.id)
available_properties = (
    sorted(grade_results_df["property_name"].dropna().unique()) if not grade_results_df.empty else []
)
if not available_properties:
    st.info("No quality test results recorded yet for this foam grade.")
    st.stop()

property_name = c2.selectbox("Property", available_properties)

ranked = rank_setting_correlations(session, grade.id, property_name)
ranked_with_data = ranked.dropna(subset=["correlation"])

if ranked_with_data.empty:
    st.info(
        "No process setting has enough runs (need at least 3) with both a recorded Finalized-"
        "phase value and this property yet. Add Finalized-phase settings for more of this "
        "grade's production runs to unlock this analysis."
    )
    st.stop()

st.subheader("All settings, ranked by association strength")
display_ranked = ranked.copy()
display_ranked["label"] = display_ranked["label"]
display_ranked = display_ranked.rename(
    columns={"label": "Process setting", "n": "Runs compared", "correlation": "Correlation"}
)[["Process setting", "Runs compared", "Correlation"]]
st.dataframe(display_ranked, hide_index=True, use_container_width=True)

top = ranked_with_data.iloc[0]
direction = "positive" if top["correlation"] > 0 else "negative"
st.caption(
    f"Strongest association: **{top['label']}** ({direction}, r={top['correlation']:.2f}) across "
    f"{int(top['n'])} runs. Historical pattern for technical review - confirm against current raw "
    "materials and process conditions before treating it as causal."
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

if len(merged) < 2:
    st.info(
        "Not enough runs with both this process setting and this property recorded yet to "
        "compare (need at least 2)."
    )
else:
    chart_df = merged[[setting_field, "actual_value"]].rename(
        columns={setting_field: PHASE_SETTING_LABELS.get(setting_field, setting_field), "actual_value": property_name}
    )
    st.scatter_chart(chart_df, x=PHASE_SETTING_LABELS.get(setting_field, setting_field), y=property_name)
    st.dataframe(
        merged[["run_id", "run_date", "recipe_version", "machine", setting_field, "actual_value", "target_value"]],
        hide_index=True,
        use_container_width=True,
    )

plant_id = grade.product_family.plant_id if grade.product_family else None
if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.divider()
    st.subheader("Ask PI3 to interpret this pattern")
    if st.button("Get PI3 interpretation", key=f"ask_pi3_correlation_{grade.id}_{property_name}"):
        ranking_summary = "\n".join(
            f"- {r['label']}: r={r['correlation']:.2f} across {int(r['n'])} runs"
            if pd.notna(r["correlation"])
            else f"- {r['label']}: not enough data ({int(r['n'])} runs)"
            for _, r in ranked.iterrows()
        )
        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer "
            f"understand which process settings are associated with {property_name} for foam "
            f"grade {grade.grade_name}. Below is a ranked list of every recorded process setting's "
            "correlation with this property across this grade's production history.\n\n"
            f"{ranking_summary}\n\n"
            "Using this ranking plus any relevant expert notes or historical cases in the connected "
            "knowledge base, explain in plain language which setting(s) most likely matter and why, "
            "and what this means practically. This is a historical pattern for the reviewer's own "
            "investigation, not a directive - phrase it as observations and hypotheses, not "
            "instructions to change a setting."
        )
        with st.spinner("Using PI3..."):
            answer = ai_assistant.ask_assistant(prompt)
        if answer:
            st.session_state[f"correlation_ai_answer_{grade.id}_{property_name}"] = answer

    ai_answer = st.session_state.get(f"correlation_ai_answer_{grade.id}_{property_name}")
    if ai_answer:
        st.subheader("🤖 PI3 interpretation")
        st.caption(
            "Generated by PI3 from the ranked correlation pattern above plus expert notes and "
            "historical cases. Confirm through your own investigation before acting on it."
        )
        st.write(ai_answer)
elif ai_assistant.availability_status(session, plant_id) == "not_configured":
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
    default_foam_grade_id=grade.id,
    page_context=(
        f"The reviewer is on the Process-Property Correlation page, looking at '{property_name}' "
        f"for foam grade '{grade.grade_name}' (id {grade.id})."
    ),
    sample_questions=[
        f"Which process setting correlates most strongly with {property_name} for {grade.grade_name}?",
        f"Which ingredient's dosage correlates most with {property_name} for {grade.grade_name}?",
        f"Have there been any quality issues reported for {grade.grade_name} recently?",
    ],
    key_prefix=f"ask_pi3_freeform_correlation_{grade.id}_{property_name}",
)

