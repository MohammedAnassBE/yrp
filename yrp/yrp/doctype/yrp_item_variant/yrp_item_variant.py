"""Compatibility API for Item variants stored as standard ERPNext Items."""

import frappe
from erpnext.stock.doctype.item.item import Item

from yrp.yrp.doctype.yrp_item.yrp_item import rename_variant


@frappe.whitelist()
def rename_item_variant(variant):
	return rename_variant(variant)


ItemVariant = Item
YRPItemVariant = ItemVariant
