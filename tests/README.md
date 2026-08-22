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

## Isolation — the suite refuses to run against anything real

`tests/isolation.py` is a fail-closed guard. It aborts the **whole session**,
not one test, if anything opens a database that is not recognisably a test
database. It is an allow-list: anything not positively recognised is refused,
because a block-list of known production hostnames passes everything nobody
thought of.

There were three real routes to production, all verified rather than assumed:

* `db.ENGINE` is built when `db` is first imported, from `db._database_url()`,
  and that function reads `st.secrets` **before** the environment. Setting
  `DATABASE_URL` in the environment does not win on any machine with a
  `.streamlit/secrets.toml`, and Streamlit Cloud always has one.
* With nothing configured, `_database_url()` falls back to
  `sqlite:///pi3_local.db` — a file, and somebody's real local data.
* Tests build their own engines. The migration-runner checks create a
  throwaway PostgreSQL database per case from whatever `PI3_TEST_DB_URL` says.

The answer is **neutralise, then verify**. `conftest.py` empties `st.secrets`
and points `DATABASE_URL` at an in-memory database before `db` is imported;
then the guard re-reads the engine that was *actually built*, and wraps
`create_engine` so every engine is checked as it is opened. What is allowed:

| Allowed | Not allowed |
|---|---|
| In-memory SQLite | A SQLite **file** — including the `pi3_local.db` fallback |
| `postgres` on the server named by `PI3_TEST_DB_URL` — the only place a test can `CREATE DATABASE` from | Any PostgreSQL when no test server was nominated |
| `pi3_test_<pid>_<n>` on that same server, host, port and user | Any other database name on it, any other server, any other backend |
| | Anything on the host of the configured production database, under any name |

`AUTH_DISABLED` is refused too. It logs in a synthetic platform owner —
`is_super_admin` True and `company_id` **None**, which `tenant_scope` treats as
*unfiltered*. A company-isolation test written the obvious way then compares
unfiltered with unfiltered and passes whatever the code does. That has already
produced one false pass in this project.

For the same reason, `tests/fixtures/tenancy.py` will not build a context with
no company:

```python
ctx = tenant(company_id=1)                       # scoped, the normal case
ctx = tenant(company_id=1, platform_owner=True)  # an owner, still scoped
ctx = platform_owner_all_companies()             # deliberately unfiltered
ctx = tenant(company_id=None)                    # raises UnfilteredScope
```

**What the guard cannot do.** It cannot tell that the server named in
`PI3_TEST_DB_URL` is production. It narrows that to one deliberate act by the
operator, and refuses outright if that server shares a host with the configured
production database — but an operator who nominates production as the test
server, on a machine with no production configuration to compare against, is
outside what a guard can see.

`tests/unit/test_isolation_guard.py` proves all of this by starting real pytest
sessions that try to open databases they must not, and asserting the session
aborted. A safety mechanism nobody has watched fail is not evidence.

## Layout

| Path | What is in it |
|---|---|
| `tests/unit/` | Runs with no external service. In-memory SQLite is allowed. |
| `tests/integration/` | Needs a real service. Today that means PostgreSQL. |
| `tests/pages/` | Streamlit page-render checks via `AppTest`. See `tests/pages/README.md`. |
| `tests/fixtures/` | Builders shared by more than one area: in-memory databases, tenant contexts, the two-company world. |
| `tests/checks/` | The pre-CR scripts, carried across. See below. |
| `tests/_recorder.py` | The machinery that replays them. |
| `tests/isolation.py` | The fail-closed guard described above. |
| `tests/conftest.py` | Isolation setup **in a fixed order**, run header, shared fixtures. |

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

## Isolation tests need two companies

`tests/fixtures/world.py` builds two fully populated companies - plant, product
family, foam grade, recipe version, production run, customer trial,
optimization trial - plus a third with nothing in it. The `world` fixture hands
it to a test.

One company is never enough. With only company A in the database, a query that
ignores the company filter altogether returns exactly what a correctly scoped
query returns, and the test passes while proving nothing.

So every isolation test asserts **both halves**: what the company may see, and
what it may not. A test that only asserts the first half is not an isolation
test, and will be treated as a defect.

The third company covers the case that is easiest to get wrong: `None` means
*unfiltered* and `[]` means *this company has none of these yet*. Treating the
second as the first would show a brand-new company every other company's
production runs on its first login.

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
