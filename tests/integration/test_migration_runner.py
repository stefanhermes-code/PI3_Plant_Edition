"""Migration runner - ledger, checksums, row-count assertions, drift, backfill.

Needs a real PostgreSQL named by PI3_TEST_DB_URL. The runner relies on
transactional DDL, dollar-quoted function bodies and to_regclass; proving
it against SQLite would prove the wrong thing.

Moved under the Permanent Automated Regression Test Suite CR on 22 August 2026.
Original: tests/test_migration_runner.py
"""

import pytest

from tests._recorder import replay

RECORDING = replay("migration_runner", expected_checks=50)

pytestmark = pytest.mark.postgres

if RECORDING.skipped:
    # Charlie's rule of 21 August 2026: these are mandatory for a technical
    # release. They are allowed to skip on a developer machine with no Postgres,
    # but a release return may not report a clean mandatory suite if they did.
    # The suite says so out loud rather than folding them into a passing total.
    pytest.skip(RECORDING.skipped, allow_module_level=True)


def test_check_module_ran():
    """The module executed to the end. A crash halfway is a failure, not a pass."""
    assert RECORDING.error is None, "\n" + (RECORDING.error or "")


def test_no_check_disappeared():
    """The frozen count. A check cannot be deleted without this going red."""
    assert RECORDING.error is None, "the module did not run to the end"
    assert len(RECORDING.checks) == RECORDING.expected_checks, (
        f"{RECORDING.module} recorded {len(RECORDING.checks)} checks, "
        f"expected {RECORDING.expected_checks}."
    )


@pytest.mark.parametrize(
    "recorded", RECORDING.checks, ids=[c.id for c in RECORDING.checks]
)
def test_check(recorded):
    assert recorded.ok, recorded.failure_message()
