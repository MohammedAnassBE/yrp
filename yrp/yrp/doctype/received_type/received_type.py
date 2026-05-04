# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ReceivedType(Document):
	def validate(self):
		self.enforce_single_default()

	def enforce_single_default(self):
		"""Only one Received Type can be the default. Unset others when this one is marked default."""
		if not self.is_default:
			return
		others = frappe.get_all(
			"Received Type",
			filters={"is_default": 1, "name": ("!=", self.name)},
			pluck="name",
		)
		for other in others:
			frappe.db.set_value("Received Type", other, "is_default", 0)

	def on_trash(self):
		settings_default = frappe.db.get_single_value(
			"YRP Stock Settings", "default_received_type"
		)
		if settings_default == self.name:
			frappe.throw(
				f"Cannot delete '{self.name}' — it is set as the default in YRP Stock Settings."
			)
