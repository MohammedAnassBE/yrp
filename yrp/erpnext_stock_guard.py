"""Keep YRP-ledger Items out of ERPNext's parallel stock engine."""

import frappe
from frappe import _


STOCK_UPDATING_INVOICES = {"Purchase Invoice", "Sales Invoice"}
STOCK_ITEM_TABLES = ("items", "packed_items", "supplied_items")
STOCK_ITEM_FIELDS = ("item_code", "item", "rm_item_code", "main_item_code")


def reject_yrp_stock_items(doc, method=None):
	"""Reject ERPNext stock posting for every stock Item on a YRP site.

	The common Item/Warehouse masters are intentional; the ledgers are not. YRP
	is the sole stock authority in the combined architecture, including for a
	new Item before its first YRP Bin/SLE exists. Non-stock accounting rows remain
	valid in ERPNext invoices when ``update_stock`` is disabled.
	"""
	if doc.doctype in STOCK_UPDATING_INVOICES and not doc.get("update_stock"):
		return
	item_codes = _get_item_codes(doc)
	for item_code in item_codes:
		if _is_stock_item(item_code):
			frappe.throw(
				_(
					"Item {0} is maintained by YRP stock and cannot be posted through {1}. "
					"Use the corresponding YRP stock transaction."
				).format(item_code, doc.doctype)
			)


def _get_item_codes(doc):
	values = set()
	if doc.get("item_code"):
		values.add(doc.item_code)
	for table_field in STOCK_ITEM_TABLES:
		for row in doc.get(table_field) or []:
			for item_field in STOCK_ITEM_FIELDS:
				if item_code := row.get(item_field):
					values.add(item_code)
	return values


def _is_stock_item(item_code):
	return bool(frappe.get_cached_value("Item", item_code, "is_stock_item"))
