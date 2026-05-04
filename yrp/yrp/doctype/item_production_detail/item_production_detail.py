import frappe
from frappe.model.document import Document


class ItemProductionDetail(Document):
	def validate(self):
		self.validate_unique_attributes()
		self.validate_attribute_references()
		self.validate_stage_continuity()

	def validate_unique_attributes(self):
		seen = set()
		for row in self.item_attributes:
			if row.attribute in seen:
				frappe.throw(f"Attribute {row.attribute} is listed more than once.")
			seen.add(row.attribute)

	def validate_attribute_references(self):
		listed = {row.attribute for row in self.item_attributes}
		if self.primary_attribute and self.primary_attribute not in listed:
			frappe.throw(f"Primary attribute {self.primary_attribute} must appear in Item Attributes table.")
		if self.dependent_attribute and self.dependent_attribute not in listed:
			frappe.throw(f"Dependent attribute {self.dependent_attribute} must appear in Item Attributes table.")
		if self.dependent_attribute and not self.dependent_attribute_mapping:
			frappe.throw("Dependent Attribute Mapping is required when Dependent Attribute is set.")

	def validate_stage_continuity(self):
		rows = list(self.ipd_processes)
		for i in range(len(rows) - 1):
			a, b = rows[i], rows[i + 1]
			if a.out_stage and b.in_stage and a.out_stage != b.in_stage:
				frappe.throw(
					f"Stage discontinuity: {a.process_name} out_stage ({a.out_stage}) "
					f"!= {b.process_name} in_stage ({b.in_stage})"
				)

	def on_submit(self):
		if self.approval_status == "Approved" and not self.approved_by:
			self.db_set("approved_by", frappe.session.user)
