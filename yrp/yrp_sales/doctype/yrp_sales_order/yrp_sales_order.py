# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from yrp.yrp_retail import sales_sources
from yrp.yrp_retail.logic import finite_number, require
from yrp.yrp_retail.pricing import check_rate, get_free_items


class YRPSalesOrder(Document):
	"""Customer order; delivery quantities are owned by YRP Delivery Notes."""

	def validate(self):
		require(self.items, "A Sales Order requires items.")
		if self.delivery_date:
			require(getdate(self.delivery_date) >= getdate(self.transaction_date), "Delivery Date cannot be before the order date.")
		for row in self.items:
			self.set_row_values(row)
		self.validate_rates()
		self.total_qty = sum(flt(row.qty) for row in self.items)
		self.total = sum(flt(row.amount) for row in self.items)
		sales_sources.validate_sales_order(self)
		self.status = self.get_status()

	def set_row_values(self, row):
		item = frappe.get_cached_doc("Item", row.item_code)
		require(not item.disabled and not item.has_variants and item.is_sales_item, "Order items must be enabled, concrete sales Items.")
		row.item_name = item.item_name
		row.stock_uom = item.stock_uom
		row.conversion_factor = get_conversion_factor(item, row.uom)
		qty = finite_number(row.qty, "Quantity")
		require(qty > 0, "Quantity must be greater than zero.")
		row.stock_qty = qty * row.conversion_factor
		if not row.rate and self.selling_price_list:
			row.rate = get_price_list_rate(self.selling_price_list, row.item_code, row.uom)
		row.amount = qty * flt(row.rate)
		row.warehouse = row.warehouse or self.set_warehouse
		row.cancelled_qty = 0  # Saves run on drafts only; cancel_qty alone cancels, after submit.

	def validate_rates(self):
		free = get_free_items(row.item_code for row in self.items)
		for row in self.items:
			check_rate(row.rate, row.item_code in free, _("Row {0}: {1}").format(row.idx, row.item_code))

	def get_status(self):
		if self.docstatus == 0:
			return "Draft"
		if self.docstatus == 2:
			return "Cancelled"
		if flt(self.per_delivered) >= 100:
			return "Closed" if any(flt(row.get("cancelled_qty")) for row in self.items) else "Delivered"
		if flt(self.per_delivered) > 0:
			return "Partially Delivered"
		return "To Deliver"

	def on_update(self):
		sales_sources.refresh_progress(self, "on_update")

	def on_submit(self):
		self.db_set("status", self.get_status(), update_modified=False)
		sales_sources.refresh_progress(self, "on_submit")

	def before_cancel(self):
		notes = frappe.db.sql_list(
			"""select distinct dni.parent from `tabYRP Delivery Note Item` dni
			join `tabYRP Delivery Note` dn on dn.name = dni.parent
			where dni.sales_order = %s and dn.docstatus < 2 order by dni.parent for update""",
			self.name,
		)
		require(not notes, _("Cancel or delete Delivery Notes {0} first.").format(", ".join(notes)))

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
		sales_sources.refresh_progress(self, "on_cancel")

	def on_discard(self):
		self.db_set("status", "Cancelled", update_modified=False)
		sales_sources.refresh_progress(self, "on_discard")

	def on_trash(self):
		sales_sources.refresh_progress(self, "on_trash")

	def update_delivered_qty(self, update_modified=False):
		"""Recompute delivery caches from the delivered quantity of submitted YRP Delivery Note rows.

		Cancelled quantity is no longer due: an order whose rest is cancelled is fully delivered."""
		delivered = dict(frappe.db.sql(
			"""select dni.so_detail, sum(dni.delivered_qty) from `tabYRP Delivery Note Item` dni
			join `tabYRP Delivery Note` dn on dn.name = dni.parent
			where dn.docstatus = 1 and dni.sales_order = %s group by dni.so_detail for update""",
			self.name,
		))
		ordered, done = 0.0, 0.0
		for row in self.items:
			row.delivered_qty = flt(delivered.get(row.name))
			row.db_set("delivered_qty", row.delivered_qty, update_modified=False)
			due = flt(row.qty) - flt(row.cancelled_qty)
			ordered += due
			done += min(row.delivered_qty, due)
		self.per_delivered = done / ordered * 100 if ordered > 0 else (100 if self.items else 0)
		self.db_set({"per_delivered": self.per_delivered, "status": self.get_status()}, update_modified=update_modified)

	def cancel_qty(self, quantities):
		"""Cancel open quantity per row, `{so_detail: qty}` in the row UOM; callers check permissions.

		Only quantity on no non-cancelled Delivery Note can be cancelled. Rows are read under lock."""
		self.flags.for_update = True
		self.load_from_db()
		self.flags.for_update = False
		require(self.docstatus == 1, "Only submitted Sales Orders can cancel quantities.")
		rows = {row.name: row for row in self.items}
		allocated = get_allocated_qty(list(quantities))
		for name, qty in quantities.items():
			require(name in rows, _("Sales Order Item {0} is not on Sales Order {1}.").format(name, self.name))
			row = rows[name]
			qty = finite_number(qty, "Cancelled quantity")
			require(qty > 0, _("Row {0} of Sales Order {1}: Quantity must be greater than zero.").format(row.idx, self.name))
			pending = get_open_stock_qty(row) - flt(allocated.get(name))
			precision = row.precision("stock_qty")
			require(flt(qty * flt(row.conversion_factor), precision) <= flt(pending, precision),
				_("Row {0} of Sales Order {1}: {2} exceeds pending {3}.").format(
					row.idx, self.name, qty, flt(pending / flt(row.conversion_factor), precision)))
			row.cancelled_qty = flt(row.cancelled_qty) + qty
			row.db_set("cancelled_qty", row.cancelled_qty, update_modified=False)
		self.update_delivered_qty(update_modified=True)


def get_open_stock_qty(row):
	"""Stock quantity of an order row that is not cancelled."""
	return flt(row.stock_qty) - flt(row.get("cancelled_qty")) * flt(row.conversion_factor)


def get_allocated_qty(so_details, exclude=None):
	"""Stock quantity on non-cancelled Delivery Notes per Sales Order row, read under lock."""
	if not so_details:
		return {}
	return dict(frappe.db.sql(
		"""select dni.so_detail, sum(dni.stock_qty) from `tabYRP Delivery Note Item` dni
		join `tabYRP Delivery Note` dn on dn.name = dni.parent
		where dni.so_detail in %(details)s and dn.docstatus < 2 and dn.name != %(exclude)s
		group by dni.so_detail for update""",
		{"details": list(so_details), "exclude": exclude or ""},
	))


def get_conversion_factor(item, uom):
	if uom == item.stock_uom:
		return 1.0
	matches = [row for row in item.uoms if row.uom == uom]
	require(len(matches) == 1, _("UOM {0} needs one conversion on Item {1}.").format(uom, item.name))
	factor = finite_number(matches[0].conversion_factor, "Conversion factor")
	require(factor > 0, "Conversion factor must be positive.")
	return factor


def get_price_list_rate(price_list, item_code, uom):
	return flt(frappe.db.get_value(
		"Item Price",
		{"price_list": price_list, "item_code": item_code, "uom": uom, "selling": 1},
		"price_list_rate",
	))
