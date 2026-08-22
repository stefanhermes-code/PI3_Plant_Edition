# `tools/`

Supporting utilities. **Nothing here runs in the release suite.** `pytest.ini`
sets `testpaths = tests`, and nothing in this directory is named so that pytest
would collect it.

## `verify_check_migration.py`

Proves that the 321 checks which existed before the Permanent Automated
Regression Test Suite CR survived the move into `tests/` unchanged.

```
git worktree add /tmp/pi3-before 5d70168
export PI3_TEST_DB_URL="postgresql+psycopg2://user:pw@host:5432"   # no db name
python tools/verify_check_migration.py --before /tmp/pi3-before
```

For each of the six original scripts it runs the original against the old
checkout, replays the module it became in the current tree, and compares the
two lists of check statements **position by position**. Comparing counts alone
would pass if two checks had swapped meaning; comparing the statements in order
is what makes the answer worth having. Exit code is 0 only on an exact match.

It is not part of the release suite because it needs two checkouts, and a
release gate should not depend on having one. It is committed because the
migration evidence should be reproducible from the repository rather than
living in whoever happened to run it — Charlie's requirement of 22 August 2026.

Without `PI3_TEST_DB_URL` the 50 migration-runner checks are skipped on both
sides and reported as `skipped`, not counted as matching.

### `migration_evidence/original_scripts/`

The six pre-CR scripts, as they stood at the moment of the move.

Four of them were in `tests/` and are also in git history at `5d70168`.
The other two — `access.py` and `regression.py`, holding 64 of the 321 checks —
had never been committed. They were working scripts, which is a fair part of
why the CR exists. Keeping copies here means the comparison needs nothing but
this repository and a checkout of the old commit.

These are reference copies. They are not maintained, not run by the suite, and
should not be edited: their whole value is being what they were.
