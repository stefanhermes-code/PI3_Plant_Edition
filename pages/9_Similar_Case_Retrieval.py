"""Screen 10: Similar Case Retrieval ("Ask PI3")

Advisory boundary (non-negotiable):
The system supports technical review. It does not issue autonomous
formulation commands. It must never phrase output as an instruction
("Increase TDI by X", "Reduce catalyst by Y", "Use this formulation").
It must instead phrase output as historical reference for human review
("Similar approved historical cases show the following adjustments and
conclusions. Review applicability against current raw materials, process
conditions, and target properties.").
"""

import streamlit as st

from db import (
    FoamGrade,
    ProductFamily,
    QualityObservation,
    SimilarCaseLink,
    TrialRecord,
    get_session,
    init_db,
)
from auth import logout_button, require_login
from helpers import confidence_badge, page_setup

page_setup("Similar Case Retrieval")
init_db()
require_login()
logout_button()

st.title("Similar Case Retrieval — Ask PI3")
st.info(
    "This retrieves comparable historical records for your own technical review. "
    "It does not tell you what to change — decisions remain with your technical team."
)
session = get_session()

col1, col2, col3 = st.columns(3)
families = session.query(ProductFamily).all()
with col1:
    family = st.selectbox("Product family", [None] + families, format_func=lambda f: "Any" if f is None else f.name)

grades_q = session.query(FoamGrade)
if family:
    grades_q = grades_q.filter(FoamGrade.product_family_id == family.id)
with col2:
    grade = st.selectbox("Foam grade", [None] + grades_q.all(), format_func=lambda g: "Any" if g is None else g.grade_name)

with col3:
    keyword = st.text_input("Issue / keyword (e.g. shrinkage, hardness drift)")

confidence_filter = st.multiselect(
    "Confidence level", ["Confirmed", "Likely", "Unconfirmed", "Rejected"], default=["Confirmed", "Likely"]
)

if st.button("Search similar cases", type="primary"):
    trials = session.query(TrialRecord).filter(TrialRecord.status == "Closed").all()

    def matches(t):
        run = t.production_run
        g = run.foam_grade
        f = g.product_family
        if family and f.id != family.id:
            return False
        if grade and g.id != grade.id:
            return False
        if keyword:
            haystack = " ".join(
                [
                    t.trial_or_change_objective or "",
                    t.conclusion or "",
                    t.reuse_recommendation or "",
                ]
                + [o.observation_type or "" for o in t.quality_observations]
            ).lower()
            if keyword.lower() not in haystack:
                return False
        return True

    results = [t for t in trials if matches(t)]
    st.session_state["similar_case_results"] = [t.id for t in results]

result_ids = st.session_state.get("similar_case_results", [])
if result_ids:
    st.divider()
    st.subheader(f"{len(result_ids)} similar approved historical case(s) found")
    st.caption(
        "Similar approved historical cases show the following adjustments and conclusions. "
        "Review applicability against current raw materials, process conditions, and target properties."
    )

    for tid in result_ids:
        t = session.get(TrialRecord, tid)
        if not t:
            continue
        run = t.production_run
        grade_obj = run.foam_grade
        with st.container(border=True):
            st.markdown(f"**Trial #{t.id}** — {grade_obj.grade_name} · recipe {run.recipe_version.version_label} · closed {t.date_closed}")
            st.write(f"Objective: {t.trial_or_change_objective}")
            for obs in t.quality_observations:
                if confidence_filter and obs.confidence_level not in confidence_filter:
                    continue
                st.write(f"- Issue: {obs.observation_type} ({confidence_badge(obs.confidence_level)})")
            st.write(f"**Conclusion:** {t.conclusion}")
            st.write(f"**Reuse recommendation:** {t.reuse_recommendation}")
            if t.adjustment_conclusions:
                st.write("Adjustments tried:")
                for a in t.adjustment_conclusions:
                    st.write(f"  - {a.parameter_changed or a.material_changed or '—'}: {a.result} ({confidence_badge(a.confidence_level)})")

elif "similar_case_results" in st.session_state:
    st.info("No similar approved historical cases matched these filters.")

st.divider()
st.subheader("Save a similar-case link")
st.caption(
    "If you've confirmed two trials are genuinely comparable, save the link so future "
    "searches surface them together."
)

closed_trials = session.query(TrialRecord).filter(TrialRecord.status == "Closed").all()
if len(closed_trials) >= 2:
    with st.form("save_similar_case_link"):
        c1, c2 = st.columns(2)
        source = c1.selectbox(
            "Trial A", closed_trials, format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name}"
        )
        target = c2.selectbox(
            "Trial B", closed_trials, format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name}"
        )
        similarity_basis = st.text_input("Similarity basis (e.g. foam grade, issue type, recipe version)")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save link")
        if submitted:
            if source.id == target.id:
                st.error("Choose two different trials.")
            else:
                session.add(
                    SimilarCaseLink(
                        source_trial_id=source.id,
                        linked_trial_id=target.id,
                        similarity_basis=similarity_basis,
                        notes=notes,
                    )
                )
                session.commit()
                st.success("Similar-case link saved.")
                st.rerun()
else:
    st.info("Close at least two trials before linking them as similar cases.")

