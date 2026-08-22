# `tests/pages/`

Page-render checks, using `streamlit.testing.v1.AppTest`.

## Why

No test in this application could import a view. All thirty files under
`views/` execute `st.` calls at module level, so importing one outside a
Streamlit runtime fails immediately.

That is not a small gap. On 21 August 2026 the CertiPUR Readiness page raised
`KeyError` on a customer's screen while 321 checks were green. It had been
doing so since v2.22.1, when the assessment engine renamed a resolved key and
the view was not renamed with it. Every check in the suite was looking at the
engine; nothing could see the page.

`AppTest` runs a page script headlessly, with a real session state and a real
script run, and reports whatever it raised. No application change is needed.

**Compiling a page proves it parses. It does not prove it runs.**

## What is checked

Two assertions per page, both of which matter:

* **It renders without raising.** The direct answer to the CertiPUR crash.
* **It puts something on the screen.** A page that raises nothing because it
  rendered nothing is not passing. Without this, a page that silently returned
  at the top - a bad access check, an early return on a missing key - would
  sail through the first assertion.

Plus one guard that there are pages to render at all, so the suite cannot
quietly become a no-op if `views/` moves.

## What makes it work

* `tests/isolation.rebind_to_shared_memory()` gives the application one
  in-memory database that the AppTest thread can also see. SQLAlchemy's default
  pool for `sqlite://` is `SingletonThreadPool`, and each connection to
  `sqlite://` gets its **own** empty database - so without this a page would
  open an empty one however carefully the test had seeded it.
* `tests/fixtures/pages.py` seeds a company, plant, family, grade, recipe and
  components, so a page reaches its data-dependent code. A smoke test against
  an empty database exercises the top of a page and none of the parts that
  break.
* The session state is the shape a **real login** produces, with a real
  `company_id`. Not the `AUTH_DISABLED` shape, which leaves `company_id` None -
  `tenant_scope` reads that as unfiltered, so a page rendered under it would be
  drawing every company's data and the test would never notice.

## Proved, not assumed

The exact v2.22.1 defect was reintroduced on 22 August 2026 and this suite
turned red on `33_CertiPUR_Readiness`, naming the missing key. It was then
removed again. A regression suite that has never caught the bug it was built
for is a claim, not a control.

## What it is not

A render check is a smoke test. It proves a page runs and draws something with
representative data in front of it. It does not prove the numbers on it are
right - that is what the unit suites are for - and it does not exercise
interaction, because each page is run once with no clicks.
