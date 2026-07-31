"""Screen: Companies

The tenant boundary for the whole app: every plant, raw material, supplier,
and user account belongs to exactly one company. Platform-owner-only (see
auth.require_platform_owner) - a customer's own admin manages their users
and custom roles, but never other companies or the subscription catalog.

Company deletion is deliberately not offered once a company has any real
data under it (users, plants, raw materials, suppliers) - deactivate it
instead so its history stays intact. A company can only be deleted while
it's still empty (e.g. created by mistake).
"""

import streamlit as st

from auth import current_user, logout_button, require_login, require_platform_owner
from db import Company, RawMaterial, Supplier, SubscriptionType, User, Plant, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_setup, render_function_action_intro

page_setup("Companies")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Companies")
render_function_action_intro(
    function_text=(
        "The tenant boundary for the whole app: every plant, raw material, supplier, and user "
        "account belongs to exactly one company. Each company is assigned a subscription type, "
        "which caps how many users/plants it can have and gates whole feature areas (Industrial "
        "Intelligence, PI3/AI, Reports)."
    ),
    action_text=(
        "Add a company, assign it a subscription type, then go to User Accounts to create its "
        "first admin user. Deactivate a company (rather than deleting it) once it has real data - "
        "deletion is only offered while a company is still empty."
    ),
)
session = get_session()
user = current_user()

subscription_types = session.query(SubscriptionType).order_by(SubscriptionType.name).all()

with st.expander("Add company", expanded=False):
    with st.form("add_company"):
        name = st.text_input("Company name *")
        subscription = st.selectbox(
            "Subscription type", [None] + subscription_types,
            format_func=lambda s: "— none assigned —" if s is None else s.name,
        )
        contact_name = st.text_input("Contact name")
        contact_email = st.text_input("Contact email")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save company")
        if submitted:
            if not name.strip():
                st.error("Company name is required.")
            else:
                session.add(
                    Company(
                        name=name.strip(),
                        subscription_type_id=subscription.id if subscription else None,
                        contact_name=contact_name,
                        contact_email=contact_email,
                        notes=notes,
                        active=True,
                    )
                )
                session.commit()
                st.success(f"Company '{name}' added. Go to User Accounts to create its first user.")
                st.rerun()

st.divider()
companies = session.query(Company).order_by(Company.name).all()
if not companies:
    st.info("No companies recorded yet.")
else:
    company_rows = [
        {
            "Name": c.name,
            "Subscription": c.subscription_type.name if c.subscription_type else "—",
            "Platform owner": "Yes" if c.is_platform_owner else "",
            "Users": session.query(User).filter(User.company_id == c.id).count(),
            "Plants": session.query(Plant).filter(Plant.company_id == c.id).count(),
            "Active": "Yes" if c.active else "No",
        }
        for c in companies
    ]
    st.caption("Click a row to edit that company.")
    idx = clickable_table(company_rows, key="companies_table")
    if idx is not None:
        st.session_state["company_selected_id"] = companies[idx].id
    else:
        st.session_state.pop("company_selected_id", None)

    selected_id = st.session_state.get("company_selected_id")
    selected = next((c for c in companies if c.id == selected_id), None)

    if selected:
        st.markdown(f"**Edit company: {selected.name}**")
        with st.form(f"edit_company_{selected.id}"):
            e_name = st.text_input("Company name *", value=selected.name, key=f"edit_co_name_{selected.id}")
            e_sub = st.selectbox(
                "Subscription type", [None] + subscription_types,
                index=(
                    ([None] + subscription_types).index(selected.subscription_type)
                    if selected.subscription_type in subscription_types else 0
                ),
                format_func=lambda s: "— none assigned —" if s is None else s.name,
                key=f"edit_co_sub_{selected.id}",
            )
            e_contact_name = st.text_input(
                "Contact name", value=selected.contact_name or "", key=f"edit_co_cname_{selected.id}"
            )
            e_contact_email = st.text_input(
                "Contact email", value=selected.contact_email or "", key=f"edit_co_cemail_{selected.id}"
            )
            e_active = st.checkbox("Active", value=selected.active, key=f"edit_co_active_{selected.id}")
            e_notes = st.text_area("Notes", value=selected.notes or "", key=f"edit_co_notes_{selected.id}")
            if selected.is_platform_owner:
                st.caption("This is the platform-owner company - it cannot be deactivated.")
            if st.form_submit_button("Save changes"):
                if not e_name.strip():
                    st.error("Company name is required.")
                else:
                    selected.name = e_name.strip()
                    selected.subscription_type_id = e_sub.id if e_sub else None
                    selected.contact_name = e_contact_name
                    selected.contact_email = e_contact_email
                    selected.active = e_active or selected.is_platform_owner
                    selected.notes = e_notes
                    session.commit()
                    st.success("Company updated.")
                    st.rerun()

        related_counts = {
            "user(s)": session.query(User).filter(User.company_id == selected.id).count(),
            "plant(s)": session.query(Plant).filter(Plant.company_id == selected.id).count(),
            "raw material(s)": session.query(RawMaterial).filter(RawMaterial.company_id == selected.id).count(),
            "supplier(s)": session.query(Supplier).filter(Supplier.company_id == selected.id).count(),
        }
        total_related = sum(related_counts.values())
        if selected.is_platform_owner:
            st.caption("The platform-owner company cannot be deleted.")
        elif total_related:
            detail = ", ".join(f"{n} {k}" for k, n in related_counts.items() if n)
            st.caption(
                f"This company has {detail} - deactivate it instead of deleting, so that data's "
                "history stays intact."
            )
        else:
            def _do_delete_company(_session=session, _id=selected.id):
                _session.query(Company).filter(Company.id == _id).delete(synchronize_session=False)
                _session.commit()
                st.session_state.pop("company_selected_id", None)

            delete_with_confirm(
                f"'{selected.name}'", _do_delete_company, key_prefix=f"company_{selected.id}",
                extra_warning="This company has no users, plants, raw materials, or suppliers yet - deleting it is safe.",
            )

        if st.button("Clear selection", key="clear_company_selection"):
            st.session_state.pop("company_selected_id", None)
            st.rerun()
