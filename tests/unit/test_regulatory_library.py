"""REACH R-A1 - the Regulatory Data Library.

Dataset slots, immutable content-addressed storage of the originals, the
activation workflow and its blockers, duplicate detection by file hash,
and one active dataset per slot.

Moved under the Permanent Automated Regression Test Suite CR on 22 August 2026.
Original: tests/test_regulatory_library.py
"""

import pytest

from tests._recorder import replay

RECORDING = replay("regulatory_library", expected_checks=91)


def test_check_module_ran():
    """The module executed to the end. A crash halfway is a failure, not a pass."""
    assert RECORDING.error is None, "\n" + (RECORDING.error or "")


def test_no_check_disappeared():
    """The frozen count. A check cannot be deleted without this going red."""
    assert RECORDING.error is None, "the module did not run to the end"
    assert len(RECORDING.checks) == RECORDING.expected_checks, (
        f"{RECORDING.module} recorded {len(RECORDING.checks)} checks, "
        f"expected {RECORDING.expected_checks}. If a check was deliberately "
        f"removed or added, change expected_checks in this file and say so in "
        f"the change request."
    )


@pytest.mark.parametrize(
    "recorded", RECORDING.checks, ids=[c.id for c in RECORDING.checks]
)
def test_check(recorded):
    assert recorded.ok, recorded.failure_message()
