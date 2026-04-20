# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CACHE_KEY = "yrp_stock_dimensions"

# DocTypes that receive dimension Link fields for ALL dimensions
STOCK_DOCTYPES = [
	"Stock Ledger Entry",
	"Bin",
	"Stock Entry Detail",
	"Stock Update Detail",
	"Stock Reconciliation Item",
	"Stock Reservation Entry",
	"Repost Item Valuation",
	"Delivery Challan Item",
	"Goods Received Note Item",
]

# DocTypes that receive dimension Link fields ONLY for the production group dimension
OPERATIONAL_DOCTYPES = [
	"Work Order",
	"Delivery Challan",
	"Goods Received Note",
]


def get_stock_dimensions():
	"""Return list of active stock dimensions from cache or DB."""
	dims = frappe.cache().get_value(CACHE_KEY)
	if dims is None:
		dims = frappe.get_all(
			"YRP Stock Dimension",
			fields=["dimension_doctype", "fieldname", "label", "mandatory", "in_valuation", "is_production_group"],
			parent_doctype="YRP Stock Settings",
			order_by="idx asc",
		)
		frappe.cache().set_value(CACHE_KEY, dims)
	return dims


def clear_dimension_cache(doc=None, method=None):
	"""Called on YRP Stock Settings save."""
	frappe.cache().delete_value(CACHE_KEY)


def get_dimension_fieldnames():
	"""Return list of fieldnames for all configured stock dimensions."""
	return [d["fieldname"] for d in get_stock_dimensions()]


def get_valuation_dimensions():
	"""Return fieldnames of dimensions that affect stock valuation grouping."""
	return [d["fieldname"] for d in get_stock_dimensions() if d["in_valuation"]]


def get_production_group():
	"""Return the single dimension that serves as the production group, or None."""
	for d in get_stock_dimensions():
		if d["is_production_group"]:
			return d
	return None


def get_mandatory_dimensions():
	"""Return dimensions that are mandatory on every stock transaction."""
	return [d for d in get_stock_dimensions() if d["mandatory"]]


@frappe.whitelist()
def create_dimension_fields():
	"""
	Read configured dimensions from YRP Stock Settings and create
	Custom Fields on target DocTypes. Idempotent — safe to run multiple times.
	"""
	dimensions = get_stock_dimensions()
	if not dimensions:
		frappe.msgprint("No stock dimensions configured in YRP Stock Settings.")
		return

	custom_fields = {}

	for dim in dimensions:
		field_def = {
			"fieldname": dim["fieldname"],
			"fieldtype": "Link",
			"options": dim["dimension_doctype"],
			"label": dim["label"],
			"insert_after": _get_insert_after(dim),
			"reqd": dim["mandatory"],
		}

		# All dimensions → stock DocTypes
		for dt in STOCK_DOCTYPES:
			if not frappe.db.exists("DocType", dt):
				continue
			custom_fields.setdefault(dt, []).append(field_def.copy())

		# Production group → also operational DocTypes (headers)
		if dim["is_production_group"]:
			for dt in OPERATIONAL_DOCTYPES:
				if not frappe.db.exists("DocType", dt):
					continue
				custom_fields.setdefault(dt, []).append(field_def.copy())

	if custom_fields:
		create_custom_fields(custom_fields, update=True)
		_ensure_bin_unique_constraint(dimensions)
		frappe.db.commit()


def _ensure_bin_unique_constraint(dimensions):
	"""Create a unique index on Bin for (item_code, warehouse, *dimension_fields).

	This prevents duplicate Bins when concurrent requests call get_or_make_bin().
	The index is idempotent — safe to call on every migrate.
	"""
	columns = ["item_code", "warehouse"] + [d["fieldname"] for d in dimensions]
	index_name = "unique_bin_dimension"

	# Validate column names — only lowercase alphanumeric + underscore allowed
	for col in columns:
		if not re.match(r"^[a-z][a-z0-9_]*$", col):
			frappe.throw(f"Invalid dimension fieldname for index: {col}")

	# Drop old index if column set changed (idempotent rebuild)
	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabBin` WHERE Key_name = %s", index_name, as_dict=True
	)
	if existing:
		existing_cols = sorted(r["Column_name"] for r in existing)
		if existing_cols != sorted(columns):
			frappe.db.sql(f"ALTER TABLE `tabBin` DROP INDEX `{index_name}`")
		else:
			return  # already correct

	col_list = ", ".join(f"`{c}`" for c in columns)
	frappe.db.sql(f"ALTER TABLE `tabBin` ADD UNIQUE INDEX `{index_name}` ({col_list})")


def _get_insert_after(dim):
	"""Determine where to insert the custom field. Default: after 'item' or at the end."""
	return "item"
