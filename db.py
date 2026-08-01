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
    UniqueConstraint,
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


ENGINE = create_engine(_database_url(), pool_pre_ping=True, pool_recycle=280)
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
# Process-data capture vocabularies (Mandatory-tier taxonomy, see
# "Expanding PI3 Plant Edition Production-Trial Data Capture" report).
# Limited to two snapshots deliberately: without a live PLC/OPC UA/MQTT link
# or a machine data export/import, there is no honest way to capture the
# in-between phases (start-up, stabilization, steady-state, adjustment) as
# anything more than guesses. "Setup" is what was planned/configured before
# or at the start of the run; "Finalized" is what was actually used, entered
# at shutdown/completion. Recording the same fields at both points gives the
# plan-vs-actual comparison for free, without needing a separate setpoint
# column next to every actual column.
PHASE_NAMES = [
    "Setup",
    "Finalized",
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

# Most common conditioning situations for flexible PU foam testing, per
# ISO 291 (standard atmospheres) and ASTM D3574 conditioning practice.
# Each maps to a suggested (temperature_c, relative_humidity_pct) default -
# these prefill the numeric fields but are always editable, since the
# actual chamber reading is what matters, not the nominal condition name.
CONDITIONING_TYPE_DEFAULTS = {
    "Standard 23°C / 50% RH": (23.0, 50.0),
    "Ambient / plant floor (uncontrolled)": (None, None),
    "Dry heat aging 70°C": (70.0, None),
    "Dry heat aging 100°C": (100.0, None),
    "Humid aging 50°C / 95% RH": (50.0, 95.0),
    "Low temperature -20°C": (-20.0, None),
    "Low temperature -40°C": (-40.0, None),
    "Other (specify)": (None, None),
}
CONDITIONING_TYPES = list(CONDITIONING_TYPE_DEFAULTS.keys())

RAW_MATERIAL_CATEGORIES = [
    "Polyol",
    "Isocyanate",
    "Blowing agent",
    "Catalyst",
    "Surfactant",
    "Flame retardant",
    "Colorant / Pigment",
    "Cross-linker / Chain extender",
    "Filler",
    "Additive",
    "Other",
]

ZONE_LABELS = ["Top", "Middle", "Bottom"]


# ---------------------------------------------------------------------------
# 0. subscription_types / companies / roles / role_page_permissions / users
#
# Multi-tenant access control. A Company is the tenant boundary: it owns a
# subscription (which caps user/plant counts and gates whole feature areas)
# and its own users. Data isolation is "shared database, company_id column"
# rather than one database per customer - plants, raw_materials, and
# suppliers each carry a company_id (everything else already hangs off
# plant_id through the existing hierarchy, so scoping the plant list per
# company scopes everything under it).
#
# Roles are a real table, not a hardcoded list, so the platform owner can
# define any number of default role templates beyond the original three
# (admin/technical/viewer, company_id NULL - cloned into every new company,
# see role_provisioning.py), and a company can define its own custom roles
# on top of its clones.
#
# Per-role, per-page access is a DENY list, not an allow list: a role with
# no RolePagePermission rows has full access to every page (matches every
# role's behavior before this per-page split existed, and needs no rows
# seeded for the common case). Each row can deny in one of two ways:
#   - can_view=False: the page is hidden entirely (nav + direct access).
#   - can_view=True, can_use=False: the page is visible and its data can be
#     read, but its Add/Edit/Delete forms and any action buttons (CSV
#     import, "Ask PI3", approvals, downloads, ...) are hidden - a genuine
#     read-only view, not just a suggestion. can_use=True with no row is
#     the default ("full access"); can_use=True is never combined with
#     can_view=False (using implies being able to view) - the admin UI only
#     ever offers three states (Hidden / View only / Full access) to avoid
#     that nonsensical combination, see access_control.py.
# As of this schema version the MODEL supports view-vs-use everywhere, but
# individual operational pages opting in to actually hiding their own
# write controls when can_use=False is a page-by-page rollout, not yet
# complete for every page - see access_control.py's module docstring for
# which pages currently check it.
# ---------------------------------------------------------------------------
class SubscriptionType(Base):
    __tablename__ = "subscription_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    max_users = Column(Integer)  # NULL = unlimited
    max_plants = Column(Integer)  # NULL = unlimited
    pi3_ai_enabled = Column(Boolean, default=True)  # PI3 Connectivity page - the one real feature differentiator between HTC's two tiers, see access_control.py
    reports_enabled = Column(Boolean, default=True)  # Report page
    # Each subscription type row is now a single fixed billing frequency
    # (2026-08-01 restructure) - "PI3 Plant Edition" and "PI3 Plant Edition
    # - Basic" each became two rows (an "- Annual" one and a "- Monthly"
    # one) instead of one row holding both prices and Company picking which
    # applies. This makes a company's fee AND billing frequency both come
    # from the single subscription_type_id it's assigned - no separate
    # Company.billing_frequency field to keep in sync (removed - see
    # Company docstring), and each frequency's price can be changed
    # independently (e.g. a monthly-only price bump) without touching the
    # other. billing_frequency is "Annual" or "Monthly".
    billing_frequency = Column(String(20), default="Annual")
    price = Column(Float)  # USD/plant, per billing_frequency above
    price_note = Column(String(200))  # free text for anything not captured above (e.g. one-time implementation fee) - no payment processing wired up
    active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    # The formal registered entity name, if different from the (often
    # shorter/trading) name above - e.g. name="Acme Foams", legal_entity_name
    # ="Acme Foam Industries Pte Ltd". Optional: left blank, `name` is used.
    legal_entity_name = Column(String(300))
    vat_number = Column(String(50))
    address = Column(String(300))
    city = Column(String(100))
    postal_code = Column(String(20))
    country = Column(String(100))
    subscription_type_id = Column(Integer, ForeignKey("subscription_types.id"))
    # True only for HTC itself: grants cross-company superadmin scope (see
    # every company, manage subscription types/companies, unrestricted by
    # any single company's plant/user limits).
    is_platform_owner = Column(Boolean, default=False)
    contact_name = Column(String(200))
    contact_email = Column(String(200))
    contact_phone = Column(String(50))
    # billing_frequency used to live here (a separate Annual/Monthly picker
    # a company chose independently of its subscription_type_id). Removed
    # 2026-08-01: SubscriptionType itself is now split one row per
    # frequency (e.g. "PI3 Plant Edition - Annual" vs "- Monthly"), so a
    # company's billing frequency is simply whichever tier row it's
    # assigned to - see SubscriptionType.billing_frequency. One field to
    # pick instead of two that could disagree.
    active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    subscription_type = relationship("SubscriptionType")


class Role(Base):
    """company_id NULL + is_builtin True is a *template* row (exactly 3:
    admin/technical/viewer) - never assigned to a User directly, and never
    shown outside the Default User Roles page (platform-owner-only). Every
    real company gets its own company_id-scoped CLONE of those 3 roles,
    seeded from the templates at company-creation time (see
    role_provisioning.clone_builtin_roles_for_company) - that clone is what
    Users actually get assigned to, and what a company's own admin narrows
    on the User Roles page. This exists because RolePagePermission is keyed
    by role_id alone: if built-in roles stayed a single shared row per
    company, one company narrowing "viewer" would silently narrow every
    other company's viewer role too (a real cross-tenant leak, caught and
    fixed 2026-07-31 before any second real customer existed to be bitten
    by it). Non-builtin (custom) roles are always company_id-scoped from
    creation and were never affected by this - "shared across every
    company" custom roles are deliberately not offered in the UI anymore,
    for the same reason."""

    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"))
    name = Column(String(100), nullable=False)
    description = Column(Text)
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class RolePagePermission(Base):
    """A row only ever exists to deny something - see the module docstring
    above the Role class for the full Hidden / View only / Full access
    semantics. can_view=False hides the page outright; can_view=True with
    can_use=False is the new (2026-07-31) view-only state."""

    __tablename__ = "role_page_permissions"

    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    page_key = Column(String(100), nullable=False)  # see access_control.PAGE_CATALOG
    can_view = Column(Boolean, default=False)
    can_use = Column(Boolean, default=True)  # False = read-only for this page (only meaningful when can_view is True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    username = Column(String(100), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(200))
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    active = Column(Boolean, default=True)
    valid_from = Column(Date)  # NULL = no start restriction
    valid_until = Column(Date)  # NULL = indefinite
    last_login_at = Column(DateTime)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    company = relationship("Company")
    role = relationship("Role")


# ---------------------------------------------------------------------------
# 1. plants
# ---------------------------------------------------------------------------
class Plant(Base):
    __tablename__ = "plants"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"))
    name = Column(String(200), nullable=False)
    plant_code = Column(String(50))
    location = Column(String(200))
    active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    company = relationship("Company")
    product_families = relationship("ProductFamily", back_populates="plant")
    pi3_ai_settings = relationship("PI3AIConnectionSetting", back_populates="plant")


# ---------------------------------------------------------------------------
# 1b. machines (foaming lines) - basic identity, one plant has many machines
#
# Lets process parameters on a production run connect to the actual
# equipment that produced them (OEM vocabulary differs - Laader Berg,
# Hennecke, Cannon, etc. - but PI3 stores the machine-neutral identity here;
# capability/limit fields such as rated conveyor speed or sidewall range can
# be added later without disrupting this).
# ---------------------------------------------------------------------------
MACHINE_OEMS = ["Laader Berg", "Hennecke", "Cannon", "Other"]


class Machine(Base):
    __tablename__ = "machines"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    name = Column(String(200), nullable=False)  # e.g. "Line 1", "Maxfoam A"
    machine_code = Column(String(50))
    oem = Column(String(50))  # Laader Berg / Hennecke / Cannon / Other
    model = Column(String(200))
    active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    plant = relationship("Plant")


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
#
# target_density/target_hardness are dedicated columns rather than entries in
# foam_grade_target_properties below because every grade has them and the
# grade-naming code itself encodes them (e.g. "28170" = 28 kg/m3 density,
# 170 N hardness at 40% ILD) - see grade_name. quality_specification (a
# free-text field) was removed: it only ever restated density/hardness in
# prose and had no other use. Any *other* physical property a grade needs to
# hit (resilience, tensile strength, ...) - optional, and often not yet
# measured/decided - goes in foam_grade_target_properties instead.
# ---------------------------------------------------------------------------
class FoamGrade(Base):
    __tablename__ = "foam_grades"

    id = Column(Integer, primary_key=True)
    product_family_id = Column(Integer, ForeignKey("product_families.id"), nullable=False)
    grade_name = Column(String(200), nullable=False)
    target_density = Column(Float)
    target_hardness = Column(Float)  # Newtons, 40% ILD
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    product_family = relationship("ProductFamily", back_populates="foam_grades")
    recipe_versions = relationship("RecipeVersion", back_populates="foam_grade")
    target_properties = relationship(
        "FoamGradeTargetProperty", back_populates="foam_grade", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 3b. foam_grade_target_properties
#
# Optional additional target specs for a foam grade beyond density/hardness
# (resilience, tensile strength, compression set, ...), reusing the same
# physical_property_definitions master list as physical_property_results so
# names/units stay consistent app-wide. target_value is nullable on purpose:
# a property can be listed as something this grade needs to meet before the
# actual number is known/agreed.
# ---------------------------------------------------------------------------
class FoamGradeTargetProperty(Base):
    __tablename__ = "foam_grade_target_properties"

    id = Column(Integer, primary_key=True)
    foam_grade_id = Column(Integer, ForeignKey("foam_grades.id"), nullable=False)
    property_definition_id = Column(Integer, ForeignKey("physical_property_definitions.id"))
    property_name = Column(String(200), nullable=False)  # snapshot text, auto-filled from the chosen definition
    target_value = Column(Float)
    unit = Column(String(50))
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    foam_grade = relationship("FoamGrade", back_populates="target_properties")


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
    # Separate from approval_status on purpose: approval_status tracks the
    # Draft/Review/Approved/Rejected workflow for THIS version; is_active
    # tracks whether it's the version currently in production use for its
    # foam grade. A version can be Approved but no longer active (it was
    # superseded by a later revision) - only one version per foam grade
    # should be active at a time, enforced in application code (see
    # helpers.activate_recipe_version), not a DB constraint.
    is_active = Column(Boolean, default=True)

    foam_grade = relationship("FoamGrade", back_populates="recipe_versions")
    components = relationship("RecipeComponent", back_populates="recipe_version")
    production_runs = relationship("ProductionRun", back_populates="recipe_version")


# ---------------------------------------------------------------------------
# Suppliers (master data)
#
# A short, curated list of supplier names so RawMaterial.default_supplier can
# be picked from a dropdown instead of retyped (and mistyped/duplicated -
# "Yiahua" vs "Jiahua") every time. Deliberately just a name + free-text
# notes: this is a lookup list for data entry, not a full vendor-management
# record (no address/contact fields - add those later only if a real need
# shows up).
# ---------------------------------------------------------------------------
class Supplier(Base):
    __tablename__ = "suppliers"
    # Uniqueness is scoped per company, not global - two different customer
    # companies can each have their own "BASF" entry without colliding.
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_supplier_company_name"),)

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"))
    name = Column(String(200), nullable=False)
    notes = Column(Text)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    company = relationship("Company")


# ---------------------------------------------------------------------------
# Raw materials (master data)
# ---------------------------------------------------------------------------
class RawMaterial(Base):
    """Master list of raw materials, so recipes can be built from a dropdown
    instead of retyping (and mistyping) the same material name every time.

    raw_material_name stays on RecipeComponent as the field of record (it is
    what every existing page/report reads), but recipe components now also
    carry raw_material_id so the same material can be traced/reported on
    across every recipe that uses it. A component can still name a material
    that isn't in this master list yet (free-text override), matching the
    same dropdown-plus-custom-entry pattern used for streams and
    conditioning types elsewhere in the app.
    """

    __tablename__ = "raw_materials"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"))
    name = Column(String(200), nullable=False)
    category = Column(String(100))
    default_supplier = Column(String(200))
    cost_per_kg = Column(Float)
    notes = Column(Text)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    company = relationship("Company")


# ---------------------------------------------------------------------------
# 5. recipe_components
# ---------------------------------------------------------------------------
class RecipeComponent(Base):
    __tablename__ = "recipe_components"

    id = Column(Integer, primary_key=True)
    recipe_version_id = Column(Integer, ForeignKey("recipe_versions.id"), nullable=False)
    raw_material_id = Column(Integer, ForeignKey("raw_materials.id"))
    raw_material_name = Column(String(200), nullable=False)
    supplier = Column(String(200))
    php = Column(Float)  # parts per hundred (polyol)
    role_in_formulation = Column(String(200))
    notes = Column(Text)

    recipe_version = relationship("RecipeVersion", back_populates="components")
    raw_material = relationship("RawMaterial")


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
    machine_id = Column(Integer, ForeignKey("machines.id"))  # which foaming line actually ran this
    operator_or_team_reference = Column(String(200))
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    plant = relationship("Plant")
    foam_grade = relationship("FoamGrade")
    machine = relationship("Machine")
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
# 6b. production_phases (two snapshots: Setup = planned, Finalized = actual)
#
# Each machine-setting field is recorded once per phase row. Because there
# are only two phases, comparing the Setup row to the Finalized row for the
# same production run IS the setpoint-vs-actual comparison - no separate
# _setpoint/_actual column pair needed on top of that.
# ---------------------------------------------------------------------------
class ProductionPhase(Base):
    __tablename__ = "production_phases"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    phase_name = Column(String(50), nullable=False)  # "Setup" or "Finalized"
    phase_start = Column(DateTime)
    phase_end = Column(DateTime)

    # Machine-level settings for this phase.
    mixer_rpm = Column(Float)
    conveyor_speed = Column(Float)  # m/min
    air_injection_rate = Column(Float)  # NL/min or % command
    air_pressure_bar = Column(Float)
    laydown_mode = Column(String(100))  # trough / fall-plate / liquid laydown / traversing / direct
    section_positions_note = Column(Text)  # free-text for geometry not covered by structured fall-plate rows below
    sidewall_width_mm = Column(Float)
    foam_height_mm = Column(Float)

    # Stoichiometric ratio/index for this phase - the report's single
    # highest-value diagnostic field (explains density/compression/cure
    # drift better than any individual stream reading). Compare the Setup
    # row's value to the Finalized row's value for the plan-vs-actual read.
    ratio_index = Column(Float)

    notes = Column(Text)
    source_file_reference = Column(String(300))  # "manual entry" or CSV filename
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    production_run = relationship("ProductionRun")


# ---------------------------------------------------------------------------
# 6c. component_stream_readings (per raw-material stream, per Setup/Finalized phase)
# ---------------------------------------------------------------------------
class ComponentStreamReading(Base):
    __tablename__ = "component_stream_readings"

    id = Column(Integer, primary_key=True)
    production_phase_id = Column(Integer, ForeignKey("production_phases.id"), nullable=False)
    stream_name = Column(String(200), nullable=False)  # e.g. Polyol A, TDI 80/20, Water blend, Catalyst
    flow_unit = Column(String(20), default="kg/min")
    flow = Column(Float)
    pump_speed = Column(Float)  # metering pump setting for this stream (RPM/Hz/% depending on OEM) - the
    # control input, distinct from flow (the resulting/measured output). Every chemical line has its own
    # pump, so this lives per stream reading, not as a single Runtime Data field.
    flow_total_qty = Column(Float)  # total delivered this phase - same base unit as flow_unit (kg or L, not per-minute)
    pressure_bar = Column(Float)
    temperature_c = Column(Float)
    calibration_status = Column(String(50))  # Valid / Expired / Failed / Not Verified
    calibration_note = Column(Text)
    notes = Column(Text)
    source_file_reference = Column(String(300))

    phase = relationship("ProductionPhase")


# ---------------------------------------------------------------------------
# 6h. fallplate_section_positions (structured laydown geometry per phase)
#
# Replaces free-text-only section_positions_note with actual mm/degree
# values per section, since fall-plate lines commonly have 4-6 independently
# positioned sections that materially affect density profile and bun
# squareness.
# ---------------------------------------------------------------------------
class FallplateSectionPosition(Base):
    __tablename__ = "fallplate_section_positions"

    id = Column(Integer, primary_key=True)
    production_phase_id = Column(Integer, ForeignKey("production_phases.id"), nullable=False)
    section_number = Column(Integer, nullable=False)
    position_mm = Column(Float)
    angle_deg = Column(Float)
    notes = Column(Text)

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
    zone_label = Column(String(50))  # Top / Middle / Bottom - deliberately just the vertical layer
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
#
# Deliberately NOT the mandatory container for routine production/quality
# data. A production run is a complete, self-sufficient record on its own
# (recipe + machine parameters + quality results). TrialRecord is an
# optional, secondary module you attach to a run only when it is genuinely
# a deliberate experiment/change investigation with a hypothesis and a
# formal closeout/approval requirement - most runs never touch this table.
# See PhysicalPropertyResult / QualityObservation / AdjustmentConclusion /
# ApprovalRecord below: they all key primarily off production_run_id, with
# trial_record_id as an optional cross-reference.
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
# 8b. physical_property_definitions / methods / uoms
#
# Master reference list (84 properties) supplied by the business as
# Flexible_PU_Foam_Physical_Properties_Master.xlsx. Each property can have
# several valid measuring-method standards (ISO/ASTM/etc. are alternatives,
# not interchangeable) and several valid units, hence the separate
# one-to-many reference tables rather than flat columns.
#
# No back-populated collections are defined here (methods/uoms are always
# queried directly by property_definition_id from page code) - see the
# _NoDeepCopyMixin note above and the ProductionRun/ProductionPhase
# precedent: a bidirectional collection here would make every
# PhysicalPropertyDefinition selectbox option carry a live, non-empty
# backref list once methods/uoms exist, which is exactly the shape that
# breaks Streamlit's widget-state deepcopy even with the mixin in place
# for *this* object - simplest to avoid the collection entirely.
# ---------------------------------------------------------------------------
class PhysicalPropertyDefinition(Base):
    __tablename__ = "physical_property_definitions"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    what_it_measures = Column(Text)
    category = Column(String(20))  # Comfort / Technical / Both
    is_common = Column(Boolean, default=False)
    sort_order = Column(Integer)


class PhysicalPropertyMethod(Base):
    __tablename__ = "physical_property_methods"

    id = Column(Integer, primary_key=True)
    property_definition_id = Column(Integer, ForeignKey("physical_property_definitions.id"), nullable=False)
    method_code = Column(String(300), nullable=False)  # e.g. "ASTM D3574 Test A"
    sort_order = Column(Integer)


class PhysicalPropertyUOM(Base):
    __tablename__ = "physical_property_uoms"

    id = Column(Integer, primary_key=True)
    property_definition_id = Column(Integer, ForeignKey("physical_property_definitions.id"), nullable=False)
    unit_label = Column(String(50), nullable=False)
    sort_order = Column(Integer)


# ---------------------------------------------------------------------------
# 9. physical_property_results
#
# Keyed primarily to the production run (every batch produces quality
# results, trial or not). trial_record_id is optional - set only when this
# result is part of a formal experiment's evidence trail.
# ---------------------------------------------------------------------------
class PhysicalPropertyResult(Base):
    __tablename__ = "physical_property_results"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"))  # optional: only for formal experiments
    sample_id = Column(Integer, ForeignKey("samples.id"))  # nullable: older rows predate sample tracking
    property_definition_id = Column(Integer, ForeignKey("physical_property_definitions.id"))  # nullable for legacy/"Other"
    property_method_id = Column(Integer, ForeignKey("physical_property_methods.id"))  # nullable
    property_name = Column(String(200), nullable=False)  # snapshot text, auto-filled from the chosen definition
    target_value = Column(Float)
    actual_value = Column(Float)
    unit = Column(String(50))
    pass_fail = Column(String(20))  # Pass / Fail
    test_method = Column(String(300))  # snapshot text, auto-filled from the chosen method
    method_revision = Column(String(50))
    replicate_no = Column(Integer)
    tested_at = Column(Date)
    notes = Column(Text)

    trial_record = relationship("TrialRecord", back_populates="physical_property_results")
    sample = relationship("Sample")
    production_run = relationship("ProductionRun")


# ---------------------------------------------------------------------------
# 10. quality_observations  (NOT "defects" - approved terminology)
#
# Keyed primarily to the production run; trial_record_id is optional.
# ---------------------------------------------------------------------------
class QualityObservation(Base):
    __tablename__ = "quality_observations"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"))  # optional: only for formal experiments
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
    production_run = relationship("ProductionRun")


# ---------------------------------------------------------------------------
# 11. adjustment_conclusions  (NOT "corrective actions" - approved terminology)
#
# This stays a trial-scoped closeout artifact in practice (it captures the
# deliberate change + result + reuse recommendation for a formal
# investigation), but also carries production_run_id directly for
# consistent querying alongside the rest of a run's quality data.
# ---------------------------------------------------------------------------
class AdjustmentConclusion(Base):
    __tablename__ = "adjustment_conclusions"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"))  # optional
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
    production_run = relationship("ProductionRun")


# ---------------------------------------------------------------------------
# 12. approval_records
#
# Also trial-scoped in practice (sign-off on a formal experiment's
# closeout), with production_run_id carried directly for consistency.
# ---------------------------------------------------------------------------
class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    trial_record_id = Column(Integer, ForeignKey("trial_records.id"))  # optional
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
    # OpenAI file id for this note's copy in the PI3/AI vector store (see
    # ai_assistant.py), so an edit/delete here can resync/remove that file
    # instead of leaving a stale copy searchable. Null if PI3/AI wasn't
    # enabled for the relevant plant when the note was saved.
    vector_store_file_id = Column(String(200))
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    # Provenance fields for notes captured via a "Save to Expert Notes"
    # button on a PI3 answer (see helpers.render_save_to_expert_notes_button)
    # rather than typed by hand. Kept on the same table/model as manual notes
    # - deliberately, since both are meant to be searchable side by side and
    # both get pushed into PI3's vector store the same way - but tagged so
    # the Expert Notes screen can show where each one came from and, for
    # PI3-sourced notes, regenerate the original Word report on demand.
    source = Column(String(20), default="Manual")  # "Manual" or "PI3"
    pi3_question = Column(Text)  # the question/label PI3 was answering, null for manual notes
    pi3_tool_log_json = Column(Text)  # JSON-serialized tool_log (free-form Ask PI3 only), null otherwise


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
ALL_MODELS = [
    Plant,
    Machine,
    ProductFamily,
    FoamGrade,
    Supplier,
    RawMaterial,
    RecipeVersion,
    RecipeComponent,
    ProductionRun,
    ProductionPhase,
    ComponentStreamReading,
    FallplateSectionPosition,
    ProductionEvent,
    RawMaterialLotUse,
    Sample,
    ConditioningSegment,
    RuntimeDataRecord,
    TrialRecord,
    PhysicalPropertyDefinition,
    PhysicalPropertyMethod,
    PhysicalPropertyUOM,
    PhysicalPropertyResult,
    QualityObservation,
    AdjustmentConclusion,
    ApprovalRecord,
    ExpertNote,
    SimilarCaseLink,
    PI3AIConnectionSetting,
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

    IMPORTANT - see close_out_session() below: reusing one session across
    reruns means every read this session does opens a transaction that, if
    never explicitly closed, stays open for as long as that browser tab's
    Streamlit session lives - not just for this rerun. app.py must call
    close_out_session() once, after routing to whichever page ran, on
    every single rerun. Do not call get_session() from anywhere that isn't
    already covered by that (e.g. a background job), without also arranging
    to close the transaction it opens.
    """
    if "_sa_session" not in st.session_state:
        st.session_state["_sa_session"] = SessionLocal()
    return st.session_state["_sa_session"]


def close_out_session():
    """Commit (or roll back, on failure) whatever transaction the page that
    just ran opened, so no Streamlit rerun ever ends with an open, idle
    transaction left sitting on the database.

    Why this exists: get_session() deliberately reuses ONE session per
    browser tab across every rerun (see its docstring), and every read
    (.query()/.get()/...) under SQLAlchemy's default autocommit=False opens
    a transaction. Pages that only display data - Trend Analysis, Recipe
    Optimization, Root-Cause Assistant, Machine Settings vs Physical
    Properties Correlation, Machine Settings Optimization, Demo Data
    Admin, and any read-only view
    of a page that also supports editing - never call session.commit()
    themselves, since they have nothing to save. Without this function,
    that transaction would sit "idle in transaction" - holding read locks
    on every table it queried - until some later rerun happened to submit a
    form, or forever, if the user only browses and then leaves the tab
    open or closes it.

    This is not a theoretical concern: exactly this happened in production
    - a single read-only page view left a transaction open for roughly 18
    hours, holding locks that blocked an unrelated schema migration until
    the stale connection was manually terminated.

    Safe to call unconditionally after every rerun: every place in this app
    that adds/edits/deletes already calls session.commit() itself within
    the same rerun the change happens in (see cascades.py's docstring - a
    whole master-data delete is deliberately one all-or-nothing
    transaction, committed once by the caller). So by the time this runs,
    there is never a "half-finished" change sitting uncommitted - this only
    ever closes out a transaction that was already left in a fully
    consistent state, whether that's a page's own prior commit or just the
    read-only queries a view-only page issued.
    """
    session = st.session_state.get("_sa_session")
    if session is None:
        return
    try:
        session.commit()
    except Exception:
        # If the underlying connection itself has gone bad (e.g. the server
        # killed it - idle-in-transaction timeout, a restart, ...), rollback()
        # can also fail. In that case don't leave this same broken Session
        # cached in st.session_state: every future rerun of this browser tab
        # would keep reusing it and keep failing the same way until the user
        # did a full page reload. Discard it instead so the next
        # get_session() call builds a fresh Session (and checks out a fresh,
        # pool_pre_ping-verified connection) on the very next rerun.
        try:
            session.rollback()
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            st.session_state.pop("_sa_session", None)
