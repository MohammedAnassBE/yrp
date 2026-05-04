# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Stock Availability Report (D-014).

Columns:
  - actual_qty   : SUM(Bin.actual_qty) for the bucket
  - reserved     : SUM(Stock Reservation Entry.reserved_qty - delivered_qty)
  - on_order     : reserved for future Purchase Order integration (returns 0 today)
  - wo_expected  : reserved for future Work Order integration (returns 0 today)
  - net_available = actual_qty - reserved

Filters:
  - item, warehouse, plus any configured dimension fieldname
  - group_by_dim: when on, every in_valuation=1 dimension is added as
    a column and a group-by axis (Gap #27).
"""

import frappe
from frappe import _
from frappe.utils import flt

from yrp.stock.dimensions import get_stock_dimensions, get_valuation_dimensions


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dims = get_stock_dimensions()
	val_dim_fields = get_valuation_dimensions()

	group_dims = val_dim_fields if filters.get("group_by_dim") else []

	rows = _query(filters, dims, group_dims)
	enriched = _attach_reservation(rows, group_dims)
	return _columns(group_dims, dims), enriched


def _query(filters, dims, group_dims):
	conds = ["1=1"]
	values = {}
	if filters.get("item"):
		conds.append("item_code = %(item)s")
		values["item"] = filters["item"]
	if filters.get("warehouse"):
		conds.append("warehouse = %(warehouse)s")
		values["warehouse"] = filters["warehouse"]
	for dim in dims:
		fn = dim["fieldname"]
		if filters.get(fn):
			conds.append(f"`{fn}` = %({fn})s")
			values[fn] = filters[fn]

	select_cols = ["item_code", "warehouse"]
	group_cols = ["item_code", "warehouse"]
	for fn in group_dims:
		select_cols.append(f"`{fn}`")
		group_cols.append(f"`{fn}`")

	rows = frappe.db.sql(
		f"""
		SELECT {', '.join(select_cols)},
		       COALESCE(SUM(actual_qty), 0) AS actual_qty,
		       COALESCE(SUM(stock_value), 0) AS stock_value
		FROM `tabBin`
		WHERE {' AND '.join(conds)}
		GROUP BY {', '.join(group_cols)}
		ORDER BY item_code, warehouse
		""",
		values,
		as_dict=True,
	)
	return rows


def _attach_reservation(rows, group_dims):
	from yrp.stock.utils import get_sre_reserved_qty

	out = []
	for r in rows:
		dim_filter = {fn: r.get(fn) for fn in group_dims}
		reserved = get_sre_reserved_qty(
			item_code=r["item_code"], warehouse=r["warehouse"], **dim_filter
		)
		actual = flt(r["actual_qty"])
		out.append(
			{
				**r,
				"reserved": flt(reserved),
				"on_order": 0.0,
				"wo_expected": 0.0,
				"net_available": actual - flt(reserved),
			}
		)
	return out


def _columns(group_dims, dims):
	cols = [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item Variant", "width": 180},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 160},
	]
	dim_lookup = {d["fieldname"]: d for d in dims}
	for fn in group_dims:
		dim = dim_lookup.get(fn) or {}
		cols.append(
			{
				"label": dim.get("label") or fn,
				"fieldname": fn,
				"fieldtype": "Link",
				"options": dim.get("dimension_doctype"),
				"width": 130,
			}
		)
	cols.extend(
		[
			{"label": _("Actual Qty"), "fieldname": "actual_qty", "fieldtype": "Float", "width": 110},
			{"label": _("Reserved"), "fieldname": "reserved", "fieldtype": "Float", "width": 100},
			{"label": _("On Order (PO)"), "fieldname": "on_order", "fieldtype": "Float", "width": 110},
			{"label": _("WO Expected"), "fieldname": "wo_expected", "fieldtype": "Float", "width": 110},
			{"label": _("Net Available"), "fieldname": "net_available", "fieldtype": "Float", "width": 120},
			{"label": _("Stock Value"), "fieldname": "stock_value", "fieldtype": "Currency", "width": 130},
		]
	)
	return cols
