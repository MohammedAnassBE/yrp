"""Roles that follow explicit YRP Partner membership.

A member gets the scope role plus the role of each source type it belongs to.
A role is removed only when no remaining membership of the user needs it.
Other roles are never touched.
"""

import frappe

from yrp.yrp_partner.customer_access import CUSTOMER_ROLE
from yrp.yrp_partner.sales_roles import SALES_PARTNER, SALES_PERSON

SCOPE_ROLE = "YRP Partner"
SOURCE_ROLES = {"Customer": CUSTOMER_ROLE, "Sales Partner": SALES_PARTNER, "Sales Person": SALES_PERSON}
MEMBER_ROLES = {SCOPE_ROLE, *SOURCE_ROLES.values()}


def get_member_roles(source_doctypes) -> set[str]:
	"""Roles that memberships of the given source DocTypes give."""
	if not source_doctypes:
		return set()
	return {SCOPE_ROLE} | {SOURCE_ROLES[doctype] for doctype in source_doctypes if doctype in SOURCE_ROLES}


def get_needed_roles(user: str) -> set[str]:
	return get_member_roles(frappe.db.sql_list(
		"""select distinct p.reference_doctype from `tabYRP Partner` p
		join `tabYRP Partner User` m on m.parent = p.name
			and m.parenttype = 'YRP Partner' and m.parentfield = 'users'
		where m.user = %s and coalesce(p.reference_name, '') != ''""",
		user,
	))


def sync_member_roles(users, removable: set[str]):
	"""Give each user the roles its memberships need; remove `removable` roles none needs."""
	for user in sorted(set(users) - {"Administrator", "Guest"}):
		if not frappe.db.exists("User", user):
			continue
		needed = get_needed_roles(user)
		doc = frappe.get_doc("User", user)
		current = {row.role for row in doc.roles}
		missing = needed - current
		extra = (removable & current) - needed
		if not missing and not extra:
			continue

		doc.set("roles", [row for row in doc.roles if row.role not in extra])
		doc.append_roles(*sorted(missing))
		doc.save(ignore_permissions=True)
