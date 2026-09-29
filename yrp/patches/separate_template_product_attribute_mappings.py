"""Give existing sales catalog owners private attribute mappings through normal saves.

No value edits, raw link rewrites or standalone-map purge are performed. A
second execution makes no changes once the affected mappings are private.
"""

import frappe

from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import (
	has_other_references,
	mapping_names,
)

OWNERS = ("YRP Item Master Template", "YRP Product")


def separate_owner(doctype, name):
	"""Re-read one owner under lock; its controller handles copy and cleanup."""
	if doctype not in OWNERS:
		frappe.throw("Attribute mapping repair requires a Template or Product.")
	doc = frappe.get_doc(doctype, name, for_update=True)
	if not any(has_other_references(mapping, doc) for mapping in sorted(mapping_names(doc))):
		return False
	doc.save()
	return True


def execute():
	if not frappe.db.exists("DocType", "YRP Item Item Attribute"):
		return
	rows = frappe.get_all(
		"YRP Item Item Attribute",
		filters={"parenttype": ["in", OWNERS], "mapping": ["is", "set"]},
		fields=["parenttype", "parent"],
	)
	for doctype, name in sorted({(row.parenttype, row.parent) for row in rows}):
		if frappe.db.exists(doctype, name):
			separate_owner(doctype, name)
