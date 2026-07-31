"""Screen: Default User Roles

Edits the 3 built-in role TEMPLATES (admin, technical, viewer) - the
company_id=NULL, is_builtin=True rows in the roles table. These are never
assigned to a User and never shown on the (company-facing) User Roles page.
They exist for exactly one purpose: role_provisioning.clone_builtin_roles_for_company
copies whatever page visibility is set here into a new company's own
built-in-role clones the moment that company is created.

Changing a template here only affects companies created AFTER the change -
it is not retroactive, on purpose (a company's own admin may have already
customized their clone; silently overwriting that would be its own kind of
cross-tenant surprise). See db.py's Role docstring for the full story of
why built-in roles are cloned per company instead of staying one shared row.

Platform-owner-only (see auth.require_platform_owner).
"""

import streamlit as st

from access_control import PAGE_CATALOG, denied_page_keys
from auth import logout_button, require_login, require_platform_owner
from db import Role, RolePagePermission, get_session, init_db
from helpers import clickable_table, page_setup, render_function_action_intro

page_setup("Default User Roles")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Default User Roles")
render_function_action_intro(
    function_text=(
        "Sets the starting page visibility for the 3 built-in roles (admin, technical, viewer) "
        "that every new company is seeded with. Not the roles themselves - those are cloned per "
        "company on creation so one company narrowing 'viewer' can never affect another company's "
        "'viewer'. This page only edits the template those clones are copied from."
    ),
    action_text=(
        "Uncheck a page for a role to have new companies start with that page hidden for anyone "
        "with that role. Changing this does NOT retroactively change any existing company's roles - "
        "each company's own admin (or the platform owner, from the User Roles page) narrows their "
        "own copy independently after creation."
    ),
)
session = get_session()

templates = (
    session.query(Role)
    .filter(Role.company_id.is_(None), Role.is_builtin.is_(True))
    .order_by(Role.name)
    .all()
)

if not templates:
    st.error(
        "No built-in role templates found - this shouldn't happen. The 3 templates "
        "(admin/technical/viewer) should have been seeded when the app's schema was set up."
    )
    st.stop()

st.caption("Click a role to edit which pages new companies start with visible for it.")
template_rows = [{"Role": t.name, "Description": t.description or "—"} for t in templates]
idx = clickable_table(template_rows, key="default_roles_table")
if idx is not None:
    st.session_state["default_role_selected_id"] = templates[idx].id
else:
    st.session_state.pop("default_role_selected_id", None)

selected_id = st.session_state.get("default_role_selected_id")
selected = next((t for t in templates if t.id == selected_id), None)

if selected:
    st.markdown(f"**Edit default page visibility: {selected.name}**")
    currently_denied = denied_page_keys(session, selected.id)
    with st.form(f"edit_default_role_{selected.id}"):
        checked = {}
        page_items = list(PAGE_CATALOG.items())
        cols = st.columns(3)
        for i, (page_key, title) in enumerate(page_items):
            with cols[i % 3]:
                checked[page_key] = st.checkbox(
                    title, value=page_key not in currently_denied, key=f"default_perm_{selected.id}_{page_key}"
                )
        if st.form_submit_button("Save default page visibility"):
            session.query(RolePagePermission).filter(RolePagePermission.role_id == selected.id).delete(
                synchronize_session=False
            )
            for page_key, is_visible in checked.items():
                if not is_visible:
                    session.add(
                        RolePagePermission(role_id=selected.id, page_key=page_key, can_view=False)
                    )
            session.commit()
            st.success(f"Default page visibility for '{selected.name}' updated for future companies.")
            st.rerun()

    if st.button("Clear selection", key="clear_default_role_selection"):
        st.session_state.pop("default_role_selected_id", None)
        st.rerun()
