import json

import frappe
from six import string_types


def update_if_string_instance(obj):
	if isinstance(obj, string_types):
		obj = json.loads(obj)

	if not obj:
		obj = {}

	return obj


def get_panel_colour_combination(ipd_doc):
	indexes = {}
	comb_details = {}
	for row in ipd_doc.stiching_item_combination_details:
		if indexes.get(row.index):
			major_colour = indexes[row.index]
			comb_details[major_colour][row.set_item_attribute_value] = row.attribute_value
		else:
			indexes[row.index] = row.major_attribute_value
			comb_details[row.major_attribute_value] = {}
			comb_details[row.major_attribute_value][row.set_item_attribute_value] = row.attribute_value

	return comb_details


def get_variant_attr_details(variant):
	attr_details = frappe.db.sql(
		""" SELECT attribute, attribute_value FROM `tabItem Variant Attribute` WHERE parent = %(parent)s """,
		{"parent": variant},
		as_dict=True,
	)
	return {attr_detail["attribute"]: attr_detail["attribute_value"] for attr_detail in attr_details}
