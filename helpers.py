"""Shared UI helpers for PI3 Plant Edition pages."""

import datetime as dt
import json

import pandas as pd
import streamlit as st

import ai_assistant
import reports
from auth import current_user
from db import ExpertNote, FoamGrade, ProductionRun, RecipeVersion, TrialRecord, get_session


def expert_note_plant_id_for_link(entity_type, entity_id, session):
    """Which plant a given Expert Note "link to" target belongs to, for the
    is_enabled_for_plant() check before pushing to PI3's vector store.
    Shared by pages/20_Expert_Notes.py and render_save_to_expert_notes_button
    below, so both resolve a link the same way."""
    if entity_type == "production_run":
        r = session.get(ProductionRun, entity_id)
        return r.plant_id if r else None
    if entity_type == "trial_record":
        t = session.get(TrialRecord, entity_id)
        return t.production_run.plant_id if t else None
    if entity_type == "foam_grade":
        g = session.get(FoamGrade, entity_id)
        return g.product_family.plant_id if g else None
    return None


def expert_note_link_label(entity_type, entity_id, session):
    """Human-readable label for a given Expert Note "link to" target, used
    both on the Expert Notes screen and as the document title when a note
    is pushed into PI3's vector store."""
    if entity_type == "production_run":
        r = session.get(ProductionRun, entity_id)
        return f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}" if r else f"Run #{entity_id} (deleted)"
    if entity_type == "trial_record":
        t = session.get(TrialRecord, entity_id)
        return f"Trial #{t.id} — {t.production_run.foam_grade.grade_name}" if t else f"Trial #{entity_id} (deleted)"
    if entity_type == "foam_grade":
        g = session.get(FoamGrade, entity_id)
        return f"Foam Grade: {g.grade_name}" if g else f"Foam Grade #{entity_id} (deleted)"
    return f"{entity_type} #{entity_id}"


def expert_note_foam_grade_id_for_link(entity_type, entity_id, session):
    """Which foam grade (if any) a given Expert Note "link to" target
    belongs to - used to populate the "Foam grade" field when regenerating
    a PI3-sourced note's Word report on demand."""
    if entity_type == "foam_grade":
        return entity_id
    if entity_type == "production_run":
        r = session.get(ProductionRun, entity_id)
        return r.foam_grade_id if r else None
    if entity_type == "trial_record":
        t = session.get(TrialRecord, entity_id)
        return t.production_run.foam_grade_id if t else None
    return None


def page_setup(title: str):
    """Kept for compatibility with existing pages, which all call this as
    their first Streamlit command. Page config, sidebar logo, and global
    styling are now set once in app.py (which runs first on every page view
    under st.navigation), so this is intentionally a no-op — calling
    st.set_page_config() a second time would raise an error."""
    pass


def activate_recipe_version(session, foam_grade_id, new_version):
    """Marks new_version as the active recipe for its foam grade, and
    deactivates whatever was active before it. Recipe versions don't
    coexist in production - a new one replaces the previous one - so
    exactly one version per foam grade should have is_active=True at a
    time. Call this right after adding+flushing new_version, before
    session.commit(). Does not touch approval_status - a version can be
    Approved but no longer active (superseded by a later revision)."""
    session.query(RecipeVersion).filter(
        RecipeVersion.foam_grade_id == foam_grade_id,
        RecipeVersion.id != new_version.id,
    ).update({"is_active": False}, synchronize_session=False)
    new_version.is_active = True


def next_version_label(current_label, existing_count):
    """Best-effort auto-generated label for the next recipe version: if
    the current label ends in a number (e.g. "28-MH-05"), increments that
    number (preserving its zero-padding width), matching the meaningful
    product-code style labels this app's users already use. Falls back to
    appending "-v{n}" if no trailing number is found. Always shown to the
    user as an editable suggestion, never silently applied - two versions
    for the same grade could otherwise collide on a generated label."""
    import re

    match = re.search(r"(\d+)$", current_label or "")
    if match:
        num = match.group(1)
        next_num = str(int(num) + 1).zfill(len(num))
        return current_label[: match.start()] + next_num
    return f"{(current_label or 'v').strip()}-v{existing_count + 1}"


def render_data_table(df, max_height=None):
    """Renders a pandas DataFrame as a left-aligned, content-width HTML
    table with left-aligned cell text.

    st.dataframe(..., use_container_width=True) always stretches to the
    full page width no matter how little data there is, which spreads a
    handful of short rows across the whole screen and makes them harder
    to read, not easier. This sizes to the actual content instead and
    aligns it (and its cell text) to the left, matching how a plain data
    table normally reads. Deliberately avoids pandas' Styler (its
    HTML-rendering path requires jinja2, which isn't otherwise a
    dependency of this app) by building the HTML directly.

    max_height, if given (e.g. "400px"), wraps the table in a scrollable
    container - use this for tables that could have many rows, so a long
    listing doesn't push the rest of the page down indefinitely."""

    def _esc(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    header_cells = "".join(
        f"<th style='text-align:left; padding:6px 16px; border-bottom:2px solid #1B6FA8; "
        f"position:sticky; top:0; background:white;'>{_esc(c)}</th>"
        for c in df.columns
    )
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(
            f"<td style='text-align:left; padding:6px 16px; border-bottom:1px solid #E4ECF1;'>{_esc(v)}</td>"
            for v in row
        )
        body_rows.append(f"<tr>{cells}</tr>")
    table_html = (
        "<table style='border-collapse:collapse;'>"
        f"<thead><tr>{header_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )
    if max_height:
        html = (
            f"<div style='overflow:auto; max-height:{max_height}; display:inline-block;'>{table_html}</div>"
        )
    else:
        html = f"<div style='display:inline-block;'>{table_html}</div>"
    st.markdown(html, unsafe_allow_html=True)


def confidence_badge(level: str) -> str:
    colors = {
        "Confirmed": "🟢",
        "Likely": "🟡",
        "Unconfirmed": "⚪",
        "Rejected": "🔴",
    }
    return f"{colors.get(level, '⚪')} {level or 'Unconfirmed'}"


def to_df(rows, columns=None):
    if not rows:
        return pd.DataFrame(columns=columns or [])
    return pd.DataFrame([r.__dict__ for r in rows]).drop(columns=["_sa_instance_state"], errors="ignore")


def selectbox_from_query(label, session, model, name_field="name", allow_none=True, key=None):
    """Render a selectbox populated from a DB query, return the selected object (or None)."""
    records = session.query(model).all()
    options = [None] if allow_none else []
    options += records
    return st.selectbox(
        label,
        options,
        format_func=lambda r: "—" if r is None else getattr(r, name_field, str(r)),
        key=key,
    )


def combine_date_time(label, key_prefix, default_date=None, default_time=None):
    """Render a date_input + time_input pair side by side and return a
    combined datetime.datetime. Used wherever a phase boundary, event, or
    sample timestamp needs both a date and a time from the operator."""
    c1, c2 = st.columns(2)
    d = c1.date_input(f"{label} — date", value=default_date or dt.date.today(), key=f"{key_prefix}_date")
    t = c2.time_input(f"{label} — time", value=default_time or dt.datetime.now().time(), key=f"{key_prefix}_time")
    return dt.datetime.combine(d, t)


def parse_dt(value):
    """Best-effort parse of a CSV/Excel cell into a datetime, or None."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def parse_bool(value):
    """Best-effort parse of a CSV/Excel cell into a bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def selection_rows(event):
    """Best-effort extraction of selected row indices from a
    st.dataframe(..., on_select="rerun") return value, tolerant of the
    exact attribute/dict shape Streamlit uses."""
    if event is None:
        return []
    sel = getattr(event, "selection", None)
    if sel is None:
        try:
            sel = event["selection"]
        except Exception:
            return []
    rows = getattr(sel, "rows", None)
    if rows is None:
        try:
            rows = sel["rows"]
        except Exception:
            return []
    return list(rows or [])


def clickable_table(rows, key):
    """Render rows (list of dicts) as a single-row-selectable table. Returns
    the selected row's index, or None if nothing is selected. Used across
    every "list + edit + delete" page so row-selection works identically
    everywhere."""
    if not rows:
        return None
    event = st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    sel = selection_rows(event)
    return sel[0] if sel else None


def delete_with_confirm(label, on_confirm, key_prefix, extra_warning=""):
    """Render a checkbox + delete button gate, calling on_confirm() and
    rerunning only once the operator has explicitly ticked the confirm box.
    Shared by every page with a delete action so the confirmation UX (and
    the requirement to tick a box before the button becomes clickable) is
    consistent app-wide."""
    st.markdown(f"**Delete {label}**")
    if extra_warning:
        st.warning(extra_warning)
    confirm = st.checkbox(f"I understand — permanently delete {label}.", key=f"{key_prefix}_confirm")
    if st.button(f"Delete {label}", key=f"{key_prefix}_btn", type="primary", disabled=not confirm):
        on_confirm()
        st.success(f"{label} deleted.")
        st.rerun()


def show_pending_banner(key):
    """Show a one-shot success banner stashed in session_state by an action
    that immediately called st.rerun() right after it. A plain st.success()
    called right before st.rerun() gets wiped before the user ever sees it,
    since the rerun restarts the script - this is why "Confirm import"
    buttons across the app could look like they silently did nothing, which
    led to operators clicking Confirm a second time and duplicating rows.
    Call this near the top of a page/section, before the action that might
    set the banner via set_pending_banner()."""
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


def set_pending_banner(key, message):
    """Stash a success message so show_pending_banner() displays it after
    the immediate st.rerun() that follows a successful action."""
    st.session_state[key] = message


def dedupe_import_rows(rows, existing_keys, key_func):
    """Split CSV-import rows into (new_rows, duplicate_rows) based on
    key_func(row) already being present in existing_keys (a set, mutated in
    place as rows are accepted). Used by every "Confirm import" button so
    that clicking it twice - e.g. because the previous success message
    wasn't visibly persistent - can't silently insert the same rows again."""
    new_rows, dup_rows = [], []
    for row in rows:
        k = key_func(row)
        if k in existing_keys:
            dup_rows.append(row)
        else:
            new_rows.append(row)
            existing_keys.add(k)
    return new_rows, dup_rows


def csv_excel_uploader(required_cols, optional_cols=None, key=None):
    """Render a file uploader for bulk CSV/Excel import, parse it, and check
    that the required columns are present. Used by every "CSV / Excel
    import" tab across the app so the upload/parse/column-check boilerplate
    (and its error messages) stay identical everywhere.

    Returns (df, filename) once a valid file with all required columns has
    been uploaded, or (None, None) otherwise (an st.error/st.caption has
    already been shown as appropriate - callers don't need to repeat that).
    """
    optional_cols = optional_cols or []
    cols_caption = "Required columns: " + ", ".join(required_cols)
    if optional_cols:
        cols_caption += ". Optional columns: " + ", ".join(optional_cols)
    st.caption(cols_caption)

    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key=key)
    if not uploaded:
        return None, None

    try:
        df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        return None, None

    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
        return None, None

    return df, uploaded.name


def render_pi3_docx_download(
    session, plant_id, key_prefix, question_label, answer, tool_log=None,
    page_context="", foam_grade_id=None,
):
    """Shared 'Download as Word (.docx)' button for any PI3-generated
    answer - both the older fixed-prompt sections (Recipe Optimization's
    formulation recommendation, Trend Analysis's and Process-Property
    Correlation's interpretation) and the free-form Ask PI3 box below them
    call this, so every PI3 answer on every page can be exported the same
    way, with identical formatting (see reports.render_pi3_qa_report_docx).

    `question_label` is what appears as "Question asked" in the export -
    for the free-form box this is literally what the reviewer typed; for a
    fixed-prompt section there's no user-typed question, so callers pass a
    short description of what was requested instead (e.g. "PI3 formulation
    recommendation for <grade>"). `tool_log` is optional and only
    populated for the free-form box, which goes through the tool-calling
    agent - fixed-prompt sections call ai_assistant.ask_assistant()
    directly (file_search only, no tools), so they have none to show."""
    grade_name = None
    if foam_grade_id:
        grade = session.get(FoamGrade, foam_grade_id)
        grade_name = grade.grade_name if grade else None

    report_data = reports.build_pi3_qa_report_data(
        question=question_label,
        answer=answer,
        tool_log=tool_log or [],
        page_context=page_context,
        plant_name=reports.plant_label(session, plant_id),
        foam_grade_name=grade_name,
        asked_by=current_user().get("display_name"),
    )
    st.download_button(
        "Download as Word (.docx)",
        data=reports.render_pi3_qa_report_docx(report_data),
        file_name=f"pi3_report_{key_prefix}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key=f"{key_prefix}_download_docx",
    )


def render_save_to_expert_notes_button(session, key_prefix, answer, question_label, link_type, entity_id, tool_log=None):
    """Shared 'Save to Expert Notes' button for any PI3-generated answer -
    lets the reviewer explicitly keep an answer worth remembering, rather
    than every PI3 interaction being saved automatically (which would fill
    Expert Notes with one-off/throwaway questions no one wants to see
    again). Saved notes are tagged source="PI3" and, same as a manually-
    typed expert note, pushed into PI3's own vector store if PI3 is enabled
    for the relevant plant - so a genuinely useful PI3 insight can surface
    again in future Similar Case Retrieval / Root-Cause Assistant searches,
    same as human-authored knowledge.

    `link_type` is one of the Expert Notes "link to" types ("foam_grade",
    "production_run", "trial_record"), `entity_id` the id of that record.

    Guards against saving the same answer twice: once saved, the button is
    replaced with a confirmation until a new answer replaces this one -
    callers must pop f"{key_prefix}_saved_note_id" from session_state
    whenever they store a new answer under f"{key_prefix}_answer" (or the
    page's equivalent), or this will keep showing "already saved" for an
    answer that was never actually saved."""
    if entity_id is None:
        return
    saved_id = st.session_state.get(f"{key_prefix}_saved_note_id")
    if saved_id:
        st.caption("✓ Saved to Expert Notes.")
        return
    if st.button("Save to Expert Notes", key=f"{key_prefix}_save_note_btn"):
        plant_id = expert_note_plant_id_for_link(link_type, entity_id, session)
        note = ExpertNote(
            linked_entity_type=link_type,
            linked_entity_id=entity_id,
            note_text=answer,
            confidence_level="Unconfirmed",
            author=current_user().get("display_name"),
            source="PI3",
            pi3_question=question_label,
            pi3_tool_log_json=json.dumps(tool_log) if tool_log else None,
        )
        if ai_assistant.is_enabled_for_plant(session, plant_id):
            link_label = expert_note_link_label(link_type, entity_id, session)
            doc_text = f"PI3 insight on {link_label}\nQuestion: {question_label}\n\n{answer}"
            note.vector_store_file_id = ai_assistant.push_document_to_vector_store(
                link_label, doc_text, metadata={"plant_id": plant_id} if plant_id else None
            )
        session.add(note)
        session.commit()
        st.session_state[f"{key_prefix}_saved_note_id"] = note.id
        st.rerun()


def render_ask_pi3_section(
    session, plant_id, default_foam_grade_id, page_context, sample_questions, key_prefix,
    note_link_type="foam_grade", note_entity_id=None,
):
    """Free-form 'ask PI3 anything about this plant's data' box, shared by
    Recipe Optimization, Process-Property Correlation, and Trend Analysis -
    this is the same spot on each page that already had a fixed, single-
    purpose PI3 prompt; this section sits alongside that one rather than
    replacing it, so the existing tested recommendation/interpretation
    still works exactly as before.

    Silently renders nothing if PI3 isn't configured or isn't enabled for
    this plant - the existing fixed-prompt section above this one on each
    page already shows the right explanation for that (see
    ai_assistant.availability_status), so this avoids showing the same
    "enable PI3" message twice on one page.

    `page_context` is a short plain-language string describing what page/
    grade/property the reviewer is currently looking at, so PI3 can
    disambiguate an underspecified question ("is this drifting" ->
    drifting for which property, on which page). `sample_questions` is a
    list of ready-made example questions shown in a dropdown - answers the
    "how would a user know what's answerable" problem without requiring
    them to write SQL-shaped questions themselves.
    """
    if ai_assistant.availability_status(session, plant_id) != "enabled":
        return

    st.markdown("**Ask PI3 your own question**")
    st.caption(
        "Ask anything about this plant's own production data - PI3 checks the actual recorded "
        "numbers (it never guesses) and can also draw on expert notes and historical cases for "
        "context. Answers are historical reference for your own investigation, not instructions."
    )

    sample_key = f"{key_prefix}_sample"
    question_key = f"{key_prefix}_question"

    def _apply_sample_question():
        # Widgets that pass both `key` and `value` only honor `value` on their
        # very first render - once session_state has an entry for that key
        # (which it does after the first rerun), later `value=` changes are
        # silently ignored. That meant picking a different sample question
        # from the dropdown below never actually updated the text area, so
        # it stayed empty and the "Ask PI3" button stayed disabled. Writing
        # straight into st.session_state[question_key] from this on_change
        # callback runs BEFORE the text_area widget is (re)built, so it picks
        # up the new value like any other externally-set session_state entry.
        chosen = st.session_state.get(sample_key)
        st.session_state[question_key] = "" if chosen in (None, "Type my own...") else chosen

    if sample_questions:
        st.selectbox(
            "Example questions",
            ["Type my own..."] + list(sample_questions),
            key=sample_key,
            on_change=_apply_sample_question,
        )

    question = st.text_area("Your question", key=question_key)

    if st.button("Ask PI3", key=f"{key_prefix}_ask_btn", disabled=not question.strip()):
        with st.spinner("Using PI3..."):
            answer, tool_log = ai_assistant.ask_plant_question(
                session,
                plant_id,
                question,
                default_foam_grade_id=default_foam_grade_id,
                page_context=page_context,
            )
        if answer:
            st.session_state[f"{key_prefix}_answer"] = answer
            st.session_state[f"{key_prefix}_tool_log"] = tool_log
            st.session_state[f"{key_prefix}_asked"] = question
            st.session_state.pop(f"{key_prefix}_saved_note_id", None)

    answer = st.session_state.get(f"{key_prefix}_answer")
    if answer:
        st.caption(f"You asked: {st.session_state.get(f'{key_prefix}_asked', '')}")
        st.write(answer)
        tool_log = st.session_state.get(f"{key_prefix}_tool_log") or []
        st.caption("Confirm through your own investigation before acting on this.")

        dl_col, save_col = st.columns([1, 1])
        with dl_col:
            render_pi3_docx_download(
                session,
                plant_id,
                key_prefix=key_prefix,
                question_label=st.session_state.get(f"{key_prefix}_asked", ""),
                answer=answer,
                tool_log=tool_log,
                page_context=page_context,
                foam_grade_id=default_foam_grade_id,
            )
        with save_col:
            render_save_to_expert_notes_button(
                session,
                key_prefix=key_prefix,
                answer=answer,
                question_label=st.session_state.get(f"{key_prefix}_asked", ""),
                link_type=note_link_type,
                entity_id=note_entity_id if note_entity_id is not None else default_foam_grade_id,
                tool_log=tool_log,
            )
