"""Whitelisted entrypoints to the IPD engine."""

import frappe

from yrp.yrp.utils.ipd_engine import get_consumables, get_process_io


@frappe.whitelist()
def calculate_process_io(ipd_name, process_name, output_demand):
	output_demand = frappe.parse_json(output_demand)
	return get_process_io(ipd_name, process_name, output_demand)


@frappe.whitelist()
def calculate_consumables(ipd_name, total_output_qty, variants=None, process_name=None):
	total_output_qty = float(total_output_qty)
	variants = frappe.parse_json(variants) if variants else None
	return get_consumables(ipd_name, total_output_qty, variants=variants, process_name=process_name)
