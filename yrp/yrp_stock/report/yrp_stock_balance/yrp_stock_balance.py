"""Stock Balance Report — dimension-aware.

Shows opening qty/value, in/out movement, and closing balance grouped by
(item, warehouse, *stock_dimensions) for a given date range.
"""

import json
from typing import Any, Dict, List

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate

from yrp.attribute_links import value as _attribute_value
from yrp.stock.ageing import FIFOSlots, get_average_age
from yrp.stock.dimensions import get_dimension_fieldnames, get_stock_dimensions

SLEntry = dict[str, Any]


@frappe.whitelist()
def get_filter_dimensions():
	"""Dimension metadata shared by Desk and the web Stock Balance filters."""
	return [
		{key: dimension[key] for key in ("fieldname", "label", "dimension_doctype")}
		for dimension in get_stock_dimensions()
	]


def parse_list_filter(value):
	"""Accept MultiSelectList arrays, JSON arrays and scalar document names like MRP."""
	if not value:
		return []
	if isinstance(value, str):
		if value.lstrip().startswith("["):
			try:
				value = json.loads(value)
			except ValueError:
				value = [value]
		else:
			value = [value]
	if not isinstance(value, list):
		value = [value]
	return [entry for entry in value if entry]


@frappe.whitelist()
def get_item_filter_options(txt="", variant=0):
	"""Permission-aware options shared by the Desk and web multi-selects."""
	search = [["name", "like", f"%{txt}%"], ["item_name", "like", f"%{txt}%"]]
	base = {
		"or_filters": search,
		"fields": ["name as value", "item_name as description"],
		"order_by": "name",
		"limit": 20,
	}
	if cint(variant):
		return frappe.get_list("Item", filters={"variant_of": ["is", "set"]}, **base)
	templates = frappe.get_list("Item", filters={"variant_of": ["is", "not set"], "has_variants": 1}, **base)
	standalones = frappe.get_list(
		"Item",
		filters=[
			["Item", "variant_of", "is", "not set"],
			["Item", "has_variants", "=", 0],
			["Item Variant Attribute", "name", "is", "not set"],
		],
		**base,
	)
	return sorted(templates + standalones, key=lambda row: row["value"])[:20]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dim_fields = get_dimension_fieldnames()
	dims = get_stock_dimensions()

	columns = get_columns(filters, dims)
	items = get_items(filters)
	if items == []:
		# An explicit selection that resolves to nothing must never mean all items.
		return columns, []
	sle = get_stock_ledger_entries(filters, items, dim_fields)
	iwb_map = get_item_warehouse_map(filters, sle, dim_fields)

	if not iwb_map:
		return columns, []

	item_wise_fifo_queue = {}
	if filters.get("show_stock_ageing_data") or filters.get("show_inward_date_split"):
		filters["show_warehouse_wise_stock"] = True
		item_wise_fifo_queue = FIFOSlots(
			filters, get_stock_ledger_entries(filters, items, dim_fields)
		).generate()

	item_map = get_item_details(items, iwb_map, filters)
	warehouse_map = get_warehouse_names(iwb_map)

	data = []
	to_date = filters.get("to_date")

	for group_key in iwb_map:
		item = group_key[0]
		group_key[1]
		if not item_map.get(item):
			continue
		qty_dict = iwb_map[group_key]
		if filters.get("remove_zero_balance_item") and qty_dict["bal_qty"] == 0:
			continue

		row = _unpack_group_key(group_key, dim_fields)
		row["warehouse_name"] = warehouse_map.get(row["warehouse"], row["warehouse"])
		row.update(item_map[item])
		row.update(qty_dict)
		fifo_queue = item_wise_fifo_queue.get(group_key, {}).get("fifo_queue", [])

		if filters.get("show_inward_date_split"):
			breakdown = get_inward_date_breakdown(fifo_queue)
			row["inward_date_breakdown"] = breakdown
			row["inward_split"] = "\n".join(f"{entry['date']}: {entry['qty']:g}" for entry in breakdown)

		if filters.get("show_stock_ageing_data"):
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


def get_inward_date_breakdown(fifo_queue):
	"""Aggregate remaining FIFO stock into oldest-first inward-date buckets."""
	buckets = {}
	for qty, posting_date, *_rest in fifo_queue or []:
		qty = flt(qty)
		if not qty or not posting_date:
			continue
		date_key = getdate(posting_date)
		buckets[date_key] = buckets.get(date_key, 0.0) + qty

	return [
		{"date": date_key.strftime("%d-%m-%Y"), "qty": flt(buckets[date_key], 3)}
		for date_key in sorted(buckets)
		if flt(buckets[date_key], 3)
	]


def get_columns(filters, dims):
	columns = [
		{
			"label": _("Item"),
			"fieldname": "parent_item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{"label": _("Item Name"), "fieldname": "parent_item_name", "fieldtype": "Data", "width": 150},
		{
			"label": _("Item Variant"),
			"fieldname": "item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"label": _("Item Variant Name"),
			"fieldname": "item_name",
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"label": _("Item Group"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 100,
		},
		{
			"label": _("Warehouse Name"),
			"fieldname": "warehouse_name",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 120,
		},
	]
	# Dynamic dimension columns
	for dim in dims:
		columns.append(
			{
				"label": _(dim["label"]),
				"fieldname": dim["fieldname"],
				"fieldtype": "Link",
				"options": dim["dimension_doctype"],
				"width": 100,
			}
		)

	columns.extend(
		[
			{
				"label": _("Stock UOM"),
				"fieldname": "stock_uom",
				"fieldtype": "Link",
				"options": "UOM",
				"width": 90,
			},
			{"label": _("Balance Qty"), "fieldname": "bal_qty", "fieldtype": "Float", "width": 100},
			{"label": _("Balance Value"), "fieldname": "bal_val", "fieldtype": "Currency", "width": 110},
			{"label": _("Opening Qty"), "fieldname": "opening_qty", "fieldtype": "Float", "width": 100},
			{"label": _("Opening Value"), "fieldname": "opening_val", "fieldtype": "Currency", "width": 110},
			{"label": _("In Qty"), "fieldname": "in_qty", "fieldtype": "Float", "width": 80},
			{"label": _("In Value"), "fieldname": "in_val", "fieldtype": "Currency", "width": 80},
			{"label": _("Out Qty"), "fieldname": "out_qty", "fieldtype": "Float", "width": 80},
			{"label": _("Out Value"), "fieldname": "out_val", "fieldtype": "Currency", "width": 80},
			{"label": _("Valuation Rate"), "fieldname": "val_rate", "fieldtype": "Currency", "width": 100},
		]
	)

	if filters.get("show_inward_date_split"):
		balance_index = next(
			(index for index, column in enumerate(columns) if column.get("fieldname") == "bal_val"),
			len(columns) - 1,
		)
		columns.insert(
			balance_index + 1,
			{
				"label": _("Inward Date Split"),
				"fieldname": "inward_split",
				"fieldtype": "Data",
				"width": 220,
			},
		)

	if filters.get("show_stock_ageing_data"):
		columns.extend(
			[
				{"label": _("Average Age"), "fieldname": "average_age", "fieldtype": "Float", "width": 100},
				{"label": _("Earliest Age"), "fieldname": "earliest_age", "fieldtype": "Int", "width": 100},
				{"label": _("Latest Age"), "fieldname": "latest_age", "fieldtype": "Int", "width": 100},
			]
		)

	if filters.get("show_variant_attributes"):
		for att in frappe.get_all("Item Attribute", pluck="name"):
			columns.append({"label": att, "fieldname": att, "width": 100})

	return columns


def get_stock_ledger_entries(filters, items, dim_fields):
	if items == []:
		return
	sle = frappe.qb.DocType("YRP Stock Ledger Entry")

	select_fields = [
		sle.item,
		sle.warehouse,
		sle.posting_date,
		sle.qty,
		sle.valuation_rate,
		sle.voucher_type,
		sle.voucher_no,
		sle.qty_after_transaction,
		sle.stock_value_difference,
		sle.stock_value,
	]
	for fn in dim_fields:
		select_fields.append(getattr(sle, fn))

	query = (
		frappe.qb.from_(sle)
		.select(*select_fields)
		.where((sle.docstatus < 2) & (sle.is_cancelled == 0))
		.orderby(sle.posting_datetime)
		.orderby(sle.creation)
	)

	if filters.get("to_date"):
		query = query.where(sle.posting_date <= filters["to_date"])
	if filters.get("warehouse"):
		warehouses = filters["warehouse"]
		if isinstance(warehouses, (list, tuple, set)):
			query = query.where(sle.warehouse.isin(list(warehouses)))
		else:
			query = query.where(sle.warehouse == warehouses)
	if suppliers := filters.get("supplier"):
		suppliers = [suppliers] if isinstance(suppliers, str) else list(suppliers)
		warehouse = frappe.qb.DocType("Warehouse")
		supplier_warehouses = (
			frappe.qb.from_(warehouse).select(warehouse.name).where(warehouse.supplier.isin(suppliers))
		)
		query = query.where(sle.warehouse.isin(supplier_warehouses))
	if items:
		query = query.where(sle.item.isin(items))
	for fn in dim_fields:
		if values := parse_list_filter(filters.get(fn)):
			query = query.where(getattr(sle, fn).isin(values))

	# Broad date ranges otherwise favour a table scan and filesort on MariaDB.
	if frappe.db.db_type == "mariadb":
		warehouses = filters.get("warehouse")
		if not items and warehouses and (isinstance(warehouses, str) or len(warehouses) == 1):
			query = query.force_index("warehouse_posting_datetime_creation_index")
		elif not (items or warehouses or filters.get("supplier")) and not any(
			parse_list_filter(filters.get(fn)) for fn in dim_fields
		):
			query = query.force_index("posting_datetime_creation_index")

	# flt can read rounding settings; load them before opening the streaming cursor.
	frappe.get_cached_doc("System Settings")
	with frappe.db.unbuffered_cursor():
		yield from query.run(as_dict=True, as_iterator=True)


def get_item_warehouse_map(filters, sle, dim_fields):
	iwb_map = {}
	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))
	opening_vouchers = get_opening_vouchers(to_date)
	float_precision = cint(frappe.db.get_default("float_precision")) or 3

	for d in sle:
		get = d.get
		group_key = (get("item"), get("warehouse"), *[get(fn) or "" for fn in dim_fields])
		qty_dict = iwb_map.get(group_key)
		if qty_dict is None:
			qty_dict = iwb_map[group_key] = frappe._dict(
				opening_qty=0.0,
				opening_val=0.0,
				in_qty=0.0,
				in_val=0.0,
				out_qty=0.0,
				out_val=0.0,
				bal_qty=0.0,
				bal_val=0.0,
				val_rate=0.0,
			)

		voucher_type = get("voucher_type")
		if voucher_type == "YRP Stock Reconciliation":
			qty_diff = flt(get("qty_after_transaction")) - flt(qty_dict["bal_qty"])
		else:
			qty_diff = flt(get("qty"))

		value_diff = flt(get("stock_value_difference"))
		posting_date = get("posting_date")

		if posting_date < from_date or get("voucher_no") in opening_vouchers.get(voucher_type, ()):
			qty_dict["opening_qty"] += qty_diff
			qty_dict["opening_val"] += value_diff
		elif from_date <= posting_date <= to_date:
			if flt(qty_diff, float_precision) >= 0:
				qty_dict["in_qty"] += qty_diff
				qty_dict["in_val"] += value_diff
			else:
				qty_dict["out_qty"] += abs(qty_diff)
				qty_dict["out_val"] += abs(value_diff)

		qty_dict["val_rate"] = get("valuation_rate")
		qty_dict["bal_qty"] += qty_diff
		qty_dict["bal_val"] += value_diff

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
	opening_vouchers = {"YRP Stock Reconciliation": set()}
	sr = frappe.qb.DocType("YRP Stock Reconciliation")
	result = (
		frappe.qb.from_(sr)
		.select(sr.name)
		.where((sr.docstatus == 1) & (sr.posting_date <= to_date) & (sr.purpose == "Opening Stock"))
	).run(as_dict=True)
	for d in result:
		opening_vouchers["YRP Stock Reconciliation"].add(d.name)
	return opening_vouchers


def get_items(filters):
	variants = parse_list_filter(filters.get("item"))
	parents = parse_list_filter(filters.get("parent_item"))
	if not parents:
		# None is unrestricted; [] is a selected filter with no matches.
		return variants or None
	item = frappe.qb.DocType("Item")
	query = (
		frappe.qb.from_(item)
		.select(item.name)
		.where(item.variant_of.isin(parents) | (item.name.isin(parents) & (item.has_variants == 0)))
	)
	if variants:
		query = query.where(item.name.isin(variants))
	return [row[0] for row in query.run()]


def get_warehouse_names(iwb_map):
	warehouse = frappe.qb.DocType("Warehouse")
	supplier = frappe.qb.DocType("Supplier")
	rows = (
		frappe.qb.from_(warehouse)
		.left_join(supplier)
		.on(warehouse.supplier == supplier.name)
		.select(warehouse.name, warehouse.warehouse_name, supplier.supplier_name)
		.where(warehouse.name.isin(list({key[1] for key in iwb_map})))
		.run(as_dict=True)
	)
	return {row.name: row.supplier_name or row.warehouse_name or row.name for row in rows}


def get_item_details(items, iwb_map, filters):
	item_details = {}
	if not items:
		items = list({key[0] for key in iwb_map})
	if not items:
		return item_details

	item_table = frappe.qb.DocType("Item")
	parent = item_table.as_("parent_item")
	result = (
		frappe.qb.from_(item_table)
		.left_join(parent)
		.on(item_table.variant_of == parent.name)
		.select(
			item_table.name,
			item_table.item_name,
			item_table.item_group,
			item_table.stock_uom,
			item_table.variant_of,
			parent.item_name.as_("parent_item_name"),
		)
		.where(item_table.name.isin(items))
	).run(as_dict=True)

	for row in result:
		row.parent_item = row.variant_of or row.name
		row.parent_item_name = row.parent_item_name or row.item_name
		item_details[row.name] = row

	if filters.get("show_variant_attributes"):
		attrs = frappe.get_all(
			"Item Variant Attribute",
			filters={"parent": ("in", list(item_details))},
			fields=["parent", "attribute", "attribute_value"],
		)
		for a in attrs:
			item_details.setdefault(a.parent, {})
			item_details[a.parent][a.attribute] = _attribute_value(a.attribute_value)

	return item_details


def _unpack_group_key(group_key, dim_fields):
	"""Unpack a group key tuple back into a dict with named fields."""
	row = {"item": group_key[0], "warehouse": group_key[1]}
	for i, fn in enumerate(dim_fields):
		row[fn] = group_key[2 + i] if (2 + i) < len(group_key) else ""
	return row
