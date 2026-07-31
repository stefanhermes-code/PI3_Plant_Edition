"""Screen 11: PI3 Connectivity

Standard version (always included): Search, Compare, Retrieve, Structure,
Report, Review and Approval.

Optional PI3 connectivity (this screen): Assisted interpretation,
question answering, advisory comparison, company-specific knowledge
interface. Separate annual fee. Disabled unless explicitly enabled in
admin settings. Even when enabled, final decisions require human review
and approval — no autonomous formulation commands, ever.

This screen owns the per-plant enable/disable toggle and commercial fee.
The actual PI3 reasoning layer (OpenAI's Responses API, with file_search
over a vector store of historical trial narratives and expert notes)
lives in ai_assistant.py.
"""

import datetime as dt

import pandas as pd
import streamlit as st

import ai_assistant
import pi3_query_tool
from db import Plant, PI3AIConnectionSetting, get_session, init_db
from auth import current_user, logout_button, require_login, require_role
from helpers import page_setup, render_data_table, render_function_action_intro
from tenant_scope import apply_scope, company_picker

page_setup("PI3 Connectivity")
init_db()
require_login()
logout_button()

st.title("PI3 Connectivity")
render_function_action_intro(
    function_text=(
        "Turns PI3's connected AI features - assisted interpretation, question answering, "
        "advisory comparison, and search over your company's own historical trial and expert-note "
        "knowledge - on or off per plant, and reports whether this deployment's credentials are "
        "actually configured. Everything else in the app (search, compare, retrieve, structure, "
        "report, review and approval) works without this; PI3 connectivity is a separately "
        "billed, opt-in add-on, off by default."
    ),
    action_text=(
        "If something elsewhere in the app claims PI3 isn't configured, check 'Deployment "
        "diagnostics' first to confirm the API key and vector-store credentials are actually "
        "visible to this deployment. Then, per plant, turn PI3 connectivity on or off using the "
        "toggle further down - only admins can change this setting, and even with it on, every "
        "PI3 output still requires human review before acting on it."
    ),
)
st.info(
    "Standard PI3 Plant Edition (search, compare, retrieve, structure, report, review "
    "and approval) is fully available without this add-on. PI3 connectivity is "
    "optional, separately billed, and disabled by default per plant."
)

if current_user()["role"] == "admin":
    st.subheader("Deployment diagnostics")
    st.caption(
        "Checks whether this deployment's secrets are actually visible to the app right "
        "now, so a missing-credentials message elsewhere in the app doesn't send you "
        "guessing. This is independent of the per-plant toggle below."
    )
    secret_checks = [
        ("OPENAI_API_KEY", ai_assistant._get_secret("OPENAI_API_KEY"), "Required for every PI3 feature."),
        (
            "PI3_VECTOR_STORE_ID",
            ai_assistant._get_secret("PI3_VECTOR_STORE_ID"),
            "Required for every PI3 feature (the file_search knowledge base).",
        ),
        (
            "PI3_MODEL",
            ai_assistant._get_secret("PI3_MODEL"),
            "Optional - falls back to a built-in default model if unset.",
        ),
        (
            "PI3_READONLY_DATABASE_URL",
            pi3_query_tool._get_secret("PI3_READONLY_DATABASE_URL"),
            "Optional - enables the free-form SQL query tool. PI3 still answers "
            "questions without it, just without that one tool.",
        ),
    ]
    render_data_table(
        pd.DataFrame(
            [
                {"Secret": name, "Present": "Yes" if value else "No", "Notes": note}
                for name, value, note in secret_checks
            ]
        )
    )
    if not (secret_checks[0][1] and secret_checks[1][1]):
        st.error(
            "OPENAI_API_KEY and/or PI3_VECTOR_STORE_ID show as missing, which is why "
            "pages show \"PI3 isn't configured for this deployment yet\". The single "
            "most common cause: in Streamlit Cloud's Secrets editor, TOML attaches any "
            "line to the LAST [section] header above it, not to the top level - so if "
            "either line was pasted below a [users.yourname] block (or any other "
            "[section]), it silently stops being visible here even though the text is "
            "in the file. Move both lines above every [section] header and save."
        )
    else:
        st.success("OPENAI_API_KEY and PI3_VECTOR_STORE_ID are both visible to the app.")
    st.divider()

session = get_session()
user = current_user()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="pi3_connectivity_company_filter"
)
active_company_id = company.id if company else None
plants = apply_scope(session.query(Plant), Plant.company_id, [active_company_id] if active_company_id else None).all()
if not plants:
    st.info("Add a plant first.")
    st.stop()

plant = st.selectbox("Plant", plants, format_func=lambda p: p.name)
setting = (
    session.query(PI3AIConnectionSetting).filter(PI3AIConnectionSetting.plant_id == plant.id).first()
)

if setting:
    st.metric("Status", setting.pi3_ai_status)
    st.write(f"Annual fee: {'EUR ' + str(setting.pi3_ai_annual_fee) if setting.pi3_ai_annual_fee else '—'}")
    if setting.pi3_ai_connectivity_enabled:
        st.success(f"Enabled by {setting.enabled_by} on {setting.enabled_at}")
    else:
        st.info("Currently disabled for this plant.")
else:
    st.info("Not yet configured for this plant. Default status: Disabled.")

st.divider()
require_role("admin")
st.subheader("Admin: configure PI3 connectivity")

with st.form("pi3_ai_settings"):
    enabled = st.toggle("Enable PI3 connectivity for this plant", value=setting.pi3_ai_connectivity_enabled if setting else False)
    annual_fee = st.number_input(
        "PI3 annual fee (EUR)", min_value=0.0, step=500.0,
        value=float(setting.pi3_ai_annual_fee) if setting and setting.pi3_ai_annual_fee else 0.0,
    )
    submitted = st.form_submit_button("Save")
    if submitted:
        user = current_user()
        if setting is None:
            setting = PI3AIConnectionSetting(plant_id=plant.id)
            session.add(setting)
        setting.pi3_ai_connectivity_enabled = enabled
        setting.pi3_ai_status = "Enabled" if enabled else "Disabled"
        setting.pi3_ai_annual_fee = annual_fee or None
        if enabled:
            setting.enabled_by = user["display_name"]
            setting.enabled_at = dt.datetime.utcnow()
        session.commit()
        st.success("PI3 connectivity settings saved.")
        st.rerun()

st.caption(
    "Even with PI3 connectivity enabled, all final decisions require human review "
    "and approval on the Approval & Review screen. No autonomous formulation commands."
)

