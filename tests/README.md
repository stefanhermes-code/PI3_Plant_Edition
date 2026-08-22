# PI3 Plant Edition — Flexible Foam · regression suite

This is the permanent automated regression suite created under the
**Permanent Automated Regression Test Suite** change request, architecture
accepted and implementation released on 21 August 2026.

## Running it

```
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

`pytest` from the repository root runs everything. The suite is not
cwd-dependent: `pytest /path/to/repo` from anywhere gives the same result.

### The PostgreSQL checks

Some checks need a real PostgreSQL, named by an environment variable:

```
export PI3_TEST_DB_URL="postgresql+psycopg2://user:password@host:5432"
pytest
```

Note there is **no database name** on the end. Each of those checks creates and
drops its own throwaway database, so it needs a server, not a schema.

Without that variable those checks **skip**, and the run header says so. They
are marked `postgres` and can be selected or excluded:

```
pytest -m postgres        # only those
pytest -m "not postgres"  # everything else
```

> **A release rule, not a preference.** The PostgreSQL-marked checks are
> mandatory for a technical release. A release return may not report a clean
> mandatory suite from a run in which they skipped. If they skipped, say
> "skipped" and give the number; do not fold them into a passing total.

## Layout

| Path | What is in it |
|---|---|
| `tests/unit/` | Runs with no external service. In-memory SQLite is allowed. |
| `tests/integration/` | Needs a real service. Today that means PostgreSQL. |
| `tests/pages/` | Streamlit page-render checks. Empty until work package 6. |
| `tests/fixtures/` | Builders shared by more than one area. |
| `tests/checks/` | The pre-CR scripts, carried across. See below. |
| `tests/_recorder.py` | The machinery that replays them. |
| `tests/conftest.py` | Path setup, run header, shared fixtures. |

## `tests/checks/` — what it is and when it goes away

Before this CR, every check in the application was written the same way: a
module-level script with a local helper

```python
def check(case, expected, actual): ...
```

called once per check, printing PASS or FAIL and exiting non-zero at the end.
There were **321** such checks across six scripts, four of them in `tests/`
and two never in version control at all.

Converting 321 checks into hand-written pytest functions is the single
operation most likely to change what a check means, or to lose one quietly.
The CR asked specifically that no check disappear during the move. So instead:

* Each script moved into `tests/checks/` **with its body unchanged**. The only
  edits are the header, the removal of the local `check()` helper and the
  print-and-exit summary, and paths made repository-relative rather than
  cwd-relative. The `check(...)` statements themselves were not retyped.
* `tests/_recorder.py` replays a check module once and records everything it
  checked.
* A thin wrapper under `unit/`, `integration/` or `pages/` turns each recorded
  check into one named pytest test, so the suite reports one test per original
  check and each failure names the original case.
* **Every check module declares how many checks it must produce.** If a module
  produces a different number, the suite fails. That is the guard against a
  check disappearing, made permanent rather than applied once during the move.

This is a migration aid, not the house style. It has one real cost, stated
plainly: a check module runs as one unit, so if it raises halfway through, the
checks after that point do not run. They are reported as a failed module rather
than as silent passes, but the per-check independence of hand-written tests is
not there.

**Tests written from now on are ordinary pytest tests** in `unit/`,
`integration/` or `pages/`, using the fixtures in `tests/fixtures/`. As each
area is rewritten that way, its check module and its wrapper are deleted, and
`tests/checks/` shrinks toward nothing.

## Adding a test

* Put it in the directory that matches what it needs, not what it is about.
  A check that needs a live PostgreSQL is an integration test even if it is
  testing one function.
* Mark anything that needs PostgreSQL with `@pytest.mark.postgres`.
* State the input and the expected outcome in the test name or the assertion
  message. A failure should be readable without opening the file.
* **Defect-to-test rule, in force from 21 August 2026:** every defect found in
  this application gets a test that reproduces it *before* the fix, and that
  test stays in this suite afterwards.
