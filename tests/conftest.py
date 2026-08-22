"""Shared configuration for the permanent regression suite.

What lives here
---------------
* the repository root on ``sys.path``, so the flat application imports work
  wherever pytest was started from;
* a run header that states the application version and, in particular, whether
  the PostgreSQL-marked checks are going to run or skip;
* fixtures every area may use.

What does not live here yet
---------------------------
The fail-closed isolation guard is work package 3 of the CR and is deliberately
not written here yet. Until it exists, nothing in this suite may be pointed at
a database that is not created by the test itself.
"""

from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    # pytest.ini sets pythonpath as well. This is here so that a developer who
    # runs a single file with `python -m pytest path/to/test.py` from somewhere
    # else still gets the same imports.
    sys.path.insert(0, PROJECT_ROOT)


def pytest_report_header(config):
    """State the two things a reader of a test run needs to know up front."""
    try:
        import version

        app = f"{version.APP_VERSION}"
    except Exception:  # noqa: BLE001 - the header must never break a run
        app = "unknown"

    db_url = os.environ.get("PI3_TEST_DB_URL")
    if db_url:
        # Never print the URL itself; it carries credentials.
        postgres = "PI3_TEST_DB_URL is set - PostgreSQL checks will run"
    else:
        postgres = (
            "PI3_TEST_DB_URL is NOT set - PostgreSQL checks will SKIP. "
            "A technical release may not be reported clean from this run."
        )

    return [f"PI3 Plant Edition - Flexible Foam, app v{app}", postgres]


@pytest.fixture
def project_root() -> str:
    """The repository root, for tests that read a source file off disk."""
    return PROJECT_ROOT


@pytest.fixture
def sqlite_session():
    """A private in-memory database with the full schema, torn down after use."""
    from tests.fixtures import sqlite_session as _make

    session = _make()
    try:
        yield session
    finally:
        session.close()
