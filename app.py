"""
PI3 Plant Edition
Main entry point / navigation router.

HTC Global Co. Ltd - flexible slabstock foam expert system, commercialised
as PI3 - Flexible PU Foam Intelligence.

This file sets page config, sidebar branding, and global styling once (it
always runs first, on every page view, under st.navigation), then routes to
the individual screens.
"""

import datetime as dt
import time

import sqlalchemy.exc as sa_exc
import streamlit as st

import audit_log
from access_control import denied_page_keys, page_visible
from auth import current_user, logout_button, require_login
from db import (
    Company,
    CustomerTrial,
    FoamGrade,
    OptimizationTrial,
    PhysicalPropertyResult,
    Plant,
    ProductFamily,
    ProductionRun,
    QualityObservation,
    RecipeVersion,
    Sample,
    close_out_session,
    get_session,
    init_db,
)
from helpers import page_setup, render_function_action_intro
from quality_standards import compute_pass_fail
from version import APP_VERSION

LOGO_PATH = "assets/htc_global_logo_blue_steel.png"

st.set_page_config(page_title="PI3 - Flexible PU Foam Intelligence", page_icon="🧪", layout="wide")

# Light styling on top of the .streamlit/config.toml color theme.
st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 1px solid #DCE6EC;
        border-radius: 10px;
        padding: 10px 16px 4px 16px;
    }
    div[data-testid="stExpander"] {
        border-radius: 10px;
        border: 1px solid #DCE6EC;
    }
    div[data-testid="stContainer"] {
        border-radius: 10px;
    }
    /* st.caption() defaults to small, low-contrast grey text everywhere in
       the app (page descriptions, disclaimers, table captions, ...) - hard
       to read for plant-floor reviewers. Force it to normal body size and
       full black instead, app-wide, since this file's global style block
       runs first on every page under st.navigation. */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] * {
        font-size: 1rem !important;
        color: #000000 !important;
        opacity: 1 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_overview():
    """Screen 1: Product Dashboard (default landing page)."""
    page_setup("Overview")
    init_db()
    require_login()
    logout_button()

    user = current_user()

    header_logo, header_text = st.columns([1, 6])
    with header_logo:
        st.image(LOGO_PATH, width=90)
    with header_text:
        st.title("PI3 — Flexible PU Foam Intelligence")
        st.caption(
            "Product Dashboard | Flexible slabstock foam expert system | "
            "HTC Global Co. Ltd"
        )
    render_function_action_intro(
        function_text=(
            "This is the landing dashboard: a snapshot of how much is in the system and how it's "
            "trending - recipes, production runs, samples/trials by source, quality tests, quality "
            "issues, pass rate, and open trials - across whichever plant, product family, and foam "
            "grade you filter to."
        ),
        action_text=(
            "Filter by plant, product family, foam grade, and date range to scope the KPIs to what "
            "you're reviewing. Use the sidebar to navigate into any specific record or workflow."
        ),
    )

    session = get_session()

    # --- Top filters ------------------------------------------------------
    col1, col2, col3, col4 = st.columns(4)

    plants = session.query(Plant).all()
    with col1:
        plant_filter = st.selectbox(
            "Plant", [None] + plants, format_func=lambda p: "All plants" if p is None else p.name
        )

    families_query = session.query(ProductFamily)
    if plant_filter:
        families_query = families_query.filter(ProductFamily.plant_id == plant_filter.id)
    families = families_query.all()
    with col2:
        family_filter = st.selectbox(
            "Product family", [None] + families, format_func=lambda f: "All families" if f is None else f.name
        )

    grades_query = session.query(FoamGrade)
    if family_filter:
        grades_query = grades_query.filter(FoamGrade.product_family_id == family_filter.id)
    grades = grades_query.all()
    with col3:
        grade_filter = st.selectbox(
            "Foam grade", [None] + grades, format_func=lambda g: "All grades" if g is None else g.grade_name
        )

    with col4:
        date_range = st.date_input(
            "Date range",
            value=(dt.date.today() - dt.timedelta(days=90), dt.date.today()),
        )

    st.divider()

    # --- KPI cards ----------------------------------------------------
    # Row 1 (unchanged): the original production/quality-rate/open-trials
    # snapshot.
    all_runs = session.query(ProductionRun).all()
    recurring_observations = (
        session.query(QualityObservation).filter(QualityObservation.frequency == "Recurring").all()
    )
    all_results = session.query(PhysicalPropertyResult).all()
    # Recomputed live via compute_pass_fail() rather than trusted from each
    # result's stored pass_fail column - see the same note in
    # analytics.property_results_dataframe. Keeps this KPI in sync with the
    # current tolerance rules immediately, with no separate recompute step.
    computed_verdicts = [
        compute_pass_fail(r.property_name, r.target_value, r.actual_value) for r in all_results
    ]
    known_verdicts = [v for v in computed_verdicts if v is not None]
    pass_count = known_verdicts.count("Pass")
    pass_rate = f"{round(100 * pass_count / len(known_verdicts))}%" if known_verdicts else "—"
    # Open trials across both independent lab-trial flows (see
    # db.py's CustomerTrial / OptimizationTrial) - the old TrialRecord
    # concept (a formal-experiment flag on a production run) was removed
    # 2026-08-04: zero real rows across 244 production runs, fully
    # superseded by these two.
    open_customer_trials = session.query(CustomerTrial).filter(CustomerTrial.status != "Closed").count()
    open_optimization_trials = (
        session.query(OptimizationTrial).filter(OptimizationTrial.status != "Closed").count()
    )
    active_trials = open_customer_trials + open_optimization_trials

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Production runs", len(all_runs))
    kpi2.metric("Recurring quality issues", len(recurring_observations))
    kpi3.metric("Quality test pass rate", pass_rate)
    kpi4.metric("Open customer/optimization trials", active_trials)

    # Row 2 (added 2026-08-05 per user request): overall system volume -
    # how much master data and how many quality records exist, full totals
    # rather than the "recurring only"/"open only" slices above.
    recipes_count = session.query(RecipeVersion).count()
    quality_tests_count = len(all_results)
    quality_issues_count = session.query(QualityObservation).count()

    kpi5, kpi6, kpi7 = st.columns(3)
    kpi5.metric("Recipes", recipes_count)
    kpi6.metric("Quality tests", quality_tests_count)
    kpi7.metric("Quality issues", quality_issues_count)

    # Row 3 (added 2026-08-05): trials/samples broken out by the app's 3
    # sample sources (see db.SAMPLE_SOURCE_TYPES) - "Production samples"
    # covers the same production-run-linked samples the run count above
    # doesn't otherwise surface a total for.
    production_samples_count = (
        session.query(Sample).filter(Sample.production_run_id.isnot(None)).count()
    )
    customer_trials_count = session.query(CustomerTrial).count()
    optimization_trials_count = session.query(OptimizationTrial).count()

    kpi8, kpi9, kpi10 = st.columns(3)
    kpi8.metric("Production samples", production_samples_count)
    kpi9.metric("Customer trials", customer_trials_count)
    kpi10.metric("Optimization trials", optimization_trials_count)

overview_page = st.Page(render_overview, title="Overview", icon="🏠", default=True)
report_page = st.Page("pages/21_Report.py", title="Report", icon="🖨️")

setup_pages = [
    ("plant_overview", st.Page("pages/1_Plant_Installation_Overview.py", title="Plant & Foam Equipment Overview", icon="🏭")),
    ("product_family_foam_grade", st.Page("pages/2_Product_Family_Foam_Grade.py", title="Product Family & Foam Grade", icon="🧬")),
    ("raw_materials", st.Page("pages/14_Raw_Materials.py", title="Raw Materials", icon="🧴")),
    ("recipes", st.Page("pages/3_Recipe_Version_Record.py", title="Recipes", icon="📋")),
]

production_pages = [
    ("production_run", st.Page("pages/4_Production_Run_Trial_Record.py", title="Production Run", icon="⚙️")),
]

experiment_pages = [
    ("samples_conditioning", st.Page("pages/9_Samples_Conditioning.py", title="Production Samples", icon="🧊")),
    ("customer_trials", st.Page("pages/11_Customer_Trials.py", title="Customer Trials & Samples", icon="🤝")),
    ("optimization_trials", st.Page("pages/12_Optimization_Trials.py", title="Optimization Trials & Samples", icon="🚀")),
]

# Split out from Production 2026-08-04 per user direction (segregation of
# duties: quality inspection/testing shouldn't sit under the same nav
# section as the production floor that made the batch). Ordered after
# Samples & Trials since a result/issue is always recorded against a
# sample, and a sample can come from any of the 3 pages in that section.
quality_pages = [
    ("quality_test_result", st.Page("pages/5_Physical_Property_Result.py", title="Quality Test Result", icon="📏")),
    ("quality_issue", st.Page("pages/6_Quality_Observation.py", title="Quality Issue", icon="🔍")),
]

# The value of PI3 Plant Edition is the join that already exists in the
# schema: recipe, machine settings, and physical property / quality
# results all keyed to the same production run. These pages are that join
# put to work - named after what they actually do, not branded as "AI".
industrial_intelligence_pages = [
    ("recipe_optimization", st.Page("pages/15_Recipe_Optimization.py", title="Recipe Optimization", icon="🧪")),
    ("trend_analysis", st.Page("pages/16_Trend_Analysis.py", title="Trend Analysis", icon="📈")),
    (
        "machine_settings_correlation",
        st.Page(
            "pages/17_Process_Property_Correlation.py",
            title="Machine Settings vs Physical Properties Correlation",
            icon="🔗",
        ),
    ),
    ("root_cause_assistant", st.Page("pages/18_Root_Cause_Assistant.py", title="Root-Cause Assistant", icon="🩺")),
    ("machine_settings_optimization", st.Page("pages/19_Machine_Settings_Optimization.py", title="Machine Settings Optimization", icon="⚙️")),
    ("expert_notes", st.Page("pages/20_Expert_Notes.py", title="Expert Notes", icon="🧠")),
]

admin_pages = [
    ("user_roles_admin", st.Page("pages/24_User_Roles.py", title="User Roles", icon="🔑")),
]

platform_admin_pages = [
    ("companies_admin", st.Page("pages/23_Companies.py", title="Companies", icon="🏢")),
    ("subscription_types_admin", st.Page("pages/22_Subscription_Types.py", title="Subscription Types", icon="🎟️")),
    ("default_user_roles_admin", st.Page("pages/26_Default_User_Roles.py", title="Default User Roles", icon="🗝️")),
    ("user_accounts_admin", st.Page("pages/25_User_Accounts.py", title="User Accounts", icon="👤")),
    ("pi3_ai_connectivity", st.Page("pages/10_PI3_AI_Connectivity.py", title="PI3 Connectivity", icon="🤖")),
    ("performance_admin", st.Page("pages/27_Performance.py", title="Performance", icon="⚡")),
    ("pilot_analysis_admin", st.Page("pages/28_Pilot_Analysis.py", title="Pilot Analysis", icon="🔬")),
]

nav_sections_with_keys = {
    "Setup": setup_pages,
    "Production": production_pages,
    "Samples & Trials": experiment_pages,
    "Quality": quality_pages,
    "Industrial Intelligence": industrial_intelligence_pages,
    "Company Admin": admin_pages,
    "Application Admin": platform_admin_pages,
}

# Nav visibility: a fresh, unauthenticated script run has no role/company in
# session_state yet (require_login() only populates it once a page actually
# runs, further down) - show everything unfiltered in that case, since every
# page still gates its own content behind require_login()/require_role().
# Once logged in, narrow by the user's role (page_key deny-list) and their
# company's subscription (feature flags) - see access_control.py.
init_db()
_nav_session = get_session()
_is_authenticated = bool(st.session_state.get("authenticated"))
_is_platform_owner = bool(st.session_state.get("is_platform_owner", False)) if _is_authenticated else True
_is_super_admin = bool(st.session_state.get("is_super_admin", False)) if _is_authenticated else True
_denied_keys = denied_page_keys(_nav_session, st.session_state.get("role_id")) if _is_authenticated else set()
_company_id = st.session_state.get("company_id") if _is_authenticated else None
_subscription = None
if _company_id:
    _company = _nav_session.get(Company, _company_id)
    _subscription = _company.subscription_type if _company else None

def _visible(key):
    return page_visible(
        key, is_platform_owner=_is_platform_owner, subscription=_subscription, denied_keys=_denied_keys,
        is_super_admin=_is_super_admin,
    )


nav_sections = {
    section_name: [page for key, page in pages if _visible(key)]
    for section_name, pages in nav_sections_with_keys.items()
}
nav_sections = {name: pages for name, pages in nav_sections.items() if pages}

# Overview is the landing dashboard, not a gated feature - always shown once
# logged in. Report is a subscription-gated feature (reports_enabled), so it
# goes through the same page_visible() check as everything else.
top_pages = [overview_page]
if _visible("report"):
    top_pages.append(report_page)

# position="hidden" turns off Streamlit's built-in nav widget so we can draw
# our own sidebar from scratch below. This is the only reliable way to get
# custom content (logo + version) to appear ABOVE the page links: Streamlit
# always renders its automatic nav menu first, before any other sidebar
# content, regardless of where in the script that content is written.
pg = st.navigation(
    {"PI3 Plant Edition": top_pages, **nav_sections},
    position="hidden",
)

with st.sidebar:
    logo_col, version_col = st.columns([1, 1.4], vertical_alignment="center")
    logo_col.image(LOGO_PATH, width=140)
    with version_col:
        st.markdown("**PI3 Plant Edition**")
        st.caption(f"v{APP_VERSION}")
    st.divider()

    for page in top_pages:
        st.page_link(page)
    for section_name, pages in nav_sections.items():
        st.caption(section_name)
        for page in pages:
            st.page_link(page)

_page_load_t0 = time.perf_counter()
try:
    pg.run()
    # Reaching here means this rerun's page script ran to completion using
    # the cached session without SQLAlchemy objecting - clear any earlier
    # recovery flag so a *future* one-off corruption (see except below) can
    # still be auto-recovered from, rather than only ever once per tab.
    st.session_state["_sa_session_recovery_attempted"] = False
except sa_exc.InvalidRequestError:
    # Production incident, 2026-08-05: a plain, ordinary .all() query on
    # Default User Roles crashed with sqlalchemy.exc.InvalidRequestError
    # from deep inside Session._connection_for_bind - not a bad query, a
    # broken Session. get_session() deliberately caches ONE SQLAlchemy
    # Session per browser tab across every rerun (see its docstring), but
    # a Session is not thread-safe, and Streamlit can cancel an in-flight
    # script run the moment a newer rerun supersedes it (e.g. two quick
    # clicks, or a slow network round trip). If that cancellation lands
    # mid-statement, the Session's internal transaction state machine is
    # left stuck in a state that refuses ALL further SQL - on any page,
    # not just the one that was interrupted - because the Session object
    # itself is broken, not the data or the query.
    #
    # There is nothing page-specific to fix: discard the cached session
    # (the next get_session() call builds a fresh one against a
    # pool_pre_ping-verified connection) and rerun once so the user gets
    # the page they asked for instead of a crash. Guarded to one recovery
    # attempt per browser tab (reset above on the next clean run) so a
    # different, page-code-level bug that happens to also raise
    # InvalidRequestError can't silently rerun forever instead of
    # surfacing normally.
    if not st.session_state.get("_sa_session_recovery_attempted"):
        st.session_state["_sa_session_recovery_attempted"] = True
        st.session_state.pop("_sa_session", None)
        st.rerun()
    raise
finally:
    # Only touch the session if it's still the healthy one this rerun
    # started with - if the except branch above discarded it, there is no
    # transaction left to time/close, and touching the (broken, discarded)
    # local reference again would just recreate the same failure.
    if st.session_state.get("_sa_session") is _nav_session:
        # Page-load timing (added 2026-08-05, v2.0 performance audit): pg.run()
        # is the single choke point every page's script executes through, on
        # both a fresh navigation and every widget-triggered rerun - timing
        # around it here captures the real "how long did this page take"
        # metric for every page, with no per-page-file instrumentation needed.
        # Logged via the same session pg.run() itself used (get_session()
        # returns one session per browser tab - see db.py), then committed by
        # close_out_session() right below, same as any other write a page made
        # during this rerun. Uses _nav_session rather than a fresh get_session()
        # call so this still works even if the routed page's own script raised
        # partway through (the finally still runs) and left that session's
        # transaction in a state a NEW session wouldn't share.
        audit_log.log_page_load(_nav_session, pg.title, (time.perf_counter() - _page_load_t0) * 1000)
        # See db.py close_out_session(): every rerun of every page must end
        # with no open transaction left on the database, or a read-only page
        # view (Trend Analysis, Recipe Optimization, ...) leaves one sitting
        # idle for as long as the browser tab stays open - which has already
        # caused a real production incident (an 18-hour-old idle transaction
        # blocking a schema migration). The try/finally ensures this still
        # runs even if the routed page's script raised an exception.
        close_out_session()
