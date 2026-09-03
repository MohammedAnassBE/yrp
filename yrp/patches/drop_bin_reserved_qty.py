# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Drop the legacy `reserved_qty` column from tabYRP Bin (D-002 / Gap #8).

Reservation state lives in tabYRP Stock Reservation Entry; consumers query it
live via yrp.stock.utils.get_sre_reserved_qty / get_available_stock.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", 'YRP Bin'):
		return
	cols = frappe.db.sql("SHOW COLUMNS FROM `tabYRP Bin` LIKE 'reserved_qty'")
	if cols:
		frappe.db.sql_ddl("ALTER TABLE `tabYRP Bin` DROP COLUMN `reserved_qty`")
	# Frappe metadata cleanup
	frappe.db.delete("DocField", {"parent": 'YRP Bin', "fieldname": "reserved_qty"})
	frappe.clear_cache(doctype='YRP Bin')
