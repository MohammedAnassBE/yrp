"""Stock Reconciliation — physical count / opening stock.

Creates SLEs where qty is the movement needed to reach the counted balance
and qty_after_transaction is the final reconciled balance.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class StockReconciliation(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		grouped = group_items_for_ui(self.get("items") or [], "Stock Reconciliation")
		self.set_onload("item_details", grouped)

	def before_validate(self):
		from yrp.stock.save_stock_items import ungroup_items_from_ui
		from yrp.stock.dimensions import apply_dimension_defaults

		if self.get("item_details") and self._action != "submit":
			rows = ungroup_items_from_ui(self.item_details, "Stock Reconciliation")
			self.set("items", [])
			for r in rows:
				# Reconciliation rows need a warehouse — auto-fill from header
				r.setdefault("warehouse", self.default_warehouse)
				self.append("items", r)
		apply_dimension_defaults(self.get("items") or [])

	def validate(self):
		if not self.items:
			frappe.throw(_("At least one item is required"))
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		self.set_rate_from_last_sle()

		for row in self.items:
			if not row.warehouse:
				row.warehouse = self.default_warehouse
			if not row.warehouse:
				frappe.throw(_("Row {0}: Warehouse is required").format(row.idx))
			row.conversion_factor = row.conversion_factor or 1.0
			row.stock_qty = (row.qty or 0) * row.conversion_factor
			row.stock_uom_rate = (row.rate or 0) / row.conversion_factor if row.conversion_factor else 0
			row.amount = (row.qty or 0) * (row.rate or 0)

		if self.purpose == "Opening Stock":
			self.validate_no_prior_sle(dim_fields)

	def set_rate_from_last_sle(self):
		"""Auto-fill rate from last uncancelled SLE if the user didn't enter one.
		Scoped to (warehouse, valuation_dims) bucket (Gap #17). If no SLE
		exists in the bucket, fall back to last SLE of the item across
		any warehouse. If still nothing and allow_zero_valuation_rate is
		not checked, throw."""
		from yrp.stock.utils import get_last_sle_rate
		from yrp.stock.dimensions import get_dimension_fieldnames

		dim_fields = get_dimension_fieldnames()
		for row in self.items:
			if flt(row.rate) > 0:
				continue
			warehouse = row.warehouse or self.default_warehouse
			dim_filters = {fn: row.get(fn) for fn in dim_fields}
			last_rate, _matched = get_last_sle_rate(
				row.item, warehouse=warehouse, **dim_filters
			)
			if flt(last_rate) > 0:
				row.rate = flt(last_rate)
			elif not row.allow_zero_valuation_rate:
				frappe.throw(
					_("Row {0}: Rate is mandatory for {1}. "
					  "Check 'Allow Zero Valuation Rate' if you want to proceed with zero rate."
					).format(row.idx, row.item)
				)

	def validate_no_prior_sle(self, dim_fields):
		for row in self.items:
			filters = {"item": row.item, "warehouse": row.warehouse, "is_cancelled": 0}
			for fn in dim_fields:
				filters[fn] = row.get(fn)
			if frappe.db.exists("Stock Ledger Entry", filters):
				frappe.throw(_("Row {0}: prior Stock Ledger Entry exists; cannot post Opening Stock").format(row.idx))

	def _build_sl_entries(self, cancel=False):
		"""Build SLE dicts for submit or cancel — single source of truth.

		On submit: qty = target - previous balance, qty_after_transaction = target.
		On cancel: qty_after_transaction = 0; the active original SLE is marked
		cancelled before the Bin is refreshed.
		"""
		from yrp.stock.dimensions import get_dimension_fieldnames
		from yrp.stock.utils import get_stock_balance

		dim_fields = get_dimension_fieldnames()
		entries = []
		for row in self.items:
			dim_values = {fn: row.get(fn) for fn in dim_fields}
			if cancel:
				movement_qty = 0
				qty_after = 0
			else:
				qty_after = 0 if row.make_qty_zero else row.stock_qty
				previous_qty = get_stock_balance(
					row.item,
					row.warehouse,
					posting_date=self.posting_date,
					posting_time=self.posting_time,
					**dim_values,
				)
				movement_qty = flt(qty_after) - flt(previous_qty)

			base = {
				"item": row.item,
				"warehouse": row.warehouse,
				"uom": row.uom,
				"voucher_type": "Stock Reconciliation",
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": movement_qty,
				"qty_after_transaction": qty_after,
				"reconciled_qty": qty_after,
				"rate": row.rate or 0,
				"is_reconciliation": 1,
			}
			base.update(dim_values)
			entries.append(base)
		return entries

	def on_submit(self):
		from yrp.stock.stock_ledger import make_sl_entries

		make_sl_entries(self._build_sl_entries())

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		from yrp.stock.stock_ledger import make_sl_entries

		make_sl_entries(self._build_sl_entries(cancel=True), cancel=True)
