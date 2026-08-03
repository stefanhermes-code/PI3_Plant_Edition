"""Screen: User Accounts

Real, database-backed user accounts (hashed passwords) replacing the old
st.secrets [users.<name>] blocks - see auth.py. Each account belongs to one
company and one role, can be deactivated without deleting its history, and
can carry a validity window (valid_from/valid_until) for time-limited
access such as a contractor or a trial user.

The platform owner (HTC) sees and manages every company's users; a
company's own admin only sees and manages their own company's users, and
is blocked from exceeding that company's subscribed user limit.

Only when the company being viewed IS the platform owner (HTC itself) does
an extra "Super admin" checkbox appear, to set User.is_super_admin - an
unconditional bypass of every page/role restriction in the app (see
db.py's User docstring and access_control.can_use_page). Deliberately not
offered for any other company: it's an escape hatch reserved for HTC's own
trusted staff, not something a customer's admin should ever be able to
grant to one of their own users.
"""

import datetime as dt

import streamlit as st

import audit_log
from auth import current_user, hash_password, logout_button, require_login, require_role
from db import Company, Role, User, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_setup, render_function_action_intro

page_setup("User Accounts")
init_db()
require_login()
require_role("admin")
logout_button()

st.title("User Accounts")
render_function_action_intro(
    function_text=(
        "Real user accounts with hashed passwords, replacing the old shared config-file logins. "
        "Each account belongs to one company and one role, and can carry a validity window "
        "(start/end date) for time-limited access - a contractor or trial user, for example - on "
        "top of being able to deactivate it outright without losing its history."
    ),
    action_text=(
        "Add a user for your company, pick their role, and optionally set a validity window. "
        "Adding a new user is blocked once your company's subscription's user limit is reached - "
        "upgrade the subscription (Companies page) or deactivate an unused account first."
    ),
)
session = get_session()
user = current_user()
is_platform_owner = user["is_platform_owner"]
own_company_id = user["company_id"]


def _roles_for_company(company_id):
    return (
        session.query(Role)
        .filter((Role.company_id.is_(None)) | (Role.company_id == company_id))
        .order_by(Role.company_id.isnot(None), Role.name)
        .all()
    )


def _active_user_count(company_id):
    return session.query(User).filter(User.company_id == company_id, User.active.is_(True)).count()


all_companies = session.query(Company).order_by(Company.name).all()
if is_platform_owner:
    if not all_companies:
        st.warning("No companies exist yet - add one on the Companies page first.")
        st.stop()
    company_filter = st.selectbox(
        "Company", all_companies, format_func=lambda c: c.name, key="user_company_filter"
    )
else:
    company_filter = next((c for c in all_companies if c.id == own_company_id), None)
    if not company_filter:
        st.warning("Your account isn't linked to a company yet - contact the platform administrator.")
        st.stop()

roles_for_add = _roles_for_company(company_filter.id)
subscription = company_filter.subscription_type
max_users = subscription.max_users if subscription else None
current_count = _active_user_count(company_filter.id)
limit_reached = max_users is not None and current_count >= max_users

with st.expander("Add user", expanded=False):
    if limit_reached:
        st.warning(
            f"{company_filter.name} is at its subscription's user limit ({max_users} active users). "
            "Upgrade the subscription or deactivate an unused account before adding another."
        )
    with st.form("add_user"):
        username = st.text_input("Username * (used to log in)")
        display_name = st.text_input("Display name")
        role = st.selectbox("Role *", roles_for_add, format_func=lambda r: r.name)
        password = st.text_input("Password *", type="password")
        c1, c2 = st.columns(2)
        valid_from = c1.date_input("Valid from (optional)", value=None)
        valid_until = c2.date_input("Valid until (optional)", value=None)
        active = st.checkbox("Active", value=True)
        super_admin = False
        if company_filter.is_platform_owner:
            super_admin = st.checkbox(
                "Super admin (bypasses every page/role restriction app-wide - HTC staff only)",
                value=False,
            )
        submitted = st.form_submit_button("Save user", disabled=limit_reached)
        if submitted:
            username_clean = username.strip().lower()
            if not username_clean or not password:
                st.error("Username and password are required.")
            elif session.query(User).filter(User.username == username_clean).first():
                st.error(f"Username '{username_clean}' is already taken.")
            else:
                new_user = User(
                    company_id=company_filter.id,
                    username=username_clean,
                    password_hash=hash_password(password),
                    display_name=display_name or username_clean,
                    role_id=role.id,
                    active=active,
                    valid_from=valid_from or None,
                    valid_until=valid_until or None,
                    is_super_admin=super_admin,
                )
                session.add(new_user)
                session.flush()
                audit_log.log_role_change(
                    session, target_type="user",
                    change_summary=f"Created user '{username_clean}' with role '{role.name}'" + (" (super admin)" if super_admin else ""),
                    changed_by_user_id=user["id"], company_id=company_filter.id,
                    target_id=new_user.id, target_label=username_clean,
                )
                session.commit()
                st.success(f"User '{username_clean}' added.")
                st.rerun()

st.divider()
users = (
    session.query(User)
    .filter(User.company_id == company_filter.id)
    .order_by(User.username)
    .all()
)
if not users:
    st.info(f"No user accounts yet for {company_filter.name}.")
else:
    user_rows = [
        {
            "Username": u.username,
            "Display name": u.display_name or "",
            "Role": u.role.name if u.role else "—",
            "Active": "Yes" if u.active else "No",
            **({"Super admin": "Yes" if u.is_super_admin else ""} if company_filter.is_platform_owner else {}),
            "Valid from": u.valid_from or "—",
            "Valid until": u.valid_until or "—",
            "Last login": u.last_login_at,
        }
        for u in users
    ]
    st.caption(f"{len(users)} user(s) at {company_filter.name}. Click a row to edit.")
    idx = clickable_table(user_rows, key="users_table")
    if idx is not None:
        st.session_state["user_selected_id"] = users[idx].id
    else:
        st.session_state.pop("user_selected_id", None)

    selected_id = st.session_state.get("user_selected_id")
    selected = next((u for u in users if u.id == selected_id), None)

    if selected:
        st.markdown(f"**Edit user: {selected.username}**")
        roles_for_edit = _roles_for_company(selected.company_id)
        with st.form(f"edit_user_{selected.id}"):
            e_display = st.text_input(
                "Display name", value=selected.display_name or "", key=f"edit_user_disp_{selected.id}"
            )
            e_role = st.selectbox(
                "Role *", roles_for_edit,
                index=next((i for i, r in enumerate(roles_for_edit) if r.id == selected.role_id), 0),
                format_func=lambda r: r.name, key=f"edit_user_role_{selected.id}",
            )
            e_new_password = st.text_input(
                "Reset password (leave blank to keep current)", type="password", key=f"edit_user_pw_{selected.id}"
            )
            c1, c2 = st.columns(2)
            e_valid_from = c1.date_input(
                "Valid from (optional)", value=selected.valid_from, key=f"edit_user_vf_{selected.id}"
            )
            e_valid_until = c2.date_input(
                "Valid until (optional)", value=selected.valid_until, key=f"edit_user_vu_{selected.id}"
            )
            e_active = st.checkbox("Active", value=selected.active, key=f"edit_user_active_{selected.id}")
            e_super_admin = selected.is_super_admin
            if company_filter.is_platform_owner:
                e_super_admin = st.checkbox(
                    "Super admin (bypasses every page/role restriction app-wide - HTC staff only)",
                    value=selected.is_super_admin, key=f"edit_user_super_{selected.id}",
                )
            if st.form_submit_button("Save changes"):
                changes = []
                old_role = next((r for r in roles_for_edit if r.id == selected.role_id), None)
                if e_role.id != selected.role_id:
                    changes.append(f"role: '{old_role.name if old_role else '—'}' → '{e_role.name}'")
                if e_active != selected.active:
                    changes.append(f"active: {selected.active} → {e_active}")
                if e_super_admin != selected.is_super_admin:
                    changes.append(f"super admin: {selected.is_super_admin} → {e_super_admin}")
                if (e_valid_from or None) != selected.valid_from:
                    changes.append(f"valid from: {selected.valid_from or '—'} → {e_valid_from or '—'}")
                if (e_valid_until or None) != selected.valid_until:
                    changes.append(f"valid until: {selected.valid_until or '—'} → {e_valid_until or '—'}")
                if e_new_password:
                    changes.append("password reset")

                selected.display_name = e_display
                selected.role_id = e_role.id
                selected.valid_from = e_valid_from or None
                selected.valid_until = e_valid_until or None
                selected.active = e_active
                selected.is_super_admin = e_super_admin
                if e_new_password:
                    selected.password_hash = hash_password(e_new_password)
                if changes:
                    audit_log.log_role_change(
                        session, target_type="user",
                        change_summary=f"Updated user '{selected.username}': " + "; ".join(changes),
                        changed_by_user_id=user["id"], company_id=selected.company_id,
                        target_id=selected.id, target_label=selected.username,
                    )
                session.commit()
                st.success("User updated.")
                st.rerun()

        def _do_delete_user(_session=session, _id=selected.id, _username=selected.username, _company_id=selected.company_id):
            audit_log.log_role_change(
                _session, target_type="user",
                change_summary=f"Deleted user '{_username}'",
                changed_by_user_id=user["id"], company_id=_company_id,
                target_id=_id, target_label=_username,
            )
            _session.query(User).filter(User.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("user_selected_id", None)

        # Historical records (production runs, approvals, ...) reference
        # a user by free-text name, not a real users.id foreign key, so
        # there's no cascade/orphan risk to check the way there is for
        # Companies/Subscription Types/User Roles (see
        # PI3_Gaps_and_Ambiguities.docx, finding 2.3). The two real footguns
        # unique to THIS page are blocked explicitly instead: deleting the
        # account you're currently logged in as, and deleting the last
        # remaining active super admin (which would leave no one able to
        # bypass role restrictions platform-wide to fix the mistake).
        is_self = selected.id == st.session_state.get("user_id")
        is_last_super_admin = selected.is_super_admin and (
            session.query(User)
            .filter(User.is_super_admin.is_(True), User.active.is_(True), User.id != selected.id)
            .count()
            == 0
        )
        if is_self:
            st.warning(
                "You can't delete the account you're currently logged in as. Deactivate it instead, "
                "or have another admin delete it."
            )
        elif is_last_super_admin:
            st.warning(
                "This is the only active super admin account - deleting it would leave no one with "
                "unrestricted platform-owner access. Promote another user to super admin first, or "
                "deactivate this one instead."
            )
        else:
            delete_with_confirm(
                f"user '{selected.username}'", _do_delete_user, key_prefix=f"user_{selected.id}",
                extra_warning="This is a leaf record — deleting it has no other effects. Consider deactivating instead if you want to keep a record they once had access.",
            )

        if st.button("Clear selection", key="clear_user_selection"):
            st.session_state.pop("user_selected_id", None)
            st.rerun()
