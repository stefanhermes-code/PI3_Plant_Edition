"""Screen 27: Performance

Added 2026-08-02 after a reported "app feels slow in general", expanded
2026-08-05 (v2.0 performance audit) to cover three separate things that
were previously invisible, each measured only as real usage happens - none
of these run on a fixed schedule:

1. Page load time (PageLoadLog) - the FULL page-script execution time,
   logged once per Streamlit rerun of ANY page (a fresh navigation and
   every widget-triggered rerun on that same page - see app.py's single
   st.navigation() pg.run() call, timed centrally there so no individual
   page file needed to be touched). This is the direct answer to the
   original complaint ("a simple screen-build takes 15-20 seconds").
2. PI3 call time (PI3InteractionLog.response_time_ms) - already captured
   for every PI3 call since the Gate 6 audit-logging batch (items 49-51),
   just not surfaced anywhere until now. Broken down by call_site - the 5
   fixed-prompt Intelligence-page sections each pass their own label (see
   ai_assistant.ask_assistant()'s call_site parameter) plus the free-form
   "Ask PI3" box.
3. Shared data-loading function time (PerformanceLog) - the original
   metric this page shipped with: analytics.py's three functions
   (run_settings_dataframe, property_results_dataframe,
   actual_usage_dataframe), each logging one row only on a cache MISS (a
   cache HIT never re-executes the function body, so it never reaches the
   logging call either - see analytics._log_performance). The most
   granular/technical of the three, kept last.

Each section below guards its own "no data yet" case independently rather
than the whole page stopping if just one of the three tables is empty -
PageLoadLog in particular will already have plenty of rows from ordinary
navigation long before anyone has triggered a PerformanceLog cache miss.

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
from db import PageLoadLog, PerformanceLog, PI3InteractionLog, get_session, init_db
from helpers import CHART_ZOOM_HINT, page_setup, render_data_table, render_function_action_intro

page_setup("Performance")
init_db()
require_login()
require_platform_owner()
logout_button()

st.title("Performance")
render_function_action_intro(
    function_text=(
        "Three things are measured behind the scenes, each recorded only as real usage happens - "
        "none of this runs on a fixed schedule: how long a page takes to fully load (every "
        "navigation and every rerun caused by a click or filter change), how long PI3 takes to "
        "answer a question, and how long the app's shared data-loading functions take on the rare "
        "occasion they have to fetch fresh data instead of reusing a recent cached result."
    ),
    action_text=(
        "Pick a time window below. If a load time climbs over time as more production data is "
        "recorded, that's the concrete signal to revisit that part of the app again."
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
cutoff = dt.datetime.utcnow() - window if window is not None else None

# Bucket size scales with the selected window so a timeline chart always has
# a sensible number of points: 5-minute buckets for "Last hour" (a daily
# bucket would collapse it to one point), hourly for "Last 24 hours", daily
# otherwise. Shared by all three sections below.
_BUCKET_FREQ = {"Last hour": "5min", "Last 24 hours": "1h"}
bucket_freq = _BUCKET_FREQ.get(window_label, "1D")


def _timeline_chart(df, value_col, y_title):
    """Average `value_col` per time bucket, plus a dashed overall-average
    reference line, with a non-zero-anchored Y-axis (see
    helpers.render_scatter_chart_no_zero / views/16_Trend_Analysis.py for
    the same fix applied elsewhere: these durations cluster in a narrow
    band, so a zero-anchored axis squeezes the real variation into a thin
    sliver at the top). Shared by all three sections below rather than
    three near-identical copies."""
    timeline = (
        df.set_index("created_at")
        .resample(bucket_freq)[value_col]
        .mean()
        .dropna()
        .rename(y_title)
        .reset_index()
    )
    overall_avg = df[value_col].mean()
    line = (
        alt.Chart(timeline)
        .mark_line(point=True)
        .encode(
            x=alt.X("created_at:T", title=None),
            y=alt.Y(f"{y_title}:Q", title=y_title, scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("created_at:T", title="When"), alt.Tooltip(f"{y_title}:Q", format=".1f")],
        )
    )
    avg_rule = (
        alt.Chart(pd.DataFrame({"avg": [overall_avg]}))
        .mark_rule(color="#E45756", strokeDash=[4, 4])
        .encode(y=alt.Y("avg:Q"))
    )
    st.altair_chart((line + avg_rule).interactive(), use_container_width=True)
    st.caption(CHART_ZOOM_HINT)


def _breakdown_table(df, group_col, group_label, value_col, unit_label, count_label):
    """Shared 'by X' summary table (group name, average, slowest, count) -
    same shape used for by-page, by-call-site, and by-data-type below."""
    by_group = (
        df.groupby(group_col)
        .agg(avg=(value_col, "mean"), slowest=(value_col, "max"), n=(value_col, "count"))
        .reset_index()
    )
    decimals = 1 if unit_label == "s" else 0
    by_group["avg"] = by_group["avg"].round(decimals)
    by_group["slowest"] = by_group["slowest"].round(decimals)
    by_group = by_group.sort_values("avg", ascending=False).rename(
        columns={
            group_col: group_label,
            "avg": f"Average ({unit_label})",
            "slowest": f"Slowest ({unit_label})",
            "n": count_label,
        }
    )
    render_data_table(by_group)


# ---------------------------------------------------------------------------
# 1. Page load time
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Page load time")
st.caption(
    "How long each page took to fully load and become interactive - measured on every "
    "navigation to a page AND every rerun on that page (clicking a button, changing a filter). "
    "This is exactly what a plant reviewer experiences as 'the app is slow'."
)

page_query = session.query(PageLoadLog)
if cutoff is not None:
    page_query = page_query.filter(PageLoadLog.created_at >= cutoff)
page_rows = page_query.order_by(PageLoadLog.created_at.desc()).all()
page_load_df = pd.DataFrame(
    [{"page_name": r.page_name, "duration_ms": r.duration_ms, "created_at": r.created_at} for r in page_rows]
)

if page_load_df.empty:
    st.info(f"No page-load data logged yet for '{window_label}'. This fills in automatically as pages are visited.")
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Pages loaded", len(page_load_df))
    c2.metric("Avg load time", f"{page_load_df['duration_ms'].mean():.0f} ms")
    c3.metric("Slowest load", f"{page_load_df['duration_ms'].max():.0f} ms")
    _timeline_chart(page_load_df, "duration_ms", "Load time (ms)")
    st.caption("By page:")
    _breakdown_table(page_load_df, "page_name", "Page", "duration_ms", "ms", "Times loaded")

# ---------------------------------------------------------------------------
# 2. PI3 call time
# ---------------------------------------------------------------------------
st.divider()
st.subheader("PI3 call time")
st.caption(
    "How long PI3 took to answer, from the 5 fixed-prompt sections on the Industrial Intelligence "
    "pages and the free-form 'Ask PI3' box."
)

PI3_CALL_SITE_LABELS = {
    "recipe_optimization": "Recipe Optimization",
    "trend_analysis": "Trend Analysis",
    "process_property_correlation": "Machine Settings vs Physical Properties Correlation",
    "root_cause_assistant": "Root-Cause Assistant",
    "machine_settings_optimization": "Machine Settings Optimization",
    "ask_plant_question": "Free-form “Ask PI3”",
    # Fallback for interactions logged before 2026-08-05, when every
    # fixed-prompt section shared this one generic label - see
    # ai_assistant.ask_assistant()'s call_site parameter.
    "ask_assistant": "Fixed-prompt section (older data, not yet by page)",
}

pi3_query = session.query(PI3InteractionLog).filter(PI3InteractionLog.response_time_ms.isnot(None))
if cutoff is not None:
    pi3_query = pi3_query.filter(PI3InteractionLog.created_at >= cutoff)
pi3_rows = pi3_query.order_by(PI3InteractionLog.created_at.desc()).all()
pi3_df = pd.DataFrame(
    [
        {
            "call_site": PI3_CALL_SITE_LABELS.get(r.call_site, r.call_site),
            "duration_ms": r.response_time_ms,
            "duration_s": r.response_time_ms / 1000,
            "created_at": r.created_at,
        }
        for r in pi3_rows
    ]
)

if pi3_df.empty:
    st.info(f"No PI3 call data logged yet for '{window_label}'. This fills in automatically as PI3 is used.")
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("PI3 calls", len(pi3_df))
    c2.metric("Avg response time", f"{pi3_df['duration_s'].mean():.1f} s")
    c3.metric("Slowest response", f"{pi3_df['duration_s'].max():.1f} s")
    _timeline_chart(pi3_df, "duration_s", "Response time (s)")
    st.caption("By where it was asked:")
    _breakdown_table(pi3_df, "call_site", "Asked from", "duration_s", "s", "Number of calls")

# ---------------------------------------------------------------------------
# 3. Shared data-loading function time (the original metric this page
#    shipped with - most granular/technical, kept last)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Data-loading time")
st.caption(
    "PI3 loads three kinds of data behind the scenes for the analysis pages. This shows how long "
    "each one takes to load, on average, on the rare occasion it actually has to fetch fresh data "
    "rather than reuse a result it already loaded in the last 30 seconds."
)

data_query = session.query(PerformanceLog)
if cutoff is not None:
    data_query = data_query.filter(PerformanceLog.created_at >= cutoff)
data_rows = data_query.order_by(PerformanceLog.created_at.desc()).all()
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
        for l in data_rows
    ]
)

if log_df.empty:
    st.info(
        f"No data-loading data logged yet for '{window_label}'. This only fills in as the "
        "Industrial Intelligence pages (Recipe Optimization, Trend Analysis, Machine Settings vs "
        "Physical Properties Correlation, Root-Cause Assistant, Machine Settings Optimization) are "
        "actually used and hit a cache miss - visit one of those pages, then come back here."
    )
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Loads logged", len(log_df))
    c2.metric("Avg duration", f"{log_df['duration_ms'].mean():.0f} ms")
    c3.metric("Slowest load", f"{log_df['duration_ms'].max():.0f} ms")
    _timeline_chart(log_df, "duration_ms", "Load time (ms)")

    FUNCTION_LABELS = {
        "run_settings_dataframe": "Production run data",
        "property_results_dataframe": "Quality test result data",
        "actual_usage_dataframe": "Material usage data",
    }
    by_function_df = log_df.assign(data_type=log_df["function_name"].map(lambda f: FUNCTION_LABELS.get(f, f)))
    st.caption("By data type:")
    _breakdown_table(by_function_df, "data_type", "Data type", "duration_ms", "ms", "Times reloaded")
    st.caption(
        "'Times reloaded' is how often this data type actually had to be fetched fresh in this "
        "window - a low number relative to how much the app was used means the cache is doing its job."
    )

st.divider()
st.caption(
    "Housekeeping: rows older than 30 days are trimmed automatically from all three logs above (a "
    "small random chance on each new logged call), so none of them grow unbounded."
)
