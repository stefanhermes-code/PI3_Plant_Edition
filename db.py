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
import threading

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
    Several pages pass live ORM objects (Plant, FoamGrade, CustomerTrial, ...)
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

# How the foam is laid down onto the conveyor. Controlled vocabulary (was a
# free-text "laydown mode" field until 2026-08-03): LLD (liquid laydown
# device - pours directly), Trough, or Traverse (a moving/oscillating LLD
# head). Deliberately just these three - anything else is genuinely rare
# enough on the lines this app targets that it isn't worth an "Other" escape
# hatch yet.
FOAMING_MODES = ["LLD", "Trough", "Traverse"]

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

# Flexible-foam customer types - the controlled vocabulary behind the Customer
# Type dropdown on views/29_Customers.py.
#
# Source: PI3_Flexible_Foam_Customer_Types_Master.csv, supplied by Charlie on
# 18 Aug 2026 at Stefan's request. Held as a constant here rather than a
# database table, the same as RAW_MATERIAL_CATEGORIES above: it is a stable
# industry vocabulary, not tenant data, so every company sees the same list and
# nobody has to seed it per company.
#
# Order is the master's sort_order, which groups the types - the list is not
# alphabetical on purpose, so related types sit together in the dropdown.
#
# The master also carries customer_type_id and active_flag. The ids are kept
# below so a future revision can be diffed against the source file; active_flag
# is not, because every row in the delivered master is active - if a type ever
# needs retiring, drop it from this list and decide then what to do with the
# customers already carrying it (see _CUSTOMER_TYPE_UNKNOWN_SUFFIX handling on
# the Customers page, which already tolerates a stored value that is no longer
# offered).
CUSTOMER_TYPES = [
    # (master id, name, group)
    ("FFCT001", "Mattress and Bedding Manufacturer", "Comfort and Furniture"),
    ("FFCT002", "Upholstered Furniture Manufacturer", "Comfort and Furniture"),
    ("FFCT003", "Office Furniture and Seating Manufacturer", "Comfort and Furniture"),
    ("FFCT004", "Furniture Component Manufacturer", "Comfort and Furniture"),
    ("FFCT005", "Contract and Hospitality Furniture Manufacturer", "Comfort and Furniture"),
    ("FFCT006", "Automotive Seating Supplier", "Transportation"),
    ("FFCT007", "Automotive Interior and NVH Supplier", "Transportation"),
    ("FFCT008", "Commercial Vehicle Seating Manufacturer", "Transportation"),
    ("FFCT009", "Railway Seating and Interior Manufacturer", "Transportation"),
    ("FFCT010", "Aircraft Seating and Interior Manufacturer", "Transportation"),
    ("FFCT011", "Marine Seating and Interior Manufacturer", "Transportation"),
    ("FFCT012", "Healthcare Mattress and Support Surface Manufacturer", "Healthcare and Care"),
    ("FFCT013", "Medical Cushioning and Positioning Product Manufacturer", "Healthcare and Care"),
    ("FFCT014", "Wheelchair and Pressure Care Product Manufacturer", "Healthcare and Care"),
    ("FFCT015", "Baby and Childcare Product Manufacturer", "Healthcare and Care"),
    ("FFCT016", "Acoustic and Sound Absorption Product Manufacturer", "Technical and Industrial"),
    ("FFCT017", "Air and Liquid Filtration Product Manufacturer", "Technical and Industrial"),
    ("FFCT018", "Gasket Sealing and Technical Component Manufacturer", "Technical and Industrial"),
    ("FFCT019", "Industrial Cushioning and Vibration Control Manufacturer", "Technical and Industrial"),
    ("FFCT020", "Protective Packaging Manufacturer", "Technical and Industrial"),
    ("FFCT021", "Electronics and Electrical Cushioning Manufacturer", "Technical and Industrial"),
    ("FFCT022", "Appliance Component Manufacturer", "Technical and Industrial"),
    ("FFCT023", "Textile Lamination and Composite Manufacturer", "Technical and Industrial"),
    ("FFCT024", "Building Acoustic and Interior Product Manufacturer", "Construction and Flooring"),
    ("FFCT025", "Carpet Underlay and Flooring Manufacturer", "Construction and Flooring"),
    ("FFCT026", "Rebond Foam Manufacturer", "Construction and Flooring"),
    ("FFCT027", "Cleaning Sponge Manufacturer", "Consumer Products"),
    ("FFCT028", "Industrial and Janitorial Cleaning Product Manufacturer", "Consumer Products"),
    ("FFCT029", "Personal Care and Cosmetic Sponge Manufacturer", "Consumer Products"),
    ("FFCT030", "Footwear and Insole Manufacturer", "Consumer Products"),
    ("FFCT031", "Sports Leisure and Protective Padding Manufacturer", "Consumer Products"),
    ("FFCT032", "Apparel and Garment Padding Manufacturer", "Consumer Products"),
    ("FFCT033", "Pet Bedding and Cushion Product Manufacturer", "Consumer Products"),
    ("FFCT034", "Foam Converter and Fabricator", "Conversion and Distribution"),
    ("FFCT035", "Foam Distributor and Wholesaler", "Conversion and Distribution"),
    ("FFCT036", "Private Label and Retail Product Brand", "Conversion and Distribution"),
    ("FFCT999", "Other Flexible Foam Customer", "Other"),
]

# What the dropdown offers, and what is stored on Customer.customer_type. The
# NAME is stored, not the id - same "text snapshot" convention the rest of this
# schema uses (RawMaterial.default_supplier, CustomerTrial.customer_name), so a
# customer row stays readable without a join and survives this list changing.
CUSTOMER_TYPE_NAMES = [name for _id, name, _group in CUSTOMER_TYPES]

# name -> group, for grouping/segmentation in reporting later. Nothing reads it
# yet; it is here so the group column from the master is not thrown away.
CUSTOMER_TYPE_GROUP = {name: group for _id, name, group in CUSTOMER_TYPES}

ZONE_LABELS = ["Top", "Middle", "Bottom", "Whole sample / N/A"]

# A sample/result/issue belongs to exactly one of these three parents -
# never more than one, never none. Enforced at the app level
# (sample_source_fk_field() below, used by pages 5/6/9) rather than a DB CHECK, to keep local SQLite
# dev portable. "Production" is the only one that ever has real machine/
# process settings (ProductionPhase) behind it - CustomerTrial and
# OptimizationTrial are both independent lab-trial flows with no such
# context, per user direction 2026-08-03.
SAMPLE_SOURCE_TYPES = ["Production Run", "Customer Trial", "Optimization Trial"]


def sample_source_fk_field(source_type):
    """Which FK column on Sample/PhysicalPropertyResult/QualityObservation
    corresponds to a SAMPLE_SOURCE_TYPES value - single point of truth so
    pages 5/6/9 never hardcode this mapping three separate times."""
    return {
        "Production Run": "production_run_id",
        "Customer Trial": "customer_trial_id",
        "Optimization Trial": "optimization_trial_id",
    }[source_type]


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
    # Login identifier as of 2026-08-05 (per user direction) - the login
    # form asks for this, not username. username is kept (mirrored to the
    # same value on every create/edit) purely so every existing username-
    # keyed reference elsewhere in the app (session_state, audit logs)
    # keeps working unchanged - it is never shown or asked for separately
    # on the User Accounts page anymore.
    email = Column(String(255), nullable=False, unique=True)
    username = Column(String(100), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(200))
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    active = Column(Boolean, default=True)
    valid_from = Column(Date)  # NULL = no start restriction
    valid_until = Column(Date)  # NULL = indefinite
    # Added 2026-08-01: an unconditional bypass of every RolePagePermission
    # check (access_control.can_use_page/page_visible), independent of
    # is_platform_owner and of whatever role this user happens to carry.
    # Why this exists, given Company.is_platform_owner already exists: that
    # flag is a company-SCOPE marker (which companies' data you can see),
    # not a personal permission bypass - by design (see access_control.
    # can_use_page's docstring) a platform-owner-company user assigned a
    # narrow role (e.g. "viewer") is still meant to be restricted like
    # anyone else, so HTC can give its own staff genuinely limited access
    # too. But that same design means the platform owner's own "Platform
    # Admin" role clone is just an ordinary row in the roles table -
    # reachable and editable from the User Roles page like any other company's role - so
    # a platform-owner admin could accidentally narrow their OWN role out
    # from under themselves with no separate escape hatch. is_super_admin is
    # that escape hatch: a per-person flag, editable only on a platform-
    # owner-company user (see views/25_User_Accounts.py), that always
    # resolves to full access everywhere no matter what any role's
    # RolePagePermission rows say. Reserve it for the platform owner's own
    # trusted staff, not customers' admins.
    is_super_admin = Column(Boolean, default=False)
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
    # should be active at a time. Enforced in application code (see
    # helpers.activate_recipe_version) AND, since 2026-08-01 (PI3_Gaps_and_
    # Ambiguities.docx finding 1.5), at the database level too: a partial
    # unique index (ux_recipe_version_one_active_per_grade, on
    # (foam_grade_id) WHERE is_active) means a direct SQL write that would
    # leave a grade with two active versions now fails outright rather than
    # silently succeeding. Zero active versions per grade is still allowed.
    is_active = Column(Boolean, default=True)

    # Stoichiometric ratio/index - moved here from ProductionPhase on
    # 2026-08-03. It is a formulation constant that determines the
    # isocyanate php in this recipe, not something set or measured per
    # production run: every run of this recipe version uses the same
    # ratio/index, so it belongs on the recipe, not duplicated on each
    # Setup/Finalized phase snapshot. See ProductionPhase.ratio_index below
    # for the (now-legacy, read-only) per-phase field this replaced.
    ratio_index = Column(Float)

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
    # Controlled vocabulary (FOAMING_MODES) since 2026-08-03 - renamed from
    # "laydown_mode" (incorrect/confusing wording) and converted from free
    # text to LLD / Trough / Traverse. Column name kept as foaming_mode.
    foaming_mode = Column(String(100))
    # Whether the top-flat system is in use for this phase - a distinct
    # yes/no equipment-configuration question, not part of foaming_mode.
    # Nullable (not a plain default-False Boolean): NULL means "not
    # recorded", not "confirmed not in use" - important for legacy rows
    # entered before this field existed.
    top_flat_system_used = Column(Boolean)
    # RETIRED 2026-08-03: replaced by the structured FallplateSectionPosition
    # rows below (section_number/position_mm/angle_deg), which now cover
    # fall-plate geometry directly. Column kept, unread/unwritten by the app,
    # so historical free-text notes already entered are never destroyed -
    # same precedent as RuntimeDataRecord further down this file.
    section_positions_note = Column(Text)
    # Displayed in the UI as "Tunnel width" since 2026-08-05 - "sidewall
    # width" wasn't a term plant-floor users recognized. Column name kept
    # as-is (no migration) - only every user-facing label was changed, via
    # analytics.PHASE_SETTING_LABELS and each page's widget label/caption
    # text.
    sidewall_width_mm = Column(Float)
    # RETIRED FROM THE SETUP TAB 2026-08-03: foam height is a measured
    # outcome of the foaming process, not something planned/configured at
    # Setup - it no longer appears on the Setup form. Still recorded on the
    # Finalized/Runtime Data phase, where it belongs as an observed result.
    foam_height_mm = Column(Float)

    # Ambient plant-floor conditions at the time this phase was recorded -
    # measured, not set. RETIRED FROM THE SETUP TAB 2026-08-03: ambient
    # conditions are environmental, not something planned/configured before
    # a run starts, so they no longer appear on the Setup form - only on
    # Runtime Data (Finalized), which is the actual/observed snapshot.
    ambient_temperature_c = Column(Float)
    ambient_humidity_pct = Column(Float)

    # RETIRED 2026-08-03: ratio/index is a recipe-level formulation constant
    # (it determines the isocyanate php in a recipe), not something that
    # varies per production phase - moved to RecipeVersion.ratio_index (see
    # db.py's RecipeVersion class). This column is no longer read or written
    # by the app; kept only so historical per-phase values already recorded
    # are never destroyed, same precedent as RuntimeDataRecord.
    ratio_index = Column(Float)

    # Length of foam actually produced during this phase, in metres - added
    # 2026-08-05 per user request, Runtime Data (Finalized) only, same
    # precedent as foam_height_mm above (a measured outcome, not a Setup
    # setting - never shown on the Setup form). Optional: when left blank,
    # the Runtime Data tab calculates it instead from conveyor_speed x the
    # phase_start/phase_end duration - see views/4's _compute_runtime_output().
    # Combined with sidewall_width_mm and foam_height_mm this gives the
    # produced volume (m3), and with the foam grade's target_density, the
    # produced weight (kg).
    meters_produced = Column(Float)

    # Rise time - moved here from the now-retired RuntimeDataRecord table on
    # 2026-08-02, so it lives on the same ProductionPhase row as everything
    # else instead of a separate loose runtime log. See RuntimeDataRecord
    # below. Like foam_height_mm/ambient_temperature_c/ambient_humidity_pct,
    # this is a measured reaction-time OUTCOME, not a configured setting -
    # removed from analytics.PHASE_SETTING_FIELDS on 2026-08-05 for that
    # reason (see that list's docstring), so it no longer appears in the
    # Machine Settings correlation/optimization rankings, only wherever a
    # page genuinely wants the observed value.
    #
    # curing_notes (a free-text "curing/cutting timing notes" box) removed
    # 2026-08-03 per user direction - not a real, reliably-captured field in
    # practice. RuntimeDataRecord.curing_notes below is untouched (already
    # retired/unread by the app, kept only so its historical rows aren't
    # destroyed) - this only removes the live, actively-used copy.
    rise_time = Column(Float)

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
    # The material this line actually meters, resolved against the raw-material
    # master (added 2026-08-18). stream_name is whatever the line controller
    # exports and is not a reliable identifier: the same material appears as
    # "Polyol" on one import and "Caradol SC65-18S" on another, while a label
    # like "AMINE 1" does not name a product at all. Without this link a recipe
    # line and the stream that metered it cannot be reconciled on screen, which
    # is exactly what a foam technologist tries to do first.
    #
    # Nullable by design: a new import can carry a label nobody has mapped yet,
    # and NULL ("not yet mapped") is a better state than a wrong link. Existing
    # rows were backfilled by matching each stream's flow, as a percentage of
    # total polyol flow, against the php of the run's own recipe components -
    # see migration backfill_ambiguous_streams_by_recipe_dosage.
    raw_material_id = Column(Integer, ForeignKey("raw_materials.id", ondelete="SET NULL"))
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
    raw_material = relationship("RawMaterial")


# ---------------------------------------------------------------------------
# 6h. fallplate_section_positions (structured laydown geometry per phase)
#
# Replaces free-text-only section_positions_note with actual per-point
# values, since fall-plate lines commonly have 4-6 independently adjustable
# joints/supports that materially affect density profile and bun squareness.
#
# Field shape follows how a Laader Berg Maxfoam actually defines a fall-plate
# profile (2026-08-03, per user-supplied machine reference): NOT one overall
# plate angle, but a series of vertical positions at the plate joints/
# support points between the trough outlet and the horizontal conveyor -
#
#   Trough outlet (FP start) -> FP1 -> FP2 -> FP3 -> ... -> horizontal conveyor
#
# - each point's height above the conveyor datum (or its raw actuator/
# encoder position, on older machines that don't report a calculated
# height) is the setting actually entered on the machine; because plate
# lengths and horizontal spacing are mechanically fixed, the angle of each
# individual plate section can be derived from the adjacent points' heights
# - so angle_deg is recorded as a calculated-or-recorded value, not the
# primary setting. section_number is this point's position in that
# sequence (1 = closest to the trough outlet); MAXFOAM_FALLPLATE_SECTION_
# COUNTS below gives the expected number of adjustable points for known
# Maxfoam models, purely as an entry-form guide (nothing here enforces it -
# other OEMs/models, or a Maxfoam generation not in that table, just use as
# many rows as the machine actually has).
# ---------------------------------------------------------------------------
class FallplateSectionPosition(Base):
    __tablename__ = "fallplate_section_positions"

    id = Column(Integer, primary_key=True)
    production_phase_id = Column(Integer, ForeignKey("production_phases.id"), nullable=False)
    section_number = Column(Integer, nullable=False)  # point ID along the fall profile, 1 = nearest the trough outlet
    distance_from_trough_mm = Column(Float)  # horizontal distance from the trough outlet to this point
    position_mm = Column(Float)  # vertical height above the conveyor datum (the primary machine setting)
    actuator_position = Column(Float)  # raw actuator/encoder/screw position, for machines that don't report a calculated height
    angle_deg = Column(Float)  # this plate section's angle - calculated from adjacent points' heights, or recorded directly
    notes = Column(Text)

    phase = relationship("ProductionPhase")


# Expected adjustable fall-plate section count per Laader Berg Maxfoam
# model - an entry-form guide only (see FallplateSectionPosition above),
# not a validation rule. Sourced from user-supplied machine reference,
# 2026-08-03; not independently verified against a Laader Berg operating
# manual (none was publicly available), so treat as indicative and correct
# it here if a real HMI/manual says otherwise for a given generation.
MAXFOAM_FALLPLATE_SECTION_COUNTS = {
    "Maxfoam 5010": 4,
    "Maxfoam 5020": 5,
    "Maxfoam 5025": 6,
    "Maxfoam 5035": 6,
}

# Sentinel for "the machine is a Laader Berg but not one of the known Maxfoam
# generations above" - lets the Machine setup form offer a controlled
# dropdown for Laader Berg (so expected_fallplate_section_count reliably
# matches instead of depending on free text) while still allowing any
# generation not yet in MAXFOAM_FALLPLATE_SECTION_COUNTS to be recorded via
# a free-text fallback.
OTHER_LAADER_BERG_MODEL = "Other Laader Berg model (specify below)"
MAXFOAM_MODELS = list(MAXFOAM_FALLPLATE_SECTION_COUNTS.keys()) + [OTHER_LAADER_BERG_MODEL]


def expected_fallplate_section_count(machine):
    """Best-effort lookup of MAXFOAM_FALLPLATE_SECTION_COUNTS for a given
    Machine row (matches if its `model` text contains one of the known
    Maxfoam model keys) - returns None (no guidance available) for any
    other OEM/model/generation, or if machine is None."""
    if machine is None or not machine.model:
        return None
    model_text = machine.model.strip().lower()
    for key, count in MAXFOAM_FALLPLATE_SECTION_COUNTS.items():
        if key.lower() in model_text:
            return count
    return None


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
    # Exactly one of these three is set (see SAMPLE_SOURCE_TYPES /
    # sample_source_fk_field() above) - production_run_id for real
    # production-batch QC samples, customer_trial_id / optimization_trial_id
    # for the two independent lab-trial flows added 2026-08-03. All three
    # nullable; enforced at the app level, not a DB CHECK.
    production_run_id = Column(Integer, ForeignKey("production_runs.id"))
    customer_trial_id = Column(Integer, ForeignKey("customer_trials.id"))
    optimization_trial_id = Column(Integer, ForeignKey("optimization_trials.id"))
    sample_ts = Column(DateTime)
    zone_label = Column(String(50))  # Top / Middle / Bottom / Whole sample - N/A (see ZONE_LABELS)
    # cure_age_hours removed 2026-08-03 per user direction (the other
    # "curing time" field that isn't really a thing in practice).
    notes = Column(Text)

    production_run = relationship("ProductionRun")
    customer_trial = relationship("CustomerTrial")
    optimization_trial = relationship("OptimizationTrial")


# ---------------------------------------------------------------------------
# 6g. conditioning_segments - REMOVED 2026-08-04 per user direction ("drop
# conditioning, it is irrelevant, eliminate it from the functionality").
# The conditioning_segments table itself was also dropped from Supabase in
# the same batch (no migration system in this app - tables are created via
# Base.metadata.create_all(), so there was no migration file to delete
# either; the DROP TABLE was run directly). If conditioning history is ever
# wanted again, it would need to be rebuilt from scratch, not restored from
# here - unlike RuntimeDataRecord/MaintenanceAndLicenseRecord elsewhere in
# this file, this model wasn't left in place, since the user explicitly
# asked for the data itself to be gone, not just unused.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. runtime_data_records
#
# RETIRED 2026-08-02: the Production Run page's old "Process Phases" +
# standalone "Runtime Data" tabs did overlapping work (line_speed duplicated
# ProductionPhase.conveyor_speed, temperature/pressure text fields duplicated
# structured fields elsewhere), so the page was restructured into two
# dedicated tabs - "Setup" and "Runtime Data" - both backed by ProductionPhase
# (phase_name "Setup"/"Finalized"). The two genuinely unique fields this table
# had, rise_time and curing_notes, moved onto ProductionPhase alongside
# ambient_temperature_c/ambient_humidity_pct (see above). The model/table
# stays here, unread and unwritten by the app, purely so the historical rows
# already in production are never destroyed - same precedent as
# MaintenanceAndLicenseRecord elsewhere in this file.
# ---------------------------------------------------------------------------
class RuntimeDataRecord(Base):
    __tablename__ = "runtime_data_records"

    id = Column(Integer, primary_key=True)
    production_run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False)
    line_speed = Column(Float)
    pump_speed_or_flow_data = Column(String(200))
    temperature_data = Column(String(200))
    pressure_data = Column(String(200))
    rise_time = Column(Float)
    curing_notes = Column(Text)
    source_file_reference = Column(String(300))
    imported_at = Column(DateTime, default=dt.datetime.utcnow)

    production_run = relationship("ProductionRun", back_populates="runtime_records")


# ---------------------------------------------------------------------------
# 8c. customer_trials (lab trial made for a customer/sales opportunity)
#
# Added 2026-08-03, per user correction: this is NOT a production run with a
# "purpose" flag - it's a genuinely independent flow. A customer trial is
# typically a small lab-scale box made to answer a specific sales
# opportunity, with no machine/process settings behind it at all (no
# ProductionPhase, no Setup/Runtime Data - that structure only exists for a
# real production run). It still targets a foam grade and, usually, a
# formulation - hence foam_grade_id (required, so this flows into the same
# foam-grade-keyed Intelligence pipeline as production data) and
# recipe_version_id (optional - a trial formulation isn't always a saved
# recipe version). Samples, quality test results, and quality issues attach
# here via their own nullable customer_trial_id FK (see Sample,
# PhysicalPropertyResult, QualityObservation below) - never via
# production_run_id, which stays NULL for every row tied to a trial.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 8b. customers (lightweight customer master)
#
# Ported from PI3 Rigid Foam Edition's CR-14 (12 Aug 2026) so the two editions
# model customer identity the same way. Deliberately lightweight - "a practical
# application reference rather than a full CRM": company name, a contact person
# and email, and an optional free-text type. Sales pipeline, multiple contacts
# and commercial history are out of scope.
#
# Before this, a customer existed only as free text on CustomerTrial.
# customer_name, which drifts the same way every other free-text reference in
# this app has: two trials for the same customer entered by different people
# under slightly different names, with nothing tying them together.
#
# Uniqueness is scoped per company, matching Supplier above - two tenant
# companies can each have their own "King Furniture" without colliding.
# ---------------------------------------------------------------------------
class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("company_id", "company_name", name="uq_customer_company_name"),)

    id = Column(Integer, primary_key=True)
    # company_id is the TENANT that owns this record; company_name is the
    # CUSTOMER's own name. Those two reading as the same thing is exactly what
    # went wrong in the UI - see below.
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    # Labelled "Customer Name" everywhere in the UI, not "Company Name"
    # (18 Aug 2026, Stefan: the old label sat next to the tenant "Company"
    # column on views/29_Customers.py and read as a duplicate of it). The
    # column keeps its ported-from-Rigid-Foam name on purpose - renaming it
    # would mean migrating the uq_customer_company_name constraint plus every
    # read site, for nothing a user would ever see.
    company_name = Column(String(200), nullable=False)
    contact_person = Column(String(200))
    contact_email = Column(String(200))
    # Constrained to CUSTOMER_TYPES above (18 Aug 2026). Still a plain String
    # rather than an FK or an Enum: the NAME is stored, so a row stays readable
    # without a join, and a later revision of the vocabulary cannot orphan a
    # customer or require a migration. String(100) is comfortably above the
    # longest name in the master, which is 56 characters.
    customer_type = Column(String(100))
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    company = relationship("Company")


class CustomerTrial(Base):
    __tablename__ = "customer_trials"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    foam_grade_id = Column(Integer, ForeignKey("foam_grades.id"), nullable=False)
    recipe_version_id = Column(Integer, ForeignKey("recipe_versions.id"))
    # Nullable on purpose: every row that predates the customer master, and any
    # import that cannot be confidently auto-matched, keeps working untouched.
    customer_id = Column(Integer, ForeignKey("customers.id"))

    # customer_name is DELIBERATELY KEPT alongside customer_id rather than
    # replaced by it. It is still what reports.py and the trial pages read for
    # display, and keeping it means no historical row loses information. It is
    # maintained as a synced text snapshot of Customer.company_name - the same
    # "text snapshot, not a live-only reference" pattern RawMaterial.
    # default_supplier already uses for Supplier. The Customers page re-syncs it
    # on rename; cascades.backfill_trial_customers() maps existing rows.
    customer_name = Column(String(200), nullable=False)
    sales_opportunity_reference = Column(String(200))
    requested_by = Column(String(200))
    trial_objective = Column(Text)  # what the customer wants evaluated, and why
    responsible_person = Column(String(200))
    trial_date = Column(Date)
    batch_reference = Column(String(200))  # this trial's own box/batch identifier
    status = Column(String(50), default="Open")  # Open / Pending Closure / Closed

    # closeout
    outcome = Column(Text)
    customer_feedback = Column(Text)
    follow_up_action = Column(Text)
    reviewed_by = Column(String(200))
    date_closed = Column(Date)

    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    plant = relationship("Plant")
    foam_grade = relationship("FoamGrade")
    recipe_version = relationship("RecipeVersion")

    # Closeout enforced app-level only (no DB-level CHECK constraint, to
    # limit migration scope for this batch).
    REQUIRED_CLOSEOUT_FIELDS = ["outcome", "reviewed_by", "date_closed"]

    def missing_closeout_fields(self):
        return [f for f in self.REQUIRED_CLOSEOUT_FIELDS if not getattr(self, f)]

    def can_close(self):
        return len(self.missing_closeout_fields()) == 0


# ---------------------------------------------------------------------------
# 8d. optimization_trials (lab trial stemming from a Performance Improvement
# initiative, related to but independent of the Industrial Intelligence
# section's own analysis - see CustomerTrial above for the shared reasoning
# on why this doesn't go through Production Run).
# ---------------------------------------------------------------------------
class OptimizationTrial(Base):
    __tablename__ = "optimization_trials"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    foam_grade_id = Column(Integer, ForeignKey("foam_grades.id"), nullable=False)
    recipe_version_id = Column(Integer, ForeignKey("recipe_versions.id"))

    improvement_initiative_reference = Column(String(200))
    hypothesis = Column(Text)
    what_changed = Column(Text)
    responsible_person = Column(String(200))
    trial_date = Column(Date)
    batch_reference = Column(String(200))
    status = Column(String(50), default="Open")  # Open / Pending Closure / Closed

    # closeout
    result_against_target = Column(Text)
    conclusion = Column(Text)
    reuse_recommendation = Column(Text)
    reviewed_by = Column(String(200))
    approved_by = Column(String(200))
    date_closed = Column(Date)

    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    plant = relationship("Plant")
    foam_grade = relationship("FoamGrade")
    recipe_version = relationship("RecipeVersion")

    REQUIRED_CLOSEOUT_FIELDS = ["conclusion", "reuse_recommendation", "reviewed_by", "approved_by", "date_closed"]

    def missing_closeout_fields(self):
        return [f for f in self.REQUIRED_CLOSEOUT_FIELDS if not getattr(self, f)]

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
# Keyed to exactly one of a production run, a customer trial, or an
# optimization trial - see SAMPLE_SOURCE_TYPES above.
# ---------------------------------------------------------------------------
class PhysicalPropertyResult(Base):
    __tablename__ = "physical_property_results"

    id = Column(Integer, primary_key=True)
    # Exactly one of these three is set - see SAMPLE_SOURCE_TYPES above.
    production_run_id = Column(Integer, ForeignKey("production_runs.id"))
    customer_trial_id = Column(Integer, ForeignKey("customer_trials.id"))
    optimization_trial_id = Column(Integer, ForeignKey("optimization_trials.id"))
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

    sample = relationship("Sample")
    production_run = relationship("ProductionRun")
    customer_trial = relationship("CustomerTrial")
    optimization_trial = relationship("OptimizationTrial")


# ---------------------------------------------------------------------------
# 10. quality_observations  (NOT "defects" - approved terminology)
#
# Keyed to exactly one of a production run, a customer trial, or an
# optimization trial - see SAMPLE_SOURCE_TYPES above.
# ---------------------------------------------------------------------------
class QualityObservation(Base):
    __tablename__ = "quality_observations"

    id = Column(Integer, primary_key=True)
    # Exactly one of these three is set - see SAMPLE_SOURCE_TYPES above.
    production_run_id = Column(Integer, ForeignKey("production_runs.id"))
    customer_trial_id = Column(Integer, ForeignKey("customer_trials.id"))
    optimization_trial_id = Column(Integer, ForeignKey("optimization_trials.id"))
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

    production_run = relationship("ProductionRun")
    customer_trial = relationship("CustomerTrial")
    optimization_trial = relationship("OptimizationTrial")


# ---------------------------------------------------------------------------
# 13. expert_notes
# ---------------------------------------------------------------------------
class ExpertNote(Base):
    __tablename__ = "expert_notes"

    id = Column(Integer, primary_key=True)
    linked_entity_type = Column(String(100), nullable=False)  # e.g. "production_run", "foam_grade"
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
# 17. performance_logs
# ---------------------------------------------------------------------------
# Added 2026-08-02, in response to a reported "app feels slow in general".
# Records one row every time one of analytics.py's three shared, cached
# data-loading functions (run_settings_dataframe, property_results_dataframe,
# actual_usage_dataframe) actually has to hit the database - i.e. a cache
# MISS, not every call. A cache hit never re-executes the function body, so
# it never reaches the logging call either; that's deliberate, not an
# oversight - the whole point of this table is to show how expensive the
# real work is and how often it actually happens, not to log the (much more
# frequent, and near-instant) cache hits too. See analytics._log_performance.
#
# grade_ids stores foam_grade_id as text (comma-joined) rather than a proper
# FK, since that parameter is sometimes a single id and sometimes a list (a
# foam family's pooled grade ids - see analytics._grade_id_list) - this
# table is a lightweight operational log, not a normalized relationship, so
# a denormalized text snapshot is the right amount of structure here.
#
# Logging itself must never be able to break a page: analytics._log_performance
# wraps this insert in a try/except and swallows any failure silently, so a
# logging problem (e.g. this table not yet migrated on some environment)
# degrades to "no data on the Performance admin page", never a crashed
# Intelligence page.
class PerformanceLog(Base):
    __tablename__ = "performance_logs"

    id = Column(Integer, primary_key=True)
    function_name = Column(String(100), nullable=False)
    grade_ids = Column(String(200))  # comma-joined foam_grade_id(s), or NULL for "no grade filter"
    property_name = Column(String(200))
    row_count = Column(Integer)
    duration_ms = Column(Float, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 17b. page_load_logs
# ---------------------------------------------------------------------------
# Added 2026-08-05, alongside the v2.0 performance audit's caching batch, to
# answer the actual original complaint directly ("a simple screen-build
# takes 15-20 seconds") rather than only the narrower PerformanceLog metric
# above (which only covers 3 shared data-loading functions, and only on a
# cache miss). This instead records the FULL page-script execution time,
# logged once per Streamlit rerun of ANY page - both a fresh navigation and
# every widget-triggered rerun on that same page, since a rerun re-executes
# the whole page script top to bottom under Streamlit's model. Measured
# around app.py's single pg.run() call (see st.navigation()) - the one
# choke point every page's script runs through - so this fires for every
# page with no change needed to any of the ~27 individual page files.
#
# Much higher volume than PerformanceLog (every rerun, not just cache
# misses), so it gets the same 30-day trim housekeeping - see
# audit_log.log_page_load().
class PageLoadLog(Base):
    __tablename__ = "page_load_logs"

    id = Column(Integer, primary_key=True)
    page_name = Column(String(200), nullable=False)
    duration_ms = Column(Float, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 16a-16g. Audit / usage / pilot-learning logging package
#
# Added 2026-08-03 for Gate 6, Items 47-56 of the Duroflex pilot readiness
# list (see PI3_Application_Changes_Needed.docx, section 3.2) - before this,
# none of the ten items existed: no login history, no page-usage tracking,
# only one of the six PI3 call sites optionally persisted its Q&A (and only
# if the reviewer chose to save it to Expert Notes), no token/cost or
# response-time capture, no error log, no export/access log, no role-change
# history, no PI3 answer feedback, and no HTC review page. All seven tables
# below are deliberately append-only (application code only ever INSERTs -
# see audit_log.py, the shared module every logging call goes through) and
# all seven use nullable user_id/company_id: a login FAILURE has no user_id
# yet (the username may not even exist), and any of these could in principle
# fire from a legacy/dev session (AUTH_DISABLED) with no real User row
# behind it - a NOT NULL FK would silently break logging in exactly the
# cases most worth capturing (e.g. a failed login attempt).
# ---------------------------------------------------------------------------
class LoginEvent(Base):
    """Item 47 - append-only login/logout history. One row per attempt,
    not per session: a failed login (bad password, deactivated account,
    outside its valid_from/valid_until window - see auth._check_db_login)
    is just as important to retain as a success. username_attempted is
    kept separately from user_id because a failed attempt against a
    username that doesn't exist at all has no User row to link to."""
    __tablename__ = "login_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    username_attempted = Column(String(150))
    company_id = Column(Integer, ForeignKey("companies.id"))
    event_type = Column(String(20), nullable=False)  # login_success / login_failure / logout
    detail = Column(String(300))  # e.g. "invalid password", "account deactivated", "outside valid window"
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    user = relationship("User")
    company = relationship("Company")


class PageViewEvent(Base):
    """Item 48 - page/module usage tracking. One row per NAVIGATION to a
    page, not per Streamlit rerun (a rerun fires on every widget
    interaction within the same page - see audit_log.log_page_view_if_new
    for how a duplicate row per click is avoided)."""
    __tablename__ = "page_view_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    page_name = Column(String(200), nullable=False)
    viewed_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    user = relationship("User")
    company = relationship("Company")


class PI3InteractionLog(Base):
    """Items 49-51 - every PI3 question and response, across all call
    sites (the 5 fixed-prompt Intelligence-page sections AND every
    free-form 'Ask PI3' box - see ai_assistant.ask_assistant() /
    ask_plant_question(), which both write here directly so no call site
    can be missed), plus OpenAI token usage/estimated cost and response
    time for each call. call_site is a short stable label identifying
    which page/section asked (see ai_assistant.py's callers) - not a
    controlled vocabulary at the DB level, since new call sites will keep
    being added as the app grows."""
    __tablename__ = "pi3_interaction_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    plant_id = Column(Integer, ForeignKey("plants.id"))
    call_site = Column(String(100), nullable=False)
    question_text = Column(Text)
    response_text = Column(Text)
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)
    estimated_cost_usd = Column(Float)
    response_time_ms = Column(Float)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    user = relationship("User")
    company = relationship("Company")
    plant = relationship("Plant")


class PI3Feedback(Base):
    """Item 55 - user feedback (thumbs up/down + optional comment) on one
    specific PI3 answer, linked back to the PI3InteractionLog row it's
    reacting to - see helpers.render_pi3_feedback_control."""
    __tablename__ = "pi3_feedback"

    id = Column(Integer, primary_key=True)
    pi3_interaction_log_id = Column(Integer, ForeignKey("pi3_interaction_logs.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    rating = Column(String(10), nullable=False)  # 'up' / 'down'
    comment = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    interaction = relationship("PI3InteractionLog")
    user = relationship("User")


class ErrorLog(Base):
    """Item 52 - application errors and failed operations that would
    otherwise only ever reach the user via a transient st.error()/
    st.warning() and then vanish with the next rerun. Not exhaustive of
    every possible exception in the app (that would mean wrapping every
    single try/except) - covers the highest-value points where a real
    failure has historically shown up (PI3 API calls, DB session
    recovery) - see audit_log.log_error()."""
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    page_name = Column(String(200))
    error_message = Column(Text, nullable=False)
    traceback_text = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    user = relationship("User")
    company = relationship("Company")


class ExportLog(Base):
    """Item 53 - recipe/report/PI3-answer export and access events.
    Logged via st.download_button's on_click callback (see
    audit_log.log_export and its call sites in views/21_Report.py and
    helpers.render_pi3_docx_download) - fires exactly when the reviewer
    actually clicks Download, not merely when the button is rendered."""
    __tablename__ = "export_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    export_type = Column(String(100), nullable=False)
    description = Column(String(300))
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    user = relationship("User")
    company = relationship("Company")


class RoleChangeLog(Base):
    """Item 54 - user and role/permission change history. Logs a
    human-readable summary of what changed rather than a full per-field
    old/new diff (the User Roles page access grid alone has one entry per
    page x 3 access levels - a full diff would be a lot of machinery for
    a pilot-scale audit trail); target_type is 'user', 'role', or
    'permission' (page-access grid saves)."""
    __tablename__ = "role_change_logs"

    id = Column(Integer, primary_key=True)
    changed_by_user_id = Column(Integer, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    target_type = Column(String(20), nullable=False)  # user / role / permission
    target_id = Column(Integer)
    target_label = Column(String(200))
    change_summary = Column(Text, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    changed_by = relationship("User")
    company = relationship("Company")


# ---------------------------------------------------------------------------
# 16. maintenance_and_license_records
# ---------------------------------------------------------------------------
# NOTE (corrected 2026-08-01, PI3_Gaps_and_Ambiguities.docx finding 1.1):
# this list previously omitted the entire multi-tenant/access-control layer
# (SubscriptionType, Company, Role, RolePagePermission, User) plus
# FoamGradeTargetProperty - 6 of the app's 34 mapped model classes. Confirmed
# by a repo-wide search that ALL_MODELS is not imported or referenced
# anywhere else in the codebase (init_db()'s Base.metadata.create_all()
# discovers every mapped class automatically via SQLAlchemy's own
# declarative registry, independent of this list), so the omission had zero
# runtime effect - table creation, migrations, and everything else already
# covered all 34 tables regardless. Completed here purely so this list is
# accurate if something is ever built against it later.
ALL_MODELS = [
    SubscriptionType,
    Company,
    Role,
    RolePagePermission,
    User,
    Plant,
    Machine,
    ProductFamily,
    FoamGrade,
    FoamGradeTargetProperty,
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
    RuntimeDataRecord,
    CustomerTrial,
    OptimizationTrial,
    PhysicalPropertyDefinition,
    PhysicalPropertyMethod,
    PhysicalPropertyUOM,
    PhysicalPropertyResult,
    QualityObservation,
    ExpertNote,
    PI3AIConnectionSetting,
    PerformanceLog,
    LoginEvent,
    PageViewEvent,
    PI3InteractionLog,
    PI3Feedback,
    ErrorLog,
    ExportLog,
    RoleChangeLog,
]


def init_db():
    """Create all tables if they do not already exist. Safe to call on every
    app start - the actual schema-reflection work now only runs once per
    server process (see _ensure_schema_ready below), not once per call.

    Before 2026-08-05 this called Base.metadata.create_all() directly and
    was invoked from app.py's module-level code, which reruns on EVERY
    Streamlit widget interaction anywhere in the app (app.py is the
    st.navigation entry point - its top-level code re-executes on every
    click, not just on navigation). That meant a full schema check
    (Inspector round trip(s) against all ~39 mapped tables) against the
    remote Supabase Postgres on every single click, forever, even though
    the schema only ever needs checking once per process lifetime. This
    was one of the largest fixed-overhead contributors to the "every
    screen build takes 15-20s" performance report - see PROJECT_STATUS.md
    / the v2.0 performance audit."""
    _ensure_schema_ready()


@st.cache_resource
def _ensure_schema_ready():
    """st.cache_resource caches the return value in-process, shared across
    every browser session this server handles - so this body runs exactly
    once per process, not once per rerun. Returns a value (rather than
    None) only so the cache has something to store; callers never use it."""
    Base.metadata.create_all(bind=ENGINE)
    return True


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


def session_lock():
    """Re-entrant lock guarding the one Session this browser tab shares.

    A SQLAlchemy Session is not thread-safe, and Streamlit does not
    guarantee that only one script thread touches a browser session at a
    time: when a rerun is triggered while a slow page is still running,
    Streamlit sets a stop flag on the old run but that thread keeps
    executing until it next calls into the Streamlit API. On a page that
    spends 20-60s in analytics before its next st.* call - Performance,
    Recipe Optimization, Trend Analysis, Machine Settings Optimization -
    that leaves a wide window in which the old thread is still mid-query
    while the new thread starts issuing its own.

    Both threads then operate on the same Session, which surfaces as
    'This session is provisioning a new connection; concurrent operations
    are not permitted' - the error logged twice against Performance and
    Default User Roles on 2026-08-05, and the reason a third was logged as
    'inactive state, due to the SQL transaction being rolled back' after
    one thread's failure left the shared transaction dead for the other.

    The lock is stored per browser session, alongside the Session it
    guards, so tabs never block each other."""
    if "_sa_session_lock" not in st.session_state:
        st.session_state["_sa_session_lock"] = threading.RLock()
    return st.session_state["_sa_session_lock"]


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

    Second incident, 2026-08-18: the same leak has a faster, louder failure
    mode. Supabase sets idle_in_transaction_session_timeout to 5 minutes,
    so a transaction left open across reruns gets its connection killed
    server-side; the next query that browser tab issues then dies with
    OperationalError on a socket that is already gone. The Postgres log
    showed 16 such terminations between 2026-08-17 22:00 and 2026-08-18
    08:00. Note that pool_pre_ping cannot prevent this: pre-ping validates
    a connection at pool CHECKOUT, and a Session sitting on an open
    transaction never returns its connection to the pool. app.py now
    recovers from it (see _is_dead_connection there), but recovery costs a
    rerun - closing the transaction out here is what stops it happening.

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

    # Do not touch the Session while another script thread still holds it -
    # see session_lock(). A superseded rerun that is still mid-query owns
    # the lock; forcing a commit underneath it is what produced the
    # 'concurrent operations are not permitted' failures. That thread runs
    # its own close_out_session() when it finishes, so skipping here loses
    # nothing. The timeout keeps this from ever becoming a hang: if the
    # holder is genuinely stuck we fall through and leave the transaction
    # for the next rerun rather than blocking this one indefinitely.
    lock = session_lock()
    if not lock.acquire(timeout=5):
        return

    try:
        # After a failed statement SQLAlchemy marks the transaction
        # inactive; commit() on an inactive Session raises rather than
        # doing anything useful, so roll back to clear the state instead.
        # This is the second of the two 2026-08-05 error signatures.
        if not session.is_active:
            session.rollback()
            return
        session.commit()
    except Exception as commit_exc:
        # If the underlying connection itself has gone bad (e.g. the server
        # killed it - idle-in-transaction timeout, a restart, ...), rollback()
        # can also fail. In that case don't leave this same broken Session
        # cached in st.session_state: every future rerun of this browser tab
        # would keep reusing it and keep failing the same way until the user
        # did a full page reload. Discard it instead so the next
        # get_session() call builds a fresh Session (and checks out a fresh,
        # pool_pre_ping-verified connection) on the very next rerun.
        discarded = False
        try:
            session.rollback()
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            st.session_state.pop("_sa_session", None)
            discarded = True

        # Item 52 (Gate 6) - this is one of the two highest-value error
        # points in the app (the other being failed PI3 calls - see
        # ai_assistant.py's _record_pi3_error), so it's logged here
        # directly rather than waiting on individual pages to catch it.
        # audit_log is imported locally, not at module level: audit_log.py
        # imports its ORM model classes FROM this module, so a top-level
        # `import audit_log` here would be circular. By the time this
        # function actually runs, db.py has already finished importing (this
        # is a function body, not import-time code), so the deferred import
        # resolves cleanly. A fresh session is used for the write itself,
        # since the original one is either broken or was just discarded.
        try:
            import audit_log

            log_session = get_session()
            audit_log.log_error(
                log_session,
                error_message="Database transaction commit failed" + (" (session discarded)" if discarded else " (rolled back)"),
                exc=commit_exc,
                user_id=st.session_state.get("user_id"),
                company_id=st.session_state.get("company_id"),
                page_name=st.session_state.get("_current_page_title"),
            )
            log_session.commit()
        except Exception:
            pass
    finally:
        # Always hand the lock back, including on the early `return` in the
        # inactive-session branch above.
        lock.release()
