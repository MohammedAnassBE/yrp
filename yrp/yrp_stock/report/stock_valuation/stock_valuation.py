"""Stock Valuation Report — dimension-aware.

Shows the current stock value for each (item, warehouse, *dimensions)
bucket as of a given date. Computes qty, valuation rate, and total
stock value from the last Stock Ledger Entry per bucket.
"""

from typing import Dict, List

import frappe
from frappe import _
from frappe.utils import flt

from yrp.stock.dimensions import get_stock_dimensions, get_dimension_fieldnames
from yrp.stock.stock_ledger import get_previous_sle
from yrp.stock.utils import get_combine_datetime


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dims = get_stock_dimensions()
	dim_fields = get_dimension_fieldnames()

	columns = get_columns(dims)
	data = get_data(filters, dim_fields)

	chart_data = get_chart_data(data)
	return columns, data, None, chart_data


def get_columns(dims):
	columns = [
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Link", "options": "Item Variant", "width": 150},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Link", "options": "Item", "width": 150},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 100},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
	]
	for dim in dims:
		columns.append({
			"label": _(dim["label"]),
			"fieldname": dim["fieldname"],
			"fieldtype": "Link",
			"options": dim["dimension_doctype"],
			"width": 100,
		})
	columns.extend([
		{"label": _("Stock UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 90},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
		{"label": _("Valuation Rate"), "fieldname": "valuation_rate", "fieldtype": "Currency", "width": 120},
		{"label": _("Stock Value"), "fieldname": "stock_value", "fieldtype": "Currency", "width": 130},
		{"label": _("% of Total Value"), "fieldname": "pct_of_total", "fieldtype": "Percent", "width": 110},
	])
	return columns


def get_data(filters, dim_fields):
	to_date = filters.get("to_date") or frappe.utils.today()
	remove_zero = filters.get("remove_zero_balance_item")

	# Get all distinct (item, warehouse, *dims) buckets from SLE
	sle = frappe.qb.DocType("Stock Ledger Entry")
	group_fields = [sle.item, sle.warehouse] + [getattr(sle, fn) for fn in dim_fields]

	query = (
		frappe.qb.from_(sle)
		.select(*group_fields)
		.distinct()
		.where((sle.is_cancelled == 0) & (sle.posting_date <= to_date))
	)
	if filters.get("warehouse"):
		query = query.where(sle.warehouse == filters["warehouse"])
	if filters.get("item"):
		query = query.where(sle.item == filters["item"])
	elif filters.get("parent_item"):
		variants = frappe.get_all("Item Variant", filters={"item": filters["parent_item"]}, pluck="name")
		if variants:
			query = query.where(sle.item.isin(variants))
	for fn in dim_fields:
		if filters.get(fn):
			query = query.where(getattr(sle, fn) == filters[fn])

	buckets = query.run(as_dict=True)

	# For each bucket, get the last SLE state
	item_cache = {}
	data = []
	total_value = 0.0

	for bucket in buckets:
		args = frappe._dict({
			"item": bucket.item,
			"warehouse": bucket.warehouse,
			"posting_date": to_date,
			"posting_time": "23:59:59",
			"posting_datetime": get_combine_datetime(to_date, "23:59:59"),
		})
		for fn in dim_fields:
			args[fn] = bucket.get(fn)

		last_sle = get_previous_sle(args)
		qty = flt(last_sle.qty_after_transaction) if last_sle else 0.0
		val_rate = flt(last_sle.valuation_rate) if last_sle else 0.0
		stock_value = flt(qty * val_rate)

		if remove_zero and not qty:
			continue

		# Item details
		if bucket.item not in item_cache:
			parent = frappe.db.get_value("Item Variant", bucket.item, "item")
			item_cache[bucket.item] = frappe.db.get_value(
				"Item", parent,
				["name as item_name", "item_group", "default_unit_of_measure as stock_uom"],
				as_dict=True,
			) or {}
		details = item_cache[bucket.item]

		row = {
			"item": bucket.item,
			"item_name": details.get("item_name", ""),
			"item_group": details.get("item_group", ""),
			"warehouse": bucket.warehouse,
			"stock_uom": details.get("stock_uom", ""),
			"qty": qty,
			"valuation_rate": val_rate,
			"stock_value": stock_value,
		}
		for fn in dim_fields:
			row[fn] = bucket.get(fn) or ""

		data.append(row)
		total_value += stock_value

	# Calculate % of total
	for row in data:
		row["pct_of_total"] = flt(row["stock_value"] / total_value * 100, 2) if total_value else 0

	data.sort(key=lambda r: r["stock_value"], reverse=True)
	return data


def get_chart_data(data):
	if not data:
		return {}
	top = data[:10]
	return {
		"data": {
			"labels": [r["item"] for r in top],
			"datasets": [{"name": _("Stock Value"), "values": [r["stock_value"] for r in top]}],
		},
		"type": "bar",
	}
