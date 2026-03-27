# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document


class ZPLRawPrintFormat(Document):
	pass


@frappe.whitelist()
def get_value_with_pad(value, pad):
	"""Left-justify value to pad length with spaces."""
	value = str(value)
	pad = int(pad)
	if len(value) < pad:
		value = value.ljust(pad, " ")
	return value


@frappe.whitelist()
def get_item_size(item, item_size, text_size):
	"""Map item length to font/text size for raw label printing."""
	if isinstance(item_size, str):
		item_size = json.loads(item_size)
	if isinstance(text_size, str):
		text_size = json.loads(text_size)

	for idx, threshold in enumerate(item_size):
		if len(str(item)) <= threshold:
			return text_size[idx]

	return text_size[-1] if text_size else None
