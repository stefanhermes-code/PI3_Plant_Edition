"""Screen 28: Company Analysis

Added 2026-08-03 for Gate 6, Item 56 of the Duroflex pilot readiness list
(PI3_Application_Changes_Needed.docx, section 3.2): "HTC support/pilot-
analysis view" - a single place for HTC to review how a customer company is
actually using the app, without needing direct Supabase access. Renamed
2026-08-05 from "Pilot Analysis" to "Company Analysis" - the underlying
page_key (pilot_analysis_admin) and file path are unchanged, this is a
display-label-only rename, same pattern as the "Sidewall width" -> "Tunnel
width" rename. Reads the
seven audit tables added for Gate 6 (see db.py, "Audit / usage /
pilot-learning logging package" and audit_log.py) - login/logout history
(Item 47), page usage (Item 48), every PI3 question/answer with token
usage, cost, and response time (Items 49-51), application errors (Item
52), recipe/report export and access events (Item 53), user/role change
history (Item 54), and PI3 answer feedback (Item 55). This page is the
one place all ten items become visible together.

Platform-owner-only (see auth.require_platform_owner) - same reasoning as
Performance and PI3 Connectivity: this is HTC's own operational view into
how a customer deployment is being used, not something a customer
company's own admin needs or should see. See access_control.py's
PLATFORM_ONLY_KEYS.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import current_user, logout_button, require_login, require_platform_owner
from db import (
    Company,
    ErrorLog,
    ExportLog,
    LoginEvent,
    PageViewEvent,
    PI3Feedback,
    PI3InteractionLog,
    RoleChangeLog,
    get_session,
    init_db,
)
from helpers import page_setup, render_data_table, render_function_action_intro

page_setup("Company Analysis")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Company Analysis")
render_function_action_intro(
    function_text=(
        "HTC's own review of how a pilot deployment is actually being used - logins, page usage, "
        "every PI3 question and answer (with token usage, estimated cost, response time, and "
        "reviewer feedback), application errors, recipe/report exports, and user/role changes. "
        "Everything here comes from the app's own audit tables, not from customer-facing pages."
    ),
    action_text=(
        "Pick a company and time window, then work through the tabs below. The PI3 Usage & "
        "Feedback tab is the most useful starting point for a pilot check-in - it shows both how "
        "much PI3 is actually being asked, and whether reviewers are finding the answers useful."
    ),
)

session = get_session()
user = current_user()

all_companies = session.query(Company).order_by(Company.name).all()
c1, c2 = st.columns([1, 1])
with c1:
    company_filter = st.selectbox(
        "Company", [None] + all_companies, format_func=lambda c: "All companies" if c is None else c.name,
    )
with c2:
    WINDOWS = {
        "Last 24 hours": dt.timedelta(hours=24),
        "Last 7 days": dt.timedelta(days=7),
        "Last 30 days": dt.timedelta(days=30),
        "All logged data": None,
    }
    window_label = st.selectbox("Time window", list(WINDOWS.keys()), index=1)
    window = WINDOWS[window_label]

company_id = company_filter.id if company_filter else None
cutoff = dt.datetime.utcnow() - window if window is not None else None


def _scoped(query, model, created_at_field):
    if company_id is not None:
        query = query.filter(model.company_id == company_id)
    if cutoff is not None:
        query = query.filter(created_at_field >= cutoff)
    return query


login_events = _scoped(session.query(LoginEvent), LoginEvent, LoginEvent.created_at).order_by(
    LoginEvent.created_at.desc()
).all()
page_views = _scoped(session.query(PageViewEvent), PageViewEvent, PageViewEvent.viewed_at).order_by(
    PageViewEvent.viewed_at.desc()
).all()
pi3_logs = _scoped(session.query(PI3InteractionLog), PI3InteractionLog, PI3InteractionLog.created_at).order_by(
    PI3InteractionLog.created_at.desc()
).all()
error_logs = _scoped(session.query(ErrorLog), ErrorLog, ErrorLog.created_at).order_by(
    ErrorLog.created_at.desc()
).all()
export_logs = _scoped(session.query(ExportLog), ExportLog, ExportLog.created_at).order_by(
    ExportLog.created_at.desc()
).all()
role_changes = _scoped(session.query(RoleChangeLog), RoleChangeLog, RoleChangeLog.created_at).order_by(
    RoleChangeLog.created_at.desc()
).all()
pi3_log_ids = [p.id for p in pi3_logs]
feedback = (
    session.query(PI3Feedback).filter(PI3Feedback.pi3_interaction_log_id.in_(pi3_log_ids)).all()
    if pi3_log_ids else []
)

st.subheader("Summary")
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Logins", sum(1 for e in login_events if e.event_type == "login_success"))
k2.metric("Page views", len(page_views))
k3.metric("PI3 questions", len(pi3_logs))
k4.metric("Errors", len(error_logs))
k5.metric("Exports", len(export_logs))
k6.metric("Role/user changes", len(role_changes))

tab_login, tab_pages, tab_pi3, tab_errors, tab_exports, tab_roles = st.tabs(
    ["Login Activity", "Page Usage", "PI3 Usage & Feedback", "Errors", "Exports", "Role/User Changes"]
)

with tab_login:
    if not login_events:
        st.info(f"No login activity logged for '{window_label}'.")
    else:
        counts = pd.Series([e.event_type for e in login_events]).value_counts()
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("Successful logins", int(counts.get("login_success", 0)))
        cc2.metric("Failed attempts", int(counts.get("login_failure", 0)))
        cc3.metric("Logouts", int(counts.get("logout", 0)))
        login_df = pd.DataFrame(
            [
                {
                    "Time (UTC)": e.created_at,
                    "Event": e.event_type,
                    "Username attempted": e.username_attempted or "—",
                    "Detail": e.detail or "—",
                }
                for e in login_events
            ]
        )
        render_data_table(login_df.head(300), max_height="500px")

with tab_pages:
    if not page_views:
        st.info(f"No page-view activity logged for '{window_label}'.")
    else:
        by_page = (
            pd.Series([p.page_name for p in page_views])
            .value_counts()
            .rename_axis("Page")
            .reset_index(name="Views")
        )
        render_data_table(by_page)
        st.bar_chart(by_page.set_index("Page")["Views"], horizontal=True)

with tab_pi3:
    if not pi3_logs:
        st.info(f"No PI3 questions logged for '{window_label}'.")
    else:
        total_tokens = sum(p.total_tokens or 0 for p in pi3_logs)
        # `or 0` prices an interaction with no cost figure as free. That is
        # fine for the arithmetic and misleading on its own, so the count of
        # unpriced interactions is carried alongside and stated below the
        # metric - a total that silently omits rows understates the spend, and
        # understating is the wrong direction when the figure is used to check
        # a customer is being charged enough.
        total_cost = sum(p.estimated_cost_usd or 0 for p in pi3_logs)
        unpriced = [p for p in pi3_logs if not p.estimated_cost_usd]
        cached_total = sum(p.cached_input_tokens or 0 for p in pi3_logs)
        prompt_total = sum(p.prompt_tokens or 0 for p in pi3_logs)
        avg_response_ms = (
            sum(p.response_time_ms for p in pi3_logs if p.response_time_ms is not None)
            / max(1, sum(1 for p in pi3_logs if p.response_time_ms is not None))
        )
        up_count = sum(1 for f in feedback if f.rating == "up")
        down_count = sum(1 for f in feedback if f.rating == "down")

        pc1, pc2, pc3, pc4 = st.columns(4)
        pc1.metric("Total tokens", f"{total_tokens:,}" if total_tokens else "—")
        pc2.metric("Estimated cost", f"${total_cost:,.2f}" if total_cost else "—")
        pc3.metric("Avg response time", f"{avg_response_ms:.0f} ms" if avg_response_ms else "—")
        pc4.metric("Feedback", f"👍 {up_count} / 👎 {down_count}" if (up_count or down_count) else "No feedback yet")

        if unpriced:
            st.warning(
                f"{len(unpriced)} of {len(pi3_logs)} interaction(s) in this period carry no cost "
                "figure, so the total above is a floor rather than the full spend. Those are "
                "interactions recorded before pricing was applied, or answered by a model with "
                "no published rate on file. They are not priced retrospectively."
            )
        if cached_total and prompt_total:
            st.caption(
                f"{cached_total:,} of {prompt_total:,} input tokens were served from OpenAI's "
                f"prompt cache ({100 * cached_total / prompt_total:.0f}%), billed at a tenth of "
                "the fresh rate. A falling share here is a cost rise."
            )
        st.caption(
            "Estimated cost is the recorded token counts priced at the published rate for the "
            "model that answered, with cached input priced separately from fresh input (see "
            "ai_assistant.MODEL_TOKEN_RATES_USD_PER_1M). Where a cached count was recorded the "
            "figure is exact; on an interaction predating that, it is an upper bound."
        )

        by_call_site = (
            pd.Series([p.call_site for p in pi3_logs])
            .value_counts()
            .rename_axis("Call site")
            .reset_index(name="Questions")
        )
        render_data_table(by_call_site)

        st.subheader("Recent questions")
        feedback_by_log_id = {f.pi3_interaction_log_id: f for f in feedback}
        recent_pi3 = pd.DataFrame(
            [
                {
                    "Time (UTC)": p.created_at,
                    "Call site": p.call_site,
                    "Question": (p.question_text or "")[:150],
                    "Tokens": p.total_tokens,
                    "Response (ms)": p.response_time_ms,
                    "Feedback": (
                        {"up": "👍", "down": "👎"}.get(feedback_by_log_id[p.id].rating, "—")
                        if p.id in feedback_by_log_id else "—"
                    ),
                }
                for p in pi3_logs[:300]
            ]
        )
        render_data_table(recent_pi3, max_height="500px")

        if feedback:
            st.subheader("Feedback comments")
            comments = [f for f in feedback if f.comment]
            if comments:
                comments_df = pd.DataFrame(
                    [
                        {
                            "Time (UTC)": f.created_at,
                            "Rating": {"up": "👍", "down": "👎"}.get(f.rating, f.rating),
                            "Comment": f.comment,
                        }
                        for f in comments
                    ]
                )
                render_data_table(comments_df, max_height="300px")
            else:
                st.caption("No written comments in this window - just thumbs up/down ratings.")

with tab_errors:
    if not error_logs:
        st.info(f"No errors logged for '{window_label}'. This is a good sign.")
    else:
        error_df = pd.DataFrame(
            [
                {
                    "Time (UTC)": e.created_at,
                    "Page": e.page_name or "—",
                    "Message": e.error_message,
                }
                for e in error_logs
            ]
        )
        render_data_table(error_df.head(300), max_height="500px")
        st.caption("Click a row's message to see the short summary; full tracebacks are stored but not shown here.")

with tab_exports:
    if not export_logs:
        st.info(f"No exports logged for '{window_label}'.")
    else:
        by_type = (
            pd.Series([e.export_type for e in export_logs])
            .value_counts()
            .rename_axis("Export type")
            .reset_index(name="Count")
        )
        render_data_table(by_type)
        export_df = pd.DataFrame(
            [
                {
                    "Time (UTC)": e.created_at,
                    "Type": e.export_type,
                    "Detail": e.description or "—",
                }
                for e in export_logs
            ]
        )
        render_data_table(export_df.head(300), max_height="500px")

with tab_roles:
    if not role_changes:
        st.info(f"No user/role changes logged for '{window_label}'.")
    else:
        role_df = pd.DataFrame(
            [
                {
                    "Time (UTC)": r.created_at,
                    "Target type": r.target_type,
                    "Target": r.target_label or "—",
                    "Change": r.change_summary,
                }
                for r in role_changes
            ]
        )
        render_data_table(role_df.head(300), max_height="500px")
