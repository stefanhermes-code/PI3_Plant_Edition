"""Shared configuration for the permanent regression suite.

Read the ordering below before changing anything in this file. It is not
stylistic: ``db.ENGINE`` is created when ``db`` is first imported, so anything
that decides which database the suite talks to has to happen before that, and
this module is the only thing guaranteed to run first.

Order of operations
-------------------
1. Learn what production looks like, from the configuration as it stands.
   This has to happen before step 2 destroys the evidence.
2. Empty ``st.secrets``. ``db._database_url()`` reads secrets *before* the
   environment, and ``auth._auth_disabled()`` reads secrets only, so on a
   machine with a ``.streamlit/secrets.toml`` neither can be steered by an
   environment variable. Emptying it is what makes the next step effective.
3. Point ``DATABASE_URL`` at an in-memory database.
4. Wrap ``create_engine``, so every engine opened during the run is checked as
   it is opened rather than after the fact.
5. At session start, verify the outcome rather than trust the intent: re-read
   the engine ``db`` actually built, and confirm AUTH_DISABLED is off.

Steps 1 to 4 run at import. Step 5 runs in ``pytest_sessionstart``.
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

from tests import isolation  # noqa: E402 - must follow the sys.path line above

def pytest_ignore_collect(collection_path, config):
    """Never collect a guard probe unless this run *is* the guard probe.

    The tests that prove the isolation guard write a throwaway test into
    ``tests/_guard_probe_<pid>/`` and run pytest on it in a subprocess. That
    test is meant to abort the session, so a normal run must not pick it up if
    a crash ever leaves the directory behind.
    """
    if "_guard_probe" in str(collection_path):
        return os.environ.get("PI3_GUARD_PROBE") != "1"
    return None


# --- 1. Learn what production looks like, before removing the evidence ------
def _learn_production():
    """Record the hosts a test must never touch. Hostnames only."""
    for source in (os.environ.get("DATABASE_URL"), _secret("DATABASE_URL")):
        if source:
            isolation.remember_production_host(source)
    for store in (os.environ.get("SUPABASE_URL"), _secret("SUPABASE_URL")):
        if store:
            isolation.remember_production_host(store)
    isolation.nominate_test_server(os.environ.get("PI3_TEST_DB_URL"))


def _secret(name):
    try:
        import streamlit as st

        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # noqa: BLE001 - no secrets file at all is the normal case
        pass
    return None


_learn_production()


# --- 2. Empty st.secrets ----------------------------------------------------
class _NoSecrets(dict):
    """An empty stand-in for ``st.secrets``.

    Supports the two shapes the application uses - ``"KEY" in st.secrets`` and
    ``st.secrets.get("KEY", default)`` - and holds nothing, so every caller
    takes its no-configuration path.
    """


def _empty_streamlit_secrets():
    try:
        import streamlit as st

        st.secrets = _NoSecrets()
    except Exception:  # noqa: BLE001 - verify_module_engine catches the fallout
        pass


if "db" in sys.modules:  # pragma: no cover - would mean something imported first
    raise RuntimeError(
        "db was imported before tests/conftest.py ran. db.ENGINE is built at "
        "import time, so the suite can no longer choose its own database. "
        "Whatever imported it must not be imported at collection time."
    )

_empty_streamlit_secrets()


# --- 3. Point the application at an in-memory database, and put the live -----
#        evidence store out of reach
os.environ["DATABASE_URL"] = "sqlite://"
isolation.forget_storage_credentials(os.environ)


# --- 4. Check every engine as it is opened, and every trip to the store ------
isolation.install()
isolation.install_storage_guard()


# --- 5. Verify the outcome --------------------------------------------------
def pytest_sessionstart(session):
    isolation.verify_module_engine()
    isolation.verify_auth_not_disabled()
    isolation.verify_storage_out_of_reach()
    # One in-memory database the AppTest thread can see too. See the function's
    # docstring for why the default pool is not good enough. Checked by the
    # allow-list like any other engine.
    isolation.rebind_to_shared_memory()
    isolation.verify_module_engine()


def pytest_report_header(config):
    """State the things a reader of a test run needs to know up front."""
    try:
        import version

        app = version.APP_VERSION
    except Exception:  # noqa: BLE001 - the header must never break a run
        app = "unknown"

    try:
        import db

        engine = db.ENGINE.url.render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001
        engine = "not built yet"

    if os.environ.get("PI3_TEST_DB_URL"):
        postgres = "PI3_TEST_DB_URL is set - PostgreSQL checks will run"
    else:
        postgres = (
            "PI3_TEST_DB_URL is NOT set - PostgreSQL checks will SKIP. "
            "A technical release may not be reported clean from this run."
        )

    return [
        f"PI3 Plant Edition - Flexible Foam, app v{app}",
        f"isolation: db.ENGINE -> {engine}",
        postgres,
    ]


# --- fixtures ---------------------------------------------------------------
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


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    """Empty st.cache_data around every test.

    Several access-control and scope helpers are decorated with
    ``@st.cache_data`` and keyed on ``company_id`` or ``role_id`` alone - the
    session is underscore-prefixed so Streamlit does not try to hash it. Two
    tests that both use company 1, against two different in-memory databases,
    would otherwise see each other's answers. Autouse because remembering it
    per test is exactly the kind of thing nobody remembers.
    """
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture
def world(sqlite_session):
    """Two fully populated companies plus one with nothing in it."""
    from tests.fixtures import two_company_world

    return two_company_world(sqlite_session)


@pytest.fixture
def tenant():
    """Build a company-scoped context. Refuses to build an unfiltered one.

    See ``tests/fixtures/tenancy.py`` for why that refusal matters.
    """
    from tests.fixtures import tenant as _tenant

    return _tenant
