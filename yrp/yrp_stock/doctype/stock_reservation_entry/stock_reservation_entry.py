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
		# closed_qty + delivered_qty must never exceed reserved_qty.
		if flt(self.delivered_qty) + flt(self.closed_qty) > flt(self.reserved_qty) + 1e-9:
			frappe.throw(_(
				"delivered_qty ({0}) + closed_qty ({1}) cannot exceed reserved_qty ({2})."
			).format(self.delivered_qty, self.closed_qty, self.reserved_qty))
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
			delivered = flt(self.delivered_qty)
			closed = flt(self.closed_qty)
			reserved = flt(self.reserved_qty)
			# Closed-short takes precedence: once the user manually closes the
			# leftover reservation, status reflects that even if only part was
			# delivered. delivered + closed == reserved by construction of
			# close_at_delivered, but we accept >= for forward-safety.
			if closed > 0 and (delivered + closed) >= reserved:
				self.status = "Closed"
			elif delivered >= reserved:
				self.status = "Delivered"
			elif delivered > 0:
				self.status = "Partially Delivered"
			elif reserved >= (self.voucher_qty or reserved):
				self.status = "Reserved"
			else:
				self.status = "Partially Reserved"

	@frappe.whitelist()
	def close_at_delivered(self):
		"""Manually close this SRE at whatever has been delivered so far. Sets
		closed_qty = reserved_qty - delivered_qty and flips status to 'Closed'.
		The leftover reservation stops counting in get_sre_reserved_qty.

		Use when: reserved 100, only 50 ever got dispatched, plan changed and
		the other 50 should be released. Preserves reserved_qty and
		delivered_qty for audit (no in-place mutation).
		"""
		if self.docstatus != 1:
			frappe.throw(_("Only a submitted Stock Reservation Entry can be closed."))
		if self.status in ("Delivered", "Closed", "Cancelled"):
			frappe.throw(_("Stock Reservation Entry is already {0}.").format(self.status))
		remaining = flt(self.reserved_qty) - flt(self.delivered_qty) - flt(self.closed_qty)
		if remaining <= 0:
			frappe.throw(_("Nothing left to close — reserved_qty already fully delivered or closed."))
		new_closed = flt(self.closed_qty) + remaining
		self.closed_qty = new_closed
		self.set_status()
		self.db_set(
			{"closed_qty": new_closed, "status": self.status},
			update_modified=False,
		)
		return {"closed_qty": new_closed, "status": self.status}
