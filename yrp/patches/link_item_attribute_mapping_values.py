"""Preserve mapping text and row identity while replacing it with scoped Links."""

import frappe

from yrp.attribute_values import MASTER, attribute_value_name, ensure_value_master


def execute():
	frappe.reload_doc("yrp", "doctype", "yrp_item_attribute_value")
	from yrp.yrp.doctype.yrp_item.yrp_item import ensure_global_attribute_values

	# Resolve legacy blank mapping attributes only when every value identifies
	# the same unique native attribute. Ambiguous history must stop the patch.
	for mapping in frappe.get_all("YRP Item Item Attribute Mapping", fields=["name", "attribute_name"]):
		if mapping.attribute_name:
			continue
		values = frappe.get_all(
			"YRP Item Item Attribute Mapping Value", filters={"parent": mapping.name}, pluck="attribute_value"
		)
		candidates = None
		for value in filter(None, values):
			parents = set(
				frappe.get_all(
					"Item Attribute Value",
					filters={"attribute_value": value, "parenttype": "Item Attribute"},
					pluck="parent",
				)
			)
			candidates = parents if candidates is None else candidates & parents
		if candidates is None:
			continue
		if len(candidates) != 1:
			frappe.throw(f"Cannot resolve attribute for mapping {mapping.name}")
		frappe.db.set_value(
			"YRP Item Item Attribute Mapping",
			mapping.name,
			"attribute_name",
			candidates.pop(),
			update_modified=False,
		)

	rows = frappe.db.sql(
		"""SELECT v.name,v.attribute_value,m.attribute_name
		FROM `tabYRP Item Item Attribute Mapping Value` v
		LEFT JOIN `tabYRP Item Item Attribute Mapping` m ON m.name=v.parent
		WHERE COALESCE(v.attribute_value,'')<>''""",
		as_dict=True,
	)
	for row in rows:
		if row.attribute_name or frappe.db.exists(MASTER, row.attribute_value):
			continue
		# Orphan child history has no parent attribute. The earlier migration
		# retained each source value master's exact ID as its native child ID.
		# Resolve that identity first; do not guess between same-label attributes.
		native = frappe.db.get_value(
			"Item Attribute Value", row.attribute_value, ["parent", "attribute_value"], as_dict=True
		)
		if native and native.attribute_value == row.attribute_value:
			row.attribute_name = native.parent
		else:
			parents = set(
				frappe.get_all(
					"Item Attribute Value", filters={"attribute_value": row.attribute_value}, pluck="parent"
				)
			)
			if len(parents) != 1:
				frappe.throw(f"Cannot resolve orphan mapping row {row.name}")
			row.attribute_name = parents.pop()
	pairs = {
		(row.attribute_name, row.attribute_value)
		for row in rows
		if not frappe.db.exists(MASTER, row.attribute_value)
	}
	for attribute, value in sorted(pairs):
		# A mapping is itself an explicit allowed-value declaration. Preserve
		# historical values absent from ERPNext's global list by adding the pair.
		if not frappe.get_cached_value("Item Attribute", attribute, "numeric_values"):
			ensure_global_attribute_values(attribute, [value], check_permission=False)
		ensure_value_master(attribute, value)
	for row in frappe.get_all(
		"Item Attribute Value",
		filters={"parenttype": "Item Attribute", "parentfield": "item_attribute_values"},
		fields=["parent", "attribute_value"],
		limit_page_length=0,
	):
		ensure_value_master(row.parent, row.attribute_value)
	for row in rows:
		if frappe.db.exists(MASTER, row.attribute_value):
			continue
		frappe.db.set_value(
			"YRP Item Item Attribute Mapping Value",
			row.name,
			"attribute_value",
			attribute_value_name(row.attribute_name, row.attribute_value),
			update_modified=False,
		)
	frappe.clear_document_cache("YRP Item Item Attribute Mapping")
