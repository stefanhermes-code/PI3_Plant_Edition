"""Screen: Expert Notes

Captures qualitative expert knowledge - the kind of thing that lives in a
technical person's head or a stray email, not a structured measurement -
linked to a production run (the common case), a trial/experiment, or a
foam grade. This is the raw material PI3 needs: when PI3 connectivity is
enabled for the relevant plant, saving a note here also feeds it into
PI3 so future Similar Case Retrieval searches and Root-Cause Assistant
reasoning can retrieve it.

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
from auth import current_user, logout_button, require_login
from db import CONFIDENCE_LEVELS, ExpertNote, FoamGrade, ProductionRun, TrialRecord, get_session, init_db
from helpers import (
    clickable_table,
    delete_with_confirm,
    expert_note_foam_grade_id_for_link,
    expert_note_link_label,
    expert_note_plant_id_for_link,
    page_setup,
    render_function_action_intro,
)

page_setup("Expert Notes")
init_db()
require_login()
logout_button()

st.title("Expert Notes")
render_function_action_intro(
    function_text=(
        "Captures qualitative expert knowledge that doesn't fit a structured field - a hunch "
        "about why a batch behaved oddly, a supplier quirk, a process tip - linked to a "
        "production run, a trial/experiment, or a foam grade. It also shows the PI3-sourced notes "
        "a reviewer chose to keep from Recipe Optimization, Trend Analysis, Process-Property "
        "Correlation, or Root-Cause Assistant, each tagged with its originating question and "
        "re-exportable as the same Word report the reviewer originally saw. When PI3 connectivity "
        "is enabled for the relevant plant, a note saved here also feeds PI3 so future Similar "
        "Case Retrieval searches and Root-Cause Assistant comparisons can retrieve it."
    ),
    action_text=(
        "Pick what the note is about (a production run, trial, or foam grade), write it, set a "
        "confidence level, and save - there's no other structured field to fill in, so use this "
        "for anything worth remembering that the rest of the app has no place for. Click a "
        "PI3-sourced note to re-download its original Word report, or edit/delete any note the "
        "same way as elsewhere in the app."
    ),
)
session = get_session()
user = current_user()

LINK_TYPES = {
    "Production Run": "production_run",
    "Trial / Experiment": "trial_record",
    "Foam Grade": "foam_grade",
}


runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()
trials = session.query(TrialRecord).order_by(TrialRecord.created_at.desc()).all()
grades = session.query(FoamGrade).order_by(FoamGrade.grade_name).all()

st.subheader("Add an expert note")
# The "Link to" selector lives outside the form on purpose: widgets inside
# an st.form don't trigger a rerun until the form is submitted, so with it
# inside the form, switching from "Production Run" to "Trial / Experiment"
# would leave the wrong entity dropdown (still "Production run") showing
# until the reviewer hit Save - by then it's too late to pick a trial.
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
    elif entity_type == "trial_record":
        if not trials:
            st.warning("No trials yet - create one on the Trial / Experiment page first.")
        entity = st.selectbox(
            "Trial *", trials,
            format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name} ({t.status})",
        )
    else:
        if not grades:
            st.warning("No foam grades yet - create one on the Product Family & Foam Grade page first.")
        entity = st.selectbox("Foam grade *", grades, format_func=lambda g: g.grade_name)
    note_text = st.text_area("Note *")
    confidence_level = st.selectbox("Confidence level", CONFIDENCE_LEVELS, index=2)
    author = st.text_input("Author", value=user["display_name"])
    submitted = st.form_submit_button("Save note")
    if submitted:
        if not entity:
            st.error("Nothing to link to - add a trial or foam grade first.")
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
                    link_label, doc_text, metadata={"plant_id": plant_id} if plant_id else None
                )
            session.add(note)
            session.commit()
            st.success("Expert note saved." + (" Fed into PI3." if note.vector_store_file_id else ""))
            st.rerun()

st.divider()
st.subheader("Expert notes")

notes = session.query(ExpertNote).order_by(ExpertNote.created_at.desc()).all()
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
    if idx is not None:
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
            )
        with st.form(f"edit_note_{selected.id}"):
            e_text = st.text_area("Note *", value=selected.note_text, key=f"edit_note_text_{selected.id}")
            e_confidence = st.selectbox(
                "Confidence level", CONFIDENCE_LEVELS,
                index=CONFIDENCE_LEVELS.index(selected.confidence_level) if selected.confidence_level in CONFIDENCE_LEVELS else 2,
                key=f"edit_note_conf_{selected.id}",
            )
            e_author = st.text_input("Author", value=selected.author or "", key=f"edit_note_author_{selected.id}")
            if st.form_submit_button("Save changes"):
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
                            link_label, doc_text, metadata={"plant_id": plant_id} if plant_id else None
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

        delete_with_confirm(
            "this expert note", _do_delete_note, key_prefix=f"note_{selected.id}",
            extra_warning=(
                "This is a leaf record — deleting it has no other effects (its copy in "
                "PI3, if any, is removed too)."
            ),
        )

        if st.button("Clear selection", key="clear_note_selection"):
            st.session_state.pop("note_selected_id", None)
            st.rerun()
