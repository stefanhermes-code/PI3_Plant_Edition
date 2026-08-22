"""Company scope for tests, built so the false pass cannot be written.

Why this exists
---------------
On 20 August 2026 a company-isolation test in this project passed while
proving nothing. The reason is worth stating exactly, because it is not
obvious and it will recur:

``AUTH_DISABLED = true`` logs in a synthetic platform owner. That sets
``is_super_admin`` True **and** leaves ``company_id`` as ``None``. Throughout
``tenant_scope`` and ``access_control``, ``None`` is not "no company" - it is
the sentinel for **unfiltered**, the platform owner viewing all companies.

So a test that builds a context the obvious way, leaves ``company_id`` unset,
and then asserts that company A cannot see company B's rows is comparing an
unfiltered query with an unfiltered query. It passes whatever the code does,
including code that leaks everything.

The rule this module enforces
-----------------------------
A test context must name a real company. Wanting the unfiltered case is
legitimate - the platform owner really does see everything - but it has to be
asked for by name, so it appears in the test and a reader can see it was
meant.

    ctx = tenant(company_id=1)                    # scoped, the normal case
    ctx = tenant(company_id=1, platform_owner=True)  # owner, still scoped
    ctx = platform_owner_all_companies()          # deliberately unfiltered
    ctx = tenant(company_id=None)                 # raises UnfilteredScope
"""

from __future__ import annotations

from dataclasses import dataclass


class UnfilteredScope(RuntimeError):
    """Raised when a test asks for a context with no company scope."""


@dataclass(frozen=True)
class TenantContext:
    """What the application needs to know to decide what a person may see."""

    company_id: int | None
    is_platform_owner: bool = False
    is_super_admin: bool = False

    @property
    def is_unfiltered(self) -> bool:
        return self.company_id is None

    def as_kwargs(self) -> dict:
        """The keyword form the access-control helpers take."""
        return {
            "company_id": self.company_id,
            "is_platform_owner": self.is_platform_owner,
            "is_super_admin": self.is_super_admin,
        }


def tenant(company_id, *, platform_owner: bool = False, super_admin: bool = False) -> TenantContext:
    """A context scoped to one company.

    ``company_id`` may not be None. If a test wants the unfiltered platform
    owner, it calls :func:`platform_owner_all_companies` and says so.
    """
    if company_id is None:
        raise UnfilteredScope(
            "company_id=None is the UNFILTERED sentinel, not an empty scope. "
            "A test that leaves it unset compares unfiltered with unfiltered "
            "and passes whatever the code does. Name a real company, or call "
            "platform_owner_all_companies() if the unfiltered case is what is "
            "being tested."
        )
    return TenantContext(
        company_id=company_id,
        is_platform_owner=platform_owner,
        is_super_admin=super_admin,
    )


def platform_owner_all_companies() -> TenantContext:
    """The deliberately unfiltered context, named so it cannot happen by accident."""
    return TenantContext(company_id=None, is_platform_owner=True, is_super_admin=False)
