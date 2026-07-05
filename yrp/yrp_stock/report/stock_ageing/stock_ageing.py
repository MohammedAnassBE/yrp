"""Stock Ageing Report — dimension-aware.

Shows how long stock has been sitting using FIFO analysis. For each
(item, warehouse, *dimensions) bucket, computes:

- **Available Qty**: current stock on hand
- **Average Age**: weighted average days in stock (qty × days / total qty)
- **Age Ranges**: qty breakdown into configurable brackets (e.g. 0-30, 31-60, 61-90, 90+)
  Helps identify slow-moving or dead stock that may need clearance
- **Earliest / Latest**: the oldest and newest stock entry dates

The FIFO queue tracks each incoming batch by posting date. Outgoing
transactions consume from the oldest batch first. What remains in the
queue represents the current stock with its original entry dates —
that's what drives the age calculation.

Use cases:
- Identify dead stock sitting beyond 90 days for write-off or clearance
- Monitor freshness for perishable or time-sensitive items
- Warehouse-wise ageing comparison to optimize stock distribution
- Dimension-level visibility (e.g. per lot, per batch) for granular tracking
"""

from operator import itemgetter
from typing import Dict, List, Tuple

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt

from yrp.stock.ageing import FIFOSlots, get_average_age, get_range_age
from yrp.stock.dimensions import get_stock_dimensions, get_dimension_fieldnames


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dims = get_stock_dimensions()
	dim_fields = get_dimension_fieldnames()
	columns = get_columns(filters, dims)

	item_details = FIFOSlots(filters).generate()
	data = format_report_data(filters, item_details, filters.get("to_date"), dim_fields)

	chart_data = get_chart_data(data, filters)
	return columns, data, None, chart_data


def format_report_data(filters, item_details, to_date, dim_fields):
	_func = itemgetter(1)
	data = []
	precision = cint(frappe.db.get_single_value("System Settings", "float_precision", cache=True)) or 3

	for key, item_dict in item_details.items():
		if not flt(item_dict.get("total_qty"), precision):
			continue

		details = item_dict["details"]
		fifo_queue = sorted(filter(_func, item_dict["fifo_queue"]), key=_func)
		if not fifo_queue:
			continue

		average_age = get_average_age(fifo_queue, to_date)
		earliest_age = date_diff(to_date, fifo_queue[0][1])
		latest_age = date_diff(to_date, fifo_queue[-1][1])
		range1, range2, range3, above_range3 = get_range_age(
			fifo_queue, to_date,
			cint(filters.get("range1", 30)),
			cint(filters.get("range2", 60)),
			cint(filters.get("range3", 90)),
		)

		row = {
			"item": details.get("item") or (key[0] if isinstance(key, tuple) else key),
			"item_name": details.get("item_name", ""),
			"item_group": details.get("item_group", ""),
			"stock_uom": details.get("stock_uom", ""),
		}

		if filters.get("show_warehouse_wise_stock") and isinstance(key, tuple) and len(key) >= 2:
			row["warehouse"] = key[1]
			# Dimension values from the key
			for i, fn in enumerate(dim_fields):
				idx = 2 + i
				row[fn] = key[idx] if idx < len(key) else ""

		row.update({
			"qty": flt(item_dict.get("total_qty"), precision),
			"average_age": average_age,
			"range1": flt(range1, precision),
			"range2": flt(range2, precision),
			"range3": flt(range3, precision),
			"above_range3": flt(above_range3, precision),
			"earliest": earliest_age,
			"latest": latest_age,
		})

		data.append(row)
	return data


def get_columns(filters, dims):
	range_columns = []
	setup_ageing_columns(filters, range_columns)

	columns = [
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Link", "options": "Item Variant", "width": 150},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 150},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 100},
	]

	if filters.get("show_warehouse_wise_stock"):
		columns.append(
			{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		)
		for dim in dims:
			columns.append({
				"label": _(dim["label"]),
				"fieldname": dim["fieldname"],
				"fieldtype": "Link",
				"options": dim["dimension_doctype"],
				"width": 100,
			})

	columns.extend([
		{"label": _("Available Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
		{"label": _("Average Age"), "fieldname": "average_age", "fieldtype": "Float", "width": 100},
	])
	columns.extend(range_columns)
	columns.extend([
		{"label": _("Earliest"), "fieldname": "earliest", "fieldtype": "Int", "width": 80},
		{"label": _("Latest"), "fieldname": "latest", "fieldtype": "Int", "width": 80},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 100},
	])
	return columns


def setup_ageing_columns(filters, range_columns):
	r1 = cint(filters.get("range1", 30))
	r2 = cint(filters.get("range2", 60))
	r3 = cint(filters.get("range3", 90))
	ranges = [
		f"0 - {r1}",
		f"{r1 + 1} - {r2}",
		f"{r2 + 1} - {r3}",
		_("{0} - Above").format(r3 + 1),
	]
	for i, label in enumerate(ranges):
		fieldname = "range" + str(i + 1) if i < 3 else "above_range3"
		range_columns.append({
			"label": _("Age ({0})").format(label),
			"fieldname": fieldname,
			"fieldtype": "Float",
			"width": 140,
		})


def get_chart_data(data, filters):
	if not data:
		return {}
	# Aggregate by item for chart (even in warehouse-wise mode)
	item_age = {}
	for r in data:
		item = r.get("item")
		if item not in item_age:
			item_age[item] = {"total_qty_age": 0.0, "total_qty": 0.0}
		qty = r.get("qty", 0)
		item_age[item]["total_qty_age"] += r.get("average_age", 0) * qty
		item_age[item]["total_qty"] += qty

	chart_items = []
	for item, vals in item_age.items():
		avg = vals["total_qty_age"] / vals["total_qty"] if vals["total_qty"] else 0
		chart_items.append({"item": item, "average_age": flt(avg, 2)})

	chart_items.sort(key=lambda r: r["average_age"], reverse=True)
	top = chart_items[:10]
	return {
		"data": {
			"labels": [r["item"] for r in top],
			"datasets": [{"name": _("Average Age"), "values": [r["average_age"] for r in top]}],
		},
		"type": "bar",
	}
