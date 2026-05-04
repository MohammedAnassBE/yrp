"""Bin — balance snapshot per Item x Warehouse x dimensions.

Each Bin tracks:
  - actual_qty:     per ALL dimensions (Fresh has its own qty, Used has its own qty)
  - valuation_rate: per VALUATION dimensions only (Fresh and Used share the same rate within a Lot)
  - stock_value:    actual_qty * valuation_rate

Dimension fields are added dynamically by the YRP Stock dimension patch.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from yrp.stock.dimensions import get_stock_dimensions, get_valuation_dimensions


class Bin(Document):
	def before_save(self):
		# Auto-fill stock_uom from the parent Item if not set
		if not self.stock_uom and self.item_code:
			parent = frappe.db.get_value("Item Variant", self.item_code, "item")
			if parent:
				self.stock_uom = frappe.db.get_value("Item", parent, "default_unit_of_measure")

def update_qty(bin_name, args):
	"""Refresh Bin values from the Stock Ledger.

	Two separate queries are needed because qty and rate use different scopes:
	  - actual_qty:     from latest SLE matching ALL dimensions (this Bin's specific bucket)
	  - valuation_rate: from latest SLE matching VALUATION dimensions only (shared rate)
	  - stock_value:    computed as actual_qty * valuation_rate
	"""
	bin_doc = frappe.get_doc("Bin", bin_name)
	all_dims = get_stock_dimensions()
	val_dim_fields = get_valuation_dimensions()

	# --- Query 1: actual_qty from latest SLE matching ALL dimensions ---
	actual_qty = _get_latest_sle_value(
		bin_doc, all_dims, "qty_after_transaction"
	)

	# --- Query 2: valuation_rate from latest SLE matching VALUATION dims only ---
	valuation_rate = _get_latest_sle_value(
		bin_doc, [d for d in all_dims if d["fieldname"] in val_dim_fields], "valuation_rate"
	)

	# --- Compute stock_value ---
	stock_value = actual_qty * valuation_rate

	bin_doc.db_set("actual_qty", actual_qty, update_modified=False)
	bin_doc.db_set("valuation_rate", valuation_rate, update_modified=False)
	bin_doc.db_set("stock_value", stock_value, update_modified=False)


def _get_latest_sle_value(bin_doc, dims, field_name):
	"""Get a single field value from the latest non-cancelled SLE matching the given dimensions.

	Args:
		bin_doc: the Bin document (has item_code, warehouse, and dimension values)
		dims: list of dimension dicts to filter by (each has "fieldname")
		field_name: which SLE field to read (e.g., "qty_after_transaction" or "valuation_rate")

	Returns:
		float value of the requested field, or 0.0 if no matching SLE exists
	"""
	sle = frappe.qb.DocType("Stock Ledger Entry")

	query = (
		frappe.qb.from_(sle)
		.select(getattr(sle, field_name))
		.where(sle.item == bin_doc.item_code)
		.where(sle.warehouse == bin_doc.warehouse)
		.where(sle.is_cancelled == 0)
	)

	# Filter by the specified dimensions
	for dim in dims:
		fn = dim["fieldname"]
		val = bin_doc.get(fn)
		if val is not None:
			query = query.where(sle[fn] == val)

	# Get the most recent SLE
	query = query.orderby(sle.posting_datetime, order=frappe.qb.desc)
	query = query.orderby(sle.creation, order=frappe.qb.desc)
	query = query.limit(1)

	rows = query.run()
	return flt(rows[0][0]) if rows else 0.0
