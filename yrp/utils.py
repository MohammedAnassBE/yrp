import json

import frappe
from six import string_types

from yrp.attribute_links import value as _attribute_value


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
			comb_details[major_colour][_attribute_value(row.set_item_attribute_value)] = _attribute_value(
				row.attribute_value
			)
		else:
			indexes[row.index] = _attribute_value(row.major_attribute_value)
			comb_details[_attribute_value(row.major_attribute_value)] = {}
			comb_details[_attribute_value(row.major_attribute_value)][
				_attribute_value(row.set_item_attribute_value)
			] = _attribute_value(row.attribute_value)

	return comb_details


def get_variant_attr_details(variant):
	attr_details = frappe.db.sql(
		""" SELECT attribute, attribute_value FROM `tabItem Variant Attribute` WHERE parent = %(parent)s """,
		{"parent": variant},
		as_dict=True,
	)
	return {
		attr_detail["attribute"]: _attribute_value(attr_detail["attribute_value"])
		for attr_detail in attr_details
	}
