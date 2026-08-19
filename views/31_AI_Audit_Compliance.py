"""Screen 31: AI Audit & Compliance

First slice of the CR "AI Governance, Human Verification, Audit
Traceability and Platform Admin Compliance View" (19 August 2026): the
audit trail and the view onto it.

Every PI3 answer has always been recorded (PI3InteractionLog, Gate 6 items
49-51). What was missing was the evidence around the answer - which model,
which prompt version, which tools were consulted, whether the output was
process-relevant, and what a human decided about it. Those fields now
exist on the interaction row (see db.PI3InteractionLog, CR section 8.1)
and the human decision is a separate append-only table
(db.PI3InteractionReview, CR section 8.2). This page reads both.

Rows written before the CR carry the original fields only. They are shown
as "not recorded" rather than filled in - reconstructing an audit trail
after the fact would make it worthless.

Platform-owner-only, like the other Application Admin pages: the point of
the page is the view ACROSS every customer company, which is exactly the
scope a customer's own admin must not have. See
access_control.PLATFORM_ONLY_KEYS.
"""

import datetime as dt
import json

import pandas as pd
import streamlit as st

import ai_governance
import audit_log
from auth import current_user, logout_button, require_login, require_platform_owner
from db import (
    Company,
    PI3InteractionLog,
    PI3InteractionReview,
    Plant,
    User,
    get_session,
    init_db,
)
from helpers import clickable_table, page_setup, render_function_action_intro

# Guard against loading the whole table into one page render. The audit
# population is filtered first; this is the ceiling on what a single filter
# combination will pull back, and the page says so out loud when it bites -
# a silently truncated audit view would read as "that is everything".
MAX_ROWS = 2000

ANY = "All"
NOT_RECORDED = "not recorded"

page_setup("AI Audit & Compliance")
init_db()
require_login()
require_platform_owner()
logout_button()

session = get_session()
user = current_user()

st.title("AI Audit & Compliance")
render_function_action_intro(
    function_text=(
        "The complete record of every PI3 interaction across every customer company: what was "
        "asked, which model and prompt version answered it, what data and tools were consulted, "
        "how the interaction was classified, and what a human reviewer decided about it. "
        "Interactions classified Process / Safety Relevant are the ones that carry a technical "
        "recommendation into a plant, so those are the ones that require a recorded decision "
        "before trial or operational use."
    ),
    action_text="Set the reporting period and filters, then open an interaction to read its full record.",
    action_steps=[
        "**Step 1.** Set the reporting period at the top - every figure and list below follows it.",
        "**Step 2.** Narrow with the filters: company, plant, user, call site, classification, "
        "review status, model or prompt version.",
        "**Step 3.** Click an interaction in the list to open its full evidence record.",
        "**Step 4.** Record a review decision on the interaction where one is required.",
    ],
    action_note=(
        "Interactions recorded before the governance fields existed show them as "
        f"\"{NOT_RECORDED}\". Those values are left blank deliberately - historical evidence that "
        "was never captured is not reconstructed."
    ),
)


# --- Reporting period -----------------------------------------------------
today = dt.date.today()
period_col, preset_col = st.columns([3, 2])
with preset_col:
    preset = st.selectbox(
        "Period",
        ["Last 30 days", "Last 7 days", "Last 90 days", "This year", "All time", "Custom"],
        key="ai_audit_preset",
    )
if preset == "Last 7 days":
    default_range = (today - dt.timedelta(days=7), today)
elif preset == "Last 90 days":
    default_range = (today - dt.timedelta(days=90), today)
elif preset == "This year":
    default_range = (dt.date(today.year, 1, 1), today)
elif preset == "All time":
    default_range = (dt.date(2000, 1, 1), today)
else:
    default_range = (today - dt.timedelta(days=30), today)

with period_col:
    if preset == "Custom":
        chosen = st.date_input("Reporting period", value=default_range, key="ai_audit_range")
        if isinstance(chosen, (list, tuple)) and len(chosen) == 2:
            date_from, date_to = chosen
        else:
            date_from, date_to = default_range
    else:
        date_from, date_to = default_range
        st.text_input(
            "Reporting period",
            value=f"{date_from} to {date_to}",
            disabled=True,
            key="ai_audit_range_display",
        )

start_dt = dt.datetime.combine(date_from, dt.time.min)
end_dt = dt.datetime.combine(date_to, dt.time.max)


# --- Load the population --------------------------------------------------
base_query = (
    session.query(PI3InteractionLog)
    .filter(PI3InteractionLog.created_at >= start_dt)
    .filter(PI3InteractionLog.created_at <= end_dt)
    .order_by(PI3InteractionLog.created_at.desc())
)
interactions = base_query.limit(MAX_ROWS + 1).all()
truncated = len(interactions) > MAX_ROWS
interactions = interactions[:MAX_ROWS]

if not interactions:
    st.info("No PI3 interactions in this period.")
    st.stop()

companies = {c.id: c.name for c in session.query(Company).all()}
plants = {p.id: p.name for p in session.query(Plant).all()}
users = {u.id: (u.display_name or u.email or f"User {u.id}") for u in session.query(User).all()}

reviews_by_interaction = {}
for rev in (
    session.query(PI3InteractionReview)
    .filter(PI3InteractionReview.pi3_interaction_log_id.in_([i.id for i in interactions]))
    .order_by(PI3InteractionReview.created_at)
    .all()
):
    reviews_by_interaction.setdefault(rev.pi3_interaction_log_id, []).append(rev)


def _latest_status(interaction):
    """Current review position for an interaction. Reviews are append-only,
    so the newest row is the standing decision; an interaction that needs a
    decision and has none is Pending. Anything that never required
    verification has no review state at all."""
    revs = reviews_by_interaction.get(interaction.id)
    if revs:
        return revs[-1].review_status
    if interaction.verification_required:
        return ai_governance.REVIEW_PENDING
    return None


def _label(value):
    return value if value not in (None, "") else NOT_RECORDED


# --- Filters --------------------------------------------------------------
with st.expander("Filters", expanded=False):
    f1, f2, f3 = st.columns(3)
    with f1:
        company_options = [ANY] + sorted({companies.get(i.company_id, NOT_RECORDED) for i in interactions})
        f_company = st.selectbox("Company", company_options, key="ai_audit_company")
        plant_options = [ANY] + sorted({plants.get(i.plant_id, NOT_RECORDED) for i in interactions})
        f_plant = st.selectbox("Plant", plant_options, key="ai_audit_plant")
        user_options = [ANY] + sorted({users.get(i.user_id, NOT_RECORDED) for i in interactions})
        f_user = st.selectbox("User", user_options, key="ai_audit_user")
        f_id = st.text_input("Interaction ID", key="ai_audit_id", placeholder="Exact id")
    with f2:
        site_options = [ANY] + sorted({i.call_site for i in interactions if i.call_site})
        f_site = st.selectbox("PI3 call site", site_options, key="ai_audit_site")
        f_class = st.selectbox(
            "Classification", [ANY] + list(ai_governance.CLASSIFICATIONS) + [NOT_RECORDED],
            key="ai_audit_class",
        )
        f_source = st.selectbox(
            "Classification source", [ANY] + list(ai_governance.CLASSIFICATION_SOURCES) + [NOT_RECORDED],
            key="ai_audit_source",
        )
        f_verif = st.selectbox(
            "Verification required", [ANY, "Yes", "No"], key="ai_audit_verif"
        )
    with f3:
        f_review = st.selectbox(
            "Review status", [ANY] + list(ai_governance.REVIEW_STATUSES) + ["Not applicable"],
            key="ai_audit_review",
        )
        model_options = [ANY] + sorted({_label(i.model_name) for i in interactions})
        f_model = st.selectbox("Model", model_options, key="ai_audit_model")
        sysver_options = [ANY] + sorted({_label(i.system_prompt_version) for i in interactions})
        f_sysver = st.selectbox("System prompt version", sysver_options, key="ai_audit_sysver")
        callver_options = [ANY] + sorted({_label(i.call_prompt_version) for i in interactions})
        f_callver = st.selectbox("Call prompt version", callver_options, key="ai_audit_callver")


def _keep(i):
    if f_company != ANY and companies.get(i.company_id, NOT_RECORDED) != f_company:
        return False
    if f_plant != ANY and plants.get(i.plant_id, NOT_RECORDED) != f_plant:
        return False
    if f_user != ANY and users.get(i.user_id, NOT_RECORDED) != f_user:
        return False
    if f_site != ANY and i.call_site != f_site:
        return False
    if f_class != ANY and _label(i.interaction_classification) != f_class:
        return False
    if f_source != ANY and _label(i.classification_source) != f_source:
        return False
    if f_verif != ANY and bool(i.verification_required) != (f_verif == "Yes"):
        return False
    if f_model != ANY and _label(i.model_name) != f_model:
        return False
    if f_sysver != ANY and _label(i.system_prompt_version) != f_sysver:
        return False
    if f_callver != ANY and _label(i.call_prompt_version) != f_callver:
        return False
    if f_review != ANY:
        status = _latest_status(i)
        if f_review == "Not applicable":
            if status is not None:
                return False
        elif status != f_review:
            return False
    if f_id.strip():
        if not f_id.strip().isdigit() or i.id != int(f_id.strip()):
            return False
    return True


population = [i for i in interactions if _keep(i)]

if truncated:
    st.warning(
        f"This period holds more than {MAX_ROWS} interactions. The newest {MAX_ROWS} are loaded - "
        "narrow the reporting period to see the rest."
    )


# --- Summary --------------------------------------------------------------
st.subheader("Summary")
statuses = [_latest_status(i) for i in population]
required = [i for i in population if i.verification_required]
completed = [
    i for i in required
    if _latest_status(i) not in (None, ai_governance.REVIEW_PENDING)
]

m1, m2, m3, m4 = st.columns(4)
m1.metric("PI3 interactions", len(population))
m2.metric(
    "Process / Safety Relevant",
    sum(1 for i in population if i.interaction_classification == ai_governance.PROCESS_SAFETY_RELEVANT),
)
m3.metric("Verification required", len(required))
m4.metric("Verification completed", len(completed))

m5, m6, m7, m8 = st.columns(4)
m5.metric("Verification pending", len(required) - len(completed))
m6.metric("Accepted for trial", statuses.count(ai_governance.REVIEW_ACCEPTED))
m7.metric("Modified", statuses.count(ai_governance.REVIEW_MODIFIED))
m8.metric("Rejected", statuses.count(ai_governance.REVIEW_REJECTED))

version_counts = {}
for i in population:
    version_counts[_label(i.system_prompt_version)] = version_counts.get(_label(i.system_prompt_version), 0) + 1
if version_counts:
    st.caption(
        "System prompt versions in this population: "
        + ", ".join(f"{v} ({n})" for v, n in sorted(version_counts.items()))
    )

if not population:
    st.info("No interactions match these filters.")
    st.stop()


# --- Interaction list -----------------------------------------------------
st.subheader("Interactions")
st.caption(f"{len(population)} interaction(s). Click a row to open its full record.")

list_rows = [
    {
        "ID": i.id,
        "When": i.created_at,
        "Company": companies.get(i.company_id, "—"),
        "Plant": plants.get(i.plant_id, "—"),
        "User": users.get(i.user_id, "—"),
        "Call site": i.call_site,
        "Classification": _label(i.interaction_classification),
        "Verification": "Required" if i.verification_required else "—",
        "Review": _latest_status(i) or "—",
    }
    for i in population
]
sel_idx = clickable_table(list_rows, key="ai_audit_list")
if sel_idx is not None and sel_idx < len(population):
    st.session_state["ai_audit_selected_id"] = population[sel_idx].id
elif st.session_state.get("ai_audit_selected_id") not in {i.id for i in population}:
    st.session_state.pop("ai_audit_selected_id", None)

selected = next(
    (i for i in population if i.id == st.session_state.get("ai_audit_selected_id")), None
)
if selected is None:
    st.stop()


# --- Interaction detail ---------------------------------------------------
st.divider()
st.subheader(f"Interaction #{selected.id}")

status = _latest_status(selected)
if status:
    st.info(f"**{status}** — {ai_governance.REVIEW_DISPLAY.get(status, '')}")

d1, d2 = st.columns(2)
with d1:
    st.markdown("**Identity**")
    st.write(
        pd.DataFrame(
            [
                {"Field": "Recorded", "Value": str(selected.created_at)},
                {"Field": "Company", "Value": companies.get(selected.company_id, "—")},
                {"Field": "Plant", "Value": plants.get(selected.plant_id, "—")},
                {"Field": "User", "Value": users.get(selected.user_id, "—")},
                {"Field": "Call site", "Value": selected.call_site or "—"},
            ]
        ).set_index("Field")
    )
with d2:
    st.markdown("**AI configuration**")
    st.write(
        pd.DataFrame(
            [
                {"Field": "Model", "Value": _label(selected.model_name)},
                {"Field": "Application version", "Value": _label(selected.application_version)},
                {
                    "Field": "System prompt",
                    "Value": f"{_label(selected.system_prompt_version)} / {_label(selected.system_prompt_hash)}",
                },
                {
                    "Field": "Call prompt",
                    "Value": f"{_label(selected.call_prompt_version)} / {_label(selected.call_prompt_hash)}",
                },
                {"Field": "OpenAI response", "Value": _label(selected.openai_response_id)},
            ]
        ).set_index("Field")
    )

st.markdown("**Governance**")
g1, g2, g3, g4 = st.columns(4)
g1.metric("Classification", _label(selected.interaction_classification))
g2.metric("Assigned by", _label(selected.classification_source))
g3.metric("Verification required", "Yes" if selected.verification_required else "No")
g4.metric("Notice shown", "Yes" if selected.verification_message_shown else "No")

with st.expander("Question", expanded=True):
    st.text(selected.question_text or NOT_RECORDED)

with st.expander("PI3 response", expanded=True):
    st.markdown(selected.response_text or NOT_RECORDED)


def _show_json(label, raw):
    with st.expander(label, expanded=False):
        if not raw:
            st.caption(
                f"{NOT_RECORDED} — this interaction either used no tools or predates the "
                "governance fields."
            )
            return
        try:
            st.json(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            st.text(raw)


_show_json("Tool evidence", selected.tool_log_json)
_show_json("Retrieval evidence", selected.retrieval_evidence_json)
_show_json("OpenAI response chain", selected.openai_response_chain_json)

usage_bits = [
    f"{selected.total_tokens} tokens" if selected.total_tokens else None,
    f"${selected.estimated_cost_usd:.4f}" if selected.estimated_cost_usd else None,
    f"{selected.response_time_ms:.0f} ms" if selected.response_time_ms else None,
]
st.caption("Usage: " + (" · ".join(b for b in usage_bits if b) or NOT_RECORDED))


# --- Human review ---------------------------------------------------------
st.markdown("**Human review**")
existing = reviews_by_interaction.get(selected.id, [])
if existing:
    st.dataframe(
        [
            {
                "When": r.created_at,
                "Reviewer": users.get(r.reviewer_user_id, "—"),
                "Decision": r.review_status,
                "Comment": r.review_comment or "—",
                "Customer action": r.customer_final_action or "—",
            }
            for r in existing
        ],
        hide_index=True,
        use_container_width=True,
    )
else:
    st.caption("No review recorded against this interaction yet.")

st.caption(
    "A review is added, never edited. Recording a new decision leaves the previous one in place, "
    "and the original PI3 question and answer above stay exactly as generated."
)
with st.form(f"ai_audit_review_{selected.id}"):
    new_status = st.selectbox(
        "Decision *",
        [s for s in ai_governance.REVIEW_STATUSES if s != ai_governance.REVIEW_PENDING],
        key=f"ai_audit_review_status_{selected.id}",
    )
    new_comment = st.text_area("Reviewer comment", key=f"ai_audit_review_comment_{selected.id}")
    new_action = st.text_area(
        "Customer action taken",
        help="What was actually done, where that differs from the recommendation.",
        key=f"ai_audit_review_action_{selected.id}",
    )
    if st.form_submit_button("Record decision"):
        row = audit_log.log_pi3_review(
            session,
            pi3_interaction_log_id=selected.id,
            review_status=new_status,
            reviewer_user_id=user.get("id"),
            review_comment=(new_comment or "").strip() or None,
            customer_final_action=(new_action or "").strip() or None,
        )
        if row is None:
            st.error("The decision was not recorded. Try again, or check the error log.")
        else:
            session.commit()
            st.success("Decision recorded.")
            st.rerun()
