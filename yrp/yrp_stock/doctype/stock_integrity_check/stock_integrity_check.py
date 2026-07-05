# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Stock Integrity Check (D-013) — daily SLE-vs-Bin reconciliation report.

Detects three categories of mismatch:
  - qty_mismatch       : Bin.actual_qty != latest SLE.qty_after_transaction for
                         the same (item, warehouse, *dims) bucket.
  - missing_bin        : SLE history exists for a bucket with no Bin row.
  - requires_backfill  : SLE rows have NULL on a currently-configured
                         dimension (Gap #15). Not fixable in-place; needs a
                         dedicated migration patch.

Per-row Fix and Fix-All re-derive Bin.actual_qty from the live SUM(qty) of
non-cancelled SLEs for the bucket. Reposting (forward valuation recompute)
is a separate concern handled by Repost Item Valuation.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now, today


class StockIntegrityCheck(Document):
	def on_submit(self):
		"""Submitting freezes the snapshot. Use the per-row / fix-all APIs to repair."""
		pass


def run_daily_check():
	"""Scheduler entry point — creates a fresh Stock Integrity Check.

	Hook in hooks.py scheduler_events.daily.
	"""
	from yrp.stock.dimensions import get_stock_dimensions

	dims = get_stock_dimensions()
	dim_fields = [d["fieldname"] for d in dims]

	doc = frappe.get_doc(
		{"doctype": "Stock Integrity Check", "check_date": today(), "status": "In Progress"}
	).insert(ignore_permissions=True)

	mismatches = _detect_qty_mismatches(dim_fields)
	missing = _detect_missing_bins(dim_fields)
	backfill = _detect_null_dims(dim_fields) if dim_fields else []

	for row in mismatches + missing + backfill:
		doc.append("results", row)

	doc.mismatches_count = len(doc.results)
	doc.fixed_count = 0
	doc.status = "Completed"
	doc.save(ignore_permissions=True)
	return doc.name


def _detect_qty_mismatches(dim_fields):
	"""Bin.actual_qty != live SUM(SLE.qty) for the same bucket."""
	from yrp.stock.dimensions import assert_safe_fieldname

	for fn in dim_fields:
		assert_safe_fieldname(fn)
	dim_select = ", ".join(f"`{fn}`" for fn in dim_fields) if dim_fields else ""
	dim_join_on = " AND ".join(
		f"(sle.`{fn}` = bin.`{fn}` OR (sle.`{fn}` IS NULL AND bin.`{fn}` IS NULL))"
		for fn in dim_fields
	)
	join_extra = (" AND " + dim_join_on) if dim_fields else ""
	bin_select = (", " + dim_select) if dim_fields else ""

	rows = frappe.db.sql(
		f"""
		SELECT bin.item_code, bin.warehouse, bin.actual_qty AS bin_qty,
		       COALESCE(SUM(CASE WHEN sle.is_cancelled = 0 THEN sle.qty ELSE 0 END), 0) AS sle_qty
		       {bin_select.replace(', `', ', bin.`')}
		FROM `tabBin` bin
		LEFT JOIN `tabStock Ledger Entry` sle
		  ON sle.item = bin.item_code
		 AND sle.warehouse = bin.warehouse
		 {join_extra}
		GROUP BY bin.name
		HAVING ABS(bin_qty - sle_qty) > 0.001
		""",
		as_dict=True,
	)
	return [
		{
			"item_code": r["item_code"],
			"warehouse": r["warehouse"],
			"dimensions_json": json.dumps({fn: r.get(fn) for fn in dim_fields}),
			"category": "qty_mismatch",
			"sle_qty": flt(r["sle_qty"]),
			"bin_qty": flt(r["bin_qty"]),
			"diff": flt(r["bin_qty"]) - flt(r["sle_qty"]),
		}
		for r in rows
	]


def _detect_missing_bins(dim_fields):
	"""SLE rows whose bucket has no Bin row (Gap #14)."""
	from yrp.stock.dimensions import assert_safe_fieldname

	for fn in dim_fields:
		assert_safe_fieldname(fn)
	dim_cols = (", " + ", ".join(f"sle.`{fn}`" for fn in dim_fields)) if dim_fields else ""
	join_match = " AND ".join(
		f"(sle.`{fn}` = b.`{fn}` OR (sle.`{fn}` IS NULL AND b.`{fn}` IS NULL))"
		for fn in dim_fields
	)
	on_extra = (" AND " + join_match) if dim_fields else ""

	rows = frappe.db.sql(
		f"""
		SELECT sle.item AS item_code, sle.warehouse,
		       COALESCE(SUM(sle.qty), 0) AS sle_qty
		       {dim_cols}
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabBin` b
		  ON b.item_code = sle.item
		 AND b.warehouse = sle.warehouse
		 {on_extra}
		WHERE sle.is_cancelled = 0
		  AND b.name IS NULL
		GROUP BY sle.item, sle.warehouse {(", " + ", ".join(f"sle.`{fn}`" for fn in dim_fields)) if dim_fields else ""}
		""",
		as_dict=True,
	)
	return [
		{
			"item_code": r["item_code"],
			"warehouse": r["warehouse"],
			"dimensions_json": json.dumps({fn: r.get(fn) for fn in dim_fields}),
			"category": "missing_bin",
			"sle_qty": flt(r["sle_qty"]),
			"bin_qty": 0.0,
			"diff": -flt(r["sle_qty"]),
		}
		for r in rows
	]


def _detect_null_dims(dim_fields):
	"""SLEs with NULL on any currently-configured dimension (Gap #15)."""
	from yrp.stock.dimensions import assert_safe_fieldname

	results = []
	for fn in dim_fields:
		assert_safe_fieldname(fn)
		# Skip dimensions that don't have a column (newly-added but not migrated).
		cols = frappe.db.sql(
			"SHOW COLUMNS FROM `tabStock Ledger Entry` LIKE %s", fn
		)
		if not cols:
			continue
		rows = frappe.db.sql(
			f"""
			SELECT item AS item_code, warehouse, COUNT(*) AS row_count
			FROM `tabStock Ledger Entry`
			WHERE is_cancelled = 0 AND (`{fn}` IS NULL OR `{fn}` = '')
			GROUP BY item, warehouse
			""",
			as_dict=True,
		)
		for r in rows:
			results.append(
				{
					"item_code": r["item_code"],
					"warehouse": r["warehouse"],
					"dimensions_json": json.dumps({fn: None}),
					"category": "requires_backfill",
					"sle_qty": flt(r["row_count"]),
					"bin_qty": 0.0,
					"diff": 0.0,
				}
			)
	return results


# ---------------------------------------------------------------
# Fix actions
# ---------------------------------------------------------------
@frappe.whitelist()
def fix_integrity_row(parent_name, row_name):
	"""Re-derive Bin.actual_qty from live SUM(SLE.qty) for one row.

	Disabled for `requires_backfill` — those need a dedicated migration patch.
	"""
	row = frappe.get_doc("Stock Integrity Check Item", row_name)
	if row.parent != parent_name:
		frappe.throw(_("Row does not belong to this Integrity Check"))
	if row.fixed:
		return
	if row.category == "requires_backfill":
		frappe.throw(_("requires_backfill rows must be fixed via a dedicated patch."))

	from yrp.stock.dimensions import assert_safe_fieldname

	dim_filters = json.loads(row.dimensions_json or "{}")

	# Recompute live SUM
	conds = ["item = %s", "warehouse = %s", "is_cancelled = 0"]
	values = [row.item_code, row.warehouse]
	for fn, val in dim_filters.items():
		assert_safe_fieldname(fn)
		if val is None or val == "":
			conds.append(f"(`{fn}` IS NULL OR `{fn}` = '')")
		else:
			conds.append(f"`{fn}` = %s")
			values.append(val)
	live_sum = (
		frappe.db.sql(
			f"SELECT COALESCE(SUM(qty), 0) FROM `tabStock Ledger Entry` WHERE {' AND '.join(conds)}",
			tuple(values),
		)[0][0]
		or 0
	)
	live_sum = flt(live_sum)

	# Find or create Bin and update.
	from yrp.stock.utils import get_or_make_bin

	bin_name = get_or_make_bin(row.item_code, row.warehouse, **dim_filters)
	frappe.db.set_value("Bin", bin_name, "actual_qty", live_sum, update_modified=False)

	row.db_set("fixed", 1)
	row.db_set("fixed_by", frappe.session.user)
	row.db_set("fixed_at", now())

	parent = frappe.get_doc("Stock Integrity Check", parent_name)
	parent.db_set("fixed_count", (parent.fixed_count or 0) + 1)


@frappe.whitelist()
def fix_all_integrity(parent_name):
	"""Background fix-all (Gap #26)."""
	frappe.enqueue(
		"yrp.yrp_stock.doctype.stock_integrity_check.stock_integrity_check._fix_all_worker",
		parent_name=parent_name,
		queue="long",
		timeout=3600,
	)
	return {"queued": True}


def _fix_all_worker(parent_name):
	parent = frappe.get_doc("Stock Integrity Check", parent_name)
	for row in parent.results:
		if row.fixed or row.category == "requires_backfill":
			continue
		try:
			fix_integrity_row(parent_name, row.name)
		except Exception:
			frappe.log_error(title=f"Stock Integrity fix failed: {row.name}")
	frappe.db.commit()
