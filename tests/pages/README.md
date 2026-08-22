# `tests/pages/`

Streamlit page-render checks, using `streamlit.testing.v1.AppTest`.

**Deliberately empty.** The directory exists because the CR asked for the
permanent structure to be created in one step; the render suite itself is work
package 6 and has not been released to start.

It matters more than an empty directory suggests. On 21 August 2026 the
CertiPUR Readiness page raised `KeyError` on a customer's screen while 321
checks were green, because no check in this application can import a view: all
thirty pages execute `st.` calls at module level, so importing one outside a
Streamlit runtime fails. `AppTest` is what closes that gap.

Until then, the nearest coverage is in `tests/unit/test_app_regression.py`:
every page is compiled, and the navigation keys and the access-control
catalogue are checked against each other. Compiling a page proves it parses.
It does not prove it runs.
