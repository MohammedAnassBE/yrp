"""Variants follow their template Item; drop direct master-template links and their variant copy setting."""

import frappe


def execute():
	frappe.db.delete(
		"Variant Field", {"parent": "Item Variant Settings", "field_name": "yrp_item_master_template"}
	)
	frappe.db.set_value(
		"Item",
		{"variant_of": ["is", "set"], "yrp_item_master_template": ["is", "set"]},
		"yrp_item_master_template",
		None,
		update_modified=False,
	)
