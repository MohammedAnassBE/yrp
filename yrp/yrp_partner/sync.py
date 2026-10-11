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
