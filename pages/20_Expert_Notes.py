"""Screen: Expert Notes

Captures qualitative expert knowledge - the kind of thing that lives in a
technical person's head or a stray email, not a structured measurement -
linked to a production run (the common case), a foam grade, or a foam
family (added 2026-08-02, alongside "analyze by foam family" on Trend
Analysis/Process-Property Correlation/Machine Settings Optimization - see
helpers.analysis_unit_picker). This is the raw material
PI3 needs: when PI3 connectivity is enabled for the relevant plant, saving
a note here also feeds it into PI3 so future Root-Cause Assistant reasoning
can retrieve it.

Also shows PI3-sourced notes - insights a reviewer explicitly chose to
keep via a "Save to Expert Notes" button on Recipe Optimization, Trend
Analysis, Machine Settings vs Physical Properties Correlation, or
Root-Cause Assistant (both
their fixed-prompt sections and free-form Ask PI3 boxes). These are
tagged with their originating question and can be re-exported as the
same Word report the reviewer originally saw.
"""

import json

import streamlit as st

import ai_assistant
import reports
from access_control import can_use_page
from auth import current_user, logout_button, require_login
from db import CONFIDENCE_LEVELS, ExpertNote, FoamGrade, ProductionRun, get_session, init_db
from helpers import (
    clickable_table,
    company_id_for_plant,
    delete_with_confirm,
    expert_note_foam_grade_id_for_link,
    expert_note_link_label,
    expert_note_plant_id_for_link,
    log_export_click,
    page_setup,
    render_function_action_intro,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, grade_ids_for_company, run_ids_for_company

page_setup("Expert Notes")
init_db()
require_login()
logout_button()

st.title("Expert Notes")
render_function_action_intro(
    function_text=(
        "Captures qualitative expert knowledge that doesn't fit a structured field - a hunch "
        "about why a batch behaved oddly, a supplier quirk, a process tip - linked to a "
        "production run, a foam grade, or a foam family. It also shows the PI3-sourced notes "
        "a reviewer chose to keep from Recipe Optimization, Trend Analysis, Process-Property "
        "Correlation, Root-Cause Assistant, or Machine Settings Optimization, each tagged with "
        "its originating question and re-exportable as the same Word report the reviewer "
        "originally saw. When PI3 connectivity is enabled for the relevant plant, a note saved "
        "here also feeds PI3 so future free-form Ask PI3 questions and Root-Cause Assistant "
        "comparisons can retrieve it. An aggregate report further down breaks all notes in scope "
        "down by confidence level, source, and linked-entity type."
    ),
    action_text=(
        "Pick what the note is about (a production run, foam grade, or foam family), write it, set a "
        "confidence level, and save - there's no other structured field to fill in, so use this "
        "for anything worth remembering that the rest of the app has no place for. Click a "
        "PI3-sourced note to re-download its original Word report, or edit/delete any note the "
        "same way as elsewhere in the app."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("expert_notes", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="expert_notes_company_filter"
)
active_company_id = company.id if company else None
scoped_run_ids = run_ids_for_company(session, active_company_id)
scoped_grade_ids = grade_ids_for_company(session, active_company_id)

LINK_TYPES = {
    "Production Run": "production_run",
    "Foam Grade": "foam_grade",
    "Foam Family": "product_family",
}


runs = (
    apply_scope(session.query(ProductionRun), ProductionRun.id, scoped_run_ids)
    .order_by(ProductionRun.created_at.desc())
    .all()
)
grades = (
    apply_scope(session.query(FoamGrade), FoamGrade.id, scoped_grade_ids)
    .order_by(FoamGrade.grade_name)
    .all()
)
# Foam families offered here are derived from this same scoped grades list
# (any family with at least one in-scope grade), not a separate company
# scope query - keeps "what's offered to link to" consistent with the
# Foam Grade option above rather than a second, possibly-diverging notion
# of company scope for families specifically.
families = sorted({g.product_family for g in grades if g.product_family}, key=lambda f: f.name)

st.subheader("Add an expert note")
# The "Link to" selector lives outside the form on purpose: widgets inside
# an st.form don't trigger a rerun until the form is submitted, so with it
# inside the form, switching from "Production Run" to "Foam Family" would
# leave the wrong entity dropdown (still "Production run") showing until
# the reviewer hit Save - by then it's too late to pick the right one.
# Keeping it outside means the entity dropdown below updates immediately.
link_type_choice = st.selectbox("Link to *", list(LINK_TYPES.keys()), key="new_note_link_type")
entity_type = LINK_TYPES[link_type_choice]

with st.form("add_expert_note"):
    if entity_type == "production_run":
        if not runs:
            st.warning("No production runs yet - create one on the Production Run page first.")
        entity = st.selectbox(
            "Production run *", runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
        )
    elif entity_type == "foam_grade":
        if not grades:
            st.warning("No foam grades yet - create one on the Product Family & Foam Grade page first.")
        entity = st.selectbox("Foam grade *", grades, format_func=lambda g: g.grade_name)
    else:
        if not families:
            st.warning("No foam families yet - create one on the Product Family & Foam Grade page first.")
        entity = st.selectbox("Foam family *", families, format_func=lambda f: f.name)
    note_text = st.text_area("Note *")
    confidence_level = st.selectbox("Confidence level", CONFIDENCE_LEVELS, index=2)
    author = st.text_input("Author", value=user["display_name"])
    submitted = st.form_submit_button("Save note", disabled=not page_usable)
    if submitted and page_usable:
        if not entity:
            st.error("Nothing to link to - add a production run or foam grade first.")
        elif not note_text.strip():
            st.error("Note text is required.")
        else:
            note = ExpertNote(
                linked_entity_type=entity_type,
                linked_entity_id=entity.id,
                note_text=note_text.strip(),
                confidence_level=confidence_level,
                author=author,
                source="Manual",
            )
            plant_id = expert_note_plant_id_for_link(entity_type, entity.id, session)
            if ai_assistant.is_enabled_for_plant(session, plant_id):
                link_label = expert_note_link_label(entity_type, entity.id, session)
                doc_text = (
                    f"Expert note on {link_label}\n"
                    f"Confidence: {confidence_level}\nAuthor: {author or '—'}\n\n{note_text.strip()}"
                )
                note.vector_store_file_id = ai_assistant.push_document_to_vector_store(
                    link_label,
                    doc_text,
                    metadata={"plant_id": plant_id, "company_id": company_id_for_plant(plant_id, session)}
                    if plant_id
                    else None,
                )
            session.add(note)
            session.commit()
            st.success("Expert note saved." + (" Fed into PI3." if note.vector_store_file_id else ""))
            st.rerun()

st.divider()
st.subheader("Expert notes")

all_notes = session.query(ExpertNote).order_by(ExpertNote.created_at.desc()).all()
if active_company_id is None:
    notes = all_notes
else:
    # ExpertNote is polymorphic (linked_entity_type + linked_entity_id can
    # point at a production run, trial record, foam grade, or foam family).
    # Scope each kind against the id set already computed above for that
    # company. Missing the product_family branch here would make any note
    # PI3 saved from a "foam family" analysis (see analysis_unit_picker,
    # helpers.py) invisible to the very company that created it - not just
    # a cosmetic gap, a real "where did my note go" bug.
    scoped_run_id_set = set(scoped_run_ids) if scoped_run_ids else set()
    scoped_grade_id_set = set(scoped_grade_ids) if scoped_grade_ids else set()
    scoped_family_id_set = {f.id for f in families}
    notes = [
        n
        for n in all_notes
        if (n.linked_entity_type == "production_run" and n.linked_entity_id in scoped_run_id_set)
        or (n.linked_entity_type == "foam_grade" and n.linked_entity_id in scoped_grade_id_set)
        or (n.linked_entity_type == "product_family" and n.linked_entity_id in scoped_family_id_set)
    ]

# ---------------------------------------------------------------------------
# Expert Notes Report (Context / Analysis / Conclusions) - an always-
# visible aggregate over the exact `notes` list already scoped above (by
# confidence level, source, and linked-entity type), distinct from the
# existing conditional per-note "Download as Word" button further down
# (kept as-is - that button re-exports one PI3-sourced note's own original
# report, this is a standing breakdown across every note in scope).
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Expert Notes Report")
en_scope_label = company.name if company else "All companies"
st.caption(f"Context, analysis, and conclusions for expert notes in scope: {en_scope_label}.")
expert_notes_report_data = reports.build_expert_notes_report_data(session, notes, en_scope_label)
en_rc1, en_rc2 = st.columns(2)
en_rc1.metric("Total notes", expert_notes_report_data["total"])
en_rc2.metric(
    "Fed into PI3",
    f"{expert_notes_report_data['in_pi3_count']} of {expert_notes_report_data['total']}"
    if expert_notes_report_data["total"] else "—",
)
en_dl1, en_dl2 = st.columns(2)
en_dl1.download_button(
    "Download Word", data=reports.render_expert_notes_report_docx(expert_notes_report_data),
    file_name="expert_notes_report.docx",
    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    key="expert_notes_report_docx",
    on_click=log_export_click, args=("expert_notes_report_docx",),
    kwargs={"description": en_scope_label},
)
en_dl2.download_button(
    "Download Excel", data=reports.render_expert_notes_report_excel(expert_notes_report_data),
    file_name="expert_notes_report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="expert_notes_report_excel",
    on_click=log_export_click, args=("expert_notes_report_excel",),
    kwargs={"description": en_scope_label},
)

if not notes:
    st.info("No expert notes recorded yet.")
else:
    note_rows = [
        {
            "Linked to": expert_note_link_label(n.linked_entity_type, n.linked_entity_id, session),
            "Note": (n.note_text[:120] + "…") if len(n.note_text) > 120 else n.note_text,
            "Source": n.source or "Manual",
            "Confidence": n.confidence_level,
            "Author": n.author or "",
            "Created": n.created_at,
            "In PI3": "Yes" if n.vector_store_file_id else "No",
        }
        for n in notes
    ]
    st.caption("Click a row to edit (and optionally delete) that note.")
    idx = clickable_table(note_rows, key="expert_notes_table")
    if idx is not None and idx < len(notes):
        st.session_state["note_selected_id"] = notes[idx].id
    else:
        st.session_state.pop("note_selected_id", None)

    selected_id = st.session_state.get("note_selected_id")
    selected = next((n for n in notes if n.id == selected_id), None)

    if selected:
        st.markdown(
            f"**Edit note on {expert_note_link_label(selected.linked_entity_type, selected.linked_entity_id, session)}**"
        )
        if selected.source == "PI3":
            st.caption(f"Source: PI3, from the question “{selected.pi3_question or '—'}”")
            grade_id = expert_note_foam_grade_id_for_link(selected.linked_entity_type, selected.linked_entity_id, session)
            grade = session.get(FoamGrade, grade_id) if grade_id else None
            plant_id = expert_note_plant_id_for_link(selected.linked_entity_type, selected.linked_entity_id, session)
            report_data = reports.build_pi3_qa_report_data(
                question=selected.pi3_question,
                answer=selected.note_text,
                tool_log=json.loads(selected.pi3_tool_log_json) if selected.pi3_tool_log_json else [],
                plant_name=reports.plant_label(session, plant_id),
                foam_grade_name=grade.grade_name if grade else None,
                asked_by=selected.author,
                asked_at=selected.created_at,
            )
            st.download_button(
                "Download as Word (.docx)",
                data=reports.render_pi3_qa_report_docx(report_data),
                file_name=f"pi3_report_expert_note_{selected.id}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"expert_note_{selected.id}_download_docx",
                on_click=log_export_click, args=("expert_note_pi3_docx",),
                kwargs={"description": f"Expert Note #{selected.id}"},
            )
        with st.form(f"edit_note_{selected.id}"):
            e_text = st.text_area("Note *", value=selected.note_text, key=f"edit_note_text_{selected.id}")
            e_confidence = st.selectbox(
                "Confidence level", CONFIDENCE_LEVELS,
                index=CONFIDENCE_LEVELS.index(selected.confidence_level) if selected.confidence_level in CONFIDENCE_LEVELS else 2,
                key=f"edit_note_conf_{selected.id}",
            )
            e_author = st.text_input("Author", value=selected.author or "", key=f"edit_note_author_{selected.id}")
            if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                if not e_text.strip():
                    st.error("Note text is required.")
                else:
                    plant_id = expert_note_plant_id_for_link(selected.linked_entity_type, selected.linked_entity_id, session)
                    if ai_assistant.is_enabled_for_plant(session, plant_id):
                        if selected.vector_store_file_id:
                            ai_assistant.delete_document_from_vector_store(selected.vector_store_file_id)
                        link_label = expert_note_link_label(selected.linked_entity_type, selected.linked_entity_id, session)
                        doc_text = (
                            f"Expert note on {link_label}\n"
                            f"Confidence: {e_confidence}\nAuthor: {e_author or '—'}\n\n{e_text.strip()}"
                        )
                        selected.vector_store_file_id = ai_assistant.push_document_to_vector_store(
                            link_label,
                            doc_text,
                            metadata={"plant_id": plant_id, "company_id": company_id_for_plant(plant_id, session)}
                            if plant_id
                            else None,
                        )
                    selected.note_text = e_text.strip()
                    selected.confidence_level = e_confidence
                    selected.author = e_author
                    session.commit()
                    st.success("Expert note updated.")
                    st.rerun()

        def _do_delete_note(_session=session, _id=selected.id, _file_id=selected.vector_store_file_id):
            if _file_id:
                ai_assistant.delete_document_from_vector_store(_file_id)
            _session.query(ExpertNote).filter(ExpertNote.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("note_selected_id", None)

        if page_usable:
            delete_with_confirm(
                "this expert note", _do_delete_note, key_prefix=f"note_{selected.id}",
                extra_warning=(
                    "This is a leaf record — deleting it has no other effects (its copy in "
                    "PI3, if any, is removed too)."
                ),
            )
        else:
            st.caption("View-only access - deleting is restricted for your role.")

        if st.button("Clear selection", key="clear_note_selection"):
            st.session_state.pop("note_selected_id", None)
            st.rerun()
