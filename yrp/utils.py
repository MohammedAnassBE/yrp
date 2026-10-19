import json

import frappe
from yrp.attribute_read_context import read_value
from six import string_types

from yrp.attribute_links import value as _attribute_value


def update_if_string_instance(obj):
	if isinstance(obj, string_types):
		obj = json.loads(obj)

	if not obj:
		obj = {}

	return obj


def get_variant_attr_details(variant):
	return read_value("variant", variant, lambda: _read_get_variant_attr_details(variant))


def _read_get_variant_attr_details(variant):
	attr_details = frappe.db.sql(
		""" SELECT attribute, attribute_value FROM `tabItem Variant Attribute` WHERE parent = %(parent)s """,
		{"parent": variant},
		as_dict=True,
	)
	return {
		attr_detail["attribute"]: _attribute_value(attr_detail["attribute_value"])
		for attr_detail in attr_details
	}
