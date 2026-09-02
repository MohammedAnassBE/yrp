# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class YRPReceivedType(Document):
	def validate(self):
		self.enforce_single_default()

	def enforce_single_default(self):
		"""Only one Received Type can be the default. Unset others when this one is marked default."""
		if not self.is_default:
			return
		others = frappe.get_all(
			'YRP Received Type',
			filters={"is_default": 1, "name": ("!=", self.name)},
			pluck="name",
		)
		for other in others:
			frappe.db.set_value('YRP Received Type', other, "is_default", 0)

	def on_trash(self):
		settings = frappe.get_single('YRP YRP Stock Settings')
		if settings.get("default_received_type") == self.name:
			frappe.throw(
				f"Cannot delete '{self.name}' — it is set as the default in YRP Stock Settings."
			)
		if settings.get("default_rejected_received_type") == self.name:
			frappe.throw(
				f"Cannot delete '{self.name}' — it is set as the default rejected type in YRP Stock Settings."
			)


ReceivedType = YRPReceivedType
