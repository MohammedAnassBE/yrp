# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.contacts.address_and_contact import load_address_and_contact, delete_contact_and_address


class Warehouse(Document):
	def onload(self):
		"""Load address and contacts in `__onload`."""
		load_address_and_contact(self)

	def on_trash(self):
		delete_contact_and_address("Warehouse", self.name)

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
