"""Company isolation, written so that a passing test means something.

Work package 4 of the Permanent Automated Regression Test Suite CR.

Every case here uses two fully populated companies. With only one company in
the database, a query that ignores the company filter entirely returns exactly
what a correctly scoped query returns, and the test passes while proving
nothing. That is not a hypothetical: it is how a false pass was produced in
this project on 20 August.

Each case therefore asserts both halves - what the company may see, and what
it may **not** see. A test that only asserts the first half is not an isolation
test.
"""

from __future__ import annotations

import pytest

import access_control as ac
import db as m
import tenant_scope as ts
from tests.fixtures import (
    InvalidIsolationContext,
    UnfilteredScope,
    isolation_tenant,
    platform_owner_all_companies,
    tenant,
)


# --- the scope resolvers ----------------------------------------------------

SCOPES = [
    ("plants", lambda s, cid: ts.plant_ids_for_company(s, cid), "plant_id"),
    ("foam grades", lambda s, cid: ts.grade_ids_for_company(s, cid), "grade_id"),
    ("production runs", lambda s, cid: ts.run_ids_for_company(s, cid), "run_id"),
    (
        "customer trials",
        lambda s, cid: ts.customer_trial_ids_for_company(s, cid),
        "customer_trial_id",
    ),
    (
        "optimization trials",
        lambda s, cid: ts.optimization_trial_ids_for_company(s, cid),
        "optimization_trial_id",
    ),
]


@pytest.mark.parametrize("label,resolve,attribute", SCOPES, ids=[s[0] for s in SCOPES])
def test_a_company_resolves_its_own_rows(world, label, resolve, attribute):
    ctx = isolation_tenant(company_id=world.a.company_id)
    ids = resolve(world.session, ctx.company_id)
    assert getattr(world.a, attribute) in ids


@pytest.mark.parametrize("label,resolve,attribute", SCOPES, ids=[s[0] for s in SCOPES])
def test_a_company_does_not_resolve_the_other_companys_rows(world, label, resolve, attribute):
    """The denial half. This is the assertion that makes the pair meaningful."""
    ctx = isolation_tenant(company_id=world.a.company_id)
    ids = resolve(world.session, ctx.company_id)
    assert getattr(world.b, attribute) not in ids


@pytest.mark.parametrize("label,resolve,attribute", SCOPES, ids=[s[0] for s in SCOPES])
def test_the_denial_holds_in_the_other_direction_too(world, label, resolve, attribute):
    """B must not see A either. A filter that happens to work one way round
    because of insertion order is not a filter."""
    ctx = isolation_tenant(company_id=world.b.company_id)
    ids = resolve(world.session, ctx.company_id)
    assert getattr(world.b, attribute) in ids
    assert getattr(world.a, attribute) not in ids


# --- the real cross-company denial, at the query level ----------------------

def test_a_scoped_query_returns_this_companys_run_and_not_the_others(world):
    """Not the id list - the rows a page would actually put on screen."""
    ctx = isolation_tenant(company_id=world.a.company_id)
    run_ids = ts.run_ids_for_company(world.session, ctx.company_id)

    query = ts.apply_scope(
        world.session.query(m.ProductionRun), m.ProductionRun.id, run_ids
    )
    visible = {run.id for run in query.all()}

    assert world.a.run_id in visible
    assert world.b.run_id not in visible
    assert len(visible) == 1


def test_the_same_query_unscoped_would_have_shown_both(world):
    """What the failure looks like, stated once so the pair above is readable.

    If ``apply_scope`` were skipped - or handed the unfiltered sentinel by
    accident - this is what the page would show. The test above is only
    meaningful because this one is true.
    """
    visible = {run.id for run in world.session.query(m.ProductionRun).all()}
    assert visible == {world.a.run_id, world.b.run_id}


def test_a_company_with_no_plants_sees_nothing_rather_than_everything(world):
    """The dangerous case. An empty scope must filter to zero rows.

    ``None`` means unfiltered and ``[]`` means "this company has none of these
    yet". Treating the second as the first is how a brand-new company would be
    shown every other company's production runs on its first login.
    """
    ctx = isolation_tenant(company_id=world.empty_company_id)
    run_ids = ts.run_ids_for_company(world.session, ctx.company_id)
    assert run_ids == []

    query = ts.apply_scope(
        world.session.query(m.ProductionRun), m.ProductionRun.id, run_ids
    )
    assert query.all() == []


def test_the_platform_owner_sees_everything_and_has_to_ask_for_it_by_name(world):
    """The unfiltered case is legitimate. It is not the default."""
    ctx = platform_owner_all_companies()
    assert ctx.is_unfiltered

    run_ids = ts.run_ids_for_company(world.session, ctx.company_id)
    assert run_ids is None  # the unfiltered sentinel, not an empty list

    query = ts.apply_scope(
        world.session.query(m.ProductionRun), m.ProductionRun.id, run_ids
    )
    assert {run.id for run in query.all()} == {world.a.run_id, world.b.run_id}


def test_a_context_with_no_company_cannot_be_built_by_accident():
    """The guard against the false pass, at the point a test is written."""
    with pytest.raises(UnfilteredScope) as raised:
        tenant(company_id=None)
    assert "UNFILTERED" in str(raised.value)


def test_an_isolation_test_cannot_be_written_without_a_company():
    with pytest.raises(InvalidIsolationContext) as raised:
        isolation_tenant(company_id=None)
    assert "UNFILTERED" in str(raised.value)


@pytest.mark.parametrize("flag", ["platform_owner", "super_admin"])
def test_an_isolation_test_cannot_be_written_as_a_scope_bypassing_user(flag):
    """Both states bypass company scoping by design.

    An isolation assertion made in either would hold whatever the scoping code
    did - the same false pass as leaving company_id unset, one step further
    along. It fails here, before the business assertion is reached.
    """
    with pytest.raises(InvalidIsolationContext) as raised:
        isolation_tenant(company_id=1, **{flag: True})
    assert "bypasses company scoping" in str(raised.value)


def test_the_strict_rule_is_not_a_ban_on_those_contexts_everywhere():
    """Access-control tests legitimately need them, and still get them."""
    assert tenant(company_id=1, platform_owner=True).is_platform_owner
    assert tenant(company_id=1, super_admin=True).is_super_admin
    assert platform_owner_all_companies().is_unfiltered


def test_a_platform_owner_scoped_to_one_company_is_still_scoped(world):
    """Being the platform owner is not the same as viewing all companies."""
    ctx = tenant(company_id=world.a.company_id, platform_owner=True)
    assert not ctx.is_unfiltered

    run_ids = ts.run_ids_for_company(world.session, ctx.company_id)
    assert run_ids == [world.a.run_id]


# --- implementation scope is company-scoped too -----------------------------

def test_one_companys_switched_off_page_does_not_affect_another(world):
    """CompanyPageAvailability rows must not leak across companies."""
    page = sorted(ac.CONFIGURABLE_PAGE_KEYS)[0]
    world.session.add(
        m.CompanyPageAvailability(
            company_id=world.a.company_id, page_key=page, available=False
        )
    )
    world.session.commit()

    assert page in ac.unavailable_page_keys(world.session, world.a.company_id)
    assert page not in ac.unavailable_page_keys(world.session, world.b.company_id)


def test_a_stale_row_for_a_non_configurable_page_cannot_hide_it(world):
    """A page the customer may not switch off stays visible even with a row."""
    page = sorted(ac.NON_CONFIGURABLE_PAGE_KEYS)[0]
    world.session.add(
        m.CompanyPageAvailability(
            company_id=world.a.company_id, page_key=page, available=False
        )
    )
    world.session.commit()

    assert page not in ac.unavailable_page_keys(world.session, world.a.company_id)


def test_an_unscoped_company_id_returns_no_restrictions(world):
    """company_id None is the platform owner viewing unscoped. Full application."""
    world.session.add(
        m.CompanyPageAvailability(
            company_id=world.a.company_id,
            page_key=sorted(ac.CONFIGURABLE_PAGE_KEYS)[0],
            available=False,
        )
    )
    world.session.commit()
    assert ac.unavailable_page_keys(world.session, None) == set()


# --- role permissions -------------------------------------------------------

def test_one_roles_denial_does_not_touch_another_role(sqlite_session):
    viewer = m.Role(name="Viewer")
    operator = m.Role(name="Operator")
    sqlite_session.add_all([viewer, operator])
    sqlite_session.flush()
    page = sorted(ac.CONFIGURABLE_PAGE_KEYS)[0]
    sqlite_session.add(
        m.RolePagePermission(role_id=viewer.id, page_key=page, can_view=False)
    )
    sqlite_session.commit()

    assert page in ac.denied_page_keys(sqlite_session, viewer.id)
    assert page not in ac.denied_page_keys(sqlite_session, operator.id)


def test_a_role_that_cannot_use_a_page_is_told_so(sqlite_session):
    role = m.Role(name="Read Only")
    sqlite_session.add(role)
    sqlite_session.flush()
    page = sorted(ac.CONFIGURABLE_PAGE_KEYS)[0]
    sqlite_session.add(
        m.RolePagePermission(role_id=role.id, page_key=page, can_view=True, can_use=False)
    )
    sqlite_session.commit()

    assert not ac.can_use_page(page, role_id=role.id, session=sqlite_session)


def test_a_super_admin_is_never_locked_out_of_a_page(sqlite_session):
    """The deliberate escape hatch, asserted so it cannot be removed silently."""
    role = m.Role(name="Read Only")
    sqlite_session.add(role)
    sqlite_session.flush()
    page = sorted(ac.CONFIGURABLE_PAGE_KEYS)[0]
    sqlite_session.add(
        m.RolePagePermission(role_id=role.id, page_key=page, can_view=False, can_use=False)
    )
    sqlite_session.commit()

    assert ac.can_use_page(
        page, role_id=role.id, session=sqlite_session, is_super_admin=True
    )


# --- page visibility --------------------------------------------------------

def visible(page_key, **overrides):
    kwargs = {
        "is_platform_owner": False,
        "subscription": None,
        "denied_keys": frozenset(),
        "is_super_admin": False,
        "unavailable_keys": frozenset(),
        "certipur_enabled": False,
    }
    kwargs.update(overrides)
    return ac.page_visible(page_key, **kwargs)


@pytest.mark.parametrize("page_key", sorted(ac.PLATFORM_ONLY_KEYS))
def test_a_platform_only_page_is_hidden_from_a_customer(page_key):
    assert not visible(page_key)
    assert visible(page_key, is_platform_owner=True)


def test_implementation_scope_beats_a_role_that_grants_the_page():
    page = sorted(ac.CONFIGURABLE_PAGE_KEYS)[0]
    assert visible(page)
    assert not visible(page, unavailable_keys=frozenset({page}))


def test_implementation_scope_does_not_hide_a_page_it_was_not_given():
    page = sorted(ac.CONFIGURABLE_PAGE_KEYS)[0]
    other = sorted(ac.CONFIGURABLE_PAGE_KEYS)[1]
    assert visible(page, unavailable_keys=frozenset({other}))


def test_a_role_denial_hides_the_page():
    page = sorted(ac.CONFIGURABLE_PAGE_KEYS)[0]
    assert not visible(page, denied_keys=frozenset({page}))


def test_no_subscription_is_treated_as_full_access_not_as_a_lockout():
    """A data gap must not lock a company out of its own application."""
    for page_key in sorted(ac.REPORT_KEYS):
        assert visible(page_key, subscription=None)


def test_a_subscription_without_reports_hides_only_the_report_pages():
    class NoReports:
        reports_enabled = False

    for page_key in sorted(ac.REPORT_KEYS):
        assert not visible(page_key, subscription=NoReports())

    unaffected = sorted(set(ac.CONFIGURABLE_PAGE_KEYS) - set(ac.REPORT_KEYS))
    assert unaffected, "the fixture would be vacuous if every page were a report"
    for page_key in unaffected:
        assert visible(page_key, subscription=NoReports())
