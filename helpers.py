"""Shared UI helpers for PI3 Plant Edition pages."""

import datetime as dt

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


def combine_date_time(label, key_prefix, default_date=None, default_time=None):
    """Render a date_input + time_input pair side by side and return a
    combined datetime.datetime. Used wherever a phase boundary, event, or
    sample timestamp needs both a date and a time from the operator."""
    c1, c2 = st.columns(2)
    d = c1.date_input(f"{label} — date", value=default_date or dt.date.today(), key=f"{key_prefix}_date")
    t = c2.time_input(f"{label} — time", value=default_time or dt.datetime.now().time(), key=f"{key_prefix}_time")
    return dt.datetime.combine(d, t)


def parse_dt(value):
    """Best-effort parse of a CSV/Excel cell into a datetime, or None."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def parse_bool(value):
    """Best-effort parse of a CSV/Excel cell into a bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")
