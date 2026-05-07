"""Whitelisted entrypoints to the IPD engine."""

import frappe

from yrp.yrp.doctype.item_production_detail import item_production_detail as ipd


@frappe.whitelist()
def calculate_process_io(ipd_name, process_name, output_demand):
	return ipd.calculate_process_io(ipd_name, process_name, output_demand)


@frappe.whitelist()
def calculate_consumables(ipd_name, total_output_qty, variants=None, process_name=None):
	return ipd.calculate_consumables(
		ipd_name,
		total_output_qty,
		variants=variants,
		process_name=process_name,
	)


@frappe.whitelist()
def calculate_matrix_bom(ipd_name, variant_demands, process_names=None, include_outputs=False):
	return ipd.calculate_matrix_bom(
		ipd_name,
		variant_demands,
		process_names=process_names,
		include_outputs=include_outputs,
	)


@frappe.whitelist()
def calculate_accessories(ipd_name, variant_demands, process_name=None):
	return ipd.calculate_accessories(ipd_name, variant_demands, process_name=process_name)


@frappe.whitelist()
def calculate_bom(ipd_name, variant_demands, process_names=None, include_outputs=False):
	return ipd.calculate_bom(
		ipd_name,
		variant_demands,
		process_names=process_names,
		include_outputs=include_outputs,
	)
