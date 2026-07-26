"""Industrial Intelligence: Recipe Optimization

Compares physical property outcomes across every recipe version of a foam
grade against target specs, alongside each version's formulation, showing
which version performs best and what is different about it. Also lets a
user ask PI3 for a formulation recommendation against target properties,
informed by this foam grade's recipe and quality-test history (see the
advisory boundary at the bottom of this page).
"""

import pandas as pd
import streamlit as st

import ai_assistant
from analytics import pass_rate, property_results_dataframe
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup

page_setup("Recipe Optimization")
init_db()
require_login()
logout_button()

st.title("Recipe Optimization")
st.caption(
    "Compares quality test results across every recipe version of a foam grade, showing "
    "each formulation change's actual effect on quality."
)
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade and at least one recipe version first.")
    st.stop()

grade = st.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)
versions = sorted(grade.recipe_versions, key=lambda v: v.created_at)

if not versions:
    st.info("This foam grade has no recipe versions yet.")
    st.stop()

results_df = property_results_dataframe(session, foam_grade_id=grade.id)

# Per-property summary tables, kept keyed by property name so the PI3
# recommendation prompt below can reuse them instead of recomputing.
property_summaries = {}

if results_df.empty:
    st.info("No quality test results recorded yet for this foam grade's production runs.")
else:
    properties = sorted(results_df["property_name"].dropna().unique())
    st.subheader("Property outcomes by recipe version")
    for prop in properties:
        sub = results_df[results_df["property_name"] == prop]
        summary = (
            sub.groupby("recipe_version")
            .agg(
                avg_actual=("actual_value", "mean"),
                avg_target=("target_value", "mean"),
                results=("result_id", "count"),
                pass_rate=("pass_fail", pass_rate),
            )
            .reset_index()
        )
        summary["avg_actual"] = summary["avg_actual"].round(2)
        summary["avg_target"] = summary["avg_target"].round(2)
        property_summaries[prop] = summary
        with st.container(border=True):
            st.markdown(f"**{prop}**")
            st.dataframe(summary, hide_index=True, use_container_width=True)
            best = summary.dropna(subset=["pass_rate"]).sort_values("pass_rate", ascending=False)
            if not best.empty:
                st.caption(
                    f"Highest pass rate for {prop}: recipe {best.iloc[0]['recipe_version']} "
                    f"({best.iloc[0]['pass_rate']:.0%}, n={int(best.iloc[0]['results'])}). "
                    "Review against current raw materials and process conditions before reusing."
                )

st.divider()
st.subheader("Formulation by version")
st.caption("The raw materials, dosage (php), and role recorded for each recipe version.")
for v in versions:
    with st.expander(f"{v.version_label} — {v.approval_status} — {v.change_note or ''}"):
        if v.components:
            st.dataframe(
                [
                    {
                        "Raw material": c.raw_material_name,
                        "Supplier": c.supplier,
                        "php": c.php,
                        "Role": c.role_in_formulation,
                    }
                    for c in v.components
                ],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("No components recorded for this version yet.")

st.divider()
st.subheader("Ask PI3 for a formulation recommendation")

plant_id = grade.product_family.plant_id if grade.product_family else None

if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.caption(
        "PI3 reviews this foam grade's recipe versions, formulation, and quality-test history "
        "against the target properties you enter below, and proposes a formulation for your "
        "technical team to evaluate and confirm."
    )
    target_properties = st.text_area(
        "Target properties",
        placeholder=(
            "e.g. Density 28 kg/m3, Hardness (CLD 40%) 3.5-4.0 kPa, Resilience > 55%, "
            "Tensile strength > 100 kPa"
        ),
        key="recipe_opt_targets",
    )
    if st.button(
        "Get PI3 recommendation",
        key=f"ask_pi3_recipe_opt_{grade.id}",
        disabled=not target_properties.strip(),
    ):
        composition_lines = [
            f"Version {v.version_label} ({v.approval_status}): "
            + ", ".join(f"{c.raw_material_name} {c.php} php ({c.role_in_formulation})" for c in v.components)
            for v in versions
            if v.components
        ]
        composition_summary = "\n".join(composition_lines) or "No formulation data recorded for any version."

        outcome_lines = []
        for prop, summary in property_summaries.items():
            for _, row in summary.iterrows():
                pass_rate_value = row["pass_rate"]
                pass_rate_text = f"{pass_rate_value:.0%}" if pd.notna(pass_rate_value) else "—"
                outcome_lines.append(
                    f"{prop} — version {row['recipe_version']}: avg actual {row['avg_actual']}, "
                    f"avg target {row['avg_target']}, pass rate {pass_rate_text}, "
                    f"n={int(row['results'])}"
                )
        outcome_summary = "\n".join(outcome_lines) or "No quality test results recorded yet."

        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer "
            f"select a formulation direction for {grade.grade_name}. Using this foam grade's "
            "recipe version history, formulation composition, and quality test outcomes below, "
            "plus any relevant expert notes or historical cases in the connected knowledge "
            "base, propose a formulation that could meet the target properties given.\n\n"
            "Phrase this as a recommendation for the reviewer to evaluate and confirm through "
            "their own trial process, addressed directly to the target properties requested.\n\n"
            f"Foam grade: {grade.grade_name}\n\n"
            f"Recipe versions and composition:\n{composition_summary}\n\n"
            f"Quality test outcomes by version:\n{outcome_summary}\n\n"
            f"Target properties requested:\n{target_properties.strip()}\n"
        )
        with st.spinner("Using PI3..."):
            answer = ai_assistant.ask_assistant(prompt)
        if answer:
            st.session_state[f"recipe_opt_ai_answer_{grade.id}"] = answer

    ai_answer = st.session_state.get(f"recipe_opt_ai_answer_{grade.id}")
    if ai_answer:
        st.subheader("🤖 PI3 recommendation")
        st.caption(
            "Generated by PI3 from this foam grade's recipe and quality-test history plus "
            "expert notes and historical cases. For your technical team to evaluate and "
            "confirm before applying."
        )
        st.write(ai_answer)
else:
    st.caption(
        "Enable PI3 connectivity for this plant (PI3 Connectivity, in Admin) to get a "
        "formulation recommendation here."
    )

