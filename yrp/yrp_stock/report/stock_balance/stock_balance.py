"""Stock Balance Report — dimension-aware.

Shows opening qty/value, in/out movement, and closing balance grouped by
(item, warehouse, *stock_dimensions) for a given date range.
"""

from typing import Any, Dict, List

import frappe
from frappe import _
from frappe.query_builder.functions import CombineDatetime
from frappe.utils import cint, date_diff, flt, getdate

from yrp.stock.ageing import FIFOSlots, get_average_age
from yrp.stock.dimensions import get_stock_dimensions, get_dimension_fieldnames


SLEntry = Dict[str, Any]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dim_fields = get_dimension_fieldnames()
	dims = get_stock_dimensions()

	columns = get_columns(filters, dims)
	items = get_items(filters)
	sle = get_stock_ledger_entries(filters, items, dim_fields)

	if not sle:
		return columns, []

	if filters.get("show_stock_ageing_data"):
		filters["show_warehouse_wise_stock"] = True
		item_wise_fifo_queue = FIFOSlots(filters, sle).generate()

	iwb_map = get_item_warehouse_map(filters, sle, dim_fields)
	item_map = get_item_details(items, sle, filters)

	data = []
	to_date = filters.get("to_date")

	for group_key in iwb_map:
		item = group_key[0]
		warehouse = group_key[1]
		if not item_map.get(item):
			continue
		qty_dict = iwb_map[group_key]
		if filters.get("remove_zero_balance_item") and qty_dict["bal_qty"] == 0:
			continue

		row = {"item": item, "warehouse": warehouse}
		# Add dimension values from the group key
		for i, fn in enumerate(dim_fields):
			row[fn] = group_key[2 + i] if (2 + i) < len(group_key) else ""
		row.update(item_map[item])
		row.update(qty_dict)

		if filters.get("show_stock_ageing_data"):
			fifo_data = item_wise_fifo_queue.get(group_key, {})
			fifo_queue = fifo_data.get("fifo_queue", [])
			ageing = {"average_age": 0, "earliest_age": 0, "latest_age": 0}
			if fifo_queue:
				sorted_q = sorted([e for e in fifo_queue if e[1]], key=lambda x: x[1])
				if sorted_q:
					ageing["average_age"] = get_average_age(sorted_q, to_date)
					ageing["earliest_age"] = date_diff(to_date, sorted_q[0][1])
					ageing["latest_age"] = date_diff(to_date, sorted_q[-1][1])
			row.update(ageing)

		data.append(row)

	return columns, data


def get_columns(filters, dims):
	columns = [
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Link", "options": "Item Variant", "width": 150},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Link", "options": "Item", "width": 150},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 100},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
	]
	# Dynamic dimension columns
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
		{"label": _("Balance Qty"), "fieldname": "bal_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Balance Value"), "fieldname": "bal_val", "fieldtype": "Currency", "width": 110},
		{"label": _("Opening Qty"), "fieldname": "opening_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Opening Value"), "fieldname": "opening_val", "fieldtype": "Currency", "width": 110},
		{"label": _("In Qty"), "fieldname": "in_qty", "fieldtype": "Float", "width": 80},
		{"label": _("In Value"), "fieldname": "in_val", "fieldtype": "Currency", "width": 80},
		{"label": _("Out Qty"), "fieldname": "out_qty", "fieldtype": "Float", "width": 80},
		{"label": _("Out Value"), "fieldname": "out_val", "fieldtype": "Currency", "width": 80},
		{"label": _("Valuation Rate"), "fieldname": "val_rate", "fieldtype": "Currency", "width": 100},
	])

	if filters.get("show_stock_ageing_data"):
		columns.extend([
			{"label": _("Average Age"), "fieldname": "average_age", "fieldtype": "Float", "width": 100},
			{"label": _("Earliest Age"), "fieldname": "earliest_age", "fieldtype": "Int", "width": 100},
			{"label": _("Latest Age"), "fieldname": "latest_age", "fieldtype": "Int", "width": 100},
		])

	if filters.get("show_variant_attributes"):
		for att in frappe.get_all("Item Attribute", pluck="name"):
			columns.append({"label": att, "fieldname": att, "width": 100})

	return columns


def get_stock_ledger_entries(filters, items, dim_fields):
	sle = frappe.qb.DocType("Stock Ledger Entry")

	select_fields = [
		sle.item, sle.warehouse, sle.posting_date, sle.qty,
		sle.valuation_rate, sle.voucher_type, sle.voucher_no,
		sle.qty_after_transaction, sle.stock_value_difference, sle.stock_value,
	]
	for fn in dim_fields:
		select_fields.append(getattr(sle, fn))

	query = (
		frappe.qb.from_(sle)
		.select(*select_fields)
		.where((sle.docstatus < 2) & (sle.is_cancelled == 0))
		.orderby(CombineDatetime(sle.posting_date, sle.posting_time))
		.orderby(sle.creation)
		.orderby(sle.qty)
	)

	if filters.get("to_date"):
		query = query.where(sle.posting_date <= filters["to_date"])
	if filters.get("warehouse"):
		query = query.where(sle.warehouse == filters["warehouse"])
	if items:
		query = query.where(sle.item.isin(items))
	for fn in dim_fields:
		if filters.get(fn):
			query = query.where(getattr(sle, fn) == filters[fn])

	return query.run(as_dict=True)


def get_item_warehouse_map(filters, sle, dim_fields):
	iwb_map = {}
	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))
	opening_vouchers = get_opening_vouchers(to_date)
	float_precision = cint(frappe.db.get_default("float_precision")) or 3

	for d in sle:
		group_key = (d.item, d.warehouse) + tuple(d.get(fn) or "" for fn in dim_fields)
		if group_key not in iwb_map:
			iwb_map[group_key] = frappe._dict(
				opening_qty=0.0, opening_val=0.0,
				in_qty=0.0, in_val=0.0,
				out_qty=0.0, out_val=0.0,
				bal_qty=0.0, bal_val=0.0,
				val_rate=0.0,
			)
		qty_dict = iwb_map[group_key]

		if d.voucher_type == "Stock Reconciliation":
			qty_diff = flt(d.qty_after_transaction) - flt(qty_dict.bal_qty)
		else:
			qty_diff = flt(d.qty)

		value_diff = flt(d.stock_value_difference)

		if d.posting_date < from_date or d.voucher_no in opening_vouchers.get(d.voucher_type, []):
			qty_dict.opening_qty += qty_diff
			qty_dict.opening_val += value_diff
		elif from_date <= d.posting_date <= to_date:
			if flt(qty_diff, float_precision) >= 0:
				qty_dict.in_qty += qty_diff
				qty_dict.in_val += value_diff
			else:
				qty_dict.out_qty += abs(qty_diff)
				qty_dict.out_val += abs(value_diff)

		qty_dict.val_rate = d.valuation_rate
		qty_dict.bal_qty += qty_diff
		qty_dict.bal_val += value_diff

	# Remove keys with no transactions
	pop_keys = []
	for key, qty_dict in iwb_map.items():
		no_txn = True
		for k, v in qty_dict.items():
			val = flt(v, float_precision)
			qty_dict[k] = val
			if k != "val_rate" and val:
				no_txn = False
		if no_txn:
			pop_keys.append(key)
	for key in pop_keys:
		iwb_map.pop(key)

	return iwb_map


def get_opening_vouchers(to_date):
	opening_vouchers = {"Stock Reconciliation": []}
	sr = frappe.qb.DocType("Stock Reconciliation")
	result = (
		frappe.qb.from_(sr)
		.select(sr.name)
		.where((sr.docstatus == 1) & (sr.posting_date <= to_date) & (sr.purpose == "Opening Stock"))
	).run(as_dict=True)
	for d in result:
		opening_vouchers["Stock Reconciliation"].append(d.name)
	return opening_vouchers


def get_items(filters):
	if item := filters.get("item"):
		return [item] if isinstance(item, str) else item
	item_filters = {}
	if parent_item := filters.get("parent_item"):
		item_filters["item"] = parent_item
	return frappe.get_all("Item Variant", filters=item_filters, pluck="name", order_by=None)


def get_item_details(items, sle, filters):
	item_details = {}
	if not items:
		items = list({d.item for d in sle})
	if not items:
		return item_details

	item_table = frappe.qb.DocType("Item")
	variant_table = frappe.qb.DocType("Item Variant")

	result = (
		frappe.qb.from_(item_table)
		.from_(variant_table)
		.select(
			variant_table.name,
			item_table.name.as_("item_name"),
			item_table.item_group,
			item_table.default_unit_of_measure.as_("stock_uom"),
		)
		.where((variant_table.name.isin(items)) & (item_table.name == variant_table.item))
	).run(as_dict=True)

	for row in result:
		item_details[row.name] = row

	if filters.get("show_variant_attributes"):
		attrs = frappe.get_all(
			"Item Variant Attribute",
			filters={"parent": ("in", list(item_details))},
			fields=["parent", "attribute", "attribute_value"],
		)
		for a in attrs:
			item_details.setdefault(a.parent, {})
			item_details[a.parent][a.attribute] = a.attribute_value

	return item_details
