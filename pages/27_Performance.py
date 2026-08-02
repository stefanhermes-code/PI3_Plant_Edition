"""Screen 27: Performance

Added 2026-08-02 after a reported "app feels slow in general". Shows real,
measured evidence of how expensive the Industrial Intelligence pages'
shared data-loading functions (analytics.run_settings_dataframe,
property_results_dataframe, actual_usage_dataframe) actually are, instead
of leaving that as a guess - each one logs a PerformanceLog row (see
db.py) every time it actually has to hit the database.

Every row here is a cache MISS: analytics.py's three functions are wrapped
in st.cache_data(ttl=30) (see analytics.py's _DATA_CACHE_TTL docstring), and
a cache HIT never re-executes the function body, so it never reaches the
logging call either. That is deliberate, not a gap - this page exists to
show how expensive the real work is and how often it actually happens, not
to also count the far more frequent (and near-instant) cache hits. A low
number of logged rows relative to how much a page was actually used is
itself a good sign: it means the cache is absorbing most of the repeat
work.

Platform-owner-only (see auth.require_platform_owner): this is an
operational/engineering view of the deployment itself, not something a
customer company's own admin needs or should see - same reasoning as
PI3 Connectivity and the other Application Admin pages. See
access_control.py's PLATFORM_ONLY_KEYS.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import logout_button, require_login, require_platform_owner
from db import PerformanceLog, get_session, init_db
from helpers import page_setup, render_data_table, render_function_action_intro

page_setup("Performance")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Performance")
render_function_action_intro(
    function_text=(
        "Shows how long the app's shared data-loading functions actually took, each time one of "
        "them had to fetch fresh data from the database (a cache miss) rather than reuse an "
        "already-cached result from the last 30 seconds. This is the same work that was previously "
        "invisible - now there's a real record of it instead of a guess."
    ),
    action_text=(
        "Pick a time window below to see recent activity. If a function's average duration climbs "
        "over time as more production data is recorded, that is the concrete signal to revisit "
        "analytics.py's query design again."
    ),
)

session = get_session()

WINDOWS = {
    "Last hour": dt.timedelta(hours=1),
    "Last 24 hours": dt.timedelta(hours=24),
    "Last 7 days": dt.timedelta(days=7),
    "Last 30 days": dt.timedelta(days=30),
    "All logged data": None,
}
window_label = st.selectbox("Time window", list(WINDOWS.keys()), index=1)
window = WINDOWS[window_label]

query = session.query(PerformanceLog)
if window is not None:
    cutoff = dt.datetime.utcnow() - window
    query = query.filter(PerformanceLog.created_at >= cutoff)
logs = query.order_by(PerformanceLog.created_at.desc()).all()

log_df = pd.DataFrame(
    [
        {
            "function_name": l.function_name,
            "grade_ids": l.grade_ids,
            "property_name": l.property_name,
            "row_count": l.row_count,
            "duration_ms": l.duration_ms,
            "created_at": l.created_at,
        }
        for l in logs
    ]
)

if log_df.empty:
    st.info(
        f"No performance data logged yet for '{window_label}'. This table only fills in as the "
        "Industrial Intelligence pages (Recipe Optimization, Trend Analysis, Machine Settings vs "
        "Physical Properties Correlation, Root-Cause Assistant, Machine Settings Optimization) are "
        "actually used and hit a cache miss - visit one of those pages, then come back here."
    )
    st.stop()

st.subheader("Summary")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Calls logged", len(log_df))
col2.metric("Avg duration", f"{log_df['duration_ms'].mean():.0f} ms")
col3.metric("Slowest call", f"{log_df['duration_ms'].max():.0f} ms")
col4.metric("Most recent", log_df["created_at"].max().strftime("%Y-%m-%d %H:%M UTC"))

st.subheader("By function")
st.caption(
    "One row per shared data-loading function. 'Calls' is how many times this function actually "
    "hit the database in this window - a low count relative to how much the app was used means "
    "the cache is doing its job."
)
by_function = (
    log_df.groupby("function_name")
    .agg(
        calls=("duration_ms", "count"),
        avg_duration_ms=("duration_ms", "mean"),
        p95_duration_ms=("duration_ms", lambda s: s.quantile(0.95)),
        max_duration_ms=("duration_ms", "max"),
        avg_rows=("row_count", "mean"),
    )
    .reset_index()
)
by_function["avg_duration_ms"] = by_function["avg_duration_ms"].round(1)
by_function["p95_duration_ms"] = by_function["p95_duration_ms"].round(1)
by_function["avg_rows"] = by_function["avg_rows"].round(0)
by_function = by_function.sort_values("avg_duration_ms", ascending=False).rename(
    columns={
        "function_name": "Function",
        "calls": "Calls",
        "avg_duration_ms": "Avg duration (ms)",
        "p95_duration_ms": "p95 duration (ms)",
        "max_duration_ms": "Max duration (ms)",
        "avg_rows": "Avg rows returned",
    }
)
render_data_table(by_function)

st.bar_chart(
    log_df.groupby("function_name")["duration_ms"].mean().rename("Avg duration (ms)"),
    horizontal=True,
)

st.subheader("Recent calls")
st.caption("Most recent 200 logged calls in this window, newest first.")
recent = log_df.head(200).copy()
recent["created_at"] = recent["created_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
recent = recent.rename(
    columns={
        "function_name": "Function",
        "grade_ids": "Foam grade id(s)",
        "property_name": "Property",
        "row_count": "Rows",
        "duration_ms": "Duration (ms)",
        "created_at": "Logged at (UTC)",
    }
)
render_data_table(
    recent[["Logged at (UTC)", "Function", "Foam grade id(s)", "Property", "Rows", "Duration (ms)"]],
    max_height="400px",
)

st.caption(
    "Housekeeping: rows older than 30 days are trimmed automatically (a small random chance on "
    "each new logged call), so this table doesn't grow unbounded."
)
