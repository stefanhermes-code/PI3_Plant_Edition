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

READ-ONLY BY DESIGN. Stefan's ruling, 19 Aug 2026: this page shows the
record, it does not create it. Only the company that generated an answer may
qualify it, and they do that on the answer itself (see
helpers.render_pi3_verification_panel). HTC recording a decision on a
customer's PI3 output would make HTC a party to accepting the
recommendation - the exact responsibility this CR exists to place with the
customer. A customer that leaves an answer unqualified has made its own
decision, and Pending on this page is the evidence of that.
"""

import datetime as dt
import json

import pandas as pd
import streamlit as st

import ai_governance
import reports
from auth import current_user, logout_button, require_login, require_platform_owner
from db import (
    Company,
    ErrorLog,
    PI3Feedback,
    PI3InteractionLog,
    PI3InteractionReview,
    Plant,
    User,
    get_session,
    init_db,
)
from helpers import clickable_table, log_export_click, page_setup, render_function_action_intro

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
        "by the customer before trial or operational use."
    ),
    action_text="Set the reporting period and filters, then open an interaction to read its full record.",
    action_steps=[
        "Set the reporting period at the top - every figure and list below follows it.",
        "Narrow with the filters: company, plant, user, call site, classification, review status, "
        "model or prompt version.",
        "Click an interaction in the list to open its full evidence record.",
        "Export the filtered population when a compliance record is needed.",
    ],
    action_note=(
        "This page is read-only. Verification is recorded by the company that generated the "
        "answer, on the answer itself - a decision taken here would place responsibility for "
        "qualifying it in the wrong hands. Interactions recorded before the governance fields "
        f"existed show them as \"{NOT_RECORDED}\"; those values are left blank deliberately, "
        "because historical evidence that was never captured is not reconstructed."
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

# st.stop() is deliberately NOT used anywhere on this page. It leaves
# Streamlit's stop flag set, and every st.* call afterwards - including the
# ones app.py makes in its own finally block - re-raises the StopException.
# On 19 Aug 2026 that skipped app.py's page-lock release and wedged the whole
# browser session: the page rendered once and every click after it spun
# forever. app.py is now hardened against it (see the note in its finally),
# and this page reaches the end of its script on every render as well.
page_has_data = bool(interactions)
if not page_has_data:
    st.info("No PI3 interactions in this period.")

companies = {c.id: c.name for c in session.query(Company).all()}
plants = {p.id: p.name for p in session.query(Plant).all()}
users = {u.id: (u.display_name or u.email or f"User {u.id}") for u in session.query(User).all()}

# PI3 call failures in the same period (CR section 10.1). Recorded by
# ai_assistant._record_pi3_error as an ErrorLog row, so a failed call is
# visible here rather than only in the general error log.
pi3_error_count = (
    session.query(ErrorLog)
    .filter(ErrorLog.created_at >= start_dt)
    .filter(ErrorLog.created_at <= end_dt)
    .filter(ErrorLog.error_message.like("PI3 call failed%"))
    .count()
)

# Existing thumbs-up/down feedback, shown on the interaction detail
# (CR section 10.3, Charlie's item E).
feedback_by_interaction = {}
for fb in (
    session.query(PI3Feedback)
    .filter(PI3Feedback.pi3_interaction_log_id.in_([i.id for i in interactions]))
    .order_by(PI3Feedback.created_at)
    .all()
):
    feedback_by_interaction.setdefault(fb.pi3_interaction_log_id, []).append(fb)

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
        # No review row is "Verification outstanding". "Pending review" is a
        # decision a person actually recorded, and conflating the two hides
        # whether anyone has looked - Charlie's correction of 20 Aug 2026.
        return ai_governance.VERIFICATION_OUTSTANDING
    return None


def _label(value):
    return value if value not in (None, "") else NOT_RECORDED


def _who(recorded_name, user_id):
    """Who did this, preferring the name recorded at the time over a lookup
    through the user id. The id is a link, not an identity - a renamed or
    deleted user would otherwise take their name out of the audit record, and
    a session that authenticates outside the users table never had an id to
    record in the first place."""
    return recorded_name or users.get(user_id) or NOT_RECORDED

if page_has_data:

    # --- Filters --------------------------------------------------------------
    with st.expander("Filters", expanded=False):
        f1, f2, f3 = st.columns(3)
        with f1:
            company_options = [ANY] + sorted({companies.get(i.company_id, NOT_RECORDED) for i in interactions})
            f_company = st.selectbox("Company", company_options, key="ai_audit_company")
            plant_options = [ANY] + sorted({plants.get(i.plant_id, NOT_RECORDED) for i in interactions})
            f_plant = st.selectbox("Plant", plant_options, key="ai_audit_plant")
            user_options = [ANY] + sorted({_who(i.user_display_name, i.user_id) for i in interactions})
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
                "Review status",
                [ANY] + list(ai_governance.ALL_REVIEW_STATES) + ["Not applicable"],
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
        if f_user != ANY and _who(i.user_display_name, i.user_id) != f_user:
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
    # "Governed" means the interactions this CR actually governs: those
    # classified as requiring verification. Charlie's item G - the outcome
    # counts must not silently mix in a legacy review recorded against an
    # interaction that never required one.
    required = [i for i in population if i.verification_required]
    required_ids = {i.id for i in required}
    governed_statuses = [_latest_status(i) for i in required]
    resolved = [
        i for i in required
        if _latest_status(i) not in ai_governance.UNRESOLVED_REVIEW_STATES
    ]
    outstanding = [
        i for i in required
        if _latest_status(i) == ai_governance.VERIFICATION_OUTSTANDING
    ]
    deferred = [
        i for i in required
        if _latest_status(i) == ai_governance.REVIEW_PENDING
    ]
    # Review events recorded against interactions that never required
    # verification - the historical test review on interaction 1 is one.
    # Kept visible, counted separately, never folded into the outcome figures.
    legacy_reviews = [
        r for i in population if i.id not in required_ids
        for r in reviews_by_interaction.get(i.id, [])
    ]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("PI3 interactions", len(population))
    m2.metric(
        "Process / Safety Relevant",
        sum(1 for i in population if i.interaction_classification == ai_governance.PROCESS_SAFETY_RELEVANT),
    )
    m3.metric("Verification required", len(required))
    m4.metric("Decision recorded", len(resolved))

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Verification outstanding", len(outstanding))
    m6.metric("Reviewer deferred (Pending)", len(deferred))
    m7.metric("PI3 errors in period", pi3_error_count)
    m8.metric("Legacy review events", len(legacy_reviews))

    st.caption(
        "Outcome counts below are of interactions that REQUIRED verification, so they cannot "
        "be inflated by a review recorded against one that did not."
    )
    o1, o2, o3 = st.columns(3)
    o1.metric("Accepted for trial", governed_statuses.count(ai_governance.REVIEW_ACCEPTED))
    o2.metric("Modified", governed_statuses.count(ai_governance.REVIEW_MODIFIED))
    o3.metric("Rejected", governed_statuses.count(ai_governance.REVIEW_REJECTED))
    if legacy_reviews:
        st.caption(
            f"{len(legacy_reviews)} review event(s) sit against interactions that never required "
            "verification. They stay in the record and in the export, and are excluded from the "
            "outcome counts above."
        )

    version_counts = {}
    for i in population:
        version_counts[_label(i.system_prompt_version)] = version_counts.get(_label(i.system_prompt_version), 0) + 1
    if version_counts:
        st.caption(
            "System prompt versions in this population: "
            + ", ".join(f"{v} ({n})" for v, n in sorted(version_counts.items()))
        )

    # --- Audit export -----------------------------------------------------
    # CR section 11. The export is the filtered population, not the whole
    # table: what a compliance reader needs is the same set they are looking
    # at, so the figures in the workbook and the figures on screen cannot
    # disagree. Interaction ID is the key across all four sheets.
    if population:
        export_summary = [
            {"Item": "Reporting period", "Value": f"{date_from} to {date_to}"},
            {"Item": "Exported (UTC)", "Value": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")},
            {"Item": "Exported by", "Value": user.get("display_name") or user.get("username") or "—"},
            {"Item": "Company filter", "Value": f_company},
            {"Item": "Plant filter", "Value": f_plant},
            {"Item": "User filter", "Value": f_user},
            {"Item": "Call site filter", "Value": f_site},
            {"Item": "Classification filter", "Value": f_class},
            {"Item": "Classification source filter", "Value": f_source},
            {"Item": "Verification required filter", "Value": f_verif},
            {"Item": "Review status filter", "Value": f_review},
            {"Item": "Model filter", "Value": f_model},
            {"Item": "System prompt version filter", "Value": f_sysver},
            {"Item": "Call prompt version filter", "Value": f_callver},
            {"Item": "PI3 interactions", "Value": len(population)},
            {
                "Item": "Process / Safety Relevant",
                "Value": sum(
                    1 for i in population
                    if i.interaction_classification == ai_governance.PROCESS_SAFETY_RELEVANT
                ),
            },
            {"Item": "Verification required", "Value": len(required)},
            {"Item": "Decision recorded (of those required)", "Value": len(resolved)},
            {"Item": "Verification outstanding - no review event", "Value": len(outstanding)},
            {"Item": "Pending review - reviewer deferred", "Value": len(deferred)},
            {"Item": "Accepted for trial (of those required)",
             "Value": governed_statuses.count(ai_governance.REVIEW_ACCEPTED)},
            {"Item": "Modified (of those required)",
             "Value": governed_statuses.count(ai_governance.REVIEW_MODIFIED)},
            {"Item": "Rejected (of those required)",
             "Value": governed_statuses.count(ai_governance.REVIEW_REJECTED)},
            {"Item": "Legacy review events - interaction did not require verification",
             "Value": len(legacy_reviews)},
            {"Item": "PI3 errors in period", "Value": pi3_error_count},
        ]
        if not any(i.estimated_cost_usd for i in population):
            export_summary.append({
                "Item": "Estimated cost",
                "Value": "Not calculated - PI3_INPUT_COST_PER_1M_TOKENS and "
                         "PI3_OUTPUT_COST_PER_1M_TOKENS are not configured, so no rate exists "
                         "to price the recorded tokens against",
            })
        for version, count in sorted(version_counts.items()):
            export_summary.append({"Item": f"System prompt version {version}", "Value": count})
        if truncated:
            export_summary.append(
                {"Item": "Row cap reached",
                 "Value": f"Newest {MAX_ROWS} interactions in the period only - narrow the period to export the rest"}
            )

        export_interactions = [
            {
                "Interaction ID": i.id,
                "Recorded (UTC)": i.created_at,
                "Company": companies.get(i.company_id, ""),
                "Plant": plants.get(i.plant_id, ""),
                "User": _who(i.user_display_name, i.user_id),
                "Call site": i.call_site,
                "Classification": i.interaction_classification,
                "Classification source": i.classification_source,
                "Verification required": "Yes" if i.verification_required else "No",
                "Notice shown": "Yes" if i.verification_message_shown else "No",
                "Review status": _latest_status(i) or "Not applicable",
                "Model": i.model_name,
                "Application version": i.application_version,
                "System prompt version": i.system_prompt_version,
                "System prompt hash": i.system_prompt_hash,
                "Call prompt version": i.call_prompt_version,
                "Call prompt hash": i.call_prompt_hash,
                "OpenAI response ID": i.openai_response_id,
                "Total tokens": i.total_tokens,
                "Estimated cost (USD)": i.estimated_cost_usd,
                "Response time (ms)": i.response_time_ms,
                "Question": i.question_text,
                "PI3 response": i.response_text,
            }
            for i in population
        ]

        export_reviews = [
            {
                "Interaction ID": r.pi3_interaction_log_id,
                "Review ID": r.id,
                "Recorded (UTC)": r.created_at,
                "Reviewer": _who(r.reviewer_display_name, r.reviewer_user_id),
                "Reviewer company": r.reviewer_company_name or companies.get(r.reviewer_company_id, ""),
                "Reviewer plant": r.reviewer_plant_name or plants.get(r.reviewer_plant_id, ""),
                "Decision": r.review_status,
                "Reviewer comment": r.review_comment,
                "Customer action taken": r.customer_final_action,
            }
            for i in population
            for r in reviews_by_interaction.get(i.id, [])
        ]

        export_tools = []
        for i in population:
            seq = 0
            for label, raw in (("Tool call", i.tool_log_json), ("Retrieval", i.retrieval_evidence_json)):
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    # Unparseable evidence is exported as-is rather than
                    # dropped - it is still what was recorded.
                    seq += 1
                    export_tools.append({
                        "Interaction ID": i.id, "Sequence": seq,
                        "Evidence type": label, "Tool": "", "Detail": raw,
                    })
                    continue
                entries = parsed if isinstance(parsed, list) else [parsed]
                for entry in entries:
                    seq += 1
                    tool = entry.get("tool") if isinstance(entry, dict) else ""
                    export_tools.append({
                        "Interaction ID": i.id,
                        "Sequence": seq,
                        "Evidence type": label,
                        "Tool": tool or "",
                        "Detail": json.dumps(entry, indent=2, default=str),
                    })

        st.download_button(
            "Download audit export (Excel)",
            data=reports.render_ai_audit_export_xlsx(
                export_summary, export_interactions, export_reviews, export_tools,
            ),
            file_name=f"pi3_ai_audit_{date_from}_{date_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ai_audit_export",
            on_click=log_export_click,
            args=("ai_audit_compliance_xlsx",),
            kwargs={"description": f"{len(population)} interaction(s), {date_from} to {date_to}"},
        )
        st.caption(
            f"Four sheets - Summary, Interactions, Reviews, Tool Evidence - covering the "
            f"{len(population)} interaction(s) matching the filters above, keyed on Interaction ID."
        )

    if not population:
        st.info("No interactions match these filters.")
    else:


        # --- Interaction list -----------------------------------------------------
        st.subheader("Interactions")
        st.caption(f"{len(population)} interaction(s). Click a row to open its full record.")

        list_rows = [
            {
                "ID": i.id,
                "When": i.created_at,
                "Company": companies.get(i.company_id, "—"),
                "Plant": plants.get(i.plant_id, "—"),
                "User": _who(i.user_display_name, i.user_id),
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
            (i for i in population if i.id == st.session_state.get("ai_audit_selected_id")),
            None,
        )
        if selected is not None:


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
                            {"Field": "User", "Value": _who(selected.user_display_name, selected.user_id)},
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
                # A blank cost column reads as "this call was free". Say why
                # instead: the tokens are recorded, there is simply no rate to
                # price them against until the two cost secrets are set.
                f"${selected.estimated_cost_usd:.4f}" if selected.estimated_cost_usd
                else "cost not priced - no per-token rate configured",
                f"{selected.response_time_ms:.0f} ms" if selected.response_time_ms else None,
            ]
            st.caption("Usage: " + (" · ".join(b for b in usage_bits if b) or NOT_RECORDED))


            # --- Reviewer feedback ----------------------------------------------------
            st.markdown("**Reviewer feedback**")
            fb_rows = feedback_by_interaction.get(selected.id, [])
            if fb_rows:
                st.dataframe(
                    [
                        {
                            "When": f.created_at,
                            "Rating": {"up": "👍 Useful", "down": "👎 Not useful"}.get(f.rating, f.rating),
                            "Comment": f.comment or "—",
                            "By": _who(None, f.user_id),
                        }
                        for f in fb_rows
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("No feedback given on this answer.")

            # --- Human review ---------------------------------------------------------
            st.markdown("**Human review**")
            existing = reviews_by_interaction.get(selected.id, [])
            if existing:
                st.dataframe(
                    [
                        {
                            "When": r.created_at,
                            "Reviewer": _who(r.reviewer_display_name, r.reviewer_user_id),
                            "Company": r.reviewer_company_name or companies.get(r.reviewer_company_id, "—"),
                            "Plant": r.reviewer_plant_name or plants.get(r.reviewer_plant_id, "—"),
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
                "This page does not record decisions. Verification belongs to the company that "
                "generated the answer, and is recorded by them on the answer itself - see the "
                "note at the top of this page. Reviews are append-only, so what is shown above "
                "is the full history, and the original question and answer are never altered by "
                "one."
            )
