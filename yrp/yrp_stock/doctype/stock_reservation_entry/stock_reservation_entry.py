"""Stock Reservation Entry — reserves stock against a voucher.

D-002 / D-008: reservations are query-based. Bin no longer tracks
reserved_qty; consumers call yrp.stock.utils.get_sre_reserved_qty(...)
or get_available_stock(...) to compute reservations live.

Status is computed from delivered vs reserved quantities.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class StockReservationEntry(Document):
	def validate(self):
		if not self.reserved_qty or self.reserved_qty <= 0:
			frappe.throw(_("Reserved Qty must be > 0"))
		self.set_status()

	def before_submit(self):
		"""Bug D (r-010 High #7): live over-reservation check with row lock.

		The form's `available_qty` field is computed at form-load time and
		is stale by submit time, especially under concurrent SRE creation.
		We instead lock the Bin row, read live actual_qty, sum live reserved
		from active SREs (excluding self), and throw if reserved_qty exceeds
		the difference.
		"""
		from yrp.stock.utils import get_or_make_bin, get_sre_reserved_qty
		from yrp.stock.dimensions import get_stock_dimensions

		dim_filters = {
			d["fieldname"]: self.get(d["fieldname"]) for d in get_stock_dimensions()
		}
		bin_name = get_or_make_bin(self.item_code, self.warehouse, **dim_filters)
		# Lock the Bin row for the duration of this transaction so concurrent
		# SRE submits serialize on the same bucket.
		frappe.db.sql(
			"SELECT name FROM `tabBin` WHERE name=%s FOR UPDATE", bin_name
		)
		actual = flt(frappe.db.get_value("Bin", bin_name, "actual_qty"))
		other_reserved = get_sre_reserved_qty(
			item_code=self.item_code,
			warehouse=self.warehouse,
			exclude_voucher_type="Stock Reservation Entry",
			exclude_voucher_name=self.name,
			**dim_filters,
		)
		# Subtract other SREs' active reservations (this SRE itself is excluded
		# because it's not yet docstatus=1 at this point, but exclude defensively).
		available = actual - other_reserved
		if self.reserved_qty > available:
			frappe.throw(
				_(
					"Reserved Qty {0} exceeds live available {1} "
					"(actual {2} − reserved by others {3})."
				).format(self.reserved_qty, available, actual, other_reserved)
			)

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

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
