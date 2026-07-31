"""Shared page-visibility rules for the multi-tenant admin layer.

Three independent things can hide a page from the current user, checked in
this order by `page_visible()`:

1. Platform-only pages (Companies, Subscription Types) - only ever visible
   to a user whose company is the platform owner (HTC itself), regardless
   of role. Not configurable per role; this is a hard gate.
2. Subscription feature flags - a company's SubscriptionType can turn off
   PI3/AI and/or Reports for every user at that company, including its own
   admins. This is deliberately the ONLY page-level differentiator between
   HTC's two real commercial tiers ("PI3 Plant Edition" and "PI3 Plant
   Edition - Basic"): every page - including Recipe Optimization, Trend
   Analysis, Machine Settings Correlation, Root-Cause Assistant, Machine
   Settings Optimization, Similar Case Retrieval, and Expert Notes - stays
   visible on Basic, because each of those pages already has its own
   deterministic core that works with zero PI3 involvement (cost/diff
   calculations, correlation ranking, control charts/Cpk/CUSUM, the
   run-vs-prior-run diff, ...) and independently checks per-plant PI3
   enablement (ai_assistant.is_enabled_for_plant / any_plant_enabled)
   before rendering its own "Ask PI3" section. pi3_ai_enabled only hides
   the PI3 Connectivity admin page itself - which is what determines
   whether any plant at that company can ever have PI3 turned on in the
   first place, which is what every one of those per-page checks keys off.
   An earlier version of this file bundled those 7 pages behind their own
   feature flags (industrial_intelligence_enabled / case_review_enabled) -
   that was wrong: it hid real, PI3-independent value from Basic customers
   instead of letting each page's own PI3 check do its job.
3. Role page permissions - a DENY list, not an allow list (see db.py's
   RolePagePermission docstring): a role with no rows sees everything;
   an explicit can_view=False row for a page_key hides just that page for
   that role.

PAGE_CATALOG is the single source of truth for page_key -> display title,
used both to build app.py's nav and to render the permission checkbox grid
on the User Roles admin page.
"""

from db import RolePagePermission

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
    "maintenance_license_admin": "Maintenance & License Admin",
    "demo_data_admin": "Demo Data Admin",
    "pi3_ai_connectivity": "PI3 Connectivity",
    "companies_admin": "Companies",
    "subscription_types_admin": "Subscription Types",
    "user_roles_admin": "User Roles",
    "user_accounts_admin": "User Accounts",
}

PI3_AI_KEYS = frozenset({"pi3_ai_connectivity"})
REPORT_KEYS = frozenset({"report"})
PLATFORM_ONLY_KEYS = frozenset({"companies_admin", "subscription_types_admin"})


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


def page_visible(page_key, *, is_platform_owner, subscription, denied_keys):
    """subscription may be None (no subscription assigned yet - treat as
    full access rather than locking a company out over a data gap)."""
    if page_key in PLATFORM_ONLY_KEYS:
        return bool(is_platform_owner)
    if subscription is not None:
        if page_key in PI3_AI_KEYS and not subscription.pi3_ai_enabled:
            return False
        if page_key in REPORT_KEYS and not subscription.reports_enabled:
            return False
    if page_key in denied_keys:
        return False
    return True
