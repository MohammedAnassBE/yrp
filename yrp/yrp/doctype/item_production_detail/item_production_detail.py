import json

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

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


def get_ipd_primary_values(production_detail):
	doc = frappe.get_cached_doc("Item Production Detail", production_detail)
	primary_attr_values = []
	mapping = None
	for row in doc.item_attributes:
		attribute_name = row.get("attribute") or row.get("item_attribute")
		if attribute_name == doc.primary_item_attribute:
			mapping = row.get("mapping")
			break
	if mapping:
		map_doc = frappe.get_cached_doc("Item Item Attribute Mapping", mapping)
		for val in map_doc.values:
			primary_attr_values.append(val.attribute_value)
	return primary_attr_values


@frappe.whitelist()
def get_calculated_bom(item_production_detail, items, lot_name, process_name=None, doctype=None, deliverable=False):
	lot = frappe.get_doc("Lot", lot_name)
	variant_demands = _get_lot_variant_demands(lot, items)
	bom = calculate_lot_bom(
		item_production_detail,
		variant_demands,
		process_names=process_name,
		include_outputs=False,
	)
	major_rows = bom["major_deliverables"]
	accessory_rows = bom["accessories"]
	bom_summary_rows = [
		_to_lot_bom_row(row)
		for row in major_rows + accessory_rows
	]

	lot.set("bom_summary", bom_summary_rows)
	lot.bom_summary_json = json.dumps(
		{
			"major_deliverables": major_rows,
			"accessories": accessory_rows,
		},
		default=str,
	)
	lot.last_calculated_time = now_datetime()
	lot.total_quantity = int(sum(row["qty"] for row in variant_demands))
	lot.save(ignore_permissions=True)
	return {
		"rows": len(bom_summary_rows),
		"major_rows": len(major_rows),
		"accessory_rows": len(accessory_rows),
		"total_qty": lot.total_quantity,
	}


def _get_lot_variant_demands(lot, items=None):
	rows = []
	items = frappe.parse_json(items) if isinstance(items, str) else items
	for row in items or lot.get("lot_order_details") or []:
		item_variant = row.get("item_variant") if isinstance(row, dict) else row.item_variant
		quantity = row.get("quantity") if isinstance(row, dict) else row.quantity
		if item_variant and float(quantity or 0) > 0:
			rows.append({"item_variant": item_variant, "qty": float(quantity or 0)})

	if not rows:
		for row in lot.get("items") or []:
			if row.item_variant and float(row.qty or 0) > 0:
				rows.append({"item_variant": row.item_variant, "qty": float(row.qty or 0)})

	if not rows:
		frappe.throw("Please add Item Variant and Qty before calculating BOM.")
	return rows


def _to_lot_bom_row(row):
	return {
		"item_name": row.get("item_variant"),
		"process_name": row.get("process_name"),
		"required_qty": row.get("required_qty") or row.get("qty") or 0,
		"uom": row.get("uom"),
	}
