"""Shared audit/usage/pilot-learning logging helpers (Gate 6, Items 47-56 -
see PI3_Application_Changes_Needed.docx, section 3.2).

Every write in this module goes through session.add() + session.flush()
and deliberately does NOT call session.commit(): db.close_out_session()
(called once per Streamlit rerun) commits automatically, so a logging call
made partway through a page's normal flow rides along with whatever else
that rerun ends up committing rather than needing its own transaction.
flush() (not commit()) is still used so a just-created row's id is
available immediately in the same rerun - e.g. PI3Feedback needs the id of
the PI3InteractionLog row it's reacting to, and the docx/Expert-Notes flow
in helpers.py wants the interaction id to show a feedback control next to
the answer that was just generated.

THE AI AUDIT TRAIL IS INSERT-ONLY. Stefan's ruling, 19 Aug 2026: the
platform owner must not be able to intervene in the contents of the audit
trail - only to see whether a record is there or not. There is no function in
this module, and no screen in this application, that edits or deletes a
pi3_interaction_logs or pi3_interaction_reviews row. Reviews are appended
(log_pi3_review), never updated. The single exception is
pi3_interaction_logs.verification_message_shown, which the application sets
once when the verification notice is actually rendered - a system fact about
what was displayed, not content anyone types. Do not add an edit or delete
path here; if a correction is needed, it is another appended row.

Every function here is deliberately best-effort: a logging failure must
never break the reviewer's actual task (submitting a recipe, reading a
report, asking PI3 a question). Each function wraps its own session.add/
flush in a try/except and swallows the exception after rolling back just
that piece of work - it does not re-raise, and it does not call
st.error(), since a broken audit-log write is not something a plant
reviewer needs to see or act on.
"""

import datetime as dt
import random
import traceback as tb_module

from db import (
    ErrorLog,
    ExportLog,
    LoginEvent,
    PageLoadLog,
    PageViewEvent,
    PI3Feedback,
    PI3InteractionLog,
    PI3InteractionReview,
    RoleChangeLog,
)


def _safe_flush(session):
    """Flush just-added row(s); on failure, roll back only this write and
    swallow the error. Returns True on success, False on failure."""
    try:
        session.flush()
        return True
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        return False


def log_login_event(session, event_type, username_attempted=None, user_id=None, company_id=None, detail=None):
    """Item 47. event_type is 'login_success', 'login_failure', or 'logout'."""
    try:
        row = LoginEvent(
            user_id=user_id,
            username_attempted=username_attempted,
            company_id=company_id,
            event_type=event_type,
            detail=detail,
        )
        session.add(row)
        _safe_flush(session)
    except Exception:
        pass


def log_page_view_if_new(session, session_state, user_id, company_id, page_name):
    """Item 48. Logs one row per navigation to a page, not per Streamlit
    rerun - a rerun also fires on every widget interaction within the
    same page, which would otherwise inflate usage counts. session_state
    is the caller's st.session_state (passed in rather than imported here
    so this module has no Streamlit dependency); the last-logged page
    name is tracked under "_audit_last_page_logged"."""
    if session_state.get("_audit_last_page_logged") == page_name:
        return
    try:
        row = PageViewEvent(user_id=user_id, company_id=company_id, page_name=page_name)
        session.add(row)
        if _safe_flush(session):
            session_state["_audit_last_page_logged"] = page_name
    except Exception:
        pass


def log_page_load(session, page_name, duration_ms):
    """Added 2026-08-05 for the v2.0 performance audit's Performance-page
    expansion (page load time, by page). Called from app.py around the
    single st.navigation() pg.run() call - the one choke point every
    page's script runs through - so this fires for every page, on every
    rerun, without touching any individual page file. Unlike
    log_page_view_if_new above (deduped to once per navigation), this
    logs every single rerun on purpose: a rerun re-executes the whole page
    script, and "the app feels slow" was always about that per-rerun cost,
    not just the first load.

    Same best-effort + housekeeping convention as analytics._log_performance
    (PerformanceLog): a logging failure must never break page routing, and
    a ~2% chance per call of trimming rows older than 30 days keeps this
    table from growing unbounded given how much more often it's written
    than the once-per-navigation PageViewEvent."""
    try:
        session.add(PageLoadLog(page_name=page_name, duration_ms=round(duration_ms, 2)))
        _safe_flush(session)
        if random.random() < 0.02:
            cutoff = dt.datetime.utcnow() - dt.timedelta(days=30)
            session.query(PageLoadLog).filter(PageLoadLog.created_at < cutoff).delete()
            _safe_flush(session)
    except Exception:
        pass


def log_pi3_interaction(
    session,
    call_site,
    question_text=None,
    response_text=None,
    user_id=None,
    company_id=None,
    plant_id=None,
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    estimated_cost_usd=None,
    response_time_ms=None,
    governance=None,
):
    """Items 49-51. Returns the new PI3InteractionLog row (with .id
    populated via flush) on success, or None on failure - callers that
    want to attach feedback (Item 55) or a docx/Expert-Notes save should
    hold onto the returned row's id.

    `governance` is the optional AI-governance evidence dict built by
    ai_assistant._governance_fields() (CR of 19 Aug 2026, section 8.1):
    model, application version, prompt versions and hashes, OpenAI
    response id and chain, tool log, retrieval evidence, classification
    and verification flags. Unknown keys are ignored rather than raising,
    so a caller that has only part of the picture still logs; a key that
    is absent stays NULL, which reads as "not recorded" instead of a
    fabricated value."""
    allowed = {
        "user_display_name",
        "model_name",
        "application_version",
        "system_prompt_version",
        "system_prompt_hash",
        "call_prompt_version",
        "call_prompt_hash",
        "openai_response_id",
        "openai_response_chain_json",
        "tool_log_json",
        "retrieval_evidence_json",
        "interaction_classification",
        "classification_source",
        "verification_required",
        "verification_message_shown",
    }
    extra = {k: v for k, v in (governance or {}).items() if k in allowed}
    try:
        row = PI3InteractionLog(
            user_id=user_id,
            company_id=company_id,
            plant_id=plant_id,
            call_site=call_site,
            question_text=question_text,
            response_text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            response_time_ms=response_time_ms,
            **extra,
        )
        session.add(row)
        if _safe_flush(session):
            return row
        return None
    except Exception:
        return None


def log_pi3_review(
    session,
    pi3_interaction_log_id,
    review_status,
    reviewer_user_id=None,
    reviewer_display_name=None,
    reviewer_company_id=None,
    reviewer_company_name=None,
    reviewer_plant_id=None,
    reviewer_plant_name=None,
    review_comment=None,
    customer_final_action=None,
):
    """Records one human decision against a PI3 answer (CR of 19 Aug 2026,
    section 7). Append-only: a later decision on the same interaction is
    another row, and the interaction's own question and answer are never
    touched. Returns the new row, or None on failure.

    Unlike everything else in this module, a failure here IS worth the
    caller noticing - a reviewer who believes their decision was recorded
    when it was not is the one case where a silent audit failure has
    consequences. The exception is still swallowed, so the return value is
    what the caller checks."""
    try:
        row = PI3InteractionReview(
            pi3_interaction_log_id=pi3_interaction_log_id,
            reviewer_user_id=reviewer_user_id,
            reviewer_display_name=reviewer_display_name,
            reviewer_company_id=reviewer_company_id,
            reviewer_company_name=reviewer_company_name,
            reviewer_plant_id=reviewer_plant_id,
            reviewer_plant_name=reviewer_plant_name,
            review_status=review_status,
            review_comment=review_comment,
            customer_final_action=customer_final_action,
        )
        session.add(row)
        if _safe_flush(session):
            return row
        return None
    except Exception:
        return None


def log_pi3_feedback(session, pi3_interaction_log_id, rating, user_id=None, comment=None):
    """Item 55. rating is 'up' or 'down'."""
    try:
        row = PI3Feedback(
            pi3_interaction_log_id=pi3_interaction_log_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
        )
        session.add(row)
        _safe_flush(session)
        return row
    except Exception:
        return None


def log_error(session, error_message, exc=None, user_id=None, company_id=None, page_name=None):
    """Item 52. Pass the caught exception as exc to capture a traceback;
    error_message should be a short human-readable summary (what the app
    was trying to do when it failed), not the raw str(exc)."""
    traceback_text = None
    if exc is not None:
        try:
            traceback_text = "".join(
                tb_module.format_exception(type(exc), exc, exc.__traceback__)
            )
        except Exception:
            traceback_text = str(exc)
    try:
        row = ErrorLog(
            user_id=user_id,
            company_id=company_id,
            page_name=page_name,
            error_message=error_message,
            traceback_text=traceback_text,
        )
        session.add(row)
        _safe_flush(session)
    except Exception:
        pass


def log_export(session, export_type, description=None, user_id=None, company_id=None):
    """Item 53. Called from a download button's on_click callback, so it
    fires exactly when the reviewer actually clicks Download."""
    try:
        row = ExportLog(
            user_id=user_id,
            company_id=company_id,
            export_type=export_type,
            description=description,
        )
        session.add(row)
        _safe_flush(session)
    except Exception:
        pass


def log_role_change(session, target_type, change_summary, changed_by_user_id=None, company_id=None, target_id=None, target_label=None):
    """Item 54. target_type is 'user', 'role', or 'permission' (page-access
    grid saves on the User Roles page)."""
    try:
        row = RoleChangeLog(
            changed_by_user_id=changed_by_user_id,
            company_id=company_id,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            change_summary=change_summary,
        )
        session.add(row)
        _safe_flush(session)
    except Exception:
        pass
