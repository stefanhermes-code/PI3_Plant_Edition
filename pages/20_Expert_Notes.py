"""Screen: Expert Notes

Captures qualitative expert knowledge - the kind of thing that lives in a
technical person's head or a stray email, not a structured measurement -
linked to a trial or foam grade. This is the raw material PI3 needs: when
PI3 connectivity is enabled for the relevant plant, saving a note here
also feeds it into PI3 so future Similar Case Retrieval searches and
Root-Cause Assistant reasoning can retrieve it.
"""

import streamlit as st

import ai_assistant
from auth import current_user, logout_button, require_login
from db import CONFIDENCE_LEVELS, ExpertNote, FoamGrade, TrialRecord, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_setup

page_setup("Expert Notes")
init_db()
require_login()
logout_button()

st.title("Expert Notes")
st.caption(
    "Qualitative knowledge that doesn't fit a structured field - a hunch about why a "
    "batch behaved oddly, a supplier quirk, a process tip. Linked to a trial or foam "
    "grade. When PI3 connectivity is enabled for the relevant plant, saving a note "
    "here also feeds PI3 so Similar Case Retrieval and the Root-Cause Assistant can "
    "find it later."
)
session = get_session()
user = current_user()

LINK_TYPES = {"Trial / Experiment": "trial_record", "Foam Grade": "foam_grade"}


def _plant_id_for_link(entity_type, entity_id, session):
    if entity_type == "trial_record":
        t = session.get(TrialRecord, entity_id)
        return t.production_run.plant_id if t else None
    if entity_type == "foam_grade":
        g = session.get(FoamGrade, entity_id)
        return g.product_family.plant_id if g else None
    return None


def _link_label(entity_type, entity_id, session):
    if entity_type == "trial_record":
        t = session.get(TrialRecord, entity_id)
        return f"Trial #{t.id} — {t.production_run.foam_grade.grade_name}" if t else f"Trial #{entity_id} (deleted)"
    if entity_type == "foam_grade":
        g = session.get(FoamGrade, entity_id)
        return f"Foam Grade: {g.grade_name}" if g else f"Foam Grade #{entity_id} (deleted)"
    return f"{entity_type} #{entity_id}"


trials = session.query(TrialRecord).order_by(TrialRecord.created_at.desc()).all()
grades = session.query(FoamGrade).order_by(FoamGrade.grade_name).all()

st.subheader("Add an expert note")
with st.form("add_expert_note"):
    link_type_choice = st.selectbox("Link to *", list(LINK_TYPES.keys()))
    entity_type = LINK_TYPES[link_type_choice]
    if entity_type == "trial_record":
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
            )
            plant_id = _plant_id_for_link(entity_type, entity.id, session)
            if ai_assistant.is_enabled_for_plant(session, plant_id):
                link_label = _link_label(entity_type, entity.id, session)
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
            "Linked to": _link_label(n.linked_entity_type, n.linked_entity_id, session),
            "Note": (n.note_text[:120] + "…") if len(n.note_text) > 120 else n.note_text,
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
            f"**Edit note on {_link_label(selected.linked_entity_type, selected.linked_entity_id, session)}**"
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
                    plant_id = _plant_id_for_link(selected.linked_entity_type, selected.linked_entity_id, session)
                    if ai_assistant.is_enabled_for_plant(session, plant_id):
                        if selected.vector_store_file_id:
                            ai_assistant.delete_document_from_vector_store(selected.vector_store_file_id)
                        link_label = _link_label(selected.linked_entity_type, selected.linked_entity_id, session)
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
