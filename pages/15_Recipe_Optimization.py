"""Industrial Intelligence: Recipe Optimization

Compares physical property outcomes across the recipe versions of the same
foam grade against target specs, alongside each version's composition -
the direct answer to "which formulation actually performs best, and what's
different about it". Historical comparison for technical review, not an
automated formulation instruction (see the advisory boundary at the bottom
of this page).
"""

import streamlit as st

from analytics import pass_rate, property_results_dataframe
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup, show_advisory_footer

page_setup("Recipe Optimization")
init_db()
require_login()
logout_button()

st.title("Recipe Optimization")
st.caption(
    "Compares physical property results across every recipe version of a foam grade, so a "
    "formulation change's actual effect on quality is visible - not just the change itself."
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

if results_df.empty:
    st.info("No physical property results recorded yet for this foam grade's production runs.")
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
st.subheader("Recipe composition by version")
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

show_advisory_footer()
