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

import pandas as pd
import streamlit as st

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
    close_out_session,
    get_session,
    init_db,
)
from helpers import page_setup, render_data_table, render_function_action_intro
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
            "This is the landing dashboard: a snapshot of production run count, recurring "
            "quality issues, quality test pass rate, and open customer/optimization trials across "
            "whichever plant, product family, and foam grade you filter to, plus a table of the "
            "most recently logged quality issues. The quick-action links below jump straight "
            "into logging a new run, quality test result, or quality issue."
        ),
        action_text=(
            "Filter by plant, product family, foam grade, and date range to scope the KPIs and "
            "the recent-issues table to what you're reviewing. Use the quick-action links to jump "
            "directly into common data-entry tasks instead of navigating the sidebar."
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

    # --- KPI cards: production/quality first, experiments as a secondary metric ---
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

    st.divider()

    # --- Main table: recent quality issues by product family/grade ----
    st.subheader("Recent quality issues")

    obs_rows = []
    for obs in session.query(QualityObservation).order_by(QualityObservation.observed_at.desc()).limit(25):
        # Resolve whichever of the three mutually-exclusive sources this
        # issue belongs to (see db.SAMPLE_SOURCE_TYPES) - a quality issue
        # from a customer/optimization trial has no production_run at all.
        if obs.production_run_id is not None:
            run = obs.production_run
            grade = run.foam_grade if run else None
            source_desc = f"Run #{run.id}" if run else f"Run #{obs.production_run_id}"
        elif obs.customer_trial_id is not None:
            t = obs.customer_trial
            grade = t.foam_grade if t else None
            source_desc = f"Customer Trial #{t.id} — {t.customer_name}" if t else f"Customer Trial #{obs.customer_trial_id}"
        elif obs.optimization_trial_id is not None:
            t = obs.optimization_trial
            grade = t.foam_grade if t else None
            ref = (t.improvement_initiative_reference or "(no reference)") if t else ""
            source_desc = f"Optimization Trial #{t.id} — {ref}" if t else f"Optimization Trial #{obs.optimization_trial_id}"
        else:
            grade = None
            source_desc = "—"
        family = grade.product_family if grade else None
        obs_rows.append(
            {
                "Observed": obs.observed_at,
                "Product family": family.name if family else "—",
                "Foam grade": grade.grade_name if grade else "—",
                "Source": source_desc,
                "Issue type": obs.observation_type,
                "Severity": obs.severity,
                "Frequency": obs.frequency,
                "Confidence": obs.confidence_level,
            }
        )

    if obs_rows:
        render_data_table(pd.DataFrame(obs_rows), max_height="500px")
    else:
        st.info("No quality issues recorded yet. Load demo data (see README) or start entering records.")

    st.divider()

    # --- Action buttons ------------------------------------------------------
    # Each quick action links to a page that can itself be hidden by a role's
    # page permissions or a subscription's feature flags (see access_control's
    # _visible(), computed at module level once nav is built below) -
    # st.page_link() raises if the target isn't among the pages passed to
    # st.navigation(), so only show a quick action when its page is visible.
    st.subheader("Quick actions")
    quick_actions = [
        ("pages/4_Production_Run_Trial_Record.py", "Add a production run", "➕", "production_run"),
        ("pages/9_Samples_Conditioning.py", "Add a sample", "🧊", "samples_conditioning"),
        ("pages/5_Physical_Property_Result.py", "Record a quality test result", "📏", "quality_test_result"),
        ("pages/6_Quality_Observation.py", "Add a quality issue", "📋", "quality_issue"),
    ]
    visible_actions = [a for a in quick_actions if _visible(a[3])]
    if visible_actions:
        for col, (page_path, label, icon, _key) in zip(st.columns(len(visible_actions)), visible_actions):
            col.page_link(page_path, label=label, icon=icon)

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
    ("quality_test_result", st.Page("pages/5_Physical_Property_Result.py", title="Quality Test Result", icon="📏")),
    ("quality_issue", st.Page("pages/6_Quality_Observation.py", title="Quality Issue", icon="🔍")),
]

experiment_pages = [
    ("samples_conditioning", st.Page("pages/9_Samples_Conditioning.py", title="Samples & Conditioning", icon="🧊")),
    ("customer_trials", st.Page("pages/11_Customer_Trials.py", title="Customer Trials", icon="🤝")),
    ("optimization_trials", st.Page("pages/12_Optimization_Trials.py", title="Optimization Trials", icon="🚀")),
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
    "Trials & Samples": experiment_pages,
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

try:
    pg.run()
finally:
    # See db.py close_out_session(): every rerun of every page must end
    # with no open transaction left on the database, or a read-only page
    # view (Trend Analysis, Recipe Optimization, ...) leaves one sitting
    # idle for as long as the browser tab stays open - which has already
    # caused a real production incident (an 18-hour-old idle transaction
    # blocking a schema migration). The try/finally ensures this still
    # runs even if the routed page's script raised an exception.
    close_out_session()
