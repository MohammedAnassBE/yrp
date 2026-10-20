import frappe

from yrp.stock.dimensions import get_stock_dimensions
from yrp.web_reports.registry import WebReport

STATIC_COLUMNS = {
	"parent_item": "Item",
	"parent_item_name": "Item Name",
	"item": "Item Variant",
	"item_name": "Item Variant Name",
	"item_group": "Item Group",
	"warehouse": "Warehouse",
	"warehouse_name": "Warehouse Name",
	"stock_uom": "Stock UOM",
	"bal_qty": "Balance Qty",
	"bal_val": "Balance Value",
	"opening_qty": "Opening Qty",
	"opening_val": "Opening Value",
	"in_qty": "In Qty",
	"in_val": "In Value",
	"out_qty": "Out Qty",
	"out_val": "Out Value",
	"val_rate": "Valuation Rate",
	"inward_split": "Inward Date Split",
	"average_age": "Average Age",
	"earliest_age": "Earliest Age",
	"latest_age": "Latest Age",
}


def get_reports() -> list[WebReport]:
	dimensions = list(get_stock_dimensions())
	filters = {
		"from_date": "Date",
		"to_date": "Date",
		"item": ("MultiSelectList", "Item"),
		"parent_item": ("MultiSelectList", "Item"),
		"warehouse": ("Link", "Warehouse"),
		"supplier": ("Link", "Supplier"),
		"remove_zero_balance_item": "Check",
		"show_variant_attributes": "Check",
		"show_stock_ageing_data": "Check",
		"show_inward_date_split": "Check",
	}
	link_sources = {
		"parent_item": "Item",
		"item": "Item",
		"item_group": "Item Group",
		"warehouse": "Warehouse",
	}
	base_columns = dict(STATIC_COLUMNS)
	for dimension in dimensions:
		filters[dimension["fieldname"]] = ("MultiSelectList", dimension["dimension_doctype"])
		link_sources[dimension["fieldname"]] = dimension["dimension_doctype"]
		base_columns[dimension["fieldname"]] = dimension["label"]
	for attribute in frappe.get_all("Item Attribute", fields=["name", "attribute_name"]):
		base_columns[attribute.name] = attribute.attribute_name or attribute.name
	return [
		WebReport(
			key="stock-balance",
			kind="frappe_report",
			report="YRP Stock Balance",
			filters=filters,
			link_sources=link_sources,
			base_columns=base_columns,
			permission_doctypes=("YRP Stock Ledger Entry",),
			background="always",
			dependency_doctypes="yrp.yrp_stock.web_reports.stock_balance_dependency_doctypes",
		)
	]


def stock_balance_dependency_doctypes(filters=None) -> list[str]:
	doctypes = {"YRP Stock Ledger Entry", "Item", "Item Group", "Warehouse", "Supplier", "UOM"}
	doctypes.update(dimension["dimension_doctype"] for dimension in get_stock_dimensions())
	return sorted(doctypes)
