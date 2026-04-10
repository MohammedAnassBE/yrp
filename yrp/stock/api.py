"""Whitelisted stock APIs consumed by the Vue StockItemEditor and reports."""

import frappe
from frappe.utils import flt

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
		items = _json.loads(items)
	out = {}
	for it in items:
		out[it] = _get_stock_balance(it, warehouse, **dimension_filters)
	return out


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
	"""Typeahead for Warehouse Link controls; honors Warehouse User restriction."""
	user = frappe.session.user
	conditions = ["w.disabled = 0"]
	values = {"txt": f"%{txt}%", "start": start, "page_len": page_len}
	if filters:
		for k, v in (filters or {}).items():
			conditions.append(f"w.{k} = %({k})s")
			values[k] = v

	# If Warehouse User restrictions exist for this user, filter to those warehouses
	restricted = frappe.db.sql_list(
		"""SELECT DISTINCT parent FROM `tabWarehouse User` WHERE user=%s""", user,
	)
	if restricted:
		conditions.append(f"w.name IN ({', '.join(['%s'] * len(restricted))})")
		values_list = list(values.values())
		query = f"""
			SELECT w.name FROM `tabWarehouse` w
			WHERE {' AND '.join(conditions)} AND w.name LIKE %s
			LIMIT %s, %s
		"""
		return frappe.db.sql(query, restricted + [values["txt"], values["start"], values["page_len"]])

	query = f"""
		SELECT w.name FROM `tabWarehouse` w
		WHERE {' AND '.join(conditions)} AND w.name LIKE %(txt)s
		LIMIT %(start)s, %(page_len)s
	"""
	return frappe.db.sql(query, values)
