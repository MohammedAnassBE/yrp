"""Restore stable attribute-value names and update their Links."""

import frappe
from frappe.utils.global_search import rebuild_for_doctype

from yrp.attribute_value_identity import attribute_value_name
from yrp.attribute_values import MASTER

BATCH_SIZE = 100


def execute():
	rows = frappe.get_all(
		MASTER,
		fields=["name", "attribute_name", "attribute_value"],
		order_by="name asc",
		limit_page_length=0,
	)
	counts = {"renamed": 0, "merged": 0, "unchanged": 0}
	for offset in range(0, len(rows), BATCH_SIZE):
		for row in rows[offset : offset + BATCH_SIZE]:
			counts[_rename_value(row)] += 1
		frappe.db.commit()
		frappe.logger("yrp").info("Attribute value hash migration: %s", counts)
	if counts["renamed"] or counts["merged"]:
		rebuild_for_doctype(MASTER)


def _rename_value(row):
	target = attribute_value_name(row.attribute_name, row.attribute_value)
	if row.name == target:
		return "unchanged"
	owner = frappe.db.get_value(MASTER, target, ["attribute_name", "attribute_value"], as_dict=True)
	if owner and (owner.attribute_name, owner.attribute_value) != (row.attribute_name, row.attribute_value):
		frappe.throw(f"Attribute value hash collision: {row.name} -> {target}")
	frappe.rename_doc(
		MASTER, row.name, target, force=True, merge=bool(owner), show_alert=False, rebuild_search=False
	)
	return "merged" if owner else "renamed"
