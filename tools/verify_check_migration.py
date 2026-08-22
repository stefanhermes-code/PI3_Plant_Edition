#!/usr/bin/env python3
"""Prove that the 321 pre-CR checks survived the move into the permanent suite.

Why this exists
---------------
Work packages 1 and 2 of the Permanent Automated Regression Test Suite CR moved
321 checks out of six standalone scripts and into ``tests/``. Charlie's
acceptance of 22 August 2026 asked for the comparison used to prove that move
to be committed, so the evidence stays reproducible from the repository rather
than living in whoever ran it.

It is deliberately NOT part of the release suite. It compares two checkouts, so
it cannot run from a single working copy, and a release gate should not depend
on having one. ``pytest.ini`` sets ``testpaths = tests``; this lives in
``tools/`` and is named so that pytest would not collect it even if it did.

What it does
------------
For each of the six original scripts:

* runs the original, as it was, against a checkout of the code as it was;
* replays the module it became, in the current tree;
* compares the two lists of check statements **position by position**.

Comparing counts alone would pass if two checks had swapped meaning. Comparing
the statements in order is what makes the answer worth having.

Usage
-----
    git worktree add /tmp/pi3-before 5d70168
    python tools/verify_check_migration.py --before /tmp/pi3-before

``--after`` defaults to this repository. Set ``PI3_TEST_DB_URL`` (with no
database name on the end) or the 50 migration-runner checks are skipped on both
sides and reported as such, rather than silently counted as matching.

Exit code is 0 only when every script matches its module exactly.

Note on the two scripts that were never in version control
----------------------------------------------------------
``access.py`` and ``regression.py`` held 64 of the 321 checks and had never
been committed - they were working scripts. Copies as they stood at the moment
of the move are kept in ``tools/migration_evidence/original_scripts/`` beside
the four that were in ``tests/``, so this comparison needs nothing but the
repository and a checkout of the old commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ORIGINALS = os.path.join(HERE, "migration_evidence", "original_scripts")

# section, original script, module it became, the wrapper that owns it
PAIRS = [
    ("A", "test_certipur_fixtures.py", "certipur_criteria",
     "tests/unit/test_certipur_criteria.py"),
    ("B", "test_sample_integrity.py", "sample_integrity",
     "tests/unit/test_sample_integrity.py"),
    ("C", "test_regulatory_library.py", "regulatory_library",
     "tests/unit/test_regulatory_library.py"),
    ("D", "test_migration_runner.py", "migration_runner",
     "tests/integration/test_migration_runner.py"),
    ("E", "access.py", "access_control", "tests/unit/test_access_control.py"),
    ("F", "regression.py", "app_regression", "tests/unit/test_app_regression.py"),
]

# The originals printed one line per check: "  [PASS] <case>" for the four in
# tests/, and "  [PASS] <case>: expected X, got Y" for the two working scripts.
VERDICT_LINE = re.compile(r"^  \[(PASS|FAIL)\] (.*)$")


def original_cases(script: str, before: str) -> tuple[list[str], list[str], str]:
    """Run one pre-CR script as it was and read its checks back off its output."""
    env = dict(os.environ)
    # The originals assumed the repository root was importable. One of them
    # assumed it so incorrectly that it could not run from the layout it was
    # committed into - a defect the move fixed. Setting this reproduces the
    # working directory they were actually proved in.
    env["PYTHONPATH"] = before
    proc = subprocess.run(
        [sys.executable, os.path.join(ORIGINALS, script)],
        cwd=before, env=env, capture_output=True, text=True,
    )
    cases, verdicts = [], []
    for line in proc.stdout.split("\n"):
        match = VERDICT_LINE.match(line)
        if not match:
            continue
        text = match.group(2)
        if ": expected " in text:
            text = text.rsplit(": expected ", 1)[0]
        cases.append(text)
        verdicts.append(match.group(1))
    return cases, verdicts, proc.stdout + proc.stderr


def moved_cases(module: str, after: str) -> tuple[list[str], list[bool], str | None]:
    """Replay the module that script became, in the current tree."""
    code = (
        "import json,sys;sys.path.insert(0,%r);"
        "from tests._recorder import replay;"
        "r=replay(%r,0);"
        "print('__RESULT__'+json.dumps({'cases':[c.case for c in r.checks],"
        "'ok':[c.ok for c in r.checks],'err':r.error,'skip':r.skipped}))"
        % (after, module)
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=after, env=dict(os.environ),
        capture_output=True, text=True,
    )
    marker = [l for l in proc.stdout.split("\n") if l.startswith("__RESULT__")]
    if not marker:
        raise SystemExit(
            f"{module}: the replay produced no result.\n{proc.stdout[-2000:]}\n"
            f"{proc.stderr[-2000:]}"
        )
    data = json.loads(marker[-1][len("__RESULT__"):])
    return data["cases"], data["ok"], data["err"] or data["skip"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before", required=True,
        help="checkout of the commit before the move (e.g. a git worktree at 5d70168)",
    )
    parser.add_argument("--after", default=REPO, help="the current tree (default: this repository)")
    parser.add_argument("--verbose", action="store_true", help="print every differing case")
    args = parser.parse_args()

    before = os.path.abspath(args.before)
    after = os.path.abspath(args.after)
    if not os.path.isdir(before):
        raise SystemExit(f"--before is not a directory: {before}")

    if not os.environ.get("PI3_TEST_DB_URL"):
        print(
            "note: PI3_TEST_DB_URL is not set, so the 50 migration-runner checks\n"
            "      are skipped on both sides. They are reported as skipped below,\n"
            "      not counted as matching.\n"
        )

    rows, problems, total_before, total_after = [], [], 0, 0
    for section, script, module, wrapper in PAIRS:
        b_cases, b_verdicts, b_output = original_cases(script, before)
        a_cases, a_ok, a_note = moved_cases(module, after)
        total_before += len(b_cases)
        total_after += len(a_cases)

        skipped = not b_cases and not a_cases
        identical = b_cases == a_cases

        if not identical:
            for index, (was, now) in enumerate(zip(b_cases, a_cases)):
                if was != now:
                    problems.append(f"{section} #{index}: before={was!r} after={now!r}")
                    if not args.verbose:
                        break
            if len(b_cases) != len(a_cases):
                problems.append(
                    f"{section}: {len(b_cases)} checks before, {len(a_cases)} after"
                )
            if not b_cases:
                problems.append(f"{section}: the original produced nothing.\n{b_output[-1500:]}")
        if any(v == "FAIL" for v in b_verdicts):
            problems.append(f"{section}: {b_verdicts.count('FAIL')} were already failing before the move")
        if a_ok and not all(a_ok):
            problems.append(f"{section}: {a_ok.count(False)} failing after the move")
        if a_note and not skipped:
            problems.append(f"{section}: {a_note}")

        rows.append((section, script, len(b_cases), wrapper, len(a_cases), identical, skipped))

    width_b = max(len(r[1]) for r in rows) + 2
    width_a = max(len(r[3]) for r in rows) + 2
    print(f"{'':3}{'before':<{width_b}}{'n':>4}   {'after':<{width_a}}{'n':>4}   same")
    for section, script, n_before, wrapper, n_after, identical, skipped in rows:
        state = "skipped" if skipped else ("yes" if identical else "NO")
        print(f"{section:<3}{script:<{width_b}}{n_before:>4}   {wrapper:<{width_a}}{n_after:>4}   {state}")
    print(f"{'':3}{'TOTAL':<{width_b}}{total_before:>4}   {'':<{width_a}}{total_after:>4}")
    print()

    if problems:
        print("PROBLEMS:")
        for problem in problems:
            print("  -", problem)
        return 1

    print(
        "No differences. Every check statement is character-identical, in the "
        "same order."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
