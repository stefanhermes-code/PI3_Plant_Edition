"""
PI3 Plant Edition
Multi-tenant, database-backed login.

Users live in the `users` table (see db.py), not in st.secrets - this is
what makes real customer accounts possible: hashed passwords, a per-account
validity window (valid_from/valid_until), and company/role scoping. A
"company" is the tenant boundary (see db.py Company); a user's role can be
one of three built-in roles shared by every company (admin, technical,
viewer - same semantics as before) or a custom role a company's own admin
defines on the User Roles page.

Legacy fallback: if the `users` table has zero rows at all (a deployment
that hasn't been through the new User Accounts admin page yet), login
falls back to the old st.secrets [users.<name>] blocks so an existing
deployment isn't locked out mid-migration. The moment at least one real DB
user exists, secrets.toml-based login is ignored entirely - there is no
way to have both active at once, to avoid two disagreeing sources of truth
for who's allowed in.

Roles: admin, technical, viewer
- admin:     full access, including Maintenance/License Admin and PI3 connectivity toggle.
             A company's own admin manages that company's users/custom roles; only a
             platform-owner admin (HTC) can manage Companies or Subscription Types.
- technical: can create/edit records, cannot approve their own trial closures
             or change commercial/admin settings
- viewer:    read-only access to all screens

is_super_admin (User.is_super_admin): an unconditional bypass of every
RolePagePermission check, independent of role name or is_platform_owner -
see db.py's User docstring for why this exists separately from
is_platform_owner. Only ever set on a platform-owner-company user (the
User Accounts page only offers the toggle in that context).

Expected st.secrets structure for the legacy fallback only (see
.streamlit/secrets.toml.example):

[users.jane]
password = "changeme"
display_name = "Jane Doe"
role = "admin"
"""

import datetime as dt

import bcrypt
import streamlit as st

import audit_log
from db import User, get_session


def _auth_disabled():
    try:
        return bool(st.secrets.get("AUTH_DISABLED", False))
    except Exception:
        return False


def _legacy_users_from_secrets():
    try:
        return dict(st.secrets.get("users", {}))
    except Exception:
        return {}


def hash_password(plain_password):
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password, password_hash):
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _db_has_any_users(session):
    return session.query(User).first() is not None


def _check_db_login(session, username, password):
    """Returns (User, None) on success, or (None, error message) on failure."""
    user = session.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return None, "Invalid username or password."
    if not user.active:
        return None, "This account has been deactivated. Contact your administrator."
    today = dt.date.today()
    if user.valid_from and today < user.valid_from:
        return None, f"This account isn't active yet (valid from {user.valid_from})."
    if user.valid_until and today > user.valid_until:
        return None, f"This account's access window ended on {user.valid_until}. Contact your administrator."
    return user, None


def _start_db_session(session, user):
    st.session_state["authenticated"] = True
    st.session_state["auth_source"] = "db"
    st.session_state["user_id"] = user.id
    st.session_state["username"] = user.username
    st.session_state["display_name"] = user.display_name or user.username
    st.session_state["role_id"] = user.role_id
    st.session_state["role"] = user.role.name if user.role else "viewer"
    st.session_state["company_id"] = user.company_id
    st.session_state["is_platform_owner"] = bool(user.company and user.company.is_platform_owner)
    st.session_state["is_super_admin"] = bool(user.is_super_admin)
    user.last_login_at = dt.datetime.utcnow()
    audit_log.log_login_event(
        session, "login_success", username_attempted=user.username,
        user_id=user.id, company_id=user.company_id,
    )
    session.commit()


def _start_legacy_session(username, user_record):
    st.session_state["authenticated"] = True
    st.session_state["auth_source"] = "legacy"
    st.session_state["username"] = username
    st.session_state["display_name"] = user_record.get("display_name", username)
    st.session_state["role"] = user_record.get("role", "viewer")
    st.session_state["role_id"] = None
    st.session_state["company_id"] = None
    # Legacy secrets.toml users predate multi-tenancy entirely - treat them
    # as platform-owner scope so nothing they previously had access to
    # disappears out from under them.
    st.session_state["is_platform_owner"] = True
    st.session_state["is_super_admin"] = True


def _log_page_view():
    """Item 48. Called from require_login()'s already-authenticated paths -
    both the AUTH_DISABLED dev bypass and the normal fast path - so it
    fires on every page a reviewer actually lands on, without touching
    any of the ~27 individual page files. page_setup() (see helpers.py)
    stashes the page's display title into session_state as the literal
    first line of every page, before require_login() runs; the dedup
    against re-running the SAME page (a widget click causes a rerun, not
    a navigation) lives in audit_log.log_page_view_if_new."""
    page_name = st.session_state.get("_current_page_title")
    if not page_name:
        return
    try:
        session = get_session()
        audit_log.log_page_view_if_new(
            session, st.session_state,
            user_id=st.session_state.get("user_id"),
            company_id=st.session_state.get("company_id"),
            page_name=page_name,
        )
    except Exception:
        pass


def require_login():
    """Render a login form if the user is not authenticated. Stops execution
    of the calling page until login succeeds.

    Development-only bypass: if AUTH_DISABLED = true is set in secrets, this
    skips the login form entirely and logs in as a synthetic platform-owner
    admin user, so the whole app is reachable without credentials. Meant for
    a UAT/dev deployment only - remove or set to false before anyone relies
    on this deployment being access-controlled, since with it on, anyone
    with the app's URL sees everything with full admin rights, no login
    needed."""

    if _auth_disabled():
        st.session_state["authenticated"] = True
        st.session_state.setdefault("auth_source", "dev")
        st.session_state.setdefault("username", "dev")
        st.session_state.setdefault("display_name", "Dev (auth disabled)")
        st.session_state.setdefault("role", "admin")
        st.session_state.setdefault("role_id", None)
        st.session_state.setdefault("company_id", None)
        st.session_state.setdefault("is_platform_owner", True)
        st.session_state.setdefault("is_super_admin", True)
        _log_page_view()
        return

    if st.session_state.get("authenticated"):
        _log_page_view()
        return

    session = get_session()
    db_has_users = _db_has_any_users(session)
    legacy_users = {} if db_has_users else _legacy_users_from_secrets()

    st.title("PI3 Plant Edition")
    st.caption("Flexible slabstock foam expert system")

    if not db_has_users and not legacy_users:
        st.warning(
            "No user accounts configured yet. Ask your administrator to create one on the "
            "User Accounts page, or (fresh install only) add a [users.<name>] block to "
            "`.streamlit/secrets.toml`."
        )
        st.stop()

    with st.form("login_form"):
        username = st.text_input("Username").strip().lower()
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        if db_has_users:
            user, error = _check_db_login(session, username, password)
            if error:
                audit_log.log_login_event(
                    session, "login_failure", username_attempted=username, detail=error,
                )
                st.error(error)
            else:
                _start_db_session(session, user)
                st.rerun()
        else:
            user_record = legacy_users.get(username)
            if user_record and password == user_record.get("password"):
                audit_log.log_login_event(
                    session, "login_success", username_attempted=username,
                    detail="legacy secrets.toml login",
                )
                _start_legacy_session(username, user_record)
                st.rerun()
            else:
                audit_log.log_login_event(
                    session, "login_failure", username_attempted=username,
                    detail="invalid legacy credentials",
                )
                st.error("Invalid username or password.")

    st.stop()


def current_user():
    return {
        "id": st.session_state.get("user_id"),
        "username": st.session_state.get("username"),
        "display_name": st.session_state.get("display_name"),
        "role": st.session_state.get("role", "viewer"),
        "role_id": st.session_state.get("role_id"),
        "company_id": st.session_state.get("company_id"),
        "is_platform_owner": st.session_state.get("is_platform_owner", False),
        "is_super_admin": st.session_state.get("is_super_admin", False),
    }


def require_role(*allowed_roles):
    """Call at the top of a page to restrict it to certain roles."""
    role = st.session_state.get("role", "viewer")
    if role not in allowed_roles:
        st.error(
            f"Your role ('{role}') does not have access to this screen. "
            f"Required role: {', '.join(allowed_roles)}."
        )
        st.stop()


def require_platform_owner():
    """Call at the top of a page reserved for the platform owner (HTC) only
    - Companies and Subscription Types - regardless of the visiting user's
    role name, since even another company's own 'admin' must not manage
    other companies or the global subscription catalog."""
    if not st.session_state.get("is_platform_owner", False):
        st.error("This screen is only available to the platform administrator.")
        st.stop()


def logout_button():
    with st.sidebar:
        user = current_user()
        st.markdown(f"**{user['display_name']}**  \nRole: `{user['role']}`")
        if st.button("Log out"):
            try:
                session = get_session()
                audit_log.log_login_event(
                    session, "logout", username_attempted=user["username"],
                    user_id=user["id"], company_id=user["company_id"],
                )
                session.commit()
            except Exception:
                pass
            for key in (
                "authenticated", "auth_source", "user_id", "username", "display_name",
                "role", "role_id", "company_id", "is_platform_owner", "is_super_admin",
                "_audit_last_page_logged",
            ):
                st.session_state.pop(key, None)
            st.rerun()
