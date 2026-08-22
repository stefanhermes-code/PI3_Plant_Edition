"""The fail-closed isolation guard, proved rather than described.

Work package 3 of the Permanent Automated Regression Test Suite CR.

Two halves. The first exercises the allow-list decision directly: what does the
guard say about this URL, and why. The second is the part that matters - it
starts a real pytest session, has it open a database it must not open, and
asserts the session aborted. A safety mechanism nobody has watched fail is not
evidence that it works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests import isolation

PROJECT_ROOT = Path(isolation.__file__).resolve().parent.parent

TEST_SERVER = "postgresql+psycopg2://tester:pw@test-db.internal:5432"
PRODUCTION = "postgresql://postgres:pw@db.aazkdsqpytjciiqtvnfj.supabase.co:5432/postgres"


@pytest.fixture
def state():
    """Drive the guard's recorded state without disturbing the live session."""
    saved = (isolation.STATE.test_server, set(isolation.STATE.production_hosts))
    isolation.nominate_test_server(TEST_SERVER)
    isolation.STATE.production_hosts = {"db.aazkdsqpytjciiqtvnfj.supabase.co"}
    try:
        yield isolation.STATE
    finally:
        isolation.STATE.test_server = saved[0]
        isolation.STATE.production_hosts = saved[1]


# --- the allow-list decision ------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite+pysqlite:///:memory:",
    ],
)
def test_in_memory_sqlite_is_allowed(state, url):
    assert isolation.verdict(url).allowed


def test_a_sqlite_file_is_refused(state):
    """The no-configuration fallback in db._database_url() is a file."""
    result = isolation.verdict("sqlite:///pi3_local.db")
    assert not result.allowed
    assert "pi3_local.db" in result.reason


def test_the_production_host_is_refused_whatever_the_database_is_called(state):
    """Rule 0. An ephemeral-looking name on a production host is still production."""
    result = isolation.verdict(
        "postgresql://postgres:pw@db.aazkdsqpytjciiqtvnfj.supabase.co:5432/pi3_test_1_1"
    )
    assert not result.allowed
    assert "production" in result.reason


def test_a_postgres_url_is_refused_when_no_test_server_was_nominated():
    saved = isolation.STATE.test_server
    isolation.STATE.test_server = None
    try:
        result = isolation.verdict("postgresql://u:p@somewhere:5432/pi3_test_1_1")
        assert not result.allowed
        assert "PI3_TEST_DB_URL" in result.reason
    finally:
        isolation.STATE.test_server = saved


def test_another_server_is_refused_even_if_the_name_looks_ephemeral(state):
    result = isolation.verdict("postgresql://tester:pw@elsewhere:5432/pi3_test_1_1")
    assert not result.allowed
    assert "not the server nominated" in result.reason


def test_a_different_user_on_the_right_host_is_refused(state):
    result = isolation.verdict("postgresql://root:pw@test-db.internal:5432/pi3_test_1_1")
    assert not result.allowed


def test_a_real_looking_database_on_the_test_server_is_refused(state):
    """Right server, wrong database. A test may only open one it created."""
    result = isolation.verdict("postgresql://tester:pw@test-db.internal:5432/pi3_production")
    assert not result.allowed
    assert "throwaway" in result.reason


def test_the_throwaway_pattern_is_allowed(state):
    assert isolation.verdict(
        "postgresql://tester:pw@test-db.internal:5432/pi3_test_4242_1"
    ).allowed


def test_the_maintenance_database_is_allowed_on_the_test_server(state):
    """The one deliberate hole: there is nowhere else to CREATE DATABASE from."""
    assert isolation.verdict(
        "postgresql://tester:pw@test-db.internal:5432/postgres"
    ).allowed


def test_an_unknown_backend_is_refused(state):
    """Allow-list, not block-list: what nobody thought of is refused."""
    result = isolation.verdict("mysql+pymysql://u:p@host/db")
    assert not result.allowed
    assert "allow-list" in result.reason


def test_an_unparseable_url_is_refused(state):
    assert not isolation.verdict("this is not a url").allowed


def test_the_refusal_message_never_prints_the_password(state):
    """A refusal is printed and pasted into reports. It must not leak a secret."""
    with pytest.raises(BaseException) as raised:
        isolation._refuse(PRODUCTION, "test", "test")
    assert "pw" not in str(raised.value).replace("password", "")
    assert "***" in str(raised.value)


# --- proof by refusal -------------------------------------------------------

PROBE_PREAMBLE = "import sqlalchemy as sa\n\n\n"


def run_probe(body: str) -> subprocess.CompletedProcess:
    """Run one throwaway test in a real pytest session and return the result.

    The probe lives under ``tests/`` so that ``tests/conftest.py`` - the thing
    being proved - is loaded exactly as it is in a normal run.
    """
    probe_dir = PROJECT_ROOT / "tests" / f"_guard_probe_{os.getpid()}"
    if probe_dir.exists():
        shutil.rmtree(probe_dir)
    probe_dir.mkdir()
    (probe_dir / "__init__.py").write_text("")
    (probe_dir / "test_probe.py").write_text(PROBE_PREAMBLE + textwrap.dedent(body))
    env = dict(os.environ, PI3_GUARD_PROBE="1")
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             str(probe_dir / "test_probe.py")],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, env=env, timeout=180,
        )
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_the_probe_harness_itself_passes_a_clean_test():
    """The control. Without it, every refusal below could be the harness failing."""
    result = run_probe(
        """
        def test_in_memory_is_fine():
            engine = sa.create_engine("sqlite://")
            assert engine.url.database in (None, "")
        """
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_the_session_aborts_when_a_test_opens_the_production_database():
    """The one that matters."""
    result = run_probe(
        f"""
        def test_opens_production():
            sa.create_engine({PRODUCTION!r})
        """
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "TEST ISOLATION GUARD - SESSION ABORTED" in output
    assert "1 passed" not in output
    # and it named the reason rather than dying obscurely
    assert "PI3_TEST_DB_URL" in output or "production" in output
    # and it did not print the password
    assert ":pw@" not in output


def test_the_session_aborts_when_a_test_opens_a_sqlite_file():
    result = run_probe(
        """
        def test_opens_the_local_file():
            sa.create_engine("sqlite:///pi3_local.db")
        """
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "TEST ISOLATION GUARD - SESSION ABORTED" in output
    assert "somebody's real local data" in output


def test_the_session_aborts_when_db_engine_itself_points_at_production():
    """The st.secrets route: the variable said one thing, the engine is another.

    This is why the guard re-reads the engine that was built instead of
    trusting the environment variable that was meant to build it.
    """
    result = run_probe(
        f"""
        import db
        import sqlalchemy
        from tests import isolation

        def test_engine_is_verified_not_assumed():
            # Exactly what pytest_sessionstart calls, against an engine built
            # the way st.secrets would have built it.
            db.ENGINE = sqlalchemy.engine.create.create_engine({PRODUCTION!r})
            isolation.verify_module_engine()
        """
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "TEST ISOLATION GUARD - SESSION ABORTED" in output
    assert "db.ENGINE" in output


def test_the_session_aborts_when_auth_is_disabled():
    """AUTH_DISABLED makes an isolation test compare unfiltered with unfiltered."""
    result = run_probe(
        """
        import auth
        from tests import isolation

        def test_auth_disabled_is_refused():
            auth._auth_disabled = lambda: True
            isolation.verify_auth_not_disabled()
        """
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "TEST ISOLATION GUARD - SESSION ABORTED" in output
    assert "AUTH_DISABLED" in output


def test_the_sessionstart_hook_is_the_thing_that_calls_the_verification():
    """Proving the check works is not proving anything calls it.

    ``pytest_sessionstart`` is a pytest contract, and the run header from the
    same conftest demonstrably fires, so conftest is loaded. What is left to
    prove is that the hook body actually performs both verifications - so call
    it, with an engine it must refuse.
    """
    import sqlalchemy

    import db
    import tests.conftest as conftest

    saved = db.ENGINE
    db.ENGINE = sqlalchemy.engine.create.create_engine(PRODUCTION)
    try:
        with pytest.raises(BaseException) as raised:
            conftest.pytest_sessionstart(None)
        assert "TEST ISOLATION GUARD - SESSION ABORTED" in str(raised.value)
        assert "db.ENGINE" in str(raised.value)
    finally:
        db.ENGINE = saved


def test_a_production_secrets_file_does_not_win():
    """The route the environment variable loses to.

    ``db._database_url()`` reads ``st.secrets`` before the environment, so on a
    machine with a secrets file the environment variable is ignored. The suite
    handles that by emptying ``st.secrets`` before ``db`` is imported. This
    proves it: run a probe from a directory that has a production secrets file,
    and assert the engine that gets built is still in memory.
    """
    workdir = PROJECT_ROOT / "tests" / f"_guard_probe_secrets_{os.getpid()}"
    shutil.rmtree(workdir, ignore_errors=True)
    (workdir / ".streamlit").mkdir(parents=True)
    (workdir / ".streamlit" / "secrets.toml").write_text(
        f'DATABASE_URL = "{PRODUCTION}"\nAUTH_DISABLED = true\n'
    )
    probe_dir = PROJECT_ROOT / "tests" / f"_guard_probe_{os.getpid()}"
    shutil.rmtree(probe_dir, ignore_errors=True)
    probe_dir.mkdir()
    (probe_dir / "__init__.py").write_text("")
    (probe_dir / "test_probe.py").write_text(textwrap.dedent(
        """
        def test_engine_is_still_in_memory():
            import db
            assert db.ENGINE.url.get_backend_name() == "sqlite"
            assert db.ENGINE.url.database in (None, "", ":memory:")

        def test_auth_is_not_disabled_either():
            import auth
            assert auth._auth_disabled() is False
        """
    ))
    env = dict(os.environ, PI3_GUARD_PROBE="1")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             str(probe_dir / "test_probe.py")],
            cwd=str(workdir), capture_output=True, text=True, env=env, timeout=180,
        )
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "2 passed" in output


# --- the evidence store -----------------------------------------------------

def test_the_live_evidence_store_is_not_configured_during_a_run():
    """Regulatory originals are evidence. A fixture file written into the live
    bucket is indistinguishable from a real one afterwards."""
    import regulatory_storage

    assert not regulatory_storage.is_configured()
    assert not os.environ.get("SUPABASE_URL")
    assert not os.environ.get("SUPABASE_SERVICE_ROLE_KEY")


def test_the_storage_guard_is_installed_in_this_very_session():
    assert isolation.STATE.storage_installed


def test_a_storage_call_that_injects_a_transport_still_works():
    """The control. Without it, the refusal below could just be a broken store.

    This is how every storage case in the suite is written, and it must keep
    working - a guard that blocks the legitimate path as well is not a guard,
    it is an outage.
    """
    import regulatory_storage as rs

    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return 200, b"{}"

    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
    try:
        result = rs.put_original(
            "candidate_list", b"fixture bytes", "fixture.xlsx", transport=transport
        )
    finally:
        isolation.forget_storage_credentials(os.environ)

    assert calls, "the injected transport should have been used"
    assert result["storage_bucket"] == rs.BUCKET


def test_the_session_aborts_when_a_test_would_really_write_to_the_bucket():
    """Proof by refusal, for the evidence store.

    The probe configures the store and calls put_original without injecting a
    transport - the mistake a future test could make - and the session must
    stop rather than PUT a fixture file into the live bucket.
    """
    result = run_probe(
        """
        import os

        import regulatory_storage as rs

        def test_forgets_to_inject_a_transport():
            os.environ["SUPABASE_URL"] = "https://example.supabase.co"
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
            rs.put_original("candidate_list", b"fixture", "fixture.xlsx")
        """
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "TEST ISOLATION GUARD - SESSION ABORTED" in output
    assert "object store" in output


def test_a_normal_run_has_the_guard_installed_and_pointing_at_memory():
    """The live session this test is running in is itself isolated."""
    import db

    assert isolation.STATE.installed
    assert hasattr(sa_create_engine(), "__wrapped__")
    assert db.ENGINE.url.get_backend_name() == "sqlite"
    assert db.ENGINE.url.database in (None, "", ":memory:")


def sa_create_engine():
    import sqlalchemy

    return sqlalchemy.create_engine
