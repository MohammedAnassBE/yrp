"""Backfill `IPD Item Attribute.mapping` from the parent Item's
`Item Item Attribute.mapping` for every existing IPD row.

Idempotent. Skips rows that already have a mapping set.
"""

import frappe


def execute():
	if not frappe.db.has_column("IPD Item Attribute", "mapping"):
		return  # field hasn't synced yet
	# For each IPD, for each item_attributes row missing a mapping, look up the
	# parent Item's Item Item Attribute row matching the same attribute and copy
	# its mapping value across.
	rows = frappe.db.sql(
		"""
		SELECT iia.name, iia.parent, iia.attribute, ipd.item
		FROM `tabIPD Item Attribute` iia
		JOIN `tabItem Production Detail` ipd ON ipd.name = iia.parent
		WHERE iia.parenttype = 'Item Production Detail'
		  AND (iia.mapping IS NULL OR iia.mapping = '')
		""",
		as_dict=True,
	)
	for r in rows:
		mapping = frappe.db.get_value(
			"Item Item Attribute",
			{"parent": r.item, "parenttype": "Item", "attribute": r.attribute},
			"mapping",
		)
		if mapping:
			frappe.db.set_value("IPD Item Attribute", r.name, "mapping", mapping, update_modified=False)
