"""Fail-closed isolation: the suite refuses to run against anything real.

Work package 3 of the Permanent Automated Regression Test Suite CR.

The problem this solves
-----------------------
Nothing about running a test stops it connecting to production. Three specific
routes exist in this application, all of them verified rather than assumed:

1. ``db.ENGINE`` is built at import time from ``db._database_url()``, and that
   function reads ``st.secrets`` **before** the environment. Setting
   ``DATABASE_URL`` in the environment therefore does not win on any machine
   that has a ``.streamlit/secrets.toml``, and Streamlit Cloud always has one.

2. With nothing configured at all, ``_database_url()`` falls back to
   ``sqlite:///pi3_local.db`` - a file, and somebody's real local data.

3. Individual tests build their own engines with ``sqlalchemy.create_engine``.
   The migration-runner checks build a throwaway PostgreSQL database per case,
   from whatever ``PI3_TEST_DB_URL`` says. Nothing checks what it says.

The approach: neutralise, then verify
-------------------------------------
``tests/conftest.py`` removes the two hazards at their source before ``db`` is
imported for the first time - it empties ``st.secrets`` and points
``DATABASE_URL`` at an in-memory database.

Then this module **verifies the outcome rather than trusting the intent**. It
re-reads the engine that was actually built, and it wraps ``create_engine`` so
that every engine created during the run is checked as it is created. Anything
not recognisably ephemeral aborts the whole session.

It is an **allow-list**. A block-list of known production hostnames would pass
anything nobody thought of, which is the wrong way round for this.

What it cannot do
-----------------
It cannot tell that the server named in ``PI3_TEST_DB_URL`` is production. It
narrows that to one deliberate act by the operator, and it refuses outright if
that server is the same host as the configured production database - but an
operator who nominates production as the test server, with no production
configuration present to compare against, is outside what a guard can see.
That limit is stated here rather than left to be discovered.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import make_url

# A throwaway PostgreSQL database created by a test. Today only the
# migration-runner checks make these: "pi3_test_<pid>_<n>".
_EPHEMERAL_DB_NAME = re.compile(r"^pi3_test_\d+_\d+$")

# The maintenance database. A test needs it to CREATE and DROP the throwaway
# databases above; there is nowhere else to connect to do that. Allowed only on
# the exact server the operator nominated as the test server, and only when a
# test server was nominated at all.
_ADMIN_DB_NAME = "postgres"

_IN_MEMORY_SQLITE = {None, "", ":memory:"}


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str


class IsolationState:
    """What the guard was told, once, at the start of the session."""

    def __init__(self):
        self.test_server = None          # sa URL of PI3_TEST_DB_URL, if any
        self.production_hosts = set()    # hosts we know are not for testing
        self.installed = False
        self.original_create_engine = None
        self.storage_installed = False
        self.original_urlopen = None
        self.aborted = None

    def describe(self):
        lines = []
        if self.test_server is not None:
            lines.append(
                f"test PostgreSQL server: {self.test_server.host}:"
                f"{self.test_server.port or 5432}"
            )
        else:
            lines.append("test PostgreSQL server: none nominated")
        if self.production_hosts:
            lines.append("hosts refused as production: " + ", ".join(sorted(self.production_hosts)))
        return lines


STATE = IsolationState()


def _host_of(url_text):
    try:
        return (make_url(url_text).host or "").lower() or None
    except Exception:  # noqa: BLE001 - an unparseable URL is not a host
        return None


def remember_production_host(url_text):
    """Record a host that must never be connected to during a test run."""
    host = _host_of(url_text)
    if host:
        STATE.production_hosts.add(host)


def nominate_test_server(url_text):
    """Record the PostgreSQL server the operator offered for testing."""
    if not url_text:
        STATE.test_server = None
        return
    try:
        STATE.test_server = make_url(url_text)
    except Exception:  # noqa: BLE001
        STATE.test_server = None


def verdict(url) -> Verdict:
    """Is this engine URL one a test is allowed to open?

    Allow-list. Everything that is not positively recognised is refused.
    """
    try:
        url = make_url(url) if isinstance(url, str) else url
    except Exception as exc:  # noqa: BLE001
        return Verdict(False, f"the URL could not be parsed ({exc})")

    backend = url.get_backend_name()
    host = (url.host or "").lower() or None

    # Rule 0, before anything else: a host known to be production is refused
    # however ephemeral the database name on the end of it looks.
    if host and host in STATE.production_hosts:
        return Verdict(
            False,
            f"{host} is the host of the configured production database. "
            "A test may not connect to it under any name.",
        )

    if backend == "sqlite":
        if url.database in _IN_MEMORY_SQLITE:
            return Verdict(True, "in-memory SQLite")
        return Verdict(
            False,
            f"SQLite file {url.database!r}. A test must not read or write a "
            "database file - that is somebody's real local data, and it "
            "survives the test. Use an in-memory database.",
        )

    if backend in ("postgresql", "postgres"):
        if STATE.test_server is None:
            return Verdict(
                False,
                "a PostgreSQL connection, but no test server was nominated. "
                "Set PI3_TEST_DB_URL to a server whose databases may be "
                "created and dropped freely.",
            )
        server = STATE.test_server
        same_server = (
            (server.host or "").lower() == (host or "")
            and (server.port or 5432) == (url.port or 5432)
            and (server.username or "") == (url.username or "")
        )
        if not same_server:
            return Verdict(
                False,
                f"PostgreSQL at {host}:{url.port or 5432} as "
                f"{url.username!r} is not the server nominated in "
                "PI3_TEST_DB_URL.",
            )
        if url.database == _ADMIN_DB_NAME:
            return Verdict(True, "the maintenance database on the nominated test server")
        if url.database and _EPHEMERAL_DB_NAME.match(url.database):
            return Verdict(True, "a throwaway test database on the nominated test server")
        return Verdict(
            False,
            f"database {url.database!r} on the test server is not a throwaway "
            "one. A test may only open a database it created itself "
            "(pi3_test_<pid>_<n>) or the maintenance database.",
        )

    return Verdict(
        False,
        f"{backend!r} is not a backend this suite knows how to isolate. "
        "Nothing outside the allow-list is permitted.",
    )


def _refuse(url, reason, where):
    """Abort the whole session. Not this test - the session."""
    try:
        shown = make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001
        shown = "<unparseable>"

    message = "\n".join(
        [
            "",
            "=" * 78,
            "TEST ISOLATION GUARD - SESSION ABORTED",
            "=" * 78,
            f"Refused: {shown}",
            f"Where:   {where}",
            f"Why:     {reason}",
            "",
            *STATE.describe(),
            "",
            "The suite fails closed. Nothing that is not recognisably ephemeral",
            "is allowed, so an unrecognised database is refused rather than",
            "assumed safe. No test ran against it.",
            "=" * 78,
            "",
        ]
    )
    STATE.aborted = message

    import pytest

    pytest.exit(message, returncode=3)


def install():
    """Wrap create_engine so every engine is checked as it is created."""
    if STATE.installed:
        return
    STATE.original_create_engine = sa.create_engine

    def guarded_create_engine(*args, **kwargs):
        if args:
            called_with = args[0]
            check = verdict(called_with)
            if not check.allowed:
                _refuse(called_with, check.reason, "sqlalchemy.create_engine")
        return STATE.original_create_engine(*args, **kwargs)

    guarded_create_engine.__wrapped__ = STATE.original_create_engine
    sa.create_engine = guarded_create_engine
    if hasattr(sa.engine, "create_engine"):
        sa.engine.create_engine = guarded_create_engine
    STATE.installed = True


def uninstall():
    """Only used by the tests that prove the guard."""
    if not STATE.installed:
        return
    sa.create_engine = STATE.original_create_engine
    if hasattr(sa.engine, "create_engine"):
        sa.engine.create_engine = STATE.original_create_engine
    STATE.installed = False


def verify_module_engine():
    """Re-read the engine ``db`` actually built, and refuse if it is not ours.

    This is the part that survives ``st.secrets`` outranking the environment.
    conftest sets the variable; this checks what was built from it.
    """
    import db

    check = verdict(db.ENGINE.url)
    if not check.allowed:
        _refuse(db.ENGINE.url, check.reason, "db.ENGINE, built at import time")
    return check


# --- the evidence store -----------------------------------------------------
#
# Charlie, 22 August 2026, guard A: "test evidence and fixture documents never
# enter live evidence stores."
#
# The database guard above says nothing about object storage, and regulatory
# originals are evidence in exactly the sense that matters. regulatory_storage
# reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY the same way db reads its
# settings - st.secrets first, then the environment - and PUTs the file into
# the "regulatory-sources" bucket over HTTP. conftest empties st.secrets, so
# with those two variables in the environment of a developer machine or a CI
# runner, a test that called put_original() without injecting a transport
# would write a fixture file into the live evidence store. Nothing today does;
# nothing stopped it either.
#
# Same shape as the database guard: neutralise, then verify. conftest removes
# the two variables; this wraps the single point where the module would
# actually reach the network, and aborts the session if anything gets there.

_STORAGE_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")


def forget_storage_credentials(env):
    """Take the live evidence store out of reach before anything reads it."""
    for name in _STORAGE_ENV:
        env.pop(name, None)


def install_storage_guard():
    """Abort the session if a test would really talk to the evidence store.

    A test that injects a transport - which is how every storage case in this
    suite is written - never reaches this. A test that forgets to is stopped
    here rather than discovered in the bucket.
    """
    if STATE.storage_installed:
        return
    import urllib.request

    STATE.original_urlopen = urllib.request.urlopen

    def guarded_urlopen(req, *args, **kwargs):
        url = getattr(req, "full_url", None) or (req if isinstance(req, str) else "?")
        _refuse(
            "sqlite://",
            "a test tried to reach the object store over the network at "
            f"{url}. Regulatory originals are evidence: a fixture file written "
            "into a live bucket is indistinguishable from a real one "
            "afterwards. Inject a transport into the storage call instead.",
            "urllib.request.urlopen, from a test",
        )

    urllib.request.urlopen = guarded_urlopen
    STATE.storage_installed = True


def uninstall_storage_guard():
    """Only used by the tests that prove the guard."""
    if not STATE.storage_installed:
        return
    import urllib.request

    urllib.request.urlopen = STATE.original_urlopen
    STATE.storage_installed = False


def verify_storage_out_of_reach():
    """Confirm the evidence store is not configured for this session."""
    import regulatory_storage

    if regulatory_storage.is_configured():
        _refuse(
            "sqlite://",
            "the live evidence store is configured for this test session. "
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must not be readable "
            "while tests run.",
            "regulatory_storage.is_configured()",
        )
    return True


def rebind_to_shared_memory():
    """Give the application one in-memory database that every thread can see.

    ``db._database_url()`` returns ``sqlite://`` here, and SQLAlchemy's default
    pool for an in-memory SQLite is ``SingletonThreadPool``: one connection per
    thread, and each connection to ``sqlite://`` gets its **own** empty
    database. That is fine while everything runs on one thread, and wrong the
    moment it does not - ``streamlit.testing.v1.AppTest`` runs the page script
    on a separate thread, so a page would see an empty database however
    carefully a test had seeded it.

    ``StaticPool`` keeps one connection for the life of the engine and hands
    the same one to every thread, so the seed and the page agree about what
    exists.

    The new engine is created through the wrapped ``create_engine``, so it is
    checked by the allow-list exactly like any other.
    """
    from sqlalchemy.pool import StaticPool

    import db

    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db.ENGINE = engine
    db.SessionLocal.configure(bind=engine)
    db.Base.metadata.create_all(engine)
    return engine


def verify_auth_not_disabled():
    """AUTH_DISABLED changes what a visibility test proves, so refuse it.

    With AUTH_DISABLED = true the application logs in a synthetic platform
    owner: ``is_super_admin`` True and ``company_id`` None. ``tenant_scope``
    treats a None company_id as UNFILTERED. A company-isolation test written
    the obvious way then compares unfiltered with unfiltered and passes
    whatever the code does. It has already produced one false pass in this
    project, which is why this is checked rather than trusted.
    """
    import auth

    if auth._auth_disabled():
        _refuse(
            "sqlite://",
            "AUTH_DISABLED is true. It makes every session a platform owner "
            "with an unfiltered company scope, so an isolation test would "
            "compare unfiltered with unfiltered and pass whatever the code "
            "does.",
            "auth._auth_disabled()",
        )
    return True
