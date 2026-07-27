"""
PI3 Plant Edition
Simple role-gated login.

This is an internal tool for a small technical team, so authentication is
deliberately simple: named users with a role, defined in st.secrets. This
is NOT a full identity/SSO system - that remains out of scope for now.

Roles: admin, technical, viewer
- admin:     full access, including Maintenance/License Admin and PI3 connectivity toggle
- technical: can create/edit records, cannot approve their own trial closures
             or change commercial/admin settings
- viewer:    read-only access to all screens

Expected st.secrets structure (see .streamlit/secrets.toml.example):

[users.jane]
password = "changeme"
display_name = "Jane Doe"
role = "admin"

[users.tom]
password = "changeme2"
display_name = "Tom Smith"
role = "technical"
"""

import streamlit as st

ROLES = ["admin", "technical", "viewer"]


def _users_from_secrets():
    try:
        return dict(st.secrets.get("users", {}))
    except Exception:
        return {}


def _auth_disabled():
    try:
        return bool(st.secrets.get("AUTH_DISABLED", False))
    except Exception:
        return False


def require_login():
    """Render a login form if the user is not authenticated. Stops execution
    of the calling page until login succeeds.

    Development-only bypass: if AUTH_DISABLED = true is set in secrets, this
    skips the login form entirely and logs in as a synthetic admin user, so
    the whole app is reachable without credentials. Meant for a UAT/dev
    deployment only - remove or set to false before anyone relies on this
    deployment being access-controlled, since with it on, anyone with the
    app's URL sees everything with full admin rights, no login needed."""

    if _auth_disabled():
        st.session_state["authenticated"] = True
        st.session_state.setdefault("username", "dev")
        st.session_state.setdefault("display_name", "Dev (auth disabled)")
        st.session_state.setdefault("role", "admin")
        return

    if st.session_state.get("authenticated"):
        return

    users = _users_from_secrets()

    st.title("PI3 Plant Edition")
    st.caption("Flexible slabstock foam expert system")

    if not users:
        st.warning(
            "No users configured yet. Add a [users.<name>] block to "
            "`.streamlit/secrets.toml` (see secrets.toml.example) before "
            "using this app."
        )
        st.stop()

    with st.form("login_form"):
        username = st.text_input("Username").strip().lower()
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        user_record = users.get(username)
        if user_record and password == user_record.get("password"):
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            st.session_state["display_name"] = user_record.get("display_name", username)
            st.session_state["role"] = user_record.get("role", "viewer")
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.stop()


def current_user():
    return {
        "username": st.session_state.get("username"),
        "display_name": st.session_state.get("display_name"),
        "role": st.session_state.get("role", "viewer"),
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


def logout_button():
    with st.sidebar:
        user = current_user()
        st.markdown(f"**{user['display_name']}**  \nRole: `{user['role']}`")
        if st.button("Log out"):
            for key in ("authenticated", "username", "display_name", "role"):
                st.session_state.pop(key, None)
            st.rerun()
