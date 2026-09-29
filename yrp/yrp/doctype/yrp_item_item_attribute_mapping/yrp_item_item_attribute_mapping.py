# Copyright (c) 2021, Essdee and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class YRPItemItemAttributeMapping(Document):
	def _validate_links(self):
		from yrp.attribute_values import normalize_mapping

		normalize_mapping(self)
		super()._validate_links()


ItemItemAttributeMapping = YRPItemItemAttributeMapping
