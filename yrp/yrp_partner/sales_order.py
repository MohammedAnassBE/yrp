"""Guard native Sales Order processing paths that do not call document.save()."""
import frappe
from frappe import _

from yrp.yrp_partner.permissions import is_partner


class PartnerSalesOrderMixin:
	def _check_partner_processing(self):
		if is_partner():
			frappe.throw(_("Partner users cannot process Sales Orders."), frappe.PermissionError)

	def update_status(self, status):
		# Native bulk close/reopen checks only DocType write permission before
		# writing status directly. Scoped partner draft access is not authority
		# to process any submitted Sales Order, including the partner's own.
		self._check_partner_processing()
		return super().update_status(status)

	@frappe.whitelist()
	def create_stock_reservation_entries(self, items_details=None, from_voucher_type=None, notify=True):
		self._check_partner_processing()
		return super().create_stock_reservation_entries(
			items_details=items_details, from_voucher_type=from_voucher_type, notify=notify)

	@frappe.whitelist()
	def cancel_stock_reservation_entries(self, sre_list=None, notify=True):
		self._check_partner_processing()
		return super().cancel_stock_reservation_entries(sre_list=sre_list, notify=notify)

	@frappe.whitelist()
	def create_delivery_schedule(self, child_row, schedules):
		self._check_partner_processing()
		return super().create_delivery_schedule(child_row, schedules)
