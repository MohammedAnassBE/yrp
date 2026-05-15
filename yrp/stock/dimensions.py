# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CACHE_KEY = "yrp_stock_dimensions"
MANAGED_DIMENSION_FIELD_MARKER = "Managed by YRP Stock Dimension"

# Strict identifier whitelist for any dimension fieldname that ever reaches
# raw SQL via f-string interpolation. Validated at save time on YRP Stock
# Settings, and re-checked defensively before every SQL build.
_FIELDNAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def assert_safe_fieldname(fn):
	"""Raise if a fieldname can't be safely interpolated into raw SQL.

	Defense-in-depth: YRP Stock Settings.validate enforces this at save
	time, but every place that writes raw SQL with a dynamic fieldname
	calls this as well so a bad value can't survive a validation bypass.
	"""
	if not isinstance(fn, str) or not _FIELDNAME_RE.match(fn):
		frappe.throw(f"Invalid stock-dimension fieldname: {fn!r}")

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
	"Purchase Order",
	"Delivery Challan",
	"Goods Received Note",
	"Process Cost",
]

# These child tables belong to operational documents that already carry the
# production-group dimension on the header. The child row still may keep the
# field for traceability/back-compat, but it must not block save when the
# header controls the production group.
OPERATIONAL_CHILD_DOCTYPES = {
	"Delivery Challan Item",
	"Goods Received Note Item",
}


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


def append_production_group_filters(filters, source_doc, target_doctype):
	"""Append production-group filters when both docs carry the dimension field."""
	meta = frappe.get_meta(target_doctype)
	for dim in get_stock_dimensions():
		if not dim.get("is_production_group"):
			continue
		fieldname = dim["fieldname"]
		if not meta.get_field(fieldname):
			continue
		value = source_doc.get(fieldname)
		if value:
			filters.append([fieldname, "=", value])


def get_mandatory_dimensions():
	"""Return dimensions that are mandatory on every stock transaction."""
	return [d for d in get_stock_dimensions() if d["mandatory"]]


# Dimensions whose default value lives on YRP Stock Settings. Maps the
# dimension fieldname to the settings field that stores its default.
DIMENSION_DEFAULT_SETTINGS_FIELD = {
	"received_type": "default_received_type",
}


def apply_dimension_defaults(rows):
	"""Fill blank dimension values on child rows with the configured default.

	`rows` is any iterable of Frappe child docs or dicts. For each dimension
	whose fieldname appears in DIMENSION_DEFAULT_SETTINGS_FIELD, missing
	values are filled from the corresponding YRP Stock Settings field.
	"""
	if not rows:
		return
	dim_fieldnames = [
		d["fieldname"] for d in get_stock_dimensions()
		if d["fieldname"] in DIMENSION_DEFAULT_SETTINGS_FIELD
	]
	if not dim_fieldnames:
		return
	defaults = {}
	for fn in dim_fieldnames:
		settings_field = DIMENSION_DEFAULT_SETTINGS_FIELD[fn]
		val = frappe.db.get_single_value("YRP Stock Settings", settings_field)
		if val:
			defaults[fn] = val
	if not defaults:
		return
	for row in rows:
		for fn, default_value in defaults.items():
			current = row.get(fn) if hasattr(row, "get") else row.__dict__.get(fn)
			if not current:
				if hasattr(row, "set"):
					row.set(fn, default_value)
				else:
					row[fn] = default_value


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
			"reqd": 1,
			"description": MANAGED_DIMENSION_FIELD_MARKER,
		}

		# All dimensions → stock DocTypes
		for dt in STOCK_DOCTYPES:
			if not frappe.db.exists("DocType", dt):
				continue
			doc_field_def = field_def.copy()
			doc_field_def["insert_after"] = _get_insert_after(dim, dt)
			if dim["is_production_group"] and dt in OPERATIONAL_CHILD_DOCTYPES:
				doc_field_def["reqd"] = 0
			custom_fields.setdefault(dt, []).append(doc_field_def)

		# Production group → also operational DocTypes (headers)
		if dim["is_production_group"]:
			for dt in OPERATIONAL_DOCTYPES:
				if not frappe.db.exists("DocType", dt):
					continue
				doc_field_def = field_def.copy()
				doc_field_def["insert_after"] = _get_insert_after(dim, dt)
				custom_fields.setdefault(dt, []).append(doc_field_def)

	if custom_fields:
		create_custom_fields(custom_fields, update=True)
		_ensure_bin_unique_constraint(dimensions)
		_delete_orphan_dimension_fields(dimensions)
		frappe.db.commit()


def _delete_orphan_dimension_fields(dimensions):
	"""Bug A follow-on: when a dimension is removed from YRP Stock Settings,
	its Custom Field (created reqd=1) is left orphaned and would block every
	stock transaction with 'Value missing'. Sweep them up.

	Operates on STOCK_DOCTYPES + OPERATIONAL_DOCTYPES — every doctype that
	create_dimension_fields can touch. Only fields explicitly marked as
	YRP-managed are considered, so unrelated Custom Fields are never deleted.
	"""
	current_fieldnames = {d["fieldname"] for d in dimensions}
	target_doctypes = STOCK_DOCTYPES + OPERATIONAL_DOCTYPES
	rows = frappe.get_all(
		"Custom Field",
		filters={"dt": ("in", target_doctypes), "fieldtype": "Link"},
		fields=["name", "dt", "fieldname", "description"],
	)
	for row in rows:
		if row["fieldname"] in current_fieldnames:
			continue
		if MANAGED_DIMENSION_FIELD_MARKER not in (row.get("description") or ""):
			continue
		frappe.db.delete("Custom Field", {"name": row["name"]})


def _ensure_bin_unique_constraint(dimensions):
	"""Create a unique index on Bin for (item_code, warehouse, *dimension_fields).

	This prevents duplicate Bins when concurrent requests call get_or_make_bin().
	The index is idempotent — safe to call on every migrate.
	"""
	columns = ["item_code", "warehouse"] + [d["fieldname"] for d in dimensions]
	index_name = "unique_bin_dimension"

	# Validate column names — only lowercase alphanumeric + underscore allowed
	for col in columns:
		assert_safe_fieldname(col)

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


def _get_insert_after(dim, doctype=None):
	"""Determine where to insert the custom field. Default: after 'item' or at the end."""
	if doctype == "Purchase Order":
		return "expected_delivery_date"
	return "item"
