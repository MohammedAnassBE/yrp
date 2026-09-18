"""Compatibility API for the retired YRP Item Variant DocType.

Physical variants are standard ERPNext Items (`variant_of` points to their
template). YRP keeps item codes stable, so the historical rename action no
longer derives or mutates the Item code.
"""

import frappe
from erpnext.stock.doctype.item.item import Item


@frappe.whitelist()
def rename_item_variant(variant):
	doc = frappe.get_doc("Item", variant)
	if not doc.variant_of:
		frappe.throw(f"Item {variant} is not a variant")
	return doc.name


ItemVariant = Item
YRPItemVariant = ItemVariant
