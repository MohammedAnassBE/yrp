# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""
One-time migration that wires Received Type into the YRP stock engine as a
runtime stock dimension.

Steps (idempotent):
  1. Ensure a Received Type named "Accepted" exists with is_default=1.
  2. Set YRP Stock Settings.default_received_type = "Accepted" if blank.
  3. Add a YRP Stock Dimension row (received_type, mandatory, in_valuation=0)
     to YRP Stock Settings if not already present.
  4. Run create_dimension_fields() to add the Custom Field on every
     STOCK_DOCTYPES entry and rebuild the Bin unique index.
  5. Backfill received_type='Accepted' on every existing row of every
     stock-bearing table where the column is NULL/empty.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

DEFAULT_RT_NAME = "Accepted"
DIMENSION_FIELDNAME = "received_type"
DIMENSION_LABEL = "Received Type"

# Tables that hold the received_type column once create_dimension_fields() runs.
# Keep in sync with yrp.stock.dimensions.STOCK_DOCTYPES.
BACKFILL_DOCTYPES = [
	"Stock Ledger Entry",
	"Bin",
	"Stock Entry Detail",
	"Stock Update Detail",
	"Stock Reconciliation Item",
	"Stock Reservation Entry",
	"Repost Item Valuation",
]


def execute():
	if not frappe.db.exists("DocType", "YRP Stock Settings"):
		return
	if not frappe.db.exists("DocType", "Received Type"):
		# Received Type DocType not yet migrated — nothing to do this run.
		return

	_ensure_default_received_type()
	_ensure_settings_default()
	_ensure_dimension_row()
	_ensure_settings_field_on_yrp_stock_settings()

	# Create the Custom Fields and rebuild the Bin unique index.
	from yrp.stock.dimensions import create_dimension_fields, clear_dimension_cache

	clear_dimension_cache()
	create_dimension_fields()

	_backfill_received_type()
	frappe.db.commit()


def _ensure_default_received_type():
	if frappe.db.exists("Received Type", DEFAULT_RT_NAME):
		# Make sure it's flagged as default.
		if not frappe.db.get_value("Received Type", DEFAULT_RT_NAME, "is_default"):
			frappe.db.set_value("Received Type", DEFAULT_RT_NAME, "is_default", 1)
		return

	doc = frappe.get_doc(
		{
			"doctype": "Received Type",
			"received_type_name": DEFAULT_RT_NAME,
			"is_default": 1,
		}
	)
	doc.insert(ignore_permissions=True)


def _ensure_settings_default():
	current = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
	if not current:
		frappe.db.set_single_value(
			"YRP Stock Settings", "default_received_type", DEFAULT_RT_NAME
		)


def _ensure_settings_field_on_yrp_stock_settings():
	"""Safety net for sites that migrated the patch before the JSON change reached them.

	Adds default_received_type as a Custom Field if the standard field is missing.
	The standard JSON field will take precedence on the next migrate.
	"""
	meta = frappe.get_meta("YRP Stock Settings", cached=False)
	if meta.get_field("default_received_type"):
		return
	create_custom_fields(
		{
			"YRP Stock Settings": [
				{
					"fieldname": "default_received_type",
					"fieldtype": "Link",
					"label": "Default Received Type",
					"options": "Received Type",
					"insert_after": "transit_warehouse",
				}
			]
		},
		update=True,
	)


def _ensure_dimension_row():
	settings = frappe.get_single("YRP Stock Settings")
	for row in settings.get("stock_dimensions") or []:
		if row.fieldname == DIMENSION_FIELDNAME:
			return
	settings.append(
		"stock_dimensions",
		{
			"dimension_doctype": "Received Type",
			"fieldname": DIMENSION_FIELDNAME,
			"label": DIMENSION_LABEL,
			"mandatory": 1,
			"in_valuation": 0,
			"is_production_group": 0,
		},
	)
	settings.flags.ignore_permissions = True
	settings.save()


def _backfill_received_type():
	"""Set received_type='Accepted' on every NULL/empty row in stock tables."""
	for doctype in BACKFILL_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		table = f"tab{doctype}"
		# Confirm the column exists before touching it (defensive — create_dimension_fields
		# above should have created it, but skip if site has the DocType but no column).
		columns = frappe.db.sql(f"SHOW COLUMNS FROM `{table}` LIKE 'received_type'")
		if not columns:
			continue
		frappe.db.sql(
			f"""
			UPDATE `{table}`
			SET received_type = %s
			WHERE received_type IS NULL OR received_type = ''
			""",
			DEFAULT_RT_NAME,
		)
