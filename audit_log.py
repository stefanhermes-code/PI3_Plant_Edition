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
):
    """Items 49-51. Returns the new PI3InteractionLog row (with .id
    populated via flush) on success, or None on failure - callers that
    want to attach feedback (Item 55) or a docx/Expert-Notes save should
    hold onto the returned row's id."""
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
