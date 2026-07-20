"""Shared UI helpers for PI3 Plant Edition pages."""

import pandas as pd
import streamlit as st

from db import get_session

ADVISORY_DISCLAIMER = (
    "PI3 Plant Edition supports technical review. It surfaces historical "
    "records and conclusions for the team to evaluate - it does not issue "
    "formulation instructions and does not replace qualified technical "
    "judgment. Review applicability against current raw materials, process "
    "conditions, and target properties before acting."
)


def page_setup(title: str):
    """Kept for compatibility with existing pages, which all call this as
    their first Streamlit command. Page config, sidebar logo, and global
    styling are now set once in app.py (which runs first on every page view
    under st.navigation), so this is intentionally a no-op — calling
    st.set_page_config() a second time would raise an error."""
    pass


def confidence_badge(level: str) -> str:
    colors = {
        "Confirmed": "🟢",
        "Likely": "🟡",
        "Unconfirmed": "⚪",
        "Rejected": "🔴",
    }
    return f"{colors.get(level, '⚪')} {level or 'Unconfirmed'}"


def to_df(rows, columns=None):
    if not rows:
        return pd.DataFrame(columns=columns or [])
    return pd.DataFrame([r.__dict__ for r in rows]).drop(columns=["_sa_instance_state"], errors="ignore")


def selectbox_from_query(label, session, model, name_field="name", allow_none=True, key=None):
    """Render a selectbox populated from a DB query, return the selected object (or None)."""
    records = session.query(model).all()
    options = [None] if allow_none else []
    options += records
    return st.selectbox(
        label,
        options,
        format_func=lambda r: "—" if r is None else getattr(r, name_field, str(r)),
        key=key,
    )


def show_advisory_footer():
    st.divider()
    st.caption(f"Advisory boundary: {ADVISORY_DISCLAIMER}")
