# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WorkStation(Document):
	def validate(self):
		if self.default:
			self.validate_default_uniqueness()

	def validate_default_uniqueness(self):
		"""Only one Work Station can be marked as default."""
		existing = frappe.db.exists("Work Station", {
			"default": 1,
			"name": ["!=", self.name],
		})
		if existing:
			frappe.throw(f"Work Station '{existing}' is already set as default. Only one default is allowed.")
