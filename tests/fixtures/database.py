"""In-memory databases for tests that need a schema but not a server."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import db as m


def sqlite_session():
    """A private in-memory database, so no test can see another test's rows.

    StaticPool keeps every connection pointed at the same in-memory database
    for the lifetime of the engine; without it each connection would get its
    own empty one. ``check_same_thread`` is off because SQLAlchemy may hand the
    connection to a different thread than the one that created it.

    This is the same helper the moved check modules define privately. It is
    here so that tests written from now on share one definition.
    """
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    m.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()
