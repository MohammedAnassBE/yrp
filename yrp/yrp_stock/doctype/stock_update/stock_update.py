"""Stock Update — simple add/reduce stock adjustment.

Add: increases stock qty at the given warehouse (incoming stock)
Reduce: decreases stock qty at the given warehouse (outgoing stock)
"""

import frappe
from frappe import _
from frappe.model.document import Document


class StockUpdate(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		grouped = group_items_for_ui(self.get("stock_update_details") or [], "Stock Update")
		self.set_onload("item_details", grouped)

	def before_validate(self):
		from yrp.stock.save_stock_items import ungroup_items_from_ui
		from yrp.stock.dimensions import apply_dimension_defaults

		if self.get("item_details") and self._action != "submit":
			rows = ungroup_items_from_ui(self.item_details, "Stock Update")
			self.set("stock_update_details", [])
			for r in rows:
				self.append("stock_update_details", r)
			self.set_rate_from_last_sle()
		apply_dimension_defaults(self.get("stock_update_details") or [])

	def set_rate_from_last_sle(self):
		"""Auto-fill rate from last uncancelled SLE for each item, scoped
		to (warehouse, valuation_dims) bucket (Gap #17)."""
		from frappe.utils import flt
		from yrp.stock.utils import get_last_sle_rate
		from yrp.stock.dimensions import get_dimension_fieldnames

		dim_fields = get_dimension_fieldnames()
		for row in self.stock_update_details:
			dim_filters = {fn: row.get(fn) for fn in dim_fields}
			rate, _matched = get_last_sle_rate(
				row.item_variant, warehouse=self.warehouse, **dim_filters
			)
			row.rate = flt(rate)

	def validate(self):
		if not self.stock_update_details:
			frappe.throw(_("At least one item is required"))
		from yrp.stock.utils import get_stock_balance
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		for row in self.stock_update_details:
			if not row.update_diff_qty or row.update_diff_qty <= 0:
				frappe.throw(_("Row {0}: qty must be > 0").format(row.idx))

			# Auto-fill UOM from Item Variant if not set
			if not row.uom:
				parent = frappe.db.get_value("Item Variant", row.item_variant, "item")
				row.uom = frappe.db.get_value("Item", parent, "default_unit_of_measure") if parent else None
			if not row.uom:
				frappe.throw(_("Row {0}: UOM is required").format(row.idx))

			row.conversion_factor = row.conversion_factor or 1.0
			row.stock_qty = row.update_diff_qty * row.conversion_factor

			# For Reduce: enforce reservation-aware available stock (H.2).
			# Reservation is honored regardless of Item.allow_negative_stock;
			# the negative-stock flag only bypasses the actual_qty>=0 check.
			if self.update_type == "Reduce":
				from yrp.stock.utils import get_available_stock

				dim_filters = {fn: row.get(fn) for fn in dim_fields}
				actual = get_stock_balance(row.item_variant, self.warehouse, **dim_filters)
				available = get_available_stock(
					row.item_variant,
					self.warehouse,
					exclude_voucher_type="Stock Update",
					exclude_voucher_name=self.name,
					**dim_filters,
				)
				row.available_stock = actual

				parent_item = frappe.get_cached_value(
					"Item Variant", row.item_variant, "item"
				)
				item_allows_neg = bool(
					parent_item
					and frappe.get_cached_value(
						"Item", parent_item, "allow_negative_stock"
					)
				)
				reserved = actual - available
				if not item_allows_neg and row.stock_qty > actual:
					frappe.throw(
						_("Row {0}: cannot reduce {1}, only {2} available").format(
							row.idx, row.stock_qty, actual
						)
					)
				# H.2: reservation is honored regardless of allow_negative_stock.
				# Only fire when there's an actual reservation to bypass; with
				# reserved=0, the negative-stock flag alone governs.
				if reserved > 0 and row.stock_qty > available:
					frappe.throw(
						_(
							"Row {0}: requested {1} exceeds available-after-reservation {2}. "
							"Negative-stock cannot bypass an active reservation."
						).format(row.idx, row.stock_qty, available)
					)

	# ------------------------------------------------------------------
	# Submit and Cancel — both use _build_sl_entries to avoid duplication
	# ------------------------------------------------------------------
	def on_submit(self):
		from yrp.stock.stock_ledger import make_sl_entries
		make_sl_entries(self._build_sl_entries())

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		from yrp.stock.stock_ledger import make_sl_entries
		make_sl_entries(self._build_sl_entries(cancel=True), cancel=True)

	def _build_sl_entries(self, cancel=False):
		"""Build SLE dicts for submit or cancel — single source of truth.

		Submit (Add):    qty = +stock_qty, rate = rate,  outgoing_rate = 0
		Submit (Reduce): qty = -stock_qty, rate = 0,     outgoing_rate = rate

		Cancel reverses the direction:
		Cancel (Add):    qty = -stock_qty, rate = 0,     outgoing_rate = rate  (was incoming, now outgoing)
		Cancel (Reduce): qty = +stock_qty, rate = rate,  outgoing_rate = 0     (was outgoing, now incoming)
		"""
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		is_add = self.update_type == "Add"

		# On submit: Add is positive, Reduce is negative
		# On cancel: flip the sign
		if cancel:
			is_incoming = not is_add  # cancel of Add = outgoing, cancel of Reduce = incoming
			sign = -1 if is_add else 1
		else:
			is_incoming = is_add
			sign = 1 if is_add else -1

		entries = []
		for row in self.stock_update_details:
			rate = row.rate or 0
			entry = {
				"item": row.item_variant,
				"warehouse": self.warehouse,
				"uom": row.uom,
				"voucher_type": "Stock Update",
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": sign * row.stock_qty,
				# Incoming stock needs "rate" for valuation (add to FIFO queue)
				# Outgoing stock needs "outgoing_rate" for FIFO consumption
				"rate": rate if is_incoming else 0,
				"outgoing_rate": rate if not is_incoming else 0,
			}
			if cancel:
				entry["is_cancelled"] = 1

			# Add dimension values
			for fn in dim_fields:
				entry[fn] = row.get(fn)

			entries.append(entry)

		return entries
