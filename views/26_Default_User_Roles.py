"""Screen: Default User Roles

Manages the role TEMPLATES (company_id=NULL, is_builtin=True rows in the
roles table) that every new company is seeded with. These are never
assigned to a User and never shown on the (company-facing) User Roles
page. They exist for one purpose: role_provisioning.clone_builtin_roles_for_company
copies whatever's set here into a new company's own role clones the
moment that company is created.

Originally exactly 3 fixed templates (admin/technical/viewer, since
renamed/consolidated); the platform owner can now add more, rename/
describe any of them, and delete ones that aren't needed - except
"Company Admin" (renamed 2026-08-05 from "Platform Admin", itself renamed
2026-08-04 from the old literal "admin" - the "Platform Admin" name was
retired because it misled people into thinking it granted cross-company
platform-owner power, when it only ever meant "full access within your
own company"; that's now spelled out by the name itself). It's protected
(see access_control.STRUCTURALLY_REQUIRED_ROLE_NAMES): every company
needs a role literally named "Company Admin" for its own User Roles/User
Accounts pages to ever be reachable again.

Each template's access to every page is one of three states - Hidden /
View only / Full access, not just a visibility checkbox - see db.py's
Role docstring and access_control.py's module docstring for the full
Hidden/View-only/Full-access model. Changing a template here only affects
companies created AFTER the change - it is not retroactive, on purpose (a
company's own admin may have already customized their clone; silently
overwriting that would be its own kind of cross-tenant surprise).

Platform-owner-only (see auth.require_platform_owner).
"""

import streamlit as st

import audit_log
from access_control import current_access_states, protected_role_name, save_access_states
from auth import current_user, logout_button, require_login, require_platform_owner
from db import Role, RolePagePermission, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_access_grid, page_setup, render_function_action_intro

page_setup("Default User Roles")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Default User Roles")
render_function_action_intro(
    function_text=(
        "Defines the role templates every new company is seeded with (Company Admin plus whatever "
        "else has been added here) - not the roles themselves, which are cloned per company on "
        "creation so one company narrowing a role can never affect another's. For each page, a "
        "template grants Hidden (page not shown), View only (visible, read-only), or Full access "
        "(visible and fully usable)."
    ),
    action_text=(
        "Add a template if the built-in ones don't cover a role your customers commonly need. "
        "Click a template to edit its name/description or its per-page access. 'Company Admin' is "
        "protected - every company needs a role by that exact name for its own admin pages to "
        "stay reachable - so it can't be renamed or deleted. Changes here only apply to companies "
        "created afterward; existing companies' clones are untouched."
    ),
)
session = get_session()
user = current_user()

with st.expander("Add default role", expanded=False):
    with st.form("add_default_role"):
        new_name = st.text_input("Role name *")
        new_description = st.text_area("Description")
        if st.form_submit_button("Save default role"):
            if not new_name.strip():
                st.error("Role name is required.")
            else:
                new_template = Role(
                    company_id=None, name=new_name.strip(), description=new_description, is_builtin=True,
                )
                session.add(new_template)
                session.flush()
                audit_log.log_role_change(
                    session, target_type="role",
                    change_summary=f"Created default role template '{new_name.strip()}'",
                    changed_by_user_id=user["id"], company_id=None,
                    target_id=new_template.id, target_label=new_name.strip(),
                )
                session.commit()
                st.success(f"Default role '{new_name}' added. New companies will be seeded with it.")
                st.rerun()

st.divider()
templates = (
    session.query(Role)
    .filter(Role.company_id.is_(None), Role.is_builtin.is_(True))
    .order_by(Role.name)
    .all()
)

if not templates:
    st.info("No default role templates yet - add one above.")
else:
    template_rows = [
        {
            "Role": t.name,
            "Description": t.description or "—",
            "Protected": "Yes" if protected_role_name(t.name) else "",
        }
        for t in templates
    ]
    st.caption("Click a role to edit its name/description and per-page access.")
    idx = clickable_table(template_rows, key="default_roles_table")
    if idx is not None and idx < len(templates):
        st.session_state["default_role_selected_id"] = templates[idx].id
    else:
        st.session_state.pop("default_role_selected_id", None)

    selected_id = st.session_state.get("default_role_selected_id")
    selected = next((t for t in templates if t.id == selected_id), None)

    if selected:
        is_protected = protected_role_name(selected.name)
        st.markdown(f"**Edit default role: {selected.name}**")
        if is_protected:
            st.caption(
                "This role is protected - every company needs a role named 'Company Admin' for its "
                "own admin pages to stay reachable, so its name can't be changed and it can't be deleted."
            )
        with st.form(f"edit_default_role_name_{selected.id}"):
            e_name = st.text_input(
                "Role name *", value=selected.name, disabled=is_protected, key=f"edit_default_name_{selected.id}"
            )
            e_description = st.text_area(
                "Description", value=selected.description or "", key=f"edit_default_desc_{selected.id}"
            )
            if st.form_submit_button("Save name/description"):
                if is_protected:
                    st.info("Protected role - description can still be saved, but the name can't change.")
                    if e_description != (selected.description or ""):
                        audit_log.log_role_change(
                            session, target_type="role",
                            change_summary=f"Updated default role template '{selected.name}': description updated",
                            changed_by_user_id=user["id"], company_id=None,
                            target_id=selected.id, target_label=selected.name,
                        )
                    selected.description = e_description
                    session.commit()
                    st.success("Description updated.")
                    st.rerun()
                elif not e_name.strip():
                    st.error("Role name is required.")
                else:
                    changes = []
                    if e_name.strip() != selected.name:
                        changes.append(f"name: '{selected.name}' → '{e_name.strip()}'")
                    if e_description != (selected.description or ""):
                        changes.append("description updated")
                    selected.name = e_name.strip()
                    selected.description = e_description
                    if changes:
                        audit_log.log_role_change(
                            session, target_type="role",
                            change_summary=f"Updated default role template '{selected.name}': " + "; ".join(changes),
                            changed_by_user_id=user["id"], company_id=None,
                            target_id=selected.id, target_label=selected.name,
                        )
                    session.commit()
                    st.success("Default role updated.")
                    st.rerun()

        st.markdown("**Page access** — Hidden, View only, or Full access, per page.")
        current_states = current_access_states(session, selected.id)
        with st.form(f"edit_default_role_pages_{selected.id}"):
            selections = page_access_grid(current_states, key_prefix=f"default_perm_{selected.id}")
            if st.form_submit_button("Save default page access"):
                changed_pages = [
                    f"{page_key}: {current_states.get(page_key, 'Full access')} → {new_state}"
                    for page_key, new_state in selections.items()
                    if current_states.get(page_key, "Full access") != new_state
                ]
                save_access_states(session, selected.id, selections)
                if changed_pages:
                    audit_log.log_role_change(
                        session, target_type="permission",
                        change_summary=f"Updated default page access for role template '{selected.name}': " + "; ".join(changed_pages),
                        changed_by_user_id=user["id"], company_id=None,
                        target_id=selected.id, target_label=selected.name,
                    )
                session.commit()
                st.success(f"Default page access for '{selected.name}' updated for future companies.")
                st.rerun()

        if is_protected:
            st.caption("Protected role - can't be deleted.")
        else:
            def _do_delete_template(_session=session, _id=selected.id, _name=selected.name):
                audit_log.log_role_change(
                    _session, target_type="role",
                    change_summary=f"Deleted default role template '{_name}'",
                    changed_by_user_id=user["id"], company_id=None,
                    target_id=_id, target_label=_name,
                )
                _session.query(RolePagePermission).filter(RolePagePermission.role_id == _id).delete(
                    synchronize_session=False
                )
                _session.query(Role).filter(Role.id == _id).delete(synchronize_session=False)
                _session.commit()
                st.session_state.pop("default_role_selected_id", None)

            delete_with_confirm(
                f"default role '{selected.name}'", _do_delete_template, key_prefix=f"default_role_{selected.id}",
                extra_warning=(
                    "This only stops new companies from being seeded with this role - it does not "
                    "touch any existing company's clones or the users assigned to them."
                ),
            )

        if st.button("Clear selection", key="clear_default_role_selection"):
            st.session_state.pop("default_role_selected_id", None)
            st.rerun()
