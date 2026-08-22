#!/usr/bin/env python3
"""Fail the build if the PostgreSQL-marked checks did not actually run.

Charlie's rule of 21 August 2026: the PostgreSQL-marked checks are mandatory
for a technical release, and a release return may not report a clean mandatory
suite from a run in which they skipped.

They skip quietly and by design on a developer machine with no PostgreSQL. In
CI that is not acceptable, and "the log said skipped" is not a control if
nobody reads the log. So this reads the JUnit report and fails when the
integration suite did not execute.

Usage: assert_postgres_ran.py <junit.xml>
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

INTEGRATION = "tests.integration"


def main(path: str) -> int:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        print(f"::error::could not read the test report at {path}: {exc}")
        return 1

    ran = skipped = 0
    for case in tree.iter("testcase"):
        classname = case.get("classname") or ""
        if not classname.startswith(INTEGRATION):
            continue
        if case.find("skipped") is not None:
            skipped += 1
        else:
            ran += 1

    print(f"integration tests executed: {ran}")
    print(f"integration tests skipped:  {skipped}")

    if ran == 0:
        print(
            "::error::The PostgreSQL-marked checks did not run. They are "
            "mandatory for a technical release, so this run cannot be reported "
            "as a clean mandatory suite. Check that the postgres service "
            "started and that PI3_TEST_DB_URL is set - with no database name "
            "on the end."
        )
        return 1

    if skipped:
        print(
            f"::error::{skipped} PostgreSQL-marked check(s) skipped. A "
            "mandatory suite may not be reported clean with checks skipped."
        )
        return 1

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
