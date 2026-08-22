"""Shared builders for the permanent suite.

Anything more than one test area needs belongs here rather than being copied.
The moved check modules in ``tests/checks/`` still carry their own private
copies of some of these helpers; those copies are collapsed onto this module as
each area is rewritten as ordinary pytest tests.
"""

from .database import sqlite_session
from .world import CompanyRows, World, two_company_world
from .tenancy import (
    InvalidIsolationContext,
    TenantContext,
    UnfilteredScope,
    isolation_tenant,
    platform_owner_all_companies,
    tenant,
)

__all__ = [
    "CompanyRows",
    "InvalidIsolationContext",
    "TenantContext",
    "UnfilteredScope",
    "platform_owner_all_companies",
    "isolation_tenant",
    "sqlite_session",
    "tenant",
    "two_company_world",
    "World",
]
