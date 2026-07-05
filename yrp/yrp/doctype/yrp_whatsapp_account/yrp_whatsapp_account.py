# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class YRPWhatsAppAccount(Document):
	def validate(self):
		if self.is_default:
			frappe.db.sql(
				"""
				UPDATE `tabYRP WhatsApp Account`
				SET is_default = 0
				WHERE name != %s AND is_default = 1
				""",
				self.name,
			)
