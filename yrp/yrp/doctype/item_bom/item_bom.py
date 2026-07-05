import frappe
from frappe.model.document import Document


class ItemBOM(Document):
	def validate(self):
		if self.based_on_attribute_mapping and not self.attribute_mapping:
			frappe.throw("Attribute Mapping is required when Based on Attribute Mapping is checked.")
		if self.qty_of_product <= 0 or self.qty_of_bom_item <= 0:
			frappe.throw("Qty of Product and Qty of BOM Item must be positive.")
