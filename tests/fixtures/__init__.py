"""Shared builders for the permanent suite.

Anything more than one test area needs belongs here rather than being copied.
The moved check modules in ``tests/checks/`` still carry their own private
copies of some of these helpers; those copies are collapsed onto this module as
each area is rewritten as ordinary pytest tests.
"""

from .database import sqlite_session

__all__ = ["sqlite_session"]
