"""Screen 10: Similar Case Retrieval ("Use PI3")

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

import ai_assistant
from access_control import can_use_page
from db import (
    FoamGrade,
    ProductFamily,
    QualityObservation,
    SimilarCaseLink,
    TrialRecord,
    get_session,
    init_db,
)
from auth import current_user, logout_button, require_login
from helpers import (
    clickable_table,
    confidence_badge,
    delete_with_confirm,
    page_setup,
    render_function_action_intro,
    view_only_notice,
)
from tenant_scope import (
    apply_scope,
    company_picker,
    family_ids_for_plants,
    plant_ids_for_company,
    run_ids_for_company,
)

page_setup("Similar Case Retrieval")
init_db()
require_login()
logout_button()

st.title("Similar Case Retrieval — Use PI3")
render_function_action_intro(
    function_text=(
        "Searches your own historical trial, quality-issue, and expert-note records for cases "
        "similar to a current problem - by product family, foam grade, keyword, or, optionally, "
        "PI3's semantic search over expert notes and closed-case history - so you can see what "
        "was tried and concluded before instead of starting from scratch."
    ),
    action_text=(
        "Narrow by product family and/or foam grade, type a keyword describing the issue (e.g. "
        "shrinkage, hardness drift), and set which confidence levels to include. Turn on 'Also "
        "use PI3' if you want semantic matches beyond exact keyword hits - it only searches "
        "plants with PI3 connectivity enabled. Review the cases it returns against your current "
        "raw materials, process conditions, and target properties before applying anything from "
        "them."
    ),
)
st.info(
    "This retrieves comparable historical records for your own technical review. "
    "Decisions remain with your technical team."
)
session = get_session()
user = current_user()
page_usable = can_use_page("similar_case_retrieval", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice(action="using PI3 and saving similar-case links")
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="scr_company_filter"
)
active_company_id = company.id if company else None
scoped_plant_ids = plant_ids_for_company(session, active_company_id)
scoped_family_ids = family_ids_for_plants(session, scoped_plant_ids)
scoped_run_ids = run_ids_for_company(session, active_company_id)

col1, col2, col3 = st.columns(3)
families = apply_scope(session.query(ProductFamily), ProductFamily.id, scoped_family_ids).all()
with col1:
    family = st.selectbox("Product family", [None] + families, format_func=lambda f: "Any" if f is None else f.name)

grades_q = apply_scope(session.query(FoamGrade), FoamGrade.product_family_id, scoped_family_ids)
if family:
    grades_q = grades_q.filter(FoamGrade.product_family_id == family.id)
with col2:
    grade = st.selectbox("Foam grade", [None] + grades_q.all(), format_func=lambda g: "Any" if g is None else g.grade_name)

with col3:
    keyword = st.text_input("Issue / keyword (e.g. shrinkage, hardness drift)")

confidence_filter = st.multiselect(
    "Confidence level", ["Confirmed", "Likely", "Unconfirmed", "Rejected"], default=["Confirmed", "Likely"]
)

ai_available = ai_assistant.any_plant_enabled(session)
ask_ai = False
if ai_available:
    ask_ai = st.checkbox(
        "Also use PI3 (semantic search over expert notes & closed-case history)",
        disabled=not page_usable,
        help=(
            "Uses PI3 to search beyond exact keyword matches — it can surface relevant "
            "expert notes and past cases even when the wording differs. Optional, "
            "separately billed add-on; only searches knowledge from plants that have "
            "PI3 connectivity enabled."
        ),
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

    if ask_ai and ai_available and page_usable:
        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam "
            "manufacturer find similar historical cases for their own review. Search "
            "the connected knowledge base (expert notes and closed trial records) for "
            "cases relevant to the following, and summarize what you find.\n\n"
            "IMPORTANT: never phrase your answer as an instruction or command (e.g. do "
            "not say 'increase TDI by X' or 'use this formulation'). Always phrase it as "
            "historical reference for the reviewer to judge applicability themselves "
            "(e.g. 'Similar past cases show ...; review applicability against current "
            "raw materials, process conditions, and target properties.').\n\n"
            f"Product family: {family.name if family else 'any'}\n"
            f"Foam grade: {grade.grade_name if grade else 'any'}\n"
            f"Issue / keyword: {keyword or '(none specified)'}\n"
        )
        with st.spinner("Using PI3..."):
            st.session_state["similar_case_ai_answer"] = ai_assistant.ask_assistant(prompt)
    else:
        st.session_state.pop("similar_case_ai_answer", None)

result_ids = st.session_state.get("similar_case_results", [])

ai_answer = st.session_state.get("similar_case_ai_answer")
if ai_answer:
    st.divider()
    st.subheader("🤖 PI3 search")
    st.caption(
        "Generated by PI3 from expert notes and closed-case history. Review applicability "
        "against current raw materials, process conditions, and target properties."
    )
    st.write(ai_answer)
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

closed_trials = (
    apply_scope(session.query(TrialRecord), TrialRecord.production_run_id, scoped_run_ids)
    .filter(TrialRecord.status == "Closed")
    .all()
)
if len(closed_trials) >= 2:
    if not page_usable:
        st.caption("View-only access - saving a similar-case link is restricted for your role.")
    else:
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

# ---------------------------------------------------------------------------
# Saved similar-case links - list, edit, delete
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Saved similar-case links")

# closed_trials above is already scoped by apply_scope() to the current
# company filter (or unrestricted for the platform owner with no filter
# set) - reuse that same id set here so a link never surfaces a trial
# outside the reviewer's current scope.
closed_trial_ids = {t.id for t in closed_trials}
links = [
    link for link in session.query(SimilarCaseLink).order_by(SimilarCaseLink.created_at.desc()).all()
    if link.source_trial_id in closed_trial_ids and link.linked_trial_id in closed_trial_ids
]

if not links:
    st.info("No similar-case links saved yet.")
else:
    def _trial_label(trial_id):
        t = session.get(TrialRecord, trial_id)
        if not t:
            return f"Trial #{trial_id} (not found)"
        return f"Trial #{t.id} — {t.production_run.foam_grade.grade_name}"

    link_rows = [
        {
            "Trial A": _trial_label(link.source_trial_id),
            "Trial B": _trial_label(link.linked_trial_id),
            "Similarity basis": link.similarity_basis or "—",
            "Notes": link.notes or "—",
        }
        for link in links
    ]
    st.caption("Click a row to edit (and optionally delete) that link.")
    idx = clickable_table(link_rows, key="similar_case_links_table")
    if idx is not None and idx < len(links):
        st.session_state["similar_case_link_selected_id"] = links[idx].id
    else:
        st.session_state.pop("similar_case_link_selected_id", None)

    selected_link_id = st.session_state.get("similar_case_link_selected_id")
    selected_link = next((link for link in links if link.id == selected_link_id), None)

    if selected_link:
        st.markdown(f"**Edit link #{selected_link.id}**")
        with st.form(f"edit_similar_case_link_{selected_link.id}"):
            all_trials_for_edit = (
                apply_scope(session.query(TrialRecord), TrialRecord.production_run_id, scoped_run_ids)
                .filter(TrialRecord.status == "Closed")
                .all()
            )
            trial_options = all_trials_for_edit
            source_default = next(
                (i for i, t in enumerate(trial_options) if t.id == selected_link.source_trial_id), 0
            )
            target_default = next(
                (i for i, t in enumerate(trial_options) if t.id == selected_link.linked_trial_id), 0
            )
            ec1, ec2 = st.columns(2)
            e_source = ec1.selectbox(
                "Trial A", trial_options, index=source_default,
                format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name}",
                key=f"edit_link_source_{selected_link.id}",
            )
            e_target = ec2.selectbox(
                "Trial B", trial_options, index=target_default,
                format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name}",
                key=f"edit_link_target_{selected_link.id}",
            )
            e_basis = st.text_input(
                "Similarity basis", value=selected_link.similarity_basis or "",
                key=f"edit_link_basis_{selected_link.id}",
            )
            e_notes = st.text_area("Notes", value=selected_link.notes or "", key=f"edit_link_notes_{selected_link.id}")
            if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                if e_source.id == e_target.id:
                    st.error("Choose two different trials.")
                else:
                    selected_link.source_trial_id = e_source.id
                    selected_link.linked_trial_id = e_target.id
                    selected_link.similarity_basis = e_basis
                    selected_link.notes = e_notes
                    session.commit()
                    st.success("Similar-case link updated.")
                    st.rerun()

        def _do_delete_link(_session=session, _id=selected_link.id):
            _session.query(SimilarCaseLink).filter(SimilarCaseLink.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("similar_case_link_selected_id", None)

        if page_usable:
            delete_with_confirm(
                f"link #{selected_link.id}", _do_delete_link, key_prefix=f"similar_case_link_{selected_link.id}",
                extra_warning="This is a leaf record — deleting it has no other effects.",
            )
        else:
            st.caption("View-only access - deleting is restricted for your role.")

        if st.button("Clear selection", key="clear_similar_case_link_selection"):
            st.session_state.pop("similar_case_link_selected_id", None)
            st.rerun()

