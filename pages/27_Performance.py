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

import altair as alt
import pandas as pd
import streamlit as st

from auth import logout_button, require_login, require_platform_owner
from db import PerformanceLog, get_session, init_db
from helpers import CHART_ZOOM_HINT, page_setup, render_data_table, render_function_action_intro

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

st.subheader("Load time over time")
st.caption(
    "How long a data load took, plotted over time. The dashed 'Overall average' line is the "
    "average for the whole time window selected above - if the solid line is climbing above it "
    "as more production data is recorded, that's the concrete signal something needs attention."
)

# Bucket size scales with the selected window so the chart always has a
# sensible number of points: 5-minute buckets for "Last hour" (a daily
# bucket would collapse it to one point), hourly for "Last 24 hours", daily
# otherwise. Every bucket is one point on the chart, average load time
# across all data types in that bucket.
_BUCKET_FREQ = {
    "Last hour": "5min",
    "Last 24 hours": "1h",
}
bucket_freq = _BUCKET_FREQ.get(window_label, "1D")

timeline = (
    log_df.set_index("created_at")
    .resample(bucket_freq)["duration_ms"]
    .mean()
    .dropna()
    .rename("Load time (ms)")
    .reset_index()
)
overall_avg = log_df["duration_ms"].mean()

# Plain st.line_chart always anchors the Y-axis at 0, which is fine for a
# metric that's naturally zero-based but not here: load times cluster in a
# narrow band (see this page's earlier chart, roughly 700-1300ms), so a
# zero-anchored axis squeezes all the real variation into a thin sliver at
# the top - same fix applied to every other trend chart in the app (see
# helpers.render_scatter_chart_no_zero, pages/16_Trend_Analysis.py).
line = (
    alt.Chart(timeline)
    .mark_line(point=True)
    .encode(
        x=alt.X("created_at:T", title=None),
        y=alt.Y("Load time (ms):Q", title="Load time (ms)", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("created_at:T", title="When"), alt.Tooltip("Load time (ms):Q", format=".0f")],
    )
)
avg_rule = (
    alt.Chart(pd.DataFrame({"avg": [overall_avg]}))
    .mark_rule(color="#E45756", strokeDash=[4, 4])
    .encode(y=alt.Y("avg:Q"))
)
st.altair_chart((line + avg_rule).interactive(), use_container_width=True)
st.caption(CHART_ZOOM_HINT)

st.subheader("By data type")
st.caption(
    "PI3 loads three kinds of data behind the scenes for the analysis pages. This shows how long "
    "each one takes to load, on average, when it actually has to fetch fresh data rather than "
    "reuse a result it already loaded in the last 30 seconds."
)
FUNCTION_LABELS = {
    "run_settings_dataframe": "Production run data",
    "property_results_dataframe": "Quality test result data",
    "actual_usage_dataframe": "Material usage data",
}
by_function = (
    log_df.assign(data_type=log_df["function_name"].map(lambda f: FUNCTION_LABELS.get(f, f)))
    .groupby("data_type")
    .agg(
        avg_duration_ms=("duration_ms", "mean"),
        slowest_ms=("duration_ms", "max"),
        reload_count=("duration_ms", "count"),
    )
    .reset_index()
)
by_function["avg_duration_ms"] = by_function["avg_duration_ms"].round(0)
by_function["slowest_ms"] = by_function["slowest_ms"].round(0)
by_function = by_function.sort_values("avg_duration_ms", ascending=False).rename(
    columns={
        "data_type": "Data type",
        "avg_duration_ms": "Average load time (ms)",
        "slowest_ms": "Slowest load (ms)",
        "reload_count": "Times reloaded",
    }
)
render_data_table(by_function)
st.caption(
    "'Times reloaded' is how often this data type actually had to be fetched fresh in this window "
    "- a low number relative to how much the app was used means the cache is doing its job."
)

st.caption(
    "Housekeeping: rows older than 30 days are trimmed automatically (a small random chance on "
    "each new logged call), so this table doesn't grow unbounded."
)
