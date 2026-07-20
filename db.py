"""
PI3 Plant Edition - v0.1 internal prototype
Database layer: SQLAlchemy models for the 16 approved v0.1 entities.

Connection:
- Production / Streamlit Cloud: set st.secrets["DATABASE_URL"] to a Supabase
  Postgres connection string (Session pooler, e.g.
  postgresql+psycopg2://postgres:<password>@<host>:5432/postgres).
- Local development: falls back to a local SQLite file (pi3_local.db) if
  DATABASE_URL is not set. Do NOT rely on SQLite for the deployed app -
  Streamlit Community Cloud's filesystem is not guaranteed to persist
  across reboots/redeploys.
"""

import datetime as dt
import os

import streamlit as st
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


class _NoDeepCopyMixin:
    """Mixin applied to every ORM model via declarative_base(cls=...).

    Streamlit's widget-state tracking (session_state.py: register_widget)
    deepcopies a selectbox's option values to detect changes across reruns.
    Several pages pass live ORM objects (Plant, FoamGrade, TrialRecord, ...)
    directly as selectbox options. Once any bidirectional relationship
    collection reachable from one of those objects becomes non-empty (e.g.
    a trial gets its first physical property result), copy.deepcopy hits a
    known SQLAlchemy/backref-collection incompatibility and raises
    (AttributeError: '...' object has no attribute '_sa_instance_state', or
    'InstanceState' object has no attribute 'obj').

    These are already persistent, identity-mapped objects, so there is no
    good reason to actually duplicate one: returning `self` from
    __deepcopy__ sidesteps the incompatibility entirely and is semantically
    fine here (nothing in this app relies on Streamlit's before/after value
    comparison for these widgets - none of them use on_change=).
    """

    def __deepcopy__(self, memo):
        return self


Base = declarative_base(cls=_NoDeepCopyMixin)


def _database_url() -> str:
    # 1. Streamlit secrets (Streamlit Cloud deployment)
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass
    # 2. Environment variable (local / CI)
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    # 3. Local fallback - SQLite, dev only
    return "sqlite:///pi3_local.db"


ENGINE = create_engine(_database_url(), pool_pre_ping=True)
# expire_on_commit=False: keep already-loaded attributes readable after a
# commit, since the session below is reused across Streamlit reruns rather
# than recreated each time.
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Confidence / status vocabularies (shared across entities)
# ---------------------------------------------------------------------------
CONFIDENCE_LEVELS = ["Confirmed", "Likely", "Unconfirmed", "Rejected"]
APPROVAL_STATUSES = ["Draft", "Pending Review", "Approved", "Rejected"]
TRIAL_STATUSES = ["Open", "Pending Closure", "Closed"]
INSTALLATION_TYPES = ["Single Plant", "Multi-Plant", "Enterprise / Group"]

# Process-data capture vocabularies (Mandatory-tier taxonomy, see
# "Expanding PI3 Plant Edition Production-Trial Data Capture" report).
PHASE_NAMES = [
    "Pre-run / Calibration",
    "Start-up",
    "Stabilization",
    "Steady-state",
    "Adjustment / Grade change",
    "Shutdown",
]
EVENT_TYPES = [
    "Alarm",
    "Intervention",
    "Grade Change",
    "Planned Pause",
    "Unplanned Pause",
    "Other",
]
SEVERITIES = ["Low", "Medium", "High"]
ZONE_LABELS = [
    "Head-Left-Top", "Head-Center-Top", "Head-Right-Top",
    "Middle-Left-Top", "Middle-Center-Top", "Middle-Right-Top",
    "Tail-Left-Top", "Tail-Center-Top", "Tail-Right-Top",
    "Head-Center-Middle", "Middle-Center-Middle", "Tail-Center-Middle",
    "Head-Center-Bottom", "Middle-Center-Bottom", "Tail-Center-Bottom",
    "Other",
]


# ---------------------------------------------------------------------------
# 1. plants
# ---------------------------------------------------------------------------
class Plant(Base):
    __tablename__ = "plants"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    plant_code = Column(String(50))
    location = Column(String(200))
    active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    product_families = relationship("ProductFamily", back_populates="plant")
    maintenance_records = relationship("MaintenanceLicenseRecord", back_populates="plant")
    pi3_ai_settings = relationship("PI3AIConnectionSetting", back_populates="plant")


# ---------------------------------------------------------------------------
# 2. product_families
# ---------------------------------------------------------------------------
class ProductFamily(Base):
    __tablename__ = "product_families"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    name = Column(String(200), nullable=False)
    application = Column(String(200))
    customer_segment = Column(String(200))
    description = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    plant = relationship("Plant", back_populates="product_families")
    foam_grades = relationship("FoamGrade", back_populates="product_family")


# ---------------------------------------------------------------------------
# 3. foam_grades
# ---------------------------------------------------------------------------
class FoamGrade(Base):
    __tablename__ = "foam_grades"

    id = Column(Integer, primary_key=True)
    product_family_id = Column(Integer, ForeignKey("product_families.id"), nullable=False)
    grade_name = Column(String(200), nullable=False)
    target_density = Column(Float)
    target_hardness = Column(Float)
    quality_specification = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    product_family = relationship("ProductFamily", back_populates="foam_grades")
    recipe_versions = relationship("RecipeVersion", back_populates="foam_grade")


# ---------------------------------------------------------------------------
# 4. recipe_versions
# ---------------------------------------------------------------------------
class RecipeVersion(Base):
    __tablename__ = "recipe_versions"

    id = Column(Integer, primary_key=True)
    foam_grade_id = Column(Integer, ForeignKey("foam_grades.id"), nullable=False)
    version_label = Column(String(100), nullable=False)
    effective_date = Column(Date)
    change_note = Column(Text)
    approval_status = Column(String(50), default="Draft")
    created_by = Column(String(200))
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    foam_grade = relationship("FoamGrade", back_populates="recipe_versions")
    components = relationship("RecipeComponent", back_populates="recipe_version")
    production_runs = relationship("ProductionRun", back_populates="recipe_version")


# ---------------------------------------------------------------------------
# 5. recipe_components
# ---------------------------------------------------------------------------
class RecipeComponent(Base):
    __tablename__ = "recipe_components"

    id = Column(Integer, primary_key=True)
    recipe_version_id = Column(Integer, ForeignKey("recipe_versions.id"), nullable=False)
    raw_material_name = Column(String(200), nullable=False)
    supplier = Column(String(200))
    php = Column(Float)  # parts per hundred (polyol)
    role_in_formulation = Column(String(200))
    notes = Column(Text)

    recipe_version = relationship("RecipeVersion", back_populates="components")


# ---------------------------------------------------------------------------
# 6. production_runs
# ---------------------------------------------------------------------------
class ProductionRun(Base):
    __tablename__ = "production_runs"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    foam_grade_id = Column(Integer, ForeignKey("foam_grades.id"), nullable=False)
    recipe_version_id = Column(Integer, ForeignKey("recipe_versions.id"), nullable=False)
    run_date = Column(Date)
    batch_reference = Column(String(200))
    block_reference = Column(String(200))
    machine_id = Column(String(200))
    operator_or_team_reference = Column(String(200))
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    plant = relationship("Plant")
    foam_grade = relationship("FoamGrade")
    recipe_version = relationship("RecipeVersion", back_populates="production_runs")
    runtime_records = relationship("RuntimeDataRecord", back_populates="production_run")
    trial_records = relationship("TrialRecord", back_populates="production_run")
    # Note: phases/events/lot_uses/samples are deliberately NOT exposed as
    # back-populated collections here. All page code queries those tables
    # directly by production_run_id instead of via a run.phases-style
    # relationship. Adding a bidirectional collection here made ProductionRun
    # (and therefore any FoamGrade/ProductFamily selectbox reachable via
    # RecipeVersion.production_runs) carry a live, non-empty backref
    # collection once rows existed - and Streamlit's widget-state tracking
    # deepcopies selectbox option objects, which crashes on SQLAlchemy
    # InstrumentedList backref collections (AttributeError: '...' object has
    # no attribute '_sa_instance_state'). Keeping these one-directional
    # (see production_run = relationship(...) on each child model below)
    # avoids that entirely.


# ---------------------------------------------------------------------------
# 6b. production_phases (Mandatory-tier: phase timestamps + machine settings)
# ---------------------------------------------------------------------------
class ProductionPhase(Base):
    __tablename__ = "production_phases"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    phase_name = Column(String(50), nullable=False)
    phase_start = Column(DateTime)
    phase_end = Column(DateTime)
    is_steady_state = Column(Boolean, default=False)

    # Machine-level settings for this phase (setpoint / actual pattern).
    mixer_rpm_setpoint = Column(Float)
    mixer_rpm_actual_mean = Column(Float)
    conveyor_speed_setpoint = Column(Float)  # m/min
    conveyor_speed_actual_mean = Column(Float)  # m/min
    air_injection_rate = Column(Float)  # NL/min or % command
    air_pressure_bar = Column(Float)
    laydown_mode = Column(String(100))  # trough / fall-plate / liquid laydown / traversing / direct
    section_positions_note = Column(Text)  # free-text geometry note (fall-plate section heights, etc.)
    sidewall_width_mm = Column(Float)
    foam_height_actual_mean_mm = Column(Float)

    notes = Column(Text)
    source_file_reference = Column(String(300))  # "manual entry" or CSV filename
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    production_run = relationship("ProductionRun")


# ---------------------------------------------------------------------------
# 6c. component_stream_readings (Mandatory-tier: per raw-material stream)
# ---------------------------------------------------------------------------
class ComponentStreamReading(Base):
    __tablename__ = "component_stream_readings"

    id = Column(Integer, primary_key=True)
    production_phase_id = Column(Integer, ForeignKey("production_phases.id"), nullable=False)
    stream_name = Column(String(200), nullable=False)  # e.g. Polyol A, TDI 80/20, Water blend, Catalyst
    flow_unit = Column(String(20), default="kg/min")
    flow_setpoint = Column(Float)
    flow_actual_mean = Column(Float)
    flow_actual_min = Column(Float)
    flow_actual_max = Column(Float)
    flow_actual_sd = Column(Float)
    pressure_actual_mean_bar = Column(Float)
    temperature_setpoint_c = Column(Float)
    temperature_actual_mean_c = Column(Float)
    notes = Column(Text)
    source_file_reference = Column(String(300))

    phase = relationship("ProductionPhase")


# ---------------------------------------------------------------------------
# 6d. production_events (Mandatory-tier: alarms / interventions / grade changes)
# ---------------------------------------------------------------------------
class ProductionEvent(Base):
    __tablename__ = "production_events"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    production_phase_id = Column(Integer, ForeignKey("production_phases.id"))
    event_ts = Column(DateTime, nullable=False)
    event_type = Column(String(50), nullable=False)
    severity = Column(String(20))
    description = Column(Text)
    action_taken = Column(Text)
    source_file_reference = Column(String(300))

    production_run = relationship("ProductionRun")
    phase = relationship("ProductionPhase")


# ---------------------------------------------------------------------------
# 6e. raw_material_lot_uses (Mandatory-tier: supplier lot actually consumed)
# ---------------------------------------------------------------------------
class RawMaterialLotUse(Base):
    __tablename__ = "raw_material_lot_uses"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    component_stream_name = Column(String(200), nullable=False)
    supplier_lot_no = Column(String(200), nullable=False)
    notes = Column(Text)
    source_file_reference = Column(String(300))

    production_run = relationship("ProductionRun")


# ---------------------------------------------------------------------------
# 6f. samples (Mandatory-tier: sample-to-lab traceability backbone)
# ---------------------------------------------------------------------------
class Sample(Base):
    __tablename__ = "samples"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    sample_ts = Column(DateTime)
    zone_label = Column(String(50))
    x_mm = Column(Float)
    y_mm = Column(Float)
    z_mm = Column(Float)
    cure_age_hours = Column(Float)
    notes = Column(Text)

    production_run = relationship("ProductionRun")


# ---------------------------------------------------------------------------
# 6g. conditioning_segments (Mandatory-tier: conditioning history per sample)
# ---------------------------------------------------------------------------
class ConditioningSegment(Base):
    __tablename__ = "conditioning_segments"

    id = Column(Integer, primary_key=True)
    sample_id = Column(Integer, ForeignKey("samples.id"), nullable=False)
    condition_type = Column(String(200))  # e.g. "Standard 23C/50%RH", "Ambient plant floor"
    temperature_c = Column(Float)
    relative_humidity_pct = Column(Float)
    segment_start = Column(DateTime)
    segment_end = Column(DateTime)
    notes = Column(Text)

    sample = relationship("Sample")


# ---------------------------------------------------------------------------
# 7. runtime_data_records
# ---------------------------------------------------------------------------
class RuntimeDataRecord(Base):
    __tablename__ = "runtime_data_records"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    line_speed = Column(Float)
    pump_speed_or_flow_data = Column(String(200))
    temperature_data = Column(String(200))
    pressure_data = Column(String(200))
    ambient_temperature = Column(Float)
    ambient_humidity = Column(Float)
    rise_time = Column(Float)
    curing_notes = Column(Text)
    source_file_reference = Column(String(300))
    imported_at = Column(DateTime, default=dt.datetime.utcnow)

    production_run = relationship("ProductionRun", back_populates="runtime_records")


# ---------------------------------------------------------------------------
# 8. trial_records
# ---------------------------------------------------------------------------
class TrialRecord(Base):
    __tablename__ = "trial_records"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)

    # objective / setup
    trial_or_change_objective = Column(Text, nullable=False)
    hypothesis = Column(Text)
    what_changed = Column(Text)
    responsible_person = Column(String(200))
    status = Column(String(50), default="Open")  # Open / Pending Closure / Closed

    # closeout fields - ALL required before status can become "Closed"
    result_against_target = Column(Text)
    physical_property_outcome = Column(Text)
    conclusion = Column(Text)
    reuse_recommendation = Column(Text)
    reviewed_by = Column(String(200))
    approved_by = Column(String(200))
    date_closed = Column(Date)

    created_at = Column(DateTime, default=dt.datetime.utcnow)

    production_run = relationship("ProductionRun", back_populates="trial_records")
    quality_observations = relationship("QualityObservation", back_populates="trial_record")
    physical_property_results = relationship("PhysicalPropertyResult", back_populates="trial_record")
    adjustment_conclusions = relationship("AdjustmentConclusion", back_populates="trial_record")
    approval_records = relationship("ApprovalRecord", back_populates="trial_record")

    REQUIRED_CLOSEOUT_FIELDS = [
        "conclusion",
        "reuse_recommendation",
        "reviewed_by",
        "approved_by",
        "date_closed",
    ]

    def missing_closeout_fields(self):
        missing = []
        for field in self.REQUIRED_CLOSEOUT_FIELDS:
            if not getattr(self, field):
                missing.append(field)
        return missing

    def can_close(self):
        return len(self.missing_closeout_fields()) == 0


# ---------------------------------------------------------------------------
# 9. physical_property_results
# ---------------------------------------------------------------------------
class PhysicalPropertyResult(Base):
    __tablename__ = "physical_property_results"

    id = Column(Integer, primary_key=True)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("samples.id"))  # nullable: older rows predate sample tracking
    property_name = Column(String(100), nullable=False)  # density, hardness, tensile, elongation, compression_set, airflow
    target_value = Column(Float)
    actual_value = Column(Float)
    unit = Column(String(50))
    pass_fail = Column(String(20))  # Pass / Fail
    test_method = Column(String(200))
    method_revision = Column(String(50))
    replicate_no = Column(Integer)
    tested_at = Column(Date)

    trial_record = relationship("TrialRecord", back_populates="physical_property_results")
    sample = relationship("Sample")


# ---------------------------------------------------------------------------
# 10. quality_observations  (NOT "defects" - approved terminology)
# ---------------------------------------------------------------------------
class QualityObservation(Base):
    __tablename__ = "quality_observations"

    id = Column(Integer, primary_key=True)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"), nullable=False)
    observation_type = Column(String(200), nullable=False)  # e.g. shrinkage, hardness drift, collapse, splitting
    severity = Column(String(50))  # Low / Medium / High
    frequency = Column(String(50))  # One-off / Recurring
    location_in_block = Column(String(200))
    suspected_cause = Column(Text)
    confidence_level = Column(String(50), default="Unconfirmed")
    product_impact = Column(Text)
    customer_impact = Column(Text)
    notes = Column(Text)
    observed_at = Column(Date)

    trial_record = relationship("TrialRecord", back_populates="quality_observations")


# ---------------------------------------------------------------------------
# 11. adjustment_conclusions  (NOT "corrective actions" - approved terminology)
# ---------------------------------------------------------------------------
class AdjustmentConclusion(Base):
    __tablename__ = "adjustment_conclusions"

    id = Column(Integer, primary_key=True)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"), nullable=False)
    parameter_changed = Column(String(200))
    formulation_changed = Column(Boolean, default=False)
    material_changed = Column(String(200))
    result = Column(Text)
    reuse_recommendation = Column(Text)
    confidence_level = Column(String(50), default="Unconfirmed")
    follow_up_required = Column(Boolean, default=False)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    trial_record = relationship("TrialRecord", back_populates="adjustment_conclusions")


# ---------------------------------------------------------------------------
# 12. approval_records
# ---------------------------------------------------------------------------
class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(Integer, primary_key=True)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"), nullable=False)
    reviewed_by = Column(String(200))
    approved_by = Column(String(200))
    approval_status = Column(String(50), default="Pending Review")
    review_notes = Column(Text)
    date_reviewed = Column(Date)
    date_approved = Column(Date)

    trial_record = relationship("TrialRecord", back_populates="approval_records")


# ---------------------------------------------------------------------------
# 13. expert_notes
# ---------------------------------------------------------------------------
class ExpertNote(Base):
    __tablename__ = "expert_notes"

    id = Column(Integer, primary_key=True)
    linked_entity_type = Column(String(100), nullable=False)  # e.g. "trial_record", "foam_grade"
    linked_entity_id = Column(Integer, nullable=False)
    note_text = Column(Text, nullable=False)
    confidence_level = Column(String(50), default="Unconfirmed")
    author = Column(String(200))
    created_at = Column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# 14. similar_case_links
# ---------------------------------------------------------------------------
class SimilarCaseLink(Base):
    __tablename__ = "similar_case_links"

    id = Column(Integer, primary_key=True)
    source_trial_id = Column(Integer, ForeignKey("trial_records.id"), nullable=False)
    linked_trial_id = Column(Integer, ForeignKey("trial_records.id"), nullable=False)
    similarity_basis = Column(String(200))  # product_family / foam_grade / observation_type / recipe_version
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# 15. pi3_ai_connection_settings
# ---------------------------------------------------------------------------
class PI3AIConnectionSetting(Base):
    __tablename__ = "pi3_ai_connection_settings"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    pi3_ai_connectivity_enabled = Column(Boolean, default=False)
    pi3_ai_status = Column(String(50), default="Disabled")
    pi3_ai_annual_fee = Column(Float)
    enabled_by = Column(String(200))
    enabled_at = Column(DateTime)

    plant = relationship("Plant", back_populates="pi3_ai_settings")


# ---------------------------------------------------------------------------
# 16. maintenance_and_license_records
# ---------------------------------------------------------------------------
class MaintenanceLicenseRecord(Base):
    __tablename__ = "maintenance_and_license_records"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    plant_count = Column(Integer, default=1)
    installation_type = Column(String(100), default="Single Plant")
    deployment_type = Column(String(100))
    license_value = Column(Float)
    annual_maintenance_percentage = Column(Float, default=18.0)
    annual_maintenance_value = Column(Float)
    maintenance_start_date = Column(Date)
    renewal_date = Column(Date)

    plant = relationship("Plant", back_populates="maintenance_records")


ALL_MODELS = [
    Plant,
    ProductFamily,
    FoamGrade,
    RecipeVersion,
    RecipeComponent,
    ProductionRun,
    ProductionPhase,
    ComponentStreamReading,
    ProductionEvent,
    RawMaterialLotUse,
    Sample,
    ConditioningSegment,
    RuntimeDataRecord,
    TrialRecord,
    PhysicalPropertyResult,
    QualityObservation,
    AdjustmentConclusion,
    ApprovalRecord,
    ExpertNote,
    SimilarCaseLink,
    PI3AIConnectionSetting,
    MaintenanceLicenseRecord,
]


def init_db():
    """Create all tables if they do not already exist. Safe to call on every app start."""
    Base.metadata.create_all(bind=ENGINE)


def get_session():
    """Return a SQLAlchemy session that persists for the lifetime of the
    Streamlit browser session (via st.session_state), rather than a fresh
    session on every script rerun.

    Streamlit widgets (e.g. st.selectbox) can hold onto ORM objects across
    reruns. If each rerun created a brand-new session, the session backing
    an object selected in an earlier rerun would already be gone, and
    accessing a not-yet-loaded (lazy) relationship on it would raise
    sqlalchemy.orm.exc.DetachedInstanceError. Reusing one session per
    browser session keeps those objects attached and loadable.
    """
    if "_sa_session" not in st.session_state:
        st.session_state["_sa_session"] = SessionLocal()
    return st.session_state["_sa_session"]
