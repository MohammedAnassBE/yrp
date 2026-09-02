import frappe
from frappe.model.rename_doc import rename_doc


def execute():
	if frappe.db.exists("DocType", 'YRP Process Details'):
		return
	if not frappe.db.exists("DocType", "Process Detail"):
		return

	rename_doc(
		"DocType",
		"Process Detail",
		'YRP Process Details',
		force=True,
		ignore_permissions=True,
	)
