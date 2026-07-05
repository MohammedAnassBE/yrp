# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Drop the legacy `reserved_qty` column from tabBin (D-002 / Gap #8).

Reservation state lives in tabStock Reservation Entry; consumers query it
live via yrp.stock.utils.get_sre_reserved_qty / get_available_stock.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Bin"):
		return
	cols = frappe.db.sql("SHOW COLUMNS FROM `tabBin` LIKE 'reserved_qty'")
	if cols:
		frappe.db.sql("ALTER TABLE `tabBin` DROP COLUMN `reserved_qty`")
	# Frappe metadata cleanup
	frappe.db.delete("DocField", {"parent": "Bin", "fieldname": "reserved_qty"})
	frappe.clear_cache(doctype="Bin")
