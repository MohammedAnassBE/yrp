"""Keep migrated sales history out of live document workflows."""

import frappe
from frappe import _
from frappe.utils import cint


def is_historical(doc_or_name, doctype=None):
	if not doc_or_name:
		return False
	if isinstance(doc_or_name, str):
		name = doc_or_name
	else:
		if cint(doc_or_name.get("is_historical")):
			return True
		# A request must not bypass the guard by clearing the stored marker.
		doctype = doctype or doc_or_name.get("doctype")
		name = doc_or_name.get("name")
	return bool(
		name
		and frappe.get_meta(doctype).has_field("is_historical")
		and cint(frappe.db.get_value(doctype, name, "is_historical"))
	)


def reject_historical(doc_or_name, doctype=None):
	if is_historical(doc_or_name, doctype):
		frappe.throw(_("This is a migrated historical record and cannot be changed."))
