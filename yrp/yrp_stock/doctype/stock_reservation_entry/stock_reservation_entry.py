"""Stock Reservation Entry — reserves stock against a voucher.

Updates Bin.reserved_qty on submit/cancel. Status computed from delivered vs
reserved quantities.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class StockReservationEntry(Document):
	def validate(self):
		if not self.reserved_qty or self.reserved_qty <= 0:
			frappe.throw(_("Reserved Qty must be > 0"))
		if self.reserved_qty > (self.available_qty or 0):
			frappe.throw(_("Reserved qty exceeds available qty {0}").format(self.available_qty))
		self.set_status()

	def on_submit(self):
		self.update_bin_reserved_qty()

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self.update_bin_reserved_qty()

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
		elif self.docstatus == 2:
			self.status = "Cancelled"
		else:
			delivered = self.delivered_qty or 0
			reserved = self.reserved_qty or 0
			if delivered >= reserved:
				self.status = "Delivered"
			elif delivered > 0:
				self.status = "Partially Delivered"
			elif reserved >= (self.voucher_qty or reserved):
				self.status = "Reserved"
			else:
				self.status = "Partially Reserved"

	def update_bin_reserved_qty(self):
		from yrp.stock.utils import get_or_make_bin
		from yrp.stock.dimensions import get_stock_dimensions

		dim_filters = {d["fieldname"]: self.get(d["fieldname"]) for d in get_stock_dimensions()}
		bin_name = get_or_make_bin(self.item_code, self.warehouse, **dim_filters)
		bin_doc = frappe.get_doc("Bin", bin_name)
		bin_doc.update_reserved_stock()
