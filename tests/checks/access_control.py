"""Access control and tenant isolation for the CertiPUR Readiness page."""
# ---------------------------------------------------------------------------
# Moved into the permanent suite on 22 August 2026 under the Permanent
# Automated Regression Test Suite CR. The body below is the original script,
# unchanged except for this header, the removal of the local check() helper and
# the print-and-exit summary, and paths made repository-relative instead of
# cwd-relative. The check() statements themselves were not retyped.
#
# Replayed by tests/_recorder.py. Not importable on its own.
# ---------------------------------------------------------------------------
from tests._recorder import PROJECT_ROOT, check, print  # noqa: A004
import os as _os


def _root(*parts):
    """A path inside the repository, wherever pytest was started from."""
    return _os.path.join(PROJECT_ROOT, *parts)

import access_control as ac

V = lambda **kw: ac.page_visible("certipur_readiness", **{
    "is_platform_owner": False, "subscription": None, "denied_keys": frozenset(),
    "is_super_admin": False, "unavailable_keys": frozenset(), "certipur_enabled": False, **kw})

print("=" * 78); print("E. ACCESS CONTROL - certipur_readiness"); print("=" * 78)
print("\nE1. The add-on gate")
check("company without the add-on cannot see the page", False, V(certipur_enabled=False))
check("company with the add-on can see the page", True, V(certipur_enabled=True))
check("add-on off beats a role that grants the page", False, V(certipur_enabled=False, denied_keys=frozenset()))

print("\nE2. Role permission still applies on top of the add-on")
check("add-on on, role denies the page", False, V(certipur_enabled=True, denied_keys=frozenset({"certipur_readiness"})))
check("add-on on, role allows the page", True, V(certipur_enabled=True))

print("\nE3. Implementation scope (Function Availability) still applies")
check("a stale deny-list row would hide the page, not contradict the add-on", False,
      V(certipur_enabled=True, unavailable_keys=frozenset({"certipur_readiness"})))
check("another page switched off does not affect this one", True,
      V(certipur_enabled=True, unavailable_keys=frozenset({"trend_analysis"})))

print("\nE4. Super admin")
check("super admin sees the page regardless of the add-on", True, V(is_super_admin=True))

print("\nE5. The page key is registered everywhere it must be")
check("in PAGE_CATALOG", True, "certipur_readiness" in ac.PAGE_CATALOG)
check("in PAGE_SECTION", True, "certipur_readiness" in ac.PAGE_SECTION)
check("section is Industrial Intelligence", "Industrial Intelligence", ac.PAGE_SECTION["certipur_readiness"])
check("deliberately NOT in the implementation deny-list - the add-on flag owns it",
      False, "certipur_readiness" in ac.CONFIGURABLE_PAGE_KEYS)
check("and it is named in NON_CONFIGURABLE_PAGE_KEYS on purpose",
      True, "certipur_readiness" in ac.NON_CONFIGURABLE_PAGE_KEYS)
check("not a platform-only page", False, "certipur_readiness" in ac.PLATFORM_ONLY_KEYS)
check("PAGE_SECTION and PAGE_CATALOG have not drifted", set(), set(ac.PAGE_CATALOG) ^ set(ac.PAGE_SECTION))

print("\nE6. Every other page is unaffected by the add-on flag")
diff = [k for k in ac.PAGE_CATALOG if k != "certipur_readiness"
        and ac.page_visible(k, is_platform_owner=True, subscription=None, denied_keys=frozenset(),
                            is_super_admin=False, unavailable_keys=frozenset(), certipur_enabled=False)
         != ac.page_visible(k, is_platform_owner=True, subscription=None, denied_keys=frozenset(),
                            is_super_admin=False, unavailable_keys=frozenset(), certipur_enabled=True)]
check("pages whose visibility changes with certipur_enabled", [], diff)
