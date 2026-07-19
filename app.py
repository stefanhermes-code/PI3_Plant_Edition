"""
PI3 Plant Edition - v0.1 internal prototype
Screen 1: Product Dashboard (main entry point)

HTC Global Co. Ltd - flexible slabstock foam expert system.
Non-negotiable: this is not a generic database and not a troubleshooting
tool. It exists to make recipe, runtime, physical-property, quality,
adjustment/conclusion, and approval history usable and reusable.
"""

import datetime as dt

import streamlit as st

from auth import current_user, logout_button, require_login
from db import (
    FoamGrade,
    Plant,
    ProductFamily,
    QualityObservation,
    TrialRecord,
    init_db,
)
from helpers import page_setup, show_advisory_footer
from db import get_session

page_setup("Dashboard")
init_db()
require_login()
logout_button()

user = current_user()

st.title("PI3 Plant Edition — Product Dashboard")
st.caption("Flexible slabstock foam expert system | HTC Global Co. Ltd | Internal v0.1 prototype")

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

# --- KPI cards ----------------------------------------------------------
all_trials = session.query(TrialRecord).all()
open_trials = [t for t in all_trials if t.status != "Closed"]
recurring_observations = (
    session.query(QualityObservation).filter(QualityObservation.frequency == "Recurring").all()
)
unresolved_trials = [t for t in all_trials if t.status != "Closed" and not t.can_close()]
confirmed_lessons = [
    t for t in all_trials if t.status == "Closed" and t.conclusion
]

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("Open trials", len(open_trials))
kpi2.metric("Recurring quality observations", len(recurring_observations))
kpi3.metric("Trials pending closeout requirements", len(unresolved_trials))
kpi4.metric("Confirmed / closed lessons", len(confirmed_lessons))

st.divider()

# --- Main table: recent issues by product family and severity ----------
st.subheader("Recent quality observations")

obs_rows = []
for obs in session.query(QualityObservation).order_by(QualityObservation.observed_at.desc()).limit(25):
    trial = obs.trial_record
    run = trial.production_run if trial else None
    grade = run.foam_grade if run else None
    family = grade.product_family if grade else None
    obs_rows.append(
        {
            "Observed": obs.observed_at,
            "Product family": family.name if family else "—",
            "Foam grade": grade.grade_name if grade else "—",
            "Observation type": obs.observation_type,
            "Severity": obs.severity,
            "Frequency": obs.frequency,
            "Confidence": obs.confidence_level,
            "Trial status": trial.status if trial else "—",
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
a1.page_link("pages/3_Recipe_Version_Record.py", label="Open a recipe version", icon="🧪")
a2.page_link("pages/4_Production_Run_Trial_Record.py", label="Add a trial", icon="➕")
a3.page_link("pages/6_Quality_Observation.py", label="Add a quality observation", icon="📋")
a4.page_link("pages/9_Similar_Case_Retrieval.py", label="Ask PI3 / find similar cases", icon="🔎")

show_advisory_footer()
