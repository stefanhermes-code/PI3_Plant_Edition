"""Shared UI helpers for PI3 Plant Edition pages."""

import datetime as dt

import pandas as pd
import streamlit as st

from db import get_session


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


def selection_rows(event):
    """Best-effort extraction of selected row indices from a
    st.dataframe(..., on_select="rerun") return value, tolerant of the
    exact attribute/dict shape Streamlit uses."""
    if event is None:
        return []
    sel = getattr(event, "selection", None)
    if sel is None:
        try:
            sel = event["selection"]
        except Exception:
            return []
    rows = getattr(sel, "rows", None)
    if rows is None:
        try:
            rows = sel["rows"]
        except Exception:
            return []
    return list(rows or [])


def clickable_table(rows, key):
    """Render rows (list of dicts) as a single-row-selectable table. Returns
    the selected row's index, or None if nothing is selected. Used across
    every "list + edit + delete" page so row-selection works identically
    everywhere."""
    if not rows:
        return None
    event = st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    sel = selection_rows(event)
    return sel[0] if sel else None


def delete_with_confirm(label, on_confirm, key_prefix, extra_warning=""):
    """Render a checkbox + delete button gate, calling on_confirm() and
    rerunning only once the operator has explicitly ticked the confirm box.
    Shared by every page with a delete action so the confirmation UX (and
    the requirement to tick a box before the button becomes clickable) is
    consistent app-wide."""
    st.markdown(f"**Delete {label}**")
    if extra_warning:
        st.warning(extra_warning)
    confirm = st.checkbox(f"I understand — permanently delete {label}.", key=f"{key_prefix}_confirm")
    if st.button(f"Delete {label}", key=f"{key_prefix}_btn", type="primary", disabled=not confirm):
        on_confirm()
        st.success(f"{label} deleted.")
        st.rerun()


def csv_excel_uploader(required_cols, optional_cols=None, key=None):
    """Render a file uploader for bulk CSV/Excel import, parse it, and check
    that the required columns are present. Used by every "CSV / Excel
    import" tab across the app so the upload/parse/column-check boilerplate
    (and its error messages) stay identical everywhere.

    Returns (df, filename) once a valid file with all required columns has
    been uploaded, or (None, None) otherwise (an st.error/st.caption has
    already been shown as appropriate - callers don't need to repeat that).
    """
    optional_cols = optional_cols or []
    cols_caption = "Required columns: " + ", ".join(required_cols)
    if optional_cols:
        cols_caption += ". Optional columns: " + ", ".join(optional_cols)
    st.caption(cols_caption)

    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key=key)
    if not uploaded:
        return None, None

    try:
        df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        return None, None

    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
        return None, None

    return df, uploaded.name
