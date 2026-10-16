"""Preserve legacy deliverable item types in the received-type dimension.

Model sync and stock-dimension setup create the destination field. Keep the
legacy column and any explicitly populated destination values intact.
"""

import frappe


def execute():
	doctype = "YRP Work Order Deliverables"
	if not frappe.db.has_column(doctype, "item_type"):
		return
	if not frappe.db.has_column(doctype, "received_type"):
		raise RuntimeError(f"{doctype}.received_type must exist before backfilling item_type")

	frappe.db.sql("""
		UPDATE `tabYRP Work Order Deliverables`
		SET received_type = item_type
		WHERE (received_type IS NULL OR received_type = '')
		  AND item_type IS NOT NULL AND item_type != ''
	""")
