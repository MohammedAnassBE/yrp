# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Process(Document):
	def validate(self):
		self.validate_value_change_attributes()

	def validate_value_change_attributes(self):
		if self.is_group and self.value_change_attributes:
			frappe.throw(
				_("A group Process cannot have Value Change Attributes; remove them or untick Is Group.")
			)

		seen = set()
		for row in self.value_change_attributes:
			if row.attribute in seen:
				frappe.throw(
					_("Attribute {0} is listed more than once in Value Change Attributes.").format(
						frappe.bold(row.attribute)
					)
				)
			seen.add(row.attribute)
