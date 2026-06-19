import frappe
from frappe.model.document import Document

from yrp.yrp.utils import ipd_engine


class ItemProductionDetail(Document):
	def autoname(self):
		# Name as "<item>-<version>" (mirrors production_api), incrementing the
		# integer `version` per item, instead of Frappe's default random hash.
		if not self.item:
			return
		max_version = frappe.db.sql(
			"select max(version) from `tabItem Production Detail` where item = %s",
			(self.item,),
		)[0][0]
		new_version = (max_version or 0) + 1
		self.version = new_version
		self.name = f"{self.item}-{new_version}"

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
		if self.primary_item_attribute and self.primary_item_attribute not in listed:
			frappe.throw(f"Primary attribute {self.primary_item_attribute} must appear in Item Attributes table.")
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


@frappe.whitelist()
def calculate_process_io(ipd_name, process_name, output_demand):
	output_demand = frappe.parse_json(output_demand)
	return ipd_engine.get_process_io(ipd_name, process_name, output_demand)


@frappe.whitelist()
def calculate_consumables(ipd_name, total_output_qty, variants=None, process_name=None):
	total_output_qty = float(total_output_qty)
	variants = frappe.parse_json(variants) if variants else None
	return ipd_engine.get_consumables(
		ipd_name,
		total_output_qty,
		variants=variants,
		process_name=process_name,
	)


def calculate_major_deliverables(ipd_name, variant_demands, process_names=None, include_outputs=False):
	return ipd_engine.calculate_major_deliverables(
		ipd_name,
		variant_demands,
		process_names=process_names,
		include_outputs=frappe.utils.cint(include_outputs),
	)


def calculate_accessory_bom(ipd_name, variant_demands, process_name=None):
	return ipd_engine.calculate_accessory_bom(
		ipd_name,
		variant_demands,
		process_name=process_name,
	)


def calculate_lot_bom(ipd_name, variant_demands, process_names=None, include_outputs=False):
	return ipd_engine.calculate_lot_bom(
		ipd_name,
		variant_demands,
		process_names=process_names,
		include_outputs=frappe.utils.cint(include_outputs),
	)


@frappe.whitelist()
def calculate_matrix_bom(ipd_name, variant_demands, process_names=None, include_outputs=False):
	return calculate_major_deliverables(
		ipd_name,
		variant_demands,
		process_names=process_names,
		include_outputs=include_outputs,
	)


@frappe.whitelist()
def calculate_accessories(ipd_name, variant_demands, process_name=None):
	return calculate_accessory_bom(ipd_name, variant_demands, process_name=process_name)


@frappe.whitelist()
def calculate_bom(ipd_name, variant_demands, process_names=None, include_outputs=False):
	return calculate_lot_bom(
		ipd_name,
		variant_demands,
		process_names=process_names,
		include_outputs=include_outputs,
	)
