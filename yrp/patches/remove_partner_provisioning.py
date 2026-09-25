"""Retire account-management configuration and refresh derived access only.

Existing login names, roles, mobile numbers and enabled states are preserved.
Frappe retains retired database columns; this removes their Custom Field metadata,
not historical User records. Existing queued backfills retain their revisions
and load the current membership-only implementation when they execute.
"""

import frappe


def execute():
	from yrp.yrp_partner.sync import ensure_partner

	for fieldname in ("yrp_partner_managed", "yrp_partner_generated_email", "yrp_partner_auto_disabled"):
		name = frappe.db.get_value("Custom Field", {
			"dt": "User", "fieldname": fieldname, "module": "YRP Partner",
		}, "name")
		if name:
			# These exact app-owned markers have no business-document references.
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	if not frappe.db.table_exists("YRP Partner Type"):
		return
	if not frappe.db.table_exists("YRP Partner"):
		return
	for row in frappe.get_all("YRP Partner", fields=["partner_type", "reference_doctype", "reference_name"]):
		if frappe.db.exists(row.reference_doctype, row.reference_name):
			ensure_partner(row.partner_type, row.reference_doctype, row.reference_name)
