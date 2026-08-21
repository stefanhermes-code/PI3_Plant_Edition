"""Shared page-visibility rules for the multi-tenant admin layer.

Four independent things can hide a page from the current user, checked in
this order by `page_visible()`:

0. Implementation scope - whether this customer was implemented with the page
   at all (db.CompanyPageAvailability, added 2026-08-20, configured by HTC
   only on views/32_Function_Availability.py). Checked immediately after the
   platform-only gate below, and deliberately BEFORE both the subscription
   flag and the role permission: a page the customer never bought is not a
   permission question, so no role edit and no tier change should reach it.
   Zero rows for a company means the full application, so nothing that
   existed before this table changed behaviour. A switched-off page is hidden
   outright rather than shown locked (Stefan, 20 August 2026) - the customer
   should not see a door they cannot open.

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
   enforcement effect, and as of 2026-08-01 it no longer even triggers a
   warning anywhere in the UI - it's tracked on SubscriptionType purely as
   an internal commercial record of which tier a company is sold on. The
   platform owner (HTC) has the unilateral right to enable PI3 connectivity
   for any plant regardless of what this flag says, with no confirmation
   step in the way; a customer is never shown that this flag exists or
   that a higher tier would unlock anything, since customers don't
   self-serve this switch in the first place (see PI3 Connectivity page,
   platform-owner-only). Every operational page - including Recipe
   Optimization, Trend Analysis, Machine Settings Correlation, Root-Cause
   Assistant, and Machine Settings Optimization - stays visible on every
   tier, because each already has its own deterministic core that works
   with zero PI3 involvement (cost/diff calculations, correlation ranking,
   control charts/Cpk/CUSUM, the run-vs-prior-run diff, ...) and
   independently checks per-plant PI3 enablement
   (ai_assistant.is_enabled_for_plant / any_plant_enabled) before rendering
   its own "Ask PI3" section. An earlier version of this file bundled
   those pages behind their own feature flags
   (industrial_intelligence_enabled / case_review_enabled) - that was
   wrong: it hid real, PI3-independent value from Basic customers instead
   of letting each page's own PI3 check do its job. Similar Case Retrieval
   used to be part of this group too, but the whole page was dropped on
   2026-08-01 (see below) - it never demonstrated real added value beyond
   what Expert Notes and the search-first workflow already cover, and its
   "Also use PI3" toggle could search across every company's data, a
   cross-tenant leak the other PI3-touching pages don't have.
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
   Result, Quality Issue, Production Samples, Customer Trials & Samples,
   Optimization Trials & Samples, Raw Materials, Expert
   Notes, Recipe Optimization, Trend Analysis, Machine Settings
   Correlation, and Root-Cause Assistant (their "Ask PI3"/"Save to Expert
   Notes" actions), and Machine Settings Optimization (its single, fixed-
   prompt "Get PI3 interpretation" button - unlike the other four PI3-
   enabled pages, this one has no free-form "Ask PI3" box or "Save to
   Expert Notes" action of its own; corrected 2026-08-01, see
   PI3_Gaps_and_Ambiguities.docx finding 2.1, which caught this docstring
   overstating what the page actually offers). The Report page is
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

import streamlit as st

from db import CompanyPageAvailability, RolePagePermission

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
# auth.require_role("Company Admin", "Platform Admin") on the User Roles
# and User Accounts pages, and views/10_PI3_AI_Connectivity.py's role in
# ("Company Admin", "Platform Admin") check) - a company with no role by
# one of these two names could never manage its own users or roles again.
# "Company Admin" is the one every regular company is seeded with (see
# role_provisioning.py) and is what STRUCTURALLY_REQUIRED_ROLE_NAMES/
# protected_role_name() actually protect on the Default User Roles page -
# it's the only one of the two that's still a clonable template. "Platform
# Admin" is a second, equally-valid name for the same two gates, reserved
# exclusively for HTC's own company (the platform owner) - HTC's own role
# is named that on purpose, distinct from every customer's "Company
# Admin", even though the two names grant identical access to their own
# company's admin pages. Neither name has ever granted cross-company
# power on its own - that's controlled separately by Company.
# is_platform_owner / require_platform_owner() and User.is_super_admin.
#
# History: renamed 2026-08-05 from "Platform Admin" (itself renamed
# 2026-08-04 from the literal "admin") to "Company Admin" for every
# regular company, since the old name misled customers into thinking
# their own admin had platform-wide reach. HTC's own role was renamed
# back to "Platform Admin" the same day, once it became clear HTC's own
# admin should keep a visibly distinct label - the "Company Admin"
# template itself was NOT reverted, so new companies still get the
# correctly-scoped name.
STRUCTURALLY_REQUIRED_ROLE_NAMES = frozenset({"company admin"})

# Both names that mean "the one administrator of this company" - see
# STRUCTURALLY_REQUIRED_ROLE_NAMES's docstring above for why there are two.
# Used by views/25_User_Accounts.py to enforce a single company-wide rule
# (2026-08-05, per user direction): a company should never have more than
# one active user holding either of these role names at once, regardless
# of which of the two names its own admin role happens to be called.
ADMIN_ROLE_NAMES = frozenset({"company admin", "platform admin"})

# page_key -> title (title kept here only for the permission-matrix editor;
# app.py's own st.Page(..., title=...) calls remain the source of truth for
# what's actually shown in the sidebar).
PAGE_CATALOG = {
    "plant_overview": "Plant & Foam Equipment Overview",
    "product_family_foam_grade": "Product Family & Foam Grade",
    "raw_materials": "Raw Materials",
    # Its own key, not folded into raw_materials. Until 18 Aug 2026 supplier
    # management was a tab inside the Raw Materials page and shared its key,
    # so a role could not be given one without the other. See
    # views/30_Suppliers.py.
    "suppliers": "Suppliers",
    "recipes": "Recipes",
    "production_run": "Production Run",
    "quality_test_result": "Quality Test Result",
    "quality_issue": "Quality Issue",
    "samples_conditioning": "Production Samples",
    "customers": "Customers",
    "customer_trials": "Customer Trials & Samples",
    "optimization_trials": "Optimization Trials & Samples",
    "recipe_optimization": "Recipe Optimization",
    "trend_analysis": "Trend Analysis",
    "machine_settings_correlation": "Machine Settings vs Physical Properties Correlation",
    "root_cause_assistant": "Root-Cause Assistant",
    "machine_settings_optimization": "Machine Settings Optimization",
    "expert_notes": "Expert Notes",
    "certipur_readiness": "CertiPUR Readiness",
    "report": "Report",
    "pi3_ai_connectivity": "PI3 Connectivity",
    "companies_admin": "Companies",
    "subscription_types_admin": "Subscription Types",
    "user_roles_admin": "User Roles",
    "default_user_roles_admin": "Default User Roles",
    "user_accounts_admin": "User Accounts",
    "performance_admin": "Performance",
    "pilot_analysis_admin": "Company Analysis",
    "ai_audit_compliance": "AI Audit & Compliance",
    "function_availability_admin": "Function Availability",
}

REPORT_KEYS = frozenset({"report"})
PLATFORM_ONLY_KEYS = frozenset(
    {
        "companies_admin", "subscription_types_admin", "pi3_ai_connectivity",
        "default_user_roles_admin", "performance_admin", "pilot_analysis_admin",
        # Cross-customer AI governance evidence - platform owner only, by
        # definition: the value of the page is the view ACROSS companies.
        "ai_audit_compliance",
        # Decides what every OTHER company is implemented with. Platform owner
        # only, and excluded from CONFIGURABLE_PAGE_KEYS below - the screen
        # that switches pages off must not be switchable off.
        "function_availability_admin",
    }
)

# page_key -> the navigation section it sits in, matching
# app.py's nav_sections_with_keys. Kept here rather than read back out of
# app.py because app.py builds st.Page objects, which cannot be imported from
# without starting Streamlit - and because the implementation workbook (see
# reports.render_function_availability_xlsx) has to group pages the same way
# the customer sees them in the sidebar. The consistency check below fails at
# import time if a page is ever added to PAGE_CATALOG and not to this map, so
# the two cannot silently drift.
PAGE_SECTION = {
    "plant_overview": "Setup",
    "product_family_foam_grade": "Setup",
    "raw_materials": "Setup",
    "suppliers": "Setup",
    "recipes": "Setup",
    "production_run": "Production",
    "customers": "Customers",
    "samples_conditioning": "Samples & Trials",
    "customer_trials": "Samples & Trials",
    "optimization_trials": "Samples & Trials",
    "quality_test_result": "Quality",
    "quality_issue": "Quality",
    "recipe_optimization": "Industrial Intelligence",
    "trend_analysis": "Industrial Intelligence",
    "machine_settings_correlation": "Industrial Intelligence",
    "root_cause_assistant": "Industrial Intelligence",
    "machine_settings_optimization": "Industrial Intelligence",
    "expert_notes": "Industrial Intelligence",
    "certipur_readiness": "Industrial Intelligence",
    "report": "Reports",
    "user_roles_admin": "Company Admin",
    "user_accounts_admin": "Company Admin",
    "pi3_ai_connectivity": "Application Admin",
    "companies_admin": "Application Admin",
    "subscription_types_admin": "Application Admin",
    "default_user_roles_admin": "Application Admin",
    "performance_admin": "Application Admin",
    "pilot_analysis_admin": "Application Admin",
    "ai_audit_compliance": "Application Admin",
    "function_availability_admin": "Application Admin",
}

# The order sections are shown in, on the Function Availability screen and in
# the implementation workbook. Matches the sidebar order.
SECTION_ORDER = (
    "Setup", "Production", "Customers", "Samples & Trials", "Quality",
    "Industrial Intelligence", "Reports", "Company Admin", "Application Admin",
)

if set(PAGE_SECTION) != set(PAGE_CATALOG):
    raise RuntimeError(
        "PAGE_SECTION and PAGE_CATALOG have drifted apart: "
        + repr(sorted(set(PAGE_CATALOG) ^ set(PAGE_SECTION)))
    )

# Pages a customer implementation can switch off (see
# db.CompanyPageAvailability). Everything else is excluded on purpose:
#
#   - PLATFORM_ONLY_KEYS are HTC's own screens. They are not sold, so there is
#     nothing to include or exclude, and one of them is the screen that does
#     this configuring - which must stay reachable whatever is switched off.
#   - user_roles_admin and user_accounts_admin are structurally required: a
#     company that cannot reach them can never manage its own users or roles
#     again, and nothing in the application could put that right from the
#     customer's side.
#
# Overview is not in PAGE_CATALOG at all - it is the landing page and is
# always shown (see app.py's top_pages).
NON_CONFIGURABLE_PAGE_KEYS = PLATFORM_ONLY_KEYS | frozenset(
    {
        "user_roles_admin", "user_accounts_admin",
        # CertiPUR Readiness is a commercial add-on with its own explicit
        # opt-in on the company (Company.certipur_enabled), not a page that
        # was always there and can be taken away. It is excluded here so the
        # two mechanisms cannot disagree - a deny-list row saying a customer
        # HAS the page while the add-on flag says they have not would be a
        # contradiction with no obvious winner.
        "certipur_readiness",
    }
)
CONFIGURABLE_PAGE_KEYS = frozenset(PAGE_CATALOG) - NON_CONFIGURABLE_PAGE_KEYS


@st.cache_data(ttl=60)
def denied_page_keys(_session, role_id):
    """Every page_key this role has an explicit can_view=False row for.

    Cached (2026-08-05, performance audit): this runs from app.py's
    module-level code, which reruns on every single widget interaction
    anywhere in the app - previously that meant a fresh DB round trip for
    nav visibility on every click. `_session` is underscore-prefixed so
    Streamlit doesn't try to hash the SQLAlchemy Session object; the cache
    key is just role_id, which is what actually determines the result.
    save_access_states() below clears this cache immediately after any
    edit, so a permission change is never masked by the 60s TTL - the TTL
    is only there as a safety net for cache entries from a role that gets
    edited by a process other than this one (e.g. a second browser tab)."""
    if not role_id:
        return set()
    rows = (
        _session.query(RolePagePermission)
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
    transaction.

    Clears denied_page_keys()'s cache for every role (st.cache_data has no
    per-key clear, only clear-everything) so this edit takes effect on the
    very next rerun instead of waiting out that cache's 60s TTL - the two
    call sites (User Roles, Default User Roles) both save then immediately
    rerun the page, so without this the admin would see their own edit
    appear to not work."""
    st.cache_data.clear()
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


@st.cache_data(ttl=60)
def unavailable_page_keys(_session, company_id):
    """Every page_key this company has NOT been implemented with (see
    db.CompanyPageAvailability).

    Company-level rows only - plant_id IS NULL. The plant column exists for a
    later override and nothing resolves it yet; see the note above the model.

    Cached on the same terms and for the same reason as denied_page_keys()
    above: this is called from app.py's module-level nav code, which reruns on
    every widget interaction anywhere in the app. `_session` is
    underscore-prefixed so Streamlit does not try to hash the SQLAlchemy
    Session; the cache key is company_id, which is what determines the result.
    save_page_availability() clears the cache immediately after an edit, so a
    change is never masked by the 60s TTL.

    A company_id of None (the platform owner viewing unscoped, or the
    AUTH_DISABLED development fallback) has nothing to resolve and returns an
    empty set - the full application, same as before this table existed."""
    if not company_id:
        return set()
    rows = (
        _session.query(CompanyPageAvailability)
        .filter(
            CompanyPageAvailability.company_id == company_id,
            CompanyPageAvailability.plant_id.is_(None),
            CompanyPageAvailability.available.is_(False),
        )
        .all()
    )
    # Intersected with what is actually configurable, so a stale row for a
    # page that has since been retired, or one loaded against a key that is no
    # longer sellable, cannot hide something it was never allowed to hide.
    return {r.page_key for r in rows} & set(CONFIGURABLE_PAGE_KEYS)


def current_page_availability(session, company_id):
    """page_key -> True/False for every configurable page, for the editor.

    Unlike unavailable_page_keys() this returns a complete map rather than
    just the exceptions, because the screen and the workbook both have to show
    the customer every page and its state, not only the ones switched off."""
    off = set()
    if company_id:
        rows = (
            session.query(CompanyPageAvailability)
            .filter(
                CompanyPageAvailability.company_id == company_id,
                CompanyPageAvailability.plant_id.is_(None),
                CompanyPageAvailability.available.is_(False),
            )
            .all()
        )
        off = {r.page_key for r in rows}
    return {key: key not in off for key in CONFIGURABLE_PAGE_KEYS}


def save_page_availability(session, company_id, availability, set_by=None):
    """Replaces every company-level row for company_id to match
    `availability` (page_key -> True/False). True entries are simply omitted,
    matching the "no row = available" deny-list convention.

    Returns the sorted list of page_keys left switched off, so the caller can
    log and report exactly what was written rather than what it intended.

    Does not commit - the caller controls the transaction. Clears the cache
    for the same reason save_access_states() does: st.cache_data has no
    per-key clear, and both call sites save then rerun immediately, so without
    this the administrator would see their own edit appear not to work.

    Plant-level rows are left untouched. Nothing writes them today; if that
    changes, this function deletes only what it owns rather than quietly
    discarding an override it does not understand."""
    st.cache_data.clear()
    session.query(CompanyPageAvailability).filter(
        CompanyPageAvailability.company_id == company_id,
        CompanyPageAvailability.plant_id.is_(None),
    ).delete(synchronize_session=False)
    switched_off = []
    for page_key, is_available in availability.items():
        if page_key not in CONFIGURABLE_PAGE_KEYS:
            # Silently ignoring an unknown key would let a hand-edited
            # workbook write a row that hides nothing and confuses the next
            # person to read the table.
            raise ValueError("%s is not a configurable page" % page_key)
        if not is_available:
            session.add(
                CompanyPageAvailability(
                    company_id=company_id, plant_id=None, page_key=page_key,
                    available=False, set_by=set_by,
                )
            )
            switched_off.append(page_key)
    return sorted(switched_off)


def can_use_page(page_key, *, role_id, session, is_super_admin=False):
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
    full use, same as before this three-state model existed.

    is_super_admin (see db.py's User.is_super_admin) IS an unconditional
    bypass, unlike is_platform_owner above - it's a deliberate per-person
    escape hatch, not a scope marker, added 2026-08-01 so the platform
    owner's own trusted staff can never be locked out of their own
    operational pages by an edit to their own role's permissions (which,
    unlike a customer's role, the platform owner can reach and change via
    the User Roles page like any other company's role)."""
    if is_super_admin:
        return True
    return page_key not in usable_page_keys_denied(session, role_id)


def protected_role_name(name):
    """True if this role name is load-bearing for the app itself (see
    STRUCTURALLY_REQUIRED_ROLE_NAMES) and must never be renamed away from
    or deleted, on the Default User Roles template page or anywhere else a
    role's name can be edited."""
    return (name or "").strip().lower() in STRUCTURALLY_REQUIRED_ROLE_NAMES


def page_visible(page_key, *, is_platform_owner, subscription, denied_keys, is_super_admin=False, unavailable_keys=None, certipur_enabled=False):
    """subscription may be None (no subscription assigned yet - treat as
    full access rather than locking a company out over a data gap).

    is_super_admin (see db.py's User.is_super_admin / can_use_page's
    docstring) short-circuits every other check here too, so a super-admin
    never has a nav item hidden out from under them by a role permission
    edit - the same escape-hatch reasoning as can_use_page, extended to
    visibility."""
    if is_super_admin:
        return True
    if page_key in PLATFORM_ONLY_KEYS:
        return bool(is_platform_owner)
    # CertiPUR Readiness is visible only to a company that has opted into it,
    # and to the platform owner, who needs to reach it to configure and support
    # it. Checked here rather than left to the page, so the menu item does not
    # appear for a customer who has not bought the add-on.
    #
    # Written as a NEGATIVE gate on purpose (corrected 2026-08-21, found while
    # building the CR section 20 evidence pack). The first version returned
    # `bool(is_platform_owner or certipur_enabled)` directly, which ended the
    # function - so once the add-on was on, `denied_keys` was never consulted
    # and role permission did not apply to this page at all. A company admin
    # could untick CertiPUR Readiness for a role, see the change saved, and
    # have it do nothing. The add-on decides whether the page EXISTS for the
    # company; the role still decides who inside the company may see it.
    if page_key == "certipur_readiness" and not (is_platform_owner or certipur_enabled):
        return False
    # Implementation scope, checked BEFORE subscription and role: a page the
    # customer was never implemented with is not a permission question, so it
    # should not be reachable by widening a role or changing a tier. Placed
    # after the platform-only gate on purpose, so that whatever a company has
    # switched off, HTC's own screens - including the one that does this
    # configuring - stay reachable.
    if unavailable_keys and page_key in unavailable_keys:
        return False
    if subscription is not None:
        if page_key in REPORT_KEYS and not subscription.reports_enabled:
            return False
    if page_key in denied_keys:
        return False
    return True
