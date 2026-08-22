"""Fixtures for the page-render suite."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def seeded_application():
    """One seeded application, shared by every page in the suite.

    Session-scoped on purpose. These are render checks: a page is run once,
    with no interaction, so nothing here writes. Reseeding for each of thirty
    pages would multiply the slowest part of the suite for no gain.
    """
    import db

    from tests.fixtures.pages import seed_application

    session = db.SessionLocal()
    try:
        return seed_application(session)
    finally:
        session.close()


@pytest.fixture
def signed_in(seeded_application):
    from tests.fixtures.pages import signed_in_state

    return signed_in_state(seeded_application)
