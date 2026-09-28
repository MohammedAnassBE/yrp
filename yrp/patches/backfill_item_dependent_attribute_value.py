"""Backfill the reportable dependent value without resaving historical Items."""

import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	# Post-model patches run before fixtures. Install this field explicitly so
	# the backfill also works on an existing site during its first upgrade.
	fixtures = json.loads(Path(frappe.get_app_path("yrp", "fixtures", "custom_field.json")).read_text())
	field = next(row for row in fixtures if row.get("name") == "Item-dependent_attribute_value")
	create_custom_fields({"Item": [field]})

	conflicts = frappe.db.sql("""
		SELECT v.name
		FROM `tabItem` v
		JOIN `tabItem` t ON t.name = v.variant_of
		JOIN `tabItem Variant Attribute` a ON a.parent = v.name
			AND a.parenttype = 'Item' AND a.parentfield = 'attributes'
			AND a.attribute = t.dependent_attribute
		WHERE COALESCE(t.dependent_attribute, '') != ''
		GROUP BY v.name
		HAVING COUNT(DISTINCT NULLIF(a.attribute_value, '')) > 1
		LIMIT 10
	""")
	if conflicts:
		frappe.throw("Conflicting dependent attribute values on Items: " + ", ".join(row[0] for row in conflicts))

	# Missing historical attribute rows remain blank; do not guess from names.
	# SQL updates only this derived field and preserves modified/modified_by.
	frappe.db.sql("""
		UPDATE `tabItem` v
		JOIN `tabItem` t ON t.name = v.variant_of
		JOIN (
			SELECT parent, attribute, MIN(NULLIF(attribute_value, '')) AS attribute_value
			FROM `tabItem Variant Attribute`
			WHERE parenttype = 'Item' AND parentfield = 'attributes'
			GROUP BY parent, attribute
		) a ON a.parent = v.name AND a.attribute = t.dependent_attribute
		SET v.dependent_attribute_value = a.attribute_value
		WHERE COALESCE(t.dependent_attribute, '') != ''
			AND NOT (v.dependent_attribute_value <=> a.attribute_value)
	""")
	frappe.clear_document_cache("Item")
