"""Stock Update — simple add/reduce adjustment."""

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

		if self.get("item_details") and self._action != "submit":
			rows = ungroup_items_from_ui(self.item_details, "Stock Update")
			self.set("stock_update_details", [])
			for r in rows:
				self.append("stock_update_details", r)
			self.set_rate_from_last_sle()

	def set_rate_from_last_sle(self):
		from frappe.utils import flt

		for row in self.stock_update_details:
			last_rate = frappe.db.get_value(
				"Stock Ledger Entry",
				{"item": row.item_variant, "is_cancelled": 0},
				"valuation_rate",
				order_by="posting_datetime desc, creation desc",
			) or 0.0
			row.rate = flt(last_rate)

	def validate(self):
		if not self.stock_update_details:
			frappe.throw(_("At least one item is required"))
		from yrp.stock.utils import get_stock_balance
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		for row in self.stock_update_details:
			if not row.update_diff_qty or row.update_diff_qty <= 0:
				frappe.throw(_("Row {0}: qty must be > 0").format(row.idx))
			row.conversion_factor = row.conversion_factor or 1.0
			row.stock_qty = row.update_diff_qty * row.conversion_factor
			if self.update_type == "Reduce":
				dim_filters = {fn: row.get(fn) for fn in dim_fields}
				avail = get_stock_balance(row.item_variant, self.warehouse, **dim_filters)
				row.available_stock = avail
				if row.stock_qty > avail:
					frappe.throw(_("Row {0}: cannot reduce {1}, only {2} available").format(row.idx, row.stock_qty, avail))

	def on_submit(self):
		from yrp.stock.stock_ledger import make_sl_entries
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		sign = 1 if self.update_type == "Add" else -1
		entries = []
		for row in self.stock_update_details:
			base = {
				"item": row.item_variant,
				"warehouse": self.warehouse,
				"uom": row.uom,
				"voucher_type": "Stock Update",
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": sign * row.stock_qty,
				"rate": row.rate or 0 if self.update_type == "Add" else 0,
				"outgoing_rate": row.rate or 0 if self.update_type == "Reduce" else 0,
			}
			for fn in dim_fields:
				base[fn] = row.get(fn)
			entries.append(base)
		make_sl_entries(entries)

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		from yrp.stock.stock_ledger import make_sl_entries

		entries = self.on_submit_entries_for_cancel()
		make_sl_entries(entries, cancel=True)

	def on_submit_entries_for_cancel(self):
		"""Reuse the submit-time entry layout but flip qty signs."""
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		sign = -1 if self.update_type == "Add" else 1
		entries = []
		for row in self.stock_update_details:
			base = {
				"item": row.item_variant,
				"warehouse": self.warehouse,
				"uom": row.uom,
				"voucher_type": "Stock Update",
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": sign * row.stock_qty,
				"rate": row.rate or 0,
				"is_cancelled": 1,
			}
			for fn in dim_fields:
				base[fn] = row.get(fn)
			entries.append(base)
		return entries
