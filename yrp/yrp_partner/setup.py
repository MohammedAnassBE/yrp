"""Read permissions for user-configured Partner source types.

Fixed roles, permissions and fields are shipped as fixtures in hooks.py. Only
the dynamic source selected in a Partner Type needs a permission at runtime.
"""
import frappe


def ensure_partner_read_permission(doctype):
	"""Grant only base read; query/document hooks narrow records and deny writes."""
	from frappe.permissions import add_permission

	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": "YRP Partner"}):
		add_permission(doctype, "YRP Partner")
