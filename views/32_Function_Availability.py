"""Screen: Function Availability

Which pages a customer has actually been implemented with. Not every customer
needs every function, and until now every company got the whole application
whether they had bought it, been trained on it, or had any data for it.

This sits ABOVE the role permissions on User Roles: that page answers "what
may this role do inside a page the customer has", this one answers "does the
customer have the page at all". A page switched off here is invisible to every
user of that company regardless of role, including their own administrator,
because the reason it is off is commercial and implementational - not a
permission the customer administers for themselves. See access_control.py's
module docstring, rule 0.

Deny-list, like RolePagePermission: a row exists only to switch a page OFF, so
a company with nothing configured keeps the full application. That is why
every company that predates this screen was unaffected by it, and why a
company created before its implementation sheet is loaded is over-served
rather than left looking broken.

Two ways in, both landing on the same rows (Stefan, 20 August 2026):
  - the grid below, filled in directly; and
  - a workbook that mirrors it, sent to the customer before implementation,
    filled in by them, and read back here.
The workbook is generated from access_control.CONFIGURABLE_PAGE_KEYS, so a
page added to the application appears in the next sheet without anyone
remembering to add it.

Plant-level override: agreed in principle, not built. Navigation is drawn once
per browser session before any page has picked a plant, so a plant row cannot
hide a menu item today. db.CompanyPageAvailability already carries the column;
see the note above that model.

Platform-owner-only (see auth.require_platform_owner).
"""

import streamlit as st

import audit_log
import reports
from access_control import (
    CONFIGURABLE_PAGE_KEYS,
    PAGE_CATALOG,
    PAGE_SECTION,
    SECTION_ORDER,
    current_page_availability,
    save_page_availability,
)
from auth import current_user, logout_button, require_login, require_platform_owner
from db import Company, get_session, init_db
from helpers import page_setup, render_function_action_intro, set_pending_banner, show_pending_banner

page_setup("Function Availability")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Function Availability")
render_function_action_intro(
    function_text=(
        "Sets which pages a customer has been implemented with. A page switched off here does not "
        "appear in that company's navigation at all, for any of their users including their own "
        "administrator - it is not a permission they can grant themselves. A company with nothing "
        "set here has the full application."
    ),
    action_steps=[
        "Pick the customer at the top.",
        "Either set each page below to included or not and save, or open the Customer workbook "
        "tab, send the customer the workbook, and load their completed file back here.",
        "Check the count above the tabs afterwards - it states how many pages that customer now "
        "has.",
    ],
    action_note=(
        "The workbook and the grid write the same rows, so either route can be used and one can "
        "correct the other afterwards. HTC's own screens (Companies, Subscription Types, PI3 "
        "Connectivity, Performance, Company Analysis, AI Audit & Compliance and this page) are "
        "not listed - they are not sold. User Roles and User Accounts are not listed either: a "
        "company that cannot reach those can never manage its own users again, and nothing in "
        "the application could put that right."
    ),
)

session = get_session()
user = current_user()
show_pending_banner("fa_banner")

companies = session.query(Company).order_by(Company.name).all()

if not companies:
    st.info("There are no companies on the system yet.")
else:
    labels = {c.id: c.name for c in companies}
    company = st.selectbox(
        "Customer", companies, format_func=lambda c: labels.get(c.id, "?"), key="fa_company",
    )

    if company is None:
        st.info("Select a customer.")
    else:
        if company.is_platform_owner:
            st.warning(
                "This is HTC's own company, not a customer. Switching a page off here hides it "
                "from HTC's own users, which is only useful for demonstrating what a customer "
                "would see."
            )

        availability = current_page_availability(session, company.id)
        keys_in_order = sorted(
            CONFIGURABLE_PAGE_KEYS,
            key=lambda k: (
                SECTION_ORDER.index(PAGE_SECTION[k]) if PAGE_SECTION[k] in SECTION_ORDER else 99,
                PAGE_CATALOG[k].lower(),
            ),
        )
        included_now = sum(1 for k in keys_in_order if availability.get(k, True))
        st.caption(
            "%s currently has %d of %d configurable pages, plus the Overview and their own user "
            "administration, which are always available."
            % (company.name, included_now, len(keys_in_order))
        )

        grid_tab, workbook_tab = st.tabs(["Set it here", "Customer workbook"])

        # ------------------------------------------------------------------
        # 1. The grid
        # ------------------------------------------------------------------
        with grid_tab:
            with st.form("fa_grid"):
                chosen = {}
                for section in SECTION_ORDER:
                    section_keys = [k for k in keys_in_order if PAGE_SECTION[k] == section]
                    if not section_keys:
                        continue
                    st.markdown("**%s**" % section)
                    cols = st.columns(2)
                    for i, page_key in enumerate(section_keys):
                        with cols[i % 2]:
                            chosen[page_key] = st.checkbox(
                                PAGE_CATALOG[page_key],
                                value=availability.get(page_key, True),
                                key="fa_%d_%s" % (company.id, page_key),
                            )
                    st.write("")

                if st.form_submit_button("Save for %s" % company.name):
                    off = save_page_availability(
                        session, company.id, chosen, set_by=user.get("display_name") or user.get("username"),
                    )
                    audit_log.log_role_change(
                        session,
                        target_type="function_availability",
                        change_summary=(
                            "Function availability set: %d of %d pages included"
                            % (len(chosen) - len(off), len(chosen))
                            + (" (excluded: %s)" % ", ".join(PAGE_CATALOG[k] for k in off) if off else "")
                        ),
                        changed_by_user_id=user.get("id"),
                        company_id=company.id,
                        target_id=company.id,
                        target_label=company.name,
                    )
                    session.commit()
                    set_pending_banner(
                        "fa_banner",
                        "Saved. %s now has %d of %d configurable pages."
                        % (company.name, len(chosen) - len(off), len(chosen)),
                    )
                    st.rerun()

            st.caption(
                "A change takes effect on that company's next page view. Anyone of theirs already "
                "signed in keeps the navigation they loaded with until they move to another page."
            )

        # ------------------------------------------------------------------
        # 2. The workbook
        # ------------------------------------------------------------------
        with workbook_tab:
            st.markdown("**Send to the customer**")
            st.caption(
                "The workbook lists every page with its current setting. The customer marks each "
                "one Yes or No and sends it back."
            )
            st.download_button(
                "Download the implementation workbook",
                data=reports.render_function_availability_xlsx(company.name, availability),
                file_name="PI3_Implementation_Scope_%s.xlsx"
                % (company.name or "customer").replace(" ", "_"),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="fa_download",
            )

            st.divider()
            st.markdown("**Load a completed workbook**")
            uploaded = st.file_uploader(
                "Completed workbook", type=["xlsx"], key="fa_upload_%d" % company.id,
            )

            if uploaded is None:
                st.caption(
                    "Nothing is written until the change has been shown and confirmed below."
                )
            else:
                proposed, problems, missing = reports.read_function_availability_xlsx(uploaded)

                if problems:
                    # All-or-nothing, the same rule the raw-material lot importer
                    # follows: every fault is listed and nothing is written, because
                    # a half-applied scope leaves a customer with a navigation
                    # nobody intended and no record of what was skipped.
                    st.error(
                        "This workbook was not loaded. %d problem%s found - nothing has been "
                        "changed." % (len(problems), "" if len(problems) == 1 else "s")
                    )
                    for problem in problems:
                        st.write("- " + problem)
                else:
                    changes = [
                        (k, availability.get(k, True), proposed.get(k, True))
                        for k in keys_in_order
                        if availability.get(k, True) != proposed.get(k, True)
                    ]
                    if missing:
                        st.warning(
                            "%d page%s not mentioned in the sheet, so %s treated as included: %s"
                            % (
                                len(missing), "" if len(missing) == 1 else "s",
                                "it is" if len(missing) == 1 else "they are",
                                ", ".join(PAGE_CATALOG[k] for k in missing),
                            )
                        )
                    if not changes:
                        st.info(
                            "The workbook read cleanly and matches what %s already has. Nothing "
                            "to change." % company.name
                        )
                    else:
                        st.success("The workbook read cleanly. %d change%s to apply:" % (
                            len(changes), "" if len(changes) == 1 else "s"))
                        st.dataframe(
                            [
                                {
                                    "Section": PAGE_SECTION[k],
                                    "Page": PAGE_CATALOG[k],
                                    "Now": "Included" if was else "Not included",
                                    "After": "Included" if will else "Not included",
                                }
                                for k, was, will in changes
                            ],
                            hide_index=True,
                            use_container_width=True,
                        )
                        if st.button("Apply to %s" % company.name, key="fa_apply", type="primary"):
                            off = save_page_availability(
                                session, company.id, proposed,
                                set_by=user.get("display_name") or user.get("username"),
                            )
                            audit_log.log_role_change(
                                session,
                                target_type="function_availability",
                                change_summary=(
                                    "Function availability loaded from workbook: %d of %d pages "
                                    "included" % (len(proposed) - len(off), len(proposed))
                                    + (" (excluded: %s)" % ", ".join(PAGE_CATALOG[k] for k in off) if off else "")
                                ),
                                changed_by_user_id=user.get("id"),
                                company_id=company.id,
                                target_id=company.id,
                                target_label=company.name,
                            )
                            session.commit()
                            set_pending_banner(
                                "fa_banner",
                                "Workbook applied. %s now has %d of %d configurable pages."
                                % (company.name, len(proposed) - len(off), len(proposed)),
                            )
                            st.rerun()
