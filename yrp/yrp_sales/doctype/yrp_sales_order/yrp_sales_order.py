# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from yrp.yrp_retail import sales_sources
from yrp.yrp_retail.logic import finite_number, require
from yrp.yrp_retail.pricing import check_rate


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

	def validate_rates(self):
		codes = sorted({row.item_code for row in self.items})
		free = set(frappe.get_all("Item", filters={"name": ["in", codes], "yrp_is_free_item": 1}, pluck="name"))
		for row in self.items:
			check_rate(row.rate, row.item_code in free, _("Row {0}: {1}").format(row.idx, row.item_code))

	def get_status(self):
		if self.docstatus == 0:
			return "Draft"
		if self.docstatus == 2:
			return "Cancelled"
		if flt(self.per_delivered) >= 100:
			return "Delivered"
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

	def on_trash(self):
		sales_sources.refresh_progress(self, "on_trash")

	def update_delivered_qty(self):
		"""Recompute delivery caches from the delivered quantity of submitted YRP Delivery Note rows."""
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
			ordered += flt(row.qty)
			done += min(row.delivered_qty, flt(row.qty))
		self.per_delivered = done / ordered * 100 if ordered else 0
		self.db_set({"per_delivered": self.per_delivered, "status": self.get_status()}, update_modified=False)


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
