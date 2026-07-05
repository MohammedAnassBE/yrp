# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Backfill Stock Reconciliation SLE movement qty.

Older Stock Reconciliation rows stored qty=0 and qty_after_transaction as the
counted balance. Reconciliation SLEs should store qty as the movement needed
to reach the counted balance, otherwise later replay from qty deltas loses the
physical-count adjustment.
"""

import frappe
from frappe.utils import flt


def execute():
	if not frappe.db.exists("DocType", "Stock Ledger Entry"):
		return

	from yrp.stock.dimensions import assert_safe_fieldname, get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	for fn in dim_fields:
		assert_safe_fieldname(fn)

	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": "Stock Reconciliation",
			"is_cancelled": 0,
		},
		fields=[
			"name",
			"item",
			"warehouse",
			"posting_datetime",
			"creation",
			"qty_after_transaction",
			*dim_fields,
		],
		order_by="posting_datetime asc, creation asc",
	)

	for row in rows:
		updates = {"reconciled_qty": flt(row.qty_after_transaction)}
		if abs(flt(row.qty)) < 0.0000001 and abs(flt(row.qty_after_transaction)) > 0.0000001:
			previous_qty = _get_previous_qty_after_transaction(row, dim_fields)
			updates["qty"] = flt(row.qty_after_transaction) - flt(previous_qty)
		frappe.db.set_value("Stock Ledger Entry", row.name, updates, update_modified=False)


def _get_previous_qty_after_transaction(row, dim_fields):
	conditions = [
		"item = %s",
		"warehouse = %s",
		"is_cancelled = 0",
		"name != %s",
		"(posting_datetime < %s OR (posting_datetime = %s AND creation < %s))",
	]
	values = [
		row.item,
		row.warehouse,
		row.name,
		row.posting_datetime,
		row.posting_datetime,
		row.creation,
	]

	for fn in dim_fields:
		value = row.get(fn)
		if value is None:
			conditions.append(f"`{fn}` IS NULL")
		else:
			conditions.append(f"`{fn}` = %s")
			values.append(value)

	previous = frappe.db.sql(
		f"""
		SELECT qty_after_transaction
		FROM `tabStock Ledger Entry`
		WHERE {" AND ".join(conditions)}
		ORDER BY posting_datetime DESC, creation DESC
		LIMIT 1
		""",
		values,
	)
	return flt(previous[0][0]) if previous else 0.0
