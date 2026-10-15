"""Generate one YRP Partner per configured source record.

Membership (the Partner's `users` table) is explicit. An administrator or a
custom app maintains it; yrp only reads it for permissions and never derives it.
"""

import frappe

_GENERATION_TOKEN = object()


def ensure_partner(partner_type, reference_doctype, reference_name):
	"""Create the source's Partner when it is missing; existing members are never changed."""
	filters = {"partner_type": partner_type, "reference_name": reference_name}
	if frappe.db.exists("YRP Partner", filters):
		return
	partner = frappe.get_doc({"doctype": "YRP Partner", **filters, "reference_doctype": reference_doctype})
	partner.flags.generation_token = _GENERATION_TOKEN
	try:
		partner.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		if not frappe.db.exists("YRP Partner", filters):
			raise


def sync_document(doc, method=None):
	"""Create missing Partners when a configured source record is saved."""
	if doc.doctype.startswith("YRP Partner") or not frappe.db.table_exists("YRP Partner Type"):
		return
	for partner_type in frappe.get_all(
		"YRP Partner Type", filters={"reference_doctype": doc.doctype}, pluck="name"
	):
		ensure_partner(partner_type, doc.doctype, doc.name)


def ensure_historical_partner(name, partner_type, reference_doctype):
	"""Preserve an unbound historical identity without inventing a source record.

	This internal import path retains an explicit name, validates the configured
	type and never overwrites an existing identity or grants membership roles.
	Unbound references use NULL so the business-binding unique index stays valid.
	"""
	if not name or not partner_type or not reference_doctype:
		raise frappe.ValidationError("Historical Partner requires a name, type and reference DocType")
	if frappe.db.get_value("YRP Partner Type", partner_type, "reference_doctype") != reference_doctype:
		raise frappe.ValidationError("Historical Partner type does not match its reference DocType")
	if existing := frappe.db.get_value(
		"YRP Partner", name, ["partner_type", "reference_doctype", "reference_name"], as_dict=True
	):
		if (existing.partner_type, existing.reference_doctype) != (partner_type, reference_doctype) or existing.reference_name:
			raise frappe.ValidationError(f"Historical Partner name conflicts with an existing identity: {name}")
		return
	partner = frappe.get_doc({
		"doctype": "YRP Partner", "partner_type": partner_type,
		"reference_doctype": reference_doctype, "reference_name": None,
	})
	partner.flags.generation_token = _GENERATION_TOKEN
	partner.insert(ignore_permissions=True, ignore_mandatory=True, set_name=name)
