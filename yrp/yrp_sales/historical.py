"""Keep migrated sales history out of live document workflows."""

import frappe
from frappe import _
from frappe.utils import cint


def is_historical(doc_or_name, doctype=None):
	if not doc_or_name:
		return False
	doc = frappe.get_doc(doctype, doc_or_name) if isinstance(doc_or_name, str) else doc_or_name
	if cint(doc.get("is_historical")):
		return True
	# A request must not bypass the guard by clearing the stored marker.
	doctype = doctype or doc.get("doctype")
	return bool(
		doc.get("name")
		and frappe.get_meta(doctype).has_field("is_historical")
		and cint(frappe.db.get_value(doctype, doc.name, "is_historical"))
	)


def reject_historical(doc_or_name, doctype=None):
	if is_historical(doc_or_name, doctype):
		frappe.throw(_("This is a migrated historical record and cannot be changed."))
