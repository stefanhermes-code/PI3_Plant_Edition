"""
PI3 Plant Edition - v0.1 internal prototype
Main entry point / navigation router.

HTC Global Co. Ltd - flexible slabstock foam expert system, commercialised
as PI3 - Flexible PU Foam Intelligence.

This file sets page config, sidebar branding, and global styling once (it
always runs first, on every page view, under st.navigation), then routes to
the individual screens.
"""

import datetime as dt

import streamlit as st

from auth import current_user, logout_button, require_login
from db import (
    FoamGrade,
    PhysicalPropertyResult,
    Plant,
    ProductFamily,
    ProductionRun,
    QualityObservation,
    TrialRecord,
    get_session,
    init_db,
)
from helpers import page_setup, show_advisory_footer
from version import APP_VERSION

LOGO_PATH = "assets/htc_global_logo_blue_steel.png"

st.set_page_config(page_title="PI3 - Flexible PU Foam Intelligence", page_icon="🧪", layout="wide")

# Sidebar branding: a larger HTC Global logo with the app version right next
# to it, above the navigation menu (bump version.py on every commit/push).
# Built with columns rather than st.logo() so the version text can sit
# beside the logo instead of being confined to st.logo()'s fixed header slot.
with st.sidebar:
    logo_col, version_col = st.columns([1, 1.4], vertical_alignment="center")
    logo_col.image(LOGO_PATH, width=140)
    with version_col:
        st.markdown("**PI3 Plant Edition**")
        st.caption(f"v{APP_VERSION}")
    st.divider()

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
            "HTC Global Co. Ltd | Internal v0.1 prototype"
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
    all_results = session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.pass_fail.isnot(None)).all()
    pass_count = len([r for r in all_results if r.pass_fail == "Pass"])
    pass_rate = f"{round(100 * pass_count / len(all_results))}%" if all_results else "—"
    active_trials = session.query(TrialRecord).filter(TrialRecord.status != "Closed").count()

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Production runs", len(all_runs))
    kpi2.metric("Recurring quality observations", len(recurring_observations))
    kpi3.metric("Physical property pass rate", pass_rate)
    kpi4.metric("Active trials / experiments", active_trials)

    st.divider()

    # --- Main table: recent quality observations by product family/grade ----
    st.subheader("Recent quality observations")

    obs_rows = []
    for obs in session.query(QualityObservation).order_by(QualityObservation.observed_at.desc()).limit(25):
        run = obs.production_run
        grade = run.foam_grade if run else None
        family = grade.product_family if grade else None
        obs_rows.append(
            {
                "Observed": obs.observed_at,
                "Product family": family.name if family else "—",
                "Foam grade": grade.grade_name if grade else "—",
                "Run": f"#{run.id}" if run else "—",
                "Observation type": obs.observation_type,
                "Severity": obs.severity,
                "Frequency": obs.frequency,
                "Confidence": obs.confidence_level,
                "Trial": f"#{obs.trial_record_id}" if obs.trial_record_id else "—",
            }
        )

    if obs_rows:
        st.dataframe(obs_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No quality observations recorded yet. Load demo data (see README) or start entering records.")

    st.divider()

    # --- Action buttons ------------------------------------------------------
    st.subheader("Quick actions")
    a1, a2, a3, a4 = st.columns(4)
    a1.page_link("pages/4_Production_Run_Trial_Record.py", label="Add a production run", icon="➕")
    a2.page_link("pages/5_Physical_Property_Result.py", label="Record a property result", icon="📏")
    a3.page_link("pages/6_Quality_Observation.py", label="Add a quality observation", icon="📋")
    a4.page_link("pages/9_Similar_Case_Retrieval.py", label="Ask PI3 / find similar cases", icon="🔎")

    show_advisory_footer()


overview_page = st.Page(render_overview, title="Overview", icon="🏠", default=True)

setup_pages = [
    st.Page("pages/1_Plant_Installation_Overview.py", title="Plant & Installation Overview", icon="🏭"),
    st.Page("pages/2_Product_Family_Foam_Grade.py", title="Product Family & Foam Grade", icon="🧬"),
    st.Page("pages/3_Recipe_Version_Record.py", title="Recipe Version Record", icon="📋"),
]

production_pages = [
    st.Page("pages/4_Production_Run_Trial_Record.py", title="Production Run", icon="⚙️"),
    st.Page("pages/5_Physical_Property_Result.py", title="Physical Property Result", icon="📏"),
    st.Page("pages/6_Quality_Observation.py", title="Quality Observation", icon="🔍"),
]

experiment_pages = [
    st.Page("pages/13_Trial_Experiment.py", title="Trial / Experiment", icon="🧫"),
    st.Page("pages/7_Adjustment_Conclusion.py", title="Adjustment & Conclusion", icon="🛠️"),
    st.Page("pages/8_Approval_Review.py", title="Approval & Review", icon="✅"),
    st.Page("pages/9_Similar_Case_Retrieval.py", title="Similar Case Retrieval", icon="🧭"),
]

intelligence_pages = [
    st.Page("pages/10_PI3_AI_Connectivity.py", title="PI3 / AI Connectivity", icon="🤖"),
]

admin_pages = [
    st.Page("pages/11_Maintenance_License_Admin.py", title="Maintenance & License Admin", icon="💳"),
    st.Page("pages/12_Demo_Data_Admin.py", title="Demo Data Admin", icon="🗂️"),
]

pg = st.navigation(
    {
        "PI3 Plant Edition": [overview_page],
        "Setup": setup_pages,
        "Production": production_pages,
        "Experiments (optional)": experiment_pages,
        "Intelligence": intelligence_pages,
        "Admin": admin_pages,
    }
)
pg.run()
