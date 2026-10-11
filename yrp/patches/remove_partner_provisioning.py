"""Retire account-management configuration.

Existing login names, roles, mobile numbers and enabled states are preserved.
Frappe retains retired database columns; this removes their Custom Field metadata,
not historical User records.
"""

import frappe


def execute():
	for fieldname in ("yrp_partner_managed", "yrp_partner_generated_email", "yrp_partner_auto_disabled"):
		name = frappe.db.get_value("Custom Field", {
			"dt": "User", "fieldname": fieldname, "module": "YRP Partner",
		}, "name")
		if name:
			# These exact app-owned markers have no business-document references.
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
