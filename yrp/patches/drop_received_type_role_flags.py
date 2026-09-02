# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Drop the legacy `is_rework_source` and `is_rejected` flags from Received Type.

Rework eligibility is now identified through YRP Stock Settings:
`default_received_type` and `default_rejected_received_type`. The Received
Type whose `is_rejected` flag was set is migrated into the new settings
field before the column is dropped.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", 'YRP Received Type'):
		return

	cols = set(frappe.db.get_table_columns('YRP Received Type'))

	if "is_rejected" in cols:
		rows = frappe.db.sql(
			"SELECT name FROM `tabYRP Received Type` WHERE is_rejected = 1 ORDER BY name LIMIT 1",
			as_dict=True,
		)
		if rows:
			current = frappe.db.get_single_value(
				'YRP YRP Stock Settings', "default_rejected_received_type"
			)
			if not current:
				frappe.db.set_single_value(
					'YRP YRP Stock Settings',
					"default_rejected_received_type",
					rows[0]["name"],
				)
		frappe.db.commit()
		frappe.db.sql_ddl("ALTER TABLE `tabYRP Received Type` DROP COLUMN `is_rejected`")

	if "is_rework_source" in cols:
		frappe.db.commit()
		frappe.db.sql_ddl("ALTER TABLE `tabYRP Received Type` DROP COLUMN `is_rework_source`")

	for fieldname in ("is_rework_source", "is_rejected"):
		frappe.db.delete("DocField", {"parent": 'YRP Received Type', "fieldname": fieldname})

	frappe.clear_cache(doctype='YRP Received Type')
