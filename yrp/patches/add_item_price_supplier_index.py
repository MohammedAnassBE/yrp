import frappe


def execute():
	frappe.db.add_index(
		"YRP Item Price",
		["item_name", "supplier", "docstatus"],
		"item_supplier_docstatus_index",
	)
