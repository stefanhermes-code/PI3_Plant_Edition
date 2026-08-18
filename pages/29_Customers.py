"""Screen: Customers (master data)

Ported from PI3 Rigid Foam Edition's CR-14 (12 Aug 2026) so both editions model
customer identity the same way.

Deliberately lightweight - a practical application reference rather than a full
CRM: Customer Name, Contact Person, Contact Email, and an optional free-text
Customer Type.

The field is labelled "Customer Name" everywhere in the UI, but the column
behind it is Customer.company_name. That mismatch is deliberate (18 Aug 2026,
Stefan): this table lists customers, and "Company Name" sitting next to the
"Company" column - which is the TENANT that owns the record - read as if the
two were the same thing. Renaming the label fixed the confusion with no
migration; renaming the column would have meant touching the unique
constraint, cascades.backfill_trial_customers(), the importer and every
read site for no user-visible gain. Sales pipeline, multiple contacts, commercial history and
customer-category governance are out of scope.

Before this, a customer existed only as free text on CustomerTrial.customer_name
and drifted the way every free-text reference in this app has. Customer Trials &
Samples sources its customer from this master via CustomerTrial.customer_id;
customer_name is kept beside it as a synced display snapshot so no historical
row loses information. See db.py's CustomerTrial docstring, and
cascades.backfill_trial_customers() for how pre-existing text values are mapped
without ever silently merging two different-looking names.
"""

import re

import pandas as pd
import streamlit as st

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from cascades import backfill_trial_customers
from db import Customer, CustomerTrial, get_session, init_db
from helpers import (
    clickable_table,
    csv_excel_uploader,
    delete_with_confirm,
    page_setup,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    view_only_notice,
)
from tenant_scope import company_picker

# customer_name, not company_name, for the same reason the labels changed
# above. Safe to change outright rather than accepting both: no customer has
# ever been created by import (the only row in the table came from
# backfill_trial_customers), so there is no existing spreadsheet to break.
CUSTOMER_REQUIRED_COLUMNS = ["customer_name"]
CUSTOMER_OPTIONAL_COLUMNS = ["contact_person", "contact_email", "customer_type"]

# Local rather than in helpers.py: this is the only page that validates an
# email, and a shared helper would be a wider change than this page warrants.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(value):
    """Empty is valid - Contact Email is optional."""
    text = (value or "").strip()
    return True if not text else bool(_EMAIL_RE.match(text))


page_setup("Customers")
init_db()
require_login()
logout_button()

st.title("Customers")
render_function_action_intro(
    function_text=(
        "Maintains a lightweight master list of customers - Customer Name, Contact Person, Contact "
        "Email and an optional Customer Type - so customer identity has one home instead of living "
        "only as free text on Customer Trials & Samples. A practical reference, not a CRM: no sales "
        "pipeline, multiple contacts or commercial history."
    ),
    action_text=(
        "Add a customer manually, or bulk-load a list via CSV/Excel import. Customer Trials & Samples "
        "picks its customer from this list. Use 'Link existing trials' below to map trials that were "
        "recorded before this page existed."
    ),
)

session = get_session()
user = current_user()
is_platform_owner = user["is_platform_owner"]
own_company_id = user["company_id"]
page_usable = can_use_page(
    "customers", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"]
)
if not page_usable:
    view_only_notice()

company_filter, all_companies = company_picker(
    st, session, is_platform_owner, own_company_id, key="customer_company_filter"
)
if not is_platform_owner and not company_filter:
    st.warning("Your account isn't linked to a company yet - contact the platform administrator.")
    st.stop()


def _target_company(key):
    """Company a new customer should be created under - same shape as the
    Raw Materials page's own target-company logic."""
    if not is_platform_owner:
        return company_filter
    if company_filter is not None:
        return company_filter
    return st.selectbox("Company *", all_companies, format_func=lambda c: c.name, key=key)


tab_create, tab_manage, tab_import = st.tabs(["Add customer", "Edit / delete", "CSV / Excel import"])

with tab_create:
    if not page_usable:
        st.caption("View-only access - adding a customer is restricted for your role.")
    else:
        target_company = _target_company("add_customer_company")
        with st.form("add_customer"):
            new_name = st.text_input("Customer Name *")
            new_contact = st.text_input("Contact Person")
            new_email = st.text_input("Contact Email")
            new_type = st.text_input(
                "Customer Type", help="Optional free text - there is no fixed category list."
            )
            if st.form_submit_button("Add customer"):
                name = new_name.strip()
                if not name:
                    st.error("Customer Name is required.")
                elif not target_company:
                    st.error("Pick a company for this customer.")
                elif not _valid_email(new_email):
                    st.error("Contact Email doesn't look like a valid email address.")
                elif (
                    session.query(Customer)
                    .filter(Customer.company_name == name, Customer.company_id == target_company.id)
                    .first()
                ):
                    st.error(f"'{name}' is already in the list.")
                else:
                    session.add(
                        Customer(
                            company_id=target_company.id,
                            company_name=name,
                            contact_person=new_contact.strip(),
                            contact_email=new_email.strip(),
                            customer_type=new_type.strip(),
                        )
                    )
                    session.commit()
                    st.success(f"Customer '{name}' added.")
                    st.rerun()

with tab_manage:
    customers_query = session.query(Customer)
    if company_filter is not None:
        customers_query = customers_query.filter(Customer.company_id == company_filter.id)
    customers = customers_query.order_by(Customer.company_name).all()

    if not customers:
        st.info("No customers recorded yet.")
    else:
        rows = [
            {
                **({"Company": c.company.name if c.company else "—"} if is_platform_owner else {}),
                "Customer Name": c.company_name,
                "Contact Person": c.contact_person or "—",
                "Contact Email": c.contact_email or "—",
                "Customer Type": c.customer_type or "—",
                "Linked trials": session.query(CustomerTrial)
                .filter(CustomerTrial.customer_id == c.id)
                .count(),
            }
            for c in customers
        ]
        st.caption(f"{len(customers)} customer(s). Click a row to edit or delete it.")
        idx = clickable_table(rows, key="customer_table")
        if idx is not None and idx < len(customers):
            st.session_state["customer_selected_id"] = customers[idx].id
        elif st.session_state.get("customer_selected_id") not in {c.id for c in customers}:
            st.session_state.pop("customer_selected_id", None)

        selected = next(
            (c for c in customers if c.id == st.session_state.get("customer_selected_id")), None
        )

        if selected is not None:
            st.subheader(f"Edit: {selected.company_name}")
            if not page_usable:
                st.caption("View-only access - editing and deleting is restricted for your role.")
            else:
                with st.form(f"edit_customer_{selected.id}"):
                    if is_platform_owner:
                        e_company = st.selectbox(
                            "Company *", all_companies,
                            index=next(
                                (i for i, c in enumerate(all_companies) if c.id == selected.company_id), 0
                            ),
                            format_func=lambda c: c.name,
                            key=f"edit_customer_company_{selected.id}",
                        )
                    else:
                        e_company = company_filter
                    e_name = st.text_input(
                        "Customer Name *", value=selected.company_name,
                        key=f"edit_customer_name_{selected.id}",
                    )
                    e_contact = st.text_input(
                        "Contact Person", value=selected.contact_person or "",
                        key=f"edit_customer_contact_{selected.id}",
                    )
                    e_email = st.text_input(
                        "Contact Email", value=selected.contact_email or "",
                        key=f"edit_customer_email_{selected.id}",
                    )
                    e_type = st.text_input(
                        "Customer Type", value=selected.customer_type or "",
                        key=f"edit_customer_type_{selected.id}",
                    )
                    if st.form_submit_button("Save changes"):
                        name = e_name.strip()
                        if not name:
                            st.error("Customer Name is required.")
                        elif not _valid_email(e_email):
                            st.error("Contact Email doesn't look like a valid email address.")
                        else:
                            renamed = name != selected.company_name
                            selected.company_id = e_company.id if e_company else selected.company_id
                            selected.company_name = name
                            selected.contact_person = e_contact.strip()
                            selected.contact_email = e_email.strip()
                            selected.customer_type = e_type.strip()
                            if renamed:
                                # customer_id is the live link, but customer_name
                                # is still the display snapshot every trial and
                                # report reads - keep it consistent on rename.
                                session.query(CustomerTrial).filter(
                                    CustomerTrial.customer_id == selected.id
                                ).update({"customer_name": name}, synchronize_session="fetch")
                            session.commit()
                            st.success("Customer updated.")
                            st.rerun()

                linked = (
                    session.query(CustomerTrial)
                    .filter(CustomerTrial.customer_id == selected.id)
                    .count()
                )
                warning = (
                    f"{linked} customer trial(s) link to this customer. Deleting it removes only the "
                    "master record - those trials keep their customer name as text and are not deleted."
                    if linked
                    else "No customer trials link to this customer - deleting it is safe."
                )

                def _do_delete_customer(_session=session, _id=selected.id):
                    # customer_id is a real foreign key, so clear it on every
                    # linked trial first; the trial's customer_name snapshot is
                    # what remains.
                    _session.query(CustomerTrial).filter(CustomerTrial.customer_id == _id).update(
                        {"customer_id": None}, synchronize_session="fetch"
                    )
                    _session.query(Customer).filter(Customer.id == _id).delete(synchronize_session=False)
                    _session.commit()
                    st.session_state.pop("customer_selected_id", None)

                delete_with_confirm(
                    f"customer '{selected.company_name}'", _do_delete_customer,
                    key_prefix=f"customer_{selected.id}", extra_warning=warning,
                )

            if st.button("Clear selection", key="clear_customer_selection"):
                st.session_state.pop("customer_selected_id", None)
                st.rerun()

    st.divider()
    st.markdown("**Link existing trials**")
    st.caption(
        "Maps customer trials recorded before this page existed onto the master, creating a customer "
        "where none matches. Matching is exact and case-insensitive; two different-looking names are "
        "never merged, they are reported below for you to decide."
    )
    unlinked = session.query(CustomerTrial).filter(CustomerTrial.customer_id.is_(None)).count()
    st.caption(f"{unlinked} trial(s) currently have no customer linked.")
    if page_usable and st.button("Link existing trials", key="backfill_trial_customers"):
        result = backfill_trial_customers(session)
        st.success(
            f"Linked {result['linked']} trial(s), creating {result['created']} customer(s)."
        )
        if result["possible_duplicates"]:
            st.warning(
                "These look like they could be the same customer entered twice. Nothing was merged - "
                "review and rename or delete as you see fit."
            )
            render_data_table(
                pd.DataFrame(
                    [
                        {"Name": a, "Looks like": b, "Similarity": ratio}
                        for _company_id, a, b, ratio in result["possible_duplicates"]
                    ]
                )
            )
        st.rerun()

with tab_import:
    if not page_usable:
        st.caption("View-only access - importing customers is restricted for your role.")
    else:
        import_company = _target_company("import_customer_company")
        show_pending_banner("customer_import_msg")
        df, filename = csv_excel_uploader(
            CUSTOMER_REQUIRED_COLUMNS, CUSTOMER_OPTIONAL_COLUMNS, key="customer_upload"
        )
        if df is not None and not import_company:
            st.error("Pick a company above before importing.")
        elif df is not None:
            existing_names = {
                c.company_name.strip().lower()
                for c in session.query(Customer)
                .filter(Customer.company_id == import_company.id)
                .all()
            }
            good_rows, dup_rows = [], []
            for _, row in df.iterrows():
                name_val = str(row.get("customer_name", "") or "").strip()
                if not name_val:
                    continue
                if name_val.lower() in existing_names:
                    dup_rows.append(row)
                else:
                    good_rows.append(row)
                    existing_names.add(name_val.lower())

            st.write(
                f"Rows ready to import: **{len(good_rows)}** | Rows flagged as duplicates: **{len(dup_rows)}**"
            )
            if dup_rows:
                st.warning("These rows match a customer already in the list and were skipped.")
                render_data_table(pd.DataFrame(dup_rows), max_height="400px")

            if good_rows and st.button("Confirm import", key="confirm_customer_import"):
                for row in good_rows:
                    session.add(
                        Customer(
                            company_id=import_company.id,
                            company_name=str(row["customer_name"]).strip(),
                            contact_person=str(row.get("contact_person", "") or "").strip(),
                            contact_email=str(row.get("contact_email", "") or "").strip(),
                            customer_type=str(row.get("customer_type", "") or "").strip(),
                        )
                    )
                session.commit()
                set_pending_banner(
                    "customer_import_msg", f"Imported {len(good_rows)} customer(s) from {filename}."
                )
                st.rerun()
