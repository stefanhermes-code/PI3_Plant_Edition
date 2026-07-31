"""Screen: User Roles

Three built-in roles (admin, technical, viewer) ship for every company and
can't be renamed or deleted - see auth.py's docstring for what each grants
today. Any company's own admin can also define custom roles scoped to just
that company. Every role (built-in or custom) can have its page visibility
narrowed on this screen: unchecking a page here hides it from anyone with
that role, on top of whatever their company's subscription already hides.

The platform owner (HTC) sees and manages every company's custom roles;
a company's own admin only sees the built-in roles plus their own.
"""

import streamlit as st

from access_control import PAGE_CATALOG, denied_page_keys
from auth import current_user, logout_button, require_login, require_role
from db import Company, Role, RolePagePermission, User, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_setup, render_function_action_intro

page_setup("User Roles")
init_db()
require_login()
require_role("admin")
logout_button()

st.title("User Roles")
render_function_action_intro(
    function_text=(
        "Three built-in roles (admin, technical, viewer) are available to every company and "
        "can't be renamed or deleted. Any company's own admin can also define custom roles scoped "
        "to just that company. Every role's page visibility can be narrowed here - unchecking a "
        "page hides it from anyone with that role."
    ),
    action_text=(
        "Add a custom role if the three built-in ones don't fit (e.g. a 'plant floor' role that "
        "can only see Production Run and Quality screens). Click a role below to edit its page "
        "visibility, or its name/description for custom roles - built-in roles can only have their "
        "page visibility adjusted, not their name."
    ),
)
session = get_session()
user = current_user()
is_platform_owner = user["is_platform_owner"]
own_company_id = user["company_id"]

companies = session.query(Company).order_by(Company.name).all() if is_platform_owner else []

with st.expander("Add custom role", expanded=False):
    with st.form("add_role"):
        name = st.text_input("Role name *")
        description = st.text_area("Description")
        if is_platform_owner:
            scope = st.radio(
                "Available to", ["All companies (shared)", "One specific company"], horizontal=True,
            )
            company_for_role = None
            if scope == "One specific company":
                company_for_role = st.selectbox("Company *", companies, format_func=lambda c: c.name)
        else:
            scope = "One specific company"
            company_for_role = None
        submitted = st.form_submit_button("Save role")
        if submitted:
            if not name.strip():
                st.error("Role name is required.")
            elif scope == "One specific company" and is_platform_owner and not company_for_role:
                st.error("Pick a company for this role.")
            else:
                target_company_id = (
                    None if (is_platform_owner and scope == "All companies (shared)")
                    else (company_for_role.id if is_platform_owner else own_company_id)
                )
                session.add(
                    Role(
                        company_id=target_company_id,
                        name=name.strip(),
                        description=description,
                        is_builtin=False,
                    )
                )
                session.commit()
                st.success(f"Role '{name}' added.")
                st.rerun()

st.divider()
if is_platform_owner:
    roles = session.query(Role).order_by(Role.company_id.isnot(None), Role.name).all()
else:
    roles = (
        session.query(Role)
        .filter((Role.company_id.is_(None)) | (Role.company_id == own_company_id))
        .order_by(Role.company_id.isnot(None), Role.name)
        .all()
    )

if not roles:
    st.info("No roles found.")
else:
    company_by_id = {c.id: c.name for c in (companies or session.query(Company).all())}
    role_rows = [
        {
            "Name": r.name,
            "Scope": "All companies" if r.company_id is None else company_by_id.get(r.company_id, "—"),
            "Built-in": "Yes" if r.is_builtin else "No",
            "Users": session.query(User).filter(User.role_id == r.id).count(),
        }
        for r in roles
    ]
    st.caption("Click a role to edit its page visibility (and name/description, for custom roles).")
    idx = clickable_table(role_rows, key="roles_table")
    if idx is not None:
        st.session_state["role_selected_id"] = roles[idx].id
    else:
        st.session_state.pop("role_selected_id", None)

    selected_id = st.session_state.get("role_selected_id")
    selected = next((r for r in roles if r.id == selected_id), None)

    if selected:
        st.markdown(f"**Edit role: {selected.name}**")
        if selected.is_builtin:
            st.caption("Built-in role - name and description can't be changed, but page visibility can.")
        with st.form(f"edit_role_{selected.id}"):
            e_name = st.text_input(
                "Role name *", value=selected.name, disabled=selected.is_builtin, key=f"edit_role_name_{selected.id}"
            )
            e_description = st.text_area(
                "Description", value=selected.description or "", disabled=selected.is_builtin,
                key=f"edit_role_desc_{selected.id}",
            )
            if st.form_submit_button("Save name/description"):
                if selected.is_builtin:
                    st.info("Built-in role - nothing to save here, see page visibility below.")
                elif not e_name.strip():
                    st.error("Role name is required.")
                else:
                    selected.name = e_name.strip()
                    selected.description = e_description
                    session.commit()
                    st.success("Role updated.")
                    st.rerun()

        st.markdown("**Page visibility** — uncheck a page to hide it for anyone with this role.")
        currently_denied = denied_page_keys(session, selected.id)
        with st.form(f"edit_role_pages_{selected.id}"):
            checked = {}
            page_items = list(PAGE_CATALOG.items())
            cols = st.columns(3)
            for i, (page_key, title) in enumerate(page_items):
                with cols[i % 3]:
                    checked[page_key] = st.checkbox(
                        title, value=page_key not in currently_denied, key=f"perm_{selected.id}_{page_key}"
                    )
            if st.form_submit_button("Save page visibility"):
                session.query(RolePagePermission).filter(RolePagePermission.role_id == selected.id).delete(
                    synchronize_session=False
                )
                for page_key, is_visible in checked.items():
                    if not is_visible:
                        session.add(
                            RolePagePermission(role_id=selected.id, page_key=page_key, can_view=False)
                        )
                session.commit()
                st.success("Page visibility updated.")
                st.rerun()

        users_with_role = session.query(User).filter(User.role_id == selected.id).count()
        if selected.is_builtin:
            st.caption("Built-in roles can't be deleted.")
        elif users_with_role:
            st.caption(
                f"{users_with_role} user(s) currently have this role - reassign them before deleting it."
            )
        else:
            def _do_delete_role(_session=session, _id=selected.id):
                _session.query(RolePagePermission).filter(RolePagePermission.role_id == _id).delete(
                    synchronize_session=False
                )
                _session.query(Role).filter(Role.id == _id).delete(synchronize_session=False)
                _session.commit()
                st.session_state.pop("role_selected_id", None)

            delete_with_confirm(
                f"role '{selected.name}'", _do_delete_role, key_prefix=f"role_{selected.id}",
                extra_warning="No user currently has this role - deleting it is safe.",
            )

        if st.button("Clear selection", key="clear_role_selection"):
            st.session_state.pop("role_selected_id", None)
            st.rerun()
