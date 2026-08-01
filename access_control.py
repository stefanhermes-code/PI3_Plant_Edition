"""Shared page-visibility rules for the multi-tenant admin layer.

Three independent things can hide a page from the current user, checked in
this order by `page_visible()`:

1. Platform-only pages (Companies, Subscription Types, PI3 Connectivity) -
   only ever visible to a user whose company is the platform owner (HTC
   itself), regardless of role. Not configurable per role; this is a hard
   gate. PI3 Connectivity joined this group after starting out merely
   subscription-gated (see point 2's history) - its edit form was already
   platform-owner-only (this is HTC's own commercial add-on switch, not
   something a company's own admin self-serves), so a company admin who
   could still open the page only ever saw a read-only status view with no
   action available. Not worth a page of its own for that - moved in with
   Companies/Subscription Types instead.
2. Subscription feature flags - REPORT_KEYS (reports_enabled) is the one
   remaining subscription-tier feature switch. PI3/AI used to work the
   same way (pi3_ai_enabled hiding the PI3 Connectivity page for anyone
   whose company lacked it), but since that page is now platform-only
   regardless of tier (point 1), pi3_ai_enabled no longer has any runtime
   enforcement effect - it's tracked on SubscriptionType purely as the
   commercial record of which tier a company is sold on, and the platform
   owner is trusted to honor it manually when deciding whether to flip a
   given plant's PI3 toggle on. Every operational page - including Recipe
   Optimization, Trend Analysis, Machine Settings Correlation, Root-Cause
   Assistant, Machine Settings Optimization, Similar Case Retrieval, and
   Expert Notes - stays visible on every tier, because each already has
   its own deterministic core that works with zero PI3 involvement
   (cost/diff calculations, correlation ranking, control charts/Cpk/CUSUM,
   the run-vs-prior-run diff, ...) and independently checks per-plant PI3
   enablement (ai_assistant.is_enabled_for_plant / any_plant_enabled)
   before rendering its own "Ask PI3" section. An earlier version of this
   file bundled those 7 pages behind their own feature flags
   (industrial_intelligence_enabled / case_review_enabled) - that was
   wrong: it hid real, PI3-independent value from Basic customers instead
   of letting each page's own PI3 check do its job.
3. Role page permissions - a DENY list, not an allow list (see db.py's
   RolePagePermission docstring): a role with no rows sees everything, in
   full; an explicit row can hide a page entirely (can_view=False) or make
   it view-only (can_view=True, can_use=False - the page renders and its
   data can be read, but its own Add/Edit/Delete forms and action buttons
   should be hidden). The three-state picker (Hidden / View only / Full
   access) is built and edited on the Default User Roles page (platform
   owner - sets what new companies start with) and the User Roles page
   (per company) via current_access_states()/save_access_states() below.

   Enforcing view-only INSIDE a page (hiding/disabling its own write
   controls) is a page-by-page opt-in, not automatic just because the row
   exists - a page checks its own usability with can_use_page() (or the
   set-based usable_page_keys_denied() for pages with several independent
   write actions to gate) and conditionally disables/skips rendering its
   forms and action buttons. As of 2026-08-01 this is rolled out on every
   operational page with a write action: Plant & Foam Equipment Overview,
   Product Family & Foam Grade, Recipes, Production Run, Quality Test
   Result, Quality Issue, Adjustment & Conclusion, Approval & Review,
   Trial / Experiment, Raw Materials, Similar Case Retrieval, Expert
   Notes, Recipe Optimization, Trend Analysis, Machine Settings
   Correlation, Root-Cause Assistant, and Machine Settings Optimization
   (its "Ask PI3"/"Save to Expert Notes" actions). The Report page is
   deliberately NOT gated - every control on it is a preview or a
   PDF/Excel download, nothing writes to the database, so there is
   nothing for view-only to restrict. The 4 platform-only pages
   (Companies, Subscription Types, PI3 Connectivity, plus the two
   role-name-gated admin pages User Roles/User Accounts) are also
   unaffected - those are gated by page_visible()'s platform-only rule or
   a literal admin role-name check, not by can_use.

PAGE_CATALOG is the single source of truth for page_key -> display title,
used both to build app.py's nav and to render the permission grid on the
User Roles / Default User Roles admin pages.
"""

from db import RolePagePermission

# The three states the admin UI ever offers for a role's access to a page -
# see the module docstring above for what each means. Stored as two
# booleans on RolePagePermission (can_view, can_use), but never let a UI
# offer "use without view": that's not a real state.
ACCESS_HIDDEN = "hidden"
ACCESS_VIEW_ONLY = "view_only"
ACCESS_FULL = "full"
ACCESS_STATE_LABELS = {
    ACCESS_HIDDEN: "Hidden",
    ACCESS_VIEW_ONLY: "View only",
    ACCESS_FULL: "Full access",
}

# Role names required by name-literal checks elsewhere in the app (see
# auth.require_role("admin") on the User Roles and User Accounts pages,
# and pages/10_PI3_AI_Connectivity.py's role == "admin" check) - a company
# with no role named "admin" could never manage its own users or roles
# again. Enforced as a delete/rename guard on the Default User Roles page.
STRUCTURALLY_REQUIRED_ROLE_NAMES = frozenset({"admin"})

# page_key -> title (title kept here only for the permission-matrix editor;
# app.py's own st.Page(..., title=...) calls remain the source of truth for
# what's actually shown in the sidebar).
PAGE_CATALOG = {
    "plant_overview": "Plant & Foam Equipment Overview",
    "product_family_foam_grade": "Product Family & Foam Grade",
    "raw_materials": "Raw Materials",
    "recipes": "Recipes",
    "production_run": "Production Run",
    "quality_test_result": "Quality Test Result",
    "quality_issue": "Quality Issue",
    "trial_experiment": "Trial / Experiment",
    "adjustment_conclusion": "Adjustment & Conclusion",
    "approval_review": "Approval & Review",
    "recipe_optimization": "Recipe Optimization",
    "trend_analysis": "Trend Analysis",
    "machine_settings_correlation": "Machine Settings vs Physical Properties Correlation",
    "root_cause_assistant": "Root-Cause Assistant",
    "machine_settings_optimization": "Machine Settings Optimization",
    "similar_case_retrieval": "Similar Case Retrieval",
    "expert_notes": "Expert Notes",
    "report": "Report",
    "pi3_ai_connectivity": "PI3 Connectivity",
    "companies_admin": "Companies",
    "subscription_types_admin": "Subscription Types",
    "user_roles_admin": "User Roles",
    "default_user_roles_admin": "Default User Roles",
    "user_accounts_admin": "User Accounts",
}

REPORT_KEYS = frozenset({"report"})
PLATFORM_ONLY_KEYS = frozenset(
    {"companies_admin", "subscription_types_admin", "pi3_ai_connectivity", "default_user_roles_admin"}
)


def denied_page_keys(session, role_id):
    """Every page_key this role has an explicit can_view=False row for."""
    if not role_id:
        return set()
    rows = (
        session.query(RolePagePermission)
        .filter(RolePagePermission.role_id == role_id, RolePagePermission.can_view.is_(False))
        .all()
    )
    return {r.page_key for r in rows}


def current_access_states(session, role_id):
    """page_key -> ACCESS_HIDDEN / ACCESS_VIEW_ONLY / ACCESS_FULL for every
    page_key this role has an explicit RolePagePermission row for. A
    page_key with no row isn't in the returned dict at all - callers should
    treat a missing key as ACCESS_FULL (the default for everyone before
    this three-state model existed, and the default for any page nobody's
    ever touched the permissions for)."""
    if not role_id:
        return {}
    rows = session.query(RolePagePermission).filter(RolePagePermission.role_id == role_id).all()
    states = {}
    for r in rows:
        if not r.can_view:
            states[r.page_key] = ACCESS_HIDDEN
        elif not r.can_use:
            states[r.page_key] = ACCESS_VIEW_ONLY
        else:
            states[r.page_key] = ACCESS_FULL
    return states


def save_access_states(session, role_id, states):
    """Replaces every RolePagePermission row for role_id to match `states`
    (page_key -> ACCESS_HIDDEN/ACCESS_VIEW_ONLY/ACCESS_FULL). ACCESS_FULL
    entries are simply omitted, matching the existing "no row = full
    access" deny-list convention. Does not commit - caller controls the
    transaction."""
    session.query(RolePagePermission).filter(RolePagePermission.role_id == role_id).delete(
        synchronize_session=False
    )
    for page_key, state in states.items():
        if state == ACCESS_HIDDEN:
            session.add(RolePagePermission(role_id=role_id, page_key=page_key, can_view=False, can_use=False))
        elif state == ACCESS_VIEW_ONLY:
            session.add(RolePagePermission(role_id=role_id, page_key=page_key, can_view=True, can_use=False))
        # ACCESS_FULL -> no row needed, that's the default.


def usable_page_keys_denied(session, role_id):
    """Every page_key this role can see but can't act on (ACCESS_VIEW_ONLY)
    - the set an operational page should check itself against to decide
    whether to render its own Add/Edit/Delete forms and action buttons.
    Deliberately separate from denied_page_keys(): a page that's fully
    hidden never reaches the point of asking this question."""
    if not role_id:
        return set()
    rows = (
        session.query(RolePagePermission)
        .filter(
            RolePagePermission.role_id == role_id,
            RolePagePermission.can_view.is_(True),
            RolePagePermission.can_use.is_(False),
        )
        .all()
    )
    return {r.page_key for r in rows}


def can_use_page(page_key, *, role_id, session):
    """Single-page convenience wrapper around usable_page_keys_denied() for
    a page that just wants one yes/no answer at the top of its script, to
    decide whether to render its own Add/Edit/Delete forms and action
    buttons. No is_platform_owner special-case here on purpose: unlike
    page_visible()'s PLATFORM_ONLY_KEYS gate (which is about cross-company
    SCOPE - seeing every company's data), being the platform owner's own
    staff doesn't exempt a "viewer"-equivalent role from view-only
    restrictions on ordinary operational pages. A role_id of None (the
    legacy secrets.toml fallback, or AUTH_DISABLED dev mode) has no
    RolePagePermission rows to deny anything, so it naturally resolves to
    full use, same as before this three-state model existed."""
    return page_key not in usable_page_keys_denied(session, role_id)


def protected_role_name(name):
    """True if this role name is load-bearing for the app itself (see
    STRUCTURALLY_REQUIRED_ROLE_NAMES) and must never be renamed away from
    or deleted, on the Default User Roles template page or anywhere else a
    role's name can be edited."""
    return (name or "").strip().lower() in STRUCTURALLY_REQUIRED_ROLE_NAMES


def page_visible(page_key, *, is_platform_owner, subscription, denied_keys):
    """subscription may be None (no subscription assigned yet - treat as
    full access rather than locking a company out over a data gap)."""
    if page_key in PLATFORM_ONLY_KEYS:
        return bool(is_platform_owner)
    if subscription is not None:
        if page_key in REPORT_KEYS and not subscription.reports_enabled:
            return False
    if page_key in denied_keys:
        return False
    return True
