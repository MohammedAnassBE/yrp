"""Retire YRP Product; the template Item (has_variants) is the product.

Fails loudly if any Product or Item link still holds data. Frappe retains the
retired `tabItem.yrp_product` column; this removes its Custom Field metadata.
"""

import frappe

DOCTYPES = ("YRP Product", "YRP Product Category")


def execute():
	if frappe.db.has_column("Item", "yrp_product") and frappe.db.exists("Item", {"yrp_product": ["is", "set"]}):
		frappe.throw("Items still link a YRP Product; move them to their template Item first.")
	for doctype in DOCTYPES:
		if frappe.db.table_exists(doctype) and frappe.db.count(doctype):
			frappe.throw(f"{doctype} still has rows; remove them before retiring the DocType.")
	if frappe.db.exists("Custom Field", "Item-yrp_product"):
		frappe.delete_doc("Custom Field", "Item-yrp_product", ignore_permissions=True, force=True)
	for doctype in DOCTYPES:
		for name in frappe.get_all("Custom Field", filters={"dt": doctype}, pluck="name"):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
		frappe.db.delete("Property Setter", {"doc_type": doctype})
		frappe.db.delete("Custom DocPerm", {"parent": doctype})
		frappe.db.delete("Workspace Link", {"link_type": "DocType", "link_to": doctype})
		frappe.db.delete("Workspace Sidebar Item", {"link_type": "DocType", "link_to": doctype})
		# The Product's Table field links the Category, so the Product goes first.
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, ignore_permissions=True, force=True)
		frappe.db.sql_ddl(f"drop table if exists `tab{doctype}`")
