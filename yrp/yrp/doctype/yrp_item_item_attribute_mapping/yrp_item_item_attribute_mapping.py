# Copyright (c) 2021, Essdee and contributors
# For license information, please see license.txt

"""Validate ERPNext attribute selections; store their YRP Link identities."""

from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr

from yrp.attribute_links import value as actual_attribute_value
from yrp.attribute_values import normalize_mapping


class YRPItemItemAttributeMapping(Document):
	def validate(self):
		from yrp.yrp.doctype.yrp_item.yrp_item import validate_attribute_value
		from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import validate_mapping_unlocked

		validate_mapping_unlocked(self)
		if not self.attribute_name:
			frappe.throw(_("Select an Item Attribute before adding values."))
		attribute = frappe.get_cached_doc("Item Attribute", self.attribute_name)
		seen = set()
		for row in self.get("values") or []:
			stored = row.attribute_value
			if not stored:
				frappe.throw(_("Row {0}: Attribute Value is required.").format(row.idx))
			# Internal callers may provide ERPNext value text. Persisted rows use
			# the YRP Link; both validate against the same native attribute.
			value = actual_attribute_value(stored)
			key = value
			if attribute.numeric_values:
				key = _number(value)
				if key is None:
					frappe.throw(_("Row {0}: Attribute Value must be a finite number.").format(row.idx))
			validate_attribute_value(self.attribute_name, value)
			if key in seen:
				frappe.throw(_("Row {0}: Attribute Value {1} is repeated.").format(row.idx, value))
			seen.add(key)

	def _validate_links(self):
		normalize_mapping(self)
		super()._validate_links()


def _number(value):
	try:
		number = Decimal(cstr(value))
		return number if number.is_finite() else None
	except InvalidOperation:
		return None


ItemItemAttributeMapping = YRPItemItemAttributeMapping
