"""Clones the 3 built-in role templates (admin/technical/viewer) into a new
company's own copies.

See db.py's Role docstring for why this exists: the templates
(company_id=NULL, is_builtin=True) are never assigned to a User directly and
never shown outside the Default User Roles page. Every real company needs
its own company_id-scoped clone so that one company narrowing a built-in
role's page visibility can never affect another company.
"""

from db import Role, RolePagePermission


def clone_builtin_roles_for_company(session, company_id):
    """Creates company-scoped clones of the 3 built-in role templates for
    company_id, copying each template's current page-visibility rows. Call
    once, right after a Company row is created (and flushed, so it has an
    id) - session.flush()'d here so the caller has real role ids available
    if needed, but does NOT commit; the caller controls the transaction.

    Safe to call more than once by accident in the sense that it won't
    crash, but will create duplicate role rows - callers should only call
    this exactly once per company, at creation time."""
    templates = (
        session.query(Role)
        .filter(Role.company_id.is_(None), Role.is_builtin.is_(True))
        .all()
    )
    for template in templates:
        clone = Role(
            company_id=company_id,
            name=template.name,
            description=template.description,
            is_builtin=True,
        )
        session.add(clone)
        session.flush()  # need clone.id before copying permission rows

        template_denials = (
            session.query(RolePagePermission)
            .filter(RolePagePermission.role_id == template.id)
            .all()
        )
        for denial in template_denials:
            session.add(
                RolePagePermission(role_id=clone.id, page_key=denial.page_key, can_view=denial.can_view)
            )
