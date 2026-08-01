"""Screen: Companies

The tenant boundary for the whole app: every plant, raw material, supplier,
and user account belongs to exactly one company. Platform-owner-only (see
auth.require_platform_owner) - a customer's own admin manages their users
and custom roles, but never other companies or the subscription catalog.

Adding a company is treated as registering a new customer: legal entity
name, VAT number, billing address, and a contact person (name/email/phone)
are captured alongside the subscription assignment, in addition to the
short display `name` used everywhere else in the app (company pickers,
nav, etc.). The subscription type picker sits outside the save form so the
resulting fee is shown live as soon as one is chosen - never typed in by
hand. A subscription type is now a single tier/frequency combination (e.g.
"PI3 Plant Edition - Annual" vs "PI3 Plant Edition - Monthly" are two
separate types), so picking one sets the company's fee AND billing
frequency together - there is no separate frequency picker to keep in
sync.

Company deletion is deliberately not offered once a company has any real
data under it (users, plants, raw materials, suppliers) - deactivate it
instead so its history stays intact. A company can only be deleted while
it's still empty (e.g. created by mistake).
"""

import streamlit as st

from auth import current_user, logout_button, require_login, require_platform_owner
from db import Company, RawMaterial, Supplier, SubscriptionType, User, Plant, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_setup, render_function_action_intro
from role_provisioning import clone_builtin_roles_for_company

page_setup("Companies")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Companies")
render_function_action_intro(
    function_text=(
        "The tenant boundary for the whole app: every plant, raw material, supplier, and user "
        "account belongs to exactly one company. Registering a company captures its legal/billing "
        "details (legal entity name, VAT number, address, contact person) alongside a subscription "
        "type, which caps how many users/plants it can have, gates PI3/AI and Reports, and sets "
        "the fee and billing frequency together (each subscription type is a single fixed tier + "
        "frequency, e.g. 'PI3 Plant Edition - Monthly')."
    ),
    action_text=(
        "Add a company and assign it a subscription type - the fee and billing frequency show "
        "automatically once it's picked - then go to User Accounts to create its first admin "
        "user. Click an existing company to edit any of its details. Deactivate a company (rather "
        "than deleting it) once it has real data - deletion is only offered while a company is "
        "still empty."
    ),
)


def _fee_for(subscription):
    """Human-readable fee for a given subscription type, with no Company
    object required - used to show a live fee preview while adding/editing,
    before anything is saved. Each subscription type is a single fixed
    tier + billing frequency (see SubscriptionType.billing_frequency), so
    there's no separate frequency argument to pass in anymore."""
    if subscription is None or not subscription.price:
        return "—"
    freq_label = "yr" if (subscription.billing_frequency or "Annual") == "Annual" else "mo"
    return f"${subscription.price:,.0f}/plant/{freq_label}"


def _effective_fee(company):
    return _fee_for(company.subscription_type)


session = get_session()
user = current_user()

subscription_types = session.query(SubscriptionType).order_by(SubscriptionType.name).all()

with st.expander("Add company", expanded=False):
    st.caption("Subscription type (fee and billing frequency update automatically as you choose):")
    add_subscription = st.selectbox(
        "Subscription type", [None] + subscription_types,
        format_func=lambda s: "— none assigned —" if s is None else s.name,
        key="add_co_subscription",
    )
    st.metric("Fee", _fee_for(add_subscription))

    with st.form("add_company"):
        st.markdown("**Company**")
        name = st.text_input("Company name (display name) *")
        legal_entity_name = st.text_input("Legal entity name (if different from display name)")
        vat_number = st.text_input("VAT number")
        st.markdown("**Address**")
        address = st.text_input("Address")
        addr_col1, addr_col2, addr_col3 = st.columns(3)
        with addr_col1:
            city = st.text_input("City")
        with addr_col2:
            postal_code = st.text_input("Postal code")
        with addr_col3:
            country = st.text_input("Country")
        st.markdown("**Contact person**")
        c_col1, c_col2, c_col3 = st.columns(3)
        with c_col1:
            contact_name = st.text_input("Contact name")
        with c_col2:
            contact_email = st.text_input("Contact email")
        with c_col3:
            contact_phone = st.text_input("Contact phone")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save company")
        if submitted:
            if not name.strip():
                st.error("Company name is required.")
            else:
                new_company = Company(
                    name=name.strip(),
                    legal_entity_name=legal_entity_name.strip() or None,
                    vat_number=vat_number.strip() or None,
                    address=address.strip() or None,
                    city=city.strip() or None,
                    postal_code=postal_code.strip() or None,
                    country=country.strip() or None,
                    subscription_type_id=add_subscription.id if add_subscription else None,
                    contact_name=contact_name,
                    contact_email=contact_email,
                    contact_phone=contact_phone.strip() or None,
                    notes=notes,
                    active=True,
                )
                session.add(new_company)
                session.flush()  # need new_company.id before cloning its roles
                clone_builtin_roles_for_company(session, new_company.id)
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
            "Billing": (c.subscription_type.billing_frequency or "Annual") if c.subscription_type else "—",
            "Fee": _effective_fee(c),
            "Country": c.country or "—",
            "Platform owner": "Yes" if c.is_platform_owner else "",
            "Users": session.query(User).filter(User.company_id == c.id).count(),
            "Plants": session.query(Plant).filter(Plant.company_id == c.id).count(),
            "Active": "Yes" if c.active else "No",
        }
        for c in companies
    ]
    st.caption("Click a row to edit that company.")
    idx = clickable_table(company_rows, key="companies_table")
    if idx is not None and idx < len(companies):
        # The bounds check guards a stale selection surviving a delete: right
        # after deleting the selected row, the table shrinks by one but the
        # dataframe widget's selection state can still report the old index
        # until the operator clicks again.
        st.session_state["company_selected_id"] = companies[idx].id
    else:
        st.session_state.pop("company_selected_id", None)

    selected_id = st.session_state.get("company_selected_id")
    selected = next((c for c in companies if c.id == selected_id), None)

    if selected:
        st.markdown(f"**Edit company: {selected.name}**")

        st.caption("Subscription type (fee and billing frequency update automatically as you choose):")
        e_sub = st.selectbox(
            "Subscription type", [None] + subscription_types,
            index=(
                ([None] + subscription_types).index(selected.subscription_type)
                if selected.subscription_type in subscription_types else 0
            ),
            format_func=lambda s: "— none assigned —" if s is None else s.name,
            key=f"edit_co_sub_{selected.id}",
        )
        st.metric("Fee", _fee_for(e_sub))

        with st.form(f"edit_company_{selected.id}"):
            st.markdown("**Company**")
            e_name = st.text_input("Company name (display name) *", value=selected.name, key=f"edit_co_name_{selected.id}")
            e_legal_name = st.text_input(
                "Legal entity name (if different from display name)",
                value=selected.legal_entity_name or "", key=f"edit_co_legal_{selected.id}",
            )
            e_vat = st.text_input("VAT number", value=selected.vat_number or "", key=f"edit_co_vat_{selected.id}")
            st.markdown("**Address**")
            e_address = st.text_input("Address", value=selected.address or "", key=f"edit_co_address_{selected.id}")
            e_addr_col1, e_addr_col2, e_addr_col3 = st.columns(3)
            with e_addr_col1:
                e_city = st.text_input("City", value=selected.city or "", key=f"edit_co_city_{selected.id}")
            with e_addr_col2:
                e_postal = st.text_input(
                    "Postal code", value=selected.postal_code or "", key=f"edit_co_postal_{selected.id}"
                )
            with e_addr_col3:
                e_country = st.text_input(
                    "Country", value=selected.country or "", key=f"edit_co_country_{selected.id}"
                )
            st.markdown("**Contact person**")
            e_c_col1, e_c_col2, e_c_col3 = st.columns(3)
            with e_c_col1:
                e_contact_name = st.text_input(
                    "Contact name", value=selected.contact_name or "", key=f"edit_co_cname_{selected.id}"
                )
            with e_c_col2:
                e_contact_email = st.text_input(
                    "Contact email", value=selected.contact_email or "", key=f"edit_co_cemail_{selected.id}"
                )
            with e_c_col3:
                e_contact_phone = st.text_input(
                    "Contact phone", value=selected.contact_phone or "", key=f"edit_co_cphone_{selected.id}"
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
                    selected.legal_entity_name = e_legal_name.strip() or None
                    selected.vat_number = e_vat.strip() or None
                    selected.address = e_address.strip() or None
                    selected.city = e_city.strip() or None
                    selected.postal_code = e_postal.strip() or None
                    selected.country = e_country.strip() or None
                    selected.subscription_type_id = e_sub.id if e_sub else None
                    selected.contact_name = e_contact_name
                    selected.contact_email = e_contact_email
                    selected.contact_phone = e_contact_phone.strip() or None
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
