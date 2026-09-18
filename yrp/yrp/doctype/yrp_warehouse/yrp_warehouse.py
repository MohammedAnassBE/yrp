# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def _call_super(instance, method_name, *args, **kwargs):
	method = getattr(super(YRPWarehouseMixin, instance), method_name, None)
	if method:
		return method(*args, **kwargs)
	return None


class YRPWarehouseMixin:
	"""YRP operation permissions added to ERPNext's standard Warehouse."""

	def validate(self):
		_call_super(self, "validate")
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before or before.is_group == self.is_group:
			return
		if _has_yrp_stock(self.name):
			frappe.throw(
				_("Cannot change Is Group for Warehouse {0} because YRP stock exists.").format(self.name)
			)

	def on_trash(self):
		if _has_yrp_stock(self.name):
			frappe.throw(_("Cannot delete Warehouse {0} because YRP stock exists.").format(self.name))
		return _call_super(self, "on_trash")

	def validate_user_permission(self, user=None):
		"""Check if a user is permitted to operate on this warehouse.
		Returns True if no users configured (open to all) or user is in the list.
		"""
		if not self.warehouse_users:
			return True
		if user is None:
			user = frappe.session.user
		permitted_users = [d.user for d in self.warehouse_users]
		return user in permitted_users

	def check_user_permission(self, user=None):
		"""Throw if user is not permitted to operate on this warehouse."""
		if not self.validate_user_permission(user):
			frappe.throw(
				_("User {0} is not permitted to operate on Warehouse {1}").format(
					user or frappe.session.user, self.name
				)
			)


def _has_yrp_stock(warehouse):
	return bool(
		frappe.db.exists("YRP Bin", {"warehouse": warehouse})
		or frappe.db.exists("YRP Stock Ledger Entry", {"warehouse": warehouse})
		or frappe.db.exists("YRP Stock Reservation Entry", {"warehouse": warehouse})
	)


YRPWarehouse = YRPWarehouseMixin
