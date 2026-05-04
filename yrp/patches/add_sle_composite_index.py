# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Add composite index on Stock Ledger Entry for bucket queries (D.3, Gap #23)."""

import frappe

INDEX_NAME = "idx_sle_bucket"
INDEX_COLS = ["item", "warehouse", "is_cancelled", "posting_datetime", "creation"]


def execute():
	if not frappe.db.exists("DocType", "Stock Ledger Entry"):
		return
	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabStock Ledger Entry` WHERE Key_name = %s", INDEX_NAME
	)
	if existing:
		return
	col_list = ", ".join(f"`{c}`" for c in INDEX_COLS)
	frappe.db.sql(
		f"ALTER TABLE `tabStock Ledger Entry` ADD INDEX `{INDEX_NAME}` ({col_list})"
	)
