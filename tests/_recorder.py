"""Recorder that carries the pre-CR check scripts into the permanent suite.

Background
----------
Before this suite existed, every check in this application was written the same
way: a module-level script with a local helper

    def check(case, expected, actual): ...

called once per check, printing PASS or FAIL and exiting non-zero at the end.
There were 321 such checks across six scripts.

Rewriting 321 checks by hand into pytest functions is the single operation most
likely to change what a check means, or to lose one without anyone noticing.
So the scripts were moved into ``tests/checks/`` with their bodies untouched:
the only edit is the first few lines and the last few, where the local helper
and the print-and-exit summary are replaced by an import from this module.

This module then replays a check module and hands each recorded check to a thin
pytest wrapper, so the suite reports one named pytest test per original check.

This is a migration aid, not the house style. Tests written from here on are
ordinary pytest tests. See ``tests/README.md``.
"""

from __future__ import annotations

import builtins
import importlib
import os
import re
import traceback
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Section headers in the original scripts are printed, not declared, so the
# print shim below is what notices them. Two forms are in use:
#   "A. THE SLOT CATALOGUE"                   - a top-level group
#   "A1. Meets requirement - every criterion" - a case group inside it
# and occasionally a lettered variant, "F5a. The case that prompted the change".
_SECTION_RE = re.compile(r"^([A-Z]\d*[a-z]?)\.\s+(\S.*)$")

# A pytest test id may not contain characters that break -k selection or the
# terminal report, and must stay stable so a check can be referred to by name.
_ID_SAFE = re.compile(r"[^0-9A-Za-z]+")


class SkipChecks(Exception):
    """Raised by a check module that cannot run at all in this environment.

    The migration-runner checks need a real PostgreSQL. The original script
    printed a reason and exited 0. Here it says so, the wrapper turns it into a
    pytest skip, and the frozen count is not asserted - a skipped module has
    produced no checks on purpose, which is not the same as losing them.
    """


@dataclass(frozen=True)
class Check:
    """One recorded check, exactly as the original script stated it."""

    section: str
    case: str
    expect: object
    got: object
    detail: str
    ok: bool

    @property
    def id(self) -> str:
        stem = _ID_SAFE.sub("_", self.case).strip("_")[:90]
        return f"{self.section}_{stem}" if self.section else stem

    def failure_message(self) -> str:
        lines = [
            f"{self.section + '. ' if self.section else ''}{self.case}",
            f"  expected: {self.expect!r}",
            f"  actual:   {self.got!r}",
        ]
        if self.detail:
            lines.append(f"  detail:   {self.detail}")
        return "\n".join(lines)


@dataclass
class Recording:
    """Everything one check module produced when it was replayed."""

    module: str
    expected_checks: int
    checks: list = field(default_factory=list)
    log: list = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None

    @property
    def count_matches(self) -> bool:
        return self.error is None and len(self.checks) == self.expected_checks


class _Recorder:
    def __init__(self) -> None:
        self.checks: list[Check] = []
        self.log: list[str] = []
        self.section = ""

    def check(self, case, expect, got, detail="") -> None:
        self.checks.append(
            Check(self.section, str(case), expect, got, str(detail), expect == got)
        )

    def print(self, *args, **kwargs) -> None:
        line = kwargs.get("sep", " ").join(str(a) for a in args)
        self.log.append(line)
        match = _SECTION_RE.match(line.strip())
        if match:
            self.section = match.group(1)


# Exceptions that must never be recorded as a failed check. A session that is
# being torn down deliberately - the isolation guard refusing a database, a
# Ctrl-C - has to reach pytest, not be logged as check number 41.
_ALWAYS_PROPAGATE: tuple = (KeyboardInterrupt, SystemExit)
try:  # pragma: no cover - pytest is always present when the suite runs
    from _pytest.outcomes import Exit as _PytestExit

    _ALWAYS_PROPAGATE = _ALWAYS_PROPAGATE + (_PytestExit,)
except Exception:  # noqa: BLE001 - the recorder works without pytest too
    pass


_current: _Recorder | None = None
_loaded: dict[str, Recording] = {}


def check(case, expect, got, detail="") -> None:
    """Record one check. Replaces the local helper each script used to define."""
    if _current is None:
        raise RuntimeError(
            "A tests.checks module was imported outside replay(). "
            "Check modules are not importable on their own; load them with "
            "tests._recorder.replay()."
        )
    _current.check(case, expect, got, detail)


def print(*args, **kwargs) -> None:  # noqa: A001 - deliberate shadow
    """Capture the script's own output, and notice its section headers."""
    if _current is None:
        builtins.print(*args, **kwargs)
        return
    _current.print(*args, **kwargs)


def replay(module: str, expected_checks: int) -> Recording:
    """Import one check module once and return everything it recorded.

    ``expected_checks`` is the count frozen when the module was moved. It is
    asserted by the wrapper, so a check cannot be deleted, or silently skipped
    by an early return, without the suite going red.
    """
    if module in _loaded:
        # Python caches modules, so a second import would record nothing and
        # look like every check had vanished. Fail loudly instead.
        raise RuntimeError(
            f"tests.checks.{module} was replayed twice. Each check module has "
            "exactly one pytest wrapper."
        )

    global _current
    recording = Recording(module=module, expected_checks=expected_checks)
    _current = _Recorder()
    try:
        importlib.import_module(f"tests.checks.{module}")
    except SkipChecks as reason:
        recording.skipped = str(reason)
    except _ALWAYS_PROPAGATE:
        # The isolation guard aborts the session by raising. A check module
        # must not swallow that and report it as a failed check - the whole run
        # is supposed to stop.
        raise
    except BaseException:  # noqa: BLE001 - a broken module must not stop collection
        recording.error = traceback.format_exc()
    finally:
        recording.checks = _current.checks
        recording.log = _current.log
        _current = None

    _loaded[module] = recording
    return recording
