"""Whitelisted stock APIs consumed by the Vue StockItemEditor and reports."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_conversion_factor
from yrp.stock.utils import get_stock_balance as _get_stock_balance


@frappe.whitelist()
def get_stock_dimensions_for_ui():
	"""Return dimension config + label/options for the Vue editor."""
	dims = get_stock_dimensions()
	out = []
	for d in dims:
		out.append({
			"fieldname": d["fieldname"],
			"label": d["label"],
			"options": d["dimension_doctype"],
			"mandatory": d["mandatory"],
			"in_valuation": d["in_valuation"],
			"is_production_group": d["is_production_group"],
		})
	return out


@frappe.whitelist()
def get_stock_balance(item, warehouse, posting_date=None, posting_time=None, **dimension_filters):
	return _get_stock_balance(item, warehouse, posting_date=posting_date, posting_time=posting_time, **dimension_filters)


@frappe.whitelist()
def get_valuation_rate(item, warehouse, posting_date=None, posting_time=None, **dimension_filters):
	qty, rate = _get_stock_balance(
		item, warehouse, posting_date=posting_date, posting_time=posting_time,
		with_valuation_rate=True, **dimension_filters,
	)
	return rate


@frappe.whitelist()
def get_stock_balance_for_items(items, warehouse, **dimension_filters):
	"""Bulk balance lookup. ``items`` may be a JSON list."""
	import json as _json
	if isinstance(items, str):
		try:
			items = _json.loads(items)
		except (ValueError, _json.JSONDecodeError):
			frappe.throw(_("Invalid item list format"))
	out = {}
	for it in items:
		out[it] = _get_stock_balance(it, warehouse, **dimension_filters)
	return out


@frappe.whitelist()
def get_total_stock(item_code, filters=None):
	"""Aggregate Bin.actual_qty and stock_value for an item across the
	provided filters (warehouse and any configured dimension fieldname).

	B.2: live SUM aggregator.
	"""
	import json as _json

	if isinstance(filters, str):
		try:
			filters = _json.loads(filters)
		except (ValueError, _json.JSONDecodeError):
			frappe.throw(_("Invalid filters JSON"))
	filters = filters or {}

	from yrp.stock.dimensions import assert_safe_fieldname

	allowed = {"warehouse"}
	allowed.update(d["fieldname"] for d in get_stock_dimensions())

	conds = ["item_code = %s"]
	values = [item_code]
	for key, val in filters.items():
		if key not in allowed or val is None:
			continue
		assert_safe_fieldname(key)
		conds.append(f"`{key}` = %s")
		values.append(val)

	row = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(actual_qty), 0) AS actual_qty,
		       COALESCE(SUM(stock_value), 0) AS stock_value
		FROM `tabBin`
		WHERE {' AND '.join(conds)}
		""",
		tuple(values),
		as_dict=True,
	)
	return {
		"actual_qty": flt(row[0]["actual_qty"]) if row else 0.0,
		"stock_value": flt(row[0]["stock_value"]) if row else 0.0,
	}


@frappe.whitelist()
def get_item_uom_and_rate(item):
	"""UOM, conversion factors, and last incoming rate for an item variant."""
	parent = frappe.db.get_value("Item Variant", item, "item")
	stock_uom = frappe.db.get_value("Item", parent, "default_unit_of_measure") if parent else None
	conversions = frappe.get_all(
		"UOM Conversion Detail",
		filters={"parent": parent},
		fields=["uom", "conversion_factor"],
	) if parent else []
	last_rate = frappe.db.get_value(
		"Stock Ledger Entry",
		{"item": item, "is_cancelled": 0, "qty": [">", 0]},
		"valuation_rate",
		order_by="posting_datetime desc, creation desc",
	) or 0.0
	return {
		"stock_uom": stock_uom,
		"conversions": conversions,
		"last_rate": flt(last_rate),
	}


@frappe.whitelist()
def warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Typeahead for Warehouse Link controls; honors Warehouse User restriction.

	Uses QueryBuilder instead of raw SQL to prevent injection via filter keys.
	Only known Warehouse fields are accepted as filters.
	"""
	# Only allow filtering on known Warehouse fields
	ALLOWED_FILTER_FIELDS = {"name", "disabled", "is_transit", "default_supplier"}

	wh = frappe.qb.DocType("Warehouse")
	q = frappe.qb.from_(wh).select(wh.name).where(wh.disabled == 0)

	# Apply user-provided filters (only whitelisted fields)
	if filters:
		for key, value in filters.items():
			if key not in ALLOWED_FILTER_FIELDS:
				continue
			q = q.where(wh[key] == value)

	# Text search
	if txt:
		q = q.where(wh.name.like(f"%{txt}%"))

	# Restrict to warehouses this user has access to
	user = frappe.session.user
	restricted = frappe.db.sql_list(
		"SELECT DISTINCT parent FROM `tabWarehouse User` WHERE user=%s", user
	)
	if restricted:
		q = q.where(wh.name.isin(restricted))

	q = q.limit(cint(page_len)).offset(cint(start))
	return q.run()
