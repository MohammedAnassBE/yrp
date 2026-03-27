# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class YRPSettings(Document):
	def validate(self):
		self.validate_production_order_attributes()

	def validate_production_order_attributes(self):
		grid_count = 0
		seen = set()
		for row in self.production_order_attributes or []:
			if row.attribute in seen:
				frappe.throw(_("Duplicate attribute {0} in Production Order Attributes").format(row.attribute))
			seen.add(row.attribute)
			if row.is_grid_attribute:
				grid_count += 1
		if grid_count > 1:
			frappe.throw(_("Only one attribute can be marked as Grid Attribute"))
