"""Let template Item edits reach variants for classification, free flag and defaults."""

import frappe

FIELDS = ("yrp_item_type", "yrp_categories", "yrp_is_free_item", "item_defaults")


def execute():
	settings = frappe.get_single("Item Variant Settings")
	existing = {row.field_name for row in settings.fields}
	missing = [field for field in FIELDS if field not in existing]
	if not missing:
		return
	for field in missing:
		settings.append("fields", {"field_name": field})
	settings.save(ignore_permissions=True)
