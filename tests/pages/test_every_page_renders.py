"""Every page renders. The check no test in this application could make.

Work package 6 of the Permanent Automated Regression Test Suite CR.

On 21 August 2026 the CertiPUR Readiness page raised KeyError on a customer's
screen while 321 checks were green. It had been doing so since v2.22.1, when
the assessment engine renamed a resolved key and the view was not renamed with
it. No check could have caught it: every one of the thirty files under views/
executes st. calls at module level, so no test could import one.

Compiling a page proves it parses. It does not prove it runs. This does.
"""

from __future__ import annotations

import pytest

from tests.fixtures.pages import first_exception, render, view_files

VIEW_FILES = view_files()


def page_id(name: str) -> str:
    return name[:-3]


def test_there_are_pages_to_render():
    """A guard against the whole suite quietly becoming a no-op.

    If views/ moved or the glob broke, every parametrised case below would
    vanish and the run would still be green.
    """
    assert len(VIEW_FILES) >= 30, VIEW_FILES


@pytest.mark.parametrize("view_file", VIEW_FILES, ids=[page_id(v) for v in VIEW_FILES])
def test_the_page_renders_without_raising(view_file, signed_in):
    app = render(view_file, signed_in)
    raised = first_exception(app)
    assert raised is None, f"{view_file} raised:\n{raised}"


@pytest.mark.parametrize("view_file", VIEW_FILES, ids=[page_id(v) for v in VIEW_FILES])
def test_the_page_puts_something_on_the_screen(view_file, signed_in):
    """A page that raises nothing because it rendered nothing is not passing.

    Without this, a page that silently returned at the top - a bad access
    check, an early return on a missing key - would sail through the test
    above.
    """
    app = render(view_file, signed_in)
    produced = (
        len(app.markdown)
        + len(app.title)
        + len(app.header)
        + len(app.subheader)
        + len(app.dataframe)
        + len(app.table)
        + len(app.button)
        + len(app.selectbox)
        + len(app.text_input)
        + len(app.info)
        + len(app.warning)
        + len(app.error)
        + len(app.caption)
        + len(app.metric)
    )
    assert produced > 0, f"{view_file} rendered nothing at all"
