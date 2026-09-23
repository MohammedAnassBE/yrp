# Copyright (c) 2021, Essdee and contributors
# For license information, please see license.txt

"""Per-template selections of ERPNext's canonical Item Attribute values.

Item Attribute Value is a child table: mappings store its value text, never its
internal row name. The Desk picker and server validation share that contract.
"""

from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr


class YRPItemItemAttributeMapping(Document):
	def validate(self):
		from yrp.yrp.doctype.yrp_item.yrp_item import validate_attribute_value

		if not self.attribute_name:
			frappe.throw(_("Select an Item Attribute before adding values."))
		attribute = frappe.get_cached_doc("Item Attribute", self.attribute_name)
		seen = set()
		for row in self.get("values") or []:
			value = row.attribute_value
			if value in (None, ""):
				frappe.throw(_("Row {0}: Attribute Value is required.").format(row.idx))
			key = value
			if attribute.numeric_values:
				# ERPNext's flt() treats malformed text as zero; reject it before
				# delegating the actual range/increment rules to ERPNext.
				key = _number(value)
				if key is None:
					frappe.throw(_("Row {0}: Attribute Value must be a finite number.").format(row.idx))
			validate_attribute_value(self.attribute_name, value)
			if key in seen:
				frappe.throw(_("Row {0}: Attribute Value {1} is repeated.").format(row.idx, value))
			seen.add(key)


def _number(value):
	try:
		number = Decimal(cstr(value))
		return number if number.is_finite() else None
	except InvalidOperation:
		return None


@frappe.whitelist()
def search_attribute_values(attribute=None, txt=""):
	"""Autocomplete strings scoped to a readable Item Attribute.

	Numeric attributes define values through a range instead of child rows. Show
	a bounded range preview and include a valid typed number even beyond it.
	"""

	from yrp.yrp.doctype.yrp_item.yrp_item import get_global_attribute_values

	if not attribute:
		return []
	doc = frappe.get_doc("Item Attribute", attribute)
	doc.check_permission("read")
	if not doc.numeric_values:
		needle = cstr(txt).casefold()
		return [value for value in get_global_attribute_values(attribute) if needle in value.casefold()][:99]

	start, end, step = (_number(doc.get(field)) for field in ("from_range", "to_range", "increment"))
	if start is None or end is None or step is None or step <= 0 or end < start:
		return []
	values = []
	entered = _number(txt)
	if entered is not None and start <= entered <= end and (entered - start) % step == 0:
		values.append(cstr(txt))
	for index in range(99):
		value = start + step * index
		if value > end:
			break
		text = format(value.normalize(), "f")
		if cstr(txt) in text and text not in values:
			values.append(text)
	return values[:99]


ItemItemAttributeMapping = YRPItemItemAttributeMapping
