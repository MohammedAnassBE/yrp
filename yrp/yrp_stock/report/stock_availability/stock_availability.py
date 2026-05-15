# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Stock Availability Report (D-014).

Columns:
  - actual_qty   : SUM(Bin.actual_qty) for the bucket
  - reserved     : SUM(Stock Reservation Entry.reserved_qty - delivered_qty)
  - on_order     : pending Purchase Order qty from open POs
  - wo_expected  : pending Work Order Receivables qty from open WOs
  - net_available = actual_qty - reserved

Filters:
  - item, warehouse, plus any configured dimension fieldname
  - group_by_dim: when on, every in_valuation=1 dimension is added as
    a column and a group-by axis (Gap #27).
"""

import frappe
from frappe import _
from frappe.utils import flt

from yrp.stock.dimensions import (
	DIMENSION_DEFAULT_SETTINGS_FIELD,
	assert_safe_fieldname,
	get_stock_dimensions,
	get_valuation_dimensions,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dims = get_stock_dimensions()
	val_dim_fields = get_valuation_dimensions()

	group_dims = val_dim_fields if filters.get("group_by_dim") else []
	defaults = _dimension_defaults(dims)

	rows = _query_bin(filters, dims, group_dims)
	rows = _attach_planning_quantities(rows, filters, dims, group_dims, defaults)
	enriched = _attach_reservation(rows, filters, dims, group_dims)
	return _columns(group_dims, dims), enriched


def _query_bin(filters, dims, group_dims):
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
		assert_safe_fieldname(fn)
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


def _attach_planning_quantities(rows, filters, dims, group_dims, defaults):
	row_map = {}
	for row in rows:
		_merge_row(row_map, row, group_dims, actual_qty=row.get("actual_qty"), stock_value=row.get("stock_value"))

	for row in _query_purchase_order_pending(filters, dims, group_dims, defaults):
		_merge_row(row_map, row, group_dims, on_order=row.get("on_order"))

	for row in _query_work_order_expected(filters, dims, group_dims, defaults):
		_merge_row(row_map, row, group_dims, wo_expected=row.get("wo_expected"))

	return sorted(
		row_map.values(),
		key=lambda row: (
			row.get("item_code") or "",
			row.get("warehouse") or "",
			*[row.get(fn) or "" for fn in group_dims],
		),
	)


def _query_purchase_order_pending(filters, dims, group_dims, defaults):
	if not frappe.db.exists("DocType", "Purchase Order"):
		return []

	sources = _dimension_sources("Purchase Order", "Purchase Order Item", dims)
	select_cols = [
		"poi.item_variant AS item_code",
		"po.delivery_warehouse AS warehouse",
		(
			"SUM(COALESCE(poi.pending_quantity, 0) * "
			"CASE WHEN COALESCE(poi.conversion_factor, 0) = 0 THEN 1 ELSE poi.conversion_factor END) AS on_order"
		),
	]
	group_cols = ["poi.item_variant", "po.delivery_warehouse"]
	values = {}
	for fn in group_dims:
		expr = _dimension_source_expression(sources.get(fn), "po", "poi", fn)
		if expr:
			select_cols.append(f"{expr} AS `{fn}`")
			group_cols.append(expr)
		else:
			param = f"default_{fn}"
			select_cols.append(f"%({param})s AS `{fn}`")
			values[param] = defaults.get(fn)

	conds = [
		"po.docstatus = 1",
		"COALESCE(po.open_status, 'Open') != 'Close'",
		"COALESCE(poi.pending_quantity, 0) > 0",
	]
	if filters.get("item"):
		conds.append("poi.item_variant = %(item)s")
		values["item"] = filters["item"]
	if filters.get("warehouse"):
		conds.append("po.delivery_warehouse = %(warehouse)s")
		values["warehouse"] = filters["warehouse"]
	if not _apply_dimension_filters(conds, values, filters, dims, sources, defaults, "po", "poi"):
		return []

	return frappe.db.sql(
		f"""
		SELECT {', '.join(select_cols)}
		FROM `tabPurchase Order Item` poi
		INNER JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE {' AND '.join(conds)}
		GROUP BY {', '.join(group_cols)}
		""",
		values,
		as_dict=True,
	)


def _query_work_order_expected(filters, dims, group_dims, defaults):
	if not frappe.db.exists("DocType", "Work Order"):
		return []

	sources = _dimension_sources("Work Order", "Work Order Receivables", dims)
	select_cols = [
		"wor.item_variant AS item_code",
		"wo.delivery_location AS delivery_location",
		"SUM(COALESCE(wor.pending_quantity, 0)) AS wo_expected",
	]
	group_cols = ["wor.item_variant", "wo.delivery_location"]
	values = {}
	for fn in group_dims:
		expr = _dimension_source_expression(sources.get(fn), "wo", "wor", fn)
		if expr:
			select_cols.append(f"{expr} AS `{fn}`")
			group_cols.append(expr)
		else:
			param = f"default_{fn}"
			select_cols.append(f"%({param})s AS `{fn}`")
			values[param] = defaults.get(fn)

	conds = [
		"wo.docstatus = 1",
		"COALESCE(wo.open_status, 'Open') != 'Close'",
		"COALESCE(wor.pending_quantity, 0) > 0",
	]
	if filters.get("item"):
		conds.append("wor.item_variant = %(item)s")
		values["item"] = filters["item"]
	if not _apply_dimension_filters(conds, values, filters, dims, sources, defaults, "wo", "wor"):
		return []

	rows = frappe.db.sql(
		f"""
		SELECT {', '.join(select_cols)}
		FROM `tabWork Order Receivables` wor
		INNER JOIN `tabWork Order` wo ON wo.name = wor.parent
		WHERE {' AND '.join(conds)}
		GROUP BY {', '.join(group_cols)}
		""",
		values,
		as_dict=True,
	)

	warehouse_by_supplier = _single_warehouse_by_supplier()
	out = []
	for row in rows:
		warehouse = warehouse_by_supplier.get(row.get("delivery_location"))
		if not warehouse:
			continue
		if filters.get("warehouse") and warehouse != filters["warehouse"]:
			continue
		row["warehouse"] = warehouse
		out.append(row)
	return out


def _attach_reservation(rows, filters, dims, group_dims):
	from yrp.stock.utils import get_sre_reserved_qty

	out = []
	for r in rows:
		dim_filter = _reservation_dimension_filter(r, filters, dims, group_dims)
		reserved = get_sre_reserved_qty(
			item_code=r["item_code"], warehouse=r["warehouse"], **dim_filter
		)
		actual = flt(r["actual_qty"])
		out.append(
			{
				**r,
				"reserved": flt(reserved),
				"net_available": actual - flt(reserved),
			}
		)
	return out


def _merge_row(row_map, row, group_dims, **values):
	key = _row_key(row, group_dims)
	target = row_map.setdefault(
		key,
		{
			"item_code": row.get("item_code"),
			"warehouse": row.get("warehouse"),
			"actual_qty": 0.0,
			"stock_value": 0.0,
			"on_order": 0.0,
			"wo_expected": 0.0,
		},
	)
	for fn in group_dims:
		target[fn] = row.get(fn)
	for fieldname, value in values.items():
		target[fieldname] = flt(target.get(fieldname)) + flt(value)


def _row_key(row, group_dims):
	return (
		row.get("item_code"),
		row.get("warehouse"),
		*((fn, row.get(fn)) for fn in group_dims),
	)


def _dimension_defaults(dims):
	defaults = {}
	for dim in dims:
		fn = dim["fieldname"]
		settings_field = DIMENSION_DEFAULT_SETTINGS_FIELD.get(fn)
		if not settings_field:
			continue
		defaults[fn] = frappe.db.get_single_value("YRP Stock Settings", settings_field)
	return defaults


def _dimension_sources(parent_doctype, child_doctype, dims):
	parent_meta = frappe.get_meta(parent_doctype)
	child_meta = frappe.get_meta(child_doctype)
	sources = {}
	for dim in dims:
		fn = dim["fieldname"]
		assert_safe_fieldname(fn)
		if parent_meta.has_field(fn):
			sources[fn] = "parent"
		elif child_meta.has_field(fn):
			sources[fn] = "child"
		else:
			sources[fn] = None
	return sources


def _dimension_source_expression(source, parent_alias, child_alias, fn):
	assert_safe_fieldname(fn)
	if source == "parent":
		return f"{parent_alias}.`{fn}`"
	if source == "child":
		return f"{child_alias}.`{fn}`"
	return None


def _apply_dimension_filters(conds, values, filters, dims, sources, defaults, parent_alias, child_alias):
	for dim in dims:
		fn = dim["fieldname"]
		if not filters.get(fn):
			continue
		expr = _dimension_source_expression(sources.get(fn), parent_alias, child_alias, fn)
		if expr:
			conds.append(expr + f" = %({fn})s")
			values[fn] = filters[fn]
			continue
		if defaults.get(fn) and filters[fn] == defaults[fn]:
			continue
		return False
	return True


def _reservation_dimension_filter(row, filters, dims, group_dims):
	dim_filter = {}
	for dim in dims:
		fn = dim["fieldname"]
		if fn in group_dims:
			dim_filter[fn] = row.get(fn)
		elif filters.get(fn):
			dim_filter[fn] = filters[fn]
	return dim_filter


def _single_warehouse_by_supplier():
	rows = frappe.db.sql(
		"""
		SELECT supplier, MIN(name) AS warehouse, COUNT(*) AS warehouse_count
		FROM `tabWarehouse`
		WHERE disabled = 0 AND COALESCE(supplier, '') != ''
		GROUP BY supplier
		HAVING warehouse_count = 1
		""",
		as_dict=True,
	)
	return {row.supplier: row.warehouse for row in rows}


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
