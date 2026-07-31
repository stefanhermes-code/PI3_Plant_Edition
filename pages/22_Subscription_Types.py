"""Screen: Subscription Types

The commercial tiers this app is sold under. Each type caps how many users
and plants a company can have (blank = unlimited) and gates whole feature
areas (Industrial Intelligence, Case Review, PI3/AI, Reports) for every
user at any company on that tier - including that company's own admin.
Platform-owner-only (see auth.require_platform_owner). No payment
processing is wired up here - this only tracks and enforces the
limits/features themselves.
"""

import streamlit as st

from auth import logout_button, require_login, require_platform_owner
from db import Company, SubscriptionType, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_setup, render_function_action_intro

page_setup("Subscription Types")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Subscription Types")
render_function_action_intro(
    function_text=(
        "Defines the commercial tiers companies subscribe to: a cap on user count and plant "
        "count (blank = unlimited), and on/off switches for Industrial Intelligence (Recipe "
        "Optimization, Trend Analysis, Correlation, Root-Cause Assistant, Machine Settings "
        "Optimization), Case Review (Similar Case Retrieval, Expert Notes), PI3/AI, and the "
        "Report screen. These limits are enforced when a company's admin tries to add a new "
        "user or plant beyond the cap, and the feature switches hide the relevant pages entirely "
        "for anyone at a company on that tier."
    ),
    action_text=(
        "Add a subscription type, set its limits/feature switches, then assign it to a company "
        "on the Companies page. Deactivate a type instead of deleting it once a company is using "
        "it - deletion is only offered while nothing is assigned to it."
    ),
)
session = get_session()

with st.expander("Add subscription type", expanded=False):
    with st.form("add_subscription_type"):
        name = st.text_input("Name * (e.g. Starter, Professional, Enterprise)")
        c1, c2 = st.columns(2)
        max_users = c1.number_input("Max users (0 = unlimited)", min_value=0, step=1, value=0)
        max_plants = c2.number_input("Max plants (0 = unlimited)", min_value=0, step=1, value=0)
        f1, f2, f3, f4 = st.columns(4)
        ii_enabled = f1.checkbox("Industrial Intelligence", value=True)
        cr_enabled = f2.checkbox("Case Review", value=True)
        ai_enabled = f3.checkbox("PI3/AI", value=True)
        reports_enabled = f4.checkbox("Reports", value=True)
        price_note = st.text_input("Price note (free text, e.g. \"$500/mo\" - not billed automatically)")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save subscription type")
        if submitted:
            if not name.strip():
                st.error("Name is required.")
            else:
                session.add(
                    SubscriptionType(
                        name=name.strip(),
                        max_users=int(max_users) or None,
                        max_plants=int(max_plants) or None,
                        industrial_intelligence_enabled=ii_enabled,
                        case_review_enabled=cr_enabled,
                        pi3_ai_enabled=ai_enabled,
                        reports_enabled=reports_enabled,
                        price_note=price_note or None,
                        notes=notes,
                        active=True,
                    )
                )
                session.commit()
                st.success(f"Subscription type '{name}' added.")
                st.rerun()

st.divider()
sub_types = session.query(SubscriptionType).order_by(SubscriptionType.name).all()
if not sub_types:
    st.info("No subscription types recorded yet.")
else:
    rows = [
        {
            "Name": s.name,
            "Max users": s.max_users if s.max_users else "Unlimited",
            "Max plants": s.max_plants if s.max_plants else "Unlimited",
            "Industrial Intelligence": "Yes" if s.industrial_intelligence_enabled else "No",
            "Case Review": "Yes" if s.case_review_enabled else "No",
            "PI3/AI": "Yes" if s.pi3_ai_enabled else "No",
            "Reports": "Yes" if s.reports_enabled else "No",
            "Companies": session.query(Company).filter(Company.subscription_type_id == s.id).count(),
            "Active": "Yes" if s.active else "No",
        }
        for s in sub_types
    ]
    st.caption("Click a row to edit that subscription type.")
    idx = clickable_table(rows, key="subscription_types_table")
    if idx is not None:
        st.session_state["subtype_selected_id"] = sub_types[idx].id
    else:
        st.session_state.pop("subtype_selected_id", None)

    selected_id = st.session_state.get("subtype_selected_id")
    selected = next((s for s in sub_types if s.id == selected_id), None)

    if selected:
        st.markdown(f"**Edit subscription type: {selected.name}**")
        with st.form(f"edit_subtype_{selected.id}"):
            e_name = st.text_input("Name *", value=selected.name, key=f"edit_st_name_{selected.id}")
            c1, c2 = st.columns(2)
            e_max_users = c1.number_input(
                "Max users (0 = unlimited)", min_value=0, step=1,
                value=int(selected.max_users or 0), key=f"edit_st_users_{selected.id}",
            )
            e_max_plants = c2.number_input(
                "Max plants (0 = unlimited)", min_value=0, step=1,
                value=int(selected.max_plants or 0), key=f"edit_st_plants_{selected.id}",
            )
            f1, f2, f3, f4 = st.columns(4)
            e_ii = f1.checkbox(
                "Industrial Intelligence", value=selected.industrial_intelligence_enabled, key=f"edit_st_ii_{selected.id}"
            )
            e_cr = f2.checkbox(
                "Case Review", value=selected.case_review_enabled, key=f"edit_st_cr_{selected.id}"
            )
            e_ai = f3.checkbox("PI3/AI", value=selected.pi3_ai_enabled, key=f"edit_st_ai_{selected.id}")
            e_reports = f4.checkbox(
                "Reports", value=selected.reports_enabled, key=f"edit_st_rep_{selected.id}"
            )
            e_price_note = st.text_input(
                "Price note", value=selected.price_note or "", key=f"edit_st_price_{selected.id}"
            )
            e_active = st.checkbox("Active", value=selected.active, key=f"edit_st_active_{selected.id}")
            e_notes = st.text_area("Notes", value=selected.notes or "", key=f"edit_st_notes_{selected.id}")
            if st.form_submit_button("Save changes"):
                if not e_name.strip():
                    st.error("Name is required.")
                else:
                    selected.name = e_name.strip()
                    selected.max_users = int(e_max_users) or None
                    selected.max_plants = int(e_max_plants) or None
                    selected.industrial_intelligence_enabled = e_ii
                    selected.case_review_enabled = e_cr
                    selected.pi3_ai_enabled = e_ai
                    selected.reports_enabled = e_reports
                    selected.price_note = e_price_note or None
                    selected.active = e_active
                    selected.notes = e_notes
                    session.commit()
                    st.success("Subscription type updated.")
                    st.rerun()

        companies_using = session.query(Company).filter(Company.subscription_type_id == selected.id).count()
        if companies_using:
            st.caption(
                f"{companies_using} compan{'y' if companies_using == 1 else 'ies'} currently on this tier - "
                "deactivate it instead of deleting, since deleting would leave them with no assigned tier."
            )
        else:
            def _do_delete_subtype(_session=session, _id=selected.id):
                _session.query(SubscriptionType).filter(SubscriptionType.id == _id).delete(synchronize_session=False)
                _session.commit()
                st.session_state.pop("subtype_selected_id", None)

            delete_with_confirm(
                f"'{selected.name}'", _do_delete_subtype, key_prefix=f"subtype_{selected.id}",
                extra_warning="No company is currently assigned to this subscription type - deleting it is safe.",
            )

        if st.button("Clear selection", key="clear_subtype_selection"):
            st.session_state.pop("subtype_selected_id", None)
            st.rerun()
